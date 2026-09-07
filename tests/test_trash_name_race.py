"""#2468 (post-#2304 audit P3): restore-vs-create name race.

Restore pre-checks the name under the per-team sweep lock; create_graph runs
under a different lock (_provision_lock), so a create can land the freed name
between the pre-check and the flip. On the SUPABASE lane the conditional
PATCH then trips the partial unique index (uq_graphs_team_name_active →
PostgREST 409 → RuntimeError) which must map to HTTP 409, not a 500. On the
REGISTRY lane (no unique index) both rows go live — the restore must roll
itself back (never leave duplicate live names) and 409. Both paths tested
here on the supabase-lane harness (monkeypatch isolates each branch).
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
_GID = "g_namerace0000000001"
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
        db_path = os.path.join(tmpdir, "namerace.db")
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


def test_supabase_duplicate_key_maps_to_409(sb_client, as_owner, monkeypatch):
    """The lane PATCH tripping uq_graphs_team_name_active (a create won the
    name between the pre-check and the flip) must surface as HTTP 409, not a
    500 — the fake control plane does not model the unique index, so the
    RuntimeError is injected at the seam."""
    tc, fake, _ = sb_client
    _seed(fake)
    as_owner()

    def _dupe(*_a, **_k):
        # Real seam shape: cp.query raises RuntimeError WITHOUT the PostgREST
        # body — the message is the status line only (#2468 VGATE catch).
        raise RuntimeError(
            "Supabase control-plane query failed (graphs): HTTP 409")

    monkeypatch.setattr("tortoise.hosted_api._make_sdk", lambda *a, **k: None)
    # Patch the lane seam the endpoint imports fresh (inside the endpoint).
    import tortoise.supabase_control as sc
    monkeypatch.setattr(sc, "restore_graph", _dupe)
    r = tc.post(f"/v1/graphs/trash/{_GID}/restore?team_id={_TEAM}")
    assert r.status_code == 409, r.text
    assert "concurrent create" in r.json()["detail"]


def test_registry_rollback_on_post_flip_name_conflict(sb_client, as_owner,
                                                      monkeypatch):
    """Registry-lane semantics exercised via the shared endpoint: when a
    concurrent create lands the name after the flip, the restore rolls the
    row back to the trash (never duplicate live names) and 409s."""
    tc, fake, _ = sb_client
    _seed(fake)
    as_owner()
    calls = {"n": 0}

    async def _conflict_first_false(*a, **k):
        # Pre-check (call 1): clean. Post-flip re-check (call 2): a create
        # landed the name — conflict.
        calls["n"] += 1
        return calls["n"] >= 2

    monkeypatch.setattr(ha_mod, "_trash_name_conflict",
                        _conflict_first_false)
    r = tc.post(f"/v1/graphs/trash/{_GID}/restore?team_id={_TEAM}")
    assert r.status_code == 409, r.text
    # The row is back in the trash (status deleted + a fresh deleted_at).
    rows = [g for g in fake.tables["graphs"] if g["id"] == _GID]
    assert rows and rows[0]["status"] == "deleted"
    assert rows[0]["deleted_at"]
