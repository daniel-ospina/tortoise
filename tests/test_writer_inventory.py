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

from tortoise.auth import lookup_hash  # noqa: I001
from tortoise.hosted_api import app, get_current_team, get_current_user

from tests._http_fixtures import patched_tortoise_sdk
from tests.fake_control_plane import ErrorControlPlane, FakeControlPlane
from tests.test_supabase_control import FREE_TEAM, TOKEN, _key_row, _membership_row  # noqa: F401

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


@pytest.fixture
def client(monkeypatch, supabase_env, spy):
    """TestClient in Supabase mode with a temp embedded DB + registry spy."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "inv.db")
        # #2127: shared helper (tests._http_fixtures.patched_tortoise_sdk) —
        # patch __init__ → temp DB + #1950 TORTOISE_DB_PATH pin + close-then-
        # clear at enter; pop-pin → restore __init__ → deterministic anchor
        # close → clear overrides at exit (replaces this file's local
        # _patch_tortoise_sdk_init — the #1497 original).
        with patched_tortoise_sdk(db_path), TestClient(app) as tc:
            yield tc, supabase_env, spy


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
        "user_id": _USER1, "email": "owner@example.com"}
    return tc, fake, spy


def _owner_membership(**overrides) -> dict:
    row = {
        "id": "mem-1", "user_id": _USER1, "team_id": "team-free-001",
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
        # 20260825000001: optional label rides the response (null when unset)
        assert set(body) == {"id", "key", "key_prefix", "created_at", "name"}
        assert body["name"] is None
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

    def test_mint_with_name_writes_row_and_lists(self, team_client):
        """20260825000001: supabase-mode mint with a label stores name on the
        row and returns it from GET /v1/team/keys (the production dashboard
        label input path)."""
        tc, fake, _ = team_client
        r = tc.post("/v1/team/keys", json={"name": "CI deploy"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["name"] == "CI deploy"
        rows = fake.tables["api_keys"]
        assert rows[0]["name"] == "CI deploy"
        listed = tc.get("/v1/team/keys").json()["keys"]
        assert any(k["id"] == body["id"] and k.get("name") == "CI deploy" for k in listed)

    def test_never_touches_registry(self, team_client, spy):
        tc, fake, _ = team_client  # noqa: RUF059
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
        tc, fake, _ = team_client  # noqa: RUF059
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
        tc, fake, _ = team_client  # noqa: RUF059
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


# ── POST /v1/agent/signup (identity path via provision_team_with_token) ──

class TestAgentSignup:
    def test_signup_provisions_identity_path(self, client):
        """#765 writer flip + #1709 token: signup → provision_team_with_token
        RPC with NULL user_id + identity + the signup-token hash;
        teams/membership/api_keys/token rows land; key resolves."""
        tc, fake, _ = client
        r = tc.post("/v1/agent/signup", json={})
        assert r.status_code == 200, r.text
        body = r.json()
        key, team_id, identity = body["key"], body["team_id"], body["identity"]
        assert identity.startswith("anon-")

        # exactly one provision RPC call, identity path, wrapper fn
        assert len(fake.rpc_calls) == 1
        fn, p = fake.rpc_calls[0]
        assert fn == "provision_team_with_token"
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
        # #1709: the minted st_ token is hashed (SHA-256 lookup_hash — never
        # the plaintext) and passed to the wrapper
        tok = body["signup_token"]
        assert tok.startswith("st_")
        assert p["p_signup_token_hash"] == lookup_hash(tok)

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
        # #1709: the token row landed (hash-only; bound to the team)
        token_rows = [t for t in fake.tables.get("agent_signup_tokens", [])
                      if t["team_id"] == team_id]
        assert len(token_rows) == 1
        assert token_rows[0]["token_hash"] == lookup_hash(tok)

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

    def test_rate_limit_query_shape(self, client, monkeypatch):
        """#1081: the old per-identity count was dead (#741 — server-side
        identity is fresh per request) and has been REMOVED. The per-IP
        signup limiter (2/24h) is the compensating control; the 3rd mint
        from one IP 429s in Supabase mode too (mode-independent store)."""
        tc, fake, _ = client  # noqa: RUF059
        monkeypatch.delenv("RATE_LIMIT_DISABLED", raising=False)
        for _ in range(2):
            r = tc.post("/v1/agent/signup", json={"identity": "anon-client-chosen"})
            assert r.status_code == 200, r.text
        r = tc.post("/v1/agent/signup", json={"identity": "anon-client-chosen"})
        assert r.status_code == 429, r.text

    def test_signup_caps_match_free_tier(self, client):
        """#1081 (indicator 3): provision_team's p_* params must mirror
        tier_limits("free") exactly — a pricing-drift regression must never
        silently un-cap anon teams in Supabase mode."""
        from tortoise.pricing import tier_limits
        tc, fake, _ = client
        r = tc.post("/v1/agent/signup", json={})
        assert r.status_code == 200, r.text
        fn, p = next(c for c in fake.rpc_calls if c[0] == "provision_team_with_token")  # noqa: RUF059
        lim = tier_limits("free")
        assert p["p_max_users"] == lim["max_users_per_team"]
        assert p["p_max_graphs"] == lim["max_graphs_per_team"]
        assert p["p_ops_allowance"] == lim["included_write_ops_per_month"]
        assert p["p_graph_size_cap"] == lim["max_graph_nodes"]
        assert p["p_tier"] == "free"


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
        assert body["graph_name"] == f"team_{body['team_id']}"  # #1903: stored name == data-plane namespace

        fn, p = fake.rpc_calls[0]
        assert fn == "provision_team"
        assert p["p_graph_name"] == f"team_{body['team_id']}"
        # persisted teams.graph_name pinned (the round-trip consumers read it)
        assert next(t for t in fake.tables["teams"]
                    if t["id"] == body["team_id"])["graph_name"] == \
            f"team_{body['team_id']}"
        assert p["p_user_id"] == _USER1
        assert p["p_identity"] is None
        assert p["p_team_id"] == body["team_id"]
        assert p["p_team_name"] == "acme"
        # owner membership landed for the JWT user
        mem = [m for m in fake.tables["team_memberships"]
               if m["user_id"] == _USER1 and m["team_id"] == body["team_id"]]
        assert len(mem) == 1 and mem[0]["role"] == "owner"

    def test_create_team_keyless_no_api_keys_row(self, user_client):
        """#1921: POST /v1/teams provisions KEYLESS — no tt_ mint, no
        api_keys row. The old per-call mint persisted only the hash and
        never returned the plaintext — a dead key permanently counted
        against max_api_keys (2 free teams exhausted the cap with zero
        usable keys). Mirror of create_onboarding_team's #1716 keyless
        provision: all-NULL key params → teams + membership, NO key row."""
        tc, fake, _ = user_client
        r = tc.post("/v1/teams", json={"name": "keyless"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert "key" not in body  # the response never carries a key
        tid = body["team_id"]
        fn, p = fake.rpc_calls[0]
        assert fn == "provision_team"
        # all-or-none key guard (migration 20260825214233): all-NULL =
        # keyless — no api_keys row, no max_api_keys slot consumed.
        assert p["p_api_key"] is None
        assert p["p_key_hash"] is None
        assert p["p_lookup_hash"] is None
        assert p["p_key_prefix"] is None
        rows = [k for k in fake.tables["api_keys"] if k["team_id"] == tid]
        assert rows == [], "create_team must not mint a dead api_keys row"

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
        since = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()  # noqa: UP017
        # 3 MEMBER rows (invite accepts) — must NOT trigger the owner limit
        fake.seed("team_memberships", [
            {"id": f"mem-inv-{i}", "user_id": _USER1,
             "team_id": f"team-inv-{i}", "role": "member",
             "status": "active", "created_at": since}
            for i in range(3)
        ])
        r = tc.post("/v1/teams", json={"name": "mine"})
        assert r.status_code == 200, r.text

    def test_rate_limit_3_per_hour(self, user_client):
        tc, fake, _ = user_client
        from datetime import datetime, timedelta, timezone
        since = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()  # noqa: UP017
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
        old = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()  # noqa: UP017
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

    def test_backup_round_trip_dashboard_created_team(self, user_client, monkeypatch):
        """#1903 backup surface: a dashboard-created team's backup resolves
        teams.graph_name (= team_{team_id} post-fix) and dumps the REAL data
        graph (manifest node_count + restore round-trip capture the seeded
        point). Mirrors the pro_backup_client setup (:1006-1031) — POST
        /backups is key-auth (get_current_team) and the tier gate reads the
        dependency dict."""
        import base64 as _b64  # noqa: I001
        import tortoise.hosted_api as ha_mod
        from tortoise import pricing as _pricing
        from tortoise.hosted_backup import MemoryStorage

        tc, fake, _ = user_client  # noqa: RUF059
        monkeypatch.setenv(
            "TORTOISE_BACKUP_KEY", _b64.b64encode(os.urandom(32)).decode()
        )
        store = MemoryStorage()  # SHARED — _backup_storage is called per request
        monkeypatch.setattr(ha_mod, "_backup_storage", lambda: store)
        monkeypatch.setattr(
            _pricing, "daily_backups_enabled", lambda tier: tier == "pro"
        )
        r = tc.post("/v1/teams", json={"name": "acme"})
        assert r.status_code == 200, r.text
        team_id = r.json()["team_id"]
        assert r.json()["graph_name"] == f"team_{team_id}"
        # get_current_team_session honors the get_current_team override
        # (hosted_api.py:1540-1548), so one override covers create + restore.
        app.dependency_overrides[get_current_team] = lambda: dict(
            TEST_TEAM, team_id=team_id, tier="pro", backup_enabled=True)
        # seed the real data graph: bind the raw handle to the EXPLICIT
        # team_{team_id} graph (the same graph the backup dump reads via
        # from_uri) so the seed target is explicit and lane-independent —
        # mirrors test_restore_binds_live_graph_to_teams_graph_name.
        sdk = ha_mod._make_sdk(namespace=team_id)
        try:
            sdk._get_proj().db.select_graph(f"team_{team_id}").query(
                "CREATE (p:Point {id:'seed-1', content:'real decision'})"
            )
        finally:
            sdk.close()
        r = tc.post("/backups")
        assert r.status_code == 201, r.text
        manifest = r.json()
        assert manifest["graph_name"] == f"team_{team_id}"  # stored name wins
        # dump captured non-skip nodes (the provision RPC may co-mint
        # starter PackInstall nodes, so assert >=1, not an exact total)
        assert manifest["node_count"] >= 1, \
            f"dump empty: graph {manifest['graph_name']} — seed/redirect divergence"
        # restore round-trip: the dump captured the seeded node
        backup_key = f"backups/{manifest['backup_id']}/dump.enc"
        r2 = tc.post("/backups/restore",
                     json={"backup_key": backup_key, "confirm": True})
        assert r2.status_code == 200, r2.text
        assert r2.json()["restored"]["nodes"] >= 1, \
            f"restore empty: {r2.json()}"
        # specific-content proof: the seeded point survived the round-trip
        probe = ha_mod._make_sdk(namespace=team_id)
        try:
            rows = probe._get_proj().db.select_graph(
                f"team_{team_id}").query(
                    "MATCH (p:Point {id:'seed-1'}) RETURN count(p)").result_set
            # swap proof: the dump excludes TeamMeta (_EXPORT_SKIP_LABELS), so
            # a post-restore TeamMeta would mean the live graph was never
            # replaced by the restore.
            metas = probe._get_proj().db.select_graph(
                f"team_{team_id}").query(
                    "MATCH (t:TeamMeta) RETURN count(t)").result_set
        finally:
            probe.close()
        assert rows[0][0] == 1
        assert metas[0][0] == 0


# ── Members surface: list / remove / role change ───────────────────────────

class TestMembers:
    def _seed_team(self, fake, team_id="team-free-001"):
        fake.seed("team_memberships", [
            _owner_membership(team_id=team_id),
            {"id": "mem-2", "user_id": _USER2, "team_id": team_id,
             "role": "member", "status": "active", "identity": None},
            {"id": "mem-3", "user_id": None, "team_id": team_id,
             "role": "member", "status": "active", "identity": "anon-abc123"},
        ])

    def test_list_members_active_and_invited(self, user_client):
        tc, fake, _ = user_client
        self._seed_team(fake)
        fake.seed("team_memberships", [
            {"id": "mem-4", "user_id": _USER4, "team_id": "team-free-001",
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
        r = tc.delete(f"/v1/teams/team-free-001/members/{_USER2}")
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
        r = tc.delete(f"/v1/teams/team-free-001/members/{_USER1}")
        assert r.status_code == 409

    def test_remove_unknown_404(self, user_client):
        tc, fake, _ = user_client
        self._seed_team(fake)
        r = tc.delete("/v1/teams/team-free-001/members/ghost")
        assert r.status_code == 404

    def test_change_role(self, user_client):
        tc, fake, _ = user_client
        self._seed_team(fake)
        r = tc.patch(f"/v1/teams/team-free-001/members/{_USER2}",
                     json={"role": "admin"})
        assert r.status_code == 200, r.text
        assert r.json() == {"user_id": _USER2, "role": "admin"}
        mem = next(m for m in fake.tables["team_memberships"]
                   if m["id"] == "mem-2")
        assert mem["role"] == "admin"

    def test_change_owner_role_409(self, user_client):
        tc, fake, _ = user_client
        self._seed_team(fake)
        r = tc.patch(f"/v1/teams/team-free-001/members/{_USER1}",
                     json={"role": "member"})
        assert r.status_code == 409

    def test_never_touches_registry(self, user_client, spy):
        tc, fake, _ = user_client
        self._seed_team(fake)
        assert tc.get("/v1/teams/team-free-001/members").status_code == 200
        assert tc.delete(f"/v1/teams/team-free-001/members/{_USER2}").status_code == 200
        assert tc.patch(f"/v1/teams/team-free-001/members/{_USER2}",
                        json={"role": "member"}).status_code == 200
        spy.assert_clean()


# ── POST /v1/internal/reconcile (expired-bootstrap sweep) ──────────────────

class TestReconcile:
    def test_sweeps_expired_bootstrap_keys(self, client):
        tc, fake, _ = client
        from datetime import datetime, timedelta, timezone
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()  # noqa: UP017
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()  # noqa: UP017
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
        # #1748: seed the session-user context (get_current_team_session
        # carries session_user_id for JWT auth; tests override the dep).
        app.dependency_overrides[get_current_team] = lambda: dict(
            TEST_TEAM, session_user_id="user-1")
        r = tc.post("/v1/onboarding/team", json={"name": "subteam"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["graph_name"] == f"team_{body['team_id']}"  # #1903: stored name == data-plane namespace
        assert "key" not in body  # #1716: the response never carries a key
        fn, p = fake.rpc_calls[0]
        assert fn == "provision_team"
        assert p["p_graph_name"] == f"team_{body['team_id']}"
        # persisted teams.graph_name pinned (the round-trip consumers read it)
        assert next(t for t in fake.tables["teams"]
                    if t["id"] == body["team_id"])["graph_name"] == \
            f"team_{body['team_id']}"
        # #1748: USER path — the session user is the owner member (no
        # throwaway anon-{uuid} identity).
        assert p["p_user_id"] == "user-1"
        assert p["p_identity"] is None
        assert p["p_team_id"] == body["team_id"]
        # #1716: keyless provisioning — all-NULL key params → NO api_keys row
        # attributable to the sub-team (the old per-call tt_ mint was an
        # unrecoverable dead credential: plaintext never returned, hash-only
        # at rest, counted against max_api_keys, unclaimable #1082).
        assert p["p_api_key"] is None
        assert p["p_key_hash"] is None
        assert p["p_lookup_hash"] is None
        assert p["p_key_prefix"] is None
        keys = [k for k in fake.tables["api_keys"]
                if k["team_id"] == body["team_id"]]
        assert keys == []
        # the session user is a REAL owner member (role owner, status active,
        # user_id set, identity NULL) — the RPC's membership upsert, NOT a
        # hand-inserted row.
        mem = [m for m in fake.tables["team_memberships"]
               if m["team_id"] == body["team_id"]]
        assert len(mem) == 1
        assert mem[0]["user_id"] == "user-1"
        assert mem[0].get("identity") is None
        assert mem[0]["role"] == "owner"
        assert mem[0]["status"] == "active"
        # onboarding state write went to the seam too (teams row)
        state = next(t for t in fake.tables["teams"]
                     if t["id"] == TEST_TEAM["team_id"])["onboarding_state"]
        assert state["team_created"] is True

    def test_subteam_requires_session_user(self, team_client):
        """#1748: no session user on the team context (session_user_id or
        key created_by) → 403 — never a throwaway-identity orphan team."""
        tc, fake, _ = team_client
        app.dependency_overrides[get_current_team] = lambda: dict(
            TEST_TEAM, session_user_id=None)
        r = tc.post("/v1/onboarding/team", json={"name": "orphan"})
        assert r.status_code == 403, r.text
        assert fake.rpc_calls == []  # no provision attempted
        assert all(t["id"] != "orphan" for t in fake.tables["teams"])

    def test_key_auth_owner_from_key_creator(self, client):
        """#1748 key-auth branch: a real Bearer tt_ key (no session JWT —
        the dashboard wizard authenticates with a session-minted bootstrap/
        recovery key) provisions the sub-team for the key's CREATOR
        (api_keys.created_by = the user UUID — team dicts from
        resolve_api_key carry it). The created_by='api' sentinel (a key
        minted by create_api_key's key-auth/override path, #1511) is NOT a
        real user → 403, never an owner-less orphan."""
        tc, fake, _ = client
        # a session-minted key: created_by = the session user's UUID
        key = "tt_" + "ab" * 16
        fake.seed("api_keys", [_key_row(
            id="k-user", created_by="user-1",
            lookup_hash=lookup_hash(key), key_prefix=key[:10])])
        r = tc.post("/v1/onboarding/team", json={"name": "keyowner"},
                    headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 200, r.text
        fn, p = fake.rpc_calls[0]
        assert fn == "provision_team"
        assert p["p_user_id"] == "user-1"
        assert p["p_identity"] is None
        # the 'api' sentinel creator → 403
        key2 = "tt_" + "cd" * 16
        fake.seed("api_keys", [_key_row(
            id="k-api", created_by="api",
            lookup_hash=lookup_hash(key2), key_prefix=key2[:10])])
        r2 = tc.post("/v1/onboarding/team", json={"name": "apikey"},
                     headers={"Authorization": f"Bearer {key2}"})
        assert r2.status_code == 403, r2.text
    def test_keyless_subteam_session_key_mint_still_works(self, team_client):
        """#1748: a keyless onboarding sub-team has NO api_keys row
        attributable to it, yet the REAL journey works — the session user is
        the owner member (provisioned on the USER path, NOT hand-inserted),
        so a session-key mint (POST /v1/session/key) resolves the
        membership, writes the api_keys row itself, and the minted key
        resolves on REST. The sub-team is listable and deletable by its
        owner — the full #1716 escape hatch, now actually reachable."""
        tc, fake, _ = team_client
        app.dependency_overrides[get_current_team] = lambda: dict(
            TEST_TEAM, session_user_id=_USER1)
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": _USER1, "email": "user-1@example.com"}
        r = tc.post("/v1/onboarding/team", json={"name": "subteam"})
        assert r.status_code == 200, r.text
        sub_team_id = r.json()["team_id"]
        assert [k for k in fake.tables["api_keys"]
                if k["team_id"] == sub_team_id] == []
        # REAL membership grant from provisioning — no hand-inserted row:
        # the fake's provision_team emulation wrote it via p_user_id.
        assert any(m["team_id"] == sub_team_id
                   and m["user_id"] == _USER1
                   and m["role"] == "owner"
                   and m["status"] == "active"
                   for m in fake.tables["team_memberships"])
        # session-key mint resolves the owner membership → 200
        r2 = tc.post("/v1/session/key", json={"purpose": "recovery"})
        assert r2.status_code == 200, r2.text
        key = r2.json()["key"]
        assert key.startswith("tt_")
        rows = [k for k in fake.tables["api_keys"]
                if k["team_id"] == sub_team_id]
        assert len(rows) == 1
        assert rows[0]["lookup_hash"] == lookup_hash(key)
        assert rows[0]["created_via"] == "recovery"
        # the minted key resolves on REST (api_keys.lookup_hash path)
        app.dependency_overrides.clear()
        r3 = tc.get("/v1/team", headers={"Authorization": f"Bearer {key}"})
        assert r3.status_code == 200, r3.text
        assert r3.json()["team_id"] == sub_team_id
        # the sub-team is LISTABLE by the owner (GET /v1/teams)
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": _USER1, "email": "user-1@example.com"}
        r4 = tc.get("/v1/teams")
        assert r4.status_code == 200, r4.text
        assert any(t["team_id"] == sub_team_id for t in r4.json())
        # and DELETABLE by the owner (DELETE /v1/teams/{id})
        r5 = tc.delete(f"/v1/teams/{sub_team_id}")
        assert r5.status_code in (200, 202), r5.text
        # the key is revoked by the delete cascade → auth fails closed
        app.dependency_overrides.clear()
        r6 = tc.get("/v1/team", headers={"Authorization": f"Bearer {key}"})
        assert r6.status_code == 401, r6.text

    def test_never_touches_registry(self, team_client, spy):
        tc, _, _ = team_client
        # #1748: the onboarding sub-team is provisioned on the USER path —
        # seed the session-user context so the write takes the RPC path.
        app.dependency_overrides[get_current_team] = lambda: dict(
            TEST_TEAM, session_user_id="user-1")
        r = tc.post("/v1/onboarding/team", json={"name": "subteam"})
        assert r.status_code == 200, r.text
        spy.assert_clean()


# ── Zero-registry sweep (the grep-driven inventory, asserted mechanically) ─

_INVENTORY_ENDPOINTS = [
    # (method, path, json_body, headers, note)
    ("post", "/v1/team/keys", None, None, "create_api_key writer"),
    ("get", "/v1/team/keys", None, None, "list_api_keys reader"),
    ("post", "/v1/agent/signup", {}, None, "agent_signup writer"),
    ("post", "/v1/agent/token/revoke",
     {"signup_token": "st_" + "ab" * 32}, None, "agent_token_revoke writer"),
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
            "user_id": _USER1, "email": "owner@example.com"}
        # #1748: seed the session user on the team context (onboarding
        # sub-team provisioning takes the USER path → the sweep exercises
        # the real RPC write, not a 403 short-circuit).
        app.dependency_overrides[get_current_team] = lambda: dict(
            TEST_TEAM, session_user_id=_USER1)
        fake.seed("team_memberships", [
            _owner_membership(),
            {"id": "mem-2", "user_id": _USER2, "team_id": "team-free-001",
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


# ── /backups + /backups/restore in Supabase mode (#924) ─────────────────────

class TestBackupEndpointsSupabaseGraphName:
    """#924: the on-demand backup endpoints resolve the graph name from the
    control plane via the SAME seam as the sweep (backup_sweep.team_graph_name)
    — Supabase mode reads teams.graph_name (SDK team creation names graphs
    team_{name}, NOT team_{id}; #768), registry mode is team_{id}. The old
    team_{id} hardcode targeted a nonexistent graph for SDK-created teams
    (P0-guard trip on the sweep, cross-graph rejection on restore).
    """

    @pytest.fixture
    def pro_backup_client(self, client, monkeypatch):
        """Supabase-mode client, Pro tier, in-memory backup storage, and a
        teams row whose graph_name differs from the team_{id} convention."""
        import base64 as _b64  # noqa: I001
        import tortoise.hosted_api as ha_mod
        from tortoise import pricing as _pricing
        from tortoise.hosted_backup import MemoryStorage

        tc, fake, spy = client  # noqa: RUF059
        monkeypatch.setenv(
            "TORTOISE_BACKUP_KEY", _b64.b64encode(os.urandom(32)).decode()
        )
        store = MemoryStorage()  # SHARED — _backup_storage is called per request
        monkeypatch.setattr(ha_mod, "_backup_storage", lambda: store)
        # Backups gate: pro passes (pricing.json still marks daily_backups
        # "planned", so the allowlist is patched like test_hosted_api does).
        monkeypatch.setattr(
            _pricing, "daily_backups_enabled", lambda tier: tier == "pro"
        )
        # SDK-created team: the graph is named per teams.graph_name — NOT
        # team_{id} (#768). team_myapp != team_team-pro-924, so a team_{id}
        # hardcode is provably wrong here.
        fake.seed("teams", [{
            "id": "team-pro-924", "name": "myapp",
            "graph_name": "team_myapp", "tier": "pro", "backup_enabled": True,
        }])
        app.dependency_overrides[get_current_team] = lambda: dict(
            TEST_TEAM, team_id="team-pro-924", tier="pro")
        yield tc, fake, store
        app.dependency_overrides.clear()

    def test_backup_create_uses_teams_graph_name(self, pro_backup_client):
        """POST /backups names the archive per teams.graph_name, not team_{id}."""
        tc, fake, _ = pro_backup_client  # noqa: RUF059
        r = tc.post("/backups")
        assert r.status_code == 201, r.text
        manifest = r.json()
        assert manifest["team_id"] == "team-pro-924"
        # The teams row wins: team_myapp, never team_team-pro-924.
        assert manifest["graph_name"] == "team_myapp"
        assert manifest["backup_id"].startswith("team-pro-924/")

    def test_backup_restore_uses_teams_graph_name(self, pro_backup_client):
        """Round trip: the restore resolves the SAME teams.graph_name, so the
        backup it just created passes the cross-graph isolation check (the
        old team_{id} hardcode rejected it with a 400 cross-graph error)."""
        tc, fake, _ = pro_backup_client  # noqa: RUF059
        r = tc.post("/backups")
        assert r.status_code == 201, r.text
        manifest = r.json()
        backup_key = f"backups/{manifest['backup_id']}/dump.enc"
        r = tc.post(
            "/backups/restore", json={"backup_key": backup_key, "confirm": True}
        )
        assert r.status_code == 200, r.text
        assert r.json()["restored"] == {"nodes": 0, "edges": 0}

    def test_restore_binds_live_graph_to_teams_graph_name(self, pro_backup_client):
        """The restore's live-graph bound is teams.graph_name, not team_{id}:
        the on-demand backup dumps the REAL team_myapp graph (node_count > 0)
        and a restore round-trips against it (200, node restored). With the
        old team_{id} hardcode the backup dumped the phantom team_{id} graph
        (always empty) — a real graph named team_{name} would never be
        backed up, exactly the #924 data-loss hazard (review P1 #935: the
        fix binds the dump to the resolved graph, so the data IS captured)."""
        import tortoise.hosted_api as ha_mod

        tc, fake, _ = pro_backup_client  # noqa: RUF059
        # Seed the REAL (SDK-created) live graph — named team_myapp per
        # teams.graph_name, NOT team_{id} (#768).
        sdk = ha_mod._make_sdk(namespace=f"test_writer_team_pro_924_{os.urandom(4).hex()}")
        try:
            live = sdk._get_proj().db.select_graph("team_myapp")
            live.query(
                "CREATE (p:Point {id:'seed-1', content:'real decision'})"
            )
        finally:
            sdk.close()
        # The backup dumps the RESOLVED graph (teams.graph_name), so the
        # seeded node IS captured (review P1 #935: the old dump came from the
        # SDK-namespace phantom team_{id} graph and was always empty).
        r = tc.post("/backups")
        assert r.status_code == 201, r.text
        manifest = r.json()
        assert manifest["graph_name"] == "team_myapp"
        assert manifest["node_count"] == 1, (
            "backup must dump the resolved teams.graph_name graph, not the "
            "phantom team_{id} SDK namespace"
        )
        backup_key = f"backups/{manifest['backup_id']}/dump.enc"
        # Restore binds the live graph = team_myapp → the non-empty backup
        # round-trips (200, 1 node restored) — with the old phantom-team_{id}
        # hardcode the backup was empty and the restore "succeeded" on
        # nothing, silently losing the real graph.
        r = tc.post(
            "/backups/restore", json={"backup_key": backup_key, "confirm": True}
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["restored"]["nodes"] == 1
        assert body["restored"]["edges"] == 0
        # #1709 (docker-lane hygiene): team_myapp is a SHARED graph in the
        # test matrix — the seed + restore leftovers must be cleaned up or
        # the next test in this class (backup_restore, which expects the
        # graph EMPTY) fails on stale nodes.
        try:
            sdk = ha_mod._make_sdk(namespace="registry")
            live = sdk._get_proj().db.select_graph("team_myapp")
            live.query("MATCH (n) DELETE n")
            sdk.close()
        except Exception:
            pass

    def test_backup_create_fail_closed_when_team_vanished(self, pro_backup_client):
        """A team missing from teams (or without graph_name) 503s — never a
        backup of a guessed/wrong graph."""
        tc, fake, _ = pro_backup_client  # noqa: RUF059
        app.dependency_overrides[get_current_team] = lambda: dict(
            TEST_TEAM, team_id="team-ghost", tier="pro")
        try:
            r = tc.post("/backups")
            assert r.status_code == 503, r.text
            assert "vanished from the control plane" in r.json()["detail"]
        finally:
            app.dependency_overrides.clear()


# #1719 (codebase-review P1-1): JWT subjects + team_memberships.user_id are
# real UUIDs in prod (uuid column) — non-UUID literals would 22P02. Identity
# anchors (anon-*) stay non-UUID to exercise the identity path.
_USER1 = "9f2c1a40-0000-4a00-8000-000000000001"
_USER2 = "9f2c1a40-0000-4a00-8000-000000000002"
_USER4 = "9f2c1a40-0000-4a00-8000-000000000004"

