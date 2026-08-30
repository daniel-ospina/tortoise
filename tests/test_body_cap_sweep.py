"""#2032 body-sweep cap tests — public surfaces.

The sweep replaces every `request.json()`/`request.body()` in
tortoise/hosted_api.py with the #2029 streaming cap helper
(`_read_capped_body`) + per-surface caps. These tests pin the TWO load-bearing
properties of the sweep:

1. **413-before-parse:** an oversized CHUNKED body (no Content-Length — the
   RFC 7230 Transfer-Encoding case the cap exists for) returns 413 with the
   per-surface detail, and 413 is NOT swallowed by any local try/catch-all.
2. **Contract preservation:** malformed / empty / valid bodies behave exactly
   as before the sweep (500 / 422 / 400 / {} / mint).

Auth-gated surfaces (create_api_key, commit_session, claim_team,
agent_token_revoke, /internal/provision, /internal/demo) get their cap tests
in their home files (test_hosted_api.py, test_commit_endpoint.py,
test_claim_endpoints.py, test_signup_token_revoke.py) where the auth fixtures
live. This file covers the PUBLIC + webhook + OAuth surfaces with
self-contained fixtures.
"""
from __future__ import annotations

import contextlib
import os

import pytest
from fastapi.testclient import TestClient

# Pepper + disabled rate limiting BEFORE tortoise imports (mirrors
# test_hosted_api.py / test_billing.py); the pepper is required by
# tortoise.auth at import time.
os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")
# Registry-lane determinism: never inherit a dev shell's Supabase env.
for _v in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_SERVICE_KEY",
           "TORTOISE_CONTROL_PLANE", "TURNSTILE_SECRET_KEY",
           "TORTOISE_SIGNUP_EMAIL_CONFIRM"):
    os.environ.pop(_v, None)

from tortoise.hosted_api import app  # noqa: E402, I001

from tests.fake_control_plane import FakeControlPlane  # noqa: E402


def _oversized_chunked(n_chunks: int = 8, step: int = 8192):
    """Chunked generator, NO content-length — forces the streaming-cap path
    (a buffering parse of these chunks would 500, so 413 uniquely proves the
    cap fired before parse). 64 KiB total with defaults — > every test cap
    (256 B / 8192 B / 1024 B monkeypatched below)."""
    for _ in range(n_chunks):
        yield b"x" * step


def _patch_sdk_init(monkeypatch, db_path: str):
    """Route EVERY TortoiseSDK construction to one temp DB (embedded lane),
    mirroring test_hosted_api._patch_tortoise_sdk_init."""
    import tortoise.hosted_api as ha_mod

    _orig = ha_mod.TortoiseSDK.__init__

    def _patched(self, db_path_arg=None, *, namespace=None, **kwargs):
        _orig(self, db_path, namespace=namespace)

    @contextlib.contextmanager
    def _manager():
        monkeypatch.setattr(ha_mod.TortoiseSDK, "__init__", _patched)
        ha_mod._FALLBACK_KEEPALIVE.clear()
        try:
            yield
        finally:
            app.dependency_overrides.clear()
            ha_mod._FALLBACK_KEEPALIVE.clear()

    return _manager()


@pytest.fixture
def embedded_client(monkeypatch, tmp_path):
    """Registry-lane TestClient on a temp embedded DB (no Docker)."""

    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    for _v in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY",
               "SUPABASE_SERVICE_KEY", "TORTOISE_CONTROL_PLANE",
               "TURNSTILE_SECRET_KEY", "TORTOISE_SIGNUP_EMAIL_CONFIRM"):
        monkeypatch.delenv(_v, raising=False)
    monkeypatch.setenv("RATE_LIMIT_DISABLED", "1")
    db_path = os.path.join(tmp_path, "sweep.db")
    with _patch_sdk_init(monkeypatch, db_path), \
            TestClient(app, raise_server_exceptions=False) as tc:
        yield tc


@pytest.fixture
def supabase_client(monkeypatch, tmp_path):
    """Supabase-mode TestClient (FakeControlPlane) for the OAuth surfaces."""
    import tortoise.supabase_control as sc

    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc_role_key_test")
    monkeypatch.setenv("RATE_LIMIT_DISABLED", "1")
    cp = FakeControlPlane({"teams": [], "api_keys": [],
                           "team_memberships": [], "invitations": []})
    monkeypatch.setattr(sc, "get_control_plane", lambda: cp)
    db_path = os.path.join(tmp_path, "oauth_sweep.db")
    with _patch_sdk_init(monkeypatch, db_path), \
            TestClient(app, raise_server_exceptions=False) as tc:
        yield tc


# ── 413 detail literals (import-time derivation pin) ───────────────────


class TestDetailConstantsPinned:
    """#2032 review (second-model gate P2): the 413 detail strings derive
    their byte counts from the cap constants at IMPORT time. Pin the exact
    literals here (no monkeypatched caps) so a derivation regression — or a
    source-level cap change that alters the message — is caught; the per-site
    413 tests above assert `detail == ha_mod._BODY_413_DETAIL` (constant
    reference), which cannot catch message drift."""

    def test_body_detail_literals(self):
        import tortoise.hosted_api as ha_mod
        assert ha_mod._BODY_413_DETAIL == (
            "request body exceeds the size cap (256 KiB)")
        assert ha_mod._COMMIT_SESSION_413_DETAIL == (
            "commit session request body exceeds the size cap (8 MiB)")
        assert ha_mod._STRIPE_WEBHOOK_413_DETAIL == (
            "Stripe webhook body exceeds the size cap (1 MiB)")


# ── /v1/register ────────────────────────────────────────────────────────


class TestRegisterCap:
    def test_register_oversized_chunked_413(self, embedded_client, monkeypatch):
        import tortoise.hosted_api as ha_mod
        monkeypatch.setattr(ha_mod, "_BODY_MAX_BYTES", 256)
        r = embedded_client.post("/v1/register", content=_oversized_chunked())
        assert r.status_code == 413
        assert r.json()["detail"] == ha_mod._BODY_413_DETAIL

    def test_register_oversized_spoofed_cl_413(self, embedded_client, monkeypatch):
        """Valid payload + a spoofed short Content-Length: the streaming cap
        catches the under-claim (the CL header is never trusted)."""
        import tortoise.hosted_api as ha_mod
        monkeypatch.setattr(ha_mod, "_BODY_MAX_BYTES", 256)
        payload = b'{"email": "a@b.co", "password": "secret123"}'
        r = embedded_client.post(
            "/v1/register", content=_oversized_chunked(),
            headers={"content-length": str(len(payload))})
        assert r.status_code == 413

    def test_register_malformed_500_preserved(self, embedded_client):
        """Malformed JSON → uncaught JSONDecodeError → 500 (unchanged)."""
        r = embedded_client.post(
            "/v1/register", content=b"{not json",
            headers={"content-type": "application/json"})
        assert r.status_code == 500

    def test_register_valid_mint_200(self, embedded_client):
        r = embedded_client.post(
            "/v1/register",
            json={"email": "cap-sweep@example.com", "password": "supersecret1"})
        assert r.status_code == 200, r.text
        assert r.json()["team_id"]


# ── /v1/signup/email ─────────────────────────────────────────────────────


class TestEmailSignupCap:
    def test_email_signup_oversized_chunked_413(self, embedded_client, monkeypatch):
        """Cap fires BEFORE the unconfigured-503 check (the 503 runs after the
        parse) — an oversized body 413s even on an unconfigured deployment."""
        import tortoise.hosted_api as ha_mod
        monkeypatch.setattr(ha_mod, "_BODY_MAX_BYTES", 256)
        r = embedded_client.post("/v1/signup/email", content=_oversized_chunked())
        assert r.status_code == 413
        assert r.json()["detail"] == ha_mod._BODY_413_DETAIL

    def test_email_signup_malformed_422_exact_detail(self, embedded_client):
        """Malformed/empty → 422 with the EXACT long string (unchanged)."""
        r = embedded_client.post(
            "/v1/signup/email", content=b"{",
            headers={"content-type": "application/json"})
        assert r.status_code == 422
        assert r.json()["detail"] == (
            "Invalid email or password. Check the email format and that the "
            "password is at least 6 characters.")

    def test_email_signup_valid_unconfigured_503(self, embedded_client):
        """A VALID body parses past the cap and hits the unconfigured 503 —
        proves the capped read is byte-transparent for under-cap bodies."""
        r = embedded_client.post(
            "/v1/signup/email",
            json={"email": "cap@example.com", "password": "supersecret1"})
        assert r.status_code == 503, r.text


# ── /v1/session/login ────────────────────────────────────────────────────


class TestSessionLoginCap:
    def test_session_login_oversized_chunked_413(self, embedded_client, monkeypatch):
        import tortoise.hosted_api as ha_mod
        monkeypatch.setattr(ha_mod, "_BODY_MAX_BYTES", 256)
        r = embedded_client.post("/v1/session/login", content=_oversized_chunked())
        assert r.status_code == 413
        assert r.json()["detail"] == ha_mod._BODY_413_DETAIL

    def test_session_login_empty_body_401_preserved(self, embedded_client):
        """Empty body → {} coercion → empty api_key → 401 prefix gate
        (unchanged — the 413 must NOT leak into the catch-all)."""
        r = embedded_client.post(
            "/v1/session/login", content=b"",
            headers={"content-type": "application/json"})
        assert r.status_code == 401

    def test_session_login_malformed_401_preserved(self, embedded_client):
        r = embedded_client.post(
            "/v1/session/login", content=b"{nope",
            headers={"content-type": "application/json"})
        assert r.status_code == 401


# ── /v1/agent/signup + /v1/agent/recover ─────────────────────────────────


class TestAgentSignupCap:
    def test_agent_signup_oversized_json_413(self, embedded_client, monkeypatch):
        import tortoise.hosted_api as ha_mod
        monkeypatch.setattr(ha_mod, "_BODY_MAX_BYTES", 256)
        r = embedded_client.post(
            "/v1/agent/signup", content=_oversized_chunked(),
            headers={"content-type": "application/json"})
        assert r.status_code == 413
        assert r.json()["detail"] == ha_mod._BODY_413_DETAIL

    def test_agent_signup_oversized_non_json_not_capped(self, embedded_client, monkeypatch):
        """PIN: the capped read sits INSIDE the content-type branch — an
        oversized NON-JSON body is ignored ({} path), never 413'd. A misplaced
        read outside the branch would turn this into a 413."""
        import tortoise.hosted_api as ha_mod
        monkeypatch.setattr(ha_mod, "_BODY_MAX_BYTES", 256)
        r = embedded_client.post(
            "/v1/agent/signup", content=_oversized_chunked(),
            headers={"content-type": "text/plain"})
        assert r.status_code != 413

    def test_agent_signup_malformed_json_500_preserved(self, embedded_client):
        """JSON content-type + malformed → uncaught → 500 (unchanged)."""
        r = embedded_client.post(
            "/v1/agent/signup", content=b"{oops",
            headers={"content-type": "application/json"})
        assert r.status_code == 500

    def test_agent_signup_empty_json_500_preserved(self, embedded_client):
        """Empty body + JSON content-type → json.loads(b'') raises → 500
        (unchanged — the rejected `if raw else None` guard would have made
        this a mint; the sweep must NOT change it)."""
        r = embedded_client.post(
            "/v1/agent/signup", content=b"",
            headers={"content-type": "application/json"})
        assert r.status_code == 500

    def test_agent_signup_empty_no_ct_mints_200(self, embedded_client):
        """No content-type → body {} → mint path (unchanged)."""
        r = embedded_client.post("/v1/agent/signup", content=b"")
        assert r.status_code == 200, r.text
        assert r.json()["key"]


class TestAgentRecoverCap:
    def test_agent_recover_oversized_json_413(self, embedded_client, monkeypatch):
        import tortoise.hosted_api as ha_mod
        monkeypatch.setattr(ha_mod, "_BODY_MAX_BYTES", 256)
        r = embedded_client.post(
            "/v1/agent/recover", content=_oversized_chunked(),
            headers={"content-type": "application/json"})
        assert r.status_code == 413

    def test_agent_recover_empty_body_422_preserved(self, embedded_client):
        """{} body → no signup_token → uniform 422 (unchanged)."""
        r = embedded_client.post("/v1/agent/recover", content=b"{}")
        assert r.status_code == 422
        assert r.json()["detail"]["error_code"] == "invalid_signup_token"


# ── /webhooks/stripe ─────────────────────────────────────────────────────


class TestStripeWebhookCap:
    def test_webhook_oversized_chunked_413(self, embedded_client, monkeypatch):
        """413 fires BEFORE signature verification — no Stripe env needed."""
        import tortoise.hosted_api as ha_mod
        monkeypatch.setattr(ha_mod, "_STRIPE_WEBHOOK_MAX_BYTES", 1024)
        r = embedded_client.post(
            "/webhooks/stripe", content=_oversized_chunked(),
            headers={"stripe-signature": "t=1,v1=deadbeef"})
        assert r.status_code == 413
        assert r.json()["detail"] == ha_mod._STRIPE_WEBHOOK_413_DETAIL


# ── OAuth surfaces (Supabase mode) ───────────────────────────────────────


class TestOAuthCaps:
    def test_oauth_token_oversized_413(self, supabase_client, monkeypatch):
        import tortoise.hosted_api as ha_mod
        monkeypatch.setattr(ha_mod, "_BODY_MAX_BYTES", 256)
        r = supabase_client.post(
            "/oauth/token", content=_oversized_chunked(),
            headers={"content-type": "application/x-www-form-urlencoded"})
        assert r.status_code == 413
        assert r.json()["detail"] == ha_mod._BODY_413_DETAIL

    def test_oauth_token_empty_form_400_preserved(self, supabase_client):
        """Empty form → parse_qs {} → grant_type None → RFC 6749
        unsupported_grant_type (unchanged)."""
        r = supabase_client.post("/oauth/token", content=b"")
        assert r.status_code == 400
        assert r.json()["error"] == "unsupported_grant_type"

    def test_oauth_revoke_oversized_413(self, supabase_client, monkeypatch):
        import tortoise.hosted_api as ha_mod
        monkeypatch.setattr(ha_mod, "_BODY_MAX_BYTES", 256)
        r = supabase_client.post(
            "/oauth/revoke", content=_oversized_chunked(),
            headers={"content-type": "application/x-www-form-urlencoded"})
        assert r.status_code == 413

    def test_oauth_consent_oversized_413(self, supabase_client, monkeypatch):
        """Cap fires BEFORE verify_session_jwt — no session stub needed."""
        import tortoise.hosted_api as ha_mod
        monkeypatch.setattr(ha_mod, "_BODY_MAX_BYTES", 256)
        r = supabase_client.post("/oauth/consent", content=_oversized_chunked())
        assert r.status_code == 413

    def test_oauth_dcr_register_oversized_413(self, supabase_client, monkeypatch):
        import tortoise.hosted_api as ha_mod
        monkeypatch.setattr(ha_mod, "_BODY_MAX_BYTES", 256)
        r = supabase_client.post("/register", content=_oversized_chunked())
        assert r.status_code == 413

    def test_oauth_consent_malformed_400_preserved(self, supabase_client):
        r = supabase_client.post("/oauth/consent", content=b"{",
                                 headers={"content-type": "application/json"})
        assert r.status_code == 400
        assert r.json()["detail"] == "Invalid JSON body"
