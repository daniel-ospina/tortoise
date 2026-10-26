"""#2976 — the temporal retrieval leg (hermetic, no DB, no model, no I/O).

Pins the two halves of the fix and the no-op invariant:

  1. ``detect_time_constraint`` now fires on the EVENT-referenced
     ordering/comparison shapes the LongMemEval temporal class actually uses
     ("which happened first, the X or the Y", "how many days passed between
     A and B", "most recently", "last Saturday") — pre-#2976 these were 34/55
     ``None``, so the time machinery never ran. It also recovers the compared
     anchor phrases.
  2. ``tortoise.temporal_leg.temporal_leg_order`` builds the leg: best content
     match per anchor (co-present coverage) + date/session spread, in
     SEMANTIC rank order (never date-sorted), excluding undated candidates.
  3. The fusion is additive: an empty leg leaves the existing RRF order
     untouched, and a question with no temporal constraint produces no leg.

The regression surface (the pre-#2976 shapes) stays pinned in
``tests/test_temporal_constraint.py``; this file only adds.
"""
from __future__ import annotations

import inspect

from tools.longmem_eval.retrieve import (
    TimeConstraint,
    detect_time_constraint,
    retrieve_for_question,
)
from tortoise.search_engine import rrf_fusion
from tortoise.temporal_leg import (
    DEFAULT_TEMPORAL_LEG_BUCKET_CAP,
    DEFAULT_TEMPORAL_LEG_LIMIT,
    content_tokens,
    effective_promotion_budget,
    temporal_leg_empty_reason,
    temporal_leg_fusion_order,
    temporal_leg_order,
)

# ── helpers ──────────────────────────────────────────────────────────────────


def _hit(pid: str, date: str, session: str, content: str) -> dict:
    return {"id": pid, "session_date": date, "session_id": session,
            "content": content}


# ── 1. detector: event-referenced ordering/comparison shapes ─────────────────

#: Real LongMemEval temporal-reasoning questions (verbatim from the
#: longmemeval_s_cleaned dataset) that the pre-#2976 detector classified None.
_EVENT_ORDERING_QUESTIONS = [
    "Which item did I purchase first, the dog bed for Max or the training "
    "pads for Luna?",
    "Which event happened first, my cousin's wedding or Michael's "
    "engagement party?",
    "Which mode of transport did I use most recently, a bus or a train?",
    "Which device did I set up first, the smart thermostat or the mesh "
    "network system?",
    "Who graduated first, second and third among Emma, Rachel and Alex?",
    "What is the order of the three trips I took in the past three months, "
    "from earliest to latest?",
    "How many days passed between the day I started watering my herb garden "
    "and the day I harvested my first batch of fresh herbs?",
    "How many weeks passed between the day I bought my new tennis racket "
    "and the day I received it?",
    "How many weeks in total do I spent on reading 'The Nightingale' and "
    "listening to 'Sapiens: A Brief History of Humankind' and 'The Power'?",
    "How many days before my best friend's birthday party did I order her "
    "gift?",
    "How long had I been a member of 'Book Lovers Unite' when I attended "
    "the meetup?",
    "What was the airline that I flied with on Valentine's day?",
    "Who did I meet with during the lunch last Tuesday?",
]


def test_event_referenced_questions_detected():
    kinds = {q: detect_time_constraint(q).kind
             for q in _EVENT_ORDERING_QUESTIONS}
    missed = [q for q, k in kinds.items() if k != "ordering"]
    assert not missed, missed


def test_or_comparison_recovers_both_anchors():
    c = detect_time_constraint(
        "Which item did I purchase first, the dog bed for Max or the training "
        "pads for Luna?")
    assert c.kind == "ordering"
    assert c.anchors == ("the dog bed for max", "the training pads for luna")


def test_between_recovers_both_anchors():
    c = detect_time_constraint(
        "How many days passed between the day I started watering my herb "
        "garden and the day I harvested my first batch of fresh herbs?")
    assert c.kind == "ordering"
    assert len(c.anchors) == 2
    assert "herb garden" in c.anchors[0]
    assert "fresh herbs" in c.anchors[1]


def test_before_recovers_the_reference_anchor():
    c = detect_time_constraint(
        "How many days before my best friend's birthday party did I order her "
        "gift?")
    assert c.kind == "ordering"
    assert c.anchors == ("my best friend's birthday party",)


def test_comparison_without_superlative_word_still_fires():
    # "which … A or B" with no first/earlier token
    c = detect_time_constraint(
        "Which bike did I fixed or serviced the past weekend?")
    assert c.kind == "ordering"


def test_recency_and_interval_precedence_unchanged():
    # numeric recency still wins (additive change must not shadow it)
    assert detect_time_constraint("She told me 5 days ago.").kind == "recency"
    assert detect_time_constraint(
        "Between June 1 and June 15, what happened?",
        default_year=2025).kind == "interval"
    # an explicit interval that ALSO carries an ordering word must stay an
    # interval — the #2976 ordering regexes would otherwise shadow it and
    # emit garbage anchors (review P1)
    c = detect_time_constraint(
        "Between June 1 and June 15, what happened after the meeting?",
        default_year=2025)
    assert c.kind == "interval"
    assert (c.start, c.end) == ("2025-06-01", "2025-06-15")
    assert c.anchors == ()
    assert detect_time_constraint(
        "What happened between 2025-06-01 and 2025-06-15 after that?"
    ).kind == "interval"
    # but an event-referenced "between A and B" (no dates) is still ordering
    assert detect_time_constraint(
        "How many days passed between the day I bought the racket and the "
        "day I received it?").kind == "ordering"


def test_non_date_between_does_not_crash_and_is_ordering():
    # review-caught P1: the interval regex must only accept MONTH names —
    # "between level 1 and level 3" reaches the ordering branch, never
    # strptime (pre-fix it raised ValueError and the question failed on
    # every retry)
    for q in ("How many days between level 1 and level 3 did I train?",
              "How many days passed between week 1 and week 3 of the "
              "program?",
              "How long did it take between phase 1 and phase 2?"):
        assert detect_time_constraint(q).kind == "ordering", q
    # an unparseable but month-shaped bound degrades, never raises: with no
    # ordering cue it degrades to None (no filter/reorder); with one it
    # degrades to ordering
    assert detect_time_constraint(
        "Between February 30 and March 4, what happened?",
        default_year=2025).kind is None
    assert detect_time_constraint(
        "How many days between February 30 and March 4?",
        default_year=2025).kind == "ordering"


def test_no_temporal_constraint_stays_none():
    c = detect_time_constraint("What is the user's preferred coffee order?")
    assert c.kind is None
    assert c.anchors == ()


def test_anchors_default_is_empty_for_pre_2976_consumers():
    # the added field is additive: positional construction still works
    c = TimeConstraint("ordering")
    assert c.kind == "ordering" and c.start is None and c.anchors == ()


def test_arm_default_is_off():
    # fail-safe OFF (#1745 posture): the tri-state default is None (env-
    # resolved); the promotion budget is the co-present pair.
    assert inspect.signature(
        retrieve_for_question).parameters["temporal_leg"].default is None
    assert DEFAULT_TEMPORAL_LEG_LIMIT == 2
    assert DEFAULT_TEMPORAL_LEG_BUCKET_CAP == 1


# ── 2. the leg ───────────────────────────────────────────────────────────────


def test_content_tokens_drop_stopwords_and_temporal_words():
    # "the day I bought" must contribute only the event noun
    assert content_tokens("the day I bought my new tennis racket") == {
        "bought", "new", "tennis", "racket"}
    assert content_tokens("") == frozenset()


def test_leg_is_empty_without_dated_candidates():
    assert temporal_leg_order([]) == []
    assert temporal_leg_order(
        [_hit("a", "", "s", "no date"), _hit("b", "", "s", "no date")]) == []


def test_anchor_co_present_pair_is_promoted_above_the_cluster():
    # 10 semantically-rank-0 items from ONE session, then both compared
    # instances deep in the semantic ranking. The leg must surface both
    # halves at its head (research §1/§2: ordering needs both instances).
    candidates = [
        _hit(f"cluster{i}", "2025-01-01", "s0", "misc note about weather")
        for i in range(10)
    ]
    candidates.append(
        _hit("gold_a", "2025-03-01", "sA",
             "I bought the dog bed for Max at the pet store"))
    candidates.append(
        _hit("gold_b", "2025-05-01", "sB",
             "I ordered the training pads for Luna online"))
    order = temporal_leg_order(
        candidates,
        anchors=("the dog bed for max", "the training pads for luna"))
    assert order[:2] == ["gold_a", "gold_b"], order


def test_long_anchor_needs_majority_token_overlap():
    # "the dog bed for max" → {dog, bed, max} (need 2). A hit sharing only
    # "bed" must NOT claim the anchor, even at a better semantic rank.
    candidates = [
        _hit("weak", "2025-02-01", "sW", "i need a new bed"),
        _hit("strong", "2025-01-01", "sS", "the dog bed for max arrived"),
    ]
    order = temporal_leg_order(candidates,
                               anchors=("the dog bed for max",))
    assert order[0] == "strong", order


def test_spread_caps_any_single_date_session():
    # a semantically dominant session must not re-monopolize the leg; the
    # spread is over ANCHOR-RELEVANT evidence only
    candidates = [
        _hit(f"cluster{i}", "2025-01-01", "s0", "herb garden note")
        for i in range(6)
    ]
    for j, d in enumerate(("2025-02-01", "2025-03-01", "2025-04-01")):
        candidates.append(_hit(f"other{j}", d, f"s{j}", "herb garden other"))
    order = temporal_leg_order(candidates, anchors=("herb garden",),
                               limit=8, bucket_cap=2)
    assert sum(1 for pid in order if pid.startswith("cluster")) == 2
    # three different dates each contribute, in semantic-rank order
    assert {"other0", "other1", "other2"} <= set(order)


def test_leg_requires_a_matched_anchor():
    # no anchors → inert (a spread-only promotion of arbitrary deep pool
    # items is measurably harmful; see the module docstring)
    candidates = [
        _hit("a", "2025-01-01", "sA", "alpha"),
        _hit("b", "2025-02-01", "sB", "beta"),
    ]
    assert temporal_leg_order(candidates) == []
    assert temporal_leg_order(candidates, anchors=()) == []
    # an anchor matching nothing → still inert
    assert temporal_leg_order(candidates, anchors=("gamma delta",)) == []


def test_leg_preserves_semantic_rank_not_date_order():
    # oldest-first base order; the leg must read it as the relevance prior
    # (2-token anchor: a 1-token one is subject to the rarity gate below)
    candidates = [
        _hit("oldest", "2025-01-01", "sA", "alpha beta"),
        _hit("middle", "2025-02-01", "sB", "alpha beta"),
        _hit("newest", "2025-03-01", "sC", "alpha beta"),
    ]
    assert temporal_leg_order(candidates, anchors=("alpha beta",), limit=3) == [
        "oldest", "middle", "newest"]


def test_one_token_anchor_must_be_rare_in_the_pool():
    # review-caught: a 1-token anchor ("...the day I received it" →
    # {received}) is claimed by ANY candidate carrying the word, so it is
    # only eligible when that word is RARE in this pool. Here 3/3 carry it
    # → dropped (inert); the same word in a pool where only one candidate
    # carries it is eligible and drives the pick.
    common = [_hit(f"c{i}", f"2025-01-0{i + 1}", f"s{i}", "body received")
              for i in range(3)]
    assert temporal_leg_order(common, anchors=("i received it",)) == []
    rare = [
        _hit("noise1", "2025-01-01", "sA", "bloody mary recipe"),
        _hit("noise2", "2025-01-02", "sB", "spare tyre pressure"),
        _hit("hit", "2025-01-03", "sC", "the dog bed arrived received"),
    ]
    assert temporal_leg_order(rare, anchors=("i received it",)) == ["hit"]


def test_leg_is_deterministic_and_bounded():
    candidates = [
        _hit(f"c{i}", f"2025-01-{i + 1:02d}", f"s{i}", "alpha beta")
        for i in range(30)
    ]
    a = temporal_leg_order(candidates, anchors=("alpha beta",), limit=5)
    b = temporal_leg_order(candidates, anchors=("alpha beta",), limit=5)
    assert a == b
    assert len(a) == 5
    assert all(pid in {c["id"] for c in candidates} for pid in a)


def test_bucket_cap_is_a_true_cap_across_both_phases():
    # review-caught: a phase-1 anchor pick consumes its bucket's slot, so
    # phase 2 cannot add a SECOND pick from that same (date, session).
    # The anchor's best match is c1, which is NOT its bucket's first member.
    candidates = [
        _hit("c0", "2025-01-01", "sA", "i need a new bed"),
        _hit("c1", "2025-01-01", "sA", "the dog bed for max"),
        _hit("c2", "2025-02-01", "sB", "the dog bed for max"),
    ]
    picks = temporal_leg_order(
        candidates, anchors=("dog bed for max",), limit=2, bucket_cap=1)
    assert picks == ["c1", "c2"], picks  # never c0: same bucket as c1
    # bucket_cap=2 lets the same bucket contribute again
    picks2 = temporal_leg_order(
        candidates, anchors=("dog bed for max",), limit=2, bucket_cap=2)
    assert picks2 == ["c1", "c0"], picks2


def test_placement_head_is_the_counterfactual_and_tail_is_the_default():
    # tail: picks land BELOW the head slice; head: at leg ranks 0..N. Both
    # are exposed so the eval lane can A/B them (the near-ceiling proxy
    # cannot settle placement — see the module docstring).
    candidates = [
        _hit(f"c{i}", f"2025-01-{i + 1:02d}", f"s{i}", "alpha beta")
        for i in range(6)
    ]
    # ceiling = window − limit = 3, so the head slice c0..c2 needs no
    # promotion: the tail draw comes from OUTSIDE it and the base order is
    # unchanged.
    tail_order, tail_picks = temporal_leg_fusion_order(
        candidates, anchors=("alpha beta",), window=4, limit=1)
    assert tail_picks == ["c3"]
    assert tail_order == [f"c{i}" for i in range(6)]  # head intact
    # the counterfactual draws with head=0 and fuses at leg ranks 0..N, so
    # a pick CAN occupy rank 0.
    head_order, head_picks = temporal_leg_fusion_order(
        candidates, anchors=("alpha beta",), window=4, limit=1,
        placement="head")
    assert head_picks == ["c0"]
    assert head_order[0] == "c0"
    # an anchor matching only DEEP is promotable in both modes — but the
    # tail keeps it at/after the ceiling while the counterfactual lifts it
    # to rank 0 (the displacement the shipped default refuses).
    deep = [
        _hit(f"n{i}", f"2025-02-{i + 1:02d}", f"t{i}", "unrelated words")
        for i in range(6)
    ] + [_hit("deep_gold", "2025-03-01", "sZ", "alpha beta")]
    t_order, t_picks = temporal_leg_fusion_order(
        deep, anchors=("alpha beta",), window=4, limit=1)
    assert t_picks == ["deep_gold"]
    assert t_order[0] == "n0" and t_order.index("deep_gold") >= 3
    h_order, h_picks = temporal_leg_fusion_order(
        deep, anchors=("alpha beta",), window=4, limit=1, placement="head")
    assert h_picks == ["deep_gold"]
    assert h_order[0] == "deep_gold"


def test_undated_candidates_are_never_selected():
    candidates = [        _hit("dated", "2025-01-01", "sA", "the dog bed for max"),
        _hit("undated", "", "sB", "the dog bed for max"),
    ]
    assert temporal_leg_order(candidates, anchors=("the dog bed for max",)) == [
        "dated"]
    # a pool with NO dated candidate is inert even with a matching anchor
    assert temporal_leg_order(
        [_hit("u1", "", "sA", "the dog bed for max")],
        anchors=("the dog bed for max",)) == []


def test_head_exclusion_keeps_promotion_out_of_the_visible_head():
    # `head` = the ceiling: candidates at ranks < head need no lift. An
    # anchor whose ONLY match is inside the head yields an inert leg
    # (never a double-counted promotion of an already-visible item).
    candidates = [
        _hit("visible", "2025-01-01", "sA", "the dog bed for max"),
        _hit("deep1", "2025-02-01", "sB", "weather note"),
        _hit("deep2", "2025-03-01", "sC", "weather note"),
    ]
    assert temporal_leg_order(
        candidates, anchors=("the dog bed for max",), head=1) == []
    # with no exclusion the same anchor is promoted
    assert temporal_leg_order(
        candidates, anchors=("the dog bed for max",), head=0) == ["visible"]


def test_head_boundary_is_the_candidate_index_not_the_dated_index():
    # review-caught: an UNDATED candidate inside the head must not shift the
    # exclusion boundary downward (which silently made a deep anchor match
    # unpromotable). The boundary is the candidate index.
    candidates = [
        _hit("undated", "", "sU", "no date here"),
        _hit("deep_gold", "2025-02-01", "sA", "the dog bed for max"),
        _hit("other", "2025-03-01", "sB", "weather note"),
    ]
    # head=1 excludes only the undated candidate → deep_gold is promotable
    assert temporal_leg_order(
        candidates, anchors=("the dog bed for max",), head=1) == ["deep_gold"]
    # head=2 excludes deep_gold too → inert
    assert temporal_leg_order(
        candidates, anchors=("the dog bed for max",), head=2) == []


# ── 3. additive fusion: the no-op invariant ──────────────────────────────────


def test_empty_temporal_leg_leaves_rrf_order_byte_identical():
    base = [(f"p{i}", 0.0) for i in range(60)]
    fused = rrf_fusion([base, []], strategy_names=["semantic", "temporal"],
                       weights={"temporal": 1.0})
    assert list(fused) == [pid for pid, _ in base]


def _deep_gold_candidates():
    """The measured failure shape: 60 semantic candidates, gold deep."""
    candidates = [
        _hit(f"cluster{i}", "2025-01-01", f"s{i}", "weather note")
        for i in range(60)
    ]
    candidates[45] = _hit("gold_a", "2025-03-01", "sA",
                          "i bought the dog bed for max at the pet store")
    candidates[50] = _hit("gold_b", "2025-05-01", "sB",
                          "i ordered the training pads for luna online")
    return candidates


def test_fusion_helper_promotes_deep_gold_into_the_reader_window():
    # The measured failure shape: gold at semantic ranks 45/50 while the
    # reader window is 12. This exercises the SHIPPED wiring
    # (temporal_leg_fusion_order) — not a re-implementation of its formula.
    candidates = _deep_gold_candidates()
    order, picks = temporal_leg_fusion_order(
        candidates,
        anchors=("the dog bed for max", "the training pads for luna"),
        window=12)
    assert picks == ["gold_a", "gold_b"], picks
    assert {"gold_a", "gold_b"} <= set(order[:12]), order[:14]
    # the visible head keeps its relative order (no scrambling, no
    # double-counted picks)
    assert order[:5] == [f"cluster{i}" for i in range(5)], order[:6]


def test_fusion_helper_is_inert_without_anchors_or_dates():
    candidates = _deep_gold_candidates()
    order, picks = temporal_leg_fusion_order(candidates, anchors=(), window=12)
    assert picks == []
    assert order == [c["id"] for c in candidates]
    undated = [_hit(f"u{i}", "", "s", "x") for i in range(5)]
    order, picks = temporal_leg_fusion_order(
        undated, anchors=("x",), window=12)
    assert picks == [] and order == [c["id"] for c in undated]


def test_fusion_helper_clamps_the_budget_so_the_tail_invariant_holds():
    # an unbounded knob value must not push picks back to leg ranks 0..N
    # (the takeover): the helper clamps to at most a third of the window.
    candidates = _deep_gold_candidates()
    for wild in (12, 60, 10_000):
        order, picks = temporal_leg_fusion_order(
            candidates,
            anchors=("the dog bed for max", "the training pads for luna"),
            window=12, limit=wild)
        assert picks, (wild, picks)
        # head slots keep their base order for at least 8 of the 12 slots
        assert order[:8] == [f"cluster{i}" for i in range(8)], (wild, order[:10])
        assert {"gold_a", "gold_b"} <= set(order[:12]), (wild, order[:14])


def test_fusion_helper_never_double_counts_an_in_head_pick():
    # the visible head is excluded from promotion: an anchor matching only
    # an in-head candidate leaves the order byte-identical (the pre-review
    # wiring double-counted it and yanked it to the top).
    candidates = [
        _hit("head_gold", "2025-01-01", "sA", "the dog bed for max"),
        _hit("deep0", "2025-02-01", "sB", "weather note"),
        _hit("deep1", "2025-03-01", "sC", "weather note"),
    ]
    order, picks = temporal_leg_fusion_order(
        candidates, anchors=("the dog bed for max",), window=4)
    assert picks == []
    assert order == [c["id"] for c in candidates]


def test_fusion_helper_window_one_is_benign():
    # window=1 has no head to scramble: the single pick lands at rank 0 and
    # the call must not raise (documented boundary)
    candidates = _deep_gold_candidates()
    order, picks = temporal_leg_fusion_order(
        candidates, anchors=("the dog bed for max",), window=1)
    assert picks == ["gold_a"], picks
    assert len(order) == len(candidates)


def test_window_tail_placement_keeps_picks_out_of_the_head():
    # MECHANISM test only: a pick fused at leg ranks 0..N sorts above every
    # visible head item. That is the shape the tail placement avoids (the
    # proxy does NOT show it is end-to-end worse — see the module
    # docstring); this pins both shapes so a placement regression is caught.
    base = [(f"cluster{i}", 0.0) for i in range(60)]
    base[45] = ("gold_a", 0.0)
    base[50] = ("gold_b", 0.0)
    rank_zero = rrf_fusion(
        [base, [("gold_a", 0.0), ("gold_b", 0.0)]],
        strategy_names=["semantic", "temporal"], weights={"temporal": 1.0})
    assert list(rank_zero)[:2] == ["gold_a", "gold_b"]
    tail = rrf_fusion(
        [base, [*base[:10], ("gold_a", 0.0), ("gold_b", 0.0)]],
        strategy_names=["semantic", "temporal"], weights={"temporal": 1.0})
    assert list(tail)[:2] == ["cluster0", "cluster1"], list(tail)[:4]


def test_empty_reason_taxonomy_is_exhaustive():
    for dated, anchors, weight, budget, expected in (
        (5, 2, 1.0, 0, "budget_disabled"),
        (0, 2, 1.0, 2, "no_dated_candidates"),
        (5, 0, 1.0, 2, "no_anchors"),
        (5, 2, 0.0, 2, "weight_disabled"),
        (5, 2, 1.0, 2, "no_promotable_anchor_match"),
    ):
        assert temporal_leg_empty_reason(
            dated=dated, anchors=anchors, weight=weight,
            budget=budget) == expected


def test_effective_promotion_budget_is_clamped():
    # window 12: at most a third of the window. This asserts the PURE rule;
    # the caller's telemetry (``temporal_leg_stats``) is NOT covered here —
    # it needs a DB-backed retrieve_for_question (documented residual in
    # tortoise/temporal_leg.py). limit <= 0 is OFF (0), so a direct-call
    # zero budget and the arm layer agree; the env path floors <1 to the
    # default, so the env off switch is TORTOISE_LME_TEMPORAL_LEG=0.
    assert [effective_promotion_budget(12, x)
            for x in (0, -3, 1, 2, 5, 12, 10_000)] == [
        0, 0, 1, 2, 4, 4, 4]
    assert effective_promotion_budget(24, 100) == 8
    assert effective_promotion_budget(2, 100) == 1
    assert effective_promotion_budget(1, 100) == 1


def test_zero_limit_fusion_is_inert():
    candidates = _deep_gold_candidates()
    order, picks = temporal_leg_fusion_order(
        candidates, anchors=("the dog bed for max",), window=12, limit=0)
    assert picks == []
    assert order == [c["id"] for c in candidates]


def test_deictic_only_anchors_are_dropped():
    # "today"/"yesterday" carry no anchor signal: the detector must report
    # NO anchors (so the trace says no_anchors, not a phantom match)
    for q in ("How many days before today did I train?",
              "What happened before yesterday?",
              "Which did I do first, today or tomorrow?"):
        assert detect_time_constraint(q).anchors == (), q


def test_zero_weight_temporal_leg_is_a_no_op_on_membership_and_order():
    base = [(f"p{i}", 0.0) for i in range(20)]
    fused = rrf_fusion([base, [("deep", 0.0)]],
                       strategy_names=["semantic", "temporal"],
                       weights={"temporal": 0.0})
    assert list(fused) == [pid for pid, _ in base] + ["deep"]
