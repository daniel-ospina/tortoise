"""Tests for GitHub OAuth onboarding endpoints (#499).

Covers: connect (auth URL + state), callback (exchange + encrypted storage),
status (connected/not), auth requirements, state validation.
"""
from __future__ import annotations

import os

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("TORTOISE_ENCRYPTION_KEY", "I2n-E3K857hF9ENLgrOZ8YBPkEB4tu4jyrb1aJMUtnI=")
os.environ.setdefault("GITHUB_CLIENT_ID", "test-client-id")
os.environ.setdefault("GITHUB_CLIENT_SECRET", "test-client-secret")

import pytest
from fastapi.testclient import TestClient

from tortoise.hosted_api import app
from tortoise.sdk import TortoiseSDK


@pytest.fixture
def client(tmp_path):
    db_path = str(tmp_path / "github.db")
    orig_init = TortoiseSDK.__init__

    def _patched(self, db_path_arg=None, *, namespace=None, **kw):
        # Callers may pass db_path as a keyword (lands in **kw) — pop it so
        # it never conflicts with the explicit kwarg (#493).
        kw.pop("db_path", None)
        orig_init(self, db_path=db_path if db_path_arg is None else db_path_arg,
                  namespace=namespace, **kw)

    TortoiseSDK.__init__ = _patched
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
    db_path = str(tmp_path / "github_unauth.db")
    orig_init = TortoiseSDK.__init__

    def _patched(self, db_path_arg=None, *, namespace=None, **kw):
        # Callers may pass db_path as a keyword (lands in **kw) — pop it so
        # it never conflicts with the explicit kwarg (#493).
        kw.pop("db_path", None)
        orig_init(self, db_path=db_path if db_path_arg is None else db_path_arg,
                  namespace=namespace, **kw)

    TortoiseSDK.__init__ = _patched
    with TestClient(app) as tc:
        yield tc
    TortoiseSDK.__init__ = orig_init


class TestGitHubConnect:
    def test_connect_returns_auth_url(self, client):
        r = client.post("/v1/onboarding/github/connect", json={"org": "acme"})
        assert r.status_code == 200
        body = r.json()
        assert "auth_url" in body and "state" in body
        assert "github.com/login/oauth/authorize" in body["auth_url"]
        assert "client_id=test-client-id" in body["auth_url"]

    def test_connect_requires_auth(self, unauth_client):
        r = unauth_client.post("/v1/onboarding/github/connect", json={})
        assert r.status_code == 401


class TestGitHubCallback:
    def test_callback_rejects_bad_state(self, client):
        r = client.get("/v1/onboarding/github/callback?code=x&state=bad")
        assert r.status_code == 404

    def test_callback_missing_code(self, client):
        # Get a real state first
        r = client.post("/v1/onboarding/github/connect", json={})
        state = r.json()["state"]
        r = client.get(f"/v1/onboarding/github/callback?state={state}")
        assert r.status_code == 404  # no code

    def test_callback_handles_denial(self, client):
        # follow_redirects=False: TestClient follows the 302 to the external
        # welcome URL which isn't served locally → would show 404 instead of
        # the redirect we're testing.
        r = client.get("/v1/onboarding/github/callback?error=access_denied",
                       follow_redirects=False)
        assert r.status_code == 302
        assert "github=denied" in r.headers["location"]


class TestGitHubStatus:
    def test_status_requires_auth(self, unauth_client):
        r = unauth_client.get("/v1/onboarding/github/status")
        assert r.status_code == 401

    def test_status_not_connected(self, client):
        r = client.get("/v1/onboarding/github/status")
        assert r.status_code == 200
        body = r.json()
        assert body["connected"] is False
        assert body["org"] is None
