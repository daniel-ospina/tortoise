"""Tests for pack loading, kind expansion, and relation declarations.

Also covers manifest v3 (epic #909, research-r6 §3): kindDefs, chains,
relations[].extractable, extraction config, per-pack load isolation, and
the core-entity expansion fix (§6.2a).
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from tortoise.pack_registry import PackManifest, PackRegistry


REPO_PACKS_DIR = Path(__file__).resolve().parents[1] / "packs"


# ── Manifest v3 fixtures (research-r6 §3/§4, epic #909) ───────────────────

V3_DEV = {
    "namespace": "dev", "name": "Development",
    "ontology": {
        "extends": "core",
        "objectKinds": ["issue", "code"],
        "pointKinds": ["requirement"],
        "documentKinds": ["architectureDoc"],
    },
}

V3_PS = {
    "namespace": "product-strategy", "name": "Product Strategy",
    "version": "0.2.0", "tier": "free",
    "ontology": {
        "extends": "core",
        "objectKinds": ["product", "feature", "customerSegment", "market",
                         "requirement", "architecture"],
        "subclassOf": {"feature": "WorkItem", "requirement": "WorkItem",
                        "architecture": "Document"},
        "equivalentTo": {"requirement": ["dev:requirement"]},
        "pointKinds": ["jobToBeDone", "useCase", "userJourney", "valueProposition"],
        "eventKinds": ["release"],
        "documentKinds": ["competitiveAnalysis", "marketResearch"],
        "kindDefs": {
            "product": {"description": "A marketable offering",
                         "nearMisses": ["feature"]},
            "market": {"description": "The competitive arena",
                        "storeAs": "tag"},  # explicit override of entity inference
            "useCase": {
                "description": "A concrete scenario in which a user achieves a goal",
                "synonyms": ["use case"],
                "examples": ["As a PM I can see the roadmap so I can plan releases"],
                "nearMisses": ["userJourney", "jobToBeDone", "requirement"],
                "enforcement": "retry",
            },
            "userJourney": {"nearMisses": ["useCase", "workflow"],
                             "enforcement": "retry"},
            "workflow": {"description": "core kind; chain step 4"},
            "architecture": {"description": "The design decisions for a solution",
                              "nearMisses": ["dev:architectureDoc"]},
            "valueProposition": {"extractable": False, "storeAs": "claim"},
        },
        "chains": [
            {"id": "productDelivery", "name": "Product Delivery Chain",
             "description": "How product strategy flows into shipped architecture",
             "steps": ["useCase", "feature", "userJourney", "workflow",
                        "requirement", "architecture"],
             "enforcement": "warn"},
            {"id": "crossPackChain", "steps": ["product", "dev:issue"]},
        ],
        "relations": [
            {"predicate": "addresses", "mechanism": "IMPL",
             "fromKind": "product-strategy:useCase",
             "toKind": "product-strategy:feature",
             "extractable": True},
            {"predicate": "contains", "mechanism": "IMPL",
             "fromKind": "product-strategy:product",
             "toKind": "product-strategy:feature"},
        ],
    },
    "extraction": {
        "active": True,
        "sourceTypes": ["conversation", "document"],
        "enforcement": {
            "default": "warn",
            "kinds": {"userJourney": "block", "jobToBeDone": "block"},
            "relations": {"contains": "block"},
            "chains": {"crossPackChain": "retry", "productDelivery": "retry"},
        },
    },
}


def _write_pack(d: str, ns: str, data: dict) -> None:
    os.makedirs(f"{d}/{ns}", exist_ok=True)
    with open(f"{d}/{ns}/manifest.yaml", "w") as f:
        yaml.dump(data, f)


@pytest.fixture
def v3_registry():
    """Temp registry: a v3 product-strategy pack + a v2 dev pack."""
    d = tempfile.mkdtemp(prefix="pack_v3_test_")
    _write_pack(d, "dev", V3_DEV)
    _write_pack(d, "product-strategy", V3_PS)
    registry = PackRegistry(d)
    registry.load_all()
    return registry


@pytest.fixture
def registry():
    """Create a temporary pack directory with all 4 packs loaded."""
    d = tempfile.mkdtemp(prefix="pack_test_")
    packs = {
        "dev": {
            "namespace": "dev", "name": "Development",
            "ontology": {
                "extends": "core",
                "objectKinds": ["epic", "issue", "code"],
                "subclassOf": {"epic": "Project", "issue": "WorkItem"},
                "equivalentTo": {"issue": ["pm:issue"]},
            }
        },
        "pm": {
            "namespace": "pm", "name": "Project Management",
            "ontology": {
                "extends": "core",
                "objectKinds": ["issue", "sprint"],
                "subclassOf": {"issue": "WorkItem"},
                "equivalentTo": {"issue": ["dev:issue"]},
            }
        },
        "product-strategy": {
            "namespace": "product-strategy", "name": "Product Strategy",
            "ontology": {
                "extends": "core",
                "objectKinds": ["product", "feature"],
                "subclassOf": {"feature": "WorkItem"},
            }
        },
    }
    for ns, data in packs.items():
        os.makedirs(f"{d}/{ns}", exist_ok=True)
        with open(f"{d}/{ns}/manifest.yaml", "w") as f:
            yaml.dump(data, f)
    registry = PackRegistry(d)
    registry.load_all()
    return registry


class TestPackLoading:
    def test_all_packs_loaded(self, registry):
        packs = registry.list_packs()
        assert len(packs) == 3

    def test_no_errors(self, registry):
        assert not registry.errors


class TestKindExpansion:
    def test_workitem_expands_to_subclasses(self, registry):
        expanded = registry.expand_kind("WorkItem")
        assert "WorkItem" in expanded
        assert "dev:issue" in expanded
        assert "pm:issue" in expanded
        assert "product-strategy:feature" in expanded

    def test_project_expands_to_epic(self, registry):
        expanded = registry.expand_kind("Project")
        assert "Project" in expanded
        assert "dev:epic" in expanded

    def test_equivalence_bidirectional(self, registry):
        dev_expanded = registry.expand_kind("dev:issue")
        pm_expanded = registry.expand_kind("pm:issue")
        assert "pm:issue" in dev_expanded
        assert "dev:issue" in pm_expanded

    def test_unknown_kind_returns_self(self, registry):
        assert registry.expand_kind("nonexistent") == ["nonexistent"]

    def test_leaf_kind_only_self(self, registry):
        expanded = registry.expand_kind("dev:code")
        assert expanded == ["dev:code"]


class TestSubclassValidation:
    def test_valid_subclass(self):
        registry = PackRegistry("/tmp/nonexistent")
        errors = registry._validate({
            "namespace": "dev", "name": "Dev",
            "ontology": {
                "extends": "core",
                "objectKinds": ["epic"],
                "subclassOf": {"epic": "Project"},
            }
        })
        assert not errors

    def test_subclass_not_in_kinds(self):
        registry = PackRegistry("/tmp/nonexistent")
        errors = registry._validate({
            "namespace": "dev", "name": "Dev",
            "ontology": {
                "extends": "core",
                "objectKinds": ["epic"],
                "subclassOf": {"issue": "WorkItem"},  # issue not declared
            }
        })
        assert errors

    def test_subclass_bad_parent(self):
        registry = PackRegistry("/tmp/nonexistent")
        errors = registry._validate({
            "namespace": "dev", "name": "Dev",
            "ontology": {
                "extends": "core",
                "objectKinds": ["epic"],
                "subclassOf": {"epic": "Nonexistent"},
            }
        })
        assert errors


class TestEquivalenceValidation:
    def test_valid_equivalence(self):
        registry = PackRegistry("/tmp/nonexistent")
        errors = registry._validate({
            "namespace": "dev", "name": "Dev",
            "ontology": {
                "extends": "core",
                "objectKinds": ["issue"],
                "equivalentTo": {"issue": ["pm:issue"]},
            }
        })
        assert not errors

    def test_equivalence_no_colon(self):
        registry = PackRegistry("/tmp/nonexistent")
        errors = registry._validate({
            "namespace": "dev", "name": "Dev",
            "ontology": {
                "extends": "core",
                "objectKinds": ["issue"],
                "equivalentTo": {"issue": ["notask"]},  # no colon
            }
        })
        assert errors

    def test_equivalence_not_in_kinds(self):
        registry = PackRegistry("/tmp/nonexistent")
        errors = registry._validate({
            "namespace": "dev", "name": "Dev",
            "ontology": {
                "extends": "core",
                "objectKinds": ["epic"],
                "equivalentTo": {"issue": ["pm:issue"]},  # issue not declared
            }
        })
        assert errors


# ── Manifest v3: kindDefs validation (research-r6 §3.1/§3.5) ──────────────

class TestV3KindDefsValidation:
    """kindDefs: keys ⊆ declared kinds ∪ core kinds; strict on value keys."""

    def _base(self):
        return {
            "namespace": "ps", "name": "PS",
            "ontology": {
                "extends": "core",
                "objectKinds": ["product", "feature"],
                "pointKinds": ["useCase"],
            },
        }

    def test_kinddefs_all_allowed_keys_valid(self):
        registry = PackRegistry("/tmp/nonexistent")
        raw = self._base()
        raw["ontology"]["kindDefs"] = {
            "useCase": {
                "description": "d", "synonyms": ["u"], "examples": ["e"],
                "nearMisses": ["feature"], "extractable": True,
                "storeAs": "claim", "enforcement": "retry",
            },
        }
        assert not registry._validate(raw)

    def test_kinddef_key_not_declared(self):
        registry = PackRegistry("/tmp/nonexistent")
        raw = self._base()
        raw["ontology"]["kindDefs"] = {"notAKind": {"description": "d"}}
        errors = registry._validate(raw)
        assert any("not declared" in e and "notAKind" in e for e in errors)

    def test_kinddef_core_kind_allowed(self):
        """Core kinds (e.g. workflow) may be documented in kindDefs (§4)."""
        registry = PackRegistry("/tmp/nonexistent")
        raw = self._base()
        raw["ontology"]["kindDefs"] = {
            "workflow": {"description": "core kind referenced by a chain"},
        }
        assert not registry._validate(raw)

    def test_kinddef_unknown_value_key(self):
        registry = PackRegistry("/tmp/nonexistent")
        raw = self._base()
        raw["ontology"]["kindDefs"] = {"useCase": {"color": "blue"}}
        errors = registry._validate(raw)
        assert any("unknown key" in e and "color" in e for e in errors)

    def test_kinddef_bad_store_as(self):
        registry = PackRegistry("/tmp/nonexistent")
        raw = self._base()
        raw["ontology"]["kindDefs"] = {"useCase": {"storeAs": "thing"}}
        errors = registry._validate(raw)
        assert any("storeAs" in e and "claim" in e for e in errors)

    def test_kinddef_bad_enforcement(self):
        registry = PackRegistry("/tmp/nonexistent")
        raw = self._base()
        raw["ontology"]["kindDefs"] = {"useCase": {"enforcement": "hard"}}
        errors = registry._validate(raw)
        assert any("enforcement" in e and "warn" in e for e in errors)

    def test_kinddef_synonyms_must_be_list(self):
        registry = PackRegistry("/tmp/nonexistent")
        raw = self._base()
        raw["ontology"]["kindDefs"] = {"useCase": {"synonyms": "use case"}}
        errors = registry._validate(raw)
        assert any("synonyms" in e for e in errors)

    def test_kinddef_nearmisses_must_be_strings(self):
        registry = PackRegistry("/tmp/nonexistent")
        raw = self._base()
        raw["ontology"]["kindDefs"] = {"useCase": {"nearMisses": [1, 2]}}
        errors = registry._validate(raw)
        assert any("nearMisses" in e for e in errors)

    def test_kinddef_extractable_must_be_bool(self):
        registry = PackRegistry("/tmp/nonexistent")
        raw = self._base()
        raw["ontology"]["kindDefs"] = {"useCase": {"extractable": "yes"}}
        errors = registry._validate(raw)
        assert any("extractable" in e for e in errors)

    def test_kinddefs_must_be_map(self):
        registry = PackRegistry("/tmp/nonexistent")
        raw = self._base()
        raw["ontology"]["kindDefs"] = ["useCase"]
        errors = registry._validate(raw)
        assert any("kindDefs" in e for e in errors)


# ── Manifest v3: kindDefs semantics — storeAs/extractable/enforcement ─────

class TestV3KindDefsSemantics:
    """Read-side helpers: store_as, is_extractable, enforcement resolution."""

    def test_store_as_inference_point_claim(self, v3_registry):
        pack = v3_registry.get_pack("product-strategy")
        assert pack.store_as("jobToBeDone") == "claim"

    def test_store_as_inference_object_entity(self, v3_registry):
        pack = v3_registry.get_pack("product-strategy")
        assert pack.store_as("feature") == "entity"

    def test_store_as_inference_event(self, v3_registry):
        pack = v3_registry.get_pack("product-strategy")
        assert pack.store_as("release") == "event"

    def test_store_as_decision_point_kind(self):
        """decision/vision/strategy/plan/goal/target/humanApproval → decision."""
        for kind in ("decision", "strategy", "plan", "goal"):
            pack = PackManifest(
                namespace="t", name="T", version="1", tier="free",
                description="", path=Path("."),
                point_kinds=[kind],
            )
            assert pack.store_as(kind) == "decision"

    def test_store_as_explicit_override_wins(self, v3_registry):
        """objectKind 'market' — inferred entity, explicitly declared tag."""
        pack = v3_registry.get_pack("product-strategy")
        assert pack.store_as("market") == "tag"

    def test_is_extractable_default_true(self, v3_registry):
        pack = v3_registry.get_pack("product-strategy")
        assert pack.is_extractable("feature")  # kindDefs entry, no extractable key
        assert pack.is_extractable("customerSegment")  # no kindDefs entry at all

    def test_kinddef_extractable_false(self, v3_registry):
        pack = v3_registry.get_pack("product-strategy")
        assert not pack.is_extractable("valueProposition")

    def test_relation_extractable_default_false(self, v3_registry):
        """R6 §3.3: relations are non-extractable unless a pack opts in."""
        pack = v3_registry.get_pack("product-strategy")
        contains = [r for r in pack.relations if r["predicate"] == "contains"][0]
        assert not pack.relation_is_extractable(contains)

    def test_relation_extractable_true(self, v3_registry):
        pack = v3_registry.get_pack("product-strategy")
        addresses = [r for r in pack.relations if r["predicate"] == "addresses"][0]
        assert pack.relation_is_extractable(addresses)

    def test_enforcement_kinddef_wins_over_extraction_kinds(self, v3_registry):
        """Resolution: kindDefs[].enforcement → extraction.enforcement.kinds."""
        pack = v3_registry.get_pack("product-strategy")
        # userJourney: kindDefs retry; extraction.enforcement.kinds block
        assert pack.enforcement_for("userJourney") == "retry"

    def test_enforcement_extraction_kinds(self, v3_registry):
        pack = v3_registry.get_pack("product-strategy")
        # jobToBeDone: no kindDefs enforcement; extraction.kinds block
        assert pack.enforcement_for("jobToBeDone") == "block"

    def test_enforcement_default_warn(self, v3_registry):
        pack = v3_registry.get_pack("product-strategy")
        assert pack.enforcement_for("product") == "warn"

    def test_enforcement_kinddef_only(self, v3_registry):
        pack = v3_registry.get_pack("product-strategy")
        assert pack.enforcement_for("useCase") == "retry"

    def test_enforcement_for_relation(self, v3_registry):
        pack = v3_registry.get_pack("product-strategy")
        assert pack.enforcement_for_relation("contains") == "block"
        assert pack.enforcement_for_relation("addresses") == "warn"

    def test_enforcement_for_chain_own_field_wins(self, v3_registry):
        """chain.enforcement beats extraction.enforcement.chains (R6 §3.2/§3.4)."""
        pack = v3_registry.get_pack("product-strategy")
        # productDelivery: chain enforcement warn; extraction.chains retry
        assert pack.enforcement_for_chain("productDelivery") == "warn"

    def test_enforcement_for_chain_extraction_cfg(self, v3_registry):
        pack = v3_registry.get_pack("product-strategy")
        # crossPackChain: no chain enforcement; extraction.chains retry
        assert pack.enforcement_for_chain("crossPackChain") == "retry"

    def test_enforcement_for_chain_default(self, v3_registry):
        pack = v3_registry.get_pack("product-strategy")
        assert pack.enforcement_for_chain("nope") == "warn"

    def test_activation_source_types(self, v3_registry):
        """§3.4: active ∧ (sourceTypes empty or contains source_type)."""
        pack = v3_registry.get_pack("product-strategy")
        assert pack.is_active_for("conversation")
        assert pack.is_active_for("document")
        assert not pack.is_active_for("github_issue")

    def test_activation_empty_source_types_means_all(self):
        pack = PackManifest(
            namespace="t", name="T", version="1", tier="free",
            description="", path=Path("."),
            extraction={"active": True, "sourceTypes": [],
                        "enforcement": {"default": "warn", "kinds": {},
                                         "relations": {}, "chains": {}}},
        )
        assert pack.is_active_for("github_issue")
        assert pack.is_active_for("anything")

    def test_activation_inactive_pack(self):
        pack = PackManifest(
            namespace="t", name="T", version="1", tier="free",
            description="", path=Path("."),
            extraction={"active": False, "sourceTypes": [],
                        "enforcement": {"default": "warn", "kinds": {},
                                         "relations": {}, "chains": {}}},
        )
        assert not pack.is_active_for("conversation")


# ── Manifest v3: chains validation (research-r6 §3.2/§3.5) ────────────────

class TestV3ChainsValidation:
    """chains: id/steps required, unique ids, enforcement ∈ {warn, retry, block}."""

    def _base(self):
        return {
            "namespace": "ps", "name": "PS",
            "ontology": {
                "extends": "core",
                "objectKinds": ["feature"],
                "pointKinds": ["useCase"],
            },
        }

    def test_chain_valid(self):
        registry = PackRegistry("/tmp/nonexistent")
        raw = self._base()
        raw["ontology"]["chains"] = [{
            "id": "c1", "name": "C", "description": "d",
            "steps": ["useCase", "feature"], "enforcement": "retry",
        }]
        assert not registry._validate(raw)

    def test_chain_missing_id(self):
        registry = PackRegistry("/tmp/nonexistent")
        raw = self._base()
        raw["ontology"]["chains"] = [{"steps": ["useCase"]}]
        errors = registry._validate(raw)
        assert any("missing required 'id'" in e for e in errors)

    def test_chain_duplicate_id(self):
        registry = PackRegistry("/tmp/nonexistent")
        raw = self._base()
        raw["ontology"]["chains"] = [
            {"id": "c1", "steps": ["useCase"]},
            {"id": "c1", "steps": ["feature"]},
        ]
        errors = registry._validate(raw)
        assert any("duplicate id 'c1'" in e for e in errors)

    def test_chain_missing_steps(self):
        registry = PackRegistry("/tmp/nonexistent")
        raw = self._base()
        raw["ontology"]["chains"] = [{"id": "c1"}]
        errors = registry._validate(raw)
        assert any("'steps' must be a non-empty list" in e for e in errors)

    def test_chain_empty_steps(self):
        registry = PackRegistry("/tmp/nonexistent")
        raw = self._base()
        raw["ontology"]["chains"] = [{"id": "c1", "steps": []}]
        errors = registry._validate(raw)
        assert any("'steps' must be a non-empty list" in e for e in errors)

    def test_chain_bad_enforcement(self):
        registry = PackRegistry("/tmp/nonexistent")
        raw = self._base()
        raw["ontology"]["chains"] = [{
            "id": "c1", "steps": ["useCase"], "enforcement": "hard",
        }]
        errors = registry._validate(raw)
        assert any("enforcement" in e and "warn" in e for e in errors)

    def test_chain_steps_not_strings(self):
        registry = PackRegistry("/tmp/nonexistent")
        raw = self._base()
        raw["ontology"]["chains"] = [{"id": "c1", "steps": [1, 2]}]
        errors = registry._validate(raw)
        assert any("'steps' must be a non-empty list" in e for e in errors)

    def test_chain_edges_must_be_declared(self):
        registry = PackRegistry("/tmp/nonexistent")
        raw = self._base()
        raw["ontology"]["relations"] = [{
            "predicate": "contains", "mechanism": "IMPL",
            "fromKind": "ps:feature", "toKind": "ps:feature",
        }]
        raw["ontology"]["chains"] = [{
            "id": "c1", "steps": ["useCase", "feature"],
            "edges": ["notAPredicate"],
        }]
        errors = registry._validate(raw)
        assert any("edge 'notAPredicate' is not a declared relation predicate" in e
                   for e in errors)

    def test_chain_edges_core_predicate_allowed(self):
        registry = PackRegistry("/tmp/nonexistent")
        raw = self._base()
        raw["ontology"]["chains"] = [{
            "id": "c1", "steps": ["useCase", "feature"], "edges": ["IMPL"],
        }]
        assert not registry._validate(raw)

    def test_chains_must_be_list(self):
        registry = PackRegistry("/tmp/nonexistent")
        raw = self._base()
        raw["ontology"]["chains"] = {"id": "c1", "steps": ["useCase"]}
        errors = registry._validate(raw)
        assert any("chains must be a list" in e for e in errors)


# ── Manifest v3: post-load cross-pack resolution (nearMisses, chain steps) ─

class TestV3CrossPackResolution:
    """nearMisses + chain steps resolve post-load (R6 §3.5, W-5 failure b)."""

    def test_v3_fixture_loads_clean(self, v3_registry):
        assert not v3_registry.errors
        assert set(v3_registry.packs) == {"dev", "product-strategy"}

    def test_nearmiss_cross_pack_resolves(self, v3_registry):
        """architecture nearMisses dev:architectureDoc — cross-pack target."""
        assert not v3_registry.errors

    def test_chain_step_core_bare_resolves(self, v3_registry):
        """'workflow' resolves to the core kind (bare)."""
        assert not v3_registry.errors

    def test_chain_step_cross_pack_resolves(self, v3_registry):
        """crossPackChain step 'dev:issue' resolves cross-pack."""
        assert not v3_registry.errors

    def test_nearmiss_unknown_target(self):
        d = tempfile.mkdtemp(prefix="pack_v3_bad_")
        _write_pack(d, "ps", {
            "namespace": "ps", "name": "PS",
            "ontology": {
                "extends": "core",
                "objectKinds": ["product"],
                "kindDefs": {"product": {"nearMisses": ["ghostKind"]}},
            },
        })
        registry = PackRegistry(d)
        registry.load_all()
        assert "ps" in registry.errors
        assert any("nearMisses" in e and "ghostKind" in e for e in registry.errors["ps"])

    def test_chain_step_unknown_target(self):
        d = tempfile.mkdtemp(prefix="pack_v3_bad_")
        _write_pack(d, "ps", {
            "namespace": "ps", "name": "PS",
            "ontology": {
                "extends": "core",
                "objectKinds": ["product"],
                "chains": [{"id": "c1", "steps": ["product", "ghostKind"]}],
            },
        })
        registry = PackRegistry(d)
        registry.load_all()
        assert "ps" in registry.errors
        assert any("step 'ghostKind'" in e and "does not resolve" in e
                   for e in registry.errors["ps"])

    def test_chain_step_ambiguous_bare_kind(self):
        """Bare step declared by two packs → ambiguous (W-5 failure b)."""
        d = tempfile.mkdtemp(prefix="pack_v3_ambig_")
        for ns in ("alpha", "beta"):
            _write_pack(d, ns, {
                "namespace": ns, "name": ns,
                "ontology": {"extends": "core", "objectKinds": ["widget"]},
            })
        _write_pack(d, "gamma", {
            "namespace": "gamma", "name": "Gamma",
            "ontology": {
                "extends": "core",
                "objectKinds": ["gadget"],
                "chains": [{"id": "c1", "steps": ["gadget", "widget"]}],
            },
        })
        registry = PackRegistry(d)
        registry.load_all()
        assert "gamma" in registry.errors
        assert any("step 'widget'" in e and "ambiguous" in e
                   for e in registry.errors["gamma"])

    def test_chain_step_single_namespace_bare_ok(self):
        """Bare step declared by exactly one other pack resolves."""
        d = tempfile.mkdtemp(prefix="pack_v3_single_")
        _write_pack(d, "alpha", {
            "namespace": "alpha", "name": "Alpha",
            "ontology": {"extends": "core", "objectKinds": ["widget"]},
        })
        _write_pack(d, "beta", {
            "namespace": "beta", "name": "Beta",
            "ontology": {
                "extends": "core",
                "objectKinds": ["gadget"],
                "chains": [{"id": "c1", "steps": ["gadget", "widget"]}],
            },
        })
        registry = PackRegistry(d)
        registry.load_all()
        assert not registry.errors

    def test_nearmiss_ambiguous_bare_kind(self):
        d = tempfile.mkdtemp(prefix="pack_v3_nm_ambig_")
        for ns in ("alpha", "beta"):
            _write_pack(d, ns, {
                "namespace": ns, "name": ns,
                "ontology": {"extends": "core", "objectKinds": ["widget"]},
            })
        _write_pack(d, "gamma", {
            "namespace": "gamma", "name": "Gamma",
            "ontology": {
                "extends": "core",
                "objectKinds": ["gadget"],
                "kindDefs": {"gadget": {"nearMisses": ["widget"]}},
            },
        })
        registry = PackRegistry(d)
        registry.load_all()
        assert "gamma" in registry.errors
        assert any("nearMisses" in e and "ambiguous" in e
                   for e in registry.errors["gamma"])


# ── Manifest v3: relations[].extractable ──────────────────────────────────

class TestV3RelationExtractableFlag:
    def test_extractable_must_be_bool(self):
        registry = PackRegistry("/tmp/nonexistent")
        errors = registry._validate({
            "namespace": "dev", "name": "Dev",
            "ontology": {
                "extends": "core",
                "objectKinds": ["issue", "code"],
                "relations": [{
                    "predicate": "addresses", "mechanism": "IMPL",
                    "fromKind": "dev:issue", "toKind": "dev:code",
                    "extractable": "yes",
                }],
            },
        })
        assert any("extractable" in e and "boolean" in e for e in errors)

    def test_list_relations_includes_extractable(self, v3_registry):
        relations = v3_registry.list_relations()
        by_pred = {r["predicate"]: r for r in relations}
        assert by_pred["addresses"]["extractable"] is True
        assert by_pred["contains"]["extractable"] is False


# ── Manifest v3: extraction config validation (research-r6 §3.4/§3.5) ─────

class TestV3ExtractionValidation:
    def _base(self):
        return {
            "namespace": "ps", "name": "PS",
            "ontology": {
                "extends": "core",
                "objectKinds": ["product"],
                "pointKinds": ["useCase"],
                "chains": [{"id": "c1", "steps": ["useCase"]}],
            },
        }

    def test_extraction_valid(self):
        registry = PackRegistry("/tmp/nonexistent")
        raw = self._base()
        raw["extraction"] = {
            "active": True,
            "sourceTypes": ["conversation", "document"],
            "enforcement": {
                "default": "warn",
                "kinds": {"useCase": "retry"},
                "relations": {"IMPL": "block"},
                "chains": {"c1": "warn"},
            },
        }
        assert not registry._validate(raw)

    def test_extraction_not_map(self):
        registry = PackRegistry("/tmp/nonexistent")
        raw = self._base()
        raw["extraction"] = "yes"
        errors = registry._validate(raw)
        assert any("extraction must be a map" in e for e in errors)

    def test_active_must_be_bool(self):
        registry = PackRegistry("/tmp/nonexistent")
        raw = self._base()
        raw["extraction"] = {"active": "yes"}
        errors = registry._validate(raw)
        assert any("extraction.active must be a boolean" in e for e in errors)

    def test_unknown_source_type_rejected(self):
        """Typo protection: unknown source type → error."""
        registry = PackRegistry("/tmp/nonexistent")
        raw = self._base()
        raw["extraction"] = {"sourceTypes": ["conversatio"]}
        errors = registry._validate(raw)
        assert any("not a known source type" in e and "conversatio" in e
                   for e in errors)

    def test_escape_hatch_source_type_allowed(self):
        """Escape-hatch list admits future connector source kinds (§3.5)."""
        registry = PackRegistry("/tmp/nonexistent")
        raw = self._base()
        raw["extraction"] = {"sourceTypes": ["email"]}
        assert not registry._validate(raw)

    def test_source_types_must_be_list(self):
        registry = PackRegistry("/tmp/nonexistent")
        raw = self._base()
        raw["extraction"] = {"sourceTypes": "conversation"}
        errors = registry._validate(raw)
        assert any("sourceTypes must be a list" in e for e in errors)

    def test_enforcement_bad_default(self):
        registry = PackRegistry("/tmp/nonexistent")
        raw = self._base()
        raw["extraction"] = {"enforcement": {"default": "hard"}}
        errors = registry._validate(raw)
        assert any("enforcement.default" in e for e in errors)

    def test_enforcement_bad_kind_level(self):
        registry = PackRegistry("/tmp/nonexistent")
        raw = self._base()
        raw["extraction"] = {"enforcement": {"kinds": {"useCase": "hard"}}}
        errors = registry._validate(raw)
        assert any("enforcement.kinds" in e for e in errors)

    def test_enforcement_unknown_kind_key(self):
        """Typo protection: enforcement.kinds key must be a declared kind."""
        registry = PackRegistry("/tmp/nonexistent")
        raw = self._base()
        raw["extraction"] = {"enforcement": {"kinds": {"usecase": "warn"}}}
        errors = registry._validate(raw)
        assert any("enforcement.kinds" in e and "usecase" in e for e in errors)

    def test_enforcement_unknown_chain_key(self):
        registry = PackRegistry("/tmp/nonexistent")
        raw = self._base()
        raw["extraction"] = {"enforcement": {"chains": {"c99": "warn"}}}
        errors = registry._validate(raw)
        assert any("enforcement.chains" in e and "c99" in e for e in errors)

    def test_enforcement_unknown_relation_key(self):
        registry = PackRegistry("/tmp/nonexistent")
        raw = self._base()
        raw["extraction"] = {"enforcement": {"relations": {"hovers": "warn"}}}
        errors = registry._validate(raw)
        assert any("enforcement.relations" in e and "hovers" in e for e in errors)

    def test_enforcement_unknown_section(self):
        registry = PackRegistry("/tmp/nonexistent")
        raw = self._base()
        raw["extraction"] = {"enforcement": {"points": {"x": "warn"}}}
        errors = registry._validate(raw)
        assert any("unknown key 'points'" in e for e in errors)


# ── Manifest v3: backward compatibility (research-r6 §6.2a) ───────────────

class TestV3BackwardCompatibility:
    """A manifest without the v3 sections behaves exactly as today."""

    def test_v2_manifest_unchanged_semantics(self, tmp_path):
        d = str(tmp_path / "packs")
        _write_pack(d, "legacy", {
            "namespace": "legacy", "name": "Legacy",
            "ontology": {
                "extends": "core",
                "objectKinds": ["widget"],
                "relations": [{
                    "predicate": "contains", "mechanism": "IMPL",
                    "fromKind": "legacy:widget", "toKind": "legacy:widget",
                }],
            },
        })
        registry = PackRegistry(d)
        loaded = registry.load_all()
        assert loaded == 1
        assert not registry.errors
        pack = registry.get_pack("legacy")
        # Extraction defaults: active, all source types, warn enforcement
        assert pack.extraction["active"] is True
        assert pack.extraction["sourceTypes"] == []
        assert pack.enforcement_for("widget") == "warn"
        assert pack.is_active_for("conversation")
        # All kinds extractable, relations non-extractable
        assert pack.is_extractable("widget")
        assert not pack.relation_is_extractable(pack.relations[0])
        # No chains, no kindDefs
        assert pack.chains == []
        assert pack.kind_defs == {}

    def test_current_v2_packs_load_with_zero_errors(self):
        """§6.2a regression: the CURRENT registry packs (v2) load unchanged."""
        registry = PackRegistry(REPO_PACKS_DIR)
        loaded = registry.load_all()
        assert loaded == 4  # dev, marketing, product-strategy, project-management
        assert not registry.errors, f"v2 packs must load clean: {registry.errors}"


# ── Core-entity expansion fix (research-r6 §6.2a) ─────────────────────────

class TestCoreEntityExpansionFix:
    """Document/Source/Subject/Point/Event are expansion parents."""

    def test_document_expands_architecture_subclass(self, v3_registry):
        """`architecture: Document` must actually expand (was the §6.2a gap)."""
        expanded = v3_registry.expand_kind("Document")
        assert "Document" in expanded
        assert "product-strategy:architecture" in expanded

    def test_workitem_expansion_still_correct(self, v3_registry):
        expanded = v3_registry.expand_kind("WorkItem")
        assert "WorkItem" in expanded
        assert "product-strategy:feature" in expanded
        assert "product-strategy:requirement" in expanded

    def test_expansion_respects_load_isolation(self):
        """Errored packs contribute nothing to the expansion table."""
        d = tempfile.mkdtemp(prefix="pack_v3_iso_exp_")
        _write_pack(d, "good", {
            "namespace": "good", "name": "Good",
            "ontology": {
                "extends": "core",
                "objectKinds": ["thing"],
                "subclassOf": {"thing": "Document"},
            },
        })
        _write_pack(d, "bad", {
            "namespace": "bad", "name": "Bad", "tier": "enterprise",
            "ontology": {"extends": "core", "objectKinds": ["junk"]},
        })
        registry = PackRegistry(d)
        registry.load_all()
        assert "bad" in registry.errors
        assert "bad" not in registry.packs
        assert "good:thing" in registry.expand_kind("Document")


# ── Per-pack load isolation (R-16, plan §W-5) ─────────────────────────────

class TestPerPackLoadIsolation:
    """One broken pack fails that pack only — the registry stays healthy."""

    def test_validation_error_fails_only_that_pack(self):
        d = tempfile.mkdtemp(prefix="pack_v3_iso_")
        _write_pack(d, "good", V3_DEV)
        _write_pack(d, "bad", {
            "namespace": "bad", "name": "Bad", "tier": "enterprise",
            "ontology": {"extends": "core", "objectKinds": ["junk"]},
        })
        registry = PackRegistry(d)
        loaded = registry.load_all()
        assert loaded == 1
        assert "bad" in registry.errors
        assert "bad" not in registry.packs
        # The healthy pack is fully queryable — registry + compile still work
        # (V3_DEV's manifest namespace is "dev")
        assert "dev" in registry.packs
        assert len(registry.list_packs()) == 1
        assert registry.register_kinds()["registered"] == 4  # issue, code, requirement, architectureDoc
        assert registry.expand_kind("dev:issue") == ["dev:issue"]

    def test_cross_pack_error_pack_dropped_from_compile(self):
        """A pack with unresolved cross-pack refs is excluded too."""
        d = tempfile.mkdtemp(prefix="pack_v3_iso_x_")
        _write_pack(d, "good", V3_DEV)
        _write_pack(d, "broken", {
            "namespace": "broken", "name": "Broken",
            "ontology": {
                "extends": "core",
                "objectKinds": ["issue"],
                "relations": [{
                    "predicate": "addresses", "mechanism": "IMPL",
                    "fromKind": "ghost:thing", "toKind": "broken:issue",
                }],
            },
        })
        registry = PackRegistry(d)
        loaded = registry.load_all()
        assert loaded == 1
        assert "broken" in registry.errors
        assert "broken" not in registry.packs
        assert any("does not resolve" in e for e in registry.errors["broken"])
        # Registry + compile still work for the healthy pack
        assert len(registry.list_packs()) == 1
        assert "dev:issue" in registry._all_known_kinds()


# ── Worked product-strategy example (research-r6 §4, §6.2a) ───────────────

class TestWorkedExampleV3Fixture:
    """The §4 worked example (trimmed fixture) + the real v2 packs → clean."""

    def test_worked_example_zero_validation_errors(self):
        d = tempfile.mkdtemp(prefix="pack_v3_worked_")
        # Real v2 packs (dev/marketing/project-management) + the v3 fixture as
        # product-strategy — the §6.2a scenario: 4 packs, zero errors.
        fixture = Path(__file__).resolve().parent / "fixtures" / "pack_v3" / "product-strategy.yaml"
        for ns in ("dev", "marketing", "project-management"):
            os.makedirs(f"{d}/{ns}", exist_ok=True)
            shutil.copy(REPO_PACKS_DIR / ns / "manifest.yaml", f"{d}/{ns}/manifest.yaml")
        os.makedirs(f"{d}/product-strategy", exist_ok=True)
        shutil.copy(fixture, f"{d}/product-strategy/manifest.yaml")

        registry = PackRegistry(d)
        loaded = registry.load_all()
        assert loaded == 4
        assert not registry.errors, f"worked example must load clean: {registry.errors}"

        ps = registry.get_pack("product-strategy")
        # Chain parsed and steps resolved (core workflow + equivalentTo)
        assert [c["id"] for c in ps.chains] == ["productDelivery"]
        assert ps.chains[0]["steps"] == [
            "useCase", "feature", "userJourney", "workflow",
            "requirement", "architecture",
        ]
        # Core-entity expansion: architecture: Document expands
        assert "product-strategy:architecture" in registry.expand_kind("Document")
        # Enforcement: kindDefs retry for useCase; warn default elsewhere
        assert ps.enforcement_for("useCase") == "retry"
        assert ps.enforcement_for("product") == "warn"
        # Extractable relations: the two chain edges opt in, others don't
        extractable = [r["predicate"] for r in ps.relations
                       if ps.relation_is_extractable(r)]
        assert extractable == ["addresses", "addresses"]
        # Activation: conversation/document activate the pack
        assert ps.is_active_for("conversation")
        assert not ps.is_active_for("github_issue")
        # equivalence resolved: requirement ≡ dev:requirement
        assert "dev:requirement" in registry.expand_kind("product-strategy:requirement")
