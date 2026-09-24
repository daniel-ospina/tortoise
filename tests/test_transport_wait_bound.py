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
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from tortoise import hosted_api as ha
from tortoise import mcp_auth as ma
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
    """A bound small enough to breach in a test without a 10 s wall-clock wait.

    Patches the CANONICAL constant's single home (#3834 F7): both surfaces — the
    REST middleware in ``hosted_api`` and the MCP seam in ``mcp_server`` — read
    ``mcp_auth``'s attribute, so one patch reaches both. There is no second copy
    to forget.
    """
    monkeypatch.setattr(ma, "_TRANSPORT_WAIT_BOUND_S", 0.05)
    return 0.05


# ── the recorded value, and the one exemption ─────────────────────────────

def test_bound_and_retry_signal_are_the_recorded_values():
    """The number is the owner's (10 s under the 15 s client budget), and the
    advertised back-off GREW with the bound (#3834 F3) rather than inviting a
    re-entry into the abandoned work. The constants live in ``mcp_auth`` (#3834
    F7) — both surfaces read ONE home.
    """
    assert ma._TRANSPORT_WAIT_BOUND_S == 10.0
    assert ma._TRANSPORT_WAIT_RETRY_AFTER_S == 10
    # No second copy: hosted_api reads the SAME module, not an imported snapshot.
    assert ha._mcp_auth is ma


def test_retry_signal_is_never_shorter_than_the_bound():
    """#3834 F3: the retry signal must not invite overlap.

    Every breach is caused by work that EXCEEDED the bound, so an advertised
    delay shorter than the bound tells a compliant caller to re-enter the SAME
    slow operation while the abandoned attempt is still running. The SHIPPED
    pair is 10/10, which buys a FLOOR, not an elimination: a compliant caller
    cannot re-enter before the bound elapses, so overlap now requires work that
    outlives ``bound + retry_after`` (≈20 s) — and the measured tail still
    exceeds that (p99 22,476 ms, max 157,116 ms in the population recorded in
    ``mcp_auth.py``), so a retry can still land while the abandoned original
    runs. This record is for the PRE-FIX pair: at the pre-fix bound/retry = 10/2
    the steady-state concurrent copies of ONE logical operation were 5 (measured
    at 1/50 scale: refusals=5, dispatches_started=5, peak_concurrent=5), and for
    a non-idempotent tool the abandoned original can still commit AFTER the
    caller was told to retry — duplicate side effects. A prose caveat cannot
    discharge this: retry middleware acts on status/code/header, not on the
    body. Pinned so a future edit cannot quietly re-open the window — do NOT
    restore retry = 2.
    """
    assert ma._TRANSPORT_WAIT_RETRY_AFTER_S >= ma._TRANSPORT_WAIT_BOUND_S, (
        f"advertised back-off {ma._TRANSPORT_WAIT_RETRY_AFTER_S!r}s is shorter "
        f"than the {ma._TRANSPORT_WAIT_BOUND_S!r}s bound — a compliant retry "
        "re-enters the abandoned operation while it still runs")
    from tortoise import mcp_server as ms
    assert ms._mcp_auth is ma  # the MCP seam reads the same one source


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
    msg = ma._TRANSPORT_WAIT_BOUND_MESSAGE
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
async def test_handler_timeout_error_is_not_rewritten_as_a_wait_breach(fast_bound):
    """A handler that ITSELF raises ``TimeoutError`` must surface to the caller,
    not be converted into a 504 refusal.

    ``asyncio.TimeoutError`` IS builtin ``TimeoutError`` (3.11+), so the
    ``wait_for`` timeout branch is indistinguishable from the awaited dispatch
    raising unless it checks that the task actually finished. Pinned because the
    ``wait_for`` + ``shield`` primitive made the two collide (``asyncio.wait``
    did not propagate the child's exception through the deadline branch).
    """
    async def app(scope, receive, send):
        raise TimeoutError("handler's own deadline")

    mw = ha.WaitBoundMiddleware(app)
    with pytest.raises(TimeoutError, match="handler's own deadline"):
        await _drive(mw, _scope())


@pytest.mark.asyncio
async def test_breach_is_a_504_with_retry_after_and_a_readable_body(fast_bound):
    mw = ha.WaitBoundMiddleware(_slow_app(5.0))
    rec = await _drive(mw, _scope())
    assert rec.status == 504
    assert rec.headers()["retry-after"] == str(ma._TRANSPORT_WAIT_RETRY_AFTER_S)
    # Reuses the §6.1 shape — NOT a parallel `{"error": {...}}` vocabulary.
    assert rec.json == {"detail": ma._TRANSPORT_WAIT_BOUND_MESSAGE}


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
    assert rec.headers()["retry-after"] == str(ma._TRANSPORT_WAIT_RETRY_AFTER_S)
    body = rec.json
    assert body["jsonrpc"] == "2.0"
    assert body["error"]["code"] == ERR_TIMEOUT
    assert body["error"]["data"]["retry_after"] == ma._TRANSPORT_WAIT_RETRY_AFTER_S
    assert body["error"]["message"] == ma._TRANSPORT_WAIT_BOUND_MESSAGE


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


@pytest.mark.asyncio
async def test_internal_cron_route_is_exempt(fast_bound):
    """#4939: `/v1/internal/` is a CLASS the #3834 ruling never reached, not a
    second instance of the `/v1/context` exemption. The ruling is about what a
    USER experiences; every route under this prefix is an operator/cron endpoint
    behind `_check_internal`, and its CRON callers publish their own patience
    (`registry-cron.sh` posts the sweep with `curl -m 600`; the operator runbook
    curls carry an explicit `--max-time` for the same reason). A user-patience
    bound bounds no user there — it only turns a 600 s batch job into a 10 s
    failure, which is how the DR sweep refused on every hourly run for 12 days
    while the archives aged.

    Driven through the REAL `/status` shape the DR driver depends on: if the
    sweep is refused, nothing backs up and the refusal is what the driver reads
    as `status=error`.
    """
    mw = ha.WaitBoundMiddleware(_slow_app(0.2, status=200))
    rec = await _drive(mw, _scope("/v1/internal/backups/sweep", method="POST"))
    assert rec.status == 200, "the DR sweep must reach its handler, not a 10 s refusal"


@pytest.mark.parametrize("path", [
    "/v1/internalfoo",      # a sibling that merely SHARES the letters
    "/v1/internal",         # the bare prefix: no trailing slash, and no such route
    "/v1/internalX/sweep",
    "/v2/internal/sweep",
])
@pytest.mark.asyncio
async def test_internal_exemption_prefix_is_boundary_exact(fast_bound, path):
    """The MCP arm's boundary test (`test_mcp_prefix_is_boundary_exact`),
    applied to this prefix: the exemption must not spread by spelling."""
    mw = ha.WaitBoundMiddleware(_slow_app(5.0))
    rec = await _drive(mw, _scope(path, method="POST"))
    assert rec.status == 504, f"{path} must stay bounded"


def test_the_two_readings_stay_two_constants():
    """The exact-match SET is 'one handler whose OWN record exempts it' and the
    PREFIX is 'a class the ruling never reached'. Folding the prefix into the set
    would silently make the set's exactness test — and the reasoning it pins —
    meaningless, so they are asserted separately."""
    expected_exact = frozenset({("POST", "/v1/context")})
    assert expected_exact == ha._TRANSPORT_WAIT_BOUND_EXEMPT
    assert ha._TRANSPORT_WAIT_BOUND_EXEMPT_PREFIX == "/v1/internal/"


def test_no_internal_route_declares_a_body_parameter():
    """#4939 security: exempting the prefix removed the only cap on an
    UNAUTHENTICATED body read. FastAPI parses a declared `body:` parameter in
    `get_request_handler` BEFORE any dependency or handler body runs, so the
    whole body was buffered before `_check_internal` could reject the caller —
    and the 10 s transport bound used to truncate that read. There is no
    body-size middleware on the hosted app, no Fly edge timeout, and
    `hard_limit = 200`, so a declared body parameter on an internal route means
    an unauthenticated caller can hold connections and grow memory unbounded.

    The five routes that used to take one now call `_read_internal_json_body`
    AFTER the key check. This test is the guard against reintroducing one.
    """
    offenders = []
    for route in ha.app.routes:
        path = getattr(route, "path", "")
        if not path.startswith(ha._TRANSPORT_WAIT_BOUND_EXEMPT_PREFIX):
            continue
        params = getattr(getattr(route, "dependant", None), "body_params", None) or []
        if params:
            offenders.append((path, [getattr(p, "name", "?") for p in params]))
    assert offenders == [], (
        "an internal route must read its body AFTER _check_internal via "
        f"_read_internal_json_body; found declared body params: {offenders}"
    )


async def _read_internal_body(headers, raw, *, required=False):
    """Drive `_read_internal_json_body` through a REAL Starlette `Request`."""
    pending = [{"type": "http.request", "body": raw, "more_body": False}]

    async def receive():
        return pending.pop(0) if pending else {
            "type": "http.request", "body": b"", "more_body": False}

    req = Request(_scope("/v1/internal/x", method="POST", headers=headers), receive)
    return await ha._read_internal_json_body(req, required=required)


@pytest.mark.asyncio
@pytest.mark.parametrize("headers,raw,required,expected", [
    # curl -d sends form-urlencoded unless told otherwise — the MIRROR-CHECK
    # shape that regressed. The body must parse anyway.
    ([("content-type", "application/x-www-form-urlencoded")],
     b'{"bucket": "mirror"}', False, {"bucket": "mirror"}),
    ([], b'{"bucket": "mirror"}', False, {"bucket": "mirror"}),
    ([("content-type", "application/json; charset=utf-8")], b'{"a": 1}', False, {"a": 1}),
    ([("content-type", "application/vnd.api+json")], b'{"a": 1}', False, {"a": 1}),
    # absent / empty body mirrors the signature it replaced
    ([("content-type", "application/json")], b"", False, {}),
    ([], b"   ", False, {}),
    ([("content-type", "application/json")], b"null", False, {}),
    # ... and the required signature's 422s
    ([], b"", True, 422),
    ([("content-type", "application/json")], b"null", True, 422),
    ([], b"{}", True, {}),
    # malformed / non-object stay loud, as FastAPI's were
    ([], b"{bad", False, 422),
    ([], b"[1,2]", False, 422),
    ([], b'"str"', False, 422),
    ([], b"grace_days=365", False, 422),
])
async def test_internal_body_parse_is_content_type_agnostic(headers, raw, required, expected):
    """#4939 regression guard. The FIRST version of the helper gated the read on
    `content-type.startswith("application/json")`, which is STRICTER than what
    FastAPI accepted. `curl -d '{"bucket": "<mirror>"}'` without an explicit
    Content-Type header was therefore read as an ABSENT body, and the runbook's
    MIRROR check silently verified the PRIMARY bucket and returned 200 — a loud
    422 turned into a confident false pass on a disaster-recovery step. The same
    gate silently discarded the purge's documented `grace_days` override.

    So: parse whatever body is present, and mirror ONLY the absent-body and
    null-body behaviour of the two signatures this helper replaced.
    """
    if expected == 422:
        with pytest.raises(StarletteHTTPException) as exc:
            await _read_internal_body(headers, raw, required=required)
        assert exc.value.status_code == 422
    else:
        assert await _read_internal_body(headers, raw, required=required) == expected


@pytest.mark.asyncio
async def test_internal_body_route_authenticates_before_reading_the_body():
    """The ORDERING the exemption made load-bearing, asserted by BEHAVIOUR
    rather than by reading the source. `receive` never delivers a complete
    body, so a route that parsed a `body:` parameter first (FastAPI's default,
    and what the five internal body routes used to do) would await forever and
    this test would time out. Authenticating first answers immediately and the
    capped read — which sits after `_check_internal` — is never reached.
    """
    pulls = []

    async def _never_completes():
        pulls.append(1)
        await asyncio.Event().wait()  # a client that never finishes its body

    for path in ("/v1/internal/backups/drill",
                 "/v1/internal/driver/heartbeat",
                 "/v1/internal/backups/re-baseline"):
        rec = _Recorder()
        scope = _scope(path, method="POST",
                       headers=[("content-type", "application/json")])
        await asyncio.wait_for(ha.app(scope, _never_completes, rec), timeout=10.0)
        # 503 when no internal key is configured (fail closed), 401 on a
        # mismatch — never a 422/400 from body parsing, which would prove the
        # body was read before the key check.
        assert rec.status in (401, 503), (
            f"{path} answered {rec.status}; an unauthenticated caller must be "
            "rejected WITHOUT its body being read"
        )


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
    monkeypatch.setattr(ma, "_TRANSPORT_WAIT_BOUND_S", 30.0)
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
    monkeypatch.setattr(ma, "_TRANSPORT_WAIT_BOUND_S", 30.0)
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
    monkeypatch.setattr(ma, "_TRANSPORT_WAIT_BOUND_S", 0.05)

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
    monkeypatch.setattr(ma, "_TRANSPORT_WAIT_BOUND_S", 0.05)

    async def _slow_route(request):
        await asyncio.sleep(0.3)
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/slow", _slow_route)])
    app.add_middleware(ha.WaitBoundMiddleware)
    with TestClient(app) as client:
        r = client.get("/slow", headers={"Origin": "https://app.premiselabs.co"})
    assert r.status_code == 504
    assert r.headers["retry-after"] == str(ma._TRANSPORT_WAIT_RETRY_AFTER_S)
    assert r.headers["access-control-allow-origin"] == "https://app.premiselabs.co"
    assert r.json() == {"detail": ma._TRANSPORT_WAIT_BOUND_MESSAGE}


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
    assert payload["message"] == ma._TRANSPORT_WAIT_BOUND_MESSAGE
    assert payload["data"]["retry_after"] == ma._TRANSPORT_WAIT_RETRY_AFTER_S
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
    assert err["message"] == ma._TRANSPORT_WAIT_BOUND_MESSAGE
    assert err["data"]["retry_after"] == ma._TRANSPORT_WAIT_RETRY_AFTER_S


def test_mcp_seam_spends_the_transport_deadline_not_a_fresh_one(
        mcp_slow_tool, monkeypatch):
    """#3834 F1: ONE deadline, not two.

    The middleware's deadline is abandoned the moment ``http.response.start``
    is on the wire (the SSE refusal can no longer be substituted), and the MCP
    seam used to start its OWN fresh bound at that point — so every pre-SSE cost
    (org resolution, rate limit, routing) went uncounted and the caller-visible
    wait was the SUM. Measured with the real layering (pre-SSE 0.2 s, bound
    0.3 s, tool 0.3 s) the total was 0.522 s for an advertised 0.3 s; a 6 s org
    resolution plus an 11 s tool would exceed the 15 s client budget with NO
    legible refusal — the exact failure this unit exists to eliminate.

    This mounts the REAL parent app (``WaitBoundMiddleware`` included) over the
    MCP sub-app and injects a 0.2 s pre-SSE cost, then asserts the caller only
    waits the bound (+ε). The sibling SSE test mounts the MCP app with NO parent
    middleware, which is precisely why it could not see this.
    """
    from contextlib import asynccontextmanager

    from starlette.applications import Starlette
    from starlette.routing import Mount
    from starlette.testclient import TestClient

    from tortoise import mcp_server as ms

    bound = 0.3
    monkeypatch.setattr(ma, "_TRANSPORT_WAIT_BOUND_S", bound)
    monkeypatch.setattr(ha, "_track_analytics_event", lambda *a, **k: None)

    mcp_app = ms.create_http_app(auth_mode="none")

    @asynccontextmanager
    async def _lifespan(parent_app):
        async with mcp_app.lifespan(mcp_app):
            yield

    class _PreSseCost:
        """A realistic pre-SSE cost (org resolution / rate limit / routing)."""

        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] == "http":
                await asyncio.sleep(0.2)
            await self.app(scope, receive, send)

    parent = Starlette(lifespan=_lifespan, routes=[Mount("/mcp", app=mcp_app)])
    parent.add_middleware(_PreSseCost)
    # Registered LAST → OUTERMOST, so it wraps _PreSseCost (add_middleware
    # inserts at index 0).
    parent.add_middleware(ha.WaitBoundMiddleware)
    try:
        with TestClient(parent) as client:
            t0 = time.perf_counter()
            # `/mcp/` (trailing slash) on purpose: posting `/mcp` makes Starlette
            # answer 307 → `/mcp/`, and the followed redirect would run this
            # pre-SSE cost TWICE — measuring the redirect, not the deadline.
            r = client.post("/mcp/", headers=_MCP_HEADERS, json={
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "_bound_slow", "arguments": {}}})
            elapsed = time.perf_counter() - t0
    finally:
        ms._pending_mcp_wait_bound.clear()

    assert r.status_code == 200, r.text
    body = _parse_sse(r.text)
    assert body is not None, r.text
    assert body["result"]["isError"] is True, (
        "the tool returned success — the seam did not spend the transport's "
        "remaining deadline (or the pre-SSE middleware did not run)")
    assert elapsed <= bound + 0.15, (
        f"caller-visible wait {elapsed:.3f}s exceeded the {bound}s bound+ε — "
        "pre-SSE cost is being added to a FRESH MCP deadline (two deadlines)")


def test_mcp_breach_reports_the_caller_visible_interval(
        mcp_slow_tool, monkeypatch):
    """#3834 F-1: the MCP arm's breach event must report the SAME interval the
    REST arm reports — the CALLER-VISIBLE wait — not this seam's own entry.

    The seam spends only the transport's REMAINING deadline, so a ``t0``-based
    interval under-reports by the entire pre-SSE cost (org resolution, rate
    limit, routing). Measured on the pre-fix head with the real layering at
    bound 0.3 s and pre-SSE 0.2 s: the caller waited ~0.3 s while the event said
    ~0.09 s. A consumer thresholding ``latency_ms >= 10000`` counted ZERO MCP
    breaches on the very surface the bound was justified by.
    """
    from contextlib import asynccontextmanager

    from starlette.applications import Starlette
    from starlette.routing import Mount
    from starlette.testclient import TestClient

    from tortoise import mcp_server as ms

    bound = 0.3
    monkeypatch.setattr(ma, "_TRANSPORT_WAIT_BOUND_S", bound)
    seen: list = []
    monkeypatch.setattr(ha, "_track_analytics_event",
                        lambda org, ev, props: seen.append((ev, props)))

    mcp_app = ms.create_http_app(auth_mode="none")

    @asynccontextmanager
    async def _lifespan(parent_app):
        async with mcp_app.lifespan(mcp_app):
            yield

    class _PreSseCost:
        """A realistic pre-SSE cost (org resolution / rate limit / routing)."""

        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] == "http":
                await asyncio.sleep(0.2)
            await self.app(scope, receive, send)

    parent = Starlette(lifespan=_lifespan, routes=[Mount("/mcp", app=mcp_app)])
    parent.add_middleware(_PreSseCost)
    parent.add_middleware(ha.WaitBoundMiddleware)
    try:
        with TestClient(parent) as client:
            t0 = time.perf_counter()
            r = client.post("/mcp/", headers=_MCP_HEADERS, json={
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "_bound_slow", "arguments": {}}})
            elapsed = time.perf_counter() - t0
        for _ in range(250):
            if seen:
                break
            time.sleep(0.02)
    finally:
        ms._pending_mcp_wait_bound.clear()

    assert r.status_code == 200, r.text
    breach = [p for e, p in seen if e == ha._TRANSPORT_WAIT_BOUND_EVENT]
    assert len(breach) == 1, breach
    emitted = breach[0]["latency_ms"]
    # The seam-local interval is ~remaining == bound - pre-SSE (~0.1 s); the
    # caller-visible one is ~bound. A regression back to the seam's ``t0``
    # lands well under the 0.75 * bound floor.
    assert emitted >= 0.75 * bound * 1000, (
        f"the MCP breach reported {emitted} ms for a caller-visible wait of "
        f"{elapsed * 1000:.0f} ms — that is the seam-local interval, not the "
        "interval the bound governs")
    assert abs(emitted - elapsed * 1000) < 150, (
        f"emitted {emitted} ms vs caller-visible {elapsed * 1000:.0f} ms — the "
        "two arms are measuring different intervals")


def test_mcp_breach_is_recorded_exactly_once_on_the_composed_path(
        mcp_slow_tool, monkeypatch):
    """#3834 F-2: ONE logical breach must produce ONE
    ``transport_wait_bound_exceeded`` — and no false ``status=timeout``.

    When the pre-SSE cost ALONE exceeds the bound the middleware refuses (its
    SSE response never starts) and emits; the ABANDONED MCP app then reaches the
    dispatch, where the transport's remaining deadline has collapsed to 0, so
    the seam used to emit a SECOND event plus an ``mcp_tool_call{status:
    "timeout"}`` for a dispatch it never waited on. Reproduced on the pre-fix
    head: one ``tools/call`` → two breach rows + one false timeout row.

    The seam reads the middleware's ``scope["state"]["_wait_bound_refused"]``
    flag, so this test also proves the shared dict crosses Starlette's ``Mount``
    boundary (the F1 test proves the same for ``_wait_bound_t0``). The portal is
    kept alive after the 504 so the abandoned inner task can reach the seam, as
    it would on a real server.
    """
    from contextlib import asynccontextmanager

    from starlette.applications import Starlette
    from starlette.routing import Mount
    from starlette.testclient import TestClient

    from tortoise import mcp_server as ms

    bound = 0.3
    monkeypatch.setattr(ma, "_TRANSPORT_WAIT_BOUND_S", bound)
    seen: list = []
    monkeypatch.setattr(ha, "_track_analytics_event",
                        lambda org, ev, props: seen.append((ev, props)))

    mcp_app = ms.create_http_app(auth_mode="none")

    @asynccontextmanager
    async def _lifespan(parent_app):
        async with mcp_app.lifespan(mcp_app):
            yield

    class _PreSseCost:
        """Pre-SSE cost alone exceeds the bound → the middleware refuses."""

        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] == "http":
                await asyncio.sleep(0.4)
            await self.app(scope, receive, send)

    parent = Starlette(lifespan=_lifespan, routes=[Mount("/mcp", app=mcp_app)])
    parent.add_middleware(_PreSseCost)
    parent.add_middleware(ha.WaitBoundMiddleware)
    try:
        with TestClient(parent) as client:
            r = client.post("/mcp/", headers=_MCP_HEADERS, json={
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "_bound_slow", "arguments": {}}})
            # Keep the portal (and the request loop) alive: the middleware has
            # already answered 504, but the abandoned dispatch keeps running.
            time.sleep(1.5)
        for _ in range(250):
            if seen:
                break
            time.sleep(0.02)
        time.sleep(0.3)
    finally:
        ms._pending_mcp_wait_bound.clear()

    assert r.status_code == 504, r.text
    breach = [p for e, p in seen if e == ha._TRANSPORT_WAIT_BOUND_EVENT]
    assert len(breach) == 1, (
        f"one request recorded {len(breach)} breach events — the seam emitted "
        f"a second one for a refusal the transport already delivered: {breach}")
    # The surviving event is the MIDDLEWARE's (it answered the caller): no
    # ``tool_name``, and the path is the transport's, not the seam's hardcoded
    # ``/mcp`` literal.
    assert "tool_name" not in breach[0], breach
    assert breach[0]["path"] == "/mcp/", breach
    timeouts = [p for e, p in seen
                if e == "mcp_tool_call" and p.get("status") == "timeout"]
    assert timeouts == [], (
        "a dispatch the transport had already refused was recorded as a "
        f"timeout: {timeouts}")


@pytest.mark.asyncio
async def test_mcp_wait_bound_fast_path_overhead_is_bounded(monkeypatch):
    """#3834 F2 guard: the seam wraps EVERY tool call, so its fast-path cost
    must stay small.

    WHAT THIS COVERS: the seam's TYPICAL (median) incremental dispatch cost,
    sampled INTERLEAVED against the unwrapped ``_original_call_tool`` on the
    same tool, so host-load drift lands on both arms instead of masquerading as
    seam cost. The cost is the per-call ``ensure_future`` task indirection —
    ABANDON-don't-cancel requires owning the task — measured at p50 ≈ +0.48 ms.
    It is NOT a guard on the waiter: ``wait_for(shield)`` and ``asyncio.wait``
    are cost-neutral (198 µs vs 199 µs p50, same 53.8 µs floor).

    WHAT THIS DOES **NOT** COVER: the CI gate
    ``tests/test_mcp_telemetry.py::TestOverhead::test_p95_under_5ms`` asserts an
    ABSOLUTE p95 on a quiet CI host. This guard is a DELTA on a possibly-loaded
    host — a different statistic against a different baseline — and it neither
    re-derives nor substitutes for that absolute budget. At load ~150 on this
    host the absolute p95 is 20–32 ms even on unmodified ``origin/main`` (the raw
    UNWRAPPED baseline alone 7.6–16.5 ms), so the absolute budget is not
    reproducible here at all. Median, not mean and not p95: a single scheduler
    spike must not decide the verdict on a shared box.
    """
    import statistics

    from fastmcp.tools import FunctionTool

    from tortoise import mcp_server as ms

    def _bound_fast() -> dict:
        return {"ok": True}

    ms.mcp.add_tool(FunctionTool.from_function(
        _bound_fast, name="_bound_fast", description="wait-bound test: fast"))
    monkeypatch.setattr(ha, "_track_analytics_event", lambda *a, **k: None)
    # No-op the telemetry emitter so the delta is the SEAM's cost alone —
    # ``_wrapped_call_tool`` emits one event per call and that is not the seam.
    monkeypatch.setattr(ms, "_emit_mcp_tool_call_telemetry",
                        lambda *a, **k: None)
    try:
        # Interleaved, so load drift hits BOTH arms rather than the second only.
        wrapped: list[float] = []
        raw: list[float] = []
        for _ in range(80):
            t = time.perf_counter()
            await ms.mcp.call_tool("_bound_fast")
            wrapped.append((time.perf_counter() - t) * 1000.0)
            t = time.perf_counter()
            await ms._original_call_tool("_bound_fast")
            raw.append((time.perf_counter() - t) * 1000.0)
        wrapped.sort()
        raw.sort()
        w_med, r_med = statistics.median(wrapped), statistics.median(raw)
        w_p95 = wrapped[int(len(wrapped) * 0.95) - 1]
        r_p95 = raw[int(len(raw) * 0.95) - 1]
        delta = w_med - r_med
        # 1.5 ms bounds the seam's TYPICAL cost (measured p50 ≈ +0.48 ms); the
        # absolute p95 gate above is a different quantity and is deliberately
        # not asserted here (see the docstring). The p95 delta is REPORTED in
        # the failure message for diagnosis, never asserted: on a shared box it
        # is load-dominated (measured +1.0 to +7.3 ms across two runs at load
        # ~150) and would flake.
        assert delta < 1.5, (
            f"the wait-bound seam added {delta:.3f} ms per fast call (median "
            f"wrapped {w_med:.3f} ms, raw {r_med:.3f} ms; p95 delta "
            f"{w_p95 - r_p95:+.3f} ms) — the seam's typical cost grew from the "
            "measured p50 ≈ +0.48 ms (the per-call ``ensure_future`` owns the "
            "task ABANDON-don't-cancel needs)")
    finally:
        try:  # noqa: SIM105
            ms.mcp.local_provider.remove_tool("_bound_fast")
        except Exception:
            pass


@pytest.mark.asyncio
async def test_mcp_breach_emit_imports_hosted_api_off_the_event_loop(
        fast_bound, mcp_slow_tool):
    """#3834 G1: the breach path must not import ``hosted_api`` on the loop.

    ``hosted_api`` imports ``mcp_server`` at module scope, so the MCP seam can
    only reach the shared breach writer through a lazy import — and that import
    builds the whole hosted FastAPI app. DONE ON THE LOOP (as it was), it froze
    the loop and delivered the refusal seconds after the bound: in a fresh
    process against a 0.05 s bound the refusal returned after ~2–4 s with an
    event-loop heartbeat gap of the same span.

    This pin fails on the PROPERTY, not on a proxy. ``hosted_api`` is removed
    from ``sys.modules`` AND from the ``tortoise`` package attribute, so
    ``from tortoise import hosted_api`` CANNOT resolve as a dict lookup, and a
    ``sys.meta_path`` finder records the thread whenever the name is ACTUALLY
    imported (``exec_module``), supplying a stub module that carries only the
    writer. A regression that re-imports on the loop executes the finder on the
    MAIN thread first and this assertion goes red. An earlier version inserted
    the fake into ``sys.modules``, which turned the on-loop import into a dict
    lookup and made the pin unable to fail on the property it names (#3834
    re-review H1).
    """
    import contextlib
    import importlib.abc
    import importlib.util
    import sys
    import threading
    import types

    import tortoise
    from tortoise import mcp_server as ms

    recorded: list = []

    def _record(org_id, route_path, method, latency_ms, *, tool_name=None):
        pass

    class _StubLoader(importlib.abc.Loader):
        def create_module(self, spec):
            return types.ModuleType(spec.name)

        def exec_module(self, module):
            # Recorded at the moment the REAL import would have run, so the
            # thread here is the thread that pays the import cost.
            recorded.append(threading.current_thread())
            module._emit_wait_bound_breach = _record

    class _Finder(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname != "tortoise.hosted_api":
                return None
            return importlib.util.spec_from_loader(fullname, _StubLoader())

    saved_mod = sys.modules.pop("tortoise.hosted_api", None)
    had_attr = hasattr(tortoise, "hosted_api")
    saved_attr = getattr(tortoise, "hosted_api", None)
    if had_attr:
        delattr(tortoise, "hosted_api")
    finder = _Finder()
    sys.meta_path.insert(0, finder)
    try:
        result = await ms.mcp.call_tool("_bound_slow")
        assert result.is_error is True
        for _ in range(200):
            if recorded:
                break
            await asyncio.sleep(0.02)
    finally:
        with contextlib.suppress(ValueError):
            sys.meta_path.remove(finder)
        sys.modules.pop("tortoise.hosted_api", None)
        if saved_mod is not None:
            sys.modules["tortoise.hosted_api"] = saved_mod
        if had_attr:
            tortoise.hosted_api = saved_attr
        elif hasattr(tortoise, "hosted_api"):
            delattr(tortoise, "hosted_api")
    assert recorded, (
        "hosted_api was never actually imported — the shared writer was not "
        "reached, so this pin measured nothing")
    assert recorded[0] is not threading.main_thread(), (
        "hosted_api's import ran ON the event loop (thread "
        f"{recorded[0]!r}) — that is the G1 freeze, which delivers the "
        "refusal seconds late")


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
    monkeypatch.setattr(ma, "_TRANSPORT_WAIT_BOUND_S", 30.0)
    monkeypatch.setattr(ha, "_track_analytics_event",
                        lambda *a, **k: None)
    events: list = []
    monkeypatch.setattr(
        ms, "_emit_mcp_tool_call_telemetry",
        lambda org, name, status, latency, kind: events.append((status, kind)))
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
        # A cancellation must not inflate the "ok" count of the mcp_tool_call
        # series this bound is measured from (CancelledError bypasses
        # `except Exception`, so without the dedicated branch it read "ok").
        assert ("cancelled", "caller_cancelled") in events, (
            f"a cancelled call was not classified: {events!r}")
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
    monkeypatch.setattr(ma, "_TRANSPORT_WAIT_BOUND_S", 30.0)
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
async def test_mcp_tool_name_is_sanitized_at_the_log_and_the_analytics_sink(
        fast_bound, monkeypatch, caplog):
    """#3834 F-3: the MCP tool name is the client-supplied JSON-RPC
    ``params.name`` and reaches the seam without a registry-membership guarantee
    on the UNscoped path (a scoped key's name is resolved and checked by
    ``_enforce_mcp_tool_scope`` first), so it is untrusted for the same reason the
    REST arm sanitizes its route path. A name
    carrying CR/LF, an ANSI escape, NUL, U+2028/U+2029 and the C1 introducers
    must appear ESCAPED in BOTH the log record and the analytics props.

    ``_original_call_tool`` is replaced so a crafted name reliably reaches the
    breach path (an unregistered name would otherwise fail resolution before the
    bound). The name is the only thing under test.
    """
    import logging

    from tortoise import mcp_server as ms

    raw = "evil\r\nFORGED\x1b[31m\x00\u2028\u2029\u0085NEL\x9bCSI\x7f"
    escaped = ("evil\\r\\nFORGED\\x1b[31m\\x00\\u2028\\u2029\\x85NEL"
               "\\x9bCSI\\x7f")
    seen: list = []
    monkeypatch.setattr(ha, "_track_analytics_event",
                        lambda org, ev, props: seen.append((ev, props)))

    async def _slow_dispatch(name, arguments=None, **kwargs):
        await asyncio.sleep(5.0)

    monkeypatch.setattr(ms, "_original_call_tool", _slow_dispatch)
    try:
        with caplog.at_level(logging.WARNING, logger=ms._log.name):
            result = await ms.mcp.call_tool(raw)
        assert result.meta == {ms._WAIT_BOUND_META_KEY: True}
        for _ in range(250):
            events = {e for e, _ in seen}
            if ha._TRANSPORT_WAIT_BOUND_EVENT in events and "mcp_tool_call" in events:
                break
            await asyncio.sleep(0.02)
    finally:
        ms._pending_mcp_wait_bound.clear()

    # ── the log sink ───────────────────────────────────────────────────
    warnings = [r.getMessage() for r in caplog.records
                if r.levelno == logging.WARNING
                and "MCP tools/call" in r.getMessage()]
    assert warnings, "the MCP breach warning never fired"
    assert escaped in warnings[0], (
        f"the tool name was not escaped in the log line: {warnings[0]!r}")
    for ch in ("\r", "\n", "\x1b", "\x00", "\u2028", "\u2029",
               "\u0085", "\u009b", "\x7f"):
        assert ch not in warnings[0], (
            f"raw control char {ch!r} reached the log line: {warnings[0]!r}")

    # ── both analytics sinks ───────────────────────────────────────────
    by_event: dict = {}
    for ev, props in seen:
        by_event.setdefault(ev, []).append(props)
    assert ha._TRANSPORT_WAIT_BOUND_EVENT in by_event, seen
    breach_name = by_event[ha._TRANSPORT_WAIT_BOUND_EVENT][0]["tool_name"]
    assert breach_name == escaped, (
        f"the breach prop carried the raw name: {breach_name!r}")
    assert "mcp_tool_call" in by_event, seen
    call_name = by_event["mcp_tool_call"][0]["tool_name"]
    assert call_name == escaped, (
        f"the mcp_tool_call prop carried the raw name: {call_name!r}")


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
