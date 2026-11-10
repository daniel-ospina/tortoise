"""#11001 — the pack's DECLARED hierarchy, read; never inferred.

Red-first suite. It fails at HEAD because:
  * ``PackManifest`` DROPS ``ontology.hierarchies`` at load (validated in
    ``_validate``, never carried onto the dataclass), so the declaration cannot
    even be observed, and
  * no producer exists — ``hierarchies`` was validated and read by nobody, so
    the graph holds a flat catalogue of Objects with zero edges.

The failure the pair of tests below exists to catch is the INVENTED edge: a
producer that infers a hierarchy (kind nesting, name similarity, "theme probably
contains epic") instead of reading the declaration it was given. An invented
edge is a structure nobody asserted, and every downstream answer inherits it.
"""
from __future__ import annotations

import os
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.pack_hierarchy import (
    HIERARCHY_STRUCTURAL,
    declared_hierarchy,
    hierarchy_diagnostics,
    plan_hierarchy_edges,
)
from tortoise.pack_registry import PackRegistry

PACKS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "packs")


# ── fixtures ──────────────────────────────────────────────────────────────

def _registry(tmp_path, *, hierarchies, relations=(), kinds=("theme", "initiative", "epic", "issue")):
    """A one-pack registry whose manifest declares `hierarchies` verbatim."""
    d = str(tmp_path / "packs")
    pack = {
        "namespace": "test",
        "name": "Test Pack",
        "version": "1.0.0",
        "tier": "free",
        "description": "hierarchy fixture",
        "ontology": {
            "extends": "core",
            "objectKinds": list(kinds),
            "relations": list(relations),
            "hierarchies": hierarchies,
        },
    }
    os.makedirs(f"{d}/test", exist_ok=True)
    with open(f"{d}/test/manifest.yaml", "w") as f:
        yaml.dump(pack, f)
    registry = PackRegistry(d)
    registry.load_all()
    assert not registry.errors, registry.errors
    return registry


def _declared_pairs(registry=None):
    """The declared pairs as plain tuples — comparable without the dataclass."""
    return [(e.predicate, e.from_kind, e.to_kind) for e in declared_hierarchy(registry)]


# ── 1. the declaration survives the load ──────────────────────────────────

class TestTheDeclarationIsCarried:
    def test_manifest_carries_hierarchies(self, tmp_path):
        """HEAD drops the field entirely: `ontology.hierarchies` is validated
        and then discarded, so nothing downstream can read it."""
        reg = _registry(tmp_path, hierarchies=[{"path": "Theme → Epic", "type": "hasPart"}])
        assert reg.packs["test"].hierarchies == [{"path": "Theme → Epic", "type": "hasPart"}]


# ── 2. a declared hierarchy produces EXACTLY itself ────────────────────────

class TestDeclaredProducesExactlyItself:
    def test_one_declaration_one_edge(self, tmp_path):
        reg = _registry(
            tmp_path,
            hierarchies=[{"path": "Theme → Epic", "type": "hasPart"}],
            relations=[{"predicate": "hasPart", "mechanism": "IMPL",
                        "semantics": "hasPart",
                        "fromKind": "test:theme", "toKind": "test:epic"}],
        )
        assert _declared_pairs(reg) == [("hasPart", "test:theme", "test:epic")]

    def test_declaration_is_read_in_the_direction_it_is_written(self, tmp_path):
        """`A → B` is A hasPart B. Reversing it is the cheapest possible
        inference, so it is pinned."""
        reg = _registry(
            tmp_path,
            hierarchies=[{"path": "Theme → Epic", "type": "hasPart"}],
            relations=[{"predicate": "hasPart", "mechanism": "IMPL",
                        "semantics": "hasPart",
                        "fromKind": "test:theme", "toKind": "test:epic"}],
        )
        (edge,) = declared_hierarchy(reg)
        assert (edge.from_kind, edge.to_kind) == ("test:theme", "test:epic")

    def test_the_type_is_the_predicate(self, tmp_path):
        """The entry's `type` is the edge's predicate — not a kind and not a
        guess at `hasPart`."""
        reg = _registry(
            tmp_path,
            hierarchies=[{"path": "Theme → Epic", "type": "dependsOn"}],
            relations=[{"predicate": "dependsOn", "mechanism": "IMPL",
                        "fromKind": "test:theme",
                        "toKind": "test:epic"}],
        )
        assert _declared_pairs(reg) == [("dependsOn", "test:theme", "test:epic")]


# ── 3. a pack that declares NONE produces NONE ────────────────────────────

class TestNoDeclarationNoEdges:
    def test_absent_hierarchies_produces_nothing(self, tmp_path):
        reg = _registry(tmp_path, hierarchies=[])
        assert _declared_pairs(reg) == []

    def test_none_of_the_four_kinds_is_paired_without_a_declaration(self, tmp_path):
        """Four kinds, zero declarations, zero edges — even though a heuristic
        could "obviously" nest them theme → initiative → epic → issue."""
        reg = _registry(tmp_path, hierarchies=[])
        candidates = [
            ("test:theme", "t1", "test:initiative", "i1"),
            ("test:initiative", "i1", "test:epic", "e1"),
            ("test:epic", "e1", "test:issue", "s1"),
        ]
        assert plan_hierarchy_edges(candidates, registry=reg) == []


# ── 4. the INVENTED edge is the failure (E2E-4 T8) ─────────────────────────

class TestInventedEdgeEmitsNothing:
    def test_an_undeclared_pair_produces_no_edge(self, tmp_path):
        """`theme → issue` is not declared; only `theme → epic` is. A producer
        that walks kinds instead of reading the declaration emits it."""
        reg = _registry(
            tmp_path,
            hierarchies=[{"path": "Theme → Epic", "type": "hasPart"}],
            relations=[{"predicate": "hasPart", "mechanism": "IMPL",
                        "semantics": "hasPart",
                        "fromKind": "test:theme", "toKind": "test:epic"}],
        )
        candidates = [
            ("test:theme", "t1", "test:epic", "e1"),      # declared
            ("test:theme", "t1", "test:issue", "s1"),     # INVENTED
        ]
        plan = plan_hierarchy_edges(candidates, registry=reg)
        assert [(p.predicate, p.from_id, p.to_id) for p in plan] == [
            ("hasPart", "t1", "e1")], "an undeclared pair must emit zero edges"

    def test_a_direct_hop_past_the_declared_levels_emits_nothing(self, tmp_path):
        """E2E-4 T8 verbatim: with the four-level path declared, a direct
        theme→epic hop is undeclared and must emit ZERO — the transitive
        closure is the tempting inference this pins out."""
        reg = _registry(
            tmp_path,
            hierarchies=[
                {"path": "Theme → Initiative", "type": "hasPart"},
                {"path": "Initiative → Epic", "type": "hasPart"},
                {"path": "Epic → Issue", "type": "hasPart"},
            ],
            relations=[{"predicate": "hasPart", "mechanism": "IMPL",
                        "semantics": "hasPart",
                        "fromKind": "test:theme", "toKind": "test:initiative"},
                       {"predicate": "hasPart", "mechanism": "IMPL",
                        "semantics": "hasPart",
                        "fromKind": "test:initiative", "toKind": "test:epic"},
                       {"predicate": "hasPart", "mechanism": "IMPL",
                        "semantics": "hasPart",
                        "fromKind": "test:epic", "toKind": "test:issue"}],
        )
        candidates = [
            ("test:theme", "t1", "test:initiative", "i1"),
            ("test:initiative", "i1", "test:epic", "e1"),
            ("test:epic", "e1", "test:issue", "s1"),
            ("test:theme", "t1", "test:epic", "e1"),   # direct hop — INVENTED
            ("test:theme", "t1", "test:issue", "s1"),  # closure — INVENTED
        ]
        plan = plan_hierarchy_edges(candidates, registry=reg)
        assert sorted((p.from_kind, p.to_kind) for p in plan) == [
            ("test:epic", "test:issue"),
            ("test:initiative", "test:epic"),
            ("test:theme", "test:initiative"),
        ]

    def test_a_hierarchy_with_no_declared_write_path_is_reported_not_written(self, tmp_path):
        """`type: relatedTo` binds no write path (it is neither a structural
        predicate nor a declared mechanism). It must be NAMED, not silently
        dropped and not written anyway."""
        reg = _registry(
            tmp_path,
            hierarchies=[{"path": "Theme → Epic", "type": "relatedTo"}],
        )
        assert declared_hierarchy(reg) == ()
        (diag,) = hierarchy_diagnostics(reg)
        assert diag["pack"] == "test"
        assert diag["from_kind"] == "test:theme"
        assert diag["to_kind"] == "test:epic"
        assert "relatedTo" in diag["reason"]


# ── 5. the write path is CONSUMED, never re-derived (#11001) ──────────────

class TestWritePathIsConsumed:
    def test_structural_is_chosen_for_a_plain_edge(self, tmp_path):
        """ONTOLOGY §2: composition is a PLAIN, unweighted edge. `hasPart` is
        in the writer's structural set, so that is the path — not the operator
        the same relation also declares through `mechanism: IMPL`."""
        reg = _registry(
            tmp_path,
            hierarchies=[{"path": "Theme → Epic", "type": "hasPart"}],
            relations=[{"predicate": "hasPart", "mechanism": "IMPL",
                        "semantics": "hasPart",
                        "fromKind": "test:theme", "toKind": "test:epic"}],
        )
        (edge,) = declared_hierarchy(reg)
        assert edge.write_path == HIERARCHY_STRUCTURAL

    def test_an_operator_only_declaration_reports_the_operator_path(self, tmp_path):
        """A pack that binds only a mechanism, on a predicate the structural
        writer refuses, gets the operator path — read from
        `relation_write_paths`, not re-derived here."""
        reg = _registry(
            tmp_path,
            hierarchies=[{"path": "Theme → Epic", "type": "strengthens"}],
            relations=[{"predicate": "strengthens", "mechanism": "IMPL",
                        "fromKind": "test:theme", "toKind": "test:epic"}],
        )
        (edge,) = declared_hierarchy(reg)
        assert edge.write_path == "operator"

    def test_the_semantics_leg_binds_a_renamed_relation(self, tmp_path):
        """A pack may spell the verb differently in `relations[]` than in
        `hierarchies[]` (`dev` writes `decomposesInto` with `semantics:
        hasPart` where its hierarchy says `type: hasPart`). ONE declaration
        spelled twice — the join is on the declared fields, never on a string
        guess. Made load-bearing here with a `type` the structural writer does
        NOT accept, so only the `semantics` leg can bind it."""
        reg = _registry(
            tmp_path,
            hierarchies=[{"path": "Theme → Epic", "type": "supports"}],
            relations=[{"predicate": "backsUp", "mechanism": "IMPL",
                        "semantics": "supports",
                        "fromKind": "test:theme", "toKind": "test:epic"}],
        )
        (edge,) = declared_hierarchy(reg)
        assert (edge.predicate, edge.from_kind, edge.to_kind) == (
            "supports", "test:theme", "test:epic")
        assert edge.write_path == "operator"
        assert edge.mechanism == "IMPL"


# ── 6. idempotence ────────────────────────────────────────────────────────

class TestIdempotence:
    def test_the_same_pack_applied_twice_does_not_duplicate(self, tmp_path):
        reg = _registry(
            tmp_path,
            hierarchies=[{"path": "Theme → Epic", "type": "hasPart"}],
            relations=[{"predicate": "hasPart", "mechanism": "IMPL",
                        "semantics": "hasPart",
                        "fromKind": "test:theme", "toKind": "test:epic"}],
        )
        candidates = [("test:theme", "t1", "test:epic", "e1"),
                      ("test:theme", "t1", "test:epic", "e1")]  # duplicate pair
        first = plan_hierarchy_edges(candidates, registry=reg)
        second = plan_hierarchy_edges(candidates, registry=reg)
        assert len(first) == 1, "a repeated candidate is ONE edge"
        assert first == second, "the plan is a pure function of the declaration"

    def test_repeated_application_yields_one_edge_per_declared_pair(self, tmp_path):
        """Plan stability is the producer's half of idempotence; the writer's
        half is its `MERGE` (`projection/edges.py`, `create_edge`)."""
        reg = _registry(
            tmp_path,
            hierarchies=[{"path": "Theme → Epic", "type": "hasPart"},
                         {"path": "Epic → Issue", "type": "hasPart"}],
            relations=[{"predicate": "hasPart", "mechanism": "IMPL",
                        "semantics": "hasPart",
                        "fromKind": "test:theme", "toKind": "test:epic"},
                       {"predicate": "hasPart", "mechanism": "IMPL",
                        "semantics": "hasPart",
                        "fromKind": "test:epic", "toKind": "test:issue"}],
        )
        candidates = [("test:theme", "t1", "test:epic", "e1"),
                      ("test:epic", "e1", "test:issue", "s1")]
        keys = {(p.predicate, p.from_id, p.to_id)
                for _ in range(3)
                for p in plan_hierarchy_edges(candidates, registry=reg)}
        assert len(keys) == 2


# ── 7. the real packs (the census number the report quotes) ────────────────

class TestTheShippedPacks:
    def test_every_shipped_hierarchy_resolves_to_a_write_path(self):
        """`declared=<n>` in the lane report is THIS count, over the packs this
        repo ships — and zero diagnostics means no declared hierarchy is
        unbound."""
        reg = PackRegistry(PACKS_DIR)
        reg.load_all()
        declared = declared_hierarchy(reg)
        assert len(declared) >= 6, (
            f"the shipped packs declare at least 6 hierarchies, resolved "
            f"{len(declared)}: {[ (e.pack, e.from_kind, e.to_kind) for e in declared ]}")
        assert hierarchy_diagnostics(reg) == (), (
            "a shipped hierarchy entry binds no declared write path")
        for edge in declared:
            assert edge.write_path, f"{edge.pack}: {edge.from_kind}->{edge.to_kind}"

    def test_dev_declares_theme_to_epic_and_epic_to_issue(self):
        reg = PackRegistry(PACKS_DIR)
        reg.load_all()
        pairs = {(e.from_kind, e.to_kind) for e in declared_hierarchy(reg) if e.pack == "dev"}
        assert ("dev:theme", "dev:epic") in pairs
        assert ("dev:epic", "dev:issue") in pairs

    def test_the_chain_steps_are_not_mistaken_for_a_hierarchy(self):
        """`chains` (`epicToCode`: epic, issue, code) is a DIFFERENT declaration
        — it is prompt guidance, not a composition. Reading it as a hierarchy
        would invent `issue → code`."""
        reg = PackRegistry(PACKS_DIR)
        reg.load_all()
        assert ("dev:issue", "dev:code") not in {
            (e.from_kind, e.to_kind) for e in declared_hierarchy(reg)}

    # ── KIN-2: the commitment spine (sixth capstone row E6) ────────────────
    # `docs/epics/2026-11-05-5087-extractor-v4/06-pack-logic-decisions.md`
    # §KIN-2 approved three connections on 2026-11-05 and NO child declared
    # them, so the census reported `goal` as a kind the graph can hold and
    # cannot connect (`docs/evidence/11083/pack-relation-census.json`: 0 of 42
    # declared connections named it as an endpoint, `owner: None`).
    #
    # `goal` is a CORE kind and there is no `packs/core/manifest.yaml`, so the
    # declarations live in `dev` — the pack that owns `epic`/`indicator`/`theme`
    # and already points relations at core kinds (`deploysTo` →
    # `core:deployment`).
    _KIN2_PAIRS = (
        ("core:goal", "ownedBy", "Subject"),
        ("dev:epic", "addresses", "core:goal"),
        ("dev:indicator", "addresses", "core:goal"),
    )

    def test_the_kin2_goal_connections_are_declared_and_writable(self):
        """Declared, and bound to a write path — a declaration nobody can
        write is the `predicate-not-in-allowlist` shape #2766 exists to kill.

        The census is what MEASURES the write (`direct` for the `ownedBy` row,
        `mediated` for the two `addresses` rows); this pin is the hermetic half
        that reds the moment a declaration is dropped, without a database.
        """
        from tortoise.pack_registry import relation_write_verdict

        reg = PackRegistry(PACKS_DIR)
        reg.load_all()
        rows = {(r.get("fromKind"), r.get("predicate"), r.get("toKind"))
                for r in reg.list_relations()}
        for pair in self._KIN2_PAIRS:
            assert pair in rows, (
                f"KIN-2's {pair[0]} --{pair[1]}--> {pair[2]} is approved "
                f"(06-pack-logic-decisions.md §KIN-2) and not declared — the "
                f"commitment spine is dangling again")
        for rel in reg.list_relations():
            if (rel.get("fromKind"), rel.get("predicate"),
                    rel.get("toKind")) in self._KIN2_PAIRS:
                verdict, paths = relation_write_verdict(rel)
                assert verdict == "writable", (rel, verdict)
                assert paths, rel

    def test_the_kin2_ownedBy_row_names_its_replay_path(self):
        """`core:goal --ownedBy--> Subject` is the first declaration in the
        shipped catalog whose TARGET is a Subject, so it takes the STRUCTURAL
        path (`relation_write_verdict` resolves structural over operator when
        both bind) and lands a bare `(:Object)-[:ownedBy]->(:Subject)`.

        A bare structural edge is only durable if the projection CARRIES it and
        can ADDRESS its endpoints on replay. Both are asserted here against the
        two constants the replay actually reads, so a predicate dropped from the
        carrier set — or a label dropped from the addressable set — reds THIS
        test instead of quietly making the edge live-only. The measured
        `live == rebuild` for this predicate's structural lane is
        `tests/test_9553_class4_structural_edge_records.py`.
        """
        from tortoise.pack_registry import relation_write_verdict
        from tortoise.projection.edges import (
            RECORD_ADDRESSABLE_EDGE_LABELS,
            RECORD_CARRIED_STRUCTURAL_RELS,
        )

        reg = PackRegistry(PACKS_DIR)
        reg.load_all()
        row = next(
            r for r in reg.list_relations()
            if (r.get("fromKind"), r.get("predicate")) == ("core:goal", "ownedBy"))
        verdict, paths = relation_write_verdict(row)
        assert verdict == "writable"
        assert "structural" in paths, paths
        # the two conditions replay needs, read from the replay's own constants
        assert "ownedBy" in RECORD_CARRIED_STRUCTURAL_RELS, (
            "`ownedBy` left the record-carried set — a rebuild would drop the "
            "goal→owner edge while it survives live")
        assert "Subject" in RECORD_ADDRESSABLE_EDGE_LABELS, (
            "`Subject` left the addressable set — the fold could not re-create "
            "the edge's target on replay")
        # …and the row really did resolve to the Subject half of $3.6's
        # Entity -> (Subject | Object) target, not to an Object
        assert row.get("toKind") == "Subject", row


# ── 8. the writer: the existing verb, and the MERGE it lands ───────────────

class _MergingSDK:
    """A writer double that models the ONE thing that matters: MERGE.

    ``create_edge`` lands ``MATCH … MERGE (s)-[:pred]->(t)``
    (``projection/edges.py::create_edge``), so a second call for the same
    (predicate, from, to) matches the existing edge instead of adding one. A
    double that merely counts calls could not distinguish idempotence from
    duplication, so this one keeps the MERGE key set.
    """

    def __init__(self):
        self.calls: list[tuple[str, str, str]] = []
        self.edges: set[tuple[str, str, str]] = set()

    def link_entities(self, relation, from_id, to_id, **_kw):
        self.calls.append((relation, from_id, to_id))
        self.edges.add((relation, from_id, to_id))
        return {"relation": relation}


def _two_level_registry(tmp_path):
    return _registry(
        tmp_path,
        hierarchies=[{"path": "Theme → Epic", "type": "hasPart"},
                     {"path": "Epic → Issue", "type": "hasPart"}],
        relations=[{"predicate": "hasPart", "mechanism": "IMPL",
                    "semantics": "hasPart",
                    "fromKind": "test:theme", "toKind": "test:epic"},
                   {"predicate": "hasPart", "mechanism": "IMPL",
                    "semantics": "hasPart",
                    "fromKind": "test:epic", "toKind": "test:issue"}],
    )


class TestTheWriter:
    def test_applying_the_same_pack_twice_leaves_two_edges(self, tmp_path):
        from tortoise.pack_hierarchy import apply_hierarchy_edges

        reg = _two_level_registry(tmp_path)
        candidates = [("test:theme", "t1", "test:epic", "e1"),
                      ("test:epic", "e1", "test:issue", "s1"),
                      ("test:theme", "t1", "test:issue", "s1")]  # INVENTED hop
        sdk = _MergingSDK()
        first = apply_hierarchy_edges(sdk, candidates, registry=reg)
        second = apply_hierarchy_edges(sdk, candidates, registry=reg)
        assert len(first) == len(second) == 2
        assert sdk.edges == {("hasPart", "t1", "e1"), ("hasPart", "e1", "s1")}
        assert len(sdk.calls) == 4, "each apply issues the same two declared edges"

    def test_the_existing_linking_verb_is_used_and_no_second_writer_added(self, tmp_path):
        """`link_entities` is the approved surface's one linking verb
        (`beta-sdk-surface.md` row 19). A composition-specific verb would be a
        surface change Daniel must approve first."""
        from tortoise.pack_hierarchy import apply_hierarchy_edges

        class _Refuses:
            def __getattr__(self, name):
                raise AssertionError(
                    f"the producer must not reach {name!r} — `link_entities` is "
                    f"the one approved linking verb")

            def link_entities(self, relation, from_id, to_id, **_kw):
                return {"relation": relation}

        reg = _two_level_registry(tmp_path)
        written = apply_hierarchy_edges(
            _Refuses(), [("test:theme", "t1", "test:epic", "e1")], registry=reg)
        assert [w.from_id for w in written] == ["t1"]

    def test_an_operator_pair_is_not_written_by_this_producer(self, tmp_path):
        """A structural hierarchy declaration must not mint a carrying operator:
        that is composition-as-argument, which the issue rules out."""
        from tortoise.pack_hierarchy import apply_hierarchy_edges

        reg = _registry(
            tmp_path,
            hierarchies=[{"path": "Theme → Epic", "type": "strengthens"}],
            relations=[{"predicate": "strengthens", "mechanism": "IMPL",
                        "fromKind": "test:theme", "toKind": "test:epic"}],
        )
        sdk = _MergingSDK()
        assert apply_hierarchy_edges(
            sdk, [("test:theme", "t1", "test:epic", "e1")], registry=reg) == []
        assert sdk.calls == []


# ── 9. the review's blocking findings, pinned ─────────────────────────────
# Each of these was a measured defect in an earlier draft of the producer:
# a declaration the writer refuses was reported as writable, and the fan-out
# cap silently dropped declared edges. They are regression tests, not
# hypotheticals.

class TestTheVerdictIsAboutThePredicateWeWrite:
    def test_a_row_that_is_structural_for_another_predicate_does_not_bind_this_one(self, tmp_path):
        """`relation_write_paths` answers for the ROW's predicate. Accepting its
        `structural` verdict for a DIFFERENT `type` made apply raise
        `Unknown predicate: decomposesInto` mid-apply — after committing the
        earlier edges — leaving the graph half-hierarchied with no diagnostic."""
        from tortoise.pack_hierarchy import apply_hierarchy_edges

        reg = _registry(
            tmp_path,
            hierarchies=[{"path": "Epic → Issue", "type": "decomposesInto"}],
            # The row is structural FOR `hasPart`; the edge's predicate would be
            # `decomposesInto`, which `create_edge` refuses.
            relations=[{"predicate": "hasPart", "mechanism": "IMPL",
                        "semantics": "decomposesInto",
                        "fromKind": "test:epic", "toKind": "test:issue"}],
        )
        # The row IS structural — for `hasPart`. The edge this declaration
        # describes carries `decomposesInto`, which `create_edge` refuses, so
        # the structural verdict must NOT be transplanted onto it: the entry
        # binds the operator leg (the row declares a mechanism) and this
        # producer writes nothing, rather than raising mid-apply.
        (edge,) = declared_hierarchy(reg)
        assert edge.write_path == "operator", (
            "a structural verdict earned by another predicate must not be "
            "rewritten as this predicate's structural path")
        sdk = _MergingSDK()
        assert apply_hierarchy_edges(
            sdk, [("test:epic", "e1", "test:issue", "s1")], registry=reg) == []
        assert sdk.calls == []

    def test_a_predicate_only_another_writer_accepts_is_not_reported_writable(self, tmp_path):
        """`STRUCTURAL_WRITE_PREDICATES` is the UNION of three writers;
        `link_entities` routes to exactly one of them (`create_edge`).
        `aboutPoint` is in the union and NOT in `create_edge`'s set, so an
        earlier draft reported a ✓ declared hierarchy and then raised
        `Unknown predicate: aboutPoint` at write time — a false green on the
        surface #11001 exists to make honest."""
        reg = _registry(
            tmp_path,
            hierarchies=[{"path": "Theme → Epic", "type": "aboutPoint"}],
        )
        assert declared_hierarchy(reg) == ()
        (diag,) = hierarchy_diagnostics(reg)
        assert "aboutPoint" in diag["reason"]

    def test_a_kind_no_pack_declares_is_not_resolved(self, tmp_path):
        """`Theme → other:Nope`: the namespace may or may not be installed, but
        `Nope` is nobody's kind. A verbatim `ns:kind` acceptance reported it as
        a ✓ declared hierarchy — a declaration over a kind the graph cannot
        hold."""
        reg = _registry(
            tmp_path,
            hierarchies=[{"path": "Theme → other:Nope", "type": "hasPart"}],
        )
        assert declared_hierarchy(reg) == ()
        (diag,) = hierarchy_diagnostics(reg)
        assert "other:Nope" in diag["reason"]


class TestNoWriteSideCap:
    def test_a_hub_keeps_every_declared_edge(self, tmp_path):
        """`tortoise/fanout.py` records the owner's 2026-09-23 ruling
        (`STORAGE-ARCHITECTURE.md` §11.5): the 200 cap "is **NOT a write-side
        rule … a cap that drops stored edges risks losing a real connection,
        which is the product**". An earlier draft clamped the plan to 200 and
        silently dropped 5 of 205 declared edges, and made the plan a function
        of candidate ORDER rather than of the pair set."""
        reg = _registry(
            tmp_path,
            hierarchies=[{"path": "Theme → Epic", "type": "hasPart"}],
            relations=[{"predicate": "hasPart", "mechanism": "IMPL",
                        "semantics": "hasPart",
                        "fromKind": "test:theme", "toKind": "test:epic"}],
        )
        candidates = [("test:theme", "t1", "test:epic", f"e{i}") for i in range(205)]
        plan = plan_hierarchy_edges(candidates, registry=reg)
        assert len(plan) == 205, "a write-side cap loses stored connections"
        shuffled = list(reversed(candidates))
        assert len(plan_hierarchy_edges(shuffled, registry=reg)) == 205, (
            "the plan must be a function of the pair set, not of candidate order")
