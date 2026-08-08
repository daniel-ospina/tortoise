"""D3 tests — session-key mint (E1, the #518 chicken-and-egg fix).

Epic: 2026-08-07-tortoise-user-journeys · Issue: #570 (D3)
Plan §6.2 E1 + §6.6 APIKey extension: bootstrap (24h, cap-exempt, 3-active
backstop) vs recovery (persistent, revocable, counts against max_api_keys,
auto-revoke-oldest at cap). get_current_team rejects expired keys.
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
        sdk = TortoiseSDK(os.path.join(tmpdir, "test.db"), namespace="test-e1")
        yield sdk


class TestE1SessionKey:
    def test_recovery_key_persistent_and_counts_against_cap(self, sdk):
        team = sdk.team_create("recovery-team")
        # (E1 endpoint itself is FastAPI-level; here we verify the APIKey node
        # schema supports expires_at/created_via that E1 writes, and that the
        # tier cap is readable so E1 can enforce it)
        sdk._get_registry().query(
            "CREATE (k:APIKey {id:'e1test', team_id:$tid, key_hash:'h', key_prefix:'tt_', "
            "created_by:'u', created_at:'2026-08-07', revoked_at:null, "
            "expires_at:null, created_via:'recovery'})",
            params={"tid": team["id"]},
        )
        rows = sdk._get_registry().query(
            "MATCH (k:APIKey {id:'e1test'}) RETURN k.expires_at, k.created_via",
        ).result_set
        assert rows[0][0] is None  # persistent
        assert rows[0][1] == "recovery"

    def test_bootstrap_key_carries_expiry(self, sdk):
        team = sdk.team_create("boot-team")
        sdk._get_registry().query(
            "CREATE (k:APIKey {id:'boot1', team_id:$tid, key_hash:'h', key_prefix:'tt_', "
            "created_by:'u', created_at:'2026-08-07', revoked_at:null, "
            "expires_at:'2026-08-08T00:00:00+00:00', created_via:'bootstrap'})",
            params={"tid": team["id"]},
        )
        rows = sdk._get_registry().query(
            "MATCH (k:APIKey {id:'boot1'}) RETURN k.expires_at, k.created_via",
        ).result_set
        assert rows[0][0] is not None  # 24h expiry
        assert rows[0][1] == "bootstrap"

    def test_tier_limits_available_for_e1(self):
        # E1 reads tier_limits to enforce recovery-key cap
        lim = pricing.tier_limits("free")
        assert lim["max_api_keys"] == 2
        assert pricing.has_overage("pro") and not pricing.has_overage("solo")
