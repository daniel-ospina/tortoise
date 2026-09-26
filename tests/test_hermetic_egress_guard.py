"""#4387 item 2 — the suite's egress guard (tests/conftest.py).

Pins BOTH directions of the guard installed by
``tests/conftest.py::_hermetic_egress_guard``:

  * a real (non-loopback) egress attempt is blocked at the transport, and the
    analytics POST / JWKS GET are answered by the stub and recorded;
  * loopback and the in-process TestClient transport are NOT blocked.

The guard is a test-suite fixture, so these tests exercise it exactly as the
~45 SUPABASE_URL-setting files do — through the real product call sites
(``hosted_api._track_analytics_event``, ``session_auth._fetch_jwks``).
"""
from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest

import tortoise.hosted_api as ha

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
    posts = hermetic_egress.analytics_posts
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

    gets = hermetic_egress.jwks_gets
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
    assert [c.host for c in hermetic_egress.blocked] == ["api.resend.com"]


def test_loopback_egress_is_not_blocked():
    """The guard must not blanket-block loopback (local test servers)."""
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
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
