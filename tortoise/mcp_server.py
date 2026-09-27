"""TORT-MCP-001: MCP server wrapping TortoiseSDK. Stdio transport, ~10 tools."""
from __future__ import annotations  # noqa: I001

import asyncio
import contextlib
import inspect
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
from fastmcp.tools import ToolResult
from pydantic import ValidationError as PydanticValidationError
from tortoise.auth import is_dev_mode as _is_dev_mode
from tortoise.config import is_db_uri as _is_db_uri
from tortoise.sdk import (TortoiseSDK, INGEST_GRANULARITIES,
                          INGEST_PROMOTION_POLICIES, _first_non_draft_status,
                          _RESERVED_ACTOR_PROPS, SUPERSEDE_STRUCTURAL_RELS)
from tortoise import monitoring
from tortoise.mcp_auth import (_current_org_id, _current_org_limits,
                               _current_scopes, _current_legacy_full_access,
                               _current_graph_id, _transport_mode, _tool_group,
                               _get_org_sdk, HTTP_ALLOWED, ERR_UNAUTHORIZED,
                               ERR_EXCLUDED, ERR_TIMEOUT, SELFHOST_ORG_ID)
# #3834: the transport wait-bound vocabulary is read as a MODULE attribute
# (``tortoise.mcp_auth`` is its single home), so the fast path pays no
# ``hosted_api`` import — that import is ~1.7 s and builds the whole hosted
# FastAPI app — and a patch of the canonical constant reaches this surface.
from tortoise import mcp_auth as _mcp_auth

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

# ----
# Server instructions (epic #2080 end-state seam #2126 — Claude-family
# priming). FastMCP exposes these via the MCP initialize handshake; Claude
# Code / Claude Desktop / Cursor / Codex attach them to the system prompt,
# so this block is the low-cost per-turn memory-usage guidance for every
# MCP-capable harness (the pull side of the seams; the per-turn push side is
# volunteer-turn.sh registered via `tortoise install <harness>`).
_MCP_INSTRUCTIONS = (
    "You are connected to Tortoise, an epistemic memory graph. "
    "tortoise_search / tortoise_query surface durable facts from prior "
    "sessions; tortoise_session_capture ingests a session transcript. "
    "Only persist information that is durable, factual, and reusable — "
    "not transient task chatter. Cite what you act on, and prefer the "
    "structured pointKinds over free text. If a search/query returns "
    "nothing relevant, say so plainly instead of fabricating recollection."
)

mcp = FastMCP("tortoise", instructions=_MCP_INSTRUCTIONS)

# ── MCP tool-call telemetry (#889) ─────────────────────────────────
# One structured analytics event per MCP tool call — friction evidence for the
# surface-design epic (#888). Installed at mcp.call_tool, the single dispatch
# point every transport (stdio, streamable HTTP, programmatic) funnels
# through, so no per-tool instrumentation is needed. Transport-level auth
# (OrgResolutionMiddleware) runs BEFORE this point in HTTP mode: latency
# therefore measures tool execution only, and authenticated requests already
# carry the org_id ContextVar (empty string for unauthenticated paths).
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


def _emit_mcp_tool_call_telemetry(org_id: str, tool_name: str, status: str,
                                  latency_ms: int, error_kind: str | None) -> None:
    """Fire-and-forget, fail-safe analytics write. Never raises, never blocks.

    ``status`` is one of this full set — the vocabulary is stated HERE because
    this is the single emission point for ``mcp_tool_call``:

    - ``ok`` — the call completed.
    - ``validation_error`` / ``auth_error`` / ``exec_error`` — mapped by
      ``_classify_mcp_call_error`` (``error_kind`` = the offending field, the
      exception class, or the unwrapped cause's class name, respectively);
      the #236 stdio auth gate is the OTHER ``auth_error`` producer, with
      ``error_kind = "stdio_auth_gate"`` — it is not an exception class, so it
      is not produced by that classifier.
    - ``timeout`` — the transport wait bound fired at this seam
      (``error_kind = "wait_bound"``).
    - ``cancelled`` — the caller cancelled the dispatch
      (``error_kind = "caller_cancelled"``).
    - ``refused`` — the TRANSPORT had already refused and answered this request
      before this seam could wait on it; the refusal is recorded once here
      instead of being duplicated as a false ``timeout``
      (``error_kind = "transport_wait_bound"``).

    The first four are the #889 set; ``timeout``, ``cancelled`` and ``refused``
    were added by #3834. The #888 research brief
    (``docs/epics/2026-08-11-888-surface-design/01-research-brief.md``) still
    lists only the original four — it is #888's artifact, not this lane's, so it
    is deliberately not edited here and this docstring is the live vocabulary.

    The Supabase write (sync httpx POST, up to 5s timeout in
    _track_analytics_event) runs OFF the tool-call hot path: on the default
    executor when an event loop is running (all server transports), else on a
    daemon thread. Any failure is logged and swallowed — telemetry must never
    break a tool call.
    """
    props = {"tool_name": _mcp_auth._sanitize_for_log(tool_name),
             "status": status,
             "latency_ms": latency_ms, "error_kind": error_kind}

    def _write() -> None:
        try:
            from tortoise.hosted_api import _track_analytics_event
            _track_analytics_event(org_id, "mcp_tool_call", props)
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


# ── #3834: the MCP-level wait bound ────────────────────────────────
# The HTTP transport's WaitBoundMiddleware bounds the REST routes, but it is
# INERT for MCP tool calls: FastMCP 3.4.6 runs Streamable-HTTP in SSE mode and
# starts the EventSourceResponse BEFORE dispatching the tool
# (``mcp/server/streamable_http.py`` — "Start the SSE response (this will send
# headers immediately)"), so ``http.response.start`` is on the wire within
# milliseconds and there is no refusal left to substitute. The measured
# population — the 7,795 ``mcp_tool_call`` events, p99 22.5 s, max 157 s — is
# exactly this dispatch, so the bound is enforced HERE, at ``mcp.call_tool``:
# the single seam every transport funnels through. It is applied across the
# registry rather than per tool (no tool name is referenced), so the 98→25
# surface rebuild (#4282) cannot throw it away.

#: Tool dispatches abandoned past the bound, held only so their late
#: result/exception is retrieved (never "exception was never retrieved"), so the
#: #2850 workload gauge stays non-idle while they run, and so a test can await
#: them. Entries remove themselves on completion.
_pending_mcp_wait_bound: set = set()

#: Breach-telemetry SCHEDULING futures (the telemetry pool's submit future),
#: tracked only so a late exception on it is retrieved. The write's own future
#: is tracked by the shared writer in ``hosted_api``.
_pending_mcp_wait_bound_telemetry: set = set()

#: The breach marker on a refused result's ``_meta``. A client can branch on it
#: without parsing prose; ``_wrapped_call_tool`` reads it to keep the
#: accompanying ``mcp_tool_call`` telemetry out of ``ok`` — ``timeout`` when this
#: seam's own deadline fires, or ``refused``/``transport_wait_bound`` when the
#: transport already refused the request and this seam suppresses its duplicate.
_WAIT_BOUND_META_KEY = "tortoise_wait_bound"


def _hold_mcp_dispatch_after_request(task) -> None:
    """Keep an abandoned MCP dispatch tracked, and hold the #2850 gauge for it.

    The round-1 fix moved ``InFlightMiddleware`` inside ``WaitBoundMiddleware``
    so an abandoned REST handler keeps counting until it finishes — else the
    watchdog's idle predicate reads idle while abandoned work still runs. That
    ordering does NOT cover this seam: the abandoned MCP dispatch is a task
    created inside the MCP app, and the HTTP POST (which the gauge wraps) ends
    as soon as the SSE refusal is written. So this seam holds the gauge itself
    for the life of the abandoned dispatch.

    Called on the TIMEOUT path only. The cancellation path is the opposite case
    (the cancellation is propagated into the dispatch); see the ``except
    asyncio.CancelledError`` branch in ``_await_under_mcp_wait_bound``. The gauge
    exits in a ``finally`` so a raising abandoned dispatch cannot leak a slot.
    """
    monitoring.workload_enter()
    _pending_mcp_wait_bound.add(task)

    def _done(t) -> None:
        _pending_mcp_wait_bound.discard(t)
        try:
            if not t.cancelled():
                t.exception()  # retrieve, so it is never un-retrieved
        finally:
            monitoring.workload_exit()

    task.add_done_callback(_done)


def _emit_mcp_wait_bound_breach_off_loop(org_id: str, latency_ms: int,
                                         name: str) -> None:
    """Schedule the shared breach writer OFF the event loop (#3834 G1).

    The writer is ``hosted_api._emit_wait_bound_breach`` — the SAME single emit
    site the REST arm uses — but ``hosted_api`` imports ``mcp_server`` at module
    scope, so this module cannot import it back at module scope. Importing it
    lazily HERE, on the loop and on the BREACH path, built the whole hosted
    FastAPI app before the refusal could be written: measured in a fresh process
    against a 0.05 s bound, the refusal came back after ~2–4 s (load-dependent)
    with the event loop frozen for the same span — the refusal is the product on
    this path, so a late one defeats the unit. The lazy import therefore lives
    INSIDE the callable submitted to the telemetry pool
    (``monitoring.control_plane_worker("telemetry")``, daemon workers, bounded
    backlog; #3498): the worker thread pays the import, the loop writes the
    refusal. The single emit site is unchanged — only where its module is
    imported moved.
    """
    # #3834 F-3: sanitize the client-supplied tool name at the analytics sink.
    # The helper is shared with the REST arm (``mcp_auth``), not a third copy —
    # ``mcp_server`` already imports that module, so this pays no ``hosted_api``
    # import. Sanitized on the loop (pure and cheap), so the off-loop closure
    # carries only the safe value.
    safe_name = _mcp_auth._sanitize_for_log(name)

    def _emit() -> None:
        try:
            from tortoise import hosted_api as _ha
            _ha._emit_wait_bound_breach(
                org_id, "/mcp", "POST", latency_ms, tool_name=safe_name)
        except Exception:  # telemetry must never turn a refusal into an error
            _log.debug("mcp wait-bound telemetry emit failed", exc_info=True)

    try:
        fut = monitoring.control_plane_worker("telemetry").submit(_emit)
    except Exception:  # telemetry must never turn a refusal into an error
        _log.debug("mcp wait-bound telemetry schedule failed", exc_info=True)
        return
    _pending_mcp_wait_bound_telemetry.add(fut)

    def _done(f) -> None:
        _pending_mcp_wait_bound_telemetry.discard(f)
        # ``submit`` returns a PRE-FAILED future when the telemetry worker's
        # bounded backlog is full (``_WorkerBacklogFull``) — the saturation
        # that also causes breaches. Mirror ``hosted_api._telemetry_done`` so
        # the outer hop's drop is traceable rather than silent (#3834 H3).
        if not f.cancelled() and f.exception() is not None:
            _log.debug(
                "mcp wait-bound telemetry schedule dropped: %r", f.exception())

    fut.add_done_callback(_done)


# Captured before wrapping — the middleware chain re-dispatches
# call_tool(run_middleware=False) internally; the wrapper passes those
# through untouched so exactly ONE event is emitted per client tool call.
_original_call_tool = mcp.call_tool

#: The key ``hosted_api.WaitBoundMiddleware`` writes the transport arrival time
#: under, on the shared ASGI ``scope["state"]`` dict (the same dict the auth
#: dependency writes ``org_id`` into).
_WAIT_BOUND_ARRIVAL_KEY = "_wait_bound_t0"

#: The key the middleware sets when IT has already emitted the breach event and
#: answered the caller (#3834 F-2). The seam reads it so one request records
#: exactly one ``transport_wait_bound_exceeded``.
_WAIT_BOUND_REFUSED_KEY = "_wait_bound_refused"


def _wait_bound_state() -> dict | None:
    """The shared ASGI ``scope["state"]`` dict, or None off-HTTP.

    Both the transport arrival stamp (#3834 F1) and the already-refused flag
    (#3834 F-2) ride this one dict, which ``hosted_api.WaitBoundMiddleware``
    writes and Starlette's ``Mount`` forwards to this sub-app as the SAME
    mapping.

    HTTP-only by construction: on stdio there is no request, so this returns
    None. It deliberately never raises — a missing dict means "no transport
    stamp / no refusal", never "fail the tool call". ``get_http_request`` is
    tried first because it is the public API, but its MCP-SDK ``request_ctx``
    branch can return a protocol object rather than the Starlette request, so
    FastMCP's HTTP ContextVar is the reliable fallback.
    """
    candidates: list = []
    try:
        from fastmcp.server.dependencies import get_http_request
        candidates.append(get_http_request())
    except Exception:
        pass
    try:
        from fastmcp.server.http import _current_http_request
        candidates.append(_current_http_request.get())
    except Exception:
        pass
    for request in candidates:
        scope = getattr(request, "scope", None)
        if not isinstance(scope, dict):
            continue
        state = scope.get("state")
        if isinstance(state, dict):
            return state
    return None


def _wait_bound_arrival() -> float | None:
    """When the HTTP request arrived at the transport, or None off-HTTP.

    The stamp is written by ``hosted_api.WaitBoundMiddleware`` and is what makes
    the bound ONE deadline instead of two (#3834 F1). The middleware cannot
    bound an MCP *tool call* — Streamable-HTTP starts the SSE response BEFORE
    dispatching the tool — so it hands this seam the SAME arrival time and the
    seam waits only the REMAINING part of the bound. Without it the
    caller-visible wait is pre-SSE cost + bound (measured: 0.522 s for an
    advertised 0.3 s bound) and a slow org resolution can push the total past
    the 15 s client budget with no legible refusal.

    HTTP-only by construction, so on stdio it returns None and the full bound
    applies.
    """
    state = _wait_bound_state()
    if state is not None:
        stamp = state.get(_WAIT_BOUND_ARRIVAL_KEY)
        if isinstance(stamp, (int, float)):
            return float(stamp)
    return None


def _wait_bound_refused() -> bool:
    """True when the transport already refused this request (#3834 F-2).

    ``hosted_api.WaitBoundMiddleware`` sets this on the shared
    ``scope["state"]`` when IT emits the breach event and answers the caller —
    the pre-SSE-stall path, where pre-SSE cost >= bound and this seam's own
    deadline has already collapsed to 0. The seam must then NOT emit a second
    ``transport_wait_bound_exceeded`` and must NOT record the redundant refusal
    as an ``mcp_tool_call`` ``timeout``. It still returns the refusal, so the
    abandoned dispatch still terminates cleanly.
    """
    state = _wait_bound_state()
    return bool(state.get(_WAIT_BOUND_REFUSED_KEY)) if state is not None else False


async def _await_under_mcp_wait_bound(name: str, arguments, *, version,
                                     task_meta):
    """Await the real tool dispatch under the transport wait bound (#3834).

    On breach the caller gets a legible refusal that REUSES the shipped
    vocabulary: the message and the advertised delay come from the SINGLE module
    both surfaces read (``mcp_auth._TRANSPORT_WAIT_BOUND_MESSAGE`` /
    ``mcp_auth._TRANSPORT_WAIT_RETRY_AFTER_S``), and ``retry_after`` rides the
    result.

    ⚠️ Why the refusal is a ``CallToolResult(isError=True)`` and NOT a JSON-RPC
    ``error`` object: the MCP SDK's ``tools/call`` handler wraps every handler
    exception except ``UrlElicitationRequiredError`` into exactly that shape
    (``mcp/server/lowlevel/server.py::_make_error_result``), so a raised
    ``McpError`` loses its code and ``data`` on this surface. The result channel
    is the only one the SDK exposes once the SSE stream has started; carrying
    the same message plus ``error.data.retry_after`` inside the result keeps the
    retry signal shipped WITH the bound instead of dropping it.

    On the TIMEOUT path the dispatch is ABANDONED, never cancelled: cancelling
    an ``asyncio`` await runs every ``finally`` the handler owns, and would also
    release this seam's #2850 gauge hold (``_hold_mcp_dispatch_after_request``)
    while the dispatched work is still in flight — the exact
    busy-misread-as-idle the hold exists to prevent. (The SDK-closing
    ``finally`` that makes an early cancel destructive is a fact about the
    hosted handlers in ``hosted_api`` — #2988 / #3718 — not about this module.)
    On the CANCELLATION path the opposite holds — the cancellation is propagated
    INTO the dispatch, as the direct await this wrapper replaced did, so the
    tool's own cancellation cleanup runs.
    """
    t0 = _time.perf_counter()
    # #3834 F1: spend the TRANSPORT's REMAINING deadline, not a fresh one. The
    # caller-visible wait is pre-SSE cost + bound; a fresh bound here makes it a
    # SUM, and a slow org resolution can push the total past the client budget
    # with no legible refusal. ``_time.monotonic()`` matches the middleware's
    # clock (``time.monotonic``), NOT this function's ``perf_counter`` t0.
    arrival = _wait_bound_arrival()
    if arrival is None:
        remaining = float(_mcp_auth._TRANSPORT_WAIT_BOUND_S)  # stdio / no HTTP request
    else:
        remaining = max(
            0.0, _mcp_auth._TRANSPORT_WAIT_BOUND_S - (_time.monotonic() - arrival))
    task = asyncio.ensure_future(
        _original_call_tool(name, arguments, version=version,
                            run_middleware=True, task_meta=task_meta))
    try:
        # ``wait_for`` + ``shield`` rather than ``asyncio.wait`` — the same
        # primitive choice as ``hosted_api.WaitBoundMiddleware.__call__``. The
        # seam's cost is NOT the waiter: measured, the two are cost-neutral (same
        # trivial coroutine — ``wait_for(shield)`` 198 µs p50 vs ``asyncio.wait``
        # 199 µs p50, identical 53.8 µs floor; an inline ``asyncio.timeout`` is
        # 7.7 µs). It is the per-call ``ensure_future`` task indirection —
        # ABANDON-don't-cancel requires owning the task — measured as a dispatch
        # delta of p50 ≈ +0.48 ms, p95 ≈ +1.7 ms vs the unwrapped
        # ``_original_call_tool``. The ``shield`` is what preserves
        # ABANDON-don't-cancel: ``wait_for`` alone cancels the awaited future on
        # timeout, and cancelling here would run the SDK-closing ``finally``
        # under work still using it (#2988 / #3718).
        return await asyncio.wait_for(asyncio.shield(task), timeout=remaining)
    except asyncio.CancelledError:
        # Outer cancellation (client disconnect, server shutdown, transport
        # teardown). Propagate it INTO the dispatch and await it, so the tool's
        # own cancellation path runs — abandoning here would silently keep a
        # cancelled dispatch alive. Suppress only the child's CANCELLATION; a
        # genuine error from its cleanup must surface.
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        raise
    except TimeoutError:
        if task.done():
            # The dispatch finished as the deadline expired: surface its own
            # result/exception. A handler's ``TimeoutError`` is NOT a wait
            # breach (``asyncio.TimeoutError`` IS builtin ``TimeoutError``).
            return task.result()
        # ``shield`` kept the inner dispatch RUNNING; the breach path below
        # abandons it on purpose.
        pass

    _hold_mcp_dispatch_after_request(task)
    # #3834 F-2: the transport already refused AND answered this request (a
    # pre-SSE stall, where pre-SSE cost >= bound and this seam's remaining
    # deadline was 0). The middleware's event is then the truthful record;
    # emitting here would be a second, mutually-inconsistent
    # ``transport_wait_bound_exceeded`` for the same request.
    transport_refused = _wait_bound_refused()
    # #3834 F-1: report the interval the bound actually governs — the
    # CALLER-VISIBLE wait, the SAME interval the REST arm's breach event reports
    # (``hosted_api`` uses ``time.monotonic() - transport arrival``). ``t0`` here
    # is THIS seam's entry, which is AFTER pre-SSE (org resolution, rate limit,
    # routing); because the seam spends only the transport's REMAINING deadline,
    # a ``t0``-based interval under-reports by the whole pre-SSE cost. On stdio
    # there is no transport arrival, so the seam-local ``t0`` stands in.
    if arrival is None:
        latency_ms = int((_time.perf_counter() - t0) * 1000)
    else:
        latency_ms = int((_time.monotonic() - arrival) * 1000)
    # #3834 F-3: the tool name is the client-supplied JSON-RPC ``params.name``.
    # On the SCOPED path ``_enforce_mcp_tool_scope`` has already resolved it
    # against the registry (and denies an unregistered name), but on the
    # UNscoped (org-wide / selfhost) path it reaches this seam without any
    # registry-membership guarantee — and the ``mcp_tool_call`` sink is always on,
    # so it is untrusted in the same class of sink the REST arm already sanitizes
    # its route path for. Escape it for the log line (the analytics sink
    # sanitizes its own copy).
    safe_name = _mcp_auth._sanitize_for_log(name)
    if transport_refused:
        # The transport's warning already covers this request; a second
        # "refusing legibly" warning would describe a refusal this seam never
        # delivers (the middleware dropped the SSE response).
        _log.debug(
            "MCP tools/call %s already refused at the transport; this seam's "
            "refusal is redundant", safe_name)
    else:
        # The SAME breach writer the REST arm uses — one emit site, one prop
        # vocabulary — scheduled OFF the loop (see
        # ``_emit_mcp_wait_bound_breach_off_loop``): the fast path must not pay
        # a ``hosted_api`` import, and neither must the breach path, where that
        # import froze the loop and delivered the refusal seconds late.
        _emit_mcp_wait_bound_breach_off_loop(
            _current_org_id.get() or "", latency_ms, name)
        _log.warning(
            "transport wait bound (%.0fs) exceeded: MCP tools/call %s — "
            "refusing legibly", _mcp_auth._TRANSPORT_WAIT_BOUND_S, safe_name)
    retry_after = _mcp_auth._TRANSPORT_WAIT_RETRY_AFTER_S
    return ToolResult(
        content=_mcp_auth._TRANSPORT_WAIT_BOUND_MESSAGE,
        # The REST JSON-RPC error payload, carried on the channel this surface
        # has: same code, same message, same `data.retry_after`.
        structured_content={"error": {
            "code": ERR_TIMEOUT,
            "message": _mcp_auth._TRANSPORT_WAIT_BOUND_MESSAGE,
            "data": {"retry_after": retry_after},
        }},
        meta={_WAIT_BOUND_META_KEY: True},
        is_error=True,
    )


def _enforce_mcp_tool_scope(name: str) -> None:
    """C5 #2114 (D-C5-3, MCP half): pre-filter scope gate at the tool-call
    boundary — the single dispatch point every tenant tool call crosses.

    - legacy full-access keys (scopes None OR legacy_full_access) and OAuth/
      session resolutions (scopes None) pass — existing flows unchanged.
    - a SCOPED key is enforced: the tool's registry entry carries the declared
      `writes` flag — a `writes=True` tool needs graphs:write, everything else
      needs graphs:read (write implies read — graphs:write satisfies reads).
      `WRITE_TOOL_NAMES` is the derived view of that flag (#4170).
    - an UNRESOLVABLE name is DENIED (`AuthorizationError`), never served as a
      read. The old else-branch treated any name missing from the parallel
      write list as a read, so a writer absent from that list was reachable by
      a graphs:read-only key (#4170).
    - deleg=0 children without a data scope never reach here (the
      middleware rejects them at resolution); deleg=0 children WITH a data
      scope are routed to their own graph by _get_org_sdk and enforced
      here like any scoped key.

    Raises AuthorizationError (mapped to an authz error result + classified
    auth_error by _classify_mcp_call_error) — same failure family as the
    middleware's KEY_NOT_USER_MINTED / SUSPENDED signals."""

    scopes = _current_scopes.get()
    if scopes is None or _current_legacy_full_access.get():
        return
    have = set(scopes)
    entry = get_tool_by_name().get(name)
    if entry is None:
        # #4170: an unresolvable name is DENIED, never served as a read. The
        # default used to be "read", so a write missing from the parallel list
        # was reachable from a graphs:read-only key.
        raise AuthorizationError(
            f"Unknown tool {name} — denied (not present in the registry).")
    if entry.writes:
        if "graphs:write" not in have:
            raise AuthorizationError(
                f"Key lacks graphs:write scope for tool {name}.")
    elif "graphs:read" not in have and "graphs:write" not in have:
        raise AuthorizationError(
            f"Key lacks a graph data scope (graphs:read) for tool {name}.")


def _reject_graph_bound_mcp_org_surface(surface: str) -> None:
    """C5 #2114 (re-review 3): MCP tools that write ORG-level state (the
    DEFAULT graph's onboarding node / pack installs / session-recording
    toggle — data that lives outside a custom graph) reject graph-bound
    keys outright, mirroring REST's _reject_graph_bound_org_surface. A
    per-graph key must never write the org default graph through an MCP
    org tool (cross-graph write). Org-wide keys / OAuth / selfhost
    (graph_id None) pass."""

    if _current_graph_id.get():
        raise AuthorizationError(
            f"Graph-scoped keys cannot access {surface}.")


async def _wrapped_call_tool(name: str, arguments: dict[str, Any] | None = None, *,
                             version=None, run_middleware: bool = True,
                             task_meta=None):
    """Telemetry-instrumented single dispatch point (installed as mcp.call_tool).

    Emits one mcp_tool_call analytics event per client tool call — the ``status``
    vocabulary is documented at ``_emit_mcp_tool_call_telemetry`` — with
    latency measured around the tool execution only (transport auth runs
    before this point and is excluded). Background-task dispatches
    (task_meta) are measured at scheduling granularity — our tools never use
    them, and the #888 evidence window runs normal synchronous clients.
    """
    if not run_middleware:
        return await _original_call_tool(name, arguments, version=version,
                                         run_middleware=False, task_meta=task_meta)
    org_id = _current_org_id.get() or ""
    _enforce_mcp_tool_scope(name)
    maybe_record_mcp_read(
        name, org_id, _current_org_limits.get(),
        # REVIEW-FIX P2 (#4057): `arguments` is the raw PRE-validation dict, and
        # this metering runs BEFORE the dispatch. So a tool that does not
        # declare `dry_run` — whose schema will REJECT that key — would be
        # recorded as a READ the caller never performed. Gate on the declared
        # preview set, not on key presence in untrusted input.
        dry_run=name in _dry_run_tool_names() and bool((arguments or {}).get("dry_run")),
    )
    status, error_kind = "ok", None
    t0 = _time.perf_counter()
    try:
        result = await _await_under_mcp_wait_bound(
            name, arguments, version=version, task_meta=task_meta)
        if getattr(result, "meta", None) and result.meta.get(_WAIT_BOUND_META_KEY):
            # #3834: the transport wait bound refused this dispatch. Its own
            # status (not exec_error) so "how often are we breaching 10 s" is
            # answerable from the SAME mcp_tool_call series the bound was
            # justified by.
            if _wait_bound_refused():
                # #3834 F-2: the TRANSPORT already refused and answered this
                # request before this seam could wait on it (pre-SSE cost >=
                # bound, so the remaining deadline was 0). Recording ``timeout``
                # would be a second, FALSE row in the very series the bound is
                # measured from. ``refused`` is its own status: the dispatch was
                # abandoned by an OUTER refusal, not by this seam's deadline.
                status, error_kind = "refused", "transport_wait_bound"
            else:
                status, error_kind = "timeout", "wait_bound"
        # The stdio auth gate (#236) returns an error dict instead of raising
        # (TORTOISE_API_KEY set → every call is rejected). Classify it so
        # unauthenticated stdio calls don't masquerade as ok.
        payload = getattr(result, "structured_content", result)
        if (isinstance(payload, dict)
                and isinstance(payload.get("error"), str)
                and payload["error"].startswith("Authentication required")):
            status, error_kind = "auth_error", "stdio_auth_gate"
        return result
    except asyncio.CancelledError:
        # A cancelled dispatch is neither an "ok" nor an exec error — classifying
        # it as timeout or success would hide the cancellation rate in the same
        # mcp_tool_call series the bound is measured from. (Pre-existing: the
        # generic `except Exception` never saw CancelledError, so a cancelled
        # call emitted status="ok".)
        status, error_kind = "cancelled", "caller_cancelled"
        raise
    except Exception as exc:
        status, error_kind = _classify_mcp_call_error(exc)
        raise
    finally:
        # NOTE (#3834 F-1): this is deliberately the SEAM-LOCAL interval (tool
        # dispatch, transport cost excluded), NOT the caller-visible interval
        # the breach event above reports. ``mcp_tool_call`` is an established
        # dispatch series (#888/#889; the p99 22.5 s population the bound was
        # justified by), and its contract — documented at ``_wrapped_call_tool``
        # — excludes transport auth. Redefining it would silently break
        # comparability with that population. The bound's own telemetry is the
        # breach event, which measures the caller-visible wait on both arms.
        latency_ms = int((_time.perf_counter() - t0) * 1000)
        try:
            _emit_mcp_tool_call_telemetry(org_id, name, status, latency_ms,
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
        from tortoise.projection import FalkorProjection  # noqa: I001
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
    # epic #900 T7 (#1043): the index path creates Sources + Events/Documents
    # + references edges — node/edge-creating, quota-gated (S8 pin, I25)
    "tortoise_index_files",
})


# #4170: the write permission lives on each ToolDefinition entry (`writes`),
# so WRITE_TOOL_NAMES is DERIVED — a rename or a merge edits the entry and the
# permission travels with it. It is no longer a hand-maintained parallel list
# that a new writer could silently be missing from.
#
# #308 (R3, scoping delta 11): this is also the read-velocity classification
# set — tools/call for a tool NOT in it counts as a read. It is NOT the
# complement of _QUOTA_GATED: tortoise_ingest is _quota_gated-wrapped but
# absent from that frozenset, and the demo-create tool writes Points via
# _enforce_quota without the wrapper.
#
# The `# noqa: E402` is deliberate: the bottom `tool_registry` import exists
# for the adapter, and importing the derived helpers here keeps this module's
# import order unchanged (tool_registry does not import mcp_server — no cycle).
from tortoise.tool_registry import get_tool_by_name, get_write_tool_names  # noqa: E402

WRITE_TOOL_NAMES: frozenset[str] = get_write_tool_names()

# #4057: a tool is dry-run-capable iff its SERVED HANDLER declares a `dry_run`
# parameter — the fact this metering needs. It is deliberately NOT "the raw
# argument dict carries a `dry_run` key": that dict is the PRE-validation
# client input, read BEFORE the dispatch, so a caller could move the read
# counter for a tool whose schema REJECTS the key (a READ recorded for a call
# that is then refused). Computed from the declarations and cached (lazy — the
# handlers are defined below this point — because it sits on the per-call
# path). `tests/test_dry_run_preview.py::TestPreviewToolSetIsDeclared` pins it:
# the six preview tools must be in it, a write tool without `dry_run` must not,
# and the metering behaviour is verified through the real dispatch seam.
_DRY_RUN_TOOLS: frozenset[str] | None = None


def _dry_run_tool_names() -> frozenset[str]:
    """Registry names whose served handler declares `dry_run` (computed once)."""
    global _DRY_RUN_TOOLS
    if _DRY_RUN_TOOLS is None:
        from tortoise.tool_registry import TOOL_REGISTRY

        names = set()
        for entry in TOOL_REGISTRY:
            fn = globals().get(entry.name)
            if fn is None:
                continue
            try:
                params = inspect.signature(fn).parameters
            except (TypeError, ValueError):  # pragma: no cover - non-callable
                continue
            if "dry_run" in params:
                names.add(entry.name)
        _DRY_RUN_TOOLS = frozenset(names)
    return _DRY_RUN_TOOLS


# #329: per-org per-minute LLM-call budget for tortoise_analyze (operator LLM
# keys back outbound calls; the rate limiter alone is not the bound).
_ANALYZE_LLM_BUDGET: dict[str, list[float]] = {}


def _analyze_llm_budget_available() -> bool:
    """True if this org still has analyze LLM budget this minute (HTTP only).

    Beyond budget the tool degrades to keyword-only classification (no paid
    outbound call). Stdio (no org context) is not budgeted.
    """
    import time as _t  # noqa: I001
    from tortoise.mcp_auth import _current_org_id
    from tortoise.quota import MAX_ANALYZE_LLM_PER_MIN
    org_id = _current_org_id.get()
    if not org_id:
        return True  # stdio/operator — no org budget accounting
    now_ts = _t.time()
    bucket = _ANALYZE_LLM_BUDGET.setdefault(org_id, [])
    bucket[:] = [ts for ts in bucket if now_ts - ts < 60]
    # prune -> check -> append (never pop between check and append — that
    # orphans the appended timestamp and silently disables the budget)
    if len(bucket) >= MAX_ANALYZE_LLM_PER_MIN:
        return False
    bucket.append(now_ts)
    return True


def _enforce_quota(resource: str = "points") -> None:
    """#329: fail-closed org quota pre-write for MCP write tools.

    HTTP mode: limits come from the middleware-resolved ContextVar (same
    limits REST sees); fallback resolves from the registry. Stdio mode
    (no org context) → skip — operator/trusted (batch caps still apply).
    """
    from tortoise.mcp_auth import SELFHOST_ORG_ID, _current_org_id, _current_org_limits
    from tortoise.quota import enforce_org_limit, resolve_org_limits
    org_id = _current_org_id.get()
    if not org_id:
        return  # stdio/operator — no org context
    if org_id == SELFHOST_ORG_ID:
        # Selfhost transport placeholder (#338): no tenant registry exists —
        # quota is N/A (selfhost has no billing). Batch caps still apply.
        return
    limits = _current_org_limits.get()
    if limits is None:
        limits = resolve_org_limits(org_id)
    # Count on the SAME org SDK the tool writes to (identical connection),
    # so the count and the write can never target different databases.
    enforce_org_limit(limits, resource, sdk=_get_org_sdk())


def _alert_unmetered(lane: str, org_id: str | None,
                     error: BaseException) -> None:
    """Emit the #3981 operator alert for a dropped increment, never raising.

    The import is itself guarded: ``tortoise.metering`` may be the thing that
    failed, and an unguarded import inside an ``except`` would turn a
    bookkeeping fault into the user-facing failure the owner's ruling forbids.
    """
    try:
        from tortoise.metering import report_unmetered_increment
    except Exception:  # noqa: BLE001, RUF100 — the alert must never raise
        logging.getLogger("tortoise.metering").error(
            "UNMETERED INCREMENT (#3981): lane=%s team=%s error=%s: %s "
            "(metering module unavailable)", lane, org_id or "<none>",
            type(error).__name__, error)
        # The fallback still ALERTS — a log line on an ephemeral Fly rootfs is
        # the #3677 loss class this lane exists to remove, and this is the one
        # case where the ledger AND the reporter are both down. The kind
        # constant lives in ``operator_alert``, importable when ``metering`` is not.
        with contextlib.suppress(Exception):
            from tortoise.operator_alert import alert_unmetered_increment

            alert_unmetered_increment(lane, org_id, error)
        return
    report_unmetered_increment(lane=lane, org_id=org_id, error=error)


def _quota_gated(fn, resource: str = "points", abuse_weight=None):
    """Wrap a bound SDK method with a pre-write quota check + metering.

    Preserves the bound-callable style (_safe(_get_org_sdk().name, ...)):
    the quota check runs INSIDE _safe's try so errors surface as structured
    error dicts (see _safe's QuotaExceededError/QuotaCheckError mapping).

    #681: after a successful write (fn returns without raising), records a
    write op for overage metering. Best-effort — the increment never blocks the
    tool, and the drop is never silent (#3981): when the increment cannot be
    recorded the operator is alerted (lane=mcp_write_op) and the error is
    absorbed. The raise from an unresolvable metering window is a SIGNAL, not a
    refusal; the user-facing refusal here is ``_enforce_quota`` above, which
    runs BEFORE the write.

    #308 (R1, scoping delta 8): ``abuse_weight`` records a WEIGHTED
    point_create event after a successful Point-creating write — int for a
    fixed weight, or callable(result, args, kwargs) -> int for bulk ops
    (ingest/checkpoint/file_decision). Tools that do not create Points pass
    None and record nothing (an edit/update burst must never trip R1).
    """
    def _gated(*args, **kwargs):
        _enforce_quota(resource)
        result = fn(*args, **kwargs)
        try:
            from tortoise.mcp_auth import _current_org_id
            org_id = _current_org_id.get()
        except Exception:  # noqa: BLE001, RUF100 — no org context; metering is skipped
            org_id = None
        # Metering (#681): best-effort, after successful write
        if org_id:
            try:
                from tortoise.mcp_auth import _current_org_limits
                limits = _current_org_limits.get() or {}
                from tortoise.metering import record_write_ops
                record_write_ops(org_id, tier=limits.get("tier"))
            except Exception as e:  # noqa: BLE001, RUF100 — never block the tool
                _alert_unmetered("mcp_write_op", org_id, e)
            # #308 (R1): weighted point_create recording + evaluation. The
            # engine piggybacks R2 evaluation on the same call. Its OWN
            # best-effort block — an abuse-recording failure is not a dropped
            # increment and must never be reported as one (#3981).
            try:
                if abuse_weight is not None and not _abuse_off():
                    n = (int(abuse_weight(result, args, kwargs) or 0)
                         if callable(abuse_weight) else int(abuse_weight))
                    if n > 0:
                        from tortoise import abuse as _abuse
                        _abuse.get_engine().record_point_create(org_id, n)
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


def maybe_record_mcp_read(name: str, org_id: str, limits: dict | None,
                          *, dry_run: bool = False) -> None:
    """#308 (R3, scoping delta 11): read-velocity counting for non-write
    tools/call. Explicit write set (WRITE_TOOL_NAMES) — writes never count
    as reads. A ``dry_run=True`` preview of a write tool performs only READS,
    so it is metered as a read (the alternative is an un-metered enumeration
    channel that no read-velocity alert can see). key_id rides the limits
    ContextVar (Supabase resolutions carry it; registry resolutions may not →
    per-org counting only there). Best-effort: telemetry never breaks the
    tool call."""
    try:
        if not org_id or org_id == SELFHOST_ORG_ID or _abuse_off():
            return
        if name in WRITE_TOOL_NAMES and not dry_run:
            return
        from tortoise import abuse as _abuse
        _abuse.record_read((limits or {}).get("key_id"), org_id)
    except Exception:
        pass


def _scrub_error(msg: str) -> str:
    """#43 sanitize: strip hostnames, ports, passwords from error messages."""
    import re
    msg = re.sub(r'://[^@]*@', '://***@', msg)  # password in URI
    msg = re.sub(r'(host=|at |to )[\w.-]+(:\d+)?', r'\1***', msg)  # host:port
    return msg


class _SafeError(dict):
    """Failure result from :func:`_safe` (transport, auth, quota, exception).

    A ``dict`` *subclass*, deliberately not a plain dict. A successful SDK
    write returns the created node's own property dict, which may contain a
    user-supplied key literally named ``"error"`` — so key presence cannot
    distinguish "the call failed" from "the call succeeded and the user has a
    prop called error". Gating on ``"error" not in result`` therefore silently
    dropped the onboarding observation for such writes (#3926). Gate on
    ``isinstance(result, _SafeError)`` instead.

    As a dict subclass the value still indexes, compares, and serializes
    exactly as the plain error dict did — the wire shape is unchanged.
    """

    __slots__ = ()


def _safe(fn, *args, **kwargs):
    """Call fn; return an _SafeError on exception instead of raising.

    #329: QuotaExceededError → {"error", "code": ERR_QUOTA}; QuotaCheckError
    → {"error", "code": ERR_QUOTA_SERVER} (fail-closed counting).

    Transport-aware auth gate (#236). Fail-closed: if _transport_mode is None
    (unset/misconfigured) ALL operations reject. HTTP mode trusts transport-level
    auth (OrgResolutionMiddleware 401'd pre-dispatch). Stdio mode keeps the
    dev-mode gate. NEVER depends on is_dev_mode() alone — it returns True in
    hosted production (TORTOISE_API_KEY unset), which would silently bypass auth.
    """
    mode = _transport_mode.get()
    if mode is None:
        return _SafeError({
            "error": (
                "Authentication required. MCP transport mode not initialized."
            )
        })
    if mode == "http":
        pass  # auth enforced at transport (OrgResolutionMiddleware)
    elif mode == "stdio":
        if not _is_dev_mode():
            return _SafeError({
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
            })
    else:
        # Unknown transport mode — fail-closed (code-review fix)
        return _SafeError({"error": f"Unknown MCP transport mode: {mode!r}"})
    try:
        result = fn(*args, **kwargs)
        return result
    except Exception as e:
        monitoring.record_error()
        from tortoise.exceptions import BundleValidationError
        from tortoise.quota import QuotaCheckError, QuotaExceededError
        if isinstance(e, BundleValidationError):
            # A2: dedicated branch BEFORE the generic — violations survive
            # the MCP boundary INTACT (E2E-12.1/E2E-15(c)); the wire shape is
            # {error: <first message>, code: ERR_BUNDLE_INVALID, violations}.
            # REVIEW-FIX P2: message content scrubbed (#43) — violation
            # messages echo client-controlled refs/endpoints that could carry
            # URIs with credentials; structure (section/index/message keys)
            # survives intact.
            scrubbed = [{**v, "message": _scrub_error(v["message"])}
                        for v in e.violations]
            return _SafeError({"error": _scrub_error(str(e)),
                               "code": ERR_BUNDLE_INVALID,
                               "violations": scrubbed})
        if isinstance(e, QuotaExceededError):
            return _SafeError({"error": str(e), "code": ERR_QUOTA})
        # Epic 903-C11 (#1249): BudgetExceededError (full-mode dream budget
        # unsatisfiable — C6) is quota-class → ERR_QUOTA.
        from tortoise.exceptions import BudgetExceededError
        if isinstance(e, BudgetExceededError):
            return _SafeError({"error": str(e), "code": ERR_QUOTA})
        if isinstance(e, QuotaCheckError):
            return _SafeError({"error": str(e), "code": ERR_QUOTA_SERVER})
        from tortoise.exceptions import Phase2Error
        if isinstance(e, Phase2Error):
            # A2: Phase-2 failure — {error, batch_id} with NO code (distinct
            # from Phase-1's ERR_BUNDLE_INVALID); the batch_id lets the agent
            # audit the partial commit before re-sending (cycle-23/24 pin).
            # REVIEW-FIX P2: message scrubbed (#43).
            return _SafeError({"error": _scrub_error(str(e)),
                               **({"batch_id": e.batch_id} if e.batch_id else {})})
        msg = _scrub_error(str(e))
        return _SafeError({"error": msg})


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
# A2: Phase-1 bundle validation failure (carries .violations).
ERR_BUNDLE_INVALID = -32008
# Application-defined pre-SDK param errors (tool-level validation that never
# reaches the SDK): invalid granularity / promotion_policy on tortoise_ingest
# return {error, code: ERR_INVALID} naming the valid values (E2E-8.3 pin).
ERR_INVALID = -32003


# #1486 (code-review P1): server-managed node properties tenant props must
# never set. is_episodic is the points-quota discriminator (quota.py counts
# only `is_episodic IS NULL OR = false` points) — a tenant setting it true
# would exclude their points from the quota (unlimited points past the
# paid-tier cap). sourcePath/source_path/id are the pre-existing #329
# denylist. The MCP tools reject these AT THE BOUNDARY (before the `**props`
# unpack can bind the SDK's explicit server-managed params); the SDK's
# _sanitize_props reject is the fail-closed backstop.
_SERVER_MANAGED_PROPS = frozenset({  # #3947: envelope capture directive (not a tenant prop)
    "is_episodic", "sourcePath", "source_path", "id", "_server_id", "outdated", "contains_session",
    # #5004: the embedding's journal IDENTITY keys are server-minted. Rejected
    # at this boundary AND in `sdk._sanitize_props` (the fail-closed backstop).
    # `embedding` ITSELF is deliberately NOT here — `create_point` has a
    # recorded decision (PR #3018 review P2) that a caller-supplied vector is
    # stored verbatim; the writer marks it `embedding_verbatim` instead.
    "embedding_model", "embedding_revision", "embedding_text_hash",
    "embedding_verbatim", "embedding_preserved"})


# #2600: client-supplied actor claims are STRIP-AND-IGNORE (never a 4xx —
# the server owns attribution). Imported from tortoise/sdk.py — single
# source of truth (#2664 code-review P2: no duplicate frozenset drift).
# authoredBy is deliberately NOT here (pre-existing client author-label
# residual).


def _reject_server_managed_props(props: dict | None) -> str | None:
    """Strip client-forged actor claims, then reject remaining server-managed
    fields (#329/#1486). Returns an error message or None."""
    # #2600: strip + ignore FIRST — never stored, never a 4xx. Runs inside
    # this single choke point (11 tool call sites) so no per-tool strip is
    # missed. Guard None (optional props= kwargs on entity tools call with
    # no props dict).
    # In-place pop is the contract here: call sites ALWAYS pass a fresh
    # per-request dict (`props = _parse(props)` above each call — never a
    # shared/cached object), and the function returns only an error string,
    # so the stripped dict MUST be the caller's own for the strip to reach
    # storage. (Unlike sdk._sanitize_props, which copies and returns the
    # cleaned dict.) A warning is logged when a client-supplied actor key is
    # stripped, matching the SDK backstop's log evidence.
    if not props:
        return None
    for k in _RESERVED_ACTOR_PROPS:
        if k in props:
            _log.warning(
                "ignoring client-supplied %r at MCP boundary", k)
            props.pop(k)
    bad = _SERVER_MANAGED_PROPS & set(props or {})
    if not bad:
        return None
    return ("server-managed field(s) cannot be set via props: "
            f"{sorted(bad)}")



def _http_excluded_error() -> dict:
    """#236: JSON-RPC error for tools excluded from the tenant HTTP surface (D4)."""
    return {
        "jsonrpc": "2.0",
        "error": {
            "code": ERR_EXCLUDED,
            "message": (
                "This tool is not available over HTTP. "
                "Use the hosted REST API or stdio MCP."),
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
                          credibility: str | int | float | None = None,
                          props: Any = None,
                          dedup: bool = True) -> dict:
    """Create a Point node (statement, decision, vision, hypothesis, etc.).

    On a successful write from an incomplete org, records the onboarding
    steps this write is evidence for (`harness-connected`,
    `first-points-filed`, plus `decide-completed` for a decision-shaped
    write) and hands completion to the canonical fork-aware gate — no
    separate ceremony needed, and no step the write did not observe (#3784).

    dedup=True (default): idempotent — returns existing Point if content matches.
    dedup=False: force-create even if content is identical.

    Decision parts (#2199) — pointKind option/criterion/evidence/decision
    created WITHOUT an explicit status land LIVE with an explicit starting
    belief, so the documented decide flow ranks on the first attempt (no
    promote/calibrate chores): omit ``credibility`` for the system starting
    belief 'medium' (provenance 'system-default', visible in
    tortoise_calibrate_summary), or pass your own via the plain-language
    ladder — gold / high / medium / low / unverified (also T0-T4 or numeric
    0-4) — stamped 'set-by-author'. Pass an explicit status=... via props to
    keep full manual control (capture/extraction paths use status='draft').
    Unknown ladder words are rejected (never a silent Beta(1,1) fallback).

    → See /skill:tortoise-graph-reasoning for pointKind guidance:
      evidence is a role (not a kind), use Source for provenance.
    """
    props = _parse(props)
    _reject = _reject_server_managed_props(props)
    if _reject:
        return {"error": _reject, "code": ERR_INVALID}
    merged = dict(props or {})
    if authoredBy:
        merged["authoredBy"] = authoredBy
    if credibility is not None:
        merged["credibility"] = credibility
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
    result = _safe(_quota_gated(_get_org_sdk().create_point, "points", abuse_weight=1), kind, content, **merged)
    # #3926: gate on the typed failure result, never on key presence — a user
    # prop named "error" must not suppress the onboarding observation.
    if not isinstance(result, _SafeError):
        # #3784: only a decision-shaped write observes the decision step —
        # `decision` is the pointKind the documented EP decide protocol
        # files (tortoise/onboarding/SKILL.md §5) and the one file_decision
        # creates. Read the PERSISTED pointKind when the write returned one
        # (the server must observe what was recorded, not what was asked
        # for); any other kind observes no decision.
        _recorded_kind = (result.get("pointKind") if isinstance(result, dict)
                          else kind)
        _maybe_onboarding_auto_complete(
            decision_observed=(str(_recorded_kind or kind).strip().lower()
                               == "decision"))
    return result


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
        rows = _safe(_get_org_sdk().query_points_by_tag, tag)
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
        return _safe(_get_org_sdk().tortoise_fts_query, text, kind=kind,
                     entity_type=entity_type, limit=eff_limit,
                     min_confidence=min_confidence or 0.0,
                     order_by=order_by or "relevance")
    if paginated:
        eff_offset = offset if offset is not None else 0
        if page is not None:
            eff_offset = (page - 1) * eff_limit
        return _safe(_get_org_sdk().paginated_query, kind, skip=eff_offset,
                     limit=eff_limit, include_retracted=include_retracted,
                     **(filters or {}))
    result = _safe(_get_org_sdk().query, kind,
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
    return _safe(_get_org_sdk().check_structure)


def tortoise_validate_domain(domain: str) -> dict:
    """Validate a domain's ontology integrity — advisory, read-only (#405).

    Runs the domain's graph-surface validators (orphan useCase, dangling
    refs, draft hygiene) against the live graph and returns enriched,
    actionable violations ({rule, kind, ref, message, fix}) plus drift
    warnings for manifest chains with no registered validator. Never
    modifies the graph; violations are warnings, not blocks."""
    return _safe(_get_org_sdk().validate_domain, domain)


def tortoise_audit(point_kinds: list[str] | None = None) -> dict:
    """Audit graph wiring quality — 8 checks (epic #348).

    Returns structured JSON: per-check counts (uncapped) + capped samples +
    summary + exit_code (0 clean, 1 issues). Same surface as the
    `tortoise audit` CLI — both wrap the shared SDK audit() method.
    """
    return _safe(_get_org_sdk().audit, point_kinds=point_kinds)


def tortoise_summarize_structure() -> dict:
    """Structure summary — points on the graph across ALL point kinds (#2205).
    Returns {total, operators, gate0_jtbds..gate4_requirements, gate_total}.
    total counts every non-operator kind (statements, observations, decisions,
    ...), not just the product-strategy gates; operators is reported
    separately. Alias → overview(section='structure') (epic #888 W3)."""
    return _safe(_get_org_sdk().summarize_structure)


def tortoise_list_pointkinds() -> list[dict]:
    """List all pointKinds present in the graph with counts. What EXISTS.
    Alias → overview(section='pointkinds') (epic #888 W3)."""
    return _safe(_get_org_sdk().list_pointkinds)


def tortoise_list_sources() -> list[dict]:
    """List all Sources with point counts. Where data came FROM.
    Alias → overview(section='sources') (epic #888 W3)."""
    return _safe(_get_org_sdk().list_sources)


def tortoise_list_namespaces() -> list[dict]:
    """List installed pack namespaces.
    Alias → overview(section='namespaces') (epic #888 W3)."""
    return _safe(_get_org_sdk().list_namespaces)


def tortoise_list_batch(batch_id: str) -> dict:
    """Audit one ingest bundle's stamped artifacts (epic #902 A13)."""
    return _safe(_get_org_sdk().list_batch, batch_id)


def tortoise_list_batches(limit: int = 20) -> list[dict]:
    """Batch discovery — recent distinct ingest batch_ids (epic #902 A13)."""
    return _safe(_get_org_sdk().list_batches, limit=limit)


def tortoise_packs_list() -> list[dict]:
    """List this org's ACTIVE packs (#318 — multi-tenant pack isolation).

    Shared pack catalog + the tenant graph's PackInstall activation records
    (ensure-then-read core shared with REST GET /v1/packs — no REST/MCP
    divergence). Auth-only scoping via the _current_org_id contextvar seam:
    cross-tenant access is structurally impossible. D6 masking: empty result
    when nothing is installed (never an error); the only error surface is
    auth/transport failure.
    """
    # Fail-closed (#318): in HTTP/tenant mode a missing org context must
    # NEVER fall back to the base (default-namespace) SDK for pack
    # introspection — pack state is per-tenant. (stdio/selfhost keeps the
    # base SDK: single-tenant, the base graph IS the tenant.)
    if _current_org_id.get() is None and _transport_mode.get() == "http":
        return {"error": "Authentication required. No team context.",
                "code": ERR_UNAUTHORIZED}
    from tortoise.pack_state import get_tenant_packs
    return _safe(lambda: get_tenant_packs(_get_org_sdk()))


def tortoise_pack_install(manifest_yaml: str) -> dict:
    """#1935: install a custom expansion pack on the HOSTED surface.

    Validates against the shared registry validator (schema + cross-pack)
    plus the tenant policy (reserved starter namespace + ontology-only v1),
    stores the manifest graph-natively in the tenant's graph
    (``:PackManifest``) and activates it (idempotent).

    DEPLOYMENT-GATED (#1935 R10): on SELF-HOST this tool is an actionable
    stub — self-host configures packs via the filesystem packs dir
    (``TORTOISE_PACKS_DIR``) + the ``tortoise pack`` CLI instead. The gate
    is the serving app: ``tortoise.hosted_api`` is only imported in the
    hosted deployment.
    """
    # C5 #2114 (final-gate P2): packs are DEFAULT-graph org-level state —
    # graph-bound keys rejected (REST twin upload_pack_manifest parity; a
    # per-graph install would orphan pack state list_packs never sees).
    _reject_graph_bound_mcp_org_surface("pack install")
    import sys as _sys

    from tortoise.pack_manifest_store import upsert_tenant_manifest
    if _sys.modules.get("tortoise.hosted_api") is None:
        return {
            "installed": False,
            "error": "tortoise_pack_install is HOSTED-only — on self-host add "
                      "custom packs via the filesystem packs dir "
                      "(TORTOISE_PACKS_DIR) + the 'tortoise pack' CLI.",
        }
    if _transport_mode.get() != "http":
        return {"installed": False,
                "error": "pack install requires the HTTP transport."}
    try:
        record = upsert_tenant_manifest(_get_org_sdk(), manifest_yaml)
    except ValueError as e:
        return {"installed": False, "validation_errors": [str(e)]}
    return {"installed": True, **record}


def tortoise_list_tags() -> list[dict]:
    """List all Tag names with count of tagged Points. Where tags are USED.
    Alias → overview(section='tags') (epic #888 W3)."""
    return _safe(_get_org_sdk().list_tags)


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
    return _safe(_get_org_sdk().get_point, id)


# ── Entity Resolution (GAP-01 #6987) ──────────────────────────

def tortoise_suggest_entry_points(query: str, limit: int = 5,
                                  kind_filter: str | None = None) -> list[dict]:
    """Entity resolution — NL query → matching entities from the graph.

    Uses hybrid search (tortoise_fts_query) for semantic entity resolution.
    Falls back to string match (CONTAINS) if hybrid search unavailable.
    Returns [{id, name, kind, confidence}] sorted by confidence DESC.
    """
    try:
        results = _safe(_get_org_sdk().tortoise_fts_query, query, kind=kind_filter, limit=limit)
        # #3926: a _safe failure is an _SafeError (never a list), so the
        # list check alone is the failure gate — a row whose props carry a
        # user key named "error" must still resolve.
        if isinstance(results, list) and results:
            return [{"id": r["id"], "name": r.get("content", ""),
                     "kind": r.get("point_kind", ""),
                     "confidence": round(
                         0.5 * r.get("scores", {}).get("rrf", 0.0) +
                         0.5 * r.get("ep", {}).get("confidence_mean", 0.0), 4)}
                    for r in results]
    except Exception:
        pass
    return _safe(_get_org_sdk().suggest_entry_points, query, limit=limit, kind_filter=kind_filter)


# ── Semantic Search (#6990) ────────────────────────────────────

def tortoise_search(query: str | None = None, kind: str | None = None,
                    threshold: float = 0.0, limit: int = 10,
                    min_confidence: float = 0.0,
                    order_by: str = "relevance",
                    entity_type: str = "point",
                    relationship_filter: str | None = None,
                    traversal_path: str | None = None) -> list[dict]:
    """Hybrid search with RRF fusion + EP annotation.

    entity_type: 'point' (default), 'event', 'subject', 'document', 'object', 'operator', or 'source'.
    Full-scan mode: omit query, set kind → all Points of kind (current-view
    by default — terminal statuses retracted/superseded/outdated/archived/
    deprecated excluded, #1391; the SDK tortoise_fts_query exposes
    include_terminal=True for the complete supersede-structure view).
    Best-match mode: provide query → RRF fusion of FTS + vector + structural.

    Point results annotated with EP breakdown. confidence_mean is THE point's
    confidence (belief mean α/(α+β): persisted posterior when EP has run,
    else persisted prior mean, else neutral 0.5) — agrees with
    tortoise_get_confidence / recall for the same point. contention is the
    structural edge-ratio family (a different quantity). min_confidence
    filters on confidence_mean (belief); defaults to 0.0 (no filter).

    relationship_filter: 'predicate:target_id' — only return points connected to
        target_id via an operator with label=predicate
        (e.g., 'addresses:customerSegment-1').
    traversal_path: 'FromKind→ToKind' — only return points that participate in
        a relationship path of the form FromKind→ToKind (e.g., 'Product→Feature').

    order_by (#25, #560):
      - 'relevance' (default): pure RRF fusion order (FTS + vector + structural).
      - 'confidence': sort by the PERSISTED EP confidence (n.confidence — the
        same belief mean ep.confidence_mean carries, post-#2206).
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
    return _safe(_get_org_sdk().tortoise_fts_query, query, kind=kind,
                 threshold=threshold, limit=limit,
                 entity_type=entity_type,
                 min_confidence=min_confidence, order_by=order_by,
                 relationship_filter=relationship_filter,
                 traversal_path=traversal_path)



def tortoise_expand_relationships(point_id: str) -> list[dict]:
    """Full relationship payload for ONE Point, incl. each related point's content.

    #1353 D14 — the expand side of the list/expand split: tortoise_search returns
    bounded state entries (IDs + labels + direction + peer state, no content);
    use this to read a single point's complete relationships, including the
    related points' full text, on demand (single-point fan-out is trivially cheap).
    """
    return _safe(_get_org_sdk().expand_relationships, point_id)


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
            _get_org_sdk().recall_gaps, query, kind=kind,
            limit=limit if limit is not None else 20,
            min_load=min_load if min_load is not None else _RECALL_GAPS_DEFAULTS["min_load"],
            max_support=max_support if max_support is not None else _RECALL_GAPS_DEFAULTS["max_support"],
            include_superseded=include_superseded,
        )
    elif mode == "subgraph":
        results = _safe(
            _get_org_sdk().recall_subgraph, seed or query,
            depth=depth if depth is not None else _RECALL_SUBGRAPH_DEFAULTS["depth"],
            completeness=completeness if completeness is not None else _RECALL_SUBGRAPH_DEFAULTS["completeness"],
            max_nodes=max_nodes if max_nodes is not None else 500,
        )
    elif mode == "custom":
        # Raw params, full control — no preset clamping.
        results = _safe(
            _get_org_sdk().recall_state, query, kind=kind,
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
            _get_org_sdk().recall_state, query, kind=kind,
            limit=limit if limit is not None else 10,
            include_superseded=include_superseded,
            min_confidence=min_confidence,
            relevance_exp=relevance_exp if relevance_exp is not None else defaults["relevance_exp"],
            confidence_exp=confidence_exp if confidence_exp is not None else defaults["confidence_exp"],
            centrality_weight=centrality_weight if centrality_weight is not None else defaults["centrality_weight"],
        )

    # _safe returns an _SafeError on SDK exceptions — surface it at the TOP
    # level so consumers never mis-parse results (#3926: never key presence).
    if isinstance(results, _SafeError):
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
                    require_calibration: bool = True) -> dict:
    """Compute confidence via EP belief propagation. Returns {iterations, converged, confidences}.

    Pass anchors=[point_ids] for BFS subgraph selection.
    Calibration is required by default (#344): raises CalibrationError on
    uncalibrated graphs. Pass require_calibration=False to run on topology
    alone (explicit opt-out only).
    max_hops: BFS depth from anchors (default 1).
    rel_filter: edge types — "IMPL", "NAND", or "IMPL|NAND" (default).
    direction: IMPL traversal — "incoming", "outgoing", or "both" (default).

    #395 (delta C): no-arg (no factors/anchors) runs LOCAL EP over the dirty
    subgraph on stdio/embedded. Over HTTP the request-scoped SDK has no dirty
    state, so no-arg returns diagnostic "no_dirty_state_http" — factors or
    anchors are REQUIRED over HTTP. anchors + max_hops=None over HTTP is
    clamped to a deterministic bounded default (whole-component BFS is
    unbounded on a multi-tenant surface).
    """
    # #395 (delta C): HTTP no-arg is the disable-contract — a fresh
    # request-scoped SDK (mcp_auth.py:69) always has empty _dirty_roots, so
    # the no-arg path would silently return {} where today it runs whole-
    # graph EP (the #7288 timeout surface). Transport-aware branch lives in
    # the handler (precedent: _transport_mode checks at mcp_server.py:962).
    # #1163: the graph is now the dirty-state source of truth — hydrate the
    # request-scoped SDK's persisted dirty roots; the diagnostic only fires
    # when the graph is TRULY clean (no persisted dirty state).
    if _transport_mode.get() == "http" and factors is None and anchors is None:
        sdk = _get_org_sdk()
        sdk._hydrate_dirty_roots()
        if not sdk._dirty_roots:
            return {"iterations": 0, "converged": True, "confidences": {},
                    "diagnostic": "no_dirty_state_http"}
    # anchors + max_hops=None over HTTP → clamp to a deterministic bounded
    # default (max_hops=None now means full connected subgraph — unbounded
    # BFS on a multi-tenant hosted surface is not acceptable).
    if _transport_mode.get() == "http" and max_hops is None:
        max_hops = 1
    factors = _parse(factors)
    evidence = _parse(evidence)
    anchors = _parse(anchors)
    return _safe(_get_org_sdk().compute_confidence, factors, evidence,
                 anchors=anchors,
                 max_hops=max_hops, rel_filter=rel_filter,
                 direction=direction,
                 require_calibration=require_calibration)


def tortoise_set_point_baseline(claim_id: str, alpha: float, beta: float) -> dict:
    """Set Beta prior evidence for a claim."""
    return _safe(_get_org_sdk().set_point_baseline, claim_id, alpha, beta)


def tortoise_get_confidence(claim_id: str,
                            require_calibration: bool | None = None) -> dict:
    """Get EP confidence for a claim: {mean, variance, alpha, beta}.

    #1157: the (possibly writing) lazy-dream read is gated on calibration
    state like the other EP surfaces — CalibrationError when evidence points
    are uncalibrated. None (default) resolves to the shared fail-closed
    posture TORTOISE_EP_REQUIRE_CALIBRATION (default True, post-#344); pass
    False explicitly to opt out.
    """
    return _safe(_get_org_sdk().get_confidence, claim_id,
                 require_calibration=require_calibration)


def tortoise_calibrate_summary() -> list[dict]:
    """Audit graph calibration state. Returns per-point guidance."""
    return _safe(_get_org_sdk().calibrate_summary)


def tortoise_dream(full: bool = False, dirty_only: bool = True,
                   max_hops: int = 2,
                   require_calibration: bool | None = None,
                   mode: str | None = None,
                   budget: int | None = None) -> dict:
    """Run EP stabilization (dreaming, #85).

    Stabilizes confidence values after batch writes without an explicit
    compute_confidence call. Default: dreams the accumulated dirty subgraph
    (incremental). Set full=True for whole-graph stabilization.

    mode (epic 903-C6, #1244): explicit strategy override ∈ {"local",
    "stale-first", "full"} — wins over full/dirty_only (I1 precedence);
    None → auto-select. budget: per-pass operator cap; an explicit budget a
    full pass cannot satisfy raises BudgetExceededError (→ ERR_QUOTA).

    #1157: the EP write is gated on calibration state — CalibrationError when
    evidence points are uncalibrated. None (default) resolves to the shared
    fail-closed posture TORTOISE_EP_REQUIRE_CALIBRATION (default True,
    post-#344); pass False explicitly to opt out.

    #329: EXCLUDED from tenant HTTP — whole-graph EP is CPU-heavy
    (operator/stdio only; REST /v1/dream is separately budgeted).
    """
    if _transport_mode.get() == "http":
        return _http_excluded_error()
    # Epic 903-C11 (#1249): validate mode BEFORE _safe (the wrapper swallows
    # exceptions into plain error dicts) — unknown mode → ERR_INVALID
    # (E2E-8.3 convention — named valid values in the message).
    if mode is not None and mode not in ("local", "stale-first", "full"):
        return {"error": (
            f"unknown dream mode {mode!r} — expected one of "
            "'local', 'stale-first', 'full'"), "code": ERR_INVALID}
    return _safe(_get_org_sdk().dream, dirty_only=dirty_only, full=full,
                 max_hops=max_hops,
                 require_calibration=require_calibration,
                 mode=mode, budget=budget)


def tortoise_dream_health() -> dict:
    """Dream observability (epic 903-C7, #1245): the zero-output silent-death
    alarm verdict + health record (last pass, coverage, failure rate,
    region_attempts, warm-start savings). Embedded call-triggered evaluator
    (no daemon per #176)."""
    if _transport_mode.get() == "http":
        return _http_excluded_error()
    return _safe(_get_org_sdk().dream_health_check)


def tortoise_update_point(id: str, props: Any) -> dict:
    """Update properties on a Point. Safe — modifies one Point only."""
    props = _parse(props)
    _reject = _reject_server_managed_props(props)
    if _reject:
        return {"error": _reject, "code": ERR_INVALID}
    return _safe(_quota_gated(_get_org_sdk().update_point, "points"), id, **(props or {}))

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
    return _safe(_quota_gated(_get_org_sdk().create_operator, "points", abuse_weight=1), op_type, source_id, target_ids,
                 direction=direction)


def tortoise_annotate_operator(id: str, bias: float, precision: float,
                                consistency: float, directness: float) -> dict:
    """Annotate an operator Point with structured epistemic dimensions.

    bias: 0-1 — hidden stake beyond stated position.
    precision: 0-1 — how narrow/well-defined the relevance claim is.
    consistency: 0-1 — stability across contexts.
    directness: 0-1 — how directly source bears on target.
    """
    return _safe(_get_org_sdk().annotate_operator, id, bias, precision, consistency, directness)


def tortoise_get_operator(id: str) -> dict:
    """Get an operator Point by ID. Returns all properties including annotation dimensions.
    Raises error if the Point is not an operator.
    Alias → get(id, type='operator') (epic #888 W3)."""
    point = _safe(_get_org_sdk().get_point, id)
    if isinstance(point, dict) and point and not point.get("is_operator"):
        return {"error": f"Point {id!r} is not an operator"}
    return point


def tortoise_mitigate_operator(id: str, reason: str, strength: float = 0.5,
                               credibility: str | int | float | None = None) -> dict:
    """Create a mitigation Point that modulates an operator's edge strength.

    MITIGATION STRENGTH SEMANTICS — single source of truth is the
    tortoise/weights.py module docstring (#2315; product decision
    2026-09-07: mitigation is a GRADED DAMPENER, not a refutation).
    Sanctioned band: [0.10, 0.50] — 0.10 minor caveat (weakest), 0.50
    major counter-evidence (STRONGEST; never >0.50 — would invert the
    claim, use NAND). Formula: w_eff = w * (1 - strength); EP reads
    mitigation_strength via compute_operator_weight (weights.py). Strength
    is NOT how true the reason is and is NOT fused into the mitigation
    point's prior. The decide tooling clamps to the band before writing.

    reason: Why the edge is weaker than it appears.
    strength: dampening strength in [0.10, 0.50] (0.50 = strong).
    credibility: optional starting belief for the mitigation reason itself
      (plain-language ladder gold/high/medium/low/unverified, T0-T4, or
      numeric 0-4) — stamped 'set-by-author'. Omit for the #2199 system
      starting belief ('medium', provenance 'system-default') so the
      mitigation is a calibrated live evidence point (no CalibrationError).
    Idempotent — second call updates existing mitigation.
    """
    return _safe(_quota_gated(_get_org_sdk().mitigate_operator, "points", abuse_weight=1), id, reason, strength, credibility)


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
    result = _safe(_quota_gated(_get_org_sdk().file_decision, "points",
                          abuse_weight=lambda r, a, k: 1 + len(a[0] or []) + len(a[1] or [])), options, evidence, choice)
    # #3926: gate on the typed failure result, never on key presence.
    if not isinstance(result, _SafeError):
        # #3784: this call IS the observation — a decision was filed.
        _maybe_onboarding_auto_complete(decision_observed=True)
    return result


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
    return _safe(_quota_gated(_get_org_sdk().file_human_approval, "points", abuse_weight=1),
                 approver_id, artifact_id, point_ids, decision_content)


def tortoise_delete_point(id: str, dry_run: bool = False) -> dict:
    """Delete a Point. DESTRUCTIVE — requires human confirmation. Cannot be undone.

    dry_run=True previews the blast radius — the point and every edge that
    would be removed — and changes nothing; dry_run=False (default) deletes.
    """
    if dry_run:
        return _safe(_preview_delete_point, _get_org_sdk(), id)
    return _safe(_get_org_sdk().delete_point_wrapped, id)


def tortoise_invalidate(id: str, corrected_by_id: str,
                        dry_run: bool = False) -> dict:
    """Mark a Point outdated with a CORRECTS edge from the correcting Point.

    The `corrected_by_id` point CORRECTS the invalidated point.
    Returns {invalidated, id, corrected_by}.

    dry_run=True previews the transition (one point outdated, one CORRECTS
    edge added) and changes nothing; dry_run=False (default) applies it.
    """
    if dry_run:
        return _safe(_preview_invalidate, _get_org_sdk(), id, corrected_by_id)
    return _safe(_quota_gated(_get_org_sdk().invalidate_point, "points"), id, corrected_by_id)


def tortoise_supersede(old_id: str, new_id: str, transfer_edges: bool = True,
                       dry_run: bool = False) -> dict:
    """Atomically replace old Point with new — CORRECTS edge + outdated flag.

    transfer_edges=True (default): full supersede — all edges move from old to
    new. transfer_edges=False: invalidate behavior — mark old outdated with a
    CORRECTS edge only (no edge transfer). Absorbs tortoise_invalidate.
    Returns {invalidated, id, corrected_by} (+ edges_transferred when
    transfer_edges=True).

    dry_run=True previews exactly which edges would transfer and changes
    nothing; dry_run=False (default) applies the supersede.
    """
    if dry_run:
        return _safe(_preview_supersede, _get_org_sdk(),
                     old_id, new_id, transfer_edges)
    return _safe(_quota_gated(_get_org_sdk().supersede, "points"),
                 old_id, new_id, transfer_edges=transfer_edges)


def tortoise_retract_point(id: str, dry_run: bool = False) -> dict:
    """Tombstone-retract a Point — status='retracted' (point stays in graph).

    Terminal state transition; default query/list surfaces exclude retracted
    points (opt-in via include_retracted). Raises ValueError if the point is
    missing, is an operator, or is already terminal (the shared terminal
    vocabulary: status in live.TERMINAL_EXCLUDED_STATUSES OR the legacy
    outdated=true flag).

    dry_run=True previews the one-node status transition (and runs the same
    guard, so a rejected input is rejected here too) and changes nothing;
    dry_run=False (default) retracts.
    """
    if dry_run:
        return _safe(_preview_retract_point, _get_org_sdk(), id)
    return _safe(_quota_gated(_get_org_sdk().retract_point, "points"), id)


def tortoise_events_poll(after: str | None = None, types: Any = None,
                         limit: int = 100) -> dict:
    """Poll graph/claim events after an opaque cursor (at-least-once).

    Returns {events: [...], next_cursor}. after=None → tail (oldest retained).
    Expired cursor → structured error ('cursor expired — replay from tail');
    malformed cursor → 'invalid cursor'. types: comma-free list of event types
    (11 registered claim types: PointAdded, OperatorAdded, PointRetracted,
    PointSuperseded, OperatorAnnotated, PointPromoted, OperatorPromoted,
    DedupeRecorded, DedupeRejected, ObjectSuperseded, PointInvalidated)
    or None for all.

    readOnlyHint covers user-visible state: the poll NEVER mutates user
    content. A rare maintenance purge (retention) may run at most once per
    TORTOISE_EVENT_RETENTION_INTERVAL — an internal housekeeping DELETE of
    expired :GraphEvent nodes, gated so steady-state polls are read-only.
    """
    if types is not None:
        types = _parse(types)
        if not isinstance(types, list):
            types = [types]
    return _safe(_get_org_sdk().events_poll, after=after, types=types, limit=limit)



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
    proj = _get_org_sdk()._get_proj()
    # #236: HTTP mode ignores user-supplied graph_name — org graph authoritative
    # (cross-tenant injection guard). Stdio mode honors it (operator use).
    if _transport_mode.get() == "http":
        graph_name = f"org_{_current_org_id.get()}"
    return _safe(entityProfile, proj.db, graph_name, entity_id,
                  hops=hops, pointKind=pointKind, confidenceMin=confidenceMin)


def tortoise_traverse(entity_id: str, max_hops: int = 2,
                       graph_name: str = "tortoise") -> dict:
    """Multi-hop graph traversal from entity following ALL relationship types.

    Returns {entity: {...}, nodes: [{node, relationship, depth}, ...]}.
    """
    from tortoise.navigation import tortoise_traverse as _traverse
    proj = _get_org_sdk()._get_proj()
    # #236: HTTP mode ignores user-supplied graph_name (cross-tenant guard)
    if _transport_mode.get() == "http":
        graph_name = f"org_{_current_org_id.get()}"
    return _safe(_traverse, proj.db, graph_name, entity_id, max_hops)


def main():
    _transport_mode.set("stdio")
    # #2203: the stdio server must terminate its embedded redis-server child
    # when the parent is killed — SIGTERM from the harness/session teardown,
    # SIGHUP on terminal/session death, SIGINT when the process started with
    # it ignored (non-tty stdin). The guard's handler closes every live
    # embedded server INLINE (registry of guarded FalkorDB clients) and then
    # re-raises the signal, so the server dies with the parent on any of
    # those kills — no dependence on atexit or on unwinding the event loop.
    # (Client disconnect = stdin EOF → mcp.run returns → the explicit close
    # after it below.) Idempotent.
    from tortoise.embedded_lifecycle import (
        close_embedded_clients,
        install_embedded_signal_cleanup,
    )
    install_embedded_signal_cleanup()
    # #2204: announce dev mode (no auth) at the actual serve start, NOT at
    # module import — incidental importers (hosted_api, doctor, tests) must
    # stay quiet. Stdio is the only path that cannot carry auth headers, so
    # the announcement lives here (python -m tortoise.mcp_server and
    # `tortoise serve` both funnel through main()).
    if _is_dev_mode():
        _log.warning("TORTOISE_API_KEY not set — running in dev mode (no auth)")
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
    try:
        mcp.run(transport="stdio")
    finally:
        # #2203: deterministic teardown when the stdio session ends (client
        # disconnect / stdin EOF / abnormal session end) — close every
        # embedded server this process opened NOW instead of relying on
        # atexit ordering; a no-op when nothing was opened (docker-URI
        # mode), idempotent (already-closed clients skip).
        close_embedded_clients()


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
    return _safe(_quota_gated(_get_org_sdk().checkpoint, "points",
                          abuse_weight=lambda r, a, k: int((r or {}).get("filed") or 0)), items,
                 agent_name=agent_name, threshold=threshold)


def tortoise_diary_write(agent_name: str, entry: str,
                         topic: str | None = None,
                         wing: str | None = None) -> dict:
    """Write an agent diary entry (AAAK format suggested).
    Creates a Point with pointKind=diary, authoredBy=agent.
    """
    return _safe(_quota_gated(_get_org_sdk().diary_write, "points", abuse_weight=1), agent_name, entry, topic=topic, wing=wing)


def tortoise_diary_read(agent_name: str, last_n: int = 10,
                        wing: str | None = None) -> list[dict]:
    """Read recent diary entries for an agent, newest first."""
    return _safe(_get_org_sdk().diary_read, agent_name, last_n, wing=wing)


def tortoise_list_graphs() -> list[str]:
    """List graph names. HTTP: only the calling org's own graphs (exact
    `org_{org_id}` / legacy `team_{org_id}` equality — no cross-tenant
    enumeration). Stdio: full list (operator context).
    Alias → overview(section='graphs') (epic #888 W3)."""
    graphs = _safe(_get_org_sdk().list_graphs)
    if not isinstance(graphs, list):
        return graphs
    if _transport_mode.get() == "http":
        from tortoise.mcp_auth import _current_org_id
        org_id = _current_org_id.get()
        # #3543: the tenant graph is `org_{org_id}` post-rename and
        # `team_{org_id}` before it; no data migration rewrites the stored
        # namespace, so both spellings are the calling org's own graph.
        # Same dual probe as hosted_api._graph_has_org_namespace — the sites
        # must agree or a legacy org's graphs become invisible here alone.
        own = {f"org_{org_id}", f"team_{org_id}"} if org_id else set()
        return [g for g in graphs if g in own]
    return graphs


def tortoise_status() -> dict:
    """Graph health + entity counts + FalkorDB connectivity.
    Returns {connected, counts: {Point, Event, ...}, total_entities}.
    Alias → overview(section='status') (epic #888 W3).
    """
    return _safe(_get_org_sdk().status)


def tortoise_health() -> dict:
    """Health check + basic metrics: graph_size, last_ingest, error_count, uptime.
    Alias → overview(section='health') (epic #888 W3).

    #2202 (health-truthful): probes the SDK THIS server actually serves —
    the request-scoped org SDK over HTTP and the base SDK over stdio — so the
    report reflects the caller's own graph, never monitoring's module-global
    handle.
    #3143 correction: this tool and hosted /health do NOT probe the same
    graph, so they can disagree about reachability. This tool probes the
    CALLER'S ORG graph (``_get_org_sdk()``); hosted /health probes the
    DEFAULT graph (``_make_sdk(namespace=None)`` through
    ``hosted_api._probe_db()``). They share a FalkorDB SERVER, not a graph,
    and the probe is not a bare reachability check: ``_probe_once`` runs
    ``sdk._get_proj()`` (connect + version probe + ``_ensure_indexes()``),
    whose cost scales with the PROBED graph. So an org graph can time out
    while the default graph answers ok — #3143 is that case. Their budgets
    differ by design too: this tool gives the reachability query a fresh
    ``PROBE_TIMEOUT``, /health spends one shared budget across both phases
    (its cached-verdict staleness window is #3062).
    The pre-#2202 code probed monitoring's module-global handle, which ONLY
    the stdio entrypoint (main()) registers: on the HTTP daemon/hosted
    surfaces it stayed None and every call reported degraded/no_sdk_registered
    while /health (fresh SDK probe) said ok — the first call every onboarding
    script makes lied. graph_size likewise counts the SERVED graph, never an
    empty unregistered handle.

    #3143 (health-truthful): the probe's 1.5s budget was written to bound the
    sub-millisecond ``RETURN 1`` reachability query, but it also bounded the
    projection cold-start (``_get_proj()``: connect + ``_ensure_indexes()`` —
    ~28 round trips, and an index build over the whole graph when one is
    missing). That cost scales with graph size, so a large, fully-reachable
    org (9,019 entities) timed out during setup and reported
    ``degraded``/``graph_size 0`` while ``tortoise_status`` worked. The tool
    now passes the cold-start allowance it always pays for — it builds a
    request-scoped SDK per call — while the platform liveness gate keeps the
    tight fast-degrade bound. The allowance is resolved at CALL time
    (``monitoring.probe_setup_timeout()``) so ``TORTOISE_PROBE_SETUP_TIMEOUT``
    set in the repo-root ``.env`` — loaded after this module imports
    ``tortoise.monitoring`` — is honoured instead of frozen at import."""
    # #236: route through _safe() so every tool is gated (defense-in-depth;
    # reachable only post-auth over HTTP).
    # #3143: pass the cold-start allowance (call-time resolved) so a reachable
    # graph whose cold-start exceeds /health's shared budget is reported ok
    # with its real graph_size instead of degraded/0.
    return _safe(lambda: monitoring.metrics(
        sdk=_get_org_sdk(),
        setup_timeout=monitoring.probe_setup_timeout(),
    ))


def tortoise_session_context() -> dict:
    """Return 'what happened last session' — diary entries, recent Points, Events, confidence changes.
    Returns {no_prior_sessions, diary_entries, recent_points, recent_events, confidence_changes}.
    """
    return _safe(_get_org_sdk().session_context)


def tortoise_issue_insight(title: str, body: str | None = None,
                           repo: str | None = None, limit: int = 2) -> dict:
    """Return a compact 'there's more in the graph' insight for a would-be issue.

    Call BEFORE filing an issue: surfaces cross-session decisions / EP-tagged
    claims matching the title (semantic stage) plus prior indexed issues for
    the repo (repo stage, when repo= given). Fail-closed: empty graph ->
    no_prior_knowledge; populated graph + repo with zero indexed points ->
    repo_not_indexed. Returns {has_prior, data_points, insight, more_in_graph}.
    """
    return _safe(_get_org_sdk().issue_insight, title, body=body, repo=repo, limit=limit)


def tortoise_ingest_corpus(directory: str) -> dict:
    """Batch document ingestion — walk directory, parse YAML frontmatter
    from .md files, create/update Document nodes.
    Returns {ingested, updated, skipped}.

    #236: EXCLUDED from tenant HTTP — walks server filesystem with a
    user-supplied path (path-traversal vector). Stdio-only.
    """
    if _transport_mode.get() == "http":
        return _http_excluded_error()
    return _safe(_get_org_sdk().ingest_corpus, directory)

# ── Taxonomy ─────────────────────────────────────────────────

def tortoise_taxonomy() -> dict[str, int]:
    """Count entities by node label. Returns {Point: N, Event: N, Subject: N, Object: N, Document: N}.
    Alias → overview(section='taxonomy') (epic #888 W3)."""
    return _safe(_get_org_sdk().taxonomy)


def tortoise_list_topics(entity_id: str) -> dict:
    """entityProfile lite for an entity. Returns {id, pointKind, neighbors, neighborCounts}.
    Alias → overview(section='topics', entity_id=...) (epic #888 W3)."""
    return _safe(_get_org_sdk().list_topics, entity_id)


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
    return _safe(_get_org_sdk().topic_summarize, topic,
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
    — plus, with the W4 enrichment flag ON (epic #2080), an additive ``why``
    array of canonical why-blocks (support_chain/ep/conflicts/supersession/
    tradeoffs/dig_deeper) for the Points surfaced in ``raw`` (zero-LLM,
    bounded batch reads; absent with the flag OFF — byte-identical).
    """
    from tortoise.analyze import analyze
    from tortoise.navigation import entityProfile

    anchor_ids = _parse(anchor_ids)

    entity_subgraph_ids = None
    if entityId:
        try:
            proj = _get_org_sdk()._get_proj()
            # #236: HTTP mode must use the org graph, NOT the hardcoded
            # "tortoise" graph — that hardcode bypasses org isolation via
            # db.select_graph() (cross-tenant read). Stdio keeps "tortoise".
            gname = f"org_{_current_org_id.get()}" if _transport_mode.get() == "http" else "tortoise"
            profile = entityProfile(proj.db, gname, entityId, hops=2)
            ids = {entityId}
            for category in profile.get("connected", {}).values():
                for node in category:
                    if node.get("id"):
                        ids.add(node["id"])
            entity_subgraph_ids = ids
        except Exception:
            pass  # fall back to full-graph analysis

    # #329: bound paid outbound LLM calls per org per minute — beyond budget
    # the tool degrades to keyword-only classification.
    use_llm = _analyze_llm_budget_available()
    result = _safe(analyze, question, _get_org_sdk()._get_proj(),
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
    # W4 (#2101): additive why-layer enrichment — the additive keys ride the
    # ``why`` entries for the Point ids surfaced in ``raw`` (flag-gated;
    # absent with the flag OFF — the response stays byte-identical).
    # Zero-LLM, bounded batch reads, fail-open (no key on assembly error).
    if isinstance(result, dict) and result.get("raw"):
        try:
            from tortoise.why import (  # noqa: I001
                assemble_why_blocks, point_ids_in_raw, w4_enrichment_enabled,
            )
            if not w4_enrichment_enabled():
                return result
            _proj = _get_org_sdk()._get_proj()
            _ids = point_ids_in_raw(result["raw"])[:20]
            if _ids:
                _blocks = assemble_why_blocks(_proj, _ids)
                result["why"] = [b for pid, b in _blocks.items()
                                  if pid in _ids]
        except Exception as e:  # noqa: BLE001, RUF100 — fail-open, never break the turn
            _log.warning("W4 why-layer enrichment failed (analyze): %s", e)
            pass
    return result


# ── P1-3: Staleness Detection ─────────────────────────────────

def tortoise_stale(days: int = 30, limit: int = 50) -> dict:
    """Find Points not updated in N days. Returns {stale, count, cutoff, limit}.
    Alias → overview(section='stale', days=, limit=) (epic #888 W3)."""
    return _safe(_get_org_sdk().stale_points, days=days, limit=limit)


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
    scope: optional topic text or Point id — narrows the candidate pool to the
        retrieval-NEAREST points. A focus filter, not an exact-match or
        relevance gate: near-but-not-exact is intended, and a scope matching
        nothing normally still returns its nearest neighbours — though a
        degraded single-leg run can score every hit 0 and return nothing. The
        result is empty when retrieval returns nothing, when every retrieved id
        is dropped (RRF score <= 0, e.g. the TF-IDF fallback, or an
        operator/terminal/outdated / [MITIGATION] row), or — for mode=add —
        when no candidate pair clears similarity_threshold. mode=prune applies
        no similarity bar.

    Never mutates the graph.
    """
    return _safe(_get_org_sdk().review_connections, mode=mode, scope=scope)


def tortoise_find_cross_lens_candidates(
    threshold: float = 0.72,
    max_candidates: int = 200,
    routing: str = "truth",
    top_k: int = 20,
) -> dict:
    """Cross-lens candidate discovery (READ-ONLY, #438 bring-your-own-agent).

    Surface unverified candidate pairs between Points from DIFFERENT sources
    (cross-stream discovery over the vector index) with lens pair, cosine
    similarity, point context, and dedup vs existing operators. The payload
    carries a single #901 routing field ("truth"|"relevance") but stays
    NEUTRAL — no op_type hint; the customer agent decides semantics and
    writes operators via the normal API (no in-repo verifier, #438 D7).
    Gated on registered sourceKind (any tier, D3); hard cap 200
    candidates/cycle (D4). top_k is hard-clamped to 100 so an agent cannot
    inflate the per-cycle recall budget. Empty results (not errors) when
    there is nothing to see (D8). Never mutates the graph.
    """
    return _safe(_get_org_sdk().get_cross_lens_candidates,
                 threshold=threshold, max_candidates=max_candidates,
                 routing=routing, top_k=top_k)


def tortoise_provenance(point_id: str) -> dict:
    """Provenance chain — "Who decided this?" Follows authoredBy → Subject → delegation."""
    return _safe(_get_org_sdk().provenance, point_id)


# ── Multi-tenancy (#7001) ────────────────────────────────────

def tortoise_org_create(name: str) -> dict:
    """Create isolated org graph via FalkorDB select_graph.
    Generates a per-org API key. Returns {name, graph_name, api_key, id}.
    destructiveHint=true — creates persistent resources.
    idempotentHint=false — duplicate org names raise an error.

    #236: EXCLUDED from tenant HTTP — provisioning belongs to
    /internal/provision behind FASTAPI_INTERNAL_KEY (privilege boundary).
    Stdio-only.
    """
    if _transport_mode.get() == "http":
        return _http_excluded_error()
    return _safe(_get_org_sdk().org_create, name)


# ── Entity CRUD (ONTOLOGY v2.5) ───────────────────────────────

# ── Write/revise consolidation (epic #888 W2) ──────────────────────

def tortoise_create_entity(type: str, name: str, props: Any = None) -> dict:
    """Create an entity — type: subject|object|event|document.

    Event entities wire about* edges from aboutSubject/aboutObject/aboutPoint/
    aboutDocument props. Returns {node, nudges} — nudges suggest IMPL/NAND/
    mitigate connections to related Points (advisory, not enforced).
    """
    props = _parse(props)
    _reject = _reject_server_managed_props(props)
    if _reject:
        return {"error": _reject, "code": ERR_INVALID}
    return _safe(_quota_gated(_get_org_sdk().create_entity, "points"),
                 type, name, **(props or {}))


def tortoise_update(id: str, props: Any = None) -> dict:
    """Update a Point OR entity by id. Points get point-lifecycle semantics
    (draft→live promote via status, version increment for Point:Object,
    status validation); entities get a plain property update."""
    props = _parse(props)
    _reject = _reject_server_managed_props(props)
    if _reject:
        return {"error": _reject, "code": ERR_INVALID}
    return _safe(_quota_gated(_get_org_sdk().update, "points"),
                 id, **(props or {}))


def tortoise_delete(id: str, dry_run: bool = False) -> dict:
    """Delete a Point or entity by id. DESTRUCTIVE — requires human confirmation.

    dry_run=True previews the blast radius — which node resolves, and every
    edge that would go with it — and changes nothing; dry_run=False
    (default) deletes.
    """
    if dry_run:
        return _safe(_preview_delete, _get_org_sdk(), id)
    result = _safe(_get_org_sdk().delete, id)
    if isinstance(result, _SafeError):
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
        return _safe(_quota_gated(_get_org_sdk().mitigate_operator, "points", abuse_weight=1),
                     id, reason, strength)
    if action == "annotate":
        dims = (bias, precision, consistency, directness)
        if any(d is None for d in dims):
            return {"error": "operator_action(action='annotate') requires "
                              "bias, precision, consistency, directness"}
        return _safe(_quota_gated(_get_org_sdk().annotate_operator, "points"),
                     id, *dims)
    return {"error": f"operator_action: unknown action {action!r} — "
                      f"must be 'mitigate' or 'annotate'"}


def tortoise_create_subject(name: str, subjectKind: str, props: Any = None) -> dict:
    """Create a Subject node (org, role, organization, person)."""
    props = _parse(props)
    _reject = _reject_server_managed_props(props)
    if _reject:
        return {"error": _reject, "code": ERR_INVALID}
    return _safe(_quota_gated(_get_org_sdk().create_subject, "points"), name, subjectKind, **(props or {}))

def tortoise_create_object(name: str, objectKind: str, props: Any = None) -> dict:
    """Create an Object node (product, customer, skill, etc.)."""
    props = _parse(props)
    _reject = _reject_server_managed_props(props)
    if _reject:
        return {"error": _reject, "code": ERR_INVALID}
    return _safe(_quota_gated(_get_org_sdk().create_object, "points"), name, objectKind, **(props or {}))

def tortoise_create_event(name: str, eventKind: str, props: Any = None) -> dict:
    """Create an Event node (meeting, decision, deployment, etc.)."""
    props = _parse(props)
    _reject = _reject_server_managed_props(props)
    if _reject:
        return {"error": _reject, "code": ERR_INVALID}
    return _safe(_quota_gated(_get_org_sdk().create_event, "points"), name, eventKind, **(props or {}))


def tortoise_get_events(eventKind: str | None = None, limit: int = 20) -> list[dict]:
    """Get recent Events, optionally filtered by eventKind (e.g. 'AgentSession').
    Alias → get(id, type='events', limit=) (epic #888 W3)."""
    return _safe(_get_org_sdk().get_events, eventKind=eventKind, limit=limit)

def tortoise_get_session(session_id: str) -> dict:
    """Get a single agent session Event by session_id.
    Alias → get(id, type='session') (epic #888 W3)."""
    return _safe(_get_org_sdk().get_session, session_id)

def tortoise_index_sessions(directory: str, extract_metadata: bool = True, llm_model: str | None = None) -> dict:
    """Index session .md files as AgentSession Events. Returns {ingested, updated, skipped, failed, errors}.

    #236: EXCLUDED from tenant HTTP — walks server filesystem with a
    user-supplied path (path-traversal vector, same as ingest_corpus). Stdio-only.
    """
    if _transport_mode.get() == "http":
        return _http_excluded_error()
    if not os.path.isdir(directory):
        return {"error": f"Directory not found: {directory!r}. Provide a valid path to a directory containing .md session files."}
    return _safe(_get_org_sdk().index_sessions, directory, extract_metadata=extract_metadata, llm_model=llm_model)

def tortoise_index_files(directory: str, corpus_name: str | None = None,
                        extract_metadata: bool = False) -> dict:
    """Index a corpus directory of .md files as Sources + Events/Documents
    (epic #900 — the unified index path). Returns the honest §3.1 summary
    (file_count, indexed, updated, skipped, failed, aborted, ignored,
    errors[], by_kind). Idempotent: re-runs converge to skipped. On a shared
    graph, give each corpus a unique corpus_name (default = directory
    basename) to avoid cross-corpus url collisions.

    #329: EXCLUDED from tenant HTTP — walks server filesystem with a
    user-supplied path (path-traversal vector, same as index_sessions /
    ingest_corpus). Stdio-only. Node/edge-creating → quota-gated.
    """
    if _transport_mode.get() == "http":
        return _http_excluded_error()
    if not os.path.isdir(directory):
        return {"error": f"Directory not found: {directory!r}. Provide a valid "
                         f"path to a directory containing .md files."}
    return _safe(_quota_gated(_get_org_sdk().index_directory, "points"),
                 directory, corpus_name=corpus_name,
                 extract_metadata=extract_metadata)

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
    return _safe(_get_org_sdk().search_sessions, query, agent=agent, topics=topics_list,
                 after=after, before=before, limit=limit, offset=offset)

def tortoise_create_document(title: str, documentKind: str, props: Any = None) -> dict:
    """Create a Document node (research, planDoc, meetingNotes, etc.)."""
    props = _parse(props)
    _reject = _reject_server_managed_props(props)
    if _reject:
        return {"error": _reject, "code": ERR_INVALID}
    return _safe(_quota_gated(_get_org_sdk().create_document, "points"), title, documentKind, **(props or {}))

def tortoise_create_source(url: str, sourceKind: str, tier: str | None = None,
                           sourceDate: str | None = None, props: Any = None) -> dict:
    """Create a Source node for provenance (document, web, db, etc.).

    Sources track content origin — url is the permalink key. Points link to
    Sources via extractedFrom edge (Ontology v2.5). ``tier`` (T0-T4) stores the
    credibility tier on ``credibilityTier`` (dual-write with tier-form
    sourceKind); ``sourceDate`` is the evidence-age clock for recency decay.
    """
    props = _parse(props) or {}
    _reject = _reject_server_managed_props(props)
    if _reject:
        return {"error": _reject, "code": ERR_INVALID}
    # tier/sourceDate are first-class kwargs (#398) — pop from props if a legacy
    # caller passed them there (kwarg wins; avoids TypeError on splat).
    props.pop("tier", None)
    props.pop("sourceDate", None)
    return _safe(_quota_gated(_get_org_sdk().create_source, "points"), url, sourceKind,
                 tier=tier, sourceDate=sourceDate, **props)


# #2204: decorator removed — the tool is registered by the registry adapter
# (TOOL_REGISTRY entry carries the same annotations; see module bottom). The
# stale @mcp.tool() decorator double-registered the name with fastmcp's local
# provider ("Component already exists" noise at import).
def tortoise_get_source_reliability(url: str) -> dict:
    """Derive a Source's reliability (0-1) — query-time, cache-consistency-checked.

    Reliability is the mean of the same modulated prior EP uses as base weight
    (tier + recency decay + reputation-weighted agent assessments). Untiered +
    unassessed → None. NOTE: refreshes the documented reliability cache on the
    Source node (write-through projection), so this tool is not read-only.
    """
    return _safe(_get_org_sdk().get_source_reliability, url)


# #2204: decorator removed — registry adapter owns registration (see
# tortoise_create_source note).
def tortoise_assess_source(url: str, assessor: str, score: float,
                           rationale: str) -> dict:
    """Record an agent's assessment of a Source (0-1 score + rationale).

    Creates a pointKind='assessment' Statement Point (ontology §2 — evaluations
    are Points, not edges). Latest assessment per (url, assessor) wins; older
    are marked outdated. Weighted by the assessor's reputation snapshot
    (compute_reputation at write time). Feeds the source's reliability factor
    (clamped [0.1, 2.0]).
    """
    return _safe(_quota_gated(_get_org_sdk().assess_source, "points"),
                 url, assessor, score, rationale)


# #2204: decorator removed — registry adapter owns registration (see
# tortoise_create_source note).
def tortoise_set_source_tier(url: str, tier: str) -> dict:
    """Set (or change) a Source's credibility tier (T0-T4). Non-destructive.

    Writes credibilityTier only — never overwrites sourceKind type strings.
    Dirty-marks the inheritance gate + clears the reliability cache so EP and
    reliability reads reflect the new tier promptly.
    """
    return _safe(_get_org_sdk().set_source_tier, url, tier)

def tortoise_get_entity(id: str | None = None, type: str | None = None,
                        limit: int = 20) -> Any:
    """Get any entity by ID, eventId, or url.

    This is the BROAD fetch tool (owner decision, `docs/product/canonical-mcp-tools.md`,
    approval_pr 4120): `type` selects the node kind exactly as `tortoise_get` did, so the
    retirement pointers that name `tortoise_get_entity(id, type=...)` resolve. With no
    `type`, it keeps its narrow meaning — the entity addressed by an id|eventId|url —
    and the SDK method `TortoiseSDK.get_entity` is untouched (the decision separates the
    tool's broad meaning from the SDK's narrow one).
    """
    if type is not None or id is None:
        return tortoise_get(id, type=type, limit=limit)
    return _safe(_get_org_sdk().get_entity, id)

def tortoise_update_entity(id: str, props: Any = None) -> dict:
    """Update any entity's properties."""
    props = _parse(props)
    _reject = _reject_server_managed_props(props)
    if _reject:
        return {"error": _reject, "code": ERR_INVALID}
    return _safe(_quota_gated(_get_org_sdk().update_entity, "points"), id, **(props or {}))

def tortoise_delete_entity(id: str, dry_run: bool = False) -> dict | bool:
    """Delete any entity by ID.

    dry_run=True returns a preview dict naming the node(s) and every edge
    that would be removed, and changes nothing; dry_run=False (default)
    deletes and returns whether a node was found.
    """
    if dry_run:
        return _safe(_preview_delete_entity, _get_org_sdk(), id)
    return _safe(_get_org_sdk().delete_entity, id)

def tortoise_create_edge(source_id: str, target_id: str, predicate: str) -> dict:
    """Create a typed structural edge between two entities (reification rule).

    Relation (predicate): performs, produces, uses, memberOf, ownedBy, managedBy,
    about*, related, dependsOn, etc. Operator-less by default — lazy promotion
    via operator_action(action='mitigate') when mitigation becomes needed.
    Returns {edge, created, nudges}. (Param order kept from the legacy surface:
    source_id, target_id, predicate → SDK create_edge(relation, from_id, to_id).)
    """
    return _safe(_quota_gated(_get_org_sdk().create_edge, "points"),
                 predicate, source_id, target_id)


def tortoise_ingest(bundle: Any = None, granularity: str = "bulk",
                    promotion_policy: str = "gated") -> dict:
    """Heterogeneous bulk write (epic #888 W4) — one call writes points +
    entities + sources + connections coherently. Nodes are written first, then
    the connections between them; connections carrying 'operator' (IMPL/NAND)
    create operator Points (reification rule v3.5 §8), connections carrying
    'relation' stay plain structural edges. Local refs address bundle items.
    Endpoint typing (#2062): operator-routed connections (reify:true /
    mitigation / part-whole) accept plain-Point or Event endpoints (by node
    id; eventId-only Events stay violations); direct-edge connections (plain
    IMPL/NAND) are plain-Point-only.

    granularity='bulk' (default): aggregated {created, ids, nudges}.
    granularity='granular': per-item results for agent step-by-step control.
    promotion_policy='gated' (DEFAULT, Q2): points stay draft, connections
    never promote (operator path: promote_source=False via #780). ANY
    effective status other than 'draft' on a point item is REJECTED under
    gated (INGEST_CONTRACT row 9 — no bypass of the gated contract; case
    variants, nested props={...}, and terminal statuses included; use
    promotion_policy='auto' or promote after ingest via the SDK's
    update_point(status='live')).
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
    # Row-9 guard (shared helper — identical to the SDK's): under gated,
    # points must stay draft — ANY effective status other than the exact
    # canonical "draft" (top-level or nested props={...}) is a violation.
    if promotion_policy == "gated":
        bad = _first_non_draft_status(bundle.get("points"))
        if bad is not None:
            i, st = bad
            return {"error": f"points[{i}] status:{st!r} is not allowed "
                              f"under promotion_policy 'gated' — under "
                              f"gated points stay draft; pass "
                              f"promotion_policy='auto' for explicit live, "
                              f"or keep draft and promote via "
                              f"update_point(status='live')",
                    "code": ERR_INVALID}
    return _safe(_quota_gated(_get_org_sdk().ingest, "points",
                          abuse_weight=lambda r, a, k: int(((r or {}).get("created") or {}).get("points") or 0)),
                 bundle, granularity=granularity, promotion_policy=promotion_policy)

def tortoise_get_governance(subject_id: str) -> list:
    """Get all entities owned by a Subject.
    Alias → get(id, type='governance') (epic #888 W3)."""
    return _safe(_get_org_sdk().get_owned_entities, subject_id)


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


#: #3510 — the no-arg combined summary reports DATA-PROPORTIONAL orient
#: sections as bounded counts, never as their full row array. A section is
#: data-proportional when it yields one row per graph row rather than one row
#: per graph-shape token (a fixed vocabulary) AND IS UNCAPPED — four qualify:
#: `sources` (one row per registered URL), `tags` (one row per :Tag node),
#: `pointkinds` (one row per pointKind PRESENT) and `structure_check` (one row
#: per violating Point). `stale` is also one row per graph row, but it is
#: CAPPED by the caller's `limit` (default 50 rows; still only ~3,000 rows at
#: limit=5000), so it is not folded here. Returning any of the four wholesale
#: made the orientation call more expensive than the list_* calls it was built
#: to replace — measured on an embedded graph: `sources` 35,190 of 36,498 bytes
#: (96.4%) at 300 sources, `tags` 14,000 of 15,187 bytes (92.2%) at 400 tags,
#: `structure_check` 73,490 of 93,829 bytes (78.3%) at 400 orphaned drafts,
#: `pointkinds` 19,200 bytes at 400 distinct kinds. Each wrapped section keeps
#: its full array reachable via its own section=.
_OVERVIEW_SUMMARY_TOP = 20


#: #3510 review (P2) — the folded remainder is a SIBLING field of the summary,
#: never a key inside the group map. `group_field` is free-form graph text (a
#: tag name, a `sourceKind` string), so there is no in-map sentinel string a
#: real group cannot produce: a genuine group named "other" used to be merged
#: with the remainder (3 real `sourceKind="other"` rows reported 14). Kept out
#: of the map, the collision is impossible by construction.
_OVERVIEW_SUMMARY_OTHER = "other"


def _overview_summary(rows: Any, group_field: str, out_field: str,
                      count_field: str | None = None, *,
                      unit: str = "rows") -> Any:
    """Bounded summary of a data-proportional orient section (#3510).

    Returns {total, <out_field>[, with_points][, other]} — never the rows.
    `<out_field>` groups the rows by `group_field`, keeps the
    _OVERVIEW_SUMMARY_TOP largest groups and reports the folded remainder as the
    sibling `other`. The bound is _OVERVIEW_SUMMARY_TOP, NOT the group
    vocabulary: `group_field` is graph text a caller can invent on every row, so
    the vocabulary is unbounded and the top-N cut is what bounds the map (see
    tests/test_orient_direct_consolidation.py::
    test_fold_is_bounded_when_the_group_vocabulary_is_unbounded).

    THE UNIT IS THE POINT — `unit` fixes what every number means (each
    `<out_field>` value, `total` and `other`), or the summary misinforms:

    * ``unit="magnitude"`` — the group's summed `count_field`. Used by `tags`
      and `pointkinds`, where a group is exactly ONE row, so a row count would
      report a literal 1 for every group while throwing away the count the row
      carries (`tortoise_list_tags` reports {"hot": 50}; the summary must say
      50, not 1).
    * ``unit="rows"`` — the group's row count. Used by `sources`, where a group
      aggregates many registered Sources, so the row count (how many sources of
      this kind) is a real magnitude and keeps the registry's mostly-`points:0`
      emptiness visible; and by `structure_check`, whose rows carry no separate
      magnitude, so a violation row's count IS its count of violations.

    The fold is a partition in that unit:
    ``sum(<out_field>.values()) + other == total``, always.

    `with_points` is emitted only when the unit is rows and a `count_field` was
    given: it counts rows whose magnitude is positive — the SAME row unit as
    `total` (e.g. "how many registered sources actually extracted points"). It
    is omitted for a magnitude section, where a row count stranded beside a
    magnitude `total` would be the very unit confusion this parameter exists to
    stop.

    A non-list input is an error envelope from _safe (or a mocked shape) and is
    passed through unchanged.
    """
    if not isinstance(rows, list):
        return rows
    if unit not in ("rows", "magnitude"):
        raise ValueError(f"_overview_summary: unknown unit {unit!r}")
    if unit == "magnitude" and count_field is None:
        raise ValueError("_overview_summary: unit='magnitude' needs a count_field")
    with_points = 0
    groups: dict[str, list[int]] = {}
    for row in rows:
        row = row if isinstance(row, dict) else {}
        magnitude = 0
        if count_field is not None:
            value = row.get(count_field)
            if isinstance(value, (int, float)) and value > 0:
                with_points += 1
                magnitude = int(value)
        raw = row.get(group_field)
        # "" is the group key for a row whose group value is absent or blank —
        # a real blank value and a missing one describe the same thing, so they
        # are the same group. (The previous "unknown" fallback was a synthetic
        # key a real group literally named "unknown" merged into: the same
        # collision class as the `other` remainder this function now keeps out
        # of the map.)
        key = raw if isinstance(raw, str) and raw else ""
        bucket = groups.setdefault(key, [0, 0])
        bucket[0] += 1
        bucket[1] += magnitude

    def _unit_value(bucket: list[int]) -> int:
        return bucket[1] if unit == "magnitude" else bucket[0]

    # The section's own unit ranks first: for `sources` the row count decides
    # (how many sources of this kind), for `tags`/`pointkinds` the row counts all
    # tie at 1 so the summed magnitude is the only thing that can order them
    # (most-used tags first).
    if unit == "magnitude":
        top = sorted(groups.items(),
                     key=lambda kv: (-_unit_value(kv[1]), -kv[1][0], kv[0]))
    else:
        top = sorted(groups.items(),
                     key=lambda kv: (-_unit_value(kv[1]), -kv[1][1], kv[0]))
    bounded = {k: _unit_value(b) for k, b in top[:_OVERVIEW_SUMMARY_TOP]}
    other = sum(_unit_value(b) for _, b in top[_OVERVIEW_SUMMARY_TOP:])
    total = sum(_unit_value(b) for _, b in top)
    summary: dict[str, Any] = {"total": total}
    if unit == "rows" and count_field is not None:
        summary["with_points"] = with_points
    summary[out_field] = bounded
    # SIBLING field, never a key in `<out_field>` — see _OVERVIEW_SUMMARY_OTHER.
    if other:
        summary[_OVERVIEW_SUMMARY_OTHER] = other
    return summary


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
    section except topics (which requires entity_id). The UNCAPPED sections
    whose size grows with the graph's ROWS rather than its shape (a fixed
    vocabulary) are reported as bounded counts, never as rows, so the summary
    stays compact as the graph grows (#3510). Each number in a section's
    summary is in that section's OWN unit:
      `sources`         → {total, with_points, by_kind} in SOURCES (a group is
                          many source rows, so `by_kind` counts sources)
      `tags`            → {total, by_name} in TAGGED-POINT counts
      `pointkinds`      → {total, by_kind} in POINT counts
      `structure_check` → {total, by_rule} in VIOLATIONS
    Each `by_*` map keeps its top 20 groups and reports the folded remainder as
    the sibling `other`, in the same unit. Pass the matching section= for the
    full array.

    topics: entityProfile lite for an entity — requires entity_id.
    stale: Points not updated in N days — honors days/limit.
    """
    if section is None:
        combined: dict[str, Any] = {}
        for sec in _OVERVIEW_SECTIONS:
            if sec == "topics":
                continue  # requires entity_id — not part of the default summary
            result = _overview_section(sec, entity_id, days, limit)
            # #3510: every data-proportional section is folded to bounded
            # counts here — the rows themselves stay behind the explicit
            # section= calls. `tags`/`pointkinds` fold in their section's own
            # magnitude (each group is one row, so a row count would be a
            # literal 1); `sources`/`structure_check` fold in rows.
            if sec == "sources":
                result = _overview_summary(result, "sourceKind", "by_kind",
                                           "points")
            elif sec == "tags":
                result = _overview_summary(result, "name", "by_name", "count",
                                           unit="magnitude")
            elif sec == "pointkinds":
                result = _overview_summary(result, "kind", "by_kind", "count",
                                           unit="magnitude")
            elif sec == "structure_check":
                result = _overview_summary(result, "type", "by_rule")
            combined[sec] = result
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
    sdk = _get_org_sdk()
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
        return _safe(_get_org_sdk().get_session, id)
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
    return _safe(_get_org_sdk().backfill_v25, dry_run=dry_run)


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
    # Filesystem-walk vector (same as ingest_corpus/index_sessions): the
    # corpus path is caller-controlled and rglobs the HOST filesystem —
    # excluded from tenant HTTP; stdio/CLI only (#1090 review).
    if _transport_mode.get() == "http":
        return _http_excluded_error()
    sdk = _get_org_sdk()
    if corpus_dir is not None:
        return _safe(sdk.mine_corpus, corpus_dir,
                     extract_entities=extract_entities)
    if not transcript or not source_id:
        return {"error": "transcript= and source_id= are required "
                         "(or corpus_dir= for a batch)"}
    try:
        from tortoise.api import EventAPI  # noqa: I001
        from tortoise.log import EventLog
        from tortoise.mining import mine_conversation
        import tempfile, os  # noqa: E401
        log = sdk._get_event_log()
        if log is None:
            log = EventLog(os.path.join(
                tempfile.mkdtemp(prefix="tortoise_mcp_mine_"), "events.jsonl"))
        api = EventAPI(log, initiated_by="extractor", agent_id="mcp",
                       projection=sdk._get_proj())
    except Exception as exc:
        return {"error": f"mine setup failed: {exc}"}
    return _safe(mine_conversation, transcript, source_id, api,
                 extract_entities=extract_entities,
                 content_dedup=content_dedup,
                 session_date=session_date,
                 participants=participants,
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
    return _safe(_get_org_sdk().list_dedup_candidates,
                 candidate_type=candidate_type, limit=limit)


def tortoise_approve_merge(candidate_id: str,
                           action: str = "merge") -> dict:
    """Review a dedup/temporal candidate (W-2/W-4, #787).

    action='merge' → content: wire the alreadyDecided IMPL (draft prior) or
    defer to promotion (live prior); temporal: defer the NAND/supersede to
    promotion. action='reject' → the candidate stays separate and is no
    longer surfaced. Idempotent for repeated identical reviews.
    """
    return _safe(_get_org_sdk().approve_merge, candidate_id, action=action)


def tortoise_promote_point(point_id: str) -> dict:
    """Reviewer-gated draft→live promotion (Phase-4, #785/#787).

    The ONLY path a draft extraction Point may go live: blocks on
    quarantined batches {blocked, reason, batch_id}, rejects operator
    nodes, no-ops on already-live (DE2E-N9), promotes incident draft
    operators once all endpoints are live (R16), and wires deferred
    dedup/temporal links (Variant C / W-4).
    """
    return _safe(_get_org_sdk().promote_point, point_id)


def tortoise_belief_timeline(topic: str, limit: int = 50) -> dict:
    """Dated, ordered belief chain for a topic (J-4, #786/#787).

    Decision Points aboutObject-connected to the topic entity, validFrom-
    ordered (superseded priors kept visible via the CORRECTS chain), each
    with {content, pointKind, validFrom, status, linked_by, related}.
    """
    return _safe(_get_org_sdk().belief_timeline, topic, limit=limit)


# ── Tool Registry Adapter (#454) ────────────────────────────────
# Replaces @mcp.tool() decorators with programmatic registration.
# Function bodies remain module-level callables; the adapter wraps each
# via FunctionTool.from_function() and registers them on the shared mcp.
# The register_all call lives at the MODULE BOTTOM — after every module-level
# tool definition (incl. the onboarding tools + tortoise_session_capture
# defined below) — so the adapter resolves a handler for every registry
# entry from globals() (#2210: entries defined after this point used to be
# logged "no handler — skipped" while decorators half-registered them).
# ── Onboarding MCP tools (#498/#499/#500) ───────────────────────
# Wrappers for the hosted onboarding flow. These call the org-scoped SDK
# directly (same pattern as all tools) — the REST endpoints in hosted_api.py
# expose the same operations to the welcome page.

# Epic #888 no-regret: once an org's onboarding completes, the seven
# tortoise_onboarding_* tools retire from that org's steady-state MCP
# surface (tools/list) — the REST /v1/onboarding/* endpoints remain for the
# web onboarding flow. Function bodies are untouched; only the listing hides
# them. #2210: no @mcp.tool decorators here — the module-bottom register_all
# registers each from its registry entry (annotations are registry-canonical).
# See _HTTPToolFilter.list_tools.
_ONBOARDING_TOOL_NAMES: frozenset[str] = frozenset({
    "tortoise_onboarding_demo_create", "tortoise_onboarding_state",
    "tortoise_onboarding_session_recording", "tortoise_onboarding_github_connect",
    "tortoise_onboarding_github_index", "tortoise_onboarding_github_status",
    "tortoise_onboarding_seed",  # #1999 (W3)
})

# 60s per-org TTL cache for the tools/list gate — the onboarding-state read
# hits the control plane (Supabase orgs row / registry Org node); tools/list
# is called once per session but bounding the read to 1/min/org avoids any
# amplification (review fix, Epic #888). Staleness is fine: the gate is
# surface cosmetics and already fails open.
_onboarding_state_cache: dict[str, tuple[float, bool]] = {}
_ONBOARDING_STATE_TTL = 60.0

#: In-flight gate resolutions, keyed by org (#2924 review). The gate now AWAITS
#: between the cache lookup and the fill, so without this N concurrent
#: ``tools/list`` requests for ONE org all miss and each submits its own
#: graph-pool offload — a redundant stampede on the pool that also carries
#: graph WRITES, arriving exactly when the read is slow. Concurrent callers
#: share one task; the entry is dropped when it settles.
_onboarding_gate_inflight: dict[str, asyncio.Task[bool]] = {}


def _drop_gate_inflight(org_id: str, task: object) -> None:
    """Drop ``task`` from the in-flight map — ONLY if it is still the entry.

    #2924 review: a bare ``pop(org_id)`` is a real bug, not a simplification.
    The failed-read path deliberately leaves no cache entry, so the NEXT caller
    (same loop batch, same org) can install a replacement while the settled
    task's done-callbacks are still queued; a bare pop then evicts the LIVE
    replacement, and a third caller starts a duplicate read — two resolutions in
    flight for one org, on exactly the control-plane blip the single-flight
    exists to stop amplifying.
    """
    if _onboarding_gate_inflight.get(org_id) is task:
        _onboarding_gate_inflight.pop(org_id, None)


async def _resolve_onboarding_gate(org_id: str) -> bool:
    """Resolve the gate ONCE, for every concurrent caller (#2924).

    Fills the TTL cache on success; a FAILED read fills nothing, so the next
    ``list_tools`` retries rather than caching the failure. Never raises — the
    caller's fail-open contract is preserved by the caller.
    """
    from tortoise.hosted_api import _get_onboarding_projection_off_loop
    try:
        # #2001 (W5): the gate reads the merged projection — node-aware wire
        # completion; fail-open coercion (non-bool / 'unavailable' → False).
        projection = await _get_onboarding_projection_off_loop(org_id)
        complete = projection.get("onboarding_complete")
        complete = bool(complete) if isinstance(complete, bool) else False
    except Exception:
        return False  # never cache a failed read — retry next list
    _onboarding_state_cache[org_id] = (_time.time(), complete)
    return complete


async def _org_onboarding_complete() -> bool:
    """True when the current HTTP org's onboarding is complete.

    Fail-open: stdio/selfhost (no tenant Org row) and transient control-plane
    read failures return False — a read hiccup must never hide the tools a
    org still needs to finish onboarding. Reads the canonical onboarding
    state via hosted_api._get_onboarding_projection (jsonb ``teams`` row + the
    graph OnboardingState node), cached 60s per org.

    #2924: ``async`` because the read is OFF the event loop —
    ``_get_onboarding_projection`` is synchronous end to end (blocking PostgREST
    over ``httpx.Client`` AND a fresh FalkorDB client construction including
    ``ssl.create_default_context``), and calling it inline from this gate
    blocked the single loop on the MCP ``tools/list`` hot path. A ``py-spy``
    MainThread dump taken during a 1.08 s ``/health`` stall caught exactly this
    call chain; the app's own heartbeat recorded ``loop_lag_max_ms`` of 2033 ms.
    Only the cache MISS is offloaded, so the steady state stays a memory read;
    concurrent misses for one org share a single resolution
    (``_onboarding_gate_inflight``) so the await cannot become a stampede.
    """
    from tortoise.mcp_auth import SELFHOST_ORG_ID, _current_org_id
    org_id = _current_org_id.get()
    if not org_id or org_id == SELFHOST_ORG_ID:
        return False
    now = _time.time()
    cached = _onboarding_state_cache.get(org_id)
    if cached is not None and now - cached[0] < _ONBOARDING_STATE_TTL:
        return cached[1]
    task = _onboarding_gate_inflight.get(org_id)
    if task is None or task.done():
        task = asyncio.ensure_future(_resolve_onboarding_gate(org_id))
        _onboarding_gate_inflight[org_id] = task
        task.add_done_callback(
            lambda _t, _org=org_id: _drop_gate_inflight(_org, _t))
    try:
        # shield: one caller hanging up must not cancel the shared resolution
        # out from under the others.
        return await asyncio.shield(task)
    except Exception:
        return False  # never cache a failed read — retry next list


def _onboarding_state() -> dict:
    """Read this org's onboarding progress — the merged projection (jsonb
    OPERATIONAL keys + graph FLOW keys; graph-down FLOW 'unavailable')."""
    from tortoise.hosted_api import _get_onboarding_projection as _read_state
    org_id = _current_org_id.get()
    if org_id is None:
        return {"error": "No team context (HTTP mode required)"}
    return _read_state(org_id)


def tortoise_onboarding_demo_create() -> dict:
    """Create the demo epistemic graph (4 layers) for this org. Idempotent.

    Q4 — 'Create a demo graph?' — shows what Tortoise memory looks like.
    """
    from tortoise.hosted_api import _seed_demo_graph
    org_id = _current_org_id.get()
    if org_id is None:
        return {"error": "No team context (HTTP mode required)"}
    # C5 #2114 (re-review 3): the demo seeds the org's DEFAULT graph —
    # graph-bound keys rejected (REST twin public_demo parity).
    _reject_graph_bound_mcp_org_surface("demo seed")
    # #329: demo graph creation creates nodes — quota-gate it
    _enforce_quota("points")
    result = _seed_demo_graph(org_id)
    # Auto-update onboarding state
    try:
        from tortoise.hosted_api import _update_onboarding_state
        _update_onboarding_state(org_id, demo_created=True)
    except Exception:
        pass
    return result


def tortoise_onboarding_state() -> dict:
    """Return this org's onboarding progress (Q6 verification step)."""
    # #2300: reads the org's DEFAULT-graph/control-plane onboarding
    # projection (org-level surface) — graph-bound keys rejected (REST
    # twin GET /v1/onboarding/state parity, C5 #2114). A per-graph key must
    # never observe the org's onboarding state outside its graph.
    _reject_graph_bound_mcp_org_surface("onboarding state")
    return _onboarding_state()


def tortoise_onboarding_seed(org_name: str | None = None,
                             person_name: str | None = None) -> dict:
    """File the two onboarding anchor Subjects for this org (#1999, W3):
    Organization (Subject/organization) + User (Subject/naturalPerson)
    linked memberOf — interactive, ontology-precise.

    Interactive (WF-2): call WITHOUT names to discover gaps (the person
    display name is email-derived and needs user confirmation — never
    invented) or collisions (a same-name Subject that is not this org/user —
    disambiguation required, never a silent merge); call WITH the
    user-confirmed names to file. Returns status:
    'needs_confirmation' (gaps[] — ask the user), 'collision'
    (collisions[] — ask for a disambiguated name), or 'seeded'
    (two Subjects + memberOf + org_subject_id + first-points-filed).
    Compact orgs seed-lite (org-anchor Subject only, no person ask)."""
    # C5 #2114: the anchors file into the org DEFAULT graph — graph-bound
    # keys rejected (REST twin onboarding seed parity).
    _reject_graph_bound_mcp_org_surface("onboarding seed")
    org_id = _current_org_id.get()
    if org_id is None:
        return {"error": "No team context (HTTP mode required)"}
    from tortoise.hosted_api import (
        HTTPException as _HTTPException,
    )
    from tortoise.hosted_api import (
        _org_email,
        _run_onboarding_seed,
    )
    try:
        return _run_onboarding_seed(
            org_id, org_name=org_name, person_name=person_name,
            person_email=_org_email(org_id))
    except _HTTPException as exc:
        # 503 graph-down (fail-loud FLOW write) etc. — surfaced honestly,
        # never a silent skip (agent retries when the graph is back).
        return {"error": str(exc.detail),
                "status": getattr(exc, "status_code", 500)}
    except Exception as exc:  # pragma: no cover - defensive
        return {"error": f"seed failed: {exc}"}


def tortoise_onboarding_session_recording(enabled: bool) -> dict:
    """Toggle automatic session recording for this org (Q3 / dashboard
    Memory sources sessions toggle).

    #1927: session_recording is the OPTIONAL OFF-SWITCH (default ON,
    ToS-covered) — not a consent gate. The write flips the flag the capture
    pipeline checks (409 when off); ``capture_revised`` is written for
    backward-compatibility with the registered state keys (the exactly-once
    re-ask machinery it fed was removed with the gate)."""
    # C5 #2114 (re-review 3): org-level surface — graph-bound keys rejected
    # (REST twin set_session_recording parity).
    _reject_graph_bound_mcp_org_surface("session recording toggle")
    org_id = _current_org_id.get()
    if org_id is None:
        return {"error": "No team context (HTTP mode required)"}
    from tortoise.hosted_api import _update_onboarding_state
    state = _update_onboarding_state(org_id, session_recording=enabled,
                                     capture_revised=True)
    return {"onboarding": state}


def tortoise_onboarding_github_connect(org: str | None = None) -> dict:
    """Initiate GitHub OAuth — returns the authorize URL + CSRF state (Q1)."""
    # #2300: initiates org-level GitHub OAuth + stores org CSRF/org state
    # (control-plane) — graph-bound keys rejected (REST twin
    # POST /v1/onboarding/github/connect parity, #2300 closes the REST
    # residual). A per-graph key must never start a ORG-wide OAuth.
    _reject_graph_bound_mcp_org_surface("github connect")
    org_id = _current_org_id.get()
    if org_id is None:
        return {"error": "No team context (HTTP mode required)"}
    import secrets  # noqa: I001
    from urllib.parse import urlencode
    import os as _os
    client_id = _os.environ.get("GITHUB_CLIENT_ID")
    if not client_id:
        # Prompt-canonical text (#496/#540): GitHub OAuth is hosted-mode only.
        # In self-host HTTP the env never exists — match the prompt's documented
        # recovery anchor instead of the misleading "OAuth not configured".
        # (hosted_api.py REST endpoints keep the 503 ops signal.)
        return {"error": "No team context (HTTP mode required)"}
    state = secrets.token_urlsafe(24)
    # Store CSRF state so the callback can validate it (P2 review fix) —
    # must be visible to the REST callback handler in the same process.
    import time as _time  # noqa: I001
    from tortoise.hosted_api import _GITHUB_STATES
    _GITHUB_STATES[state] = {"org_id": org_id, "org": org or org_id,
                             "created_at": _time.time()}
    callback = _os.environ.get("GITHUB_CALLBACK_URL",
                               "https://api.premiselabs.co/v1/onboarding/github/callback")
    params = {"client_id": client_id, "redirect_uri": callback,
              "scope": "repo", "state": state}
    auth_url = f"https://github.com/login/oauth/authorize?{urlencode(params)}"
    return {"auth_url": auth_url, "state": state}


def tortoise_onboarding_github_status() -> dict:
    """Return GitHub connection status for this org (Q1 verify)."""
    # #2300: reads org-level GitHub credential state (control-plane) —
    # graph-bound keys rejected (REST twin GET /v1/onboarding/github/status
    # parity, #2300 closes the REST residual).
    _reject_graph_bound_mcp_org_surface("github status")
    org_id = _current_org_id.get()
    if org_id is None:
        return {"error": "No team context (HTTP mode required)"}
    # Follows the hosted seam (plan Task 6): Supabase orgs row via the
    # service-role control plane in Supabase mode, registry for selfhost.
    from tortoise.hosted_api import _github_credentials
    try:
        enc, org = _github_credentials(org_id)
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


# ── Auto-complete onboarding on first real write ────────────────
# When an agent makes its first successful graph write (create_point or
# file_decision), the server records the onboarding steps THAT WRITE IS
# EVIDENCE FOR, then hands the completion decision to the canonical
# fork-aware gate — no agent-side state machine ceremony needed.
#
# #3784: a step edge is a record of something the server OBSERVED. Filing a
# step the write does not evidence records a fact the user never produced,
# and the Setup guide then reports complete for work that did not happen.

def _maybe_onboarding_auto_complete(*,
                                    decision_observed: bool = False) -> None:
    """After a successful agent write, record the onboarding facts that
    write is itself evidence for, then let the canonical gate decide
    completion. Idempotent: steps are FWW edges, replay is a no-op.

    Observed steps (#3784) — the step's own label is the claim, so the
    server may file it only on the event the label describes:
    - ``harness-connected`` + ``first-points-filed``: a successful agent
      tool call IS the observation for both — the harness reached the
      server, and the two triggering tools file points (label: "Seed your
      first memory").
    - ``decide-completed`` (label: "Make your first decision"): filed ONLY
      when the caller observed a decision — ``tortoise_file_decision``
      succeeded, or ``tortoise_create_point(kind="decision")`` (the
      documented EP decide protocol, ``tortoise/onboarding/SKILL.md`` §5).
      A plain point write observes no decision and must not claim one.
      (``skills/tortoise-decide/SKILL.md``'s option/criterion/evidence flow
      is a DELIBERATE false negative — claiming a decision at the refinement
      step would be the same unobserved fact, inverted. See #3916.)
    - ``catalog-presented`` (label: "Review the catalog"): NEVER inferred
      from a write. Its presentation is observed by the agent catalog
      checkpoint (``hosted_api._CHECKPOINT_STEPS``), or asserted by an
      external caller through ``PATCH /v1/onboarding/state``
      (``catalog_presented``). The dashboard used to render-mark it on a
      build-fork pick, but that writer is deleted; the id stays an accepted,
      OPTIONAL record either way. #3913 (owner ruling 2026-09-20): it is NO
      LONGER a build-gate requirement — the build fork completes on the two
      observed acts above — so it is never a completion input.

    Status is SERVER-OWNED and fork-aware: completion is delegated to
    ``hosted_api._maybe_apply_completion`` (the canonical
    ``state.completion_gate_satisfied`` eval, honouring fork=None→self,
    compact-first and fork_unsure_at), so this function can never flip an
    org to complete while a required step is missing.

    ``decision_observed`` is keyword-only and defaults to False: a caller
    that forgets to declare its observation fails CLOSED (claims no
    decision), never open.

    Caches the ``tools/list`` verdict ``True`` only when ``_maybe_apply_completion``
    reports a real transition to complete; that helper pops the entry itself,
    so a completion is visible immediately.

    Only fires in HTTP (hosted) mode with a real org_id — stdio and
    self-host calls are no-ops."""
    from tortoise.mcp_auth import SELFHOST_ORG_ID, _current_org_id
    org_id = _current_org_id.get()
    if not org_id or org_id == SELFHOST_ORG_ID:
        return  # stdio / self-host: no hosted onboarding state
    # Fast check: if the 60s cache says complete, skip.
    now = _time.time()
    cached = _onboarding_state_cache.get(org_id)
    if cached is not None and now - cached[0] < _ONBOARDING_STATE_TTL and cached[1]:
        return  # already known complete
    try:
        from tortoise.hosted_api import (
            _emit_onboarding_step_events,
            _get_onboarding_projection,
            _get_onboarding_state,
            _maybe_apply_completion,
            _onboarding_distinct_id,
            _org_proj,
        )
        from tortoise.onboarding.state import (
            write_completed_step as _os_write_step,
        )
        proj = _org_proj(org_id)
        projection = _get_onboarding_projection(org_id)
        prog = projection.get("onboarding_complete")
        if isinstance(prog, bool) and prog:
            _onboarding_state_cache[org_id] = (now, True)
            return  # already complete
        # File ONLY the steps this write observed (#3784). Idempotent FWW
        # edges — a replay is a no-op.
        observed = ["harness-connected", "first-points-filed"]
        if decision_observed:
            observed.append("decide-completed")
        legacy_mirror = bool(
            _get_onboarding_state(org_id).get("onboarding_complete"))
        # #2006 (W11): emit IMMEDIATELY after each creating write, so a later
        # step's failure cannot discard an edge creation this call already
        # observed. Fail-safe (capture never raises, and the helper guards each
        # emit), so this can never block the agent's write.
        for step in observed:
            res = _os_write_step(proj, org_id, step,
                                 status_from_mirror=legacy_mirror)
            if res.get("created"):
                _emit_onboarding_step_events(
                    [step],
                    distinct_id=_onboarding_distinct_id(org_id),
                    org_id=org_id, source="mcp_auto")
        # Server-owned status → the canonical fork-aware gate decides, never
        # this function (monotonic; a no-op if already complete).
        if _maybe_apply_completion(org_id):
            # The helper returned a real TRANSITION to complete — cache the
            # tools/list verdict. An already-complete org never reaches here
            # (the projection short-circuit above cached it), and caching
            # True for an incomplete org would retire the onboarding tools
            # from tools/list — a second false "you're all set".
            _onboarding_state_cache[org_id] = (now, True)
    except Exception:
        # Fail-open: a transient graph/control-plane error must NOT block
        # the agent's write. Next write re-triggers this check.
        return


# ── #1727 Slice 2 (Task 13): tortoise_session_capture — the T3 filing tool ──
# Claude Web (and every harness with an MCP surface) files sessions through
# this tool. It calls the SAME capture pipeline as POST /v1/sessions
# (hosted_api._capture_session_impl) so the two surfaces can never drift on
# gate order: admission 429 (#3060) → session_recording opt-out 409 → empty
# 422 → quota 402. A missing provider key is NOT a gate (#3892): the capture
# is STORED and only the LLM extraction is skipped, reported as
# `extraction_mode: "no-provider"`. Stdio/self-host returns an honest "requires
# hosted mode" error —
# there is deliberately NO local fallback that bypasses the capture pipeline
# (a prompt-injection exfiltration surface must not exist).


def tortoise_session_capture(conversation: list[dict],
                             harness: str | None = None,
                             session_id: str | None = None,
                             machine_id: str | None = None,
                             model: str | None = None) -> dict:
    """File an agent session into the graph (T3 workflows prompt surface).

    Server-enforced gates (identical to POST /v1/sessions — VERIFIED order,
    #2093 S2): boundary 422 (invalid harness/shape — SessionRequest
    construction below) -> 409 (recording off) -> 503 (no provider) -> 400
    (turn cap) -> 422 (empty/blank transcript) -> 402 (quota). Returns the
    capture result (a memory_write_v1 envelope, #2104) on success, or an
    honest error dict on failure (the per-harness last-error state key is
    recorded on non-2xx EXCEPT the #3060 capacity 429 — a server condition —
    and cleared on 2xx; same receipt semantics as the REST path).
    """
    from tortoise.mcp_auth import (  # noqa: I001
        SELFHOST_ORG_ID,
        _current_org_id,
        _current_org_limits,
        _current_graph_id,
        _current_graph_namespace,
        _current_scopes,
        _current_legacy_full_access,
    )
    from tortoise.sdk import _current_actor_user_id  # #2600
    org_id = _current_org_id.get()
    if not org_id or org_id == SELFHOST_ORG_ID:
        # stdio / self-host HTTP: no hosted state plane, no receipts — the
        # honest error (matching the onboarding-tool precedent), never a
        # local fallback that bypasses the 409/402/503 gates.
        return {"error": "session capture requires hosted mode — the "
                          "tortoise_session_capture tool files to Tortoise "
                          "Cloud (server-enforced recording + receipts); "
                          "self-hosted stdio capture is not available."}
    from tortoise.session_attribution import (
        derive_machine_id,
        sanitize_attribution_field,
    )
    # #2681: derive-only fallback when caller does not supply.
    if machine_id is None:
        machine_id = derive_machine_id()
    if machine_id is not None:
        machine_id = sanitize_attribution_field(machine_id, max_length=256)
    if model is not None:
        model = sanitize_attribution_field(model, max_length=128)

    from tortoise.hosted_api import (
        _CAPTURE_MARKER_EXECUTOR,
        _CAPTURE_SESSION_IN_FLIGHT_DETAIL,
        SessionRequest,
        _capture_abandoned_marker,
        _capture_lane,
        _capture_session_impl,
        _capture_session_key,
        _record_capture_last_error,
        _reserve_capture_slot,
        _submit_off_loop,
    )
    limits = _current_org_limits.get() or {}
    org = {"org_id": org_id, "tier": limits.get("tier", "free"),
            "key_id": None}
    # #2600: carry the resolved human actor (set by OrgResolutionMiddleware)
    # into the impl's org dict so the Session MERGE + _data_sdk ContextVar
    # set see it — never depend on the asyncio.run context bridge.
    org["actor_user_id"] = _current_actor_user_id.get()
    # C6 #2115 (D-C6-4): a graph-bound key's capture must land in ITS graph
    # — carry the resolution ContextVars into the impl's org dict so
    # _data_sdk routes there (and the per-graph recording gate reads the
    # key's override). Session/OAuth/org-wide resolutions have empty
    # context → no graph fields → default graph (unchanged).
    _gid = _current_graph_id.get()
    if _gid:
        org["graph_id"] = _gid
        org["graph_namespace"] = _current_graph_namespace.get()
        org["scopes"] = _current_scopes.get() or []
        org["legacy_full_access"] = bool(_current_legacy_full_access.get())
    if limits.get("max_points") is not None:
        org["max_points"] = int(limits["max_points"])
    # #4010: carry the resolved sessions limit through the SAME bridge, but
    # ONLY when it is actually present. `_check_org_limit(org, "sessions")`
    # treats an EXPLICIT None as unlimited and a MISSING key as fail-closed
    # (#310 GAP-B) — so a presence guard is required, not `.get()`: the bridge
    # must not synthesize a key the resolver never produced (that would be the
    # same silent leniency the `enforce_org_limit` fallback removal exists to
    # kill, and it would make MCP capture succeed where REST 500s).
    if "max_sessions" in limits:
        org["max_sessions"] = limits["max_sessions"]
    try:
        body = SessionRequest(conversation=conversation, harness=harness,
                              session_id=session_id,
                              machine_id=machine_id, model=model)
    except Exception as e:
        # Pydantic 422-equivalent (invalid harness / conversation shape).
        return {"error": f"invalid capture payload: {e}", "status": 422}
    try:
        import asyncio
        # #3060: the SAME admission reservation as the REST endpoint. This
        # surface shares `_CAPTURE_EXECUTOR`, so without reserving here the cap
        # would not bind MCP captures at all — they would queue unboundedly
        # behind a stalled pool, the exact failure mode the cap closes
        # (reviewer measurement: cap=2, 4 concurrent extractions). #3129:
        # the session_id goes with it, so a duplicate in-flight capture of the
        # same session is refused on this surface too (scoped to this tenant).
        slot = _reserve_capture_slot(_capture_session_key(org, session_id))
        # #3129: parity with the REST endpoint — a cancellation between the
        # attempt starting and its outcome being recorded would leave the
        # Session at `capture_ok=NULL`, which the replay rule reads as
        # "presumed captured" (see hosted_api._capture_abandoned_marker).
        _state: dict = {}
        try:
            return asyncio.run(_capture_session_impl(body, None, org,
                                                     slot=slot,
                                                     state=_state))
        except asyncio.CancelledError:
            # #3129: DEFENSIVE — parity with the REST endpoint, but not
            # reachable under the current dispatch: this tool is a SYNC
            # FastMCP callable, and fastmcp runs sync callables via
            # `anyio.to_thread.run_sync` with the default
            # `abandon_on_cancel=False`, so the inner `asyncio.run` loop is
            # never cancelled and no CancelledError reaches here (cycle-4
            # review). Kept because the cost is nil and a future async tool or
            # a cancellation-capable dispatcher would need it — see the
            # residual sentinel issue for real MCP abandonment coverage.
            if _state.get("attempted") and not _state.get("finalized"):
                # Off-loop, key held until it lands (see the REST endpoint and
                # hosted_api._CaptureSlot.hold_until).
                try:
                    slot.hold_until(_submit_off_loop(
                        _CAPTURE_MARKER_EXECUTOR, _capture_abandoned_marker,
                        _state.get("proj"), session_id,
                        _state.get("lane") or _capture_lane()))
                except Exception:  # pragma: no cover - pool shut down
                    _log.exception("abandoned-capture marker submit failed")
            raise
        finally:
            slot.release()
    except Exception as e:
        status = getattr(e, "status_code", 500)
        detail = getattr(e, "detail", str(e))
        # #3060: the capacity 429 is a SERVER condition, not an org capture
        # failure — never paint it on the dashboard (REST does the same).
        # #3129: likewise the in-flight 409.
        if (status >= 400 and status != 429
                and detail != _CAPTURE_SESSION_IN_FLIGHT_DETAIL):
            with contextlib.suppress(Exception):
                _record_capture_last_error(org_id, harness, str(detail))
        # #3665: a 402 from the shared capture impl is ALWAYS a quota refusal
        # — the points-estimate gate, the cohort cost cap, or the
        # ``_check_org_limit(org, "sessions")`` limit — so carry the shared
        # ERR_QUOTA code rather than making the caller interpret a bare status.
        # One mapping site covers every 402 this impl can raise, so REST and
        # MCP cannot drift on the class of a refusal.
        out = {"error": str(detail), "status": status}
        if status == 402:
            out["code"] = ERR_QUOTA
        return out


def tortoise_graph_set_recording(recording: bool | None,
                                 graph_id: str | None = None) -> dict:
    """Set or clear a graph's session-recording override (#2302) — the MCP
    twin of PATCH /v1/graphs/{graph_id} (recording), sharing the SAME
    hosted_api core (``_apply_graph_recording_override``) so the two
    surfaces can never drift on permission, semantics, or storage.

    recording: true|false sets the per-graph override; null removes it
    (inherit the org default — a null never flips an org ON, #1927
    default-ON preserved). Requires hosted mode + the same management
    permission as the REST PATCH: a team:manage-scoped key (or the legacy
    full-access class) — graph data scopes alone are NOT enough. A
    graph-bound owner-minted manager key may set ANY graph in the org
    (explicit graph_id), incl. the DEFAULT graph ('default').

    graph_id: the graph to change; when omitted, the calling key's OWN
    bound graph is the target (the override the capture 409 gate reads),
    else the org DEFAULT graph ('default').

    The capture 409 ("Session recording is disabled for this graph")
    routes agents here — call this tool to turn recording back on, then
    retry the capture. Returns {graph_id, recording}; errors return
    {error, status} (403 missing scope, 404 unknown graph, 422 bad body).
    """
    from tortoise.mcp_auth import (
        SELFHOST_ORG_ID,
        _current_graph_id,
        _current_legacy_full_access,
        _current_org_id,
        _current_scopes,
    )
    org_id = _current_org_id.get()
    if not org_id or org_id == SELFHOST_ORG_ID:
        # stdio / self-host HTTP: the per-graph override is control-plane
        # state with no local registry row to write — honest error, never a
        # silent local no-op (capture-tool parity).
        return {"error": "graph recording is a hosted control-plane "
                           "setting — requires hosted mode"}
    gid = graph_id or (_current_graph_id.get() or "default")
    # Permission gate mirrors the REST PATCH key branch: team:manage (or
    # the legacy full-access class). Graph data scopes alone 403.
    scopes = _current_scopes.get()
    legacy = bool(_current_legacy_full_access.get())
    if not (legacy or scopes is None or "team:manage" in (scopes or [])):
        return {"error": "Missing team:manage scope — per-graph recording "
                           "is a team-management setting (mirror of "
                           "PATCH /v1/graphs/{graph_id})", "status": 403}
    from tortoise.hosted_api import (
        GraphRecordingPatch,
        _apply_graph_recording_override,
    )
    key_ctx = {
        "org_id": org_id,
        "key_id": "mcp",
        "scopes": list(scopes) if scopes is not None else None,
        "legacy_full_access": legacy,
        "session_user_id": None,
    }
    try:
        # Strict bool/null (no truthy-string coercion) — REST body parity.
        body = GraphRecordingPatch(recording=recording)
    except Exception as e:
        return {"error": f"recording must be true, false or null ({e})",
                "status": 422}
    try:
        import asyncio
        return asyncio.run(
            _apply_graph_recording_override(gid, body, org_id, key_ctx))
    except Exception as e:
        status = getattr(e, "status_code", 500)
        detail = getattr(e, "detail", str(e))
        return {"error": str(detail), "status": status}


def tortoise_onboarding_github_index(org: str, repo: str | None = None) -> dict:
    """Start background GitHub indexing of an org's issues/PRs (Q2).

    Returns {job_id, status} — poll via the REST endpoint or check
    onboarding state for github_indexed.
    """
    # C5 #2114 (re-review 3): indexes into the DEFAULT graph — graph-bound
    # keys rejected (REST twin index_github parity).
    _reject_graph_bound_mcp_org_surface("github index")
    org_id = _current_org_id.get()
    if org_id is None:
        return {"error": "No team context (HTTP mode required)"}
    import asyncio as _asyncio

    from tortoise.hosted_api import (
        _github_token_enc,
        _run_indexing,
        _start_index_job,
        _validate_repo_scope,
    )
    try:
        encrypted = _github_token_enc(org_id)
    except Exception:
        # Name the actual plane (registry vs Supabase) so selfhost operators
        # aren't misled (code-review P2, PR #861).
        from tortoise.supabase_control import is_supabase_enabled
        plane = "Supabase control plane" if is_supabase_enabled() else "registry"
        return {"error": f"{plane} unavailable"}
    if not encrypted:
        return {"error": "GitHub not connected. Run tortoise_onboarding_github_connect first."}
    # #1725 + P1-1 (PR #1792): single-flight — an in-flight started job for
    # the org is reused AND the run is spawned ONLY when the job was
    # freshly minted (never two concurrent walks on one job).
    job_id, is_new = _start_index_job(org_id)
    if is_new:
        try:
            # #1845: _run_indexing now takes a repo LIST (None = all). The
            # MCP tool's single optional repo is wrapped into a one-item
            # list through the same allowlist validator as the REST
            # re-poll — a bare str would be iterated character-by-character
            # by the new list contract (regression caught in review).
            _asyncio.get_event_loop().create_task(
                _run_indexing(job_id, org_id, org,
                              _validate_repo_scope([repo] if repo else None)))
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
                    tool_group: str | None = None,
                    emit_oauth_challenge: bool = False) -> Any:
    """Configured Streamable HTTP app for the hosted platform (#236).

    IMPORTANT: ``emit_oauth_challenge`` defaults to **False** on purpose. Leave
    it alone unless this app is served alongside a real authorization server
    (i.e. ``hosted_api``). A challenge emitted where no
    ``/.well-known/oauth-protected-resource`` route exists points the client at a
    404 — strictly worse than the bare 401. Only ``tortoise/hosted_api.py``
    passes True.

    Mounted at /mcp on the existing FastAPI app. Auth + rate limiting +
    security headers + body-size caps live INSIDE this app's middleware
    stack — the parent FastAPI app.mount() does NOT propagate its own
    middleware to mounted sub-apps (verified Starlette behavior).

    auth_mode (additive, default "tenant" = hosted byte-identical):
      "tenant" → OrgResolutionMiddleware (registry Bearer tt_ keys)
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
    from starlette.middleware import Middleware  # noqa: I001
    from starlette.responses import JSONResponse
    from tortoise.mcp_auth import (MCPRateLimitMiddleware,
                                   SecurityHeadersMiddleware,
                                   RequestBodySizeMiddleware)
    from fastmcp.server.transforms import Transform

    # auth_mode middleware selection. OrgResolutionMiddleware (tenant mode) is
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
        from tortoise.mcp_auth import OrgResolutionMiddleware
        # #2864: only the HOSTED app passes emit_oauth_challenge=True. Tenant-mode
        # self-host (`tortoise serve --http`, this function's default) has no
        # authorization server and registers no /.well-known/* routes, so a
        # challenge there would point the client at a 404.
        auth_mw = Middleware(OrgResolutionMiddleware, registry_sdk=_registry_sdk,
                             emit_challenge=emit_oauth_challenge)
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

        The excluded tools (org_create/backfill_v25/ingest_corpus) remain
        registered on the shared module-level mcp instance for stdio, but are
        filtered out of the HTTP tool listing so tenants can't discover them.
        When tool_group is set, only that group's tools are listed — role-
        scoped servers keep the agent's tool-selection surface under ~20.
        """
        async def list_tools(self, tools):
            group = _tool_group.get()
            # Skip the control-plane read when it can't change the outcome: in
            # a curation-group-scoped app (other than "onboarding") the group
            # filter below already excludes the onboarding tools.
            onboarding_done = False
            if not (group and group != "onboarding"):
                # #2924: awaited — the gate read is off the event loop (the
                # read is synchronous end to end; see _org_onboarding_complete).
                onboarding_done = await _org_onboarding_complete()

            def _visible(t):
                if t.name not in HTTP_ALLOWED:
                    return False
                tgroup = GROUP_BY_NAME.get(t.name)
                # explicit curation-group request — serve that group's tools
                if group and tgroup != group:
                    return False
                # Epic #888: onboarding tools retire from the steady-state
                # surface once this org's onboarding is complete (fail-open
                # — state read errors keep them visible).
                if onboarding_done and t.name in _ONBOARDING_TOOL_NAMES:  # noqa: SIM103
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
# and the FastMCPAdapter.register_all() call above (every tool function in
# this module has been defined by this point, so the adapter below resolves
# a handler for EVERY registry entry — #2210). Placing it earlier (was
# line ~1211) made `python -m tortoise.mcp_server` enter mcp.run() with ZERO
# tools registered — onboarding's Step 0 (tortoise_health) failed with
# "Can't connect to Tortoise". Importing callers (tortoise serve via
# __main__.py, deployment.py) are unaffected: they import the module first
# (which executes register_all), then call main().


# ── Dry-run previews (#4057) ─────────────────────────────────────
# `dry_run=True` answers "what would this destroy?" and changes NOTHING.
#
# A preview is READ-ONLY: it runs the same existence/lifecycle validations
# the write path runs (so a bad input fails here exactly as the write would,
# with the same message), then enumerates the nodes and edges the write
# would touch. It never emits an event, never marks the projection dirty,
# and never enters `_quota_gated` — that wrapper meters a WRITE, and a
# preview performs none. It IS wrapped in `_safe`, so the same
# auth/transport gate that protects the write protects the preview: a dry
# run is a read, not an auth bypass.
#
# The flag is opt-in PREVIEW, not opt-in destruction: `dry_run` defaults to
# `False`, which is today's behaviour byte-for-byte, so no existing caller
# changes meaning. Flipping the default to True would make every existing
# `tortoise_delete(id)` call a silent no-op — a breaking change to the
# surface, not an additive off-by-default field (the #3986 carve-out).
#
# Every preview reports the concrete blast radius. Where the writer's
# transfer rule is non-trivial (supersede), the preview mirrors the rule and
# a differential test pins the preview count against the writer's OWN
# `edges_transferred`, so a drift fails the suite instead of shipping a
# preview that lies.

_PREVIEW_NOTE = "No changes were made — re-call with dry_run=false to apply."


def _preview_result(tool: str, would: str, **fields) -> dict:
    return {"dry_run": True, "tool": tool, "would": would, **fields,
            "note": _PREVIEW_NOTE}


def _preview_delete_edges(sdk, internal_ids: list) -> list[dict]:
    """All edges incident to ANY of the nodes, as {type, from, to}.

    Set-based and deduped by INTERNAL edge id. A per-node loop would
    double-count an edge whose BOTH endpoints are being deleted (once per
    endpoint), and a logical-id match would return both nodes' edges for each
    node; `ID(r)` dedup is exact in both cases. Endpoints are reported by
    LOGICAL identity (id, else eventId/name/url), never the internal id.
    DETACH DELETE removes each node and every incident edge, so this set IS
    the delete's edge blast radius.
    """
    if not internal_ids:
        return []
    rows = sdk._get_proj().g.query(
        "MATCH (a)-[r]-(b) WHERE ID(a) IN $nids OR ID(b) IN $nids "
        "RETURN ID(r), type(r), "
        "coalesce(startNode(r).id, startNode(r).eventId, startNode(r).name, startNode(r).url), "
        "coalesce(endNode(r).id, endNode(r).eventId, endNode(r).name, endNode(r).url)",
        params={"nids": internal_ids},
    ).result_set
    seen: set = set()
    out: list[dict] = []
    for rid, rtype, src, tgt in rows:
        if rid in seen:
            continue
        seen.add(rid)
        out.append({"type": rtype, "from": src, "to": tgt})
    return out


def _preview_orphan_tags(sdk, internal_ids: list) -> list[str]:
    """Tags the writer's orphan-GC would delete alongside the Points.

    `delete_point` runs, when the deleted point had any TAGGED edge, a
    graph-wide `MATCH (t:Tag) WHERE NOT (t)<-[:TAGGED]-() DELETE t`. A tag is
    removed iff it has no TAGGED edge from a SURVIVING point — which also
    catches pre-existing orphans. (Only `delete_point` does this;
    `_delete_entity` does not.)
    """
    if not internal_ids:
        return []
    rows = sdk._get_proj().g.query(
        "MATCH (t:Tag) OPTIONAL MATCH (t)<-[:TAGGED]-(q) "
        "WITH t, [x IN collect(ID(q)) WHERE NOT x IN $nids] AS survivors "
        "WHERE size(survivors) = 0 RETURN coalesce(t.name, t.id, t.tag)",
        params={"nids": internal_ids},
    ).result_set
    return [r[0] for r in rows]


def _preview_delete_point(sdk, id: str) -> dict:
    # The writer's `MATCH (n:Point {id:$id}) DETACH DELETE n` deletes EVERY
    # matching Point, so count internal ids — not a hardcoded 1.
    proj = sdk._get_proj()
    internals = [row[0] for row in proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN ID(n)",
        params={"id": id},
    ).result_set]
    edges = _preview_delete_edges(sdk, internals)
    # Tag GC is gated on the deleted point having a TAGGED edge.
    has_tags = bool(proj.g.query(
        "MATCH (n:Point {id:$id})-[:TAGGED]->() RETURN count(*) > 0",
        params={"id": id},
    ).result_set[0][0]) if internals else False
    tags = _preview_orphan_tags(sdk, internals) if has_tags else []
    return _preview_result(
        "tortoise_delete_point", "delete",
        found=bool(internals),
        target={"id": id, "label": "Point"},
        nodes_removed=len(internals),
        edges_removed=len(edges),
        tags_removed=len(tags),
        nodes=[id] * len(internals),
        edges=edges,
        tags=tags,
    )


def _preview_delete_entity(sdk, id: str) -> dict:
    # REVIEW-FIX P2 (#4057): the label→id-property table is IMPORTED, never
    # re-hardcoded. `_delete_entity` (sdk.py) and the replay fold
    # (`projection._delete_entity_by_id`) both read
    # `projection._CANONICAL_ENTITY_ID_PROPS`, so a local copy here could drift
    # — and drift makes this preview UNDER-report the blast radius (a label
    # whose id property moved would match nothing and silently drop out of the
    # count), which is the dangerous direction.
    from tortoise.projection import _CANONICAL_ENTITY_ID_PROPS
    proj = sdk._get_proj()
    seen: set = set()
    nodes: list[str] = []
    for label, prop in _CANONICAL_ENTITY_ID_PROPS:
        # Dedup by INTERNAL node id: a node carrying two matched labels
        # (`:Point:Object`) is deleted ONCE — the writer's first DETACH DELETE
        # removes it and the next label matches 0. Two DISTINCT nodes sharing
        # an id value are both deleted, and their internal ids differ, so
        # this neither over- nor under-counts. (Matches the writer, which
        # never dedups by logical id.)
        for (internal,) in proj.g.query(
            f"MATCH (n:{label} {{{prop}:$id}}) RETURN ID(n)",
            params={"id": id},
        ).result_set:
            if internal in seen:
                continue
            seen.add(internal)
            nodes.append(id)
    # `_delete_entity` does NOT run the Tag GC (only `delete_point` does).
    edges = _preview_delete_edges(sdk, sorted(seen))
    return _preview_result(
        "tortoise_delete_entity", "delete",
        found=bool(nodes),
        target={"id": id},
        nodes_removed=len(nodes),
        edges_removed=len(edges),
        nodes=nodes,
        edges=edges,
    )


def _preview_delete(sdk, id: str) -> dict:
    """Preview `tortoise_delete` — resolve the label first, exactly as
    `TortoiseSDK.delete` does, then preview the branch it would take."""
    resolved = sdk._get_proj()._resolve_entity(id, by_id=True, by_eventId=True)
    if not resolved:
        return _preview_result(
            "tortoise_delete", "delete",
            found=False,
            target={"id": id, "label": None},
            nodes_removed=0,
            edges_removed=0,
            nodes=[],
            edges=[],
        )
    label = resolved[0]["label"]
    out = (_preview_delete_point(sdk, id) if label == "Point"
           else _preview_delete_entity(sdk, id))
    out["tool"] = "tortoise_delete"
    out["target"] = {"id": id, "label": label}
    return out


def _preview_retract_point(sdk, id: str) -> dict:
    """Preview `tortoise_retract_point` — a ONE-node status flip.

    The shared lifecycle guard runs first, so a missing / operator / already
    terminal point raises exactly as the write would (no rosy preview over an
    input the write would reject).
    """
    guard = sdk._assert_lifecycle_guard(id, method="retraction")
    # The writer's CAS carries `WHERE <non-terminal>` and a duplicated id can
    # mix a live and a terminal node — count only the rows it would stamp.
    from tortoise.live import _terminal_excluded
    n_affected = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) WHERE " + _terminal_excluded("n.status") +
        " RETURN count(n)",
        params={"id": id},
    ).result_set[0][0]
    return _preview_result(
        "tortoise_retract_point", "retract",
        found=True,
        target={"id": id},
        nodes_affected=n_affected,
        nodes_removed=0,
        edges_removed=0,
        edges_added=0,
        status={"id": id, "from": guard.get("status") or "live",
                "to": "retracted", "outdated": guard.get("outdated")},
    )


def _preview_invalidate(sdk, id: str, corrected_by_id: str) -> dict:
    """Preview `tortoise_invalidate` — outdate ONE point, add ONE CORRECTS
    edge. Both lifecycle guards AND the writer's #5358 inverted-window
    precondition run first, mirroring the write's validation order and
    messages.

    The #5358 check reads its own clock, which precedes the writer's. The
    guarantee is therefore ONE-WAY, in the safe direction: the preview refuses
    whenever the write would refuse (a start inside the preview→write interval
    may be refused here and accepted there — conservative, never a false
    "the write will succeed"). Do NOT "fix" that asymmetry by dropping the
    shared call: a green preview must mean the write accepts."""
    if id == corrected_by_id:
        raise ValueError(
            f"invalidate_point: corrected_by cannot be the point itself ({id!r})"
        )
    old = sdk._assert_lifecycle_guard(
        id, method="invalidation", role="source", missing_ok=True)
    if old is None:
        return _preview_result(
            "tortoise_invalidate", "invalidate",
            found=False, invalidated=False,
            target={"id": id, "corrected_by": corrected_by_id},
            nodes_affected=0, edges_removed=0, edges_added=0,
        )
    sdk._assert_lifecycle_guard(
        corrected_by_id, method="invalidation", role="corrector")
    # #5358: mirror the writer's inverted-window precondition — the SAME
    # shared check `invalidate_point` runs — so the preview cannot report
    # "would invalidate" for an input the write refuses (#4057's contract:
    # a preview over an input the write would reject must reject it too).
    # `_preview_supersede(transfer_edges=False)` reuses this function, so it
    # inherits the check.
    from datetime import datetime, timezone
    sdk._assert_window_start_not_inverted(
        id, datetime.now(timezone.utc).isoformat())  # noqa: UP017
    # The writer is `MATCH (a:Point {id:$new}), (b:Point {id:$old}) MERGE
    # (a)-[:CORRECTS]->(b)` — it binds EVERY matching pair, stamps EVERY
    # matching old node, and the MERGE adds only pairs that lack the edge.
    proj = sdk._get_proj()
    n_old = proj.g.query("MATCH (b:Point {id:$o}) RETURN count(b)",
                        params={"o": id}).result_set[0][0]
    pairs_added = proj.g.query(
        "MATCH (a:Point {id:$n}), (b:Point {id:$o}) "
        "WHERE NOT (a)-[:CORRECTS]->(b) RETURN count(*)",
        params={"n": corrected_by_id, "o": id},
    ).result_set[0][0]
    edges = [{"type": "CORRECTS", "from": corrected_by_id, "to": id}
             for _ in range(pairs_added)]
    return _preview_result(
        "tortoise_invalidate", "invalidate",
        found=True, invalidated=True,
        target={"id": id, "corrected_by": corrected_by_id},
        nodes_affected=n_old,
        nodes_removed=0,
        edges_removed=0,
        edges_added=len(edges),
        status={"id": id, "from": old.get("status") or "live",
                "to": "outdated", "outdated": True},
        edges=edges,
    )


def _preview_supersede(sdk, old_id: str, new_id: str,
                       transfer_edges: bool = True) -> dict:
    """Preview `tortoise_supersede`.

    transfer_edges=False is the invalidate behaviour and reuses that preview.
    transfer_edges=True enumerates the edges the writer repoints, leg by leg:
      * 2a operator -> old, EXCEPT `alreadyDecided` (kept on the dead prior).
        The writer uses CREATE here, so every row becomes its own edge;
      * 2a-DIRECT operator-less IMPL/NAND incident to old, excluding operator
        targets. The writer MERGEs, so rows to the same target collapse;
      * 2b the structural rels in `SUPERSEDE_STRUCTURAL_RELS` (imported from
        the writer — sdk.py's own named constant), also MERGEs.
    An edge whose far endpoint IS the successor is delete-only (no phantom
    self-edge) — reported under `edges_dropped`.

    `edges_transferred_from_old` is the writer's own old-side count — on any
    graph with no direct IMPL/NAND self-loop at `old` it equals the number the
    writer reports as `edges_transferred`, and a differential test pins the two
    together there. It is NOT equal when BOTH of two conditions hold: `old`
    carries a direct IMPL/NAND self-loop of type `T`, AND no edge
    `(new)-[r:T]->(old)` of that SAME type already exists. Then the writer is the
    one that over-counts: its out-pass repoints `(old)-[r:T]->(old)` to
    `(new)->(old)` and MERGEs that edge into existence — and the MERGE is typed
    by `rtype`, the self-loop's OWN type (`sdk.py`, `MERGE (new)-[nr:{rtype}]->(t)`) —
    and its in-pass then matches the freshly created edge and delete-onlys it,
    booking one removed edge twice — while this preview dedups the two matches
    and counts the edge ONCE. The TYPE matters: only a same-type edge suppresses
    the divergence, because only a same-type edge collapses that MERGE. Measured
    across all six states (self-loop type x pre-existing edge):

        no pre-existing edge   preview 2  writer 3  DIFFER
        same type              preview 3  writer 3  agree — the MERGE collapses onto
                                                     it and the in-pass delete-onlys
                                                     that pre-existing edge, which this
                                                     preview also counts, under
                                                     `edges_dropped`
        opposite type          preview 3  writer 4  DIFFER — nothing to collapse onto,
                                                     so the out-pass still creates

    Each condition has its own test. This
    value is the accurate one; the self-loop is reported under `edges_dropped`
    with the reason "self-loop on the old node". It counts every row the write
    removes from old, INCLUDING the delete-only rows (`edges_dropped`: a
    self-loop, or a far endpoint that is the successor / not a Point) that the
    writer also books as "transferred" while creating nothing.
    It is therefore NOT "the edges that arrive at the successor": those are
    `edges_created_at_new` + `edges_already_present_at_new`.
    `edges_remaining_at_old` is the complementary count — the edges incident to
    old that the write neither repoints nor removes (e.g. `related`, `TAGGED`,
    `aboutSource`, an inbound `CORRECTS`, an `alreadyDecided` operator edge via
    `edges_kept_attached`). Without it a caller cannot read the residual blast
    radius from any field. The `CORRECTS` edge the write ADDS is not included
    (it does not exist yet) — see `corrects_edge`.
    `edges_created_at_new` is the NET-NEW edge count the write creates at the
    successor (MERGE destinations already present are excluded), and
    `edges_already_present_at_new` names those no-ops; both are multiplied by
    `successor_nodes` when a duplicate successor id fans the writer out.
    """
    if not transfer_edges:
        out = _preview_invalidate(sdk, old_id, new_id)
        out["tool"] = "tortoise_supersede"
        out["would"] = "invalidate (transfer_edges=false)"
        out["transfer_edges"] = False
        return out
    if old_id == new_id:
        raise ValueError("supersede_point: old_id and new_id must differ")
    sdk._assert_lifecycle_guard(old_id, method="supersession", role="source")
    sdk._assert_lifecycle_guard(new_id, method="supersession", role="target")
    proj = sdk._get_proj()
    from tortoise.security import validate_rel_type
    created: list[dict] = []   # 2a operator edges — CREATE, never collapse
    merged: list[tuple] = []   # (merge_key, edge) — MERGE-backed legs
    dropped: list[dict] = []
    kept: list[dict] = []
    # INTERNAL ids of every edge the write removes from old (repointed OR
    # delete-only). Set-keyed so an edge matched by two passes is handled once;
    # `edges_remaining_at_old` is the incident count minus this set.
    handled_old_edge_ids: set = set()

    # 2a — operator edges. The writer validates every relationship type BEFORE
    # any mutation (a raw/imported undeclared type is an injection primitive),
    # so the preview validates too.
    for op_id, rtype, _idx, op_label, op_rid in proj.g.query(
        "MATCH (op:Point {is_operator:true})-[r]->(o:Point {id:$old}) "
        "RETURN op.id, type(r), r.idx, op.label, ID(r)",
        params={"old": old_id},
    ).result_set:
        validate_rel_type(rtype)
        if op_label == "alreadyDecided":
            kept.append({"type": rtype, "from": op_id, "to": old_id,
                         "reason": "alreadyDecided stays on the superseded prior"})
        else:
            created.append({"type": rtype, "from": op_id, "to": new_id})
            handled_old_edge_ids.add(op_rid)

    succ_rows = proj.g.query("MATCH (n:Point {id:$id}) RETURN ID(n)",
                            params={"id": new_id}).result_set
    # 2b's self-edge guard mirrors the writer EXACTLY: `succ_rows[0][0]` is a
    # scalar (the writer reads only the first successor row), so with a
    # duplicate successor id the writer still transfers an old edge that ends
    # at a LATER successor. `succ_count` models the writer's MATCH fan-out.
    succ_first = succ_rows[0][0] if succ_rows else None
    succ_count = max(len(succ_rows), 1)
    # Pre-existing edges at the successor: the MERGE-backed legs collapse into
    # an edge that is already there, so it is NOT net-new ("edges that would
    # transfer" must not claim a delta the write will not make). SDK-reachable
    # input: `new` already carrying an edge to the same destination.
    pre_out = {(r[0], r[1]) for r in proj.g.query(
        "MATCH (new:Point {id:$n})-[r]->(t) RETURN type(r), ID(t)",
        params={"n": new_id}).result_set}
    pre_in = {(r[0], r[1]) for r in proj.g.query(
        "MATCH (t)-[r]->(new:Point {id:$n}) RETURN type(r), ID(t)",
        params={"n": new_id}).result_set}

    # 2a-DIRECT — operator-less IMPL/NAND, both directions. The writer's
    # exclusion set is built from the far LOGICAL ids (a far node sharing an
    # operator's id is excluded even if it is not itself an operator), and its
    # repoint MERGE requires `(t:Point {id:$tid})` — a non-Point far endpoint
    # is deleted at old and creates nothing.
    seen_direct: set = set()
    for direction in ("out", "in"):
        if direction == "out":
            dq = ("MATCH (o:Point {id:$old})-[r:IMPL|NAND]->(t) "
                  "RETURN type(r), t.id, ID(t), labels(t), ID(r)")
        else:
            dq = ("MATCH (t)-[r:IMPL|NAND]->(o:Point {id:$old}) "
                  "RETURN type(r), t.id, ID(t), labels(t), ID(r)")
        direct_rows = proj.g.query(dq, params={"old": old_id}).result_set
        far_ids = [row[1] for row in direct_rows]
        op_ids = {r[0] for r in proj.g.query(
            "MATCH (p:Point) WHERE p.id IN $ids "
            "AND coalesce(p.is_operator,false) = true RETURN p.id",
            params={"ids": far_ids}).result_set} if far_ids else set()
        for rtype, tid, t_internal, t_labels, rid in direct_rows:
            if rid in seen_direct:
                continue  # a self-loop is matched by BOTH passes
            seen_direct.add(rid)
            if tid in op_ids:
                continue  # operator target — owned by 2a, never repointed
            handled_old_edge_ids.add(rid)
            if tid == old_id:
                dropped.append({"type": rtype, "other": tid,
                                "reason": "self-loop on the old node — its repoint is "
                                          "undone by the writer's in-pass delete-only"})
            elif tid == new_id:
                dropped.append({"type": rtype, "other": tid,
                                "reason": "far endpoint IS the successor — delete-only"})
            elif "Point" not in (t_labels or []):
                dropped.append({"type": rtype, "other": tid,
                                "reason": "far endpoint is not a Point — the repoint "
                                          "MERGE matches nothing (old edge removed)"})
            else:
                merged.append((
                    (rtype, direction, t_internal),
                    {"type": rtype,
                     "from": new_id if direction == "out" else tid,
                     "to": tid if direction == "out" else new_id},
                ))

    # 2b — structural rels (the writer's own constant — never a local copy).
    for rel in SUPERSEDE_STRUCTURAL_RELS:
        for rtype, tid, t_internal, rid in proj.g.query(
            f"MATCH (o:Point {{id:$old}})-[r:{rel}]->(t) "
            "RETURN type(r), coalesce(t.id,t.eventId,t.name,t.url), ID(t), ID(r)",
            params={"old": old_id},
        ).result_set:
            handled_old_edge_ids.add(rid)
            if succ_first is not None and t_internal == succ_first:
                dropped.append({"type": rtype, "other": tid or new_id,
                                "reason": "far endpoint IS the successor — delete-only"})
            else:
                merged.append(((rtype, "out", t_internal),
                               {"type": rtype, "from": new_id, "to": tid}))

    # Collapse ONLY the MERGE-backed legs, keyed on the far node's INTERNAL id
    # (the writer MERGEs by node identity, not by logical id).
    seen: set = set()
    merged_final: list[tuple] = []
    for key, edge in merged:
        if key in seen:
            continue
        seen.add(key)
        merged_final.append((key, edge))
    # A MERGE destination that already exists at the successor is a no-op.
    created_edges: list[dict] = list(created)  # 2a operator CREATEs always create
    already_present: list[dict] = []
    for (rtype, direction, t_internal), edge in merged_final:
        present = ((rtype, t_internal) in pre_out if direction == "out"
                   else (rtype, t_internal) in pre_in)
        (already_present if present else created_edges).append(edge)
    removed_raw = len(created) + len(merged) + len(dropped)
    # The writer's status write is a bare `MATCH (n:Point {id:$id}) SET …` — it
    # binds EVERY matching node (no cardinality guard), which is exactly what
    # `_preview_invalidate` already counts as `n_old`. Mirror it instead of
    # hardcoding 1.
    n_old = proj.g.query("MATCH (n:Point {id:$old}) RETURN count(n)",
                         params={"old": old_id}).result_set[0][0]
    # Every edge still incident to old after the write removes the handled set.
    # The writer ADDS only `(new)-[:CORRECTS]->(old)` (reported separately), and
    # that edge does not exist yet, so it is correctly absent here.
    incident_at_old = proj.g.query(
        "MATCH (o:Point {id:$old})-[r]-() RETURN count(r)",
        params={"old": old_id},
    ).result_set[0][0]
    return _preview_result(
        "tortoise_supersede", "supersede",
        found=True,
        transfer_edges=True,
        target={"old_id": old_id, "new_id": new_id},
        nodes_affected=n_old,
        nodes_removed=0,
        edges_transferred_from_old=removed_raw,
        edges_remaining_at_old=incident_at_old - len(handled_old_edge_ids),
        edges_created_at_new=len(created_edges) * succ_count,
        edges_already_present_at_new=len(already_present) * succ_count,
        successor_nodes=succ_count,
        edges_transferred=removed_raw,
        edges=created_edges + already_present,
        edges_dropped=dropped,
        edges_kept_attached=kept,
        corrects_edge={"type": "CORRECTS", "from": new_id, "to": old_id},
        status={"id": old_id, "to": "superseded", "outdated": True},
    )


# ── Tool Registry Adapter (#454) — registration (module bottom) ──
# Executes after EVERY module-level tool function definition above, so the
# handlers dict covers the whole registry: the seven onboarding tools and
# tortoise_session_capture used to be logged "no handler — skipped" (they
# were defined after this block's old mid-module position) — #2210.
from tortoise.tool_registry import TOOL_REGISTRY, GROUP_BY_NAME, FastMCPAdapter  # noqa: E402, I001

_adapter = FastMCPAdapter(mcp)
_adapter.register_all(TOOL_REGISTRY, {
    t.name: globals()[t.name]
    for t in TOOL_REGISTRY
    if t.name in globals()
})


# ── Retired names (#3883): a removed name RESOLVES and WARNS ────────────────
# #3836 (b): when a name is retired, a caller still gets an answer and is TOLD the
# name is retired, naming the replacement. A silent "tool not found" is not
# acceptable — which is why this exists BEFORE any name is retired (#3883 is a
# hard prerequisite for executing the #3863 removals).
#
# A retired name is deliberately NOT a registered component, so it is absent from
# `tools/list` and the advertised surface really does shrink. `_RetiredToolTransform`
# resolves it on `get_tool`, so `tools/call` still works. The shim reuses the
# ORIGINAL handler, so the answer is exactly what the live tool returned (same
# structured content, same inferred output schema); the warning is ADDED, never
# substituted. The warning rides BOTH the result content (so an agent sees it) and
# the result `_meta` (so a client can read it).


def _retired_warning(spec: Any) -> dict[str, Any]:
    """The machine-readable warning carried on the result and on the tool itself."""
    # The declared `sdk_method` is published only when it actually resolves. Five
    # registry entries declare a binding that does not exist (the #3838 drift), and
    # the generated doc marks them `~~method~~ (no such method)`; the runtime warning
    # is a machine-readable payload, so it must not assert as fact what the doc
    # calls out as a false declaration.
    from tortoise.sdk import TortoiseSDK

    declared = spec.sdk_method or None
    resolved = declared if declared and hasattr(TortoiseSDK, declared) else None
    return {
        "name": spec.name,
        "retired": True,
        "use_instead": spec.retired_use_instead,
        "sdk_method": resolved,
        "sdk_method_exists": resolved is not None,
        "message": (
            f"RETIRED TOOL: `{spec.name}` has been retired from the Tortoise MCP "
            f"surface. It still answers, but it is no longer advertised. Call "
            f"`{spec.retired_use_instead}` instead (#3883)."
        ),
    }


def _warn_retired_result(base: Any, spec: Any) -> Any:
    """The result the live tool produced, plus a warning that the name is retired."""
    from fastmcp.tools.base import ToolResult
    from mcp.types import TextContent

    if not isinstance(base, ToolResult):
        return base

    warning = _retired_warning(spec)
    meta = dict(base.meta or {})
    tortoise_meta = meta.get("tortoise")
    meta["tortoise"] = {
        **(tortoise_meta if isinstance(tortoise_meta, dict) else {}),
        "retired": warning,
    }
    # The warning goes LAST, not first: the payload stays `content[0]` and
    # `structured_content` is untouched, so a caller that reads the payload — the
    # normal path — is byte-identical to the live tool. Only a caller of the
    # RETIRED name sees the extra block, and seeing it is the point (#3883).
    return ToolResult(
        content=[*base.content, TextContent(type="text", text=warning["message"])],
        structured_content=base.structured_content,
        meta=meta,
        is_error=base.is_error,
    )


def build_retired_tools(retired_registry: list[Any], handlers: dict[str, Any]) -> dict[str, Any]:
    """Build the retired-name shims: same schema, same answer, plus a warning."""
    import functools

    from fastmcp.tools import FunctionTool

    def _make_shim(original: Any, base: Any, spec: Any) -> Any:
        # A factory, not a loop-local closure: a bare `def` inside the loop would
        # capture the LOOP variable and every shim would call the last handler.
        @functools.wraps(original)
        def retired_fn(*args, **kwargs):
            return _warn_retired_result(
                base.convert_result(original(*args, **kwargs)), spec
            )

        return retired_fn

    shims: dict[str, Any] = {}
    for spec in retired_registry:
        original = handlers.get(spec.name)
        if original is None:
            continue
        # `base` is the tool this name WOULD have been, so `convert_result` yields
        # byte-identical structured output (incl. the `x-fastmcp-wrap-result`
        # envelope for list-returning handlers).
        base = FunctionTool.from_function(
            original, name=spec.name,
            description=spec.description, annotations=spec.annotations,
        )
        retired_fn = _make_shim(original, base, spec)
        retired_fn.__doc__ = (
            f"RETIRED — use {spec.retired_use_instead}. {spec.description}"
        )
        shims[spec.name] = FunctionTool.from_function(
            retired_fn, name=spec.name, description=retired_fn.__doc__,
            annotations=spec.annotations,
            meta={"tortoise": {"retired": _retired_warning(spec)}},
        )
    return shims


from fastmcp.server.transforms import Transform  # noqa: E402


class _RetiredToolTransform(Transform):
    """Serve retired names on `get_tool` (with a warning) without advertising them.

    `list_tools` strips them so the advertised surface shrinks; `get_tool` falls
    back to the shim when no live tool owns the name. Registered unconditionally,
    even with zero retired names, so the gate reads the transform set from the
    source and a name can never be retired without the gate noticing.
    """

    def __init__(self, shims: dict[str, Any]) -> None:
        self._shims = dict(shims)

    async def list_tools(self, tools: Any) -> Any:
        return [t for t in tools if getattr(t, "name", None) not in self._shims]

    async def get_tool(self, name: str, call_next: Any, *, version: Any = None) -> Any:
        tool = await call_next(name, version=version)
        if tool is not None:
            return tool
        return self._shims.get(name)


from tortoise.tool_registry import RETIRED_TOOL_REGISTRY  # noqa: E402

_RETIRED_SHIMS = build_retired_tools(RETIRED_TOOL_REGISTRY, {
    t.name: globals()[t.name]
    for t in RETIRED_TOOL_REGISTRY
    if t.name in globals()
})
if not getattr(mcp, "_retired_tool_transform_registered", False):
    mcp.add_transform(_RetiredToolTransform(_RETIRED_SHIMS))
    mcp._retired_tool_transform_registered = True

if __name__ == "__main__":
    main()
