"""#10538 — the window arm's RETENTION baseline, measured hermetically.

WHY THIS FILE EXISTS
--------------------
#10538 asks for *"time-aware query expansion … derive the question's date range
and its related facts, and use them to widen the candidate set"*, and names its
grading unit: ``recall_all@5`` — ALL evidence sessions in the top 5 — on the
frozen write_path corpus.

The *mechanism* landed twice: ``#10549`` (``2f2582a27``) taught
``tortoise.temporal_leg`` to treat a question's DATE RANGE as a target when the
visible slots are session-clustered, and ``#10628`` (``7488a770e``) forwarded the
``placement`` knob so the eval-lane counterfactual became runnable. The *number*
never landed — and by the owner's ruling of 2026-11-02 it cannot right now:
**no eval runs until the open P0/P1 queue is empty** (measured 2026-11-03:
15 ``P0-blocker`` + 24 ``P1-severe`` open, so the gate is closed).

What that left behind is the failure mode this instrument exists to close: every
figure the arm was ever justified with lives in **issue prose** and in **``/tmp``
probe scripts** (``/tmp/probe_c4q2.py``, ``/tmp/probe_10538_geom.py``) — artifacts
that die with the box. Nothing in the repository measured the window arm's
**retention** — whether the set of evidence sessions the reader can see got wider
or merely changed:

* the unit tests pin individual pool shapes, and never report a net
  distinct-session delta;
* ``tools/longmem_eval/temporal_leg_replay.py`` does not exercise this arm at all
  — its 55 pinned questions all resolve to the ``ordering`` constraint class and
  ``facet_window_bounds`` has no computable bound for ``ordering``, so
  ``date_window`` is ``None`` on every one. That is recorded in
  ``tortoise/temporal_leg.py``'s module docstring (*"The replay therefore
  exercises the ANCHOR path only … Do not read these rows as a measurement of
  the window arm"*) and in the replay's own inline comment on the resolution;
* the end-to-end surface that WOULD measure it (``temporal_leg_stats`` —
  on/applied/kind/picks/dated/ceiling/leg, ``tools/longmem_eval/retrieve.py``,
  emitted by ``run.py``) reports ORDER, not retention, and reaching it means an
  eval run, which the 2026-11-02 owner ruling has postponed.

So the arm that #10538 shipped had **no retention measurement anywhere**. This
is that surface: deterministic, hermetic, free, and on the SHIPPED wiring.

WHAT IT MEASURES
----------------
``temporal_leg_fusion_order`` is a **post-cut splice** — it re-orders ids after
the reader's membership cut, so it can only swap items INSIDE a fixed window.
The only questions that can be asked of it are therefore window-level:

* did the set of evidence SESSIONS the reader can see get wider, or merely
  different (``net``)?
* did it DELETE a session from the reader window — i.e. was an evidence
  session that the reader could see before no longer visible after
  (``deleted_sessions``, summarised as ``additive``)? ``evicted_sole_representatives``
  is the narrower SLOT-level diagnostic, and it is NEITHER sufficient NOR
  necessary for a deletion: a session holding TWO slots disappears when both are
  displaced with no sole representative at all (measured, on the
  ``two_slot_tail_session`` pool below), and a sole representative can be
  displaced while its session stays visible through a DIFFERENT bucket (two hits
  of one session carrying different raw date strings are two buckets). Read
  ``deleted_sessions`` / ``additive`` for the verdict.

  ⛔ WHAT IS AND IS NOT DECIDED HERE. The owner RULED ``window-exit`` on
  2026-11-07 (#10538, comment ``6711158625``) and the guard that enforces it —
  ``_non_eviction_filter`` in ``tortoise/temporal_leg.py``, on by default — has
  shipped. So "may an arm pay for a slot by evicting base evidence?" is NOT an
  open question and this file does not report a report-only predicate: it asks
  for the PRE-RULING splice EXPLICITLY (``NON_EVICTION = False``) because its
  receipt's subject is the baseline the guard is measured AGAINST, and every row
  stamps which arm produced it (see ``_row``'s note). What remains open, and
  what this instrument deliberately does not decide, is the PLACEMENT verdict —
  ``tail`` vs ``head``. A stale "the ruling is undecided" line is how a bypass
  gets argued later.
* can the graded metric see the picks at all (``pick_ranks`` vs
  ``graded_by_recall_all_at_5``)?

⛔ **THE GRADED SURFACE IS THE READER'S POOL, WHICH IS THE *DEDUPED* ONE.**
``recall_all@5`` grades ``hits[:5]``, and ``hits`` is ``dedup_pool(annotated)``
applied AFTER fusion — so the fused index space the splice works in is not the
index space the metric grades. ``dedup_pool`` caps RAW ``session-transcript``
chunks per session and never caps points/turn points, so the distinction is
invisible on a chunk-free pool and decisive on the chunk-bearing shape the
corpus is actually ingested as: the cap removes slots ABOVE a tail pick and
lifts it into the graded top 5. Every pool therefore carries its chunk shape
(``raw``, reported per row as ``chunk_bearing``), and the verdict is keyed by
shape rather than stated once. A single number here would be a claim about ONE
shape dressed as a property of the arm — which is exactly what a fresh-context
review caught in the first draft.

⚠️ HOW FAR THE CAP REACHES, measured row by row rather than inferred from the
ceiling (review cycle 15: the earlier "the cap only bites past the metric's cut"
was false at windows 5 and 6). On the chunk-bearing pools, at ``limit=2``:

* ``window = 3, 4`` — the cap changes NOTHING: pre-cap and post-cap ranks are
  identical (``[2]`` and ``[3]``), exactly as on the chunk-free rows.
* ``window = 5`` — the cap lifts the pick's READER RANK (``[4]`` -> ``[3]``);
  both readings are already inside the top 5, so the graded count is unchanged.
* ``window = 6`` — the cap puts the SECOND pick inside the top 5
  (``[4, 5]`` -> ``[3, 4]``, graded 1 -> 2); the chunk-free row stays
  ``[4, 5]`` / graded 1.
* ``window >= 7`` — the pre-cap ranks are ``5, 6`` or deeper, where the metric
  sees nothing, so the cap is the only reason a pick CAN be graded. How many it
  actually puts inside the top 5 is not uniform across the sweep, and the
  receipt's rows are the statement: at ``window = 7`` and at the shipped ``12``
  BOTH picks land at ``[3, 4]``; at ``13`` one lands on
  ``chunk_bearing_issue_probe`` (``[11, 12]`` -> ``[4, 5]``) and both on the other
  two pools; at ``14`` it is no longer enough on the probe at all
  (``[12]`` -> ``[5]``, graded 0) while the deep pool still grades both. That is
  why the sweep publishes 13 and 14 instead of stopping at the shipped window.

So the boundary the two shapes share is 3-6 (both graded, the ceiling doing the
work for the first pick) and the one they do NOT share is 7+: a chunk-free pool
grades nothing there at any swept depth, while a chunk-bearing pool grades at
least one pick at every swept depth except the probe's ``window = 14`` — where
the cap is no longer sufficient. Read the row, not the rule.

WHAT IT IS NOT
--------------
⛔ It is **not** the #9097 Q3 exit measurement and must never be reported as one.
There is no reader, no judge, no graph, no dataset and no spend here — the pools
are hand-built shapes, so the receipt measures the ARM'S GEOMETRY, never answer
quality. The end-to-end ``recall_all@5`` number stays owed to the eval lane, which
the owner ruling above has postponed.

⛔ It is **not a placement verdict**. ``--placement head`` rows are the rank-0
counterfactual the module docstring parks on the eval lane; they are included
because they are what makes "the unit is reachable only by displacement"
checkable, not because they are a recommendation.

Usage::

    uv run python -m tools.longmem_eval.window_arm_baseline            # write receipt
    uv run python -m tools.longmem_eval.window_arm_baseline --stdout   # print it
    uv run python -m tools.longmem_eval.window_arm_baseline --check    # exit 1 on drift
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter BEFORE the imports below — this module is a
# PACKAGE module, so its only working invocation is `python -m` (run as a script,
# `sys.path[0]` is this file's own directory and the `tools.longmem_eval` import
# dies), and a module-level 3.11+-only import would fail before a later guard
# could run. The refusal names the invocation that works, not the one that does
# not (#5136).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/longmem_eval/window_arm_baseline.py requires Python >= 3.12 "
        f"(got {sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python -m tools.longmem_eval.window_arm_baseline`"
    )

import argparse
import json
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
RECEIPT_PATH = (
    REPO / "docs" / "scoping" / "receipts"
    / "2026-11-03-10538-window-arm-retention.json"
)

#: The issue's own date range — every pool below is dated inside it, so the
#: window branch's membership predicate never changes across a row and a
#: difference between two rows is always the ARM's, never the filter's.
DATE_WINDOW = ("2023-04-10", "2023-04-21")

#: The shipped geometry: ``retrieve.py`` passes ``window=effective_top_k``
#: (``DEFAULT_TR_TOP_K = 12``), ``limit=DEFAULT_TEMPORAL_LEG_LIMIT`` (2) and
#: ``bucket_cap=DEFAULT_TEMPORAL_LEG_BUCKET_CAP`` (1).
SHIPPED_WINDOW = 12


def _pool(spec: list[tuple[str, int]],
          raw: tuple[str, ...] = ()) -> list[dict]:
    """Build a dated pool in semantic rank order from ``[(session, count), …]``.

    Indices are assigned in rank order exactly as a caller's annotated pool is,
    so ``sa0 … saN`` are the ids a reader window holds and ``sb10`` the ones a
    promotion reaches for.

    ``raw`` names the sessions whose hits are ingested as RAW
    ``session-transcript`` chunks (``point_kind``), which is what the multi-
    session corpus carries: the harness's ``ingest_mode=deterministic`` writes
    turn points AND raw session-transcript chunks, and ``dedup_pool`` caps those
    per session. A session NOT named is the compact epistemic surface (turn
    points), which ``dedup_pool`` never caps — so ``raw=()`` is the chunk-free
    geometry every other pool here uses, and the two shapes are told apart by a
    pool field rather than by a reader's memory.
    """
    from tortoise.retrieval import SESSION_TRANSCRIPT_KIND

    pool: list[dict] = []
    for session, count in spec:
        for _ in range(count):
            hit = {
                "id": f"{session.lower()}{len(pool)}",
                "session_id": session,
                "session_date": "2023-04-15",
                "content": "alpha",
            }
            if session in raw:
                hit["point_kind"] = SESSION_TRANSCRIPT_KIND
            pool.append(hit)
    return pool


def pool_for(name: str) -> list[dict]:
    """The pool a ``POOLS`` entry describes — spec AND chunk shape, together.

    Callers must go through this rather than ``_pool(entry["spec"])``: the
    ``raw`` field is part of the shape, and a helper that quietly dropped it
    would measure the chunk-free geometry while labelling it chunk-bearing.
    """
    entry = POOLS[name]
    return _pool(entry["spec"], entry.get("raw", ()))


#: The PRE-REGISTERED pools — each is a shape the #10538 record already names,
#: so the receipt reproduces the argument rather than inventing new cases.
#: ⛔ TWO EXCEPTIONS, both DISCLOSED at the entry itself rather than left to this
#: header: the ``chunk_bearing_issue_probe`` / ``chunk_bearing_whole_window_cluster``
#: entries re-measure two of the shapes above with the clustering session
#: ingested as raw transcript chunks (the shape the corpus is actually written
#: in, added by the finding-1 remedy), and ``chunk_bearing_deep_window`` is a
#: PURPOSE-BUILT shape with no pre-existing counterpart, added by the finding-3
#: remedy because both chunk-bearing pools above cap down to a pool NO DEEPER
#: than the window and so cannot express a displacement at all. It is not an
#: argument the record already made; it is the shape the record's question needs
#: in order to be answerable, and its entry says so.
#: ``clustered`` is what the arm's clustering gate must answer for
#: that pool at the shipped geometry (``head = window - limit``), so it is
#: EXECUTABLE metadata, not a comment: the pin asserts it against
#: ``tortoise.temporal_leg._session_clustered`` at every swept window. A
#: clustered pool can still be INERT — ``single_session_pool`` is, because every
#: bucket it could promote is one the reader already holds (cause (c) of
#: ``no_window_candidates``) — so ``clustered`` describes the gate, not the
#: outcome.
POOLS: dict[str, dict] = {
    # the issue's own probe (comment 6574179020): one session owns 10 of the 12
    # visible slots, two OTHER sessions hold the tail, and three more sessions
    # sit just below the window — the only promotable ones.
    "issue_probe_4520": {
        "spec": [("sA", 10), ("sB", 1), ("sC", 1), ("sD", 1), ("sE", 1),
                 ("sF", 1)],
        "clustered": True,
    },
    # the decision card's most favourable shape: the failure taken to its end,
    # one session owning the WHOLE window — and the arm is genuinely additive
    # here (+2 distinct, no deletion) at windows 6, 7 AND the shipped 12.
    # ⚠️ From 13 the picks reach past the window's tail and start deleting:
    # w=13 deletes ["sD"] (net falls to +1), w=14 deletes ["sE"] (net 0). That
    # is the same finding in the other direction, and it is why the receipt
    # publishes the whole sweep rather than the shipped row alone.
    "whole_window_cluster": {
        "spec": [("sA", 12), ("sD", 1), ("sE", 1), ("sF", 1)],
        "clustered": True,
    },
    # review cycles 5/7 (#10549): at the SHIPPED window every bucket in reach is
    # one the reader already holds, so the honest verdict there is INERT, not
    # "applied" (the receipt's window 12-14 rows; itself cause (c) of
    # ``no_window_candidates``). ⚠️ Shallower in the sweep it DOES fire —
    # windows 3-7 promote ``sb8``/``sc11`` for net +1/+2 — which is a different
    # geometry, not the harness's ``tr_top_k``.
    "partial_cluster_nothing_new": {
        "spec": [("sA", 8), ("sB", 3), ("sC", 1), ("sA", 1)],
        "clustered": True,
    },
    # the shape that looks additive and is not (fresh-context review, cycle 1):
    # a session holding TWO of the window's tail slots loses BOTH, so it leaves
    # the window while a sole-representative predicate sees nothing wrong and
    # ``net`` even reports a widening. This is why ``additive`` is defined on
    # session DELETION and not on single-slot occupancy.
    "two_slot_tail_session": {
        "spec": [("sA", 10), ("sB", 2), ("sD", 1), ("sE", 1)],
        "clustered": True,
    },
    # the control: the visible window already spans distinct sessions, so the
    # spread must decline and the fused order must be byte-identical.
    "unclustered_window": {
        "spec": [(f"s{i}", 1) for i in range(12)] + [("sD", 1), ("sE", 1)],
        "clustered": False,
    },
    # cause (c) of ``no_window_candidates``: one session only, so every
    # in-window bucket is held and the exclusion starves the pick set.
    "single_session_pool": {
        "spec": [("sA", 14)],
        "clustered": True,
    },
    # ⛔ THE SHAPE THE CORPUS IS ACTUALLY INGESTED AS (fresh-context review,
    # finding 1). Every pool above is CHUNK-FREE: no hit carries a
    # ``point_kind``, so ``dedup_pool`` is a no-op and the fused index space this
    # arm splices into happens to equal the reader's. The multi-session corpus is
    # not chunk-free — ``ingest_mode=deterministic`` writes raw
    # ``session-transcript`` chunks alongside the turn points — and ``dedup_pool``
    # caps those at ``DEFAULT_MAX_CHUNKS_PER_SESSION`` per session in rank order,
    # which REMOVES slots ABOVE a tail pick and lifts it into ``hits[:5]``. The
    # two pools below are the issue's own probe and the starved-cluster end shape
    # made chunk-bearing, i.e. the same argument, measured on the geometry the
    # arm targets instead of on a geometry that cannot show the cap's effect.
    "chunk_bearing_issue_probe": {
        "spec": [("sA", 10), ("sB", 1), ("sC", 1), ("sD", 1), ("sE", 1),
                 ("sF", 1)],
        "clustered": True,
        "raw": ("sA",),
    },
    "chunk_bearing_whole_window_cluster": {
        "spec": [("sA", 12), ("sD", 1), ("sE", 1), ("sF", 1)],
        "clustered": True,
        "raw": ("sA",),
    },
    # ⛔ THE DISPLACEMENT-EXPRESSIBLE CHUNK-BEARING SHAPE (fresh-context review,
    # finding 3). On BOTH pools above the reader's chunk cap leaves the pool
    # no deeper than the window — 8 and 6 against window 12 — so the
    # reader's window holds the WHOLE pool, and `evicted_ids`, `deleted_sessions`
    # and `additive` cannot express a displacement at all: they are entailed by
    # `reader_pool_depth <= window`, not measured, so an `additive: True` row
    # there
    # would read as "nothing was displaced" when it means "nothing COULD be".
    # This pool keeps the same shape (one raw-chunk session above the fold, the
    # reusable evidence below it) but carries enough compact sessions that the
    # capped pool — 3 raw chunks of sA + 12 turn-point sessions = 15 — still
    # FILLS the window, so "no session was deleted" is a MEASUREMENT here. Rows
    # publish `displacement_expressible` so the two readings cannot be confused.
    "chunk_bearing_deep_window": {
        "spec": [("sA", 12)] + [(f"s{chr(ord('B') + i)}", 1)
                                  for i in range(12)],
        "clustered": True,
        "raw": ("sA",),
    },
}

#: The geometry sweep: ``3,4,5,6`` are where a pick can still reach the graded
#: top 5; ``7`` is the first window where it cannot; ``12`` is the shipped one;
#: ``13,14`` are the deep-window end.
#:
#: ⛔ THAT BOUNDARY IS A PROPERTY OF THE CHUNK-FREE SHAPE, NOT OF THE SWEEP
#: (fresh-context review, finding 1). `fused rank == reader rank` only when no
#: hit is a raw ``session-transcript`` chunk. At ``3,4,5,6`` a pick is graded on
#: EITHER shape — the ceiling is inside the top 5 there, which is the CHUNK-FREE
#: reason, and the cap only decides whether the SECOND pick is graded too. From
#: ``7`` on the two shapes part: on a CHUNK-BEARING pool the cap removes the raw
#: chunks ABOVE the pick, so it is still graded at EVERY swept window from 3
#: through 13 (and at 14 on two of the three chunk-bearing pools), while on a
#: chunk-free pool it is graded nowhere from 7 on. Stating "7 is where it cannot"
#: as a fact about the sweep is the same one-shape-as-a-property-of-the-arm error
#: the shape-keyed verdict fields exist to prevent — read the row's own
#: ``chunk_bearing``.
GEOMETRY = (3, 4, 5, 6, 7, SHIPPED_WINDOW, 13, 14)

#: Pool depths swept at the shipped window — the "shallow pool" loophole: an arm
#: with nothing below the window is inert, and is NOT a win.
SHALLOW_DEPTHS = (6, 9, 11, 12, 13)


def _shallow_pool(depth: int) -> list[dict]:
    """A pool of exactly ``depth`` hits: one session owning all but the last two
    slots, then two single-slot sessions.

    The point of the sweep is the arm's INERT case: at ``depth <= window`` the
    reader window holds the whole pool, so every bucket below the promotion
    ceiling is one the window already holds and there is nothing to promote. An
    inert arm is not a win, and a receipt that only swept deep pools would let
    "fired == 0" read as "no harm" instead of "no opportunity".
    """
    assert depth >= 3, (
        f"_shallow_pool({depth}) cannot build its shape — the two trailing "
        f"single-slot sessions need at least one slot before them")
    spec: list[tuple[str, int]] = [("sA", depth - 2), ("sB", 1), ("sC", 1)]
    return _pool(spec)[:depth]


#: #10538: the guard state every row of this receipt is measured under. It is
#: a module constant so the row AND the geometry block read one value.
NON_EVICTION = False


def _row(pool: list[dict], *, window: int, limit: int, placement: str) -> dict:
    """One measured row — the SHIPPED wiring, never a re-implementation."""
    from tortoise.retrieval import is_raw_chunk
    from tortoise.temporal_leg import (
        effective_promotion_budget,
        temporal_leg_fusion_order,
        window_arm_retention,
    )

    order, picks = temporal_leg_fusion_order(
        pool, anchors=(), window=window, limit=limit,
        date_window=DATE_WINDOW, placement=placement,
        # ⛔ #10538 (owner ruling 2026-11-07): the non-eviction guard is now the
        # SHIPPED default, so this receipt — whose SUBJECT is the PRE-RULING
        # splice — must ask for the unguarded arm explicitly. ⚠️ Do NOT read
        # this as "the guard is inert on this instrument": it is not, and
        # flipping :data:`NON_EVICTION` to ``True`` does NOT reproduce these
        # rows. Measured on THIS instrument (review cycle 4, both arms run with
        # everything else unchanged): the guarded TAIL arm still fires on 25 of
        # the 85 rows, and ``picks``/``net``/``pick_ranks`` differ on 33 of
        # them — e.g. a ``w=13`` row goes from ``['se13','sf14'] net 1`` to
        # ``[] net 0``. The switch is passed EXPLICITLY (never inherited
        # from the environment) so the committed receipt stays a build
        # artifact that reproduces byte-for-byte on any box; the GUARDED arm's
        # behaviour is pinned mechanism-level in
        # `tests/test_temporal_leg_non_eviction_10538.py`, where each of the
        # three conditions has its own discriminating case.
        non_eviction=NON_EVICTION)
    # ``window_arm_retention``'s DEFAULT models the reader (the post-``dedup_pool``
    # pool the metric grades), so nothing here applies a chunk cap by hand: a row
    # measures whatever shape ``pool`` carries, and the shape is reported beside
    # it so a reader cannot mistake one for the other.
    retention = window_arm_retention(
        pool, order, picks=picks, window=window, limit=limit)
    return {
        "window": window,
        "pool_depth": len(pool),
        "chunk_bearing": any(is_raw_chunk(h) for h in pool),
        "reader_pool_depth": retention["reader_pool_depth"],
        "reader_pool_depth_fused": retention["reader_pool_depth_fused"],
        "limit_requested": limit,
        "budget_effective": effective_promotion_budget(window, limit),
        "placement": placement,
        # ⛔ #10538 (fresh-context review, finding 6): the row must name the
        # GUARD state too, not only the placement. The instrument runs the
        # PRE-RULING splice (``non_eviction=False``, see the call above), and
        # without this field an operator comparing a fresh `temporal_leg_stats`
        # row (guard ON by default) against this receipt would find two
        # artifacts disagreeing on picks/net with nothing in either naming the
        # difference.
        "non_eviction": NON_EVICTION,
        "picks": picks,
        "fired": bool(picks),
        "pick_ranks": retention["pick_ranks"],
        "pick_ranks_before_dedup": retention["pick_ranks_before_dedup"],
        "picks_dropped_by_reader_dedup":
            retention["picks_dropped_by_reader_dedup"],
        "picks_outside_window": retention["picks_outside_window"],
        "graded_by_recall_all_at_5": retention["graded_by_recall_all_at_5"],
        "base_sessions": retention["base_sessions"],
        "fused_sessions": retention["fused_sessions"],
        "net": retention["net"],
        "evicted_ids": retention["evicted_ids"],
        "evicted_sessions": retention["evicted_sessions"],
        "evicted_sole_representatives": retention["evicted_sole_representatives"],
        "deleted_sessions": retention["deleted_sessions"],
        "base_sessionless_slots": retention["base_sessionless_slots"],
        "fused_sessionless_slots": retention["fused_sessionless_slots"],
        "additive": retention["additive"],
        "base_graded_evicted": retention["base_graded_evicted"],
        # ⛔ CAN THIS ROW EXPRESS A DISPLACEMENT AT ALL? `evicted_ids` is
        # `[i for i in base_ids if i not in fused_set]` over two `[:window]`
        # windows of the SAME pool, so when the reader's pool is NO DEEPER than
        # the window (shallower OR exactly the window's height) both windows hold
        # the whole pool and the displacement
        # fields are entailed to be empty/True by arithmetic, not by the arm.
        # A row with this False says NOTHING about displacement in either
        # direction; the fields that name it (`additive`,
        # `unit_reachable_without_displacement…`) must not be read from it.
        "displacement_expressible": (
            retention["reader_pool_depth"] > window
            and retention["reader_pool_depth_fused"] > window),
        "order_changed": order != [c["id"] for c in pool],
    }


def measure() -> dict[str, Any]:
    """Build the whole receipt. Deterministic; no DB, no dataset, no network."""
    from tortoise.retrieval import DEFAULT_MAX_CHUNKS_PER_SESSION
    from tortoise.temporal_leg import (
        DEFAULT_TEMPORAL_LEG_BUCKET_CAP,
        DEFAULT_TEMPORAL_LEG_LIMIT,
    )

    rows: dict[str, list[dict]] = {}
    for name in POOLS:
        pool = pool_for(name)
        rows[name] = [
            _row(pool, window=w, limit=DEFAULT_TEMPORAL_LEG_LIMIT,
                 placement="tail")
            for w in GEOMETRY
        ]

    shipped_pool = pool_for("issue_probe_4520")
    shipped_geometry = [
        _row(shipped_pool, window=SHIPPED_WINDOW,
             limit=DEFAULT_TEMPORAL_LEG_LIMIT, placement=placement)
        for placement in ("tail", "head")
    ]

    # The SAME shipped geometry on the chunk-bearing shape — the geometry the
    # corpus is ingested with. Kept as an explicit pair rather than inferred from
    # ``by_pool`` so the two halves of the verdict cannot silently diverge.
    # ⛔ BOTH PLACEMENTS, PER POOL (fresh-context review, finding 2). The pre-fix
    # table carried ONE chunk-bearing row (tail), while `verdict["why"]` stated
    # that head "reaches ranks 0-1 on either shape" — a claim no chunk-bearing
    # row measured. Publishing the pair makes the head sentence a measurement on
    # each shape instead of an extrapolation from the chunk-free one.
    chunk_bearing_shipped = {
        f"chunk_bearing_{name}_{placement}": _row(
            pool_for(f"chunk_bearing_{name}"), window=SHIPPED_WINDOW,
            limit=DEFAULT_TEMPORAL_LEG_LIMIT, placement=placement)
        for name in ("issue_probe", "whole_window_cluster", "deep_window")
        for placement in ("tail", "head")
    }
    # The TAIL half alone — what "no pick is graded?" and "is the unit reachable
    # without displacement?" are questions about. The head half is the
    # counterfactual and is never folded into either verdict field.
    chunk_bearing_tail = {
        name.replace("_tail", ""): row
        for name, row in chunk_bearing_shipped.items()
        if name.endswith("_tail")
    }

    shallow_rows = []
    for depth in SHALLOW_DEPTHS:
        pool = _shallow_pool(depth)
        shallow_rows.append(
            _row(pool, window=SHIPPED_WINDOW,
                 limit=DEFAULT_TEMPORAL_LEG_LIMIT, placement="tail"))

    # The arm's headline figures, stated once so a reader does not have to
    # aggregate: at the shipped geometry, does the graded unit see ANY pick — and
    # does the answer depend on the pool's chunk shape?
    tail = shipped_geometry[0]
    head = shipped_geometry[1]
    return {
        "instrument": "tools/longmem_eval/window_arm_baseline.py",
        "issue": 10538,
        "epic": 2513,
        "date_window": list(DATE_WINDOW),
        "geometry": {
            "window": SHIPPED_WINDOW,
            "limit": DEFAULT_TEMPORAL_LEG_LIMIT,
            "bucket_cap": DEFAULT_TEMPORAL_LEG_BUCKET_CAP,
            # the PRE-RULING splice: this receipt is the baseline the #10538
            # non-eviction guard is measured AGAINST, so the guard is off here
            # by construction and every row says so.
            "non_eviction": NON_EVICTION,
            # The reader's per-session raw-chunk cap `window_arm_retention`
            # models by default (`retrieve.py` applies it AFTER fusion).
            "max_chunks_per_session": DEFAULT_MAX_CHUNKS_PER_SESSION,
        },
        "what_this_is": (
            "the window arm's RETENTION baseline on the shipped wiring — "
            "hermetic, no reader/judge/graph/dataset/spend. It measures the "
            "ARM'S GEOMETRY, never answer quality."
        ),
        "what_this_is_not": (
            "NOT the #9097 Q3 exit measurement, and NOT a placement verdict. "
            "recall_all@5 on the frozen write_path corpus is owed to the eval "
            "lane, postponed by the owner ruling of 2026-11-02 "
            "(no eval runs while the P0/P1 queue is open)."
        ),
        "verdict": {
            "scope": "AT THE SHIPPED GEOMETRY ONLY (window=12, limit=2, "
                     "bucket_cap=1, reader chunk cap applied), ON BOTH POOL "
                     "SHAPES: CHUNK-FREE pools (no raw session-transcript hits, "
                     "so the reader's per-session cap is inert and the fused "
                     "index space equals the reader's) and CHUNK-BEARING pools "
                     "(raw transcript chunks, the shape the multi-session "
                     "corpus is ingested as). The two do NOT agree, and neither "
                     "number is universal — read the field's own shape. The "
                     "sweep below reaches the graded top 5 at windows 3-6 ON A "
                     "CHUNK-FREE POOL (the pick's ceiling is itself inside the "
                     "top 5 there) — a different geometry, NOT the harness's "
                     "tr_top_k, so it is not a way to reach the unit. A "
                     "CHUNK-BEARING pool is graded at those same windows for "
                     "that same ceiling reason, and stays graded at EVERY swept "
                     "window from 3 through 13 because from window 7 on the "
                     "reader's chunk cap has removed the raw chunks above the "
                     "pick. Where a row carries "
                     "`displacement_expressible: false` the capped pool is "
                     "no deeper than the window (it holds the WHOLE pool, and "
                     "six non-expressible rows are exactly the window's "
                     "height), so its `additive` / "
                     "`evicted_ids` / `deleted_sessions` are arithmetic, not "
                     "measurement — read them only where it is true.",
            "shipped_tail_picks_graded_by_recall_all_at_5":
                tail["graded_by_recall_all_at_5"],
            "shipped_tail_net_distinct_sessions": tail["net"],
            "shipped_tail_additive": tail["additive"],
            "shipped_tail_deleted_sessions": tail["deleted_sessions"],
            "head_picks_graded_by_recall_all_at_5":
                head["graded_by_recall_all_at_5"],
            # ⛔ SHAPE-KEYED, because the answer is. A single boolean here was
            # the finding: it read FALSE only on the chunk-free pools the
            # instrument happened to build, and it was about to be read as a
            # property of the arm.
            "unit_graded_by_recall_all_at_5_at_window_12": {
                "chunk_free_pools": tail["graded_by_recall_all_at_5"],
                "chunk_bearing_pools": {
                    name: row["graded_by_recall_all_at_5"]
                    for name, row in chunk_bearing_tail.items()
                },
            },
            # DERIVED from the rows above (graded > 0 AND no session deleted),
            # never asserted — so the two fields cannot drift apart.
            #
            # ⛔ THE CHUNK-BEARING HALF IS READ ONLY FROM EXPRESSIBLE ROWS
            # (fresh-context review, finding 3). `additive` / `evicted_ids` are
            # entailed to be True/empty when the reader's capped pool is
            # no deeper than the window (it holds the whole pool), so a row
            # carrying
            # `displacement_expressible: false` cannot support a claim about
            # displacement in either direction. The excluded rows are NAMED
            # rather than silently dropped, and a shape with no expressible row
            # at all reports ``None`` — never a vacuous True.
            "unit_reachable_without_displacement_at_window_12": {
                "chunk_free_pools": bool(tail["graded_by_recall_all_at_5"])
                and tail["additive"],
                "chunk_bearing_pools": (
                    all(row["graded_by_recall_all_at_5"] and row["additive"]
                        for row in chunk_bearing_tail.values()
                        if row["displacement_expressible"])
                    if any(row["displacement_expressible"]
                           for row in chunk_bearing_tail.values()) else None),
                "chunk_bearing_pools_displacement_expressible": {
                    name: row["displacement_expressible"]
                    for name, row in chunk_bearing_tail.items()
                },
            },
            "why": (
                "TWO INDEX SPACES, and only one of them is graded. The splice "
                "puts a tail pick at ceiling = window - budget in the caller's "
                "PRE-cap fused order (ranks 10-11 at window=12). But the harness "
                "fuses and THEN dedups — retrieve.py reassigns `annotated` to the "
                "fused order and the next statement is `pool = "
                "_dedup_pool(annotated, max_chunks_per_session=3)`, with `hits` "
                "= that pool — and recall_all@5 grades hits[:5] of the DEDUPED "
                "pool. dedup_pool caps RAW session-transcript chunks per session "
                "in rank order and never caps points/turn points, so the cap is "
                "a no-op on a chunk-free pool (fused rank == reader rank: 0 of 2 "
                "picks graded) and REMOVES SLOTS ABOVE the pick on a "
                "chunk-bearing one, lifting both picks to reader ranks 3-4 where "
                "recall_all@5 grades BOTH. The chunk-bearing pool also loses no "
                "session at the shipped window (net 0, deleted_sessions [], "
                "additive True), where the chunk-free one trades sB and sC away "
                "(net 0, deleted_sessions ['sB','sC'], additive False). So the "
                "unit is reachable-without-displacement ONLY on the geometry the "
                "arm targets, and NOT on a chunk-free pool — the opposite of what "
                "the pre-fix receipt stated as a universal. Placement='head' "
                "is a different answer on each shape, and it is MEASURED on "
                "each (the shipped-geometry chunk-bearing table publishes the "
                "head row beside the tail one): on a CHUNK-FREE pool head "
                "reaches ranks 0-1 by EVICTING sB and sC — both sole "
                "representatives — so it buys the unit with a swap, and that "
                "is exactly the #8713 (a) risk below. On a CHUNK-BEARING pool "
                "head also reaches ranks 0-1 but evicts NOTHING "
                "(`evicted_ids: []`, `deleted_sessions: []`) on all three "
                "pools INCLUDING `chunk_bearing_deep_window`, whose capped "
                "pool still fills the window so the emptiness is measured "
                "rather than entailed — because the chunk cap had already "
                "brought both picks inside the reader window at ranks 3-4, so "
                "the promotion is a pure RE-ORDER there. That is not a "
                "recommendation for head: on that shape the TAIL already "
                "reaches the graded top 5, so head buys no unit the default "
                "does not. ⚠️ What is NOT claimed here: this counterfactual has "
                "never been measured on the shipped corpus, and this instrument "
                "still has no judge and no dataset. The nearest "
                "measured analogue is #8713's C4 source-session RE-INJECTION "
                "arm — a DIFFERENT mechanism, and legible only when its capsweep "
                "receipt "
                "(docs/scoping/receipts/2026-09-17-2513-reinjection-capsweep-"
                "412470cd3.json) is split three ways: "
                "(a) the INJECTION's own eviction costs reader_surface@5 "
                "0.9436 -> 0.9289 — and the guard-ablated `inj_only` arm reads "
                "the same 0.9289, so the guard accounts for NONE of it; that is "
                "pure slot displacement damaging the reader surface, which is "
                "exactly the risk head placement carries; "
                "(b) the shared session-diverse guard — a session capped at 2 "
                "INSIDE the window — adds evidence_recall@5 0.527 -> 0.4706 "
                "(-5.6 pt, +4/-11 questions); "
                "(c) the closure is the guard's alone — `inj_only` is identical "
                "to `off` on every RECALL metric (recall_all@5 0.6761, "
                "session_recall@5 0.831, evidence_recall@5 0.527), its only "
                "difference being the two reader_surface@5 regressions the "
                "injection itself causes, per (a) — so the SAME arm NET GAINS "
                "recall_all@5 0.6761 -> 0.8592 (+0.1831, +13/-0). "
                "So the session-diverse re-order buys the ALL-sessions metric "
                "with item-level evidence, while unguarded displacement damages "
                "the reader surface for nothing. The only direct reading of "
                "head placement is temporal_leg.py's TF-IDF replay (-placement "
                "head -limit 2: recall@12 0.9506 vs 0.9228 tail), on a "
                "near-ceiling base that cannot represent the shipped 0/52 "
                "failure — which is why the placement A/B belongs on the eval "
                "lane and why this instrument reports it rather than ranking it."
            ),
        },
        "by_pool": rows,
        "shipped_geometry_by_placement": shipped_geometry,
        "shipped_geometry_chunk_bearing": chunk_bearing_shipped,
        "shallow_pool_at_shipped_window": shallow_rows,
    }


def render_receipt() -> str:
    return json.dumps(measure(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_receipt(path: Path = RECEIPT_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_receipt(), encoding="utf-8")
    return path


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="compare against the committed receipt; write "
                             "nothing (exit 1 on drift)")
    parser.add_argument("--stdout", action="store_true",
                        help="print the receipt instead of writing it")
    args = parser.parse_args(argv)

    text = render_receipt()
    if args.stdout:
        print(text, end="")
        return 0
    if args.check:
        committed = (RECEIPT_PATH.read_text(encoding="utf-8")
                     if RECEIPT_PATH.exists() else "")
        if committed != text:
            print(f"⛔ receipt drift: {RECEIPT_PATH} does not match the "
                  f"instrument output")
            return 1
        print(f"✅ receipt reproduces: {RECEIPT_PATH}")
        return 0
    path = write_receipt()
    print(f"✅ wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
