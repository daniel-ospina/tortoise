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
#:
#: ⚠️ These names ride the RESPONSE too — this module returns the layer's stored
#: properties rather than renaming them, which is a deliberate departure from
#: ``GET /v1/points``' ``pointKind`` → ``kind``. There, `kind` is the REST name
#: for a property the point doors never accept as `pointKind`; here the write
#: door's own field name is `objectKind`/`subjectKind`, so a client writes and
#: reads the same word (and `?kind=` filters the thing it names).
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


def _layer_terminal_predicate(label: str) -> str:
    """The layer's OWN terminal vocabulary, through the shared predicate.

    ⛔ ``live._terminal_excluded``'s default is the POINT family
    (``TERMINAL_EXCLUDED_STATUSES``) and it ANDs the legacy ``outdated`` flag,
    which is a Point-only marker. Its docstring says so explicitly: *"The OBJECT
    read surfaces (#3301) pass ``commit_ops.OBJECT_TERMINAL_STATUSES`` — a
    different family with no ``outdated`` member — and must pass
    ``include_outdated_flag=False``"*. Handing an Object/Subject list the Point
    family would hide an entity ended by a word the entity family declares and
    nothing can mark, so the family is chosen per label here rather than
    defaulted.

    Behaviour-preserving TODAY — ``live.py:658`` records that the Object and
    Subject sets are subsets of the Point set and that no entity writer sets
    ``outdated`` — which is exactly why the wrong family would never show up as a
    visible bug, only as a silent drift when either set changes.
    """
    from .commit_ops import OBJECT_TERMINAL_STATUSES, SUBJECT_TERMINAL_STATUSES
    from .live import _terminal_excluded

    family = (SUBJECT_TERMINAL_STATUSES if label == "Subject"
              else OBJECT_TERMINAL_STATUSES)
    return _terminal_excluded("n.status", family, include_outdated_flag=False)


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
    the position axis cannot drift between the two list routes — with the layer's
    OWN status family (see ``_layer_terminal_predicate``), never the Point one.
    """
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

    conditions = [_layer_terminal_predicate(label)]
    params: dict[str, Any] = {}
    if kind:
        # ⛔ THE TWO SPELLINGS OF ONE KIND ARE THE SAME KIND, so the filter is an
        # equivalence set, not equality. `core:team` and `team` are both admitted
        # by the route's validator, and both are storable: a blank kind is
        # DROPPED by `kind_fallbacks.named_kind` (which also TRIMS surrounding
        # whitespace, so the stored value is the caller's spelling apart from
        # that trim), an uninstalled NAMESPACE is refused by
        # `hosted_api._require_approved_kind` — a check on the namespace, never
        # on the spelling — and nothing else rewrites the value. Measured: one
        # graph holds `team` and `core:team` side by side. So an EQUALITY filter
        # answered `200 count: 0, truncated: false` for the spelling that
        # happened not to be the stored one — the "byte-identical to *this graph
        # names no such thing*" page this read exists not to produce, and it was
        # a spelling the route's own refusal list recommends. The set is exactly
        # {bare, core:bare} for a `core:`-namespaced kind and its bare form; an
        # ARBITRARY namespace stays exact, because `zzz:goal` is a vocabulary
        # item of its own and must not pull in the bare `goal` rows.
        # ⛔ `kind_is_known` appears nowhere in this rule: it is a boolean
        # VALIDATOR (reached on a create through the warn-not-block
        # `kind_other_layer` advisory, and none of those calls raises or
        # rewrites), and a count of its calls is a property of the door and the
        # spelling, so no number for it is quoted here.
        conditions.append(f"n.{kind_prop} IN $kinds")
        params = {"kinds": kind_equivalents(kind)}
    where = " AND ".join(conditions)

    # ⛔ `ro_query` (GRAPH.RO_QUERY), never `query`, on every read in this module.
    # The verb is part of the contract, not a style choice (`cache_eviction.py`
    # states the same rule at its own boundary): `query` is GRAPH.QUERY, which
    # MINTS the named graph when it does not exist, and it is also the verb this
    # repo's privatisation ratchet counts as residue on an untyped receiver. A
    # GET that creates the store it was asked to read is a write, and this module
    # holds only reads.
    #
    # ⚠️ INHERITED RESIDUE, stated rather than implied: this module's OWN calls
    # hold the rule, but THREE delegations it reaches still resolve through
    # GRAPH.QUERY — `projection._resolve_entity` (behind every by-id route and
    # `/belief`), `navigation._resolve_root` (behind `entity_relations`), and the
    # SDK arm `/v1/sources` delegates to: `TortoiseSDK.list_knowledge(kind=
    # 'source')` -> `TortoiseSDK._list_sources`, which runs
    # `MATCH (s:Source) …` over `proj.g.query` (`sdk.py::_list_sources`). So
    # "the new GETs cannot mint a graph" is true of the Cypher below and NOT yet
    # of those three; fixing them is pre-existing debt in their own homes, not a
    # guarantee this module's comment can confer. A reviewer read the sentence
    # above as covering them, which is why the residue is named here — and read
    # it as covering only the two IDENTITY resolutions, which is why the third is
    # named with the route that reaches it.
    total_rows = proj.g.ro_query(
        f"MATCH (n:{label}) WHERE {where} RETURN count(n)", params=params,
    ).result_set
    total = int(total_rows[0][0]) if total_rows else 0
    rows = proj.g.ro_query(
        f"MATCH (n:{label}) WHERE {where} RETURN properties(n) "
        f"ORDER BY n.{order_prop}, id(n) SKIP $skip LIMIT $limit",
        params={**params, "skip": offset, "limit": limit},
    ).result_set
    return [dict(r[0]) for r in rows], total


def kind_equivalents(kind: str) -> list[str]:
    """The spellings that NAME THE SAME KIND as ``kind`` — ``[bare, core:bare]``.

    Deliberately NOT a namespace-stripping generalisation: an arbitrary namespace
    (``zzz:goal``) is its own vocabulary item, so it answers only itself, while
    the repo's ``core:`` namespace and the bare form are two spellings of one
    value (``kind_layers.declares_subject`` states the same equivalence for the
    binder). ``NIL_KINDS`` is its own pair for the same reason.
    """
    from tortoise.kind_layers import NIL_KINDS

    if kind in NIL_KINDS:
        return sorted(NIL_KINDS)
    namespace, sep, bare = kind.partition(":")
    if not sep:
        return [kind, f"core:{kind}"]
    if namespace == "core":
        return [kind, bare]
    return [kind]


def layer_kind_values(proj: Any, label: str) -> frozenset[str]:
    """The ``<layer>Kind`` spellings this graph actually CARRIES on ``label``.

    The second half of the vocabulary a layer list route validates its ``kind``
    filter against (``hosted_api._refuse_unknown_layer_kind``); the first half is
    the DECLARED vocabulary (:func:`tortoise.kind_layers.declared_layers`).

    ⛔ It is read from the GRAPH and not from a constant because the write doors
    on this property are OPEN: ``POST /v1/objects`` persists an ``objectKind`` no
    vocabulary declares (``commit_schema``'s #5202 note),
    ``create_subject(subjectKind="Subject")`` is accepted and STORED, and a pack
    names its own spellings (``venture:tranche``). A validator built ONLY from the
    declared vocabulary would refuse a filter over a row that exists — a false
    block on data the caller can otherwise page to and read by id, which is the
    over-block direction and worse than the silence it would be replacing.

    ⛔ NO terminal predicate and NO ``kind`` filter of its own: this answers
    "which spellings does this layer hold", so a kind whose rows are ALL
    tombstoned is still a real kind and its filtered page is honestly EMPTY.
    Reusing the list read's own WHERE here would make a fully-ended kind
    unanswerable by filter — reintroducing the same ambiguity one level down.

    ``label`` is a constant from :data:`LAYER_KIND_PROP`, and the property name
    comes from that map rather than from any caller, so nothing here is
    caller-controlled query text. ``ro_query`` on the same contract as every
    other call in this module.
    """
    label = _safe_label(label)
    kind_prop = LAYER_KIND_PROP.get(label)
    if kind_prop is None:
        raise ValueError(
            f"layer_kind_values has no declaration for label {label!r}")
    rows = proj.g.ro_query(
        f"MATCH (n:{label}) WHERE n.{kind_prop} IS NOT NULL "
        f"RETURN DISTINCT n.{kind_prop}",
    ).result_set
    return frozenset(str(r[0]) for r in rows if r[0] is not None)


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
    pack namespaces, ``[]`` means *"no installed pack is NAMED for this
    predicate"* (a core or structural predicate — legal and ordinary), and
    ``declarationsReadable: false`` means *"the registry could not be asked"*.
    Flattening the third into ``[]`` would report an unreadable registry as a
    positive finding about a vocabulary we could not see.

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
    #
    # ⚠️ A URL cannot be passed on the ROUTE (`{entity_id}` is one segment, and a
    # URL contains `/`), so a url-keyed Source reaches its relations only through
    # this function, not through `GET /v1/entities/{id}/relations`. Its own
    # document read is `/v1/sources/{url}` (which uses the `:path` converter).
    # Stated rather than implied, because the alternative — a `:path` converter
    # here — would greedily swallow the trailing `/relations`.
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

    # ⛔ `CALL { … } RETURN … ORDER BY … SKIP/LIMIT`, NOT `… UNION ALL … ORDER BY
    # … SKIP/LIMIT` directly. The flat form SILENTLY DROPS the window on the
    # deployed engine (FalkorDB graph module 42006): measured at three parallel
    # edges, `SKIP 0/1/2/3 LIMIT 2` each returned the SAME first rows, so
    # `offset` was inert and the store returned the node's ENTIRE edge set on
    # every request — `rows[:limit]` below would have been the only bound. The
    # single-branch form is unaffected (which is why `list_layer`'s paging
    # works), so this is the UNION shape's bug, not a parameter problem. The
    # wrapped form was measured to page correctly (`skip=0 → [B,C]`,
    # `skip=1 → [C,D]`).
    inner = (
        f"MATCH (n:{root_label} {{{root_key}:$eid}})-[r]->(m) "
        "RETURN coalesce(m.id, m.eventId, m.url) AS oid, labels(m) AS labels, "
        "       coalesce(m.name, m.content, m.url) AS name, "
        "       type(r) AS mechanism, coalesce(r.label, '') AS declared, "
        "       r.idx AS idx, 'out' AS direction, id(r) AS rid "
        "UNION ALL "
        f"MATCH (n:{root_label} {{{root_key}:$eid}})<-[r]-(m) "
        # ⛔ A SELF-LOOP matches BOTH orientations, so without this it is
        # returned TWICE — two rows, one edge, and `count` double-counts it.
        # Excluded on the reverse branch only, so a self-edge is reported once,
        # as outgoing (it is both; there is no third value that would be true).
        #
        # ⛔ THE GUARD IS `id(m) <> id(n)`, NOT the coalesced identity. The
        # coalesce form (`coalesce(m.id, m.eventId, m.url) <> coalesce(n.id,
        # n.eventId, n.url)`) answers NULL for a neighbour that carries NO
        # identity property, and a NULL WHERE drops the row — so every INBOUND
        # edge from an identity-less node silently vanished from the answer,
        # which is the "read as isolated" defect this read exists to avoid.
        # `id()` is total (every node has one), so the self-loop test is exact
        # and no neighbour is filtered out for lacking a name.
        "WHERE id(m) <> id(n) "
        "RETURN coalesce(m.id, m.eventId, m.url) AS oid, labels(m) AS labels, "
        "       coalesce(m.name, m.content, m.url) AS name, "
        "       type(r) AS mechanism, coalesce(r.label, '') AS declared, "
        "       r.idx AS idx, 'in' AS direction, id(r) AS rid"
    )
    query = (
        f"CALL {{ {inner} }} RETURN oid, labels, name, mechanism, declared, "
        "      idx, direction ORDER BY direction, mechanism, oid, idx, rid "
        "SKIP $skip LIMIT $limit"
    )
    # `limit + 1`: the INTERNAL probe, never a caller-visible cap — the caller's
    # bound still governs what is delivered (`rows[:limit]` below), so the
    # route's `le=1000` stays the ceiling and no cap VALUE changes here.
    rows = proj.g.ro_query(
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
