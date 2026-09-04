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
import contextlib
import hashlib
import json
import math
import os
import random
import re
import sys
import tempfile
import threading
import time
import warnings
from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

# #1642 FIX 7: this tool is NOT a pytest suite — it has no conftest, so its
# --workers 8 mode used to leak every per-question embedded server outside
# any lifecycle seam. Opt into the fast interpreter-exit close for ephemeral
# test-tree servers (embedded_lifecycle.atexit_fast_close): a SIGKILLed run
# now exits its servers in seconds instead of orphaning them. The lme-*
# per-question trees are classified ephemeral by the reaper (EPHEMERAL_
# PREFIXES), so even a hard-killed run's servers converge via the scheduled
# reaper.
os.environ.setdefault("TORTOISE_FAST_ATEXIT", "1")

from tortoise.model_adapters import (
    RotatingModel,
    RoutingModel,
    is_fatal,
)
from tortoise.sdk import TortoiseSDK
from tortoise.shared_state.concurrency import flock_exclusive

# #1349: the probe seam — benchmark-only model injection (the production
# entrypoint rejects the override env). Imported at module level so the
# spot-check producer and the test monkeypatch share one symbol.
from ..embedder_probe import DEFAULT_MODEL_ID, PROBE_MODELS, inject_model
from . import dataset as ds
from . import encode_cache
from . import usage as lme_usage
from .dataset_audit import audit_dataset
from .errors import (
    INGEST_QUESTION_RETRIES,
    INGEST_WRITE_RETRIES,
    RESUME_ATTEMPTS_CAP,
    WriteStageRetriesExhausted,
    eval_failure_class,
    retryable_transient,
)
from .ingest import DEFAULT_CHUNK_TURNS, ingest_haystack
from .judge import build_judge, is_abstention
from .preflight import FatalProviderError, PreflightError, run_preflight
from .reader import build_reader, reader_prompt_constants
from .report import (
    _outcome_grade,
    build_report,
    compare_reports,
    default_report_path,
    git_sha,
    print_comparison,
    save_report,
)
from .rerank import _TRUTHY, RERANK_MODEL_DEFAULT, _env_int, rerank_enabled
from .retrieve import (
    DATA_AVAILABILITY_GATE_REASONS,
    DEFAULT_CONTEXT_ITEM_CAP,
    DEFAULT_CONTEXT_TOKEN_CAP,
    DEFAULT_EVIDENCE_BOOST_SOURCE,
    DEFAULT_EVIDENCE_BOOST_VERBATIM,
    DEFAULT_MAX_CHUNKS_PER_SESSION,
    DEFAULT_RETRIEVAL_BUDGET_MS,
    DEFAULT_TR_TOP_K,
    EVAL_RETRIEVAL_BUDGET_MS,
    GATE_MARKER_TTL_MIN,
    GATE_MAX_GATED,
    GATE_QUERY_Q_MS,
    GATE_REASON_CENSUS_ERROR,
    GATE_REASONS,
    GATE_RETRY_N,
    GATE_TIMEOUT_MS,
    HARD_GATE_REASONS,
    MODEL_ENCODE_FAILED_EXIT,
    VECTOR_TIMEOUT_MS,
    CensusReads,
    ModelEncodeFailedError,
    VectorBreakerOpenError,
    _consensus_read,
    _query_with_deadline,
    folded_pool_rows,
    resolve_answer_session_indices,
    retrieve_for_question,
    run_integrity_gate,
)

DEFAULT_KS = (5, 10, 20)
DEFAULT_TOP_K = 20
DEFAULT_MAX_RETRIES = 3
BACKOFF_BASE_S = 2.0
BACKOFF_CAP_S = 30.0

# ── #1786 (R2/R3): whole-question retry + resume-claim constants ─────────
#: R2 full-jitter start-spread max (s) — pinned ≥ the maximum expected
#: stall duration (the E2E's forced 40-60 s pause). The REAL anti-\
#: amplification enforcement is the BoundedSemaphore below; the jitter only
#: spreads start times so exhausted workers do not re-burn in lockstep.
R2_JITTER_MAX_S = 60.0
#: Resume-claim stamp TTL (s): sized from the ingest-duration TAIL, not the
#: mean — ≥ 2.5× the ~40-min worst-case legitimate re-attempt. PID-liveness
#: is primary; the TTL is the pid-reuse backstop.
RESUME_CLAIM_TTL_S = 90 * 60
#: Persisted failure-entry ``error`` repr cap (P2-7) — a multi-MB exception
#: repr must not bloat the checkpoint or break the JSON round-trip.
ERROR_REPR_CAP = 2000

#: #1786 (P2-6/P2-1): the shared resume-tier concurrency limiter — ≤ 2
#: concurrent ~25-min re-ingests across BOTH tiers (R2 re-attempts inside
#: the initial attempt + ``--retry-failed`` resume re-attempts). The tiers
#: never contend (R2 runs in the initial attempt's process; resumes run in
#: later processes), but the retry-amplification bound is enforced across
#: the recovery surface WITHIN each process — a ``BoundedSemaphore`` is
#: process-local, so cross-process concurrency is bounded by the flocked
#: claim CAS (Task 2 Step 7), never by this limiter.
_REINGEST_LIMITER = threading.BoundedSemaphore(2)

#: #1349 HNSW spot-check artifact dir — gate_1349.py's "HNSW artifact
#: present+cleared" check reads exactly this file.
SPOTCHECK_ARTIFACT_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "docs" / "research" / "2026-08-17-1349-embedder-selection"
)

#: #1349 checkpoint format marker (v2 = keyed by surface/retriever/model/
#: prompt + code fingerprint).
CHECKPOINT_FORMAT = "lme-checkpoint-v2"

#: Per-retriever required outcome keys — checkpoint validation drops a
#: truncated/corrupt record so the question re-encodes (never silently
#: dropped from the denominator).
REQUIRED_OUTCOME_KEYS: dict[str, tuple[str, ...]] = {
    "hybrid": ("question_id", "session_recall@k", "turn_recall@k"),
    "vector": ("question_id", "session_recall@k", "turn_recall@k",
               "ndcg@10", "p@10", "p@5", "ranked_ids"),
}


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
        prev = os.environ.get("TORTOISE_DB_URI")
        os.environ.setdefault("TORTOISE_DB_URI", db_uri)

        def _cleanup():
            if prev is None:
                os.environ.pop("TORTOISE_DB_URI", None)
            else:
                os.environ["TORTOISE_DB_URI"] = prev

        return TortoiseSDK(namespace=namespace), _cleanup
    td = tempfile.TemporaryDirectory(dir=work_dir, prefix="lme-")
    # #1944: under parallel-matrix/CI load the vendored redislite 10s server-
    # start timeout races (RedisLiteServerStartError — the falkordb.so module
    # load + socket bind can exceed 10s on a contended runner), making the
    # embedded eval questions fail with zero outcomes. Raise the client-side
    # start budget for the per-question embedded servers (normally instant).
    import redislite.client as _rc
    if _rc.Redis.start_timeout < 60:
        _rc.Redis.start_timeout = 60
    return TortoiseSDK(os.path.join(td.name, "lme.db")), td.cleanup


def _ensure_work_dir(work_dir: str | None) -> None:
    """Create the work dir if missing — ``TemporaryDirectory(dir=…)`` and
    the checkpoint file both require the parent to exist (issue #1349 T8
    pilot: observed FileNotFoundError on every question)."""
    if work_dir:
        Path(work_dir).mkdir(parents=True, exist_ok=True)


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


def _rerank_lambda(raw: str) -> float:
    """argparse ``type=`` guard for ``--rerank-lambda``: must be in [0,1]
    (boundary values accepted — λ=1 degenerates to pure rerank, λ=0 to pure
    similarity, per D7). Out-of-range fails fast before the question loop."""
    value = float(raw)
    if not (0.0 <= value <= 1.0):
        raise argparse.ArgumentTypeError(f"must be in [0,1], got {value}")
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


def _resolve_boost_float(env_name: str, default: float,
                         cli_value: float | None) -> float:
    """C2 (#1745) run-level boost-multiplier resolution (CLI > env >
    default): a value < 1.0 fails loudly (SystemExit) — the multiplier is
    a rank-scaling DIVISION, so 0.0 would ZeroDivide and a negative would
    silently invert the pool order (review P1-2). Non-finite values
    (NaN/Inf) fail the same way (review F9 — a NaN passes the < 1.0
    comparison and would poison every sort key; inf would zero every key
    and make the boost a silent no-op while methodology records it).
    Mirrors
    ``_resolve_rerank_env_int``'s fail-fast lo-validation; the
    retrieve-layer clamp (rerank._env_boost_float) is the per-question
    safety net."""
    if cli_value is not None:
        value = cli_value
    else:
        raw = os.environ.get(env_name)
        if raw is None or not raw.strip():
            return default
        try:
            value = float(raw.strip())
        except ValueError:
            return default
    if not (math.isfinite(value) and value >= 1.0):
        raise SystemExit(f"{env_name} must be >= 1.0, got {value!r}")
    return value


# ── R6 (#1545): effective rerank config resolution ──────────────────────


def _resolve_rerank_env_int(name: str, default: int, lo: int) -> int:
    """Run-level raw-env validation (D9): a parseable-but-out-of-range env
    value (``CAP=0`` / ``POOL=0``) fails fast (SystemExit) exactly like the
    CLI path; unparseable garbage falls back to the default — the
    retrieve-layer clamp (rerank._env_int) is the per-question safety net,
    NOT the run-level validation. Boundary values (``lo``) accepted."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        return default
    if value < lo:
        raise SystemExit(f"{name} must be >= {lo}, got {value!r}")
    return value


def _resolve_rerank_env_float(name: str, default: float,
                              lo: float, hi: float) -> float:
    """Run-level raw-env validation for the MMR lambda (0..1; boundary
    values accepted; out-of-range fails fast; garbage → default)."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except ValueError:
        return default
    if not (lo <= value <= hi):
        raise SystemExit(f"{name} must be in [{lo},{hi}], got {value!r}")
    return value


def _resolve_rerank(*, rerank: bool | None, rerank_model: str | None,
                    rerank_pool: int | None, per_session_cap: int | None,
                    mmr_lambda: float | None, max_k: int) -> dict:
    """The effective R6 config (CLI > env > default), resolved ONCE per run
    (D9). Env resolution + re-validation happen ONLY when the effective
    rerank is ON — a baseline run must be completely unaffected by the
    TORTOISE_LME_RERANK_CAP/LAMBDA/POOL env vars (only ``rerank_enabled``
    reads TORTOISE_LME_RERANK itself). ``pool_size`` is the EFFECTIVE
    APPLIED pool (the ``max(ks)``-adjusted value — a baseline run records
    ``max(ks)``, never the nominal env default 40). Returns a dict with the
    report/fingerprint ``config`` and the per-question retrieve kwargs.
    """
    rerank_on = rerank_enabled(rerank)
    if not rerank_on:
        applied = (max(rerank_pool, max_k) if rerank_pool is not None
                   else max_k)
        return {
            "rerank_on": False,
            "config": {"enabled": False, "model": None, "lambda_": None,
                       "per_session_cap": None, "pool_size": applied},
            "model": None, "rerank_pool": rerank_pool,
            "per_session_cap": None, "mmr_lambda": None,
        }
    cap = (per_session_cap if per_session_cap is not None
           else _resolve_rerank_env_int("TORTOISE_LME_RERANK_CAP", 2, lo=1))
    lam = (mmr_lambda if mmr_lambda is not None
           else _resolve_rerank_env_float("TORTOISE_LME_RERANK_LAMBDA",
                                          0.7, 0.0, 1.0))
    pool = (rerank_pool if rerank_pool is not None
            else _resolve_rerank_env_int("TORTOISE_LME_RERANK_POOL", 40, lo=1))
    model = (rerank_model
             or os.environ.get("TORTOISE_LME_RERANK_MODEL")
             or RERANK_MODEL_DEFAULT)
    applied = max(pool, max_k)
    return {
        "rerank_on": True,
        "config": {"enabled": True, "model": model, "lambda_": lam,
                   "per_session_cap": cap, "pool_size": applied},
        "model": model, "rerank_pool": pool,
        "per_session_cap": cap, "mmr_lambda": lam,
    }


# R3 (#1542): the embedder pinned for the eval pre-flight — now derived from
# tortoise.embeddings (the single source of truth; #1349 swapped the default
# to bge-small). The pre-flight probe asserts this dimension (384) so a swap
# or a wrong-dimension model is caught before any run, never silently used.
from tortoise.embeddings import EMBEDDING_MODEL as PINNED_EMBEDDER_MODEL  # noqa: E402

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
    # #1349: the loaded model id — EmbeddingModel has no model_id attr, so
    # fall back to the probe state (the ACTUAL injected candidate for
    # --model runs) before the pinned default. Without the probe check an
    # injected minilm/arctic-xs run would mislabel its evidence as the
    # production literal.
    try:
        from ..embedder_probe import get_state as _probe_state
        _ps = _probe_state()
        model_id = (_ps or {}).get("hf_id") or PINNED_EMBEDDER_MODEL
    except Exception:
        model_id = PINNED_EMBEDDER_MODEL
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
        # Reachable under --mock (warn + continue) AND under --skip-preflight
        # (the gate is lifted for debugging; #1626). Distinguish the two so an
        # operator isn't told a real run was "mock".
        print("[longmem_eval] WARNING: embedder unavailable "
              f"(reason={reason}) — the vector/dense leg is DISABLED for "
              "this run; install with: uv sync --group dev "
              "--extra embeddings", file=sys.stderr)
        return status
    print("[longmem_eval] EMBEDDER PRE-FLIGHT FAILED — the dense (vector) "
          f"leg cannot run (reason={reason}). Refusing to start: publishing "
          "a dense-less report is worse than no report.", file=sys.stderr)
    print("The eval env must install the pinned embedder (R3 #1542):",
          file=sys.stderr)
    print("  uv sync --group dev --extra embeddings", file=sys.stderr)
    print("  uv run python -c \"from sentence_transformers import "
          f"SentenceTransformer; SentenceTransformer('{PINNED_EMBEDDER_MODEL}')\"",
          file=sys.stderr)
    print("Verify with:", file=sys.stderr)
    print("  uv run python -c \"from tortoise.embeddings import "
          "EmbeddingModel; m = EmbeddingModel.get(load_timeout=600); "
          "assert m is not None; print('embedder OK')\"", file=sys.stderr)
    # #1626: numeric exit code (the message goes to stderr) — consistent with
    # the PreflightError path's SystemExit(1); a string code made CLI exit
    # status 1 ambiguous and broke the exit-code contract.
    raise SystemExit(1)

@contextlib.contextmanager
def _temporary_env_var(name: str, value: str):
    """Set ``name`` to ``value`` for the duration of the block; restore on exit.

    An explicit ``--db`` must WIN over a stale pre-existing env URI (a stale
    ``TORTOISE_DB_URI`` in the caller's shell would otherwise silently
    redirect the spot-check to the wrong FalkorDB server). The env is always
    restored so ``run_main()`` can be invoked repeatedly in one process
    without leaking the URI into later no-path SDK constructions (issue
    #1349 isolation).
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


# ── #1786 (R3): failure-entry lifecycle + resume-claim helpers ────────────
# The failure-entry schema (Task 1 Step 4, additive to the existing
# ``error``/``error_class``/``failed_at_utc`` shape — no format bump):
# ``{question_id, question_type, error, error_class, retryable, attempts,
#  failed_at_utc, in_progress: {in_progress_utc, pid} | null}``.


def _utc_now() -> datetime:
    """Injectably clocked UTC now — the resume-claim liveness/age checks
    share this seam (a test advances the clock without sleeping the 90-min
    TTL; monkeypatch ``run._utc_now``)."""
    return datetime.now(UTC)


def _quarantine_corrupt(path: Path) -> Path:
    """Rename ``<path>`` → ``<name>.corrupt.<utc>`` (P2-12). Guarded: only
    rename if the file still exists; swallow ``FileNotFoundError`` (a
    concurrent process that passed its ``is_file`` pre-check, waited on the
    flock, then read a file the other process renamed must not crash on its
    OWN rename). Caller MUST hold the checkpoint flock."""
    stamp = _utc_now().strftime("%Y%m%dT%H%M%S%f")
    qpath = path.with_name(f"{path.name}.corrupt.{stamp}")
    with contextlib.suppress(FileNotFoundError):
        # a concurrent process that passed its is_file pre-check, waited on
        # the flock, then read a file the other process renamed must not
        # crash on its OWN rename.
        path.rename(qpath)
    return qpath


def _write_json_atomic(p: Path, data: dict) -> None:
    """Atomic tmp+``os.replace`` JSON write (mirrors ``_save_checkpoint``'s
    write). Caller MUST hold the checkpoint flock."""
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def _read_checkpoint_or_quarantine(p: Path) -> dict:
    """Read the checkpoint JSON; on corrupt, quarantine (guarded rename)
    and REFUSE via ``CheckpointStaleError`` (P2-8/P2-12 — never silently
    merge-into-{} and overwrite on-disk state). Caller MUST hold the flock."""
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as e:
        qpath = _quarantine_corrupt(p)
        raise CheckpointStaleError(
            f"checkpoint {p} is corrupt ({e!r}) — quarantined to {qpath}; "
            f"refusing to proceed (fix or restore a backup)") from e
    except OSError as e:
        raise CheckpointStaleError(
            f"checkpoint {p} is unreadable ({e!r}) — refusing to proceed") from e


def _stamp_age(stamp: Any, *, now: datetime | None = None) -> float | None:
    """The claim stamp's age in seconds (None when absent/unparseable).
    TZ-naive ``in_progress_utc`` values are coerced to UTC (P2-3 review
    hardening — ``fromisoformat`` accepts naive strings, and an
    aware-minus-naive subtraction would TypeError the claim path)."""
    if not isinstance(stamp, dict):
        return None
    ts = stamp.get("in_progress_utc")
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return (now or _utc_now()) - parsed


def _stamp_live(stamp: Any) -> bool:
    """True when the claim stamp is a LIVE pid (POSIX ``os.kill(pid, 0)``
    probe). PID-liveness is PRIMARY (P1-4); the TTL is the pid-reuse
    backstop handled by :func:`_stamp_claimable`."""
    if not isinstance(stamp, dict):
        return False
    pid = stamp.get("pid")
    if not isinstance(pid, int):
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # alive but owned by another user — never steal


def _stamp_claimable(stamp: Any, *, now: datetime | None = None) -> bool:
    """A stamp is claimable iff ``os.kill(pid, 0)`` FAILS or the stamp age
    EXCEEDS the TTL (P2-3 precedence, pinned in one sentence). A live-pid
    stamp below the TTL is NEVER claimable."""
    if not isinstance(stamp, dict) or not stamp:
        return True
    pid = stamp.get("pid")
    alive = False
    if isinstance(pid, int):
        try:
            os.kill(pid, 0)
            alive = True
        except ProcessLookupError:
            alive = False
        except PermissionError:
            alive = True
    if not alive:
        return True  # dead pid → claimable immediately (PID-liveness primary)
    age = _stamp_age(stamp, now=now)
    return age is not None and age.total_seconds() > RESUME_CLAIM_TTL_S


def _legacy_repr_match(err: str) -> bool:
    """P1-5 legacy rescue: a pre-feature failure entry (no ``retryable``
    field) is re-attempted when its persisted ``error`` repr matches a
    transport transient class name (``network:TimeoutError`` / plain
    ``TimeoutError(...)`` / ``network:ConnectionError`` — the reprs the 4
    motivating reval-run entries carry). Repr-substring matching is brittle,
    so this is ONLY the legacy fallback, never the primary signal."""
    return ("TimeoutError" in err) or ("ConnectionError" in err)


def _retry_failed_skip_reason(entry: dict, *,
                              cap: int = RESUME_ATTEMPTS_CAP,
                              check_stamp: bool = True) -> str | None:
    """Why a failure entry is NOT ``--retry-failed`` eligible (None =
    eligible). The gate (Task 2 Step 2): ``error_class ==
    ingest:retries_exhausted`` AND (``retryable`` True OR the legacy repr
    rescue) AND ``attempts < cap``. All reads are ``.get()``-based (P1-5 —
    legacy entries lack the additive fields).

    ``check_stamp`` (P1-3): the ``in_progress`` stamp check is an ADVISORY
    fast path only — it evaluates against the startup-loaded (unlocked)
    failures list. The flocked claim CAS (Step 7) re-verifies with
    ``_stamp_claimable`` (TTL-aware) and passes ``check_stamp=False`` here —
    the advisory live-stamp check must NOT veto a claim that the TTL age
    branch admits."""
    if entry.get("error_class") != "ingest:retries_exhausted":
        return (f"error_class={entry.get('error_class')!r} — only "
                f"ingest:retries_exhausted entries are re-attempted "
                f"(reader:/judge: entries stay permanently skipped)")
    if entry.get("retryable") is False:
        return "retryable=false (deterministic failure — never re-attempted)"
    if entry.get("retryable") is None and not _legacy_repr_match(
            str(entry.get("error") or "")):
        # Legacy entry without a 'retryable' field: the repr-string check is
        # the defined legacy rescue (P1-5 — a non-matching repr is skipped).
        return ("legacy entry without a 'retryable' field whose error "
                "repr does not match a transport transient — skipped")
    attempts = entry.get("attempts")
    if not isinstance(attempts, int):
        attempts = 0
    if attempts >= cap:
        return (f"attempt budget exhausted (attempts={attempts} >= cap={cap}) "
                f"— entry retained; free disk / wait out the outage, then "
                f"raise the cap (fingerprint-refusing — rotate the "
                f"checkpoint) or hand-remove the retained entry")
    # Advisory fast path (P1-3/P2-3): a stamp that is NOT claimable (a
    # LIVE pid below the TTL) means another process is mid-re-attempt — the
    # flocked CAS re-verifies this under the flock (this check is an
    # unlocked read, advisory only). TTL-aware: a dead-pid stamp or a
    # >90-min hung-live stamp IS claimable, so it must pass this fast path
    # and reach the flocked CAS (which re-verifies authoritatively) — the
    # old age-AGNOSTIC ``_stamp_live`` check here hard-vetoed exactly the
    # claims the TTL is designed to admit.
    if check_stamp and not _stamp_claimable(entry.get("in_progress")):
        return "currently claimed by another process (live in_progress stamp)"
    return None


def _retry_failed_gate_ok(entry: dict) -> bool:
    return _retry_failed_skip_reason(entry) is None


def _failure_entry(qid: str, question_type: str, exc: BaseException, *,
                   stage: str, attempts: int, prior: dict | None = None,
                   usage: dict | None = None) -> dict:
    """Build a persisted failure entry (Task 1 Step 4 schema). The handler
    must UNWRAP the sentinel BEFORE calling this — ``error``/``error_class``/
    ``retryable`` are derived from the INNER exception, never the sentinel.

    ``prior`` (the ON-DISK entry re-read under the flock) drives the
    recovery-tier preservation (P1-1): a qid that once entered the recovery
    tier (``ingest:retries_exhausted``) stays tier-eligible even when a
    re-attempt fails at a NON-ingest stage (the re-extraction advances
    ``_stage`` to reader/judge — without preservation an LLM-provider
    transient there would re-classify the entry and silently exclude it)."""
    retryable = retryable_transient(exc)
    error_class = eval_failure_class(exc, site=stage)
    if prior and prior.get("error_class") == "ingest:retries_exhausted":
        error_class = "ingest:retries_exhausted"
    err = repr(exc)
    if len(err) > ERROR_REPR_CAP:
        err = err[:ERROR_REPR_CAP] + "…<truncated>"
    entry = {
        "question_id": qid,
        "question_type": question_type,
        "error": err,
        "error_class": error_class,
        "retryable": retryable,
        "attempts": attempts,
        "failed_at_utc": _utc_now().isoformat(),
        "in_progress": None,
    }
    # #2185: the qid's CUMULATIVE usage envelope rides the failure entry as
    # the kill-9-safe replica — present ONLY when an LLM was actually
    # called (the A4 load fold reads it; mock/failed-early runs stay
    # byte-unchanged).
    if usage is not None:
        entry["usage"] = usage
    return entry


def _build_failure_entry(qid: str, question_type: str, exc: BaseException,
                         stage: str, r2_attempted: int,
                         resume_reattempt: bool, prior: dict | None,
                         *, usage_env: dict | None = None) -> dict:
    """The run-loop failure-entry builder (module-level — B023-clean so the
    per-question loop can pass it via ``functools.partial``). Counter
    semantics (P1-6): the in-run R2 increments the persisted counter (an
    R2-exhausted entry starts at ``attempts=1``); a provider transient that
    never entered the write-stage loop starts at ``attempts=0``; each failed
    ``--retry-failed`` re-attempt increments from the ON-DISK ``prior``
    (never the stale in-memory copy; never at claim — a kill -9 mid-attempt
    leaves the counter untouched so the dead-pid re-claim stays admitted)."""
    attempts = ((1 if r2_attempted else 0)
                if not resume_reattempt
                else (int((prior or {}).get("attempts", 0) or 0) + 1))
    return _failure_entry(qid, question_type, exc, stage=stage,
                          attempts=attempts, prior=prior,
                          usage=usage_env)


def _claim_reattempt(checkpoint: str | None, qid: str, cap: int, *,
                     now: datetime | None = None) -> bool:
    """Task 2 Step 7 concurrent-resume CLAIM — compare-and-swap under ONE
    flock acquisition: (1) acquire the same exclusive flock as
    ``_save_checkpoint``; (2) RE-READ the on-disk entry; (3) re-verify the
    gate + the stamp is dead (the Step 2 unlocked check is an ADVISORY fast
    path — this flocked re-verify is authoritative); (4) write the FULL
    entry with the ``in_progress`` stamp added — every additive field
    (``error``/``error_class``/``retryable``/``attempts``) preserved, never
    a wholesale ``{question_id, in_progress}`` replacement; (5) release.

    ``now`` is the injectable clock seam for the TTL tests."""
    if not checkpoint:
        return False
    p = Path(checkpoint)
    if not p.is_file():
        return False
    with flock_exclusive(p.with_suffix(p.suffix + ".lock")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError,
                RecursionError):
            return False  # corrupt — _load_checkpoint's quarantine governs
        entries = data.get("failures", []) or []
        prior = next((f for f in entries
                      if isinstance(f, dict) and f.get("question_id") == qid),
                     None)
        if prior is None or _retry_failed_skip_reason(
                prior, cap=cap, check_stamp=False) is not None:
            return False
        # Authoritative stamp re-verify: claimable iff the pid is dead or
        # the stamp age EXCEEDS the TTL (P2-3 precedence).
        if not _stamp_claimable(prior.get("in_progress"), now=now):
            return False
        claimed = dict(prior)
        claimed["in_progress"] = {
            "in_progress_utc": _utc_now().isoformat(),
            "pid": os.getpid(),
        }
        data["failures"] = [
            claimed if (isinstance(f, dict) and f.get("question_id") == qid)
            else f for f in entries]
        _write_json_atomic(p, data)
        return True


def _upsert_failure(checkpoint: str | None, qid: str,
                    build: Callable[[dict | None], dict], *,
                    fingerprint: dict | None = None,
                    run_key: str | None = None, surface: str | None = None,
                    retriever: str | None = None, model: str | None = None,
                    prompt: str | None = None) -> dict:
    """Task 2 Step 4 append-site: ONE flocked write — re-read the on-disk
    base, REPLACE any prior entry for ``qid`` with ``build(prior)`` (never
    append a duplicate — ``n_failed = len(failures)`` must never
    double-count a qid), write the full file inline. The replacement is
    built from the ON-DISK prior (never the stale in-memory copy), so a
    capped qid cannot be re-admitted and a recovery-tier entry written by
    another process cannot be missed. Returns the final persisted entry.

    #1786 (code-review F6): the FRESH-file branch (no checkpoint exists yet)
    emits the FULL checkpoint key set (``format``/``run_key``/``surface``/
    ``retriever``/``model``/``prompt``/``fingerprint``) via
    ``_write_checkpoint_locked`` — a kill -9 between this write and the
    trailing ``_save_checkpoint`` used to leave a PARTIAL-shape file
    (``{failures, outcomes}`` with no markers) that the next resume REFUSED
    in entirety ("predates the fingerprint contract")."""
    if not checkpoint:
        return build(None)
    p = Path(checkpoint)
    p.parent.mkdir(parents=True, exist_ok=True)
    with flock_exclusive(p.with_suffix(p.suffix + ".lock")):
        if p.is_file():
            data = _read_checkpoint_or_quarantine(p)
        else:
            data = {"failures": [], "outcomes": []}
        entries = data.get("failures", []) or []
        prior = next((f for f in entries
                      if isinstance(f, dict) and f.get("question_id") == qid),
                     None)
        entry = build(prior)
        data["failures"] = [
            entry if (isinstance(f, dict) and f.get("question_id") == qid)
            else f for f in entries]
        if not any(isinstance(f, dict) and f.get("question_id") == qid
                   for f in data["failures"]):
            data["failures"].append(entry)
        if p.is_file():
            _write_json_atomic(p, data)
        else:
            # Fresh-file branch: write the FULL checkpoint shape so a
            # kill -9 in the window before the trailing save cannot leave
            # a markerless file the loader refuses wholesale.
            _write_checkpoint_locked(
                p, data.get("outcomes", []), data["failures"], fingerprint,
                run_key=run_key, surface=surface, retriever=retriever,
                model=model, prompt=prompt)
        return entry


# Tuning knobs that change extraction output (M7 #1739, Gap 2): the MODELS
# registry has entries sharing ONE wire id with different tuning — but note
# only -noreason is actually discriminated from the trio: deepseek-v4-pro and
# deepseek-v4-pro-xhigh are BYTE-IDENTICAL configs (both max_tokens=500 with
# the fixed temperature=0.0 default → both fingerprint
# 'deepseek/deepseek-v4-pro|max_tokens=500' — correct, nothing differs);
# deepseek-v4-pro-noreason (max_tokens=8000, disable_reasoning=True) is the
# discriminating variant. A wire-id-only fingerprint would silently resume a
# reasoning-ON checkpoint under reasoning-OFF -noreason. Appended only when
# non-default (max_tokens whenever not None; temperature when explicitly
# non-default — not None and != 0.0, mirroring the max_tokens check's
# explicit structure; thinking_budget/disable_reasoning when truthy), so a
# default-tuning adapter's fingerprint stays its bare wire id (the #1732
# literal contract: DeepSeekDirectModel('deepseek-v4-flash') → 'deepseek-v4-flash').
_TUNING_FINGERPRINT_ATTRS = ("max_tokens", "temperature", "thinking_budget",
                             "disable_reasoning")


def _adapter_fingerprint(model: Any) -> str:
    """Stable, tuning-aware fingerprint of a single (non-wrapper) adapter.

    M4 #1732: adapters expose ``.id`` (the API-facing wire id), not
    ``.model_id`` — prefer ``.model_id`` then ``.id``; repr is the last
    resort, made LOUD (warnings.warn) so a future adapter missing both
    attributes surfaces at fingerprint time instead of silently
    re-introducing the #1732 address-bearing-repr failure class (a repr
    embeds a memory address ``<... at 0x...>`` that would make the
    fingerprint NON-deterministic across processes). M7 #1739 (Gap 2): the
    tuning knobs that change extraction output ride the fingerprint when
    non-default — ``max_tokens`` whenever not None (an explicit 0 cap is a
    real value, only None is the uncapped default), ``temperature`` when
    explicitly non-default (not None and != 0.0 — None means the provider
    default ~1.0 on the wire, 0.0 is the fixed extractor default), the
    rest when truthy (thinking_budget>0 / disable_reasoning=True) — so
    two registry entries sharing a wire id but differing in tuning
    fingerprint differently (note: deepseek-v4-pro ≡ deepseek-v4-pro-xhigh
    — byte-identical configs, same fingerprint — only -noreason is
    discriminated from that trio). Default-tuning adapters stay at the bare wire
    id — the #1732 cross-provider contract is preserved (provider stays a
    routing detail for single adapters)."""
    for attr in ("model_id", "id"):
        mid = getattr(model, attr, None)
        if mid is not None and str(mid).strip():  # degenerate (whitespace) ids fall through
            break
    else:
        warnings.warn(
            f"model fingerprint: {type(model).__name__} has neither "
            ".model_id nor .id — fingerprint falls back to repr(model) "
            "(address-bearing, non-deterministic across processes); give "
            "the adapter a stable id to keep the checkpoint-resume "
            "contract", stacklevel=2)
        return repr(model)  # last resort (never for production adapters)
    parts = [mid]
    for attr in _TUNING_FINGERPRINT_ATTRS:
        value = getattr(model, attr, None)
        if attr == "max_tokens":
            if value is not None:  # None = uncapped (the only "off"); 0 is a real cap
                parts.append(f"{attr}={value}")
        elif attr == "temperature":
            # Explicit non-default only (mirrors max_tokens' is-not-None
            # structure): None (the provider default ~1.0 on the wire) and
            # 0.0 (the fixed extractor default) are both omitted.
            if value is not None and value != 0.0:
                parts.append(f"{attr}={value}")
        elif value:  # thinking_budget>0, disable_reasoning=True
            parts.append(f"{attr}={value}")
    return "|".join(parts)


def _session_worker_spec_tuning(spec: str) -> tuple[str, int | None, float, str]:
    """M5-gated resolution of an explicit ``--extractor-model`` for the
    session-parallel worker path (M7 #1739 / review #1742):
    ``(wire id, max_tokens, temperature, pinned id)`` — the registry
    entry's REAL wire id (never the registry key — ``solar-pro4``/``claude-opus-5`` are not
    valid wire ids) resolved through the ``REGISTRY_KEY_TO_ID`` remap so
    every lane gets a valid id (``deepseek-flash-direct`` →
    ``deepseek/deepseek-v4-flash``: the direct lane strips to the bare
    ``deepseek-v4-flash`` (thinking disabled in the adapter), the OpenRouter
    lane keeps the prefixed id — bare ids
    404 there). The 4th element is the pinned entry's OWN id (pre-lane-
    normalization) — the #1742 pin-rewrite warning in
    ``_build_cli_extractor_model`` compares it against what the router
    actually serves. REFUSES entries the ingest_v2 factory cannot express
    (``thinking_budget`` / ``disable_reasoning`` — ``_build_single``
    forwards only max_tokens/temperature): loud, never a silent reasoning
    flip."""
    from tests.model_adapters import MODELS
    from tortoise.model_adapters import REGISTRY_KEY_TO_ID
    if spec not in MODELS:
        raise SystemExit(f"unknown extractor model {spec!r}; "
                         f"known: {sorted(MODELS)}")
    entry = MODELS[spec]()
    try:
        if (getattr(entry, "thinking_budget", 0)
                or getattr(entry, "disable_reasoning", False)):
            raise SystemExit(
                f"--extractor-model {spec!r} with --session-workers > 1 is "
                "refused: the session-parallel factory cannot express its "
                "thinking_budget/disable_reasoning tuning (it would silently "
                "flip to defaults) — drop --session-workers or use the "
                "default extractor path")
        return REGISTRY_KEY_TO_ID.get(spec, entry.id), \
            entry.max_tokens, entry.temperature, entry.id
    finally:
        entry.close()


def _served_wire_ids(model: Any) -> list[str]:
    """The raw ``.id`` of every member adapter a wrapper serves (a bare
    adapter → ``[model.id]``). Feed for the #1742 pin-rewrite warning — the
    fingerprint records the SERVED composition, this surfaces what the wire
    actually carries at build time."""
    if isinstance(model, RoutingModel):
        members = [m for m in (model.primary, model.fallback)
                   if m is not None]
    elif isinstance(model, RotatingModel):
        members = list(model.providers)
    else:
        members = [model]
    return [str(getattr(m, "id", "")) for m in members]


def _warn_pin_rewrite(spec: str, pinned_id: str, built: Any) -> None:
    """LOUD warning when a pinned ``--extractor-model`` id is NOT served
    verbatim at ``session_workers > 1`` (review #1742): lane normalization
    (#1790 ``_direct_wire_id`` — ``deepseek/deepseek-v4-flash`` →
    ``deepseek-v4-flash`` on the direct lane) can REWRITE the pin's wire id
    with no user-visible signal; the "REFUSED (safe direction)" framing
    only materializes at resume time. Compares WIRE IDS only (never
    tuning — the -pro/-xhigh/-noreason trio shares one id and must not
    trip this). A warning, not a refusal — fresh sw>1 runs keep working."""
    served_ids = _served_wire_ids(built)
    if not served_ids or pinned_id in served_ids:
        return
    warnings.warn(
        f"--extractor-model {spec!r} pinned wire id {pinned_id!r} is not "
        f"served verbatim at --session-workers > 1: lane normalization "
        f"serves {sorted(set(served_ids))!r} instead (e.g. #1790 "
        f"_direct_wire_id: deepseek/deepseek-v4-flash → deepseek-v4-flash on "
        f"the direct lane) — the checkpoint fingerprints the SERVED id, so "
        f"a cross-lane/env resume is refused (safe direction); pin a "
        f"lane-neutral id or accept the substitution",
        stacklevel=2)


def _build_cli_extractor_model(*, spec: str | None,
                               session_workers: int):
    """Build the extractor model a CLI run will actually serve AND
    fingerprint (M7 #1739 / review #1742): the fingerprint must record the
    EXTRACTING model, so the build mirrors the serving path.

    ``session_workers == 1``: the run-level model IS the extracting model —
    an explicit ``spec`` stays a MODELS registry lookup (M5 pinning, registry
    tuning applies); unset delegates to the production router
    (``build_extractor_model``, uncapped — the #1350 owner decision).
    ``session_workers > 1``: requests per-worker models via the ingest_v2
    ``model_factory``, so the run-level model — and the fingerprint — is
    built the SAME way the factory does (``_session_worker_spec_tuning``
    resolves the registry entry's real wire id + expressible tuning; the
    unset case stays UNCAPPED, matching the session_workers=1 owner
    decision). NOTE: the live ``ingest_haystack_v2`` on main currently
    shadows the parallel factory path with a sequential copy (pre-existing
    duplicate, tracked separately — #1744), so workers fall back to the
    shared ``extractor_model``; the fingerprint-vs-served guard remains the
    safety invariant and records the serving config either way. A spec'd
    run therefore fingerprints identically across a session-workers toggle
    only when the router resolves a SINGLE lane
    matching the registry adapter (the same effective config — resume
    accepted); in multi-lane environments the sw>1 path is provider-routed
    (RoutingModel/RotatingModel composition) and a toggle is REFUSED —
    safe direction, the fingerprint records what each path serves."""
    if session_workers > 1:
        from tests.model_adapters import build_extractor_model
        if spec is not None:
            wire_id, max_tokens, temperature, pinned_id = \
                _session_worker_spec_tuning(spec)
            built = build_extractor_model(
                wire_id, max_tokens=max_tokens, temperature=temperature)
            _warn_pin_rewrite(spec, pinned_id, built)
            return built
        return build_extractor_model(max_tokens=None, temperature=0.0)
    if spec:
        # M5 pinning: an explicit --extractor-model stays a registry lookup.
        from tests.model_adapters import MODELS
        if spec not in MODELS:
            raise SystemExit(f"unknown extractor model {spec!r}; "
                             f"known: {sorted(MODELS)}")
        return MODELS[spec]()
    # #1530 D9: the unset case delegates to the production router via the
    # shim — single source of truth (removed the bespoke env branch). Same
    # decision surface: TORTOISE_EXTRACTOR_PROVIDER picks the primary;
    # DEEPSEEK_API_KEY alone → deepseek-direct; else OpenRouter. Uncapped
    # (the #1350 owner decision).
    from tests.model_adapters import build_extractor_model
    return build_extractor_model(max_tokens=None, temperature=0.0)


def _model_id(model: Any) -> str | None:
    """A stable fingerprint string for a model object (None → None).

    M4 #1732 fix (PILOT #1549 context): adapters expose ``.id`` (the
    API-facing wire id), not ``.model_id`` — the old fallback returned
    ``repr(model)`` which embeds a memory address (``<DeepSeekDirectModel
    object at 0x...>``), making the fingerprint NON-deterministic across
    processes and refusing every resume (CheckpointStaleError on
    ``extractor_model`` even with identical git_sha). Prefer ``.model_id``
    then ``.id``; only fall back to repr as a last resort.

    M7 #1739 fix (Gap 1): wrapper models from ``build_extractor_model()``
    (RoutingModel / RotatingModel — the DEFAULT CLI path) expose neither
    attribute, so they are composed structurally instead:
    ``provider:wire-id`` per member adapter, joined with ``+`` and carrying
    each member's tuning suffix. A SINGLE-member wrapper emits the bare
    member fingerprint (no provider prefix, no shape prefix) — one lane has
    no routing, so the default path is comparable to the equivalent bare
    ``MODELS`` entry (the #1732 single-adapter contract). Multi-lane
    wrappers are shape-prefixed (``routing:`` / ``rotating:``) so a
    failover wrapper and a rotation pool over the same members fingerprint
    differently (RoutingModel keeps (primary, fallback) order — order IS
    effective config; RotatingModel members are SORTED by provider — pool
    order is a routing detail for the weighted rotation, so a reorder keeps
    checkpoints valid while a membership change still invalidates them). A
    memberless wrapper (degenerate) yields None, never an empty string.
    RotatingModel weights are deliberately EXCLUDED: on the production path
    (build_extractor_model) weights are a pure function of the member set
    (fixed per-provider values), which IS fingerprinted; a programmatic
    weight change is out of fingerprint scope (git_sha-protected). The
    provider is part of the WRAPPER identity (which lanes the pool routes
    to — a pool change is an effective-config change) but remains a routing
    detail for single adapters (a bare adapter's fingerprint stays
    wire-id-only, the #1732 cross-provider contract). Deterministic — no
    repr/address — and identical across fresh instances, so the #1549
    checkpoint-resumable protocol works on the default path.
    """
    if model is None:
        return None
    # NOTE (fingerprint charset invariant): the composed literals use ``+``
    # (member separator), ``:`` (provider separator) and ``|`` (tuning
    # separator) — collision-free ONLY because wire ids are alphanumeric
    # with ``/ - .`` (the MODELS registry and REGISTRY_KEY_TO_ID remaps
    # enforce this; no id contains a separator). A future wire id carrying
    # a separator must be rejected/escaped at the adapter boundary, not
    # here.
    if isinstance(model, RoutingModel):
        members = [m for m in (model.primary, model.fallback)
                   if m is not None]
        if not members:
            return None  # degenerate wrapper — no lane to fingerprint
        if len(members) == 1:
            return _adapter_fingerprint(members[0])  # single lane = bare adapter
        return ("routing:"
                + "+".join(f"{m.provider}:{_adapter_fingerprint(m)}"
                           for m in members))
    if isinstance(model, RotatingModel):
        members = sorted(model.providers, key=lambda p: p.provider)
        if not members:
            return None  # degenerate wrapper — no lane to fingerprint
        if len(members) == 1:
            return _adapter_fingerprint(members[0])  # single lane = bare adapter
        return ("rotating:"
                + "+".join(f"{m.provider}:{_adapter_fingerprint(m)}"
                           for m in members))
    return _adapter_fingerprint(model)


def _build_fingerprint(*, reader_model: str, judge_model: str,
                       ks: tuple[int, ...], top_k: int, split: str,
                       ingest_mode: str, extractor_model: Any,
                       max_retries: int, dataset_fingerprint: str,
                       rerank_config: dict,
                       context_item_cap: int | None = None,
                       evidence_boost: bool | None = None,
                       evidence_boost_verbatim: float | None = None,
                       evidence_boost_source: float | None = None,
                       max_chunks_per_session: int | None = None,
                       # #1786 (P1-1/P1-2/P2-4): the write-path retry knobs —
                       # ALWAYS present (results-relevant by construction: a
                       # question that dies at 0 write retries survives at 2 —
                       # the same class as max_retries). A pre-feature
                       # checkpoint therefore refuses via CheckpointStaleError
                       # (the SAFE direction — Task 8 requires a fresh
                       # checkpoint anyway).
                       ingest_write_retries: int = INGEST_WRITE_RETRIES,
                       ingest_question_retries: int = INGEST_QUESTION_RETRIES,
                       resume_attempts_cap: int = RESUME_ATTEMPTS_CAP,
                       # #1786 (R5): the eval's HYBRID-arm retrieval deadline
                       # (ms) — conditional presence (present iff non-default:
                       # the eval always passes EVAL_RETRIEVAL_BUDGET_MS, so a
                       # 500-ms-budget checkpoint refuses a 1500-ms resume).
                       # The vector arm's VECTOR_TIMEOUT_MS stays OUT of this
                       # key (SDK-pinned, not eval-configurable — P2-5).
                       retrieval_budget_ms: int | None = None) -> dict:
    """The effective-run-config fingerprint (M7 #1527, D7 schema).

    ``workers`` is deliberately EXCLUDED (per-question isolation makes
    results workers-invariant) but recorded in ``methodology.workers``.
    R6 (#1545, D9): the full effective rerank config rides the fingerprint
    (``rerank_config``) — a config-mismatched resume is refused by the
    existing fingerprint gate (a baseline checkpoint resumed with
    ``--rerank``, a pool change with rerank off, … — Gate 8).

    M7 #1739: ``extractor_model`` discriminates BOTH wire id and tuning —
    tuning variants sharing a wire id (``deepseek-v4-pro-xhigh`` vs
    ``deepseek-v4-pro-noreason``) fingerprint differently, so a cross-tuning
    resume is refused (was silently accepted post-#1732); the default
    wrapper path (RoutingModel/RotatingModel) composes its member adapters
    (``provider:wire-id`` + tuning; single-lane wrappers emit the bare
    member fingerprint; multi-lane wrappers are shape-prefixed routing:/rotating:)
    — never an address-bearing repr. SESSION_WORKERS: ``--session-workers
    > 1`` requests per-worker models via the ingest_v2 ``model_factory``
    (note: the live ``ingest_haystack_v2`` on main currently shadows the
    parallel factory path with a sequential copy — pre-existing duplicate,
    tracked separately (#1744) — so workers fall back to the shared
    ``extractor_model``; the fingerprint-vs-served guard remains the safety
    invariant and records the serving config either way).
    ``_build_cli_extractor_model`` builds the fingerprinted model and
    run_main threads the resolved spec + tuning into the factory so the
    workers serve EXACTLY what the fingerprint records (a spec'd run
    fingerprints identically across a session-workers
    toggle only in single-lane environments — the same effective config,
    resume accepted; multi-lane envs are provider-routed at sw>1 and the
    toggle is REFUSED, safe direction; the unset run stays UNCAPPED,
    matching the session_workers=1 owner decision). Registry entries the
    factory cannot express (thinking_budget/disable_reasoning) are refused
    loudly at session_workers>1. WIRE-ID
    MUTABILITY: wire ids are API-facing and mutable (e.g. #1706, pre-#1790,
    renamed deepseek-v4-flash → deepseek-chat; #1790 migrated back to
    deepseek-v4-flash) — a rename loudly invalidates every
    existing checkpoint (CheckpointStaleError on ``extractor_model``): safe
    by design, expected on any future normalization change.
    ENV-DEPENDENCE (default path): with ``--extractor-model`` unset the
    fingerprint resolves via ``resolve_extractor_provider`` — which lanes
    the router serves is a function of the extractor-key env
    (DEEPSEEK_API_KEY / OPENROUTER_API_KEY / VENICE_API_KEY /
    TORTOISE_EXTRACTOR_PROVIDER). A resume is therefore only valid within
    the SAME provider/key environment: identical git_sha + CLI across
    machines with differing key env (e.g. DEEPSEEK_API_KEY-only vs
    OPENROUTER_API_KEY-only) refuses with CheckpointStaleError — safe
    direction, the env changes what the default path serves.
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
        "rerank": rerank_config,
        # #1786 (P1-1/P1-2): the three retry constants are ALWAYS present —
        # a deliberate default-fingerprint change so a pre-feature checkpoint
        # resumed under post-feature DEFAULTS refuses instead of silently
        # changing retry semantics (0 retries → 2 write retries + 1 R2 + 2
        # resumes). ``--retry-failed`` is NOT fingerprinted (a recorded
        # resume-mode, methodology + checkpoint field — Task 2 Step 1).
        "ingest_write_retries": ingest_write_retries,
        "ingest_question_retries": ingest_question_retries,
        "resume_attempts_cap": resume_attempts_cap,
    } | {
        # C1/C2/C5 (#1745): the effective reader-context + evidence-boost
        # knobs ride the fingerprint (present only when the caller passes
        # them — defaults keep the fingerprint byte-identical for existing
        # callers/tests). A config-mismatched resume (e.g. an unboosted
        # checkpoint resumed with TORTOISE_LME_EVIDENCE_BOOST=1, or a
        # context-item-cap change) is refused by the existing fingerprint
        # gate. ``_fingerprint_diffs`` key-unions, so a None value here vs
        # an absent key in the checkpoint would mismatch — hence the
        # conditional presence.
        **({k: v for k, v in (
            ("context_item_cap", context_item_cap),
            ("evidence_boost", evidence_boost),
            ("evidence_boost_verbatim", evidence_boost_verbatim),
            ("evidence_boost_source", evidence_boost_source),
            ("max_chunks_per_session", max_chunks_per_session),
            # #1786 (R5): the eval's hybrid retrieval budget — conditional
            # presence (the eval always passes 1500, so a pre-feature /
            # 500-ms-budget checkpoint refuses via CheckpointStaleError).
            ("retrieval_budget_ms", retrieval_budget_ms),
        ) if v is not None}),
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


def _legit_sessionless(outcome: dict) -> bool:
    """Outcome whose question has no answer session / evidence turns to
    recall (M6 #1526 N/A-not-0.0): ``turn_recall@k`` records None — never a
    forced 0.0 — because ``evidence_turn_ids`` (dataset-derived) is empty.
    Such outcomes are legitimately all-zero on session recall (abstention
    questions — the answer IS "not mentioned") and must NOT be treated as
    retrieval-dead, or they would re-encode on every resume forever.

    Known limitation: the vector arm (#1349) records ``turn_recall@k`` as a
    forced 0.0 for empty evidence (an M6 divergence from the hybrid path),
    so a vector-mode abstention is not recognized as sessionless here and
    is re-encoded per resume. The M6 fix belongs in retrieve.py (vector
    arm); not yet tracked — #1745 is the point-level evidence-recall gap
    (evidence@20 vs chunk-level), which does NOT cover this forced-0.0-vs-
    None divergence.
    """
    tr = outcome.get("turn_recall@k")
    return isinstance(tr, dict) and bool(tr) and all(
        v is None for v in tr.values())


# ── R3 (#1542) D4 leg-reason vocabulary (tortoise/search_engine.py) ────────
#: Per-leg trace entries are {"leg", "ran", "degraded", "reason", "count"};
#: the engine emits ``reason`` from a closed vocabulary. Classification for
#: the #1764 resume-quality gate:
#:   DEAD at count==0 (genuinely retrieval-dead):
#:     ``empty_results`` — FTS ran clean and found nothing (the pilot
#:         artifact);
#:     ``query_failed`` — a real driver failure;
#:     ``timeout`` — the strategy deadline expired (as_completed merge,
#:         R3 #1542 D4): the query never completed, results discarded;
#:   BENIGN (environmental/structural — never retrieval-dead):
#:     ``ok`` — live retrieval;
#:     ``index_missing`` — embedded FalkorDBLite, no FTS index; degrades
#:         quietly, never trips the breaker;
#:     ``no_embeddings`` — vector arm: zero embedded points;
#:     ``breaker_open`` — the breaker skipped the strategy.
#: Tuples (not sets) so a corrupt non-string reason (hand-edited files)
#: compares by equality instead of TypeErroring the gate on hashing.
DEAD_LEG_REASONS: tuple[str, ...] = (
    "empty_results", "query_failed", "timeout")
BENIGN_LEG_REASONS: tuple[str, ...] = (
    "ok", "index_missing", "no_embeddings", "breaker_open")
KNOWN_LEG_REASONS: tuple[str, ...] = tuple(
    sorted(set(DEAD_LEG_REASONS) | set(BENIGN_LEG_REASONS)))

#: Per-leg result-count floors F_leg (#1785, plan Task 1 Step 2) — derived
#: from the committed historical re-validation fixture (31 healthy full-graph
#: outcomes): min observed per-leg return count minus 1, CLAMPED >= 1, with
#: by-design-zero legs (TR entity legs, abstention questions) excluded from
#: the min. NOT a threshold-from-noise constant (the anti-pattern retracted
#: for the ratio tier); a committed constant — a change breaks CI. Derivation:
#: fts min=25 -> 24; structural min=120 -> 119; vector min=120 -> 119
#: (checkpoint git_sha 57f43978, dataset digest d6f21ea9d60a0d56). A leg
#: count AT or BELOW its floor is an OBSERVABLE degradation signal for the
#: leg-health predicate / watchdog leg-deadness arm (a naturally-short leg
#: on a ratio=1.000 healthy graph is exempted by the collapse guard).
F_LEG_FLOORS: dict[str, int] = {
    "fts": 24,
    "structural": 119,
    "vector": 119,
}


def _leg_entry_dead(entry: dict, *, floors: dict[str, int] | None = None) -> bool:
    """Observable-signal leg-degradation predicate for one leg entry.

    A leg is degraded ONLY on an observable signal (plan second-model P2):
    a genuinely-dead reason with count==0 (``empty_results``/``query_failed``),
    a timeout, or a LIVE (``ok``) count BELOW the committed per-leg floor
    F_leg. Benign reasons (``index_missing``, ``no_embeddings``,
    ``breaker_open``) carry MEANINGLESS counts (no index / zero embedded
    points) — never dead, never floor-checked. Above-floor live counts
    with no exception/timeout are classified HEALTHY (a silent mid-cursor
    short-read above the floor is indistinguishable from a healthy
    low-count leg — the trigger stays armed rather than suppressing on an
    unobservable signal)."""
    floors = floors if floors is not None else F_LEG_FLOORS
    reason = entry.get("reason")
    if reason in ("timeout", "query_failed"):
        return True
    if reason == "empty_results" and entry.get("count", 0) == 0:
        return True
    if reason == "ok" and floors:
        floor = floors.get(entry.get("leg"))
        if floor is not None and entry.get("count", 0) < floor:
            return True
    return False


def _legs_degraded(outcome: dict, question_type: str = "", *,
                   floors: dict[str, int] | None = None) -> list[tuple[str, list]]:
    """Degraded (non-by-design) legs of an outcome, per the conservative
    leg-health predicate (#1785). Returns ``[]`` for a healthy outcome.

    By-design-zero exclusion (plan cycle3-P1-8): TR questions run with
    ``entity_types=("point", "event")`` — the event FTS leg is EMPTY BY
    DESIGN and must not count as a signal (the point leg is the non-by-design
    one; both entries share the ``fts`` trace, so a signal requires ALL fts
    entries dead on a TR question). Legless outcomes (vector-arm /
    retrieval-only — no ``legs`` key) are exempt (healthy-vacuous, never a
    KeyError), consistent with the ``retrieval_only`` exemption.
    """
    legs = outcome.get("legs")
    if not isinstance(legs, list):
        return []
    is_tr = str(question_type) == "temporal-reasoning"
    by_leg: dict[str, list[dict]] = {}
    for leg in legs:
        if isinstance(leg, dict) and leg.get("leg"):
            by_leg.setdefault(str(leg["leg"]), []).append(leg)
    degraded: list[tuple[str, list]] = []
    for name, entries in by_leg.items():
        if name == "fts" and is_tr and entries:
            if all(_leg_entry_dead(e, floors=floors) for e in entries):
                degraded.append((name, entries))
            continue
        for entry in entries:
            if _leg_entry_dead(entry, floors=floors):
                degraded.append((name, [entry]))
    return degraded


def session_recall_all_zero(sr) -> bool:
    """Type-strict all-zero predicate for a ``session_recall@k`` dict:
    bool False is an int subclass but is NOT a recorded recall value, so
    only real int/float zeros count (mirrors session_healthy's bool
    exclusion). Shared by the resume-quality gate (run.py) and the
    protocol's pre-resume scan (run_protocol.py) so the predicate can
    never drift between them."""
    return (isinstance(sr, dict) and bool(sr)
            and all(isinstance(v, (int, float))
                    and not isinstance(v, bool) and v == 0
                    for v in sr.values()))


def _retriever_from_checkpoint(data: dict,
                               forwarded: str | None = None
                               ) -> tuple[str, str | None]:
    """Resolve the retriever whose required-key set applies to a
    checkpoint — single source for the runner (``_load_checkpoint``) and
    the protocol's pre-resume scan (run_protocol.checkpoint_resume_quality)
    so the two can never report per different retrievers.

    Resolution order (first hit wins):
      - ``forwarded`` — an explicit caller retriever (the runner always
        forwards run_evaluation's retriever, default "hybrid");
      - the checkpoint's first-class top-level ``retriever`` field
        (written by ``_save_checkpoint``), validated against
        REQUIRED_OUTCOME_KEYS;
      - the ``run_key`` segment ``{surface}__{retriever}__{model}__
        {prompt}`` (older files), validated likewise;
      - "hybrid" (the runner's default).

    Returns (effective retriever, source) with source ∈ {"forwarded",
    "top-level", "run_key", "default"}."""
    if forwarded is not None:
        return forwarded, "forwarded"
    field = data.get("retriever")
    if isinstance(field, str) and field in REQUIRED_OUTCOME_KEYS:
        return field, "top-level"
    saved_key = data.get("run_key")
    if isinstance(saved_key, str):
        parts = saved_key.split("__")
        if len(parts) >= 2 and parts[1] in REQUIRED_OUTCOME_KEYS:
            return parts[1], "run_key"
    return "hybrid", "default"


# ══ PRODUCT-PARITY NOTE (eval-only) ══════════════════════════════════════
# This is a QUALITY knob that lives in the eval harness and is NOT wired
# into the product (tortoise/) path.
#   Product default: no analog — the product has no long-running batch
#       resumption surface (capture/search are request-scoped), so no
#       checkpoint-quality gate exists or is needed.
#   Why eval-only:   #1764's gate refuses to resume retrieval-dead
#       checkpointed outcomes (the pilot's 0.74 baseline was a two-
#       population blend: 20 dead-FTS-leg resumed outcomes at 0.55 + 30
#       fresh at 0.867). Landed eval-side only (commit 9aae30c2).
#   Ship-to-product: not applicable — the gate only exists where
#       checkpoint-resume exists (the eval runner); no tracking issue
#       filed.
#   Rationale:       eval-runner integrity for reproducible benchmarks —
#       the product has no batch-resumption surface to port into.
# ═════════════════════════════════════════════════════════════════════════
def resume_gate_reject_reason(outcome: dict) -> str | None:
    """#1764 resume-quality gate: why a checkpointed outcome is NOT
    resumable, or None when it passes.

    The pilot's 0.74 baseline was a two-population blend: 20 outcomes
    resumed byte-identical from a pre-crash run (accuracy 0.55; 13/20 ran
    with a dead FTS retrieval leg — fts.count=0, ``empty_results``) + 30
    fresh (0.867). FTS-empty predicts failure almost perfectly within the
    resumed set (0.31 vs 1.00). Two recorded signals mark an outcome as
    retrieval-dead:

      - the ``legs`` trace (R3 #1542 D4: {"leg", "ran", "degraded",
        "reason", "count"}) shows the FTS leg dead — a genuinely-dead
        reason with ``count == 0`` and no live fts entry (TR questions
        trace one entry per entity type, so a live point leg rescues a
        legitimately-empty event leg) AND recall data does not positively
        show the session surfaced. The full R3 (#1542) leg-reason
        vocabulary (tortoise/search_engine.py — see DEAD_LEG_REASONS /
        BENIGN_LEG_REASONS) classifies: DEAD at count==0 —
        ``empty_results`` (the pilot artifact: FTS ran clean and found
        nothing), ``query_failed`` (a real driver failure), ``timeout``
        (the strategy deadline expired — query never completed, results
        discarded); BENIGN — ``ok`` (live retrieval), ``index_missing``
        (environmental: expected in embedded FalkorDBLite with no FTS
        index; degrades quietly, never trips the breaker),
        ``no_embeddings`` (vector arm: zero embedded points),
        ``breaker_open`` (the breaker skipped the strategy). Benign
        reasons are NOT dead legs — treating them as dead would reject
        every embedded outcome on every resume (livelock). A healthy
        (non-zero) vector-rescued session (FTS empty but recall > 0) is
        NOT retrieval-dead — rejecting it would livelock, since the
        re-encode reproduces the same FTS-empty shape on the next resume;
      - every ``session_recall@k`` value is 0.0 (the session never
        surfaced at any depth).

    Signals fire ONLY on positive recorded evidence: an outcome with no
    ``legs`` trace (pre-R3 checkpoint, vector arm) or no fts entry is NOT
    refused BY THE DEAD-FTS SIGNAL (the session-zero signal still applies
    when ``session_recall@k`` is recorded all-zero) — absent data ≠ dead
    leg, and an fts entry recording ``index_missing`` (embedded
    FalkorDBLite — no FTS index) or ``breaker_open`` is benign, not dead.
    An fts entry with ``count == 0`` and a reason OUTSIDE the known
    vocabulary (future vocabulary, hand-edited files, reason=None) is NOT
    treated as dead either (fail-open — the index_missing livelock
    lesson); the vocabulary-drift event is surfaced by the CALLERS that
    own the load decision (see ``unknown_leg_reasons``) — this predicate
    is PURE, no side effects: it is the shared single source of truth for
    both the runner's ``_load_checkpoint`` and the protocol's pre-resume
    scan, and a print here would double-fire in a real ``cmd_run`` flow
    and fire in ``--dry-run`` contexts where no load decision happens.
    Legitimately session-less outcomes
    (abstention questions — ``turn_recall@k`` all None per the M6
    N/A-not-0.0 contract) are exempt: their all-zero session recall and
    legitimately empty FTS are the question's shape, not a dead backend.
    The session-zero signal is an INDEPENDENT rejection trigger per issue
    #1764 (Indicator 1 requires flagging ``session_recall@k`` all-zero on
    its own) — it is NOT only a dead-leg detector. A deterministic miss
    (live retrieval that surfaced the wrong sessions) or a vector-mode
    miss (no ``legs`` trace at all) with all-zero recall is rejected and
    re-encodes on every resume until retrieval improves — the checkpoint
    self-heals only on a successful re-encode. That fail-closed posture is
    INTENDED (re-verifying beats trusting a session that never surfaced)
    and is distinct from the abstention exemption above: an abstention is
    the question's shape (``turn_recall@k`` all None), while an all-zero
    session signal with real turn evidence means the retriever never
    surfaced the session.
    breaker_open outcomes are excluded by the caller (kept — a legitimately
    dropped question must never re-run).
    """
    if not isinstance(outcome, dict):
        return None
    # #1785 (Task 2): the graph-integrity gate's reasons refuse a resume —
    # a gated outcome (shape-OK, would otherwise load byte-identical and
    # re-enter aggregates) must never resume. The refusal consumes the
    # UNION of the phase-keyed reason lists (``gate_reasons`` = pre-
    # retrieval, ``post_retrieval_reasons`` = post-retrieval), so a
    # pre-green/post-red outcome (H6 loss between gate and retrieval) is
    # refused too. ``gate_reasons`` is NEVER a required key — read via
    # ``.get`` with a ``[]`` default so every pre-change checkpoint resumes
    # identically (the abstention exemption is a GATE-side presence skip;
    # a truncated graph on an abstention question is still integrity loss
    # and IS refused here).
    gate_reasons = outcome.get("gate_reasons") or []
    post_reasons = outcome.get("post_retrieval_reasons") or []
    for reason in list(gate_reasons) + list(post_reasons):
        if reason in GATE_REASONS:
            return f"gate-red: {reason}"
    if _legit_sessionless(outcome):
        return None  # N/A — no answer session to recall, never a dead leg
    legs = outcome.get("legs")
    sr = outcome.get("session_recall@k")
    # a recall dict with any positive numeric value proves the session
    # surfaced (a live leg produced it) — corrupt values (strings/None)
    # do NOT count as surfaced, so a dead FTS leg is still rejected on
    # corrupt recall data instead of being silently resumed.
    session_healthy = (isinstance(sr, dict) and bool(sr)
                       and any(isinstance(v, (int, float))
                               and not isinstance(v, bool)
                               and math.isfinite(v) and v > 0
                               for v in sr.values()))
    # type-strict zero: bool False is an int subclass but is NOT a recorded
    # recall value — a corrupt boolean must not count as "all zeros" (and
    # sibling session_healthy already excludes bools). Shared predicate
    # (session_recall_all_zero) — the protocol scan derives its advisory
    # zero_session count from the same source so it can never drift.
    session_zero = session_recall_all_zero(sr)
    if isinstance(legs, list):
        # TR questions trace one fts entry PER entity type (point + event
        # share the leg_trace) — the leg is dead only when no fts entry
        # shows live retrieval (a live point leg rescues a legitimately-
        # empty event leg).
        fts_entries = [leg for leg in legs
                       if isinstance(leg, dict) and leg.get("leg") == "fts"]
        # R3 (#1542) leg-reason vocabulary: only genuinely-dead reasons
        # mark the leg dead — ``empty_results`` (the pilot artifact: FTS
        # ran clean and found nothing), ``query_failed`` (a real driver
        # failure) and ``timeout`` (the strategy deadline expired — query
        # never completed, results discarded). ``index_missing`` is
        # environmental/benign (expected in embedded FalkorDBLite — no FTS
        # index; degrades quietly, does not trip the breaker),
        # ``no_embeddings`` (vector arm: zero embedded points) and
        # ``breaker_open`` (the breaker skipping the strategy) are not
        # retrieval-dead, or every embedded outcome would be rejected on
        # every resume (livelock). Tuple membership — a corrupt non-string
        # reason must compare, not TypeError the gate.
        dead_fts = [leg for leg in fts_entries
                    if leg.get("reason") in DEAD_LEG_REASONS
                    and leg.get("count") == 0]
        # #1764/code-review: an fts entry with count==0 and a reason
        # OUTSIDE the known vocabulary (future vocabulary, hand-edited
        # files, reason=None) is NOT dead — the ``dead_fts`` filter above
        # only matches DEAD_LEG_REASONS (fail-open; the index_missing
        # livelock lesson). The predicate is PURE (no side effects — it is
        # the shared single source of truth for the runner and the
        # protocol scan), so the vocabulary-drift event is surfaced by the
        # callers via ``unknown_leg_reasons`` — never printed here.
        live_fts = any(leg.get("reason") == "ok"
                       and leg.get("count", 0) > 0 for leg in fts_entries)
        # The dead-FTS signal fires only when the session did NOT
        # positively surface: a healthy vector leg that rescued the session
        # (session_recall > 0) is NOT retrieval-dead — rejecting it would
        # livelock, since the re-encode reproduces the same FTS-empty shape
        # on the next resume.
        if dead_fts and not live_fts and not session_healthy:
            return "fts.count=0 (dead FTS retrieval leg)"
    # #1764: the session-zero signal is an INDEPENDENT rejection trigger,
    # not merely a dead-leg detector — a deterministic miss (live
    # retrieval surfaced the wrong sessions) or a vector-mode miss (no
    # legs trace) with all-zero recall is rejected and re-encodes on every
    # resume until retrieval improves (the checkpoint self-heals only on a
    # successful re-encode). Intended fail-closed posture — distinct from
    # the abstention exemption (a legit sessionless outcome has
    # turn_recall all-None; here real turn evidence exists but the session
    # never surfaced).
    if session_zero:
        return "session_recall@k all zeros (session never surfaced)"
    return None


def unknown_leg_reasons(outcome: dict) -> list:
    """#1764/code-review: the fts-leg reason strings OUTSIDE the known
    vocabulary with ``count == 0`` (future vocabulary, hand-edited files,
    reason=None) — the classification data behind the gate's fail-open
    unknown-reason handling (see ``resume_gate_reject_reason``). PURE
    helper, no side effects: ``resume_gate_reject_reason`` is the shared
    single source of truth called by BOTH the runner (``_load_checkpoint``)
    and the protocol scan (run_protocol.checkpoint_resume_quality), so
    vocabulary-drift surfacing belongs to the callers that own the load
    decision — this helper just answers "which unknown reasons does this
    outcome carry?".

    Returns the raw reason values in first-seen order, deduplicated
    (normally ``str``; ``None``/other corrupt values pass through raw so a
    caller's repr matches the recorded value). An unknown reason does NOT
    make the leg dead — it is fail-open (the index_missing livelock
    lesson) — it only marks vocabulary drift the callers surface loudly.
    """
    if not isinstance(outcome, dict):
        return []
    legs = outcome.get("legs")
    if not isinstance(legs, list):
        return []
    seen: list = []
    for leg in legs:
        if (isinstance(leg, dict) and leg.get("leg") == "fts"
                and leg.get("count") == 0
                and leg.get("reason") not in KNOWN_LEG_REASONS):
            reason = leg.get("reason")
            if reason not in seen:
                seen.append(reason)
    return seen


def _load_checkpoint(path: str | None,
                     expected_fingerprint: dict | None = None,
                     *, run_key: str | None = None,
                     retriever: str = "hybrid",
                     retry_failed: bool = False,
                     fold_usage: bool = False
                     ) -> tuple[dict[str, dict], list[dict]]:
    """Load (completed-by-qid, failures) from the checkpoint state file.

    M7 (#1527, D7): the loaded checkpoint's fingerprint must match the
    effective run config — a mismatch raises ``CheckpointStaleError`` naming
    the differing fields (refuse stale resume). A legacy v1 checkpoint
    (no ``fingerprint`` key) is refused too. #1349: the checkpoint also
    carries the per-model ``run_key`` (``{surface}__{retriever}__{model}__
    {prompt}``) — a cross-surface (embedded↔hnsw) or cross-model resume is
    impossible by construction. The read happens under an exclusive flock
    (D8) so a reader never sees a mid-merge file.

    #1764: the resume-quality gate runs per outcome — a completed record
    whose recorded retrieval shows a dead FTS leg (``fts.count=0``) or zero
    session recall is REJECTED (dropped from the completed set so the
    question re-encodes, mirroring the truncated-outcome path) instead of
    silently contaminating the baseline with a stale outcome. breaker_open
    outcomes are exempt (legitimately dropped — never re-run). The
    checkpoint self-heals only when the re-encode succeeds: a still-dead
    backend (or a failed re-encode) keeps the outcome rejected on every
    resume — surfaced via the run log / protocol resume-quality note so
    the dead backend is seen, never silently converged.

    #1786 (P2-12, P2-6): the corrupt-file contract CHANGED — a
    JSONDecodeError checkpoint is now QUARANTINED (guarded
    ``<name>.corrupt.<utc>`` rename) and REFUSED with an actionable error
    (never the old silent fresh-start: that discarded the failures list,
    the in_progress claim stamps, and bypassed the fingerprint gate —
    ``--retry-failed`` state evaporated). Valid-JSON-wrong-shape
    ``failures`` entries are schema-validated (P2-10/P2-3) — type-checks on
    PRESENT fields only (legacy tolerance, P2-2: missing optional keys
    are allowed so pre-feature entries pass), never an unhandled crash.

    #1786 (R3): ``retry_failed`` drives the load-time advisory warnings —
    recoverable-class failures skipped without the flag, and the per-entry
    eligibility warnings when the flag is on (never a silent skip).
    """
    if not path:
        return {}, []
    p = Path(path)
    if not p.is_file():
        return {}, []
    try:
        with flock_exclusive(p.with_suffix(p.suffix + ".lock")):
            data = json.loads(p.read_text(encoding="utf-8"))
    except TimeoutError as e:
        # D8 flock timeout — a concurrent writer holds the lock. Accurate
        # message: NOT a corrupt file (TimeoutError subclasses OSError, so
        # it must be caught BEFORE the corrupt clause or it is misreported).
        print(f"[longmem_eval] WARNING: checkpoint {p} lock busy ({e!r}) — "
              f"concurrent writer; ignoring for now — every question "
              f"re-encodes", file=sys.stderr)
        return {}, []
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as e:
        # #1786 (P2-12): refuse-and-quarantine — the file is renamed under
        # the SAME exclusive flock as _save_checkpoint (guarded rename),
        # and the loader refuses instead of silently fresh-starting (the
        # old contract discarded the failures list + claim stamps and
        # bypassed the fingerprint gate). RecursionError (a deeply nested
        # crafted JSON) quarantines + refuses identically to JSONDecodeError.
        with flock_exclusive(p.with_suffix(p.suffix + ".lock")):
            qpath = _quarantine_corrupt(p)
        raise CheckpointStaleError(
            f"checkpoint {p} is corrupt ({e!r}) — quarantined to {qpath}; "
            f"refusing to resume (fix or restore a backup; a fresh start "
            f"would discard the failures list + in-progress claims)") from e
    except OSError as e:
        raise CheckpointStaleError(
            f"checkpoint {p} is unreadable ({e!r}) — refusing to resume") from e
    fmt = data.get("format")
    saved_key = data.get("run_key")
    # #1785 (Task 2, cycle2-P2-21 / P2-10b): a checkpoint carrying a
    # run-level abort marker (``degraded_aborted`` — the mid-run watchdog
    # aborted; ``checkpoint_abort`` — a checkpoint-persist failure) is
    # REFUSED by the runner's OWN resume path — never a silent continuation
    # of an aborted run's gate-green completed questions into a 'clean'
    # baseline (two-population contamination). The protocol scan's refusal
    # shares the same marker names (run_protocol.py, parallel #1785 file).
    for _marker, _label in (("degraded_aborted", "degraded-aborted"),
                            ("checkpoint_abort", "checkpoint-abort")):
        if data.get(_marker):
            raise CheckpointStaleError(
                f"checkpoint {p} carries a {_label} run marker "
                f"({data.get(_marker)!r}) — refusing resume (the run aborted "
                f"before completing; delete the file or force a fresh run "
                f"with re-pre-flight)")
    if fmt is None and saved_key is None:
        # No #1349 format marker AND no run_key → stale #1144-era or
        # foreign. A file carrying an M7 fingerprint falls through to the
        # fingerprint gate; otherwise refuse (raise when a fingerprint is
        # expected — M7 D7 contract; warn+return on the raw keyed path).
        if data.get("fingerprint") is None:
            if expected_fingerprint is not None:
                raise CheckpointStaleError(
                    f"checkpoint {p} predates the fingerprint contract (no "
                    f"'fingerprint' key) — delete it or re-fingerprint it")
            print(f"[longmem_eval] WARNING: checkpoint {p} has no format/"
                  f"run_key/fingerprint markers (stale #1144-era or foreign) "
                  f"— ignoring; every question re-encodes", file=sys.stderr)
            return {}, []
    elif fmt is not None and fmt != CHECKPOINT_FORMAT:
        print(f"[longmem_eval] WARNING: checkpoint {p} format "
              f"{fmt!r} != {CHECKPOINT_FORMAT!r} — ignoring; every question "
              f"re-encodes", file=sys.stderr)
        return {}, []
    elif run_key is not None and saved_key is not None and saved_key != run_key:
        print(f"[longmem_eval] WARNING: checkpoint {p} belongs to run "
              f"{saved_key!r}, current run is {run_key!r} — "
              f"refusing cross-config resume (per-model checkpoint keying); "
              f"every question re-encodes", file=sys.stderr)
        return {}, []
    # #1764/code-review: cross-check the FILE-DERIVED retriever (top-level
    # ``retriever`` field → run_key segment — _retriever_from_checkpoint
    # with no forwarded value) against the FORWARDED retriever — a
    # disagreement means the caller's required-key set is derived from a
    # different retriever than the checkpoint's own claim (top-level field
    # OR run_key segment). Warn only; load behavior is unchanged (the
    # required-key set comes from the forwarded retriever, per
    # run_evaluation's call).
    file_retriever, file_source = _retriever_from_checkpoint(data, None)
    if file_retriever != retriever and file_source != "default":
        print(f"[longmem_eval] WARNING: checkpoint {p} retriever "
              f"{file_retriever!r} (from {file_source}) != forwarded "
              f"retriever {retriever!r} — required-key set derived from "
              f"{retriever!r}", file=sys.stderr)
    if expected_fingerprint is not None:
        fp = data.get("fingerprint")
        # #1349 merge: a checkpoint written by the vector-arm path carries
        # format+run_key but no M7 fingerprint — the run_key check above
        # already guards cross-config resume; fall through (no code-drift
        # gate available) instead of refusing a legitimately-keyed file.
        diffs = ([] if fp is None
                 else _fingerprint_diffs(expected_fingerprint, fp))
        if diffs:
            detail = ", ".join(
                f"{k}: {expected_fingerprint.get(k)!r} != {fp.get(k)!r}"
                for k in diffs)
            raise CheckpointStaleError(
                f"checkpoint {p} is stale: effective run config differs on "
                f"{sorted(diffs)} ({detail}) — refusing resume (delete the "
                f"file to re-run the questions)")
    # The required-key set derives from the SAME retriever resolution the
    # protocol scan uses (run_protocol.checkpoint_resume_quality —
    # _retriever_from_checkpoint, single source) so the two can never
    # drift apart. The runner always forwards (run_evaluation passes
    # retriever=retriever, default "hybrid"), so the forwarded value wins
    # here; the file branches serve callers without a forwarded retriever
    # (the scan's programmatic path).
    effective_retriever, _retriever_source = _retriever_from_checkpoint(
        data, retriever)
    required = REQUIRED_OUTCOME_KEYS.get(effective_retriever,
                                         ("question_id",))
    outcomes: dict[str, dict] = {}
    gate_rejected = 0
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
        # #1764: refuse resume of retrieval-dead outcomes — drop from the
        # completed set so the question re-encodes (single-population
        # discipline: no stale dead-leg outcomes in baselines).
        reason = resume_gate_reject_reason(o)
        # #1764/code-review: the gate predicate is PURE (no side effects —
        # it is the shared single source of truth called by both this
        # loader and the protocol's pre-resume scan; the old in-predicate
        # print double-fired in a real cmd_run flow and fired in --dry-run
        # contexts where no load decision happens), so the unknown-reason
        # vocabulary-drift warning is surfaced HERE — once per gate-
        # eligible outcome, naming qid + the unknown reason(s), same loud
        # wording as before (fail-open — NOT dead).
        unknown = unknown_leg_reasons(o)
        if unknown:
            print(f"[longmem_eval] WARNING: outcome "
                  f"{o.get('question_id')!r} fts leg has unknown "
                  f"reason(s) {', '.join(repr(r) for r in unknown)} with "
                  f"count=0 — NOT treated as dead (fail-open on unknown "
                  f"vocabulary); known reasons: {KNOWN_LEG_REASONS}",
                  file=sys.stderr)
        if reason is not None:
            gate_rejected += 1
            print(f"[longmem_eval] WARNING: checkpoint outcome "
                  f"{o.get('question_id')!r} rejected by the resume-quality "
                  f"gate ({reason}) — re-encoding just this question",
                  file=sys.stderr)
            continue
        outcomes[o["question_id"]] = o
    failures = [f for f in data.get("failures", [])
                if isinstance(f, dict) and f.get("question_id")]
    # #1786 (P2-10/P2-3): schema-validate the failures list — valid-JSON-
    # wrong-shape entries REFUSE (a string ``attempts`` would TypeError the
    # gate's ``<`` comparison, a non-dict entry would AttributeError on
    # ``.get``, a string ``in_progress.pid`` would TypeError the claim's
    # ``os.kill`` probe). Legacy tolerance (P2-2): ONLY PRESENT keys are
    # type-checked — missing optional keys (``retryable``/``attempts``/
    # ``in_progress``) are allowed so pre-feature entries pass. A MISSING
    # ``failures`` key is the defined skip-with-WARNING (empty list — the
    # historical behavior); an explicit non-list is a shape violation.
    raw_failures = data.get("failures")
    if raw_failures is None:
        print(f"[longmem_eval] WARNING: checkpoint {p} has no 'failures' "
              f"list — treating as empty", file=sys.stderr)
        raw_failures = []
    failures = _validate_failures_schema(raw_failures, checkpoint=p)
    print(f"[longmem_eval] resumed checkpoint {p}: {len(outcomes)} completed, "
          f"{len(failures)} failed (skipping both)"
          + (f"; {gate_rejected} rejected by the resume-quality gate "
             f"(re-encoding)" if gate_rejected else ""), file=sys.stderr)
    # #1786 (R3, Task 2 Steps 1-2): the load-time advisory + eligibility
    # warnings — recoverable-class failures skipped without ``--retry-failed``
    # are a LOUD event, never silent; the per-entry skip reasons warn too.
    if failures:
        recoverable = [f for f in failures
                       if f.get("error_class") == "ingest:retries_exhausted"]
        if recoverable and not retry_failed:
            print(f"[longmem_eval] WARNING: {len(recoverable)} recoverable-"
                  f"class failure(s) exist (ingest:retries_exhausted) — "
                  f"WITHOUT --retry-failed they are skipped on resume (delete "
                  f"the checkpoint to re-run them)", file=sys.stderr)
        elif retry_failed:
            for f in failures:
                reason = _retry_failed_skip_reason(f)
                if reason:
                    print(f"[longmem_eval] WARNING: --retry-failed skips "
                          f"{f.get('question_id')!r}: {reason}",
                          file=sys.stderr)
    # #2185 (A4): the kill-9 read-back. The collector starts EMPTY in a
    # fresh process — fold the checkpoint's ``usage_overhead`` snapshot and
    # then reconcile every record that carries a usage replica (failure
    # entries + breaker-open outcomes) so the resumed report's overhead
    # section shows the FULL historical spend. Shortfall-only per bucket
    # (never the overlap) — idempotent on resume.
    if fold_usage:
        _fold_checkpoint_usage(data, outcomes, failures)
    return outcomes, failures


def _fold_checkpoint_usage(data: dict, outcomes: dict[str, dict],
                           failures: list[dict]) -> None:
    """A4 kill-9-window read-back for #2185 — see _load_checkpoint."""
    collector = lme_usage.get_collector()
    payload = data.get("usage_overhead")
    if isinstance(payload, dict) and payload:
        collector.merge_overhead_payload(payload)
    for o in outcomes.values():
        if not o.get("breaker_open"):
            continue
        rep = o.get("usage")
        if isinstance(rep, dict) and isinstance(rep.get("by_stage"), dict):
            collector.fold_replica(o["question_id"], rep["by_stage"])
    for f in failures:
        rep = f.get("usage")
        if isinstance(rep, dict) and isinstance(rep.get("by_stage"), dict):
            collector.fold_replica(f["question_id"], rep["by_stage"])


def _validate_failures_schema(failures_raw: Any, *, checkpoint: Path) -> list[dict]:
    """#1786 (P2-10/P2-3): schema validation for the checkpoint's
    ``failures`` list — REFUSES (via ``CheckpointStaleError``) on shape
    violations instead of crashing the resume gate. Type-checks ONLY
    PRESENT fields (legacy tolerance P2-2); recurses INTO the
    ``in_progress`` dict (pid must be an int — a string pid would TypeError
    the claim's ``os.kill(pid, 0)`` probe; ``in_progress_utc`` must parse as
    ISO-8601 — an unparseable value would ValueError the age computation)."""
    if not isinstance(failures_raw, list):
        raise CheckpointStaleError(
            f"checkpoint {checkpoint} 'failures' must be a list, got "
            f"{type(failures_raw).__name__} — refusing load")
    out: list[dict] = []
    for f in failures_raw:
        if not isinstance(f, dict):
            raise CheckpointStaleError(
                f"checkpoint {checkpoint} failure entry is not a dict: {f!r} "
                f"— refusing load")
        if not f.get("question_id"):
            continue
        attempts = f.get("attempts")
        if attempts is not None and (isinstance(attempts, bool)
                                     or not isinstance(attempts, int)):
            raise CheckpointStaleError(
                f"checkpoint {checkpoint} failure {f.get('question_id')!r} "
                f"attempts must be int, got {attempts!r} — refusing load")
        # #1786 (code-review F7): the gate's only budget check is
        # ``attempts >= cap`` (False for negatives), so a crafted negative
        # attempts would otherwise be re-attempted N+2 times across resumes.
        # Clamp the range (a sane upper bound refuses pathological crafted
        # values too — the cap is small, so anything far past it is corrupt).
        if attempts is not None and (attempts < 0 or attempts > 1_000_000):
            raise CheckpointStaleError(
                f"checkpoint {checkpoint} failure {f.get('question_id')!r} "
                f"attempts out of range, got {attempts!r} — refusing load")
        retryable = f.get("retryable")
        if retryable is not None and not isinstance(retryable, bool):
            raise CheckpointStaleError(
                f"checkpoint {checkpoint} failure {f.get('question_id')!r} "
                f"retryable must be bool, got {retryable!r} — refusing load")
        in_progress = f.get("in_progress")
        if in_progress is not None:
            if not isinstance(in_progress, dict):
                raise CheckpointStaleError(
                    f"checkpoint {checkpoint} failure "
                    f"{f.get('question_id')!r} in_progress must be a dict, "
                    f"got {in_progress!r} — refusing load")
            pid = in_progress.get("pid")
            if pid is not None and (isinstance(pid, bool)
                                    or not isinstance(pid, int)):
                raise CheckpointStaleError(
                    f"checkpoint {checkpoint} failure {f.get('question_id')!r} "
                    f"in_progress.pid must be int, got {pid!r} — refusing load")
            ts = in_progress.get("in_progress_utc")
            if ts is not None:
                try:
                    datetime.fromisoformat(ts)
                except (TypeError, ValueError) as e:
                    raise CheckpointStaleError(
                        f"checkpoint {checkpoint} failure {f.get('question_id')!r} "
                        f"in_progress_utc unparseable: {ts!r} — refusing load") from e
        out.append(f)
    return out


def _merge_checkpoint(path: Path, outcomes: list[dict],
                      failures: list[dict], *,
                      remove_failures: list[str] | None = None
                      ) -> tuple[list[dict], list[dict]]:
    """Merge the on-disk checkpoint with the in-memory snapshot (M7 #1527,
    D8 — cross-process merge-under-lock): outcomes dict-by-qid (the fresh
    in-memory outcome wins on tie), failures append-only by qid. A missing
    disk file → the in-memory snapshot wins (fresh start).

    #1786 (P2-8): a CORRUPT disk base is QUARANTINED and REFUSED — the
    in-memory snapshot never silently wins over a corrupt base via
    ``os.replace`` (that dropped on-disk-only claim stamps + failure
    entries). #1786 (P1-3/P2-1): read-through reconciliation —
    ``remove_failures`` tombstones are honored (a removed entry stays
    removed), a live ``in_progress`` claim stamp on the disk base survives
    a stale unstamped in-memory copy (the CAS protection is never erased),
    and the ``attempts`` counter reconciles with MAX semantics (a
    concurrent process's increment is never regressed below the disk value
    — a capped qid must not be re-admitted for an unbudgeted re-burn)."""
    if path.is_file():
        data = _read_checkpoint_or_quarantine(path)
        disk_out = {o["question_id"]: o for o in data.get("outcomes", [])
                    if isinstance(o, dict) and o.get("question_id")}
        disk_fail = {f["question_id"]: f for f in data.get("failures", [])
                     if isinstance(f, dict) and f.get("question_id")}
    else:
        disk_out, disk_fail = {}, {}
    merged_out = {**disk_out, **{o["question_id"]: o for o in outcomes}}
    merged_fail = dict(disk_fail)
    for f in failures:
        qid = f["question_id"]
        disk = merged_fail.get(qid)
        if disk is None:
            merged_fail[qid] = f
            continue
        merged = {**disk, **f}
        # Counter monotonicity (P2-1): the disk-base attempts wins on tie
        # when greater — a concurrent process's later save must not regress
        # a capped qid below the disk value.
        da, ma = disk.get("attempts"), f.get("attempts")
        if isinstance(da, int) and isinstance(ma, int) and da > ma:
            merged["attempts"] = da
        # Live claim stamp preservation (P1-3): a stale unstamped in-memory
        # copy must not overwrite a live on-disk claim.
        if _stamp_live(disk.get("in_progress")):
            merged["in_progress"] = disk["in_progress"]
        merged_fail[qid] = merged
    for qid in (remove_failures or []):
        merged_fail.pop(qid, None)
    return list(merged_out.values()), list(merged_fail.values())


def _save_checkpoint(path: str | None, outcomes: list[dict],
                     failures: list[dict], fingerprint: dict | None = None, *,
                     run_key: str | None = None, surface: str | None = None,
                     retriever: str | None = None, model: str | None = None,
                     prompt: str | None = None,
                     remove_failures: list[str] | None = None,
                     degraded_aborted: dict | None = None,
                     checkpoint_abort: dict | None = None,
                     usage_overhead: dict | None = None) -> None:
    """Atomically persist partial results after each question (resume).

    M7 (#1527, D7/D8): writes the code fingerprint; the write happens under
    an exclusive flock with a re-read-and-merge, so two concurrent run
    PROCESSES sharing one checkpoint lose nothing (each merge adds its
    qids). ``os.replace`` keeps the final file atomic.

    #1786 (P2-10): ``remove_failures`` prunes the named qids' failure
    entries IN THE SAME flocked write as the outcome save — the atomic
    single-write that makes ``--retry-failed`` remove-on-success correct
    (a kill -9 between an outcome write and a separate removal write would
    leave a stale failure entry for a completed qid → the next resume
    re-burns a completed question and the report misgrades it).

    #1785 (P2-10/P1-7): ``degraded_aborted``/``checkpoint_abort`` ride the
    SAME flocked write as the final outcomes — a run-level abort marker is
    persisted atomically WITH the final checkpoint write (no persist-race
    window where gate-green outcomes resume into a 'clean' continuation).
    """
    if not path:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with flock_exclusive(p.with_suffix(p.suffix + ".lock")):
        merged_outcomes, merged_failures = _merge_checkpoint(
            p, outcomes, failures, remove_failures=remove_failures)
        _write_checkpoint_locked(
            p, merged_outcomes, merged_failures, fingerprint,
            run_key=run_key, surface=surface, retriever=retriever,
            model=model, prompt=prompt,
            degraded_aborted=degraded_aborted,
            checkpoint_abort=checkpoint_abort,
            usage_overhead=usage_overhead)


def _write_checkpoint_locked(
        p: Path, outcomes: list[dict], failures: list[dict],
        fingerprint: dict | None = None, *, run_key: str | None = None,
        surface: str | None = None, retriever: str | None = None,
        model: str | None = None, prompt: str | None = None,
        degraded_aborted: dict | None = None,
        checkpoint_abort: dict | None = None,
        usage_overhead: dict | None = None) -> None:
    """Inline atomic checkpoint write (tmp + ``os.replace``) for callers
    ALREADY holding the checkpoint flock (P2-1 flock-reentrancy pin: never
    call ``_save_checkpoint`` from inside a held flock — the nested
    ``flock_exclusive`` would spin 5 s and raise ``TimeoutError`` (an
    OSError subclass, misreportable as corruption))."""
    payload: dict = {
        "format": CHECKPOINT_FORMAT,
        "run_key": run_key,
        "surface": surface,
        "retriever": retriever,
        "model": model or "default",
        "prompt": prompt or "default",
        "fingerprint": fingerprint,
        "outcomes": outcomes,
        "failures": failures,
        "updated_at_utc": _utc_now().isoformat(),
    }
    # #1785: run-level abort markers ride the checkpoint top level
    # (additive — absent on healthy runs; the resume-scan + the runner's
    # own load path refuse them, plan P2-10b/P1-3).
    if degraded_aborted is not None:
        payload["degraded_aborted"] = degraded_aborted
    if checkpoint_abort is not None:
        payload["checkpoint_abort"] = checkpoint_abort
    # #2185: the collector's overhead snapshot (keyless/preflight rows +
    # moved failed/breaker qids) rides the checkpoint — present only when
    # non-empty (pre-change and mock checkpoints stay byte-identical). The
    # keyless sentinel is spelled ``__preflight__`` here (load normalizes).
    if usage_overhead:
        payload["usage_overhead"] = usage_overhead
    _write_json_atomic(p, payload)


# ═══════════════════════════════════════════════════════════════════════════
# #1785 graph-integrity run machinery — watchdog, live-run markers,
# checkpoint-persist retry, per-session census replay (Task 3), and the
# falsification-trigger predicate (Task 5 consumes it, does not build it).
# Plan: docs/plans/2026-08-27-1785-session-recall.md.
# ═══════════════════════════════════════════════════════════════════════════

class WatchdogAbortError(RuntimeError):
    """Mid-run watchdog abort — the run is degrading and can never certify.

    The run aborts early with a DISTINCT reason (plan Task 1 Step 5) and
    the ``degraded_aborted`` marker is recorded on the checkpoint — never a
    bare traceback, never a 4.5h burn to a guaranteed-fail verdict.
    """

    def __init__(self, reason: str):
        super().__init__(f"run aborted by watchdog: {reason}")
        self.reason = reason


class CheckpointPersistError(RuntimeError):
    """Checkpoint persist failed after retry-N-with-backoff (plan P2-10).

    The run aborts with a DISTINCT ``checkpoint_abort`` marker on the
    checkpoint (never a bare traceback); a resume refuses it (Task 2).
    """


#: Watchdog rolling-window length (questions) — the latency and gate-red
#: windows are GLOBAL across workers (plan cycle2-P2-24).
_WATCHDOG_WINDOW = 10
#: Strategy-timeout-rate arm: >= 2 timeouts in the last-10 window.
_WATCHDOG_TIMEOUT_RATE = 2
#: Gate-red-fraction arm: > 0.25 of the last-10 window.
_WATCHDOG_GATE_RED_FRACTION = 0.25
#: Consecutive-census_error arm: >= 3 in a row (per worker).
_WATCHDOG_CONSEC_CENSUS_ERROR = 3
#: Mid-write failure-rate arm: cumulative failures > 0.05 with min count 2
#: (min-count-2 waived for short runs where one failure already exceeds the
#: rate — n < 20; plan cycle4-P1-12/P1-15).
_WATCHDOG_FAILURE_RATE = 0.05
_WATCHDOG_FAILURE_MIN = 2
_WATCHDOG_FAILURE_MIN_WAIVE_N = 20


class _RunWatchdog:
    """Task 1 Step 5 mid-run watchdog — six in-process signals.

      * strategy-timeout rate >= 2 in the last 10;
      * gate-red fraction > 0.25 in the last 10 (gate-red = the UNION of
        ``gate_reasons`` + ``post_retrieval_reasons`` EXCLUDING the
        data-availability classes (``dataset_join_error`` — #1900: a join
        failure flags the DATASET, never graph health; plan P1-5);
      * >= 3 consecutive ``census_error`` (per-worker by nature);
      * census-query latency p95 > 2x Q (100 ms) across the last 10
        (SUCCESSFUL-read latency only — retried reads are excluded, P2-9);
      * mid-write failure-rate: cumulative n_failures / n_questions > 0.05
        (SCOPED — fires only when the run has gated outcomes OR is a
        revalidation run; a gated-free recoverable-failure run keeps the
        #1776 certifier semantics un-aborted, cycle4-P1-12);
      * leg-deadness: >= 2 consecutive questions with ANY non-by-design leg
        degraded (below F_leg / empty / exception / timeout) — the run's
        DOMINANT degradation signature (15/46 FTS-leg-empty outcomes).

    Data-availability classification (#1900): the gate-red / hard-census
    arms count DEGRADATION reasons only — ``dataset_join_error`` (a
    data-availability class in ``DATA_AVAILABILITY_GATE_REASONS``) FLAGS a
    question the DATASET cannot resolve/grade, never a truncated/missing
    graph. The flag is NON-ABORTING: the revalidate-mode first-gate-red
    abort and the non-revalidate hard-census abort must NOT fire on it (a
    healthy-pool false positive aborted reval3 at finalize). It IS still
    counted toward the whole-run gated-coverage bound (a
    data-availability-heavy run cannot certify), and the report grades it
    UNCHANGED — the flag adds no error classes and does not itself flip
    ``valid``: a join-error outcome with healthy ingest carries
    ``error_classes={}`` / ``valid=True`` and grades CLEAN; one whose
    ingest also faulted grades via its ingest errors (permanent/structural
    ingest faults hard; transient-only rate-limited recoverable) — a
    flag, not an exclusion.

    Thresholds are evaluated with a CUMULATIVE degradation accumulator —
    a recovery window does NOT reset prior degradation to zero (sawtooth
    cannot evade; P1-8); the abort condition for each rolling arm is the
    window signal holding with a cumulative crossing count >= 2 (a single
    one-off crossing window never aborts; two arms crossing in the same
    window produce EXACTLY ONE abort — the first signal checked wins).

    Mode-aware arms (P1-2/cycle4-P1-15): under ``revalidate=True`` the
    watchdog aborts on the FIRST gate-red / FIRST strategy timeout / FIRST
    mid-write failure (single-outcome arms — the run can never certify, so
    no burn to a guaranteed-fail verdict); under ``revalidate=False`` it
    adds run-can-never-certify cumulative aborts aligned to the certifier's
    whole-run bounds (strategy-timeout at bound+1, the FIRST hard census
    class, and the first gated outcome pushing the running whole-run gated
    fraction past the shared ``TORTOISE_LME_GATE_MAX_GATED`` bound).
    """

    def __init__(self, *, revalidate: bool, n_questions: int):
        self.revalidate = bool(revalidate)
        self.n_questions = max(1, n_questions)
        self._lock = threading.Lock()
        self._window: list[dict] = []
        self._crossings: dict[str, int] = {}
        self._gate_red_total = 0
        self._n_gated_total = 0
        self._n_hard_invalid = 0
        self._timeout_total = 0
        self._failures = 0
        self._n_seen = 0
        self._aborted = False
        # strategy-timeout cumulative bound: bound+1 (the 3rd for the 50-Q
        # run, the 2nd for a 10-Q smoke; plan cycle4-P1-15).
        self._timeout_bound = max(1, round(0.04 * self.n_questions)) + 1

    # ── signal recording (called per completed question) ──
    def record(self, *, qid: str, gate_reasons: list[str],
               post_retrieval_reasons: list[str], strategy_timeout: bool,
               census_latency_ms: float | None, consec_census_error: int,
               legs_degraded: bool) -> str | None:
        """Record one completed question's signals; returns an abort
        reason (str) when the watchdog fires, else None. EXACTLY ONE abort
        record per run (``_aborted`` latches)."""
        with self._lock:
            if self._aborted:
                return None
            gate_red = bool(gate_reasons or post_retrieval_reasons)
            # #1900: data-availability gate-reds (``dataset_join_error`` —
            # the DATASET cannot resolve/grade the question) are NOT
            # degradation signals. The gate-red / hard-census arms count
            # DEGRADATION reasons only; the whole-run gated-coverage bound
            # keeps ALL gate-reds (a data-availability-heavy run still
            # cannot certify).
            _all_reasons = list(gate_reasons or []) + list(
                post_retrieval_reasons or [])
            degradation_red = [r for r in _all_reasons
                               if r not in DATA_AVAILABILITY_GATE_REASONS]
            hard = [r for r in degradation_red if r in HARD_GATE_REASONS]
            if gate_red:
                self._n_gated_total += 1
            if degradation_red:
                self._gate_red_total += 1
            if hard:
                self._n_hard_invalid += 1
            if strategy_timeout:
                self._timeout_total += 1
            self._n_seen += 1
            self._window.append({
                "gate_red": bool(degradation_red),
                "timeout": bool(strategy_timeout),
                "latency": census_latency_ms,
                "legs_degraded": bool(legs_degraded),
            })
            if len(self._window) > _WATCHDOG_WINDOW:
                self._window.pop(0)
            reason = self._abort_reason(
                consec_census_error=consec_census_error,
                legs_degraded=bool(legs_degraded))
            if reason:
                self._aborted = True
            return reason

    def record_failure(self) -> str | None:
        """Record a mid-write failure (failures never enter outcomes — this
        is the only arm that samples them). Returns an abort reason when
        the (scoped) failure-rate arm fires."""
        with self._lock:
            if self._aborted:
                return None
            if self.revalidate:
                self._aborted = True
                return "mid_write_failure"
            # SCOPED (cycle4-P1-12): fires only when the run has gated
            # outcomes — a gated-free recoverable-failure run keeps the
            # #1776 certifier semantics un-aborted.
            if self._n_gated_total == 0:
                return None
            self._failures += 1
            rate = self._failures / self.n_questions
            min_count = (_WATCHDOG_FAILURE_MIN
                         if self.n_questions >= _WATCHDOG_FAILURE_MIN_WAIVE_N
                         else 1)
            if self._failures >= min_count and rate > _WATCHDOG_FAILURE_RATE:
                self._aborted = True
                return "mid_write_failure"
            return None

    # ── internal: evaluate the arms (first-wins; exactly one record) ──
    def _abort_reason(self, *, consec_census_error: int,
                      legs_degraded: bool) -> str | None:
        if self.revalidate:
            # single-outcome arms
            if self._gate_red_total >= 1:
                return "gate_red"
            if self._timeout_total >= 1:
                return "strategy_timeout"
            return None
        # run-can-never-certify cumulative arms (non-revalidate)
        if self._timeout_total >= self._timeout_bound:
            return "strategy_timeout"
        if self._n_hard_invalid >= 1:
            return "census_error"
        if self._n_seen and self._n_gated_total / self.n_questions > GATE_MAX_GATED:
            return "gated_fraction"
        # rolling-window arms are INERT on runs < 10 questions (plan
        # cycle2-P3: the whole-run cumulative + revalidate single-outcome
        # arms cover short runs — a 5-Q smoke must not abort on a
        # naturally-short small-graph leg).
        if self.n_questions < 10:
            return None
        # rolling window arms (window signal + cumulative crossing >= 2)
        if (len(self._window) == _WATCHDOG_WINDOW
                and sum(1 for w in self._window if w["timeout"])
                >= _WATCHDOG_TIMEOUT_RATE):
            self._bump("timeout_rate")
            if self._crossings["timeout_rate"] >= 2:
                return "strategy_timeout"
        if (len(self._window) == _WATCHDOG_WINDOW
                and (sum(1 for w in self._window if w["gate_red"])
                     / _WATCHDOG_WINDOW) > _WATCHDOG_GATE_RED_FRACTION):
            self._bump("gate_red_fraction")
            if self._crossings["gate_red_fraction"] >= 2:
                return "gate_red"
        if consec_census_error >= _WATCHDOG_CONSEC_CENSUS_ERROR:
            self._bump("consec_census_error")
            if self._crossings["consec_census_error"] >= 2:
                return "census_error"
        latencies = [w["latency"] for w in self._window
                     if w["latency"] is not None]
        if len(latencies) >= 5 and _p95(latencies) > 2 * GATE_QUERY_Q_MS:
            self._bump("latency_p95")
            if self._crossings["latency_p95"] >= 2:
                return "latency"
        if legs_degraded and len(self._window) >= 2                 and all(w["legs_degraded"] for w in self._window[-2:]):
            self._bump("leg_dead")
            if self._crossings["leg_dead"] >= 2:
                return "leg_dead"
        return None

    def _bump(self, signal: str) -> None:
        self._crossings[signal] = self._crossings.get(signal, 0) + 1


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, max(0, round(0.95 * len(s)) - 1))]


# ── live-run markers (plan cycle4-P1-13 / cycle4-P2-37) ────────────────────
#: Out-of-band JSON sentinel keyed by per-question namespace in the work
#: dir (NOT a Point property — §9 no-schema-changes). Content: run_key +
#: pid + launch/heartbeat UTC. A marker with no heartbeat within the TTL
#: is STALE and auto-cleared with a warning; a LIVE marker owned by a
#: different process means a peer is mid-question — the fresh-run
#: namespace cleanup REFUSES (never clobber a peer's in-flight graph).


def _marker_file(work_dir: str | None, namespace: str) -> Path | None:
    if not work_dir or not namespace:
        return None
    return Path(work_dir) / "lme_markers" / f"{namespace}.json"


def _marker_live(path: Path, *, ttl_min: int | None = None) -> bool:
    """True when the marker file exists and its heartbeat is fresh."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    hb = data.get("heartbeat_utc")
    if not isinstance(hb, str):
        return False
    ttl = (ttl_min if ttl_min is not None
           else GATE_MARKER_TTL_MIN)
    try:
        age_min = (datetime.now(UTC) - datetime.fromisoformat(hb)).total_seconds() / 60
    except ValueError:
        return False
    return age_min <= ttl


def _namespace_cleanup_allowed(work_dir: str | None, namespace: str,
                               run_key: str | None) -> bool:
    """#1785 Task 1 Step 2 cleanup-site guard: cleanup proceeds unless a
    LIVE peer marker exists (a peer is mid-question on this namespace).
    Our OWN process marker (same pid) never blocks the cleanup. A stale
    marker is auto-cleared with a warning (the owner crashed). Returns
    True (cleanup allowed) or False (refused — never clobbered)."""
    path = _marker_file(work_dir, namespace)
    if path is None:
        return True  # no marker surface (work_dir unset) — residual stated
    if not path.exists():
        return True
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True
    if data.get("pid") == os.getpid():
        return True  # our own marker (resume continuation) — allowed
    if _marker_live(path):
        print(f"[longmem_eval] WARNING: live run marker on namespace "
              f"{namespace!r} owned by pid {data.get('pid')} — refusing "
              f"fresh-namespace cleanup (a peer is mid-question); leftover "
              f"nodes stay (presence tier remains primary)",
              file=sys.stderr)
        return False
    # stale marker → auto-clear with a warning
    print(f"[longmem_eval] WARNING: stale run marker on namespace "
          f"{namespace!r} (owner pid {data.get('pid')} gone) — clearing",
          file=sys.stderr)
    with contextlib.suppress(OSError):
        path.unlink()
    return True


def _write_run_marker(work_dir: str | None, namespace: str | None,
                      run_key: str | None) -> None:
    """Write/refresh the run's own marker for a namespace (heartbeat).

    Refuses to OVERWRITE a LIVE foreign-pid marker (defense-in-depth on the
    cleanup guard's check-before-write ordering — a peer mid-question on
    the same namespace must never have its live marker clobbered by our
    heartbeat refresh; plan cycle3-P2-33 / cycle4-P2-37)."""
    if not work_dir or not namespace:
        return
    path = _marker_file(work_dir, namespace)
    try:
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
            if (data.get("pid") != os.getpid() and _marker_live(path)):
                return  # a live peer's marker — never clobber it
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "run_key": run_key,
            "pid": os.getpid(),
            "heartbeat_utc": datetime.now(UTC).isoformat(),
        }), encoding="utf-8")
    except OSError:
        pass  # marker is advisory — a write failure never aborts the run


def _clear_run_marker(work_dir: str | None, namespace: str | None) -> None:
    """Decommission OUR OWN live marker (pid-checked) — a peer's live
    marker on the same namespace is never unlinked (review P1: the run-end
    clear is pid-blind otherwise and could delete a concurrent peer's
    marker, resurrecting the clobber hazard)."""
    if not work_dir or not namespace:
        return
    path = _marker_file(work_dir, namespace)
    with contextlib.suppress(OSError):
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return
            if data.get("pid") == os.getpid():
                path.unlink()


# ── checkpoint-persist retry (plan P2-10) ──────────────────────────────────
#: Checkpoint persist retry count + backoff (seconds).
CHECKPOINT_SAVE_RETRIES = 3
_CHECKPOINT_BACKOFF_BASE_S = 0.5


def _save_checkpoint_safe(path: str | None, outcomes: list[dict],
                          failures: list[dict], fingerprint: dict | None = None,
                          *, run_key: str | None = None,
                          surface: str | None = None,
                          retriever: str | None = None,
                          model: str | None = None,
                          prompt: str | None = None,
                          remove_failures: list[str] | None = None,
                          degraded_aborted: dict | None = None,
                          checkpoint_abort: dict | None = None,
                          usage_overhead: dict | None = None) -> None:
    """_save_checkpoint with retry-N-with-backoff; on exhaustion raises
    CheckpointPersistError (never a bare traceback; the run-level marker
    is recorded on the checkpoint so a resume refuses it — Task 2)."""
    attempt = 0
    while True:
        try:
            _save_checkpoint(
                path, outcomes, failures, fingerprint, run_key=run_key,
                surface=surface, retriever=retriever, model=model,
                prompt=prompt, remove_failures=remove_failures,
                degraded_aborted=degraded_aborted,
                checkpoint_abort=checkpoint_abort,
                usage_overhead=usage_overhead)
            return
        except Exception as e:  # noqa: BLE001, RUF100
            attempt += 1
            if attempt > CHECKPOINT_SAVE_RETRIES:
                raise CheckpointPersistError(
                    f"checkpoint persist failed after "
                    f"{CHECKPOINT_SAVE_RETRIES} retries ({e!r})") from e
            time.sleep(_CHECKPOINT_BACKOFF_BASE_S * attempt)


def _record_run_abort(path: str | None, *, marker: str, detail: dict,
                      outcomes: list[dict], failures: list[dict],
                      fingerprint: dict | None = None, run_key: str | None = None,
                      surface: str | None = None, retriever: str | None = None,
                      model: str | None = None, prompt: str | None = None) -> None:
    """Best-effort persist of a run-level abort marker (``degraded_aborted``
    or ``checkpoint_abort``) onto the checkpoint so a resume refuses it —
    atomic WITH the final checkpoint write (plan P1-7: no persist-race
    window where gate-green outcomes resume into a 'clean' continuation)."""
    if not path:
        return
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with flock_exclusive(p.with_suffix(p.suffix + ".lock")):
            data = {}
            if p.is_file():
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    data = {}
            data[marker] = {**detail, "utc": _utc_now().isoformat()}
            data["outcomes"] = outcomes
            data["failures"] = failures
            if fingerprint is not None:
                data["fingerprint"] = fingerprint
            _write_json_atomic(p, data)
    except Exception:  # noqa: BLE001, RUF100
        pass  # best-effort — the abort still propagates loudly


# ── Task 3: per-session census replay (loss-location diagnostic) ───────────
#: Verdict states (plan Task 3 acceptance): H6a (never-durably-written under
#: load), H6b (post-ingest removal — GC/durability; H6c UNREACHABLE by this
#: protocol — no restart cycle, so 'loss only post-ingest' is NECESSARILY
#: H6b), INCONCLUSIVE (signature reproduced, no loss), and 'H6a unexercised
#: — env remediation only' (failure signature NOT reproduced — explicitly
#: NOT a passing verdict, plan P2-3).
REPLAY_VERDICT_H6A = "H6a"
REPLAY_VERDICT_H6B = "H6b"
REPLAY_VERDICT_INCONCLUSIVE = "INCONCLUSIVE"
REPLAY_VERDICT_UNEXERCISED = "H6a unexercised — env remediation only"


def replay_verdict(trace: dict, gc_events: list | None = None) -> str:
    """Task 3 decision rule (pure): classify the loss stage from the
    per-session census trace.

      * loss accumulating DURING ingest (any Phase-A census observed < its
        turns+chunks expectation) → H6a (never-durably-written), UNLESS a
        GC/fork-compaction event sits inside the loss window (a fork-GC
        removal inside the narrow per-session window is indistinguishable
        from never-written at that check — the GC capture is required for
        BOTH verdict branches, final-verification P2) → then H6b;
      * loss appearing only between the per-session censuses and the post-
        ingest census → H6b (H6c is UNREACHABLE — no restart cycle in the
        protocol, plan P2-2);
      * no loss reproduced → INCONCLUSIVE when the failure signature WAS
        reproduced; otherwise 'H6a unexercised — env remediation only'
        (explicitly NOT passing, plan P2-3/P2-6).
    """
    gc = gc_events or []
    sessions = trace.get("sessions") or {}
    during_ingest_loss = False
    loss_sis: list[int] = []
    for si, s in sessions.items():
        if not isinstance(s, dict):
            continue
        exp_a = s.get("phase_a_expected")
        obs_a = s.get("phase_a_observed")
        if (isinstance(exp_a, int) and isinstance(obs_a, int)
                and obs_a < exp_a):
            during_ingest_loss = True
            loss_sis.append(int(si))
    if during_ingest_loss:
        if any(gc_in_window(gc, si) for si in loss_sis):
            return REPLAY_VERDICT_H6B
        return REPLAY_VERDICT_H6A
    post = trace.get("post_ingest") or {}
    exp_p = post.get("expected")
    obs_p = post.get("observed")
    if (isinstance(exp_p, int) and isinstance(obs_p, int)
            and obs_p < exp_p):
        return REPLAY_VERDICT_H6B
    if trace.get("signature_reproduced"):
        return REPLAY_VERDICT_INCONCLUSIVE
    return REPLAY_VERDICT_UNEXERCISED


def gc_in_window(gc_events: list, si: int) -> bool:
    """True when any GC/fork-compaction event is attributable to the loss
    window of session ``si`` (events carry a session index or timestamp;
    a timestamp-only event counts when the trace is post-hoc correlated)."""
    for ev in gc_events:
        if isinstance(ev, dict) and ev.get("si") == si:
            return True
    return bool(gc_events)  # timestamp-only capture → conservative


class _PerSessionCensus:
    """Task 3 per-session census — interleaves with ingest via the shared
    query-wrapper seam (retrieve.install_gate_fault_proxy; the #1744
    dual-copy caveat: the replay runs with ``session_workers=1`` sequential,
    so the shared live copy is the one exercised). Detects session
    boundaries by the deterministic id pattern ``lme:{qid}:s{si}`` in write
    params; after each session's Phase A (raw turn/chunk) batch and Phase C
    (payload) batch, runs a per-session census (read-verified — a partial
    read is labeled read-fault and retried; only a stable two-read consensus
    counts as loss, plan Task 3). The census's OWN reads carry no id-pattern
    params (``{"q": ..., "si": ...}``), so the wrapper forwards them without
    re-triggering boundary detection (no recursion). The trace + verdict are
    written to a diagnostic JSON under the work dir. Diagnostic-only —
    fingerprint-excluded (plan P3)."""

    _SI_RE = re.compile(r"lme:[^:]+:s(\d+)")

    def __init__(self, proj: Any, qid: str, question: dict,
                 chunk_turns: int, work_dir: str | None = None,
                 signature_reproduced: bool = False,
                 gc_events: list | None = None):
        self.proj = proj
        self.qid = qid
        self.question = question
        self.chunk_turns = chunk_turns
        self.work_dir = work_dir
        self.signature_reproduced = bool(signature_reproduced)
        self.gc_events = gc_events or []
        self.sessions: dict[str, dict] = {}
        self.post_ingest: dict = {}
        self.faults: list[dict] = []
        self._current_si: int | None = None
        self._batch_no: dict[int, int] = {}
        self._prior_proxy = None

    def _si_of(self, params: dict | None) -> int | None:
        if not params:
            return None
        for v in params.values():
            if isinstance(v, str):
                m = self._SI_RE.search(v)
                if m:
                    return int(m.group(1))
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, str):
                        m = self._SI_RE.search(item)
                        if m:
                            return int(m.group(1))
        return None

    def _wrapped(self, cypher: str, params: dict | None = None,
                 timeout=None):
        si = self._si_of(params)
        if si is not None:
            if self._current_si is not None and si != self._current_si:
                self._census(self._current_si)
            self._current_si = si
        return self._real_query(cypher, params=params, timeout=timeout)

    def __enter__(self) -> _PerSessionCensus:
        # ``proj.g`` is a _GuardedGraph (__slots__ — its ``query`` cannot be
        # shadowed); the underlying falkordb Graph handle ``proj.g._g`` is a
        # plain object whose ``query`` method CAN be shadowed. The census's
        # OWN reads carry no id-pattern params ({"q", "si"}) so the wrapper
        # forwards them without re-triggering boundary detection (no
        # recursion).
        graph = self.proj.g
        self._real_query = graph._g.query
        graph._g.query = self._wrapped
        return self

    def __exit__(self, *exc) -> bool:
        self.proj.g._g.query = self._real_query
        if self._current_si is not None:
            self._census(self._current_si)
        return False

    # ── per-session census (read-verified) ──
    def _census(self, si: int) -> None:
        batch = self._batch_no.get(si, 0)
        self._batch_no[si] = batch + 1
        phase = "phase_a" if batch == 0 else "phase_c"
        sessions_list = self.question.get("haystack_sessions") or []
        if si < len(sessions_list):
            n_turns = len(sessions_list[si])
            n_chunks = len(_session_chunk_windows(
                sessions_list[si], self.chunk_turns))
        else:
            n_turns = n_chunks = 0
        expected = n_turns + n_chunks
        reads = self._read_verify_count(si)
        if reads.status == "consensus":
            observed = reads.value
        else:
            observed = None
            self.faults.append({"si": si, "phase": phase, "label": "read-fault",
                                "retried": True, "reads": reads.reads})
        entry = self.sessions.setdefault(str(si), {})
        entry[f"{phase}_expected"] = expected
        entry[f"{phase}_observed"] = observed

    def _read_verify_count(self, si: int) -> CensusReads:
        return _consensus_read(
            self.proj, {
                "label_scan": lambda: self._si_count_label(si),
                "traversal": lambda: self._si_count_traversal(si),
            }, retry_n=GATE_RETRY_N, timeout_ms=GATE_TIMEOUT_MS,
            label=f"per-session-{si}")

    def _si_count_label(self, si: int) -> int:
        rows = _query_with_deadline(
            self.proj,
            "MATCH (p:Point {lme_question_id:$q, lme_session_index:$si}) "
            "RETURN count(*)",
            params={"q": self.qid, "si": si}, timeout_ms=GATE_TIMEOUT_MS)
        return rows[0][0] if rows else 0

    def _si_count_traversal(self, si: int) -> int:
        rows = _query_with_deadline(
            self.proj,
            "MATCH (s:Session {lme_question_id:$q, lme_session_index:$si})"
            "-[:CONTAINS]->(p:Point) WHERE p.lme_session_index = $si "
            "RETURN count(DISTINCT p)",
            params={"q": self.qid, "si": si}, timeout_ms=GATE_TIMEOUT_MS)
        return rows[0][0] if rows else 0

    def finalize(self, ingest_stats: dict | None) -> dict:
        """Post-ingest full-question census + trace + verdict. Called AFTER
        the ingest returns (the post-ingest census needs the ingest stats'
        expected denominator). Returns the trace dict."""
        stats = ingest_stats or {}
        if "points" in stats:
            expected = (stats.get("turns", 0) + stats.get("chunks", 0)
                        + stats.get("points", 0))
        else:
            expected = stats.get("turns", 0) + stats.get("chunks", 0)
        try:
            idxs, _ = resolve_answer_session_indices(self.question)
            pool = folded_pool_rows(self.proj, self.qid, idxs or [])
            self.post_ingest = {"expected": expected,
                                "observed": pool.get("ns_count", 0)}
        except Exception:  # noqa: BLE001, RUF100
            self.post_ingest = {"expected": expected, "observed": None,
                                "error": "post-ingest census failed"}
        trace = {
            "qid": self.qid,
            "chunk_turns": self.chunk_turns,
            "signature_reproduced": self.signature_reproduced,
            "sessions": self.sessions,
            "post_ingest": self.post_ingest,
            "faults": self.faults,
            "verdict": replay_verdict({
                "signature_reproduced": self.signature_reproduced,
                "sessions": self.sessions,
                "post_ingest": self.post_ingest,
            }, gc_events=self.gc_events),
        }
        print(f"[longmem_eval] per-session census replay ({self.qid}): "
              f"verdict = {trace['verdict']}", file=sys.stderr)
        if self.work_dir:
            try:
                out = Path(self.work_dir) / f"per_session_census_{self.qid}.json"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(trace, indent=2, sort_keys=True)
                               + "\n", encoding="utf-8")
            except OSError:
                pass
        return trace


def _session_chunk_windows(session: list[dict], chunk_turns: int) -> list:
    """Chunk-window partitioning mirroring ingest._session_chunks' window
    (chunk_turns turns per window) — used for the per-session Phase-A
    census expectation (turns + chunks)."""
    if chunk_turns <= 0 or not session:
        return []
    return [session[i:i + chunk_turns]
            for i in range(0, len(session), chunk_turns)]


class _ReplayLoadWorkers:
    """Task 3 load injection: N synthetic write workers hammering scratch
    namespaces on the same FalkorDB server while the per-session census
    replay ingests — reproduces the degraded environment's write pressure
    (concurrent TimeoutErrors / AOF-fsync stalls). Default 0 = no load."""

    def __init__(self, sdk_factory: Callable[[], Any], n: int):
        self.sdk_factory = sdk_factory
        self.n = max(0, int(n))
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        if self.n == 0:
            return
        for i in range(self.n):
            t = threading.Thread(target=self._worker, args=(i,),
                                 daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=5)

    def _worker(self, i: int) -> None:
        import random as _random
        try:
            sdk = self.sdk_factory()
            proj = sdk._get_proj()
            qid = f"replay-load-{i}"
            while not self._stop.is_set():
                si = _random.randint(0, 3)
                pid = f"lme:{qid}:s{si}:p{_random.randint(0, 1 << 30)}"
                with contextlib.suppress(Exception):
                    proj.g.query(
                        "MERGE (p:Point {id:$id}) SET p.lme_question_id=$q, "
                        "p.lme_session_index=$si, p.has_answer=true",
                        params={"id": pid, "q": qid, "si": si})
        except Exception:  # noqa: BLE001, RUF100
            pass


# ── falsification-trigger predicate (Task 5 consumes; pure) ────────────────
def falsification_trigger(*, ratio_ok: bool, presence_ok: bool,
                          floor_ok: bool, post_census_ok: bool,
                          legs_healthy: bool, strategy_timeout: bool,
                          miss: bool) -> bool:
    """Task 5 falsification trigger — a pure function over the FULL 6-input
    conjunction (plan §1 falsification check + Task 5): fires ONLY on
    ratio = 1.000 AND presence green AND per-session floor green AND post-
    retrieval census green AND legs healthy AND NOT a strategy-timeout
    artifact AND still a miss. Every suppression arm enumerated in
    tests/test_graph_integrity_gate.py."""
    return (miss and ratio_ok and presence_ok and floor_ok
            and post_census_ok and legs_healthy and not strategy_timeout)


def _gate_red(outcome: dict) -> list[str]:
    """Union of phase-keyed gate reasons for one outcome (watchdog +
    certifier + resume refusal consume the same union — plan P1-3/P1-5)."""
    return list((outcome.get("gate_reasons") or [])
                + (outcome.get("post_retrieval_reasons") or []))


def _hard_gate_red(outcome: dict) -> list[str]:
    return [r for r in _gate_red(outcome) if r in HARD_GATE_REASONS]


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
    session_workers: int = 1,
    # M7 #1739 / #1742: the CLI extractor spec for the session-parallel
    # worker factory — the RESOLVED wire id (never the registry key) plus
    # the registry entry's expressible tuning, so the workers serve EXACTLY
    # what _build_cli_extractor_model fingerprints. Threaded from run_main —
    # the old free ``args`` closure was a latent NameError.
    session_worker_model_spec: str | None = None,
    session_worker_max_tokens: int | None = None,
    session_worker_temperature: float = 0.0,
    preflight: dict | None = None,
    # R3 (#1542) D2: the embedder pre-flight status (from _preflight_embedder
    # in run_main) — forwarded to the report methodology (D5: embedder +
    # vector_strategy always emitted; None default keeps programmatic
    # callers — tests, capstone #1549 — on the not_checked default).
    embedder_status: dict | None = None,
    chunk_turns: int = DEFAULT_CHUNK_TURNS,
    max_context_tokens: int = DEFAULT_CONTEXT_TOKEN_CAP,
    max_chunks_per_session: int = DEFAULT_MAX_CHUNKS_PER_SESSION,
    # C1 (#1745): reader-context item cap (default 40; env
    # TORTOISE_LME_CONTEXT_ITEMS). TR questions ignore it — tr_top_k is the
    # pinned TR item cap.
    context_item_cap: int | None = None,
    # C2 (#1745): evidence-mark boost — OFF by default in code (the plan's
    # default decision; ON for the re-validation run via env or flag).
    evidence_boost: bool | None = None,
    evidence_boost_verbatim: float | None = None,
    evidence_boost_source: float | None = None,
    # R5 (#1544): TR knobs — temporal-reasoning questions get the events
    # union pool, the engine recency date weight, the TR-constraint window
    # filter, time-ascending rendering, and the tighter tr_top_k cap
    # (20→12). Non-TR questions ignore them (byte-identical path).
    tr_top_k: int = DEFAULT_TR_TOP_K,
    tr_date_weight: float = 0.5,
    tr_events: bool = True,
    # R6 (#1545): the rerank layer — OFF by default (byte-identical
    # baseline); ``rerank_prewarm`` carries the run_main pre-warm outcome
    # (model_load_ms / prewarmed / reason) for the report config block.
    rerank: bool | None = None,
    rerank_model: str | None = None,
    rerank_pool: int | None = None,
    per_session_cap: int | None = None,
    mmr_lambda: float | None = None,
    rerank_prewarm: dict | None = None,
    # M7 (#1527): run-hygiene inputs.
    dataset_fingerprint: str = "unknown",
    integrity_threshold: float = 0.0,
    integrity_justification: str | None = None,
    # #1786 (R3): ``--retry-failed`` resume mode — re-attempts
    # ``ingest:retries_exhausted`` failure entries (retryable + attempts <
    # RESUME_ATTEMPTS_CAP). NOT fingerprinted (a recorded resume-mode,
    # methodology + checkpoint field); the marker is DISARMED during
    # re-attempts so no resume-internal whole-question retry gets a budget.
    retry_failed: bool = False,
    # #1786 (R1/R2): the write-path retry budget — the SAME values
    # ``_build_fingerprint`` records (always-present fingerprint members).
    ingest_write_retries: int = INGEST_WRITE_RETRIES,
    ingest_question_retries: int = INGEST_QUESTION_RETRIES,
    resume_attempts_cap: int = RESUME_ATTEMPTS_CAP,
    # #1786 (R5): the eval's HYBRID-arm retrieval deadline (ms) — the eval
    # (run_main) always passes EVAL_RETRIEVAL_BUDGET_MS (1500); None keeps
    # the SDK-default 500 ms for programmatic callers.
    retrieval_budget_ms: int | None = None,
    # #1785 (Task 1 Step 5 / Task 3): the mid-run watchdog + revalidation
    # mode signal + the per-session census replay (diagnostic-only).
    # ``revalidate`` is forwarded to the report certifier (build_report)
    # — a Task-5 re-validation refuses ANY gate-red outcome / strategy
    # timeout / mid-write failure (no exclusion path) and the watchdog
    # aborts on the FIRST such outcome (no 4.5h burn). ``per_session_census``
    # + ``replay_load_workers`` + ``replay_signature_reproduced`` +
    # ``replay_gc_events`` drive the Task 3 loss-location replay — all
    # fingerprint-excluded (a knob change must not alter resume-eligibility
    # of pre-change checkpoints).
    revalidate: bool = False,
    per_session_census: bool = False,
    replay_load_workers: int = 0,
    replay_signature_reproduced: bool = False,
    replay_gc_events: list | None = None,
    # #1349 vector arm: retriever routing + injected model + retrieval-only.
    retriever: str = "hybrid",
    model: str | None = None,
    query_prompt: str | None = None,
    retrieval_only: bool = False,
    surface: str = "embedded",
    run_key: str | None = None,
    # #1349 --db mode: a FalkorDB URI drives the per-question SDK (HNSW
    # queryNodes surface, per-(question, model) graph isolation) instead of
    # the embedded tempdir. encode_cache (model-keyed) persists query
    # encodings across questions/processes.
    db_uri: str | None = None,
    encode_cache: Any | None = None,
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

    M7 #1739 / review #1742 (session workers): ``session_workers > 1`` (v2
    ingest ONLY — the sole mode with a worker factory) takes the three
    ``session_worker_*`` args — ``session_worker_model_spec`` (the RESOLVED
    wire id, never the registry key), ``session_worker_max_tokens`` and
    ``session_worker_temperature`` — which carry the worker-factory config
    run_main resolved (``_session_worker_spec_tuning``) so the workers serve
    EXACTLY the fingerprinted extractor_model. A ``session_workers > 1``
    call whose factory config fingerprints differently from the passed
    ``extractor_model`` is REFUSED pre-loop (ValueError — before any
    question runs, no network); run_main's CLI path threads the trio so the
    legitimate path never trips it.
    """
    # E2E-3 Precondition 2: the audit is computed from the loaded instances
    # BEFORE anything else — no report can be produced without it.
    dataset_semantics_audit = audit_dataset(instances)
    # C2 (#1745): resolve the evidence-boost tri-state ONCE, before the
    # loop — a None with the TORTOISE_LME_EVIDENCE_BOOST env set must not
    # record `false` in the methodology while the per-question retrieval
    # boosted (the plan's Task-3 contract: methodology records the knobs
    # truthfully). Mirrors the retrieve-layer gate (fail-safe OFF: only
    # 1/true/yes/on enables).
    if evidence_boost is None:
        eb_env = (os.environ.get("TORTOISE_LME_EVIDENCE_BOOST") or "")
        evidence_boost = eb_env.strip().lower() in _TRUTHY
    # C1/C2 (#1745): resolve the remaining boost knobs ONCE, before the
    # loop — the methodology and the fingerprint must record EXACTLY what
    # the per-question retrieval serves (CLI > env > default, mirroring
    # the evidence_boost tri-state above). retrieve_for_question's own env
    # fallback only fires for DIRECT callers now — the run path always
    # passes the resolved values, so methodology == actual on every path.
    # C2 off-path hygiene (review P2-2): the multiplier env vars are
    # resolved/validated ONLY when the boost is actually ON — a boost-off
    # run must be completely unaffected by a stray TORTOISE_LME_EVIDENCE_BOOST_VERBATIM/_SOURCE
    # (mirrors the R6 "_resolve_rerank" precedent in this file: a baseline
    # run never reads the R6 env vars). When off, the multipliers stay
    # None — the fingerprint's conditional key union drops them (inert
    # knobs never gate the checkpoint fingerprint) and the methodology
    # records the default constants.
    if context_item_cap is None:
        context_item_cap = _env_int(
            "TORTOISE_LME_CONTEXT_ITEMS", DEFAULT_CONTEXT_ITEM_CAP)
    if evidence_boost:
        evidence_boost_verbatim = _resolve_boost_float(
            "TORTOISE_LME_EVIDENCE_BOOST_VERBATIM",
            DEFAULT_EVIDENCE_BOOST_VERBATIM, evidence_boost_verbatim)
        evidence_boost_source = _resolve_boost_float(
            "TORTOISE_LME_EVIDENCE_BOOST_SOURCE",
            DEFAULT_EVIDENCE_BOOST_SOURCE, evidence_boost_source)
    else:
        # Off-path: never record/fingerprint inert multipliers — an
        # explicitly-passed programmatic value is dropped (the CLI layer
        # is the user-facing validation surface; here the knob has no
        # effect on the run, so it must not gate the checkpoint or the
        # methodology). The fingerprint's conditional key union drops
        # None, and the methodology falls back to the default constants.
        evidence_boost_verbatim = None
        evidence_boost_source = None
    # M7 #1739 / #1742: the session-parallel worker factory must serve
    # EXACTLY the fingerprinted config — a programmatic caller passing a
    # spec'd extractor_model with session_workers > 1 but forgetting the
    # resolved session_worker_* trio would fingerprint one config while the
    # workers served another (the checkpoint would be accepted on resume
    # over results produced by a different model). Fail loud on mismatch.
    # Scoped to the v2 ingest path — the ONLY mode with a worker factory;
    # non-v2 modes never build extractor_model (None) and the flag is
    # rejected at the CLI (see run_main) / inert programmatically.
    if session_workers > 1 and ingest_mode == "v2":
        from tests.model_adapters import build_extractor_model
        served = build_extractor_model(
            session_worker_model_spec or None,
            max_tokens=session_worker_max_tokens,
            temperature=session_worker_temperature)
        try:
            if extractor_model is None:
                raise ValueError(
                    "session_workers > 1 (ingest_mode=v2) requires a "
                    "fingerprinted extractor_model — build it with "
                    "_build_cli_extractor_model(spec=..., "
                    "session_workers=N) so the run fingerprints EXACTLY "
                    "what the workers serve; a None extractor_model cannot "
                    "match the worker-factory config (refusing fingerprint- "
                    "vs served-config divergence)")
            if _model_id(extractor_model) != _model_id(served):
                raise ValueError(
                    "session_workers > 1 requires the worker-factory config "
                    "(session_worker_model_spec / max_tokens / temperature) "
                    "to fingerprint identically to the extractor_model the "
                    "run fingerprints — pass a router-built extractor_model "
                    "(e.g. via _build_cli_extractor_model(spec=..., "
                    "session_workers=N)) or the matching resolved "
                    "session_worker_* args; refusing fingerprint- vs "
                    "served-config divergence")
        finally:
            served.close()
    # R6 (#1545) D9: the effective rerank config resolves once, before the
    # loop — the fingerprint records it and refuses config-mismatched
    # resumes (three-valued resume: equal → allowed; different → refused;
    # pre-R6 checkpoints are refused by M7's fingerprint gate regardless).
    rr = _resolve_rerank(
        rerank=rerank, rerank_model=rerank_model, rerank_pool=rerank_pool,
        per_session_cap=per_session_cap, mmr_lambda=mmr_lambda,
        max_k=max(ks))
    # #1349: per-model checkpoint keying — a --db HNSW run never resumes
    # against embedded brute-force checkpoints (and vice versa); cross-model
    # resume is impossible by construction.
    surface = "hnsw" if db_uri else "embedded"
    run_key = checkpoint_key(surface, retriever, model, query_prompt)
    fingerprint = _build_fingerprint(
        reader_model=(reader.model_id if reader is not None
                      else "n/a (retrieval-only)"),
        judge_model=(judge.model_id if judge is not None
                     else "n/a (retrieval-only)"),
        ks=ks, top_k=top_k, split=split, ingest_mode=ingest_mode,
        extractor_model=extractor_model, max_retries=max_retries,
        dataset_fingerprint=dataset_fingerprint,
        rerank_config=rr["config"],
        # C1/C2/C5 (#1745): the RESOLVED knob values ride the fingerprint
        # (F4 resolved them above — methodology == actual == fingerprint).
        context_item_cap=context_item_cap,
        evidence_boost=bool(evidence_boost),
        evidence_boost_verbatim=evidence_boost_verbatim,
        evidence_boost_source=evidence_boost_source,
        max_chunks_per_session=max_chunks_per_session,
        # #1786 (P1-1/P1-2/P2-4): the three retry knobs (ALWAYS present —
        # results-relevant) + the hybrid retrieval budget (conditional
        # presence — the eval always passes the non-default 1500). All four
        # stale pre-feature checkpoints via CheckpointStaleError.
        ingest_write_retries=ingest_write_retries,
        ingest_question_retries=ingest_question_retries,
        resume_attempts_cap=resume_attempts_cap,
        retrieval_budget_ms=retrieval_budget_ms,
    )
    done, prior_failures = _load_checkpoint(checkpoint, fingerprint,
                                            run_key=run_key,
                                            retriever=retriever,
                                            retry_failed=retry_failed,
                                            # #2185 (A4): fold the
                                            # checkpoint's usage payload +
                                            # kill-9 replicas into the
                                            # collector (empty in a fresh
                                            # process) before the loop runs.
                                            fold_usage=True)
    outcomes: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = list(prior_failures)
    # #1785 (Task 1 Step 2): FRESH vs RESUME — the ratio denominator is only
    # meaningful on a clean per-question namespace. A resume (prior completed
    # or failed state in the checkpoint) SKIPS the fresh-run namespace
    # cleanup and the ratio tier is suppressed in the gate (leftover nodes
    # from the prior partial run are expected; presence is primary).
    resumed_run = bool(done or prior_failures)
    # #1785 (Task 1 Step 5): the mid-run watchdog — shared run-level state
    # across workers (the latency/gate-red windows are GLOBAL; the
    # consecutive-census_error arm is per-worker by nature). Aborts the run
    # early with ``degraded_aborted`` on sustained degradation so a re-
    # validation can never burn ~4.5h to a guaranteed-fail verdict.
    watchdog = _RunWatchdog(revalidate=revalidate, n_questions=len(instances))
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
        # #1786 (R3): the resume gate — a previously-failed qid is skipped
        # UNLESS --retry-failed re-attempts it (ingest:retries_exhausted +
        # retryable [+ the legacy repr rescue] + attempts < cap). The
        # eligibility warnings surfaced at load; the flocked CAS claim
        # (Step 7) is the AUTHORITATIVE re-verify (this .get()-based check
        # is the advisory fast path — stale the moment a concurrent process
        # claims).
        prior_failure = next((f for f in failures
                              if f.get("question_id") == qid), None)
        resume_reattempt = False
        resume_semaphore_held = False
        _need_claim = False
        if prior_failure is not None:
            if not retry_failed or _retry_failed_skip_reason(
                    prior_failure, cap=resume_attempts_cap) is not None:
                print(f"  [resume] {qid} previously failed — skipping "
                      f"(delete the checkpoint to retry)", file=sys.stderr)
                return
            _need_claim = True
        t_q_start = time.monotonic()
        # M7 (D6): the failure site for the error census. Covers the non-LLM
        # graph pipeline (ingest + retrieve) first; reader/judge set it just
        # before their calls.
        _stage = "ingest"
        # #1786 (R2): the whole-question retry state. ``r2_retained`` holds
        # the ORIGINAL write-stage exception — the tier-identity source:
        # whenever R2 fails at ANY non-write stage (a reader/judge transient
        # during R2's re-extraction), the failure entry's error_class grades
        # from this RETAINED exception, never the live one (P2-1).
        # ``r2_attempted`` counts the R2 launches (0 or 1 — the marker is
        # armed only for the INITIAL attempt, so a resume re-attempt or a
        # second R2-internal exhaustion never re-fires it: a disarm bug
        # would triple the ~25-min re-burn and corrupt the counter). The
        # BoundedSemaphore is held for the R2 re-ingest DURATION (the
        # retry-amplification enforcement, AWS REL05-BP03).
        r2_retained: BaseException | None = None
        r2_attempted = 0
        r2_semaphore_held = False
        # #1785 (Task 1 Step 5): per-worker consecutive-census_error
        # counter (the watchdog's per-worker arm) + the gate timing/ratio
        # signals recorded per completed question.
        _consec_census = 0
        _gate_latency_ms: float | None = None
        _gate_ratio: float | None = None
        # #1786 (code-review F3): the success/breaker paths fall through to
        # the SAME trailing save as the failure path (under ``_lock``) —
        # this flag carries their ``remove_failures`` tombstone so a
        # --retry-failed re-attempt's remove-on-success stays ONE flocked
        # write without saving inside the per-question try (the old
        # in-try save snapshotted ``done`` OUTSIDE the lock → a concurrent
        # ``done`` mutation raised RuntimeError → bogus failure entry).
        _save_remove_failures: list[str] | None = None
        try:
            # #1786 (code-review F9 cycle 2): acquire-then-claim INSIDE the
            # try — (a) the limiter slot is released by the outer finally even
            # on a raise between acquire and the claim; (b) the flocked CAS
            # re-reads the on-disk entry AFTER the wait, so a worker blocked
            # on acquire no longer holds a live claim stamp while not working
            # (a stale claim stolen + completed by another process reads as
            # ``prior is None`` → claim fails → self-healing skip, never a
            # duplicate ~25-min re-ingest). P2-1: the resume tier shares the
            # limiter with R2 (released on completion/failure in the outer
            # finally) — N eligible entries + workers=N must not launch N
            # concurrent ~25-min re-ingests (retry-amplification,
            # AWS REL05-BP03).
            if _need_claim:
                _REINGEST_LIMITER.acquire()
                resume_semaphore_held = True
                if not _claim_reattempt(checkpoint, qid, resume_attempts_cap):
                    print(f"  [resume] {qid} re-attempt claimed by another "
                          f"process — skipping", file=sys.stderr)
                    return
                resume_reattempt = True
                print(f"  [resume] {qid} re-attempting transient failure "
                      f"(--retry-failed)", file=sys.stderr)
            # #2185: bind the question key so every sink row fired below
            # (reader/judge calls + the extractor daemon threads via
            # copy_context) buckets under THIS qid. Set AFTER the resume
            # early-returns — a skipped qid never fires an LLM call here and
            # must not accumulate rows.
            lme_usage.set_question_key(qid)
            while True:
                try:
                    namespace = (question_graph_namespace(
                        model, query_prompt, qid) if db_uri else None)
                    sdk, cleanup = _make_question_sdk(
                        db_uri=db_uri,
                        namespace=namespace,
                        work_dir=work_dir)
                    try:
                        _sdk_cleanup = cleanup
                        # #1785 (Task 1 Step 2): FRESH-run per-question
                        # namespace cleanup — the ratio denominator is only
                        # meaningful on a clean namespace (leftover nodes from
                        # a prior partial run would push the ratio > 1.0 →
                        # ``census_overflow`` on a clean question). SKIPPED on
                        # resume (presence is primary; leftovers expected) and
                        # in embedded mode (fresh tempdir — isolation by
                        # construction). The cleanup REFUSES while a LIVE peer
                        # marker exists on the same namespace — never clobber
                        # a concurrent run's in-flight question graph (plan
                        # cycle3-P2-33 / cycle4-P2-37: 'never clobbered'
                        # fallback). ORDERING PINNED (review P1): the guard is
                        # checked BEFORE our own marker is written — a
                        # write-before-check ordering would overwrite the
                        # peer's LIVE marker with our pid and the cleanup
                        # would proceed, clobbering the peer's in-flight
                        # graph. Our marker is written only AFTER the guard
                        # passes (heartbeats refresh it at the gate sites).
                        if (db_uri and not resumed_run
                                and _namespace_cleanup_allowed(
                                    work_dir, namespace, run_key)):
                            _write_run_marker(work_dir, namespace, run_key)
                            # TARGETED per-question wipe (not a bulk
                            # ``MATCH (n) DETACH DELETE n`` — the
                            # _GuardedGraph bulk-wipe guard refuses non-
                            # test-named graphs; the namespace is per-
                            # question, so the lme_question_id-scoped delete
                            # removes every node this question's ingest can
                            # write (operator Points carry no
                            # lme_question_id and are NOT counted by the
                            # label-scan census anyway).
                            with contextlib.suppress(Exception):
                                sdk._get_proj().g.query(
                                    "MATCH (n) WHERE n.lme_question_id = $q "
                                    "DETACH DELETE n", params={"q": qid})
                        # M7 (D5): ingest is timed in isolation — the write-path
                        # cost is a report component (extractor vs retrieve vs
                        # reader vs judge attribution).
                        # #1785 (Task 3): the per-session census replay wraps
                        # the ingest — a query wrapper interleaves a read-
                        # verified per-session census after each session's
                        # Phase A/C writes, and the post-ingest census runs via
                        # ``finalize`` (the trace + verdict are written to the
                        # work dir). Diagnostic-only — fingerprint-excluded.
                        _replay_census = (None if not per_session_census
                                          else _PerSessionCensus(
                                              sdk._get_proj(), qid, question,
                                              chunk_turns, work_dir=work_dir,
                                              signature_reproduced=
                                              replay_signature_reproduced,
                                              gc_events=replay_gc_events))

                        def _run_ingest(_sdk):
                            if ingest_mode == "v2":
                                from tests.model_adapters import build_extractor_model

                                from .ingest_v2 import ingest_haystack_v2
                                return ingest_haystack_v2(
                                    _sdk, question, extractor_model,
                                    chunk_turns=chunk_turns,
                                    # Pilot #1549: session-parallel extraction
                                    # within a question (the LLM phase is the
                                    # wall-clock dominant cost). NOTE: the live
                                    # ingest_haystack_v2 on main shadows the
                                    # parallel worker-factory path with a
                                    # sequential copy (pre-existing duplicate,
                                    # tracked separately — #1744), so workers
                                    # currently fall back to the shared
                                    # extractor_model — which is exactly what
                                    # the fingerprint records. The per-session
                                    # census replay forces ``session_workers=1``
                                    # for deterministic measurement (#1744
                                    # caveat).
                                    session_workers=(
                                        1 if per_session_census
                                        else session_workers),
                                    # M7 #1739 / #1742: the factory spec +
                                    # tuning are threaded in (never the
                                    # run_main-local ``args`` closure — that was
                                    # a latent NameError) and mirror exactly what
                                    # _build_cli_extractor_model fingerprints:
                                    # the workers serve the SAME config the
                                    # checkpoint records.
                                    model_factory=(
                                        (lambda: build_extractor_model(
                                            session_worker_model_spec or None,
                                            max_tokens=session_worker_max_tokens,
                                            temperature=session_worker_temperature))
                                        if session_workers > 1 else None),
                                    # #1786 (R1): the write-stage retry budget —
                                    # the SAME value the fingerprint records — +
                                    # marker arming (DISARMED during a
                                    # --retry-failed resume re-attempt: no
                                    # resume-internal whole-question retry
                                    # budget, P1-1).
                                    ingest_write_retries=ingest_write_retries,
                                    write_marker_armed=not resume_reattempt)
                            return ingest_haystack(
                                _sdk, question, chunk_turns=chunk_turns)

                        t_ingest = time.monotonic()
                        if _replay_census is not None:
                            with _replay_census:
                                ingest_stats = _run_ingest(sdk)
                            _replay_census.finalize(ingest_stats)
                        else:
                            ingest_stats = _run_ingest(sdk)
                        ingest_latency_ms = round(
                            (time.monotonic() - t_ingest) * 1000.0, 2)
                        # M7 (D3): the authoritative live graph pool size — the
                        # retrieval-pool denominator the methodology documents.
                        # #1785: FOLDED pool_rows (plan P2-2/P2-5) — a single
                        # Cypher returning BOTH the unfiltered namespace count
                        # (the ratio numerator) AND per-session membership for
                        # the mapped answer-session indices (the presence +
                        # per-session-floor + lost-mark-cross-check data). The
                        # join resolution is fail-closed (``dataset_join_error``
                        # rides the gate, never a ValueError, never silently
                        # matching nothing).
                        _ans_idxs, _join_err = resolve_answer_session_indices(
                            question)
                        pool_census = folded_pool_rows(
                            sdk._get_proj(), qid, _ans_idxs or [])
                        pool_size = pool_census["ns_count"]
                        # #1785 (Task 1): the PRE-RETRIEVAL graph-integrity
                        # gate — flags truncated graphs fail-loud (ratio
                        # sub-1.0 → ``graph_truncated``, >1.0 →
                        # ``census_overflow``, presence-red →
                        # ``answer_session_absent``, evidence-mark short →
                        # ``evidence_mark_census``, fault → ``census_error``).
                        # The gate FLAGS, never skips retrieval — a red
                        # question STILL runs retrieve_for_question and its
                        # outcome stays shape-OK, graded via grade_by_qid
                        # (plan final-verification P2). ``resumed_run``
                        # suppresses the ratio tier (leftover nodes from a
                        # prior partial run are expected) — presence stays
                        # primary. ``retrieval_only`` exempts the tiers.
                        _t_gate = time.monotonic()
                        _gate = run_integrity_gate(
                            sdk._get_proj(), question, qid,
                            ingest_stats=ingest_stats,
                            pool_result=pool_census,
                            retrieval_only=retrieval_only,
                            resumed=resumed_run)
                        # P1-1 (review): the watchdog latency arm keys on the
                        # AVERAGE per-query census latency (plan P2-9: per-
                        # query p95 > 2xQ — never the per-question total, a
                        # count-vs-latency mix). Reads that went to read-
                        # verify retry are excluded by the read-verify layer
                        # (only successful reads are counted).
                        _gate_reads = max(1, int(_gate.get("census", {}).get("reads", 10)))
                        _gate_latency_ms = round(
                            ((time.monotonic() - _t_gate) * 1000.0)
                            / _gate_reads, 2)
                        # P2-4 (review): refresh the live-marker heartbeat at
                        # the pre-retrieval gate (a v2 question can exceed the
                        # marker TTL mid-question; a stale marker would be
                        # auto-cleared by a peer and clobbered).
                        if db_uri and not resumed_run:
                            _write_run_marker(work_dir, namespace, run_key)
                        gate_reasons = list(_gate["reasons"])
                        _gate_ratio = _gate.get("ratio")
                        _consec_census = (0 if GATE_REASON_CENSUS_ERROR
                                          not in gate_reasons
                                          else _consec_census + 1)
                        ret = retrieve_for_question(
                            sdk, question, ks=ks, top_k=top_k,
                            retriever=retriever,
                            max_context_tokens=max_context_tokens,
                            max_chunks_per_session=max_chunks_per_session,
                            tr_top_k=tr_top_k,
                            tr_date_weight=tr_date_weight,
                            tr_events=tr_events,
                            rerank=rr["rerank_on"],
                            rerank_model=rr["model"],
                            rerank_pool=rr["rerank_pool"],
                            per_session_cap=rr["per_session_cap"],
                            mmr_lambda=rr["mmr_lambda"],
                            # C1/C2 (#1745): reader-context item cap + evidence-
                            # mark boost (OFF by default; the re-validation run
                            # enables it via env/flag).
                            context_item_cap=context_item_cap,
                            evidence_boost=evidence_boost,
                            evidence_boost_verbatim=evidence_boost_verbatim,
                            evidence_boost_source=evidence_boost_source,
                            # #1786 (R5): the eval's elevated HYBRID-arm
                            # retrieval deadline via the existing seam (the
                            # vector arm keeps VECTOR_TIMEOUT_MS=5000).
                            retrieval_budget_ms=retrieval_budget_ms)

                        # #1785 (Task 1): the POST-RETRIEVAL census — a second
                        # invocation of the shared gate predicates immediately
                        # after retrieval (inside the try, before the finally's
                        # sdk.close) distinguishes loss-between-gate-and-
                        # retrieval (H6 — the falsification-critical case) from
                        # genuine retrieval misses. Runs on gate-green questions
                        # only (already-red questions keep their pre-existing
                        # red — no wasted budget on final verdicts; plan P2-3).
                        # The reasons are phase-keyed (``post_retrieval_reasons``
                        # vs ``gate_reasons``) so downstream consumers (resume
                        # refusal, certifier, falsification trigger) can tell
                        # them apart (plan P2-8).
                        if not gate_reasons:
                            _t_post = time.monotonic()
                            _post = run_integrity_gate(
                                sdk._get_proj(), question, qid,
                                ingest_stats=ingest_stats,
                                pool_result=None,
                                retrieval_only=retrieval_only,
                                resumed=resumed_run)
                            if _gate_latency_ms is not None:
                                _post_reads = max(1, int(
                                    _post.get("census", {}).get("reads", 10)))
                                _gate_latency_ms = round(
                                    _gate_latency_ms
                                    + ((time.monotonic() - _t_post) * 1000.0)
                                    / _post_reads, 2)
                            if db_uri and not resumed_run:
                                _write_run_marker(work_dir, namespace, run_key)
                            post_retrieval_reasons = list(_post["reasons"])
                            _consec_census = (0 if GATE_REASON_CENSUS_ERROR
                                              not in post_retrieval_reasons
                                              else _consec_census + 1)
                        else:
                            post_retrieval_reasons = []
                        # #1349 retrieval-only: reader/judge never invoked — the
                        # outcome carries retrieval + breaker accounting only.
                        if retrieval_only:
                            hypothesis = None
                            label = None
                            reader_ms = 0.0
                            judge_ms = 0.0
                        else:
                            _stage = "reader"
                            t0 = time.monotonic()
                            # A1 #1546 invariant: the reader receives question_type
                            # ONLY. The _abs marker (question_id suffix) must never
                            # cross — abstention is derived by the reader from the
                            # evidence (rendered hits + dates), via the universal
                            # partial-knowledge clause in system_prompt_for.
                            hypothesis = _call_with_backoff(
                                lambda _ret=ret: reader.answer(
                                    # R1 (#1540) D6: the reader consumes EXACTLY the
                                    # budget-capped rank-interleaved context the token
                                    # metric reports (was the full uncapped pool).
                                    context_hits=_ret["context_points"],
                                    question=question["question"],
                                    question_date=question.get("question_date", "") or None,
                                    question_type=question.get("question_type", "") or None,
                                ),
                                what=f"reader for {qid}", retries=max_retries)
                            reader_ms = (time.monotonic() - t0) * 1000.0

                            _stage = "judge"
                            t0 = time.monotonic()
                            label = _call_with_backoff(
                                lambda _hyp=hypothesis: judge.judge(
                                    question_type=question.get("question_type", ""),
                                    question=question["question"],
                                    answer=question.get("answer", ""),
                                    hypothesis=_hyp,
                                    abstention=is_abstention(qid),
                                ),
                                what=f"judge for {qid}", retries=max_retries)
                            judge_ms = (time.monotonic() - t0) * 1000.0
                    finally:
                        sdk.close()
                        with contextlib.suppress(Exception):
                            _sdk_cleanup()

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
                        # #1746 (D7): the per-question LLM telemetry + recovery
                        # counters — the report's warning-only truncation readout
                        # (criterion 3: no UNRECORDED truncation with valid=true).
                        "llm_calls": (ingest_stats.get("llm") or {}).get("calls", 0),
                        "llm_retries": (ingest_stats.get("llm") or {}).get("retries", 0),
                        "llm_truncated": (ingest_stats.get("llm") or {}).get("truncated", 0),
                        "recovery": ingest_stats.get("recovery", {}),
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
                        "leg_mix": ret.get("match_source_counts"),
                        "leg_mix@k": ret.get("match_source_counts@k"),
                        "pool_size": pool_size,
                        # #1785 (Task 1): graph-integrity gate reasons —
                        # phase-keyed lists ([] = gate green; plan P2-8).
                        # Consumed by the resume-scan refusal (Task 2,
                        # union semantics), the report certifier, and the
                        # falsification trigger. Read via .get defaults so
                        # pre-change checkpoints resume identically.
                        "gate_reasons": gate_reasons,
                        "post_retrieval_reasons": post_retrieval_reasons,
                        "evidence_written": evidence_written,
                        "evidence_retrieved@k": ret.get("evidence_retrieved@k"),
                        "ingest_latency_ms": ingest_latency_ms,
                        # R3 (#1542) D3/D4: write-time embedding coverage + the
                        # per-leg trace (vector/fts/structural/fallback — E2E-1
                        # never-null leg-mix, recorded per question).
                        "points_total": ret.get("points_total"),
                        "points_embedded": ret.get("points_embedded"),
                        "embedding_coverage": ret.get("embedding_coverage"),
                        "legs": ret.get("legs"),
                        # R5 (#1544): the TR-constraint surface per question — the
                        # detected kind (TR only) + whether the window filter fell
                        # back to the unfiltered pool (never starve the reader).
                        "tr_constraint": ret.get("tr_constraint"),
                        "tr_window_fallback": ret.get("tr_window_fallback", False),
                        # C4 (#1745): the reader-surface evidence metric
                        # (context-level; the metric C1 actually moves).
                        "reader_evidence@k": ret.get("reader_evidence@k"),
                        # #1948: the reader-surface metric (evidence-bearing
                        # points AND chunks in the FULL reader context) —
                        # wired end-to-end alongside reader_evidence@k.
                        "reader_surface@k": ret.get("reader_surface@k"),
                        # Task 0 (#1745): ranked ids + evidence-turn matches
                        # populated for BOTH arms (the pilot's context composition
                        # was unreconstructable — 0/50); ranked_ids_pre_boost is
                        # the C2 ablation surface (identical when the boost is
                        # off). The #1349 vector-arm gate metrics (ndcg@10/p@10/
                        # p@5) stay conditional on the vector arm's keys.
                        "ranked_ids": ret.get("ranked_ids"),
                        "ranked_ids_pre_boost": ret.get("ranked_ids_pre_boost"),
                        "evidence_turn_matches": ret.get("evidence_turn_matches"),
                        "evidence_boost": ret.get("evidence_boost"),
                        # R6 (#1545): the rerank pass + latency ride the outcome —
                        # they stay ABSENT on baseline outcomes (the projection in
                        # outcomes_to_report adds them conditionally).
                        **({"rerank_pass": ret["rerank_pass"],
                            "rerank_latency_ms": ret.get("rerank_latency_ms", 0.0)}
                           if "rerank_pass" in ret else {}),
                        # #1349 vector arm: gate metrics + breaker-open dropped marker.
                        **({"ndcg@10": ret["ndcg@10"],
                            "p@10": ret["p@10"],
                            "p@5": ret["p@5"]}
                           if "ndcg@10" in ret else {}),
                        # #1786 (Task 1 Step 5): the two distinct per-question
                        # recovery counters — ingest_retries (write-stage retry
                        # count, from the ingest stats) and whole_question_retries
                        # (R2 count, 0 or 1). The E2E asserts the R2 counter,
                        # NEVER ingest_retries, as the R2-fired signal (P1-4).
                        "ingest_retries": ingest_stats.get("ingest_retries", 0),
                        "whole_question_retries": r2_attempted,
                    }
                    # #2185: drain the qid's CUMULATIVE usage envelope — the
                    # outcome carries ``llm_usage`` ONLY when an LLM was
                    # actually called this run (mock/retrieval-only outcomes
                    # stay byte-identical, 16-key set).
                    _usage_env = lme_usage.get_collector().drain_question(qid)
                    if _usage_env is not None:
                        outcome["llm_usage"] = _usage_env
                    with _lock:
                        outcomes.append(outcome)
                        done[qid] = outcome
                        if resume_reattempt:
                            # remove-on-success IN ONE flocked write (P2-10) —
                            # the trailing _save_checkpoint carries the
                            # remove_failures tombstone.
                            failures[:] = [f for f in failures
                                           if f.get("question_id") != qid]
                    # #1785 (Task 1 Step 5): the mid-run watchdog records
                    # this question's signals (gate-red union, strategy
                    # timeout, census latency, consecutive census_error,
                    # leg-deadness) and aborts the run early with a
                    # DISTINCT reason when a sustained/mode-aware arm fires
                    # (no 4.5h burn to a guaranteed-fail verdict).
                    _strategy_timeout = any(
                        isinstance(leg, dict)
                        and leg.get("reason") == "timeout"
                        for leg in (outcome.get("legs") or []))
                    _leg_sigs = _legs_degraded(
                        outcome, question.get("question_type", ""))
                    _dead_sigs = [name for name, entries in _leg_sigs
                                  if any(e.get("reason") in ("timeout", "query_failed")
                                         or (e.get("reason") == "empty_results"
                                             and e.get("count", 0) == 0)
                                         for e in entries)]
                    _below_floor = [name for name, entries in _leg_sigs
                                    if name not in _dead_sigs]
                    # collapse guard (plan P2-12): below-floor-only legs on
                    # a ratio=1.000 gate-green question are NATURALLY short
                    # (small graphs) — not a degradation signal.
                    if (_below_floor and not _dead_sigs
                            and not outcome.get("gate_reasons")
                            and _gate_ratio == 1.0):
                        _below_floor = []
                    _abort_reason = watchdog.record(
                        qid=qid,
                        gate_reasons=list(outcome.get("gate_reasons") or []),
                        post_retrieval_reasons=list(
                            outcome.get("post_retrieval_reasons") or []),
                        strategy_timeout=_strategy_timeout,
                        census_latency_ms=_gate_latency_ms,
                        consec_census_error=_consec_census,
                        legs_degraded=bool(_dead_sigs or _below_floor))
                    if _abort_reason:
                        raise WatchdogAbortError(_abort_reason)
                    # #1786 (code-review F3): the save is NOT done here — it
                    # falls through to the SAME trailing ``with _lock:
                    # _save_checkpoint(...)`` as the failure path so the
                    # snapshot of ``done``/``failures`` happens UNDER the lock
                    # (the old in-try save iterated ``done`` unlocked → a
                    # concurrent worker's append raised RuntimeError → the
                    # generic handler fabricated a bogus failure entry for an
                    # already-succeeded qid).
                    _save_remove_failures = [qid] if resume_reattempt else None
                    break
                except VectorBreakerOpenError:
                    # #1349: vector-arm breaker drops are NOT failures — the
                    # question is marked breaker_open and excluded from the
                    # means (count surfaced in report["dropped"]). Never
                    # recall 0.
                    with _lock:
                        dropped_outcome = {
                            "question_id": qid,
                            "question_type": question.get("question_type", ""),
                            "breaker_open": True,
                            "dropped_reason": "breaker_open",
                            "label": None,
                            "hypothesis": None,
                            "session_recall@k": {str(k): 0.0 for k in ks},
                            "turn_recall@k": {str(k): 0.0 for k in ks},
                            "ndcg@10": None, "p@10": None, "p@5": None,
                        }
                        outcomes.append(dropped_outcome)
                        done[qid] = dropped_outcome
                        # #2185: breaker-open spend is OVERHEAD (Am 20/24) —
                        # drained into the collector's overhead store; the
                        # returned envelope rides the dropped outcome as a
                        # kill-9-safe replica (the load-time A4 fold
                        # reconstructs it for a resumed report).
                        _usage_env = lme_usage.get_collector().drain_to_overhead(qid)
                        if _usage_env is not None:
                            dropped_outcome["usage"] = _usage_env
                        if resume_reattempt:
                            # #1786 (review P2): a --retry-failed re-attempt
                            # that ends breaker-open must NOT leave the
                            # failure entry (with its live claim stamp)
                            # behind — the qid is now in ``done`` and would
                            # otherwise double-count (outcome + n_failed)
                            # and the stale entry would never be cleaned
                            # (resume short-circuits on done). Purge +
                            # tombstone in ONE flocked write.
                            failures[:] = [f for f in failures
                                           if f.get("question_id") != qid]
                    # #1786 (code-review F3): same fall-through as the
                    # success path — the trailing save snapshots under
                    # ``_lock``; a save-time CheckpointStaleError (corrupt
                    # base quarantine) must abort loudly, never become a
                    # per-question failure entry.
                    _save_remove_failures = [qid] if resume_reattempt else None
                    break
                except ModelEncodeFailedError:
                    # #1349: the graph has ZERO embedding-bearing points — empty
                    # recall is indistinguishable from a legit no-hit. ABORT the
                    # whole config run (never report empty recall as a result); the
                    # runner exits MODEL_ENCODE_FAILED_EXIT.
                    raise
                except Exception as e:  # noqa: BLE001, RUF100
                    # #1785 (review P0-1): a watchdog abort / checkpoint-
                    # persist failure raised on the SUCCESS path must NOT be
                    # swallowed by the per-question failure handler (it would
                    # record a bogus failure entry and continue the run — the
                    # watchdog would never abort). Re-raise so the dispatch
                    # handler records the run-level marker and aborts.
                    if isinstance(e, (WatchdogAbortError, CheckpointPersistError)):
                        raise
                    # M2 (#1523, D4): a fatal-class provider error mid-run means the
                    # key died (billing cap hit, revocation) — continuing would
                    # silently produce garbage questions. Abort the run instead of
                    # recording a per-question failure (E2E-2: no silent degradation).
                    # Transient-exhausted errors still record into ``failures`` and
                    # the run continues (existing behavior — the per-question
                    # isolation semantics are unchanged for transients). The
                    # predicate-FALSE re-raise (never sentinel-wrapped) keeps the
                    # abort path seeing the RAW fatal exception.
                    if is_fatal(e):
                        raise FatalProviderError(where="run-loop", exc=e, qid=qid) from e
                    # #1786: unwrap the write-stage sentinel FIRST — error /
                    # error_class / retryable are ALL derived from the INNER
                    # exception (evaluating the predicate on the sentinel itself
                    # would persist retryable=False → permanent loss).
                    inner = (e.original
                             if isinstance(e, WriteStageRetriesExhausted) else e)
                    marker = (isinstance(e, WriteStageRetriesExhausted)
                              and retryable_transient(inner))
                    if r2_retained is None and marker:
                        r2_retained = inner
                    # R2 (Task 1 Step 4): the whole-question last-resort retry —
                    # ONLY on a write-stage-exhausted marker from the INITIAL
                    # attempt (never a reader/judge exhaustion, never a resume
                    # re-attempt, never twice). Fires AFTER the is_fatal check —
                    # a fatal-class exception aborts immediately (a dead API key
                    # must never re-burn ~25 min) and creates NO failure entry.
                    if (marker and not resume_reattempt and r2_attempted == 0
                            and ingest_question_retries > 0):
                        r2_attempted += 1
                        print(f"[longmem_eval] question {qid} write-stage "
                              f"retries exhausted — whole-question retry "
                              f"{r2_attempted}/{ingest_question_retries} "
                              f"(jittered start ≤{R2_JITTER_MAX_S:.0f}s)",
                              file=sys.stderr)
                        if not r2_semaphore_held:
                            _REINGEST_LIMITER.acquire()
                            r2_semaphore_held = True
                        # Full-jitter start spread [0, 60]s — the REAL
                        # anti-amplification bound is the semaphore (≤ 2
                        # concurrent ~25-min re-ingests); the jitter only
                        # spreads start times so exhausted workers do not
                        # re-burn into a just-recovered server in lockstep.
                        # R2's OWN write-stage retry window (~36 s) absorbs
                        # a pause tail — a mid-pause start is NOT burned.
                        time.sleep(random.uniform(0.0, R2_JITTER_MAX_S))
                        _stage = "ingest"  # stage reset at retry start
                        continue
                    print(f"[longmem_eval] question {qid} FAILED (non-fatal, "
                          f"continuing): {e!r}", file=sys.stderr)
                    # Tier identity (P2-1): the RETAINED original write-stage
                    # exception grades the R2-failure entry even when R2
                    # failed at a NON-write stage (a reader/judge transient
                    # during R2's re-extraction has NO sentinel and would
                    # otherwise grade reader:/judge:retries_exhausted — the
                    # exact permanent-exclusion bug this pin targets).
                    if r2_retained is not None:
                        tier_exc = r2_retained
                        entry_stage = "ingest"
                    else:
                        tier_exc = inner
                        entry_stage = _stage

                    # #2185: drain the qid's CUMULATIVE usage (rows
                    # accumulate across the whole-question retry ``continue``s
                    # and are drained ONCE at the terminal failure) into the
                    # overhead store. The returned envelope rides the failure
                    # entry as the kill-9-safe replica — the A4 load fold
                    # reconstructs the spend when the checkpoint save lost it.
                    _usage_env = lme_usage.get_collector().drain_to_overhead(qid)

                    with _lock:
                        # Counter semantics (P1-6): the in-run R2 DOES
                        # increment the persisted counter (R2-exhausted
                        # entry starts at attempts=1); a provider transient
                        # that never entered the write-stage loop starts at
                        # attempts=0; each failed --retry-failed re-attempt
                        # increments from the ON-DISK prior (never the stale
                        # in-memory copy; never at claim — a kill -9 mid-
                        # attempt leaves the counter untouched).
                        entry = _upsert_failure(
                            checkpoint, qid,
                            partial(_build_failure_entry, qid,
                                    question.get("question_type", ""),
                                    tier_exc, entry_stage, r2_attempted,
                                    resume_reattempt, usage_env=_usage_env),
                            fingerprint=fingerprint, run_key=run_key,
                            surface=surface, retriever=retriever,
                            model=model, prompt=query_prompt)
                        failures[:] = (
                            [f for f in failures
                             if f.get("question_id") != qid] + [entry])
                        # #1785 (Task 1 Step 5): the mid-write failure-rate
                        # arm samples failures here (failures never enter
                        # outcomes — no other arm sees them). Under
                        # revalidate=True a single mid-write failure aborts
                        # (zero-failure verdict); under revalidate=False the
                        # scoped rate arm fires when the run has gated
                        # outcomes and the rate bound is exceeded.
                        _abort = watchdog.record_failure()
                        if _abort:
                            raise WatchdogAbortError(_abort) from None
                    break
        finally:
            if r2_semaphore_held:
                _REINGEST_LIMITER.release()
            if resume_semaphore_held:
                _REINGEST_LIMITER.release()
        with _lock:
            _save_checkpoint_safe(
                checkpoint, list(done.values()), failures, fingerprint,
                run_key=run_key, surface=surface, retriever=retriever,
                model=model, prompt=query_prompt,
                remove_failures=_save_remove_failures,
                # #2185: the collector's overhead snapshot (keyless rows +
                # moved failed/breaker qids) rides every per-question save —
                # killed mid-run, a resume reconstructs the exact spend.
                usage_overhead=lme_usage.get_collector().overhead_payload(
                    checkpoint_form=True))

    # ── dispatch: sequential (workers=1) or a thread pool ──
    # #1785: the run's own live marker is written per-question namespace at
    # the start and cleared on completion/abort (decommission — plan
    # cycle4-P1-13(d)); the Task 3 load-injection workers run concurrently
    # with the per-session-census replay questions.
    _load_workers = (_ReplayLoadWorkers(
        lambda: _make_question_sdk(db_uri=db_uri, namespace=None,
                                   work_dir=work_dir)[0] if db_uri
        else _make_question_sdk(db_uri=None, namespace=None,
                                work_dir=work_dir)[0],
        replay_load_workers)
        if (per_session_census and replay_load_workers > 0) else None)
    if _load_workers is not None:
        _load_workers.start()
    try:
        if workers <= 1:
            for i, question in enumerate(instances):
                _run_one(question, i)
        else:
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(
                    max_workers=workers) as ex:
                futures = [
                    ex.submit(_run_one, question, i)
                    for i, question in enumerate(instances)
                ]
                for f in concurrent.futures.as_completed(futures):
                    f.result()  # re-raise any unexpected error
    except (WatchdogAbortError, CheckpointPersistError) as e:
        # #1785 (Task 1 Step 5 / P2-10): a mid-run abort is recorded on the
        # checkpoint with a DISTINCT run-level marker (``degraded_aborted`` /
        # ``checkpoint_abort``) — never a bare traceback; a resume refuses it
        # (Task 2 run-level refusal). The completed window's outcomes never
        # reach an aggregate/report claim (the exception propagates).
        _marker = ("degraded_aborted" if isinstance(e, WatchdogAbortError)
                   else "checkpoint_abort")
        _detail = ({"reason": getattr(e, "reason", str(e))}
                   if isinstance(e, WatchdogAbortError) else {"reason": str(e)})
        print(f"[longmem_eval] RUN ABORTED — {_marker}: "
              f"{_detail['reason']}", file=sys.stderr)
        _record_run_abort(
            checkpoint, marker=_marker, detail=_detail,
            outcomes=list(done.values()), failures=failures,
            fingerprint=fingerprint, run_key=run_key, surface=surface,
            retriever=retriever, model=model, prompt=query_prompt)
        raise
    finally:
        if _load_workers is not None:
            _load_workers.stop()
        if db_uri and not resumed_run and work_dir:
            for question in instances:
                _clear_run_marker(
                    work_dir, question_graph_namespace(
                        model, query_prompt, question["question_id"]))

    # #2185 (Task 6): end-of-run overhead drain — spend that was NOT
    # drained onto an outcome (residual late daemon sink fires after their
    # question's drain, keyless/preflight rows, breaker-open + failed
    # question moves) is swept into the overhead envelope handed to the
    # report builder. Never double-counted: per-question usage lives on the
    # outcomes; failure-entry / breaker-outcome replicas are NOT re-summed
    # here (the A4 load fold reconstructed them into the collector at
    # resume; on a fresh run the runtime move already parked them there).
    usage_overhead: dict | None = None
    try:
        lme_usage.get_collector().sweep_to_overhead()
        usage_overhead = lme_usage.get_collector().drain_overhead()
    except Exception:  # noqa: BLE001 — metering must never crash a run
        usage_overhead = None

    return outcomes, outcomes_to_report(
        outcomes,
        # #1349 vector arm: retriever/model/query_prompt/mode/run_key.
        retriever=retriever, model=model, query_prompt=query_prompt,
        retrieval_only=retrieval_only, surface=surface, run_key=run_key,
        revalidate=revalidate,
        # #2185 (Task 6): the drained overhead envelope (None on
        # usage-free runs keeps the report byte-identical).
        usage_overhead=usage_overhead,
        reader_model=(reader.model_id if reader is not None
                      else "n/a (retrieval-only)"),
        reader_model_spec=getattr(reader, "model_spec", "") if reader else "",
        reader_provider=getattr(reader, "provider", None) if reader else None,
        reader_pinned=getattr(reader, "pinned", None) if reader else None,
        reader_system_prompt=system_prompt,
        reader_type_fragments=type_fragments,
        judge_model=(judge.model_id if judge is not None
                     else "n/a (retrieval-only)"),
        ks=ks,
        top_k=top_k,
        split=split,
        ingest_mode=ingest_mode,
        failures=failures,
        preflight=preflight,
        embedder_status=embedder_status,
        # R1 (#1540) D7: knob values recorded verbatim in the methodology
        # (the run protocol step-2 gate consumes them). C1/C2 (#1745): the
        # reader-context item cap + evidence-boost knobs ride the same
        # dict so published numbers carry their methodology.
        r1_knobs={
            "chunk_turns": chunk_turns,
            "context_token_cap": max_context_tokens,
            "max_chunks_per_session": max_chunks_per_session,
            "context_item_cap": (context_item_cap
                                 if context_item_cap is not None
                                 else DEFAULT_CONTEXT_ITEM_CAP),
            "evidence_boost": bool(evidence_boost),
            "evidence_boost_verbatim": (
                evidence_boost_verbatim
                if evidence_boost_verbatim is not None
                else DEFAULT_EVIDENCE_BOOST_VERBATIM),
            "evidence_boost_source": (
                evidence_boost_source
                if evidence_boost_source is not None
                else DEFAULT_EVIDENCE_BOOST_SOURCE),
            # #1786 (Task 2 Step 5): the recoverable-class resume-mode flag
            # + the write-path retry knobs recorded in the methodology so
            # the revalidation comparison can distinguish retried outcomes
            # (the flag is NOT fingerprinted — a recorded resume-mode;
            # the knobs are fingerprinted AND recorded here for truthfulness).
            "retry_failed": bool(retry_failed),
            "ingest_write_retries": ingest_write_retries,
            "ingest_question_retries": ingest_question_retries,
            "resume_attempts_cap": resume_attempts_cap,
        },
        # R5 (#1544) D7: TR knob values recorded verbatim in the
        # methodology (the run protocol step-2/6 knob sweeps consume them;
        # tr_top_k and R1's context cap are complementary flood controls).
        # #1786 (R5): the per-arm retrieval budgets ride the same channel —
        # methodology.retrieval_config.hybrid_budget_ms (the eval's elevated
        # deadline via the _elevated_timeout_ms seam; recorded as the SDK
        # default when the caller did not elevate) + vector_budget_ms (the
        # SDK-pinned VECTOR_TIMEOUT_MS — NOT eval-configurable).
        r5_knobs={
            "tr_top_k": tr_top_k,
            "tr_date_weight": tr_date_weight,
            "tr_events": tr_events,
            "retrieval_config": {
                "hybrid_budget_ms": (retrieval_budget_ms
                                      if retrieval_budget_ms is not None
                                      else DEFAULT_RETRIEVAL_BUDGET_MS),
                "vector_budget_ms": VECTOR_TIMEOUT_MS,
            },
        },
        # R6 (#1545): the effective rerank config + pre-warm outcome → the
        # report's rerank block (config + aggregates). None on baseline runs
        # → zero rerank keys in the report (the no-flag-report contract).
        rerank_config=(rr["config"] | {
            "model_load_ms": (rerank_prewarm or {}).get("model_load_ms"),
            "prewarmed": (rerank_prewarm or {}).get("prewarmed"),
            "prewarm_reason": (rerank_prewarm or {}).get("reason"),
        }) if rr["rerank_on"] else None,
        # M7 (#1527): publication-gated audit + run-hygiene provenance.
        dataset_semantics_audit=dataset_semantics_audit,
        integrity_threshold=integrity_threshold,
        integrity_justification=integrity_justification,
        python_version=f"{sys.version_info[0]}.{sys.version_info[1]}."
              f"{sys.version_info[2]}",
        workers=workers,
        dataset_fingerprint=dataset_fingerprint,
    )


#: #1785 (P2-7): whether the parallel report.py's ``build_report`` has
#: landed the ``revalidate`` param yet — the outcomes_to_report forward is
#: guarded so either parallel merge order stays green (a pre-param
#: build_report must not TypeError).
_BUILD_REPORT_REVALIDATE_OK: frozenset[str] = frozenset(
    getattr(build_report, "__code__", None).co_varnames
    if getattr(build_report, "__code__", None) is not None else ())


#: #2185 (Task 6): build_report's ``usage_overhead`` param presence — same
#: guard rationale as _BUILD_REPORT_REVALIDATE_OK (the #2185 run.py/report.py
#: edits land in one PR, but the guard keeps either merge order green if the
#: parallel file drifts).
_BUILD_REPORT_USAGE_OK: frozenset[str] = frozenset(
    getattr(build_report, "__code__", None).co_varnames
    if getattr(build_report, "__code__", None) is not None else ())


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
    # #1349 vector arm: forwarded to build_report — retriever routing +
    # injected model + mode ride the report methodology + provenance.
    retriever: str = "hybrid",
    model: str | None = None,
    query_prompt: str | None = None,
    retrieval_only: bool = False,
    surface: str = "embedded",
    run_key: str | None = None,
    # #1785 (Task 1 Step 4 / P2-7): the revalidation mode signal — forwarded
    # to build_report's certifier (a Task-5 re-validation refuses ANY gate-
    # red outcome / strategy timeout / mid-write failure — no exclusion
    # path). The forwarding is unit-tested (P2-6: a regression dropping it
    # fails CI); build_report's parallel #1785 file owns the param — the
    # forward is guarded so either merge order stays green.
    revalidate: bool = False,
    # R3 (#1542) D5: forwarded to build_report — the dense-leg methodology
    # keys are always emitted (not_checked default when omitted).
    embedder_status: dict | None = None,
    r1_knobs: dict[str, Any] | None = None,
    # R5 (#1544) D7: the TR knob values (tr_top_k / tr_date_weight /
    # tr_events) recorded in the report methodology — same pattern as
    # ``r1_knobs``.
    r5_knobs: dict[str, Any] | None = None,
    # R6 (#1545): the effective rerank config + pre-warm outcome (None on
    # baseline runs → zero rerank keys in the report).
    rerank_config: dict[str, Any] | None = None,
    # M7 (#1527): publication-gated audit + run-hygiene provenance.
    dataset_semantics_audit: dict[str, Any] | None = None,
    integrity_threshold: float = 0.0,
    integrity_justification: str | None = None,
    python_version: str = "",
    workers: int = 1,
    dataset_fingerprint: str = "unknown",
    # #2185 (Task 6): the drained collector overhead envelope — forwarded to
    # build_report's conditional usage block (breaker-open + failed +
    # preflight spend; None on mock/usage-free runs → no new report keys).
    usage_overhead: dict[str, Any] | None = None,
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
                "n_ingest_errors", "ingest_error_text",
                # #1746 (D7): llm telemetry + recovery ride the Layer-1
                # projection — the truncated_valid readout consumes them.
                "llm_calls", "llm_retries", "llm_truncated", "recovery",
                "context_tokens",
                # #1349 vector arm: the gate's per-question metrics ride the
                # Layer-1 projection (extract_report in gate_1349.py reads
                # them from the report's outcomes) + the breaker-open dropped
                # markers (dropped-question accounting, never recall 0).
                "ndcg@10", "p@10", "p@5", "ranked_ids",
                "evidence_turn_matches", "breaker_open", "dropped_reason",
                # C4 (#1745): the reader-surface evidence metric + the C2
                # pre/post ablation + the boost block ride the projection
                # (read via o.get so a pre-#1745 checkpoint resumes without
                # KeyError — the keys stay absent until the outcome carries
                # them).
                "reader_evidence@k", "ranked_ids_pre_boost",
                "evidence_boost",
                # #1948: the reader-surface metric rides the projection
                # alongside reader_evidence@k (absent until the outcome
                # carries it — pre-#1948 checkpoints resume without
                # KeyError).
                "reader_surface@k",
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
                # #1785 (Task 1): the graph-integrity gate reasons (phase-
                # keyed lists) ride the Layer-1 projection — read via
                # o.get defaults so a pre-change checkpoint resumes without
                # KeyError (the keys stay absent until the outcome carries
                # them; plan P1-3).
                "gate_reasons", "post_retrieval_reasons",
                # #1786 (Task 1 Step 5): the per-question recovery counters
                # (ingest_retries = write-stage retry count; whole_question_
                # retries = the R2 count, 0 or 1 — read via o.get so a
                # pre-feature checkpoint resumes without KeyError).
                "ingest_retries", "whole_question_retries",
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
                  "tr_window_fallback": bool(o.get("tr_window_fallback", False)),
                  # R6 (#1545): the rerank pass + latency are added to the
                  # selector ONLY when the outcome carries them (a baseline
                  # outcome must NEVER project rerank_pass: null; stale
                  # pre-R6 checkpoint outcomes are skipped by the same
                  # condition and read via .get() so they can't KeyError).
                  **({"rerank_pass": o["rerank_pass"],
                      "rerank_latency_ms": o.get("rerank_latency_ms", 0.0)}
                     if o.get("rerank_pass") is not None else {}),
                  # #2185 (Task 6): the per-question LLM usage envelope is
                  # projected ONLY when the outcome carries it (conditional
                  # rerank_pass pattern — mock/pre-seam outcomes NEVER gain
                  # a null llm_usage key; the published report stays
                  # byte-compatible with pre-#2185 consumers).
                  **({"llm_usage": o["llm_usage"]}
                     if o.get("llm_usage") is not None else {}),
                  # #1747 (round-17 code review): breaker_open outcomes are
                  # published with the SAME no-error-signal shape the grader
                  # consumed — the runner's raw dropped outcome (run.py
                  # breaker construction) carries NO valid/error_classes
                  # keys, so build_report grades it clean (n_excluded_hard
                  # == 0, valid True), but this selector materialized them
                  # as null, and a PRESENT-null error_classes re-grades HARD
                  # (round-10 fail-closed shape) — every persisted
                  # vector-arm report with a breaker drop self-contradicted
                  # its own verdict. Emit the shape the GRADER actually
                  # consumed for the breaker outcome: when the raw grade is
                  # CLEAN, publish valid/error_classes exactly as graded
                  # (missing → the clean defaults True/{}); when the raw
                  # grade is hard/recoverable (a TAMPERED breaker outcome
                  # carrying a hard census, a present non-bool/falsy valid
                  # flag, or a recoverable-only census), the selector's
                  # materialization stands — the published record re-grades
                  # identically to what the verdict read (round-17 review-
                  # fix: the earlier key-presence-only override stomped a
                  # present valid: False to True, and left one-key-present
                  # hybrid shapes (valid present / error_classes absent and
                  # vice versa) publishing a clean-shape contradiction).
                  **({"valid": o.get("valid", True),
                      "error_classes": o.get("error_classes", {})}
                     if o.get("breaker_open") and _outcome_grade(o) == "clean"
                     else {})}
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
        rerank_config=rerank_config,
        embedder_status=embedder_status,
        extra=extra,
        # #1349 vector arm: retriever/model/query_prompt/retrieval_only/
        # surface/run_key ride the report methodology + provenance.
        retriever=retriever,
        model=model,
        query_prompt=query_prompt,
        retrieval_only=retrieval_only,
        surface=surface,
        run_key=run_key,
        # M7 (#1527): publication-gated audit + run-hygiene provenance.
        dataset_semantics_audit=dataset_semantics_audit,
        integrity_threshold=integrity_threshold,
        integrity_justification=integrity_justification,
        python_version=python_version,
        workers=workers,
        dataset_fingerprint=dataset_fingerprint,
        # #1785 (P2-7): forward the revalidation mode signal to the
        # certifier — guarded against either parallel merge order (a
        # pre-param build_report must not TypeError; the parallel report.py
        # file adds ``revalidate: bool = False``).
        **({"revalidate": revalidate}
           if "revalidate" in _BUILD_REPORT_REVALIDATE_OK else {}),
        # #2185 (Task 6): the drained collector overhead envelope feeds the
        # conditional usage block (same-PR param — direct forward; None on
        # usage-free runs keeps the mock report byte-identical).
        **({"usage_overhead": usage_overhead}
           if "usage_overhead" in _BUILD_REPORT_USAGE_OK else {}),
    )


def _sha16(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


#: The judge rubric identity the parity module hashes against (must match
#: battery.parity's judge_rubric_id source string).
JUDGE_RUBRIC_ID = "longmemeval-official"


def reader_prompt_source() -> str:
    """The reader prompt content the parity module hashes. Mirrors the
    product reader prompt (tortoise/reader.py); must be kept in sync with
    battery.parity.runner (the unchanged-check compares both sides).

    R1 (#1540) D6/D7 + C1 (#1745): the reader consumes the budget-capped
    RANK-INTERLEAVED context (points + raw chunks in true RRF rank order,
    bounded by the token budget AND the context item cap) — the parity
    hash changes; the #1144 baseline record is refreshed at the next
    parity run (a run-time action — no committed baseline exists).

    #1773 closure (#1987 Task 10): the hash now covers the A1 universal
    partial-knowledge abstention clause (``_ABSTRACTION_FRAGMENT`` — the
    two-phase presence-commit/abstention wording appended to EVERY
    question's prompt) so prompt drift on the abstention substance is
    human-visible in the report; the clause text itself is appended below
    and the recorded ``reader_prompt_hash`` (``_sha16(reader_prompt_source())``)
    changes with it.
    """
    from tortoise.reader import _ABSTRACTION_FRAGMENT as _a1
    return (
        "Current Date: {question_date} header + per-session date annotation "
        "on every retrieved chunk (question_date + haystack_dates surfaced — "
        "temporal-reasoning questions are answerable); rank-interleaved "
        "budget-capped context (C1 #1745, replaces R1's points-first "
        "UX-3 #1540): extracted points AND raw turn-granular chunks render "
        "in true RRF rank order bounded by the token budget and the "
        "context_item_cap; type-fragments: temporal (date math), "
        "preference (option commitment), knowledge-update (answer-from-newer, "
        "date-conditional: current-value → newest/superseding point, "
        "point-in-time → chain-walk by session date — E5 CORRECTS markers + "
        "session-date annotations, no parallel mechanism), multi-session "
        "(aggregation: distinct events, no double-count, reconcile by date); "
        "universal A1 abstention clause (#1775 two-phase presence-commit/"
        "abstention) appended to EVERY question: " + _a1
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
    # #1747 (round-17 code-review P2): valid=false is commonly decided by
    # terms NOT in the line above (the hard veto, the excluded-outcome
    # veto, or the vacuity guard — an operator can see valid: false with
    # invalid_rate 0.0 where the only printed numbers did NOT decide the
    # verdict). Surface the deciding terms when the verdict is false —
    # including the vacuity evidence (n_attempted + dropped + n_excluded:
    # when the rate and veto terms all pass, a false verdict means the
    # outcome-derived attempted set was empty — round-17 review-fix: the
    # line used to omit those, reading "decided by 0/0/0" for an all-
    # dropped run).
    if integ.get("valid") is False:
        print(f"  invalidity decided by: n_hard_invalid "
              f"{integ.get('n_hard_invalid')}, n_excluded_hard "
              f"{integ.get('n_excluded_hard')}, n_excluded "
              f"{integ.get('n_excluded')}, n_attempted "
              f"{integ.get('n_attempted')} (invalid_rate "
              f"{integ.get('invalid_rate')} vs threshold "
              f"{integ.get('threshold')}; dropped "
              f"{report.get('n_dropped', 0)}; vacuity = all "
              "non-veto/rate terms pass with an empty outcome-derived "
              "attempted set)")
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
    # #1946: the extraction-health gate readout — printed BEFORE the score
    # so the healthy/degraded split is seen before the blended accuracy
    # (reval3: 33/50 questions ingested 0 semantic points yet the 0.880
    # blended healthy 0.824 + degraded 0.909 into one headline).
    eh = report.get("extraction_health")
    if eh:
        print("── extraction health ──")
        flag = "⚠ DEGRADED" if eh.get("status") == "degraded" else "healthy"
        print(f"status: {flag}  (healthy {eh.get('healthy_n')} / degraded "
              f"{eh.get('degraded_n')}, degraded_fraction "
              f"{eh.get('degraded_fraction')} >= threshold "
              f"{eh.get('threshold')}; min_points {eh.get('min_points')})")
        pop = eh.get("per_population_accuracy")
        if pop:
            for label, d in (("healthy", pop.get("healthy") or {}),
                             ("degraded", pop.get("degraded") or {})):
                acc = d.get("accuracy")
                print(f"  {label:<9} accuracy "
                      f"{acc if acc is not None else 'n/a'} (n={d.get('n')})")
            if eh.get("status") == "degraded":
                if eh.get("degraded_n"):
                    print("  ⚠ degraded population present — the overall "
                          "accuracy blends two populations; read the "
                          "per-population numbers above")
                else:
                    # the flag fired on the run census alone (no degraded
                    # question outcomes) — surface the deciding term,
                    # mirroring the integrity block's "invalidity decided
                    # by" line.
                    print("  ⚠ run flagged by census: fatal_402_billing / "
                          "empty_embed_list present in the error census "
                          "(above) — extraction was billing/embedding-limited")
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
    lat_keys = ["retrieval", "reader", "judge", "ingest",
                "total_per_question"]
    if "rerank" in lat:                       # R6: baseline reports carry no
        lat_keys.insert(1, "rerank")          # rerank latency block
    for key in lat_keys:
        d = lat.get(key, {})
        print(f"  {key:<20} {d}")
    rr_block = report.get("rerank")
    if rr_block:
        print("rerank (R6): enabled={} model={} lambda={} cap={} "
              "pool={} applied_fraction={} degraded_n={} "
              "max_session_chunks_max={}".format(
                  rr_block.get("enabled"), rr_block.get("model"),
                  rr_block.get("lambda_"), rr_block.get("per_session_cap"),
                  rr_block.get("pool_size"),
                  rr_block.get("applied_fraction"),
                  rr_block.get("degraded_n"),
                  rr_block.get("max_session_chunks_max")))
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
    # #1349 vector arm (embedder selection): retriever routing + injected model.
    p.add_argument("--retriever", default="hybrid", choices=("hybrid", "vector"),
                   help="retriever: 'hybrid' (default RRF) or 'vector' (#1349 "
                        "vector-only arm — run_vector_query ONLY, nDCG@10 + "
                        "P@10/P@5 emitted; breaker-open questions dropped)")
    p.add_argument("--model", default=None,
                   help="embedding model short name injected before the run "
                        "(tools/embedder_probe PROBE_MODELS: minilm | arctic-xs "
                        "| arctic-s | bge-small; default = production literal)")
    p.add_argument("--query-prompt", default=None,
                   help="named prompt template threaded to the injected model "
                        "(e.g. 'query' for snowflake-arctic vendor configs)")
    p.add_argument("--retrieval-only", action="store_true",
                   help="run retrieval WITHOUT reader/judge — accuracy is None "
                        "and the report is the #1349 gate's vector recall block")
    p.add_argument("--db", default=None,
                   help="#1349 --db mode: a FalkorDB URI "
                        "(docker://|redis://|rediss://|bolt://) — per-question "
                        "SDKs scoped to (question, model-run) graphs, HNSW "
                        "queryNodes surface. Without it, embedded tempdir "
                        "graphs (brute-force surface).")
    p.add_argument("--spot-check", action="store_true",
                   help="#1349 HNSW spot-check: run winner AND control in one "
                        "pass over the FULL question set, emit the paired "
                        "{cleared, n, metric_deltas} artifact at the pinned "
                        "path (requires --db and --model)")
    p.add_argument("--load-timeout", type=float, default=None,
                   help="EmbeddingModel load timeout override (seconds) — the "
                        "first model load on a contended machine can exceed "
                        "the 90s default)")
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
                        "default 8000 — rank-interleaved, C1 #1745: points + chunks "
                        "in true RRF order bounded by the token budget and the "
                        "context item cap)")
    p.add_argument("--max-chunks-per-session", type=_positive_int, default=None,
                   help="per-session raw-chunk cap in the retrieval pool "
                        "(env TORTOISE_LME_MAX_CHUNKS_PER_SESSION; default 3 — "
                        "E2E-1 session-dedup, R1 #1540; C5 #1745 raised the "
                        "cap 2->3 so the evidence chunk is not capped out)")
    # C1 (#1745): the reader-context item cap (env first, CLI overrides;
    # the token budget --context-cap selects within it; TR questions keep
    # the pinned --tr-top-k item cap).
    p.add_argument("--context-items", type=_positive_int, default=None,
                   help="reader context ITEM cap (env TORTOISE_LME_CONTEXT_ITEMS; "
                        "default 40 — the 8k token budget rarely binds at ~114 "
                        "tok/item, so the item cap bounds reader flood, C1 #1745)")
    # C2 (#1745): the evidence-mark boost — tri-state --evidence-boost /
    # --no-evidence-boost (None default so the TORTOISE_LME_EVIDENCE_BOOST
    # env still applies; OFF by default in code — ON only for the
    # re-validation run).
    eb = p.add_mutually_exclusive_group()
    eb.add_argument("--evidence-boost", dest="evidence_boost",
                    action="store_true", default=None,
                    help="enable the C2 evidence-mark rank boost (marked "
                         "hits move up by a stable rank offset before "
                         "evidence_recall@k; default: env "
                         "TORTOISE_LME_EVIDENCE_BOOST — OFF by default in "
                         "code, ON for the re-validation run, #1745)")
    eb.add_argument("--no-evidence-boost", dest="evidence_boost",
                    action="store_false", default=None,
                    help="disable the C2 evidence-mark boost even when "
                         "TORTOISE_LME_EVIDENCE_BOOST is set "
                         "(tri-state: explicit flags beat the env)")
    p.add_argument("--evidence-boost-verbatim", type=float, default=None,
                   help="verbatim/raw-chunk mark rank-offset multiplier "
                        "(env TORTOISE_LME_EVIDENCE_BOOST_VERBATIM; default "
                        f"{DEFAULT_EVIDENCE_BOOST_VERBATIM})")
    p.add_argument("--evidence-boost-source", type=float, default=None,
                   help="source-session-only mark rank-offset multiplier "
                        "(env TORTOISE_LME_EVIDENCE_BOOST_SOURCE; default "
                        f"{DEFAULT_EVIDENCE_BOOST_SOURCE})")
    p.add_argument("--mock", action="store_true",
                   help="offline mode: MockReader + MockJudge, no API keys (CI)")
    p.add_argument("--skip-preflight", action="store_true",
                   help="bypass the pre-flight API gate AND the dense-leg "
                        "(embedder) gate (debugging/offline only — the "
                        "run-protocol gate must be ON for pilot/500 runs; "
                        "a skipped dense leg is recorded as unavailable in "
                        "the report methodology)")
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
    p.add_argument("--session-workers", type=int, default=1,
                   help="parallelize session extraction WITHIN a question "
                        "(pilot #1549: the LLM phase is the wall-clock "
                        "dominant cost; 8 = up to ~10x faster when the API "
                        "sustains it; requires the model factory path)")
    p.add_argument("--workers", type=int, default=1,
                   help="parallel question workers (default 1 = sequential). "
                        "Each question runs in its own isolated graph; the "
                        "practical ceiling is provider rate limits + machine "
                        "memory (each worker spawns an embedded redislite "
                        "server). 8-16 on a quiet machine; #1786 (R7): ≤ 3 "
                        "recommended for --db runs on contended hosts — the "
                        "eval ingest is itself a load generator (5 workers × "
                        "~25 min ingest per question on a shared FalkorDB "
                        "container stalls the write path)")
    # R6 (#1545): the rerank layer — tri-state --rerank/--no-rerank (None
    # default so the TORTOISE_LME_RERANK env still applies), pool/cap/lambda
    # validated at parse time (boundary values accepted; the env path is
    # re-validated in run_main only when the effective rerank is ON).
    rr = p.add_mutually_exclusive_group()
    rr.add_argument("--rerank", dest="rerank", action="store_true",
                    default=None,
                    help="enable the R6 post-fusion cross-encoder + MMR "
                         "rerank stage (default: env TORTOISE_LME_RERANK — "
                         "fail-safe OFF: only 1/true/yes/on enables; "
                         "requires a FRESH checkpoint, #1545)")
    rr.add_argument("--no-rerank", dest="rerank", action="store_false",
                    default=None,
                    help="disable rerank even when TORTOISE_LME_RERANK is set "
                         "(tri-state: explicit flags beat the env)")
    p.add_argument("--rerank-pool", type=_positive_int, default=None,
                   help="rerank pool depth (env TORTOISE_LME_RERANK_POOL; "
                        "default 40; honored with rerank OFF too — the "
                        "pool-only isolation arm, OQ5; >= 1; the effective "
                        "applied pool is max(pool, max(k)))")
    p.add_argument("--rerank-cap", type=_positive_int, default=None,
                   help="per-session MMR cap (env TORTOISE_LME_RERANK_CAP; "
                        "default 2; >= 1; E2E-10 caps per-session context "
                        "chunks <= 1-2)")
    p.add_argument("--rerank-lambda", type=_rerank_lambda, default=None,
                   help="MMR lambda in [0,1] (env TORTOISE_LME_RERANK_LAMBDA; "
                        "default 0.7; 1.0 = pure rerank, 0.0 = pure "
                        "similarity; boundary values accepted)")
    p.add_argument("--rerank-model", default=None,
                   help="cross-encoder model name (env "
                        "TORTOISE_LME_RERANK_MODEL; default "
                        "cross-encoder/ms-marco-MiniLM-L6-v2)")
    p.add_argument("--checkpoint", default=None,
                   help="partial-results state file (JSON) for error isolation "
                        "+ resume: completed/failed questions are checkpointed "
                        "after every question and skipped on re-run")
    p.add_argument("--retry-failed", action="store_true",
                   help="#1786 (R3): on resume, RE-ATTEMPT transient-failed "
                        "questions instead of skipping them — only "
                        "ingest:retries_exhausted failure entries with "
                        "retryable=true (or a legacy transport repr) and "
                        f"attempts < {RESUME_ATTEMPTS_CAP}; attempts are "
                        "bounded across resumes (the in-run whole-question "
                        "retry counts as the first); on success the failure "
                        "entry is removed in one flocked write and the report "
                        "grades the qid clean. NOT fingerprinted (a recorded "
                        "resume-mode — the revalidation protocol sets it); "
                        "default off for back-compat")
    # #1785 (Task 1 Step 4 / Task 3): the revalidation mode signal + the
    # loss-location replay flags — ALL fingerprint-excluded (a knob change
    # must never alter resume-eligibility of pre-change checkpoints).
    p.add_argument("--revalidate", action="store_true",
                   help="#1785 (Task 5): revalidation mode — the report "
                        "certifier refuses ANY gate-red outcome / strategy "
                        "timeout / mid-write failure (no exclusion path; the "
                        "mid-run watchdog aborts on the FIRST such outcome); "
                        "env TORTOISE_LME_REVALIDATE=1; resolution CLI > env "
                        "> default (False)")
    p.add_argument("--per-session-census", action="store_true",
                   help="#1785 (Task 3): loss-location replay — a read-verified "
                        "per-session census interleaves with ingest (after each "
                        "session's Phase A/C writes) under concurrent write "
                        "load; the trace + verdict (H6a / H6b / INCONCLUSIVE / "
                        "H6a unexercised) are written to the work dir. "
                        "Diagnostic-only — fingerprint-excluded; forces "
                        "session_workers=1 for deterministic measurement")
    p.add_argument("--replay-load-workers", type=int, default=0,
                   help="#1785 (Task 3): concurrent synthetic write workers "
                        "hammering scratch namespaces while the per-session-"
                        "census replay ingests (reproduces the degraded run's "
                        "write pressure). 0 = no load injection")
    p.add_argument("--replay-signature-reproduced", action="store_true",
                   help="#1785 (Task 3): record that the real run's failure "
                        "signature (concurrent TimeoutErrors / AOF-fsync "
                        "stalls) WAS reproduced during the replay — required "
                        "for an INCONCLUSIVE verdict to count as pass-with-"
                        "evidence; without it the verdict is 'H6a unexercised "
                        "— env remediation only' (NOT passing, plan P2-3)")
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




def spotcheck_artifact_path(winner: str) -> Path:
    """Pinned artifact path — gate_1349.py's "HNSW artifact present+cleared"
    check reads exactly this file."""
    return SPOTCHECK_ARTIFACT_DIR / f"hnsw-spotcheck-{winner}.json"


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _delta_summary(deltas: list[float | None]) -> dict[str, Any]:
    """One-sided paired normal-approximation summary of winner−control deltas.

    ``deltas`` is the FULL-length per-question list in burn order; entries
    that are None are DROPPED questions (breaker_open/absent in either arm)
    and are skipped by the n/mean/var/p math — but they keep their slot in
    the emitted ``deltas`` list so the gate sees the full coverage.
    """
    valid = [d for d in deltas if d is not None]
    n = len(valid)
    base = {"n": n, "deltas": list(deltas)}
    if n == 0:
        return {"mean_delta": 0.0, "one_sided_p": None, **base}
    mean = sum(valid) / n
    if n == 1:
        return {"mean_delta": round(mean, 6), "one_sided_p": None, **base}
    var = sum((d - mean) ** 2 for d in valid) / (n - 1)
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
    """Paired winner-vs-control artifact shape: {cleared, n, dropped_qids,
    metric_deltas}.

    ``n`` is the GATE question count the spot-check ran — the union of both
    arms EXCLUDING ``_abs`` abstention questions (mirroring the gate's
    ``is_gate_question`` filter; the gate requires art n == its filtered
    burn set, so a producer that counts the full split never matches on a
    split containing abstentions). Never the shrinking intersection. Each
    metric's ``deltas`` is full-length (one entry per qid); a qid that is
    breaker_open or absent in either arm is recorded as a None sentinel and
    listed in ``dropped_qids`` (the paired p skips it)."""
    k = "10" if "10" in (str(x) for x in ks) else str(ks[-1])

    def _gate_qid(qid: str) -> bool:
        # Exclude _abs abstentions (the gate's is_gate_question filter).
        # Divergence note: gate_1349.is_gate_question returns False for a
        # MISSING question_type; here absent type counts as included. Real
        # outcomes always carry it from the dataset (so the divergence is
        # unreachable in production), and the lenient side only over-counts
        # the artifact n → BLOCK, never a false pass (fail-closed). The
        # test fixtures omit question_type, hence the leniency.
        if "_abs" in qid:
            return False
        o = results[winner].get(qid) or results[control].get(qid) or {}
        qt = o.get("question_type")
        if not qt:
            return True
        return (str(qt).startswith("single-session-")
                or qt in {"temporal-reasoning", "knowledge-update",
                          "multi-session"})

    full_qids = sorted(q for q in
                       (set(results[winner]) | set(results[control]))
                       if _gate_qid(q))

    def _dropped(qid: str) -> bool:
        w, c = results[winner].get(qid), results[control].get(qid)
        return (w is None or c is None
                or w.get("breaker_open") or c.get("breaker_open"))

    dropped_qids = [qid for qid in full_qids if _dropped(qid)]
    metric_deltas: dict[str, Any] = {}
    for metric in ("turn_recall@10", "ndcg@10"):
        deltas: list[float | None] = []
        for qid in full_qids:
            w, c = results[winner].get(qid), results[control].get(qid)
            if _dropped(qid):
                deltas.append(None)  # sentinel: dropped, no paired delta
                continue
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
        "n": len(full_qids),
        "dropped_qids": dropped_qids,
        "metric_deltas": metric_deltas,
        "cleared": cleared,
        "rule": ("one-sided paired normal-approximation z-test per co-primary "
                 "metric over the non-dropped questions of the FULL question "
                 "set (n records the full count; a post-hoc n that shrinks "
                 "until p<0.10 is forbidden — dropped breaker_open/absent "
                 "qids keep None sentinels in the full-length deltas and are "
                 "listed in dropped_qids); BH q=0.10 over m=2 → cleared iff "
                 "min one-sided p ≤ 0.05. gate_1349.py recomputes the exact "
                 "bootstrap p from the per-question deltas."),
        "checkpoint_key_prefix": "hnsw__vector__{model}__{prompt}",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "git_sha": git_sha(),
    }


def _run_spot_check(args, instances: list[dict], *, ks, top_k, db_uri) -> dict:
    """Winner AND control in one pass → one paired artifact (documented shape)."""
    winner = args.model
    control = "minilm"
    cache_root = Path(args.cache_dir).expanduser() if args.cache_dir else ds.cache_dir()
    results: dict[str, dict[str, dict]] = {}
    for model in (winner, control):
        state = inject_model(model, query_prompt=args.query_prompt,
                              load_timeout=getattr(args, "load_timeout", None))
        model_id = state.get("hf_id") or PROBE_MODELS.get(model) or DEFAULT_MODEL_ID
        cache = encode_cache.EncodeCache(
            encode_cache.cache_path_for(cache_root, model, args.query_prompt),
            model_id=model_id, prompt_name=args.query_prompt)
        # Activate the model-keyed cache around the whole pass: it wraps
        # compute_embedding (ingest-time interception) AND sets the
        # _ACTIVE_CACHE global that encode_query reads (query-encode
        # persistence across questions/processes). Without .active() the
        # cache is dead code — every question re-encodes the overlapping
        # haystack (the 5-10x redundancy the cache exists to eliminate).
        with cache.active():
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


def run_main(argv: list[str] | None = None) -> dict[str, Any]:
    _assert_python_version()
    parser = _build_parser()
    args = parser.parse_args(argv)
    # M7 #1739 / #1742: session-parallel extraction exists only on the v2
    # ingest path (the worker factory). The flag was previously parsed but
    # never threaded — a silent no-op on every mode; now it is wired for
    # v2 and REJECTED loudly elsewhere (fail fast with an accurate message
    # instead of a confusing fingerprint mismatch or a silent no-op). The
    # --compare pure-artifact branch is flag-tolerant (handled before any
    # run machinery).
    if args.session_workers > 1 and args.ingest_mode != "v2" \
            and not args.compare:
        parser.error("--session-workers > 1 requires --ingest-mode v2 "
                     "(session-parallel extraction exists only on the v2 "
                     "path)")

    # --db: FalkorDB URI handling mirroring tests/eval/retrieval/run.py:549
    # (URI → TORTOISE_DB_URI → TortoiseSDK()). Non-URI --db is rejected —
    # the per-question graph isolation derives from the URI server. Test
    # isolation (#1349): the URI reaches the SDK via the env, restored on
    # exit so a leaked TORTOISE_DB_URI can't silently change later SDKs.
    db_uri = args.db
    if db_uri is None and os.environ.get("TORTOISE_DB_URI"):
        db_uri = os.environ["TORTOISE_DB_URI"]
    if db_uri is not None:
        if "://" not in db_uri:
            parser.error(
                f"--db must be a FalkorDB URI (docker://|redis://|rediss://|"
                f"bolt://), got {db_uri!r} — the per-question isolated graphs "
                f"derive from the URI's server")
        with _temporary_env_var("TORTOISE_DB_URI", db_uri):
            return _run_main(parser, args, db_uri)
    return _run_main(parser, args, db_uri)


def _run_main(parser: argparse.ArgumentParser, args,
              db_uri: str | None) -> dict[str, Any]:
    """Body of :func:`run_main` (split so the caller can scope the
    TORTOISE_DB_URI env to the run — issue #1349 test isolation)."""
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
    # C1 (#1745): reader-context item cap (env first, CLI overrides;
    # >= 1 validated). TR questions ignore it — tr_top_k is the pinned TR
    # item cap.
    context_item_cap = _resolve_int_knob(
        "TORTOISE_LME_CONTEXT_ITEMS", DEFAULT_CONTEXT_ITEM_CAP,
        args.context_items)
    # C2 (#1745): evidence-mark boost — tri-state (None default so the
    # TORTOISE_LME_EVIDENCE_BOOST env still applies; OFF by default in
    # code — fail-safe: only 1/true/yes/on enables, mirroring the R6
    # rerank gate). Per-class multipliers default to the retrieve.py
    # constants (env overrides; CLI beats env; < 1.0 fails loudly —
    # resolved ONLY when the boost is on, so a stray multiplier env never
    # aborts a boost-off run, review P2-2).
    if args.evidence_boost is not None:
        evidence_boost = args.evidence_boost
    else:
        eb_env = (os.environ.get("TORTOISE_LME_EVIDENCE_BOOST") or "")
        evidence_boost = eb_env.strip().lower() in _TRUTHY
    evidence_boost_verbatim = args.evidence_boost_verbatim
    evidence_boost_source = args.evidence_boost_source
    if evidence_boost:
        evidence_boost_verbatim = _resolve_boost_float(
            "TORTOISE_LME_EVIDENCE_BOOST_VERBATIM",
            DEFAULT_EVIDENCE_BOOST_VERBATIM, args.evidence_boost_verbatim)
        evidence_boost_source = _resolve_boost_float(
            "TORTOISE_LME_EVIDENCE_BOOST_SOURCE",
            DEFAULT_EVIDENCE_BOOST_SOURCE, args.evidence_boost_source)
    else:
        # Off-path hygiene (review P2-2, mirrors the R6 precedent in this
        # file): the ambient TORTOISE_LME_EVIDENCE_BOOST_VERBATIM/_SOURCE
        # env vars are NOT read when the boost is off — a stray/stale
        # value must never abort a baseline run (or gate its fingerprint).
        # EXPLICIT CLI args are still validated even on the off path — a
        # user typo (--evidence-boost-verbatim 0.5 / nan) fails loudly
        # (test_knob_cli_validation contract). After validation the values
        # are DROPPED (set to None) so the inert knobs never ride the
        # fingerprint or the methodology on a boost-off run (review
        # re-check: a checkpoint must not become sensitive to a knob that
        # never affected the results).
        if args.evidence_boost_verbatim is not None:
            _resolve_boost_float(
                "TORTOISE_LME_EVIDENCE_BOOST_VERBATIM",
                DEFAULT_EVIDENCE_BOOST_VERBATIM, args.evidence_boost_verbatim)
        if args.evidence_boost_source is not None:
            _resolve_boost_float(
                "TORTOISE_LME_EVIDENCE_BOOST_SOURCE",
                DEFAULT_EVIDENCE_BOOST_SOURCE, args.evidence_boost_source)
        evidence_boost_verbatim = None
        evidence_boost_source = None
    # R5 (#1544) TR knobs: argparse defaults (12 / 0.5 / events-on),
    # recorded verbatim in the report methodology (D7).
    tr_top_k = args.tr_top_k
    tr_date_weight = args.tr_date_weight
    tr_events = not args.no_tr_events

    # R6 (#1545): the effective rerank config (CLI > env > default) resolves
    # BEFORE the loop; env resolution + re-validation happen ONLY when the
    # effective rerank is ON (a baseline run never reads the R6 env vars —
    # Gate 7). Then pre-warm the cross-encoder ONCE (also gated on the
    # effective rerank being on — a baseline run never loads the ~90MB
    # model); a pre-warm failure does NOT disable the run (per-question
    # get_scorer TTL-retries continue, D8b) and model_load_ms is recorded
    # once in the report config.
    rr = _resolve_rerank(
        rerank=args.rerank, rerank_model=args.rerank_model,
        rerank_pool=args.rerank_pool, per_session_cap=args.rerank_cap,
        mmr_lambda=args.rerank_lambda, max_k=max(ks))
    rerank_prewarm: dict | None = None
    if rr["rerank_on"]:
        from .rerank import get_scorer
        t0 = time.monotonic()
        scorer, reason = get_scorer(rr["model"])
        model_load_ms = round((time.monotonic() - t0) * 1000.0, 2)
        rerank_prewarm = {"model_load_ms": model_load_ms,
                          "prewarmed": scorer is not None, "reason": reason}
        if scorer is None:
            print(f"[longmem_eval] WARNING: cross-encoder pre-warm failed "
                  f"({reason[:120]}…) — rerank stays ON; per-question "
                  f"TTL-retries continue (each question degrades to "
                  f"rerank-off with a recorded reason, D8b)",
                  file=sys.stderr)

    # #1349 HNSW spot-check: a dedicated evidence producer — winner + control
    # in one pass over the FULL question set, paired artifact at the pinned
    # path. Requires --db (HNSW surface) + --model != control. Guards run
    # BEFORE model injection so a bad invocation fails fast (no model load).
    if args.spot_check:
        if not db_uri:
            parser.error("--spot-check requires --db (the HNSW production "
                         "surface — the spot-check must never run on "
                         "embedded brute-force)")
        if not args.model:
            parser.error("--spot-check requires --model <winner>")
        if args.model == "minilm":
            # winner == control → every metric delta is 0 by construction; the
            # paired artifact would be meaningless. Rejected at the gate.
            parser.error("--spot-check requires a non-control winner model "
                         "(minilm is the control)")
        if args.retriever != "vector":
            parser.error("--spot-check is a vector-arm producer — pass "
                         "--retriever vector")
        if 10 not in ks:
            parser.error("--spot-check measures turn_recall@10 / ndcg@10 — "
                         f"10 must be in --k (got {','.join(map(str, ks))})")

    # #1349: inject the candidate embedder BEFORE the pre-flight so the
    # singleton IS the candidate (probe records resolved revision; HARD-FAILs
    # on load failure). ``--model`` is benchmark-only — the production
    # entrypoint rejects the override env (Dockerfile entrypoint.sh).
    # The spot-check injects winner AND control itself (one pass, one
    # artifact) — never pre-inject here.
    if args.model and not args.spot_check:
        if args.model not in PROBE_MODELS:
            raise SystemExit(
                f"unknown --model {args.model!r}; known: {sorted(PROBE_MODELS)}")
        inject_model(args.model, query_prompt=args.query_prompt,
                     load_timeout=args.load_timeout)

    # R3 (#1542) D2: embedder pre-flight — before dataset load (fail before
    # the ~tens-of-MB download). Real runs refuse to start when the dense
    # leg can't run; --mock warns and continues. The status flows into the
    # report methodology (D5: embedder + vector_strategy always emitted).
    # R3 (#1542) D2: the embedder gate. `--skip-preflight` must ALSO skip
    # this gate — it's the "skip all gates" debugging flag; a real (non-mock)
    # run without it still refuses to start dense-less (#1626).
    embedder_status = _preflight_embedder(
        mock=args.mock or args.skip_preflight)

    instances = ds.load_dataset(
        args.split, limit=args.limit, data_path=args.data,
        cache=Path(args.cache_dir).expanduser() if args.cache_dir else None,
        download=not args.no_download,
    )

    if args.spot_check:
        return _run_spot_check(args, instances, ks=ks, top_k=top_k, db_uri=db_uri)

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
    sw_model_spec = None
    sw_max_tokens = None
    sw_temperature = 0.0
    if args.ingest_mode == "v2":
        # M7 #1739 / #1742: the fingerprinted model must match what actually
        # extracts — session_workers>1 fingerprints the worker-factory config
        # (see _build_cli_extractor_model); the factory's spec + tuning are
        # resolved once and threaded into run_evaluation so the workers
        # serve EXACTLY the fingerprinted config.
        if args.session_workers > 1 and args.extractor_model:
            (sw_model_spec, sw_max_tokens, sw_temperature,
             _sw_pinned_id) = _session_worker_spec_tuning(args.extractor_model)
        extractor_model = _build_cli_extractor_model(
            spec=args.extractor_model, session_workers=args.session_workers)

    # #2185 (A2): bind the run-level usage collector BEFORE preflight — the
    # reader/judge/extractor sinks record from here on; every preflight ping
    # lands as keyless overhead (never evidence). ``reset()`` guards an
    # in-process double run (Am 19). attach() no-ops on mocks (no
    # complete()-bearing member) so --mock runs emit ZERO usage rows — the
    # byte-identical-report contract.
    _collector = lme_usage.get_collector()
    _collector.reset()
    if reader is not None:
        _collector.attach(getattr(reader, "_model", None), stage="reader",
                          provider=getattr(reader, "provider", None))
    if judge is not None:
        _collector.attach(getattr(judge, "_model", None), stage="judge",
                          provider=getattr(judge, "provider", None))
    if extractor_model is not None:
        _collector.attach(extractor_model, stage="ingest", provider=None)

    # M7 #1739 / review #1742 (resource lifecycle): the run-level
    # extractor_model (built above) is closed on EVERY exit path via the
    # try/finally — the CLI previously leaked its session. requests.Session
    # is reusable after close(), so no double-close hazard with the
    # fingerprint guard's served model. (Per-worker model_factory models are
    # built inside ingest_v2.py's worker threads when the parallel path is
    # live — out of run.py's reach and out of PR scope; on main the
    # sequential copy shadows that path (pre-existing duplicate, tracked
    # separately), so the shared extractor_model extracts instead.)
    try:
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
                # M7 #1739 / #1742: wire the session-parallel path (the flag was
                # parsed but never threaded — a silent no-op) and give the worker
                # factory the resolved spec + tuning so the workers serve exactly
                # what the fingerprint records.
                session_workers=args.session_workers,
                session_worker_model_spec=sw_model_spec,
                session_worker_max_tokens=sw_max_tokens,
                session_worker_temperature=sw_temperature,
                embedder_status=embedder_status,
                chunk_turns=chunk_turns, max_context_tokens=context_cap,
                max_chunks_per_session=max_chunks_per_session,
                # C1/C2 (#1745): reader-context item cap + evidence-mark
                # boost (OFF by default in code — the re-validation run
                # enables via env/flag; knobs recorded in the methodology).
                context_item_cap=context_item_cap,
                evidence_boost=evidence_boost,
                evidence_boost_verbatim=evidence_boost_verbatim,
                evidence_boost_source=evidence_boost_source,
                tr_top_k=tr_top_k, tr_date_weight=tr_date_weight,
                tr_events=tr_events,
                rerank=rr["rerank_on"], rerank_model=rr["model"],
                rerank_pool=rr["rerank_pool"],
                per_session_cap=rr["per_session_cap"],
                mmr_lambda=rr["mmr_lambda"],
                rerank_prewarm=rerank_prewarm,
                dataset_fingerprint=dataset_fingerprint,
                integrity_threshold=args.integrity_threshold,
                integrity_justification=args.integrity_justification,
                # #1349 vector arm: retriever routing + injected model + mode.
                retriever=args.retriever,
                model=args.model,
                query_prompt=args.query_prompt,
                retrieval_only=args.retrieval_only,
                db_uri=db_uri,
                # #1786 (R3/R1/R2/R5): the write-path retry budget + the
                # --retry-failed resume mode (default off) + the eval's
                # elevated HYBRID-arm retrieval deadline (1500 ms via the
                # _elevated_timeout_ms seam — the vector arm keeps
                # VECTOR_TIMEOUT_MS=5000). All four fingerprint keys stale
                # pre-feature checkpoints (CheckpointStaleError — the SAFE
                # direction; Task 8 requires a fresh checkpoint anyway).
                retry_failed=args.retry_failed,
                ingest_write_retries=INGEST_WRITE_RETRIES,
                ingest_question_retries=INGEST_QUESTION_RETRIES,
                resume_attempts_cap=RESUME_ATTEMPTS_CAP,
                retrieval_budget_ms=EVAL_RETRIEVAL_BUDGET_MS,
                # #1785 (Task 1 Step 4 / Task 3): revalidation mode + the
                # loss-location replay flags (CLI > env > default).
                revalidate=(args.revalidate
                            or ((os.environ.get(
                                "TORTOISE_LME_REVALIDATE") or "")
                                .strip().lower() in _TRUTHY)),
                per_session_census=args.per_session_census,
                replay_load_workers=args.replay_load_workers,
                replay_signature_reproduced=(
                    args.replay_signature_reproduced),
            )
        except FatalProviderError as e:
            print("[longmem_eval] RUN ABORTED — fatal provider error mid-run "
                  "(a 401/402/403 means the key died; continuing would silently "
                  "degrade the run):", file=sys.stderr)
            print(str(e), file=sys.stderr)
            raise SystemExit(1) from e
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
    finally:
        if extractor_model is not None:
            extractor_model.close()


if __name__ == "__main__":
    run_main()
