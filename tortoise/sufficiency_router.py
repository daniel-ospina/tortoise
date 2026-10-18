"""§12.4 the SUFFICIENCY ROUTER + §12.5 the ESCALATION WRITE-BACK (#5009).

WHY THIS MODULE EXISTS
======================
``docs/architecture/STORAGE-ARCHITECTURE.md`` §12.4 makes one rule the
load-bearing answer to *"how do we keep the RAM bill from exploding?"*:

    Escalate on SUFFICIENCY, not on query type.

*"What kind of query is this?"* is a classifier that must be trained, drifts,
and is wrong at the boundary. *"Did the cheap tier answer it?"* is a **measured
property of the answer.** §12.8 item 3 names the artifact (§12.4's router) and
item 4 names the half that compounds (§12.5's write-back):

    "Build the write-back — otherwise escalation is a permanent tax rather
     than an investment."

This module is BOTH halves' decision layer, and it is deliberately the
*decision* only. ``tortoise/residency_router.py`` (interface D, #7953) is the
sibling — a second, distinct router over a different fact — and it already
CONSUMES a sufficiency verdict (``route_residency(sufficient=...)``) that
nothing produced until this module. The two compose; they do not overlap.

WHAT IS DECIDED HERE, AND WHAT IS NOT
=====================================
DECIDED (pure, no I/O, no clock, no model, no graph):

* **the sufficiency verdict** — a three-state fact read off the evidence
  already in hand (``assess_sufficiency``);
* **the bounded escalation trigger** — one route, and a budget that cannot be
  walked twice (``route_sufficiency``);
* **the write-back plan** — verified findings become derived summary units that
  carry their own span, cannot promote a claim's class, and dedupe by identity
  so a repeat escalation is not paid for twice (``plan_writeback``).

NOT DECIDED (deliberately, and each has a named owner):

* **the tier count.** §12.3 records TWO tiers, but that fold was made by the
  design and *not* ruled on by the owner — this issue carries it as OPEN. So
  this module never names a tier: it names the §12.4 repair (``escalate-tier``,
  the same route word the residency router already uses) and takes the budget
  as a caller parameter. Committing "Tier 2" here would settle an open ruling
  by accident.
* **the thresholds.** Every measure takes its threshold from the CALLER and
  there are no defaults. The design's method is draft → run → look → refine
  (§14.1 O5) and its numbers are hand-set on real sessions — a default here
  would be a target nobody chose (§14.1 O2) wearing a constant's clothes.
* **the walk itself, and the Tier-1 store.** No tier store exists and no
  reader calls this yet. §12.5's persistence seam is the caller's; this module
  emits the plan and performs no I/O, so it cannot become a second writer.
* **the premise measurement.** #5009's own evidence discipline says *"whether
  our queries are actually answered badly by a cheap tier … precedes the
  build"*. That measurement needs this verdict to exist (you cannot measure
  sufficiency before it has a definition), which is why the decision layer is
  the right first slice — and why the read path is NOT wired here.

THE THREE-STATE VERDICT — why not a boolean
===========================================
``undetermined`` is a first-class member, never a default:

* an **unassessed** read (no threshold supplied, or a provenance field the
  caller never fetched) must not score as ``insufficient`` — that would
  escalate on a measurement that was never taken, and re-pay forever;
* it must not score as ``sufficient`` either — that is the "silent ok" that
  §14.3 V4 exists to kill.

So ``undetermined`` yields ``sufficient=None`` — the exact shape
``route_residency`` already accepts — and the router serves, which is the
design's default answer (§12.3: Tier 1 is the DEFAULT, the deeper tier is
escalation-only). Same polarity as §17.3's ``marker=None`` → *not assessed*,
never ``hot``.

⚠️ **Hand the ROUTE's ``sufficient`` to ``route_residency``, not the verdict's.**
The verdict reports what was MEASURED; the route reports what the bound will PAY
FOR (``SufficiencyRoute.sufficient``). ``route_residency`` escalates on
``sufficient is False`` unconditionally, so passing the raw verdict re-walks a
tier ``route_sufficiency`` just refused to walk.

⛔ **RESIDENCY OUTRANKS SUFFICIENCY (§17.3), enforced here too.** A read the
cache could not see makes *"insufficient"* unestablished, so
``assess_sufficiency`` REFUSES to judge when the caller has not established
residency (``resident`` is ``None`` or ``False``). A residency miss is repaired
by fetching the record, never by walking a tier. The guard is an ALLOW-LIST
(``resident is not True``) with a type check in front of it — the check is the
load-bearing half (an identity test against ``False`` lets a JSON column's ``0``
through as if residency had been established, which turns the strongest guard
here into a no-op in the expensive direction), and the allow-list is the
defence behind it.

⛔ **NO QUERY-CLASS INPUT — pinned structurally.** The router takes no
``query``, ``query_type``, ``complexity`` or ``intent`` argument, and a test
greps the signature so a future lane cannot turn this into the classifier
§12.4 rejects without reddening. ``anchors`` are **the entities the question
references**, already resolved — a coverage measurement over the answer, not a
prediction about the question. Each is matched as a WORD, not an infix: a bare
substring test scores ``AI`` covered on *"he said the main thing"*, which is a
confident ``sufficient`` produced by a measure that never measured. The word
boundary applies to anchors whose characters are ASCII word characters; an
anchor in a script with no word separators (CJK, Thai) is matched as a
substring, because there adjacency is not a boundary and ``(?<!\\w)`` could never
hold — that would report zero coverage for every such entity.

**Provenance is read from the shape the read path emits.** ``SearchResult``
carries ``source_ref`` as an ATTRIBUTE and nests it as ``provenance.source``
on the wire (``to_dict()``, the #3837 additive contract) — both are read, so
the measure has a producer on the one shape every caller actually holds.

THE WRITE-BACK'S PROVENANCE DISCIPLINE (§12.5 · extractor §2.4.2)
================================================================
A summary unit written back to the cheap tier:

* **carries its own span** (``SourceSpan``) or it is an unattributable belief —
  a finding with no span is REFUSED, never downgraded to a summary without one;
* **never replaces the raw fact.** There is no field on ``SummaryUnit`` that can
  name a superseded/replaced source and no field that carries the raw payload:
  the unit holds an ADDRESS, and a test refuses a future field named like a
  payload or a supersession. The raw log stays immutable.
* **never promotes a class** — §12.4's *"incorrect promotion: an episodic fact
  promoted into a semantic rule"* is refused by construction, because the unit's
  class is the finding's class and ``promote_to`` must either be ``None`` or
  equal to it. (§12.7's entry of the same NAME is the ROUTING failure — *"the
  router learning to fetch from the wrong tier"* — a different defect; the
  class sense is §12.4's.)
* **dedupes by identity** — the key is content-addressed over (span, normalised
  claim), so the SAME verified finding written back twice is ONE unit. That is
  what makes the escalation an investment rather than a repeating tax; its
  absence is the published *"no attribution loop"* failure (§12.7).

WHAT THIS MODULE DELIBERATELY IS NOT
====================================
* **Not the residency router.** A cache miss is not an insufficiency (§17.3):
  that decision is ``tortoise/residency_router.py`` and this module never emits
  its ``fetch-record`` route.
* **Not a status term.** §17.3 fixes the read-status vocabulary at four terms
  (``tortoise/status_vocabulary.py``); a verdict and a route are *directives*,
  not statuses, and nothing here mints a fifth.
* **Not a writer.** No graph handle, no store, no ``record_*`` — the plan is
  the output.
* **No public surface change.** No MCP tool and no public ``TortoiseSDK``
  method is added, removed or renamed.
"""
from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .residency_router import ROUTE_ESCALATE_TIER, ROUTE_FETCH_RECORD, ROUTE_SERVE, ROUTES

__all__ = [
    "EVIDENCE_PROVENANCE_KEY",
    "EVIDENCE_PROVENANCE_WIRE_KEY",
    "INSUFFICIENT",
    "MEASURES",
    "MEASURE_ANCHOR_COVERAGE",
    "MEASURE_PROVENANCE_COVERAGE",
    "REASONS",
    "REASON_ALREADY_ESCALATED",
    "REASON_ANCHORS_UNCOVERED",
    "REASON_BUDGET_EXHAUSTED",
    "REASON_EVIDENCE_EMPTY",
    "REASON_MEASURES_MET",
    "REASON_NO_MEASURE",
    "REASON_PROVENANCE_THIN",
    "REASON_PROVENANCE_UNASSESSED",
    "REASON_RESIDENT_MISS",
    "REASON_RESIDENT_UNASSESSED",
    "REFUSALS",
    "REFUSAL_CLASS_CONFLICT",
    "REFUSAL_NO_CLAIM",
    "REFUSAL_NO_PROVENANCE",
    "REFUSAL_PROMOTION",
    "REFUSAL_UNVERIFIED",
    "ROUTES",
    "SPAN_OFFSET_MAX",
    "SUFFICIENT",
    "UNDETERMINED",
    "VERDICTS",
    "SourceSpan",
    "SufficiencyRoute",
    "SufficiencyVerdict",
    "SummaryUnit",
    "VerifiedFinding",
    "WritebackPlan",
    "WritebackRefusal",
    "assess_sufficiency",
    "evidence_provenance",
    "evidence_record",
    "plan_writeback",
    "route_sufficiency",
    "unit_key",
]

# ── the verdict vocabulary ──────────────────────────────────────────────────

#: The answer to *"does the evidence already in hand answer this?"* — a
#: MEASURED property of the answer, never a predicted property of the question.
SUFFICIENT = "sufficient"
INSUFFICIENT = "insufficient"
#: Not established. NEVER a synonym for either neighbour: an unassessed measure
#: is neither a negative finding (§17.3's discipline) nor a green one.
UNDETERMINED = "undetermined"

VERDICTS: tuple[str, ...] = (SUFFICIENT, INSUFFICIENT, UNDETERMINED)

#: The measure names a verdict reports. Each is a fraction in ``[0, 1]`` read
#: off the evidence itself, and each is named in the verdict so a reader can
#: audit WHY a route fired.
MEASURE_ANCHOR_COVERAGE = "anchor-coverage"
MEASURE_PROVENANCE_COVERAGE = "provenance-coverage"
MEASURES: tuple[str, ...] = (MEASURE_ANCHOR_COVERAGE, MEASURE_PROVENANCE_COVERAGE)

#: Reasons. A verdict carries every reason it found, not only the decisive one.
REASON_EVIDENCE_EMPTY = "evidence-empty"
REASON_ANCHORS_UNCOVERED = "anchors-uncovered"
REASON_PROVENANCE_THIN = "provenance-thin"
#: A measure the CALLER CONFIGURED that the assembled rows could not support: a
#: ``required_provenance_coverage`` was supplied and NO row carries a fetched
#: provenance field (a flag-off read). The measure was never taken, so the answer
#: is UNDETERMINED — never ``sufficient``: a requirement that could not be
#: measured is not a met requirement, and reporting it as one is the silent green
#: §14.3 V4 exists to kill.
REASON_PROVENANCE_UNASSESSED = "provenance-unassessed"
REASON_MEASURES_MET = "measures-met"
REASON_NO_MEASURE = "no-measure"
#: The residency branch: a miss is the RECORD's repair, and an unassessed read
#: has not established insufficiency at all (§17.3).
REASON_RESIDENT_MISS = "resident-miss"
REASON_RESIDENT_UNASSESSED = "resident-unassessed"
#: Serve reasons: a measured-sufficient answer, an insufficiency that has no
#: budget left to repair, and one whose walk is already behind it. The last two
#: are DISTINCT on purpose: a ``reason`` is a claim, and claiming an exhausted
#: budget while ``budget_remaining`` is three is the same class of false record
#: §17.3 refuses (``residency_router``: *"the reason must name the truth too"*).
REASON_BUDGET_EXHAUSTED = "escalation-budget-exhausted"
REASON_ALREADY_ESCALATED = "already-escalated"

#: The two reasons that describe the CALLER'S POLICY rather than the evidence.
#: They live on the route (never on a verdict), and both imply an insufficiency.
_BUDGET_REASONS: tuple[str, ...] = (REASON_BUDGET_EXHAUSTED, REASON_ALREADY_ESCALATED)

REASONS: tuple[str, ...] = (
    REASON_EVIDENCE_EMPTY,
    REASON_ANCHORS_UNCOVERED,
    REASON_PROVENANCE_THIN,
    REASON_PROVENANCE_UNASSESSED,
    REASON_MEASURES_MET,
    REASON_NO_MEASURE,
    REASON_RESIDENT_MISS,
    REASON_RESIDENT_UNASSESSED,
    REASON_BUDGET_EXHAUSTED,
    REASON_ALREADY_ESCALATED,
)

#: Which reasons a VERDICT may carry. An allow-list, because the route reads
#: ``verdict.reasons[0]`` and reports it as WHY it fired: an ``insufficient``
#: verdict justified by ``measures-met`` would escalate on a claim about a
#: measurement that PASSED. (The two budget reasons live on the ROUTE, not on a
#: verdict — they describe the caller's policy, not the evidence.)
_VERDICT_REASONS: dict[str, tuple[str, ...]] = {
    SUFFICIENT: (REASON_MEASURES_MET,),
    INSUFFICIENT: (REASON_EVIDENCE_EMPTY, REASON_ANCHORS_UNCOVERED, REASON_PROVENANCE_THIN),
    UNDETERMINED: (REASON_NO_MEASURE, REASON_PROVENANCE_UNASSESSED,
                   REASON_RESIDENT_MISS, REASON_RESIDENT_UNASSESSED),
}

#: Write-back refusals (§12.5 / extractor §2.4.2). A refusal is RECORDED, never
#: a silent drop: a finding that cannot be written back honestly is not quietly
#: rewritten into one that can.
REFUSAL_NO_PROVENANCE = "no-provenance"
REFUSAL_UNVERIFIED = "unverified"
REFUSAL_PROMOTION = "promotion"
REFUSAL_NO_CLAIM = "no-claim"
#: Two findings share a ``unit_key`` but disagree about the claim's CLASS. The
#: dedupe may not pick one: whichever arrives second would be dropped silently
#: and §12.4's "incorrect promotion" would then be settled by input ORDER, with
#: the plan still reported ``ok``.
REFUSAL_CLASS_CONFLICT = "class-conflict"

REFUSALS: tuple[str, ...] = (
    REFUSAL_NO_PROVENANCE,
    REFUSAL_UNVERIFIED,
    REFUSAL_PROMOTION,
    REFUSAL_NO_CLAIM,
    REFUSAL_CLASS_CONFLICT,
)

#: The ATTRIBUTE an evidence row / ``SearchResult`` carries its provenance
#: under. On the WIRE it is nested — ``SearchResult.to_dict()`` emits
#: ``{"provenance": {"source": …}}`` (``search_engine.py``, the #3837 additive
#: contract, pinned by ``tests/test_search_promoted_fields.py``) — so the key
#: here alone cannot read the shape the read path actually emits; see
#: ``EVIDENCE_PROVENANCE_WIRE_KEY``.
EVIDENCE_PROVENANCE_KEY = "source_ref"
#: The key the provenance link lives under INSIDE the wire's ``provenance``
#: block — the shape ``sdk.search`` / ``ask_lane`` hand a caller.
EVIDENCE_PROVENANCE_WIRE_KEY = "source"

#: The text keys an evidence row may carry its assembled content under, best
#: first. ``snippet`` is the 200-char wire preview; it is read LAST because a
#: truncated preview is a weaker coverage signal than the full content.
_TEXT_KEYS = ("content", "summary", "text", "snippet")
#: The keys a row carries the ENTITIES it names under, OUTSIDE its content text.
#: Anchor coverage is a measurement of whether the answer references the
#: question's entities, and a hit that names an entity in its ``subject``
#: (``{id, name, kind}``) or its ``topics`` (a list) is that evidence — reading
#: only ``content`` scored it ``0.0`` and escalated a tier for the very row that
#: answered the question.
_ENTITY_KEYS = ("subject", "topics")
#: Top-level (attribute-shape) aliases, then the nested wire path.
_PROVENANCE_KEYS = (EVIDENCE_PROVENANCE_KEY, "sourceRef")
_PROVENANCE_WIRE_KEYS = (EVIDENCE_PROVENANCE_WIRE_KEY, EVIDENCE_PROVENANCE_KEY)


def _entity_terms(value: Any) -> list[str]:
    """The READABLE names in a ``subject``/``topics`` value — never its kind.

    ``subject`` is ``{id, name, kind}`` and ``kind`` is a label class
    (``subjectKind``): reading it would let an anchor like ``point`` match every
    row. Only names/ids are evidence of *which entity* a row is about.
    """
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        out: list[str] = []
        for key in ("name", "label", "id"):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                out.append(item)
        return out
    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
        nested: list[str] = []
        for item in value:
            nested.extend(_entity_terms(item))
        return nested
    return []


# ── the evidence, normalised once ──────────────────────────────────────────


@dataclass(frozen=True)
class _Evidence:
    """One assembled evidence row, read as the things the measures need.

    ``text`` is the row's assembled content; ``entities`` is the NAMES it
    carries OUTSIDE that content (``subject``/``topics``) — both are evidence of
    what the row is about, so anchor coverage reads both. The entity names stay
    a TUPLE rather than one joined string: a joined haystack lets a phrase stitch
    the tail of one entity to the head of the next (``"ACME Corp"`` + ``"contracts"``
    would "contain" the anchor ``Corp contracts``).

    ``provenance`` is ``None`` when the row does not CARRY the field at all —
    which is a different fact from "the row carries no link". That distinction
    is the whole reason the provenance measure can be *unassessed* rather than
    zero: a caller reading with ``search_provenance_enabled()`` off never
    fetches ``source_ref``, and scoring that as insufficient would punish a
    flag, not the evidence.

    ``provenance_fetched`` carries the OTHER half of that distinction, which
    ``provenance`` alone could not express. A row read with provenance ON but
    carrying no LINK (``to_dict()`` emits the block for ``captured_at`` alone)
    must measure ``0.0`` — it is evidence with no source. Reading only the link
    made the measure fail OPEN on exactly the worst-provenance answer: a fully
    unattributed read vanished into "unassessed" and the verdict served.
    """

    id: str
    text: str
    provenance: Any
    entities: tuple[str, ...] = ()
    provenance_fetched: bool = False


def _as_mapping(row: object) -> Mapping[str, Any]:
    if isinstance(row, Mapping):
        return row
    if isinstance(row, str):
        return {"content": row}
    as_dict = getattr(row, "to_dict", None)
    if callable(as_dict):
        try:
            out = as_dict()
        except Exception:  # pragma: no cover — a row that cannot serialise
            out = None
        if isinstance(out, Mapping):
            return out
    return {}


def _provenance_fetched(data: object) -> bool:
    """Did this row FETCH the provenance field at all — link or no link?

    ``evidence_provenance`` answers *"what link does it carry"*, and returns
    ``None`` both when the field was never fetched and when it was fetched with
    no link. The provenance MEASURE needs the difference: a flag-off read is
    unassessed, while a flag-on read with zero links is a measured ``0.0``.

    ``SearchResult.to_dict()`` emits the ``provenance`` block whenever
    ``source_ref``, ``captured_at`` or ``currency`` is present, so the block's
    PRESENCE proves the fetch happened even when it carries no ``source``.
    """
    mapping = _as_mapping(data) or (data if isinstance(data, Mapping) else {})
    if mapping.get("provenance"):
        return True
    for key in _PROVENANCE_KEYS:
        if mapping.get(key):
            return True
    if not isinstance(data, Mapping):
        # ``captured_at``/``currency`` are part of the block ``to_dict()`` emits,
        # so an object holding either has FETCHED provenance even with no link.
        for key in (*_PROVENANCE_KEYS, "provenance", "captured_at", "currency"):
            if getattr(data, key, None):
                return True
    return False


def evidence_record(row: object) -> _Evidence:
    """Read ONE assembled evidence row into the shape the measures read.

    Accepts a mapping (``SearchResult.to_dict()``), an object carrying the same
    attributes, or a bare string (its own content). Nothing here interprets the
    row's meaning — it reads the fields the read path already emits.
    """
    data = _as_mapping(row)
    if not data and not isinstance(row, Mapping):
        data = {k: getattr(row, k) for k in _TEXT_KEYS if hasattr(row, k)}
        data.setdefault("id", getattr(row, "id", ""))
        for k in _PROVENANCE_KEYS + _ENTITY_KEYS:
            if hasattr(row, k):
                data[k] = getattr(row, k)
    text = ""
    for key in _TEXT_KEYS:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            text = value
            break
    entities = tuple(
        term for key in _ENTITY_KEYS for term in _entity_terms(data.get(key)))
    # Read provenance from the ORIGINAL row, never from the rebuilt ``data``: the
    # rebuild covers a fixed key set and therefore drops an object's
    # ``provenance`` block and ``captured_at``/``currency`` attributes — a row
    # that LITERALLY carries a link then measured as *unassessed*, which is the
    # fail-open direction the fetched/linked split exists to close, surviving on
    # the other input shape these functions document as accepted.
    return _Evidence(
        id=str(data.get("id") or ""),
        text=text,
        provenance=evidence_provenance(row),
        entities=entities,
        provenance_fetched=_provenance_fetched(row),
    )


def evidence_provenance(data: object) -> Any:
    """The provenance LINK a row carries, or ``None`` when the row lacks it.

    ``None`` means **the field was not fetched** (a flag-off read, or a row kind
    that has none) — not *"this fact has no source"*. The measure that consumes
    it treats the two differently on purpose.
    """
    mapping = _as_mapping(data) or (data if isinstance(data, Mapping) else {})
    for key in _PROVENANCE_KEYS:
        if mapping.get(key):
            return mapping[key]
    # The shape the read path actually emits: ``SearchResult.to_dict()`` nests
    # the link as ``provenance.source``. Reading only the top-level attribute
    # key left the provenance measure permanently unassessed on the one shape
    # every real producer emits — a declared measure with no producer, one layer
    # down from the defect #5009 exists to kill.
    nested = mapping.get("provenance")
    if isinstance(nested, Mapping):
        for key in _PROVENANCE_WIRE_KEYS:
            if nested.get(key):
                return nested[key]
    # The ATTRIBUTE shape (``evidence_record`` accepts an object as well as its
    # ``to_dict()``), so the exported helper answers the same question for a row
    # it was handed directly. Returning ``None`` here would read as "the field
    # was never fetched" for a row that carries it — the one distinction this
    # function exists to preserve.
    if not isinstance(data, Mapping):
        # The ATTRIBUTE shape can carry the SAME nested block the wire does
        # (``row.provenance == {"source": …}``), so it is read before the flat
        # aliases; otherwise an object holding a real link measured unassessed.
        nested_attr = getattr(data, "provenance", None)
        if isinstance(nested_attr, Mapping):
            for key in _PROVENANCE_WIRE_KEYS:
                if nested_attr.get(key):
                    return nested_attr[key]
        for key in _PROVENANCE_KEYS:
            if hasattr(data, key) and getattr(data, key):
                return getattr(data, key)
    return None


# ── the verdict ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SufficiencyVerdict:
    """The §12.4 answer, with the measures and reasons that produced it.

    ``sufficient`` is the ``bool | None`` bridge to ``route_residency``:
    ``True`` / ``False`` / ``None`` (undetermined). Carrying it as a property
    rather than a loose boolean is what keeps a third state from being
    flattened at the seam.
    """

    verdict: str
    reasons: tuple[str, ...] = ()
    measures: tuple[tuple[str, float], ...] = ()

    def __post_init__(self) -> None:
        if self.verdict not in VERDICTS:
            raise ValueError(
                f"verdict must be one of {VERDICTS}, got {self.verdict!r} — an "
                f"unrecognised verdict is refused, not defaulted"
            )
        if not self.reasons:
            raise ValueError(
                f"a {self.verdict!r} verdict must carry its reason(s) — the route "
                f"reports one as WHY it fired, so a reasonless verdict would make "
                f"the route assert a cause nothing measured"
            )
        allowed = _VERDICT_REASONS[self.verdict]
        for reason in self.reasons:
            if reason not in REASONS:
                raise ValueError(f"unknown reason {reason!r}; expected one of {REASONS}")
            if reason not in allowed:
                raise ValueError(
                    f"reason {reason!r} does not belong to a {self.verdict!r} "
                    f"verdict; allowed: {allowed}"
                )
        for name, value in self.measures:
            if name not in MEASURES:
                raise ValueError(f"unknown measure {name!r}; expected one of {MEASURES}")
            if not isinstance(value, float) or not (0.0 <= value <= 1.0):
                raise ValueError(f"measure {name!r} must be a fraction in [0, 1], got {value!r}")

    @property
    def sufficient(self) -> bool | None:
        """The verdict as ``route_residency``'s ``sufficient`` input."""
        if self.verdict == SUFFICIENT:
            return True
        if self.verdict == INSUFFICIENT:
            return False
        return None

    @property
    def is_insufficient(self) -> bool:
        return self.verdict == INSUFFICIENT


def _threshold(name: str, value: object) -> float:
    """A caller-supplied threshold. There is deliberately NO default."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number in [0, 1], got {value!r}")
    out = float(value)
    if not (0.0 <= out <= 1.0):
        raise ValueError(f"{name} must be in [0, 1], got {value!r}")
    return out


#: The scripts that do NOT put spaces between words. This is the UNSPACED
#: allow-list, and the polarity matters: a script NOT named here is treated as
#: spaced, so an unrecognised script fails CLOSED (its adjacency is a word
#: boundary) instead of matching everywhere. The unspaced side is a closed, small
#: set; the spaced side is open. Hangul and Yi are deliberately absent — Korean
#: and Nuosu Yi DO separate words with spaces.
_UNSPACED_SCRIPTS = frozenset({
    "CJK", "HIRAGANA", "KATAKANA", "THAI", "LAO", "KHMER", "MYANMAR", "TIBETAN",
})


def _script(ch: str) -> str:
    """The first word of a character's Unicode NAME — its script, for our purpose.

    ``unicodedata.name('的')`` is ``CJK UNIFIED IDEOGRAPH-7684``; ``name('a')`` is
    ``LATIN SMALL LETTER A``. That prefix is enough to answer the only question
    asked here, and it is the stdlib's own data rather than a hand-rolled table.
    """
    return unicodedata.name(ch, "").split(" ", 1)[0]


#: Symbols whose NFKC decomposition contains LETTERS OR DIGITS. NFKC is a
#: COMPATIBILITY folding, so it rewrites ``™`` to ``TM``, ``№`` to ``No`` and
#: ``Ⅰ`` to ``I`` — turning a SEPARATOR into characters that are word characters,
#: which then GLUE onto the anchor: ``ACME™契約`` and ``Tortoise™`` measured 0.0
#: after the fold (they were 1.0 before it). The same mechanism runs the other
#: way for ``½`` -> ``1⁄2``, where a single-digit anchor ``1`` was CREDITED by a
#: fraction. A symbol keeps separating, so it is folded to a space BEFORE NFKC.
_FOLD_AS_SEPARATOR = frozenset().union(*(
    frozenset(chr(cp) for cp in range(lo, hi + 1))
    for lo, hi in (
        # Latin-1 block FIRST: the vulgar fractions live here, not in the
        # Number Forms block, and they fold to a DIGIT RUN with U+2044 between
        # (`½` -> `1⁄2`) — which credited a single-digit anchor `1`. The
        # superscripts (`²³¹`) are deliberately ABSENT: they attach to a token
        # (`AI²`) rather than terminate a word, so they stay glued.
        (0x00AA, 0x00AA),   # ª             -> a   (1º/1ª ordinal markers)
        (0x00B5, 0x00B5),   # µ             -> Greek mu
        (0x00BA, 0x00BA),   # º             -> o
        (0x00BC, 0x00BE),   # ¼ ½ ¾         -> 1⁄4, 1⁄2, 3⁄4
        (0x2105, 0x2106),   # ℅ ℆           -> c/o, c/u
        (0x2116, 0x2116),   # №             -> No
        (0x2120, 0x2122),   # ℠ ℡ ™         -> SM, TEL, TM
        (0x213B, 0x213B),   # ℻             -> FAX
        (0x2150, 0x217F),   # fractions + Roman numerals
        (0x2189, 0x2189),   # ↉             -> 0/3
        # NUMERALS AND SYMBOLS only. From 0x249C on the circled block is Latin
        # LETTERS: `Ⓐ` and `🅐` are DECORATED LETTERS and must keep gluing
        # (`aiⒶ`, `ai🅐` stay 0.0), which is why the enclosed-alphanumeric
        # supplement is not pre-mapped wholesale — only its eleven leading DIGIT
        # forms are, because `🄀` folds to `0.` and those are numerals. `🅊`
        # (U+1F14A) is the counter-example in the other direction: NFKC already
        # folds it to `HV`, so it glues through the letter path and never reaches
        # the name branch. The underscore family (U+FF3F, U+FE33/FE34,
        # U+FE4D–U+FE4F) is likewise absent, so a decorated `_` behaves like `_`.
        (0x1F100, 0x1F10A), # 🄀 … 🄊       -> 0., … 9,   (digits, at the head of
        #                     the enclosed block whose LETTERS must keep gluing)
        (0x2460, 0x249B),   # ① … ⑳, ⑴ … ⒇, ⒈ … ⒛
        (0x24EA, 0x24FF),   # ⓪ ⓫ … ⓴
        (0x3250, 0x325F),   # ㉑ … ㉟ (circled 21–35)
        (0x32B1, 0x32BF),   # ㊱ … ㊿ (circled 36–50) — the series is CONTINUOUS, so
        #                     splitting the block at 0x3260 dropped these
        (0x32C0, 0x32CB),   # ㋀ … ㋋ (telegraph January…December -> `1月` … `12月`)
        (0x3300, 0x33FF),   # SQUARED CJK abbreviations
    )
))


def _fold(text: str) -> str:
    """Symbol-separate, whitespace-collapse, NFKC, casefold — the match form.

    Order matters twice over. The pre-map runs BEFORE NFKC so a symbol can never
    become a word character, and it runs before the whitespace collision so a
    symbol-only string folds to ``""`` and is caught by the blank guard. NFKC
    runs before casefold so the two operands share one form (see ``_mentions``
    for why NFKC rather than NFC).
    """
    spaced = "".join(" " if ch in _FOLD_AS_SEPARATOR else ch for ch in text)
    # Collapse AFTER the pre-map: pre-mapping first turns a symbol-only string
    # into blanks, and collapsing first let `_fold("™")` be `" "` — truthy, so the
    # blank guard passed and `" " in haystack` reported FULL coverage for an
    # anchor nobody named on every multi-word row.
    return unicodedata.normalize("NFKC", " ".join(spaced.split())).casefold()


#: Korean attaches case particles to the noun with NO space — `삼성전자는`,
#: `한국의`, `ACME의` are the canonical forms, not the exception. Hangul is still a
#: SPACED script (it separates WORDS with spaces), so without this a Hangul
#: neighbour always read as a word continuation and `삼성전자는 반도체를 만든다`
#: scored 0.0 — as did `ACME의 계약` and `AI를 도입했다`, which is how a Latin
#: anchor in Korean text became unmatchable.
#:
#: The accepted set is CLOSED and the check is deliberately reject-biased: the
#: run must be a particle, or two STACKED particles, covering the WHOLE run. So
#: `한국의` matches while `한국어` (`어` is not a particle) and `한국도시`
#: (`도` + `시`, where `시` is not) do not.
_KO_CASE = frozenset({
    "은", "는", "이", "가", "을", "를", "의", "에", "에서", "에게", "께",
    "께서", "과", "와", "로", "으로", "부터", "까지", "보다", "처럼", "로서",
    "으로서", "이랑", "랑", "나", "이나", "라도", "이면", "면", "므로",
    "이므로", "니까", "이니까", "지만", "이지만", "고", "이고", "며", "이며",
    "라고", "이라고", "라는", "이라는", "란", "이란", "이라", "이라면", "이라야",
})

#: AUXILIARY particles, which may follow a case particle (`에서` + `도` = `에서도`).
#: The STACK ORDER is what separates a legal stack from a compound word: real
#: Korean words decompose into case+case or aux+case (`도로` road = 도+로,
#: `의의` significance = 의+의, `가을` autumn = 가+을, `이의` objection), and an
#: unrestricted two-member split accepted every one of them.
_KO_AUX = frozenset({"도", "만", "는", "은", "부터", "까지"})

_KO_PARTICLES = _KO_CASE | _KO_AUX | frozenset({
    "에게", "께서", "으로", "부터", "까지", "보다", "처럼", "하고", "이다",
    "입니다", "이야", "야", "에서", "에는", "에도", "에만", "으로는", "으로도",
    "에서는", "으로부터", "서는", "마다", "밖에", "조차", "든지", "이든지",
    "커녕", "보다는", "하고서", "이며",
})

#: Korean BOUND NOUNS and COUNTERS attach exactly like particles — `2026년 3월`,
#: `서울시 정책`, `5명 참석`, `100원` — so without them the natural written form of
#: a Korean date, place or quantity scored 0.0 even after the particle rule.
_KO_BOUND_NOUNS = frozenset({
    "년", "월", "일", "시", "분", "초", "개", "명", "원", "위", "회", "차",
    "호", "층", "대", "마리", "권", "장", "병", "잔", "그루", "살", "번",
    "가지", "곳", "개월", "년도", "대로", "등", "및", "약", "쪽", "편",
})

def _hangul_run(text: str, start: int) -> str:
    """The maximal run of Hangul SYLLABLES beginning at ``start`` (capped at 6)."""
    end = start
    while end < len(text) and end - start < 6 and 0xAC00 <= ord(text[end]) <= 0xD7A3:
        end += 1
    return text[start:end]


def _particle_follows(text: str, start: int) -> bool:
    """Does a Korean particle or bound noun begin at ``start`` and cover the run?

    The WHOLE run must be accounted for, which is what keeps `한국어` (`어` is
    neither), `한국도시` and `한국도로공사` out. A stack is accepted only in the
    grammatical order — a CASE particle then an AUXILIARY one (`에서`+`도`) — so
    `도로`, `의의`, `가을`, `도의` and `만만` are not read as noun+particle.
    """
    run = _hangul_run(text, start)
    if not run:
        return False
    if run in _KO_PARTICLES or run in _KO_BOUND_NOUNS:
        return True
    return any(
        run[:i] in _KO_CASE and run[i:] in _KO_AUX
        for i in range(1, min(len(run), 5))
    )


def _spaced_word_char(ch: str) -> bool:
    """Is ``ch`` a word character of a script that separates words with spaces?

    TRUE unless the character's script is on the known-UNSPACED allow-list. Digits
    and ``_`` are word characters in every script (``PR2``, ``PR_x`` are single
    tokens), and a combining mark CONTINUES the letter it attaches to — treating
    one as a separator would carve a letter in two.

    ⚠️ The polarity is load-bearing. An allow-list of the SPACED side (``LATIN``
    only, as an earlier version had) answers "not a word character" for full-width
    and mathematical Latin — ``Ａ`` (``FULLWIDTH LATIN CAPITAL LETTER A``) and
    ``𝐀`` (``MATHEMATICAL BOLD CAPITAL A``) are Latin word characters of a spaced
    script — so ``AI`` matched ``Ａai``. Naming the unspaced side instead makes
    every one of those fail closed.
    """
    if not ch:
        return False
    if ch == "_" or ch.isdigit():
        return True
    if ch.isspace():
        # Separators stay separators. Both operands are whitespace-collapsed
        # before matching, so this is belt-and-braces — and it is needed, because
        # ``\n``/``\t`` also have an EMPTY name and would otherwise block.
        return False
    if not ch.isalpha():
        if unicodedata.category(ch).startswith("M"):
            return True
        if 0xE000 <= ord(ch) <= 0xF8FF:
            # BMP PRIVATE USE is icon-font territory in extracted text — a Word
            # Symbol-font bullet is ``U+F0B7`` — and it sits BETWEEN tokens, so it
            # is a SEPARATOR: ``ACME\uf0b7契約を締結`` must match. Assigned
            # CJK-compatibility private use lives in the supplementary planes,
            # which still block.
            return False
        name = unicodedata.name(ch, "")
        if not name:
            # UNNAMED and not BMP private use (unassigned, surrogates,
            # supplementary PUA) offers no script to consult, and reading it as a
            # separator fails OPEN — ``AI`` matched ``ai\u0378``. It blocks
            # instead: the fail-closed side, like an unrecognised script.
            return True
        # CIRCLED/SQUARED Latin (``Ⓐ``, ``ⓐ``) is ``So`` — a letter ``isalpha()``
        # really does not report — so its NAME is what catches it. (``Ａ`` and
        # ``𝐀`` ARE ``isalpha()`` letters; what makes them separators is the
        # inverted polarity below, since their name's first word is ``FULLWIDTH``
        # / ``MATHEMATICAL`` rather than ``LATIN``.)
        return "LATIN" in name
    return _script(ch) not in _UNSPACED_SCRIPTS


def _occurs_as_word(needle: str, haystack: str) -> bool:
    """Is ``needle`` a WORD of ``haystack`` — including inside unspaced scripts?

    The boundary is decided AT THE MATCH, never for the haystack as a whole. A
    haystack-level proxy (``" " not in haystack``) is wrong in both directions: a
    Chinese sentence containing a year — ``我们与ACME的合同已于 2026 年 3 月 签署``
    — is overwhelmingly unspaced text, yet it contains separators, so the
    relaxation was switched OFF for it and the row that literally answers scored
    0.0.

    So an occurrence is accepted when NEITHER neighbour is a word character of a
    spaced script: ``AI`` stays out of ``main``, ``naïve``, ``Ａai``; ``PR`` out of
    ``práctica``; ``ИИ`` out of ``России``; ``ΑΙ`` out of ``ΜΑΙΟΣ``. ``ACME``
    still matches inside CJK and Thai text, with or without a number or a
    full-width character later in the sentence.
    """
    start = haystack.find(needle)
    while start != -1:
        end = start + len(needle)
        left = haystack[start - 1] if start else ""
        right = haystack[end] if end < len(haystack) else ""
        if _spaced_word_char(left):
            blocked = True
        elif not _spaced_word_char(right):
            blocked = False
        else:
            # A Hangul neighbour is a word character of a SPACED script, but an
            # attached Korean particle is a boundary, not a continuation.
            blocked = not _particle_follows(haystack, end)
        if not blocked:
            return True
        start = haystack.find(needle, start + 1)
    return False


def _mentions(needle: str, row: _Evidence) -> bool:
    """Does ``row`` MENTION ``needle`` as a word, in its text or its entities?

    A bare substring test is unbounded, and the whole verdict rests on this one
    relevance check: ``"ai" in "he said the main thing"`` is ``True``, so a
    short entity name (``AI``, ``PR``, ``Q3``, ``Art``) scored COVERED on text
    that never mentions it — a confident ``sufficient`` in the fail-open
    direction §14.3 V4 exists to kill. The comparison beside it (``row.id``) is
    exact, so the weaker signal was the looser one. Word boundaries make this
    the same KIND of test as the id check.

    BOTH SIDES are whitespace-normalised before matching: a phrase's internal
    spacing is not part of its identity (same rule as ``_normalise_claim``), and
    a wrapped ``content``/``summary`` must not make a multi-word entity
    unmatchable.

    The row's entity NAMES are read too, one at a time (a join would let a phrase
    span two entities). The boundary rule applies to anchors drawn from a SPACED
    script; an ideographic/Thai anchor has no word separators to respect in its
    own script, so adjacency is not a boundary for it and the substring test IS
    the correct semantics (``合同`` must match inside ``我们签署了合同``).

    Empty/blank anchors are refused by ``assess_sufficiency`` long before they
    reach here; the guard remains as defence, since ``"" in …`` is universal.
    """
    # `_fold` — compatibility folding, not merely canonical, on BOTH sides: NFKC
    # folds the width variants (`ＡＩ` -> `AI`, `２０２６` -> `2026`, half-width kana
    # -> full-width) as well as the composition forms. NFC alone left a full-width
    # spelling unmatchable in both directions and made half-width kana a spaced
    # word character (`ｼ`'s first name word is `HALFWIDTH`), so `当社のＡＩ技術`
    # and `ｼｽﾃﾑACMEｼｽﾃﾑ` scored 0.0. It also removes the operands' form-dependence:
    # the extractor and canonical stores emit NFC while filesystem/OCR text and
    # caller anchors may be NFD/NFKC. `_fold` pre-maps the symbols NFKC would turn
    # into LETTERS to a space first, so the fold cannot glue `ACME™契約` together.
    folded = _fold(needle)
    if not folded:
        return False
    if folded == _fold(row.id):
        return True
    # Does the ANCHOR itself come from a spaced script? Only then is a word
    # boundary meaningful in its OWN script; an unspaced-script anchor has none.
    # The test is the SCRIPT, not `isascii()`: the ASCII proxy granted the rule to
    # Latin text only, so a Cyrillic/Greek/Hebrew/Arabic anchor was infix-matched
    # (`ИИ` covered `России`) — the same fail-open class, one operand over.
    bounded = any(
        _script(ch) not in _UNSPACED_SCRIPTS
        for ch in folded if ch.isalnum() or ch == "_"
    )
    for candidate in (row.text, *row.entities):
        if not candidate:
            continue
        haystack = _fold(candidate)
        if _occurs_as_word(folded, haystack) if bounded else folded in haystack:
            return True
    return False


def assess_sufficiency(
    evidence: Iterable[object],
    *,
    anchors: Sequence[str] = (),
    required_anchor_coverage: float | None = None,
    required_provenance_coverage: float | None = None,
    resident: bool | None = None,
) -> SufficiencyVerdict:
    """Measure *"does the evidence I already have actually answer this?"*

    ``evidence``   the assembled rows, already in hand — the check runs on them
                   and costs no extra retrieval (§12.4: one call, one pass).
                   A SEQUENCE of rows: one row is passed as ``[row]``. A bare
                   ``str``/``bytes``/``Mapping`` is REFUSED, because it is a
                   single row — not an iterable of them — and iterating it
                   would shred it into one row per CHARACTER (a ``str``) or per
                   KEY (a ``Mapping``). Those synthetic rows contain text no
                   producer emitted, so a measure built on them reports a
                   confident verdict about evidence that does not exist.
    ``anchors``    the entities the question references, ALREADY RESOLVED by the
                   caller. Not a query class: the measure is the evidence's
                   coverage of them, not a prediction about the question. Each
                   element must be a non-blank string: an empty anchor matches
                   everything, so counting it would report ``1.0`` coverage for
                   an entity nobody named.
    ``required_anchor_coverage`` / ``required_provenance_coverage``
                   caller-supplied thresholds. Omit one and that measure is NOT
                   ASSESSED (see ``REASON_NO_MEASURE``) — it never silently
                   counts as met. Supplying the anchor threshold while
                   resolving NO anchors is refused rather than silently
                   unassessed: the caller configured a measure, and reporting
                   ``no-measure`` would blame a configuration that exists.
    ``resident``   §17.3's precedence: ``None`` = not established, ``False`` = a
                   residency miss. Either way this function REFUSES to call the
                   evidence insufficient — that is the record's repair, not a
                   tier's. Anything that is not a real ``bool``/``None`` (``0``,
                   ``""``, an int flag from a JSON payload) is REFUSED: an
                   allow-list guard is what keeps this from failing open.

    FAIL-CLOSED POLARITY: ``sufficient`` requires at least one measure to have
    been ASSESSED and every assessed measure to have met its threshold. A
    verdict reached from nothing is ``undetermined``.
    """
    if required_anchor_coverage is not None:
        required_anchor_coverage = _threshold(
            "required_anchor_coverage", required_anchor_coverage)
    if required_provenance_coverage is not None:
        required_provenance_coverage = _threshold(
            "required_provenance_coverage", required_provenance_coverage)
    if isinstance(evidence, (str, bytes, bytearray, Mapping)):
        # ONE row is not an iterable OF rows: a ``str`` iterates its characters
        # and a ``Mapping`` iterates its keys, so either silently mints rows
        # carrying text nobody assembled (``{"content": "acme"}`` becomes the
        # rows ``"id"``, ``"content"``). That is the fail-open direction — a
        # measure over invented rows reports a confident ``sufficient`` on the
        # answer it was supposed to test. Refused loudly, mirroring the
        # ``anchors`` guard below: the caller wraps the row (``[row]``).
        raise TypeError(
            f"evidence must be a sequence of rows, not a single "
            f"{type(evidence).__name__} — a single row is passed as ``[row]``; "
            f"iterating this shape would measure its "
            f"{'characters' if isinstance(evidence, (str, bytes, bytearray)) else 'keys'}"
        )
    if isinstance(anchors, (str, bytes)):
        raise TypeError("anchors must be a sequence of strings, not a single string")
    for anchor in anchors:
        if not isinstance(anchor, str):
            raise TypeError(f"each anchor must be a string, got {anchor!r}")
        if not anchor.strip():
            raise ValueError(
                "an anchor must name something — an empty/blank anchor matches "
                "every row, so counting it reports full coverage for an entity "
                "nobody named (the fail-open direction §14.3 V4 forbids)"
            )
    if required_anchor_coverage is not None and not anchors:
        raise ValueError(
            "required_anchor_coverage was supplied but no anchors were resolved — "
            "an empty anchor set cannot disable a measure the caller configured; "
            "pass required_anchor_coverage=None to leave it unassessed"
        )
    if resident is not None and not isinstance(resident, bool):
        # An ALLOW-LIST, not a truthiness test: ``resident = row.get("resident")``
        # from a JSON column is ``0``/``1``, and identity tests against ``False``
        # let ``0`` through as if residency had been ESTABLISHED — the guard would
        # then be a no-op in the one direction §17.3 forbids (an unassessed read
        # walking a tier). Refused loudly so a caller converts deliberately.
        raise TypeError(
            f"resident must be True, False or None, got {resident!r} — a "
            f"non-bool (an int flag, \"\", 0) is not an established residency"
        )

    # §17.3, the collapse guard. Residency outranks sufficiency: a read the
    # store could not see has not ESTABLISHED insufficiency, so an escalation
    # here would walk a tier of a store that never held the node.
    if resident is not True:
        reason = REASON_RESIDENT_MISS if resident is False else REASON_RESIDENT_UNASSESSED
        return SufficiencyVerdict(UNDETERMINED, (reason,), ())

    rows = tuple(evidence_record(row) for row in evidence)

    if not rows:
        # Only reachable with residency ESTABLISHED, so an empty assembly is a
        # genuine insufficiency rather than a miss read as a negative finding.
        return SufficiencyVerdict(INSUFFICIENT, (REASON_EVIDENCE_EMPTY,), ())

    measures: list[tuple[str, float]] = []
    failures: list[str] = []

    if required_anchor_coverage is not None:  # anchors is non-empty: see the guard
        covered = sum(1 for a in anchors if any(_mentions(a, r) for r in rows))
        value = float(covered) / float(len(anchors))
        measures.append((MEASURE_ANCHOR_COVERAGE, value))
        if value < required_anchor_coverage:
            failures.append(REASON_ANCHORS_UNCOVERED)

    # The provenance measure is assessed ONLY when the rows actually carry the
    # field — see `_Evidence.provenance`. An unfetched field is unassessed, not
    # zero: a flag-off read must not be scored as insufficient evidence.
    provenance_unassessed = False
    if required_provenance_coverage is not None:
        # The NUMERATOR is the rows that carry a LINK; the DENOMINATOR is every
        # row in the assembly. A row that never fetched provenance is still an
        # UNSOURCED row of THIS answer, so letting it leave the denominator let a
        # MIXED assembly (1 sourced Point + 3 non-Point rows, which carry no
        # provenance block however the flag is set) report 1.0 on a 25%-sourced
        # answer — the silent green this module exists to kill, produced by the
        # measurement instead of removed by it. Only an assembly where NO row
        # fetched the field is unassessed; scoring THAT as zero would punish a
        # flag rather than the evidence.
        fetched = [r for r in rows if r.provenance_fetched]
        if fetched:
            linked = sum(1 for r in fetched if r.provenance is not None)
            value = float(linked) / float(len(rows))
            measures.append((MEASURE_PROVENANCE_COVERAGE, value))
            if value < required_provenance_coverage:
                failures.append(REASON_PROVENANCE_THIN)
        else:
            provenance_unassessed = True

    if failures:
        return SufficiencyVerdict(INSUFFICIENT, tuple(failures), tuple(measures))
    if provenance_unassessed:
        # The caller configured a measure the rows could not support. Another
        # measure passing does not stand in for it — returning ``sufficient``
        # here asserted a green over a requirement nobody measured.
        return SufficiencyVerdict(
            UNDETERMINED, (REASON_PROVENANCE_UNASSESSED,), tuple(measures))
    if not measures:
        return SufficiencyVerdict(UNDETERMINED, (REASON_NO_MEASURE,), ())
    return SufficiencyVerdict(SUFFICIENT, (REASON_MEASURES_MET,), tuple(measures))


# ── the bounded trigger ────────────────────────────────────────────────────


@dataclass(frozen=True)
class SufficiencyRoute:
    """One canonical route — a DIRECTIVE about where to look (§17.3).

    Never a status term, and never ``fetch-record``: that is the residency
    router's repair and emitting it here would collapse the two events.
    """

    route: str
    reason: str
    verdict: str
    escalated: bool = False
    budget_remaining: int = 0

    def __post_init__(self) -> None:
        if self.route not in ROUTES:
            raise ValueError(f"route must be one of {ROUTES}, got {self.route!r}")
        if self.route == ROUTE_FETCH_RECORD:
            raise ValueError(
                "a sufficiency route may never be `fetch-record` — that is the "
                "RESIDENCY repair (§17.3), and collapsing the two events either "
                "escalates unnecessarily or treats a cache miss as a finding"
            )
        if self.verdict not in VERDICTS:
            raise ValueError(f"verdict must be one of {VERDICTS}, got {self.verdict!r}")
        if self.reason not in REASONS:
            raise ValueError(f"unknown reason {self.reason!r}; expected one of {REASONS}")
        if not isinstance(self.escalated, bool):
            raise ValueError(f"escalated must be a bool, got {self.escalated!r}")
        if isinstance(self.budget_remaining, bool) or not isinstance(
                self.budget_remaining, int):
            raise ValueError(
                f"budget_remaining must be a non-negative int, got {self.budget_remaining!r}")
        if self.budget_remaining < 0:
            raise ValueError(
                f"budget_remaining must be a non-negative int, got {self.budget_remaining!r}")
        # The `escalated` axis, and the budget's own coherence. The factory always
        # gets these right, but the route is the artifact a consumer RECORDS and
        # then replays (`route_sufficiency(verdict, escalated=route.escalated,
        # budget=route.budget_remaining)`), so a hand-built or deserialised route
        # that contradicts itself would defeat the ONE bound that stops a walk
        # loop — the same standard this type already applies to verdict and reason.
        if self.route == ROUTE_ESCALATE_TIER and self.escalated is not True:
            raise ValueError(
                f"route {self.route!r} DIRECTS a walk, so escalated must be True, "
                f"got {self.escalated!r} — replaying this route would walk the same "
                f"tier again forever"
            )
        if self.reason == REASON_ALREADY_ESCALATED and self.escalated is not True:
            raise ValueError(
                f"reason {self.reason!r} claims a walk is already behind this "
                f"verdict, so escalated must be True, got {self.escalated!r}"
            )
        if self.reason == REASON_BUDGET_EXHAUSTED and self.budget_remaining != 0:
            raise ValueError(
                f"reason {self.reason!r} claims the budget is gone, so "
                f"budget_remaining must be 0, got {self.budget_remaining!r} — a "
                f"reason and a budget that disagree is a false record"
            )
        # The route is the artifact a consumer RECORDS, so it holds the same
        # coherence the factory guarantees: an `escalate-tier` route is a
        # measured insufficiency (never a green), and the two budget reasons
        # describe an insufficiency too. Without this the verdict-level
        # allow-list is defeated one layer up by a hand-built/deserialised route.
        if self.route == ROUTE_ESCALATE_TIER:
            if self.verdict != INSUFFICIENT:
                raise ValueError(
                    f"route {self.route!r} means a MEASURED insufficiency, so the "
                    f"verdict must be {INSUFFICIENT!r}, got {self.verdict!r}"
                )
            if self.reason not in _VERDICT_REASONS[INSUFFICIENT]:
                raise ValueError(
                    f"route {self.route!r} escalated on reason {self.reason!r}, which "
                    f"is not an insufficiency of the evidence; expected one of "
                    f"{_VERDICT_REASONS[INSUFFICIENT]}"
                )
        elif self.reason in _BUDGET_REASONS:
            if self.verdict != INSUFFICIENT:
                raise ValueError(
                    f"reason {self.reason!r} describes a bounded insufficiency, so the "
                    f"verdict must be {INSUFFICIENT!r}, got {self.verdict!r}"
                )
        elif self.reason not in _VERDICT_REASONS[self.verdict]:
            raise ValueError(
                f"reason {self.reason!r} does not belong to a {self.verdict!r} "
                f"verdict; allowed: {_VERDICT_REASONS[self.verdict]}"
            )


    @property
    def escalates_tier(self) -> bool:
        """Does this route DIRECT a walk? ``False`` for every refused walk."""
        return self.route == ROUTE_ESCALATE_TIER

    @property
    def sufficient(self) -> bool | None:
        """The value to hand ``route_residency(sufficient=...)``.

        Hand THIS, not ``verdict.sufficient``. The verdict says what was
        MEASURED; this route says what the bound will actually PAY FOR. A verdict
        that is insufficient while ``route_sufficiency`` REFUSED the walk (the
        budget is gone, or a walk is already behind it) yields ``None`` here — so
        the sibling router, whose ``sufficient is False`` branch escalates
        unconditionally, cannot re-walk the tier this router just declined to
        walk. Handing it the raw verdict bypassed the only bound there is.
        """
        if self.escalates_tier:
            return False
        if self.verdict == SUFFICIENT:
            return True
        return None


def route_sufficiency(
    verdict: SufficiencyVerdict,
    *,
    budget: int,
    escalated: bool = False,
) -> SufficiencyRoute:
    """The §12.4 trigger, BOUNDED — escalate once, never walk a tier loop.

    ``budget``      the caller's escalation PERMIT. **Mandatory, with no
                    default**: §12.3's tier count is OPEN, so an omitted
                    argument would answer it by default for every caller — the
                    same class of un-chosen target ``_threshold`` refuses.
                    ⚠️ WHAT IT ACTUALLY BUYS: this router spends AT MOST ONE
                    step, because ``escalated`` — not the budget's SIZE — is
                    what bounds a chain (a still-insufficient verdict after a
                    walk is reported, never re-walked). So ``budget > 1``
                    authorises a LATER chain, not a second walk in this one, and
                    the value is carried back on ``budget_remaining`` for the
                    caller's own accounting. A caller wanting a third tier
                    re-enters with the deeper tier's OWN verdict, and whether
                    that walk happens is that chain's decision: §12.3's count
                    stays a product ruling this module does not make.
    ``escalated``   this answer has already been walked deeper once, so a still
                    insufficient verdict is reported, not re-walked. That is the
                    bound on §12.7's *"router overhead"*: an unbounded repair
                    loop costs more than the retrieval it routes.

    The escalation fires ONLY on a measured ``insufficient``. ``undetermined``
    serves: it is not a negative finding, and §12.3's default answer is the
    cheap tier.

    The two bounds report DISTINCTLY — ``escalation-budget-exhausted`` only when
    the budget is actually gone, ``already-escalated`` when the walk is simply
    behind this verdict — because ``reason`` is a claim a reason-keyed consumer
    acts on, and ``budget_remaining`` must not contradict it.
    """
    if not isinstance(verdict, SufficiencyVerdict):
        raise TypeError("route_sufficiency takes a SufficiencyVerdict, not a bool — "
                        "a bare False cannot say WHY, and `None` is a real state")
    if not isinstance(escalated, bool):
        # The one parameter whose job is to stop a SECOND walk. A falsy non-bool
        # (``0`` from a JSON column, ``""``, ``[]``) was read as "not escalated"
        # and silently bought a tier — the same type gap ``resident`` closed.
        raise TypeError(
            f"escalated must be True or False, got {escalated!r} — a non-bool is "
            f"not a statement about whether this answer was already walked"
        )
    if isinstance(budget, bool) or not isinstance(budget, int) or budget < 0:
        raise ValueError(f"budget must be a non-negative int, got {budget!r}")

    if verdict.verdict == INSUFFICIENT and not escalated and budget > 0:
        return SufficiencyRoute(
            route=ROUTE_ESCALATE_TIER,
            # The verdict GUARANTEES at least one reason and that it names an
            # insufficiency (``_VERDICT_REASONS``), so this is the cause the
            # verdict actually stated — never a fabricated fallback.
            reason=verdict.reasons[0],
            verdict=verdict.verdict,
            escalated=True,
            budget_remaining=budget - 1,
        )
    if verdict.verdict == INSUFFICIENT:
        # Known short, and either nothing left to spend OR the walk is already
        # behind this verdict: report it rather than loop. The two are DISTINCT
        # reasons — the budget one is only true when the budget is actually gone,
        # and `budget_remaining` below must not contradict what `reason` claims.
        reason = (REASON_BUDGET_EXHAUSTED if budget == 0
                  else REASON_ALREADY_ESCALATED)
        return SufficiencyRoute(
            route=ROUTE_SERVE,
            reason=reason,
            verdict=verdict.verdict,
            escalated=escalated,
            budget_remaining=budget,
        )
    return SufficiencyRoute(
        route=ROUTE_SERVE,
        reason=verdict.reasons[0],
        verdict=verdict.verdict,
        escalated=escalated,
        budget_remaining=budget,
    )


# ── §12.5 the escalation write-back ───────────────────────────────────────


#: The ceiling on a span offset, and the reason it is repeated here rather than
#: imported. ``tortoise/commit_schema.py`` is THE ONE HOME for the span-address
#: rule (``SPAN_OFFSET_MAX``, E4 / ``#5007``): **an offset past this is silently
#: CLAMPED on write**, so the committed address and the stored address diverge —
#: a unit would then claim a span it does not have. This module keeps its imports
#: pure (``commit_schema`` pulls pydantic and yaml at import, ~240 ms, and this
#: decision layer sits on the read path), so the VALUE is copied, not re-derived;
#: ``tests/test_sufficiency_router.py`` pins it to the one home, so the two
#: cannot drift apart unnoticed.
SPAN_OFFSET_MAX = 2**63 - 1


@dataclass(frozen=True)
class SourceSpan:
    """An ADDRESS into the immutable raw log (extractor §2.4.2).

    Mirrors ``commit_schema``'s span polarity: the offsets are all-or-nothing
    and ``end`` must be past ``start``. The span TEXT is never stored here — a
    written-back unit that carried a copy of the raw payload would stop being a
    summary and start being a second, divergent copy of the evidence.
    """

    source_id: str
    turn: int | None = None
    start: int | None = None
    end: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source_id, str) or not self.source_id.strip():
            raise ValueError("a span needs the id of the raw source it addresses")
        if (self.start is None) != (self.end is None):
            raise ValueError(
                "span start and end are all-or-nothing — one offset is not an address")
        for name, value in (("turn", self.turn), ("start", self.start), ("end", self.end)):
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"span {name} must be a non-negative int, got {value!r}")
            if value > SPAN_OFFSET_MAX:
                # NOT a stylistic bound: the store clamps past it (E4 / #5007), so
                # such an address is stored as a DIFFERENT address and the unit's
                # span stops being the thing it points at.
                raise ValueError(
                    f"span {name} must be <= {SPAN_OFFSET_MAX} — an offset past the "
                    f"ceiling `commit_schema` clamps on write, so this address "
                    f"would be stored as a different one; got {value!r}"
                )
        if self.start is not None and self.end is not None and self.end <= self.start:
            raise ValueError(
                f"span end ({self.end}) must be greater than span start ({self.start})")

    def canonical(self) -> str:
        """The identity of the address, used by ``unit_key``."""
        turn = "-" if self.turn is None else str(self.turn)
        start = "-" if self.start is None else str(self.start)
        end = "-" if self.end is None else str(self.end)
        return f"{self.source_id}#{turn}:{start}-{end}"


@dataclass(frozen=True)
class VerifiedFinding:
    """What an escalation VERIFIED, plus the address that proves it.

    ``claim_class`` is the class the finding HAS. Nothing on this type lets a
    caller ask for a different one; ``plan_writeback(promote_to=...)`` is the
    only door, and it refuses anything but the same class (§12.4 — §12.7's entry
    of the same name is the routing failure, not this one).
    """

    claim: str
    span: SourceSpan | None
    claim_class: str
    verified: bool = False


@dataclass(frozen=True)
class SummaryUnit:
    """A derived summary written back to the cheap tier — never authoritative.

    ``derived`` is always ``True``: a summary unit is a *finding about* the raw
    log, never a replacement for it. The type deliberately has no field naming a
    superseded/replaced source and none carrying the raw payload — see
    ``tests/test_sufficiency_router.py``, which refuses such a field, because
    "the write-back must respect extractor §2.4.2: a finding written back
    carries the SPAN, and the raw fact behind it stays immutable" (§12.5).
    """

    unit_key: str
    claim: str
    claim_class: str
    span: SourceSpan
    derived: bool = True

    def __post_init__(self) -> None:
        """The type holds its own invariants, like every sibling in this module.

        ``plan_writeback`` refuses a finding without a span, but the TYPE is
        public (``__all__``) and §12.5's persistence seam is the caller's — so a
        hand-built unit is the realistic way an unattributable belief reaches the
        store. A field-name pin cannot see a value-level bypass; this can.
        """
        if not isinstance(self.span, SourceSpan):
            raise TypeError(
                f"a summary unit needs a SourceSpan, got {self.span!r} — a unit "
                f"without an address is an unattributable belief (§12.5)"
            )
        if not isinstance(self.claim, str) or not self.claim.strip():
            # ``plan_writeback`` REFUSES `no-claim`; the type must not accept what
            # the planner refuses, and a non-str claim would otherwise reach
            # ``_normalise_claim`` and raise AttributeError instead of a refusal.
            raise ValueError(
                f"a summary unit needs a non-empty claim, got {self.claim!r}")
        if self.derived is not True:
            raise ValueError(
                "a summary unit is always derived: it is a finding ABOUT the raw "
                "log and can never be authoritative (extractor §2.4.2)"
            )
        if not isinstance(self.unit_key, str) or not self.unit_key.strip():
            raise ValueError(
                f"unit_key must be a non-empty string, got {self.unit_key!r}")
        expected = unit_key(self.span, self.claim)
        if self.unit_key != expected:
            raise ValueError(
                f"unit_key {self.unit_key!r} is not the identity of (span, claim) "
                f"— expected {expected!r}; the attribution loop dedupes on this key, "
                f"so a hand-written one silently mints a duplicate"
            )


@dataclass(frozen=True)
class WritebackRefusal:
    """A finding that could NOT be written back honestly, and why."""

    claim: str
    reason: str

    def __post_init__(self) -> None:
        if self.reason not in REFUSALS:
            raise ValueError(f"unknown refusal {self.reason!r}; expected one of {REFUSALS}")


@dataclass(frozen=True)
class WritebackPlan:
    """The write-back's OUTPUT: units to persist, and refusals to report.

    A plan performs no I/O. Persistence is the caller's seam — no Tier-1 store
    exists yet, and §12.3's tier count is OPEN, so this module must not become
    the second place that decides where a summary unit lands.
    """

    units: tuple[SummaryUnit, ...] = ()
    refusals: tuple[WritebackRefusal, ...] = ()

    @property
    def ok(self) -> bool:
        """True when nothing was refused (an empty plan is not `ok` by itself)."""
        return bool(self.units) and not self.refusals


def _normalise_claim(claim: str) -> str:
    """Identity normalisation ONLY — the unit keeps the claim verbatim."""
    return " ".join(claim.split()).casefold()


def unit_key(span: SourceSpan, claim: str) -> str:
    """Content-addressed identity for a summary unit.

    Over ``(span, normalised claim)`` so that the SAME verified finding written
    back twice is ONE unit. This is the attribution loop's whole mechanism: the
    second escalation of the same question must not pay for a duplicate, or
    §12.5's "escalation is a permanent tax" is exactly what we ship.
    """
    if not isinstance(span, SourceSpan):
        raise TypeError("unit_key needs a SourceSpan — a unit without an address is unattributable")
    payload = f"{span.canonical()}\x00{_normalise_claim(claim)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def plan_writeback(
    findings: Iterable[VerifiedFinding],
    *,
    promote_to: str | None = None,
) -> WritebackPlan:
    """Plan §12.5's write-back: verified findings → derived summary units.

    A finding is written back only when it is VERIFIED and CARRIES A SPAN. Every
    other finding is REFUSED with a named reason and reported — never silently
    dropped, and never rewritten into a shape that passes.

    ``promote_to`` is the §12.4 *"incorrect promotion"* door, and it is bolted
    shut: it may be ``None`` or the finding's own class, never a different one
    (an episodic fact must not become a semantic rule by being written back).
    """
    units: list[SummaryUnit] = []
    refusals: list[WritebackRefusal] = []
    seen: dict[str, SummaryUnit] = {}
    #: Keys whose findings disagreed about the claim's CLASS. Neither may be
    #: written back: the dedupe cannot pick one, and picking by input order would
    #: be §12.4's incorrect promotion decided by iteration order.
    conflicted: set[str] = set()

    for finding in findings:
        claim = finding.claim if isinstance(finding.claim, str) else ""
        if not claim.strip():
            refusals.append(WritebackRefusal(claim="", reason=REFUSAL_NO_CLAIM))
            continue
        if finding.verified is not True:
            # TierMem writes back VERIFIED findings; an unverified one is a
            # belief, and a belief written into the cheap tier is served as if
            # it were evidence next time.
            refusals.append(WritebackRefusal(claim=claim, reason=REFUSAL_UNVERIFIED))
            continue
        if finding.span is None:
            refusals.append(WritebackRefusal(claim=claim, reason=REFUSAL_NO_PROVENANCE))
            continue
        if promote_to is not None and promote_to != finding.claim_class:
            refusals.append(WritebackRefusal(claim=claim, reason=REFUSAL_PROMOTION))
            continue
        key = unit_key(finding.span, claim)
        if key in seen:
            if seen[key].claim_class != finding.claim_class:
                # Same identity, different CLASS: refuse the KEY outright rather
                # than keep whichever arrived first. Order-independent by
                # construction, and REPORTED — a silent drop here would be the
                # promotion door closing quietly instead of bolted.
                conflicted.add(key)
                refusals.append(
                    WritebackRefusal(claim=claim, reason=REFUSAL_CLASS_CONFLICT))
            # Otherwise idempotent: the same verified finding is ONE unit.
            continue
        unit = SummaryUnit(
            unit_key=key,
            claim=claim,
            claim_class=finding.claim_class,
            span=finding.span,
        )
        seen[key] = unit
        units.append(unit)

    if conflicted:
        units = [unit for unit in units if unit.unit_key not in conflicted]
    return WritebackPlan(units=tuple(units), refusals=tuple(refusals))
