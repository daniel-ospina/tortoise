"""#2976 — offline replay of the temporal retrieval leg (measured effect).

WHY THIS EXISTS: the leg's real effect must be measured on the eval lane
(``tools/longmem_eval/run.py`` with ``TORTOISE_LME_TEMPORAL_LEG=1``), which
needs a full graph ingest and hours of compute. This script is the
*reproducible offline proxy* used to size and tune the leg before that run:
it replays the pinned 55-question temporal analysis subset
(``measure_temporal.deterministic_subset`` over the committed census) against
the dataset's OWN haystack turns, ranking them with a TF-IDF semantic proxy
and then applying the real leg (``tortoise.temporal_leg``) + the real
``rrf_fusion`` with the exact wiring formula from ``retrieve.py``.

WHAT IT IS NOT: the shipped retrieval path. The product ranks extracted
POINTS with a dense bge-small leg + FTS + structural; this proxy ranks raw
TURNS with TF-IDF. The proxy's baseline is near-ceiling (recall@12 ≈ 0.94),
so it can only prove the leg does no harm on an easy base — it cannot
reproduce the shipped 0/52 base where gold sits at ranks 41–120. Treat the
eval lane as authoritative; this script's job is to catch a regression that
re-introduces the rank-0 placement (`--placement head`; the mechanism is
pinned in tests, and the proxy shows it is NOT measurably worse here — so
placement is a structural choice, not a measured win).

Usage:
    uv run python -m tools.longmem_eval.temporal_leg_replay
"""
from __future__ import annotations

import argparse
import json
import sys

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from tortoise.temporal_leg import (
    DEFAULT_TEMPORAL_LEG_BUCKET_CAP,
    DEFAULT_TEMPORAL_LEG_LIMIT,
    DEFAULT_TEMPORAL_LEG_WEIGHT,
    effective_promotion_budget,
    temporal_leg_fusion_order,
)

from .dataset import load_dataset
from .measure_temporal import deterministic_subset, load_census
from .retrieve import detect_time_constraint


def _subset_questions(data_path: str | None) -> list[dict]:
    """The pinned 55-Q temporal subset, joined to the dataset instances."""
    census = load_census()
    pinned = {r["qid"] for r in deterministic_subset(census["rows"])}
    questions = load_dataset("s", data_path=data_path, download=data_path is None)
    by_id = {q.get("question_id") or q.get("qid"): q for q in questions}
    return [by_id[qid] for qid in pinned if qid in by_id]


def replay(questions: list[dict], *, window: int,
           limit: int = DEFAULT_TEMPORAL_LEG_LIMIT,
           weight: float = DEFAULT_TEMPORAL_LEG_WEIGHT,
           bucket_cap: int = DEFAULT_TEMPORAL_LEG_BUCKET_CAP,
           placement: str = "tail") -> dict:
    """Run the base ranking and the leg-fused ranking over each question."""
    base_recall = leg_recall = 0.0
    base_all_turns = leg_all_turns = 0
    base_all_sessions = leg_all_sessions = 0
    up = down = fired = n = 0
    for q in questions:
        sessions = q.get("haystack_sessions") or []
        dates = q.get("haystack_dates") or []
        session_ids = q.get("haystack_session_ids") or []
        contents: list[str] = []
        sid_of: list[str] = []
        date_of: list[str] = []
        gold: set[int] = set()
        for si, session in enumerate(sessions):
            for turn in session:
                contents.append(turn.get("content") or "")
                sid_of.append(session_ids[si] if si < len(session_ids) else "")
                date_of.append(dates[si] if si < len(dates) else "")
                if turn.get("has_answer"):
                    gold.add(len(contents) - 1)
        if not any(contents) or not gold:
            continue
        matrix = TfidfVectorizer(stop_words="english").fit_transform(
            [*contents, q["question"]])
        sim = cosine_similarity(matrix[-1], matrix[:-1])[0]
        base_order = sorted(range(len(contents)), key=lambda i: (-sim[i], i))

        constraint = detect_time_constraint(q["question"])
        candidates = [
            {"id": str(i), "session_date": date_of[i],
             "session_id": sid_of[i], "content": contents[i]}
            for i in base_order
        ]
        # the SHIPPED wiring (no re-implementation): window-tail placement
        # + the fixed promotion budget.
        order_ids, picks = temporal_leg_fusion_order(
            candidates, anchors=constraint.anchors, window=window,
            limit=limit, bucket_cap=bucket_cap, weight=weight,
            placement=placement)
        leg_order = [int(pid) for pid in order_ids]
        if picks:
            fired += 1

        n += 1
        base_top = set(base_order[:window])
        leg_top = set(leg_order[:window])
        base_recall += len(gold & base_top) / len(gold)
        leg_recall += len(gold & leg_top) / len(gold)
        base_all_turns += gold <= base_top
        leg_all_turns += gold <= leg_top
        gold_sessions = {sid_of[i] for i in gold}
        base_all_sessions += gold_sessions <= {sid_of[i] for i in base_top}
        leg_all_sessions += gold_sessions <= {sid_of[i] for i in leg_top}
        base_worst = max(base_order.index(i) for i in gold)
        leg_worst = max(leg_order.index(i) for i in gold)
        up += leg_worst < base_worst
        down += leg_worst > base_worst
    return {
        "questions": n,
        "leg_fired": fired,
        "window": window,
        "limit_requested": limit,
        "budget_effective": effective_promotion_budget(window, limit),
        "weight": weight,
        "bucket_cap": bucket_cap,
        "placement": placement,
        "evidence_turn_recall_base": round(base_recall / n, 4) if n else 0.0,
        "evidence_turn_recall_leg": round(leg_recall / n, 4) if n else 0.0,
        "all_gold_turns_base": base_all_turns,
        "all_gold_turns_leg": leg_all_turns,
        "all_gold_sessions_base": base_all_sessions,
        "all_gold_sessions_leg": leg_all_sessions,
        "worst_gold_rank_up": up,
        "worst_gold_rank_down": down,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window", type=int, default=12,
                        help="reader window (TR tr_top_k default)")
    parser.add_argument("--limit", type=int, default=DEFAULT_TEMPORAL_LEG_LIMIT,
                        help="promotion budget (DEFAULT_TEMPORAL_LEG_LIMIT); "
                             "use 1 for the conservative no-harm arm")
    parser.add_argument("--weight", type=float,
                        default=DEFAULT_TEMPORAL_LEG_WEIGHT,
                        help="temporal leg RRF weight")
    parser.add_argument("--bucket-cap", type=int,
                        default=DEFAULT_TEMPORAL_LEG_BUCKET_CAP,
                        help="per date/session cap inside the budget")
    parser.add_argument("--placement", choices=("tail", "head"), default="tail",
                        help="'tail' = shipped window-tail placement (a pick "
                             "backfills below the semantic head); 'head' = "
                             "the rank-0 counterfactual (diagnostic; "
                             "equal-or-better here, so placement is settled "
                             "structurally, not by this proxy)")
    parser.add_argument("--data", default=None,
                        help="local LongMemEval-S JSON (skips the cache)")
    parser.add_argument("--json", action="store_true", help="print JSON")
    args = parser.parse_args(argv)
    questions = _subset_questions(args.data)
    out = replay(questions, window=args.window, limit=args.limit,
                 weight=args.weight, bucket_cap=args.bucket_cap,
                 placement=args.placement)
    if args.json:
        print(json.dumps(out, indent=2, sort_keys=True))
    else:
        print(f"n={out['questions']} window={out['window']} "
              f"leg fired on {out['leg_fired']}")
        print(f"evidence-turn recall@{out['window']}: "
              f"base {out['evidence_turn_recall_base']} -> "
              f"leg {out['evidence_turn_recall_leg']}")
        print(f"ALL gold turns in window:  base {out['all_gold_turns_base']}"
              f"/{out['questions']} -> leg {out['all_gold_turns_leg']}"
              f"/{out['questions']}")
        print(f"ALL gold sessions co-present: "
              f"base {out['all_gold_sessions_base']}/{out['questions']} -> "
              f"leg {out['all_gold_sessions_leg']}/{out['questions']}")
        print(f"worst-gold-rank: up {out['worst_gold_rank_up']} "
              f"down {out['worst_gold_rank_down']}")
        print(f"config: limit={args.limit}->{out['budget_effective']} "
              f"(clamped) weight={args.weight} "
              f"bucket_cap={args.bucket_cap} "
              f"placement={out['placement']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
