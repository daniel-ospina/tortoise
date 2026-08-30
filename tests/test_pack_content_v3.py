"""Tests for pack content v3 (issue #950, epic #909 slice 4b).

Loads the REAL packs from the repo and verifies the converted manifests:
  - product-strategy: full §4 worked example — productDelivery chain
    (all 6 steps resolve), `architecture` expansion (subclassOf Document),
    `requirement` ≡ dev:requirement (bidirectional), 2 extractable chain
    edges, extraction activation + enforcement defaults (useCase → retry).
  - dev: epic→issue→code chain + kindDefs (requirement ≡ ps:requirement,
    architectureDoc link note).
  - marketing: campaign→content→channel chain (real kind names).
  - pm: kindDefs enrichment for issue/sprint/card.
  - All 5 packs compile with zero validation errors (whole-registry compile,
    R-16) and versions bumped to 0.2.0.
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

    def test_all_four_packs_load_with_zero_errors(self, registry):
        assert set(registry.packs) == {
            "agent-ops", "dev", "marketing", "product-strategy", "pm",
        }
        assert not registry.errors

    def test_all_versions_bumped_to_0_2_0(self, registry):
        # the four legacy packs were bumped to 0.2.0; agent-ops (#1933) is
        # new at 0.1.0
        for ns in ("dev", "marketing", "product-strategy", "pm"):
            assert registry.get_pack(ns).version == "0.2.0", ns


class TestProductDeliveryChain:
    """product-strategy: the owner's chain (research-r6 §4, worked example).

    Issue #405 decision (2026-08-15): jobToBeDone is step-0 — the canonical
    chain matches check_structure/skill (JTBD → useCase → feature →
    userJourney → workflow → requirement → architecture)."""

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
        assert ps.kind_subclasses == {
            "feature": "WorkItem",
            "requirement": "WorkItem",
            "architecture": "Document",
        }


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
    """dev: epic→issue→code chain + kindDefs (requirement, architectureDoc link)."""

    def test_epic_to_code_chain(self, registry):
        dev = registry.get_pack("dev")
        assert [c["id"] for c in dev.chains] == ["epicToCode"]
        assert dev.chains[0]["steps"] == ["epic", "issue", "code"]
        assert dev.enforcement_for_chain("epicToCode") == "warn"

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
