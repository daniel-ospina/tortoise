"""Embedding-based cross-lens candidate generation for Tortoise (#399).

Recall-only: finds point pairs from DIFFERENT lenses that may describe the
same concept. This module NEVER writes to the graph and never decides
operator semantics — it produces candidates for a verifier (the extractor's
cue-word gate today; the LLM relation verifier in #6306).

Lens derivation (when lens_key is None): point["lens"] → point["source"] →
point["provenance"]["source_id"] → point["speaker"] → "unknown".

Threshold calibration (all-MiniLM-L6-v2, measured 2026-08-07):
  near-duplicate paraphrases ....... 0.90+   (NEAR_DUPLICATE_THRESHOLD = 0.75)
  cross-vocabulary paraphrase band . 0.35-0.51  ← DEFAULT_THRESHOLD = 0.40
  motivating pair (#399) ............ 0.29 (boundary: topically similar, NOT
                                         logically implied — verification's job)
  unrelated / noise floor ........... <= 0.15

#6306 contract: find_cross_lens_matches(points) over folded document points
({pid: {"content", "lens"}} or provenance.source_id); candidates are INPUT to
the LLM relation verifier — never operators by themselves.
"""
from __future__ import annotations

import logging
from typing import Callable  # noqa: UP035

import numpy as np

logger = logging.getLogger(__name__)

NEAR_DUPLICATE_THRESHOLD = 0.75
DEFAULT_THRESHOLD = 0.40


def _lens_of(point: dict, lens_key: str | None) -> str:
    if lens_key is not None:
        v = point.get(lens_key)
        # Truthiness, not is-not-None: an empty string is not a lens identity
        # (code-review P3, #399) — it must not collapse distinct points into
        # one "empty" lens.
        return str(v) if v else "unknown"
    for key in ("lens", "source"):
        v = point.get(key)
        if v:
            return str(v)
    prov = point.get("provenance")
    if isinstance(prov, dict) and prov.get("source_id") is not None:
        return str(prov["source_id"])
    sp = point.get("speaker")
    return str(sp) if sp is not None else "unknown"


def find_cross_lens_matches(
    points: dict[str, dict],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    lens_key: str | None = None,
    encode: Callable[[list[str]], np.ndarray] | None = None,
) -> list[dict]:
    """Recall-only cross-lens candidate generation (#399).

    Args:
        points: point_id → {"content": str, ...}; content is the only
            required key. Lens identity resolved via _lens_of.
        threshold: cosine similarity cutoff (default 0.40, calibrated).
        lens_key: explicit field to use as the lens; None → derivation chain.
        encode: injected encoder (tests); None → shared tortoise.embeddings
            _encode (real model; deterministic TF-IDF degraded fallback).

    Returns:
        Candidates sorted by similarity descending:
        [{"src", "dst", "similarity", "lenses": [l1,l2], "speakers": [...],
          "degraded": bool}] — same-lens pairs excluded. Never writes to the
        graph and never returns operator semantics.
    """
    ids = list(points)
    if not ids:
        return []  # never call the encoder on empty input (fake encoders raise)
    texts = [points[i]["content"] for i in ids]
    lenses = [_lens_of(points[i], lens_key) for i in ids]
    speakers = [points[i].get("speaker", "unknown") for i in ids]

    degraded = False
    if encode is not None:
        vectors = np.asarray(encode(texts), dtype=np.float64)
        if vectors.shape[0] != len(texts):
            # Code-review P4 (#399): an encoder returning the wrong row count
            # silently masked an IndexError behind the extractor's blanket
            # except — surface the contract violation instead.
            raise ValueError(
                f"encoder returned {vectors.shape[0]} rows for {len(texts)} texts"
            )
    else:
        # Lazy import (#399 D9): keeps test_mock_extractor_multi_source_fallback
        # (patched builtins.__import__ raising on "embeddings") able to trip the
        # extractor's fallback even when tortoise.cross_lens is already cached
        # in sys.modules.
        from tortoise.embeddings import _encode  # noqa: PLC0415, RUF100
        vectors, degraded = _encode(texts)

    from tortoise.embeddings import cosine_similarity_matrix  # noqa: PLC0415, RUF100
    sim = cosine_similarity_matrix(vectors)
    if degraded:
        logger.info("cross-lens matching degraded to TF-IDF (%d points)", len(ids))

    candidates = []
    n = len(ids)
    for i in range(n):
        for j in range(i + 1, n):
            if lenses[i] == lenses[j]:
                continue
            if sim[i, j] >= threshold:
                candidates.append({
                    "src": ids[i], "dst": ids[j],
                    "similarity": float(sim[i, j]),
                    "lenses": [lenses[i], lenses[j]],
                    "speakers": [speakers[i], speakers[j]],
                    "degraded": degraded,
                })
    candidates.sort(key=lambda c: c["similarity"], reverse=True)
    return candidates
