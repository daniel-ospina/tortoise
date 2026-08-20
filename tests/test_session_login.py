"""POST /v1/session/login — #1511 API-key → session exchange endpoint tests.

Mirrors test_claim_endpoints.py: Supabase-mode env + FakeControlPlane +
monkeypatched httpx for the GoTrue admin calls. The exchange resolves the
key (resolve_api_key parity), enforces the dashboard-login gate (forced),
branches on the key's created_by shape, and mints the CREATOR's session
(no member-key escalation) via GoTrue generate_link + /verify.
"""
from __future__ import annotations

import os
import sys
import time
import uuid

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
import pytest
from fastapi.testclient import TestClient

import tortoise.hosted_api as ha_mod
import tortoise.supabase_control as sc
from tests.fake_control_plane import FakeControlPlane
from tortoise.auth import lookup_hash
from tortoise.hosted_api import app

_SUPABASE_URL = "https://sessionlogin.supabase.co"
_OWNER = "e7e0794e-267d-427c-a3a2-7d01cfd5611e"
_MEMBER = "fa3d811a-e4c8-4cf4-a001-aa2959472d85"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", _SUPABASE_URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc-session-login")
    monkeypatch.setenv("RATE_LIMIT_DISABLED", "1")
    ha_mod._SESSION_BUCKETS.clear()
    fake = FakeControlPlane()
    monkeypatch.setattr(sc, "get_control_plane", lambda: fake)
    yield fake
    ha_mod._SESSION_BUCKETS.clear()


@pytest.fixture
def fake(_env):
    return _env


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _seed_team(fake, *, team_id="t1", created_by=_OWNER,
               user_ids=None, status="active", email="owner@example.com",
               team_extra=None):
    """Seed a claimed team + an api_keys row created by `created_by`."""
    team_row = {"id": team_id, "name": "Team", "tier": "free",
                "max_users": 5, "max_graphs": 5, "graph_size_cap": 10000,
                "ops_allowance": 1000, "email": email}
    if team_extra:
        team_row.update(team_extra)
    fake.seed("teams", [team_row])
    mems = [{"team_id": team_id, "user_id": uid, "role": "owner" if uid == _OWNER else "member",
             "status": status}
            for uid in (user_ids or [_OWNER, _MEMBER])]
    fake.seed("team_memberships", mems)
    return team_id


def _mint_key(fake, team_id="t1", created_by=_OWNER):
    plain = f"tt_{uuid.uuid4().hex}"
    fake.seed("api_keys", [{
        "id": f"k-{uuid.uuid4().hex[:8]}",
        "team_id": team_id,
        "lookup_hash": lookup_hash(plain),
        "key_prefix": plain[:10],
        "created_via": "provisioned",
        "created_by": created_by,
        "enabled": True,
        "revoked_at": None,
        "expires_at": None,
    }])
    return plain


def _patch_gotrue(monkeypatch, *, user_email="owner@example.com",
                  session_user_id=_OWNER):
    """Patch httpx GET (admin user) + POST (generate_link + verify)."""
    def _get(url, **kwargs):
        assert url.endswith(f"/auth/v1/admin/users/{_OWNER}") or \
            url.endswith(f"/auth/v1/admin/users/{_MEMBER}"), f"url: {url}"
        return httpx.Response(200, json={"id": session_user_id, "email": user_email},
                              request=httpx.Request("GET", url))

    def _post(url, **kwargs):
        if url.endswith("/auth/v1/admin/generate_link"):
            return httpx.Response(200, json={"hashed_token": "ht-1"},
                                  request=httpx.Request("POST", url))
        if url.endswith("/auth/v1/verify"):
            return httpx.Response(200, json={
                "access_token": "at", "refresh_token": "rt", "expires_in": 3600,
                "expires_at": 9999999999, "token_type": "bearer",
                "user": {"id": session_user_id, "email": user_email}},
                request=httpx.Request("POST", url))
        raise AssertionError(f"unexpected POST {url}")

    monkeypatch.setattr(httpx, "get", _get)
    monkeypatch.setattr(httpx, "post", _post)


def _exchange(client, key: str):
    return client.post("/v1/session/login", json={"api_key": key})


class TestSessionLogin:
    def test_member_minted_key_mints_member_session_no_escalation(
            self, client, fake, monkeypatch):
        """A member-minted key (created_by = member UUID) → 200 with
        session.user.id == the key's creator — no escalation."""
        _seed_team(fake)
        key = _mint_key(fake, created_by=_MEMBER)
        _patch_gotrue(monkeypatch, session_user_id=_MEMBER)
        r = _exchange(client, key)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["access_token"] == "at"
        assert body["user"]["id"] == _MEMBER

    def test_dashboard_minted_key_succeeds(self, client, fake, monkeypatch):
        """A dashboard-minted key (created_by = session user UUID after the
        create_api_key fix) → 200, session for the creator."""
        _seed_team(fake)
        key = _mint_key(fake, created_by=_OWNER)
        _patch_gotrue(monkeypatch)
        r = _exchange(client, key)
        assert r.status_code == 200, r.text
        assert r.json()["user"]["id"] == _OWNER

    def test_verify_response_without_expires_at_gets_injected(self, client, fake, monkeypatch):
        """#1511: real GoTrue /verify returns no expires_at (only expires_in) —
        the server must inject it (epoch seconds) so the client's storeSession
        strict-validity check accepts the cookie session. Mock-parity pin."""
        _seed_team(fake)
        key = _mint_key(fake, created_by=_OWNER)

        def _get(url, **kwargs):
            return httpx.Response(200, json={"id": _OWNER, "email": "owner@example.com"},
                                  request=httpx.Request("GET", url))

        def _post(url, **kwargs):
            if url.endswith("/auth/v1/admin/generate_link"):
                return httpx.Response(200, json={"hashed_token": "ht-1"},
                                      request=httpx.Request("POST", url))
            if url.endswith("/auth/v1/verify"):
                # NO expires_at — the real GoTrue AccessTokenResponse shape.
                return httpx.Response(200, json={
                    "access_token": "at", "refresh_token": "rt",
                    "expires_in": 3600, "token_type": "bearer",
                    "user": {"id": _OWNER, "email": "owner@example.com"}},
                    request=httpx.Request("POST", url))
            raise AssertionError(f"unexpected POST {url}")

        monkeypatch.setattr(httpx, "get", _get)
        monkeypatch.setattr(httpx, "post", _post)
        r = _exchange(client, key)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["expires_in"] == 3600
        assert body.get("expires_at")
        now = int(time.time())
        assert abs(body["expires_at"] - (now + 3600)) < 30

    def test_invalid_key_401(self, client, fake):
        r = _exchange(client, "tt_does-not-exist")
        assert r.status_code == 401

    def test_suspended_team_403(self, client, fake):
        _seed_team(fake, team_extra={"suspended_at": "2026-01-01"})
        key = _mint_key(fake)
        r = _exchange(client, key)
        assert r.status_code == 403

    def test_dashboard_key_login_disabled_403(self, client, fake, monkeypatch):
        _seed_team(fake)
        key = _mint_key(fake)
        # Force the flag off on the resolved team (resolve_api_key reads
        # dashboard_key_login from the teams row — seed it off).
        monkeypatch.setattr(sc, "get_control_plane", lambda: FakeControlPlane(
            tables={"teams": [{"id": "t1", "name": "T", "tier": "free", "max_users": 5,
                               "max_graphs": 5, "graph_size_cap": 10000, "ops_allowance": 1000,
                               "email": "x@y.com", "dashboard_key_login": False}],
                    "team_memberships": [{"team_id": "t1", "user_id": _OWNER,
                                          "role": "owner", "status": "active"}],
                    "api_keys": [{"id": "k1", "team_id": "t1", "lookup_hash": lookup_hash(key),
                                  "created_by": _OWNER, "enabled": True,
                                  "revoked_at": None, "expires_at": None}]}))
        r = _exchange(client, key)
        assert r.status_code == 403
        assert r.json()["detail"]["error_code"] == "dashboard_login_disabled"

    def test_api_created_by_key_403_key_not_user_minted(self, client, fake):
        _seed_team(fake)
        key = _mint_key(fake, created_by="api")
        r = _exchange(client, key)
        assert r.status_code == 403
        assert r.json()["detail"]["error_code"] == "KEY_NOT_USER_MINTED"

    def test_null_created_by_key_403_key_not_user_minted(self, client, fake):
        _seed_team(fake)
        key = _mint_key(fake, created_by=None)
        r = _exchange(client, key)
        assert r.status_code == 403
        assert r.json()["detail"]["error_code"] == "KEY_NOT_USER_MINTED"

    def test_identity_key_on_anon_team_403_anon_team_no_owner(self, client, fake):
        fake.seed("teams", [{"id": "t-anon", "name": "T", "tier": "free",
                             "max_users": 5, "max_graphs": 5, "graph_size_cap": 10000,
                             "ops_allowance": 1000, "email": None}])
        fake.seed("team_memberships", [{"team_id": "t-anon", "identity": "anon-abc",
                                        "role": "owner", "status": "active"}])
        key = _mint_key(fake, team_id="t-anon", created_by="anon-abc")
        r = _exchange(client, key)
        assert r.status_code == 403
        assert r.json()["detail"]["error_code"] == "ANON_TEAM_NO_OWNER"

    def test_identity_key_on_claimed_team_403_key_not_user_minted(self, client, fake):
        _seed_team(fake)
        key = _mint_key(fake, created_by="reg-xyz")
        r = _exchange(client, key)
        assert r.status_code == 403
        assert r.json()["detail"]["error_code"] == "KEY_NOT_USER_MINTED"

    def test_uuid_creator_non_member_on_anon_team_403_key_not_user_minted(
            self, client, fake):
        """Pinned order (VGATE P3): the claim funnel is for IDENTITY-shaped
        creators ONLY — a UUID creator who is no longer an active member (a
        team that lost its claimed owner) is KEY_NOT_USER_MINTED, never
        ANON_TEAM_NO_OWNER."""
        fake.seed("teams", [{"id": "t-anon2", "name": "T", "tier": "free",
                             "max_users": 5, "max_graphs": 5, "graph_size_cap": 10000,
                             "ops_allowance": 1000, "email": None}])
        fake.seed("team_memberships", [{"team_id": "t-anon2", "identity": "anon-xyz",
                                        "role": "owner", "status": "active"}])
        key = _mint_key(fake, team_id="t-anon2", created_by=_MEMBER)  # UUID, not a member
        r = _exchange(client, key)
        assert r.status_code == 403
        assert r.json()["detail"]["error_code"] == "KEY_NOT_USER_MINTED"

    def test_removed_creator_403_key_not_user_minted(self, client, fake):
        _seed_team(fake, user_ids=[_OWNER])  # member NOT on the team
        key = _mint_key(fake, created_by=_MEMBER)
        r = _exchange(client, key)
        assert r.status_code == 403
        assert r.json()["detail"]["error_code"] == "KEY_NOT_USER_MINTED"

    def test_non_dict_json_body_401_not_500(self, client, fake, monkeypatch):
        """Security review r2 (P3): a non-dict JSON body ([1,2,3], "abc")
        must not raise AttributeError → 500 before the rate-limit check."""
        r = client.post("/v1/session/login", json=[1, 2, 3])
        assert r.status_code == 401, r.text  # invalid key path (no crash)
        r2 = client.post("/v1/session/login", content='"abc"',
                         headers={"Content-Type": "application/json"})
        assert r2.status_code == 401, r2.text
        r3 = client.post("/v1/session/login", json={"api_key": 12345})
        assert r3.status_code == 401, r3.text  # non-string key value (P2-1)

    def test_gotrue_transport_error_502(self, client, fake, monkeypatch):
        """Code-review P1: an httpx transport exception from the GoTrue admin
        calls must map to 502 "Auth service unavailable", never a raw 500
        (the client would otherwise show the misleading "Invalid API key")."""
        _seed_team(fake)
        key = _mint_key(fake, created_by=_OWNER)

        def _boom(*a, **kw):
            raise httpx.ConnectError("boom")

        monkeypatch.setattr(httpx, "get", _boom)
        monkeypatch.setattr(httpx, "post", _boom)
        r = _exchange(client, key)
        assert r.status_code == 502, r.text
        assert "unavailable" in r.json()["detail"]

    def test_mint_session_identity_backstop_403(self, client, fake, monkeypatch):
        """Security review: a GoTrue /verify session for a DIFFERENT user.id
        than the mint target must be rejected (KEY_NOT_USER_MINTED) — never
        stored in the parent-domain cookie."""
        _seed_team(fake)
        key = _mint_key(fake, created_by=_OWNER)

        def _get(url, **kwargs):
            return httpx.Response(200, json={"id": _OWNER, "email": "owner@example.com"},
                                  request=httpx.Request("GET", url))

        def _post(url, **kwargs):
            if url.endswith("/auth/v1/admin/generate_link"):
                return httpx.Response(200, json={"hashed_token": "ht-1"},
                                      request=httpx.Request("POST", url))
            if url.endswith("/auth/v1/verify"):
                # WRONG user — a GoTrue anomaly must not mint the wrong session.
                return httpx.Response(200, json={
                    "access_token": "at", "refresh_token": "rt", "expires_in": 3600,
                    "expires_at": 9999999999, "token_type": "bearer",
                    "user": {"id": "some-other-user", "email": "other@example.com"}},
                    request=httpx.Request("POST", url))
            raise AssertionError(f"unexpected POST {url}")

        monkeypatch.setattr(httpx, "get", _get)
        monkeypatch.setattr(httpx, "post", _post)
        r = _exchange(client, key)
        assert r.status_code == 403, r.text
        assert r.json()["detail"]["error_code"] == "KEY_NOT_USER_MINTED"

    def test_creator_account_missing_403(self, client, fake, monkeypatch):
        _seed_team(fake)
        key = _mint_key(fake, created_by=_OWNER)
        monkeypatch.setattr(httpx, "get",
                            lambda url, **kw: httpx.Response(
                                404, json={"msg": "Not found"},
                                request=httpx.Request("GET", url)))
        r = _exchange(client, key)
        assert r.status_code == 403
        assert r.json()["detail"]["error_code"] == "ACCOUNT_MISSING"

    def test_rate_limited_429(self, client, fake, monkeypatch):
        monkeypatch.delenv("RATE_LIMIT_DISABLED", raising=False)  # limiter ON
        _seed_team(fake)
        key = _mint_key(fake)
        _patch_gotrue(monkeypatch)
        # 5/hr/IP → 6th attempt 429.
        for _ in range(5):
            r = _exchange(client, key)
            assert r.status_code == 200, r.text
        r = _exchange(client, key)
        assert r.status_code == 429

    def test_toctou_no_session_returned(self, client, fake, monkeypatch):
        """Owner deleted between GET and mint (TOCTOU): the post-verify
        membership backstop rejects — no session returned."""
        _seed_team(fake)
        key = _mint_key(fake, created_by=_OWNER)
        _patch_gotrue(monkeypatch)
        # Post-verify backstop: remove the membership from the fake so the
        # sanity check fails. Simulated by patching membership_for_user_team
        # to return None for the mint-target after the mint.
        real = sc.membership_for_user_team
        calls = {"n": 0}

        def _flaky(cp, user_id, team_id):
            calls["n"] += 1
            if calls["n"] > 1:  # pre-mint check passes; post-verify check fails
                return None
            return real(cp, user_id, team_id)

        monkeypatch.setattr(sc, "membership_for_user_team", _flaky)
        r = _exchange(client, key)
        assert r.status_code == 403


class TestCreateApiKeySessionAttribution:
    def test_session_authed_mint_records_user_uuid(self, client, fake, monkeypatch):
        """#1511: a SESSION-authed POST /v1/team/keys records the session
        user's UUID as created_by (so dashboard-minted keys can drive the
        exchange); the override/key-auth path keeps "api" (covered by
        test_writer_inventory.py:192)."""
        _seed_team(fake, user_ids=[_OWNER])

        async def _fake_verify(request):
            return {"user_id": _OWNER, "email": "owner@example.com",
                    "app_metadata": {"providers": ["github"]}}

        from tortoise import session_auth
        monkeypatch.setattr(session_auth, "verify_session_jwt", _fake_verify)
        r = client.post("/v1/team/keys", headers={"Authorization": "Bearer eyJ.session"})
        assert r.status_code == 200, r.text
        rows = [row for row in fake.tables["api_keys"]
                if row.get("created_via") == "provisioned"]
        assert rows, "no api_keys row written"
        assert rows[0]["created_by"] == _OWNER, \
            f"session mint must record the user UUID, got {rows[0]['created_by']!r}"
