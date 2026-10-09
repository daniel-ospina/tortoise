"""#2886 — deterministic temporal aggregation over admitted dated events.

The census ``frequency/count`` class (12/133 rows,
``tests/_assembly_census.json``) is, MEASURED, not a counting class: every
row is date arithmetic over two dated events — ``before``-offset, interval,
or duration — collapsed into one bucket by the census's lexical label.
Two independent pieces of committed evidence:

* the class's own question texts (all 12 match ``how many <time-unit>``;
  none matches an explicit frequency surface — "how many times"/"how
  often");
* the assembler's own shape router (``tests/test_assembly_pure.py``) already
  files ``370a8ff4`` — a row labelled ``frequency/count`` — as an
  interval-since twin, and pins the class as a measured-negative.

This module is the deterministic resolution core for that class, AND for
the literal frequency/count surface the issue names. It is deliberately
NOT folded into ``tortoise/assembly.py`` (#2165): the assembler locates
subjects through the graph; this core is a downstream arithmetic/tally step
over the events the reader already ADMITTED. The caller passes the admitted
dated events (or the two resolved anchor dates) and gets a number back.

Two paths, both pure functions over admitted dated events — no graph, no
model, no IO, no clock:

* COUNT / TOTAL — tally DISTINCT dated events once. The multi-session
  restatement trap (the same event restated in a later session) is closed
  by a deterministic event-identity key: an explicit event id, else the
  normalized content, else the repo's committed conservative paraphrase
  band (``extractor_v2.fold_allowed`` + ``NOOP_MIN_OVERLAP``). Events are
  canonicalised in ``(session_date, id, normalized content)`` order — a
  TOTAL key, never an input-index tie-break — and restatements are resolved
  as an order-independent clustering (union-find over the pairwise fold
  relation, i.e. its transitive closure), so the earliest articulation wins
  and the result does not depend on input order. TOTAL additionally sums
  each distinct event's span (``gpt4_a1b77f9c`` — weeks across three
  books).
* DIFFERENCE — calendar arithmetic over two admitted dated anchors:
  interval ("how many days between A and B"), before-offset ("how many days
  before B did A happen"), duration ("how many days did it take to finish
  X"). Whole-unit, floor-to-completed semantics; signed (``end - start``).

Design invariants (mirroring the assembly lane's R1/R13 discipline):

* **Abstain, never guess** — an unresolvable question, a missing anchor, or
  an undated event yields ``value=None`` with a ``reason``; the caller falls
  through to the reader lane byte-identically (the reader keeps its #2013
  mandate for conversion-bound cases).
* **Bound + deterministic** — the event input is capped (``MAX_EVENTS``);
  the same inputs always produce the same output.

Identity caveat (documented, never silent): content-identical wording on two
DIFFERENT occasions is folded to ONE event only for IDENTITY-LESS rows (the
restatement rule the issue names). The fold is never applied across explicit
ids: two rows that carry DISTINCT ``event_id`` values are always DISTINCT
events, even when their content is identical, and their occurrences are
counted (and their spans summed) separately. A caller that knows the rows
are genuinely distinct occurrences (e.g. a repeated activity) must pass
distinct ``event_id`` values — the explicit id is the strongest identity and
always wins.

Residual (NOT covered here — recorded so it is not mistaken for verified):

* The paraphrase band is conservative (D12/O4): a restatement that negates,
  re-conditions or substitutes its claim stays a SEPARATE event. That is
  the repo's committed fold discipline, not a defect here.
* The reader prompt's ``_MULTI_SESSION_FRAGMENT`` already instructs the LLM
  to count distinct events; this module is the deterministic equivalent for
  the arithmetic/tally sub-step, never a silent replacement of the reader
  (a reader-model swap stays #2013-gated).
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any

from .extractor_v2 import NOOP_MIN_OVERLAP, fold_allowed

__all__ = [
    "MAX_EVENTS",
    "EventTally",
    "TemporalAggregateIntent",
    "TemporalAggregateKind",
    "TemporalResolution",
    "as_date",
    "classify_temporal_aggregate",
    "count_distinct_events",
    "difference_in_unit",
    "resolve_temporal_aggregate",
]

#: Bound on the admitted-event input. A tally over an unbounded population is
#: never a bounded claim; a caller passing more degrades to abstention
#: (reason ``"capped"``) rather than publishing a truncated count.
MAX_EVENTS = 2000

#: Calendar units the difference/total paths understand (whole-unit
#: floor-to-completed semantics).
_UNITS: tuple[str, ...] = ("days", "weeks", "months", "years")
#: Singular → plural normalization for the unit token captured by the
#: morphology.
_UNIT_FORMS: dict[str, str] = {
    "day": "days", "days": "days",
    "week": "weeks", "weeks": "weeks",
    "month": "months", "months": "months",
    "year": "years", "years": "years",
}


class TemporalAggregateKind(StrEnum):
    """The resolved aggregation shape of a temporal question.

    * ``count`` — a frequency/count surface ("how many times", "how often"):
      tally DISTINCT events.
    * ``total`` — a summed span across >1 event ("how many weeks in total
      spent on A and B"): sum the DISTINCT events' spans.
    * ``interval`` — elapsed time between two dated anchors ("between A and
      B", "since A when B").
    * ``before-offset`` — how far one event precedes another ("how many days
      before B did A happen").
    * ``duration`` — the length of one bounded state ("how many days did it
      take to finish X", "how many weeks have I been X when Y").
    """

    COUNT = "count"
    TOTAL = "total"
    INTERVAL = "interval"
    BEFORE_OFFSET = "before-offset"
    DURATION = "duration"


@dataclass(frozen=True)
class TemporalAggregateIntent:
    """The deterministic classification of one question.

    ``kind`` — the resolved :class:`TemporalAggregateKind`. ``unit`` — the
    calendar unit for the difference/total shapes (``days``/``weeks``/
    ``months``/``years``) or None for a bare count / bare "how long".
    ``distinct`` — whether the shape requires each event counted once (True
    for count/total; None for the arithmetic shapes, which need exactly two
    anchors).
    """

    kind: TemporalAggregateKind
    unit: str | None = None
    distinct: bool | None = None


# ── morphology (ordered high-precision; first match wins) ─────────────────

# Explicit frequency/count surface — the literal issue class. "how many
# times", "how often", "how frequently", and the count-of-times phrasing.
_RE_COUNT = re.compile(
    r"\bhow\s+(?:many\s+times|often|frequently)\b"
    r"|\b(?:count|number)\s+of\s+times\b",
    re.IGNORECASE)
# Summed-span surface — "in total"/"altogether"/"combined" + a time unit.
# Checked BEFORE the unit-prefixed shapes so "how many weeks in total do I
# spent on A and B" is a TOTAL (sum across events), not one duration.
_RE_TOTAL = re.compile(
    r"\bhow\s+many\s+(?P<unit>days?|weeks?|months?|years?)\b[^?]*?"
    r"\b(?:in\s+total|in\s+all|altogether|combined|total)\b",
    re.IGNORECASE)
# before-offset: "how many <unit> before/prior to/earlier than <X> …"
_RE_BEFORE_OFFSET = re.compile(
    r"\bhow\s+many\s+(?P<unit>days?|weeks?|months?|years?)\s+"
    r"(?:before|prior\s+to|earlier\s+than)\b",
    re.IGNORECASE)
# interval: "how many <unit> (had|have|…)? (passed|elapsed)? between|since",
# including the "since A when B" and leading-"Between A and B, how many …"
# forms. The `[^?]*?` gap lets the verb phrase fall in between.
_RE_INTERVAL = re.compile(
    r"\bhow\s+many\s+(?P<unit>days?|weeks?|months?|years?|time)\b[^?]*?"
    r"\b(?:between|since)\b"
    r"|^\s*between\b[^?]*?,\s*how\s+many\s+"
    r"(?P<unit2>days?|weeks?|months?|years?)\b",
    re.IGNORECASE)
# duration: "how many <unit> did I spend/take", "how many <unit> have I been",
# and the "how long" span surface.
_RE_DURATION = re.compile(
    r"\bhow\s+many\s+(?P<unit>days?|weeks?|months?|years?)\s+"
    r"(?:did|does|do)\s+(?:i|you|we|they|it|he|she)\s+"
    r"(?:spend|take|last|stay)\b"
    r"|\bhow\s+many\s+(?P<unit2>days?|weeks?|months?|years?)\s+"
    r"(?:have|had|has)\s+(?:i|you|we|they|he|she)\s+been\b"
    r"|\bhow\s+long\b",
    re.IGNORECASE)


def _unit_of(match: re.Match[str] | None) -> str | None:
    """The normalized plural unit captured by one of the named groups."""
    if match is None:
        return None
    groups = match.groupdict()
    for name in ("unit", "unit2"):
        if groups.get(name):
            return _UNIT_FORMS.get(groups[name].lower())
    return None


def classify_temporal_aggregate(
    question: str | None,
) -> TemporalAggregateIntent | None:
    """Deterministic classification of a temporal question into an
    aggregation shape (``None`` when the question is not a temporal
    count/total/difference).

    Ordered, pinned rule set — first match wins:

    1. empty → None.
    2. explicit frequency/count surface → COUNT.
    3. summed-span surface ("in total" + a time unit) → TOTAL.
    4. ``how many <unit> before/prior to/earlier than …`` → BEFORE_OFFSET.
    5. ``how many <unit> … between|since`` (incl. ``between A and B, how
       many …``) → INTERVAL.
    6. ``how many <unit> did I spend/take`` / ``how many <unit> have I been``
       / ``how long`` → DURATION.
    7. anything else → None.

    The classification table is pinned by
    ``tests/test_temporal_aggregation.py`` — a vocabulary change is a
    deliberate, reviewed diff.
    """
    if not question or not str(question).strip():
        return None
    q = " ".join(str(question).split())
    if _RE_COUNT.search(q):
        return TemporalAggregateIntent(
            TemporalAggregateKind.COUNT, unit=None, distinct=True)
    m = _RE_TOTAL.search(q)
    if m:
        return TemporalAggregateIntent(
            TemporalAggregateKind.TOTAL, unit=_unit_of(m), distinct=True)
    m = _RE_BEFORE_OFFSET.search(q)
    if m:
        return TemporalAggregateIntent(
            TemporalAggregateKind.BEFORE_OFFSET, unit=_unit_of(m),
            distinct=None)
    m = _RE_INTERVAL.search(q)
    if m:
        return TemporalAggregateIntent(
            TemporalAggregateKind.INTERVAL, unit=_unit_of(m), distinct=None)
    m = _RE_DURATION.search(q)
    if m:
        return TemporalAggregateIntent(
            TemporalAggregateKind.DURATION, unit=_unit_of(m), distinct=None)
    return None


# ── date normalization ────────────────────────────────────────────────────

def as_date(value: Any) -> date | None:
    """Parse an ISO date/datetime (``Z`` suffix / space separator tolerated)
    to a ``date``. Date-only: the calendar day carried by the string is used
    (the eval's session dates are date-only). The v2-lane undated sentinel
    (pre-1970) is NOT a usable date. Returns None for absent/garbage values —
    never raises."""
    if value in (None, ""):
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        d = date.fromisoformat(s[:10])
    except (ValueError, TypeError):
        return None
    if d <= date(1970, 1, 1):
        return None
    return d


# ── distinct-event tally (the restatement trap) ───────────────────────────

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^a-z0-9 ]")


def _content_key(event: Mapping[str, Any]) -> str:
    """Normalized content of an admitted event (lowercase alnum tokens,
    whitespace-collapsed). Empty when the event carries no content text."""
    raw = (event.get("content") or event.get("text")
           or event.get("statement") or "")
    text = _PUNCT_RE.sub(" ", str(raw).lower())
    return _WS_RE.sub(" ", text).strip()


def _event_id(event: Mapping[str, Any]) -> str:
    for key in ("event_id", "eventId", "id", "point_id"):
        val = event.get(key)
        if val not in (None, ""):
            return str(val)
    return ""


def _event_date(event: Mapping[str, Any]) -> date | None:
    for key in ("session_date", "date", "start_date", "started_at",
                "created_at"):
        d = as_date(event.get(key))
        if d is not None:
            return d
    return None


def _overlap_ratio(a: str, b: str) -> float:
    """Token-overlap ratio mirroring ``extractor_v2._overlap_ratio``'s
    near-symmetric guard (max/min < 1.5) so an asymmetric token-subset is
    never folded. Kept local to avoid importing a private symbol."""
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    lo, hi = min(len(ta), len(tb)), max(len(ta), len(tb))
    if hi / lo >= 1.5:
        return 0.0
    return len(ta & tb) / lo


def _restatement(content: str, prior: str) -> bool:
    """True when ``content`` is the SAME event as ``prior``: byte-identical
    after normalization, or a conservative paraphrase — overlap ≥ the
    committed NOOP band AND ``fold_allowed`` (no negation/condition/subject
    substitution)."""
    if not content or not prior:
        return False
    if content == prior:
        return True
    return (_overlap_ratio(content, prior) >= NOOP_MIN_OVERLAP
            and fold_allowed(content, prior))


@dataclass(frozen=True)
class EventTally:
    """The distinct-event tally result.

    ``n_events`` — distinct events counted once. ``n_input`` — raw input
    rows. ``collapsed`` — ``n_input - n_events`` (restatements folded).
    ``keys`` — the canonical event keys in tally order. ``unit`` — the
    reported unit for a TOTAL span (None for a bare count). ``total`` — the
    summed span for TOTAL (None for a bare count). ``reason`` — set when
    the tally abstained (``capped``), else None.
    """

    n_events: int
    n_input: int
    collapsed: int
    keys: tuple[str, ...] = ()
    unit: str | None = None
    total: int | None = None
    reason: str | None = None


def _canonical_order(
    events: Sequence[Mapping[str, Any]],
) -> list[tuple[int, Mapping[str, Any]]]:
    """Stable ``(index, event)`` order keyed by ``(session_date, event_id,
    normalized content)`` — a TOTAL key, so two identity-less events sharing
    a date are separated by their content and NEVER by the caller's input
    index. Undated events sort last. Rows still tied after the content key
    are structurally identical (same date, same id, same content) and so
    belong to the same identity cluster; their relative order is immaterial
    to the tally."""
    def key(item: tuple[int, Mapping[str, Any]]):
        _i, e = item
        content = _content_key(e)
        d = _event_date(e)
        return (0, d.isoformat(), _event_id(e), content) if d is not None \
            else (1, "", _event_id(e), content)

    return sorted(enumerate(events), key=key)


class _DisjointSet:
    """Minimal union-find for order-independent restatement clustering.

    The cluster a row lands in must not depend on which prior row it happened
    to be compared against, so the fold relation is resolved as its
    transitive closure (union every pair that folds) rather than a greedy
    scan over already-kept rows. ``union`` roots at the smaller index so the
    internal structure is deterministic, though callers derive the cluster's
    reported key from canonical order, not from the root.
    """
    __slots__ = ("_parent",)

    def __init__(self, n: int) -> None:
        self._parent = list(range(n))

    def find(self, x: int) -> int:
        parent = self._parent
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            if rb < ra:
                ra, rb = rb, ra
            self._parent[rb] = ra


def _span_days(event: Mapping[str, Any]) -> int | None:
    """Per-event span in days: ``end - start`` from the event's own dated
    props (end falls back to the question date only when the caller supplied
    it as ``question_date``). Returns None when either bound is
    missing/unparseable."""
    start = None
    for key in ("start_date", "started_at", "session_date", "date",
                "created_at"):
        start = as_date(event.get(key))
        if start is not None:
            break
    end = None
    for key in ("end_date", "ended_at", "completed_at", "question_date"):
        end = as_date(event.get(key))
        if end is not None:
            break
    if start is None or end is None:
        return None
    return (end - start).days


def _days_to_unit(days: int, unit: str | None) -> int:
    if unit in (None, "days"):
        return days
    if unit == "weeks":
        return days // 7
    if unit == "months":
        # Defensive only: count_distinct_events rejects a months total with
        # an abstention before reaching here. Kept as a programming-error
        # guard rather than a silent approximation.
        raise ValueError("cannot express a day sum in calendar months")
    if unit == "years":
        return days // 365
    raise ValueError(f"cannot express a day count in unit {unit!r}")


def count_distinct_events(
    events: Iterable[Mapping[str, Any]],
    *,
    unit: str | None = None,
    total: bool = False,
) -> EventTally:
    """Tally distinct admitted dated events once (the multi-session
    restatement trap closed).

    Deterministic canonicalisation: events are ordered by
    ``(session_date, event_id, normalized content)`` — a total key, so
    same-date / undated identity-less rows never tie-break on input index —
    and identity clusters are resolved order-independently (union-find over
    the pairwise fold relation), so the count does not depend on input
    order. Identity is exclusive: an explicit event id collapses ONLY a
    repeated occurrence of the same id,
    and content-based identity (identical wording, or a conservative-
    paraphrase restatement under ``fold_allowed`` + ``NOOP_MIN_OVERLAP``)
    folds ONLY identity-less rows. Content never overrides two distinct
    explicit ids — distinct ids with identical content stay SEPARATE events
    (a repeated activity is counted once per occurrence).

    ``total=True`` additionally sums each distinct event's span in the
    requested ``unit``; an event with no parsable span contributes 0 to the
    sum but still counts as one distinct event. A ``total=True`` request in
    a unit that cannot be inverted from a day sum (``months``) abstains
    (``reason="no_unit"``) rather than raising — the never-guess contract.
    Input over ``MAX_EVENTS`` abstains (``reason="capped"``); an empty
    input yields ``n_events=0`` (a real zero — not a truncation).
    """
    rows = list(events)
    if len(rows) > MAX_EVENTS:
        return EventTally(0, len(rows), 0, reason="capped", unit=unit)
    if total and unit not in (None, "days", "weeks", "years"):
        # A day sum cannot be inverted into calendar months — abstain
        # (never raise, never approximate); the span is not published.
        return EventTally(0, len(rows), 0, reason="no_unit", unit=unit)
    ordered = _canonical_order(rows)

    # ── cluster identity, order-independently ─────────────────────────────
    # Union-find over the two identity relations: same explicit id, and the
    # symmetric restatement relation among IDENTITY-LESS content rows. An
    # event that carries an explicit id is anchored by that id and neither
    # folds into nor absorbs a content match, so the fold can never override
    # two distinct explicit ids (the occurrence-undercount bug). Every fold
    # pair is precomputed and unioned (transitive closure), so the cluster —
    # and therefore the tally — does not depend on the order rows arrive or
    # on which prior a later row happens to be compared against.
    dsu = _DisjointSet(len(rows))
    first_id: dict[str, int] = {}
    #: ``(index, content, token-set)`` for IDENTITY-LESS content rows; the
    #: token set is precomputed once so the O(n²) fold scan does not re-split
    #: every string on every comparison.
    content_rows: list[tuple[int, str, frozenset[str]]] = []
    for i, event in ordered:
        eid = _event_id(event)
        if eid:
            if eid in first_id:
                dsu.union(first_id[eid], i)
            else:
                first_id[eid] = i
            continue
        content = _content_key(event)
        if content:
            content_rows.append((i, content, frozenset(content.split())))
    for a in range(len(content_rows)):
        ia, ca, ta = content_rows[a]
        for b in range(a + 1, len(content_rows)):
            ib, cb, tb = content_rows[b]
            la, lb = len(ta), len(tb)
            lo, hi = (la, lb) if la < lb else (lb, la)
            if not lo or hi / lo >= 1.5:
                continue
            if len(ta & tb) / lo < NOOP_MIN_OVERLAP:
                continue
            # Overlap band already cleared; ``_restatement`` re-checks it and
            # applies the conservative fold gate (byte-identical or
            # ``fold_allowed``). The prefilter above just avoids the gate's
            # cost on pairs that cannot possibly clear the band.
            if _restatement(cb, ca):
                dsu.union(ia, ib)

    # One key per cluster, taken from its earliest canonical member (so the
    # earliest articulation still wins) — deterministic and input-order
    # independent.
    keys: list[str] = []
    cluster_seen: dict[int, str] = {}
    span_days = 0
    for i, event in ordered:
        root = dsu.find(i)
        if root in cluster_seen:
            continue
        eid = _event_id(event)
        content = _content_key(event)
        if eid:
            key = f"id:{eid}"
        elif content:
            key = f"content:{content}"
        else:
            # neither id nor content — not identifiable; keep it as its own
            # cluster (never silently drop admitted evidence). The rank is a
            # function of canonical position, so it is input-order stable.
            key = f"row:{len(keys)}"
        cluster_seen[root] = key
        keys.append(key)
        if total:
            span_days += _span_days(event) or 0
    n_events = len(keys)
    if total:
        return EventTally(n_events, len(rows), len(rows) - n_events,
                          keys=tuple(keys), unit=unit,
                          total=_days_to_unit(span_days, unit))
    return EventTally(n_events, len(rows), len(rows) - n_events,
                      keys=tuple(keys), unit=unit)


# ── calendar difference over two admitted anchors ─────────────────────────

def _completed_months(start: date, end: date) -> int:
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return months


def _completed_years(start: date, end: date) -> int:
    years = end.year - start.year
    if (end.month, end.day) < (start.month, start.day):
        years -= 1
    return years


def difference_in_unit(start: date | str | None, end: date | str | None,
                       unit: str | None) -> int | None:
    """Whole-unit calendar difference ``end - start`` (signed), or None when
    either bound is missing/unparseable.

    Semantics: ``days`` is the exact day count; ``weeks`` floors to
    completed units; ``months`` is the completed-calendar-month difference
    (the ``end.day < start.day`` adjustment); ``years`` floors to completed
    years. An unknown unit raises ``ValueError`` (a programming error —
    never a silent guess).
    """
    if unit not in _UNITS:
        raise ValueError(f"unknown unit {unit!r} — expected one of {_UNITS}")
    a = as_date(start)
    b = as_date(end)
    if a is None or b is None:
        return None
    if unit == "days":
        return (b - a).days
    if unit == "weeks":
        return (b - a).days // 7
    if unit == "months":
        return _completed_months(a, b)
    return _completed_years(a, b)


# ── one-call resolution seam ──────────────────────────────────────────────

@dataclass(frozen=True)
class TemporalResolution:
    """The resolved answer to a temporal aggregation question.

    ``kind`` — the resolved shape (None when the question is not a temporal
    aggregation). ``value`` — the deterministic answer, or None when the
    path abstained. ``unit`` — the reported unit. ``method`` — which path
    produced the value (``count_distinct``/``sum_distinct``/``difference``);
    None when unresolved. ``n_events`` — distinct events for the tally
    paths. ``reason`` — why the path abstained (never-silent vocabulary).
    ``intent`` — the classification (None for a non-temporal question).
    """

    kind: TemporalAggregateKind | None
    value: int | None
    unit: str | None
    method: str | None
    n_events: int | None = None
    reason: str | None = None
    intent: TemporalAggregateIntent | None = None


def resolve_temporal_aggregate(
    question: str | None,
    *,
    events: Iterable[Mapping[str, Any]] = (),
    start: date | str | None = None,
    end: date | str | None = None,
    unit: str | None = None,
) -> TemporalResolution:
    """The named deterministic resolution path (hermetic).

    Dispatch:

    * COUNT → :func:`count_distinct_events` → ``n_events``.
    * TOTAL → :func:`count_distinct_events` with ``total=True`` → summed
      span across distinct events.
    * INTERVAL / BEFORE_OFFSET / DURATION → :func:`difference_in_unit` over
      the caller-supplied anchors (``start``/``end``); missing anchors
      abstain (``reason="no_anchors"``) so the reader lane keeps the case.

    ``unit`` overrides the classified unit (a caller with an explicit unit —
    e.g. the eval's per-question answer unit — is authoritative).

    Empty event input abstains (``reason="no_events"``) for COUNT/TOTAL —
    an empty admitted set is not a measured zero, and the never-guess
    contract forbids reporting one. The tally layer
    (:func:`count_distinct_events`) still returns ``n_events=0`` for empty
    input; only the resolver refuses to treat it as an answer.
    """
    intent = classify_temporal_aggregate(question)
    if intent is None:
        return TemporalResolution(
            None, None, None, None, reason="not_temporal", intent=None)
    eff_unit = unit or intent.unit
    if intent.kind is TemporalAggregateKind.COUNT:
        tally = count_distinct_events(events)
        if tally.reason:
            return TemporalResolution(
                intent.kind, None, None, None, n_events=None,
                reason=tally.reason, intent=intent)
        if tally.n_input == 0:
            # An EMPTY admitted-event set is not a measured zero: with no
            # events there is nothing to tally, so abstain and let the
            # reader lane keep the case (never guess zero). A real zero —
            # e.g. "how many times did I skydive?" in a session that was
            # scanned and contained no skydive — is a caller-side judgement
            # (pass evidence), not something this pure core can infer.
            return TemporalResolution(
                intent.kind, None, None, None, n_events=None,
                reason="no_events", intent=intent)
        return TemporalResolution(
            intent.kind, tally.n_events, None, "count_distinct",
            n_events=tally.n_events, intent=intent)
    if intent.kind is TemporalAggregateKind.TOTAL:
        if eff_unit not in (None, "days", "weeks", "years"):
            # a day sum cannot be inverted into calendar months — abstain
            # rather than approximate (the never-guess contract).
            return TemporalResolution(
                intent.kind, None, eff_unit, None, reason="no_unit",
                intent=intent)
        tally = count_distinct_events(events, unit=eff_unit, total=True)
        if tally.reason:
            return TemporalResolution(
                intent.kind, None, tally.unit, None, n_events=None,
                reason=tally.reason, intent=intent)
        if tally.n_input == 0:
            # Same never-guess rule as COUNT: no admitted events → abstain,
            # never publish a 0 span.
            return TemporalResolution(
                intent.kind, None, tally.unit, None, n_events=None,
                reason="no_events", intent=intent)
        return TemporalResolution(
            intent.kind, tally.total, tally.unit, "sum_distinct",
            n_events=tally.n_events, intent=intent)
    # INTERVAL / BEFORE_OFFSET / DURATION — arithmetic over two anchors
    if eff_unit not in _UNITS:
        return TemporalResolution(
            intent.kind, None, eff_unit, None, reason="no_unit",
            intent=intent)
    value = difference_in_unit(start, end, eff_unit)
    if value is None:
        return TemporalResolution(
            intent.kind, None, eff_unit, None, reason="no_anchors",
            intent=intent)
    return TemporalResolution(
        intent.kind, value, eff_unit, "difference", intent=intent)
