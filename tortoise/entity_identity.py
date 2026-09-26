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
   and do not union: record a non-folded entry and raise
   :class:`AmbiguousEntityName`.

The "live" predicate is ``live._terminal_excluded`` — the same vocabulary every
other read surface uses — so a terminal (superseded/deprecated/archived/
retracted/outdated) holder never resolves a name.

Do NOT re-implement this resolution at a call site. A hand-rolled
``id = $x OR name = $x`` disjunction is exactly the defect this module replaces,
and a hand-rolled ``LIMIT 1`` is the silent-pick variant of it.
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

    ``candidate_ids`` names only the carriers that HAVE an id — what a caller
    can actually target. ``holder_count`` is the true number of live holders,
    which is HIGHER when a legacy/stub carrier carries no ``id`` at all. The
    count is never inferred from ``candidate_ids``: an id-less holder is still
    a live holder, and the whole point of the refusal is that the coordinate
    did not resolve to one identity.
    """

    def __init__(self, label: str, name: str, candidate_ids,
                 holder_count: int | None = None):
        self.label = label
        self.name = name
        self.candidate_ids = tuple(str(c) for c in candidate_ids if c)
        self.holder_count = (len(self.candidate_ids) if holder_count is None
                             else holder_count)
        super().__init__(
            f"{label} {name!r} resolves to {self.holder_count} live "
            f"carriers {list(self.candidate_ids)} — refusing to guess "
            f"(route-then-refuse, #3633)")


def _require_identity_label(label: str) -> None:
    if label not in _IDENTITY_LABELS:
        raise RuntimeError(
            f"entity identity: unsafe label {label!r} "
            f"({'/'.join(_IDENTITY_LABELS)} only)")


def record_non_folded(label: str, name: str, candidate_ids,
                      holder_count: int | None = None) -> None:
    """Record a refused name coordinate as a **non-folded entry** (no raise).

    The log line IS the non-folded record: it names the coordinate and the
    carriers that were deliberately not folded. A refusal site that must keep
    running (a candidate resolver, a batch probe that skips one ref) calls this
    before dropping the ref; a site that must fail loud calls
    :func:`_refuse`, which records and then raises.

    ``holder_count`` is the true live-holder count when some holder has no
    id (see :class:`AmbiguousEntityName`); it defaults to the number of ids
    supplied.
    """
    candidates = sorted(str(c) for c in candidate_ids if c)
    total = len(candidates) if holder_count is None else holder_count
    logger.warning(
        "entity-identity: refusing a name-keyed read — %s %r resolves to %d "
        "live carriers %s; recording a non-folded entry and refusing to guess "
        "(#3633 route-then-refuse)",
        label, name, total, candidates)


def _refuse(label: str, name: str, candidate_ids,
            holder_count: int | None = None) -> None:
    """Record the non-folded entry, then refuse.

    Recording before raising keeps the evidence even when a caller catches the
    exception.
    """
    record_non_folded(label, name, candidate_ids, holder_count)
    raise AmbiguousEntityName(label, name, candidate_ids, holder_count)


def live_name_holder_ids(g, label: str, name: str) -> list[str | None]:
    """Every LIVE holder of ``name`` for ``label`` — ONE ENTRY PER LIVE ROW.

    A holder with no ``id`` contributes ``None``. That is deliberate and
    load-bearing: this base still mints id-less ``:Object``/``:Subject`` nodes
    on a real path (a bare ``MERGE (o:Object {name:$name})`` — the aboutObject
    wiring in ``hosted_api``), and a "collect only the ids" version
    (``if r[0]``) SILENTLY DROPS such a holder from the count, so a name held by
    one id-bearing and one id-less live node resolves to the id-bearing carrier
    instead of refusing. That silent pick is the exact guess this module exists
    to eliminate, so never reintroduce an ``if r[0]`` filter here.

    Zero rows is a legal "no live holder"; the caller decides refusal, and a
    lone id-less holder resolves to ``None`` (there is no id to route to).
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
    holders: exactly one id-bearing holder -> its id; none, or a lone holder
    that carries no ``id`` (nothing to route to) -> ``None``; **two or more
    live holders -> refuse** (:class:`AmbiguousEntityName`, non-folded entry
    recorded) — an id-less holder COUNTING toward that refusal.
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
        _refuse(label, value, by_name, holder_count=len(by_name))
    return by_name[0] if by_name else None


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

    Returns the resolved node's ``id`` (for a document Source, ``id == url`` by
    construction), or ``None`` when nothing matches.
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
    # An `:Object` carries a `url` too (`_connect_issue_objects` writes
    # `MERGE (o:Object {id:$oid}) SET … o.url=$url`), and the probe this
    # resolver replaces matched `:Object` by URL — so the URL arm must probe the
    # SAME candidate space as the id and name arms, not `:Source` alone.
    url_rows = g.query(
        f"MATCH (n) WHERE {_DOCUMENT_PREDICATE} AND n.url = $value "
        "RETURN n.id, n.url",
        params={"value": value}).result_set
    # A candidate whose id is unset still has a url identity, so report the url
    # as the coordinate. NOTE: `create_edge`'s TARGET resolution is id/eventId
    # only ("a url-only stub Source is therefore NOT a valid target"), so this
    # preserves the pre-#3633 pass-through of the raw coordinate — it does not
    # promise the downstream edge lands.
    url_hits = [r[0] or r[1] for r in url_rows if (r[0] or r[1])]
    if len(url_hits) > 1:
        _refuse("Object|document-Source", value, url_hits)
    if url_hits:
        return url_hits[0]
    name_rows = g.query(
        f"MATCH (n) WHERE {_DOCUMENT_PREDICATE} AND n.name = $value "
        f"AND {_terminal_excluded('n.status')} "
        "RETURN n.id",
        params={"value": value}).result_set
    # One entry per live row: an id-less candidate still COUNTS toward the
    # ambiguity (see live_name_holder_ids) — a lone id-less candidate resolves
    # to ``None`` because there is no id to route to.
    name_hits = [r[0] for r in name_rows]
    if len(name_hits) > 1:
        _refuse("Object|document-Source", value, name_hits,
                holder_count=len(name_hits))
    return name_hits[0] if name_hits else None
