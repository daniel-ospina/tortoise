"""#3839 (lane B7) — the retention indicator (teams active at day 7/15/30/60/90).

The indicator's whole reason to exist is one rule, and it is easy to get wrong:

    a ZERO (or a rate) is only ever emitted with ``state == "measured"``, and a
    mark whose day has NOT ARRIVED is ``null`` — never ``false``.

The issue thread settled the definition; these tests pin the four axes that
decide whether the number means anything (start = provisioned, use =
capture-or-useful-search, UTC days, cumulative "on or after day N"), the
per-mark ambiguity rule for a forward-only ``result_count``, and the binding
guard D12-b: this is an AGGREGATION, never a data-retention window.
"""
from __future__ import annotations

import os
from datetime import UTC, date, datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

from tests._http_fixtures import patched_tortoise_sdk
from tortoise import retention_indicator as ri
from tortoise.hosted_api import app

TODAY = datetime.now(UTC).date()
AS_OF = TODAY


def _iso(day: date, hour: int = 12) -> str:
    return datetime.combine(day, time(hour), tzinfo=UTC).isoformat()


def _capture(day: date, hour: int = 12, org: str = "org-a") -> dict:
    return {"org_id": org, "event_name": ri.CAPTURE_EVENT,
            "created_at": _iso(day, hour), "properties": {}}


def _search(day: date, count: int | None, *, tool: str = "tortoise_search",
            org: str = "org-a", hour: int = 12) -> dict:
    props: dict = {"tool_name": tool}
    if count is not None:
        props[ri.RESULT_COUNT_PROP] = count
    return {"org_id": org, "event_name": ri.MCP_TELEMETRY_EVENT,
            "created_at": _iso(day, hour), "properties": props}


# ══ pure: the interval, the axes, the fold ═════════════════════════════════

class TestWilson:
    def test_matches_the_standard_worked_example(self):
        low, high = ri.wilson_interval(8, 10)
        assert low == pytest.approx(0.4901, abs=1e-3), low
        assert high == pytest.approx(0.9433, abs=1e-3), high

    def test_bounds_are_always_inside_the_unit_interval(self):
        """The naive normal interval can produce impossible bounds at small n;
        the Wilson one cannot. This is why the standard is adopted."""
        for k, n in ((0, 1), (1, 1), (0, 3), (1, 3), (3, 3), (0, 10), (10, 10)):
            low, high = ri.wilson_interval(k, n)
            assert 0.0 <= low <= high <= 1.0, (k, n, low, high)

    def test_zero_of_n_still_has_an_upper_bound(self):
        low, high = ri.wilson_interval(0, 10)
        assert low == 0.0
        assert high == pytest.approx(0.2775, abs=1e-3)

    def test_no_denominator_is_not_a_rate(self):
        assert ri.wilson_interval(0, 0) == (None, None)

    def test_k_is_clamped_to_n(self):
        assert ri.wilson_interval(99, 10) == ri.wilson_interval(10, 10)


class TestActivityPredicate:
    def test_a_capture_is_a_use(self):
        assert ri.row_establishes_use(_capture(TODAY)) is True

    def test_a_search_that_returned_something_is_a_use(self):
        assert ri.row_establishes_use(_search(TODAY, 3)) is True

    def test_a_search_that_returned_nothing_is_not_a_use(self):
        assert ri.row_establishes_use(_search(TODAY, 0)) is False

    def test_a_non_retrieval_tool_is_not_a_use(self):
        assert ri.row_establishes_use(
            _search(TODAY, 5, tool="tortoise_health")) is False

    def test_an_unrelated_event_is_not_a_use(self):
        assert ri.row_establishes_use(
            {"event_name": "onboarding_seed_complete", "properties": {}}) is False

    def test_a_missing_result_count_is_unclassifiable_not_false(self):
        """#3675's prop is forward-only. A retrieval row without it cannot
        establish a use AND cannot rule one out — the day is ambiguous."""
        assert ri.row_establishes_use(_search(TODAY, None)) is None

    def test_a_non_int_result_count_is_unclassifiable(self):
        assert ri.row_establishes_use(_search(TODAY, "3")) is None
        assert ri.row_establishes_use(_search(TODAY, True)) is None

    def test_a_non_mapping_properties_does_not_raise(self):
        assert ri.row_establishes_use(
            {"event_name": ri.MCP_TELEMETRY_EVENT, "properties": None}) is None
        assert ri.row_establishes_use(
            {"event_name": ri.MCP_TELEMETRY_EVENT, "properties": {}}) is None
        assert ri.row_establishes_use(
            {"event_name": ri.MCP_TELEMETRY_EVENT, "properties": "oops"}) is None

    def test_properties_as_a_json_string_is_read(self):
        """PostgREST can hand the jsonb column back as a JSON-encoded STRING.
        That shape is measured live on this store and is parsed by
        ``activation_scorecard._row_props`` (pinned there by
        ``test_properties_as_a_json_string_is_counted``); the scorecard is the
        module this predicate claims parity with, so the retrieval leg must
        read it too. Without that branch every search row on this store shape
        reads unclassifiable and the leg silently contributes nothing."""
        import json

        row = {"event_name": ri.MCP_TELEMETRY_EVENT, "created_at": _iso(TODAY)}
        assert ri.row_establishes_use(
            {**row, "properties": json.dumps(
                {"tool_name": "tortoise_search", ri.RESULT_COUNT_PROP: 3})}
        ) is True
        assert ri.row_establishes_use(
            {**row, "properties": json.dumps(
                {"tool_name": "tortoise_search", ri.RESULT_COUNT_PROP: 0})}
        ) is False
        # The unreadable-count rule is unchanged on this shape: a string with
        # no result_count is still ambiguous, not a silent "not a use".
        assert ri.row_establishes_use(
            {**row, "properties": json.dumps({"tool_name": "tortoise_search"})}
        ) is None

    def test_a_json_string_that_is_not_a_mapping_is_not_a_use(self):
        import json

        assert ri._row_props({"properties": json.dumps([1, 2])}) == {}
        assert ri._row_props({"properties": json.dumps("nope")}) == {}
        assert ri.row_establishes_use(
            {"event_name": ri.MCP_TELEMETRY_EVENT,
             "properties": json.dumps("not-a-mapping")}) is None


class TestFoldActivityDays:
    def test_collects_only_qualifying_days(self):
        days = [_iso(TODAY - timedelta(days=1))]
        rows = [_capture(TODAY - timedelta(days=1)),
                _search(TODAY - timedelta(days=2), 0),
                _search(TODAY - timedelta(days=3), 4)]
        active, ambiguous, reason = ri.fold_activity_days(
            rows, since_day=TODAY - timedelta(days=30), until_day=TODAY)
        assert reason is None
        assert {d.isoformat() for d in active} == {days[0][:10],
                                                   (TODAY - timedelta(days=3)).isoformat()}
        assert ambiguous == set()

    def test_an_unclassifiable_retrieval_is_reported_separately(self):
        rows = [_search(TODAY - timedelta(days=4), None)]
        active, ambiguous, reason = ri.fold_activity_days(
            rows, since_day=TODAY - timedelta(days=30), until_day=TODAY)
        assert reason is None
        assert active == set()
        assert {d.isoformat() for d in ambiguous} == {
            (TODAY - timedelta(days=4)).isoformat()}

    def test_an_unparseable_timestamp_refuses_the_fold(self):
        rows = [{"event_name": ri.CAPTURE_EVENT, "created_at": "not-a-date",
                 "properties": {}}]
        active, _ambiguous, reason = ri.fold_activity_days(
            rows, since_day=TODAY - timedelta(days=30), until_day=TODAY)
        assert active == set()
        assert reason == ri.REASON_UNPARSEABLE_CREATED_AT

    def test_a_naive_timestamp_is_unparseable_not_assumed(self):
        rows = [{"event_name": ri.CAPTURE_EVENT,
                 "created_at": "2026-10-01T12:00:00", "properties": {}}]
        _a, _b, reason = ri.fold_activity_days(
            rows, since_day=TODAY - timedelta(days=365), until_day=TODAY)
        assert reason == ri.REASON_UNPARSEABLE_CREATED_AT

    def test_an_out_of_window_row_refuses_the_fold(self):
        """A server-side predicate that did not apply returns rows; counting
        them is a plausible wrong number, so the fold refuses."""
        rows = [_capture(TODAY + timedelta(days=2))]
        _a, _b, reason = ri.fold_activity_days(
            rows, since_day=TODAY - timedelta(days=30), until_day=TODAY)
        assert reason == ri.REASON_WINDOW_PREDICATE_NOT_APPLIED

    def test_a_non_mapping_row_refuses_the_fold(self):
        """A shape anomaly must not be skipped silently: skipping under-counts
        with no signal."""
        _a, _b, reason = ri.fold_activity_days(
            ["not-a-row"], since_day=TODAY - timedelta(days=30),
            until_day=TODAY)
        assert reason == ri.REASON_ANALYTICS_SHAPE

    def test_day_boundaries_are_utc(self):
        """A use at 23:00Z and one at 01:00Z the next day are TWO days, not
        one — the UTC boundary is the definition."""
        rows = [_capture(TODAY, hour=23), _capture(TODAY + timedelta(days=1), hour=1)]
        active, _amb, reason = ri.fold_activity_days(
            rows, since_day=TODAY, until_day=TODAY + timedelta(days=1))
        assert reason is None
        assert len(active) == 2


class TestMarks:
    def test_an_unarrived_mark_is_none_not_false(self):
        start = AS_OF - timedelta(days=10)
        row = ri.team_row(org_id="o", start=start.isoformat(),
                          state=ri.STATE_MEASURED, reason=None,
                          active_days=set(), as_of=AS_OF)
        assert row["active_at"]["7"] is False
        assert row["active_at"]["30"] is None   # day 30 has not arrived
        assert row["active_at"]["90"] is None

    def test_cumulative_not_exact_day(self):
        """Activity on day 12 counts for the day-7 mark (on/after), which is
        the whole reason the exact-day reading was rejected."""
        start = AS_OF - timedelta(days=40)
        row = ri.team_row(org_id="o", start=start.isoformat(),
                          state=ri.STATE_MEASURED, reason=None,
                          active_days={start + timedelta(days=12)}, as_of=AS_OF)
        assert row["active_at"]["7"] is True
        assert row["active_at"]["15"] is False

    def test_activity_before_the_first_mark_does_not_retain(self):
        start = AS_OF - timedelta(days=40)
        row = ri.team_row(org_id="o", start=start.isoformat(),
                          state=ri.STATE_MEASURED, reason=None,
                          active_days={start + timedelta(days=5)}, as_of=AS_OF)
        assert row["active_at"]["7"] is False

    def test_an_unavailable_team_has_no_false_marks(self):
        row = ri.team_row(org_id="o", start=(AS_OF - timedelta(days=40)).isoformat(),
                          state=ri.STATE_UNAVAILABLE, reason="x",
                          active_days=None, as_of=AS_OF)
        assert set(row["active_at"].values()) == {None}

    def test_ambiguity_is_applied_per_mark(self):
        """A day-10 ambiguity cannot make the day-30 mark unknown — refusing
        the whole team would throw away a sound measurement."""
        start = AS_OF - timedelta(days=40)
        row = ri.team_row(org_id="o", start=start.isoformat(),
                          state=ri.STATE_MEASURED, reason=None,
                          active_days=set(),
                          ambiguous_days={start + timedelta(days=10)},
                          as_of=AS_OF)
        assert row["active_at"]["7"] is None      # day-10 is inside [day 7, as_of]
        assert row["active_at"]["15"] is False    # day-10 is before day 15
        assert row["unclassifiable_marks"] == ["7"]

    def test_a_definite_use_settles_the_mark_despite_an_ambiguous_day(self):
        """The definition is a DISJUNCTION over days: one genuine use on/after
        day N settles the mark ``True``, so an ambiguous day in the same window
        cannot turn a provably-retained team into an unknown.

        Regression: the ambiguity was consulted BEFORE the verdict, so a
        day-10 ambiguity made the day-7 mark ``None`` even though a capture on
        day 20 had already established it. That contradicted the module
        docstring's own "ONLY candidate use" rule and made the cumulative
        series non-monotone."""
        start = AS_OF - timedelta(days=40)
        row = ri.team_row(org_id="o", start=start.isoformat(),
                          state=ri.STATE_MEASURED, reason=None,
                          active_days={start + timedelta(days=20)},
                          ambiguous_days={start + timedelta(days=10)},
                          as_of=AS_OF)
        assert row["active_at"] == {"7": True, "15": True, "30": False,
                                    "60": None, "90": None}
        assert row["unclassifiable_marks"] == []

    def test_a_settled_mark_is_never_none_above_a_true_one(self):
        """CUMULATIVE(A) >= CUMULATIVE(B) for A < B: a definite use on day 20
        has to read ``True`` at day 7 AND day 15 — a hole at the weaker mark
        while the stronger one is True is a contradiction the reader cannot
        interpret (and it silently dropped retained teams out of the bands)."""
        start = AS_OF - timedelta(days=40)
        row = ri.team_row(org_id="o", start=start.isoformat(),
                          state=ri.STATE_MEASURED, reason=None,
                          active_days={start + timedelta(days=20)},
                          ambiguous_days={start + timedelta(days=12)},
                          as_of=AS_OF)
        seen = [row["active_at"][str(n)] for n in ri.RETENTION_MARKS]
        assert seen[:3] == [True, True, False]
        assert row["unclassifiable_marks"] == []

    def test_the_90_day_histogram_window_is_bounded(self):
        start = AS_OF - timedelta(days=200)
        old = {start + timedelta(days=5)}          # 195 days ago: outside 90
        row = ri.team_row(org_id="o", start=start.isoformat(),
                          state=ri.STATE_MEASURED, reason=None,
                          active_days=old, as_of=AS_OF)
        assert row["active_days_90"] == 0

    def test_trailing_30_is_a_window_not_a_total(self):
        start = AS_OF - timedelta(days=200)
        row = ri.team_row(org_id="o", start=start.isoformat(),
                          state=ri.STATE_MEASURED, reason=None,
                          active_days={AS_OF - timedelta(days=5)}, as_of=AS_OF)
        assert row["active_within_30"] is True

    def test_a_definite_trailing_use_settles_the_window_despite_an_ambiguity(
            self):
        """`active_within_30` is the SAME disjunction as a mark: a definite use
        in the trailing window settles it `True`, and an ambiguous day there
        cannot un-make it. Regression: the window check consulted the
        ambiguity first, so a provably-warm team read `None` and was dropped
        from the headline trailing denominator."""
        start = AS_OF - timedelta(days=40)
        row = ri.team_row(org_id="o", start=start.isoformat(),
                          state=ri.STATE_MEASURED, reason=None,
                          active_days={AS_OF - timedelta(days=5)},
                          ambiguous_days={AS_OF - timedelta(days=3)},
                          as_of=AS_OF)
        assert row["active_within_30"] is True

    def test_an_unsettled_trailing_window_is_none_not_false(self):
        start = AS_OF - timedelta(days=40)
        row = ri.team_row(org_id="o", start=start.isoformat(),
                          state=ri.STATE_MEASURED, reason=None,
                          active_days=set(),
                          ambiguous_days={AS_OF - timedelta(days=3)},
                          as_of=AS_OF)
        assert row["active_within_30"] is None

    def test_the_90_day_count_stays_none_under_ambiguity(self):
        """`active_days_90` is a COUNT, not a disjunction: an ambiguous day in
        its window changes the number, so it stays `None` even when another
        day in the window is a definite use."""
        start = AS_OF - timedelta(days=40)
        row = ri.team_row(org_id="o", start=start.isoformat(),
                          state=ri.STATE_MEASURED, reason=None,
                          active_days={AS_OF - timedelta(days=5)},
                          ambiguous_days={AS_OF - timedelta(days=3)},
                          as_of=AS_OF)
        assert row["active_days_90"] is None


# ══ pure: the cohort fold ══════════════════════════════════════════════════

def _row(org: str, start: date, *, state: str = ri.STATE_MEASURED,
         active: set[date] | None = None, ambiguous: set[date] | None = None,
         reason: str | None = None) -> dict:
    return ri.team_row(org_id=org, start=start.isoformat(), state=state,
                       reason=reason, active_days=active,
                       ambiguous_days=ambiguous or set(), as_of=AS_OF)


class TestCohortSummary:
    def test_the_denominator_includes_inactive_teams(self):
        start = AS_OF - timedelta(days=40)
        rows = [_row("a", start, active={start + timedelta(days=10)}),
                _row("b", start, active=set())]
        cohort = ri.cohort_summary(rows, as_of=AS_OF)
        assert cohort["marks"]["7"]["n"] == 2
        assert cohort["marks"]["7"]["k"] == 1
        assert cohort["marks"]["7"]["state"] == ri.STATE_MEASURED

    def test_a_team_younger_than_the_mark_is_excluded(self):
        rows = [_row("a", AS_OF - timedelta(days=40), active=set()),
                _row("b", AS_OF - timedelta(days=3), active=set())]
        cohort = ri.cohort_summary(rows, as_of=AS_OF)
        assert cohort["marks"]["7"]["n"] == 1
        assert cohort["marks"]["60"]["state"] == ri.STATE_NOT_MEASURABLE

    def test_a_measured_zero_is_emitted_when_nothing_is_active(self):
        rows = [_row("a", AS_OF - timedelta(days=40), active=set())]
        cell = ri.cohort_summary(rows, as_of=AS_OF)["marks"]["7"]
        assert cell["state"] == ri.STATE_MEASURED
        assert (cell["k"], cell["n"], cell["rate"]) == (0, 1, 0.0)

    def test_an_unmeasurable_team_makes_the_cell_partial_and_is_counted(self):
        start = AS_OF - timedelta(days=40)
        rows = [_row("a", start, active={start + timedelta(days=10)}),
                _row("b", start, state=ri.STATE_UNAVAILABLE, reason="boom")]
        cell = ri.cohort_summary(rows, as_of=AS_OF)["marks"]["7"]
        assert cell["state"] == "partial"
        assert cell["n"] == 1                      # the measured subset only
        assert cell["unmeasured_teams"] == 1        # and the omission is visible

    def test_an_ambiguous_mark_is_excluded_and_counted(self):
        start = AS_OF - timedelta(days=40)
        rows = [_row("a", start, active={start + timedelta(days=10)}),
                _row("b", start, active=set(),
                     ambiguous={start + timedelta(days=10)})]
        cell = ri.cohort_summary(rows, as_of=AS_OF)["marks"]["7"]
        assert cell["state"] == "partial"
        assert cell["n"] == 1

    def test_a_definite_use_offsets_an_ambiguous_day_in_the_same_window(self):
        """Regression for the P1 review finding, at the cohort level: a genuine
        use at day 20 settles the day-7 / day-15 marks even with an ambiguous
        day-10 row, so the team stays in the mark denominators AND in the
        band cells (which need BOTH marks non-``None``)."""
        start = AS_OF - timedelta(days=40)
        rows = [_row("a", start, active={start + timedelta(days=20)},
                     ambiguous={start + timedelta(days=10)})]
        cohort = ri.cohort_summary(rows, as_of=AS_OF)
        cell = cohort["marks"]["7"]
        assert cell["state"] == ri.STATE_MEASURED
        assert (cell["k"], cell["n"], cell["unmeasured_teams"]) == (1, 1, 0)
        band = cohort["bands"]["7->15"]
        assert band["state"] == ri.STATE_MEASURED
        assert (band["left_active"], band["right_active"]) == (1, 1)

    def test_bands_report_the_drop(self):
        start = AS_OF - timedelta(days=40)
        rows = [_row("a", start, active={start + timedelta(days=8)}),
                _row("b", start, active={start + timedelta(days=20)})]
        band = ri.cohort_summary(rows, as_of=AS_OF)["bands"]["7->15"]
        assert band["left_active"] == 2   # both active on/after day 7
        assert band["right_active"] == 1  # only b on/after day 15
        assert band["drop"] == 1

    def test_trailing_30_counts_only_recent_activity(self):
        start = AS_OF - timedelta(days=200)
        rows = [_row("a", start, active={AS_OF - timedelta(days=5)}),
                _row("b", start, active={AS_OF - timedelta(days=100)})]
        cell = ri.cohort_summary(rows, as_of=AS_OF)["trailing_30_active"]
        assert (cell["k"], cell["n"]) == (1, 2)

    def test_a_definite_trailing_use_stays_in_the_trailing_denominator(self):
        """Regression at the cohort level: a team with a definite use in the
        trailing window must stay in the trailing denominator even when an
        ambiguous day sits there too — otherwise a provably-warm team is
        dropped from the headline number."""
        start = AS_OF - timedelta(days=40)
        rows = [_row("a", start, active={AS_OF - timedelta(days=5)},
                     ambiguous={AS_OF - timedelta(days=3)})]
        cell = ri.cohort_summary(rows, as_of=AS_OF)["trailing_30_active"]
        assert cell["state"] == ri.STATE_MEASURED
        assert (cell["k"], cell["n"], cell["unmeasured_teams"]) == (1, 1, 0)
        assert cell["reason"] is None

    def test_an_unsettled_trailing_window_is_excluded_and_counted(self):
        """The over-fix guard: with no definite trailing use, an ambiguous day
        still leaves the window unclassifiable and the team unmeasured."""
        start = AS_OF - timedelta(days=40)
        rows = [_row("a", start, active=set(),
                     ambiguous={AS_OF - timedelta(days=3)})]
        cell = ri.cohort_summary(rows, as_of=AS_OF)["trailing_30_active"]
        assert cell["state"] == ri.STATE_UNAVAILABLE
        assert cell["unmeasured_teams"] == 1

    def test_a_fully_measured_trailing_cell_does_not_claim_a_refusal(self):
        """``reason`` is a refusal label. Emitting it on a fully measured cell
        says the number is partial when it is not — the inverse of the module's
        "the honest states are enforced, not narrated" rule."""
        start = AS_OF - timedelta(days=40)
        rows = [_row("a", start, active={AS_OF - timedelta(days=5)})]
        cell = ri.cohort_summary(rows, as_of=AS_OF)["trailing_30_active"]
        assert cell["state"] == ri.STATE_MEASURED
        assert cell["unmeasured_teams"] == 0
        assert cell["reason"] is None

    def test_a_partial_trailing_cell_still_names_the_refusal(self):
        """The fix must not silence the label where it is TRUE."""
        start = AS_OF - timedelta(days=40)
        rows = [_row("a", start, active={AS_OF - timedelta(days=5)}),
                _row("b", start, state=ri.STATE_UNAVAILABLE, reason="boom")]
        cell = ri.cohort_summary(rows, as_of=AS_OF)["trailing_30_active"]
        assert cell["state"] == "partial"
        assert cell["unmeasured_teams"] == 1
        assert cell["reason"] == "some_teams_are_unmeasurable"

    def test_a_clean_histogram_reads_measured(self):
        start = AS_OF - timedelta(days=200)
        rows = [_row("a", start, active={AS_OF - timedelta(days=1)})]
        hist = ri.cohort_summary(rows, as_of=AS_OF)["active_days_histogram"]
        assert hist["state"] == ri.STATE_MEASURED
        assert hist["unmeasured_teams"] == 0

    def test_histogram_reads_partial_when_a_team_is_unmeasured(self):
        """The histogram is built from the same population rule as the
        trailing cell and `n`/`buckets` already exclude the missing team, so
        ``state`` is the only signal a consumer switching on it has. It was
        the one cohort sub-surface that could never read ``partial``."""
        start = AS_OF - timedelta(days=200)
        rows = [_row("a", start, active={AS_OF - timedelta(days=1)}),
                _row("b", start, state=ri.STATE_UNAVAILABLE, reason="boom")]
        hist = ri.cohort_summary(rows, as_of=AS_OF)["active_days_histogram"]
        assert hist["unmeasured_teams"] == 1
        assert hist["state"] == "partial"
        assert hist["n"] == 1

    def test_histogram_reads_partial_when_a_window_carries_an_ambiguity(self):
        """The other half of the same condition: a measured team whose 90-day
        window has an ambiguous day has no ``active_days_90``, so it is in
        ``unmeasured_teams`` and must partial the histogram too."""
        start = AS_OF - timedelta(days=200)
        rows = [_row("a", start, active={AS_OF - timedelta(days=1)}),
                _row("b", start, active=set(),
                     ambiguous={AS_OF - timedelta(days=3)})]
        hist = ri.cohort_summary(rows, as_of=AS_OF)["active_days_histogram"]
        assert hist["unmeasured_teams"] == 1
        assert hist["state"] == "partial"
        assert hist["n"] == 1

    def test_a_zero_eligible_histogram_with_a_team_behind_it_is_unavailable(
            self):
        """The zero-denominator branch must mirror `_cell`: a surface with no
        measurable team but an unmeasured one behind it is `unavailable`, not
        `not_measurable`, so a consumer switching on `state` gets the same
        answer from the histogram and the trailing cell for one population."""
        start = AS_OF - timedelta(days=40)
        rows = [_row("a", start, active=set(),
                     ambiguous={AS_OF - timedelta(days=3)})]
        cohort = ri.cohort_summary(rows, as_of=AS_OF)
        assert cohort["active_days_histogram"]["n"] is None
        assert (cohort["active_days_histogram"]["state"]
                == cohort["trailing_30_active"]["state"]
                == ri.STATE_UNAVAILABLE)
    def test_histogram_buckets_separate_cadences(self):
        start = AS_OF - timedelta(days=200)
        daily = {AS_OF - timedelta(days=i) for i in range(12)}
        occasional = {AS_OF - timedelta(days=i) for i in (1, 40)}
        rows = [_row("daily", start, active=daily),
                _row("occasional", start, active=occasional),
                _row("gone", start, active=set())]
        hist = ri.cohort_summary(rows, as_of=AS_OF)["active_days_histogram"]
        assert hist["buckets"]["10+"] == 1
        assert hist["buckets"]["1-2"] == 1
        assert hist["buckets"]["0"] == 1
        assert hist["n"] == 3


class TestBuildTeamRows:
    def test_a_failed_read_makes_only_that_team_unavailable(self):
        teams = [{"org_id": "a", "start": AS_OF - timedelta(days=40)},
                 {"org_id": "b", "start": AS_OF - timedelta(days=40)}]
        activity = {
            "a": ({AS_OF - timedelta(days=10)}, set(), None),
            "b": (None, set(), ri.REASON_ANALYTICS_UNREACHABLE),
        }
        rows = ri.build_team_rows(teams=teams, activity=activity, as_of=AS_OF)
        by_id = {r["org_id"]: r for r in rows}
        assert by_id["a"]["state"] == ri.STATE_MEASURED
        assert by_id["b"]["state"] == ri.STATE_UNAVAILABLE
        assert by_id["b"]["reason"] == ri.REASON_ANALYTICS_UNREACHABLE

    def test_an_ambiguous_day_does_not_make_the_team_unavailable(self):
        """The ambiguity rule is PER MARK: an unclassifiable day leaves the
        team `measured` (with the reason named on its row) and only the marks
        it leaves unsettled read `null`. The module docstring and LIMITATIONS
        used to claim the team was reported `unavailable` — which the code has
        never done, and which would drop its sound marks from the denominator."""
        start = AS_OF - timedelta(days=40)
        rows = ri.build_team_rows(
            teams=[{"org_id": "a", "start": start}],
            activity={"a": ({start + timedelta(days=20)},
                             {start + timedelta(days=10)}, None)},
            as_of=AS_OF)
        row = rows[0]
        assert row["state"] == ri.STATE_MEASURED
        assert row["reason"] == ri.REASON_UNCLASSIFIABLE_USE_DAY
        assert row["active_at"]["7"] is True      # settled by the day-20 use
        assert row["unclassifiable_marks"] == []


class TestAssemble:
    def test_a_surface_failure_emits_no_cohort_at_all(self):
        payload = ri.assemble(as_of=AS_OF, team_rows=[],
                              surface_state=ri.STATE_UNAVAILABLE,
                              surface_reason=ri.REASON_WRITE_PATH_UNCONFIGURED)
        assert payload["state"] == ri.STATE_UNAVAILABLE
        assert payload["cohort"] is None
        assert payload["reason"] == ri.REASON_WRITE_PATH_UNCONFIGURED

    def test_the_definition_and_its_hash_ride_with_the_number(self):
        payload = ri.assemble(as_of=AS_OF, team_rows=[])
        assert payload["definition"]["marks"] == list(ri.RETENTION_MARKS)
        assert payload["definition"]["churn_reasons"] == list(ri.CHURN_REASONS)
        assert len(payload["definition_hash"]) == 16


class TestAggregationOnlyGuard:
    """Binding guard D12-b: #3839 must not smuggle a data-retention window in
    through the metric. The module is READ-ONLY over existing rows and defines
    no window after which anything is deleted, pruned or purged."""

    def test_the_module_carries_no_write_or_prune_surface(self):
        import inspect
        src = inspect.getsource(ri)
        for verb in ('method="DELETE"', 'method="POST"', 'method="PATCH"'):
            assert verb not in src, verb
        for name in dir(ri):
            assert not name.lower().startswith(
                ("prune", "purge", "delete", "expire", "sweep")), name

    def test_the_guard_is_stated_on_the_payload(self):
        assert ri.AGGREGATION_ONLY is True
        payload = ri.assemble(as_of=AS_OF, team_rows=[])
        assert payload["definition"]["aggregation_only"] is True
        assert any("D12-b" in line for line in payload["limitations"])


# ══ readers (fake control plane) ═══════════════════════════════════════════

class FakeCP:
    """Serves `organizations` and `analytics_events` with server-side filtering
    and a completeness flag, mirroring the ``SupabaseControlPlane.query_all``
    contract the readers depend on."""

    def __init__(self, orgs, events, *, fleet_complete=True,
                 activity_complete=True):
        self.orgs = orgs
        self.events = events
        self.fleet_complete = fleet_complete
        self.activity_complete = activity_complete
        self.calls: list[tuple[str, list]] = []

    def query_all(self, table, *, select=None, filters=None, cursor="id",
                  page_size=1000, max_pages=100):
        fs = list(filters or [])
        self.calls.append((table, fs))
        if table == "organizations":
            rows = [{"id": o["id"], "created_at": o.get("created_at")}
                    for o in self.orgs if o.get("deleted_at") is None]
            return rows, self.fleet_complete
        out = []
        for row in self.events:
            ok = True
            for col, op, value in fs:
                cell = row.get(col)
                matches = (
                    (op == "eq" and cell == value)
                    or (op == "gte" and str(cell) >= str(value))
                    or (op == "lt" and str(cell) < str(value))
                )
                if not matches:
                    ok = False
            if ok:
                out.append(row)
        return out, self.activity_complete


class TestReadTeamStarts:
    def test_reads_id_and_created_at_into_dates(self):
        cp = FakeCP([{"id": "org-a", "created_at": _iso(AS_OF - timedelta(days=5))}],
                    [])
        teams, complete, reason = ri.read_team_starts(cp)
        assert complete is True and reason is None
        assert teams == [{"org_id": "org-a", "start": AS_OF - timedelta(days=5)}]

    def test_an_uncertified_walk_is_not_a_denominator(self):
        cp = FakeCP([{"id": "org-a", "created_at": _iso(AS_OF - timedelta(days=5))}],
                    [], fleet_complete=False)
        _teams, complete, _reason = ri.read_team_starts(cp)
        assert complete is False

    def test_an_unparseable_start_refuses_rather_than_shrinking_the_cohort(self):
        cp = FakeCP([{"id": "org-a", "created_at": "garbage"}], [])
        teams, complete, reason = ri.read_team_starts(cp)
        assert teams is None and complete is False
        assert reason == ri.REASON_START_UNREADABLE

    def test_a_read_failure_is_a_reason_not_an_exception(self):
        class Boom:
            def query_all(self, *a, **k):
                raise RuntimeError("store down")

        teams, complete, reason = ri.read_team_starts(Boom())
        assert teams is None and complete is False
        assert reason == ri.REASON_FLEET_FAILED


class TestReadOrgActivity:
    def test_folds_both_legs_and_ignores_deleted_rows(self):
        start = AS_OF - timedelta(days=40)
        events = [_capture(start + timedelta(days=10)),
                  _search(start + timedelta(days=20), 2)]
        cp = FakeCP([], events)
        active, ambiguous, reason = ri.read_org_activity(
            cp, "org-a", since_day=start, until_day=AS_OF)
        assert reason is None and ambiguous == set()
        assert {d.isoformat() for d in active} == {
            (start + timedelta(days=10)).isoformat(),
            (start + timedelta(days=20)).isoformat()}

    def test_a_truncated_walk_is_a_refusal_not_a_count(self):
        start = AS_OF - timedelta(days=40)
        cp = FakeCP([], [_capture(start + timedelta(days=10))],
                    activity_complete=False)
        active, _amb, reason = ri.read_org_activity(
            cp, "org-a", since_day=start, until_day=AS_OF)
        assert active is None
        assert reason == ri.REASON_ANALYTICS_TRUNCATED

    def test_an_unreachable_store_is_a_reason(self):
        class Boom:
            def query_all(self, *a, **k):
                raise RuntimeError("store down")

        active, _amb, reason = ri.read_org_activity(
            Boom(), "org-a", since_day=AS_OF - timedelta(days=40), until_day=AS_OF)
        assert active is None
        assert reason == ri.REASON_ANALYTICS_UNREACHABLE


# ══ the operator endpoint ══════════════════════════════════════════════════

PATH = "/v1/internal/retention/cohort"
_INTERNAL_KEY = "test-internal-retention-secret"
INTERNAL_HEADERS = {"Authorization": f"Bearer {_INTERNAL_KEY}"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "retention.db")
    monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
    with patched_tortoise_sdk(db_path), TestClient(app) as tc:
        yield tc


@pytest.fixture
def internal_key(monkeypatch):
    monkeypatch.setenv("FASTAPI_INTERNAL_KEY", _INTERNAL_KEY)
    return dict(INTERNAL_HEADERS)


def _wire(monkeypatch, cp, *, write_path=True):
    import tortoise.activation_scorecard as ac
    import tortoise.supabase_control as sc

    monkeypatch.setattr(ac, "analytics_write_path_configured",
                        lambda: write_path)
    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)
    monkeypatch.setattr(sc, "get_control_plane", lambda: cp)
    return cp


class TestEndpointAuth:
    def test_unconfigured_server_fails_closed(self, client, monkeypatch):
        monkeypatch.delenv("FASTAPI_INTERNAL_KEY", raising=False)
        assert client.get(PATH).status_code == 503

    def test_requires_the_operator_credential(self, client, monkeypatch):
        monkeypatch.setenv("FASTAPI_INTERNAL_KEY", _INTERNAL_KEY)
        assert client.get(PATH).status_code == 401
        assert client.get(
            PATH, headers={"Authorization": "Bearer tt_" + "A" * 40}
        ).status_code == 401
        assert client.get(
            PATH, headers={"Authorization": "Bearer nope"}
        ).status_code == 401

    def test_a_future_reading_is_refused(self, client, internal_key, monkeypatch):
        _wire(monkeypatch, FakeCP([], []))
        future = (AS_OF + timedelta(days=1)).isoformat()
        resp = client.get(PATH, params={"as_of": future}, headers=internal_key)
        assert resp.status_code == 422
        assert "future" in resp.json()["detail"]

    def test_a_malformed_reading_is_refused(self, client, internal_key, monkeypatch):
        _wire(monkeypatch, FakeCP([], []))
        resp = client.get(PATH, params={"as_of": "yesterday"}, headers=internal_key)
        assert resp.status_code == 422


class TestEndpointAvailability:
    def test_a_dark_write_path_is_unavailable_not_a_fleet_of_zeros(
            self, client, internal_key, monkeypatch):
        _wire(monkeypatch, FakeCP([], []), write_path=False)
        resp = client.get(PATH, params={"as_of": AS_OF.isoformat()},
                          headers=internal_key)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["state"] == ri.STATE_UNAVAILABLE
        assert body["reason"] == ri.REASON_WRITE_PATH_UNCONFIGURED
        assert body["cohort"] is None

    def test_an_uncertified_fleet_is_unavailable_not_a_partial_rate(
            self, client, internal_key, monkeypatch):
        cp = FakeCP([{"id": "org-a", "created_at": _iso(AS_OF - timedelta(days=40))}],
                    [], fleet_complete=False)
        _wire(monkeypatch, cp)
        body = client.get(PATH, params={"as_of": AS_OF.isoformat()},
                          headers=internal_key).json()
        assert body["state"] == ri.STATE_UNAVAILABLE
        assert body["reason"] == ri.REASON_FLEET_INCOMPLETE
        assert body["cohort"] is None

    def test_a_fleet_over_the_bound_is_refused_not_truncated(
            self, client, internal_key, monkeypatch):
        import tortoise.hosted_api as ha
        monkeypatch.setattr(ha, "_RETENTION_MAX_ORGS", 1)
        cp = FakeCP([{"id": "org-a", "created_at": _iso(AS_OF - timedelta(days=40))},
                     {"id": "org-b", "created_at": _iso(AS_OF - timedelta(days=40))}],
                    [])
        _wire(monkeypatch, cp)
        resp = client.get(PATH, params={"as_of": AS_OF.isoformat()},
                          headers=internal_key)
        assert resp.status_code == 422
        assert "blast-radius" in resp.json()["detail"]


class TestEndpointReading:
    def test_the_happy_path_returns_the_table_and_the_cohort(
            self, client, internal_key, monkeypatch):
        start = AS_OF - timedelta(days=40)
        cp = FakeCP(
            [{"id": "org-a", "created_at": _iso(start)},
             {"id": "org-b", "created_at": _iso(start)},
             {"id": "org-new", "created_at": _iso(AS_OF - timedelta(days=3))}],
            [_capture(start + timedelta(days=10), org="org-a"),
             _search(start + timedelta(days=20), 1, org="org-a")],
        )
        _wire(monkeypatch, cp)
        body = client.get(PATH, params={"as_of": AS_OF.isoformat()},
                          headers=internal_key).json()
        assert body["state"] == ri.STATE_MEASURED
        by_id = {t["org_id"]: t for t in body["teams"]}
        assert by_id["org-a"]["active_at"]["7"] is True
        assert by_id["org-b"]["active_at"]["7"] is False
        assert by_id["org-new"]["active_at"]["7"] is None   # too young, not False
        assert body["cohort"]["marks"]["7"]["k"] == 1
        assert body["cohort"]["marks"]["7"]["n"] == 2
        assert body["cohort"]["marks"]["60"]["state"] == ri.STATE_NOT_MEASURABLE

    def test_an_unreadable_org_names_itself_and_partials_the_cell(
            self, client, internal_key, monkeypatch):
        start = AS_OF - timedelta(days=40)

        class FlakyCP(FakeCP):
            def query_all(self, table, **kw):
                if table == "analytics_events" and kw.get("filters") and any(
                        f[2] == "org-b" for f in kw["filters"]):
                    raise RuntimeError("store down")
                return super().query_all(table, **kw)

        cp = FlakyCP(
            [{"id": "org-a", "created_at": _iso(start)},
             {"id": "org-b", "created_at": _iso(start)}],
            [_capture(start + timedelta(days=10), org="org-a")],
        )
        _wire(monkeypatch, cp)
        body = client.get(PATH, params={"as_of": AS_OF.isoformat()},
                          headers=internal_key).json()
        by_id = {t["org_id"]: t for t in body["teams"]}
        assert by_id["org-b"]["state"] == ri.STATE_UNAVAILABLE
        cell = body["cohort"]["marks"]["7"]
        assert cell["state"] == "partial"
        assert cell["unmeasured_teams"] == 1
        assert any("org-b" in note for note in body["notes"])

    def test_the_endpoint_audits_one_operator_read(
            self, client, internal_key, monkeypatch):
        import tortoise.hosted_api as ha
        start = AS_OF - timedelta(days=40)
        recorded: list[dict] = []

        async def _spy(request, org_id, operation, **kw):
            recorded.append({"org_id": org_id, "operation": operation, **kw})

        monkeypatch.setattr(ha, "_async_audit", _spy)
        _wire(monkeypatch, FakeCP(
            [{"id": "org-a", "created_at": _iso(start)}], []))
        client.get(PATH, params={"as_of": AS_OF.isoformat()},
                   headers=internal_key)
        assert len(recorded) == 1
        assert recorded[0]["operation"] == "retention_cohort_read"
        assert recorded[0]["org_id"] == ""
        assert recorded[0]["detail"]["teams_total"] == 1


class TestRetentionReadsDoNotShareTheDefaultExecutor:
    """#3060 doctrine: long or stallable blocking work must NOT ride the loop's
    shared default executor (it carries ~80 other `to_thread` sites and the auth
    middleware's abuse hooks). The fleet walk and each per-org walk are
    paginated control-plane HTTP round trips, so both go to the dedicated
    `_RETENTION_EXECUTOR` — the `_SCORECARD_EXECUTOR` precedent. The pool SIZE
    is also what bounds live threads, since cancelling the await does not stop
    the worker (CPython #87185)."""

    def test_the_fleet_and_per_org_reads_use_the_dedicated_pool(
            self, client, internal_key, monkeypatch):
        import tortoise.hosted_api as ha
        start = AS_OF - timedelta(days=40)
        seen: list[object] = []
        real = ha._run_off_loop

        async def _spy(executor, fn, /, *args, **kwargs):
            seen.append(executor)
            return await real(executor, fn, *args, **kwargs)

        monkeypatch.setattr(ha, "_run_off_loop", _spy)
        _wire(monkeypatch, FakeCP(
            [{"id": "org-a", "created_at": _iso(start)},
             {"id": "org-b", "created_at": _iso(start)}],
            [_capture(start + timedelta(days=10), org="org-a")]))
        resp = client.get(PATH, params={"as_of": AS_OF.isoformat()},
                          headers=internal_key)
        assert resp.status_code == 200, resp.text
        # 1 fleet read + one read per org; every one on the dedicated pool.
        assert len(seen) == 3
        assert all(e is ha._RETENTION_EXECUTOR for e in seen)
        assert ha._RETENTION_EXECUTOR is not ha._SCORECARD_EXECUTOR

    def test_the_worker_count_clamp_is_pinned_by_explicit_inputs(self):
        """Pin the CLAMP, not the production expression re-evaluated from the
        same env read (which is a tautology): the pool is built at import time,
        so this is the only way to detect a changed floor/ceiling."""
        import tortoise.hosted_api as ha
        assert ha._retention_worker_count(None, 4) == 4    # unset -> default
        assert ha._retention_worker_count("", 4) == 4      # empty -> default
        assert ha._retention_worker_count("abc", 4) == 4   # non-numeric -> default
        assert ha._retention_worker_count("3", 4) == 3
        assert ha._retention_worker_count("0", 4) == 1     # floor
        assert ha._retention_worker_count("99", 4) == 8    # ceiling
        assert ha._retention_worker_count("4", 2) == 4     # raw wins over default

    def test_the_pool_and_the_semaphore_read_the_same_bound(self):
        """A semaphore wider than the pool would let a read burn its whole
        timeout queued, then report a self-inflicted `org_read_timeout`."""
        import tortoise.hosted_api as ha
        assert ha._RETENTION_EXECUTOR._max_workers == ha._RETENTION_WORKERS
        assert ha._RETENTION_WORKERS >= 1
        assert ha._RETENTION_WORKERS <= 8
