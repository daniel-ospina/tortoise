"""#801: server-side email signup — over_email_send_rate_limit fix tests.

POST /v1/signup/email creates the Supabase auth user via the GoTrue ADMIN
API (service-role key) with email_confirm=true, so NO confirmation email
is sent and Supabase's built-in SMTP per-IP send bucket
(over_email_send_rate_limit, the P1 production signup blocker) is never
touched. The client then signs in with the password.

Covered here:
- 503 with a clear zero-email pointer when Supabase is not configured
  (selfhost — the web form falls back to its legacy client-side signup).
- 200 user_created with email_confirm=true in the GoTrue admin request
  body; TORTOISE_SIGNUP_EMAIL_CONFIRM=false opts back into the
  confirmation-email funnel.
- GoTrue error mapping: user_already_exists → 409 already_registered
  (same contract as /v1/register), weak_password → 422, rate-limit 429
  pass-through with the `tortoise signup` pointer + Retry-After,
  transport errors → 502.
- Validation (422) and the shared /v1/register IP bucket (3/hour).
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
import pytest
from fastapi.testclient import TestClient

import tortoise.hosted_api as ha_mod
from tortoise.hosted_api import app

_SUPABASE_URL = "https://testref.supabase.co"
_SERVICE_KEY = "test-service-role-key-123"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _clean_supabase_env(monkeypatch):
    """Start every test from a Supabase-unconfigured baseline + disabled
    IP limiter (unless a test opts in). Also resets the shared /v1/register
    IP bucket so this module never poisons (or is poisoned by) other tests."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    monkeypatch.delenv("TORTOISE_SIGNUP_EMAIL_CONFIRM", raising=False)
    monkeypatch.setenv("RATE_LIMIT_DISABLED", "1")
    ha_mod._register_buckets.clear()
    yield
    ha_mod._register_buckets.clear()


def _configured(monkeypatch, *, email_confirm: str | None = None):
    monkeypatch.setenv("SUPABASE_URL", _SUPABASE_URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", _SERVICE_KEY)
    if email_confirm is not None:
        monkeypatch.setenv("TORTOISE_SIGNUP_EMAIL_CONFIRM", email_confirm)


def _fake_post(fake):
    """Install a httpx.post fake returning `fake` (a response or exception).
    Returns the captured (url, kwargs) for assertions."""
    captured = {}

    def _post(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        if isinstance(fake, Exception):
            raise fake
        return fake

    return _post, captured


class TestEmailSignup:
    def test_unconfigured_returns_503_with_zero_email_pointer(self, client):
        r = client.post("/v1/signup/email", json={"email": "a@b.co", "password": "password123"})
        assert r.status_code == 503, r.text
        assert "tortoise signup" in r.json()["detail"]

    @pytest.mark.parametrize("status", [200, 201])
    def test_creates_user_with_email_confirm_true(self, client, monkeypatch, status):
        _configured(monkeypatch)
        resp = httpx.Response(status, json={"id": "user-abc", "email": "a@b.co"})
        fake, captured = _fake_post(resp)
        monkeypatch.setattr(httpx, "post", fake)

        r = client.post("/v1/signup/email", json={"email": "A@B.co", "password": "password123"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["message"] == "user_created"
        assert body["user_id"] == "user-abc"
        assert body["email"] == "a@b.co"  # lowercased by validation
        assert body["email_confirm"] is True

        assert captured["url"] == f"{_SUPABASE_URL}/auth/v1/admin/users"
        assert captured["kwargs"]["json"] == {
            "email": "a@b.co", "password": "password123", "email_confirm": True,
        }
        assert captured["kwargs"]["headers"]["Authorization"] == f"Bearer {_SERVICE_KEY}"
        assert captured["kwargs"]["headers"]["apikey"] == _SERVICE_KEY

    def test_email_confirm_false_opt_in_sends_confirmation_email(self, client, monkeypatch):
        _configured(monkeypatch, email_confirm="false")
        fake, captured = _fake_post(httpx.Response(201, json={"id": "user-2"}))
        monkeypatch.setattr(httpx, "post", fake)

        r = client.post("/v1/signup/email", json={"email": "a@b.co", "password": "password123"})
        assert r.status_code == 200, r.text
        assert captured["kwargs"]["json"]["email_confirm"] is False

    @pytest.mark.parametrize("falsy", ["0", "no", "off"])
    def test_email_confirm_falsy_variants_disable_confirmation(self, client, monkeypatch, falsy):
        _configured(monkeypatch, email_confirm=falsy)
        fake, captured = _fake_post(httpx.Response(200, json={"id": "user-3"}))
        monkeypatch.setattr(httpx, "post", fake)

        r = client.post("/v1/signup/email", json={"email": "a@b.co", "password": "password123"})
        assert r.status_code == 200, r.text
        assert captured["kwargs"]["json"]["email_confirm"] is False, f"{falsy!r} should mean email_confirm=false"

    def test_already_registered_maps_409(self, client, monkeypatch):
        _configured(monkeypatch)
        fake, _ = _fake_post(httpx.Response(
            422, json={"code": "user_already_exists",
                       "msg": "A user with this email address has already been registered"}))
        monkeypatch.setattr(httpx, "post", fake)

        r = client.post("/v1/signup/email", json={"email": "a@b.co", "password": "password123"})
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["message"] == "already_registered"
        assert r.json()["detail"]["email"] == "a@b.co"

    def test_weak_password_maps_422(self, client, monkeypatch):
        _configured(monkeypatch)
        fake, _ = _fake_post(httpx.Response(
            422, json={"code": "weak_password", "msg": "Password should be at least 6 characters."}))
        monkeypatch.setattr(httpx, "post", fake)

        r = client.post("/v1/signup/email", json={"email": "a@b.co", "password": "123456"})
        assert r.status_code == 422, r.text
        assert "Password is too weak" in r.json()["detail"]

    def test_unrecognized_gotrue_422_does_not_leak_raw_message(self, client, monkeypatch):
        _configured(monkeypatch)
        fake, _ = _fake_post(httpx.Response(
            422, json={"code": "some_future_error", "msg": "internal db constraint detail"}))
        monkeypatch.setattr(httpx, "post", fake)

        r = client.post("/v1/signup/email", json={"email": "a@b.co", "password": "password123"})
        assert r.status_code == 422, r.text
        assert "internal db constraint detail" not in r.json()["detail"]
        assert "Invalid signup request" in r.json()["detail"]

    def test_gotrue_429_passthrough_with_cli_pointer(self, client, monkeypatch):
        _configured(monkeypatch)
        fake, _ = _fake_post(httpx.Response(
            429, json={"code": "over_email_send_rate_limit", "msg": "email rate limit exceeded"}))
        monkeypatch.setattr(httpx, "post", fake)

        r = client.post("/v1/signup/email", json={"email": "a@b.co", "password": "password123"})
        assert r.status_code == 429, r.text
        assert "tortoise signup" in r.json()["detail"]
        assert r.headers.get("retry-after") == "3600"

    def test_transport_error_maps_502(self, client, monkeypatch):
        _configured(monkeypatch)
        fake, _ = _fake_post(httpx.ConnectError("boom"))
        monkeypatch.setattr(httpx, "post", fake)

        r = client.post("/v1/signup/email", json={"email": "a@b.co", "password": "password123"})
        assert r.status_code == 502, r.text

    def test_invalid_email_rejected_without_calling_supabase(self, client, monkeypatch):
        _configured(monkeypatch)
        fake, captured = _fake_post(httpx.Response(200, json={"id": "x"}))
        monkeypatch.setattr(httpx, "post", fake)

        r = client.post("/v1/signup/email", json={"email": "not-an-email", "password": "password123"})
        assert r.status_code == 422, r.text
        # #801 review P1: the 422 detail must NOT echo the submitted password
        assert "password123" not in r.json()["detail"]
        r2 = client.post("/v1/signup/email", json={"email": "a@b.co"})
        assert r2.status_code == 422, r2.text
        assert "url" not in captured  # GoTrue never called

    def test_shared_ip_bucket_3_per_hour(self, client, monkeypatch):
        _configured(monkeypatch)
        monkeypatch.delenv("RATE_LIMIT_DISABLED", raising=False)  # limiter ON
        fake, _ = _fake_post(httpx.Response(200, json={"id": "u1"}))
        monkeypatch.setattr(httpx, "post", fake)

        for _ in range(3):
            r = client.post("/v1/signup/email", json={"email": "a@b.co", "password": "password123"})
            assert r.status_code == 200, r.text
        r = client.post("/v1/signup/email", json={"email": "a@b.co", "password": "password123"})
        assert r.status_code == 429, r.text
        assert r.headers.get("retry-after") == "3600"
