"""Tests for pack_registry validation gaps, query methods, and cross-pack refs.

Covers (#7864, #7863):
  - Validation edge cases not covered in test_pack_kinds.py
  - Query methods: list_relations, list_connectors, list_tools, register_kinds
  - Cross-pack reference resolution
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

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
        """A NON-canonical lowercase parent must error (#2727: canonical
        lowercase object kinds like `document`/`target` are accepted, so the
        rule is membership-scoped, not blanket PascalCase)."""
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

    # ── Layer 2 — the semantics vocabulary (#7852) ──────────────────────

    def test_relation_unknown_semantics_warns_naming_alternatives(self, caplog):
        """An unknown semantics value is flagged, never refused (W2)."""
        import logging

        registry = PackRegistry("/tmp/nonexistent")
        with caplog.at_level(logging.WARNING, logger="tortoise.pack_registry"):
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
                        "semantics": "suports",
                    }],
                },
            })
        assert not any("semantics" in e for e in errors), errors
        assert "'suports'" in caplog.text
        # The warning names the valid alternatives.
        assert "supports" in caplog.text
        assert "mutuallyExclusive" in caplog.text

    def test_semantics_directions_covers_every_value(self):
        """A value cannot be added without stating the direction it records."""
        from tortoise.pack_registry import (
            SEMANTICS_DIRECTIONS,
            SEMANTICS_VALUES,
        )

        assert set(SEMANTICS_DIRECTIONS) == set(SEMANTICS_VALUES)
        assert set(SEMANTICS_DIRECTIONS.values()) <= {"->", "<-", "<->", "-"}

    def test_relation_canonical_semantics_accepted(self):
        registry = PackRegistry("/tmp/nonexistent")
        for value in ("addresses", "mutuallySupportive", "mutuallyExclusive",
                      "refutes", "causes", "transacts", "related",
                      "hasPart", "dependsOn"):
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
                        "semantics": value,
                    }],
                },
            })
            assert not any("semantics" in e for e in errors), (value, errors)

    def test_relation_transitional_semantics_not_refused(self):
        """A retired word warns (packs align in #7848) but never blocks."""
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
                    "semantics": "opposes",
                }],
            },
        })
        assert not any("semantics" in e for e in errors), errors

    def test_relation_semantics_must_be_non_empty_string(self):
        registry = PackRegistry("/tmp/nonexistent")
        for bad in ("", 7, ["addresses"]):
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
                        "semantics": bad,
                    }],
                },
            })
            assert any("must be a non-empty" in e for e in errors), (bad, errors)

    def test_relation_near_misses_must_be_list_of_strings(self):
        registry = PackRegistry("/tmp/nonexistent")
        for bad in ("supports", [1], ["supports", ""]):
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
                        "nearMisses": bad,
                    }],
                },
            })
            assert any("nearMisses" in e for e in errors), (bad, errors)

    def test_relation_missing_semantics_warns_naming_alternatives(self, caplog):
        """A relation with no meaning is flagged, never refused."""
        import logging

        registry = PackRegistry("/tmp/nonexistent")
        with caplog.at_level(logging.WARNING, logger="tortoise.pack_registry"):
            errors = registry._validate({
                "namespace": "pm", "name": "PM",
                "ontology": {
                    "extends": "core",
                    "objectKinds": ["issue", "sprint"],
                    "relations": [{
                        "predicate": "blocks",
                        "from": "Object",
                        "to": "Object",
                    }],
                },
            })
        assert not any("semantics" in e for e in errors), errors
        assert "declares no semantics" in caplog.text
        assert "addresses" in caplog.text

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
                    "semantics": "addresses",
                    "nearMisses": ["supports"],
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

    def test_includes_semantics_and_near_misses_when_present(self, query_registry):
        relations = query_registry.list_relations()
        addr = [r for r in relations if r["predicate"] == "addresses"][0]  # noqa: RUF015
        assert addr["semantics"] == "addresses"
        assert addr["nearMisses"] == ["supports"]
        comp = [r for r in relations if r["predicate"] == "competesWith"][0]  # noqa: RUF015
        assert "semantics" not in comp
        assert "nearMisses" not in comp


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

    def test_from_kind_absent_namespace_is_unresolved_naming_the_pack(self, tmp_path):
        """#2781: an ABSENT namespace is a missing pack, not a typo — the pack
        loads and the reference is reported against the pack that is missing.
        (Pre-#2781 this isolated `dev`, which is how ONE absent pack took two
        or three others down with it.)"""
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
        loaded = registry.load_all()
        assert loaded == 1
        assert not registry.errors
        assert "dev" in registry.packs
        msgs = registry.unresolved_refs["dev"]
        assert any("nonexistent" in m and "not loaded" in m for m in msgs), msgs
        assert any("does not resolve" in m for m in msgs), msgs

    def test_to_kind_absent_namespace_is_unresolved_naming_the_pack(self, tmp_path):
        """Same clause for toKind."""
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
        loaded = registry.load_all()
        assert loaded == 1
        assert not registry.errors
        assert any("nonexistent" in m and "not loaded" in m
                   for m in registry.unresolved_refs["dev"])

    def test_unknown_kind_inside_a_PRESENT_pack_is_still_fatal(self, tmp_path):
        """The typo guard #2781 must not weaken: when the NAMESPACE resolves and
        only the kind is wrong, the reference is a misspelling of a kind that a
        loaded pack declares — the referrer is isolated. `thing:isue` is not a
        missing pack named `thing`."""
        d = str(tmp_path / "packs")
        os.makedirs(f"{d}/thing", exist_ok=True)
        with open(f"{d}/thing/manifest.yaml", "w") as f:
            yaml.dump({
                "namespace": "thing",
                "name": "Thing",
                "ontology": {"extends": "core", "objectKinds": ["issue"]},
            }, f)
        os.makedirs(f"{d}/dev", exist_ok=True)
        with open(f"{d}/dev/manifest.yaml", "w") as f:
            yaml.dump({
                "namespace": "dev",
                "name": "Development",
                "ontology": {
                    "extends": "core",
                    "objectKinds": ["epic"],
                    "relations": [{
                        "predicate": "addresses",
                        "fromKind": "dev:epic",
                        "toKind": "thing:isue",
                        "mechanism": "IMPL",
                    }],
                },
            }, f)
        registry = PackRegistry(d)
        loaded = registry.load_all()
        assert loaded == 1 and "thing" in registry.packs
        assert "dev" not in registry.packs
        assert any("thing:isue" in e and "does not resolve" in e
                   for e in registry.errors["dev"])
        assert "dev" not in registry.unresolved_refs

    def test_absent_namespace_reports_the_closest_loaded_pack(self, tmp_path):
        """The diagnostic is actionable: a near-miss namespace names the pack
        the author probably meant."""
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
                        "fromKind": "deve:issue",
                        "toKind": "dev:issue",
                        "mechanism": "IMPL",
                    }],
                },
            }, f)
        registry = PackRegistry(d)
        registry.load_all()
        assert any("did you mean 'dev'" in m
                   for m in registry.unresolved_refs["dev"])

    def test_declared_dependency_absent_is_reported_and_pack_stays_loaded(self, tmp_path):
        """#2781 indicator 3 — `depends_on` has a CONSUMER. A declared
        dependency that is not loaded is reported against the declaring pack,
        naming the missing pack; the declaring pack still loads (indicator 2)."""
        d = str(tmp_path / "packs")
        os.makedirs(f"{d}/isolated", exist_ok=True)
        with open(f"{d}/isolated/manifest.yaml", "w") as f:
            yaml.dump({
                "namespace": "isolated",
                "name": "Isolated",
                "ontology": {"extends": "core", "objectKinds": ["note"]},
                "depends_on": ["hr"],
            }, f)
        registry = PackRegistry(d)
        loaded = registry.load_all()
        assert loaded == 1
        assert not registry.errors
        msgs = registry.unresolved_refs["isolated"]
        assert any("'hr'" in m and "not loaded" in m for m in msgs), msgs

    def test_declared_dependency_present_reports_nothing(self, tmp_path):
        """A satisfied `depends_on` is silent — the check must not cost the
        catalog anything."""
        d = str(tmp_path / "packs")
        for ns in ("hr", "fund"):
            os.makedirs(f"{d}/{ns}", exist_ok=True)
            with open(f"{d}/{ns}/manifest.yaml", "w") as f:
                yaml.dump({
                    "namespace": ns,
                    "name": ns,
                    "ontology": {"extends": "core", "objectKinds": ["thing"]},
                    "depends_on": ["hr"] if ns == "fund" else [],
                }, f)
        registry = PackRegistry(d)
        assert registry.load_all() == 2
        assert not registry.errors and not registry.unresolved_refs

    def test_depends_on_must_be_a_list_of_non_empty_strings(self, tmp_path):
        """Shape guard: `depends_on: pm` (a bare string) would otherwise iterate
        as characters and report a dependency on 'p'."""
        registry = PackRegistry("/tmp/nonexistent")
        for bad in ("pm", ["pm", ""], [7], [None]):
            errors = registry._validate({
                "namespace": "x", "name": "X",
                "ontology": {"extends": "core", "objectKinds": ["thing"]},
                "depends_on": bad,
            })
            assert any("depends_on must be a list" in e for e in errors), (bad, errors)
        assert registry._validate({
            "namespace": "x", "name": "X",
            "ontology": {"extends": "core", "objectKinds": ["thing"]},
            "depends_on": None,
        }) == []

    def test_core_prefixed_typo_stays_fatal_even_with_a_missing_dep(self, tmp_path):
        """A `core:` ref never names a pack, so a declared-but-absent dependency
        must not excuse a core-kind typo."""
        d = str(tmp_path / "packs")
        os.makedirs(f"{d}/dev", exist_ok=True)
        with open(f"{d}/dev/manifest.yaml", "w") as f:
            yaml.dump({
                "namespace": "dev",
                "name": "Development",
                "ontology": {
                    "extends": "core",
                    "objectKinds": ["issue"],
                    "kindDefs": {"issue": {"nearMisses": ["core:NotAKind"]}},
                },
                "depends_on": ["hr"],
            }, f)
        registry = PackRegistry(d)
        assert registry.load_all() == 0
        assert any("core:NotAKind" in e for e in registry.errors["dev"])

    def test_bare_ref_unresolvable_without_a_missing_dep_is_fatal(self, tmp_path):
        """A bare name with no declared-but-absent dependency has no evidence
        that a pack is missing — it stays a typo, and the pack is isolated."""
        d = str(tmp_path / "packs")
        os.makedirs(f"{d}/dev", exist_ok=True)
        with open(f"{d}/dev/manifest.yaml", "w") as f:
            yaml.dump({
                "namespace": "dev",
                "name": "Development",
                "ontology": {
                    "extends": "core",
                    "objectKinds": ["issue"],
                    "kindDefs": {"issue": {"nearMisses": ["gadget"]}},
                },
            }, f)
        registry = PackRegistry(d)
        assert registry.load_all() == 0
        assert any("nearMisses target 'gadget'" in e
                   for e in registry.errors["dev"])

    def test_bare_ref_is_reported_when_a_declared_dep_is_absent(self, tmp_path):
        """The OTHER half of the clause: the same bare name is a reported gap
        (not a typo) once the pack declares a dependency it does not have — that
        declaration is what makes `depends_on` do work."""
        d = str(tmp_path / "packs")
        os.makedirs(f"{d}/dev", exist_ok=True)
        with open(f"{d}/dev/manifest.yaml", "w") as f:
            yaml.dump({
                "namespace": "dev",
                "name": "Development",
                "ontology": {
                    "extends": "core",
                    "objectKinds": ["issue"],
                    "kindDefs": {"issue": {"nearMisses": ["gadget"]}},
                },
                "depends_on": ["hr"],
            }, f)
        registry = PackRegistry(d)
        assert registry.load_all() == 1
        assert not registry.errors
        assert any("nearMisses target 'gadget'" in m
                   for m in registry.unresolved_refs["dev"])

    # ── #2781 regression: the shipped catalog must not cascade ──────────

    @pytest.mark.parametrize("victim", ["project-management", "product-strategy"])
    def test_dropping_one_catalog_pack_does_not_drop_its_referrers(
            self, tmp_path, victim):
        """The failure the issue names, pinned on the REAL catalog.

        Pre-#2781, dropping `project-management` (namespace `pm`) removed 3 of
        the 5 starter packs — `dev` and `product-strategy` went with it — because
        an unresolvable cross-pack reference ISOLATED the referring pack, and
        the isolation then cascaded through the fixpoint. Dropping
        `product-strategy` did the same to `dev` and `marketing`.

        Post-#2781 every other pack still loads: the absent one is reported
        against its referrers by name, and nothing is isolated.
        """
        import shutil

        src = Path(__file__).resolve().parent.parent / "packs"
        packs = tmp_path / "packs"
        shutil.copytree(src, packs,
                        ignore=shutil.ignore_patterns("_template"))
        shutil.rmtree(packs / victim)
        registry = PackRegistry(str(packs))
        loaded = registry.load_all()
        survivors = {"agent-ops", "dev", "marketing", "pm", "product-strategy"}
        survivors.discard({"project-management": "pm",
                           "product-strategy": "product-strategy"}[victim])
        assert not registry.errors, registry.errors
        assert survivors <= set(registry.packs), (
            f"dropping {victim} cascaded: loaded={sorted(registry.packs)}")
        assert loaded == len(registry.packs)
        # Every remaining pack is still addressable by kind expansion.
        assert "dev:issue" in registry._all_known_kinds()
        assert registry.expand_kind("dev:issue")

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

    def test_relation_near_misses_unresolvable_target_error(self, tmp_path):
        """A nearMisses target that is no predicate and no semantics → error."""
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
                        "toKind": "dev:issue",
                        "mechanism": "IMPL",
                        "nearMisses": ["noSuchPredicate"],
                    }],
                },
            }, f)
        registry = PackRegistry(d)
        registry.load_all()
        assert any(
            "nearMisses target 'noSuchPredicate'" in e
            for e in registry.errors.get("dev", [])
        )

    def test_relation_near_misses_resolves_to_semantics(self, tmp_path):
        """A nearMisses target may be another semantics value."""
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
                        "toKind": "dev:issue",
                        "mechanism": "IMPL",
                        "nearMisses": ["mutuallySupportive"],
                    }],
                },
            }, f)
        registry = PackRegistry(d)
        registry.load_all()
        assert not any(
            "nearMisses" in e for e in registry.errors.get("dev", [])
        )
