"""#2521 (C5 #2513) — aggregative-intent detector + facet-coverage check.

HERMETIC tests (no graph, no model, no IO): the classification table
(test (a) of the issue), the pure facet-key extraction + k-of-N coverage
math (test (b) — complete/partial/none + the never-flag-open-ended rule),
the OFF-by-default signature gate, and the bounded/fail-open contracts.
The graph-backed half of the coverage check (anchor resolution through the
Object-name spine + the eval arm marker) lives in
tests/test_aggregative_facet_coverage.py (docker lane) — this file runs
the IDENTICAL pure logic offline (the single-source-of-truth split that
evidence.py establishes for the M6 marks).

Product module under test: ``tortoise/aggregate.py`` (#2521 — C5 #2513
aggregative-intent detection + per-facet coverage check; the seam the
#2519 completeness-loop routing (C3-3) will consume).
"""
from __future__ import annotations

import inspect

import pytest

from tortoise import aggregate as agg

# ── (a) detector classification table ──────────────────────────────────────
# (query, is_aggregative, quantifier, scope)
CLASSIFICATION_TABLE = [
    # ── counting / quantifier aggregations, entity-scoped surface ─────────
    ("how many playlists do i have on spotify",
     True, "how_many", "entity-scoped"),
    ("how much did the road bike repairs cost me in total",
     True, "how_much", "entity-scoped"),
    ("how many times did we discuss the api key migration across sessions",
     True, "how_many", "entity-scoped"),
    ("what is the total number of bikes i own",
     True, "count_of", "entity-scoped"),
    ("how often do i go to the gym",
     True, "how_often", "entity-scoped"),
    ("tell me how many shirts i packed for costa rica",
     True, "how_many", "entity-scoped"),
    ("how much ram did i upgrade my laptop to",
     True, "how_much", "entity-scoped"),
    ("what was the total cost of my trip to costa rica",
     True, "total", "entity-scoped"),
    ("every time we went to the gym did we do cardio",
     True, "every", "entity-scoped"),
    ("How many largemouth bass did I catch on my fishing trip to Lake "
     "Michigan?", True, "how_many", "entity-scoped"),
    # ── open-ended aggregation (the memory corpus is the subject — the
    #    never-flag rule: these are NEVER coverage-flagged downstream) ─────
    ("how many memories do i have in total", True, "how_many", "open-ended"),
    ("how much have we talked in total", True, "how_much", "open-ended"),
    ("how many times have we talked", True, "how_many", "open-ended"),
    ("how much total did everything cost", True, "how_much", "open-ended"),
    ("how many sessions have we had about everything",
     True, "how_many", "open-ended"),
    # ── non-aggregative / other-intent queries (never flagged) ────────────
    ("what did i eat for dinner on tuesday", False, None, None),
    ("did i go running yesterday morning", False, None, None),
    # how-long / elapsed-time are TEMPORAL-REASONING classes (R5 #1544),
    # not counting aggregation
    ("how long was the flight to costa rica", False, None, None),
    ("how many days ago did i go running", False, None, None),
    ("what is my current api key status", False, None, None),
    ("", False, None, None),
    (None, False, None, None),
]


@pytest.mark.parametrize(
    ("query", "is_aggregative", "quantifier", "scope"),
    CLASSIFICATION_TABLE,
    ids=[f"r{i}" for i in range(len(CLASSIFICATION_TABLE))],
)
def test_detector_classification_table(query, is_aggregative, quantifier,
                                       scope):
    """The pinned #2521 classification table (test (a)): the hermetic
    rule-based detector returns intent + scope deterministically. A
    vocabulary change is a deliberate, reviewed table diff."""
    verdict = agg.detect_aggregative_intent(query)
    assert verdict.is_aggregative is is_aggregative
    assert verdict.quantifier == quantifier
    assert verdict.scope == scope


def test_detector_facet_dimension_hints():
    """The detector's aggregation-axis hint shares the coverage facet
    vocabulary (FACET_DIMENSIONS): date-cue questions hint ``date_month``,
    kind-cue questions ``kind``, everything else ``session`` — so the hint
    feeds the coverage math directly and can never raise a dimension
    ValueError through the one-call seam."""
    assert agg.detect_aggregative_intent(
        "how much do i spend on coffee per month").facet_dimension == \
        "date_month"
    assert agg.detect_aggregative_intent(
        "how many kinds of tea do i drink").facet_dimension == "kind"
    assert agg.detect_aggregative_intent(
        "how much did the road bike repairs cost me in total"
    ).facet_dimension == "session"
    assert agg.detect_aggregative_intent(
        "what did i eat for dinner").facet_dimension is None
    # vocabulary alignment pin: every hint the detector emits is a legal
    # coverage dimension (the mismatch class VGATE caught — a "date" hint
    # would raise inside compute_facet_coverage)
    for q in ("how much do i spend on coffee per month",
              "how many kinds of tea do i drink",
              "how much did the road bike repairs cost me in total"):
        hint = agg.detect_aggregative_intent(q).facet_dimension
        assert hint is None or hint in agg.FACET_DIMENSIONS


def test_verdict_seam_normalizes_unknown_dimension():
    """Defense-in-depth for the NEVER-raises seam contract: an unknown
    coverage dimension (from a caller or a future detector hint) falls
    back to the wired default instead of raising a ValueError through
    the one-call surface (a date-cued scoped question would otherwise
    raise inside compute_facet_coverage)."""
    class _NoGraph:
        g = None

    # an unknown explicit dimension must not raise (fails open at the
    # anchor gate; the dimension fallback is what we assert here)
    verdict = agg.aggregative_verdict(
        query="how much do i spend on coffee per month",
        proj=_NoGraph(), retrieved_points=[], dimension="bogus")
    assert verdict["signal"] == "none"
    assert verdict["reason"] == "no_anchor"
    # the detector hint itself is a legal dimension (no raise anywhere)
    verdict = agg.aggregative_verdict(
        query="how much do i spend on coffee per month",
        proj=_NoGraph(), retrieved_points=[])
    assert verdict["reason"] in ("no_anchor", "fail_open")


def test_detector_elapsed_time_and_how_long_never_aggregative():
    """Regression pin: "how many days ago" (R5's elapsed-time ordering) and
    "how long" (duration/span) are the temporal classes' shapes — they must
    never classify as counting aggregation even though they share the
    "how many/much" surface words."""
    for q in ("how many days ago did i last see my dentist",
              "how many weeks ago did we discuss the migration",
              "how long did the bike ride take",
              "3 days ago how much did i weigh"):
        assert agg.detect_aggregative_intent(q).is_aggregative is False, q


# ── (b) facet enumeration + per-facet coverage check (pure math) ───────────
def test_facet_key_for_point_dimensions():
    """Facet-key extraction per dimension: session (session_id/sessionId),
    kind (point_kind/pointKind), date_month (date-prop truncated to
    YYYY-MM). A point lacking the axis value yields None — it contributes
    to neither census, so k-of-N stays honest."""
    assert agg.facet_key_for_point({"session_id": "s1"}) == "s1"
    assert agg.facet_key_for_point({"sessionId": "s1"}) == "s1"
    assert agg.facet_key_for_point({"session_id": "  "}) is None
    assert agg.facet_key_for_point({"id": "p1"}) is None
    assert agg.facet_key_for_point(
        {"point_kind": "statement"}, dimension="kind") == "statement"
    assert agg.facet_key_for_point(
        {"pointKind": "decision"}, dimension="kind") == "decision"
    assert agg.facet_key_for_point(
        {"kind": "statement"}, dimension="kind") == "statement"
    assert agg.facet_key_for_point({}, dimension="kind") is None
    assert agg.facet_key_for_point(
        {"session_date": "2026-09-07"}, dimension="date_month") == "2026-09"
    assert agg.facet_key_for_point(
        {"created_at": "2026-09-07T10:00:00Z"},
        dimension="date_month") == "2026-09"
    assert agg.facet_key_for_point(
        {"session_date": "not-a-date"}, dimension="date_month") is None
    assert agg.facet_key_for_point({}, dimension="date_month") is None
    with pytest.raises(ValueError):
        agg.facet_key_for_point({"session_id": "s1"}, dimension="nope")


def test_coverage_complete_partial_none():
    """The core k-of-N semantics: complete when every known facet is
    represented in the retrieval, partial when k < N (including k=0 — the
    worst partial), and none when no exhaustive N is enumerable (empty
    census — never a 0.0 claim)."""
    census = [{"id": f"p{i}", "session_id": f"s{i % 3}"} for i in range(6)]
    # complete: all 3 known session-facets retrieved
    complete = agg.compute_facet_coverage(
        census_points=census,
        retrieved_points=[
            {"id": "a", "session_id": "s0"},
            {"id": "b", "session_id": "s1"},
            {"id": "c", "session_id": "s2"},
        ])
    assert complete.signal == "complete"
    assert complete.facet_coverage == 1.0
    assert complete.n_facets == 3
    assert complete.retrieved_facets == 3
    assert complete.missing_facets == ()
    assert complete.capped is False
    # partial: one of three known facets retrieved
    partial = agg.compute_facet_coverage(
        census_points=census,
        retrieved_points=[{"id": "a", "session_id": "s0"}])
    assert partial.signal == "partial"
    assert partial.facet_coverage == pytest.approx(1 / 3)
    assert partial.missing_facets == ("s1", "s2")
    # zero-of-N is still partial (the retrieval surfaced none of the N
    # known facets — the honest worst case, never "complete")
    none_retrieved = agg.compute_facet_coverage(
        census_points=census, retrieved_points=[])
    assert none_retrieved.signal == "partial"
    assert none_retrieved.facet_coverage == 0.0
    assert len(none_retrieved.missing_facets) == 3
    # no enumerable N → signal none (not 0.0 — no coverage claim)
    no_census = agg.compute_facet_coverage(
        census_points=[], retrieved_points=[{"id": "a", "session_id": "s0"}])
    assert no_census.signal == "none"
    assert no_census.facet_coverage is None
    assert no_census.n_facets == 0
    # undated/unsessioned points join neither side
    mixed = agg.compute_facet_coverage(
        census_points=[{"id": "p1", "session_id": "s0"},
                       {"id": "p2"}],  # no session linkage
        retrieved_points=[{"id": "p1", "session_id": "s0"}])
    assert mixed.n_facets == 1
    assert mixed.signal == "complete"


def test_coverage_kind_and_date_dimensions():
    """kind/date_month dimensions ride the same pure math (the spine
    supports them; session is the wired v1 default)."""
    kind = agg.compute_facet_coverage(
        census_points=[
            {"id": "p1", "point_kind": "statement"},
            {"id": "p2", "point_kind": "decision"},
            {"id": "p3", "point_kind": "statement"},
        ],
        retrieved_points=[{"id": "r1", "point_kind": "statement"}],
        dimension="kind")
    assert kind.signal == "partial"
    assert kind.facet_coverage == 0.5
    assert kind.missing_facets == ("decision",)
    months = agg.compute_facet_coverage(
        census_points=[
            {"id": "p1", "session_date": "2026-09-07"},
            {"id": "p2", "session_date": "2026-10-01"},
        ],
        retrieved_points=[
            {"id": "r1", "session_date": "2026-09-20"},
            {"id": "r2", "session_date": "2026-10-15"},
        ],
        dimension="date_month")
    assert months.signal == "complete"
    assert months.facet_coverage == 1.0


def test_coverage_bounded_cap_degrades_to_none(monkeypatch):
    """Bounded + fail-open: an enumeration that overflows MAX_FACETS
    distinct known facets is NOT exhaustive — the verdict degrades to
    ``none`` (capped=True) instead of publishing a truncated N (the
    all-pieces honesty claim never rides a truncated census)."""
    monkeypatch.setattr(agg, "MAX_FACETS", 3)
    census = [{"id": f"p{i}", "session_id": f"s{i}"} for i in range(6)]
    got = agg.compute_facet_coverage(
        census_points=census,
        retrieved_points=[{"id": f"r{i}", "session_id": f"s{i}"}
                          for i in range(3)])
    assert got.capped is True
    assert got.signal == "none"
    assert got.n_facets == 3  # capped at the bound, flagged not-exhaustive
    # under the cap the same shape reports honestly
    monkeypatch.setattr(agg, "MAX_FACETS", 100)
    got = agg.compute_facet_coverage(
        census_points=census,
        retrieved_points=[{"id": f"r{i}", "session_id": f"s{i}"}
                          for i in range(3)])
    assert got.capped is False
    assert got.signal == "partial"
    assert got.missing_facets == ("s3", "s4", "s5")


def test_coverage_missing_report_bounded(monkeypatch):
    """The reported missing-facet list is bounded (MAX_MISSING_REPORTED) —
    the loop only needs a bounded re-query target; full enumeration is the
    census's job."""
    monkeypatch.setattr(agg, "MAX_MISSING_REPORTED", 2)
    census = [{"id": f"p{i}", "session_id": f"s{i}"} for i in range(10)]
    got = agg.compute_facet_coverage(
        census_points=census,
        retrieved_points=[{"id": "a", "session_id": "s0"}])
    assert got.signal == "partial"
    assert len(got.missing_facets) == 2


def test_never_flag_open_ended_scope():
    """The never-flag-open-ended rule (text gate): corpus-self-referential
    aggregation ("how many memories/how much have we talked") is scope
    ``open-ended`` — the coverage path must never run for it, regardless of
    what an index holds (the index gate backs this up in the graph test).
    assert the pure classification + the seam's early-exit vocabulary."""
    open_q = "how many memories do i have in total"
    intent = agg.detect_aggregative_intent(open_q)
    assert intent.is_aggregative is True
    assert intent.scope == "open-ended"
    # open-ended scope is never "entity-scoped": the verdict seam keys on
    # this so a corpus question can never reach the anchor census
    assert intent.scope != "entity-scoped"


def test_aggregative_verdict_requires_no_graph_for_early_exits():
    """The one-call seam is fail-open by construction: non-aggregative and
    open-ended queries never touch the graph (the reason vocabulary pins
    the early exits; the anchor/index-gated paths are the docker test's)."""

    class _NoGraph:
        g = None

    proj = _NoGraph()
    non_agg = agg.aggregative_verdict(
        query="what did i eat for dinner on tuesday", proj=proj,
        retrieved_points=[])
    assert non_agg["signal"] == "none"
    assert non_agg["reason"] == "not_aggregative"
    assert non_agg["detected_intent"]["is_aggregative"] is False
    open_ended = agg.aggregative_verdict(
        query="how many memories do i have in total", proj=proj,
        retrieved_points=[])
    assert open_ended["signal"] == "none"
    assert open_ended["reason"] == "open_ended"
    assert open_ended["missing_facets"] == []
    assert open_ended["facet_coverage"] is None
    # a graphless proj reaching the anchor gate fails open (no raise)
    scoped = agg.aggregative_verdict(
        query="how much did the road bike repairs cost me in total",
        proj=proj, retrieved_points=[])
    assert scoped["signal"] == "none"
    assert scoped["reason"] in ("no_anchor", "fail_open")


# ── (c) OFF-by-default surface (hermetic half) ─────────────────────────────
def test_retrieval_seam_off_by_default_signature():
    """The eval retrieval seam (``retrieve_for_question``) adds the arm as a
    tri-state kwarg defaulting to None (env-gated, fail-safe OFF) — the
    off-path passes no new kwarg and emits no verdict key (byte-identical
    default; the docker fixture test proves equality over a live graph)."""
    from tools.longmem_eval.retrieve import retrieve_for_question
    sig = inspect.signature(retrieve_for_question)
    assert sig.parameters["aggregative_flag"].default is None
    assert agg.DEFAULT_FACET_DIMENSION == "session"
    # product seam is module-level + hermetic (no graph import at module
    # import time — lazy anchor resolution keeps embedded imports clean)
    assert inspect.isfunction(agg.detect_aggregative_intent)
    assert inspect.isfunction(agg.aggregative_verdict)
