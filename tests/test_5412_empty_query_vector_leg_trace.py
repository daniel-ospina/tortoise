"""#5412 — the empty-query-vector guard is an exit branch, so it must record one.

``run_vector_query``'s docstring promises a per-leg ``leg_trace`` entry *at
every exit branch* (R3 #1542 D4), and the trace is not display-only: it is the
INPUT to a decision. :func:`declared_degraded_read` derives
``vector_leg_unavailable`` / ``missing_legs`` / ``reason`` from it, and
:func:`require_hybrid_read` refuses a read on that marker.

The degenerate-input guard (``if not query_vec: return []``) emitted no entry,
so a caller that supplied no query vector was read as a leg that was ABSENT —
``reason="leg_absent"``, i.e. the leg blamed for not existing — when the truth
is that the caller asked it nothing. A new reason is the fix; it must NOT
reuse or move any reason in the #2952 vocabulary, and it must not read as a
healthy run (that would launder a keyword-only read into "hybrid").

Mutations this file reds:
- delete the ``_record(...)`` call from the guard → the trace is empty and
  every assertion below fails with ``leg_trace_unavailable`` / no entry.
- record ``ran=True`` (or ``degraded=True``) for the guard → the "nothing ran,
  nothing failed" assertions and the metering assertion fail.
"""
from __future__ import annotations

import pytest

from tortoise import retrieval_metering, search_engine
from tortoise.search_engine import (
    declared_degraded_read,
    require_hybrid_read,
    run_vector_query,
)


def _guard_trace(query_vec) -> list[dict]:
    """Run the guard with a trace and return the trace it produced.

    ``graph=None``: the guard precedes every graph use (``test_5404``'s own
    ordering pin), so a graph stub would prove nothing here.
    """
    trace: list[dict] = []
    assert run_vector_query(None, query_vec, leg_trace=trace) == []
    return trace


@pytest.mark.parametrize("query_vec", [[], None, ()], ids=["list", "none", "tuple"])
def test_empty_query_vector_records_exactly_one_entry(query_vec):
    """The guard records ONE entry — not zero, and no fabricated run."""
    trace = _guard_trace(query_vec)

    assert len(trace) == 1, (
        "the empty-query-vector guard is an exit branch, so the opt-in trace "
        f"must hold exactly one entry; got {trace!r}")
    entry = trace[0]
    assert entry["leg"] == "vector"
    # Nothing was read and nothing failed: the caller asked no dense question.
    assert entry["ran"] is False
    assert entry["degraded"] is False
    assert entry["count"] == 0
    assert entry["reason"] == search_engine.NO_QUERY_VECTOR
    # No path was taken, so no mechanism may be claimed — the key's own
    # contract (#4999: absent when no path was taken, e.g. ``breaker_open``).
    assert "mechanism" not in entry


def test_the_reason_is_its_own_term_not_a_reused_one():
    """A distinct cause gets a distinct term — the #2952 vocabulary is frozen."""
    trace = _guard_trace([])
    reason = trace[0]["reason"]

    assert reason == "no_query_vector"
    assert reason not in {
        # existing reasons must not be silently repurposed (#4999 froze the
        # mechanism vocabulary; #2952 owns these).
        "ok", "empty_results", "no_embeddings", "index_missing",
        "query_failed", "breaker_open", "timeout", "below_relevance_floor",
        # the two DECLARATION-side fallbacks the guard used to be misread as:
        "leg_absent", "leg_trace_unavailable",
    }


def test_declaration_names_the_real_cause_not_leg_absent():
    """``declared_degraded_read`` reports the guard's reason, not absence.

    Before the fix the trace had NO vector entry, so the declaration either
    fell to ``leg_trace_unavailable`` (guard-only trace) or ``leg_absent``
    (a text leg alongside) — both name a leg that is missing, not a question
    that was never asked.
    """
    marker = declared_degraded_read(_guard_trace([]))

    assert marker is not None, "an empty-query-vector read cannot be hybrid"
    assert marker["reason"] == "no_query_vector"
    # Fail-closed is preserved and stated: no dense rows were contributed.
    assert marker["degraded_read"] is True
    assert marker["hybrid"] is False
    assert marker[search_engine.VECTOR_LEG_UNAVAILABLE] is True
    assert marker["missing_legs"] == ["vector"]


def test_require_hybrid_read_refuses_with_the_real_reason():
    """The fail-loud gate refuses — and its reason is the truthful one."""
    from tortoise.exceptions import HybridReadUnavailableError

    with pytest.raises(HybridReadUnavailableError) as ei:
        require_hybrid_read(_guard_trace([]))

    assert ei.value.reason == "no_query_vector"


def test_a_text_leg_alongside_does_not_restore_leg_absent():
    """The reported symptom's OTHER half: a text leg present.

    A real ``recall_state``-shaped trace carries an ``fts`` entry next to the
    vector one, and the declaration's ``elif entries: reason = "leg_absent"``
    is what the guard's silence used to fall into there. Pinning only the
    guard-only trace leaves that path green: an edit that made
    ``leg_absent`` win whenever a text leg is present would still pass the
    other tests while returning ``leg_absent`` for every product read.
    """
    from tortoise.exceptions import HybridReadUnavailableError

    trace = [{"leg": "fts", "ran": True, "degraded": False,
              "reason": "ok", "count": 2}, *_guard_trace([])]

    marker = declared_degraded_read(trace)
    assert marker is not None
    assert marker["reason"] == "no_query_vector", (
        "a text leg alongside must not restore leg_absent — the vector leg "
        f"was not absent, it was not asked; got {marker['reason']!r}")
    assert marker["reason"] != "leg_absent"
    assert marker[search_engine.VECTOR_LEG_UNAVAILABLE] is True

    with pytest.raises(HybridReadUnavailableError) as ei:
        require_hybrid_read(trace)
    assert ei.value.reason == "no_query_vector"


def test_guard_records_a_leg_note_that_counts_nothing(monkeypatch):
    """Nothing ran and nothing failed, so the meter must not move.

    ``_record`` notes the leg BEFORE the trace check, so the guard's entry now
    reaches the tally too. A ``ran=True`` record would inflate ``leg_runs``
    with a read that never happened; a ``degraded=True`` one would count a
    degradation for a leg that was never asked. Neither is true here.
    """
    notes: list[dict] = []

    def _capture(leg, *, ran, degraded):
        notes.append({"leg": leg, "ran": ran, "degraded": degraded})

    monkeypatch.setattr(retrieval_metering, "note_leg", _capture)

    _guard_trace([])

    assert notes == [{"leg": "vector", "ran": False, "degraded": False}], (
        f"the guard must note a ran=False/degraded=False leg; got {notes!r}")
