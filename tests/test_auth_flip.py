"""REST + MCP auth-flip tests (#767, plan Task 3) — Supabase-backed resolution.

Covers the flip end-to-end with the in-memory FakeControlPlane (zero network):
- REST get_current_team: valid key auths, revoked key 401, registry-only key
  401 (E2E-7-negative), Supabase error → 500 (fail-closed), header semantics.
- E2E-2 session-key round-trip: /v1/session/key mint → api_keys row
  (lookup_hash/created_via/expires_at) → resolves on REST → revoked rejected.
- MCP TeamResolutionMiddleware: resolves via the SAME shared function;
  registry-only/revoked → 401 JSON-RPC; Supabase error → 503.

Env-gated: SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY set → Supabase mode;
TORTOISE_CONTROL_PLANE=registry (or unset creds) keeps the registry path —
covered by the unchanged existing suites (test_hosted_api, test_mcp_http).
"""
from __future__ import annotations

import os
import tempfile
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.routing import Mount

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

from tortoise.auth import lookup_hash  # noqa: I001
from tortoise.hosted_api import app, get_current_team, get_current_user  # noqa: F401
from tortoise.mcp_server import create_http_app

from tests.fake_control_plane import ErrorControlPlane, FakeControlPlane
from tests.test_supabase_control import (
    FREE_TEAM, TEAM_TIER_TEAM, TOKEN, _key_row, _membership_row,
)

# #1719 (Task 3): real UUIDs — JWT subjects + team_memberships.user_id are
# uuid in prod; non-UUID literals would 22P02 (the fake now enforces it).
_USER1 = "9f2c1a40-0000-4a00-8000-000000000001"
_USER2 = "9f2c1a40-0000-4a00-8000-000000000002"
_USER9 = "9f2c1a40-0000-4a00-8000-000000000009"

# Supabase mode token (deterministic via conftest pepper)


# ── Fixtures ────────────────────────────────────────────────────────────────

def _enable_supabase(monkeypatch, cp) -> FakeControlPlane:
    """Turn Supabase mode on and inject the fake control plane."""
    import tortoise.supabase_control as sc
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc_role_key_test")
    monkeypatch.setattr(sc, "get_control_plane", lambda: cp)
    return cp


@pytest.fixture
def supabase_fake() -> FakeControlPlane:
    """Fake control plane pre-seeded with one free team."""
    return FakeControlPlane({
        "api_keys": [],
        "team_memberships": [],
        "teams": [dict(FREE_TEAM)],
    })


def _patch_tortoise_sdk_init(db_path: str):
    """Make hosted_api's TortoiseSDK use a temp embedded DB (mirrors
    test_hosted_api) so /v1/team/keys-style registry reads don't touch prod."""
    import tortoise.hosted_api as ha_mod
    _orig = ha_mod.TortoiseSDK.__init__

    def _patched(self, db_path_arg=None, *, namespace=None, **kwargs):
        _orig(self, db_path, namespace=namespace)

    ha_mod.TortoiseSDK.__init__ = _patched
    # #1497: break the _make_sdk embedded fallback anchor — module-level
    # _FALLBACK_KEEPALIVE survives tests, so an anchored SDK bound to a prior
    # test's temp DB leaks state / dies socket. Re-bind to THIS temp DB.
    ha_mod._FALLBACK_KEEPALIVE.clear()
    return _orig


@pytest.fixture
def rest_client(monkeypatch, supabase_fake):
    """TestClient over the real app with REAL get_current_team (no override)
    resolving against the fake Supabase control plane."""
    _enable_supabase(monkeypatch, supabase_fake)
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "auth.db")
        _orig = _patch_tortoise_sdk_init(db_path)
        try:
            with TestClient(app) as tc:
                yield tc, supabase_fake
        finally:
            import tortoise.hosted_api as ha_mod
            ha_mod.TortoiseSDK.__init__ = _orig
            app.dependency_overrides.clear()


# ── REST flip ───────────────────────────────────────────────────────────────

class TestRestAuthFlip:
    def test_valid_key_auths(self, rest_client):
        """A Supabase api_keys row (lookup_hash match) authenticates REST."""
        tc, fake = rest_client
        fake.seed("api_keys", [_key_row()])
        r = tc.get("/v1/team/keys",
                   headers={"Authorization": f"Bearer {TOKEN}"})
        assert r.status_code == 200, r.text
        assert "keys" in r.json()

    def test_last_used_at_write_through(self, rest_client):
        """#685: successful auth writes api_keys.last_used_at (best-effort)."""
        tc, fake = rest_client
        fake.seed("api_keys", [_key_row()])
        r = tc.get("/v1/team/keys", headers={"Authorization": f"Bearer {TOKEN}"})
        assert r.status_code == 200
        assert fake.tables["api_keys"][0]["last_used_at"] is not None

    def test_revoked_key_401(self, rest_client):
        """P1-2: api_keys.revoked_at rejects on REST."""
        tc, fake = rest_client
        fake.seed("api_keys", [_key_row(revoked_at="2026-08-01T00:00:00Z")])
        r = tc.get("/v1/team/keys", headers={"Authorization": f"Bearer {TOKEN}"})
        assert r.status_code == 401

    def test_registry_only_key_401(self, rest_client):
        """E2E-7-negative: a key that exists only in the FalkorDB registry
        does NOT authenticate REST anymore."""
        tc, _ = rest_client  # fake has NO api_keys/team_memberships rows
        r = tc.get("/v1/team/keys", headers={"Authorization": f"Bearer {TOKEN}"})
        assert r.status_code == 401

    def test_missing_header_401(self, rest_client):
        tc, _ = rest_client
        assert tc.get("/v1/team/keys").status_code == 401

    def test_bad_scheme_401(self, rest_client):
        tc, _ = rest_client
        r = tc.get("/v1/team/keys", headers={"Authorization": f"Basic {TOKEN}"})
        assert r.status_code == 401

    def test_wrong_prefix_401(self, rest_client):
        tc, _ = rest_client
        r = tc.get("/v1/team/keys", headers={"Authorization": "Bearer not-a-tt-key"})
        assert r.status_code == 401

    def test_supabase_down_500_fail_closed(self, monkeypatch, rest_client):
        """P1-3: a Supabase error is a 500 — never a 401, never a 200, never
        a registry fallback."""
        import tortoise.supabase_control as sc
        monkeypatch.setattr(sc, "get_control_plane", lambda: ErrorControlPlane())
        tc, _ = rest_client
        r = tc.get("/v1/team/keys", headers={"Authorization": f"Bearer {TOKEN}"})
        assert r.status_code == 500
        assert r.json()["detail"] == "Auth error"


# ── E2E-2 session-key round-trip ────────────────────────────────────────────

class TestSessionKeyRoundTrip:
    """mint → api_keys row → resolve → revoked rejected (E2E-2 indicator)."""

    @pytest.fixture
    def authed_user(self):
        app.dependency_overrides[get_current_user] = lambda: {"user_id": _USER1}
        yield
        app.dependency_overrides.pop(get_current_user, None)

    def test_mint_resolves_then_revoked_rejected(self, rest_client, authed_user):
        tc, fake = rest_client
        fake.seed("team_memberships", [_membership_row(user_id=_USER1, team_id="team-free-001")])
        fake.tables["api_keys"] = []  # ensure clean

        # bootstrap mint → api_keys row with created_via + expires_at
        r = tc.post("/v1/session/key", json={"purpose": "bootstrap"})
        assert r.status_code == 200, r.text
        key = r.json()["key"]
        assert key.startswith("tt_")
        assert r.json()["purpose"] == "bootstrap"
        assert r.json()["expires_at"] is not None

        rows = fake.tables["api_keys"]
        assert len(rows) == 1
        assert rows[0]["created_via"] == "bootstrap"
        assert rows[0]["created_by"] == _USER1
        assert rows[0]["expires_at"] is not None  # 24h
        assert rows[0]["lookup_hash"] == lookup_hash(key)
        assert rows[0]["key_prefix"] == key[:10]

        # minted key RESOLVES on REST (api_keys.lookup_hash path)
        r = tc.get("/v1/team/keys", headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 200, r.text

        # revoked → rejected (authoritative)
        rows[0]["revoked_at"] = "2026-08-03T00:00:00Z"
        r = tc.get("/v1/team/keys", headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 401

    def test_recovery_mint_persistent_no_expiry(self, rest_client, authed_user):
        tc, fake = rest_client
        fake.seed("team_memberships", [_membership_row(user_id=_USER1, team_id="team-free-001")])
        r = tc.post("/v1/session/key", json={"purpose": "recovery"})
        assert r.status_code == 200, r.text
        assert r.json()["expires_at"] is None
        assert fake.tables["api_keys"][0]["created_via"] == "recovery"

    def test_bootstrap_cap_three_active(self, rest_client, authed_user):
        tc, fake = rest_client
        fake.seed("team_memberships", [_membership_row(user_id=_USER1, team_id="team-free-001")])
        for i in range(3):
            fake.seed("api_keys", [_key_row(
                id=f"boot-{i}", created_via="bootstrap", created_by=_USER1,
                lookup_hash=f"hash-{i}")])
        r = tc.post("/v1/session/key", json={"purpose": "bootstrap"})
        assert r.status_code == 429

    def test_recovery_cap_auto_revokes_oldest_other(self, rest_client, authed_user):
        """Free tier max_api_keys=2: minting a 3rd recovery key auto-revokes
        the oldest OTHER user's key (#750.10 — never the user's own)."""
        tc, fake = rest_client
        fake.seed("team_memberships", [_membership_row(user_id=_USER1, team_id="team-free-001")])
        fake.seed("api_keys", [
            _key_row(id="other-old", created_via="recovery", created_by=_USER2,
                     lookup_hash="h1", created_at="2026-08-01T00:00:00Z"),
            _key_row(id="other-new", created_via="recovery", created_by=_USER2,
                     lookup_hash="h2", created_at="2026-08-02T00:00:00Z"),
        ])
        r = tc.post("/v1/session/key", json={"purpose": "recovery"})
        assert r.status_code == 200, r.text
        by_id = {row["id"]: row for row in fake.tables["api_keys"]}
        assert by_id["other-old"]["revoked_at"] is not None  # oldest other revoked
        assert by_id["other-new"]["revoked_at"] is None
        new_rows = [row for row in fake.tables["api_keys"]
                    if row["id"] not in ("other-old", "other-new")]
        assert len(new_rows) == 1
        assert new_rows[0]["revoked_at"] is None  # minted key lands unrevoked
        assert new_rows[0]["created_via"] == "recovery"

    def test_mint_requires_membership(self, rest_client, authed_user):
        tc, fake = rest_client
        fake.tables["team_memberships"] = []
        r = tc.post("/v1/session/key", json={"purpose": "bootstrap"})
        assert r.status_code == 403


# ── E2E-3 invitations flip (plan Task 4) ───────────────────────────────────

class TestInvitesEndpointFlip:
    """E2E-3: owner invites by email → invite verified via lookup_hash,
    accepted → real membership with the invited role, pending invite
    consumed; a used/revoked invite cannot be re-accepted — over HTTP in
    Supabase mode with the fake control plane."""

    @pytest.fixture
    def as_user(self):
        """Override get_current_user per test (JWT session user)."""

        def _set(user_id: str, email: str | None = None):
            app.dependency_overrides[get_current_user] = lambda: {
                "user_id": user_id, "email": email}

        yield _set
        app.dependency_overrides.pop(get_current_user, None)

    @pytest.fixture
    def team_tier(self, rest_client):
        """Team-tier team with user-1 as owner (invites enabled)."""
        tc, fake = rest_client
        fake.tables["teams"] = [dict(TEAM_TIER_TEAM)]
        fake.seed("team_memberships", [{
            "user_id": _USER1, "team_id": "team-team-001",
            "role": "owner", "status": "active"}])
        return tc, fake

    def test_mint_accept_round_trip_role_preserved(self, team_tier, as_user):
        """E2E-3 happy path: mint → lookup_hash row → accept → membership
        with the INVITED role; consumed invite cannot be re-accepted."""
        tc, fake = team_tier
        as_user(_USER1)

        r = tc.post("/v1/invites", json={
            "team_id": "team-team-001", "email": "bob@example.com",
            "role": "admin"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "invited"
        assert body["role"] == "admin"
        token = body["token"]
        assert token

        # invite verified via lookup_hash (token never stored plaintext)
        rows = fake.tables["invitations"]
        assert len(rows) == 1
        assert rows[0]["lookup_hash"] == lookup_hash(token)
        assert rows[0]["email"] == "bob@example.com"
        assert rows[0]["status"] == "pending"

        # accept as the invitee (JWT email must match)
        as_user(_USER2, "bob@example.com")
        r = tc.post("/v1/invites/accept", json={"token": token})
        assert r.status_code == 200, r.text
        assert r.json() == {"team_id": "team-team-001", "role": "admin"}

        mem = [m for m in fake.tables["team_memberships"]
               if m["user_id"] == _USER2]
        assert len(mem) == 1
        assert mem[0]["role"] == "admin"  # invited role preserved (O/I/T)
        assert mem[0]["status"] == "active"
        assert rows[0]["status"] == "accepted"  # pending invite consumed
        assert rows[0]["accepted_at"] is not None

        # used invite cannot be re-accepted (E2E-3)
        r = tc.post("/v1/invites/accept", json={"token": token})
        assert r.status_code == 400
        assert "accepted" in r.json()["detail"]

    def test_mint_dedup_409(self, team_tier, as_user):
        tc, fake = team_tier
        as_user(_USER1)
        payload = {"team_id": "team-team-001", "email": "bob@example.com",
                   "role": "member"}
        assert tc.post("/v1/invites", json=payload).status_code == 200
        r = tc.post("/v1/invites", json=payload)
        assert r.status_code == 409
        assert len(fake.tables["invitations"]) == 1

    def test_mint_requires_team_tier(self, rest_client, as_user):
        """Free tier → 402 (invites are a Team-tier feature, D7 #574)."""
        tc, fake = rest_client
        fake.seed("team_memberships", [{
            "user_id": _USER1, "team_id": "team-free-001",
            "role": "owner", "status": "active"}])
        as_user(_USER1)
        r = tc.post("/v1/invites", json={
            "team_id": "team-free-001", "email": "bob@example.com",
            "role": "member"})
        assert r.status_code == 402

    def test_mint_requires_owner_admin(self, team_tier, as_user):
        tc, fake = team_tier
        fake.seed("team_memberships", [{
            "user_id": _USER9, "team_id": "team-team-001",
            "role": "member", "status": "active"}])
        as_user(_USER9)
        r = tc.post("/v1/invites", json={
            "team_id": "team-team-001", "email": "bob@example.com",
            "role": "member"})
        assert r.status_code == 403

    def test_expired_invite_rejected(self, team_tier, as_user):
        tc, fake = team_tier
        as_user(_USER1)
        r = tc.post("/v1/invites", json={
            "team_id": "team-team-001", "email": "bob@example.com",
            "role": "member"})
        token = r.json()["token"]
        from datetime import datetime, timedelta, timezone
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()  # noqa: UP017
        fake.tables["invitations"][0]["expires_at"] = past
        as_user(_USER2, "bob@example.com")
        r = tc.post("/v1/invites/accept", json={"token": token})
        assert r.status_code == 400
        assert "expired" in r.json()["detail"]

    def test_revoked_invite_rejected_after_rescind(self, team_tier, as_user):
        """E2E-3: rescind → revoked; a revoked invite cannot be accepted."""
        tc, fake = team_tier
        as_user(_USER1)
        r = tc.post("/v1/invites", json={
            "team_id": "team-team-001", "email": "bob@example.com",
            "role": "member"})
        invite_id = r.json()["invite_id"]
        token = r.json()["token"]

        r = tc.delete(f"/v1/invites/{invite_id}?team_id=team-team-001")
        assert r.status_code == 200, r.text
        assert r.json()["revoked"] is True
        assert fake.tables["invitations"][0]["status"] == "revoked"

        as_user(_USER2, "bob@example.com")
        r = tc.post("/v1/invites/accept", json={"token": token})
        assert r.status_code == 400
        assert "revoked" in r.json()["detail"]
        # no membership created for the invitee (user-1's row is the owner)
        assert all(m["user_id"] != _USER2
                   for m in fake.tables["team_memberships"])

    def test_rescind_requires_owner_admin(self, team_tier, as_user):
        tc, fake = team_tier
        as_user(_USER1)
        r = tc.post("/v1/invites", json={
            "team_id": "team-team-001", "email": "bob@example.com",
            "role": "member"})
        invite_id = r.json()["invite_id"]
        fake.seed("team_memberships", [{
            "user_id": _USER9, "team_id": "team-team-001",
            "role": "member", "status": "active"}])
        as_user(_USER9)
        r = tc.delete(f"/v1/invites/{invite_id}?team_id=team-team-001")
        assert r.status_code == 403
        assert fake.tables["invitations"][0]["status"] == "pending"

    def test_list_pending_invites(self, team_tier, as_user):
        tc, fake = team_tier
        as_user(_USER1)
        tokens = {}
        for email in ("bob@example.com", "carol@example.com", "dave@example.com"):
            r = tc.post("/v1/invites", json={
                "team_id": "team-team-001", "email": email,
                "role": "member"})
            assert r.status_code == 200, r.text
            tokens[email] = r.json()["token"]
        # consume one (accepted), rescind another → only one stays pending
        as_user(_USER2, "bob@example.com")
        r = tc.post("/v1/invites/accept", json={"token": tokens["bob@example.com"]})
        assert r.status_code == 200, r.text
        as_user(_USER1)
        r = tc.delete(f"/v1/invites/{fake.tables['invitations'][2]['id']}?team_id=team-team-001")
        assert r.status_code == 200, r.text

        r = tc.get("/v1/invites?team_id=team-team-001")
        assert r.status_code == 200
        rows = r.json()
        assert [i["email"] for i in rows] == ["carol@example.com"]
        assert rows[0]["status"] == "pending"

    def test_invites_fail_closed_on_control_plane_error(self, monkeypatch,
                                                        team_tier, as_user):
        """A Supabase error is a 500 — never a registry fallback (#851)."""
        import tortoise.supabase_control as sc
        monkeypatch.setattr(sc, "get_control_plane", lambda: ErrorControlPlane())
        tc, _ = team_tier
        as_user(_USER1)
        r = tc.post("/v1/invites", json={
            "team_id": "team-team-001", "email": "bob@example.com",
            "role": "member"})
        assert r.status_code == 500


# ── MCP flip ────────────────────────────────────────────────────────────────

def _mounted_test_client(mcp_app):
    """Mount the MCP app at /mcp (mirrors hosted_api + test_mcp_http)."""

    @asynccontextmanager
    async def _lifespan(parent_app):
        async with mcp_app.lifespan(mcp_app):
            yield

    parent = Starlette(lifespan=_lifespan, routes=[Mount("/mcp", app=mcp_app)])
    return TestClient(parent)


def _mcp_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }


def _parse_sse_json(r) -> dict | None:
    """Extract the JSON payload from an SSE response body."""
    text = r.text
    if text.startswith("{"):
        return r.json()
    for line in text.splitlines():
        if line.startswith("data: "):
            import json
            return json.loads(line[6:])
    return None


class TestMcpAuthFlip:
    def test_mcp_resolves_via_supabase(self, monkeypatch, supabase_fake):
        """MCP TeamResolutionMiddleware resolves tt_ keys via Supabase."""
        supabase_fake.seed("api_keys", [_key_row()])
        _enable_supabase(monkeypatch, supabase_fake)
        mcp_app = create_http_app(allowed_origins=[])
        tc = _mounted_test_client(mcp_app)
        tc.headers.update(_mcp_headers(TOKEN))
        with tc:
            r = tc.post("/mcp", json={"jsonrpc": "2.0", "method": "tools/list", "id": 1})
            assert r.status_code == 200, r.text
            body = _parse_sse_json(r)
            assert body is not None and "result" in body

    def test_mcp_registry_only_key_401(self, monkeypatch, supabase_fake):
        """E2E-7-negative: registry-only key → 401 JSON-RPC on MCP."""
        _enable_supabase(monkeypatch, supabase_fake)  # no key rows at all
        mcp_app = create_http_app(allowed_origins=[])
        tc = _mounted_test_client(mcp_app)
        tc.headers.update(_mcp_headers(TOKEN))
        with tc:
            r = tc.post("/mcp", json={"jsonrpc": "2.0", "method": "tools/list", "id": 1})
            assert r.status_code == 401
            body = _parse_sse_json(r)
            assert body is not None and "error" in body
            assert "tt_" in body["error"]["message"]

    def test_mcp_revoked_key_401(self, monkeypatch, supabase_fake):
        _enable_supabase(monkeypatch, supabase_fake)
        supabase_fake.seed("api_keys",
                           [_key_row(revoked_at="2026-08-01T00:00:00Z")])
        mcp_app = create_http_app(allowed_origins=[])
        tc = _mounted_test_client(mcp_app)
        tc.headers.update(_mcp_headers(TOKEN))
        with tc:
            r = tc.post("/mcp", json={"jsonrpc": "2.0", "method": "tools/list", "id": 1})
            assert r.status_code == 401

    def test_mcp_supabase_error_503_fail_closed(self, monkeypatch):
        """A Supabase error is a 503 JSON-RPC — never 200, never a registry
        fallback."""
        import tortoise.supabase_control as sc
        monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc_role_key_test")
        monkeypatch.setattr(sc, "get_control_plane", lambda: ErrorControlPlane())
        mcp_app = create_http_app(allowed_origins=[])
        tc = _mounted_test_client(mcp_app)
        tc.headers.update(_mcp_headers(TOKEN))
        with tc:
            r = tc.post("/mcp", json={"jsonrpc": "2.0", "method": "tools/list", "id": 1})
            assert r.status_code == 503
            body = _parse_sse_json(r)
            assert body is not None and "error" in body

    def test_mcp_supabase_mode_never_touches_registry(self, monkeypatch,
                                                      supabase_fake):
        """In Supabase mode the middleware must not construct the registry SDK
        (its apikey_verify would 503 on a registry-only key)."""
        import tortoise.mcp_auth as ma
        supabase_fake.seed("api_keys", [_key_row()])
        _enable_supabase(monkeypatch, supabase_fake)
        called = []

        orig = ma.TeamResolutionMiddleware._get_registry_sdk

        def _boom(self):
            called.append(True)
            raise AssertionError("registry SDK must not be used in Supabase mode")

        ma.TeamResolutionMiddleware._get_registry_sdk = _boom
        try:
            mcp_app = create_http_app(allowed_origins=[])
            tc = _mounted_test_client(mcp_app)
            tc.headers.update(_mcp_headers(TOKEN))
            with tc:
                r = tc.post("/mcp", json={"jsonrpc": "2.0", "method": "tools/list", "id": 1})
                assert r.status_code == 200, r.text
            assert called == []
        finally:
            ma.TeamResolutionMiddleware._get_registry_sdk = orig
