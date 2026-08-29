#!/usr/bin/env python3
"""Detector-parity gate (#1987 Task 10): measure ``detect_question_type``
(tortoise/reader.py) against the eval dataset's ``question_type`` labels.

Runs the deterministic product detector over the eval dataset question texts
and compares to the dataset labels (all 6 types + the ``_abs`` abstention
set), printing per-class agreement + a mismatch census. ``--gate`` exits
non-zero below 0.85.

LABEL MAPPING (pinned — the detector returns the 4 fragment types + None):
  * ``single-session-user`` / ``single-session-assistant`` → None counts as
    AGREEMENT (no fragment exists for them — the generic baseline is the
    correct product output; dataset.py:11 six types). The 0.85 gate is
    defined over the MAPPED agreement; the single-session classes appear in
    the census only (per-class agreement shown) and are never counted as
    structural mismatches (strict-equality accounting would structurally cap
    agreement below 0.85 regardless of detector quality).
  * ``temporal-reasoning`` / ``knowledge-update`` / ``multi-session`` /
    ``single-session-preference`` → the detector's exact match.
  * ``_abs`` questions (the marker lives in question_id) are classified by
    their base type — the reader never sees the marker.

FAILURE BRANCH (P2-3): MAPPED agreement < 0.85 at the pre-ship gate → the
runbook records the branch and files a tracked follow-up issue with an
OWNER before the Task 12 merge gate passes; the detector default remains in
effect until the follow-up lands and is re-verified.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

import sys as _sys
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in _sys.path:
    _sys.path.insert(0, _REPO_ROOT)

# The dataset is fetched on demand into a cache dir — never committed.
from tools.longmem_eval.dataset import load_dataset  # noqa: E402

GATE_THRESHOLD = 0.85

#: dataset question_type → product detector output that counts as agreement
MAPPED_AGREEMENT = {
    "temporal-reasoning": "temporal-reasoning",
    "knowledge-update": "knowledge-update",
    "multi-session": "multi-session",
    "single-session-preference": "single-session-preference",
    # no fragment exists for these classes — the generic baseline is correct
    "single-session-user": None,
    "single-session-assistant": None,
}

#: classes reported in the census but never structural mismatches
_CENSUS_ONLY = {"single-session-user", "single-session-assistant"}


def run_parity(questions: list[dict]) -> dict:
    """Run the detector over question texts; return the census + mapped
    agreement. ``questions`` items must carry ``question`` and
    ``question_type`` (+ ``question_id`` for the _abs census)."""
    from tortoise.reader import detect_question_type

    census: Counter = Counter()
    agreement: Counter = Counter()
    mismatches: list[dict] = []
    for q in questions:
        label = q.get("question_type") or ""
        detected = detect_question_type(q.get("question") or "")
        expected = MAPPED_AGREEMENT.get(label)
        base_type = label.split("_abs")[0]
        census[base_type] += 1
        if label in MAPPED_AGREEMENT and detected == expected:
            agreement[base_type] += 1
        else:
            mismatches.append({
                "question_id": q.get("question_id", "?"),
                "label": label, "detected": detected,
            })
    total = sum(census.values())
    agreed = sum(agreement.values())
    return {
        "census": dict(census),
        "agreement": dict(agreement),
        "mismatches": mismatches,
        "mapped_agreement": (agreed / total) if total else 1.0,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--split", default="s", help="dataset split (default s)")
    ap.add_argument("--gate", action="store_true",
                    help="exit 1 when mapped agreement < 0.85")
    ap.add_argument("--limit", type=int, default=None,
                    help="cap the question count (debug)")
    args = ap.parse_args()

    ds = load_dataset(split=args.split)
    questions = list(ds)
    if args.limit:
        questions = questions[: args.limit]
    if not questions:
        print("detector-parity: dataset has no questions for split "
              f"{args.split!r} — cannot measure", file=sys.stderr)
        return 2

    result = run_parity(questions)
    print("detector-parity: per-class agreement (mapped) —")
    for cls in sorted(result["census"]):
        n = result["census"][cls]
        ok = result["agreement"].get(cls, 0)
        print(f"  {cls:28s} {ok:4d}/{n:4d}  ({ok / n:.3f})")
    print(f"mapped agreement: {result['mapped_agreement']:.3f} "
          f"(gate >= {GATE_THRESHOLD})")
    if result["mismatches"]:
        print(f"mismatch census: {len(result['mismatches'])} "
              f"(first 10 shown)")
        for m in result["mismatches"][:10]:
            print(f"  {m['question_id']}: label={m['label']!r} "
                  f"detected={m['detected']!r}")
    if args.gate and result["mapped_agreement"] < GATE_THRESHOLD:
        print("detector-parity: FAIL — mapped agreement below the 0.85 gate; "
              "record the branch in the Task 12 runbook and file the tracked "
              "follow-up issue (P2-3).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
