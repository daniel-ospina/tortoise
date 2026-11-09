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
representative. They do **not** pin a placement verdict — ``tail`` vs ``head``
is the still-open question on #10538, and a test that decided it would be a
decision recorded in the wrong place. ⚠️ Non-eviction itself is NOT open: the
owner ruled ``window-exit`` on 2026-11-07 and the guard ships ON (it has its own
file, ``test_temporal_leg_non_eviction_10538.py``); this file pins the
PRE-RULING splice that the guard is measured against.
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


def _fused(pool, *, window, limit=DEFAULT_TEMPORAL_LEG_LIMIT, placement="tail",
           non_eviction=False):
    """The fused caller over this file's pools — the PRE-RULING splice by default.

    ⛔ ``non_eviction=False`` is EXPLICIT and deliberate (#10538, owner ruling
    2026-11-07). Every pin in this file measures the **retention instrument**
    on the splice the committed receipt was built from, and the guard default
    makes that splice unreachable on these pools — it refuses the picks, so
    ``pick_ranks``/``evicted_ids``/``graded_by_recall_all_at_5`` would all
    collapse to their inert values and the instrument would stop being
    measured. Passing the switch here keeps the SUBJECT of this file (what a
    pick does to the window) under test; the guard itself, and each of its
    three conditions, is pinned in
    ``tests/test_temporal_leg_non_eviction_10538.py`` — including a case that
    runs one of these pools through BOTH arms and asserts the guarded arm is
    the additive one.
    """
    order, picks = temporal_leg_fusion_order(
        pool, anchors=(), window=window, limit=limit,
        date_window=baseline.DATE_WINDOW, placement=placement,
        non_eviction=non_eviction)
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
    # ⛔ SHAPE-KEYED, NOT ONE BOOLEAN (fresh-context review, finding 1). The
    # pre-fix field read `False` as though it were a property of the arm; it was
    # false only on the chunk-free pools the instrument happened to build, and
    # the reader's `dedup_pool` cap lifts the same picks into the graded top 5 on
    # the chunk-bearing shape the corpus is actually ingested as.
    reachable = verdict["unit_reachable_without_displacement_at_window_12"]
    assert reachable["chunk_free_pools"] is False, reachable
    assert reachable["chunk_bearing_pools"] is True, reachable
    # ⛔ THE AGGREGATE MUST SAY WHICH ROWS COULD SPEAK (fresh-context review,
    # finding 3). `additive`/`evicted_ids` are entailed to be True/empty when the
    # reader's capped pool is no deeper than the window (it holds the WHOLE
    # pool), so a shape whose only
    # rows are that shallow supports no displacement claim either way. The deep
    # pool is the one that fills the window after the cap, so it is the one the
    # chunk-bearing half may be read from.
    assert reachable["chunk_bearing_pools_displacement_expressible"] == {
        "chunk_bearing_issue_probe": False,
        "chunk_bearing_whole_window_cluster": False,
        "chunk_bearing_deep_window": True,
    }, ("a displacement claim must name the rows where displacement is "
        f"expressible at all: {reachable}")
    assert verdict["unit_graded_by_recall_all_at_5_at_window_12"] == {
        "chunk_free_pools": 0,
        "chunk_bearing_pools": {"chunk_bearing_issue_probe": 2,
                               "chunk_bearing_whole_window_cluster": 2,
                               "chunk_bearing_deep_window": 2},
    }
    assert "AT THE SHIPPED GEOMETRY ONLY" in verdict["scope"], (
        "the reachability claim must be scoped to window=12 — the receipt's own "
        "sweep reaches the graded top 5 at windows 3-6, so an unscoped "
        "\"unreachable\" would be contradicted by its own rows")
    assert "windows 3-6 ON A CHUNK-FREE POOL" in verdict["scope"], (
        "the sweep's 3-6 boundary is a CHUNK-FREE property — on a chunk-bearing "
        "pool the cap grades the pick at every swept window through 13, so an "
        "unqualified statement is the one-shape-as-a-property error this "
        "verdict's other fields exist to prevent")
    assert "CHUNK-FREE" in verdict["scope"] and "CHUNK-BEARING" in verdict["scope"], (
        "the scope must name BOTH pool shapes: the two do not agree, and the "
        "cap is what makes them differ")
    why = verdict["why"]
    assert "never been measured on the shipped" in why, (
        "the head counterfactual has NO direct shipped-corpus measurement")
    # The #8713 citation must carry the source's own THREE-WAY split, because the
    # still-open PLACEMENT verdict is argued from this receipt:
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
    """The measured fact the non-eviction ruling RESTED on: at ``window=12`` the
    arm widens by 2 and drops 2, and the two it drops are the SOLE visible
    representatives of their sessions. Nothing in the repo pinned this before.

    ⚠️ "RESTED on", not "rests on" (review cycle 6): the owner ruled on this
    fact on 2026-11-07 and the guard now refuses exactly these picks — the
    question this test's premise leaves open is PLACEMENT, not eviction (see the
    module docstring). A premise that still calls eviction undecided is how a
    later lane argues the guard's refusal is the bug to remove."""
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


def test_the_graded_rank_depends_on_the_pools_chunk_shape():
    """⛔ FRESH-CONTEXT REVIEW, FINDING 1 — the pin for it.

    ``recall_all@5`` grades ``hits[:5]``, and ``hits`` is the **deduped** pool:
    ``retrieve.py`` reassigns ``annotated`` to the fused order and THEN applies
    ``_dedup_pool(annotated, max_chunks_per_session=3)``. ``dedup_pool`` caps RAW
    ``session-transcript`` chunks per session in rank order and never caps
    points/turn points, so:

    * CHUNK-FREE pool — the cap is a no-op, a tail pick spliced at ceiling 10
      stays at reader rank 10, and the metric sees NONE of them;
    * CHUNK-BEARING pool — the cap removes the raw chunks ABOVE the pick, both
      picks land at reader ranks 3-4, and the metric grades BOTH.

    The pre-fix instrument sliced the PRE-dedup order and published ``0`` for
    both shapes, so this pin is RED before the fix: on the chunk-bearing pools
    it reads ``[10, 11]``/``graded 0`` instead of ``[3, 4]``/``graded 2``.
    """
    receipt = json.loads(baseline.RECEIPT_PATH.read_text(encoding="utf-8"))
    chunk_free, chunk_bearing = {}, {}
    for name, rows in receipt["by_pool"].items():
        row = next(r for r in rows
                   if r["window"] == SHIPPED_WINDOW and r["placement"] == "tail")
        bucket = chunk_bearing if row["chunk_bearing"] else chunk_free
        bucket[name] = row
    assert chunk_free and chunk_bearing, (
        "the receipt must carry BOTH shapes, or the two readings cannot be told "
        "apart")
    for name, row in chunk_free.items():
        assert row["pick_ranks"] == row["pick_ranks_before_dedup"], name
        assert all(rank >= 5 for rank in row["pick_ranks"]), (
            f"{name}: a pick reached the graded top 5 on a CHUNK-FREE pool: "
            f"{row['pick_ranks']}")
        assert row["graded_by_recall_all_at_5"] == 0, name
    for name, row in chunk_bearing.items():
        assert row["fired"], f"{name}: the arm must fire on this shape"
        assert row["pick_ranks_before_dedup"] == [10, 11], (
            f"{name}: the splice put the picks at {row['pick_ranks_before_dedup']} "
            "— if the SPLICE moved, the cap is no longer the cause of what "
            "follows")
        assert row["pick_ranks"] == [3, 4], (
            f"{name}: the reader's chunk cap must lift the picks into the "
            f"graded top 5; got {row['pick_ranks']}")
        assert row["graded_by_recall_all_at_5"] == 2, name
        assert row["reader_pool_depth"] < row["pool_depth"], (
            f"{name}: the cap must actually bite on a chunk-bearing pool")


def test_the_chunk_cap_is_the_CAUSE_of_the_graded_rank_and_not_the_splice():
    """Non-vacuity for the test above: the SAME pool and the SAME fused order
    give the old, pre-fix answer when the cap is switched off.

    Without this, a change to the splice that happened to move the picks would
    silently take the credit for the cap's work.
    """
    pool = baseline.pool_for("chunk_bearing_issue_probe")
    order, picks = _fused(pool, window=SHIPPED_WINDOW)
    reader = window_arm_retention(pool, order, picks=picks,
                                  window=SHIPPED_WINDOW)
    splice = window_arm_retention(pool, order, picks=picks,
                                  window=SHIPPED_WINDOW,
                                  max_chunks_per_session=None)
    # the splice is IDENTICAL in both readings — only the reader's pool differs
    assert reader["pick_ranks_before_dedup"] == splice["pick_ranks"] == [10, 11]
    assert splice["graded_by_recall_all_at_5"] == 0, (
        "with the cap off, the pre-fix reading must reproduce exactly")
    assert reader["graded_by_recall_all_at_5"] == 2
    assert splice["reader_pool_depth"] == len(pool), (
        "cap off means the reader's pool IS the candidate pool")
    assert reader["reader_pool_depth"] < len(pool)


def test_a_pick_the_reader_cap_removes_is_reported_not_read_as_inert():
    """A promotion can pay for a slot the reader NEVER RECEIVES: if the pick is
    itself a raw chunk of a session that already holds the cap's worth of raw
    chunks above it, ``dedup_pool`` drops it. That must read as its own fault,
    not as ``picks_absent_from_fused`` (a caller bug) and not as an inert arm.

    Reachable through the shipped caller via the bucket/date divergence the
    module documents: ``held_buckets`` is keyed on ``(session_date,
    session_id)``, so a session already visible under one raw date can still be
    promoted from another — while the reader's cap is keyed on the session.
    """
    from tortoise.retrieval import SESSION_TRANSCRIPT_KIND

    raw = SESSION_TRANSCRIPT_KIND
    # window=6, limit=2 -> ceiling 4, so the head holds sB's first three raw
    # chunks and the promotion reaches sB's FOURTH (a different date bucket, so
    # `held_buckets` lets it through) — which the reader's cap then removes.
    pool = [
        {"id": "sa0", "session_id": "sA", "session_date": "2023-04-15",
         "content": "alpha", "point_kind": raw},
        {"id": "sb1", "session_id": "sB", "session_date": "2023-04-15",
         "content": "alpha", "point_kind": raw},
        {"id": "sb2", "session_id": "sB", "session_date": "2023-04-15",
         "content": "alpha", "point_kind": raw},
        {"id": "sb3", "session_id": "sB", "session_date": "2023-04-15",
         "content": "alpha", "point_kind": raw},
        {"id": "sc4", "session_id": "sC", "session_date": "2023-04-15",
         "content": "alpha"},
        {"id": "sd5", "session_id": "sD", "session_date": "2023-04-15",
         "content": "alpha"},
        {"id": "sb6", "session_id": "sB", "session_date": "2023-04-16",
         "content": "alpha", "point_kind": raw},
    ]
    order, picks = _fused(pool, window=6)
    assert picks == ["sb6"], (
        f"the shipped caller must actually promote the capped pick; got {picks}")
    got = window_arm_retention(pool, order, picks=picks, window=6)
    assert got["picks_absent_from_fused"] == [], (
        "the pick IS in the caller's order — reporting it as lost would blame "
        "the caller")
    assert got["picks_dropped_by_reader_dedup"] == ["sb6"], (
        "a pick the reader's cap removed must be reported on its own field")
    assert got["pick_ranks"] == [] and got["graded_by_recall_all_at_5"] == 0
    # ...and it is NOT inert: the pick set is non-empty, which is the property
    # an emptied `pick_ranks` alone cannot carry.
    no_cap = window_arm_retention(pool, order, picks=picks, window=6,
                                  max_chunks_per_session=None)
    assert no_cap["picks_dropped_by_reader_dedup"] == [], (
        "with the cap off nothing is dropped — the cap is the cause")
    assert no_cap["pick_ranks"] == [4]
    assert no_cap["graded_by_recall_all_at_5"] == 1, (
        "the pre-fix reading graded a pick the reader never receives — which is "
        "the whole finding in one line")


def test_the_pick_rank_is_the_ceiling_at_every_geometry():
    """The law, not a number: a pick's fused index IS ``ceiling`` (and
    ``ceiling + i`` for the i-th) because the tail splice is
    ``[head] + [picks]``. A change here is a change to the placement contract.

    Measured on ``pick_ranks_before_dedup`` — ``ceiling`` is the SPLICE
    geometry, so comparing it to the reader's post-cap rank would only hold on a
    chunk-free pool (this one is, which is why both readings agree here; the
    chunk-bearing divergence is pinned separately).
    """
    pool = _pool([("sA", 10), ("sB", 1), ("sC", 1), ("sD", 1), ("sE", 1),
                  ("sF", 1)])
    for window in baseline.GEOMETRY:
        order, picks = _fused(pool, window=window)
        got = window_arm_retention(pool, order, picks=picks[:1] if picks else [],
                                   window=window)
        ranks = [order.index(p) for p in picks]
        assert got["pick_ranks_before_dedup"] == ranks[:1]
        assert got["pick_ranks"] == got["pick_ranks_before_dedup"]
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


def test_the_sweep_boundary_is_a_shape_property_not_a_sweep_property():
    """⛔ THE 3-6 / 7 BOUNDARY BELONGS TO THE CHUNK-FREE SHAPE ONLY
    (fresh-context review, finding 1).

    On a chunk-free pool fused rank and reader rank coincide, so a tail pick sits
    at fused rank ``ceiling``, is in the graded top 5 at windows 3-6, and cannot
    be from window 7 on. On a CHUNK-BEARING pool the reader's cap removes the raw
    chunks ABOVE the pick, so the pick is graded at every swept window through
    13. Publishing the boundary as a fact about the sweep — which the pre-fix
    ``GEOMETRY`` comment, the receipt's ``scope`` string and this file's own
    rationale all did — is the one-shape-as-a-property-of-the-arm error the
    shape-keyed verdict fields exist to prevent.
    """
    receipt = json.loads(baseline.RECEIPT_PATH.read_text(encoding="utf-8"))

    def tail_rows(chunk_bearing):
        return {name: [r for r in rows
                       if r["placement"] == "tail" and r["chunk_bearing"]
                       is chunk_bearing]
                for name, rows in receipt["by_pool"].items()
                if any(r["placement"] == "tail" and r["chunk_bearing"]
                       is chunk_bearing for r in rows)}

    chunk_free, chunk_bearing = tail_rows(False), tail_rows(True)
    assert chunk_free and chunk_bearing
    # the boundary the CHUNK-FREE shape makes — on the pools it FIRES on (a
    # clustered-but-inert pool such as `single_session_pool` grades nothing at
    # ANY window, which is cause (c), not a boundary): graded up to 6, not from 7
    graded_free = {name: {r["window"]: r["graded_by_recall_all_at_5"]
                          for r in rows if r["fired"]}
                   for name, rows in chunk_free.items()}
    fired_free = {n: g for n, g in graded_free.items() if g}
    assert fired_free, "no chunk-free pool fires — the boundary is unmeasurable"
    for name, graded in fired_free.items():
        assert all(graded[w] >= 1 for w in (3, 4, 5, 6)), name
        # a pool that stops FIRING at 7+ reports no row at all from there, which
        # is the same "graded nothing" answer
        assert all(graded.get(w, 0) == 0 for w in (7, SHIPPED_WINDOW, 13, 14)), \
            name
    # …and the boundary it does NOT make: a chunk-bearing pool reaches the
    # graded top 5 at 7 and at the shipped 12, which the sweep comment denied.
    for name, rows in chunk_bearing.items():
        graded = {r["window"]: r["graded_by_recall_all_at_5"]
                  for r in rows if r["fired"]}
        assert graded, name
        assert all(graded[w] >= 1 for w in (3, 4, 5, 6, 7, SHIPPED_WINDOW, 13)), (
            f"{name}: window 7 is where a CHUNK-FREE pool stops being graded — "
            f"a chunk-bearing pool is still graded there: {graded}")
    # the published scope must NAME the shape it is qualifying
    assert "windows 3-6 ON A CHUNK-FREE POOL" in receipt["verdict"]["scope"]
    assert "stays graded at EVERY swept window from 3 through 13" in (
        receipt["verdict"]["scope"]), (
        "the scope must carry the chunk-bearing range as well as the "
        "chunk-free boundary")
    # ⛔ AND IT MUST NOT OVER-ATTRIBUTE THE CAUSE TO THE CAP AT THE LOW WINDOWS
    # (review cycle 14, finding 2). At windows 3-6 the pick is graded on EITHER
    # shape because the ceiling is inside the top 5; the cap only decides whether
    # the SECOND pick is graded. The cap is what grades the pick from window 7
    # on. Attaching "because the cap removes the raw chunks above it" to the
    # whole 3-13 range is an over-attribution, and the receipt's own rows refute
    # it: at w=3 and w=4 `pick_ranks` and `pick_ranks_before_dedup` are IDENTICAL
    # on the chunk-bearing pools, exactly as on the chunk-free ones.
    free_probe = {r["window"]: r for r in chunk_free["issue_probe_4520"]}
    for window in (3, 4):
        assert (free_probe[window]["pick_ranks"]
                == free_probe[window]["pick_ranks_before_dedup"])
    for name, rows in chunk_bearing.items():
        low = {r["window"]: r for r in rows}
        for window in (3, 4):
            assert (low[window]["pick_ranks"]
                    == low[window]["pick_ranks_before_dedup"]), (
                f"{name} w={window}: the cap is NOT what grades the pick "
                "here — this range belongs to the ceiling, not to the cap")


def test_the_caps_reach_is_measured_at_each_window_not_inferred():
    """⛔ THE CAP'S WINDOW THRESHOLDS, ROW BY ROW (review cycle 15).

    An earlier draft said "the cap only bites past the metric's cut (window >= 7)",
    which the receipt refutes at windows 5 and 6 — and it contradicted the
    ``GEOMETRY`` comment two screens away, which said the cap decides whether the
    SECOND pick is graded. Both were prose; this pin makes the thresholds read
    from the rows so neither can drift back:

    * w=3,4 — the cap changes nothing (pre-cap == post-cap ranks);
    * w=5   — it lifts the READER RANK (4 -> 3), graded count unchanged;
    * w=6   — it puts the SECOND pick inside the top 5 (4,5 -> 3,4; graded 1->2)
              while the chunk-free row stays [4,5] / graded 1;
    * w>=7  — it is the ONLY reason any pick is graded on a chunk-bearing pool.
    """
    receipt = json.loads(baseline.RECEIPT_PATH.read_text(encoding="utf-8"))
    bearing = {name: {r["window"]: r for r in rows
                      if r["placement"] == "tail"}
               for name, rows in receipt["by_pool"].items()
               if rows[0]["chunk_bearing"] and rows[0]["placement"] == "tail"}
    free = {r["window"]: r for r in receipt["by_pool"]["issue_probe_4520"]
            if r["placement"] == "tail"}
    assert bearing and free

    for name, rows in bearing.items():
        for window in (3, 4):
            assert (rows[window]["pick_ranks"]
                    == rows[window]["pick_ranks_before_dedup"]), (
                f"{name} w={window}: the cap moved a rank it cannot move — "
                "there is nothing above the pick to remove yet")
            assert (rows[window]["graded_by_recall_all_at_5"]
                    == free[window]["graded_by_recall_all_at_5"]), (
                f"w={window}: the two shapes must agree where the cap is inert")
        # w=5: the rank moves, the graded count does not
        before, after = rows[5]["pick_ranks_before_dedup"], rows[5]["pick_ranks"]
        assert after != before and after[0] < before[0], name
        assert rows[5]["graded_by_recall_all_at_5"] == 1, name
        # w=6: the cap is what grades the SECOND pick
        assert rows[6]["pick_ranks_before_dedup"] == [4, 5], name
        assert rows[6]["pick_ranks"] == [3, 4], name
        assert rows[6]["graded_by_recall_all_at_5"] == 2, name
        assert free[6]["graded_by_recall_all_at_5"] == 1, (
            "the contrast row: without the cap the second pick is NOT graded")
        # w>=7: the pre-cap ranks are past the cut, so the cap is the only reason
        # a pick can be graded — but HOW MANY land inside the top 5 is not
        # uniform, and the rows say so: both at 7 and at 12, one-or-both at 13,
        # and on the probe none at all at 14.
        for window in (7, SHIPPED_WINDOW, 13, 14):
            assert all(rank >= 5
                       for rank in rows[window]["pick_ranks_before_dedup"]), (
                f"{name} w={window}: a pick is graded PRE-cap here, so the cap "
                "is not what grades it")
            assert free[window].get("graded_by_recall_all_at_5", 0) == 0, window
        for window in (7, SHIPPED_WINDOW):
            assert rows[window]["pick_ranks"] == [3, 4], name
            assert rows[window]["graded_by_recall_all_at_5"] == 2, name
        # w=13 is pinned EXACTLY, per pool (review cycle 17, finding 1): one
        # pick on the probe ([11,12] -> [4,5], graded 1), both on the other two
        # ([11,12] -> [3,4], graded 2). A `>= 1` could not red on that clause.
        assert (rows[13]["pick_ranks_before_dedup"], rows[13]["pick_ranks"],
                rows[13]["graded_by_recall_all_at_5"]) == (
            ([11, 12], [4, 5], 1)
            if name == "chunk_bearing_issue_probe"
            else ([11, 12], [3, 4], 2)), name
        # the deep-window end, where the cap stops being sufficient on the probe
        assert {n: r[14]["graded_by_recall_all_at_5"]
                for n, r in bearing.items()} == {
            "chunk_bearing_issue_probe": 0,
            "chunk_bearing_whole_window_cluster": 1,
            "chunk_bearing_deep_window": 2,
        }


def test_displacement_fields_are_read_only_where_they_are_expressible():
    """⛔ A POOL NO DEEPER THAN THE WINDOW CANNOT REPORT A DISPLACEMENT AT ALL
    (fresh-context review, findings 3 and 17-2).

    ``evicted_ids`` is ``[i for i in base_ids if i not in fused_set]`` over two
    ``[:window]`` windows of the SAME pool. When the reader's capped pool is no
deeper than the window — SHALLOWER, or exactly the window's height — BOTH
windows hold the whole pool, so ``evicted_ids == []`` and ``additive is True``
are arithmetic, true whatever the arm does. (Equality is NOT a rare case: six
committed rows sit at ``reader_pool_depth == window`` — the ``depth=12`` shallow
row, ``partial_cluster_nothing_new`` at 13, the w=14 rows of three pools, and
``chunk_bearing_whole_window_cluster`` at w=6.) Every row now publishes
``displacement_expressible`` so a reader cannot take that vacuous True as a
measurement, and the verdict aggregate reads only the rows where it is true.

⚠️ This pin's own wording was the cycle-17 finding: it said "SHALLOWER", which
is refuted by those six rows — the predicate is ``>``, so equality also reads
``false``, and the entailment argument is the same at equality (both slices are
the whole pool).
    """
    receipt = json.loads(baseline.RECEIPT_PATH.read_text(encoding="utf-8"))
    rows = [r for rows in receipt["by_pool"].values() for r in rows]
    assert rows
    for row in rows:
        expressible = (row["reader_pool_depth"] > row["window"]
                       and row["reader_pool_depth_fused"] > row["window"])
        assert row["displacement_expressible"] is expressible, (
            f"window={row['window']}: the flag must "
            "be derived from the reader's own pool depth, not asserted")
        if not expressible:
            # the vacuity itself, pinned: nothing could have been displaced
            assert row["evicted_ids"] == [] and row["deleted_sessions"] == [], (
                "a non-expressible row must not be able to report a "
                "displacement — if it does, the expressibility rule is wrong")
    # the deep chunk-bearing pool is the one that DOES fill the window after the
    # cap, so it is the row the chunk-bearing half of the verdict is read from
    deep = next(r for r in rows
                if r["pool_depth"] > 20 and r["chunk_bearing"])
    assert deep["displacement_expressible"] is True
    assert deep["reader_pool_depth"] > SHIPPED_WINDOW
    # ⛔ EQUALITY IS NOT EXPRESSIBILITY (review cycle 17, finding 2): the flag is
    # `>`, so a row whose capped pool is EXACTLY the window's height holds the
    # whole pool and reports nothing about displacement either. If this pin ever
    # finds no such row, the prose's "no deeper than the window" is stale.
    equal_depth = [r for r in rows
                   if r["reader_pool_depth"] == r["window"]]
    assert equal_depth, (
        "no committed row sits at reader_pool_depth == window — the prose's "
        "'no deeper than the window (it holds the whole pool)' is then "
        "unmeasured")
    for row in equal_depth:
        assert row["displacement_expressible"] is False, row["window"]
        assert row["evicted_ids"] == [] and row["additive"] is True, (
            "at equality both [:window] slices are the whole pool, so the "
            "displacement fields must be the vacuous ones")


def test_the_most_favourable_pool_names_the_windows_it_is_additive_at():
    """⛔ OVER-RESTRICTING A CLAIM IS A FINDING TOO (review cycle 17, #3).

    The ``whole_window_cluster`` entry said the arm is additive here "at the
    SHIPPED window … ⚠️ Only at window 12", and that "deeper in the sweep" it
    starts deleting while citing the w=13 rows. The receipt says the +2 / no-
    deletion condition is true at windows 6, 7 AND 12, and that the deletion
    starts at 13 — so the caveat was pointing at the wrong window in both
    directions. Both halves are pinned from the rows.
    """
    receipt = json.loads(baseline.RECEIPT_PATH.read_text(encoding="utf-8"))
    rows = {r["window"]: r for r in receipt["by_pool"]["whole_window_cluster"]
            if r["placement"] == "tail"}
    additive = sorted(w for w, r in rows.items() if r["additive"])
    assert additive == [3, 4, 5, 6, 7, SHIPPED_WINDOW], additive
    assert [w for w, r in rows.items() if r["net"] == 2] == [6, 7, SHIPPED_WINDOW]
    assert rows[SHIPPED_WINDOW]["deleted_sessions"] == []
    assert rows[13]["deleted_sessions"] == ["sD"]
    assert rows[14]["deleted_sessions"] == ["sE"]


def test_the_head_counterfactual_is_measured_on_BOTH_shapes():
    """⛔ THE HEAD CLAIM MUST BE A MEASUREMENT ON EACH SHAPE IT NAMES
    (fresh-context review, finding 2).

    The pre-fix receipt published ONE head row and it was chunk-free, while
    ``verdict["why"]`` stated that head "reaches ranks 0-1 on either shape … so
    it too is a swap". Running the same counterfactual on the chunk-bearing
    shape refutes the second half: nothing is evicted there, because the chunk
    cap had already brought both picks inside the reader window. The two shapes
    now publish a head row beside their tail one.
    """
    receipt = json.loads(baseline.RECEIPT_PATH.read_text(encoding="utf-8"))
    shipped = receipt["shipped_geometry_chunk_bearing"]
    pools = {k.rsplit("_", 1)[0] for k in shipped}
    assert pools, shipped
    for pool in pools:
        assert f"{pool}_tail" in shipped and f"{pool}_head" in shipped, (
            f"{pool}: the counterfactual must be published per shape, not "
            "extrapolated from the chunk-free pool")
        head, tail = shipped[f"{pool}_head"], shipped[f"{pool}_tail"]
        assert head["pick_ranks"] == [0, 1], pool
        assert head["picks"] == tail["picks"], pool
        assert head["graded_by_recall_all_at_5"] == 2, pool
    # …and the one place head DOES pay by eviction is the chunk-free pool
    head_cf = next(r for r in receipt["shipped_geometry_by_placement"]
                   if r["placement"] == "head")
    assert head_cf["chunk_bearing"] is False
    assert head_cf["deleted_sessions"] == ["sB", "sC"]
    assert head_cf["evicted_sole_representatives"] == ["sb10", "sc11"]
    why = receipt["verdict"]["why"]
    assert "is a different answer on each shape" in why, (
        "the head sentence must be shape-keyed — one shape's outcome stated as "
        "the arm's is the finding this pin exists for")
    assert "evicts NOTHING" in why


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
    occupancy — otherwise the field the still-open placement verdict is argued
    from would answer "no harm, and it widened" for an arm that deleted a
    session. (Non-eviction is already ruled — see the module docstring.)
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
    # not left as an incidental property — `additive` is the field the still-open
    # PLACEMENT question (tail vs head) will be argued from. (Non-eviction is
    # already ruled; see the module docstring.)
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
        pool = baseline.pool_for(name)
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


def test_the_graded_count_follows_the_metrics_own_cutoff_not_the_window():
    """``graded_by_recall_all_at_5`` counts ``pick_ranks < 5`` of the DEDUPED
    pool and carries NO reader-window term: ``recall_all@5`` grades
    ``hits[:5]``, and ``hits`` is that pool UNTRUNCATED (``retrieve.py``:
    ``"hits": pool``, ``top = hits[:k]``). A pick the reader's WINDOW hides
    can still be inside the graded top 5, so the two questions are reported
    separately — this field grades, ``picks_outside_window`` says whether the
    reader can see the pick.

    Reachable only by a direct caller at these geometries (the shipped caller
    splices at ``ceiling = window - budget < window``), which the module
    supports — so both fields are gated on their own reading rather than
    assumed.
    """
    pool = _pool([("sA", 3), ("sB", 1)])
    order = ["sa0", "sa1", "sa2", "sb3"]
    got = window_arm_retention(pool, order, picks=["sb3"], window=3)
    assert got["pick_ranks"] == [3]
    assert got["picks_outside_window"] == ["sb3"], (
        "a pick past a 3-slot window is invisible to the reader")
    assert got["graded_by_recall_all_at_5"] == 1, (
        "rank 3 is inside the metric's hits[:5], so it IS graded — the count "
        "must follow the metric's own cutoff, not the measured window")
    # window=0: the reader sees nothing in the WINDOW, but the pool is still
    # the whole 4-slot deduped list, so a pick at pool rank 0 IS graded. The
    # visibility question is answered by ``picks_outside_window``, not here.
    zero = window_arm_retention(pool, order, picks=["sa0"], window=0)
    assert zero["fused_ids"] == []
    assert zero["pick_ranks"] == [0]
    assert zero["picks_outside_window"] == ["sa0"]
    assert zero["graded_by_recall_all_at_5"] == 1


def test_the_sole_representative_count_is_per_session_not_per_bucket():
    """The count is per ``session_id``, NOT per ``(session_date, session_id)``
    bucket (the key ``held_buckets`` uses), so a session split across two raw
    date strings has TWO base-window slots as far as this count is concerned and
    NEITHER of its evicted ids is a sole representative even though each
    ``(date, session)`` bucket held one.

    That makes the field report LESS than a bucket-keyed count would — on this
    pool 0 where a bucket-keyed count gives 2 — and it is the NOT-NECESSARY
    direction: a session is DELETED with no sole representative reported. It says
    nothing about the NOT-SUFFICIENT direction, which is enabled by
    ``held_buckets``' BUCKET keying (a promotion may re-add a session the reader
    already holds under a different raw date) and is flagged identically by both
    counts — see ``tortoise.temporal_leg.window_arm_retention``.

    The pre-registered pools in the receipt cannot see any of this: every hit in
    them carries ONE date, so a `(date, session)` bucket and a session are the
    same thing and both definitions agree.
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


def test_the_predicate_is_reported_but_the_guarded_arm_is_what_ships():
    """⛔ #10538 PREMISE RETIRED BY THE OWNER RULING (2026-11-07).

    This pin used to be ``test_the_predicate_is_reported_but_never_enforced``,
    asserting that ``additive`` is a REPORT the module would not turn into a
    pick filter *"without an owner ruling"*. The ruling arrived (``window-exit``
    — non-eviction a HARD CONDITION), so the predicate is now enforced on the
    shipped tail arm and this pin carries the NEW contract.

    What survives unchanged: ``window_arm_retention`` stays REPORT-ONLY (it
    never gates; its callers decide), it is deterministic, and it mutates
    nothing. What changed: the FUSED caller refuses the splice the predicate
    calls non-additive, so the two arms differ — and that difference is the
    guard's whole effect, asserted here as one comparison on one pool.
    """
    pool = _pool([("sA", 10), ("sB", 1), ("sC", 1), ("sD", 1), ("sE", 1),
                  ("sF", 1)])
    before = [(c["id"], c["session_id"]) for c in pool]
    order, picks = _fused(pool, window=SHIPPED_WINDOW)
    order_snapshot, picks_snapshot = list(order), list(picks)

    first = window_arm_retention(pool, order, picks=picks, window=SHIPPED_WINDOW)
    second = window_arm_retention(pool, order, picks=picks, window=SHIPPED_WINDOW)

    # the measurement is a pure REPORT: deterministic, non-mutating.
    assert first == second
    assert first["additive"] is False
    assert first["deleted_sessions"]
    assert [(c["id"], c["session_id"]) for c in pool] == before
    assert order == order_snapshot and picks == picks_snapshot
    # ...and the arm that produced it is unchanged by having been measured.
    again_order, again_picks = _fused(pool, window=SHIPPED_WINDOW)
    assert (again_order, again_picks) == (order_snapshot, picks_snapshot)

    # ...while the GUARDED arm on the SAME pool promotes nothing and is
    # additive — the ruling's effect, in one comparison.
    g_order, g_picks = _fused(pool, window=SHIPPED_WINDOW, non_eviction=True)
    guarded = window_arm_retention(pool, g_order, picks=g_picks,
                                   window=SHIPPED_WINDOW)
    assert g_picks == [], (
        "the guard must refuse every pick that trades a session away")
    assert guarded["additive"] is True
    assert guarded["deleted_sessions"] == []
    assert guarded["pick_ranks"] == [], (
        "a fully-refused arm reports no pick rank at all — never a stale rank "
        "for a pick that was not promoted")
    assert g_order == [c["id"] for c in pool], (
        "a fully-refused arm must leave the fused order byte-identical")
