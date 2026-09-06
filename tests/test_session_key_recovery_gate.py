"""#2380 — owner/admin-gated recovery session-key mint (Option A) + surfacing.

POST /v1/session/key `purpose:"recovery"` must be owner/admin-only in BOTH
auth lanes (registry/selfhost + Supabase) via the `_require_owner_admin`
seam — a member-role session must not mint a persistent deleg-NULL
owner-class key, and the member-at-cap auto-revoke side-effect (which used
to rotate the OWNER's oldest durable key) dies with the 403. `bootstrap`
(24h ephemeral) stays member-open per product posture. Also covers the P2
items of #2380: created_by (minting user) on GET /v1/team/keys rows in both
lanes (registry BOTH SELECT variants — graph-filtered and unfiltered — plus
the supabase select), the _require_owner_admin outage wrap (RuntimeError →
503 control_plane_unavailable, #1719 class), and the auth-lane discriminator
(session dicts carry session_user_id/auth_lane on the production JWT branch;
key-auth dicts carry neither in both control planes; the dependency-override
seam keeps gating when a test injects session_user_id).

Supabase lane via FakeControlPlane + dependency overrides (mirrors
tests/test_dashboard_login.py member-matrix fixtures and tests/test_auth_
flip.py); registry-lane parity via the patched_tortoise_sdk fixture
(mirrors tests/test_session_key_http.py) — no raw SDK constructions on the
mint path.
"""
from __future__ import annotations

import asyncio
import os
import tempfile

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import tortoise.hosted_api as ha
import tortoise.session_auth as sa
import tortoise.supabase_control as sc
from tests._http_fixtures import patched_tortoise_sdk
from tests.fake_control_plane import ErrorControlPlane, FakeControlPlane
from tests.test_supabase_control import FREE_TEAM
from tortoise.auth import hash_api_key
from tortoise.hosted_api import (
    _require_owner_admin,
    _suspended_detail,
    app,
    get_current_team,
    get_current_team_session,
    get_current_team_session_ungated,
    get_current_user,
)
from tortoise.sdk import TortoiseSDK

# #1719 (Task 3): team_memberships.user_id is a uuid column — real JWT
# subjects are UUIDs; non-UUID literals 22P02 under FakeControlPlane's
# fidelity check. api_keys.created_by mirrors the minting user's UUID.
_U1 = "9f2c1a40-0000-4a00-8000-000000000001"
_U2 = "9f2c1a40-0000-4a00-8000-000000000002"
_U3 = "9f2c1a40-0000-4a00-8000-000000000003"

_ROLE_403 = "Requires owner or admin role in team"
_SB_TEAM = "team-free-001"


def _assert_role_403(r):
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == _ROLE_403


def _assert_recovery_mint_200(r, team_id: str):
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["purpose"] == "recovery"
    assert body["team_id"] == team_id
    assert body["expires_at"] is None
    assert body["key"].startswith("tt_")


def _assert_suspended_detail(r):
    assert r.status_code == 403, r.text
    detail = r.json()["detail"]
    assert detail == _suspended_detail()  # byte-identical detail dict
    assert detail["code"] == "SUSPENDED"


# ═══════════════════════════════════════════════════════════════════════════
# Shared fixtures (supabase lane env / user override)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def as_user():
    """Override get_current_user per test (JWT session user)."""

    def _set(user_id: str, email: str | None = None):
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": user_id, "email": email}

    yield _set
    app.dependency_overrides.pop(get_current_user, None)


def _sb_membership(fake, team_id: str, user_id: str, role: str):
    """Seed an active membership row (member-matrix helper, mirrors
    test_dashboard_login._seed_membership)."""
    fake.tables.setdefault("team_memberships", []).append({
        "id": f"mem-{team_id}-{user_id}-{role}",
        "team_id": team_id, "user_id": user_id, "role": role,
        "status": "active", "created_at": "2026-01-01T00:00:00Z",
        "identity": None, "lookup_hash": None,
    })


def _sb_team(fake, team_id: str, **overrides) -> dict:
    """Seed (or fetch) a teams row shaped like FREE_TEAM with a new id."""
    for t in fake.tables.setdefault("teams", []):
        if t.get("id") == team_id:
            t.update(overrides)
            return t
    team = dict(FREE_TEAM, id=team_id)
    team.update(overrides)
    fake.tables["teams"].append(team)
    return team


def _sb_key(fake, key_id: str, team_id: str, *, created_by,
            created_via: str | None = "recovery",
            created_at: str = "2026-01-01T00:00:00Z",
            revoked_at: str | None = None):
    """Seed an api_keys row directly (no mint-trigger side effects — mirror
    the #750.10 fixtures in test_auth_flip)."""
    fake.tables.setdefault("api_keys", []).append({
        "id": key_id, "team_id": team_id, "key_prefix": "tt_seeded",
        "lookup_hash": f"hash-{key_id}", "created_via": created_via,
        "created_by": created_by, "created_at": created_at,
        "expires_at": None, "revoked_at": revoked_at,
        "enabled": True, "name": None,
    })


@pytest.fixture
def sb(monkeypatch):
    """Supabase-mode TestClient with a fake control plane + temp DB."""
    monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://recovery-gate.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc_recovery_gate")
    fake = FakeControlPlane({
        "api_keys": [], "team_memberships": [], "teams": [dict(FREE_TEAM)],
        "invitations": [], "abuse_events": [],
    })
    monkeypatch.setattr(sc, "get_control_plane", lambda: fake)
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "rg-sb.db")
        with patched_tortoise_sdk(db_path), TestClient(app) as tc:
            yield tc, fake


# ═══════════════════════════════════════════════════════════════════════════
# Registry-lane fixtures (selfhost embedded — mirrors test_session_key_http)
# ═══════════════════════════════════════════════════════════════════════════

# #1588: hold registry SDKs alive — the `reg` fixture returns
# _get_registry() but the SDK goes out of scope; with #1475 close-on-GC the
# shared server is shut down before the test uses the handle.
_REG_SDKS: list = []


@pytest.fixture
def reg_client(monkeypatch):
    """Registry-mode TestClient + temp embedded DB (no supabase creds)."""
    monkeypatch.delenv("TORTOISE_CONTROL_PLANE", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "rg-reg.db")
        with patched_tortoise_sdk(db_path):
            try:
                with TestClient(app) as tc:
                    yield tc
            finally:
                while _REG_SDKS:
                    try:  # noqa: SIM105
                        _REG_SDKS.pop().close()
                    except Exception:
                        pass


@pytest.fixture
def reg():
    """Registry graph handle (same temp DB via the patched __init__)."""
    sdk = TortoiseSDK(namespace="registry")
    _REG_SDKS.append(sdk)
    return sdk._get_registry()


def _seed_team(reg, team_id: str, tier: str = "free",
               suspended_at: str | None = None):
    props = {"id": team_id, "name": team_id, "tier": tier}
    if suspended_at is not None:
        props["suspended_at"] = suspended_at
    reg.query(
        "CREATE (t:Team {id:$id, name:$name, tier:$tier"
        + (", suspended_at:$suspended_at" if suspended_at is not None else "")
        + "})",
        params=props,
    )


def _seed_membership(reg, team_id: str, user_id: str, role: str,
                     status: str = "active"):
    reg.query(
        "CREATE (m:Membership {user_id:$uid, team_id:$tid, role:$role, "
        "status:$status, created_at:'2026-08-01T00:00:00+00:00'})",
        params={"uid": user_id, "tid": team_id, "role": role, "status": status},
    )


def _seed_api_key(reg, team_id: str, key_id: str, *, created_by: str | None,
                  created_via: str | None, created_at: str,
                  revoked_at: str | None = None):
    """Seed an APIKey node. created_by=None stores a NULL prop (legacy rows
    minted pre-created_by — the #2380 grandfathered set); pass
    include_created_by=False via the raw _seed_legacy_key for a node that
    LACKS the property entirely."""
    reg.query(
        "CREATE (k:APIKey {id:$id, team_id:$tid, key_hash:'h', key_prefix:'tt_x', "
        "created_by:$cb, created_at:$ca, revoked_at:$ra, expires_at:null, "
        "created_via:$cv})",
        params={"id": key_id, "tid": team_id, "cb": created_by, "ca": created_at,
                "ra": revoked_at, "cv": created_via},
    )


def _count_active_keys(reg, team_id: str) -> int:
    rows = reg.query(
        "MATCH (k:APIKey {team_id:$tid}) WHERE k.revoked_at IS NULL RETURN count(k)",
        params={"tid": team_id},
    ).result_set
    return int(rows[0][0])


# ═══════════════════════════════════════════════════════════════════════════
# Task 1 — recovery mint gate (supabase lane)
# ═══════════════════════════════════════════════════════════════════════════

class TestRecoveryGateSupabase:
    """Owner/admin-gate purpose='recovery' on POST /v1/session/key
    (Supabase lane — fake control plane)."""

    def test_member_recovery_mint_403(self, sb, as_user):
        """#2380 root: a MEMBER session minting a recovery key 403s — the
        #2297 POLICY A escalation root, recovery flavor (pre-fix 200 → a
        persistent deleg-NULL owner-class key + the at-cap auto-revoke of
        the owner's oldest key)."""
        tc, fake = sb
        _sb_membership(fake, _SB_TEAM, _U1, "member")
        as_user(_U1)
        r = tc.post("/v1/session/key", json={"purpose": "recovery"})
        _assert_role_403(r)
        # audit trail: the 403 must not mint anything
        assert fake.tables["api_keys"] == []

    def test_member_bootstrap_mint_200_unchanged(self, sb, as_user):
        """bootstrap (24h ephemeral) stays member-open — product posture."""
        tc, fake = sb
        _sb_membership(fake, _SB_TEAM, _U1, "member")
        as_user(_U1)
        r = tc.post("/v1/session/key", json={"purpose": "bootstrap"})
        assert r.status_code == 200, r.text
        assert r.json()["purpose"] == "bootstrap"
        assert r.json()["expires_at"] is not None
        assert len(fake.tables["api_keys"]) == 1

    def test_owner_recovery_mint_200(self, sb, as_user):
        tc, fake = sb
        _sb_membership(fake, _SB_TEAM, _U1, "owner")
        as_user(_U1)
        _assert_recovery_mint_200(
            tc.post("/v1/session/key", json={"purpose": "recovery"}), _SB_TEAM)
        row = fake.tables["api_keys"][0]
        assert row["created_via"] == "recovery"
        assert row["created_by"] == _U1

    def test_admin_recovery_mint_200(self, sb, as_user):
        """Admins ride the same gate (owner/admin role tuple, #1148)."""
        tc, fake = sb
        _sb_membership(fake, _SB_TEAM, _U1, "admin")
        as_user(_U1)
        _assert_recovery_mint_200(
            tc.post("/v1/session/key", json={"purpose": "recovery"}), _SB_TEAM)

    def test_multi_membership_role_check_targets_resolved_team(self, sb, as_user):
        """#2380 acceptance: owner of team A + member of team B — the role
        gate must check the RESOLVED tid (body team_id when multi-
        membership), never memberships[0]. B (member) → 403; A (owner) → 200."""
        tc, fake = sb
        _sb_team(fake, "team-a-001")
        _sb_team(fake, "team-b-001")
        _sb_membership(fake, "team-a-001", _U1, "owner")
        _sb_membership(fake, "team-b-001", _U1, "member")
        as_user(_U1)
        # team B first in insertion order = memberships[0] — the 403 must
        # come from the RESOLVED team, not the memberships[0] role.
        _assert_role_403(tc.post("/v1/session/key", json={
            "purpose": "recovery", "team_id": "team-b-001"}))
        assert not [k for k in fake.tables["api_keys"]
                    if k["team_id"] == "team-b-001"]
        _assert_recovery_mint_200(tc.post("/v1/session/key", json={
            "purpose": "recovery", "team_id": "team-a-001"}), "team-a-001")

    def test_member_recovery_at_cap_403_revokes_nothing(self, sb, as_user):
        """Collateral pin: a MEMBER recovery attempt at max_api_keys 403s
        and must NOT auto-revoke the owner's oldest durable key (the
        pre-#2380 side-effect: memberships[0] owner-role was never checked,
        so the member's at-cap mint rotated the owner's oldest key)."""
        tc, fake = sb
        _sb_membership(fake, _SB_TEAM, _U2, "owner")   # owner face
        _sb_membership(fake, _SB_TEAM, _U1, "member")  # acting member
        # free tier max_api_keys=2 — both seats are the OWNER's keys.
        _sb_key(fake, "own-old", _SB_TEAM, created_by=_U2,
                created_at="2026-01-01T00:00:00Z")
        _sb_key(fake, "own-new", _SB_TEAM, created_by=_U2,
                created_at="2026-01-02T00:00:00Z")
        as_user(_U1)
        r = tc.post("/v1/session/key", json={"purpose": "recovery"})
        _assert_role_403(r)
        rows = fake.tables["api_keys"]
        assert len(rows) == 2  # nothing minted
        assert all(k["revoked_at"] is None for k in rows)  # nothing revoked
        by_id = {k["id"]: k for k in rows}
        assert by_id["own-old"]["revoked_at"] is None  # owner's OLDEST intact

    def test_member_recovery_suspended_gets_lane_suspended_detail(
            self, sb, as_user):
        """Ordering pinned: the lane's OWN suspension check fires BEFORE the
        role gate — a member on a SUSPENDED team gets THIS lane's existing
        SUSPENDED detail (never the role 403), byte-identical to the
        registry lane."""
        tc, fake = sb
        _sb_team(fake, _SB_TEAM, suspended_at="2026-08-01T00:00:00Z")
        _sb_membership(fake, _SB_TEAM, _U1, "member")
        as_user(_U1)
        _assert_suspended_detail(
            tc.post("/v1/session/key", json={"purpose": "recovery"}))

    def test_owner_recovery_suspended_gets_lane_suspended_detail(
            self, sb, as_user):
        """An OWNER on a suspended team ALSO gets the lane's SUSPENDED
        detail first (the lane suspension check precedes the gate) — the
        role gate's internal _ensure_not_suspended is never reached."""
        tc, fake = sb
        _sb_team(fake, _SB_TEAM, suspended_at="2026-08-01T00:00:00Z")
        _sb_membership(fake, _SB_TEAM, _U1, "owner")
        as_user(_U1)
        _assert_suspended_detail(
            tc.post("/v1/session/key", json={"purpose": "recovery"}))


# ═══════════════════════════════════════════════════════════════════════════
# Task 1 — recovery mint gate (registry lane — byte-parity)
# ═══════════════════════════════════════════════════════════════════════════

class TestRecoveryGateRegistry:
    """Same member/owner/admin matrix on the REGISTRY (selfhost) lane —
    branch parity with TestRecoveryGateSupabase."""

    def test_member_recovery_mint_403(self, reg_client, reg, as_user):
        tc = reg_client
        _seed_team(reg, "team-r")
        _seed_membership(reg, "team-r", _U1, "member")
        as_user(_U1)
        r = tc.post("/v1/session/key", json={"purpose": "recovery"})
        _assert_role_403(r)
        assert _count_active_keys(reg, "team-r") == 0  # nothing minted

    def test_member_bootstrap_mint_200_unchanged(self, reg_client, reg, as_user):
        tc = reg_client
        _seed_team(reg, "team-r")
        _seed_membership(reg, "team-r", _U1, "member")
        as_user(_U1)
        r = tc.post("/v1/session/key", json={"purpose": "bootstrap"})
        assert r.status_code == 200, r.text
        assert r.json()["expires_at"] is not None
        assert _count_active_keys(reg, "team-r") == 1

    def test_owner_recovery_mint_200(self, reg_client, reg, as_user):
        tc = reg_client
        _seed_team(reg, "team-r")
        _seed_membership(reg, "team-r", _U1, "owner")
        as_user(_U1)
        _assert_recovery_mint_200(
            tc.post("/v1/session/key", json={"purpose": "recovery"}), "team-r")

    def test_admin_recovery_mint_200(self, reg_client, reg, as_user):
        tc = reg_client
        _seed_team(reg, "team-r")
        _seed_membership(reg, "team-r", _U1, "admin")
        as_user(_U1)
        _assert_recovery_mint_200(
            tc.post("/v1/session/key", json={"purpose": "recovery"}), "team-r")

    def test_multi_membership_role_check_targets_resolved_team(
            self, reg_client, reg, as_user):
        tc = reg_client
        _seed_team(reg, "team-ra")
        _seed_team(reg, "team-rb")
        _seed_membership(reg, "team-ra", _U1, "owner")
        _seed_membership(reg, "team-rb", _U1, "member")
        as_user(_U1)
        _assert_role_403(tc.post("/v1/session/key", json={
            "purpose": "recovery", "team_id": "team-rb"}))
        assert _count_active_keys(reg, "team-rb") == 0
        _assert_recovery_mint_200(tc.post("/v1/session/key", json={
            "purpose": "recovery", "team_id": "team-ra"}), "team-ra")

    def test_member_recovery_at_cap_403_revokes_nothing(
            self, reg_client, reg, as_user):
        tc = reg_client
        _seed_team(reg, "team-r")
        _seed_membership(reg, "team-r", _U2, "owner")
        _seed_membership(reg, "team-r", _U1, "member")
        # free tier max_api_keys=2 — both seats are the OWNER's keys.
        _seed_api_key(reg, "team-r", "own-old", created_by=_U2,
                      created_via="recovery", created_at="2026-01-01T00:00:00Z")
        _seed_api_key(reg, "team-r", "own-new", created_by=_U2,
                      created_via="recovery", created_at="2026-01-02T00:00:00Z")
        assert _count_active_keys(reg, "team-r") == 2
        as_user(_U1)
        r = tc.post("/v1/session/key", json={"purpose": "recovery"})
        _assert_role_403(r)
        rows = reg.query(
            "MATCH (k:APIKey) WHERE k.id IN ['own-old','own-new'] "
            "RETURN k.id, k.revoked_at",
        ).result_set
        by_id = {rid: revoked for rid, revoked in rows}
        assert by_id["own-old"] is None   # owner's OLDEST key intact
        assert by_id["own-new"] is None
        assert _count_active_keys(reg, "team-r") == 2  # no mint, no revoke

    def test_member_recovery_suspended_gets_lane_suspended_detail(
            self, reg_client, reg, as_user):
        tc = reg_client
        _seed_team(reg, "team-r", suspended_at="2026-08-01T00:00:00Z")
        _seed_membership(reg, "team-r", _U1, "member")
        as_user(_U1)
        r = tc.post("/v1/session/key", json={"purpose": "recovery"})
        # detail is BYTE-IDENTICAL to the supabase lane's SUSPENDED detail
        # (both lanes assert detail == _suspended_detail() in this module).
        _assert_suspended_detail(r)


# ═══════════════════════════════════════════════════════════════════════════
# Task 2 — created_by (minting user) on the keys list, both lanes
# ═══════════════════════════════════════════════════════════════════════════

def _list_override(team_id: str, **extra) -> dict:
    """Session/key-shaped dependency dict for GET /v1/team/keys (only
    team_id is read; legacy_full_access passes _require_keys_manage)."""
    dep = {"team_id": team_id, "key_id": None, "scopes": [],
           "legacy_full_access": True, "delegation_depth": None}
    dep.update(extra)
    return dep


class TestCreatedByOnKeysList:
    def test_supabase_lane_created_by_present_legacy_null(self, sb):
        """GET /v1/team/keys (supabase lane): rows carry the minting USER
        (created_by) — a modern minted row reports the user; a legacy row
        (created_by NULL / absent) degrades to None with no error."""
        tc, fake = sb
        fake.seed("api_keys", [
            {"id": "k-modern", "team_id": _SB_TEAM, "key_prefix": "tt_m1",
             "created_via": "recovery", "created_by": _U2,
             "created_at": "2026-01-02T00:00:00Z", "revoked_at": None},
            {"id": "k-legacy", "team_id": _SB_TEAM, "key_prefix": "tt_l1",
             "created_via": None,  # legacy row — NO created_by key at all
             "created_at": "2026-01-01T00:00:00Z", "revoked_at": None},
        ])
        app.dependency_overrides[get_current_team_session_ungated] = \
            lambda: _list_override(_SB_TEAM)
        try:
            r = tc.get("/v1/team/keys")
        finally:
            app.dependency_overrides.pop(get_current_team_session_ungated, None)
        assert r.status_code == 200, r.text
        by_id = {k["id"]: k for k in r.json()["keys"]}
        assert by_id["k-modern"]["created_by"] == _U2
        assert by_id["k-legacy"]["created_by"] is None  # legacy NULL, no error

    def test_registry_unfiltered_created_by_present_legacy_null(
            self, reg_client, reg):
        """Registry lane, UNFILTERED SELECT variant: created_by rides the
        rows (row[12]); a node WITHOUT the created_by prop (legacy) → None."""
        tc = reg_client
        _seed_team(reg, "team-r")
        _seed_api_key(reg, "team-r", "k-modern", created_by=_U2,
                      created_via="recovery", created_at="2026-01-02T00:00:00Z")
        # legacy node minted before created_by existed — prop ABSENT
        reg.query(
            "CREATE (k:APIKey {id:'k-legacy', team_id:'team-r', "
            "key_hash:'h', key_prefix:'tt_x', created_at:'2026-01-01T00:00:00Z'})",
        )
        app.dependency_overrides[get_current_team_session_ungated] = \
            lambda: _list_override("team-r")
        try:
            r = tc.get("/v1/team/keys")
        finally:
            app.dependency_overrides.pop(get_current_team_session_ungated, None)
        assert r.status_code == 200, r.text
        by_id = {k["id"]: k for k in r.json()["keys"]}
        assert by_id["k-modern"]["created_by"] == _U2
        assert by_id["k-legacy"]["created_by"] is None  # absent prop → None

    def test_registry_graph_filtered_created_by_present(self, reg_client, reg):
        """Registry lane, GRAPH-FILTERED SELECT variant: the ?graph_id=
        query must ALSO return created_by — updating one variant and
        forgetting the other is exactly the drift #2380 pins."""
        tc = reg_client
        _seed_team(reg, "team-r")
        reg.query(
            "CREATE (k:APIKey {id:'k-g1', team_id:'team-r', graph_id:'g1', "
            "key_hash:'h', key_prefix:'tt_x', created_by:$cb, "
            "created_at:'2026-01-02T00:00:00Z'})",
            params={"cb": _U2},
        )
        reg.query(
            "CREATE (k:APIKey {id:'k-g2', team_id:'team-r', graph_id:'g2', "
            "key_hash:'h', key_prefix:'tt_x', created_by:$cb, "
            "created_at:'2026-01-01T00:00:00Z'})",
            params={"cb": _U3},
        )
        app.dependency_overrides[get_current_team_session_ungated] = \
            lambda: _list_override("team-r")
        try:
            r = tc.get("/v1/team/keys", params={"graph_id": "g1"})
        finally:
            app.dependency_overrides.pop(get_current_team_session_ungated, None)
        assert r.status_code == 200, r.text
        keys = r.json()["keys"]
        assert [k["id"] for k in keys] == ["k-g1"]  # g2 filtered out
        assert keys[0]["created_by"] == _U2


# ═══════════════════════════════════════════════════════════════════════════
# Task 3 — outage 503 at the _require_owner_admin gate seam (#1719 class)
# ═══════════════════════════════════════════════════════════════════════════

class TestOwnerAdminGateOutage:
    """A control-plane outage on the seam's OWN membership read degrades to
    503 control_plane_unavailable (never a raw 500). Non-RuntimeError
    exceptions propagate untouched (a dialect/schema bug stays loud)."""

    def test_supabase_membership_read_runtime_error_503(self, monkeypatch):
        monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "supabase")
        monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")
        monkeypatch.setattr(sc, "get_control_plane", lambda: ErrorControlPlane())
        with pytest.raises(HTTPException) as ei:
            asyncio.run(_require_owner_admin(_U1, _SB_TEAM))
        exc = ei.value
        assert exc.status_code == 503
        assert exc.detail.get("error_code") == "control_plane_unavailable"

    def test_registry_membership_read_runtime_error_503(self, monkeypatch):
        monkeypatch.delenv("TORTOISE_CONTROL_PLANE", raising=False)
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)

        class _BoomAnchor:
            def _get_registry(self):
                class _R:
                    def query(self, *_a, **_k):
                        raise RuntimeError("FalkorDB unreachable (simulated)")

                return _R()

        monkeypatch.setattr(ha, "_registry_anchor", lambda: _BoomAnchor())
        with pytest.raises(HTTPException) as ei:
            asyncio.run(_require_owner_admin(_U1, "team-r"))
        exc = ei.value
        assert exc.status_code == 503
        assert exc.detail.get("error_code") == "control_plane_unavailable"

    def test_supabase_non_runtime_error_not_wrapped(self, monkeypatch):
        """Only RuntimeError is wrapped — a ValueError (dialect/schema bug)
        must propagate untouched, never masquerade as an outage."""
        monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "supabase")
        monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")
        monkeypatch.setattr(
            sc, "get_control_plane",
            lambda: ErrorControlPlane(exc=ValueError("schema drift")),
        )
        with pytest.raises(ValueError) as ei:
            asyncio.run(_require_owner_admin(_U1, _SB_TEAM))
        assert "schema drift" in str(ei.value)

    def test_registry_non_runtime_error_not_wrapped(self, monkeypatch):
        monkeypatch.delenv("TORTOISE_CONTROL_PLANE", raising=False)
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)

        class _BoomAnchor:
            def _get_registry(self):
                class _R:
                    def query(self, *_a, **_k):
                        raise ValueError("dialect drift")

                return _R()

        monkeypatch.setattr(ha, "_registry_anchor", lambda: _BoomAnchor())
        with pytest.raises(ValueError) as ei:
            asyncio.run(_require_owner_admin(_U1, "team-r"))
        assert "dialect drift" in str(ei.value)


# ═══════════════════════════════════════════════════════════════════════════
# Task 4 — auth-lane discriminator: markers + override-seam gating
# ═══════════════════════════════════════════════════════════════════════════

def _make_request(scope_headers, path: str = "/v1/team/keys"):
    """Minimal starlette Request scoped to the real app (direct DI calls —
    no TestClient round-trip needed for dependency-level assertions)."""
    from starlette.requests import Request

    scope = {
        "type": "http", "method": "GET", "path": path,
        "headers": scope_headers, "query_string": b"", "scheme": "http",
        "server": ("test", 80), "client": ("127.0.0.1", 50000),
        "app": app, "root_path": "",
    }
    return Request(scope)


_SESSION_HEADERS = [(b"authorization", b"Bearer eyJ.sess")]


class TestLaneMarkers:
    def test_supabase_session_dict_carries_session_user_id_and_auth_lane(
            self, sb, monkeypatch):
        """Production JWT branch of get_current_team_session attaches
        session_user_id (the #2297/#2380 gate predicate) + the explicit
        auth_lane='session' documentation marker — in Supabase mode."""
        _, fake = sb
        _sb_membership(fake, _SB_TEAM, _U1, "owner")
        monkeypatch.setenv("TORTOISE_ABUSE_DISABLED", "1")

        async def _fake_verify(_request):
            return {"user_id": _U1, "email": "owner@example.com"}

        monkeypatch.setattr(sa, "verify_session_jwt", _fake_verify)
        team = asyncio.run(
            get_current_team_session_ungated(_make_request(_SESSION_HEADERS)))
        assert team["session_user_id"] == _U1
        assert team["auth_lane"] == "session"
        assert team["team_id"] == _SB_TEAM

    def test_override_seam_dicts_pass_through_unchanged(self, sb):
        """The dependency-override seam returns override dicts UNCHANGED —
        no markers fabricated. A test that injects session_user_id keeps the
        role gate live (the ⛔ invariant — the predicate is session_user_id
        presence, never the auth_lane marker)."""
        app.dependency_overrides[get_current_team] = lambda: {
            "team_id": _SB_TEAM, "key_id": "key-1", "tier": "free",
            "scopes": [], "legacy_full_access": True,
        }
        try:
            team = asyncio.run(
                get_current_team_session_ungated(_make_request(_SESSION_HEADERS)))
        finally:
            app.dependency_overrides.pop(get_current_team, None)
        assert "session_user_id" not in team  # key-auth shape → pass-through
        assert "auth_lane" not in team

    def test_override_seam_session_dict_still_gated(self, sb, as_user):
        """A dependency-override dict WITH session_user_id (emulated session
        — the ~15+ suites' dict(TEST_TEAM, session_user_id=...) shape) must
        still run the #2297/#2380 role gate: member face → mint 403."""
        tc, fake = sb
        _sb_membership(fake, _SB_TEAM, _U1, "member")
        app.dependency_overrides[get_current_team_session] = lambda: {
            "team_id": _SB_TEAM, "tier": "free", "key_id": None,
            "scopes": [], "legacy_full_access": True,
            "delegation_depth": None, "session_user_id": _U1,
            "max_api_keys": 2, "max_users": 1, "max_graphs": 1,
        }
        try:
            r = tc.post("/v1/team/keys", json={})
        finally:
            app.dependency_overrides.pop(get_current_team_session, None)
        _assert_role_403(r)
        assert fake.tables["api_keys"] == []  # nothing minted

    def test_override_seam_key_dict_passes_through_ungated(self, sb):
        """Key-auth-shaped override dict (no session_user_id) rides the
        CLASS gates only — the #2297/#2380 role gate must NOT fire (mint
        200, created_by falls back to 'api' as documented)."""
        tc, fake = sb
        app.dependency_overrides[get_current_team_session] = lambda: {
            "team_id": _SB_TEAM, "tier": "free", "key_id": "key-1",
            "scopes": [], "legacy_full_access": True,
            "delegation_depth": None, "graph_id": None,
            "max_api_keys": 2, "max_users": 1, "max_graphs": 1,
        }
        try:
            r = tc.post("/v1/team/keys", json={})
        finally:
            app.dependency_overrides.pop(get_current_team_session, None)
        assert r.status_code == 200, r.text
        assert fake.tables["api_keys"][0]["created_by"] == "api"

    def test_registry_key_auth_dict_lacks_session_markers(
            self, reg_client, reg):
        """Key-auth resolution in the REGISTRY control plane produces a team
        dict with NO session_user_id / auth_lane — the role gate predicate
        is absent, so key-auth mint semantics are unchanged (#2297/#2380)."""
        tc = reg_client  # noqa: F841
        _seed_team(reg, "team-r")
        token = "tt_marker_test_key_000000000001"
        _seed_membership(reg, "team-r", _U1, "owner")
        reg.query(
            "CREATE (k:APIKey {id:'marker-key', team_id:'team-r', "
            "key_hash:$h, key_prefix:$kp, created_via:'provisioned', "
            "created_at:'2026-01-01T00:00:00Z'})",
            params={"h": hash_api_key(token), "kp": token[:10]},
        )
        headers = [(b"authorization", f"Bearer {token}".encode())]
        team = asyncio.run(
            get_current_team_session_ungated(_make_request(headers)))
        assert team["key_id"] is not None  # key-auth lane resolved a key
        assert "session_user_id" not in team
        assert "auth_lane" not in team
