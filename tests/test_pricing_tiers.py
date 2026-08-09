"""D1 tests — tier limits from pricing.json (decision 1d) + Graph node (1:N).

Epic: 2026-08-07-tortoise-user-journeys
Issue: #568 (D1 — user↔team↔graph decoupling + tier enforcement)
E2E: E2E-11 (team↔graph 1:N with tier limits), E2E-13 (pricing enforced)
"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

import pytest

import tortoise.pricing as pricing
from tortoise.sdk import TortoiseSDK


@pytest.fixture(autouse=True)
def _fresh_pricing():
    pricing.reload()
    yield
    pricing.reload()


@pytest.fixture
def sdk():
    with tempfile.TemporaryDirectory() as tmpdir:
        sdk = TortoiseSDK(os.path.join(tmpdir, "test.db"), namespace="test-tiers")
        yield sdk


class TestPricingLoader:
    def test_loads_canonical_tiers(self):
        assert pricing.all_tiers() == ["free", "solo", "pro", "team"]

    def test_tier_limits_match_pricing_json(self):
        free = pricing.tier_limits("free")
        assert free["max_graphs_per_team"] == 1
        assert free["max_users_per_team"] == 1
        assert free["max_api_keys"] == 2
        assert free["included_write_ops_per_month"] == 10000
        assert free["max_graph_nodes"] == 10000

        solo = pricing.tier_limits("solo")
        assert solo["max_graphs_per_team"] == 2
        assert solo["included_write_ops_per_month"] == 10000

        pro = pricing.tier_limits("pro")
        assert pro["max_graphs_per_team"] is None  # unlimited
        assert pro["max_users_per_team"] == 2
        assert pro["overage"] is True

        team = pricing.tier_limits("team")
        assert team["max_users_per_team"] is None  # unlimited
        assert team["included_write_ops_per_month"] == 200000

    def test_unknown_tier_defaults_to_free(self):
        lim = pricing.tier_limits("enterprise-unknown")
        assert lim["max_graphs_per_team"] == 1  # Free baseline

    def test_overage_config(self):
        assert pricing.overage_price_per_10k() == 5.0
        assert pricing.has_overage("pro") and pricing.has_overage("team")
        assert not pricing.has_overage("free") and not pricing.has_overage("solo")

    def test_no_max_teams_field(self):
        # Per-team billing: multi-team is a user capability, NOT a tier field
        for tier in pricing.all_tiers():
            lim = pricing.tier_limits(tier)
            assert "max_teams" not in lim


class TestTeamCreateTierLimits:
    def test_team_create_stores_tier_limits(self):
        sdk = sdk_fixture()
        result = sdk.team_create("alice-team")
        team = sdk.team_get(result["id"])
        assert team["tier"] == "free"
        assert team.get("max_graphs") == 1
        assert team.get("max_users") == 1
        assert team.get("max_api_keys") == 2
        # No max_teams field on the Team node (user-level capability)
        assert "max_teams" not in team

    def test_team_create_creates_default_graph_node(self):
        sdk = sdk_fixture()
        result = sdk.team_create("graph-team")
        graphs = sdk.graph_list(result["id"])
        assert len(graphs) == 1
        assert graphs[0]["kind"] == "default"
        assert graphs[0]["name"] == "default"
        assert graphs[0]["namespace"] == result["graph_name"]
        assert sdk.graph_count(result["id"]) == 1

    def test_custom_graph_node(self):
        sdk = sdk_fixture()
        result = sdk.team_create("multi-graph-team")
        g = sdk._graph_create(result["id"], "project-b")
        assert g["kind"] == "custom"
        assert g["namespace"] == f"team_{result['id']}_{g['graph_id']}"
        assert sdk.graph_count(result["id"]) == 2  # default + custom
        # Default graph sorts first
        graphs = sdk.graph_list(result["id"])
        assert graphs[0]["kind"] == "default"
        assert graphs[1]["kind"] == "custom"


def sdk_fixture():
    """Helper to mirror the sdk fixture in class methods."""
    import tempfile
    tmpdir = tempfile.mkdtemp()
    return TortoiseSDK(os.path.join(tmpdir, "test.db"), namespace="test-tiers")
