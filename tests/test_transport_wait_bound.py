"""#3834 — the transport-level wait bound: ONE bound, a legible refusal, and the
retry signal shipped with it.

Owner ruling 2026-09-20 (issue #3834, comment 5752962331) re-homed the cold/busy
question from the removed ask route onto the TRANSPORT. This file pins the two
properties that ruling actually asks for:

* **one bound, at the transport** — not per-route. The REST routes are bounded
  by ``WaitBoundMiddleware``; MCP tool calls are bounded at the single MCP
  dispatch seam (``mcp.call_tool``), because FastMCP's Streamable-HTTP
  transport starts the SSE response BEFORE dispatching the tool, which makes
  the middleware inert for exactly that population (round-2 P1). Either way the
  bound applies across the surface with no per-tool edit;
* **a readable refusal on breach** — what happened, whether to retry, how long —
  and the retry signal (`Retry-After` / ``error.data.retry_after``) ships in the
  SAME unit, because a bound alone turns an invisible failure into a visible one
  with no recovery.

It also pins the thing most likely to be "tidied" by a later reader: the refusal
REUSES the two formats that already exist (the §6.1 ``{"detail"}`` +
``Retry-After`` shape the rest of this app speaks, and ``mcp_auth._jsonrpc_error``
— the primitive #3851 shipped the auth-plane ``Retry-After`` through). A new
parallel format is a failure of this test, not a refactor.

The cold half of the same ruling (edge ingress queue, readiness gate + client
retry) is a VERIFICATION, not a build — see
``test_cold_half_readiness_gate_holds_or_is_reported`` at the bottom.
"""

from __future__ import annotations

import asyncio
import json
import re
import time

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from tortoise import hosted_api as ha
from tortoise.mcp_auth import ERR_TIMEOUT

# ── a minimal ASGI harness ────────────────────────────────────────────────
# The bound is a transport concern, so most cases are pinned against a tiny app
# rather than the 25k-line hosted app: a deterministic slow handler is the whole
# point, and routing the real app's auth/DB would test something else.


def _scope(path="/v1/thing", method="GET", headers=()):
    return {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(k.lower().encode("latin-1"), v.encode("latin-1"))
                    for k, v in headers],
        "scheme": "https",
        "server": ("testserver", 443),
        "client": ("203.0.113.7", 51234),
        "root_path": "",
    }


async def _receive():
    return {"type": "http.request", "body": b"", "more_body": False}


class _Recorder:
    """Collects the ASGI messages a middleware emits."""

    def __init__(self):
        self.messages = []

    async def __call__(self, message):
        self.messages.append(message)

    # helpers -------------------------------------------------------------
    @property
    def starts(self):
        return [m for m in self.messages if m["type"] == "http.response.start"]

    @property
    def status(self):
        return self.starts[0]["status"] if self.starts else None

    def headers(self):
        return {k.decode("latin-1").lower(): v.decode("latin-1")
                for k, v in (self.starts[0].get("headers") or [])} \
            if self.starts else {}

    @property
    def body(self):
        chunks = b"".join(m.get("body", b"") for m in self.messages
                          if m["type"] == "http.response.body")
        return chunks

    @property
    def json(self):
        return json.loads(self.body)


def _slow_app(delay: float, *, started=None, finished=None, status=200):
    """An ASGI app that takes ``delay`` seconds to answer.

    ``started`` / ``finished`` are optional mutable lists the app appends to, so
    a test can observe whether a breached handler was allowed to RUN TO
    COMPLETION rather than cancelled mid-flight.
    """
    async def app(scope, receive, send):
        if started is not None:
            started.append(True)
        await asyncio.sleep(delay)
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": b'{"ok": true}'})
        if finished is not None:
            finished.append(True)

    return app


async def _drive(mw, scope, recorder=None):
    recorder = recorder or _Recorder()
    await mw(scope, _receive, recorder)
    return recorder


@pytest.fixture
def fast_bound(monkeypatch):
    """A bound small enough to breach in a test without a 10 s wall-clock wait."""
    monkeypatch.setattr(ha, "_TRANSPORT_WAIT_BOUND_S", 0.05)
    return 0.05


# ── the recorded value, and the one exemption ─────────────────────────────

def test_bound_and_retry_signal_are_the_recorded_values():
    """The number is the owner's (10 s under the 15 s client budget), and the
    advertised back-off is the single source the message deliberately omits."""
    assert ha._TRANSPORT_WAIT_BOUND_S == 10.0
    assert ha._TRANSPORT_WAIT_RETRY_AFTER_S == 2


def test_the_only_exemption_is_post_context_and_it_is_method_scoped():
    """The ruling exempts `POST /v1/context` because its fail-open ceiling is a
    recorded decision. `GET /v1/context` is a DIFFERENT handler with no such
    record, so exempting it would widen the ruling past its evidence."""
    assert frozenset({("POST", "/v1/context")}) == ha._TRANSPORT_WAIT_BOUND_EXEMPT
    assert ("GET", "/v1/context") not in ha._TRANSPORT_WAIT_BOUND_EXEMPT


def test_refusal_message_is_readable_and_digit_free():
    """What happened / whether to retry / how long — and no literal number, so
    the advertised delay has exactly one source (`_TRANSPORT_WAIT_RETRY_AFTER_S`)
    and the message stays true on a surface that carries no header."""
    msg = ha._TRANSPORT_WAIT_BOUND_MESSAGE
    assert not re.search(r"\d", msg), f"message carries a literal number: {msg!r}"
    lowered = msg.lower()
    assert "wait budget" in lowered          # what happened
    assert "retry" in lowered                # whether to retry
    assert "advertised delay" in lowered     # how long


# ── breach: the refusal, in the existing formats ──────────────────────────

@pytest.mark.asyncio
async def test_fast_request_is_untouched(fast_bound):
    mw = ha.WaitBoundMiddleware(_slow_app(0.0))
    rec = await _drive(mw, _scope())
    assert rec.status == 200
    assert rec.json == {"ok": True}


@pytest.mark.asyncio
async def test_breach_is_a_504_with_retry_after_and_a_readable_body(fast_bound):
    mw = ha.WaitBoundMiddleware(_slow_app(5.0))
    rec = await _drive(mw, _scope())
    assert rec.status == 504
    assert rec.headers()["retry-after"] == str(ha._TRANSPORT_WAIT_RETRY_AFTER_S)
    # Reuses the §6.1 shape — NOT a parallel `{"error": {...}}` vocabulary.
    assert rec.json == {"detail": ha._TRANSPORT_WAIT_BOUND_MESSAGE}


@pytest.mark.asyncio
async def test_mcp_surface_gets_the_jsonrpc_refusal(fast_bound):
    """A `/mcp/…` request that stalls BEFORE its SSE response begins gets the
    #3851 JSON-RPC refusal shape (504 + `error.data.retry_after`).

    ⚠️ This does NOT mean the middleware bounds MCP tool calls — it cannot.
    For a real tool call the SDK has already started the SSE response, so the
    middleware's `response_started` branch lets it finish; that bound lives in
    `mcp_server._await_under_mcp_wait_bound` and is pinned by
    `test_mcp_http_sse_path_delivers_the_refusal`."""
    mw = ha.WaitBoundMiddleware(_slow_app(5.0))
    rec = await _drive(mw, _scope("/mcp/", method="POST"))
    assert rec.status == 504
    assert rec.headers()["retry-after"] == str(ha._TRANSPORT_WAIT_RETRY_AFTER_S)
    body = rec.json
    assert body["jsonrpc"] == "2.0"
    assert body["error"]["code"] == ERR_TIMEOUT
    assert body["error"]["data"]["retry_after"] == ha._TRANSPORT_WAIT_RETRY_AFTER_S
    assert body["error"]["message"] == ha._TRANSPORT_WAIT_BOUND_MESSAGE


@pytest.mark.asyncio
async def test_mcp_prefix_is_boundary_exact(fast_bound):
    """`/mcpfoo` is a sibling route, never the MCP surface — the same boundary
    the path canonicalizer uses. It must get the REST refusal, not JSON-RPC."""
    mw = ha.WaitBoundMiddleware(_slow_app(5.0))
    rec = await _drive(mw, _scope("/mcpfoo"))
    assert rec.status == 504
    assert set(rec.json) == {"detail"}


@pytest.mark.asyncio
async def test_refusal_carries_cors_headers(fast_bound):
    """The bound is OUTERMOST, so CORSMiddleware never sees its response. Without
    these a browser client reads the refusal as 'CORS blocked' and cannot read
    WHY it was made to wait — the exact illegibility this unit exists to fix
    (#1591 is the same position problem for 500s)."""
    mw = ha.WaitBoundMiddleware(_slow_app(5.0))
    rec = await _drive(mw, _scope(headers=[("origin",
                                            "https://app.premiselabs.co")]))
    h = rec.headers()
    assert h["access-control-allow-origin"] == "https://app.premiselabs.co"
    assert h["access-control-allow-credentials"] == "true"
    assert "retry-after" in h["access-control-expose-headers"].lower()


@pytest.mark.asyncio
async def test_refusal_carries_security_headers(fast_bound):
    """The bound is OUTERMOST, so ``HSTSMiddleware`` and
    ``SecurityHeadersMiddleware`` never see its response. They document these
    headers as present on "every response" — a breach response must not be the
    one response missing them (code-review round 2)."""
    mw = ha.WaitBoundMiddleware(_slow_app(5.0))
    rec = await _drive(mw, _scope())
    h = rec.headers()
    assert h["strict-transport-security"] == "max-age=31536000; includeSubDomains"
    assert h["x-content-type-options"] == "nosniff"
    assert h["x-frame-options"] == "DENY"
    assert h["x-xss-protection"] == "1; mode=block"


@pytest.mark.asyncio
async def test_lifespan_scope_is_passed_through(fast_bound):
    seen = []

    async def app(scope, receive, send):
        seen.append(scope["type"])

    mw = ha.WaitBoundMiddleware(app)
    await mw({"type": "lifespan"}, _receive, _Recorder())
    assert seen == ["lifespan"]


# ── the exemption ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_post_context_is_exempt(fast_bound):
    mw = ha.WaitBoundMiddleware(_slow_app(0.2, status=200))
    rec = await _drive(mw, _scope("/v1/context", method="POST"))
    assert rec.status == 200, "POST /v1/context must keep its own fail-open path"


@pytest.mark.asyncio
async def test_get_context_is_bounded(fast_bound):
    mw = ha.WaitBoundMiddleware(_slow_app(5.0))
    rec = await _drive(mw, _scope("/v1/context", method="GET"))
    assert rec.status == 504


# ── the handler is left running, never cancelled ──────────────────────────

@pytest.mark.asyncio
async def test_breached_handler_runs_to_completion_and_its_late_reply_is_dropped(
        fast_bound):
    """Do NOT cancel. This module's own doctrine (#2988, #3718) is that
    cancelling the await stops the AWAITABLE, not the worker thread, while
    running every `finally:` the handler owns — and 14 handlers here close their
    SDK in a finally. A cancel would tear the projection down under work still
    using it. So the work finishes and the response it no longer owns is DROPPED
    (an ASGI send after response.end is a protocol violation)."""
    finished = []
    mw = ha.WaitBoundMiddleware(_slow_app(0.2, finished=finished))
    rec = await _drive(mw, _scope())
    assert rec.status == 504
    assert len(rec.starts) == 1, "the refusal is the only response start"

    for _ in range(100):
        if finished:
            break
        await asyncio.sleep(0.02)
    assert finished == [True], "the breached handler was cancelled, not abandoned"
    assert len(rec.starts) == 1, "the abandoned handler's late reply leaked out"


@pytest.mark.asyncio
async def test_cancellation_propagates_into_the_handler(monkeypatch):
    """A caller that goes away must reach the handler's OWN cancellation path.

    The middleware runs the handler in a CHILD task (it must, to substitute a
    refusal on breach). Naively awaiting that child under ``asyncio.wait`` sends
    the cancellation no further than the middleware, so a disconnected request
    keeps running unseen — on the app side that is #3129: a capture cancelled
    after its extraction never records the failed attempt, and the next
    same-session request replays it as a silent 0-turn success. The transport
    half of that contract is here: the handler must see ``CancelledError``.
    Cancelling (and awaiting) is correct on THIS path only; the breach path
    above still abandons on purpose.
    """
    monkeypatch.setattr(ha, "_TRANSPORT_WAIT_BOUND_S", 30.0)
    entered = asyncio.Event()
    saw_cancel: list = []

    async def app(scope, receive, send):
        entered.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            saw_cancel.append(True)
            raise

    job = asyncio.ensure_future(_drive(ha.WaitBoundMiddleware(app), _scope()))
    await entered.wait()
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job
    assert saw_cancel == [True], (
        "the handler never saw the cancellation — the middleware consumed it, "
        "so app-side abandonment markers (#3129) would never run")


@pytest.mark.asyncio
async def test_cancellation_cleanup_error_propagates(monkeypatch):
    """A REAL error from the handler's cancellation cleanup must surface.

    The middleware suppresses only the child's CancelledError. A broader
    `suppress(BaseException)` would retrieve and discard a genuine cleanup
    failure, where the direct await this middleware replaced surfaced it — so
    the suppression's narrowness is load-bearing and is pinned here.
    """
    monkeypatch.setattr(ha, "_TRANSPORT_WAIT_BOUND_S", 30.0)
    entered = asyncio.Event()

    async def app(scope, receive, send):
        entered.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            raise RuntimeError("cleanup boom") from None

    job = asyncio.ensure_future(_drive(ha.WaitBoundMiddleware(app), _scope()))
    await entered.wait()
    job.cancel()
    with pytest.raises(RuntimeError, match="cleanup boom"):
        await job


@pytest.mark.asyncio
async def test_an_already_started_response_is_never_replaced(monkeypatch):
    """A response that has already begun streaming cannot be substituted — we
    let it finish rather than emit a second, contradictory status."""
    monkeypatch.setattr(ha, "_TRANSPORT_WAIT_BOUND_S", 0.05)

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"application/json")]})
        await asyncio.sleep(0.15)
        await send({"type": "http.response.body", "body": b'{"ok": true}'})

    rec = await _drive(ha.WaitBoundMiddleware(app), _scope())
    assert len(rec.starts) == 1
    assert rec.status == 200
    assert rec.json == {"ok": True}


# ── the breach is recorded, OFF the request path ──────────────────────────

@pytest.mark.asyncio
async def test_breach_is_recorded_off_the_request_path(fast_bound, monkeypatch):
    """`_track_analytics_event` does a BLOCKING httpx POST. On the request path
    it would create the very latency this bound exists to cut, so it is
    dispatched fire-and-forget onto the EXISTING best-effort control-plane
    telemetry seam (`monitoring.control_plane_worker("telemetry")`, #3498 —
    daemon workers, bounded backlog), never the loop's shared default pool."""
    seen = []

    def _blocking_writer(org_id, event_name, properties):
        time.sleep(0.4)          # stand-in for the blocking POST
        seen.append((org_id, event_name, properties))

    monkeypatch.setattr(ha, "_track_analytics_event", _blocking_writer)
    mw = ha.WaitBoundMiddleware(_slow_app(5.0))
    t0 = time.monotonic()
    rec = await _drive(mw, _scope("/v1/points", method="POST"))
    elapsed = time.monotonic() - t0
    assert rec.status == 504
    assert elapsed < 0.3, (
        f"the refusal waited on the analytics write ({elapsed:.2f}s) — the "
        "writer is on the request path")

    for _ in range(200):
        if seen:
            break
        await asyncio.sleep(0.02)
    assert len(seen) == 1
    org_id, event_name, props = seen[0]
    assert event_name == ha._TRANSPORT_WAIT_BOUND_EVENT
    assert props["path"] == "/v1/points"
    assert props["method"] == "POST"
    assert props["latency_ms"] >= 50
    assert set(props) <= ha._ALLOWED_ANALYTICS_PROPS, (
        "prop keys would be silently stripped by the PII filter")
    assert org_id == "", "no org resolved at the transport layer yet"


# ── wiring on the real app ────────────────────────────────────────────────

def test_middleware_is_installed_outermost():
    """OUTERMOST so it covers the middleware stack itself (auth, rate limit) and
    so a short-circuiting middleware cannot escape the bound."""
    outer = ha.app.user_middleware[0].cls
    assert outer is ha.WaitBoundMiddleware


def test_real_app_liveness_route_is_unaffected():
    """The bound must not disturb the paths Fly probes. `/health` is in-memory
    and never gates on the DB, so it cannot approach the bound."""
    with TestClient(ha.app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["db"] is not None


def test_real_app_breach_refusal_is_the_same_shape(monkeypatch):
    """End-to-end through the real stack: a route that breaches returns the same
    §6.1 504 shape and carries the retry signal, CORS included."""
    monkeypatch.setattr(ha, "_TRANSPORT_WAIT_BOUND_S", 0.05)

    async def _slow_route(request):
        await asyncio.sleep(0.3)
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/slow", _slow_route)])
    app.add_middleware(ha.WaitBoundMiddleware)
    with TestClient(app) as client:
        r = client.get("/slow", headers={"Origin": "https://app.premiselabs.co"})
    assert r.status_code == 504
    assert r.headers["retry-after"] == str(ha._TRANSPORT_WAIT_RETRY_AFTER_S)
    assert r.headers["access-control-allow-origin"] == "https://app.premiselabs.co"
    assert r.json() == {"detail": ha._TRANSPORT_WAIT_BOUND_MESSAGE}


# ── the MCP surface: the bound is enforced at the DISPATCH ────────────────
# Round-2 P1: the ASGI middleware is INERT for MCP tool calls. FastMCP runs
# Streamable-HTTP in SSE mode and starts the EventSourceResponse BEFORE
# dispatching the tool, so `http.response.start` is already on the wire when
# the deadline fires and the middleware can only let the request finish. These
# tests drive the REAL MCP dispatch — in process and through the mounted SSE
# transport — and are RED for a middleware-only bound (the P1's exact
# failure mode: a bound that reports success while bounding nothing).

_MCP_HEADERS = {"Accept": "application/json, text/event-stream",
                "Content-Type": "application/json"}


def _parse_sse(text: str):
    """Parse an MCP Streamable-HTTP body that may be SSE-framed."""
    if text.startswith("event:") or "\ndata: " in text:
        for line in text.splitlines():
            if line.startswith("data: "):
                return json.loads(line[len("data: "):])
        return None
    return json.loads(text)


@pytest.fixture
def mcp_slow_tool():
    """Register a slow test tool on the shared mcp instance; remove after.

    Yields a list the tool appends to on completion, so a test can prove the
    breached dispatch was ABANDONED (ran to completion) rather than cancelled.
    """
    from fastmcp.tools import FunctionTool

    from tortoise import mcp_server as ms

    finished: list = []

    async def _bound_slow() -> dict:
        await asyncio.sleep(0.3)
        finished.append(True)
        return {"ok": True}

    ms.mcp.add_tool(FunctionTool.from_function(
        _bound_slow, name="_bound_slow", description="wait-bound test: slow"))
    try:
        yield finished
    finally:
        try:  # noqa: SIM105
            ms.mcp.local_provider.remove_tool("_bound_slow")
        except Exception:
            pass
        # A TestClient's loop closes with the abandoned task still pending;
        # drop the leftover reference so it cannot bleed into a later test.
        ms._pending_mcp_wait_bound.clear()


@pytest.mark.asyncio
async def test_mcp_dispatch_is_bounded_with_the_shipped_refusal(
        fast_bound, mcp_slow_tool, monkeypatch):
    """The MCP dispatch itself is bounded — this is the function that fires for
    the measured `mcp_tool_call` population. The refusal carries the SHIPPED
    message and the advertised delay (no second vocabulary)."""
    from tortoise import mcp_server as ms

    monkeypatch.setattr(ha, "_track_analytics_event",
                        lambda org, ev, props: None)
    result = await ms.mcp.call_tool("_bound_slow")
    assert result.is_error is True
    assert result.meta == {ms._WAIT_BOUND_META_KEY: True}
    payload = result.structured_content["error"]
    assert payload["code"] == ERR_TIMEOUT
    assert payload["message"] == ha._TRANSPORT_WAIT_BOUND_MESSAGE
    assert payload["data"]["retry_after"] == ha._TRANSPORT_WAIT_RETRY_AFTER_S
    # Abandoned, never cancelled: the handler runs to completion.
    for _ in range(100):
        if mcp_slow_tool:
            break
        await asyncio.sleep(0.02)
    assert mcp_slow_tool == [True], "the breached dispatch was cancelled, not abandoned"


def test_mcp_http_sse_path_delivers_the_refusal(
        fast_bound, mcp_slow_tool, monkeypatch):
    """END-TO-END through the real mounted Streamable-HTTP app in its default
    SSE mode — the mode in which the middleware is inert. A tool call past the
    bound comes back as a legible refusal carrying the retry signal, not as a
    late success. This is the test the P1 says was missing."""
    from contextlib import asynccontextmanager

    from starlette.applications import Starlette
    from starlette.routing import Mount
    from starlette.testclient import TestClient

    from tortoise import mcp_server as ms

    monkeypatch.setattr(ha, "_track_analytics_event",
                        lambda org, ev, props: None)
    app = ms.create_http_app(auth_mode="none")

    @asynccontextmanager
    async def _lifespan(parent_app):
        async with app.lifespan(app):
            yield

    parent = Starlette(lifespan=_lifespan, routes=[Mount("/mcp", app=app)])
    with TestClient(parent) as client:
        r = client.post("/mcp", headers=_MCP_HEADERS, json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "_bound_slow", "arguments": {}}})
    assert r.status_code == 200, r.text
    body = _parse_sse(r.text)
    assert body is not None, r.text
    payload = body["result"]
    assert payload["isError"] is True, (
        "the MCP call returned a success — the bound did not fire on the SSE "
        "path, which is the round-2 P1")
    assert payload["_meta"][ms._WAIT_BOUND_META_KEY] is True
    err = payload["structuredContent"]["error"]
    assert err["code"] == ERR_TIMEOUT
    assert err["message"] == ha._TRANSPORT_WAIT_BOUND_MESSAGE
    assert err["data"]["retry_after"] == ha._TRANSPORT_WAIT_RETRY_AFTER_S


@pytest.mark.asyncio
async def test_mcp_abandonment_holds_the_2850_workload_gauge(
        fast_bound, mcp_slow_tool, monkeypatch):
    """#2850 invariant on the MCP seam. Round 1 moved InFlightMiddleware inside
    WaitBoundMiddleware so an abandoned REST handler keeps the gauge non-idle
    until it genuinely finishes. That ordering does NOT cover this seam: the
    abandoned dispatch is a task created inside the MCP app, while the HTTP POST
    the gauge wraps ends as soon as the SSE refusal is written. Without an
    explicit hold, workload_is_idle() reads True while the abandoned tail
    (p99 22.5 s) still runs — the exact #2850 self-kill mis-read, on the
    population this unit measures."""
    from tortoise import mcp_server as ms
    from tortoise import monitoring

    monkeypatch.setattr(ha, "_track_analytics_event",
                        lambda org, ev, props: None)
    baseline = monitoring.workload_in_flight()
    result = await ms.mcp.call_tool("_bound_slow")
    assert result.meta == {ms._WAIT_BOUND_META_KEY: True}
    assert monitoring.workload_in_flight() > baseline, (
        "the abandoned MCP dispatch released the gauge — the #2850 self-kill "
        "predicate reads idle while it still runs")
    for _ in range(100):
        if mcp_slow_tool:
            break
        await asyncio.sleep(0.02)
    assert mcp_slow_tool == [True]
    for _ in range(100):
        if monitoring.workload_in_flight() == baseline:
            break
        await asyncio.sleep(0.02)
    assert monitoring.workload_in_flight() == baseline, (
        "the gauge leaked after the abandoned dispatch finished")


@pytest.mark.asyncio
async def test_mcp_cancellation_propagates_into_the_tool(monkeypatch):
    """The MCP seam's CANCELLATION path cancels the dispatch and awaits it.

    Same contract as the REST half (``test_cancellation_propagates_into_the_
    handler``) but for the seam that has its own bookkeeping: a regression that
    reverted the MCP branch to abandon-on-cancel would leave a cancelled
    dispatch running and untracked, and no other test would see it.
    """
    from fastmcp.tools import FunctionTool

    from tortoise import mcp_server as ms

    started = asyncio.Event()
    saw_cancel: list = []

    async def _bound_cancel_probe() -> dict:
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            saw_cancel.append(True)
            raise
        return {"ok": True}

    ms.mcp.add_tool(FunctionTool.from_function(
        _bound_cancel_probe, name="_bound_cancel",
        description="wait-bound test: cancellation probe"))
    monkeypatch.setattr(ha, "_TRANSPORT_WAIT_BOUND_S", 30.0)
    monkeypatch.setattr(ha, "_track_analytics_event",
                        lambda *a, **k: None)
    try:
        job = asyncio.ensure_future(ms.mcp.call_tool("_bound_cancel"))
        await asyncio.wait_for(started.wait(), timeout=5)
        job.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job
        assert saw_cancel == [True], (
            "the tool never saw the cancellation — the MCP seam consumed it")
        assert not ms._pending_mcp_wait_bound, (
            "a cancelled dispatch was booked as an abandoned timeout task")
    finally:
        try:  # noqa: SIM105
            ms.mcp.local_provider.remove_tool("_bound_cancel")
        except Exception:
            pass
        ms._pending_mcp_wait_bound.clear()


@pytest.mark.asyncio
async def test_mcp_cancellation_cleanup_error_propagates(monkeypatch):
    """The MCP seam surfaces a real error from the tool's cancellation cleanup.

    Same load-bearing narrowness as the REST half: `suppress(BaseException)`
    would swallow it.
    """
    from fastmcp.exceptions import ToolError
    from fastmcp.tools import FunctionTool

    from tortoise import mcp_server as ms

    entered = asyncio.Event()

    async def _bound_cleanup_boom() -> dict:
        entered.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            raise RuntimeError("cleanup boom") from None

    ms.mcp.add_tool(FunctionTool.from_function(
        _bound_cleanup_boom, name="_bound_cleanup_boom",
        description="wait-bound test: raising cancellation cleanup"))
    monkeypatch.setattr(ha, "_TRANSPORT_WAIT_BOUND_S", 30.0)
    monkeypatch.setattr(ha, "_track_analytics_event", lambda *a, **k: None)
    try:
        job = asyncio.ensure_future(ms.mcp.call_tool("_bound_cleanup_boom"))
        await asyncio.wait_for(entered.wait(), timeout=5)
        job.cancel()
        # FastMCP wraps a tool-body exception in ToolError; the point is that a
        # REAL error surfaces instead of only the cancellation.
        with pytest.raises(ToolError, match="cleanup boom"):
            await job
    finally:
        try:  # noqa: SIM105
            ms.mcp.local_provider.remove_tool("_bound_cleanup_boom")
        except Exception:
            pass


# ── the cold half: a VERIFICATION, not a build ────────────────────────────

def test_cold_half_readiness_gate_holds_or_is_reported():
    """The ruling's cold half is a verification: cold traffic waits in the edge's
    ingress queue, so an application-level timeout cannot help it — the remedy is
    a readiness gate + client retry, which we already have.

    Verified at this head, from the artifacts:
      * `fly.toml` — `auto_stop_machines = "off"` + `min_machines_running = 1`,
        so the machine-cold case does not arise by construction;
      * `fly.toml` — `[checks.loop_liveness]` (port 9090 `/healthz`, reinstated
        2026-09-17) is present;
      * `tortoise/hosted_api.py` — `@app.get("/health/ready")` exists and is
        fail-closed (503), off-loop and hard-bounded;
      * the readiness behaviour itself is pinned by
        `tests/test_health_ready_nonblocking.py`.

    This test asserts the WIRING so a later edit that silently drops one of them
    is caught here rather than in production. It deliberately asserts nothing
    about the readiness handler's internals — those have their own file.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    fly = (root / "fly.toml").read_text()
    assert 'auto_stop_machines = "off"' in fly
    assert "min_machines_running = 1" in fly
    assert "[checks.loop_liveness]" in fly, "the reinstated liveness check is gone"

    from tortoise.hosted_api import health_ready

    assert callable(health_ready)
    paths = {getattr(r, "path", None) for r in ha.app.routes}
    assert "/health/ready" in paths


# ── honest coverage pins (security + a stated limitation) ────────────────

@pytest.mark.asyncio
async def test_breach_path_is_sanitized_before_the_log_and_the_sink(
        fast_bound, monkeypatch):
    """The ASGI server percent-DECODES the path, so a request to
    `/v1/x/%0d%0aFORGED` arrives with embedded CR/LF — logged verbatim that
    forges log lines, and stored verbatim it reaches the analytics sink. The
    refusal path sanitizes ONCE, for both. The class is not CR/LF alone
    (code-review round 2): VT/FF/ESC/NUL, DEL, the C1 range (U+0085 NEL and
    U+009B CSI — line-break and escape introducers to Unicode-aware readers)
    and U+2028/U+2029 forge lines or inject terminal escapes too, so the full
    C0/C1 + DEL range is escaped."""
    seen = []
    monkeypatch.setattr(ha, "_track_analytics_event",
                        lambda org, ev, props: seen.append(props))
    mw = ha.WaitBoundMiddleware(_slow_app(5.0))
    raw = ("/v1/points/foo\r\nFORGED\x1b[31m\x0b\x0c\x00\u2028\u2029"
           "\u0085NEL\u009bCSI\x7f")
    rec = await _drive(mw, _scope(raw))
    assert rec.status == 504
    for _ in range(200):
        if seen:
            break
        await asyncio.sleep(0.02)
    assert seen, "breach telemetry never fired"
    stored = seen[0]["path"]
    for ch in ("\r", "\n", "\x1b", "\x0b", "\x0c", "\x00", "\u2028",
               "\u2029", "\u0085", "\u009b", "\x7f"):
        assert ch not in stored, (
            f"raw control char {ch!r} reached the analytics sink: {stored!r}")
    assert stored == (
        "/v1/points/foo\\r\\nFORGED\\x1b[31m\\x0b\\x0c\\x00\\u2028\\u2029"
        "\\x85NEL\\x9bCSI\\x7f"), (f"unexpected sanitized form: {stored!r}")


@pytest.mark.asyncio
async def test_synchronous_handler_is_not_bounded_it_is_stated_not_assumed(
        fast_bound):
    """HONEST LIMITATION PIN: an `asyncio` deadline only fires while the loop
    runs, so a handler that blocks the loop synchronously cannot be converted
    into a refusal — it returns 200. This module still runs synchronous work on
    the loop (#3060/#3718), where the bound is strictly additive, never a cure.
    Pinned so the limit is visible rather than assumed; if this ever returns 504
    the bound gained on-loop preemption and the docstring must change."""
    async def app(scope, receive, send):
        time.sleep(0.2)          # blocks the loop — the timer cannot fire
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": b'{"ok": true}'})

    mw = ha.WaitBoundMiddleware(app)
    rec = await _drive(mw, _scope())
    assert rec.status == 200, (
        "the loop was blocked, so the bound could not fire")
    assert rec.json == {"ok": True}
