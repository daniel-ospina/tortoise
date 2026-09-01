"""#1877 — the per-person "one free team" entitlement.

Covers both control-plane lanes:
- Supabase lane: the count_active_free_memberships helper + the POST
  /v1/teams gate matrix + the onboarding re-entry guard (FakeControlPlane,
  zero network — mirrors tests/test_writer_inventory.py).
- Registry lane: the tier='free' proxy helper + the gate ordering (429 →
  409 → 402) against the embedded registry (mirrors test_invites_http.py).

UX-research note: this is backend-only — no user-facing surface in this
file (the dashboard gate-on-click UX is covered by the e2e suite).
"""
from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from tests.fake_control_plane import FakeControlPlane
from tests.test_supabase_control import FREE_TEAM, _membership_row
from tortoise.hosted_api import app, get_current_user

_USER1 = "9f2c1a40-0000-4a00-8000-000000000001"
_UPGRADE_MSG = "Create another team requires a paid plan"


def _count_free(user_id: str) -> int:
    """#1954: the entitlement helper is async (the TOCTOU read window) —
    sync test callers wrap it in asyncio.run."""
    import asyncio as _a

    from tortoise.hosted_api import _count_active_free_memberships
    return _a.run(_count_active_free_memberships(user_id))


# ── Supabase lane ───────────────────────────────────────────────────────────


@pytest.fixture
def fake() -> FakeControlPlane:
    cp = FakeControlPlane()
    cp.seed("teams", [dict(FREE_TEAM)])
    return cp


@pytest.fixture
def supabase_env(monkeypatch, fake) -> FakeControlPlane:
    import tortoise.supabase_control as sc
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc_role_key_test")
    monkeypatch.setattr(sc, "get_control_plane", lambda: fake)
    return fake


def _close_keepalive_anchors(module) -> None:
    """Deterministically close every keepalive anchor (SHUTDOWN SAVE).

    #2090: replaces the clear-without-close leak (each eviction shut the
    redislite daemon down mid-test → 403). # mirrors
    tests/test_hosted_api.py:144-153 — keep in sync.
    """
    for ns in list(module._FALLBACK_KEEPALIVE):
        anchor = module._FALLBACK_KEEPALIVE.pop(ns, None)
        if anchor is not None:
            try:
                anchor.close()
            except Exception:
                pass


@pytest.fixture
def client(monkeypatch, supabase_env):
    """TestClient in Supabase mode with a temp embedded DB (the gate's
    graph mint touches the SDK anchor)."""
    import tortoise.hosted_api as ha_mod
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "inv.db")
        _orig = ha_mod.TortoiseSDK.__init__
        _fallback = ha_mod._FALLBACK_KEEPALIVE

        def _patched(self, db_path_arg=None, *, namespace=None, **kwargs):
            _orig(self, db_path, namespace=namespace)

        ha_mod.TortoiseSDK.__init__ = _patched
        ha_mod._FALLBACK_KEEPALIVE.clear()
        # #1950: pin TORTOISE_DB_PATH to the SAME temp DB the patched init
        # forces, so _make_sdk's keepalive anchor path matches and the anchor
        # is REUSED instead of evicted + closed on every registry access (each
        # eviction shut the redislite daemon down mid-test, losing the seed
        # between this fixture's write and the gate read → 403).
        os.environ["TORTOISE_DB_PATH"] = db_path
        try:
            with TestClient(app) as tc:
                yield tc, supabase_env
        finally:
            os.environ.pop("TORTOISE_DB_PATH", None)
            ha_mod.TortoiseSDK.__init__ = _orig
            _close_keepalive_anchors(ha_mod)  # replaces clear() — close-before-clear
            app.dependency_overrides.clear()


@pytest.fixture
def user_client(client):
    tc, fake = client
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": _USER1, "email": "owner@example.com"}
    return tc, fake


def _seed_team(fake, team_id: str, tier: str = "free",
               subscription_status=None) -> None:
    fake.seed("teams", [dict(FREE_TEAM, id=team_id, name=team_id, tier=tier,
                             subscription_status=subscription_status)])


def _seed_membership(fake, team_id: str, status: str = "active",
                     user_id: str = _USER1) -> None:
    fake.seed("team_memberships",
              [_membership_row(user_id=user_id, team_id=team_id,
                               status=status)])


class TestCountActiveFreeMemberships:
    def test_free_team_counted(self, fake):
        from tortoise.supabase_control import count_active_free_memberships
        _seed_team(fake, "team-free-a")
        _seed_membership(fake, "team-free-a")
        assert count_active_free_memberships(fake, _USER1) == 1

    def test_paid_team_not_counted(self, fake):
        from tortoise.supabase_control import count_active_free_memberships
        _seed_team(fake, "team-paid", tier="pro", subscription_status="active")
        _seed_membership(fake, "team-paid")
        assert count_active_free_memberships(fake, _USER1) == 0

    def test_past_due_trialing_not_counted(self, fake):
        """The active set is {active, past_due, trialing} — a paying-but-
        past-due team is NOT a free slot (review P2: a truncated set would
        silently 402 paying users)."""
        from tortoise.supabase_control import count_active_free_memberships
        for status in ("past_due", "trialing"):
            _seed_team(fake, f"team-{status}", tier="pro",
                       subscription_status=status)
            _seed_membership(fake, f"team-{status}")
        assert count_active_free_memberships(fake, _USER1) == 0

    def test_removed_membership_excluded(self, fake):
        from tortoise.supabase_control import count_active_free_memberships
        _seed_team(fake, "team-removed")
        _seed_membership(fake, "team-removed", status="removed")
        assert count_active_free_memberships(fake, _USER1) == 0

    def test_dangling_membership_skipped(self, fake):
        """A membership whose team row is missing (the #302 soft-delete
        sweep) must be skipped — not counted, never a 500."""
        from tortoise.supabase_control import count_active_free_memberships
        _seed_membership(fake, "team-purged")
        assert count_active_free_memberships(fake, _USER1) == 0

    def test_non_uuid_user_id_returns_0(self, fake):
        """#1719 shape-gate: a non-UUID would 22P02 → PostgREST 500 if it
        reached a user_id eq filter — return 0 without querying."""
        from tortoise.supabase_control import count_active_free_memberships
        _seed_team(fake, "team-free-b")
        _seed_membership(fake, "team-free-b")
        assert count_active_free_memberships(fake, "reg-abc123") == 0


class TestCreateTeamEntitlement:
    def test_zero_teams_200(self, user_client):
        tc, _fake = user_client
        r = tc.post("/v1/teams", json={"name": "first"})
        assert r.status_code == 200, r.text

    def test_all_paid_200(self, user_client):
        tc, fake = user_client
        _seed_team(fake, "team-paid", tier="pro", subscription_status="active")
        _seed_membership(fake, "team-paid")
        r = tc.post("/v1/teams", json={"name": "second"})
        assert r.status_code == 200, r.text  # the new team is the 1 free slot

    def test_one_free_402(self, user_client):
        tc, fake = user_client
        _seed_team(fake, "team-free-a")
        _seed_membership(fake, "team-free-a")
        r = tc.post("/v1/teams", json={"name": "second"})
        assert r.status_code == 402
        assert _UPGRADE_MSG in r.json()["detail"]
        assert isinstance(r.json()["detail"], str)

    def test_free_plus_paid_402(self, user_client):
        """free+paid → 402: the new team would start Free → 2 free teams."""
        tc, fake = user_client
        _seed_team(fake, "team-free-a")
        _seed_membership(fake, "team-free-a")
        _seed_team(fake, "team-paid", tier="pro", subscription_status="active")
        _seed_membership(fake, "team-paid")
        r = tc.post("/v1/teams", json={"name": "third"})
        assert r.status_code == 402
        assert _UPGRADE_MSG in r.json()["detail"]

    def test_dup_name_free_capped_409_not_402(self, user_client):
        """Ordering pinned: 429 → 409 → 402 — a free-capped user creating a
        duplicate name gets 409, not 402."""
        tc, fake = user_client
        _seed_team(fake, "team-free-a")
        _seed_membership(fake, "team-free-a")
        fake.seed("teams", [dict(FREE_TEAM, id="t-dup", name="acme")])
        r = tc.post("/v1/teams", json={"name": "acme"})
        assert r.status_code == 409
        assert "already exists" in r.json()["detail"]

    def test_rate_limit_429_first(self, user_client):
        tc, fake = user_client
        since = (datetime.now(UTC) - timedelta(minutes=30)).isoformat()
        for i in range(3):
            _seed_team(fake, f"team-{i}")
            fake.seed("team_memberships", [
                _membership_row(user_id=_USER1, team_id=f"team-{i}",
                                role="owner", created_at=since)])
        r = tc.post("/v1/teams", json={"name": "fourth"})
        assert r.status_code == 429

    def test_onboarding_patch_cannot_reset_team_created(self, team_client_factory):
        """#1877 security P1: the re-entry guard is SERVER-authoritative —
        a PATCH resetting team_created:false must not re-open the
        unlimited-free-sub-team bypass."""
        from tortoise.hosted_api import get_current_team_session, get_current_team_session_ungated
        tc, _fake = team_client_factory
        dep = dict(FREE_TEAM, team_id="team-free-001", tier="free",
                   session_user_id=_USER1)
        app.dependency_overrides[get_current_team_session] = lambda: dict(dep)
        app.dependency_overrides[get_current_team_session_ungated] = lambda: dict(dep)
        r1 = tc.post("/v1/onboarding/team", json={"name": "subteam"})
        assert r1.status_code == 200, r1.text
        # attempt the client reset via the PATCH surface
        rp = tc.patch("/v1/onboarding/state", json={"team_created": False})
        assert rp.status_code == 200, rp.text
        r2 = tc.post("/v1/onboarding/team", json={"name": "subteam2"})
        assert r2.status_code == 409, "the guard must survive a PATCH reset attempt"

    def test_onboarding_second_call_409(self, team_client_factory):
        """P0 re-entry guard (review P0 fix): the wizard creates the
        sub-team ONCE. A second call through the PRODUCTION-SHAPED
        dependency (no fabricated onboarding_state — the dep dict never
        carries it) reads the PERSISTED team_created state and 409s — no
        unlimited free sub-team minting via this endpoint."""
        from tortoise.hosted_api import get_current_team_session
        tc, _fake = team_client_factory
        app.dependency_overrides[get_current_team_session] = lambda: dict(
            FREE_TEAM, team_id="team-free-001", tier="free",
            session_user_id=_USER1)
        r1 = tc.post("/v1/onboarding/team", json={"name": "subteam"})
        assert r1.status_code == 200, r1.text
        r2 = tc.post("/v1/onboarding/team", json={"name": "subteam2"})
        assert r2.status_code == 409
        assert "already created" in r2.json()["detail"]


# ── Registry lane (selfhost) ────────────────────────────────────────────────


@pytest.fixture
def reg_client(monkeypatch):
    """Client in REGISTRY mode with a temp embedded registry db."""
    import tortoise.hosted_api as ha_mod
    monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "registry")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "reg.db")
        _orig = ha_mod.TortoiseSDK.__init__
        _fallback = ha_mod._FALLBACK_KEEPALIVE

        def _patched(self, db_path_arg=None, *, namespace=None, **kwargs):
            _orig(self, db_path, namespace=namespace)

        ha_mod.TortoiseSDK.__init__ = _patched
        ha_mod._FALLBACK_KEEPALIVE.clear()
        # #1950: pin TORTOISE_DB_PATH (see client fixture) — the anchor is
        # reused, not evicted, on every registry access.
        os.environ["TORTOISE_DB_PATH"] = db_path
        try:
            with TestClient(app) as tc:
                # #2090: the dropped _make_sdk SDK below is benign — the same
                # call's anchor eagerly holds a connection (count >= 2 at GC →
                # disconnect-only, never NOSAVE).
                reg = ha_mod._make_sdk(namespace="registry")._get_registry()
                yield tc, reg
        finally:
            os.environ.pop("TORTOISE_DB_PATH", None)
            ha_mod.TortoiseSDK.__init__ = _orig
            _close_keepalive_anchors(ha_mod)  # replaces clear() — close-before-clear
            app.dependency_overrides.clear()


def _reg_seed(reg, team_id: str, tier: str = "free"):
    reg.query(
        "CREATE (t:Team {id:$id, name:$id, tier:$tier})",
        params={"id": team_id, "tier": tier},
    )


def _reg_member(reg, team_id: str, user_id: str = _USER1):
    reg.query(
        "CREATE (m:Membership {user_id:$uid, team_id:$tid, role:'owner', "
        "status:'active', created_at:'2026-08-01T00:00:00+00:00'})",
        params={"uid": user_id, "tid": team_id},
    )


class TestRegistryEntitlement:
    def test_helper_tier_free_counted(self, reg_client):
        _tc, reg = reg_client
        _reg_seed(reg, "team-free-r")
        _reg_member(reg, "team-free-r")
        assert _count_free(_USER1) == 1

    def test_helper_tier_pro_not_counted(self, reg_client):
        _tc, reg = reg_client
        _reg_seed(reg, "team-pro-r", tier="pro")
        _reg_member(reg, "team-pro-r")
        assert _count_free(_USER1) == 0

    def test_create_free_capped_402_no_team_minted(self, reg_client):
        """The gate fires BEFORE team_create — assert no Team node exists."""
        tc, reg = reg_client
        _reg_seed(reg, "team-free-r")
        _reg_member(reg, "team-free-r")
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": _USER1, "email": "owner@example.com"}
        try:
            r = tc.post("/v1/teams", json={"name": "blocked"})
        finally:
            app.dependency_overrides.clear()
        assert r.status_code == 402
        assert _UPGRADE_MSG in r.json()["detail"]
        rows = reg.query("MATCH (t:Team {name:'blocked'}) RETURN count(t)").result_set
        assert rows[0][0] == 0, "no team must be minted when gated"

    def test_create_free_capped_dup_409(self, reg_client):
        """Registry ordering: 429 → 409 (dup pre-check) → 402."""
        tc, reg = reg_client
        _reg_seed(reg, "team-free-r")
        _reg_member(reg, "team-free-r")
        _reg_seed(reg, "t-dup", tier="free")
        reg.query("MATCH (t:Team {id:'t-dup'}) SET t.name='acme'")
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": _USER1, "email": "owner@example.com"}
        try:
            r = tc.post("/v1/teams", json={"name": "acme"})
        finally:
            app.dependency_overrides.clear()
        assert r.status_code == 409
        assert "already exists" in r.json()["detail"]

    def test_create_team_keyless_registry(self, reg_client):
        """#1921 registry-lane parity: POST /v1/teams provisions KEYLESS
        (mint_key=False, the create_onboarding_team #1716 shape) — no tt_
        mint, no api_key hash on the Team node, no APIKey node. The old
        default mint persisted a hash whose plaintext was never returned
        — a dead key counted against max_api_keys."""
        tc, reg = reg_client
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": _USER1, "email": "owner@example.com"}
        try:
            r = tc.post("/v1/teams", json={"name": "keyless"})
        finally:
            app.dependency_overrides.clear()
        assert r.status_code == 200, r.text
        body = r.json()
        assert "key" not in body  # the response never carries a key
        tid = body["team_id"]
        rows = reg.query(
            "MATCH (t:Team {id:$tid}) RETURN t.id, t.api_key",
            params={"tid": tid},
        ).result_set
        assert len(rows) == 1
        assert rows[0][1] is None  # no dead key hash on the Team node
        n_keys = reg.query(
            "MATCH (k:APIKey {team_id:$tid}) RETURN count(k)",
            params={"tid": tid},
        ).result_set[0][0]
        assert n_keys == 0  # no APIKey node minted for the team

    def test_create_membership_primed_by_team_create(self, reg_client):
        """#1877 second-model P1: the owner Membership is created INSIDE
        team_create (no post-hoc swallow) — so after a 0-team create, the
        count is primed and a SECOND create 402s. A swallowed membership
        failure would leave the team uncounted → unlimited free teams."""
        tc, reg = reg_client
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": _USER1, "email": "owner@example.com"}
        try:
            r1 = tc.post("/v1/teams", json={"name": "first"})
            assert r1.status_code == 200, r1.text
            # the owner membership landed (primed the gate)
            rows = reg.query(
                "MATCH (m:Membership {user_id:$uid, status:'active'}) "
                "RETURN count(m)", params={"uid": _USER1},
            ).result_set
            assert rows[0][0] == 1, "team_create must create the owner membership"
            r2 = tc.post("/v1/teams", json={"name": "second"})
            assert r2.status_code == 402
        finally:
            app.dependency_overrides.clear()


@pytest.fixture
def team_client_factory(client):
    tc, fake = client
    return tc, fake


# ── #1954: concurrent TOCTOU tests ─────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_locks():
    """#1954 review P2: the memoized asyncio locks bind to the event loop
    on first CONTENDED acquisition — tests run the app under fresh
    asyncio.run loops, so clear them between tests to avoid cross-loop
    RuntimeError."""
    import tortoise.hosted_api as _ha
    _ha._TEAM_CREATE_LOCKS.clear()
    yield
    _ha._TEAM_CREATE_LOCKS.clear()


class TestConcurrentTeamCreationTOCTOU:
    """#1954 — the "one free team" check+provision is read-then-write:
    concurrent POST /v1/teams (or /v1/onboarding/team) requests can all
    read count==0 (and the 429 owner-membership count sees 0 too) then all
    provision → multiple free teams. The fix is an in-process per-user
    asyncio lock (_team_create_lock) around the check+provision in every
    create_team lane + the onboarding re-entry guard.

    NOTE (review P1, honest framing): the single-process hosted lane's
    check+provision is ATOMIC today (sync control-plane calls — the
    count helper is async with an explicit yield, so the window becomes
    real the moment any await enters the lane). These tests are therefore
    OUTCOME-INVARIANT regression guards under concurrent dispatch
    (exactly one minted / the rest gated) rather than race reproducers;
    the multi-worker gap (per-process locks) is filed as a separate
    DB-constraint follow-up."""

    def test_concurrent_create_team_one_minted(self, user_client):
        _tc, fake = user_client
        import asyncio

        import httpx

        async def _burst():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport,
                                         base_url="http://test") as ac:
                r1, r2 = await asyncio.gather(
                    ac.post("/v1/teams", json={"name": "alpha"}),
                    ac.post("/v1/teams", json={"name": "beta"}),
                )
                return r1, r2

        r1, r2 = asyncio.run(_burst())
        statuses = sorted((r1.status_code, r2.status_code))
        assert statuses == [200, 402], (r1.text, r2.text)
        denied = r1 if r1.status_code == 402 else r2
        assert _UPGRADE_MSG in denied.json()["detail"]
        # exactly one team minted + exactly one owner membership (the gate
        # is primed — a third sequential create would 402 too)
        teams = fake.query("teams")
        minted = [t for t in teams if t.get("name") in ("alpha", "beta")]
        assert len(minted) == 1, f"expected exactly one minted team: {minted}"
        mems = fake.query("team_memberships",
                          filters=[("user_id", "eq", _USER1)])
        assert len(mems) == 1, "exactly one owner membership must exist"

    def test_concurrent_create_team_one_minted_registry(self, reg_client):
        """Registry (selfhost) lane — same invariant. NOTE: the registry
        lane may run MULTI-PROCESS; the in-process lock is the single-
        process guard, DB-level enforcement is the multi-process backstop
        (documented in _team_create_lock)."""
        _tc, reg = reg_client
        import asyncio

        import httpx

        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": _USER1, "email": "owner@example.com"}
        try:
            async def _burst():
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(transport=transport,
                                             base_url="http://test") as ac:
                    r1, r2 = await asyncio.gather(
                        ac.post("/v1/teams", json={"name": "gamma"}),
                        ac.post("/v1/teams", json={"name": "delta"}),
                    )
                    return r1, r2

            r1, r2 = asyncio.run(_burst())
        finally:
            app.dependency_overrides.clear()
        statuses = sorted((r1.status_code, r2.status_code))
        assert statuses == [200, 402], (r1.text, r2.text)
        denied = r1 if r1.status_code == 402 else r2
        assert _UPGRADE_MSG in denied.json()["detail"]
        rows = reg.query(
            "MATCH (t:Team) WHERE t.name IN ['gamma','delta'] RETURN count(t)"
        ).result_set
        assert rows[0][0] == 1, "exactly one Team node must be minted"
        rows = reg.query(
            "MATCH (m:Membership {user_id:$uid, status:'active'}) "
            "RETURN count(m)", params={"uid": _USER1},
        ).result_set
        assert rows[0][0] == 1, "exactly one owner membership must exist"

    def test_concurrent_onboarding_sub_team_one_minted(self, team_client_factory):
        """The onboarding re-entry guard is read-then-write too: a
        concurrent double-call must mint exactly ONE sub-team (second gets
        the 409 "Sub-team already created" — different names so the
        duplicate-name 409 cannot mask the guard)."""
        from tortoise.hosted_api import get_current_team_session
        _tc, fake = team_client_factory
        app.dependency_overrides[get_current_team_session] = lambda: dict(
            FREE_TEAM, team_id="team-free-001", tier="free",
            session_user_id=_USER1)
        import asyncio

        import httpx

        try:
            async def _burst():
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(transport=transport,
                                             base_url="http://test") as ac:
                    r1, r2 = await asyncio.gather(
                        ac.post("/v1/onboarding/team",
                                json={"name": "subalpha"}),
                        ac.post("/v1/onboarding/team",
                                json={"name": "subbeta"}),
                    )
                    return r1, r2

            r1, r2 = asyncio.run(_burst())
        finally:
            app.dependency_overrides.clear()
        statuses = sorted((r1.status_code, r2.status_code))
        assert statuses == [200, 409], (r1.text, r2.text)
        denied = r1 if r1.status_code == 409 else r2
        assert "already created" in denied.json()["detail"]
        # exactly one sub-team minted (main team-free-001 + one sub-team row)
        teams = fake.query("teams")
        minted = [t for t in teams
                  if t.get("name") in ("subalpha", "subbeta")]
        assert len(minted) == 1, f"expected exactly one sub-team: {minted}"

    def test_lock_is_per_user(self):
        """#1954 throughput decision: the lock is keyed by user_id — the
        same user's concurrent check+provision calls serialize; different
        users do NOT share a lock (a single global lock would serialize
        every tenant's team creation)."""
        from tortoise.hosted_api import _team_create_lock
        same_a = _team_create_lock("user-a")
        same_b = _team_create_lock("user-a")
        other = _team_create_lock("user-b")
        assert same_a is same_b, "same user must share the lock"
        assert same_a is not other, "different users must not share a lock"

    def test_lock_serializes_same_user_critical_sections(self):
        """#1954 mechanism: the per-user lock actually serializes two
        overlapping critical sections for the same user (the second waits
        for the first to release). This is the red-without-lock
        discriminator: a no-op/removed lock lets the second section enter
        while the first is still inside, and the strict ordering fails."""
        import asyncio

        from tortoise.hosted_api import _team_create_lock

        async def _run():
            lock = _team_create_lock(_USER1)
            order = []

            async def holder():
                async with lock:
                    order.append("a-enter")
                    await asyncio.sleep(0.02)
                    order.append("a-exit")

            async def waiter():
                async with lock:
                    order.append("b-enter")

            await asyncio.gather(holder(), waiter())
            return order

        order = asyncio.run(_run())
        assert order == ["a-enter", "a-exit", "b-enter"], \
            f"critical sections did not serialize: {order}"
