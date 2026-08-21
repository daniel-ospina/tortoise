#!/usr/bin/env python3
"""Build ``tests/fixtures/lme_v2_healthy52.json`` — the 52-healthy calibration
fixture for the M6 evidence-marking recalibration (#1526, epic #1509).

CLI::

    python tools/longmem_eval/build_healthy52_fixture.py \
        --checkpoint /tmp/lme-v2-full.json \
        --dataset ~/.cache/tortoise-longmemeval/longmemeval_s_cleaned.json \
        --out tests/fixtures/lme_v2_healthy52.json

The fixture pins the 2026-08-20 v2 checkpoint + dataset state so the M6
micro-test (run protocol step 2) can calibrate the evidence marks OFFLINE —
no run, no LLM keys. Regeneration is a deliberate, reviewed act (a future
dataset re-download can change qids — the cleaned split is versioned on HF);
this script makes it reproducible with honest provenance.

**Healthy criterion:** v2-checkpoint outcomes with ``ingest.points > 0``
(52/496 on the 2026-08-20 run — all ``single-session-user``, first in run
order; extraction health decays with run position via 402 exhaustion).

**Per question (compact, from the dataset cache):** metadata + the has-answer
session's turns — the minimal content to recompute marks (a)+(c) offline (the
full 52-question haystack is 26.8 MB — not committable).

**Per question (v2-checkpoint subset):** ingest counts/recall/errors — pins
the old miscalibration (51/52 with ``evidence_points == 0``, total 1/12,085).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017


def _answer_sessions(question: dict) -> list[list[dict]]:
    """The turns of the has-answer session(s): sessions whose haystack id is
    an ``answer_session_ids`` member OR that contain >=1 ``has_answer`` turn
    (the M7 dataset-semantics audit owns the equivalence proof; for the 52
    healthy questions both select the same single session)."""
    sessions = question.get("haystack_sessions") or []
    ids = question.get("haystack_session_ids") or []
    answer_ids = set(question.get("answer_session_ids") or [])
    out: list[list[dict]] = []
    for si, session in enumerate(sessions):
        sid = ids[si] if si < len(ids) else f"{question['question_id']}-s{si}"
        is_answer = sid in answer_ids or any(
            bool(t.get("has_answer")) for t in session)
        if is_answer:
            out.append(session)
    return out


def build_fixture(checkpoint_path: Path, dataset_path: Path) -> dict:
    ck = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    outcomes = {o["question_id"]: o for o in ck.get("outcomes", [])}
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))

    healthy = [(qid, o) for qid, o in outcomes.items()
               if (o.get("ingest") or {}).get("points", 0) > 0]
    # Deterministic regeneration: run order in the checkpoint.
    healthy.sort(key=lambda t: ck["outcomes"].index(t[1]))

    by_qid = {q["question_id"]: q for q in dataset}
    questions: list[dict] = []
    missing: list[str] = []
    for qid, o in healthy:
        q = by_qid.get(qid)
        if q is None:
            missing.append(qid)
            continue
        ing = o.get("ingest") or {}
        ans_sessions = _answer_sessions(q)
        evidence_turns = [t for s in ans_sessions for t in s
                          if t.get("has_answer")]
        questions.append({
            "question_id": qid,
            "question_type": q.get("question_type", ""),
            "question": q.get("question", ""),
            "answer": q.get("answer", ""),
            "question_date": q.get("question_date", ""),
            "answer_session_ids": q.get("answer_session_ids") or [],
            "n_haystack_sessions": len(q.get("haystack_sessions") or []),
            "haystack_session_ids": q.get("haystack_session_ids") or [],
            "haystack_dates": q.get("haystack_dates") or [],
            # Compact: the has-answer session(s) turns only (verbatim
            # content) — enough to recompute marks (a)+(c) offline.
            "answer_sessions": ans_sessions,
            "n_evidence_turns": len(evidence_turns),
            # v2-checkpoint subset (pins the old miscalibration).
            "checkpoint": {
                "points": ing.get("points", 0),
                "evidence_points": ing.get("evidence_points", 0),
                "sessions": ing.get("sessions", 0),
                "turns": ing.get("turns", 0),
                "raw_transcripts": ing.get("raw_transcripts", 0),
                "entities": ing.get("entities", 0),
                "events": ing.get("events", 0),
                "operators": ing.get("operators", 0),
                "supersessions": ing.get("supersessions", 0),
                "n_ingest_errors": o.get("n_ingest_errors", 0),
                "first_error": o.get("ingest_error_text"),
                "evidence_recall@k": o.get("evidence_recall@k") or {},
                "turn_recall@k": o.get("turn_recall@k") or {},
                "session_recall@k": o.get("session_recall@k") or {},
            },
        })

    total_points = sum(q["checkpoint"]["points"] for q in questions)
    total_evidence = sum(q["checkpoint"]["evidence_points"] for q in questions)
    zero_evidence = sum(1 for q in questions
                        if q["checkpoint"]["evidence_points"] == 0)
    no_answer_session = sum(1 for q in questions if not q["answer_sessions"])
    evidence_turns_inside = sum(
        1 for q in questions
        if q["n_evidence_turns"] > 0)
    if missing:
        raise SystemExit(f"checkpoint qids missing from dataset: {missing}")

    fixture = {
        "_meta": {
            "dataset": "xiaowu0162/longmemeval-cleaned",
            "split": "s",
            "checkpoint_source": str(checkpoint_path),
            "dataset_source": str(dataset_path),
            "updated_at_utc": _now_iso(),
            "healthy_criterion": "v2 checkpoint outcome with ingest.points > 0 "
                                "(52/496 on the 2026-08-20 run)",
            "calibration_goal": "evidence-marking marks (a)+(c) coverage "
                                ">= 0.95 (E2E-3 gate); vacuity band initial "
                                "value (D6)",
            "miscalibration_note": f"old >=0.4 predicate fired "
                                   f"{total_evidence}/{total_points} "
                                   f"({zero_evidence}/52 healthy questions "
                                   f"with zero evidence marks)",
            "n_questions": len(questions),
            "n_evidence_turns": sum(q["n_evidence_turns"] for q in questions),
            "n_questions_without_answer_session": no_answer_session,
            "n_evidence_bearing": evidence_turns_inside,
            "builder": "tools/longmem_eval/build_healthy52_fixture.py",
        },
        "questions": questions,
    }
    return fixture


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--checkpoint", required=True, type=Path,
                   help="v2 run checkpoint JSON (outcomes[] with ingest stats)")
    p.add_argument("--dataset", required=True, type=Path,
                   help="LongMemEval-S cleaned dataset JSON (cache copy)")
    p.add_argument("--out", required=True, type=Path,
                   help="output fixture path (tests/fixtures/lme_v2_healthy52.json)")
    args = p.parse_args(argv)

    fixture = build_fixture(args.checkpoint, args.dataset)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(fixture, indent=1, sort_keys=True) + "\n",
        encoding="utf-8")

    meta = fixture["_meta"]
    print(f"wrote {args.out} ({args.out.stat().st_size / 1e6:.2f} MB)")
    print(f"  questions: {meta['n_questions']} "
          f"(healthy criterion: ingest.points > 0)")
    print(f"  evidence turns: {meta['n_evidence_turns']} across "
          f"{meta['n_evidence_bearing']} evidence-bearing questions")
    print(f"  without answer session: {meta['n_questions_without_answer_session']}")
    print(f"  checkpoint pin: {meta['miscalibration_note']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
