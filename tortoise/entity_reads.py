"""#14592 Phase A — the ONE read layer behind the entity/layer read routes.

The product could WRITE all four ontology layers (Point · Object · Subject ·
Source) and read back only Points. Object and Subject were write-only over REST,
Source had no route at all, and neither relations nor confidence had one — while
every one of those reads already existed behind the MCP door. REST parity is
therefore a REUSE problem, not a query problem, and this module exists so it
stays one: the routes in ``hosted_api`` hold no Cypher of their own.

**What delegates to what** (the table the PR body restates):

===========================================  =========================================
route                                         the function it calls
===========================================  =========================================
``GET /v1/objects``, ``/v1/subjects``         :func:`list_layer` (this module)
``GET /v1/sources``                           ``TortoiseSDK.list_knowledge(kind='source')``
                                              — the ``tortoise_list_knowledge`` door's arm
``GET /v1/{objects,subjects,sources}/{id}``   ``FalkorProjection._resolve_entity`` +
                                              ``projection.entities.filter_source_props``
                                              — the two functions ``TortoiseSDK.get_entity``
                                              (the ``tortoise_get_entity`` door) is composed of
``GET /v1/entities/{id}/relations``           :func:`entity_relations` (this module)
``GET /v1/entities/{id}/belief``              ``TortoiseSDK.check_confidence(claim_id=…)`` +
                                              ``TortoiseSDK._provenance`` +
                                              ``event_store.latest_for_node``
===========================================  =========================================

⛔ **NO MCP TOOL AND NO PUBLIC SDK METHOD IS ADDED OR RENAMED.** The two reads
that had no reusable function (``list_layer``, ``entity_relations``) live here,
free functions over a projection, so Phase B's ``entity_profile`` can adopt them
without a surface change and so this module adds nothing to ``TOOL_REGISTRY`` or
to the public ``TortoiseSDK`` methods (#14592's contradiction test (a)).

⛔ **§2.0 THE RECORD IS TRUTH — these reads do not consult ``TORTOISE_RECORD_SERVE``.**
They read the graph, exactly as ``GET /v1/points`` (the list route this parity
mirrors) and ``GET /v1/search`` already do. The record seam is armed on the
ANCHORED single-point read (``GET /v1/points/{id}``) only; extending it here
would be a second, divergent implementation of a seam with six named fallback
reasons, and pretending otherwise in the response shape would be worse than
saying it: a client of these routes is told nothing about which store answered
because there is only one store being asked. Phase D re-derives against the
record.
"""
from __future__ import annotations

from typing import Any

#: The ontology layers this module can list, with the property that holds each
#: layer's KIND discriminator. Read from the writers' own vocabulary rather than
#: re-spelled here: ``POST /v1/objects`` writes ``objectKind``, ``POST
#: /v1/subjects`` writes ``subjectKind`` (both via ``TortoiseSDK._create_object``
#: / ``_create_subject``), and a filter naming a different property would answer
#: an empty page for a graph full of the thing the caller asked for.
LAYER_KIND_PROP: dict[str, str] = {
    "Object": "objectKind",
    "Subject": "subjectKind",
}

#: The property a layer's rows are ordered by. Objects and Subjects both carry
#: ``name`` (the identity the create doors key them by). Kept as a declaration
#: rather than a query literal so ``list_layer`` cannot order by a property the
#: layer does not have — an ordering property absent from every row makes
#: ``SKIP``/``LIMIT`` windows non-adjacent slices of anything (#5346).
LAYER_ORDER_PROP: dict[str, str] = {"Object": "name", "Subject": "name"}


def _safe_label(label: str) -> str:
    """Refuse a label that is not an identifier before it reaches query text.

    The labels this module queries are all constants from :data:`LAYER_KIND_PROP`
    and from navigation's own root table, so this can only fire on a programming
    error — which is exactly when a data-derived value must not be interpolated
    (``navigation._SAFE_LABEL_RE``'s rule, applied at this module's boundary too
    rather than trusting every caller).
    """
    from .navigation import _SAFE_LABEL_RE

    if not _SAFE_LABEL_RE.match(label or ""):
        raise ValueError(f"refusing a non-identifier graph label: {label!r}")
    return label


def list_layer(
    proj: Any,
    label: str,
    *,
    limit: int,
    offset: int = 0,
    kind: str | None = None,
) -> tuple[list[dict], int]:
    """``(rows, total)`` for one ontology layer, paged over a TOTAL order.

    The read behind ``GET /v1/objects`` and ``GET /v1/subjects``. There was no
    list read for either layer anywhere in the tree (verified: no
    ``list_knowledge`` arm, no SDK method, no MCP tool), which is what made the
    layers write-only; this is that read, in ONE place.

    ``total`` is an EXACT ``count()`` under the same WHERE as the page, not a
    ``limit+1`` probe — so the route can publish a real total rather than a
    lower bound wearing a total's clothes (``truncation.py``'s recorded rule),
    and a caller can walk ``offset`` to completion.

    ``skip``/``limit`` paging requires the order to be TOTAL (#5346): ``name``
    alone is not (two Objects may share one name — #3590 D2 makes same-name
    carriers legal), and with a non-total order the separate query each page
    issues returns non-adjacent slices, so a walk drops rows and repeats others
    while the count still reports the enumeration complete. ``id(n)`` is unique
    per node, so ``(name, id(n))`` is total. Deliberately ``id(n)`` rather than
    ``n.id``: entity ids are NOT unique across labels and ids are the thing this
    layer's own writes can collide on.

    ⛔ ENDED ROWS ARE EXCLUDED from the list, and reachable by id — the same
    tombstone contract ``GET /v1/points`` states (#432/#689/#13034), read through
    the SAME shared predicate (``live._terminal_excluded``) so the vocabulary and
    the position axis cannot drift between the two list routes.
    """
    from .live import _terminal_excluded

    label = _safe_label(label)
    order_prop = LAYER_ORDER_PROP.get(label)
    kind_prop = LAYER_KIND_PROP.get(label)
    if order_prop is None or kind_prop is None:
        raise ValueError(f"list_layer has no declaration for label {label!r}")

    # #8447's sibling hazard, one direction over: an offset past the end is a
    # legitimate EMPTY page (not an error), but a NEGATIVE one is a caller bug
    # that would otherwise reach Cypher. The route's own ``Query(ge=0)`` refuses
    # it at the door; this is the belt to that braces so a direct caller cannot
    # smuggle one in.
    if offset < 0:
        raise ValueError(f"offset must be >= 0, got {offset}")
    if limit < 1:
        raise ValueError(f"limit must be >= 1, got {limit}")

    conditions = [_terminal_excluded("n.status")]
    params: dict[str, Any] = {}
    if kind:
        conditions.append(f"n.{kind_prop} = $kind")
        params = {"kind": kind}
    where = " AND ".join(conditions)

    total_rows = proj.g.query(
        f"MATCH (n:{label}) WHERE {where} RETURN count(n)", params=params,
    ).result_set
    total = int(total_rows[0][0]) if total_rows else 0
    rows = proj.g.query(
        f"MATCH (n:{label}) WHERE {where} RETURN properties(n) "
        f"ORDER BY n.{order_prop}, id(n) SKIP $skip LIMIT $limit",
        params={**params, "skip": offset, "limit": limit},
    ).result_set
    return [dict(r[0]) for r in rows], total


def _endpoint_preview(row: Any) -> dict:
    """The OTHER endpoint of one edge, as identity + label + its own name.

    ⛔ Deliberately NOT ``properties(m)``. Two reasons, and the second is not
    stylistic: (1) a relation list's payload is per-EDGE, so returning whole
    nodes multiplies a dense node's fan-out by its neighbours' size; (2) a
    ``:Source`` neighbour would hand back its stored payload — the exact
    disclosure ``#3998``'s filter exists to stop, on a route that never asked for
    it. The name falls back through each layer's own primary text property
    (``name`` for Object/Subject/Event, ``content`` for Point, ``url`` for
    Source), which is the same coalesce ``search_engine.get_relationships`` uses
    for ``related_content``.
    """
    labels = row[1] or []
    return {
        "id": row[0],
        "label": (labels[0] if labels else None),
        "name": row[2],
    }


def entity_relations(
    proj: Any,
    entity_id: str,
    *,
    limit: int,
    offset: int = 0,
) -> tuple[dict, int]:
    """``(body, candidates)`` for every directed edge touching ``entity_id``.

    The read behind ``GET /v1/entities/{id}/relations``. ⛔ **No existing
    function returns this**, which is why it is extracted rather than reused —
    and the two nearest neighbours each answer a different question, stated here
    so a future lane does not "consolidate" onto the wrong one:

    * ``navigation.entityProfile`` follows EVERY edge type but returns no
      direction: its per-hop query UNIONs ``(n)-[r]->(m)`` with ``(n)<-[r]-(m)``
      and keeps only ``type(r)``, so which way the edge runs is gone by the time
      the row is read.
    * ``search_engine.get_relationships`` (behind ``sdk._expand_relationships``)
      DOES carry direction, but only for the EPISTEMIC predicates
      (``IMPL|NAND|hasPart``, direct or operator-mediated). An extracted
      entity↔entity relation — the ``relations[]`` an extraction states, written
      through ``create_edge`` — is a STRUCTURAL edge (``causes``, ``addresses``,
      ``ownedBy`` …) and does not appear in it at all. Answering this route from
      that read would report an entity with a full structural neighbourhood as
      isolated, which is the "read as-isolated" defect #6976 records one layer
      down.

    Direction, predicate and mechanism are kept as three separate facts rather
    than folded into one string, because they are three: ``mechanism`` is the
    stored edge type (``IMPL``/``NAND``/``hasPart`` for an operator's typed edge,
    or the predicate itself for a structural edge), ``predicate`` is the verb the
    edge carries (``r.label`` where an operator named one, else the type), and
    ``direction`` says which end of it this node sits on. That vocabulary is
    ``get_relationships``', not a second one invented here.

    ⛔ **THE DECLARATION IS THREE-VALUED, NEVER TWO.** ``declaredBy`` is a list of
    pack namespaces, ``[]`` means *"declared by no installed pack"* (a core or
    structural predicate, perfectly legal), and ``declarationsReadable: false``
    means *"the registry could not be asked"*. Flattening the third into ``[]``
    would report an unreadable registry as a positive finding that nothing
    declares the predicate — a false claim about a vocabulary we could not see.

    ``candidates`` is the ``limit + 1`` PROBE row, exactly as ``GET /v1/points``
    reads its own cap: it proves *there are more edges than this page delivers*
    without a second COUNT over the whole edge set, so the caller passes
    ``exact=False`` and never publishes the probe as a total.
    """
    from .navigation import _HOP_KEYS, _resolve_root

    # Identity comes from navigation's own root table (`_resolve_root` is the
    # index-backed labeled union `entityProfile`/`tortoise_traverse` resolve a
    # traversal root with), so a url-keyed `:Source` — which carries no `id` —
    # is addressable here exactly as it is there, and there is no second
    # label→identity table to drift (#5448).
    root_label, root, root_key = _resolve_root(proj.g, entity_id)
    if root_label is None:
        return ({"entity": None, "relations": [], "count": 0,
                 "declarationsReadable": True}, 0)
    _safe_label(root_label)
    if root_key not in _HOP_KEYS:
        # `_resolve_root` can only produce a key from its own constant table;
        # this cannot fire unless that table gained a fourth identity key that
        # this build's `_HOP_KEYS` has not seen. Refuse rather than interpolate.
        raise ValueError(
            f"resolution produced an unsupported identity key {root_key!r}")

    # Both directions in ONE statement, each row carrying its own direction
    # literal — the shape `_hop_query` uses, with the one column it drops kept.
    # The source side binds `{key:$eid}` so each branch is index-backed on the
    # anchor's own identity table (#327).
    query = (
        f"MATCH (n:{root_label} {{{root_key}:$eid}})-[r]->(m) "
        "RETURN coalesce(m.id, m.eventId, m.url) AS oid, labels(m) AS labels, "
        "       coalesce(m.name, m.content, m.url) AS name, "
        "       type(r) AS mechanism, coalesce(r.label, '') AS declared, "
        "       r.idx AS idx, 'out' AS direction "
        "UNION ALL "
        f"MATCH (n:{root_label} {{{root_key}:$eid}})<-[r]-(m) "
        "RETURN coalesce(m.id, m.eventId, m.url) AS oid, labels(m) AS labels, "
        "       coalesce(m.name, m.content, m.url) AS name, "
        "       type(r) AS mechanism, coalesce(r.label, '') AS declared, "
        "       r.idx AS idx, 'in' AS direction "
        # ``ORDER BY`` on a UNION sorts the UNION's result, and every column is
        # NAMED in both branches so it depends on no engine-specific rendering
        # of an expression. The order is TOTAL for one node's edge set:
        # (direction, mechanism, other endpoint, idx) — ``idx`` is the last key
        # because Cypher allows PARALLEL edges between the same pair, so without
        # it a SKIP/LIMIT window would not partition the set (#5346's rule, one
        # relation over).
        "ORDER BY direction, mechanism, oid, idx SKIP $skip LIMIT $limit"
    )
    # `limit + 1`: the INTERNAL probe, never a caller-visible cap — the caller's
    # bound still governs what is delivered (`rows[:limit]` below), so the
    # route's `le=1000` stays the ceiling and no cap VALUE changes here.
    rows = proj.g.query(
        query, params={"eid": entity_id, "skip": offset, "limit": limit + 1},
    ).result_set
    candidates = len(rows)
    rows = rows[:limit]

    from .pack_registry import declared_relation_packs

    declared = declared_relation_packs()
    relations: list[dict] = []
    for row in rows:
        mechanism = row[3]
        declared_spelling = row[4]
        predicate = declared_spelling or mechanism
        relations.append({
            "direction": row[6],
            "predicate": predicate,
            "mechanism": mechanism,
            "other": _endpoint_preview(row),
            "idx": row[5],
            # `None` (not `[]`) when the registry could not be read: see the
            # three-valued rule in this function's docstring.
            "declaredBy": (None if declared is None
                           else list(declared.get(predicate, ()))),
        })
    return {
        "entity": {
            "id": entity_id,
            "label": root_label,
            # The root's own name, on the same coalesce as a neighbour's, so the
            # two sides of a relation render the same way.
            "name": (root.get("name") or root.get("content")
                     or root.get("url") or root.get("eventId")),
        },
        "relations": relations,
        "count": len(relations),
        "declarationsReadable": declared is not None,
    }, candidates
