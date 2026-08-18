"""Hybrid retrieval per LongMemEval question (issue #1144, axis 2).

Routes each question through the repo's production hybrid search
(``TortoiseSDK.tortoise_fts_query`` — RRF fusion of FTS + vector + structural
with the TF-IDF degradation fallback, so embedded/CI runs degrade gracefully
and Docker/HNSW runs use the full stack). The retrieved pool covers BOTH the
epistemic turn points and the raw verbatim session transcripts — the
"graph + raw sessions" hybrid the design-locked axis-2 protocol calls for.

Reported per question:
  * session-level recall@k  — fraction of ``answer_session_ids`` (evidence
    sessions) that appear among the top-k retrieved points' sessions,
  * turn-level recall@k      — fraction of evidence turns (``has_answer``)
    whose turn point appears in the top-k,
  * context tokens           — estimated LLM tokens of the top-k context
    handed to the reader (whitespace tokens + 10% markup allowance; the
    estimator is recorded in report provenance),
  * retrieval latency ms.
"""
from __future__ import annotations

import time
from typing import Any

from tortoise.sdk import TortoiseSDK

from .ingest import point_props_for_hits

# token-count estimator: rough LLM token ≈ whitespace tokens, plus markup
# allowance for role prefixes/JSON. Documented in report provenance.
_TOKEN_ESTIMATOR = "whitespace-tokens + 10% markup allowance"


def _estimate_tokens(text: str) -> int:
    return int(len(text.split()) * 1.1)


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
) -> dict[str, Any]:
    """Run retrieval for one question and compute recall@k + context stats.

    ``top_k`` is the context size handed to the reader (default 20 — the
    design-locked depth; recall is reported at every k in ``ks``).
    """
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
            "content": h["content"],
            "match_source": h.get("match_source", ""),
            "session_id": p.get("session_id", ""),
            "lme_session_index": si,
            "session_date": dates[si] if 0 <= si < len(dates) else "",
            "has_answer": p.get("has_answer", False),
        })

    # ── recall@k (session-level + turn-level) ──
    session_recall: dict[str, float] = {}
    turn_recall: dict[str, float] = {}
    # #1369: v2-mode evidence — has_answer-marked extracted points (the

    # extractor's recall contribution). The deterministic leg's evidence
    # turns carry turn ids; the v2 leg marks the extracted points instead,
    # so turn recall is computed over the marks when present.
    ev_rows = sdk._get_proj().g.query(
        "MATCH (p:Point) WHERE p.lme_question_id = $q AND p.has_answer = true "
        "RETURN count(*)", params={"q": qid}).result_set
    evidence_point_count = ev_rows[0][0] if ev_rows else 0
    for k in ks:
        top = annotated[:k]
        if answer_sessions:
            retrieved_sessions = {h["session_id"] for h in top if h["session_id"]}
            session_recall[str(k)] = (
                len(answer_sessions & retrieved_sessions) / len(answer_sessions))
        else:
            session_recall[str(k)] = 0.0
        if evidence_point_count:
            # v2 leg: did the extracted point CONTAINING the answer surface?
            ev_hits = {h["id"] for h in top if h["has_answer"]}
            turn_recall[str(k)] = len(ev_hits) / evidence_point_count
        elif evidence_turn_ids:
            # deterministic leg: did the evidence TURN surface?
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

    return {
        "question_id": qid,
        "hits": annotated,
        "session_recall@k": session_recall,
        "turn_recall@k": turn_recall,
        "context_tokens": context_tokens,
        "context_point_count": len(context_points),
        "retrieval_latency_ms": round(latency_ms, 2),
    }
