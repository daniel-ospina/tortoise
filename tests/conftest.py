"""D11 #578 — shared fixtures for the epic E2E suite.

provision_test_user: creates a provisioned test user (team + membership +
key) with tier + demo_seed control. Tier injection writes the Team node
directly (no user-facing tier path in v1). Used by E2E-1/3/4/5/10/11/12/13.
"""
from __future__ import annotations

import os
import tempfile

import pytest

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

from tortoise.sdk import TortoiseSDK
from tortoise.pricing import tier_limits


@pytest.fixture
def provision_test_user():
    created = []

    def factory(tier: str = "free", demo_seed: bool = True):
        tmpdir = tempfile.mkdtemp()
        sdk = TortoiseSDK(os.path.join(tmpdir, "e2e.db"), namespace="e2e-tests")
        team = sdk.team_create(f"e2e-{os.urandom(4).hex()}")
        lim = tier_limits(tier)
        sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.tier=$tier, t.max_graphs=$mg, "
            "t.max_users=$mu, t.max_api_keys=$mk, t.ops_allowance=$ops, t.graph_size_cap=$nodes",
            params={"id": team["id"], "tier": tier,
                    "mg": lim["max_graphs_per_team"], "mu": lim["max_users_per_team"],
                    "mk": lim["max_api_keys"], "ops": lim["included_write_ops_per_month"],
                    "nodes": lim["max_graph_nodes"]},
        )
        if demo_seed:
            try:
                sdk._graph_create(team["id"], "demo", kind="custom")
            except Exception:
                pass
        user_id = f"user-{os.urandom(4).hex()}"
        sdk.membership_create(team["id"], user_id, "owner")
        created.append(sdk)
        return {"sdk": sdk, "team_id": team["id"], "api_key": team["api_key"],
                "graph_name": team["graph_name"], "team_name": team["name"],
                "user_id": user_id}

    yield factory
    for sdk in created:
        try:
            sdk.close()
        except Exception:
            pass


@pytest.fixture(scope="session")
def shared_embedded_db():
    """One shared embedded FalkorDBLite DB for the whole session (#221 R5).

    R5 mitigation for the redislite process leak (#176): tests that need an
    embedded (redislite) DB create ONE server per session instead of one per
    test. Each test wipes the graph on its own (or uses a per-test graph
    name), so state never leaks across tests while the subprocess count stays
    at 1.

    Restored 2026-08-08 (#493): the D11 conftest rewrite (#578) dropped this
    fixture but five test files (test_ep_selector, test_ranking,
    test_sdk_legacy_coverage, test_search_sessions_temporal,
    test_session_semantic_search) still depend on it — the main suite had no
    CI to catch the breakage until the Python test job landed.

    # TODO(#176): stopgap — remove when the redislite root-cause fix lands.
    """
    db_path = os.path.join(
        tempfile.mkdtemp(prefix="tortoise_shared_embedded_"), "shared.db"
    )
    yield db_path


@pytest.fixture
def test_user(provision_test_user):
    return provision_test_user(tier="free", demo_seed=True)
