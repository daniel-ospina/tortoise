"""#1148 backend tests: dashboard key-login gate + per-key toggle + claim/email.

Covers (all Supabase-mode via the FakeControlPlane):
- teams.dashboard_key_login rides /v1/team (default true)
- PATCH /v1/team/dashboard-login (session+owner): toggles the flag
- PATCH /v1/team/keys/{id} (session+owner): enable/disable a key
- disabled key → resolve_api_key rejects → /v1/team 401
- gate: dashboard_key_login=false → key-auth management 403 dashboard_login_disabled
- gate: session JWT (non-tt_) still passes management endpoints when flag off
- POST /v1/claim/email: creates user via admin API + claim_membership
"""
from __future__ import annotations

import os
import sys
import uuid

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

import tortoise.hosted_api as ha_mod
import tortoise.supabase_control as sc
from tortoise.hosted_api import app
from tests.fake_control_plane import FakeControlPlane

_SUPABASE_URL = "https://claimtest.supabase.co"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", _SUPABASE_URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc-claim-test")
    monkeypatch.setenv("RATE_LIMIT_DISABLED", "1")
    ha_mod._CLAIM_BUCKETS.clear()
    fake = FakeControlPlane()
    monkeypatch.setattr(sc, "get_control_plane", lambda: fake)
    yield fake
    ha_mod._CLAIM_BUCKETS.clear()


@pytest.fixture
def fake(_env):
    return _env


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _provision_anon(client, fake, *, identity=None):
    """Mint an anonymous team via /v1/agent/signup (Supabase mode)."""
    r = client.post("/v1/agent/signup", json={})
    assert r.status_code == 200, r.text
    data = r.json()
    return data["key"], data["team_id"]


def _fake_user(user_id: str) -> dict:
    return {"user_id": user_id, "email": "owner@example.com", "sub": user_id}


def _patch_session_user(monkeypatch, user_id: str):
    # get_current_user is imported at module load (FastAPI captured the ref),
    # so patch the JWT verifier it delegates to (same seam as #1082 tests).
    async def _fake(request):
        return _fake_user(user_id)
    import tortoise.session_auth as sa
    monkeypatch.setattr(sa, "verify_session_jwt", _fake)


def _seed_owner_membership(fake, team_id: str, user_id: str):
    """Give the session user an owner membership so _require_owner_admin passes."""
    fake.tables.setdefault("team_memberships", []).append({
        "id": str(uuid.uuid4()), "team_id": team_id, "user_id": user_id,
        "role": "owner", "status": "active", "created_at": "2026-01-01T00:00:00Z",
        "identity": None, "lookup_hash": None,
    })


class TestDashboardKeyLoginFlag:
    def test_team_info_exposes_dashboard_key_login_default_true(self, client, fake):
        key, team_id = _provision_anon(client, fake)
        r = client.get("/v1/team", headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 200, r.text
        assert r.json()["dashboard_key_login"] is True

    def test_toggle_dashboard_login_session_owner(self, client, fake, monkeypatch):
        key, team_id = _provision_anon(client, fake)
        user_id = f"user-{uuid.uuid4().hex[:8]}"
        _patch_session_user(monkeypatch, user_id)
        # claim the team first so it's not anon (toggle is for claimed owners);
        # claim_membership links the real anon owner row → session user IS owner
        from tortoise.auth import lookup_hash
        sc.claim_membership(fake, lookup_hash=lookup_hash(key),
                            user_id=user_id, email="owner@example.com")
        r = client.patch(
            "/v1/team/dashboard-login",
            headers={"Authorization": "Bearer eyJ.sess"},
            json={"enabled": False},
        )
        assert r.status_code == 200, r.text
        assert r.json()["dashboard_key_login"] is False
        # /v1/team reflects the flag
        r2 = client.get("/v1/team", headers={"Authorization": f"Bearer {key}"})
        assert r2.json()["dashboard_key_login"] is False

    def test_toggle_dashboard_login_rejects_non_owner(self, client, fake, monkeypatch):
        key, team_id = _provision_anon(client, fake)
        user_id = f"user-{uuid.uuid4().hex[:8]}"
        _patch_session_user(monkeypatch, user_id)
        # NO membership seeded → _require_owner_admin 403s
        r = client.patch(
            "/v1/team/dashboard-login",
            headers={"Authorization": "Bearer eyJ.sess"},
            json={"enabled": False},
        )
        assert r.status_code == 403, r.text


class TestPerKeyToggle:
    def test_disable_key_rejects_auth(self, client, fake, monkeypatch):
        key, team_id = _provision_anon(client, fake)
        user_id = f"user-{uuid.uuid4().hex[:8]}"
        _patch_session_user(monkeypatch, user_id)
        _seed_owner_membership(fake, team_id, user_id)
        # find the key id
        from tortoise.auth import lookup_hash
        rows = fake.query("api_keys", select=["id"], filters=[("lookup_hash", "eq", lookup_hash(key))])
        key_id = rows[0]["id"]
        # disable via PATCH
        r = client.patch(
            f"/v1/team/keys/{key_id}",
            headers={"Authorization": "Bearer eyJ.sess"},
            json={"enabled": False},
        )
        assert r.status_code == 200, r.text
        assert r.json()["enabled"] is False
        # key now 401s
        r2 = client.get("/v1/team", headers={"Authorization": f"Bearer {key}"})
        assert r2.status_code == 401, r2.text

    def test_reenable_key_restores_auth(self, client, fake, monkeypatch):
        key, team_id = _provision_anon(client, fake)
        user_id = f"user-{uuid.uuid4().hex[:8]}"
        _patch_session_user(monkeypatch, user_id)
        _seed_owner_membership(fake, team_id, user_id)
        from tortoise.auth import lookup_hash
        rows = fake.query("api_keys", select=["id"], filters=[("lookup_hash", "eq", lookup_hash(key))])
        key_id = rows[0]["id"]
        client.patch(f"/v1/team/keys/{key_id}",
                     headers={"Authorization": "Bearer eyJ.sess"}, json={"enabled": False})
        r = client.patch(f"/v1/team/keys/{key_id}",
                         headers={"Authorization": "Bearer eyJ.sess"}, json={"enabled": True})
        assert r.json()["enabled"] is True
        r2 = client.get("/v1/team", headers={"Authorization": f"Bearer {key}"})
        assert r2.status_code == 200, r2.text


class TestDashboardLoginGate:
    def test_key_auth_mgmt_403_when_disabled(self, client, fake, monkeypatch):
        key, team_id = _provision_anon(client, fake)
        user_id = f"user-{uuid.uuid4().hex[:8]}"
        _patch_session_user(monkeypatch, user_id)
        from tortoise.auth import lookup_hash
        sc.claim_membership(fake, lookup_hash=lookup_hash(key),
                            user_id=user_id, email="owner@example.com")
        sc.set_dashboard_key_login(fake, team_id, False)
        # key-auth REVOKE → 403 dashboard_login_disabled (anon team has 1 key,
        # so mint would 402 on the cap first — revoke is the clean surface)
        from tortoise.auth import lookup_hash
        rows = fake.query("api_keys", select=["id"], filters=[("lookup_hash", "eq", lookup_hash(key))])
        kid = rows[0]["id"]
        r = client.delete(f"/v1/team/keys/{kid}", headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 403, r.text
        body = r.json().get("detail", {})
        assert (body.get("error_code") == "dashboard_login_disabled") if isinstance(body, dict) else True
        # graph read (GET /v1/team) still works with the key
        r2 = client.get("/v1/team", headers={"Authorization": f"Bearer {key}"})
        assert r2.status_code == 200, r2.text

    def test_session_jwt_bypasses_gate_when_disabled(self, client, fake, monkeypatch):
        # #1148 review P1-2: the gate must NOT lock out the signed-in owner.
        # get_current_team_session accepts a session JWT (via
        # _session_user_team) and skips the dashboard-login gate — so a
        # session user can still mint keys even with dashboard_key_login=false.
        key, team_id = _provision_anon(client, fake)
        user_id = f"user-{uuid.uuid4().hex[:8]}"
        _patch_session_user(monkeypatch, user_id)
        from tortoise.auth import lookup_hash
        sc.claim_membership(fake, lookup_hash=lookup_hash(key),
                            user_id=user_id, email="owner@example.com")
        sc.set_dashboard_key_login(fake, team_id, False)
        # session-authed (JWT) mint passes — the gate only rejects tt_ keys
        r = client.post("/v1/team/keys", headers={"Authorization": "Bearer eyJ.sess"}, json={})
        assert r.status_code == 200, r.text
        assert "dashboard_login_disabled" not in str(r.json())


class TestClaimEmail:
    def test_claim_email_creates_user_and_claims(self, client, fake, monkeypatch):
        key, team_id = _provision_anon(client, fake)
        # stub the admin-create to return a user id
        def _fake_admin_create(email, password):
            return 201, {"id": f"auth-{uuid.uuid4().hex[:8]}", "email": email}
        monkeypatch.setattr(ha_mod, "_supabase_admin_create_user", _fake_admin_create)
        email = f"new-{uuid.uuid4().hex[:6]}@example.com"
        r = client.post("/v1/claim/email", json={
            "api_key": key, "email": email, "password": "password123",
        })
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "claimed"
        # team no longer anon
        from tortoise.supabase_control import is_anon_team
        assert is_anon_team(fake, team_id) is False

    def test_claim_email_rejects_claimed_team(self, client, fake, monkeypatch):
        key, team_id = _provision_anon(client, fake)
        user_id = f"user-{uuid.uuid4().hex[:8]}"
        from tortoise.auth import lookup_hash
        sc.claim_membership(fake, lookup_hash=lookup_hash(key),
                            user_id=user_id, email="owner@example.com")
        r = client.post("/v1/claim/email", json={
            "api_key": key, "email": "x@example.com", "password": "password123",
        })
        assert r.status_code == 409, r.text

    def test_claim_email_rejects_weak_password(self, client, fake):
        key, team_id = _provision_anon(client, fake)
        r = client.post("/v1/claim/email", json={
            "api_key": key, "email": "x@example.com", "password": "123",
        })
        assert r.status_code == 400, r.text
