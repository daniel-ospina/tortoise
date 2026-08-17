"""LongMemEval-S external comparability runner — CLI + programmatic entry.

    python -m tools.longmem_eval.run --split s [--limit N] [--mock] [...]
    python -m tools.longmem_eval.run --db docker://... --retriever vector \
        --model arctic-s --spot-check        # HNSW winner-vs-control producer

Pipeline per question (fresh isolated graph per question — the benchmark's
independent-memory protocol, no cross-question contamination):
    ingest haystack sessions → retrieval (hybrid RRF or vector arm) →
    reader LLM answers from context → official answer-check judge scores.

Retrieval arms (``--retriever``):
  * ``hybrid`` (default) — the legacy ``tortoise_fts_query`` RRF path
    (backward-compatible; degraded TF-IDF in embedded mode).
  * ``vector`` (#1349 gate arm) — ``run_vector_query`` ONLY, query encoded by
    the injected model (``--model`` → ``tools.embedder_probe.inject_model``).
    Emits turn_recall@10 + nDCG@10 (primary co-metrics) + P@10/P@5. HARD-FAIL
    on encode-degrade (MODEL_ENCODE_FAILED, exit 4) and breaker-open
    questions are routed through dropped-question accounting — never recall 0.

FalkorDB mode (``--db docker://...``): replaces the per-question tempdir
embedded db with a FalkorDB connection (HNSW ``queryNodes`` branch). Per-RUN
graph isolation is CRITICAL — the HNSW index is global per graph, so each
(question, model-run) gets its own graph name (``question_graph_namespace``);
otherwise a winner-vs-control spot-check's second run finds ids already
present and silently reuses the first model's vectors.

``--spot-check``: named reproducible producer — runs ``--model`` (winner) AND
control (minilm) in one pass over the full question set, emitting ONE paired
artifact at ``docs/research/2026-08-17-1349-embedder-selection/
hnsw-spotcheck-{winner}.json`` (``{cleared, n, metric_deltas}``) for
gate_1349.py's "HNSW artifact present+cleared" check.

Checkpoints: per-model keying ``{surface}__{retriever}__{model}__{prompt}``
(surface ∈ embedded|hnsw — a --db run never resumes against embedded-mode
brute-force checkpoints), versioned format (stale #1144-era checkpoints are
unreadable, not misread), atomic temp-file-then-rename writes. Resume against
a truncated/corrupt record re-encodes just that question with a warning —
never crash, never silently drop from the denominator.

Encode cache: model-keyed (``sha256(model_id + prompt_name + text)``),
disk-persisted, namespaced per (model, prompt) — the cross-question haystack
redundancy (5-10× redundant encodes) that makes the burn feasible. Active
when ``--model`` is set (the burn config path).

Concurrency model: SEQUENTIAL workers (one question at a time) — the simplest
correct choice: per-question isolated graphs + the shared encode cache make
parallelism a coordination cost with no correctness benefit; the cache file is
therefore single-writer.

Resilience (per-question error isolation): each question is wrapped in its
own try/except — a transient LLM error is retried with exponential backoff
(--max-retries) and a question that still fails is recorded in the report's
``failures`` list while the run continues (one bad question never aborts the
whole 500-Q run). ``--checkpoint <state.json>`` checkpoints completed +
failed questions after every question; re-running with the same file resumes
(skips completed/failed, continues the rest).

Run modes:
    --mock        fully offline (MockReader + MockJudge; CI smoke, no keys)
    --retrieval-only  skips reader/judge entirely — same retrieval output as
                      --mock but structurally immune to reader/judge
                      contamination (report accuracy is None with a note)
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
import math
import os
import random
import re
import sys
import tempfile
import time
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tortoise.sdk import TortoiseSDK

from ..embedder_probe import (
    DEFAULT_MODEL_ID, PROBE_MODELS, inject_model,
)
from . import dataset as ds
from . import encode_cache
from .ingest import ingest_haystack
from .judge import build_judge, is_abstention
from .reader import build_reader
from .report import build_report, default_report_path, git_sha, save_report
from .retrieve import (
    MODEL_ENCODE_FAILED_EXIT, ModelEncodeFailedError, VectorBreakerOpenError,
    retrieve_for_question,
)

DEFAULT_KS = (5, 10, 20)
DEFAULT_TOP_K = 20
DEFAULT_MAX_RETRIES = 3
BACKOFF_BASE_S = 2.0
BACKOFF_CAP_S = 30.0

#: Checkpoint file format marker — bumped from the #1144-era v1 shape so stale
#: checkpoints are unreadable, not misread.
CHECKPOINT_FORMAT = "lme-checkpoint-v2"

#: Per-retriever required outcome keys (resume validation — a truncated record
#: is re-encoded, never silently dropped from the denominator).
REQUIRED_OUTCOME_KEYS: dict[str, tuple[str, ...]] = {
    "hybrid": ("question_id", "session_recall@k", "turn_recall@k"),
    "vector": ("question_id", "session_recall@k", "turn_recall@k",
               "ndcg@10", "p@10", "p@5", "ranked_ids"),
}

#: Pinned artifact path for the HNSW spot-check (gate_1349.py consumes it).
SPOTCHECK_ARTIFACT_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "docs" / "research" / "2026-08-17-1349-embedder-selection"
)

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


@contextmanager
def _temporary_env_var(name: str, value: str):
    """Set ``name`` to ``value`` for the duration of the block; restore on exit.

    An explicit ``--db`` must WIN over a stale pre-existing env URI (a stale
    ``TORTOISE_DB_URI`` in the caller's shell would otherwise silently
    redirect the spot-check to the wrong FalkorDB server — the reference
    runner's setdefault semantics are NOT used here because the spot-check
    is a deterministic evidence producer). The env is always restored so
    ``run_main()`` can be invoked repeatedly in one process without leaking
    the URI into later no-path SDK constructions (issue #1349 isolation).
    """
    prev = os.environ.get(name)
    os.environ[name] = value
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = prev


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


# ── per-model checkpoint keying + graph isolation ───────────────────────────

def checkpoint_key(surface: str, retriever: str, model: str | None,
                   prompt: str | None) -> str:
    """Checkpoint key ``{surface}__{retriever}__{model}__{prompt}``.

    surface ∈ embedded|hnsw — a ``--db`` HNSW run must NEVER resume against
    embedded-mode brute-force checkpoints (which would emit brute-force
    retrieval as the HNSW artifact and trivially clear GATE (c) on the wrong
    surface). Cross-model resume is impossible by construction.
    """
    return f"{surface}__{retriever}__{model or 'default'}__{prompt or 'default'}"


def question_graph_namespace(model: str, prompt: str | None, qid: str) -> str:
    """Distinct FalkorDB graph per (question, model-run).

    Point-id/label scoping does NOT isolate the HNSW index (it is global
    across the graph, filters only on retracted status). Without a distinct
    graph per (model, qid) a winner-vs-control spot-check's second run finds
    ids already present and silently reuses the first model's vectors
    (control recall ≈ winner recall — a bogus comparison).
    """
    m = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(model or "default"))[:40] or "default"
    p = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(prompt or "default"))[:16] or "default"
    return f"{m}__{p}__{qid}"


def _make_question_sdk(*, db_uri: str | None, namespace: str | None,
                       work_dir: str | None = None) -> tuple[TortoiseSDK, Any]:
    """Per-question SDK construction.

    ``--db`` mode: a FalkorDB connection on the URI's server, scoped to the
    (question, model-run) graph via the SDK namespace (graph name
    ``team_{namespace}``) — HNSW ``queryNodes`` branch reachable
    (``_is_embedded=False``). Embedded mode: a fresh tempdir db (isolation by
    construction). Returns (sdk, cleanup).

    The ``--db`` cleanup restores ``TORTOISE_DB_URI`` to its pre-call value:
    the setdefault below is how the URI reaches the SDK, but mutating the
    process env permanently leaks the URI into every later SDK/validation in
    the same process (issue #1349 test isolation).
    """
    if db_uri:
        # Mirrors tests/eval/retrieval/run.py:549 — the URI becomes the SDK's
        # connection source (setdefault: an explicit env wins, matching the
        # reference runner's semantics).
        prev = os.environ.get("TORTOISE_DB_URI")
        os.environ.setdefault("TORTOISE_DB_URI", db_uri)

        def _cleanup():
            if prev is None:
                os.environ.pop("TORTOISE_DB_URI", None)
            else:
                os.environ["TORTOISE_DB_URI"] = prev

        return TortoiseSDK(namespace=namespace), _cleanup
    td = tempfile.TemporaryDirectory(dir=work_dir, prefix="lme-")
    return TortoiseSDK(os.path.join(td.name, "lme.db")), td.cleanup


# ── checkpoint IO (atomic + keyed + per-outcome validated) ─────────────────

def _load_checkpoint(path: str | None, *, run_key: str,
                     retriever: str = "hybrid") -> tuple[dict[str, dict], list[dict]]:
    """Load (completed-by-qid, failures) from the checkpoint state file.

    Refuses (with a warning — never crash) checkpoints whose format is stale
    (#1144-era), whose ``run_key`` differs (cross-surface/cross-model resume
    is impossible), or that are corrupt. Per-outcome validation: a truncated/
    corrupt record is dropped so the question re-encodes — never silently
    dropped from the denominator.
    """
    if not path:
        return {}, []
    p = Path(path)
    if not p.is_file():
        return {}, []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        print(f"[longmem_eval] WARNING: checkpoint {p} is corrupt ({e!r}) — "
              f"ignoring; every question re-encodes", file=sys.stderr)
        return {}, []
    if data.get("format") != CHECKPOINT_FORMAT:
        print(f"[longmem_eval] WARNING: checkpoint {p} format "
              f"{data.get('format')!r} != {CHECKPOINT_FORMAT!r} (stale "
              f"#1144-era or foreign) — ignoring; every question re-encodes",
              file=sys.stderr)
        return {}, []
    if data.get("run_key") != run_key:
        print(f"[longmem_eval] WARNING: checkpoint {p} belongs to run "
              f"{data.get('run_key')!r}, current run is {run_key!r} — refusing "
              f"cross-config resume (per-model checkpoint keying); every "
              f"question re-encodes", file=sys.stderr)
        return {}, []
    required = REQUIRED_OUTCOME_KEYS.get(retriever, ("question_id",))
    outcomes: dict[str, dict] = {}
    for o in data.get("outcomes", []):
        if not isinstance(o, dict) or not o.get("question_id"):
            continue
        if o.get("breaker_open"):
            outcomes[o["question_id"]] = o  # legitimately dropped — keep
            continue
        missing = [k for k in required if k not in o]
        if missing:
            print(f"[longmem_eval] WARNING: checkpoint outcome "
                  f"{o.get('question_id')!r} truncated/corrupt (missing "
                  f"{missing}) — re-encoding just this question",
                  file=sys.stderr)
            continue
        outcomes[o["question_id"]] = o
    failures = [f for f in data.get("failures", [])
                if isinstance(f, dict) and f.get("question_id")]
    print(f"[longmem_eval] resumed checkpoint {p}: {len(outcomes)} completed, "
          f"{len(failures)} failed (skipping both)", file=sys.stderr)
    return outcomes, failures


def _save_checkpoint(path: str | None, outcomes: list[dict],
                     failures: list[dict], *, run_key: str, surface: str,
                     retriever: str, model: str | None, prompt: str | None) -> None:
    """Atomically persist partial results after each question (resume).

    temp-file-then-rename: a crash mid-write can never leave a truncated
    checkpoint (an ENOSPC on rename surfaces as an OSError from the caller —
    the question is recorded in the run's failures, never silently dropped).
    """
    if not path:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps({
        "format": CHECKPOINT_FORMAT,
        "run_key": run_key,
        "surface": surface,
        "retriever": retriever,
        "model": model or "default",
        "prompt": prompt or "default",
        "outcomes": outcomes,
        "failures": failures,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
    }, indent=2), encoding="utf-8")
    os.replace(tmp, p)


# ── the per-question pipeline ───────────────────────────────────────────────

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
    retriever: str = "hybrid",
    model: str | None = None,
    query_prompt: str | None = None,
    retrieval_only: bool = False,
    db_uri: str | None = None,
    encode_cache: encode_cache.EncodeCache | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run the full per-question pipeline over ``instances``.

    Each question gets a FRESH isolated graph: a tempdir embedded db by
    default, or a distinct (question, model-run) FalkorDB graph under
    ``--db``. Per-question error isolation: a transient LLM/provider error is
    retried with exponential backoff, and a question that still fails is
    recorded in ``report['failures']`` and the run CONTINUES. A
    :class:`ModelEncodeFailedError` (vector arm encode-degrade) ABORTS the
    run — empty recall is never reported as a result. A breaker-open question
    is marked ``breaker_open`` and routed through the report's dropped
    accounting (excluded from means, count surfaced — never recall 0, never
    silently excluded). Partial results are checkpointed after every question
    under the per-model key ``{surface}__{retriever}__{model}__{prompt}``.

    Concurrency model: sequential workers (documented in the module docstring).

    Returns (completed-outcomes, report-dict built from them).
    """
    if retriever not in ("hybrid", "vector"):
        raise ValueError(f"retriever must be 'hybrid' or 'vector', got {retriever!r}")
    surface = "hnsw" if db_uri else "embedded"
    run_key = checkpoint_key(surface, retriever, model, query_prompt)
    done, prior_failures = _load_checkpoint(checkpoint, run_key=run_key,
                                            retriever=retriever)
    outcomes: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = list(prior_failures)

    cache_cm = encode_cache.active() if encode_cache is not None else nullcontext()
    with cache_cm:
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
                sdk, cleanup = _make_question_sdk(
                    db_uri=db_uri,
                    namespace=question_graph_namespace(
                        model or "default", query_prompt, qid),
                    work_dir=work_dir)
                try:
                    ingest_stats = ingest_haystack(sdk, question)
                    ret = retrieve_for_question(sdk, question, ks=ks,
                                                top_k=top_k, retriever=retriever)
                    if not retrieval_only:
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
                    else:
                        hypothesis, label = None, None
                        reader_ms = judge_ms = 0.0
                finally:
                    sdk.close()
                    cleanup()

                outcome: dict[str, Any] = {
                    "question_id": qid,
                    "question_type": question.get("question_type", ""),
                    "question_date": question.get("question_date", ""),
                    "label": label,
                    "hypothesis": hypothesis,
                    "ingest": ingest_stats,
                    "retriever": retriever,
                    "session_recall@k": ret["session_recall@k"],
                    "turn_recall@k": ret["turn_recall@k"],
                    "context_tokens": ret["context_tokens"],
                    "context_point_count": ret["context_point_count"],
                    "retrieval_latency_ms": ret["retrieval_latency_ms"],
                    "reader_latency_ms": round(reader_ms, 2),
                    "judge_latency_ms": round(judge_ms, 2),
                    "total_ms": round((time.monotonic() - t_q_start) * 1000.0, 2),
                }
                if retriever == "vector":
                    outcome.update({
                        "ndcg@10": ret["ndcg@10"],
                        "p@10": ret["p@10"],
                        "p@5": ret["p@5"],
                        "ranked_ids": ret["ranked_ids"],
                        "evidence_turn_matches": ret["evidence_turn_matches"],
                    })
                outcomes.append(outcome)
                done[qid] = outcome
            except ModelEncodeFailedError:
                # vector-arm encode-degrade: abort the config run with the
                # distinct exit code — empty recall is indistinguishable from
                # a legit no-hit and must never be reported as a result.
                _save_checkpoint(checkpoint, list(done.values()), failures,
                                 run_key=run_key, surface=surface,
                                 retriever=retriever, model=model, prompt=query_prompt)
                raise
            except VectorBreakerOpenError as e:
                print(f"[longmem_eval] question {qid} DROPPED (breaker open, "
                      f"surfaced via dropped accounting — excluded from means, "
                      f"count recorded): {e}", file=sys.stderr)
                outcome = {
                    "question_id": qid,
                    "question_type": question.get("question_type", ""),
                    "question_date": question.get("question_date", ""),
                    "label": None,
                    "hypothesis": None,
                    "retriever": retriever,
                    "breaker_open": True,
                    "dropped_reason": "breaker_open",
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
            _save_checkpoint(checkpoint, list(done.values()), failures,
                             run_key=run_key, surface=surface,
                             retriever=retriever, model=model, prompt=query_prompt)

    reader_model = reader.model_id if reader is not None else "n/a (retrieval-only)"
    judge_model = judge.model_id if judge is not None else "n/a (retrieval-only)"
    return outcomes, outcomes_to_report(
        outcomes,
        reader_model=reader_model,
        judge_model=judge_model,
        ks=ks,
        top_k=top_k,
        split=split,
        failures=failures,
        retriever=retriever,
        model=model,
        query_prompt=query_prompt,
        retrieval_only=retrieval_only,
        surface=surface,
        run_key=run_key,
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
    failures: list[dict[str, Any]] | None = None,
    retriever: str = "hybrid",
    model: str | None = None,
    query_prompt: str | None = None,
    retrieval_only: bool = False,
    surface: str = "embedded",
    run_key: str | None = None,
) -> dict[str, Any]:
    """Aggregate outcomes (programmatic entry used by tests too)."""
    extra_outcomes = []
    for o in outcomes:
        slim = {k: o.get(k) for k in (
            "question_id", "question_type", "question_date", "label",
            "hypothesis", "session_recall@k", "turn_recall@k",
            "context_tokens", "retrieval_latency_ms", "reader_latency_ms",
            "judge_latency_ms", "total_ms", "ndcg@10", "p@10", "p@5",
            "ranked_ids", "evidence_turn_matches", "retriever",
            "breaker_open", "dropped_reason",
        )}
        extra_outcomes.append(slim)
    return build_report(
        outcomes,
        dataset_id=dataset_id,
        split=split,
        reader_model=reader_model,
        judge_model=judge_model,
        extraction_approach=EXTRACTION_APPROACH,
        ks=ks,
        top_k=top_k,
        failures=failures,
        retriever=retriever,
        model=model,
        query_prompt=query_prompt,
        retrieval_only=retrieval_only,
        surface=surface,
        run_key=run_key,
        extra={"outcomes": extra_outcomes},
    )


# ── spot-check artifact (HNSW winner-vs-control producer) ──────────────────

def spotcheck_artifact_path(winner: str) -> Path:
    """Pinned artifact path — gate_1349.py's "HNSW artifact present+cleared"
    check reads exactly this file."""
    return SPOTCHECK_ARTIFACT_DIR / f"hnsw-spotcheck-{winner}.json"


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _delta_summary(deltas: list[float]) -> dict[str, Any]:
    """One-sided paired normal-approximation summary of winner−control deltas.

    The exact bootstrap p is recomputed by gate_1349.py from the per-question
    ``deltas`` — this is the artifact's T2-level directional read (m=2 bar:
    BH q=0.10 → the smallest p must be ≤ 0.05, z ≈ 1.645).
    """
    n = len(deltas)
    base = {"n": n, "deltas": list(deltas)}
    if n == 0:
        return {"mean_delta": 0.0, "one_sided_p": None, **base}
    mean = sum(deltas) / n
    if n == 1:
        return {"mean_delta": round(mean, 6), "one_sided_p": None, **base}
    var = sum((d - mean) ** 2 for d in deltas) / (n - 1)
    std = math.sqrt(var)
    if std == 0.0:
        p = 0.0 if mean > 0.0 else 1.0  # deterministic sign
    else:
        z = mean / (std / math.sqrt(n))
        p = 1.0 - _normal_cdf(z)
    return {"mean_delta": round(mean, 6), "one_sided_p": round(p, 6), **base}


def _build_spotcheck_artifact(winner: str, control: str,
                              results: dict[str, dict[str, dict]],
                              ks: tuple[int, ...]) -> dict[str, Any]:
    """Paired winner-vs-control artifact shape: {cleared, n, metric_deltas}."""
    k = "10" if "10" in (str(x) for x in ks) else str(ks[-1])
    qids = sorted(set(results[winner]) & set(results[control]))
    metric_deltas: dict[str, Any] = {}
    for metric in ("turn_recall@10", "ndcg@10"):
        deltas: list[float] = []
        for qid in qids:
            w, c = results[winner][qid], results[control][qid]
            if w.get("breaker_open") or c.get("breaker_open"):
                continue  # dropped questions are excluded from the paired set
            if metric == "turn_recall@10":
                dw = w.get("turn_recall@k", {}).get(k, 0.0)
                dc = c.get("turn_recall@k", {}).get(k, 0.0)
            else:
                dw = w.get("ndcg@10", 0.0)
                dc = c.get("ndcg@10", 0.0)
            deltas.append(float(dw) - float(dc))
        metric_deltas[metric] = _delta_summary(deltas)
    cleared = any(
        metric_deltas[m].get("one_sided_p") is not None
        and metric_deltas[m]["one_sided_p"] <= 0.05
        for m in metric_deltas
    )
    return {
        "producer": "tools/longmem_eval/run.py --spot-check",
        "surface": "hnsw",
        "retriever": "vector",
        "winner": winner,
        "control": control,
        "n": len(qids),
        "metric_deltas": metric_deltas,
        "cleared": cleared,
        "rule": ("one-sided paired normal-approximation z-test per co-primary "
                 "metric over the FULL question set (n recorded; a post-hoc n "
                 "that shrinks until p<0.10 is forbidden); BH q=0.10 over m=2 "
                 "→ cleared iff min one-sided p ≤ 0.05. gate_1349.py "
                 "recomputes the exact bootstrap p from the per-question "
                 "deltas."),
        "checkpoint_key_prefix": "hnsw__vector__{model}__{prompt}",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_sha": git_sha(),
    }


def _run_spot_check(args, instances: list[dict], *, ks, top_k, db_uri) -> dict:
    """Winner AND control in one pass → one paired artifact (documented shape)."""
    winner = args.model
    control = "minilm"
    cache_root = Path(args.cache_dir).expanduser() if args.cache_dir else ds.cache_dir()
    results: dict[str, dict[str, dict]] = {}
    for model in (winner, control):
        state = inject_model(model, query_prompt=args.query_prompt)
        model_id = state.get("hf_id") or PROBE_MODELS.get(model) or DEFAULT_MODEL_ID
        cache = encode_cache.EncodeCache(
            encode_cache.cache_path_for(cache_root, model, args.query_prompt),
            model_id=model_id, prompt_name=args.query_prompt)
        outcomes, _report = run_evaluation(
            instances, reader=None, judge=None, ks=ks, top_k=top_k,
            work_dir=args.work_dir, split=args.split,
            retriever="vector", model=model, query_prompt=args.query_prompt,
            retrieval_only=True, db_uri=db_uri, encode_cache=cache,
        )
        results[model] = {o["question_id"]: o for o in outcomes}
        print(f"[longmem_eval] spot-check pass complete: model={model} "
              f"questions={len(outcomes)}", file=sys.stderr)
    artifact = _build_spotcheck_artifact(winner, control, results, ks=ks)
    path = spotcheck_artifact_path(winner)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    print(f"[longmem_eval] HNSW spot-check artifact written: {path}",
          file=sys.stderr)
    return artifact


# ── summary printing ────────────────────────────────────────────────────────

def _print_summary(report: dict[str, Any]) -> None:
    ret = report["retrieval"]
    print("\n" + "=" * 64)
    print(f"LongMemEval {report['split']} — {report['n_questions']} questions "
          f"(retriever={report['methodology']['retriever']})")
    acc = report["accuracy"]
    if acc is None:
        print("accuracy: not computed (--retrieval-only run — no reader/judge; "
              "no bogus accuracy from unset labels)")
    else:
        print(f"overall accuracy:        {acc['overall']}")
        print(f"task-averaged accuracy:  {acc['task_averaged']}")
        print(f"abstention accuracy:     {acc['abstention']} "
              f"(n={acc['abstention_n']})")
        for cat, v in acc["per_category"].items():
            print(f"  {cat:<28} {v['accuracy']} (n={v['n']})")
    print("retrieval recall@k (session / turn):")
    for k, v in ret["session_recall@k"].items():
        print(f"  k={k:<3} session {v}   turn {ret['turn_recall@k'][k]}")
    if "ndcg@10" in ret:
        print(f"nDCG@10: {ret['ndcg@10']}   P@10: {ret['p@10']}   "
              f"P@5: {ret['p@5']}")
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
    if report.get("n_dropped", 0):
        print(f"dropped: {report['n_dropped']} question(s) excluded from means "
              f"(breaker_open={report['dropped']['breaker_open']}) — count "
              f"surfaced, see report['dropped']")
    print("=" * 64)


# ── CLI ─────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m tools.longmem_eval.run",
        description="LongMemEval-S external comparability runner (issue #1144, "
                    "axis 2; #1349 vector arm + HNSW mode): ingest haystacks → "
                    "hybrid/vector retrieval → reader LLM → official GPT-4o "
                    "judge, with full methodology provenance.")
    p.add_argument("--split", default=ds.DEFAULT_SPLIT,
                   choices=sorted(ds.SPLIT_FILES), help="dataset split (default s)")
    p.add_argument("--limit", type=int, default=None,
                   help="run only the first N questions (smoke; default: full split)")
    p.add_argument("--data", default=None,
                   help="local dataset JSON/JSONL path (skips download)")
    p.add_argument("--cache-dir", default=None,
                   help="dataset + encode-cache root (default TORTOISE_LME_CACHE_DIR "
                        "or ~/.cache/tortoise-longmemeval)")
    p.add_argument("--work-dir", default=None,
                   help="temp dir for per-question graphs (default system tmp)")
    p.add_argument("--k", default=",".join(map(str, DEFAULT_KS)),
                   help="comma-separated recall@k values (default 5,10,20)")
    p.add_argument("--top-k", type=int, default=DEFAULT_TOP_K,
                   help="context points handed to the reader (default 20)")
    p.add_argument("--mock", action="store_true",
                   help="offline mode: MockReader + MockJudge, no API keys (CI)")
    p.add_argument("--retriever", default="hybrid", choices=("hybrid", "vector"),
                   help="retrieval arm: hybrid (legacy RRF, default — "
                        "backward-compatible) or vector (#1349 gate arm — "
                        "run_vector_query ONLY, never tortoise_fts_query)")
    p.add_argument("--model", default=None,
                   help="probe model short name "
                        f"{sorted(PROBE_MODELS)} — injects BEFORE ingest and "
                        "query encoding (tools.embedder_probe.inject_model)")
    p.add_argument("--query-prompt", default=None,
                   help="named prompt template threaded to the injected model "
                        "(e.g. 'query' for the snowflake-arctic vendor config)")
    p.add_argument("--retrieval-only", action="store_true",
                   help="skip reader/judge entirely — same retrieval output as "
                        "--mock but structurally immune to reader/judge "
                        "contamination (report accuracy is None with a note)")
    p.add_argument("--db", default=None,
                   help="FalkorDB URI (docker://|redis://|rediss://|bolt://) — "
                        "replaces the per-question embedded db (HNSW branch); "
                        "distinct graph per (question, model-run). Default: "
                        "$TORTOISE_DB_URI")
    p.add_argument("--spot-check", action="store_true",
                   help="HNSW spot-check producer: runs --model (winner) AND "
                        "control (minilm) in one pass over the full question "
                        "set, emitting ONE paired artifact at "
                        "docs/research/2026-08-17-1349-embedder-selection/"
                        "hnsw-spotcheck-{winner}.json for gate_1349.py")
    p.add_argument("--checkpoint", default=None,
                   help="partial-results state file (JSON) for error isolation "
                        "+ resume: completed/failed questions are checkpointed "
                        "after every question and skipped on re-run; keyed "
                        "per {surface}__{retriever}__{model}__{prompt}")
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
    parser = _build_parser()
    args = parser.parse_args(argv)

    # --db: FalkorDB URI handling mirroring tests/eval/retrieval/run.py:549
    # (URI → TORTOISE_DB_URI → TortoiseSDK()). Non-URI --db is rejected —
    # the per-question graph isolation derives from the URI server.
    db_uri = args.db
    if db_uri is None and os.environ.get("TORTOISE_DB_URI"):
        db_uri = os.environ["TORTOISE_DB_URI"]
    if db_uri is not None:
        if "://" not in db_uri:
            parser.error(
                f"--db must be a FalkorDB URI (docker://|redis://|rediss://|"
                f"bolt://), got {db_uri!r} — the per-question isolated graphs "
                f"derive from the URI's server")
        # Test isolation (#1349): the URI must reach the SDK via the env, but
        # the process env is restored on exit — a leaked TORTOISE_DB_URI would
        # silently change every later run_main()/SDK in the same process.
        with _temporary_env_var("TORTOISE_DB_URI", db_uri):
            return _run_main(parser, args, db_uri)
    return _run_main(parser, args, db_uri)


def _run_main(parser: argparse.ArgumentParser, args,
              db_uri: str | None) -> dict[str, Any]:
    """Body of :func:`run_main` (split so the caller can scope the
    TORTOISE_DB_URI env to the run — issue #1349 test isolation)."""
    ks = _parse_ks(args.k)
    top_k = args.top_k

    instances = ds.load_dataset(
        args.split, limit=args.limit, data_path=args.data,
        cache=Path(args.cache_dir).expanduser() if args.cache_dir else None,
        download=not args.no_download,
    )

    if args.model is not None and args.model not in PROBE_MODELS:
        parser.error(f"unknown probe model {args.model!r} — known: "
                     f"{sorted(PROBE_MODELS)}")

    # ── HNSW spot-check: winner + control in one pass, ONE paired artifact ──
    if args.spot_check:
        if db_uri is None:
            parser.error("--spot-check requires --db (the HNSW production "
                         "surface — the spot-check must never run on "
                         "embedded brute-force)")
        if not args.model:
            parser.error("--spot-check requires --model <winner>")
        if args.model == "minilm":
            # winner == control → every metric delta is 0 by construction; the
            # paired artifact would be meaningless. Rejected at the gate.
            parser.error("--spot-check requires a non-control winner model")
        if args.retriever != "vector":
            parser.error("--spot-check is a vector-arm producer — pass "
                         "--retriever vector")
        return _run_spot_check(args, instances, ks=ks, top_k=top_k, db_uri=db_uri)

    reader = build_reader(args.reader_model, mock=args.mock) if not args.retrieval_only else None
    judge = build_judge(args.judge_model, mock=args.mock) if not args.retrieval_only else None

    # --model: inject BEFORE ingest and query encoding (the singleton is the
    # candidate); the encode cache is active for model runs (burn path).
    encode_cache_inst = None
    if args.model:
        state = inject_model(args.model, query_prompt=args.query_prompt)
        cache_root = Path(args.cache_dir).expanduser() if args.cache_dir else ds.cache_dir()
        model_id = state.get("hf_id") or PROBE_MODELS[args.model] or DEFAULT_MODEL_ID
        encode_cache_inst = encode_cache.EncodeCache(
            encode_cache.cache_path_for(cache_root, args.model, args.query_prompt),
            model_id=model_id, prompt_name=args.query_prompt)

    try:
        outcomes, report = run_evaluation(
            instances, reader=reader, judge=judge, ks=ks, top_k=top_k,
            work_dir=args.work_dir, split=args.split,
            checkpoint=args.checkpoint, max_retries=args.max_retries,
            retriever=args.retriever, model=args.model,
            query_prompt=args.query_prompt, retrieval_only=args.retrieval_only,
            db_uri=db_uri, encode_cache=encode_cache_inst,
        )
    except ModelEncodeFailedError as e:
        print(f"[longmem_eval] MODEL_ENCODE_FAILED: {e}", file=sys.stderr)
        print(f"[longmem_eval] aborting this config run with exit code "
              f"{MODEL_ENCODE_FAILED_EXIT} (never report empty recall as a "
              f"result)", file=sys.stderr)
        raise SystemExit(MODEL_ENCODE_FAILED_EXIT) from e

    out = args.output or str(default_report_path(args.split))
    save_report(report, out)
    _print_summary(report)
    print(f"\nreport saved to: {out}")
    return report


if __name__ == "__main__":
    run_main()
