"""#11646 — the ingest producer's date shape must be READ, never silently dropped.

The fixture is the producer's verbatim output, not a hand-built lookalike. The
cached LongMemEval corpus
(``~/.cache/tortoise-longmemeval/longmemeval_s_cleaned.json``) carries
``question_date='2023/05/30 (Tue) 23:40'`` and
``haystack_dates[0]='2023/05/20 (Sat) 02:21'`` — 500/500 questions and every
haystack date in that shape, 0 ISO (re-measured for #11646; #3011 measured the
same shape 23,867/23,867 on the frozen dataset). The two ingest lanes write
exactly that string: ``tools/longmem_eval/ingest.py`` into ``createdAt`` (:246)
and the session's ``startedAt``/``endedAt`` (:300), and
``tools/longmem_eval/ingest_v2.py`` into ``when``/``validFrom`` (:260-261) — so
these are the values the three seams actually meet in a graph.

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
    rows used to sort to ``_date.max`` (last) regardless of their date.

    The ids are chosen so the ID tiebreak and the DATE order DISAGREE
    (``a-late`` sorts first by id, last by date). With the ids in the opposite
    order this test would pass even if no row were dated at all — the spine's
    tiebreak is ``(date, object_id, id)`` — so it would not pin the regression
    it is named for (review cycle 1, finding 4)."""
    port = _Port([
        {"object_id": "o", "kind": "point", "id": "a-late",
         "content": "later", "when": "2023/05/25 (Thu) 10:00",
         "created_at": None, "status": "live"},
        {"object_id": "o", "kind": "point", "id": "z-early",
         "content": "earlier", "when": "2023/05/20 (Sat) 02:21",
         "created_at": None, "status": "live"},
    ])
    rows = collect_slices(port, [_cand("o", 0)],
                          shape=AssemblyShape.ORDERING).timeline_rows
    assert [r["id"] for r in rows] == ["z-early", "a-late"]
    assert [r["date"] for r in rows] == [date(2023, 5, 20), date(2023, 5, 25)]


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
    # Ordered WITHIN the producer shape only: that value is zone-less, so it is
    # read NAIVELY (the #3982 reading) and comparing it against a UTC-anchored
    # value would make the assertion a property of the HOST zone, not the value
    # (it fails at TZ=Asia/Kolkata — review cycle 1, finding 3).
    assert _created_sort_key("2023/05/20 (Sat) 00:00") < \
        _created_sort_key(PRODUCER_SESSION_DATE) < \
        _created_sort_key("2023/05/20 (Sat) 09:00")
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
    """The already-working seam keeps working, now from ONE grammar.

    ⛔ The first four assertions are NOT the falsifier of the routing, and a
    review measured that: ``origin/main``'s local ``_CALENDAR_DATE_RE``
    (``^\\d{4}[-/]\\d{1,2}[-/]\\d{1,2}(?!\\d)``) plus its sentinel strip return
    the SAME four answers, so reverting ``coverage_loop._calendar_date`` to its
    own copy left this test green while the seam it names was un-routed. The
    last two assertions are the falsifier: a compact date and a numeric epoch
    are shapes ONLY the shared resolver dates, so they red on that revert.
    """
    assert _calendar_date(PRODUCER_SESSION_DATE) == ISO_SESSION_DATE
    assert _calendar_date(ISO_SESSION_DATE) == ISO_SESSION_DATE
    assert _calendar_date("2023/4/9") == "2023-04-09"
    assert _calendar_date("1970-01-01T00:00:00Z") is None
    # ⛔ the routing falsifier — unparseable by the pre-#11646 local copy
    assert _calendar_date("2026-W43-2") == "2026-10-20"   # ISO week
    assert _calendar_date(1684549260) == "2023-05-20"     # numeric epoch


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


@pytest.mark.parametrize("value,written", [
    ("0001-01-01T00:00:00+23:59", date(1, 1, 1)),     # UTC day is year 0
    ("9999-12-31T23:59:59-23:59", date(9999, 12, 31)),  # UTC day is year 10000
])
def test_an_unconvertible_edge_answers_with_the_stated_day_never_an_exception(
        value, written):
    """``calendar_date`` promises "never an exception", and the two
    ``utc_truncate=True`` consumer chains it feeds (``assembly._norm_date``,
    ``assembly.collect_slices``) each promise never to raise on a malformed
    stored date. A legal instant at the ``datetime`` range boundary carrying a
    real offset converts to a day OUTSIDE ``date``'s range, where ``astimezone``
    raises ``OverflowError`` (measured — review findings, two cycles).

    ``extractor_v2._valid_iso_date`` accepts both spellings
    (``^\\d{4}-\\d{2}-\\d{2}([Tt ].*)?$``, <= 40 chars), so a stored ``when``
    really can be one, and assembly calls ``_norm_date`` UNGUARDED per row.

    The answer is the day the value STATES (:func:`_utc_day`'s ``fallback``),
    not ``None``: a year-1/9999 instant is a value whose day is knowable, and
    undating it would drop a row ``origin/main`` kept. The point of the test is
    that it is an ANSWER, never a raised exception out of an assembly read —
    the pre-fix code raised here on both spellings.
    """
    assert calendar_date(value, utc_truncate=True) == written
    # every consumer inherits the answer instead of the raise; the assembly
    # ladder then applies its own pre-1970 floor / keeps the day
    assert _norm_date(value) == (None if written <= date(1970, 1, 1) else written)
    assert time_aware._as_date(value) is not None
    assert _calendar_date(value) is not None


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
    # MIXED separators are ordered by the widened read measure, so they reach
    # this door too and must be re-spelled rather than stored un-canonicalised
    # (cycle 4, finding 3 — on ``origin/main`` they were refused outright)
    assert read_caller_end("2024/03-01") == ("2024-03-01", None)
    assert read_caller_end("2024-03/01") == ("2024-03-01", None)
    # ⛔ a variant carrying ONLY AN OFFSET (no ``T``/space time part) used to
    # escape this door entirely: ``rest`` was ``[T ]\S*``, so ``2024/3/1+05:00``
    # returned None here, took the widened ``_names_an_instant``
    # "already an instant" path, and was stored VERBATIM — unpadded and
    # slash-spelled, the exact spelling #9202 review P3 settled against
    # (measured: ('2024/3/1+05:00', None)). It must re-spell like any variant.
    assert read_caller_end("2024/3/1+05:00") == ("2024-03-01+05:00", None)
    assert read_caller_end("2024/03/01+05:00") == ("2024-03-01+05:00", None)
    assert read_caller_end("2024/3/1-05:00") == ("2024-03-01-05:00", None)
    # ⛔ the offset arm must be as WIDE as ``_INSTANT_RE``'s, or ONE defect class
    # gets THREE outcomes at the door: a bare ``±HH`` and ``Z`` tail used to fall
    # past this helper and be stored verbatim (or, for ``2024/3/1Z``, refused) —
    # measured `('2024/3/1+05', None)`, `('2023/05/20Z', None)`, ValueError.
    assert read_caller_end("2024/3/1+05") == ("2024-03-01+05", None)
    assert read_caller_end("2023/05/20Z") == ("2023-05-20Z", None)
    assert read_caller_end("2023/05/20+05") == ("2023-05-20+05", None)
    assert read_caller_end("2024/3/1Z") == ("2024-03-01Z", None)
    # …and an offset that is not a real one is REFUSED, not re-spelled
    with pytest.raises(ValueError):
        read_caller_end("2024/3/1+25:00")
    # real instants: accepted, never re-spelled
    assert read_caller_end(PRODUCER_SESSION_DATE) == (PRODUCER_SESSION_DATE, None)
    assert read_caller_end("2024-06-01T12:00:00+02:00") == (
        "2024-06-01T12:00:00+02:00", None)
    # a variant whose TIME is unreadable is still refused, never half-shaped
    with pytest.raises(ValueError):
        read_caller_end("2024/3/1Txx")


def test_a_stated_offset_that_is_not_real_names_no_instant():
    """A STATED offset that is not a real one (``+25:00``) leaves the instant
    UNKNOWABLE — it does not make the value zone-less.

    ``_match_offset`` answers ``None`` for both "no offset stated" and "offset
    stated and refused", so without an explicit guard a bad offset fell through
    to the naive reading and ``_names_an_instant`` answered ``True`` — a false
    PASS that reads ``END_KNOWN`` for a bound whose instant cannot be computed,
    the reading #9202 exists to deny (measured before the guard:
    ``_names_an_instant('2023/05/20T02:00:00+25:00') is True``).
    """
    for bad in ("2023/05/20T02:00:00+25:00",
                "2023/05/20T02:00:00-25:00",
                "2023/05/20T02:00:00+00:99",
                "2023-05-20+25:00"):
        assert calendar_datetime(bad) is None, bad
        assert _names_an_instant(bad) is False, bad
        assert end_state(bad, None) == END_UNKNOWN, bad
    # …while a real offset, no offset, and Z keep working (no over-blocking)
    assert calendar_datetime("2023/05/20T02:00:00+05:00").utcoffset() == \
        timedelta(hours=5)
    assert calendar_datetime("2023/05/20T02:00:00Z").utcoffset() == timedelta(0)
    assert calendar_datetime("2023/05/20").utcoffset() == timedelta(0)
    assert _names_an_instant("2023/05/20T02:00:00+05:00") is True


def test_the_dashed_offset_arm_is_delegated_and_that_divergence_is_recorded():
    """The guard above lives in THIS module's offset parser, which
    ``_created_sort_key`` reaches only for a SLASH spelling (its ``"/" in s``
    arm). A DASHED value goes straight to ``datetime.fromisoformat``, whose
    offset parser NORMALISES an impossible one rather than refusing it:

        datetime.fromisoformat("2023-05-20T02:00:00+00:99")
            -> 2023-05-20 02:00:00+01:39

    so ``calendar_datetime`` answers ``None`` while ``_created_sort_key``
    happily orders it and ``_names_an_instant`` reads ``END_KNOWN`` — two
    notions of "names an instant" for one string.

    ⛔ This is PRE-EXISTING dashed behaviour (`origin/main` did the same via
    ``datetime.fromisoformat``) and it is deliberately NOT fixed here: routing
    the dashed arm through this resolver would change what the recorded #9202
    ruling reads for a shape that ruling already covers. It is PINNED so the
    divergence is visible and a later lane inherits it as a decision rather
    than rediscovering it as a bug — see the residual recorded on #11646.
    """
    # the slash spelling is refused by this module's own parser
    assert calendar_datetime("2023/05/20T02:00:00+00:99") is None
    assert _names_an_instant("2023/05/20T02:00:00+00:99") is False
    # the dashed spelling is normalised by the stdlib, so it still names an
    # instant — the pre-existing divergence, pinned rather than papered over
    assert calendar_datetime("2023-05-20T02:00:00+00:99") is None
    assert _names_an_instant("2023-05-20T02:00:00+00:99") is True
    assert _created_sort_key("2023-05-20T02:00:00+00:99")[0] == 0


# ── UNION parity: no value that parsed before may stop parsing ─────────────
#
# These are the reviewer's cycle-1 counterexamples (finding 1), all VERIFIED
# against `origin/main`'s three parsers: every one of them read a calendar-date
# PREFIX (or was as tolerant as ``datetime.fromisoformat``), so a strictly
# anchored whole-value grammar would have turned a REAL date silently undated —
# the exact defect #11646 exists to remove, reintroduced through another input.


@pytest.mark.parametrize("value,written,utc_day", [
    # (value, the WRITTEN calendar day, the day the assembly boundary sees)
    ("2023-05-20 02:21 PDT", "2023-05-20", "2023-05-20"),   # zone abbreviation
    ("2023/05/20 (Sat) 02:21 (approx)", "2023-05-20", "2023-05-20"),
    ("2023-05-20T+05:00", "2023-05-20", "2023-05-20"),      # offset, no time
    ("2023-05-20T02", "2023-05-20", "2023-05-20"),          # hour-only
    ("2023-05-20T02:21:00.1234567", "2023-05-20", "2023-05-20"),
    ("2023-05-20T02:21:00,123", "2023-05-20", "2023-05-20"),  # comma decimal
    ("2023-05-20T02:00+05", "2023-05-20", "2023-05-19"),    # +HH offset
    ("2023-05-20T02:21:00+05", "2023-05-20", "2023-05-19"),
    ("20230520T02:21:00Z", "2023-05-20", "2023-05-20"),     # compact + time
    ("2023-W43-2T11:00:00", "2023-10-24", "2023-10-24"),    # ISO week + time
    # compact / ISO-week date with an OFFSET and no time — a family
    # `tests/test_3982_date_only_utc.py` pins as REAL stored data
    ("20260610-05:00", "2026-06-10", "2026-06-10"),
    ("2026W243-05:00", "2026-06-10", "2026-06-10"),
    ("20230520-0500", "2023-05-20", "2023-05-20"),
    ("2023-W43-2+05:00", "2023-10-24", "2023-10-24"),
    # BASIC-format (colon-less) time — `fromisoformat` reads the mixed form, so
    # the assembly boundary must keep truncating it in UTC (cycle 4, finding 1)
    ("2023-05-05T0221+05", "2023-05-05", "2023-05-04"),
    ("2023-05-05T022100+05", "2023-05-05", "2023-05-04"),
    ("2023-05-05 0221+05", "2023-05-05", "2023-05-04"),
])
def test_a_value_that_parsed_before_still_names_its_day(value, written, utc_day):
    """Every one of these read a calendar-date PREFIX (or was as tolerant as
    ``datetime.fromisoformat``) before #11646, so a strictly anchored
    whole-value grammar made a REAL date silently undated — the defect the issue
    exists to remove, on another input (review cycles 1, 2 and 3).

    NOT included, deliberately: values whose leading calendar date is ambiguous
    because the old ``s[:10]`` + ``fromisoformat`` path FABRICATED a day from a
    three-digit day or a trailing number (``2023-05-072``, ``20230520.3``).
    Those stay undated — that is finding 2's class, and "restoring the union"
    for them would re-add the fabrication.
    """
    for name, seam in (("calendar_date", calendar_date),
                       ("_as_date", time_aware._as_date),
                       ("_calendar_date", _calendar_date),
                       ("_as_iso_date", time_aware._as_iso_date)):
        got = seam(value)
        assert got is not None, f"{name} lost a value it used to parse: {value!r}"
        assert str(got).startswith(written), f"{name}({value!r}) -> {got!r}"
    # the assembly boundary is the ONE seam that truncates an aware instant in
    # UTC; it must reach the grammar (not a written-day fallback) to do so
    assert _norm_date(value) == date.fromisoformat(utc_day), value


def test_a_pure_number_is_never_truncated_into_a_fabricated_day():
    """Review cycle 1, finding 2: an unanchored ``\\d{8}`` alternative read the
    first eight digits of a 9/10-digit EPOCH spelling and invented a day
    (``2020010112`` → 2020-01-01). The digit count decides, deterministically:

    * exactly 8 → a compact DATE (``20230520``);
    * 9-10 → a SECONDS epoch;
    * 11+ → undated (the millisecond shape ``validity`` refuses at the write
      door, #10079 — never a year-8380 date).

    ⛔ THIS IS THE UNION'S ONE STATED EXCEPTION, and the direction it cuts is
    not stated by "fourteen years early" alone. ``origin/main``'s
    ``assembly._norm_date`` DATES ``2020010112`` at 2020-01-01 (it takes the
    leading eight digits), so this reading replaces a dated row with a
    future-dated one — and ``assembly.collect_slices`` EXCLUDES a dated row after
    the question while RETAINING an undated one. Measured against
    ``origin/main``'s own body: ``2023052011`` main 2023-05-20 / here 2034-02-08
    (excluded), ``202305201122`` main 2023-05-20 / here ``None`` (retained, sorts
    last). The rule below is DELIBERATE and recorded in the module docstring;
    reversing it is a reopen, not a fix.
    """
    assert calendar_date("20230520") == date(2023, 5, 20)
    assert calendar_date("2020010112") == date(2034, 1, 4)
    assert calendar_date("202001011") == date(1976, 5, 26)
    assert calendar_date("202305201112") is None
    assert calendar_date("1719792000000") is None
    # the SIGN is symmetric (cycle 4, finding 2): ``_as_date`` read a signed
    # epoch through ``float()``, so a leading ``+`` must not undate the value
    # while ``-`` stays dated
    assert calendar_date("+1684549260") == date(2023, 5, 20)
    assert calendar_date("-1684549260") == date(1916, 8, 14)
    assert time_aware._as_date("+1684549260") == "2023-05-20"
    # and no seam fabricates a day for them either
    assert time_aware._as_date("202305201112") is None
    assert _calendar_date("202305201112") is None
    # ⛔ THE ASSEMBLY CONSEQUENCE, PINNED AT THE SEAM THAT SUFFERS IT. The
    # module docstring states the exception; these two lines make it impossible
    # for the date-reading direction to drift again without a red test. A
    # 10-digit ``YYYYMMDDHH`` is dated 14 years LATE here, and ``_norm_date`` is
    # both the as-of boundary and the chronological spine key, so such a row is
    # EXCLUDED from a historical as-of window — the failure mode #11646 exists
    # to remove, on another input. Reachability is external only (no in-repo
    # producer emits the shape), which is why the rule is kept rather than
    # reopened; it is pinned so the behaviour is visible, never rediscovered.
    assert _norm_date("2023052011") == date(2034, 2, 8)
    assert _norm_date("202305201122") is None


def test_every_shape_that_used_to_parse_still_parses_at_the_assembly_boundary():
    """The assembly boundary is the ONE caller that truncates an aware instant
    in UTC, so the offset spellings must reach the grammar (not a prefix
    fallback that would silently keep the written day) — review cycle 2,
    finding 1. ``datetime.fromisoformat`` accepts ``+05`` and ``+00:00:30``;
    both must land on the UTC day, as they did before #11646."""
    assert _norm_date("2023-05-20T02:00+05") == date(2023, 5, 19)
    assert _norm_date("20230520T02:21:00+05") == date(2023, 5, 19)
    # …while the written-day seams keep the written day
    assert calendar_date("2023-05-20T02:00+05") == date(2023, 5, 20)
    assert time_aware._as_date("2023-05-20T02:00+05") == "2023-05-20"
    # a compact / ISO-week date carrying a TIME keeps its day too (review
    # cycle 2, finding 2): fromisoformat reads these whole, so they must not
    # become UNDATED on the spine
    assert _norm_date("20230520T02:21:00Z") == date(2023, 5, 20)
    assert _norm_date("2023-W43-2T11:00:00") == date(2023, 10, 24)
    assert time_aware._as_date("2023-W43-2T11:00:00") == "2023-10-24"


def test_an_unrepresentable_instant_does_not_raise_out_of_a_read_path():
    """Assembly calls ``_norm_date`` UNGUARDED inside its per-row loop and at
    ten question-date sites, and both it and this resolver promise never to
    raise — review cycle 3, finding 2. A stated offset at ``date.max``/``min``
    can push the UTC conversion out of range; the day the value STATES is the
    honest answer (and the pre-#11646 parser returned ``None`` for these, never
    an exception)."""
    for value in ("9999-12-31 (Sat) 23:30-05:00",     # producer shape, max date
                  "9999-12-31T23:30:00-05:00",        # ISO form
                  "0001-01-01 (Sat) 02:21+05:00"):    # min date
        for seam in (calendar_date, _norm_date, time_aware._as_date,
                     _calendar_date, _created_sort_key):
            seam(value)  # must not raise
    # ⛔ ``_created_sort_key`` is the one that actually regressed (cycle 5,
    # finding 1): a naive instant outside the platform range made
    # ``.timestamp()`` raise out of the read MEASURE, which
    # ``validity._names_an_instant`` → ``end_state`` / ``read_caller_end`` and
    # ``support.sort`` all call. It must bucket, never raise — and a slashed
    # value is the shape the widened grammar newly admits there. The BUCKET is
    # asserted only where every host agrees (year 1 is unrepresentable
    # everywhere); near year 9999 the answer is legitimately host-dependent, so
    # there the property is the absence of an exception.
    assert _created_sort_key("0001/01/01 00:00") == (1, "0001/01/01 00:00")
    assert _created_sort_key("9999/12/31 23:59")[0] in (0, 1)
    # and the assembly ladder falls through / floors rather than aborting
    assert _norm_date("9999-12-31 (Sat) 23:30-05:00") == date(9999, 12, 31)
    assert _norm_date("0001-01-01 (Sat) 02:21+05:00") is None   # pre-1970 floor
    assert _tier_and_date({"when": "9999-12-31 (Sat) 23:30-05:00"}) == (
        "when", date(9999, 12, 31))


def test_the_reader_never_receives_a_full_timestamp_for_a_day_precision_bound():
    """Cycle 5, finding 2. ``validFrom`` is written in the producer's shape by
    ``tools/longmem_eval/ingest_v2.py``; once #11646 made such a bound
    ORDERABLE, ``retrieval._display_bound`` rendered the whole string —
    ``[valid since 2023/05/20 (Sat) 02:21]`` — beside ``precision='day'``, which
    the marker's contract forbids ("no full timestamps in the reader context").
    The bound is rendered as its DAY, through the same truncation rule."""
    from tortoise.retrieval import _display_bound, _validity_marker

    hit = {"id": "p-1"}
    assert _display_bound(PRODUCER_SESSION_DATE, "valid_from", hit) == (
        ISO_SESSION_DATE)
    assert _validity_marker({"valid_from": PRODUCER_SESSION_DATE}) == (
        "[valid since 2023-05-20]")
    # an ISO instant is unchanged (the arm that already owned this rule)
    assert _display_bound("2024-06-01T12:00:00Z", "valid_from", hit) == (
        "2024-06-01")
    # text that names no day survives whole, never as a fragment
    assert _display_bound("not-a-date-at-all", "valid_from", hit) == (
        "not-a-date-at-all")


def test_an_offset_with_fractional_seconds_reaches_the_utc_truncation():
    """Cycle 6, finding 1. ``datetime.fromisoformat`` accepts an offset whose
    SECONDS carry a fraction (``+05:00:00.5``), so the old grammar fell to the
    prefix reading and stated the WRITTEN day at the assembly boundary while
    ``retrieval._display_bound`` stated the UTC day — one stored instant, two
    days, decided by which seam read it. The offset grammar now carries its own
    fraction and the truncation applies again."""
    value = "2023-05-20T02:21:00+05:00:00.5"
    assert _norm_date(value) == date(2023, 5, 19)      # UTC truncation
    assert calendar_date(value) == date(2023, 5, 20)   # the written day
    assert calendar_datetime(value) is not None
    # and the reader is told the same day the spine used, not the other one
    from tortoise.retrieval import _display_bound
    assert _display_bound(value, "valid_from", {"id": "p"}) == "2023-05-19"


def test_the_instant_view_accepts_every_shape_the_day_view_dates():
    """Cycle 6, finding 3. ``calendar_datetime`` is documented as the view an
    ORDERING consumer wants, so a family the day view dates must not be
    un-orderable there — otherwise a later consumer silently drops rows this
    module says are dated."""
    assert calendar_date("20230520T02:21:00Z") == date(2023, 5, 20)
    assert calendar_datetime("20230520T02:21:00Z") is not None
    assert calendar_date("2023-W43-2T11:00:00") == date(2023, 10, 24)
    assert calendar_datetime("2023-W43-2T11:00:00") is not None
    # a stated-but-impossible offset is still refused, in BOTH views
    assert calendar_datetime("2023/05/20T02:00:00+25:00") is None
