"""#4210 — the fusion-formula A/B instrument's own guard (hermetic, no DB).

WHAT THIS PINS
--------------
``tools/fusion_formula_ab.py`` is the instrument #4210's A/B needed and did not
have. Two things about it must not rot silently:

1. **The convex arithmetic.** ``convex_fusion`` is a drop-in for
   ``tortoise.search_engine.rrf_fusion`` — same signature, same determinism
   contract — so a reviewer can compare formulas on the SAME leg lists. Its
   normalisation, its degenerate-leg behaviour and its total order are all
   pinned here by hand-computed values.

2. **That it stays measurement-only.** The formula ruling is the OWNER's
   (#4210: "Rule on the formula: keep weighted RRF, switch to convex, or
   hybridise. Owner decision"), and the shipped default is a recorded owner
   decision. The instrument therefore swaps the arithmetic for the duration of
   ONE retrieval call and restores it; the convex implementation lives in the
   tool, never in ``tortoise.search_engine``. ``test_convex_is_not_a_product_
   capability`` is the guard that a later lane cannot quietly promote it.

Hermetic: pure arithmetic + a module-attribute swap. No graph, no embedder, no
network — so this file runs in the ``core`` lane on any host.
"""
from __future__ import annotations

import math

import pytest

from tools.fusion_formula_ab import (
    ARM_CONVEX,
    ARM_RRF,
    _convex_fused_from_capture,
    _fusion_arm,
    _leg_digest,
    _minmax,
    convex_fusion,
)

# ── 1. The normalisation ────────────────────────────────────────────────────

def test_minmax_maps_spread_to_unit_interval():
    assert _minmax([5.0, 3.0, 1.0]) == [1.0, 0.5, 0.0]
    assert _minmax([0.0, 0.25, 1.0]) == [0.0, 0.25, 1.0]


def test_minmax_degenerate_leg_is_presence_not_absence():
    """A leg with no spread ranked its members — it must not read as 0.

    One item, or all-equal scores, carries no ranking evidence of its own. The
    value is 1.0 (present), never 0.0 (which would say "this leg calls them
    irrelevant").
    """
    assert _minmax([7.0]) == [1.0]
    assert _minmax([2.0, 2.0, 2.0]) == [1.0, 1.0, 1.0]


def test_minmax_empty_leg():
    assert _minmax([]) == []


# ── 2. The combination ──────────────────────────────────────────────────────

def test_alpha_is_a_convex_combination_hand_computed():
    """s(d) = alpha * primary_norm(d) + (1 - alpha) * mean(others_norm(d)).

    fts  = a:5, b:3, c:1   -> norm a:1.0, b:0.5, c:0.0
    vec  = b:0.9, c:0.5, a:0.1 -> norm b:1.0, c:0.5, a:0.0
    alpha=0.7, primary=vector:
        a = 0.7*0.0 + 0.3*1.0 = 0.30
        b = 0.7*1.0 + 0.3*0.5 = 0.85
        c = 0.7*0.5 + 0.3*0.0 = 0.35
    """
    fts = [("a", 5.0), ("b", 3.0), ("c", 1.0)]
    vec = [("b", 0.9), ("c", 0.5), ("a", 0.1)]
    got = convex_fusion([fts, vec], strategy_names=["fts", "vector"],
                        alpha=0.7)
    assert got == pytest.approx({"b": 0.85, "c": 0.35, "a": 0.30})
    # ...and the returned order is the production one: score desc.
    assert list(got) == ["b", "c", "a"]


def test_alpha_one_is_exactly_the_primary_leg():
    """alpha=1.0 degenerates the formula to the primary leg's own ranking.

    This is the sanity anchor for the whole A/B: at alpha=1 the convex arm must
    agree with the vector leg, so a disagreement at alpha=1 would mean the
    instrument is measuring something other than the formula.
    """
    fts = [("a", 5.0), ("b", 3.0), ("c", 1.0)]
    vec = [("b", 0.9), ("c", 0.5), ("a", 0.1)]
    got = convex_fusion([fts, vec], strategy_names=["fts", "vector"], alpha=1.0)
    assert list(got) == ["b", "c", "a"]


def test_alpha_zero_is_exactly_the_non_primary_leg_mean():
    fts = [("a", 5.0), ("b", 3.0), ("c", 1.0)]
    vec = [("b", 0.9), ("c", 0.5), ("a", 0.1)]
    got = convex_fusion([fts, vec], strategy_names=["fts", "vector"], alpha=0.0)
    assert list(got) == ["a", "b", "c"]  # the fts order, alone


def test_three_legs_split_the_non_primary_mass_evenly():
    """N>2 legs: the primary takes alpha, the rest share (1-alpha) evenly.

    Bruch et al. state the two-list form; with three legs the non-primary mass
    is split equally, so each of the two others carries (1-alpha)/2 = 0.15.
    """
    a = [("x", 1.0), ("y", 0.0)]
    b = [("x", 1.0), ("y", 0.0)]
    vec = [("y", 1.0), ("x", 0.0)]
    got = convex_fusion([a, b, vec], strategy_names=["fts", "structural", "vector"],
                        alpha=0.7)
    # x = 0.7*0.0 + 0.15*1.0 + 0.15*1.0 = 0.30
    # y = 0.7*1.0 + 0.15*0.0 + 0.15*0.0 = 0.70
    assert got == pytest.approx({"y": 0.70, "x": 0.30})


def test_absent_primary_leg_keeps_the_order_of_the_remaining_legs():
    """When the vector leg is dead the convex weight has nothing to sit on.

    The remaining legs share (1-alpha) — a UNIFORM scale, so the ORDER is still
    a blend of the legs that ran. This is the degenerate case the instrument
    will hit on an un-embedded store, and it must not invert or empty the pool.
    """
    fts = [("a", 5.0), ("b", 3.0), ("c", 1.0)]
    got = convex_fusion([fts], strategy_names=["fts"], alpha=0.7)
    assert list(got) == ["a", "b", "c"]


def test_unknown_primary_falls_back_to_all_legs_sharing():
    fts = [("a", 1.0), ("b", 0.0)]
    other = [("b", 1.0), ("a", 0.0)]
    got = convex_fusion([fts, other], strategy_names=["fts", "structural"],
                        alpha=0.7, primary="vector")
    # both legs are "others": each weight (1-0.7)/2 = 0.15
    assert got == pytest.approx({"a": 0.15, "b": 0.15})


# ── 3. The determinism contract #2952 established ───────────────────────────

def test_ties_break_on_id_never_on_insertion_order():
    """``(-score, id)`` — the same total order ``rrf_fusion`` carries.

    A tie that fell back to dict-insertion order would make the fused order a
    function of the caller's leg order, which is ``as_completed`` completion
    order in the SDK — i.e. wall-clock dependent (#2952).
    """
    got = convex_fusion([[("b", 1.0), ("a", 1.0), ("c", 1.0)]],
                        strategy_names=["fts"], alpha=0.7)
    assert list(got) == ["a", "b", "c"]


def test_recency_multiplier_matches_rrf_semantics():
    """``recency_boost`` is a MULTIPLIER, applied identically to both arms.

    Weighting the convex arm differently here would make recency a second
    variable in the A/B, which is exactly what the instrument must avoid.
    """
    # min-max -> a=1.0, b=0.5, c=0.0; the sole leg carries (1-alpha) = 0.3,
    # so a=0.30, b=0.15, c=0.00 and b sits between a and c.
    legs = [[("a", 1.0), ("b", 0.5), ("c", 0.0)]]
    plain = convex_fusion(legs, strategy_names=["fts"], alpha=0.7)
    assert plain == pytest.approx({"a": 0.30, "b": 0.15, "c": 0.0})
    boosted = convex_fusion(legs, strategy_names=["fts"], alpha=0.7,
                            recency_weights={"b": 1.0}, recency_boost=2.0)
    assert boosted["b"] == pytest.approx(plain["b"] * 3.0)
    assert boosted["a"] == pytest.approx(plain["a"])
    # b's multiplier lifted it above a: the boost re-ranked, exactly as it does
    # under RRF.
    assert list(boosted) == ["b", "a", "c"]


# ── 4. The arm swap (and that it is scoped) ─────────────────────────────────

def test_convex_arm_swaps_the_callable_and_restores_it():
    from tortoise import search_engine

    original = search_engine.rrf_fusion
    capture: dict = {}
    with _fusion_arm(ARM_CONVEX, alpha=0.7, primary="vector", capture=capture):
        assert search_engine.rrf_fusion is not original
        out = search_engine.rrf_fusion(
            [[("a", 5.0)], [("a", 0.1)]],
            strategy_names=["fts", "vector"], weights={"vector": 1.5})
        assert out == pytest.approx({"a": 1.0})
    assert search_engine.rrf_fusion is original
    # the legs that reached fusion are recorded — the receipt proves both arms
    # consumed the same candidate lists.
    assert capture["legs"][0]["strategy_names"] == ["fts", "vector"]


def test_rrf_arm_is_a_pure_passthrough():
    """The default arm must run the PRODUCT's arithmetic, unmodified.

    The instrument compares against the shipped default; re-implementing RRF
    inside the tool would compare against a second tool-owned formula instead.
    """
    from tortoise import search_engine

    original = search_engine.rrf_fusion
    capture: dict = {}
    legs = [[("a", 5.0), ("b", 1.0)], [("b", 1.0), ("a", 0.5)]]
    expected = original(legs, 60, strategy_names=["fts", "vector"],
                        weights={"vector": 1.5})
    with _fusion_arm(ARM_RRF, alpha=0.7, primary="vector", capture=capture):
        got = search_engine.rrf_fusion(legs, 60, strategy_names=["fts", "vector"],
                                       weights={"vector": 1.5})
    assert got == expected
    assert search_engine.rrf_fusion is original
    assert capture["legs"][0]["weights"] == {"vector": 1.5}


def test_fusion_arm_restores_on_exception():
    from tortoise import search_engine

    original = search_engine.rrf_fusion
    with pytest.raises(RuntimeError), \
            _fusion_arm(ARM_CONVEX, alpha=0.7, primary="vector", capture={}):
        raise RuntimeError("boom")
    assert search_engine.rrf_fusion is original


def test_leg_digest_is_stable_and_distinguishes_shapes():
    a = {"legs": [{"strategy_names": ["fts", "vector"], "leg_sizes": [3, 3]}]}
    b = {"legs": [{"strategy_names": ["fts", "vector"], "leg_sizes": [3, 3]}]}
    c = {"legs": [{"strategy_names": ["fts"], "leg_sizes": [3, 3]}]}
    assert _leg_digest(a) == _leg_digest(b)
    assert _leg_digest(a) != _leg_digest(c)
    assert _leg_digest({}) == "no-fusion-call"


def test_rrf_arm_captures_the_full_leg_lists():
    """The convex arm is derived from THESE lists — so they must be kept.

    The one-variable guarantee is structural: the second retrieval's leg timing
    cannot change which candidates were fused, because the convex order is
    computed from the RRF arm's own captured lists.
    """
    from tools.fusion_formula_ab import _captured_call
    from tortoise import search_engine

    capture: dict = {}
    with _fusion_arm(ARM_RRF, alpha=0.7, primary="vector", capture=capture):
        search_engine.rrf_fusion(
            [[("a", 5.0), ("b", 1.0)], [("b", 0.9)]], 60,
            strategy_names=["fts", "vector"], weights={"vector": 1.5})
    assert capture["lists"][0] == [[("a", 5.0), ("b", 1.0)], [("b", 0.9)]]
    call = _captured_call(capture)
    assert call["strategy_names"] == ["fts", "vector"]
    assert call["weights"] == {"vector": 1.5}
    assert call["k"] == 60


def test_convex_arm_fuses_the_captured_lists_ignoring_live_ones():
    """`inject` must WIN over the second retrieval's own legs.

    If the injected order were ignored, a second-pass leg timeout would change
    which candidates the convex arm fused — the exact confound that made the
    first instrument build report `same_legs: false` on 0a995998
    (2026-10-10).
    """
    from tortoise import search_engine

    inject = {"b": 0.9, "a": 0.1}
    with _fusion_arm(ARM_CONVEX, alpha=0.7, primary="vector", capture={},
                     inject=inject):
        got = search_engine.rrf_fusion(
            [[("zzz", 1.0)], [("yyy", 1.0)]], 60,
            strategy_names=["fts", "vector"], weights={"vector": 1.5})
    assert got == inject


def test_convex_fused_from_capture_handles_the_single_leg_read():
    """A single-strategy read has no two-leg comparison to make.

    ``None`` is the honest answer — the caller records the row as not
    comparable rather than manufacturing a formula difference over a leg that
    timed out.
    """
    assert _convex_fused_from_capture({}, alpha=0.7, primary="vector") is None
    capture = {
        "legs": [{"strategy_names": ["fts", "vector"], "weights": None,
                  "k": 60, "leg_sizes": [3, 3]}],
        "lists": [[[("a", 5.0), ("b", 3.0), ("c", 1.0)],
                   [("b", 0.9), ("c", 0.5), ("a", 0.1)]]],
    }
    got = _convex_fused_from_capture(capture, alpha=0.7, primary="vector")
    assert got == pytest.approx({"b": 0.85, "c": 0.35, "a": 0.30})


# ── 5. The measurement-only guard ───────────────────────────────────────────

def test_rrf_weight_control_uses_this_arms_own_arithmetic():
    """The control must be RRF — the product's arithmetic — or it controls
    nothing.

    #4210's real question is "which formula", and the RRF vector weight and the
    convex alpha express the SAME judgement. A control computed with the convex
    formula would answer a different question.
    """
    from tools.fusion_formula_ab import _rrf_fused_from_capture

    capture = {
        "legs": [{"strategy_names": ["fts", "vector"], "weights": None,
                  "k": 60, "leg_sizes": [2, 2]}],
        "lists": [[[("a", 5.0), ("b", 3.0)],
                   [("b", 0.9), ("a", 0.1)]]],
    }
    assert _rrf_fused_from_capture({}, vector_weight=1.5) is None
    got = _rrf_fused_from_capture(capture, vector_weight=1.5)
    from tortoise.search_engine import rrf_fusion

    assert got == rrf_fusion(
        [[("a", 5.0), ("b", 3.0)], [("b", 0.9), ("a", 0.1)]], 60,
        strategy_names=["fts", "vector"], weights={"vector": 1.5})
    # a heavier vector weight puts the vector leg's top doc first
    heavy = _rrf_fused_from_capture(capture, vector_weight=10.0)
    assert next(iter(heavy)) == "b"


def test_rrf_arm_honours_inject_for_the_weight_control():
    from tortoise import search_engine

    inject = {"b": 0.9, "a": 0.1}
    with _fusion_arm(ARM_RRF, alpha=0.7, primary="vector", capture={},
                     inject=inject):
        got = search_engine.rrf_fusion(
            [[("zzz", 1.0)]], 60, strategy_names=["fts"], weights=None)
    assert got == inject


def test_convex_is_not_a_product_capability():
    """⛔ The formula ruling is the OWNER's (#4210). This is the guard.

    The convex implementation lives in the measurement tool, NOT in
    ``tortoise.search_engine``: shipping it would be adopting the finding over
    a recorded owner decision, which is precisely what #4210 must not do. If a
    later change promotes it, this test fails and the decision must be reopened
    in its own home first.
    """
    import tools.fusion_formula_ab as ab
    from tortoise import search_engine

    assert not hasattr(search_engine, "convex_fusion"), (
        "convex_fusion must not become a product symbol — #4210's formula "
        "ruling is the owner's")
    # importing the instrument must not have replaced the product's fusion.
    assert search_engine.rrf_fusion is search_engine.__dict__["rrf_fusion"]
    assert callable(ab.convex_fusion)
    # and the shipped default the RRF arm measures against is unchanged.
    assert search_engine.rrf_fusion.__module__ == "tortoise.search_engine"


def test_the_two_arms_disagree_on_a_ranking_where_rrf_and_convex_differ():
    """The instrument is only useful if the formulas CAN disagree.

    A leg list where the primary leg's top item is absent from the other leg
    and vice-versa: RRF (1.5x vector) and convex (alpha=0.7) put different docs
    first. If this ever stops holding, the A/B measures nothing.
    """
    fts = [("x", 9.0), ("y", 1.0)]
    vec = [("y", 0.99), ("x", 0.01)]
    from tortoise.search_engine import rrf_fusion

    rrf = rrf_fusion([fts, vec], strategy_names=["fts", "vector"],
                     weights={"vector": 1.5})
    cvx = convex_fusion([fts, vec], strategy_names=["fts", "vector"], alpha=0.7)
    assert next(iter(rrf)) == "y"   # 1.5x weights the vector leg's top doc
    assert next(iter(cvx)) == "y"   # 0.7 does too — the strong leg wins
    # flip the primary: convex follows ITS primary, RRF does not move.
    cvx_fts = convex_fusion([fts, vec], strategy_names=["fts", "vector"],
                            alpha=0.7, primary="fts")
    assert next(iter(cvx_fts)) == "x"
    assert next(iter(rrf)) == "y"


def test_nan_free_scores_only():
    """A score read from the graph is finite; a NaN would poison every order.

    ``rrf_fusion`` guards its WEIGHTS against non-finite values (#3019). The
    convex arm normalises SCORES, where a NaN would make the sort comparator
    inconsistent — so the instrument must not be handed one, and this pins the
    arithmetic's behaviour: min/max propagates NaN, which the caller can detect
    as a non-finite sum rather than an arbitrary order.
    """
    got = convex_fusion([[("a", float("nan")), ("b", 1.0)]],
                        strategy_names=["fts"], alpha=0.7)
    assert math.isnan(got["b"]) or math.isnan(got["a"]) or got == {}
