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

    def search_open(self, kind, org_id=""):
        return [
            n for n, t in self.issues.items()
            if f"[DR] {kind}" in t
            and (org_id == "" or t.endswith(f" — {org_id}"))
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
        now=FIXED, orgs=[], r2_orgs=[], state_orgs=[], newest_ts_by_org={},
        simulate_age=None, driver_heartbeat_ts=None, r2_ok=True, known_good=True,
        stale_threshold_min=90, driver_down_threshold_min=240,
        in_grace=False, kill_switch_off=False,
    )
    base.update(kw)
    return compute_status(**base)


def test_status_never_for_seam_team_without_archive():
    s = _status(orgs=["team_a"])
    assert s["per_team"]["team_a"] == "never"


def test_status_stale_and_ok():
    s = _status(orgs=["team_a", "team_b"], r2_orgs=["team_a", "team_b"],
                state_orgs=["team_a", "team_b"],
                newest_ts_by_org={"team_a": _ts(1), "team_b": _ts(100)})
    assert s["per_team"]["team_a"] == "ok"
    assert s["per_team"]["team_b"] == "stale"


def test_status_stamp_missing():
    s = _status(orgs=["team_a"], r2_orgs=["team_a"], state_orgs=[],
                newest_ts_by_org={"team_a": _ts(1)})
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
    s = _status(orgs=["team_a"], r2_orgs=["team_a"], state_orgs=["team_a"],
                newest_ts_by_org={"team_a": _ts(1)},
                simulate_age=_ts(200))
    assert s["per_team"]["team_a"] == "stale"


def test_status_backup_set_missing():
    s = _status(state_orgs=["team_x"], r2_orgs=[])
    assert s["backup_set_missing"] == ["team_x"]


# ── watcher poll ────────────────────────────────────────────────────────────


def _seed_archive(storage, team: str, hours_ago: float) -> None:
    """Seed with the REAL create_backup key shape: {YYYYMMDD}T{HHMMSSmmm}Z_{rnd}
    (the random suffix is what the freshness parser must strip — review P1-1)."""
    ts = _ts(hours_ago)
    key = f"{ts.strftime('%Y%m%dT%H%M%S')}{ts.microsecond // 1000:03d}Z_{secrets.token_hex(4)}"
    backup_id = f"{team}/{key}"
    manifest = {"backup_id": backup_id, "org_id": team, "graph_name": f"org_{team}",
                "created_at": ts.isoformat(), "node_count": 1, "edge_count": 0,
                "sha256": "0" * 64}
    storage.upload(f"backups/{backup_id}/manifest.json", json.dumps(manifest).encode())
    storage.upload(f"backups/{backup_id}/dump.enc", b"x")


def _seed_state(storage, team: str) -> None:
    storage.upload(
        f"ops/teams/{team}/state.json",
        json.dumps({"node_count": 1, "updated_at": FIXED.isoformat()}).encode(),
    )


def _watcher(storage, ch, *, grace_min=0, orgs=("team_a",), now_fn=None,
              graph_provider=None, eligible_provider=None) -> BackupWatcher:
    store = _store(ch)
    return BackupWatcher(
        storage, store,
        org_provider=lambda: list(orgs),
        state_reader=lambda t: {},
        graph_provider=graph_provider,
        eligible_provider=eligible_provider,
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
    w = _watcher(storage, ch, orgs=())
    status = w.poll()
    assert status["no_teams"] is True
    assert ch.issues == {}
    assert ch.telegram == []


def test_watcher_unknown_silence_on_r2_down_fresh():
    class _Boom(MemoryStorage):
        def list(self, prefix):
            raise ConnectionError("r2 down")

    ch = _Channels()
    w = _watcher(_Boom(), ch, orgs=("team_a",))
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
        org_provider=lambda: [], state_reader=lambda t: {},
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
    manifest = {"backup_id": backup_id, "org_id": team, "graph_id": gid,
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
    _seed_default_state_with_name(storage, "team_a", "org_team_a")
    # stale DEFAULT flat dump
    _seed_archive(storage, "team_a", 200)
    # FRESH pre-#2313 custom-era flat dump (custom namespace as graph_name)
    ts = _ts(0.2)
    key = f"{ts.strftime('%Y%m%dT%H%M%S')}{ts.microsecond // 1000:03d}Z_{secrets.token_hex(4)}"
    backup_id = f"team_a/{key}"
    m = {"backup_id": backup_id, "org_id": "team_a",
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


# ── #3658: eligibility gate on the per-org NEVER_BACKED_UP census ─────────

def test_status_not_eligible_is_not_never():
    """An org the sweep does not target is ``not_eligible`` — NOT ``never``.

    The sweep only archives eligible orgs (tier != 'free' AND
    backup_enabled); keying the watch census on every org made ``never``
    guaranteed by construction for the non-eligible tail, flooding
    NEVER_BACKED_UP and hiding a genuine eligible-and-never-backed-up org
    behind the identical signal.
    """
    s = _status(orgs=["team_pro", "team_free"], eligible_orgs={"team_pro"})
    assert s["per_team"]["team_free"] == "not_eligible"
    # The eligible org with no archive is still the REAL gap.
    assert s["per_team"]["team_pro"] == "never"


def test_status_no_eligibility_set_is_pre_3658_parity():
    """``eligible_orgs=None`` (no provider / legacy all-org sweep) keeps the
    pre-#3658 classification: every census org is a target, so an archive-less
    one is ``never`` (not ``not_eligible``)."""
    s = _status(orgs=["team_free"])
    assert s["per_team"]["team_free"] == "never"


def test_status_not_eligible_is_not_backup_set_missing():
    """No archive is OWED to a non-eligible org, so state-without-archives is
    not a missing backup set either."""
    s = _status(state_orgs=["team_free", "team_pro"], r2_orgs=[],
                eligible_orgs={"team_pro"})
    assert s["backup_set_missing"] == ["team_pro"]


def test_watcher_not_eligible_org_opens_no_incident():
    """A non-eligible org opens nothing.

    The census carries a healthy ELIGIBLE org beside the non-eligible one: an
    eligibility set must name at least one org we actually watch to count as
    evidence (the credibility rule — see `_eligible_set`), so a set naming
    only unknown orgs is UNCONFIRMED, not a way to build this fixture."""
    ch = _Channels()
    storage = MemoryStorage()
    _seed_archive(storage, "team_pro", 0.5)
    _seed_state(storage, "team_pro")
    w = _watcher(storage, ch, orgs=("team_free", "team_pro"),
                 eligible_provider=lambda: ["team_pro"])
    status = w.poll()
    assert status["per_team"]["team_free"] == "not_eligible"
    assert status["per_team"]["team_pro"] == "ok"
    assert ch.issues == {}
    assert ch.telegram == []


def test_watcher_eligible_never_still_fires():
    """The gate must not SILENCE a real gap: an eligible org with no archive
    still files NEVER_BACKED_UP."""
    ch = _Channels()
    w = _watcher(MemoryStorage(), ch, orgs=("team_pro",),
                 eligible_provider=lambda: ["team_pro"])
    status = w.poll()
    assert status["per_team"]["team_pro"] == "never"
    assert list(ch.issues.values()) == ["[DR] NEVER_BACKED_UP — team_pro"]


def test_watcher_org_downgraded_to_free_resolves_never_incident():
    """An org that STOPS being eligible (downgraded to free) resolves a
    NEVER_BACKED_UP opened while it was eligible — no stale incident left
    behind by the gate."""
    ch = _Channels()
    storage = MemoryStorage()
    # The anchor org is IN THE CENSUS and healthy: an eligibility set must name
    # at least one org we actually watch (the credibility rule), so a set that
    # is a subset of nothing real is UNCONFIRMED rather than a downgrade.
    _seed_archive(storage, "team_anchor", 0.5)
    _seed_state(storage, "team_anchor")
    # The set must stay NON-EMPTY on the downgrade: an EMPTY eligibility result
    # is UNCONFIRMED too (#3658 review) and turns the gate OFF — which would
    # read team_a as `never` again rather than as a downgrade.
    eligible = {"team_a", "team_anchor"}
    w = _watcher(storage, ch, orgs=("team_a", "team_anchor"),
                 eligible_provider=lambda: sorted(eligible))
    w.poll()
    assert list(ch.issues.values()) == ["[DR] NEVER_BACKED_UP — team_a"]
    eligible.discard("team_a")  # downgraded to free → no longer a sweep target
    status = w.poll()
    assert status["per_team"]["team_a"] == "not_eligible"
    assert ch.issues == {}


def test_watcher_eligibility_read_failure_is_gate_off():
    """A FIRST-CONTACT eligibility read failure fails OPEN toward alerting —
    with no confirmed set, it must never silence a genuine NEVER_BACKED_UP."""
    def _boom():
        raise RuntimeError("control plane down")

    ch = _Channels()
    w = _watcher(MemoryStorage(), ch, orgs=("team_a",),
                 eligible_provider=_boom)
    status = w.poll()
    assert status["per_team"]["team_a"] == "never"
    assert list(ch.issues.values()) == ["[DR] NEVER_BACKED_UP — team_a"]


def test_watcher_eligibility_blip_uses_last_known_good():
    """#3658 review: a TRANSIENT eligibility-read failure must not re-open the
    whole non-eligible census (nor resolve real incidents) — it evaluates from
    the last CONFIRMED set, like a degraded R2 poll."""
    ch = _Channels()
    state = {"fail": False}

    def _prov():
        if state["fail"]:
            raise RuntimeError("control-plane blip")
        return ["team_pro"]

    w = _watcher(MemoryStorage(), ch, orgs=("team_free", "team_pro"),
                 eligible_provider=_prov)
    status = w.poll()
    assert status["per_team"]["team_free"] == "not_eligible"
    assert status["per_team"]["team_pro"] == "never"
    assert list(ch.issues.values()) == ["[DR] NEVER_BACKED_UP — team_pro"]
    # The eligibility read blips: the last-known set still gates team_free.
    state["fail"] = True
    status2 = w.poll()
    assert status2["per_team"]["team_free"] == "not_eligible"
    assert list(ch.issues.values()) == ["[DR] NEVER_BACKED_UP — team_pro"]


def test_watcher_non_iterable_eligibility_does_not_kill_the_poll():
    """#3658 review: a provider returning a non-iterable degrades to gate-off
    WITHOUT killing the poll (no poll_error, heartbeat still written) — a bad
    gate value must not manufacture WATCHER_DOWN."""
    ch = _Channels()
    storage = MemoryStorage()
    w = _watcher(storage, ch, orgs=("team_a",), eligible_provider=lambda: 3)
    status = w.poll()
    assert "poll_error" not in status
    assert status["per_team"]["team_a"] == "never"
    # A first-contact failure is ALSO reported as degraded (#3658 review):
    # otherwise an operator cannot tell "no provider wired" from "the
    # eligibility read is failing", and the latter is the one needing action.
    assert status["eligible_degraded"] is True
    assert any("NEVER_BACKED_UP" in t for t in ch.issues.values())
    assert HEARTBEAT_KEY in storage.list("ops/")


def test_watcher_empty_eligibility_is_unconfirmed_not_a_silent_census():
    """#3658 review (P1). An EMPTY eligibility result is indistinguishable from
    a read that answered with nothing, and treating it as CONFIRMED puts every
    census org in ``not_eligible`` and resolves the whole DR surface — total
    alerting silence, the one outcome the gate promises never to produce. It
    must degrade like any other unconfirmed read."""
    ch = _Channels()
    w = _watcher(MemoryStorage(), ch, orgs=("team_a",), eligible_provider=lambda: [])
    status = w.poll()
    assert status["per_team"]["team_a"] == "never"  # gate OFF, not not_eligible
    assert list(ch.issues.values()) == ["[DR] NEVER_BACKED_UP — team_a"]
    assert status["eligible_degraded"] is True  # unconfirmed, and SAID so
    assert w._known_eligible is None  # no poisoned EMPTY cache


def test_watcher_empty_eligibility_does_not_poison_the_confirmed_set():
    """The empty result must not overwrite the last CONFIRMED set: otherwise a
    later read failure keeps gating on the empty set and keeps resolving — the
    "self-heals on the next complete poll" claim would not hold."""
    ch = _Channels()
    mode = {"v": "ok"}

    def _prov():
        if mode["v"] == "boom":
            raise RuntimeError("control-plane blip")
        return [] if mode["v"] == "empty" else ["team_pro"]

    w = _watcher(MemoryStorage(), ch, orgs=("team_free", "team_pro"),
                 eligible_provider=_prov)
    s1 = w.poll()
    assert s1["per_team"]["team_free"] == "not_eligible"
    assert s1["eligible_degraded"] is False

    mode["v"] = "empty"
    s2 = w.poll()
    assert s2["per_team"]["team_free"] == "not_eligible"  # last-known still gates
    assert s2["eligible_degraded"] is True
    assert w._known_eligible == {"team_pro"}  # NOT emptied

    mode["v"] = "boom"
    s3 = w.poll()
    assert s3["per_team"]["team_free"] == "not_eligible"  # still not poisoned
    assert s3["eligible_degraded"] is True


def test_watcher_degraded_poll_does_not_open_backup_set_missing():
    """#3658 review (P2). BACKUP_SET_MISSING asserts an ABSENCE ("state exists,
    no archive"), so it needs a confirmed archive read for the same reason the
    resolves do: on a degraded poll ``compute_status`` substitutes the CENSUS
    for the archive surface, so an org whose archives are intact but which is
    absent from the live census would be paged as a data-loss condition."""
    ch = _Channels()
    storage = MemoryStorage()
    _seed_archive(storage, "team_a", 0.5)
    _seed_state(storage, "team_a")
    w = _watcher(storage, ch, orgs=("team_a",))
    w.poll()
    assert ch.issues == {}  # healthy and fresh — nothing to page

    class _Boom(MemoryStorage):
        def list(self, prefix):
            raise ConnectionError("r2 down")

    w._storage = _Boom()
    w._orgs = lambda: []  # census empties on the SAME degraded poll
    status = w.poll()
    # The inference IS made (state present, archive surface unreadable)...
    assert status["backup_set_missing"] == ["team_a"]
    # ...but an ABSENCE we could not measure must not be paged.
    assert not any("BACKUP_SET_MISSING" in t for t in ch.issues.values())


def test_watcher_census_shrink_does_not_falsely_resolve_backup_set_missing():
    """#3658 review (cycle 3). BACKUP_SET_MISSING is state/archive-derived, not
    census-derived: a state-only org that leaves the census is STILL listed in
    `backup_set_missing`, so resolving it on the universe-shrink was immediately
    undone by the BSM open later in the SAME poll — a false "✅ DR resolved"
    push plus a fresh episode for a live condition."""
    ch = _Channels()
    storage = MemoryStorage()
    _seed_state(storage, "team_x")  # state present, no archive, not in census
    w = _watcher(storage, ch, orgs=("team_x",))
    w.poll()
    assert sorted(ch.issues.values()) == [
        "[DR] BACKUP_SET_MISSING — team_x", "[DR] NEVER_BACKED_UP — team_x"]

    w._orgs = lambda: []  # the census shrinks; state + no archive both remain
    w.poll()
    # A live data-loss condition must not be announced as resolved...
    assert not any("resolved" in t.lower() and "BACKUP_SET_MISSING" in t
                   for t in ch.telegram)
    # ...and must not be re-filed as a new episode either.
    assert list(ch.issues.values()) == ["[DR] BACKUP_SET_MISSING — team_x"]


def test_watcher_blank_eligibility_ids_are_unconfirmed_too():
    """#3658 review (cycle 3). A guard that rejects only a ZERO-LENGTH result is
    bypassed one element away: `[""]` (or a whitespace-only id) is non-empty,
    matches no census org, and would put every org in `not_eligible` — the same
    total silence, and reported as a confirmed healthy read."""
    ch = _Channels()
    w = _watcher(MemoryStorage(), ch, orgs=("team_a", "team_b"),
                 eligible_provider=lambda: [""])
    status = w.poll()
    assert status["per_team"] == {"team_a": "never", "team_b": "never"}
    assert sorted(ch.issues.values()) == [
        "[DR] NEVER_BACKED_UP — team_a", "[DR] NEVER_BACKED_UP — team_b"]
    assert status["eligible_degraded"] is True
    assert w._known_eligible is None

    # A whitespace-only id is the same shape (and passes the shipped
    # provider's `if r.get("id")` truthiness filter).
    ch2 = _Channels()
    w2 = _watcher(MemoryStorage(), ch2, orgs=("team_a",),
                  eligible_provider=lambda: ["   "])
    assert w2.poll()["per_team"]["team_a"] == "never"
    assert w2._known_eligible is None


def test_watcher_mapping_eligibility_is_rejected_as_unconfirmed(caplog):
    """#3658 review (cycles 4/6/9). A Mapping and its VIEWS are iterable — they
    yield KEYS — so accepting one reads `{"team_a": True}` as the eligible set
    `{"team_a"}`: an id set manufactured from a mapping's keys rather than a
    declaration of which orgs are eligible. (`dict.keys()` is a MappingView,
    NOT a Mapping, so a Mapping-only clause does not cover it.)

    Both must be rejected EXPLICITLY, not merely by the credibility rule: keyed
    by REAL census ids (as here) they would otherwise pass credibility. So this
    asserts the rejection is the explicit one, by its logged reason.
    """
    import logging as _logging

    for bad in ({"team_a": True}, {"team_a": True}.keys()):
        ch = _Channels()
        w = _watcher(MemoryStorage(), ch, orgs=("team_a",),
                     eligible_provider=lambda b=bad: b)
        with caplog.at_level(_logging.WARNING):
            status = w.poll()
        assert status["per_team"]["team_a"] == "never", bad  # OFF, not not_eligible
        assert status["eligible_degraded"] is True, bad
        assert w._known_eligible is None, bad
        assert "not a str id set" in caplog.text, bad
        assert "dict" in caplog.text, bad


def test_watcher_census_reference_is_what_the_classifier_sees():
    """#3658 review (cycle 9). The credential reference is derived from the
    MATERIALISED census the classifier uses, so the two cannot disagree. A
    stringified reference credentialed an eligibility read while every RAW id
    still read `not_eligible` — the whole census silenced while reported
    healthy. And re-iterating `raw_orgs` after materialising it left the
    reference EMPTY for a one-shot iterator census, permanently rejecting every
    later correct read."""
    ch = _Channels()
    w = _watcher(MemoryStorage(), ch, orgs=(123,),  # a NON-str census id
                 eligible_provider=lambda: ["123"])
    status = w.poll()
    assert status["per_team"][123] == "never"  # gate OFF — never silenced
    assert status["eligible_degraded"] is True
    assert w._known_eligible is None

    # A one-shot iterator census must still populate the reference (and so
    # ACCEPT a correct eligibility read).
    ch2 = _Channels()
    w2 = _watcher(MemoryStorage(), ch2, orgs=("team_a",))
    w2._orgs = lambda: (o for o in ["team_a"])
    w2._eligible = lambda: ["team_a"]
    w2.poll()
    assert w2._last_confirmed_census == {"team_a"}
    assert w2._known_eligible == {"team_a"}


def test_watcher_r2_only_id_cannot_make_eligibility_credible():
    """#3658 review (cycle 6). The credibility reference is the CENSUS, not
    `census ∪ r2_orgs`: a `backups/<id>/` prefix outlives a departed or foreign
    org until lifecycle purge, so an R2-only id must NOT be able to make an
    otherwise-foreign eligibility read look healthy — that would put the whole
    census in `not_eligible` and page nothing for a genuine never-backed-up
    org."""
    ch = _Channels()
    storage = MemoryStorage()
    _seed_archive(storage, "team_ghost", 0.5)  # an R2 prefix, NOT in the census
    _seed_state(storage, "team_ghost")
    w = _watcher(storage, ch, orgs=("team_a",),
                 eligible_provider=lambda: ["team_ghost"])
    status = w.poll()
    assert status["eligible_degraded"] is True
    assert status["per_team"]["team_a"] == "never"  # the REAL gap still pages
    assert any("NEVER_BACKED_UP — team_a" in t for t in ch.issues.values())
    assert w._known_eligible is None


def test_watcher_unconfirmed_census_does_not_credential_a_foreign_read():
    """#3658 review (cycle 8). The credibility reference must be the last
    CONFIRMED PURE CENSUS — not `per_team` (`census ∪ r2_orgs`). On an
    unconfirmed census the fallback `orgs` IS that union, so reusing it lets an
    R2-only id (a departed org whose prefix outlives it) credential a foreign
    eligibility read: the real census goes `not_eligible`, its incident is
    closed with a false "resolved" push, AND `_known_eligible` is poisoned —
    and that never clears by itself, so the silence is permanent."""
    ch = _Channels()
    storage = MemoryStorage()
    _seed_archive(storage, "team_ghost", 0.5)  # an R2 prefix, never in census
    _seed_state(storage, "team_ghost")
    w = _watcher(storage, ch, orgs=("team_a",),
                 eligible_provider=lambda: ["team_ghost"])
    w.poll()
    assert any("NEVER_BACKED_UP — team_a" in t for t in ch.issues.values())
    assert w._known_eligible is None

    w._orgs = lambda: None  # census read UNCONFIRMED this poll
    status = w.poll()
    assert status["eligible_degraded"] is True
    assert status["per_team"]["team_a"] == "never"  # NOT `not_eligible`
    assert w._known_eligible is None  # and no poisoning to carry forward
    assert any("NEVER_BACKED_UP — team_a" in t for t in ch.issues.values())

    w._orgs = lambda: ["team_a"]  # census back: the gap must still be a gap
    assert w.poll()["per_team"]["team_a"] == "never"


def test_watcher_org_departing_across_grace_still_closes_post_grace():
    """#3658 review (cycle 10 — restores HEAD parity). The shrink reference must
    be re-baselined OUTSIDE the grace/unknown gate. Inside it, a process that
    BOOTS into grace (grace restarts on every boot) never baselines it, so an
    org that leaves the census right at the grace boundary can never be shrunk
    out — its PERSISTED incidents strand open forever."""
    ch = _Channels()
    storage = MemoryStorage()
    shared = AlertStore(
        storage, file_issue=ch.file_issue, close_issue=ch.close_issue,
        search_open=ch.search_open, push_telegram=ch.push_telegram,
        repo="daniel-ospina/tortoise", assignee="u", now=lambda: FIXED,
    )

    def _proc(grace: int, orgs: list[str]) -> BackupWatcher:
        return BackupWatcher(
            storage, shared, org_provider=lambda: list(orgs),
            state_reader=lambda t: {}, driver_heartbeat_reader=lambda: {},
            stale_threshold_min=90, driver_down_threshold_min=240,
            grace_min=grace, now=lambda: FIXED,
        )

    _proc(0, ["team_a", "team_b"]).poll()  # before the restart: opens both
    assert sorted(ch.issues.values()) == [
        "[DR] NEVER_BACKED_UP — team_a", "[DR] NEVER_BACKED_UP — team_b"]

    w = _proc(120, ["team_a", "team_b"])  # a NEW process that BOOTS INTO GRACE
    w.poll()
    assert w._last_confirmed_per_team == {"team_a", "team_b"}, \
        "the shrink reference must be baselined while in grace"

    w._grace_min = 0  # grace expires...
    w._orgs = lambda: ["team_b"]  # ...and team_a leaves the census at the boundary
    w.poll()
    assert list(ch.issues.values()) == ["[DR] NEVER_BACKED_UP — team_b"], \
        "a departure at the grace boundary must still close"


def test_watcher_census_turnover_with_a_stale_cached_set_fails_open():
    """#3658 review (cycle 10). A CACHED eligibility set must also stay credible
    against the CURRENT census: after a census turnover it names none of the
    orgs we now watch, and returning it as the gate would put every one of them
    in `not_eligible` — the total-silence class the credibility check exists to
    close. It must fail OPEN (gate off ⇒ over-alerting) instead."""
    ch = _Channels()
    state = {"fail": False}

    def _prov():
        if state["fail"]:
            raise RuntimeError("control-plane blip")
        return ["team_a"]

    w = _watcher(MemoryStorage(), ch, orgs=("team_a",), eligible_provider=_prov)
    w.poll()
    assert w._known_eligible == {"team_a"}

    state["fail"] = True  # the read now fails...
    w._orgs = lambda: ["team_b"]  # ...and the census has turned over
    status = w.poll()
    assert status["per_team"]["team_b"] == "never", \
        "a cached set disjoint from the current census must not silence it"
    assert status["eligible_degraded"] is True


def test_watcher_padded_or_foreign_eligibility_ids_are_unconfirmed():
    """#3658 review (cycles 4-5). An id set that names NO org we actually watch
    is not usable evidence — whether the ids are padded, foreign, or the
    characters a string iterates into. Accepting any of them as CONFIRMED would
    put the whole census in `not_eligible` and resolve the entire DR surface:
    total alerting silence. Each must instead degrade to the gate being OFF
    (fail open toward ALERTING), so the real NEVER_BACKED_UP still fires."""
    for bad in ([" team_a "], ["team_other"], list("team_a"), set("team_a"),
                iter("team_a")):
        ch = _Channels()
        w = _watcher(MemoryStorage(), ch, orgs=("team_a",),
                     eligible_provider=lambda b=bad: b)
        status = w.poll()
        assert status["eligible_degraded"] is True, bad
        assert status["per_team"]["team_a"] == "never", bad
        assert w._known_eligible is None, bad
        assert list(ch.issues.values()) == ["[DR] NEVER_BACKED_UP — team_a"], bad


def test_watcher_bare_string_eligibility_does_not_poison_the_census():
    """#3658 review: a bare str is iterable — without a shape guard,
    ``set("team_a")`` becomes ``{'t','e','a','m','_'}``, every org reads
    ``not_eligible`` and the WHOLE census is silenced (the masking #3658
    removes). It must degrade like any other unconfirmed read."""
    ch = _Channels()
    w = _watcher(MemoryStorage(), ch, orgs=("team_a", "team_b"),
                 eligible_provider=lambda: "team_a")
    status = w.poll()
    assert status["per_team"] == {"team_a": "never", "team_b": "never"}
    assert sorted(ch.issues.values()) == [
        "[DR] NEVER_BACKED_UP — team_a", "[DR] NEVER_BACKED_UP — team_b"]
    assert w._known_eligible is None  # the cache was never poisoned


def test_watcher_eligibility_degraded_is_reported():
    """#3658 review: evaluating from the stale last-known set must be
    OBSERVABLE (status + heartbeat) so it is not mistaken for confirmed."""
    ch = _Channels()
    storage = MemoryStorage()
    state = {"fail": False}

    def _prov():
        if state["fail"]:
            raise RuntimeError("control-plane blip")
        return ["team_pro"]

    w = _watcher(storage, ch, orgs=("team_free", "team_pro"),
                 eligible_provider=_prov)
    s1 = w.poll()
    assert s1["eligible_degraded"] is False
    assert json.loads(storage.download(HEARTBEAT_KEY))["eligible_degraded"] is False
    state["fail"] = True
    s2 = w.poll()
    assert s2["eligible_degraded"] is True
    assert s2["per_team"]["team_free"] == "not_eligible"  # last-known still gates
    assert json.loads(storage.download(HEARTBEAT_KEY))["eligible_degraded"] is True


def test_watcher_cp_flicker_on_build_call_does_not_resolve():
    """#3658 review: a graph provider that answers the scan call and returns None on
    the build (confirmation) call must NOT let the graph's incident be resolved by
    universe-shrink off an unconfirmed surface (fabricated recovery).

    Merged (#3031 cycle-2 P1 + #3405 review P2): ``per_graph`` is built from the
    ONE cached scan enumeration, so the graph STAYS on the surface; the build
    loop's confirmation read gates the shrink DECISION, and its ``None`` clears
    ``graph_surface_confirmed``. The safety property is main's; the mechanism is
    the cache for the surface plus the confirmation read for the shrink.
    """
    ch = _Channels()
    storage = MemoryStorage()
    _seed_archive(storage, "team_a", 0.5)
    _seed_state(storage, "team_a")
    _seed_graph_archive(storage, "team_a", "g_x", 200)  # stale custom graph
    _seed_graph_state(storage, "team_a", "g_x")
    w = _watcher(storage, ch, graph_provider=lambda t: ["g_x"])
    w.poll()
    assert any("STALE — team_a:g_x" in t for t in ch.issues.values())
    calls = {"n": 0}

    def _flicker(t):
        calls["n"] += 1
        return ["g_x"] if calls["n"] == 1 else None  # scan ok, confirmation unconfirmed

    w._graphs_for = _flicker
    status = w.poll()
    assert calls["n"] == 2, (
        "one cached scan read supplies per_graph; a second confirmation read "
        "gates the shrink decision (#3405 review P2)"
    )
    # The graph stays on the surface (cached list) → the shrink cannot resolve it.
    assert "team_a:g_x" in status["per_graph"], status["per_graph"]
    assert any("STALE — team_a:g_x" in t for t in ch.issues.values())  # survives


def test_watcher_second_read_disagreement_blocks_shrink_without_dropping_keys():
    """#3405 review P2: a read that DISAGREES with the cached enumeration must
    clear ``graph_surface_confirmed`` for the shrink decision while ``per_graph``
    still comes from the CACHED list.

    The regression this pins: caching the scan read removed main's second read,
    so on a HEALTHY poll a single silently-TRUNCATED enumeration (the documented
    PostgREST ``db-max-rows`` fail-open class) dropped a live graph's key out of
    ``per_graph`` and the universe-shrink resolved its still-active incidents —
    a fabricated false recovery, re-opened when the full read returned (a
    ✅/🚨 flap). A complete re-read now blocks that resolve, and the CACHED list
    keeps the keys so an inconsistent read cannot drop them either. A legitimate
    vanish is only DEFERRED by one poll, never stranded.
    """
    ch = _Channels()
    storage = MemoryStorage()
    _seed_archive(storage, "team_a", 0.5)
    _seed_state(storage, "team_a")
    _seed_graph_archive(storage, "team_a", "g_x", 200)  # stale custom graph
    _seed_graph_state(storage, "team_a", "g_x")
    _seed_graph_archive(storage, "team_a", "g_y", 200)  # stale custom graph
    _seed_graph_state(storage, "team_a", "g_y")
    w = _watcher(storage, ch, graph_provider=lambda t: ["g_x", "g_y"])
    s1 = w.poll()
    assert set(s1["per_graph"]) == {"team_a:g_x", "team_a:g_y"}
    assert any("STALE — team_a:g_x" in t for t in ch.issues.values())
    assert any("STALE — team_a:g_y" in t for t in ch.issues.values())
    assert w._last_graph_keys == {"team_a:g_x", "team_a:g_y"}

    # Poll 2: the SCAN (cached) read is silently TRUNCATED to {g_x}; the
    # CONFIRMATION read is complete ({g_x, g_y}). g_y's archive is still there.
    calls = {"n": 0}

    def _disagree(t):
        calls["n"] += 1
        return ["g_x"] if calls["n"] % 2 == 1 else ["g_x", "g_y"]

    w._graphs_for = _disagree
    s2 = w.poll()
    # (a) no keys dropped: per_graph stays on the CACHED list (so the truncated
    # read's own key is present) and the inconsistent re-read injects no keys.
    assert "team_a:g_x" in s2["per_graph"], s2["per_graph"]
    assert "team_a:g_y" not in s2["per_graph"], s2["per_graph"]
    # (b) no resolve: the disagreement cleared graph_surface_confirmed, so the
    # universe-shrink did not run and the live graph's incident survives.
    assert any("STALE — team_a:g_y" in t for t in ch.issues.values()), (
        "a read DISAGREEMENT must not resolve a live graph's incident"
    )
    assert w._last_graph_keys == {"team_a:g_x", "team_a:g_y"}, (
        "an unconfirmed shrink must not re-baseline the reference off the "
        "truncated read (that would strand g_y un-resolvable forever)"
    )

    # Poll 3: both reads AGREE on the complete {g_x} and g_y is genuinely gone →
    # resolved. The disagreement only deferred it by one poll.
    w._graphs_for = lambda t: ["g_x"]
    w.poll()
    assert not any("team_a:g_y" in t for t in ch.issues.values())


def test_watcher_universe_shrink_to_empty_resolves_org_incidents():
    """#3658 review (pre-existing): the no-teams resolution read
    ``_last_status`` AFTER it was overwritten with this poll's (empty) census,
    so org incidents could never close on a universe shrink. It must use the
    PREVIOUS snapshot."""
    ch = _Channels()
    w = _watcher(MemoryStorage(), ch, orgs=("team_a",))
    w.poll()
    assert list(ch.issues.values()) == ["[DR] NEVER_BACKED_UP — team_a"]
    w._orgs = lambda: []  # census collapses to empty
    status = w.poll()
    assert status["no_teams"] is True
    assert ch.issues == {}


def test_watcher_partial_universe_shrink_resolves_departed_org():
    """#3658 review: a PARTIAL shrink (one org leaves, others remain — so
    ``no_teams`` is False) must also close the departed org's incidents, not
    just the all-empty case."""
    ch = _Channels()
    w = _watcher(MemoryStorage(), ch, orgs=("team_a", "team_b"))
    w.poll()
    assert sorted(ch.issues.values()) == [
        "[DR] NEVER_BACKED_UP — team_a", "[DR] NEVER_BACKED_UP — team_b"]
    w._orgs = lambda: ["team_b"]
    status = w.poll()
    assert status["no_teams"] is False
    assert list(ch.issues.values()) == ["[DR] NEVER_BACKED_UP — team_b"]


def test_watcher_degraded_poll_does_not_resolve_a_real_never_incident():
    """#3658 review (P1). A degraded R2 poll evaluates from the last-known-good
    cache and treats every census org as archived, so a NO-ARCHIVE org lands in
    the cache-miss ``stale``/``stamp_missing`` arm — a state whose handler
    resolves the OTHER kinds. Resolving off that inference is a FABRICATED
    RECOVERY: the org genuinely has no archive, so its NEVER_BACKED_UP must
    survive the degraded poll, and no false "resolved" push may fire."""
    ch = _Channels()
    storage = MemoryStorage()
    _seed_state(storage, "team_a")  # known org, still no archive
    w = _watcher(storage, ch)
    s1 = w.poll()
    assert s1["per_team"]["team_a"] == "never"
    assert any("NEVER_BACKED_UP — team_a" in t for t in ch.issues.values())

    class _Boom(MemoryStorage):
        def list(self, prefix):
            raise ConnectionError("r2 down")

    w._storage = _Boom()
    s2 = w.poll()
    # The degraded poll classifies from the CACHE — team_a is not `never` here,
    # it is the cache-miss arm. That is exactly why the resolve is unsafe (and
    # what makes this test discriminate rather than pass trivially).
    assert s2["per_team"]["team_a"] == "stale"
    assert any("NEVER_BACKED_UP — team_a" in t for t in ch.issues.values()), \
        "a degraded poll must not resolve a real NEVER_BACKED_UP"
    assert not any("resolved" in t.lower() for t in ch.telegram), \
        "no fabricated recovery push off an unconfirmed archive read"


def test_watcher_degraded_poll_does_not_shrink_resolve():
    """#3658 review (P2). On a degraded poll ``per_team`` is derived from the
    CACHE (``compute_status`` overwrites ``r2_orgs`` with the census), so it is
    not a faithful census surface. A census shrink observed on that same
    degraded poll must therefore NOT resolve the departed org's incidents."""
    ch = _Channels()
    storage = MemoryStorage()
    w = _watcher(storage, ch, orgs=("team_a", "team_b"))
    w.poll()
    assert sorted(ch.issues.values()) == [
        "[DR] NEVER_BACKED_UP — team_a", "[DR] NEVER_BACKED_UP — team_b"]

    class _Boom(MemoryStorage):
        def list(self, prefix):
            raise ConnectionError("r2 down")

    w._storage = _Boom()
    w._orgs = lambda: ["team_b"]  # census shrinks on the SAME degraded poll
    status = w.poll()
    assert status["no_teams"] is False
    assert any("NEVER_BACKED_UP — team_a" in t for t in ch.issues.values()), \
        "a degraded poll's per_team is cache-derived, not a census"


def test_watcher_org_departing_in_a_degraded_poll_still_closes_on_recovery():
    """#3658 review (cycle 7). The shrink reference must NOT be overwritten by a
    degraded poll: `per_team` on a degraded poll is the census, so persisting it
    forgets an org that left the census — and once the poll recovers, the shrink
    set no longer contains that org, stranding its incidents open FOREVER.
    Mirror of the per-graph surface, which re-baselines only when confirmed."""
    ch = _Channels()
    w = _watcher(MemoryStorage(), ch, orgs=("team_a", "team_b"))
    w.poll()
    assert sorted(ch.issues.values()) == [
        "[DR] NEVER_BACKED_UP — team_a", "[DR] NEVER_BACKED_UP — team_b"]

    class _Boom(MemoryStorage):
        def list(self, prefix):
            raise ConnectionError("r2 down")

    w._storage = _Boom()  # degraded...
    w._orgs = lambda: ["team_b"]  # ...and the census shrinks in the same poll
    w.poll()
    assert any("NEVER_BACKED_UP — team_a" in t for t in ch.issues.values())

    w._storage = MemoryStorage()  # R2 recovers; the census stays shrunk
    w.poll()
    assert list(ch.issues.values()) == ["[DR] NEVER_BACKED_UP — team_b"], \
        "the departed org's incidents must close on the next CONFIRMED poll"


def test_watcher_backup_set_missing_closes_on_restart_when_archives_return():
    """#3658 review (P2) — restoration of the pre-#3658 positive scan.

    In production the alert store shares the watcher's PERSISTENT storage
    (``hosted_api._alert_store_from`` → ``_backup_storage()``), so dedup state
    survives a process RESTART — but the watcher's in-memory
    ``_last_backup_set_missing`` does NOT. The cross-poll diff alone therefore
    cannot close a BACKUP_SET_MISSING whose archives came back before the new
    process's first poll: the incident stays open forever and its dedup state
    can absorb the next real recurrence. This models exactly that — one
    persistent store, two watcher processes."""
    ch = _Channels()
    storage = MemoryStorage()
    shared = AlertStore(
        storage, file_issue=ch.file_issue, close_issue=ch.close_issue,
        search_open=ch.search_open, push_telegram=ch.push_telegram,
        repo="daniel-ospina/tortoise", assignee="u", now=lambda: FIXED,
    )
    _seed_state(storage, "team_x")  # state present, no archives, not in census

    def _proc() -> BackupWatcher:
        return BackupWatcher(
            storage, shared, org_provider=lambda: [], state_reader=lambda t: {},
            driver_heartbeat_reader=lambda: {}, stale_threshold_min=90,
            driver_down_threshold_min=240, grace_min=0, now=lambda: FIXED,
        )

    _proc().poll()
    assert any("BACKUP_SET_MISSING — team_x" in t for t in ch.issues.values())

    _seed_archive(storage, "team_x", 0.5)  # archives are back
    status = _proc().poll()  # NEW process: empty cross-poll memory
    assert status["backup_set_missing"] == []
    assert ch.issues == {}


def test_watcher_universe_shrink_keeps_an_org_that_still_has_archives():
    """#3658 review (security) — the safety invariant the shrink relies on.
    ``per_team`` is ``census ∪ r2_orgs``, so an org can leave it only by
    leaving the census AND having no archive listing. An org that still holds
    archives must survive a census shrink; that is precisely the property which
    stops a silently SHORT census read (the documented PostgREST
    ``db-max-rows`` fail-open class) from resolving a real incident for an org
    whose data IS present."""
    ch = _Channels()
    storage = MemoryStorage()
    _seed_archive(storage, "team_a", 200)  # stale, but still on R2
    _seed_state(storage, "team_a")
    w = _watcher(storage, ch, orgs=("team_a", "team_b"))
    w.poll()
    assert any("STALE — team_a" in t for t in ch.issues.values())
    w._orgs = lambda: ["team_b"]  # team_a leaves the census
    status = w.poll()
    assert "team_a" in status["per_team"]  # still present via the R2 surface
    assert any("STALE — team_a" in t for t in ch.issues.values())


def test_watcher_backup_set_missing_closes_when_the_state_disappears():
    """#3658 review: a state-only org never appears in ``per_team``, so the old
    per_team resolve scan could never close its BACKUP_SET_MISSING. It must
    close when the subject leaves the set across polls."""
    ch = _Channels()
    storage = MemoryStorage()
    _seed_state(storage, "team_x")  # state present, no archives, not in census
    w = _watcher(storage, ch, orgs=())
    w.poll()
    assert any("BACKUP_SET_MISSING — team_x" in t for t in ch.issues.values())
    storage.delete("ops/teams/team_x/state.json")
    status = w.poll()
    assert status["backup_set_missing"] == []
    assert ch.issues == {}


def test_watcher_generator_eligibility_is_materialised_once():
    """#3658 review: validating a one-shot iterator consumes it, so ``set()``
    would cache an EMPTY confirmed set and silence the whole census. The value
    must be materialised exactly once."""
    ch = _Channels()

    def _gen():
        yield "team_pro"

    w = _watcher(MemoryStorage(), ch, orgs=("team_free", "team_pro"),
                 eligible_provider=_gen)
    status = w.poll()
    assert w._known_eligible == {"team_pro"}
    assert status["per_team"]["team_free"] == "not_eligible"
    assert status["per_team"]["team_pro"] == "never"
    assert list(ch.issues.values()) == ["[DR] NEVER_BACKED_UP — team_pro"]


def test_watcher_unconfirmed_census_does_not_resolve_departed_org():
    """#3658 review: a control-plane census failure must NOT resolve a real
    incident off a fabricated-empty census (the org surface needs the same
    guard as `graph_surface_confirmed`); it evaluates from last-known."""
    ch = _Channels()
    storage = MemoryStorage()
    _seed_archive(storage, "team_b", 0.5)
    _seed_state(storage, "team_b")
    w = _watcher(storage, ch, orgs=("team_a", "team_b"))
    w.poll()
    assert any("NEVER_BACKED_UP — team_a" in t for t in ch.issues.values())

    def _boom():
        raise RuntimeError("control-plane blip")

    w._orgs = _boom
    status = w.poll()
    assert status["per_team"]["team_a"] == "never"  # last-known census retained
    assert any("NEVER_BACKED_UP — team_a" in t for t in ch.issues.values())  # survives


def test_watcher_not_eligible_closes_preexisting_backup_set_missing():
    """#3658 review: an org downgraded out of the eligible set must not keep a
    pre-existing BACKUP_SET_MISSING.

    The DEGRADED poll is what makes this test discriminate (review cycle 4):
    with `r2_confirmed` False the positive scan AND the cross-poll diff are both
    skipped, so only the `not_eligible` branch can close the incident. On a
    healthy poll the reconciliation resolves it first, and removing that
    branch's resolve would leave this test green while the mechanism it names
    was gone."""
    ch = _Channels()
    storage = MemoryStorage()
    _seed_archive(storage, "team_other", 0.5)
    _seed_state(storage, "team_other")
    w = _watcher(storage, ch, orgs=("team_a", "team_other"),
                 eligible_provider=lambda: ["team_other"])
    w.poll()  # healthy poll: establishes the last-known-good surface
    assert ch.issues == {}

    w._alerts.open_incident("BACKUP_SET_MISSING", "team_a")
    assert any("BACKUP_SET_MISSING — team_a" in t for t in ch.issues.values())

    class _Boom(MemoryStorage):
        def list(self, prefix):
            raise ConnectionError("r2 down")

    w._storage = _Boom()  # degraded: every other close path is gated off
    status = w.poll()
    assert status["per_team"]["team_a"] == "not_eligible"
    # The BSM is gone. (`R2_DOWN` is expected to be open — the poll IS degraded;
    # asserting only on the kind under test keeps that from muddying the point.)
    assert not any("BACKUP_SET_MISSING" in t for t in ch.issues.values())


def test_watcher_stamp_missing_to_stale_closes_metadata_lost():
    """#3658 review (pre-existing): the `stale`/`never` branches resolved
    nothing, so a `stamp_missing`→`stale` transition left METADATA_LOST open
    beside the new STALE (two concurrent incidents for one org)."""
    ch = _Channels()
    storage = MemoryStorage()
    _seed_archive(storage, "team_a", 0.5)  # archive, NO state → stamp_missing
    clock = [FIXED]
    w = _watcher(storage, ch, now_fn=lambda: clock[0])
    w.poll()
    assert any("METADATA_LOST — team_a" in t for t in ch.issues.values())
    _seed_state(storage, "team_a")
    clock[0] = FIXED + timedelta(hours=5)  # archive now stale (>90 min)
    status = w.poll()
    assert status["per_team"]["team_a"] == "stale"
    assert any("STALE — team_a" in t for t in ch.issues.values())
    assert not any("METADATA_LOST" in t for t in ch.issues.values())


def test_watcher_not_eligible_org_graph_opens_no_incident():
    """#3658 covers the per-graph surface too: the sweep only archives
    eligible orgs' graphs, so a non-eligible org's custom graph is not an
    un-backed-up gap and must open nothing."""
    ch = _Channels()
    storage = MemoryStorage()
    # A healthy ELIGIBLE org in the census, so the eligibility set is credible
    # (it must name an org we watch); it has no custom graphs, so the assertion
    # below is about team_free only.
    _seed_archive(storage, "team_pro", 0.5)
    _seed_state(storage, "team_pro")
    w = _watcher(storage, ch, orgs=("team_free", "team_pro"),
                 graph_provider=lambda t: ["g_x"] if t == "team_free" else [],
                 eligible_provider=lambda: ["team_pro"])
    status = w.poll()
    assert "team_free:g_x" not in status["per_graph"]
    assert ch.issues == {}


def test_watcher_org_downgrade_resolves_graph_incidents():
    """An org that stops being eligible drops out of the per-graph surface,
    resolving the custom-graph incidents opened while it was eligible."""
    ch = _Channels()
    storage = MemoryStorage()
    # NON-EMPTY on the downgrade — an empty eligibility result is UNCONFIRMED
    # (#3658 review) and would turn the gate off instead of downgrading — and
    # the anchor must be a CENSUS org, or the set names nothing we watch and
    # is UNCONFIRMED by the credibility rule instead.
    _seed_archive(storage, "team_anchor", 0.5)
    _seed_state(storage, "team_anchor")
    eligible = {"team_a", "team_anchor"}
    w = _watcher(storage, ch, orgs=("team_a", "team_anchor"),
                 graph_provider=lambda t: ["g_x"] if t == "team_a" else [],
                 eligible_provider=lambda: sorted(eligible))
    w.poll()
    assert any("NEVER_BACKED_UP — team_a:g_x" in t for t in ch.issues.values())
    eligible.discard("team_a")
    status = w.poll()
    assert "team_a:g_x" not in status["per_graph"]
    assert ch.issues == {}


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
    m = {"backup_id": backup_id, "org_id": team, "graph_name": graph_name,
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
    _seed_default_state_with_name(storage, "team_a", "org_team_a")
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
    the shrink poll, and a lingering `ops/teams/{team}/state.json` keeps
    BACKUP_SET_MISSING open.

    NOTE: this test does NOT defend the shrink leg's BACKUP_SET_MISSING exclusion —
    the kind is not yet open when the leg runs, so re-adding it to the tuple still
    passes (verified by mutation). The exclusion is pinned by
    `test_no_teams_shrink_does_not_resolve_then_reopen_a_backup_set_missing_team`.
    """
    ch = _Channels()
    storage = MemoryStorage()
    _seed_archive(storage, "team_a", 200)          # stale → STALE
    _seed_state(storage, "team_a")                 # → keeps BACKUP_SET_MISSING
    w = _watcher(storage, ch)

    w.poll()   # team present, stale
    assert any("STALE" in t for t in ch.issues.values()), ch.issues

    # The universe disappears from the seam AND from R2, but the state file stays.
    w._orgs = lambda: []
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
    """The shrink leg resolves the PREVIOUS org surface — the caller snapshots
    `_last_confirmed_per_team` before this poll re-baselines it; without the
    snapshot the leg reads the current (empty) surface and is a no-op.
    """
    ch = _Channels()
    storage = MemoryStorage()
    _seed_archive(storage, "team_a", 200)
    _seed_state(storage, "team_a")
    w = _watcher(storage, ch)
    w.poll()
    assert any("STALE" in t for t in ch.issues.values()), ch.issues

    w._orgs = lambda: []
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
    w = _watcher(storage, ch, orgs=())
    # The incident is already open in the WATCHER's own store — e.g. its close
    # failed on an earlier poll. (Seeding through `_store(ch)` would write to a
    # different MemoryStorage and make this test vacuous — verified by mutation.)
    assert w._alerts.open_incident("BACKUP_SET_MISSING", "team_a") is True
    number = max(ch.issues)
    w._last_confirmed_per_team = {"team_a"}   # and it was on the surface

    for _ in range(3):                          # repeated polls = the flip surface
        w.poll()

    assert not [t for t in ch.telegram if "resolved" in t], ch.telegram
    assert max(ch.issues) == number, "the incident must not be re-filed (flip)"
    assert list(ch.issues.values()) == ["[DR] BACKUP_SET_MISSING — team_a"], ch.issues


def test_a_failed_team_resolve_is_carried_pending_and_retried():
    """Final-cycle review P1: `prev_per_team` is a ONE-SHOT snapshot, so a team
    whose close failed on the shrink poll would never be revisited — the same
    permanent-orphan defect the graph path fixed with its `pending` set. The
    failure must be carried in `_pending_team_resolves` and retried once the
    close cooldown allows it.
    """
    ch = _Channels()
    storage = MemoryStorage()
    clock = [FIXED]
    w = _watcher(storage, ch, orgs=(), now_fn=lambda: clock[0])
    assert w._alerts.open_incident("STALE", "team_a") is True
    assert w._alerts.open_incident("NEVER_BACKED_UP", "team_a") is True
    assert w._alerts.open_incident("METADATA_LOST", "team_a") is True
    w._last_confirmed_per_team = {"team_a"}

    attempts = {"n": 0}

    def _flaky(kind, team_id=""):
        attempts["n"] += 1
        raise RuntimeError("github 403")

    w._alerts.resolve_incident = _flaky  # type: ignore[method-assign]

    w.poll()
    first = attempts["n"]
    assert first >= 3, "the shrink poll must attempt the team's kinds"
    assert "team_a" in w._pending_team_resolves, "the failure must be carried"

    # Next poll (still cooling down / still failing): retried, not forgotten.
    clock[0] = clock[0] + timedelta(minutes=5)
    w.poll()
    assert attempts["n"] > first, "the pending team must be retried"
    assert "team_a" in w._pending_team_resolves

    # Recovery: the resolves succeed and the team leaves the pending set.
    w._alerts.resolve_incident = lambda kind, team_id="": True  # type: ignore[method-assign]
    w.poll()
    assert "team_a" not in w._pending_team_resolves
