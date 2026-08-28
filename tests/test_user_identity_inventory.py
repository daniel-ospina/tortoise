"""GET /v1/user/identity — login-method inventory endpoint tests (#1765).

Server-authority inventory: user_identity_inventory RPC (seam) + GoTrue
admin user (email / last_sign_in_at) + linking_available. Session-only.
Registry mode → {"unsupported": true}. 502 on seam failure.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: I001
from fastapi.testclient import TestClient

import tortoise.hosted_api as ha_mod
import tortoise.session_auth as sa
import tortoise.supabase_control as sc
from tortoise.hosted_api import app
from tests.fake_control_plane import FakeControlPlane
from datetime import UTC


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://identitytest.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc-identity-test")
    monkeypatch.setenv("TORTOISE_LINK_INTENT_SECRET", "test-secret")
    monkeypatch.setenv("RATE_LIMIT_DISABLED", "1")
    fake = FakeControlPlane()
    monkeypatch.setattr(sc, "get_control_plane", lambda: fake)
    yield fake
    ha_mod._id_rate_buckets.clear()


@pytest.fixture
def fake(_env):
    return _env


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _user(user_id: str = "u-1", **kw):
    return {"user_id": user_id, "email": "u@example.com",
            "app_metadata": {"providers": ["github"]}, **kw}


def _admin(user_id: str = "u-1", *, email="u@example.com", confirmed=True,
           last_sign_in=None, identities=None):
    if last_sign_in is None:
        from datetime import datetime, timedelta
        last_sign_in = (datetime.now(UTC) - timedelta(seconds=60)).isoformat()
    return {"id": user_id, "email": email,
            "email_confirmed_at": "2026-08-01T00:00:00+00:00" if confirmed else None,
            "last_sign_in_at": last_sign_in,
            "identities": identities or []}


def test_requires_session(client, monkeypatch):
    async def _raise_401(request):
        raise __import__("fastapi").HTTPException(401, "unauthorized")
    monkeypatch.setattr(sa, "verify_session_jwt", _raise_401)
    r = client.get("/v1/user/identity")
    assert r.status_code == 401


def test_unsupported_in_registry_mode(client, monkeypatch):
    monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "registry")
    async def _fake_verify(request):
        return _user()
    monkeypatch.setattr(sa, "verify_session_jwt", _fake_verify)
    r = client.get("/v1/user/identity")
    assert r.status_code == 200
    assert r.json() == {"unsupported": True}


def test_inventory_shapes(client, monkeypatch, fake):
    fake.auth_users = [
        {"id": "u-oauth", "email": "o@x.com", "email_confirmed_at": None,
         "encrypted_password": ""},
        {"id": "u-conf", "email": "c@x.com", "email_confirmed_at": "2026-08-01T00:00:00Z",
         "encrypted_password": None},
    ]
    fake.auth_identities = [{"id": "i1", "user_id": "u-oauth", "provider": "github",
                             "provider_id": "gh1"}]
    async def _fake_verify(request):
        return _user("u-oauth")
    monkeypatch.setattr(sa, "verify_session_jwt", _fake_verify)
    monkeypatch.setattr(ha_mod, "_identity_admin_user",
                        lambda uid: _admin("u-oauth", email="o@x.com", confirmed=False))
    r = client.get("/v1/user/identity")
    assert r.status_code == 200
    body = r.json()
    assert body["login_methods"] == 1          # github only ('' pwd, unconfirmed email)
    assert body["has_password"] is False
    assert body["linking_available"] is False  # fail-closed default
    assert body["reauth_required"] is False    # last_sign_in fresh


def test_linking_available_when_env_on(client, monkeypatch):
    monkeypatch.setenv("TORTOISE_MANUAL_LINKING_ENABLED", "1")
    async def _fake_verify(request):
        return _user()
    monkeypatch.setattr(sa, "verify_session_jwt", _fake_verify)
    monkeypatch.setattr(ha_mod, "_identity_admin_user", lambda uid: _admin())
    r = client.get("/v1/user/identity")
    assert r.json()["linking_available"] is True


def test_reauth_required_when_stale(client, monkeypatch):
    from datetime import datetime, timedelta
    stale = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    async def _fake_verify(request):
        return _user()
    monkeypatch.setattr(sa, "verify_session_jwt", _fake_verify)
    monkeypatch.setattr(ha_mod, "_identity_admin_user",
                        lambda uid: _admin(last_sign_in=stale))
    r = client.get("/v1/user/identity")
    assert r.json()["reauth_required"] is True


def test_502_on_seam_failure(client, monkeypatch, fake):
    async def _fake_verify(request):
        return _user()
    monkeypatch.setattr(sa, "verify_session_jwt", _fake_verify)
    monkeypatch.setattr(sc, "user_identity_inventory", lambda cp, uid: (_ for _ in ()).throw(RuntimeError("boom")))
    r = client.get("/v1/user/identity")
    assert r.status_code == 502


def test_keys_tier_excludes_agent_principals_via_api(client, monkeypatch, fake):
    """C10 exclusions at the endpoint: st_/anon- keys never count toward the
    user's keys tier."""
    import tortoise.session_auth as sa
    async def _fake_verify(request):
        return _user("u-keys")
    monkeypatch.setattr(sa, "verify_session_jwt", _fake_verify)
    monkeypatch.setattr(ha_mod, "_identity_admin_user", lambda uid: _admin("u-keys"))
    fake.auth_users = [{"id": "u-keys", "email": "k@x.com",
                        "email_confirmed_at": "2026-08-01T00:00:00Z",
                        "encrypted_password": None}]
    fake.seed("api_keys", [
        {"id": "k1", "team_id": "t1", "created_by": "u-keys", "revoked_at": None, "enabled": True},
        {"id": "k2", "team_id": "t2", "created_by": "st_agent", "revoked_at": None, "enabled": True},
        {"id": "k3", "team_id": "t3", "created_by": "anon-x", "revoked_at": None, "enabled": True},
    ])
    r = client.get("/v1/user/identity")
    assert r.json()["keys_tier"] == 1  # only the user-minted key


def test_inventory_contract_includes_last_sign_in(client, monkeypatch):
    """last_sign_in_at must be present (drives the client ReauthDialog)."""
    import tortoise.session_auth as sa
    async def _fake_verify(request):
        return _user()
    monkeypatch.setattr(sa, "verify_session_jwt", _fake_verify)
    monkeypatch.setattr(ha_mod, "_identity_admin_user", lambda uid: _admin())
    r = client.get("/v1/user/identity")
    assert "last_sign_in_at" in r.json()
