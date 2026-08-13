"""TORT-MCP-001: MCP server wrapping TortoiseSDK. Stdio transport, ~10 tools."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
import time as _time
from pathlib import Path
from typing import Any, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import (AuthorizationError, FastMCPError, ToolError,
                                ValidationError as FastMCPValidationError)
from mcp.types import ToolAnnotations
from pydantic import ValidationError as PydanticValidationError
from tortoise.auth import is_dev_mode as _is_dev_mode
from tortoise.config import is_db_uri as _is_db_uri
from tortoise.sdk import TortoiseSDK, INGEST_GRANULARITIES, INGEST_PROMOTION_POLICIES
from tortoise import monitoring
from tortoise.mcp_auth import (_current_team_id, _current_team_limits,
                               _transport_mode, _get_team_sdk,
                               HTTP_ALLOWED, ERR_EXCLUDED, SELFHOST_TEAM_ID)

_log = logging.getLogger(__name__)

def _load_dotenv(path: str | None = None) -> None:
    """Tiny .env loader — repo-root .env, KEY=VALUE lines, no new deps.

    Only sets environment keys that are empty/unset, so an explicit
    TORTOISE_DB_URI in the process env always wins. Mirrors the hosted
    entrypoint philosophy: the DB target must be explicit, never accidental.
    """
    if path is None:
        path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", ".env"
        )
    if not os.path.exists(path):
        return
    try:
        for raw in Path(path).read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            # python-dotenv semantics: quoted values are literal (no inline
            # comment stripping); unquoted values strip inline comments
            # (whitespace + '#'). A bare '#' in an unquoted value is preserved.
            value = value.strip()
            if value[:1] in ('"', "'") and value[-1:] == value[:1]:
                value = value[1:-1]
            else:
                value = value.split(" #", 1)[0].strip()
            # Only fill keys that are absent — never override an explicitly
            # set (even empty) environment variable.
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError as e:
        _log.debug("Could not read .env (%s): %s — continuing without it", path, e)


if "pytest" not in sys.modules:
    _load_dotenv()  # resolve TORTOISE_DB_URI from repo-root .env (skipped under pytest)


# ── Safety annotations ───────────────────────────────────────────
# readOnlyHint=true: agent auto-approves, no confirmation needed
# destructiveHint=true: agent MUST get human confirmation
# idempotentHint=true: repeated calls have no extra side effects

mcp = FastMCP("tortoise")

# ── MCP tool-call telemetry (#889) ─────────────────────────────────
# One structured analytics event per MCP tool call — friction evidence for the
# surface-design epic (#888). Installed at mcp.call_tool, the single dispatch
# point every transport (stdio, streamable HTTP, programmatic) funnels
# through, so no per-tool instrumentation is needed. Transport-level auth
# (TeamResolutionMiddleware) runs BEFORE this point in HTTP mode: latency
# therefore measures tool execution only, and authenticated requests already
# carry the team_id ContextVar (empty string for unauthenticated paths).
# Fail-safe by construction: every emission path is try/except'd and
# fire-and-forget, so a telemetry failure can never break a tool call.


def _validation_error_kind(exc: PydanticValidationError) -> str:
    """error_kind for a pydantic validation failure: '<error_type>:<field>'.

    e.g. "missing:query" (required field absent) or "string_type:message"
    (wrong type). The #888 root-cause diagnostic (COUNT vs NAMING vs
    DESCRIPTIONS vs STEERING) needs bad calls distinguishable from execution
    failures, and the offending field is the steering signal.
    """
    try:
        errors = exc.errors()
    except Exception:
        return type(exc).__name__
    if not errors:
        return "validation"
    first = errors[0]
    err_type = str(first.get("type") or "validation")
    loc = first.get("loc") or ()
    field = ".".join(str(p) for p in loc)
    return f"{err_type}:{field}" if field else err_type


def _classify_mcp_call_error(exc: BaseException) -> tuple[str, str | None]:
    """Map a dispatch exception to (status, error_kind).

    validation_error → pydantic argument-validation failures (raised raw or
        wrapped by fastmcp's ValidationError, whose __cause__ carries the
        pydantic errors); error_kind = '<error_type>:<field>'.
    auth_error → AuthorizationError.
    exec_error → everything else; error_kind = the exception class name,
        unwrapping fastmcp's ToolError/FastMCPError wrapper to the underlying
        cause so the class stays diagnostic (RuntimeError, not ToolError).
    """
    if isinstance(exc, PydanticValidationError):
        return "validation_error", _validation_error_kind(exc)
    if isinstance(exc, FastMCPValidationError):
        cause = exc.__cause__
        if isinstance(cause, PydanticValidationError):
            return "validation_error", _validation_error_kind(cause)
        return "validation_error", type(exc).__name__
    if isinstance(exc, AuthorizationError):
        return "auth_error", type(exc).__name__
    kind_exc = exc
    for _ in range(5):  # bounded unwrap: ToolError → cause (RuntimeError etc.)
        if (isinstance(kind_exc, (ToolError, FastMCPError))
                and kind_exc.__cause__ is not None
                and kind_exc.__cause__ is not kind_exc):
            kind_exc = kind_exc.__cause__
        else:
            break
    return "exec_error", type(kind_exc).__name__


# In-flight telemetry writes (executor futures) — kept so verification/tests
# can await them and so GC never collects a pending future mid-write.
_pending_telemetry: set = set()


def _emit_mcp_tool_call_telemetry(team_id: str, tool_name: str, status: str,
                                  latency_ms: int, error_kind: str | None) -> None:
    """Fire-and-forget, fail-safe analytics write. Never raises, never blocks.

    The Supabase write (sync httpx POST, up to 5s timeout in
    _track_analytics_event) runs OFF the tool-call hot path: on the default
    executor when an event loop is running (all server transports), else on a
    daemon thread. Any failure is logged and swallowed — telemetry must never
    break a tool call.
    """
    props = {"tool_name": tool_name, "status": status,
             "latency_ms": latency_ms, "error_kind": error_kind}

    def _write() -> None:
        try:
            from tortoise.hosted_api import _track_analytics_event
            _track_analytics_event(team_id, "mcp_tool_call", props)
        except Exception:
            _log.debug("mcp_tool_call telemetry write failed", exc_info=True)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None and not loop.is_closed():
        try:
            fut = loop.run_in_executor(None, _write)
            _pending_telemetry.add(fut)
            fut.add_done_callback(_pending_telemetry.discard)
            return
        except Exception:
            _log.debug("mcp_tool_call telemetry schedule failed", exc_info=True)
            return
    try:
        threading.Thread(target=_write, daemon=True).start()
    except Exception:
        _log.debug("mcp_tool_call telemetry thread start failed", exc_info=True)


async def _flush_mcp_telemetry() -> None:
    """Await all in-flight telemetry writes (verification / tests)."""
    pending = list(_pending_telemetry)
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


# Captured before wrapping — the middleware chain re-dispatches
# call_tool(run_middleware=False) internally; the wrapper passes those
# through untouched so exactly ONE event is emitted per client tool call.
_original_call_tool = mcp.call_tool


async def _wrapped_call_tool(name: str, arguments: dict[str, Any] | None = None, *,
                             version=None, run_middleware: bool = True,
                             task_meta=None):
    """Telemetry-instrumented single dispatch point (installed as mcp.call_tool).

    Emits one mcp_tool_call analytics event per client tool call, with
    latency measured around the tool execution only (transport auth runs
    before this point and is excluded). Background-task dispatches
    (task_meta) are measured at scheduling granularity — our tools never use
    them, and the #888 evidence window runs normal synchronous clients.
    """
    if not run_middleware:
        return await _original_call_tool(name, arguments, version=version,
                                         run_middleware=False, task_meta=task_meta)
    team_id = _current_team_id.get() or ""
    maybe_record_mcp_read(name, team_id, _current_team_limits.get())
    status, error_kind = "ok", None
    t0 = _time.perf_counter()
    try:
        result = await _original_call_tool(name, arguments, version=version,
                                           run_middleware=True, task_meta=task_meta)
        # The stdio auth gate (#236) returns an error dict instead of raising
        # (TORTOISE_API_KEY set → every call is rejected). Classify it so
        # unauthenticated stdio calls don't masquerade as ok.
        payload = getattr(result, "structured_content", result)
        if (isinstance(payload, dict)
                and isinstance(payload.get("error"), str)
                and payload["error"].startswith("Authentication required")):
            status, error_kind = "auth_error", "stdio_auth_gate"
        return result
    except Exception as exc:
        status, error_kind = _classify_mcp_call_error(exc)
        raise
    finally:
        latency_ms = int((_time.perf_counter() - t0) * 1000)
        try:
            _emit_mcp_tool_call_telemetry(team_id, name, status, latency_ms,
                                          error_kind)
        except Exception:
            # A telemetry bug must never mask or break the tool call itself.
            _log.debug("mcp_tool_call telemetry emit failed", exc_info=True)


mcp.call_tool = _wrapped_call_tool  # install at the single dispatch point

# ── Lazy SDK initialization (#451) ─────────────────────────────────
# sdk is None at import time — _get_sdk() lazily resolves and connects
# on first call. Prevents import-time network I/O (3x retry + sys.exit)
# in environments without a live FalkorDB server.
_sdk = None
sdk = None  # module-level override point (test swap pattern: mcp_mod.sdk = test_sdk)


def _get_sdk():
    """Lazily resolve TORTOISE_DB_URI, connect, and return TortoiseSDK.

    Cached after first successful call. URI branches + 3x Docker retry
    + sys.exit(1) on exhaustion are preserved exactly — but deferred
    from import time to first tool call (or first call to main()).
    The module-level ``sdk`` attribute acts as an override (set by
    test_enumeration_surfaces.py swap pattern) — when non-None it is
    returned directly, bypassing lazy init.

    Error surface: exceptions here (connection failure, sys.exit on retry
    exhaustion) propagate BEFORE _safe() wrapping in tool bodies (call
    arguments are evaluated first). In normal operation main() calls
    _get_sdk() before mcp.run(), so failures surface at server startup —
    equivalent to the pre-#451 import-time behavior. Only callers that
    invoke mcp.run() directly without main() see an unwrapped error.

    Reset semantics: restoring ``sdk = None`` after a test swap falls
    through to the CACHED _sdk instance — it does not re-connect. Set
    both ``sdk`` and ``_sdk`` to None to force re-initialization.
    """
    global _sdk
    # Module-level sdk override (test swap pattern) takes priority
    if sdk is not None:
        return sdk
    if _sdk is not None:
        return _sdk

    _db_uri = os.environ.get("TORTOISE_DB_URI", "")
    if _db_uri.startswith(("docker://", "redis://", "rediss://")):
        from tortoise.projection import FalkorProjection
        import time as _time
        # Retry Docker connection 3x with backoff; exit on exhaustion (#25 P3a, #32).
        # _sdk is cached ONLY on success — assigning before the retry loop left a
        # poisoned docker-URI SDK cached when the connection failed and a caller
        # caught the SystemExit, breaking every later tool call (localhost:6379
        # connection refused across the whole suite, #493).
        for attempt in range(3):
            try:
                # Connect FIRST (FalkorProjection.from_uri probes eagerly), then
                # commit to the global — TortoiseSDK() is lazy and never
                # connects, so assigning it before from_uri left a poisoned
                # docker-URI SDK cached when the connection failed and a caller
                # caught the SystemExit, breaking every later tool call
                # (localhost:6379 connection refused across the whole suite, #493).
                _proj = FalkorProjection.from_uri(_db_uri)
                if attempt > 0:
                    _log.warning("Docker connection succeeded on attempt %d", attempt + 1)
                _sdk = TortoiseSDK()
                _sdk._proj = _proj
                break
            except Exception as e:
                if attempt < 2:
                    _log.warning("Docker connection attempt %d failed: %s — retrying in 2s", attempt + 1, e)
                    _time.sleep(2)
                else:
                    _log.error("Docker connection failed after 3 attempts. Set TORTOISE_DB_URI or ensure FalkorDB is running.")
                    sys.exit(1)
    elif _db_uri:
        # File path — use Lite mode (backward compat: bare non-docker URI).
        # resolve_db_path() rejects relative paths + applies canonical default.
        from tortoise.config import resolve_db_path as _resolve_db_path
        _sdk = TortoiseSDK(db_path=_resolve_db_path(_db_uri))
    else:
        # No URI: default to canonical embedded path via resolve_db_path()
        from tortoise.config import resolve_db_path as _resolve_db_path
        _sdk = TortoiseSDK(db_path=_resolve_db_path())
    return _sdk

# Announce auth mode at startup
if _is_dev_mode():
    _log.warning("TORTOISE_API_KEY not set — running in dev mode (no auth)")


# #329: node/edge-creating MCP write tools that MUST be quota-gated. Completeness
# is enforced by an introspective test (tests/test_mcp_http.py) that scans every
# HTTP_ALLOWED tool body for node/edge-creating SDK calls and asserts membership.
# New node/edge-creating tools MUST be added here.
_QUOTA_GATED: frozenset[str] = frozenset({
    "tortoise_create_point", "tortoise_create_operator", "tortoise_create_event",
    "tortoise_create_subject", "tortoise_create_object", "tortoise_create_document",
    "tortoise_create_source", "tortoise_checkpoint", "tortoise_file_decision",
    "tortoise_update_entity", "tortoise_update_point", "tortoise_diary_write",
    "tortoise_mitigate_operator",
    # edge-creating tools — edge growth is the same graph-flood family
    "tortoise_create_edge", "tortoise_supersede", "tortoise_invalidate",
    "tortoise_retract_point",
    # epic #888 W2 consolidated write surface
    "tortoise_create_entity", "tortoise_update", "tortoise_operator_action",
    # delegates to hosted_api._seed_demo_graph (creates the 4-layer demo graph)
    "tortoise_onboarding_demo_create",
    # #684: node-creating tools that were missed in the original #329 audit
    "tortoise_file_human_approval",  # creates Event + decision Point + IMPL edges
    "tortoise_assess_source",        # creates assessment Point
})


# #308 (R3, scoping delta 11): the explicit WRITE set for read-velocity
# classification — tools/call for a tool NOT in this set counts as a read.
# NOT derived as the complement of _QUOTA_GATED: tortoise_ingest is
# _quota_gated-wrapped but absent from that frozenset, and the demo-create
# tool writes Points via _enforce_quota without the wrapper. Membership is
# asserted by an introspective test (plan Task 11) so a new write tool cannot
# silently be counted as a read.
WRITE_TOOL_NAMES: frozenset[str] = _QUOTA_GATED | frozenset({
    "tortoise_ingest",               # bulk write (wrapped, not in _QUOTA_GATED)
    "tortoise_onboarding_demo_create",  # seeds the 4-layer demo graph
})


# #329: per-team per-minute LLM-call budget for tortoise_analyze (operator LLM
# keys back outbound calls; the rate limiter alone is not the bound).
_ANALYZE_LLM_BUDGET: dict[str, list[float]] = {}


def _analyze_llm_budget_available() -> bool:
    """True if this team still has analyze LLM budget this minute (HTTP only).

    Beyond budget the tool degrades to keyword-only classification (no paid
    outbound call). Stdio (no team context) is not budgeted.
    """
    import time as _t
    from tortoise.mcp_auth import _current_team_id
    from tortoise.quota import MAX_ANALYZE_LLM_PER_MIN
    team_id = _current_team_id.get()
    if not team_id:
        return True  # stdio/operator — no team budget accounting
    now_ts = _t.time()
    bucket = _ANALYZE_LLM_BUDGET.setdefault(team_id, [])
    bucket[:] = [ts for ts in bucket if now_ts - ts < 60]
    # prune -> check -> append (never pop between check and append — that
    # orphans the appended timestamp and silently disables the budget)
    if len(bucket) >= MAX_ANALYZE_LLM_PER_MIN:
        return False
    bucket.append(now_ts)
    return True


def _enforce_quota(resource: str = "points") -> None:
    """#329: fail-closed team quota pre-write for MCP write tools.

    HTTP mode: limits come from the middleware-resolved ContextVar (same
    limits REST sees); fallback resolves from the registry. Stdio mode
    (no team context) → skip — operator/trusted (batch caps still apply).
    """
    from tortoise.mcp_auth import SELFHOST_TEAM_ID, _current_team_id, _current_team_limits
    from tortoise.quota import enforce_team_limit, resolve_team_limits
    team_id = _current_team_id.get()
    if not team_id:
        return  # stdio/operator — no team context
    if team_id == SELFHOST_TEAM_ID:
        # Selfhost transport placeholder (#338): no tenant registry exists —
        # quota is N/A (selfhost has no billing). Batch caps still apply.
        return
    limits = _current_team_limits.get()
    if limits is None:
        limits = resolve_team_limits(team_id)
    # Count on the SAME team SDK the tool writes to (identical connection),
    # so the count and the write can never target different databases.
    enforce_team_limit(limits, resource, sdk=_get_team_sdk())


def _quota_gated(fn, resource: str = "points", abuse_weight=None):
    """Wrap a bound SDK method with a pre-write quota check + metering.

    Preserves the bound-callable style (_safe(_get_team_sdk().name, ...)):
    the quota check runs INSIDE _safe's try so errors surface as structured
    error dicts (see _safe's QuotaExceededError/QuotaCheckError mapping).

    #681: after a successful write (fn returns without raising), records a
    write op for overage metering. Best-effort — metering failures are
    swallowed and never block the tool.

    #308 (R1, scoping delta 8): ``abuse_weight`` records a WEIGHTED
    point_create event after a successful Point-creating write — int for a
    fixed weight, or callable(result, args, kwargs) -> int for bulk ops
    (ingest/checkpoint/file_decision). Tools that do not create Points pass
    None and record nothing (an edit/update burst must never trip R1).
    """
    def _gated(*args, **kwargs):
        _enforce_quota(resource)
        result = fn(*args, **kwargs)
        # Metering (#681): best-effort, after successful write
        try:
            from tortoise.mcp_auth import _current_team_id, _current_team_limits
            team_id = _current_team_id.get()
            if team_id:
                limits = _current_team_limits.get() or {}
                from tortoise.metering import record_write_ops
                record_write_ops(team_id, tier=limits.get("tier"))
                # #308 (R1): weighted point_create recording + evaluation.
                # The engine piggybacks R2 evaluation on the same call.
                if abuse_weight is not None and not _abuse_off():
                    n = (int(abuse_weight(result, args, kwargs) or 0)
                         if callable(abuse_weight) else int(abuse_weight))
                    if n > 0:
                        from tortoise import abuse as _abuse
                        _abuse.get_engine().record_point_create(team_id, n)
        except Exception:
            pass  # best-effort — never block the tool
        return result
    return _gated


def _abuse_off() -> bool:
    try:
        from tortoise.abuse import abuse_disabled
        return abuse_disabled()
    except Exception:
        return True


def maybe_record_mcp_read(name: str, team_id: str, limits: dict | None) -> None:
    """#308 (R3, scoping delta 11): read-velocity counting for non-write
    tools/call. Explicit write set (WRITE_TOOL_NAMES) — writes never count
    as reads. key_id rides the limits ContextVar (Supabase resolutions carry
    it; registry resolutions may not → per-team counting only there).
    Best-effort: telemetry never breaks the tool call."""
    try:
        if not team_id or team_id == SELFHOST_TEAM_ID or _abuse_off():
            return
        if name in WRITE_TOOL_NAMES:
            return
        from tortoise import abuse as _abuse
        _abuse.record_read((limits or {}).get("key_id"), team_id)
    except Exception:
        pass


def _safe(fn, *args, **kwargs):
    """Call fn; return error dict on exception instead of raising.

    #329: QuotaExceededError → {"error", "code": ERR_QUOTA}; QuotaCheckError
    → {"error", "code": ERR_QUOTA_SERVER} (fail-closed counting).

    Transport-aware auth gate (#236). Fail-closed: if _transport_mode is None
    (unset/misconfigured) ALL operations reject. HTTP mode trusts transport-level
    auth (TeamResolutionMiddleware 401'd pre-dispatch). Stdio mode keeps the
    dev-mode gate. NEVER depends on is_dev_mode() alone — it returns True in
    hosted production (TORTOISE_API_KEY unset), which would silently bypass auth.
    """
    mode = _transport_mode.get()
    if mode is None:
        return {
            "error": (
                "Authentication required. MCP transport mode not initialized."
            )
        }
    if mode == "http":
        pass  # auth enforced at transport (TeamResolutionMiddleware)
    elif mode == "stdio":
        if not _is_dev_mode():
            return {
                "error": (
                    "Authentication required. The MCP stdio transport cannot "
                    "carry auth tokens, so TORTOISE_API_KEY disables stdio. "
                    "Options: (1) self-hosted authenticated MCP — run "
                    "'tortoise serve --http' (tenant mode; bootstrap a key with "
                    "'tortoise key create'); (2) hosted — point your MCP client "
                    "at https://api.premiselabs.co/mcp/ with 'Authorization: "
                    "Bearer <tt_key>'; (3) local stdio dev mode — unset "
                    "TORTOISE_API_KEY."
                )
            }
    else:
        # Unknown transport mode — fail-closed (code-review fix)
        return {"error": f"Unknown MCP transport mode: {mode!r}"}
    try:
        result = fn(*args, **kwargs)
        return result
    except Exception as e:
        monitoring.record_error()
        from tortoise.quota import QuotaCheckError, QuotaExceededError
        if isinstance(e, QuotaExceededError):
            return {"error": str(e), "code": ERR_QUOTA}
        if isinstance(e, QuotaCheckError):
            return {"error": str(e), "code": ERR_QUOTA_SERVER}
        msg = str(e)
        # Sanitize: strip hostnames, ports, passwords from error messages (#43)
        import re
        msg = re.sub(r'://[^@]*@', '://***@', msg)  # password in URI
        msg = re.sub(r'(host=|at |to )[\w.-]+(:\d+)?', r'\1***', msg)  # host:port
        return {"error": msg}


def _scrub_analyze_answer(answer: str) -> str:
    """#329: boundary scrub for analyze() answers — strip common internals.

    analyze() already redacts its own error paths; this is defense-in-depth
    against future regressions (paths, hostnames, credentials).
    """
    import re
    answer = re.sub(r"://[^@\s]*@", "://***@", answer)
    answer = re.sub(r"(?P<pre>[/\\])\w+\.(?:db|jsonl|log)(?=[\"'\s,)])", r"\g<pre>***", answer)
    return answer[:2000]


# #329: quota error codes. NOTE: the ERR_* namespace is split — the
# auth-side codes (ERR_UNAUTHORIZED/-32001, ERR_RATE_LIMIT/-32002,
# ERR_EXCLUDED/-32004, ERR_REGISTRY/-32005, ERR_SUSPENDED/-32006) live in
# mcp_auth.py; the quota + tool-validation codes live HERE in mcp_server.py.
# Pre-existing collision: ERR_QUOTA=-32006 (here) vs ERR_SUSPENDED=-32006
# (mcp_auth) — tracked as a follow-up (client cannot distinguish the two).
ERR_QUOTA = -32006
ERR_QUOTA_SERVER = -32007
# Application-defined pre-SDK param errors (tool-level validation that never
# reaches the SDK): invalid granularity / promotion_policy on tortoise_ingest
# return {error, code: ERR_INVALID} naming the valid values (E2E-8.3 pin).
ERR_INVALID = -32003


def _http_excluded_error() -> dict:
    """#236: JSON-RPC error for tools excluded from the tenant HTTP surface (D4)."""
    return {
        "jsonrpc": "2.0",
        "error": {
            "code": ERR_EXCLUDED,
            "message": "This tool is not available over HTTP. "
                        "Use the hosted REST API or stdio MCP.",
        },
        "id": None,
    }


def _parse(v: Any) -> Any:
    """Parse JSON string inputs from LLM agents into native Python types.

    FastMCP strict-typed schemas reject JSON strings for list/dict params.
    LLM agents naturally emit JSON strings. This bridges the gap.
    """
    if isinstance(v, str):
        try:
            return json.loads(v)
        except (json.JSONDecodeError, TypeError):
            return v
    return v


def tortoise_create_point(kind: str, content: str,
                          authoredBy: str | None = None,
                          props: Any = None,
                          dedup: bool = True) -> dict:
    """Create a Point node (statement, decision, vision, hypothesis, etc.).

    dedup=True (default): idempotent — returns existing Point if content matches.
    dedup=False: force-create even if content is identical.

    → See /skill:tortoise-graph-reasoning for pointKind guidance:
      evidence is a role (not a kind), use Source for provenance.
    """
    props = _parse(props)
    merged = dict(props or {})
    if authoredBy:
        merged["authoredBy"] = authoredBy
    # #329 tag batch cap + value validation
    from tortoise.quota import MAX_TAGS_PER_POINT
    tags = merged.get("tags") or []
    if isinstance(tags, list):
        if len(tags) > MAX_TAGS_PER_POINT:
            return {"error": f"tags exceed the cap ({MAX_TAGS_PER_POINT})", "code": ERR_QUOTA}
        for t in tags:
            if not isinstance(t, str) or not t.strip() or len(t) > 200:
                return {"error": f"invalid tag value: {t!r} (must be a non-empty string ≤ 200 chars)"}
    merged["dedup"] = dedup
    return _safe(_quota_gated(_get_team_sdk().create_point, "points", abuse_weight=1), kind, content, **merged)


def tortoise_query(kind: str | None = None,
                   filters: Any = None,
                   text: str | None = None,
                   order_by: str | None = None,
                   min_confidence: float | None = None,
                   entity_type: str = "point",
                   tag: str | None = None,
                   include_retracted: bool = False,
                   offset: int | None = None,
                   limit: int | None = None,
                   page: int | None = None) -> list[dict] | dict:
    """Query points by pointKind and/or property filters — structural exact-match
    retrieval for known shapes (Epic #888 consolidation of paginated_query +
    query_points_by_tag into this one tool).

    - Structural path (text=None): property-filter query via sdk.query().
    - tag=<name>: filter Points by TAGGED edge (previously
      tortoise_query_points_by_tag). Tag mode takes precedence over text and
      ignores kind/filters/min_confidence/entity_type/order_by.
    - Pagination: pass offset=/limit= (or 1-based page=). When any pagination
      param is set, returns {results, total, hasMore} (previously
      tortoise_paginated_query); a plain list is returned otherwise. Not
      combinable with text= (fts has no offset/skip) — error dict returned.
    - text=<query>: routes through tortoise_fts_query() for hybrid search
      (unchanged behavior; limit defaults to 100).
    - include_retracted=True surfaces tombstones on every path (structural,
      paginated, and tag modes); default False excludes them.

    Validation: page must be >= 1, offset >= 0, limit >= 1 — violations return
    a structured error dict.

    Use tortoise_query for known shapes; use tortoise_search for semantic
    relevance; use tortoise_get_point for a single known ID.
    """
    filters = _parse(filters)
    if page is not None and page < 1:
        return {"error": "page must be >= 1 (1-based), got " + str(page)}
    if offset is not None and offset < 0:
        return {"error": "offset must be >= 0, got " + str(offset)}
    if limit is not None and limit < 1:
        return {"error": "limit must be >= 1, got " + str(limit)}
    # Resolve include_retracted (#888): explicit param wins; else the filters-dict
    # value (don't silently drop a caller's True intent — review P2-2); else False.
    # Guard non-dict filters: a malformed-JSON string from _parse passes the substring
    # `in` check but has no .pop -> unguarded AttributeError (review P1-1).
    if isinstance(filters, dict) and "include_retracted" in filters:
        if not include_retracted:
            include_retracted = bool(filters.pop("include_retracted"))
        else:
            filters.pop("include_retracted")
    paginated = offset is not None or page is not None
    eff_limit = limit if limit is not None else (20 if paginated else 100)
    if tag is not None:
        rows = _safe(_get_team_sdk().query_points_by_tag, tag)
        if not isinstance(rows, list):
            return rows
        # query_points_by_tag has no retracted exclusion in the SDK — mirror
        # the tombstone contract of the other paths here (Epic #888).
        if not include_retracted:
            rows = [r for r in rows if r.get("status") != "retracted"]
        if not paginated:
            return rows
        eff_offset = offset if offset is not None else 0
        if page is not None:
            eff_offset = (page - 1) * eff_limit
        total = len(rows)
        return {"results": rows[eff_offset:eff_offset + eff_limit],
                "total": total,
                "hasMore": eff_offset + eff_limit < total}
    if text:
        if paginated:
            return {"error": "offset/page not supported with text — use limit only, or tortoise_search"}
        return _safe(_get_team_sdk().tortoise_fts_query, text, kind=kind,
                     entity_type=entity_type, limit=eff_limit,
                     min_confidence=min_confidence or 0.0,
                     order_by=order_by or "relevance")
    if paginated:
        eff_offset = offset if offset is not None else 0
        if page is not None:
            eff_offset = (page - 1) * eff_limit
        return _safe(_get_team_sdk().paginated_query, kind, skip=eff_offset,
                     limit=eff_limit, include_retracted=include_retracted,
                     **(filters or {}))
    result = _safe(_get_team_sdk().query, kind,
                   include_retracted=include_retracted, **(filters or {}))
    # If empty results and a kind filter was provided, attach suggestion
    if isinstance(result, list) and len(result) == 0 and kind is not None:
        from tortoise.query_suggestions import compute_suggestion
        suggestion = compute_suggestion(kind)
        if suggestion:
            return {"results": result, "suggestion": suggestion}
    return result


def tortoise_paginated_query(kind: str | None = None,
                             skip: int = 0, limit: int = 20,
                             filters: Any = None,
                             include_retracted: bool = False) -> dict:
    """DEPRECATED (Epic #888) — thin alias for tortoise_query(offset=, limit=).

    Kept for one release with grace; will be removed in the next release.
    Migrate to: tortoise_query(kind=..., offset=skip, limit=limit, filters=...)
    """
    filters = _parse(filters)
    return tortoise_query(kind=kind, filters=filters, offset=skip, limit=limit,
                          include_retracted=include_retracted)


def tortoise_check_structure() -> list[dict]:
    """Check Gate 0→4 chain integrity (orphans, dangling refs).
    Alias → overview(section='structure_check') (epic #888 W3)."""
    return _safe(_get_team_sdk().check_structure)


def tortoise_summarize_structure() -> dict:
    """Count points per Gate (by pointKind). Returns {gateN_*, total}.
    Alias → overview(section='structure') (epic #888 W3)."""
    return _safe(_get_team_sdk().summarize_structure)


def tortoise_list_pointkinds() -> list[dict]:
    """List all pointKinds present in the graph with counts. What EXISTS.
    Alias → overview(section='pointkinds') (epic #888 W3)."""
    return _safe(_get_team_sdk().list_pointkinds)


def tortoise_list_sources() -> list[dict]:
    """List all Sources with point counts. Where data came FROM.
    Alias → overview(section='sources') (epic #888 W3)."""
    return _safe(_get_team_sdk().list_sources)


def tortoise_list_namespaces() -> list[dict]:
    """List installed pack namespaces.
    Alias → overview(section='namespaces') (epic #888 W3)."""
    return _safe(_get_team_sdk().list_namespaces)


def tortoise_list_tags() -> list[dict]:
    """List all Tag names with count of tagged Points. Where tags are USED.
    Alias → overview(section='tags') (epic #888 W3)."""
    return _safe(_get_team_sdk().list_tags)


def tortoise_query_points_by_tag(tag: str) -> list[dict]:
    """DEPRECATED (Epic #888) — thin alias for tortoise_query(tag=...).

    Kept for one release with grace; will be removed in the next release.
    Migrate to: tortoise_query(tag=tag)

    Note: passes include_retracted=True to preserve this tool's pre-#888 behavior
    (raw tag results incl. tombstones); the new tortoise_query(tag=...) default
    excludes retracted points.
    """
    return tortoise_query(tag=tag, include_retracted=True)


def tortoise_get_point(id: str) -> dict:
    """Get a single Point by ID. Returns all properties, or empty dict.
    Alias → get(id, type='point') (epic #888 W3)."""
    return _safe(_get_team_sdk().get_point, id)


# ── Entity Resolution (GAP-01 #6987) ──────────────────────────

def tortoise_suggest_entry_points(query: str, limit: int = 5,
                                  kind_filter: str | None = None) -> list[dict]:
    """Entity resolution — NL query → matching entities from the graph.

    Uses hybrid search (tortoise_fts_query) for semantic entity resolution.
    Falls back to string match (CONTAINS) if hybrid search unavailable.
    Returns [{id, name, kind, confidence}] sorted by confidence DESC.
    """
    try:
        results = _safe(_get_team_sdk().tortoise_fts_query, query, kind=kind_filter, limit=limit)
        if isinstance(results, list) and results and "error" not in results[0]:
            return [{"id": r["id"], "name": r.get("content", ""),
                     "kind": r.get("point_kind", ""),
                     "confidence": round(
                         0.5 * r.get("scores", {}).get("rrf", 0.0) +
                         0.5 * r.get("ep", {}).get("confidence_mean", 0.0), 4)}
                    for r in results]
    except Exception:
        pass
    return _safe(_get_team_sdk().suggest_entry_points, query, limit=limit, kind_filter=kind_filter)


# ── Semantic Search (#6990) ────────────────────────────────────

def tortoise_search(query: str | None = None, kind: str | None = None,
                    threshold: float = 0.0, limit: int = 10,
                    min_confidence: float = 0.0,
                    order_by: str = "relevance",
                    entity_type: str = "point") -> list[dict]:
    """Hybrid search with RRF fusion + EP annotation.

    entity_type: 'point' (default), 'event', 'subject', 'document', 'object', 'operator', or 'source'.
    Full-scan mode: omit query, set kind → all Points of kind.
    Best-match mode: provide query → RRF fusion of FTS + vector + structural.

    Point results annotated with EP breakdown (confidence_mean + variance + contested + contention).
    min_confidence defaults to 0.0 (no filter).

    order_by (#25, #560):
      - 'relevance' (default): pure RRF fusion order (FTS + vector + structural).
      - 'confidence': sort by the PERSISTED EP confidence (n.confidence), not the
        structural edge ratio.
      - 'graph': graph-informed rerank — weighted fusion of similarity +
        persisted EP confidence + operator connectivity + 30-day recency decay
        (tortoise.ranking.GraphRanker). Results annotated with a
        'graph_ranking' breakdown {similarity, graph_boost, recency_boost,
        final_score, variance, contested}.

    Contestation is surfaced, never scored: contested claims carry
    ep.contested=true + ep.variance (real EP posterior variance from persisted
    α/β) but are ranked exactly like any other claim with the same confidence
    (#580/#583).

    Note: threshold default changed from 0.3 (Phase 0 semantic search) to 0.0.
    RRF scores are rank-based (0.01-0.05 range typical), not cosine similarity (0-1).
    Use threshold > 0 to filter out very weak matches; the old 0.3 default would
    reject nearly all RRF results. (#20)
    """
    return _safe(_get_team_sdk().tortoise_fts_query, query, kind=kind,
                 threshold=threshold, limit=limit,
                 entity_type=entity_type,
                 min_confidence=min_confidence, order_by=order_by)


# ── Recall — epistemic intents (epic #898) ─────────────────────

# UC1 default exponents/weights for the multiplicative gate (Wave A).
_RECALL_STATE_DEFAULTS = {"relevance_exp": 1.0, "confidence_exp": 1.0,
                          "centrality_weight": 0.10}
# UC2 gap thresholds (Wave B).
_RECALL_GAPS_DEFAULTS = {"min_load": 1, "max_support": 2}
# UC3 subgraph expansion (Wave B).
_RECALL_SUBGRAPH_DEFAULTS = {"depth": 2, "completeness": "full"}
# Valid recall modes (preset + override pattern, #898 design-decision comment).
_RECALL_MODES = ("state", "gaps", "subgraph", "custom")


def tortoise_recall(query: str | None = None,
                    mode: str = "state",
                    kind: str | None = None,
                    limit: int | None = None,
                    include_superseded: bool = False,
                    min_confidence: float = 0.0,
                    relevance_exp: float | None = None,
                    confidence_exp: float | None = None,
                    centrality_weight: float | None = None,
                    seed: str | None = None,
                    depth: int | None = None,
                    completeness: str | None = None,
                    min_load: int | None = None,
                    max_support: int | None = None,
                    max_nodes: int | None = None) -> dict:
    """Epistemic recall — four intents via mode (preset + override pattern).

    mode="state" (default, UC1): "what is true and high-confidence right
    now". Multiplicative confidence gate
    (score = relevance^a × confidence^b × (1 + w_c·centrality)), excludes
    superseded/deprecated/retracted by default (include_superseded=True
    brings them back), object-centric (Objects + the Points about them
    ranked together), surfaces the most important arguments (operators),
    high-contention NANDs and mitigations, and flags contested claims with
    attached counter-evidence (never rank-penalized).

    mode="gaps" (UC2): load-bearing but under-supported claims — the weak
    links of a reasoning cycle. Graph-structure query (epistemic load vs
    epistemic support): score = load / (1 + support), with load = outgoing
    IMPL + outgoing NAND edges and support = incoming IMPL +
    extractedFrom→Source edges (reads IMPL/NAND whether operator-mediated
    or direct — reification rule). Requires ``query`` (topic scope) or
    ``kind`` (population scan). Preset: min_load=1, max_support=2, limit=20
    (all overridable).

    mode="subgraph" (UC3): the COMPLETE connected subgraph for a
    seed/topic — completeness-optimized (high recall, precision secondary),
    used before connecting a new document. Requires ``seed`` (node id,
    Source url, or topic text). Returns {nodes, edges, stats}.

    mode="custom": raw parameters, full control — params pass straight
    through to the state machinery with NO mode tuning (the underlying
    function defaults apply to anything unset). Mode-specific params
    (seed/depth/completeness/min_load/max_support/max_nodes) are NOT
    applicable to custom (custom is state-shaped).

    Per-mode defaults are set by the preset; every param is individually
    overridable per call.

    Returns {"mode": ..., "results": [...]} (state/gaps/custom — each result
    carries the standard SearchResult shape plus recall_ranking /
    gaps_ranking breakdowns) or {"mode": "subgraph", "nodes": [...],
    "edges": [...], "stats": {...}}.
    """
    if mode not in _RECALL_MODES:
        return {
            "mode": mode,
            "error": f"recall mode {mode!r} not recognized — use "
                     f"state|gaps|subgraph|custom.",
        }

    if mode == "gaps":
        results = _safe(
            _get_team_sdk().recall_gaps, query, kind=kind,
            limit=limit if limit is not None else 20,
            min_load=min_load if min_load is not None else _RECALL_GAPS_DEFAULTS["min_load"],
            max_support=max_support if max_support is not None else _RECALL_GAPS_DEFAULTS["max_support"],
            include_superseded=include_superseded,
        )
    elif mode == "subgraph":
        results = _safe(
            _get_team_sdk().recall_subgraph, seed or query,
            depth=depth if depth is not None else _RECALL_SUBGRAPH_DEFAULTS["depth"],
            completeness=completeness if completeness is not None else _RECALL_SUBGRAPH_DEFAULTS["completeness"],
            max_nodes=max_nodes if max_nodes is not None else 500,
        )
    elif mode == "custom":
        # Raw params, full control — no preset clamping.
        results = _safe(
            _get_team_sdk().recall_state, query, kind=kind,
            limit=limit if limit is not None else 10,
            include_superseded=include_superseded,
            min_confidence=min_confidence,
            relevance_exp=relevance_exp if relevance_exp is not None else _RECALL_STATE_DEFAULTS["relevance_exp"],
            confidence_exp=confidence_exp if confidence_exp is not None else _RECALL_STATE_DEFAULTS["confidence_exp"],
            centrality_weight=centrality_weight if centrality_weight is not None else _RECALL_STATE_DEFAULTS["centrality_weight"],
        )
    else:  # state
        defaults = _RECALL_STATE_DEFAULTS
        results = _safe(
            _get_team_sdk().recall_state, query, kind=kind,
            limit=limit if limit is not None else 10,
            include_superseded=include_superseded,
            min_confidence=min_confidence,
            relevance_exp=relevance_exp if relevance_exp is not None else defaults["relevance_exp"],
            confidence_exp=confidence_exp if confidence_exp is not None else defaults["confidence_exp"],
            centrality_weight=centrality_weight if centrality_weight is not None else defaults["centrality_weight"],
        )

    # _safe returns an error dict on SDK exceptions — surface it at the TOP
    # level so consumers never mis-parse results.
    if isinstance(results, dict) and "error" in results:
        return {"mode": mode, **results}
    if mode == "subgraph":
        # recall_subgraph returns {nodes, edges, stats} — spread flat.
        return {"mode": mode, **results}
    return {"mode": mode, "results": results}


# ── EP Belief Propagation (#6908) ────────────────────────────────

def tortoise_compute_confidence(factors: Any = None,
                    evidence: Any = None,
                    anchors: Any = None,
                    max_hops: int = 1,
                    rel_filter: str = "IMPL|NAND",
                    direction: str = "both",
                    require_calibration: bool = False) -> dict:
    """Compute confidence via EP belief propagation. Returns {iterations, converged, confidences}.

    Pass anchors=[point_ids] for BFS subgraph selection.
    Pass require_calibration=True to gate on calibration state.
    max_hops: BFS depth from anchors (default 1).
    rel_filter: edge types — "IMPL", "NAND", or "IMPL|NAND" (default).
    direction: IMPL traversal — "incoming", "outgoing", or "both" (default).
    """
    factors = _parse(factors)
    evidence = _parse(evidence)
    anchors = _parse(anchors)
    return _safe(_get_team_sdk().compute_confidence, factors, evidence,
                 anchors=anchors,
                 max_hops=max_hops, rel_filter=rel_filter,
                 direction=direction,
                 require_calibration=require_calibration)


def tortoise_set_point_baseline(claim_id: str, alpha: float, beta: float) -> dict:
    """Set Beta prior evidence for a claim."""
    return _safe(_get_team_sdk().set_point_baseline, claim_id, alpha, beta)


def tortoise_get_confidence(claim_id: str) -> dict:
    """Get EP confidence for a claim: {mean, variance, alpha, beta}."""
    return _safe(_get_team_sdk().get_confidence, claim_id)


def tortoise_calibrate_summary() -> list[dict]:
    """Audit graph calibration state. Returns per-point guidance."""
    return _safe(_get_team_sdk().calibrate_summary)


def tortoise_dream(full: bool = False, dirty_only: bool = True,
                   max_hops: int = 2) -> dict:
    """Run EP stabilization (dreaming, #85).

    Stabilizes confidence values after batch writes without an explicit
    compute_confidence call. Default: dreams the accumulated dirty subgraph
    (incremental). Set full=True for whole-graph stabilization.

    #329: EXCLUDED from tenant HTTP — whole-graph EP is CPU-heavy
    (operator/stdio only; REST /v1/dream is separately budgeted).
    """
    if _transport_mode.get() == "http":
        return _http_excluded_error()
    return _safe(_get_team_sdk().dream, dirty_only=dirty_only, full=full,
                 max_hops=max_hops)


def tortoise_update_point(id: str, props: Any) -> dict:
    """Update properties on a Point. Safe — modifies one Point only."""
    props = _parse(props)
    return _safe(_quota_gated(_get_team_sdk().update_point, "points"), id, **(props or {}))

def tortoise_create_operator(op_type: str, source_id: str, target_ids: Any,
                              direction: str = "bidirectional") -> dict:
    """Create an operator connecting Points.
    
    op_type: 'IMPL' (A supports B), 'NAND' (A contradicts B),
             'composedOf'/'decomposesInto'/'contains'/'wraps' → stored as hasPart edge.
    source_id: source/parent Point ID.
    target_ids: target/child Point IDs (1 for IMPL/NAND, N for part/whole).
    direction: 'bidirectional' (default) or 'unidirectional' — EP propagation
      direction. Default is mutual (both directions); pass 'unidirectional'
      for a directed attack (attacker's truth penalizes the target, no
      back-pressure).

    → See /skill:tortoise-graph-reasoning for proper usage:
      annotation, mitigation, NAND constraints, veracity vs implication.
    """
    target_ids = _parse(target_ids)
    # #329 batch cap on operator target fan-out
    from tortoise.quota import MAX_OPERATOR_TARGETS
    if isinstance(target_ids, list) and len(target_ids) > MAX_OPERATOR_TARGETS:
        return {"error": f"create_operator target_ids exceed the cap ({MAX_OPERATOR_TARGETS})",
                "code": ERR_QUOTA}
    return _safe(_quota_gated(_get_team_sdk().create_operator, "points", abuse_weight=1), op_type, source_id, target_ids,
                 direction=direction)


def tortoise_annotate_operator(id: str, bias: float, precision: float,
                                consistency: float, directness: float) -> dict:
    """Annotate an operator Point with structured epistemic dimensions.

    bias: 0-1 — hidden stake beyond stated position.
    precision: 0-1 — how narrow/well-defined the relevance claim is.
    consistency: 0-1 — stability across contexts.
    directness: 0-1 — how directly source bears on target.
    """
    return _safe(_get_team_sdk().annotate_operator, id, bias, precision, consistency, directness)


def tortoise_get_operator(id: str) -> dict:
    """Get an operator Point by ID. Returns all properties including annotation dimensions.
    Raises error if the Point is not an operator.
    Alias → get(id, type='operator') (epic #888 W3)."""
    point = _safe(_get_team_sdk().get_point, id)
    if isinstance(point, dict) and point and not point.get("is_operator"):
        return {"error": f"Point {id!r} is not an operator"}
    return point


def tortoise_mitigate_operator(id: str, reason: str, strength: float = 0.5) -> dict:
    """Create a mitigation Point that modulates an operator's edge strength.

    reason: Why the edge is weaker than it appears.
    strength: 0-1 — 0=fully neutralized, 1=fully intact (default 0.5).
    Idempotent — second call updates existing mitigation.
    """
    return _safe(_quota_gated(_get_team_sdk().mitigate_operator, "points", abuse_weight=1), id, reason, strength)


def tortoise_file_decision(options: Any, evidence: Any,
                           choice: int) -> dict:
    """File a simple decision directly to the graph.

    Creates decision + options + evidence + IMPL edges atomically.
    For low-stakes decisions where the answer is clear — no EP,
    no calibration, no research cycles. Under 5 graph operations.

    options: list of option descriptions (e.g. ["JSON", "YAML", "TOML"])
    evidence: list of evidence statements supporting the choice
    choice: 0-indexed option index (e.g. 0 = JSON)

    Returns {decision_id, option_ids: [...], evidence_ids: [...]}.
    """
    options = _parse(options)
    evidence = _parse(evidence)
    # #329 batch caps
    from tortoise.quota import MAX_FILE_DECISION_EVIDENCE, MAX_FILE_DECISION_OPTIONS
    if isinstance(options, list) and len(options) > MAX_FILE_DECISION_OPTIONS:
        return {"error": f"file_decision options exceed the cap ({MAX_FILE_DECISION_OPTIONS})",
                "code": ERR_QUOTA}
    if isinstance(evidence, list) and len(evidence) > MAX_FILE_DECISION_EVIDENCE:
        return {"error": f"file_decision evidence exceeds the cap ({MAX_FILE_DECISION_EVIDENCE})",
                "code": ERR_QUOTA}
    return _safe(_quota_gated(_get_team_sdk().file_decision, "points",
                          abuse_weight=lambda r, a, k: 1 + len(a[0] or []) + len(a[1] or [])), options, evidence, choice)


def tortoise_file_human_approval(approver_id: str, artifact_id: str,
                                 point_ids: Any,
                                 decision_content: str | None = None) -> dict:
    """File a human approval of a planning artifact to the graph (#531).

    Records an Event (eventKind: humanApproval) with full provenance
    (approver, artifact, approved claims), creates a decision Point
    (pointKind: humanApproval) that seeds grounding and carries an EP
    evidence prior, and fans out unidirectional IMPL edges (label
    approvedBy) from the approval Point to the approved claim Points so
    dependent claims strengthen.

    approver_id: Subject id of the human approving
    artifact_id: Object/Document id of the artifact being approved
    point_ids: claim Point ids being approved
    decision_content: optional content override for the decision Point

    Returns {event_id, decision_point_id, impl_operator_ids, confidence_delta}.
    """
    point_ids = _parse(point_ids)
    return _safe(_quota_gated(_get_team_sdk().file_human_approval, "points", abuse_weight=1),
                 approver_id, artifact_id, point_ids, decision_content)


def tortoise_delete_point(id: str) -> dict:
    """Delete a Point. DESTRUCTIVE — requires human confirmation. Cannot be undone."""
    return _safe(_get_team_sdk().delete_point_wrapped, id)


def tortoise_invalidate(id: str, corrected_by_id: str) -> dict:
    """Mark a Point outdated with a CORRECTS edge from the correcting Point.

    The `corrected_by_id` point CORRECTS the invalidated point.
    Returns {invalidated, id, corrected_by}.
    """
    return _safe(_quota_gated(_get_team_sdk().invalidate_point, "points"), id, corrected_by_id)


def tortoise_supersede(old_id: str, new_id: str, transfer_edges: bool = True) -> dict:
    """Atomically replace old Point with new — CORRECTS edge + outdated flag.

    transfer_edges=True (default): full supersede — all edges move from old to
    new. transfer_edges=False: invalidate behavior — mark old outdated with a
    CORRECTS edge only (no edge transfer). Absorbs tortoise_invalidate.
    Returns {invalidated, id, corrected_by} (+ edges_transferred when
    transfer_edges=True).
    """
    return _safe(_quota_gated(_get_team_sdk().supersede, "points"),
                 old_id, new_id, transfer_edges=transfer_edges)


def tortoise_retract_point(id: str) -> dict:
    """Tombstone-retract a Point — status='retracted' (point stays in graph).

    Terminal state transition; default query/list surfaces exclude retracted
    points (opt-in via include_retracted). Raises ValueError if the point is
    missing, is an operator, or is already terminal (retracted/superseded/
    archived).
    """
    return _safe(_quota_gated(_get_team_sdk().retract_point, "points"), id)


def tortoise_events_poll(after: str | None = None, types: Any = None,
                         limit: int = 100) -> dict:
    """Poll graph/claim events after an opaque cursor (at-least-once).

    Returns {events: [...], next_cursor}. after=None → tail (oldest retained).
    Expired cursor → structured error ('cursor expired — replay from tail');
    malformed cursor → 'invalid cursor'. types: comma-free list of event types
    (PointAdded, OperatorAdded, PointRetracted, PointSuperseded,
    OperatorAnnotated) or None for all.

    readOnlyHint covers user-visible state: the poll NEVER mutates user
    content. A rare maintenance purge (retention) may run at most once per
    TORTOISE_EVENT_RETENTION_INTERVAL — an internal housekeeping DELETE of
    expired :GraphEvent nodes, gated so steady-state polls are read-only.
    """
    if types is not None:
        types = _parse(types)
        if not isinstance(types, list):
            types = [types]
    return _safe(_get_team_sdk().events_poll, after=after, types=types, limit=limit)



# ── Navigation (#6962, #6963, #6964) ─────────────────────────────

def tortoise_entity_profile(entity_id: str, hops: int = 2,
                             graph_name: str = "tortoise",
                             pointKind: str | None = None,
                             confidenceMin: float | None = None) -> dict:
    """Entity-centric traversal — BFS from entity node, categorize connected nodes.

    Returns {entity: {...}, connected: {points, documents, events, subjects, objects}}.
    Optional filters: pointKind, confidenceMin.
    """
    from tortoise.navigation import entityProfile
    proj = _get_team_sdk()._get_proj()
    # #236: HTTP mode ignores user-supplied graph_name — team graph authoritative
    # (cross-tenant injection guard). Stdio mode honors it (operator use).
    if _transport_mode.get() == "http":
        graph_name = f"team_{_current_team_id.get()}"
    return _safe(entityProfile, proj.db, graph_name, entity_id,
                  hops=hops, pointKind=pointKind, confidenceMin=confidenceMin)


def tortoise_traverse(entity_id: str, max_hops: int = 2,
                       graph_name: str = "tortoise") -> dict:
    """Multi-hop graph traversal from entity following ALL relationship types.

    Returns {entity: {...}, nodes: [{node, relationship, depth}, ...]}.
    """
    from tortoise.navigation import tortoise_traverse as _traverse
    proj = _get_team_sdk()._get_proj()
    # #236: HTTP mode ignores user-supplied graph_name (cross-tenant guard)
    if _transport_mode.get() == "http":
        graph_name = f"team_{_current_team_id.get()}"
    return _safe(_traverse, proj.db, graph_name, entity_id, max_hops)


def main():
    _transport_mode.set("stdio")
    monitoring.register(_get_sdk())
    uri = os.environ.get("TORTOISE_DB_URI")
    db_path = os.environ.get("TORTOISE_DB_PATH")
    if not uri and not db_path:
        if os.environ.get("TORTOISE_ALLOW_EMBEDDED") == "1":
            _log.warning(
                "Neither TORTOISE_DB_URI nor TORTOISE_DB_PATH set — running "
                "embedded (empty graph). Test-only escape hatch."
            )
        else:
            _log.error(
                "Neither TORTOISE_DB_URI nor TORTOISE_DB_PATH is set. MCP would "
                "silently connect to an empty embedded DB. Set TORTOISE_DB_URI "
                "(docker://...) or TORTOISE_DB_PATH (canonical embedded path) in "
                "the environment or .env, then restart. "
                "Override with TORTOISE_ALLOW_EMBEDDED=1 (test only)."
            )
            sys.exit(1)
    # #942: embedded FalkorDBLite is SINGLE-WRITER / EVAL-ONLY. `not uri` is
    # NOT the predicate — _get_sdk treats a bare-path TORTOISE_DB_URI as
    # embedded (backward compat), and that path must warn too. Placed AFTER
    # the config-error guard above so a missing-config exit stays clean.
    # Single-fire: `tortoise serve` stdio and the tortoise-serve console
    # script both funnel here; no other entrypoint prints it for stdio.
    if not _is_db_uri(uri):
        from tortoise._embedded import EMBEDDED_EVAL_BANNER

        print(EMBEDDED_EVAL_BANNER, file=sys.stderr)
    mcp.run(transport="stdio")


# ── P0 Group 3: Checkpoint, Diary, Status, Ingest ──────────────

def tortoise_checkpoint(items: Any,
                        agent_name: str = "checkpoint",
                        threshold: float = 0.95) -> dict:
    """Session batch save — two-tier dedup (content hash + embedding similarity).

    items: [{wing, room, content}, ...]
    agent_name: name for provenance events (default: "checkpoint")
    threshold: cosine similarity for semantic dedup (0.0-1.0).
               Set to 1.0 to disable semantic dedup (hash-only).
    Returns {filed: N, duplicates: M}.
    """
    items = _parse(items)
    # #329 batch cap: a single checkpoint call must not create unbounded nodes
    from tortoise.quota import MAX_CHECKPOINT_ITEMS
    if isinstance(items, list) and len(items) > MAX_CHECKPOINT_ITEMS:
        return {"error": f"checkpoint items exceed the batch cap ({MAX_CHECKPOINT_ITEMS})",
                "code": ERR_QUOTA}
    return _safe(_quota_gated(_get_team_sdk().checkpoint, "points",
                          abuse_weight=lambda r, a, k: int((r or {}).get("filed") or 0)), items,
                 agent_name=agent_name, threshold=threshold)


def tortoise_diary_write(agent_name: str, entry: str,
                         topic: str | None = None,
                         wing: str | None = None) -> dict:
    """Write an agent diary entry (AAAK format suggested).
    Creates a Point with pointKind=diary, authoredBy=agent.
    """
    return _safe(_quota_gated(_get_team_sdk().diary_write, "points", abuse_weight=1), agent_name, entry, topic=topic, wing=wing)


def tortoise_diary_read(agent_name: str, last_n: int = 10,
                        wing: str | None = None) -> list[dict]:
    """Read recent diary entries for an agent, newest first."""
    return _safe(_get_team_sdk().diary_read, agent_name, last_n, wing=wing)


def tortoise_list_graphs() -> list[str]:
    """List graph names. HTTP: only the calling team's own graphs (exact
    team_{team_id} equality — no cross-tenant enumeration). Stdio: full list
    (operator context).
    Alias → overview(section='graphs') (epic #888 W3)."""
    graphs = _safe(_get_team_sdk().list_graphs)
    if not isinstance(graphs, list):
        return graphs
    if _transport_mode.get() == "http":
        from tortoise.mcp_auth import _current_team_id
        team_id = _current_team_id.get()
        own = f"team_{team_id}" if team_id else None
        return [g for g in graphs if own is not None and g == own]
    return graphs


def tortoise_status() -> dict:
    """Graph health + entity counts + FalkorDB connectivity.
    Returns {connected, counts: {Point, Event, ...}, total_entities}.
    Alias → overview(section='status') (epic #888 W3).
    """
    return _safe(_get_team_sdk().status)


def tortoise_health() -> dict:
    """Health check + basic metrics: graph_size, last_ingest, error_count, uptime.
    Alias → overview(section='health') (epic #888 W3)."""
    # #236: route through _safe() so every tool is gated (defense-in-depth;
    # reachable only post-auth over HTTP).
    return _safe(monitoring.metrics)


def tortoise_session_context() -> dict:
    """Return 'what happened last session' — diary entries, recent Points, Events, confidence changes.
    Returns {no_prior_sessions, diary_entries, recent_points, recent_events, confidence_changes}.
    """
    return _safe(_get_team_sdk().session_context)


def tortoise_ingest_corpus(directory: str) -> dict:
    """Batch document ingestion — walk directory, parse YAML frontmatter
    from .md files, create/update Document nodes.
    Returns {ingested, updated, skipped}.

    #236: EXCLUDED from tenant HTTP — walks server filesystem with a
    user-supplied path (path-traversal vector). Stdio-only.
    """
    if _transport_mode.get() == "http":
        return _http_excluded_error()
    return _safe(_get_team_sdk().ingest_corpus, directory)

# ── Taxonomy ─────────────────────────────────────────────────

def tortoise_taxonomy() -> dict[str, int]:
    """Count entities by node label. Returns {Point: N, Event: N, Subject: N, Object: N, Document: N}.
    Alias → overview(section='taxonomy') (epic #888 W3)."""
    return _safe(_get_team_sdk().taxonomy)


def tortoise_list_topics(entity_id: str) -> dict:
    """entityProfile lite for an entity. Returns {id, pointKind, neighbors, neighborCounts}.
    Alias → overview(section='topics', entity_id=...) (epic #888 W3)."""
    return _safe(_get_team_sdk().list_topics, entity_id)


def tortoise_topic_summarize(topic: str,
                             max_seeds: int = 50,
                             max_hops: int = 1,
                             include_relationships: bool = True) -> dict:
    """Epistemic topic summarization — settled vs contested structure (#592).

    For a topic query (e.g. "pricing", "architecture"), returns the epistemic
    structure: what is significant/settled (high confidence, strong connections)
    and what is contested (elevated variance, NAND conflicts), plus the argument
    topology connecting them.

    Classification uses EP posterior variance from persisted posterior (posterior_alpha/beta, falling back to ep_alpha/beta priors):
    - significant/settled: confidence_mean >= 0.7 AND variance < 0.01
    - contested: variance > 0.04 (destabilized posterior)
    - disputed pairs: NAND-connected where both have variance > 0.02

    Args:
        topic: Topic string to summarize (e.g. "pricing", "security").
        max_seeds: Max seed Points to retrieve (default 50).
        max_hops: Operator-chain expansion from seeds (0 = seeds only).
        include_relationships: Fetch argument topology (default True).

    Returns:
        {topic, total_points, significant: [...], contested: [...],
         disputed_pairs: [...], argument_structure: {...}, meta: {...}}
    """
    return _safe(_get_team_sdk().topic_summarize, topic,
                 max_seeds=max_seeds, max_hops=max_hops,
                 include_relationships=include_relationships)


# ── Graph Analysis ──────────────────────────────────────────────

def tortoise_analyze(question: str,
                    entityId: str | None = None,
                    anchor_ids: Any = None,
                    max_hops: int = 1,
                    rel_filter: str = "IMPL|NAND",
                    direction: str = "both") -> dict:
    """Answer natural language questions about the Tortoise epistemic graph.

    Ask things like: "where is the disagreement?" "what supports claim X?"
    "what are we most uncertain about?" "show me the evidence chain for Y."

    Optional entityId scopes the analysis to a specific entity's subgraph.
    Optional anchor_ids (list of Point IDs) scopes via BFS subgraph selection.
    max_hops: BFS depth from anchors (default 1).
    rel_filter: edge types — "IMPL", "NAND", or "IMPL|NAND" (default).
    direction: IMPL traversal — "incoming", "outgoing", or "both" (default).
    Returns {"answer": "...", "raw": [...], "pattern": "...", "query": "..."}
    """
    from tortoise.analyze import analyze
    from tortoise.navigation import entityProfile

    anchor_ids = _parse(anchor_ids)

    entity_subgraph_ids = None
    if entityId:
        try:
            proj = _get_team_sdk()._get_proj()
            # #236: HTTP mode must use the team graph, NOT the hardcoded
            # "tortoise" graph — that hardcode bypasses team isolation via
            # db.select_graph() (cross-tenant read). Stdio keeps "tortoise".
            gname = f"team_{_current_team_id.get()}" if _transport_mode.get() == "http" else "tortoise"
            profile = entityProfile(proj.db, gname, entityId, hops=2)
            ids = {entityId}
            for category in profile.get("connected", {}).values():
                for node in category:
                    if node.get("id"):
                        ids.add(node["id"])
            entity_subgraph_ids = ids
        except Exception:
            pass  # fall back to full-graph analysis

    # #329: bound paid outbound LLM calls per team per minute — beyond budget
    # the tool degrades to keyword-only classification.
    use_llm = _analyze_llm_budget_available()
    result = _safe(analyze, question, _get_team_sdk()._get_proj(),
                   entity_subgraph_ids=entity_subgraph_ids,
                   anchor_ids=anchor_ids,
                   max_hops=max_hops,
                   rel_filter=rel_filter,
                   direction=direction,
                   use_llm=use_llm)
    # #329 defense-in-depth: analyze() self-redacts, but scrub the answer at the
    # boundary too in case a future error path leaks internals.
    if isinstance(result, dict) and isinstance(result.get("answer"), str):
        result["answer"] = _scrub_analyze_answer(result["answer"])
    return result


# ── P1-3: Staleness Detection ─────────────────────────────────

def tortoise_stale(days: int = 30, limit: int = 50) -> dict:
    """Find Points not updated in N days. Returns {stale, count, cutoff, limit}.
    Alias → overview(section='stale', days=, limit=) (epic #888 W3)."""
    return _safe(_get_team_sdk().stale_points, days=days, limit=limit)


def tortoise_review_connections(mode: str = "both", scope: str | None = None) -> dict:
    """Review graph connections (READ-ONLY) — the hygiene counterpart to connect.

    mode=add: surface related-but-MISSING connections as suggestions
        {from, to, suggested_relation, reason, similarity} — nudge, don't
        enforce (the agent decides, then acts via operator_action/create_edge).
    mode=prune: flag illogical/stale IMPL/NAND connections
        {from, to, relation, issue, suggested_action, detail} with
        issue in (contradictory, stale, contested) and suggested_action in
        (review, prune, re-point).
    mode=both: run both, return {add: [...], prune: [...]}.
    scope: optional topic text or Point id — narrows the candidate pool.

    Never mutates the graph.
    """
    return _safe(_get_team_sdk().review_connections, mode=mode, scope=scope)


def tortoise_provenance(point_id: str) -> dict:
    """Provenance chain — "Who decided this?" Follows authoredBy → Subject → delegation."""
    return _safe(_get_team_sdk().provenance, point_id)


# ── Multi-tenancy (#7001) ────────────────────────────────────

def tortoise_team_create(name: str) -> dict:
    """Create isolated team graph via FalkorDB select_graph.
    Generates a per-team API key. Returns {name, graph_name, api_key, id}.
    destructiveHint=true — creates persistent resources.
    idempotentHint=false — duplicate team names raise an error.

    #236: EXCLUDED from tenant HTTP — provisioning belongs to
    /internal/provision behind FASTAPI_INTERNAL_KEY (privilege boundary).
    Stdio-only.
    """
    if _transport_mode.get() == "http":
        return _http_excluded_error()
    return _safe(_get_team_sdk().team_create, name)


# ── Entity CRUD (ONTOLOGY v2.5) ───────────────────────────────

# ── Write/revise consolidation (epic #888 W2) ──────────────────────

def tortoise_create_entity(type: str, name: str, props: Any = None) -> dict:
    """Create an entity — type: subject|object|event|document.

    Event entities wire about* edges from aboutSubject/aboutObject/aboutPoint/
    aboutDocument props. Returns {node, nudges} — nudges suggest IMPL/NAND/
    mitigate connections to related Points (advisory, not enforced).
    """
    props = _parse(props)
    return _safe(_quota_gated(_get_team_sdk().create_entity, "points"),
                 type, name, **(props or {}))


def tortoise_update(id: str, props: Any = None) -> dict:
    """Update a Point OR entity by id. Points get point-lifecycle semantics
    (draft→live promote via status, version increment for Point:Object,
    status validation); entities get a plain property update."""
    props = _parse(props)
    return _safe(_quota_gated(_get_team_sdk().update, "points"),
                 id, **(props or {}))


def tortoise_delete(id: str) -> dict:
    """Delete a Point or entity by id. DESTRUCTIVE — requires human confirmation."""
    result = _safe(_get_team_sdk().delete, id)
    if isinstance(result, dict) and "error" in result:
        return result
    return {"deleted": bool(result), "id": id}


def tortoise_operator_action(action: str, id: str, reason: str | None = None,
                             strength: float = 0.5,
                             bias: float | None = None,
                             precision: float | None = None,
                             consistency: float | None = None,
                             directness: float | None = None) -> dict:
    """Consolidated operator write action — action=mitigate|annotate.

    mitigate: reason (required) + strength (0-1, default 0.5) — creates/updates
    the mitigation Point (idempotent). annotate: bias/precision/consistency/
    directness (all required, 0-1).
    """
    if action == "mitigate":
        if not reason:
            return {"error": "operator_action(action='mitigate') requires 'reason'"}
        return _safe(_quota_gated(_get_team_sdk().mitigate_operator, "points", abuse_weight=1),
                     id, reason, strength)
    if action == "annotate":
        dims = (bias, precision, consistency, directness)
        if any(d is None for d in dims):
            return {"error": "operator_action(action='annotate') requires "
                              "bias, precision, consistency, directness"}
        return _safe(_quota_gated(_get_team_sdk().annotate_operator, "points"),
                     id, *dims)
    return {"error": f"operator_action: unknown action {action!r} — "
                      f"must be 'mitigate' or 'annotate'"}


def tortoise_create_subject(name: str, subjectKind: str, props: Any = None) -> dict:
    """Create a Subject node (team, role, organization, person)."""
    props = _parse(props)
    return _safe(_quota_gated(_get_team_sdk().create_subject, "points"), name, subjectKind, **(props or {}))

def tortoise_create_object(name: str, objectKind: str, props: Any = None) -> dict:
    """Create an Object node (product, customer, skill, etc.)."""
    props = _parse(props)
    return _safe(_quota_gated(_get_team_sdk().create_object, "points"), name, objectKind, **(props or {}))

def tortoise_create_event(name: str, eventKind: str, props: Any = None) -> dict:
    """Create an Event node (meeting, decision, deployment, etc.)."""
    props = _parse(props)
    return _safe(_quota_gated(_get_team_sdk().create_event, "points"), name, eventKind, **(props or {}))


def tortoise_get_events(eventKind: str | None = None, limit: int = 20) -> list[dict]:
    """Get recent Events, optionally filtered by eventKind (e.g. 'AgentSession').
    Alias → get(id, type='events', limit=) (epic #888 W3)."""
    return _safe(_get_team_sdk().get_events, eventKind=eventKind, limit=limit)

def tortoise_get_session(session_id: str) -> dict:
    """Get a single agent session Event by session_id.
    Alias → get(id, type='session') (epic #888 W3)."""
    return _safe(_get_team_sdk().get_session, session_id)

def tortoise_index_sessions(directory: str, extract_metadata: bool = True, llm_model: str | None = None) -> dict:
    """Index session .md files as AgentSession Events. Returns {ingested, updated, skipped, failed, errors}.

    #236: EXCLUDED from tenant HTTP — walks server filesystem with a
    user-supplied path (path-traversal vector, same as ingest_corpus). Stdio-only.
    """
    if _transport_mode.get() == "http":
        return _http_excluded_error()
    if not os.path.isdir(directory):
        return {"error": f"Directory not found: {directory!r}. Provide a valid path to a directory containing .md session files."}
    return _safe(_get_team_sdk().index_sessions, directory, extract_metadata=extract_metadata, llm_model=llm_model)

def tortoise_search_sessions(query: str, agent: str | None = None, topics: Any = None,
                             after: str | None = None, before: str | None = None,
                             limit: int = 10, offset: int = 0) -> list[dict]:
    """Search indexed agent sessions. Returns Events with narrative_arc snippets.

    after/before bound the search to sessions whose startedAt falls in
    [after, before] (inclusive). Accept ISO-8601 strings (e.g.
    '2026-07-01T00:00:00Z' or '2026-07-31T23:59:59+00:00'); values are
    normalized to UTC. Sessions without startedAt are excluded when a bound
    is set.
    """
    topics = _parse(topics)
    if isinstance(topics, str):
        topics_list = [t.strip() for t in topics.split(",") if t.strip()]
    elif isinstance(topics, list):
        topics_list = topics
    else:
        topics_list = None
    return _safe(_get_team_sdk().search_sessions, query, agent=agent, topics=topics_list,
                 after=after, before=before, limit=limit, offset=offset)

def tortoise_create_document(title: str, documentKind: str, props: Any = None) -> dict:
    """Create a Document node (research, planDoc, meetingNotes, etc.)."""
    props = _parse(props)
    return _safe(_quota_gated(_get_team_sdk().create_document, "points"), title, documentKind, **(props or {}))

@mcp.tool(annotations=ToolAnnotations(idempotentHint=True))
def tortoise_create_source(url: str, sourceKind: str, tier: str | None = None,
                           sourceDate: str | None = None, props: Any = None) -> dict:
    """Create a Source node for provenance (document, web, db, etc.).

    Sources track content origin — url is the permalink key. Points link to
    Sources via extractedFrom edge (Ontology v2.5). ``tier`` (T0-T4) stores the
    credibility tier on ``credibilityTier`` (dual-write with tier-form
    sourceKind); ``sourceDate`` is the evidence-age clock for recency decay.
    """
    props = _parse(props) or {}
    # tier/sourceDate are first-class kwargs (#398) — pop from props if a legacy
    # caller passed them there (kwarg wins; avoids TypeError on splat).
    props.pop("tier", None)
    props.pop("sourceDate", None)
    return _safe(_quota_gated(_get_team_sdk().create_source, "points"), url, sourceKind,
                 tier=tier, sourceDate=sourceDate, **props)


@mcp.tool()
def tortoise_get_source_reliability(url: str) -> dict:
    """Derive a Source's reliability (0-1) — query-time, cache-consistency-checked.

    Reliability is the mean of the same modulated prior EP uses as base weight
    (tier + recency decay + reputation-weighted agent assessments). Untiered +
    unassessed → None. NOTE: refreshes the documented reliability cache on the
    Source node (write-through projection), so this tool is not read-only.
    """
    return _safe(_get_team_sdk().get_source_reliability, url)


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def tortoise_assess_source(url: str, assessor: str, score: float,
                           rationale: str) -> dict:
    """Record an agent's assessment of a Source (0-1 score + rationale).

    Creates a pointKind='assessment' Statement Point (ontology §2 — evaluations
    are Points, not edges). Latest assessment per (url, assessor) wins; older
    are marked outdated. Weighted by the assessor's reputation snapshot
    (compute_reputation at write time). Feeds the source's reliability factor
    (clamped [0.1, 2.0]).
    """
    return _safe(_quota_gated(_get_team_sdk().assess_source, "points"),
                 url, assessor, score, rationale)


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def tortoise_set_source_tier(url: str, tier: str) -> dict:
    """Set (or change) a Source's credibility tier (T0-T4). Non-destructive.

    Writes credibilityTier only — never overwrites sourceKind type strings.
    Dirty-marks the inheritance gate + clears the reliability cache so EP and
    reliability reads reflect the new tier promptly.
    """
    return _safe(_get_team_sdk().set_source_tier, url, tier)

def tortoise_get_entity(id: str) -> dict:
    """Get any entity by ID, eventId, or url.
    Alias → get(id, type='entity') (epic #888 W3)."""
    return _safe(_get_team_sdk().get_entity, id)

def tortoise_update_entity(id: str, props: Any = None) -> dict:
    """Update any entity's properties."""
    props = _parse(props)
    return _safe(_quota_gated(_get_team_sdk().update_entity, "points"), id, **(props or {}))

def tortoise_delete_entity(id: str) -> bool:
    """Delete any entity by ID."""
    return _safe(_get_team_sdk().delete_entity, id)

def tortoise_create_edge(source_id: str, target_id: str, predicate: str) -> dict:
    """Create a typed structural edge between two entities (reification rule).

    Relation (predicate): performs, produces, uses, memberOf, ownedBy, managedBy,
    about*, related, dependsOn, etc. Operator-less by default — lazy promotion
    via operator_action(action='mitigate') when mitigation becomes needed.
    Returns {edge, created, nudges}. (Param order kept from the legacy surface:
    source_id, target_id, predicate → SDK create_edge(relation, from_id, to_id).)
    """
    return _safe(_quota_gated(_get_team_sdk().create_edge, "points"),
                 predicate, source_id, target_id)


def tortoise_ingest(bundle: Any = None, granularity: str = "bulk",
                    promotion_policy: str = "gated") -> dict:
    """Heterogeneous bulk write (epic #888 W4) — one call writes points +
    entities + sources + connections coherently. Nodes are written first, then
    the connections between them; connections carrying 'operator' (IMPL/NAND)
    create operator Points (reification rule v3.5 §8), connections carrying
    'relation' stay plain structural edges. Local refs address bundle items.

    granularity='bulk' (default): aggregated {created, ids, nudges}.
    granularity='granular': per-item results for agent step-by-step control.
    promotion_policy='gated' (DEFAULT, Q2): points stay draft, connections
    never promote (operator path: promote_source=False via #780). Per-item
    status:'live' is REJECTED under gated (INGEST_CONTRACT row 9 — no bypass
    of the gated contract; use promotion_policy='auto' or promote after
    ingest via the SDK's update_point(status='live')).
    promotion_policy='auto': #131 parity — source points promote on wire
    (only draft/null-status sources; retracted/deprecated are never
    resurrected); the operator node is written without a status property
    (live by projection — the #780 asymmetry). Deduped connections never
    retro-promote (promotion fires on FIRST edge creation only).
    Idempotent-ish: points dedup by content hash + kind, sources by url,
    operators by input set.
    """
    bundle = _parse(bundle)
    if bundle is None:
        bundle = {}
    if not isinstance(bundle, dict):
        return {"error": "bundle must be a dict with points/entities/sources/"
                          "connections sections", "code": ERR_INVALID}
    if granularity not in INGEST_GRANULARITIES:
        return {"error": f"granularity must be 'bulk' or 'granular', got "
                          f"{granularity!r}", "code": ERR_INVALID}
    if promotion_policy not in INGEST_PROMOTION_POLICIES:
        return {"error": f"promotion_policy must be 'gated' or 'auto', got "
                          f"{promotion_policy!r}", "code": ERR_INVALID}
    # Row-9 guard (SDK-side, mirrors ingest()): under gated, an explicit
    # status:'live' on a point item is a violation. Handled here pre-SDK so
    # MCP clients get the structured ERR_INVALID shape, not a generic error.
    if promotion_policy == "gated":
        for i, item in enumerate(bundle.get("points") or []):
            if isinstance(item, dict) and item.get("status") == "live":
                return {"error": f"points[{i}] status:'live' is not allowed under "
                                  f"promotion_policy 'gated' — pass "
                                  f"promotion_policy='auto' for explicit live, or "
                                  f"keep draft and promote via "
                                  f"update_point(status='live')",
                        "code": ERR_INVALID}
    return _safe(_quota_gated(_get_team_sdk().ingest, "points",
                          abuse_weight=lambda r, a, k: int(((r or {}).get("created") or {}).get("points") or 0)),
                 bundle, granularity=granularity, promotion_policy=promotion_policy)

def tortoise_get_governance(subject_id: str) -> list:
    """Get all entities owned by a Subject.
    Alias → get(id, type='governance') (epic #888 W3)."""
    return _safe(_get_team_sdk().get_owned_entities, subject_id)


# ── Orient / Direct consolidation (epic #888 W3) ─────────────────────
# PR #912 design: the list_* zoo + status/health/taxonomy/structure fold
# into ONE overview(section=) tool; the get_* zoo folds into ONE
# get(id, type=) tool with id auto-detection. The old tools below remain
# registered as thin aliases for one release (identical shapes).

_OVERVIEW_SECTIONS = (
    "taxonomy", "structure", "structure_check", "pointkinds", "tags",
    "sources", "namespaces", "graphs", "topics", "health", "status",
    "stale",
)


def _overview_section(section: str, entity_id: str | None,
                      days: int, limit: int) -> Any:
    """Dispatch one overview section to its original tool body."""
    if section == "taxonomy":
        return tortoise_taxonomy()
    if section == "structure":
        return tortoise_summarize_structure()
    if section == "structure_check":
        return tortoise_check_structure()
    if section == "pointkinds":
        return tortoise_list_pointkinds()
    if section == "tags":
        return tortoise_list_tags()
    if section == "sources":
        return tortoise_list_sources()
    if section == "namespaces":
        return tortoise_list_namespaces()
    if section == "graphs":
        return tortoise_list_graphs()
    if section == "topics":
        if not entity_id:
            return {"error": "overview(section='topics') requires entity_id"}
        return tortoise_list_topics(entity_id)
    if section == "health":
        return tortoise_health()
    if section == "status":
        return tortoise_status()
    if section == "stale":
        return tortoise_stale(days=days, limit=limit)
    return {"error": f"overview: unknown section {section!r}. "
                      f"Valid sections: {', '.join(_OVERVIEW_SECTIONS)}"}


def tortoise_overview(section: str | None = None,
                      entity_id: str | None = None,
                      days: int = 30,
                      limit: int = 50) -> Any:
    """Graph orientation in one call — consolidates the list_*/status/health/
    taxonomy/structure zoo (epic #888 W3, PR #912).

    section selects one orient surface:
      taxonomy | structure | structure_check | pointkinds | tags | sources |
      namespaces | graphs | topics | health | status | stale
    Each section returns exactly what the legacy tool returned.

    Omit section → compact combined summary: {section: result} for every
    section except topics (which requires entity_id).

    topics: entityProfile lite for an entity — requires entity_id.
    stale: Points not updated in N days — honors days/limit.
    """
    if section is None:
        combined: dict[str, Any] = {}
        for sec in _OVERVIEW_SECTIONS:
            if sec == "topics":
                continue  # requires entity_id — not part of the default summary
            combined[sec] = _overview_section(sec, entity_id, days, limit)
        return combined
    if not isinstance(section, str):
        return {"error": f"overview: section must be a string, got {type(section).__name__}"}
    return _overview_section(section.strip().lower(), entity_id, days, limit)


_GET_TYPES = ("point", "operator", "entity", "event", "session",
              "events", "governance")


def _get_auto_detect(id: str) -> dict:
    """Resolve a node id to its properties without a type hint.

    Order: canonical entity resolution (id | eventId | url, Point priority)
    then AgentSession lookup by session_id/sessionId. Returns {} when the
    id matches nothing (same contract as get_point/get_entity).
    """
    sdk = _get_team_sdk()
    try:
        resolved = sdk._get_proj()._resolve_entity(
            id, by_id=True, by_eventId=True, by_url=True)
    except Exception:
        resolved = []
    if resolved:
        return dict(resolved[0]["properties"])
    session = _safe(sdk.get_session, id)
    if isinstance(session, dict) and session:
        return session
    return {}


def tortoise_get(id: str, type: str | None = None,
                 limit: int = 20) -> Any:
    """Fetch a node by id — consolidates get_point/get_entity/get_operator/
    get_events/get_session/get_governance (epic #888 W3, PR #912).

    type selects the node kind:
      point | operator | entity | event | session | events | governance
    Omitted type → auto-detect by id lookup (Point/Subject/Object/Document/
    Source/Event by id|eventId|url; AgentSession by session_id|sessionId).

    Returns the node properties (same shape as the legacy tool it replaces).
    type='events': id is optional — recent Events list, id used as an
        eventKind filter when given (get_events(eventKind=id, limit=limit)).
    type='governance': entities owned by the Subject id.
    """
    if not isinstance(type, str) or not type.strip():
        if not isinstance(id, str) or not id.strip():
            return {"error": "get: 'id' is required when type is omitted"}
        return _get_auto_detect(id.strip())
    t = type.strip().lower()
    if t == "events":
        # get_events is a list surface — id is an optional eventKind filter
        return tortoise_get_events(eventKind=id or None, limit=limit)
    if not isinstance(id, str) or not id.strip():
        return {"error": f"get: 'id' is required for type={t!r}"}
    id = id.strip()
    if t == "point":
        return tortoise_get_point(id)
    if t == "operator":
        return tortoise_get_operator(id)
    if t == "entity":
        return tortoise_get_entity(id)
    if t == "event":
        return tortoise_get_entity(id)  # Event nodes resolve via get_entity
    if t == "session":
        return _safe(_get_team_sdk().get_session, id)
    if t == "governance":
        return tortoise_get_governance(id)
    return {"error": f"get: unknown type {type!r}. "
                      f"Valid types: {', '.join(_GET_TYPES)}"}


def tortoise_backfill_v25(dry_run: bool = True) -> dict:
    """Backfill database to ONTOLOGY v2.5 schema.

    #236: EXCLUDED from tenant HTTP — schema-level migration (operator-only).
    """
    if _transport_mode.get() == "http":
        return _http_excluded_error()
    return _safe(_get_team_sdk().backfill_v25, dry_run=dry_run)


# ── Phase-4 mining/promotion/dedup/timeline surface (#787, DE2E-7) ──
# J-6 error contract: every tool returns the SDK result on success or
# {"error": message} on failure (via _safe — the repo-wide convention).


def tortoise_mine_conversations(transcript: str | None = None,
                                corpus_dir: str | None = None,
                                source_id: str | None = None,
                                extract_entities: bool = True,
                                content_dedup: bool = True,
                                session_date: str | None = None,
                                participants: Any = None) -> dict:
    """Mine agent conversations into the graph (W-1..W-4, #787).

    Single transcript (transcript= + source_id=) or a batch corpus
    (corpus_dir= — per-file failures reported non-fatally in 'errors',
    mined-marker resume, R17 security validation). Returns the mine result
    incl. batch_id/batch_status (W-3 gate), dedup_* and temporal_* keys.
    """
    sdk = _get_team_sdk()
    if corpus_dir is not None:
        return _safe(sdk.mine_corpus, corpus_dir,
                     extract_entities=extract_entities)
    if not transcript or not source_id:
        return {"error": "transcript= and source_id= are required "
                         "(or corpus_dir= for a batch)"}
    from tortoise.api import EventAPI
    from tortoise.log import EventLog
    from tortoise.mining import mine_conversation
    import tempfile, os
    log = sdk._get_event_log()
    if log is None:
        log = EventLog(os.path.join(
            tempfile.mkdtemp(prefix="tortoise_mcp_mine_"), "events.jsonl"))
    api = EventAPI(log, initiated_by="extractor", agent_id="mcp",
                   projection=sdk._get_proj())
    return _safe(mine_conversation, transcript, source_id, api,
                 extract_entities=extract_entities,
                 content_dedup=content_dedup,
                 session_date=session_date,
                 sdk=sdk)


def tortoise_list_dedup_candidates(candidate_type: str = "content",
                                   limit: int = 50) -> dict:
    """Review queue for dedup/temporal candidates (W-2/W-4, #787).

    candidate_type='content' → content-dedup candidates; 'temporal' →
    contradictory/replacement decision Points pending promotion wiring;
    'entity' → [] (entity queue tracked separately). Each entry carries
    {id, content, pointKind, method/similarity (content) or replacement
    (temporal), target_id, candidate_type, status}.
    """
    return _safe(_get_team_sdk().list_dedup_candidates,
                 candidate_type=candidate_type, limit=limit)


def tortoise_approve_merge(candidate_id: str,
                           action: str = "merge") -> dict:
    """Review a dedup/temporal candidate (W-2/W-4, #787).

    action='merge' → content: wire the alreadyDecided IMPL (draft prior) or
    defer to promotion (live prior); temporal: defer the NAND/supersede to
    promotion. action='reject' → the candidate stays separate and is no
    longer surfaced. Idempotent for repeated identical reviews.
    """
    return _safe(_get_team_sdk().approve_merge, candidate_id, action=action)


def tortoise_promote_point(point_id: str) -> dict:
    """Reviewer-gated draft→live promotion (Phase-4, #785/#787).

    The ONLY path a draft extraction Point may go live: blocks on
    quarantined batches {blocked, reason, batch_id}, rejects operator
    nodes, no-ops on already-live (DE2E-N9), promotes incident draft
    operators once all endpoints are live (R16), and wires deferred
    dedup/temporal links (Variant C / W-4).
    """
    return _safe(_get_team_sdk().promote_point, point_id)


def tortoise_belief_timeline(topic: str, limit: int = 50) -> dict:
    """Dated, ordered belief chain for a topic (J-4, #786/#787).

    Decision Points aboutObject-connected to the topic entity, validFrom-
    ordered (superseded priors kept visible via the CORRECTS chain), each
    with {content, pointKind, validFrom, status, linked_by, related}.
    """
    return _safe(_get_team_sdk().belief_timeline, topic, limit=limit)


# ── Tool Registry Adapter (#454) ────────────────────────────────
# Replaces @mcp.tool() decorators with programmatic registration.
# Function bodies remain module-level callables; the adapter wraps each
# via FunctionTool.from_function() and registers them on the shared mcp.
# Must execute AFTER all tool function definitions (at module bottom).
from tortoise.tool_registry import TOOL_REGISTRY, GROUP_BY_NAME, FastMCPAdapter

_adapter = FastMCPAdapter(mcp)
_adapter.register_all(TOOL_REGISTRY, {
    t.name: globals()[t.name]
    for t in TOOL_REGISTRY
    if t.name in globals()
})



# ── Onboarding MCP tools (#498/#499/#500) ───────────────────────
# Wrappers for the hosted onboarding flow. These call the team-scoped SDK
# directly (same pattern as all tools) — the REST endpoints in hosted_api.py
# expose the same operations to the welcome page.

# Epic #888 no-regret: once a team's onboarding completes, the six
# tortoise_onboarding_* tools retire from that team's steady-state MCP
# surface (tools/list) — the REST /v1/onboarding/* endpoints remain for the
# web onboarding flow. Definitions and handlers are untouched; only the
# listing hides them. See _HTTPToolFilter.list_tools.
_ONBOARDING_TOOL_NAMES: frozenset[str] = frozenset({
    "tortoise_onboarding_demo_create", "tortoise_onboarding_state",
    "tortoise_onboarding_session_recording", "tortoise_onboarding_github_connect",
    "tortoise_onboarding_github_index", "tortoise_onboarding_github_status",
})

# 60s per-team TTL cache for the tools/list gate — the onboarding-state read
# hits the control plane (Supabase teams row / registry Team node); tools/list
# is called once per session but bounding the read to 1/min/team avoids any
# amplification (review fix, Epic #888). Staleness is fine: the gate is
# surface cosmetics and already fails open.
_onboarding_state_cache: dict[str, tuple[float, bool]] = {}
_ONBOARDING_STATE_TTL = 60.0


def _team_onboarding_complete() -> bool:
    """True when the current HTTP team's onboarding is complete.

    Fail-open: stdio/selfhost (no tenant Team row) and transient control-plane
    read failures return False — a read hiccup must never hide the tools a
    team still needs to finish onboarding. Reads the canonical onboarding
    state via hosted_api._get_onboarding_state (Supabase teams row or registry
    Team node), cached 60s per team.
    """
    from tortoise.mcp_auth import SELFHOST_TEAM_ID, _current_team_id
    team_id = _current_team_id.get()
    if not team_id or team_id == SELFHOST_TEAM_ID:
        return False
    now = _time.time()
    cached = _onboarding_state_cache.get(team_id)
    if cached is not None and now - cached[0] < _ONBOARDING_STATE_TTL:
        return cached[1]
    try:
        from tortoise.hosted_api import _get_onboarding_state
        complete = bool(_get_onboarding_state(team_id).get("onboarding_complete"))
    except Exception:
        return False  # never cache a failed read — retry next list
    _onboarding_state_cache[team_id] = (now, complete)
    return complete


def _onboarding_state() -> dict:
    """Read this team's onboarding progress from the registry Team node."""
    from tortoise.hosted_api import _get_onboarding_state as _read_state
    team_id = _current_team_id.get()
    if team_id is None:
        return {"error": "No team context (HTTP mode required)"}
    return _read_state(team_id)


@mcp.tool(annotations=ToolAnnotations(idempotentHint=True))
def tortoise_onboarding_demo_create() -> dict:
    """Create the demo epistemic graph (4 layers) for this team. Idempotent.

    Q4 — 'Create a demo graph?' — shows what Tortoise memory looks like.
    """
    from tortoise.hosted_api import _seed_demo_graph
    team_id = _current_team_id.get()
    if team_id is None:
        return {"error": "No team context (HTTP mode required)"}
    # #329: demo graph creation creates nodes — quota-gate it
    _enforce_quota("points")
    result = _seed_demo_graph(team_id)
    # Auto-update onboarding state
    try:
        from tortoise.hosted_api import _update_onboarding_state
        _update_onboarding_state(team_id, demo_created=True)
    except Exception:
        pass
    return result


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def tortoise_onboarding_state() -> dict:
    """Return this team's onboarding progress (Q6 verification step)."""
    return _onboarding_state()


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def tortoise_onboarding_session_recording(enabled: bool) -> dict:
    """Toggle automatic session recording for this team (Q3)."""
    team_id = _current_team_id.get()
    if team_id is None:
        return {"error": "No team context (HTTP mode required)"}
    from tortoise.hosted_api import _update_onboarding_state
    state = _update_onboarding_state(team_id, session_recording=enabled)
    return {"onboarding": state}


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def tortoise_onboarding_github_connect(org: str | None = None) -> dict:
    """Initiate GitHub OAuth — returns the authorize URL + CSRF state (Q1)."""
    team_id = _current_team_id.get()
    if team_id is None:
        return {"error": "No team context (HTTP mode required)"}
    import secrets
    from urllib.parse import urlencode
    import os as _os
    client_id = _os.environ.get("GITHUB_CLIENT_ID")
    if not client_id:
        return {"error": "GitHub OAuth not configured"}
    state = secrets.token_urlsafe(24)
    # Store CSRF state so the callback can validate it (P2 review fix) —
    # must be visible to the REST callback handler in the same process.
    import time as _time
    from tortoise.hosted_api import _GITHUB_STATES
    _GITHUB_STATES[state] = {"team_id": team_id, "org": org or team_id,
                             "created_at": _time.time()}
    callback = _os.environ.get("GITHUB_CALLBACK_URL",
                               "https://api.premiselabs.co/v1/onboarding/github/callback")
    params = {"client_id": client_id, "redirect_uri": callback,
              "scope": "repo", "state": state}
    auth_url = f"https://github.com/login/oauth/authorize?{urlencode(params)}"
    return {"auth_url": auth_url, "state": state}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def tortoise_onboarding_github_status() -> dict:
    """Return GitHub connection status for this team (Q1 verify)."""
    team_id = _current_team_id.get()
    if team_id is None:
        return {"error": "No team context (HTTP mode required)"}
    # Follows the hosted seam (plan Task 6): Supabase teams row via the
    # service-role control plane in Supabase mode, registry for selfhost.
    from tortoise.hosted_api import _github_credentials
    try:
        enc, org = _github_credentials(team_id)
    except RuntimeError:
        # Fail-closed: a control-plane outage is an ERROR, not "disconnected"
        # — reporting connected=False would make the user think GitHub got
        # disconnected. Name the actual plane (registry vs Supabase) so
        # selfhost operators aren't misled (code-review P2, PR #861).
        from tortoise.supabase_control import is_supabase_enabled
        plane = "Supabase control plane" if is_supabase_enabled() else "registry"
        return {"error": f"{plane} unavailable"}
    except Exception:
        return {"connected": False, "org": None, "repos_count": None}
    if not enc:
        return {"connected": False, "org": None, "repos_count": None}
    return {"connected": True, "org": org, "repos_count": None}


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def tortoise_onboarding_github_index(org: str, repo: str | None = None) -> dict:
    """Start background GitHub indexing of an org's issues/PRs (Q2).

    Returns {job_id, status} — poll via the REST endpoint or check
    onboarding state for github_indexed.
    """
    team_id = _current_team_id.get()
    if team_id is None:
        return {"error": "No team context (HTTP mode required)"}
    import secrets as _secrets
    import asyncio as _asyncio
    from tortoise.hosted_api import _INDEX_JOBS, _github_token_enc, _run_indexing
    try:
        encrypted = _github_token_enc(team_id)
    except Exception:
        # Name the actual plane (registry vs Supabase) so selfhost operators
        # aren't misled (code-review P2, PR #861).
        from tortoise.supabase_control import is_supabase_enabled
        plane = "Supabase control plane" if is_supabase_enabled() else "registry"
        return {"error": f"{plane} unavailable"}
    if not encrypted:
        return {"error": "GitHub not connected. Run tortoise_onboarding_github_connect first."}
    job_id = _secrets.token_hex(8)
    _INDEX_JOBS[job_id] = {"status": "started", "progress": 0,
                           "points_created": 0, "error": None,
                           "team_id": team_id,
                           "created_at": _asyncio.get_event_loop().time()}
    try:
        _asyncio.get_event_loop().create_task(
            _run_indexing(job_id, team_id, org, repo))
    except RuntimeError:
        return {"error": "No running event loop"}
    return {"job_id": job_id, "status": "started"}


# ── HTTP Streamable transport (#236) ─────────────────────────────

def create_http_app(*, allowed_origins: list[str] | None = None,
                    allowed_hosts: list[str] | None = None,
                    rate_limit: int = 100,
                    _registry_sdk=None,
                    auth_mode: Literal["tenant", "static", "none"] = "tenant",
                    api_key: str | None = None,
                    tool_group: str | None = None) -> Any:
    """Configured Streamable HTTP app for the hosted platform (#236).

    Mounted at /mcp on the existing FastAPI app. Auth + rate limiting +
    security headers + body-size caps live INSIDE this app's middleware
    stack — the parent FastAPI app.mount() does NOT propagate its own
    middleware to mounted sub-apps (verified Starlette behavior).

    auth_mode (additive, default "tenant" = hosted byte-identical):
      "tenant" → TeamResolutionMiddleware (registry Bearer tt_ keys)
      "static" → StaticKeyMiddleware (single TORTOISE_API_KEY, self-host LAN)
      "none"   → no auth middleware (localhost-bound self-host eval)

    tool_group: optional curation-group filter (#523) — role-scoped server
      (e.g. "memory" exposes only memory tools to the agent).

    path="/": the app is mounted at /mcp on the parent FastAPI app, which
    strips the mount prefix before dispatching to this sub-app — so routes
    must live at / (parent /mcp → sub-app /). The GET /mcp metadata route
    is registered on the shared module-level mcp instance — safe for stdio
    (route unused) and coexists with the POST/DELETE streamable-http route.
    """
    from starlette.middleware import Middleware
    from starlette.responses import JSONResponse
    from tortoise.mcp_auth import (MCPRateLimitMiddleware,
                                   SecurityHeadersMiddleware,
                                   RequestBodySizeMiddleware)
    from fastmcp.server.transforms import Transform

    # auth_mode middleware selection. TeamResolutionMiddleware (tenant mode) is
    # imported here but only ever INSTANTIATED in the tenant branch — static/none
    # modes never construct it, and hosted_api is only ever lazily imported when
    # a tenant token is verified (mcp_auth delegates via function-level import).
    auth_mw = None
    transport_mw = None
    group_mw = None
    if tool_group:
        from tortoise.mcp_auth import ToolGroupMiddleware
        group_mw = Middleware(ToolGroupMiddleware, tool_group=tool_group)
    if auth_mode == "tenant":
        from tortoise.mcp_auth import TeamResolutionMiddleware
        auth_mw = Middleware(TeamResolutionMiddleware, registry_sdk=_registry_sdk)
    elif auth_mode == "static":
        from tortoise.mcp_auth import StaticKeyMiddleware
        auth_mw = Middleware(StaticKeyMiddleware, api_key=api_key)
        from tortoise.mcp_auth import TransportModeMiddleware
        transport_mw = Middleware(TransportModeMiddleware)
    elif auth_mode == "none":
        from tortoise.mcp_auth import TransportModeMiddleware
        transport_mw = Middleware(TransportModeMiddleware)

    class _HTTPToolFilter(Transform):
        """Hide HTTP-excluded tools from tools/list (D4) + optional curation
        group scoping (#523).

        The excluded tools (team_create/backfill_v25/ingest_corpus) remain
        registered on the shared module-level mcp instance for stdio, but are
        filtered out of the HTTP tool listing so tenants can't discover them.
        When tool_group is set, only that group's tools are listed — role-
        scoped servers keep the agent's tool-selection surface under ~20.
        """
        async def list_tools(self, tools):
            from tortoise.mcp_auth import _tool_group
            group = _tool_group.get()
            # Skip the control-plane read when it can't change the outcome: in
            # a curation-group-scoped app (other than "onboarding") the group
            # filter below already excludes the onboarding tools.
            onboarding_done = False
            if not (group and group != "onboarding"):
                onboarding_done = _team_onboarding_complete()

            def _visible(t):
                if t.name not in HTTP_ALLOWED:
                    return False
                if group and GROUP_BY_NAME.get(t.name) != group:
                    return False
                # Epic #888: onboarding tools retire from the steady-state
                # surface once this team's onboarding is complete (fail-open
                # — state read errors keep them visible).
                if onboarding_done and t.name in _ONBOARDING_TOOL_NAMES:
                    return False
                return True

            return [t for t in tools if _visible(t)]

    # Guard against transform accumulation: create_http_app() is called at
    # hosted_api import AND in every test fixture — each call would append a
    # new _HTTPToolFilter to the shared module-level mcp instance (code-review
    # P2 fix). Register once.
    if not getattr(mcp, "_http_tool_filter_registered", False):
        mcp.add_transform(_HTTPToolFilter())
        mcp._http_tool_filter_registered = True

    @mcp.custom_route("/", methods=["GET"])
    async def mcp_metadata(request):
        # Epic #529 E2E (T8): real Streamable HTTP clients (MCP TS SDK —
        # pi mcp-client v1.29.0 observed) open a GET listener that expects
        # an SSE stream. Returning the JSON self-test there fails their
        # JSON-RPC parse and aborts the whole connection. Per the
        # Streamable HTTP spec, a server that offers no SSE stream answers
        # GET with 405 — SDKs handle that gracefully. Non-SSE GETs (curl,
        # browsers, the self-test probe) keep the JSON metadata response.
        if "text/event-stream" in request.headers.get("accept", ""):
            return JSONResponse(
                {"error": "no SSE stream offered; use POST for JSON-RPC"},
                status_code=405)
        return JSONResponse({"status": "ok", "protocol": "mcp",
                             "transport": "streamable-http",
                             "endpoint": "/mcp"})

    middleware = [
        Middleware(SecurityHeadersMiddleware),
        Middleware(RequestBodySizeMiddleware),
    ]
    if auth_mw is not None and auth_mode == "tenant":
        # Original position: tenant auth sits between body-size and rate-limit
        # (byte-identical to pre-auth_mode hosted stack).
        middleware.append(auth_mw)
    middleware.append(Middleware(MCPRateLimitMiddleware, max_per_minute=rate_limit))
    if auth_mw is not None and auth_mode != "tenant":
        # Static mode: rate limiter sits OUTSIDE auth so failed-key attempts are
        # throttled (code-review P1 — unlimited brute force on a user-chosen key).
        middleware.append(auth_mw)
    if transport_mw is not None:
        # Innermost — runs after auth validated, right before the app:
        # initializes the transport-mode ContextVars selfhost tools need.
        middleware.append(transport_mw)
    if group_mw is not None:
        # Sets the curation-group ContextVar for the tools/list transform.
        middleware.append(group_mw)

    return mcp.http_app(
        transport="streamable-http",
        stateless_http=True,
        host_origin_protection=True,
        allowed_origins=allowed_origins or [],
        allowed_hosts=allowed_hosts or [],
        path="/",
        middleware=middleware,
    )


# #993: the stdio entrypoint guard MUST run AFTER every @mcp.tool decorator
# and the FastMCPAdapter.register_all() call above. Placing it earlier (was
# line ~1211) made `python -m tortoise.mcp_server` enter mcp.run() with ZERO
# tools registered — onboarding's Step 0 (tortoise_health) failed with
# "Can't connect to Tortoise". Importing callers (tortoise serve via
# __main__.py, deployment.py) are unaffected: they import the module first
# (which executes register_all), then call main().
if __name__ == "__main__":
    main()
