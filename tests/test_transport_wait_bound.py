"""#3834 — the transport-level wait bound: ONE bound, a legible refusal, and the
retry signal shipped with it.

Owner ruling 2026-09-20 (issue #3834, comment 5752962331) re-homed the cold/busy
question from the removed ask route onto the TRANSPORT. This file pins the two
properties that ruling actually asks for:

* **one bound, at the transport** — not per-route, so it covers the mounted MCP
  app (and therefore the tools) by construction, with no per-tool edit;
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
    assert ha._TRANSPORT_WAIT_BOUND_EXEMPT == frozenset({("POST", "/v1/context")})
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
    """The mounted MCP app is bounded too — that is what makes the bound apply
    to the tools with no per-tool edit. Its refusal is #3851's JSON-RPC shape,
    because the MCP surface has no HTTP body of its own to hang words on."""
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
    dispatched exactly as `tortoise/mcp_server.py:208-224` does."""
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
