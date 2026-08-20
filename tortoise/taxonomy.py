"""Taxonomy tools — read-only entity counting and domain enumeration.

P0-5 tortoise_taxonomy, P0-6 tortoise_list_domains, P0-7 tortoise_list_topics.
One Cypher query per tool. No mutations.
"""
from __future__ import annotations

from typing import Any  # noqa: F401


def taxonomy(proj) -> dict[str, int]:
    """Count entities by node label. Returns {Point: N, Event: N, ...}."""
    labels = ("Point", "Event", "Subject", "Object", "Document")
    result: dict[str, int] = {}
    for label in labels:
        n = proj.g.query(f"MATCH (n:{label}) RETURN count(n)").result_set[0][0]
        result[label] = n
    return result


def list_topics(proj, entity_id: str) -> dict:
    """entityProfile lite: connected topics grouped by kind.

    Returns {entity, pointKind, neighbors: [{pointKind}], neighborCounts: {kind: N}}.
    """
    entity = proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN properties(n)",
        params={"id": entity_id},
    ).result_set
    if not entity:
        return {"error": f"Entity {entity_id} not found"}

    props = entity[0][0]

    # Connected neighbors via operators (2-hop: n ← operator → m)
    # ponytail: split into two directed queries — FalkorDB undirected
    # multi-rel-type patterns are unreliable across versions.
    neighbors_impl = proj.g.query(
        "MATCH (n:Point {id:$id})<-[r1:IMPL]-(op:Point {is_operator:true})"
        "-[r2:IMPL]->(m:Point) "
        "WHERE m.id <> n.id AND m.is_operator = false "
        "RETURN m.pointKind",
        params={"id": entity_id},
    ).result_set
    neighbors_nand = proj.g.query(
        "MATCH (n:Point {id:$id})<-[r1:NAND]-(op:Point {is_operator:true})"
        "-[r2:NAND]->(m:Point) "
        "WHERE m.id <> n.id AND m.is_operator = false "
        "RETURN m.pointKind",
        params={"id": entity_id},
    ).result_set
    neighbors = neighbors_impl + neighbors_nand

    neighbor_list = [
        {"pointKind": r[0]}
        for r in neighbors if r[0] is not None
    ]

    # Count by kind
    kind_counts: dict[str, int] = {}
    for n in neighbor_list:
        k = n["pointKind"] or "unknown"
        kind_counts[k] = kind_counts.get(k, 0) + 1

    return {
        "id": props.get("id"),
        "pointKind": props.get("pointKind"),
        "content": props.get("content", "")[:200],  # ponytail: preview, not full
        "neighbors": neighbor_list,
        "neighborCounts": kind_counts,
    }
