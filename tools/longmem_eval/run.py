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
import hashlib
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
from tortoise.shared_state.concurrency import flock_exclusive

from . import dataset as ds
from .dataset_audit import audit_dataset
from .errors import eval_failure_class
from .ingest import DEFAULT_CHUNK_TURNS, ingest_haystack
from .judge import build_judge, is_abstention
from .preflight import FatalProviderError, PreflightError, run_preflight
from .reader import build_reader, reader_prompt_constants
from .report import (
    build_report,
    compare_reports,
    default_report_path,
    git_sha,
    print_comparison,
    save_report,
)
from .retrieve import (
    DEFAULT_CONTEXT_TOKEN_CAP,
    DEFAULT_MAX_CHUNKS_PER_SESSION,
    DEFAULT_TR_TOP_K,
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


# R3 (#1542): the MemDelta-pinned embedder (all-MiniLM-L6-v2, 384-dim,
# #399 calibration). The pre-flight probe asserts this dimension so a swap
# or a wrong-dimension model is caught before any run, never silently used.
PINNED_EMBEDDER_MODEL = "all-MiniLM-L6-v2"
EMBEDDER_PROBE_DIM = 384


def _sentence_transformers_version() -> str | None:
    """Guarded version lookup — None (null) when the extra is absent, never
    an exception (R3 #1542 D2)."""
    try:
        import importlib.metadata
        return importlib.metadata.version("sentence-transformers")
    except Exception:  # noqa: BLE001, RUF100
        return None


def _embedder_status(*, available: bool, reason: str | None,
                     model: str | None = None,
                     st_version: str | None = None) -> dict:
    """The embedder pre-flight status dict (R3 #1542 D2/D5): well-formed in
    every env — when the extra is absent the version field is null, not an
    exception."""
    return {
        "model": model,
        "sentence_transformers_version": st_version,
        "available": available,
        "reason": reason,
    }


def _preflight_embedder(*, mock: bool) -> dict:
    """R3 (#1542) D2: pre-flight the dense leg — never a silent None.

    Verifies USABILITY, not just loadability: after ``EmbeddingModel.get()``
    succeeds, runs one probe encode and asserts the 384-dim output. A real
    (non-mock) run refuses to start when the embedder is missing or broken
    (SystemExit naming the remediation commands); ``--mock`` warns and
    continues (the status is still recorded in the report methodology).

    Timeouts are mode-aware: real runs probe with ``load_timeout=600`` (the
    cold-download window for the first-ever model fetch); ``--mock`` probes
    with ``load_timeout=30`` so an offline env without a cached model warns
    and continues in ~30s instead of stalling 10 minutes.
    """
    from tortoise.embeddings import EmbeddingModel

    timeout = 30.0 if mock else 600.0
    try:
        model = EmbeddingModel.get(load_timeout=timeout)
    except Exception:  # noqa: BLE001, RUF100
        model = None
    st_version = _sentence_transformers_version()
    if model is None:
        status = _embedder_status(available=False, reason="no_embedder",
                                  st_version=st_version)
        return _finalize_embedder_preflight(status, mock=mock)
    model_id = getattr(model, "model_id", None) or PINNED_EMBEDDER_MODEL
    try:
        vec = model.encode(["probe"])
        if vec is None or len(vec) == 0:
            raise ValueError("empty probe encode output")
        dim = int(vec[0].shape[0] if hasattr(vec[0], "shape")
                  else len(vec[0]))
        if dim != EMBEDDER_PROBE_DIM:
            status = _embedder_status(
                available=False, reason="dim_mismatch",
                model=model_id, st_version=st_version)
            return _finalize_embedder_preflight(status, mock=mock)
    except Exception:  # noqa: BLE001, RUF100
        status = _embedder_status(
            available=False, reason="encode_failed",
            model=model_id, st_version=st_version)
        return _finalize_embedder_preflight(status, mock=mock)
    status = _embedder_status(available=True, reason=None,
                              model=model_id, st_version=st_version)
    print(f"[longmem_eval] embedder pre-flight OK: {model_id} "
          f"(sentence-transformers {st_version or 'n/a'})", file=sys.stderr)
    return status


def _finalize_embedder_preflight(status: dict, *, mock: bool) -> dict:
    """R3 (#1542) D2 gate: real runs refuse to start with a degraded dense
    leg (SystemExit with the exact remediation); ``--mock`` warns and
    continues (CI smoke stays runnable offline)."""
    reason = status.get("reason")
    if mock:
        print("[longmem_eval] WARNING: embedder unavailable "
              f"(reason={reason}) — the vector/dense leg is DISABLED for "
              "this mock run; install with: uv sync --group dev "
              "--extra embeddings", file=sys.stderr)
        return status
    raise SystemExit(
        "[longmem_eval] EMBEDDER PRE-FLIGHT FAILED — the dense (vector) "
        f"leg cannot run (reason={reason}). Refusing to start: publishing "
        "a dense-less report is worse than no report.\n"
        "The eval env must install the pinned embedder (R3 #1542):\n"
        "  uv sync --group dev --extra embeddings\n"
        "  uv run python -c \"from sentence_transformers import "
        "SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')\"\n"
        "Verify with:\n"
        "  uv run python -c \"from tortoise.embeddings import "
        "EmbeddingModel; m = EmbeddingModel.get(load_timeout=600); "
        "assert m is not None; print('embedder OK')\"")


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


class CheckpointStaleError(RuntimeError):
    """Refused-resume: the checkpoint's fingerprint does not match the
    effective run config (or the checkpoint predates the fingerprint
    contract). E2E-2 owned negative — stale resume aborts clearly instead of
    silently reusing results from a different config (M7 #1527, D7)."""


def _model_id(model: Any) -> str | None:
    """A stable fingerprint string for a model object (None → None)."""
    if model is None:
        return None
    mid = getattr(model, "model_id", None)
    return mid or repr(model)


def _build_fingerprint(*, reader_model: str, judge_model: str,
                       ks: tuple[int, ...], top_k: int, split: str,
                       ingest_mode: str, extractor_model: Any,
                       max_retries: int, dataset_fingerprint: str) -> dict:
    """The effective-run-config fingerprint (M7 #1527, D7 schema).

    ``workers`` is deliberately EXCLUDED (per-question isolation makes
    results workers-invariant) but recorded in ``methodology.workers``.
    """
    return {
        "git_sha": git_sha(),
        "python": sys.version.split()[0],
        "dataset_fingerprint": dataset_fingerprint,
        "split": split,
        "ks": list(ks),
        "top_k": top_k,
        "ingest_mode": ingest_mode,
        "extractor_model": _model_id(extractor_model),
        "reader_model": reader_model,
        "judge_model": judge_model,
        "max_retries": max_retries,
        "reader_prompt_hash": _sha16(reader_prompt_source()),
        "judge_rubric_id_hash": _sha16(JUDGE_RUBRIC_ID),
    }


def _fingerprint_diffs(expected: dict, actual: dict) -> list[str]:
    """Field names differing between two fingerprint dicts (sorted)."""
    return sorted(k for k in set(expected) | set(actual)
                  if expected.get(k) != actual.get(k))


def _dataset_fingerprint(path: Path) -> str:
    """sha256[:16] of the resolved dataset file (streamed) — "unknown" when
    unreadable (programmatic/test callers pass "unknown" explicitly)."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()[:16]
    except OSError:
        return "unknown"


def _load_checkpoint(path: str | None,
                     expected_fingerprint: dict) -> tuple[dict[str, dict], list[dict]]:
    """Load (completed-by-qid, failures) from the checkpoint state file.

    M7 (#1527, D7): the loaded checkpoint's fingerprint must match the
    effective run config — a mismatch raises ``CheckpointStaleError`` naming
    the differing fields (refuse stale resume). A legacy v1 checkpoint
    (no ``fingerprint`` key) is refused too. The read happens under an
    exclusive flock (D8) so a reader never sees a mid-merge file.
    """
    if not path:
        return {}, []
    p = Path(path)
    if not p.is_file():
        return {}, []
    with flock_exclusive(p.with_suffix(p.suffix + ".lock")):
        data = json.loads(p.read_text(encoding="utf-8"))
    fp = data.get("fingerprint")
    if fp is None:
        raise CheckpointStaleError(
            f"checkpoint {p} predates the fingerprint contract (no "
            f"'fingerprint' key) — delete it or re-fingerprint it")
    diffs = _fingerprint_diffs(expected_fingerprint, fp)
    if diffs:
        raise CheckpointStaleError(
            f"checkpoint {p} is stale: effective run config differs on "
            f"{sorted(diffs)} — refusing resume (delete the file to re-run "
            f"the questions)")
    outcomes = {o["question_id"]: o for o in data.get("outcomes", [])}
    failures = list(data.get("failures", []))
    print(f"[longmem_eval] resumed checkpoint {p}: {len(outcomes)} completed, "
          f"{len(failures)} failed (skipping both)", file=sys.stderr)
    return outcomes, failures


def _merge_checkpoint(path: Path, outcomes: list[dict],
                      failures: list[dict]) -> tuple[list[dict], list[dict]]:
    """Merge the on-disk checkpoint with the in-memory snapshot (M7 #1527,
    D8 — cross-process merge-under-lock): outcomes dict-by-qid (the fresh
    in-memory outcome wins on tie), failures append-only by qid. A missing
    or corrupt disk file → the in-memory snapshot wins (fresh start)."""
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            data = {}
        disk_out = {o["question_id"]: o for o in data.get("outcomes", [])}
        disk_fail = {f["question_id"]: f for f in data.get("failures", [])}
    else:
        disk_out, disk_fail = {}, {}
    merged_out = {**disk_out, **{o["question_id"]: o for o in outcomes}}
    merged_fail = {**disk_fail, **{f["question_id"]: f for f in failures}}
    return list(merged_out.values()), list(merged_fail.values())


def _save_checkpoint(path: str | None, outcomes: list[dict],
                     failures: list[dict], fingerprint: dict) -> None:
    """Atomically persist partial results after each question (resume).

    M7 (#1527, D7/D8): writes the code fingerprint; the write happens under
    an exclusive flock with a re-read-and-merge, so two concurrent run
    PROCESSES sharing one checkpoint lose nothing (each merge adds its
    qids). ``os.replace`` keeps the final file atomic.
    """
    if not path:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with flock_exclusive(p.with_suffix(p.suffix + ".lock")):
        merged_outcomes, merged_failures = _merge_checkpoint(
            p, outcomes, failures)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps({
            "fingerprint": fingerprint,
            "outcomes": merged_outcomes,
            "failures": merged_failures,
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
    # R3 (#1542) D2: the embedder pre-flight status (from _preflight_embedder
    # in run_main) — forwarded to the report methodology (D5: embedder +
    # vector_strategy always emitted; None default keeps programmatic
    # callers — tests, capstone #1549 — on the not_checked default).
    embedder_status: dict | None = None,
    chunk_turns: int = DEFAULT_CHUNK_TURNS,
    max_context_tokens: int = DEFAULT_CONTEXT_TOKEN_CAP,
    max_chunks_per_session: int = DEFAULT_MAX_CHUNKS_PER_SESSION,
    # R5 (#1544): TR knobs — temporal-reasoning questions get the events
    # union pool, the engine recency date weight, the TR-constraint window
    # filter, time-ascending rendering, and the tighter tr_top_k cap
    # (20→12). Non-TR questions ignore them (byte-identical path).
    tr_top_k: int = DEFAULT_TR_TOP_K,
    tr_date_weight: float = 0.5,
    tr_events: bool = True,
    # M7 (#1527): run-hygiene inputs.
    dataset_fingerprint: str = "unknown",
    integrity_threshold: float = 0.0,
    integrity_justification: str | None = None,
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

    M7 (#1527): computes the dataset recall-semantics audit from the loaded
    instances (publication gate — the report provably carries it), builds the
    checkpoint fingerprint from the effective run config (stale resume
    refused), and instruments every outcome with leg-mix / pool-size /
    evidence-written-·retrieved / ingest-latency fields.
    """
    # E2E-3 Precondition 2: the audit is computed from the loaded instances
    # BEFORE anything else — no report can be produced without it.
    dataset_semantics_audit = audit_dataset(instances)
    fingerprint = _build_fingerprint(
        reader_model=reader.model_id,
        judge_model=judge.model_id,
        ks=ks, top_k=top_k, split=split, ingest_mode=ingest_mode,
        extractor_model=extractor_model, max_retries=max_retries,
        dataset_fingerprint=dataset_fingerprint,
    )
    done, prior_failures = _load_checkpoint(checkpoint, fingerprint)
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
        # M7 (D6): the failure site for the error census. Covers the non-LLM
        # graph pipeline (ingest + retrieve) first; reader/judge set it just
        # before their calls.
        _stage = "ingest"
        try:
            with tempfile.TemporaryDirectory(dir=work_dir, prefix="lme-") as td:
                sdk = TortoiseSDK(os.path.join(td, "lme.db"))
                try:
                    # M7 (D5): ingest is timed in isolation — the write-path
                    # cost is a report component (extractor vs retrieve vs
                    # reader vs judge attribution).
                    t_ingest = time.monotonic()
                    if ingest_mode == "v2":
                        from .ingest_v2 import ingest_haystack_v2
                        ingest_stats = ingest_haystack_v2(
                            sdk, question, extractor_model,
                            chunk_turns=chunk_turns)
                    else:
                        ingest_stats = ingest_haystack(
                            sdk, question, chunk_turns=chunk_turns)
                    ingest_latency_ms = round(
                        (time.monotonic() - t_ingest) * 1000.0, 2)
                    # M7 (D3): the authoritative live graph pool size — the
                    # retrieval-pool denominator the methodology documents
                    # (single Cypher, no N+1).
                    pool_rows = sdk._get_proj().g.query(
                        "MATCH (p:Point {lme_question_id:$q}) RETURN count(*)",
                        params={"q": qid}).result_set
                    pool_size = pool_rows[0][0] if pool_rows else 0
                    ret = retrieve_for_question(
                        sdk, question, ks=ks, top_k=top_k,
                        max_context_tokens=max_context_tokens,
                        max_chunks_per_session=max_chunks_per_session,
                        tr_top_k=tr_top_k,
                        tr_date_weight=tr_date_weight,
                        tr_events=tr_events)

                    _stage = "reader"
                    t0 = time.monotonic()
                    # A1 #1546 invariant: the reader receives question_type
                    # ONLY. The _abs marker (question_id suffix) must never
                    # cross — abstention is derived by the reader from the
                    # evidence (rendered hits + dates), via the universal
                    # partial-knowledge clause in system_prompt_for.
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

                    _stage = "judge"
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

            # M7 (D4): evidence written = the ingest leg's own count
            # (deterministic → evidence_turns; v2 → evidence_points);
            # error_classes = ingest-stage classes + (later) the failure class.
            ingest_errors = ingest_stats.get("errors") or []
            evidence_written = (
                ingest_stats.get("evidence_points", 0) if ingest_mode == "v2"
                else ingest_stats.get("evidence_turns", 0))
            outcome = {
                "question_id": qid,
                "question_type": question.get("question_type", ""),
                "question_date": question.get("question_date", ""),
                "label": label,
                "hypothesis": hypothesis,
                "ingest": ingest_stats,
                "n_ingest_errors": len(ingest_errors),
                "ingest_error_text": (ingest_errors or [None])[0],
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
                # M7 (#1527, D1–D5): per-question validity + instrumentation
                # (all persisted in the Layer-1 outcomes projection). M4
                # (#1524, D4): ``error_classes`` is the ingest CENSUS dict
                # (class → count, granular extractor vocabulary) — not a flat
                # per-error list — so the run-level census rolls exact counts.
                # ``valid`` is extraction integrity only (S15): reader/judge
                # failures stay in the top-level ``failures`` list.
                "valid": len(ingest_errors) == 0,
                "error_classes": ingest_stats.get("error_census", {}),
                "leg_mix": ret["match_source_counts"],
                "leg_mix@k": ret["match_source_counts@k"],
                "pool_size": pool_size,
                "evidence_written": evidence_written,
                "evidence_retrieved@k": ret["evidence_retrieved@k"],
                "ingest_latency_ms": ingest_latency_ms,
                # R3 (#1542) D3/D4: write-time embedding coverage + the
                # per-leg trace (vector/fts/structural/fallback — E2E-1
                # never-null leg-mix, recorded per question).
                "points_total": ret["points_total"],
                "points_embedded": ret["points_embedded"],
                "embedding_coverage": ret["embedding_coverage"],
                "legs": ret["legs"],
                # R5 (#1544): the TR-constraint surface per question — the
                # detected kind (TR only) + whether the window filter fell
                # back to the unfiltered pool (never starve the reader).
                "tr_constraint": ret.get("tr_constraint"),
                "tr_window_fallback": ret.get("tr_window_fallback", False),
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
                    # M7 (D6): the P2-aligned eval error class, site-prefixed
                    # (reader:retries_exhausted / judge:fatal / ingest:…).
                    "error_class": eval_failure_class(e, site=_stage),
                    "failed_at_utc": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
                })
        with _lock:
            _save_checkpoint(checkpoint, list(done.values()), failures,
                             fingerprint)

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
        embedder_status=embedder_status,
        # R1 (#1540) D7: knob values recorded verbatim in the methodology
        # (the run protocol step-2 gate consumes them).
        r1_knobs={
            "chunk_turns": chunk_turns,
            "context_token_cap": max_context_tokens,
            "max_chunks_per_session": max_chunks_per_session,
        },
        # R5 (#1544) D7: TR knob values recorded verbatim in the
        # methodology (the run protocol step-2/6 knob sweeps consume them;
        # tr_top_k and R1's context cap are complementary flood controls).
        r5_knobs={
            "tr_top_k": tr_top_k,
            "tr_date_weight": tr_date_weight,
            "tr_events": tr_events,
        },
        # M7 (#1527): publication-gated audit + run-hygiene provenance.
        dataset_semantics_audit=dataset_semantics_audit,
        integrity_threshold=integrity_threshold,
        integrity_justification=integrity_justification,
        python_version=f"{sys.version_info[0]}.{sys.version_info[1]}."
              f"{sys.version_info[2]}",
        workers=workers,
        dataset_fingerprint=dataset_fingerprint,
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
    # R3 (#1542) D5: forwarded to build_report — the dense-leg methodology
    # keys are always emitted (not_checked default when omitted).
    embedder_status: dict | None = None,
    r1_knobs: dict[str, Any] | None = None,
    # R5 (#1544) D7: the TR knob values (tr_top_k / tr_date_weight /
    # tr_events) recorded in the report methodology — same pattern as
    # ``r1_knobs``.
    r5_knobs: dict[str, Any] | None = None,
    # M7 (#1527): publication-gated audit + run-hygiene provenance.
    dataset_semantics_audit: dict[str, Any] | None = None,
    integrity_threshold: float = 0.0,
    integrity_justification: str | None = None,
    python_version: str = "",
    workers: int = 1,
    dataset_fingerprint: str = "unknown",
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
                # M8 (#1528, D6): the live graph point count rides the
                # projection — the flip-list zero-point flag consumes it.
                "context_point_count",
                "retrieval_latency_ms", "reader_latency_ms",
                "judge_latency_ms", "total_ms",
                # M7 (#1527, D11): the Layer-1 payload projection — validity,
                # error classes, leg-mix, pool size, evidence written/
                # retrieved, isolated ingest cost.
                "valid", "error_classes", "leg_mix", "leg_mix@k",
                "pool_size", "evidence_written", "evidence_retrieved@k",
                "ingest_latency_ms",
                # R3 (#1542) D3/D4: dense-leg observability — read via
                # o.get so a pre-R3 checkpoint resumes with the defaults
                # (coverage keys → None, legs → []) instead of KeyError.
                "points_total", "points_embedded", "embedding_coverage",
                # R5 (#1544): TR-constraint surface — read via o.get so a
                # pre-R5 checkpoint resumes with the defaults (None/False).
                "tr_constraint",
            )} | {"legs": list(o.get("legs") or []),
                  # False default: a pre-R5 checkpoint had no TR path — no
                  # filter ran, so the fallback flag is honestly False.
                  "tr_window_fallback": bool(o.get("tr_window_fallback", False))}
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
        r5_knobs=r5_knobs,
        embedder_status=embedder_status,
        extra=extra,
        # M7 (#1527): publication-gated audit + run-hygiene provenance.
        dataset_semantics_audit=dataset_semantics_audit,
        integrity_threshold=integrity_threshold,
        integrity_justification=integrity_justification,
        python_version=python_version,
        workers=workers,
        dataset_fingerprint=dataset_fingerprint,
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
        "context_token_cap tokens; type-fragments: temporal (date math), "
        "preference (option commitment), knowledge-update (answer-from-newer, "
        "date-conditional: current-value → newest/superseding point, "
        "point-in-time → chain-walk by session date — E5 CORRECTS markers + "
        "session-date annotations, no parallel mechanism), multi-session "
        "(aggregation: distinct events, no double-count, reconcile by date)"
    )

def _print_summary(report: dict[str, Any]) -> None:
    # M4/M7 (#1527): the integrity block + error census print BEFORE the
    # score — a run's validity is asserted before its numbers are read.
    integ = report.get("integrity") or {}
    print("\n" + "=" * 64)
    print(f"LongMemEval {report['split']} — {report['n_questions']} questions")
    print("── integrity ──")
    print(f"valid: {integ.get('valid')}  (threshold {integ.get('threshold')}; "
          f"n_attempted {integ.get('n_attempted')}, n_valid "
          f"{integ.get('n_valid')}, n_invalid {integ.get('n_invalid')}, "
          f"n_failed {integ.get('n_failed')}, "
          f"invalid_rate {integ.get('invalid_rate')})")
    if integ.get("justified"):
        print(f"  justified override: "
              f"{integ.get('threshold_violation_justification')}")
    census = integ.get("error_census") or {}
    if census:
        print("error census:")
        for cls, count in census.items():
            print(f"  {cls:<28} {count}")
    else:
        print("error census: no errors")
    for c in integ.get("checks") or []:
        print(f"  check: {c}")
    print("── score ──")
    acc = report["accuracy"]
    print(f"overall accuracy:        {acc['overall']}")
    print(f"task-averaged accuracy:  {acc['task_averaged']}")
    print(f"abstention accuracy:     {acc['abstention']} "
          f"(n={acc['abstention_n']})")
    for cat, v in acc["per_category"].items():
        print(f"  {cat:<28} {v['accuracy']} (n={v['n']})")
    ret = report["retrieval"]
    if ret.get("session_recall@k") is None:
        print("retrieval recall: NOT PUBLISHED — dataset semantics audit "
              "verdict not-trusted (see methodology.dataset_semantics_audit)")
    else:
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
    print(f"latency (ms) retrieval/reader/judge/ingest/total:")  # noqa: F541
    for key in ("retrieval", "reader", "judge", "ingest",
                "total_per_question"):
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
    p.add_argument("--tr-top-k", type=int, default=DEFAULT_TR_TOP_K,
                   help="TR-questions context cap (default 12 — the 20→12 "
                        "transcript-flood control, R5 #1544)")
    p.add_argument("--tr-date-weight", type=float, default=0.5,
                   help="TR-questions recency date weight (default 0.5 — the "
                        "RRF recency multiplier; 0.0 disables, R5 #1544)")
    p.add_argument("--no-tr-events", action="store_true",
                   help="exclude the events timeline from the TR retrieval "
                        "pool (default: events included, R5 #1544)")
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
    p.add_argument("--integrity-threshold", type=float, default=0.0,
                   help="max allowed invalid_rate for integrity.valid (default "
                        "0.0 — any failed question or ingest-error question "
                        "marks the run invalid). An override records "
                        "integrity.justified; the report always records the "
                        "numbers + the reason, so a violated override still "
                        "yields valid=false (E2E-2, M7 #1527 D1)")
    p.add_argument("--integrity-justification", default=None,
                   help="free text recorded with a non-default "
                        "--integrity-threshold (integrity."
                        "threshold_violation_justification)")
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
    # M8 (#1528, D6): the capstone's report-comparison command — no dataset
    # load, no API keys, no run environment needed.
    p.add_argument("--compare", nargs=2, metavar=("REPORT_A", "REPORT_B"),
                   default=None,
                   help="compare two report JSONs (A = baseline/older, "
                        "B = newer): shared-qid deltas are primary, exact "
                        "McNemar + Wilson 95%% CIs per category, per-category "
                        "flip lists, comparability warnings + caveats — no "
                        "run needed")
    p.add_argument("--compare-out", default=None,
                   help="path to write the comparison JSON with --compare "
                        "(default: stdout only)")
    return p


def _assert_python_version() -> None:
    """Refuse a <3.12 eval env fast (M7 #1527, D9). Factored for unit
    testing (monkeypatch sys.version_info). The guard runs BEFORE dataset
    load / key checks — pyproject requires-python >=3.12 and the eval graph
    write path are 3.12-only."""
    if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
        # (pyproject requires-python already refuses <3.12 at install time;
        # this refuses a 3.11 env reached via PYTHONPATH / --ignore-requires)
        raise SystemExit(
            f"longmem_eval requires Python >= 3.12 (got "
            f"{sys.version_info[0]}.{sys.version_info[1]}) — pyproject "
            "requires-python >=3.12; the eval graph write path is 3.12-only")


def run_main(argv: list[str] | None = None) -> dict[str, Any]:
    _assert_python_version()
    args = _build_parser().parse_args(argv)
    # M8 (#1528, D6): --compare is a pure artifact command — handled before
    # ANY run machinery (no dataset load, no embedder pre-flight, no keys).
    if args.compare:
        a_path, b_path = args.compare
        cmp = compare_reports(
            json.loads(Path(a_path).read_text(encoding="utf-8")),
            json.loads(Path(b_path).read_text(encoding="utf-8")),
        )
        print_comparison(cmp)
        if args.compare_out:
            out = Path(args.compare_out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(cmp, indent=2, sort_keys=True)
                           + "\n", encoding="utf-8")
            print(f"\ncomparison saved to: {out}")
        return cmp
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
    # R5 (#1544) TR knobs: argparse defaults (12 / 0.5 / events-on),
    # recorded verbatim in the report methodology (D7).
    tr_top_k = args.tr_top_k
    tr_date_weight = args.tr_date_weight
    tr_events = not args.no_tr_events

    # R3 (#1542) D2: embedder pre-flight — before dataset load (fail before
    # the ~tens-of-MB download). Real runs refuse to start when the dense
    # leg can't run; --mock warns and continues. The status flows into the
    # report methodology (D5: embedder + vector_strategy always emitted).
    embedder_status = _preflight_embedder(mock=args.mock)

    instances = ds.load_dataset(
        args.split, limit=args.limit, data_path=args.data,
        cache=Path(args.cache_dir).expanduser() if args.cache_dir else None,
        download=not args.no_download,
    )

    # M7 (D7): the dataset fingerprint hashes the RESOLVED dataset file (the
    # checkpoint's dataset_fingerprint must match on resume).
    if args.data:
        dataset_file = Path(args.data)
    else:
        cache_base = (Path(args.cache_dir).expanduser() if args.cache_dir
                      else ds.cache_dir())
        dataset_file = cache_base / ds.SPLIT_FILES[args.split]
    dataset_fingerprint = _dataset_fingerprint(dataset_file)

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
            embedder_status=embedder_status,
            chunk_turns=chunk_turns, max_context_tokens=context_cap,
            max_chunks_per_session=max_chunks_per_session,
            tr_top_k=tr_top_k, tr_date_weight=tr_date_weight,
            tr_events=tr_events,
            dataset_fingerprint=dataset_fingerprint,
            integrity_threshold=args.integrity_threshold,
            integrity_justification=args.integrity_justification,
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
