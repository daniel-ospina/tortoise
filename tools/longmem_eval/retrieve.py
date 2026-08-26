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
  * turn-level recall@k      — fraction of evidence-MARKED points
    (``has_answer``) among the top-k; the numerator counts extracted
    points only (``pointKind <> 'session-transcript'``, D5 #1540) so raw
    chunks can never inflate turn recall against the points-only
    denominator; when the graph has no marks but the dataset has evidence
    turns, falls back to the deterministic evidence-turn ids (honest
    attribution per leg),
  * evidence recall@k        — the extractor's recall contribution: marked
    extracted points surfaced / marked extracted points total (same
    marked-set accounting as turn-level when marks exist; ``None`` when
    the graph has zero marks). N/A semantics (M6, #1526): ``None`` when
    the denominator is EMPTY — "no evidence exists" stays distinguishable
    from "evidence exists but never surfaces" (#1369),
  * chunk evidence recall@k  — the M6 raw-chunk containment view (R1
    #1540): containment-marked raw chunks surfaced / marked chunks total;
    granularity-aware by construction,
  * context tokens           — estimated LLM tokens of the budget-capped
    rank-interleaved context handed to the reader (whitespace tokens + 10%
    markup allowance; the estimator is recorded in report provenance),
  * retrieval latency ms.

R1 #1540 (epic #1509): candidates are fetched at ``max(ks) * 3`` depth
(pool-depth headroom so a monopolizing session's points cannot crowd other
sessions out BEFORE dedup runs), the pool is deduped per-session
(``max_chunks_per_session`` raw chunks per session, rank order — E2E-1).

#1745 (epic #1509): ``_assemble_context`` builds the budget-capped,
RANK-INTERLEAVED context (C1 — replaces R1's points-first UX decision 3,
which starved raw chunks from the reader: all points preceded all chunks
regardless of RRF rank, so any pool with >= top_k points dropped the
chunk leg that retains ~2x the evidence). Pool items render in true RRF
rank order (points and chunks interleaved) bounded by the token budget
AND the ``context_item_cap`` (default 40, knob
``TORTOISE_LME_CONTEXT_ITEMS``); TR questions keep the pinned
``tr_top_k``=12 item cap (R5 flood control). Recall@k is computed over
the DEDUPED pool (``ret["hits"]`` == the pool — the retrieval contract);
since C1, the pool is an APPROXIMATE upper bound on what the reader
could actually see (the budget walk's skip-not-starve lets a lower-ranked
marked item enter the k-prefix, so ``reader_evidence@k`` can exceed it) —
``reader_evidence@k`` (C4) is the independent reader-surface measure.

C2 (#1745): the evidence-mark boost (``_apply_evidence_boost``) re-ranks
marked hits up by a stable rank offset BEFORE ``_recall_metrics`` so the
pool-based ``evidence_recall@k`` honestly measures the boosted pool;
marks are recomputed at read time (``evidence.mark_for``) so verbatim/
raw-chunk marks get the full boost while source-session-only marks get a
reduced one. OFF by default in code (env ``TORTOISE_LME_EVIDENCE_BOOST``
or the explicit ``evidence_boost`` flag enables it) — the plan's default
decision: ON only for the re-validation run.
"""
from __future__ import annotations

import math
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from tortoise import search_engine
from tortoise.embeddings import EmbeddingModel
from tortoise.sdk import TortoiseSDK

from . import encode_cache
from .ingest import EXTRACTION_POINT_KIND, event_props_for_hits, point_props_for_hits

# ── #1349 vector arm: exceptions, nDCG/P metrics, vector_search ────────────
#: The runner aborts a config run with this exit code when the graph has zero
#: embedding-bearing points (empty recall is indistinguishable from a legit
#: no-hit — never reported as a result).
MODEL_ENCODE_FAILED_EXIT = 4


class ModelEncodeFailedError(RuntimeError):
    """The graph has zero embedding-bearing points (MODEL_ENCODE_FAILED).

    Raised by :func:`vector_search` at search time — an empty recall is
    indistinguishable from a legit no-hit and must never be reported as
    such; the runner aborts that config run with a distinct exit code.
    """


class VectorBreakerOpenError(RuntimeError):
    """The vector circuit breaker is OPEN (or ``run_vector_query`` raised).

    Caught by the runner's dropped-question accounting: excluded from the
    means, count surfaced (``dropped.breaker_open``).
    """


def dcg_at_k(gains: list[float], k: int) -> float:
    """Discounted cumulative gain over ``gains[:k]`` with log2(i+2) discount."""
    return sum(g / math.log2(i + 2) for i, g in enumerate(gains[:k]))


def ndcg_at_k(ranked_ids: list[str], evidence_ids: set[str], k: int = 10) -> float:
    """Binary-gain nDCG@k over the ranked retrieved ids.

    gain = 1 for a retrieved turn that is a ``has_answer`` evidence turn else
    0; position discount log2(i+2); IDCG = the ideal ranking with all evidence
    turns first, capped at k. Zero-evidence-turn questions -> 0.0 (included in
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


#: Elevated vector-search timeout (ms). The search_engine default (500ms) plus
#: the per-strategy circuit breaker trips on large haystacks and returns [] —
#: indistinguishable from recall 0. The in-repo hard tier uses 5000ms.
VECTOR_TIMEOUT_MS = 5000


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
    if _count_embedded_points(proj) == 0:
        raise ModelEncodeFailedError(
            "MODEL_ENCODE_FAILED: graph has 0 embedding-bearing Points — the "
            "injected embedder degraded at ingest; aborting the vector arm "
            "(refusing to report empty recall as a result)")
    if search_engine._breaker("vector").is_open():
        raise VectorBreakerOpenError(
            "vector circuit breaker is OPEN — question dropped from the "
            "vector arm (surfaced via dropped-question accounting)")
    qvec = _encode_query_vec(query)
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
    except Exception as e:
        raise VectorBreakerOpenError(
            f"run_vector_query raised for the question: {e!r}") from e
    if breaker._fails > fails_before or breaker.is_open():
        raise VectorBreakerOpenError(
            "vector query failed/breaker tripped during the call — question "
            "dropped from the vector arm (surfaced via dropped-question "
            "accounting)")
    return res


# token-count estimator: rough LLM token ≈ whitespace tokens, plus markup
# allowance for role prefixes/JSON. Documented in report provenance.
_TOKEN_ESTIMATOR = "whitespace-tokens + 10% markup allowance"

#: R1 (#1540): per-session raw-chunk cap in the pool (E2E-1; the R6 MMR
#: variant tunes it post-baseline). C5 (#1745): 2 -> 3 — the R1 cap was
#: capping out the evidence chunk on ~18 questions (chunk_evidence_recall@20
#: = 0 with evidence_recall@20 high); only 4/854 S-split sessions have >2
#: marked chunks, so the budget cost is bounded.
DEFAULT_MAX_CHUNKS_PER_SESSION = 3
#: C1 (#1745): reader-context ITEM cap (default 40, knob
#: ``TORTOISE_LME_CONTEXT_ITEMS``) — the measured ~114 tok/item means a
#: 60-item pool (~6.8k tokens) may not bind the 8k token budget, so an
#: unceilinged budget walk would flood the reader; the item cap bounds
#: reader flood while the token budget selects within it (top-k saturation
#: research, plan §3). TR questions ignore it — ``tr_top_k`` stays the
#: pinned TR item cap (R5 #1544 flood control).
DEFAULT_CONTEXT_ITEM_CAP = 40
#: C2 (#1745): evidence-mark boost rank-offset multipliers — verbatim/
#: raw-chunk marks (the precise ones) get the full boost, source-session-
#: only points a reduced one (marks never influence RRF ranking — H2).
#: Knobs: ``TORTOISE_LME_EVIDENCE_BOOST_VERBATIM`` /
#: ``TORTOISE_LME_EVIDENCE_BOOST_SOURCE``.
DEFAULT_EVIDENCE_BOOST_VERBATIM = 1.5
DEFAULT_EVIDENCE_BOOST_SOURCE = 1.15
#: R1 (#1540): reader context token budget (≈ the pre-v2 baseline context
#: size — a 4.4x reduction from the measured 35k whole-session flood;
#: LightMem: compact evidence wins under tight budgets).
DEFAULT_CONTEXT_TOKEN_CAP = 8000
#: R1 (#1540): candidate-depth headroom — one session's points must not
#: crowd other sessions out BEFORE dedup runs.
DEFAULT_POOL_MULTIPLIER = 3

#: R5 (#1544): TR top_k cap (20→12) — the transcript-flood control for
#: temporal-reasoning questions (9/18 TR losses were reader refusals under
#: ~40k-token floods despite sr@5=1.0). Knob-exposed via ``--tr-top-k``.
DEFAULT_TR_TOP_K = 12


# ── R5 (#1544): TR-constraint detection ─────────────────────────────────────

@dataclass(frozen=True)
class TimeConstraint:
    """A detected temporal shape in a TR question (D5).

    ``kind``: "interval" | "recency" | "ordering" | None
    ``start``: ISO date (interval) | day count (recency) | None
    ``end``: ISO date (interval) | None
    """
    kind: str | None
    start: str | None = None
    end: str | None = None


#: recency window unit map (D5): day=1, week=7, month=30.
_UNIT_DAYS = {"day": 1, "week": 7, "month": 30}


def detect_time_constraint(text: str, *,
                           default_year: int | None = None) -> TimeConstraint:
    """Detect the temporal shape the issue names ("between…and…", "ago",
    "how many days") in a TR question's text (D5).

    | kind      | trigger                                                       | behavior |
    |-----------|---------------------------------------------------------------|----------|
    | interval  | "between <Month day> and <Month day>" (year from              | hard filter on session_date ∈ [start, end] |
    |           |  ``default_year``) or ISO "YYYY-MM-DD" bounds                | |
    | recency   | "N days/weeks/months ago", "last N …" (unit map              | hard filter on [qdate − N_days, qdate] |
    |           |  day=1/week=7/month=30)                                      | |
    | ordering  | "how many days", "how long", "when did", bare "ago" with     | no filter — the question needs the full |
    |           |  no numeric bound                                             | dated set to compute a span/ordering |
    | None      | no match                                                     | no filter, no reorder (pure date weight) |

    Degradation rule: an unparseable bound (bare "ago", "how many days"
    with no number) degrades to ``ordering`` — never a hard filter that
    could starve the evidence out of the window. ``default_year`` anchors
    month-day intervals without a year (the caller passes the question's
    year; fallback 2026).
    """
    t = " ".join(text.lower().split())
    # recency bound: "N days/weeks/months ago" / "last N …" — the only
    # shapes that give a computable window without the answer.
    m = re.search(
        r"(?:\b(\d+)\s+(days?|weeks?|months?)\s+ago\b"
        r"|\blast\s+(\d+)\s+(days?|weeks?|months?)\b)", t)
    if m:
        n = int(m.group(1) or m.group(3))
        unit = (m.group(2) or m.group(4)).rstrip("s")
        return TimeConstraint("recency", start=str(n * _UNIT_DAYS[unit]))
    # ordering shapes: the question needs the FULL dated set — "how many
    # days ago" with no numeric bound, "how long", "when did", bare
    # "ago" with no bound (D5: no hard filter, no false bounds).
    if (re.search(r"\bago\b", t)
            or re.search(r"\bhow\s+(many\s+)?days\b", t)
            or re.search(r"\bhow\s+long\b", t)
            or re.search(r"\bwhen\s+did\b", t)):
        return TimeConstraint("ordering")
    # interval: "between <Month day> and <Month day>" (year from
    # ``default_year``) or ISO "YYYY-MM-DD" bounds — explicit window.
    m = re.search(
        r"between\s+(\d{4}-\d{2}-\d{2})\s+and\s+(\d{4}-\d{2}-\d{2})", t)
    if m:
        return TimeConstraint("interval", start=m.group(1), end=m.group(2))
    m = re.search(
        r"between\s+([a-z]+)\s+(\d{1,2})\s+and\s+([a-z]+)\s+(\d{1,2})", t)
    if m:
        def _iso(mon: str, day: str) -> str:
            d = datetime.strptime(f"{mon} {day} {default_year or 2026}",
                                  "%B %d %Y")
            return d.date().isoformat()
        return TimeConstraint("interval", start=_iso(m.group(1), m.group(2)),
                              end=_iso(m.group(3), m.group(4)))
    return TimeConstraint(None)


def _apply_time_window(annotated: list[dict], constraint: TimeConstraint,
                       *, question_date: str | None) -> list[dict]:
    """R5 (D5): hard time-window filter on the annotated hits' session_date.

    * interval — keep hits whose ``session_date ∈ [start, end]`` (inclusive;
      ISO dates compare lexicographically).
    * recency — keep hits whose ``session_date ∈ [qdate − N_days, qdate]``
      (N from ``constraint.start``; missing/unparseable qdate → the filter
      cannot compute bounds → returns [] so the caller's defensive fallback
      (``tr_window_fallback``) keeps the unfiltered pool).

    Undated hits (no session_date) never satisfy a window — the filter
    returns only in-window DATED hits. The caller falls back when this
    returns empty (never starve the reader into abstention).
    """
    if not annotated:
        return []
    if constraint.kind == "interval":
        start, end = constraint.start, constraint.end
        if not start or not end:
            return []
        return [h for h in annotated
                if h.get("session_date") and start <= h["session_date"] <= end]
    if constraint.kind == "recency":
        try:
            n_days = int(constraint.start or 0)
            qdate = (datetime.strptime(str(question_date or "")[:10],
                                       "%Y-%m-%d").date()
                     if question_date else None)
        except (TypeError, ValueError):
            return []
        if qdate is None:
            return []  # recency math needs the question date — fall back
        from datetime import timedelta
        lo = (qdate - timedelta(days=n_days)).isoformat()
        hi = qdate.isoformat()
        return [h for h in annotated
                if h.get("session_date") and lo <= h["session_date"] <= hi]
    return list(annotated)  # ordering/None → no filter


def _estimate_tokens(text: str) -> int:
    return int(len(text.split()) * 1.1)


_ROLE_PREFIX = re.compile(r"^\[(user|assistant|system|tool|unknown)\]\s+",
                             re.IGNORECASE)


def _speaker_for_turns(proj, turn_ids: list[str]) -> dict[str, str]:
    """One-query speaker lookup for source-turn links (E3 D7). Returns
    {turn_node_id: speaker} for the turns that exist."""
    ids = [t for t in turn_ids if t]
    if not ids:
        return {}
    rows = proj.g.query(
        "MATCH (n:Point) WHERE n.id IN $ids "
        "RETURN n.id, coalesce(n.speaker, '')", params={"ids": ids}).result_set
    return {r[0]: r[1] for r in rows}


def _annotate_hits(hits: list[dict], props: dict, dates: list[str]) -> list[dict]:
    """Annotate raw search hits with session linkage + promoted state.

    Extracted from ``retrieve_for_question`` (#1367) so the passthrough of
    the search payload's D8 supersession fields (``superseded_by`` /
    ``supersedes`` — #1353) is unit-testable. Additive keys: a hit carries
    them when the graph has CORRECTS edges — the full retrieval path
    (Docker/HNSW) via SearchResult, and the embedded TF-IDF fallback via the
    call-site decoration (E5 #1537, fetch_point_epistemic_state batch at
    tortoise_fts_query). Without CORRECTS state they stay None/[] and
    ``render_context`` renders byte-identically to today.

    E3 (#1535): the hit's ``speaker`` is derived at read time — its own
    speaker prop (turn points carry it) or, when the source-turn node was
    fetched in the same batch, that turn's speaker. Passes ``quote`` /
    ``search_keys`` / ``source_turn_id`` through for R2's future
    query-expansion consumer.
    """
    annotated: list[dict] = []
    for h in hits:
        p = props.get(h["id"], {})
        si = p.get("lme_session_index", -1)
        turn_ref = p.get("source_turn_id") or ""
        spk = (p.get("speaker") or ""
               or (props.get(turn_ref, {}).get("speaker") or ""
                   if turn_ref else ""))
        annotated.append({
            "id": h["id"],
            "content": h["content"],
            # M7 (#1527, D2): the leg that produced this hit is never empty —
            # a missing match_source serializes to "unknown", not "" (E2E-1
            # "never null" is asserted here, at the hit level).
            "match_source": h.get("match_source") or "unknown",
            "session_id": p.get("session_id", ""),
            "lme_session_index": si,
            "session_date": dates[si] if 0 <= si < len(dates) else "",
            "has_answer": p.get("has_answer", False),
            # E3: quote/search_keys pass through (R2 consumer); source_turn_id
            # + speaker are the derivation surface the reader decoration uses.
            "quote": p.get("quote", ""),
            "search_keys": p.get("search_keys") or [],
            "source_turn_id": turn_ref,
            "speaker": spk,
            # R1 (#1540): pointKind lets retrieval distinguish raw chunks
            # (session-transcript) from extracted points — per-session
            # chunk dedup + the D5 evidence-denominator split key on it.
            "point_kind": p.get("point_kind", ""),
            # #1367: promoted supersession state — pass the search payload's
            # D8 fields through (superseded_by = newest incoming CORRECTS
            # claim; supersedes = outgoing CORRECTS claims). Reused, not
            # re-detected.
            "superseded_by": h.get("superseded_by"),
            "supersedes": h.get("supersedes") or [],
            # E6 (#1538) D7: promoted validity-window fields — additive,
            # only when present (undated hits render no [valid …] marker).
            "valid_from": h.get("valid_from") or "",
            "valid_to": h.get("valid_to") or "",
            "expired_at": h.get("expired_at") or "",
        })
    return annotated


def _is_raw_chunk(h: dict) -> bool:
    """True for a raw verbatim chunk (pointKind ``session-transcript``).
    Points of every other kind (extracted statements, episodic turn points)
    are the compact epistemic surface (D3 #1540: never chunk-capped)."""
    return h.get("point_kind") == "session-transcript"


def _dedup_pool(annotated: list[dict], *,
                max_chunks_per_session: int) -> list[dict]:
    """Per-session chunk cap (rank order): at most ``max_chunks_per_session``
    raw chunks per session survive in the pool (E2E-1 #1540). Bucket key =
    the hit's session_id when present, else its lme_session_index —
    distinct sessions NEVER share a bucket (no ``-1`` collapse).
    Points/turn points are never capped (compact epistemic surface, D3).
    """
    if max_chunks_per_session < 1:
        raise ValueError("max_chunks_per_session must be >= 1, got "
                         f"{max_chunks_per_session!r}")
    seen: dict[str, int] = {}
    pool: list[dict] = []
    for h in annotated:
        if _is_raw_chunk(h):
            key = h.get("session_id") or f"idx:{h.get('lme_session_index', -1)}"
            if seen.get(key, 0) >= max_chunks_per_session:
                continue
            seen[key] = seen.get(key, 0) + 1
        pool.append(h)
    return pool


def _apply_evidence_boost(
    pool: list[dict],
    *,
    question: dict | None = None,
    evidence_sessions: set[str] | None = None,
    answer_turn_contents: list[str] | None = None,
    boost_verbatim: float = DEFAULT_EVIDENCE_BOOST_VERBATIM,
    boost_source: float = DEFAULT_EVIDENCE_BOOST_SOURCE,
) -> tuple[list[dict], dict[str, Any]]:
    """C2 (#1745): evidence-mark rank boost over the deduped pool.

    Marked hits (H2: marks never influence RRF ranking — the fused score is
    content-similarity-only) move up by a stable rank offset so they can
    surface into the pool top-20. The boost is a RANK re-scaling, NOT an
    RRF-score multiplier (verifier P1-6: annotated hits drop
    ``scores.rrf`` — there is no score to multiply): ``scaled_rank =
    original_index / factor`` with factor 1.0 unmarked, ``boost_source``
    for source-session-only marks, ``boost_verbatim`` for verbatim /
    raw-chunk marks. Placement is position-ceiling promotion (Horn's
    greedy, review F2): hits are processed in descending scaled-priority
    order and each takes the LARGEST free position <= its ceiling, where
    the ceiling is the ORIGINAL pool index for marked hits and
    unconstrained for unmarked hits. Properties:
      * position-ceiling — never demotes a marked hit below its original
        pool index (a dense run of higher-factor marked hits cannot push
        a lower-factor marked hit out of the top-k it occupied pre-boost),
      * never reorders within a boost class (same factor -> monotonic)
        and never reorders unmarked hits (relative order preserved),
      * bounded — no negative ranks, every hit lands in [0, n).

    The verbatim-vs-source split is recomputed at READ TIME via
    ``evidence.mark_for`` (verifier P1-4: the OR'd ``has_answer`` prop
    cannot express the split) — the annotated hits already carry
    ``content``/``quote``/``session_id`` and the question carries
    ``haystack_sessions``, so no graph change is needed. A stored
    ``has_answer`` hit whose read-time recompute finds no mark is treated
    as source-class (conservative — never a full boost on ambiguous
    provenance).

    Placement contract: the caller applies this BEFORE ``_recall_metrics``
    so post-fix ``evidence_recall@k`` is honestly "evidence recall over the
    boosted pool" (stated in the methodology); the pre-boost ranking rides
    back in ``stats["pre_boost_ranked_ids"]`` for the C4 ablation.

    Returns ``(boosted_pool, stats)``; ``stats`` carries the per-class
    multiplier, the read-time mark census and the pre-boost id order.
    """
    from . import evidence as ev
    # factor domain guard (review P1-2 + F9): a boost factor < 1.0 is a rank
    # DIVISION — 0.0 is a ZeroDivisionError and a negative factor silently
    # inverts the pool order. Non-finite values (NaN/Inf) are rejected the
    # same way (a NaN passes the < 1.0 comparison and would poison every
    # sort key; inf would zero every key and make the boost a silent
    # no-op). Reject loudly at the function boundary (the
    # env/CLI layers clamp >= 1.0 independently; this is the last line).
    if not (math.isfinite(boost_verbatim) and boost_verbatim >= 1.0) \
            or not (math.isfinite(boost_source) and boost_source >= 1.0):
        raise ValueError(
            "evidence-boost multipliers must be >= 1.0 and finite (a "
            "rank-scaling division), got verbatim="
            f"{boost_verbatim!r} source={boost_source!r}")
    if evidence_sessions is None:
        evidence_sessions = (ev.evidence_sessions(question)
                             if question else set())
    if answer_turn_contents is None:
        answer_turn_contents = [
            (t.get("content") or "")
            for s in ((question or {}).get("haystack_sessions") or [])
            for t in s if t.get("has_answer")
        ]
    census = {"source_session": 0, "verbatim": 0, "raw_chunk": 0}
    scored: list[tuple[dict, float, int]] = []
    marked_by_idx: dict[int, bool] = {}
    for i, h in enumerate(pool):
        marks = ev.mark_for(
            h, session_id=h.get("session_id"),
            evidence_sessions=evidence_sessions,
            answer_turn_contents=answer_turn_contents)["marks"]
        for mk in census:
            if marks.get(mk):
                census[mk] += 1
        if marks.get("verbatim") or marks.get("raw_chunk"):
            factor = boost_verbatim
        elif marks.get("source_session") or h.get("has_answer"):
            factor = boost_source
        else:
            factor = 1.0
        scored.append((h, i / factor, i))
        marked_by_idx[i] = factor > 1.0
    # Position-ceiling promotion (review F2): the plain ascending sort let a
    # dense run of higher-factor marked hits (verbatim chunks, x1.5) pass a
    # lower-factor marked hit (source point, x1.15) and DEMOTE it below its
    # original pool index — the reproduced counter-example moved the point
    # 19 -> 22, out of top-20 (evidence_recall@20 dropped 1 -> 0 with the
    # boost ON). Horn's greedy: process hits in DESCENDING scaled-priority
    # order (for unmarked hits the scaled key IS the pool index, so they
    # run in descending index order) and assign each hit the LARGEST free
    # position <= its ceiling — original index for marked hits,
    # unconstrained for unmarked. Properties (verified by brute force):
    #   (a) marked hits never land below their original index;
    #   (b) unmarked relative order preserved (descending processing +
    #       largest-free assignment = strictly decreasing slots);
    #   (c) order within a boost class preserved (same argument).
    n = len(pool)
    free = list(range(n))
    placement: dict[int, tuple[dict, float, int]] = {}
    for h, key, i in sorted(
            scored, key=lambda x: (-x[1], -x[2])):
        ceiling = i if marked_by_idx[i] else n  # +inf ~ n: always satisfiable
        pos = max(p for p in free if p <= ceiling)
        free.remove(pos)
        placement[pos] = (h, key, i)
    scored = [placement[p] for p in range(n)]
    stats: dict[str, Any] = {
        "applied": True,
        "boost_verbatim": boost_verbatim,
        "boost_source": boost_source,
        "marks_census": census,
        "pre_boost_ranked_ids": [h["id"] for h in pool],
        "moved": sum(1 for _, _, i in scored
                      if _rank_delta(scored, i)),
    }
    return [h for h, _, _ in scored], stats


def _rank_delta(scored: list[tuple[dict, float, int]], orig_index: int) -> bool:
    """True when the hit with original pool index ``orig_index`` moved to a
    strictly earlier position after the boost (the ``moved`` counter)."""
    new_pos = next(pos for pos, (_, _, i) in enumerate(scored)
                   if i == orig_index)
    return new_pos < orig_index


def _recall_metrics(
    hits: list[dict],
    *,
    ks: tuple[int, ...],
    answer_sessions: set[str],
    evidence_turn_ids: set[str],
    evidence_point_count: int,
    chunk_evidence_point_count: int,
) -> tuple[dict[str, float], dict[str, float | None],
           dict[str, float | None], dict[str, float | None]]:
    """Recall@k over a hit list (session + turn + evidence + chunk-evidence).

    Extracted from ``retrieve_for_question`` (R6 #1545) — identical math,
    reused for the rerank pool-recall diagnostic. ``evidence_point_count`` /
    ``chunk_evidence_point_count`` are the D5 denominators (computed once,
    before the loop — no hoisting exists or is needed).
    """
    session_recall: dict[str, float] = {}
    turn_recall: dict[str, float | None] = {}
    _evidence_recall: dict[str, float | None] = {}
    chunk_evidence_recall: dict[str, float | None] = {}
    for k in ks:
        top = hits[:k]
        if answer_sessions:
            retrieved_sessions = {h["session_id"] for h in top if h["session_id"]}
            session_recall[str(k)] = (
                len(answer_sessions & retrieved_sessions) / len(answer_sessions))
        else:
            session_recall[str(k)] = 0.0
        # turn/evidence numerators exclude raw chunks (D5) — otherwise
        # marked chunks in top-k inflate turn_recall beyond 1.0 against the
        # points-only denominator.
        ev_hits = {h["id"] for h in top
                   if h["has_answer"] and not _is_raw_chunk(h)}
        if evidence_point_count:
            # v2 leg: did the extracted point CONTAINING the answer surface?
            turn_recall[str(k)] = len(ev_hits) / evidence_point_count
            _evidence_recall[str(k)] = len(ev_hits) / evidence_point_count
        else:
            # M6 (#1526) N/A-not-0.0: an empty denominator is None, never a
            # forced 0.0 — "no evidence exists" stays distinguishable from
            # "evidence exists but never surfaces" (#1369).
            _evidence_recall[str(k)] = None
            if evidence_turn_ids:
                # deterministic leg: did the evidence TURN surface?
                top_ids = {h["id"] for h in top}
                turn_recall[str(k)] = (
                    len(evidence_turn_ids & top_ids) / len(evidence_turn_ids))
            else:
                turn_recall[str(k)] = None
        # chunk containment view (M6): marked raw chunks surfaced / marked
        # raw chunks total — granularity-aware by construction.
        if chunk_evidence_point_count:
            chunk_hits = {h["id"] for h in top
                          if h["has_answer"] and _is_raw_chunk(h)}
            chunk_evidence_recall[str(k)] = (
                len(chunk_hits) / chunk_evidence_point_count)
        else:
            chunk_evidence_recall[str(k)] = None
    return session_recall, turn_recall, _evidence_recall, chunk_evidence_recall


def _leg_mix(hits: list[dict]) -> dict[str, int]:
    """Counter of ``match_source`` over a hit list (M7 #1527, D2).

    Legs are never re-derived — the engine's own ``match_source``
    (fts/vector/structural/rrf/tfidf) lands on annotated hits (missing →
    ``unknown``); embedded mode legitimately shows ``{"tfidf": n}``, real
    mode ``{"rrf": n}`` (+ per-leg when the engine emits it).
    """
    counts: dict[str, int] = {}
    for h in hits:
        leg = h.get("match_source") or "unknown"
        counts[leg] = counts.get(leg, 0) + 1
    return dict(sorted(counts.items()))


def _validity_marker(h: dict) -> str:
    """Validity-window marker text for one hit (E6 #1538, D7).

    Extends the #1367 supersession markers with the promoted window fields:
      - live hit with ``valid_from`` → ``[valid since <from>]``
      - superseded hit → ``[valid <from> → <to>]``; with ``expired_at`` →
        ``[valid <from> → <to>; expired <tx-date>]``
      - undated hits → NO validity marker (byte-identical rendering)
    ISO date strings (YYYY-MM-DD — the dataset/``when`` normalization): no
    full timestamps in the reader context; timestamps stay on the graph
    properties. The supersession markers (SUPERSEDED BY / SUPERSEDES) are
    unchanged and render first."""
    marks: list[str] = []
    sb = h.get("superseded_by") or {}
    snippet = (sb.get("content_snippet") or "").strip()
    if snippet:
        marks.append(f"[SUPERSEDED BY: {snippet}]")
    supersedes = h.get("supersedes") or []
    snips = [(s.get("content_snippet") or "").strip()
             for s in supersedes if (s.get("content_snippet") or "").strip()]
    if snips:
        marks.append("[SUPERSEDES: " + " ; ".join(snips) + "]")
    vf = (h.get("valid_from") or "").strip()
    vt = (h.get("valid_to") or "").strip()
    ex = (h.get("expired_at") or "").strip()
    if vf:
        # ISO date strings only — truncate full timestamps to YYYY-MM-DD.
        vfd = vf[:10] if len(vf) > 10 else vf
        if vt:
            vtd = vt[:10] if len(vt) > 10 else vt
            if ex:
                exd = ex[:10] if len(ex) > 10 else ex
                marks.append(f"[valid {vfd} → {vtd}; expired {exd}]")
            else:
                marks.append(f"[valid {vfd} → {vtd}]")
        else:
            marks.append(f"[valid since {vfd}]")
    return " ".join(marks)


def _render_block(h: dict) -> str:
    """One hit's rendered context block — the SINGLE implementation shared
    by ``render_context`` and the token budget (factored out of
    ``render_context``, R1 #1540). ``question_date`` never appears here: it
    only prepends the ``Current Date:`` header once in ``render_context``.
    Per-hit dates come from the hit's own ``session_date``."""
    idx = h.get("lme_session_index")
    prefix = f"[session {idx}]" if idx is not None and idx >= 0 else "[session ?]"
    sdate = h.get("session_date")
    if sdate:
        prefix = f"{prefix} (session date {sdate})"
    # E3 (#1535): speaker decoration — mirrors the deterministic leg's
    # "[role] text" turn shape so the reader sees who asserted the fact.
    # Unknown → byte-identical rendering (backward-compat). Skip when the
    # content ALREADY carries a role bracket (turn points are written as
    # "[role] text" AND have the speaker prop — decorating both would
    # double-attribute, e.g. "[user] [user] ..." on the deterministic leg's
    # primary recall surface).
    spk = h.get("speaker") or ""
    # only the deterministic leg's own role-bracket shape suppresses the
    # decoration — a non-role bracket prefix ([context], [IMPORTANT])
    # must not suppress speaker attribution
    if spk and not _ROLE_PREFIX.match(h.get("content", "")):
        prefix = f"{prefix} [{spk}]"
    marker = _validity_marker(h)
    if marker:
        # _validity_marker already returns self-bracketed groups
        # (e.g. "[SUPERSEDED BY: x] [valid 2026-06-10 → 2026-06-12]") — no
        # extra wrap.
        prefix = f"{prefix} {marker}"
    return f"{prefix} {h.get('content', '')}"


def _assemble_context(pool: list[dict], *, top_k: int,
                      max_context_tokens: int,
                      question_date: str | None = None,
                      context_item_cap: int | None = None) -> list[dict]:
    """Budget-capped, rank-interleaved reader context (C1 #1745).

    Iterates the pool in TRUE RRF rank order — extracted points and raw
    chunks interleaved, a chunk ranked above a point enters the context at
    its rank, not after all points (replaces R1's points-first partition,
    UX decision 3, which starved the chunk leg: any pool with >= top_k
    points dropped chunks entirely regardless of rank). Bounded by BOTH
    the token budget (``max_context_tokens``) and an explicit item cap
    (``context_item_cap``; defaults to ``top_k`` for back-compat with
    pure-function callers — the run path passes the resolved
    ``context_item_cap`` / TR ``tr_top_k``). ``top_k`` stays "the max
    number of context items" at the default cap.

    Token accounting (the alignment invariant): raw whitespace words
    accumulate per block (question_date-independent) + the once-prepended
    ``Current Date: …`` header words; the 1.1 markup multiplier applies
    ONCE to the joined total, so ``context_tokens ==
    _estimate_tokens(render_context(...))`` holds exactly (no per-block
    ``int()`` drift). Oversized hits are SKIPPED (continue), never starving
    the rest of the context.
    """
    if max_context_tokens < 1:
        raise ValueError("max_context_tokens must be >= 1, got "
                         f"{max_context_tokens!r}")
    item_bound = context_item_cap if context_item_cap is not None else top_k
    if item_bound < 1:
        raise ValueError("context_item_cap must be >= 1, got "
                         f"{item_bound!r}")
    header_words = (len(f"Current Date: {question_date}".split())
                    if question_date else 0)
    selected: list[dict] = []
    words = header_words
    for h in pool:
        if len(selected) >= item_bound:
            break
        cost = len(_render_block(h).split())
        if int((words + cost) * 1.1) > max_context_tokens:
            continue  # skip this hit; keep later ones (no starvation)
        selected.append(h)
        words += cost
    return selected


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

    #1367: hits carrying the promoted supersession state (superseded_by /
    supersedes — #1353 D8) are annotated so the reader sees "this statement
    replaced that one": a superseded hit is marked ``[SUPERSEDED BY:
    <newest superseding claim>]`` and a superseding hit ``[SUPERSEDES:
    <replaced claims>]`` (the superseding claim's content is included via
    its snippet; when the superseding point is itself in the hits its full
    content renders too). Hits without the state render byte-identically.

    R1 #1540: per-hit rendering is the shared ``_render_block`` (the token
    budget uses the identical accounting), so ``context_tokens`` always
    matches what the reader consumed. Output is byte-identical to pre-R1
    for non-chunk hits.
    """
    text = "\n\n".join(_render_block(h) for h in hits)
    if question_date:
        text = f"Current Date: {question_date}\n\n{text}"
    return text


def hybrid_search(sdk: TortoiseSDK, query: str, limit: int,
                   *, entity_types: tuple[str, ...] = ("point",),
                   recency_fields: dict[str, str] | None = None,
                   recency_boost: float = 0.0,
                   leg_trace: list[dict] | None = None) -> list[dict]:
    """Hybrid retrieval over the question's ingested graph.

    R5 (#1544) D4: ``entity_types`` selects the retrieval pool — TR
    questions pass ``("point", "event")`` so the dated events timeline
    joins the pool (E2E-4's "no point-only filter"); the default
    ``("point",)`` keeps every non-TR path byte-identical (baseline
    isolation, M8). Per entity type the hits come from
    ``tortoise_fts_query`` (RRF fusion of FTS + vector + structural with
    the TF-IDF degradation fallback); the results merge by
    ``scores.rrf`` desc (comparable across the calls — same k constant,
    same leg structure) with a deterministic id tiebreak. Event hits use
    ``eventId`` as ``id`` — the Point/Event id namespaces never collide
    (different label, different id field).

    ``recency_fields`` (R5 D2): {entity_type: Cypher property name} — the
    date source for the engine's recency re-rank (points ``createdAt``,
    events ``startedAt``). ``recency_boost`` is the multiplier strength
    (0.0 off). Both default off → byte-identical to pre-R5.

    ``leg_trace`` (R3 #1542 D4): a list the search records per-leg entries
    into (vector/fts/structural/fallback — the E2E-1 never-null leg-mix
    contract); the caller surfaces it as ``"legs"`` in the per-question
    result. Default None = no trace (byte-identical behavior).

    include_terminal=True (E5 #1537, E2E-6): superseded points co-retrieve
    so the reader sees the [SUPERSEDED BY] marker and discounts them; the
    marker (A2) is the reader's discount mechanism. Terminal exclusion
    (#1391) would hide the superseded claim entirely.

    R4 (#1543): structural leg activated — structural_kind scans the
    extracted (statement) points so run_structural_query stops returning
    []; hops=2 expands text hits over IMPL/NAND edges (graph as recall
    amplifier). structural_kind deliberately does NOT post-filter: the pool
    must keep the R1 union (turn points + raw transcripts + extracted
    points).
    """
    merged: dict[str, dict] = {}
    for et in entity_types:
        for h in sdk.tortoise_fts_query(
            query, entity_type=et, limit=limit,
            structural_kind=EXTRACTION_POINT_KIND,
            structural_hops=2,
            include_terminal=True, leg_trace=leg_trace,
            recency_field=(recency_fields.get(et) if recency_fields
                           else None),
            recency_boost=recency_boost,
        ):
            merged[h["id"]] = h
    # deterministic union: RRF score desc, then id (no namespace collision
    # — Point ids and eventIds are distinct namespaces by construction).
    return sorted(
        merged.values(),
        key=lambda h: (-((h.get("scores") or {}).get("rrf") or 0.0),
                       h["id"]),
    )


def _vector_retrieve(sdk: TortoiseSDK, question: dict, qid: str, *,
                     ks: tuple[int, ...], top_k: int) -> dict[str, Any]:
    """#1349 vector-only arm: encode with the injected model, run
    ``search_engine.run_vector_query`` ONLY (never tortoise_fts_query).

    Returns the pinned outcome shape the gate consumes: ranked ids,
    evidence-turn matches, and nDCG@10 / P@10 / P@5 alongside the standard
    session/turn recall@k over the raw vector hits (no dedup pool, no
    rerank, no TR knobs — the metric is the raw vector ranking).
    """
    answer_sessions = set(question.get("answer_session_ids") or [])
    dates: list[str] = question.get("haystack_dates") or []
    evidence_turn_ids = {
        f"lme:{qid}:s{si}:t{ti}"
        for si, session in enumerate(question.get("haystack_sessions") or [])
        for ti, turn in enumerate(session)
        if turn.get("has_answer")
    }

    start = time.monotonic()
    raw_hits = vector_search(sdk, question["question"], limit=max(ks))
    latency_ms = (time.monotonic() - start) * 1000.0
    hits: list[dict] = [
        {"id": pid, "score": score, "match_source": "vector"}
        for pid, score in raw_hits
    ]

    props = point_props_for_hits(sdk._get_proj(), [h["id"] for h in hits])
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

    # ── recall@k (session-level + turn-level) over the raw vector ranking ──
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
    question_date = question.get("question_date", "") or None
    context_text = render_context(context_points, question_date=question_date)
    context_tokens = _estimate_tokens(context_text) if context_text else 0

    ranked_ids = [h["id"] for h in annotated]
    out: dict[str, Any] = {
        "question_id": qid,
        "retriever": "vector",
        "hits": annotated,
        "session_recall@k": session_recall,
        "turn_recall@k": turn_recall,
        "context_tokens": context_tokens,
        "context_point_count": len(context_points),
        "retrieval_latency_ms": round(latency_ms, 2),
        # #1349 gate metrics — computed alongside turn_recall@10.
        "ranked_ids": ranked_ids,
        "evidence_turn_matches": sorted(evidence_turn_ids & set(ranked_ids)),
        "ndcg@10": round(ndcg_at_k(ranked_ids, evidence_turn_ids, k=10), 6),
        "p@10": round(precision_at_k(ranked_ids, evidence_turn_ids, k=10), 6),
        "p@5": round(precision_at_k(ranked_ids, evidence_turn_ids, k=5), 6),
    }
    return out

def retrieve_for_question(
    sdk: TortoiseSDK,
    question: dict,
    *,
    ks: tuple[int, ...] = (5, 10, 20),
    top_k: int = 20,
    # #1349: the vector arm (``retriever="vector"``) bypasses the hybrid
    # pool/rerank/TR path entirely — run_vector_query ONLY, nDCG@10 + P@10 +
    # P@5 + ranked ids + evidence-turn matches in the outcome.
    retriever: str = "hybrid",
    max_chunks_per_session: int = DEFAULT_MAX_CHUNKS_PER_SESSION,
    max_context_tokens: int = DEFAULT_CONTEXT_TOKEN_CAP,
    # R5 (#1544): TR knobs — temporal-reasoning questions get the events
    # union pool, the engine recency weight, the TR-constraint window
    # filter, time-ascending rendering, and the tighter ``tr_top_k`` cap
    # (20→12 — the transcript-flood control). Non-TR questions ignore all
    # of them (byte-identical path, regression-guarded).
    tr_top_k: int = DEFAULT_TR_TOP_K,
    tr_date_weight: float = 0.5,
    tr_events: bool = True,
    # R6 (#1545): rerank knobs — the post-fusion cross-encoder + MMR stage,
    # OFF by default (the V3 baseline path is byte-identical; no rerank keys
    # off-path, D2). ``rerank`` tri-state: True/False explicit, None = env
    # TORTOISE_LME_RERANK (fail-safe OFF). ``rerank_pool`` is honored when
    # explicitly passed even with rerank off (the pool-only isolation arm);
    # env defaults apply only while rerank is on.
    rerank: bool | None = None,
    rerank_model: str | None = None,
    rerank_pool: int | None = None,
    per_session_cap: int | None = None,
    mmr_lambda: float | None = None,
    # C1 (#1745): the reader-context ITEM cap — the run path passes the
    # resolved knob (default 40, env ``TORTOISE_LME_CONTEXT_ITEMS``); TR
    # questions IGNORE it and keep the pinned ``tr_top_k`` item cap (R5
    # flood control is never silently undone). None = env default.
    context_item_cap: int | None = None,
    # C2 (#1745): evidence-mark boost — OFF by default in code (the plan's
    # default decision: ON only for the re-validation run via
    # ``TORTOISE_LME_EVIDENCE_BOOST`` or ``evidence_boost=True``).
    # Tri-state: True/False explicit, None = env (only 1/true/yes/on
    # enables). ``evidence_boost_verbatim`` / ``evidence_boost_source``
    # override the per-class rank-offset multipliers (env fallbacks
    # ``TORTOISE_LME_EVIDENCE_BOOST_VERBATIM`` /
    # ``TORTOISE_LME_EVIDENCE_BOOST_SOURCE``).
    evidence_boost: bool | None = None,
    evidence_boost_verbatim: float | None = None,
    evidence_boost_source: float | None = None,
) -> dict[str, Any]:
    """Run retrieval for one question and compute recall@k + context stats.

    ``top_k`` is the design-locked context depth (default 20 — recall is
    reported at every k in ``ks``).
    R1 #1540: candidates are fetched at ``max(ks) * DEFAULT_POOL_MULTIPLIER``
    depth, the pool is deduped per-session (``max_chunks_per_session`` raw
    chunks per session), recall@k is computed over the DEDUPED pool
    (``ret["hits"]`` == the pool — pinned contract).
    C1 (#1745): the reader's context is the budget-capped RANK-INTERLEAVED
    ``_assemble_context`` output (``context_points`` — points and chunks
    interleaved in true RRF rank order, bounded by ``max_context_tokens``
    AND ``context_item_cap`` (default 40, env
    ``TORTOISE_LME_CONTEXT_ITEMS``); TR keeps the pinned ``tr_top_k`` item
    cap). Since C1 the pool is an APPROXIMATE upper bound on what the
    reader sees (skip-not-starve can admit a lower-ranked marked item
    into the k-prefix) — ``reader_evidence@k`` (C4) is the independent
    reader-surface measure.
    C2 (#1745): ``evidence_boost`` (OFF by default in code — env
    ``TORTOISE_LME_EVIDENCE_BOOST`` or the explicit flag enables it)
    re-ranks marked hits by a stable rank offset BEFORE ``_recall_metrics``
    via read-time ``mark_for`` recompute; ``evidence_boost_verbatim`` /
    ``evidence_boost_source`` set the per-class multipliers. Task 0
    (#1745): ``ranked_ids`` / ``evidence_turn_matches`` /
    ``ranked_ids_pre_boost`` are populated so the context composition is
    reconstructable.

    R5 (#1544) D4–D7 (TR questions only, ``question_type ==
    "temporal-reasoning"``): the pool is the point+event union
    (``hybrid_search`` entity_types — E2E-4's "no point-only filter"),
    recency-weighted by the engine (``recency_fields`` → ``recency_boost``
    keyed on the dataset's haystack_dates via graph createdAt/startedAt),
    TR-constraint detection drives a time-window filter BEFORE truncation
    (``tr_window_fallback`` when the filter would empty the dated pool —
    never starve the reader into abstention), and the reader context
    renders time-ascending (dated first, undated last). ``effective_top_k
    = tr_top_k if is_tr else top_k``. Recall metrics keep retrieval (RRF +
    date-weight) order — they measure retrieval, not rendering.
    """
    qid = question["question_id"]
    if retriever not in ("hybrid", "vector"):
        raise ValueError(f"retriever must be 'hybrid' or 'vector', got {retriever!r}")
    if retriever == "vector":
        return _vector_retrieve(sdk, question, qid, ks=ks, top_k=top_k)
    is_tr = question.get("question_type") == "temporal-reasoning"
    effective_top_k = tr_top_k if is_tr else top_k
    # C1 (#1745): the resolved reader-context item cap — TR questions keep
    # the pinned ``tr_top_k`` item cap (R5 flood control must never be
    # silently undone by the budget walk); non-TR uses the explicit arg or
    # the ``TORTOISE_LME_CONTEXT_ITEMS`` env default (40).
    if is_tr:
        eff_item_cap = tr_top_k
    elif context_item_cap is not None:
        eff_item_cap = context_item_cap
    else:
        from .rerank import _env_int
        eff_item_cap = _env_int("TORTOISE_LME_CONTEXT_ITEMS",
                                DEFAULT_CONTEXT_ITEM_CAP)
    answer_sessions = set(question.get("answer_session_ids") or [])
    dates: list[str] = question.get("haystack_dates") or []
    evidence_turn_ids = {
        f"lme:{qid}:s{si}:t{ti}"
        for si, session in enumerate(question.get("haystack_sessions") or [])
        for ti, turn in enumerate(session)
        if turn.get("has_answer")
    }

    # ── R6 (#1545) D3: scorer resolution precedes pool deepening — a
    # degraded (load-failure) question retrieves the SAME baseline pool as
    # the V3 run (never a 40-pool stand-in — D3/S11 pool contamination),
    # and the deep pool is only fetched when it would be honored. Call-time
    # imports keep the monkeypatch seam (tests inject a fake scorer). ──
    from .rerank import _env_float, _env_int, get_scorer, rerank_enabled, rerank_hits
    rerank_on = rerank_enabled(rerank)
    scorer, degrade_reason = (get_scorer(rerank_model) if rerank_on
                              else (None, ""))
    cap = (per_session_cap if per_session_cap is not None
           else _env_int("TORTOISE_LME_RERANK_CAP", 2))
    lam = (mmr_lambda if mmr_lambda is not None
           else _env_float("TORTOISE_LME_RERANK_LAMBDA", 0.7))
    pool_override = (rerank_pool if rerank_pool is not None
                     else _env_int("TORTOISE_LME_RERANK_POOL", 40))
    if rerank_on and scorer is not None:
        # applied arm — the deep pool (default 2x the baseline 20) gives the
        # reranker reorderable headroom (D4)
        pool_limit = max(pool_override, max(ks))
    elif (rerank_pool is not None) and not rerank_on:
        # pool-only isolation arm (--rerank off --rerank-pool N): deeper
        # pool, baseline ordering, hits truncated to top_k (OQ5)
        pool_limit = max(rerank_pool, max(ks))
    else:
        # baseline / degraded / off — the exact current fetch depth
        pool_limit = max(ks) * DEFAULT_POOL_MULTIPLIER

    # ── R3 (#1542) D4: per-leg trace (E2E-1 never-null leg-mix). The
    # retrieval records into ``legs`` at the engine (tortoise_fts_query) and
    # the result surfaces a SNAPSHOT copy (list(legs)) so late appends — a
    # cancelled worker thread past the 500ms deadline — can never mutate the
    # recorded outcome. Default-None callers are byte-identical. ──
    legs: list[dict] = []
    start = time.monotonic()
    # R1: pool-depth headroom — a monopolizing session's points must not
    # crowd other sessions out BEFORE dedup runs (E2E-1).
    # R5 (D4): TR questions fetch the point+event union (E2E-4's "no
    # point-only filter"); non-TR keeps the exact points-only path.
    hits = hybrid_search(
        sdk, question["question"],
        limit=pool_limit,
        leg_trace=legs,
        entity_types=("point", "event") if (is_tr and tr_events)
        else ("point",),
        recency_fields=({"point": "createdAt", "event": "startedAt"}
                        if is_tr else None),
        recency_boost=tr_date_weight if is_tr else 0.0,
    )
    latency_ms = (time.monotonic() - start) * 1000.0

    point_props = point_props_for_hits(sdk._get_proj(), [h["id"] for h in hits])
    # R5 (D8): event hits join the same annotation surface — merge the
    # event props (namespaces disjoint; lookup by h["id"] unchanged).
    event_props = event_props_for_hits(sdk._get_proj(), [h["id"] for h in hits])
    props = {**point_props, **event_props}

    # E3 (D7): resolve speaker for source-turn links whose turn node was NOT
    # itself retrieved — one batch query (the derivation surface E2E-5 builds
    # on; turn points carry their own speaker prop).
    turn_ids = [p.get("source_turn_id") for p in props.values()
                if p.get("source_turn_id")]
    speaker_by_turn = _speaker_for_turns(sdk._get_proj(), turn_ids)
    for p in props.values():
        if not p.get("speaker") and p.get("source_turn_id"):
            p["speaker"] = speaker_by_turn.get(p["source_turn_id"], "")

    # Annotate hits with session/has_answer/point_kind + promoted
    # supersession state (#1367). SearchResult carries sessionId only when
    # the engine populates it; fetch is single-query and canonical.
    # session_date comes from the dataset's haystack_dates (surfaced to the
    # reader so temporal questions are answerable — P1 #1144).
    # superseded_by/supersedes pass through from the search payload's D8
    # fields (additive — E5 #1537 decorates the embedded fallback too, so
    # markers render in embedded AND full modes).
    annotated = _annotate_hits(hits, props, dates)

    # ── R5 (D5): TR-constraint detection → time-window filter BEFORE
    # truncation (the in-window pool is what session_recall@k measures —
    # correct TR semantics). Defensive rule: when the filter would empty
    # the dated pool (no in-window hits), fall back to the unfiltered pool
    # — never starve the reader into abstention — recorded as
    # ``tr_window_fallback``. Non-TR questions skip detection entirely. ──
    tr_constraint = None
    tr_window_fallback = False
    if is_tr:
        question_date = question.get("question_date", "") or None
        try:
            year = int(question_date[:4]) if question_date else None
        except ValueError:
            year = None
        tr_constraint = detect_time_constraint(
            question["question"], default_year=year)
        if tr_constraint.kind in ("interval", "recency"):
            windowed = _apply_time_window(annotated, tr_constraint,
                                          question_date=question_date)
            if windowed:
                annotated = windowed
            else:
                tr_window_fallback = True  # keep the unfiltered pool

    # ── deduped pool (the retrieval contract: ret["hits"] == pool) ──
    pool = _dedup_pool(annotated, max_chunks_per_session=max_chunks_per_session)
    n_chunks_retrieved = sum(1 for h in annotated if _is_raw_chunk(h))
    n_chunks_pool = sum(1 for h in pool if _is_raw_chunk(h))

    # ── C2 (#1745): evidence-mark boost — applied to the DEDUPED pool
    # BEFORE ``_recall_metrics`` (the only pool-metric mover: C1 cannot
    # move the pool-based evidence@20 at all). OFF by default in code —
    # enabled only by the explicit ``evidence_boost`` flag or the
    # ``TORTOISE_LME_EVIDENCE_BOOST`` env (fail-safe OFF: only
    # 1/true/yes/on enables — the plan's default decision: ON for the
    # re-validation run). Stage order vs R6 rerank:
    # boost-before-rerank (documented, P3). TR questions boost too — the
    # window filter already ran; the boost only re-ranks within it. ──
    if evidence_boost is not None:
        boost_on = evidence_boost
    else:
        from .rerank import _TRUTHY
        boost_env = (os.environ.get("TORTOISE_LME_EVIDENCE_BOOST") or "")
        boost_on = boost_env.strip().lower() in _TRUTHY
    if boost_on:
        from .rerank import _env_boost_float
        bv = (evidence_boost_verbatim if evidence_boost_verbatim is not None
              else _env_boost_float("TORTOISE_LME_EVIDENCE_BOOST_VERBATIM",
                                    DEFAULT_EVIDENCE_BOOST_VERBATIM))
        bs = (evidence_boost_source if evidence_boost_source is not None
              else _env_boost_float("TORTOISE_LME_EVIDENCE_BOOST_SOURCE",
                                    DEFAULT_EVIDENCE_BOOST_SOURCE))
        pool, evidence_boost_stats = _apply_evidence_boost(
            pool, question=question, boost_verbatim=bv, boost_source=bs)
    else:
        evidence_boost_stats = {
            "applied": False,
            "pre_boost_ranked_ids": [h["id"] for h in pool],
        }

    # ── evidence denominators (D5 split, #1540): turn/evidence recall count
    # extracted points ONLY (pointKind <> session-transcript); containment-
    # marked raw chunks contribute exclusively to chunk_evidence_recall@k
    # (removes the granularity-bias confound: with chunks in the shared
    # denominator, the per-session chunk cap would structurally cap the
    # numerator below it and the ceiling would tighten as chunk_turns
    # shrinks). ──
    ev_rows = sdk._get_proj().g.query(
        "MATCH (p:Point) WHERE p.lme_question_id = $q AND p.has_answer = true "
        "AND coalesce(p.pointKind, '') <> 'session-transcript' "
        "RETURN count(*)", params={"q": qid}).result_set
    evidence_point_count = ev_rows[0][0] if ev_rows else 0
    ch_rows = sdk._get_proj().g.query(
        "MATCH (p:Point) WHERE p.lme_question_id = $q AND p.has_answer = true "
        "AND coalesce(p.pointKind, '') = 'session-transcript' "
        "RETURN count(*)", params={"q": qid}).result_set
    chunk_evidence_point_count = ch_rows[0][0] if ch_rows else 0

    # ── R3 (#1542) D3: write-time embedding coverage — the dense leg is
    # OBSERVABLE per question, never assumed. In the fresh-graph protocol
    # every Point for the question is written by this ingest, so coverage IS
    # write-time embedding coverage: 1.0 with the embedder present, 0.0
    # (recorded, never silent) without; a question with zero points yields
    # None (pinned shape, no crash). ──
    cov_rows = sdk._get_proj().g.query(
        "MATCH (p:Point) WHERE p.lme_question_id = $q "
        "RETURN count(p), count(p.embedding)",
        params={"q": qid}).result_set
    total_pts, embedded_pts = cov_rows[0] if cov_rows else (0, 0)
    embedding_coverage = (embedded_pts / total_pts) if total_pts else None

    # ── R6 (#1545): post-fusion rerank stage — the measured surface (S11).
    # Off-path byte-identical (no rerank keys, no extra queries); the
    # reader/context invariant holds on EVERY path:
    #   * non-applied (pool-only / score-failure / load-failure degrade):
    #     hits truncated to top_k (len(hits) == context_point_count ==
    #     min(len(pool), top_k));
    #   * applied: rerank_hits returns the SELECTED-ONLY reordered list, so
    #     len(hits) == selected_count <= top_k;
    #   * plain off (no rerank, no pool override): the full deduped pool
    #     UNTRUNCATED (today's behavior — the two coincide at defaults
    #     where the pool <= top_k).
    rerank_pass: dict[str, Any] = {"applied": False, "dropped": 0}
    rerank_ms = 0.0
    if rerank_on:
        t0 = time.monotonic()
        applied = False
        if scorer is None:
            # degraded: 20-pool fallback + truncation (D3(a)/S25) — never a
            # 40-pool stand-in masquerading as a rerank result
            rerank_pass["degrade_reason"] = degrade_reason
            pool = pool[:top_k]
        else:
            pool_recall = _recall_metrics(
                pool, ks=ks, answer_sessions=answer_sessions,
                evidence_turn_ids=evidence_turn_ids,
                evidence_point_count=evidence_point_count,
                chunk_evidence_point_count=chunk_evidence_point_count)
            selected, stats = rerank_hits(
                question["question"], pool, scorer=scorer,
                proj=sdk._get_proj(), top_k=top_k,
                per_session_cap=cap, lambda_=lam)
            applied = bool(stats.get("applied", True))
            if applied:
                rerank_pass.update(stats)
                rerank_pass["pool_size"] = len(pool)
                rerank_pass["pool_recall@k"] = {
                    "session": pool_recall[0], "turn": pool_recall[1],
                    "evidence": pool_recall[2],
                    "chunk_evidence": pool_recall[3]}
                pool = selected          # the hits ARE the reader's context
            else:
                # score-failure degrade (D8c) — same contract as load-failure
                rerank_pass["degrade_reason"] = stats.get("degrade_reason", "")
                pool = pool[:top_k]      # reader/context invariant
        rerank_pass["applied"] = applied
        rerank_ms = (time.monotonic() - t0) * 1000.0
    elif (rerank_pool is not None) and not rerank_on:
        pool = pool[:top_k]              # pool-only arm: reader sees top_k

    # ── recall@k over the DEDUPED pool (session + turn + evidence + chunk) ──
    # (on the applied path, ``pool`` is the rerank-selected list — recall
    # measures what the reader could actually see; ``rerank_pass["pool_recall@k"]``
    # carries the pre-MMR pool recall for the selection-loss diagnostic.
    # C2 (#1745): when the evidence boost is on, ``pool`` here is the
    # BOOSTED pool — ``evidence_recall@k`` is honestly "evidence recall
    # over the boosted pool" (stated in the methodology); the pre-boost
    # order rides in ``evidence_boost.pre_boost_ranked_ids``.)
    (session_recall, turn_recall, _evidence_recall,
     chunk_evidence_recall) = _recall_metrics(
        pool, ks=ks, answer_sessions=answer_sessions,
        evidence_turn_ids=evidence_turn_ids,
        evidence_point_count=evidence_point_count,
        chunk_evidence_point_count=chunk_evidence_point_count)

    # ── context handed to the reader (C1 #1745: budget-capped, rank-
    # interleaved; TR keeps the pinned tr_top_k item cap) ──
    question_date = question.get("question_date", "") or None
    context_points = _assemble_context(
        pool, top_k=effective_top_k,
        max_context_tokens=max_context_tokens,
        question_date=question_date,
        context_item_cap=eff_item_cap)
    # R5 (D6): TR context renders time-ascending — after truncation the
    # context list is stable-sorted by session_date (dated first, undated
    # last, stable within a date = retrieval order preserved). Recall
    # metrics keep retrieval order: only the READER's context list is
    # reordered. Non-TR keeps RRF order.
    if is_tr:
        context_points = sorted(
            context_points,
            key=lambda h: ((1, "") if not h.get("session_date")
                           else (0, h["session_date"])),
        )
    # The reader consumes the SAME rendered context (with the Current Date
    # header) — keep context_tokens aligned with what the reader saw.
    context_text = render_context(context_points, question_date=question_date)
    context_tokens = _estimate_tokens(context_text) if context_text else 0

    # ── C4 (#1745): reader_evidence@k — the honest reader-surface measure.
    # Fraction of evidence-marked hits actually present in
    # context_points[:k] / marked total (the SAME D5 denominator as the
    # pool-based evidence_recall@k). The pool-based evidence_recall@k is
    # NOT a strict upper bound on it: the budget walk's skip-not-starve
    # lets a lower-ranked marked item enter context_points[:k] (an
    # oversized higher-ranked hit is skipped, not dropped), so
    # reader_evidence@k is an INDEPENDENT reader-surface measure — pool
    # recall is an APPROXIMATE upper bound up to budget-skip effects. For
    # TR questions the k-prefix follows the R5 time-ascending render
    # (session_date order, stable within a date = retrieval order) — the
    # reader's READING order, not RRF rank. The C2 pre/post ablation rides
    # ``evidence_boost.pre_boost_ranked_ids``. ──
    reader_evidence: dict[str, float | None] = {}
    for k in ks:
        ctx_top = context_points[:k]
        ctx_ev = {h["id"] for h in ctx_top
                  if h["has_answer"] and not _is_raw_chunk(h)}
        reader_evidence[str(k)] = (
            len(ctx_ev) / evidence_point_count if evidence_point_count
            else None)

    out = {
        "question_id": qid,
        "hits": pool,  # pinned contract: the deduped pool (R1 #1540)
        "session_recall@k": session_recall,
        "turn_recall@k": turn_recall,
        "evidence_recall@k": _evidence_recall,
        "chunk_evidence_recall@k": chunk_evidence_recall,
        # M7 (#1527, D2/D4): leg-mix over what the reader saw (context_points)
        # + per-k over the deduped pool; evidence_retrieved@k = the turn_recall
        # numerator (has_answer non-chunk hits in pool[:k]) — persisted so the
        # report can answer "which leg found what" and "how much evidence was
        # retrieved" (evidence-written/retrieved accounting). The R6 rerank
        # bucket is applied to ``match_source_counts`` just before return (D6).
        "match_source_counts": _leg_mix(context_points),
        "match_source_counts@k": {
            str(k): _leg_mix(pool[:k]) for k in ks},
        "evidence_retrieved@k": {
            str(k): sum(1 for h in pool[:k]
                        if h["has_answer"] and not _is_raw_chunk(h))
            for k in ks},
        "context_points": context_points,
        "context_tokens": context_tokens,
        "context_point_count": len(context_points),
        # C4 (#1745): the reader-surface evidence metric — fraction of
        # evidence-marked hits present in context_points[:k] / marked total
        # (the metric C1 actually moves; pool recall is an APPROXIMATE
        # upper bound — skip-not-starve can admit a lower-ranked marked
        # item into the k-prefix).
        # N/A (None) on empty denominators, mirroring evidence_recall@k.
        "reader_evidence@k": reader_evidence,
        # Task 0 (#1745): ranked ids + evidence-turn matches populated for
        # the hybrid arm (the pilot's context composition was
        # unreconstructable — 0/50). ``ranked_ids`` is the effective pool
        # order (post-boost when C2 is on); ``ranked_ids_pre_boost`` is the
        # raw retrieval order for the C4 pre/post ablation (identical when
        # both the boost and the R6 rerank stage are off; on the rerank
        # path it is the pre-boost but PRE-RERANK order — the ablation
        # compares rerank(boost(pool)) vs rerank(pool)).
        "ranked_ids": [h["id"] for h in pool],
        "ranked_ids_pre_boost": evidence_boost_stats.get(
            "pre_boost_ranked_ids", [h["id"] for h in pool]),
        "evidence_turn_matches": sorted(
            {h["id"] for h in pool if h["has_answer"]}
            | (evidence_turn_ids & {h["id"] for h in pool})),
        # C2 (#1745): the boost block — applied flag + per-class
        # multipliers + read-time mark census + pre-boost order. Always
        # present (applied=False on the default off path).
        "evidence_boost": evidence_boost_stats,
        # R5 (#1544): TR-constraint surface — the detected kind (TR only)
        # and whether the window filter fell back to the unfiltered pool
        # (never starve the reader into abstention).
        "tr_constraint": tr_constraint.kind if tr_constraint else None,
        "tr_window_fallback": tr_window_fallback,
        # R3 (#1542) D3: write-time embedding coverage (observable dense leg).
        "points_total": total_pts,
        "points_embedded": embedded_pts,
        "embedding_coverage": embedding_coverage,
        # R3 (#1542) D4: the per-leg trace (vector/fts/structural/fallback),
        # snapshotted so a late append cannot mutate the recorded outcome.
        "legs": list(legs),
        "dedup_stats": {
            "chunks_retrieved": n_chunks_retrieved,
            "chunks_capped": n_chunks_retrieved - n_chunks_pool,
            "pool_depth_requested": pool_limit,
        },
        "retrieval_latency_ms": round(latency_ms + rerank_ms, 2),
    }
    # R6 (#1545) D6: the rerank pass is recorded ADDITIVELY — the leg-mix
    # ``rerank`` bucket counts selection-loss only (the ``mmr_dropped`` hits),
    # so ``sum(provenance legs) + dropped == pool_size`` holds as a partition
    # (never an overlay). ``reranked``/``mmr_promoted`` movement stays a
    # separate overlay metric (``rerank_pass``), never a leg. Off-path emits
    # no bucket (byte-identical leg-mix).
    match_source_counts = _leg_mix(context_points)
    if rerank_on and rerank_pass.get("applied"):
        match_source_counts["rerank"] = rerank_pass.get("dropped", 0)
    out["match_source_counts"] = match_source_counts
    # Conditional keys (D2): the off-path dict keeps today's exact shape.
    if rerank_on:
        out["rerank_pass"] = rerank_pass
        out["rerank_latency_ms"] = round(rerank_ms, 2)
    return out
