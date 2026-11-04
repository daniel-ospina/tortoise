"""#308: Cloudflare Turnstile CAPTCHA on the signup endpoints.

POST /v1/signup/email and POST /v1/register verify a Turnstile widget
token server-side against
https://challenges.cloudflare.com/turnstile/v0/siteverify before any
account/team is created.

Covered here:
- Fail-open: TURNSTILE_SECRET_KEY unset → signup proceeds without a token
  and a warning is logged (dev/selfhost mode — the endpoint must keep
  working exactly as before).
- Secret set + token missing → 400.
- Secret set + siteverify success=false → 400.
- Secret set + siteverify success=true → signup proceeds; the siteverify
  call carries {secret, response, remoteip} form data.
- siteverify transport error → 400 (fail-closed: a CAPTCHA outage must not
  silently disable abuse protection).
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
import pytest

import tortoise.hosted_api as ha_mod
from tests._verdict import verdict_client
from tortoise.hosted_api import app

_SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
_SUPABASE_URL = "https://testref.supabase.co"
_SERVICE_KEY = "test-service-role-key-123"


@pytest.fixture(scope="module")
def client():
    """The #5049 client seam (#8582): a wait-bound refusal is INCONCLUSIVE, not FAIL.

    `/v1/register` provisions a real registry graph, so its FIRST request in a
    shard pays an embedded-``redislite`` start inside the handler. That start is
    synchronous and on the event loop, so when the substrate is slow the
    product's own 10 s `WaitBoundMiddleware` cannot even fire until it returns —
    measured on `test (d)` of run 41747137960, where this file's first
    `/v1/register` call took **60.41 s** and answered
    `{"detail": mcp_auth._TRANSPORT_WAIT_BOUND_MESSAGE}` (the session fixture
    widens redislite's start bound to 60 s, `tests/_embedded.py`), redding
    `TestRegisterCaptcha::test_fail_open_without_secret_key` on `assert 504 ==
    200` in a file no part of this PR touches.

    A 504 refusal is the product saying "no answer yet", not a CAPTCHA verdict:
    the property under test was never observed, so the verdict is the
    contract's INCONCLUSIVE skip (#5049 rule 1) that NAMES the bound. Every real
    answer — including the 400s this class pins — still reaches its own
    assertion, so no genuine regression is masked. Plain `TestClient` here
    re-opens #8582 for this file, which
    `test_verdict_contract.py::test_every_converted_client_seam_builds_a_verdict_client`
    pins.
    """
    with verdict_client(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Start every test from a Turnstile- and Supabase-unconfigured baseline.

    Also resets the shared /v1/register IP bucket and the once-per-process
    fail-open warning flag so tests never poison each other (or other
    modules) with leaked env vars or log state.
    """
    monkeypatch.delenv("TURNSTILE_SECRET_KEY", raising=False)
    monkeypatch.delenv("TURNSTILE_SITE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    monkeypatch.setenv("RATE_LIMIT_DISABLED", "1")
    ha_mod._register_buckets.clear()
    ha_mod._turnstile_open_logged = False
    yield
    ha_mod._register_buckets.clear()
    ha_mod._turnstile_open_logged = False


def _dispatch_post(captcha_result, gotrue_result):
    """httpx.post fake that dispatches on URL: siteverify vs GoTrue.

    Returns (fake, captured) where captured["siteverify"] /
    captured["gotrue"] hold the (url, kwargs) of each call.

    #1512: bare httpx.Response(200, json=...) objects have no `request`
    attribute, so the endpoint's `resp.raise_for_status()` raises
    "Cannot call raise_for_status as the request instance has not been set"
    — the exception is swallowed by _verify_turnstile's fail-closed catch,
    CAPTCHA is rejected, and every valid-token test 400s. Attach a minimal
    request so raise_for_status() is a no-op on 2xx.
    """
    captured = {}

    def _with_request(resp: httpx.Response, url: str) -> httpx.Response:
        req = httpx.Request("POST", url)
        resp.request = req
        return resp

    def _post(url, **kwargs):
        if url == _SITEVERIFY_URL:
            captured["siteverify"] = (url, kwargs)
            if isinstance(captcha_result, Exception):
                raise captcha_result
            return _with_request(captcha_result, url)
        captured["gotrue"] = (url, kwargs)
        if isinstance(gotrue_result, Exception):
            raise gotrue_result
        return _with_request(gotrue_result, url)

    return _post, captured


class TestEmailSignupCaptcha:
    def test_fail_open_without_secret_key(self, client, caplog):
        """No TURNSTILE_SECRET_KEY → signup works as before (no token sent)."""
        with caplog.at_level("WARNING", logger="tortoise.hosted_api"):
            # Supabase unconfigured → 503 (the endpoint's own gate); the
            # point is the CAPTCHA gate did NOT 400 first.
            r = client.post("/v1/signup/email", json={"email": "a@b.co", "password": "password123"})
        assert r.status_code == 503, r.text
        assert "tortoise signup" in r.json()["detail"]
        assert any(
            "TURNSTILE_SECRET_KEY unset" in rec.message for rec in caplog.records
        ), "fail-open must be visible in the logs"

    def test_fail_open_warning_logged_once(self, client, caplog):
        """The fail-open warning is logged once per process, not per request."""
        with caplog.at_level("WARNING", logger="tortoise.hosted_api"):
            client.post("/v1/signup/email", json={"email": "a@b.co", "password": "password123"})
            client.post("/v1/signup/email", json={"email": "a@b.co", "password": "password123"})
        matches = [rec for rec in caplog.records if "TURNSTILE_SECRET_KEY unset" in rec.message]
        assert len(matches) == 1

    def test_missing_token_rejected_when_secret_set(self, client, monkeypatch):
        monkeypatch.setenv("TURNSTILE_SECRET_KEY", "secret-123")
        r = client.post("/v1/signup/email", json={"email": "a@b.co", "password": "password123"})
        assert r.status_code == 400, r.text
        assert "CAPTCHA" in r.json()["detail"]

    def test_empty_token_rejected_when_secret_set(self, client, monkeypatch):
        monkeypatch.setenv("TURNSTILE_SECRET_KEY", "secret-123")
        r = client.post(
            "/v1/signup/email",
            json={"email": "a@b.co", "password": "password123", "cf-turnstile-response": ""},
        )
        assert r.status_code == 400, r.text

    def test_invalid_token_rejected(self, client, monkeypatch):
        monkeypatch.setenv("TURNSTILE_SECRET_KEY", "secret-123")
        fake, captured = _dispatch_post(
            httpx.Response(200, json={"success": False, "error-codes": ["invalid-input-response"]}),
            httpx.Response(201, json={"id": "user-x"}),
        )
        monkeypatch.setattr(httpx, "post", fake)

        r = client.post(
            "/v1/signup/email",
            json={"email": "a@b.co", "password": "password123", "cf-turnstile-response": "tok-bad"},
        )
        assert r.status_code == 400, r.text
        assert captured.get("siteverify"), "siteverify must be called for an invalid token"
        assert "gotrue" not in captured, "GoTrue must not be called for a failed CAPTCHA"

    def test_valid_token_verified_and_signup_proceeds(self, client, monkeypatch):
        monkeypatch.setenv("TURNSTILE_SECRET_KEY", "secret-123")
        monkeypatch.setenv("SUPABASE_URL", _SUPABASE_URL)
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", _SERVICE_KEY)
        fake, captured = _dispatch_post(
            httpx.Response(200, json={"success": True}),
            httpx.Response(201, json={"id": "user-abc", "email": "a@b.co"}),
        )
        monkeypatch.setattr(httpx, "post", fake)

        r = client.post(
            "/v1/signup/email",
            json={"email": "a@b.co", "password": "password123", "cf-turnstile-response": "tok-ok"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["message"] == "user_created"
        url, kwargs = captured["siteverify"]
        assert url == _SITEVERIFY_URL
        assert kwargs["data"] == {
            "secret": "secret-123",
            "response": "tok-ok",
            "remoteip": "testclient",  # TestClient's request.client.host
        }
        assert captured["gotrue"][0] == f"{_SUPABASE_URL}/auth/v1/admin/users"

    def test_siteverify_transport_error_fail_closed(self, client, monkeypatch):
        """Fail-closed: a Cloudflare outage must not disable the CAPTCHA."""
        monkeypatch.setenv("TURNSTILE_SECRET_KEY", "secret-123")
        fake, captured = _dispatch_post(
            httpx.ConnectError("connection refused"),
            httpx.Response(201, json={"id": "user-x"}),
        )
        monkeypatch.setattr(httpx, "post", fake)

        r = client.post(
            "/v1/signup/email",
            json={"email": "a@b.co", "password": "password123", "cf-turnstile-response": "tok-ok"},
        )
        assert r.status_code == 400, r.text
        assert "gotrue" not in captured, "GoTrue must not be called when siteverify is unreachable"


class TestRegisterCaptcha:
    """Same gate on the self-service /v1/register endpoint."""

    def _db_env(self, monkeypatch, tmp_path):
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.setenv("TORTOISE_DB_PATH", os.path.join(tmp_path, "turnstile-register.db"))

    def test_fail_open_without_secret_key(self, client, monkeypatch, tmp_path):
        """No secret → /v1/register provisions a team without a token."""
        self._db_env(monkeypatch, tmp_path)
        r = client.post("/v1/register", json={"email": "a@b.co", "password": "password123"})
        assert r.status_code == 200, r.text
        assert r.json()["api_key"]

    def test_missing_token_rejected_when_secret_set(self, client, monkeypatch):
        monkeypatch.setenv("TURNSTILE_SECRET_KEY", "secret-123")
        r = client.post("/v1/register", json={"email": "a@b.co", "password": "password123"})
        assert r.status_code == 400, r.text
        assert "CAPTCHA" in r.json()["detail"]

    def test_valid_token_verified_and_registration_proceeds(self, client, monkeypatch, tmp_path):
        self._db_env(monkeypatch, tmp_path)
        monkeypatch.setenv("TURNSTILE_SECRET_KEY", "secret-123")
        fake, captured = _dispatch_post(
            httpx.Response(200, json={"success": True}),
            httpx.Response(500, json={}),
        )
        monkeypatch.setattr(httpx, "post", fake)

        r = client.post(
            "/v1/register",
            json={"email": "a@b.co", "password": "password123", "cf-turnstile-response": "tok-ok"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["api_key"]
        assert captured["siteverify"][0] == _SITEVERIFY_URL
