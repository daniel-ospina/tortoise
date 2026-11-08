"""The pack's DECLARED hierarchy — read, never inferred (#11001).

WHY THIS MODULE EXISTS
----------------------
The owner's direction (2026-11-04) is that the graph should be *"a map of the
business — goals → initiatives → actions/work items, each with its points and
events attached"*. Measured at the time: 5,314 Objects and **zero
Object→Object edges of any type**. The pack already declared the map —
``ontology.hierarchies`` (``Theme → Epic``, ``Epic → Issue``, …) — and
**nothing consumed it**: ``pack_registry._validate`` shape-checked the list,
then ``_load_one`` dropped it on the floor. A declaration that reaches no
producer is not a contract; it is a comment.

THE ONE RULE: READ THE DECLARATION, DO NOT INFER
------------------------------------------------
A *declared* relation is the pack's own claim about how its entities relate. A
producer that instead infers hierarchy — kind nesting, name similarity,
"``theme`` surely contains ``epic``", the transitive closure of the declared
paths — emits edges **nobody asserted**, and every downstream answer inherits
that structure. So:

  * a hierarchy is produced **iff** the pack wrote it in ``ontology.hierarchies``
    (or wrote the equivalent ``relations[]`` row — see the join below);
  * an undeclared pair produces **nothing**, including a direct hop past the
    declared levels (E2E-4 T8), even though a closure is one line away;
  * a declared entry that binds no write path is **named** in
    :func:`hierarchy_diagnostics` — never silently dropped, never written
    anyway.

TWO DECLARATIONS, ONE MEANING (the join, not a guess)
-----------------------------------------------------
``hierarchies: [{path: "Epic → Issue", type: hasPart}]`` and
``relations: [{predicate: decomposesInto, semantics: hasPart, fromKind:
dev:epic, toKind: dev:issue}]`` are ONE declaration spelled twice: the relation
spells the verb, the hierarchy names the levels. The join is therefore on the
DECLARED FIELDS only — ``(fromKind, toKind)`` must match, and ``type`` must be
the relation's ``predicate`` **or** its ``semantics``. Nothing is matched on a
substring, a stem, or a resemblance.

THE WRITE PATH IS CONSUMED, NOT RE-DERIVED
------------------------------------------
Which writer can land the edge is :func:`tortoise.pack_registry.relation_write_paths`'s
verdict — the module that declares itself *"the ONE place that decides which
declared paths apply"*. This module reads it; it does not re-decide.

RECORDED DECISION (this issue's scope, so it is not re-opened as an oversight):
**structural wins over operator when the declaration binds both.** Composition
is a plain, unweighted edge (ONTOLOGY §2), and ``hasPart`` is already in the
structural writer's own set, so no second producer is added. The operator path
is reported (``write_path == "operator"``) only for a declared ``type`` that the
structural writer refuses and a ``relations[]`` row binds by mechanism.

WHAT THIS MODULE DOES NOT DO (the named residual, on the issue)
---------------------------------------------------------------
It does not *pair instances*. An edge declares kinds; it does not say which
theme holds which epic — that association comes from the extraction channel
(the S5 Job 1 entity↔entity transport, sibling issue #10999), and inventing it
here (e.g. "every epic of kind X belongs to the first theme") is precisely the
invented edge this module exists to refuse. :func:`plan_hierarchy_edges` is the
seam that channel calls: it is given candidate instance pairs and keeps only
the declared ones.
"""

from __future__ import annotations

import dataclasses

from .pack_registry import relation_write_paths

__all__ = [
    "HIERARCHY_OPERATOR",
    "HIERARCHY_STRUCTURAL",
    "DeclaredHierarchy",
    "HierarchyEdge",
    "apply_hierarchy_edges",
    "declared_hierarchy",
    "hierarchy_diagnostics",
    "plan_hierarchy_edges",
]

#: The write path names, taken from ``relation_write_paths``' own vocabulary.
HIERARCHY_STRUCTURAL = "structural"
HIERARCHY_OPERATOR = "operator"

#: The separators a declared ``hierarchies[].path`` may use. The canonical
#: spelling is ``→`` (U+2192) — the one every shipped manifest uses — and
#: ``->`` is accepted because it is the same arrow typed on an ASCII keyboard,
#: not a different meaning. Anything else is a malformed declaration and is
#: reported, never split on a guess.
_PATH_SEPARATORS = ("\u2192", "->")


@dataclasses.dataclass(frozen=True)
class DeclaredHierarchy:
    """One ``ontology.hierarchies[]`` entry, resolved to a namespaced edge.

    Frozen and hashable so a PLAN is a set: two runs over the same declaration
    are the same object for equality, which is the producer's half of
    idempotence (the writer's half is its ``MERGE``).
    """
    pack: str           # the namespace that declared it
    path: str           # the entry's own `path` text, unchanged
    predicate: str      # the entry's `type` — the edge's predicate
    from_kind: str      # 'dev:theme'
    to_kind: str        # 'dev:epic'
    write_path: str     # HIERARCHY_STRUCTURAL | HIERARCHY_OPERATOR
    mechanism: str | None = None  # the declared mechanism, when the row binds one
    #: The pack's ``relations[]`` declares this same pack + pair + meaning, so the
    #: two entries are ONE declaration spelled twice (see ``_matching_row``).
    #: Solved here, by the function that owns the join, so a consumer that needs
    #: the DISTINCT-connection denominator does not re-derive it in prose.
    restates_relation: bool = False


@dataclasses.dataclass(frozen=True)
class HierarchyEdge:
    """One edge the declared hierarchy produces, for concrete instances."""
    predicate: str
    from_kind: str
    from_id: str
    to_kind: str
    to_id: str
    write_path: str
    pack: str


def _split_path(path: object) -> tuple[str, str] | None:
    """``'Theme → Epic'`` → ``('Theme', 'Epic')``; ``None`` when it is not a pair.

    Returns ``None`` for anything that is not exactly two non-empty sides —
    a three-level path (``Theme → Epic → Issue``) is NOT silently read as the
    first two names, because that would produce an edge the declaration does
    not state.
    """
    if not isinstance(path, str):
        return None
    for sep in _PATH_SEPARATORS:
        if sep in path:
            parts = [p.strip() for p in path.split(sep)]
            break
    else:
        return None
    if len(parts) != 2 or not all(parts):
        return None
    return parts[0], parts[1]


def _pack_kinds(pack) -> dict[str, str]:
    """``{lowercased kind name: 'ns:kind'}`` for one pack's own kinds.

    A declared path names KINDS, and a manifest spells them the way a human
    reads them (``Theme``, ``Epic``) while the vocabulary is lowerCamel
    (``theme``, ``epic``). The lookup is a case-insensitive match against the
    pack's own declared kind buckets — a name it does not declare is an
    unresolved declaration, not something to fuzzy-match elsewhere.
    """
    out: dict[str, str] = {}
    for bucket in ("object_kinds", "event_kinds", "document_kinds", "action_kinds",
                   "point_kinds"):
        for kind in getattr(pack, bucket, ()) or ():
            out.setdefault(str(kind).lower(), f"{pack.namespace}:{kind}")
    return out


def _resolve_kind(name: str, pack, kinds_by_name: dict[str, str],
                  registry) -> str | None:
    """Resolve one side of a declared path to a namespaced kind, or ``None``.

    A bare name is looked up in the DECLARING pack's own vocabulary. A
    ``ns:kind`` spelling is accepted only when that namespace actually declares
    that kind — ``relations[].fromKind`` gets this referential integrity from
    ``_validate`` and ``_validate_cross_pack_refs``; a ``hierarchies[].path``
    used to get none, so ``Theme → other:Nope`` resolved verbatim and was
    reported as a ✓ declared hierarchy over a kind no pack owns. That is the
    "declared and produced disagree silently" defect this module exists to
    remove, one level up.
    """
    if ":" not in name:
        return kinds_by_name.get(name.lower())
    ns, _, kind = name.partition(":")
    if not ns or not kind:
        return None
    decl = registry.packs.get(ns)
    if decl is None:
        return None
    return name if any(
        f"{ns}:{k}" == name
        for bucket in ("object_kinds", "event_kinds", "document_kinds",
                       "action_kinds", "point_kinds")
        for k in (getattr(decl, bucket, ()) or ())
    ) else None


def _matching_row(pack, predicate: str, from_kind: str, to_kind: str):
    """The ``relations[]`` row declaring this pair, or ``None``.

    Matched on the DECLARED fields only: the row must pair the same
    ``(fromKind, toKind)`` and spell the same meaning — ``predicate ==`` the
    hierarchy's ``type``, or ``semantics ==`` it (``decomposesInto`` with
    ``semantics: hasPart`` is one declaration written twice).
    """
    for rel in getattr(pack, "relations", ()) or ():
        if not isinstance(rel, dict):
            continue
        if rel.get("fromKind") != from_kind or rel.get("toKind") != to_kind:
            continue
        if rel.get("predicate") != predicate and rel.get("semantics") != predicate:
            continue
        return rel
    return None


def _binding(pack, predicate: str, from_kind: str, to_kind: str) -> tuple[str, str | None] | None:
    """``(write_path, mechanism)`` for a declared pair, or ``None`` if unbound.

    ⛔ **The verdict must be about the predicate this seam will actually WRITE,
    which is the hierarchy entry's ``type``** — not about whichever row happened
    to match. Two measured ways that distinction bites, both closed here:

    * ``relation_write_paths`` answers for the ROW's predicate. A row
      ``{predicate: hasPart, semantics: decomposesInto}`` is structural *for
      ``hasPart``*, but the edge this function would write carries
      ``decomposesInto``, which ``create_edge`` refuses — so accepting that
      verdict raised ``Unknown predicate`` mid-apply and left the graph
      half-hierarchied.
    * ``STRUCTURAL_WRITE_PREDICATES`` is the UNION of three writers
      (``create_edge``, ``create_about_edge``, ``ingest``). This seam writes
      through exactly one of them — ``link_entities`` → ``create_edge`` — so
      ``aboutPoint`` / ``extractedFrom`` read as ``structural`` there and are
      refused here, i.e. ``pack validate`` reported a declaration as writable
      that the writer it will use rejects.

    So the structural leg is read from the writer this seam calls
    (``_VALID_EDGE_PREDICATES``, ``create_edge``'s own set — the same constant
    ``sdk.py`` reads), and a row's verdict is only consulted for the OPERATOR
    leg, which is not a write this module performs.
    """
    from .projection.edges import _VALID_EDGE_PREDICATES

    row = _matching_row(pack, predicate, from_kind, to_kind)
    mechanism = row.get("mechanism") if row else None
    # The structural leg answers for the predicate we will WRITE (`type`), and
    # for a predicate `create_edge` accepts it needs no row at all: the hierarchy
    # entry is itself a declaration. A row matched by `semantics` is a SPELLING
    # variant of the same edge (`decomposesInto` / `semantics: hasPart`) and its
    # own verdict is never transplanted here — it contributes the mechanism, and
    # nothing else.
    if predicate in _VALID_EDGE_PREDICATES:
        return HIERARCHY_STRUCTURAL, mechanism
    if row is not None and HIERARCHY_OPERATOR in relation_write_paths(row):
        return HIERARCHY_OPERATOR, mechanism
    return None


def _registry_or_default(registry):
    if registry is not None:
        return registry
    from .domain_loader import _get_registry

    return _get_registry()


def _resolve(registry) -> tuple[tuple[DeclaredHierarchy, ...], tuple[dict, ...]]:
    """``(declared, diagnostics)`` — the one resolver both public verbs read."""
    reg = _registry_or_default(registry)
    declared: list[DeclaredHierarchy] = []
    diagnostics: list[dict] = []
    for ns in sorted(reg.packs):
        pack = reg.packs[ns]
        kinds_by_name = _pack_kinds(pack)
        for entry in getattr(pack, "hierarchies", ()) or ():
            raw_path = entry.get("path") if isinstance(entry, dict) else None
            predicate = entry.get("type") if isinstance(entry, dict) else None
            base = {"pack": ns, "path": raw_path, "type": predicate}
            sides = _split_path(raw_path)
            if sides is None:
                diagnostics.append({
                    **base,
                    "reason": (
                        f"the path {raw_path!r} is not a two-level "
                        f"'From → To' declaration; nothing is produced from it "
                        f"(a three-level path is refused rather than read as "
                        f"its first two names)"),
                })
                continue
            from_kind = _resolve_kind(sides[0], pack, kinds_by_name, reg)
            to_kind = _resolve_kind(sides[1], pack, kinds_by_name, reg)
            if from_kind is None or to_kind is None:
                missing = sides[0] if from_kind is None else sides[1]
                diagnostics.append({
                    **base,
                    "from_kind": from_kind,
                    "to_kind": to_kind,
                    "reason": (
                        f"the path names the kind {missing!r}, which pack "
                        f"{ns!r} does not declare — a hierarchy over a kind the "
                        f"pack does not own is not resolvable"),
                })
                continue
            if not isinstance(predicate, str) or not predicate:
                diagnostics.append({
                    **base, "from_kind": from_kind, "to_kind": to_kind,
                    "reason": "the entry declares no `type` — no predicate to write",
                })
                continue
            binding = _binding(pack, predicate, from_kind, to_kind)
            if binding is None:
                diagnostics.append({
                    **base, "from_kind": from_kind, "to_kind": to_kind,
                    "reason": (
                        f"`type: {predicate}` binds no declared write path for "
                        f"{from_kind} → {to_kind}: it is not in the structural "
                        f"writer's predicate set and no `relations[]` row for "
                        f"this pair binds a mechanism (the #2766 "
                        f"predicate-not-in-allowlist state). Nothing is "
                        f"produced, and nothing is inferred in its place"),
                })
                continue
            write_path, mechanism = binding
            declared.append(DeclaredHierarchy(
                pack=ns,
                path=str(raw_path),
                predicate=predicate,
                from_kind=from_kind,
                to_kind=to_kind,
                write_path=write_path,
                mechanism=mechanism,
                # ONE resolver for the join: `_matching_row` is the function
                # whose docstring calls the two shapes "one declaration spelled
                # twice", and a consumer that re-derived it (e.g. by comparing
                # rows itself) would be the second answer to one question this
                # module exists to remove.
                restates_relation=_matching_row(
                    pack, predicate, from_kind, to_kind) is not None,
            ))
    return tuple(declared), tuple(diagnostics)


def declared_hierarchy(registry=None) -> tuple[DeclaredHierarchy, ...]:
    """Every hierarchy the loaded packs DECLARE — and nothing else.

    The answer is a function of ``ontology.hierarchies`` (joined to
    ``relations[]`` on the declared ``(fromKind, toKind)`` + ``predicate|
    semantics``) alone. A pack that declares none contributes none; a pair no
    pack declares is absent no matter how plausible it looks.

    ``registry=None`` reads the process-wide default registry
    (``domain_loader._get_registry``), so a caller inside the runtime gets the
    installed catalog without threading one through.
    """
    return _resolve(registry)[0]


def hierarchy_diagnostics(registry=None) -> tuple[dict, ...]:
    """Every declared entry that produces NO edge, with the reason it does not.

    The counterweight to :func:`declared_hierarchy`: an entry that binds no
    write path, names a kind its pack does not declare, or is not a two-level
    path is NAMED here. Without this, "declared" and "produced" could disagree
    silently — which is the defect the whole module exists to remove.
    """
    return _resolve(registry)[1]


def plan_hierarchy_edges(
    candidates,
    registry=None,
    *,
    declared: tuple[DeclaredHierarchy, ...] | None = None,
) -> list[HierarchyEdge]:
    """Filter candidate instance pairs down to the DECLARED pairs.

    ``candidates`` are ``(from_kind, from_id, to_kind, to_id)`` tuples — the
    pairings an upstream channel observed (an extraction, a connector sync).
    This function's whole job is the ORACLE, not the pairing: a candidate whose
    kinds the pack does not declare is dropped, so an undeclared pair — a direct
    ``theme → epic`` hop, a transitive closure — emits **zero** edges.

    ⛔ **NO write-side cap, deliberately** — and this is a recorded decision, not
    an omission. ``tortoise/fanout.py`` states the owner's 2026-09-23 ruling
    (``STORAGE-ARCHITECTURE.md`` §11.5) verbatim: ``PER_ENTITY_FANOUT_CAP`` (200)
    "is **NOT a write-side rule.** Do not cap what gets **stored** … a cap that
    drops stored edges *risks losing a real connection, which is the product*".
    An earlier draft of this function clamped the plan to that number, which
    silently dropped 5 of 205 declared edges and made the plan a function of
    candidate ORDER rather than of the pair set. The cap is a query-side
    working-set bound; the hierarchy edges are what the queries walk, so capping
    them here would remove exactly what the cap exists to protect.

    Idempotent by construction: the result is a *set* keyed by
    ``(predicate, from_id, to_id)`` in discovery order, so a candidate list with
    duplicates yields one edge per declared pair, and running the planner twice
    over the same input yields an equal list. The WRITER's half is its ``MERGE``
    (``projection/edges.py::create_edge``), so re-applying a plan does not
    duplicate the edge. ⚠️ What re-application DOES repeat is the record carrier:
    ``create_edge`` appends one ``DirectEdgeCreated`` per call
    (``_record_structural_edges``), by design and for every structural edge in
    the repo — idempotence here is about the EDGE, never about the journal.
    """
    edges = declared if declared is not None else declared_hierarchy(registry)
    if not edges:
        return []
    seen: set[tuple[str, str, str]] = set()
    out: list[HierarchyEdge] = []
    for candidate in candidates:
        try:
            from_kind, from_id, to_kind, to_id = candidate
        except (TypeError, ValueError):
            continue
        if not from_id or not to_id:
            continue
        for edge in edges:
            if edge.from_kind != from_kind or edge.to_kind != to_kind:
                continue
            key = (edge.predicate, from_id, to_id)
            if key in seen:
                continue
            seen.add(key)
            out.append(HierarchyEdge(
                predicate=edge.predicate,
                from_kind=edge.from_kind,
                from_id=from_id,
                to_kind=edge.to_kind,
                to_id=to_id,
                write_path=edge.write_path,
                pack=edge.pack,
            ))
    return out


def apply_hierarchy_edges(
    sdk,
    candidates,
    registry=None,
    *,
    declared: tuple[DeclaredHierarchy, ...] | None = None,
) -> list[HierarchyEdge]:
    """Write the planned edges through the ALREADY-EXISTING linking verb.

    ⛔ No new writer: ``sdk.link_entities`` is the approved surface's one
    linking verb (``beta-sdk-surface.md`` row 19), and it routes ``hasPart`` to
    ``create_edge`` — the structural, ``MERGE``-based writer. Adding a second
    verb for composition is what this line refuses to do.

    ⚠️ Only the ``structural`` path is written here. A declared ``operator``
    pair is reported by :func:`declared_hierarchy` for the caller that owns the
    operator question; minting an operator Point implicitly (on a *structural*
    hierarchy declaration) would be exactly the composition-as-argument the
    issue rules out.

    ⚠️ NAMED RESIDUAL: the endpoints' ACTUAL kinds are the caller's claim. This
    function filters on the candidate tuple's ``(from_kind, to_kind)``; neither
    it nor ``create_edge`` re-reads the nodes' ``objectKind`` (``create_edge``
    resolves an endpoint by ``id | eventId | url`` across every label). So a
    candidate that mislabels an Issue as ``dev:epic`` writes a pair no pack
    declares. Closing that needs a kind read per endpoint, which is the caller's
    half of the contract — the same boundary as the instance pairing above.
    """
    plan = plan_hierarchy_edges(candidates, registry, declared=declared)
    written: list[HierarchyEdge] = []
    for edge in plan:
        if edge.write_path != HIERARCHY_STRUCTURAL:
            continue
        sdk.link_entities(edge.predicate, edge.from_id, edge.to_id)
        written.append(edge)
    return written
