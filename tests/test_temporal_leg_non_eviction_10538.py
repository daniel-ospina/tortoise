"""#10538 — the NON-EVICTION GUARD: expansion may never evict base evidence.

The owner ruling (2026-11-07, recorded on the issue at comment ``6711158625``)
is **window-exit semantics**: time-aware expansion may WIDEN the window, and it
may never pay for a slot by removing evidence the reader already had. The
ruling states the guard as three conditions of the fix — *dedupe to unique
sessions*, a *per-session cap*, *never crop the anchor* — and this file pins
each one with a case that ONLY that condition can reject, so a mutation that
drops a condition cannot hide behind the other two.

Why it is a gate and not a caveat, measured (issue comment ``6574179020``): at
the shipped geometry the UNGUARDED tail splice widens by two and drops two —
``BASE distinct 3 / FUSED distinct 3``, ``EVICTED ['b1','c1']``, the sole
representatives of two sessions. Net-zero on distinct evidence sessions, and
scoreable in both directions: it can trade a gold session for a non-gold one.
``test_the_issue_probe_pool_is_net_zero_unguarded_and_additive_guarded`` is
that pool, and it asserts both halves.

Scope, declared (see ``temporal_leg_fusion_order``'s docstring): the guard
governs the DATE-WINDOW arm (#10538). The ANCHOR arm (#2976) is measured
as-is, and the ``head`` arm is the displacement counterfactual — both are
pinned here as deliberately unguarded, because a guard that quietly swallowed
them would move a published table and destroy the counterfactual.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tools.longmem_eval import retrieve as r
from tortoise import temporal_leg as tl

#: The issue's own probe pool (comment 6574179020), reproduced exactly:
#: ``sA`` owns 10 of the 12 visible slots, ``b1``/``sB`` and ``c1``/``sC`` are
#: the sole representatives of the two sessions the reader only just holds,
#: then three deeper sessions with later dates. ``window=12``/``limit=2`` ⇒
#: ``ceiling = 10``, so the picks land at fused ranks 10-11 — ON TOP of b1/c1.
ISSUE_POOL = (
    [{"id": f"a{i}", "session_id": "sA", "session_date": "2023-04-15",
      "content": "alpha beta"} for i in range(10)]
    + [{"id": "b1", "session_id": "sB", "session_date": "2023-04-16",
        "content": "beta gamma"},
       {"id": "c1", "session_id": "sC", "session_date": "2023-04-17",
        "content": "gamma delta"},
       {"id": "sd1", "session_id": "sD", "session_date": "2023-04-18",
        "content": "delta epsilon"},
       {"id": "se1", "session_id": "sE", "session_date": "2023-04-19",
        "content": "epsilon zeta"},
       {"id": "sf1", "session_id": "sF", "session_date": "2023-04-20",
        "content": "zeta eta"}]
)
RANGE = ("2023-04-10", "2023-04-20")
WINDOW = 12
LIMIT = 2


def _fuse(pool, *, window=WINDOW, limit=LIMIT, date_window=RANGE, **kwargs):
    trace: dict = kwargs.pop("trace", {})
    order, picks = tl.temporal_leg_fusion_order(
        pool, anchors=(), window=window, limit=limit,
        date_window=date_window, guard_trace=trace, **kwargs)
    return order, picks, trace


def _ids(pool):
    return [c["id"] for c in pool]


# ── 1. the measured harm, and the guard's whole effect on it ────────────────

def test_the_issue_probe_pool_is_net_zero_unguarded_and_additive_guarded():
    """Both halves of the ruling, on the pool the ruling was argued from.

    Unguarded: exactly the probe's numbers (``picks ['sd1','se1']`` at ranks
    ``[10, 11]``; ``deleted_sessions ['sB','sC']``; ``net 0``). Guarded: every
    pick refused and the fused order byte-identical to the semantic one.
    """
    u_order, u_picks, _ = _fuse(ISSUE_POOL, non_eviction=False)
    assert u_picks == ["sd1", "se1"], u_picks
    u = tl.window_arm_retention(ISSUE_POOL, u_order, picks=u_picks,
                                window=WINDOW)
    assert u["pick_ranks"] == [10, 11], u["pick_ranks"]
    assert u["deleted_sessions"] == ["sB", "sC"], u["deleted_sessions"]
    assert u["net"] == 0, u["net"]
    assert u["additive"] is False

    g_order, g_picks, g_trace = _fuse(ISSUE_POOL)
    assert g_picks == [], (
        f"the guard must refuse a widening that trades two sessions for two: "
        f"{g_picks}")
    assert g_order == _ids(ISSUE_POOL), (
        "a fully-refused arm must leave the fused order byte-identical")
    g = tl.window_arm_retention(ISSUE_POOL, g_order, window=WINDOW)
    assert g["additive"] is True and g["deleted_sessions"] == []
    assert g["net"] == 0 and g["pick_ranks"] == []
    # the refusal is ATTRIBUTED, not silent: each pick names the session it
    # would have deleted, so the ON/OFF surface can explain the empty leg.
    assert [r["id"] for r in g_trace["rejected"]] == ["sd1", "se1"]
    assert all(r["reason"].startswith("evicts:") for r in g_trace["rejected"])
    assert all(r["reason"] == "evicts:sC" for r in g_trace["rejected"]), (
        g_trace["rejected"])
    assert g_trace["applied"] is True and g_trace["targeting"] == "window"
    assert g_trace["on"] is True, (
        "the ENFORCING path must report the rule ON — a trace saying OFF while "
        f"it refuses picks is the conflation applied's docstring forbids: "
        f"{g_trace}")


# ── 2. condition (1) DEDUPE TO UNIQUE SESSIONS — discriminating ─────────────

def test_a_pick_of_a_session_the_reader_already_holds_is_refused():
    """Only condition (1) can refuse this one — the discriminating case.

    ``b2`` is ``sB`` under a DIFFERENT raw date than the visible ``b1``, so it
    is a NEW ``(date, session)`` bucket and the pre-existing bucket-level
    exclusion (``held_buckets``) does not stop it. Admitting it would delete
    NOTHING (``sB`` stays visible through ``b1``), so condition (3) cannot be
    the reason: the pick is refused because it re-delivers a source the reader
    already has, which is the ruling's "dedupe to unique sessions".
    """
    pool = (
        [{"id": f"a{i}", "session_id": "sA", "session_date": "2023-04-10",
          "content": "alpha"} for i in range(3)]
        + [{"id": "b1", "session_id": "sB", "session_date": "2023-04-11",
            "content": "alpha"},
           {"id": "b2", "session_id": "sB", "session_date": "2023-04-12",
            "content": "alpha"}]
    )
    # the pre-guard arm DOES pick b2 — so this case is reachable, not vacuous.
    u_order, u_picks, _ = _fuse(pool, window=4, limit=1, non_eviction=False)
    assert u_picks == ["b2"], u_picks
    # ...and admitting it deletes no session (that is why condition 3 is not
    # the one that fires):
    u = tl.window_arm_retention(pool, u_order, picks=u_picks, window=4)
    assert u["deleted_sessions"] == [], u["deleted_sessions"]
    assert u["net"] == 0 and u["base_sessions"] == u["fused_sessions"] == 2

    g_order, g_picks, g_trace = _fuse(pool, window=4, limit=1)
    assert g_picks == []
    assert g_trace["rejected"] == [
        {"id": "b2", "session": "sB", "reason": "session_already_visible"}]
    assert g_order == _ids(pool)


# ── 3. condition (2) PER-SESSION CAP — discriminating ──────────────────────

def test_two_picks_from_one_session_are_one_delivery():
    """``x1``/``x2`` are ``sX`` under two raw dates; neither is visible, and
    the FIRST is admitted additively. The second is refused as
    ``duplicate_session`` — the only condition that can fire on it (its
    session is by then claimed, and its own admission would delete nothing
    a second time)."""
    pool = (
        [{"id": f"a{i}", "session_id": "sA", "session_date": "2023-04-10",
          "content": "alpha"} for i in range(6)]
        + [{"id": "x1", "session_id": "sX", "session_date": "2023-04-11",
            "content": "alpha"},
           {"id": "x2", "session_id": "sX", "session_date": "2023-04-12",
            "content": "alpha"}]
    )
    _, u_picks, _ = _fuse(pool, window=6, limit=2, non_eviction=False)
    assert u_picks == ["x1", "x2"], u_picks

    g_order, g_picks, g_trace = _fuse(pool, window=6, limit=2)
    assert g_picks == ["x1"], g_picks
    assert [r["reason"] for r in g_trace["rejected"]] == ["duplicate_session"]
    assert g_trace["rejected"][0]["session"] == "sX"
    assert g_order != _ids(pool), "the admitted pick must still widen the window"


# ── 4. condition (3) NEVER CROP THE ANCHOR — and its CONTRAST ──────────────

def test_a_pick_may_displace_a_REDUNDANT_slot_and_the_window_widens():
    """The guard is a non-eviction rule, not an off switch.

    ``sA`` and ``sB`` each hold two visible slots, so the tail slot it
    displaces is redundant and ``sC`` is genuinely new: the pick is ADMITTED
    and ``net`` moves ``2 → 3``. Without this case a guard that refused
    everything would pass all the others.
    """
    pool = (
        [{"id": "a0", "session_id": "sA", "session_date": "2023-04-10",
          "content": "alpha"},
         {"id": "a1", "session_id": "sA", "session_date": "2023-04-10",
          "content": "alpha"},
         {"id": "b1", "session_id": "sB", "session_date": "2023-04-11",
          "content": "alpha"},
         {"id": "b2", "session_id": "sB", "session_date": "2023-04-11",
          "content": "alpha"},
         {"id": "c1", "session_id": "sC", "session_date": "2023-04-12",
          "content": "alpha"}]
    )
    g_order, g_picks, g_trace = _fuse(pool, window=4, limit=1)
    assert g_picks == ["c1"], g_picks
    assert g_trace["rejected"] == []
    g = tl.window_arm_retention(pool, g_order, picks=g_picks, window=4)
    assert g["deleted_sessions"] == []
    assert g["additive"] is True
    assert (g["base_sessions"], g["fused_sessions"], g["net"]) == (2, 3, 1)


def test_the_guard_judges_the_RUNS_reader_cap_not_a_hard_wired_default():
    """The guard must judge the window the READER actually receives.

    ``retrieve.py`` fuses and THEN dedups the pool with the RUN's
    ``--max-chunks-per-session``, so a guard measuring a fixed cap would clear
    a pick that deletes a session from the real window — a false PASS, which is
    the failure mode this whole guard exists to prevent. Pinned structurally:
    the cap must be THREADED from the fusion caller into the filter and into
    ``window_arm_retention``, and never overridden to ``None`` (the pre-reader
    view).

    ⚠️ This was a live defect on the first head of this PR (fresh-context
    review, finding 1): the filter hard-wired ``DEFAULT_MAX_CHUNKS_PER_SESSION``
    while ``temporal_leg_fusion_order`` had no cap parameter at all, so on a run
    with ``TORTOISE_LME_MAX_CHUNKS_PER_SESSION=1`` the guard reported
    ``applied: True, rejected: []`` over an order that deleted a session.
    """
    src = Path(tl.__file__).read_text()
    fn = next(
        node for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.FunctionDef) and node.name == "_non_eviction_filter"
    )
    calls = [
        node for node in ast.walk(fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"window_arm_retention", "_reader_order"}
    ]
    assert calls, "the guard stopped consulting the reader-window model"
    for call in calls:
        keywords = {kw.arg: kw.value for kw in call.keywords}
        assert "max_chunks_per_session" in keywords, (
            f"{call.func.id} must be given the RUN's cap explicitly")
        passed = keywords["max_chunks_per_session"]
        assert isinstance(passed, ast.Name) \
            and passed.id == "max_chunks_per_session", (
            "the cap must be the threaded parameter, not a module constant and "
            "not None — either would grade a window the reader does not get")
    # ...and the fusion caller must thread its own parameter into the filter.
    fus = next(
        node for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.FunctionDef)
        and node.name == "temporal_leg_fusion_order"
    )
    filter_calls = [
        node for node in ast.walk(fus)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_non_eviction_filter"
    ]
    assert filter_calls, "the guard is not called from the fused caller"
    for call in filter_calls:
        keywords = {kw.arg: kw.value for kw in call.keywords}
        assert isinstance(keywords.get("max_chunks_per_session"), ast.Name) \
            and keywords["max_chunks_per_session"].id == \
            "max_chunks_per_session", (
            "the fused caller must forward ITS cap into the guard")
    # ...and the harness call site must forward the run's cap to the leg.
    src_r = Path(r.__file__).read_text()
    for call in [
        node for node in ast.walk(ast.parse(src_r))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "temporal_leg_fusion_order"
    ]:
        kw = {k.arg: k.value for k in call.keywords}
        assert isinstance(kw.get("max_chunks_per_session"), ast.Name) \
            and kw["max_chunks_per_session"].id == "max_chunks_per_session", (
            "retrieve.py must pass the run's max_chunks_per_session — the "
            "guard would otherwise model a window the reader never sees")


def test_a_reader_cap_change_moves_the_guard_VERDICT_not_just_the_window():
    """The cap's behavioural proof — on the GUARD'S ANSWER, not on `_reader_order`.

    ⚠️ This pin was written one call short on the first attempt (fresh-context
    review, cycle 2, finding 1): it asserted only that `_reader_order` returns
    different windows at different caps, which is a property of pre-existing
    code and would stay green if `_non_eviction_filter` went back to a hard-wired
    cap. It now compares `temporal_leg_fusion_order`'s VERDICT at two caps over
    one pool: at the run's tighter cap the reader window is a different set of
    sessions, so the same picks are refused — and at the looser cap they are
    admitted.

    The pool is chunk-bearing on purpose: the reader's cap removes raw
    ``session-transcript`` chunks ABOVE the picks, which is the only way the cap
    can change which sessions are visible.
    """
    pool = (
        [{"id": f"k{i}", "session_id": "sA", "session_date": "2023-04-10",
          "point_kind": "session-transcript", "content": "alpha"}
         for i in range(4)]
        + [{"id": f"a{i}", "session_id": "sA", "session_date": "2023-04-10",
            "content": "alpha"} for i in range(4)]
        + [{"id": "x0", "session_id": "sB", "session_date": "2023-04-11",
            "content": "alpha"},
           {"id": "x1", "session_id": "sC", "session_date": "2023-04-12",
            "content": "alpha"}]
    )
    tight_trace: dict = {}
    tight_order, tight_picks = tl.temporal_leg_fusion_order(
        pool, window=6, limit=2, date_window=RANGE, guard_trace=tight_trace,
        max_chunks_per_session=1)
    loose_trace: dict = {}
    loose_order, loose_picks = tl.temporal_leg_fusion_order(
        pool, window=6, limit=2, date_window=RANGE, guard_trace=loose_trace,
        max_chunks_per_session=None)

    # the SAME picks are selected in both arms — the cap moves the VERDICT, not
    # the selection (so this cannot pass by the arm happening not to fire):
    assert loose_picks == ["x0", "x1"], loose_picks
    assert tight_picks == [], tight_picks
    assert tight_trace["applied"] is True and loose_trace["applied"] is True
    assert {r["id"] for r in tight_trace["rejected"]} == {"x0", "x1"}
    # and at the reader's real (tight) cap each refusal names the reason that
    # only that cap produces: x0 re-delivers a session the window already holds
    # once the chunk cap lifts the deeper hits, and x1 would then evict sB.
    reasons = {r["id"]: r["reason"] for r in tight_trace["rejected"]}
    assert reasons == {"x0": "session_already_visible", "x1": "evicts:sB"}, reasons
    # both arms agree that nothing was deleted — the difference is what the
    # guard can SEE, which is exactly the false-PASS surface the fix closes.
    for order in (tight_order, loose_order):
        assert tl.window_arm_retention(
            pool, order, window=6,
            max_chunks_per_session=1 if order is tight_order else None,
        )["deleted_sessions"] == []


def test_each_pick_is_judged_CUMULATIVELY_not_in_isolation():
    """A refusal must not make a later pick look harmless.

    ⚠️ Unpinned until fresh-context review cycle 2 (finding 2): every other
    multi-pick case in this file has each pick independently evicting its own
    session, so a rewrite that judged picks in isolation would refuse the same
    set and stay green. Here it is not the same set.

    Pool: ``sB`` holds TWO visible slots (``b1``, ``b2``) and no sole
    representative, and ``sC``/``sD`` sit below the window. Taught one pick at a
    time, BOTH ``c1`` and ``d1`` look harmless (each alone displaces a redundant
    ``sB`` slot); admitted together they take BOTH of them and **delete sB** from
    the reader window — the harm the ruling forbids, invisible to
    ``evicted_sole_representatives``. Judged cumulatively, ``c1`` is admitted and
    ``d1`` is refused because it would take ``sB``'s last slot.
    """
    pool = (
        [{"id": f"a{i}", "session_id": "sA", "session_date": "2023-04-10",
          "content": "alpha"} for i in range(8)]
        + [{"id": "b1", "session_id": "sB", "session_date": "2023-04-11",
            "content": "alpha"},
           {"id": "b2", "session_id": "sB", "session_date": "2023-04-11",
            "content": "alpha"},
           {"id": "c1", "session_id": "sC", "session_date": "2023-04-12",
            "content": "alpha"},
           {"id": "d1", "session_id": "sD", "session_date": "2023-04-13",
            "content": "alpha"}]
    )
    trace: dict = {}
    order, picks = tl.temporal_leg_fusion_order(
        pool, window=10, limit=2, date_window=RANGE, guard_trace=trace,
        max_chunks_per_session=None)
    assert picks == ["c1"], picks
    assert [(r["id"], r["reason"]) for r in trace["rejected"]] == [
        ("d1", "evicts:sB")], trace["rejected"]
    # ⚠️ `accepted` is part of the trace's documented contract and this is the
    # ONE shape where it disagrees with `rejected`: the guard ADMITTED `c1` and
    # refused `d1`. Unpinned until review cycle 8 (finding 2): rewriting the
    # guard path's write to `"accepted": []` kept every suite green, and that
    # row would then read `applied: True, accepted: []` — a guard that admitted
    # one pick while claiming it admitted none. Asserted on the PARTIAL-
    # acceptance row precisely because a full-refusal row (`accepted == []`) and
    # a full-acceptance row are both satisfied by the wrong value.
    assert trace["accepted"] == ["c1"], trace["accepted"]
    assert trace["applied"] is True and trace["targeting"] == "window"
    # the window WIDENS (2 -> 3 sessions) and sB survives: both halves of the
    # ruling at once, on the shape a per-pick guard gets wrong.
    got = tl.window_arm_retention(pool, order, picks=picks, window=10,
                                  max_chunks_per_session=None)
    assert got["deleted_sessions"] == []
    assert (got["base_sessions"], got["fused_sessions"], got["net"]) == (2, 3, 1)
    # control: the UNGUARDED arm admits both and deletes sB — i.e. this fixture
    # really does carry the harm, so the assertion above is not vacuous.
    u_order, u_picks = tl.temporal_leg_fusion_order(
        pool, window=10, limit=2, date_window=RANGE, non_eviction=False,
        max_chunks_per_session=None)
    assert u_picks == ["c1", "d1"], u_picks
    u = tl.window_arm_retention(pool, u_order, picks=u_picks, window=10,
                                max_chunks_per_session=None)
    assert u["deleted_sessions"] == ["sB"], u["deleted_sessions"]


# ── 5. the OFF-SWITCH restores the pre-ruling splice byte-for-byte ─────────

def test_the_off_switch_restores_the_pre_ruling_splice():
    """``non_eviction=False`` is the switch, and it is the ONLY thing that
    changes: on the issue's pool the picks, their ranks, the deleted sessions
    and the assembled order are the pre-ruling ones — which is what keeps the
    committed retention receipt a reproducible build artifact."""
    off_order, off_picks, off_trace = _fuse(ISSUE_POOL, non_eviction=False)
    assert off_picks == ["sd1", "se1"]
    # the switch is RECORDED, not silent: a disabled guard reports itself, so a
    # receipt can never look guarded when it is not.
    assert off_trace == {"on": False, "applied": False,
                         "targeting": "window", "rejected": [],
                         "accepted": ["sd1", "se1"]}, off_trace
    # the order is the documented tail splice: head intact, picks at the tail
    det = tl.window_arm_retention(ISSUE_POOL, off_order, picks=off_picks,
                                 window=WINDOW)
    assert det["pick_ranks_before_dedup"] == det["pick_ranks"] == [10, 11]
    assert off_order[:10] == [f"a{i}" for i in range(10)]
    # and the DEFAULT (no argument) is the guarded arm — the ruling's default.
    on_order, on_picks, _ = _fuse(ISSUE_POOL)
    assert (on_picks, on_order) == ([], _ids(ISSUE_POOL))
    assert on_order != off_order


# ── 6. the env resolver: fail-safe ON, only an explicit falsy waives it ────

def test_the_resolver_defaults_on_and_only_an_explicit_falsy_turns_it_off():
    assert r.DEFAULT_TEMPORAL_LEG_NON_EVICTION is True
    assert r.resolve_temporal_leg_non_eviction(None, None) is True
    for off in ("0", "off", "OFF", " false ", "no"):
        assert r.resolve_temporal_leg_non_eviction(None, off) is False, off
    # a typo must never waive a hard condition of the arm
    for garbage in ("", "   ", "of", "nope", "1", "true", "on", "yes", "None"):
        assert r.resolve_temporal_leg_non_eviction(None, garbage) is True, garbage


def test_an_explicit_argument_wins_over_the_environment():
    assert r.resolve_temporal_leg_non_eviction(True, "0") is True
    assert r.resolve_temporal_leg_non_eviction(False, "1") is False
    assert r.resolve_temporal_leg_non_eviction(False, None) is False


# ── 7. the empty-leg reason names the guard, and only when it fired ────────

def test_the_trace_attributes_a_guard_refusal_instead_of_laundering_it():
    kw = dict(dated=5, anchors=0, weight=1.0, budget=2, date_window=True)
    assert tl.temporal_leg_empty_reason(**kw) == "no_window_candidates"
    assert tl.temporal_leg_empty_reason(**kw, guard_rejected=2) == \
        "non_eviction_guard"
    # a weight-0 arm never reached the guard, so it must not claim the guard
    assert tl.temporal_leg_empty_reason(
        dated=5, anchors=0, weight=0.0, budget=2, date_window=True,
        guard_rejected=2) == "weight_disabled"
    # and a caller that predates the guard keeps its pre-guard reasons
    assert tl.temporal_leg_empty_reason(
        dated=5, anchors=2, weight=1.0, budget=2) == \
        "no_promotable_anchor_match"
    # ⚠️ THE PRECEDENCE ITSELF (cycle 9, finding 2): the guard branch must sit
    # BEFORE the window/anchor branches, because "a guard refusal is a true
    # statement about the run whatever targeted it" (the docstring's words). The
    # case above only has ``anchors=0``, so a rewrite nesting the guard branch
    # inside ``if anchors <= 0 and date_window`` kept every suite green while
    # laundering a real guard refusal on an ANCHORED window question as an
    # ordinary ``no_promotable_anchor_match`` — which is what invites "fixing" a
    # working guard. Anchors > 0 AND date_window, so both rival branches are
    # live when the guard's must win.
    assert tl.temporal_leg_empty_reason(
        dated=5, anchors=1, weight=1.0, budget=2, date_window=True,
        guard_rejected=2) == "non_eviction_guard"
    # ...and with no refusals that anchored-window row still reads its own
    # reason, so the assertion above is about the guard and not the shape.
    assert tl.temporal_leg_empty_reason(
        dated=5, anchors=1, weight=1.0, budget=2, date_window=True) == \
        "no_promotable_anchor_match"


# ── 8. declared scope: the anchor arm and the head counterfactual ──────────

def test_the_anchor_arm_is_not_guarded_and_says_so():
    """#2976 is measured AS-IS; folding the #10538 guard across it would move a
    published table for an arm no ruling covers. Pinned behaviourally: an
    anchor-targeted call promotes with the guard ON, and the trace records
    ``applied: False`` with ``targeting: "anchor"`` — so the receipt can never
    be read as "the guard ran here"."""
    pool = (
        [{"id": "n0", "session_id": "n0", "session_date": "2023-04-10",
          "content": "unrelated words"},
         {"id": "n1", "session_id": "n1", "session_date": "2023-04-11",
          "content": "unrelated words"},
         {"id": "gold", "session_id": "g", "session_date": "2023-04-12",
          "content": "smoker brisket"}]
    )
    trace: dict = {}
    _, picks = tl.temporal_leg_fusion_order(
        pool, anchors=("smoker brisket",), window=2, limit=1,
        guard_trace=trace)
    assert picks == ["gold"], picks
    assert trace["applied"] is False
    assert trace["targeting"] == "anchor"
    assert trace["rejected"] == []


def test_the_head_counterfactual_is_exempt_from_the_guard():
    """Guard on, ``placement="head"`` still promotes: that arm exists to
    MEASURE the displacement the guard forbids, so guarding it would answer the
    placement question by construction."""
    trace: dict = {}
    _, head_picks = tl.temporal_leg_fusion_order(
        ISSUE_POOL, anchors=(), window=WINDOW, limit=LIMIT,
        date_window=RANGE, placement="head", guard_trace=trace)
    assert head_picks, "the counterfactual must stay reproducible"
    assert trace.get("applied") is False
    assert trace.get("rejected") == []


# ── 9. the SHIPPED call forwards it, and the receipt carries it ────────────

def _call_keywords(fn_name: str, callee: str) -> list[dict]:
    src = Path(r.__file__).read_text()
    calls = [
        node for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name) and node.func.id == callee
    ]
    assert calls, f"the {callee} call site vanished — re-anchor this pin"
    return [{kw.arg: kw.value for kw in call.keywords} for call in calls]


def test_the_shipped_call_forwards_the_resolved_guard():
    for keywords in _call_keywords("retrieve_for_question",
                                   "temporal_leg_fusion_order"):
        assert "non_eviction" in keywords, (
            "the leg is called without non_eviction= — the resolved guard "
            "would never reach the arm (#10538)")
        passed = keywords["non_eviction"]
        assert isinstance(passed, ast.Name) and passed.id == "_tl_non_eviction", (
            "the call site must pass the RESOLVED switch (_tl_non_eviction), "
            "not a constant — a literal True would make the env off-switch a "
            "lie while the receipt still echoed it")


def _shipped_call() -> dict:
    calls = _call_keywords("retrieve_for_question", "temporal_leg_fusion_order")
    assert len(calls) == 1, calls
    return calls[0]


def test_the_shipped_call_forwards_the_GUARD_TRACE_too():
    """#10538 (cycle 10, finding 1): the `guard_trace=` forward was unpinned.

    Deleting it kept every suite green - the PICK filter still runs, so nothing
    looks broken - while every row's receipt degraded to ``applied: False``,
    ``targeting: "none"``, ``rejected: []`` and a guard-caused empty leg reverted
    to ``no_window_candidates``. That is the false PASS this field pair exists to
    prevent, one line up in the wiring from the pins that check the values.
    """
    keywords = _shipped_call()
    assert "guard_trace" in keywords, (
        "the leg is called without guard_trace= - every receipt row would "
        "report the guard as never applied (#10538)")
    passed = keywords["guard_trace"]
    assert isinstance(passed, ast.Name) and passed.id == "_tl_guard_trace", (
        ast.unparse(passed))


def test_the_shipped_call_RESOLVES_the_switch_instead_of_using_the_constant():
    """#10538 (cycle 10, finding 2a): replacing the resolver with
    ``DEFAULT_TEMPORAL_LEG_NON_EVICTION`` kept 89/89 green, which turns the
    ``TORTOISE_LME_TEMPORAL_LEG_NON_EVICTION=0`` off-switch into a no-op while
    ``run.py`` still resolves that env into the fingerprint - so a checkpoint
    stamped guard-OFF becomes resumable by a build that ran the guard.
    """
    src = Path(r.__file__).read_text()
    tree = ast.parse(src)
    resolver_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "resolve_temporal_leg_non_eviction"
    ]
    assert resolver_calls, (
        "retrieve.py no longer resolves the non-eviction switch - the env "
        "off-switch is dead (#10538)")
    # ⚠️ The KEY LITERAL, not just "some os.environ.get(" (cycle 11, finding 1):
    # the prefix form accepted a renamed key, e.g.
    # ``os.environ.get("TORTOISE_LME_TEMPORAL_LEG_PLACEMENT")``, which keeps the
    # off-switch dead while ``run.py`` still resolves the real key into the
    # fingerprint — a guard-OFF checkpoint resuming under a guard-ON build.
    def _is_env_read(node: ast.AST, key: str) -> bool:
        return (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "environ"
                and bool(node.args)
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == key)

    assert any(
        any(_is_env_read(arg, "TORTOISE_LME_TEMPORAL_LEG_NON_EVICTION")
            for arg in node.args)
        for node in resolver_calls
    ), (
        "the resolver must be handed the SAME env the fingerprint reads: "
        "os.environ.get('TORTOISE_LME_TEMPORAL_LEG_NON_EVICTION')")
    # ...and the resolved value must actually BE what the arm receives: the
    # name is assigned EXACTLY ONCE, from that call (a later
    # ``_tl_non_eviction = True`` would discard it — cycle 11, finding 1b).
    assigned = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "_tl_non_eviction"
                for t in node.targets)
    ]
    assert len(assigned) == 1, (
        f"_tl_non_eviction must be assigned exactly once: {len(assigned)}")
    assert (isinstance(assigned[0].value, ast.Call)
            and isinstance(assigned[0].value.func, ast.Name)
            and assigned[0].value.func.id
            == "resolve_temporal_leg_non_eviction"), (
        f"the switch must BE the resolver's result, not "
        f"{ast.unparse(assigned[0].value)}")
    # ...and the resolved name is what gets forwarded
    assert _shipped_call()["non_eviction"].id == "_tl_non_eviction"


def test_the_receipt_ECHOES_the_resolved_switch_not_a_literal():
    """#10538 (cycle 10, finding 2b): ``"non_eviction": _tl_non_eviction`` ->
    ``True`` kept every gate green, so every guard-OFF row - the off-switch's
    whole purpose - would attest the ruling as ON."""
    slots = 0
    for node in _stats_dicts():
        for key, value in zip(node.keys, node.values, strict=True):
            if isinstance(key, ast.Constant) and key.value == "non_eviction":
                slots += 1
                assert (isinstance(value, ast.Name)
                        and value.id == "_tl_non_eviction"), (
                    "non_eviction must echo the RESOLVED switch: "
                    f"{ast.unparse(value)}")
    assert slots == 1, slots


def test_the_receipt_REJECTION_list_comes_from_the_trace():
    """#10538 (cycle 10, finding 4): ``non_eviction_rejected`` could be emptied
    to ``[]`` with every gate green - the field the design calls "where the ids
    come from" would carry none."""
    found = False
    for node in _stats_dicts():
        for key, value in zip(node.keys, node.values, strict=True):
            if not (isinstance(key, ast.Constant)
                    and key.value == "non_eviction_rejected"):
                continue
            found = True
            # exact receiver + key, structurally (cycle 11, finding 2):
            # `list({}.get("rejected") or [])` shared the token and would empty
            # the field for every row — the field the design calls "where the
            # ids come from".
            assert _wraps(value, "list", "rejected"), ast.unparse(value)
    assert found, "the receipt lost non_eviction_rejected"


def test_the_empty_leg_reason_gets_the_REAL_refusal_count():
    """#10538 (cycle 10, finding 3): only the keyword NAME was pinned, so
    ``guard_rejected=len(trace.get("rejected") or [])`` -> ``0`` survived -
    making ``non_eviction_guard`` unproducible at the shipped surface and
    turning a guard refusal into ``no_window_candidates``."""
    calls = _call_keywords("retrieve_for_question",
                           "temporal_leg_empty_reason")
    assert calls, "the empty-reason call site vanished - re-anchor this pin"
    # exact receiver AND key (cycle 11, finding 2): a prefix check accepted
    # `len(_tl_guard_trace.get("accepted") or [])`, which makes
    # `non_eviction_guard` unproducible — every guard refusal re-reads as
    # `no_window_candidates`.
    assert any(
        _wraps(kw["guard_rejected"], "len", "rejected")
        for kw in calls if "guard_rejected" in kw
    ), [ast.unparse(kw["guard_rejected"]) for kw in calls
        if "guard_rejected" in kw]


def _is_trace_get(node: ast.AST, key: str) -> bool:
    """``_tl_guard_trace.get("<key>")`` — receiver AND key, structurally.

    ⚠️ Structural, not textual (cycle 11): a substring test on ``ast.unparse``
    is quote-sensitive, and ``"rejected" in unparse(value)`` accepted a FOREIGN
    receiver or the wrong key.
    """
    return (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "_tl_guard_trace"
            and bool(node.args)
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == key)


def _wraps(call: ast.AST, fn_name: str, inner_key: str) -> bool:
    """``<fn_name>(_tl_guard_trace.get("<inner_key>") ...)``"""
    return (isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == fn_name
            and any(_is_trace_get(node, inner_key) for node in ast.walk(call)))


def _stats_dicts() -> list[ast.Dict]:
    src = Path(r.__file__).read_text()
    return [
        node for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Dict)
        and any(isinstance(k, ast.Constant) and k.value == "non_eviction"
                for k in node.keys)
    ]


def test_the_receipt_carries_the_guard_and_its_refusals():
    stats = _stats_dicts()
    assert stats, "temporal_leg_stats lost the non_eviction field (#10538)"
    keys = {k.value for node in stats for k in node.keys
            if isinstance(k, ast.Constant)}
    # ⚠️ ALL FOUR, in the arm-bearing stats dict itself (cycle 3, finding 3):
    # `non_eviction_targeting` was left unpinned, so renaming or dropping the
    # field that makes `applied: False` legible would have gone unnoticed — and
    # every scoped-out row would read like a window row where the guard ran and
    # admitted everything.
    assert {"non_eviction", "non_eviction_applied", "non_eviction_targeting",
            "non_eviction_rejected"} <= keys, keys
    # ...and the targeting field is BOUND to the guard's trace, not re-derived.
    # ⚠️ KEY-level, not dict-level (cycle 4, finding 3): asking only whether
    # SOME value in SOME stats dict calls ``.get("targeting")`` stayed green
    # under the swap ``"non_eviction_targeting": non_eviction_applied(...)`` /
    # ``"non_eviction_applied": trace.get("targeting")`` — i.e. under exactly
    # the collapse of the field pair that this pin exists to prevent. So the
    # check pairs each dict's keys with its values POSITIONALLY and inspects the
    # value sitting at the ``non_eviction_targeting`` key itself.
    def _binds(node: ast.Dict) -> bool:
        for key, value in zip(node.keys, node.values, strict=True):
            if not (isinstance(key, ast.Constant)
                    and key.value == "non_eviction_targeting"):
                continue
            return _emits_targeting(value)
        return False

    def _is_targeting_get(node: ast.AST) -> bool:
        # the RECEIVER too (cycle 12, finding 2): without `node.func.value`
        # being `_tl_guard_trace`, any other in-scope mapping Name satisfied
        # this and the field degraded to `"none"` on every row.
        return (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "_tl_guard_trace"
                and bool(node.args)
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "targeting")

    def _emits_targeting(value: ast.AST) -> bool:
        """Is the EMITTED value the targeting lookup?

        ⚠️ TOP-LEVEL, not a subtree search (cycle 9, finding 1): `any(… for node
        in ast.walk(value))` also matched ``not trace.get("targeting")`` and
        ``(trace.get("targeting") if False else …)``, so a one-token polarity
        inversion shipped the inverse receipt with every gate green. The value
        may legitimately be wrapped by the documented ``or "none"``
        normalisation, so an ``or`` chain is accepted — but only when its FIRST
        operand is the lookup and no ``not`` sits anywhere in the emitted
        expression.
        """
        if _is_targeting_get(value):
            return True
        if isinstance(value, ast.BoolOp) and isinstance(value.op, ast.Or):
            return bool(value.values) and _is_targeting_get(value.values[0])
        if isinstance(value, ast.BinOp):        # `get(...) or "none"` lowered
            return _is_targeting_get(getattr(value, "left", None))
        return False

    assert any(_binds(node) for node in stats), (
        "non_eviction_targeting must come from guard_trace.get('targeting')")
    # ...and the APPLIED slot must carry the applied PREDICATE — the mirror of
    # `_binds`. ⚠️ The first version of this assertion named a symbol that does
    # not exist (`_non_eviction_targeting`), so its conjunction was permanently
    # False and the assertion could never fire (review cycle 8, finding 1). The
    # second version searched the SUBTREE for a call, which caught the swap but
    # not the POLARITY: ``not non_eviction_applied(trace)`` — the inverse receipt
    # on every row — stayed green (cycle 9, finding 1). So the check is on the
    # TOP-LEVEL expression: exactly a call to the predicate over the guard trace,
    # with no unary/branch wrapper. That is what the field emits, and a
    # semantics-preserving rewrite must update this pin — which is the point.
    def _is_applied_predicate(value: ast.AST) -> bool:
        return (isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id == "non_eviction_applied"
                and len(value.args) == 1
                # the ARGUMENT must be the guard's trace (cycle 12, finding 2):
                # any Name satisfied the previous form, so a shadow mapping
                # reported `False` on every row with all four pins green.
                and isinstance(value.args[0], ast.Name)
                and value.args[0].id == "_tl_guard_trace")

    applied_slots = 0
    for node in stats:
        for key, value in zip(node.keys, node.values, strict=True):
            if (isinstance(key, ast.Constant)
                    and key.value == "non_eviction_applied"):
                applied_slots += 1
                assert _is_applied_predicate(value), (
                    "non_eviction_applied must BE non_eviction_applied(trace) "
                    f"— polarity included — not {ast.unparse(value)}")
    assert applied_slots == 1, applied_slots


def test_the_leg_trace_passes_the_refusal_count_to_the_empty_reason():
    for keywords in _call_keywords("retrieve_for_question",
                                   "temporal_leg_empty_reason"):
        assert "guard_rejected" in keywords, (
            "an empty leg caused by the guard would be laundered as "
            "'no_window_candidates' (#10538)")


def _fp(monkeypatch, *, leg: str | None, non_eviction: str | None):
    """The eval fingerprint under a given (leg, guard) arm state."""
    from tools.longmem_eval import run as runner

    for name, value in (("TORTOISE_LME_TEMPORAL_LEG", leg),
                        ("TORTOISE_LME_TEMPORAL_LEG_NON_EVICTION",
                         non_eviction)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    return runner._build_fingerprint(
        reader_model="r", judge_model="j", ks=(5,), top_k=5, split="s",
        ingest_mode="deterministic", extractor_model=None, max_retries=3,
        dataset_fingerprint="x", rerank_config={})


def test_the_guard_is_stamped_into_the_run_fingerprint(monkeypatch, tmp_path):
    """Behavioural, not a grep: flipping the switch must change the fingerprint.

    The switch is flipped the ordinary way — env + the SAME `--checkpoint` — so
    without this key every completed question is reused from the OTHER arm and
    the report's aggregate is a two-arm mixture presented as one arm. ⚠️ The
    first head of this PR pinned only that the string appeared in `run.py`
    (fresh-context review, finding 4): an inverted `!=` there passed that pin
    while stamping every guard-ON run and leaving guard-OFF unstamped.
    """
    assert _fp(monkeypatch, leg="1", non_eviction=None).get(
        "temporal_leg_non_eviction") is None, (
        "the shipped (guard-ON) default must leave the key ABSENT so every "
        "existing arm-ON checkpoint still resumes byte-identically")
    off = _fp(monkeypatch, leg="1", non_eviction="0")
    assert off["temporal_leg_non_eviction"] is False, off
    # a typo resolves to the shipped default, so it must NOT mint a new arm
    assert _fp(monkeypatch, leg="1", non_eviction="of").get(
        "temporal_leg_non_eviction") is None
    # and a leg-OFF run carries no guard key at all (the whole arm is absent)
    assert _fp(monkeypatch, leg=None, non_eviction="0").get(
        "temporal_leg_non_eviction") is None


def test_a_guard_off_checkpoint_refuses_to_resume_a_guarded_run(monkeypatch):
    """The consequence, pinned end-to-end: the two arms do not mix."""
    from tools.longmem_eval import run as runner

    guarded = _fp(monkeypatch, leg="1", non_eviction=None)
    off = _fp(monkeypatch, leg="1", non_eviction="0")
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        cp = Path(td) / "cp.json"
        runner._save_checkpoint(str(cp), [], [], guarded)
        try:
            runner._load_checkpoint(str(cp), off)
        except runner.CheckpointStaleError as exc:
            assert "temporal_leg_non_eviction" in str(exc), str(exc)
        else:  # pragma: no cover - the failure this test exists for
            raise AssertionError(
                "a guard-OFF run resumed a guard-ON checkpoint — the "
                "aggregate would be a two-arm mixture (#10538)")

def test_the_run_fingerprint_stamps_a_guarded_vs_unguarded_arm():
    """A guard-OFF checkpoint must never resume under the guard ON: the
    promotion sets differ, so the fused orders differ."""
    src = (Path(r.__file__).parent / "run.py").read_text()
    keys = {
        node.value for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
    }
    assert "temporal_leg_non_eviction" in keys, (
        "the run fingerprint does not stamp the non-eviction arm — the "
        "ordinary way to flip the switch (env + the SAME --checkpoint) would "
        "report a two-arm mixture as one arm (#10538)")


# ── 10. the receipt's `applied` is the GUARD's answer, not the knob's ──────

def test_the_receipt_reports_whether_the_guard_RAN_not_whether_it_was_ON():
    """``non_eviction_applied`` must come from the guard's trace.

    Re-deriving it from the knobs (``the switch resolved ON and the placement is
    not 'head'``) reports enforcement on the two shapes where the guard is
    scoped OUT and never ran: the ``#2976`` ANCHOR arm (unguarded by decision,
    measured as-is) and an INERT leg (no picks, so the guard was never reached
    and the trace carries no ``applied`` key at all).

    Both shapes are built here from REAL fusion calls — the anchor arm's own
    trace above, the inert leg's own trace — rather than hand-written dicts, so
    a change to the guard's trace shape re-fails this pin instead of passing
    silently.
    """
    pool = (
        [{"id": "n0", "session_id": "n0", "session_date": "2023-04-10",
          "content": "unrelated words"},
         {"id": "n1", "session_id": "n1", "session_date": "2023-04-11",
          "content": "unrelated words"},
         {"id": "gold", "session_id": "g", "session_date": "2023-04-12",
          "content": "smoker brisket"}]
    )
    anchor_trace: dict = {}
    _, anchor_picks = tl.temporal_leg_fusion_order(
        pool, anchors=("smoker brisket",), window=2, limit=1,
        guard_trace=anchor_trace)
    assert anchor_picks == ["gold"], anchor_picks
    # the rule is ON and the placement is the shipped "tail" — the knob-derived
    # answer would be True; the guard never ran.
    assert anchor_trace["on"] is True
    assert anchor_trace["applied"] is False
    assert r.non_eviction_applied(anchor_trace) is False, (
        "the receipt claims the guard was ENFORCED on the unguarded #2976 "
        "anchor arm")

    inert_trace: dict = {}
    _, inert_picks = tl.temporal_leg_fusion_order(
        pool, anchors=(), window=2, limit=1, guard_trace=inert_trace)
    assert inert_picks == []
    # ⚠️ This pin first asserted `"applied" not in inert_trace` (the inert path
    # left the key absent). The inert path now STAMPS `applied: False` — the
    # same answer, in a shape a receipt reader cannot mistake for a missing
    # field — so the pin states the value instead of the absence. What must not
    # change is the ANSWER: an inert leg never reached the guard.
    assert inert_trace.get("applied") is False, inert_trace
    assert r.non_eviction_applied(inert_trace) is False, (
        "an inert leg never reached the guard, so it cannot report it applied")
    assert r.non_eviction_applied(None) is False

    # ...and the shapes where it DID run still report it, in both directions.
    _, _, guarded_trace = _fuse(ISSUE_POOL)              # refused every pick
    assert guarded_trace["applied"] is True
    assert r.non_eviction_applied(guarded_trace) is True
    _, _, off_trace = _fuse(ISSUE_POOL, non_eviction=False)
    assert off_trace["applied"] is False
    assert r.non_eviction_applied(off_trace) is False


def test_the_retention_measure_does_not_still_claim_it_never_gates():
    """The measurement's docstring is the file a bypass gets argued from.

    ``window_arm_retention`` carried a hard "it never gates / nothing in this
    module branches on any value it returns" promise, written while the
    ``window-exit`` vs ``ab-head-ab`` question was OPEN. The owner ruled
    ``window-exit`` on 2026-11-07, so ``_non_eviction_filter`` now branches on
    ``deleted_sessions`` — and a docstring still promising the opposite tells a
    later lane that the gating it can see is a bug to be removed.
    """
    doc = tl.window_arm_retention.__doc__ or ""
    assert "never gates" not in doc, (
        "the retention docstring still promises this measure never gates, but "
        "_non_eviction_filter branches on deleted_sessions under the "
        "2026-11-07 ruling")
    assert "Nothing in this module branches" not in doc
    assert "_non_eviction_filter" in doc, (
        "the docstring must NAME its one gating consumer, so the scope is read "
        "from the measurement it is justified by")
    # the gate is real, not just documented: the issue's own pool is refused.
    _, g_picks, g_trace = _fuse(ISSUE_POOL)
    assert g_picks == [] and g_trace["applied"] is True


def test_the_baseline_instrument_does_not_still_call_the_ruling_open():
    """#10538 (cycle 5): the same stale-status class, in the sibling file this
    PR edits. ``window_arm_baseline``'s MODULE docstring — which is also its
    ``--help`` description, so it is operator-facing, not internal prose — said
    the predicate "is reported here, never enforced: whether an arm MAY pay for
    a slot by evicting base evidence is the open owner ruling". The owner ruled
    ``window-exit`` on 2026-11-07 and the guard ships ON, so a reader of that
    line concludes eviction is undecided and a refusal is the bug to remove.

    The correct statement is the one the module now makes: the receipt's SUBJECT
    is the pre-ruling splice, and the still-open question is PLACEMENT.
    """
    from tools.longmem_eval import window_arm_baseline as wab

    doc = wab.__doc__ or ""
    assert "never enforced" not in doc, (
        "the instrument still calls the predicate report-only, but the "
        "2026-11-07 ruling is enforced by _non_eviction_filter")
    assert "open owner ruling" not in doc
    assert "RULED" in doc, (
        "the docstring must state that the ruling is decided, so a lane reading "
        "it cannot argue a bypass from an 'undecided' premise")
    # and it still names the one thing that IS open, or the correction over-reads
    assert "PLACEMENT" in doc


def test_both_switches_DEFAULT_to_the_shipped_arm():
    """#10538 (cycle 10, findings 5 and 6): the DEFAULTS were unpinned.

    Flipping the replay CLI's ``--non-eviction`` ``default="on"`` -> ``"off"``
    kept every test green (the CLI pin only exercises explicit flags), so the
    instrument's no-flag run would measure the PRE-RULING splice while
    ``replay()``'s own API default stayed guarded; relaxing
    ``temporal_leg_fusion_order``'s ``max_chunks_per_session`` default to
    ``None`` also survived, silently reverting every non-threading caller (the
    replay passes no cap) to the pre-reader view the docstring warns against.

    Both are DEFAULTS, so they are pinned on the signature/argparse node - no
    behavioural call can see them without a different fixture for each.
    """
    from tools.longmem_eval import temporal_leg_replay as rep

    src = Path(rep.__file__).read_text()
    defaults = [
        node for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_argument"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "--non-eviction"
    ]
    assert defaults, "the replay lost its --non-eviction flag"
    for call in defaults:
        kw = {k.arg: k.value for k in call.keywords}
        assert isinstance(kw.get("default"), ast.Constant) and \
            kw["default"].value == "on", (
                f"--non-eviction defaults to {ast.unparse(kw.get('default'))}, "
                "but the shipped arm is ON (#10538)")

    # the fusion signature's cap default
    src = Path(tl.__file__).read_text()
    fns = [
        node for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.FunctionDef)
        and node.name == "temporal_leg_fusion_order"
    ]
    assert fns, "temporal_leg_fusion_order vanished"
    args = fns[0].args
    names = [a.arg for a in args.kwonlyargs]
    assert "max_chunks_per_session" in names, names
    default = args.kw_defaults[names.index("max_chunks_per_session")]
    assert isinstance(default, ast.Name) and \
        default.id == "DEFAULT_MAX_CHUNKS_PER_SESSION", (
            "the fusion default must be the reader's real cap, not "
            f"{ast.unparse(default)} - a None default reverts every caller that "
            "does not thread the run's cap to the pre-reader view (#10538)")


# ── 11. the REPLAY instrument's switch and stamp ───────────────────────────

def _replay_question() -> dict:
    """One hand-built window-arm question: a dated range, one dominant session,
    and gold in the deeper sessions the arm exists to reach."""
    # ONE session owns the visible window (that is the clustering the guard's
    # window branch requires); the deeper sessions are single-turn and carry
    # gold, so a promotion would reach them.
    sessions = [[{"content": "alpha beta gamma", "has_answer": True}]
                for _ in range(10)]
    sessions += [[{"content": "delta epsilon zeta", "has_answer": True}],
                 [{"content": "eta theta iota", "has_answer": True}],
                 [{"content": "iota kappa lambda", "has_answer": True}]]
    return {
        "question": "What did I do between 2023-04-10 and 2023-04-20?",
        "question_date": "2023-04-25",
        "haystack_sessions": sessions,
        "haystack_dates": (["2023-04-15"] * 10
                           + ["2023-04-16", "2023-04-17", "2023-04-18"]),
        "haystack_session_ids": (["sA"] * 10 + ["sB", "sC", "sD"]),
    }


def test_the_replay_switch_is_forwarded_AND_stamped():
    """#10538 (cycle 3, finding 2): the replay inherited the guard with neither a
    switch nor a stamp, so a guarded row and a pre-ruling row were two
    indistinguishable receipts with different numbers.

    Both halves are pinned here because either alone is useless: forwarding
    without the stamp gives an unattributable row, and the stamp without
    forwarding LIES about the row. The fixture is measured to actually differ
    between the arms, so the assertion cannot pass on an arm that never fires.
    """
    from tools.longmem_eval import temporal_leg_replay as rep

    q = _replay_question()
    guarded = rep.replay([q], window=12)
    unguarded = rep.replay([q], window=12, non_eviction=False)

    # (i) the stamp: each row names the guard state that produced it
    assert guarded["non_eviction"] is True, guarded
    assert unguarded["non_eviction"] is False, unguarded
    # (ii) the forward, WITH ITS DIRECTION. ⚠️ `!=` alone was direction-blind
    # (cycle 4, finding 2): inverting the kwarg at the fusion call
    # (`non_eviction=not non_eviction`) merely SWAPPED the two counts, so a pin
    # that only asked "different?" stayed green while the shipped default ran
    # the unguarded splice and every row still attested to the resolved switch
    # — a false attestation of the ruling. The exact counts are pinned, so an
    # inversion reds here no matter which way the fixture's numbers fall.
    assert unguarded["leg_fired"] == 1, unguarded          # the arm DOES fire
    assert guarded["leg_fired"] == 0, guarded              # ...and is refused
    assert guarded["non_eviction"] is True and unguarded["non_eviction"] is False


def test_the_replay_cli_exposes_the_switch_and_normalises_it():
    """The operator's spelling (`--non-eviction on|off`) is the one that must
    reach `replay()` — a flag that parses but does not forward is the shape that
    produced this finding."""
    import sys

    from tools.longmem_eval import temporal_leg_replay as rep

    seen: dict = {}
    real_replay, real_subset = rep.replay, rep._subset_questions

    def _spy(questions, **kwargs):
        seen.update(kwargs)
        # the reporter the CLI prints from — every key it reads, zeroed.
        return {
            "questions": 0, "window": 0, "leg_fired": 0,
            "evidence_turn_recall_base": 0.0, "evidence_turn_recall_leg": 0.0,
            "all_gold_turns_base": 0, "all_gold_turns_leg": 0,
            "all_gold_sessions_base": 0, "all_gold_sessions_leg": 0,
            "worst_gold_rank_up": 0, "worst_gold_rank_down": 0,
            "budget_effective": 0, "limit_requested": 0,
            "placement": "tail", "weight": 1.0, "bucket_cap": 1,
            "non_eviction": kwargs.get("non_eviction"),
        }

    try:
        rep.replay = _spy
        rep._subset_questions = lambda data=None: []   # no dataset read
        for flag, expected in (("on", True), ("off", False)):
            seen.clear()
            rep.main(["--non-eviction", flag])
            assert seen.get("non_eviction") is expected, (flag, seen)
    finally:
        rep.replay = real_replay
        rep._subset_questions = real_subset
        sys.modules.pop("__main__", None)


# ── 12. the inert targeting value has a producer ───────────────────────────

def test_an_anchorless_leg_stamps_the_documented_none_target():
    """#10538 (cycle 4, finding 4): ``targeting_trace`` documents
    ``"anchor" | "window" | "none"``, but every "no target existed" path
    RETURNED before the end-of-function write, so ``"none"`` had no producer and
    the receipt's ``non_eviction_targeting`` came out JSON ``null``. A consumer
    keying on ``"none"`` therefore missed every anchor-less row — and that field
    is the only thing that makes ``applied: False`` legible.

    Each early return is exercised, because each was a separate way out.
    """
    from tortoise.temporal_leg import temporal_leg_order

    def _trace(pool, **kw):
        trace: dict = {}
        temporal_leg_order(pool, targeting_trace=trace, **kw)
        return trace

    dated = [{"id": f"c{i}", "content": "alpha beta", "session_date": "2023-04-15"}
             for i in range(3)]

    # (a) no dated candidate at all
    pool = [{"id": "c0", "content": "alpha beta"}]
    assert _trace(pool, anchors=(), window=3, limit=2)["targeting"] == "none"
    # (b) nothing promotable — every dated candidate sits inside the head slice
    assert _trace(dated, anchors=(), window=3, limit=2, head=99)["targeting"] == "none"
    # (c) a degenerate budget returns before any targeting work
    assert _trace(dated, anchors=(), window=3, limit=0)["targeting"] == "none"
    # The FIRING arms keep naming themselves — ``"anchor"`` and ``"window"``
    # already have their own discriminating pins
    # (``test_the_issue_probe_pool_is_net_zero_unguarded_and_additive_guarded``,
    # ``test_the_anchor_arm_is_not_guarded_and_says_so`` and
    # ``test_temporal_leg_placement_10538.py``'s guarded-arm case), so this
    # case only has to
    # prove the up-front stamp does not CLOBBER them.
    assert _trace(ISSUE_POOL, anchors=(), window=WINDOW, limit=LIMIT,
                  head=WINDOW - LIMIT, date_window=RANGE)["targeting"] == "window"


def test_the_receipt_normalises_a_traceless_target_to_the_documented_none():
    """The RECEIPT surface, not just the producer: the stats block must spell
    ``"none"`` even when the trace it was handed carries no ``targeting`` key at
    all — the documented domain, never ``null``. Pinned on the emitted expression
    because that is the only place the normalisation lives, and pinned to the
    ``non_eviction_targeting`` KEY (cycle 4, finding 3's lesson)."""
    src = Path(r.__file__).read_text()
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values, strict=True):
            if not (isinstance(key, ast.Constant)
                    and key.value == "non_eviction_targeting"):
                continue
            found = True
            # the value is a BoolOp/`or` chain ending in the literal "none"
            literals = [n.value for n in ast.walk(value)
                        if isinstance(n, ast.Constant)
                        and isinstance(n.value, str)]
            assert "none" in literals, (
                f"non_eviction_targeting must normalise to 'none': {ast.dump(value)}")
    assert found, "the receipt lost the non_eviction_targeting field"


# ── 13. the remaining wiring tokens the receipt block depends on ───────────

def test_the_trace_holder_is_an_empty_dict_not_None():
    """#10538 (cycle 11, finding 4): ``_tl_guard_trace: dict[str, Any] = {}`` was
    unpinned, so ``= None`` survived every suite — and ``_tl_guard_trace.get(…)``
    then raises ``AttributeError`` on every guarded-leg row. Load-bearing
    precisely because no test drives ``retrieve_for_question`` with the temporal
    leg on, so this block is defended only by AST pins."""
    src = Path(r.__file__).read_text()
    holders = [
        node for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "_tl_guard_trace"
    ]
    assert len(holders) == 1, f"{len(holders)} declarations of _tl_guard_trace"
    assert isinstance(holders[0].value, ast.Dict) and not holders[0].value.keys, (
        "_tl_guard_trace must be initialised to an empty dict, not "
        f"{ast.unparse(holders[0].value) if holders[0].value else None}")

    # ⚠️ AND IT MUST BE THE ONLY BINDING IN THE SHIPPED FUNCTION (cycle 12,
    # finding 1): counting declarations missed a later `_tl_guard_trace = {}`
    # (or `.clear()`) after the leg call — a plausible "re-init after handing it
    # over" refactor that erases the guard's entire receipt while the guard
    # still enforces. Every receipt field and the empty-leg reason read this
    # object, so the row would attest `applied: False`, `targeting: "none"`,
    # `rejected: []` and revert a guard refusal to `no_window_candidates` — the
    # false PASS this whole pin family exists to prevent.
    fn = [
        node for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.FunctionDef)
        and node.name == "retrieve_for_question"
    ]
    assert len(fn) == 1, f"{len(fn)} definitions of retrieve_for_question"
    body = fn[0].body
    bindings = [
        node for node in ast.walk(ast.Module(body=body, type_ignores=[]))
        if (isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "_tl_guard_trace"
                    for t in node.targets))
        or (isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "_tl_guard_trace")
    ]
    assert len(bindings) == 1, (
        "_tl_guard_trace must be bound exactly ONCE in "
        f"retrieve_for_question; found {len(bindings)} — a later rebind erases "
        "the guard's receipt (#10538)")
    # ⚠️ NOT JUST rebind/clear (cycle 13, finding 1): `|=`, `.update(...)`,
    # `.pop(...)`, `del trace[...]` and `.setdefault(...)` all mutate the same
    # object after the leg call and every one of them kept the suite green. The
    # holder is the WHOLE receipt's storage, so the rule is stated positively:
    # after its single binding, `_tl_guard_trace` may only be READ — i.e. appear
    # as ``_tl_guard_trace.get(...)``, as a bare name argument (``applied``,
    # ``guard_trace=``, ``non_eviction_applied``), or inside ``len(...)``.
    def _root_name(node: ast.AST) -> str | None:
        while True:
            if isinstance(node, ast.Name):
                return node.id
            if isinstance(node, (ast.Attribute, ast.Subscript)):
                node = node.value
            elif isinstance(node, ast.Call):
                node = node.func
            else:
                return None

    writes = []
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if (isinstance(node, ast.AugAssign) and _root_name(node.target) == \
                "_tl_guard_trace") or (isinstance(node, ast.Delete) and any(
                _root_name(t) == "_tl_guard_trace" for t in node.targets)):
            writes.append(ast.unparse(node))
        elif isinstance(node, ast.Call) and _root_name(node) == "_tl_guard_trace":
            fn = node.func
            attr = fn.attr if isinstance(fn, ast.Attribute) else None
            if attr not in ("get",):
                writes.append(ast.unparse(node))
    assert not writes, (
        "_tl_guard_trace may only be READ after its single binding; these "
        f"mutate the guard's receipt: {writes} (#10538)")


def test_the_shipped_call_arms_the_guard_with_the_QUESTION_WINDOW():
    """#10538 (cycle 11, finding 5): the guard is scoped to the DATE-WINDOW arm,
    so ``date_window=_tl_date_window`` at the shipped call is the guard's ONLY
    production reachability. ``date_window=None`` there survives every suite
    (they call the leg directly), silently reverting #10538 to "shipped but
    never armed" while all the mechanism pins stay green.
    """
    keywords = _shipped_call()
    expectations = {
        "date_window": "_tl_date_window",
        "window": "effective_top_k",
        "limit": "_tl_limit",
        "weight": "_tl_weight",
    }
    for name, ident in expectations.items():
        assert name in keywords, f"the shipped call dropped {name}="
        node = keywords[name]
        assert isinstance(node, ast.Name) and node.id == ident, (
            f"{name}= must be {ident}, not {ast.unparse(node)}")


def test_the_inert_leg_reports_the_target_it_actually_had():
    """#10538 (cycle 11, minor): the inert branch's ``targeting`` value was
    unpinned, so a window-targeted row with no picks could be mislabelled
    ``"none"`` — the one field that makes ``applied: False`` legible.

    Pinned behaviourally: a clustered window-shaped pool whose picks the guard
    admits NOTHING for still reports ``targeting: "window"`` (the arm had a
    target, it just produced no pick) and ``applied`` is False.
    """
    trace: dict = {}
    tl.temporal_leg_fusion_order(
        ISSUE_POOL, anchors=(), window=WINDOW, limit=LIMIT,
        date_window=RANGE, guard_trace=trace, weight=0.0)
    assert trace["applied"] is False, trace
    assert trace["targeting"] == "window", (
        "an inert but WINDOW-targeted row must not report 'none' — it had a "
        f"target: {trace}")
    # ⚠️ `on` is the RESOLVED SWITCH on every branch (cycle 13, finding 3): the
    # enforcing path's write was unpinned and had no shipped consumer, so
    # ``"on": False`` there survived — a trace reporting the rule OFF on the one
    # path where it is ENFORCING, which is the conflation `applied`'s docstring
    # forbids.
    assert trace["on"] is True, trace
    assert trace["rejected"] == [] and trace["accepted"] == []


def test_a_REDUNDANT_slot_may_be_traded_but_a_deleted_session_may_not():
    """#10538 (cycle 11, finding 3): the guard's third condition has to read
    ``deleted_sessions``, not ``evicted_sessions`` — they are deliberately
    different (``evicted`` is the superset: sessions that lost *A* slot), and the
    ruling's unit is the SESSION.

    Unpinned they were indistinguishable, because every fixture where the reason
    fires has the two fields coinciding. This is the shape where they differ:
    ``sB`` holds TWO slots inside the six-slot window, the pick displaces one of
    them and ``sB`` stays visible through the other. So the arm is WIDENING by
    trading a redundant slot — explicitly allowed — and the guard must ADMIT it.

    With ``evicted_sessions`` in that branch instead, the pick is refused and the
    reason names a session that was never deleted: net harm reported where the
    ruling permits the trade, which is a guard that blocks the fix it belongs to.
    """
    pool = (
        [{"id": f"a{i}", "session_id": "sA", "session_date": "2023-04-01",
          "content": "alpha"} for i in range(4)]
        + [{"id": "b0", "session_id": "sB", "session_date": "2023-04-02",
            "content": "alpha"},
           {"id": "b1", "session_id": "sB", "session_date": "2023-04-02",
            "content": "alpha"}]
        + [{"id": f"c{i}", "session_id": "sC", "session_date": "2023-04-03",
            "content": "alpha"} for i in range(4)]
    )
    geometry = dict(window=6, limit=1,
                    date_window=("2023-04-01", "2023-04-30"),
                    max_chunks_per_session=None)

    # the UNGUARDED arm really does trade the redundant slot ...
    u_order, u_picks = tl.temporal_leg_fusion_order(
        pool, non_eviction=False, **geometry)
    assert u_picks, "this fixture must be the FIRING case"
    u = tl.window_arm_retention(pool, u_order, picks=u_picks, window=6,
                                max_chunks_per_session=None)
    assert u["evicted_sessions"] == ["sB"], u
    assert u["deleted_sessions"] == [], (
        "the fixture must evict WITHOUT deleting, or it cannot discriminate")
    assert u["net"] >= 0, u

    # ... and the guard ADMITS it, because no session leaves the window.
    trace: dict = {}
    g_order, g_picks = tl.temporal_leg_fusion_order(
        pool, guard_trace=trace, **geometry)
    assert g_picks == u_picks, (
        "the guard refused a permitted trade: reading evicted_sessions instead "
        f"of deleted_sessions — {trace['rejected']}")
    assert trace["rejected"] == [] and trace["accepted"] == u_picks
    g = tl.window_arm_retention(pool, g_order, window=6,
                                max_chunks_per_session=None)
    assert g["deleted_sessions"] == [] and g["net"] >= 0


# ── 14. the refusal reason names EVERY session a pick would have deleted ───

def test_the_reason_names_EVERY_deleted_session_not_just_the_first(monkeypatch):
    """#10538 (cycle 13, finding 2): ``",".join(deleted_sessions)`` →
    ``deleted_sessions[0]`` survived every suite, because every existing case
    has exactly ONE deleted session per trial.

    No randomised pool reached two (a trial adds one pick, so the shipped
    geometry rarely frees two sessions at once), so this pins the JOIN directly:
    the retention verdict is stubbed to report two deleted sessions and the
    emitted reason is asserted in full. That is the field the receipt calls its
    evidence of WHICH session would have been lost — under-reporting it makes an
    operator read a two-session risk as a one-session risk.
    """
    real = tl.window_arm_retention

    def _fake(*args, **kwargs):
        return {"deleted_sessions": ["sB", "sC"], "evicted_sessions": ["sB", "sC"],
                "net": 0, "pick_ranks": [], "additive": False}

    # one deep candidate in the window range, so condition (3) is what fires
    pool = (
        [{"id": f"a{i}", "session_id": "sA", "session_date": "2023-04-01",
          "content": "alpha"} for i in range(6)]
        + [{"id": "x0", "session_id": "sX", "session_date": "2023-04-02",
            "content": "alpha"}]
    )
    trace: dict = {}
    try:
        monkeypatch.setattr(tl, "window_arm_retention", _fake)
        tl.temporal_leg_fusion_order(
            pool, anchors=(), window=6, limit=1,
            date_window=("2023-04-01", "2023-04-30"), guard_trace=trace,
            max_chunks_per_session=None)
    finally:
        monkeypatch.setattr(tl, "window_arm_retention", real)

    reasons = [r["reason"] for r in trace["rejected"]]
    assert reasons, trace
    assert all(r == "evicts:sB,sC" for r in reasons), (
        f"the reason must name EVERY deleted session: {reasons}")
