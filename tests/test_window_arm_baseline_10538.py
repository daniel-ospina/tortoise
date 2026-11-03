"""#10538 — the window arm's RETENTION baseline, pinned.

The instrument is ``tools/longmem_eval/window_arm_baseline.py`` and its committed
receipt is ``docs/scoping/receipts/2026-11-03-10538-window-arm-retention.json``.

WHY THIS FILE EXISTS. Before it, the window arm ``#10549`` shipped had **no
in-repo RETENTION measurement**: the unit tests pinned individual pool shapes and
never reported a net distinct-session delta, and
``tools/longmem_eval/temporal_leg_replay.py`` never exercises this arm at all
(all 55 pinned questions resolve to the ``ordering`` constraint class, so
``date_window`` is ``None`` on every one — recorded in
``tortoise/temporal_leg.py``'s module docstring and in the replay's own inline
comment on the resolution, not in the replay's docstring). The end-to-end
surface that WOULD measure it (``temporal_leg_stats``) reports order, not
retention, and reaching it means an eval run the 2026-11-02 owner ruling has
postponed. Every figure the arm was justified with lived in issue prose and in
``/tmp`` probes that die with the box.

These tests pin the LAW rather than a happy number: where a pick lands relative
to the graded top 5, whether the visible evidence-session SET widened or merely
changed, and whether a displaced slot was a session's sole visible
representative. They do **not** pin a placement verdict — ``window-exit`` vs
``ab-head-ab`` is an open owner ruling on #10538, and a test that decided it
would be a decision recorded in the wrong place.
"""
from __future__ import annotations

import json

from tools.longmem_eval import window_arm_baseline as baseline
from tortoise.temporal_leg import (
    DEFAULT_TEMPORAL_LEG_LIMIT,
    temporal_leg_fusion_order,
    window_arm_retention,
)

SHIPPED_WINDOW = baseline.SHIPPED_WINDOW


def _pool(spec):
    return baseline._pool(spec)


def _fused(pool, *, window, limit=DEFAULT_TEMPORAL_LEG_LIMIT, placement="tail"):
    order, picks = temporal_leg_fusion_order(
        pool, anchors=(), window=window, limit=limit,
        date_window=baseline.DATE_WINDOW, placement=placement)
    return order, picks


def test_the_committed_receipt_reproduces_from_the_instrument():
    """The receipt is a build artifact, not prose: a drift means a number a
    ruling was argued from has moved without anyone noticing."""
    assert baseline.RECEIPT_PATH.exists(), (
        f"missing committed receipt: {baseline.RECEIPT_PATH}")
    committed = baseline.RECEIPT_PATH.read_text(encoding="utf-8")
    assert committed == baseline.render_receipt(), (
        "receipt drift — re-run `uv run python -m "
        "tools.longmem_eval.window_arm_baseline` and review the diff")


def test_the_receipt_disclaims_the_measurement_it_is_not():
    """Guards the one confusion that would matter: this instrument must never be
    reported as the #9097 Q3 exit measurement or as a placement verdict."""
    receipt = json.loads(baseline.RECEIPT_PATH.read_text(encoding="utf-8"))
    assert "NOT the #9097 Q3 exit measurement" in receipt["what_this_is_not"]
    assert "NOT a placement verdict" in receipt["what_this_is_not"]
    verdict = receipt["verdict"]
    assert verdict["unit_reachable_without_displacement_at_window_12"] is False
    assert "AT THE SHIPPED GEOMETRY ONLY" in verdict["scope"], (
        "the reachability claim must be scoped to window=12 — the receipt's own "
        "sweep reaches the graded top 5 at windows 3-6, so an unscoped "
        "\"unreachable\" would be contradicted by its own rows")
    why = verdict["why"]
    assert "never been measured on the shipped" in why, (
        "the head counterfactual has NO direct shipped-corpus measurement")
    # The #8713 citation must carry the source's own THREE-WAY split, because a
    # pending ruling is argued from this receipt:
    #   (a) the injection's own eviction -> reader_surface@5 0.9436 -> 0.9289,
    #       and `inj_only` (guard ablated) reads the SAME 0.9289, so the guard
    #       accounts for none of it;
    #   (b) the session-diverse guard -> evidence_recall@5 0.527 -> 0.4706;
    #   (c) the closure is the guard's -> recall_all@5 0.6761 -> 0.8592, a NET
    #       GAIN, with `inj_only` identical to `off` on every RECALL metric
    #       (its reader_surface@5 differs — that is (a)'s injection loss).
    for fragment in ("reader_surface@5 0.9436 -> 0.9289",
                     "same 0.9289",
                     "evidence_recall@5 0.527 -> 0.4706",
                     "recall_all@5 0.6761 -> 0.8592",
                     "on every RECALL metric",
                     "the closure is the guard's alone"):
        assert fragment in why, (
            f"the #8713 citation lost part of the source's split ({fragment!r}) "
            f"— a one-sided harm claim, or an attribution the source refutes")


def test_the_retention_recount_agrees_with_a_hand_count():
    """``window_arm_retention`` is the source of truth for the receipt, so it is
    re-derived here by hand on the issue's own probe pool (comment 6574179020)
    rather than trusted."""
    pool = _pool([("sA", 10), ("sB", 1), ("sC", 1), ("sD", 1), ("sE", 1),
                  ("sF", 1)])
    order, picks = _fused(pool, window=12)
    got = window_arm_retention(pool, order, picks=picks, window=12)

    session_of = {c["id"]: c["session_id"] for c in pool}
    base_window = [c["id"] for c in pool][:12]
    fused_window = order[:12]
    assert got["base_ids"] == base_window
    assert got["fused_ids"] == fused_window
    assert got["base_sessions"] == len({session_of[i] for i in base_window}) == 3
    assert got["fused_sessions"] == len({session_of[i] for i in fused_window})
    assert got["net"] == got["fused_sessions"] - got["base_sessions"]
    assert got["evicted_ids"] == [i for i in base_window
                                  if i not in set(fused_window)]
    assert got["pick_ranks"] == [order.index(p) for p in picks]


def test_the_shipped_geometry_is_net_zero_and_loses_sole_representatives():
    """The measured fact the whole open ruling rests on: at ``window=12`` the arm
    widens by 2 and drops 2, and the two it drops are the SOLE visible
    representatives of their sessions. Nothing in the repo pinned this before."""
    pool = _pool([("sA", 10), ("sB", 1), ("sC", 1), ("sD", 1), ("sE", 1),
                  ("sF", 1)])
    order, picks = _fused(pool, window=SHIPPED_WINDOW)
    got = window_arm_retention(pool, order, picks=picks, window=SHIPPED_WINDOW)

    assert picks, "the arm must be the FIRING case, not an inert one"
    assert got["base_sessions"] == 3 and got["fused_sessions"] == 3
    assert got["net"] == 0, "the arm reported a widening it did not deliver"
    assert got["evicted_sessions"] == ["sB", "sC"]
    assert len(got["evicted_sole_representatives"]) == 2
    assert got["additive"] is False, (
        "the additive predicate must reject an arm that trades a session away")


def test_every_pick_at_the_shipped_geometry_lands_below_the_graded_top_five():
    """``recall_all@5`` grades ``hits[:5]``; a tail pick starts at
    ``ceiling = window - budget``. So the arm's own unit cannot see its own picks
    at the shipped window — on ANY of the pre-registered pools."""
    receipt = json.loads(baseline.RECEIPT_PATH.read_text(encoding="utf-8"))
    for name, rows in receipt["by_pool"].items():
        row = next(r for r in rows
                   if r["window"] == SHIPPED_WINDOW and r["placement"] == "tail")
        assert all(rank >= 5 for rank in row["pick_ranks"]), (
            f"{name}: a pick reached the graded top 5 at window=12: "
            f"{row['pick_ranks']}")
        assert row["graded_by_recall_all_at_5"] == 0, name


def test_the_pick_rank_is_the_ceiling_at_every_geometry():
    """The law, not a number: a pick's fused index IS ``ceiling`` (and
    ``ceiling + i`` for the i-th) because the tail splice is
    ``[head] + [picks]``. A change here is a change to the placement contract."""
    pool = _pool([("sA", 10), ("sB", 1), ("sC", 1), ("sD", 1), ("sE", 1),
                  ("sF", 1)])
    for window in baseline.GEOMETRY:
        order, picks = _fused(pool, window=window)
        got = window_arm_retention(pool, order, picks=picks[:1] if picks else [],
                                   window=window)
        ranks = [order.index(p) for p in picks]
        assert ranks == [got["ceiling"] + i for i in range(len(picks))], (
            f"window={window}: picks {picks} at {ranks}, ceiling "
            f"{got['ceiling']}")


def test_head_placement_reaches_the_graded_top_five_only_by_displacement():
    """The counterfactual, measured HERE: ``placement='head'`` puts the picks at
    ranks 0-1 on this pool — i.e. it reaches the unit by taking the semantic
    head's slots, so it is a swap and not a widening (``additive`` is False).

    ⚠️ This is NOT a claim about answer quality on the shipped corpus, which has
    never been measured for either placement. The nearest measured analogue is
    #8713's C4 source-session RE-INJECTION arm — a different mechanism, and not
    a pure loss: its capsweep receipt splits it into the session-diverse GUARD
    (a session capped at 2 inside the window) costing `evidence_recall@5`
    0.527 → 0.4706 while the SAME arm NET GAINED at the graded metric
    (`recall_all@5` 0.6761 → 0.8592). The only direct head reading is the
    TF-IDF replay in `tortoise/temporal_leg.py`'s docstring, which scores head
    equal-or-better (0.9506 vs 0.9228 recall@12) on a near-ceiling base that
    cannot represent the shipped failure. Reported as a fact, never as a
    recommendation."""
    pool = _pool([("sA", 10), ("sB", 1), ("sC", 1), ("sD", 1), ("sE", 1),
                  ("sF", 1)])
    tail_order, tail_picks = _fused(pool, window=SHIPPED_WINDOW)
    head_order, head_picks = _fused(pool, window=SHIPPED_WINDOW,
                                   placement="head")
    head = window_arm_retention(pool, head_order, picks=head_picks,
                                window=SHIPPED_WINDOW)

    assert head_picks == tail_picks, "the two arms must choose the same ids"
    assert head["pick_ranks"] == [0, 1]
    assert tail_order != head_order, (
        "the two placements must produce different fused orders, or this pin "
        "is vacuous")
    assert head["graded_by_recall_all_at_5"] == 2
    assert head["net"] == 0 and head["additive"] is False, (
        "the head arm reaches the unit by eviction, not by widening")


def test_the_arm_is_inert_when_nothing_sits_below_the_window():
    """An inert arm is not a win. At the shipped window a pool no deeper than the
    window has nothing to promote, so ``fired == 0`` must read as "no
    opportunity", never as "no harm"."""
    for depth in baseline.SHALLOW_DEPTHS:
        pool = baseline._shallow_pool(depth)
        order, picks = _fused(pool, window=SHIPPED_WINDOW)
        if depth <= SHIPPED_WINDOW:
            assert picks == [], f"depth={depth} promoted with nothing below it"
            assert order == [c["id"] for c in pool]
        else:
            # ...and one slot deeper it fires, which is what makes the case
            # above non-vacuous.
            assert picks, f"depth={depth} should promote the one deep session"


def test_a_two_slot_session_that_loses_both_slots_is_a_deletion():
    """Fresh-context review, cycle 1 (P1): the SOLE-REPRESENTATIVE predicate is
    NEITHER sufficient NOR necessary for a deletion (cycle 6 added the
    not-sufficient half: a sole representative can be displaced while its session
    stays visible through a DIFFERENT bucket, because two raw date strings for
    one session are two buckets). A session holding two of the window's tail
    slots loses both at the shipped geometry, so it leaves the window while
    ``evicted_sole_representatives`` is empty and ``net`` reports a WIDENING.

    ``additive`` is therefore defined on session DELETION, not on single-slot
    occupancy — otherwise the field the pending owner ruling will be argued from
    would answer "no harm, and it widened" for an arm that deleted a session.
    """
    pool = _pool([("sA", 10), ("sB", 2), ("sD", 1), ("sE", 1)])
    order, picks = _fused(pool, window=SHIPPED_WINDOW)
    got = window_arm_retention(pool, order, picks=picks, window=SHIPPED_WINDOW)

    assert got["evicted_sole_representatives"] == [], (
        "the fixture must be the case the sole-representative test misses")
    assert got["deleted_sessions"] == ["sB"], got["deleted_sessions"]
    assert got["additive"] is False, (
        "a session left the reader window and the predicate called it additive")
    assert got["net"] > 0, (
        "this fixture's whole point is that net even reports a widening")


def test_a_hit_with_no_session_is_not_a_session():
    """The fail-closed rule ``_session_clustered`` already applies: collapsing
    every absent id into one bucket would let 'the window dropped a session' fire
    on hits that belong to no session at all."""
    pool = [
        {"id": "x0", "session_date": "2023-04-15", "content": "alpha"},
        {"id": "x1", "session_id": "sA", "session_date": "2023-04-15",
         "content": "alpha"},
        {"id": "x2", "session_id": "sA", "session_date": "2023-04-15",
         "content": "alpha"},
    ]
    # the sessionless hit is displaced and a real session's slots are not
    got = window_arm_retention(pool, ["x1", "x2"], window=2)
    assert got["base_sessionless_slots"] == 1
    assert got["fused_sessionless_slots"] == 0
    assert got["evicted_sessions"] == [] and got["deleted_sessions"] == []
    # ⚠️ `additive` is SESSION-level, so it says NOTHING about displaced
    # non-sessions: a displaced sessionless base slot leaves it True and shows
    # up only here and in the sessionless pair. Pinned as a stated limitation,
    # not left as an incidental property — `additive` is the field the pending
    # window-exit / ab-head-ab ruling will be argued from.
    assert got["additive"] is True
    assert got["evicted_ids"] == ["x0"], (
        "the displaced sessionless hit must still be reported")
    assert got["base_sessions"] == got["fused_sessions"] == 1
    # ...and a sessionless hit CAN be promoted (it carries a session_date, so it
    # forms its own (date, "") bucket): the two counts are reported PER WINDOW,
    # so a promoted one is not silently dropped from the report.
    promoted = window_arm_retention(pool, ["x0", "x1"], window=2)
    assert promoted["base_sessionless_slots"] == 1
    assert promoted["fused_sessionless_slots"] == 1
    assert promoted["base_sessions"] == promoted["fused_sessions"] == 1
    # ...and a window made ENTIRELY of sessionless hits reports zero sessions,
    # not one phantom session sharing the "" key.
    all_bare = [{"id": f"y{i}", "session_date": "2023-04-15",
                 "content": "alpha"} for i in range(3)]
    got_bare = window_arm_retention(all_bare, [c["id"] for c in all_bare],
                                    window=3)
    assert got_bare["base_sessions"] == 0 and got_bare["fused_sessions"] == 0
    assert got_bare["base_sessionless_slots"] == 3


def test_every_pre_registered_pool_declares_its_real_gate_state():
    """``POOLS[*]["clustered"]`` is metadata the receipt publishes, so it is made
    EXECUTABLE rather than left as a comment: at every swept window the arm's own
    gate must give the declared answer, or the table is describing a pool nobody
    is measuring.

    Note a clustered pool can still be INERT — ``single_session_pool`` is,
    because every bucket it could promote is one the reader already holds (cause
    (c) of ``no_window_candidates``) — so this pins the GATE, never the outcome.
    """
    from tortoise.temporal_leg import _session_clustered

    for name, spec in baseline.POOLS.items():
        pool = _pool(spec["spec"])
        for window in baseline.GEOMETRY:
            head = max(window - DEFAULT_TEMPORAL_LEG_LIMIT, 0)
            got = _session_clustered(pool, head, DEFAULT_TEMPORAL_LEG_LIMIT,
                                     window)
            assert got is spec["clustered"], (
                f"{name} declares clustered={spec['clustered']} but the gate "
                f"says {got} at window={window}")


def test_a_pick_that_never_reaches_the_fused_order_is_reported_not_dropped():
    """A lost pick must not read as an inert arm: an emptied ``pick_ranks`` is
    otherwise indistinguishable from "nothing was promoted"."""
    pool = _pool([("sA", 10), ("sB", 1), ("sC", 1)])
    order = [c["id"] for c in pool]
    got = window_arm_retention(pool, order, picks=["sb10", "ghost99"],
                               window=12)
    assert got["pick_ranks"] == [order.index("sb10")]
    assert got["picks_absent_from_fused"] == ["ghost99"]


def test_a_pick_outside_the_reader_window_is_not_counted_as_graded():
    """The metric grades the READER's top 5 (``hits[:5]``), so a pick beyond the
    window is not graded however high it ranks in the caller's order.

    Reachable only by a direct caller at these geometries (the shipped caller
    splices at ``ceiling = window - budget < window``), which the module
    supports — so the field is gated on visibility rather than assumed.
    """
    pool = _pool([("sA", 3), ("sB", 1)])
    order = ["sa0", "sa1", "sa2", "sb3"]
    got = window_arm_retention(pool, order, picks=["sb3"], window=3)
    assert got["pick_ranks"] == [3]
    assert got["picks_outside_window"] == ["sb3"]
    assert got["graded_by_recall_all_at_5"] == 0, (
        "a pick outside a 3-slot window was reported as graded")
    # window=0: the reader sees nothing, so nothing can be graded — even a pick
    # that sits at fused index 0.
    zero = window_arm_retention(pool, order, picks=["sa0"], window=0)
    assert zero["fused_ids"] == []
    assert zero["pick_ranks"] == [0]
    assert zero["picks_outside_window"] == ["sa0"]
    assert zero["graded_by_recall_all_at_5"] == 0


def test_the_sole_representative_count_is_per_session_not_per_bucket():
    """The count is per ``session_id``, NOT per ``(session_date, session_id)``
    bucket (the key ``held_buckets`` uses) — and that mismatch is exactly what
    makes the not-sufficient case reachable: a session split across two raw date
    strings has TWO base-window slots as far as this count is concerned, so
    neither of its evicted ids is a sole representative even though each
    ``(date, session)`` bucket held one.

    The pre-registered pools cannot see this: ``_pool()`` stamps one date on
    every hit, so both definitions agree on all six of them.
    """
    pool = [
        {"id": f"sa{i}", "session_id": "sA", "session_date": "2023-04-15",
         "content": "alpha"} for i in range(10)
    ] + [
        {"id": "sx10", "session_id": "sX", "session_date": "2023-04-15",
         "content": "alpha"},
        {"id": "sx11", "session_id": "sX", "session_date": "2023-04-16",
         "content": "alpha"},
        {"id": "sd12", "session_id": "sD", "session_date": "2023-04-18",
         "content": "alpha"},
        {"id": "se13", "session_id": "sE", "session_date": "2023-04-19",
         "content": "alpha"},
    ]
    order, picks = _fused(pool, window=SHIPPED_WINDOW)
    got = window_arm_retention(pool, order, picks=picks,
                               window=SHIPPED_WINDOW)
    assert set(got["evicted_ids"]) == {"sx10", "sx11"}, got["evicted_ids"]
    assert got["evicted_sole_representatives"] == [], (
        "the count became per-bucket — a session split across two dates has two "
        "slots by this definition")
    assert got["deleted_sessions"] == ["sX"] and got["additive"] is False


def test_the_unclustered_window_declines_at_every_geometry():
    """The control the whole arm is gated on: a window that already spans
    distinct sessions must not be widened into, or the arm would displace a
    correct rank-(k-1) gold on a question that was never starved."""
    pool = _pool([(f"s{i}", 1) for i in range(12)]
                 + [("sD", 1), ("sE", 1)])
    for window in baseline.GEOMETRY:
        order, picks = _fused(pool, window=window)
        assert picks == [], f"window={window}: fired on an unclustered window"
        assert order == [c["id"] for c in pool]


def test_the_predicate_is_reported_but_never_enforced():
    """``window_arm_retention`` is read-only instrumentation: computing it must
    not change a single pick, and it must not mutate its inputs. Whether the
    predicate may GATE a promotion is the open owner ruling on #10538 — a
    function that quietly enforced it would be answering that ruling in code."""
    pool = _pool([("sA", 10), ("sB", 1), ("sC", 1), ("sD", 1), ("sE", 1),
                  ("sF", 1)])
    before = [(c["id"], c["session_id"]) for c in pool]
    order, picks = _fused(pool, window=SHIPPED_WINDOW)
    order_snapshot, picks_snapshot = list(order), list(picks)

    first = window_arm_retention(pool, order, picks=picks, window=SHIPPED_WINDOW)
    second = window_arm_retention(pool, order, picks=picks, window=SHIPPED_WINDOW)

    assert first == second
    assert first["additive"] is False
    assert [(c["id"], c["session_id"]) for c in pool] == before
    assert order == order_snapshot and picks == picks_snapshot
    # ...and the arm that produced it is unchanged by having been measured.
    again_order, again_picks = _fused(pool, window=SHIPPED_WINDOW)
    assert (again_order, again_picks) == (order_snapshot, picks_snapshot)
