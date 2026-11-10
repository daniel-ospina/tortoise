"""C3-1 (#2519/#2567): the evidence-completeness retrieval loop — product side.

The multi-session partial-evidence lever (docs/scoping/2026-09-08-2519-
coverage-loop.md §3 mechanism (a), program C3): a one-shot top-k retrieve
cannot enumerate ALL sessions that carry an aggregation question's subject —
k<N stays 0 on the official all-or-nothing ``recall_all@5`` binary. This
module ships the **retrieve → check → expand → merge** completeness loop as
hermetic product primitives (the product-cohesion rule — the loop lands in
``tortoise/`` and the LongMemEval harness measures it):

1. **CENSUS** — :func:`facet_census`: a RULE-BASED facet census over the
   query + the graph (NO model, bounded): countable entity-scoped facets
   only. The query's own entities resolve through the Object-name spine
   (the same anchor resolution #2518 uses); each anchor's census span = the
   distinct sessions carrying points ``-[:aboutObject]->``-linked to it,
   optionally date-qualified by an interval/recency bound detected in the
   query text (the R5 ``detect_time_constraint`` seam's shapes).
2. **CHECK** — :func:`coverage_gap`: is the retrieved set facet-incomplete
   against the census? Fires only for an ENTITY-SCOPED facet whose span is
   seeded in the top window yet not fully covered by it (partial evidence
   — the §7 "facet-based, never re-rank the pool that missed" rule).
   Open-ended queries (no countable facet) never fire.
3. **EXPAND** — :func:`loop_expansion_pass`: ONE targeted second sparse pass
   for the missing facet (hard ``LOOP_MAX_EXTRA_PASSES`` = 1 bound), reusing
   the A4/C2 reserved-slot OR contract (original query tokens keep their
   slots; the missing facet's alias vocabulary fills the bounded expansion
   tail) — a re-QUERY for the missing facet, never a re-rank of the pool
   that missed.
4. **MERGE** — :func:`merge_expansion_order` + :func:`session_diverse_order`
   (pure): the additive union in second-pass relevance order (base hits the
   sparse re-query re-finds keep their slots — the A4 leg-merge contract at
   pool level) followed by the mandatory session-diverse window discipline
   (§3(d)) — no session may hold more than the per-session cap of the guard
   window, so a same-session flood (points were never session-capped)
   cannot crowd the cross-session recovery evidence out of the window the
   official binary grades.

Every graph step is bounded and fail-open (any fetch/query failure keeps
the ORIGINAL pool — byte-identical, the A4/C2 posture); the loop is OFF by
default (the #1745 fail-safe decision) and the eval arms it via
``--coverage-loop`` / ``TORTOISE_LME_COVERAGE_LOOP`` (2×2 covariate with the
#2518 entity-key expansion arm).

Stage ownership: this module owns the product rules + graph passes. The
harness's ``retrieve_for_question`` (tools/longmem_eval/retrieve.py) composes
census → gap → expand → merge on the ANNOTATED pool (session linkage lives
there) and records the per-outcome markers (``loop_iterations`` /
``loop_fired_facet`` / ``loop_merged_added``). The ask lane adopts the same
seams when the sealed A/B gate lands.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

# #11646: ONE calendar-date resolver for every stored date shape — this module
# used to hold its own copy of the grammar, which is how the copies drifted.
from tortoise.calendar_date import UNDATED_SENTINEL_DATE as _UNDATED_SENTINEL_DATE
from tortoise.calendar_date import calendar_date as _shared_calendar_date

logger = logging.getLogger(__name__)

# ── loop budget + merge-discipline constants (the hard, documented bounds) ──
#: Hard iteration bound: the loop may run at most ONE extra retrieval pass
#: beyond the one-shot pass (§7 iteration-cost-creep guard; §8 census).
LOOP_MAX_EXTRA_PASSES = 1

#: The top-k guard window the loop protects (the all-or-nothing recall
#: window the official binary grades; the eval's ``ks`` starts at 5).
DEFAULT_LOOP_GUARD_WINDOW = 5

#: Merge-discipline per-session cap INSIDE the guard window: a session may
#: hold at most this many of the window's ranks, so one session can never
#: starve the other N−1 answer sessions out of the window (§3(d)/§7).
DEFAULT_LOOP_SESSION_CAP = 2

#: Census span cap — the distinct-session harvest per anchor is bounded so
#: a high-degree anchor cannot unboundedly feed the check (like the #2518
#: alias caps; the span is a coverage CENSUS, never a full enumeration).
DEFAULT_LOOP_SPAN_CAP = 64

#: Date-qualified facet window shape (the R5 seam's interval shapes) —
#: mirrored from tools/longmem_eval/retrieve.py::detect_time_constraint so
#: the product census can qualify an entity facet's span WITHOUT importing
#: the harness (the eval's function stays authoritative for TR filtering).
_DATE_INTERVAL_ISO_RE = re.compile(
    r"between\s+(\d{4}-\d{2}-\d{2})\s+and\s+(\d{4}-\d{2}-\d{2})")
_DATE_RECENCY_RE = re.compile(
    r"(?:\b(\d+)\s+(days?|weeks?|months?)\s+ago\b"
    r"|\blast\s+(\d+)\s+(days?|weeks?|months?)\b)")
_UNIT_DAYS = {"day": 1, "week": 7, "month": 30}

#: Calendar-date shapes live in ``tortoise/calendar_date.py`` (#11646) — ONE
#: resolver, imported above, so this module carries no grammar of its own. What
#: this seam needs from it is the measured fact that the ingest producer's
#: ``2023/05/20 (Sat) 03:29`` (23,867/23,867 values on the frozen LongMemEval
#: dataset, #3011) and ISO both parse, unpadded month/day included, validated
#: through ``date``. That is a UNION over what this seam read before, and the
#: written-day semantics it always had (a timestamp offset never moves the day)
#: are the resolver's default, so every value reads the same day it did.


@dataclass(frozen=True)
class LoopFacet:
    """One countable, entity-scoped facet of the query.

    ``kind``: "entity" (an Object-spine anchor named by the query) —
    date-range bounds QUALIFY an entity facet's span; a date alone never
    enumerates a facet (open-ended never fires, §3(a) rule proxy).
    ``key``: stable facet identity ("entity:<name>") for the fired-facet
    marker. ``span``: the census-known session ids carrying the facet
    (bounded). ``aliases``: the additive expansion vocabulary (the anchor's
    name + linked points' E3 ``search_keys`` — the vocabulary that lets a
    same-subject point from a starved session match a re-query).
    """
    kind: str
    key: str
    name: str
    span: frozenset[str]
    aliases: tuple[str, ...] = ()


def facet_date_constraint(text: str) -> tuple[str | None, str | None, str | None]:
    """Detect a countable date bound in the query (pure; the R5 seam's
    interval/recency shapes). Returns (kind, start, end) — kind is
    "interval" | "recency" | None. Mirrors the eval seam's regexes so the
    two can never drift on the shapes the census qualifies on. An interval
    bound is an inclusive ISO window; a recency bound carries the day count
    in ``start`` (the caller applies it against its session-date source).
    """
    t = " ".join(text.lower().split())
    m = _DATE_INTERVAL_ISO_RE.search(t)
    if m:
        return "interval", m.group(1), m.group(2)
    m = _DATE_RECENCY_RE.search(t)
    if m:
        n = int(m.group(1) or m.group(3))
        unit = (m.group(2) or m.group(4)).rstrip("s")
        return "recency", str(n * _UNIT_DAYS[unit]), None
    return None, None, None


def _calendar_date(value: object) -> str | None:
    """Normalize a session/question date to ``YYYY-MM-DD``, or ``None``.

    Delegates to ``tortoise.calendar_date`` (#11646) — the ONE resolver for
    every stored date shape in the read path — instead of holding its own
    copy of the grammar. It accepts what the two date sources actually carry
    (ISO dates and timestamps, and the ingest producer's real
    ``2023/05/20 (Sat) 03:29``, #3011) and validates through ``date``, so an
    out-of-range ``2026-13-45`` is undated too.

    The WRITTEN-DAY semantics are this seam's (and ``sdk._iso_date10``'s): an
    offset-bearing timestamp keeps the day the dataset states. The resolver's
    other reading — UTC truncation — belongs to the assembly as-of boundary
    and is opt-in there.

    ``None`` means UNDATED (missing, empty, the undated sentinel, free text):
    no date is ever fabricated, and the caller decides what undated means
    for its bound (the span qualification drops it — it cannot be proven
    in-window; the window resolution ignores it).
    """
    d = _shared_calendar_date(value)
    if d is None:
        return None
    iso = d.isoformat()
    # The sentinel's date itself (any offset spelling) is "no date" — the rule
    # ``TortoiseSDK._iso_date10`` and ``subgraph_render`` already apply.
    return None if iso == _UNDATED_SENTINEL_DATE else iso


def facet_window_bounds(kind: str | None, start: str | None, end: str | None,
                        session_dates: dict[str, str] | None = None,
                        *, anchor: str | None = None,
                        ) -> tuple[str, str] | None:
    """Resolve a detected date bound into an inclusive ISO ``(lo, hi)``
    window — THE single source of truth for the temporal facet's window
    arithmetic (:func:`facet_census` and everything it date-qualifies).

    :func:`_span_in_window` is its consumer in ``tortoise/``; the routed
    point-level twin on the eval seam
    (``tools/longmem_eval/retrieve.py::_apply_time_window``) resolves through
    it too, passing ``anchor`` — so BOTH halves of the temporal facet read the
    producer's ``2023/04/10 (Mon) 17:50`` shape instead of comparing raw
    strings (#8710, #9182).

    * ``interval`` — ``(start, end)`` as calendar dates, ordered (a reversed
      "between A and B" still means the window the question named rather
      than an empty one).
    * ``recency`` — ``[qdate − N days, qdate]``, INCLUSIVE on BOTH ends (the
      same inclusivity ``_span_in_window`` applies with ``lo <= d <= hi``, and
      pinned by ``test_span_in_window_qualifies_the_producer_slash_date_shape``,
      which asserts the ``qdate − N`` boundary day IS in the window), where
      ``qdate`` is the LATEST parseable date in ``session_dates``. The graph
      carries no global clock, so the haystack's newest session stands in for
      "now" (the eval passes the question's ``haystack_dates``; unchanged from
      the pre-#8710 rule).

    ``anchor`` (keyword-only) is the RECENCY clock. ``None`` — the census
    rule, and the default — uses the latest parseable date in
    ``session_dates`` (the graph carries no global clock). The eval seam
    passes the question's own ``question_date``: there the question date IS
    the clock, so an ``anchor`` that cannot be resolved returns ``None`` (the
    caller's defensive fallback) rather than silently substituting a
    different one.

    ``None`` means NO BOUND: the caller keeps its unqualified input. Every
    unresolvable input degrades here rather than raising — an unknown kind, a
    missing/unparseable bound, a recency day count that is not an integer, a
    recency bound with no parseable session date (or unresolvable anchor). That is the #8710 rule and
    it is structural: a date that cannot be resolved can only ever mean
    "unbounded", NEVER "matches nothing", because a degenerate hard filter
    silently kills the facet (the pre-#8710 recency branch raised ``TypeError``
    on the raw ISO string and the interval branch compared strings, so the
    whole temporal facet was dead on the producer's ``2023/05/20 (Sat) 03:29``
    shape).
    """
    if kind == "interval":
        lo, hi = _calendar_date(start), _calendar_date(end)
        if lo is None or hi is None:
            return None
        return (lo, hi) if lo <= hi else (hi, lo)
    if kind == "recency":
        # A MISSING day count is unresolvable, and unresolvable means NO BOUND:
        # coercing it to 0 built the NARROWEST possible window (``qdate``
        # alone), a hard one-day filter that empties the span — the exact
        # "never matches-nothing" failure the rule above forbids.
        if start is None or not str(start).strip():
            return None
        if anchor is not None:
            # An explicit clock is authoritative; unresolvable means NO BOUND,
            # never a substitution (the census's latest-known rule would be a
            # different question's clock on the eval seam).
            resolved_anchor = _calendar_date(anchor)
            if resolved_anchor is None:
                return None
        else:
            known = [d for d in (_calendar_date(v)
                                 for v in (session_dates or {}).values()) if d]
            if not known:
                return None
            resolved_anchor = max(known)
        try:
            # A negative count is clamped to 0 so it can never INVERT the
            # window (the product path cannot emit one: ``_DATE_RECENCY_RE``
            # is ``\d+``). An UNREPRESENTABLE one is unresolvable, not fatal:
            # "in the last 99999999999999999999 days" reaches here from real
            # question text, and the contract is ``None``, never a raise (the
            # same guard ``assembly.resolve_relative_anchor`` applies to the
            # same input).
            n_days = max(0, int(start))
            qdate = date.fromisoformat(resolved_anchor)
            return ((qdate - timedelta(days=n_days)).isoformat(),
                    qdate.isoformat())
        except (OverflowError, TypeError, ValueError):
            return None
    return None


def _span_in_window(span: frozenset[str],
                    session_dates: dict[str, str] | None,
                    kind: str | None, start: str | None,
                    end: str | None) -> frozenset[str]:
    """Date-qualify a facet's session span (pure).

    The window comes from :func:`facet_window_bounds` (the shared
    resolution); the session dates are normalized through
    :func:`_calendar_date` (ISO and the producer's ``2023/05/20 (Sat) 03:29``
    shape compare correctly — comparing the RAW strings is what made the
    interval branch drop every session on the frozen dataset).

    No bound (unknown kind, no session dates, or a bound that cannot be
    resolved) keeps the span untouched. When a bound DOES apply, an undated
    session cannot be proven in-window and is dropped (the span stays honest
    — a facet whose whole span filters out is dropped by the caller).
    """
    if kind is None or not session_dates:
        return span
    window = facet_window_bounds(kind, start, end, session_dates)
    if window is None:
        return span
    lo, hi = window
    return frozenset(
        s for s in span
        if (d := _calendar_date(session_dates.get(s))) is not None
        and lo <= d <= hi)


def _token_overlap(query: str, name: str, *, keep_numeric: bool = False) -> bool:
    """Quality gate (pure): the anchor's name must share a sparse token with
    the query (mirrors #2518's "a question that names its subject maps to
    the Object nodes whose names share its tokens" — an Object that scored
    on the FTS index without token overlap is index noise, not the query's
    facet)."""
    from tortoise.sparse import tokenize_sparse_query
    q_toks = set(tokenize_sparse_query(query, keep_numeric=keep_numeric))
    if not q_toks:
        return False
    n_toks = set(tokenize_sparse_query(name, keep_numeric=True))
    return bool(q_toks & n_toks)


def facet_census(proj: Any, query: str, *,
                 session_dates: dict[str, str] | None = None,
                 keep_numeric: bool = False,
                 max_anchors: int | None = None,
                 max_span_sessions: int | None = None) -> list[LoopFacet]:
    """The rule-based facet census (step 1): resolve the query's own entity
    anchors THROUGH the Object-name index (the #2518 spine) and harvest each
    anchor's session span + expansion vocabulary from its ``aboutObject``-
    linked points (bounded, one batched query). Fail-open: any graph failure
    yields [] (no facets → the check can never fire → the caller keeps the
    ORIGINAL pool byte-identical).

    ``session_dates``: {session_id: date-string} over the query's haystack —
    when provided AND the query text carries an interval/recency bound, each
    anchor's span is date-qualified to the in-window sessions (the §4 P0
    date-range facet) through the SHARED :func:`facet_window_bounds`
    resolution (#8710 — both date shapes the sources carry, and an
    unresolvable bound degrades to "no bound" rather than emptying the
    span). ``max_anchors``/``max_span_sessions`` override the documented
    bounds (tests pin the defaults).
    """
    if not query or not query.strip():
        return []
    # C2 (#2518) anchor numbers are single-sourced from the SDK (lazy import
    # — this module must never create an import cycle with tortoise.sdk).
    from tortoise.sdk import (
        _ENTITY_ALIAS_MAX,
        _ENTITY_ALIAS_PER_ANCHOR,
        _ENTITY_ANCHOR_CANDIDATES,
        _ENTITY_ANCHOR_LIMIT,
    )
    anchors_n = max_anchors if max_anchors is not None else _ENTITY_ANCHOR_LIMIT
    span_cap = (max_span_sessions if max_span_sessions is not None
                else DEFAULT_LOOP_SPAN_CAP)
    date_kind, date_start, date_end = facet_date_constraint(query)
    try:
        from tortoise.search_engine import run_fts_query
        anchor_rows = run_fts_query(
            proj.g, query, entity_type="object",
            limit=_ENTITY_ANCHOR_CANDIDATES,
            keep_numeric=keep_numeric,
            # #3301: anchor RESOLUTION only — the census harvests names,
            # never surfaces these Objects, so terminal Objects stay
            # resolvable (the assembly-resolver principle).
            excluded_statuses=())
    except Exception:  # noqa: BLE001, RUF100
        logger.warning(
            "C3-1 facet census anchor resolution failed — loop no-ops "
            "(the original pool is kept)", exc_info=True)
        return []
    if not anchor_rows:
        return []
    # deterministic anchor order: (score desc, id asc) — the C2 tiebreak.
    anchors = sorted(anchor_rows, key=lambda r: (-r[1], r[0]))[:anchors_n]
    anchor_ids = [aid for aid, _score in anchors]
    try:
        rows = proj.g.query(
            "MATCH (o:Object) WHERE o.id IN $ids "
            "OPTIONAL MATCH (o)<-[:aboutObject]-(p:Point) "
            "RETURN o.id, o.name, "
            "       collect(DISTINCT coalesce(p.session_id, '')), "
            "       collect(coalesce(p.search_keys, ''))",
            params={"ids": anchor_ids},
        ).result_set
    except Exception:  # noqa: BLE001, RUF100
        logger.warning(
            "C3-1 facet census harvest failed — loop no-ops (the original "
            "pool is kept)", exc_info=True)
        return []
    facets: list[LoopFacet] = []
    for row in rows or []:
        _oid, name = row[0], str(row[1] or "")
        if not name or not _token_overlap(query, name, keep_numeric=keep_numeric):
            continue
        sessions: list[str] = []
        aliases: list[str] = []
        per_anchor = 0
        for v in (row[3] or []):
            if per_anchor >= _ENTITY_ALIAS_PER_ANCHOR:
                break
            values = ([str(x) for x in v if x]
                      if isinstance(v, (list, tuple)) else [str(v)] if v else [])
            for x in values:
                x = x.strip()
                if x and per_anchor < _ENTITY_ALIAS_PER_ANCHOR:
                    aliases.append(x)
                    per_anchor += 1
        seen_sess: set[str] = set()
        for v in (row[2] or []):
            s = str(v or "").strip()
            if s and s not in seen_sess:
                seen_sess.add(s)
                sessions.append(s)
            if len(sessions) >= span_cap:
                break
        if not sessions:
            # no session-linked content → nothing countable to cover
            continue
        span = _span_in_window(frozenset(sessions), session_dates,
                               date_kind, date_start, date_end)
        if not span:
            continue
        # deterministic alias pool: sorted, deduped, capped (C2's ordering —
        # DB collect() order is not cross-run stable).
        ordered: list[str] = []
        seen_alias: set[str] = set()
        for a in sorted(aliases):
            if a in seen_alias:
                continue
            seen_alias.add(a)
            ordered.append(a)
            if len(ordered) >= _ENTITY_ALIAS_MAX:
                break
        aliases = ordered
        if name not in aliases:
            aliases = [name, *aliases]
        facets.append(LoopFacet(
            kind="entity", key=f"entity:{name}", name=name,
            span=span, aliases=tuple(aliases[: _ENTITY_ALIAS_MAX])))
    return facets


def _session_of(hit: dict, session_key: Callable[[dict], str] | None = None) -> str:
    """A hit's session identity — delegates to the pool's AUTHORITY
    (``retrieval.session_key_of``: ``session_id`` when present, else the
    ``idx:{lme_session_index}`` bucket, else the wire ``sessionId``) so the
    loop's window census and the pool's dedup never disagree on what a
    session is. The import is FUNCTION-LOCAL: ``coverage_loop`` stays a
    stdlib-only leaf at import time and the ``retrieval → coverage_loop``
    dependency stays one-way (no cycle under either import order).

    The camel ``sessionId`` leg is #10540: without it a product pool hit —
    whose only identity is that camel key (the fetch pops the snake prop
    from every point hit) — censused as a phantom, so for a
    captured-session pool of TURN hits this census counted zero real
    sessions. #10632's own instrument INHERITED that blindness rather than
    covering it (``reader_window_displacement`` keys through the same
    AUTHORITY, ``retrieval.session_key_of`` — which this function delegates
    to — and filters the ``idx:*`` namespace, so it reported
    ``sessions_before = 0`` for such a pool); a hit whose session was
    present in a spelling the authority read was never affected.
    """
    if session_key is not None:
        return session_key(hit)
    from tortoise.retrieval import session_key_of
    return session_key_of(hit)


def coverage_gap(facets: list[LoopFacet], pool: list[dict], *,
                 window: int = DEFAULT_LOOP_GUARD_WINDOW,
                 session_key: Callable[[dict], str] | None = None,
                 ) -> list[LoopFacet]:
    """The completeness CHECK (step 2, pure): which census facets is the
    retrieved pool facet-INCOMPLETE against?

    Returns every census facet F where:
      * F is SEEDED — at least one of F's span sessions is represented in
        the pool's top-``window`` ranks (the pool IS about this subject —
        an anchor the pool never touches is index noise, not a miss), and
      * F is UNDER-COVERED — F's span is not fully inside the window's
        sessions (a countable span session is missing from the window the
        official binary grades: partial evidence).

    Open-ended queries produce no facets → no fire, by construction. A span
    fully inside the window (single-session subjects, complete coverage)
    never fires. Deterministic: the returned list keeps the census order
    (the driver fires on the first entry; the ONE expand pass unions the
    missing facets' vocabulary — the hard ≤1-extra-pass bound).
    """
    if not facets or not pool:
        return []
    window = max(1, min(window, len(pool)))
    win_sessions = {_session_of(h, session_key) for h in pool[:window]}
    # A synthetic ``idx:*`` key names no graph session — the ``lme_session_
    # index`` bucket, and since #3591 the per-hit ``idx:point:{id}`` bucket
    # of a hit with no session identity — so it can never be a session a
    # census ``span`` names. Drop the whole namespace rather than the one
    # literal ``idx:-1``: after #3591 the per-hit bucket would otherwise
    # read as a session PRESENT in the window, which is the opposite of
    # what it means.
    win_sessions = {k for k in win_sessions
                    if k and not k.startswith("idx:")}
    missing: list[LoopFacet] = []
    for facet in facets:
        span = set(facet.span)
        if not span:
            continue
        if span & win_sessions and not span <= win_sessions:
            missing.append(facet)
    return missing


def _aliases_add_recall(query: str, aliases: tuple[str, ...],
                        *, keep_numeric: bool = False) -> bool:
    """The no-op guard (pure): an alias pool that tokenizes to ONLY the
    original query's tokens cannot add recall (the reserved-slot OR contract
    would drop it all) — skip the wasted second pass (C2's guard)."""
    from tortoise.sparse import tokenize_sparse_query
    orig = set(tokenize_sparse_query(query, keep_numeric=keep_numeric))
    if not orig:
        return False
    new_toks: set[str] = set()
    for alias in aliases:
        new_toks.update(tokenize_sparse_query(alias, keep_numeric=True))
    return bool(new_toks - orig)


def loop_expansion_pass(proj: Any, query: str, facets: list[LoopFacet], *,
                        limit: int,
                        keep_numeric: bool = False,
                        excluded_statuses: tuple | None = None,
                        leg_trace: list[dict] | None = None,
                        ) -> dict:
    """The EXPAND step (step 3 over the graph): ONE targeted second sparse
    pass for the missing facets' vocabulary (hard ``LOOP_MAX_EXTRA_PASSES``
    = 1 bound — however many facets the check found incomplete, the loop
    runs at most ONE extra retrieval pass; their alias pools union into that
    single pass's expansion tail).

    Mirrors the A4/C2 second-pass machinery exactly: the facets' alias pools
    fill ONLY ``build_or_query``'s bounded expansion tail (the original
    query tokens keep their slots — the reserved-slot contract), the pass
    is additive recall only, and any fetch/query failure returns
    ``expanded_ids=None`` so the caller keeps the ORIGINAL pool
    byte-identical (the fail-open posture).

    Returns a report dict (never raises):
      ``facet_keys``    — the facets the pass targeted (census order).
      ``expanded_ids`` — the second pass's ranked point ids, None when no
                         pass ran (no additive vocabulary / failure / empty
                         result).
      ``iterations``   — 1 iff an extra retrieval pass actually ran, else 0
                         (the §8 iteration census).
    """
    if limit < 1 or not facets:
        return {"facet_keys": [f.key for f in facets],
                "expanded_ids": None, "iterations": 0}
    from tortoise.sdk import _ENTITY_ALIAS_MAX
    # union the missing facets' vocabulary (anchor names first, then keys)
    # — one deterministic, capped alias pool for the single pass.
    aliases: list[str] = []
    seen: set[str] = set()
    for facet in facets:
        for a in facet.aliases:
            if a in seen:
                continue
            seen.add(a)
            aliases.append(a)
            if len(aliases) >= _ENTITY_ALIAS_MAX:
                break
        if len(aliases) >= _ENTITY_ALIAS_MAX:
            break
    if not aliases or not _aliases_add_recall(
            query, tuple(aliases), keep_numeric=keep_numeric):
        # an alias pool that tokenizes to only the query's own tokens cannot
        # add recall — no wasted identical second pass (C2's guard).
        return {"facet_keys": [f.key for f in facets],
                "expanded_ids": None, "iterations": 0}
    from tortoise.search_engine import run_fts_query
    _trace: list[dict] = []
    try:
        expanded = run_fts_query(
            proj.g, query, entity_type="point", limit=limit,
            excluded_statuses=excluded_statuses,
            keep_numeric=keep_numeric,
            expansion_terms=aliases,
            leg_trace=_trace,
        )
    except Exception:  # noqa: BLE001, RUF100
        logger.warning(
            "C3-1 loop expansion pass failed — keeping the original pool "
            "(fail-open)", exc_info=True)
        return {"facet_keys": [f.key for f in facets],
                "expanded_ids": None, "iterations": 0}
    if leg_trace is not None:
        leg_trace.extend(_trace)
    if not expanded:
        return {"facet_keys": [f.key for f in facets],
                "expanded_ids": None, "iterations": 1}
    return {"facet_keys": [f.key for f in facets],
            "expanded_ids": [pid for pid, _s in expanded],
            "iterations": 1}


def merge_expansion_order(pool: list[dict], added: list[dict],
                          expanded_ids: list[str]) -> list[dict]:
    """The additive union in second-pass relevance order (pure, step 4).

    Mirrors the A4/C2 leg-merge contract at the pool level: the second
    pass is a re-QUERY of the same question with the missing facet's
    vocabulary — its ranked members (base hits re-found by the pass AND
    newly surfaced recovery hits) carry the loop's best relevance signal
    and lead the merged pool in pass order; base hits the pass did not
    re-find (legs the sparse re-query cannot see) keep their base ranks
    appended after. Additive by construction: every base hit survives; ids
    dedupe across the union. The caller applies the session-diverse window
    discipline (:func:`session_diverse_order`) on top.
    """
    by_id = {h["id"]: h for h in pool}
    by_id.update({h["id"]: h for h in added})
    merged: list[dict] = []
    seen: set[str] = set()
    for pid in expanded_ids:
        h = by_id.get(pid)
        if h is not None and pid not in seen:
            merged.append(h)
            seen.add(pid)
    merged.extend(h for h in pool if h["id"] not in seen)
    return merged


def session_diverse_order(items: list[dict], *,
                          window: int = DEFAULT_LOOP_GUARD_WINDOW,
                          per_session_cap: int = DEFAULT_LOOP_SESSION_CAP,
                          session_key: Callable[[dict], str] | None = None,
                          floor_term: bool = False,
                          ) -> list[dict]:
    """The merge discipline (step 4, pure): reorder ``items`` so the top
    ``window`` ranks are session-diverse — no session holds more than
    ``per_session_cap`` of the window.

    Same-session near-dupes are deferred past the window (a monopolizing
    session's points were never capped — §1(2)); the cap yields to
    completeness only when no other session can fill the window (a pool
    that is one session's alone still surfaces its own evidence; such pools
    never fire the loop anyway). Additive by construction: reordering never
    drops an item — the windowed prefix + the deferred tail partition the
    input. Stable: within a session, original order is preserved.

    ``floor_term`` (#2513) adds the LOWER bound this discipline was missing.
    ``per_session_cap`` is only an UPPER bound: once ``window`` slots are held
    by ``ceil(window / per_session_cap)`` or more distinct sessions the
    ADMISSION condition ``len(windowed) < window`` is false for every later
    item, so a session whose best rank is past the window is DEFERRED, never
    admitted — measured on the n=71 multi-session cohort as the 4/0 row of the
    guard-reach census (4 non-monopolised misses, 0 closed; §1 of the #2513
    diagnosis). Called *minimum representation* / a per-group LOWER bound in
    the fair-ranking literature (arXiv 1906.01747 ``l_{v,p}``: at least l
    items of group v among the top p) — the exact dual of the cap we ship
    (arXiv 2103.14000 §coverage-based diversity).

    With ``floor_term`` on, an item from a session holding NO window slot
    takes a slot back from a session holding MORE THAN ONE, by evicting that
    session's lowest-ranked windowed item (whose survivor still represents its
    session — the least information loss available). Four properties hold by
    construction, and the FOURTH (the unavoidable density cost) is why this
    is OFF by default:

    * the window never holds more than ``window`` distinct sessions (the lower
      bound is bounded, not a completeness blocker);
    * ``per_session_cap`` still binds in the arm: an EVICTING session loses a
      slot (``counts[sj] -= 1``) and the ADMITTED session is recorded as
      holding exactly one (``counts[s] = 1``), so the floor term can never let
      a session exceed the cap to buy representation;
    * the deferred tail stays in ORIGINAL RANK order, so a session's own items
      never invert and a downstream per-session re-cap (``dedup_pool``, which
      keeps the first ``max_chunks_per_session`` IN POOL ORDER) still keeps a
      session's best-ranked chunks — not an artifact of the reorder;
    * **the density cost is NOT avoidable, only minimised.** In the SATURATED
      regime (>= ``window`` distinct sessions available) the window has zero
      spare slots, so admitting a new session necessarily drops a duplicate:
      the floor term yields ``window`` distinct sessions, the same count
      ``per_session_cap=1`` yields, and — in that regime, where no duplicate
      survives — there is no "which one goes" choice to make, so the window is
      ELEMENTWISE ``per_session_cap=1``'s. That equality is asserted directly
      by ``test_floor_term_is_bounded_by_the_window_and_spends_the_duplicate_slot``
      rather than quoted as a sample count here, for the same reason the
      displacement rates below are not: a bare "N of N pools" needs its
      generator, pool size and seed to be checkable at all, and this note is
      meant to be re-checkable. Against ``per_session_cap=1`` the difference is
      confined to the UNDER-saturated regime (fewer than ``window`` distinct
      sessions available), where the floor term keeps a session's later item in
      rank order instead of pushing it past the window. ⛔ Against the CAP-ONLY
      GUARD, though, the admitted session SET differs in BOTH regimes: with
      fewer than ``window`` distinct sessions the window can still be full of
      DUPLICATES, so a session ranked past it is admitted here where the
      cap-only guard deferred it (the 4/0 census row above is exactly that
      case). It spends nothing at all while the pool holds no more than
      ``ceil(window / per_session_cap)`` distinct sessions — that number is
      structural, not sampled: a ``window``-slot window at
      ``per_session_cap`` needs at least ``ceil(window / per_session_cap)``
      sessions to fill, so below that threshold nothing is displaced. Above
      it the displacement rate rises with the distinct-session count; no
      ratio is quoted here, because a bare fraction without its generator
      (pool size, session distribution, seed) cannot be re-checked by the
      next reader, which is the failure this note exists to avoid.
      It is therefore NOT a density-free alternative to
      ``per_session_cap=1`` — the #2513 §4 projection for ``cap=1`` (residual
      10->5, recall_all@5 0.8592->0.9296, evidence density lost on 20/71) is
      the ceiling for this lever too. Arming it requires that sealed A/B
      measurement.
    """
    if not items:
        return []
    window = max(1, min(window, len(items)))
    counts: dict[str, int] = {}
    # Both lists carry ``(original_rank, item)`` pairs, not bare items. The rank
    # is what the tail re-sort needs, and it CANNOT be re-derived afterwards:
    # a ``{id(h): k for k, h in enumerate(items)}`` map keeps only the LAST
    # index of an object that appears twice, so the earlier occurrence sorts as
    # if it sat at the later rank and the tail INVERTS — the exact failure the
    # sort exists to prevent. Carrying the index the item was read AT makes a
    # pool with a repeated object agree with its content-equal twin.
    windowed: list[tuple[int, dict]] = []
    deferred: list[tuple[int, dict]] = []
    for k, h in enumerate(items):
        s = _session_of(h, session_key)
        n = counts.get(s, 0)
        if len(windowed) >= window:
            # FLOOR TERM: admit a session with no window slot by taking one
            # back from a session that holds more than one. Evict that
            # session's LOWEST-ranked windowed item — the least information
            # loss available, since the survivor still represents its session.
            if floor_term and n == 0:
                for j in range(len(windowed) - 1, -1, -1):
                    sj = _session_of(windowed[j][1], session_key)
                    if counts.get(sj, 0) > 1:
                        deferred.append(windowed.pop(j))
                        counts[sj] -= 1
                        windowed.append((k, h))
                        counts[s] = 1
                        break
                else:
                    deferred.append((k, h))
            else:
                deferred.append((k, h))
        elif n >= per_session_cap:
            deferred.append((k, h))
        else:
            windowed.append((k, h))
            counts[s] = n + 1
    # An eviction pushes a LOW-ranked item into the tail behind items deferred at
    # higher ranks. Restore original rank order when the arm is on, so no
    # session's own items invert and the re-cap below still keeps its best ones.
    # Sorting the carried rank (not a re-derived one) is identity-safe.
    if floor_term:
        deferred.sort(key=lambda pair: pair[0])
    # Under-filled window (every remaining item belongs to a capped session):
    # fill from the deferred tail in original order — the cap is a diversity
    # guard, never a completeness blocker.
    while len(windowed) < window and deferred:
        windowed.append(deferred.pop(0))
    return [h for _, h in windowed] + [h for _, h in deferred]
