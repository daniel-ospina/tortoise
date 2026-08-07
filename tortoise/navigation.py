"""Navigation — entity-centric graph traversal and profiling.

entityProfile: BFS from entity, categorize connected nodes by type.
tortoise_traverse: multi-hop traversal following ALL relationship types.
"""
from __future__ import annotations

import re
from typing import Any


#: Root-lookup branches for _resolve_root (issue #327). Every branch is a
#: labeled lookup so the planner uses Node By Index Scan instead of All Node
#: Scan. Mirrors the legacy `MATCH (n) WHERE n.id = $eid` semantics exactly:
#: nodes are matched by their property id (Event.id == eventId, so the
#: eventId branch is equivalent; Source nodes carry id except url-only
#: ingestion stubs, which the legacy query also never matched). Session
#: roots are preserved for BFS over CONTAINS edges.
_ROOT_BRANCHES = (
    ("Point", "id"), ("Subject", "id"), ("Object", "id"),
    ("Document", "id"), ("Source", "id"), ("Session", "id"),
    ("Event", "eventId"),
)


def _resolve_root(g: Any, entity_id: str) -> tuple[str | None, dict]:
    """Index-backed root lookup: returns (label, properties) or (None, {}).

    One UNION query over labeled branches — each branch index-backed (#327).
    """
    union_q = " UNION ".join(
        f"MATCH (n:{label}) WHERE n.{prop} = $eid "
        f"RETURN '{label}' AS label, properties(n) AS props LIMIT 1"
        for label, prop in _ROOT_BRANCHES
    )
    rows = g.query(union_q, params={"eid": entity_id}).result_set
    if not rows:
        return None, {}
    label, props = rows[0][0], rows[0][1]
    parsed = dict(props)
    parsed["type"] = label
    return label, parsed


#: Safe label-identifier pattern — fail-closed before interpolating a graph
#: label into Cypher (issue #327 security review). Node labels are created by
#: the app's own hardcoded MERGE statements today, but a future data-derived
#: label must never reach query text.
_SAFE_LABEL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


#: Per-hop frontier-node match: label on the SOURCE side in BOTH directions.
#: (Verified: `MATCH (n:Point)<-[r]-(m)` -> Index Scan; the neighbor-labeled
#: form `MATCH (n)<-[r]-(m:Point)` degrades to All Node Scan — the trap.)
def _hop_query(label: str) -> str | None:
    if not _SAFE_LABEL_RE.match(label or ""):
        # Unknown label -> no nodes match (legacy behavior: the property-id
        # lookup matched nothing for labels without an id property).
        return None
    return (
        f"MATCH (n:{label} {{id:$eid}})-[r]->(m) "
        f"WHERE NOT m.id IN $visited RETURN m, type(r) UNION "
        f"MATCH (n:{label} {{id:$eid}})<-[r]-(m) "
        f"WHERE NOT m.id IN $visited RETURN m, type(r)"
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
    """
    g = db.select_graph(graph_name)

    # 1. Fetch the root entity (index-backed labeled union, #327)
    root_label, root = _resolve_root(g, entity_id)
    if root_label is None:
        return {"entity": {}, "connected": {"points": [], "documents": [],
               "events": [], "subjects": [], "objects": []}}

    # 2. BFS — follow all edges for `hops` levels. Frontier tracks (id, label);
    #    the per-hop query binds the frontier node's label on the SOURCE side
    #    so every hop uses the id range index (issue #327).
    visited: set[str] = {entity_id}
    frontier: list[tuple[str, str]] = [(entity_id, root_label)]
    connected: list[dict] = []

    for _ in range(hops):
        next_frontier: list[tuple[str, str]] = []
        for fid, flabel in frontier:
            hq = _hop_query(flabel)
            rows = (g.query(hq, params={"eid": fid, "visited": list(visited)})
                    .result_set if hq else [])
            for row in rows:
                node = _parse_node(row[0])
                rel_type = row[1] if len(row) > 1 else None
                nid = node.get("id")
                nlabel = node.get("type")
                if nid and nid not in visited:
                    node["_relationship"] = rel_type
                    connected.append(node)
                    visited.add(nid)
                    next_frontier.append((nid, nlabel))
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
        elif label == "document":
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

    Returns {entity: {...}, nodes: [{"node": {...}, "relationship": "...", "depth": N}, ...]}.
    Each connected node includes the relationship type and BFS depth.
    """
    g = db.select_graph(graph_name)

    # Fetch root (index-backed labeled union, #327)
    root_label, root = _resolve_root(g, entity_id)

    # BFS with depth tracking; frontier carries (id, label, depth) so each
    # per-hop query can bind the source label for index-backed lookups.
    visited: set[str] = {entity_id}
    frontier: list[tuple[str, str, int]] = (
        [(entity_id, root_label, 0)] if root_label else [])
    nodes: list[dict] = []

    for _ in range(max_hops):
        next_frontier: list[tuple[str, str, int]] = []
        for fid, flabel, depth in frontier:
            hq = _hop_query(flabel)
            rows = (g.query(hq, params={"eid": fid, "visited": list(visited)})
                    .result_set if hq else [])
            for row in rows:
                node = _parse_node(row[0])
                rel_type = row[1] if len(row) > 1 else None
                nid = node.get("id")
                nlabel = node.get("type")
                if nid and nid not in visited:
                    nodes.append({
                        "node": node,
                        "relationship": rel_type,
                        "depth": depth + 1,
                    })
                    visited.add(nid)
                    next_frontier.append((nid, nlabel, depth + 1))
        frontier = next_frontier

    return {"entity": root, "nodes": nodes}


# ── Shared helper ──────────────────────────────────────

def _parse_node(node: Any) -> dict:
    """Parse FalkorDB Node → flat dict {id, type, ...properties}.

    Handles FalkorDB Node objects (1.6+) and raw list form for testing.

    Prefers the public ``id`` property (ULID) from node properties over the
    internal FalkorDB numeric node ID.  Falls back to the internal ID only
    when nodes lack an ``id`` property (edge case, typically test mocks).
    See #44: traverse was leaking internal IDs downstream.
    """
    if hasattr(node, 'properties'):
        props = dict(node.properties)
        if "id" not in props:
            props["id"] = str(node.id)
        props["type"] = node.labels[0] if node.labels else "unknown"
        return props
    # Raw list form: [id, [labels], [[k, v], ...]]
    props = {k: v for k, v in node[2]}
    if "id" not in props:
        props["id"] = str(node[0])
    props["type"] = node[1][0] if node[1] else "unknown"
    return props
