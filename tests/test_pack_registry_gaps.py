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
from tortoise.pack_registry import CORE_ALIASED_KINDS, CORE_SUBCLASSES
from tortoise.pack_registry import CANONICAL_EVENT_KINDS, CANONICAL_OBJECT_KINDS
from tortoise.pack_registry import CANONICAL_POINT_KINDS, PackRegistry
from tortoise.value_extractor import compile_kind_index_spec

REPO = Path(__file__).resolve().parent.parent
PACKS_DIR = REPO / "packs"


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


# ── Relation write-path verdicts (#2766) ──────────────────────────────────

def _verdict_registry(tmp_path, relations):
    """A one-pack registry carrying `relations`, for the verdict tests."""
    d = str(tmp_path / "packs")
    pack = {
        "namespace": "test",
        "name": "Test Pack",
        "version": "1.0.0",
        "tier": "free",
        "description": "verdict fixture",
        "ontology": {
            "extends": "core",
            "objectKinds": ["issue"],
            "relations": relations,
        },
    }
    os.makedirs(f"{d}/test", exist_ok=True)
    with open(f"{d}/test/manifest.yaml", "w") as f:
        yaml.dump(pack, f)
    registry = PackRegistry(d)
    registry.load_all()
    return registry


class TestRelationWriteVerdict:
    """The three verdicts, on fixtures — `predicate-not-in-allowlist` is the
    state the guard exists to make loud (declared but nothing can write it)."""

    def test_operator_path_is_writable(self):
        from tortoise.pack_registry import relation_write_verdict

        verdict, paths = relation_write_verdict(
            {"predicate": "groundedIn", "mechanism": "IMPL"})
        assert verdict == "writable"
        assert paths == ("operator",)

    def test_structural_path_is_writable_without_a_mechanism(self):
        """Old-format relations have no mechanism; a structural predicate is
        still a real write path (create_edge)."""
        from tortoise.pack_registry import relation_write_verdict

        verdict, paths = relation_write_verdict({"predicate": "hasPart"})
        assert verdict == "writable"
        assert paths == ("structural",)

    def test_structural_leg_counts_every_writer(self):
        """#8285 review P1: the `structural` leg must cover EVERY structural
        writer, not just `create_edge`'s set.

        `aboutPoint` is written by `create_about_edge` and `extractedFrom` by
        `sdk.ingest`; both are declarable with zero validation errors, so
        reporting them `predicate-not-in-allowlist` asserted a runtime the
        code contradicts. Before this fix the resolver read only
        `_VALID_EDGE_PREDICATES` (neither predicate is in it) and this fails.
        """
        from tortoise.pack_registry import relation_write_verdict

        for predicate in ("aboutPoint", "extractedFrom"):
            verdict, paths = relation_write_verdict({"predicate": predicate})
            assert verdict == "writable", (predicate, verdict)
            assert paths == ("structural",), (predicate, paths)

    def test_structural_leg_is_the_union_of_the_writers_sets(self):
        """Drift guard: the verdict must answer from the union of the writers'
        sets, so a predicate added to any single writer cannot be reported as
        unwritable again.

        Asserting the whole union (not just the two known misses) is what makes
        this fail on the pre-fix code for the right reason and keeps holding as
        the writers grow.
        """
        from tortoise.pack_registry import relation_write_verdict
        from tortoise.projection.edges import (
            _VALID_EDGE_PREDICATES,
            STRUCTURAL_REL_LABELS,
            STRUCTURAL_WRITE_PREDICATES,
        )

        # The union is exactly the writers' structural vocabulary.
        assert (
            _VALID_EDGE_PREDICATES | set(STRUCTURAL_REL_LABELS)
        ) == STRUCTURAL_WRITE_PREDICATES
        # ... and every member answers `writable` / `structural`.
        for predicate in sorted(STRUCTURAL_WRITE_PREDICATES):
            verdict, paths = relation_write_verdict({"predicate": predicate})
            assert verdict == "writable", (predicate, verdict)
            assert paths == ("structural",), (predicate, paths)
        # A predicate outside every writer is still reported — the union does
        # not turn the verdict permissive.
        assert relation_write_verdict({"predicate": "blocks"})[0] == \
            "predicate-not-in-allowlist"

    def test_shipped_docs_structural_list_matches_the_writers(self):
        """The docs tell authors which predicates are structural; the list must
        be the writers' union, not one writer's subset.

        Before this fix the list omitted `aboutPoint` and `extractedFrom`, so
        an author consulting it was told a real write path does not exist.
        """
        import re
        from pathlib import Path

        from tortoise.projection.edges import STRUCTURAL_WRITE_PREDICATES

        root = Path(__file__).resolve().parent.parent
        docs = (root / "docs" / "EXPANSION_PACKS.md").read_text()
        match = re.search(r"the writers accept \(([^)]*)\)", docs)
        assert match, "the structural predicate list moved — update this pin"
        listed = set(re.findall(r"`([^`]+)`", match.group(1)))
        assert listed == set(STRUCTURAL_WRITE_PREDICATES), (
            "docs structural list diverged from the writers",
            sorted(set(STRUCTURAL_WRITE_PREDICATES) ^ listed),
        )

    def test_declaration_only_is_reported_and_silent(self):
        from tortoise.pack_registry import relation_write_verdict

        verdict, paths = relation_write_verdict(
            {"predicate": "funds", "declarationOnly": True})
        assert verdict == "declaration-only"
        assert paths == ()

    def test_no_write_path_without_the_marker_is_not_in_allowlist(self):
        from tortoise.pack_registry import relation_write_verdict

        verdict, paths = relation_write_verdict({"predicate": "blocks"})
        assert verdict == "predicate-not-in-allowlist"
        assert paths == ()

    def test_declaration_only_defaults_off(self):
        """Absence of the marker must not silently count as declaration-only."""
        from tortoise.pack_registry import relation_write_verdict

        assert relation_write_verdict(
            {"predicate": "blocks", "declarationOnly": False}
        )[0] == "predicate-not-in-allowlist"

    def test_no_path_warns_at_pack_load(self, tmp_path, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="tortoise.pack_registry"):
            reg = _verdict_registry(tmp_path, [{
                "predicate": "blocks", "from": "Object", "to": "Object",
            }])
        assert "test" in reg.packs, reg.errors
        assert "the declaration binds no write path" in caplog.text
        assert "'blocks'" in caplog.text
        # The message names both routes out, so the author is not left stuck.
        assert "declarationOnly: true" in caplog.text
        assert "mechanism" in caplog.text
        # #8285 P1: the verdict is a DECLARATION check, not a runtime refusal.
        # `create_operator` accepts any label (warn-not-block, #1934) — measured
        # on 2026-10-02, #2766 — so the warning must not claim the runtime
        # refuses or drops the edge (that is the false claim this pins against).
        assert "warn-not-block" in caplog.text
        assert "declaration gap" in caplog.text
        assert "no runtime write path" not in caplog.text
        assert "silently drop" not in caplog.text

    def test_declaration_only_marker_loads_without_the_warning(self, tmp_path, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="tortoise.pack_registry"):
            reg = _verdict_registry(tmp_path, [{
                "predicate": "funds", "from": "Object", "to": "Object",
                "declarationOnly": True,
            }])
        assert "test" in reg.packs, reg.errors
        assert "no runtime write path" not in caplog.text
        assert "has no effect" not in caplog.text
        # ... and the discoverable API reports the author's choice, not a defect.
        assert [r["write_verdict"] for r in reg.list_relations()] == ["declaration-only"]

    def test_declaration_only_on_a_writable_relation_has_no_effect(self, caplog):
        """A marker the runtime contradicts must not pass in silence."""
        import logging

        registry = PackRegistry("/tmp/nonexistent")
        with caplog.at_level(logging.WARNING, logger="tortoise.pack_registry"):
            registry._validate({
                "namespace": "dev", "name": "Dev",
                "ontology": {
                    "extends": "core",
                    "objectKinds": ["issue"],
                    "relations": [{
                        "predicate": "hasPart", "from": "Object", "to": "Object",
                        "declarationOnly": True,
                    }],
                },
            })
        assert "has no effect" in caplog.text
        assert "structural" in caplog.text

    def test_declaration_only_must_be_a_boolean(self):
        registry = PackRegistry("/tmp/nonexistent")
        errors = registry._validate({
            "namespace": "dev", "name": "Dev",
            "ontology": {
                "extends": "core",
                "objectKinds": ["issue"],
                "relations": [{
                    "predicate": "blocks", "from": "Object", "to": "Object",
                    "declarationOnly": "yes",
                }],
            },
        })
        assert any("'declarationOnly' must be a boolean" in e for e in errors), errors

    def test_shipped_guidance_states_the_declaration_gap_not_a_refusal(self):
        """#8285 P1: the SHIPPED prose must not assert a runtime capability the
        writer does not enforce.

        `create_operator` accepts any label (warn-not-block, #1934); only
        `create_edge` is strict. Before this pin, the warning and
        `docs/EXPANSION_PACKS.md` claimed a non-mechanism relation had "no
        runtime write path" and would be "silently dropped" — both false, and
        measured false on 2026-10-02 (#2766). This test fails on that wording.
        """
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        docs = (root / "docs" / "EXPANSION_PACKS.md").read_text()
        template = (root / "packs" / "_template" / "manifest.yaml").read_text()

        for text, name in ((docs, "EXPANSION_PACKS.md"),
                           (template, "_template/manifest.yaml")):
            assert "warn-not-block" in text, name
            # The three runtime-refusal claims the review found false.
            assert "the runtime would silently drop it" not in text, name
            assert "nothing writes it" not in text, name
            assert "the runtime has no surface that can write" not in text, name
        # The docs must name the actual gap, not a refusal.
        assert "declaration" in docs and "gap" in docs
        # The `declarationOnly` path re-check: the marker silences the warning,
        # it does NOT stop the writer from accepting the label.
        assert "silences the warning" in docs
        assert "stop a caller from writing the label" in docs

    def test_list_relations_carries_the_verdict(self, query_registry):
        """The discoverable API must not assert a path the declaration does not
        bind."""
        relations = query_registry.list_relations()
        assert {r["write_verdict"] for r in relations} == {"writable"}
        assert {tuple(r["write_paths"]) for r in relations} == {("operator",)}

    def test_shipped_catalog_reports_the_legacy_relations(self):
        """Regression pin on the real packs: the legacy `pm` rows are RESOLVED,
        not silenced (#11083).

        This pin used to assert the opposite — that all four `pm` rows
        (`blocks`, `partOfSprint`, `assignedTo`, `onBoard`) came back
        `predicate-not-in-allowlist`. #11083 resolved them under #2766's
        three-layer convention: three gain a `mechanism` + a canonical meaning
        (so they bind the operator/structural write path), and `onBoard` — a
        VIEW over cards, recomputed from `cardMoved` — keeps its declaration
        with `declarationOnly: true` and a printed reason.

        What must stay true, and is asserted here:
          * the declared POPULATION does not shrink to make the count green
            (36 relations before and after — nothing deleted, #11083 Target);
          * no shipped row is `predicate-not-in-allowlist` — the state #2766
            calls "the declaration gap" must be empty in the shipped catalog;
          * the one acknowledged declaration is `onBoard`, and it carries the
            reason the census prints (`declarationOnlyReason`).
        """
        from tortoise.pack_registry import (
            RELATION_WRITE_VERDICTS,
            default_packs_dir,
        )

        reg = PackRegistry(default_packs_dir())
        reg.load_all()
        relations = reg.list_relations()
        # Every verdict is one the schema declares — the vocabulary is closed.
        assert {r["write_verdict"] for r in relations} <= set(RELATION_WRITE_VERDICTS)
        assert len(relations) == 36, (
            "the declared relation population must not be reduced by deleting "
            f"a declaration to make the verdict counts green, got {len(relations)}"
        )
        reported = {
            (r["pack"], r["predicate"])
            for r in relations
            if r["write_verdict"] == "predicate-not-in-allowlist"
        }
        assert reported == set(), reported
        acknowledged = {
            (r["pack"], r["predicate"])
            for r in relations
            if r["write_verdict"] == "declaration-only"
        }
        assert acknowledged == {("pm", "onBoard")}, acknowledged
        on_board = next(
            r for r in relations
            if r["pack"] == "pm" and r["predicate"] == "onBoard"
        )
        assert on_board.get("declarationOnlyReason"), (
            "`declarationOnly: true` without a printable reason is a SILENCER, "
            "not a disposition (#11083 R7)"
        )

        # ⛔ The DOMAIN WORDS must SURVIVE. #2766's convention — restated as the
        # owner's ruling for these exact rows (#11083 comment 6714848519,
        # 2026-11-08) — is "keep each domain word as the **label**, give it the
        # canonical layer-2 meaning, and it becomes writable". A rename to the
        # meaning's own name DELETES a word: `CORE_OPERATOR_LABELS` unions
        # `SEMANTICS_VALUES` with each pack's `relations[].predicate`, so a caller
        # (or the extractor) emitting `blocks` would hit `undeclared_relation` and
        # be told to use a different word. This assertion used to work; it was
        # rewritten to population COUNTS, which cannot see a rename — and the
        # rename then landed with nothing red anywhere in the tree.
        pm_rows = {r["predicate"]: r for r in relations if r["pack"] == "pm"}
        assert set(pm_rows) == {
            "blocks", "partOfSprint", "assignedTo", "onBoard"}, (
            "a `pm` domain word was deleted or renamed — the ruling keeps every "
            f"label and changes only its MEANING, got {sorted(pm_rows)}")
        assert pm_rows["blocks"]["semantics"] == "dependsOn"
        assert pm_rows["blocks"]["effective_direction"] == "<-", (
            "'A blocks B' means B depends on A — the ruled direction flip; "
            "`dependsOn`'s table default `->` transfers confidence "
            "blocker→blocked, the inverse of the truth")
        assert pm_rows["partOfSprint"]["semantics"] == "hasPart"
        assert pm_rows["assignedTo"]["semantics"] == "managedBy"

        # ⛔ #11083's two new discoverable cells, which nothing read. Deleting
        # `entry["effective_direction"]` (or `entry["direction"]`) from
        # `list_relations()` reddened NOTHING before this assertion — on the cell
        # the module comment calls the one that keeps "the discoverable API" from
        # asserting a relation is bound to a sense its declaration does not bind.
        holds = next(
            r for r in relations
            if (r["pack"], r["predicate"]) == ("venture", "holds")
        )
        assert holds["direction"] == "<-", (
            "the DECLARED per-edge sense (ruling (c)) is not surfaced")
        assert holds["effective_direction"] == "<-", (
            "the resolved sense is not surfaced — or it let `ownedBy`'s table "
            "default `->` outrank the declaration")


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

    def test_relation_near_miss_on_an_absent_pack_is_reported_not_fatal(
            self, tmp_path):
        """#9663: a RELATION `nearMisses` target resolves through the SAME #2781
        gate the relation endpoints use.

        A pack may legitimately name another pack's PREDICATE as the sibling it
        is confused with (`constrainedBy` vs venture's `gatedBy`). Before this,
        relation `nearMisses` wrote straight to `self.errors` — so a pack that
        declared the dependency was ISOLATED for it, and `unresolved_refs`
        stayed empty: the whole pack was dropped with no diagnostic naming the
        pack to install. Measured on the shipped catalog with `venture` removed,
        `portfolio` was dropped and `unresolved_refs` was `{}`.
        """
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
                        "nearMisses": ["gatedBy"],
                    }],
                },
                "depends_on": ["hr"],
            }, f)
        registry = PackRegistry(d)
        assert registry.load_all() == 1
        assert not registry.errors
        assert any("nearMisses target 'gatedBy'" in m
                   for m in registry.unresolved_refs["dev"])
        assert any("hr" in m for m in registry.unresolved_refs["dev"])

    def test_relation_near_miss_typo_without_a_dep_stays_fatal(self, tmp_path):
        """The typo guard is NOT weakened by the #2781 routing: an unknown
        predicate with no declared-but-absent dependency has no evidence that a
        pack is missing, so the pack is still isolated."""
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
                        "nearMisses": ["gadget"],
                    }],
                },
            }, f)
        registry = PackRegistry(d)
        assert registry.load_all() == 0
        assert any("nearMisses target 'gadget'" in e
                   for e in registry.errors["dev"])

    def test_a_namespaced_near_miss_is_fatal_even_without_the_sibling(
            self, tmp_path):
        """A NAMESPACED relation `nearMisses` target is fatal while predicates
        and semantics values are declared BARE (the #2030 contract).

        The #2781 gate is right for a bare ref: the pack may legitimately be
        naming a SIBLING pack's predicate, so an absent `hr` explains `gatedBy`
        away as "install pack 'hr'". A namespaced target matches no predicate or
        semantics value a bare-conforming catalog declares, so that advice cannot
        make it resolve — and once `pack validate` reads `unresolved_refs` into
        its verdict (#9663) the advice would also turn a TYPO into an
        installable, fixable gap.

        `bare_only=True` at that one call site keeps it fatal. FAIL-ON: drop
        that argument and this pack LOADS (0 → 1) with the ref filed as a
        survivable gap; the bare case above must keep loading, so the guard
        narrows nothing else.

        The one escape is a pack that itself declares a namespaced PREDICATE
        (`_validate` only checks `pred[0].islower()`, so one is accepted and
        then resolves such a ref). No shipped pack does; rejecting one is a
        behaviour change filed as #9760. Do not restate this docstring as
        "in every catalog" — measured false on such a catalog.
        """
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
                        # `venture` is absent from this catalog, and it would
                        # not help if it were present.
                        "nearMisses": ["venture:holds"],
                    }],
                },
                "depends_on": ["venture"],
            }, f)
        registry = PackRegistry(d)
        assert registry.load_all() == 0, registry.unresolved_refs
        assert any("nearMisses target 'venture:holds'" in e
                   for e in registry.errors["dev"])
        assert "dev" not in registry.packs

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


# ── #11000 — the kind expansion table: superclass + CORE hierarchy ──────────
#
# TWO independent defects lived in `_build_kind_expansions`, and each one is
# pinned separately below so neither can be "fixed" by accident and neither can
# regress behind the other's green:
#
#   Leg A — a pack child never expanded to its declared `subclassOf` parent.
#           The loop bound `parent` and never read it (the B007
#           unused-loop-variable suppression was the tell) while the comment above it claimed "children also expand to
#           parent". Measured at 86aaef8f0271: `expand_kind("dev:issue")` →
#           `['dev:issue', 'pm:issue']` (the `equivalentTo` peer only), and
#           `expand_kind("dev:bug")` → `['dev:bug']`.
#
#   Leg B — the CORE hierarchy (ONTOLOGY §6) was never a source for the table at
#           all. `get_subclasses` read only pack-declared `kind_subclasses`, so
#           `expand_kind("Object") == ["Object"]` and `get_subclasses("Subject")
#           == []` while §6 declares 16 Object subclasses and 5 Subject ones.
#           This is a hard dependency of #11656, whose gate must accept a
#           declared `toKind: Subject` as "a Subject or any subtype".
#
# Population is always DERIVED (from the loaded registry, from §6/§5 parsed out
# of the doc, or from the registry's own declarations) — never a hand-copied
# list — so a new pair or a new kind joins the pin automatically instead of
# silently leaving coverage.

_S6_SECTION = "## §6. Subclass Model"


def _ontology_s6_table() -> dict[str, str]:
    """ONTOLOGY §6's "Core subclasses (no pack needed)" table → {parent: cell}.

    Parsed from the doc rather than restated, so a §6 edit that the code does not
    follow reds here instead of drifting silently. The doc is owner-gated
    (`CRITICAL RULE` at its head), which is exactly why the CODE is pinned to it
    rather than the other way round.
    """
    text = (REPO / "docs" / "ONTOLOGY.md").read_text(encoding="utf-8")
    section = text.split(_S6_SECTION, 1)[1]
    table: dict[str, str] = {}
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            if table:  # the table ended
                break
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        parent, children = cells[0], cells[1]
        if not parent or set(parent) <= set("-: ") or parent == "Parent":
            continue
        table[parent] = children
    return table


def _kinds_in_cell(cell: str) -> set[str]:
    return {tok for tok in cell.replace("*", "").replace("`", "")
            .replace(",", " ").split() if tok}


def _ontology_fenced(header: str) -> set[str]:
    """Parse a §5 vocabulary fenced block (comments and prose stripped)."""
    text = (REPO / "docs" / "ONTOLOGY.md").read_text(encoding="utf-8")
    after = text.split(header, 1)[1]
    block = after.split("```", 2)[1]
    block = block.split("\n", 1)[1] if "\n" in block else ""
    kinds: set[str] = set()
    for line in block.splitlines():
        line = line.split("#", 1)[0]
        kinds.update(tok for tok in line.replace(",", " ").split() if tok)
    return kinds


@pytest.fixture(scope="module")
def shipped() -> PackRegistry:
    """The 7 shipped packs, loaded once."""
    registry = PackRegistry(str(PACKS_DIR))
    assert registry.load_all() == 7, registry.errors
    assert not registry.errors, registry.errors
    return registry


class TestCoreHierarchy:
    """#11000 — the declared kind hierarchy actually reaches `expand_kind`."""

    # ── Leg A: pack child → its declared parent ──────────────────────────

    def test_every_declared_parent_is_in_its_child_expansion(self, shipped):
        """For EVERY declared `subclassOf` pair, the parent is in the child's bucket.

        The failure this prevents: the append being dropped again (the original
        defect), a new declared pair landing half-wired, or the widening being
        applied in one namespace only. The population is derived from the loaded
        registry — 17 pairs across 7 packs today — so it cannot pass vacuously
        and a newly declared pair is covered without touching this test.
        """
        pairs = [(f"{p.namespace}:{child}", parent)
                 for p in shipped.packs.values()
                 for child, parent in p.kind_subclasses.items()]
        assert len(pairs) >= 17, f"declared subclassOf pairs went missing: {pairs}"
        assert len({c.split(":", 1)[0] for c, _ in pairs}) >= 2, pairs

        missing = [
            (child, parent, shipped.expand_kind(child))
            for child, parent in pairs
            if parent not in shipped.expand_kind(child)
        ]
        assert not missing, (
            "a declared `subclassOf` parent is absent from its child's expansion "
            "— the declaration is loaded and validated but widens nothing "
            f"(#11000 Leg A): {missing}")

        # …and in the spelling rows are actually STORED under. The classifier's
        # candidate index spells core kinds `core:WorkItem` (extractor_v2
        # .CORE_OBJECT_KEYS) and the open doors accept that spelling, so a bucket
        # carrying only the bare form matches nothing on the `search`/object path.
        missing_core = [
            (child, parent) for child, parent in pairs
            if f"core:{parent}" not in shipped.expand_kind(child)
        ]
        assert not missing_core, (
            "the child's expansion carries the bare parent but not the parent's "
            "`core:` spelling, so a row stored under the classifier's spelling is "
            f"invisible: {missing_core}")

    def test_a_child_gains_only_its_own_parent(self, shipped):
        """…and NOT its parent's other children.

        The failure this prevents: widening done by UNIONING the parent's bucket
        instead of naming the parent. That looks green on the test above while
        quietly turning `expand_kind('dev:issue')` into "every WorkItem", so a
        read for issues returns pull requests, cards and features — a
        wrong-BROAD answer, the mirror of the defect being fixed.

        ⚠ It deliberately does NOT forbid the parent's PARENT: the closure is
        depth 2 by design (`dev:issue` gains `WorkItem` AND `Object`), and the
        transitive UP direction is pinned by
        `test_the_closure_is_depth_two_because_a_declared_parent_is_a_core_child`.
        """
        declared_parents = {parent for p in shipped.packs.values()
                            for parent in p.kind_subclasses.values()}
        assert declared_parents >= {"Project", "WorkItem", "Problem"}, declared_parents

        over_wide = []
        for p in shipped.packs.values():
            for child, parent in p.kind_subclasses.items():
                bucket = set(shipped.expand_kind(f"{p.namespace}:{child}"))
                gained = bucket & declared_parents
                if gained != {parent}:
                    over_wide.append((f"{p.namespace}:{child}", parent, sorted(gained)))
        assert not over_wide, (
            "a child's expansion names a declared parent that is not its own — "
            f"the widening copied the parent's bucket instead of its name: {over_wide}")

    # ── Leg B: the CORE hierarchy (ONTOLOGY §6) ────────────────────────

    def test_the_s6_core_table_matches_the_declared_parentage(self):
        """ONTOLOGY §6's rows ARE `CORE_SUBCLASSES` — parsed, not restated.

        The failure this prevents: the core parentage being a second, drifting
        copy of the §5 vocabulary. §6 is owner-gated (no edit without Daniel's
        approval of the exact change), so the CODE is pinned to the doc: a §6
        row that changes without the code following, or a code row edited without
        §6, reds here and names both sides.
        """
        table = _ontology_s6_table()

        # §6's row set is pinned EXACTLY, in both directions — a `>=` here would
        # let a NEW §6 row (say `Source`) land while this pin stayed green, which
        # is precisely the Leg-B failure mode (declared in the doc, absent from
        # `CORE_SUBCLASSES`, so it widens nothing). The two non-empty differences
        # that remain are today's deliberate exceptions, named:
        #   * `Document` — §6's row is the prose statement that a document is a
        #     `:Source`, not an Object subclass (§4.4), so there is no code row;
        #   * `Point` / `Event` — §6 has no row for either, so the code derives
        #     them from §5 (``CANONICAL_POINT_KINDS`` / ``CANONICAL_EVENT_KINDS``,
        #     pinned by `test_point_and_event_rows_are_the_s5_vocabularies`).
        # Giving §6 those rows is an ONTOLOGY edit, which is owner-gated
        # (`docs/ONTOLOGY.md` — no edit without Daniel's approval), so this pin
        # reports the gap rather than quietly diverging.
        assert set(table) - set(CORE_SUBCLASSES) == {"Document"}, (
            "ONTOLOGY §6 declares a core parentage the code does not carry: "
            f"{sorted(set(table) - set(CORE_SUBCLASSES))}")
        assert set(CORE_SUBCLASSES) - set(table) == {"Point", "Event"}, (
            "CORE_SUBCLASSES carries a row ONTOLOGY §6 does not declare: "
            f"{sorted(set(CORE_SUBCLASSES) - set(table))}")

        assert _kinds_in_cell(table["Object"]) == set(CORE_SUBCLASSES["Object"]), (
            "ONTOLOGY §6's Object row and CORE_SUBCLASSES['Object'] disagree: "
            f"doc-only={sorted(_kinds_in_cell(table['Object']) - set(CORE_SUBCLASSES['Object']))}, "
            f"code-only={sorted(set(CORE_SUBCLASSES['Object']) - _kinds_in_cell(table['Object']))}")
        assert _kinds_in_cell(table["Subject"]) == set(CORE_SUBCLASSES["Subject"]), (
            "ONTOLOGY §6's Subject row and CORE_SUBCLASSES['Subject'] disagree: "
            f"doc-only={sorted(_kinds_in_cell(table['Subject']) - set(CORE_SUBCLASSES['Subject']))}, "
            f"code-only={sorted(set(CORE_SUBCLASSES['Subject']) - _kinds_in_cell(table['Subject']))}")

        # §6 says Document is NOT a subclass of Object, and its row is prose
        # saying so — so `Document` must have no core children (its pack
        # subclasses still expand; see the next test).
        assert "not a subclass" in table["Document"].lower(), table["Document"]
        assert "Document" not in CORE_SUBCLASSES, CORE_SUBCLASSES.get("Document")

        # `Object` is the §5 object block, unchanged — the two constants must not
        # diverge now that one is derived from the other.
        assert set(CORE_SUBCLASSES["Object"]) == set(CANONICAL_OBJECT_KINDS)

    def test_the_two_spellings_of_one_kind_answer_a_read_the_same(self, shipped):
        """A bare short name carries its namespaced forms' WHOLE bucket.

        The failure this prevents: the widening reaching `dev:issue` but not the
        bare `issue` the same row can be stored under, so `query(kind="issue")`
        matches strictly less than `query(kind="dev:issue")` for one kind — the
        spelling asymmetry this PR removes for `core:`, left standing for the
        bare form. The population is derived (every namespaced kind in the loaded
        registry), so a new pack kind is covered without touching this test.
        """
        def bare_of(full: str) -> str:
            return full.split(":", 1)[1]

        narrower = []
        for full in shipped._kind_expansions:
            if ":" not in full:
                continue
            full_bucket = set(shipped.expand_kind(full))
            bare_bucket = set(shipped.expand_kind(bare_of(full)))
            if not full_bucket <= bare_bucket:
                narrower.append((full, bare_of(full),
                                 sorted(full_bucket - bare_bucket)))
        assert not narrower, (
            "a bare kind spelling answers NARROWER than its namespaced form, so "
            "one kind read two ways returns different sets: "
            f"{narrower[:10]}")

        # The measured symptoms, so a future reordering is caught by name.
        assert {"WorkItem", "core:WorkItem"} <= set(shipped.expand_kind("issue"))
        assert {"Project", "core:Project"} <= set(shipped.expand_kind("program"))
        # …and the bare form itself is still present (#6146) — the widening is
        # additive, never a replacement.
        assert "issue" in shipped.expand_kind("issue")

    def test_every_declared_parent_is_a_classifier_candidate(self, shipped):
        """The parent `expand_kind` widens to is one the classifier can emit.

        #11000's own indicator is that the index spec includes the parent for
        every declared `subclassOf` pair (issue #11000, Targets). `expand_kind`
        is not the only consumer of the hierarchy — the classifier's candidate
        index is the other — so this MEASURES the indicator instead of asserting
        it in prose, and names the one exception rather than claiming "no index
        edit is owed" in general.

        The exception: `product-strategy:architecture` → `Document`. `Document`
        is deliberately not in `CORE_SUBCLASSES` (a document is a `:Source`,
        §4.4) and is not a classifiable kind at all — a document is classified
        by its `documentKind` genre (§5), so no `core:Document` candidate exists
        or is owed.
        """
        spec = compile_kind_index_spec(packs_dir=PACKS_DIR)
        assert spec, "the classifier kind index came back empty"
        assert len(spec) > 50, sorted(spec)[:10]

        parents = {parent for p in shipped.packs.values()
                   for parent in p.kind_subclasses.values()}
        assert parents >= {"Project", "WorkItem", "Problem", "Document"}, parents

        missing = sorted(parent for parent in parents
                         if f"core:{parent}" not in spec)
        assert missing == ["Document"], (
            "a declared `subclassOf` parent is not a classifier candidate, so a "
            "narrative of that kind can never be classified to it: "
            f"{missing}")
        assert not [k for k in spec if k.split(":", 1)[-1] == "Document"], (
            "`Document` became a classifiable kind — this pin, and the plan's "
            "`Document` exception, are stale")

    def test_the_subject_row_is_the_one_authority(self):
        """`CORE_SUBCLASSES['Subject']` is `kind_layers.subject_kind_names()`.

        The failure this prevents: a SECOND answer to "what is a §5 Subject
        kind". #9964 made `kind_layers` the one authority for the kind↔layer
        question; this row is spelled in `pack_registry` (importing that
        authority would pull `extractor_v2` onto the pack-load path), so the two
        are pinned together. A subject kind added to `extractor_v2.SUBJECTS` and
        not here would otherwise leave `expand_kind('Subject')` narrow with every
        test green.
        """
        from tortoise.kind_layers import subject_kind_names

        declared = {k.split(":", 1)[1] if k.startswith("core:") else k
                    for k in subject_kind_names()}
        assert declared, "the subject vocabulary came back empty — pin is vacuous"
        assert set(CORE_SUBCLASSES["Subject"]) == declared, (
            "the Subject hierarchy diverged from kind_layers.subject_kind_names(): "
            f"code-only={sorted(set(CORE_SUBCLASSES['Subject']) - declared)}, "
            f"authority-only={sorted(declared - set(CORE_SUBCLASSES['Subject']))}")

    def test_point_and_event_rows_are_the_s5_vocabularies(self):
        """§6 has no Point/Event row, so they follow §5 — and the gap is NAMED.

        The failure this prevents: silently inheriting a wider or narrower set
        than §5 declares. §5's point block carries eleven names while the
        pack-facing set carries nine; the two extra (`humanApproval`, `event`)
        are the #1013 option-B gate-only exception, pinned here as an EXACT
        difference so a third name cannot join it unnoticed.
        """
        assert set(CORE_SUBCLASSES["Point"]) == set(CANONICAL_POINT_KINDS), (
            f"Point row drifted: {sorted(set(CORE_SUBCLASSES['Point']) ^ set(CANONICAL_POINT_KINDS))}")
        assert set(CORE_SUBCLASSES["Event"]) == set(CANONICAL_EVENT_KINDS), (
            f"Event row drifted: {sorted(set(CORE_SUBCLASSES['Event']) ^ set(CANONICAL_EVENT_KINDS))}")

        s5_point = _ontology_fenced("### Point Kind Vocabulary (core)")
        assert s5_point, "the §5 point block parsed empty — pin is vacuous"
        assert s5_point - set(CORE_SUBCLASSES["Point"]) == {"humanApproval", "event"}, (
            "the §5-point-vs-hierarchy gap is no longer exactly the two documented "
            f"gate-only kinds (#1013 option B): {sorted(s5_point - set(CORE_SUBCLASSES['Point']))}")
        s5_event = _ontology_fenced("### Event Kind Vocabulary (core)")
        assert s5_event, "the §5 event block parsed empty — pin is vacuous"
        assert s5_event == set(CORE_SUBCLASSES["Event"]), (
            f"§5's event block and the Event row disagree: {sorted(s5_event ^ set(CORE_SUBCLASSES['Event']))}")

    def test_core_roots_expand_to_their_core_subclasses(self, shipped):
        """`expand_kind(<core root>)` carries §6's/§5's children, in both spellings.

        The failure this prevents: the Leg-B half of the original defect —
        `expand_kind('Object') == ['Object']` and `get_subclasses('Subject') ==
        []` — returning. `#11656` reads this table to accept a declared
        `toKind: Subject` as "a Subject or any subtype"; an empty answer there
        turns every valid subject link into a false refusal.
        """
        for parent, children in CORE_SUBCLASSES.items():
            expanded = shipped.expand_kind(parent)
            assert parent in expanded, (parent, expanded)
            absent = sorted(set(children) - set(expanded))
            assert not absent, (
                f"expand_kind({parent!r}) is missing its declared core subclasses "
                f"({absent}) — the core hierarchy is not a source for the table")
            absent_core = sorted(f"core:{c}" for c in children
                                 if f"core:{c}" not in expanded)
            assert not absent_core, (
                f"expand_kind({parent!r}) is missing the `core:` spelling of "
                f"{absent_core} — a row stored under the classifier's spelling is invisible")
            assert set(shipped.get_subclasses(parent)) >= set(children), (
                f"get_subclasses({parent!r}) dropped the core half")

        # The measured symptoms, verbatim — a root with no pack subclasses at all
        # must still widen (Subject/Object had none, which is why they read []).
        assert set(shipped.expand_kind("Subject")) >= {
            "organization", "team", "role", "legalPerson", "naturalPerson",
            "core:team",
        }
        assert len(shipped.expand_kind("Object")) > 1
        for root in ("Point", "Event"):
            assert len(shipped.expand_kind(root)) > 1, root

    def test_document_keeps_its_pack_subclasses_and_gains_no_genre_axis(self, shipped):
        """§6: a document is a `:Source`, NOT an Object subclass — so no genre axis.

        The failure this prevents: "completing" the table by treating the
        `documentKind` genre vocabulary (`planDoc`, `research`, …) as Document
        subclasses. Those are genres of a `:Source` (§5), not kinds of the
        Document entity type, and widening Document to them would make
        `search(kind='Document')` return every plan and transcript as a
        document-kind match. The pack-declared subclass must keep working.
        """
        expanded = set(shipped.expand_kind("Document"))
        assert "Document" in expanded
        assert "product-strategy:architecture" in expanded, (
            "the PACK-declared Document subclass stopped expanding (§6.2a regression)")
        from tortoise.pack_registry import CANONICAL_DOCUMENT_KINDS

        genres = set(CANONICAL_DOCUMENT_KINDS) - {"Document"}
        assert genres, "the document genre axis came back empty — pin is vacuous"
        leaked = sorted(genres & expanded)
        assert not leaked, (
            "the Document expansion carries documentKind GENRES as if they were "
            f"subclasses (§6: a document is a :Source): {leaked}")

    # ── the two spellings of one core kind ────────────────────────────

    def test_core_prefixed_and_bare_spellings_are_one_kind(self, shipped):
        """`core:WorkItem` and `WorkItem` expand to the same kind.

        The failure this prevents: fixing the missing parent while leaving the
        fix INERT for the spelling that is actually emitted/stored. The
        classifier's candidate index spells core kinds `core:<Kind>`
        (`extractor_v2.CORE_OBJECT_KEYS`/`SUBJECTS`/`EVENTS`), `create_object`/
        `create_subject` accept that spelling, and `subject_binding._normalize_kind`
        already folds it away for comparison — the expansion table was the one
        place that told the two apart.
        """
        assert CORE_ALIASED_KINDS, "alias universe is empty — pin is vacuous"
        for kind in sorted(CORE_ALIASED_KINDS):
            core = f"core:{kind}"
            bare_bucket = set(shipped.expand_kind(kind))
            core_bucket = set(shipped.expand_kind(core))
            assert kind in bare_bucket and core in bare_bucket, (kind, sorted(bare_bucket))
            assert kind in core_bucket and core in core_bucket, (core, sorted(core_bucket))
            assert bare_bucket <= core_bucket, (
                f"expand_kind({core!r}) dropped members of expand_kind({kind!r}): "
                f"{sorted(bare_bucket - core_bucket)}")
            assert core_bucket <= bare_bucket, (
                f"expand_kind({kind!r}) dropped members of expand_kind({core!r}): "
                f"{sorted(core_bucket - bare_bucket)}")

        # The Subject vocabulary is the reason the alias universe is not just
        # `CORE_KINDS`: `organization`/`team`/… are §5 Subject kinds that no
        # canonical set holds, yet `core:team` is exactly what SUBJECTS declares.
        assert "team" in CORE_ALIASED_KINDS
        assert "core:team" in shipped.expand_kind("team")

        # STORED LEGACY SPELLINGS — an OUTCOME pin, not an ordering rule (#11000
        # R3, corrected by the post-merge review of #11667): `core:deployment`
        # must carry `dev:deployment`, because pre-#7848 rows are stored under the
        # legacy spelling and a read keyed on the relation's spelling has to find
        # them. The recovery comes from the EQUIVALENCE seeding —
        # `_equivalent(kind, f"core:{kind}")` plus `LEGACY_KIND_ALIASES` recorded
        # as equivalences — so it holds whichever order those constants are
        # folded in, and nothing has to run "after `LEGACY_KIND_ALIASES`". This
        # used to state the reverse ("the alias pass must run AFTER … or a stored
        # legacy spelling is dropped"), which measured false: the assertion below
        # stayed green with the ordering reversed AND with the copy pass deleted,
        # so the pin never depended on that pass and the prose misled readers
        # about where the recovery comes from. Measured in that review: the
        # deleted pass appended 0 members to 0 of 311 buckets.
        assert "dev:deployment" in shipped.expand_kind("core:deployment")

    def test_a_pack_kind_registered_under_a_core_name_round_trips_both_spellings(
            self, tmp_path):
        """A pack may declare a kind whose BARE name is a core alias (`team`).

        The failure this prevents: the two spellings diverging for a shape the
        SHIPPED packs do not have, so the equality above passed vacuously for it.
        A manifest with `ontology.objectKinds: [team]` is ACCEPTED
        (`canonical_kind_axes("team")` is empty, so the #2786 collision guard does
        not fire), and it puts `synth:team` in `expand_kind("team")`. With the
        bare-inherited members not round-tripping into the `core:` bucket,
        `expand_kind("core:team")` missed them — the same one-kind-two-answers
        asymmetry this PR exists to remove.

        Reachability is narrow: of the 73 `CORE_ALIASED_KINDS` members only five
        are registrable this way (`team`, `role`, `organization`, `legalPerson`,
        `naturalPerson` — the children of §5's `Subject` row). The other 68 are
        either owned by a canonical axis (the #2786 guard refuses
        `objectKinds: [plan]`) or PascalCase (`Subject`, `Object`, …) and refused
        by the camelCase shape check.
        """
        (tmp_path / "synth").mkdir()
        (tmp_path / "synth" / "manifest.yaml").write_text(
            "name: Synthetic\n"
            "namespace: synth\n"
            "version: 1.0.0\n"
            "tier: free\n"
            "ontology:\n"
            "  extends: core\n"
            "  objectKinds: [team]\n"
        )
        registry = PackRegistry(str(tmp_path))
        assert registry.load_all() == 1, registry.errors
        assert not registry.errors, registry.errors

        bare = set(registry.expand_kind("team"))
        core = set(registry.expand_kind("core:team"))
        assert "synth:team" in bare, bare
        assert bare <= core, (
            "the two spellings of one kind answer differently for a pack kind "
            f"registered under an aliased core name: {sorted(bare - core)}")
        assert core <= bare, sorted(core - bare)

    def test_a_multi_segment_equivalent_to_target_does_not_chain_into_a_bucket(
            self, tmp_path):
        """A colon-bearing `equivalentTo` target must not reach a bucket's closure.

        The failure this prevents is a silent WRONG-WIDE read. `bare_inherits` is
        keyed by the tail of a single-segment `ns:kind` name, and the closure
        closes in one hop because a value is never itself a key. A multi-segment
        target breaks that: `x:synth:team` has tail `synth:team`, which IS a key
        (for the real `synth:team` kind), so the closure chained `synth:widget` —
        a kind in no relation to `team` — into `expand_kind("team")`, i.e. into
        the read path's `IN` clause.

        `expand_kind("core:team")` is spared at this head, but NOT by the guard:
        the closure takes one round, so it reaches `core:team → team →
        synth:team` and never `synth:team → x:synth:team`. Both spellings are
        asserted below anyway — a future fixpoint that ALSO dropped the guard
        would widen `core:team` too, and that leg is what would catch it. (A
        fixpoint alone changes nothing: with the guard in place this loop is
        already at its fixpoint after one round — measured.)

        `equivalentTo` only has to CONTAIN ':' (an unresolved target is a warning
        and the pack stays loaded), so this shape is accepted by the registry.
        `bare_to_full` therefore admits single-segment names only; the rest stay
        in `expansions` and are still chained to their declared `equivalentTo`
        peer, which is correct — they simply never seed a `bare_inherits` chain.
        """
        (tmp_path / "synth").mkdir()
        (tmp_path / "synth" / "manifest.yaml").write_text(
            "name: Synthetic\n"
            "namespace: synth\n"
            "version: 1.0.0\n"
            "tier: free\n"
            "ontology:\n"
            "  extends: core\n"
            "  objectKinds: [team, widget]\n"
            "  equivalentTo:\n"
            "    widget: [x:synth:team]\n"
        )
        registry = PackRegistry(str(tmp_path))
        assert registry.load_all() == 1, registry.errors

        for kind in ("team", "core:team"):
            bucket = set(registry.expand_kind(kind))
            assert "synth:widget" not in bucket, (
                f"an unrelated kind chained into the `{kind}` bucket through a "
                f"multi-segment equivalentTo target: {sorted(bucket)}")
            assert "x:synth:team" not in bucket, sorted(bucket)
            # …and the round-trip this whole class exists for still holds.
            assert "synth:team" in bucket, sorted(bucket)

    def test_get_subclasses_folds_the_core_prefix_like_expand_kind(self, shipped):
        """`get_subclasses` answers the `core:` spelling too, not a wrong-empty.

        The failure this prevents: the SAME asymmetry the spelling pass above
        removes from `expand_kind`, left in the method that table is built from.
        Measured before the fold: `get_subclasses("core:Subject") == []` while
        `expand_kind("core:Subject")` returned the whole Subject bucket, and
        `get_subclasses("core:WorkItem") == []` while `expand_kind("core:WorkItem")`
        reached `dev:issue`. A caller that spells a kind the way the classifier
        and the write doors do (``extractor_v2.SUBJECTS`` is ``core:organization``)
        then gets a wrong-empty instead of the subclasses.
        """
        for parent in CORE_SUBCLASSES:
            assert shipped.get_subclasses(f"core:{parent}") == shipped.get_subclasses(parent)
            assert set(shipped.get_subclasses(f"core:{parent}")) >= set(
                CORE_SUBCLASSES[parent])
        assert "dev:issue" in shipped.get_subclasses("core:WorkItem")
        assert "dev:epic" in shipped.get_subclasses("core:Project")

    # ── the negative control ───────────────────────────────────────────

    def test_the_subject_row_matches_s5_except_the_nil_bucket(self):
        """§5's Subject block and this row differ by EXACTLY `other`.

        The failure this prevents: the doc is NOT internally consistent here —
        §5's block and the "Core subclass model (§6)" paragraph (which lives in
        §1, not §6) both list `other`, while §6's TABLE row and
        the #9964 authority (`kind_layers.subject_kind_names()`) both omit it.
        Before this pin the code comment claimed "both exclude `other`", which
        was false about §5, and nothing measured the gap — so a reader of §5
        would have concluded this row was short by one kind. Pinning the
        difference as an EXACT set makes the divergence a stated decision: a
        second name joining or leaving it reds here.
        """
        s5_subject = _ontology_fenced("### Subject Kind Vocabulary (core)")
        assert s5_subject, "the §5 subject block parsed empty — pin is vacuous"
        assert s5_subject - set(CORE_SUBCLASSES["Subject"]) == {"other"}, (
            "the §5-subject-vs-Subject-row gap is no longer exactly the NIL "
            f"bucket: {sorted(s5_subject - set(CORE_SUBCLASSES['Subject']))}")
        assert not (set(CORE_SUBCLASSES["Subject"]) - s5_subject), (
            "the Subject row names a kind §5 does not: "
            f"{sorted(set(CORE_SUBCLASSES['Subject']) - s5_subject)}")
        # …and §6's table row is the one that agrees with the row (its prose does not).
        table = _ontology_s6_table()
        assert _kinds_in_cell(table["Subject"]) == set(CORE_SUBCLASSES["Subject"])

    def test_point_and_event_have_no_s6_row_and_that_is_recorded(self):
        """§6 declares rows for Object/Document/Subject ONLY — Point/Event are an extension.

        The failure this prevents: `CORE_SUBCLASSES` grows a Point and an Event
        row justified only inside a code comment, so a lane reading §6 concludes
        `expand_kind("Event")` should be `["Event"]` and files the widening as a
        regression. The omission is asserted here rather than described, and the
        extension is recorded where a lane reads it (#11000 + the epic plan's §6
        table) — `docs/ONTOLOGY.md` is owner-gated, so proposing the two rows is
        the doc lane's job and this test is the fact it rests on.
        """
        table = _ontology_s6_table()
        assert set(table) >= {"Object", "Subject", "Document"}, table
        for invented in ("Point", "Event"):
            assert invented not in table, (
                f"§6 gained a {invented} row — the code's extension is now a "
                "transcription and this module's comment is stale")
            assert invented in CORE_SUBCLASSES, (
                f"{invented} lost its core hierarchy — §6 has no row, so only "
                "§5 backs it and `test_point_and_event_rows_are_the_s5_vocabularies` "
                "is now the sole pin")

    def test_a_kind_without_a_subclassof_declaration_is_unchanged(self, shipped):
        """A kind with no `subclassOf` (and no equivalence/alias) stays self-only.

        Control: this passes before AND after the fix. It pins that the widening
        is DECLARATION-DRIVEN — that it did not become a blanket "every pack kind
        gains a parent", which would make every kind read as every other kind.
        Population is derived: every declared pack kind that appears in no
        `subclassOf` child / `equivalentTo` (either end) / bare-form / legacy-alias
        entry.
        """
        from tortoise.pack_registry import LEGACY_KIND_ALIASES

        universe = {k for packs in shipped.list_all_kinds().values() for k in packs}
        touched = {f"{p.namespace}:{c}" for p in shipped.packs.values()
                   for c in p.kind_subclasses}
        # `equivalentTo` is BIDIRECTIONAL, so the TARGET end is widened too —
        # omitting it would red this control on a legitimate equivalence rather
        # than on a blanket widening.
        for p in shipped.packs.values():
            for kind, targets in p.kind_equivalences.items():
                touched.add(f"{p.namespace}:{kind}")
                touched.update(targets)
        for new_kind, legacy in LEGACY_KIND_ALIASES.items():
            touched.add(new_kind)
            touched.update(legacy)
        # A bare spelling inherits from its namespaced forms, so it is not
        # "untouched" either.
        touched |= {k.split(":", 1)[1] for k in touched if ":" in k}
        untouched = universe - touched
        assert untouched, "no untouched pack kind exists — the control is vacuous"

        widened = [(k, shipped.expand_kind(k)) for k in sorted(untouched)
                   if shipped.expand_kind(k) != [k]]
        assert not widened, (
            "a kind with no `subclassOf`/`equivalentTo` declaration was widened "
            f"anyway — the widening is not declaration-driven: {widened}")
        assert shipped.expand_kind("dev:code") == ["dev:code"]

    # ── the closure: depth 2, and DIRECTIONAL ─────────────────────────

    def test_the_closure_is_depth_two_because_a_declared_parent_is_a_core_child(
            self, shipped, tmp_path):
        """ONE hop is NOT the closure — a declared parent is itself a core child.

        #11000's first cut argued the declaration model is depth 1, so one hop
        would equal the transitive closure. That argument is WRONG, and this
        test is the correction: `_validate` does restrict a `subclassOf` parent
        to core kinds (true — pinned below), but ALL 16 of the
        `CANONICAL_OBJECT_KINDS` are THEMSELVES children of `Object` (the row is
        that canonical set), so every declared pair is the chain
        `pack kind ⊑ core child ⊑ Object`
        (`dev:bug ⊑ Problem ⊑ Object`). Measured at the one-hop head: 16 of the
        17 declared pairs had a parent that is an Object child, and
        `expand_kind("Object")` carried none of them.

        Both directions are asserted, because they are different relations:
        DOWN (a root gathers its whole subtree) and UP (a child inherits its
        ancestors' NAMES). The over-wide direction — a child gaining its
        parent's OTHER children — is pinned separately, by
        `test_a_child_gains_only_its_own_parent`.
        """
        # The one-hop argument's still-true half: a pack kind can never parent.
        pack_dir = tmp_path / "grandchild"
        pack_dir.mkdir()
        (pack_dir / "manifest.yaml").write_text(yaml.safe_dump({
            "namespace": "grandchild", "name": "Grandchild",
            "ontology": {
                "extends": "core",
                "objectKinds": ["thing"],
                "subclassOf": {"thing": "dev:epic"},  # a PACK kind as parent
            },
        }), encoding="utf-8")
        registry = PackRegistry(str(tmp_path))
        registry.load_all()
        assert any("not found in core ontology" in e
                   for e in registry.errors.get("grandchild", [])), registry.errors

        # …and the half that is FALSE: a declared parent IS a core child.
        core_children = {c for row in CORE_SUBCLASSES.values() for c in row}
        depth_two = sorted(
            f"{p.namespace}:{child}"
            for p in shipped.packs.values()
            for child, parent in p.kind_subclasses.items()
            if parent in core_children
        )
        assert depth_two, (
            "no declared subclassOf parent is a core child — the declaration "
            "model collapsed to depth 1 and the closure below is over-built")

        # DOWN: every root reaches every DECLARED descendant, transitively. This
        # is the assertion whose absence let `expand_kind('Object')` miss 16 pack
        # kinds. The expected set is walked from the declarations here rather
        # than compared bucket-to-bucket, because a CROSS-AXIS kind (`goal`,
        # `target`) is a child of BOTH `Object` and `Point`, so a child's bucket
        # legitimately holds an ancestor `Object` has no claim on.
        children_of = {root: set(kids) for root, kids in CORE_SUBCLASSES.items()}
        for p in shipped.packs.values():
            for child, parent in p.kind_subclasses.items():
                children_of.setdefault(parent, set()).add(f"{p.namespace}:{child}")
        for root in CORE_SUBCLASSES:
            expected: set[str] = set()
            stack = list(children_of.get(root, ()))
            while stack:
                kind = stack.pop()
                if kind in expected:
                    continue
                expected.add(kind)
                stack.extend(children_of.get(kind, ()))
            assert expected, root
            missing = sorted(expected - set(shipped.expand_kind(root)))
            assert not missing, (
                f"expand_kind({root!r}) does not reach its declared descendants "
                f"transitively: {missing}")

        # UP: every declared child carries its parent's ancestors by NAME. The
        # ancestor is derived from `CORE_SUBCLASSES` in reverse ("which root's row
        # holds this parent"), NOT read forward — a declared parent is a core
        # CHILD (`WorkItem`), never a root, so `CORE_SUBCLASSES.get(parent)` is
        # always `()` and an assertion built on it is vacuous. Mutation-checked:
        # deleting `_ancestors(peer)` from the closure leaves a forward lookup
        # green while this one reds.
        root_of = {child: root for root, kids in CORE_SUBCLASSES.items()
                   for child in kids}
        assert root_of, "no core parent rows parsed — pin is vacuous"
        for p in shipped.packs.values():
            for child, parent in p.kind_subclasses.items():
                bucket = set(shipped.expand_kind(f"{p.namespace}:{child}"))
                assert parent in bucket, (child, parent)
                if parent in root_of:
                    root = root_of[parent]
                    assert root in bucket, (
                        f"{p.namespace}:{child} misses its parent's ancestor "
                        f"{root!r} — the UP closure is not transitive: {sorted(bucket)}")
                    assert f"core:{root}" in bucket, (
                        f"{p.namespace}:{child} misses `core:{root}` — the "
                        "ancestor arrives in one spelling only")

        # …and the same UP direction for the CORE rows themselves: every child in
        # a §6/§5 row names its parent, in BOTH spellings. Measured before this
        # pin: `expand_kind("team") == ["team", "core:team"]` — no `Subject` —
        # because §5's Subject kinds had no bucket until the `core:` pass ran
        # after the closure.
        for root, kids in CORE_SUBCLASSES.items():
            for child in kids:
                bucket = set(shipped.expand_kind(child))
                assert root in bucket, (
                    f"expand_kind({child!r}) does not name its core parent "
                    f"{root!r} — the UP direction skips a CORE row: {sorted(bucket)}")
                assert f"core:{root}" in bucket, (child, root)

    def test_the_closure_does_not_walk_down_from_an_ancestor(self, shipped):
        """The closure is DIRECTIONAL — a child does not inherit its parent's subtree.

        The failure this prevents: implementing the closure as a plain bucket
        union ("for each member that is itself a key, add that bucket"). That
        computes the right answer for a ROOT and the wrong one for a CHILD:
        `WorkItem`'s bucket holds all eight of its children, so a union would put
        `dev:pullRequest` in `expand_kind("dev:issue")` — every issue read would
        return pull requests, cards and features. A reviewer proposing the fix
        for the missing-leg-B report will propose exactly this; it must red.
        """
        work_item_children = sorted(
            f"{p.namespace}:{c}"
            for p in shipped.packs.values()
            for c, parent in p.kind_subclasses.items()
            if parent == "WorkItem"
        )
        assert len(work_item_children) >= 2, work_item_children
        # A declared `equivalentTo` peer is the SAME kind (#7848's surviving
        # cross-pack bridge), so it is not a sibling leak — excluded here so the
        # guard fires on a directional mistake and not on a legitimate alias.
        peers: set[str] = set()
        for p in shipped.packs.values():
            for kind, targets in p.kind_equivalences.items():
                peers.add(f"{p.namespace}:{kind}")
                peers.update(targets)
        for child in work_item_children:
            siblings = set(work_item_children) - {child} - peers
            leaked = siblings & set(shipped.expand_kind(child))
            assert not leaked, (
                f"{child} gained its SIBLINGS ({sorted(leaked)}) — the closure "
                "walked down from an ancestor instead of up to it")

    def test_a_core_root_reaches_stored_legacy_spellings_beneath_it(self, shipped):
        """A root's subtree carries the legacy spellings its descendants carry.

        The failure this prevents: `LEGACY_KIND_ALIASES` is applied to the kind
        it renames, so a root that gathers its descendants by NAME still misses
        the pre-#7848 spelling of one of them — `deployment` would be in
        `expand_kind("Event")` while `dev:deployment` (the spelling actually in
        stored graphs) was not, i.e. present in the table and matching no row.
        Derived: every legacy alias whose new spelling is a descendant of a root
        must also appear in that root's bucket.
        """
        from tortoise.pack_registry import LEGACY_KIND_ALIASES

        checked = 0
        for root in CORE_SUBCLASSES:
            expanded = set(shipped.expand_kind(root))
            for new_kind, legacy_kinds in LEGACY_KIND_ALIASES.items():
                if new_kind not in expanded:
                    continue
                checked += 1
                missing = sorted(set(legacy_kinds) - expanded)
                assert not missing, (
                    f"expand_kind({root!r}) reaches {new_kind!r} but not its "
                    f"stored spelling(s) {missing} — a pre-#7848 row is unreadable")
        assert checked, (
            "no legacy alias sits under any core root — this pin is vacuous "
            "(it would pass with the propagation removed)")

    def test_a_bare_kind_inherits_the_widening_of_its_namespaced_forms(self, shipped):
        """`expand_kind("issue")` ⊇ `expand_kind("dev:issue")`.

        The failure this prevents: the fix being INERT for the bare spelling,
        which is a live stored state (#6146 — `migrate_kinds` is opt-in, never a
        read precondition). The bare bucket used to be assembled as
        `[bare, *fulls]` and nothing more, so it named `dev:issue` without
        inheriting what `dev:issue` had gained: a bare-kinded row still missed
        the `WorkItem` the whole change exists to reach. The reverse is NOT owed
        and is asserted absent, because `dev:code` must stay `["dev:code"]`.
        """
        namespaced = sorted(f"{p.namespace}:{c}"
                            for p in shipped.packs.values() for c in p.kind_subclasses)
        assert namespaced, "no namespaced subclass exists — pin is vacuous"
        starved = []
        for full in namespaced:
            bare = full.split(":", 1)[1]
            if bare == full:
                continue
            gained = set(shipped.expand_kind(full)) - set(shipped.expand_kind(bare))
            if gained:
                starved.append((bare, full, sorted(gained)))
        assert not starved, (
            "a bare kind did not inherit the widening of its namespaced form: "
            f"{starved}")
        assert "WorkItem" in shipped.expand_kind("issue")


    def test_a_cross_axis_parent_is_a_recorded_decision(self, shipped):
        """The one declared pair whose parent is ALSO a live point kind — pinned.

        `portfolio:allocationTarget` (an `objectKind`) subclasses `target`, and
        `target` is a canonical POINT kind as well (§5; the overlap is the #7813
        owner ruling — confidence propagates across `:Point`/`:Object`/`:Event`,
        so the duplication is the model, not residue).

        Consequence, recorded rather than discovered: `sdk.query(kind=
        "portfolio:allocationTarget")` builds `n.pointKind IN [...]`, so after
        the Leg A append it matches `pointKind='target'` POINTS where it used to
        return nothing. Accepted — the declaration says an allocation target IS a
        target — and pinned EXACTLY so it is a decision a reviewer can see, and so
        a future second cross-axis pair has to be acknowledged here rather than
        sliding in.
        """
        from tortoise.pack_registry import CANONICAL_POINT_KINDS

        cross_axis = sorted(
            f"{p.namespace}:{child}"
            for p in shipped.packs.values()
            for child, parent in p.kind_subclasses.items()
            if parent in CANONICAL_POINT_KINDS
        )
        assert cross_axis == ["portfolio:allocationTarget"], (
            "the set of declared subclasses whose parent is also a POINT kind "
            f"changed: {cross_axis}")

        expanded = shipped.expand_kind("portfolio:allocationTarget")
        assert "target" in expanded and "core:target" in expanded, expanded
        # …and the reverse direction is unchanged: `target` still reaches the
        # child (this was already true before the fix — asserted so the two
        # directions cannot be confused).
        assert "portfolio:allocationTarget" in shipped.expand_kind("target")
