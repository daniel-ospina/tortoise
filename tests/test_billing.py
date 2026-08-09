"""Tests for tortoise.billing (#310) — StripeClient, PriceCatalog,
effective_tier, apply_limits, reconcile_team, plus (Tasks 5/7/8) the
checkout/portal endpoints and the webhook handler.

External Stripe calls are NEVER made: StripeClient is monkeypatched at the
httpx layer (wrapper tests) or at the class-method layer (reconcile/webhook
tests). The live-leg E2E lives in tests/e2e/test_billing_upgrade.py behind
@pytest.mark.stripe (skip-guarded on STRIPE_TEST_* keys).
"""
from __future__ import annotations

import hashlib
import hmac as hmac_mod
import json
import os
import time

import pytest

# #67: pepper is mandatory for the auth module — set before importing the app.
os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
# Register rate limiter + request rate limiter trip in full-suite runs.
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

import tortoise.billing as billing
from tortoise.billing import (
    BillingConfigError,
    BillingError,
    PriceCatalog,
    StripeClient,
    apply_limits,
    effective_tier,
    reconcile_team,
)

# ── Shared fixtures (module-level, used across Tasks 2/5/7/8) ───────────────

# 8 price ids — 4 tiers × monthly/annual. Amounts (USD cents) must match
# pricing.json: annual = monthly × 12 × (1 − 20%).
VALID_CATALOG = {
    "free": {"monthly": {"id": "price_000freeM", "amount_usd": 0},
             "annual": {"id": "price_000freeA", "amount_usd": 0}},
    "solo": {"monthly": {"id": "price_100soloM", "amount_usd": 900},
             "annual": {"id": "price_100soloA", "amount_usd": 8640}},
    "pro": {"monthly": {"id": "price_200proMM", "amount_usd": 2500},
            "annual": {"id": "price_200proAA", "amount_usd": 24000}},
    "team": {"monthly": {"id": "price_300teamM", "amount_usd": 14900},
             "annual": {"id": "price_300teamA", "amount_usd": 143040}},
}

# Stripe Subscription object shape (items[0].price.id is the tier key).
FIXTURE_SUB = {
    "id": "sub_123",
    "status": "active",
    "cancel_at_period_end": False,
    "current_period_end": 9999999999,
    "items": {"data": [{"price": {"id": "price_200proMM"}}]},
}


def _sign(payload: bytes, secret: str, ts: int | None = None,
          extra_sigs: list[str] | None = None) -> str:
    """Build a Stripe-Signature header over raw payload bytes."""
    ts = ts if ts is not None else int(time.time())
    sig = hmac_mod.new(secret.encode(), f"{ts}.".encode() + payload,
                       hashlib.sha256).hexdigest()
    header = f"t={ts},v1={sig}"
    if extra_sigs:
        header += "".join(f",v1={s}" for s in extra_sigs)
    return header


@pytest.fixture
def signed_payload():
    """Build (raw_payload_bytes, signature_header, secret) triples."""
    def _make(payload: dict, secret: str = "whsec_test", ts: int | None = None,
              extra_sigs: list[str] | None = None, tamper: bool = False):
        raw = json.dumps(payload).encode()
        if tamper:
            header = _sign(raw[:-1], secret, ts=ts, extra_sigs=extra_sigs)
            raw = raw + b" "  # modified AFTER signing → hmac mismatch
        else:
            header = _sign(raw, secret, ts=ts, extra_sigs=extra_sigs)
        return raw, header, secret
    return _make


@pytest.fixture
def stripe_env(monkeypatch):
    """Valid billing env: Stripe keys + the 8-price catalog."""
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_123")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setenv("STRIPE_PRICE_IDS", json.dumps(VALID_CATALOG))


@pytest.fixture
def billing_sdk(monkeypatch, tmp_path):
    """SDK against an embedded FalkorDBLite DB (TORTOISE_DB_PATH pattern)."""
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "billing.db"))
    from tortoise.sdk import TortoiseSDK
    sdk = TortoiseSDK(str(tmp_path / "billing.db"), namespace="registry")
    yield sdk
    sdk.close()


class _FakeResponse:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code
        self.text = json.dumps(data)

    def json(self):
        return self._data


class _FakeHttpxClient:
    """Records (method, url, params) and returns canned Stripe responses.

    Every instance registers itself in ``_instances`` so tests can assert on
    the requests the module under test actually made.
    """

    _instances: list["_FakeHttpxClient"] = []

    def __init__(self, **kwargs):
        self.requests: list[tuple] = []
        _FakeHttpxClient._instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, data=None, headers=None, **kwargs):
        self.requests.append(("POST", url, data))
        return _FakeResponse({"id": "cus_new123", "url": "https://checkout.stripe.com/pay/abc"})

    def get(self, url, params=None, headers=None, **kwargs):
        self.requests.append(("GET", url, params))
        return _FakeResponse({"data": [], "id": "cus_new123"})


def _last_http_requests() -> list[tuple]:
    """Requests recorded by the most recently-created fake httpx client."""
    if not _FakeHttpxClient._instances:
        return []
    return _FakeHttpxClient._instances[-1].requests


# ── Signature verification ──────────────────────────────────────────────────

class TestSignatureVerification:
    def test_signature_verify_ok(self, signed_payload):
        raw, header, secret = signed_payload({"type": "checkout.session.completed", "id": "evt_1"})
        event = StripeClient(secret_key="sk_test_x").verify_webhook_signature(raw, header, secret)
        assert event["type"] == "checkout.session.completed"

    def test_signature_tampered_payload_400(self, signed_payload):
        raw, header, secret = signed_payload({"type": "checkout.session.completed"}, tamper=True)
        with pytest.raises(BillingError):
            StripeClient(secret_key="sk_test_x").verify_webhook_signature(raw, header, secret)

    def test_signature_expired_timestamp(self, signed_payload):
        raw, header, secret = signed_payload({"type": "x"}, ts=int(time.time()) - 3600)
        with pytest.raises(BillingError, match="expired|timestamp"):
            StripeClient(secret_key="sk_test_x").verify_webhook_signature(raw, header, secret)

    def test_signature_multiple_v1_accepted(self, signed_payload):
        raw, header, secret = signed_payload(
            {"type": "x"}, extra_sigs=["0" * 64])
        event = StripeClient(secret_key="sk_test_x").verify_webhook_signature(raw, header, secret)
        assert event["type"] == "x"

    def test_missing_webhook_secret_raises_config_error(self):
        with pytest.raises(BillingConfigError):
            StripeClient(secret_key="sk_test_x", webhook_secret="").verify_webhook_signature(
                b"{}", "t=1,v1=abc")


# ── Price catalog ───────────────────────────────────────────────────────────

class TestPriceCatalog:
    def test_catalog_loads_8_prices(self):
        cat = PriceCatalog(json.dumps(VALID_CATALOG))
        assert len(cat.price_ids()) == 8

    def test_catalog_rejects_unknown_tier(self):
        bad = dict(VALID_CATALOG)
        bad["enterprise"] = {"monthly": "price_xM", "annual": "price_xA"}
        with pytest.raises(BillingError, match="unknown tier"):
            PriceCatalog(json.dumps(bad))

    def test_catalog_rejects_missing_interval(self):
        bad = json.loads(json.dumps(VALID_CATALOG))
        del bad["solo"]["annual"]
        with pytest.raises(BillingError, match="annual|missing"):
            PriceCatalog(json.dumps(bad))

    def test_catalog_rejects_wrong_annual_discount(self):
        bad = json.loads(json.dumps(VALID_CATALOG))
        bad["solo"]["annual"]["amount_usd"] = 10800  # 0% discount, must be 20%
        with pytest.raises(BillingError, match="annual"):
            PriceCatalog(json.dumps(bad))

    def test_catalog_rejects_non_price_id(self):
        bad = json.loads(json.dumps(VALID_CATALOG))
        bad["solo"]["monthly"] = {"id": "cus_123", "amount_usd": 900}
        with pytest.raises(BillingError, match="price_"):
            PriceCatalog(json.dumps(bad))

    def test_tier_for_price(self):
        cat = PriceCatalog(json.dumps(VALID_CATALOG))
        assert cat.tier_for_price("price_200proMM") == "pro"
        assert cat.tier_for_price("price_100soloA") == "solo"
        assert cat.tier_for_price("price_000freeM") == "free"

    def test_tier_for_price_unknown_id_raises(self):
        cat = PriceCatalog(json.dumps(VALID_CATALOG))
        with pytest.raises(BillingError, match="unknown price"):
            cat.tier_for_price("price_nope")

    def test_catalog_missing_env_degrades(self, monkeypatch):
        monkeypatch.delenv("STRIPE_PRICE_IDS", raising=False)
        with pytest.raises(BillingConfigError):
            PriceCatalog()  # raises at first use, not at import
        import tortoise.billing  # noqa: F401 — import must never raise

    def test_stripe_client_missing_secret_raises_lazy(self, monkeypatch):
        monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
        with pytest.raises(BillingConfigError):
            StripeClient()


# ── StripeClient wrapper (httpx layer, form-encoded) ────────────────────────

class TestStripeClient:
    def test_create_customer(self, monkeypatch):
        monkeypatch.setattr("httpx.Client", _FakeHttpxClient)
        client = StripeClient(secret_key="sk_test_123")
        cid = client.create_customer("owner@example.com")
        assert cid == "cus_new123"
        method, url, params = _last_http_requests()[0]
        assert method == "POST"
        assert url == "https://api.stripe.com/v1/customers"
        assert params == {"email": "owner@example.com"}

    def test_create_checkout_session_form_encoded(self, monkeypatch):
        monkeypatch.setattr("httpx.Client", _FakeHttpxClient)
        client = StripeClient(secret_key="sk_test_123")
        url = client.create_checkout_session(
            "team_1", "price_200proMM", "cus_1",
            "https://app.example.com/team?session_id={CHECKOUT_SESSION_ID}",
            "https://app.example.com/team?checkout=cancelled")
        assert url == "https://checkout.stripe.com/pay/abc"
        method, api_url, params = _last_http_requests()[0]
        assert method == "POST" and api_url.endswith("/checkout/sessions")
        assert params["mode"] == "subscription"
        assert params["customer"] == "cus_1"
        assert params["client_reference_id"] == "team_1"
        assert params["metadata[team_id]"] == "team_1"

    def test_create_portal_session(self, monkeypatch):
        monkeypatch.setattr("httpx.Client", _FakeHttpxClient)
        client = StripeClient(secret_key="sk_test_123")
        url = client.create_portal_session("cus_1", "https://app.example.com/team")
        assert url == "https://checkout.stripe.com/pay/abc"
        method, api_url, params = _last_http_requests()[0]
        assert method == "POST" and api_url.endswith("/billing_portal/sessions")
        assert params["customer"] == "cus_1"

    def test_get_subscription(self, monkeypatch):
        monkeypatch.setattr("httpx.Client", _FakeHttpxClient)
        client = StripeClient(secret_key="sk_test_123")
        sub = client.get_subscription("sub_1")
        assert sub == {"data": [], "id": "cus_new123"}
        assert _last_http_requests()[0][1].endswith("/subscriptions/sub_1")


# ── effective_tier (lazy grace) ─────────────────────────────────────────────

class TestEffectiveTier:
    def test_past_due_within_grace_keeps_tier(self):
        team = {"tier": "pro", "subscription_status": "past_due",
                "grace_until": time.time() + 3600}
        assert effective_tier(team) == "pro"

    def test_past_due_grace_expired_degrades_to_free(self):
        team = {"tier": "pro", "subscription_status": "past_due",
                "grace_until": time.time() - 1}
        assert effective_tier(team) == "free"

    def test_active_past_period_end_keeps_tier(self):
        """No defensive 'period passed → free' branch (review fix 6): Stripe
        auto-renews, so the webhook (not wall-clock) drives state."""
        team = {"tier": "pro", "subscription_status": "active",
                "current_period_end": time.time() - 1000}
        assert effective_tier(team) == "pro"

    def test_never_upgrades(self):
        team = {"tier": "free", "subscription_status": "active"}
        assert effective_tier(team) == "free"

    def test_no_billing_fields_keeps_stored_tier(self):
        team = {"tier": "solo"}
        assert effective_tier(team) == "solo"


# ── apply_limits / reconcile_team (registry mirror) ─────────────────────────

class TestApplyLimitsAndReconcile:
    def test_apply_limits_writes_tier_and_limits_atomically(self, monkeypatch, billing_sdk):
        sdk = billing_sdk
        team = sdk.team_create("limits-team")
        queries: list[str] = []
        orig_query = sdk._get_registry().query

        def counting_query(q, **kwargs):
            queries.append(q)
            return orig_query(q, **kwargs)

        monkeypatch.setattr(sdk._get_registry(), "query", counting_query)
        apply_limits(sdk, team["id"], "pro")
        assert len(queries) == 1  # single atomic Cypher SET
        t = sdk.team_get(team["id"])
        assert t["tier"] == "pro"
        assert t["max_points"] == 100000   # == max_graph_nodes (GAP-B mapping)
        assert t["max_api_keys"] == 10
        assert t["max_sessions"] == 1000
        assert t["max_users"] == 2
        assert t.get("max_graphs") is None   # pro = unlimited (None not stored)

    def test_reconcile_subscription_repairs_mirror(self, monkeypatch, stripe_env, billing_sdk):
        sdk = billing_sdk
        team = sdk.team_create("recon-team")
        # Drift: registry says free; Stripe says the team pays for pro.
        sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.subscription_id='sub_123'",
            params={"id": team["id"]},
        )
        monkeypatch.setattr(billing.StripeClient, "get_subscription",
                            lambda self, sid: dict(FIXTURE_SUB))
        reconcile_team(sdk, team["id"])
        t = sdk.team_get(team["id"])
        assert t["tier"] == "pro"
        assert t["max_points"] == 100000
        assert t["subscription_status"] == "active"
        assert t["subscription_id"] == "sub_123"

    def test_reconcile_customer_only_matches_first_active(self, monkeypatch, stripe_env, billing_sdk):
        """A team with only stripe_customer_id (missed checkout event) is
        repaired via list_subscriptions — first active sub wins."""
        sdk = billing_sdk
        team = sdk.team_create("customer-only")
        sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.stripe_customer_id='cus_1'",
            params={"id": team["id"]},
        )
        inactive = {"id": "sub_x", "status": "canceled",
                    "items": {"data": [{"price": {"id": "price_100soloM"}}]}}
        active = {"id": "sub_y", "status": "active", "current_period_end": 9999,
                  "cancel_at_period_end": False,
                  "items": {"data": [{"price": {"id": "price_300teamM"}}]}}
        monkeypatch.setattr(billing.StripeClient, "list_subscriptions",
                            lambda self, cid: [inactive, active])
        reconcile_team(sdk, team["id"])
        t = sdk.team_get(team["id"])
        assert t["tier"] == "team"
        assert t["subscription_status"] == "active"

    def test_reconcile_noop_without_identifiers(self, stripe_env, billing_sdk):
        sdk = billing_sdk
        team = sdk.team_create("noop-team")
        reconcile_team(sdk, team["id"])  # no subscription_id / customer_id → no-op
        t = sdk.team_get(team["id"])
        assert t["tier"] == "free"

    def test_reconcile_unknown_price_keeps_tier(self, monkeypatch, stripe_env, billing_sdk):
        """Unparseable price → error surfaces to caller; stored tier untouched."""
        sdk = billing_sdk
        team = sdk.team_create("unknown-price")
        sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.subscription_id='sub_1', t.tier='pro', "
            "t.subscription_status='active'",
            params={"id": team["id"]},
        )
        sub = {"id": "sub_1", "status": "active", "cancel_at_period_end": False,
               "items": {"data": [{"price": {"id": "price_unknown"}}]}}
        monkeypatch.setattr(billing.StripeClient, "get_subscription",
                            lambda self, sid: sub)
        with pytest.raises(BillingError, match="unknown price"):
            reconcile_team(sdk, team["id"])
        t = sdk.team_get(team["id"])
        assert t["tier"] == "pro"  # preserved — never downgraded on unparseable price
        assert t["subscription_status"] == "active"
