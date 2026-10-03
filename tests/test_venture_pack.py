"""Venture domain pack (issue #2725, epic #2696) — manifest, state model, and
the two pack-fit layers.

What this file pins:

1. **Manifest validity + scope** — the pack compiles clean in the whole-registry
   catalog and declares the approved kind set: Objects hold STATE, Events are
   dated occurrences that carry no state, and no relation, chain step or
   equivalence points at another pack.
2. **Kind coverage vs the domain's thing-types** — every recurring thing-type in
   the source corpus maps to a pack kind or a core kind, and the kinds that were
   deliberately excluded (CRM deal/opportunity, HR role opening) stay excluded.
   ⛔ The pilot corpus is PRIVATE customer data: this file contains no customer
   name, no portfolio-company name, no person, and no quoted corpus text. All
   fixtures are neutral and synthetic.
3. **The state/supersede shape** — a later claim must SUPERSEDE an earlier one;
   a re-mention can never resurrect a superseded Object; the current-state read
   excludes superseded Objects; and claims (Points) supersede rather than
   accumulate. This requirement is proven in two halves, deliberately: the
   pack-SHAPE half (Objects hold state, no pointKind is declared, Events are
   stateless) is DB-free and fails without the pack; the ENGINE half
   (supersession resolves to the latest version, a re-mention cannot resurrect)
   is pack-agnostic by construction — it pins the machinery the model relies on,
   and is named as such rather than pretending to be pack-specific.
4. **The two orthogonal pack-fit layers** — per-graph pack APPROVAL
   (`:PackInstall`) and per-item kind CLASSIFICATION (the kind index and the
   extraction master list) are separate mechanisms. Approval does not gate
   classification today, and classification does not consult approval records.
   Per-document domain detection is parked and asserted absent.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: I001
import yaml

from tests._live_utils import LIVE_URI_SKIP_REASON
from tortoise import extractor_v2 as v2
from tortoise.commit_ops import apply_supersessions
from tortoise.extractor_v2 import CHAINS, _PACK_TRIGGERS
from tortoise.pack_registry import CORE_KINDS, PackRegistry, default_packs_dir
from tortoise.pack_state import ensure_tenant_packs, get_tenant_packs
from tortoise.search_engine import fetch_point_epistemic_state
from tortoise.sdk import TortoiseSDK
from tortoise.value_extractor import compile_kind_index_spec


REPO_PACKS_DIR = Path(__file__).resolve().parents[1] / "packs"
VENTURE_MANIFEST = REPO_PACKS_DIR / "venture" / "manifest.yaml"
RAW_MANIFEST = yaml.safe_load(VENTURE_MANIFEST.read_text())

EXPECTED_OBJECT_KINDS = frozenset({
    "investment", "program", "asset", "fundingAgreement", "tranche",
    "condition", "actionItem",
})
EXPECTED_EVENT_KINDS = frozenset({
    "disbursement", "conditionMet", "actionItemCompleted",
})
EXPECTED_NAMESPACES = frozenset({
    "agent-ops", "dev", "marketing", "pm", "product-strategy", "venture",
})


def _server_uri_set() -> bool:
    uri = os.environ.get("TORTOISE_DB_URI") or ""
    return uri.split("://", 1)[0] in {"docker", "redis", "rediss"} and bool(
        urlparse(uri).hostname)


requires_db = pytest.mark.skipif(not _server_uri_set(), reason=LIVE_URI_SKIP_REASON)


@pytest.fixture(scope="module")
def registry() -> PackRegistry:
    r = PackRegistry(default_packs_dir())
    n = r.load_all()
    assert n == len(EXPECTED_NAMESPACES), (
        f"expected {len(EXPECTED_NAMESPACES)} packs, got {n}: {r.errors}")
    assert not r.errors, f"whole-registry compile must be clean: {r.errors}"
    return r


@pytest.fixture(scope="module")
def venture(registry: PackRegistry):
    return registry.get_pack("venture")


@pytest.fixture
def sdk(tmp_path, force_sparse_tfidf):
    """A fresh graph per test (URI-less runs use the embedded carve-out)."""
    s = TortoiseSDK(db_path=str(tmp_path / "venture.db"),
                    namespace=f"test_venture_{os.urandom(4).hex()}")
    try:
        yield s
    finally:
        s.close()


def _object_state(sdk: TortoiseSDK, name: str):
    rows = sdk._get_proj().g.query(
        "MATCH (o:Object {name:$n}) RETURN o.status, o.supersededBy",
        params={"n": name}).result_set
    return list(rows[0]) if rows else None


def _object_names(results: list[dict]) -> set[str]:
    return {r.get("content") or r.get("name") for r in results}


# ── 1. Manifest validity + scope ─────────────────────────────────────────


class TestVentureManifest:
    def test_registry_compiles_clean_and_keeps_every_pack(self, registry):
        assert set(registry.packs) == set(EXPECTED_NAMESPACES)
        assert not registry.errors

    def test_pack_identity(self, venture):
        assert venture.namespace == "venture"
        assert venture.name == "Venture"
        assert venture.version == "0.1.0"
        assert venture.tier == "free"

    def test_kind_buckets_are_exactly_the_approved_set(self, venture):
        assert set(venture.object_kinds) == EXPECTED_OBJECT_KINDS
        assert set(venture.event_kinds) == EXPECTED_EVENT_KINDS
        # No pointKind is declared: belief rides the core `statement` kind so it
        # supersedes. No documentKinds: this pack names domain things, not file
        # shapes.
        assert set(venture.point_kinds) == set()
        assert set(venture.document_kinds) == set()

    def test_objects_and_events_are_disjoint(self, venture):
        assert not (set(venture.object_kinds) & set(venture.event_kinds))

    def test_subclass_parents_are_core_pascalcase(self, venture):
        assert venture.kind_subclasses == {
            "program": "Project",
            "actionItem": "WorkItem",
            "fundingAgreement": "Object",
        }
        for parent in venture.kind_subclasses.values():
            assert parent in CORE_KINDS
            assert parent[0].isupper()

    def test_funding_agreement_does_not_claim_the_blocked_parent(self, venture):
        # `fundingAgreement ⊂ agreement` is the intended model, but the
        # subclassOf gate rejects lowercase canonical parents (#2783). Declaring
        # it would make the whole pack fail validation and be silently dropped,
        # so the intent is recorded as a comment instead. Flip on #2783.
        assert venture.kind_subclasses["fundingAgreement"] != "agreement"
        assert "agreement" in CORE_KINDS

    def test_no_cross_pack_references(self, venture):
        """Requirement: no cross-pack referencing, in any direction."""
        own_ns = {f"venture:{k}" for k in
                  (set(venture.object_kinds) | set(venture.event_kinds)
                   | set(venture.point_kinds) | set(venture.document_kinds))}
        own_bare = {k.split(":", 1)[1] for k in own_ns}
        allowed = own_ns | set(CORE_KINDS)

        assert venture.kind_equivalences == {}, "equivalentTo is cross-pack by definition"
        for rel in venture.relations:
            assert rel["fromKind"] in allowed, rel
            assert rel["toKind"] in allowed, rel
        for chain in venture.chains:
            for step in chain["steps"]:
                assert step in own_bare or step in CORE_KINDS, (chain["id"], step)

    def test_every_declared_kind_has_a_kind_def(self, venture):
        declared = (set(venture.object_kinds) | set(venture.event_kinds))
        assert declared <= set(venture.kind_defs)

    def test_relations_are_declared_and_resolvable(self, registry, venture):
        declared = {r["predicate"] for r in venture.relations}
        assert declared == {
            "funds", "gatedBy", "protects", "producedBy", "assignedTo",
            "partOf", "holds",
        }
        listed = {r["predicate"] for r in registry.list_relations()
                  if r.get("pack") == "venture"}
        assert listed == declared
        for rel in venture.relations:
            assert rel["mechanism"] in ("IMPL", "NAND")

    def test_relation_semantics_map_onto_the_writable_predicate_set(self, venture):
        """#2766: declared relations have no write path today, so `semantics`
        carries the mapping onto the writable structural vocabulary wherever one
        EXISTS — and stays equal to the predicate where none does yet (honest,
        not invented). The writable set is IMPORTED, never restated, so a change
        to the runtime allowlist is visible here."""
        from tortoise.projection.edges import _VALID_EDGE_PREDICATES

        mapped = {
            "gatedBy": "dependsOn",
            "partOf": "hasPart",
            "producedBy": "produces",
            "protects": "related",
            "assignedTo": "managedBy",
        }
        by_pred = {r["predicate"]: r for r in venture.relations}
        assert set(by_pred) == set(mapped) | {"funds", "holds"}
        for pred, sem in mapped.items():
            assert by_pred[pred]["semantics"] == sem
            assert sem in _VALID_EDGE_PREDICATES, (pred, sem)
        # No writable analogue exists for these two yet: they self-map rather
        # than claim a mapping that would be a lie.
        for pred in ("funds", "holds"):
            assert by_pred[pred]["semantics"] == pred
            assert pred not in _VALID_EDGE_PREDICATES

    def test_extraction_config_is_declared(self, venture):
        assert venture.extraction["active"] is True
        # Empty = active for every source type. The meeting source kinds are not
        # in the pack validator's allowlist until #2726 (PR #2747) lands, and a
        # pack that declares an unregistered source type is dropped.
        assert venture.extraction["sourceTypes"] == []
        assert venture.is_active_for("conversation") is True
        # Exactly two kinds carry a bounded classifier retry — the two the
        # evidence justifies (see test_enforcement.py's index-reachable retry
        # pin); every other kind inherits the pack default. Per-kind retry is
        # declared on the kindDefs, so the `enforcement.kinds` map stays empty
        # rather than restating the same choice on a second rung.
        assert venture.enforcement_for("asset") == "retry"
        assert venture.enforcement_for("condition") == "retry"
        for kind in ("investment", "program", "fundingAgreement", "tranche",
                     "actionItem", "disbursement", "conditionMet",
                     "actionItemCompleted"):
            assert venture.enforcement_for(kind) == "warn", kind
        assert venture.extraction["enforcement"]["kinds"] == {}

    def test_memory_granularity_declares_durable_and_ephemeral(self):
        # Read from the raw manifest: memory_granularity is an `ontology:` key
        # (a top-level one is silently ignored).
        g = RAW_MANIFEST["ontology"]["memory_granularity"]
        assert "Durable" in g and "Ephemeral" in g

    def test_pack_declares_no_fold_or_value_surface(self):
        """The per-kind status fold is #2729 (generalised by #2792) and the
        per-kind value-field surface is #2818 (behind #2782). This pack declares
        neither: inventing one here is how a second, divergent mechanism starts."""
        forbidden = {"pipeline", "pipelines", "folds", "statusFolds", "statuses",
                     "values", "valueFields", "domainDetection"}
        assert not (set(RAW_MANIFEST["ontology"]) & forbidden)
        for kd in RAW_MANIFEST["ontology"]["kindDefs"].values():
            assert not (set(kd) & forbidden)


# ── 2. Kind coverage vs the domain's thing-types ─────────────────────────


# Recurring thing-types the source corpus yields → the kind that covers each.
# The right-hand side must resolve to a kind of this pack or a core kind.
CORPUS_THING_TYPE_COVERAGE = {
    "the fund's stake in a backed company": "venture:investment",
    "the backed company / a counterparty (an actor)": "Subject",
    "a multi-milestone initiative under a stake": "venture:program",
    "a produced product / technology / dataset": "venture:asset",
    "a patent family or other protected IP": "venture:asset",
    "a grant / term of award / round / loan": "venture:fundingAgreement",
    "a scheduled parcel of committed funds": "venture:tranche",
    "a gating requirement on a parcel (e.g. a spend threshold)": "venture:condition",
    "a dated release of funds": "venture:disbursement",
    "the dated satisfaction of a gate": "venture:conditionMet",
    "an owned, dated commitment from a meeting": "venture:actionItem",
    "the dated closing of a commitment": "venture:actionItemCompleted",
    "a person (owner, board member, candidate)": "Subject",
    "a claim or opinion about any of the above": "statement",
    "'milestone' in its licensing-payment sense": "venture:tranche",
}

# Thing-types deliberately NOT in this pack, with the domain that owns them.
EXCLUDED_THING_TYPES = {
    # CRM: a stage pipeline over counterparties is CRM vocabulary.
    "opportunity": "crm",
    "deal": "crm",
    "pipeline": "crm",
    # HR: hiring needs are carried as action items until an HR pack exists.
    "roleOpening": "hris",
    "candidate": "hris",
    "interview": "hris",
    # Collides with pm:milestone + a legacy point kind + a miner event kind.
    "milestone": "pm",
}


class TestKindCoverage:
    @staticmethod
    def _known_kinds(registry: PackRegistry) -> set[str]:
        # list_all_kinds() is a dict keyed by bucket (objectKinds, eventKinds, …)
        # with namespaced values.
        return set().union(*registry.list_all_kinds().values()) | set(CORE_KINDS)

    def test_every_corpus_thing_type_resolves_to_a_known_kind(self, registry):
        known = self._known_kinds(registry)
        unmapped = {tt: k for tt, k in CORPUS_THING_TYPE_COVERAGE.items()
                    if k not in known}
        assert not unmapped, f"thing-types without a real kind: {unmapped}"

    def test_coverage_uses_the_pack_not_only_core(self, venture):
        targets = set(CORPUS_THING_TYPE_COVERAGE.values())
        pack_targets = {t for t in targets if t.startswith("venture:")}
        # The pack must be doing real work: ≥ 8 of the thing-types resolve to a
        # pack kind rather than falling back to a core kind.
        assert len(pack_targets) >= 8
        declared = set(venture.object_kinds) | set(venture.event_kinds)
        for t in pack_targets:
            assert t.split(":", 1)[1] in declared

    def test_excluded_thing_types_are_not_declared(self, venture):
        declared = (set(venture.object_kinds) | set(venture.event_kinds)
                    | set(venture.point_kinds) | set(venture.document_kinds))
        for kind in EXCLUDED_THING_TYPES:
            assert kind not in declared, (
                f"'{kind}' belongs to another domain and must not be declared here")

    def test_full_ontology_layers_are_representable(self, venture, registry):
        """Requirement: the model uses all four layers — Subjects, Objects
        (state), Points, Events — and Events carry no state."""
        # Subjects: core; the pack attaches to them rather than redeclaring them.
        assert {r["toKind"] for r in venture.relations} & {"Subject"}
        # Objects: the state carriers.
        assert venture.object_kinds
        # Events: the dated layer.
        assert venture.event_kinds
        # Points: claims ride the core claim kind — this pack declares none, so a
        # claim about a venture Object is a `statement` that supersedes, not a
        # pack-owned point kind that accumulates.
        assert venture.point_kinds == []
        assert "statement" in CORE_KINDS
        assert "statement" in self._known_kinds(registry)


# ── 3. The state / supersede shape ───────────────────────────────────────


class TestStateSupersedeShape:
    def test_every_object_kind_declares_a_lifecycle(self, venture):
        # These are PROSE assertions by design: the machine-readable per-kind
        # lifecycle surface belongs to #2729 (generalised by #2792), so until it
        # lands the lifecycle is declared in the kindDef description (which is
        # extractor prompt material) and pinned here. When #2729 lands, move
        # these to the declared surface and delete the prose coupling.
        for kind in venture.object_kinds:
            desc = venture.kind_defs[kind]["description"]
            assert "Lifecycle:" in desc, kind
            assert "→" in desc, kind

    def test_no_event_kind_declares_state(self, venture):
        for kind in venture.event_kinds:
            desc = venture.kind_defs[kind]["description"]
            assert "Lifecycle:" not in desc, kind
            assert "carries no state" in desc or "no state" in desc, kind

    def test_store_as_puts_state_on_entities_and_facts_on_events(self, venture):
        for kind in venture.object_kinds:
            assert venture.store_as(kind) == "entity", kind
        for kind in venture.event_kinds:
            assert venture.store_as(kind) == "event", kind

    def test_money_state_is_on_the_tranche_object_not_the_event(self, venture):
        tranche = venture.kind_defs["tranche"]["description"]
        disb = venture.kind_defs["disbursement"]["description"]
        assert "conditions-met" in tranche and "released" in tranche
        assert "state carrier" in tranche
        assert "Event" in disb and "no state" in disb

    @requires_db
    def test_object_supersession_leaves_only_the_latest_state_live(self, sdk):
        """A later STATE VERSION of one logical entity supersedes the earlier one,
        so "what is the release position?" has exactly one answer.

        Shape the engine actually implements: Objects are name-keyed, so two
        versions of one thing cannot share a name — a state change is a NEW
        Object linked to the old one by supersession. This test is
        pack-agnostic by construction (it pins the engine contract the pack's
        model rides); the pack-shape half of the requirement is pinned by the
        DB-free tests above.
        """
        sdk.create_object("Tranche T2 — pending", objectKind="venture:tranche",
                          status="pending")
        sdk.create_object("Tranche T2 — released", objectKind="venture:tranche",
                          status="released")

        warns: list[str] = []
        applied = apply_supersessions(
            sdk._get_proj(), sdk,
            [{"superseded": "Tranche T2 — pending",
              "supersedes_by": "Tranche T2 — released",
              "evidence": "the parcel was released"}],
            session_id="sess_tranche", warn=warns.append)
        assert applied == 1, warns

        assert _object_state(sdk, "Tranche T2 — pending") == [
            "superseded", "Tranche T2 — released"]
        assert _object_state(sdk, "Tranche T2 — released") == ["released", None]

        # The CURRENT-STATE read carries only the latest claim.
        live = sdk.recall_state(kind="venture:tranche", object_centric=True,
                                limit=10)
        assert "Tranche T2 — released" in _object_names(live)
        assert "Tranche T2 — pending" not in _object_names(live)
        # …and history is still reachable explicitly.
        hist = sdk.recall_state(kind="venture:tranche", object_centric=True,
                                limit=10, include_superseded=True)
        assert "Tranche T2 — pending" in _object_names(hist)

    @requires_db
    def test_a_re_mention_cannot_resurrect_a_superseded_object(self, sdk):
        """The clobber guard: append-without-supersede must not be able to make
        a superseded state current again."""
        sdk.create_object("Condition C1", objectKind="venture:condition",
                          status="unmet")
        sdk.create_object("Condition C2", objectKind="venture:condition",
                          status="met")
        apply_supersessions(
            sdk._get_proj(), sdk,
            [{"superseded": "Condition C1", "supersedes_by": "Condition C2",
              "evidence": "the spend threshold was met"}],
            session_id="sess_cond", warn=lambda _m: None)
        assert _object_state(sdk, "Condition C1") == ["superseded", "Condition C2"]

        # A later meeting re-mentions the old condition.
        sdk.create_entity("object", "Condition C1",
                          objectKind="venture:condition", status="unmet")
        assert _object_state(sdk, "Condition C1")[0] == "superseded"

    @requires_db
    def test_a_state_claim_supersedes_rather_than_accumulates(self, sdk):
        """The Point half of the model: a later claim supersedes the earlier one,
        so the graph never holds two competing current answers. The pack declares
        NO pointKind by design — a claim about a venture Object is a core
        `statement` — so this pins the claim layer, not a pack kind."""
        old = sdk.create_point("statement", "Tranche T2 is still pending")
        new = sdk.create_point("statement", "Tranche T2 was released")
        sdk.supersede_point(old["id"], new["id"])

        state = fetch_point_epistemic_state(sdk._get_proj().g, [old["id"], new["id"]])
        assert state[old["id"]]["status"] == "superseded"
        assert state[old["id"]]["superseded_by"]["id"] == new["id"]
        # The successor is the current claim — it is not itself superseded.
        assert state[new["id"]]["status"] != "superseded"


# ── 4. The two orthogonal pack-fit layers ────────────────────────────────


class TestPackFitLayers:
    """Layer (a) APPROVAL: which packs a graph allows — per graph.
    Layer (b) CLASSIFICATION: which approved kind an item gets — per item.

    The two must not be conflated: approval is graph-scoped and lives in
    `:PackInstall`; classification is a catalog-scoped kind index. Per-document
    domain detection (a third mechanism) is parked and asserted absent.
    """

    def test_classification_index_is_catalog_scoped(self, registry):
        spec = compile_kind_index_spec()
        venture_kinds = {f"venture:{k}" for k in
                         set(registry.get_pack("venture").object_kinds)
                         | set(registry.get_pack("venture").event_kinds)}
        missing = venture_kinds - set(spec)
        assert not missing, f"classifier cannot see: {missing}"
        assert spec["venture:tranche"]["section"] == "objects"

    def test_classification_does_not_take_a_graph_or_approval_input(self):
        """Structural proof that classification is not derived from per-graph
        approval: the compile entry point takes no sdk/graph argument."""
        import inspect
        params = set(inspect.signature(compile_kind_index_spec).parameters)
        assert not (params & {"sdk", "graph", "graph_name", "namespace", "team"})

    @requires_db
    def test_approval_is_per_graph(self, tmp_path, force_sparse_tfidf):
        a = TortoiseSDK(db_path=str(tmp_path / "a.db"),
                        namespace=f"test_venture_appr_a_{os.urandom(4).hex()}")
        b = TortoiseSDK(db_path=str(tmp_path / "b.db"),
                        namespace=f"test_venture_appr_b_{os.urandom(4).hex()}")
        try:
            ensure_tenant_packs(a, starter=["venture"])
            ensure_tenant_packs(b, starter=["dev"])

            assert [p["namespace"] for p in get_tenant_packs(a)] == ["venture"]
            assert [p["namespace"] for p in get_tenant_packs(b)] == ["dev"]
        finally:
            a.close()
            b.close()

    @requires_db
    def test_classification_is_not_gated_by_this_graphs_approval(
            self, sdk, force_sparse_tfidf):
        """The two layers stated as the coupling that ACTUALLY exists today.

        Approval is per-graph (`:PackInstall`); classification is catalog-scoped.
        A graph that never approved the venture pack therefore still classifies
        and accepts its kinds — this is the interim behaviour of #2714/#2728,
        pinned deliberately instead of left incidental. When per-graph approval
        starts gating classification, this assertion MUST flip — which is the
        point: the change becomes visible here rather than silently altering
        behaviour. (An earlier version of this test asserted that installing the
        pack leaves the index unchanged; that cannot fail, since the compile
        takes no graph input at all.)
        """
        installs = [p["namespace"] for p in get_tenant_packs(sdk)]
        assert "venture" not in installs, installs

        assert "venture:tranche" in compile_kind_index_spec()

        # …and the write path accepts the kind with no approval on this graph.
        node = sdk.create_object("Tranche T1", objectKind="venture:tranche",
                                 status="pending")
        assert node["objectKind"] == "venture:tranche"

    def test_no_per_document_domain_detection(self):
        """Requirement: per-document 'domain detection' is parked. Classification
        picks a kind per item; it must not pick a pack per document."""
        for attr in ("detect_domain", "domain_detect", "pack_selector",
                     "select_pack_for_document"):
            assert not hasattr(v2, attr), attr
        assert not (set(RAW_MANIFEST["ontology"])
                    & {"domainDetection", "domains", "detector"})

    def test_domain_pack_exposure_is_keyword_gated_not_document_gated(self):
        """`_PACK_TRIGGERS` is the compact-mode story-keyword heuristic — it is
        per-story token selection, not per-document domain detection, and the
        venture namespace must have an entry so it is gateable like the
        starters."""
        assert "venture:" in _PACK_TRIGGERS
        assert all(isinstance(t, str) for t in _PACK_TRIGGERS["venture:"])
        selected = v2._select_pack_kinds(
            "the board discussed the grant and its second tranche",
            {"venture:tranche": "x", "dev:code": "y"})
        assert "venture:tranche" in selected
        assert "dev:code" not in selected


# ── 5. Wiring guards (these fail against the pre-change tree) ────────────


class TestPackWiringGuards:
    def test_every_shipped_pack_namespace_reaches_the_extraction_master_list(
            self, registry):
        """A pack whose namespace never reaches ``pack_kinds`` compiles in the
        registry but is INVISIBLE to the extractor — the pack ships inert. This
        is the guard for that whole class of bug, not just venture.

        The pack-kind set is derived from the compiled value brief (#5165), so
        the default (ungated catalog-union) path must carry every shipped
        namespace; a namespace absent here can never be offered by the prompt.
        """
        master_ns = {k.split(":", 1)[0]
                     for k in v2.build_master_list()["pack_kinds"]}
        missing = [ns for ns in registry.packs if ns not in master_ns]
        assert not missing, (
            f"packs invisible to the extractor (absent from pack_kinds): {missing}")

    def test_venture_kinds_are_in_the_master_list_and_its_forms(self):
        master = v2.build_master_list()
        pack_kinds = set(master["pack_kinds"])
        expected = {f"venture:{k}" for k in
                    EXPECTED_OBJECT_KINDS | EXPECTED_EVENT_KINDS}
        assert expected <= pack_kinds, sorted(expected - pack_kinds)
        forms = v2.master_kind_forms(master)
        assert expected <= forms

    def test_declared_chains_agree_with_the_canonical_chain_table(self, venture):
        """Packs declare their chains in the manifest AND (for prompt guidance)
        in the canonical `CHAINS` table — the dev/marketing/product-strategy
        pattern. The two must not drift."""
        assert venture.chains
        for chain in venture.chains:
            cid = chain["id"]
            assert cid in CHAINS, cid
            assert CHAINS[cid]["path"] == chain["steps"], cid
            assert CHAINS[cid]["note"]

    def test_venture_chains_do_not_trigger_completeness_notes(self):
        """A stake with no programme yet, a tranche whose release is not
        discussed in the same meeting, and an action item that does not close in
        the meeting that opened it are all NORMAL. The completeness contract
        ('first emitted step requires the next') would fire on ordinary captures,
        so the venture chains are canonical and exempt — this test pins that."""
        notes = v2.validate_chain_completeness({
            "entities": [{"kind": "venture:investment"},
                         {"kind": "venture:tranche"},
                         {"kind": "venture:actionItem"}],
        })
        assert [n for n in notes if n["chain"].startswith("venture")] == []
