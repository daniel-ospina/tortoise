"""Hybrid + vector retrieval per LongMemEval question (issues #1144, #1349).

Two retrievers, selected via ``retrieve_for_question(retriever=...)``:

  * ``hybrid`` (default, backward-compatible) — the repo's production hybrid
    search (``TortoiseSDK.tortoise_fts_query``: RRF fusion of FTS + vector +
    structural with the TF-IDF degradation fallback).
  * ``vector`` — the #1349 embedder-selection arm: encode the query with the
    injected model and run ``tortoise.search_engine.run_vector_query`` ONLY
    (never ``tortoise_fts_query`` — the gate metric is vector-only). Emits
    nDCG@10 (binary has_answer gains, log₂(i+2) discount, IDCG = all evidence
    turns first capped at 10, zero-evidence → 0.0) + P@10 (secondary) +
    P@5 (tertiary) alongside turn_recall@10.

The vector arm is HARD-FAIL by design:
  * a graph with zero embedding-bearing points raises
    :class:`ModelEncodeFailedError` (MODEL_ENCODE_FAILED — the runner aborts
    that config run with a distinct exit code; empty recall is
    indistinguishable from a legit no-hit and must never be reported as such),
  * an open vector circuit breaker raises :class:`VectorBreakerOpenError` —
    the runner marks the question ``breaker_open`` and routes it through the
    gate's dropped-question accounting (excluded from means, count surfaced),
  * ``run_vector_query`` is called with an ELEVATED 5000ms timeout (the
    default 500ms cap + breaker would trip on large haystacks and return []
    which reads as recall 0).

Reported per question (both arms):
  * session-level recall@k, turn-level recall@k,
  * context tokens, retrieval latency ms.
"""
from __future__ import annotations

import math
import time
from typing import Any

from tortoise import search_engine
from tortoise.embeddings import EmbeddingModel
from tortoise.sdk import TortoiseSDK

from . import encode_cache
from .ingest import point_props_for_hits

# token-count estimator: rough LLM token ≈ whitespace tokens, plus markup
# allowance for role prefixes/JSON. Documented in report provenance.
_TOKEN_ESTIMATOR = "whitespace-tokens + 10% markup allowance"


def _estimate_tokens(text: str) -> int:
    return int(len(text.split()) * 1.1)


#: Elevated vector-search timeout (ms). The search_engine default (500ms) plus
#: the per-strategy circuit breaker trips on large haystacks and returns [] —
#: indistinguishable from recall 0. The in-repo hard tier uses 5000ms.
VECTOR_TIMEOUT_MS = 5000

#: Distinct exit code for a MODEL_ENCODE_FAILED abort (encode-degrade on the
#: vector arm must abort the config run, never report empty recall).
MODEL_ENCODE_FAILED_EXIT = 4


class ModelEncodeFailedError(RuntimeError):
    """MODEL_ENCODE_FAILED — the per-question graph has zero embedding-bearing
    points (ingest-time encode degrade). Aborts the whole config run with
    :data:`MODEL_ENCODE_FAILED_EXIT` — never silently empty recall."""


class VectorBreakerOpenError(RuntimeError):
    """The vector circuit breaker is OPEN (or ``run_vector_query`` raised).

    Raised from :func:`vector_search` when the breaker is open BEFORE the
    call, when ``run_vector_query`` raised mid-call, or when the post-call
    check sees the breaker tripped/bumped during the call (``run_vector_query``
    SWALLOWS infra failures — on timeout/connection/query error it records a
    breaker failure and returns ``[]``, never raises; the empty result is
    indistinguishable from a legit no-hit). The runner marks the question
    ``breaker_open`` — excluded from the means, surfaced in the dropped-
    question accounting — never counted as recall 0."""


def dcg_at_k(gains: list[float], k: int) -> float:
    """Discounted cumulative gain over ``gains[:k]`` with log₂(i+2) discount."""
    return sum(g / math.log2(i + 2) for i, g in enumerate(gains[:k]))


def ndcg_at_k(ranked_ids: list[str], evidence_ids: set[str], k: int = 10) -> float:
    """Binary-gain nDCG@k over the ranked retrieved ids.

    gain = 1 for a retrieved turn that is a ``has_answer`` evidence turn else
    0; position discount log₂(i+2); IDCG = the ideal ranking with all evidence
    turns first, capped at k. Zero-evidence-turn questions → 0.0 (included in
    the mean — matches turn_recall@10's report default).
    """
    if not evidence_ids:
        return 0.0
    gains = [1.0 if pid in evidence_ids else 0.0 for pid in ranked_ids[:k]]
    idcg = dcg_at_k([1.0] * min(len(evidence_ids), k), k)
    if idcg == 0.0:
        return 0.0
    return dcg_at_k(gains, k) / idcg


def precision_at_k(ranked_ids: list[str], evidence_ids: set[str], k: int) -> float:
    """Precision@k: fraction of the top-k retrieved ids that are evidence
    turns (0.0 when there are no evidence turns)."""
    if k <= 0:
        return 0.0
    top = ranked_ids[:k]
    if not top:
        return 0.0
    return len(set(top) & evidence_ids) / k


def _count_embedded_points(proj) -> int:
    """n(embedding IS NOT NULL) over the per-question graph's Points."""
    rows = proj.g.query(
        "MATCH (n:Point) WHERE n.embedding IS NOT NULL RETURN count(n)"
    ).result_set
    if not rows:
        return 0
    try:
        return int(rows[0][0])
    except (TypeError, ValueError, IndexError):
        return 0


def _encode_query_vec(query: str) -> list[float]:
    """Encode the query via the injected model (EmbeddingModel singleton).

    ``tools.embedder_probe.inject_model`` replaces the sentence-transformers
    symbol BEFORE the first ``EmbeddingModel.get()``, so the singleton IS the
    candidate model. Routed through the disk-persisted encode cache when one
    is active (model-keyed — no cross-model contamination)."""
    model = EmbeddingModel.get()
    if model is None:
        raise ModelEncodeFailedError(
            "query encoding failed: EmbeddingModel.get() returned None "
            "(degraded to TF-IDF) — refusing to run the vector arm")
    return encode_cache.encode_query(model, query)


def vector_search(sdk: TortoiseSDK, query: str, limit: int) -> list[tuple[str, float]]:
    """Vector-only retrieval over the question's ingested graph.

    Returns raw ``(id, score)`` tuples from ``search_engine.run_vector_query``
    — the HNSW ``queryNodes`` branch in ``--db``/server mode, brute-force
    euclideanDistance in embedded mode. NEVER calls ``tortoise_fts_query``.

    Raises:
        ModelEncodeFailedError: the graph has zero embedding-bearing points
            (MODEL_ENCODE_FAILED — aborts the config run).
        VectorBreakerOpenError: the vector circuit breaker is open or
            ``run_vector_query`` raised.
    """
    proj = sdk._get_proj()
    # Search-time degrade detection: a graph with zero embedding-bearing
    # points would return empty recall indistinguishable from a legit no-hit.
    if _count_embedded_points(proj) == 0:
        raise ModelEncodeFailedError(
            f"MODEL_ENCODE_FAILED: graph has 0 embedding-bearing Points — the "
            f"injected embedder degraded at ingest; aborting the vector arm "
            f"(refusing to report empty recall as a result)")
    if search_engine._breaker("vector").is_open():
        raise VectorBreakerOpenError(
            "vector circuit breaker is OPEN — question dropped from the "
            "vector arm (surfaced via dropped-question accounting)")
    qvec = _encode_query_vec(query)
    # run_vector_query SWALLOWS infra failures: on timeout/connection/query
    # error it records a breaker failure (``_breaker_record("vector", False)``)
    # and returns [] — never raises. An empty result is indistinguishable from
    # a legit no-hit, so snapshot the failure counter before the call and
    # re-check after: a bumped counter (or a breaker that tripped mid-call,
    # e.g. a concurrent strategy's failure) means the query FAILED and must
    # route through breaker-open accounting, never recall 0.
    breaker = search_engine._breaker("vector")
    fails_before = breaker._fails
    try:
        res = search_engine.run_vector_query(
            proj.g, qvec, limit,
            is_embedded=getattr(proj, "_is_embedded", True),
            vector_index_api=getattr(proj, "_vector_index_api", None),
            timeout_ms=VECTOR_TIMEOUT_MS,
        )
    except VectorBreakerOpenError:
        raise
    except Exception as e:  # noqa: BLE001 — breaker-open routing (never recall 0)
        raise VectorBreakerOpenError(
            f"run_vector_query raised for the question: {e!r}") from e
    if breaker._fails > fails_before or breaker.is_open():
        # The tripping call itself is caught here (Q3 in the failure series),
        # not just the Q4 pre-check — the swallowed [] never reaches the
        # report as recall 0 / nDCG 0.
        raise VectorBreakerOpenError(
            "vector query failed/breaker tripped during the call — question "
            "dropped from the vector arm (surfaced via dropped-question "
            "accounting)")
    return res


def render_context(hits: list[dict], *, question_date: str | None = None) -> str:
    """Render annotated hits as the reader-facing context text.

    Shared by the LLM reader (its prompt input) and the token estimator so
    ``context_tokens`` always matches what the reader actually consumed.

    The rendering follows the OFFICIAL LongMemEval gen.py shape: a
    ``Current Date: {question_date}`` header (the question's date, needed to
    answer temporal-reasoning questions — "how many days ago") and a
    per-session date annotation on every chunk. Without these, TR questions
    are structurally unanswerable (TR ≈ 0% regardless of retrieval) — P1
    #1144.
    """
    blocks = []
    for h in hits:
        idx = h.get("lme_session_index")
        prefix = f"[session {idx}]" if idx is not None and idx >= 0 else "[session ?]"
        sdate = h.get("session_date")
        if sdate:
            prefix = f"{prefix} (session date {sdate})"
        blocks.append(f"{prefix} {h.get('content', '')}")
    text = "\n\n".join(blocks)
    if question_date:
        text = f"Current Date: {question_date}\n\n{text}"
    return text


def hybrid_search(sdk: TortoiseSDK, query: str, limit: int) -> list[dict]:
    """Hybrid retrieval over the question's ingested graph (points only).

    Returns raw hit dicts from ``tortoise_fts_query`` (id, content,
    match_source, scores…) — embedded mode degrades to the in-memory TF-IDF
    fallback automatically.
    """
    return sdk.tortoise_fts_query(query, entity_type="point", limit=limit)


def retrieve_for_question(
    sdk: TortoiseSDK,
    question: dict,
    *,
    ks: tuple[int, ...] = (5, 10, 20),
    top_k: int = 20,
    retriever: str = "hybrid",
) -> dict[str, Any]:
    """Run retrieval for one question and compute recall@k + context stats.

    ``top_k`` is the context size handed to the reader (default 20 — the
    design-locked depth; recall is reported at every k in ``ks``).

    ``retriever`` ∈ {"hybrid", "vector"}: hybrid is the legacy RRF path;
    vector is the #1349 gate arm (run_vector_query ONLY, nDCG@10 + P@10 +
    P@5 + ranked ids + evidence-turn matches in the outcome).
    """
    if retriever not in ("hybrid", "vector"):
        raise ValueError(f"retriever must be 'hybrid' or 'vector', got {retriever!r}")
    qid = question["question_id"]
    answer_sessions = set(question.get("answer_session_ids") or [])
    dates: list[str] = question.get("haystack_dates") or []
    evidence_turn_ids = {
        f"lme:{qid}:s{si}:t{ti}"
        for si, session in enumerate(question.get("haystack_sessions") or [])
        for ti, turn in enumerate(session)
        if turn.get("has_answer")
    }

    start = time.monotonic()
    if retriever == "vector":
        raw_hits = vector_search(sdk, question["question"], limit=max(ks))
        hits: list[dict] = [
            {"id": pid, "score": score, "match_source": "vector"}
            for pid, score in raw_hits
        ]
    else:
        hits = hybrid_search(sdk, question["question"], limit=max(ks))
    latency_ms = (time.monotonic() - start) * 1000.0

    props = point_props_for_hits(sdk._get_proj(), [h["id"] for h in hits])

    # Annotate hits with session/has_answer (SearchResult carries sessionId
    # only when the engine populates it; fetch is single-query and canonical).
    # session_date comes from the dataset's haystack_dates (surfaced to the
    # reader so temporal questions are answerable — P1 #1144).
    annotated: list[dict] = []
    for h in hits:
        p = props.get(h["id"], {})
        si = p.get("lme_session_index", -1)
        annotated.append({
            "id": h["id"],
            "content": p.get("content") or h.get("content", ""),
            "match_source": h.get("match_source", ""),
            "session_id": p.get("session_id", ""),
            "lme_session_index": si,
            "session_date": dates[si] if 0 <= si < len(dates) else "",
            "has_answer": p.get("has_answer", False),
        })

    # ── recall@k (session-level + turn-level) ──
    session_recall: dict[str, float] = {}
    turn_recall: dict[str, float] = {}
    for k in ks:
        top = annotated[:k]
        if answer_sessions:
            retrieved_sessions = {h["session_id"] for h in top if h["session_id"]}
            session_recall[str(k)] = (
                len(answer_sessions & retrieved_sessions) / len(answer_sessions))
        else:
            session_recall[str(k)] = 0.0
        if evidence_turn_ids:
            top_ids = {h["id"] for h in top}
            turn_recall[str(k)] = len(evidence_turn_ids & top_ids) / len(evidence_turn_ids)
        else:
            turn_recall[str(k)] = 0.0

    # ── context handed to the reader (top_k) ──
    context_points = annotated[:top_k]
    # The reader consumes the SAME rendered context (with the Current Date
    # header) — keep context_tokens aligned with what the reader saw.
    question_date = question.get("question_date", "") or None
    context_text = render_context(context_points, question_date=question_date)
    context_tokens = _estimate_tokens(context_text) if context_text else 0

    out: dict[str, Any] = {
        "question_id": qid,
        "retriever": retriever,
        "hits": annotated,
        "session_recall@k": session_recall,
        "turn_recall@k": turn_recall,
        "context_tokens": context_tokens,
        "context_point_count": len(context_points),
        "retrieval_latency_ms": round(latency_ms, 2),
    }
    if retriever == "vector":
        ranked_ids = [h["id"] for h in annotated]
        out["ranked_ids"] = ranked_ids
        out["evidence_turn_matches"] = sorted(evidence_turn_ids & set(ranked_ids))
        # nDCG@10 (primary), P@10 (secondary), P@5 (tertiary) — computed
        # alongside turn_recall@10 in the per-question outcome.
        out["ndcg@10"] = round(ndcg_at_k(ranked_ids, evidence_turn_ids, k=10), 6)
        out["p@10"] = round(precision_at_k(ranked_ids, evidence_turn_ids, k=10), 6)
        out["p@5"] = round(precision_at_k(ranked_ids, evidence_turn_ids, k=5), 6)
    return out
