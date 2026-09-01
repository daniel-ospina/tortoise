#!/usr/bin/env python3
"""Issue #2071 step 6/7 (I1): key-gated real-model probe — the 3 SSP
long-gold questions with CURATED human-verified correct-paraphrase
answers.

The probe INJECTS the curated answers directly (``judge.judge(...)`` per
question) to isolate the judge from reader capability — the current
default reader cannot produce a correct answer for b0479f84 (#2069
reader-MODEL class) or 1d4e3b97 (#2070 retrieval class), so the probe is
the only clean measurement of whether the semantic judge FAIRLY credits
a correct-but-paraphrased answer to each long-gold question (indicator
I1 at the top of the ladder).

Live path (default): requires a judge provider key (the ask_spotcheck
fail-fast contract — ``OPENAI_API_KEY`` for the official gpt-4o judge,
or the ``TORTOISE_LME_JUDGE_MODEL`` provider key); asserts the judge
returns True on all 3 curated paraphrases. Pending keys locally — the
live path is documented, not run (fail-fast verified instead).

Offline path (``--offline``): runs the SAME 3 probes through the
scripted fake judge (``ScriptedSemanticJudge`` with the pinned rules) —
the 3/3-True pin, key-free, for CI.

Example:
    uv run python tools/ask_spotcheck_probe.py --offline     # 3/3 True, exit 0
    uv run python tools/ask_spotcheck_probe.py               # fail-fast without key; 3/3 live with key
"""
from __future__ import annotations

import argparse
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from tools.ask_spotcheck import _load_composition, _require_judge_key  # noqa: E402
from tools.ask_spotcheck_consistency import (  # noqa: E402
    CURATED_ANSWERS,
    LONG_GOLD_QIDS,
    OFFLINE_RULES,
)
from tools.longmem_eval.judge import (  # noqa: E402
    ScriptedSemanticJudge,
    build_judge,
)


def _probes(questions: list[dict]) -> list[dict]:
    """The 3 long-gold probe cases: (question, gold, curated paraphrase)."""
    by_id = {q["question_id"]: q for q in questions}
    probes = []
    for qid in LONG_GOLD_QIDS:
        q = by_id[qid]
        probes.append({
            "question_id": qid,
            "question_type": q["question_type"],
            "question": q["question"],
            "answer": q["answer"],
            "hypothesis": CURATED_ANSWERS[qid]["answer"],
        })
    return probes


def run_probe(judge, probes: list[dict]) -> list[dict]:
    """Judge each curated paraphrase; returns per-question verdicts."""
    verdicts = []
    for p in probes:
        ok = judge.judge(
            question_type=p["question_type"], question=p["question"],
            answer=p["answer"], hypothesis=p["hypothesis"],
            abstention=False)
        verdicts.append({"question_id": p["question_id"], "ok": ok,
                         "hypothesis": p["hypothesis"][:80]})
    return verdicts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="I1 key-gated probe (issue #2071 step 6): curated "
                    "correct-paraphrase answers for the 3 SSP long-gold "
                    "questions must grade True (indicator 1)")
    ap.add_argument("--fixture", default=None,
                    help="composition fixture path (default: committed "
                         "fixture / TORTOISE_SPOTCHECK_FIXTURE / /tmp)")
    ap.add_argument("--offline", action="store_true",
                    help="run the scripted fake judge (key-free CI pin) "
                         "instead of the live build_judge()")
    args = ap.parse_args(argv)

    probes = _probes(_load_composition(args.fixture))

    if args.offline:
        judge = ScriptedSemanticJudge(rules=OFFLINE_RULES)
        judge_label = "scripted-semantic (offline fake)"
    else:
        try:
            key_env = _require_judge_key()
        except (RuntimeError, ValueError) as exc:
            print(f"ask_spotcheck_probe: {exc}", file=sys.stderr)
            return 2
        judge = build_judge()
        judge_label = f"live {judge.model_id} ({key_env})"

    verdicts = run_probe(judge, probes)
    for v in verdicts:
        print(f"[probe] {v['question_id']}: ok={v['ok']} "
              f"hypothesis={v['hypothesis']!r}")
    n_ok = sum(1 for v in verdicts if v["ok"])
    print(f"probe: {n_ok}/{len(verdicts)} True on curated correct "
          f"paraphrases (judge={judge_label}) — indicator I1")
    return 0 if n_ok == len(verdicts) else 1


if __name__ == "__main__":
    sys.exit(main())
