"""Per-team quota enforcement shared by REST and MCP (#329).

Design: limits are resolved ONCE by the authenticated caller
(``hosted_api.get_current_team`` / MCP ``TeamResolutionMiddleware``) via
``resolve_team_limits`` and passed to ``enforce_team_limit`` — never re-fetched
per write. Counting is fail-closed: any counting exception raises
``QuotaCheckError`` (server error), never a silent pass.

Import topology: stdlib-only at module level; ``tortoise.sdk`` imported
function-level inside the helpers to avoid any cycle (hosted_api → mcp_server
→ mcp_auth → sdk is the canonical direction; quota is a leaf consumer).

No team context (stdio/operator) → ``enforce_team_limit(None, ...)`` returns
cleanly (skip) — mirrors REST ``_check_team_limit``'s ``if not team_id: return``.
Batch caps are unconditional in both modes.
"""
from __future__ import annotations

import os
import tempfile


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

# ── Default limits (match REST today: team.get("max_points") or 1000) ──────
DEFAULT_MAX_POINTS = 1000
DEFAULT_MAX_API_KEYS = 20
DEFAULT_MAX_SESSIONS = 1000

_RESOURCE_LIMIT_KEYS = {
    "points": "max_points",
    "api_keys": "max_api_keys",
    "sessions": "max_sessions",
}


class QuotaExceededError(Exception):
    """Team is at/over its resource limit — the write must be rejected (402)."""


class QuotaCheckError(Exception):
    """Quota counting/config failed — fail closed (500/503), never pass."""


def resolve_team_limits(team_id: str) -> dict:
    """Resolve a team's limits from the registry Team node.

    Missing Team node → QuotaCheckError (fail-closed; the auth layer should
    guarantee key→team mapping). Missing attributes → defaults
    (1000/20/1000 — matching today's effective behavior).
    """
    if not team_id:
        raise QuotaCheckError("resolve_team_limits requires a team_id")
    reg = _make_sdk(namespace="registry")
    rows = reg._get_registry().query(
        "MATCH (t:Team {id:$id}) "
        "RETURN t.tier, t.max_points, t.max_api_keys, t.max_sessions",
        params={"id": team_id},
    ).result_set
    if not rows:
        raise QuotaCheckError(f"Team {team_id!r} not found in registry")
    tier, mp, mak, ms = rows[0]
    return {
        "team_id": team_id,
        "tier": tier or "free",
        "max_points": int(mp) if mp is not None else DEFAULT_MAX_POINTS,
        "max_api_keys": int(mak) if mak is not None else DEFAULT_MAX_API_KEYS,
        "max_sessions": int(ms) if ms is not None else DEFAULT_MAX_SESSIONS,
    }


def count_team_usage(team_id: str, resource: str, sdk=None) -> int:
    """Count current usage for a resource. Raises QuotaCheckError on failure.

    Public so callers needing the raw count (e.g. extraction-aware estimates)
    can use it without duplicating the fail-closed handling.
    """
    return _count_resource(team_id, resource, sdk=sdk)


def _count_resource(team_id: str, resource: str, sdk=None) -> int:
    """Count current usage for a resource. Raises QuotaCheckError on failure."""
    try:
        if resource == "api_keys":
            reg = (sdk if sdk is not None and getattr(sdk, "_namespace", None) == "registry"
                   else _make_sdk(namespace="registry"))
            rows = reg._get_registry().query(
                "MATCH (k:APIKey {team_id: $tid}) WHERE k.revoked_at IS NULL RETURN count(k)",
                params={"tid": team_id},
            ).result_set
            return int(rows[0][0])
        else:
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
        raise QuotaCheckError(f"quota count failed for {resource}: {redact_error(e)}") from e


def enforce_team_limit(limits: dict | None, resource: str, sdk=None) -> None:
    """Reject a write when the team is at/over its resource limit.

    Args:
        limits: resolved team limits dict (from resolve_team_limits or the
            authenticated caller). None → skip (stdio/operator, no team).
        resource: "points" | "api_keys" | "sessions".
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
        limit = DEFAULT_MAX_POINTS if resource == "points" else (
            DEFAULT_MAX_API_KEYS if resource == "api_keys" else DEFAULT_MAX_SESSIONS)
    count = _count_resource(team_id, resource, sdk=sdk)
    if count >= limit:
        raise QuotaExceededError(
            f"Team {resource} limit reached ({limit}). Upgrade your plan to increase it."
        )
