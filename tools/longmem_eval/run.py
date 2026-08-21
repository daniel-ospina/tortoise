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

from tortoise.model_adapters import is_fatal
from tortoise.sdk import TortoiseSDK

from . import dataset as ds
from .ingest import DEFAULT_CHUNK_TURNS, ingest_haystack
from .judge import build_judge, is_abstention
from .preflight import FatalProviderError, PreflightError, run_preflight
from .reader import build_reader, reader_prompt_constants
from .report import build_report, default_report_path, save_report
from .retrieve import (
    DEFAULT_CONTEXT_TOKEN_CAP,
    DEFAULT_MAX_CHUNKS_PER_SESSION,
    retrieve_for_question,
)

DEFAULT_KS = (5, 10, 20)
DEFAULT_TOP_K = 20
DEFAULT_MAX_RETRIES = 3
BACKOFF_BASE_S = 2.0
BACKOFF_CAP_S = 30.0

# Recorded verbatim in report methodology — published numbers carry their
# extraction approach (design-locked axis 2: "WITH full methodology").
# R1 (#1540): raw verbatim evidence is turn-granular raw chunks (the
# whole-session blob is retired; union of chunks == the full session).
EXTRACTION_APPROACH = (
    "deterministic session ingestion: episodic turn points (pointKind=event, "
    "[role] content, has_answer on evidence turns) + turn-granular raw "
    "chunks (pointKind=session-transcript, non-overlapping verbatim windows "
    "of chunk_turns turns each — the union of chunks == the full session; "
    "chunks unmarked so the deterministic leg keeps its turn-id evidence "
    "path, R1 #1540) + Session nodes; no LLM epistemic extraction in this "
    "run (LLM extraction is a documented future option)"
)

EXTRACTION_APPROACH_V2 = (
    "v2 extractor ingestion (#1369): the production 5-stage pipeline "
    "(extractor_v2.extract_session_v2 — S1 story chunked+compiled, S2 "
    "map-to-embed, S3 real-backend search, S4 gap review, S5 deterministic "
    "embed) per haystack session; payload written as entities/events/points/"
    "operators; turn-granular raw chunks retained and containment-marked "
    "(R1 #1540 — written before extraction so verbatim retention survives "
    "extractor failure); evidence marked by three OR'd marks (M6 #1526 — "
    "source-session attribution / verbatim quote anchor / raw-chunk "
    "containment) written to the eval has_answer property; "
    "evidence_recall@k = N/A (None) when the graph has no evidence points"
)


def _parse_ks(raw: str) -> tuple[int, ...]:
    return tuple(sorted({int(x.strip()) for x in raw.split(",") if x.strip()}))


def _positive_int(raw: str) -> int:
    """argparse ``type=`` guard for the R1 knobs: 0/negative are rejected
    with a clear message (never a silent run — a 0 cap would empty the
    context and 0 chunk_turns would delete the verbatim leg)."""
    value = int(raw)
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1, got {value}")
    return value


def _resolve_int_knob(env_name: str, default: int, cli_value: int | None) -> int:
    """Knob resolution (mirrors the ``TORTOISE_LME_READER_MODEL`` pattern —
    ``spec or env or default``): the CLI flag wins when given, else the env
    var when set, else the default (CLI > env > default). An invalid env
    value fails loudly (SystemExit), never silently (R6 #1540)."""
    if cli_value is not None:
        return cli_value
    raw = os.environ.get(env_name)
    if raw is not None and raw.strip():
        try:
            value = int(raw.strip())
        except ValueError:
            raise SystemExit(
                f"{env_name} must be an integer, got {raw!r}") from None
        if value < 1:
            raise SystemExit(f"{env_name} must be >= 1, got {value}")
        return value
    return default


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
        except Exception as e:  # noqa: BLE001, RUF100
            # M2 (#1523, D4): a fatal-class error (401/402/403 or config-4xx
            # per the P2 taxonomy — tortoise/model_adapters.is_fatal) is
            # deterministic and permanent — retrying it is pointless. Re-raise
            # immediately; the caller's per-question guard then aborts the run
            # (FatalProviderError). Transients keep the existing backoff path.
            if is_fatal(e):
                raise
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
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
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
    workers: int = 1,
    preflight: dict | None = None,
    chunk_turns: int = DEFAULT_CHUNK_TURNS,
    max_context_tokens: int = DEFAULT_CONTEXT_TOKEN_CAP,
    max_chunks_per_session: int = DEFAULT_MAX_CHUNKS_PER_SESSION,
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
    import threading
    _lock = threading.Lock()
    # M5 (#1525): the run's reader prompt constants, recorded verbatim in the
    # report methodology — cross-cell prompt drift is visible in the report.
    system_prompt, type_fragments = reader_prompt_constants()

    def _run_one(question: dict, i: int) -> None:
        """Per-question pipeline — isolated graph, parallel-safe."""
        qid = question["question_id"]
        print(f"[longmem_eval] [{i + 1}/{len(instances)}] {qid} "
              f"({question.get('question_type', '?')})", file=sys.stderr)
        if qid in done:
            print(f"  [resume] {qid} already completed — reusing checkpoint",
                  file=sys.stderr)
            with _lock:
                outcomes.append(done[qid])
            return
        if any(f["question_id"] == qid for f in failures):
            print(f"  [resume] {qid} previously failed — skipping "
                  f"(delete the checkpoint to retry)", file=sys.stderr)
            return
        t_q_start = time.monotonic()
        try:
            with tempfile.TemporaryDirectory(dir=work_dir, prefix="lme-") as td:
                sdk = TortoiseSDK(os.path.join(td, "lme.db"))
                try:
                    if ingest_mode == "v2":
                        from .ingest_v2 import ingest_haystack_v2
                        ingest_stats = ingest_haystack_v2(
                            sdk, question, extractor_model,
                            chunk_turns=chunk_turns)
                    else:
                        ingest_stats = ingest_haystack(
                            sdk, question, chunk_turns=chunk_turns)
                    ret = retrieve_for_question(
                        sdk, question, ks=ks, top_k=top_k,
                        max_context_tokens=max_context_tokens,
                        max_chunks_per_session=max_chunks_per_session)

                    t0 = time.monotonic()
                    hypothesis = _call_with_backoff(
                        lambda: reader.answer(
                            # R1 (#1540) D6: the reader consumes EXACTLY the
                            # budget-capped points-first context the token
                            # metric reports (was the full uncapped pool).
                            context_hits=ret["context_points"],
                            question=question["question"],
                            question_date=question.get("question_date", "") or None,
                            question_type=question.get("question_type", "") or None,
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
                "ingest_error_text": (ingest_stats.get("errors") or [None])[0],
                "session_recall@k": ret["session_recall@k"],
                "turn_recall@k": ret["turn_recall@k"],
                "evidence_recall@k": ret.get("evidence_recall@k"),
                # R1 (#1540): the M6 raw-chunk containment view, wired
                # end-to-end (T5's sweep collection has a defined source).
                "chunk_evidence_recall@k": ret.get("chunk_evidence_recall@k"),
                "context_tokens": ret["context_tokens"],
                "context_point_count": ret["context_point_count"],
                "retrieval_latency_ms": ret["retrieval_latency_ms"],
                "reader_latency_ms": round(reader_ms, 2),
                "judge_latency_ms": round(judge_ms, 2),
                "total_ms": round((time.monotonic() - t_q_start) * 1000.0, 2),
            }
            with _lock:
                outcomes.append(outcome)
                done[qid] = outcome
        except Exception as e:  # noqa: BLE001, RUF100
            # M2 (#1523, D4): a fatal-class provider error mid-run means the
            # key died (billing cap hit, revocation) — continuing would
            # silently produce garbage questions. Abort the run instead of
            # recording a per-question failure (E2E-2: no silent degradation).
            # Transient-exhausted errors still record into ``failures`` and
            # the run continues (existing behavior — the per-question
            # isolation semantics are unchanged for transients).
            if is_fatal(e):
                raise FatalProviderError(where="run-loop", exc=e, qid=qid) from e
            print(f"[longmem_eval] question {qid} FAILED (non-fatal, "
                  f"continuing): {e!r}", file=sys.stderr)
            with _lock:
                failures.append({
                    "question_id": qid,
                    "question_type": question.get("question_type", ""),
                    "error": repr(e),
                    "failed_at_utc": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
                })
        with _lock:
            _save_checkpoint(checkpoint, list(done.values()), failures)

    # ── dispatch: sequential (workers=1) or a thread pool ──
    if workers <= 1:
        for i, question in enumerate(instances):
            _run_one(question, i)
    else:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [
                ex.submit(_run_one, question, i)
                for i, question in enumerate(instances)
            ]
            for f in concurrent.futures.as_completed(futures):
                f.result()  # re-raise any unexpected error

    return outcomes, outcomes_to_report(
        outcomes,
        reader_model=reader.model_id,
        reader_model_spec=getattr(reader, "model_spec", ""),
        reader_provider=getattr(reader, "provider", None),
        reader_pinned=getattr(reader, "pinned", None),
        reader_system_prompt=system_prompt,
        reader_type_fragments=type_fragments,
        judge_model=judge.model_id,
        ks=ks,
        top_k=top_k,
        split=split,
        ingest_mode=ingest_mode,
        failures=failures,
        preflight=preflight,
        # R1 (#1540) D7: knob values recorded verbatim in the methodology
        # (the run protocol step-2 gate consumes them).
        r1_knobs={
            "chunk_turns": chunk_turns,
            "context_token_cap": max_context_tokens,
            "max_chunks_per_session": max_chunks_per_session,
        },
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
    reader_model_spec: str = "",
    reader_provider: str | None = None,
    reader_pinned: bool | None = None,
    reader_system_prompt: str = "",
    reader_type_fragments: dict[str, str] | None = None,
    preflight: dict | None = None,
    r1_knobs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Aggregate outcomes (programmatic entry used by tests too).

    M5 (#1525): the reader's resolved identity (model_spec/provider/pinned)
    + the verbatim prompt constants are recorded in the methodology so the
    report self-describes exactly which reader model/prompt produced its
    numbers. R1 (#1540) D7: the granularity/context/dedup knob values are
    recorded via ``r1_knobs`` so published numbers carry their
    methodology.
    """
    extra: dict[str, Any] = {
        "outcomes": [
            {k: o.get(k) for k in (
                "question_id", "question_type", "question_date", "label",
                "hypothesis", "session_recall@k", "turn_recall@k",
                "evidence_recall@k",
                "chunk_evidence_recall@k",  # R1 #1540 D5: containment view
                "n_ingest_errors", "context_tokens",
                "retrieval_latency_ms", "reader_latency_ms",
                "judge_latency_ms", "total_ms",
            )}
            for o in outcomes
        ]
    }
    # M2 (#1523, D6): the pre-flight block rides ``extra["preflight"]`` —
    # additive (default None keeps every existing caller/test green; M7 owns
    # the report shape and may promote it into methodology later).
    if preflight is not None:
        extra["preflight"] = preflight
    return build_report(
        outcomes,
        dataset_id=dataset_id,
        split=split,
        reader_model=reader_model,
        judge_model=judge_model,
        # #1414 parity-leg producer: persist the methodology hashes (the
        # battery's unchanged-check compares these).
        reader_prompt_hash=_sha16(reader_prompt_source()),
        judge_rubric_id_hash=_sha16(JUDGE_RUBRIC_ID),
        reader_model_spec=reader_model_spec,
        reader_provider=reader_provider,
        reader_pinned=reader_pinned,
        reader_system_prompt=reader_system_prompt,
        reader_type_fragments=reader_type_fragments,
        extraction_approach=(EXTRACTION_APPROACH_V2 if ingest_mode == "v2"
                            else EXTRACTION_APPROACH),
        ingest_mode=ingest_mode,
        ks=ks,
        top_k=top_k,
        failures=failures,
        r1_knobs=r1_knobs,
        extra=extra,
    )


def _sha16(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


#: The judge rubric identity the parity module hashes against (must match
#: battery.parity's judge_rubric_id source string).
JUDGE_RUBRIC_ID = "longmemeval-official"


def reader_prompt_source() -> str:
    """The reader prompt content the parity module hashes. Mirrors the
    longmem_eval reader prompt; must be kept in sync with
    battery.parity.runner (the unchanged-check compares both sides).

    R1 (#1540) D6/D7: the reader consumes the budget-capped points-first
    context (UX decision 3) — the parity hash changes; the #1144 baseline
    record is refreshed at the next parity run (a run-time action — no
    committed baseline exists).
    """
    return (
        "Current Date: {question_date} header + per-session date annotation "
        "on every retrieved chunk (question_date + haystack_dates surfaced — "
        "temporal-reasoning questions are answerable); points-first "
        "budget-capped context (UX-3 #1540): extracted points render in "
        "rank order, raw turn-granular chunks backfill the remaining "
        "context_token_cap tokens"
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
        cev = (ret.get("chunk_evidence_recall@k") or {}).get(k)
        suffix = f"   evidence {ev}" if ev is not None else ""
        if cev is not None:
            suffix += f"   chunk-evidence {cev}"
        print(f"  k={k:<3} session {v}   turn {ret['turn_recall@k'][k]}{suffix}")
    ingest_errors = sum(o.get("n_ingest_errors", 0)
                        for o in report.get("outcomes", []))
    if ingest_errors:
        print(f"⚠ {ingest_errors} v2-ingest error(s) across questions — "
              f"recall may be raw-transcript-only (see report outcomes)")
    print(f"context tokens mean:     {ret['context_tokens_mean']}")
    lat = report["latency_ms"]
    print(f"latency (ms) retrieval/reader/judge/total:")  # noqa: F541
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
    # M2 (#1523): the gate result is visible in the run's stdout.
    from .preflight import format_preflight
    print(format_preflight(report.get("preflight")))
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
                   help="max context items handed to the reader (default 20; "
                        "the token budget --context-cap bounds it further, R1 #1540)")
    p.add_argument("--chunk-turns", type=_positive_int, default=None,
                   help="turns per raw-chunk window (env TORTOISE_LME_CHUNK_TURNS; "
                        "default 2; >= 1; the run protocol step-2 sweep selects "
                        "the value for the pilot + 500-Q run, R1 #1540)")
    p.add_argument("--context-cap", type=_positive_int, default=None,
                   help="reader context token budget (env TORTOISE_LME_CONTEXT_CAP; "
                        "default 8000 — points first, chunks backfill, R1 #1540)")
    p.add_argument("--max-chunks-per-session", type=_positive_int, default=None,
                   help="per-session raw-chunk cap in the retrieval pool "
                        "(env TORTOISE_LME_MAX_CHUNKS_PER_SESSION; default 2 — "
                        "E2E-1 session-dedup, R1 #1540)")
    p.add_argument("--mock", action="store_true",
                   help="offline mode: MockReader + MockJudge, no API keys (CI)")
    p.add_argument("--skip-preflight", action="store_true",
                   help="bypass the pre-flight API gate (debugging/offline "
                        "only — the run-protocol gate must be ON for "
                        "pilot/500 runs)")
    p.add_argument("--ingest-mode", default="deterministic",
                   choices=["deterministic", "v2"],
                   help="ingestion: deterministic (turn points + raw transcripts) "
                        "or v2 (the production 5-stage extractor, #1369; raw "
                        "transcripts retained)")
    p.add_argument("--extractor-model", default=None,
                   help="extractor model spec for --ingest-mode v2 "
                        "(default: the production router — TORTOISE_EXTRACTOR_"
                        "PROVIDER deepseek-direct primary / openrouter fallback, "
                        "uncapped, #1530)")
    p.add_argument("--workers", type=int, default=1,
                   help="parallel question workers (default 1 = sequential). "
                        "Each question runs in its own isolated graph; the "
                        "practical ceiling is provider rate limits + machine "
                        "memory (each worker spawns an embedded redislite "
                        "server). 8-16 on a quiet machine")
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
                        "openrouter:deepseek/deepseek-v4-flash — the M5 "
                        "pinned reader, #1525; an override records "
                        "reader_pinned=false + warns on stderr)")
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
    # R1 (#1540) knobs: env-first, CLI overrides, validated >= 1 (R6).
    chunk_turns = _resolve_int_knob("TORTOISE_LME_CHUNK_TURNS",
                                    DEFAULT_CHUNK_TURNS, args.chunk_turns)
    context_cap = _resolve_int_knob("TORTOISE_LME_CONTEXT_CAP",
                                    DEFAULT_CONTEXT_TOKEN_CAP, args.context_cap)
    max_chunks_per_session = _resolve_int_knob(
        "TORTOISE_LME_MAX_CHUNKS_PER_SESSION",
        DEFAULT_MAX_CHUNKS_PER_SESSION, args.max_chunks_per_session)

    instances = ds.load_dataset(
        args.split, limit=args.limit, data_path=args.data,
        cache=Path(args.cache_dir).expanduser() if args.cache_dir else None,
        download=not args.no_download,
    )

    reader = build_reader(args.reader_model, mock=args.mock)
    judge = build_judge(args.judge_model, mock=args.mock)

    extractor_model = None
    if args.ingest_mode == "v2":
        if args.extractor_model:
            # M5 pinning: an explicit --extractor-model stays a registry lookup.
            from tests.model_adapters import MODELS
            if args.extractor_model not in MODELS:
                raise SystemExit(f"unknown extractor model {args.extractor_model!r}; "
                                 f"known: {sorted(MODELS)}")
            extractor_model = MODELS[args.extractor_model]()
        else:
            # #1530 D9: the unset case delegates to the production router via
            # the shim — single source of truth (removed the bespoke env
            # branch). Same decision surface: TORTOISE_EXTRACTOR_PROVIDER
            # picks the primary; DEEPSEEK_API_KEY alone → deepseek-direct;
            # else OpenRouter. Uncapped (the #1350 owner decision).
            from tests.model_adapters import build_extractor_model
            extractor_model = build_extractor_model(
                max_tokens=None, temperature=0.0)

    # M2 (#1523): the pre-flight gate runs AFTER reader/judge/extractor_model
    # are built and BEFORE anything in the question loop starts. --mock skips
    # it (no keys/network); --skip-preflight bypasses it for debugging (the
    # run-protocol gate must be ON for pilot/500 runs).
    if args.mock or args.skip_preflight:
        preflight = {
            "status": "skipped", "mock": args.mock,
            "reason": "mock" if args.mock else "skip-preflight",
            "checks": [],
            "detail": "mock" if args.mock else "skipped via --skip-preflight",
        }
    else:
        try:
            preflight = run_preflight(
                reader=reader, judge=judge, extractor_model=extractor_model)
        except PreflightError as e:
            print("[longmem_eval] PRE-FLIGHT GATE FAILED — aborting before "
                  "the run starts (no questions executed):", file=sys.stderr)
            print(str(e), file=sys.stderr)
            raise SystemExit(1) from e

    try:
        outcomes, report = run_evaluation(  # noqa: RUF059
            instances, reader=reader, judge=judge, ks=ks, top_k=top_k,
            work_dir=args.work_dir, split=args.split,
            checkpoint=args.checkpoint, max_retries=args.max_retries,
            ingest_mode=args.ingest_mode, extractor_model=extractor_model,
            workers=max(1, args.workers), preflight=preflight,
            chunk_turns=chunk_turns, max_context_tokens=context_cap,
            max_chunks_per_session=max_chunks_per_session,
        )
    except FatalProviderError as e:
        print("[longmem_eval] RUN ABORTED — fatal provider error mid-run "
              "(a 401/402/403 means the key died; continuing would silently "
              "degrade the run):", file=sys.stderr)
        print(str(e), file=sys.stderr)
        raise SystemExit(1) from e

    out = args.output or str(default_report_path(args.split))
    save_report(report, out)
    _print_summary(report)
    print(f"\nreport saved to: {out}")
    return report


if __name__ == "__main__":
    run_main()
