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
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from tortoise.hosted_api import app, get_current_user
from tests.fake_control_plane import FakeControlPlane
from tests.test_supabase_control import FREE_TEAM, _membership_row  # noqa: F401

_USER1 = "9f2c1a40-0000-4a00-8000-000000000001"
_UPGRADE_MSG = "Create another team requires a paid plan"


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
        try:
            with TestClient(app) as tc:
                yield tc, supabase_env
        finally:
            ha_mod.TortoiseSDK.__init__ = _orig
            ha_mod._FALLBACK_KEEPALIVE.clear()
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
        tc, fake = user_client
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
        since = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        for i in range(3):
            _seed_team(fake, f"team-{i}")
            fake.seed("team_memberships", [
                _membership_row(user_id=_USER1, team_id=f"team-{i}",
                                role="owner", created_at=since)])
        r = tc.post("/v1/teams", json={"name": "fourth"})
        assert r.status_code == 429

    def test_onboarding_second_call_409(self, team_client_factory):
        """P0 re-entry guard (review P0 fix): the wizard creates the
        sub-team ONCE. A second call through the PRODUCTION-SHAPED
        dependency (no fabricated onboarding_state — the dep dict never
        carries it) reads the PERSISTED team_created state and 409s — no
        unlimited free sub-team minting via this endpoint."""
        from tortoise.hosted_api import get_current_team_session
        tc, fake = team_client_factory
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
        try:
            with TestClient(app) as tc:
                reg = ha_mod._make_sdk(namespace="registry")._get_registry()
                yield tc, reg
        finally:
            ha_mod.TortoiseSDK.__init__ = _orig
            ha_mod._FALLBACK_KEEPALIVE.clear()
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
        tc, reg = reg_client
        _reg_seed(reg, "team-free-r")
        _reg_member(reg, "team-free-r")
        from tortoise.hosted_api import _count_active_free_memberships
        assert _count_active_free_memberships(_USER1) == 1

    def test_helper_tier_pro_not_counted(self, reg_client):
        tc, reg = reg_client
        _reg_seed(reg, "team-pro-r", tier="pro")
        _reg_member(reg, "team-pro-r")
        from tortoise.hosted_api import _count_active_free_memberships
        assert _count_active_free_memberships(_USER1) == 0

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


@pytest.fixture
def team_client_factory(client):
    tc, fake = client
    return tc, fake
