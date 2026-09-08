"""#2521 (C5 #2513) — aggregative-intent detection + per-facet coverage check.

The multi-session evidence-surface program's measured loss is PARTIAL
evidence: one-shot top-k retrieval surfaces k < N of the N evidence pieces
when an answer spans sessions (docs/scoping/2026-09-07-2513-multisession-
evidence-surface.md §1 — 53% of the 100-Q slice's misses). #2518 (entity/
fact-augmented key expansion) raises how many subject-matching sessions
*enter the pool*; it neither detects the AGGREGATIVE query class ("how
much/how many/total across sessions") nor checks per-facet completeness.
This module is #2521: the deterministic detector + the facet-based coverage
check the #2519 loop (C3-3 routing) will consume.

The design/coverage-feedback decision (made — do NOT revisit):
entity-anchored index enumerates all facts about subject X; the detector
recognizes the aggregative surface; facet enumeration feeds a per-facet
coverage check; the coverage-incomplete signal fires ONLY on entity-scoped
aggregations where the index is exhaustive; open-ended questions are NEVER
flagged; there is no general completeness oracle (a corpus-wide "do I have
all memories" question has no enumerable N and must never claim one).

Layering (product-first — the eval stays a thin measuring caller):

  1. ``detect_aggregative_intent`` — hermetic, deterministic, rule-based
     classifier over the query surface (counting/quantifier patterns).
     No graph, no model, no IO: the classification table is unit-testable
     offline.
  2. ``facet_key_for_point`` + ``compute_facet_coverage`` — hermetic
     facet-key extraction + the pure k-of-N coverage math over a facet
     census and the current retrieval's points. Single source of truth so
     the graph fixture tests and the eval run the identical logic offline.
  3. ``collect_anchor_census`` + ``aggregative_verdict`` — the entity-
     anchor-spine adapter + one-call seam. Anchor resolution goes through
     the SAME surface #2518 uses (bounded FTS over the Object-name index;
     linked Points via the ``aboutObject`` join) so the two levers can
     never disagree on what "about subject X" means. Fail-open on every
     graph fetch (never turns a working retrieval into a broken one).

Scope gating (the never-flag rule, double-gated):

  * text gate — a query whose aggregation subject is the memory corpus
    itself ("how many memories", "how much have we talked") is
    ``scope="open-ended"`` and the coverage path is skipped entirely;
  * index gate — an "entity-scoped" surface must actually RESOLVE an
    Object anchor through the name index. No anchor resolved ⇒ the index
    holds no exhaustive enumeration for this subject ⇒ no coverage claim
    (signal ``none``), regardless of how aggregative the wording is.
    There is deliberately no third "general oracle": a scoped-looking
    query that names no stored entity simply never fires.

Facet semantics: a facet is one axis of the N evidence pieces the anchor's
linked points enumerate. Dimension ``session`` (the default and the only
wired v1 dimension — the measured cross-session miss class) groups by the
point's ``session_id``; ``kind`` groups by point kind; ``date_month``
groups by the point's date prop truncated to ``YYYY-MM``. The coverage
verdict emits ``signal`` ∈ {``none``, ``partial``, ``complete``}: none
when no exhaustive N is enumerable (fail-open), partial when k < N, and
complete when every known facet has a representative in the retrieval.
Bounded: at most ``MAX_FACETS`` distinct facets are enumerated; a census
that overflows the bound is NOT exhaustive and degrades to ``none``
(capped=True) — the all-pieces claim must never ride a truncated N.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

_logger = logging.getLogger(__name__)

#: Max Object-spine anchors resolved from the query text — identical to
#: #2518's ``_ENTITY_ANCHOR_LIMIT`` (the same seam must bound the same way;
#: a larger window would harvest noise keys from co-matching entities).
DEFAULT_MAX_ANCHORS = 3
#: Wider DB-side candidate fetch for anchor resolution (mirrors #2518's
#: ``_ENTITY_ANCHOR_CANDIDATES`` — ``run_fts_query`` applies its LIMIT in
#: Cypher BEFORE the Python re-sort, so a bare top-N fetch would cut
#: equal-score ties DB-side in arbitrary order).
_ANCHOR_CANDIDATES = 20
#: Bound on linked Points enumerated per anchor. The check's all-pieces
#: semantics only hold when the census is exhaustive — a per-anchor
#: overflow makes the verdict fail open (never a truncated claim).
MAX_POINTS_PER_ANCHOR = 200
#: Bound on the number of DISTINCT known facets. N is what the
#: "k of N known facets" honesty signal reports; an N beyond this cap
#: cannot be asserted exhaustive, so the verdict degrades to ``none``
#: (capped=True) instead of publishing a truncated N.
MAX_FACETS = 200
#: Bound on the reported missing-facet list (the reader/loop only needs a
#: bounded re-query target — full enumeration is the census's job).
MAX_MISSING_REPORTED = 20

# ── Facet dimensions ────────────────────────────────────────────────────────
#: The facet axes the spine supports for grouping an anchor's linked
#: points. ``session`` is the wired v1 dimension (the measured cross-
#: session miss class #2513 targets); ``kind``/``date_month`` are pure-
#: function supported and left for C3-3 to wire (the loop's date-range
#: facets will ride the R5 time-constraint seam).
FACET_DIMENSIONS: tuple[str, ...] = ("session", "kind", "date_month")
DEFAULT_FACET_DIMENSION = "session"

# ── Detector vocabulary ─────────────────────────────────────────────────────
#: Quantifier patterns, ordered by precedence (the FIRST match wins — a
#: question like "how much … in total" is a how-much amount aggregation,
#: not the generic total marker). Each entry: (quantifier_id, regex).
#: The surface set is pinned by the #2521 classification table test.
AGGREGATIVE_QUANTIFIERS: tuple[tuple[str, str], ...] = (
    # frequency aggregation — "how often do I …" counts occurrences
    ("how_often", r"\bhow\s+(?:often|frequently)\b"),
    # counting aggregation — "how many X" (times, sessions, instances)
    ("how_many", r"\bhow\s+many\b"),
    # amount aggregation — "how much did X cost"
    ("how_much", r"\bhow\s+much\b"),
    # explicit count-of/number-of phrasing — checked BEFORE the generic
    # total marker so "total number of bikes" classifies as a count
    # ("count_of"), not as the amount marker
    ("count_of", r"\b(?:count|number)\s+of\b"),
    # summed-amount markers (post-posed "in total" or pre-posed "total X")
    ("total", r"\b(?:in\s+total|altogether|combined|in\s+all|sum\s+of)\b"
              r"|\btotal\b"),
    # universal-quantifier enumeration — "every X" / "each X" occurrences
    ("every", r"\b(?:every|each)\b"),
)
#: Compiled form of :data:`AGGREGATIVE_QUANTIFIERS` (built once at import —
#: module import stays IO-free and deterministic).
_QUANTIFIER_RES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (qid, re.compile(pattern)) for qid, pattern in AGGREGATIVE_QUANTIFIERS
)

#: Elapsed-time shapes are TEMPORAL-REASONING (R5 #1544 territory — the
#: R5 detector's ``ordering`` class), NOT counting aggregation: "how many
#: days ago did I last run" asks a single date, and a k-of-N facet check
#: over "days" would be meaningless. Excluded before the quantifier scan.
_ELAPSED_TIME_RE = re.compile(
    r"\bhow\s+many\s+(?:days?|weeks?|months?|years?)\s+ago\b"
    r"|\b\d+\s+(?:days?|weeks?|months?|years?)\s+ago\b"
)
#: "how long" is excluded outright: it asks a duration/span (single fact,
#: or R5/C6's temporal-ordering class), never a sum/count across facets.
_HOW_LONG_RE = re.compile(r"\bhow\s+long\b")

#: Self-corpus language — a query whose aggregation subject is the stored
#: memory corpus itself has NO enumerable index N ("how many memories do I
#: have"). When EVERY post-quantifier content token is covered by this set
#: (∪ the sweep markers), the query is ``scope="open-ended"`` and the
#: coverage path never runs (never-flag rule).
_CORPUS_HEADS: frozenset[str] = frozenset({
    "memory", "memories", "note", "notes", "conversation", "conversations",
    "chat", "chats", "session", "sessions", "message", "messages",
    "thought", "thoughts", "story", "stories", "info", "information",
    "data", "stuff", "things", "thing", "everything", "anything",
    "recordings", "entries", "logs",
})
#: Bare activity verbs with no scoped object — "how many times have we
#: talked" has no enumerable subject; "how many times did we discuss the
#: API migration" does (the migration Object anchors the census).
_UNANCHORED_ACTIVITY: frozenset[str] = frozenset({
    "talk", "talked", "talking", "chat", "chatted", "speak", "spoke",
    "spend", "spent", "occur", "occurred", "happen", "happened",
    "write", "wrote", "said", "saying",
})
#: Quantity/measure words that describe the aggregation itself, never the
#: enumerated subject.
_MEASURE_WORDS: frozenset[str] = frozenset({
    "times", "time", "often", "frequently", "amount", "count", "number",
    "much", "many", "total", "sum", "days", "day", "weeks", "week",
    "months", "month", "years", "year", "hour", "hours", "minutes",
    "sometimes", "frequency", "cost", "costs", "price", "prices",
    "worth", "money", "paid", "spent",
})
#: Function/scaffold words stripped when isolating the aggregation subject.
_FUNCTION_WORDS: frozenset[str] = frozenset({
    "how", "did", "do", "does", "have", "has", "had", "i", "me", "my",
    "we", "you", "your", "us", "a", "an", "the", "of", "to", "for", "on",
    "in", "at", "with", "is", "are", "was", "were", "be", "been", "about",
    "all", "across", "over", "and", "or", "mine", "ours", "what",
    "that", "this", "these", "those", "it", "its", "there", "here",
})
#: Facet-dimension word cues — which axis the aggregation runs along
#: (recorded as the detector's ``facet_dimension`` hint; the pure coverage
#: math supports all of them, v1 retrieval wiring stays ``session``).
_DATE_CUE_RE = re.compile(
    r"\b(?:per|each|every)\s+(?:day|week|month|year)\b"
    r"|\b(?:days?|weeks?|months?|years?)\s+(?:per|a|each)\b"
    r"|\b(?:daily|weekly|monthly|yearly)\b"
)
_KIND_CUE_RE = re.compile(r"\b(?:kind|kinds|types?|categories?|kinds?)\b")


def _normalize_tokens(text: str) -> list[str]:
    """Lowercased alnum tokens (the same structural token shape the sparse
    tokenizer uses — deterministic across backends)."""
    return re.sub(r"[^a-z0-9 ]", " ", (text or "").lower()).split()


def _strip_phrase(text: str, phrase: str) -> str:
    """Remove one whole-word phrase occurrence (any position) from
    normalized text — word-boundary-guarded so a phrase can never be
    stripped out of the middle of an unrelated token."""
    return re.sub(rf"\b{re.escape(phrase)}\b", " ", text, count=1).strip()


@dataclass(frozen=True)
class AggregativeIntent:
    """The detector verdict for one query (hermetic, deterministic).

    ``is_aggregative`` — the query surface carries a counting/quantifier
    aggregation pattern ("how many/how much/how often/total/count/every").
    ``quantifier`` — the matched quantifier id from
    :data:`AGGREGATIVE_QUANTIFIERS` (None when not aggregative).
    ``scope`` — ``"entity-scoped"`` (a scoped subject the anchor spine
    could enumerate), ``"open-ended"`` (the aggregation subject is the
    memory corpus itself — never flagged), or None (not aggregative).
    ``facet_dimension`` — the aggregation axis hint
    (``session``/``kind``/``date_month`` — the same vocabulary as
    :data:`FACET_DIMENSIONS`, so the hint feeds the coverage math
    directly) or None. Surface-only: the AUTHORITATIVE
    entity-scope gate is anchor RESOLUTION through the Object-name index
    (``aggregative_verdict``) — an entity-scoped surface that resolves no
    anchor emits no coverage signal (fail-open, never a false flag).
    """

    is_aggregative: bool
    quantifier: str | None = None
    scope: str | None = None
    facet_dimension: str | None = None


def _open_ended_scope(remaining: str) -> bool:
    """True when the post-quantifier remainder carries NO enumerable
    subject — every content token is corpus self-reference, a bare
    activity verb, or a measure word. This is the text gate of the
    never-flag rule; the anchor-resolution index gate backs it up."""
    tokens = [t for t in _normalize_tokens(remaining)
              if t not in _FUNCTION_WORDS]
    if not tokens:
        return True
    for t in tokens:
        if (t not in _CORPUS_HEADS and t not in _UNANCHORED_ACTIVITY
                and t not in _MEASURE_WORDS):
            return False
    return True


def detect_aggregative_intent(query: str | None) -> AggregativeIntent:
    """Hermetic, deterministic aggregative-intent detection over the query
    surface (C5 #2513, #2521). No graph, no model, no IO.

    Ordered, pinned rule set:

    1. Non-query / empty text → not aggregative.
    2. Elapsed-time shapes ("how many days ago", "3 weeks ago") and
       "how long" are TEMPORAL/ordering classes (R5's domain) — NOT
       counting aggregation.
    3. Quantifier scan (:data:`AGGREGATIVE_QUANTIFIERS`, first match
       wins) — none matched ⇒ ``is_aggregative=False``.
    4. Scope: ``"open-ended"`` when every post-quantifier content token is
       corpus self-reference / a bare activity / a measure word (never
       flagged downstream); else ``"entity-scoped"``.
    5. Facet-dimension hint from date/kind word cues (default session).

    The classification table is pinned by tests/test_aggregative_intent.py
    so a vocabulary change is a deliberate, reviewed diff.
    """
    if not query or not str(query).strip():
        return AggregativeIntent(False)
    q = " ".join(str(query).lower().split())
    # 2. temporal/ordering exclusions (before the quantifier scan)
    if _HOW_LONG_RE.search(q) or _ELAPSED_TIME_RE.search(q):
        return AggregativeIntent(False)
    # 3. quantifier scan — first match wins (precedence-ordered table)
    matched: tuple[str, re.Match[str]] | None = None
    for qid, rx in _QUANTIFIER_RES:
        m = rx.search(q)
        if m:
            matched = (qid, m)
            break
    if matched is None:
        return AggregativeIntent(False)
    qid, m = matched
    # 4. scope over the remainder (quantifier + amount markers removed)
    remaining = q
    for phrase in (m.group(0), "in total", "in all", "altogether",
                   "combined", "total", "sum"):
        remaining = _strip_phrase(remaining, phrase)
    scope = "open-ended" if _open_ended_scope(remaining) else "entity-scoped"
    # 5. facet-dimension hint (date/kind cues; default session). The
    # vocabulary IS the coverage facet vocabulary (FACET_DIMENSIONS) so the
    # hint feeds compute_facet_coverage directly: a date-cued aggregation
    # hints the ``date_month`` axis, a kind-cued one hints ``kind``.
    if _DATE_CUE_RE.search(q):
        dimension = "date_month"
    elif _KIND_CUE_RE.search(q):
        dimension = "kind"
    else:
        dimension = "session"
    return AggregativeIntent(True, quantifier=qid, scope=scope,
                             facet_dimension=dimension)


# ── Facet enumeration + the per-facet coverage check (pure) ────────────────
def facet_key_for_point(point: Mapping[str, Any],
                        dimension: str = DEFAULT_FACET_DIMENSION) -> str | None:
    """The facet key of one point under ``dimension`` (None when the point
    carries no value for that axis — such points contribute to neither the
    known nor the retrieved census, so the k-of-N comparison stays honest).

    * ``session`` — the point's session linkage (``session_id`` /
      ``sessionId`` — the stored-prop name both the eval annotation and
      the ask annotation expose). A point with no session linkage yields
      None: session-scoped completeness cannot be asserted per-point.
    * ``kind`` — the point's kind (``point_kind`` / ``pointKind`` / ``kind``).
    * ``date_month`` — the point's date prop (``session_date`` /
      ``date`` / ``createdAt`` / ``startedAt``) truncated to ``YYYY-MM``;
      an unparseable/absent date yields None.
    """
    if dimension not in FACET_DIMENSIONS:
        raise ValueError(
            f"unknown facet dimension {dimension!r} — expected one of "
            f"{FACET_DIMENSIONS}")
    if dimension == "session":
        raw = point.get("session_id") or point.get("sessionId") or ""
        key = str(raw).strip()
        return key or None
    if dimension == "kind":
        raw = (point.get("point_kind") or point.get("pointKind")
               or point.get("kind") or "")
        key = str(raw).strip()
        return key or None
    # date_month
    for prop in ("session_date", "date", "createdAt", "startedAt",
                 "created_at"):
        raw = str(point.get(prop) or "").strip()
        if raw and len(raw) >= 7 and raw[:4].isdigit() and raw[4] == "-":
            return raw[:7]
    return None


@dataclass(frozen=True)
class FacetCoverage:
    """The pure per-facet coverage verdict (k of N known facets retrieved).

    ``n_facets`` — N (the known facet count from the anchor census;
    0 = no enumerable N). ``retrieved_facets`` — k (known facets with a
    representative in the current retrieval). ``facet_coverage`` — k/N
    (None when N == 0 — distinguish "no exhaustive N" from a real 0.0).
    ``missing_facets`` — the bounded missing list. ``signal`` —
    ``"complete"`` (k == N > 0), ``"partial"`` (0 <= k < N), ``"none"``
    (no enumerable N — never a claim). ``capped`` — the census overflowed
    the bound, so N is NOT exhaustive and the signal degrades to none.
    """

    dimension: str
    n_facets: int
    retrieved_facets: int
    facet_coverage: float | None
    missing_facets: tuple[str, ...]
    signal: str
    capped: bool
    anchors: tuple[str, ...] = ()


def compute_facet_coverage(
    *,
    census_points: Sequence[Mapping[str, Any]],
    retrieved_points: Sequence[Mapping[str, Any]],
    dimension: str = DEFAULT_FACET_DIMENSION,
    anchors: Sequence[str] = (),
) -> FacetCoverage:
    """The per-facet coverage check (hermetic; no graph, no model).

    Given the anchor census points (the linked facts the index enumerates
    about the resolved entity) and the current retrieval's points (top-k
    window — the surface a future completeness loop would expand),
    enumerate the known facets (group by the facet key), compute the
    retrieved k-of-N, and emit the structured verdict. Bounded + fail-open:
    an enumeration that overflows ``MAX_FACETS`` distinct facets is not
    exhaustive and degrades to ``signal="none"`` with ``capped=True`` —
    the all-pieces honesty claim never rides a truncated N.
    """
    known: list[str] = []
    for p in census_points:
        key = facet_key_for_point(p, dimension)
        if key is not None:
            known.append(key)
    known = sorted(set(known))
    capped = len(known) > MAX_FACETS
    if capped:
        known = known[:MAX_FACETS]
    retrieved: set[str] = set()
    for p in retrieved_points:
        key = facet_key_for_point(p, dimension)
        if key is not None:
            retrieved.add(key)
    n = len(known)
    k = len([key for key in known if key in retrieved])
    coverage = (k / n) if n else None
    missing = tuple(sorted(key for key in known if key not in retrieved))
    if capped or n == 0:
        signal = "none"
    elif k >= n:
        signal = "complete"
    else:
        signal = "partial"
    return FacetCoverage(
        dimension=dimension,
        n_facets=n,
        retrieved_facets=k,
        facet_coverage=coverage,
        missing_facets=missing[:MAX_MISSING_REPORTED],
        signal=signal,
        capped=capped,
        anchors=tuple(sorted(set(str(a) for a in anchors))),
    )


# ── Entity-anchor-spine adapter (the #2518 resolution surface) ─────────────
def collect_anchor_census(
    proj: Any,
    query: str,
    *,
    max_anchors: int = DEFAULT_MAX_ANCHORS,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool] | None:
    """Resolve the query's entity anchors through the Object-name index and
    enumerate their linked Points (the cross-session ``aboutObject`` join —
    the SAME surface #2518's key expansion uses, so both levers agree on
    what "about subject X" means).

    Returns ``(anchors, census_points, truncated)``:

    * ``anchors`` — ``{"id", "name"}`` for the resolved anchors (stable
      (score, id) order, capped at ``max_anchors``);
    * ``census_points`` — normalized linked-point dicts (``id``,
      ``session_id``, ``session_date``, ``point_kind``), deduped by id;
    * ``truncated`` — True when any per-anchor point bound
      (``MAX_POINTS_PER_ANCHOR``) was hit client-side: the census is not
      exhaustive there and the caller must fail open (never claim an
      all-pieces N).

    Fail-open contract (mirrors C2 #2518): any fetch/query failure or an
    empty anchor resolution returns ``None`` — the caller keeps a no-
    coverage-signal verdict; the check can never break a working lane.
    NOTE: the caller is expected to pass a live projection; a missing
    graph is treated as an unresolvable subject WITHOUT ever calling the
    engine (a bogus graph must not trip the engine's circuit breaker).
    """
    from .search_engine import run_fts_query  # lazy: FTS needs a live index
    if proj is None or getattr(proj, "g", None) is None:
        return None
    try:
        anchor_rows = run_fts_query(
            proj.g, query, entity_type="object",
            limit=_ANCHOR_CANDIDATES)
    except Exception:
        _logger.warning(
            "aggregative anchor resolution failed — no coverage signal",
            exc_info=True)
        return None
    if not anchor_rows:
        return None
    anchors = sorted(
        anchor_rows, key=lambda r: (-r[1], r[0]))[:max_anchors]
    anchor_ids = [pid for pid, _score in anchors]
    try:
        # Two batched queries (NOT one collect() query): FalkorDB groups
        # aggregated queries by the non-aggregated columns it recognizes —
        # a RETURN o.id, o.name, collect(…) folds ``o.name`` per group in
        # a backend-dependent way (measured: empty name for a populated
        # Object). Names + census fetched separately so both stay exact.
        name_rows = proj.g.query(
            "MATCH (o:Object) WHERE o.id IN $ids "
            "RETURN o.id, o.name",
            params={"ids": anchor_ids},
        ).result_set
        name_by_id = {str(r[0]): str(r[1] or "") for r in (name_rows or [])}
        rows = proj.g.query(
            "MATCH (o:Object) WHERE o.id IN $ids "
            "OPTIONAL MATCH (o)<-[:aboutObject]-(p:Point) "
            "RETURN o.id, collect([p.id, p.session_id, "
            "p.session_date, p.createdAt, p.pointKind])",
            params={"ids": anchor_ids},
        ).result_set
    except Exception:
        _logger.warning(
            "aggregative census fetch failed — no coverage signal",
            exc_info=True)
        return None
    anchor_info: list[dict[str, Any]] = [
        {"id": str(anchor[0]), "name": name_by_id.get(str(anchor[0]), "")}
        for anchor in anchors
    ]
    census: list[dict[str, Any]] = []
    seen: set[str] = set()
    truncated = False
    for row in rows or []:
        per_anchor = 0
        for cell in (row[1] or []):
            # OPTIONAL MATCH yields one null cell when the anchor has no
            # linked points — skip it (collect nulls as [None, ...]).
            if not cell or not cell[0]:
                continue
            if per_anchor >= MAX_POINTS_PER_ANCHOR:
                truncated = True
                break
            pid = str(cell[0])
            if pid in seen:
                continue
            seen.add(pid)
            per_anchor += 1
            census.append({
                "id": pid,
                "session_id": str(cell[1] or "").strip(),
                "session_date": str(cell[2] or "").strip(),
                "created_at": str(cell[3] or "").strip(),
                "point_kind": str(cell[4] or "").strip(),
            })
    return anchor_info, census, truncated


# ── One-call seam (the #2521 OFF-by-default surface C3-3 will consume) ─────
def aggregative_verdict(
    *,
    query: str,
    proj: Any,
    retrieved_points: Sequence[Mapping[str, Any]],
    dimension: str | None = None,
) -> dict[str, Any]:
    """The detector + coverage-check seam for the retrieval path (default
    OFF in the product — the sealed #2513 A/B decides adoption; callers
    opt in behind their own gate/env, mirroring #2518's
    ``entity_key_expansion``). NEVER raises: every graph failure degrades
    to a no-coverage-signal verdict (fail-open).

    Emits the structured, per-outcome-measurable verdict the C3-3 routing
    (#2519) will consume:

    ``detected_intent`` — ``{is_aggregative, quantifier, scope,
    facet_dimension}`` from :func:`detect_aggregative_intent`;
    ``facet_coverage`` — k/N or None (no enumerable N);
    ``missing_facets`` — the bounded missing list; ``signal`` —
    ``none``/``partial``/``complete``; ``n_facets``/``retrieved_facets``;
    ``anchors`` — resolved anchor names (the C3-3 re-query vocabulary);
    ``capped``/``reason`` — census-truncation + why no signal fired.
    """
    intent = detect_aggregative_intent(query)
    base: dict[str, Any] = {
        "detected_intent": {
            "is_aggregative": intent.is_aggregative,
            "quantifier": intent.quantifier,
            "scope": intent.scope,
            "facet_dimension": intent.facet_dimension,
        },
        "facet_coverage": None,
        "n_facets": None,
        "retrieved_facets": None,
        "missing_facets": [],
        "signal": "none",
        "anchors": [],
        "capped": False,
        "reason": None,
    }
    if not intent.is_aggregative:
        base["reason"] = "not_aggregative"
        return base
    # never-flag rule (text gate): corpus-self-referential aggregation
    if intent.scope == "open-ended":
        base["reason"] = "open_ended"
        return base
    # dimension normalization (defense-in-depth for the NEVER-raises seam
    # contract): an unknown dimension hint falls back to the wired default
    # instead of propagating a ValueError out of the one-call surface
    # (the detector vocabulary already matches FACET_DIMENSIONS; this
    # guards future hints/callers).
    effective = dimension or intent.facet_dimension \
        or DEFAULT_FACET_DIMENSION
    if effective not in FACET_DIMENSIONS:
        effective = DEFAULT_FACET_DIMENSION
    # never-flag rule (index gate): an un-resolvable subject has no
    # exhaustive N in this index — no coverage claim (fail-open, no false
    # partial flag from an unresolvable entity name).
    census = collect_anchor_census(proj, query)
    if census is None:
        base["reason"] = "no_anchor"
        return base
    anchors, census_points, truncated = census
    if not census_points or truncated:
        base["reason"] = "truncated" if truncated else "no_linked_points"
        base["anchors"] = [a["name"] for a in anchors]
        base["capped"] = bool(truncated)
        return base
    verdict = compute_facet_coverage(
        census_points=census_points,
        retrieved_points=retrieved_points,
        dimension=effective,
        anchors=[a["name"] for a in anchors],
    )
    base.update({
        "facet_coverage": verdict.facet_coverage,
        "n_facets": verdict.n_facets,
        "retrieved_facets": verdict.retrieved_facets,
        "missing_facets": list(verdict.missing_facets),
        "signal": verdict.signal,
        "anchors": list(verdict.anchors),
        "capped": verdict.capped,
        "reason": "measured",
    })
    return base
