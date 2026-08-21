"""Option-5 full-context comparison cell (03-scope §Run/Testing Protocol).

The full-context cell is the CEILING / HEADROOM measurement of the memory
layer: on a question subset, feed the reader the ENTIRE haystack (every
session, every turn, dated) instead of the retrieved top-k — "option 5"
(owner-specified 2026-08-20):

    option 5 (full-context baseline cell) rides on the pilot (step 3) and the
    500 (step 5) on a ~50-question subset — tells us the ceiling / headroom
    of the memory layer.

Mechanically it is a READER-ONLY run: no graph, no retrieval. The full
haystack is rendered in the official gen.py shape (Current Date header +
per-session date annotations — the same shape the retrieval leg produces) and
handed to the reader; the official judge scores the answer. Recall is
trivially 1.0 (every session is in context), so the cell isolates
reader+judge capability — the ceiling any retrieval leg must approach.

Run via the run-protocol orchestrator (``run_protocol full-context``) or
directly::

    python -m tools.longmem_eval.full_context --data tests/fixtures/longmemeval_mini.json --limit 5 --mock
    python -m tools.longmem_eval.full_context --split s --limit 50   # real reader+judge

The report is saved with a methodology block that marks the cell as the
option-5 full-context comparison (never misread as a retrieval-backed run).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import dataset as ds
from .judge import build_judge, is_abstention
from .reader import build_reader
from .report import build_report, save_report
from .retrieve import _estimate_tokens, render_context

#: Report methodology marker — distinguishes the cell from retrieval-backed
#: runs (published numbers carry their methodology, #1144 axis 2).
CELL_EXTRACTION_APPROACH = (
    "option-5 full-context comparison cell (run protocol step 3/5): the reader "
    "received the ENTIRE dated haystack (all sessions, all turns) — no graph, "
    "no retrieval. Measures the memory layer's ceiling/headroom: recall is "
    "trivially 1.0 by construction."
)

DEFAULT_MAX_RETRIES = 3
BACKOFF_BASE_S = 2.0
BACKOFF_CAP_S = 30.0


def full_haystack_hits(question: dict) -> list[dict]:
    """Synthesize the hit list that IS the full haystack.

    One hit per haystack session, content = the verbatim session (same shape
    as the raw-transcript leg), dated + indexed so ``render_context`` renders
    the official gen.py shape the retrieval path uses. Every answer-bearing
    turn keeps its ``has_answer`` mark for the evidence-based reader.
    """
    from .ingest import _session_transcript

    dates: list[str] = question.get("haystack_dates") or []
    sessions: list[list[dict]] = question.get("haystack_sessions") or []
    hits: list[dict[str, Any]] = []
    for si, session in enumerate(sessions):
        hits.append({
            "id": f"fc:{question['question_id']}:s{si}",
            "content": _session_transcript(session),
            "match_source": "full-context",
            "session_id": f"fc-{si}",
            "lme_session_index": si,
            "session_date": dates[si] if si < len(dates) else "",
            "has_answer": any(t.get("has_answer") for t in session),
            "superseded_by": None,
            "supersedes": [],
        })
    return hits


def _call_with_backoff(fn, *, what: str, retries: int,
                       base: float = BACKOFF_BASE_S, cap: float = BACKOFF_CAP_S):
    """Exponential-backoff retries for transient LLM errors (mirrors run.py)."""
    for attempt in range(1, retries + 2):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001, RUF100
            if attempt > retries:
                raise
            wait = min(base ** attempt, cap) * (0.5 + random.random() / 2)
            print(f"[full_context] {what} failed (attempt {attempt}/{retries}): "
                  f"{e}; retrying in ~{wait:.1f}s", file=sys.stderr)
            time.sleep(wait)
    raise AssertionError("unreachable")  # pragma: no cover


def run_cell(
    instances: list[dict],
    *,
    reader,
    judge,
    checkpoint: str | None = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    split: str = ds.DEFAULT_SPLIT,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run the full-context cell over instances (reader + judge only).

    Per-question error isolation + checkpoint/resume mirror the main runner:
    transient LLM errors retry with backoff; a question that still fails is
    recorded in ``failures`` and the run continues. Returns (outcomes, report).
    """
    done: dict[str, dict] = {}
    failures: list[dict] = []
    if checkpoint:
        p = Path(checkpoint)
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            done = {o["question_id"]: o for o in data.get("outcomes", [])}
            failures = list(data.get("failures", []))
            print(f"[full_context] resumed {p}: {len(done)} completed, "
                  f"{len(failures)} failed", file=sys.stderr)

    outcomes: list[dict[str, Any]] = []
    for i, question in enumerate(instances):
        qid = question["question_id"]
        print(f"[full_context] [{i + 1}/{len(instances)}] {qid} "
              f"({question.get('question_type', '?')})", file=sys.stderr)
        if qid in done:
            outcomes.append(done[qid])
            continue
        if any(f["question_id"] == qid for f in failures):
            continue
        t0 = time.monotonic()
        try:
            hits = full_haystack_hits(question)
            question_date = question.get("question_date", "") or None
            context_text = render_context(hits, question_date=question_date)
            context_tokens = _estimate_tokens(context_text)
            t_reader = time.monotonic()
            hypothesis = _call_with_backoff(
                lambda: reader.answer(
                    context_hits=hits,
                    question=question["question"],
                    question_date=question_date,
                    question_type=question.get("question_type", "") or None,
                ),
                what=f"reader for {qid}", retries=max_retries)
            reader_ms = (time.monotonic() - t_reader) * 1000.0
            t_judge = time.monotonic()
            label = _call_with_backoff(
                lambda: judge.judge(
                    question_type=question.get("question_type", ""),
                    question=question["question"],
                    answer=question.get("answer", ""),
                    hypothesis=hypothesis,
                    abstention=is_abstention(qid),
                ),
                what=f"judge for {qid}", retries=max_retries)
            judge_ms = (time.monotonic() - t_judge) * 1000.0

            outcome = {
                "question_id": qid,
                "question_type": question.get("question_type", ""),
                "question_date": question_date or "",
                "label": label,
                "hypothesis": hypothesis,
                "ingest": {"sessions": len(hits), "points": 0, "errors": []},
                "n_ingest_errors": 0,
                # recall trivially 1.0 — every session is in context by design.
                "session_recall@k": {"5": 1.0, "10": 1.0, "20": 1.0},
                "turn_recall@k": {"5": 1.0, "10": 1.0, "20": 1.0},
                "evidence_recall@k": {"5": 1.0, "10": 1.0, "20": 1.0},
                "context_tokens": context_tokens,
                "context_point_count": len(hits),
                "retrieval_latency_ms": 0.0,
                "reader_latency_ms": round(reader_ms, 2),
                "judge_latency_ms": round(judge_ms, 2),
                "total_ms": round((time.monotonic() - t0) * 1000.0, 2),
            }
            outcomes.append(outcome)
            done[qid] = outcome
        except Exception as e:  # noqa: BLE001, RUF100
            print(f"[full_context] question {qid} FAILED (non-fatal): {e!r}",
                  file=sys.stderr)
            failures.append({
                "question_id": qid,
                "question_type": question.get("question_type", ""),
                "error": repr(e),
                "failed_at_utc": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
            })
        if checkpoint:
            p = Path(checkpoint)
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(p.suffix + ".tmp")
            # done is the source of truth (resumed + completed) — save it in
            # full so a resume reuses every completed outcome.
            tmp.write_text(json.dumps({
                "outcomes": list(done.values()),
                "failures": failures,
                "updated_at_utc": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
            }, indent=2), encoding="utf-8")
            os.replace(tmp, p)

    report = build_report(
        outcomes,
        dataset_id="xiaowu0162/longmemeval-cleaned",
        split=split,
        reader_model=reader.model_id,
        judge_model=judge.model_id,
        extraction_approach=CELL_EXTRACTION_APPROACH,
        ingest_mode="full-context-cell",
        ks=(5, 10, 20),
        top_k=len(instances[0].get("haystack_sessions") or []) if instances else 20,
        failures=failures,
        extra={
            "cell": "option-5 full-context comparison",
            "outcomes": [
                {k: o[k] for k in (
                    "question_id", "question_type", "question_date", "label",
                    "hypothesis", "context_tokens", "reader_latency_ms",
                    "judge_latency_ms", "total_ms",
                )}
                for o in outcomes
            ],
        },
    )
    # The cell bypasses retrieval entirely — replace the retrieval-backed
    # methodology lines (which build_report hardcodes for the main runner) so
    # the report does not self-contradict. Recall is trivially 1.0 by
    # construction: every session is in context.
    report["methodology"]["retrieval"] = (
        "NONE — option-5 full-context cell: the reader received the ENTIRE "
        "dated haystack verbatim; no graph, no retrieval leg")
    report["methodology"]["retrieval_scope"] = (
        "full-context cell: recall trivially 1.0 by construction (every "
        "session is in context) — the number measures reader+judge ceiling, "
        "not retrieval")
    report["methodology"]["recall_definition"] = (
        "trivial 1.0 — the cell feeds the whole haystack; no top-k to miss")
    return outcomes, report


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m tools.longmem_eval.full_context",
        description="Option-5 full-context comparison cell (#1549): reader "
                    "sees the ENTIRE haystack — the memory layer's ceiling.")
    p.add_argument("--split", default=ds.DEFAULT_SPLIT,
                   choices=sorted(ds.SPLIT_FILES))
    p.add_argument("--limit", type=int, default=None,
                   help="run only the first N questions (default: full split)")
    p.add_argument("--data", default=None,
                   help="local dataset JSON/JSONL path (skips download)")
    p.add_argument("--cache-dir", default=None)
    p.add_argument("--mock", action="store_true",
                   help="offline MockReader + MockJudge (CI smoke, no keys)")
    p.add_argument("--checkpoint", default=None,
                   help="partial-results state file (resume)")
    p.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES)
    p.add_argument("--reader-model", default=None)
    p.add_argument("--judge-model", default=None)
    p.add_argument("--output", default=None,
                   help="report JSON path (default: full_context_<ts>.report.json "
                        "in CWD — timestamped so repeated cell runs don't "
                        "clobber each other)")
    p.add_argument("--no-download", action="store_true")
    return p


def run_main(argv: list[str] | None = None) -> dict[str, Any]:
    args = _build_parser().parse_args(argv)
    instances = ds.load_dataset(
        args.split, limit=args.limit, data_path=args.data,
        cache=Path(args.cache_dir).expanduser() if args.cache_dir else None,
        download=not args.no_download,
    )
    reader = build_reader(args.reader_model, mock=args.mock)
    judge = build_judge(args.judge_model, mock=args.mock)
    outcomes, report = run_cell(
        instances, reader=reader, judge=judge,
        checkpoint=args.checkpoint, max_retries=args.max_retries,
        split=args.split,
    )
    if args.output:
        out = args.output
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")  # noqa: UP017
        out = str(Path.cwd() / f"full_context_{stamp}.report.json")
    save_report(report, out)
    acc = report["accuracy"]
    print("\n" + "=" * 64)
    print(f"Full-context cell ({report['cell']}) — {report['n_questions']} questions")
    print(f"ceiling accuracy: {acc['overall']}  (task-averaged {acc['task_averaged']})")
    for cat, v in acc["per_category"].items():
        print(f"  {cat:<28} {v['accuracy']} (n={v['n']})")
    print(f"context tokens mean: {report['retrieval']['context_tokens_mean']}")
    if report.get("n_failed", 0):
        print(f"failures: {report['n_failed']}")
    print("=" * 64)
    print(f"report saved to: {out}")
    return report


if __name__ == "__main__":
    run_main()
