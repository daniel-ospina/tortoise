"""Navigation — entity-centric graph traversal and profiling.

entityProfile: BFS from entity, categorize connected nodes by type.
tortoise_traverse: multi-hop traversal following ALL relationship types.
"""
from __future__ import annotations  # noqa: I001

import re
from typing import Any


#: Root-lookup branches for _resolve_root (issue #327). Every branch is a
#: labeled lookup so the planner uses Node By Index Scan instead of All Node
#: Scan. Mirrors the legacy `MATCH (n) WHERE n.id = $eid` semantics for the
#: canonical entity labels + Session: nodes are matched by their property id
#: (Event.id == eventId, so the eventId branch is equivalent).
#:
#: #5448: this is the THIRD label->identity table (the canonical declaration is
#: `projection._CANONICAL_ENTITY_ID_PROPS` + `_CANONICAL_ENTITY_SECONDARY_ID_PROPS`,
#: resolved by `FalkorProjection._RESOLVE_BRANCHES`). It used to be id-only, so a
#: url-only ingestion stub — the shape `create_point(extractedFrom=...)` ->
#: `_link_source` -> `_mint_source_stub` mints, and the read path has always
#: resolved — could not be a traversal ROOT: `entityProfile`/`tortoise_traverse`
#: answered `{}` for a node `get_entity` resolves. The `Source`/`url` branch closes
#: that, and the set is now PINNED to the canonical declaration by
#: `tests/test_navigation.py::test_navigation_roots_every_canonical_identity_key`
#: so the two tables cannot drift silently again. `Session` is the one deliberate
#: addition (a legacy root outside `_CANONICAL_ENTITY_ID_PROPS`).
#: Org/APIKey roots are intentionally out of scope (they carry id but live in
#: the registry graph / control plane).
_ROOT_BRANCHES = (
    ("Point", "id"), ("Subject", "id"), ("Object", "id"),
    ("Source", "id"), ("Session", "id"),
    ("Event", "eventId"), ("Source", "url"),
)

#: The identity properties a traversal frontier may be matched by (#5448). Bounded
#: to the canonical identity keys for the same reason `_resolve_entity` asserts its
#: own set: the key is interpolated into query text, so a data-derived value must
#: never reach it.
_HOP_KEYS = frozenset({"id", "eventId", "url"})

#: The canonical identity keys in PRIORITY order — the order a node's identity is
#: resolved in when it carries more than one. Mirrors the coalesce the SDK's own
#: endpoint resolver uses (``sdk._fetch_endpoint_info``:
#: ``coalesce(n.id, n.url, n.eventId)``) and the declaration at
#: ``projection._CANONICAL_ENTITY_ID_PROPS`` (``id`` per canonical label,
#: ``eventId`` for a legacy id-less ``:Event``) plus
#: ``_CANONICAL_ENTITY_SECONDARY_ID_PROPS`` (``url`` — a ``:Source``'s secondary
#: identity, #4649). The SET is pinned equal to ``_HOP_KEYS`` and a superset of
#: every declared identity key by
#: ``tests/test_navigation.py::test_identity_key_order_covers_every_declared_identity_key``,
#: so a fourth identity key cannot be added to the canonical tables and silently
#: left out of the traversal identity here.
_IDENTITY_KEY_ORDER = ("id", "url", "eventId")


def _node_identity(props: dict) -> tuple[Any, str | None]:
    """A node's canonical identity as ``(value, key)``, or ``(None, None)``.

    ``key`` is the property the value came from, so the next hop can bind THAT
    property (``{key:$eid}``) instead of assuming ``id``. The assumption is the
    defect #8538 fixes: a url-keyed ``:Source`` carries no ``id``, so a hop that
    bound ``{id:$eid}`` off it matched nothing and the node was unreachable for
    every hop after the one that discovered it.
    """
    for key in _IDENTITY_KEY_ORDER:
        value = props.get(key)
        if value:
            return value, key
    return None, None


def _resolve_root(g: Any, entity_id: str) -> tuple[str | None, dict, str | None]:
    """Index-backed root lookup: returns (label, properties, key) or (None, {}, None).

    One UNION query over labeled branches — each branch index-backed (#327).
    ``key`` is the identity property the matched branch used (``id`` / ``eventId``
    / ``url``), so the first hop can bind the root's OWN key instead of assuming
    ``id``. A url-only `:Source` root carries no ``id``, so the old
    ``MATCH (n:Source {id:$eid})`` hop from it matched nothing: the root was
    found but came back with an empty neighbourhood (#5448).
    """
    union_q = " UNION ".join(
        f"MATCH (n:{label}) WHERE n.{prop} = $eid "
        f"RETURN '{label}' AS label, properties(n) AS props, '{prop}' AS key "
        f"LIMIT 1"
        for label, prop in _ROOT_BRANCHES
    )
    rows = g.query(union_q, params={"eid": entity_id}).result_set
    if not rows:
        return None, {}, None
    row = rows[0]
    label, props = row[0], row[1]
    # A 2-column row is the pre-#5448 shape (mock fixtures, legacy callers): the
    # key is then unknowable from the row, and "id" is what every one of those
    # callers matched, so it preserves their behavior exactly.
    key = row[2] if len(row) > 2 else "id"
    parsed = dict(props)
    if label == "Source":
        # #3998 (D30): the ROOT is a read path too. `_parse_node` filters the
        # CONNECTED nodes; filtering only there left the same `:Source` root
        # handing its raw payload straight back — measured, a document created
        # with a 2400-byte `text` returned that `text` on the root through
        # `entityProfile` AND `tortoise_traverse`, while `get_entity` correctly
        # withheld it. A guarantee that holds on one read path and not another
        # is not a guarantee, so the root takes the same filter as its
        # neighbours.
        parsed = _filter_source_props(parsed)
    # #8538: the ROOT carries the same public identity as a discovered
    # neighbour. `key` is a public, addressable key (a url-only `:Source`'s
    # `url` is also the value the caller passed), so surfacing it as `id` keeps
    # the node shape identical on both sides of the hop and keeps this surface
    # free of the internal FalkorDB handle `_parse_node` would otherwise
    # synthesise (#44). An id-carrying node is unchanged.
    if not parsed.get("id"):
        parsed["id"] = entity_id
    parsed["type"] = label
    return label, parsed, key


#: Safe label-identifier pattern — fail-closed before interpolating a graph
#: label into Cypher (issue #327 security review). Node labels are created by
#: the app's own hardcoded MERGE statements today, but a future data-derived
#: label must never reach query text.
_SAFE_LABEL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


#: Per-hop frontier-node match: label AND identity key on the SOURCE side in
#: BOTH directions. (Verified: `MATCH (n:Point)<-[r]-(m)` -> Index Scan; the
#: neighbor-labeled form `MATCH (n)<-[r]-(m:Point)` degrades to All Node Scan —
#: the trap.) The key is the one the frontier node was found by: the root's own
#: key (``url`` for a url-only `:Source`, #5448), and for a DISCOVERED neighbour
#: the property its identity came from (#8538 — assuming ``id`` left a url-keyed
#: `:Source` unreachable for every hop after the one that found it).
def _hop_query(label: str, key: str = "id") -> str | None:
    if not _SAFE_LABEL_RE.match(label or "") or key not in _HOP_KEYS:
        # Unknown label -> no nodes match (legacy behavior: the property-id
        # lookup matched nothing for labels without an id property).
        return None
    # #8538: the dedupe filter compares the NEIGHBOUR's canonical identity, not
    # `m.id` alone. A url-keyed `:Source` has no `id` property, so `NOT m.id IN
    # $visited` evaluated to NULL for it and Cypher's WHERE dropped the row —
    # the node existed, `get_entity` resolved it, and navigation never saw it.
    # `coalesce(m.id, m.url, m.eventId)` is the same order `_node_identity`
    # resolves (and the same coalesce `sdk._fetch_endpoint_info` uses), so the
    # value compared here is exactly the value `_parse_node_with_identity`
    # records in `visited` and binds on the next hop. A node carrying NONE of
    # the three still coalesces to NULL and is skipped — unchanged, and
    # deliberate: it has no public identity any read path could address.
    return (
        f"MATCH (n:{label} {{{key}:$eid}})-[r]->(m) "
        f"WHERE NOT coalesce(m.id, m.url, m.eventId) IN $visited "
        f"RETURN m, type(r), r.idx UNION "
        f"MATCH (n:{label} {{{key}:$eid}})<-[r]-(m) "
        f"WHERE NOT coalesce(m.id, m.url, m.eventId) IN $visited "
        f"RETURN m, type(r), r.idx"
    )


def entityProfile(
    db: Any,               # FalkorDB client (duck-typed: needs .select_graph())
    graph_name: str,        # e.g. "tortoise", "episodic"
    entity_id: str,
    hops: int = 2,
    pointKind: str | None = None,
    confidenceMin: float | None = None,
) -> dict:
    """BFS from an entity node, following all edge types up to `hops` depth.

    Returns {entity: {...}, connected: {points, documents, events, subjects, objects}}.
    Categorizes connected nodes by label, optionally filtering by pointKind and confidenceMin.
    Each connected node carries ``_relationship`` (edge type) and ``_idx`` (#6101 —
    the traversed edge's idx: on an operator's IMPL/NAND/hasPart edge 0 names the
    operator's SOURCE and >= 1 a TARGET; ``None`` when the edge carries no idx).
    """
    g = db.select_graph(graph_name)

    # 1. Fetch the root entity (index-backed labeled union, #327)
    root_label, root, root_key = _resolve_root(g, entity_id)
    if root_label is None:
        return {"entity": {}, "connected": {"points": [], "documents": [],
               "events": [], "subjects": [], "objects": []}}

    # 2. BFS — follow all edges for `hops` levels. Frontier tracks (id, label,
    #    key); the per-hop query binds the frontier node's label AND identity key
    #    on the SOURCE side so every hop uses a range index (issue #327). The root
    #    carries the key `_resolve_root` matched it by (a url-only `:Source` root
    #    has no `id`); a discovered neighbour carries ITS OWN key (#8538) — the
    #    property its identity came from — so a hop THROUGH a url-keyed `:Source`
    #    binds `{url:$eid}` instead of the `{id:$eid}` that matches nothing.
    visited: set[str] = {entity_id}
    frontier: list[tuple[str, str, str]] = [(entity_id, root_label, root_key)]
    connected: list[dict] = []

    for _ in range(hops):
        next_frontier: list[tuple[str, str, str]] = []
        for fid, flabel, fkey in frontier:
            hq = _hop_query(flabel, fkey)
            rows = (g.query(hq, params={"eid": fid, "visited": list(visited)})
                    .result_set if hq else [])
            for row in rows:
                node, nid, nkey = _parse_node_with_identity(row[0])
                rel_type = row[1] if len(row) > 1 else None
                # #6101: same edge metadata as tortoise_traverse — idx 0 = the
                # operator's SOURCE, >= 1 = a TARGET, None on non-operator edges.
                edge_idx = row[2] if len(row) > 2 else None
                nlabel = node.get("type")
                if nid and nid not in visited:
                    node["_relationship"] = rel_type
                    node["_idx"] = edge_idx
                    connected.append(node)
                    visited.add(nid)
                    next_frontier.append((nid, nlabel, nkey))
        frontier = next_frontier

    # 3. Categorize by label, apply filters
    result: dict[str, list] = {
        "points": [], "documents": [], "events": [], "subjects": [], "objects": [],
    }
    for node in connected:
        label = node.get("type", "").lower()
        # Apply filters
        if pointKind and node.get("pointKind") != pointKind:
            continue
        if confidenceMin is not None:
            conf = node.get("confidence", 0.5)
            if conf < confidenceMin:
                continue
        # Categorize
        if label == "point":
            result["points"].append(node)
        elif label == "document" or (label == "source"
                                     and node.get("documentKind") is not None):
            # D10 (ONTOLOGY v3.15 §4.4): a document IS a :Source, so its graph
            # label is "source" — keying only on the retired "document" label
            # left this bucket permanently empty. A Source carrying a
            # documentKind is a document; a provenance/session Source (no
            # documentKind) keeps falling through to the `else` stash below,
            # exactly as before. The bare "document" branch is retained for a
            # pre-rebuild graph that still holds :Document nodes.
            result["documents"].append(node)
        elif label == "event":
            result["events"].append(node)
        elif label == "subject":
            result["subjects"].append(node)
        elif label == "object":
            result["objects"].append(node)
        else:
            # Unknown type → stash under points as fallback
            result["points"].append(node)

    return {"entity": root, "connected": result}


def tortoise_traverse(
    db: Any,               # FalkorDB client
    graph_name: str,        # e.g. "tortoise"
    entity_id: str,
    max_hops: int = 2,
) -> dict:
    """Multi-hop graph traversal from an entity following ALL relationship types.

    Returns {entity: {...}, nodes: [{"node": {...}, "relationship": "...",
    "idx": N, "depth": N}, ...]}. Each connected node includes the relationship
    type, BFS depth, and the traversed edge's ``idx`` (#6101) — the position of
    the OPERATOR-INPUT end of the edge. On an operator's IMPL/NAND/hasPart edge
    that is 0 for the operator's SOURCE and >= 1 for a TARGET; without ``idx``
    an operator's two endpoints are an unordered pair, so a cycle reads as its
    reverse. The key is ``None`` when the edge carries no ``idx`` (non-operator
    edges, and edges predating the property).
    """
    g = db.select_graph(graph_name)

    # Fetch root (index-backed labeled union, #327)
    root_label, root, root_key = _resolve_root(g, entity_id)

    # BFS with depth tracking; frontier carries (id, label, key, depth) so each
    # per-hop query can bind the source label AND identity key for index-backed
    # lookups. The root keeps the key `_resolve_root` matched it by (#5448: a
    # url-only `:Source` root has no `id`); a discovered neighbour carries its
    # OWN key (#8538), so a hop through a url-keyed `:Source` binds `{url:$eid}`.
    visited: set[str] = {entity_id}
    frontier: list[tuple[str, str, str, int]] = (
        [(entity_id, root_label, root_key, 0)] if root_label else [])
    nodes: list[dict] = []

    for _ in range(max_hops):
        next_frontier: list[tuple[str, str, str, int]] = []
        for fid, flabel, fkey, depth in frontier:
            hq = _hop_query(flabel, fkey)
            rows = (g.query(hq, params={"eid": fid, "visited": list(visited)})
                    .result_set if hq else [])
            for row in rows:
                node, nid, nkey = _parse_node_with_identity(row[0])
                rel_type = row[1] if len(row) > 1 else None
                # #6101: the hop's edge idx rides along (0 = operator SOURCE,
                # >= 1 = TARGET). Absent on non-operator edges and on legacy
                # edges written before idx existed.
                edge_idx = row[2] if len(row) > 2 else None
                nlabel = node.get("type")
                if nid and nid not in visited:
                    nodes.append({
                        "node": node,
                        "relationship": rel_type,
                        "idx": edge_idx,
                        "depth": depth + 1,
                    })
                    visited.add(nid)
                    next_frontier.append((nid, nlabel, nkey, depth + 1))
        frontier = next_frontier

    return {"entity": root, "nodes": nodes}


# ── Shared helper ──────────────────────────────────────

def _parse_node_with_identity(node: Any) -> tuple[dict, Any, str]:
    """Parse FalkorDB Node → ``(flat dict, identity value, identity key)``.

    The identity is resolved from the RAW property map, BEFORE the public
    ``id`` is synthesised. That order is load-bearing (#8538): a url-keyed
    ``:Source`` given ``id = url`` and then asked for its identity would report
    key ``id``, and the next hop would bind ``{id:$eid}`` — matching nothing.
    Resolving first keeps the key the node is ACTUALLY addressable by.

    The public ``id`` is the node's canonical identity (``id`` | ``url`` |
    ``eventId``), so a url-keyed ``:Source`` surfaces as ``id = url``: the same
    value ``_resolve_root``/``get_entity``/``_RESOLVE_BRANCHES`` resolve it by,
    and never the internal FalkorDB numeric handle (#44).
    """
    if hasattr(node, 'properties'):
        props = dict(node.properties)
        labels = list(node.labels or [])
        raw_id = str(node.id)
    else:
        # Raw list form: [id, [labels], [[k, v], ...]]
        props = {k: v for k, v in node[2]}
        labels = list(node[1] or [])
        raw_id = str(node[0])
    if "Source" in labels:
        props = _filter_source_props(props)
    value, key = _node_identity(props)
    if value is None:
        # No public identity at all: the internal handle is the only dedupe key
        # available (test mocks, and a corrupt node). Such a node is
        # unreachable through every read path AND is skipped by `_hop_query`'s
        # coalesce filter (NULL there too), so in production this value never
        # leaves the BFS's own `visited` set.
        value, key = raw_id, "id"
    props["id"] = value
    props["type"] = labels[0] if labels else "unknown"
    return props, value, key


def _parse_node(node: Any) -> dict:
    """Parse FalkorDB Node → flat dict {id, type, ...properties}.

    Handles FalkorDB Node objects (1.6+) and raw list form for testing.

    Prefers the public ``id`` property (ULID) from node properties over the
    internal FalkorDB numeric node ID.  Falls back to the internal ID only
    when nodes lack any public identity property (``id`` | ``url`` |
    ``eventId``) — edge case, typically test mocks.  See #44: traverse was
    leaking internal IDs downstream; see #8538: a url-keyed ``:Source`` has no
    ``id``, and its public identity is its ``url``, so that is what ``id``
    becomes.
    """
    return _parse_node_with_identity(node)[0]


def _filter_source_props(props: dict) -> dict:
    """#3998 (D30): a `:Source` bag must not leave the graph unfiltered.

    `entityProfile` and `tortoise_traverse` are LIVE MCP tools, and a graph
    written before the `:Source` passthrough closed still holds payload-bearing
    Sources — so an unfiltered bag here re-hands the bytes the read filter
    closes elsewhere, one module away from it. The filter runs BEFORE the
    synthetic `type` key is set: `type` is not a stored property, so the
    declaration filter would drop it.
    """
    from tortoise.projection.entities import filter_source_props

    return filter_source_props(props)[0]
