"""Tests for pack loading, kind expansion, and relation declarations."""
from __future__ import annotations

import os
import sys
import tempfile
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from tortoise.pack_registry import PackRegistry


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
                "equivalentTo": {"issue": ["pm:task"]},
            }
        },
        "pm": {
            "namespace": "pm", "name": "Project Management",
            "ontology": {
                "extends": "core",
                "objectKinds": ["task", "sprint"],
                "subclassOf": {"task": "WorkItem"},
                "equivalentTo": {"task": ["dev:issue"]},
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
        assert "pm:task" in expanded
        assert "product-strategy:feature" in expanded

    def test_project_expands_to_epic(self, registry):
        expanded = registry.expand_kind("Project")
        assert "Project" in expanded
        assert "dev:epic" in expanded

    def test_equivalence_bidirectional(self, registry):
        dev_expanded = registry.expand_kind("dev:issue")
        pm_expanded = registry.expand_kind("pm:task")
        assert "pm:task" in dev_expanded
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
                "equivalentTo": {"issue": ["pm:task"]},
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
                "equivalentTo": {"issue": ["pm:task"]},  # issue not declared
            }
        })
        assert errors
