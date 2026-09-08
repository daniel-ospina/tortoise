"""#2465 (post-#2304 audit P2): trash recovery-window grace checks.

Restore must refuse past-window + legacy tombstones with 410 (the purge is
operator-invoked today — without this gate a graph deleted 60 days ago stays
restorable indefinitely, contradicting the shipped UI copy and privacy §6).
Covers the pure helper AND the endpoint behavior (session owner → 200 restore
inside the window, 410 past-window, 410 legacy).
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
from tortoise.backup_sweep import _GRAPH_PURGE_GRACE_DAYS
from tortoise.hosted_api import _trash_grace_expired, app, get_current_user


# #2566 (re-audit P3): restore and purge must share ONE window constant —
# the alias import makes drift impossible, but pin the invariant + the
# boundary semantics (purge erases at >= the window, restore refuses only
# strictly after it) so a regression is caught at the test layer too.
def test_grace_window_constant_is_single_sourced():
    from tortoise.hosted_api import _TRASH_GRACE_DAYS

    assert _TRASH_GRACE_DAYS == _GRAPH_PURGE_GRACE_DAYS == 7


def test_grace_purge_vs_restore_boundary_agreement():
    """A row aged exactly the window is purge-eligible (>=) but restore-until-
    the-purge (strictly >) — assert the two helpers agree on the boundary so
    drift cannot silently move either cutoff."""
    from tortoise.backup_sweep import _graph_purged_at_expired

    now = datetime.now(UTC)
    at_boundary = (now - timedelta(days=_GRAPH_PURGE_GRACE_DAYS)).isoformat()
    cutoff = (now - timedelta(days=_GRAPH_PURGE_GRACE_DAYS)).isoformat()
    # Purge erases AT the cutoff (<=); restore refuses only AFTER it (>).
    assert _graph_purged_at_expired(at_boundary, cutoff) is True
    assert _trash_grace_expired(at_boundary, now=now) is False
    # Just inside: neither erases nor refuses.
    inside = (now - timedelta(days=_GRAPH_PURGE_GRACE_DAYS,
                               seconds=-1)).isoformat()
    assert _graph_purged_at_expired(inside, cutoff) is False
    assert _trash_grace_expired(inside, now=now) is False



_TEAM = "team-free-001"
_GID = "g_grace000000000001"
_OWNER = "9f2c1a40-0000-4a00-8000-000000000001"


def _seed_graph(fake, *, deleted_at: str | None, purged_at: str | None = None):
    # Solo-like team (max_graphs=2): the default counts as 1, so one custom
    # restore fits under the cap — the #2467 quota gate (landing alongside)
    # must not block the grace-path 200 case. Free (cap 1) is always at cap.
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
        "deleted_at": deleted_at, "purged_at": purged_at,
    }])


@pytest.fixture
def sb_client(monkeypatch):
    """Supabase-mode TestClient with a fake control plane + temp DB
    (mirror of test_export_delete's fixture — kept local so this file is
    self-contained; #2127 shared helper for the SDK patch)."""
    fake = FakeControlPlane({"teams": [], "api_keys": [],
                             "team_memberships": [], "invitations": []})
    _enable_supabase(monkeypatch, fake)
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "grace.db")
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


# ── Pure helper ─────────────────────────────────────────────────────────────

def test_grace_expired_inside_window():
    now = datetime.now(UTC)
    assert _trash_grace_expired(
        (now - timedelta(days=1)).isoformat(), now=now) is False
    assert _trash_grace_expired(now.isoformat(), now=now) is False


def test_grace_expired_past_window_and_boundary():
    now = datetime.now(UTC)
    # Exactly at the boundary (7 days) — NOT yet past (strictly greater).
    assert _trash_grace_expired(
        (now - timedelta(days=7)).isoformat(), now=now) is False
    assert _trash_grace_expired(
        (now - timedelta(days=7, seconds=1)).isoformat(), now=now) is True
    assert _trash_grace_expired(
        (now - timedelta(days=60)).isoformat(), now=now) is True


def test_grace_expired_legacy_and_garbage():
    assert _trash_grace_expired(None) is True      # legacy tombstone
    assert _trash_grace_expired("") is True
    assert _trash_grace_expired("not-a-date") is True


# ── Endpoint behavior (owner session, supabase lane) ────────────────────────

def test_restore_inside_window_200(sb_client, as_owner):
    tc, fake, _ = sb_client
    _seed_graph(fake, deleted_at=(datetime.now(UTC)
                                  - timedelta(days=2)).isoformat())
    as_owner()
    r = tc.post(f"/v1/graphs/trash/{_GID}/restore?team_id={_TEAM}")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "restored"


def test_restore_past_window_410(sb_client, as_owner):
    tc, fake, _ = sb_client
    _seed_graph(fake, deleted_at=(datetime.now(UTC)
                                  - timedelta(days=60)).isoformat())
    as_owner()
    r = tc.post(f"/v1/graphs/trash/{_GID}/restore?team_id={_TEAM}")
    assert r.status_code == 410, r.text
    assert "recovery window" in r.json()["detail"]


def test_restore_legacy_tombstone_410(sb_client, as_owner):
    tc, fake, _ = sb_client
    _seed_graph(fake, deleted_at=None)  # pre-#2304 tombstone — past-grace
    as_owner()
    r = tc.post(f"/v1/graphs/trash/{_GID}/restore?team_id={_TEAM}")
    assert r.status_code == 410, r.text


def test_restore_non_owner_403(sb_client, as_owner):
    tc, fake, _ = sb_client
    _seed_graph(fake, deleted_at=(datetime.now(UTC)
                                  - timedelta(days=1)).isoformat())
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "9f2c1a40-0000-4a00-8000-000000000009"}  # non-member
    r = tc.post(f"/v1/graphs/trash/{_GID}/restore?team_id={_TEAM}")
    assert r.status_code == 403, r.text
