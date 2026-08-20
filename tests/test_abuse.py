"""Unit tests for the abuse-prevention engine (#308).

Covers (plan Task 2/4/9 + scoping deltas 8/9/11/13/14):
- two-stage staging machine (single burst flags, never suspends; suspension
  requires the breach to persist across a full window boundary)
- threshold boundaries (R1 500/501, R2 10/11) + env overrides + kill-switch
- R2 piggyback on point_create (trigger-recorded key events evaluate on the
  team's next hooked request)
- read-velocity tracker (per-key + team fan-out, window expiry, notify dedup)
- geo resolver + new-country detection (fail-open, seen-cache)
- suspended-signal set semantics (invalidation signal, not an authority)
- FakeControlPlane migration-0015 trigger emulation (bootstrap exclusion,
  ON CONFLICT DO NOTHING no-duplicate)
- notify_abuse (recipient precedence, missing/NULL email fallback, no-raise)
- CLI SUSPENDED detail parse; Turnstile siteverify fail-open/fail-closed
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import abuse  # noqa: I001
from tortoise.abuse import (AbuseEngine, MemoryAbuseStore, ReadVelocityTracker,
                            SignupVelocityTracker, check_new_country,
                            clear_suspended, is_suspended_signal,
                            mark_suspended, resolve_country, reset_geo_cache)
from tests.fake_control_plane import FakeControlPlane


T0 = datetime(2026, 8, 11, 12, 0, 0, tzinfo=timezone.utc)  # noqa: UP017


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """Fresh signal set + geo cache + engine per test; no kill-switch."""
    monkeypatch.delenv("TORTOISE_ABUSE_DISABLED", raising=False)
    with abuse._SIGNAL_LOCK:
        abuse._SUSPENDED_SIGNAL.clear()
    reset_geo_cache()
    abuse.set_engine(None)
    yield


@pytest.fixture
def notified(monkeypatch):
    calls: list[tuple[str, dict, dict]] = []

    def fake_notify(kind, team, details=None):
        calls.append((kind, team, details or {}))

    monkeypatch.setattr("tortoise.notify.notify_abuse", fake_notify)
    return calls


# ── Staging machine (delta 13) ──────────────────────────────────────────────

class TestStaging:
    def engine(self, store, notified):
        return AbuseEngine(store)

    def test_at_threshold_no_flag(self, notified):
        store = MemoryAbuseStore()
        eng = AbuseEngine(store)
        # exactly 500 (threshold) must NOT flag — breach is strictly >
        assert eng.record_point_create("t1", 500, now=T0) is None
        assert store.team_flagged_at("t1") is None
        assert notified == []

    def test_above_threshold_flags(self, notified):
        store = MemoryAbuseStore()
        eng = AbuseEngine(store)
        assert eng.record_point_create("t1", 501, now=T0) == "flag"
        assert store.team_flagged_at("t1") is not None
        assert [c[0] for c in notified] == ["abuse_flag"]
        assert notified[0][2]["rule"] == "point_create"

    def test_single_burst_never_suspends(self, notified):
        """The load-bearing false-positive guarantee: a breach contained in
        one window flags and stays flagged — no matter how many evaluations
        run inside the window."""
        store = MemoryAbuseStore()
        eng = AbuseEngine(store)
        assert eng.record_point_create("t1", 501, now=T0) == "flag"
        for i in range(5):
            r = eng.record_point_create("t1", 100, now=T0 + timedelta(minutes=10 * (i + 1)))
            assert r == "breach"
        assert store.team_suspended("t1") is False
        assert not is_suspended_signal("t1")

    def test_boundary_crossing_suspends(self, notified):
        """Continuity evidence: events exist on BOTH sides of the window
        boundary — the breach genuinely persisted across it."""
        store = MemoryAbuseStore()
        eng = AbuseEngine(store)
        eng.record_point_create("t1", 501, now=T0)                      # flag
        # sustained breach: every evaluation stays over threshold (a dip
        # under threshold would clean-evaluate and end the episode)
        assert eng.record_point_create(
            "t1", 501, now=T0 + timedelta(minutes=30)) == "breach"
        # at +90m the flag is a full window old; continuity band
        # (T0, T0+1800] holds the +30m events AND the current window
        # (T0+1800, T0+5400] still breaches via the fresh 501.
        r = eng.record_point_create("t1", 501, now=T0 + timedelta(minutes=90))
        assert r == "suspend"
        assert store.team_suspended("t1") is True
        assert is_suspended_signal("t1") is True
        assert "abuse_suspended" in [c[0] for c in notified]
        assert notified[-1][2]["appeal_url"]

    def test_quiet_after_flag_never_suspends(self, notified):
        store = MemoryAbuseStore()
        eng = AbuseEngine(store)
        eng.record_point_create("t1", 501, now=T0)
        # no further evaluations (team went quiet) — even far past the window
        assert store.team_suspended("t1") is False

    def test_new_burst_after_quiet_is_new_episode(self, notified):
        """Code-review P1 fix: a stale flag must not auto-suspend a fresh
        single-window burst. Flag → quiet → burst 2 windows later: the first
        evaluation of the new burst RE-FLAGS (new episode), never suspends;
        and a continued in-window breach of the new episode stays 'breach'."""
        store = MemoryAbuseStore()
        eng = AbuseEngine(store)
        assert eng.record_point_create("t1", 501, now=T0) == "flag"
        # quiet for 2 full windows, then a new single-window burst
        burst = T0 + timedelta(seconds=7200)
        assert eng.record_point_create("t1", 501, now=burst) == "flag"
        assert store.team_suspended("t1") is False
        assert eng.record_point_create("t1", 100,
                                       now=burst + timedelta(minutes=10)) == "breach"
        assert store.team_suspended("t1") is False
        # the re-flagged episode stages normally from its own anchor
        # (sustained breach throughout — no clean evaluation)
        assert eng.record_point_create(
            "t1", 501, now=burst + timedelta(minutes=30)) == "breach"
        r = eng.record_point_create("t1", 501, now=burst + timedelta(minutes=90))
        assert r == "suspend"

    def test_cross_rule_staging_independent(self, notified):
        """Code-review P1 fix: an old R1 flag must not escalate a FIRST R2
        breach — flags are per rule."""
        store = MemoryAbuseStore()
        eng = AbuseEngine(store)
        eng.record_point_create("t1", 501, now=T0)  # R1 flag
        # first-ever R2 breach 25h later (> R1 flag age, but R2 has no flag)
        later = T0 + timedelta(hours=25)
        for i in range(11):
            store.record_event("t1", "key_create", key_id=f"k{i}",
                               created_at=later)
        r = eng.evaluate_key_creates("t1", now=later)
        assert r == "flag"  # stage 1 for R2, NOT suspension
        assert store.team_suspended("t1") is False

    def test_weighted_rows_sum(self, notified):
        """One weighted row (bulk ingest) counts by weight, not rows."""
        store = MemoryAbuseStore()
        eng = AbuseEngine(store)
        eng.record_point_create("t1", 250, now=T0)
        assert store.window_sum("t1", "point_create", 3600, now=T0) == 250
        eng.record_point_create("t1", 251, now=T0)
        assert store.team_flagged_at("t1") is not None  # 501 via 2 rows


class TestKeyRule:
    def _seed_keys(self, store, team, n, when):
        for i in range(n):
            store.record_event(team, "key_create", key_id=f"k{i}",
                               created_at=when)

    def test_r2_threshold_boundary(self, notified):
        store = MemoryAbuseStore()
        eng = AbuseEngine(store)
        self._seed_keys(store, "t1", 10, T0)
        assert eng.evaluate_key_creates("t1", now=T0) is None   # 10 == threshold
        self._seed_keys(store, "t1", 1, T0)
        assert eng.evaluate_key_creates("t1", now=T0) == "flag"  # 11 > 10

    def test_r2_suspends_across_boundary(self, notified):
        store = MemoryAbuseStore()
        eng = AbuseEngine(store)
        self._seed_keys(store, "t1", 11, T0)
        assert eng.evaluate_key_creates("t1", now=T0) == "flag"
        # continuity evidence inside (flag, flag+window], then a breach that
        # persists past flagged_at + 24h
        self._seed_keys(store, "t1", 1, T0 + timedelta(hours=1))
        self._seed_keys(store, "t1", 11, T0 + timedelta(hours=25))
        assert eng.evaluate_key_creates("t1", now=T0 + timedelta(hours=25)) == "suspend"

    def test_r2_isolated_bursts_re_flag_not_suspend(self, notified):
        """Two separated key-mint bursts are two episodes — the second
        re-flags, it does not inherit the stale first flag."""
        store = MemoryAbuseStore()
        eng = AbuseEngine(store)
        self._seed_keys(store, "t1", 11, T0)
        assert eng.evaluate_key_creates("t1", now=T0) == "flag"
        self._seed_keys(store, "t1", 11, T0 + timedelta(hours=25))
        assert eng.evaluate_key_creates("t1", now=T0 + timedelta(hours=25)) == "flag"
        assert store.team_suspended("t1") is False

    def test_piggyback_on_point_create(self, notified):
        """Trigger-recorded key events (signup RPC path) evaluate on the
        team's next hooked request — delta-13 fix for the dead seam."""
        store = MemoryAbuseStore()
        eng = AbuseEngine(store)
        self._seed_keys(store, "t1", 11, T0)
        # a plain point create triggers the R2 evaluation
        result = eng.record_point_create("t1", 1, now=T0)
        assert result == "flag"
        flag_rows = [r for r in store.rows if r["event_type"] == "flag"]
        assert flag_rows[0]["details"]["rule"] == "key_create"


class TestEpisodeLifecycle:
    def test_recovery_clears_episodes_no_stale_suspend(self, notified):
        """Confirmation-review P1 regression test: flag → suspend →
        un-suspend → fresh single-window burst must RE-FLAG, never suspend
        (the un-suspend ends every flag episode)."""
        store = MemoryAbuseStore()
        eng = AbuseEngine(store)
        eng.record_point_create("t1", 501, now=T0)
        eng.record_point_create("t1", 501, now=T0 + timedelta(minutes=30))
        assert eng.record_point_create(
            "t1", 501, now=T0 + timedelta(minutes=90)) == "suspend"
        store.unsuspend_team("t1")
        assert store.latest_flag_at("t1", "point_create") is None
        # fresh burst long after recovery: stage 1 again, never stage 2
        burst = T0 + timedelta(days=30)
        assert eng.record_point_create("t1", 501, now=burst) == "flag"
        assert eng.record_point_create(
            "t1", 100, now=burst + timedelta(minutes=10)) == "breach"
        assert store.team_suspended("t1") is False

    def test_clean_evaluation_ends_episode(self, notified):
        """Confirmation-review P2: a window back under threshold ends the
        episode — ongoing sub-threshold activity can never feed continuity
        for a stale-flag suspension."""
        store = MemoryAbuseStore()
        eng = AbuseEngine(store)
        assert eng.record_point_create("t1", 501, now=T0) == "flag"
        # in-window breach stays an open episode
        assert eng.record_point_create(
            "t1", 10, now=T0 + timedelta(minutes=30)) == "breach"
        assert store.latest_flag_at("t1", "point_create") is not None
        # window goes clean → episode ends
        assert eng.record_point_create(
            "t1", 1, now=T0 + timedelta(hours=3)) is None
        assert store.latest_flag_at("t1", "point_create") is None
        # much later burst: re-flag, never a stale-flag suspend
        burst = T0 + timedelta(days=10)
        assert eng.record_point_create("t1", 501, now=burst) == "flag"
        assert store.team_suspended("t1") is False

    def test_unsuspend_clears_all_rules(self, notified):
        store = MemoryAbuseStore()
        eng = AbuseEngine(store)
        eng.record_point_create("t1", 501, now=T0)           # R1 flag
        later = T0 + timedelta(hours=2)  # (fictional time kept in the past)
        for i in range(11):
            store.record_event("t1", "key_create", key_id=f"k{i}",
                               created_at=later)
        eng.evaluate_key_creates("t1", now=later)              # R2 flag
        assert store.latest_flag_at("t1", "point_create") is not None
        assert store.latest_flag_at("t1", "key_create") is not None
        store.unsuspend_team("t1")
        assert store.latest_flag_at("t1", "point_create") is None
        assert store.latest_flag_at("t1", "key_create") is None

    def test_registry_write_field_scoped(self):
        """Selfhost durability: the registry callback gets FIELD-SCOPED
        writes — a flag write can never clobber suspended_at (review P2)."""
        writes: list[tuple[str, str, object]] = []
        store = MemoryAbuseStore(
            registry_write=lambda tid, field, value:
            writes.append((tid, field, value)))
        eng = AbuseEngine(store)
        eng.record_point_create("t1", 501, now=T0)
        assert ("t1", "flagged_at", (T0.isoformat())) in writes
        eng.record_point_create("t1", 501, now=T0 + timedelta(minutes=30))
        eng.record_point_create("t1", 501, now=T0 + timedelta(minutes=90))
        assert any(w[1] == "suspended_at" and w[2] is not None for w in writes)
        store.unsuspend_team("t1")
        assert ("t1", "suspended_at", None) in writes
        assert ("t1", "flagged_at", None) in writes
        # every write names exactly one field (no combined prop clobber)
        assert all(w[1] in ("suspended_at", "flagged_at") for w in writes)

    def test_geo_notify_flood_cap(self, monkeypatch, notified):
        """CF-IPCountry is spoofable — geo notifications are capped at
        10/team/24h (security review)."""
        store = MemoryAbuseStore()
        for i in range(25):
            check_new_country("t1", f"C{i:02d}", store)
        geo = [c for c in notified if c[0] == "abuse_new_ip"]
        assert len(geo) == 10  # capped
        ip_events = [r for r in store.rows if r["event_type"] == "auth_ip"]
        assert len(ip_events) == 10  # cap-before-record


class TestSwitches:
    def test_kill_switch(self, monkeypatch, notified):
        monkeypatch.setenv("TORTOISE_ABUSE_DISABLED", "1")
        store = MemoryAbuseStore()
        eng = AbuseEngine(store)
        assert eng.record_point_create("t1", 5000, now=T0) is None
        assert eng.evaluate_key_creates("t1", now=T0) is None
        assert store.rows == []

    def test_env_threshold_override(self, monkeypatch, notified):
        monkeypatch.setenv("TORTOISE_ABUSE_POINT_THRESHOLD", "5")
        store = MemoryAbuseStore()
        eng = AbuseEngine(store)
        assert eng.record_point_create("t1", 5, now=T0) is None
        assert eng.record_point_create("t1", 1, now=T0) == "flag"

    def test_empty_team_id_noop(self, notified):
        store = MemoryAbuseStore()
        eng = AbuseEngine(store)
        assert eng.record_point_create("", 9999, now=T0) is None
        assert store.rows == []


# ── R3: read velocity ───────────────────────────────────────────────────────

class TestReadVelocity:
    def test_per_key_breach_notifies_once(self, monkeypatch, notified):
        tr = ReadVelocityTracker()
        now = time.time()
        for i in range(100):
            assert tr.record_read("key1", "t1", now=now + i * 0.01) is None
        breach = tr.record_read("key1", "t1", now=now + 2)
        assert breach == ("key", "key1")
        assert [c[0] for c in notified] == ["abuse_read_velocity"]
        # key-scope breach still notifies the TEAM owner (team_id carried)
        assert notified[0][1]["team_id"] == "t1"
        # dedup: same window → no repeat notification, no repeat breach return
        assert tr.record_read("key1", "t1", now=now + 3) is None
        assert len(notified) == 1

    def test_team_fanout_breach(self, monkeypatch, notified):
        """Two keys under the per-key limit each, >100 together → team-level
        notification (multi-key fan-out detection)."""
        tr = ReadVelocityTracker()
        now = time.time()
        for i in range(60):
            assert tr.record_read("kA", "t1", now=now + i * 0.01) is None
        for i in range(40):  # 60 + 40 = 100 → still under
            assert tr.record_read("kB", "t1", now=now + i * 0.01) is None
        breach = tr.record_read("kB", "t1", now=now + 2)  # 101st team read
        assert breach == ("team", "t1")
        assert notified and notified[0][2]["scope"] == "team"

    def test_window_expiry(self, monkeypatch, notified):
        tr = ReadVelocityTracker()
        now = time.time()
        for i in range(100):  # noqa: B007
            tr.record_read("key1", "t1", now=now)
        # all 100 age out after the 300s window
        assert tr.record_read("key1", "t1", now=now + 301) is None

    def test_kill_switch(self, monkeypatch, notified):
        monkeypatch.setenv("TORTOISE_ABUSE_DISABLED", "1")
        tr = ReadVelocityTracker()
        now = time.time()
        for i in range(200):  # noqa: B007
            assert tr.record_read("key1", "t1", now=now) is None


# ── R8: signup velocity (in-memory, notify-only) ────────────────────────────

class TestSignupVelocity:
    def test_success_feed_breach_notifies_once(self, monkeypatch, notified):
        # P1-FIX-2: breach on >= — the success feed fires on the 2nd mint
        # ("IP consumed its entire allowance" = the designed review signal).
        tr = SignupVelocityTracker(threshold=2, window_s=3600)
        assert tr.record_signup("1.2.3.4", team_id="t1") is None
        breach = tr.record_signup("1.2.3.4", team_id="t2")  # 2nd mint = breach
        assert breach == ("ip", "1.2.3.4")
        assert [c[0] for c in notified] == ["abuse_signup_velocity"]
        assert len(notified) == 1
        # dedup: further mints in the same window do NOT re-notify
        tr.record_signup("1.2.3.4", team_id="t3")
        assert len(notified) == 1

    def test_window_expiry_rearms(self, monkeypatch, notified):
        tr = SignupVelocityTracker(threshold=2, window_s=60)
        for i in range(2):
            tr.record_signup("9.9.9.9", team_id=f"t{i}", now=1000.0 + i)
        assert len(notified) == 1
        # window expires → a fresh burst is a NEW episode (re-notify)
        for i in range(2):
            tr.record_signup("9.9.9.9", team_id=f"t{i}", now=2000.0 + i)
        assert len(notified) == 2

    def test_block_path_same_episode(self, monkeypatch, notified):
        # P1-FIX-2: success breach (2nd mint) + 429 block dedup to ONE email
        # per (ip, window) — same dedup key, never two.
        tr = SignupVelocityTracker(threshold=2, window_s=3600)
        tr.record_signup("1.2.3.4", team_id="t1")
        tr.record_signup("1.2.3.4", team_id="t2")   # success breach → notify
        tr.record_block("1.2.3.4")                    # 429 path → dedup'd
        tr.record_block("1.2.3.4")
        assert len(notified) == 1

    def test_kill_switch(self, monkeypatch, notified):
        monkeypatch.setenv("TORTOISE_ABUSE_DISABLED", "1")
        tr = SignupVelocityTracker(threshold=1, window_s=3600)
        assert tr.record_signup("1.2.3.4", team_id="t1") is None

    def test_memory_bound(self, monkeypatch, notified):
        # P1-B: the prune drops STALE entries (R3 precedent) — feed 10,100
        # distinct IPs where the first 200 have old `now` timestamps outside
        # the window; assert they are pruned and live entries survive.
        tr = SignupVelocityTracker(threshold=1000, window_s=3600)
        base = 1_000_000.0
        for i in range(10_100):
            now = base - 7200 if i < 200 else base  # first 200 stale (>window)
            tr.record_signup(f"10.{(i // 250) % 250}.{i % 250}", team_id=f"t{i}", now=now)
        # 10,100 > 10,000 → prune ran; 200 stale dropped, 9,900 live remain
        assert len(tr._by_ip) == 9_900


# ── R4: geo ─────────────────────────────────────────────────────────────────

class TestGeo:
    def test_resolve_country_header(self):
        assert resolve_country({"cf-ipcountry": "us"}) == "US"
        assert resolve_country({"other": "x"}) is None
        assert resolve_country(None) is None
        assert resolve_country({"cf-ipcountry": ""}) is None

    def test_new_country_notifies_once(self, monkeypatch, notified):
        store = MemoryAbuseStore()
        assert check_new_country("t1", "US", store) is True
        assert [c[0] for c in notified] == ["abuse_new_ip"]
        assert store.seen_countries("t1") == {"US"}
        assert check_new_country("t1", "US", store) is False
        assert len(notified) == 1
        assert check_new_country("t1", "DE", store) is True
        assert len(notified) == 2

    def test_geo_fail_open_without_country(self, notified):
        store = MemoryAbuseStore()
        assert check_new_country("t1", None, store) is False
        assert notified == []

    def test_seen_cache_ttl_refresh(self, monkeypatch, notified):
        """After TTL expiry the durable store is re-consulted (a restart or
        a new worker never re-notifies countries already on record)."""
        store = MemoryAbuseStore()
        store.record_event("t1", "auth_ip", country="US")
        reset_geo_cache()
        assert check_new_country("t1", "US", store) is False
        assert notified == []


# ── Signal set (delta 14) ───────────────────────────────────────────────────

class TestSignalSet:
    def test_signal_semantics(self):
        assert not is_suspended_signal("t1")
        mark_suspended("t1")
        assert is_suspended_signal("t1")
        clear_suspended("t1")
        assert not is_suspended_signal("t1")
        mark_suspended("")  # empty team id never marks
        assert not is_suspended_signal("")


# ── FakeControlPlane migration-0015 emulation (delta 9) ────────────────────

class TestFakeTrigger:
    def _provision(self, fake, team_id, lookup):
        fake.rpc("provision_team", {
            "p_user_id": None, "p_identity": f"id-{lookup[:6]}",
            "p_team_id": team_id, "p_team_name": team_id,
            "p_api_key": f"tt_{lookup}", "p_key_hash": "kh",
            "p_lookup_hash": lookup, "p_graph_name": f"team_{team_id}",
            "p_email": f"{lookup}@x.co", "p_key_prefix": team_id[:8],
        })

    def test_provision_records_key_create(self):
        fake = FakeControlPlane()
        self._provision(fake, "t1", "aaaa1111")
        events = [r for r in fake.tables["abuse_events"]
                  if r["event_type"] == "key_create"]
        assert len(events) == 1 and events[0]["team_id"] == "t1"

    def test_reprovision_no_duplicate_event(self):
        fake = FakeControlPlane()
        self._provision(fake, "t1", "aaaa1111")
        self._provision(fake, "t1", "aaaa1111")  # ON CONFLICT DO NOTHING
        events = [r for r in fake.tables["abuse_events"]
                  if r["event_type"] == "key_create"]
        assert len(events) == 1

    def test_bootstrap_mints_excluded(self):
        fake = FakeControlPlane()
        fake.query("api_keys", method="POST", json_body={
            "id": "k1", "team_id": "t1", "created_via": "bootstrap"})
        assert fake.tables.get("abuse_events", []) == []
        fake.query("api_keys", method="POST", json_body={
            "id": "k2", "team_id": "t1", "created_via": "recovery"})
        events = [r for r in fake.tables["abuse_events"]
                  if r["event_type"] == "key_create"]
        assert len(events) == 1

    def test_suspend_rpc_toggles_state(self):
        fake = FakeControlPlane().seed("teams", [{"id": "t1", "tier": "free"}])
        fake.rpc("abuse_suspend", {"p_team_id": "t1"})
        assert fake.tables["teams"][0]["suspended_at"] is not None
        fake.rpc("abuse_unsuspend", {"p_team_id": "t1"})
        assert fake.tables["teams"][0]["suspended_at"] is None
        assert fake.tables["teams"][0].get("flagged_at") is None

    def test_supabase_store_over_fake(self):
        """SupabaseAbuseStore window_sum/flag/suspend over the fake plane."""
        from tortoise.abuse import SupabaseAbuseStore
        fake = FakeControlPlane().seed("teams", [{"id": "t1", "tier": "free"}])
        store = SupabaseAbuseStore(fake)
        store.record_event("t1", "point_create", weight=300)
        store.record_event("t1", "point_create", weight=201)
        assert store.window_sum("t1", "point_create", 3600) == 501
        store.flag_team("t1", "point_create", {"count": 501})
        assert store.team_flagged_at("t1") is not None
        store.suspend_team("t1")
        assert store.team_suspended("t1")
        store.unsuspend_team("t1")
        assert not store.team_suspended("t1")
        alerts = store.recent_alerts("t1")
        assert alerts and alerts[0]["type"] in ("suspend", "flag")


# ── notify_abuse (Task 4) ───────────────────────────────────────────────────

class TestNotifyAbuse:
    def test_recipient_precedence_and_fallbacks(self, monkeypatch):
        import tortoise.notify as notify
        sent: list[tuple[str, str]] = []
        monkeypatch.setattr(notify, "_send_resend",
                            lambda key, to, subj, html: sent.append((key, to)))
        monkeypatch.setenv("RESEND_API_KEY", "re_test")
        monkeypatch.setenv("BILLING_NOTIFY_TO", "ops@premiselabs.co")

        notify.notify_abuse("abuse_flag", {"team_id": "t1", "email": "owner@x.co"}, {})
        assert sent[-1][1] == "owner@x.co"

        # NULL email → ops fallback
        notify.notify_abuse("abuse_flag", {"team_id": "t1", "email": None}, {})
        assert sent[-1][1] == "ops@premiselabs.co"

        # MISSING email key (registry dict shape) → ops fallback, no KeyError
        notify.notify_abuse("abuse_flag", {"team_id": "t1"}, {})
        assert sent[-1][1] == "ops@premiselabs.co"

    def test_never_raises_on_channel_failure(self, monkeypatch):
        import tortoise.notify as notify

        def boom(*a, **k):
            raise RuntimeError("resend down")

        monkeypatch.setattr(notify, "_send_resend", boom)
        monkeypatch.setenv("RESEND_API_KEY", "re_test")
        monkeypatch.setenv("BILLING_NOTIFY_TO", "ops@premiselabs.co")
        # must not raise
        notify.notify_abuse("abuse_suspended", {"team_id": "t1"}, {"rule": "point_create"})

    def test_unknown_kind_ignored(self, monkeypatch):
        import tortoise.notify as notify
        sent = []
        monkeypatch.setattr(notify, "_send_resend", lambda *a: sent.append(a))
        monkeypatch.setenv("RESEND_API_KEY", "re_test")
        monkeypatch.setenv("BILLING_NOTIFY_TO", "ops@x.co")
        notify.notify_abuse("not_a_kind", {"team_id": "t1"}, {})
        assert sent == []


# ── CLI SUSPENDED parse (Task 9) ────────────────────────────────────────────

class TestCliSuspendedParse:
    def test_parse_suspended_detail(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from tortoise.__main__ import _suspended_info
        body = json.dumps({"detail": {"code": "SUSPENDED",
                                      "message": "Team suspended.",
                                      "appeal_url": "https://x/appeal"}})
        info = _suspended_info(body)
        assert info is not None
        msg, url = info
        assert "Team suspended." in msg and url == "https://x/appeal"

    def test_parse_other_bodies(self):
        from tortoise.__main__ import _suspended_info
        assert _suspended_info(json.dumps({"detail": "Forbidden"})) is None
        assert _suspended_info(json.dumps({"detail": {"code": "OTHER"}})) is None
        assert _suspended_info("not json at all") is None
        assert _suspended_info("") is None


# ── Turnstile siteverify (Task 7) ───────────────────────────────────────────

class TestTurnstile:
    def test_fail_open_when_secret_unset(self, monkeypatch):
        import tortoise.hosted_api as ha
        monkeypatch.delenv("TURNSTILE_SECRET_KEY", raising=False)
        called = []
        monkeypatch.setattr("httpx.post", lambda *a, **k: called.append(1))
        assert asyncio.run(ha._verify_turnstile(None, None)) is True
        assert called == []  # no siteverify call without a secret

    def test_fail_closed_on_missing_token(self, monkeypatch):
        import tortoise.hosted_api as ha
        monkeypatch.setenv("TURNSTILE_SECRET_KEY", "sec")
        assert asyncio.run(ha._verify_turnstile(None, "1.2.3.4")) is False
        assert asyncio.run(ha._verify_turnstile("", "1.2.3.4")) is False

    def test_siteverify_success_and_failure(self, monkeypatch):
        import tortoise.hosted_api as ha
        monkeypatch.setenv("TURNSTILE_SECRET_KEY", "sec")

        class Resp:
            def __init__(self, payload):
                self._p = payload

            def raise_for_status(self):
                pass

            def json(self):
                return self._p

        monkeypatch.setattr("httpx.post",
                            lambda *a, **k: Resp({"success": True}))
        assert asyncio.run(ha._verify_turnstile("tok", None)) is True
        monkeypatch.setattr("httpx.post",
                            lambda *a, **k: Resp({"success": False}))
        assert asyncio.run(ha._verify_turnstile("tok", None)) is False

    def test_fail_closed_on_network_error(self, monkeypatch):
        import tortoise.hosted_api as ha
        monkeypatch.setenv("TURNSTILE_SECRET_KEY", "sec")

        def boom(*a, **k):
            raise OSError("unreachable")

        monkeypatch.setattr("httpx.post", boom)
        assert asyncio.run(ha._verify_turnstile("tok", None)) is False
