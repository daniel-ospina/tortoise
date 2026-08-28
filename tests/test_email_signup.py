"""#801: server-side email signup — over_email_send_rate_limit fix tests.

POST /v1/signup/email creates the Supabase auth user via the GoTrue ADMIN
API (service-role key) with email_confirm=true, so NO confirmation email
is sent and Supabase's built-in SMTP project-wide email-send bucket
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

# #1719 (Task 3): team_memberships.user_id is a uuid column — real JWT
# subjects are UUIDs, so non-UUID user_id literals are prod-impossible.
_U_REG_CLAIM = "9f2c1a40-0000-4a00-8000-00000000000c"


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
        detail = r.json()["detail"]
        assert "tortoise signup" in detail
        assert "zero-email" in detail  # the selfhost fallback framing the name promises

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

    def test_email_confirm_false_opt_in_passes_flag_to_gotrue(self, client, monkeypatch):
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
        """#863: the 429 pass-through now carries the mechanism (error_code) as
        a dict detail — message keeps the `tortoise signup` pointer."""
        _configured(monkeypatch)
        fake, _ = _fake_post(httpx.Response(
            429, json={"code": 429, "error_code": "over_email_send_rate_limit",
                       "msg": "email rate limit exceeded"}))
        monkeypatch.setattr(httpx, "post", fake)

        r = client.post("/v1/signup/email", json={"email": "a@b.co", "password": "password123"})
        assert r.status_code == 429, r.text
        assert "tortoise signup" in r.json()["detail"]["message"]
        assert r.json()["detail"]["error_code"] == "over_email_send_rate_limit"
        assert r.headers.get("retry-after") == "3600"

    def test_gotrue_429_per_ip_code_passthrough(self, client, monkeypatch):
        """#863: a GoTrue per-IP rate-limit code passes through unchanged so the
        client can pick the short tier + network copy."""
        _configured(monkeypatch)
        fake, _ = _fake_post(httpx.Response(
            429, json={"code": 429, "error_code": "over_request_rate_limit",
                       "msg": "request rate limit reached"}))
        monkeypatch.setattr(httpx, "post", fake)

        r = client.post("/v1/signup/email", json={"email": "a@b.co", "password": "password123"})
        assert r.status_code == 429, r.text
        assert r.json()["detail"]["error_code"] == "over_request_rate_limit"

    @pytest.mark.parametrize("msg,expected", [
        ("email rate limit exceeded", "over_email_send_rate_limit"),
        ("request rate limit reached", "over_request_rate_limit"),
    ])
    def test_gotrue_429_code_less_msg_heuristic(self, client, monkeypatch, msg, expected):
        """#863: a code-less GoTrue 429 (stale body) is classified by message —
        "email" → email bucket, otherwise per-IP request limit."""
        _configured(monkeypatch)
        fake, _ = _fake_post(httpx.Response(429, json={"msg": msg}))
        monkeypatch.setattr(httpx, "post", fake)

        r = client.post("/v1/signup/email", json={"email": "a@b.co", "password": "password123"})
        assert r.status_code == 429, r.text
        assert r.json()["detail"]["error_code"] == expected

    def test_gotrue_429_error_description_only_maps_email_bucket(self, client, monkeypatch):
        """#863 (review P2): a body with ONLY error_description (prose, no
        error_code/msg) must classify as the email bucket when it mentions
        email — the description belongs in the message scan, and the
        "email"-in-code fallback catches it. Regression: it was previously
        slotted as a code and fell through to the per-IP default."""
        _configured(monkeypatch)
        fake, _ = _fake_post(httpx.Response(
            429, json={"code": 429, "error_description": "Email rate limit exceeded"}))
        monkeypatch.setattr(httpx, "post", fake)

        r = client.post("/v1/signup/email", json={"email": "a@b.co", "password": "password123"})
        assert r.status_code == 429, r.text
        assert r.json()["detail"]["error_code"] == "over_email_send_rate_limit"

    def test_gotrue_429_error_code_wins_over_numeric_code(self, client, monkeypatch):
        """#863: real GoTrue bodies carry the HTTP status in `code` (numeric) —
        the stable error_code must win, or the known-code passthrough is dead
        code. A non-email msg proves the code (not the heuristic) decided."""
        _configured(monkeypatch)
        fake, _ = _fake_post(httpx.Response(
            429, json={"code": 429, "error_code": "over_email_send_rate_limit",
                       "msg": "request rate limit reached"}))
        monkeypatch.setattr(httpx, "post", fake)

        r = client.post("/v1/signup/email", json={"email": "a@b.co", "password": "password123"})
        assert r.status_code == 429, r.text
        assert r.json()["detail"]["error_code"] == "over_email_send_rate_limit"

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
        # #863: the API's own per-IP bucket 429 must be labeled as per-IP so the
        # client uses the short tier + network copy (not the email-bucket copy).
        assert r.json()["detail"]["error_code"] == "over_request_rate_limit_ip"
        assert r.json()["detail"]["message"] == "Too many registration attempts. Please try again later."


class TestEmailSignupClaim:
    """#1082 PR1 + #1765 demotion — reg- identity teams.

    A reg- team (registered with email at mint, identity anchor
    reg-<sha256(email)[:12]>, user_id NULL) is claimable exactly like an
    anon team. #1765: claim NO LONGER writes teams.email — the mint-time
    contact value survives (email is a user property now). Same key, same
    team, memories intact.
    """

    @pytest.fixture(autouse=True)
    def _supabase_claim_env(self, monkeypatch):
        from tests.fake_control_plane import FakeControlPlane  # noqa: I001
        import tortoise.supabase_control as sc
        import tortoise.hosted_api as ha_mod

        monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "supabase")
        monkeypatch.setenv("SUPABASE_URL", "https://regclaim.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc-reg-claim")
        monkeypatch.setenv("RATE_LIMIT_DISABLED", "1")
        fake = FakeControlPlane()
        monkeypatch.setattr(sc, "get_control_plane", lambda: fake)

        async def _confirmed(request):
            return True

        monkeypatch.setattr(ha_mod, "_gotrue_email_confirmed", _confirmed)
        return fake

    def test_reg_claim_email_not_overwritten(self, client, monkeypatch):
        """#1765 demotion: claim never writes teams.email — the mint-time
        contact value (reg-a@example.com) survives the claim."""
        import uuid as _uuid  # noqa: I001
        from tortoise.auth import lookup_hash as _lh, hash_api_key as _hash
        import tortoise.supabase_control as sc
        fake = sc.get_control_plane()

        # a reg- mint: identity = reg-<sha256(email)[:12]>, email set at mint
        reg_email = "reg-a@example.com"
        import hashlib
        identity = "reg-" + hashlib.sha256(reg_email.encode()).hexdigest()[:12]
        team_id = f"team-reg-{_uuid.uuid4().hex[:10]}"
        api_key = f"tt_{_uuid.uuid4().hex}"
        sc.provision_team(fake, **{
            "p_user_id": None, "p_identity": identity,
            "p_team_id": team_id, "p_team_name": f"Reg {team_id}",
            "p_api_key": api_key, "p_key_hash": _hash(api_key),
            "p_lookup_hash": _lh(api_key), "p_graph_name": f"team_{team_id}",
            "p_email": reg_email, "p_key_prefix": api_key[:10], "p_tier": "free",
            "p_max_users": 1, "p_max_graphs": 1, "p_ops_allowance": 10000,
            "p_graph_size_cap": 10000,
        })

        async def _verify(request):
            return {"user_id": _U_REG_CLAIM, "email": "verified-b@example.com",
                    "app_metadata": {"providers": ["github"]}}

        monkeypatch.setattr(ha_mod, "verify_session_jwt", _verify)
        r = client.post(
            "/v1/claim",
            headers={"Authorization": "Bearer abc.def.ghi"},
            json={"api_key": api_key},
        )
        assert r.status_code == 200, r.text
        team_row = next(t for t in fake.tables["teams"] if t["id"] == team_id)
        assert team_row.get("email") == "reg-a@example.com", (
            f"claim must NOT write teams.email — mint contact survives, "
            f"got {team_row.get('email')}")
        mem = next(m for m in fake.tables["team_memberships"]
                   if m["team_id"] == team_id)
        assert mem["user_id"] == _U_REG_CLAIM
        assert mem["identity"] is None

class TestRegisterIdempotencyReanchor:
    """#1765: post-demotion register idempotency — the reg- identity row is
    the authoritative unclaimed-owner key (uq_teams_email is gone). A second
    register for the same email → 409 already_registered, never 500.
    """

    @pytest.fixture(autouse=True)
    def _reg_env(self, monkeypatch):
        from tests.fake_control_plane import FakeControlPlane  # noqa: I001
        import tortoise.supabase_control as sc
        monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "supabase")
        monkeypatch.setenv("SUPABASE_URL", "https://regid.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc-reg-id")
        monkeypatch.setenv("RATE_LIMIT_DISABLED", "1")
        fake = FakeControlPlane()
        monkeypatch.setattr(sc, "get_control_plane", lambda: fake)
        return fake

    def test_second_register_same_email_409(self, client, monkeypatch):
        """First register mints a reg- owner row; the second register (same
        email, no Supabase user yet) must 409 on the reg- identity pre-check
        — never a 500."""
        import tortoise.hosted_api as ha_mod  # noqa: I001
        from tortoise.supabase_control import membership_by_identity
        import hashlib

        email = "dup-reg@example.com"
        # simulate the leftover from a first register: the reg- owner row
        # exists but the graph mint was never completed / client never
        # finished signup — the exact case team_by_email alone misses.
        identity = "reg-" + hashlib.sha256(email.lower().encode()).hexdigest()[:12]
        import uuid as _uuid

        import tortoise.supabase_control as sc
        from tortoise.auth import hash_api_key as _hash
        from tortoise.auth import lookup_hash as _lh
        fake = sc.get_control_plane()
        sc.provision_team(fake, **{
            "p_user_id": None, "p_identity": identity,
            "p_team_id": f"team-regdup-{_uuid.uuid4().hex[:10]}",
            "p_team_name": "RegDup", "p_api_key": f"tt_{_uuid.uuid4().hex}",
            "p_key_hash": _hash("tt_x"), "p_lookup_hash": _lh("tt_x"),
            "p_graph_name": "team_regdup", "p_email": email,
            "p_key_prefix": "tt_regdup", "p_tier": "free",
            "p_max_users": 1, "p_max_graphs": 1, "p_ops_allowance": 10000,
            "p_graph_size_cap": 10000,
        })
        assert membership_by_identity(fake, identity) is not None

        # the second register must hit the reg- identity pre-check → 409
        from fastapi.testclient import TestClient
        with TestClient(ha_mod.app) as c:
            r = c.post("/v1/register", json={
                "email": email, "password": "hunter22"})
            assert r.status_code == 409, r.text
            assert r.json()["detail"]["message"] == "already_registered"


class TestRegisterRace:
    """#1765 Task 3 Step 10: concurrent registers with the same email → one
    200, one 409, never 500 (the reg- identity unique-index backstop)."""

    @pytest.fixture(autouse=True)
    def _race_env(self, monkeypatch):
        from tests.fake_control_plane import FakeControlPlane  # noqa: I001
        import tortoise.supabase_control as sc
        monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "supabase")
        monkeypatch.setenv("SUPABASE_URL", "https://racesupabase.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc-race")
        monkeypatch.setenv("RATE_LIMIT_DISABLED", "1")
        fake = FakeControlPlane()
        monkeypatch.setattr(sc, "get_control_plane", lambda: fake)
        return fake

    def test_concurrent_register_one_200_one_409(self, client, monkeypatch):
        """Two threads POST /v1/register with the same email. The reg-
        identity pre-check + fake uq_member_identity_active parity yield
        exactly one 200 and one 409 — never a 500."""
        import threading

        email = "race@example.com"
        statuses = []
        lock = threading.Lock()

        def register():
            r = client.post("/v1/register", json={"email": email, "password": "hunter22"})
            with lock:
                statuses.append(r.status_code)

        t1 = threading.Thread(target=register)
        t2 = threading.Thread(target=register)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        codes = sorted(statuses)
        assert 500 not in codes, f"race must never 500, got {codes}"
        assert codes == [200, 409], f"expected one 200 + one 409, got {codes}"
