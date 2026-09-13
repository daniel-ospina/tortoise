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

    def search_open(self, kind, team_id=""):
        return [
            n for n, t in self.issues.items()
            if f"[DR] {kind}" in t
            and (team_id == "" or t.endswith(f" — {team_id}"))
        ]

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


def _watcher(storage, ch, *, grace_min=0, teams=("team_a",), now_fn=None,
              graph_provider=None) -> BackupWatcher:
    store = _store(ch)
    return BackupWatcher(
        storage, store,
        team_provider=lambda: list(teams),
        state_reader=lambda t: {},
        graph_provider=graph_provider,
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


# ── #2313 Task 4: per-graph freshness + watcher custom-graph surface ───────


def _seed_graph_archive(storage, team: str, gid: str, hours_ago: float) -> None:
    """Seed a per-graph (nested ``default``/custom segment) archive."""
    ts = _ts(hours_ago)
    key = f"{ts.strftime('%Y%m%dT%H%M%S')}{ts.microsecond // 1000:03d}Z_{secrets.token_hex(4)}"
    backup_id = f"{team}/{gid}/{key}"
    manifest = {"backup_id": backup_id, "team_id": team, "graph_id": gid,
                "graph_name": f"g-{gid}", "created_at": ts.isoformat(),
                "node_count": 1, "edge_count": 0, "sha256": "0" * 64}
    storage.upload(f"backups/{backup_id}/manifest.json",
                   json.dumps(manifest).encode())
    storage.upload(f"backups/{backup_id}/dump.enc", b"x")


def _seed_graph_state(storage, team: str, gid: str) -> None:
    storage.upload(
        f"ops/teams/{team}/graphs/{gid}/state.json",
        json.dumps({"node_count": 1, "updated_at": FIXED.isoformat()}).encode(),
    )


def test_newest_backup_ts_reads_legacy_flat_and_nested_default():
    """Team freshness = max over pre-#2313 flat dumps and the default graph's
    nested (``default`` segment) dumps; custom nested keys excluded."""
    from tortoise.backup_watcher import _newest_backup_ts
    storage = MemoryStorage()
    _seed_archive(storage, "team_a", 200)         # legacy flat — stale
    _seed_graph_archive(storage, "team_a", "default", 0.5)  # nested default — fresh
    _seed_graph_archive(storage, "team_a", "g_custom", 0.2)  # custom — NOT team-level
    newest = _newest_backup_ts(storage, "team_a")
    assert newest is not None
    age_min = (FIXED - newest).total_seconds() / 60.0
    assert 25 < age_min < 35  # the 0.5h nested default won, not the 200h flat


def test_newest_graph_backup_ts_scoped():
    from tortoise.backup_watcher import _newest_graph_backup_ts
    storage = MemoryStorage()
    _seed_graph_archive(storage, "team_a", "g_a", 200)
    _seed_graph_archive(storage, "team_a", "g_b", 0.5)
    assert _newest_graph_backup_ts(storage, "team_a", "g_a") is not None
    oldest = _newest_graph_backup_ts(storage, "team_a", "g_a")
    newest = _newest_graph_backup_ts(storage, "team_a", "g_b")
    assert newest > oldest  # type: ignore[operator]
    assert _newest_graph_backup_ts(storage, "team_a", "g_none") is None


def test_watcher_custom_graph_stale_opens_incident_with_graph_subject():
    ch = _Channels()
    storage = MemoryStorage()
    # Healthy default baseline (team-level ok) so ONLY the custom graph is
    # the same-kind filer (the AlertStore mock's search_open matches kind
    # only; the real GH search is subject-scoped).
    _seed_archive(storage, "team_a", 0.5)
    _seed_state(storage, "team_a")
    _seed_graph_archive(storage, "team_a", "g_x", 200)  # stale (>90 min)
    _seed_graph_state(storage, "team_a", "g_x")
    w = _watcher(storage, ch, graph_provider=lambda t: ["g_x"])
    status = w.poll()
    assert status["per_graph"]["team_a:g_x"] == "stale"
    # incident subject carries the graph identity (issue + telegram)
    assert any("STALE — team_a:g_x" in t for t in list(ch.issues.values()))
    assert any("team_a:g_x" in t for t in ch.telegram)

    # Fresh custom backup → resolved.
    _seed_graph_archive(storage, "team_a", "g_x", 0.5)
    w.poll()
    assert ch.issues == {}


def test_watcher_custom_never_and_stamp_missing():
    ch = _Channels()
    storage = MemoryStorage()
    _seed_archive(storage, "team_a", 0.5)
    _seed_state(storage, "team_a")
    # g_never: seam graph with no archives → NEVER_BACKED_UP (graph subject)
    w = _watcher(storage, ch, graph_provider=lambda t: ["g_never", "g_stamp"])
    status = w.poll()
    assert status["per_graph"]["team_a:g_never"] == "never"
    assert status["per_graph"]["team_a:g_stamp"] == "never"
    tg = [t for t in ch.telegram]
    assert any("NEVER_BACKED_UP" in t and "team_a:g_never" in t for t in tg)
    assert any("NEVER_BACKED_UP" in t and "team_a:g_stamp" in t for t in tg)
    ch2 = _Channels()
    # g_stamp has archives but no per-graph state → METADATA_LOST
    storage2 = MemoryStorage()
    _seed_archive(storage2, "team_a", 0.5)
    _seed_state(storage2, "team_a")
    _seed_graph_archive(storage2, "team_a", "g_stamp", 0.5)
    w2 = _watcher(storage2, ch2, graph_provider=lambda t: ["g_stamp"])
    status2 = w2.poll()
    assert status2["per_graph"]["team_a:g_stamp"] == "stamp_missing"
    assert any("METADATA_LOST" in t and "team_a:g_stamp" in t for t in ch2.telegram)


def test_watcher_custom_state_without_archives_is_backup_set_missing():
    """#2374 regression: per-graph state that EXISTS with NO archives
    classifies as the backup-set-missing class (archives lost/pruned) — not
    "never" (never backed up is impossible once state exists: state is
    written only after a successful dump). Wrong kind sends triage astray
    after a bulk archive deletion."""
    ch = _Channels()
    storage = MemoryStorage()
    _seed_state(storage, "team_a")
    # g_x: per-graph state present, NO archives on a CONFIRMED scan.
    _seed_graph_state(storage, "team_a", "g_x")
    w = _watcher(storage, ch, graph_provider=lambda t: ["g_x"])
    status = w.poll()
    assert status["per_graph"]["team_a:g_x"] == "backup_set_missing"
    assert any("BACKUP_SET_MISSING" in t and "team_a:g_x" in t
               for t in list(ch.issues.values()))
    # Recovery: a fresh default + custom archive land → ok, incidents resolved.
    _seed_archive(storage, "team_a", 0.5)
    _seed_graph_archive(storage, "team_a", "g_x", 0.5)
    w.poll()
    assert ch.issues == {}


def test_watcher_custom_graph_removal_resolves_incidents():
    ch = _Channels()
    storage = MemoryStorage()
    _seed_archive(storage, "team_a", 0.5)
    _seed_state(storage, "team_a")
    _seed_graph_archive(storage, "team_a", "g_gone", 200)
    _seed_graph_state(storage, "team_a", "g_gone")
    w = _watcher(storage, ch, graph_provider=lambda t: ["g_gone"])
    w.poll()
    assert any("STALE — team_a:g_gone" in t for t in list(ch.issues.values()))
    # graph deleted from the control plane → provider drops it → resolved
    w._graphs_for = lambda t: []
    w.poll()
    assert ch.issues == {}


class _BoomListStorage(MemoryStorage):
    """MemoryStorage whose ``list`` raises — simulates R2 read failure AFTER a
    healthy poll so the watcher evaluates degraded-from-known-good."""

    def list(self, prefix):
        raise RuntimeError("r2 down")


def test_watcher_degraded_never_fabrication_for_new_custom_graph():
    """F1 regression (#2313 Task 4): NEVER_BACKED_UP requires a confirmed
    listing. On a degraded poll (R2 down), a custom graph never seen in a
    confirmed scan must NOT be classified never — stale at worst."""
    ch = _Channels()
    storage = MemoryStorage()
    _seed_archive(storage, "team_a", 0.5)
    _seed_state(storage, "team_a")
    _seed_graph_archive(storage, "team_a", "g_old", 0.5)
    _seed_graph_state(storage, "team_a", "g_old")
    w = _watcher(storage, ch, graph_provider=lambda t: ["g_old"])
    s1 = w.poll()
    assert s1["per_graph"]["team_a:g_old"] == "ok"  # healthy baseline

    # R2 dies; a NEW custom graph joins the seam surface mid-outage.
    w._storage = _BoomListStorage()
    w._graphs_for = lambda t: ["g_old", "g_new"]
    s2 = w.poll()
    # g_old from the cache stays ok; g_new (unconfirmed) is stale, never NEVER.
    assert s2["per_graph"]["team_a:g_old"] == "ok"
    assert s2["per_graph"]["team_a:g_new"] == "stale"
    assert not any("NEVER_BACKED_UP" in t and "team_a:g_new" in t
                   for t in ch.telegram)

    # R2 recovers; the scan is now a CONFIRMED listing — g_new has no archives
    # → never is legitimate again.
    w._storage = MemoryStorage()
    w._storage._objects = dict(storage._objects)
    s3 = w.poll()
    assert s3["per_graph"]["team_a:g_new"] == "never"
    assert any("NEVER_BACKED_UP" in t and "team_a:g_new" in t
               for t in ch.telegram)


def _seed_default_state_with_name(storage, team, name):
    storage.upload(
        f"ops/teams/{team}/graphs/default/state.json",
        json.dumps({"node_count": 1, "graph_name": name,
                    "updated_at": FIXED.isoformat()}).encode(),
    )


def test_watcher_shrink_does_not_resolve_on_unconfirmed_surface():
    """FIX-C regression: a failed control-plane read (provider None) must
    never resolve real custom-graph incidents — the mirror of NEVER requiring
    a confirmed listing (a CP blip at sweep time is when customs age)."""
    ch = _Channels()
    storage = MemoryStorage()
    _seed_archive(storage, "team_a", 0.5)
    _seed_state(storage, "team_a")
    _seed_graph_archive(storage, "team_a", "g_x", 200)  # stale
    _seed_graph_state(storage, "team_a", "g_x")
    w = _watcher(storage, ch, graph_provider=lambda t: ["g_x"])
    w.poll()
    assert any("STALE — team_a:g_x" in t for t in list(ch.issues.values()))

    # CP blip → provider returns None (unconfirmed). Issue must SURVIVE and
    # the poll must NOT crash — the team-level surface keeps evaluating
    # (per_team present, no poll_error, per_graph empty not fabricated).
    w._graphs_for = lambda t: None
    s2 = w.poll()
    assert not s2.get("poll_error"), s2
    assert s2.get("per_team", {}) is not None
    assert s2.get("per_graph") == {}
    assert any("STALE — team_a:g_x" in t for t in list(ch.issues.values())), \
        "an unconfirmed surface must not resolve real incidents"

    # Genuine deletion → provider returns [] (confirmed empty) → resolved.
    w._graphs_for = lambda t: []
    w.poll()
    assert not any("STALE — team_a:g_x" in t for t in list(ch.issues.values()))


def test_watcher_legacy_custom_flat_does_not_gate_team_freshness():
    """FIX-F regression: a pre-#2313 C5-era flat on-demand dump of a CUSTOM
    graph (flat key, custom namespace as graph_name) is NOT the default — it
    must not keep team freshness green while the default is stale."""
    storage = MemoryStorage()
    # default per-graph state names the default graph
    _seed_default_state_with_name(storage, "team_a", "team_team_a")
    # stale DEFAULT flat dump
    _seed_archive(storage, "team_a", 200)
    # FRESH pre-#2313 custom-era flat dump (custom namespace as graph_name)
    ts = _ts(0.2)
    key = f"{ts.strftime('%Y%m%dT%H%M%S')}{ts.microsecond // 1000:03d}Z_{secrets.token_hex(4)}"
    backup_id = f"team_a/{key}"
    m = {"backup_id": backup_id, "team_id": "team_a",
         "graph_name": "team_team_a_g_custom",  # a custom namespace
         "created_at": ts.isoformat(), "node_count": 1, "edge_count": 0,
         "sha256": "0" * 64}
    storage.upload(f"backups/{backup_id}/manifest.json",
                   json.dumps(m).encode())
    storage.upload(f"backups/{backup_id}/dump.enc", b"x")
    from tortoise.backup_watcher import _newest_backup_ts
    newest = _newest_backup_ts(storage, "team_a")
    # the DEFAULT's stale flat dump (200h old = 12000 min) governs — the
    # fresh custom-era flat (0.2h) must NOT mask it
    assert newest is not None
    age_min = (FIXED - newest).total_seconds() / 60.0
    assert age_min > 10000, f"custom-era flat masked default staleness ({age_min})"
    assert age_min > 5000


def test_watcher_custom_per_graph_state_does_not_mask_missing_default_mirror():
    """#2367 regression: the team-level state scan must match ONLY the team
    mirror (ops/teams/{t}/state.json). The pre-fix len>=4 filter absorbed the
    #2313 per-graph state keys (ops/teams/{t}/graphs/{gid}/state.json), so a
    present custom per-graph state silently suppressed the DEFAULT graph's
    METADATA_LOST when its team mirror was missing (the default rides ONLY the
    team surface — no other loop compensates)."""
    ch = _Channels()
    storage = MemoryStorage()
    # Default graph: FRESH archive, team-level state mirror ABSENT.
    _seed_archive(storage, "team_a", 0.5)
    # Custom graph: fresh archive + per-graph state present (healthy #2313
    # surface) — its 6-segment key must not stand in for the team mirror.
    _seed_graph_archive(storage, "team_a", "g_x", 0.5)
    _seed_graph_state(storage, "team_a", "g_x")
    w = _watcher(storage, ch, graph_provider=lambda t: ["g_x"])
    status = w.poll()
    # The custom graph is healthy on its own per-graph surface...
    assert status["per_graph"]["team_a:g_x"] == "ok"
    # ...but the default's missing mirror still fires METADATA_LOST.
    assert status["per_team"]["team_a"] == "stamp_missing"
    assert list(ch.issues.values()) == ["[DR] METADATA_LOST — team_a"]


def _seed_flat_manifest(storage, team: str, graph_name: str,
                        hours_ago: float) -> str:
    """Seed a legacy FLAT manifest (pre-#2313 key shape) and return its
    backup_id (team/key)."""
    ts = _ts(hours_ago)
    key = (f"{ts.strftime('%Y%m%dT%H%M%S')}"
           f"{ts.microsecond // 1000:03d}Z_{secrets.token_hex(4)}")
    backup_id = f"{team}/{key}"
    m = {"backup_id": backup_id, "team_id": team, "graph_name": graph_name,
         "created_at": ts.isoformat(), "node_count": 1, "edge_count": 0,
         "sha256": "0" * 64}
    storage.upload(f"backups/{backup_id}/manifest.json",
                   json.dumps(m).encode())
    storage.upload(f"backups/{backup_id}/dump.enc", b"x")
    return backup_id


def _seed_legacy_flat_index(storage, team: str,
                            entries: dict[str, dict]) -> None:
    from tortoise.backup_sweep import _legacy_flat_index_key
    storage.upload(_legacy_flat_index_key(team),
                   json.dumps(entries).encode())


def test_watcher_legacy_flat_index_is_authoritative():
    """#2370: the sweep-written legacy-flat index decides flat classification
    — a flat the index marks DEFAULT counts toward team freshness even though
    its manifest names a custom namespace; the same R2 content with an index
    entry marking it CUSTOM is excluded. (Pre-index the manifest read decided
    by graph_name vs the default name; the index is the sweep's once-only
    classification.)"""
    from tortoise.backup_watcher import _newest_backup_ts
    for index_kind, counts in (("default", True), ("custom", False)):
        storage = MemoryStorage()
        bid = _seed_flat_manifest(storage, "team_a",
                                  "team_team_a_g_custom", 0.2)
        gid = "default" if index_kind == "default" else "g_custom"
        _seed_legacy_flat_index(storage, "team_a",
                                {bid: {"graph_name": "team_team_a_g_custom",
                                       "graph_id": gid}})
        newest = _newest_backup_ts(storage, "team_a")
        if counts:
            assert newest is not None, index_kind
            age_min = (FIXED - newest).total_seconds() / 60.0
            assert age_min < 90, index_kind
        else:
            assert newest is None, index_kind


class _ManifestBoomStorage(MemoryStorage):
    """MemoryStorage whose flat-manifest downloads raise — simulates a
    transient object-read failure (the #2370 fabricated-STALE trigger)."""

    def download(self, key):
        if key.endswith("/manifest.json"):
            raise RuntimeError("transient r2 read failure")
        return super().download(key)


def test_watcher_transient_manifest_read_failure_no_fabricated_stale():
    """#2370 P2-2 regression: an unreadable flat manifest must NOT drop the
    newest archive from freshness (pre-fix `except Exception: continue`
    excluded it → fabricated spurious STALE). Count-as-default is the
    pre-#2313 key-derived parity — exists ⇒ counts this cycle."""
    ch = _Channels()
    storage = _ManifestBoomStorage()
    _seed_default_state_with_name(storage, "team_a", "team_team_a")
    _seed_state(storage, "team_a")  # team mirror (team surface)
    _seed_archive(storage, "team_a", 0.5)  # fresh flat default (0.5 h)
    w = _watcher(storage, ch)
    status = w.poll()
    # fresh (0.5 h < 90 min) — NOT stale, no incident
    assert status["per_team"]["team_a"] == "ok"
    assert not any("STALE" in t for t in list(ch.issues.values()))


# ── #3031: a broken alerter must never fabricate WATCHER_DOWN ────────────────


def test_alert_path_failure_still_writes_the_heartbeat():
    """#3031: the alert store shares the R2 storage, so the degraded condition
    R2_DOWN reports is exactly what makes `open_incident` raise. Uncontained,
    that exception escaped the poll and skipped the heartbeat — and the DR driver
    then filed WATCHER_DOWN against a watcher that was alive, masking the real
    R2 fault.

    The fixture seeds a STALE team, so the alert legs DO run: `open_incident` is
    the first leg that fires (never resolving), and it raises here.
    """
    ch = _Channels()
    storage = MemoryStorage()
    _seed_archive(storage, "team_a", 200)   # stale → the alert legs fire
    _seed_state(storage, "team_a")
    w = _watcher(storage, ch)

    calls: list[tuple[str, str]] = []

    def _boom(kind, team_id="", detail=None):
        calls.append(("open", f"{kind}/{team_id}"))
        raise RuntimeError("R2 read outage while filing the incident")

    w._alerts.open_incident = _boom  # type: ignore[method-assign]
    status = w.poll()

    # The failure was actually reached (no vacuous pass: the leg ran and raised).
    assert calls == [("open", "STALE/team_a")], calls
    hb = json.loads(storage.download(HEARTBEAT_KEY))
    assert hb["last_poll_at"], "the heartbeat must be written despite the alert failure"
    assert status["per_team"]["team_a"] == "stale"
    assert ch.issues == {}, "nothing could be filed — the store is down"


def test_vanished_graph_resolution_failure_is_contained():
    """#3031 (review): the universe-shrink resolutions used to run on the poll's
    critical path OUTSIDE any containment — a raise there escaped before the
    heartbeat and fabricated WATCHER_DOWN. Same gate, now contained, and the
    `_last_graph_keys` update happens only AFTER a successful pass so the retry is
    not lost."""
    ch = _Channels()
    storage = MemoryStorage()
    _seed_archive(storage, "team_a", 2)
    _seed_state(storage, "team_a")
    w = _watcher(storage, ch)
    w._last_graph_keys = {"team_a", "team_a:vanished"}

    def _boom(kind, team_id=""):
        raise RuntimeError("alert store down")

    w._alerts.resolve_incident = _boom  # type: ignore[method-assign]
    w.poll()

    assert json.loads(storage.download(HEARTBEAT_KEY))["last_poll_at"]
    # `resolve_incident` RAISES on a failed close (a `False` return only means
    # "nothing open"), and the failed key is kept so the next poll's `prev - cur`
    # diff re-includes it. Retiring it would document a retry that never happens.
    assert "team_a:vanished" in w._last_graph_keys
    attempts = {"n": 0}

    def _count(kind, team_id=""):
        attempts["n"] += 1
        raise RuntimeError("alert store still down")

    w._alerts.resolve_incident = _count  # type: ignore[method-assign]
    w.poll()
    assert attempts["n"] > 0, "the pending key must be retried on the next poll"


def test_alert_failure_does_not_lose_the_poll_or_the_heartbeat():
    """A failure while resolving/opening a leg must not stop the poll: the poll
    returns its status and the heartbeat still lands (the remaining legs are
    re-evaluated on the next poll — the lifecycle is dedup-backed and idempotent;
    that trade is what guarantees the heartbeat)."""
    ch = _Channels()
    storage = MemoryStorage()
    _seed_archive(storage, "team_a", 200)
    _seed_state(storage, "team_a")
    w = _watcher(storage, ch)

    def _boom(kind, team_id="", detail=None):
        raise RuntimeError("alert store down")

    w._alerts.open_incident = _boom  # type: ignore[method-assign]
    w.poll()
    # No incident could be filed (the store is down) — but the poll completed and
    # the heartbeat is fresh, so the failure is visible as itself, not as a
    # fabricated WATCHER_DOWN.
    assert ch.issues == {}
    assert json.loads(storage.download(HEARTBEAT_KEY))["r2_ok"] is True


# ── cycle-3 review: universe shrink across surfaces ─────────────────────────


def test_universe_shrink_closes_the_absent_team_kinds_without_churn():
    """A team whose seam entry and R2 prefix disappear closes its STALE incident on
    the shrink poll — and a lingering `ops/teams/{team}/state.json` keeps
    BACKUP_SET_MISSING open WITHOUT the resolve/re-open churn that would flip the
    Telegram ✅/🚨 every poll (cycle-3 review P2).

    The lingering state file IS the condition that kind reports (state without
    archives), so staying open is correct; what matters is that repeated polls do
    not re-file/re-announce it.
    """
    ch = _Channels()
    storage = MemoryStorage()
    _seed_archive(storage, "team_a", 200)          # stale → STALE
    _seed_state(storage, "team_a")                 # → keeps BACKUP_SET_MISSING
    w = _watcher(storage, ch)

    w.poll()   # team present, stale
    assert any("STALE" in t for t in ch.issues.values()), ch.issues

    # The universe disappears from the seam AND from R2, but the state file stays.
    w._teams = lambda: []
    for k in list(storage.list("backups/team_a/")):
        storage.delete(k)

    w.poll()
    titles = sorted(ch.issues.values())
    assert not any("STALE" in t for t in titles), titles
    assert titles == ["[DR] BACKUP_SET_MISSING — team_a"], titles

    # Second shrink poll: the same single issue, no resolve-then-reopen churn.
    w.poll()
    assert sorted(ch.issues.values()) == titles, ch.issues
    assert len(ch.issues) == 1, ch.issues


def test_no_teams_leg_uses_the_previous_surface_snapshot():
    """The `no_teams` leg resolves the PREVIOUS team surface — the caller snapshots
    it before overwriting `_last_status`; without the snapshot the leg reads the
    current (empty) surface and is a no-op."""
    ch = _Channels()
    storage = MemoryStorage()
    _seed_archive(storage, "team_a", 200)
    _seed_state(storage, "team_a")
    w = _watcher(storage, ch)
    w.poll()
    assert any("STALE" in t for t in ch.issues.values()), ch.issues

    w._teams = lambda: []
    for k in [k for k in storage.list("") if k.startswith("backups/team_a/")]:
        storage.delete(k)
    storage.delete("ops/teams/team_a/state.json")   # fully gone from every surface
    w.poll()
    assert not ch.issues, f"the previous surface's incidents must close: {ch.issues}"


def test_no_teams_shrink_does_not_resolve_then_reopen_a_backup_set_missing_team():
    """The exclusion's purpose, pinned directly and non-vacuously: a team the shrink
    leg would resolve (it is in the PREVIOUS surface) whose BACKUP_SET_MISSING is
    still open (an earlier close failed) must not be resolved and immediately
    re-opened in the SAME poll — that is a ✅-then-🚨 Telegram flip with a fresh
    issue on every poll.

    Mutation-checked: re-adding BACKUP_SET_MISSING to the shrink tuple makes this
    test fail (the ✅ push appears); without it, the open leg is a dedup no-op.
    """
    ch = _Channels()
    storage = MemoryStorage()
    _seed_state(storage, "team_a")              # state, never any archives
    w = _watcher(storage, ch, teams=())
    # The incident is already open in the WATCHER's own store — e.g. its close
    # failed on an earlier poll. (Seeding through `_store(ch)` would write to a
    # different MemoryStorage and make this test vacuous — verified by mutation.)
    assert w._alerts.open_incident("BACKUP_SET_MISSING", "team_a") is True
    number = max(ch.issues)
    w._last_status = {"per_team": {"team_a": "stale"}}   # and it was on the surface

    for _ in range(3):                          # repeated polls = the flip surface
        w.poll()

    assert not [t for t in ch.telegram if "resolved" in t], ch.telegram
    assert max(ch.issues) == number, "the incident must not be re-filed (flip)"
    assert list(ch.issues.values()) == ["[DR] BACKUP_SET_MISSING — team_a"], ch.issues
