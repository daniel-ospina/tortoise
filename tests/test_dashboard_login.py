"""#1148 backend tests: dashboard key-login gate + per-key toggle + claim/email.

Covers (all Supabase-mode via the FakeControlPlane):
- teams.dashboard_key_login rides /v1/team (default true)
- PATCH /v1/team/dashboard-login (session+owner): toggles the flag
- PATCH /v1/team/keys/{id} (session+owner): enable/disable a key
- disabled key → resolve_api_key rejects → /v1/team 401
- gate: dashboard_key_login=false → key-auth management 403 dashboard_login_disabled
- gate: session JWT (non-tt_) still passes management endpoints when flag off
- POST /v1/claim/email: creates user via admin API + claim_membership
- #2230: key DELETE/PATCH honor ?team_id= in session mode (multi-membership
  fixture — non-first team revoke/rename/toggle with the pin 200s, a
  wrong-team or non-member pin fails closed 403, pinless PATCH unchanged)
"""
from __future__ import annotations

import os
import sys
import uuid

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: I001
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
        key, team_id = _provision_anon(client, fake)  # noqa: RUF059
        r = client.get("/v1/team", headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 200, r.text
        assert r.json()["dashboard_key_login"] is True

    def test_toggle_dashboard_login_session_owner(self, client, fake, monkeypatch):
        key, team_id = _provision_anon(client, fake)  # noqa: RUF059
        user_id = str(uuid.uuid4())
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
        key, team_id = _provision_anon(client, fake)  # noqa: RUF059
        user_id = str(uuid.uuid4())
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
        user_id = str(uuid.uuid4())
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
        user_id = str(uuid.uuid4())
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


class TestKeyRenameSupabase:
    """PATCH /v1/team/keys/{id} — rename (label), Supabase control-plane mode.

    Same session+owner guard as the enabled toggle; the label is display-only
    (rename must never affect authentication). Registry-mode rename coverage
    lives in test_hosted_api.py::TestKeysRename.
    """

    def test_rename_key_persists_label(self, client, fake, monkeypatch):
        key, team_id = _provision_anon(client, fake)
        user_id = str(uuid.uuid4())
        _patch_session_user(monkeypatch, user_id)
        _seed_owner_membership(fake, team_id, user_id)
        from tortoise.auth import lookup_hash
        rows = fake.query("api_keys", select=["id"],
                          filters=[("lookup_hash", "eq", lookup_hash(key))])
        key_id = rows[0]["id"]
        r = client.patch(
            f"/v1/team/keys/{key_id}",
            headers={"Authorization": "Bearer eyJ.sess"},
            json={"name": "prod CI"},
        )
        assert r.status_code == 200, r.text
        # rename-only PATCH echoes exactly what it applied (no enabled key)
        assert r.json() == {"key_id": key_id, "name": "prod CI"}
        # label persisted on the row
        rows2 = fake.query("api_keys", select=["name"], filters=[("id", "eq", key_id)])
        assert rows2[0]["name"] == "prod CI"
        # key still authenticates — name is display metadata only
        r2 = client.get("/v1/team", headers={"Authorization": f"Bearer {key}"})
        assert r2.status_code == 200, r2.text

    def test_rename_clears_label_with_empty_string(self, client, fake, monkeypatch):
        key, team_id = _provision_anon(client, fake)
        user_id = str(uuid.uuid4())
        _patch_session_user(monkeypatch, user_id)
        _seed_owner_membership(fake, team_id, user_id)
        from tortoise.auth import lookup_hash
        rows = fake.query("api_keys", select=["id"],
                          filters=[("lookup_hash", "eq", lookup_hash(key))])
        key_id = rows[0]["id"]
        client.patch(
            f"/v1/team/keys/{key_id}",
            headers={"Authorization": "Bearer eyJ.sess"},
            json={"name": "temp"},
        )
        r = client.patch(
            f"/v1/team/keys/{key_id}",
            headers={"Authorization": "Bearer eyJ.sess"},
            json={"name": "   "},
        )
        assert r.status_code == 200, r.text
        assert r.json()["name"] is None
        rows2 = fake.query("api_keys", select=["name"], filters=[("id", "eq", key_id)])
        assert rows2[0]["name"] is None

    def test_rename_clears_label_with_null(self, client, fake, monkeypatch):
        # The dashboard sends JSON null to clear a label — null must be
        # applied (field present), not treated as absent (P1 review fix).
        key, team_id = _provision_anon(client, fake)
        user_id = str(uuid.uuid4())
        _patch_session_user(monkeypatch, user_id)
        _seed_owner_membership(fake, team_id, user_id)
        from tortoise.auth import lookup_hash
        rows = fake.query("api_keys", select=["id"],
                          filters=[("lookup_hash", "eq", lookup_hash(key))])
        key_id = rows[0]["id"]
        client.patch(
            f"/v1/team/keys/{key_id}",
            headers={"Authorization": "Bearer eyJ.sess"},
            json={"name": "temp"},
        )
        r = client.patch(
            f"/v1/team/keys/{key_id}",
            headers={"Authorization": "Bearer eyJ.sess"},
            json={"name": None},
        )
        assert r.status_code == 200, r.text
        assert r.json()["name"] is None
        rows2 = fake.query("api_keys", select=["name"], filters=[("id", "eq", key_id)])
        assert rows2[0]["name"] is None

    def test_rename_requires_owner(self, client, fake, monkeypatch):
        key, _ = _provision_anon(client, fake)
        user_id = str(uuid.uuid4())
        _patch_session_user(monkeypatch, user_id)
        # NO membership seeded → _require_owner_admin 403s
        from tortoise.auth import lookup_hash
        rows = fake.query("api_keys", select=["id"],
                          filters=[("lookup_hash", "eq", lookup_hash(key))])
        key_id = rows[0]["id"]
        r = client.patch(
            f"/v1/team/keys/{key_id}",
            headers={"Authorization": "Bearer eyJ.sess"},
            json={"name": "x"},
        )
        assert r.status_code == 403, r.text

    def test_rename_and_toggle_in_one_patch(self, client, fake, monkeypatch):
        # The dashboard's rename used to echo `enabled`; the API supports a
        # combined body — both mutations must land in supabase mode.
        key, team_id = _provision_anon(client, fake)
        user_id = str(uuid.uuid4())
        _patch_session_user(monkeypatch, user_id)
        _seed_owner_membership(fake, team_id, user_id)
        from tortoise.auth import lookup_hash
        rows = fake.query("api_keys", select=["id"],
                          filters=[("lookup_hash", "eq", lookup_hash(key))])
        key_id = rows[0]["id"]
        r = client.patch(
            f"/v1/team/keys/{key_id}",
            headers={"Authorization": "Bearer eyJ.sess"},
            json={"enabled": False, "name": "off-ci"},
        )
        assert r.status_code == 200, r.text
        assert r.json() == {"key_id": key_id, "enabled": False, "name": "off-ci"}
        row = fake.query("api_keys", select=["enabled", "name"],
                         filters=[("id", "eq", key_id)])[0]
        assert row["enabled"] is False and row["name"] == "off-ci"
        # disabled key now rejects
        assert client.get("/v1/team",
                          headers={"Authorization": f"Bearer {key}"}).status_code == 401

    def test_rename_revoked_key_409(self, client, fake, monkeypatch):
        # The revoked guard covers rename too (P3 review fix parity).
        key, team_id = _provision_anon(client, fake)
        user_id = str(uuid.uuid4())
        _patch_session_user(monkeypatch, user_id)
        _seed_owner_membership(fake, team_id, user_id)
        from tortoise.auth import lookup_hash
        rows = fake.query("api_keys", select=["id"],
                          filters=[("lookup_hash", "eq", lookup_hash(key))])
        key_id = rows[0]["id"]
        client.delete(f"/v1/team/keys/{key_id}",
                      headers={"Authorization": f"Bearer {key}"})
        r = client.patch(
            f"/v1/team/keys/{key_id}",
            headers={"Authorization": "Bearer eyJ.sess"},
            json={"name": "x"},
        )
        assert r.status_code == 409, r.text
        row = fake.query("api_keys", select=["name"], filters=[("id", "eq", key_id)])[0]
        assert row.get("name") is None  # label unchanged

    def test_rename_clamps_to_64_chars(self, client, fake, monkeypatch):
        key, team_id = _provision_anon(client, fake)
        user_id = str(uuid.uuid4())
        _patch_session_user(monkeypatch, user_id)
        _seed_owner_membership(fake, team_id, user_id)
        from tortoise.auth import lookup_hash
        rows = fake.query("api_keys", select=["id"],
                          filters=[("lookup_hash", "eq", lookup_hash(key))])
        key_id = rows[0]["id"]
        r = client.patch(
            f"/v1/team/keys/{key_id}",
            headers={"Authorization": "Bearer eyJ.sess"},
            json={"name": "x" * 200},
        )
        assert r.status_code == 200, r.text
        assert r.json()["name"] == "x" * 64

    def test_patch_empty_body_422(self, client, fake, monkeypatch):
        # At least one of enabled/name must be present (code-review P2).
        key, team_id = _provision_anon(client, fake)
        user_id = str(uuid.uuid4())
        _patch_session_user(monkeypatch, user_id)
        _seed_owner_membership(fake, team_id, user_id)
        from tortoise.auth import lookup_hash
        rows = fake.query("api_keys", select=["id"],
                          filters=[("lookup_hash", "eq", lookup_hash(key))])
        key_id = rows[0]["id"]
        r = client.patch(
            f"/v1/team/keys/{key_id}",
            headers={"Authorization": "Bearer eyJ.sess"},
            json={},
        )
        assert r.status_code == 422, r.text

    def test_enabled_null_does_not_reenable(self, client, fake, monkeypatch):
        # An explicit null for enabled must be treated as absent — it must
        # never re-enable a disabled key (re-review P2).
        key, team_id = _provision_anon(client, fake)
        user_id = str(uuid.uuid4())
        _patch_session_user(monkeypatch, user_id)
        _seed_owner_membership(fake, team_id, user_id)
        from tortoise.auth import lookup_hash
        rows = fake.query("api_keys", select=["id"],
                          filters=[("lookup_hash", "eq", lookup_hash(key))])
        key_id = rows[0]["id"]
        # disable first
        r = client.patch(
            f"/v1/team/keys/{key_id}",
            headers={"Authorization": "Bearer eyJ.sess"},
            json={"enabled": False},
        )
        assert r.status_code == 200, r.text
        # null must not flip it back
        r = client.patch(
            f"/v1/team/keys/{key_id}",
            headers={"Authorization": "Bearer eyJ.sess"},
            json={"enabled": None},
        )
        assert r.status_code == 200, r.text
        assert r.json() == {"key_id": key_id}
        row = fake.query("api_keys", select=["enabled"],
                         filters=[("id", "eq", key_id)])[0]
        assert row["enabled"] is False  # still disabled
        assert client.get("/v1/team",
                          headers={"Authorization": f"Bearer {key}"}).status_code == 401

    def test_rename_unknown_key_404(self, client, fake, monkeypatch):
        user_id = str(uuid.uuid4())
        _patch_session_user(monkeypatch, user_id)
        _seed_owner_membership(fake, "team-404-xyz", user_id)
        r = client.patch(
            "/v1/team/keys/does-not-exist",
            headers={"Authorization": "Bearer eyJ.sess"},
            json={"name": "x"},
        )
        assert r.status_code == 404, r.text


class TestDashboardLoginGate:
    def test_key_auth_mgmt_403_when_disabled(self, client, fake, monkeypatch):
        key, team_id = _provision_anon(client, fake)
        user_id = str(uuid.uuid4())
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
        user_id = str(uuid.uuid4())
        _patch_session_user(monkeypatch, user_id)
        from tortoise.auth import lookup_hash
        sc.claim_membership(fake, lookup_hash=lookup_hash(key),
                            user_id=user_id, email="owner@example.com")
        sc.set_dashboard_key_login(fake, team_id, False)
        # session-authed (JWT) mint passes — the gate only rejects tt_ keys
        r = client.post("/v1/team/keys", headers={"Authorization": "Bearer eyJ.sess"}, json={})
        assert r.status_code == 200, r.text
        assert "dashboard_login_disabled" not in str(r.json())

    def test_session_mgmt_degrades_under_0015_drift(self, client, fake,
                                                    monkeypatch, caplog):
        """#1096: the session branch (_session_user_team) routes through the
        fail-soft seam — under 0015 drift session-authed management degrades
        (200, never 500) and logs the WARNING tripwire. Pins the contract the
        plan's surface map states (a revert to the raw combined query would
        500 here with no other test catching it)."""
        key, team_id = _provision_anon(client, fake)  # noqa: RUF059
        user_id = str(uuid.uuid4())
        _patch_session_user(monkeypatch, user_id)
        from tortoise.auth import lookup_hash
        sc.claim_membership(fake, lookup_hash=lookup_hash(key),
                            user_id=user_id, email="owner@example.com")
        fake.missing_columns = {"teams": {"suspended_at", "flagged_at"}}
        with caplog.at_level("WARNING", logger="tortoise.supabase_control"):
            r = client.post("/v1/team/keys",
                            headers={"Authorization": "Bearer eyJ.sess"},
                            json={})
        assert r.status_code == 200, r.text  # degrade, never 500
        assert any("additive" in rec.message for rec in caplog.records)

    def test_session_mgmt_degrades_under_phantom_import_columns(
            self, client, fake, monkeypatch, caplog):
        """#1832: the session branch (_session_user_team) must degrade when
        the #1230 import columns (last_import_sha256 / max_points — real
        since migration 20260817000001; missing_columns here simulates a
        schema one migration behind, i.e. DRIFT rather than the original
        pre-migration reality) are missing from the teams table.
        The fail-soft ladder drops _TEAM_ADDITIVE_IMPORT_TIER first (newest
        migration first); before the #1832 fix the tier was absent from the
        ladder, so EVERY retry still selected the (then-phantom) columns →
        PGRST204 → terminal raise → HTTP 500 on /v1/team (and /v1/team/keys,
        /v1/sessions, /v1/onboarding/state) for every session-JWT user."""
        key, team_id = _provision_anon(client, fake)
        user_id = str(uuid.uuid4())
        _patch_session_user(monkeypatch, user_id)
        from tortoise.auth import lookup_hash
        sc.claim_membership(fake, lookup_hash=lookup_hash(key),
                            user_id=user_id, email="owner@example.com")
        fake.missing_columns = {"teams": {
            "last_import_sha256", "last_import_quarantined_sha256",
            "max_points"}}
        with caplog.at_level("WARNING", logger="tortoise.supabase_control"):
            r = client.get("/v1/team",
                            headers={"Authorization": "Bearer eyJ.sess"})
        assert r.status_code == 200, r.text  # degrade, never 500
        assert r.json()["team_id"] == team_id
        assert any("additive" in rec.message for rec in caplog.records)


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
        key, team_id = _provision_anon(client, fake)  # noqa: RUF059
        user_id = str(uuid.uuid4())
        from tortoise.auth import lookup_hash
        sc.claim_membership(fake, lookup_hash=lookup_hash(key),
                            user_id=user_id, email="owner@example.com")
        r = client.post("/v1/claim/email", json={
            "api_key": key, "email": "x@example.com", "password": "password123",
        })
        assert r.status_code == 409, r.text

    def test_claim_email_rejects_weak_password(self, client, fake):
        key, team_id = _provision_anon(client, fake)  # noqa: RUF059
        r = client.post("/v1/claim/email", json={
            "api_key": key, "email": "x@example.com", "password": "123",
        })
        assert r.status_code == 400, r.text


class TestCrossTeamMintProtection:
    """#1148 gate-closing P1: a session user must NOT mint keys / restore
    backups / open billing for a team they don't belong to via ?team_id=.
    (get_current_team_session → _session_user_team membership check.)"""

    def test_session_cannot_mint_key_for_other_team(self, client, fake, monkeypatch):
        # two anon teams
        keyA, teamA = _provision_anon(client, fake)
        keyB, teamB = _provision_anon(client, fake)  # noqa: RUF059
        user_id = str(uuid.uuid4())
        _patch_session_user(monkeypatch, user_id)
        # user owns ONLY team A (claim links the real anon owner row)
        from tortoise.auth import lookup_hash
        sc.claim_membership(fake, lookup_hash=lookup_hash(keyA),
                            user_id=user_id, email="ownerA@example.com")
        # mint for team B (not a member) → 403
        r = client.post(
            f"/v1/team/keys?team_id={teamB}",
            headers={"Authorization": "Bearer eyJ.sess"}, json={},
        )
        assert r.status_code == 403, r.text
        assert "No membership in team" in str(r.json())
        # mint for own team A → works
        r2 = client.post(
            f"/v1/team/keys?team_id={teamA}",
            headers={"Authorization": "Bearer eyJ.sess"}, json={},
        )
        assert r2.status_code == 200, r2.text

    def test_session_cannot_toggle_dashboard_login_for_other_team(self, client, fake, monkeypatch):
        keyA, teamA = _provision_anon(client, fake)  # noqa: RUF059
        _, teamB = _provision_anon(client, fake)
        user_id = str(uuid.uuid4())
        _patch_session_user(monkeypatch, user_id)
        from tortoise.auth import lookup_hash
        sc.claim_membership(fake, lookup_hash=lookup_hash(keyA),
                            user_id=user_id, email="ownerA@example.com")
        # toggle dashboard-login for team B → 403 (the endpoint's own
        # _require_owner_admin would 403 anyway; this pins the membership gate)
        r = client.patch(
            f"/v1/team/dashboard-login?team_id={teamB}",
            headers={"Authorization": "Bearer eyJ.sess"},
            json={"enabled": False},
        )
        assert r.status_code == 403, r.text


class TestKeyManagementTeamPins:
    """#2230: key DELETE/PATCH honor ?team_id= in session mode for
    multi-membership users (the #2167 rule-4 carve-out — create/list pins
    shipped in #2167; revoke/rename/toggle were the gap).

    Fixture: one session user who OWNS two teams (A claimed first →
    memberships[0]=A, B second). The pre-#2230 dashboard on team B (≠ first
    membership) could not revoke B's key — the session resolved A and DELETE
    403'd "Not your API key" — and its PATCH pins were SILENTLY IGNORED (a
    wrong-team key_id mutated whenever the user owned both teams). DELETE
    resolves the pin via get_current_team_session → _session_user_team;
    PATCH (session-only, get_current_user + intrinsic key team) now enforces
    the pin in the supabase lane: membership-check first (same 403 as the
    mint/list pins), then fail closed on a key outside the pinned team with
    DELETE's exact 403 detail."""

    def _two_claimed_teams(self, client, fake, monkeypatch):
        """Provision A + B, claim both for one session user (A first so
        memberships[0]=A). Returns (teamA, teamB)."""
        keyA, teamA = _provision_anon(client, fake)
        keyB, teamB = _provision_anon(client, fake)
        user_id = str(uuid.uuid4())
        _patch_session_user(monkeypatch, user_id)
        from tortoise.auth import lookup_hash
        sc.claim_membership(fake, lookup_hash=lookup_hash(keyA),
                            user_id=user_id, email="ownerA@example.com")
        sc.claim_membership(fake, lookup_hash=lookup_hash(keyB),
                            user_id=user_id, email="ownerB@example.com")
        return teamA, teamB

    def _key_id(self, fake, team_id):
        rows = fake.query("api_keys", select=["id"],
                          filters=[("team_id", "eq", team_id)])
        assert rows, f"no api_keys row for {team_id}"
        return rows[0]["id"]

    def test_delete_non_first_team_key_no_pin_403(self, client, fake, monkeypatch):
        """The pre-fix dashboard failure, pinned as the fail-closed default:
        a PINLESS session DELETE resolves memberships[0] (team A) — team B's
        key must 403 "Not your API key", never delete. This is why the
        dashboard's revokeKey sends the pin."""
        teamA, teamB = self._two_claimed_teams(client, fake, monkeypatch)  # noqa: RUF059
        kid = self._key_id(fake, teamB)
        r = client.delete(f"/v1/team/keys/{kid}",
                          headers={"Authorization": "Bearer eyJ.sess"})
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == "Not your API key"

    def test_delete_non_first_team_key_pinned_200(self, client, fake, monkeypatch):
        """#2230 target flow: DELETE with ?team_id=<selected B> revokes B's
        key (the session lane honors the pin membership-checked)."""
        teamA, teamB = self._two_claimed_teams(client, fake, monkeypatch)  # noqa: RUF059
        kid = self._key_id(fake, teamB)
        r = client.delete(f"/v1/team/keys/{kid}?team_id={teamB}",
                          headers={"Authorization": "Bearer eyJ.sess"})
        assert r.status_code == 200, r.text
        assert r.json()["revoked"] is True
        row = fake.query("api_keys", select=["revoked_at"],
                         filters=[("id", "eq", kid)])[0]
        assert row["revoked_at"] is not None

    def test_delete_pinned_wrong_team_403(self, client, fake, monkeypatch):
        """Fail-closed: pin A while the key belongs to B (or vice versa) →
        the same 403 DELETE raises on team mismatch — never a cross-team
        revoke, even when the user owns both teams."""
        teamA, teamB = self._two_claimed_teams(client, fake, monkeypatch)
        kid = self._key_id(fake, teamB)
        r = client.delete(f"/v1/team/keys/{kid}?team_id={teamA}",
                          headers={"Authorization": "Bearer eyJ.sess"})
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == "Not your API key"

    def test_delete_pinned_unrelated_team_403(self, client, fake, monkeypatch):
        """Membership gate on the pin (mirrors _session_user_team): a user
        pinning a team they don't belong to gets the mint/list 403 — no
        existence oracle, no cross-team revoke."""
        teamA, teamB = self._two_claimed_teams(client, fake, monkeypatch)  # noqa: RUF059
        _, teamC = _provision_anon(client, fake)  # third team, NOT claimed
        kid = self._key_id(fake, teamC)
        r = client.delete(f"/v1/team/keys/{kid}?team_id={teamC}",
                          headers={"Authorization": "Bearer eyJ.sess"})
        assert r.status_code == 403, r.text
        assert "No membership in team" in str(r.json())

    def test_patch_rename_non_first_team_key_pinned_200(self, client, fake, monkeypatch):
        """#2230 target flow: rename (PATCH {name}) with ?team_id=<selected B>
        persists on B's key."""
        teamA, teamB = self._two_claimed_teams(client, fake, monkeypatch)  # noqa: RUF059
        kid = self._key_id(fake, teamB)
        r = client.patch(f"/v1/team/keys/{kid}?team_id={teamB}",
                         headers={"Authorization": "Bearer eyJ.sess"},
                         json={"name": "bravo-ci"})
        assert r.status_code == 200, r.text
        assert r.json()["name"] == "bravo-ci"
        # Persistence, not just the response echo (the handler builds the
        # response name from the request — a silently dropped _sb_set_name
        # write would otherwise false-pass).
        row = fake.query("api_keys", select=["name"],
                         filters=[("id", "eq", kid)])[0]
        assert row.get("name") == "bravo-ci"

    def test_patch_toggle_non_first_team_key_pinned_200(self, client, fake, monkeypatch):
        """#2230 target flow: enable/disable (PATCH {enabled}) with
        ?team_id=<selected B> flips B's key."""
        teamA, teamB = self._two_claimed_teams(client, fake, monkeypatch)  # noqa: RUF059
        kid = self._key_id(fake, teamB)
        r = client.patch(f"/v1/team/keys/{kid}?team_id={teamB}",
                         headers={"Authorization": "Bearer eyJ.sess"},
                         json={"enabled": False})
        assert r.status_code == 200, r.text
        row = fake.query("api_keys", select=["enabled"],
                         filters=[("id", "eq", kid)])[0]
        assert row["enabled"] is False

    def test_patch_pinned_wrong_team_403_no_write(self, client, fake, monkeypatch):
        """The NEW server behavior (the pre-#2230 hole): a PATCH pinning team
        A while the key belongs to B was silently IGNORED — the rename
        succeeded whenever the user owned both teams (acting in B's context
        mutated A's key). Now it fails closed with DELETE's exact 403 and the
        row is untouched (no partial write)."""
        teamA, teamB = self._two_claimed_teams(client, fake, monkeypatch)
        kid = self._key_id(fake, teamB)
        r = client.patch(f"/v1/team/keys/{kid}?team_id={teamA}",
                         headers={"Authorization": "Bearer eyJ.sess"},
                         json={"name": "hijacked"})
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == "Not your API key"
        row = fake.query("api_keys", select=["name"],
                         filters=[("id", "eq", kid)])[0]
        assert row.get("name") is None  # label unchanged

    def test_patch_toggle_pinned_wrong_team_403_no_write(self, client, fake, monkeypatch):
        """Same fail-closed for the enabled toggle: a wrong-team pin must not
        flip the key."""
        teamA, teamB = self._two_claimed_teams(client, fake, monkeypatch)
        kid = self._key_id(fake, teamB)
        r = client.patch(f"/v1/team/keys/{kid}?team_id={teamA}",
                         headers={"Authorization": "Bearer eyJ.sess"},
                         json={"enabled": False})
        assert r.status_code == 403, r.text
        row = fake.query("api_keys", select=["enabled"],
                         filters=[("id", "eq", kid)])[0]
        # provisioned rows have no explicit enabled column (None = enabled at
        # resolve time) — unchanged means still NOT disabled.
        assert row["enabled"] is not False

    def test_patch_pinned_unrelated_team_403(self, client, fake, monkeypatch):
        """Membership gate on the PATCH pin too — pinning a team the user
        doesn't belong to 403s with the same message as the mint/list pins."""
        teamA, teamB = self._two_claimed_teams(client, fake, monkeypatch)  # noqa: RUF059
        _, teamC = _provision_anon(client, fake)  # third team, NOT claimed
        kid = self._key_id(fake, teamC)
        r = client.patch(f"/v1/team/keys/{kid}?team_id={teamC}",
                         headers={"Authorization": "Bearer eyJ.sess"},
                         json={"name": "x"})
        assert r.status_code == 403, r.text
        assert "No membership in team" in str(r.json())

    def test_patch_non_member_pin_unknown_key_403_no_oracle(self, client, fake, monkeypatch):
        """#2230 (code-review P2): the membership gate precedes the key
        lookup — a non-member pin 403s even when the key_id exists NOWHERE
        (no cross-team key-existence oracle). Pre-fix the api_key_by_id 404
        fired first, so a guessed key_id on an unclaimed team answered 404
        vs 403 — the exact divergence DELETE's DI-time gate never had."""
        teamA, teamB = self._two_claimed_teams(client, fake, monkeypatch)  # noqa: RUF059
        _, teamC = _provision_anon(client, fake)  # third team, NOT claimed
        r = client.patch(f"/v1/team/keys/{uuid.uuid4()}?team_id={teamC}",
                         headers={"Authorization": "Bearer eyJ.sess"},
                         json={"name": "x"})
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == "No membership in team"

    def test_patch_no_pin_still_works_backwards_compatible(self, client, fake, monkeypatch):
        """The pin is additive: a PINLESS multi-membership PATCH keeps today's
        intrinsic-team behavior (200 when the user is owner/admin of the
        key's team) — existing API consumers are unaffected."""
        teamA, teamB = self._two_claimed_teams(client, fake, monkeypatch)  # noqa: RUF059
        kid = self._key_id(fake, teamB)
        r = client.patch(f"/v1/team/keys/{kid}",
                         headers={"Authorization": "Bearer eyJ.sess"},
                         json={"name": "pinless"})
        assert r.status_code == 200, r.text


class TestBackupsSessionAuth:
    """#1831 P2-4: GET /backups rides the session dual-auth (#1828).

    loadBackups calls api('/backups') with NO key when a recoverable mint
    failure (#1830) left apiKey empty — a bare get_current_team dependency
    would 401 and the Backups card silently vanished for Pro users. The
    ungated dual-auth accepts session JWT OR tt_ key; only team_id is read.
    """

    def test_backups_list_with_session_jwt(self, client, fake, monkeypatch):
        key, _team_id = _provision_anon(client, fake)
        user_id = str(uuid.uuid4())
        _patch_session_user(monkeypatch, user_id)
        # claim so the session user resolves a team via memberships
        from tortoise.auth import lookup_hash
        sc.claim_membership(fake, lookup_hash=lookup_hash(key),
                            user_id=user_id, email="owner@example.com")
        # in-memory backup store — list must not touch R2
        from tortoise.hosted_backup import MemoryStorage
        monkeypatch.setattr(ha_mod, "_backup_storage", lambda: MemoryStorage())
        r = client.get("/backups", headers={"Authorization": "Bearer eyJ.sess"})
        assert r.status_code == 200, r.text
        assert r.json() == {"backups": []}

    def test_backups_list_with_key_still_works(self, client, fake, monkeypatch):
        key, _team_id = _provision_anon(client, fake)
        from tortoise.hosted_backup import MemoryStorage
        monkeypatch.setattr(ha_mod, "_backup_storage", lambda: MemoryStorage())
        r = client.get("/backups", headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 200, r.text
        assert r.json() == {"backups": []}

    def test_backups_list_no_auth_401(self, client, fake, monkeypatch):
        from tortoise.hosted_backup import MemoryStorage
        monkeypatch.setattr(ha_mod, "_backup_storage", lambda: MemoryStorage())
        r = client.get("/backups")
        assert r.status_code == 401, r.text
