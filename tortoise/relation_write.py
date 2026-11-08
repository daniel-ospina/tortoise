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

ACCOUNTING: EVERY USABLE STATEMENT IS WRITTEN, DROPPED OR COLLAPSED
-------------------------------------------------------------------
``apply_relation_edges`` returns ``written``, ``dropped`` AND ``collapsed``,
and ``written + dropped + collapsed == considered`` over the statements it
accepted. That third bucket is not decoration: the DIRECT writer's edge
identity is ``(mechanism, src, dst)`` — the stated verb is deliberately NOT in
it, because ``create_direct_edge`` has one edge per mechanism/endpoint pair and
its ``label`` carries ONE meaning. So ``causes`` and ``supports`` stated over
the same pair are one ``IMPL`` edge, and the second meaning cannot ride a label
that already carries the first. The alternative — folding it into ``dropped`` —
would report a meaning that DID land as lost, and staying silent (what this
module used to do) leaves ``written + dropped`` short of the stated population
with nothing naming the gap. A collapse is NAMED and counted, and it is
reported through the caller's ``warn`` channel like a drop.

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

The plan half of that key is per writer, and the two are NOT the same key: the
structural writer dedups on ``(predicate, src, dst)`` (the statement's own
verb is in it) and the direct writer on ``(mechanism, src, dst)`` (it is not —
see ACCOUNTING above). A second capture of the same statement is a MERGE no-op
on the edge and a collapse in the plan, never a second edge.

RESIDUALS THIS LEG DOES NOT CLOSE (measured, not assumed)
---------------------------------------------------------
* ``references`` written structurally is a PLAIN Object→Object edge: its
  anchored writer (``link_source_to_entity``) is Source→Entity, so the leg
  cannot supply a ``sourceVersion`` here. The direction is faithful — only the
  census reading is affected (``derivation_edges.references`` counts anchored
  edges), and a stated ``references`` between two Objects is a real statement
  that must not be dropped, so it is written unanchored rather than refused.
* A registry read that RAISES is reported as ``None``/``()`` (see
  :func:`_mechanism_for` / :func:`_stated_verdict`), i.e. as "the registry
  declares nothing" rather than "the registry could not be read". Defensive
  only — every current caller is in-process — and recorded here rather than
  left to be rediscovered.
"""

from __future__ import annotations

import dataclasses

from .pack_registry import declared_mechanisms, relation_write_paths

__all__ = [
    "RELATION_DIRECT",
    "RELATION_REFUSED",
    "RELATION_STRUCTURAL",
    "RelationCollapse",
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

#: Predicates `link_entities` routes AWAY from `create_edge`, so the structural
#: arm cannot write them FAITHFULLY. `wasDerivedFrom` goes to
#: `create_derivation`, which stores `(dst)-[:wasDerivedFrom]->(src)`: the
#: stated direction inverted. Writing that is worse than refusing it, so the arm
#: refuses it BY NAME (measured unreachable from shipped packs today — `git grep
#: -n wasDerivedFrom -- packs/ config/` names no relations row — but the gate is
#: `predicate in _VALID_EDGE_PREDICATES`, which ADMITS it).
_ROUTED_AWAY = frozenset({"wasDerivedFrom"})


@dataclasses.dataclass(frozen=True)
class RelationEdge:
    """One edge the narrative (or its pack's declaration) licenses."""

    src_id: str
    dst_id: str
    predicate: str          # the predicate actually WRITTEN
    write_path: str         # RELATION_STRUCTURAL | RELATION_DIRECT
    declared_by: str | None  # the pack whose hierarchy fixed the predicate
    stated_predicate: str   # the story's own verb, kept for the record
    src_name: str = ""       # the stated endpoint NAMES — a refusal names them
    dst_name: str = ""
    mechanism: str | None = None  # the registry's mechanism, when one bound
    label: str | None = None      # the Layer-2 meaning, when it rode one


@dataclasses.dataclass(frozen=True)
class RelationCollapse:
    """One stated relation FOLDED onto an edge another statement already owns.

    Not a drop: the edge IS written, the second statement's MEANING is not (the
    direct writer's identity excludes the stated verb, so the label is taken).
    Counted and named so that ``written + dropped + collapsed`` reconciles with
    the stated population — see the module docstring's ACCOUNTING section.
    """

    src: str
    dst: str
    predicate: str   # the stated verb that produced no edge of its own
    into: str        # the predicate of the edge it folded onto
    reason: str


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


def _routed_away_reason(predicate: str) -> str:
    """Why this predicate may not be written structurally (``""`` when it may)."""
    if predicate not in _ROUTED_AWAY:
        return ""
    return (
        f"`{predicate}` is routed by `link_entities` to the derivation writer, "
        f"which stores the pair as (dst)-[:{predicate}]->(src) — the OPPOSITE "
        f"of the direction the story stated — so this leg refuses it rather "
        f"than commit an inverted edge")


def _collapse(src: str, dst: str, predicate: str, into: str) -> RelationCollapse:
    """The record for a statement folded onto an edge already planned."""
    if predicate == into:
        return RelationCollapse(
            src, dst, predicate, into,
            f"the same stated relation ({predicate!r}) appears twice in this "
            f"capture — the edge is written once and the second statement is "
            f"literally the same edge")
    return RelationCollapse(
        src, dst, predicate, into,
        f"the stated verb {predicate!r} folds onto the {into!r} edge this plan "
        f"already writes between the same two nodes — the DIRECT writer's "
        f"identity is (mechanism, src, dst), so a second meaning over that pair "
        f"is the SAME edge and its `label` cannot displace the first's "
        f"(#11237)")


def plan_relation_edges(
    relations,
    entity_index: dict,
    registry=None,
    *,
    declared=None,
) -> tuple[list[RelationEdge], list[RelationDrop], list[RelationCollapse]]:
    """``(edges, drops, collapses)`` for the narrative's relations — no writes.

    ``entity_index`` is ``{name: [(graph_id, kind), …]}``: every entity THIS
    capture wrote, keyed by the name the story used. ``registry``/``declared``
    are the ``pack_hierarchy`` seams (a test passes its own catalog).

    ``collapses`` are statements that produced NO edge of their own because an
    already-planned edge IS that edge (see the module docstring's ACCOUNTING) —
    neither written nor dropped, and counted so the three buckets reconcile.

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
    collapsed: list[RelationCollapse] = []
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
                    collapsed.append(_collapse(src, dst, predicate,
                                               edge.predicate))
                    continue
                reason = _routed_away_reason(edge.predicate)
                if reason:
                    drops.append(RelationDrop(src, dst, predicate, reason))
                    continue
                seen.add(key)
                edges.append(RelationEdge(
                    src_id=src_id, dst_id=dst_id, predicate=edge.predicate,
                    write_path=RELATION_STRUCTURAL, declared_by=edge.pack,
                    stated_predicate=predicate, src_name=src, dst_name=dst))
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
                collapsed.append(_collapse(src, dst, predicate, predicate))
                continue
            routed = _routed_away_reason(predicate)
            if routed:
                drops.append(RelationDrop(src, dst, predicate, routed))
                continue
            seen.add(key)
            edges.append(RelationEdge(
                src_id=src_id, dst_id=dst_id, predicate=predicate,
                write_path=RELATION_STRUCTURAL, declared_by=None,
                stated_predicate=predicate, src_name=src, dst_name=dst))
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
                collapsed.append(_collapse(src, dst, predicate, mechanism))
                continue
            seen.add(key)
            edges.append(RelationEdge(
                src_id=src_id, dst_id=dst_id, predicate=mechanism,
                write_path=RELATION_DIRECT, declared_by=None,
                stated_predicate=predicate, src_name=src, dst_name=dst,
                mechanism=mechanism, label=label))
            continue
        drops.append(RelationDrop(
            src, dst, predicate,
            f"`{predicate}` binds only the operator path "
            f"({paths}) and declares no mechanism this leg can write without "
            f"an operator — the endpoints are Objects, so no operator is "
            f"licensed (ONTOLOGY §3.9)"))
    return edges, drops, collapsed


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

    Returns ``{"edges": [...], "drops": [...], "collapses": [...],
    "written": n, "dropped": n, "collapsed": n, "considered": n,
    "write_paths": {...}}`` — the counts ride the capture receipt, the lists
    are the per-statement record. Every drop AND every collapse is reported
    through ``warn`` (the caller's warnings channel), so a stated relation that
    produced no edge of its own is never silent.

    ⛔ ``written + dropped + collapsed == considered``. The third bucket exists
    because the DIRECT writer's identity is ``(mechanism, src, dst)``: two
    stated verbs over one pair are ONE edge and the second meaning cannot ride
    a label the first already owns. It is neither "written" (its meaning did
    not land) nor "dropped" (the edge between that pair DID land), so it is
    counted apart and NAMED — see the module docstring's ACCOUNTING section.

    ⛔ A writer failure on ONE edge never aborts the rest: `#11237`'s channel is
    fail-open exactly as its producer is (`_assemble_relations` keeps what it
    validated and counts what it lost), so a single refusal is named and the
    next relation is still attempted.
    """
    plan, drops, collapses = plan_relation_edges(
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
            # ⛔ The record's shape is the SAME as every other drop's: the two
            # stated endpoint NAMES, then the stated verb. This used to put the
            # verb in `src` and leave `dst` empty, so a reader could not tell
            # which pair the writer refused (#11237 review).
            drops.append(RelationDrop(
                edge.src_name, edge.dst_name, edge.stated_predicate,
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
        for fold in collapses:
            warn(
                f"narrative relation folded (#11237): "
                f"{fold.predicate} {fold.src!r} → {fold.dst!r} "
                f"onto {fold.into!r} — {fold.reason}")
    return {
        "edges": written,
        "drops": [dataclasses.asdict(d) for d in drops],
        "collapses": [dataclasses.asdict(c) for c in collapses],
        "written": len(written),
        "dropped": len(drops),
        "collapsed": len(collapses),
        "considered": len(written) + len(drops) + len(collapses),
        "write_paths": by_path,
    }
