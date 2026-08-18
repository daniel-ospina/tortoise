"""Report aggregation + methodology provenance (issue #1144, axis 2).

Aggregates per-question outcomes into the published report shape:
overall + task-averaged + per-category accuracy (the five paper abilities:
information extraction, multi-session reasoning, temporal reasoning,
knowledge updates, abstention), per-type accuracy (the six raw dataset
types), retrieval recall@k (session- and turn-level), context tokens, and
latency — together with a full methodology block (dataset id, split, reader
model, judge model, extraction approach, k values, token estimator, git sha,
run date) so numbers are honestly contextualized (no "#1" claims).
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# question_type → paper category (the five abilities from the LongMemEval
# paper; abstention is signalled by the ``_abs`` suffix, not a type).
PAPER_CATEGORY = {
    "single-session-user": "Information Extraction",
    "single-session-assistant": "Information Extraction",
    "single-session-preference": "Information Extraction",
    "multi-session": "Multi-Session Reasoning",
    "temporal-reasoning": "Temporal Reasoning",
    "knowledge-update": "Knowledge Updates",
}


def category_of(question: dict) -> str:
    if "_abs" in question["question_id"]:
        return "Abstention"
    return PAPER_CATEGORY.get(question["question_type"], "Other")


def _mean(xs: list[float]) -> float:
    return round(sum(xs) / len(xs), 4) if xs else 0.0


def _percentile(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    import math
    k = (len(xs) - 1) * q
    lo = int(math.floor(k))
    hi = int(math.ceil(k))
    if lo == hi:
        return xs[lo]
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
            timeout=5, cwd=Path(__file__).resolve().parent.parent.parent,
        )
        return out.stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def build_report(
    outcomes: list[dict[str, Any]],
    *,
    dataset_id: str,
    split: str,
    reader_model: str,
    judge_model: str,
    extraction_approach: str,
    ingest_mode: str = "deterministic",
    ks: tuple[int, ...],
    top_k: int,
    extra: dict[str, Any] | None = None,
    failures: list[dict[str, Any]] | None = None,
    reader_prompt_hash: str = "",
    judge_rubric_id_hash: str = "",
) -> dict[str, Any]:
    # #1414 (additive to #1144): the methodology-unchanged check for the
    # battery parity leg compares these hashes against the baseline record.
    """Aggregate per-question outcomes into the report + provenance dict.

    ``outcomes`` must contain only COMPLETED questions (failed questions are
    passed via ``failures`` and reported separately — a transient LLM error
    on one question must not abort the run or skew the aggregates).
    """
    n = len(outcomes)

    # ── accuracy ──
    labels = [o["label"] for o in outcomes]
    overall = _mean([1.0 if l else 0.0 for l in labels])

    by_category: dict[str, list[bool]] = {}
    by_type: dict[str, list[bool]] = {}
    abstention_labels: list[bool] = []
    for o in outcomes:
        q = {"question_id": o["question_id"],
             "question_type": o.get("question_type", "")}
        by_category.setdefault(category_of(q), []).append(o["label"])
        by_type.setdefault(q["question_type"], []).append(o["label"])
        if "_abs" in q["question_id"]:
            abstention_labels.append(o["label"])

    per_category = {c: {"accuracy": _mean([1.0 if l else 0.0 for l in ls]),
                        "n": len(ls)} for c, ls in sorted(by_category.items())}
    per_type = {t: {"accuracy": _mean([1.0 if l else 0.0 for l in ls]),
                    "n": len(ls)} for t, ls in sorted(by_type.items())}
    # task-averaged accuracy = mean of the per-raw-type means (official
    # print_qa_metrics.py definition).
    task_averaged = (_mean([1.0 if l else 0.0 for l in labels])
                     if len(by_type) <= 1 else
                     _mean([v["accuracy"] for v in per_type.values()]))

    # ── retrieval recall@k (session + turn level, mean over questions) ──
    session_recall: dict[str, float] = {}
    turn_recall: dict[str, float] = {}
    evidence_recall: dict[str, float] = {}
    for k in ks:
        sr = [o["session_recall@k"].get(str(k), 0.0) for o in outcomes]
        tr = [o["turn_recall@k"].get(str(k), 0.0) for o in outcomes]
        session_recall[str(k)] = _mean(sr)
        turn_recall[str(k)] = _mean(tr)
        er = [(o.get("evidence_recall@k") or {}).get(str(k), None)
              for o in outcomes]
        if any(v is not None for v in er):
            evidence_recall[str(k)] = _mean([v or 0.0 for v in er])

    # ── context tokens ──
    ctx = [o["context_tokens"] for o in outcomes]
    ctx_mean = round(sum(ctx) / n, 1) if n else 0.0

    # ── latency (ms) ──
    def _lat(keys: list[str]) -> dict[str, float]:
        xs = []
        for o in outcomes:
            v = o
            for k in keys:
                v = v.get(k, 0.0)
            xs.append(float(v or 0.0))
        return {"mean_ms": round(sum(xs) / n, 2) if n else 0.0,
                "p50_ms": round(_percentile(xs, 0.50), 2),
                "p95_ms": round(_percentile(xs, 0.95), 2)} if xs else {}

    return {
        "benchmark": "LongMemEval",
        "dataset": dataset_id,
        "split": split,
        "n_questions": n,
        "accuracy": {
            "overall": overall,
            "task_averaged": task_averaged,
            "abstention": _mean([1.0 if l else 0.0 for l in abstention_labels]),
            "abstention_n": len(abstention_labels),
            "per_category": per_category,
            "per_type": per_type,
        },
        "retrieval": {
            "session_recall@k": session_recall,
            "turn_recall@k": turn_recall,
            "evidence_recall@k": evidence_recall or None,
            "context_tokens_mean": ctx_mean,
            "context_point_count_mean": round(
                sum(o["context_point_count"] for o in outcomes) / n, 2) if n else 0,
        },
        "latency_ms": {
            "retrieval": _lat(["retrieval_latency_ms"]),
            "reader": _lat(["reader_latency_ms"]),
            "judge": _lat(["judge_latency_ms"]),
            "total_per_question": _lat(["total_ms"]),
        },
        "methodology": {
            "reader_prompt_hash": reader_prompt_hash,
            "judge_rubric_id_hash": judge_rubric_id_hash,
            "reader_model": reader_model,
            "judge_model": judge_model,
            "ingest_mode": ingest_mode,
            "judge_rule": "official LongMemEval get_anscheck_prompt; "
                          "label = 'yes' in response.lower()",
            "judge_call_shape": "official evaluate_qa.py: messages=[user], "
                                "n=1, temperature=0, max_tokens=10 — no "
                                "response_format (JSON mode), no system "
                                "message",
            "reader_context_format": "official gen.py shape: 'Current Date: "
                                     "{question_date}' header + per-session "
                                     "date annotation on every retrieved chunk "
                                     "(question_date + haystack_dates surfaced — "
                                     "temporal-reasoning questions are "
                                     "answerable)",
            "extraction_approach": extraction_approach,
            "retrieval": "Tortoise hybrid RRF (FTS+vector+structural, TF-IDF "
                         "fallback) over graph turn points + raw session "
                         "transcripts (pointKind session-transcript)",
            "retrieval_scope": "ISOLATED per-question corpus — each question's "
                               "haystack is ingested into a fresh graph and "
                               "recall is measured against that question alone; "
                               "NOT the official full-corpus indexing (official "
                               "retrievers index all questions' histories "
                               "together). Per-question recall@k is therefore "
                               "computed on a smaller, question-scoped corpus "
                               "and is not directly comparable to the paper's "
                               "recall numbers",
            "recall_definition": "session-level: fraction of answer_session_ids "
                                 "(evidence sessions) in top-k; turn-level: "
                                 "fraction of has_answer turns in top-k; both "
                                 "measured over the isolated per-question corpus "
                                 "(see retrieval_scope)",
            "token_estimator": "whitespace tokens + 10% markup allowance",
            "k_values": list(ks),
            "top_k_context": top_k,
            "dataset_source": dataset_id,
            "split": split,
            "git_sha": git_sha(),
            "run_at_utc": datetime.now(timezone.utc).isoformat(),
        },
        "failures": failures or [],
        "n_failed": len(failures or []),
        **(extra or {}),
    }


def save_report(report: dict[str, Any], path: Path | str) -> Path:
    """Write the report JSON (pretty-printed) and return the path."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                 encoding="utf-8")
    return p


def default_report_path(split: str, *, output_dir: str | None = None) -> Path:
    """Default report path: output dir (or CWD) + timestamped filename."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = Path(output_dir) if output_dir else Path.cwd()
    return base / f"longmemeval_{split}_{stamp}.report.json"
