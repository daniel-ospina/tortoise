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

This module is the ONE place that answers that question for a SINGLE
coordinate. It applies the governing plan's §B.1 disposition, *route then
refuse* (``docs/plans/2026-09-15-3590-minted-identity.md``):

1. **route** — resolve the coordinate to **exactly one id**. An exact id match
   wins outright (an id is an unambiguous identity; a name that happens to equal
   another node's id loses). Otherwise the name is resolved over **live**
   holders only.
2. **refuse** — when a name is held by two or more live entities, do not guess
   and do not union: record a non-folded entry and raise
   :class:`AmbiguousEntityName`.

The "live" predicate is ``live._terminal_excluded`` — the same vocabulary every
other read surface uses — so a terminal (superseded/deprecated/archived/
retracted/outdated) holder never resolves a name.

Do NOT re-implement this resolution at a call site. A hand-rolled
``id = $x OR name = $x`` disjunction is exactly the defect this module replaces,
and a hand-rolled ``LIMIT 1`` is the silent-pick variant of it.

Scope: this is the single-coordinate resolver. The BATCH probes keep their own
call-site discipline deliberately — ``assembly.docker_resolver_port
.exact_objects`` (id arm wins; name arm single-candidate-or-drop, over #3317's
Object vocabulary), ``commit_ops``' two supersession probes (a fold target may
itself be terminal), and ``onboarding.seed.find_subject_by_name`` (a duck-typed
handle, and it needs the resolved PROPS, not just an id). Those sites reuse this
discipline but cannot share this call; do not "consolidate" them here without
owning their predicates (see the ``OVERRIDES`` markers at each).
"""

from __future__ import annotations

import logging

from tortoise.live import _terminal_excluded

logger = logging.getLogger(__name__)

#: The only labels whose ``name`` is a legal *natural key*. Anything else is
#: refused loudly rather than interpolated into a query (parity with
#: ``resolve_structural_target``'s runtime label defense).
_IDENTITY_LABELS = ("Object", "Subject")


class AmbiguousEntityName(ValueError):
    """A name resolves to two or more live carriers — refuse, never guess.

    Carries structured fields so an MCP/HTTP boundary can map it to a 409
    without string-matching the message (the plan's "Open for the implementer"
    shape: ``label``, ``name``, ``candidate_ids``).
    """

    def __init__(self, label: str, name: str, candidate_ids):
        self.label = label
        self.name = name
        self.candidate_ids = tuple(_candidate_labels(candidate_ids))
        super().__init__(
            f"{label} {name!r} resolves to {len(self.candidate_ids)} live "
            f"carriers {list(self.candidate_ids)} — refusing to guess "
            f"(route-then-refuse, #3633)")


def _candidate_labels(candidate_ids) -> list[str]:
    """Render a holder set for a message — one label per live CARRIER.

    An id-less legacy carrier is a real holder, so it is labelled ``<no-id>``
    rather than dropped. Dropping it (the earlier behaviour) made a name held
    by an id-less carrier plus an id-bearing one read as "exactly one holder",
    which silently resolved the addressable one and discarded the other.
    """
    return [str(c) if c else "<no-id>" for c in candidate_ids]


def _require_identity_label(label: str) -> None:
    if label not in _IDENTITY_LABELS:
        raise RuntimeError(
            f"entity identity: unsafe label {label!r} "
            f"({'/'.join(_IDENTITY_LABELS)} only)")


def record_non_folded(label: str, name: str, candidate_ids) -> None:
    """Record a refused name coordinate as a **non-folded entry** (no raise).

    The log line IS the non-folded record: it names the coordinate and the
    carriers that were deliberately not folded. A refusal site that must keep
    running (a candidate resolver, a batch probe that skips one ref) calls this
    before dropping the ref; a site that must fail loud calls
    :func:`_refuse`, which records and then raises.
    """
    candidates = sorted(_candidate_labels(candidate_ids))
    logger.warning(
        "entity-identity: refusing a name-keyed read — %s %r resolves to %d "
        "live carriers %s; recording a non-folded entry and refusing to guess "
        "(#3633 route-then-refuse)",
        label, name, len(candidates), candidates)


def _refuse(label: str, name: str, candidate_ids) -> None:
    """Record the non-folded entry, then refuse.

    Recording before raising keeps the evidence even when a caller catches the
    exception.
    """
    record_non_folded(label, name, candidate_ids)
    raise AmbiguousEntityName(label, name, candidate_ids)


def live_name_holder_ids(g, label: str, name: str) -> list[str | None]:
    """Every LIVE holder of ``name`` for ``label`` (unordered), one entry per
    CARRIER — an id-less legacy holder contributes ``None``.

    The entry is KEPT rather than filtered out so ``len(...)`` counts live
    holders, not live ids: a name held by one id-less carrier AND one
    id-bearing carrier is TWO holders (refuse), never a silent resolve onto the
    addressable one. Zero rows is a legal "no live holder"; the caller decides
    refusal. This is the primitive a refusal site uses when it needs the raw
    holder set (e.g. a batch probe that must keep ``id``/``name`` apart).
    """
    _require_identity_label(label)
    rows = g.query(
        f"MATCH (n:{label} {{name:$name}}) "
        f"WHERE {_terminal_excluded('n.status')} "
        "RETURN n.id",
        params={"name": name}).result_set
    return [r[0] for r in rows]


def resolve_entity_id(g, label: str, value: str | None) -> str | None:
    """Route-then-refuse: the single live id for an id-or-name coordinate.

    Exact id match wins (returned regardless of status — an id is unambiguous
    even when its carrier is terminal). Otherwise a NAME is resolved over live
    holders: exactly one -> its id; none -> ``None``; **two or more -> refuse**
    (:class:`AmbiguousEntityName`, non-folded entry recorded).
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
        _refuse(label, value, id_hits)
    if id_hits:
        return id_hits[0]
    by_name = live_name_holder_ids(g, label, value)
    if len(by_name) > 1:
        _refuse(label, value, by_name)
    # A lone id-less holder has no id to hand back (and is unaddressable).
    return by_name[0] if by_name and by_name[0] else None


#: A document is a ``:Source`` carrying ``documentKind`` (D10, ONTOLOGY v3.15
#: §4.4); the pre-D10 ``:Document`` label is retired.
_DOCUMENT_PREDICATE = "(n:Object OR (n:Source AND n.documentKind IS NOT NULL))"


def resolve_document_target_id(g, value: str | None) -> str | None:
    """Route-then-refuse for an Object **or** document Source coordinate.

    The candidate space is the one ``file_human_approval`` has always probed:
    an ``Object``, or a ``Source`` carrying ``documentKind``. Precedence is
    id > url > name — the two exact identifiers first (an id is unambiguous; a
    document Source's ``url`` is its identity key), the natural key last. A name
    held by two or more live candidates, or an id claimed by two nodes, refuses
    rather than unioning.

    Returns the resolved node's ``id``, or ``None`` when nothing matches. A
    document Source's ``id`` normally equals its ``url`` (``_upsert_source``
    writes ``s.id = coalesce($id, $url)``), but an explicit-id ``SourceCreated``
    or a legacy node CAN carry ``id != url`` — hence the separate url arm. A
    candidate addressable ONLY by url and carrying NO ``id`` is unresolved
    (``None``), never returned as its url: no edge target accepts a url
    coordinate.
    """
    if value is None:
        return None
    id_rows = g.query(
        f"MATCH (n) WHERE {_DOCUMENT_PREDICATE} AND n.id = $value "
        "RETURN n.id",
        params={"value": value}).result_set
    id_hits = [r[0] for r in id_rows if r[0]]
    if len(id_hits) > 1:
        _refuse("Object|document-Source", value, id_hits)
    if id_hits:
        return id_hits[0]
    url_rows = g.query(
        "MATCH (n:Source) WHERE n.documentKind IS NOT NULL AND n.url = $value "
        "RETURN n.id",
        params={"value": value}).result_set
    # Return the node's real ID only. A url-addressed document Source normally
    # has id == url, so the id arm above already resolved it; this arm exists
    # for the id != url case. An id-less Source is NOT a usable coordinate:
    # ``create_edge``'s TARGET resolves by id | eventId and has NO url branch
    # (``projection/edges.py::create_edge`` documents "a url-only stub Source
    # is therefore NOT a valid target"), so substituting the url here would
    # validate a node the caller cannot then be linked to and silently drop the
    # ``uses`` edge. Refuse it instead (falls through to the name arm, then to
    # None -> the caller's explicit ValueError).
    # Count every url CARRIER, not just the id-bearing ones: two Sources
    # sharing one url (one id-less) is a duplicate-identity claim and must
    # refuse, never silently return the addressable one (the same principle the
    # id arm applies to a duplicate id).
    url_carriers = [r[0] for r in url_rows]
    if len(url_carriers) > 1:
        _refuse("Object|document-Source", value, url_carriers)
    if url_carriers and url_carriers[0]:
        return url_carriers[0]
    name_rows = g.query(
        f"MATCH (n) WHERE {_DOCUMENT_PREDICATE} AND n.name = $value "
        f"AND {_terminal_excluded('n.status')} "
        "RETURN n.id",
        params={"value": value}).result_set
    # Count every live CARRIER here too (see live_name_holder_ids): one id-less
    # plus one id-bearing same-name candidate is TWO candidates -> refuse, not a
    # silent resolve onto the addressable one.
    name_carriers = [r[0] for r in name_rows]
    if len(name_carriers) > 1:
        _refuse("Object|document-Source", value, name_carriers)
    return name_carriers[0] if name_carriers and name_carriers[0] else None
