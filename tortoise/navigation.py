"""Navigation — entity-centric graph traversal and profiling.

entityProfile: BFS from entity, categorize connected nodes by type.
tortoise_traverse: multi-hop traversal following ALL relationship types.
"""
from __future__ import annotations

from typing import Any


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

    # 1. Fetch the root entity
    root_rows = g.query(
        "MATCH (n) WHERE n.id = $eid RETURN n",
        params={"eid": entity_id},
    ).result_set
    if not root_rows:
        return {"entity": {}, "connected": {"points": [], "documents": [],
               "events": [], "subjects": [], "objects": []}}
    root = _parse_node(root_rows[0][0])

    # 2. BFS — follow all edges for `hops` levels
    visited: set[str] = {entity_id}
    frontier: list[str] = [entity_id]
    connected: list[dict] = []

    for _ in range(hops):
        next_frontier: list[str] = []
        for fid in frontier:
            rows = g.query(
                "MATCH (n)-[r]->(m) WHERE n.id = $eid AND NOT m.id IN $visited "
                "RETURN m, type(r) UNION "
                "MATCH (n)<-[r]-(m) WHERE n.id = $eid AND NOT m.id IN $visited "
                "RETURN m, type(r)",
                params={"eid": fid, "visited": list(visited)},
            ).result_set
            for row in rows:
                node = _parse_node(row[0])
                rel_type = row[1] if len(row) > 1 else None
                nid = node.get("id")
                if nid and nid not in visited:
                    node["_relationship"] = rel_type
                    connected.append(node)
                    visited.add(nid)
                    next_frontier.append(nid)
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

    # Fetch root
    root_rows = g.query(
        "MATCH (n) WHERE n.id = $eid RETURN n",
        params={"eid": entity_id},
    ).result_set
    root = _parse_node(root_rows[0][0]) if root_rows else {}

    # BFS with depth tracking
    visited: set[str] = {entity_id}
    frontier: list[tuple[str, int]] = [(entity_id, 0)]  # (id, depth)
    nodes: list[dict] = []

    for _ in range(max_hops):
        next_frontier: list[tuple[str, int]] = []
        for fid, depth in frontier:
            rows = g.query(
                "MATCH (n)-[r]->(m) WHERE n.id = $eid AND NOT m.id IN $visited "
                "RETURN m, type(r) UNION "
                "MATCH (n)<-[r]-(m) WHERE n.id = $eid AND NOT m.id IN $visited "
                "RETURN m, type(r)",
                params={"eid": fid, "visited": list(visited)},
            ).result_set
            for row in rows:
                node = _parse_node(row[0])
                rel_type = row[1] if len(row) > 1 else None
                nid = node.get("id")
                if nid and nid not in visited:
                    nodes.append({
                        "node": node,
                        "relationship": rel_type,
                        "depth": depth + 1,
                    })
                    visited.add(nid)
                    next_frontier.append((nid, depth + 1))
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
