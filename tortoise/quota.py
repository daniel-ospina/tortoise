"""Per-team quota enforcement shared by REST and MCP (#329, #683, #686).

Design: limits are resolved ONCE by the authenticated caller
(``hosted_api.get_current_team`` / MCP ``TeamResolutionMiddleware``) via
``resolve_team_limits`` and passed to ``enforce_team_limit`` — never re-fetched
per write.

Fail-closed decision (#686)
~~~~~~~~~~~~~~~~~~~~~~~~~~~
Counting is **fail-closed**: any counting exception raises
``QuotaCheckError`` (server error), never a silent pass.

Rationale:
- **Money at stake.** Fail-open would let free-tier teams exceed paid limits
  undetected during a DB outage — direct revenue risk and abuse vector.
- **Fail-closed is the secure default.** When you can't verify, don't grant.
- **Customer harm from fail-closed is bounded.** A DB outage that breaks
  count queries typically also breaks the actual write (same store) — we're
  failing fast, not adding net-new unavailability.
- **Alerting mitigates ops risk.** Every count failure is logged at ERROR
  level with team_id and resource, so operators see the outage immediately
  and can decide whether to temporarily disable enforcement.

Import topology: stdlib-only at module level; ``tortoise.sdk`` imported
function-level inside the helpers to avoid any cycle (hosted_api → mcp_server
→ mcp_auth → sdk is the canonical direction; quota is a leaf consumer).

No team context (stdio/operator) → ``enforce_team_limit(None, ...)`` returns
cleanly (skip) — mirrors REST ``_check_team_limit``'s ``if not team_id: return``.
Batch caps are unconditional in both modes.

Downgrade-over-limit decision (#683)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
When a team downgrades (e.g. Pro→Free) while over the NEW tier's limits
(e.g. 2 memberships but Free allows only 1), the downgrade MUST be BLOCKED
with a clear error message listing which limits the team exceeds.

Rationale and decisions:
- **Block downgrade (not graceful-degrade).** Allowing a downgrade that
  immediately locks out members or breaks graphs creates a trust-destroying
  experience: the dashboard shows Free features, but the team has 2 members
  and 2 graphs — inconsistent and confusing.
- **No silent pass.** Trust requires that published limits are real limits.
  If a team can be over-limit on a lower tier, the limits aren't real.
- **No auto-delete.** Never delete data to fit a downgrade. The team must
  explicitly remove members/graphs before the downgrade can proceed.
- **Stripe webhook downgrade path (future, #310).** When the
  ``customer.subscription.updated`` webhook processes a tier downgrade, the
  ``mirror_subscription`` handler should check limits via this module BEFORE
  applying the new tier: if the team exceeds the new tier's limits, the
  downgrade must be rejected (log + alert), keeping the team at its current
  tier until the over-limit condition is resolved by the team owner.

  Until #310 lands the Stripe integration, there is no user-facing tier-change
  REST endpoint — the decision is a documented policy, not a live code path.
  The ``team_update`` SDK method (registry-level, no REST surface) allows
  tier/limit writes for operational relief; it does NOT check downgrade
  preconditions (operator intent overrides).
"""
from __future__ import annotations

import logging
import os
import tempfile

_logger = logging.getLogger(__name__)


def _make_sdk(*, namespace: str | None = None):
    """Build a TortoiseSDK with hosted_api._make_sdk's precedence (inline copy —
    quota is a leaf consumer and must not import hosted_api).

    URI mode (docker:// / redis:// / rediss://) when TORTOISE_DB_URI is set;
    else embedded via TORTOISE_DB_PATH (default /data/tortoise.db) with a
    tempfile fallback when the volume is unwritable (test env).
    """
    from tortoise.sdk import TortoiseSDK  # function-level: avoid cycles
    if os.environ.get("TORTOISE_DB_URI"):
        return TortoiseSDK(namespace=namespace)
    db_path = os.environ.get("TORTOISE_DB_PATH", "/data/tortoise.db")
    try:
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
    except OSError:
        db_path = os.path.join(tempfile.gettempdir(), "tortoise.db")
    return TortoiseSDK(db_path=db_path, namespace=namespace)


# ── Batch-cap constants (unconditional — both modes) ───────────────────────
MAX_CHECKPOINT_ITEMS = 500
MAX_FILE_DECISION_OPTIONS = 50
MAX_FILE_DECISION_EVIDENCE = 100
MAX_TAGS_PER_POINT = 50
MAX_OPERATOR_TARGETS = 500
MAX_SESSION_TURNS = 500
MAX_EXTRACTIONS_PER_TURN = 200
MAX_ANALYZE_LLM_PER_MIN = 60
#: #1987 Task 6: per-team per-minute LLM-call budget for the ask lane
#: (mirrors ``MAX_ANALYZE_LLM_PER_MIN`` — the #329 pattern). Per-process
#: (in-memory) scoping: a multi-worker uvicorn deployment scales the
#: 60/min bound ×workers.
MAX_ASK_LLM_PER_MIN = 60
MAX_DREAM_FULL_PER_HOUR = 6

# ── Value-first mining budget + Layer-1 payload caps (epic #909 §4.4) ────
# Per-session CUMULATIVE budget: net-new non-episodic delta, post-
# reconciliation (consumed by the commit endpoint, epic #909 slice 5).
# soft → WARN telemetry at 15; hard 25 → hold (PL3); ceiling 50 → 402.
MAX_VALUE_POINTS_PER_SESSION = {"soft": 15, "hard": 25, "ceiling": 50}
# Layer-1 RAW payload point count cap → 422. Deliberately NAMED differently
# from the budget ceiling (also 50) to prevent wiring the wrong 50 (plan
# R-decoupling, §4.4): the raw cap is independent of the budget check.
MAX_PAYLOAD_POINTS = 50
# Layer-1 per-type payload caps → 422 (independent, not summed).
MAX_ENTITIES = 500
MAX_OPERATORS = 500

# ── Default limits ──────────────────────────────────────────────────────────
# max_points/max_api_keys have NO constant here — they resolve from
# tortoise.pricing.tier_limits (product/pricing.json) so a legacy team without
# stored limits gets pricing-correct caps, never the stale 1000/20 consts that
# contradicted pricing.json (#310 GAP-B, review fix 2). max_sessions has no
# pricing.json field — flat 1000 across tiers (matches REST today).
DEFAULT_MAX_SESSIONS = 1000

# ── Documents cap: DERIVED-CONSTANT (T2-P2a, #1726 Slice 1) ────────────────
# max_documents is DERIVED from max_points with a documented conversion
# factor — deliberately NOT a pricing.json field (a pricing field would ripple
# through tier_limits/_REQUIRED_LIMIT_KEYS and every tier's resolved limits;
# the issue pins the derived-constant option to avoid that KeyError surface).
# Rationale for 10×: docs are content nodes WITHOUT claim extraction or EP
# churn (Sources only, #1726 slice 1) — an order of magnitude cheaper than
# points; the gate still bounds runaway corpus growth.
_DOCUMENTS_FROM_POINTS_FACTOR = 10

_RESOURCE_LIMIT_KEYS = {
    "points": "max_points",
    "api_keys": "max_api_keys",
    "sessions": "max_sessions",
    "users": "max_users",
    "graphs": "max_graphs",
}


class QuotaExceededError(Exception):
    """Team is at/over its resource limit — the write must be rejected (402)."""


class QuotaCheckError(Exception):
    """Quota counting/config failed — fail closed (500/503), never pass."""


def derived_tier(team_row: dict) -> str:
    """Effective tier for a team row — the #1082 anon-ceiling derivation.

    A zero-email (anonymous) team whose owner membership has NO linked
    user_id runs the reduced ``anon`` tier until it is claimed (#1082 PR1
    links the verified OAuth identity to the owner row; claim raises the
    team to ``free``). The predicate is membership-based
    (``is_anon_team``) — NEVER the ``teams.email IS NULL`` proxy, which
    misclassifies reg- teams (email set at mint, owner user_id still NULL)
    and legacy real-user rows.

    Registry mode: NO-OP (raw tier) — the anon ceiling is Supabase-mode
    only in v1 (selfhost is operator-controlled, not a farm surface).

    NOTE: distinct from billing.effective_tier(team, now) (grace-period
    logic, billing.py:382) — do not merge.

    Args:
        team_row: the teams row / Team dict (must carry ``tier`` and,
            for Supabase mode, ``id``).
    Returns:
        The effective tier string.
    """
    tier = team_row.get("tier") or "free"
    team_id = team_row.get("id") or team_row.get("team_id")
    if not team_id:
        return tier
    try:
        from tortoise.supabase_control import (  # noqa: I001
            get_control_plane, is_anon_team, is_supabase_enabled,
        )
        if not is_supabase_enabled():
            return tier  # registry mode: no-op
        cp = get_control_plane()
        if tier == "free" and is_anon_team(cp, team_id):
            return "anon"
        return tier
    except Exception:
        # Fail-open to the stored tier on control-plane read errors — the
        # anon ceiling is a protection posture, never a reason to 500 the
        # auth path.
        return tier


def resolve_team_limits(team_id: str) -> dict:
    """Resolve a team's limits from the control plane.

    Supabase mode (post-#669 flip): the teams row via the service-role
    seam — the registry is DELETED and querying it would auto-recreate the
    empty graph (post-flip verification finding, #669). Registry mode: the
    Team node, as before.

    Missing Team → QuotaCheckError (fail-closed; the auth layer should
    guarantee key→team mapping). Missing attributes → defaults
    (aligned with product/pricing.json free tier).
    """
    if not team_id:
        raise QuotaCheckError("resolve_team_limits requires a team_id")
    from tortoise.supabase_control import (  # noqa: I001
        _QUOTA_SELECT,
        _TEAM_ADDITIVE_0015_TIER,
        _TEAM_ADDITIVE_BILLING_TIER,
        _TEAM_ADDITIVE_DKL_TIER,
        _TEAM_ADDITIVE_IMPORT_TIER,
        _teams_row_fail_soft,
        get_control_plane, is_supabase_enabled,
    )
    if is_supabase_enabled():
        # #1859 P3-2 review (P2): route through the #1096 fail-soft seam —
        # the max_points column (20260817000001) is ADDITIVE; a direct
        # select 400s on a schema one migration behind, which would turn a
        # degrade-to-tier-defaults into a hard failure. Same additive
        # ladder as resolve_api_key / _session_user_team.
        row = _teams_row_fail_soft(
            get_control_plane(), team_id, select=_QUOTA_SELECT,
            additive_tiers=[_TEAM_ADDITIVE_IMPORT_TIER,
                            _TEAM_ADDITIVE_DKL_TIER,
                            _TEAM_ADDITIVE_0015_TIER,
                            _TEAM_ADDITIVE_BILLING_TIER],
        )
        if row is None:
            raise QuotaCheckError(f"Team {team_id!r} not found in control plane")
        # #1082 PR2: the anon ceiling is tier-DERIVED at resolution — an
        # unclaimed zero-email team (owner user_id NULL) resolves to the
        # reduced ``anon`` tier until claimed, then lifts to free. The row
        # needs its ``id`` for the is_anon_team predicate (already have
        # team_id).
        row = {**row, "id": team_id, "tier": derived_tier({**row, "id": team_id})}
        tier = row.get("tier") or "free"
        from tortoise.pricing import tier_limits
        lim = tier_limits(tier)
        # #1082 PR2: when the derived tier is anon, the STORED quota columns
        # (minted at free values by agent_signup/register) are overridden
        # read-time with the reduced anon caps — an unclaimed zero-email
        # team must never bind at free limits (indicator 4).
        anon_override = tier == "anon"
        # Mirror the registry shape EXACTLY (review P2, PR #911): NULL
        # max_users/max_graphs = UNLIMITED (Team tier) — preserve None,
        # never substitute pricing defaults (enforce_team_limit treats an
        # explicit None limit as unlimited; substituting finite caps would
        # hard-cap legacy/migrated rows). max_points override (GAP-B,
        # 20260817000001) takes precedence over graph_size_cap (the
        # fallback), then pricing; max_api_keys/max_sessions fall back to
        # pricing/defaults.
        mu = row.get("max_users")
        mg = row.get("max_graphs")
        # #1859 P3-2: max_points column (points-cap override, migration
        # 20260817000001) takes precedence; graph_size_cap is the legacy
        # fallback (GAP-B); pricing last — mirrors import_team.
        mp = row.get("max_points")
        if mp is None:
            mp = row.get("graph_size_cap")
        return {
            "team_id": team_id, "tier": tier,
            "max_users": (lim["max_users_per_team"] if anon_override
                           else (int(mu) if mu is not None else None)),
            "max_graphs": (lim["max_graphs_per_team"] if anon_override
                            else (int(mg) if mg is not None else None)),
            "max_points": (int(lim["max_graph_nodes"]) if anon_override
                            else (int(mp) if mp is not None else lim["max_graph_nodes"])),
            "max_api_keys": lim["max_api_keys"],
            "max_sessions": DEFAULT_MAX_SESSIONS,
        }
    reg = _make_sdk(namespace="registry")
    rows = reg._get_registry().query(
        "MATCH (t:Team {id:$id}) "
        "RETURN t.tier, t.max_users, t.max_graphs, "
        "t.max_points, t.max_api_keys, t.max_sessions",
        params={"id": team_id},
    ).result_set
    if not rows:
        raise QuotaCheckError(f"Team {team_id!r} not found in registry")
    tier, mu, mg, mp, mak, ms = rows[0]
    tier = tier or "free"
    from tortoise.pricing import tier_limits
    lim = tier_limits(tier)
    return {
        "team_id": team_id,
        "tier": tier,
        # max_users/max_graphs: None means unlimited (Team tier); preserve it.
        "max_users": int(mu) if mu is not None else None,
        "max_graphs": int(mg) if mg is not None else None,
        "max_points": int(mp) if mp is not None else lim["max_graph_nodes"],
        "max_api_keys": int(mak) if mak is not None else lim["max_api_keys"],
        "max_sessions": int(ms) if ms is not None else DEFAULT_MAX_SESSIONS,
    }


def count_team_usage(team_id: str, resource: str, sdk=None) -> int:
    """Count current usage for a resource. Raises QuotaCheckError on failure.

    Public so callers needing the raw count (e.g. extraction-aware estimates)
    can use it without duplicating the fail-closed handling.

    Supported resources: points, api_keys, sessions, users, graphs,
    documents (#1726: the :Document count with the transcript discriminator).
    ``points`` counts non-episodic Points + Object/Subject nodes (#1911).
    """
    return _count_resource(team_id, resource, sdk=sdk)


def _count_resource(team_id: str, resource: str, sdk=None) -> int:
    """Count current usage for a resource. Raises QuotaCheckError on failure.

    Supported resources:
    - points: non-episodic Points in tenant graph (the Point-level
      ``is_episodic`` flag is the quota discriminator — #947, epic #909
      §4.4; legacy Points without the flag count as non-episodic,
      fail-closed, until graph-scripts/backfill_is_episodic.py backfills
      them, R-18) PLUS all Object/Subject nodes (#1911 — the /v1/objects
      + /v1/subjects gates check this resource, and Object/Subject carry
      only their own labels, so they must be counted here or the cap is
      vacuous for those writes)
    - api_keys: active (non-revoked) APIKey nodes in registry
    - sessions: Session nodes in tenant graph (MATCH (s:Session) — NOT the
      all-nodes count; #947 P0)
    - users: active Membership nodes in registry
    - graphs: Graph nodes in registry
    - documents (#1726): :Document nodes in the tenant graph with the
      discriminator ``COALESCE(documentKind,'') != 'transcript'`` — NULL-kind
      docs COUNT (no leak; a frontmatter-less docs-endpoint doc is NULL-kind
      and counts), session transcripts (documentKind='transcript', the
      /v1/sessions commit MERGE at hosted_api.py) are EXCLUDED so a captured
      session never consumes the docs gate.
    """
    try:
        # ── Registry-scoped counts (api_keys, users, graphs) ──
        if resource in ("api_keys", "users", "graphs"):
            # #765 (plan Task 8 quota paths): in Supabase control-plane mode
            # the count reads Supabase via the seam — post-flip the registry
            # is DELETED, so a registry count would fail-open (0 nodes) or
            # 500. Mirrors the registry predicates exactly:
            #   api_keys: revoked_at IS NULL (expired rows still count)
            #   users:    status IS NULL OR status = 'active'
            #   graphs:   the default graph derived from teams.graph_name
            #             (no graphs table in the plan data model — custom
            #             graphs are not tracked in Supabase mode).
            # Selfhost (registry mode) keeps the registry count.
            from tortoise.supabase_control import (  # noqa: I001
                get_control_plane, graph_metadata, is_supabase_enabled,
            )
            if is_supabase_enabled():
                cp = get_control_plane()
                if resource == "api_keys":
                    rows = cp.query(
                        "api_keys", select=["id"],
                        filters=[("team_id", "eq", team_id),
                                 ("revoked_at", "is", None)],
                    )
                    return len(rows)
                if resource == "users":
                    rows = cp.query(
                        "team_memberships", select=["status"],
                        filters=[("team_id", "eq", team_id)],
                    )
                    return len([r for r in rows
                                if r.get("status") in (None, "active")])
                # graphs: default graph exists whenever the team row does
                return len(graph_metadata(cp, team_id))
            reg = (sdk if sdk is not None and getattr(sdk, "_namespace", None) == "registry"
                   else _make_sdk(namespace="registry"))
            if resource == "api_keys":
                rows = reg._get_registry().query(
                    "MATCH (k:APIKey {team_id: $tid}) WHERE k.revoked_at IS NULL RETURN count(k)",
                    params={"tid": team_id},
                ).result_set
            elif resource == "users":
                rows = reg._get_registry().query(
                    "MATCH (m:Membership {team_id: $tid}) "
                    "WHERE m.status IS NULL OR m.status = 'active' RETURN count(m)",
                    params={"tid": team_id},
                ).result_set
            else:  # graphs
                rows = reg._get_registry().query(
                    "MATCH (g:Graph {team_id: $tid}) RETURN count(g)",
                    params={"tid": team_id},
                ).result_set
            return int(rows[0][0])

        # ── Tenant-graph-scoped counts (sessions, points) ──
        if sdk is None:
            sdk = _make_sdk(namespace=team_id)
        if resource == "documents":
            # #1726 Slice 1: the documents resource — :Document count with
            # the transcript discriminator (T2-P2a). NULL-kind docs COUNT
            # (COALESCE) — a frontmatter-less docs-endpoint doc never leaks;
            # session transcripts (documentKind='transcript', hosted_api.py
            # commit MERGE) are excluded so capture never consumes the docs
            # gate (the gate fires on /v1/index/docs ONLY).
            rows = sdk._get_proj().g.query(
                "MATCH (d:Document) "
                "WHERE COALESCE(d.documentKind, '') <> 'transcript' "
                "RETURN count(d)",
            ).result_set
            return int(rows[0][0])
        if resource == "sessions":
            # #947 (epic #909 §4.4, W-4): the P0 — count Session nodes, NOT
            # all nodes. Pre-fix this fell through to MATCH (n) (~25 nodes
            # per captured session) → false 402 after ~40 captures.
            rows = sdk._get_proj().g.query(
                "MATCH (s:Session) RETURN count(s)",
            ).result_set
            return int(rows[0][0])
        # points: non-episodic Points PLUS Object/Subject nodes (#1911). The
        # Point-level is_episodic flag is the Point discriminator — a MISSING
        # flag counts as non-episodic (fail-closed, R-18): legacy regex-path
        # captures lack it and must be backfilled episodic by
        # graph-scripts/backfill_is_episodic.py, else false 402s persist for
        # existing capture users. Object/Subject nodes are counted
        # UNCONDITIONALLY: they carry only their own labels (projection/
        # entities.py _upsert_object/_upsert_subject) and previously NEVER
        # appeared in this count — the /v1/objects + /v1/subjects gates
        # (hosted_api._check_team_limit resource="points") and the MCP
        # create_object/create_subject tools (_quota_gated "points") checked
        # a count that could only ever see Points, so a free team could write
        # unbounded objects/subjects without ever 402ing (bug-hunt 2026-08-28
        # server P2-1, #1911). max_points IS the pricing max_graph_nodes node
        # cap (tortoise.pricing tier_limits) — counting Object+Subject
        # against it applies the plan's real node cap, not a Point-only cap.
        # #1844 interplay (recorded intent): the GitHub indexer is an
        # UNGATED object writer (hosted_api.py:11097 — no points-quota
        # preflight, by design). Its minted Object nodes now count against
        # the cap, so an indexing-heavy team can be pushed past max_points,
        # after which all points-gated writes 402 until upgrade — the
        # intended "0 uncapped object/subject growth" posture. A future
        # indexer preflight (follow-up) would gate the job itself.
        rows = sdk._get_proj().g.query(
            "MATCH (n) "
            "WHERE (n:Point AND (n.is_episodic IS NULL OR n.is_episodic = false)) "
            "   OR n:Object OR n:Subject "
            "RETURN count(n)",
        ).result_set
        return int(rows[0][0])
    except QuotaCheckError:
        raise
    except Exception as e:
        from .security import redact_error
        redacted = redact_error(e)
        _logger.error(
            "quota count failed (fail-closed): team=%s resource=%s error=%s",
            team_id, resource, redacted,
        )
        raise QuotaCheckError(f"quota count failed for {resource}: {redacted}") from e


def enforce_team_limit(limits: dict | None, resource: str, sdk=None) -> None:
    """Reject a write when the team is at/over its resource limit.

    Args:
        limits: resolved team limits dict (from resolve_team_limits or the
            authenticated caller). None → skip (stdio/operator, no team).
        resource: "points" | "api_keys" | "sessions" | "users" | "graphs"
            | "documents".
        sdk: pre-built team SDK (REST callers already hold one) — optional.

    Raises:
        QuotaExceededError: team at/over limit (402-equivalent).
        QuotaCheckError: counting failed (fail-closed).
    """
    if limits is None:
        return  # stdio/operator — no team context
    team_id = limits.get("team_id")
    if not team_id:
        return
    # ── documents: DERIVED-CONSTANT cap (T2-P2a, #1726) — handled BEFORE the
    # pricing-keyed generic path. Deliberately NOT in _RESOURCE_LIMIT_KEYS: a
    # pricing.json max_documents field would ripple through tier_limits /
    # _REQUIRED_LIMIT_KEYS / every tier's resolved limits (the KeyError
    # surface the plan pins against). max_documents = max_points ×
    # _DOCUMENTS_FROM_POINTS_FACTOR; an explicitly-None max_points is
    # UNLIMITED (Team tier) — skip. The documents gate fires on the
    # /v1/index/docs job ONLY (cycle-3 P2: never an unpinned tenant-global
    # surprise).
    if resource == "documents":
        max_points = limits.get("max_points")
        if max_points is None:
            return  # explicitly-None = unlimited
        try:
            max_points = int(max_points)
        except (TypeError, ValueError):
            raise QuotaCheckError(
                f"team limits max_points invalid for documents resource "
                f"({max_points!r})") from None
        limit = max_points * _DOCUMENTS_FROM_POINTS_FACTOR
        count = _count_resource(team_id, "documents", sdk=sdk)
        if count >= limit:
            raise QuotaExceededError(
                f"Team documents limit reached ({limit}). Upgrade your plan "
                f"to increase it."
            )
        return
    limit_key = _RESOURCE_LIMIT_KEYS.get(resource)
    if limit_key is None:
        raise QuotaCheckError(f"unknown quota resource: {resource!r}")
    limit = limits.get(limit_key)
    if limit is None:
        # An explicitly-None limit means UNLIMITED (Team tier: users/graphs
        # stored null) — skip enforcement (#683). Distinguish from a MISSING
        # key, which is fail-closed (#310 GAP-B): never silently fall back to
        # lenient caps.
        if limit_key in limits:
            return
        if resource == "sessions":
            limit = DEFAULT_MAX_SESSIONS
        else:
            raise QuotaCheckError(f"team limits missing {limit_key} for resource {resource!r}")
    count = _count_resource(team_id, resource, sdk=sdk)
    if count >= limit:
        raise QuotaExceededError(
            f"Team {resource} limit reached ({limit}). Upgrade your plan to increase it."
        )


# ── Ask lane: shared budget bucket + bounded runner (#1987 Tasks 6/7/8) ────
#
# The ONE shared per-team per-minute LLM budget for the ask lane, used by
# BOTH the hosted REST handler and the hosted MCP handler (no duplicated
# prune/check/append logic — mcp_server.py imports ``tortoise.quota``). The
# bucket is bounded: idle team keys are pruned under a TTL + LRU cap so
# memory stays bounded under N-teams-ask-once / M-teams-idle, and a
# reactivated team starts clean (P2-15).

import collections  # noqa: E402
import contextlib  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

_ASK_BUDGET_TTL_S = 300.0       # idle team bucket expiry
_ASK_BUDGET_MAX_TEAMS = 1024    # LRU bound on the bucket dict
_ask_llm_budget: collections.OrderedDict[str, list[float]] = \
    collections.OrderedDict()
_ask_budget_lock = threading.Lock()


def _selfhost_transport_active() -> bool:
    """The transport-keyed selfhost exemption channel (tortoise/transport.py
    — the SELFHOST_TEAM_ID VALUE is never the exemption key)."""
    from tortoise.transport import _selfhost_transport
    return _selfhost_transport.get()


def ask_llm_budget_available(team_id: str | None) -> bool:
    """True if this team still has ask LLM budget this minute.

    Exemptions (always allowed): ``not team_id`` (stdio/None — mirroring
    ``_analyze_llm_budget_available``'s ``if not team_id: return True``,
    mcp_server.py) AND the selfhost-transport signal
    (``_selfhost_transport.get()`` — stdio + selfhost MCP modes are
    unbudgeted). A hosted team with the RAW id "selfhost" is budget-charged
    (the exemption is transport-keyed, never value-keyed).

    Prune → check → append (never pop between check and append — that
    orphans the appended timestamp and silently disables the budget).
    """
    if not team_id or _selfhost_transport_active():
        return True
    now_ts = time.monotonic()
    with _ask_budget_lock:
        bucket = _ask_llm_budget.get(team_id)
        if bucket is None:
            bucket = []
        # prune stale entries for THIS team (equal/sub-second timestamps
        # safe — monotonic, no index errors)
        bucket[:] = [ts for ts in bucket if now_ts - ts < 60.0]
        if len(bucket) >= MAX_ASK_LLM_PER_MIN:
            return False
        bucket.append(now_ts)
        _ask_llm_budget[team_id] = bucket
        _ask_llm_budget.move_to_end(team_id)
        # TTL + LRU bound: drop expired/least-recently-used team keys
        expired = [k for k, v in _ask_llm_budget.items()
                   if v and now_ts - v[-1] >= _ASK_BUDGET_TTL_S]
        for k in expired:
            del _ask_llm_budget[k]
        while len(_ask_llm_budget) > _ASK_BUDGET_MAX_TEAMS:
            _ask_llm_budget.popitem(last=False)
    return True


def _reset_ask_budget_for_tests() -> None:
    """Test seam — clears the shared ask budget bucket."""
    with _ask_budget_lock:
        _ask_llm_budget.clear()


def ask_budget_retry_after(team_id: str | None) -> float:
    """Seconds until the team's ask budget window self-heals (Retry-After
    for the 429 ``quota_exceeded`` response — ≈ the prune delay; 0 when no
    budget was consumed)."""
    if not team_id:
        return 0.0
    now_ts = time.monotonic()
    with _ask_budget_lock:
        bucket = _ask_llm_budget.get(team_id) or []
        if not bucket:
            return 0.0
        oldest = min(bucket)
        return max(0.0, 60.0 - (now_ts - oldest))


class AskInFlightLimitError(Exception):
    """Per-team in-flight cap hit (4 concurrent) — mapped to 429
    ``in_flight_limit`` by the ask handlers."""


class AskBoundedTimeoutError(Exception):
    """The bounded ask section exceeded ``_ASK_TIMEOUT_S`` (semaphore queue
    OR the reader call) — mapped to 504 ``timeout`` by the ask handlers."""


#: Ask-lane bounds (#1987 Task 7): global semaphore, per-team in-flight cap,
#: and the injectable module-level timeout (monkeypatched in tests — no real
#: 60s sleeps).
_ASK_TIMEOUT_S = 60
_ASK_EXEC_FLOOR_S = 5.0     # a started ask is guaranteed >= this much execution time
_ASK_GLOBAL_SEMAPHORE_SIZE = 8
_ASK_TEAM_IN_FLIGHT_CAP = 4

#: Loop-safe semaphore/in-flight state: keyed by the RUNNING loop object via
#: a weak-keyed dict (entries die with their loop — a recreated loop rebinds
#: cleanly; the size bound is one entry per live loop). Never a module-level
#: ``asyncio.Semaphore`` bound to the first loop it is awaited in (the
#: mcp_server.py hazard, P1-8/P2-8).
import weakref  # noqa: E402 — the state helpers import it lazily too

_ask_loop_state: weakref.WeakKeyDictionary = None  # type: ignore[assignment]
_ask_loop_state_lock = threading.Lock()


def _ask_state_for_loop(loop):
    import weakref
    global _ask_loop_state
    if _ask_loop_state is None:
        _ask_loop_state = weakref.WeakKeyDictionary()
    with _ask_loop_state_lock:
        st = _ask_loop_state.get(loop)
        if st is None:
            import asyncio
            st = {"sem": asyncio.Semaphore(_ASK_GLOBAL_SEMAPHORE_SIZE),
                  "in_flight": collections.Counter()}
            _ask_loop_state[loop] = st
        return st


def _reset_ask_loop_state_for_tests() -> None:
    """Test seam — drops the loop-keyed semaphore/in-flight state."""
    import weakref
    global _ask_loop_state
    with _ask_loop_state_lock:
        _ask_loop_state = weakref.WeakKeyDictionary()


def _call_sync(fn, args, kwargs):
    """Thread-pool trampoline: ``run_ask_bounded`` runs the SYNC ask lane
    (retrieval + annotation + reader) in an executor thread."""
    return fn(*args, **kwargs)


def ask_in_flight_capacity(team_id: str | None) -> bool:
    """True when the per-team in-flight ask cap still has room (or team_id
    is None). A cheap pre-check for the budget gates (#1987 P2): a request
    that ``run_ask_bounded`` will reject with 429 ``in_flight_limit`` must
    not burn a budget slot. Best-effort — a benign race can still charge a
    slot that later 429s, but the common full-cap case is skipped."""
    if not team_id:
        return True
    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return True
    st = _ask_state_for_loop(loop)
    return st["in_flight"][team_id] < _ASK_TEAM_IN_FLIGHT_CAP


async def run_ask_bounded(fn, team_id: str | None, *args, **kwargs):
    """Shared bounded ask runner (#1987 Task 7/8/9) — the ONE wrapper the
    hosted HTTP handler, the hosted MCP handler, and the selfhost REST
    handler all await.

    Bounds: global ``asyncio.Semaphore(8)`` + ``asyncio.wait_for(_ASK_TIMEOUT_S)``
    wrapping the FULL bounded section (semaphore acquire + the to_thread
    reader call) — total per-request latency is capped at ``_ASK_TIMEOUT_S``;
    a request queued behind 8 in-flight past the budget 504s (bounded, never
    an unbounded queue wait). The per-team in-flight cap (4) lives INSIDE
    this wrapper (shared by HTTP + MCP): the counter increments ON ENTRY
    (BEFORE ``Semaphore.acquire`` — queued asks count toward the team's cap
    while waiting) and is released on the ``to_thread`` future's COMPLETION
    (an ``add_done_callback`` — release on completion, NOT on ``wait_for``
    firing: a timed-out ask keeps its slot until the thread finishes, so a
    504 burst cannot leak counters/slots and the follow-up ask succeeds).

    The acquire-cancelled path (wait_for fires during the queue wait — no
    future exists) decrements the counter in the wrapper's finally.

    Raises ``AskInFlightLimitError`` (per-team cap), ``AskBoundedTimeoutError``
    (504), or the underlying exception from ``fn``.
    """
    import asyncio
    loop = asyncio.get_running_loop()
    st = _ask_state_for_loop(loop)
    sem = st["sem"]
    inflight = st["in_flight"]
    # ``_sdk_team_id`` is the bound SDK lane's metering team_id (hosted
    # HTTP/MCP handlers pass the team; selfhost passes None) — stripped here
    # so ``fn`` (sdk.ask) receives it WITHOUT colliding with this wrapper's
    # own ``team_id`` (the in-flight-cap key).
    fn_kwargs = dict(kwargs)
    sdk_team_id = fn_kwargs.pop("_sdk_team_id", team_id)
    if sdk_team_id is not None:
        fn_kwargs["team_id"] = sdk_team_id
    # contextvars do NOT propagate to run_in_executor worker threads (3.12,
    # empirically confirmed) — capture the selfhost-transport flag HERE (the
    # asyncio context) and thread it through so the executor-thread metering
    # exemption is honored (never a phantom :MeteringRecord).
    from tortoise.transport import _selfhost_transport
    if _selfhost_transport.get():
        fn_kwargs["_selfhost_transport"] = True
    if team_id:
        if inflight[team_id] >= _ASK_TEAM_IN_FLIGHT_CAP:
            raise AskInFlightLimitError(
                f"team {team_id!r} in-flight ask cap reached "
                f"({_ASK_TEAM_IN_FLIGHT_CAP})")
        inflight[team_id] += 1
    future = None
    t_start = time.monotonic()
    # The SEMAPHORE-ACQUIRE window is bounded by the REMAINDER of the total
    # budget after reserving the execution floor: a request that cannot be
    # acquired within ``_ASK_TIMEOUT_S - _ASK_EXEC_FLOOR_S`` 504s at acquire
    # WITHOUT starting (no wasted model call).
    acquire_timeout = max(0.0, _ASK_TIMEOUT_S - _ASK_EXEC_FLOOR_S)
    try:
        try:
            await asyncio.wait_for(sem.acquire(), timeout=acquire_timeout)
        except (TimeoutError, asyncio.CancelledError) as e:
            # wait_for fired during the queue wait — no future exists; the
            # counter decrements here (never a leaked counter).
            if team_id:
                inflight[team_id] -= 1
            if isinstance(e, asyncio.TimeoutError):
                raise AskBoundedTimeoutError(
                    f"ask queued past {acquire_timeout}s (8 in flight)") from e
            raise
        try:
            future = loop.run_in_executor(None, _call_sync, fn, args, fn_kwargs)
        except BaseException:
            # loop closed/shutting down → run_in_executor raises SYNCHRONOUSLY
            # (no future, no done-callback): release the slot + counter NOW.
            with contextlib.suppress(Exception):
                sem.release()
            if team_id:
                inflight[team_id] -= 1
            raise

        def _release(_fut) -> None:
            with contextlib.suppress(Exception):  # best-effort release — never blocks
                sem.release()
            if team_id:
                inflight[team_id] -= 1

        future.add_done_callback(_release)
        # The SECOND window uses the REMAINING budget (the acquire above may
        # have already consumed part of it) — total per-request latency is
        # truly capped at ``_ASK_TIMEOUT_S``.
        remaining = max(0.0, _ASK_TIMEOUT_S - (time.monotonic() - t_start))
        try:
            return await asyncio.wait_for(asyncio.shield(future),
                                          timeout=remaining)
        except TimeoutError as e:
            raise AskBoundedTimeoutError(
                f"ask exceeded {_ASK_TIMEOUT_S}s") from e
    except AskInFlightLimitError:
        raise
    except AskBoundedTimeoutError:
        raise
    except BaseException:
        # the future's done-callback releases slot + counter on completion
        raise
