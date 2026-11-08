"""S5 Job 1 remainder (#11237) — the narrative relation WRITE path.

WHY THIS MODULE EXISTS
----------------------
`#10999` built the `relations` channel as **transport only**: `execute_embed`
validates the narrative's entity↔entity assertions into
``result["relations"]`` (``extractor_v2._assemble_relations``) and **nothing
consumed it**. A story that states *"the retry storm causes the deadline
slip"* produced no edge between the two Objects, so the graph held the two
entities and neither of the claims the story made about them.

This module is that consumer. It is deliberately the LAST step of the write
path, after the entities exist: a relation names two entity **names**, and an
edge needs two **graph ids**.

THE ONE RULE: THE REGISTRY DECIDES WHICH WRITE PATH APPLIES
-----------------------------------------------------------
``pack_registry.relation_write_paths`` states of itself that it is *"the ONE
place that decides which declared paths apply"*. This module **consumes** that
verdict — it never re-derives a writability table of its own:

  * the MECHANISM a relation's verb declares comes from
    ``pack_registry.declared_mechanisms`` (the pack row for the predicate, else
    ONTOLOGY §8's ``SEMANTICS_MECHANISMS`` for its meaning);
  * the PATHS come from ``relation_write_paths({"predicate": …, "mechanism":
    …})``;
  * an empty verdict is a REFUSAL, and it is NAMED — never a silent drop and
    never an edge written anyway.

Two things are decided here, and neither is a re-derivation of the registry's
answer:

  1. **Which WRITER the licensed path reaches.** ``structural`` is written
     through ``sdk.link_entities`` (the approved surface's one linking verb,
     ``beta-sdk-surface.md`` row 19), which routes a structural predicate to
     ``create_edge`` — a plain, ``MERGE``-based, record-carried edge
     (``RECORD_CARRIED_STRUCTURAL_RELS``). ``operator`` cannot be honoured
     here, because these endpoints are **Objects, not Points**: ONTOLOGY §3.9
     licenses a carrying operator *iff* there is a mitigation or the edge is
     Point↔Point support/contradict, and the operator writer resolves
     ``:Point`` endpoints only. So a relation the registry binds by mechanism
     is written as the **operator-less half** of that mechanism —
     ``create_direct_edge`` with the meaning riding ``label`` — which is the
     ontology's own plain-edge shape (v3.5 §8 / plan §5.3) and the same
     record-carried family (``DirectEdgeCreated``). It is NOT a second operator
     producer, and it is NOT a structural predicate invented on top of a
     meaning.
  2. **Which declared HIERARCHY the stated pair belongs to.** When a loaded
     pack declares a hierarchy over the pair's two KINDS,
     ``pack_hierarchy.plan_hierarchy_edges`` is the oracle: the declared
     predicate is written instead of the story's verb, because the declaration
     is the pack's own statement about how those levels relate. This is the
     seam ``#11001`` documented but never called (measured: no write-path call
     site existed), and it is fed **only** by the pairings the narrative
     actually stated — never by a kind cross-product, which is the invented
     edge ``pack_hierarchy`` exists to refuse.

IDENTITY IS ``(name, kind)`` AND AN AMBIGUOUS ENDPOINT IS NEVER GUESSED THROUGH
-------------------------------------------------------------------------------
``#11235``: an endpoint names a **name**, and Layer-1 identity is
``(name, kind)``. When this capture wrote two DISTINCT nodes under one name
(same name, different kinds — an Object and a Subject), the name denotes both
and neither is chosen: the relation is dropped and the reason names the
collision. ``extractor_v2`` already warns that the endpoint *is* ambiguous;
this is the half that declines to settle it.

⛔ #10000 (P0) — WHAT THIS PATH MAY CARRY
-----------------------------------------
The write consumes the **typed relation** only: two ids, a verb, and (when the
model stated one) a Layer-2 meaning from the CLOSED vocabulary. It never
touches the story's text, ``summary``, ``_searchText`` or the transcript — the
relation record it is handed was already stripped to those fields by
``_assemble_relations``, and nothing here adds a property to the edge. A write
path that re-introduced narrative prose into FalkorDB is the regression
``#10000`` exists to prevent.

IDEMPOTENCE
-----------
Both writers are ``MERGE``-based (``create_edge``'s own docstring states the
edge is the merge target; ``create_direct_edge`` is a bare-pattern MERGE with
attribute SET), so re-finalising the same batch writes the same EDGE rather
than a duplicate. ⚠️ The JOURNAL is not idempotent and is not meant to be: each
write appends one record descriptor, exactly as every other structural write in
the repo does — idempotence here is about the edge.
"""

from __future__ import annotations

import dataclasses

from .pack_registry import declared_mechanisms, relation_write_paths

__all__ = [
    "RELATION_DIRECT",
    "RELATION_REFUSED",
    "RELATION_STRUCTURAL",
    "RelationDrop",
    "RelationEdge",
    "apply_relation_edges",
    "plan_relation_edges",
]

#: The write paths this leg can reach, in the vocabulary of
#: ``pack_hierarchy``'s names (``structural`` is the registry's own word).
RELATION_STRUCTURAL = "structural"
#: The operator path's operator-LESS half — a bare IMPL/NAND edge, used where
#: the endpoints are not Points (see the module docstring, point 1).
RELATION_DIRECT = "direct"
#: The registry bound this relation to no path at all.
RELATION_REFUSED = "refused"

#: The two mechanisms an edge can carry without an operator.
_DIRECT_MECHANISMS = ("IMPL", "NAND")


@dataclasses.dataclass(frozen=True)
class RelationEdge:
    """One edge the narrative (or its pack's declaration) licenses."""

    src_id: str
    dst_id: str
    predicate: str          # the predicate actually WRITTEN
    write_path: str         # RELATION_STRUCTURAL | RELATION_DIRECT
    declared_by: str | None  # the pack whose hierarchy fixed the predicate
    stated_predicate: str   # the story's own verb, kept for the record
    mechanism: str | None = None  # the registry's mechanism, when one bound
    label: str | None = None      # the Layer-2 meaning, when it rode one


@dataclasses.dataclass(frozen=True)
class RelationDrop:
    """One stated relation this leg did NOT write, and why."""

    src: str
    dst: str
    predicate: str
    reason: str


def _entity_kind(entity_index: dict, name: str) -> tuple[str, str | None] | None:
    """``(id, kind)`` for one endpoint name, or ``None`` with the reason set.

    Returns ``None`` for an absent name, and the caller distinguishes
    "unresolved" from "ambiguous" through :func:`_endpoint_reason` — the two
    need different sentences, and collapsing them would report a name collision
    as a missing endpoint.
    """
    holders = entity_index.get(name) or ()
    ids = {hid for hid, _kind in holders if hid}
    if len(ids) != 1:
        return None
    hid = next(iter(ids))
    kind = next((k for i, k in holders if i == hid and k), None)
    return hid, kind


def _endpoint_reason(entity_index: dict, name: str) -> str | None:
    """Why this endpoint name cannot become an edge end (``None`` when it can)."""
    holders = entity_index.get(name) or ()
    ids = {hid for hid, _kind in holders if hid}
    if not ids:
        return (
            f"the endpoint {name!r} names no entity this capture wrote — an "
            f"edge to nothing is not written (#11237)")
    if len(ids) > 1:
        kinds = sorted({str(k or "unknown") for _i, k in holders})
        return (
            f"the endpoint {name!r} names {len(ids)} DIFFERENT entities of "
            f"kinds {kinds} — Layer-1 identity is (name, kind) (#11235), so the "
            f"name does not name ONE node and the edge is not guessed through")
    return None


def _mechanism_for(predicate: str, semantics: str | None) -> str | None:
    """The mechanism this verb/semantics DECLARES, from the registry (never here).

    A verb that IS a mechanism (``IMPL``/``NAND``) declares itself. Otherwise
    ``pack_registry.declared_mechanisms`` answers — one lookup over the pack
    rows and ONTOLOGY §8's map, so a second table here would be exactly the
    re-derivation the issue forbids. Deterministic: the first declaration the
    registry returns, in its own order.
    """
    if predicate in _DIRECT_MECHANISMS:
        return predicate
    try:
        found = declared_mechanisms(label=predicate, semantics=semantics)
    except Exception:  # noqa: BLE001, RUF100 — a registry read must never abort a write
        return None
    for mechanism, _source in found or ():
        if mechanism in _DIRECT_MECHANISMS:
            return mechanism
    return None


def _stated_verdict(predicate: str, mechanism: str | None) -> tuple[str, ...]:
    """The registry's declared write paths for this verb, consumed verbatim."""
    try:
        return tuple(relation_write_paths(
            {"predicate": predicate, "mechanism": mechanism}) or ())
    except Exception:  # noqa: BLE001, RUF100 — same rule: never abort on a read
        return ()


def plan_relation_edges(
    relations,
    entity_index: dict,
    registry=None,
    *,
    declared=None,
) -> tuple[list[RelationEdge], list[RelationDrop]]:
    """``(edges, drops)`` for the narrative's relations — the plan, no writes.

    ``entity_index`` is ``{name: [(graph_id, kind), …]}``: every entity THIS
    capture wrote, keyed by the name the story used. ``registry``/``declared``
    are the ``pack_hierarchy`` seams (a test passes its own catalog).

    Pure: same inputs → same plan, so the plan is comparable across runs (the
    producer's half of idempotence; the writers' ``MERGE`` is the other half).
    """
    from .pack_hierarchy import (
        HIERARCHY_STRUCTURAL,
        declared_hierarchy,
        plan_hierarchy_edges,
    )
    from .projection.edges import _VALID_EDGE_PREDICATES

    declared_edges = (declared if declared is not None
                      else declared_hierarchy(registry))
    edges: list[RelationEdge] = []
    drops: list[RelationDrop] = []
    seen: set[tuple[str, str, str]] = set()
    for rel in relations or ():
        if not isinstance(rel, dict):
            continue
        src = str(rel.get("src") or "").strip()
        dst = str(rel.get("dst") or "").strip()
        predicate = str(rel.get("predicate") or "").strip()
        if not src or not dst or not predicate:
            continue
        semantics = rel.get("semantics")
        semantics = semantics if isinstance(semantics, str) else None
        reason = _endpoint_reason(entity_index, src) \
            or _endpoint_reason(entity_index, dst)
        if reason is not None:
            drops.append(RelationDrop(src, dst, predicate, reason))
            continue
        src_id, src_kind = _entity_kind(entity_index, src)
        dst_id, dst_kind = _entity_kind(entity_index, dst)

        # ── 1. a DECLARED hierarchy over the stated pair wins ──────────────
        planned = plan_hierarchy_edges(
            [(src_kind, src_id, dst_kind, dst_id)], registry,
            declared=declared_edges) if declared_edges else []
        if planned:
            for edge in planned:
                if edge.write_path != HIERARCHY_STRUCTURAL:
                    drops.append(RelationDrop(
                        src, dst, predicate,
                        f"the pack {edge.pack!r} declares "
                        f"{edge.from_kind} → {edge.to_kind} on the OPERATOR "
                        f"path ({edge.predicate}) — this leg writes Object "
                        f"endpoints and does not mint a carrying operator"))
                    continue
                key = (edge.predicate, src_id, dst_id)
                if key in seen:
                    continue
                seen.add(key)
                edges.append(RelationEdge(
                    src_id=src_id, dst_id=dst_id, predicate=edge.predicate,
                    write_path=RELATION_STRUCTURAL, declared_by=edge.pack,
                    stated_predicate=predicate))
            continue

        # ── 2. the story's own verb, through the registry's verdict ────────
        mechanism = _mechanism_for(predicate, semantics)
        paths = _stated_verdict(predicate, mechanism)
        if not paths:
            drops.append(RelationDrop(
                src, dst, predicate,
                f"`{predicate}` binds no declared write path "
                f"(relation_write_paths() -> ()) — it is neither in the "
                f"structural writer's predicate set nor bound to a mechanism, "
                f"so nothing is written and nothing is inferred in its place"))
            continue
        if RELATION_STRUCTURAL in paths and predicate in _VALID_EDGE_PREDICATES:
            key = (predicate, src_id, dst_id)
            if key in seen:
                continue
            seen.add(key)
            edges.append(RelationEdge(
                src_id=src_id, dst_id=dst_id, predicate=predicate,
                write_path=RELATION_STRUCTURAL, declared_by=None,
                stated_predicate=predicate))
            continue
        if mechanism in _DIRECT_MECHANISMS:
            # The operator-less half of the declared mechanism. The MEANING
            # rides `label` so the two layers stay separate (#7852): the edge
            # type is the mechanism, the label is what the story meant.
            label = predicate if predicate not in _DIRECT_MECHANISMS else None
            if label is None and semantics and semantics != mechanism:
                label = semantics
            key = (mechanism, src_id, dst_id)
            if key in seen:
                continue
            seen.add(key)
            edges.append(RelationEdge(
                src_id=src_id, dst_id=dst_id, predicate=mechanism,
                write_path=RELATION_DIRECT, declared_by=None,
                stated_predicate=predicate, mechanism=mechanism,
                label=label))
            continue
        drops.append(RelationDrop(
            src, dst, predicate,
            f"`{predicate}` binds only the operator path "
            f"({paths}) and declares no mechanism this leg can write without "
            f"an operator — the endpoints are Objects, so no operator is "
            f"licensed (ONTOLOGY §3.9)"))
    return edges, drops


def apply_relation_edges(
    sdk,
    relations,
    entity_index: dict,
    registry=None,
    *,
    declared=None,
    warn=None,
) -> dict:
    """Write the planned edges through the ONE linking verb.

    Returns ``{"edges": [...], "drops": [...], "written": n, "dropped": n,
    "write_paths": {...}}`` — the counts ride the capture receipt, the lists
    are the per-relation record. Every drop is reported through ``warn`` (the
    caller's warnings channel), so a stated relation that produced no edge is
    never silent.

    ⛔ A writer failure on ONE edge never aborts the rest: `#11237`'s channel is
    fail-open exactly as its producer is (`_assemble_relations` keeps what it
    validated and counts what it lost), so a single refusal is named and the
    next relation is still attempted.
    """
    plan, drops = plan_relation_edges(
        relations, entity_index, registry, declared=declared)
    written: list[dict] = []
    for edge in plan:
        try:
            if edge.write_path == RELATION_DIRECT:
                sdk.link_entities(edge.predicate, edge.src_id, edge.dst_id,
                                  label=edge.label)
            else:
                sdk.link_entities(edge.predicate, edge.src_id, edge.dst_id)
        except Exception as exc:  # noqa: BLE001, RUF100 — fail-open per relation
            drops.append(RelationDrop(
                edge.stated_predicate, "", edge.predicate,
                f"the writer refused {edge.predicate!r} ({type(exc).__name__}: "
                f"{exc}) — the relation stays uncommitted"))
            continue
        written.append({
            "src_id": edge.src_id,
            "dst_id": edge.dst_id,
            "predicate": edge.predicate,
            "write_path": edge.write_path,
            "stated_predicate": edge.stated_predicate,
            "declared_by": edge.declared_by,
            "mechanism": edge.mechanism,
            "label": edge.label,
        })
    by_path: dict[str, int] = {}
    for row in written:
        by_path[row["write_path"]] = by_path.get(row["write_path"], 0) + 1
    if warn is not None:
        for drop in drops:
            warn(
                f"narrative relation not written (#11237): "
                f"{drop.predicate} {drop.src!r} → {drop.dst!r} — {drop.reason}")
    return {
        "edges": written,
        "drops": [dataclasses.asdict(d) for d in drops],
        "written": len(written),
        "dropped": len(drops),
        "write_paths": by_path,
    }
