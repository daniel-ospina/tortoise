"""#2469 (post-#2304 audit P3): Inspect (trash rescue) must not understate
what can be restored — count archive RUNS (dump-only runs from a crash
window included) plus legacy flat archives of the graph, not just nested
manifests. Supabase lane, fake control plane; storage monkeypatched.
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
from tortoise.hosted_backup import MemoryStorage

_TEAM = "team-free-001"
_GID = "g_inspect00000000001"
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
        db_path = os.path.join(tmpdir, "inspect.db")
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


def _seed_storage(monkeypatch) -> MemoryStorage:
    store = MemoryStorage()
    # Nested pool: one full run (dump+manifest) + one DUMP-ONLY run (the
    # create_backup crash window — manifest never uploaded).
    store.upload(f"backups/{_TEAM}/{_GID}/runA/dump.enc", b"x")
    store.upload(f"backups/{_TEAM}/{_GID}/runA/manifest.json",
                 b'{"backup_id":"b1","created_at":"2026-09-01T00:00:00Z",'
                 b'"node_count":10,"edge_count":3}')
    store.upload(f"backups/{_TEAM}/{_GID}/runB/dump.enc", b"orphan")
    # Legacy FLAT archives of this graph (index entry carries the gid).
    store.upload(f"backups/{_TEAM}/flat01/dump.enc", b"flat")
    store.upload("ops/legacy-flat-index/team-free-001.json",
                 b'{"team-free-001/flat01": {"graph_name": '
                 b'"team_team-free-001_g_inspect00000000001", '
                 b'"graph_id": "g_inspect00000000001"}}')
    monkeypatch.setattr(ha_mod, "_backup_storage", lambda: store)
    return store


def test_inspect_counts_runs_and_flat_archives(sb_client, as_owner,
                                               monkeypatch):
    tc, fake, _ = sb_client
    _seed(fake)
    _seed_storage(monkeypatch)
    as_owner()
    r = tc.get(f"/v1/graphs/trash/{_GID}/points?team_id={_TEAM}")
    assert r.status_code == 200, r.text
    body = r.json()
    # runA (full) + runB (dump-only) + flat01 = 3 restorable archives —
    # the old manifest-count said 1.
    assert body["archive_count"] == 3, body
    assert body["latest_backup"]["node_count"] == 10


def test_inspect_no_archives_reports_zero(sb_client, as_owner, monkeypatch):
    tc, fake, _ = sb_client
    _seed(fake)
    monkeypatch.setattr(ha_mod, "_backup_storage",
                        lambda: MemoryStorage())
    as_owner()
    r = tc.get(f"/v1/graphs/trash/{_GID}/points?team_id={_TEAM}")
    assert r.status_code == 200, r.text
    assert r.json()["archive_count"] == 0


def test_inspect_excludes_purge_ghosted_flat_bids(sb_client, as_owner,
                                                   monkeypatch):
    """#2561 (re-audit P3): Inspect must not count legacy-flat bids whose
    objects a purge already erased — the purge records them as ghosts even
    when its index rewrite was skipped (partial delete failure)."""
    tc, fake, _ = sb_client
    _seed(fake)
    store = _seed_storage(monkeypatch)
    # The purge erased flat01's dumps and ghost-recorded the bid; the index
    # rewrite was skipped (partial failure), so the stale entry remains.
    store.delete(f"backups/{_TEAM}/flat01/dump.enc")
    store.upload("ops/purge-flat-ghosts/team-free-001.json",
                 b'{"team-free-001/flat01": {"erased_at": "2026-09-02T00:00:00Z"}}')
    as_owner()
    r = tc.get(f"/v1/graphs/trash/{_GID}/points?team_id={_TEAM}")
    assert r.status_code == 200, r.text
    # runA + runB only — flat01 is ghosted out.
    assert r.json()["archive_count"] == 2, r.json()


def test_inspect_counts_flat_when_not_ghosted(sb_client, as_owner,
                                              monkeypatch):
    """A non-ghosted flat (index entry, objects present) still counts."""
    tc, fake, _ = sb_client
    _seed(fake)
    store = _seed_storage(monkeypatch)
    store.upload("ops/purge-flat-ghosts/team-free-001.json",
                 b'{"team-free-001/OTHER-bid": {"erased_at": "2026-09-02T00:00:00Z"}}')
    as_owner()
    r = tc.get(f"/v1/graphs/trash/{_GID}/points?team_id={_TEAM}")
    assert r.status_code == 200, r.text
    assert r.json()["archive_count"] == 3, r.json()
