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

R1 #1540 (epic #1509): candidates are fetched at the ``pool_size`` depth
(default 120, knob ``TORTOISE_LME_POOL_SIZE`` — #1947: deepened from R1's
``max(ks) * 3`` = 60 so marked evidence points ranked below rank 60 can
enter the pool; the deepest recall horizon ``max(ks)`` is always the
floor), the pool is deduped per-session (``max_chunks_per_session`` raw
chunks per session, rank order — E2E-1).

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
marks are recomputed at read time (``evidence.mark_for``) so the
#1763 answer-string marks (the point carries the GOLD ANSWER — the
strongest, answer-precise class, #1945) get the highest boost, verbatim/
raw-chunk marks the full boost, and source-session-only marks a reduced
one. OFF by default in code (env ``TORTOISE_LME_EVIDENCE_BOOST``
or the explicit ``evidence_boost`` flag enables it) — the plan's default
decision: ON only for the re-validation run. #1945: the retrieval leg
additionally emits the HONEST answer-availability metric per outcome
(``answer_string_evidence_recall@k`` — mark (d), over the effective pool)
that report.py aggregates alongside the legacy evidence_recall@k.
"""
# ═════════════════════════════════════════════════════════════════════════
# ══ HARNESS PURPOSE — READ THIS FIRST ════════════════════════════════════
# tools/longmem_eval/ is a THIN MEASUREMENT LAYER over the product
# (tortoise/): the eval calls the product's OWN engine
# (TortoiseSDK.tortoise_fts_query, extractor_v2, model_adapters) and
# measures it — there is no parallel eval retrieval stack. Quality
# improvements therefore belong IN tortoise/ (that is what ships to
# customers). Eval-only quality knobs are DOCUMENTED DEBT: each carries a
# PRODUCT-PARITY NOTE at its site naming the product default (with
# file:line), why it is eval-only, and the tracking issue for shipping it.
# See docs/audit/2026-08-29-product-cohesion.md for the full audit.
# ═════════════════════════════════════════════════════════════════════════
from __future__ import annotations

import math
import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from tortoise import search_engine
from tortoise.embeddings import EmbeddingModel
from tortoise.sdk import TortoiseSDK

from . import encode_cache, evidence
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

# ── #1786 (R5): eval retrieval budget via the existing elevation seam ────
#: The eval's HYBRID-arm collective retrieval deadline (ms) — threaded into
#: ``tortoise_fts_query(_elevated_timeout_ms=...)`` → ``degradation_chain``
#: (per-strategy server timeout + the ``as_completed`` deadline). Derived
#: from the healthy baseline (p95 82 ms / max 128 ms) + the issue target
#: p95 ≤ 2 s minus ~500 ms collection-overhead headroom. The SDK default
#: stays 500 ms (``tests/bench/test_degradation_chain.py`` untouched). The
#: VECTOR arm is deliberately 3.3x more permissive (``VECTOR_TIMEOUT_MS``,
#: ``retrieve.py:150-153`` precedent) and is UNAFFECTED by this budget.
# ══ PRODUCT-PARITY NOTE (eval-only) ══════════════════════════════════════
# This is a QUALITY knob that lives in the eval harness and is NOT wired
# into the product (tortoise/) path.
#   Product default: 500 ms degradation cap — sdk.tortoise_fts_query
#       applies ``timeout_ms=int(_elevated_timeout_ms or 500)``
#       (tortoise/sdk.py:9771); the ``_elevated_timeout_ms`` seam is
#       PRIVATE, benchmark-only (#316).
#   Why eval-only:   #1786 (R5) lifts the eval's hybrid-arm budget to
#       1500 ms (p95 ≤ 2 s target) so deep-pool retrieval isn't truncated
#       by the product's interactive-latency cap. The product keeps
#       500 ms by design.
#   Ship-to-product: not applicable — a product budget change is a
#       latency decision, not a parity gap; no tracking issue filed.
#   Rationale:       eval-only measurement budget — the harness measures
#       the product as-is plus eval elevation, never silently.
# ═════════════════════════════════════════════════════════════════════════
EVAL_RETRIEVAL_BUDGET_MS = 1500
#: The SDK-default collective cap (sdk.py ``_elevated_timeout_ms or 500``)
#: — recorded in the report methodology when the eval does not elevate.
DEFAULT_RETRIEVAL_BUDGET_MS = 500


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
#: C2 (#1745): evidence-mark boost rank-offset multipliers — the answer-
#: string mark (d, #1763 — the point's content carries the GOLD ANSWER, the
#: strongest/answer-precise signal) gets the highest multiplier, verbatim/
#: raw-chunk marks (the precise ones) the full boost, source-session-only
#: points a reduced one (marks never influence RRF ranking — H2). #1945:
#: the answer_string class is new — the reval3 census is 65% source_session
#: / 34% answer_string / 1.1% verbatim, and C2 had no class for the honest
#: mark. Knobs: ``TORTOISE_LME_EVIDENCE_BOOST_ANSWER_STRING`` /
#: ``TORTOISE_LME_EVIDENCE_BOOST_VERBATIM`` /
#: ``TORTOISE_LME_EVIDENCE_BOOST_SOURCE``.
# ══ PRODUCT-PARITY NOTE (eval-only) ══════════════════════════════════════
# This is a QUALITY knob that lives in the eval harness and is NOT wired
# into the product (tortoise/) path.
#   Product default: no boost — product ranking is content-similarity-only
#       (RRF fusion in tortoise_fts_query); marks never influence ranking
#       (H2) in either codebase. Even in the eval this is OFF by default
#       (env ``TORTOISE_LME_EVIDENCE_BOOST`` / explicit ``evidence_boost``
#       flag) — #1945/#1745 C2, landed eval-side only (commit 6b36d5fe).
#   Why eval-only:   the boost re-ranks marked hits so the pool-based
#       evidence_recall@k measures the boosted pool — a benchmark
#       measurement device. The gold-answer marks it boosts on (#1763,
#       mark (d)) are eval-time truths the product never computes.
#   Ship-to-product: not filed — the boost is inseparable from the
#       evidence-marking machinery, itself eval-only; a product port
#       would be a new feature (evidence-grounded reranking).
#   Rationale:       the harness exists to IMPROVE the product; an
#       evidence-aware rerank is a candidate product feature, not a
#       harness invention.
# ═════════════════════════════════════════════════════════════════════════
DEFAULT_EVIDENCE_BOOST_ANSWER_STRING = 2.0
DEFAULT_EVIDENCE_BOOST_VERBATIM = 1.5
DEFAULT_EVIDENCE_BOOST_SOURCE = 1.15
#: R1 (#1540): reader context token budget (≈ the pre-v2 baseline context
#: size — a 4.4x reduction from the measured 35k whole-session flood;
#: LightMem: compact evidence wins under tight budgets).
DEFAULT_CONTEXT_TOKEN_CAP = 8000
#: #1947: pool fetch depth for the baseline hybrid arm — deepened 60→120
#: (reval3: 66% of marked evidence points never entered the 60-item pool,
#: so the C2 boost had no material to work with; the vector leg already
#: returns 120 hits/question). Knob ``TORTOISE_LME_POOL_SIZE``; the
#: deepest recall horizon ``max(ks)`` is always the floor (recall@k is
#: computed over the deduped pool). Replaces R1 #1540's ``max(ks) * 3``
#: candidate-depth headroom — depth also serves the R1 contract (one
#: session's points must not crowd other sessions out BEFORE dedup runs).
#: The headroom is now FLAT-capped at the configured depth (previously
#: proportional ``max(ks) * 3``) — in-repo callers never exceed ``max(ks)
#: = 40`` (old and new depth coincide there at 120); external callers
#: porting R1 semantics should raise ``pool_size`` explicitly for deeper
#: recall horizons.
# ══ PRODUCT-PARITY NOTE (eval-only) ══════════════════════════════════════
# This is a QUALITY knob that lives in the eval harness and is NOT wired
# into the product (tortoise/) path.
#   Product default: pool = ``limit * 2`` (20 items at the hosted/MCP
#       default limit=10) with a DELIBERATE env-only opt-in floor
#       (``TORTOISE_POOL_FLOOR``, unset → behaves exactly as pre-#1348) —
#       tortoise/sdk.py:9662-9686 ("NO BAKED DEFAULT FLOOR").
#   Why eval-only:   #1947 deepened the eval pool 60→120 (reval3: 66% of
#       marked evidence never entered the 60-item pool, so the C2 boost
#       had no material) and landed eval-side only (commit ba986f8b
#       touched only tools/longmem_eval/). Shipping the depth is a
#       PRODUCT decision (audit G2: raising the default is the single
#       highest-leverage retrieval change); the harness cannot make it.
#   Ship-to-product: open product decision (audit G2) — no tracking
#       issue filed; tracked as the G2 cohesion gap.
#   Rationale:       the harness exists to IMPROVE the product; the deep
#       pool is a candidate product feature (recall depth), not a
#       harness invention.
# ═════════════════════════════════════════════════════════════════════════
DEFAULT_POOL_SIZE = 120

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


def _mark_bands(ranks: list[int]) -> dict[str, int]:
    """#1947: marked-hit rank bands over the deduped pool — the
    ``pool_depth`` diagnostic's histogram. Bands are the semantically
    meaningful windows: ``top-20`` (the recall@k horizon), ``21-40`` (the
    reader-context window beyond top-20), ``41-120`` (the deepened pool's
    headroom), ``121+`` (beyond the default pool depth — only reachable
    when ``pool_size`` is raised above the default)."""
    counts = {"top-20": 0, "21-40": 0, "41-120": 0, "121+": 0}
    for r in ranks:
        if r < 20:
            counts["top-20"] += 1
        elif r < 40:
            counts["21-40"] += 1
        elif r < 120:
            counts["41-120"] += 1
        else:
            counts["121+"] += 1
    return counts


def _apply_evidence_boost(
    pool: list[dict],
    *,
    question: dict | None = None,
    evidence_sessions: set[str] | None = None,
    answer_turn_contents: list[str] | None = None,
    boost_answer_string: float = DEFAULT_EVIDENCE_BOOST_ANSWER_STRING,
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
    raw-chunk marks, ``boost_answer_string`` for the #1763 answer-string
    mark (d). Placement is position-ceiling promotion (Horn's
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

    #1945: the answer_string mark (d) is a FIRST-CLASS boost class with
    the STRONGEST multiplier (>= verbatim's) — it is the honest
    "this point contains the gold answer" signal, computed at eval time
    from the question's gold ``answer`` (the extractor never sees it).
    Class priority: answer_string (strongest) > verbatim/raw_chunk >
    source_session > unmarked. A stored ``has_answer`` hit with no
    read-time mark still falls back to the source class (conservative).

    The verbatim-vs-source split is recomputed at READ TIME via
    ``evidence.mark_for`` (verifier P1-4: the OR'd ``has_answer`` prop
    cannot express the split) — the annotated hits already carry
    ``content``/``quote``/``session_id`` and the question carries
    ``haystack_sessions`` + the gold ``answer``, so no graph change is
    needed.

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
    if not (math.isfinite(boost_answer_string)
            and boost_answer_string >= 1.0) \
            or not (math.isfinite(boost_verbatim) and boost_verbatim >= 1.0) \
            or not (math.isfinite(boost_source) and boost_source >= 1.0):
        raise ValueError(
            "evidence-boost multipliers must be >= 1.0 and finite (a "
            "rank-scaling division), got answer_string="
            f"{boost_answer_string!r} verbatim={boost_verbatim!r} "
            f"source={boost_source!r}")
    if evidence_sessions is None:
        evidence_sessions = (ev.evidence_sessions(question)
                             if question else set())
    if answer_turn_contents is None:
        answer_turn_contents = [
            (t.get("content") or "")
            for s in ((question or {}).get("haystack_sessions") or [])
            for t in s if t.get("has_answer")
        ]
    # #1945: the gold answer (mark (d)) is a benchmark truth the dataset
    # question carries — empty when absent (None question / no answer),
    # in which case answer_string never fires and the class is inert.
    gold_answer = str((question or {}).get("answer") or "")
    census = {"source_session": 0, "verbatim": 0, "raw_chunk": 0,
              "answer_string": 0}
    scored: list[tuple[dict, float, int]] = []
    marked_by_idx: dict[int, bool] = {}
    for i, h in enumerate(pool):
        marks = ev.mark_for(
            h, session_id=h.get("session_id"),
            evidence_sessions=evidence_sessions,
            answer_turn_contents=answer_turn_contents,
            gold_answer=gold_answer)["marks"]
        for mk in census:
            if marks.get(mk):
                census[mk] += 1
        # #1945: the answer-string class is the strongest signal (the
        # point carries the GOLD ANSWER) and takes priority over the
        # verbatim/raw-chunk provenance classes.
        if marks.get("answer_string"):
            factor = boost_answer_string
        elif marks.get("verbatim") or marks.get("raw_chunk"):
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
        "boost_answer_string": boost_answer_string,
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

    #1948 turn-vs-evidence semantics (PINNED — reval3 finding (a)):
    ``turn_recall@k`` and ``evidence_recall@k`` are THE SAME formula whenever
    evidence points exist — both are marked (``has_answer``) non-chunk hits
    in top-k ÷ ``evidence_point_count``. The reval3 aggregate split
    (turn@20 0.722 vs evidence@20 0.299) is a denominator/population
    artifact, NOT a separate "turn vs evidence" retrieval phenomenon.
    They diverge ONLY on degraded questions where ``evidence_point_count``
    is 0 (ingest wrote no evidence points): ``evidence_recall@k`` is None
    (M6 #1526 — "no evidence exists" stays distinguishable from "never
    surfaces") while ``turn_recall@k`` falls back to the DETERMINISTIC
    answer-turn binary — did the answer TURN id (``evidence_turn_ids``)
    surface in top-k (31/33 = 1.0 on reval3's degraded population). A
    turn/evidence aggregate pair over a mixed population is therefore a
    MIXED metric; compare them only on the evidence-bearing subset.
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
                # #1948 (pinned): the degraded-question fallback — a
                # DIFFERENT (binary) metric from the healthy-formula
                # turn/evidence above: "did the answer TURN surface in
                # top-k" (1.0 on 31/33 reval3-degraded questions), NOT
                # point-level recall. Identical to the healthy formula
                # only when evidence_point_count > 0.
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


# ══ PRODUCT-PARITY NOTE (eval-only) ══════════════════════════════════════
# This is a QUALITY knob that lives in the eval harness and is NOT wired
# into the product (tortoise/) path.
#   Product default: NO context builder exists — MCP/SDK consumers
#       (tortoise_search / tortoise_recall / hosted /v1/search) get raw
#       ranked lists from tortoise_fts_query; nothing assembles a
#       budget-capped, rank-interleaved reader context (#1745 landed
#       eval-side only, commit 6b36d5fe).
#   Why eval-only:   the context exists to feed the EVAL reader (also
#       eval-only — see the reader.py parity note). Shipping it to the
#       product = a NEW product feature (a context/answer surface, audit
#       G7 — the missing bridge between retrieval and any future
#       answer/agent-use surface).
#   Ship-to-product: open product decision — tracked under the reader
#       decision (hosted /v1/ask vs retrieval-only scope; audit G1/G7);
#       no separate tracking issue filed.
#   Rationale:       the harness exists to IMPROVE the product; the
#       rank-interleaved context is the candidate product context/answer
#       surface, not a harness invention.
# ═════════════════════════════════════════════════════════════════════════
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
                   leg_trace: list[dict] | None = None,
                   retrieval_budget_ms: int | None = None) -> list[dict]:
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

    ``retrieval_budget_ms`` (#1786 R5): the collective retrieval deadline
    (ms) threaded into ``tortoise_fts_query(_elevated_timeout_ms=...)``
    (PRIVATE benchmark-only seam — the production SDK default stays
    500 ms). The eval passes ``EVAL_RETRIEVAL_BUDGET_MS`` (1500); None
    keeps the SDK default byte-identical.

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
            # #1786 (R5): the eval's elevated hybrid-arm deadline via the
            # existing benchmark-only seam (SDK default 500 ms untouched).
            _elevated_timeout_ms=retrieval_budget_ms,
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
    # #1947: pool fetch depth for the baseline hybrid arm — explicit arg
    # > env ``TORTOISE_LME_POOL_SIZE`` > default ``DEFAULT_POOL_SIZE``
    # (120, up from R1's ``max(ks) * 3`` = 60); ``max(ks)`` is always the
    # floor (recall@k is computed over the deduped pool — a knob below the
    # deepest recall horizon cannot silently truncate the measured
    # surface). Rerank arms keep their own pool resolution (R6 D4).
    pool_size: int | None = None,
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
    # enables). ``evidence_boost_answer_string`` / ``evidence_boost_verbatim``
    # / ``evidence_boost_source`` override the per-class rank-offset
    # multipliers (env fallbacks
    # ``TORTOISE_LME_EVIDENCE_BOOST_ANSWER_STRING`` /
    # ``TORTOISE_LME_EVIDENCE_BOOST_VERBATIM`` /
    # ``TORTOISE_LME_EVIDENCE_BOOST_SOURCE``).
    evidence_boost: bool | None = None,
    evidence_boost_answer_string: float | None = None,
    evidence_boost_verbatim: float | None = None,
    evidence_boost_source: float | None = None,
    # #1786 (R5): the hybrid-arm collective retrieval deadline (ms) — the
    # eval passes EVAL_RETRIEVAL_BUDGET_MS (1500); None = SDK default
    # 500 ms. Threads ONLY the hybrid arm (``hybrid_search`` →
    # ``tortoise_fts_query``); the vector arm keeps VECTOR_TIMEOUT_MS.
    retrieval_budget_ms: int | None = None,
) -> dict[str, Any]:
    """Run retrieval for one question and compute recall@k + context stats.

    ``top_k`` is the design-locked context depth (default 20 — recall is
    reported at every k in ``ks``).
    #1947: candidates are fetched at the ``pool_size`` depth (default 120,
    knob ``TORTOISE_LME_POOL_SIZE`` — deepened from R1's ``max(ks)*3`` =
    60: reval3 showed 66% of marked evidence points never entered the
    60-item pool, so the C2 boost had no material; ``max(ks)`` is always
    the floor), the pool is deduped per-session (``max_chunks_per_session``
    raw chunks per session), recall@k is computed over the DEDUPED pool
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
    via read-time ``mark_for`` recompute;
    ``evidence_boost_answer_string`` / ``evidence_boost_verbatim`` /
    ``evidence_boost_source`` set the per-class multipliers (the #1763
    answer-string class — #1945 — carries the highest one). #1945: the
    outcome ALSO carries the honest answer-availability metric
    (``answer_string_evidence_recall@k`` — mark (d), over the effective
    pool) that report.py aggregates as
    ``retrieval.answer_string_evidence_recall@k``. Task 0
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
        # baseline / degraded / off — #1947: the deepened fetch depth
        # (default 120, knob TORTOISE_LME_POOL_SIZE; explicit arg wins);
        # max(ks) floor so the deepest recall horizon never measures a
        # truncated pool (recall@k is computed over the deduped pool).
        pool_limit = max(
            (pool_size if pool_size is not None
             else _env_int("TORTOISE_LME_POOL_SIZE", DEFAULT_POOL_SIZE)),
            max(ks))

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
        # #1786 (R5): the eval's elevated hybrid-arm deadline (None keeps
        # the SDK-default 500 ms collective cap byte-identical).
        retrieval_budget_ms=retrieval_budget_ms,
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
    # ── #1947: pool-depth diagnostic snapshot — captured pre-boost/pre-
    # rerank so marked-point membership reflects the FETCH depth (the C2
    # boost re-orders within the pool, never membership; rerank selection
    # truncates it). Emitted in the ``pool_depth`` outcome block. The D5
    # numerator (has_answer AND not raw chunk) matches evidence_recall@k;
    # marked chunks are the chunk-evidence view. ──
    depth_pool_size = len(pool)
    depth_marked_ranks = [
        i for i, h in enumerate(pool)
        if h["has_answer"] and not _is_raw_chunk(h)]
    depth_marked_chunk_ranks = [
        i for i, h in enumerate(pool) if h["has_answer"] and _is_raw_chunk(h)]

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
        bas = (evidence_boost_answer_string
               if evidence_boost_answer_string is not None
               else _env_boost_float("TORTOISE_LME_EVIDENCE_BOOST_ANSWER_STRING",
                                     DEFAULT_EVIDENCE_BOOST_ANSWER_STRING))
        bv = (evidence_boost_verbatim if evidence_boost_verbatim is not None
              else _env_boost_float("TORTOISE_LME_EVIDENCE_BOOST_VERBATIM",
                                    DEFAULT_EVIDENCE_BOOST_VERBATIM))
        bs = (evidence_boost_source if evidence_boost_source is not None
              else _env_boost_float("TORTOISE_LME_EVIDENCE_BOOST_SOURCE",
                                    DEFAULT_EVIDENCE_BOOST_SOURCE))
        pool, evidence_boost_stats = _apply_evidence_boost(
            pool, question=question, boost_answer_string=bas,
            boost_verbatim=bv, boost_source=bs)
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
    # shrinks). ── The query shapes live in the SHARED evidence-mark census
    # helper (retrieve.py, #1785 P2-7) so the retrieval path, the pre-
    # retrieval gate, the post-retrieval census, and the per-session census
    # can never drift on the D5 pointKind filter.
    ev_rows = evidence_mark_count(sdk._get_proj(), qid)
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

    # ── #1948: reader_surface@k — the honest "did the reader see the
    # evidence" measure. Fraction of evidence-bearing content (points AND
    # chunks — the D5 union of the evidence_point_count and
    # chunk_evidence_point_count denominators) present in the FULL reader
    # context (``context_points``, what ``_assemble_context`` actually
    # delivered, bounded by the context item cap) / evidence-bearing
    # content total. Distinct from the pool-based metrics: chunk@20
    # measures pool[:20], but the reader sees up to ``context_item_cap``
    # items — a marked chunk at pool rank 21+ that IS in the context
    # counts as read here while chunk_evidence_recall@20 = 0.0 (reval3:
    # 8550ddae's marked chunk at rank 31 was in context and answered
    # correctly). k-independent by construction (the context list is the
    # same for every k; the @k suffix keeps the report shape parallel);
    # N/A (None) on empty denominators (M6 #1526, mirroring
    # evidence_recall@k). ──
    # ══ PRODUCT-PARITY NOTE (eval-only) ══════════════════════════════════
    # #1948's reader_surface@k is a benchmark metric BY NATURE: it
    # measures what the eval's reader context delivered to the (eval-only)
    # reader. The product has no reader surface (see the reader.py parity
    # note), so there is nothing for this metric to measure in tortoise/ —
    # intentionally harness-only. No tracking issue (a metric is not a
    # product feature).
    # ═════════════════════════════════════════════════════════════════════
    reader_surface: dict[str, float | None] = {}
    reader_surface_denom = evidence_point_count + chunk_evidence_point_count
    ctx_evidence_ids = {h["id"] for h in context_points
                        if h["has_answer"]}
    for k in ks:
        reader_surface[str(k)] = (
            len(ctx_evidence_ids) / reader_surface_denom
            if reader_surface_denom else None)

    out = {
        "question_id": qid,
        "hits": pool,  # pinned contract: the deduped pool (R1 #1540)
        "session_recall@k": session_recall,
        "turn_recall@k": turn_recall,
        "evidence_recall@k": _evidence_recall,
        "chunk_evidence_recall@k": chunk_evidence_recall,
        # #1945: the honest answer-availability denominator — mark (d)
        # (#1763), gold-answer string contained in the point's
        # content/quote/search_keys, computed at eval time over the
        # EFFECTIVE pool (boosted when C2 is on — the same surface the
        # legacy evidence_recall@k measures, C2 placement). The legacy
        # source-session-inflated denominator (65% of the reval3 census)
        # measures "fraction of the answer session's points surfaced"; this
        # measures "fraction of answer-bearing points surfaced". N/A
        # (None) when no answer-string-marked point exists in the pool
        # (evidence.answer_string_recall_at_k semantics — the same seam
        # report.py aggregates as ``answer_string_evidence_recall@k``,
        # absent-key -> None, never fabricated).
        "answer_string_evidence_recall@k": {
            str(k): evidence.answer_string_recall_at_k(
                pool, str(question.get("answer") or ""), k)
            for k in ks},
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
        # #1948: the reader-surface metric — evidence-bearing content
        # (points AND chunks) in the FULL reader context / evidence-
        # bearing content total. The honest "did the reader see the
        # evidence" measure (chunk@20 undercounts the rank-(20, cap]
        # window; reader_evidence@k counts points only); k-independent
        # by construction, N/A on empty denominators.
        "reader_surface@k": reader_surface,
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
        # #1947: pool-depth diagnostic — the C2 evidence-mark boost is a
        # rank offset over the DEDUPED pool, so whether marked points can
        # enter the reader context is governed by pool DEPTH, not the
        # multiplier (reval3: 66% of marked points sat beyond the 60-item
        # pool → the boost moved 0/17 questions). Reports how many marked
        # points enter the pool at the deepened fetch depth, banded by
        # pool rank. The snapshot is pre-boost/pre-rerank by construction
        # (membership at fetch depth is the honest depth signal).
        "pool_depth": {
            "requested": pool_limit,
            "pool_size": depth_pool_size,
            "marked_points_total": evidence_point_count,
            "marked_points_in_pool": len(depth_marked_ranks),
            "marked_points_bands": _mark_bands(depth_marked_ranks),
            "marked_chunks_in_pool": len(depth_marked_chunk_ranks),
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


# ═══════════════════════════════════════════════════════════════════════════
# #1785 graph-integrity gate — shared gate-predicate callable
# ---------------------------------------------------------------------------
# The re-validation's 5 session@20=0.0 questions were graph-integrity
# artifacts: their answer sessions' Point nodes were ABSENT from the graph at
# retrieval time (pool_size : points_total ratio 0.005–0.196 vs exactly 1.000
# healthy), NOT retrieval misses (3/5 answered correctly in the pilot with
# intact graphs). The gate below makes truncation fail-loud at run time so no
# third aggregate can silently blend degraded outcomes. Plan:
# docs/plans/2026-08-27-1785-session-recall.md (Task 1).
#
# Tier resolution (plan §4A): hard-reject is PRESENCE-driven (primary,
# exact); the ratio is a secondary sub-1.0 truncation FLAG (any sub-1.0 ratio
# is a truncated graph — healthy is exactly 1.000; there is NO clean
# separation below 1.0); ratio > 1.0 is an integrity anomaly (``census_overflow``,
# fail-closed, never silently passed). The 0.25 ratio reject constant is
# RETRACTED (e47becba 0.026 and af8d2e46 0.148 were hits).
# ═══════════════════════════════════════════════════════════════════════════

# ── closed reason vocabulary (report.py + run_protocol.py consume these) ──
GATE_REASON_GRAPH_TRUNCATED = "graph_truncated"
GATE_REASON_ANSWER_SESSION_ABSENT = "answer_session_absent"
GATE_REASON_EVIDENCE_MARK_CENSUS = "evidence_mark_census"
GATE_REASON_CENSUS_ERROR = "census_error"
GATE_REASON_DATASET_JOIN_ERROR = "dataset_join_error"
GATE_REASON_CENSUS_OVERFLOW = "census_overflow"

#: Every reason key the gate can emit (also the vocabulary the resume-scan
#: refusal predicate and the report certifier consume).
GATE_REASONS: tuple[str, ...] = (
    GATE_REASON_GRAPH_TRUNCATED,
    GATE_REASON_ANSWER_SESSION_ABSENT,
    GATE_REASON_EVIDENCE_MARK_CENSUS,
    GATE_REASON_CENSUS_ERROR,
    GATE_REASON_DATASET_JOIN_ERROR,
    GATE_REASON_CENSUS_OVERFLOW,
)

#: Fail-closed classes — a hard census class vetoes through the report's
#: attempted-set grading (plan Task 1 Step 4 reason→grade mapping).
HARD_GATE_REASONS: tuple[str, ...] = (
    GATE_REASON_CENSUS_ERROR,
    GATE_REASON_DATASET_JOIN_ERROR,
    GATE_REASON_CENSUS_OVERFLOW,
)

#: Tracked-only classes — counted in n_gated, graded normally otherwise.
TRACKED_GATE_REASONS: tuple[str, ...] = (
    GATE_REASON_GRAPH_TRUNCATED,
    GATE_REASON_ANSWER_SESSION_ABSENT,
    GATE_REASON_EVIDENCE_MARK_CENSUS,
)


def _gate_env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _gate_env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# ── gate-config knobs (ALL fingerprint-excluded — a knob change must never
# alter resume-eligibility of pre-change checkpoints; plan Task 2) ──
#: Read-verify retry count N (fixed constant per plan Task 1 Step 2).
GATE_RETRY_N: int = _gate_env_int("TORTOISE_LME_GATE_RETRY_N", 2)
#: Per-session node-count floor tolerance T (evidence-bearing points).
GATE_FLOOR_T: int = _gate_env_int("TORTOISE_LME_GATE_FLOOR_T", 5)
#: Per-query latency allowance Q (ms) — the watchdog latency arm keys on
#: p95 > 2× Q across the last-10 window (SUCCESSFUL reads only).
GATE_QUERY_Q_MS: int = _gate_env_int("TORTOISE_LME_GATE_QUERY_Q", 100)
#: Per-query census timeout T_census (ms) — a census query exceeding this
#: yields ``census_error``, never a hang (enforced by the proxy deadline,
#: not the driver socket timeout).
GATE_TIMEOUT_MS: int = _gate_env_int("TORTOISE_LME_GATE_TIMEOUT_MS", 500)
#: Certifier / watchdog gated-fraction bound (shared knob, plan cycle2-P2-16).
GATE_MAX_GATED: float = _gate_env_float("TORTOISE_LME_GATE_MAX_GATED", 0.25)
#: Leg-deadness arm rolling window (questions).
GATE_LEG_DEAD_WINDOW: int = _gate_env_int("TORTOISE_LME_GATE_LEG_DEAD_WINDOW", 10)
#: Live-run-marker TTL (minutes) — a marker with no heartbeat within the TTL
#: is stale and auto-cleared with a warning (plan cycle4-P1-13).
GATE_MARKER_TTL_MIN: int = _gate_env_int("TORTOISE_LME_GATE_MARKER_TTL_MIN", 30)

#: D5 evidence-mark filter fragment — SINGLE-SOURCED so the shared helper,
#: the independent second read, and the raw third-shape probe can never
#: drift (plan P2-7). Excludes session-transcript raw chunks in BOTH ingest
#: modes (a naive ``has_answer=true`` count exceeds ``evidence_points`` on
#: healthy v2 questions because v2 transcript chunks carry
#: ``has_answer = contains_evidence``).
D5_POINTKIND_FILTER = "coalesce(p.pointKind, '') <> 'session-transcript'"

# ── fault-injection seam (plan P2-5) ───────────────────────────────────────
#: Test-only query wrapper around ``proj.g.query`` — unit/docker fault
#: scenarios (short-reads, timeouts, stalls) install a proxy here instead of
#: touching the real client. ``None`` = no injection (production path is
#: byte-identical). Every census query goes through :func:`_gate_query`.
_gate_fault_proxy: Callable | None = None


def install_gate_fault_proxy(proxy: Callable | None) -> None:
    """Install (or clear) the gate fault-injection proxy (test seam).

    The proxy signature is ``proxy(query_fn, cypher, params) -> result`` —
    it may call ``query_fn``, return a synthetic result_set list, raise, or
    block past T_census. ``None`` restores the production path.
    """
    global _gate_fault_proxy
    _gate_fault_proxy = proxy


def reset_gate_fault_proxy() -> None:
    install_gate_fault_proxy(None)


def _gate_query(proj: Any, cypher: str, params: dict | None = None) -> Any:
    if _gate_fault_proxy is not None:
        return _gate_fault_proxy(proj.g.query, cypher, params)
    return proj.g.query(cypher, params=params)


class _DeadlineTimedOut(RuntimeError):
    """A census query exceeded T_census inside the proxy deadline."""


class _DeadlineFaulted(RuntimeError):
    """A census query raised inside the proxy deadline thread."""


def _query_with_deadline(proj: Any, cypher: str, params: dict | None = None,
                         timeout_ms: int | None = None) -> list:
    """Run a census query bounded by its OWN deadline (T_census).

    The stateless single-shot ``proj.g.query`` driver has no timeout param
    (``socket_timeout=10`` is a backstop, not a budget mechanism) — a
    stalled server (the AOF-fsync-stall fault class) would block ~10 s per
    query. The proxy deadline converts an exceeded census query into
    ``_DeadlineTimedOut`` within the stated budget (plan cycle2-P1-7).
    """
    budget = timeout_ms if timeout_ms is not None else GATE_TIMEOUT_MS
    result: list = []
    error: BaseException | None = None
    done = threading.Event()

    def _run() -> None:
        nonlocal result, error
        try:
            rows = _gate_query(proj, cypher, params)
            result = list(rows.result_set)
        except BaseException as ex:
            error = ex
        finally:
            done.set()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    if not done.wait(budget / 1000.0):
        raise _DeadlineTimedOut(
            f"census query exceeded T_census={budget}ms")
    if error is not None:
        raise error  # type: ignore[misc]
    return result


# ── read-verify protocol (plan Task 1 Step 2) ───────────────────────────────
@dataclass
class CensusReads:
    """Two-read consensus outcome for one census."""
    value: Any          # agreed value (None on mismatch)
    status: str         # "consensus" | "mismatch"
    reads: dict         # shape → value per read
    retries: int        # retry count consumed


def _consensus_read(proj: Any, shapes: dict[str, Callable[[], list]], *,
                    retry_n: int | None = None,
                    timeout_ms: int | None = None,
                    label: str = "census") -> CensusReads:
    """Two independent-shape reads with retry-on-mismatch (read-verify).

    Both shapes run; a disagreement (different values, or any read faulted)
    retries the pair up to ``retry_n`` times. Only a stable agreement counts
    as a value. A persistent mismatch returns status="mismatch" (caller
    maps to ``census_error`` — NEVER a verdict, never pick-a-read; plan
    P2-2). The shapes MUST be genuinely different access paths (plan
    second-model P2: a syntax-level change does not qualify — a systematic
    server-side partial read poisons same-plan reads identically).
    """
    n = retry_n if retry_n is not None else GATE_RETRY_N
    last_reads: dict = {}
    for attempt in range(n + 1):
        reads: dict = {}
        faulted = False
        for name, fn in shapes.items():
            try:
                reads[name] = fn()
            except Exception:  # noqa: BLE001, RUF100
                reads[name] = None
                faulted = True
        last_reads = reads
        values = [v for v in reads.values() if v is not None]
        if not faulted and values and len(set(map(repr, values))) == 1:
            return CensusReads(value=values[0], status="consensus",
                               reads=reads, retries=attempt)
    return CensusReads(value=None, status="mismatch",
                       reads=last_reads, retries=n)


def _probe_raw(proj: Any, cypher: str, params: dict | None = None,
               timeout_ms: int | None = None) -> list:
    """Third-shape probe — a RAW query NOT via any shared helper (plan
    Task 1 Step 2: a discriminating probe confirms before anything is
    labeled absent/lost). Every query with the stateless single-shot
    client sees CURRENT state — a fresh session by construction (plan
    P1-I); the shim-only session-fault scenarios fabricate the fault via
    the injected proxy instead."""
    return _query_with_deadline(proj, cypher, params, timeout_ms=timeout_ms)


# ── folded pool_rows census (plan P2-2: presence folds into pool_rows) ─────
#: Single Cypher returning BOTH the unfiltered namespace count (the ratio
#: numerator) AND per-session membership for the mapped answer-session
#: indices (presence + lost-mark cross-check + per-session floor). The
#: membership is an OPTIONAL MATCH so an EMPTY membership still preserves
#: the namespace count (a truncated graph with zero answer-session points
#: must not collapse ns_count to 0).
FOLDED_POOL_ROWS_CYPHER = (
    "MATCH (p:Point {lme_question_id:$q}) "
    "WITH count(p) AS ns_count "
    "OPTIONAL MATCH (m:Point {lme_question_id:$q}) "
    "WHERE m.lme_session_index IN $idxs "
    "RETURN ns_count, m.lme_session_index AS si, "
    "coalesce(m.has_answer, false) AS has "
    "ORDER BY si"
)

#: Independent ratio second shape — a genuinely different ACCESS PATH (a
#: relationship traversal vs the label+property scan of the folded query),
#: per plan P1-5/second-model P2. Counts points reachable from Session nodes
#: via CONTAINS; turn/chunk/extracted points all carry CONTAINS edges in
#: both ingest modes (entities are Object nodes, events are Event nodes,
#: operator Points carry no ``lme_question_id`` — none match the label scan).
RATIO_SECOND_SHAPE_CYPHER = (
    "MATCH (s:Session {lme_question_id:$q})-[:CONTAINS]->(p:Point) "
    "RETURN count(DISTINCT p)"
)


def classify_ratio(pool_size, expected) -> str | None:
    """Pure ratio-tier classification (no reads) — the SAME classification
    the gate's read-verified ratio tier applies (plan §4A tier resolution):

      * healthy = exactly 1.000 → ``None`` (no reason; there is NO clean
        separation below 1.0 — any sub-1.0 ratio is a truncated graph);
      * sub-1.0 → ``graph_truncated`` (truncation FLAG — the reject tier
        is presence-driven, the 0.25 ratio constant is retracted);
      * >1.0 → ``census_overflow`` (integrity anomaly — census counting
        leftovers from a prior partial run; fail-closed, never silently
        passed);
      * expected <= 0 → ``census_error`` (a zero-point completed ingest is
        itself integrity suspicion; never ZeroDivisionError).

    Shared by the gate and the historical checkpoint ratio-tier replay
    (tests/test_graph_integrity_gate.py) so the replay tests the REAL
    classification function.
    """
    if expected is None or expected <= 0:
        return GATE_REASON_CENSUS_ERROR
    if pool_size is None:
        return None  # absent pool readout — read-verify layer handles it
    if pool_size < expected:
        return GATE_REASON_GRAPH_TRUNCATED
    if pool_size > expected:
        return GATE_REASON_CENSUS_OVERFLOW
    return None


def folded_pool_rows(proj: Any, qid: str, idxs: list[int]) -> dict:
    """Run the folded pool_rows census — namespace count + per-session
    membership for the mapped answer-session indices (plan P2-2/P2-5).
    Returns ``{"ns_count": int, "members": [(si, has_answer), ...]}`` —
    computed on the UNFILTERED namespace set regardless of any
    retrieval-side filters pool_rows carries. ``idxs == []`` (abstention
    exemption) returns just the namespace count (membership trivially
    empty). Raises on query failure (caller maps to ``census_error``).
    """
    rows = _query_with_deadline(
        proj, FOLDED_POOL_ROWS_CYPHER,
        params={"q": qid, "idxs": list(idxs)})
    if not rows:
        return {"ns_count": 0, "members": []}
    ns_count = rows[0][0]
    members = [(int(r[1]), bool(r[2])) for r in rows if r[1] is not None]
    return {"ns_count": ns_count, "members": members}


def ratio_second_read(proj: Any, qid: str) -> int:
    """Independent ratio second shape — the CONTAINS traversal count."""
    rows = _query_with_deadline(
        proj, RATIO_SECOND_SHAPE_CYPHER, params={"q": qid})
    return rows[0][0] if rows else 0


# ── dataset-join resolution (plan Task 1 Step 2) ────────────────────────────
def resolve_answer_session_indices(question: dict) -> tuple[list[int], str | None]:
    """Resolve the mapped answer-session indices for a question.

    ``answer_session_ids`` (dataset source-session id strings) → positions
    within ``haystack_session_ids``. Returns ``(indices, None)`` on success
    (``[]`` for the abstention exemption — EMPTY ``answer_session_ids``),
    or ``(None, GATE_REASON_DATASET_JOIN_ERROR)`` on a fail-closed join
    failure: an answer id absent from ``haystack_session_ids``, an
    out-of-range index, a duplicated source-session id (uniqueness
    unestablishable — never silent first-occurrence resolution), or an
    empty ``haystack_session_ids``. ``answer_session_ids=None`` or a
    key-absent field is NOT the abstention path — it fails closed (plan
    P2-11: None/key-absent must not silently skip the presence check).
    """
    answer_ids = question.get("answer_session_ids")
    if answer_ids is None:
        return None, GATE_REASON_DATASET_JOIN_ERROR
    if not isinstance(answer_ids, list) or not all(
            isinstance(a, str) for a in answer_ids):
        return None, GATE_REASON_DATASET_JOIN_ERROR
    if not answer_ids:
        return [], None  # abstention exemption (empty ids only)
    haystack = question.get("haystack_session_ids")
    if not isinstance(haystack, list) or not haystack:
        return None, GATE_REASON_DATASET_JOIN_ERROR
    # duplicate source-session id → uniqueness unestablishable
    seen: set[str] = set()
    for h in haystack:
        if not isinstance(h, str):
            return None, GATE_REASON_DATASET_JOIN_ERROR
        if h in seen:
            return None, GATE_REASON_DATASET_JOIN_ERROR
        seen.add(h)
    position = {sid: i for i, sid in enumerate(haystack)}
    indices: list[int] = []
    for aid in answer_ids:
        if aid not in position:
            return None, GATE_REASON_DATASET_JOIN_ERROR
        idx = position[aid]
        if idx >= len(question.get("haystack_sessions") or []):
            return None, GATE_REASON_DATASET_JOIN_ERROR
        indices.append(idx)
    return sorted(set(indices)), None


# ── evidence-mark census (extracted D5 shared helper, plan P1-1/P2-7) ──────
def evidence_mark_count(proj: Any, qid: str, *,
                        created_point_ids: list[str] | None = None,
                        per_session: bool = False,
                        timeout_ms: int | None = None) -> list:
    """D5 evidence-mark census — pointKind-filtered ``has_answer`` count.

    Scoped WRITE-OBSERVED to ``created_point_ids`` (the ids this run's
    ``_write_payload`` actually created — plan P1-1) when supplied, else
    namespace-wide (the ``evidence_turns``-denominator fallback for ingest
    paths without created-ids exposure). ``per_session=True`` groups by
    ``lme_session_index`` (the per-session floor + Task 3's census share
    this shape — the floor adds NO first reads, plan cycle2-P2-19).
    ``timeout_ms`` routes through the T_census deadline wrapper (gate
    callers); the retrieval path leaves it None for byte-identical
    transport (the shared requirement is the QUERY SHAPE — the single-
    sourced D5_POINTKIND_FILTER fragment — not the transport, plan P2-7).

    Returns a list of rows: ``[[count]]`` (flat) or ``[[si, count], ...]``
    (per-session).
    """
    if created_point_ids is not None:
        base = (
            "MATCH (p:Point) WHERE p.id IN $ids "
            f"AND {D5_POINTKIND_FILTER} AND p.has_answer = true"
        )
        params: dict = {"ids": list(created_point_ids)}
    else:
        base = (
            "MATCH (p:Point) WHERE p.lme_question_id = $q "
            f"AND {D5_POINTKIND_FILTER} AND p.has_answer = true"
        )
        params = {"q": qid}
    if per_session:
        # RedisGraph/FalkorDB GROUP BY is IMPLICIT (non-aggregated return
        # columns group) — an explicit ``GROUP BY si`` alias clause is a
        # syntax error.
        cypher = base + " RETURN p.lme_session_index, count(*)"
    else:
        cypher = base + " RETURN count(*)"
    if timeout_ms is not None:
        return _query_with_deadline(proj, cypher, params=params,
                                    timeout_ms=timeout_ms)
    return _gate_query(proj, cypher, params=params).result_set


# ── the shared gate-predicate callable ─────────────────────────────────────
# ══ PRODUCT-PARITY NOTE (eval-only) ══════════════════════════════════════
# This is a QUALITY knob that lives in the eval harness and is NOT wired
# into the product (tortoise/) path.
#   Product default: a MANUAL equivalent only — MCP tortoise_check_structure
#       (tortoise/mcp_server.py:793) + chain_enforcer.validate_chains
#       (warn-only residual backstop, tortoise/chain_enforcer.py:51); not
#       auto-wired into the capture path.
#   Why eval-only:   #1785's gate runs per question to keep a truncated/
#       degraded graph from certifying benchmark numbers (census read-
#       verify protocol + fail-closed reasons). Landed eval-side only
#       (commit 1864d4fd touched only tools/longmem_eval/).
#   Ship-to-product: candidate feature — auto-wiring integrity checks
#       into the product capture path is unassigned; no tracking issue
#       filed.
#   Rationale:       the harness exists to IMPROVE the product; automatic
#       graph-integrity gating is a candidate product feature, not a
#       harness invention.
# ═════════════════════════════════════════════════════════════════════════
def run_integrity_gate(
    proj: Any, question: dict, qid: str, *,
    ingest_stats: dict | None = None,
    pool_result: dict | None = None,
    retrieval_only: bool = False,
    resumed: bool = False,
    retry_n: int | None = None,
    floor_t: int | None = None,
    timeout_ms: int | None = None,
) -> dict:
    """Evaluate the graph-integrity gate for one question (plan Task 1).

    Returns ``{"reasons": [...], "ratio": float|None, "expected": int|None,
    "pool_size": int|None, "members": [(si, has_answer)], "census": {...}}``.
    ``reasons == []`` = gate green. Every census runs the read-verify
    protocol (two independent shapes + retry + third-shape probe on
    absence); a persistent fault maps to ``census_error`` (fail CLOSED —
    excluded from aggregates, never a verdict, never a crash). The gate
    FLAGS, never skips retrieval (a red question still runs
    ``retrieve_for_question`` — the run site owns that contract).

    Tier resolution:
      * ``retrieval_only=True`` → no tiers (breaker-open / vector-arm /
        full-context: no ingest-stats surface; plan P1-4).
      * extraction-error fold (plan cycle3-P1-11): ingest stats recording a
        session extraction exception → NO integrity reasons (the question
        is already invalid via ``n_ingest_errors``/``error_census`` — the
        error attribution is the true cause; NEVER ``census_overflow`` on
        the corrupted denominator, never a bare ``answer_session_absent``).
      * ratio: sub-1.0 → ``graph_truncated`` (flag); >1.0 →
        ``census_overflow`` (fail-closed anomaly); suppressed on resume
        (leftover nodes from a prior partial run are expected — presence is
        primary). expected == 0 with a completed ingest → ``census_error``
        (a zero-point completed ingest is itself integrity suspicion;
        distinct from the ``retrieval_only`` exemption by the ABSENCE of
        the flag — never ZeroDivisionError).
      * presence: red when ANY mapped answer-session index has zero points
        (multi-session red-on-any); skipped for the abstention exemption
        (EMPTY ``answer_session_ids`` only); join failure →
        ``dataset_join_error`` (fail-closed, never a ValueError, never
        silently matching nothing).
      * per-session floor: derived at gate time from the write-path
        per-session evidence-point stat (``ingest_stats["per_session_"
        "evidence_points"]`` — the ONE floor source, plan §11 decision 5);
        floor = max(1, expected − T). Red-on-any. Absent stat → tier not
        applicable. A present session with expected 0 evidence stays GREEN
        (P2-11 — marks live entirely on transcript chunks or a no-evidence
        extractor must not silently gate-red via floor max(1, 0−T) = 1).
      * evidence-mark census: write-observed count of ``has_answer`` among
        ``created_point_ids`` (loss-only red: count < expected; inflation
        from OR-in / NOOP-fold / within-run collisions is diagnostic-only,
        never red — plan P2-1); fallback to the namespace-wide
        pointKind-filtered count vs ``evidence_points`` (v2) or
        ``evidence_turns`` (legacy) when created ids are absent; the
        lost-mark cross-check (marks present among a mapped answer
        session's points regardless of creation run) red-flags a
        mark-stripped session even when both created-id counts are 0
        (plan P1-4); the client-side created-id-set anchor fires
        ``census_error`` on a shape-independent short consensus (cycle3-
        P2-31: ns_count < len(created_point_ids) is provably faulted).
    """
    reasons: list[str] = []
    stats = ingest_stats or {}
    n = retry_n if retry_n is not None else GATE_RETRY_N
    t = floor_t if floor_t is not None else GATE_FLOOR_T
    budget = timeout_ms if timeout_ms is not None else GATE_TIMEOUT_MS
    result: dict = {"reasons": reasons, "ratio": None, "expected": None,
                    "pool_size": None, "members": [],
                    "census": {"reads": 0, "read_latency_ms": 0.0}}
    if retrieval_only:
        return result

    # ── extraction-error fold (true cause first) ──
    if stats.get("errors") or stats.get("error_census"):
        result["census"]["error_fold"] = True
        return result

    # ── read-verify latency accounting (plan P1-2/P2-7) ──
    reads_done = 0
    reads_latency_ms = 0.0
    _t_reads = time.monotonic()

    def _note_reads(n: int) -> None:
        nonlocal reads_done, reads_latency_ms
        reads_done += n
        reads_latency_ms = (time.monotonic() - _t_reads) * 1000.0

    # ── expected denominator (both stats shapes) ──
    if "points" in stats:
        expected = (stats.get("turns", 0) + stats.get("chunks", 0)
                    + stats.get("points", 0))
    else:
        # legacy ingest_haystack: only turn Points + session-transcript
        # chunks exist; evidence_points is a SUBSET of turn Points (never
        # an additional node class) — adding it would double-count and
        # push every healthy legacy ratio < 1.0 (plan P1-1).
        expected = stats.get("turns", 0) + stats.get("chunks", 0)
    result["expected"] = expected
    if expected == 0:
        reasons.append(GATE_REASON_CENSUS_ERROR)
        return result

    # ── dataset-join resolution + folded pool_rows census ──
    idxs, join_error = resolve_answer_session_indices(question)
    if join_error is not None:
        reasons.append(join_error)
    try:
        pool = (pool_result if pool_result is not None
                else folded_pool_rows(proj, qid, idxs or []))
    except Exception:  # noqa: BLE001, RUF100
        reasons.append(GATE_REASON_CENSUS_ERROR)
        return result
    _note_reads(1)
    ns_count = int(pool.get("ns_count") or 0)
    members: list[tuple[int, bool]] = [
        (int(si), bool(has)) for si, has in pool.get("members") or []]
    result["pool_size"] = ns_count
    result["members"] = members
    per_session_counts: dict[int, int] = {}
    per_session_marks: dict[int, int] = {}
    for si, has in members:
        per_session_counts[si] = per_session_counts.get(si, 0) + 1
        if has:
            per_session_marks[si] = per_session_marks.get(si, 0) + 1
    #: per-session evidence stat — the ONE floor source (plan §11 decision 5)
    floor_stats = stats.get("per_session_evidence_points")

    # ── ratio tier (read-verified; suppressed on resume) ──
    if not resumed and join_error is None:
        ratio = ns_count / expected
        result["ratio"] = ratio
        # read-verify: read1 = the folded label scan (already in hand),
        # read2 = the independent CONTAINS traversal access path.
        reads = _consensus_read(
            proj, {
                "label_scan": lambda: ns_count,
                "traversal": lambda: ratio_second_read(proj, qid),
            }, retry_n=n, timeout_ms=budget, label="ratio")
        _note_reads(len(reads.reads))
        if reads.status != "consensus":
            reasons.append(GATE_REASON_CENSUS_ERROR)
        else:
            agreed = reads.value
            # shape-independent-truncation anchor (cycle3-P2-31): the
            # client-known created-id set size never passes through the
            # server cursor — a consensus namespace count SHORTER than it
            # is provably faulted (the namespace must contain the created
            # ids), fail-closed to census_error, never a phantom flag.
            created_ids = stats.get("created_point_ids")
            if (isinstance(created_ids, list)
                    and agreed < len(created_ids)):
                reasons.append(GATE_REASON_CENSUS_ERROR)
                return result
            # wrong-count disagreement (cycle2-P2-15): the third-shape
            # probe fires when the consensus count disagrees with the
            # CLIENT-KNOWN expectation (agreed != expected — a healthy
            # consensus needs no probe; base-10 budget preserved). A
            # fresh-session probe contradicting the consensus means the
            # reads were faulted — census_error, never a verdict.
            _ratio_reason = classify_ratio(agreed, expected)
            if _ratio_reason is not None:
                try:
                    probe_rows = _probe_raw(
                        proj,
                        "MATCH (p:Point {lme_question_id:$q}) RETURN count(*)",
                        params={"q": qid}, timeout_ms=budget)
                    probe_count = probe_rows[0][0] if probe_rows else 0
                except Exception:  # noqa: BLE001, RUF100
                    reasons.append(GATE_REASON_CENSUS_ERROR)
                    return result
                _note_reads(1)
                if probe_count != agreed:
                    reasons.append(GATE_REASON_CENSUS_ERROR)
                    return result
                reasons.append(_ratio_reason)

    # ── presence tier + per-session floor ──
    if join_error is None and idxs:
        pres = _presence_consensus(
            proj, qid, idxs, per_session_counts,
            retry_n=n, timeout_ms=budget)
        _note_reads(len(pres.reads))
        if pres.status != "consensus":
            reasons.append(GATE_REASON_CENSUS_ERROR)
        else:
            observed = pres.value
            confirmed_missing = [si for si in idxs
                                 if observed.get(si, 0) == 0]
            for si in confirmed_missing:
                # absence confirmation: raw third-shape probe on a fresh
                # query; a probe finding the session means the consensus
                # reads were faulted.
                try:
                    probe_rows = _probe_raw(
                        proj,
                        "MATCH (p:Point {lme_question_id:$q, "
                        "lme_session_index:$si}) RETURN count(*)",
                        params={"q": qid, "si": si}, timeout_ms=budget)
                    probe_n = probe_rows[0][0] if probe_rows else 0
                except Exception:  # noqa: BLE001, RUF100
                    reasons.append(GATE_REASON_CENSUS_ERROR)
                    break
                if probe_n > 0:
                    reasons.append(GATE_REASON_CENSUS_ERROR)
                    break
            if confirmed_missing and GATE_REASON_CENSUS_ERROR not in reasons:
                reasons.append(GATE_REASON_ANSWER_SESSION_ABSENT)
            # per-session node-count floor (write-path stat = ONE source)
            if (isinstance(floor_stats, dict) and floor_stats
                    and GATE_REASON_ANSWER_SESSION_ABSENT not in reasons
                    and GATE_REASON_CENSUS_ERROR not in reasons):
                for si in idxs:
                    exp_ev = floor_stats.get(str(si))
                    if not isinstance(exp_ev, int):
                        continue
                    if exp_ev == 0:
                        continue  # P2-11: present + zero expected → green
                    floor = max(1, exp_ev - t)
                    if per_session_counts.get(si, 0) == 0:
                        continue  # already red via presence
                    if per_session_marks.get(si, 0) < floor:
                        reasons.append(GATE_REASON_EVIDENCE_MARK_CENSUS)

    # ── evidence-mark census (write-observed; loss-only red) ──
    if join_error is None:
        created_ids = stats.get("created_point_ids")
        if isinstance(created_ids, list):
            expected_ev = stats.get("evidence_points", 0)
            ids_param = created_ids
        else:
            ids_param = None
            expected_ev = (stats.get("evidence_points", 0)
                           if "points" in stats
                           else stats.get("evidence_turns", 0))
        try:
            def _flat() -> int:
                rows = evidence_mark_count(proj, qid, created_point_ids=ids_param,
                                           timeout_ms=budget)
                return rows[0][0] if rows else 0

            def _per_session_sum() -> int:
                rows = evidence_mark_count(
                    proj, qid, created_point_ids=ids_param, per_session=True,
                    timeout_ms=budget)
                return sum(int(r[1]) for r in rows) if rows else 0

            reads = _consensus_read(
                proj, {"flat": _flat, "per_session_sum": _per_session_sum},
                retry_n=n, timeout_ms=budget, label="evidence_mark")
        except Exception:  # noqa: BLE001, RUF100
            reads = CensusReads(value=None, status="mismatch",
                                reads={}, retries=n)
        _note_reads(len(reads.reads))
        if reads.status != "consensus":
            reasons.append(GATE_REASON_CENSUS_ERROR)
        else:
            census_count = reads.value
            if census_count < expected_ev:
                reasons.append(GATE_REASON_EVIDENCE_MARK_CENSUS)
            # lost-mark cross-check (plan P1-4): a mapped answer session
            # with ≥1 point but ZERO marks while the write-path stat claims
            # evidence red-flags a mark-stripped session (H6 attribution)
            # even when both created-id counts are 0.
            if (isinstance(floor_stats, dict) and idxs
                    and GATE_REASON_ANSWER_SESSION_ABSENT not in reasons
                    and GATE_REASON_CENSUS_ERROR not in reasons):
                for si in idxs:
                    ev_stat = floor_stats.get(str(si))
                    if (isinstance(ev_stat, int) and ev_stat > 0
                            and per_session_counts.get(si, 0) > 0
                            and per_session_marks.get(si, 0) == 0):
                        reasons.append(GATE_REASON_EVIDENCE_MARK_CENSUS)
                        break
    result["census"]["reads"] = reads_done
    result["census"]["read_latency_ms"] = round(reads_latency_ms, 2)
    return result


def _presence_consensus(proj: Any, qid: str, idxs: list[int],
                        folded_counts: dict[int, int], *, retry_n: int,
                        timeout_ms: int) -> CensusReads:
    """Presence read-verify: read1 = the folded label-scan membership
    (already in hand), read2 = the independent per-index CONTAINS
    traversal count (different access path). Retry on mismatch; only a
    stable two-read agreement counts as presence data."""
    def _traversal() -> dict:
        out: dict = {}
        for si in idxs:
            rows = _query_with_deadline(
                proj,
                "MATCH (s:Session {lme_question_id:$q, "
                "lme_session_index:$si})-[:CONTAINS]->(p:Point) "
                "WHERE p.lme_session_index = $si "
                "RETURN count(p)",
                params={"q": qid, "si": si}, timeout_ms=timeout_ms)
            out[si] = rows[0][0] if rows else 0
        return out

    read1 = {si: folded_counts.get(si, 0) for si in idxs}
    n = retry_n
    for attempt in range(n + 1):
        try:
            read2 = _traversal()
        except Exception:  # noqa: BLE001, RUF100
            read2 = None
        if read2 is not None and read1 == read2:
            return CensusReads(value=read1, status="consensus",
                               reads={"label_scan": read1, "traversal": read2},
                               retries=attempt)
    return CensusReads(value=None, status="mismatch",
                       reads={"label_scan": read1},
                       retries=n)
