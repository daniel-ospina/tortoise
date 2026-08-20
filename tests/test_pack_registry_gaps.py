"""Tests for pack_registry validation gaps, query methods, and cross-pack refs.

Covers (#7864, #7863):
  - Validation edge cases not covered in test_pack_kinds.py
  - Query methods: list_relations, list_connectors, list_tools, register_kinds
  - Cross-pack reference resolution
"""
from __future__ import annotations

import os
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: I001
from tortoise.pack_registry import PackRegistry


# ── Validation edge cases ──────────────────────────────────────────────────

class TestValidationEdgeCases:
    """Individual _validate() edge cases not covered in test_pack_kinds.py."""

    def test_missing_namespace(self):
        registry = PackRegistry("/tmp/nonexistent")
        errors = registry._validate({"name": "Test"})
        assert any("missing required field: namespace" in e for e in errors)

    def test_missing_name(self):
        registry = PackRegistry("/tmp/nonexistent")
        errors = registry._validate({"namespace": "test"})
        assert any("missing required field: name" in e for e in errors)

    def test_empty_namespace(self):
        registry = PackRegistry("/tmp/nonexistent")
        errors = registry._validate({"namespace": "", "name": "X"})
        assert any("missing required field: namespace" in e for e in errors)

    def test_colon_in_namespace(self):
        registry = PackRegistry("/tmp/nonexistent")
        errors = registry._validate({
            "namespace": "bad:ns", "name": "X",
            "ontology": {"extends": "core"},
        })
        assert any("namespace must not contain ':'" in e for e in errors)

    def test_bad_tier(self):
        registry = PackRegistry("/tmp/nonexistent")
        errors = registry._validate(
            {"namespace": "x", "name": "X", "tier": "enterprise"})
        assert any("tier" in e for e in errors)

    def test_tier_free_valid(self):
        registry = PackRegistry("/tmp/nonexistent")
        errors = registry._validate(
            {"namespace": "x", "name": "X", "tier": "free"})
        assert not any("tier" in e for e in errors)

    def test_tier_premium_valid(self):
        registry = PackRegistry("/tmp/nonexistent")
        errors = registry._validate(
            {"namespace": "x", "name": "X", "tier": "premium"})
        assert not any("tier" in e for e in errors)

    def test_equivalent_to_no_colon(self):
        registry = PackRegistry("/tmp/nonexistent")
        errors = registry._validate({
            "namespace": "dev", "name": "Dev",
            "ontology": {
                "extends": "core",
                "objectKinds": ["issue"],
                "equivalentTo": {"issue": ["notask"]},
            },
        })
        assert any("must use 'namespace:kind' format" in e for e in errors)

    def test_equivalent_to_multiple_targets_some_bad(self):
        """One bad target in a list of multiple — error raised."""
        registry = PackRegistry("/tmp/nonexistent")
        errors = registry._validate({
            "namespace": "dev", "name": "Dev",
            "ontology": {
                "extends": "core",
                "objectKinds": ["issue"],
                "equivalentTo": {"issue": ["pm:issue", "badref"]},
            },
        })
        assert any("must use 'namespace:kind' format" in e for e in errors)

    def test_subclass_of_parent_not_in_core(self):
        registry = PackRegistry("/tmp/nonexistent")
        errors = registry._validate({
            "namespace": "dev", "name": "Dev",
            "ontology": {
                "extends": "core",
                "objectKinds": ["epic"],
                "subclassOf": {"epic": "NonExistentParent"},
            },
        })
        assert any("not found in core ontology" in e for e in errors)

    def test_subclass_of_parent_lowercase(self):
        """Parent kind must be PascalCase — lowercase should error."""
        registry = PackRegistry("/tmp/nonexistent")
        errors = registry._validate({
            "namespace": "dev", "name": "Dev",
            "ontology": {
                "extends": "core",
                "objectKinds": ["epic"],
                "subclassOf": {"epic": "workItem"},
            },
        })
        assert any("must be PascalCase" in e for e in errors)

    # ── Relation validation ────────────────────────────────────────────

    def test_relation_invalid_mechanism(self):
        registry = PackRegistry("/tmp/nonexistent")
        errors = registry._validate({
            "namespace": "dev", "name": "Dev",
            "ontology": {
                "extends": "core",
                "objectKinds": ["issue", "code"],
                "relations": [{
                    "predicate": "addresses",
                    "fromKind": "dev:issue",
                    "toKind": "dev:code",
                    "mechanism": "CAUSES",
                }],
            },
        })
        assert any("mechanism must be IMPL or NAND" in e for e in errors)

    def test_relation_missing_from_kind_when_to_kind_present(self):
        registry = PackRegistry("/tmp/nonexistent")
        errors = registry._validate({
            "namespace": "dev", "name": "Dev",
            "ontology": {
                "extends": "core",
                "objectKinds": ["issue"],
                "relations": [{
                    "predicate": "addresses",
                    "toKind": "dev:issue",
                }],
            },
        })
        assert any("both fromKind and toKind required" in e for e in errors)

    def test_relation_missing_to_kind_when_from_kind_present(self):
        registry = PackRegistry("/tmp/nonexistent")
        errors = registry._validate({
            "namespace": "dev", "name": "Dev",
            "ontology": {
                "extends": "core",
                "objectKinds": ["issue"],
                "relations": [{
                    "predicate": "addresses",
                    "fromKind": "dev:issue",
                }],
            },
        })
        assert any("both fromKind and toKind required" in e for e in errors)

    def test_relation_invalid_cardinality(self):
        registry = PackRegistry("/tmp/nonexistent")
        errors = registry._validate({
            "namespace": "dev", "name": "Dev",
            "ontology": {
                "extends": "core",
                "objectKinds": ["issue", "code"],
                "relations": [{
                    "predicate": "addresses",
                    "fromKind": "dev:issue",
                    "toKind": "dev:code",
                    "mechanism": "IMPL",
                    "cardinality": "bad_card",
                }],
            },
        })
        assert any("invalid cardinality" in e for e in errors)

    def test_relation_valid_impl_mechanism(self):
        registry = PackRegistry("/tmp/nonexistent")
        errors = registry._validate({
            "namespace": "dev", "name": "Dev",
            "ontology": {
                "extends": "core",
                "objectKinds": ["issue", "code"],
                "relations": [{
                    "predicate": "addresses",
                    "fromKind": "dev:issue",
                    "toKind": "dev:code",
                    "mechanism": "IMPL",
                }],
            },
        })
        assert not errors

    def test_relation_valid_nand_mechanism(self):
        registry = PackRegistry("/tmp/nonexistent")
        errors = registry._validate({
            "namespace": "dev", "name": "Dev",
            "ontology": {
                "extends": "core",
                "objectKinds": ["issue", "code"],
                "relations": [{
                    "predicate": "competesWith",
                    "fromKind": "dev:issue",
                    "toKind": "dev:code",
                    "mechanism": "NAND",
                }],
            },
        })
        assert not errors

    def test_relation_valid_cardinalities(self):
        """All four valid cardinality values should pass."""
        registry = PackRegistry("/tmp/nonexistent")
        for card in ("one_to_one", "one_to_many", "many_to_one", "many_to_many"):
            errors = registry._validate({
                "namespace": "dev", "name": "Dev",
                "ontology": {
                    "extends": "core",
                    "objectKinds": ["issue", "code"],
                    "relations": [{
                        "predicate": "addresses",
                        "fromKind": "dev:issue",
                        "toKind": "dev:code",
                        "mechanism": "IMPL",
                        "cardinality": card,
                    }],
                },
            })
            assert not any("invalid cardinality" in e for e in errors), \
                f"Cardinality {card!r} should be valid"

    def test_relation_from_kind_referencing_own_kind_valid(self):
        """Self-reference to own pack kind should pass validation."""
        registry = PackRegistry("/tmp/nonexistent")
        errors = registry._validate({
            "namespace": "dev", "name": "Dev",
            "ontology": {
                "extends": "core",
                "objectKinds": ["issue", "pr"],
                "relations": [{
                    "predicate": "addresses",
                    "fromKind": "dev:pr",
                    "toKind": "dev:issue",
                    "mechanism": "IMPL",
                }],
            },
        })
        assert not errors

    def test_relation_from_kind_referencing_undeclared_own_kind(self):
        """Self-reference to a kind NOT in this pack's kinds → error."""
        registry = PackRegistry("/tmp/nonexistent")
        errors = registry._validate({
            "namespace": "dev", "name": "Dev",
            "ontology": {
                "extends": "core",
                "objectKinds": ["issue"],
                "relations": [{
                    "predicate": "addresses",
                    "fromKind": "dev:notdeclared",
                    "toKind": "dev:issue",
                    "mechanism": "IMPL",
                }],
            },
        })
        assert any("not declared in this pack" in e for e in errors)


# ── Registry-level validation ──────────────────────────────────────────────

class TestRegistryLevelValidation:
    """Registry-level errors: duplicate namespace packs."""

    def test_duplicate_namespace_packs(self, tmp_path):
        """Two packs with the same manifest namespace → error recorded."""
        d = str(tmp_path / "packs")
        os.makedirs(f"{d}/dev", exist_ok=True)
        with open(f"{d}/dev/manifest.yaml", "w") as f:
            yaml.dump({
                "namespace": "dev",
                "name": "Development",
                "ontology": {"extends": "core", "objectKinds": ["issue"]},
            }, f)
        # Second directory, same namespace in manifest
        os.makedirs(f"{d}/dev2", exist_ok=True)
        with open(f"{d}/dev2/manifest.yaml", "w") as f:
            yaml.dump({
                "namespace": "dev",
                "name": "Dev Duplicate",
                "ontology": {"extends": "core", "objectKinds": ["bug"]},
            }, f)
        registry = PackRegistry(d)
        registry.load_all()
        # Error is keyed by directory name (dev2), not namespace
        assert "dev2" in registry.errors
        assert any("duplicate namespace" in e for e in registry.errors["dev2"])


# ── Query methods ──────────────────────────────────────────────────────────

@pytest.fixture
def query_registry(tmp_path):
    """Registry with a pack that has relations, connectors, and tools."""
    d = str(tmp_path / "packs")
    pack = {
        "namespace": "test",
        "name": "Test Pack",
        "version": "1.0.0",
        "tier": "premium",
        "description": "A test pack",
        "ontology": {
            "extends": "core",
            "objectKinds": ["issue", "code"],
            "relations": [
                {
                    "predicate": "addresses",
                    "fromKind": "test:issue",
                    "toKind": "test:code",
                    "mechanism": "IMPL",
                    "cardinality": "one_to_many",
                },
                {
                    "predicate": "competesWith",
                    "fromKind": "test:code",
                    "toKind": "test:issue",
                    "mechanism": "NAND",
                },
            ],
        },
        "connectors": [
            {
                "source": "github",
                "entrypoint": "connectors.github:GitHubConnector",
                "config": {"required": ["api_key"]},
            },
        ],
        "tools": [
            {
                "name": "analyze",
                "description": "Analyze code",
                "entrypoint": "tools.analyzer:analyze",
                "params": {"depth": {"type": "integer", "default": 1}},
            },
        ],
    }
    os.makedirs(f"{d}/test", exist_ok=True)
    with open(f"{d}/test/manifest.yaml", "w") as f:
        yaml.dump(pack, f)
    registry = PackRegistry(d)
    registry.load_all()
    return registry


class TestListRelations:
    def test_returns_predicate_and_mechanism(self, query_registry):
        relations = query_registry.list_relations()
        assert len(relations) == 2
        for r in relations:
            assert "predicate" in r
            assert "fromKind" in r
            assert "toKind" in r
            assert "mechanism" in r
            assert "pack" in r
            assert r["pack"] == "test"

    def test_returns_correct_predicates(self, query_registry):
        relations = query_registry.list_relations()
        predicates = {r["predicate"] for r in relations}
        assert predicates == {"addresses", "competesWith"}

    def test_returns_correct_mechanisms(self, query_registry):
        relations = query_registry.list_relations()
        mechanisms = {r["mechanism"] for r in relations}
        assert mechanisms == {"IMPL", "NAND"}

    def test_includes_cardinality_when_present(self, query_registry):
        relations = query_registry.list_relations()
        addr = [r for r in relations if r["predicate"] == "addresses"][0]  # noqa: RUF015
        assert addr["cardinality"] == "one_to_many"
        comp = [r for r in relations if r["predicate"] == "competesWith"][0]  # noqa: RUF015
        assert "cardinality" not in comp


class TestListConnectors:
    def test_returns_source_and_config(self, query_registry):
        connectors = query_registry.list_connectors()
        assert len(connectors) == 1
        c = connectors[0]
        assert c["source"] == "github"
        assert c["pack"] == "test"
        assert c["entrypoint"] == "connectors.github:GitHubConnector"
        assert "api_key" in c["required_config"]

    def test_required_config_is_list(self, query_registry):
        connectors = query_registry.list_connectors()
        assert isinstance(connectors[0]["required_config"], list)


class TestListTools:
    def test_returns_name_and_params(self, query_registry):
        tools = query_registry.list_tools()
        assert len(tools) == 1
        t = tools[0]
        assert t["name"] == "analyze"
        assert t["pack"] == "test"
        assert t["description"] == "Analyze code"
        assert "params" in t
        assert t["params"]["depth"]["type"] == "integer"


class TestRegisterKinds:
    def test_idempotent_same_counts_every_call(self, query_registry):
        """register_kinds returns consistent counts — safe to call multiple times."""
        first = query_registry.register_kinds()
        assert first["registered"] > 0
        assert first["skipped"] == 0

        second = query_registry.register_kinds()
        # Second call returns same registered count (no cross-call dedup),
        # but method is still safe/idempotent — no side effects.
        assert second["registered"] == first["registered"]
        assert second["skipped"] == 0

    def test_counts_match_expected_kinds(self, query_registry):
        """Two objectKinds declared → 2 registered."""
        result = query_registry.register_kinds()
        assert result["registered"] == 2  # issue, code
        assert result["skipped"] == 0


# ── Cross-pack ref validation ──────────────────────────────────────────────

class TestCrossPackRefs:
    """Test _validate_cross_pack_refs for fromKind/toKind resolution."""

    def test_from_kind_nonexistent_pack_kind_error(self, tmp_path):
        """fromKind referencing a nonexistent pack kind → error."""
        d = str(tmp_path / "packs")
        os.makedirs(f"{d}/dev", exist_ok=True)
        with open(f"{d}/dev/manifest.yaml", "w") as f:
            yaml.dump({
                "namespace": "dev",
                "name": "Development",
                "ontology": {
                    "extends": "core",
                    "objectKinds": ["issue"],
                    "relations": [{
                        "predicate": "addresses",
                        "fromKind": "nonexistent:kind",
                        "toKind": "dev:issue",
                        "mechanism": "IMPL",
                    }],
                },
            }, f)
        registry = PackRegistry(d)
        registry.load_all()
        assert "dev" in registry.errors
        assert any(
            "does not resolve" in e for e in registry.errors["dev"]
        )

    def test_to_kind_nonexistent_pack_kind_error(self, tmp_path):
        """toKind referencing a nonexistent pack kind → error."""
        d = str(tmp_path / "packs")
        os.makedirs(f"{d}/dev", exist_ok=True)
        with open(f"{d}/dev/manifest.yaml", "w") as f:
            yaml.dump({
                "namespace": "dev",
                "name": "Development",
                "ontology": {
                    "extends": "core",
                    "objectKinds": ["issue"],
                    "relations": [{
                        "predicate": "addresses",
                        "fromKind": "dev:issue",
                        "toKind": "nonexistent:kind",
                        "mechanism": "IMPL",
                    }],
                },
            }, f)
        registry = PackRegistry(d)
        registry.load_all()
        assert "dev" in registry.errors
        assert any(
            "does not resolve" in e for e in registry.errors["dev"]
        )

    def test_valid_cross_pack_ref_no_error(self, tmp_path):
        """dev:issue → product-strategy:feature (both exist) → no error."""
        d = str(tmp_path / "packs")
        # Pack 1: dev
        os.makedirs(f"{d}/dev", exist_ok=True)
        with open(f"{d}/dev/manifest.yaml", "w") as f:
            yaml.dump({
                "namespace": "dev",
                "name": "Development",
                "ontology": {"extends": "core", "objectKinds": ["issue"]},
            }, f)
        # Pack 2: product-strategy
        os.makedirs(f"{d}/product-strategy", exist_ok=True)
        with open(f"{d}/product-strategy/manifest.yaml", "w") as f:
            yaml.dump({
                "namespace": "product-strategy",
                "name": "Product Strategy",
                "ontology": {"extends": "core", "objectKinds": ["feature"]},
            }, f)
        # Pack 3: cross-pack relation
        os.makedirs(f"{d}/relations", exist_ok=True)
        with open(f"{d}/relations/manifest.yaml", "w") as f:
            yaml.dump({
                "namespace": "relations",
                "name": "Relations Test",
                "ontology": {
                    "extends": "core",
                    "objectKinds": ["link"],
                    "relations": [{
                        "predicate": "addresses",
                        "fromKind": "dev:issue",
                        "toKind": "product-strategy:feature",
                        "mechanism": "IMPL",
                    }],
                },
            }, f)
        registry = PackRegistry(d)
        loaded = registry.load_all()
        assert loaded == 3
        # No cross-pack resolution errors
        cross_ref_errors = [
            err for ns, errs in registry.errors.items()
            for err in errs if "does not resolve" in err
        ]
        assert cross_ref_errors == [], \
            f"Unexpected cross-pack ref errors: {cross_ref_errors}"
