"""Tests for pack content v3 (issue #950, epic #909 slice 4b).

Loads the REAL packs from the repo and verifies the converted manifests:
  - product-strategy: full §4 worked example — productDelivery chain
    (JTBD → useCase → feature → userJourney → workflow → requirement →
    architecture — 7 steps, #405 step-0; the problem family anchors it via
    the feature → customerProblem relation), `architecture` expansion
    (subclassOf Document), customerProblem subclassOf Problem, `requirement`
    ≡ dev:requirement (bidirectional), 2 extractable chain edges, extraction
    activation + enforcement defaults (useCase → retry).
  - dev: epicToCode chain (epic → issue → code) + kindDefs (requirement ≡
    ps:requirement, architectureDoc link note); problem-family kinds
    (bug/incident subclassOf Problem) + typed relations (causes/fixes/
    addresses/hasPart) + enforcement (incident → retry).
  - marketing: campaign→content→channel chain (real kind names).
  - pm: kindDefs enrichment for issue/sprint/card.
  - All 5 packs compile with zero validation errors (whole-registry compile,
    R-16); dev + product-strategy at 0.3.0 (problem-family expansion),
    marketing/pm at 0.2.0, agent-ops new at 0.1.0.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: I001
from tortoise.pack_registry import PackRegistry


REPO_PACKS_DIR = Path(__file__).resolve().parents[1] / "packs"


@pytest.fixture(scope="module")
def registry():
    """The real repo registry — all 5 packs (agent-ops joined dev, marketing,
    product-strategy, project-management), converted to manifest v3."""
    r = PackRegistry(REPO_PACKS_DIR)
    n = r.load_all()
    assert n == 5, f"expected 5 packs to load, got {n}"
    assert not r.errors, f"whole-registry compile must be clean: {r.errors}"
    return r


class TestWholeRegistryCompile:
    """R-16: every pack PR must pass the whole-registry compile."""

    def test_all_five_packs_load_with_zero_errors(self, registry):
        assert set(registry.packs) == {
            "agent-ops", "dev", "marketing", "product-strategy", "pm",
        }
        assert not registry.errors

    def test_versions_reflect_pack_releases(self, registry):
        # dev + product-strategy bumped to 0.3.0 by the problem-family pack
        # expansion (theme/pullRequest/bug/incident/customerProblem kinds +
        # relations/eventKinds/enforcement — landed from the #2238 dirty-hub
        # salvage); marketing + pm untouched at 0.2.0; agent-ops (#1933) is
        # new at 0.1.0
        for ns in ("dev", "product-strategy"):
            assert registry.get_pack(ns).version == "0.3.0", ns
        for ns in ("marketing", "pm"):
            assert registry.get_pack(ns).version == "0.2.0", ns
        assert registry.get_pack("agent-ops").version == "0.1.0"


class TestProductDeliveryChain:
    """product-strategy: the owner's chain (research-r6 §4, worked example).

    #405 (2026-08-15) made jobToBeDone step-0 of productDelivery; that chain
    stays point-state-shaped (7 steps). The problem family anchors the chain
    via relations instead (feature → customerProblem, cross-pack manifests
    dev:incident → customerProblem) — an OBJECT-kind step (customerProblem)
    could never be a payload pointKind, so it is declared relationally, not
    as a chain position."""

    def test_chain_parsed_with_all_seven_steps(self, registry):
        ps = registry.get_pack("product-strategy")
        assert [c["id"] for c in ps.chains] == ["productDelivery"]
        chain = ps.chains[0]
        assert chain["steps"] == [
            "jobToBeDone", "useCase", "feature", "userJourney",
            "workflow", "requirement", "architecture",
        ]
        # Chain resolves post-load (zero errors == every step resolved,
        # incl. bare `workflow` → core kind and the new kinds).
        assert not registry.errors

    def test_chain_enforcement_warn(self, registry):
        ps = registry.get_pack("product-strategy")
        assert ps.enforcement_for_chain("productDelivery") == "warn"

    def test_new_kinds_declared(self, registry):
        ps = registry.get_pack("product-strategy")
        assert "requirement" in ps.object_kinds
        assert "architecture" in ps.object_kinds
        assert "customerProblem" in ps.object_kinds
        assert ps.kind_subclasses == {
            "feature": "WorkItem",
            "requirement": "WorkItem",
            "architecture": "Document",
            "customerProblem": "Problem",
        }
        assert "customerProblem" in ps.kind_defs


class TestArchitectureExpansion:
    """`architecture: Document` must expand (slice 4a fix, R6 §6.2a)."""

    def test_architecture_expands_from_document(self, registry):
        expanded = registry.expand_kind("Document")
        assert "Document" in expanded
        assert "product-strategy:architecture" in expanded

    def test_workitem_expansion_includes_ps_kinds(self, registry):
        """Issue target: expand_kind('WorkItem') includes ps:feature/requirement."""
        expanded = registry.expand_kind("WorkItem")
        assert "product-strategy:feature" in expanded
        assert "product-strategy:requirement" in expanded


class TestRequirementEquivalence:
    """ps:requirement ≡ dev:requirement — bidirectional (DE2E-9)."""

    def test_ps_side(self, registry):
        expanded = registry.expand_kind("product-strategy:requirement")
        assert "dev:requirement" in expanded

    def test_dev_side(self, registry):
        expanded = registry.expand_kind("dev:requirement")
        assert "product-strategy:requirement" in expanded


class TestExtractableRelations:
    """The two chain edges opt in; the existing 4 relations stay non-extractable."""

    def test_exactly_two_extractable(self, registry):
        ps = registry.get_pack("product-strategy")
        extractable = [r for r in ps.relations if ps.relation_is_extractable(r)]
        assert len(extractable) == 2
        assert [(r["predicate"], r["fromKind"], r["toKind"]) for r in extractable] == [
            ("addresses", "product-strategy:useCase", "product-strategy:feature"),
            ("addresses", "product-strategy:requirement", "product-strategy:architecture"),
        ]

    def test_existing_relations_not_extractable(self, registry):
        ps = registry.get_pack("product-strategy")
        for r in ps.relations:
            if r["predicate"] in ("contains", "targets", "competesWith"):
                assert not ps.relation_is_extractable(r)

    def test_list_relations_marks_extractable(self, registry):
        relations = registry.list_relations()
        ps_rels = [r for r in relations if r["pack"] == "product-strategy"]
        extractable = [r for r in ps_rels if r.get("extractable")]
        assert len(extractable) == 2


class TestProductStrategyEnforcement:
    """Extraction config: default warn; useCase/userJourney retry (confusable group)."""

    def test_use_case_retry(self, registry):
        ps = registry.get_pack("product-strategy")
        assert ps.enforcement_for("useCase") == "retry"

    def test_user_journey_retry(self, registry):
        ps = registry.get_pack("product-strategy")
        assert ps.enforcement_for("userJourney") == "retry"

    def test_default_warn_for_other_kinds(self, registry):
        ps = registry.get_pack("product-strategy")
        assert ps.enforcement_for("product") == "warn"
        assert ps.enforcement_for("feature") == "warn"

    def test_activation(self, registry):
        ps = registry.get_pack("product-strategy")
        assert ps.extraction["active"] is True
        assert ps.extraction["sourceTypes"] == ["conversation", "document"]
        assert ps.is_active_for("conversation")
        assert ps.is_active_for("document")
        assert not ps.is_active_for("github_issue")


class TestDevManifest:
    """dev: epicToCode chain + kindDefs (requirement, architectureDoc link),
    problem-family kinds/relations/enforcement/eventKinds."""

    def test_epic_to_code_chain(self, registry):
        dev = registry.get_pack("dev")
        assert [c["id"] for c in dev.chains] == ["epicToCode"]
        assert dev.chains[0]["steps"] == ["epic", "issue", "code"]
        assert dev.enforcement_for_chain("epicToCode") == "warn"

    def test_problem_family_subclass_wiring(self, registry):
        # dev-side half of the Problem canonicalization (pack_registry.py
        # CANONICAL_OBJECT_KINDS += Problem): re-parenting bug/incident off
        # Problem must fail the suite, not just keep the compile clean.
        dev = registry.get_pack("dev")
        assert dev.kind_subclasses == {
            "epic": "Project", "issue": "WorkItem",
            "pullRequest": "WorkItem", "bug": "Problem",
            "incident": "Problem",
        }
        # consumer-facing contract: the Problem root expands to the family
        for kind in ("dev:bug", "dev:incident", "product-strategy:customerProblem"):
            assert kind in registry.expand_kind("Problem"), kind
        assert "theme" in dev.object_kinds
        assert "pullRequest" in dev.object_kinds
        assert "bug" in dev.object_kinds
        assert "incident" in dev.object_kinds

    def test_problem_family_relations(self, registry):
        # Compile validates only fromKind/toKind resolution — predicate,
        # direction and mechanism are otherwise free — so the typed edges
        # of the problem-family delta need a content pin (subset form so
        # future benign dev relations don't churn this test).
        dev = registry.get_pack("dev")
        rels = {(r["predicate"], r["fromKind"], r["toKind"], r["mechanism"])
                for r in dev.relations}
        assert {
            ("hasPart", "dev:theme", "dev:epic", "IMPL"),
            ("causes", "dev:bug", "dev:incident", "IMPL"),
            ("fixes", "dev:pullRequest", "dev:bug", "IMPL"),
            ("addresses", "dev:issue", "dev:incident", "IMPL"),
            ("addresses", "dev:pullRequest", "dev:issue", "IMPL"),
        } <= rels

    def test_problem_family_relations_ps(self, registry):
        # the product-strategy half of the typed-edge delta, incl. the
        # cross-pack bridge (a dev incident manifests a customer problem)
        ps = registry.get_pack("product-strategy")
        rels = {(r["predicate"], r["fromKind"], r["toKind"], r["mechanism"])
                for r in ps.relations}
        assert {
            ("addresses", "product-strategy:feature",
             "product-strategy:customerProblem", "IMPL"),
            ("manifests", "dev:incident",
             "product-strategy:customerProblem", "IMPL"),
        } <= rels

    def test_problem_family_point_kinds(self, registry):
        # the delta moved `bug` out of pointKinds (now an object kind, a
        # Problem subclass) and added `risk` as a point kind — the
        # potential form of the problem family. store_as inference keys
        # off point-kind membership first (risk → claim), so the category
        # split is load-bearing for extraction typing.
        dev = registry.get_pack("dev")
        assert "risk" in dev.point_kinds
        assert "bug" not in dev.point_kinds
        assert dev.store_as("risk") == "claim"
        assert dev.store_as("bug") == "entity"

    def test_problem_family_kinddefs(self, registry):
        dev = registry.get_pack("dev")
        for kind in ("theme", "pullRequest", "bug", "incident", "risk",
                     "runbook"):
            assert kind in dev.kind_defs, kind

    def test_problem_family_memory_granularity(self):
        # dev's memory_granularity was rewritten to the problem-family
        # framing; it feeds value_extractor.compile_value_brief (S1/S2/S4
        # extraction prompts) and is dropped by the PackManifest dataclass,
        # so pin it at the raw-manifest text level.
        raw = (Path(__file__).resolve().parents[1] / "packs" / "dev" /
               "manifest.yaml").read_text()
        assert "Durable: problem-family reasoning" in raw
        assert "incident root causes" in raw
        assert "chosen vs rejected mitigations" in raw
        assert "Ephemeral: issue/PR numbers" in raw

    def test_problem_family_enforcement(self, registry):
        dev = registry.get_pack("dev")
        assert dev.enforcement_for("incident") == "retry"
        # risk stays a point kind → FIX P keeps point kinds out of the
        # kind index → `risk: retry` would be dead config → default warn.
        assert dev.enforcement_for("risk") == "warn"
        # default-warn probes: unrelated kinds are not swept into retry
        assert dev.enforcement_for("bug") == "warn"
        assert dev.enforcement_for("epic") == "warn"

    def test_problem_family_event_kinds(self, registry):
        dev = registry.get_pack("dev")
        for kind in ("prOpened", "prReviewed", "prMerged", "prClosed",
                     "incidentDeclared", "incidentResolved"):
            assert kind in dev.event_kinds, kind

    def test_kinddefs_present(self, registry):
        dev = registry.get_pack("dev")
        for kind in ("epic", "issue", "code"):
            assert kind in dev.kind_defs

    def test_requirement_kinddef_links_ps(self, registry):
        dev = registry.get_pack("dev")
        assert "product-strategy:requirement" in dev.kind_defs["requirement"]["description"]

    def test_architecture_doc_link_note(self, registry):
        dev = registry.get_pack("dev")
        desc = dev.kind_defs["architectureDoc"]["description"]
        assert "product-strategy:architecture" in desc
        # nearMisses target `architecture` resolves cross-pack (single ns)
        assert not registry.errors


class TestMarketingManifest:
    """marketing: campaign→content→channel chain using the real kind names."""

    def test_campaign_to_channel_chain(self, registry):
        mkt = registry.get_pack("marketing")
        assert [c["id"] for c in mkt.chains] == ["campaignToChannel"]
        assert mkt.chains[0]["steps"] == ["campaign", "content", "channel"]
        # All steps are real declared marketing kinds
        for step in mkt.chains[0]["steps"]:
            assert step in (
                mkt.object_kinds + mkt.point_kinds
                + mkt.document_kinds + mkt.event_kinds
            )

    def test_kinddefs_present(self, registry):
        mkt = registry.get_pack("marketing")
        for kind in ("campaign", "content", "channel"):
            assert kind in mkt.kind_defs


class TestProjectManagementManifest:
    """pm: kindDefs enrichment for issue/sprint/card (eventKinds enrichment)."""

    def test_kinddefs_for_issue_sprint_card(self, registry):
        pm = registry.get_pack("pm")
        for kind in ("issue", "sprint", "card"):
            assert kind in pm.kind_defs
            assert pm.kind_defs[kind]["description"]

    def test_existing_ontology_intact(self, registry):
        pm = registry.get_pack("pm")
        assert "cardCreated" in pm.event_kinds
        assert "sprintCompleted" in pm.event_kinds
        assert len(pm.relations) == 4  # old-format relations preserved
