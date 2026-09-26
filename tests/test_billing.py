"""Tests for tortoise.billing (#310) — StripeClient, PriceCatalog,
effective_tier, apply_limits, reconcile_org, plus (Tasks 5/7/8) the
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
    reconcile_org,
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

    _instances: list["_FakeHttpxClient"] = []  # noqa: RUF012, UP037

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
        with pytest.raises(BillingError, match="expired|timestamp"):  # noqa: RUF043
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
        with pytest.raises(BillingError, match="annual|missing"):  # noqa: RUF043
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
        assert params["metadata[org_id]"] == "team_1"

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


# ── apply_limits / reconcile_org (registry mirror) ─────────────────────────

class TestApplyLimitsAndReconcile:
    def test_apply_limits_writes_tier_and_limits_atomically(self, monkeypatch, billing_sdk):
        sdk = billing_sdk
        team = sdk.org_create("limits-team")
        # #4010: seed a stored cap FIRST so the assertion below distinguishes
        # "apply_limits DELETES the property" from "the field was never set"
        # (FalkorDB deletes a property written as NULL).
        sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.max_sessions = 1000",
            params={"id": team["id"]},
        )
        assert sdk.org_get(team["id"])["max_sessions"] == 1000
        queries: list[str] = []
        orig_query = sdk._get_registry().query

        def counting_query(q, **kwargs):
            queries.append(q)
            return orig_query(q, **kwargs)

        monkeypatch.setattr(sdk._get_registry(), "query", counting_query)
        apply_limits(sdk, team["id"], "pro")
        assert len(queries) == 1  # single atomic Cypher SET
        t = sdk.org_get(team["id"])
        assert t["tier"] == "pro"
        assert t["max_points"] == 100000   # == max_graph_nodes (GAP-B mapping)
        assert t["max_api_keys"] == 10
        # #4010: the stored 1000 is CLEARED by the tier write (NULL → the
        # property is deleted), so a later reader cannot re-cap the org.
        assert t.get("max_sessions") is None
        assert t["max_users"] == 2
        assert t.get("max_graphs") is None   # pro = unlimited (None not stored)

    def test_reconcile_subscription_repairs_mirror(self, monkeypatch, stripe_env, billing_sdk):
        sdk = billing_sdk
        team = sdk.org_create("recon-team")
        # Drift: registry says free; Stripe says the team pays for pro.
        sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.subscription_id='sub_123'",
            params={"id": team["id"]},
        )
        monkeypatch.setattr(billing.StripeClient, "get_subscription",
                            lambda self, sid: dict(FIXTURE_SUB))
        reconcile_org(sdk, team["id"])
        t = sdk.org_get(team["id"])
        assert t["tier"] == "pro"
        assert t["max_points"] == 100000
        assert t["subscription_status"] == "active"
        assert t["subscription_id"] == "sub_123"

    def test_reconcile_customer_only_matches_first_active(self, monkeypatch, stripe_env, billing_sdk):
        """A team with only stripe_customer_id (missed checkout event) is
        repaired via list_subscriptions — first active sub wins."""
        sdk = billing_sdk
        team = sdk.org_create("customer-only")
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
        reconcile_org(sdk, team["id"])
        t = sdk.org_get(team["id"])
        assert t["tier"] == "team"
        assert t["subscription_status"] == "active"

    def test_reconcile_noop_without_identifiers(self, stripe_env, billing_sdk):
        sdk = billing_sdk
        team = sdk.org_create("noop-team")
        reconcile_org(sdk, team["id"])  # no subscription_id / customer_id → no-op
        t = sdk.org_get(team["id"])
        assert t["tier"] == "free"

    def test_reconcile_unknown_price_keeps_tier(self, monkeypatch, stripe_env, billing_sdk):
        """Unparseable price → error surfaces to caller; stored tier untouched."""
        sdk = billing_sdk
        team = sdk.org_create("unknown-price")
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
            reconcile_org(sdk, team["id"])
        t = sdk.org_get(team["id"])
        assert t["tier"] == "pro"  # preserved — never downgraded on unparseable price
        assert t["subscription_status"] == "active"

    def test_mirror_registry_lane_keeps_the_cancel_flag(self, monkeypatch,
                                                        stripe_env, billing_sdk):
        """The registry (selfhost) twin keeps ``cancel_at_period_end`` — a
        ``:Team`` property with no ``organizations`` column. Pins the twin's
        field set against the seam-aware refactor (#4726): a mutation that
        routes the registry lane through ``update_org_billing``'s allow-list
        drops the flag silently. Reachable in the fixture: ``billing_sdk``
        writes the ``:Team`` node this test reads back.
        """
        monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "registry")
        sdk = billing_sdk
        team = sdk.org_create("cancel-flag")
        sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.subscription_id='sub_cf'",
            params={"id": team["id"]},
        )
        sub = {"id": "sub_cf", "status": "active", "cancel_at_period_end": True,
               "items": {"data": [{"price": {"id": "price_200proMM"}}]}}
        billing.mirror_subscription(sdk, team["id"], sub)
        t = sdk.org_get(team["id"])
        assert t["cancel_at_period_end"] is True
        assert t["subscription_status"] == "active"
        assert t["subscription_id"] == "sub_cf"


# ── Checkout + Portal endpoints (#310, Task 5) ──────────────────────────────

@pytest.fixture
def billing_client(monkeypatch, tmp_path):
    """TestClient + a REAL /v1/register team on an embedded DB.

    Shared by Tasks 5/7/8 (review fix 16a): TORTOISE_DB_PATH embedded pattern
    (mirrors tests/test_quota.py) so no Docker is needed. StripeClient is
    monkeypatched per-test — the fixture itself only wires env + app + a
    registry SDK (same DB) for direct mirror assertions.
    """
    import os  # noqa: I001
    from fastapi.testclient import TestClient
    from tortoise.hosted_api import app
    from tortoise.sdk import TortoiseSDK

    db = os.path.join(tmp_path, "billing_api.db")
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.setenv("TORTOISE_DB_PATH", db)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_123")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setenv("STRIPE_PRICE_IDS", json.dumps(VALID_CATALOG))
    monkeypatch.setenv("RATE_LIMIT_DISABLED", "1")

    sdk = TortoiseSDK(db, namespace="registry")
    with TestClient(app) as tc:
        r = tc.post("/v1/register", json={
            "email": "billing-owner@example.com",
            "password": "supersecret1",
        })
        assert r.status_code == 200, r.text
        body = r.json()
        yield {
            "client": tc,
            "sdk": sdk,
            "org_id": body["org_id"],
            "api_key": body["api_key"],
            "headers": {"Authorization": f"Bearer {body['api_key']}"},
        }
    sdk.close()


class TestCheckoutPortal:
    def test_checkout_creates_customer_persists_before_redirect(self, monkeypatch, billing_client):
        """create_customer precedes the Checkout session, and the Stripe
        customer binding is on the Team node BEFORE the redirect (survives a
        missed first webhook event — scoping P1-2, review fix 1)."""
        order: list[str] = []

        def fake_create_customer(self, email):
            order.append("create_customer")
            return "cus_checkout1"

        def fake_checkout(self, org_id, price_id, customer, success_url, cancel_url):
            order.append("create_checkout_session")
            assert order[0] == "create_customer"  # customer first
            assert customer == "cus_checkout1"    # created id passed through
            assert org_id == billing_client["org_id"]
            assert price_id == "price_200proMM"
            return "https://checkout.stripe.com/pay/session1"

        monkeypatch.setattr(billing.StripeClient, "create_customer", fake_create_customer)
        monkeypatch.setattr(billing.StripeClient, "list_subscriptions", lambda self, cid: [])
        monkeypatch.setattr(billing.StripeClient, "create_checkout_session", fake_checkout)
        r = billing_client["client"].post(
            "/v1/billing/checkout",
            json={"price_id": "price_200proMM"},
            headers=billing_client["headers"],
        )
        assert r.status_code == 200, r.text
        assert r.json()["checkout_url"] == "https://checkout.stripe.com/pay/session1"
        # Customer binding persisted on the Team node before the redirect.
        t = billing_client["sdk"].org_get(billing_client["org_id"])
        assert t["stripe_customer_id"] == "cus_checkout1"
        assert t["customer_email"] == "billing-owner@example.com"

    def test_checkout_provision_path_uses_api_key_created_by(self, monkeypatch, billing_client):
        """Provision-path teams have no Team.email — the email must resolve
        from APIKey.created_by (review fix 1), not 400."""
        sdk = billing_client["sdk"]
        # Simulate a provision-path team: no Team.email, creator on the key.
        sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) REMOVE t.email",
            params={"id": billing_client["org_id"]},
        )
        sdk._get_registry().query(
            "MATCH (k:APIKey {org_id:$tid}) SET k.created_by='provision-user@example.com'",
            params={"tid": billing_client["org_id"]},
        )
        captured: list[str] = []
        monkeypatch.setattr(billing.StripeClient, "create_customer",
                            lambda self, email: captured.append(email) or "cus_prov1")
        monkeypatch.setattr(billing.StripeClient, "list_subscriptions", lambda self, cid: [])
        monkeypatch.setattr(billing.StripeClient, "create_checkout_session",
                            lambda self, tid, pid, cid, su, cu: "https://checkout.stripe.com/pay/p1")
        r = billing_client["client"].post(
            "/v1/billing/checkout",
            json={"price_id": "price_100soloM"},
            headers=billing_client["headers"],
        )
        assert r.status_code == 200, r.text
        assert captured == ["provision-user@example.com"]  # APIKey.created_by, not 400

    # ── #4504: verified session email fallback ──────────────────────────

    def _strip_org_email_sources(self, billing_client):
        """Make the registered org email-less: no Team.email, no key
        created_by — the exact repro shape (OAuth/session org provisioned
        without an email, keys carrying no creator)."""
        sdk = billing_client["sdk"]
        sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) REMOVE t.email",
            params={"id": billing_client["org_id"]},
        )
        sdk._get_registry().query(
            "MATCH (k:APIKey {org_id:$tid}) REMOVE k.created_by",
            params={"tid": billing_client["org_id"]},
        )

    def _stub_checkout(self, monkeypatch, captured: list[str], suffix: str):
        monkeypatch.setattr(billing.StripeClient, "create_customer",
                            lambda self, email: captured.append(email) or f"cus_{suffix}")
        monkeypatch.setattr(billing.StripeClient, "list_subscriptions", lambda self, cid: [])
        monkeypatch.setattr(
            billing.StripeClient, "create_checkout_session",
            lambda self, tid, pid, cid, su, cu: f"https://checkout.stripe.com/pay/{suffix}")

    def test_checkout_session_user_email_fallback(self, monkeypatch, billing_client):
        """#4504: a session-authenticated user whose org has no Team.email and
        whose keys carry no created_by reaches Stripe — the VERIFIED session
        email is the fallback before the 400, and is persisted as
        customer_email like the rest of the chain."""
        from tortoise.hosted_api import app, get_current_org

        self._strip_org_email_sources(billing_client)
        captured: list[str] = []
        self._stub_checkout(monkeypatch, captured, "sess1")
        app.dependency_overrides[get_current_org] = lambda: {
            "org_id": billing_client["org_id"], "key_id": None, "tier": "free",
            "session_user_id": "11111111-1111-1111-1111-111111111111",
            "session_user_email": "session-user@example.com",
        }
        try:
            r = billing_client["client"].post(
                "/v1/billing/checkout", json={"price_id": "price_200proMM"})
        finally:
            app.dependency_overrides.pop(get_current_org, None)
        assert r.status_code == 200, r.text
        assert r.json()["checkout_url"] == "https://checkout.stripe.com/pay/sess1"
        assert captured == ["session-user@example.com"]
        t = billing_client["sdk"].org_get(billing_client["org_id"])
        assert t["customer_email"] == "session-user@example.com"
        assert t["stripe_customer_id"] == "cus_sess1"

    def test_checkout_no_email_anywhere_still_400(self, monkeypatch, billing_client):
        """#4504: the 400 is NOT weakened — with no email on the Team, on any
        key, and no verified session email, checkout is refused before any
        Stripe call (anon/registry context)."""
        from tortoise.hosted_api import app, get_current_org

        self._strip_org_email_sources(billing_client)
        called: list[str] = []
        monkeypatch.setattr(billing.StripeClient, "create_customer",
                            lambda self, e: called.append("create_customer") or "cus_x")
        app.dependency_overrides[get_current_org] = lambda: {
            "org_id": billing_client["org_id"], "key_id": None, "tier": "free",
        }
        try:
            r = billing_client["client"].post(
                "/v1/billing/checkout", json={"price_id": "price_200proMM"})
        finally:
            app.dependency_overrides.pop(get_current_org, None)
        assert r.status_code == 400, r.text
        assert "No customer email" in r.json()["detail"]
        assert called == []  # never reached Stripe

    def test_checkout_team_email_beats_session_email(self, monkeypatch, billing_client):
        """#4504 precedence: Team.email resolves first — the session fallback
        is additive and never outranks the existing chain."""
        from tortoise.hosted_api import app, get_current_org

        captured: list[str] = []
        self._stub_checkout(monkeypatch, captured, "team1")
        app.dependency_overrides[get_current_org] = lambda: {
            "org_id": billing_client["org_id"], "key_id": None, "tier": "free",
            "session_user_email": "session-user@example.com",
        }
        try:
            r = billing_client["client"].post(
                "/v1/billing/checkout", json={"price_id": "price_200proMM"})
        finally:
            app.dependency_overrides.pop(get_current_org, None)
        assert r.status_code == 200, r.text
        # fixture registers with Team.email = billing-owner@example.com
        assert captured == ["billing-owner@example.com"]

    def test_checkout_key_created_by_beats_session_email(self, monkeypatch, billing_client):
        """#4504 precedence: APIKey.created_by resolves before the session
        fallback — provision-path orgs keep their existing billing email."""
        from tortoise.hosted_api import app, get_current_org

        sdk = billing_client["sdk"]
        sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) REMOVE t.email",
            params={"id": billing_client["org_id"]},
        )
        sdk._get_registry().query(
            "MATCH (k:APIKey {org_id:$tid}) SET k.created_by='provision-user@example.com'",
            params={"tid": billing_client["org_id"]},
        )
        captured: list[str] = []
        self._stub_checkout(monkeypatch, captured, "key1")
        app.dependency_overrides[get_current_org] = lambda: {
            "org_id": billing_client["org_id"], "key_id": None, "tier": "free",
            "session_user_email": "session-user@example.com",
        }
        try:
            r = billing_client["client"].post(
                "/v1/billing/checkout", json={"price_id": "price_200proMM"})
        finally:
            app.dependency_overrides.pop(get_current_org, None)
        assert r.status_code == 200, r.text
        assert captured == ["provision-user@example.com"]

    def test_checkout_non_email_created_by_falls_through_to_session_email(
            self, monkeypatch, billing_client):
        """#4504: ``APIKey.created_by`` is a creator ID, not always an email
        (a session-minted key stores the user UUID; key-auth stores "api"). A
        non-email ``created_by`` must fall through to the verified session
        email rather than being posted to Stripe as the customer address."""
        from tortoise.hosted_api import app, get_current_org

        sdk = billing_client["sdk"]
        sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) REMOVE t.email",
            params={"id": billing_client["org_id"]},
        )
        sdk._get_registry().query(
            "MATCH (k:APIKey {org_id:$tid}) "
            "SET k.created_by='11111111-1111-1111-1111-111111111111'",
            params={"tid": billing_client["org_id"]},
        )
        captured: list[str] = []
        self._stub_checkout(monkeypatch, captured, "uuid1")
        app.dependency_overrides[get_current_org] = lambda: {
            "org_id": billing_client["org_id"], "key_id": None, "tier": "free",
            "session_user_email": "session-user@example.com",
        }
        try:
            r = billing_client["client"].post(
                "/v1/billing/checkout", json={"price_id": "price_200proMM"})
        finally:
            app.dependency_overrides.pop(get_current_org, None)
        assert r.status_code == 200, r.text
        # the UUID creator id is NOT a customer email — the session email wins
        assert captured == ["session-user@example.com"]
        t = billing_client["sdk"].org_get(billing_client["org_id"])
        assert t["customer_email"] == "session-user@example.com"

    def test_checkout_api_sentinel_created_by_falls_through_to_session_email(
            self, monkeypatch, billing_client):
        """#4504: the literal ``"api"`` creator id (key-auth mint) is not an
        email either — same fall-through."""
        from tortoise.hosted_api import app, get_current_org

        sdk = billing_client["sdk"]
        sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) REMOVE t.email",
            params={"id": billing_client["org_id"]},
        )
        sdk._get_registry().query(
            "MATCH (k:APIKey {org_id:$tid}) SET k.created_by='api'",
            params={"tid": billing_client["org_id"]},
        )
        captured: list[str] = []
        self._stub_checkout(monkeypatch, captured, "api1")
        app.dependency_overrides[get_current_org] = lambda: {
            "org_id": billing_client["org_id"], "key_id": None, "tier": "free",
            "session_user_email": "session-user@example.com",
        }
        try:
            r = billing_client["client"].post(
                "/v1/billing/checkout", json={"price_id": "price_200proMM"})
        finally:
            app.dependency_overrides.pop(get_current_org, None)
        assert r.status_code == 200, r.text
        assert captured == ["session-user@example.com"]

    def test_checkout_padded_created_by_is_returned_stripped(
            self, monkeypatch, billing_client):
        """#4504 review: the shape gate trims before validating, so a padded
        email-shaped ``created_by`` must be handed to Stripe trimmed — never
        the raw DB string."""
        from tortoise.hosted_api import app, get_current_org

        sdk = billing_client["sdk"]
        sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) REMOVE t.email",
            params={"id": billing_client["org_id"]},
        )
        sdk._get_registry().query(
            "MATCH (k:APIKey {org_id:$tid}) SET k.created_by=' provision@example.com '",
            params={"tid": billing_client["org_id"]},
        )
        captured: list[str] = []
        self._stub_checkout(monkeypatch, captured, "pad1")
        app.dependency_overrides[get_current_org] = lambda: {
            "org_id": billing_client["org_id"], "key_id": None, "tier": "free",
            "session_user_email": "session-user@example.com",
        }
        try:
            r = billing_client["client"].post(
                "/v1/billing/checkout", json={"price_id": "price_200proMM"})
        finally:
            app.dependency_overrides.pop(get_current_org, None)
        assert r.status_code == 200, r.text
        assert captured == ["provision@example.com"]  # trimmed, not padded
        t = sdk.org_get(billing_client["org_id"])
        assert t["customer_email"] == "provision@example.com"

    def test_checkout_reuse_keeps_stored_customer_email(self, monkeypatch, billing_client):
        """#4504 review: when the Stripe customer is reused, a second member's
        session must not rewrite the org's stored billing contact — the mirror
        must keep the address that belongs to the reused customer."""
        from tortoise.hosted_api import app, get_current_org

        sdk = billing_client["sdk"]
        sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.stripe_customer_id='cus_existing', "
            "t.customer_email='first@example.com'",
            params={"id": billing_client["org_id"]},
        )
        created: list[str] = []
        monkeypatch.setattr(billing.StripeClient, "create_customer",
                            lambda self, e: created.append(e) or "cus_new")
        monkeypatch.setattr(billing.StripeClient, "list_subscriptions", lambda self, cid: [])
        monkeypatch.setattr(
            billing.StripeClient, "create_checkout_session",
            lambda self, tid, pid, cid, su, cu: "https://checkout.stripe.com/pay/reuse1")
        app.dependency_overrides[get_current_org] = lambda: {
            "org_id": billing_client["org_id"], "key_id": None, "tier": "free",
            "session_user_email": "second@example.com",
        }
        try:
            r = billing_client["client"].post(
                "/v1/billing/checkout", json={"price_id": "price_200proMM"})
        finally:
            app.dependency_overrides.pop(get_current_org, None)
        assert r.status_code == 200, r.text
        assert created == []  # reused the stored customer, not recreated
        t = sdk.org_get(billing_client["org_id"])
        assert t["customer_email"] == "first@example.com"  # unchanged
        assert t["stripe_customer_id"] == "cus_existing"

    def test_checkout_active_subscription_409(self, monkeypatch, billing_client):
        """Stored mirror active → 409 BEFORE any Stripe call — no customer
        created, no session minted."""
        billing_client["sdk"]._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.subscription_status='active'",
            params={"id": billing_client["org_id"]},
        )
        called: list[str] = []
        monkeypatch.setattr(billing.StripeClient, "create_customer",
                            lambda self, e: called.append("create_customer") or "cus_x")
        monkeypatch.setattr(billing.StripeClient, "create_checkout_session",
                            lambda *a, **k: called.append("checkout") or "https://x")
        r = billing_client["client"].post(
            "/v1/billing/checkout",
            json={"price_id": "price_200proMM"},
            headers=billing_client["headers"],
        )
        assert r.status_code == 409, r.text
        assert called == []  # guard fired before any Stripe call

    def test_checkout_stale_mirror_race_409(self, monkeypatch, billing_client):
        """Clean stored mirror but Stripe lists an active subscription → 409,
        no Checkout session created (review fix 5 — Stripe is the money
        authority, the mirror may read 'free' between checkout + webhook)."""
        sub = {"id": "sub_race", "status": "active",
               "items": {"data": [{"price": {"id": "price_200proMM"}}]}}
        monkeypatch.setattr(billing.StripeClient, "create_customer", lambda self, e: "cus_race1")
        monkeypatch.setattr(billing.StripeClient, "list_subscriptions", lambda self, cid: [sub])
        called: list[str] = []
        monkeypatch.setattr(billing.StripeClient, "create_checkout_session",
                            lambda *a, **k: called.append("checkout") or "https://x")
        r = billing_client["client"].post(
            "/v1/billing/checkout",
            json={"price_id": "price_200proMM"},
            headers=billing_client["headers"],
        )
        assert r.status_code == 409, r.text
        assert called == []  # no session created

    def test_checkout_unknown_price_400(self, billing_client):
        r = billing_client["client"].post(
            "/v1/billing/checkout",
            json={"price_id": "price_nope"},
            headers=billing_client["headers"],
        )
        assert r.status_code == 400, r.text

    def test_checkout_success_url_env_driven(self, monkeypatch, billing_client):
        """success/cancel URLs come from env (review fix 10b), not hardcoded."""
        monkeypatch.setenv(
            "BILLING_SUCCESS_URL",
            "https://app.example.com/success?session_id={CHECKOUT_SESSION_ID}",
        )
        monkeypatch.setenv("BILLING_CANCEL_URL", "https://app.example.com/cancel?checkout=cancelled")
        seen: dict[str, str] = {}

        def fake_checkout(self, org_id, price_id, customer, success_url, cancel_url):
            seen["success_url"] = success_url
            seen["cancel_url"] = cancel_url
            return "https://checkout.stripe.com/pay/e1"

        monkeypatch.setattr(billing.StripeClient, "create_customer", lambda self, e: "cus_e1")
        monkeypatch.setattr(billing.StripeClient, "list_subscriptions", lambda self, c: [])
        monkeypatch.setattr(billing.StripeClient, "create_checkout_session", fake_checkout)
        r = billing_client["client"].post(
            "/v1/billing/checkout",
            json={"price_id": "price_200proMM"},
            headers=billing_client["headers"],
        )
        assert r.status_code == 200, r.text
        assert seen["success_url"] == "https://app.example.com/success?session_id={CHECKOUT_SESSION_ID}"
        assert seen["cancel_url"] == "https://app.example.com/cancel?checkout=cancelled"

    def test_checkout_missing_env_503(self, monkeypatch, billing_client):
        """Lazy config: missing STRIPE_PRICE_IDS → 503, never a crash."""
        monkeypatch.delenv("STRIPE_PRICE_IDS", raising=False)
        r = billing_client["client"].post(
            "/v1/billing/checkout",
            json={"price_id": "price_200proMM"},
            headers=billing_client["headers"],
        )
        assert r.status_code == 503, r.text

    def test_checkout_defaults_follow_dashboard_url(self, monkeypatch, billing_client):
        """#1135: BILLING_*_URL unset → defaults derive from TORTOISE_DASHBOARD_URL
        (env-driven host, never a hardcoded app.premiselabs.co literal)."""
        monkeypatch.setenv("TORTOISE_DASHBOARD_URL", "https://dash.example.com")
        monkeypatch.delenv("BILLING_SUCCESS_URL", raising=False)
        monkeypatch.delenv("BILLING_CANCEL_URL", raising=False)
        seen: dict[str, str] = {}

        def fake_checkout(self, org_id, price_id, customer, success_url, cancel_url):
            seen["success_url"] = success_url
            seen["cancel_url"] = cancel_url
            return "https://checkout.stripe.com/p/e1"

        monkeypatch.setattr(billing.StripeClient, "create_customer", lambda self, e: "cus_e1")
        monkeypatch.setattr(billing.StripeClient, "list_subscriptions", lambda self, c: [])
        monkeypatch.setattr(billing.StripeClient, "create_checkout_session", fake_checkout)
        r = billing_client["client"].post(
            "/v1/billing/checkout",
            json={"price_id": "price_200proMM"},
            headers=billing_client["headers"],
        )
        assert r.status_code == 200, r.text
        assert seen["success_url"] == "https://dash.example.com/team?session_id={CHECKOUT_SESSION_ID}"
        assert seen["cancel_url"] == "https://dash.example.com/team?checkout=cancelled"

    def test_portal_returns_url(self, monkeypatch, billing_client):
        billing_client["sdk"]._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.stripe_customer_id='cus_portal1'",
            params={"id": billing_client["org_id"]},
        )
        monkeypatch.setattr(
            billing.StripeClient, "create_portal_session",
            lambda self, cid, return_url: "https://billing.stripe.com/p/session1",
        )
        r = billing_client["client"].post("/v1/billing/portal", headers=billing_client["headers"])
        assert r.status_code == 200, r.text
        assert r.json()["portal_url"] == "https://billing.stripe.com/p/session1"

    def test_portal_404_no_customer(self, billing_client):
        r = billing_client["client"].post("/v1/billing/portal", headers=billing_client["headers"])
        assert r.status_code == 404, r.text


class TestBillingSupabaseStore:
    """#4640 — Supabase mode: the orgs row is the authority (#669) and the
    registry graph is DELETED there. The checkout sync-persist and the portal
    read must both use the row; the pre-fix code wrote and read the registry,
    so a just-subscribed org's portal-open 404'd with "no Stripe customer".

    The registry-SDK spy is load-bearing: constructing a registry-namespaced
    SDK here would be the #878 resurrection vector and raises. Non-registry
    constructions are left to the real factory.
    """

    ORG_ID = "org_sb_4640"

    @pytest.fixture
    def sb(self, monkeypatch):
        from fastapi.testclient import TestClient

        import tortoise.hosted_api as ha
        import tortoise.supabase_control as sc
        from tests.fake_control_plane import FakeControlPlane

        monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc_role_key_test")
        # PIN the lane: is_supabase_enabled() would otherwise honour an ambient
        # TORTOISE_CONTROL_PLANE=registry and flip these tests to selfhost.
        monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "supabase")
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_123")
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
        monkeypatch.setenv("STRIPE_PRICE_IDS", json.dumps(VALID_CATALOG))
        monkeypatch.setenv("RATE_LIMIT_DISABLED", "1")
        fake = FakeControlPlane({"organizations": [{
            "id": self.ORG_ID, "name": "sb", "tier": "solo",
            "email": "owner@example.com",
            "stripe_customer_id": None, "subscription_status": None,
            "customer_email": None,
        }], "webhook_events": []})
        monkeypatch.setattr(sc, "get_control_plane", lambda: fake)

        _orig_make_sdk = ha._make_sdk

        def _spy_make_sdk(*a, **kw):
            if kw.get("namespace") == "registry":
                raise AssertionError(
                    "registry SDK constructed in Supabase mode (#878)")
            return _orig_make_sdk(*a, **kw)

        monkeypatch.setattr(ha, "_make_sdk", _spy_make_sdk)

        org = {"org_id": self.ORG_ID, "tier": "solo",
               "email": "owner@example.com"}
        ha.app.dependency_overrides[ha.get_current_org_session] = lambda: dict(org)
        try:
            yield TestClient(ha.app), fake
        finally:
            ha.app.dependency_overrides.pop(ha.get_current_org_session, None)

    def test_portal_opens_right_after_a_successful_checkout(self, monkeypatch, sb):
        """The checkout write lands on the orgs row; the portal reads it back
        and passes the authoritative customer id to Stripe."""
        tc, fake = sb
        seen: dict = {}
        monkeypatch.setattr(billing.StripeClient, "create_customer",
                            lambda self, email: "cus_sb_1")
        monkeypatch.setattr(billing.StripeClient, "list_subscriptions",
                            lambda self, cid: [])
        monkeypatch.setattr(billing.StripeClient, "create_checkout_session",
                            lambda self, *a: "https://checkout.stripe.com/pay/sb1")
        monkeypatch.setattr(
            billing.StripeClient, "create_portal_session",
            lambda self, cid, return_url: seen.update(cid=cid) or
            "https://billing.stripe.com/p/sb1")

        r = tc.post("/v1/billing/checkout", json={"price_id": "price_100soloM"})
        assert r.status_code == 200, r.text
        row = fake.tables["organizations"][0]
        assert row["stripe_customer_id"] == "cus_sb_1"
        assert row["customer_email"] == "owner@example.com"

        r2 = tc.post("/v1/billing/portal")
        assert r2.status_code == 200, r2.text
        assert r2.json()["portal_url"] == "https://billing.stripe.com/p/sb1"
        assert seen["cid"] == "cus_sb_1"

    def test_portal_404_when_genuinely_no_customer(self, monkeypatch, sb):
        """No customer binding on the authoritative row → the 404 still fires,
        and no Stripe portal call is made."""
        tc, _fake = sb
        called: list = []
        monkeypatch.setattr(
            billing.StripeClient, "create_portal_session",
            lambda self, cid, return_url: called.append(cid) or "u")
        r = tc.post("/v1/billing/portal")
        assert r.status_code == 404, r.text
        assert "no Stripe customer" in r.json()["detail"]
        assert called == []

    def test_checkout_400_when_resolved_org_has_no_email(self, monkeypatch, sb):
        """Supabase mode with no email link: the resolved org dict carries no
        email and there is no session email → the terminal 400 (the `sdk=None`
        fall-through), never an AttributeError 500, and no Stripe call."""
        tc, _fake = sb
        import tortoise.hosted_api as ha
        org = {"org_id": self.ORG_ID, "tier": "solo"}
        ha.app.dependency_overrides[ha.get_current_org_session] = lambda: dict(org)
        called: list = []
        monkeypatch.setattr(billing.StripeClient, "create_customer",
                            lambda self, e: called.append("create_customer") or "cus_x")
        monkeypatch.setattr(billing.StripeClient, "list_subscriptions",
                            lambda self, cid: called.append("list_subscriptions") or [])
        monkeypatch.setattr(billing.StripeClient, "create_checkout_session",
                            lambda self, *a: called.append("create_checkout_session") or "u")
        r = tc.post("/v1/billing/checkout", json={"price_id": "price_100soloM"})
        assert r.status_code == 400, r.text
        assert "No customer email" in r.json()["detail"]
        assert called == []

    def test_webhook_written_binding_opens_the_portal(self, monkeypatch, sb):
        """The exact production path: `checkout.session.completed` writes the
        binding through the webhook's `update_org_billing` seam, and the
        portal's `org_billing_state` read finds the same column."""
        tc, fake = sb
        org_id = self.ORG_ID
        monkeypatch.setattr(
            billing.StripeClient, "verify_webhook_signature",
            lambda self, raw, sig: {
                "id": "evt_sb_1", "type": "checkout.session.completed",
                "data": {"object": {
                    "client_reference_id": org_id,
                    "customer": "cus_wh_1",
                    "customer_details": {"email": "owner@example.com"},
                    "subscription": None}}})
        monkeypatch.setattr(
            billing.StripeClient, "create_portal_session",
            lambda self, cid, return_url: f"https://billing.stripe.com/p/{cid}")

        r = tc.post("/webhooks/stripe", content=b"{}",
                    headers={"stripe-signature": "t=1,v1=x"})
        assert r.status_code == 200, r.text
        assert fake.tables["organizations"][0]["stripe_customer_id"] == "cus_wh_1"

        r2 = tc.post("/v1/billing/portal")
        assert r2.status_code == 200, r2.text
        assert r2.json()["portal_url"] == "https://billing.stripe.com/p/cus_wh_1"

    def test_checkout_reuses_the_stored_customer(self, monkeypatch, sb):
        """The stored-mirror guard reads the orgs row: a bound customer is
        reused (never a second Stripe customer) and its email is preserved."""
        tc, fake = sb
        fake.tables["organizations"][0]["stripe_customer_id"] = "cus_existing"
        fake.tables["organizations"][0]["customer_email"] = "stored@example.com"
        created: list = []
        monkeypatch.setattr(billing.StripeClient, "create_customer",
                            lambda self, e: created.append(e) or "cus_new")
        monkeypatch.setattr(billing.StripeClient, "list_subscriptions",
                            lambda self, cid: [])
        monkeypatch.setattr(billing.StripeClient, "create_checkout_session",
                            lambda self, *a: "https://checkout.stripe.com/pay/sb2")

        r = tc.post("/v1/billing/checkout", json={"price_id": "price_100soloM"})
        assert r.status_code == 200, r.text
        assert created == [], "a stored customer must be reused, not re-created"
        row = fake.tables["organizations"][0]
        assert row["stripe_customer_id"] == "cus_existing"
        assert row["customer_email"] == "stored@example.com"

    def test_checkout_backfills_a_missing_customer_email(self, monkeypatch, sb):
        """A row with a bound customer but no stored email is backfilled
        (the mixed case of the reuse rule)."""
        tc, fake = sb
        fake.tables["organizations"][0]["stripe_customer_id"] = "cus_existing"
        monkeypatch.setattr(billing.StripeClient, "create_customer",
                            lambda self, e: "cus_new")
        monkeypatch.setattr(billing.StripeClient, "list_subscriptions",
                            lambda self, cid: [])
        monkeypatch.setattr(billing.StripeClient, "create_checkout_session",
                            lambda self, *a: "https://checkout.stripe.com/pay/sb3")

        r = tc.post("/v1/billing/checkout", json={"price_id": "price_100soloM"})
        assert r.status_code == 200, r.text
        row = fake.tables["organizations"][0]
        assert row["stripe_customer_id"] == "cus_existing"
        assert row["customer_email"] == "owner@example.com"

    def test_org_email_beats_the_session_email(self, monkeypatch, sb):
        """#4508 precedence: the authoritative org row's email wins over the
        clicking member's session email."""
        tc, _fake = sb
        import tortoise.hosted_api as ha
        org = {"org_id": self.ORG_ID, "tier": "solo",
               "email": "org@example.com",
               "session_user_email": "member@example.com"}
        ha.app.dependency_overrides[ha.get_current_org_session] = lambda: dict(org)
        created: list = []
        monkeypatch.setattr(billing.StripeClient, "create_customer",
                            lambda self, e: created.append(e) or "cus_p")
        monkeypatch.setattr(billing.StripeClient, "list_subscriptions",
                            lambda self, cid: [])
        monkeypatch.setattr(billing.StripeClient, "create_checkout_session",
                            lambda self, *a: "https://checkout.stripe.com/pay/sb4")

        r = tc.post("/v1/billing/checkout", json={"price_id": "price_100soloM"})
        assert r.status_code == 200, r.text
        assert created == ["org@example.com"]

    def test_checkout_409_when_row_already_active(self, monkeypatch, sb):
        """The stored-mirror guard reads subscription_status from the row and
        rejects BEFORE any Stripe call."""
        tc, fake = sb
        fake.tables["organizations"][0]["subscription_status"] = "active"
        called: list = []
        monkeypatch.setattr(billing.StripeClient, "create_customer",
                            lambda self, e: called.append("create_customer") or "cus_x")
        monkeypatch.setattr(billing.StripeClient, "list_subscriptions",
                            lambda self, cid: called.append("list_subscriptions") or [])
        monkeypatch.setattr(billing.StripeClient, "create_checkout_session",
                            lambda self, *a: called.append("create_checkout_session") or "u")
        r = tc.post("/v1/billing/checkout", json={"price_id": "price_100soloM"})
        assert r.status_code == 409, r.text
        assert called == [], "the active-status guard must reject before any Stripe call"

    def test_mirror_subscription_writes_the_orgs_row_not_the_registry(self, sb):
        """#4726: in Supabase mode ``mirror_subscription`` writes the billing
        state to the authoritative ``organizations`` row, exactly as the
        webhook's ``_set`` does. The pre-fix unconditional
        ``MATCH (t:Team) SET`` matched 0 rows (silent no-op) and resurrected
        the deleted registry graph by executing on it (#878).

        Mutation that makes this fail: drop the Supabase branch — the mirror
        then calls ``sdk._get_registry()`` (this sentinel raises) and the
        row's ``subscription_status``/``subscription_id`` stay None.
        Reachable in the fixture: the ``sb`` row exists and
        ``get_control_plane`` is patched to it.
        """
        _, fake = sb

        class _NoRegistrySdk:
            def _get_registry(self):  # pragma: no cover — must never run
                raise AssertionError(
                    "Supabase mode must not touch the registry graph (#878)")

        sub = {"id": "sub_sb_4726", "status": "active",
               "cancel_at_period_end": True,
               "current_period_start": 1756512000,
               "current_period_end": 1759104000,
               "items": {"data": [{"price": {"id": "price_200proMM"}}]}}
        summary = billing.mirror_subscription(
            _NoRegistrySdk(), self.ORG_ID, sub,
            customer_email="owner@example.com")
        assert summary == {"tier": "pro", "interval": "monthly",
                           "status": "active"}
        row = fake.tables["organizations"][0]
        assert row["tier"] == "pro"
        assert row["subscription_status"] == "active"
        assert row["subscription_id"] == "sub_sb_4726"
        assert row["customer_email"] == "owner@example.com"
        # epoch ints are normalised by update_org_billing (PostgREST cannot
        # bind a bare number to timestamptz) — both bounds land.
        assert row["current_period_start"] == "2025-08-30T00:00:00+00:00"
        assert row["current_period_end"] == "2025-09-29T00:00:00+00:00"
        # ``cancel_at_period_end`` has no ``organizations`` column — it is the
        # registry-twin property and must not be written to the row.
        assert "cancel_at_period_end" not in row

    def test_reconcile_org_reads_the_orgs_row_in_supabase_mode(self, sb, monkeypatch):
        """#4726: ``reconcile_org`` reads the subscription/customer identifiers
        from the same store the mirror writes — the orgs row — and never
        constructs a registry-namespaced SDK (``sdk=None``).

        Mutation that makes this fail: keep the registry read — the pre-fix
        code calls ``sdk._get_registry()`` and raises AttributeError on
        ``None``. Reachable in the fixture: the ``sb`` row is seeded with both
        identifiers and ``StripeClient.get_subscription`` is stubbed to Stripe
        truth.
        """
        _, fake = sb
        fake.tables["organizations"][0].update({
            "subscription_id": "sub_sb_4726r",
            "stripe_customer_id": "cus_sb_4726r",
        })
        monkeypatch.setattr(
            billing.StripeClient, "get_subscription",
            lambda self, sid: {"id": sid, "status": "active",
                               "items": {"data": [
                                   {"price": {"id": "price_300teamM"}}]}})
        summary = billing.reconcile_org(None, self.ORG_ID)
        assert summary["action"] == "mirror_subscription"
        assert summary["tier"] == "team"
        row = fake.tables["organizations"][0]
        assert row["tier"] == "team"
        assert row["subscription_status"] == "active"
        assert row["subscription_id"] == "sub_sb_4726r"

    def test_reconcile_org_raises_when_the_row_is_absent(self, sb, monkeypatch):
        """Supabase mode: an absent org row is a LOUD ``BillingError`` (the
        registry lane's "not found" contract, now seam-aware) — never a
        silent 0-row no-op, and never a registry-SDK construction.

        Mutation that makes this fail: drop the ``if not row: raise`` — the
        absent row then falls through to the no-op return. Reachable in the
        fixture: ``sb`` holds only ``ORG_ID``, so a different id is absent.
        """
        _, _fake = sb
        monkeypatch.setattr(
            billing.StripeClient, "get_subscription",
            lambda self, sid: (_ for _ in ()).throw(
                AssertionError("must not reach Stripe for an absent org")))
        with pytest.raises(BillingError, match="not found"):
            billing.reconcile_org(None, "org_missing_4726")


class TestWebhook:
    """POST /webhooks/stripe — 4-event semantics, dedup, security (Task 7)."""

    @staticmethod
    def _post(client, event, sig="t=1700000000,v1=deadbeef"):
        return client.post(
            "/webhooks/stripe",
            content=json.dumps(event),
            headers={"stripe-signature": sig},
        )

    @staticmethod
    def _verify(event):
        from tortoise import billing as bl  # noqa: F401

        def fake_verify(self, payload, sig_header):
            return event

        return fake_verify

    def _bind_customer(self, billing_client, customer_id, org_id=None):
        """Persist the stripe_customer_id binding (the checkout endpoint does
        this BEFORE redirect — webhook events are customer-bound, never ref-bound
        (Qwen review P0: client_reference_id is attacker-controlled)."""
        tid = org_id or billing_client["org_id"]
        billing_client["sdk"]._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.stripe_customer_id=$cid",
            params={"id": tid, "cid": customer_id})

    def _mirror(self, billing_client, org_id=None):
        sdk = billing_client["sdk"]
        tid = org_id or billing_client["org_id"]
        rows = sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) RETURN t.tier, t.subscription_status, "
            "t.stripe_customer_id, t.subscription_id, t.grace_until",
            params={"id": tid},
        ).result_set
        return rows[0] if rows else None

    def test_checkout_completed_activates_team(self, monkeypatch, billing_client):
        from tortoise import billing as bl
        org_id = billing_client["org_id"]
        self._bind_customer(billing_client, "cus_1")
        monkeypatch.setattr(bl.StripeClient, "verify_webhook_signature", self._verify({
            "type": "checkout.session.completed", "id": "evt_c1",
            "data": {"object": {"client_reference_id": org_id, "customer": "cus_1",
                                "customer_details": {"email": "o@e.com"},
                                "subscription": "sub_1"}}}))
        monkeypatch.setattr(bl.StripeClient, "get_subscription",
                            lambda self, sid: FIXTURE_SUB)
        r = self._post(billing_client["client"], {})
        assert r.status_code == 200, r.text
        tier, status, cust, sub_id, _ = self._mirror(billing_client)
        assert (tier, status, cust, sub_id) == ("pro", "active", "cus_1", "sub_1")

    def test_webhook_analytics_carries_plan_and_tier(
            self, monkeypatch, tmp_path, billing_client):
        """#3821 regression: the billing emit passes plan/tier to
        `_track_analytics_event`; before #3821 neither was in
        `_ALLOWED_ANALYTICS_PROPS`, so every billing row was written
        STRIPPED — the live silent loss since c928b0316 (2026-08-09)."""
        from tortoise import billing as bl
        from tortoise import hosted_api as ha

        org_id = billing_client["org_id"]
        self._bind_customer(billing_client, "cus_3821")
        fallback = tmp_path / "billing-analytics.jsonl"
        monkeypatch.setattr(ha, "_ANALYTICS_FALLBACK_PATH", str(fallback))
        for var in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY",
                    "SUPABASE_SERVICE_ROLE_KEY"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setattr(bl.StripeClient, "verify_webhook_signature",
                            self._verify({
            "type": "checkout.session.completed", "id": "evt_3821",
            "data": {"object": {"client_reference_id": org_id,
                                "customer": "cus_3821",
                                "customer_details": {"email": "o@e.com"},
                                "subscription": "sub_3821"}}}))
        monkeypatch.setattr(bl.StripeClient, "get_subscription",
                            lambda self, sid: FIXTURE_SUB)
        r = self._post(billing_client["client"], {})
        assert r.status_code == 200, r.text
        rows = [json.loads(line) for line in
                fallback.read_text().splitlines() if line.strip()]
        assert rows, "the webhook must emit a billing analytics row"
        props = rows[-1]["properties"]
        assert props.get("plan") == "pro"
        assert props.get("tier") == "pro"
        assert props.get("status") == "checkout.session.completed"

    def test_webhook_replay_dedup_single_processing(self, monkeypatch, billing_client):
        from tortoise import billing as bl
        from tortoise import notify as nt
        org_id = billing_client["org_id"]
        calls = []
        monkeypatch.setattr(nt, "notify_billing_event",
                            lambda *a, **k: calls.append(a))
        self._bind_customer(billing_client, "cus_2")
        monkeypatch.setattr(bl.StripeClient, "verify_webhook_signature", self._verify({
            "type": "checkout.session.completed", "id": "evt_dedup",
            "data": {"object": {"client_reference_id": org_id, "customer": "cus_2",
                                "subscription": "sub_2"}}}))
        monkeypatch.setattr(bl.StripeClient, "get_subscription",
                            lambda self, sid: FIXTURE_SUB)
        for _ in range(2):  # Stripe retry
            r = self._post(billing_client["client"], {})
            assert r.status_code == 200
        assert len(calls) == 1, "notifications must fire once on replay"

    def test_webhook_bad_signature_400(self, monkeypatch, billing_client):
        from tortoise import billing as bl
        monkeypatch.setattr(bl.StripeClient, "verify_webhook_signature",
                            lambda self, p, s: (_ for _ in ()).throw(
                                bl.BillingError("bad")))
        r = self._post(billing_client["client"], {"type": "x"})
        assert r.status_code == 400

    def test_webhook_expired_timestamp_400(self, monkeypatch, billing_client):
        from tortoise import billing as bl
        monkeypatch.setattr(bl.StripeClient, "verify_webhook_signature",
                            lambda self, p, s: (_ for _ in ()).throw(
                                bl.BillingError("outside tolerance")))
        r = self._post(billing_client["client"], {"type": "x"})
        assert r.status_code == 400

    def test_webhook_payment_failed_sets_grace(self, monkeypatch, billing_client):
        from tortoise import billing as bl
        org_id = billing_client["org_id"]
        self._bind_customer(billing_client, "cus_1")
        monkeypatch.setattr(bl.StripeClient, "verify_webhook_signature", self._verify({
            "type": "invoice.payment_failed", "id": "evt_pf",
            "data": {"object": {"client_reference_id": org_id,
                                "customer": "cus_1",
                                "lines": {"data": [{"period": {"end": 4102444800}}]}}}}))
        r = self._post(billing_client["client"], {})
        assert r.status_code == 200
        _, status, _, _, grace = self._mirror(billing_client)
        assert status == "past_due" and grace

    def test_webhook_cancel_at_period_end_keeps_tier(self, monkeypatch, billing_client):
        from tortoise import billing as bl
        org_id = billing_client["org_id"]
        # set the team to pro first (simulate active sub)
        billing_client["sdk"]._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.tier='pro', t.subscription_status='active'",
            params={"id": org_id})
        self._bind_customer(billing_client, "cus_1")
        monkeypatch.setattr(bl.StripeClient, "verify_webhook_signature", self._verify({
            "type": "customer.subscription.updated", "id": "evt_cae",
            "data": {"object": {"client_reference_id": org_id, "id": "sub_1",
                                "customer": "cus_1", "status": "active",
                                "cancel_at_period_end": True}}}))
        r = self._post(billing_client["client"], {})
        assert r.status_code == 200
        tier, status, *_ = self._mirror(billing_client)  # noqa: RUF059
        assert tier == "pro", "cancel_at_period_end must keep tier until period end"

    def test_webhook_subscription_updated_canceled_reverts(self, monkeypatch, billing_client):
        """review fix 11: status='canceled' via .updated (deleted event dropped)."""
        from tortoise import billing as bl
        org_id = billing_client["org_id"]
        billing_client["sdk"]._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.tier='team', t.subscription_status='active'",
            params={"id": org_id})
        self._bind_customer(billing_client, "cus_1")
        monkeypatch.setattr(bl.StripeClient, "verify_webhook_signature", self._verify({
            "type": "customer.subscription.updated", "id": "evt_canc",
            "data": {"object": {"client_reference_id": org_id, "id": "sub_1",
                                "customer": "cus_1", "status": "canceled",
                                "cancel_at_period_end": False}}}))
        r = self._post(billing_client["client"], {})
        assert r.status_code == 200
        tier, status, *_ = self._mirror(billing_client)
        assert tier == "free" and status == "canceled"

    def test_webhook_unknown_price_keeps_tier_and_notifies(self, monkeypatch, billing_client):
        """review fix 7: price not in STRIPE_PRICE_IDS → keep tier + ops notify."""
        from tortoise import billing as bl
        from tortoise import notify as nt
        org_id = billing_client["org_id"]
        billing_client["sdk"]._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.tier='pro', t.subscription_status='active'",
            params={"id": org_id})
        notified = []
        monkeypatch.setattr(nt, "notify_billing_event",
                            lambda *a, **k: notified.append(a))
        self._bind_customer(billing_client, "cus_3")
        monkeypatch.setattr(bl.StripeClient, "verify_webhook_signature", self._verify({
            "type": "checkout.session.completed", "id": "evt_unk",
            "data": {"object": {"client_reference_id": org_id, "customer": "cus_3",
                                "subscription": "sub_3"}}}))
        monkeypatch.setattr(bl.StripeClient, "get_subscription",
                            lambda self, sid: {"items": [{"price": {"id": "price_UNKNOWN"}}]})
        r = self._post(billing_client["client"], {})
        assert r.status_code == 200
        tier, status, *_ = self._mirror(billing_client)  # noqa: RUF059
        assert tier == "pro", "unknown price must NOT downgrade an active sub"
        assert notified, "ops notification must fire on unknown price"

    def test_webhook_deleted_reverts_free(self, monkeypatch, billing_client):
        from tortoise import billing as bl
        org_id = billing_client["org_id"]
        billing_client["sdk"]._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.tier='team', t.subscription_status='active'",
            params={"id": org_id})
        self._bind_customer(billing_client, "cus_1")
        monkeypatch.setattr(bl.StripeClient, "verify_webhook_signature", self._verify({
            "type": "customer.subscription.deleted", "id": "evt_del",
            "data": {"object": {"client_reference_id": org_id, "customer": "cus_1"}}}))
        r = self._post(billing_client["client"], {})
        assert r.status_code == 200
        tier, status, *_ = self._mirror(billing_client)
        assert tier == "free" and status == "canceled"

    def test_webhook_unhandled_type_200(self, monkeypatch, billing_client):
        from tortoise import billing as bl
        monkeypatch.setattr(bl.StripeClient, "verify_webhook_signature", self._verify({
            "type": "charge.succeeded", "id": "evt_other",
            "data": {"object": {"client_reference_id": billing_client["org_id"]}}}))
        r = self._post(billing_client["client"], {})
        assert r.status_code == 200

    def test_webhook_no_team_binding_200(self, monkeypatch, billing_client):
        from tortoise import billing as bl
        monkeypatch.setattr(bl.StripeClient, "verify_webhook_signature", self._verify({
            "type": "checkout.session.completed", "id": "evt_noteam",
            "data": {"object": {"customer": "cus_x", "subscription": "sub_x"}}}))
        r = self._post(billing_client["client"], {})
        assert r.status_code == 200
        assert r.json()["detail"] == "no team binding"

    def test_webhook_audit_recorded(self, monkeypatch, billing_client):
        import tortoise.hosted_api as ha
        from tortoise import billing as bl
        org_id = billing_client["org_id"]
        audited = []

        async def fake_audit(*a, **k):
            audited.append(a)

        monkeypatch.setattr(ha, "_async_audit", fake_audit)
        self._bind_customer(billing_client, "cus_1")
        monkeypatch.setattr(bl.StripeClient, "verify_webhook_signature", self._verify({
            "type": "customer.subscription.deleted", "id": "evt_audit",
            "data": {"object": {"client_reference_id": org_id, "customer": "cus_1"}}}))
        r = self._post(billing_client["client"], {})
        assert r.status_code == 200
        ops = [a[2] for a in audited if len(a) > 2]  # (request, org_id, operation)
        assert "billing_cancel" in ops, "billing_cancel audit event must be recorded"

    def test_webhook_failure_log_redacts_secret(self, monkeypatch, billing_client, caplog):
        """review fix 9: no secret value leaks into webhook error logs."""
        import logging  # noqa: I001
        from tortoise import billing as bl
        monkeypatch.setattr(bl.StripeClient, "verify_webhook_signature",
                            lambda self, p, s: (_ for _ in ()).throw(
                                bl.BillingError("whsec_test leaked in message")))
        with caplog.at_level(logging.WARNING):
            r = self._post(billing_client["client"], {"type": "x"})
        assert r.status_code == 400
        joined = "\n".join(r.message for r in caplog.records)
        assert "whsec_test" not in joined


class TestBootReconcile:
    """Task 8 — boot reconcile repairs drift without ever blocking boot."""

    def test_boot_reconcile_repairs_drift(self, monkeypatch, billing_client):
        """Out-of-band Stripe change (e.g. portal downgrade) corrected at boot."""
        from tortoise import billing as bl
        org_id = billing_client["org_id"]
        sdk = billing_client["sdk"]
        # team has an active pro subscription in the mirror
        sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.tier='pro', t.subscription_status='active', "
            "t.subscription_id='sub_1', t.stripe_customer_id='cus_1'",
            params={"id": org_id})
        # Stripe truth: subscription downgraded to solo
        monkeypatch.setattr(bl.StripeClient, "get_subscription",
                            lambda self, sid: {"id": "sub_1", "status": "active",
                                               "items": {"data": [{"price": {"id": "price_100soloM"}}]}})
        summary = bl.reconcile_org(sdk, org_id)
        assert summary["action"] == "mirror_subscription"
        row = sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) RETURN t.tier", params={"id": org_id}).result_set
        assert row[0][0] == "solo", "mirror must converge to Stripe truth"

    def test_mirror_writes_both_period_bounds_and_never_nulls_a_stored_one(
            self, monkeypatch, billing_client):
        """#4216 → mutation: bind ``current_period_end`` UNCONDITIONALLY
        (``params["period_end"] = sub.get(...)``) — the pre-fix mirror's shape.

        ``mirror_subscription`` is an AUTHORING path for the subscription: the
        authoritative push must persist the meter window as a PAIR and must
        never NULL a stored bound just because a payload omits it — a NULL end
        makes ``metering._current_period`` RAISE, dropping the org's increments
        and leaving its cohort cap unenforceable. Driven through the REAL
        ``reconcile_org`` (whose only writer is the mirror) and read through the
        REAL meter.

        RED pre-fix: the stored ``current_period_end`` is cleared (the
        unconditional bind writes ``None``) and ``_current_period`` raises.
        """
        from datetime import datetime

        from tortoise import billing as bl
        from tortoise import metering as m

        monkeypatch.setattr(m, "_supabase_mode", lambda: False)  # registry lane
        org_id = billing_client["org_id"]
        sdk = billing_client["sdk"]
        start = int(datetime.fromisoformat(
            "2026-09-03T00:00:00+00:00").timestamp())
        end = int(datetime.fromisoformat(
            "2026-10-03T00:00:00+00:00").timestamp())
        sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.subscription_id='sub_4216_mirror', "
            "t.current_period_start=$ps, t.current_period_end=$pe",
            params={"id": org_id, "ps": start, "pe": end})

        # Stripe truth: this payload OMITS both bounds — it must not clear them.
        monkeypatch.setattr(bl.StripeClient, "get_subscription",
                            lambda self, sid: {
                                "id": "sub_4216_mirror", "status": "active",
                                "items": {"data": [
                                    {"price": {"id": "price_200proMM"}}]}})
        summary = bl.reconcile_org(sdk, org_id)
        assert summary["action"] == "mirror_subscription"

        row = sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) RETURN t.current_period_start, "
            "t.current_period_end", params={"id": org_id}).result_set[0]
        assert row[0] == start and row[1] == end, row
        assert m._current_period(org_id).end_iso == "2026-10-03T00:00:00+00:00"

        # ...and when the payload DOES carry the bounds, the mirror writes them.
        # #4216 / Stripe `2025-03-31.basil`: the bounds live on the ITEM here —
        # the reader must fall back to it (top-level absent).
        new_start = int(datetime.fromisoformat(
            "2026-10-03T00:00:00+00:00").timestamp())
        new_end = int(datetime.fromisoformat(
            "2026-11-03T00:00:00+00:00").timestamp())
        monkeypatch.setattr(bl.StripeClient, "get_subscription",
                            lambda self, sid: {
                                "id": "sub_4216_mirror", "status": "active",
                                "items": {"data": [
                                    {"price": {"id": "price_200proMM"},
                                     "current_period_start": new_start,
                                     "current_period_end": new_end}]}})
        bl.reconcile_org(sdk, org_id)
        row = sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) RETURN t.current_period_start, "
            "t.current_period_end", params={"id": org_id}).result_set[0]
        assert row[0] == new_start and row[1] == new_end, row

    def test_subscription_period_bounds_reads_the_item_fallback(self):
        """#4216 / Stripe `2025-03-31.basil`: the period fields moved onto the
        subscription ITEMS. Mutation caught: reading only the top level — a
        Basil-or-later payload then yields ``(None, None)`` and the meter anchor
        is never written, leaving the paying org unmeterable.

        Also pins the precedence: a pre-Basil top-level value WINS over an item
        value.
        """
        from tortoise.billing import subscription_period_bounds

        assert subscription_period_bounds({"items": {"data": [{
            "current_period_start": 100, "current_period_end": 200}]}}) \
            == (100, 200)
        assert subscription_period_bounds({
            "current_period_start": 1, "current_period_end": 2,
            "items": {"data": [{"current_period_start": 3,
                                 "current_period_end": 4}]}}) == (1, 2)
        assert subscription_period_bounds({}) == (None, None)
        assert subscription_period_bounds({"items": []}) == (None, None)
        # a truthy NON-list/non-dict `items` must not raise (malformed webhook
        # payload; the checkout call site is outside a try) — #4216 review.
        assert subscription_period_bounds({"items": "x"}) == (None, None)
        assert subscription_period_bounds({"items": 5}) == (None, None)
        assert subscription_period_bounds({
            "current_period_start": 7, "current_period_end": 8,
            "items": "x"}) == (7, 8)

    def test_boot_reconcile_repairs_customer_only_team(self, monkeypatch, billing_client):
        """Missed checkout.session.completed: only stripe_customer_id exists."""
        from tortoise import billing as bl
        org_id = billing_client["org_id"]
        sdk = billing_client["sdk"]
        sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.tier='free', t.subscription_status=NULL, "
            "t.stripe_customer_id='cus_only'",
            params={"id": org_id})
        monkeypatch.setattr(bl.StripeClient, "list_subscriptions",
                            lambda self, cid: [{"id": "sub_x", "status": "active",
                                                "items": {"data": [{"price": {"id": "price_200proMM"}}]}}])
        summary = bl.reconcile_org(sdk, org_id)
        assert summary["action"] == "mirror_customer_first_active"
        row = sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) RETURN t.tier, t.subscription_status",
            params={"id": org_id}).result_set
        assert row[0][0] == "pro" and row[0][1] == "active"

    def test_boot_reconcile_non_fatal_on_stripe_error(self, monkeypatch, billing_client):
        """A Stripe outage during reconcile must not break anything."""
        from tortoise import billing as bl
        org_id = billing_client["org_id"]
        billing_client["sdk"]._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.subscription_id='sub_1'",
            params={"id": org_id})
        monkeypatch.setattr(bl.StripeClient, "get_subscription",
                            lambda self, sid: (_ for _ in ()).throw(bl.StripeAPIError("outage")))
        # contract: reconcile RAISES on outage; the BOOT thread (lifespan)
        # catches + logs — non-fatality lives at the boot boundary.
        import pytest
        with pytest.raises(bl.StripeAPIError):
            bl.reconcile_org(billing_client["sdk"], org_id)

class TestLifespanStartup:
    def test_lifespan_startup_returns_without_blocking(self, monkeypatch, tmp_path):
        """The lifespan's startup half is cheap and synchronous, so it must
        RETURN well inside the join window — a startup that blocks would hold
        uvicorn's bind.

        NOTE(#4262): this replaces `test_boot_reconcile_hanging_stripe_never_
        blocks_boot`, which asserted a boot billing-reconcile daemon thread that
        no longer exists (nothing creates a `billing-reconcile` thread and
        `reconcile_org` has no production caller). The old test called
        `_lifespan(None)`, which crashed immediately in `_start_liveness(None)`,
        so its assertion could never fail; it then nested a second lifespan
        inside the shared TestClient's. This runs the lifespan against its own
        stub app instead, and guards the whole thread body so an in-thread crash
        cannot read as a successful return.
        """
        import asyncio
        import threading
        import time
        from types import SimpleNamespace

        from tortoise.hosted_api import _lifespan

        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "lifespan.db"))
        monkeypatch.setenv("RATE_LIMIT_DISABLED", "1")

        app = SimpleNamespace(state=SimpleNamespace())
        thread_error: list[BaseException] = []

        def _run():
            # The WHOLE body is guarded: an exception before `asyncio.run`
            # (e.g. a broken import) would otherwise kill the thread and read
            # as a successful return.
            try:
                async def _quick():
                    async with _lifespan(app):
                        return

                asyncio.run(_quick())
            except BaseException as exc:
                thread_error.append(exc)

        # daemon=True: a regression that BLOCKS startup must fail the assertion
        # below, not pin interpreter shutdown.
        started = time.monotonic()
        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join(timeout=10)
        elapsed = time.monotonic() - started
        try:
            # `join` only bounds the WAIT — assert the startup half itself is
            # prompt, not merely "under the timeout".
            assert elapsed < 5, f"lifespan startup took {elapsed:.1f}s"
            assert not t.is_alive(), "lifespan startup did not return within 10s"
            assert not thread_error, f"lifespan raised in its thread: {thread_error!r}"
        finally:
            # `_lifespan` arms the process-lifetime /healthz listener and
            # `_stop_liveness` deliberately does not tear it down — release it so
            # this test leaves no bound socket (repo convention:
            # tests/test_monitoring.py::_clean_heartbeat_and_listeners,
            # tests/test_hosted_api.py::TestBootOrder).
            import tortoise.monitoring as monitoring

            server = getattr(app.state, "_healthz_server", None)
            if server is not None:
                monitoring.stop_health_listener(server)


class TestTeamInfoBillingSurface:
    """#1623 — GET /v1/team exposes the billing surface the dashboard needs:
    subscription_status/customer_email (read off the Team node through the
    auth dict sources) + catalog-resolved checkout_price_id/checkout_price_ids.
    """

    def test_team_info_exposes_billing_surface(self, billing_client):
        """subscription_status/customer_email are read from the Team NODE —
        SET them (the webhook's store) then assert the round-trip; the
        catalog-driven fields resolve from STRIPE_PRICE_IDS."""
        billing_client["sdk"]._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.subscription_status=$s, "
            "t.customer_email=$e",
            params={"id": billing_client["org_id"], "s": "active",
                    "e": "billing-owner@example.com"})
        r = billing_client["client"].get("/v1/team",
                                         headers=billing_client["headers"])
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["subscription_status"] == "active"
        assert body["customer_email"] == "billing-owner@example.com"
        assert body["checkout_price_id"] == VALID_CATALOG["pro"]["monthly"]["id"]
        assert body["checkout_price_ids"] == {
            "solo": VALID_CATALOG["solo"]["monthly"]["id"],
            "pro": VALID_CATALOG["pro"]["monthly"]["id"],
            "team": VALID_CATALOG["team"]["monthly"]["id"],
        }

    def test_team_info_billing_surface_degrades_without_catalog(self, billing_client, monkeypatch):
        """No STRIPE_PRICE_IDS (registry/selfhost) → best-effort None/{} —
        /v1/team must NOT 500/503 (PriceCatalog() constructor raises
        BillingConfigError when unconfigured; the helper catches it)."""
        monkeypatch.delenv("STRIPE_PRICE_IDS", raising=False)
        r = billing_client["client"].get("/v1/team",
                                         headers=billing_client["headers"])
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["checkout_price_id"] is None
        assert body["checkout_price_ids"] == {}

    def test_team_info_catalog_failure_logged_once(self, billing_client,
                                                    monkeypatch, caplog):
        """#4335: an unconfigured/broken catalog must be OBSERVABLE — exactly
        ONE WARNING naming the exception class, cached so a second /v1/team
        does not re-log. The message never carries the secret value."""
        import logging

        import tortoise.hosted_api as hosted_api

        monkeypatch.delenv("STRIPE_PRICE_IDS", raising=False)
        # The latch is process-global; start this test from a clean state.
        monkeypatch.setattr(hosted_api, "_checkout_catalog_failure_logged", False,
                            raising=False)
        with caplog.at_level(logging.WARNING, logger="tortoise.hosted_api"):
            r1 = billing_client["client"].get("/v1/team",
                                              headers=billing_client["headers"])
            r2 = billing_client["client"].get("/v1/team",
                                              headers=billing_client["headers"])
        assert r1.status_code == 200, r1.text
        assert r2.status_code == 200, r2.text
        warnings = [rec for rec in caplog.records
                    if "checkout price catalog unavailable" in rec.getMessage()]
        assert len(warnings) == 1, (
            f"expected exactly one cached WARNING, got {len(warnings)}")
        assert "BillingConfigError" in warnings[0].getMessage()

    def test_team_info_catalog_failure_log_scrubs_secret_value(
            self, billing_client, monkeypatch, caplog):
        """#4335: a malformed catalog can carry a secret-shaped value (a Stripe
        key — or credentials-in-URI — pasted into a price-id slot).
        PriceCatalog quotes the offending id in its error, so the WARNING must
        scrub it through the composite redactor — the secret must never reach
        the log."""
        import logging

        import tortoise.hosted_api as hosted_api

        for secret in ("sk_live_SUPERSECRET1234567890", "docker://user:pass@host"):
            bad = json.loads(json.dumps(VALID_CATALOG))
            bad["solo"]["monthly"]["id"] = secret
            monkeypatch.setenv("STRIPE_PRICE_IDS", json.dumps(bad))
            monkeypatch.setattr(hosted_api, "_checkout_catalog_failure_logged", False,
                                raising=False)
            caplog.clear()
            with caplog.at_level(logging.WARNING, logger="tortoise.hosted_api"):
                r = billing_client["client"].get("/v1/team",
                                                 headers=billing_client["headers"])
            assert r.status_code == 200, r.text
            warnings = [rec.getMessage() for rec in caplog.records
                        if "checkout price catalog unavailable" in rec.getMessage()]
            assert len(warnings) == 1, warnings
            msg = warnings[0]
            assert secret not in msg, msg
            assert "***" in msg, f"expected redaction for {secret!r}: {msg}"
            assert "BillingError" in msg, msg

    def test_checkout_catalog_latch_clears_after_success(self, billing_client,
                                                         monkeypatch):
        """#4335: a success resets the latch, so a NEW outage after a recovery
        is reported again instead of being silently absorbed forever."""
        import tortoise.hosted_api as hosted_api

        monkeypatch.setattr(hosted_api, "_checkout_catalog_failure_logged", True,
                            raising=False)
        assert hosted_api._checkout_price_ids()  # valid catalog from the fixture env
        assert hosted_api._checkout_catalog_failure_logged is False

    def test_team_info_empty_paid_catalog_logged_once(self, billing_client,
                                                      monkeypatch, caplog):
        """#4335: a catalog that PARSES but resolves no paid tier is a total
        checkout outage — it must be reported once, and the latch must survive
        a second request (the missing default price must not re-arm it)."""
        import logging

        import tortoise.hosted_api as hosted_api

        monkeypatch.setenv("STRIPE_PRICE_IDS", json.dumps(
            {"free": VALID_CATALOG["free"]}))
        monkeypatch.setattr(hosted_api, "_checkout_catalog_failure_logged", False,
                            raising=False)
        with caplog.at_level(logging.WARNING, logger="tortoise.hosted_api"):
            r1 = billing_client["client"].get("/v1/team",
                                              headers=billing_client["headers"])
            r2 = billing_client["client"].get("/v1/team",
                                              headers=billing_client["headers"])
        assert r1.status_code == 200, r1.text
        assert r2.status_code == 200, r2.text
        assert r1.json()["checkout_price_ids"] == {}
        warnings = [rec.getMessage() for rec in caplog.records
                    if "no paid checkout tier" in rec.getMessage()]
        assert len(warnings) == 1, (
            f"expected exactly one zero-paid-tier WARNING, got {warnings}")

    def test_team_info_partial_paid_catalog_stays_silent(self, billing_client,
                                                         monkeypatch, caplog):
        """#2789: a deployment may sell a subset of tiers (solo/team only, no
        pro). That is a supported configuration, not a catalog failure — it
        must NOT warn."""
        import logging

        import tortoise.hosted_api as hosted_api

        partial = {k: v for k, v in VALID_CATALOG.items() if k != "pro"}
        monkeypatch.setenv("STRIPE_PRICE_IDS", json.dumps(partial))
        monkeypatch.setattr(hosted_api, "_checkout_catalog_failure_logged", False,
                            raising=False)
        with caplog.at_level(logging.WARNING, logger="tortoise.hosted_api"):
            r = billing_client["client"].get("/v1/team",
                                             headers=billing_client["headers"])
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["checkout_price_ids"] == {
            "solo": VALID_CATALOG["solo"]["monthly"]["id"],
            "team": VALID_CATALOG["team"]["monthly"]["id"],
        }
        assert body["checkout_price_id"] is None  # pro absent — default CTA disabled
        assert not [rec for rec in caplog.records
                    if "checkout price catalog unavailable" in rec.getMessage()], \
            "a legitimately partial catalog must not warn"

    def test_team_info_subscription_status_none_when_unset(self, billing_client):
        """Node field unset → None (the Billing page renders 'Free plan')."""
        r = billing_client["client"].get("/v1/team",
                                         headers=billing_client["headers"])
        assert r.status_code == 200, r.text
        assert r.json()["subscription_status"] is None

    def test_team_info_exposes_nodes_used_and_max_nodes(self, billing_client, tmp_path):
        """#4331: /v1/team carries the node figure the cap ACTUALLY gates
        (`count_org_usage(org, 'points')`: non-episodic Points + Object +
        Subject, #1911) and the org's enforced node cap (`max_points`).
        `point_count` is NOT that figure — it is :Point-only."""
        org_id = billing_client["org_id"]
        from tortoise.sdk import TortoiseSDK
        tenant = TortoiseSDK(os.path.join(tmp_path, "billing_api.db"),
                             namespace=org_id)
        try:
            tenant.create_object("acme", objectKind="org")
            r = billing_client["client"].get(
                "/v1/team", headers=billing_client["headers"])
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["nodes_used"] == 1          # the Object counts
            assert body["point_count"] == 0         # :Point-only stays 0
            assert body["max_nodes"] == 10000       # free tier default
        finally:
            tenant.close()

    def test_team_info_max_nodes_honors_stored_override(self, billing_client):
        """#4331: `max_nodes` is the org's OWN cap (`max_points`), not the
        tier's nominal default — a stored override must be what the display
        compares against, because it is what the write gate enforces."""
        billing_client["sdk"]._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.max_points = 12345",
            params={"id": billing_client["org_id"]})
        r = billing_client["client"].get("/v1/team",
                                         headers=billing_client["headers"])
        assert r.status_code == 200, r.text
        assert r.json()["max_nodes"] == 12345

    def test_team_info_nodes_used_fails_soft(self, monkeypatch, billing_client):
        """#4331: a quota-read failure must NOT 500 /v1/team — the node stat
        degrades to None (unknown; a failed read is never a genuine 0) while
        the rest of the billing surface still renders."""
        import tortoise.quota as quota

        def _boom(*a, **kw):
            raise RuntimeError("graph unavailable")

        monkeypatch.setattr(quota, "count_org_usage", _boom)
        r = billing_client["client"].get("/v1/team",
                                         headers=billing_client["headers"])
        assert r.status_code == 200, r.text
        body = r.json()
        # None, not 0: a failed read must not look like a genuinely empty org
        # (the client renders no figure for None).
        assert body["nodes_used"] is None
        assert body["max_nodes"] == 10000
        assert body["tier"] == "free"
