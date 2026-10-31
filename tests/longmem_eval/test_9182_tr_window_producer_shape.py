"""#9182 — the R5 TR hard time-window on the PRODUCER's real date shape.

The frozen LongMemEval dataset carries ``session_date`` / ``question_date`` as
``2023/04/10 (Mon) 17:50`` — 0 of 500 questions are ISO (measured on
``longmemeval_oracle.json``). The seam used to compare those RAW strings, so
the window was dead in both branches:

* recency — ``strptime(str(question_date)[:10], "%Y-%m-%d")`` raised on
  ``2023/04/10 (Mon) 23:07`` → ``except (TypeError, ValueError): return []``;
* interval — ``start <= session_date <= end`` compared an ISO bound against a
  slash-shaped date; ``"/"`` (0x2F) > ``"-"`` (0x2D) at index 4, so every dated
  hit failed the upper bound.

``[]`` is the caller's "cannot compute bounds" signal, so the caller fell back
to the unfiltered pool (``tr_window_fallback``) and the R5 hard filter never
applied — every TR delta attributed to the window (#1544, #2520) was measuring
the fallback.

These tests pin the whole contract on the producer's shape: both branches
resolve, the ISO control is untouched, and every unresolvable input still
degrades to "no bound" (the caller's fallback) rather than to an empty filter.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from tools.longmem_eval.retrieve import TimeConstraint, _apply_time_window

# The issue's own repro fixture, verbatim.
HITS = [
    {"session_date": "2023/04/10 (Mon) 17:50"},
    {"session_date": "2023/04/05 (Wed) 11:00"},
]
QUESTION_DATE = "2023/04/10 (Mon) 23:07"


def _dates(hits: list[dict]) -> list[str]:
    return [h["session_date"] for h in hits]


def test_issue_repro_recency_resolves_on_the_producer_shape():
    """Criterion (a), verbatim from #9182: ``[]`` before the fix, both
    in-window hits after it (2023-04-05 is inside ``qdate − 7`` =
    2023-04-03)."""
    out = _apply_time_window(
        HITS, TimeConstraint("recency", start="7"),
        question_date=QUESTION_DATE)
    assert _dates(out) == [
        "2023/04/10 (Mon) 17:50",
        "2023/04/05 (Wed) 11:00",
    ]


def test_interval_branch_resolves_on_the_producer_shape():
    """Criterion (c), interval branch: an ISO bound window now qualifies the
    producer's slash dates instead of dropping every one of them."""
    out = _apply_time_window(
        HITS,
        TimeConstraint("interval", start="2023-04-03", end="2023-04-10"),
        question_date=QUESTION_DATE)
    assert _dates(out) == [
        "2023/04/10 (Mon) 17:50",
        "2023/04/05 (Wed) 11:00",
    ]


def test_out_of_window_producer_hits_are_dropped():
    """The window is a HARD filter once it resolves — it does not degrade to
    "keep everything" (that would be the same defect, inverted)."""
    recency = _apply_time_window(
        HITS, TimeConstraint("recency", start="2"),
        question_date=QUESTION_DATE)
    assert _dates(recency) == ["2023/04/10 (Mon) 17:50"]
    interval = _apply_time_window(
        HITS, TimeConstraint("interval", start="2023-04-09",
                             end="2023-04-10"),
        question_date=QUESTION_DATE)
    assert _dates(interval) == ["2023/04/10 (Mon) 17:50"]


def test_recency_boundary_day_is_inclusive():
    """``qdate − 7`` (2023-04-03) is IN the window; ``qdate − 8`` is out. The
    inclusion is the shared resolver's documented rule, asserted here on the
    producer's shape so a half-open window cannot slip in."""
    hits = [{"session_date": "2023/04/03 (Mon) 09:00"},
            {"session_date": "2023/04/02 (Sun) 09:00"}]
    out = _apply_time_window(hits, TimeConstraint("recency", start="7"),
                             question_date=QUESTION_DATE)
    assert _dates(out) == ["2023/04/03 (Mon) 09:00"]


def test_iso_date_only_control_is_unchanged():
    """A date-only ISO ``session_date`` behaves exactly as before the fix —
    this is the control case the issue names ("no change to the ISO control
    cases"), and it is the shape the shipped suite uses."""
    assert _apply_time_window(
        [{"session_date": "2023-04-10"}],
        TimeConstraint("recency", start="7"),
        question_date="2023-04-10") == [{"session_date": "2023-04-10"}]
    assert _apply_time_window(
        [{"session_date": "2023-04-10"}],
        TimeConstraint("interval", start="2023-04-03", end="2023-04-10"),
        question_date="2023-04-10") == [{"session_date": "2023-04-10"}]


def test_timestamp_shaped_iso_hit_on_the_boundary_day_is_now_kept():
    """DECLARED behaviour change (a WIDENING, not a control): a hit whose
    ``session_date`` carries a time component — ``2023-04-10T00:00:00Z`` — on
    the last day of the window was DROPPED before this fix in BOTH branches
    (the raw comparison ``"2023-04-10T00:00:00Z" <= "2023-04-10"`` fails on
    the lexical tail), and is kept now that both sides resolve to calendar
    dates.

    This is #9182's defect seen from the ISO side: a date-only bound is
    inclusive of the whole calendar day. Pinned explicitly so the widening is
    visible to anyone re-cutting a TR arm comparison, instead of hiding inside
    an assertion labelled "unchanged" (fresh-context review, P2)."""
    hit = [{"session_date": "2023-04-10T00:00:00Z"}]
    assert _apply_time_window(
        hit, TimeConstraint("recency", start="7"),
        question_date="2023-04-10") == hit
    assert _apply_time_window(
        hit, TimeConstraint("interval", start="2023-04-03", end="2023-04-10"),
        question_date="2023-04-10") == hit
    # …and the day AFTER the window is still dropped: what widened is the
    # calendar-day boundary, not the filter.
    assert _apply_time_window(
        [{"session_date": "2023-04-11T00:00:00Z"}],
        TimeConstraint("recency", start="7"),
        question_date="2023-04-10") == []


def test_the_raw_string_compare_is_why_normalization_is_required():
    """The defect's MECHANISM, pinned as a fact rather than left in prose.

    Both dead halves of the pre-fix seam were lexical, and both are reproduced
    here on the exact values the producer and the ISO bounds carry. This is
    the non-vacuity record for the rest of the module: if either assertion
    ever starts holding, the shape changed and the surrounding tests stop
    proving what they claim.
    """
    # interval, producer shape: "/" (0x2F) > "-" (0x2D) at index 4, so the
    # ISO upper bound sorts BEFORE every slash-shaped date.
    assert not ("2023-04-03" <= "2023/04/10 (Mon) 17:50" <= "2023-04-10")
    # interval, ISO-with-time hit: the date-only bound is a PREFIX of the
    # timestamp, so the shorter string sorts first and the same-day hit is
    # dropped (the declared widening above).
    assert not ("2023-04-10T00:00:00Z" <= "2023-04-10")
    # recency, producer question date: ``strptime(..., "%Y-%m-%d")`` raises
    # on the slash shape — the pre-fix ``except`` turned that into the
    # caller's fallback signal, so the window never applied.
    with pytest.raises(ValueError):
        datetime.strptime(QUESTION_DATE[:10], "%Y-%m-%d")


def test_absent_question_date_still_degrades_to_no_bound():
    """Recency needs a clock. A MISSING question date keeps the pre-#9182
    contract exactly: ``[]`` — the caller's fallback, never a fabricated
    window anchored on a different date."""
    for missing in (None, ""):
        assert _apply_time_window(
            HITS, TimeConstraint("recency", start="7"),
            question_date=missing) == []


def test_unparseable_question_date_degrades_to_no_bound():
    """An UNRESOLVABLE anchor is "no bound", never a substituted clock (the
    census's latest-session-date rule would answer a different question) and
    never a raise."""
    assert _apply_time_window(
        HITS, TimeConstraint("recency", start="7"),
        question_date="not a date") == []
    assert _apply_time_window(
        HITS, TimeConstraint("recency", start="7"),
        question_date="2023-13-45") == []


def test_undated_hit_never_satisfies_a_window():
    """An undated hit cannot be proven in-window, so a resolved window drops
    it — while the dated sibling survives."""
    hits = [{"session_date": "2023/04/10 (Mon) 17:50"},
            {"session_date": ""},
            {}]
    out = _apply_time_window(hits, TimeConstraint("recency", start="7"),
                             question_date=QUESTION_DATE)
    assert out == [{"session_date": "2023/04/10 (Mon) 17:50"}]


def test_the_seam_returns_a_non_empty_window_for_the_caller_to_keep():
    """Criterion (b), SEAM-LEVEL pin (not end-to-end): the caller sets
    ``tr_window_fallback`` ONLY when this returns empty
    (``retrieve.py::retrieve_for_question``: ``windowed =
    _apply_time_window(...); if windowed: annotated = windowed else:
    tr_window_fallback = True``). Returning the in-window pool on the producer
    shape is what stops the marker firing.

    Scope stated honestly: this pins the seam's return, so it would not notice
    a change in the CALLER's branch. The end-to-end marker itself is pinned on
    ISO shapes by ``tests/test_longmem_runner.py::
    test_tr_window_filter_excludes_out_of_window_session`` (marker False) and
    ``::test_tr_window_filter_falls_back_when_starved`` (marker True), and on
    the PRODUCER's slash shape by ``::
    test_tr_window_applies_on_the_producers_slash_date_shape`` (marker False,
    pool narrowed to the in-window session) — that last one needs no frozen
    dataset (``tools/longmem_eval/ingest.py`` copies ``haystack_dates[si]``
    verbatim into the session's recorded date), and it lives in the runner
    module rather than here because ``tests/longmem_eval/**`` is the
    non-gating lane (owner decision 2026-10-27, #10194), so a pin that must
    gate a merge cannot live in this file.
    """
    windowed = _apply_time_window(
        [*HITS, {"session_date": "2022-01-01"}],
        TimeConstraint("interval", start="2023-04-03", end="2023-04-10"),
        question_date=QUESTION_DATE)
    assert windowed  # non-empty → the caller takes `annotated = windowed`
    assert windowed == HITS  # …and the out-of-window row IS dropped


def test_reversed_interval_means_the_window_the_question_named():
    """The shared resolver orders a reversed pair rather than emptying it —
    a pre-fix ``[]`` here was another way to reach the fallback."""
    out = _apply_time_window(
        HITS,
        TimeConstraint("interval", start="2023-04-10", end="2023-04-03"),
        question_date=QUESTION_DATE)
    assert _dates(out) == [
        "2023/04/10 (Mon) 17:50",
        "2023/04/05 (Wed) 11:00",
    ]


def test_ordering_and_none_constraints_are_a_no_op():
    """Unchanged contract: only ``interval`` / ``recency`` filter."""
    pool = [{"id": "a", "session_date": "2023/04/10 (Mon) 17:50"}]
    assert _apply_time_window(pool, TimeConstraint("ordering"),
                             question_date=QUESTION_DATE) == pool
    assert _apply_time_window(pool, TimeConstraint(None),
                             question_date=QUESTION_DATE) == pool


def test_a_resolved_interval_that_matches_nothing_returns_empty():
    """The ONE case that must stay ``[]``: a window that resolves and has no
    in-window dated hit (the caller's documented fallback path)."""
    assert _apply_time_window(
        HITS, TimeConstraint("interval", start="2022-01-01",
                             end="2022-01-31"),
        question_date=QUESTION_DATE) == []


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
