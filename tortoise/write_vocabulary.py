"""S5/C10 (#11484) — the structural WRITE VOCABULARY, DERIVED per graph.

THE RULING THIS IMPLEMENTS (owner, 2026-11-05 — recorded on `#11484`):

    *"the core ontology is expanded by the expansion packs, so they're just an
    expansion. And the enforced write vocabulary needs to be matched to the
    others **automatically**, because the ontology **core+packs is the canonical
    source**. Note the packs can be toggled on/off **per graph**."*

So the graph's admitted structural predicates are a **function of its effective
ontology**, never a hand-maintained list beside a declarative one::

    effective_ontology(graph)  = core §3.7/§3.9 ∪ installed_packs(graph)
    allowed_predicates(graph)  = core predicates  ∪ {rel.predicate ∈ installed}

THE DEFECT THIS MODULE REMOVES, measured at the head it was filed against
(`04-plan.md` §5.3a): the OPERATOR/label half was already derived
(``pack_registry.declared_relation_predicates``: ``declared |= {r["predicate"]
for r in reg.list_relations()}``) while the STRUCTURAL half was a static literal
(``projection/edges.py::_VALID_EDGE_PREDICATES``) — an asymmetry inside ONE
tree. Consequence: a pack-declared object relation (``funds`` from
``packs/venture``, ``dependsOn`` from ``packs/dev``) was **rejected by the
structural writer** and accepted only as an IMPL operator *label*, so the
structural object layer could never be expanded by a pack.

⛔ THREE FACTS PER PREDICATE — NOT A SET OF NAMES (`04-plan.md` §6, contract I10).
A set-of-names implementation passes every other pin and still produces
**live-only edges that `rebuild_all` drops**, silently. So the derivation answers
``{admitted_by, verb, record_carried}`` per predicate:

  * ``admitted_by`` — ``"core"``, or the NAMESPACE of the pack that declares it.
    The core always wins the credit when both declare a predicate (a pack may
    not take ownership of a §3.9 predicate by re-declaring it).
  * ``verb`` — WHICH writer accepts it: ``create_edge`` (the generic mint),
    ``create_about_edge`` (the ``about*`` mint), or ``ingest`` (the relation
    arm's own ``extractedFrom`` exemption). Derived from the writers' own sets,
    so a predicate added to any one writer moves this answer with it.
  * ``record_carried`` — is the edge DURABLE across ``rebuild_all``? True when
    either carrier answers: the record carrier
    (``structural_edge_record_carrier`` — the lane journals a flat
    ``DirectEdgeCreated``) or the endpoint-snapshot carrier
    (``structural_edge_carrier``). A predicate admitted live but carried by
    neither is written and then dropped by a rebuild with no warning — the
    failure mode this field exists to make statable.

PER-GRAPH, FROM THE EXISTING RESOLVER — DERIVATION, NOT INVENTION
----------------------------------------------------------------
The per-graph half is ``pack_state.graph_approval_namespaces(g)`` (the bare
graph-handle twin of ``graph_installed_namespaces(sdk)``, #5475/#2728). Its
THREE-VALUED answer is honoured verbatim, because the distinction decides what a
graph can express:

  * ``None`` — no ``:PackInstall`` records at all ⇒ the **catalog union**
    (#2714 indicator 3: every pre-#318 / self-hosted / restored graph);
  * a frozenset — the graph's recorded ∪ already-in-data namespaces;
  * an EMPTY frozenset — a CONFIGURED graph whose deliberate set is empty
    (#2728): **only core**, and the union fallback MUST NOT apply.

⛔ A caller with no graph gets the catalog union — that is not a widening, it is
the same answer ``relation_write_paths`` gave before this module existed, and it
is what the catalog-facing surfaces (``tortoise pack validate``, pack load, the
census tool) must keep answering.

COST — the fast path is UNCHANGED
---------------------------------
The per-graph read is a whole-graph kind scan (``pack_state`` says so), and
``create_edge`` is on the write path. So the core half is answered from a memo
with **no graph read at all**, and the resolver runs only for a predicate the
core does not carry — i.e. only for a pack-declared predicate, which is exactly
the case whose answer must be per-graph. A core predicate therefore costs one
dict lookup, as before.

⛔ THE RESOLVER'S FAILURE IS NOT A WIDENING. ``graph_approval_namespaces``
RAISES when the graph is unreachable (its documented posture), and this module
lets that propagate: a write that cannot read its own graph is about to fail
anyway, and answering "every pack is installed" from an outage is precisely the
silent widening the resolver refuses to make.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: The namespace of the CORE vocabulary. Repeated from ``pack_state.CORE_NAMESPACE``
#: rather than imported, so this module stays importable from the projection
#: without dragging the pack stack in behind it (the same lazy-import budget
#: ``relation_write_paths`` documents). ``tests/test_11484_derived_write_vocabulary.py``
#: pins the two equal, so the spelling cannot drift.
CORE_NAMESPACE = "core"

#: The writers a predicate can be admitted by. These are the three structural
#: write surfaces the tree owns; ``verb`` answers "which one accepts this name".
VERB_CREATE_EDGE = "create_edge"
VERB_CREATE_ABOUT_EDGE = "create_about_edge"
VERB_INGEST = "ingest"

#: The verbs a PACK-declared predicate is admitted by. A pack declares a
#: business relation (``funds``: investor → investment), and the generic mint is
#: the writer that accepts an arbitrary structural predicate between two
#: resolved endpoints. The two specialist verbs stay core-only on purpose: the
#: ``about*`` mint is the epistemic-provenance family (a fixed, closed set of
#: target labels), and ``ingest``'s own exemption is ``extractedFrom`` — a
#: Point→Source provenance link, not a business relation.
PACK_VERB = VERB_CREATE_EDGE


@dataclass(frozen=True)
class PredicateFacts:
    """Contract I10 (`04-plan.md` §6) — the three facts, one predicate.

    ``admitted_by``   the ``"core"`` marker or a pack namespace.
    ``verb``          the writer that accepts it (see the ``VERB_*`` names).
    ``record_carried`` durable across ``rebuild_all`` (record or snapshot carrier).
    """

    admitted_by: str
    verb: str
    record_carried: bool

    @property
    def from_core(self) -> bool:
        return self.admitted_by == CORE_NAMESPACE

    def as_dict(self) -> dict[str, Any]:
        """The plain mapping a report/JSON surface publishes."""
        return {
            "admitted_by": self.admitted_by,
            "verb": self.verb,
            "record_carried": self.record_carried,
        }


#: ``installed`` was not supplied — resolve it from the graph, or fall back to
#: the catalog union when there is no graph. A sentinel rather than ``None``
#: because ``None`` is a MEANINGFUL answer from the resolver ("no
#: ``:PackInstall`` records") and conflating the two would make the explicit
#: catalog-union call indistinguishable from an unresolved one.
_UNSET = object()

#: The PUBLIC spelling of the sentinel above, for a caller that must put it in a
#: function SIGNATURE (a default argument is evaluated at import time, so
#: ``pack_registry.relation_write_paths(installed=UNSET)`` needs a name it can
#: import without reaching into a private one).
UNSET = _UNSET

#: ``graph`` was not supplied — the catalog-facing answer.
_NO_GRAPH = object()

#: The core table, memoised: ``id(core literal) -> {predicate: PredicateFacts}``.
#: Keyed on the writers' own frozenset object so a test that monkeypatches the
#: structural vocabulary (or a future dynamic core) cannot read a stale table.
#: ``id()`` is safe here because the value it keys is held alive by the module
#: that owns it for the process lifetime (``projection.edges``' module dict); the
#: stored tuple keeps a reference anyway, which is what makes it safe.
_CORE_MEMO: dict[int, tuple[frozenset[str], dict[str, PredicateFacts]]] = {}


def _edges():
    """Lazy access to the writers' module — never at import time.

    Importing ``tortoise.projection.edges`` pulls the projection package in
    (~60 ms, measured); the pack registry and the extraction path import THIS
    module and must not pay that. The same budget the existing
    ``relation_write_paths`` documents.
    """
    from .projection import edges as _edges_module

    return _edges_module


def core_predicates() -> dict[str, PredicateFacts]:
    """The CORE half: every predicate the structural writers accept, with facts.

    Derived from the writers' own declarations — never re-transcribed here:

    * the vocabulary is ``STRUCTURAL_WRITE_PREDICATES``, the UNION of the two
      strict mints' allow-lists plus ``ingest``'s exemption (the set the
      ``#2766`` verdict already reads, so the verdict and the writer cannot
      disagree — ``#8285`` review P1);
    * ``verb`` is resolved per predicate from which of those sets holds it;
    * ``record_carried`` asks the two carriers.
    """
    mod = _edges()
    vocabulary = mod.STRUCTURAL_WRITE_PREDICATES
    memo = _CORE_MEMO.get(id(vocabulary))
    if memo is not None and memo[0] == vocabulary:
        return dict(memo[1])

    generic = mod._VALID_EDGE_PREDICATES
    about = mod._VALID_ABOUT_EDGE_PREDICATES
    table: dict[str, PredicateFacts] = {}
    for predicate in sorted(vocabulary):
        if predicate in generic:
            verb = VERB_CREATE_EDGE
        elif predicate in about:
            verb = VERB_CREATE_ABOUT_EDGE
        else:
            # The one predicate admitted by NEITHER strict mint — the ingest
            # relation arm's own exemption (`INGEST_ONLY_EDGE_PREDICATES`,
            # derived in `projection/edges.py`, asserted singleton there).
            verb = VERB_INGEST
        carried = (mod.structural_edge_record_carrier(predicate) is not None
                   or mod.structural_edge_carrier(predicate) is not None)
        table[predicate] = PredicateFacts(
            admitted_by=CORE_NAMESPACE, verb=verb, record_carried=carried)
    _CORE_MEMO[id(vocabulary)] = (vocabulary, table)
    return dict(table)


def _registry():
    """The ONE pack registry the rest of the tree reads (``domain_loader``'s).

    ``None`` means the catalog could not be read — treated as "no pack declares
    anything", which yields the core vocabulary alone. That is the SAFE
    direction for a gate: an unreadable catalog must not admit a predicate no
    one can show a declaration for.
    """
    try:
        from .domain_loader import _get_registry

        return _get_registry()
    except Exception:  # noqa: BLE001, RUF100 — never raise out of a lookup
        return None


def resolve_installed(graph: Any) -> frozenset[str] | None:
    """The graph's approval set, from the EXISTING resolver (#2714/#2728).

    ``None`` (no ``:PackInstall`` records) keeps its meaning: the caller falls
    back to the catalog union. Raises when the graph is unreachable — the
    resolver's own posture, and the honest one for a write gate.
    """
    from .pack_state import graph_approval_namespaces

    return graph_approval_namespaces(graph)


def _pack_declarations(registry=None) -> dict[str, str]:
    """``{predicate: namespace}`` across the CATALOG — every declared relation.

    Iterates the manifests' raw ``relations[]`` rows rather than
    ``PackRegistry.list_relations()``, and that is deliberate twice over: the
    list surface computes the very verdict that calls back into this module (a
    recursion this function would deadlock on), and the raw rows are what a
    declaration IS. One row per predicate is enough for the admission credit;
    a predicate two packs declare is attributed to the first manifest in load
    order and the ambiguity is not a vocabulary question (it is a pack
    governance one).
    """
    reg = registry if registry is not None else _registry()
    if reg is None:
        return {}
    declared: dict[str, str] = {}
    for namespace, pack in sorted((reg.packs or {}).items()):
        for row in getattr(pack, "relations", ()) or ():
            if not isinstance(row, dict):
                continue
            predicate = row.get("predicate")
            if isinstance(predicate, str) and predicate and predicate not in declared:
                declared[predicate] = namespace
    return declared


def pack_predicates(installed: frozenset[str] | None = _UNSET, *,
                    registry=None) -> dict[str, PredicateFacts]:
    """The PACK half, narrowed to ``installed`` (``None`` ⇒ the whole catalog).

    A pack-declared predicate is admitted by ``create_edge`` (``PACK_VERB``) and
    is **record-carried**: the structural edge producer
    (``projection.edges::_record_structural_edges``) journals a
    ``DirectEdgeCreated`` for ANY relation whose endpoints the fold can address —
    it gates on the ENDPOINTS, not on a membership list — so the edge a pack
    predicate writes survives ``rebuild_all`` exactly like a core one. That is
    measured, not assumed: ``tests/test_11484_derived_write_vocabulary.py``
    writes a pack predicate and rebuilds from the journal.
    """
    declared = _pack_declarations(registry)
    if not declared:
        return {}
    # ``_UNSET`` is the direct-call default: the whole catalog, the same answer
    # ``None`` gives, kept as a distinct default so ``allowed_predicates`` can
    # tell "the caller named no set" from "the resolver said: no records".
    narrow = None if installed is _UNSET else installed
    facts: dict[str, PredicateFacts] = {}
    for predicate, namespace in declared.items():
        if narrow is not None and namespace not in narrow:
            continue
        facts[predicate] = PredicateFacts(
            admitted_by=namespace, verb=PACK_VERB, record_carried=True)
    return facts


def allowed_predicates(graph: Any = _NO_GRAPH, *,
                       installed: frozenset[str] | None = _UNSET,
                       registry=None) -> dict[str, PredicateFacts]:
    """``allowed_predicates(graph)`` — contract I10's ordered mapping.

    ``predicate -> PredicateFacts``; ordered by predicate name so two calls
    (and two processes) report the same sequence.

    Three ways to ask, one answer:

    * ``allowed_predicates(graph_handle)`` — resolve the graph's installed packs
      through ``pack_state.graph_approval_namespaces`` (the per-graph form the
      ruling requires);
    * ``allowed_predicates(installed=frozenset({"venture"}))`` — a caller that
      already holds the approval set, or a test that wants no graph at all;
    * ``allowed_predicates()`` — the CATALOG UNION (every pack in the catalog),
      which is what ``relation_write_paths`` answered before this module and what
      the catalog-facing surfaces must keep answering.

    A CORE predicate always reports ``admitted_by="core"``: a pack that
    re-declares a §3.9 predicate does not take ownership of it.
    """
    resolved = installed if installed is not _UNSET else (
        _UNSET if graph is _NO_GRAPH or graph is None else resolve_installed(graph))
    facts = core_predicates()
    for predicate, pack_facts in pack_predicates(
            None if resolved is _UNSET else resolved, registry=registry).items():
        if predicate not in facts:
            facts[predicate] = pack_facts
    return dict(sorted(facts.items()))


def allowed_predicate_names(graph: Any = _NO_GRAPH, **kwargs) -> frozenset[str]:
    """The names alone — for a caller that only asks "may I write this?".

    Same derivation as :func:`allowed_predicates`; see it for the arguments.
    """
    return frozenset(allowed_predicates(graph, **kwargs))


def predicate_facts(predicate: str, graph: Any = _NO_GRAPH, *,
                    installed: frozenset[str] | None = _UNSET,
                    registry=None) -> PredicateFacts | None:
    """ONE predicate's facts, with the core fast path (no graph read).

    ``None`` — the predicate is admitted by neither the core nor an installed
    pack. This is the shape a write gate wants: a core predicate answers from
    the memo, and the per-graph resolver runs only for a name the core does not
    carry (a pack-declared one), which is exactly the question that must be
    answered per graph.
    """
    core = core_predicates()
    facts = core.get(predicate)
    if facts is not None:
        return facts
    resolved = installed if installed is not _UNSET else (
        _UNSET if graph is _NO_GRAPH or graph is None else resolve_installed(graph))
    pack = pack_predicates(
        None if resolved is _UNSET else resolved,
        registry=registry).get(predicate)
    return pack


def is_admitted(predicate: str, graph: Any = _NO_GRAPH, *,
                installed: frozenset[str] | None = _UNSET,
                registry=None) -> bool:
    """``predicate_facts(...) is not None`` — the gate's single question."""
    return predicate_facts(predicate, graph, installed=installed,
                           registry=registry) is not None
