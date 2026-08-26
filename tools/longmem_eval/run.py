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
import time
import warnings
from datetime import UTC, datetime, timezone
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
from .rerank import RERANK_MODEL_DEFAULT, rerank_enabled
from .retrieve import (
    DEFAULT_CONTEXT_TOKEN_CAP,
    DEFAULT_MAX_CHUNKS_PER_SESSION,
    DEFAULT_TR_TOP_K,
    MODEL_ENCODE_FAILED_EXIT,
    ModelEncodeFailedError,
    VectorBreakerOpenError,
    retrieve_for_question,
)

DEFAULT_KS = (5, 10, 20)
DEFAULT_TOP_K = 20
DEFAULT_MAX_RETRIES = 3
BACKOFF_BASE_S = 2.0
BACKOFF_CAP_S = 30.0

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
# literal contract: DeepSeekDirectModel('deepseek-chat') → 'deepseek-chat').
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
    ``deepseek/deepseek-chat``: the direct lane strips to the non-reasoning
    ``deepseek-chat``, the OpenRouter lane keeps the prefixed id — bare ids
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
    (#1549 ``_direct_wire_id`` — ``deepseek/deepseek-v4-flash`` →
    ``deepseek-chat`` on the direct lane) can REWRITE the pin's wire id
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
        f"serves {sorted(set(served_ids))!r} instead (e.g. #1549 "
        f"_direct_wire_id: deepseek/deepseek-v4-flash → deepseek-chat on "
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
                       rerank_config: dict) -> dict:
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
    MUTABILITY: wire ids are API-facing and mutable (e.g. #1706 renamed
    deepseek-v4-flash → deepseek-chat) — a rename loudly invalidates every
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
    arm); tracked with the retrieve-leg issue (#1745).
    """
    tr = outcome.get("turn_recall@k")
    return isinstance(tr, dict) and bool(tr) and all(
        v is None for v in tr.values())


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
        "reason", "count"}) shows the FTS leg dead — every fts entry has
        ``count == 0`` (empty_results / index_missing / breaker_open — all
        dead legs; TR questions trace one entry per entity type, so a live
        point leg rescues a legitimately-empty event leg) AND recall data
        does not positively show the session surfaced: the dead-FTS signal
        fires only when ``session_recall@k`` is all-zero or
        absent/unrecorded. A healthy (non-zero) vector-rescued session
        (FTS empty but recall > 0) is NOT retrieval-dead — rejecting it
        would livelock, since the re-encode reproduces the same FTS-empty
        shape on the next resume;
      - every ``session_recall@k`` value is 0.0 (the session never
        surfaced at any depth).

    Signals fire ONLY on positive recorded evidence: an outcome with no
    ``legs`` trace (pre-R3 checkpoint, vector arm) or no fts entry is NOT
    refused — absent data ≠ dead leg (embedded-mode checkpoints whose
    vector leg records no_embedder legitimately still pass via their
    healthy fts leg). Legitimately session-less outcomes (abstention
    questions — ``turn_recall@k`` all None per the M6 N/A-not-0.0
    contract) are exempt: their all-zero session recall and legitimately
    empty FTS are the question's shape, not a dead backend. breaker_open
    outcomes are excluded by the caller (kept — a legitimately dropped
    question must never re-run).
    """
    if not isinstance(outcome, dict):
        return None
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
                               and not isinstance(v, bool) and v > 0
                               for v in sr.values()))
    session_zero = (isinstance(sr, dict) and bool(sr)
                    and all(v == 0 for v in sr.values()))
    if isinstance(legs, list):
        # TR questions trace one fts entry PER entity type (point + event
        # share the leg_trace) — the leg is dead only when EVERY fts entry
        # has count 0 (a live point leg rescues a legitimately-empty event
        # leg).
        fts_entries = [leg for leg in legs
                       if isinstance(leg, dict) and leg.get("leg") == "fts"]
        # The dead-FTS signal fires only when the session did NOT
        # positively surface: a healthy vector leg that rescued the session
        # (session_recall > 0) is NOT retrieval-dead — rejecting it would
        # livelock, since the re-encode reproduces the same FTS-empty shape
        # on the next resume.
        if (fts_entries and all(leg.get("count") == 0
                                for leg in fts_entries)
                and not session_healthy):
            return "fts.count=0 (dead FTS retrieval leg)"
    if session_zero:
        return "session_recall@k all zeros (session never surfaced)"
    return None


def _load_checkpoint(path: str | None,
                     expected_fingerprint: dict | None = None,
                     *, run_key: str | None = None,
                     retriever: str = "hybrid"
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
    """
    if not path:
        return {}, []
    p = Path(path)
    if not p.is_file():
        return {}, []
    try:
        with flock_exclusive(p.with_suffix(p.suffix + ".lock")):
            data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        print(f"[longmem_eval] WARNING: checkpoint {p} is corrupt ({e!r}) — "
              f"ignoring; every question re-encodes", file=sys.stderr)
        return {}, []
    fmt = data.get("format")
    saved_key = data.get("run_key")
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
    required = REQUIRED_OUTCOME_KEYS.get(retriever, ("question_id",))
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
    print(f"[longmem_eval] resumed checkpoint {p}: {len(outcomes)} completed, "
          f"{len(failures)} failed (skipping both)"
          + (f"; {gate_rejected} rejected by the resume-quality gate "
             f"(re-encoding)" if gate_rejected else ""), file=sys.stderr)
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
                     failures: list[dict], fingerprint: dict | None = None, *,
                     run_key: str | None = None, surface: str | None = None,
                     retriever: str | None = None, model: str | None = None,
                     prompt: str | None = None) -> None:
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
            "format": CHECKPOINT_FORMAT,
            "run_key": run_key,
            "surface": surface,
            "retriever": retriever,
            "model": model or "default",
            "prompt": prompt or "default",
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
    )
    done, prior_failures = _load_checkpoint(checkpoint, fingerprint,
                                            run_key=run_key,
                                            retriever=retriever)
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
            sdk, cleanup = _make_question_sdk(
                db_uri=db_uri,
                namespace=question_graph_namespace(model, query_prompt, qid)
                if db_uri else None,
                work_dir=work_dir)
            try:
                _sdk_cleanup = cleanup
                # M7 (D5): ingest is timed in isolation — the write-path
                # cost is a report component (extractor vs retrieve vs
                # reader vs judge attribution).
                t_ingest = time.monotonic()
                if ingest_mode == "v2":
                    from tests.model_adapters import build_extractor_model

                    from .ingest_v2 import ingest_haystack_v2
                    ingest_stats = ingest_haystack_v2(
                        sdk, question, extractor_model,
                        chunk_turns=chunk_turns,
                        # Pilot #1549: session-parallel extraction within a
                        # question (the LLM phase is the wall-clock dominant
                        # cost). NOTE: the live ingest_haystack_v2 on main
                        # shadows the parallel worker-factory path with a
                        # sequential copy (pre-existing duplicate, tracked
                        # separately — #1744), so workers currently fall
                        # back to the shared extractor_model — which is
                        # exactly what the fingerprint records.
                        session_workers=session_workers,
                        # M7 #1739 / #1742: the factory spec + tuning are
                        # threaded in (never the run_main-local ``args``
                        # closure — that was a latent NameError) and mirror
                        # exactly what _build_cli_extractor_model
                        # fingerprints: the workers serve the SAME config
                        # the checkpoint records.
                        model_factory=(
                            (lambda: build_extractor_model(
                                session_worker_model_spec or None,
                                max_tokens=session_worker_max_tokens,
                                temperature=session_worker_temperature))
                            if session_workers > 1 else None))
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
                    mmr_lambda=rr["mmr_lambda"])

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
                # R6 (#1545): the rerank pass + latency ride the outcome —
                # they stay ABSENT on baseline outcomes (the projection in
                # outcomes_to_report adds them conditionally).
                **({"rerank_pass": ret["rerank_pass"],
                    "rerank_latency_ms": ret.get("rerank_latency_ms", 0.0)}
                   if "rerank_pass" in ret else {}),
                # #1349 vector arm: gate metrics + breaker-open dropped marker.
                **({"ranked_ids": ret["ranked_ids"],
                    "evidence_turn_matches": ret["evidence_turn_matches"],
                    "ndcg@10": ret["ndcg@10"],
                    "p@10": ret["p@10"],
                    "p@5": ret["p@5"]}
                   if "ndcg@10" in ret else {}),
            }
            with _lock:
                outcomes.append(outcome)
                done[qid] = outcome
        except VectorBreakerOpenError:
            # #1349: vector-arm breaker drops are NOT failures — the question
            # is marked breaker_open and excluded from the means (count
            # surfaced in report["dropped"]). Never recall 0.
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
        except ModelEncodeFailedError:
            # #1349: the graph has ZERO embedding-bearing points — empty
            # recall is indistinguishable from a legit no-hit. ABORT the
            # whole config run (never report empty recall as a result); the
            # runner exits MODEL_ENCODE_FAILED_EXIT.
            raise
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
            _save_checkpoint(
                checkpoint, list(done.values()), failures, fingerprint,
                run_key=run_key, surface=surface, retriever=retriever,
                model=model, prompt=query_prompt)

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
        # #1349 vector arm: retriever/model/query_prompt/mode/run_key.
        retriever=retriever, model=model, query_prompt=query_prompt,
        retrieval_only=retrieval_only, surface=surface, run_key=run_key,
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
                # #1349 vector arm: the gate's per-question metrics ride the
                # Layer-1 projection (extract_report in gate_1349.py reads
                # them from the report's outcomes) + the breaker-open dropped
                # markers (dropped-question accounting, never recall 0).
                "ndcg@10", "p@10", "p@5", "ranked_ids",
                "evidence_turn_matches", "breaker_open", "dropped_reason",
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
                  "tr_window_fallback": bool(o.get("tr_window_fallback", False)),
                  # R6 (#1545): the rerank pass + latency are added to the
                  # selector ONLY when the outcome carries them (a baseline
                  # outcome must NEVER project rerank_pass: null; stale
                  # pre-R6 checkpoint outcomes are skipped by the same
                  # condition and read via .get() so they can't KeyError).
                  **({"rerank_pass": o["rerank_pass"],
                      "rerank_latency_ms": o.get("rerank_latency_ms", 0.0)}
                     if o.get("rerank_pass") is not None else {})}
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
                        "default 8000 — points first, chunks backfill, R1 #1540)")
    p.add_argument("--max-chunks-per-session", type=_positive_int, default=None,
                   help="per-session raw-chunk cap in the retrieval pool "
                        "(env TORTOISE_LME_MAX_CHUNKS_PER_SESSION; default 2 — "
                        "E2E-1 session-dedup, R1 #1540)")
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
                        "server). 8-16 on a quiet machine")
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
