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
  * the DIRECTION a stated meaning records comes from
    ``pack_registry.SEMANTICS_DIRECTIONS`` (``causes``/``supports`` → ``->``,
    ``mutuallySupportive`` → ``<->`` …), the SAME one map ONTOLOGY §8 rules;
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

The plan half of that key is per writer, and the two are NOT the same key: the
structural writer dedups on ``(predicate, src, dst)`` and the direct writer on
``(mechanism, src, dst)`` — the stated verb is deliberately not in the second,
which is why a second meaning over one pair is a recorded FOLD (see
``RelationEdge.folded``) rather than a second edge.

DIRECTION IS CONSUMED, NOT DEFAULTED
------------------------------------
``create_direct_edge``'s CYCLE-25 default is ``direction=None →
"bidirectional"`` for IMPL, so a one-way statement (*"the retry storm causes
the deadline slip"*) would be stored as belief flowing BOTH ways unless the leg
supplies the direction the statement already declares. It does:
:func:`_direction_for` reads ``pack_registry.SEMANTICS_DIRECTIONS`` — ONTOLOGY
§8's RULED table (``causes``/``supports`` → ``->``, ``mutuallySupportive`` →
``<->``, ``related`` → ``-``) — from the ROW first and then from the MEANING in
the order it is declared: the pack relation row's own resolved ``direction:``
(``pack_registry.declared_directions`` → ``effective_direction``, the ONE
per-row resolver ``tools/pack_relation_census.py`` asks — a DIFFERENTIATING
declared token outranks the meaning, so ``blocks``/``holds`` (both
``direction: "<-"``) are written ``<-`` and not their meanings' ``->``), then
the row's own ``semantics:`` (``declared_meanings``, the same row that supplies
the mechanism and canonicalised through ``SEMANTICS_TRANSITION``), then the
meaning riding the edge (``label``, which is the story's verb), then the verb
itself, then the record's ``semantics`` gloss, and only for a meaning that
declares NO direction does it fall back to ``_DIRECTION_STATED`` (the model's
one-way token: a stated relation runs ``src → dst``). The row comes first —
its ``direction:`` AND its meaning, in that order — because it is the
DECLARATION that licensed the write — the authority ``_mechanism_for`` already
consults for the mechanism half of the same triple (#7852) — and because
keying on the model's optional gloss made the stored direction
non-deterministic: the same pack verb landed ``-`` when the model restated the
gloss and ``->`` when it did not. ⛔ Reading the meaning but not the row's own
token is the inversion this paragraph used to describe: it wrote ``->`` on the
two shipped rows that declare ``<-`` and left the writer disagreeing with the
census about a row (#11548 review, P1). The value rides
``link_entities(direction=…)`` onto the direct arm, and the written row records
it, so a reader can tell a one-way stated claim from a writer default without
re-deriving it. ``live.direction_flows`` reads it back unchanged.

⚠️ The STRUCTURAL arm (``create_edge``) has no direction slot at all —
``link_entities`` REFUSES ``direction`` on that arm (#4282) rather than
dropping it — so a structural Object→Object edge carries no direction property
and ``direction`` is ``None`` on those rows. That is the writer's contract, not
a default chosen here: no EP belief flows along a structural edge.

ACCOUNTING
----------
``written + dropped + folded`` reconciles with the stated population, on EVERY
arm — including the four where it did not (#11548 round 3 P2 + review F3):

* a refused edge that carried a fold reports one drop per LOST MEANING (never a
  bare "1 drop" for two statements);
* a second, DIFFERENT stated verb over a declared hierarchy pair is a recorded
  fold on the declared edge rather than a silent ``continue``;
* ONE statement yields at most ONE accounted outcome. A pack that declares a
  kind pair on BOTH legs used to land the structural edge AND record a drop for
  the operator one — the same statement reported written and lost, ``2`` for a
  population of ``1``. The operator declaration is a fact about the PACK
  (``pack_hierarchy.hierarchy_diagnostics`` is its named home), so it is
  reported only when nothing for that statement landed;
* a literal REPEAT of the same statement counts ONCE on every arm. The landing
  arms already deduped it (one edge, no meaning lost —
  ``extractor_v2._assemble_relations`` dedups it upstream as well); the drop
  arms used to append per repetition and report ``dropped=2`` for a population
  the landing arms call ``1``. :func:`_drop_once` applies the same identity to
  them.

RESIDUALS THIS LEG DOES NOT CLOSE (measured, not assumed)
---------------------------------------------------------
* A pack that declares TWO STRUCTURAL hierarchies over the SAME kind pair
  (``pack_hierarchy._resolve`` does not dedup on the kind pair, so a pack edit
  reaches this) plans two edges for one statement: both are written — the
  declaration licenses both — so ``written`` reads ``2`` against a population
  of ``1``. Not a loss, and not silent (both declared predicates are the pack's
  own record), and unreachable from the shipped catalog today (measured: 6
  declarations, 0 duplicate kind pairs). Named here rather than guessed at.
* ``references`` written structurally is a PLAIN Object→Object edge: its
  anchored writer (``link_source_to_entity``) is Source→Entity, so this leg
  cannot supply a ``sourceVersion`` here. The direction is faithful — only the
  census reading is affected (``derivation_edges.references`` counts anchored
  edges) — and a stated ``references`` between two Objects is a real statement
  that must not be dropped, so it is written unanchored rather than refused.
* A registry read that RAISES is reported as ``None``/``()`` (see
  :func:`_mechanism_for` / :func:`_stated_verdict`), i.e. as "the registry
  declares nothing" rather than "the registry could not be read". Defensive
  only — every current caller is in-process — and recorded here rather than
  left to be rediscovered.
"""

from __future__ import annotations

import dataclasses

from .pack_registry import (
    declared_directions,
    declared_meanings,
    declared_mechanisms,
    relation_write_paths,
)

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

#: ⛔ THE DIRECTION FALLBACK FOR A STATED RELATION WHOSE MEANING DECLARES NONE.
#: ``create_direct_edge`` defaults a direction ABSENT to ``bidirectional`` for
#: IMPL (CYCLE-25), so an IMPL edge written without one propagates belief back
#: along a claim the story made one way. A stated relation runs ``src → dst``,
#: so the fallback is the model's own one-way token (``unidirectional``'s
#: canonical form) — but the fallback is only the FALLBACK: every meaning in
#: ONTOLOGY §8 has a RULED direction (``pack_registry.SEMANTICS_DIRECTIONS``,
#: #7852), and writing this constant for a ``mutuallySupportive`` /
#: ``mutuallyExclusive`` edge would record a one-way flow where the ruling says
#: both ways — a regression against the writer's own default (#11548 review
#: round 4, P1; round 8: the pack row's declared meaning was never a candidate
#: either, so ``partOf``/``contains`` (``hasPart``) and
#: ``protects``/``marks``/``funds`` (``related``/``transacts``) wrote ``->``).
#: ``_direction_for`` consults the table — over the DECLARED meaning first —
#: before it ever reaches this token.
_DIRECTION_STATED = "->"

#: Predicates `link_entities` routes AWAY from `create_edge`, so the structural
#: arm cannot write them FAITHFULLY. `wasDerivedFrom` goes to
#: `create_derivation`, which stores `(dst)-[:wasDerivedFrom]->(src)`: the
#: stated direction INVERTED. Writing that is worse than refusing it, so the arm
#: refuses it BY NAME (measured unreachable from shipped packs today — `git grep
#: -n wasDerivedFrom -- packs/ config/` names no relations row — but the arm's
#: gate is `predicate in _VALID_EDGE_PREDICATES`, which ADMITS it).
_ROUTED_AWAY = frozenset({"wasDerivedFrom"})


def _routed_away_reason(predicate: str) -> str:
    """Why this predicate may not be written structurally (``""`` when it may)."""
    if predicate not in _ROUTED_AWAY:
        return ""
    return (
        f"`{predicate}` is routed by `link_entities` to the derivation writer, "
        f"which stores the pair as (dst)-[:{predicate}]->(src) — the OPPOSITE "
        f"of the direction the story stated — so this leg refuses it rather "
        f"than commit an inverted edge (#11237 residual)")


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
    #: The endpoint NAMES the story used — carried so a refusal can name the
    #: pair it was about (``RelationDrop`` speaks names, not ids).
    src_name: str = ""
    dst_name: str = ""
    #: Further stated verbs this ONE edge absorbed: ``create_direct_edge`` is a
    #: bare-pattern MERGE ("exactly one edge per (src,tgt,type)"), so two
    #: meanings binding the same mechanism between the same pair cannot be two
    #: edges. They are folded here — and NAMED — instead of vanishing.
    folded: tuple[str, ...] = ()
    #: The direction the edge is WRITTEN with, on the arms whose writer takes
    #: one (``direct``). ``None`` means the writer's default — which for
    #: ``create_edge`` (structural) is the only option: it records no direction
    #: property at all.
    direction: str | None = None


@dataclasses.dataclass(frozen=True)
class RelationDrop:
    """One stated relation this leg did NOT write, and why."""

    src: str
    dst: str
    predicate: str
    reason: str


def _entity_kind(entity_index: dict, name: str) -> tuple[str, str | None]:
    """``(id, kind)`` for an endpoint name — the CALLER'S PRECONDITION holds.

    ⛔ ``name`` is guaranteed to resolve to exactly ONE id, because every caller
    runs :func:`_endpoint_reason` first and that function is where both
    refusals live (unresolved, and the ``#11235`` name collision). The
    ``if len(ids) != 1: return None`` guard that used to sit here could never
    fire — ``_endpoint_reason`` had already returned ``None`` for every failing
    input — so it was dead code that LOOKED like the collision refusal, and
    ``test_ambiguous_endpoint_is_refused_not_guessed`` named exactly it as its
    mutation. Executed: that mutant leaves every pure-plan case GREEN, because
    ``_endpoint_reason``'s ``len(ids) > 1`` arm masked it. A named mutant that
    reddens nothing is worse than none — it certifies cover the row does not
    have (agent-infra#3442 rule 3; #11548 review F2). The ambiguity decision is
    therefore HERE-absent by design, and a caller that skipped the check fails
    loudly (``StopIteration``) instead of writing an edge to a guessed node.
    """
    holders = entity_index.get(name) or ()
    hid = next(hid for hid, _kind in holders if hid)
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


def _drop_once(
    drops: list[RelationDrop],
    seen: set[tuple[str, str, str, str | None]],
    *,
    src: str,
    dst: str,
    predicate: str,
    semantics: str | None,
    reason: str,
) -> None:
    """Record ``reason`` as ONE drop for this stated STATEMENT — at most once.

    ⛔ The landing arms dedup a literal repeat of the same statement (it is one
    edge and no meaning was lost), so the drop arms must dedup it the same way
    or the accounting breaks in the other direction: two identical un-writable
    statements reported ``dropped=2`` for a population the landing arms count
    as **1** (`transacts ×2` → ``written 0, dropped 2, folded 0`` against a
    stated population of 1 — #11548 review F3(b)).

    The key is the statement's own identity — ``(src, dst, predicate,
    semantics)`` — because the ``semantics`` gloss can decide which mechanism
    BINDS (``_mechanism_for`` reads it), so two statements that read the same
    but carry different glosses are genuinely two statements.
    """
    key = (src, dst, predicate, semantics)
    if key in seen:
        return
    seen.add(key)
    drops.append(RelationDrop(src, dst, predicate, reason))


def _registry_seam(registry: object) -> dict:
    """``{"registry": …}`` when the caller NAMED a catalog, else ``{}``.

    ⛔ The two seams' ``None`` conventions are OPPOSITE, and passing one through
    to the other silently disables the pack half of the declaration:
    ``plan_relation_edges(registry=None)`` means "read the process-wide catalog"
    (``pack_hierarchy``'s convention), while
    ``declared_mechanisms(registry=None)`` means "already known ABSENT" — its
    third state is :data:`pack_registry._UNSET`, spelled by OMITTING the kwarg.
    So the plan's ``None`` becomes "no kwarg" here, never ``registry=None``.
    """
    return {} if registry is None else {"registry": registry}


def _declared_mechanism_for(predicate: str, semantics: str | None,
                            registry: object = None) -> str | None:
    """The mechanism this verb/semantics declares FIRST, carriable or not.

    ``pack_registry.declared_mechanisms`` returns the declarations in authority
    order — the pack row named by ``predicate`` first (its own ``mechanism`` is
    AUTHORITATIVE for that verb, up to and including the case where it differs
    from what its ``semantics`` maps to), then the ONTOLOGY §8 meaning legs.
    This returns that FIRST mechanism whatever it is, so a caller can tell
    "declares nothing" apart from "declares something the bare-edge leg cannot
    carry yet".
    """
    if predicate in _DIRECT_MECHANISMS:
        return predicate
    try:
        found = declared_mechanisms(label=predicate, semantics=semantics,
                                    **_registry_seam(registry))
    except Exception:  # noqa: BLE001, RUF100 — a registry read must never abort a write
        return None
    for mechanism, _source in found or ():
        return mechanism
    return None


def _mechanism_for(predicate: str, semantics: str | None,
                   registry: object = None) -> str | None:
    """The mechanism this verb/semantics DECLARES **and this leg can carry**.

    A verb that IS a mechanism (``IMPL``/``NAND``) declares itself. Otherwise
    ``pack_registry.declared_mechanisms`` answers — one lookup over the pack
    rows and ONTOLOGY §8's map, so a second table here would be exactly the
    re-derivation the issue forbids. Deterministic: the first declaration the
    registry returns, in its own order.

    ⛔ THE FIRST DECLARATION IS THE ONLY CANDIDATE (#12067 review). This used to
    scan the whole list for the first CARRRIABLE mechanism, which meant a pack
    row declaring ``mechanism: coherence`` — the authoritative declaration for
    its predicate — was silently replaced by whatever its ``semantics`` mapped
    to, i.e. ``IMPL``. Measured on a pack row `{predicate: gatedBy, mechanism:
    coherence, semantics: dependsOn}`: the plan produced a direct **``IMPL``**
    edge with no drop and no warning, so a package's stated NECESSITY relation
    was priced as SUPPORT — the silent inheritance of another mechanism's arm
    that #12067's closed set exists to make impossible. A fallback to a
    DIFFERENT mechanism is not a fallback, it is a substitution, so it is
    refused here and the relation is DROPPED with a reason that names the real
    cause (``_uncarriable_reason``).

    ``registry`` is the SAME catalog ``plan_relation_edges`` was given (see
    :func:`_registry_seam`), so the leg reads ONE catalog — the mechanism and
    the hierarchy cannot answer from different ones.
    """
    declared = _declared_mechanism_for(predicate, semantics, registry)
    if declared is None:
        return None
    return declared if declared in _DIRECT_MECHANISMS else None


def _uncarriable_reason(predicate: str, semantics: str | None,
                        registry: object = None) -> str | None:
    """The drop reason for a verb whose OWN declaration this leg cannot carry.

    ``None`` when the declaration is carriable (or absent) — the caller then
    falls back to its generic reason. Exists so the silent-drop reason names the
    real cause: the old text said the verb "is neither in the structural
    writer's predicate set nor bound to a mechanism", which is FALSE for a row
    that declares ``coherence`` — it IS bound to a mechanism, just not to one a
    bare edge can carry.
    """
    declared = _declared_mechanism_for(predicate, semantics, registry)
    if declared is None or declared in _DIRECT_MECHANISMS:
        return None
    return (
        f"`{predicate}` declares mechanism {declared!r}, which the "
        f"operator-less direct-edge leg cannot carry (#12067): a bare edge's "
        f"mechanism IS its rel type and there are two (IMPL, NAND), so this "
        f"relation is NOT written. It is NOT silently re-spelled as IMPL "
        f"either — that would price a necessity as support, which is the "
        f"inheritance #12067 closes. Reach this relation as an operator "
        f"(`create_operator(op_type={declared!r})`), or declare it IMPL/NAND. "
        f"Carriage on the bare-edge leg is the open follow-up on #12067."
    )


def _declared_meaning_for(predicate: str, registry: object = None) -> str | None:
    """The MEANING the pack relation for ``predicate`` DECLARES, else ``None``.

    The other half of the same row :func:`_mechanism_for` reads
    (``pack_registry.declared_meanings``, canonicalised through
    ``SEMANTICS_TRANSITION`` there): ``predicate: partOf`` with
    ``semantics: hasPart`` means ``hasPart``, whatever the model did or did not
    restate. A second derivation of that row here — or of the retired-spelling
    transition — is what this module's header forbids, so the registry answers and
    this leg consumes.

    ⛔ A registry read that RAISES is reported as ``None``, i.e. as "the
    declaration was not obtained" rather than aborting a capture that was
    otherwise fine (the same defensive arm as :func:`_mechanism_for`; every
    current caller is in-process). Deterministic: the first declaration in the
    registry's own order.
    """
    try:
        found = declared_meanings(label=predicate, **_registry_seam(registry))
    except Exception:  # noqa: BLE001, RUF100 — a registry read must never abort a write
        return None
    for meaning, _source in found or ():
        return meaning
    return None


def _declared_direction_for(predicate: str, registry: object = None) -> str | None:
    """The DIRECTION the pack relation for ``predicate`` RECORDS, else ``None``.

    The third leg of the same row :func:`_mechanism_for` and
    :func:`_declared_meaning_for` read, and the one this leg used to skip: the
    row's own ``direction:`` token. ``pack_registry.declared_directions`` walks
    the same rows and asks the SAME resolver the census asks
    (``effective_direction``), whose precedence — a DIFFERENTIATING declared
    token outranks the meaning — is indicator (c) of #11083. Consuming it here is
    therefore the module's own rule ("the registry decides, this leg does not
    re-derive") rather than a second opinion.

    ⛔ The bug this closes was measured on the shipped catalog: ``blocks``
    (``semantics: dependsOn, direction: "<-"``) and ``holds`` (``mechanism:
    IMPL, direction: "<-"``) both declare ``<-`` and were written ``->``, while
    ``tools/pack_relation_census.py`` reports ``<-`` for the same rows — *the
    census and the writer disagreeing about a row*, which is the exact sentence
    :func:`pack_registry.effective_direction` exists to prevent (#11548 review,
    P1). ONTOLOGY §3.6 carries the sense per declaration; §8's ``ownedBy`` row
    says the declared token wins over the default.

    A registry read that RAISES is reported as ``None`` ("the declaration was
    not obtained"), never an aborted capture — the same defensive arm as its two
    siblings.
    """
    try:
        found = declared_directions(label=predicate, **_registry_seam(registry))
    except Exception:  # noqa: BLE001, RUF100 — a registry read must never abort a write
        return None
    for direction, _source in found or ():
        return direction
    return None


def _direction_for(label: str | None, predicate: str,
                   semantics: str | None = None,
                   declared: str | None = None,
                   declared_direction: str | None = None) -> str:
    """The direction the edge's MEANING records — the ruled table, not a guess.

    ``pack_registry.SEMANTICS_DIRECTIONS`` is ONTOLOGY §8's ruled mapping from a
    meaning to the direction it records (``->`` / ``<-`` / ``<->`` / ``-``), and
    ``TortoiseSDK._direction_from_semantics`` is the SDK's one reader of it.
    Consuming it here is the same rule as the write path's own: the registry
    decides, this leg does not re-derive.

    The candidates, in the order the meaning is DECLARED, strongest first:

    0. ``declared_direction`` — the DIRECTION the pack relation row itself
       records (:func:`_declared_direction_for` →
       ``pack_registry.declared_directions`` → ``effective_direction``). The row
       is the declaration: ONTOLOGY §3.6 carries the sense per declaration and
       §8's ``ownedBy`` row says a declared token outranks the default, so this
       is not a fifth candidate but the ruling's own precedence, resolved by the
       ONE resolver the census reads. Its value is already a final token, so it
       returns before the meaning table is consulted.
    1. ``declared`` — the MEANING the pack relation row fixes for this verb
       (:func:`_declared_meaning_for`). The row is what licensed the write: it
       is the declaration ``_mechanism_for`` reads for the mechanism half of the
       same triple, so it is the authority on the meaning half too. It also
       makes the result DETERMINISTIC: keyed on the model's optional gloss
       instead, ``marks`` wrote ``-`` when the model happened to restate
       ``semantics: related`` and ``->`` when it did not — one declaration, two
       graphs (#11548 review round 8).
    2. ``label`` — the story's verb, when it is not itself a mechanism.
    3. ``predicate`` — the verb as stated, a mechanism included.
    4. ``semantics`` — the RECORD's own gloss, the last candidate: it is the
       model's restatement, and it still rules where nothing declares the verb
       (a verb outside the declared vocabulary glossed ``mutuallySupportive``).

    Every candidate is CANONICALIZED through ``SEMANTICS_TRANSITION`` first, the
    way ``declared_mechanisms`` canonicalizes both spellings: a retired word
    (``funds`` → ``transacts``, ``opposes`` → ``mutuallyExclusive``) is ruled by
    the canonical meaning's direction, and reading the pre-canonical spelling
    would compare nothing (that is how ``funds`` was stored one-way where the
    ruling is ``-``).

    ⚠️ The fallback (``_DIRECTION_STATED``) is reached only by a meaning that
    declares NO direction: a bare ``IMPL``/``NAND`` verb, or a verb and gloss
    and declaration all outside the table. It is deliberately NOT the writer's
    CYCLE-25 default (``bidirectional`` for IMPL): a stated relation runs
    ``src → dst``.

    ⚠️ A FOLD keeps the FIRST verb's label and direction (``create_direct_edge``
    is one edge per (src, tgt, type)), so the surviving meaning's direction is the
    one written; the absorbed verb is still NAMED on ``collapsed``, so nothing
    about it is silent.
    """
    from .pack_registry import SEMANTICS_DIRECTIONS, SEMANTICS_TRANSITION

    # ⛔ 0. The row's OWN ``direction:`` — the declaration that licensed the
    # write, already resolved to a final token by
    # :func:`pack_registry.effective_direction` (which applies the ruling's own
    # precedence: a DIFFERENTIATING declared token outranks the meaning). It is
    # therefore NOT a meaning and must NOT be fed through the transition table
    # below — it is the answer, not an input. Skipping it wrote ``->`` on
    # ``blocks``/``holds``, whose rows declare ``<-`` (#11548 review, P1).
    if isinstance(declared_direction, str) and declared_direction:
        return declared_direction

    for meaning in (declared, label, predicate, semantics):
        if not isinstance(meaning, str) or not meaning:
            continue
        try:
            ruled = SEMANTICS_DIRECTIONS.get(
                SEMANTICS_TRANSITION.get(meaning, meaning))
        except Exception:  # noqa: BLE001, RUF100 — a `str` SUBCLASS may raise
            # on `__hash__`; a meaning this lookup cannot read declares nothing,
            # and it must not abort the caller's write (same rule as the
            # registry reads above).
            continue
        if ruled:
            return ruled
    return _DIRECTION_STATED


def _fold_onto(
    edges: list[RelationEdge], kept_at: int, stated_predicate: str,
) -> None:
    """Record a second, DIFFERENT stated verb absorbed by the edge at ``kept_at``.

    One edge, two stated meanings, and the second one is NAMED on the edge that
    absorbed it — the arms that dedup by an edge identity (mechanism for the
    direct writer, declared predicate for the structural writer) share this.
    A literal repeat of the SAME verb is not a fold: it is the same statement
    and no meaning was lost.
    """
    kept = edges[kept_at]
    if stated_predicate == kept.stated_predicate:
        return
    if stated_predicate in kept.folded:
        return
    edges[kept_at] = dataclasses.replace(
        kept, folded=(*kept.folded, stated_predicate))


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
    #: The edge identity the STRUCTURAL arms dedup on — ``(predicate, src,
    #: dst)`` — mapped to the index of the edge it planned, so a second, DIFFERENT
    #: stated verb over the same pair is recorded as a FOLD on that edge instead
    #: of vanishing through a bare ``continue`` (#11548 round 3, P2).
    structural_at: dict[tuple[str, str, str], int] = {}
    #: The DIRECT arm's edge identity is the MECHANISM, not the predicate (see
    #: the fold comment below) — kept apart from ``structural_at`` so a
    #: structural ``IMPL`` and a direct ``IMPL`` between one pair can never be
    #: read as the same edge.
    direct_at: dict[tuple[str, str, str], int] = {}
    #: The STATEMENTS already reported as dropped. A literal repeat of an
    #: un-writable statement is one statement, exactly as it is on the landing
    #: arms (see :func:`_drop_once`).
    seen_drops: set[tuple[str, str, str, str | None]] = set()
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
            _drop_once(drops, seen_drops, src=src, dst=dst, predicate=predicate,
                       semantics=semantics, reason=reason)
            continue
        src_id, src_kind = _entity_kind(entity_index, src)
        dst_id, dst_kind = _entity_kind(entity_index, dst)

        # ── 1. a DECLARED hierarchy over the stated pair wins ──────────────
        planned = plan_hierarchy_edges(
            [(src_kind, src_id, dst_kind, dst_id)], registry,
            declared=declared_edges) if declared_edges else []
        if planned:
            # ⛔ ONE statement, at most ONE accounted outcome. A pack may declare
            # a kind pair on BOTH legs, and `plan_hierarchy_edges` returns one
            # entry per DECLARATION (not one per pair) — so the old loop landed
            # the structural edge and appended a drop row for the operator one,
            # reporting the SAME statement as written AND lost:
            # `written + dropped == 2` for a stated population of 1 (#11548
            # review F3(a)). The operator declaration is a fact about the PACK —
            # `pack_hierarchy.hierarchy_diagnostics` is its named home — not a
            # loss of the story's statement, so it is reported only when
            # nothing for this statement landed.
            landed = False
            lost_reason = ""
            for edge in planned:
                if edge.write_path != HIERARCHY_STRUCTURAL:
                    if not lost_reason:
                        operator_paths = ", ".join(sorted({
                            e.predicate for e in planned
                            if e.write_path != HIERARCHY_STRUCTURAL}))
                        lost_reason = (
                            f"the pack {edge.pack!r} declares "
                            f"{edge.from_kind} → {edge.to_kind} on the OPERATOR "
                            f"path ({operator_paths}) — this leg writes Object "
                            f"endpoints and does not mint a carrying operator")
                    continue
                key = (edge.predicate, src_id, dst_id)
                kept_at = structural_at.get(key)
                if kept_at is not None:
                    # A second statement over the same DECLARED pair plans the
                    # same edge. A different verb is a second meaning folded
                    # onto it and is NAMED; a literal repeat is the same
                    # statement and is deduped silently.
                    _fold_onto(edges, kept_at, predicate)
                    landed = True
                    continue
                routed = _routed_away_reason(edge.predicate)
                if routed:
                    if not lost_reason:
                        lost_reason = routed
                    continue
                structural_at[key] = len(edges)
                edges.append(RelationEdge(
                    src_id=src_id, dst_id=dst_id, predicate=edge.predicate,
                    write_path=RELATION_STRUCTURAL, declared_by=edge.pack,
                    stated_predicate=predicate,
                    src_name=src, dst_name=dst))
                landed = True
            if not landed:
                _drop_once(drops, seen_drops, src=src, dst=dst,
                           predicate=predicate, semantics=semantics,
                           reason=lost_reason)
            continue

        # ── 2. the story's own verb, through the registry's verdict ────────
        mechanism = _mechanism_for(predicate, semantics, registry)
        paths = _stated_verdict(predicate, mechanism)
        if not paths:
            _drop_once(
                drops, seen_drops, src=src, dst=dst, predicate=predicate,
                semantics=semantics,
                reason=(
                    # #12067 review: a verb whose OWN declaration names a
                    # mechanism this leg cannot carry must say so. The generic
                    # text below claims it is "not bound to a mechanism", which
                    # is false for a `coherence` row — and a false reason sends
                    # the pack author to fix the one thing that is already right.
                    _uncarriable_reason(predicate, semantics, registry)
                    or (
                        f"`{predicate}` binds no declared write path "
                        f"(relation_write_paths() -> ()) — it is neither in the "
                        f"structural writer's predicate set nor bound to a "
                        f"mechanism, so nothing is written and nothing is "
                        f"inferred in its place")))
            continue
        if RELATION_STRUCTURAL in paths and predicate in _VALID_EDGE_PREDICATES:
            key = (predicate, src_id, dst_id)
            kept_at = structural_at.get(key)
            if kept_at is not None:
                _fold_onto(edges, kept_at, predicate)
                continue
            routed = _routed_away_reason(predicate)
            if routed:
                _drop_once(drops, seen_drops, src=src, dst=dst,
                           predicate=predicate, semantics=semantics,
                           reason=routed)
                continue
            structural_at[key] = len(edges)
            edges.append(RelationEdge(
                src_id=src_id, dst_id=dst_id, predicate=predicate,
                write_path=RELATION_STRUCTURAL, declared_by=None,
                stated_predicate=predicate,
                src_name=src, dst_name=dst))
            continue
        if mechanism in _DIRECT_MECHANISMS:
            # The operator-less half of the declared mechanism. The MEANING
            # rides `label` so the two layers stay separate (#7852): the edge
            # type is the mechanism, the label is what the story meant.
            label = predicate if predicate not in _DIRECT_MECHANISMS else None
            if label is None and semantics and semantics != mechanism:
                label = semantics
            # ⛔ The identity here is the MECHANISM, and that is the writer's
            # own contract, not a choice made here: ``create_direct_edge`` is a
            # bare-pattern MERGE — "exactly one edge per (src,tgt,type),
            # last-writer-wins on attribute change". So two stated verbs that
            # bind the SAME mechanism between the SAME pair CANNOT be two
            # edges. The second meaning is FOLDED onto the first and recorded
            # on ``RelationEdge.folded``, because the module's own rule is
            # "a refusal is NAMED, never silent" and an uncounted fold makes a
            # session that stated three relations report ``written=2,
            # dropped=0`` with the third meaning nowhere (#11548 review, P2).
            key = (mechanism, src_id, dst_id)
            kept_at = direct_at.get(key)
            if kept_at is not None:
                _fold_onto(edges, kept_at, predicate)
                continue
            direct_at[key] = len(edges)
            edges.append(RelationEdge(
                src_id=src_id, dst_id=dst_id, predicate=mechanism,
                write_path=RELATION_DIRECT, declared_by=None,
                stated_predicate=predicate, mechanism=mechanism,
                label=label, src_name=src, dst_name=dst,
                # The meaning's RULED direction, not the writer's IMPL default
                # and not a blanket one-way (see ``_direction_for``): the pack
                # row's declared MEANING first, then the verb, and the record's
                # ``semantics`` gloss only where nothing declares one.
                direction=_direction_for(
                    label, predicate, semantics,
                    _declared_meaning_for(predicate, registry),
                    _declared_direction_for(predicate, registry))))
            continue
        # ── 3. NOTHING this leg can write — and the reason must be the real one
        #
        # ⛔ `relation_write_paths` answers for the UNION of three structural
        # writers (`STRUCTURAL_WRITE_PREDICATES` = `create_edge` +
        # `create_about_edge` + `ingest`), while this leg reaches exactly ONE of
        # them (`create_edge`, whose own set is `_VALID_EDGE_PREDICATES`). The
        # two sets differ by exactly `aboutPoint` and `extractedFrom`, so those
        # two arrived here with ``paths = ('structural',)`` and were told they
        # bind "only the operator path (('structural',))" — a sentence that
        # contradicts its own parenthetical, and one that would send a lane off
        # to build a writer the graph already has
        # (`docs/evidence/7949-capstone/RECONCILE-A1-A2-A5.md` records that
        # cost). Each state gets the sentence that is true of it (#11548 review
        # F1).
        if RELATION_STRUCTURAL in paths:
            reason = (
                f"`{predicate}` binds the STRUCTURAL path {paths} through a "
                f"writer this leg does not call: `relation_write_paths` "
                f"answers for the UNION of `create_edge`, `create_about_edge` "
                f"and `ingest`, and the linking verb this leg uses reaches "
                f"`create_edge` alone, whose own predicate set refuses "
                f"`{predicate}` — so the edge is not written here and no "
                f"second writer is minted for it")
        else:
            reason = (
                f"`{predicate}` binds only the operator path "
                f"({paths}) and declares no mechanism this leg can write "
                f"without an operator — the endpoints are Objects, so no "
                f"operator is licensed (ONTOLOGY §3.9)")
        _drop_once(drops, seen_drops, src=src, dst=dst, predicate=predicate,
                   semantics=semantics, reason=reason)
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
    "collapsed": [...], "folded": n, "write_paths": {...}}`` — the counts ride
    the capture receipt, the lists are the per-relation record. Every drop AND
    every fold is reported through ``warn`` (the caller's warnings channel), so
    a stated relation that produced no edge of its own is never silent — and
    ``written + dropped + folded`` reconciles with the stated population
    (``folded`` counts MEANINGS, so one edge that absorbed two verbs counts 2).
    That holds on the REFUSAL arm too: a refused edge that had absorbed a
    meaning yields one drop row per lost MEANING, so a refused fold is counted
    as the loss it is rather than reported as ``written=0, dropped=1`` for a
    two-statement population (#11548 round 3, P2).

    ``direction`` rides the DIRECT arm only, from ``SEMANTICS_DIRECTIONS``; the
    structural arm's writer has no slot for it (`link_entities` refuses it
    there, #4282), so those edges carry none and the row records ``None``.

    ⛔ A writer failure on ONE edge never aborts the rest: `#11237`'s channel is
    fail-open exactly as its producer is (`_assemble_relations` keeps what it
    validated and counts what it lost), so a single refusal is named and the
    next relation is still attempted.
    """
    plan, drops = plan_relation_edges(
        relations, entity_index, registry, declared=declared)
    written: list[dict] = []
    collapsed: list[dict] = []

    def _refused(edge: RelationEdge, why: str) -> None:
        """Name a refused edge, and every meaning it was carrying.

        ONE shape for BOTH refusal channels: the writer raising AND the writer
        answering that it created nothing. ``link_entities`` returns *whatever
        the chosen writer returns* (`sdk.py:17957-17964`), and the two arms
        disagree about how they report failure — the STRUCTURAL arm reaches
        ``TortoiseSDK.create_edge``, which wraps the projection's bool in an
        envelope (``{"created": False}``, `sdk.py:49406-49412`), while
        ``create_direct_edge`` RAISES and reports a dedup hit as
        ``{"created": False, "deduped": True}`` on an edge that exists. Reading
        only the exception made the leg exception-shaped on one arm and
        return-value-shaped on the other, so a structural refusal was counted as
        a WRITE: a receipt claiming an edge the graph does not hold, with no
        drop row and no warning (#11548 review, ISSUE 1). One function, so the
        two channels cannot drift apart again.
        """
        drops.append(RelationDrop(
            edge.src_name, edge.dst_name, edge.stated_predicate,
            f"the writer refused {edge.predicate!r} ({why}) — the relation "
            f"stays uncommitted"))
        for folded_verb in edge.folded:
            drops.append(RelationDrop(
                edge.src_name, edge.dst_name, folded_verb,
                f"this meaning was folded onto {edge.stated_predicate!r} "
                f"({edge.predicate!r}), whose write the writer refused "
                f"({why}) — the fold lost the meaning with the edge"))

    for edge in plan:
        try:
            if edge.write_path == RELATION_DIRECT:
                # ``direction`` is passed ONLY on this arm: ``link_entities``
                # refuses a parameter the reached writer does not honour, and
                # ``create_edge`` (structural) records no direction at all.
                # The RETURN is read: ``False`` is a refusal, not a write.
                result = sdk.link_entities(edge.predicate, edge.src_id,
                                           edge.dst_id,
                                           label=edge.label,
                                           direction=edge.direction)
            else:
                result = sdk.link_entities(edge.predicate, edge.src_id,
                                           edge.dst_id)
        except Exception as exc:  # noqa: BLE001, RUF100 — fail-open per relation
            # ⛔ ``RelationDrop`` is ``(src, dst, predicate, reason)`` — the two
            # NAMES, then the verb. This arm used to pass the stated verb as
            # ``src`` and an empty ``dst``, so a refused pair lost both of its
            # endpoints and every drop row in a receipt no longer had one
            # shape (#11548 review, P2).
            _refused(edge, f"{type(exc).__name__}: {exc}")
            continue
        # ⛔ Read the refusal channel PER ARM — the two arms do NOT answer in
        # the same shape, and reading one shape on both is how a structural
        # refusal was counted as a WRITE (#11548 review, P1/E2's false PASS).
        #
        # STRUCTURAL: ``link_entities`` dispatches to
        # :meth:`TortoiseSDK.create_edge` on this arm, and that door does NOT
        # return the projection's bare ``bool`` — it WRAPS it
        # (``sdk.py:49406-49412``): ``{"edge": …, "created": created,
        # "nudges": […]}``. So ``is False`` was DEAD here, and a write the
        # projection declined (it resolved no endpoint) was appended to
        # ``written`` with no drop row and no warning.
        #
        # DIRECT: ``create_direct_edge`` answers ``{"created": …, "deduped":
        # not created}`` (``sdk.py:29278-29279``), and a MERGE dedup hit is
        # ``created: False`` on an edge the graph DOES hold — so ``created is
        # False`` must NOT be read on this arm, or every idempotent re-capture
        # would be reported as a refusal.
        refused = result is False or (
            edge.write_path != RELATION_DIRECT
            and isinstance(result, dict)
            and result.get("created") is False)
        if refused:
            _refused(edge, "the writer answered that it created NOTHING for "
                           "this edge, so the relation stays uncommitted")
            continue
        if edge.folded:
            # ⛔ Recorded ONLY for an edge that actually LANDED. Building this
            # from the PLAN reported "the meaning was folded onto 'causes'" for
            # an edge the writer had just refused — a lost meaning described as
            # safely absorbed, and one short on the loss count (#11548 review
            # round 2, BLOCKER).
            collapsed.append({
                "src": edge.src_name,
                "dst": edge.dst_name,
                "predicate": edge.predicate,
                "kept": edge.label or edge.stated_predicate,
                "folded": list(edge.folded),
                "reason": (
                    f"{', '.join(repr(v) for v in edge.folded)} bound the same "
                    f"mechanism {edge.predicate!r} between this pair, and "
                    f"create_direct_edge is one edge per (src, tgt, type) — the "
                    f"meaning was folded onto "
                    f"{edge.label or edge.stated_predicate!r} (#11237; ONTOLOGY "
                    f"§3.9 gives an Object→Object pair no carrying operator to "
                    f"hold a second one)"),
            })
        written.append({
            "src_id": edge.src_id,
            "dst_id": edge.dst_id,
            "predicate": edge.predicate,
            "write_path": edge.write_path,
            "stated_predicate": edge.stated_predicate,
            "declared_by": edge.declared_by,
            "mechanism": edge.mechanism,
            "label": edge.label,
            # What the graph should hold for this edge — carried on the receipt
            # so a reader can tell a one-way stated claim from the writer's
            # IMPL default without re-deriving it (#11548 review, P2).
            "direction": edge.direction,
            "folded": list(edge.folded),
        })
    by_path: dict[str, int] = {}
    for row in written:
        by_path[row["write_path"]] = by_path.get(row["write_path"], 0) + 1
    if warn is not None:
        for drop in drops:
            warn(
                f"narrative relation not written (#11237): "
                f"{drop.predicate} {drop.src!r} → {drop.dst!r} — {drop.reason}")
        for fold in collapsed:
            warn(
                f"narrative relation folded (#11237): "
                f"{fold['folded']} {fold['src']!r} → {fold['dst']!r} — "
                f"{fold['reason']}")
    return {
        "edges": written,
        "drops": [dataclasses.asdict(d) for d in drops],
        "collapsed": collapsed,
        "written": len(written),
        "dropped": len(drops),
        # MEANINGS folded, not edges carrying a fold: one edge can absorb two
        # verbs, so counting edges made `written + dropped + folded` understate
        # the stated population (#11548 review round 2, nit).
        "folded": sum(len(row["folded"]) for row in collapsed),
        "write_paths": by_path,
    }
