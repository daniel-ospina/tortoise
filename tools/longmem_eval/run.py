"""LongMemEval-S external comparability runner — CLI + programmatic entry.

    python -m tools.longmem_eval.run --split s [--limit N] [--mock] [...]

Pipeline per question (fresh isolated graph per question — the benchmark's
independent-memory protocol, no cross-question contamination):
    ingest haystack sessions → hybrid retrieval (graph + raw sessions) →
    reader LLM answers from context → official answer-check judge scores.

Resilience (per-question error isolation): each question is wrapped in its
own try/except — a transient LLM error is retried with exponential backoff
(--max-retries) and a question that still fails is recorded in the report's
``failures`` list while the run continues (one bad question never aborts the
whole 500-Q run). ``--checkpoint <state.json>`` checkpoints completed +
failed questions after every question; re-running with the same file resumes
(skips completed/failed, continues the rest).

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
import random
import sys
import tempfile
import time
from datetime import datetime, timezone
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
DEFAULT_MAX_RETRIES = 3
BACKOFF_BASE_S = 2.0
BACKOFF_CAP_S = 30.0

# Recorded verbatim in report methodology — published numbers carry their
# extraction approach (design-locked axis 2: "WITH full methodology").
EXTRACTION_APPROACH = (
    "deterministic session ingestion: episodic turn points (pointKind=event, "
    "[role] content, has_answer on evidence turns) + raw verbatim session "
    "transcripts (pointKind=session-transcript) + Session nodes; no LLM "
    "epistemic extraction in this run (LLM extraction is a documented "
    "future option)"
)

EXTRACTION_APPROACH_V2 = (
    "v2 extractor ingestion (#1369): the production 5-stage pipeline "
    "(extractor_v2.extract_session_v2 — S1 story chunked+compiled, S2 "
    "map-to-embed, S3 real-backend search, S4 gap review, S5 deterministic "
    "embed) per haystack session; payload written as entities/events/points/"
    "operators; raw verbatim transcripts retained; evidence-bearing points "
    "marked has_answer by content overlap (>=0.4)"
)


def _parse_ks(raw: str) -> tuple[int, ...]:
    return tuple(sorted({int(x.strip()) for x in raw.split(",") if x.strip()}))


def _call_with_backoff(fn, *, what: str, retries: int,
                       base: float = BACKOFF_BASE_S, cap: float = BACKOFF_CAP_S):
    """Call ``fn`` with exponential-backoff retries (transient LLM errors).

    Mirrors the official runner's ``backoff.on_exception(backoff.expo, ...)``
    on rate-limit/API errors: each attempt sleeps ``base**attempt`` (jittered,
    capped) before retrying; the last exception propagates once retries are
    exhausted (the caller's per-question guard then records the failure).
    """
    for attempt in range(1, retries + 2):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 — transient LLM/provider errors
            if attempt > retries:
                raise
            wait = min(base ** attempt, cap) * (0.5 + random.random() / 2)
            print(f"[longmem_eval] {what} failed (attempt {attempt}/{retries}): "
                  f"{e}; retrying in ~{wait:.1f}s", file=sys.stderr)
            time.sleep(wait)
    raise AssertionError("unreachable")  # pragma: no cover


def _load_checkpoint(path: str | None) -> tuple[dict[str, dict], list[dict]]:
    """Load (completed-by-qid, failures) from the checkpoint state file."""
    if not path:
        return {}, []
    p = Path(path)
    if not p.is_file():
        return {}, []
    data = json.loads(p.read_text(encoding="utf-8"))
    outcomes = {o["question_id"]: o for o in data.get("outcomes", [])}
    failures = list(data.get("failures", []))
    print(f"[longmem_eval] resumed checkpoint {p}: {len(outcomes)} completed, "
          f"{len(failures)} failed (skipping both)", file=sys.stderr)
    return outcomes, failures


def _save_checkpoint(path: str | None, outcomes: list[dict],
                     failures: list[dict]) -> None:
    """Atomically persist partial results after each question (resume)."""
    if not path:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps({
        "outcomes": outcomes,
        "failures": failures,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
    }, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def run_evaluation(
    instances: list[dict],
    *,
    reader,
    judge,
    ks: tuple[int, ...] = DEFAULT_KS,
    top_k: int = DEFAULT_TOP_K,
    work_dir: str | None = None,
    split: str = ds.DEFAULT_SPLIT,
    checkpoint: str | None = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    ingest_mode: str = "deterministic",
    extractor_model=None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run the full per-question pipeline over ``instances``.

    Each question gets a FRESH embedded graph (isolation). Per-question
    error isolation: a transient LLM/provider error is retried with
    exponential backoff, and a question that still fails is recorded in
    ``report['failures']`` and the run CONTINUES (one bad question never
    aborts the whole 500-Q run). Partial results are checkpointed to
    ``checkpoint`` after every question; a resume skips completed and
    previously-failed questions (delete the state file to re-run them).

    Returns (completed-outcomes, report-dict built from them).
    """
    done, prior_failures = _load_checkpoint(checkpoint)
    outcomes: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = list(prior_failures)

    for i, question in enumerate(instances):
        qid = question["question_id"]
        print(f"[longmem_eval] [{i + 1}/{len(instances)}] {qid} "
              f"({question.get('question_type', '?')})", file=sys.stderr)
        if qid in done:
            print(f"  [resume] {qid} already completed — reusing checkpoint",
                  file=sys.stderr)
            outcomes.append(done[qid])
            continue
        if any(f["question_id"] == qid for f in failures):
            print(f"  [resume] {qid} previously failed — skipping "
                  f"(delete the checkpoint to retry)", file=sys.stderr)
            continue

        t_q_start = time.monotonic()
        try:
            with tempfile.TemporaryDirectory(dir=work_dir, prefix="lme-") as td:
                sdk = TortoiseSDK(os.path.join(td, "lme.db"))
                try:
                    if ingest_mode == "v2":
                        from .ingest_v2 import ingest_haystack_v2
                        ingest_stats = ingest_haystack_v2(
                            sdk, question, extractor_model)
                    else:
                        ingest_stats = ingest_haystack(sdk, question)
                    ret = retrieve_for_question(sdk, question, ks=ks, top_k=top_k)

                    t0 = time.monotonic()
                    hypothesis = _call_with_backoff(
                        lambda: reader.answer(
                            context_hits=ret["hits"],
                            question=question["question"],
                            question_date=question.get("question_date", "") or None,
                        ),
                        what=f"reader for {qid}", retries=max_retries)
                    reader_ms = (time.monotonic() - t0) * 1000.0

                    t0 = time.monotonic()
                    label = _call_with_backoff(
                        lambda: judge.judge(
                            question_type=question.get("question_type", ""),
                            question=question["question"],
                            answer=question.get("answer", ""),
                            hypothesis=hypothesis,
                            abstention=is_abstention(qid),
                        ),
                        what=f"judge for {qid}", retries=max_retries)
                    judge_ms = (time.monotonic() - t0) * 1000.0
                finally:
                    sdk.close()

            outcome = {
                "question_id": qid,
                "question_type": question.get("question_type", ""),
                "question_date": question.get("question_date", ""),
                "label": label,
                "hypothesis": hypothesis,
                "ingest": ingest_stats,
                "n_ingest_errors": len(ingest_stats.get("errors", []) or []),
                "session_recall@k": ret["session_recall@k"],
                "turn_recall@k": ret["turn_recall@k"],
                "evidence_recall@k": ret.get("evidence_recall@k"),
                "context_tokens": ret["context_tokens"],
                "context_point_count": ret["context_point_count"],
                "retrieval_latency_ms": ret["retrieval_latency_ms"],
                "reader_latency_ms": round(reader_ms, 2),
                "judge_latency_ms": round(judge_ms, 2),
                "total_ms": round((time.monotonic() - t_q_start) * 1000.0, 2),
            }
            outcomes.append(outcome)
            done[qid] = outcome
        except Exception as e:  # noqa: BLE001 — per-question isolation (P2)
            print(f"[longmem_eval] question {qid} FAILED (non-fatal, "
                  f"continuing): {e!r}", file=sys.stderr)
            failures.append({
                "question_id": qid,
                "question_type": question.get("question_type", ""),
                "error": repr(e),
                "failed_at_utc": datetime.now(timezone.utc).isoformat(),
            })
        _save_checkpoint(checkpoint, list(done.values()), failures)

    return outcomes, outcomes_to_report(
        outcomes,
        reader_model=reader.model_id,
        judge_model=judge.model_id,
        ks=ks,
        top_k=top_k,
        split=split,
        ingest_mode=ingest_mode,
        failures=failures,
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
    ingest_mode: str = "deterministic",
    failures: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Aggregate outcomes (programmatic entry used by tests too)."""
    return build_report(
        outcomes,
        dataset_id=dataset_id,
        split=split,
        reader_model=reader_model,
        judge_model=judge_model,
        extraction_approach=(EXTRACTION_APPROACH_V2 if ingest_mode == "v2"
                            else EXTRACTION_APPROACH),
        ingest_mode=ingest_mode,
        ks=ks,
        top_k=top_k,
        failures=failures,
        extra={
            "outcomes": [
                {k: o[k] for k in (
                    "question_id", "question_type", "question_date", "label",
                    "hypothesis", "session_recall@k", "turn_recall@k",
                    "evidence_recall@k", "n_ingest_errors", "context_tokens",
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
        ev = (ret.get("evidence_recall@k") or {}).get(k)
        suffix = f"   evidence {ev}" if ev is not None else ""
        print(f"  k={k:<3} session {v}   turn {ret['turn_recall@k'][k]}{suffix}")
    ingest_errors = sum(o.get("n_ingest_errors", 0)
                        for o in report.get("outcomes", []))
    if ingest_errors:
        print(f"⚠ {ingest_errors} v2-ingest error(s) across questions — "
              f"recall may be raw-transcript-only (see report outcomes)")
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
    if report.get("n_failed", 0):
        print(f"failures: {report['n_failed']} question(s) did not complete — "
              f"see report['failures'] (run resumed from checkpoint)")
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
    p.add_argument("--ingest-mode", default="deterministic",
                   choices=["deterministic", "v2"],
                   help="ingestion: deterministic (turn points + raw transcripts) "
                        "or v2 (the production 5-stage extractor, #1369; raw "
                        "transcripts retained)")
    p.add_argument("--extractor-model", default=None,
                   help="extractor model spec for --ingest-mode v2 "
                        "(default deepseek-flash, uncapped — the #1350 owner "
                        "decision)")
    p.add_argument("--checkpoint", default=None,
                   help="partial-results state file (JSON) for error isolation "
                        "+ resume: completed/failed questions are checkpointed "
                        "after every question and skipped on re-run")
    p.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES,
                   help="per-question LLM-call retries with exponential backoff "
                        "before the question is recorded as failed (default 3)")
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

    extractor_model = None
    if args.ingest_mode == "v2":
        from tests.model_adapters import MODELS
        if args.extractor_model:
            if args.extractor_model not in MODELS:
                raise SystemExit(f"unknown extractor model {args.extractor_model!r}; "
                                 f"known: {sorted(MODELS)}")
            extractor_model = MODELS[args.extractor_model]()
        else:
            extractor_model = MODELS["deepseek-flash"]()

    outcomes, report = run_evaluation(
        instances, reader=reader, judge=judge, ks=ks, top_k=top_k,
        work_dir=args.work_dir, split=args.split,
        checkpoint=args.checkpoint, max_retries=args.max_retries,
        ingest_mode=args.ingest_mode, extractor_model=extractor_model,
    )

    out = args.output or str(default_report_path(args.split))
    save_report(report, out)
    _print_summary(report)
    print(f"\nreport saved to: {out}")
    return report


if __name__ == "__main__":
    run_main()
