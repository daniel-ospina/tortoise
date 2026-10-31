"""#10156 — the /overview alert feed renders two REGISTERS, ages and episodes.

THE DEFECT THIS FILE PINS. `/overview` rendered every ``/v1/team/alerts`` entry
under one heading:

    Security alerts
    Suspicious activity detected on this Organization. Revoke any key you
    don't recognize.
    * Unusual API activity — Unusual read velocity detected on an API key · 25/09/2026

Every sentence there was wrong about the events behind it, and each was wrong in
a way this file makes un-repeatable:

1. ``ReadVelocityTracker`` is NOTIFY-ONLY by design (R3 never suspends, throttles
   or locks anything), so nothing had happened to the account. ``suspend`` rows
   in the whole table: 0, ever.
2. The newest ``read_velocity`` row was **2026-09-26** — the panel was showing a
   month-old trace as current news.
3. The 13 rows were 13 five-minute windows of ONE continuous bout (dedup is per
   window), rendered as 13 separate alerts.
4. "Revoke any key you don't recognize" asks the customer to destroy a working
   integration over their own normal agent traffic, for an event the system
   never acted on.

WHAT IS PINNED HERE: the register split (an observation never renders as an
incident), episode grouping against the REAL dataset, the acknowledgement
persistence, the copy — including a test whose MUTATION is spelled out so it
cannot go vacuously green — and the calibration that stops the detector firing on
a key's own normal rate.

The rendered half lives in the dashboard package
(``website/apps/dashboard/src/securityAlertsPanel.dom.test.jsx``, vitest+jsdom;
the dashboard DOES have a test runner and a CI leg for it) and the endpoint tests
in ``tests/test_abuse_integration.py`` beside the other ``/v1/team/alerts``
tests.
"""
from __future__ import annotations

import itertools
import re
from datetime import datetime, timedelta, timezone

import pytest

from tests.fake_control_plane import FakeControlPlane
from tortoise import abuse
from tortoise.abuse import (
    ALERT_TYPES,
    EPISODE_GAP_S,
    EVENT_ALERT_ACK,
    EVENT_AUTH_IP,
    EVENT_FLAG,
    EVENT_READ_VELOCITY,
    EVENT_RECOVERY_VELOCITY,
    EVENT_REVIEW,
    EVENT_SIGNUP_VELOCITY,
    EVENT_SUSPEND,
    EVENT_UNSUSPEND,
    REGISTER_ACTIVITY,
    REGISTER_ALERT,
    MemoryAbuseStore,
    ReadVelocityTracker,
    SupabaseAbuseStore,
    group_alert_episodes,
)

ORG = "3326a01ea34ae595d84de5d8f9"
#: The ONE key every ``read_velocity`` row in the live project carries.
KEY = "989c68e6a35b479b923a28542e"

#: Every ``read_velocity`` timestamp in the live project (2026-09-16 → 09-26),
#: transcribed from ``abuse_events`` (project ybetwichurajbfswfeqa). Using the
#: real rows rather than a synthetic shape is the point: the grouping threshold
#: below is justified by THIS data's actual gaps.
REAL_READ_VELOCITY_TS = [
    "2026-09-16T20:02:26.354624+00:00",
    "2026-09-16T20:38:37.352901+00:00",
    "2026-09-16T21:51:31.286836+00:00",
    "2026-09-25T17:19:05.846463+00:00",
    "2026-09-25T17:24:06.570923+00:00",
    "2026-09-25T18:57:00.936378+00:00",
    "2026-09-25T19:35:02.862826+00:00",
    "2026-09-25T21:05:03.576667+00:00",
    "2026-09-25T21:10:04.017989+00:00",
    "2026-09-25T21:34:03.72905+00:00",
    "2026-09-25T21:39:04.098441+00:00",
    "2026-09-25T21:44:04.560705+00:00",
    "2026-09-25T23:53:55.051721+00:00",
    "2026-09-25T23:58:55.378486+00:00",
    "2026-09-26T00:03:55.437286+00:00",
    "2026-09-26T00:08:55.936232+00:00",
]


def _velocity_store(ts_list=None) -> MemoryAbuseStore:
    store = MemoryAbuseStore()
    for ts in ts_list or REAL_READ_VELOCITY_TS:
        store.record_event(
            ORG, EVENT_READ_VELOCITY,
            details={"scope": "key", "id": KEY},
            created_at=datetime.fromisoformat(ts),
        )
    return store


# ── 1. The register split ───────────────────────────────────────────────────

class TestRegisterSplit:
    """An observation must never render as an incident (#10156 step 1)."""

    def test_every_alert_type_declares_a_register(self):
        """EVERY member of ``ALERT_TYPES`` must be DECLARED in the map.

        The first version asserted ``row["register"] in (ALERT, ACTIVITY)`` — an
        assertion that can NEVER fail, because the map's default IS the quiet
        register (review finding P2). A new incident-grade type would therefore
        have rendered under "Recent activity — Nothing was changed, disabled or
        locked … these are measurements" with this test green: the mirror image of
        #10156, on the register that must be right.

        So the assertion is on the DECLARATION, not on the produced value: adding
        an ``ALERT_TYPES`` member without deciding its register reds here, which
        is the decision this map exists to force.
        """
        for etype in sorted(ALERT_TYPES):
            assert etype in abuse.ALERT_REGISTER_OF, (
                f"{etype} is in ALERT_TYPES but declares no register — an "
                "undecided type silently defaults to the QUIET register, so a "
                "new incident-grade event would be rendered as a measurement")
            assert abuse.ALERT_REGISTER_OF[etype] in (
                REGISTER_ALERT, REGISTER_ACTIVITY), etype
        assert not (set(abuse.ALERT_REGISTER_OF) - set(ALERT_TYPES)), (
            "the map declares registers for types that can never be alerted")

    def test_only_facts_get_incident_framing(self):
        """The register is decided by THE FACT, not by how alarming the words
        sound: incident = something HAPPENED (state changed / a human acted / a
        credential event), observation = a detector measured a rate."""
        expected = {
            EVENT_SUSPEND: REGISTER_ALERT,
            EVENT_FLAG: REGISTER_ALERT,
            EVENT_REVIEW: REGISTER_ALERT,
            EVENT_AUTH_IP: REGISTER_ALERT,
            EVENT_READ_VELOCITY: REGISTER_ACTIVITY,
            EVENT_SIGNUP_VELOCITY: REGISTER_ACTIVITY,
            EVENT_RECOVERY_VELOCITY: REGISTER_ACTIVITY,
        }
        for etype, register in expected.items():
            row = abuse._alert_dict({"event_type": etype, "details": {}})
            assert row["register"] == register, etype

    def test_read_velocity_never_renders_as_an_incident(self):
        """The defect, at the seam the dashboard reads."""
        alerts = _velocity_store().recent_alerts(ORG)
        assert alerts, "the fixture must produce rows or this test is vacuous"
        for alert in alerts:
            assert alert["type"] == EVENT_READ_VELOCITY
            assert alert["register"] == REGISTER_ACTIVITY, (
                "a notify-only read-velocity event rendered in the incident "
                "register — the #10156 defect")

    def test_an_unknown_type_defaults_to_the_quiet_register(self):
        """Fail-quiet: incident framing asserts something happened, and an
        unrecognised row cannot support that claim."""
        row = abuse._alert_dict({"event_type": "something_new", "details": {}})
        assert row["register"] == REGISTER_ACTIVITY

    def test_the_live_feed_puts_a_suspension_in_the_incident_register(self):
        store = MemoryAbuseStore()
        store.record_event(ORG, EVENT_SUSPEND, created_at=datetime.now(timezone.utc))  # noqa: UP017
        alerts = store.recent_alerts(ORG)
        assert [a["register"] for a in alerts] == [REGISTER_ALERT]


# ── 2. Episode grouping ─────────────────────────────────────────────────────

class TestEpisodeGrouping:
    """One sustained bout = one row with ×N and a range (#10156 step 3)."""

    def test_the_live_dataset_folds_into_two_bouts(self):
        """The REAL 16 rows → 2 rows: the 09-16 bout (20:02, 20:38, 21:51 —
        consecutive silences of 36m and 73m) and the 09-25/26 bout. This is the
        measurement the EPISODE_GAP_S choice rests on."""
        alerts = _velocity_store().recent_alerts(ORG)
        assert len(alerts) == 2, [(a["first_at"], a["count"]) for a in alerts]
        assert sorted(a["count"] for a in alerts) == [3, 13]

    def test_the_reported_bout_is_one_row_with_its_count_and_range(self):
        """The 13 rows the customer saw: ONE row, ×13, 17:19 → 00:08."""
        bout = _velocity_store().recent_alerts(ORG)[0]
        assert bout["count"] == 13
        assert bout["first_at"].startswith("2026-09-25T17:19:05")
        assert bout["at"].startswith("2026-09-26T00:08:55")
        assert bout["episode"], "the episode key is what an ack is bound to"

    def test_the_gap_threshold_is_above_the_longest_silence_in_that_bout(self):
        """EPISODE_GAP_S is measured, not picked: the bout's longest internal
        silence is 2h09m (21:44:04Z → 23:53:55Z), so a smaller gap would split
        the very bout #10156 is about."""
        stamps = [datetime.fromisoformat(t) for t in REAL_READ_VELOCITY_TS[3:]]
        longest = max((b - a).total_seconds()
                      for a, b in itertools.pairwise(stamps))
        # 21:44:04.560705Z → 23:53:55.051721Z = 2h09m50s, the longest silence
        # between two CONSECUTIVE firing windows in the bout.
        assert round(longest) == 7790, longest
        assert longest < EPISODE_GAP_S

    def test_a_silence_longer_than_the_gap_starts_a_new_episode(self):
        base = datetime(2026, 9, 25, 17, 19, tzinfo=timezone.utc)  # noqa: UP017
        alerts = abuse._alert_feed(
            [{"event_type": EVENT_READ_VELOCITY,
              "details": {"scope": "key", "id": KEY},
              "created_at": (base + timedelta(seconds=EPISODE_GAP_S + 61)).isoformat()},
             {"event_type": EVENT_READ_VELOCITY,
              "details": {"scope": "key", "id": KEY},
              "created_at": base.isoformat()}],
            acks=set(), limit=20)
        assert [a["count"] for a in alerts] == [1, 1]

    def test_rows_at_the_gap_boundary_stay_in_one_episode(self):
        base = datetime(2026, 9, 25, 17, 19, tzinfo=timezone.utc)  # noqa: UP017
        alerts = abuse._alert_feed(
            [{"event_type": EVENT_READ_VELOCITY,
              "details": {"scope": "key", "id": KEY},
              "created_at": (base + timedelta(seconds=EPISODE_GAP_S)).isoformat()},
             {"event_type": EVENT_READ_VELOCITY,
              "details": {"scope": "key", "id": KEY},
              "created_at": base.isoformat()}],
            acks=set(), limit=20)
        assert [a["count"] for a in alerts] == [2]

    def test_two_different_keys_are_two_episodes(self):
        """Joining them would mis-state both: they are different subjects."""
        base = datetime(2026, 9, 25, 17, 19, tzinfo=timezone.utc)  # noqa: UP017
        rows = [
            {"event_type": EVENT_READ_VELOCITY,
             "details": {"scope": "key", "id": "k1"},
             "created_at": (base + timedelta(minutes=5)).isoformat()},
            {"event_type": EVENT_READ_VELOCITY,
             "details": {"scope": "key", "id": "k2"},
             "created_at": base.isoformat()},
        ]
        alerts = abuse._alert_feed(rows, acks=set(), limit=20)
        assert len(alerts) == 2
        assert {a["count"] for a in alerts} == {1}

    def test_a_saturated_scan_reports_a_floor_not_a_count(self):
        """A count measured on a cut window is a floor. Rendering "×100" would
        overstate what we know; the feed must say "×100+" (``truncated``)."""
        base = datetime(2026, 9, 25, 17, 19, tzinfo=timezone.utc)  # noqa: UP017
        rows = [{"event_type": EVENT_READ_VELOCITY,
                 "details": {"scope": "key", "id": KEY},
                 "created_at": (base + timedelta(minutes=5 * i)).isoformat()}
                for i in range(abuse._ALERT_SCAN)]
        alerts = abuse._alert_feed(rows, acks=set(), limit=20,
                                   scan_saturated=True)
        assert alerts[-1]["truncated"] is True
        assert alerts[-1]["count"] == len(rows)

    def test_the_MEMORY_store_computes_saturation_itself(self):
        """#10156 review finding: the only truncation test called `_alert_feed`
        with `scan_saturated=True` handed to it, so no test exercised the STORE's
        own computation — which is where the production bug was (the Supabase
        store measured saturation AFTER filtering, so it was true only when ≥500
        *alert* rows arrived in a 500-row mixed scan, i.e. almost never)."""
        base = datetime(2026, 9, 25, 17, 19, tzinfo=timezone.utc)  # noqa: UP017
        store = MemoryAbuseStore()
        for i in range(abuse._ALERT_SCAN + 5):
            store.record_event(ORG, EVENT_READ_VELOCITY,
                               details={"scope": "key", "id": KEY},
                               created_at=base + timedelta(minutes=5 * i))
        alerts = store.recent_alerts(ORG)
        assert alerts[-1]["truncated"] is True, (
            "the store cut the scan and did not say so — the count is a floor")

    def test_the_SUPABASE_store_measures_saturation_BEFORE_filtering(self):
        """The same question at the other store, where the bug actually was.

        The fake returns `_ALERT_SCAN` rows of MIXED types (>half non-alert), so
        a saturation test taken after the alert-type filter reads ~40% and is
        False. It must be measured on the row scan instead."""
        base = datetime(2026, 9, 25, 17, 19, tzinfo=timezone.utc)  # noqa: UP017
        rows = []
        for i in range(abuse._ALERT_SCAN + 1):
            is_alert = i % 3 == 0
            rows.append({
                "id": i + 1, "org_id": ORG,
                "event_type": EVENT_READ_VELOCITY if is_alert else "flag_clear",
                "details": ({"scope": "key", "id": KEY} if is_alert else {}),
                "created_at": (base + timedelta(minutes=5 * i)).isoformat(),
            })
        fake = FakeControlPlane(tables={"abuse_events": rows})
        alerts = SupabaseAbuseStore(fake).recent_alerts(ORG)
        assert alerts, "the fixture must produce at least one episode"
        assert alerts[-1]["truncated"] is True, (
            "the row scan was cut but the store reported an exact count")

    def test_exactly_a_full_scan_is_NOT_reported_as_truncated(self):
        """The mirror of the test above, and the reason both stores now request
        ONE EXTRA row: a scan that returns exactly its budget may have seen
        EVERYTHING, so calling it cut is a false `×N+` (review finding — the two
        stores answered with `>` and `>=` and disagreed)."""
        base = datetime(2026, 9, 25, 17, 19, tzinfo=timezone.utc)  # noqa: UP017
        rows = [{
            "id": i + 1, "org_id": ORG, "event_type": EVENT_READ_VELOCITY,
            "details": {"scope": "key", "id": KEY},
            "created_at": (base + timedelta(minutes=5 * i)).isoformat(),
        } for i in range(abuse._ALERT_SCAN)]
        alerts = SupabaseAbuseStore(
            FakeControlPlane(tables={"abuse_events": rows})).recent_alerts(ORG)
        assert alerts[-1].get("truncated") is not True
        assert alerts[-1]["count"] == abuse._ALERT_SCAN

    def test_a_limit_cut_tail_is_not_reported_as_truncated(self):
        """`truncated` means THE SCAN was cut, not the caller's `limit`.

        Both are `out = episodes[:limit]`-shaped, so the distinction lives in one
        condition (review nit: deleting it left the suite green). Here the scan
        sees everything and only the caller asks for less — labelling the last
        returned episode `×N+` would overstate in the other direction.
        """
        store = MemoryAbuseStore()
        base = datetime(2026, 9, 25, 0, 0, tzinfo=timezone.utc)  # noqa: UP017
        # The distinction only exists when BOTH bounds bite: the scan must be
        # saturated (so `scan_saturated` is True) AND there must be more episodes
        # than the caller asked for. 250 rows of one key then 251 of another is
        # one saturated scan (501 rows) holding TWO episodes.
        for i in range(250):
            store.record_event(ORG, EVENT_READ_VELOCITY,
                               details={"scope": "key", "id": "k-a"},
                               created_at=base + timedelta(seconds=30 * i))
        for i in range(251):
            store.record_event(ORG, EVENT_READ_VELOCITY,
                               details={"scope": "key", "id": "k-b"},
                               created_at=base + timedelta(seconds=30 * (250 + i)))
        everything = store.recent_alerts(ORG, limit=abuse._ALERT_SCAN)
        assert len(everything) == 2, everything
        assert everything[-1]["truncated"] is True, (
            "fixture: the scan must actually be saturated")
        cut = store.recent_alerts(ORG, limit=1)
        assert len(cut) == 1
        assert cut[0].get("truncated") is not True, (
            "the caller's limit was reported as SCAN truncation — the episode "
            "returned is complete and its count is exact")

    def test_a_scan_FULL_of_non_alert_rows_is_reported_as_cut(self):
        """P2 (round-8 review). `truncated` rides on a ROW, so a scan that its own
        filters emptied has nothing to carry it: the newest `_ALERT_SCAN` rows can
        all be non-alert (a burst of dismissals — the type THIS change writes — or
        a day of `flag_clear`), the feed is legitimately empty, and the dashboard
        showed NOTHING at all: not `×N+`, not "Show older", not even "nothing in
        the last two weeks". That is a silent "nothing ever happened", and this PR
        exists to delete exactly that class of claim.

        The store now answers the question separately, and the route forwards it.
        """
        base = datetime(2026, 9, 25, 17, 19, tzinfo=timezone.utc)  # noqa: UP017
        rows = [{"id": i + 1, "org_id": ORG, "event_type": EVENT_ALERT_ACK,
                 "details": {"episode": f"e{i}"},
                 "created_at": (base + timedelta(minutes=i)).isoformat()}
                for i in range(3)]
        rows += [{"id": 1000 + i, "org_id": ORG, "event_type": "flag_clear",
                  "details": {},
                  "created_at": (base + timedelta(minutes=10 + i)).isoformat()}
                 for i in range(abuse._ALERT_SCAN + 1)]
        fake = FakeControlPlane(tables={"abuse_events": rows})
        store = SupabaseAbuseStore(fake)
        assert store.recent_alerts(ORG) == [], (
            "fixture: the filtered scan must leave nothing to render")
        assert store.alert_scan_saturated(ORG) is True, (
            "an empty feed whose window was full must not read as 'nothing "
            "happened'")

    def test_an_unreadable_saturation_probe_never_claims_the_feed_is_complete(self):
        """P2 (round-23 review). The probe's job is to stop an empty feed reading as
        "nothing ever happened"; returning False on its OWN failure asserts the
        opposite fact ("this feed is complete") on the strength of a read that
        never happened — the exact false negative the flag exists to remove, and a
        `debug` log hid it twice over. Unknown must fail toward "may be cut"."""
        class _Broken:
            def query(self, table, **kw):
                raise RuntimeError("control plane down")

        store = SupabaseAbuseStore(_Broken())
        assert store.alert_scan_saturated(ORG) is True, (
            "a failed probe reported the feed as COMPLETE — unknown must never "
            "read as 'nothing is missing'")

    def test_the_acks_this_feature_writes_do_not_consume_the_scan_budget(self):
        """A dismissal can never render, so it must not push real alerts out of
        the window — and before this fix the type this PR writes was NOT excluded
        server-side (round-8 review finding). An org that acknowledges a lot would
        have been hiding its own alerts."""
        base = datetime(2026, 9, 25, 17, 19, tzinfo=timezone.utc)  # noqa: UP017
        rows = [{"id": 5000 + i, "org_id": ORG, "event_type": EVENT_ALERT_ACK,
                 "details": {"episode": f"e{i}"},
                 "created_at": (base + timedelta(seconds=i)).isoformat()}
                for i in range(600)]
        rows.append({"id": 1, "org_id": ORG, "event_type": EVENT_SUSPEND,
                     "details": {}, "created_at": base.isoformat()})
        store = SupabaseAbuseStore(
            FakeControlPlane(tables={"abuse_events": rows}))
        assert [a["type"] for a in store.recent_alerts(ORG)] == [EVENT_SUSPEND], (
            "600 acknowledgements hid a suspension — the feature's own writes "
            "consumed the scan budget")

    def test_a_busy_org_s_NON_ALERT_volume_cannot_hide_an_alert(self):
        """#10156: the scan used to read the newest 100 rows of EVERY type and
        filter client-side, so a busy org (the live project holds 6 450
        ``point_create`` rows) could have no alert row in its window at all and
        the panel read "nothing ever happened". The non-alert types are excluded
        SERVER-side now and the budget is 500.

        Pins the two ``neq`` filters: dropping either one reds here (verified).
        It does NOT pin the 500-row budget — with the filters in place, one alert
        row fits in any budget (review finding; the first docstring claimed it
        pinned "the depth"). The budget is pinned by the saturation tests
        (``test_exactly_a_full_scan_is_NOT_reported_as_truncated`` and
        ``test_the_SUPABASE_store_measures_saturation_BEFORE_filtering``), which
        assert on row counts rather than on this fixture.
        """
        base = datetime(2026, 9, 25, 17, 19, tzinfo=timezone.utc)  # noqa: UP017
        rows = [{"id": i + 1, "org_id": ORG,
                 "event_type": "point_create", "details": {},
                 "created_at": (base + timedelta(seconds=10 * i)).isoformat()}
                for i in range(600)]
        rows.append({"id": 9999, "org_id": ORG, "event_type": EVENT_READ_VELOCITY,
                     "details": {"scope": "key", "id": KEY},
                     "created_at": base.isoformat()})
        alerts = SupabaseAbuseStore(
            FakeControlPlane(tables={"abuse_events": rows})).recent_alerts(ORG)
        assert [a["type"] for a in alerts] == [EVENT_READ_VELOCITY], (
            "600 newer non-alert rows hid the one real alert — the false "
            "negative the ageing/collapse affordances exist to prevent")

    def test_grouping_is_pure_and_keeps_the_newest_row_s_fields(self):
        """The folded episode answers with the NEWEST row's label/message, so
        every pre-existing consumer and its pins keep working."""
        newest = {"type": EVENT_READ_VELOCITY, "label": "Unusual read volume",
                  "message": "m", "at": "2026-09-26T00:08:55+00:00",
                  "_subject": ("key", KEY)}
        # One hour earlier: inside the gap, so the two fold together.
        older = dict(newest, at="2026-09-25T23:08:55+00:00")
        out = group_alert_episodes([newest, older])
        assert len(out) == 1
        assert out[0]["at"] == newest["at"]
        assert out[0]["first_at"] == older["at"]
        assert out[0]["count"] == 2
        assert "_subject" not in out[0], "the internal identity field must not leak"


# ── 3. Acknowledgement persistence ──────────────────────────────────────────

class TestAcknowledgementPersistence:
    """A dismissal that evaporates is not a dismissal (#10156 step 2)."""

    def test_memory_store_acknowledges_the_episode(self):
        store = _velocity_store()
        episode = store.recent_alerts(ORG)[0]["episode"]
        assert store.recent_alerts(ORG)[0]["acked"] is False
        store.record_alert_ack(ORG, episode, by="u1")
        assert store.recent_alerts(ORG)[0]["acked"] is True

    def test_supabase_store_persists_the_ack_as_a_row(self):
        fake = FakeControlPlane()
        store = SupabaseAbuseStore(fake)
        for ts in REAL_READ_VELOCITY_TS:
            store.record_event(ORG, EVENT_READ_VELOCITY,
                               details={"scope": "key", "id": KEY},
                               created_at=datetime.fromisoformat(ts))
        episode = store.recent_alerts(ORG)[0]["episode"]
        store.record_alert_ack(ORG, episode, by="u1")
        # The ROW is the evidence: a cookie or a process cache would leave no
        # abuse_events row behind.
        acks = [r for r in fake.tables["abuse_events"]
                if r["event_type"] == EVENT_ALERT_ACK]
        assert len(acks) == 1
        assert acks[0]["details"]["episode"] == episode
        assert acks[0]["details"]["by"] == "u1"
        # …and it is READ back as the acknowledgement, not re-derived in memory.
        assert [a["episode"] for a in store.alert_acks(ORG)] == [episode]
        assert store.recent_alerts(ORG)[0]["acked"] is True

    def test_acknowledging_a_WINDOWLESS_episode_raises(self):
        """The guard must refuse the shape that actually evaporates, not just
        `None` (review finding P3): `recent_alerts()`'s DEFAULT return is a dict
        WITHOUT `_identity`, and writing it produces an ack that can only match by
        the exact key — i.e. one that dies on the next slide. The route happens to
        pass `include_identity=True`, so this is the store's own contract."""
        store = SupabaseAbuseStore(FakeControlPlane())
        store.record_event(ORG, EVENT_READ_VELOCITY,
                           details={"scope": "key", "id": KEY})
        windowless = store.recent_alerts(ORG)[0]
        assert "_identity" not in windowless
        with pytest.raises(ValueError, match="would evaporate"):
            store.record_alert_ack(ORG, windowless["episode"], ack_of=windowless)

    def test_acknowledging_an_EPISODE_THAT_IS_NOT_THERE_raises(self):
        """A key-only ack is the shape that cannot survive the scan sliding, so
        the store refuses to write one (review nit): the route 404s first, and
        this is the second guard — a caller that skipped the resolve must not get
        a silently useless row."""
        store = SupabaseAbuseStore(FakeControlPlane())
        with pytest.raises(ValueError, match="would evaporate"):
            store.record_alert_ack(ORG, "made-up|episode", by="u1")

    def test_the_ack_row_never_renders_as_an_alert(self):
        store = SupabaseAbuseStore(FakeControlPlane())
        store.record_event(ORG, EVENT_READ_VELOCITY,
                           details={"scope": "key", "id": KEY})
        episode = store.recent_alerts(ORG)[0]["episode"]
        store.record_alert_ack(ORG, episode, by="u1")
        types = {a["type"] for a in store.recent_alerts(ORG)}
        assert EVENT_ALERT_ACK not in types
        assert EVENT_ALERT_ACK not in ALERT_TYPES

    def test_an_ack_survives_the_episode_growing_at_its_newest_end(self):
        """The key is identity + START. Anything derived from live state (the
        count, the last-seen time) would re-key the episode when another window
        fires, and the acknowledgement would silently evaporate."""
        store = MemoryAbuseStore()
        for ts in REAL_READ_VELOCITY_TS[3:]:
            store.record_event(ORG, EVENT_READ_VELOCITY,
                               details={"scope": "key", "id": KEY},
                               created_at=datetime.fromisoformat(ts))
        episode = store.recent_alerts(ORG)[0]["episode"]
        store.record_alert_ack(ORG, episode, by="u1")
        # A NEW window arrives, extending the same bout.
        store.record_event(ORG, EVENT_READ_VELOCITY,
                           details={"scope": "key", "id": KEY},
                           created_at=datetime.fromisoformat(
                               "2026-09-26T00:13:56+00:00"))
        newest = store.recent_alerts(ORG)[0]
        assert newest["count"] == 14, newest
        assert newest["episode"] == episode, "the key moved — the ack is lost"
        assert newest["acked"] is True

    def test_an_ack_survives_the_SCAN_SLIDING_PAST_the_episode_s_start(self):
        """P1 (round-3 review). The episode key is ``identity + first_at``, and
        ``first_at`` is the OLDEST ROW THE SCAN CAN SEE — so the key MOVES when
        the bounded 500-row window slides past the episode's beginning. Matching
        the acknowledgement by key alone therefore lost it: the row came back
        un-acknowledged, still CURRENT (the bout kept firing), and — when the
        ack route also refused truncated episodes — undismissable forever.

        Reproduced exactly as the reviewer did it: ack a 300-window bout, then
        let 300 newer alert rows of a DIFFERENT key arrive, so the scan drops the
        bout's first 100 rows. This is the ordinary case at the live org's volume
        (~440 ``flag`` rows/day ⇒ a 500-row scan rolls over in about a day)."""
        store = MemoryAbuseStore()
        base = datetime(2026, 9, 20, 0, 0, tzinfo=timezone.utc)  # noqa: UP017
        for i in range(300):
            store.record_event(ORG, EVENT_READ_VELOCITY,
                               details={"scope": "key", "id": KEY},
                               created_at=base + timedelta(minutes=5 * i))
        first = store.recent_alerts(ORG)[0]
        assert first["count"] == 300 and first.get("truncated") is not True
        store.record_alert_ack(ORG, first["episode"], by="u1")
        assert store.recent_alerts(ORG)[0]["acked"] is True

        # 300 newer alerts for ANOTHER key push the bout's first 100 rows out of
        # the scan window. The bout is unchanged and still firing.
        for i in range(300):
            store.record_event(ORG, EVENT_FLAG, rule="disjoint_rate",
                               details={"rule": "disjoint_rate"},
                               created_at=base + timedelta(minutes=5 * (i + 300)))
        after = [a for a in store.recent_alerts(ORG, limit=abuse._ALERT_SCAN)
                 if a["type"] == EVENT_READ_VELOCITY]
        assert after, "the bout fell out of the feed entirely"
        slid = after[0]
        assert slid["episode"] != first["episode"], (
            "fixture error: the key was expected to MOVE here — that is the "
            "condition this test exists for")
        assert slid["count"] == 200, slid
        assert slid["truncated"] is True
        assert slid["acked"] is True, (
            "the acknowledgement evaporated when the scan slid past the "
            "episode's start — the row is current again and undismissable")

    def test_an_ack_survives_the_WHOLE_acked_window_sliding_out(self):
        """P2 (round-5 review), the CONTIGUITY arm.

        The previous fix survived a batch of newer rows SMALLER than the scan.
        Push more than `_ALERT_SCAN` newer alert rows and the visible episode's
        `first_at` moves past the acked window's END, so overlap alone fails and
        the still-firing bout came back un-acknowledged.

        Contiguity is the fix: a gap of at most ``EPISODE_GAP_S`` between the
        acknowledged stretch and the visible one means the same bout — the same
        rule grouping uses to decide what one episode IS, so the acknowledgement
        and the grouping cannot disagree about where a bout ends. The fixture
        keeps the rollover inside that gap by making the newer rows DENSE (600
        rows over ~100 minutes: a burst, which is when the scan rolls fastest).
        """
        store = MemoryAbuseStore()
        base = datetime(2026, 9, 20, 0, 0, tzinfo=timezone.utc)  # noqa: UP017
        for i in range(300):
            store.record_event(ORG, EVENT_READ_VELOCITY,
                               details={"scope": "key", "id": KEY},
                               created_at=base + timedelta(minutes=5 * i))
        store.record_alert_ack(ORG, store.recent_alerts(ORG)[0]["episode"],
                               by="u1")
        acked = store.alert_acks(ORG)[0]

        # 600 newer alert rows of ANOTHER identity, in ~100 minutes, while the
        # acknowledged bout keeps firing in the same gap chain.
        for i in range(600):
            at = base + timedelta(minutes=5 * 300, seconds=10 * i)
            store.record_event(ORG, EVENT_FLAG, rule="disjoint_rate",
                               details={"rule": "disjoint_rate"}, created_at=at)
            if i % 6 == 0:
                store.record_event(ORG, EVENT_READ_VELOCITY,
                                   details={"scope": "key", "id": KEY},
                                   created_at=at + timedelta(seconds=1))
        visible = [a for a in store.recent_alerts(ORG, limit=abuse._ALERT_SCAN)
                   if a["type"] == EVENT_READ_VELOCITY]
        assert visible, "the bout fell out of the feed entirely"
        episode = visible[0]
        assert episode["first_at"] > acked["at"].isoformat(), (
            "fixture error: the visible episode must START AFTER the acked "
            "window ends, or overlap alone would carry the ack and this test "
            "would not exercise the contiguity arm")
        assert episode["acked"] is True, (
            "the acknowledgement died although the bout never stopped and its "
            "continuation is inside EPISODE_GAP_S of the acked window")

    def test_an_ack_LAPSES_when_the_continuation_is_beyond_the_gap(self):
        """The deliberate OTHER direction, stated rather than discovered.

        Once the acknowledged stretch AND its continuation have scrolled out of
        the 500-row scan, the data held cannot distinguish "the same bout, still
        firing" from "a new bout of the same identity" — the rows that would prove
        it are outside the window. The choice made here is to let the
        acknowledgement LAPSE: the row returns as CURRENT, visible and dismissable
        again. The alternative (letting the old acknowledgement cover whatever
        turns up next) would silently hide a genuinely new bout behind a dismissal
        from last week, which is the worse failure for a security panel.

        Both directions are pinned so a later change has to argue with the other.
        """
        store = MemoryAbuseStore()
        base = datetime(2026, 9, 20, 0, 0, tzinfo=timezone.utc)  # noqa: UP017
        for i in range(300):
            store.record_event(ORG, EVENT_READ_VELOCITY,
                               details={"scope": "key", "id": KEY},
                               created_at=base + timedelta(minutes=5 * i))
        store.record_alert_ack(ORG, store.recent_alerts(ORG)[0]["episode"],
                               by="u1")
        # 700 newer rows for another identity spread over ~2.5 days, with the
        # acknowledged bout resuming far beyond EPISODE_GAP_S of its own window.
        for i in range(700):
            at = base + timedelta(minutes=5 * (i + 300))
            store.record_event(ORG, EVENT_FLAG, rule="disjoint_rate",
                               details={"rule": "disjoint_rate"}, created_at=at)
            if i >= 690:
                store.record_event(ORG, EVENT_READ_VELOCITY,
                                   details={"scope": "key", "id": KEY},
                                   created_at=at + timedelta(seconds=1))
        visible = [a for a in store.recent_alerts(ORG, limit=abuse._ALERT_SCAN)
                   if a["type"] == EVENT_READ_VELOCITY]
        assert visible
        assert visible[0]["acked"] is False, (
            "a dismissal from beyond the window must not silence activity whose "
            "continuity nothing in the scan can prove")
        assert visible[0]["at"], "the row is still returned — it is not hidden"

    def test_acking_the_newest_bout_does_NOT_ack_an_EARLIER_one(self):
        """P1 (round-6 review), on the issue's OWN dataset.

        The contiguity arm was written as `delta <= EPISODE_GAP_S`, and `delta`
        (episode start − acked window end) is NEGATIVE for any episode that
        starts before the acknowledged window ends — which is every older bout
        further down a newest-first feed. So acknowledging the newest bout also
        stamped the older one `acked`, and because an acked row leaves `current`
        the panel could then print "Everything shown here is acknowledged." over
        an incident nobody reviewed. That is the hidden-incident failure this
        whole change exists to remove, in the incident register.

        Both directions were pinned and both were FORWARD; nothing constructed an
        older un-acknowledged bout of the same identity.
        """
        store = MemoryAbuseStore()
        for ts in REAL_READ_VELOCITY_TS:  # ×13 on 09-25/26, ×3 on 09-16
            store.record_event(ORG, EVENT_READ_VELOCITY,
                               details={"scope": "key", "id": KEY},
                               created_at=datetime.fromisoformat(ts))
        newest, older = store.recent_alerts(ORG)[:2]
        assert (newest["count"], older["count"]) == (13, 3), (newest, older)
        store.record_alert_ack(ORG, newest["episode"], by="u1")
        feed = store.recent_alerts(ORG)
        assert feed[0]["acked"] is True, "the acked bout must leave current"
        assert feed[1]["acked"] is False, (
            "acknowledging the newest bout also stamped an EARLIER bout of the "
            "same key as acknowledged — an unreviewed incident marked done")

    def test_acking_the_newest_flag_bout_does_NOT_ack_an_earlier_one(self):
        """The same failure on the incident register, where it is worst."""
        store = MemoryAbuseStore()
        first = datetime(2026, 9, 10, 9, 0, tzinfo=timezone.utc)   # noqa: UP017
        second = datetime(2026, 9, 13, 9, 0, tzinfo=timezone.utc)  # noqa: UP017
        store.record_event(ORG, EVENT_FLAG, rule="r1", details={"rule": "r1"},
                           created_at=first)
        for i in range(3):
            store.record_event(ORG, EVENT_FLAG, rule="r1",
                               details={"rule": "r1"},
                               created_at=second + timedelta(minutes=5 * i))
        feed = store.recent_alerts(ORG)
        assert len(feed) == 2, feed
        assert feed[0]["first_at"].startswith("2026-09-13")
        store.record_alert_ack(ORG, feed[0]["episode"], by="u1")
        feed = store.recent_alerts(ORG)
        assert feed[0]["acked"] is True
        assert feed[1]["acked"] is False, (
            "a 3-day-older flag bout was marked acknowledged by acking the newer "
            "one — and an acked row leaves the current list")

    def test_a_RE_SUSPENSION_after_an_ack_is_not_hidden_by_it(self):
        """P1 (round-7 review). Grouping-by-gap is right for detector WINDOWS and
        wrong for discrete EVENTS: a suspension re-fired inside EPISODE_GAP_S is a
        NEW operator action (or a failed appeal), not "×2 of one".

        Reproduced sequence: suspended at T → the owner acknowledges it (the panel
        invites exactly that) → the appeal succeeds → re-suspended at T+2h. Same
        identity `("suspend",)`, silence 2h < 3h, so grouping folds it into the
        acknowledged episode and only `at` moves — and the panel printed
        "Everything shown here is acknowledged." while the Organization was
        suspended right now.
        """
        store = MemoryAbuseStore()
        base = datetime(2026, 9, 25, 9, 0, tzinfo=timezone.utc)  # noqa: UP017
        store.record_event(ORG, EVENT_SUSPEND, created_at=base)
        episode = store.recent_alerts(ORG)[0]
        store.record_alert_ack(ORG, episode["episode"], by="u1")
        assert store.recent_alerts(ORG)[0]["acked"] is True

        # Appeal succeeds, then a SECOND suspension two hours later.
        store.record_event(ORG, EVENT_UNSUSPEND,
                           created_at=base + timedelta(minutes=60))
        store.record_event(ORG, EVENT_SUSPEND,
                           created_at=base + timedelta(minutes=120))
        live = next(a for a in store.recent_alerts(ORG)
                    if a["type"] == EVENT_SUSPEND)
        assert live["count"] == 2, (
            "fixture: grouping folds the two suspensions into one episode")
        assert live["acked"] is False, (
            "the Organization is suspended AGAIN and the row is still marked "
            "acknowledged — the dismissal hid a live incident")

    def test_a_second_NEW_COUNTRY_sign_in_after_an_ack_is_not_hidden(self):
        """The same gate for the other incident types: a new-country sign-in is a
        discrete credential event, so a second one after a dismissal is new."""
        store = MemoryAbuseStore()
        base = datetime(2026, 9, 25, 9, 0, tzinfo=timezone.utc)  # noqa: UP017
        store.record_event(ORG, EVENT_AUTH_IP, country="BR", created_at=base)
        episode = store.recent_alerts(ORG)[0]
        store.record_alert_ack(ORG, episode["episode"], by="u1")
        store.record_event(ORG, EVENT_AUTH_IP, country="BR",
                           created_at=base + timedelta(minutes=30))
        live = store.recent_alerts(ORG)[0]
        assert live["acked"] is False, (
            "a SECOND sign-in from the new country was folded into the "
            "dismissed episode and stayed hidden")

    def test_an_OBSERVATION_episode_MAY_absorb_rows_after_the_ack(self):
        """The other half of the same gate, so the fix cannot overshoot: for a
        velocity OBSERVATION a newer row is the same measurement re-reported
        every window, and one acknowledgement covering the ongoing rate is the
        whole point of the change (#10156 step 2)."""
        store = MemoryAbuseStore()
        for ts in REAL_READ_VELOCITY_TS[3:]:
            store.record_event(ORG, EVENT_READ_VELOCITY,
                               details={"scope": "key", "id": KEY},
                               created_at=datetime.fromisoformat(ts))
        store.record_alert_ack(ORG, store.recent_alerts(ORG)[0]["episode"],
                               by="u1")
        store.record_event(ORG, EVENT_READ_VELOCITY,
                           details={"scope": "key", "id": KEY},
                           created_at=datetime.fromisoformat(
                               "2026-09-26T00:13:56+00:00"))
        newest = store.recent_alerts(ORG)[0]
        assert newest["count"] == 14
        assert newest["acked"] is True, (
            "a velocity observation must stay acknowledged while it keeps firing "
            "— otherwise every poll re-opens the same ongoing rate")

    def test_a_LATER_bout_of_the_same_identity_is_not_acknowledged(self):
        """The other half of overlap matching: the acknowledgement must not leak
        forward forever. Once the acknowledged stretch has slid entirely out of
        view and a genuinely later bout fires (a silence longer than
        ``EPISODE_GAP_S``), the new episode is un-acknowledged and current."""
        store = MemoryAbuseStore()
        for ts in REAL_READ_VELOCITY_TS:
            store.record_event(ORG, EVENT_READ_VELOCITY,
                               details={"scope": "key", "id": KEY},
                               created_at=datetime.fromisoformat(ts))
        first = store.recent_alerts(ORG)[0]
        store.record_alert_ack(ORG, first["episode"], by="u1")
        assert store.recent_alerts(ORG)[0]["acked"] is True
        # A new bout, days later, on the same key: no window overlap.
        for ts in ("2026-10-02T09:00:00+00:00", "2026-10-02T09:05:00+00:00"):
            store.record_event(ORG, EVENT_READ_VELOCITY,
                               details={"scope": "key", "id": KEY},
                               created_at=datetime.fromisoformat(ts))
        newest = store.recent_alerts(ORG)[0]
        assert newest["episode"] != first["episode"]
        assert newest["acked"] is False, (
            "an acknowledgement from last week must not silence a new bout")
        # …and the ORIGINAL episode keeps its own acknowledgement.
        older = [a for a in store.recent_alerts(ORG) if a["episode"] == first["episode"]]
        assert older and older[0]["acked"] is True

    def test_an_unreadable_ack_register_reads_as_unacknowledged(self):
        """Fail-soft in the honest direction: fail to read is ABSENT, never
        PRESENT (a phantom acknowledgement would hide a live alert) — and it
        must NOT throw, because `GET /v1/team/alerts` catches everything, so a
        throw blanks the whole feed and takes the `suspend` row off the appeal
        surface with it (#10156 review finding: this test previously asserted a
        raise while its name, and the docstring it documents, said the
        opposite)."""
        class _Broken:
            def query(self, table, **kw):
                # The ack read fails; the alert scan succeeds.
                if any(f[0] == "event_type" and f[1] == "eq"
                       and f[2] == EVENT_ALERT_ACK for f in kw.get("filters", [])):
                    raise RuntimeError("control plane down")
                return [{"event_type": EVENT_SUSPEND,
                         "created_at": "2026-09-25T17:19:05+00:00",
                         "details": {}}]

        store = SupabaseAbuseStore(_Broken())
        assert store.alert_acks(ORG) == []
        feed = store.recent_alerts(ORG)
        assert [a["type"] for a in feed] == [EVENT_SUSPEND], (
            "a failed ack read must not remove the alert feed — on a suspended "
            "org that feed IS the appeal explanation")
        assert feed[0]["acked"] is False

    def test_acks_are_org_scoped(self):
        # IDENTICAL timestamps, on purpose: with distinct ones this test passed
        # even after the `org_id` guard in `_acks_from_rows` was removed, because
        # the two episodes then fell outside each other's window (review nit —
        # discriminating only by accident). Same instant, same key, different org:
        # the guard is now the only thing that can tell them apart.
        store = MemoryAbuseStore()
        ts = datetime(2026, 9, 25, 17, 19, tzinfo=timezone.utc)  # noqa: UP017
        store.record_event(ORG, EVENT_READ_VELOCITY,
                           details={"scope": "key", "id": KEY}, created_at=ts)
        episode = store.recent_alerts(ORG)[0]["episode"]
        store.record_alert_ack(ORG, episode, by="u1")
        store.record_event("other-org", EVENT_READ_VELOCITY,
                           details={"scope": "key", "id": KEY}, created_at=ts)
        assert store.recent_alerts("other-org")[0]["acked"] is False, (
            "an acknowledgement in one org silenced the same episode in another")

    def test_an_ack_does_not_cover_a_DIFFERENT_subject_s_episode(self):
        """The identity arm of the overlap match, which is the only thing
        stopping an ack for one key silencing ANOTHER key's live, overlapping
        episode (review finding: removing the identity guard entirely left the
        whole suite green — every ack test used one subject).

        Two keys, identical windows, one acknowledged: only the acknowledged
        one may leave the current list.
        """
        store = MemoryAbuseStore()
        base = datetime(2026, 9, 25, 17, 19, tzinfo=timezone.utc)  # noqa: UP017
        for i in range(13):
            ts = base + timedelta(minutes=5 * i)
            store.record_event(ORG, EVENT_READ_VELOCITY,
                               details={"scope": "key", "id": KEY}, created_at=ts)
            store.record_event(ORG, EVENT_READ_VELOCITY,
                               details={"scope": "key", "id": "other-key"},
                               created_at=ts)
        feed = store.recent_alerts(ORG)
        mine = next(a for a in feed if KEY in a["episode"])
        theirs = next(a for a in feed if "other-key" in a["episode"])
        assert mine["first_at"] == theirs["first_at"], "fixture: windows must overlap"
        store.record_alert_ack(ORG, mine["episode"], by="u1")
        feed = store.recent_alerts(ORG)
        assert next(a for a in feed if KEY in a["episode"])["acked"] is True
        assert next(a for a in feed if "other-key" in a["episode"])["acked"] is False, (
            "acknowledging one key's episode silenced a DIFFERENT key's "
            "overlapping episode — a live alert disappeared")


# ── 4. The copy (the point of the change) ───────────────────────────────────

#: The exact sentence the panel printed above the list. Kept as a literal so the
#: regression test below is a statement about the DEFECT, not a shape.
OLD_INCIDENT_LEAD = ("Suspicious activity detected on this Organization. "
                     "Revoke any key you don't recognize.")

BLANKET_IMPERATIVE = re.compile(r"revoke any key", re.IGNORECASE)
INCIDENT_CLAIM = re.compile(r"suspicious activity detected", re.IGNORECASE)


def _velocity_message(**details) -> str:
    return abuse._alert_dict({
        "event_type": EVENT_READ_VELOCITY,
        "details": {"scope": "key", "id": KEY, **details},
    })["message"]


class TestVelocityCopy:
    """A notify-only observation must not be described as an incident."""

    def test_the_blanket_imperative_is_gone(self):
        message = _velocity_message()
        assert not BLANKET_IMPERATIVE.search(message), message
        assert not INCIDENT_CLAIM.search(message), message

    def test_the_message_states_the_fact_and_the_consequence(self):
        message = _velocity_message()
        assert "informational" in message.lower(), message
        # Round 11 (review finding): the reassurance is scoped to THE ALERT, not
        # to the org's whole state — the same panel can carry a suspension row,
        # so "nothing was changed" was a claim this row cannot make.
        assert "it did not change anything" in message.lower(), message
        assert "nothing was changed" not in message.lower(), message
        assert "read volume" in message.lower(), message

    def test_the_message_names_NO_identifier_the_customer_cannot_find(self):
        """#10156 review finding. An earlier version interpolated
        ``details["id"]`` — ``api_keys.id``, a 24-hex value — and said "revoke it
        in Keys". The Keys table renders the key's NAME plus ``key_prefix``
        (``tt_xxxxxxxx``); the id appears nowhere in the UI, so the sentence
        offered an identifier the customer could not match to any row and made
        its one action unperformable — the same unactionable-remedy class this
        change exists to remove."""
        message = _velocity_message()
        assert KEY not in message, message
        assert not re.search(r"\b[0-9a-f]{16,}\b", message), message
        assert "keys" in message.lower(), (
            "the remedy must point at the surface the customer can actually read")

    def test_the_revoke_route_is_conditional_on_not_recognising_the_activity(self):
        message = _velocity_message()
        assert re.search(r"if you don'?t recognise this activity", message,
                         re.IGNORECASE), message
        assert re.search(r"revoked there", message, re.IGNORECASE), message

    def test_an_org_scope_message_carries_no_revoke_instruction(self):
        """There is no single key to recognise, so there is nothing to offer a
        destructive action about."""
        message = abuse._alert_dict({
            "event_type": EVENT_READ_VELOCITY,
            "details": {"scope": "team", "id": ORG},
        })["message"]
        assert not re.search(r"revoke", message, re.IGNORECASE), message
        assert "it did not change anything" in message.lower(), message

    def test_the_label_is_not_an_incident_word(self):
        label = abuse._alert_dict({
            "event_type": EVENT_READ_VELOCITY,
            "details": {"scope": "key", "id": KEY},
        })["label"]
        assert not INCIDENT_CLAIM.search(label), label
        assert "unusual" in label.lower(), label

    def test_the_old_copy_would_fail_every_assertion_above(self):
        """THE MUTATION, spelled out. Reverting ``_read_velocity_message`` to the
        pre-#10156 sentence reds the four tests above — this test proves those
        assertions are load-bearing rather than vacuous, by showing the defect's
        own text tripping them.

        (A future edit that "fixes" a red by widening the regex is visible here:
        the literal it must still match is in this file.)
        """
        assert BLANKET_IMPERATIVE.search(OLD_INCIDENT_LEAD)
        assert INCIDENT_CLAIM.search(OLD_INCIDENT_LEAD)
        # The pre-fix per-row sentence, verbatim from ``_alert_dict`` at
        # origin/main before this change.
        old_row = "Unusual read velocity detected on an API key"
        assert "nothing was changed" not in old_row.lower()
        assert "informational" not in old_row.lower()


# ── 5. Calibration (#10156 step 4) ──────────────────────────────────────────

WINDOW = 300


def _drive(tracker, key_id, org_id, per_window, windows, start=1_000_000.0):
    """Feed `per_window` reads per window for `windows` windows; count breaches."""
    fires = []
    now = start
    for w in range(windows):
        for _ in range(per_window):
            hit = tracker.record_read(key_id, org_id, now=now)
            if hit:
                fires.append((w, hit))
            now += WINDOW / per_window
    return fires


def _firing_windows(series, **kw):
    """How many WINDOWS breach, for a list of reads-per-window. The tracker's
    dedup is one notify per window, so this is the alert count."""
    params = dict(threshold=100, window_s=300, baseline_factor=3,
                  baseline_samples=6)
    params.update(kw)
    tracker = ReadVelocityTracker(**params)
    fired = 0
    for window, count in enumerate(series):
        for i in range(count):
            if tracker.record_read("k", "o", now=1e6 + window * 300 + i * 1e-3):
                fired += 1
    return fired


class TestCalibration:
    """A key at its own normal rate must not alert (#10156 step 4).

    ⚠️ The measured trade is pinned HERE, both halves, because a test that only
    asserts the win would let the loss grow silently (finding H: the PR body
    previously claimed a sensitivity property that did not reproduce — a 2x/window
    ramp DOES fire). The numbers below are the table in
    ``ReadVelocityTracker``'s docstring; change one and this reds on purpose.
    """

    #: The docstring table in ``ReadVelocityTracker``, ALL of it: (label, series,
    #: before, after). "before" is the pre-#10156 detector (the floor alone).
    #: Review finding (P3): the first version pinned 5 of the 13 rows while the
    #: PR claimed the whole table was pinned. Every number here is measured, so
    #: re-measuring is `uv run pytest -k measured_trade` — change one and the
    #: prose must move with it.
    TRADE = (
        ("steady 130 x20", [130] * 20, 20, 1),
        ("steady 500 x20", [500] * 20, 20, 1),
        ("steady 20 x20", [20] * 20, 0, 0),
        ("bursty 20/500 x20", [20, 500] * 10, 10, 4),
        ("bursty 50/300 x20", [50, 300] * 10, 10, 2),
        ("geometric x1.5", [min(2000, int(5 * 1.5 ** i))
                            for i in range(14)], 6, 6),
        ("geometric x1.3", [min(2000, int(5 * 1.3 ** i))
                            for i in range(14)], 2, 0),
        ("linear 100->500 (+40, 11w)", [100 + 40 * i for i in range(11)], 10, 0),
        ("linear 100->200 (+10, 11w)", [100 + 10 * i for i in range(11)], 10, 0),
        ("linear 20->540 (+40, 14w)", [20 + 40 * i for i in range(14)], 11, 2),
        ("linear 5->705 (+100, 8w)", [5 + 100 * i for i in range(8)], 7, 5),
        ("5 -> 400 spike", [5] * 10 + [400], 1, 1),
        ("cold start 150", [150], 1, 1),
    )

    def test_the_measured_trade_is_pinned_on_both_sides(self):
        """The docstring's table, row for row — the WINS and the LOSSES.

        A test that only asserted the win would let the loss grow silently, and
        this branch has already shipped two wrong prose claims about this exact
        table. Both columns are asserted, including the rows where the detector
        got WORSE, so a change that moves a number has to update the prose.
        """
        floor_only = lambda series: sum(1 for c in series if c > 100)  # noqa: E731
        for label, series, before, after in self.TRADE:
            assert floor_only(series) == before, (
                f"{label}: the 'before' column is wrong (measured "
                f"{floor_only(series)})")
            assert _firing_windows(series) == after, (
                f"{label}: measured {_firing_windows(series)} firing windows, "
                f"docstring says {after} — update ReadVelocityTracker's table and "
                f"the PR, do not just flip this number")

    def test_the_factor_two_trade_is_also_pinned(self):
        """The docstring's justification for keeping factor=3: at factor 2 a
        linear ramp is STILL missed, while bursty traffic returns to the old
        noise. Both halves, because that comparison is the whole argument."""
        bursty = [20, 500] * 10
        linear_high = [100 + 40 * i for i in range(11)]
        assert _firing_windows(bursty, baseline_factor=3) == 4
        assert _firing_windows(bursty, baseline_factor=2) == 10, (
            "factor=2 is supposed to return bursty traffic to the pre-#10156 "
            "noise — that is why the default stays 3")
        assert _firing_windows(linear_high, baseline_factor=2) == 0, (
            "factor=2 does NOT recover the linear-ramp loss, so lowering it "
            "buys noise and no ramp sensitivity")

    def test_a_key_at_its_own_normal_rate_does_not_keep_alerting(self):
        """THE DEFECT'S MECHANISM: 130 reads/5min is a normal retrieval-heavy
        agent turn. Under the pre-#10156 rule this fired EVERY window — the live
        dataset shows exactly that (13 rows for one 6h49m bout)."""
        tracker = ReadVelocityTracker(threshold=100, window_s=WINDOW)
        fires = _drive(tracker, "busy-key", "org", 130, 20)
        assert len(fires) == 1, (
            "a key at a steady 130 reads/5min must cross the floor once and "
            f"then be recognised as its own normal rate, not {len(fires)} times")

    def test_the_same_traffic_under_the_old_rule_fires_every_window(self):
        """The other half of the measurement, pinned: with the baseline disabled
        (factor 0 → the floor alone decides, which is the pre-#10156 rule) the
        same traffic fires on every window. This is what "before" means."""
        tracker = ReadVelocityTracker(threshold=100, window_s=WINDOW,
                                      baseline_factor=0)
        fires = _drive(tracker, "busy-key", "org", 130, 20)
        assert len(fires) == 20, [w for w, _ in fires]

    def test_a_key_far_above_its_own_baseline_does_alert(self):
        """The detector is CALIBRATED, not disabled: 5 reads/window for ten
        windows, then 400 in one window."""
        tracker = ReadVelocityTracker(threshold=100, window_s=WINDOW)
        fires = _drive(tracker, "quiet-key", "org2", 5, 10)
        assert fires == [], fires
        spike = _drive(tracker, "quiet-key", "org2", 400, 1,
                       start=1_000_000.0 + 10 * WINDOW)
        assert spike, "a 80x jump over the key's own baseline must alert"
        assert spike[0][1] == ("key", "quiet-key")

    def test_a_cold_start_key_is_judged_on_the_floor_alone(self):
        """No baseline yet (first window after a deploy — the tracker is
        in-memory) means the absolute threshold is the only fact available;
        suppressing it would be inventing one."""
        tracker = ReadVelocityTracker(threshold=100, window_s=WINDOW)
        fires = _drive(tracker, "new-key", "new-org", 150, 1)
        assert fires and fires[0][1] == ("key", "new-key")

    def test_the_baseline_is_sampled_per_window_not_per_read(self):
        """THE MUTATION. An EWMA updated on every READ converges on the live
        count within a few calls, which makes `factor * baseline` always exceed
        the count — a detector turned off, not fixed. This test drives the SAME
        traffic through a tracker whose baseline is updated per read and
        requires the difference: it goes silent, which is exactly why
        `_sample_baseline` is gated on the window."""
        tracker = ReadVelocityTracker(threshold=100, window_s=WINDOW)

        def per_read(scope, ident, now):
            key = (scope, ident)
            bucket = (tracker._by_org if scope == "team" else tracker._by_key)
            count = len(bucket.get(ident, ()))
            prev = tracker._baseline.get(key)
            tracker._baseline[key] = (
                float(count) if prev is None
                else prev + (float(count) - prev) / 6.0)

        tracker._sample_baseline = per_read  # the mutated implementation
        fires = _drive(tracker, "busy-key", "org", 130, 20)
        # The mutated implementation goes SILENT (0), the shipped one fires
        # exactly once (`test_a_key_at_its_own_normal_rate_does_not_keep_alerting`
        # pins the 1). Asserting `<= 1` here would have passed under BOTH and
        # proved nothing (#10156 review finding).
        assert fires == [], (
            "the per-read mutation must silence the detector; the pair of tests "
            f"is only discriminating if this is 0 and the other is 1: got {fires}")

    def test_a_non_positive_threshold_keeps_its_fail_closed_semantics(self):
        """#5493: threshold 0 is a configured deny-ALL. Calibration must not
        loosen an operator's fail-closed setting."""
        tracker = ReadVelocityTracker(threshold=0, window_s=WINDOW)
        assert tracker.record_read("k", "o", now=1000.0) == ("key", "k")
        # …and it keeps denying after the baseline has learned the rate.
        fires = _drive(tracker, "k2", "o2", 5, 10)
        assert len(fires) == 10, len(fires)

    def test_an_exempt_key_skips_only_the_key_scope(self):
        tracker = ReadVelocityTracker(threshold=100, window_s=WINDOW,
                                      exempt_keys={"probe-key"})
        fires = _drive(tracker, "probe-key", "our-org", 200, 3)
        assert all(scope != "key" for _, (scope, _id) in fires), fires
        assert fires, ("the ORG total is still a real signal — an exempt key "
                       "must not silence it")

    def test_an_exempt_org_never_alerts_at_all(self):
        tracker = ReadVelocityTracker(threshold=100, window_s=WINDOW,
                                      exempt_orgs={"first-party"})
        assert _drive(tracker, "k", "first-party", 500, 5) == []

    def test_the_exemption_allowlists_parse_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("TORTOISE_ABUSE_EXEMPT_KEY_IDS", " a , ,b, ")
        monkeypatch.setenv("TORTOISE_ABUSE_EXEMPT_ORG_IDS", "org1")
        tracker = ReadVelocityTracker(threshold=100, window_s=WINDOW)
        assert tracker.exempt_keys == {"a", "b"}, (
            "an empty entry must never become the empty key id, which would "
            "exempt the per-key scope wholesale")
        assert tracker.exempt_orgs == {"org1"}

    def test_the_floor_default_is_unchanged(self, monkeypatch):
        """#10156 does NOT raise the threshold to make the panel quiet: a
        detector turned down to silence is not a fixed detector."""
        monkeypatch.delenv("TORTOISE_ABUSE_READ_THRESHOLD", raising=False)
        assert ReadVelocityTracker().threshold == 100

    def test_the_calibration_maps_are_bounded_like_the_count_buckets(self):
        """EVERY per-(scope, id) calibration map is bounded, measured on the maps
        that actually grow.

        Two earlier versions of this test were green while the leak it names was
        live (round-3 and round-7 review findings), for two different reasons:
        the first asserted a value measured at 0; the second drove one read per id,
        so no window ever COMPLETED and `_baseline` stayed empty — the assertion
        was on `_window_started` alone while its own docstring admitted
        `_baseline` was the map that leaked.

        So: every id here completes a window (two reads, one window apart), which
        is the only way `_baseline` fills, and BOTH maps are asserted before and
        after the prune. The prune lives in `record_read`; `_window_count` is
        asserted too because it is the third map in the same block.
        """
        tracker = ReadVelocityTracker(threshold=100, window_s=WINDOW)
        start = 1_000_000.0
        for i in range(10_050):
            tracker.record_read(f"k{i}", None, now=start)
            # …and one window later, so the window COMPLETES and the scope enters
            # `_baseline` (a single read never does).
            tracker.record_read(f"k{i}", None, now=start + WINDOW)
        for name in ("_window_started", "_window_count", "_baseline"):
            size = len(getattr(tracker, name))
            assert size >= 10_000, (name, size)
        # A later window, long after every earlier scope went quiet, must prune
        # ALL THREE: keeping a baseline while discarding the counts would judge a
        # silent scope against a rate it no longer has.
        late = start + 10 * WINDOW
        tracker.record_read("k-late", None, now=late)
        for name in ("_window_started", "_window_count", "_baseline"):
            size = len(getattr(tracker, name))
            assert size < 100, (name, size)
        assert ("key", "k-late") in tracker._window_started

