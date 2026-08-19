"""Tests for hosted onboarding endpoints (#498).

Covers: self-service register, public demo graph, onboarding state
(GET/PATCH), session-recording toggle, hosted team creation.

Uses embedded FalkorDBLite + registry SDK (mirrors test_hosted_api.py).
"""
from __future__ import annotations

import os

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

import pytest
from fastapi.testclient import TestClient

from tortoise.hosted_api import app, _make_sdk
from tortoise.sdk import TortoiseSDK


@pytest.fixture
def client(tmp_path):
    """TestClient with a temp embedded DB + registry."""
    db_path = str(tmp_path / "onboarding.db")
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
    }
    with TestClient(app) as tc:
        yield tc
    app.dependency_overrides.clear()
    TortoiseSDK.__init__ = orig_init


@pytest.fixture
def unauth_client(tmp_path):
    """TestClient WITHOUT the auth override — real 401s."""
    db_path = str(tmp_path / "unauth.db")
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

    def test_team_name_validation(self, client):
        r = client.post("/v1/onboarding/team", json={"name": ""})
        assert r.status_code < 500


# ── Register (self-service provisioning) ────────────────────────

class TestRegister:
    def test_register_invalid_email(self, client):
        r = client.post("/v1/register", json={"email": "not-an-email", "password": "x"})
        assert r.status_code < 500  # 400/422 validation, not crash

    def test_register_missing_fields(self, client):
        r = client.post("/v1/register", json={})
        assert r.status_code < 500
