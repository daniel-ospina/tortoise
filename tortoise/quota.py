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
MAX_DREAM_FULL_PER_HOUR = 6

# ── Default limits ──────────────────────────────────────────────────────────
# max_points/max_api_keys have NO constant here — they resolve from
# tortoise.pricing.tier_limits (product/pricing.json) so a legacy team without
# stored limits gets pricing-correct caps, never the stale 1000/20 consts that
# contradicted pricing.json (#310 GAP-B, review fix 2). max_sessions has no
# pricing.json field — flat 1000 across tiers (matches REST today).
DEFAULT_MAX_SESSIONS = 1000

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


def resolve_team_limits(team_id: str) -> dict:
    """Resolve a team's limits from the registry Team node.

    Missing Team node → QuotaCheckError (fail-closed; the auth layer should
    guarantee key→team mapping). Missing attributes → defaults
    (aligned with product/pricing.json free tier).
    """
    if not team_id:
        raise QuotaCheckError("resolve_team_limits requires a team_id")
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

    Supported resources: points, api_keys, sessions, users, graphs.
    """
    return _count_resource(team_id, resource, sdk=sdk)


def _count_resource(team_id: str, resource: str, sdk=None) -> int:
    """Count current usage for a resource. Raises QuotaCheckError on failure.

    Supported resources:
    - points: total nodes in tenant graph (MATCH (n) RETURN count(n))
    - api_keys: active (non-revoked) APIKey nodes in registry
    - sessions: total nodes in tenant graph (same counter as points)
    - users: active Membership nodes in registry
    - graphs: Graph nodes in registry
    """
    try:
        # ── Registry-scoped counts (api_keys, users, graphs) ──
        if resource in ("api_keys", "users", "graphs"):
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

        # ── Tenant-graph-scoped counts (points, sessions) ──
        if sdk is None:
            sdk = _make_sdk(namespace=team_id)
        rows = sdk._get_proj().g.query(
            "MATCH (n) RETURN count(n)",
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
        resource: "points" | "api_keys" | "sessions" | "users" | "graphs".
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
