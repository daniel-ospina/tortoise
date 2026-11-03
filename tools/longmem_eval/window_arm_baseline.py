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
  ``deleted_sessions`` / ``additive`` for the verdict. The predicate is reported
  here, never enforced: whether an arm MAY pay for a slot by evicting base
  evidence is the open owner ruling (``window-exit`` vs ``ab-head-ab``), and this
  file does not decide it.
* can the graded metric see the picks at all (``pick_ranks`` vs
  ``graded_by_recall_all_at_5``)?

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

import argparse
import json
import sys
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


def _pool(spec: list[tuple[str, int]]) -> list[dict]:
    """Build a dated pool in semantic rank order from ``[(session, count), …]``.

    Indices are assigned in rank order exactly as a caller's annotated pool is,
    so ``a0 … aN`` are the ids a reader window holds and ``<sid>1`` the ones a
    promotion reaches for.
    """
    pool: list[dict] = []
    for session, count in spec:
        for _ in range(count):
            pool.append({
                "id": f"{session.lower()}{len(pool)}",
                "session_id": session,
                "session_date": "2023-04-15",
                "content": "alpha",
            })
    return pool


#: The PRE-REGISTERED pools — every one of them is a shape the #10538 record
#: already names, so the receipt reproduces the argument rather than inventing
#: new cases. ``clustered`` is what the arm's clustering gate must answer for
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
    # one session owning the WHOLE window — at the SHIPPED window the arm is
    # genuinely additive here (+2 distinct). ⚠️ Only at window 12: deeper in the
    # sweep the picks reach past the window's tail and start deleting the
    # single-slot sessions below it (window 13 rows carry ``additive: false``,
    # ``deleted_sessions: ["sD"]``), which is the same finding in the other
    # direction and is why the receipt publishes the whole sweep.
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
}

#: The geometry sweep. ``3,4,5,6`` are where a pick can still reach the graded
#: top 5; ``7`` is the first window where it cannot; ``12`` is the shipped one;
#: ``13,14`` are the deep-window end.
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


def _row(pool: list[dict], *, window: int, limit: int, placement: str) -> dict:
    """One measured row — the SHIPPED wiring, never a re-implementation."""
    from tortoise.temporal_leg import (
        effective_promotion_budget,
        temporal_leg_fusion_order,
        window_arm_retention,
    )

    order, picks = temporal_leg_fusion_order(
        pool, anchors=(), window=window, limit=limit,
        date_window=DATE_WINDOW, placement=placement)
    retention = window_arm_retention(
        pool, order, picks=picks, window=window, limit=limit)
    return {
        "window": window,
        "pool_depth": len(pool),
        "limit_requested": limit,
        "budget_effective": effective_promotion_budget(window, limit),
        "placement": placement,
        "picks": picks,
        "fired": bool(picks),
        "pick_ranks": retention["pick_ranks"],
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
        "order_changed": order != [c["id"] for c in pool],
    }


def measure() -> dict[str, Any]:
    """Build the whole receipt. Deterministic; no DB, no dataset, no network."""
    from tortoise.temporal_leg import (
        DEFAULT_TEMPORAL_LEG_BUCKET_CAP,
        DEFAULT_TEMPORAL_LEG_LIMIT,
    )

    rows: dict[str, list[dict]] = {}
    for name, spec in POOLS.items():
        pool = _pool(spec["spec"])
        rows[name] = [
            _row(pool, window=w, limit=DEFAULT_TEMPORAL_LEG_LIMIT,
                 placement="tail")
            for w in GEOMETRY
        ]

    shipped_pool = _pool(POOLS["issue_probe_4520"]["spec"])
    shipped_geometry = [
        _row(shipped_pool, window=SHIPPED_WINDOW,
             limit=DEFAULT_TEMPORAL_LEG_LIMIT, placement=placement)
        for placement in ("tail", "head")
    ]

    shallow_rows = []
    for depth in SHALLOW_DEPTHS:
        pool = _shallow_pool(depth)
        shallow_rows.append(
            _row(pool, window=SHIPPED_WINDOW,
                 limit=DEFAULT_TEMPORAL_LEG_LIMIT, placement="tail"))

    # The arm's headline figure, stated once so a reader does not have to
    # aggregate: at the shipped geometry, does the graded unit see ANY pick?
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
                     "bucket_cap=1). The sweep below reaches the graded top 5 "
                     "at windows 3-6 — that is a different geometry, NOT the "
                     "harness's tr_top_k, so it is not a way to reach the unit.",
            "shipped_tail_picks_graded_by_recall_all_at_5":
                tail["graded_by_recall_all_at_5"],
            "shipped_tail_net_distinct_sessions": tail["net"],
            "shipped_tail_additive": tail["additive"],
            "shipped_tail_deleted_sessions": tail["deleted_sessions"],
            "head_picks_graded_by_recall_all_at_5":
                head["graded_by_recall_all_at_5"],
            "unit_reachable_without_displacement_at_window_12": False,
            "why": (
                "picks are spliced at ceiling = window - budget, so at window=12 "
                "they land at fused ranks 10-11 while recall_all@5 grades "
                "hits[:5]. Placement='head' reaches ranks 0-1 — but only by "
                "taking the semantic head's slots, so it too is a swap, not a "
                "widening. ⚠️ What is NOT claimed here: this counterfactual has "
                "never been measured on the shipped corpus. The nearest "
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
