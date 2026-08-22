"""R6 — cross-encoder rerank + MMR diversity (issue #1545, epic #1509).

Post-fusion rerank stage for the LongMemEval retrieval path. Off by default:
``retrieve_for_question`` calls this ONLY when the R6 gate is on (see
retrieve.py), so the V3 baseline path is byte-identical. Production search
(``search_engine.py`` / ``sdk.tortoise_fts_query``) is intentionally
untouched — this is the eval-layer measured surface (S11).

MMR: MMR(d) = lambda_*rel(d) - (1-lambda_)*max_sim(d, selected), greedy, with a
hard per-session cap (E2E-10: one session can't monopolize the context).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Sequence

logger = logging.getLogger(__name__)

RERANK_MODEL_DEFAULT = "cross-encoder/ms-marco-MiniLM-L6-v2"
RERANK_MAX_LENGTH = 512          # tokenizer-level (CrossEncoder max_length)
RERANK_TRUNCATE_CHARS = 2048     # char pre-truncation (≈500 tokens) — the
                                 # two limits are aligned so long raw
                                 # transcripts cannot blow the tokenizer
_TRUTHY = {"1", "true", "yes", "on"}


def rerank_enabled(flag: bool | None) -> bool:
    """R6 gate. Explicit kwarg wins; else env TORTOISE_LME_RERANK (fail-safe
    OFF — only 1/true/yes/on enables)."""
    if flag is not None:
        return bool(flag)
    return os.environ.get("TORTOISE_LME_RERANK", "").strip().lower() in _TRUTHY


def _env_int(name: str, default: int) -> int:
    """Retrieve-layer env int with clamp (per-question safety net): garbage
    or out-of-range (< 1) values fall back to the default — never a crash.
    The run-level validation (run.py) re-validates the RAW env values
    independently; the two layers are deliberately separate."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        return default
    if value < 1:
        return default
    return value


def _env_float(name: str, default: float) -> float:
    """Retrieve-layer env float with clamp: garbage or out-of-range values
    fall back to the default; boundary values (0.0 / 1.0) accepted."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except ValueError:
        return default
    if not (0.0 <= value <= 1.0):
        return default
    return value


def mmr_select(
    scores: dict[int, float],
    sims: dict[tuple[int, int], float],
    sessions: dict[int, str],
    *,
    top_k: int,
    per_session_cap: int,
    lambda_: float,
) -> tuple[list[int], list[int]]:
    """Greedy MMR with a hard per-session cap.

    Returns (selected_indices, dropped_indices) in selection order. A
    candidate whose session already has ``per_session_cap`` selected hits is
    skipped; candidates ABSENT from ``sessions`` (or with an empty session
    id) are their own group and are never capped by other hits. Selection
    stops when the pool is exhausted or ``top_k`` is reached — the caller
    reports ``len(selected)`` (never implied ``top_k``).
    """
    if per_session_cap < 1:
        raise ValueError(f"per_session_cap must be >= 1, got {per_session_cap}")
    if not (0.0 <= lambda_ <= 1.0):
        raise ValueError(f"lambda_ must be in [0,1], got {lambda_}")
    cands = sorted(scores)          # deterministic tie-break by index
    selected: list[int] = []
    dropped: list[int] = []
    session_counts: dict[str, int] = {}
    for _ in range(min(top_k, len(cands))):
        best, best_val = None, float("-inf")
        for i in cands:
            if i in selected:
                continue
            sid = sessions.get(i)
            if sid and session_counts.get(sid, 0) >= per_session_cap:
                continue            # capped (empty/missing sid → never capped)
            rel = scores[i]
            sim = max((sims.get((i, j), sims.get((j, i), 0.0))
                       for j in selected), default=0.0)
            val = lambda_ * rel - (1.0 - lambda_) * sim
            if val > best_val:
                best, best_val = i, val
        if best is None:
            break
        selected.append(best)
        sid = sessions.get(best)
        if sid:
            session_counts[sid] = session_counts.get(sid, 0) + 1
    dropped = [i for i in cands if i not in selected]
    return selected, dropped


def _model_name() -> str:
    return (os.environ.get("TORTOISE_LME_RERANK_MODEL", RERANK_MODEL_DEFAULT)
            .strip() or RERANK_MODEL_DEFAULT)


class CrossEncoderScorer:
    """Lazy-loaded cross-encoder. Scores (query, content) pairs → sigmoid
    normalized (0,1). MS MARCO models return logits; the sigmoid is needed
    only to put rel on the same scale as the MMR similarity term (ranking is
    unchanged without it — pinned by the API-surface finding)."""

    def __init__(self, model: str, max_length: int = RERANK_MAX_LENGTH):
        import threading as _t

        from sentence_transformers import CrossEncoder
        self._model = CrossEncoder(model, max_length=max_length)
        self._lock = _t.Lock()   # serializes predict() under workers>1

    def score(self, query: str, contents: Sequence[str]) -> list[float]:
        # Per-instance lock: workers>1 (run.py --workers) share this instance
        # across threads — concurrent predict() is not documented thread-safe
        with self._lock:
            import torch
            # char pre-truncation lives in rerank_hits (both scorers
            # exercise it — D7); max_length is the tokenizer backstop
            pairs = [(query, c) for c in contents]
            logits = self._model.predict(pairs)     # v3+ API (score_pairs deprecated)
            return [float(torch.sigmoid(torch.tensor(x))) for x in logits]


class FakeScorer:
    """Deterministic test double: query-token-overlap over content length
    (no model). Normalizing by content length keeps near-duplicates strictly
    below the exact match (pure query-overlap would tie them)."""

    def score(self, query: str, contents: Sequence[str]) -> list[float]:
        qtoks = set(query.lower().split())
        out = []
        for c in contents:
            ctoks = set(c.lower().split())
            out.append(len(qtoks & ctoks) / max(len(ctoks), 1))
        return out


_scorer_lock = threading.Lock()
_scorer_cache: dict[str, CrossEncoderScorer] = {}   # successes — permanent
_fail_cache: dict[str, float] = {}                  # failures — short-TTL only
                                                    # (a persistent outage is
                                                    # retried at most ~1/min,
                                                    # not 500×/run — D8b)
_RETRY_TTL_S = 60.0
_NOW = time.monotonic


def get_scorer(model: str | None = None) -> tuple[CrossEncoderScorer | None, str]:
    """Load (cache) the cross-encoder; returns (scorer, reason). Successes are
    cached forever; failures are cached with a short TTL (``_RETRY_TTL_S``) so a
    persistent outage degrades quickly instead of hammering the HF hub.

    DOUBLE-CHECKED LOCKING: ``_scorer_lock`` is held ACROSS construction — a
    concurrent cache miss (``--workers > 1``) blocks until the first thread's
    constructor finishes, then returns the cached instance. Without this, two
    workers can double-download the ~90MB model and diverge on instances."""
    name = model or _model_name()
    with _scorer_lock:
        if name in _scorer_cache:
            return _scorer_cache[name], ""
        if name in _fail_cache and _NOW() - _fail_cache[name] < _RETRY_TTL_S:
            return None, f"{name}: load failed recently (retry in ~{_RETRY_TTL_S:.0f}s)"
        try:
            sc = CrossEncoderScorer(name)      # constructed INSIDE the lock
        except Exception as e:
            logger.warning("cross-encoder %s unavailable — R6 degrades to "
                           "rerank-off for this question: %s", name, e)
            _fail_cache[name] = _NOW()
            return None, f"{name}: {e!r}"
        _scorer_cache[name] = sc
        _fail_cache.pop(name, None)
        return sc, ""


def _fetch_embeddings(proj, ids: list[str]) -> dict[str, list[float]]:
    """One-query stored-embedding fetch for MMR similarity (D7).

    Values arrive as round-tripped float lists (follow the existing
    stored-embedding fetch pattern in sdk.py — the ``np.asarray(emb``
    round-trip; NO custom vecf32 decoder — the storage format is fragile per
    the ``vecf32(`` write comment in create_point). Missing / empty values
    are simply absent → per-pair Jaccard fallback (never a crash)."""
    if not ids or proj is None:
        return {}
    rows = proj.g.query(
        "MATCH (n:Point) WHERE n.id IN $ids AND n.embedding IS NOT NULL "
        "RETURN n.id, n.embedding",
        params={"ids": ids},
    ).result_set
    return {r[0]: r[1] for r in rows if isinstance(r[1], list) and r[1]}


def _token_set(text: str) -> set[str]:
    return set(text.lower().split())


def _pair_sim(text_a: str, text_b: str,
              emb_a: list[float] | None, emb_b: list[float] | None) -> float:
    """MMR similarity in [0,1], scale-consistent across the pool (D7):
    ``(1 + cos)/2`` over the point ``embedding`` property when BOTH sides
    have a finite, same-dimension embedding; per-pair Jaccard token overlap
    fallback otherwise (missing / NaN / Inf / dimension mismatch — embedder
    drift must fall back, never crash). A zero/zero token union must not
    raise (``max(len(a|b), 1)``)."""
    if emb_a is not None and emb_b is not None:
        try:
            import numpy as np
            va = np.asarray(emb_a, dtype=np.float64)
            vb = np.asarray(emb_b, dtype=np.float64)
            if (va.size and vb.size and va.shape == vb.shape
                    and bool(np.isfinite(va).all()) and bool(np.isfinite(vb).all())):
                denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
                if denom > 0.0:
                    cos = float(np.dot(va, vb) / denom)
                    return max(0.0, min(1.0, (1.0 + cos) / 2.0))
        except Exception:
            pass
    ta, tb = _token_set(text_a), _token_set(text_b)
    union = len(ta | tb)
    return (len(ta & tb) / union) if union else 0.0


def rerank_hits(
    query: str,
    hits: list[dict],
    *,
    scorer,
    proj,
    top_k: int,
    per_session_cap: int,
    lambda_: float,
) -> tuple[list[dict], dict]:
    """Cross-encoder rerank + greedy MMR over ``hits`` (the deduped pool).

    Returns ``(selected_only_list, stats)`` — the selected-only reordered
    list (length ≤ ``top_k``; the returned hits ARE the reader's context on
    the applied path) plus the per-question ``rerank_pass`` stats. A score
    exception OR a length-mismatched result degrades (``applied: False`` +
    ``degrade_reason``) — the caller truncates hits to ``top_k`` (D8c),
    never a per-question failure.

    (1) ``RERANK_TRUNCATE_CHARS`` char pre-truncation on every hit content
    via ``(content or "")[:...]`` — a ``None`` content truncates to ``""``
    (scored 0.0; NO degrade from None-content alone — only a scorer that
    rejects empty content raises, which the score-call guard catches). Both
    scorers exercise the truncation (D7). (2) per-session cap keyed on
    ``session_id`` (empty ids exempt — their own group). (3) ``mmr_select``.
    (4) ``reranked`` / ``mmr_promoted`` stamped on the selected hits.
    """
    contents = [(h.get("content") or "")[:RERANK_TRUNCATE_CHARS] for h in hits]
    try:
        scores = scorer.score(query, contents)
    except Exception as e:
        return list(hits), {"applied": False, "degrade_reason": f"{e!r}"}
    if not isinstance(scores, (list, tuple)) or len(scores) != len(contents):
        return list(hits), {
            "applied": False,
            "degrade_reason": "scorer returned length-mismatched scores "
                              f"({len(scores) if isinstance(scores, (list, tuple)) else '?'} "
                              f"vs {len(contents)})",
        }

    emb = _fetch_embeddings(proj, [h["id"] for h in hits])
    sims: dict[tuple[int, int], float] = {}
    for i in range(len(hits)):
        for j in range(i + 1, len(hits)):
            sims[(i, j)] = _pair_sim(
                contents[i], contents[j],
                emb.get(hits[i]["id"]), emb.get(hits[j]["id"]))

    sessions = {i: (hits[i].get("session_id") or "") for i in range(len(hits))}
    score_map = {i: float(scores[i]) for i in range(len(hits))}
    selected, dropped = mmr_select(
        score_map, sims, sessions, top_k=top_k,
        per_session_cap=per_session_cap, lambda_=lambda_)

    out: list[dict] = []
    moved = 0
    for rank, i in enumerate(selected):
        h = dict(hits[i])
        if rank != i:
            h["reranked"] = True
            moved += 1
        if rank < i:
            h["mmr_promoted"] = True
        out.append(h)

    session_counts: dict[str, int] = {}
    for i in selected:
        sid = hits[i].get("session_id") or ""
        if sid:                       # empty-session hits are singletons
            session_counts[sid] = session_counts.get(sid, 0) + 1
    stats = {
        "applied": True,
        "moved": moved,
        "dropped": len(dropped),
        "per_session_cap": per_session_cap,
        "lambda_": lambda_,
        "selected_count": len(selected),
        "max_session_chunks": max(session_counts.values(), default=0),
    }
    return out, stats
