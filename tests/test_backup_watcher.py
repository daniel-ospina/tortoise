"""Tests for tortoise/backup_watcher.py — compute_status table + poll loop."""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone

from tortoise.alert_store import AlertStore
from tortoise.backup_watcher import (
    HEARTBEAT_KEY,
    BackupWatcher,
    compute_status,
)
from tortoise.hosted_backup import MemoryStorage

FIXED = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)  # noqa: UP017


def _ts(hours_ago: float) -> datetime:
    return FIXED - timedelta(hours=hours_ago)


class _Channels:
    def __init__(self):
        self.issues: dict[int, str] = {}
        self.telegram: list[str] = []
        self._next = 1

    def file_issue(self, title, body):
        n = self._next
        self._next += 1
        self.issues[n] = title
        return n

    def close_issue(self, number, comment=None):
        self.issues.pop(number, None)

    def search_open(self, kind):
        return [n for n, t in self.issues.items() if f"[DR] {kind}" in t]

    def push_telegram(self, text):
        self.telegram.append(text)


def _store(ch) -> AlertStore:
    return AlertStore(
        MemoryStorage(),
        file_issue=ch.file_issue, close_issue=ch.close_issue,
        search_open=ch.search_open, push_telegram=ch.push_telegram,
        repo="daniel-ospina/tortoise", assignee="u", now=lambda: FIXED,
    )


# ── compute_status table ────────────────────────────────────────────────────


def _status(**kw):
    base = dict(
        now=FIXED, teams=[], r2_teams=[], state_teams=[], newest_ts_by_team={},
        simulate_age=None, driver_heartbeat_ts=None, r2_ok=True, known_good=True,
        stale_threshold_min=90, driver_down_threshold_min=240,
        in_grace=False, kill_switch_off=False,
    )
    base.update(kw)
    return compute_status(**base)


def test_status_never_for_seam_team_without_archive():
    s = _status(teams=["team_a"])
    assert s["per_team"]["team_a"] == "never"


def test_status_stale_and_ok():
    s = _status(teams=["team_a", "team_b"], r2_teams=["team_a", "team_b"],
                state_teams=["team_a", "team_b"],
                newest_ts_by_team={"team_a": _ts(1), "team_b": _ts(100)})
    assert s["per_team"]["team_a"] == "ok"
    assert s["per_team"]["team_b"] == "stale"


def test_status_stamp_missing():
    s = _status(teams=["team_a"], r2_teams=["team_a"], state_teams=[],
                newest_ts_by_team={"team_a": _ts(1)})
    assert s["per_team"]["team_a"] == "stamp_missing"


def test_status_no_teams_signal():
    s = _status()
    assert s["no_teams"] is True
    assert s["per_team"] == {}


def test_status_unknown_on_fresh_boot_r2_down():
    s = _status(r2_ok=False, known_good=False)
    assert s["unknown"] is True


def test_status_driver_down():
    s = _status(driver_heartbeat_ts=_ts(300))
    assert s["driver_down"] is True
    s2 = _status(driver_heartbeat_ts=_ts(1))
    assert s2["driver_down"] is False


def test_status_driver_down_suppressed_by_kill_switch():
    s = _status(driver_heartbeat_ts=_ts(300), kill_switch_off=True)
    assert s["driver_down"] is False


def test_status_simulate_forces_stale():
    s = _status(teams=["team_a"], r2_teams=["team_a"], state_teams=["team_a"],
                newest_ts_by_team={"team_a": _ts(1)},
                simulate_age=_ts(200))
    assert s["per_team"]["team_a"] == "stale"


def test_status_backup_set_missing():
    s = _status(state_teams=["team_x"], r2_teams=[])
    assert s["backup_set_missing"] == ["team_x"]


# ── watcher poll ────────────────────────────────────────────────────────────


def _seed_archive(storage, team: str, hours_ago: float) -> None:
    """Seed with the REAL create_backup key shape: {YYYYMMDD}T{HHMMSSmmm}Z_{rnd}
    (the random suffix is what the freshness parser must strip — review P1-1)."""
    ts = _ts(hours_ago)
    key = f"{ts.strftime('%Y%m%dT%H%M%S')}{ts.microsecond // 1000:03d}Z_{secrets.token_hex(4)}"
    backup_id = f"{team}/{key}"
    manifest = {"backup_id": backup_id, "team_id": team, "graph_name": f"team_{team}",
                "created_at": ts.isoformat(), "node_count": 1, "edge_count": 0,
                "sha256": "0" * 64}
    storage.upload(f"backups/{backup_id}/manifest.json", json.dumps(manifest).encode())
    storage.upload(f"backups/{backup_id}/dump.enc", b"x")


def _seed_state(storage, team: str) -> None:
    storage.upload(
        f"ops/teams/{team}/state.json",
        json.dumps({"node_count": 1, "updated_at": FIXED.isoformat()}).encode(),
    )


def _watcher(storage, ch, *, grace_min=0, teams=("team_a",), now_fn=None) -> BackupWatcher:
    store = _store(ch)
    return BackupWatcher(
        storage, store,
        team_provider=lambda: list(teams),
        state_reader=lambda t: {},
        driver_heartbeat_reader=lambda: {},
        stale_threshold_min=90, driver_down_threshold_min=240,
        grace_min=grace_min, now=now_fn or (lambda: FIXED),
    )


def test_watcher_opens_stale_incident_and_resolves():
    ch = _Channels()
    storage = MemoryStorage()
    _seed_archive(storage, "team_a", 200)  # stale (>90 min)
    _seed_state(storage, "team_a")
    w = _watcher(storage, ch)
    status = w.poll()
    assert status["per_team"]["team_a"] == "stale"
    assert len(ch.issues) == 1 and "[DR] STALE" in list(ch.issues.values())[0]  # noqa: RUF015
    assert any("STALE" in t for t in ch.telegram)

    # New backup → next poll resolves.
    _seed_archive(storage, "team_a", 0.5)
    w.poll()
    assert ch.issues == {}
    assert any("resolved" in t.lower() for t in ch.telegram)


def test_watcher_never_fires_on_chronic_no_teams():
    ch = _Channels()
    storage = MemoryStorage()
    w = _watcher(storage, ch, teams=())
    status = w.poll()
    assert status["no_teams"] is True
    assert ch.issues == {}
    assert ch.telegram == []


def test_watcher_unknown_silence_on_r2_down_fresh():
    class _Boom(MemoryStorage):
        def list(self, prefix):
            raise ConnectionError("r2 down")

    ch = _Channels()
    w = _watcher(_Boom(), ch, teams=("team_a",))
    status = w.poll()
    assert status["unknown"] is True
    assert ch.issues == {}


def test_watcher_never_fires_during_grace():
    ch = _Channels()
    storage = MemoryStorage()
    _seed_archive(storage, "team_a", 200)
    w = _watcher(storage, ch, grace_min=120)
    w.poll()
    assert ch.issues == {}


def test_watcher_driver_down_opens_incident():
    ch = _Channels()
    storage = MemoryStorage()

    def _hb():
        return {"ran_at": _ts(300).isoformat()}

    store = _store(ch)
    w = BackupWatcher(
        storage, store,
        team_provider=lambda: [], state_reader=lambda t: {},
        driver_heartbeat_reader=_hb,
        stale_threshold_min=90, driver_down_threshold_min=240,
        grace_min=0, now=lambda: FIXED,
    )
    status = w.poll()
    assert status["driver_down"] is True
    assert any("[DR] DRIVER_DOWN" in t for t in ch.issues.values())


def test_watcher_writes_heartbeat():
    ch = _Channels()
    storage = MemoryStorage()
    w = _watcher(storage, ch)
    w.poll()
    hb = json.loads(storage.download(HEARTBEAT_KEY))
    assert hb["r2_ok"] is True
    assert "team_a" in hb["status"]


def test_watcher_degraded_mode_keeps_cached_surface():
    """R2 read failure after a known-good poll must NOT reclassify ok teams as
    stale from an empty cache (review P1-2 — degraded evaluates the cached
    surface, never fabricates)."""
    ch = _Channels()
    storage = MemoryStorage()
    _seed_archive(storage, "team_a", 1)   # fresh → ok
    _seed_state(storage, "team_a")
    w = _watcher(storage, ch)
    w.poll()
    assert w._last_status["per_team"]["team_a"] == "ok"

    class _Boom(MemoryStorage):
        def list(self, prefix):
            raise ConnectionError("r2 down")

    w._storage = _Boom()
    status2 = w.poll()
    # Degraded: the cached surface holds — team_a is NOT reclassified stale.
    assert status2["per_team"]["team_a"] == "ok"
    # R2_DOWN is the one incident that SHOULD fire while degraded (neither
    # fabricate NOR silence); no STALE for the healthy team.
    assert list(ch.issues.values()) == ["[DR] R2_DOWN"]


def test_watcher_production_key_parse():
    """The freshness parser must handle real create_backup keys (suffix
    stripped) — regression for review P1-1."""
    ch = _Channels()
    storage = MemoryStorage()
    _seed_archive(storage, "team_a", 1)  # production-shaped key
    _seed_state(storage, "team_a")
    w = _watcher(storage, ch)
    status = w.poll()
    assert status["per_team"]["team_a"] == "ok"  # fresh (1h < 90min)
