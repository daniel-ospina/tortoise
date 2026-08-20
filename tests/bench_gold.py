"""Score an extractor config against a hand-annotated gold fixture.

    .venv/bin/python tests/bench_gold.py tests/gold/0323_excerpt.json \
        --point-model mock:cheap --relation-model openrouter:deepseek/deepseek-v4-flash

Reports:
  - point filtering: precision/recall of "is a real point" (the extractor keeps ALL
    utterances today — no filter step — so this quantifies the filler problem).
  - operators: precision/recall vs gold, both endpoint-only and with-gate.
This is the seed of tortoise-eval; gold is judgment-heavy and needs domain-expert review.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.api import EventAPI  # noqa: E402, I001, RUF100
from tortoise.extractor import LLMExtractor  # noqa: E402, RUF100
from tortoise.ingest import build_model  # noqa: E402, RUF100
from tortoise.log import EventLog  # noqa: E402, RUF100
from tortoise.projection import fold, split  # noqa: E402, RUF100


def _prf(pred: set, gold: set):
    tp = len(pred & gold)
    p = tp / len(pred) if pred else 0.0
    r = tp / len(gold) if gold else 0.0
    f = 2 * p * r / (p + r) if (p + r) else 0.0
    return p, r, f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("gold")
    ap.add_argument("--point-model", default="mock:cheap")
    ap.add_argument("--relation-model", default="openrouter:deepseek/deepseek-v4-flash")
    args = ap.parse_args()

    gold = json.load(open(args.gold, encoding="utf-8"))  # noqa: SIM115
    transcript = "Transcript\n" + "\n".join(f"S: {u}" for u in gold["utterances"]) + "\n"

    log = EventLog("/tmp/bench.jsonl")
    if os.path.exists("/tmp/bench.jsonl"):
        os.remove("/tmp/bench.jsonl")
    ext = LLMExtractor(build_model(args.point_model),
                       build_model(args.relation_model, reasoning=True))
    ext.run(transcript, gold["source"], EventAPI(log, initiated_by="extractor"))

    pts = fold(log.read_all())
    order = sorted(pts.values(), key=lambda p: p["created_at"])
    idx = {p["id"]: i for i, p in enumerate(order)}
    stmts, ops = split(pts)  # noqa: RUF059

    # points: extractor keeps every utterance → predicted_keep = all indices
    pred_keep = set(range(len(gold["utterances"])))
    gold_keep = set(gold["points_keep"])
    pp, pr, pf = _prf(pred_keep, gold_keep)

    # operators
    pred_ops_ep = {(idx[o["operator"]["inputs"][0]], idx[o["operator"]["inputs"][1]])
                   for o in ops if len(o["operator"]["inputs"]) == 2}
    pred_ops_g = {(idx[o["operator"]["inputs"][0]], idx[o["operator"]["inputs"][1]],
                   o["operator"]["op_type"]) for o in ops if len(o["operator"]["inputs"]) == 2}
    gold_ops_ep = {(o["src"], o["dst"]) for o in gold["operators"]}
    gold_ops_g = {(o["src"], o["dst"], o["gate"]) for o in gold["operators"]}
    ep = _prf(pred_ops_ep, gold_ops_ep)
    gp = _prf(pred_ops_g, gold_ops_g)

    print(f"config: points={args.point_model}  relations={args.relation_model}")
    print(f"points kept by extractor: {len(pred_keep)}  gold real points: {len(gold_keep)}")
    print(f"  POINT-FILTER   P={pp:.2f} R={pr:.2f} F={pf:.2f}  (no filter step yet → keeps filler)")
    print(f"operators predicted: {len(pred_ops_ep)}  gold: {len(gold_ops_ep)}")
    print(f"  OP endpoints   P={ep[0]:.2f} R={ep[1]:.2f} F={ep[2]:.2f}")
    print(f"  OP +gate       P={gp[0]:.2f} R={gp[1]:.2f} F={gp[2]:.2f}")
    print("\npredicted operators (idx→idx gate):")
    for o in ops:
        i = o["operator"]["inputs"]
        if len(i) == 2:
            hit = "✓" if (idx[i[0]], idx[i[1]]) in gold_ops_ep else "·"
            print(f"  {hit} {idx[i[0]]:2}->{idx[i[1]]:2} {o['operator']['op_type']}")
    print("gold operators:", [(o["src"], o["dst"], o["gate"]) for o in gold["operators"]])


if __name__ == "__main__":
    main()
