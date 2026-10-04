"""#4387 item 2 — the suite's egress guard (tests/conftest.py).

Pins BOTH directions of the guard installed at configure time by
``tests/conftest.py::_install_hermetic_egress_guard`` (called from
``pytest_configure``):

  * a real (non-loopback) egress attempt is blocked at the transport, and the
    analytics POST / JWKS GET are answered by the stub and recorded;
  * loopback and the in-process TestClient transport are NOT blocked.

The guard is process-wide test-suite infrastructure, so these tests exercise it
exactly as the SUPABASE_URL-setting files do — through the real product call
sites (``hosted_api._track_analytics_event``, ``session_auth._fetch_jwks``).
"""
from __future__ import annotations

import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest

import tortoise.hosted_api as ha

# #4387 review: the guard must already be in place when this module is IMPORTED
# (collection), not merely before its first test runs. A session-scoped fixture
# installs too late for collection-time egress — including the `--collect-only`
# pass that emits the skip-guard manifest — so this is asserted at import time.
# A regression to fixture-time installation fails the suite during collection
# instead of passing quietly.
assert httpx.HTTPTransport.handle_request.__qualname__ == "_hermetic_handle_request", (
    "the #4387 egress guard is not installed at import/collection time"
)
assert httpx.AsyncHTTPTransport.handle_async_request.__qualname__ == (
    "_hermetic_handle_async_request"
), "the #4387 async egress guard is not installed at import/collection time"

_BLOCKED_HOST = "https://hermetic-guard.invalid"


def test_analytics_post_is_served_by_the_stub_and_recorded(
        hermetic_egress, monkeypatch, tmp_path):
    """The sink's real request-building runs; only the wire is stubbed."""
    monkeypatch.setenv("SUPABASE_URL", _BLOCKED_HOST)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc-role-key")
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    fallback = tmp_path / "analytics_fallback.jsonl"
    monkeypatch.setattr(ha, "_ANALYTICS_FALLBACK_PATH", str(fallback))

    outcome = ha._track_analytics_event("org-1", "hermetic_probe",
                                        {"harness": "pi"})

    assert outcome == "supabase", outcome
    assert not fallback.exists(), "the stubbed POST must not fall back to JSONL"
    # Scoped to the host THIS test configured, not a raw list length (#4387
    # review): the recorder is a process global, and a straggling off-loop
    # analytics emit from a preceding test (#4608) would otherwise be counted
    # here as a second post to this test's endpoint.
    posts = [c for c in hermetic_egress.analytics_posts
             if c.host == "hermetic-guard.invalid"]
    assert len(posts) == 1, hermetic_egress.calls
    call = posts[0]
    assert call.method == "POST"
    assert call.host == "hermetic-guard.invalid"
    assert call.path == "/rest/v1/analytics_events"
    # The request the PRODUCT built is observable in full: URL, credential,
    # and the JSON body (the assertion the transport-level stub must not
    # weaken — test_analytics_write_path_resolution.py/:fallback_alert.py pin
    # the same shape with their own client stubs).
    assert call.request.headers["apikey"] == "svc-role-key"
    body = json.loads(call.request.content)
    assert body["org_id"] == "org-1"
    assert body["event_name"] == "hermetic_probe"
    assert body["properties"] == {"harness": "pi"}


def test_jwks_fetch_is_answered_off_the_network(hermetic_egress, monkeypatch):
    """The pre-warm's fetch must be answered here, never by DNS/NXDOMAIN."""
    import tortoise.session_auth as sa

    monkeypatch.setattr(
        sa, "_JWKS_URL", f"{_BLOCKED_HOST}/auth/v1/.well-known/jwks.json")
    # The real _fetch_jwks must reach the guard's canned 503 (an HTTP error),
    # not a ConnectError from a real DNS/connect attempt.
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(sa._fetch_jwks())

    gets = [c for c in hermetic_egress.jwks_gets
            if c.host == "hermetic-guard.invalid"]
    assert len(gets) == 1, hermetic_egress.calls
    assert gets[0].method == "GET"
    assert gets[0].path == "/auth/v1/.well-known/jwks.json"
    assert gets[0].host == "hermetic-guard.invalid"


def test_non_test_egress_is_blocked(hermetic_egress):
    """A non-loopback, non-stubbed host fails loudly and never leaves."""
    with pytest.raises(httpx.ConnectError) as ei:
        httpx.Client(timeout=2, trust_env=False).get(
            "https://api.resend.com/emails")
    assert "#4387" in str(ei.value)
    assert "api.resend.com" in str(ei.value)
    # Scoped to the host this test used, and by MEMBERSHIP not a whole-list
    # equality (#4387 review): the recorder is a process global, so a
    # straggling call from a preceding test (#4608) can append to any kind —
    # including this one. Nothing here can attribute a call to its originator.
    blocked = [c.host for c in hermetic_egress.blocked
               if c.host == "api.resend.com"]
    assert len(blocked) == 1, hermetic_egress.calls


def test_loopback_egress_is_not_blocked():
    """The guard must not blanket-block loopback (local test servers)."""
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(timeout=5, trust_env=False) as client:
            resp = client.get(f"http://127.0.0.1:{server.server_port}/probe")
        assert resp.status_code == 200
        assert resp.text == "ok"
    finally:
        server.shutdown()
        server.server_close()


def test_lookalike_loopback_hostname_is_blocked_not_delegated(hermetic_egress):
    """#4387 review: a STRING prefix is not the 127.0.0.0/8 range.

    `host.startswith("127.")` classified `127.evil.example` as loopback and
    DELEGATED it to the real transport — real DNS, real egress — which is the
    exact silent-egress class this guard exists to stop. The host sits under
    the RFC 6761 reserved `.example` TLD, so it can never resolve: if the
    guard fails open again this test fails on the assertion below, not on a
    live request escaping the suite.
    """
    host = "127.evil.example"
    with pytest.raises(httpx.ConnectError) as ei:
        httpx.Client(timeout=2, trust_env=False).get(f"https://{host}/probe")
    assert "#4387" in str(ei.value), str(ei.value)
    assert [c.host for c in hermetic_egress.blocked if c.host == host] == [host]
    assert host not in [c.host for c in hermetic_egress.loopback]


def test_localhost_suffix_lookalike_is_blocked_not_delegated(hermetic_egress):
    """#4387 review: a STRING suffix is not a loopback classification either.

    `host.endswith(".localhost")` delegated every `*.localhost` name to the real
    transport, so whether the request left the process depended on the OS
    resolver — the accident-of-DNS this guard exists to remove. RFC 6761 does
    reserve the whole `.localhost` namespace for loopback, but that is a
    requirement on resolvers, not something the guard can rely on. Blocked
    fail-closed; `evil.localhost` resolves to 127.0.0.1/::1 on a conforming
    resolver, so if the guard fails open again this test fails on the assertion
    below (nothing listening on :443) rather than on live egress.
    """
    host = "evil.localhost"
    with pytest.raises(httpx.ConnectError) as ei:
        httpx.Client(timeout=2, trust_env=False).get(f"https://{host}/probe")
    assert "#4387" in str(ei.value), str(ei.value)
    assert [c.host for c in hermetic_egress.blocked if c.host == host] == [host]
    assert host not in [c.host for c in hermetic_egress.loopback]


def test_testclient_transport_is_not_blocked():
    """starlette's TestClient uses its own transport — never the guard."""
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    app = FastAPI()

    @app.get("/ping")
    def ping():
        return {"ok": True}

    with TestClient(app) as client:
        resp = client.get("/ping")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_guard_install_is_reference_counted_across_nested_sessions(
        hermetic_egress):
    """A nested in-process pytest session must not disarm the OUTER one.

    `pytest_configure`/`pytest_unconfigure` are balanced by DEPTH, not by a
    boolean. With a boolean, an inner session's unconfigure (pytester, or a test
    that calls `pytest.main()`) restored the pristine transports and left every
    remaining test in the OUTER session unguarded — silently, because the guard
    is what supplies the blocking.

    The owning module is resolved from the recorder's own class instead of
    `import conftest`: there are four `conftest.py` files under tests/ and only
    one of them is this one.
    """
    conftest = sys.modules[type(hermetic_egress).__module__]
    depth = conftest._HERMETIC_GUARD_DEPTH
    assert depth >= 1, "the guard is not installed in this session"

    conftest._install_hermetic_egress_guard()   # an inner session's configure
    conftest.pytest_unconfigure(None)           # ... and its unconfigure

    assert depth == conftest._HERMETIC_GUARD_DEPTH, (
        "a nested session's unconfigure disarmed the outer session's guard")
    assert httpx.HTTPTransport.handle_request.__qualname__ == (
        "_hermetic_handle_request")
    assert httpx.AsyncHTTPTransport.handle_async_request.__qualname__ == (
        "_hermetic_handle_async_request")
