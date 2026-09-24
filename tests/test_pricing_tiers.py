"""D1 tests — tier limits from pricing.json (decision 1d) + Graph node (1:N).

Epic: 2026-08-07-tortoise-user-journeys
Issue: #568 (D1 — user↔team↔graph decoupling + tier enforcement)
E2E: E2E-11 (team↔graph 1:N with tier limits), E2E-13 (pricing enforced)
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

import pytest

import tortoise.pricing as pricing
from tortoise.sdk import TortoiseSDK

_REPO_ROOT = Path(__file__).resolve().parent.parent

# #4815: pricing.json exists as TWO live artifacts. The canonical one is what
# `tortoise.pricing` loads; the E2E fixture is served to the E2E server through
# the SAME `TORTOISE_PRICING_PATH` env var (`tests/e2e/hosted/conftest.py`), and
# it carries its OWN copy of `billing.overage_tiers` and `tiers.<t>.overage`. An
# edit to one that misses the other is a silent divergence — the fixture is a
# hand-maintained copy that has needed repeated lockstep syncs, so the parity
# guard below reads every artifact, not just the one the module happens to be
# pointed at.
_PRICING_ARTIFACTS = {
    "product/pricing.json": _REPO_ROOT / "product" / "pricing.json",
    "tests/e2e/hosted/fixtures/pricing-e2e.json": (
        _REPO_ROOT / "tests" / "e2e" / "hosted" / "fixtures" / "pricing-e2e.json"),
}

# The E2E fixture's deltas, as an explicit allow-list — anything else
# differing is drift, not a declaration. Its own ``$e2e_note`` declares TWO of
# them: it adds the ``e2e_small`` cap tier and sets pro/team
# ``features.hourly_backups`` true. The ``anon`` absence is NOT claimed by the
# note; the allow-list below is the authority for it.
_FIXTURE_ONLY_TIERS = frozenset({"e2e_small"})
_FIXTURE_MISSING_TIERS = frozenset({"anon"})


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
        assert pricing.all_tiers() == ["free", "solo", "pro", "team", "anon"]

    def test_tier_limits_match_pricing_json(self):
        free = pricing.tier_limits("free")
        assert free["max_graphs_per_team"] == 1
        assert free["max_users_per_team"] == 1
        assert free["max_api_keys"] == 2
        assert free["included_write_ops_per_month"] == 10000  # post-#662 (was 1000)        assert free["max_graph_nodes"] == 10000

        solo = pricing.tier_limits("solo")
        assert solo["max_graphs_per_team"] == 2
        assert solo["included_write_ops_per_month"] == 10000
        assert solo["overage"] is True  # #4815: every paid tier is metered

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
        # #4815: solo is a PAID tier → metered (it was the only paid tier
        # without overage). free/anon are not covered by the ruling.
        assert pricing.has_overage("solo")
        assert not pricing.has_overage("free") and not pricing.has_overage("anon")

    def test_every_paid_tier_has_overage(self):
        """#4815 (owner ruling): overage is ON for EVERY paid tier — the tier
        price buys FEATURES (graphs, colleagues), never a paying customer's
        exclusion from metering. Solo was the last paid tier without overage.

        Both statements of eligibility are pinned: the per-tier DISPLAY flag
        (``tiers.<t>.overage``, what the plan card renders) and metering,
        which reads ``billing.overage_tiers`` through ``has_overage()``.
        Going red on the second means a paid tier silently stopped emitting
        threshold events / reporting ``overage_eligible``."""
        paid = [t for t in pricing.all_tiers() if pricing.tier_price(t) > 0]
        assert paid, "pricing.json must define at least one paid tier"
        for tier in paid:
            assert pricing.tier_limits(tier)["overage"] is True, (
                f"paid tier {tier!r} must have overage=True (#4815)")
            assert pricing.has_overage(tier) is True, (
                f"paid tier {tier!r} must be in billing.overage_tiers — "
                f"metering reads that, not tiers.{tier}.overage (#4815)")

    def test_overage_tiers_list_agrees_with_tier_flags(self):
        """pricing.json states overage eligibility TWICE — per-tier
        ``tiers.<t>.overage`` and ``billing.overage_tiers``. ``has_overage()``
        reads the latter, and it gates exactly two things: the 80%/100%
        threshold log events (``metering._check_thresholds``) and the
        ``overage_eligible`` / ``overage_cost_usd`` fields of the usage view
        (``metering.get_current_usage``). If the two diverge, a tier is
        advertised as metered while metering stays silent (#4815).

        Checked for EVERY loaded pricing artifact: the E2E fixture carries its
        own copy and is served through the same TORTOISE_PRICING_PATH."""
        for name, path in _PRICING_ARTIFACTS.items():
            data = json.loads(path.read_text(encoding="utf-8"))
            metered = {t for t, cfg in data["tiers"].items()
                       if cfg.get("overage")}
            declared = set(data["billing"]["overage_tiers"])
            assert declared == metered, (
                f"{name}: billing.overage_tiers {sorted(declared)} != "
                f"tiers with overage=True {sorted(metered)}")

    def test_e2e_fixture_overage_mirrors_canonical(self):
        """The E2E fixture is a served copy of product/pricing.json — its
        overage facts must track canonical for every shared tier, allowing
        ONLY its declared deltas (#303, #2317, #4815).

        Without this, an edit that flips overage in one artifact and not the
        other keeps the suite green while the E2E server meters a different
        tier set than production."""
        canonical = json.loads(
            _PRICING_ARTIFACTS["product/pricing.json"].read_text("utf-8"))
        fixture = json.loads(
            _PRICING_ARTIFACTS[
                "tests/e2e/hosted/fixtures/pricing-e2e.json"].read_text("utf-8"))

        assert fixture["billing"]["overage_tiers"] == \
            canonical["billing"]["overage_tiers"]

        for tier in sorted(set(fixture["tiers"]) & set(canonical["tiers"])):
            assert (fixture["tiers"][tier]["overage"]
                    is canonical["tiers"][tier]["overage"]), (
                f"E2E fixture tier {tier!r} overage "
                f"{fixture['tiers'][tier]['overage']} != canonical "
                f"{canonical['tiers'][tier]['overage']} (#4815)")

        assert set(fixture["tiers"]) - set(canonical["tiers"]) == \
            _FIXTURE_ONLY_TIERS, (
                "E2E fixture gained an undeclared tier — update its $e2e_note "
                "and this allow-list deliberately")
        assert set(canonical["tiers"]) - set(fixture["tiers"]) == \
            _FIXTURE_MISSING_TIERS, (
                "E2E fixture dropped a tier that is not a declared delta")

    def test_no_max_teams_field(self):
        # Per-team billing: multi-team is a user capability, NOT a tier field
        for tier in pricing.all_tiers():
            lim = pricing.tier_limits(tier)
            assert "max_teams" not in lim


class TestTeamCreateTierLimits:
    def test_team_create_stores_tier_limits(self):
        sdk = sdk_fixture()
        result = sdk.org_create("alice-team")
        team = sdk.org_get(result["id"])
        assert team["tier"] == "free"
        assert team.get("max_graphs") == 1
        assert team.get("max_users") == 1
        assert team.get("max_api_keys") == 2
        # No max_teams field on the Team node (user-level capability)
        assert "max_teams" not in team

    def test_team_create_creates_default_graph_node(self):
        sdk = sdk_fixture()
        result = sdk.org_create("graph-team")
        graphs = sdk.graph_list(result["id"])
        assert len(graphs) == 1
        assert graphs[0]["kind"] == "default"
        assert graphs[0]["name"] == "default"
        assert graphs[0]["namespace"] == result["graph_name"]
        assert sdk.graph_count(result["id"]) == 1

    def test_custom_graph_node(self):
        sdk = sdk_fixture()
        result = sdk.org_create("multi-graph-team")
        g = sdk._graph_create(result["id"], "project-b")
        assert g["kind"] == "custom"
        assert g["namespace"] == f"org_{result['id']}_{g['graph_id']}"
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
