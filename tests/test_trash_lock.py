"""#2470 (post-#2304 audit P3): restore waits sweep-scale for the per-team
lock before 503ing (the old 20s timed acquire fired during the hourly
sweep's multi-minute team pass), with a neutral message + Retry-After.
"""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import tortoise.hosted_api as ha_mod
from tests._http_fixtures import patched_tortoise_sdk
from tests.fake_control_plane import FakeControlPlane
from tests.test_export_delete import _close_seed_sdks, _enable_supabase
from tests.test_supabase_control import FREE_TEAM
from tortoise.hosted_api import app, get_current_user

_TEAM = "team-free-001"
_GID = "g_lock0000000000001"
_OWNER = "9f2c1a40-0000-4a00-8000-000000000001"


def _seed(fake):
    team = dict(FREE_TEAM)
    team.update({"id": _TEAM, "tier": "solo", "max_graphs": 2})
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


@pytest.fixture
def sb_client(monkeypatch):
    fake = FakeControlPlane({"teams": [], "api_keys": [],
                             "team_memberships": [], "invitations": []})
    _enable_supabase(monkeypatch, fake)
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "lock.db")
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


def test_restore_503_when_team_lock_held(sb_client, as_owner, monkeypatch):
    """A held per-team lock (hourly sweep / purge pass) must produce a fast
    503 with a neutral message + Retry-After, not a 20s surprise."""
    tc, fake, _ = sb_client
    _seed(fake)
    as_owner()
    monkeypatch.setattr(ha_mod, "_TRASH_RESTORE_LOCK_TIMEOUT_S", 1)
    lock = ha_mod._sweep_team_lock(_TEAM)
    acquired = lock.acquire(blocking=False)
    assert acquired
    try:
        r = tc.post(f"/v1/graphs/trash/{_GID}/restore?team_id={_TEAM}")
        assert r.status_code == 503, r.text
        assert "backup operation is in flight" in r.json()["detail"]
        assert r.headers.get("Retry-After") == "300"
    finally:
        lock.release()


def test_restore_succeeds_when_lock_free(sb_client, as_owner, monkeypatch):
    tc, fake, _ = sb_client
    _seed(fake)
    as_owner()
    monkeypatch.setattr(ha_mod, "_TRASH_RESTORE_LOCK_TIMEOUT_S", 1)
    r = tc.post(f"/v1/graphs/trash/{_GID}/restore?team_id={_TEAM}")
    assert r.status_code == 200, r.text
