"""#12322 — ``latest_readings`` orders by the INSTANT, never by the stored bytes.

The defect this file proves (measured at ``cd22562c24``, before the fix): the
read cut its page with

    ORDER BY coalesce(e.startedAt, e.createdAt, '') DESC, e.id DESC LIMIT $lim

i.e. it selected WHICH readings a judgement is computed from using the very
string comparison this module documents as wrong ~400 lines away
(``instant_at_or_after``: *"comparing the raw strings instead would order
``...+00:00`` against ``...Z`` lexically — a bug that reads as correct whenever
both sides happen to share a spelling"*). ``compare()`` then takes
``readings[0]`` as *the* newest (``goal_judgement.py:772``) and the dwell
window from ``readings[: target.dwell]`` (``:748``, ``:811``), so a mis-ordered
page IS the state the graph reports.

Two stored spellings whose instant order and byte order DISAGREE are enough to
crop the genuinely newest reading out of the page — no crowded history, no
``limit=1`` overflow:

* ``2026-11-12T01:00:00-05:00`` is 06:00Z, and its 12th byte is ``'1'``;
  ``2026-11-12T05:00:00Z`` is 05:00Z with ``'5'`` there, so ``DESC`` puts the
  OLDER row first and ``LIMIT 1`` keeps it.
* the ingest producer's ``2026/11/12 (Thu) 01:00`` sorts above every dashed row
  of the same date because ``'/'`` (0x2F) > ``'-'`` (0x2D) at index 4.

Every test here is written so that a regression reds it:

* reverting the ordering to the raw string reds the order/limit/judgement tests;
* re-introducing a Cypher-side ``LIMIT`` reds
  ``test_the_cap_is_applied_after_the_ordering`` (the two instant-newest rows are
  the lexically LAST two, so a page cut before the sort cannot contain them);
* restoring the "complete set" docstring claim reds the disclosure test.

Runnable embedded:
    TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \
        tests/test_12322_latest_readings_order.py -q -p no:cacheprovider
Runs in the docker lane too: the shared ``sdk_factory`` fixture is URI-aware.
"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager

import pytest

from tortoise.goal_judgement import Target, compare, latest_readings


@contextmanager
def _forces_tz(name: str):
    """Run a block under a forced timezone, restoring the ambient one.

    The SAME helper shape as ``tests/test_3982_date_only_utc.py`` (#3982), and
    load-bearing here rather than decorative: ``_created_sort_key`` reads a
    zone-less date-TIME in the READER's zone, so the producer fixture below is
    01:00Z on a UTC host and 05:00Z on a ``-04:00`` one — where it would TIE the
    dashed row and the id tie-break would decide. Pinning the zone makes the
    byte-vs-instant disagreement a property of the fixture instead of the host
    (#11646's residual: recorded, not fixed).
    """
    prev = os.environ.get("TZ")
    os.environ["TZ"] = name
    time.tzset()
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = prev
        time.tzset()


@pytest.fixture
def sdk(sdk_factory):
    s = sdk_factory()
    yield s
    s.close()


def _indicator(sdk, tag: str) -> dict:
    """A measured thing. ``tag`` keeps two worlds apart (name is the identity)."""
    return sdk.create_object(f"CI pipeline duration [{tag}]", objectKind="workflow")


def _reading(sdk, indicator, value, *, at, kind="measurement"):
    return sdk.create_event(
        f"CI run at {at!r}", kind, value=value, unit="minutes",
        startedAt=at, aboutObject=indicator["id"],
    )


# ── 1. the defect, at the smallest fixture that shows it ───────────────────


def test_limit_one_keeps_the_instant_newest_reading_not_the_lexically_first(sdk):
    """Two readings, one local offset and one ``Z``; the instant-newest is not
    the string-largest, and ``limit=1`` must still return it.

    RED before the fix: the raw-string ``ORDER BY`` + ``LIMIT 1`` returned the
    older row (value 5.0), because ``...T05:00:00Z`` sorts above
    ``...T01:00:00-05:00`` byte-wise.
    """
    indicator = _indicator(sdk, "offset")
    newer = _reading(sdk, indicator, 12.0, at="2026-11-12T01:00:00-05:00")  # 06:00Z
    _reading(sdk, indicator, 5.0, at="2026-11-12T05:00:00Z")  # 05:00Z
    # The premise, asserted so the fixture cannot silently stop disagreeing:
    assert "2026-11-12T05:00:00Z" > "2026-11-12T01:00:00-05:00"

    got = latest_readings(sdk, indicator["id"], limit=1)
    assert [r.reading_id for r in got] == [newer["id"]], (
        "limit=1 returned the reading whose stored BYTES sort first, not the "
        "instant-newest one"
    )
    assert [r.value for r in got] == [12.0]
    # …and the same disagreement is visible with the whole history, so the fix
    # is an ORDERING fix and not merely a wider page.
    assert [r.value for r in latest_readings(sdk, indicator["id"])] == [12.0, 5.0]


def test_the_judgement_is_computed_from_the_instant_newest_reading(sdk):
    """The consequence #11926 exists to remove: a judgement made from a stale
    measurement. ``12 >= 10`` is ``met``; answering from the 5.0 row says
    ``unmet``."""
    indicator = _indicator(sdk, "judgement")
    _reading(sdk, indicator, 12.0, at="2026-11-12T01:00:00-05:00")  # 06:00Z
    _reading(sdk, indicator, 5.0, at="2026-11-12T05:00:00Z")  # 05:00Z

    page = latest_readings(sdk, indicator["id"], limit=1)
    judgement = compare(page, Target(comparator=">=", threshold=10.0))
    assert (judgement.state, judgement.flipped) == ("met", True)
    assert judgement.reading is not None and judgement.reading.value == 12.0


def test_a_slash_spelled_reading_does_not_outrank_a_newer_dashed_one(sdk):
    """The producer's own shape. ``'/'`` > ``'-'`` at index 4, so the raw string
    puts a 01:00Z row above a 05:00Z row of the same date. RED before the fix.

    Zone-pinned on purpose: the producer spelling carries no offset, so
    ``_created_sort_key`` reads it in the reader's zone (#3982/#11646) — on a
    ``-07:00`` host its instant would be LATER than 05:00Z and there would be no
    disagreement left to prove. Under UTC the disagreement is exact.
    """
    indicator = _indicator(sdk, "producer")
    with _forces_tz("UTC"):
        _reading(sdk, indicator, 1.0, at="2026/11/12 (Thu) 01:00")  # 01:00Z
        newer = _reading(sdk, indicator, 9.0, at="2026-11-12T05:00:00Z")  # 05:00Z
        assert "2026/11/12 (Thu) 01:00" > "2026-11-12T05:00:00Z"

        got = latest_readings(sdk, indicator["id"], limit=1)
    assert [r.reading_id for r in got] == [newer["id"]]
    assert [r.value for r in got] == [9.0]


# ── 2. the cap is applied AFTER the ordering ──────────────────────────────


def test_the_cap_is_applied_after_the_ordering(sdk):
    """``limit`` is a CAP ON THE ORDERED LIST, never a pre-cut page.

    The two instant-newest readings here are also the two lexically LAST, so a
    fix that keeps ``LIMIT $lim`` in Cypher and sorts afterwards still cannot
    return them — this is the assertion that distinguishes a real fix from a
    re-sort of a wrongly-cut page.
    """
    indicator = _indicator(sdk, "cap")
    # lexically greatest strings first in the query, instant-oldest:
    _reading(sdk, indicator, 1.0, at="2026-11-12T09:00:00Z")  # 09:00Z
    _reading(sdk, indicator, 2.0, at="2026-11-12T08:00:00Z")  # 08:00Z
    _reading(sdk, indicator, 3.0, at="2026-11-12T07:00:00Z")  # 07:00Z
    # lexically smallest (they begin "2026-11-12T00:"), instant-newest — a
    # -12:00 offset buys hours of instant for no byte rank:
    _reading(sdk, indicator, 8.0, at="2026-11-12T00:20:00-12:00")  # 12:20Z
    _reading(sdk, indicator, 9.0, at="2026-11-12T00:30:00-12:00")  # 12:30Z

    page = latest_readings(sdk, indicator["id"], limit=2)
    assert [r.value for r in page] == [9.0, 8.0]
    # The full read is ordered newest-first over every spelling present.
    assert [r.value for r in latest_readings(sdk, indicator["id"])] == [
        9.0, 8.0, 1.0, 2.0, 3.0,
    ]


def test_ties_are_broken_deterministically_by_reading_id(sdk):
    """Same instant, two readings: newest-first must be a total order, or the
    same graph answers differently between reads."""
    indicator = _indicator(sdk, "tie")
    a = _reading(sdk, indicator, 4.0, at="2026-11-12T05:00:00Z")
    b = _reading(sdk, indicator, 6.0, at="2026-11-12T00:00:00-05:00")  # also 05:00Z
    got = latest_readings(sdk, indicator["id"])
    assert len(got) == 2
    assert {r.reading_id for r in got} == {a["id"], b["id"]}
    # Determinism, not a particular direction: two reads agree.
    assert [r.reading_id for r in latest_readings(sdk, indicator["id"])] == [
        r.reading_id for r in got
    ]


def test_the_read_pages_rather_than_relying_on_one_result_set(sdk, monkeypatch):
    """The history is fetched by keyset pages, so a server-side
    ``RESULTSET_SIZE`` cap cannot silently truncate it in store order (#4233).

    The page size is retuned to 2 here — the pattern
    ``tests/test_9226_dump_paging_v6.py`` uses — so paging is exercised without
    seeding a thousand events. A regression to the PRE-FIX shape (one
    ``LIMIT $lim`` query) returns 2 of the 5 rows and reds the length assertion.

    ⚠️ Disclosed bound on what this pins: a lane container reports
    ``RESULTSET_SIZE = -1``, so the server-side cap the paging exists for cannot
    be reproduced here. This test pins the paging SHAPE (and the app-level cut it
    replaced), not the cap itself.
    """
    import tortoise.goal_judgement as gj

    monkeypatch.setattr(gj, "_READINGS_PAGE_SIZE", 2)
    indicator = _indicator(sdk, "paging")
    for hour in (1, 2, 3, 4, 5):
        _reading(sdk, indicator, float(hour), at=f"2026-11-12T0{hour}:00:00Z")

    got = latest_readings(sdk, indicator["id"])
    assert len(got) == 5, "a page cut lost readings — the read is not paging"
    assert [r.value for r in got] == [5.0, 4.0, 3.0, 2.0, 1.0]


def test_a_negative_limit_is_refused_rather_than_silently_sliced(sdk):
    """A negative bound is a caller error, not a bound.

    ``out[: -1]`` drops the OLDEST reading while reading as "everything" — the
    same silent-bound class this function exists to remove (#11823/#12322), so it
    is refused instead of sliced.
    """
    indicator = _indicator(sdk, "negative")
    _reading(sdk, indicator, 9.0, at="2026-11-12T05:00:00Z")
    with pytest.raises(ValueError, match="non-negative cap"):
        latest_readings(sdk, indicator["id"], limit=-1)


def test_a_degenerate_page_size_terminates_instead_of_raising(sdk, monkeypatch):
    """The loop must end on an EMPTY page before it touches ``page[-1]``.

    ``hosted_backup._paged_rows`` checks ``if not rows: return`` first; a loop
    that checks ``len(page) < page_size`` first raises ``IndexError`` on an empty
    page when the size is 0 (measured). Unreachable at the shipped 1000, pinned
    so the next retune cannot resurrect it.
    """
    import tortoise.goal_judgement as gj

    indicator = _indicator(sdk, "degenerate")
    _reading(sdk, indicator, 9.0, at="2026-11-12T05:00:00Z")
    monkeypatch.setattr(gj, "_READINGS_PAGE_SIZE", 0)
    assert latest_readings(sdk, indicator["id"]) == []


# ── 3. an unparseable instant sorts LAST, deterministically ─────────────

def test_a_reading_with_no_parseable_instant_sorts_after_every_dated_one(sdk):
    """``_created_sort_key``'s unparseable bucket sorts LAST, deterministically.

    It must: an as-of nobody can place cannot PROVE it is recent, so it must
    never displace a dated reading as ``readings[0]`` — while still being
    returned rather than dropped (dropping is ``since``'s job, and only when a
    boundary is declared).
    """
    indicator = _indicator(sdk, "undated")
    _reading(sdk, indicator, 9.0, at="2026-11-12T05:00:00Z")
    _reading(sdk, indicator, 1.0, at="not-an-instant")
    got = latest_readings(sdk, indicator["id"])
    assert [r.value for r in got] == [9.0, 1.0]


# ── 4. the ``since`` filter composes with the ordering ──────────────────


def test_since_excludes_by_instant_on_the_correctly_ordered_list(sdk):
    """#11926's boundary filter reads a list that is now ordered by the same
    comparator it filters with — the two can no longer disagree."""
    indicator = _indicator(sdk, "since")
    _reading(sdk, indicator, 12.0, at="2026-11-12T01:00:00-05:00")  # 06:00Z
    _reading(sdk, indicator, 5.0, at="2026-11-12T05:00:00Z")  # 05:00Z
    kept = latest_readings(
        sdk, indicator["id"], since="2026-11-12T05:30:00+00:00"
    )
    assert [r.value for r in kept] == [12.0]


# ── 5. the disclosure (#11823's class, documentation form) ────────────────


def test_the_docstring_discloses_the_cap_and_claims_no_complete_set():
    """``limit`` binds this read and a list cannot report that it was hit, so
    the docstring must SAY so — the same disclosure ``live_judgements`` carries
    ("⛔ ``limit`` IS A CAP, NOT A COURTESY"). The removed sentence *"The
    readings recorded about ``indicator_id``, newest first."* claimed the
    complete set over ``limit=100``.
    """
    doc = " ".join((latest_readings.__doc__ or "").lower().split())
    assert "limit" in doc, "the docstring must name the bound it applies"
    assert "cap" in doc, "the bound must be disclosed as a CAP, not a courtesy"
    assert "the readings recorded about" not in doc, (
        "the removed sentence claimed a COMPLETE set over a bounded read"
    )
