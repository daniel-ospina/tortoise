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


def test_the_guard_reads_the_POST_cap_reader_window_not_the_splice_index():
    """The guard must judge the arm on what the READER receives.

    ``window_arm_retention`` defaults to the product chunk cap
    (``DEFAULT_MAX_CHUNKS_PER_SESSION``), because the harness fuses and THEN
    dedups: a pick spliced at fused rank 12 can be GRADED at rank 3 once the
    cap removes raw chunks above it. The guard calls the same measurement with
    the same default, so its verdict and the published retention numbers are
    computed on one surface. Pinned structurally: the filter's own call must
    not pass ``max_chunks_per_session=None`` (the pre-reader view).
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
        and node.func.id == "window_arm_retention"
    ]
    assert calls, "the guard stopped consulting the retention measurement"
    for call in calls:
        keywords = {kw.arg for kw in call.keywords}
        assert "max_chunks_per_session" not in keywords, (
            "the guard must use the READER's default chunk cap — overriding it "
            "would grade the pre-cap splice and disagree with the receipt")


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


def test_the_receipt_carries_the_guard_and_its_refusals():
    src = Path(r.__file__).read_text()
    stats = [
        node for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Dict)
        and any(isinstance(k, ast.Constant) and k.value == "non_eviction"
                for k in node.keys)
    ]
    assert stats, "temporal_leg_stats lost the non_eviction field (#10538)"
    keys = {k.value for node in stats for k in node.keys
            if isinstance(k, ast.Constant)}
    assert {"non_eviction", "non_eviction_applied",
            "non_eviction_rejected"} <= keys, keys


def test_the_leg_trace_passes_the_refusal_count_to_the_empty_reason():
    for keywords in _call_keywords("retrieve_for_question",
                                   "temporal_leg_empty_reason"):
        assert "guard_rejected" in keywords, (
            "an empty leg caused by the guard would be laundered as "
            "'no_window_candidates' (#10538)")


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
    assert "applied" not in inert_trace, inert_trace
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
