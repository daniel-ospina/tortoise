"""E2E-3-D: the genuinely LIVE leg of the upgrade flow (Stripe test mode).

Scope (review fix 16c): register a team → create a REAL Checkout session
against Stripe test mode → assert 200 + checkout_url + stripe_customer_id
persisted on the Team node. The 4-webhook-event semantics live in
tests/test_billing.py::TestWebhook (monkeypatched StripeClient) — they are NOT
re-tested here.

Skips cleanly (no error) when STRIPE_TEST_* keys are absent.
"""
import json  # noqa: F401
import os

import pytest

pytestmark = pytest.mark.stripe

def _skip_guard():
    missing = [k for k in ("STRIPE_TEST_SECRET_KEY", "STRIPE_TEST_WEBHOOK_SECRET")
               if not os.environ.get(k)]
    if missing:
        pytest.skip(f"Stripe test keys absent: {missing}")


def test_upgrade_checkout_live_leg():
    """Register → real Checkout session → 200 + checkout_url + customer persisted."""
    _skip_guard()
    import tempfile  # noqa: I001
    from fastapi.testclient import TestClient
    from tortoise.hosted_api import app
    from tortoise.sdk import TortoiseSDK

    with tempfile.TemporaryDirectory() as tmpdir:
        db = os.path.join(tmpdir, "e2e.db")
        old_uri = os.environ.get("TORTOISE_DB_URI")
        os.environ.pop("TORTOISE_DB_URI", None)
        os.environ["TORTOISE_DB_PATH"] = db
        os.environ["STRIPE_SECRET_KEY"] = os.environ["STRIPE_TEST_SECRET_KEY"]
        os.environ["STRIPE_WEBHOOK_SECRET"] = os.environ["STRIPE_TEST_WEBHOOK_SECRET"]
        try:
            sdk = TortoiseSDK(db, namespace="registry")
            with TestClient(app) as tc:
                r = tc.post("/v1/register", json={
                    "email": "e2e-upgrade@example.com",
                    "password": "supersecret1",
                })
                assert r.status_code == 200, r.text
                team_id = r.json()["team_id"]
                key = r.json()["api_key"]
                h = {"Authorization": f"Bearer {key}"}
                # team info exposes the server-resolved default checkout price
                ti = tc.get("/v1/team", headers=h).json()
                assert ti.get("checkout_price_id"), "checkout_price_id missing from /v1/team"
                # real Checkout session creation (Stripe test mode)
                cr = tc.post("/v1/billing/checkout", headers=h, json={
                    "price_id": ti["checkout_price_id"],
                })
                assert cr.status_code == 200, cr.text
                assert cr.json().get("checkout_url", "").startswith("https://checkout.stripe.com/")
                # stripe_customer_id persisted synchronously BEFORE redirect
                rows = sdk._get_registry().query(
                    "MATCH (t:Team {id:$id}) RETURN t.stripe_customer_id",
                    params={"id": team_id}).result_set
                assert rows and rows[0][0], "stripe_customer_id must be persisted at checkout creation"
        finally:
            if old_uri:
                os.environ["TORTOISE_DB_URI"] = old_uri
            else:
                os.environ.pop("TORTOISE_DB_URI", None)
            os.environ.pop("TORTOISE_DB_PATH", None)
