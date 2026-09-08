"""#2567 (C3-1 #2519) — coverage-completeness loop: hermetic RULE tests.

Pure-function tests of the product rules in ``tortoise/coverage_loop.py``
(census gap semantics, date-constraint detection, the additive merge, the
session-diverse window discipline, the reserved-slot expansion guard). No
graph, no API, no env mutation — runs on any lane (embedded or docker).

The docker-lane E2E (tests/test_coverage_loop.py) proves the composed loop
against a live FalkorDB; this file pins the RULES the composition is built
from, so a rule regression fails fast and hermetically:
  * fire/no-fire on the census gap (seeded + under-covered fires; open-
    ended / unseeded / fully-covered never fire),
  * the ≤1-extra-pass budget is a constant AND the no-op alias guard
    skips a pass that cannot add recall,
  * the merge is additive + second-pass-ordered (base-only survivors keep
    base order; base hits re-found by the pass keep their pass rank),
  * the session-diverse window discipline: a same-session flood can never
    crowd cross-session evidence out of the guard window (and reordering
    never drops an item — additive by construction),
  * the sparse reserved-slot contract the second pass composes with:
    original query tokens keep their OR slots; an alias pool fills only
    the bounded expansion tail.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from tortoise.coverage_loop import (
    DEFAULT_LOOP_GUARD_WINDOW,
    DEFAULT_LOOP_SESSION_CAP,
    LOOP_MAX_EXTRA_PASSES,
    LoopFacet,
    _aliases_add_recall,
    coverage_gap,
    facet_date_constraint,
    merge_expansion_order,
    session_diverse_order,
)
from tortoise.sparse import DEFAULT_MAX_EXPANSION_TERMS, build_or_query

QUESTION = "how much did the road bike repairs cost me in total"
ANCHOR = "road bike repairs"


def _hit(hid: str, session: str) -> dict:
    return {"id": hid, "session_id": session}


def _facet(*sessions: str) -> LoopFacet:
    return LoopFacet(kind="entity", key=f"entity:{ANCHOR}", name=ANCHOR,
                     span=frozenset(sessions))


# ── the census gap: fire/no-fire rules ────────────────────────────────────

def test_gap_fires_on_seeded_undercovered_facet():
    """A facet seeded in the guard window but not fully covered by it fires
    (partial evidence — the miss class the loop exists to repair)."""
    facets = [_facet("A", "B")]
    pool = [_hit("1", "A"), _hit("2", "A"), _hit("3", "A"),
            _hit("4", "A"), _hit("5", "A")]
    missing = coverage_gap(facets, pool, window=5)
    assert len(missing) == 1 and missing[0].key == f"entity:{ANCHOR}"


def test_gap_open_ended_never_fires():
    """No countable facet (open-ended query) → no fire, by construction."""
    assert coverage_gap([], [_hit("1", "A")], window=5) == []
    assert coverage_gap([], [], window=5) == []


def test_gap_unseeded_facet_never_fires():
    """A facet whose span sessions are NOT in the top window at all is
    index noise (the pool is not about it) — never fires."""
    facets = [_facet("X", "Y")]
    pool = [_hit("1", "A"), _hit("2", "B"), _hit("3", "C")]
    assert coverage_gap(facets, pool, window=5) == []


def test_gap_fully_covered_span_never_fires():
    """A span fully inside the window (complete evidence — e.g. a single-
    session subject) never fires: coverage is complete."""
    facets = [_facet("A")]
    pool = [_hit("1", "A"), _hit("2", "B"), _hit("3", "C")]
    assert coverage_gap(facets, pool, window=5) == []


def test_gap_empty_pool_never_fires():
    """No retrieved evidence → nothing is seeded → no fire (and no crash)."""
    assert coverage_gap([_facet("A")], [], window=5) == []


def test_gap_returns_all_missing_facets_in_census_order():
    """The check reports EVERY seeded-but-under-covered facet (the ONE
    expand pass then unions their vocabulary — never a per-facet loop)."""
    bike = _facet("A", "B")
    kitchen = LoopFacet(kind="entity", key="entity:kitchen renovation",
                        name="kitchen renovation", span=frozenset({"C", "D"}))
    pool = [_hit("1", "A"), _hit("2", "C"), _hit("3", "X"), _hit("4", "Y"),
            _hit("5", "Z")]
    missing = coverage_gap([bike, kitchen], pool, window=5)
    assert [m.key for m in missing] == [
        "entity:road bike repairs", "entity:kitchen renovation"]


def test_gap_window_clamped_to_pool():
    """A pool smaller than the guard window checks the whole pool (the E2E
    one-hit pool shape): coverage_gap never indexes past the pool."""
    facets = [_facet("A", "B")]
    assert len(coverage_gap(facets, [_hit("1", "A")], window=5)) == 1
    assert coverage_gap([_facet("A")], [_hit("1", "A")], window=5) == []


# ── the date-constraint seam (date-range facet detection) ────────────────

def test_facet_date_constraint_detection():
    kind, start, end = facet_date_constraint(
        "between 2026-03-01 and 2026-04-01 what did they say")
    assert (kind, start, end) == ("interval", "2026-03-01", "2026-04-01")
    kind, start, _end = facet_date_constraint("what happened 3 weeks ago")
    assert kind == "recency" and start == "21"
    assert facet_date_constraint(QUESTION) == (None, None, None)


# ── the budget: ≤1 extra pass + the no-op alias guard ────────────────────

def test_loop_max_extra_passes_is_one():
    """The hard iteration bound is a pinned constant: the loop may run at
    most ONE extra retrieval pass beyond the one-shot (the §7 iteration-
    cost-creep guard; the §8 census records it per outcome)."""
    assert LOOP_MAX_EXTRA_PASSES == 1


def test_alias_guard_skips_pass_that_adds_no_recall():
    """An alias pool that tokenizes to ONLY the original query's tokens
    cannot add recall under the reserved-slot OR contract — the expansion
    must not waste the extra pass (C2's no-op guard)."""
    assert not _aliases_add_recall(
        QUESTION, ("road bike repairs cost",))
    assert _aliases_add_recall(
        QUESTION, (ANCHOR, "extra charge for wheels saturday service fee"))


# ── the merge: additive + second-pass-ordered (slot reservation) ─────────

def test_merge_is_additive_and_second_pass_ordered():
    """Every base hit survives (additive); base hits the second pass re-
    found lead in pass order; base-only survivors keep their base ranks."""
    pool = [_hit("s1", "A"), _hit("d1", "D"), _hit("d2", "D")]
    added = [_hit("s9", "B")]           # recovery hit surfaced by the pass
    merged = merge_expansion_order(pool, added, ["s1", "s9"])
    assert [h["id"] for h in merged] == ["s1", "s9", "d1", "d2"]
    assert {h["id"] for h in merged} == {"s1", "s9", "d1", "d2"}


def test_merge_dedupes_across_the_union():
    """An id present in BOTH the base pool and the expansion joins once."""
    pool = [_hit("s1", "A"), _hit("d1", "D")]
    merged = merge_expansion_order(pool, [], ["s1", "s1", "s9"])
    assert [h["id"] for h in merged] == ["s1", "d1"]


# ── the merge discipline: session-diverse guard window ───────────────────

def test_same_session_flood_never_crowds_cross_session_evidence():
    """A monopolizing session's points (never session-capped by dedup) must
    not crowd the other sessions' evidence out of the guard window: with a
    session-A flood + session-B evidence + session-C recovery evidence, the
    top window holds A (capped) AND B AND C — the flood is deferred, never
    the cross-session evidence. Additive: nothing is dropped."""
    items = [_hit(f"a{i}", "A") for i in range(8)] \
        + [_hit("b1", "B"), _hit("b2", "B")] \
        + [_hit("c1", "C")]
    order = session_diverse_order(items, window=5, per_session_cap=2)
    window_sessions = [h["session_id"] for h in order[:5]]
    assert len(order) == len(items), "reordering never drops an item"
    assert set(window_sessions) >= {"B", "C"}, (
        "cross-session evidence (B, C) must enter the window over the "
        "session-A flood")
    # A capped at 2 inside the window — the flood is deferred past it.
    assert window_sessions.count("A") <= 2


def test_session_diversity_preserves_within_session_stability():
    """The discipline is stable within a session: a session's own hits keep
    their original relative order whether they land in the window or the
    deferred tail."""
    items = [_hit("a1", "A"), _hit("a2", "A"), _hit("a3", "A"),
             _hit("a4", "A"), _hit("b1", "B"), _hit("a5", "A")]
    order = session_diverse_order(items, window=5, per_session_cap=1)
    a_order = [h["id"] for h in order if h["session_id"] == "A"]
    assert a_order == ["a1", "a2", "a3", "a4", "a5"]


def test_cap_yields_to_completeness_when_window_cannot_fill():
    """A pool that is ONE session's alone still surfaces its own evidence —
    the cap is a diversity guard, never a completeness blocker (such pools
    never fire the loop anyway: their spans are fully covered)."""
    items = [_hit("a1", "A"), _hit("a2", "A"), _hit("a3", "A"),
             _hit("a4", "A"), _hit("a5", "A")]
    order = session_diverse_order(items, window=5, per_session_cap=2)
    assert len(order) == 5 and {h["id"] for h in order[:5]} == {
        "a1", "a2", "a3", "a4", "a5"}


def test_guard_defaults_pinned():
    """The guard window = the all-or-nothing recall window (5); the cap = 2
    (a session can never own more than 2 of the top-5)."""
    assert DEFAULT_LOOP_GUARD_WINDOW == 5
    assert DEFAULT_LOOP_SESSION_CAP == 2


# ── the reserved-slot OR contract the expand composes with ───────────────

def test_expansion_terms_never_displace_original_tokens():
    """The loop's second pass inherits the A4/C2 reserved-slot contract: an
    injected alias pool (the missing facet's vocabulary) fills ONLY the
    bounded expansion tail AFTER the original query tokens' slots — the
    original tokens keep their OR slots and can never be crowded out."""
    original = build_or_query(QUESTION)
    original_tokens = original.split("|")
    aliases = [ANCHOR, "extra charge for wheels saturday service fee"]
    expanded = build_or_query(QUESTION, expansion_terms=aliases)
    expanded_tokens = expanded.split("|")
    assert set(original_tokens) <= set(expanded_tokens)
    assert expanded_tokens[:len(original_tokens)] == original_tokens
    assert len(expanded_tokens) <= 12 + DEFAULT_MAX_EXPANSION_TERMS
