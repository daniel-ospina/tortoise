"""#2886 — deterministic temporal-aggregation resolution arm (eval seam).

The wiring half of #2886: the shipped ``tortoise.temporal_aggregation`` core
(``resolve_temporal_aggregate`` / ``count_distinct_events``) is routed through
the eval reader lane's per-question seam (``tools/longmem_eval/retrieve.py``:
:func:`temporal_aggregate_verdict`) behind an OFF-by-default arm, so a
gold-admitting run can read out structural-vs-conversion per question.

HERMETIC half (this file): the pure verdict builder over the caller's hits
(at the eval seam, the reader-reachable pool window — a two-sided
approximation of the reader's admitted set, #3594) — distinct-event tally
for COUNT, calendar difference for an explicit
interval window, and an explicit abstention (never a guess) when the two
arithmetic anchors are not in hand. The docker-lane arm plumbing (OFF parity,
env tri-state, the outcome keys) lives with the sibling aggregative-arm tests
in ``tests/test_aggregative_facet_coverage.py``.

Nothing here changes retrieval or the reader: the owner abstains rather than
guessing, so the reader lane keeps the case (a reader-model swap stays
#2013-gated).
"""
from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.longmem_eval.retrieve import (
    TimeConstraint,
    temporal_aggregate_verdict,
)

_CENSUS = json.loads(
    (Path(__file__).resolve().parent / "_assembly_census.json").read_text())


def _hit(pid: str, content: str, date: str) -> dict:
    return {"id": pid, "content": content, "session_date": date}


# ── (a) the arm's off-by-default kwarg surface ─────────────────────────────

def test_seam_kwarg_is_off_by_default():
    """The eval seam adds ``temporal_aggregate`` as a tri-state kwarg
    defaulting to None (env-gated fail-safe OFF — the #1745 default)."""
    from tools.longmem_eval import retrieve as r
    sig = inspect.signature(r.retrieve_for_question)
    assert sig.parameters["temporal_aggregate"].default is None


# ── (b) COUNT tallies the distinct dated events (non-vacuous) ─────────────

def test_verdict_count_tallies_distinct_candidate_events():
    hits = [
        _hit("e1", "Went to the gym.", "2025-01-01"),
        _hit("e2", "Went to the gym.", "2025-02-01"),
        _hit("e3", "Went to the gym.", "2025-03-01"),
    ]
    v = temporal_aggregate_verdict(
        "How many times did I go to the gym?", hits)
    assert v["kind"] == "count"
    assert v["method"] == "count_distinct"
    assert v["value"] == 3
    assert v["n_events"] == 3
    assert v["reason"] is None
    assert v["n_dated_events"] == 3


def test_verdict_count_folds_identity_less_restatement():
    """The multi-session restatement trap: two identity-less rows with the
    same content are ONE event; the tally is 1, not 2."""
    hits = [
        _hit("", "We adopted a rescue dog named Pixel.", "2025-01-01"),
        _hit("", "We adopted a rescue dog named Pixel.", "2025-02-01"),
    ]
    v = temporal_aggregate_verdict(
        "How many times did we adopt a dog?", hits)
    assert v["kind"] == "count"
    assert v["value"] == 1


def test_verdict_count_abstains_on_empty_candidate_set():
    """An empty candidate set is NOT a measured zero — the owner abstains
    (``no_events``) so the reader lane keeps the case."""
    v = temporal_aggregate_verdict("How many times did I skydive?", [])
    assert v["kind"] == "count"
    assert v["value"] is None
    assert v["reason"] == "no_events"


# ── (c) date arithmetic: resolved with anchors, honest without ─────────────

def test_verdict_interval_from_explicit_iso_bounds():
    constraint = TimeConstraint(
        "interval", start="2025-01-01", end="2025-01-31")
    v = temporal_aggregate_verdict(
        "How many days passed between 2025-01-01 and 2025-01-31?",
        [], constraint=constraint)
    assert v["kind"] == "interval"
    assert v["method"] == "difference"
    assert v["value"] == 30
    assert v["unit"] == "days"
    assert v["anchors"] == {"start": "2025-01-01", "end": "2025-01-31"}


def test_verdict_event_referenced_arithmetic_abstains_no_anchors():
    """The census class's shape: the two anchor EVENTS are candidate hits (with
    dates) but the caller has not resolved WHICH two — the owner abstains
    (``no_anchors``) rather than guessing from the pool span. This is the
    recorded remainder, surfaced instead of silently mis-answered."""
    hits = [
        _hit("e1", "I recovered from the flu.", "2025-02-01"),
        _hit("e2", "I went on my 10th jog outdoors.", "2025-03-01"),
    ]
    v = temporal_aggregate_verdict(
        "How many weeks had passed since I recovered from the flu when I "
        "went on my 10th jog outdoors?", hits)
    assert v["kind"] == "interval"
    assert v["value"] is None
    assert v["reason"] == "no_anchors"
    assert v["n_dated_events"] == 2


def test_verdict_non_temporal_question():
    v = temporal_aggregate_verdict("What is my favourite colour?", [])
    assert v["kind"] is None
    assert v["reason"] == "not_temporal"
    assert v["value"] is None


def test_verdict_total_flags_span_less_sum():
    """A TOTAL over hits that carry no span bounds publishes the
    module's span-less sum (0) BUT the honesty diagnostic reports 0 bounded
    spans — a gold-admitting run must not read that 0 as measured."""
    hits = [
        _hit("e1", "Reading 'The Nightingale'.", "2025-01-01"),
        _hit("e2", "Listening to 'Sapiens'.", "2025-02-01"),
    ]
    v = temporal_aggregate_verdict(
        "How many weeks in total do I spent on reading and listening?", hits)
    assert v["kind"] == "total"
    assert v["value"] == 0
    assert v["n_span_bounded_events"] == 0


def test_verdict_total_and_span_diagnostic_share_the_resolver_input():
    """#2886: ``n_span_bounded_events`` is computed over the SAME dicts the
    resolver receives, so the two halves of the "span honesty" contract
    cannot disagree. A hit carrying both bounds is visible to BOTH: the
    published total is a real sum, not a zero-span sum credited as measured
    while the diagnostic reports a bounded event."""
    hits = [_hit("e1", "Reading 'The Nightingale'.", "2025-01-01")]
    hits[0]["start_date"] = "2025-01-01"
    hits[0]["end_date"] = "2025-01-08"
    v = temporal_aggregate_verdict(
        "How many weeks in total do I spent on reading and listening?", hits)
    assert v["kind"] == "total"
    assert v["n_span_bounded_events"] == 1
    # The resolver saw the same bounds, so the total is a measured sum.
    assert v["value"] is not None
    assert v["value"] > 0


def test_verdict_reversed_span_is_not_a_bounded_event():
    """A REVERSED span (``end < start``) is a data inconsistency the core's
    ``_span_days`` rejects (it contributes nothing), so it is not a bounded
    event here either — and a TOTAL over it is not a measured sum."""
    hits = [_hit("e1", "Reading 'The Nightingale'.", "2025-01-01")]
    hits[0]["start_date"] = "2025-01-08"
    hits[0]["end_date"] = "2025-01-01"
    v = temporal_aggregate_verdict(
        "How many weeks in total do I spent on reading and listening?", hits)
    assert v["kind"] == "total"
    assert v["value"] == 0
    assert v["n_span_bounded_events"] == 0


# ── (d) the census class is exercised through the arm ──────────────────────

def test_verdict_runs_over_all_12_census_class_qids():
    """The acceptance's named path over the 12 census rows, at the ARM level:
    every row classifies to a temporal shape and the verdict abstains (no
    resolved anchors) — it never publishes a guessed number."""
    rows = [r for r in _CENSUS["rows"] if r.get("cls") == "frequency/count"]
    assert len(rows) == 12
    candidates = [
        _hit("a", "a dated candidate event.", "2025-01-01"),
        _hit("b", "another dated candidate event.", "2025-02-01"),
    ]
    for row in rows:
        v = temporal_aggregate_verdict(row["question"], candidates)
        assert v["kind"] is not None, row["qid"]
        # The three date-arithmetic shapes abstain without resolved anchors.
        # The one summed-span row publishes the module's span-less sum (0):
        # the diagnostic records that no candidate hit carried both bounds, so
        # the 0 is auditable rather than read as a measured total.
        if v["kind"] == "total":
            assert v["value"] in (None, 0), (row["qid"], v["value"])
            assert v["n_span_bounded_events"] == 0, row["qid"]
        else:
            assert v["value"] is None, (row["qid"], v["value"])
            assert v["reason"] == "no_anchors", (row["qid"], v["reason"])
