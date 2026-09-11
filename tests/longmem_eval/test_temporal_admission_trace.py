"""#2976 — temporal-retrieval admission trace: hermetic receipts + mechanism pin.

Diagnosis (2026-09-11): the shipped retrieval config answers 0 of 52
answerable TEMPORAL questions, while the gold-session oracle reader answers
42/52. The #2578 committed measurement (commit 1b59244e8, artifacts under
``docs/runbook/2578-*``) shows the loss is NOT pool admission — the gold is in
the pool for every question — but the READER-WINDOW CUT:

* 55/55 questions carry all their marked (``has_answer``) points in the
  retrieval pool (``marked_points_in_pool == marked_points_total``);
* those 113 marks sit at pool ranks **41-120** — zero in ``top-20``, zero in
  ``21-40`` (the deepened-pool headroom band, #1947);
* the temporal-reasoning reader window is ``tr_top_k`` = 12 items, applied to
  the semantic RRF head (``tools/longmem_eval/retrieve.py``: TR context is
  ``assemble_context(pool, top_k=tr_top_k, context_item_cap=tr_top_k)``,
  then merely re-sorted time-ascending *inside* that 12);
* therefore ``gold_admitted == 0/55`` and the reader refuses on 54/55
  (98.2%) — a refusal rate that is a *symptom* of the empty evidence window,
  not an independent reader defect;
* the TR temporal machinery that is supposed to reorder the pool
  (``_apply_time_window``) is INERT on this corpus: ``detect_time_constraint``
  returns ``interval``/``recency`` for **0 of 55** (34 → ``None``,
  21 → ``ordering``), so no window filter ever runs and ``tr_window_fallback``
  never fires;
* widening the window inside the RRF order (``tr_top_k`` 16/20/24) is measured
  null (0/0/1 admitted) because the band starts at rank 40, beyond even 24;
* only a full-pool REORDER closes the gap: ``applied-rerank`` /
  ``cap3-only`` admit 21/55 (answerable-correct 0 → 6/52; refusal
  0.982 → 0.764).

These tests are hermetic (committed artifacts + pure functions, no DB, no
network, no LLM). They pin the receipts so the trace cannot drift silently,
and pin the mechanism (a hit at pool rank >= the item cap is present in the
pool but absent from the reader context).

Refs #2976, #2578.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tools.longmem_eval.retrieve import (
    TimeConstraint,
    _apply_time_window,
    detect_time_constraint,
)
from tortoise.retrieval import assemble_context

REPO = Path(__file__).resolve().parent.parent.parent
OUTCOMES = REPO / "docs" / "runbook" / "2578-measured-outcomes.jsonl"
CENSUS = REPO / "tests" / "_assembly_census.json"

#: the pinned 55-Q temporal analysis subset (#2578 Task 2): ordering/compare
#: 34 + interval 19 + current-state 2. The 3 ``_abs`` qids inside it are
#: abstention-DESIGN controls (refusing is the correct behaviour).
SUBSET_CLASSES = ("ordering/compare", "interval", "current-state")
ABS_CONTROLS = ("gpt4_93159ced_abs", "gpt4_c27434e8_abs", "gpt4_fe651585_abs")

#: the measured baseline: every marked point lives in the deepened-pool
#: headroom band, never in the reader's 12-item window.
BASELINE_MARKS = 113
BASELINE_BANDS = {"top-20": 0, "21-40": 0, "41-120": 113, "121+": 0}


def _outcomes() -> list[dict]:
    return [json.loads(line)
            for line in OUTCOMES.read_text().splitlines() if line.strip()]


def _arm(arm: str) -> list[dict]:
    rows = [r for r in _outcomes() if r["arm"] == arm]
    assert rows, f"arm {arm!r} missing from {OUTCOMES.name}"
    return rows


def _bands(rows: list[dict]) -> Counter:
    agg: Counter = Counter()
    for r in rows:
        for band, n in (r["pool_depth"].get("marked_points_bands") or {}).items():
            agg[band] += n
    return agg


# ── Receipts: the committed #2578 measurement ─────────────────────────────


def test_baseline_gold_is_in_the_pool_but_never_admitted():
    """The loss is the rank CUT, not pool admission.

    If this ever fails with a non-empty ``41-120`` band and a non-zero
    ``gold_admitted``, the returned fix reached the reader window — the
    diagnosis below must then be re-derived, not assumed.
    """
    rows = _arm("A-default")
    assert len(rows) == 55

    # pool membership: complete for every question (nothing starved out)
    missing = [(r["qid"], r["pool_depth"]["marked_points_total"],
                r["pool_depth"]["marked_points_in_pool"])
               for r in rows
               if r["pool_depth"]["marked_points_in_pool"]
               != r["pool_depth"]["marked_points_total"]]
    assert missing == [], f"gold missing from the pool: {missing}"

    # ... and yet the reader saw none of it
    assert sum(bool(r["gold_admitted"]) for r in rows) == 0
    assert _bands(rows) == Counter(BASELINE_BANDS)
    assert sum(BASELINE_BANDS.values()) == BASELINE_MARKS


def test_baseline_refusal_is_a_symptom_of_the_empty_window():
    """54/55 refusals; the only 3 correct outcomes are abstention controls.

    The 98.2% refusal rate is not an independent reader defect — with zero
    gold admitted, abstaining is the *correct* reader behaviour. The
    answerable count is 52 (= 55 - 3 ``_abs``), and the baseline answers 0.
    """
    rows = _arm("A-default")
    assert sum(bool(r["reader_refusal"]) for r in rows) == 54
    correct = [r for r in rows if r["label"]]
    assert len(correct) == 3
    assert {r["qid"] for r in correct} <= set(ABS_CONTROLS)
    answerable = [r for r in rows if r["qid"] not in ABS_CONTROLS]
    assert len(answerable) == 52
    assert sum(1 for r in answerable if r["label"]) == 0


def test_window_widening_cannot_reach_the_band_only_reorder_can():
    """``tr_top_k`` 16/20/24 is null; a full-pool reorder admits 21.

    Reach ceiling: the marked band starts at pool rank 40, so an item cap of
    24 cannot reach it. A reorder (reranker) can — and does.
    """
    admitted = {arm: sum(bool(r["gold_admitted"]) for r in _arm(arm))
                for arm in ("A-default", "tr_top_k16", "tr_top_k20",
                            "tr_top_k24", "pool-only-isolation", "c2-on",
                            "cap3-only", "applied-rerank")}
    assert admitted["A-default"] == 0
    # window widening inside the semantic order: at most one lucky question
    assert admitted["tr_top_k16"] == 0
    assert admitted["tr_top_k20"] == 0
    assert admitted["tr_top_k24"] <= 1
    assert admitted["pool-only-isolation"] == 0
    assert admitted["c2-on"] == 0
    # reorder over the full pool: the only measured lever
    assert admitted["cap3-only"] == 21
    assert admitted["applied-rerank"] == 21


def test_pool_only_isolation_arm_is_vacuous_on_this_baseline():
    """The #2578 'depth isolation' arm did NOT widen anything.

    Since #1947 the baseline already fetches ``DEFAULT_POOL_SIZE`` = 120, so
    ``--rerank-pool 120 --rerank OFF`` is byte-identical to the baseline: its
    'widening the pool changes nothing' conclusion carries no evidence.
    Recorded as a finding (see the #2976 trace / follow-up filing).
    """
    base = _arm("A-default")
    arm = _arm("pool-only-isolation")
    assert {r["pool_depth"]["requested"] for r in base} == {120}
    assert {r["pool_depth"]["requested"] for r in arm} == {120}
    assert [r["gold_admitted"] for r in base] == [r["gold_admitted"] for r in arm]


def test_rerank_without_explicit_pool_collapses_the_fetch_depth():
    """``--rerank`` alone silently shrinks the candidate pool 120 -> 40.

    ``rerank_pool`` defaults to 40 and the rerank branch overrides
    ``pool_limit`` instead of taking ``max(rerank_pool, baseline)`` — so
    turning the reranker on discards the #1947 deepening unless the caller
    also passes ``--rerank-pool 120``. Recorded as a finding.
    """
    assert {r["pool_depth"]["requested"] for r in _arm("cap3-only")} == {40}
    assert {r["pool_depth"]["requested"] for r in _arm("applied-rerank")} == {120}


# ── Mechanism pin: admission is the window cut, not membership ────────────


def _synthetic_pool(n: int, marked_rank: int) -> list[dict]:
    return [
        {
            "id": f"lme:q:s{i}:t0",
            "content": f"[user] turn {i}",
            "session_id": f"s{i}",
            "lme_session_index": i,
            "session_date": f"2024-01-{i + 1:02d}",
            "has_answer": i == marked_rank,
            "point_kind": "event",
            "quote": "",
            "search_keys": [],
            "source_turn_id": "",
            "speaker": "user",
            "superseded_by": None,
            "supersedes": [],
        }
        for i in range(n)
    ]


def test_a_hit_below_the_item_cap_is_in_the_pool_but_not_the_window():
    """The exact TR mechanism: pool[:12] membership decides who the reader sees.

    A ``has_answer`` hit at pool rank 45 is a retrieval-pool member (so
    ``evidence_recall@k``/``pool_depth`` see it) yet cannot enter the reader
    context under the TR item cap of 12 — which is every measured failure.
    """
    deep = 45
    pool = _synthetic_pool(120, marked_rank=deep)
    assert pool[deep]["has_answer"] is True  # in the pool

    window = assemble_context(pool, top_k=12, max_context_tokens=8000,
                              context_item_cap=12, question_date="2024-06-01")
    ids = [h["id"] for h in window]
    assert len(window) == 12
    assert pool[deep]["id"] not in ids          # ... but not handed to the reader
    assert not any(h["has_answer"] for h in window)

    # widening to 20/24 still cannot reach it — the measured plateau
    for cap in (20, 24):
        assert not any(h["has_answer"] for h in assemble_context(
            pool, top_k=cap, max_context_tokens=8000, context_item_cap=cap,
            question_date="2024-06-01"))

    # a REORDER (what the reranker does) is the only thing that can promote it
    reordered = sorted(pool, key=lambda h: (not h["has_answer"], h["id"]))
    window2 = assemble_context(reordered, top_k=12, max_context_tokens=8000,
                               context_item_cap=12, question_date="2024-06-01")
    assert any(h["has_answer"] for h in window2)


# ── Mechanism pin: the TR temporal reorder is inert on this corpus ────────


def test_tr_time_constraint_detector_never_fires_a_window_on_the_census():
    """``_apply_time_window`` never runs on the 55-Q subset.

    ``detect_time_constraint`` returns ``interval``/``recency`` for 0 of 55
    (34 → ``None``, 21 → ``ordering``), so the only TR mechanism that can
    reorder/narrow the pool before the cut is dead code on this corpus:
    ``tr_window_fallback`` is never even reached, and TR questions keep the
    raw semantic RRF order.
    """
    census = json.loads(CENSUS.read_text())
    subset = [r for r in census["rows"] if r["cls"] in SUBSET_CLASSES]
    assert len(subset) == 55

    kinds = Counter(detect_time_constraint(r["question"]).kind for r in subset)
    measured = Counter({"ordering": 21, None: 34})
    assert kinds == measured
    assert kinds["interval"] == 0 and kinds["recency"] == 0


def test_ordering_constraint_does_not_reorder_or_filter_the_pool():
    """An ``ordering`` detection is a no-op on the pool (the docstring's
    'no filter, no reorder') — so 21/55 questions get no temporal handling
    beyond the flat ``recency_boost`` and the post-cut date re-sort."""
    pool = _synthetic_pool(20, marked_rank=15)
    out = _apply_time_window(pool, TimeConstraint("ordering"),
                            question_date="2024-06-01")
    assert [h["id"] for h in out] == [h["id"] for h in pool]
