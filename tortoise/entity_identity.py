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

Do NOT re-implement this SINGLE-coordinate resolution at a call site. A
hand-rolled ``id = $x OR name = $x`` disjunction is exactly the defect this
module replaces, and a hand-rolled ``LIMIT 1`` is the silent-pick variant of
it. A BATCH probe that must keep the id space and the name space apart per ref
(``assembly.exact_objects``, ``commit_ops``'s two supersession probes) may
split the arms explicitly — but it must resolve each arm to at most one
carrier and refuse above that, never union them.
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
    """A coordinate resolves to more than one carrier — refuse, never guess.

    Raised by two kinds of site: a NAME held by two or more carriers (over the
    site's carrier predicate — LIVE holders for the name arms, all carriers for
    the deliberately-unfiltered batch/fold probes), and raw corruption (two
    nodes claiming one id, or two documents claiming one url).

    Carries structured fields so an MCP/HTTP boundary can map it to a 409
    without string-matching the message (the plan's "Open for the implementer"
    shape: ``label``, ``name``, ``candidate_ids``).
    """

    def __init__(self, label: str, name: str, candidate_ids,
                 carrier_count: int | None = None):
        self.label = label
        self.name = name
        self.candidate_ids = tuple(str(c) for c in candidate_ids)
        #: The number of carrier ROWS the site considered — NOT
        #: ``len(candidate_ids)``: an id-less legacy carrier is a carrier but
        #: contributes no routable id, so two of them are two carriers and zero
        #: candidate ids. Neutral by design: the unfiltered arms count terminal
        #: carriers too, so the message must not claim they are all live.
        self.carrier_count = (carrier_count if carrier_count is not None
                              else len(self.candidate_ids))
        super().__init__(
            f"{label} {name!r} resolves to {self.carrier_count} carrier(s) "
            f"{list(self.candidate_ids)} — refusing to guess "
            f"(route-then-refuse, #3633)")


def _require_identity_label(label: str) -> None:
    if label not in _IDENTITY_LABELS:
        raise RuntimeError(
            f"entity identity: unsafe label {label!r} "
            f"({'/'.join(_IDENTITY_LABELS)} only)")


def record_non_folded(label: str, name: str, candidate_ids,
                      carrier_count: int | None = None) -> None:
    """Record a refused name coordinate as a **non-folded entry** (no raise).

    The log line IS the non-folded record: it names the coordinate and the
    carriers that were deliberately not folded. A refusal site that must keep
    running (a candidate resolver, a batch probe that skips one ref, a
    supersession fold that skips an ambiguous ref) calls this before dropping
    the ref; a site that must fail loud calls :func:`_refuse`, which records
    and then raises.

    ``carrier_count`` is the number of carrier ROWS considered by the site,
    which for a live-filtered resolver is the live-holder count and for the
    deliberately-unfiltered batch/fold probes (see their `OVERRIDES` markers)
    counts terminal carriers too — hence the neutral wording.

    THIS FUNCTION IS THE SEAM for the projection-side recorder: when #3590's
    S0/S2 lands ``projection._record_non_fold`` (a shaped entry in the
    per-projection ``non_folded`` set the ``rebuild == live`` harness asserts
    empty), it wires in HERE and every site that calls it inherits the entry.
    Until then the entry is a WARNING log — the strongest record available on
    this base. `commit_ops`'s supersession refusals call this alongside their
    ``warn(...)`` so the fold path is not the one silent exception.
    """
    candidates = sorted(str(c) for c in candidate_ids)
    logger.warning(
        "entity-identity: refusing a name-keyed read — %s %r resolves to %d "
        "carrier(s) (%d with a routable id) %s; recording a non-folded "
        "entry and refusing to guess (#3633 route-then-refuse)",
        label, name,
        carrier_count if carrier_count is not None else len(candidates),
        len(candidates), candidates)


def _refuse(label: str, name: str, candidate_ids,
            carrier_count: int | None = None) -> None:
    """Record the non-folded entry, then refuse.

    Recording before raising keeps the evidence even when a caller catches the
    exception.
    """
    record_non_folded(label, name, candidate_ids, carrier_count)
    raise AmbiguousEntityName(label, name, candidate_ids, carrier_count)


def live_name_holder_ids(g, label: str, name: str, *,
                         case_insensitive: bool = False) -> list[str | None]:
    """One entry per LIVE node holding ``name`` for ``label`` (unordered).

    The entry is the node's ``id`` — or ``None`` for an id-less legacy holder,
    which is a live CARRIER all the same. The caller MUST count ROWS, not
    ids: filtering the ``None`` out before the ambiguity test lets an id-less
    carrier hide, so a name held by ``[None, 'sub-real']`` would resolve to
    ``'sub-real'`` (a silent pick among two live carriers) and one held by
    ``[None, None]`` would read as "no holder". Zero rows is a legal "no live
    holder"; the caller decides refusal.

    ``case_insensitive=True`` is for the ONE legacy surface whose contract is a
    case-folded name match (``TortoiseSDK.provenance``'s ``authoredBy``,
    pinned by ``test_provenance_case_insensitive_match``); it resolves over the
    SAME live-holder set and refuses on the SAME ≥2 count, so it is a matching
    mode, not a weaker identity rule.
    """
    _require_identity_label(label)
    if case_insensitive:
        cypher = (f"MATCH (n:{label}) WHERE toLower(n.name) = toLower($name) "
                  f"AND {_terminal_excluded('n.status')} RETURN n.id")
    else:
        cypher = (f"MATCH (n:{label} {{name:$name}}) "
                  f"WHERE {_terminal_excluded('n.status')} RETURN n.id")
    rows = g.query(cypher, params={"name": name}).result_set
    return [r[0] for r in rows]


def resolve_entity_id(g, label: str, value: str | None, *,
                      case_insensitive: bool = False) -> str | None:
    """Route-then-refuse: the single live id for an id-or-name coordinate.

    Exact id match wins (returned regardless of status — an id is unambiguous
    even when its carrier is terminal). Otherwise a NAME is resolved over live
    holders: exactly one -> its id; none -> ``None``; **two or more -> refuse**
    (:class:`AmbiguousEntityName`, non-folded entry recorded). An id-less live
    holder is a carrier for the ambiguity count even though it cannot be
    returned as a routable id.

    ``case_insensitive`` forwards to :func:`live_name_holder_ids` (name arm
    only; the id arm is always exact).
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
    by_name = live_name_holder_ids(g, label, value,
                                   case_insensitive=case_insensitive)
    if len(by_name) > 1:
        _refuse(label, value, [i for i in by_name if i], carrier_count=len(by_name))
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
    # The url arm keeps the ORIGINAL candidate space (`_DOCUMENT_PREDICATE`),
    # not `:Source` alone: the pre-#3633 probe accepted `n.url = $id` for an
    # :Object too (an issue-linked Object carries `url`, sdk.py's
    # `_connect_issue_objects`), so narrowing it would turn a resolvable
    # coordinate into "artifact does not exist".
    url_rows = g.query(
        f"MATCH (n) WHERE {_DOCUMENT_PREDICATE} AND n.url = $value "
        "RETURN n.id, n.url",
        params={"value": value}).result_set
    # A Source whose id is unset still has a url identity; fall back to the url
    # itself so the returned coordinate reaches create_edge's url branch.
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
    # Count ROWS: an id-less live candidate is a carrier for the ambiguity
    # test even though it yields no routable id.
    if len(name_rows) > 1:
        _refuse("Object|document-Source", value,
                [r[0] for r in name_rows if r[0]],
                carrier_count=len(name_rows))
    return name_rows[0][0] if name_rows and name_rows[0][0] else None
