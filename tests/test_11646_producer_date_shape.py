"""#11646 — the ingest producer's date shape must be READ, never silently dropped.

The fixture is the producer's verbatim output, not a hand-built lookalike. The
cached LongMemEval corpus
(``~/.cache/tortoise-longmemeval/longmemeval_s_cleaned.json``) carries
``question_date='2023/05/30 (Tue) 23:40'`` and
``haystack_dates[0]='2023/05/20 (Sat) 02:21'`` — 500/500 questions and every
haystack date in that shape, 0 ISO (re-measured for #11646; #3011 measured the
same shape 23,867/23,867 on the frozen dataset). ``tools/longmem_eval/ingest.py``
writes exactly that string into ``createdAt`` (:246), ``when``/``validFrom``
(:260-261) and the session's ``startedAt`` (:300), so these are the values the
three seams actually meet in a graph.

Before #11646 all three seams assumed ISO:

* ``assembly._norm_date`` returned ``None`` → every eval-ingested spine row was
  UNDATED (undated sorts LAST on the chronological spine) and the as-of
  exclusion was skipped entirely (``if dq is not None``);
* ``time_aware._as_date`` returned ``None`` → ``resolve_question_date`` fell
  through to ``datetime.now(UTC)``, so a PRESENT question date was replaced by
  *today* in the shipped valid-time selection;
* ``time_aware._as_iso_date`` returned ``None`` → ``inject_query_date`` was a
  silent no-op, so the C6 dense anchor never anchored on the real corpus.

Every test below asserts the producer shape AND its ISO control, so a future
parser change cannot satisfy one by breaking the other.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from tortoise import time_aware
from tortoise.assembly import (
    AssemblyShape,
    SubjectCandidate,
    _norm_date,
    _tier_and_date,
    collect_slices,
)
from tortoise.calendar_date import (
    UNDATED_SENTINEL_DATE,
    calendar_date,
    calendar_datetime,
)
from tortoise.coverage_loop import _calendar_date
from tortoise.search_engine import _created_sort_key
from tortoise.validity import END_KNOWN, END_UNKNOWN, _names_an_instant, end_state

#: VERBATIM producer output (see the module docstring) — never a hand-built
#: lookalike: the weekday label and the unpadded day are part of the shape.
PRODUCER_QUESTION_DATE = "2023/05/30 (Tue) 23:40"
PRODUCER_SESSION_DATE = "2023/05/20 (Sat) 02:21"
#: The same two instants in ISO, as CONTROLS: the union must not change them.
ISO_QUESTION_DATE = "2023-05-30"
ISO_SESSION_DATE = "2023-05-20"


# ── seam 1: the assembly spine (R2 ladder + as-of boundary) ────────────────


def test_assembly_ladder_dates_the_producer_shape():
    """A producer-shaped ``when`` is tier ``when`` with its calendar day —
    not ``('undated', None)`` — and the ISO control is unchanged."""
    assert _norm_date(PRODUCER_SESSION_DATE) == date(2023, 5, 20)
    assert _norm_date(ISO_SESSION_DATE) == date(2023, 5, 20)
    assert _tier_and_date({"when": PRODUCER_SESSION_DATE}) == (
        "when", date(2023, 5, 20))
    assert _tier_and_date({"when": ISO_SESSION_DATE}) == (
        "when", date(2023, 5, 20))


def test_assembly_ladder_falls_through_to_a_producer_shaped_created_at():
    """The ladder's parse-FALL-THROUGH must now reach a producer-shaped
    ``createdAt`` instead of stopping at tier ``undated``."""
    assert _tier_and_date({"when": "TBD",
                           "created_at": PRODUCER_SESSION_DATE}) == (
        "created", date(2023, 5, 20))
    assert _tier_and_date({"when": None,
                           "created_at": PRODUCER_SESSION_DATE}) == (
        "created", date(2023, 5, 20))


def test_assembly_norm_date_keeps_its_undated_and_utc_contracts():
    """The union must not loosen the negatives: the v2 sentinel, garbage and
    pre-1970 artifacts stay UNDATED; an aware instant still truncates in UTC
    (never the host zone)."""
    assert _norm_date("1970-01-01T00:00:00Z") is None
    assert _norm_date(UNDATED_SENTINEL_DATE) is None
    assert _norm_date("not-a-date") is None
    assert _norm_date("") is None
    assert _norm_date(None) is None
    assert _norm_date("2026-06-10T23:30:00Z") == date(2026, 6, 10)
    assert _norm_date("2026-09-01T02:00:00+02:00") == date(2026, 9, 1)


class _Port:
    """Minimal WalkerPort stub — the collector's own contract, no docker."""

    def __init__(self, rows):
        self._rows = rows

    def state_rows(self, object_ids):
        return []

    def spine_rows(self, object_ids, per_subject_cap=200):
        return [dict(r) for r in self._rows]

    def day_rows(self, days, per_day_cap=200):
        return []


def _cand(oid: str, idx: int) -> SubjectCandidate:
    return SubjectCandidate(subject_index=idx, object_id=oid, name=oid,
                            confidence="high", source="exact")


def test_assembly_as_of_window_excludes_producer_dated_rows_after_the_question():
    """The as-of boundary the issue measured as SKIPPED: with a producer-shaped
    ``question_date``, a row dated after it is excluded — before #11646
    ``_norm_date`` returned ``None`` and ``if dq is not None`` skipped the whole
    filter, admitting every future row."""
    port = _Port([
        {"object_id": "obj-eval", "kind": "point", "id": "p-before",
         "content": "before the question", "when": "2023/05/20 (Sat) 02:21",
         "created_at": "2023/05/20 (Sat) 02:21", "status": "live"},
        {"object_id": "obj-eval", "kind": "point", "id": "p-after",
         "content": "after the question", "when": "2023/06/02 (Fri) 09:00",
         "created_at": "2023/06/02 (Fri) 09:00", "status": "live"},
        {"object_id": "obj-eval", "kind": "point", "id": "p-undated",
         "content": "no date at all", "when": None, "created_at": None,
         "status": "live"},
    ])
    slices = collect_slices(port, [_cand("obj-eval", 0)],
                            shape=AssemblyShape.ORDERING,
                            question_date=PRODUCER_QUESTION_DATE)
    ids = [r["id"] for r in slices.timeline_rows]
    assert ids == ["p-before", "p-undated"], (
        "the producer-dated row after the question must be excluded and the "
        "undated row kept undated-LAST")
    assert slices.timeline_rows[0]["date"] == date(2023, 5, 20)

    # ISO control: the same cut, now that the date parses either way
    iso_slices = collect_slices(port, [_cand("obj-eval", 0)],
                                shape=AssemblyShape.ORDERING,
                                question_date=ISO_QUESTION_DATE)
    assert [r["id"] for r in iso_slices.timeline_rows] == ids


def test_assembly_spine_orders_producer_dates_chronologically():
    """Dating the rows also restores the CHRONOLOGICAL spine: producer-shaped
    rows used to sort to ``_date.max`` (last) regardless of their date."""
    port = _Port([
        {"object_id": "o", "kind": "point", "id": "p-late",
         "content": "later", "when": "2023/05/25 (Thu) 10:00",
         "created_at": None, "status": "live"},
        {"object_id": "o", "kind": "point", "id": "p-early",
         "content": "earlier", "when": "2023/05/20 (Sat) 02:21",
         "created_at": None, "status": "live"},
    ])
    rows = collect_slices(port, [_cand("o", 0)],
                          shape=AssemblyShape.ORDERING).timeline_rows
    assert [r["id"] for r in rows] == ["p-early", "p-late"]


# ── seam 2: the shipped valid-time selection ───────────────────────────────


def test_time_aware_as_date_reads_the_producer_shape():
    assert time_aware._as_date(PRODUCER_SESSION_DATE) == ISO_SESSION_DATE
    assert time_aware._as_date(ISO_SESSION_DATE) == ISO_SESSION_DATE
    # the pre-existing numeric-epoch readings are unchanged
    assert time_aware._as_date(1684549260) == "2023-05-20"
    assert time_aware._as_date("1900000000") == "2030-03-17"
    assert time_aware._as_date("not-a-date") is None


def test_resolve_question_date_never_substitutes_the_wall_clock_for_a_present_date():
    """The defect the issue measured: a present producer-shaped question date
    was replaced by TODAY. It must resolve to its own date, and the documented
    wall-clock fallback must still apply only when NO date is supplied."""
    resolved = time_aware.resolve_question_date(PRODUCER_QUESTION_DATE)
    assert resolved == ISO_QUESTION_DATE
    assert resolved != datetime.now(tz=None).date().isoformat()
    assert time_aware.resolve_question_date(ISO_QUESTION_DATE) == ISO_QUESTION_DATE
    # absent date → today (the documented contract, unchanged by #11646)
    assert time_aware.resolve_question_date(None) == \
        datetime.now(UTC).date().isoformat()


def test_valid_time_selection_orders_by_the_questions_own_date():
    """End to end through the shipped predicate: a fact whose window closed
    before the QUESTION's date is stale, and one still open after it is not —
    both decided at the question's date, which is the whole point of the seam.
    Before #11646 the producer-shaped question date fell through to today."""
    closed = {"valid_to": "2023/05/01 (Mon) 10:00", "status": "live"}
    still_open = {"valid_to": "2023/06/15 (Thu) 10:00", "status": "live"}
    qd = time_aware.resolve_question_date(PRODUCER_QUESTION_DATE)
    assert qd == ISO_QUESTION_DATE
    assert time_aware.is_stale_entry(closed, question_date=qd)
    assert not time_aware.is_stale_entry(still_open, question_date=qd)


# ── seam 3: the C6 dense-query anchor ──────────────────────────────────────


def test_c6_anchor_injects_the_producer_question_date():
    """``_as_iso_date`` is the anchor's whole parse: ``None`` made
    ``inject_query_date`` a silent no-op on the real corpus."""
    assert time_aware._as_iso_date(PRODUCER_QUESTION_DATE) == ISO_QUESTION_DATE
    assert time_aware._as_iso_date(ISO_QUESTION_DATE) == ISO_QUESTION_DATE
    assert time_aware._as_iso_date("") is None
    assert time_aware._as_iso_date(None) is None

    anchored = time_aware.inject_query_date("where do I live now?", PRODUCER_QUESTION_DATE)
    assert anchored != "where do I live now?"
    assert ISO_QUESTION_DATE in anchored
    # idempotent, and the ISO control produces the same anchor
    assert time_aware.inject_query_date(anchored, PRODUCER_QUESTION_DATE) == anchored
    assert time_aware.inject_query_date("where do I live now?", ISO_QUESTION_DATE) == anchored

    query = "where do I live now?"
    assert time_aware.dense_query_for(
        query, time_aware=True, query_date=PRODUCER_QUESTION_DATE) == anchored
    assert time_aware.dense_query_for(
        query, time_aware=True, query_date=ISO_QUESTION_DATE) == anchored


# ── the same root at the two other readers of a stored date ────────────────


def test_created_sort_key_and_names_an_instant_read_the_producer_shape():
    """``validity._names_an_instant`` is defined as ``_created_sort_key``'s
    bucket 0, so a stored producer-shaped window end read as naming NO instant
    (``END_UNKNOWN`` — "ended, at a time we cannot pin"). It now orders."""
    assert _created_sort_key(PRODUCER_SESSION_DATE)[0] == 0
    assert _names_an_instant(PRODUCER_SESSION_DATE) is True
    assert end_state(PRODUCER_SESSION_DATE, None) == END_KNOWN
    # ordered by REAL instant, between the two ISO controls (same day ± time)
    assert _created_sort_key("2023-05-20T00:00:00Z") < \
        _created_sort_key(PRODUCER_SESSION_DATE) < \
        _created_sort_key(PRODUCER_SESSION_DATE.replace("02:21", "09:00"))
    # ISO controls unchanged (date-only = UTC midnight, #3982)
    assert _created_sort_key(ISO_SESSION_DATE) == \
        (0, datetime(2023, 5, 20, tzinfo=UTC).timestamp())


def test_the_epoch_spelled_as_a_string_still_names_no_instant():
    """The #9202 ruling is preserved: widening the grammar must not date a
    numeric STRING, which would be a fourth notion of "parseable"."""
    assert _created_sort_key("1900000000") == (1, "1900000000")
    assert _names_an_instant("1900000000") is False
    assert end_state("1900000000", None) == END_UNKNOWN


def test_coverage_loop_calendar_date_is_routed_through_the_shared_resolver():
    """The already-working seam keeps working, now from ONE grammar."""
    assert _calendar_date(PRODUCER_SESSION_DATE) == ISO_SESSION_DATE
    assert _calendar_date(ISO_SESSION_DATE) == ISO_SESSION_DATE
    assert _calendar_date("2023/4/9") == "2023-04-09"
    assert _calendar_date("1970-01-01T00:00:00Z") is None


# ── the shared resolver itself: the union, and the no-coercion decision ────


@pytest.mark.parametrize("value,expected", [
    (PRODUCER_SESSION_DATE, date(2023, 5, 20)),
    ("2023/05/20", date(2023, 5, 20)),
    ("2023/4/9", date(2023, 4, 9)),
    (ISO_SESSION_DATE, date(2023, 5, 20)),
    ("2023-4-9", date(2023, 4, 9)),
    ("2023-05-20T02:21:00Z", date(2023, 5, 20)),
    ("20261020", date(2026, 10, 20)),          # compact (assembly's union)
    ("2026-W43-2", date(2026, 10, 20)),        # ISO week (assembly's union)
    (1684549260, date(2023, 5, 20)),           # numeric epoch
    ("1684549260", date(2023, 5, 20)),         # epoch spelled as a string
    ("not-a-date", None),
    ("2026-13-45", None),                      # out of range
    ("2023/04/105", None),                     # 3-digit day is not a day
    (None, None),
    ("", None),
])
def test_calendar_date_union_of_shapes(value, expected):
    assert calendar_date(value) == expected


def test_month_granularity_is_never_coerced_to_the_first_of_the_month():
    """THE DELIBERATE DECISION: ``YYYY/MM`` has no day, so it is UNDATED.
    Coercing it to the 1st would invent an ordering position inside the month
    (ahead of every row dated within it) for a shape no producer emits."""
    for seam in (calendar_date, _norm_date, _calendar_date,
                 time_aware._as_date, time_aware._as_iso_date):
        assert seam("2023/05") is None, seam
        assert seam("2026-09") is None, seam


def test_calendar_date_utc_truncation_is_opt_in():
    """The two day semantics the callers need, stated: written day by default
    (coverage_loop / _iso_date10), UTC truncation for the assembly boundary."""
    offset_instant = "2026-09-01T00:30:00+02:00"
    assert calendar_date(offset_instant) == date(2026, 9, 1)
    assert calendar_date(offset_instant, utc_truncate=True) == date(2026, 8, 31)
    # a date-only value has no instant to truncate
    assert calendar_date("2026-09-01", utc_truncate=True) == date(2026, 9, 1)


def test_calendar_datetime_anchoring_matches_the_ordered_measure():
    """Date-only → UTC midnight; a zone-less date-TIME stays NAIVE (#3982);
    a stated offset is applied."""
    assert calendar_datetime("2023/05/20").isoformat() == \
        "2023-05-20T00:00:00+00:00"
    assert calendar_datetime(PRODUCER_SESSION_DATE).tzinfo is None
    assert calendar_datetime("2023-05-20T02:21:00+09:00").utcoffset() == \
        timedelta(hours=9)
    # an epoch STRING names no instant (#9202); the NUMBER does
    assert calendar_datetime("1900000000") is None
    assert calendar_datetime(1900000000).date() == date(2030, 3, 17)


def test_write_door_still_canonicalises_a_date_variant():
    """The WRITE door's canonicalisation of a date-field variant must survive
    the widened read measure: ``2024/03/01`` now orders, so it reaches the door
    as "already an instant" — the stored form must still be the canonical
    dashed/padded one (#9202 review P3), while a genuine instant is stored
    byte-identical."""
    from tortoise.validity import read_caller_end

    assert read_caller_end("2024/03/01") == ("2024-03-01", None)
    assert read_caller_end("2024-3-1") == ("2024-03-01", None)
    assert read_caller_end("2024/3/1T12:00:00") == ("2024-03-01T12:00:00", None)
    # real instants: accepted, never re-spelled
    assert read_caller_end(PRODUCER_SESSION_DATE) == (PRODUCER_SESSION_DATE, None)
    assert read_caller_end("2024-06-01T12:00:00+02:00") == (
        "2024-06-01T12:00:00+02:00", None)
    # a variant whose TIME is unreadable is still refused, never half-shaped
    with pytest.raises(ValueError):
        read_caller_end("2024/3/1Txx")
