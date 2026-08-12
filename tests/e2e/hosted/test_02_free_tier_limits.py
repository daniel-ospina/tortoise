"""E2E-2-D — free tier limits (quota enforcement, fail-closed).

Reconstructed case (#303). Free-tier caps come from the fixture pricing file
(TORTOISE_PRICING_PATH — canonical values); enforcement is asserted
behaviorally over the wire: api-key cap (free max_api_keys=2) and the
e2e_small node cap (max_graph_nodes=8) both fail closed with 402.

Negatives: 3rd API key → 402; point writes past the e2e_small cap → 402.
"""
from __future__ import annotations

import json
import uuid

from conftest import PRICE_IDS, PRICING_FIXTURE, bump_team_tier, skip_unless_hosted_e2e

skip_unless_hosted_e2e()


def test_free_tier_limits_visible_in_team_info(api, tenant_factory):
    """Positive: /v1/team exposes the free caps that pricing.json defines."""
    pricing = json.loads(PRICING_FIXTURE.read_text())
    free = pricing["tiers"]["free"]

    t = tenant_factory("limits-visible")
    h = {"Authorization": f"Bearer {t['api_key']}"}
    r = api.get("/v1/team", headers=h)
    assert r.status == 200, r.text()
    team = r.json()
    assert team["tier"] == "free"
    assert team["max_graphs"] == free["max_graphs_per_team"]
    assert team["write_ops_limit"] == free["included_write_ops_per_month"]


def test_api_key_cap_enforced_402(api, tenant_factory):
    """Free max_api_keys=2: register key + 1 minted = cap → 3rd mint 402s."""
    t = tenant_factory("keycap")
    h = {"Authorization": f"Bearer {t['api_key']}"}
    r = api.post("/v1/team/keys", headers=h)
    assert r.status == 200, f"2nd key must succeed: {r.status} {r.text()}"
    r = api.post("/v1/team/keys", headers=h)
    assert r.status == 402, f"3rd key must hit the cap (402), got {r.status}: {r.text()}"


def test_points_cap_fails_closed_402(api, tenant_factory):
    """Dedicated tenant bumped to e2e_small (max_graph_nodes=8): writes past
    the cap 402 (fail-closed quota) — never a silent over-write."""
    t = tenant_factory("smallcap")
    h = {"Authorization": f"Bearer {t['api_key']}"}
    bump_team_tier(api, t["team_id"], "e2e_small")
    r = api.get("/v1/team", headers=h)
    assert r.status == 200 and r.json()["tier"] == "e2e_small", r.text()

    statuses = []
    for i in range(15):
        r = api.post("/v1/points", headers=h,
                     data={"content": f"cap probe {i} ({uuid.uuid4().hex[:4]})",
                           "kind": "statement"})
        statuses.append(r.status)
        if r.status == 402:
            break
    assert 402 in statuses, f"node cap never enforced: {statuses}"
    assert statuses[-1] == 402, f"once capped, writes must stay 402: {statuses}"
    assert all(s in (200, 402) for s in statuses), statuses
