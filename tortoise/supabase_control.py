"""Supabase control-plane client for hosted auth resolution (#669 plan Task 3).

The FalkorDB ``control_plane`` registry is being retired in favor of Supabase
(teams / team_memberships / api_keys, migrations 0006–0009). This module is
the read-side seam the hosted auth paths use AFTER the flip:

- API-key resolution (REST ``get_current_team`` + MCP
  ``TeamResolutionMiddleware``) hashes the presented ``tt_`` token with
  ``tortoise.auth.lookup_hash`` (SHA-256(pepper + key), plan P1-1) and does an
  exact-match indexed lookup — O(1), no registry scan.
- ``api_keys.revoked_at`` is the authoritative revocation source (plan P1-2):
  a revoked ``api_keys`` twin REJECTS even when the matching
  ``team_memberships`` row is active.
- Tier/quota come from the ``teams`` row (max_users/max_graphs/
  graph_size_cap); fields the 0006 schema does not store (max_points →
  graph_size_cap, max_api_keys/max_sessions) fall back to
  ``tortoise.pricing.tier_limits`` defaults, mirroring the registry path.
- Invitations (plan Task 4): mint/accept/rescind live here too — pending
  invitations are redeemed by plaintext token via indexed lookup_hash
  (SHA-256(pepper + token)), accept creates the real team_memberships row
  with the INVITED role, and dedup (team, email) + 7-day expiry are
  enforced (0008 partial unique index).
- Onboarding/GitHub (plan Task 6): onboarding_state (jsonb) + email are
  read/patched on ``teams``; github_token_enc/github_org are read/written
  ONLY through the service-role seam here — the column is REVOKEd from
  anon/authenticated in migration 0006, so this module is the sole access
  path for the encrypted token in Supabase mode.
- Health (plan Task 7): /health/ready probes this control plane as the
  second plane (AND with FalkorDB) in Supabase mode.

Fail-closed contract (backup-seam P1-3 pattern): every query error raises
``RuntimeError`` — auth never falls back to the registry and never
authenticates on error. ``update_last_used`` is the one best-effort exception
(#685 telemetry write-through must never gate auth). #1096 adds a second
exception at the resolve caller: a failure of the additive teams read (0015
``suspended_at``/``flagged_at``) degrades to safe defaults
(un-suspended/un-flagged) at WARNING; base/deletion reads still raise.

Env gating (plan Task 8 names the variable): ``TORTOISE_CONTROL_PLANE``
accepts ``supabase|registry``. When unset, Supabase resolution is the default
WHEN ``SUPABASE_URL`` + a service-role key are configured; selfhost stays
registry-backed (no Supabase creds → registry). The SDK registry code paths
in ``tortoise/sdk.py`` are untouched — this flip only covers the HOSTED auth
paths (hosted_api.py + mcp_auth.py).

Transport: plain HTTP against PostgREST via httpx (already a dependency) —
no new deps, matching the analytics-write pattern in hosted_api.py.

Query dialect: ``query(table, select, filters, method, json_body, order,
limit)`` where filters are ``(column, op, value)`` tuples with ops
``eq | neq | is`` (None → ``IS NULL``) and ``lte`` (ISO-8601 cutoff,
used by the deleted-team purge sweep, #302). ``method`` supports
``GET | POST | PATCH | DELETE`` (DELETE is used only by the post-grace
hard-delete purge). The test fake implements the SAME interface over
in-memory rows, so the resolution logic is shared verbatim between CI
and production.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

_logger = logging.getLogger(__name__)

# Env-var names: SUPABASE_SERVICE_ROLE_KEY is the canonical name (edge
# functions, supabase/README.md); SUPABASE_SERVICE_KEY is the legacy name the
# analytics write path uses — accept either so the flip works with both.
_SERVICE_KEY_ENV = ("SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_SERVICE_KEY")

# Base teams columns (migration 0006 — the core teams table; drift-safe).
# email is 0006, NOT 0015 — it rides the base read. deleted_at/graph_name
# are ALSO base: graph_name is 0006 and the token-recovery echo needs it;
# deleted_at is 20260813000001 and the #1709 recovery guard ("a deleted
# team is indistinguishable from never-existed → uniform 422") must read it
# for real — a deletion column belongs to the FAIL-CLOSED class (#1096),
# never an additive-fail-soft tier.
_TEAM_BASE_SELECT = [
    "id", "name", "tier", "max_users", "max_graphs", "graph_size_cap",
    "ops_allowance", "email", "deleted_at", "graph_name",
]
# Additive teams columns, separately migrated after 0006 (0015 abuse state;
# 20260813000005 dashboard_key_login; 20260817000001 import ledger + points
# cap override, #1230). A schema one migration behind raises PostgREST HTTP
# 400 on these; the auth seam must degrade to safe defaults (un-suspended/
# un-flagged; key login allowed; import ledger unset), never take down all
# auth (#1096, defense-in-depth behind the #1095 deploy gate).
_TEAM_ADDITIVE_SELECT = [
    "suspended_at", "flagged_at",
    # #1148: whether API-key login is accepted for the dashboard (management
    # surface). Default true; claimed owners toggle it (session-authed).
    "dashboard_key_login",
    # #1230: import idempotency ledger + quarantine record + points-cap
    # override (see _TEAM_ADDITIVE_IMPORT_TIER).
    "last_import_sha256", "last_import_quarantined_sha256", "max_points",
    # #1623: Stripe billing state (0012 migration — the webhook's store) so
    # /v1/team can render plan state + the dashboard Billing page.
    "subscription_status", "customer_email",
]
# Retry tiers for the fail-soft ladder (#1096): the NEWEST migration is
# dropped FIRST, so a schema missing only the newest additive (e.g.
# 20260813000005) still reads the older additive state (0015 carries REAL
# suspension data — discarding it would bypass enforcement with real data
# present).
_TEAM_ADDITIVE_IMPORT_TIER = [
    # #1230 import idempotency ledger + quarantine record (Team-node props).
    "last_import_sha256", "last_import_quarantined_sha256",
    # points-cap override (the plan's max_points / graph_size_cap source).
    "max_points",
]
_TEAM_ADDITIVE_DKL_TIER = ["dashboard_key_login"]      # 20260813000005
_TEAM_ADDITIVE_0015_TIER = ["suspended_at", "flagged_at"]  # 0015
# Stripe billing state (0012 — OLDER than 0015). Dropped LAST in the retry
# ladder (newest migration is dropped first), so a schema missing only 0015
# still reads real billing state and a pre-0012 schema degrades to safe
# defaults (None) rather than taking down auth. #1623.
_TEAM_ADDITIVE_BILLING_TIER = ["subscription_status", "customer_email"]

# Combined quota read (primary query) — the healthy path stays ONE round-trip.
_QUOTA_SELECT = _TEAM_BASE_SELECT + _TEAM_ADDITIVE_SELECT


def _service_key() -> str:
    for name in _SERVICE_KEY_ENV:
        val = os.environ.get(name)
        if val:
            return val
    return ""


def is_supabase_enabled() -> bool:
    """True when hosted auth should resolve from Supabase.

    ``TORTOISE_CONTROL_PLANE=registry`` forces the registry (selfhost).
    ``=supabase`` (or unset with creds configured) uses Supabase. Unset with
    no creds → registry (selfhost default, plan Task 8 env split).

    FAIL-CLOSED on explicit misconfiguration: ``TORTOISE_CONTROL_PLANE=
    supabase`` with missing creds returns True so ``get_control_plane()``
    raises → REST 500 / MCP 503 (code-review P2, PR #851). A deployment that
    opted into Supabase-only must NEVER silently authenticate via the
    registry — that would let registry-only keys pass in a Supabase-only
    deployment.
    """
    mode = os.environ.get("TORTOISE_CONTROL_PLANE", "").strip().lower()
    if mode == "registry":
        return False
    configured = bool(os.environ.get("SUPABASE_URL")) and bool(_service_key())
    if mode == "supabase":
        return True  # get_control_plane() raises when creds are missing
    return configured


class SupabaseControlPlane:
    """PostgREST client for control-plane reads/writes (service role).

    Fail-closed: non-2xx responses and transport errors raise RuntimeError,
    so callers can never mistake an outage for "key not found" (a 401) or
    silently degrade to the registry. (Seam-level contract — the resolve
    caller may intentionally swallow an additive-read failure per #1096;
    base/deletion reads still raise.)
    """

    def __init__(self, url: str | None = None, service_key: str | None = None,
                 timeout: float = 5.0):
        self._url = (url or os.environ.get("SUPABASE_URL", "")).rstrip("/")
        self._key = service_key or _service_key()
        self._timeout = timeout
        if not self._url or not self._key:
            raise RuntimeError(
                "Supabase control plane not configured — SUPABASE_URL and a "
                "service-role key (SUPABASE_SERVICE_ROLE_KEY/SUPABASE_SERVICE_KEY) "
                "are required"
            )
        # Persistent client — one connection pool per control-plane instance
        # (created on first use via get_control_plane). Per-request clients
        # would pay a fresh TCP+TLS handshake on EVERY auth resolution
        # (resolve_api_key makes 2-3 queries per request; code-review P2,
        # PR #851). httpx.Client is safe for concurrent use.
        import httpx
        self._http = httpx.Client(timeout=self._timeout)

    def rpc(self, fn: str, body: dict | None = None) -> dict | None:
        """Call a Postgres function via PostgREST RPC (#765 plan Task 8).

        ``POST {url}/rest/v1/rpc/{fn}`` with the service key and JSON body.
        Used by the writer flip for ``provision_team`` (the atomic
        teams + team_memberships + api_keys upsert, migration 0010) — the
        agent-signup / register / team-create writers must NOT hand-roll
        three table writes when the RPC is one transaction.

        Fail-closed contract (same as ``query``): non-2xx responses and
        transport errors raise RuntimeError. Uses the same persistent httpx
        client — never ``with self._http`` (httpx 0.28 closes externally-
        constructed clients on __exit__; re-review P0, PR #851).
        """
        url = f"{self._url}/rest/v1/rpc/{fn}"
        headers = {
            "apikey": self._key,
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        }
        try:
            import httpx  # noqa: F401
            resp = self._http.post(url, params={"select": "*"},
                                   headers=headers, json=body or {})
        except RuntimeError:
            raise
        except Exception as e:  # transport errors — fail closed
            raise RuntimeError(
                f"Supabase control-plane RPC failed ({fn}): {e}"
            ) from e
        if resp.status_code >= 300:
            # #1709 fixer P1: carry the PostgREST error body's "message"
            # (for a Postgres RAISE EXCEPTION it is the RAISE text, e.g.
            # "recover_team_key: token not found or revoked") so caller-side
            # code mappings (_RECOVER_ERROR_CODES / _CLAIM_ERROR_CODES) can
            # actually fire. The seam previously discarded the body and the
            # RuntimeError carried ONLY "HTTP 400" — every substring mapping
            # was dead in production (recover degraded to a 500 instead of
            # the uniform 422). Deliberately best-effort: a body that is not
            # JSON (or lacks "message") degrades to the old shape.
            detail = ""
            try:
                err = resp.json()
                if isinstance(err, dict):
                    detail = err.get("message") or ""
            except Exception:
                pass
            suffix = f": {detail}" if detail else ""
            raise RuntimeError(
                f"Supabase control-plane RPC failed ({fn}): "
                f"HTTP {resp.status_code}{suffix}"
            )
        if not resp.content:
            return None
        try:
            data = resp.json()
        except Exception as e:
            raise RuntimeError(
                f"Supabase control-plane bad RPC response ({fn}): {e}"
            ) from e
        return data if isinstance(data, dict) else None

    def query(self, table: str, *, select: list[str] | None = None,
              filters: list[tuple[str, str, object]] | None = None,
              method: str = "GET", json_body: dict | None = None,
              order: str | None = None, limit: int | None = None) -> list[dict]:
        """Run one PostgREST call. Returns row dicts; [] for PATCH/no rows.

        Filters: (column, op, value) with ops ``eq``, ``neq``, ``is``
        (value None → ``col=is.null``), ``gt``, ``lt``. Raises RuntimeError
        on any failure.
        """
        url = f"{self._url}/rest/v1/{table}"
        params: dict[str, str] = {}
        if select:
            params["select"] = ",".join(select)
        for col, op, value in filters or []:
            if op == "is":
                params[col] = "is.null" if value is None else f"is.{value}"
            elif op == "eq":
                params[col] = f"eq.{value}"
            elif op == "neq":
                params[col] = f"neq.{value}"
            elif op in ("gt", "lt"):
                # #765 plan Task 8: reconcile (expires_at < now) + the
                # signup/team-creation rate-limit counts (created_at > cutoff)
                # need ordered comparisons. NULL semantics mirror SQL: a row
                # with a NULL column never matches (PostgREST's gt./lt. is
                # NULL-excluding; the fake mirrors this).
                params[col] = f"{op}.{value}"
            elif op == "lte":
                # ISO-8601 cutoff for the post-grace purge sweep (#302).
                params[col] = f"lte.{value}"
            else:
                raise ValueError(f"unsupported filter op {op!r}")
        if order:
            params["order"] = order
        if limit is not None:
            params["limit"] = str(limit)

        headers = {
            "apikey": self._key,
            "Authorization": f"Bearer {self._key}",
        }
        try:
            import httpx  # noqa: F401
            # NOTE: do NOT use `with self._http as client:` here — for a
            # client constructed OUTSIDE the context manager, __exit__ CLOSES
            # it (httpx 0.28.1: "Cannot reopen a client instance, once it has
            # been closed"). resolve_api_key makes 2-3 queries per request;
            # the second would raise and kill every auth resolution. Direct
            # method calls on the persistent client are the documented pattern
            # (re-review P0, PR #851).
            if method == "GET":
                resp = self._http.get(url, params=params, headers=headers)
            elif method == "PATCH":
                headers["Content-Type"] = "application/json"
                # return=representation when a select is given → the caller
                # sees the UPDATED rows ([] when the WHERE matched nothing),
                # enabling atomic conditional claims (single UPDATE ... WHERE
                # + rowcount via body, PR #1264 review P2).
                headers["Prefer"] = ("return=representation" if select
                                      else "return=minimal")
                resp = self._http.patch(url, params=params, headers=headers,
                                        json=json_body or {})
            elif method == "POST":
                headers["Content-Type"] = "application/json"
                headers["Prefer"] = "return=representation"
                resp = self._http.post(url, params=params, headers=headers,
                                       json=json_body or {})
            elif method == "DELETE":
                # PostgREST row delete (service role). Only used by the
                # post-grace hard-delete purge (#302) — soft paths PATCH.
                headers["Prefer"] = "return=minimal"
                resp = self._http.delete(url, params=params, headers=headers)
            else:
                raise ValueError(f"unsupported method {method!r}")
        except RuntimeError:
            raise
        except Exception as e:  # transport errors — fail closed
            raise RuntimeError(
                f"Supabase control-plane query failed ({table}): {e}"
            ) from e
        if resp.status_code >= 300:
            raise RuntimeError(
                f"Supabase control-plane query failed ({table}): "
                f"HTTP {resp.status_code}"
            )
        if not resp.content:
            return []
        try:
            data = resp.json()
        except Exception as e:
            raise RuntimeError(
                f"Supabase control-plane bad response ({table}): {e}"
            ) from e
        return data if isinstance(data, list) else [data]


# ── Singleton ──────────────────────────────────────────────────────────────

_control_plane: SupabaseControlPlane | None = None


def get_control_plane() -> SupabaseControlPlane:
    """Lazy process-wide control-plane client (tests monkeypatch this)."""
    global _control_plane
    if _control_plane is None:
        _control_plane = SupabaseControlPlane()
    return _control_plane


# ── Abuse store seam (#308) ─────────────────────────────────────────────────

_abuse_store = None


_REGISTRY_ABUSE_FIELDS = {"suspended_at", "flagged_at"}


def _registry_abuse_write(team_id: str, field: str, value) -> None:
    """Durable selfhost enforcement (scoping delta 4, code-review P1 fix):
    write ONE suspension/staging field onto the registry Team node so the
    auth seams' prop reads actually reject. Field-scoped (never both props
    at once) so concurrent flag/suspend writes cannot clobber each other
    (code-review P2). Lazy hosted_api import — the hosted_api→
    supabase_control import direction forbids module-level use."""
    if field not in _REGISTRY_ABUSE_FIELDS:
        raise ValueError(f"not an abuse state field: {field!r}")
    from tortoise import hosted_api as _ha
    sdk = _ha._make_sdk(namespace="registry")
    sdk._get_registry().query(
        f"MATCH (t:Team {{id: $id}}) SET t.{field} = $value",
        params={"id": team_id, "value": value},
    )


def get_abuse_store():
    """Lazy abuse-event store (tests monkeypatch this, mirroring
    get_control_plane). Supabase mode → durable SupabaseAbuseStore; registry
    mode → MemoryAbuseStore with a registry-write callback so enforcement is
    durable in selfhost too (R2 session-mint under-counting remains the
    documented registry degradation)."""
    global _abuse_store
    if _abuse_store is None:
        from tortoise.abuse import MemoryAbuseStore, SupabaseAbuseStore
        if is_supabase_enabled():
            _abuse_store = SupabaseAbuseStore(get_control_plane())
        else:
            _abuse_store = MemoryAbuseStore(
                registry_write=_registry_abuse_write)
    return _abuse_store


# ── Shared resolution logic (REST + MCP single source of truth) ────────────

def _parse_ts(value) -> datetime | None:
    """Parse a PostgREST/registry timestamp into an aware datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)  # noqa: UP017
    return parsed


def _teams_row_fail_soft(cp, team_id: str, *, select: list[str],
                         additive_tiers: list[list[str]]) -> dict | None:
    """Teams row with fail-soft additive columns (#1096).

    Primary query selects base+additive (one round-trip). When it raises —
    a missing additive column → PostgREST HTTP 400 (PGRST204 per the
    error reference; the error-blind seam discards the body) — retry
    progressively dropping the additive TIERS (newest migration first:
    tier[0] dropped first), padding the dropped tier's fields to safe
    defaults. Tiered so a schema missing only the NEWEST additive
    (e.g. 20260813000005 dashboard_key_login) still reads the OLDER
    additive state (0015 suspended_at/flagged_at carry REAL suspension
    data — discarding it would bypass enforcement with real data present).
    If even the base-only select fails, the error PROPAGATES (fail-closed:
    a broken teams table, or a missing base/deletion column — e.g.
    team_by_id's #302 deleted_at/grace_hours stay in THAT call site's base
    set — must never authenticate or open a kill-switch guard). Logged at
    WARNING per failed attempt so drift stays diagnosable (#1001
    post-mortem). Accepted-by-scope: a non-drift failure of the combined
    read degrades enforcement for the degrade duration (one auth; up to
    +60s on MCP) — the closure (error discrimination + revoked_at
    stamping) is deferred to the #1096 escalation decomposition.
    """
    additive = [c for tier in additive_tiers for c in tier]
    additive_defaults = {c: None for c in additive}
    # Retry ladder: full select → drop tier[0] → drop tier[0..1] → … → base.
    last_exc: Exception | None = None
    for k in range(len(additive_tiers) + 1):
        dropped = {c for tier in additive_tiers[:k] for c in tier}
        attempt_select = [c for c in select if c not in dropped]
        try:
            rows = cp.query("teams", select=attempt_select,
                            filters=[("id", "eq", team_id)])
            break
        except Exception as e:
            last_exc = e
            if not dropped:
                _logger.warning(
                    "teams read failed for %s (select=%s) — retrying with "
                    "the newest additive tier dropped (%s); a missing "
                    "additive column degrades, a missing base/deletion "
                    "column fails closed (%s)",
                    team_id, select,
                    additive_tiers[0] if additive_tiers else None, e)
            elif set(additive) <= dropped:
                # Terminal rung — the for-else below logs the single fatal
                # WARNING and raises; log nothing here to avoid a duplicate.
                pass
            else:
                _logger.warning(
                    "teams read failed for %s (select=%s) — retrying with "
                    "the next additive tier dropped (%s): %s",
                    team_id, select, additive_tiers[k], e)
    else:
        assert last_exc is not None
        _logger.warning(
            "teams base-only read failed for %s (select=%s) — "
            "fail-closed (missing base column or control-plane outage): %s",
            team_id, [c for c in select if c not in additive], last_exc)
        raise last_exc
    if not rows:
        return None
    return {**additive_defaults, **rows[0]}


def resolve_api_key(cp, token: str) -> dict | None:
    """Resolve a ``tt_`` token against Supabase. None = not found/rejected.

    Order (plan P1-1/P1-2):
    1. ``api_keys`` by lookup_hash (unique index — O(1)). Reject when
       ``revoked_at`` is set (AUTHORITATIVE — a revoked twin rejects even
       when the team_memberships row is active) or ``expires_at`` passed
       (#742 semantics).
    2. ``team_memberships`` by lookup_hash AND status='active' (long-lived
       keys; bootstrap/recovery session keys only exist in api_keys).
    3. tier/quota from the ``teams`` row.

    Fail-closed: a control-plane error raises (RuntimeError) — it never
    returns None (401) and never falls back to the registry. EXCEPTION
    (#1096): a failure of the additive teams read (0015 suspended_at/
    flagged_at — separately-migrated columns) degrades to safe defaults
    (un-suspended/un-flagged) and is logged at WARNING; the drift-safe
    base read still raises on failure (a broken teams table never
    authenticates).

    Returns the same dict shape as the registry get_current_team path
    (team_id, key_id, tier, max_users, max_graphs, max_points, max_api_keys,
    max_sessions) plus additive metadata (key_prefix/created_via/created_by).
    """
    from tortoise.auth import lookup_hash
    from tortoise.quota import DEFAULT_MAX_SESSIONS

    now = datetime.now(timezone.utc)  # noqa: UP017
    h = lookup_hash(token)
    team_id = key_id = created_via = created_by = key_prefix = None

    # api_keys read (step 1): "enabled" is an additive column
    # (20260813000005, #1148 — dashboard key-login toggle). A schema one
    # migration behind 400s on it; fail soft to the pre-#1148 default
    # (enabled), never take down all auth (#1096). Accepted-by-scope: the
    # error-blind seam swallows ANY combined-read failure (drift or a
    # transient base-ok/additive-fail error) — a stored enabled=False key
    # re-authenticates for the degrade duration (same fail-open class as
    # the teams ladder; documented in the #1096 plan). The base api_keys
    # columns (0007) stay fail-closed: a failure of the base-only retry
    # propagates.
    _API_KEY_BASE_SELECT = ["id", "team_id", "key_prefix", "created_via",
                            "created_by", "expires_at", "revoked_at"]
    try:
        rows = cp.query(
            "api_keys", select=_API_KEY_BASE_SELECT + ["enabled"],  # noqa: RUF005
            filters=[("lookup_hash", "eq", h)],
        )
    except Exception as e:
        _logger.warning(
            "api_keys read failed — retrying without additive 'enabled'; is "
            "migration 20260813000005 applied? (%s)", e)
        try:
            rows = cp.query(
                "api_keys", select=_API_KEY_BASE_SELECT,
                filters=[("lookup_hash", "eq", h)],
            )
        except Exception as e2:
            _logger.warning(
                "api_keys base-only read failed — fail-closed (missing base "
                "column or control-plane outage): %s", e2)
            raise
    if rows:
        row = rows[0]
        if row.get("revoked_at") is not None:
            # P1-2: api_keys.revoked_at is the revocation source of truth.
            return None
        if row.get("enabled") is False:
            # #1148: a disabled key stops authenticating (per-key toggle;
            # re-enable anytime). Hashes-only stored — same reject path as
            # revoked, distinct reason for the 401 detail.
            return None
        expires_at = _parse_ts(row.get("expires_at"))
        if expires_at is not None and expires_at <= now:
            # #742: expired keys must NOT authenticate.
            return None
        team_id = row["team_id"]
        key_id = row["id"]
        created_via = row.get("created_via")
        created_by = row.get("created_by")
        key_prefix = row.get("key_prefix")
    else:
        memberships = cp.query(
            "team_memberships",
            select=["team_id"],
            filters=[("lookup_hash", "eq", h), ("status", "eq", "active")],
        )
        if not memberships:
            return None  # registry-only key → nothing resolves (E2E-7-negative)
        team_id = memberships[0]["team_id"]

    team_row = _teams_row_fail_soft(
        cp, team_id, select=_QUOTA_SELECT,
        additive_tiers=[_TEAM_ADDITIVE_IMPORT_TIER,
                         _TEAM_ADDITIVE_DKL_TIER, _TEAM_ADDITIVE_0015_TIER,
                         _TEAM_ADDITIVE_BILLING_TIER])
    if team_row is None:
        # Key's team vanished — fail closed (401), never authenticate.
        return None

    # #1082 PR2: anon-ceiling derivation at the auth boundary — an
    # unclaimed zero-email team resolves to the reduced ``anon`` tier until
    # claimed (owner user_id linked). Fail-open to stored tier on error.
    from tortoise.quota import derived_tier
    tier = derived_tier({**team_row, "id": team_id})
    from tortoise.pricing import tier_limits
    lim = tier_limits(tier)
    anon_override = tier == "anon"  # #1082 PR2: stored caps were minted at
    # free values (agent_signup provisions tier='free' columns); when the
    # unclaimed-owner predicate derives anon, override read-time with the
    # reduced anon tier values — never leave free caps on an anon team.
    max_users = team_row.get("max_users")
    max_graphs = team_row.get("max_graphs")
    graph_size_cap = team_row.get("graph_size_cap")
    # #1148: dashboard key-login acceptance — normalized BEFORE the dict so
    # a None (additive-read degrade, #1096) becomes the safe default True
    # (the column is NOT NULL DEFAULT true; a drifted schema must not 403
    # key-auth management for teams that never disabled it). A stored False
    # is carried as-is (the gate stays closed).
    _dkl = team_row.get("dashboard_key_login")
    return {
        "team_id": team_id,
        "key_id": key_id,
        "tier": tier,
        # max_users/max_graphs: preserve None (unlimited, Team tier) and fall
        # back to pricing when the column is missing (mirrors registry path).
        # anon tier overrides BOTH stored and pricing (reduced caps bind).
        "max_users": (lim["max_users_per_team"] if anon_override
                      else (max_users if max_users is not None else lim["max_users_per_team"])),
        "max_graphs": (lim["max_graphs_per_team"] if anon_override
                       else (max_graphs if max_graphs is not None else lim["max_graphs_per_team"])),
        # points counter counts graph nodes → graph_size_cap (#310 GAP-B);
        # anon tier forces the reduced node cap.
        "max_points": (int(lim["max_graph_nodes"]) if anon_override
                       else (int(graph_size_cap) if graph_size_cap is not None else lim["max_graph_nodes"])),
        # 0006 teams has no max_api_keys/max_sessions columns — pricing/defaults
        "max_api_keys": lim["max_api_keys"],
        "max_sessions": DEFAULT_MAX_SESSIONS,
        # additive metadata (not part of the registry dict contract)
        "key_prefix": key_prefix,
        "created_via": created_via,
        "created_by": created_by,
        # #1148: per-key enabled state (dashboard toggle)
        "enabled": row.get("enabled", True) if rows else True,
        "dashboard_key_login": True if _dkl is None else _dkl,
        # #308: enforcement (403 SUSPENDED) + owner notification
        "suspended_at": team_row.get("suspended_at"),
        "flagged_at": team_row.get("flagged_at"),
        "email": team_row.get("email"),
        # #1623: Stripe billing state (webhook store, 0012) — /v1/team
        # renders plan state from these.
        "subscription_status": team_row.get("subscription_status"),
        "customer_email": team_row.get("customer_email"),
    }


def update_last_used(cp, key_id: str) -> None:
    """#685 write-through on api_keys.last_used_at. Best-effort by contract —
    a telemetry write must NEVER gate authentication (registry path mirrors
    this with try/except around the SET)."""
    try:
        cp.query(
            "api_keys",
            method="PATCH",
            filters=[("id", "eq", key_id)],
            json_body={"last_used_at": datetime.now(timezone.utc).isoformat()},  # noqa: UP017
        )
    except Exception:
        _logger.debug("update_last_used failed for key %s (best-effort)", key_id)


# ── Session-path helpers (get_current_user memberships, E1/E6/E8) ──────────

def user_memberships(cp, user_id: str) -> list[dict]:
    """Active memberships for a JWT user: [{team_id, role}].

    Placeholder rows (team_id='') are excluded — mirrors the registry
    ``_user_memberships`` predicate (plan §4.1 step 6).
    """
    rows = cp.query(
        "team_memberships",
        select=["team_id", "role"],
        filters=[("user_id", "eq", user_id), ("status", "eq", "active"),
                 ("team_id", "neq", "")],
    )
    return [{"team_id": r["team_id"], "role": r["role"]} for r in rows]


def membership_for_user_team(cp, user_id: str, team_id: str) -> dict | None:
    """Active membership for (user, team) → {team_id, role} | None."""
    rows = cp.query(
        "team_memberships",
        select=["role"],
        filters=[("user_id", "eq", user_id), ("team_id", "eq", team_id),
                 ("status", "eq", "active")],
    )
    if not rows:
        return None
    return {"team_id": team_id, "role": rows[0]["role"]}


def mint_target_user_for_key(cp, key_created_by, team_id: str) -> str | None:
    """#1511: the user a key's session-exchange should mint for.

    Returns the key's creator user_id when it is an ACTIVE member of the
    team (the exchange mints the CREATOR's session — no escalation: a
    member's key mints the member's session, team-scoped claims only), else
    None. The endpoint branches on the `created_by` SHAPE BEFORE calling:
    an anon/identity string on an owner-less team → ANON_TEAM_NO_OWNER; an
    "api"/NULL/unknown shape → KEY_NOT_USER_MINTED. Control-plane fact only
    (FakeControlPlane-testable); the GoTrue admin fetch + mint live in
    hosted_api.py.
    """
    if not key_created_by:
        return None
    mem = membership_for_user_team(cp, key_created_by, team_id)
    return key_created_by if mem is not None else None


def team_by_id(cp, team_id: str) -> dict | None:
    """Team row (registry-properties-shaped dict) or None.

    Additive columns fail soft (#1096): 0015 suspension/staging
    (suspended_at/flagged_at) + 20260813000005 dashboard_key_login (no
    team_by_id consumer reads it — included for seam uniformity). A schema
    missing them returns the row with safe None defaults, never raises.
    The #302 soft-delete columns (20260813000001 deleted_at/grace_hours)
    are NOT additive-fail-soft: the deletion kill-switch guard must fail
    closed, never open (a schema missing them cannot have soft-deleted
    rows — the write path is equally drifted — so fail-closed is safe).
    """
    return _teams_row_fail_soft(
        cp, team_id,
        select=["id", "name", "tier", "email", "graph_name", "max_users",  # noqa: RUF005
                "max_teams", "max_graphs", "ops_allowance", "graph_size_cap",
                "backup_enabled", "backup_latest_at", "backup_restored_at",
                "created_at", "deleted_at", "grace_hours"]
            + _TEAM_ADDITIVE_SELECT,
        additive_tiers=[_TEAM_ADDITIVE_IMPORT_TIER,
                         _TEAM_ADDITIVE_DKL_TIER, _TEAM_ADDITIVE_0015_TIER,
                         _TEAM_ADDITIVE_BILLING_TIER])


# ── Session-key mint writes (E2E-2 round-trip: mint → api_keys → resolve) ──

def active_api_keys(cp, team_id: str, *, created_via: str | None = None,
                    created_by: str | None = None) -> list[dict]:
    """Non-revoked, non-expired api_keys rows for a team (#742 expiry).

    Optional created_via/created_by filters (bootstrap cap / recovery cap
    queries). Expiry is filtered here (PostgREST dialect stays minimal).
    """
    filters: list[tuple[str, str, object]] = [
        ("team_id", "eq", team_id), ("revoked_at", "is", None),
    ]
    if created_via is not None:
        filters.append(("created_via", "eq", created_via))
    if created_by is not None:
        filters.append(("created_by", "eq", created_by))
    rows = cp.query(
        "api_keys",
        select=["id", "team_id", "created_via", "created_by", "created_at",
                "expires_at", "revoked_at"],
        filters=filters,
    )
    now = datetime.now(timezone.utc)  # noqa: UP017
    return [
        r for r in rows
        if (exp := _parse_ts(r.get("expires_at"))) is None or exp > now
    ]


def insert_api_key(cp, row: dict) -> None:
    """Insert an api_keys row (session-key mint). Raises on failure (the mint
    must not claim success for a key that cannot resolve)."""
    cp.query("api_keys", method="POST", json_body=row)


def revoke_api_key(cp, key_id: str, now: str | None = None) -> None:
    """Set revoked_at on an api_keys row (recovery-cap auto-revoke)."""
    cp.query(
        "api_keys",
        method="PATCH",
        filters=[("id", "eq", key_id)],
        json_body={"revoked_at": now or datetime.now(timezone.utc).isoformat()},  # noqa: UP017
    )

def set_api_key_enabled(cp, key_id: str, enabled: bool) -> None:
    """#1148: enable/disable an API key (per-key toggle). Disabled keys stop
    authenticating (resolve_api_key rejects enabled=false) but stay listed —
    re-enable anytime. The dashboard toggle (PATCH /v1/team/keys/{id}) calls
    this. Session-authed + owner-only enforced at the endpoint."""
    cp.query(
        "api_keys",
        method="PATCH",
        filters=[("id", "eq", key_id)],
        json_body={"enabled": bool(enabled)},
    )


def set_api_key_name(cp, key_id: str, name: str | None) -> None:
    """Rename an API key (user-facing label, PATCH /v1/team/keys/{id}).

    None clears the label back to unnamed. Never part of authentication —
    display metadata only. Session-authed + owner-only enforced at the
    endpoint (same guard as set_api_key_enabled)."""
    cp.query(
        "api_keys",
        method="PATCH",
        filters=[("id", "eq", key_id)],
        json_body={"name": name},
    )


def set_dashboard_key_login(cp, team_id: str, enabled: bool) -> None:
    """#1148: set whether API-key login is accepted for the dashboard
    (management surface). Claimed owners toggle this (session-authed,
    PATCH /v1/team/dashboard-login). When false, key-auth management calls
    return 403 dashboard_login_disabled; graph endpoints keep accepting the
    key. Anon teams always keep it true (the Protect screen IS the bootstrap)."""
    cp.query(
        "teams",
        method="PATCH",
        filters=[("id", "eq", team_id)],
        json_body={"dashboard_key_login": bool(enabled)},
    )


# ── Invitations (plan Task 4: mint / accept / rescind via lookup_hash) ────

class InvitationError(Exception):
    """Semantic invitation rejection (dedup, expiry, single-use, role).

    Carries an HTTP status so hosted_api can translate directly (409 for
    dedup/already-member, 403 for role/email mismatch, 400 for
    invalid/expired/used/revoked, 404 unknown, 422 bad role). Deliberately
    NOT a RuntimeError: these are expected client outcomes, not control-
    plane failures — the fail-closed contract (query errors → RuntimeError
    → 500) is untouched.
    """

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def invitation_mint(cp, team_id: str, email: str, role: str,
                    invited_by: str, expires_days: int = 7,
                    inviter_email: str | None = None) -> dict:
    """Create a pending invitations row; returns the plaintext token ONCE.

    The token is minted here and stored only as ``lookup_hash`` (SHA-256(pepper
    + token), plan P1-1) — the hash is computed in-process via
    ``tortoise.auth.lookup_hash``; the plaintext is returned to the caller
    exactly once. Acceptance is an O(1) indexed lookup_hash match, no scan.

    Dedup: the 0008 partial unique index (team_id, email) WHERE
    status='pending' — a duplicate PENDING invite raises InvitationError(409);
    accepted/revoked invites don't block a fresh re-invite.

    Role: 0008 CHECK closes the enum to 'admin' | 'member' (owner is not
    invitable — single-owner model, D7 #574).
    """
    import uuid  # noqa: I001
    from datetime import datetime, timedelta
    from tortoise.auth import lookup_hash as _lookup_hash

    role = (role or "member").strip().lower()
    email = (email or "").strip().lower()
    if not team_id or not email or "@" not in email:
        raise InvitationError("team_id and valid email required", status=422)
    if role not in ("admin", "member"):
        raise InvitationError("role must be 'admin' or 'member'", status=422)

    dup = cp.query(
        "invitations",
        select=["id"],
        filters=[("team_id", "eq", team_id), ("email", "eq", email),
                 ("status", "eq", "pending")],
    )
    if dup:
        raise InvitationError(
            f"Pending invitation already exists for {email} in this team",
            status=409,
        )

    token = str(uuid.uuid4())
    iid = uuid.uuid4().hex[:26]
    now = datetime.now(timezone.utc)  # noqa: UP017
    expires_at = (now + timedelta(days=expires_days)).isoformat()
    try:
        cp.query(
            "invitations",
            method="POST",
            json_body={
                "id": iid,
                "team_id": team_id,
                "lookup_hash": _lookup_hash(token),
                "role": role,
                "invited_by": invited_by,
                "inviter_email": inviter_email,
                "email": email,
                "status": "pending",
                "expires_at": expires_at,
            },
        )
    except RuntimeError as e:
        # Concurrent duplicate mint: the partial unique index (team_id, email)
        # WHERE status='pending' rejects the loser with PostgREST 409 — map
        # that to the documented InvitationError(409) instead of a 500
        # (code-review P2, PR #864). The pre-check above is a friendly
        # fast-path; the index is the authoritative dedup.
        if "HTTP 409" in str(e):
            raise InvitationError(
                f"Pending invitation already exists for {email} in this team",
                status=409,
            ) from e
        raise
    return {"id": iid, "email": email, "role": role,
            "expires_at": expires_at, "status": "pending",
            "token": token}


def invitation_accept(cp, token: str, user_id: str,
                      user_email: str | None = None) -> dict:
    """Accept a pending invitation by plaintext token (lookup_hash verify).

    Atomic single-use (E2E-3): the invitation is PATCHed to status='accepted'
    with a conditional ``WHERE status='pending'`` filter, then re-read to
    verify — a concurrent accept loses the race and the verify fails. On
    success the REAL team_memberships row is created with the INVITED role
    (O/I/T: accepted membership carries the invited role), status='active'.

    Rejects (InvitationError): unknown token (400), expired (400), used /
    already accepted (400), revoked (400), already an active member (409),
    JWT email vs invite email mismatch when the JWT carries an email (403).

    A previously removed/invited membership row for (user, team) is
    resurrected in place (registry MERGE semantics) rather than INSERTed —
    uq_member_team (user_id, team_id) would reject a duplicate row.
    """
    import uuid  # noqa: I001
    from tortoise.auth import lookup_hash as _lookup_hash

    now = datetime.now(timezone.utc)  # noqa: UP017
    h = _lookup_hash(token)
    rows = cp.query(
        "invitations",
        select=["id", "team_id", "email", "role", "status", "expires_at"],
        filters=[("lookup_hash", "eq", h)],
    )
    if not rows:
        raise InvitationError("Invalid or expired invite token")
    inv = rows[0]

    status = inv.get("status") or "pending"
    if status != "pending":
        if status == "accepted":
            raise InvitationError("Invitation has already been accepted")
        if status == "revoked":
            raise InvitationError("Invitation has been revoked")
        raise InvitationError("Invitation is not pending")
    exp = _parse_ts(inv.get("expires_at"))
    if exp is not None and exp <= now:
        raise InvitationError("Invite token expired")

    if user_email and user_email.strip().lower() != (inv.get("email") or "").lower():
        # Invitee must be the invitee's account (registry-path guard, #574).
        raise InvitationError("Invite email does not match this account",
                              status=403)

    # Existing membership for (user, team) — any status.
    existing = cp.query(
        "team_memberships",
        select=["id", "status"],
        filters=[("user_id", "eq", user_id), ("team_id", "eq", inv["team_id"])],
    )
    if existing and existing[0].get("status") == "active":
        raise InvitationError("Already a member of this team", status=409)

    # Quota/tier gate (code-review P2, PR #864): the registry accept path
    # routes through membership_create → max_users gate → 402 on quota. An
    # invite minted on Team tier and accepted after a downgrade to free must
    # not exceed the free tier's 1-user limit — mirror the gate here.
    team = team_by_id(cp, inv["team_id"])
    if team is None:
        raise InvitationError("Team no longer exists", status=404)
    if team.get("deleted_at"):
        # #302: a soft-deleted team must not mint memberships. Closes the
        # race between the delete cascade and a concurrent accept (the
        # membership kill-switch and the invitation revoke are separate
        # writes).
        raise InvitationError("Team is scheduled for deletion", status=410)
    from tortoise.pricing import tier_limits
    tier = team.get("tier") or "free"
    lim = tier_limits(tier)
    max_users = team.get("max_users")
    if max_users is None:
        max_users = lim.get("max_users_per_team")
    if max_users is not None:
        member_count = cp.query(
            "team_memberships",
            select=["id"],
            filters=[("team_id", "eq", inv["team_id"]),
                     ("status", "eq", "active")],
        )
        if len(member_count) >= int(max_users):
            raise InvitationError(
                "Team member limit reached", status=402)

    # Single-use: conditional PATCH (status='pending' filter) then verify.
    cp.query(
        "invitations",
        method="PATCH",
        filters=[("id", "eq", inv["id"]), ("status", "eq", "pending")],
        json_body={"status": "accepted", "accepted_at": now.isoformat()},
    )
    check = cp.query(
        "invitations", select=["status"], filters=[("id", "eq", inv["id"])],
    )
    if not check or check[0].get("status") != "accepted":
        # Lost the accept race — the invite was consumed in between.
        raise InvitationError("Invitation has already been accepted")

    membership_id = uuid.uuid4().hex[:26]
    try:
        if existing:
            cp.query(
                "team_memberships",
                method="PATCH",
                filters=[("id", "eq", existing[0]["id"])],
                json_body={"role": inv["role"], "status": "active",
                           "invited_email": inv.get("email"),
                           "updated_at": now.isoformat()},
            )
        else:
            cp.query(
                "team_memberships",
                method="POST",
                json_body={
                    "id": membership_id,
                    "user_id": user_id,
                    "team_id": inv["team_id"],
                    # 0001 NOT NULL columns; key_hash='pending' is the
                    # reconcilable-placeholder sentinel (an invited member has no
                    # key of their own — session keys live in api_keys).
                    "team_name": (team or {}).get("name") or "",
                    "key_hash": "pending",
                    "graph_name": (team or {}).get("graph_name") or "",
                    "role": inv["role"],  # invited role preserved (O/I/T)
                    "status": "active",
                    "invited_email": inv.get("email"),
                },
            )
    except Exception:
        # Compensating write (code-review P2, PR #864): the invite was already
        # consumed above — if the membership write fails (transient
        # control-plane error), roll the invite back to pending so the invitee
        # can retry instead of burning the invite permanently. Best-effort:
        # if THIS rollback also fails, the original error propagates (500) and
        # the invite stays consumed — an operator can re-mint.
        try:  # noqa: SIM105
            cp.query(
                "invitations",
                method="PATCH",
                filters=[("id", "eq", inv["id"])],
                json_body={"status": "pending", "accepted_at": None},
            )
        except Exception:
            pass
        raise
    return {"team_id": inv["team_id"], "role": inv["role"]}


def invitation_info_by_token(cp, token: str) -> dict | None:
    """Public invite-info lookup by plaintext token (#1177).

    Returns the display fields for the accept page: team_id, role, expires_at,
    inviter_email — or None for unknown/expired/consumed tokens. The token is
    matched via its lookup_hash (O(1) index, same as accept). Only display-safe
    fields are returned: never the lookup_hash or the invitee email.
    """
    from datetime import datetime  # noqa: F401, I001
    from tortoise.auth import lookup_hash as _lookup_hash

    rows = cp.query(
        "invitations",
        select=["id", "team_id", "role", "inviter_email", "expires_at",
                "status", "accepted_at"],
        filters=[("lookup_hash", "eq", _lookup_hash(token))],
    )
    if not rows:
        return None
    row = rows[0]
    # Consumed or revoked invites are not accept-able — treat as unknown.
    if row.get("status") not in (None, "pending") or row.get("accepted_at"):
        return None
    return {
        "team_id": row["team_id"],
        "role": row.get("role", "member"),
        "inviter_email": row.get("inviter_email"),
        "expires_at": row.get("expires_at"),
    }


def invitation_rescind(cp, invitation_id: str, team_id: str,
                       actor_user_id: str) -> dict:
    """Owner/admin rescind — set status='revoked' (soft delete).

    Scoped to the actor's team (an invite id from another team is a 404).
    Idempotent for already-revoked invites (mirrors the registry SDK); an
    ACCEPTED (used) invite cannot be rescinded — the membership already
    exists.
    """
    mem = membership_for_user_team(cp, actor_user_id, team_id)
    if not mem or mem["role"] not in ("owner", "admin"):
        raise InvitationError("Requires owner or admin role in team", status=403)

    rows = cp.query(
        "invitations",
        select=["id", "status", "team_id"],
        filters=[("id", "eq", invitation_id), ("team_id", "eq", team_id)],
    )
    if not rows:
        raise InvitationError("Invitation not found", status=404)
    inv = rows[0]
    if inv.get("status") == "accepted":
        raise InvitationError(
            "Invitation already accepted — cannot rescind", status=409)
    if inv.get("status") == "revoked":
        return {"revoked": True, "already": True,
                "invitation_id": invitation_id}
    # Status-conditional PATCH + re-read (code-review P2, PR #864): a
    # concurrent accept between our read and this PATCH must win — rescinding
    # a just-consumed invite would leave a membership with an invite that
    # says revoked. Only a row still 'pending' flips; otherwise 409.
    cp.query(
        "invitations",
        method="PATCH",
        filters=[("id", "eq", invitation_id), ("status", "eq", "pending")],
        json_body={"status": "revoked"},
    )
    check = cp.query(
        "invitations", select=["status"], filters=[("id", "eq", invitation_id)],
    )
    if not check or check[0].get("status") != "revoked":
        raise InvitationError(
            "Invitation no longer pending — cannot rescind", status=409)
    return {"revoked": True, "invitation_id": invitation_id}


def pending_invitations(cp, team_id: str) -> list[dict]:
    """Pending (unused) invites for a team — dashboard surface, oldest first.

    The actionable set: only status='pending' rows are returned (consumed /
    revoked invites are excluded; list_members shows the resulting
    memberships).
    """
    return cp.query(
        "invitations",
        select=["id", "team_id", "role", "invited_by", "email", "status",
                "expires_at", "created_at"],
        filters=[("team_id", "eq", team_id), ("status", "eq", "pending")],
        order="created_at",
    )


# ── Onboarding / email / GitHub connect (plan Task 6) ───────────────────────
#
# onboarding_state is jsonb in migration 0006 (real JSON object — no
# string-wrapping, unlike the FalkorDB registry path which stores a JSON
# string). github_token_enc is column-REVOKEd from anon/authenticated;
# service_role (this client) is the ONLY reader/writer in Supabase mode.


def team_onboarding_state(cp, team_id: str) -> dict | None:
    """Read ``teams.onboarding_state`` (jsonb) for a team.

    Returns the stored state merged over the hosted default shape
    (``hosted_api._ONBOARDING_DEFAULT_STATE`` — same shape the registry path
    auto-initializes to), so callers never see a partial dict for an existing
    row. None when the team row does not exist — the caller mirrors the
    registry ``MATCH``-no-op (a missing team reads as defaults without
    writing). Unknown stored keys are PRESERVED on the merge (mirrors the
    registry ``state.update(stored)`` semantics — dropping them would let a
    later write-back permanently erase keys the whitelist doesn't know,
    e.g. ``completed_at``/``github_index_job_id``; code-review P2, PR #861).
    """
    # Deferred import: hosted_api imports this module inside functions (#851
    # pattern), so a module-level import here would create a cycle. By call
    # time hosted_api is fully loaded.
    from tortoise.hosted_api import _ONBOARDING_DEFAULT_STATE
    rows = cp.query(
        "teams", select=["onboarding_state"], filters=[("id", "eq", team_id)]
    )
    if not rows:
        return None
    state = dict(_ONBOARDING_DEFAULT_STATE)
    stored = rows[0].get("onboarding_state")
    if isinstance(stored, dict):
        state.update(stored)  # preserve unknown keys (registry parity)
    return state


def update_onboarding_state(cp, team_id: str, state_dict: dict) -> None:
    """PATCH ``teams`` SET onboarding_state = state_dict (jsonb).

    PostgREST accepts the JSON object directly in the PATCH body (the
    ``query()`` json_body path sends it verbatim) — no string-wrapping. Raises
    on failure (fail-closed): a dropped onboarding write must surface as an
    error, never silently lose progress.
    """
    cp.query(
        "teams",
        method="PATCH",
        filters=[("id", "eq", team_id)],
        json_body={"onboarding_state": state_dict},
    )


def team_email(cp, team_id: str) -> str | None:
    """Read ``teams.email`` for a team (None when the row is missing)."""
    rows = cp.query("teams", select=["email"], filters=[("id", "eq", team_id)])
    return rows[0]["email"] if rows else None


def update_team_email(cp, team_id: str, email: str) -> None:
    """PATCH ``teams`` SET email (onboarding flow writes the signup email)."""
    cp.query(
        "teams",
        method="PATCH",
        filters=[("id", "eq", team_id)],
        json_body={"email": email},
    )


def github_credentials(cp, team_id: str) -> dict:
    """Read the encrypted GitHub token + org from ``teams``.

    github_token_enc is column-REVOKEd from anon/authenticated (migration
    0006) — this service-role seam is the ONLY read path for it in Supabase
    mode. Returns ``{"github_token_enc": ..., "github_org": ...}`` (both None
    when the team row is missing).
    """
    rows = cp.query(
        "teams",
        select=["github_token_enc", "github_org"],
        filters=[("id", "eq", team_id)],
    )
    if not rows:
        return {"github_token_enc": None, "github_org": None}
    return {
        "github_token_enc": rows[0].get("github_token_enc"),
        "github_org": rows[0].get("github_org"),
    }


def store_github_credentials(cp, team_id: str, *, token_enc: str, org: str) -> None:
    """PATCH ``teams`` SET github_token_enc + github_org (service role).

    Rotation (plan Task 6 "rotation documented"): every OAuth reconnect —
    ``github_callback`` running connect again — PATCHes a fresh encrypted
    token over the old one in place; the previous ciphertext is simply
    replaced, so rotation needs no separate endpoint or background job.
    """
    cp.query(
        "teams",
        method="PATCH",
        filters=[("id", "eq", team_id)],
        json_body={"github_token_enc": token_enc, "github_org": org},
    )


# ── Team deletion cascade (E2E-6-D, issue #302 security baseline) ──────────
#
# Two-phase deletion: soft delete (immediate access kill + grace stamp) then
# hard delete (post-grace purge). All soft phases are PATCH-based — rows stay
# for audit/forensics until the grace window elapses; the purge then DELETEs
# the control-plane rows. Immutable ``audit_events`` rows are preserved by
# design (no FK to teams — the delete trail survives the team).


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017


def soft_delete_team(cp, team_id: str, now: str | None = None,
                     grace_hours: float = 24.0) -> None:
    """Stamp ``teams.deleted_at`` + persist the grace window (#302).

    ``grace_hours`` is stored so the purge sweep and the idempotent replay
    honor the hard_delete_after the API promised at schedule time, even if
    TORTOISE_TEAM_DELETE_GRACE_HOURS changes before the sweep runs.
    Idempotent: re-stamping an already-deleted team is a no-op PATCH.
    """
    cp.query(
        "teams",
        method="PATCH",
        filters=[("id", "eq", team_id)],
        json_body={"deleted_at": now or _now_iso(), "grace_hours": grace_hours},
    )


def revoke_team_api_keys(cp, team_id: str, now: str | None = None) -> None:
    """Revoke every non-revoked ``api_keys`` row for the team.

    The key-plane kill switch: ``resolve_api_key`` treats ``revoked_at`` as
    authoritative (P1-2), so all ``tt_`` keys for the team fail closed the
    moment the delete is requested.
    """
    cp.query(
        "api_keys",
        method="PATCH",
        filters=[("team_id", "eq", team_id), ("revoked_at", "is", None)],
        json_body={"revoked_at": now or _now_iso()},
    )


def remove_team_memberships(cp, team_id: str, now: str | None = None) -> None:
    """Mark active ``team_memberships`` removed (session-plane kill switch).

    ``user_memberships``/``membership_for_user_team`` only match
    ``status='active'``, so JWT-session access (teams list, invites, owner
    checks) stops resolving immediately. ``now`` is accepted for signature
    symmetry but not written: team_memberships has no removed_at column
    (migration 0003/0009), and the owner-replay authz check distinguishes
    cascade-removal by role (owner can never be removed/demoted by any
    other path).
    """
    cp.query(
        "team_memberships",
        method="PATCH",
        filters=[("team_id", "eq", team_id), ("status", "eq", "active")],
        json_body={"status": "removed"},
    )


def revoke_team_invitations(cp, team_id: str, now: str | None = None) -> None:
    """Revoke pending ``invitations`` for the team (no redemption post-delete).

    ``invitation_accept`` rejects status != 'pending', so a pending invite
    can never mint a membership in a deleted team. invitations.status is
    NOT NULL DEFAULT 'pending' (migration 0008) — a single eq.pending
    PATCH covers every live row; the registry path additionally guards
    legacy NULL status (registry nodes have no NOT NULL constraint).
    ``now`` is accepted for signature symmetry but not written —
    invitations has no revoked_at column.
    """
    cp.query(
        "invitations",
        method="PATCH",
        filters=[("team_id", "eq", team_id), ("status", "eq", "pending")],
        json_body={"status": "revoked"},
    )


def purge_team_control_plane(cp, team_id: str) -> None:
    """Hard-delete all control-plane rows for a team (post-grace purge, #302).

    Deletes ``api_keys`` + ``team_memberships`` + ``invitations`` FIRST and
    the ``teams`` row LAST via the service-role seam: a partial failure
    leaves the teams row as the retry anchor (the sweep re-scans
    ``deleted_at <= cutoff`` and finds the team again) — mirroring
    ``_purge_registry_team``. Only called once the soft-delete grace
    window has elapsed. Raises on failure (fail-closed) — the purge sweep
    logs and skips, never crashes.
    """
    cp.query("api_keys", method="DELETE", filters=[("team_id", "eq", team_id)])
    cp.query("team_memberships", method="DELETE", filters=[("team_id", "eq", team_id)])
    cp.query("invitations", method="DELETE", filters=[("team_id", "eq", team_id)])
    cp.query("teams", method="DELETE", filters=[("id", "eq", team_id)])


# ── Task 8 writer inventory: keys + members + provisioning (#765) ────────────
#
# The remaining hosted writers flip here (plan Task 8): POST/GET/DELETE
# /v1/team/keys, /v1/agent/signup + /v1/register + POST /v1/teams (all via
# the atomic provision_team RPC, migration 0010), members DELETE/PATCH,
# member listing, reconcile, and the graph-metadata derivation for
# graph_list. Everything stays fail-closed: a query error raises RuntimeError
# and no writer ever falls back to the registry.


def provision_team(cp, **params: object) -> None:
    """Call the atomic provision_team SECURITY DEFINER RPC (migration 0010).

    One transaction: teams + team_memberships + api_keys (idempotent
    upserts; exactly one row each). Writer inventory (#765): agent_signup,
    /v1/register, POST /v1/teams and the onboarding sub-team create all
    route their Supabase writes through this RPC — never hand-rolled table
    writes (an atomic provision cannot leave a half-team behind).

    Exactly one of ``user_id`` / ``identity`` is required (the RPC enforces
    it too); key hashes are computed by the caller (the pepper lives in app
    code, never the DB — plan P1-1). Raises RuntimeError on failure
    (fail-closed): a failed provision surfaces as a 500, and the caller
    cleans up any data-plane graph it created first.
    """
    cp.rpc("provision_team", params)

    # #318 (multi-tenant pack isolation): post-RPC starter-pack activation.
    # The RPC transaction is Supabase-only; pack install-state lives in the
    # tenant graph (a different store), so activation rides AFTER the RPC as
    # an idempotent post-step (scoping: "idempotent post-step with retry-safe
    # semantics"). One hook here covers EVERY provision_team caller (/v1/register,
    # /v1/teams, agent signup, onboarding sub-team). Best-effort: failure never
    # blocks provisioning — the introspection surface self-heals on first read.
    # Embedded-aware _make_sdk (code-review conf 60, PR #1261): a bare
    # TortoiseSDK(namespace=...) with no db_path/URI raises when
    # TORTOISE_DB_URI is unset, and the exception is swallowed here →
    # activation silently skipped. _make_sdk mirrors the hosted_api hooks.
    team_id = params.get("p_team_id")
    if team_id:
        try:
            from tortoise.pack_state import ensure_tenant_packs  # noqa: I001
            from tortoise import hosted_api as _ha
            ensure_tenant_packs(_ha._make_sdk(namespace=team_id))
        except Exception:
            _logger.warning(
                "pack activation failed for team %s — self-heals on first read",
                team_id, exc_info=True)


# ── Agent signup tokens + keyless recovery (#1709, 20260814000001) ─────────
# Approach C: a server-issued 256-bit st_ token minted at first signup
# (hash-only at rest — SHA-256(PEPPER + token) via auth.lookup_hash, the
# pepper NEVER reaches SQL). Re-presenting the token is BOTH the dedupe
# check AND the keyless-recovery credential. All three RPCs are
# service_role-only SECURITY DEFINER (grant hygiene asserted in the PGlite
# suite) — an anon-executable resolve_signup_token would be a
# token-existence oracle; an anon-executable wrapper an unauthenticated mint.


def provision_team_with_token(cp, **params: object) -> None:
    """Signup mint: provision_team + one token row in ONE transaction.

    NEW-named wrapper (NOT CREATE OR REPLACE on provision_team — a trailing
    param would create a second OVERLOAD on PG16, see scope cycle-2 P1). The
    signup path calls this; every other provision_team caller (/v1/register,
    POST /v1/teams, onboarding) keeps the 15-arg RPC. ``p_signup_token_hash``
    is the caller-computed SHA-256(PEPPER + st_ token); a failed provision
    rolls back the token insert (no orphan token). Raises RuntimeError on
    failure (fail-closed → 500).
    """
    cp.rpc("provision_team_with_token", params)


def resolve_signup_token(cp, token_hash: str) -> str | None:
    """Resolve a signup-token hash → team_id; None = unknown/revoked.

    The caller (hosted_api) maps None to the UNIFORM 422 invalid_signup_token
    (same body for malformed/unknown/revoked/soft-deleted team — no existence
    signal). PostgREST does NOT echo SECURITY DEFINER RPC results with
    return=minimal (repo precedent: metering_increment) — the resolve RPC's
    scalar result is a VOLATILE return, so in production the echo is ALWAYS
    empty (the old echo-parsing here returned None for a VALID token → the
    token path 422'd in prod while passing against the fake's bare-string
    rpc). Read back the authoritative token row instead (the RPC already
    committed; a read-back failure raises fail-closed → 500, never a false
    422). A control-plane failure raises RuntimeError (fail-closed → 500,
    never 422).
    """
    try:
        cp.rpc("resolve_signup_token", {"p_token_hash": token_hash})
    except RuntimeError as e:
        _logger.warning(
            "resolve_signup_token RPC failed (%s) — fail-closed", e)
        raise
    rows = cp.query(
        "agent_signup_tokens",
        select=["team_id"],
        filters=[("token_hash", "eq", token_hash),
                 ("revoked_at", "is", None)],
    )
    return rows[0].get("team_id") if rows else None


class SignupTokenRecoveryError(Exception):
    """Semantic rejection of a recovery mint (token invalid/revoked, team
    deleted). Carries the HTTP status the caller should emit: uniform 422
    invalid_signup_token (indistinguishable from never-existed) — the RPC
    itself fails CLOSED on the FOR UPDATE zero-row lock and on soft-deleted
    teams (never mints on a bad lock)."""

    def __init__(self, message: str, status: int = 422, code: str = ""):
        super().__init__(message)
        self.status = status
        self.code = code


_RECOVER_ERROR_CODES = {
    "token not found or revoked": (422, "invalid_signup_token"),
    "team deleted": (422, "invalid_signup_token"),
}


def recover_team_key(cp, *, token_hash: str, team_id: str,
                     lookup_hash: str,
                     key_prefix: str, max_api_keys: int) -> str:
    """Keyless recovery: mint a NEW key on the token's team (one RPC tx).

    The RPC (20260814000001) SELECTs the token row FOR UPDATE (serializes
    concurrent recoveries so the non-bootstrap cap cannot overshoot),
    rejects soft-deleted teams, inserts the new api_keys row (created_via=
    'recovery', created_by='st_'||left(token_hash,12) — token-attributable,
    derived inside the RPC, never caller-supplied) and ONLY THEN, when a new
    row was actually inserted, revokes the OLDEST non-bootstrap key at the
    cap (deterministic, #750.10 semantics; a no-op retry must never revoke a
    live key). The plaintext api_key / key_hash are caller-held (they never
    reach SQL — the RPC binds auth on lookup_hash alone; review P2.8).

    PostgREST does NOT echo SECURITY DEFINER RPC results with return=minimal
    — read back the minted api_keys row (the RPC already committed; a missing
    row means the mint did NOT commit → fail-closed RuntimeError → 500,
    never a fabricated key). Raises SignupTokenRecoveryError (→ uniform 422)
    on semantic rejections; RuntimeError (fail-closed → 500) on control-plane
    failures. Returns team_id.
    """
    try:
        cp.rpc("recover_team_key", {
            "p_token_hash": token_hash,
            "p_team_id": team_id,
            "p_lookup_hash": lookup_hash,
            "p_key_prefix": key_prefix,
            "p_max_api_keys": max_api_keys,
        })
    except RuntimeError as e:
        msg = str(e)
        for code, (status, detail_code) in _RECOVER_ERROR_CODES.items():
            if code in msg:
                raise SignupTokenRecoveryError(
                    msg, status=status, code=detail_code) from e
        _logger.warning("recover_team_key RPC failed (%s) — fail-closed", e)
        raise
    rows = cp.query(
        "api_keys",
        select=["team_id"],
        filters=[("lookup_hash", "eq", lookup_hash),
                 ("team_id", "eq", team_id)],
    )
    if not rows:
        raise RuntimeError("recover_team_key returned no team_id")
    team_id = rows[0].get("team_id")
    if not isinstance(team_id, str) or not team_id:
        raise RuntimeError("recover_team_key returned no team_id")
    return team_id


# ── Claim path (#1082, PR1 — 20260813000004) ────────────────────────────────
#
# claim_membership attaches a provider-verified Supabase user to the team
# resolved from an api_keys.lookup_hash (authoritative key→team binding,
# unique index 0007, revocation-aware). The RPC NEVER accepts team_id or
# identity from the caller (solution-verify P1): a client-supplied
# team_id/identity would let any key + any session JWT claim ANY team.


class ClaimError(Exception):
    """Semantic claim rejection (already_claimed / email_in_use / invalid key).

    Carries an HTTP status so hosted_api can translate directly (409 for
    already_claimed/email_in_use, 404/403 for key problems). Deliberately
    NOT a RuntimeError: these are expected client outcomes, not control-
    plane failures — the fail-closed contract (RPC errors → RuntimeError
    → 500) is untouched.
    """

    def __init__(self, message: str, status: int = 409, code: str = ""):
        super().__init__(message)
        self.status = status
        self.code = code


# RPC RAISE messages → (HTTP status, user-facing detail). The RPC codes are
# stable (SQL test suite 20260813000004 asserts them); PostgREST wraps them
# in a 400 with the message embedded.
_CLAIM_ERROR_CODES = {
    "key_required": (400, "api_key is required to claim a team"),
    "user_required": (400, "Claim requires a verified user"),
    "key_not_found": (404, "Invalid API key — no team resolves from this key"),
    "key_not_claimable": (403, "This is a session key and cannot claim a team"),
    "key_expired": (403, "This key has expired"),
    "already_claimed": (409, "Team has already been claimed by another user"),
    "email_in_use": (409, "This email is already in use by another team"),
}


def claim_membership(cp, *, lookup_hash: str, user_id: str, email: str) -> dict:
    """Call the claim_membership SECURITY DEFINER RPC (20260813000004).

    Attaches the verified Supabase user (user_id) + verified email to the
    team resolved from ``lookup_hash``'s api_keys row (authoritative
    key→team binding). Same key, same team, memories intact. Idempotent:
    an owner row already linked to ``user_id`` is a noop success.

    The caller (hosted_api) MUST have already verified the session JWT + the
    provider-verified-email invariant — the RPC is service-role and holds
    no auth.uid() (P2-FIX-J). Raises ClaimError on semantic rejections;
    RuntimeError (fail-closed) on control-plane failures.
    """
    try:
        cp.rpc("claim_membership", {
            "p_lookup_hash": lookup_hash,
            "p_user_id": user_id,
            "p_email": email,
        })
    except RuntimeError as e:
        msg = str(e)
        for code, (status, detail) in _CLAIM_ERROR_CODES.items():
            if code in msg:
                raise ClaimError(detail, status=status, code=code) from e
        raise
    # PostgREST return=minimal drops the jsonb body — success is 2xx.
    return {"status": "claimed"}


def is_anon_team(cp, team_id: str) -> bool:
    """True when *team_id* has an ACTIVE OWNER membership with user_id NULL.

    The shared anon predicate for the claim path (#1082 PR1: only an
    unclaimed team may be claimed) AND the derived-tier ceiling (#1082 PR2:
    unclaimed teams run the reduced ``anon`` tier). Deliberately NOT the
    ``teams.email IS NULL`` proxy (reg- teams set email at mint; legacy
    real-user teams may have NULL email — both would misclassify).
    """
    rows = cp.query(
        "team_memberships",
        select=["id"],
        filters=[("team_id", "eq", team_id), ("role", "eq", "owner"),
                 ("status", "eq", "active"), ("user_id", "is", None)],
    )
    return bool(rows)


def team_by_email(cp, email: str) -> dict | None:
    """Team row for an email (register idempotency — 409 already_registered)."""
    rows = cp.query("teams", select=["id"], filters=[("email", "eq", email)])
    return rows[0] if rows else None


def team_by_name(cp, name: str) -> dict | None:
    """Team row for a name (create_team duplicate-name 409)."""
    rows = cp.query("teams", select=["id"], filters=[("name", "eq", name)])
    return rows[0] if rows else None


def team_api_keys(cp, team_id: str) -> list[dict]:
    """ALL api_keys rows for a team (revoked included — the dashboard lists
    them with their revoked_at; registry parity), newest first.
    #1708 D7: additive created_via/expires_at so the dashboard can classify
    ephemeral session keys from API data instead of a prefix heuristic."""
    rows = cp.query(
        "api_keys",
        select=["id", "key_prefix", "created_at", "last_used_at",
                "revoked_at", "enabled", "name", "created_via", "expires_at"],
        filters=[("team_id", "eq", team_id)],
    )
    rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return rows


def api_key_by_id(cp, key_id: str) -> dict | None:
    """One api_keys row by id (revoke lookup — team-scoping + already-revoked)."""
    rows = cp.query(
        "api_keys",
        select=["team_id", "revoked_at", "created_via", "enabled", "name"],
        filters=[("id", "eq", key_id)],
    )
    return rows[0] if rows else None


def membership_count_since(cp, *, cutoff: str, user_id: str | None = None,
                           identity: str | None = None,
                           role: str | None = None) -> int:
    """Membership rows created after ``cutoff`` for a user or an anon
    identity — the Supabase twin of the registry rate-limit counts
    (agent-signup per-identity 3/hour, team-create per-user 3/hour).
    NULL semantics: a row without the anchor column never matches.

    ``role`` narrows to owner rows when given (team-create parity: the
    registry counts ``(m:Membership {user_id:$uid, role:'owner'})`` — a
    user who accepted invites into other teams must NOT be rate-limited
    from creating their own; review P2, PR #874).
    """
    filters: list[tuple[str, str, object]] = [("created_at", "gt", cutoff)]
    if user_id is not None:
        filters.append(("user_id", "eq", user_id))
    if identity is not None:
        filters.append(("identity", "eq", identity))
    if role is not None:
        filters.append(("role", "eq", role))
    return len(cp.query("team_memberships", select=["id"], filters=filters))


def team_members(cp, team_id: str) -> list[dict]:
    """Member listing (E8a): active + invited memberships for a team.

    Mirrors the registry ``list_members`` predicate (status active OR
    invited). Identity rows (NULL user_id — anon agents) surface their
    ``identity`` as ``user_id`` so the members API can round-trip
    DELETE/PATCH /members/{user_id} against them (the registry stores the
    anon anchor in user_id directly; 0009 keeps it in ``identity``).
    """
    rows = cp.query(
        "team_memberships",
        select=["user_id", "identity", "role", "status", "invited_email"],
        filters=[("team_id", "eq", team_id)],
    )
    out = []
    for r in rows:
        if r.get("status") not in ("active", "invited"):
            continue
        out.append({
            "user_id": r.get("user_id") or r.get("identity"),
            "role": r.get("role"),
            "status": r.get("status"),
            "email": r.get("invited_email") or "",
        })
    return out


def membership_role(cp, team_id: str, user_id: str) -> str | None:
    """Role of a member matched by user_id OR identity (agents), any status
    (mirrors the registry remove/role-change lookup which does not filter
    status). None when no row matches."""
    for col in ("user_id", "identity"):
        rows = cp.query(
            "team_memberships",
            select=["role"],
            filters=[("team_id", "eq", team_id), (col, "eq", user_id)],
        )
        if rows:
            return rows[0]["role"]
    return None


def set_membership(cp, team_id: str, user_id: str, **updates: object) -> None:
    """PATCH a membership row matched by user_id OR identity (remove =
    status 'removed'; role change = role). Raises on failure (fail-closed)."""
    for col in ("user_id", "identity"):
        cp.query(
            "team_memberships",
            method="PATCH",
            filters=[("team_id", "eq", team_id), (col, "eq", user_id)],
            json_body=dict(updates),
        )


def expired_bootstrap_keys(cp, now: str) -> list[dict]:
    """Expired, non-revoked bootstrap api_keys (reconcile sweep — the
    ``created_via='bootstrap' AND revoked_at IS NULL AND expires_at < now``
    predicate, D3 #618 contract). NULL expires_at never matches (SQL
    semantics)."""
    return cp.query(
        "api_keys",
        select=["id"],
        filters=[("created_via", "eq", "bootstrap"),
                 ("revoked_at", "is", None),
                 ("expires_at", "lt", now)],
    )


def graph_metadata(cp, team_id: str) -> list[dict]:
    """Graph-metadata derivation for Supabase mode (reader inventory:
    graph_list). The plan data model (0006-0009) has no graphs table —
    team→graph 1:N metadata is not persisted; the DEFAULT graph is derived
    from ``teams.graph_name`` (the canonical graph name — 0006 note:
    sdk.team_create names graphs team_{name}, hosted provisioning
    team_{team_id}). Returns the registry-shaped list
    [{graph_id, name, kind, namespace}] with a stable deterministic
    graph_id (``default``) — the dashboard graph switcher key. Custom
    graphs are NOT listed in Supabase mode (their namespaces are minted
    lazily and no table records them); create_graph still returns a
    namespace for direct use.
    """
    rows = cp.query(
        "teams", select=["id", "graph_name"], filters=[("id", "eq", team_id)]
    )
    if not rows or not rows[0].get("graph_name"):
        return []
    return [{
        "graph_id": "default",
        "name": "default",
        "kind": "default",
        "namespace": rows[0]["graph_name"],
    }]


# ── Stripe webhook billing state (plan Task 10 — #771 review P1) ───────────
#
# The Stripe webhook (_webhook_apply_event) writes billing state on the
# control plane. Registry mode: Team node SETs. Supabase mode: PATCH the
# teams row (0012 added subscription_status / customer_email / grace_until /
# current_period_end next to 0006's tier / stripe_customer_id /
# subscription_id). Without this branch the webhook would silently lose
# billing state post-registry-delete — or recreate the registry graph via
# an unguarded write (FalkorDB GRAPH.QUERY auto-creates missing graphs).


def team_id_for_stripe_customer(cp, customer_id: str) -> str | None:
    """Team id whose teams.stripe_customer_id matches (subscription events).

    Registry twin: MATCH (t:Team {stripe_customer_id:$cid}) RETURN t.id.
    None when no team is bound to the customer (webhook acks 200 "no team
    binding" — Stripe stops retrying).
    """
    rows = cp.query(
        "teams", select=["id"], filters=[("stripe_customer_id", "eq", customer_id)]
    )
    return rows[0]["id"] if rows else None


def update_team_billing(cp, team_id: str, updates: dict) -> None:
    """PATCH billing state on the teams row (webhook SET twin).

    ``updates`` is a subset of {tier, stripe_customer_id, subscription_id,
    subscription_status, customer_email, grace_until, current_period_end}
    — only columns that exist on teams (0006 + 0012) are written. Raises on
    failure (fail-closed): a dropped billing write must surface, not
    silently lose an upgrade/downgrade/cancel.
    """
    allowed = {"tier", "stripe_customer_id", "subscription_id",
               "subscription_status", "customer_email", "grace_until",
               "current_period_end",
               # quota columns (0006) — apply_limits' Supabase branch writes
               # them; dropping them here would silently keep upgrades at
               # free-tier caps (re-review P1, PR #878)
               "max_users", "max_graphs", "ops_allowance", "graph_size_cap"}
    body = {k: v for k, v in updates.items() if k in allowed}
    if not body:
        return
    cp.query(
        "teams",
        method="PATCH",
        filters=[("id", "eq", team_id)],
        json_body=body,
    )


def webhook_event_marker(cp, event_id: str, etype: str) -> bool:
    """First-seen marker for a Stripe webhook event (Supabase twin of the
    registry WebhookEvent node — #771 re-review P1).

    Returns True when the event is NEW (marker created now), False when it
    was already seen. The apply itself is idempotent (replays converge);
    only side-effects (notify/audit/analytics) are gated on first-seen.
    """
    rows = cp.query(
        "webhook_events",
        select=["event_id"],
        filters=[("event_id", "eq", event_id)],
    )
    if rows:
        return False
    from datetime import datetime, timezone
    cp.query(
        "webhook_events",
        method="POST",
        json_body={
            "event_id": event_id,
            "first_seen": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
            "type": etype,
        },
    )
    return True


def team_tier(cp, team_id: str) -> str | None:
    """Current tier from the teams row (webhook analytics twin of the
    registry tier read). #1082 PR2: derives the anon ceiling — an
    unclaimed zero-email team resolves to ``anon`` until claimed."""
    rows = cp.query("teams", select=["tier"], filters=[("id", "eq", team_id)])
    if not rows:
        return None
    from tortoise.quota import derived_tier
    return derived_tier({"tier": rows[0].get("tier"), "id": team_id})


# ── Write-op metering (post-#669 flip fix — #669) ───────────────────────────
#
# Metering previously stored MeteringRecord nodes in the registry graph —
# post-flip that RECREATES the deleted registry on every /v1/team call and
# every write-op increment. Supabase mode stores rows in metering_records
# (0014): PK (team_id, period), service-role RLS.


def metering_get(cp, team_id: str, period: str) -> int:
    """Write-ops used by a team in a billing period (0 when absent)."""
    rows = cp.query(
        "metering_records",
        select=["write_ops"],
        filters=[("team_id", "eq", team_id), ("period", "eq", period)],
    )
    return int(rows[0]["write_ops"]) if rows else 0


def metering_increment(cp, team_id: str, period: str, n: int = 1,
                      nodes_written: int = 0) -> int:
    """Increment the team's write-op counter for the period; returns the new
    count. ATOMIC (review P2, PR #911): delegates to the metering_increment
    SQL RPC (0014/0017) — write_ops = write_ops + n under Postgres row locking —
    so concurrent increments can never undercount (a GET-then-PATCH would
    lose updates). Best-effort by contract (metering failures never block a
    write): the caller swallows exceptions.

    nodes_written: net-new non-episodic nodes for the period (the value-first
    commit cost driver, epic #909 §4.4/W-4 — 0 on hold commits). The 0017
    RPC increments both columns atomically under the same row lock.

    #925: the read-back is the only best-effort step. The RPC call itself
    still raises when it fails — though if the response is lost the write
    may still have committed, so callers treat a raise as best-effort (as
    before). If the RPC succeeded but the read-back fails (network blip),
    the stored counter is already correct server-side — only the current
    total is unknown — so return the known delta *n* instead of raising
    (the returned total is then approximate; a raising read-back would
    make record_write_ops return None and a caller retry would
    double-count).
    """
    cp.rpc(
        "metering_increment",
        {"p_team_id": team_id, "p_period": period, "p_n": n,
         "p_nodes_written": nodes_written},
    )
    # PostgREST does not echo SECURITY DEFINER RPC results with
    # return=minimal — read back the atomic new value. The RPC above already
    # committed; if this read-back fails, fall back to the known delta (#925).
    try:
        return metering_get(cp, team_id, period)
    except Exception:
        _logger.warning(
            "metering read-back failed after committed increment "
            "(non-fatal): team=%s period=%s n=%s", team_id, period, n,
        )
        return n
