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


def test_iso_control_is_unchanged():
    """Criterion (c), the control: the ISO shapes that already worked keep
    working byte-identically."""
    assert _apply_time_window(
        [{"session_date": "2023-04-10"}],
        TimeConstraint("recency", start="7"),
        question_date="2023-04-10") == [{"session_date": "2023-04-10"}]
    assert _apply_time_window(
        [{"session_date": "2023-04-10T00:00:00Z"}],
        TimeConstraint("interval", start="2023-04-03", end="2023-04-10"),
        question_date="2023-04-10") == [
            {"session_date": "2023-04-10T00:00:00Z"}]


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


def test_the_caller_fallback_marker_would_not_fire_on_the_producer_shape():
    """Criterion (b), mechanism pin: the caller sets ``tr_window_fallback``
    ONLY when this returns empty (``retrieve.py::retrieve_for_question``:
    ``windowed = _apply_time_window(...); if windowed: annotated = windowed
    else: tr_window_fallback = True``). Returning the in-window pool is
    exactly what stops the marker firing on a TR question whose in-window
    hits exist."""
    pool = [*HITS, {"session_date": "2022-01-01"}]  # one out-of-window row
    windowed = _apply_time_window(
        HITS + [pool[-1]], TimeConstraint("interval", start="2023-04-03",
                                         end="2023-04-10"),
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
