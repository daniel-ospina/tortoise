"""R6 — cross-encoder rerank + MMR diversity (issue #1545, epic #1509).

Post-fusion rerank stage for the LongMemEval retrieval path. Off by default:
``retrieve_for_question`` calls this ONLY when the R6 gate is on (see
retrieve.py), so the V3 baseline path is byte-identical.

MMR: MMR(d) = lambda_*rel(d) - (1-lambda_)*max_sim(d, selected), greedy, with a
hard per-session cap (E2E-10: one session can't monopolize the context).
"""
# ═════════════════════════════════════════════════════════════════════════
# ══ HARNESS PURPOSE — READ THIS FIRST ════════════════════════════════════
# tools/longmem_eval/ is a THIN MEASUREMENT LAYER over the product
# (tortoise/): the eval calls the product's OWN engine and measures it.
# Quality improvements belong IN tortoise/ (that is what ships to
# customers).
# ═════════════════════════════════════════════════════════════════════════
# ══ ONE IMPLEMENTATION (issue #2976) ═════════════════════════════════════
# The verified R6 lever was promoted to the product: the scoring logic
# (``CrossEncoderScorer`` / ``FakeScorer`` / ``mmr_select`` / ``_pair_sim``
# / ``_fetch_embeddings`` / ``rerank_hits``) and the lazy-load cache policy
# (``load_scorer``) now live ONCE in ``tortoise/rerank.py`` and are imported
# + re-exported here. This module keeps only the eval-lane adapters: the
# ``TORTOISE_LME_RERANK*`` env namespace, the gate, and the eval's own
# module-level cache globals (the test seam). The product ask lane uses the
# same code behind ``TORTOISE_ASK_RERANK``.
# ═════════════════════════════════════════════════════════════════════════
from __future__ import annotations

import logging
import math
import os
import threading
import time

# The ONE implementation (#2976) — imported from the product, re-exported so
# the eval lane (and its tests) keep the same module surface.
from tortoise.rerank import (  # noqa: F401
    _TRUTHY,
    RERANK_MAX_LENGTH,
    RERANK_MODEL_DEFAULT,
    RERANK_TRUNCATE_CHARS,
    CrossEncoderScorer,
    FakeScorer,
    _env_float,
    _env_int,
    _fetch_embeddings,
    _pair_sim,
    _token_set,
    load_scorer,
    mmr_select,
    rerank_hits,
)

logger = logging.getLogger(__name__)


def rerank_enabled(flag: bool | None) -> bool:
    """R6 gate. Explicit kwarg wins; else env TORTOISE_LME_RERANK (fail-safe
    OFF — only 1/true/yes/on enables)."""
    if flag is not None:
        return bool(flag)
    return os.environ.get("TORTOISE_LME_RERANK", "").strip().lower() in _TRUTHY


def _env_boost_float(name: str, default: float) -> float:
    """C2 (#1745) retrieve-layer env float for the evidence-mark boost
    multipliers: domain [1.0, inf) — a boost factor scales ranks UP, so
    values < 1.0 (including 0.0 — a ZeroDivisionError, and negatives — a
    silent pool inversion) are rejected and fall back to the default.
    Non-finite values (NaN/Inf — a NaN passes the < 1.0 comparison and
    would poison every sort key) are rejected the same way (review F9).
    This is deliberately NOT ``_env_float`` (whose [0, 1] MMR-lambda clamp
    would silently discard the 1.5/1.15 defaults)."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except ValueError:
        return default
    if not (math.isfinite(value) and value >= 1.0):
        return default
    return value


def _model_name() -> str:
    return (os.environ.get("TORTOISE_LME_RERANK_MODEL", RERANK_MODEL_DEFAULT)
            .strip() or RERANK_MODEL_DEFAULT)


_scorer_lock = threading.Lock()
_scorer_cache: dict[str, CrossEncoderScorer] = {}   # successes — permanent
_fail_cache: dict[str, float] = {}                  # failures — short-TTL only
                                                    # (a persistent outage is
                                                    # retried at most ~1/min,
                                                    # not 500×/run — D8b)
_RETRY_TTL_S = 60.0
_NOW = time.monotonic


def get_scorer(model: str | None = None) -> tuple[CrossEncoderScorer | None, str]:
    """Eval-lane scorer loader: the eval's own env namespace (``_model_name``)
    + module-level cache globals (the test seam), delegating the ONE load +
    TTL cache policy to ``tortoise.rerank.load_scorer``.

    ``cls`` / ``now`` are passed as the module globals so the eval's test
    seam (monkeypatch ``CrossEncoderScorer`` / ``_NOW``) still intercepts
    construction exactly as before the #2976 de-fork."""
    return load_scorer(
        model or _model_name(),
        cls=CrossEncoderScorer,
        lock=_scorer_lock,
        cache=_scorer_cache,
        fail_cache=_fail_cache,
        now=_NOW,
        retry_ttl_s=_RETRY_TTL_S,
    )
