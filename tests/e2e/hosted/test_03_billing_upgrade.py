"""E2E-3-D — Pro upgrade / billing (hermetic webhook leg).

Reconstructed case (#303; E2E-3-D precedent: tests/e2e/test_billing_upgrade.py
live Stripe leg — skip-guarded on STRIPE_TEST_* keys; the 4-webhook semantics
live in tests/test_billing.py). THIS suite's leg runs with zero Stripe
network: signed checkout.session.completed (client_reference_id + customer,
no `subscription` field → StripeClient is never constructed) binds the team,
signed customer.subscription.updated resolves the price via the LOCAL
STRIPE_PRICE_IDS catalog → tier bump + apply_limits, asserted via /v1/team.

Negatives: tampered signature → 400; unknown price via webhook → 200 + tier
preserved (review-fix-7 semantics); checkout unknown price → 400; checkout on
the unconfigured bare server → 503; webhook on the bare server → 500.
"""
from __future__ import annotations

import json
import uuid

import pytest

from conftest import (
    WEBHOOK_SECRET,
    bump_team_tier,
    is_remote_mode,
    sign_stripe_event,
    skip_unless_hosted_e2e,
)

skip_unless_hosted_e2e()

# #303 (review r2): every leg here signs Stripe webhooks with the fixture
# STRIPE_WEBHOOK_SECRET against the fixture STRIPE_PRICE_IDS catalog — a
# remote target does not share that contract (signature 400 / unknown price).
pytestmark = pytest.mark.skipif(
    is_remote_mode(),
    reason=("needs the fixture webhook secret + price catalog on the target "
            "(local hermetic seam)"))


def test_hermetic_upgrade_to_pro(api, tenant_factory):
    """Positive: two signed webhooks upgrade the team to pro with pro limits."""
    t = tenant_factory("upgrade")
    h = {"Authorization": f"Bearer {t['api_key']}"}
    bump_team_tier(api, t["team_id"], "pro")
    r = api.get("/v1/team", headers=h)
    assert r.status == 200, r.text()
    team = r.json()
    assert team["tier"] == "pro", f"tier bump did not land: {team}"
    # Pro tier confirmed — the write propagated to the auth-resolved team dict.
    # (TeamInfoResponse carries max_graphs/max_teams/write_ops_limit, not a
    # point_limit field — tier is the authoritative pro signal.)


def test_webhook_replay_idempotent(api, tenant_factory):
    """Replaying the same subscription event converges (SET semantics) — the
    tier stays pro and both deliveries 200."""
    t = tenant_factory("replay")
    h = {"Authorization": f"Bearer {t['api_key']}"}
    bump_team_tier(api, t["team_id"], "pro")
    cust = f"cus_e2e_{uuid.uuid4().hex[:10]}"
    # bind a customer first, then replay the SAME subscription.updated twice
    checkout = {"id": f"evt_replay_co_{uuid.uuid4().hex[:6]}",
                "type": "checkout.session.completed",
                "data": {"object": {"client_reference_id": t["team_id"],
                                    "customer": cust}}}
    body, sig = sign_stripe_event(checkout)
    assert api.post("/webhooks/stripe", data=body,
                    headers={"Stripe-Signature": sig}).status == 200
    sub = {"id": f"evt_replay_su_{uuid.uuid4().hex[:6]}",
           "type": "customer.subscription.updated",
           "data": {"object": {"customer": cust, "status": "active",
                               "items": [{"price": {"id": "price_e2e_pro_monthly"}}]}}}
    body, sig = sign_stripe_event(sub)
    r1 = api.post("/webhooks/stripe", data=body, headers={"Stripe-Signature": sig})
    r2 = api.post("/webhooks/stripe", data=body, headers={"Stripe-Signature": sig})
    assert r1.status == 200 and r2.status == 200, (r1.text(), r2.text())
    assert api.get("/v1/team", headers=h).json()["tier"] == "pro"


def test_tampered_signature_rejected_400(api, tenant_factory):
    t = tenant_factory("tampersig")
    event = {"id": "evt_tamper", "type": "customer.subscription.updated",
             "data": {"object": {"customer": "cus_x", "status": "active",
                                 "items": [{"price": {"id": "price_e2e_pro_monthly"}}]}}}
    body, sig = sign_stripe_event(event, secret="whsec_WRONG_SECRET")
    r = api.post("/webhooks/stripe", data=body, headers={"Stripe-Signature": sig})
    assert r.status == 400, f"tampered sig must 400, got {r.status}: {r.text()}"


def test_webhook_unknown_price_keeps_tier_200(api, tenant_factory):
    """Unknown price id via webhook → 200 ack + stored tier preserved
    (review-fix-7: ops notification, never a silent downgrade)."""
    t = tenant_factory("unknownprice")
    h = {"Authorization": f"Bearer {t['api_key']}"}
    bump_team_tier(api, t["team_id"], "pro")
    cust = f"cus_e2e_{uuid.uuid4().hex[:10]}"
    checkout = {"id": f"evt_up_co_{uuid.uuid4().hex[:6]}",
                "type": "checkout.session.completed",
                "data": {"object": {"client_reference_id": t["team_id"],
                                    "customer": cust}}}
    body, sig = sign_stripe_event(checkout)
    api.post("/webhooks/stripe", data=body, headers={"Stripe-Signature": sig})
    sub = {"id": f"evt_up_su_{uuid.uuid4().hex[:6]}",
           "type": "customer.subscription.updated",
           "data": {"object": {"customer": cust, "status": "active",
                               "items": [{"price": {"id": "price_NOT_IN_catalog"}}]}}}
    body, sig = sign_stripe_event(sub)
    r = api.post("/webhooks/stripe", data=body, headers={"Stripe-Signature": sig})
    assert r.status == 200, r.text()
    assert api.get("/v1/team", headers=h).json()["tier"] == "pro", \
        "unknown price must preserve the stored tier"


def test_checkout_unknown_price_400(api, tenant_factory):
    t = tenant_factory("checkout400")
    h = {"Authorization": f"Bearer {t['api_key']}"}
    r = api.post("/v1/billing/checkout", headers=h,
                 data={"price_id": "price_NOT_IN_catalog"})
    assert r.status == 400, f"unknown price at checkout must 400, got {r.status}"


def test_checkout_unconfigured_503_bare_server(api, bare_hosted_server, tenant_factory):
    """Bare server (no STRIPE_PRICE_IDS): checkout 503s before any Stripe
    call; webhook 500s as 'not configured'."""
    import urllib.request

    # register a tenant ON the bare server for the authed checkout leg
    email = f"e2e-bare-{uuid.uuid4().hex[:8]}@e2e.premise-labs.dev"
    req = urllib.request.Request(
        f"{bare_hosted_server.base_url}/v1/register",
        data=json.dumps({"email": email, "password": "E2ePass-303-x"}).encode(),
        headers={"Content-Type": "application/json"})
    body = json.loads(urllib.request.urlopen(req, timeout=10).read())
    key = body["api_key"]

    import urllib.error
    req = urllib.request.Request(
        f"{bare_hosted_server.base_url}/v1/billing/checkout",
        data=json.dumps({"price_id": "price_anything"}).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"})
    try:
        urllib.request.urlopen(req, timeout=10)
        raise AssertionError("checkout on unconfigured server must not 2xx")
    except urllib.error.HTTPError as e:
        assert e.code == 503, f"expected 503, got {e.code}"

    req = urllib.request.Request(
        f"{bare_hosted_server.base_url}/webhooks/stripe",
        data=b"{}", headers={"Stripe-Signature": "t=1,v1=deadbeef"})
    try:
        urllib.request.urlopen(req, timeout=10)
        raise AssertionError("webhook on unconfigured server must not 2xx")
    except urllib.error.HTTPError as e:
        assert e.code == 500, f"expected 500 (webhook not configured), got {e.code}"
