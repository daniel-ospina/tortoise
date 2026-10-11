"""#2655 — pure calendar-anchor resolution (``tortoise/calendar_anchors.py``).

Pure: no DB, no model, no clock, no I/O. The module never reads a clock — the
caller passes ``as_of`` (the question's own ``question_date``) — and every
property pinned here is a SOURCE property of the resolution rules, not a
snapshot of today's date.

The two contract properties this file exists to hold:

1. **As-of discipline** — a resolved day is NEVER after ``as_of`` (swept over a
   year of ``as_of`` values for every anchor family, not spot-checked).
2. **Abstain, never guess** — no ``as_of``, no anchor, more than one distinct
   anchor, an unrepresentable date, or an explicit FUTURE year all return
   ``None``.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from tortoise.calendar_anchors import (
    FIXED_DATE_HOLIDAYS,
    CalendarAnchorKind,
    find_anchor_spans,
    resolve_calendar_anchor,
)

# ── the holiday table is a reviewed literal, so it is pinned ───────────────

def test_fixed_date_table_is_well_formed():
    """Every table row must be a valid calendar (month, day) — a typo like
    (2, 30) would silently abstain at resolve time and be invisible."""
    assert FIXED_DATE_HOLIDAYS
    for alias, (month, day) in FIXED_DATE_HOLIDAYS.items():
        assert alias == alias.lower(), alias
        assert 1 <= month <= 12, alias
        # validate the pair against a real calendar (2023 is a non-leap year,
        # so a Feb-29 row is caught here too)
        date(2023, month, day)


@pytest.mark.parametrize("alias,expected_md", [
    ("valentine's day", (2, 14)),
    ("valentines day", (2, 14)),
    ("valentine's", (2, 14)),
    ("christmas", (12, 25)),
    ("christmas day", (12, 25)),
    ("christmas eve", (12, 24)),
    ("new year's day", (1, 1)),
    ("new year's eve", (12, 31)),
    ("halloween", (10, 31)),
    ("independence day", (7, 4)),
    ("the fourth of july", (7, 4)),
    ("fourth of july", (7, 4)),
    ("st. patrick's day", (3, 17)),
    ("april fools' day", (4, 1)),
    ("veterans day", (11, 11)),
])
def test_fixed_date_aliases(alias, expected_md):
    assert FIXED_DATE_HOLIDAYS[alias] == expected_md


# ── fixed-date holidays + the as-of year rule ──────────────────────────────

@pytest.mark.parametrize("question,as_of,expected", [
    # the census row (verbatim) — a past occurrence in the SAME year
    ("What was the airline that I flied with on Valentine's day?",
     date(2023, 2, 20), date(2023, 2, 14)),
    # on the day itself: today IS the anchor
    ("What did I do on Valentine's day?", date(2023, 2, 14),
     date(2023, 2, 14)),
    # the holiday is still ahead this year -> roll back one year (never future)
    ("What did I do on Valentine's day?", date(2023, 1, 5), date(2022, 2, 14)),
    ("What did I do on Christmas?", date(2023, 3, 1), date(2022, 12, 25)),
    ("What did I do on Christmas?", date(2023, 12, 26), date(2023, 12, 25)),
    ("What did I do on New Year's eve?", date(2024, 1, 1), date(2023, 12, 31)),
    ("What did I do on Halloween?", date(2023, 10, 31), date(2023, 10, 31)),
    ("What did I do on the fourth of july?", date(2023, 8, 1),
     date(2023, 7, 4)),
])
def test_fixed_date_rollback(question, as_of, expected):
    got = resolve_calendar_anchor(question, as_of)
    assert got is not None
    assert got.kind is CalendarAnchorKind.FIXED_DATE
    assert got.days == (expected,)


def test_explicit_year_is_honoured_and_future_years_abstain():
    got = resolve_calendar_anchor("What did I do on Christmas 2021?",
                                  date(2023, 12, 26))
    assert got is not None and got.days == (date(2021, 12, 25),)
    # an explicit FUTURE year is the question's own claim — honoured or
    # refused, NEVER silently rolled back to a year the question did not name
    assert resolve_calendar_anchor("What did I do on Christmas 2027?",
                                   date(2023, 12, 26)) is None


# ── weekday offsets ────────────────────────────────────────────────────────

@pytest.mark.parametrize("anchor_wd,as_of,expected", [
    # Monday → the immediately preceding Saturday
    ("saturday", date(2023, 6, 12), date(2023, 6, 10)),
    # asked ON a Saturday: "last Saturday" is the PREVIOUS one (never today)
    ("saturday", date(2023, 6, 10), date(2023, 6, 3)),
    ("sunday", date(2023, 6, 11), date(2023, 6, 4)),
    ("tuesday", date(2023, 9, 10), date(2023, 9, 5)),
    ("friday", date(2023, 6, 12), date(2023, 6, 9)),
    ("monday", date(2023, 6, 12), date(2023, 6, 5)),
])
def test_weekday_strictly_before(anchor_wd, as_of, expected):
    got = resolve_calendar_anchor(f"What did I do last {anchor_wd}?", as_of)
    assert got is not None
    assert got.kind is CalendarAnchorKind.WEEKDAY
    assert got.days == (expected,)


@pytest.mark.parametrize("surface", [
    "last Tuesday", "this past Tuesday", "past Tuesday",
])
def test_weekday_preposition_families(surface):
    got = resolve_calendar_anchor(f"What did I do {surface}?",
                                  date(2026, 9, 10))
    assert got is not None and got.days == (date(2026, 9, 8),)


def test_bare_weekday_is_not_an_anchor():
    """Which Tuesday a bare name denotes is not recoverable from the text —
    a guess would fire the lane on a wrong day."""
    assert find_anchor_spans("What did I do on Tuesday?") == ()
    assert resolve_calendar_anchor("What did I do on Tuesday?",
                                   date(2023, 6, 12)) is None


# ── the weekend window ─────────────────────────────────────────────────────

@pytest.mark.parametrize("as_of,expected", [
    # Monday: the weekend that just ended (Sat + Sun, both in the past)
    (date(2023, 6, 12), (date(2023, 6, 10), date(2023, 6, 11))),
    # Saturday: the Sunday is still ahead → NOT included (never a future day)
    (date(2023, 6, 10), (date(2023, 6, 10),)),
    # Sunday: the weekend the question is standing in (yesterday + today)
    (date(2023, 6, 11), (date(2023, 6, 10), date(2023, 6, 11))),
    (date(2023, 6, 13), (date(2023, 6, 10), date(2023, 6, 11))),
])
def test_weekend_window(as_of, expected):
    got = resolve_calendar_anchor("Which bike did I fix the past weekend?",
                                  as_of)
    assert got is not None
    assert got.kind is CalendarAnchorKind.WEEKEND
    assert got.days == expected


def test_census_weekend_row_resolves():
    """The census row, verbatim (``gpt4_e414231f``)."""
    got = resolve_calendar_anchor(
        "Which bike did I fixed or serviced the past weekend?",
        date(2023, 5, 24))
    assert got is not None
    assert got.days == (date(2023, 5, 20), date(2023, 5, 21))


# ── the as-of property, swept ──────────────────────────────────────────────

def test_no_resolved_day_is_ever_after_as_of():
    """Sweep a year of ``as_of`` values over every anchor family: the day the
    lane would look up is never in the future (that would answer a different
    question than the one asked)."""
    questions = [
        "What did I do on Valentine's day?",
        "What did I do on Christmas?",
        "What did I do last Monday?",
        "What did I do last Sunday?",
        "What did I do the past weekend?",
    ]
    start = date(2023, 1, 1)
    for offset in range(365):
        as_of = start + timedelta(days=offset)
        for q in questions:
            got = resolve_calendar_anchor(q, as_of)
            assert got is not None, (q, as_of)
            assert all(d <= as_of for d in got.days), (q, as_of, got.days)


# ── abstention ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("question", [
    "",
    "   ",
    "What did I do yesterday?",
    "What did I do two weeks ago?",
    "Which airline did I fly with the most in March and April?",
    "What was the date on which I attended the first BBQ event in June?",
    "What is the current status of the couch?",
])
def test_no_anchor_abstains(question):
    assert resolve_calendar_anchor(question, date(2023, 6, 12)) is None


def test_multiple_distinct_anchors_abstain():
    """An anchor SET is not a day lookup — "between Christmas and New Year's
    day" is an interval and must not be answered as a single day."""
    assert resolve_calendar_anchor(
        "What did I do between Christmas and New Year's day?",
        date(2023, 12, 30)) is None
    assert resolve_calendar_anchor(
        "What did I do last Saturday and last Sunday?", date(2023, 6, 12)
    ) is None


def test_repeated_same_anchor_is_not_ambiguous():
    got = resolve_calendar_anchor(
        "What did I do last Saturday, and what did I do last Saturday?",
        date(2023, 6, 12))
    assert got is not None and got.days == (date(2023, 6, 10),)


@pytest.mark.parametrize("as_of", [None, "", "2023-06-12", 20230612])
def test_missing_or_garbage_as_of_abstains(as_of):
    assert resolve_calendar_anchor("What did I do last Saturday?", as_of) is None


def test_word_boundary_guard():
    """An alias inside a longer word is not an anchor."""
    assert find_anchor_spans("prehalloween party on halloweenish night") == ()
    assert find_anchor_spans("the last saturdayish mood") == ()


def test_span_offsets_point_at_the_surface():
    q = "What did I do last Saturday?"
    spans = find_anchor_spans(q)
    assert len(spans) == 1
    span = spans[0]
    assert q[span.start:span.end] == "last Saturday"
    assert span.label == "last saturday"
    assert span.kind is CalendarAnchorKind.WEEKDAY


def test_resolved_anchor_carries_the_named_surface():
    got = resolve_calendar_anchor(
        "What was the airline that I flied with on Valentine's day?",
        date(2023, 2, 20))
    assert got is not None
    assert got.surface == "Valentine's day"
    assert got.label == "valentine's day"
    assert got.day == date(2023, 2, 14)
