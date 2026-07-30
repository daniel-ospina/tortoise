"""Tests for domain_loader — kind registry, manifest loading, domain routing."""
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tortoise.domain_loader import (
    known_kinds,
    register_kind,
    kind_is_known,
    load_manifest,
    resolve_domain_from_path,
    DomainRoutingConfig,
    domain_event_types,
    domain_query_patterns,
    domain_cypher_templates,
)


class TestKindRegistry:
    def test_known_kinds_returns_frozenset(self):
        kinds = known_kinds()
        assert isinstance(kinds, frozenset)
        assert "decision" in kinds
        assert "observation" in kinds
        assert "hypothesis" in kinds
        # Verify some base kinds are present
        base = {"statement", "decision", "vision", "strategy", "plan", "goal",
                "target", "observation", "hypothesis", "workflow", "requirement"}
        assert base.issubset(kinds)

    def test_register_kind_adds_to_known(self):
        before = "custom_test_kind" in known_kinds()
        register_kind("custom_test_kind")
        after = "custom_test_kind" in known_kinds()
        assert after
        # Verify the kind is now known
        assert kind_is_known("custom_test_kind")

    def test_kind_is_known_unknown(self):
        assert not kind_is_known("definitely_not_a_real_kind_xyz")

    def test_register_idempotent(self):
        register_kind("idempotent_kind")
        count_before = len(known_kinds())
        register_kind("idempotent_kind")
        assert len(known_kinds()) == count_before


class TestManifestLoading:
    def test_load_manifest_missing_file(self):
        domains = load_manifest("/nonexistent/path.yaml")
        assert domains == {}

    def test_load_manifest_valid_yaml(self):
        yaml_content = """domains:
  product:
    name: Product Strategy
    kind_values:
      decisions: [go, no_go, defer]
      statements: [market_finding, competitor_move]
    event_types: [decision_made, strategy_updated]
    query_patterns: [product, strategy, competitor]
    priority: 5
    timeout: 3.0
  engineering:
    name: Engineering
    active: false
    kind_values:
      decisions: [architecture_choice, tech_debt]
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            domains = load_manifest(f.name)
        Path(f.name).unlink()

        assert "product" in domains
        assert domains["product"].name == "Product Strategy"
        assert domains["product"].priority == 5
        assert domains["product"].timeout == 3.0
        assert "go" in known_kinds()
        assert "market_finding" in known_kinds()
        # engineering is inactive — should not appear
        assert "engineering" not in domains

    def test_load_manifest_empty_yaml(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("{}")
            f.flush()
            domains = load_manifest(f.name)
        Path(f.name).unlink()
        assert domains == {}

    def test_load_manifest_no_domains_key(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("other_key: value")
            f.flush()
            domains = load_manifest(f.name)
        Path(f.name).unlink()
        assert domains == {}


class TestDomainResolution:
    def test_resolve_domain_from_path_with_manifest(self):
        yaml = """directory_map:
  docs/01_product/: product
  docs/04_platform/: engineering
  docs/07_ux/: ux
domains: {}
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml)
            f.flush()
            result = resolve_domain_from_path("docs/04_platform/architecture.md", f.name)
        Path(f.name).unlink()
        assert result == "engineering"

    def test_resolve_domain_longest_prefix_wins(self):
        yaml = """directory_map:
  docs/04_platform/: engineering
  docs/04_platform/security/: security
domains: {}
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml)
            f.flush()
            result = resolve_domain_from_path("docs/04_platform/security/audit.md", f.name)
        Path(f.name).unlink()
        assert result == "security"

    def test_resolve_domain_no_match_falls_back_to_capability(self):
        yaml = """directory_map:
  docs/01_product/: product
domains: {}
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml)
            f.flush()
            result = resolve_domain_from_path("some/random/path.md", f.name)
        Path(f.name).unlink()
        assert result == "capability"

    def test_resolve_domain_missing_manifest(self):
        result = resolve_domain_from_path("any/path.md", "/nonexistent/manifest.yaml")
        assert result == "capability"


class TestDomainRoutingConfig:
    def test_default_values(self):
        cfg = DomainRoutingConfig(key="test", name="Test")
        assert cfg.key == "test"
        assert cfg.name == "Test"
        assert cfg.active is True
        assert cfg.event_types == []
        assert cfg.query_patterns == []
        assert cfg.timeout == 5.0
        assert cfg.priority == 10

    def test_extract_event_types(self):
        cfg = DomainRoutingConfig(key="p", name="Product", event_types=["decision_made"])
        domains = {"p": cfg}
        result = domain_event_types(domains)
        assert result == {"p": ["decision_made"]}

    def test_extract_query_patterns(self):
        cfg1 = DomainRoutingConfig(key="p", name="Product", query_patterns=["product"])
        cfg2 = DomainRoutingConfig(key="e", name="Eng", query_patterns=["product", "deploy"])
        result = domain_query_patterns({"p": cfg1, "e": cfg2})
        assert result["product"] == ["p", "e"]
        assert result["deploy"] == ["e"]

    def test_extract_cypher_templates_empty(self):
        cfg = DomainRoutingConfig(key="p", name="Product")
        result = domain_cypher_templates({"p": cfg})
        assert result == {}
