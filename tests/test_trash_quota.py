"""#2467 (post-#2304 audit P3): trash restore must respect the team's
max_graphs quota (a restore is a create-equivalent for the quota meter —
delete freed the slot, restoring re-consumes it). At-cap → 409
X-Graph-Quota; under-cap → 200. Supabase lane, fake control plane.
"""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from tests._http_fixtures import patched_tortoise_sdk
from tests.fake_control_plane import FakeControlPlane
from tests.test_export_delete import _close_seed_sdks, _enable_supabase
from tests.test_supabase_control import FREE_TEAM
from tortoise.hosted_api import app, get_current_user

_TEAM = "team-free-001"
_GID = "g_quota000000000001"
_GID2 = "g_quota000000000002"
_OWNER = "9f2c1a40-0000-4a00-8000-000000000001"


def _seed_team(fake, *, max_graphs, active_customs: int = 0):
    team = dict(FREE_TEAM)
    team.update({"id": _TEAM, "tier": "solo", "max_graphs": max_graphs})
    fake.seed("teams", [team])
    fake.seed("team_memberships", [{
        "id": "m-1", "team_id": _TEAM, "user_id": _OWNER, "role": "owner",
        "status": "active",
    }])
    rows = []
    for i in range(active_customs):
        rows.append({
            "id": f"g_live{i:013d}", "team_id": _TEAM, "name": f"live-{i}",
            "kind": "custom", "namespace": f"team_{_TEAM}_g_live{i:013d}",
            "status": "active", "deleted_at": None, "purged_at": None,
        })
    rows.append({
        "id": _GID, "team_id": _TEAM, "name": "old-bot", "kind": "custom",
        "namespace": f"team_{_TEAM}_{_GID}", "status": "deleted",
        "deleted_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
        "purged_at": None,
    })
    fake.seed("graphs", rows)


@pytest.fixture
def sb_client(monkeypatch):
    fake = FakeControlPlane({"teams": [], "api_keys": [],
                             "team_memberships": [], "invitations": []})
    _enable_supabase(monkeypatch, fake)
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "quota.db")
        with patched_tortoise_sdk(db_path):
            try:
                with TestClient(app) as tc:
                    yield tc, fake, db_path
            finally:
                _close_seed_sdks()


@pytest.fixture
def as_owner():
    def _set():
        app.dependency_overrides[get_current_user] = lambda: {"user_id": _OWNER}

    yield _set
    app.dependency_overrides.pop(get_current_user, None)


def test_restore_at_cap_409(sb_client, as_owner):
    """max_graphs=2: default + one live custom = at cap → restore 409
    X-Graph-Quota (delete freed the slot, restoring re-consumes it)."""
    tc, fake, _ = sb_client
    _seed_team(fake, max_graphs=2, active_customs=1)
    as_owner()
    r = tc.post(f"/v1/graphs/trash/{_GID}/restore?team_id={_TEAM}")
    assert r.status_code == 409, r.text
    assert "X-Graph-Quota" in r.headers


def test_restore_under_cap_200(sb_client, as_owner):
    """max_graphs=2, only the default active → restore fits under the cap."""
    tc, fake, _ = sb_client
    _seed_team(fake, max_graphs=2, active_customs=0)
    as_owner()
    r = tc.post(f"/v1/graphs/trash/{_GID}/restore?team_id={_TEAM}")
    assert r.status_code == 200, r.text


def test_restore_unlimited_tier_no_gate(sb_client, as_owner):
    """pro/team tier (max_graphs row-null AND tier default null) → the
    quota gate's early-skip fires; restore succeeds."""
    tc, fake, _ = sb_client
    team = dict(FREE_TEAM)
    team.update({"id": _TEAM, "tier": "team", "max_graphs": None})
    fake.seed("teams", [team])
    fake.seed("team_memberships", [{
        "id": "m-1", "team_id": _TEAM, "user_id": _OWNER, "role": "owner",
        "status": "active",
    }])
    fake.seed("graphs", [{
        "id": _GID, "team_id": _TEAM, "name": "old-bot", "kind": "custom",
        "namespace": f"team_{_TEAM}_{_GID}", "status": "deleted",
        "deleted_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
        "purged_at": None,
    }])
    as_owner()
    r = tc.post(f"/v1/graphs/trash/{_GID}/restore?team_id={_TEAM}")
    assert r.status_code == 200, r.text
