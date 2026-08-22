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
    points-first context handed to the reader (whitespace tokens + 10%
    markup allowance; the estimator is recorded in report provenance),
  * retrieval latency ms.

R1 #1540 (epic #1509): candidates are fetched at ``max(ks) * 3`` depth
(pool-depth headroom so a monopolizing session's points cannot crowd other
sessions out BEFORE dedup runs), the pool is deduped per-session
(``max_chunks_per_session`` raw chunks per session, rank order — E2E-1),
and ``_assemble_context`` builds the budget-capped, points-first context
(UX decision 3): extracted points render in rank order, raw chunks
backfill the remaining ``context_token_cap`` tokens. Recall@k is computed
over the DEDUPED pool (``ret["hits"]`` == the pool — the retrieval
contract), so the metrics reflect what the reader could actually see.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from tortoise.sdk import TortoiseSDK

from .ingest import (EXTRACTION_POINT_KIND, event_props_for_hits,
                     point_props_for_hits)

# token-count estimator: rough LLM token ≈ whitespace tokens, plus markup
# allowance for role prefixes/JSON. Documented in report provenance.
_TOKEN_ESTIMATOR = "whitespace-tokens + 10% markup allowance"

#: R1 (#1540): per-session raw-chunk cap in the pool (E2E-1; the R6 MMR
#: variant tunes it post-baseline).
DEFAULT_MAX_CHUNKS_PER_SESSION = 2
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
                      question_date: str | None = None) -> list[dict]:
    """Budget-capped, points-first context (UX decision 3 #1540): extracted
    points render in rank order, then raw chunks backfill the remaining
    token budget, over at most ``top_k`` pool items (top_k stays "the max
    number of context items"; the token budget bounds it further).

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
    points = [h for h in pool if not _is_raw_chunk(h)]
    chunks = [h for h in pool if _is_raw_chunk(h)]
    header_words = (len(f"Current Date: {question_date}".split())
                    if question_date else 0)
    selected: list[dict] = []
    words = header_words
    for h in (points + chunks)[:top_k]:
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


def retrieve_for_question(
    sdk: TortoiseSDK,
    question: dict,
    *,
    ks: tuple[int, ...] = (5, 10, 20),
    top_k: int = 20,
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
) -> dict[str, Any]:
    """Run retrieval for one question and compute recall@k + context stats.

    ``top_k`` is the max context size handed to the reader (default 20 —
    the design-locked depth; recall is reported at every k in ``ks``).
    R1 #1540: candidates are fetched at ``max(ks) * DEFAULT_POOL_MULTIPLIER``
    depth, the pool is deduped per-session (``max_chunks_per_session`` raw
    chunks per session), recall@k is computed over the DEDUPED pool
    (``ret["hits"]`` == the pool — pinned contract), and the reader's
    context is the budget-capped points-first ``_assemble_context`` output
    (``context_points``, bounded by ``max_context_tokens``).

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
    is_tr = question.get("question_type") == "temporal-reasoning"
    effective_top_k = tr_top_k if is_tr else top_k
    answer_sessions = set(question.get("answer_session_ids") or [])
    dates: list[str] = question.get("haystack_dates") or []
    evidence_turn_ids = {
        f"lme:{qid}:s{si}:t{ti}"
        for si, session in enumerate(question.get("haystack_sessions") or [])
        for ti, turn in enumerate(session)
        if turn.get("has_answer")
    }

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
        limit=max(ks) * DEFAULT_POOL_MULTIPLIER,
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

    # ── recall@k over the DEDUPED pool (session + turn + evidence + chunk) ──
    session_recall: dict[str, float] = {}
    turn_recall: dict[str, float] = {}
    _evidence_recall: dict[str, float | None] = {}
    chunk_evidence_recall: dict[str, float | None] = {}
    for k in ks:
        top = pool[:k]
        if answer_sessions:
            retrieved_sessions = {h["session_id"] for h in top if h["session_id"]}
            session_recall[str(k)] = (
                len(answer_sessions & retrieved_sessions) / len(answer_sessions))
        else:
            session_recall[str(k)] = 0.0
        # turn/evidence numerators exclude raw chunks (D5) — otherwise
        # marked chunks in top-k inflate turn_recall beyond 1.0 against the
        # points-only denominator (2 marked chunks + 1 marked point vs
        # denominator 1).
        ev_hits = {h["id"] for h in top
                   if h["has_answer"] and not _is_raw_chunk(h)}
        if evidence_point_count:
            # v2 leg: did the extracted point CONTAINING the answer surface?
            turn_recall[str(k)] = len(ev_hits) / evidence_point_count
            _evidence_recall[str(k)] = len(ev_hits) / evidence_point_count
        else:
            # M6 (#1526) N/A-not-0.0: an empty denominator is None, never a
            # forced 0.0 — "no evidence exists" must stay distinguishable
            # from "evidence exists but never surfaces" (#1369).
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

    # ── context handed to the reader (D4: budget-capped, points first) ──
    question_date = question.get("question_date", "") or None
    context_points = _assemble_context(
        pool, top_k=effective_top_k,
        max_context_tokens=max_context_tokens,
        question_date=question_date)
    # R5 (D6): TR context renders time-ascending — after truncation the
    # context list is stable-sorted by session_date (dated first, undated
    # last, stable within a date = retrieval order preserved). Recall
    # metrics keep retrieval order: only the READER's context list is
    # reordered. Non-TR keeps RRF order (byte-identical to today).
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

    return {
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
        # retrieved" (evidence-written/retrieved accounting).
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
            "pool_depth_requested": max(ks) * DEFAULT_POOL_MULTIPLIER,
        },
        "retrieval_latency_ms": round(latency_ms, 2),
    }
