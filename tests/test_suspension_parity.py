"""#1853 — suspended-team lockdown parity on the session-auth surface.

Bug-hunt 2026-08-28 (P2): the key-auth path and the session team-resolution
path (_session_user_team) both 403 SUSPENDED, but every endpoint gated by
_membership_team / _require_owner / _require_owner_admin resolved the team
row without checking `suspended_at` — a suspended team could create graphs,
list graph names, export the full artifact, import a full-graph overwrite,
manage members, and mint/revoke invites via session-JWT auth.

The fix wires `_ensure_not_suspended(team_row)` into the enforcement seams:
- _require_owner (export / import / delete) and _require_owner_admin
  (invites, members, key toggle, dashboard-login) — checked AFTER role
  authz (no existence-oracle change), so every owner/admin endpoint
  inherits parity
- create_graph / list_graphs (on the team node they already
  fetch — _membership_team itself stays pure)
- list_my_teams: #1912 replaced the whole-list suspension 403 with a
  per-row suspended_at field — healthy teams stay listable, and the 403
  survives only when EVERY membership is suspended (nothing healthy to
  switch to)
- rescind_invite's Supabase branch (delegates RBAC to invitation_rescind,
  which has no suspension check)

Deliberately open while suspended: GET /v1/team/alerts (appeal flow,
scoping delta 12) — asserted below.

Supabase mode via FakeControlPlane (mirrors test_export_delete / test_auth_
flip); session user via the get_current_user dependency override.
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
# Sensitive-op limiter (export/import) opt out in tests (mirrors
# test_export_delete).
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

import pytest  # noqa: I001
from fastapi.testclient import TestClient

import tortoise.hosted_api as ha_mod
from tortoise.hosted_api import app, get_current_user
from tortoise.sdk import TortoiseSDK

from tests.fake_control_plane import FakeControlPlane
from tests.test_supabase_control import (
    FREE_TEAM,
    _membership_row,
)

TEAM_ID = "team-free-001"
_TEAM2 = "team-free-002"

# #1719: team_memberships.user_id is a uuid column — real JWT subjects are
# UUIDs; non-UUID literals 22P02 under FakeControlPlane's fidelity check.
_U1 = "9f2c1a40-0000-4a00-8000-000000000001"
_U2 = "9f2c1a40-0000-4a00-8000-000000000002"
OWNER = _U1


def _enable_supabase(monkeypatch, cp) -> FakeControlPlane:
    """Turn Supabase mode on and inject the fake control plane."""
    import tortoise.supabase_control as sc

    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc_role_key_test")
    monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "supabase")
    monkeypatch.setattr(sc, "get_control_plane", lambda: cp)
    return cp


def _patch_tortoise_sdk_init(db_path: str):
    """Make hosted_api's TortoiseSDK use a temp embedded DB (mirrors
    test_export_delete) so registry/team reads don't touch prod."""
    _orig = ha_mod.TortoiseSDK.__init__

    def _patched(self, db_path_arg=None, *, namespace=None, **kwargs):
        _orig(self, db_path, namespace=namespace)

    ha_mod.TortoiseSDK.__init__ = _patched
    ha_mod._FALLBACK_KEEPALIVE.clear()
    return _orig


def _restore_sdk_init(_orig):
    ha_mod.TortoiseSDK.__init__ = _orig
    app.dependency_overrides.clear()


@pytest.fixture
def sb_client(monkeypatch):
    """Supabase-mode TestClient with a fake control plane + temp DB."""
    fake = FakeControlPlane(
        {"teams": [], "api_keys": [], "team_memberships": [], "invitations": []}
    )
    _enable_supabase(monkeypatch, fake)
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "susp.db")
        _orig = _patch_tortoise_sdk_init(db_path)
        try:
            with TestClient(app) as tc:
                yield tc, fake, db_path
        finally:
            _restore_sdk_init(_orig)


@pytest.fixture
def as_user():
    """Override get_current_user per test (JWT session user)."""

    def _set(user_id: str = OWNER):
        app.dependency_overrides[get_current_user] = lambda: {"user_id": user_id}

    yield _set
    app.dependency_overrides.pop(get_current_user, None)


def _seed_team(fake, *, suspended: bool = False, role: str = "owner", team_id: str = TEAM_ID):
    """Seed the team (+owner membership). suspended → suspended_at stamp."""
    team = dict(FREE_TEAM)
    team["id"] = team_id
    if suspended:
        team["suspended_at"] = "2026-08-01T00:00:00Z"
    fake.seed("teams", [team])
    fake.seed("team_memberships", [_membership_row(role=role, team_id=team_id)])


def _seed_pending_invite(fake, token: str = "tok-1"):
    """Seed a pending invitation (lookup_hash = token hash) for TEAM_ID."""
    from tortoise.auth import lookup_hash as _lh

    fake.seed(
        "invitations",
        [
            {
                "id": "inv-1",
                "team_id": TEAM_ID,
                "email": "bob@example.com",
                "role": "member",
                "status": "pending",
                "expires_at": None,
                "lookup_hash": _lh(token),
            }
        ],
    )


# Keep seeded registry SDKs alive for the module lifetime: a TortoiseSDK
# destructor SHUTDOWN NOSAVEs the process-shared embedded server when it
# is the last reference — letting the _seed_registry local be GC'd mid-
# suite can lose the just-seeded Team/Membership (flaky registry-mode
# 403s). Anchoring the seed SDKs removes the race deterministically.
_SEED_SDKS: list[TortoiseSDK] = []


def _seed_registry(db_path: str, *, suspended: bool = False, team_id: str = "reg-team-1",
                   m_id: str = "m-1"):
    """Seed a registry Team + owner Membership (+ optional suspended_at)."""
    sdk = TortoiseSDK(db_path, namespace="registry")
    _SEED_SDKS.append(sdk)
    reg = sdk._get_registry()
    props = {"id": team_id, "name": team_id, "tier": "free"}
    if suspended:
        props["suspended_at"] = "2026-08-01T00:00:00Z"
    reg.query(
        "CREATE (t:Team {id:$id, name:$name, tier:$tier"
        + (", suspended_at:$suspended_at" if suspended else "")
        + "})",
        params=props,
    )
    reg.query(
        "CREATE (m:Membership {id:$id, user_id:$uid, team_id:$tid, "
        "role:'owner', status:'active', joined_at:'2026-08-01T00:00:00Z'})",
        params={"id": m_id, "uid": OWNER, "tid": team_id},
    )


@pytest.fixture
def reg_client(monkeypatch):
    """Registry-mode TestClient (TORTOISE_CONTROL_PLANE=registry) + temp DB."""
    monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "registry")
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "susp-reg.db")
        _orig = _patch_tortoise_sdk_init(db_path)
        try:
            with TestClient(app) as tc:
                yield tc, db_path
        finally:
            _restore_sdk_init(_orig)


def _assert_suspended(r):
    """The key-path 403 shape: code SUSPENDED + appeal_url."""
    assert r.status_code == 403, r.text
    body = r.json()
    assert body["detail"]["code"] == "SUSPENDED"
    assert body["detail"]["appeal_url"]


class TestSuspendedTeamLockdown:
    """Every membership/owner endpoint 403s SUSPENDED on a suspended team."""

    def test_create_graph_403(self, sb_client, as_user):
        tc, fake, _ = sb_client
        _seed_team(fake, suspended=True)
        as_user()
        r = tc.post("/v1/graphs", json={"team_id": TEAM_ID, "name": "g1"})
        _assert_suspended(r)

    def test_list_graphs_403(self, sb_client, as_user):
        tc, fake, _ = sb_client
        _seed_team(fake, suspended=True)
        as_user()
        r = tc.get(f"/v1/graphs?team_id={TEAM_ID}")
        _assert_suspended(r)

    def test_list_my_teams_403(self, sb_client, as_user):
        """#1912 regression: EVERY membership suspended → nothing healthy to
        list — still 403 SUSPENDED with the appeal detail."""
        tc, fake, _ = sb_client
        _seed_team(fake, suspended=True)
        as_user()
        r = tc.get("/v1/teams")
        _assert_suspended(r)

    def test_list_my_teams_all_suspended_403(self, sb_client, as_user):
        """#1912: multiple suspended memberships (no healthy team) still 403
        as a whole — the per-row suspended_at only unblocks MIXED lists."""
        tc, fake, _ = sb_client
        _seed_team(fake, suspended=True)
        _seed_team(fake, suspended=True, team_id=_TEAM2)
        as_user()
        r = tc.get("/v1/teams")
        _assert_suspended(r)

    def test_export_403(self, sb_client, as_user):
        tc, fake, _ = sb_client
        _seed_team(fake, suspended=True)
        as_user()
        r = tc.get(f"/v1/teams/{TEAM_ID}/export")
        _assert_suspended(r)

    def test_import_403(self, sb_client, as_user):
        """Full-graph overwrite surface — the suspension check fires in
        _require_owner before the artifact body is even read."""
        tc, fake, _ = sb_client
        _seed_team(fake, suspended=True)
        as_user()
        r = tc.post(f"/v1/teams/{TEAM_ID}/import", json={})
        _assert_suspended(r)

    def test_list_members_403(self, sb_client, as_user):
        tc, fake, _ = sb_client
        _seed_team(fake, suspended=True)
        as_user()
        r = tc.get(f"/v1/teams/{TEAM_ID}/members")
        _assert_suspended(r)

    def test_remove_member_403(self, sb_client, as_user):
        tc, fake, _ = sb_client
        _seed_team(fake, suspended=True)
        as_user()
        r = tc.delete(f"/v1/teams/{TEAM_ID}/members/some-user")
        _assert_suspended(r)

    def test_change_member_role_403(self, sb_client, as_user):
        tc, fake, _ = sb_client
        _seed_team(fake, suspended=True)
        as_user()
        r = tc.patch(f"/v1/teams/{TEAM_ID}/members/some-user", json={"role": "member"})
        _assert_suspended(r)

    def test_list_invites_403(self, sb_client, as_user):
        tc, fake, _ = sb_client
        _seed_team(fake, suspended=True)
        as_user()
        r = tc.get(f"/v1/invites?team_id={TEAM_ID}")
        _assert_suspended(r)

    def test_delete_team_403(self, sb_client, as_user):
        """Suspended team cannot even schedule deletion (a destructive
        write) — the allow_removed gate only skips the check for the
        idempotent replay of an ALREADY delete-pending team."""
        tc, fake, _ = sb_client
        _seed_team(fake, suspended=True)
        as_user()
        r = tc.delete(f"/v1/teams/{TEAM_ID}")
        _assert_suspended(r)

    def test_accept_invite_403(self, sb_client, as_user):
        """A pre-suspension pending invite must not mint a membership on a
        suspended team (invitation_accept checks suspended_at next to the
        deleted_at kill-switch)."""
        tc, fake, _ = sb_client
        _seed_team(fake, suspended=True)
        _seed_pending_invite(fake)
        # invitee = a NON-member (the owner is already a member → 409 path)
        as_user(_U2)
        r = tc.post("/v1/invites/accept", json={"token": "tok-1"})
        assert r.status_code == 403, r.text
        assert "suspended" in r.json()["detail"]

    def test_invite_403(self, sb_client, as_user):
        tc, fake, _ = sb_client
        _seed_team(fake, suspended=True)
        as_user()
        r = tc.post(
            "/v1/invites", json={"team_id": TEAM_ID, "email": "bob@example.com", "role": "member"}
        )
        _assert_suspended(r)

    def test_rescind_invite_403(self, sb_client, as_user):
        """Supabase branch delegates RBAC to invitation_rescind — the
        explicit seam check must fire before the rescind write."""
        tc, fake, _ = sb_client
        _seed_team(fake, suspended=True)
        as_user()
        r = tc.delete(f"/v1/invites/inv-1?team_id={TEAM_ID}")
        _assert_suspended(r)

    def test_alerts_still_open(self, sb_client, as_user):
        """The appeal flow must stay reachable while suspended (scoping
        delta 12): /v1/team/alerts uses _membership_team directly."""
        tc, fake, _ = sb_client
        _seed_team(fake, suspended=True)
        as_user()
        r = tc.get(f"/v1/team/alerts?team_id={TEAM_ID}")
        assert r.status_code == 200, r.text
        assert r.json()["team_id"] == TEAM_ID

    def test_list_my_teams_mixed_healthy_listable(self, sb_client, as_user):
        """#1912: a suspended membership must not 403 the whole switcher.
        Mixed healthy/suspended memberships list BOTH rows — the suspended
        one carries suspended_at (auto-selection skips it; manual selection
        403s with the appeal detail) and the healthy team stays listable.
        The suspended row also skips graph resolution: it has a default
        graph on the teams row, yet graph_count stays 0."""
        tc, fake, _ = sb_client
        _seed_team(fake, suspended=True)
        _seed_team(fake, suspended=False, team_id=_TEAM2)
        # a graph_name would make graph_list return 1 graph — the skip must
        # still yield graph_count 0 / default_graph_id None for the row.
        fake.tables["teams"][0]["graph_name"] = "default"
        # mirror: the HEALTHY row in the same mixed response must still
        # resolve its default graph.
        fake.tables["teams"][1]["graph_name"] = "default"
        as_user()
        r = tc.get("/v1/teams")
        assert r.status_code == 200, r.text
        by_id = {t["team_id"]: t for t in r.json()}
        assert set(by_id) == {TEAM_ID, _TEAM2}
        assert by_id[TEAM_ID]["suspended_at"] == "2026-08-01T00:00:00Z"
        assert by_id[TEAM_ID]["graph_count"] == 0  # resolution skipped
        assert by_id[TEAM_ID]["default_graph_id"] is None
        assert by_id[_TEAM2]["suspended_at"] is None
        assert by_id[_TEAM2]["graph_count"] == 1
        assert by_id[_TEAM2]["default_graph_id"] == "default"

    def test_list_my_teams_healthy_only_200(self, sb_client, as_user):
        """Control: no suspended memberships → switcher unaffected (per-row
        suspended_at is None)."""
        tc, fake, _ = sb_client
        _seed_team(fake, suspended=False)
        as_user()
        r = tc.get("/v1/teams")
        assert r.status_code == 200, r.text
        assert len(r.json()) == 1
        assert r.json()[0]["suspended_at"] is None


class TestSuspendedTeamLockdownRegistry:
    """Registry-mode branches of the seams (selfhost): _team_node reads the
    Team node, which carries suspended_at as a property."""

    def test_create_graph_403(self, reg_client, as_user):
        tc, db_path = reg_client
        _seed_registry(db_path, suspended=True)
        as_user()
        r = tc.post("/v1/graphs", json={"team_id": "reg-team-1", "name": "g1"})
        _assert_suspended(r)

    def test_export_403(self, reg_client, as_user):
        tc, db_path = reg_client
        _seed_registry(db_path, suspended=True)
        as_user()
        r = tc.get("/v1/teams/reg-team-1/export")
        _assert_suspended(r)

    def test_list_my_teams_mixed_healthy_listable(self, reg_client, as_user):
        """#1912 registry branch: per-row suspended_at via properties(t) —
        mixed memberships list both rows, suspended carries the stamp."""
        tc, db_path = reg_client
        _seed_registry(db_path, suspended=True, team_id="reg-team-1")
        _seed_registry(db_path, suspended=False, team_id="reg-team-2", m_id="m-2")
        as_user()
        r = tc.get("/v1/teams")
        assert r.status_code == 200, r.text
        by_id = {t["team_id"]: t for t in r.json()}
        assert set(by_id) == {"reg-team-1", "reg-team-2"}
        assert by_id["reg-team-1"]["suspended_at"] == "2026-08-01T00:00:00Z"
        assert by_id["reg-team-2"]["suspended_at"] is None

    def test_export_healthy_control(self, reg_client, as_user):
        tc, db_path = reg_client
        _seed_registry(db_path, suspended=False)
        # seed a graph node so the export has data (empty namespace → 500)
        sdk = TortoiseSDK(db_path, namespace="reg-team-1")
        sdk._get_proj().g.query(
            "CREATE (p:Point {id:'pt-0', content:'c', pointKind:'claim', confidence:0.8})"
        )
        as_user()
        r = tc.get("/v1/teams/reg-team-1/export")
        assert r.status_code == 200, r.text
        assert r.json()["summary"]["points"] == 1


class TestHealthyTeamControl:
    """Control: the same seams must NOT block healthy teams (regression)."""

    def test_create_graph_still_works(self, sb_client, as_user):
        tc, fake, _ = sb_client
        _seed_team(fake, suspended=False)
        as_user()
        r = tc.post("/v1/graphs", json={"team_id": TEAM_ID, "name": "g1"})
        assert r.status_code == 200, r.text
        assert r.json()["graph_id"]

    def test_list_my_teams_still_works(self, sb_client, as_user):
        tc, fake, _ = sb_client
        _seed_team(fake, suspended=False)
        as_user()
        r = tc.get("/v1/teams")
        assert r.status_code == 200, r.text
        assert r.json()[0]["team_id"] == TEAM_ID

    def test_list_my_teams_no_memberships_200(self, sb_client, as_user):
        """#1912 guard edge: zero memberships → 200 [] (the all-suspended
        403 must not fire on an empty list — all() of [] is True)."""
        tc, fake, _ = sb_client
        as_user()
        r = tc.get("/v1/teams")
        assert r.status_code == 200, r.text
        assert r.json() == []

    def test_invite_admin_still_works_but_member_403(self, sb_client, as_user):
        """Role authz unchanged on healthy teams: owner passes (Team tier
        check 402s first — assert the seam itself passed by the 402 tier
        response, not a 403)."""
        tc, fake, _ = sb_client
        _seed_team(fake, suspended=False)
        as_user()
        r = tc.post(
            "/v1/invites", json={"team_id": TEAM_ID, "email": "bob@example.com", "role": "member"}
        )
        # FREE tier → 402 (invites require Team tier); NOT a 403 — the
        # owner/admin seam (and its new suspension check) passed.
        assert r.status_code == 402, r.text

    def test_suspended_non_owner_still_role_403_not_suspension(self, sb_client, as_user):
        """AuthZ-first: a non-owner probing a suspended team gets the role
        403, not the SUSPENDED detail (no existence-oracle change)."""
        tc, fake, _ = sb_client
        _seed_team(fake, suspended=True, role="member")
        as_user()
        r = tc.get(f"/v1/teams/{TEAM_ID}/export")
        assert r.status_code == 403
        assert "owner" in r.json()["detail"]
