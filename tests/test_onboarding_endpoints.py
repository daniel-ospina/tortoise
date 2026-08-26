"""Tests for hosted onboarding endpoints (#498).

Covers: self-service register, public demo graph, onboarding state
(GET/PATCH), session-recording toggle, hosted team creation.

Uses embedded FalkorDBLite + registry SDK (mirrors test_hosted_api.py).
"""
from __future__ import annotations

import os

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

import pytest  # noqa: I001
from fastapi.testclient import TestClient

from tortoise.hosted_api import app, _make_sdk  # noqa: F401
from tortoise.sdk import TortoiseSDK


@pytest.fixture
def client(tmp_path):
    """TestClient with a temp embedded DB + registry."""
    db_path = str(tmp_path / "onboarding.db")  # noqa: F841
    # Patch TortoiseSDK to use the temp DB (mirrors test_hosted_api.py)
    orig_init = TortoiseSDK.__init__

    def _patched(self, db_path_arg=None, *, namespace=None, db_path=None, **kw):
        # Isolate EVERY SDK construction (registry included) to the fixture's
        # temp DB — _make_sdk passes db_path= explicitly, and the registry
        # must not fall back to the shared temp default (that leaks state
        # across tests: "Team already exists").
        orig_init(self, db_path=db_path if db_path_arg is None else db_path_arg,
                  namespace=namespace, **kw)

    TortoiseSDK.__init__ = _patched
    # #1497: break the _make_sdk embedded fallback anchor — module-level
    # _FALLBACK_KEEPALIVE survives tests, so an anchored SDK bound to a prior
    # test's temp DB leaks state / dies socket. Re-bind to THIS temp DB.
    from tortoise.hosted_api import _FALLBACK_KEEPALIVE
    _FALLBACK_KEEPALIVE.clear()
    from tortoise.hosted_api import get_current_team
    app.dependency_overrides[get_current_team] = lambda: {
        "team_id": "test-team-1", "tier": "free", "key_id": "k1",
        "max_users": 1, "max_graphs": 1, "max_teams": 1,
        # #1748: the onboarding sub-team is provisioned on the USER path —
        # the session user becomes the owner member (get_current_team_session
        # attaches session_user_id for session JWT auth; tests seed it here).
        "session_user_id": "user-1",
    }
    with TestClient(app) as tc:
        yield tc
    app.dependency_overrides.clear()
    TortoiseSDK.__init__ = orig_init


@pytest.fixture
def unauth_client(tmp_path):
    """TestClient WITHOUT the auth override — real 401s."""
    db_path = str(tmp_path / "unauth.db")  # noqa: F841
    orig_init = TortoiseSDK.__init__

    def _patched(self, db_path_arg=None, *, namespace=None, db_path=None, **kw):
        # Isolate EVERY SDK construction (registry included) to the fixture's
        # temp DB — _make_sdk passes db_path= explicitly, and the registry
        # must not fall back to the shared temp default (that leaks state
        # across tests: "Team already exists").
        orig_init(self, db_path=db_path if db_path_arg is None else db_path_arg,
                  namespace=namespace, **kw)

    TortoiseSDK.__init__ = _patched
    with TestClient(app) as tc:
        yield tc
    TortoiseSDK.__init__ = orig_init


# ── Onboarding state ────────────────────────────────────────────

class TestOnboardingState:
    def test_get_state_requires_auth(self, unauth_client):
        r = unauth_client.get("/v1/onboarding/state")
        assert r.status_code == 401

    def test_get_state_default(self, client):
        r = client.get("/v1/onboarding/state")
        assert r.status_code == 200
        body = r.json()
        assert "onboarding" in body

    def test_patch_state_merge(self, client):
        r = client.patch("/v1/onboarding/state", json={"demo_created": True})
        assert r.status_code == 200
        body = r.json()
        assert body["onboarding"]["demo_created"] is True

    def test_patch_state_invalid_key(self, client):
        r = client.patch("/v1/onboarding/state", json={"not_a_field": 1})
        # Unknown keys either rejected (400) or ignored — but never 500
        assert r.status_code < 500


# ── Public demo graph ───────────────────────────────────────────

class TestPublicDemo:
    def test_demo_requires_auth(self, unauth_client):
        r = unauth_client.post("/v1/demo")
        assert r.status_code == 401

    def test_demo_creates_points(self, client):
        r = client.post("/v1/demo")
        assert r.status_code == 200
        body = r.json()
        assert "points_created" in body or "created" in body

    def test_demo_idempotent(self, client):
        r1 = client.post("/v1/demo")
        r2 = client.post("/v1/demo")
        assert r1.status_code == 200
        assert r2.status_code == 200  # no crash on re-run


# ── Session recording toggle ────────────────────────────────────

class TestSessionRecording:
    def test_enable_recording(self, client):
        r = client.post("/v1/onboarding/session-recording", json={"enabled": True})
        assert r.status_code == 200
        assert r.json()["onboarding"]["session_recording"] is True

    def test_disable_recording(self, client):
        r = client.post("/v1/onboarding/session-recording", json={"enabled": False})
        assert r.status_code == 200
        assert r.json()["onboarding"]["session_recording"] is False


# ── Hosted team creation ────────────────────────────────────────

class TestOnboardingTeam:
    def test_create_team(self, client):
        r = client.post("/v1/onboarding/team", json={"name": "acme"})
        assert r.status_code == 200
        body = r.json()
        assert body.get("team_id") or body.get("id")
        assert body.get("name") == "acme"
        assert "key" not in body  # #1716: the response never carries a key

    def test_create_team_keyless_registry(self, client):
        """#1716 registry-lane parity: the sub-team is provisioned KEYLESS —
        no tt_ mint, no api_key hash on the Team node, no APIKey node (a
        minted key whose plaintext is never returned is an unrecoverable
        dead credential; the sub-team stays keyless until a session-key
        mint)."""
        r = client.post("/v1/onboarding/team", json={"name": "keyless"})
        assert r.status_code == 200
        body = r.json()
        assert "key" not in body
        # the registry-lane SDK is the CANONICAL control plane
        # (namespace="registry" → registry_control_plane — #1748: the old
        # namespace=team_id wrote a {team_id}_control_plane graph that no
        # other registry path reads, orphaning the sub-team) — query the
        # same graph the endpoint wrote to.
        reg = _make_sdk(namespace="registry")._get_registry()
        rows = reg.query(
            "MATCH (t:Team {name:'keyless'}) RETURN t.id, t.api_key",
        ).result_set
        assert len(rows) == 1
        tid, team_key_hash = rows[0]
        assert team_key_hash is None  # no dead key hash on the Team node
        n_keys = reg.query(
            "MATCH (k:APIKey {team_id:$tid}) RETURN count(k)",
            params={"tid": tid},
        ).result_set[0][0]
        assert n_keys == 0  # no APIKey node minted for the sub-team

    def test_team_name_validation(self, client):
        r = client.post("/v1/onboarding/team", json={"name": ""})
        assert r.status_code < 500

    def test_create_team_then_session_key_mint_registry(self, client):
        """#1748 registry-lane journey (the real #1716 escape hatch):
        create sub-team (keyless, but the session user is a REAL owner
        member — no throwaway identity, no hand-inserted membership) →
        session-key mint resolves the membership → the minted key resolves
        on REST → the sub-team is listable and deletable by its owner."""
        from tortoise.hosted_api import get_current_user
        r = client.post("/v1/onboarding/team", json={"name": "journey"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert "key" not in body  # #1716: keyless — no tt_ mint at onboarding
        sub_team_id = body["team_id"]
        # the session user is the owner member (registry Membership node in
        # the CANONICAL control plane — registry_control_plane)
        reg = _make_sdk(namespace="registry")._get_registry()
        rows = reg.query(
            "MATCH (m:Membership {team_id:$tid}) "
            "RETURN m.user_id, m.role, m.status",
            params={"tid": sub_team_id},
        ).result_set
        assert rows == [["user-1", "owner", "active"]], rows
        # no APIKey node minted for the keyless sub-team
        n_keys = reg.query(
            "MATCH (k:APIKey {team_id:$tid}) RETURN count(k)",
            params={"tid": sub_team_id},
        ).result_set[0][0]
        assert n_keys == 0
        # session-key mint (registry lane) — resolves the owner membership.
        # Explicit team_id: the test registry is shared across tests in a
        # session (earlier tests' sub-teams leave user-1 memberships), so
        # the mint must disambiguate — exactly the production multi-team
        # shape (a user with several memberships passes team_id).
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": "user-1", "email": "user-1@example.com"}
        r2 = client.post("/v1/session/key", json={
            "purpose": "bootstrap", "team_id": sub_team_id})
        assert r2.status_code == 200, r2.text
        key = r2.json()["key"]
        assert key.startswith("tt_")
        assert r2.json()["team_id"] == sub_team_id
        # the minted key resolves on REST (registry APIKey node)
        app.dependency_overrides.clear()
        r3 = client.get("/v1/team",
                        headers={"Authorization": f"Bearer {key}"})
        assert r3.status_code == 200, r3.text
        assert r3.json()["team_id"] == sub_team_id
        # listable by the owner (GET /v1/teams)
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": "user-1", "email": "user-1@example.com"}
        r4 = client.get("/v1/teams")
        assert r4.status_code == 200, r4.text
        assert any(t["team_id"] == sub_team_id for t in r4.json())
        # deletable by the owner (DELETE /v1/teams/{id})
        r5 = client.delete(f"/v1/teams/{sub_team_id}")
        assert r5.status_code in (200, 202), r5.text

    def test_create_team_requires_session_user_registry(self, client):
        """#1748: no session user on the team context → 403 (never an
        owner-less orphan sub-team)."""
        from tortoise.hosted_api import get_current_team
        app.dependency_overrides[get_current_team] = lambda: {
            "team_id": "test-team-1", "tier": "free", "key_id": "k1",
            "max_users": 1, "max_graphs": 1, "max_teams": 1,
            "session_user_id": None,
        }
        r = client.post("/v1/onboarding/team", json={"name": "orphan"})
        assert r.status_code == 403, r.text
        reg = _make_sdk(namespace="registry")._get_registry()
        assert reg.query(
            "MATCH (t:Team {name:'orphan'}) RETURN count(t)",
        ).result_set[0][0] == 0


# ── Register (self-service provisioning) ────────────────────────

class TestRegister:
    def test_register_invalid_email(self, client):
        r = client.post("/v1/register", json={"email": "not-an-email", "password": "x"})
        assert r.status_code < 500  # 400/422 validation, not crash

    def test_register_missing_fields(self, client):
        r = client.post("/v1/register", json={})
        assert r.status_code < 500
