"""E2E-2-D — free tier limits (quota enforcement, fail-closed).

Reconstructed case (#303). Free-tier caps come from the fixture pricing file
(TORTOISE_PRICING_PATH — canonical values); enforcement is asserted
behaviorally over the wire: api-key cap (free max_api_keys=2) and the
e2e_small node cap (max_graph_nodes=8) both fail closed with 402.

Negatives: 3rd API key → 402; point writes past the e2e_small cap → 402.
"""
from __future__ import annotations  # noqa: I001

import json
import uuid

import pytest

from conftest import (PRICE_IDS, PRICING_FIXTURE, bump_team_tier,  # noqa: F401
                      is_remote_mode, skip_unless_hosted_e2e)

skip_unless_hosted_e2e()

# #303 (review r2): local-only seams — the tier bump drives the real webhook
# path signed with the fixture STRIPE_WEBHOOK_SECRET and resolves prices via
# the fixture STRIPE_PRICE_IDS catalog; the caps asserted here come from the
# fixture pricing file. A remote target shares none of that contract.
pytestmark = pytest.mark.skipif(
    is_remote_mode(),
    reason=("needs the fixture webhook secret + price catalog + pricing caps "
            "on the target (local hermetic seam)"))


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


def _live_keys(api, h) -> list[dict]:
    r = api.get("/v1/team/keys", headers=h)
    assert r.status == 200, r.text()
    return [k for k in r.json()["keys"] if not k.get("revoked_at")]


def test_api_key_rotate_is_cap_neutral(api, tenant_factory):
    """#4355 — the acceptance pair, over the real wire (registry lane).

    At the free `max_api_keys` cap (2) a 1-for-1 ROTATE succeeds, because the
    replacement is admitted against the POST-RELEASE count (it consumes the
    slot the displaced row frees). A plain create still 402s — the naive fix
    (exempting `POST /v1/team/keys`) would have made the second assertion fail
    instead. The live count must be unchanged by the rotate.
    """
    t = tenant_factory("keyrotate")
    h = {"Authorization": f"Bearer {t['api_key']}"}
    r = api.post("/v1/team/keys", headers=h)
    assert r.status == 200, f"2nd key must succeed: {r.status} {r.text()}"
    live = _live_keys(api, h)
    assert len(live) == 2, f"fixture must sit AT the free cap: {live}"
    assert api.post("/v1/team/keys", headers=h).status == 402, (
        "the cap must still bind a plain create")

    target = live[0]
    tid = target.get("id") or target.get("key_id")
    r = api.post(f"/v1/team/keys/{tid}/rotate", headers=h)
    assert r.status == 200, f"at-cap rotate must succeed: {r.status} {r.text()}"
    body = r.json()
    assert body["replaced_key_id"] == tid, body
    assert body["replaced_revoked"] is True, body
    assert body.get("key"), "the replacement plaintext is revealed once"
    after = _live_keys(api, h)
    assert len(after) == 2, f"rotate must be 1-for-1: {len(after)} live rows"
    assert tid not in {k.get("id") or k.get("key_id") for k in after}, (
        "the displaced row must be revoked")
    # And the cap is still exactly where it was.
    assert api.post("/v1/team/keys", headers=h).status == 402, (
        "the rotate must not have bought a key slot")


def test_api_key_rotate_refuses_a_non_occupying_row(api, tenant_factory):
    """#4355 — the fail-closed half. A row the cap never charged (here: an
    already-REVOKED row) cannot be rotated: crediting it would mint a free key
    beyond the limit. 409, and no replacement is left behind."""
    t = tenant_factory("keyrot409")
    h = {"Authorization": f"Bearer {t['api_key']}"}
    r = api.post("/v1/team/keys", headers=h)
    assert r.status == 200, r.text()
    live = _live_keys(api, h)
    # Revoke the key that is NOT the caller (the caller's own credential must
    # keep authenticating this request).
    caller_prefix = t["api_key"][:10]
    other = next(k for k in live if k.get("key_prefix") != caller_prefix)
    oid = other.get("id") or other.get("key_id")
    assert api.delete(f"/v1/team/keys/{oid}", headers=h).status == 200
    before = _live_keys(api, h)
    r = api.post(f"/v1/team/keys/{oid}/rotate", headers=h)
    assert r.status == 409, f"a revoked row frees no slot: {r.status} {r.text()}"
    assert len(_live_keys(api, h)) == len(before), (
        "a refused rotate must not mint a replacement")


def test_points_cap_fails_closed_402(api, tenant_factory):
    """Dedicated tenant bumped to e2e_small (max_graph_nodes=8): writes past
    the cap 402 (fail-closed quota) — never a silent over-write."""
    t = tenant_factory("smallcap")
    h = {"Authorization": f"Bearer {t['api_key']}"}
    bump_team_tier(api, t["org_id"], "e2e_small")
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
