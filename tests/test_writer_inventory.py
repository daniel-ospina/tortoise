"""#765 — plan Task 8 writer/reader inventory: Supabase-mode writer flip tests.

Every remaining registry writer/reader from the plan's mechanically-derived
inventory is exercised in Supabase control-plane mode against the in-memory
FakeControlPlane (zero network), with a registry SPY asserting the FalkorDB
registry is never touched:

- writers: POST /v1/team/keys, GET/DELETE /v1/team/keys/{id}, POST
  /v1/agent/signup, POST /v1/register, POST /v1/teams, members DELETE/PATCH,
  POST /v1/internal/reconcile, POST /v1/onboarding/team, /internal/provision
  (disabled), create_graph/_graph_create + graph_list (env-gated in sdk.py).
- readers: member listing, graph_list, quota counts (api_keys/users/graphs).

The zero-registry invariant: in Supabase mode a registry-namespaced SDK may
be CONSTRUCTED (graph_list is mode-aware on it) but _get_registry() must
never be called — the spy replaces it with an AssertionError.

Selfhost (TORTOISE_CONTROL_PLANE=registry) keeps the registry paths — the
unchanged test_hosted_api / test_dr_endpoints / test_control_plane suites
cover them.
"""
from __future__ import annotations

import os
import tempfile

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")
os.environ.setdefault("FASTAPI_INTERNAL_KEY", "test-internal-shared-secret-xyz")

from tortoise.auth import lookup_hash
from tortoise.hosted_api import app, get_current_team, get_current_user

from tests.fake_control_plane import ErrorControlPlane, FakeControlPlane
from tests.test_supabase_control import FREE_TEAM, TOKEN, _key_row, _membership_row

_INTERNAL_HEADERS = {"Authorization": "Bearer test-internal-shared-secret-xyz"}

TEST_TEAM = {
    "team_id": "team-free-001",
    "key_id": "key-001",
    "tier": "free",
    "max_users": 1,
    "max_graphs": 1,
    "max_points": 10000,
    "max_api_keys": 2,
    "max_sessions": 1000,
}


# ── Fixtures ────────────────────────────────────────────────────────────────


class _RegistrySpy:
    """Spy over hosted_api._make_sdk: a registry-namespaced SDK may be built
    (mode-aware methods like graph_list short-circuit before touching the
    registry) but _get_registry() must NEVER be called in Supabase mode."""

    def __init__(self, original):
        self._original = original
        self.registry_sdks = []
        self.registry_accessed = False

    def __call__(self, *, namespace: str | None = None):
        sdk = self._original(namespace=namespace)
        if namespace == "registry":
            self.registry_sdks.append(sdk)
            sdk._get_registry = self._boom  # type: ignore[method-assign]
        return sdk

    def _boom(self, *a, **kw):  # pragma: no cover — assertion helper
        self.registry_accessed = True
        raise AssertionError(
            "registry accessed in Supabase mode — #765 inventory violation")

    def assert_clean(self):
        assert not self.registry_accessed, (
            "registry was queried in Supabase mode — #765 inventory "
            "violation (a registry-namespaced SDK may be CONSTRUCTED — "
            "mode-aware methods short-circuit — but _get_registry() must "
            "never be called)")


@pytest.fixture
def spy(monkeypatch):
    import tortoise.hosted_api as ha_mod
    s = _RegistrySpy(ha_mod._make_sdk)
    monkeypatch.setattr(ha_mod, "_make_sdk", s)
    return s


@pytest.fixture
def fake() -> FakeControlPlane:
    return FakeControlPlane({
        "api_keys": [],
        "team_memberships": [],
        "teams": [dict(FREE_TEAM)],
        "invitations": [],
    })


@pytest.fixture
def supabase_env(monkeypatch, fake) -> FakeControlPlane:
    """Supabase mode on + fake control plane injected."""
    import tortoise.supabase_control as sc
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc_role_key_test")
    monkeypatch.setattr(sc, "get_control_plane", lambda: fake)
    return fake


def _patch_tortoise_sdk_init(db_path: str):
    import tortoise.hosted_api as ha_mod
    _orig = ha_mod.TortoiseSDK.__init__

    def _patched(self, db_path_arg=None, *, namespace=None, **kwargs):
        _orig(self, db_path, namespace=namespace)

    ha_mod.TortoiseSDK.__init__ = _patched
    return _orig


@pytest.fixture
def client(monkeypatch, supabase_env, spy):
    """TestClient in Supabase mode with a temp embedded DB + registry spy."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "inv.db")
        _orig = _patch_tortoise_sdk_init(db_path)
        try:
            with TestClient(app) as tc:
                yield tc, supabase_env, spy
        finally:
            import tortoise.hosted_api as ha_mod
            ha_mod.TortoiseSDK.__init__ = _orig
            app.dependency_overrides.clear()


@pytest.fixture
def team_client(client):
    """Client with get_current_team overridden (authenticated team dict)."""
    tc, fake, spy = client
    app.dependency_overrides[get_current_team] = lambda: dict(TEST_TEAM)
    return tc, fake, spy


@pytest.fixture
def user_client(client):
    """Client with get_current_user overridden (JWT session user)."""
    tc, fake, spy = client
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "user-1", "email": "user-1@example.com"}
    return tc, fake, spy


def _owner_membership(**overrides) -> dict:
    row = {
        "id": "mem-1", "user_id": "user-1", "team_id": "team-free-001",
        "role": "owner", "status": "active", "identity": None,
    }
    row.update(overrides)
    return row


# ── POST /v1/team/keys (create_api_key) ─────────────────────────────────────

class TestCreateApiKey:
    def test_key_lands_in_api_keys_and_resolves(self, team_client):
        """#765 writer flip: the minted key is an api_keys row (lookup_hash +
        created_via='provisioned') and RESOLVES via lookup_hash on REST."""
        tc, fake, _ = team_client
        r = tc.post("/v1/team/keys")
        assert r.status_code == 200, r.text
        body = r.json()
        assert set(body) == {"id", "key", "key_prefix", "created_at"}
        key = body["key"]
        assert key.startswith("tt_")

        rows = fake.tables["api_keys"]
        assert len(rows) == 1
        assert rows[0]["id"] == body["id"]
        assert rows[0]["team_id"] == TEST_TEAM["team_id"]
        assert rows[0]["lookup_hash"] == lookup_hash(key)
        assert rows[0]["key_prefix"] == key[:10]
        assert rows[0]["created_via"] == "provisioned"
        assert rows[0]["created_by"] == "api"
        assert rows[0]["revoked_at"] is None

        # minted key authenticates (api_keys.lookup_hash path)
        app.dependency_overrides.clear()
        r2 = tc.get("/v1/team", headers={"Authorization": f"Bearer {key}"})
        assert r2.status_code == 200, r2.text

    def test_never_touches_registry(self, team_client, spy):
        tc, fake, _ = team_client
        r = tc.post("/v1/team/keys")
        assert r.status_code == 200, r.text
        spy.assert_clean()

    def test_fail_closed_on_control_plane_error(self, monkeypatch, client):
        """A Supabase error is a 500 — never a registry fallback."""
        import tortoise.supabase_control as sc
        monkeypatch.setattr(sc, "get_control_plane", lambda: ErrorControlPlane())
        tc, _, _ = client
        app.dependency_overrides[get_current_team] = lambda: dict(TEST_TEAM)
        r = tc.post("/v1/team/keys")
        assert r.status_code == 500

    def test_quota_counts_api_keys_from_supabase(self, team_client):
        """#765 quota paths: the api_keys cap counts api_keys ROWS, not
        registry nodes (free tier max_api_keys=2 → 3rd key is a 402)."""
        tc, fake, _ = team_client
        assert tc.post("/v1/team/keys").status_code == 200
        assert tc.post("/v1/team/keys").status_code == 200
        r = tc.post("/v1/team/keys")
        assert r.status_code == 402, r.text


# ── GET /v1/team/keys (list_api_keys) ───────────────────────────────────────

class TestListApiKeys:
    def test_lists_all_keys_incl_revoked_newest_first(self, team_client):
        tc, fake, _ = team_client
        fake.seed("api_keys", [
            _key_row(id="k-old", created_at="2026-07-01T00:00:00Z"),
            _key_row(id="k-new", created_at="2026-08-01T00:00:00Z"),
            _key_row(id="k-revoked", created_at="2026-08-02T00:00:00Z",
                     revoked_at="2026-08-03T00:00:00Z"),
        ])
        r = tc.get("/v1/team/keys")
        assert r.status_code == 200, r.text
        keys = r.json()["keys"]
        assert [k["id"] for k in keys] == ["k-revoked", "k-new", "k-old"]
        assert keys[0]["revoked_at"] == "2026-08-03T00:00:00Z"
        assert keys[1]["revoked_at"] is None
        assert all({"id", "key_prefix", "created_at", "last_used_at",
                    "revoked_at"} <= set(k) for k in keys)

    def test_never_touches_registry(self, team_client, spy):
        tc, fake, _ = team_client
        r = tc.get("/v1/team/keys")
        assert r.status_code == 200
        spy.assert_clean()


# ── DELETE /v1/team/keys/{id} (revoke_api_key) ─────────────────────────────

class TestRevokeApiKey:
    def test_revoke_round_trip(self, team_client):
        tc, fake, _ = team_client
        r = tc.post("/v1/team/keys")
        kid, key = r.json()["id"], r.json()["key"]
        r = tc.delete(f"/v1/team/keys/{kid}")
        assert r.status_code == 200, r.text
        assert r.json() == {"revoked": True, "key_id": kid,
                            "revoked_at": fake.tables["api_keys"][0]["revoked_at"]}
        assert fake.tables["api_keys"][0]["revoked_at"] is not None

        # idempotent second revoke
        r = tc.delete(f"/v1/team/keys/{kid}")
        assert r.status_code == 200
        assert r.json()["already"] is True

        # revoked key no longer authenticates (P1-2 authoritative — the
        # api_keys twin rejects even though the row still exists)
        app.dependency_overrides.clear()
        r2 = tc.get("/v1/team", headers={"Authorization": f"Bearer {key}"})
        assert r2.status_code == 401

    def test_unknown_key_404(self, team_client):
        tc, _, _ = team_client
        assert tc.delete("/v1/team/keys/no-such").status_code == 404

    def test_other_team_key_403(self, team_client):
        tc, fake, _ = team_client
        fake.seed("api_keys", [_key_row(id="other-key", team_id="team-other")])
        r = tc.delete("/v1/team/keys/other-key")
        assert r.status_code == 403

    def test_never_touches_registry(self, team_client, spy):
        tc, fake, _ = team_client
        fake.seed("api_keys", [_key_row(id="k1")])
        assert tc.delete("/v1/team/keys/k1").status_code == 200
        spy.assert_clean()


# ── POST /v1/agent/signup (identity path via provision_team RPC) ───────────

class TestAgentSignup:
    def test_signup_provisions_identity_path(self, client):
        """#765 writer flip: signup → provision_team RPC with NULL user_id +
        identity; teams/membership/api_keys rows land; key resolves."""
        tc, fake, _ = client
        r = tc.post("/v1/agent/signup", json={})
        assert r.status_code == 200, r.text
        body = r.json()
        key, team_id, identity = body["key"], body["team_id"], body["identity"]
        assert identity.startswith("anon-")

        # exactly one provision_team RPC call, identity path
        assert len(fake.rpc_calls) == 1
        fn, p = fake.rpc_calls[0]
        assert fn == "provision_team"
        assert p["p_user_id"] is None
        assert p["p_identity"] == identity
        # key_prefix = api_key[:10] — registry-path parity (review P2,
        # PR #874)
        assert p["p_key_prefix"] == key[:10]
        assert p["p_team_id"] == team_id
        assert p["p_graph_name"] == f"team_{team_id}"
        assert p["p_lookup_hash"] == lookup_hash(key)
        assert p["p_key_hash"]  # salted PBKDF2 continuity hash
        assert p["p_tier"] == "free"

        # rows landed (fake simulates the RPC)
        assert any(t["id"] == team_id for t in fake.tables["teams"])
        mem = [m for m in fake.tables["team_memberships"]
               if m["team_id"] == team_id]
        assert len(mem) == 1
        assert mem[0]["user_id"] is None
        assert mem[0]["identity"] == identity
        assert mem[0]["role"] == "owner" and mem[0]["status"] == "active"
        keys = [k for k in fake.tables["api_keys"] if k["team_id"] == team_id]
        assert len(keys) == 1
        assert keys[0]["lookup_hash"] == lookup_hash(key)

        # minted key authenticates (api_keys.lookup_hash path)
        r2 = tc.get("/v1/team", headers={"Authorization": f"Bearer {key}"})
        assert r2.status_code == 200, r2.text
        assert r2.json()["team_id"] == team_id

    def test_never_touches_registry(self, client, spy):
        tc, _, _ = client
        r = tc.post("/v1/agent/signup", json={})
        assert r.status_code == 200, r.text
        spy.assert_clean()

    def test_fail_closed_on_rpc_error(self, monkeypatch, client):
        import tortoise.supabase_control as sc
        monkeypatch.setattr(sc, "get_control_plane", lambda: ErrorControlPlane())
        tc, _, _ = client
        r = tc.post("/v1/agent/signup", json={})
        assert r.status_code == 500

    def test_rate_limit_query_shape(self, client):
        """#741: the server-side identity is fresh per request, so the
        identity-based count is 0 by construction — replaying a client
        identity never 429s (dead-limit parity with the registry path)."""
        tc, fake, _ = client
        ident = "anon-client-chosen"
        for _ in range(4):
            r = tc.post("/v1/agent/signup", json={"identity": ident})
            assert r.status_code == 200, r.text


# ── POST /v1/register (email provision via provision_team RPC) ─────────────

class TestRegister:
    def test_register_provisions_with_email(self, client):
        tc, fake, _ = client
        r = tc.post("/v1/register", json={
            "email": "founder@example.com", "password": "hunter2secret"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["api_key"].startswith("tt_")
        assert body["team_id"] and body["graph_name"] == f"team_{body['team_id']}"

        fn, p = fake.rpc_calls[0]
        assert fn == "provision_team"
        assert p["p_user_id"] is None
        assert p["p_identity"].startswith("reg-")  # deterministic per-email
        assert p["p_email"] == "founder@example.com"
        assert p["p_lookup_hash"] == lookup_hash(body["api_key"])
        team = next(t for t in fake.tables["teams"]
                    if t["id"] == body["team_id"])
        assert team["email"] == "founder@example.com"

        # minted key authenticates
        r2 = tc.get("/v1/team", headers={"Authorization": f"Bearer {body['api_key']}"})
        assert r2.status_code == 200, r2.text

    def test_duplicate_email_409(self, client):
        tc, fake, _ = client
        fake.seed("teams", [{"id": "t-dup", "name": "dup",
                             "email": "dup@example.com"}])
        r = tc.post("/v1/register", json={
            "email": "dup@example.com", "password": "hunter2secret"})
        assert r.status_code == 409
        assert r.json()["detail"]["message"] == "already_registered"

    def test_never_touches_registry(self, client, spy):
        tc, _, _ = client
        r = tc.post("/v1/register", json={
            "email": "spy@example.com", "password": "hunter2secret"})
        assert r.status_code == 200, r.text
        spy.assert_clean()

    def test_fail_closed_on_rpc_error(self, monkeypatch, client):
        import tortoise.supabase_control as sc
        monkeypatch.setattr(sc, "get_control_plane", lambda: ErrorControlPlane())
        tc, _, _ = client
        r = tc.post("/v1/register", json={
            "email": "fail@example.com", "password": "hunter2secret"})
        assert r.status_code == 500


# ── POST /v1/teams (create_team — user path via provision_team RPC) ────────

class TestCreateTeam:
    def test_create_team_user_path(self, user_client):
        tc, fake, _ = user_client
        r = tc.post("/v1/teams", json={"name": "acme"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["name"] == "acme"
        assert body["tier"] == "free"
        assert body["graph_name"] == "team_acme"  # sdk.team_create parity

        fn, p = fake.rpc_calls[0]
        assert fn == "provision_team"
        assert p["p_user_id"] == "user-1"
        assert p["p_identity"] is None
        assert p["p_team_id"] == body["team_id"]
        assert p["p_team_name"] == "acme"
        # owner membership landed for the JWT user
        mem = [m for m in fake.tables["team_memberships"]
               if m["user_id"] == "user-1" and m["team_id"] == body["team_id"]]
        assert len(mem) == 1 and mem[0]["role"] == "owner"

    def test_duplicate_name_409(self, user_client):
        tc, fake, _ = user_client
        fake.seed("teams", [{"id": "t-acme", "name": "acme"}])
        r = tc.post("/v1/teams", json={"name": "acme"})
        assert r.status_code == 409

    def test_duplicate_name_race_maps_rpc_409(self, user_client):
        """0011 unique-index guard (review P1, PR #874): a concurrent
        duplicate name surfaces as a PostgREST 409 from provision_team →
        HTTP 409, NOT a 500 (the pre-check is a friendly fast-path; the
        index is authoritative)."""
        tc, fake, _ = user_client

        class _UniqueViolation(FakeControlPlane):
            def __init__(self, base):
                self._base = base

            def rpc(self, fn, body):
                if fn == "provision_team":
                    raise RuntimeError(
                        "Supabase control-plane RPC failed (provision_team): "
                        "HTTP 409 (duplicate key value violates unique "
                        "constraint uq_teams_name)")
                return self._base.rpc(fn, body)

            def query(self, table, **kw):
                return self._base.query(table, **kw)

        import tortoise.supabase_control as sc
        old = sc.get_control_plane
        sc.get_control_plane = lambda: _UniqueViolation(fake)
        try:
            r = tc.post("/v1/teams", json={"name": "acme"})
        finally:
            sc.get_control_plane = old
        assert r.status_code == 409, r.text

    def test_rate_limit_counts_owner_rows_only(self, user_client):
        """#743(b) parity (review P2, PR #874): the team-create rate limit
        counts OWNER memberships only — a user who accepted invites into
        other teams (member rows) must not be 429-blocked from creating
        their own team."""
        from datetime import datetime, timedelta, timezone
        tc, fake, _ = user_client
        since = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        # 3 MEMBER rows (invite accepts) — must NOT trigger the owner limit
        fake.seed("team_memberships", [
            {"id": f"mem-inv-{i}", "user_id": "user-1",
             "team_id": f"team-inv-{i}", "role": "member",
             "status": "active", "created_at": since}
            for i in range(3)
        ])
        r = tc.post("/v1/teams", json={"name": "mine"})
        assert r.status_code == 200, r.text

    def test_rate_limit_3_per_hour(self, user_client):
        tc, fake, _ = user_client
        from datetime import datetime, timedelta, timezone
        since = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        fake.seed("team_memberships", [
            _owner_membership(id=f"m{i}", team_id=f"team-{i}",
                              created_at=since)
            for i in range(3)
        ])
        r = tc.post("/v1/teams", json={"name": "fourth"})
        assert r.status_code == 429

    def test_rate_limit_ignores_old_rows(self, user_client):
        from datetime import datetime, timedelta, timezone
        tc, fake, _ = user_client
        old = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        fake.seed("team_memberships", [
            _owner_membership(id="m-old", team_id="team-old", created_at=old),
        ])
        r = tc.post("/v1/teams", json={"name": "fresh"})
        assert r.status_code == 200, r.text

    def test_never_touches_registry(self, user_client, spy):
        tc, _, _ = user_client
        r = tc.post("/v1/teams", json={"name": "acme"})
        assert r.status_code == 200, r.text
        spy.assert_clean()


# ── Members surface: list / remove / role change ───────────────────────────

class TestMembers:
    def _seed_team(self, fake, team_id="team-free-001"):
        fake.seed("team_memberships", [
            _owner_membership(team_id=team_id),
            {"id": "mem-2", "user_id": "user-2", "team_id": team_id,
             "role": "member", "status": "active", "identity": None},
            {"id": "mem-3", "user_id": None, "team_id": team_id,
             "role": "member", "status": "active", "identity": "anon-abc123"},
        ])

    def test_list_members_active_and_invited(self, user_client):
        tc, fake, _ = user_client
        self._seed_team(fake)
        fake.seed("team_memberships", [
            {"id": "mem-4", "user_id": "user-4", "team_id": "team-free-001",
             "role": "member", "status": "removed", "identity": None},
        ])
        r = tc.get("/v1/teams/team-free-001/members")
        assert r.status_code == 200, r.text
        rows = r.json()
        assert len(rows) == 3  # removed excluded
        by_id = {m["user_id"]: m for m in rows}
        # identity rows surface their anon anchor so the API can round-trip
        assert by_id["anon-abc123"]["role"] == "member"
        assert all({"user_id", "role", "status", "email"} <= set(m)
                   for m in rows)

    def test_remove_member(self, user_client):
        tc, fake, _ = user_client
        self._seed_team(fake)
        r = tc.delete("/v1/teams/team-free-001/members/user-2")
        assert r.status_code == 200, r.text
        assert r.json() == {"status": "removed"}
        mem = next(m for m in fake.tables["team_memberships"]
                   if m["id"] == "mem-2")
        assert mem["status"] == "removed"

    def test_remove_agent_member_by_identity(self, user_client):
        """Identity rows are removable via their surfaced user_id."""
        tc, fake, _ = user_client
        self._seed_team(fake)
        r = tc.delete("/v1/teams/team-free-001/members/anon-abc123")
        assert r.status_code == 200, r.text
        mem = next(m for m in fake.tables["team_memberships"]
                   if m["id"] == "mem-3")
        assert mem["status"] == "removed"

    def test_remove_owner_409(self, user_client):
        tc, fake, _ = user_client
        self._seed_team(fake)
        r = tc.delete("/v1/teams/team-free-001/members/user-1")
        assert r.status_code == 409

    def test_remove_unknown_404(self, user_client):
        tc, fake, _ = user_client
        self._seed_team(fake)
        r = tc.delete("/v1/teams/team-free-001/members/ghost")
        assert r.status_code == 404

    def test_change_role(self, user_client):
        tc, fake, _ = user_client
        self._seed_team(fake)
        r = tc.patch("/v1/teams/team-free-001/members/user-2",
                     json={"role": "admin"})
        assert r.status_code == 200, r.text
        assert r.json() == {"user_id": "user-2", "role": "admin"}
        mem = next(m for m in fake.tables["team_memberships"]
                   if m["id"] == "mem-2")
        assert mem["role"] == "admin"

    def test_change_owner_role_409(self, user_client):
        tc, fake, _ = user_client
        self._seed_team(fake)
        r = tc.patch("/v1/teams/team-free-001/members/user-1",
                     json={"role": "member"})
        assert r.status_code == 409

    def test_never_touches_registry(self, user_client, spy):
        tc, fake, _ = user_client
        self._seed_team(fake)
        assert tc.get("/v1/teams/team-free-001/members").status_code == 200
        assert tc.delete("/v1/teams/team-free-001/members/user-2").status_code == 200
        assert tc.patch("/v1/teams/team-free-001/members/user-2",
                        json={"role": "member"}).status_code == 200
        spy.assert_clean()


# ── POST /v1/internal/reconcile (expired-bootstrap sweep) ──────────────────

class TestReconcile:
    def test_sweeps_expired_bootstrap_keys(self, client):
        tc, fake, _ = client
        from datetime import datetime, timedelta, timezone
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        fake.seed("api_keys", [
            _key_row(id="expired-boot", created_via="bootstrap",
                     expires_at=past),
            _key_row(id="active-boot", created_via="bootstrap",
                     expires_at=future),
            _key_row(id="expired-recovery", created_via="recovery",
                     expires_at=past),
            _key_row(id="no-expiry", created_via="bootstrap", expires_at=None),
        ])
        r = tc.post("/v1/internal/reconcile", headers=_INTERNAL_HEADERS)
        assert r.status_code == 200, r.text
        assert r.json()["expired_keys_swept"] == 1
        by_id = {row["id"]: row for row in fake.tables["api_keys"]}
        assert by_id["expired-boot"]["revoked_at"] is not None
        assert by_id["active-boot"]["revoked_at"] is None
        assert by_id["expired-recovery"]["revoked_at"] is None
        assert by_id["no-expiry"]["revoked_at"] is None

    def test_rejects_bad_key(self, client):
        tc, _, _ = client
        r = tc.post("/v1/internal/reconcile", headers={"Authorization": "Bearer nope"})
        assert r.status_code == 401

    def test_never_touches_registry(self, client, spy):
        tc, _, _ = client
        r = tc.post("/v1/internal/reconcile", headers=_INTERNAL_HEADERS)
        assert r.status_code == 200, r.text
        spy.assert_clean()


# ── Graph surface: create_graph / list_graphs / list_my_teams ──────────────

class TestGraphSurface:
    def _seed_default_graph(self, fake):
        """Real teams rows always carry graph_name (provision_team requires
        it) — the shared FREE_TEAM fixture predates the column."""
        fake.tables["teams"][0]["graph_name"] = "team_team-free-001"

    def test_create_graph_no_registry_write(self, user_client):
        """E5: _graph_create is env-gated in sdk.py — Supabase mode returns a
        deterministic id without writing a registry Graph node."""
        tc, fake, _ = user_client
        fake.seed("team_memberships", [_owner_membership()])
        r = tc.post("/v1/graphs", json={
            "team_id": "team-free-001", "name": "research"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["kind"] == "custom"
        assert body["graph_name"].startswith("team_team-free-001_g_")
        assert body["graph_id"].startswith("g_")

    def test_list_graphs_derives_default(self, user_client):
        """E7: graph_list derives the default graph from teams.graph_name."""
        tc, fake, _ = user_client
        self._seed_default_graph(fake)
        fake.seed("team_memberships", [_owner_membership()])
        r = tc.get("/v1/graphs?team_id=team-free-001")
        assert r.status_code == 200, r.text
        graphs = r.json()
        assert graphs == [{"graph_id": "default", "name": "default",
                           "kind": "default", "point_count": 0}]

    def test_list_my_teams_uses_derived_graphs(self, user_client):
        """E6: team switcher — graph_count/default_graph_id come from the
        derived default graph, not registry Graph nodes."""
        tc, fake, _ = user_client
        self._seed_default_graph(fake)
        fake.seed("team_memberships", [_owner_membership()])
        r = tc.get("/v1/teams")
        assert r.status_code == 200, r.text
        rows = r.json()
        assert len(rows) == 1
        assert rows[0]["team_id"] == "team-free-001"
        assert rows[0]["graph_count"] == 1
        assert rows[0]["default_graph_id"] == "default"

    def test_graph_quota_counts_default_graph(self, user_client):
        """#765 quota paths: with the default graph derived from
        teams.graph_name, free tier max_graphs=1 → a custom graph 402s (the
        default occupies the slot). Without a graph_name (no default) the
        count is 0 and custom graphs pass — the count comes from Supabase,
        never the registry."""
        tc, fake, _ = user_client
        self._seed_default_graph(fake)
        fake.seed("team_memberships", [_owner_membership()])
        r = tc.post("/v1/graphs", json={
            "team_id": "team-free-001", "name": "g1"})
        assert r.status_code == 402, r.text
        # no default graph → no slot occupied → custom graphs pass
        fake.tables["teams"][0].pop("graph_name", None)
        assert tc.post("/v1/graphs", json={
            "team_id": "team-free-001", "name": "g1"}).status_code == 200
        assert tc.post("/v1/graphs", json={
            "team_id": "team-free-001", "name": "g2"}).status_code == 200

    def test_never_touches_registry(self, user_client, spy):
        tc, fake, _ = user_client
        fake.seed("team_memberships", [_owner_membership()])
        assert tc.post("/v1/graphs", json={
            "team_id": "team-free-001", "name": "research"}).status_code == 200
        assert tc.get("/v1/graphs?team_id=team-free-001").status_code == 200
        assert tc.get("/v1/teams").status_code == 200
        spy.assert_clean()


# ── /internal/provision disabled in Supabase mode ──────────────────────────

class TestInternalProvisionDisabled:
    def test_disabled_in_supabase_mode(self, client):
        tc, _, _ = client
        r = tc.post("/internal/provision", headers=_INTERNAL_HEADERS, json={
            "team_id": "team-x", "team_name": "X",
            "api_key_hash": "h", "created_by": "u"})
        assert r.status_code == 503
        assert "provision_team" in r.json()["detail"]

    def test_never_touches_registry(self, client, spy):
        tc, _, _ = client
        tc.post("/internal/provision", headers=_INTERNAL_HEADERS, json={
            "team_id": "team-x", "team_name": "X",
            "api_key_hash": "h", "created_by": "u"})
        spy.assert_clean()


# ── POST /v1/onboarding/team (Q5 sub-team via provision_team RPC) ──────────

class TestOnboardingTeam:
    def test_subteam_provisions_via_rpc(self, team_client):
        tc, fake, _ = team_client
        r = tc.post("/v1/onboarding/team", json={"name": "subteam"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["graph_name"] == "team_subteam"
        fn, p = fake.rpc_calls[0]
        assert fn == "provision_team"
        assert p["p_user_id"] is None
        assert p["p_identity"].startswith("anon-")
        assert p["p_team_id"] == body["team_id"]
        # onboarding state write went to the seam too (teams row)
        state = next(t for t in fake.tables["teams"]
                     if t["id"] == TEST_TEAM["team_id"])["onboarding_state"]
        assert state["team_created"] is True

    def test_never_touches_registry(self, team_client, spy):
        tc, _, _ = team_client
        r = tc.post("/v1/onboarding/team", json={"name": "subteam"})
        assert r.status_code == 200, r.text
        spy.assert_clean()


# ── Zero-registry sweep (the grep-driven inventory, asserted mechanically) ─

_INVENTORY_ENDPOINTS = [
    # (method, path, json_body, headers, note)
    ("post", "/v1/team/keys", None, None, "create_api_key writer"),
    ("get", "/v1/team/keys", None, None, "list_api_keys reader"),
    ("post", "/v1/agent/signup", {}, None, "agent_signup writer"),
    ("post", "/v1/register",
     {"email": "sweep@example.com", "password": "hunter2secret"},
     None, "register writer"),
    ("post", "/v1/teams", {"name": "sweep-team"}, None, "create_team writer"),
    ("get", "/v1/teams/team-free-001/members", None, None, "member listing"),
    ("post", "/v1/internal/reconcile", None, _INTERNAL_HEADERS, "reconcile"),
    ("post", "/v1/onboarding/team", {"name": "sweep-sub"}, None, "onboarding"),
    ("get", "/v1/graphs?team_id=team-free-001", None, None, "graph_list"),
]


class TestZeroRegistryInventory:
    """Mechanical sweep: EVERY inventory endpoint runs in Supabase mode with
    the registry spy installed — the registry must never be touched,
    regardless of the endpoint's HTTP outcome."""

    @pytest.mark.parametrize("method,path,body,headers,note",
                             _INVENTORY_ENDPOINTS,
                             ids=[e[4] for e in _INVENTORY_ENDPOINTS])
    def test_endpoint_never_touches_registry(self, client, spy, method, path,
                                             body, headers, note):
        tc, fake, _ = client
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": "user-1", "email": "user-1@example.com"}
        app.dependency_overrides[get_current_team] = lambda: dict(TEST_TEAM)
        fake.seed("team_memberships", [
            _owner_membership(),
            {"id": "mem-2", "user_id": "user-2", "team_id": "team-free-001",
             "role": "member", "status": "active", "identity": None},
        ])
        kwargs = {}
        if body is not None:
            kwargs["json"] = body
        if headers is not None:
            kwargs["headers"] = headers
        r = getattr(tc, method)(path, **kwargs)
        # the endpoint must complete (any HTTP status) WITHOUT touching the
        # registry — a spy hit is an AssertionError, which is the failure.
        spy.assert_clean()
        assert r.status_code < 500 or r.status_code in (500,), r.text
