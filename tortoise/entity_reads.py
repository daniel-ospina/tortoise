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
        # `hosted_api._require_approved_kind` when the graph's approval set is
        # not ``None`` — the #2714 back-compat rule in
        # `pack_state.kind_namespace_is_approved`: ``None`` means no
        # `:PackInstall` records AND no `:PackSelection` marker (every namespace
        # admitted), while a graph CONFIGURED empty (`set_graph_packs(packs=[])`,
        # #2728) resolves to ``frozenset()`` and refuses — UNLESS its data leg
        # already carries the namespace (`recorded ∪ data`, #2714 indicator 2, so
        # a namespace this graph has written stays writable). That is a check on
        # the namespace, never on the spelling — and nothing else rewrites the
        # value.
        # Measured: one
        # graph holds `team` and `core:team` side by side. So an EQUALITY filter
        # answered `200 count: 0, truncated: false` for the spelling that
        # happened not to be the stored one — the "byte-identical to *this graph
        # names no such thing*" page this read exists not to produce, and it was
        # a spelling the route's own refusal list recommends. The set is exactly
        # {bare, core:bare} for a `core:`-namespaced kind and its bare form; an
        # ARBITRARY namespace stays exact, because `kind_equivalents` folds ONLY
        # the repo's `core:` namespace — `zzz:goal` names one value and must not
        # pull in the bare `goal` rows.
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
    # ⚠️ INHERITED RESIDUE, stated rather than implied — SEVEN delegated helpers
    # behind these GETs still resolve through GRAPH.QUERY (`query`), the verb
    # that MINTS a graph which does not exist; this module's OWN calls hold the
    # `ro_query` rule above. `proj.g.query` is the wrapper that dispatches that
    # minting verb (`projection/__init__.py::_ProjectionHandle.query` →
    # `_dispatch_decided(…, "query", …)`), which is why this is residue and not
    # a style note. Enumerated PER ROUTE rather than counted, because a count
    # with no route beside it is what let this paragraph read two of five, and
    # then four of five, and then five of six, under three separate reviewers:
    #
    #   * `projection.FalkorProjection._resolve_entity`
    #     (`projection/__init__.py:21099`) — behind every by-id route
    #     (`/v1/objects/{id}`, `/v1/subjects/{id}`, `/v1/sources/{id}`) AND
    #     `/belief`'s own resolution;
    #   * `navigation._resolve_root` (`navigation.py:95`) — behind
    #     `entity_relations`;
    #   * `TortoiseSDK._list_sources` (`sdk.py:31679`) — behind `/v1/sources`,
    #     which reaches it through `list_knowledge(kind='source')`;
    #   * `ep.TortoiseEP._read_node` (`ep.py:790`, the `self.g.query` at `:804`)
    #     — behind `/belief`, through `sdk.check_confidence(claim_id=…)` →
    #     `_get_confidence` → `ep.compute_confidence`;
    #   * `TortoiseSDK._get_point` (`sdk.py:30632`, the cache arm's
    #     `_cache_handle().g.query` at `:30717`) — behind `/belief`'s evidence
    #     arm for a Point, through `sdk._provenance`;
    #   * `TortoiseSDK._provenance` itself (`sdk.py:38256`) — the CALLER of
    #     `_get_point` above, and its own minting reads are the Subject arm at
    #     `:38292` and the delegation rels at `:38315`, on top of
    #     `_raw_entry_for_point`'s `proj.g.query` at `:38239` and
    #     `resolve_entity_id_same_entity(proj.g, …)` at `:38288`
    #     (`entity_identity.py`'s `g.query`) — so listing only its callee would
    #     read as one locus where there are three;
    #   * `TortoiseSDK._uncalibrated_ep_participants` (`sdk.py:43696`, the
    #     `proj.g.query` inside its per-label loop) — behind EVERY `/belief` that
    #     has a belief to report, through `sdk.check_confidence` →
    #     `_get_confidence` → `_ensure_calibrated` (that gate is on by default:
    #     `_ep_require_calibration_default()` is `True`).
    #
    # The unit is ENTRY-POINT HELPERS (seven), over nine query-issuing functions
    # once the callees named above are counted — stated explicitly because the
    # sentence that follows is easy to read as a complete census of FUNCTIONS.
    #
    # So "the new GETs cannot mint a graph" is true of the Cypher IN THIS MODULE
    # and NOT yet of those seven resolutions: the object/subject by-id routes
    # inherit one, `entity_relations` one, `/v1/sources` one, and `/belief` four
    # (`_read_node` + `_provenance` + its `_get_point` callee +
    # `_uncalibrated_ep_participants`) on top of the
    # `_resolve_entity` its own resolution already reaches. Fixing them is pre-existing debt in their own
    # homes; it is not a guarantee this module's comment can confer. ⛔ A reader
    # auditing these routes' write-verb exposure takes this paragraph as the
    # COMPLETE residual, so an enumeration that is short by even one is a false
    # clean verdict on a route — the "claim wider than the measurement" class
    # this comment exists to prevent.
    #
    # ⛔ And ONE MORE, which the SEVEN above do not cover and a review measured
    # (P3): every one of the eight routes is served with
    # `attach_projection=True` (`hosted_api._data_sdk_with_projection`), and
    # `Projection.__init__` runs `_ensure_indexes()`, whose body issues
    # `self.g.query("CREATE INDEX …")` / a `DROP INDEX …` — so on a graph whose
    # schema is not current, the FIRST GET of ANY route here mints schema. That
    # is a SEPARATELY DECLARED write class (`_graph_metadata_write_scope(
    # "schema-ddl")`, #9124), not an undeclared one, and it is conditional on
    # `_schema_is_current()` — but it does mean no route in this file is literally
    # free of `GRAPH.QUERY`, so a reader must not take the seven resolutions above
    # as the whole story: "the new GETs cannot mint a graph" is true of the Cypher
    # in THIS MODULE and of those resolutions only.
    #
    # ⛔ AND THE HANDLE OPEN IS NOT FREE EITHER, measured rather than assumed
    # (review at 6c6b0f88): an instrumented census of `_GuardedGraph.query` shows
    # FOUR statements issued by the projection open on EVERY one of the eight
    # routes, before the route's own read — `FalkorProjection._probe_ok` (×2),
    # `_schema_is_current` and `_array_valued_fts_prop_exists` — all through
    # `query`, the minting verb. So this paragraph's "seven resolutions" is a
    # census of ROUTE-LEVEL reads, NOT of every GRAPH.QUERY a request can emit:
    # the open is shared, pre-existing machinery (`_data_sdk_offloaded(
    # attach_projection=True)`), it fires whether or not the schema is current,
    # and fixing the seven does not make these GETs non-minting. Anyone auditing
    # write-verb exposure must count the open too.
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


#: The most characters of a neighbour's own text a relation row carries. The
#: same VALUE as the inline ``[:200]`` in ``search_engine.get_relationships``'
#: ``related_content`` (there is no named constant there to share) — a relation
#: list is per-EDGE, so an unbounded preview scales as
#: ``limit × largest neighbour body`` (#14593 review P2, measured at 4000 chars
#: through a Point). Deliberately NOT imported from ``search_engine``: that slice
#: is inline to its own rendering, and coupling the two would mean a change there
#: silently re-caps a public REST field here.
_ENDPOINT_NAME_MAX = 200

#: Which property answers "the neighbour's own name" — PER LAYER, and the
#: ``ELSE`` arm deliberately does NOT read ``content`` (#14593 review P2, second
#: round). ``content`` is a Point's own text (handled explicitly) and, for anything
#: else that carries it, a LEGACY DOCUMENT PAYLOAD: ``projection.entities`` names
#: ``content`` in ``_DOC_RETIRED_KEYS`` as the pre-D10 document body, and
#: ``migrate_kinds`` records that ``_upsert_document`` DID store the raw ``content``
#: on its subpopulation — so a bare ``(:Document {id})`` left behind by an
#: interrupted ``relabel_legacy_documents`` is exactly the shape the Source arm
#: exists to stop, and with the blanket fallback still in place it was MEASURED to
#: answer ``other.name`` with the full planted body. ``:Event`` loses nothing here:
#: its narrative lives in ``session_narrative``, never on the node
#: (``_EVENT_SERVER_MANAGED_PROPS``).
_RELATION_ENDPOINT_NAME = (
    "CASE WHEN m:Source THEN m.url "
    "     WHEN m:Point THEN coalesce(m.content, m.name) "
    # ⛔ ``m.eventId`` last, the same order the IDENTITY projection uses in the
    # SELECT beside this one (``coalesce(m.id, m.eventId, m.url) AS oid``): an
    # ``:Event``'s identity key is ``eventId`` and it writes no ``id``
    # (``why.py``: ``MERGE (e:Event {eventId:$eid})``), so without it an unnamed
    # Event neighbour answered ``name: null`` while the SAME row carried its
    # eventId as ``other.id`` — measured (#14593 review P3).
    "     ELSE coalesce(m.name, m.url, m.id, m.eventId) END"
)


def _root_preview(entity_id: str, root_label: str, root: dict) -> dict:
    """The entity the caller ASKED for — same BOUND, same FLAG, not same coalesce.

    ⛔ THE PARITY IS CAP + FLAG, AND ONLY THAT (#14593 review, four rounds). The
    CAP (``_ENDPOINT_NAME_MAX``) and the ``nameTruncated`` FLAG are shared with a
    neighbour's preview, so a client may treat both sides the same way.

    ⛔ THE COALESCE IS NOT SHARED, and this docstring does NOT paraphrase it —
    earlier revisions did, three times, and each copy was wider than the code:
    "the same coalesce … so the two sides render the same way" (wrong: they do
    not), "the asymmetry is SOURCE-SHAPED" (wrong: it is not layer-specific), and
    a per-layer restatement that gave ``:Point → content`` while the arm is
    ``coalesce(m.content, m.name)``. The rule has ONE home — the code:
    ``_RELATION_ENDPOINT_NAME`` ABOVE (the neighbour side, per layer) and
    ``raw = …`` BELOW (the root side, one ``or``-chain). Read those two sites.

    What is asserted HERE is only what was measured, and it is enough to stop the
    mistake this paragraph exists for: the two sides CAN answer different strings
    for the same node — MEASURED on this head for a ``:Point`` carrying both
    ``name`` and ``content``, for a ``:Source`` carrying a ``name``, and for an
    ``:Event`` carrying ``content``. ``_create_source(url, "web",
    name="My Doc Name")`` is the shortest of the three: ``name: "My Doc Name"`` as
    a ROOT, ``name: <url>`` as a neighbour.
    ⛔ Operationally: do not dedupe ``entity.name`` against ``other.name`` on any
    layer, and do not infer the per-layer rule from this comment — it does not
    state one.

    The full body of a Point is not lost by the cap: the root is by definition
    reachable at its own route (``/v1/points/{id}`` and siblings), and this route
    answers "what is this entity connected to", not "give me its text".

    ``:Source`` cannot reach ``content`` here: ``navigation._resolve_root``
    filters a Source root before this point, which is why the ``#3998`` payload
    rule for that layer is satisfied upstream of this helper.
    """
    raw = (root.get("name") or root.get("content")
           or root.get("url") or root.get("eventId"))
    name = None if raw is None else str(raw)
    return {
        "id": entity_id,
        "label": root_label,
        "name": None if name is None else name[:_ENDPOINT_NAME_MAX],
        "nameTruncated": name is not None and len(name) > _ENDPOINT_NAME_MAX,
    }


def _endpoint_preview(row: Any) -> dict:
    """The OTHER endpoint of one edge, as identity + label + its own name.

    ⛔ Deliberately NOT ``properties(m)``. Two reasons, and the second is not
    stylistic: (1) a relation list's payload is per-EDGE, so returning whole
    nodes multiplies a dense node's fan-out by its neighbours' size; (2) a
    ``:Source`` neighbour would hand back its stored payload — the exact
    disclosure ``#3998``'s filter exists to stop, on a route that never asked for
    it.

    ⛔ THE BOUND IS PART OF THE CONTRACT, not a nicety (#14593 review P2). The
    field is a *preview*, and ``_ENDPOINT_NAME_MAX`` is the same 200-character
    limit ``search_engine.get_relationships`` applies to the equivalent
    ``related_content``. Without it this route's per-request size scaled as
    ``limit × largest neighbour body``: MEASURED on this tree, one ``related``
    edge between two Points whose ``content`` was 4000 characters returned
    ``other.name`` at 4000 — a ``:Point`` carries no ``name``, so the fallback
    landed on ``content`` — and ``limit`` is ``le=1000``. A cut is MARKED
    (``nameTruncated`` below) rather than silent, because a shortened value a
    caller cannot detect is the "absence read as an answer" class this PR
    removes elsewhere.

    ⛔ And the per-layer SELECTION is done in Cypher (``_RELATION_ENDPOINT_NAME``
    above the two UNION branches), because a single blanket ``coalesce(m.name,
    m.content, m.url)`` reached ``content`` for a ``:Source`` too — the ``#3998``
    payload disclosure arriving through the name field, which
    ``filter_source_props`` never sees.
    ⚠️ The arms are NOT restated here. An earlier revision summarised them as
    "a Source by its ``url``; a Point by its ``content``; everything else by its
    ``name``", which was false twice over — the ``:Point`` arm is
    ``coalesce(m.content, m.name)`` and the ``ELSE`` arm ends in an identity
    (``url``, ``id``, ``eventId``), not in ``name`` alone. Read the constant.
    """
    labels = row[1] or []
    raw = row[2]
    name = None if raw is None else str(raw)
    return {
        "id": row[0],
        "label": (labels[0] if labels else None),
        "name": None if name is None else name[:_ENDPOINT_NAME_MAX],
        "nameTruncated": name is not None and len(name) > _ENDPOINT_NAME_MAX,
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
        f"       {_RELATION_ENDPOINT_NAME} AS name, "
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
        f"       {_RELATION_ENDPOINT_NAME} AS name, "
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
        "entity": _root_preview(entity_id, root_label, root),
        "relations": relations,
        "count": len(relations),
        "declarationsReadable": declared is not None,
    }, candidates
