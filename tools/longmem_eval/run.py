"""LongMemEval-S external comparability runner — CLI + programmatic entry.

    python -m tools.longmem_eval.run --split s [--limit N] [--mock] [...]

Pipeline per question (fresh isolated graph per question — the benchmark's
independent-memory protocol, no cross-question contamination):
    ingest haystack sessions → hybrid retrieval (graph + raw sessions) →
    reader LLM answers from context → official answer-check judge scores.

Run modes:
    --mock        fully offline (MockReader + MockJudge; CI smoke, no keys)
    default       real LLM reader + judge via provider keys (env-driven)

Full run needs: the dataset (~tens of MB, auto-downloaded to
``~/.cache/tortoise-longmemeval`` or ``TORTOISE_LME_CACHE_DIR``) and provider
keys (OPENROUTER_API_KEY / OPENAI_API_KEY / …) — never committed, never
hardcoded. The committed MINI fixture + ``--mock`` exercises the whole
pipeline in CI.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from tortoise.sdk import TortoiseSDK

from . import dataset as ds
from .ingest import ingest_haystack
from .judge import build_judge, is_abstention
from .reader import build_reader
from .report import build_report, default_report_path, save_report
from .retrieve import retrieve_for_question

DEFAULT_KS = (5, 10, 20)
DEFAULT_TOP_K = 20

# Recorded verbatim in report methodology — published numbers carry their
# extraction approach (design-locked axis 2: "WITH full methodology").
EXTRACTION_APPROACH = (
    "deterministic session ingestion: episodic turn points (pointKind=event, "
    "[role] content, has_answer on evidence turns) + raw verbatim session "
    "transcripts (pointKind=session-transcript) + Session nodes; no LLM "
    "epistemic extraction in this run (LLM extraction is a documented "
    "future option)"
)


def _parse_ks(raw: str) -> tuple[int, ...]:
    return tuple(sorted({int(x.strip()) for x in raw.split(",") if x.strip()}))


def run_evaluation(
    instances: list[dict],
    *,
    reader,
    judge,
    ks: tuple[int, ...] = DEFAULT_KS,
    top_k: int = DEFAULT_TOP_K,
    work_dir: str | None = None,
    split: str = ds.DEFAULT_SPLIT,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run the full per-question pipeline over ``instances``.

    Each question gets a FRESH embedded graph (isolation). Returns
    (outcomes, report-dict built from them).
    """
    outcomes: list[dict[str, Any]] = []
    for i, question in enumerate(instances):
        qid = question["question_id"]
        print(f"[longmem_eval] [{i + 1}/{len(instances)}] {qid} "
              f"({question.get('question_type', '?')})", file=sys.stderr)
        t_q_start = time.monotonic()
        with tempfile.TemporaryDirectory(dir=work_dir, prefix="lme-") as td:
            sdk = TortoiseSDK(os.path.join(td, "lme.db"))
            try:
                ingest_stats = ingest_haystack(sdk, question)
                ret = retrieve_for_question(sdk, question, ks=ks, top_k=top_k)

                t0 = time.monotonic()
                hypothesis = reader.answer(
                    context_hits=ret["hits"], question=question["question"])
                reader_ms = (time.monotonic() - t0) * 1000.0

                t0 = time.monotonic()
                label = judge.judge(
                    question_type=question.get("question_type", ""),
                    question=question["question"],
                    answer=question.get("answer", ""),
                    hypothesis=hypothesis,
                    abstention=is_abstention(qid),
                )
                judge_ms = (time.monotonic() - t0) * 1000.0
            finally:
                sdk.close()

        outcomes.append({
            "question_id": qid,
            "question_type": question.get("question_type", ""),
            "label": label,
            "hypothesis": hypothesis,
            "ingest": ingest_stats,
            "session_recall@k": ret["session_recall@k"],
            "turn_recall@k": ret["turn_recall@k"],
            "context_tokens": ret["context_tokens"],
            "context_point_count": ret["context_point_count"],
            "retrieval_latency_ms": ret["retrieval_latency_ms"],
            "reader_latency_ms": round(reader_ms, 2),
            "judge_latency_ms": round(judge_ms, 2),
            "total_ms": round((time.monotonic() - t_q_start) * 1000.0, 2),
        })

    return outcomes, outcomes_to_report(
        outcomes,
        reader_model=reader.model_id,
        judge_model=judge.model_id,
        ks=ks,
        top_k=top_k,
        split=split,
    )


def outcomes_to_report(
    outcomes: list[dict[str, Any]],
    *,
    reader_model: str,
    judge_model: str,
    ks: tuple[int, ...],
    top_k: int,
    split: str,
    dataset_id: str = "xiaowu0162/longmemeval-cleaned",
) -> dict[str, Any]:
    """Aggregate outcomes (programmatic entry used by tests too)."""
    return build_report(
        outcomes,
        dataset_id=dataset_id,
        split=split,
        reader_model=reader_model,
        judge_model=judge_model,
        extraction_approach=EXTRACTION_APPROACH,
        ks=ks,
        top_k=top_k,
        extra={
            "outcomes": [
                {k: o[k] for k in (
                    "question_id", "question_type", "label", "hypothesis",
                    "session_recall@k", "turn_recall@k", "context_tokens",
                    "retrieval_latency_ms", "reader_latency_ms",
                    "judge_latency_ms", "total_ms",
                )}
                for o in outcomes
            ]
        },
    )


def _print_summary(report: dict[str, Any]) -> None:
    acc = report["accuracy"]
    print("\n" + "=" * 64)
    print(f"LongMemEval {report['split']} — {report['n_questions']} questions")
    print(f"overall accuracy:        {acc['overall']}")
    print(f"task-averaged accuracy:  {acc['task_averaged']}")
    print(f"abstention accuracy:     {acc['abstention']} "
          f"(n={acc['abstention_n']})")
    for cat, v in acc["per_category"].items():
        print(f"  {cat:<28} {v['accuracy']} (n={v['n']})")
    ret = report["retrieval"]
    print("retrieval recall@k (session / turn):")
    for k, v in ret["session_recall@k"].items():
        print(f"  k={k:<3} session {v}   turn {ret['turn_recall@k'][k]}")
    print(f"context tokens mean:     {ret['context_tokens_mean']}")
    lat = report["latency_ms"]
    print(f"latency (ms) retrieval/reader/judge/total:")
    for key in ("retrieval", "reader", "judge", "total_per_question"):
        d = lat.get(key, {})
        print(f"  {key:<20} {d}")
    print("methodology: reader={} judge={} extraction={}".format(
        report["methodology"]["reader_model"],
        report["methodology"]["judge_model"],
        report["methodology"]["extraction_approach"][:60] + "…"))
    print("=" * 64)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m tools.longmem_eval.run",
        description="LongMemEval-S external comparability runner (issue #1144, "
                    "axis 2): ingest haystacks → hybrid retrieval → reader LLM "
                    "→ official GPT-4o judge, with full methodology provenance.")
    p.add_argument("--split", default=ds.DEFAULT_SPLIT,
                   choices=sorted(ds.SPLIT_FILES), help="dataset split (default s)")
    p.add_argument("--limit", type=int, default=None,
                   help="run only the first N questions (smoke; default: full split)")
    p.add_argument("--data", default=None,
                   help="local dataset JSON/JSONL path (skips download)")
    p.add_argument("--cache-dir", default=None,
                   help="dataset cache dir (default TORTOISE_LME_CACHE_DIR or "
                        "~/.cache/tortoise-longmemeval)")
    p.add_argument("--work-dir", default=None,
                   help="temp dir for per-question graphs (default system tmp)")
    p.add_argument("--k", default=",".join(map(str, DEFAULT_KS)),
                   help="comma-separated recall@k values (default 5,10,20)")
    p.add_argument("--top-k", type=int, default=DEFAULT_TOP_K,
                   help="context points handed to the reader (default 20)")
    p.add_argument("--mock", action="store_true",
                   help="offline mode: MockReader + MockJudge, no API keys (CI)")
    p.add_argument("--reader-model", default=None,
                   help="reader model spec <provider>:<model> "
                        "(env TORTOISE_LME_READER_MODEL; default "
                        "openrouter:deepseek/deepseek-chat)")
    p.add_argument("--judge-model", default=None,
                   help="judge model spec (env TORTOISE_LME_JUDGE_MODEL; "
                        "default openai:gpt-4o-2024-08-06 — the official judge)")
    p.add_argument("--output", default=None,
                   help="report JSON path (default "
                        "longmemeval_<split>_<ts>.report.json in CWD)")
    p.add_argument("--no-download", action="store_true",
                   help="fail instead of downloading the dataset")
    return p


def run_main(argv: list[str] | None = None) -> dict[str, Any]:
    args = _build_parser().parse_args(argv)
    ks = _parse_ks(args.k)
    top_k = args.top_k

    instances = ds.load_dataset(
        args.split, limit=args.limit, data_path=args.data,
        cache=Path(args.cache_dir).expanduser() if args.cache_dir else None,
        download=not args.no_download,
    )

    reader = build_reader(args.reader_model, mock=args.mock)
    judge = build_judge(args.judge_model, mock=args.mock)

    outcomes, report = run_evaluation(
        instances, reader=reader, judge=judge, ks=ks, top_k=top_k,
        work_dir=args.work_dir, split=args.split,
    )

    out = args.output or str(default_report_path(args.split))
    save_report(report, out)
    _print_summary(report)
    print(f"\nreport saved to: {out}")
    return report


if __name__ == "__main__":
    run_main()
