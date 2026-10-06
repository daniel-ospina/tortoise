"""Route-then-refuse resolution for Object/Subject identity reads (#3633).

An ``Object``/``Subject`` identity coordinate reaches a read site in one of two
shapes:

* an **id** — opaque and stable, so it names exactly one node; or
* a **name** — a mutable *natural key* that, after #3590's D2, **two or more
  live entities may hold at the same time** (same-name coexistence is legal and
  is produced by explicit-id writers).

A read that resolves a name as if it were an id therefore has a choice the
caller never expressed: *which* of the live carriers did it mean? The historic
answers were both wrong — ``MATCH`` returning several rows and unioning their
result sets **folds two identities into one value**, and a bare ``LIMIT 1``
**silently picks** one carrier (``result_set[0]`` is backend-order-dependent).

This module is the ONE place that answers that question. It applies the
governing plan's §B.1 disposition, *route then refuse*
(``docs/plans/2026-09-15-3590-minted-identity.md``):

1. **route** — resolve the coordinate to **exactly one id**. An exact id match
   wins outright (an id is an unambiguous identity; a name that happens to equal
   another node's id loses). Otherwise the name is resolved over **live**
   holders only.
2. **refuse** — when a name is held by two or more live entities, do not guess
   and do not union: record a **structured** non-folded entry (see
   :func:`record_non_folded` / :func:`non_folded_entries`) and raise
   :class:`AmbiguousEntityName`. A name held only by TERMINAL carriers (D2's
   ``single_terminal_holder_reference``) is likewise refused — never resolved
   to a terminal id — and recorded (shape ``no-live-holder``) rather than
   silently returning ``None``, so "the holder is terminal" stays
   distinguishable from "no such name". A refusal is a record, never a silently
   passing warning: the decision record R8 requires the refused-event set to be
   asserted on, and the log line is operator visibility *alongside* the entry,
   not the entry itself.

The "live" predicate is ``live._terminal_excluded`` — the same vocabulary every
other read surface uses — so a terminal (superseded/deprecated/archived/
retracted/outdated) holder never resolves a name.

SCOPE BOUNDARY: the plan's Slice 0 owns the *durable* per-projection
non-folded journal (``_record_non_fold`` in ``projection``) and the R9
replay-time assertion over it; that slice is NOT on ``main``. Until it lands,
:func:`non_folded_entries` is a process-level assertable record, and a refusal
that raises is also carried structurally on
:class:`AmbiguousEntityName` / :class:`UnaddressableEntityName`.

Do NOT re-implement this resolution at a call site. A hand-rolled
``id = $x OR name = $x`` disjunction is exactly the defect this module replaces,
and a hand-rolled ``LIMIT 1`` is the silent-pick variant of it.
"""

from __future__ import annotations

import logging
import threading
from typing import NamedTuple

from tortoise.live import _terminal_excluded

logger = logging.getLogger(__name__)

#: The only labels whose ``name`` is a legal *natural key*. Anything else is
#: refused loudly rather than interpolated into a query (parity with
#: ``resolve_structural_target``'s runtime label defense).
_IDENTITY_LABELS = ("Object", "Subject")

#: The labels an id-typed edge endpoint is resolved over by the write path
#: (``projection.__init__``'s ``_RESOLVE_BRANCHES``). A resolved id's
#: uniqueness is checked in the CALLER's space: ``create_edge`` resolves by
#: id|eventId|url for the SOURCE and id|eventId for the TARGET, dedupes by the
#: node's own identity property, and MERGEs one edge per resolved endpoint — so
#: an id two endpoints would resolve to silently wires the edge to BOTH, or
#: re-targets it. ``Event`` keys on ``eventId``; a ``Source`` may also key on
#: ``url`` (source side only).
_ID_ENDPOINT_BRANCHES = (("Point", "id"), ("Subject", "id"),
                         ("Object", "id"), ("Source", "id"),
                         ("Event", "eventId"))
_URL_ENDPOINT_BRANCHES = (("Source", "url"),)

#: ``addressing`` modes: the SOURCE OR-set includes ``Source.url``; the TARGET
#: OR-set does not (a url-only stub is not a valid target — preserving prior
#: ``create_edge`` behaviour).
ADDRESSING_SOURCE = "source"
ADDRESSING_TARGET = "target"


class AmbiguousEntityName(ValueError):
    """A name resolves to two or more live carriers — refuse, never guess.

    Carries structured fields so an MCP/HTTP boundary can map it to a 409
    without string-matching the message (the plan's "Open for the implementer"
    shape: ``label``, ``name``, ``candidate_ids``).
    """

    def __init__(self, label: str, name: str, candidate_ids):
        self.label = label
        self.name = name
        self.candidate_ids = tuple(str(c) for c in candidate_ids)
        super().__init__(
            f"{label} {name!r} resolves to {len(self.candidate_ids)} live "
            f"carriers {list(self.candidate_ids)} — refusing to guess "
            f"(route-then-refuse, #3633)")


def _require_identity_label(label: str) -> None:
    if label not in _IDENTITY_LABELS:
        raise RuntimeError(
            f"entity identity: unsafe label {label!r} "
            f"({'/'.join(_IDENTITY_LABELS)} only)")


class NonFoldedEntry(NamedTuple):
    """One structured refusal: the coordinate and the carriers NOT folded.

    This is the record — not the log line. The durable, per-projection
    refused-event set and the R9 invariant that asserts on it live in the
    #3590 plan's Slice 0 (``_record_non_fold`` in ``projection/__init__``),
    which is NOT on ``main``; until it lands, this process-level set is the
    assertable record (see :func:`non_folded_entries`).
    """

    label: str
    name: str
    candidate_ids: tuple
    shape: str  # "ambiguous-name" | "unaddressable-name" | "duplicate-id"
    #            | "endpoint-collision" | "no-live-holder"


_non_folded: list[NonFoldedEntry] = []
_non_folded_lock = threading.Lock()


def record_non_folded(label: str, name: str, candidate_ids, *,
                      shape: str = "ambiguous-name") -> NonFoldedEntry:
    """Record a refused coordinate as a structured, assertable entry.

    The ENTRY is the record; the log line is operator visibility alongside it.
    (The decision record R8 is explicit that "a refused fold is an assertion
    failure, not a log line" — the durable assertion is Slice 0's, see
    :class:`NonFoldedEntry`. Do not describe the log line as the record.)

    A refusal site that must keep running (a candidate resolver, a batch probe
    that skips one ref) calls this before dropping the ref; a site that must
    fail loud calls :func:`_refuse`, which records and then raises.
    """
    entry = NonFoldedEntry(str(label), str(name),
                           tuple(sorted(str(c) for c in candidate_ids)), shape)
    with _non_folded_lock:
        _non_folded.append(entry)
    logger.warning(
        "entity-identity: refusing a name-keyed read — %s %r resolves to %d "
        "carriers %s (shape=%s); recorded a non-folded entry and refused to "
        "guess (#3633 route-then-refuse)",
        label, name, len(entry.candidate_ids), list(entry.candidate_ids),
        shape)
    return entry


def non_folded_entries() -> tuple[NonFoldedEntry, ...]:
    """Every non-folded entry recorded this process (assertable).

    INTERIM: the plan's Slice 0 makes this per-projection and drives the R9
    invariant; until it lands the harness asserts on this process-level tuple.
    """
    with _non_folded_lock:
        return tuple(_non_folded)


def clear_non_folded_entries() -> None:
    """Drop the recorded entries (test isolation; no production caller)."""
    with _non_folded_lock:
        _non_folded.clear()


def _refuse(label: str, name: str, candidate_ids, *,
            shape: str = "ambiguous-name") -> None:
    """Record the non-folded entry, then refuse.

    Recording before raising keeps the evidence even when a caller catches the
    exception.
    """
    record_non_folded(label, name, candidate_ids, shape=shape)
    raise AmbiguousEntityName(label, name, candidate_ids)


def live_name_holder_ids(g, label: str, name: str, *,
                         case_insensitive: bool = False) -> list:
    """Every LIVE holder of ``name`` for ``label``, by ``n.id`` (raw).

    The list is **row-per-holder**, so an id-less legacy/hosted-stub holder
    (``MERGE (o:Object {name:$name})`` mints those) contributes an entry whose
    value is ``None``. Refusal MUST count holders, not truthy ids: a name held
    by one id-carrying and one id-less live entity is still TWO live holders
    and must refuse — filtering the ``None`` out would silently pick one.

    ``case_insensitive`` opts into ``toLower`` name matching, needed by read
    surfaces that historically matched names case-insensitively (e.g.
    ``provenance``). It is OFF by default so identity resolution stays an
    exact-key match everywhere else.

    Zero rows is a legal "no live holder"; the caller decides refusal. This is
    the primitive a refusal site uses when it needs the raw holder set (e.g. a
    batch probe that must keep ``id``/``name`` matching apart).
    """
    return _name_holder_ids(g, label, name,
                            case_insensitive=case_insensitive, live_only=True)


def _name_predicate(case_insensitive: bool) -> str:
    """The name-match predicate — ONE copy, shared by every holder probe.

    A second copy is a drift site: the live probe and the all-status probe
    must compare names identically or a name can be live-looking to one and
    terminal-looking to the other.
    """
    name_pred = ("toLower(n.name) = toLower($name)" if case_insensitive
                 else "n.name = $name")
    return name_pred


def _name_holder_ids(g, label: str, name: str, *,
                     case_insensitive: bool,
                     live_only: bool) -> list:
    """Raw holder ids for ``name``; ``live_only`` applies D2's predicate."""
    _require_identity_label(label)
    live_pred = f"AND {_terminal_excluded('n.status')} " if live_only else ""
    rows = g.query(
        f"MATCH (n:{label}) "
        f"WHERE {_name_predicate(case_insensitive)} "
        f"{live_pred}"
        "RETURN n.id",
        params={"name": name}).result_set
    return [r[0] for r in rows]


def all_name_holder_ids(g, label: str, name: str, *,
                        case_insensitive: bool = False) -> list:
    """Every holder of ``name`` for ``label`` — ANY status (raw ids).

    The complement of :func:`live_name_holder_ids`: used ONLY to tell a
    terminal-only reference (a holder exists, none live) from a genuinely
    absent name, so the former can be recorded as a non-folded
    ``no-live-holder`` refusal instead of silently returning ``None``
    (#3633 §B.1 "refuse + record").
    """
    return _name_holder_ids(g, label, name,
                            case_insensitive=case_insensitive, live_only=False)


def _endpoint_idents(g, value: str, *, include_url: bool) -> set:
    """The distinct endpoint idents ``create_edge`` would resolve for ``value``.

    Mirrors ``projection._resolve_entity``: one indexed lookup per branch, then
    DEDUPE BY THE NODE'S OWN IDENTITY (``id | eventId | url``, in that order) —
    the same dedup the caller applies. Counting NODES instead would over-count
    a Source matched by both its id and its url; counting only ``n.id`` (the
    previous probe) UNDER-counts a Source whose ``url`` equals the value but
    whose ``id`` differs — which MERGEs a SECOND edge.
    """
    branches = list(_ID_ENDPOINT_BRANCHES)
    if include_url:
        branches += list(_URL_ENDPOINT_BRANCHES)
    union = " UNION ".join(
        f"MATCH (n:{label} {{{key}:$v}}) RETURN n.id, n.eventId, n.url"
        for label, key in branches)
    rows = g.query(union, params={"v": value}).result_set
    idents = set()
    for rid, eid, url in rows:
        ident = rid or eid or url
        if ident:
            idents.add(ident)
    return idents


def _require_unique_id(g, label: str, node_id: str, *,
                       addressing: str | None = None,
                       claimant_predicate: str | None = None) -> str:
    """A name resolved to ``node_id`` — but the id itself must be unclaimed.

    TWO independent corruption checks, both required:

    1. **Same-space claimant count** — more than one node in the resolver's own
       candidate space claims the id. This is raw corruption and it is checked
       FIRST, independently of ``addressing``: the endpoint-ident dedup below
       COLLAPSES same-ident claimants, so relying on it alone would let two
       nodes of one label claiming one id through (they would then be folded by
       every downstream ``id = $sid`` leg). ``claimant_predicate`` overrides the
       candidate space (``_DOCUMENT_PREDICATE`` for the document resolver,
       whose space is an Object OR a document Source).
    2. **Caller-space endpoint collision** (``addressing``) — one coordinate
       resolves to TWO DISTINCT endpoints in the caller's OR-set (e.g. a
       ``Source`` whose ``url`` equals the value but whose ``id`` differs),
       because ``create_edge`` MERGEs an edge per resolved endpoint. See
       :func:`_endpoint_idents`.

    The document/approver paths pass ``addressing``; label-narrowed readers do
    not.

    LIMIT (check 2 only): a same-ident cross-label collision (two labels
    claiming one id) is a single endpoint under the caller's own first-branch-
    wins precedence — pre-existing ``create_edge`` semantics, not a fold, and
    not detected here.
    """
    claimant_space = claimant_predicate or f"(n:{label})"
    claimants = g.query(
        f"MATCH (n) WHERE {claimant_space} AND n.id = $id RETURN n.id",
        params={"id": node_id}).result_set
    if len(claimants) > 1:
        _refuse(label, node_id, [node_id] * len(claimants),
                shape="duplicate-id")
    if addressing:
        idents = _endpoint_idents(
            g, node_id, include_url=addressing == ADDRESSING_SOURCE)
        if len(idents) > 1:
            _refuse(label, node_id, sorted(idents),
                    shape="endpoint-collision")
    return node_id


def display_holder_ids(candidate_ids) -> list:
    """Human/log ids for a refused holder set — an id-less holder is named.

    Public so every refusal site (the resolver, ``assembly``, the onboarding
    seed) records the SAME evidence: a bare ``if c`` filter would drop the
    id-less holder that made the read ambiguous in the first place.
    """
    return [c if c is not None else "<id-less>" for c in candidate_ids]


class UnaddressableEntityName(ValueError):
    """A name resolves to exactly ONE live holder that carries **no id**.

    Unambiguous but unaddressable by an id-keyed read: every post-#3590 write
    path mints an id, so an id-less holder is legacy/raw. Refuse loudly (with a
    recorded non-folded entry) rather than return a vacuous/empty value that is
    indistinguishable from "no data".
    """

    def __init__(self, label: str, name: str):
        self.label = label
        self.name = name
        super().__init__(
            f"{label} {name!r} resolves to exactly one live holder with no id — "
            f"unaddressable by an id-keyed read; refusing (#3633)")


def _record_unaddressable(label: str, name: str) -> None:
    """Record the non-folded entry, then refuse an id-less single holder."""
    record_non_folded(label, name, ["<id-less>"], shape="unaddressable-name")
    raise UnaddressableEntityName(label, name)


def _record_if_any_holder(g, label: str, value: str,
                          case_insensitive_name: bool,
                          *, predicate: str | None = None) -> None:
    """Record a ``no-live-holder`` refusal iff ``value`` has ANY holder.

    Called only on the resolver's "no live holder" path: a name with at least
    one (necessarily terminal) holder is D2's refused terminal-only reference;
    a name with NO holder is simply absent and must NOT be recorded.
    """
    if predicate is None:
        holders = all_name_holder_ids(g, label, value,
                                      case_insensitive=case_insensitive_name)
    else:
        holders = [r[0] for r in g.query(
            f"MATCH (n) WHERE {predicate} "
            f"AND {_name_predicate(case_insensitive_name)} "
            "RETURN n.id",
            params={"name": value}).result_set]
    if holders:
        record_non_folded(label, value, display_holder_ids(holders),
                          shape="no-live-holder")


def resolve_entity_id(g, label: str, value: str | None, *,
                      case_insensitive_name: bool = False,
                      addressing: str | None = None) -> str | None:
    """Route-then-refuse: the single live id for an id-or-name coordinate.

    Exact id match wins (returned regardless of status — an id is unambiguous
    even when its carrier is terminal; if two nodes CLAIM one id, that is
    corruption and refuses). Otherwise a NAME is resolved over live holders:
    exactly one -> its id (verified to be uniquely claimed); none live ->
    ``None`` (a TERMINAL-only holder is refused per D2 and recorded as a
    ``no-live-holder`` non-folded entry, so it is distinguishable from a
    genuinely absent name); **two or more live -> refuse**
    (:class:`AmbiguousEntityName`, non-folded entry recorded).

    ``addressing`` (``ADDRESSING_SOURCE`` / ``ADDRESSING_TARGET``) makes the
    uniqueness check cover the CALLER's full endpoint-resolution space (see
    :func:`_endpoint_idents`).
    """
    if value is None:
        return None
    _require_identity_label(label)
    by_id = g.query(
        f"MATCH (n:{label} {{id:$value}}) RETURN n.id",
        params={"value": value}).result_set
    id_hits = [r[0] for r in by_id if r[0]]
    if len(id_hits) > 1:
        # Two nodes claiming one id is raw corruption, never a resolvable read.
        _refuse(label, value, id_hits, shape="duplicate-id")
    if id_hits:
        if addressing:
            return _require_unique_id(g, label, id_hits[0],
                                      addressing=addressing)
        return id_hits[0]
    by_name = live_name_holder_ids(g, label, value,
                                  case_insensitive=case_insensitive_name)
    if len(by_name) > 1:
        _refuse(label, value, display_holder_ids(by_name))
    if by_name:
        if by_name[0] is None:
            _record_unaddressable(label, value)
        return _require_unique_id(g, label, by_name[0], addressing=addressing)
    # No LIVE holder. D2's ``single_terminal_holder_reference`` vector refuses
    # a terminal-only reference (a resolver must never hand a caller a terminal
    # id). Record it so "the holder is terminal" is distinguishable from "no
    # such name" — both return None, but only one is a refusal (#3633 §B.1).
    _record_if_any_holder(g, label, value, case_insensitive_name)
    return None


#: A document is a ``:Source`` carrying ``documentKind`` (D10, ONTOLOGY v3.15
#: §4.4); the pre-D10 ``:Document`` label is retired.
_DOCUMENT_PREDICATE = "(n:Object OR (n:Source AND n.documentKind IS NOT NULL))"


def resolve_document_target_id(g, value: str | None) -> str | None:
    """Route-then-refuse for an Object **or** document Source coordinate.

    The candidate space is the one ``file_human_approval`` has always probed:
    an ``Object``, or a ``Source`` carrying ``documentKind``. ``url`` is a legal
    coordinate on EITHER (``create_object(..., url=...)`` stores it on an
    Object), so the url arm probes the whole predicate, not just ``Source``.
    Precedence is id > url > name — the two exact identifiers first (an id is
    unambiguous; a document's ``url`` is its identity key), the natural key
    last. A name/url held by two or more live candidates, or an id claimed by
    two nodes, refuses rather than unioning. A resolved id is then verified
    unique across the CALLER's addressing space (``ADDRESSING_LABELS``), since
    the id-keyed write keeps the first label match.

    Returns the resolved node's ``id``, or ``None`` when nothing matches.
    """
    if value is None:
        return None
    id_rows = g.query(
        f"MATCH (n) WHERE {_DOCUMENT_PREDICATE} AND n.id = $value "
        "RETURN n.id",
        params={"value": value}).result_set
    id_hits = [r[0] for r in id_rows if r[0]]
    if len(id_hits) > 1:
        _refuse("Object|document-Source", value, id_hits,
                shape="duplicate-id")
    if id_hits:
        return _require_unique_id(
            g, "Object", id_hits[0], addressing=ADDRESSING_TARGET,
            claimant_predicate=_DOCUMENT_PREDICATE)
    url_rows = g.query(
        f"MATCH (n) WHERE {_DOCUMENT_PREDICATE} AND n.url = $value "
        "RETURN n.id, n.url",
        params={"value": value}).result_set
    # Holder-count refusal first (two candidates sharing a url is ambiguity,
    # not a pick), then addressability: a node carrying a ``url`` but NO ``id``
    # has no id for an id-keyed write to address — `create_edge`'s TARGET OR-set
    # is id|eventId only (`projection/edges.py`), so returning its url would
    # make `file_human_approval` report success while silently dropping the
    # `uses` edge. That is the "vacuous value indistinguishable from no data"
    # this module refuses.
    if len(url_rows) > 1:
        _refuse("Object|document-Source", value,
                display_holder_ids([r[0] for r in url_rows]))
    if url_rows:
        if url_rows[0][0] is None:
            _record_unaddressable("Object|document-Source", value)
        return _require_unique_id(
            g, "Object", url_rows[0][0], addressing=ADDRESSING_TARGET,
            claimant_predicate=_DOCUMENT_PREDICATE)
    name_rows = g.query(
        f"MATCH (n) WHERE {_DOCUMENT_PREDICATE} "
        f"AND {_name_predicate(False)} "
        f"AND {_terminal_excluded('n.status')} "
        "RETURN n.id",
        params={"name": value}).result_set
    # Holder-count refusal (a None entry is an id-less live holder).
    name_hits = [r[0] for r in name_rows]
    if len(name_hits) > 1:
        _refuse("Object|document-Source", value,
                display_holder_ids(name_hits))
    if name_hits:
        if name_hits[0] is None:
            _record_unaddressable("Object|document-Source", value)
        return _require_unique_id(
            g, "Object", name_hits[0], addressing=ADDRESSING_TARGET,
            claimant_predicate=_DOCUMENT_PREDICATE)
    # Same D2 refusal as the label resolver: a terminal-only holder is refused
    # and recorded, so it is distinguishable from an absent name.
    _record_if_any_holder(g, "Object|document-Source", value, False,
                          predicate=_DOCUMENT_PREDICATE)
    return None
