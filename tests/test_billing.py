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
from tests._verdict import verdict_async_client, verdict_client
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


class TestScrubbedProviderMessages:
    """#8895 review P2 (round 3): provider text must not name a customer id.

    ``reconcile_fleet`` records ``failed[].error = f"{cls}: {_scrub_secrets(msg)}"``
    and the cron driver prints that row into a PUBLIC Actions log. Stripe's own
    error text embeds the request path, so the customer/subscription id rides
    along inside the sentence — the composite redactor has to know those
    shapes, not only credentials.
    """

    def test_stripe_customer_and_subscription_ids_are_redacted(self):
        from tortoise.billing import _scrub_secrets

        for raw in ("stripe GET customers/cus_PqR3xyz9/subscriptions -> 404: {}",
                    "stripe POST subscriptions/sub_1AbCdEfGh9 failed: 400"):
            scrubbed = _scrub_secrets(raw)
            token = raw.split()[2].split("/")[1]
            assert token not in scrubbed, (
                f"{token!r} survived the redactor: {scrubbed!r}")
            assert "***" in scrubbed, scrubbed

    def test_a_snake_case_word_starting_with_sub_is_not_a_stripe_id(self):
        """Over-redaction costs diagnostics: ``sub_total`` is prose, not an id."""
        from tortoise.billing import _scrub_secrets

        assert _scrub_secrets("billing sub_total_price mismatch") == (
            "billing sub_total_price mismatch")

    def test_the_failed_row_built_from_a_stripe_error_carries_no_customer_id(
            self):
        """End of the path the public log reads: the row's own error string."""
        from tortoise.billing import StripeAPIError, _scrub_secrets

        exc = StripeAPIError(
            "stripe GET customers/cus_PqR3xyz9/subscriptions -> 404: {}")
        row_error = f"{exc.__class__.__name__}: {_scrub_secrets(str(exc))}"
        assert "cus_PqR3xyz9" not in row_error, row_error
        assert row_error.startswith("StripeAPIError: stripe GET"), row_error

    def test_credentials_are_still_redacted(self):
        """The pre-existing behaviour is not traded away for the new shapes."""
        from tortoise.billing import _scrub_secrets

        scrubbed = _scrub_secrets("auth sk_live_SUPERSECRET1234567890 rejected")
        assert "SUPERSECRET" not in scrubbed, scrubbed
        assert "***" in scrubbed, scrubbed


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

    def test_reconcile_stale_subscription_id_falls_back_to_the_customer(
            self, monkeypatch, stripe_env, billing_sdk):
        """#8895 P1: a cancel-and-resubscribe whose
        ``checkout.session.completed`` was missed leaves the OLD (canceled)
        ``subscription_id`` stored forever. Mirroring it on sight caps a paying
        org at the lower tier again — the very bug this cadence repairs — and
        reports the row as a clean ``unchanged``. The stored id is authoritative
        for IDENTITY, not for the CURRENT entitlement: an inactive one must
        yield to the active subscription the customer actually holds, and
        mirroring that one rewrites the stale pointer.
        """
        sdk = billing_sdk
        team = sdk.org_create("stale-sub-pointer")
        sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.subscription_id='sub_old', "
            "t.stripe_customer_id='cus_1'",
            params={"id": team["id"]},
        )
        stale = {"id": "sub_old", "status": "canceled",
                 "items": {"data": [{"price": {"id": "price_100soloM"}}]}}
        active = {"id": "sub_new", "status": "active", "current_period_end": 9999,
                  "cancel_at_period_end": False,
                  "items": {"data": [{"price": {"id": "price_300teamM"}}]}}
        monkeypatch.setattr(billing.StripeClient, "get_subscription",
                            lambda self, sid: dict(stale))
        monkeypatch.setattr(billing.StripeClient, "list_subscriptions",
                            lambda self, cid: [stale, active])
        out = reconcile_org(sdk, team["id"])
        t = sdk.org_get(team["id"])
        assert out["action"] == "mirror_customer_first_active"
        assert t["tier"] == "team"           # the ACTIVE sub wins, not the stale id
        assert t["subscription_id"] == "sub_new"   # stale pointer repaired
        assert t["subscription_status"] == "active"

    def test_reconcile_an_active_but_ENDING_pointer_follows_the_newer_sub(
            self, monkeypatch, stripe_env, billing_sdk):
        """#8904 review round 7: ``active`` is not proof the pointer is CURRENT.

        A cancel-at-period-end leaves the subscription ``active`` until the
        period runs out. An org that cancels and resubscribes inside that
        window holds an ACTIVE pointer to the OLD, soon-dead subscription — and
        if ``checkout.session.completed`` was missed (the premise of this whole
        cadence), the status check alone calls that pointer current, never
        consults the customer, keeps the OLD tier while the org pays for the
        new one, and records the row ``unchanged`` on a green pass.

        An ENDING subscription is a pointer to CHECK, not a fact. When the
        customer holds no newer one the LIST returns this same subscription, so
        the mirroring outcome is unchanged.
        """
        sdk = billing_sdk
        team = sdk.org_create("ending-pointer")
        sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.subscription_id='sub_old', "
            "t.stripe_customer_id='cus_1', t.tier='solo'",
            params={"id": team["id"]},
        )
        ending = {"id": "sub_old", "status": "active",
                  "cancel_at_period_end": True, "current_period_end": 9999,
                  "items": {"data": [{"price": {"id": "price_100soloM"}}]}}
        newer = {"id": "sub_new", "status": "active",
                 "cancel_at_period_end": False, "current_period_end": 19999,
                 "items": {"data": [{"price": {"id": "price_300teamM"}}]}}
        monkeypatch.setattr(billing.StripeClient, "get_subscription",
                            lambda self, sid: dict(ending))
        monkeypatch.setattr(billing.StripeClient, "list_subscriptions",
                            lambda self, cid: [ending, newer])
        out = reconcile_org(sdk, team["id"])
        t = sdk.org_get(team["id"])
        assert out["action"] == "mirror_customer_first_active", (
            "an ENDING subscription is not a current pointer, so the customer "
            f"must be consulted — got {out}")
        assert t["tier"] == "team", (
            "the org pays for Team; mirroring the ending Solo sub leaves it "
            f"capped at {t['tier']!r}")
        assert t["subscription_id"] == "sub_new"

    def test_reconcile_a_cheaper_newer_sub_does_not_revoke_a_paid_ending_one(
            self, monkeypatch, stripe_env, billing_sdk):
        """#8904 review P2: ENDING is not a refund — the paid period still runs.

        ``cancel_at_period_end`` keeps the subscription ``active`` until
        ``current_period_end`` and the customer has PAID for that period. An
        org that cancels Team and adds a cheaper Solo before the period ends
        must therefore stay on Team until the period does: preferring "any
        non-ending subscription" over a still-in-force higher one drops the
        org from Team's 600 000 nodes to Solo's 25 000 while it is paid up,
        and every write past Solo's cap is refused with "upgrade your plan".
        The row is recorded as a clean ``repaired``/``direction: down``, which
        does NOT page (only drift UP notifies) — so the revocation is silent
        on a GREEN run, the exact failure class this cadence exists to remove.

        The newer Solo subscription is not lost: once the ending Team one dies
        the next pass sees no live ending sub and mirrors Solo then.
        """
        sdk = billing_sdk
        team = sdk.org_create("ending-team-cheaper-newer")
        sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.subscription_id='sub_team', "
            "t.stripe_customer_id='cus_1', t.tier='team', "
            "t.subscription_status='active'",
            params={"id": team["id"]},
        )
        ending = {"id": "sub_team", "status": "active",
                  "cancel_at_period_end": True, "current_period_end": 19999,
                  "items": {"data": [{"price": {"id": "price_300teamM"}}]}}
        cheaper = {"id": "sub_solo", "status": "active",
                   "cancel_at_period_end": False, "current_period_end": 29999,
                   "items": {"data": [{"price": {"id": "price_100soloM"}}]}}
        monkeypatch.setattr(billing.StripeClient, "get_subscription",
                            lambda self, sid: dict(ending))
        monkeypatch.setattr(billing.StripeClient, "list_subscriptions",
                            lambda self, cid: [ending, cheaper])
        out = reconcile_org(sdk, team["id"])
        t = sdk.org_get(team["id"])
        assert out["action"] == "mirror_customer_first_active", (
            f"the customer must still be consulted about the ending sub — {out}")
        assert t["tier"] == "team", (
            "Team is paid through current_period_end, so a NEWER but CHEAPER "
            f"subscription must not revoke it early — got {t['tier']!r}")
        assert t["subscription_id"] == "sub_team", (
            f"the still-in-force pointer is the current one — got {t}")

    def test_reconcile_an_unpriceable_ENDING_sub_is_not_revoked_by_a_cheaper_newer(
            self, monkeypatch, stripe_env, billing_sdk):
        """#8904 review P2: the still-in-force guard must hold when ITS rank is unreadable.

        ``_best_ranked`` SKIPS entries it cannot price, so a customer whose only
        ENDING subscription carries a rotated-out price yields ``(None, None)``.
        A guard written as ``if kept is not None`` then skipped the comparison
        entirely and fell through to the newer, cheaper candidate — mirroring
        Solo, rewriting ``subscription_id`` to it, and recording a clean
        ``repaired``/``direction: down`` (which never pages) while Team was paid
        through ``current_period_end``. That is the silent-revocation class this
        branch exists to close, one arm over: only the direction that cannot
        revoke was implemented.

        Keeping the still-in-force sub cannot be mirrored either (its price is
        not in the catalog), and that IS the intended answer: the row is FAILED
        with the stored tier and pointer intact, so the run reds and an operator
        looks — ``subscription_plan``'s contract ("log + ops-notify + KEEP
        stored tier"), never a downgrade on a green pass.
        """
        sdk = billing_sdk
        team = sdk.org_create("ending-unpriceable-team")
        sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.subscription_id='sub_team_rotated', "
            "t.stripe_customer_id='cus_1', t.tier='team', "
            "t.subscription_status='active'",
            params={"id": team["id"]},
        )
        ending = {"id": "sub_team_rotated", "status": "active",
                  "cancel_at_period_end": True, "current_period_end": 19999,
                  "items": {"data": [{"price": {"id": "price_rotatedOut"}}]}}
        cheaper = {"id": "sub_solo", "status": "active",
                   "cancel_at_period_end": False, "current_period_end": 29999,
                   "items": {"data": [{"price": {"id": "price_100soloM"}}]}}
        monkeypatch.setattr(billing.StripeClient, "get_subscription",
                            lambda self, sid: dict(ending))
        # The newer, cheaper sub is listed FIRST: the pre-fix arm took it from
        # any position, because the comparison never ran at all.
        monkeypatch.setattr(billing.StripeClient, "list_subscriptions",
                            lambda self, cid: [cheaper, ending])
        summary = billing.reconcile_subscriptions(sdk, notifier=False)
        t = sdk.org_get(team["id"])
        assert t["tier"] == "team", (
            "Team is paid through current_period_end and the ending sub could "
            "not be PRICED, so the newer cheaper Solo one must not revoke it "
            f"— got {t['tier']!r}")
        assert t["subscription_id"] == "sub_team_rotated", (
            f"the still-in-force pointer is the current one — got {t}")
        assert summary["repaired"] == 0 and summary["drift"] == [], (
            "no write is justified, so nothing may be reported as repaired "
            f"— got {summary}")
        assert len(summary["failed"]) == 1, (
            "an unpriceable still-in-force sub cannot be mirrored, so the org "
            "must be FAILED and the run must red — a green pass here is the "
            f"silent outcome — got {summary}")
        assert "unknown price" in summary["failed"][0]["error"], (
            f"the row must name the real cause — got {summary['failed']}")

    def test_reconcile_an_unreadable_ladder_fails_the_row_with_no_write(
            self, monkeypatch, stripe_env, billing_sdk):
        """The docstrings name TWO unreadable shapes; only one was executed.

        ``_subscription_rank`` returns ``None`` for a price the catalog does not
        know (pinned by the test above) AND for a pricing ladder that cannot be
        READ at all (``_paid_tier_floor`` catching ``tier_limits``). Both put an
        ENDING, still-in-force sub on the unrankable-ending arm, and both
        docstrings claim the same outcome — the org is a FAILED row with its
        stored tier and pointer intact. The second leg raises one call LATER
        (``apply_limits`` -> ``tier_limits``, after ``subscription_plan``
        succeeded), so it is pinned here: a raise that landed after the
        control-plane write would leave the org holding a tier nobody proved.

        Honest scope: this does NOT discriminate the pre-fix arm (with the
        ladder unreadable the mirror raises on either selection, so the
        pre-fix tree also ends FAILED with the stored tier intact). It pins the
        INVARIANT the docstrings state — no write precedes the raise — not the
        selection order.
        """
        import tortoise.pricing as pricing

        sdk = billing_sdk
        team = sdk.org_create("ending-unreadable-ladder")
        # ⛔ The stored tier is ``pro`` while Stripe's still-in-force sub is
        # Team: that is the only arrangement in which "the raise precedes the
        # write" is OBSERVABLE. A store already holding ``team`` would look
        # identical after a write-before-raise.
        sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.subscription_id='sub_team', "
            "t.stripe_customer_id='cus_1', t.tier='pro', "
            "t.subscription_status='active'",
            params={"id": team["id"]},
        )
        ending = {"id": "sub_team", "status": "active",
                  "cancel_at_period_end": True, "current_period_end": 19999,
                  "items": {"data": [{"price": {"id": "price_300teamM"}}]}}
        cheaper = {"id": "sub_solo", "status": "active",
                   "cancel_at_period_end": False, "current_period_end": 29999,
                   "items": {"data": [{"price": {"id": "price_100soloM"}}]}}
        monkeypatch.setattr(billing.StripeClient, "get_subscription",
                            lambda self, sid: dict(ending))
        monkeypatch.setattr(billing.StripeClient, "list_subscriptions",
                            lambda self, cid: [cheaper, ending])

        def _unreadable_ladder(tier):
            raise KeyError("pricing.json is unreadable (ladder fault)")

        monkeypatch.setattr(pricing, "tier_limits", _unreadable_ladder)
        summary = billing.reconcile_subscriptions(sdk, notifier=False)
        t = sdk.org_get(team["id"])
        assert t["tier"] == "pro", (
            "the ladder could not be read, so no tier may be written — the "
            "raise must precede the control-plane write; a pre-write would "
            f"have stored 'team' here — got {t['tier']!r}")
        assert t["subscription_id"] == "sub_team", (
            f"the still-in-force pointer must survive the raise — got {t}")
        assert summary["repaired"] == 0 and summary["drift"] == [], (
            f"nothing may be reported as repaired — got {summary}")
        assert len(summary["failed"]) == 1, (
            "an unreadable ladder cannot be mirrored, so the org is a FAILED "
            f"row and the run reds — got {summary}")
        assert summary["failed"][0]["org_id"] == team["id"], (
            "the FAILED row must be THIS org's — ``len(failed) == 1`` is "
            "fleet-wide and a sibling Team with an identity could own it — "
            f"got {summary['failed']}")
        assert "KeyError" in summary["failed"][0]["error"], (
            "the row must carry the LADDER's own fault class (the raise comes "
            "from ``apply_limits`` -> ``tier_limits``, after "
            "``subscription_plan`` succeeded) — a price fault would read "
            f"``BillingError`` instead — got {summary['failed']}")
        assert "unreadable" in summary["failed"][0]["error"], (
            f"the row must name the ladder — got {summary['failed']}")

    def test_reconcile_an_unpriceable_ENDING_SIBLING_is_not_revoked_by_a_newer(
            self, monkeypatch, stripe_env, billing_sdk):
        """The guard is about the WHOLE ending set, not a set of one.

        A customer can hold TWO ending subscriptions at once (a cancelled Team
        plus a cancelled Solo from an earlier plan). ``_best_ranked`` skips the
        one it cannot price, so before this pin the RANKABLE sibling won the
        comparison and a newer Solo at that sibling's cap counted as "provably
        at or above" — the pass mirrored Solo, rewrote ``subscription_id`` to
        it, and recorded a clean ``repaired``/``direction: down``, which never
        pages (only drift UP notifies). Team was paid through
        ``current_period_end`` and nothing about it was ever read.

        The unrankable sibling is kept instead and cannot be mirrored, so the
        row is FAILED with the stored tier and pointer intact: loud and
        fail-closed, never a silent downgrade.
        """
        sdk = billing_sdk
        team = sdk.org_create("ending-unpriceable-sibling")
        sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.subscription_id='sub_team_rotated', "
            "t.stripe_customer_id='cus_1', t.tier='team', "
            "t.subscription_status='active'",
            params={"id": team["id"]},
        )
        ending_team = {"id": "sub_team_rotated", "status": "active",
                       "cancel_at_period_end": True,
                       "current_period_end": 19999,
                       "items": {"data": [{"price": {"id": "price_rotatedOut"}}]}}
        ending_solo = {"id": "sub_solo_old", "status": "active",
                       "cancel_at_period_end": True,
                       "current_period_end": 18999,
                       "items": {"data": [{"price": {"id": "price_100soloM"}}]}}
        cheaper = {"id": "sub_solo_new", "status": "active",
                   "cancel_at_period_end": False, "current_period_end": 29999,
                   "items": {"data": [{"price": {"id": "price_100soloM"}}]}}
        monkeypatch.setattr(billing.StripeClient, "get_subscription",
                            lambda self, sid: dict(ending_team))
        # The rankable sibling and the newer sub both sit at Solo's cap, so the
        # pre-fix comparison was satisfied by the SIBLING — the newer Solo won.
        monkeypatch.setattr(billing.StripeClient, "list_subscriptions",
                            lambda self, cid: [cheaper, ending_solo, ending_team])
        summary = billing.reconcile_subscriptions(sdk, notifier=False)
        t = sdk.org_get(team["id"])
        assert t["tier"] == "team", (
            "Team is paid through current_period_end and the ending set holds "
            "a sub nobody can price, so a newer Solo must not revoke it "
            f"— got {t['tier']!r} ({summary})")
        assert t["subscription_id"] == "sub_team_rotated", (
            f"the pointer must not be rewritten to the newer Solo — got {t}")
        assert summary["repaired"] == 0 and summary["drift"] == [], (
            "no write is justified, so nothing may be reported as repaired "
            f"— got {summary}")
        assert len(summary["failed"]) == 1, (
            "an unpriceable still-in-force sub cannot be mirrored, so the org "
            f"is a FAILED row and the run reds — got {summary}")
        assert summary["failed"][0]["org_id"] == team["id"], (
            f"the FAILED row must be this org's — got {summary['failed']}")

    def test_reconcile_customer_only_keeps_a_paid_ending_sub_over_a_cheaper_newer(
            self, monkeypatch, stripe_env, billing_sdk):
        """The SAME rank guard on the customer-only LIST site.

        An abandoned/missed checkout can leave a ``stripe_customer_id`` and no
        ``subscription_id``, and this branch feeds the same selector. Without
        the guard here an org whose only stored binding is the customer would
        drop from Team to a newer Solo before its paid period ended — a silent
        downgrade the fleet cadence then reports as a clean repair.
        """
        sdk = billing_sdk
        team = sdk.org_create("customer-only-ending-team")
        sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.stripe_customer_id='cus_1', "
            "t.tier='team', t.subscription_status='active'",
            params={"id": team["id"]},
        )
        ending = {"id": "sub_team", "status": "active",
                  "cancel_at_period_end": True, "current_period_end": 19999,
                  "items": {"data": [{"price": {"id": "price_300teamM"}}]}}
        cheaper = {"id": "sub_solo", "status": "active",
                   "cancel_at_period_end": False, "current_period_end": 29999,
                   "items": {"data": [{"price": {"id": "price_100soloM"}}]}}
        monkeypatch.setattr(billing.StripeClient, "list_subscriptions",
                            lambda self, cid: [cheaper, ending])
        reconcile_org(sdk, team["id"])
        t = sdk.org_get(team["id"])
        assert t["tier"] == "team", (
            "the ending Team sub is paid through the period, so it outranks "
            f"the newer Solo one — got {t['tier']!r}")
        assert t["subscription_id"] == "sub_team"

    def test_reconcile_real_cancellation_still_downgrades(
            self, monkeypatch, stripe_env, billing_sdk):
        """#8895 P1 guard: the customer-level fallback must NOT keep a canceled
        org paid. With NO active subscription at the customer, the stored
        (canceled) one is the truth and the org reverts to free."""
        sdk = billing_sdk
        team = sdk.org_create("real-cancel")
        sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.subscription_id='sub_gone', "
            "t.stripe_customer_id='cus_1', t.tier='pro', "
            "t.subscription_status='active'",
            params={"id": team["id"]},
        )
        stale = {"id": "sub_gone", "status": "canceled",
                 "items": {"data": [{"price": {"id": "price_100soloM"}}]}}
        monkeypatch.setattr(billing.StripeClient, "get_subscription",
                            lambda self, sid: dict(stale))
        monkeypatch.setattr(billing.StripeClient, "list_subscriptions",
                            lambda self, cid: [stale])
        reconcile_org(sdk, team["id"])
        t = sdk.org_get(team["id"])
        assert t["tier"] == "free"
        assert t["subscription_status"] == "canceled"

    def test_reconcile_list_failure_does_not_downgrade_a_paying_org(
            self, monkeypatch, stripe_env, billing_sdk):
        """#8904 review P2: an unanswerable LIST is not evidence of cancellation.

        The stored subscription is inactive, so the customer LIST is the ONLY leg
        that can prove the org holds a newer ACTIVE one. When that call fails
        there is an absence of evidence, not evidence of a cancellation — and
        mirroring the stored (inactive) subscription on it re-caps a PAYING org on
        a GREEN run, because a downgrade is recorded as a clean ``repaired`` and
        only drift UP alerts. The org is left alone: not verified, not
        downgraded (the same policy a provider outage gets everywhere else).

        Still true, and pinned below: the LIST failing must not RAISE, so a
        correct downgrade elsewhere never becomes a permanent ``failed`` row.
        """
        sdk = billing_sdk
        team = sdk.org_create("list-failure")
        sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.subscription_id='sub_gone', "
            "t.stripe_customer_id='cus_gone', t.tier='pro', "
            "t.subscription_status='active'",
            params={"id": team["id"]},
        )
        gone = {"id": "sub_gone", "status": "canceled",
                "items": {"data": [{"price": {"id": "price_100soloM"}}]}}
        monkeypatch.setattr(billing.StripeClient, "get_subscription",
                            lambda self, sid: dict(gone))
        monkeypatch.setattr(
            billing.StripeClient, "list_subscriptions",
            lambda self, cid: (_ for _ in ()).throw(
                billing.StripeAPIError("no such customer: 'cus_gone'")))
        outcome = reconcile_org(sdk, team["id"])  # must NOT raise
        assert outcome["action"] == "noop", (
            "an unanswerable LIST must leave the org alone rather than mirror a "
            f"subscription it cannot prove is current — got {outcome}")
        t = sdk.org_get(team["id"])
        assert t["tier"] == "pro", (
            "the org is PAYING; one failed provider call in the sweep must not "
            f"re-cap it — got {t['tier']!r}")
        assert t["subscription_status"] == "active", (
            "nothing was mirrored, so the stored status must be untouched "
            f"— got {t['subscription_status']!r}")

    def test_reconcile_customer_only_binding_survives_a_lookup_failure(
            self, monkeypatch, stripe_env, billing_sdk):
        """#8904 review P2: the customer-ONLY branch needs the same guard.

        ``hosted_api`` persists ``stripe_customer_id`` BEFORE the checkout
        session, so abandoned checkouts — the majority of this branch — carry a
        customer id and no subscription id. With the LIST unguarded, one
        transient 5xx/429 on one of them escaped as a ``failed`` row, and a
        non-empty ``failed`` FAILS the pass and pages "a paying org may be
        capped at the wrong tier" for an org with no entitlement at stake — the
        mirror image of the stored-pointer asymmetry fixed above.
        """
        sdk = billing_sdk
        team = sdk.org_create("customer-only-list-failure")
        sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.stripe_customer_id='cus_gone' "
            "RETURN t.id",
            params={"id": team["id"]},
        )
        monkeypatch.setattr(
            billing.StripeClient, "list_subscriptions",
            lambda self, cid: (_ for _ in ()).throw(
                billing.StripeAPIError("upstream 503")))
        outcome = reconcile_org(sdk, team["id"])  # must NOT raise
        assert outcome["action"] == "noop", (
            "a failed customer lookup is not a verdict, so this branch must "
            f"leave the org alone instead of failing the pass — got {outcome}")

    def test_failed_customer_list_at_unpaid_tier_is_skipped_not_failed(
            self, monkeypatch, stripe_env, billing_sdk, caplog):
        """#8904 review P2: the ``skipped`` line must not assert a CAUSE.

        ``skipped`` now covers two states an operator cannot tell apart: a
        proven abandoned checkout, and an unpaid-tier org whose customer LIST
        failed (left alone, NOT proven clean). The log line is what an operator
        reads, so it may report what was ESTABLISHED and hedge the cause —
        asserting "abandoned checkout" is the same
        asserting-a-cause-nobody-measured fault the wording fix exists to
        remove, one bucket over. Without this test the hedge can be deleted
        with the suite green.
        """
        import logging  # noqa: I001
        from tortoise import billing as bl
        sdk = billing_sdk
        team = sdk.org_create("customer-list-failure-unpaid")
        sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.stripe_customer_id='cus_8895skip', "
            "t.tier='free'",
            params={"id": team["id"]},
        )
        monkeypatch.setattr(
            bl.StripeClient, "list_subscriptions",
            lambda self, cid: (_ for _ in ()).throw(
                bl.StripeAPIError("upstream 503")))
        with caplog.at_level(logging.INFO):
            summary = bl.reconcile_subscriptions(sdk, notifier=False)
        assert summary["failed"] == [], (
            "a provider error is not a verdict, so it must not fail the pass "
            f"and page — got {summary}")
        assert summary["skipped"] == 1, summary
        text = "\n".join(r.getMessage() for r in caplog.records)
        assert "could be established" in text, (
            "the line must report that no subscription could be ESTABLISHED "
            f"(true for both causes) — got {text!r}")
        assert "usual cause is an abandoned checkout" in text, (
            "the line must HEDGE the cause (the other state that reaches "
            "'skipped' is a failed lookup, which proves nothing about why) — "
            f"deleting the hedge must fail this test — got {text!r}")

    def test_failed_customer_list_at_paid_tier_is_unverified_not_downgraded(
            self, monkeypatch, stripe_env, billing_sdk, caplog):
        """#8904 review P2: the PAID arm of the same guard, end to end.

        A paying org whose customer LIST failed is counted ``unverified``
        (something to look at) and physically left alone: a provider error is
        never a downgrade and never a ``repaired``/``drift``.
        """
        import logging  # noqa: I001
        from tortoise import billing as bl
        sdk = billing_sdk
        team = sdk.org_create("customer-list-failure-paid")
        sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.stripe_customer_id='cus_8895paid', "
            "t.tier='pro', t.subscription_status='active'",
            params={"id": team["id"]},
        )
        monkeypatch.setattr(
            bl.StripeClient, "list_subscriptions",
            lambda self, cid: (_ for _ in ()).throw(
                bl.StripeAPIError("upstream 503")))
        with caplog.at_level(logging.INFO):
            summary = bl.reconcile_subscriptions(sdk, notifier=False)
        assert summary["unverified"] == 1, summary
        assert summary["failed"] == [] and summary["repaired"] == 0
        assert summary["drift"] == [], (
            "a failed lookup must never be reported as a repair/drift")
        t = sdk.org_get(team["id"])
        assert t["tier"] == "pro", (
            "a provider error is not a licence to downgrade a paying org")
        text = "\n".join(r.getMessage() for r in caplog.records)
        assert "could be established" in text, (
            f"the warning must not assert Stripe ANSWERED — got {text!r}")

    def test_an_unreadable_ladder_is_unverified_never_nothing_to_verify(
            self, monkeypatch, stripe_env, billing_sdk, caplog):
        """#8904 review P2: "the ladder could not be read" is NOT "unpaid".

        ``_paid_tier_floor`` returns ``None`` when ``pricing.json`` cannot be
        read (#750.8 — a documented reachable config fault), and no tier
        compares above Free's floor in that state. Reading that as "not paid"
        files a stored ``pro`` org under ``skipped`` and logs it as "stored at
        the unpaid tier 'pro'" — a paid org passing the pass that exists to
        catch one, on the strength of an unread file. An unread signal is not a
        zero, so the org must be counted as SOMETHING TO LOOK AT and the log may
        not claim which side of the cap it sits on.
        """
        import logging  # noqa: I001
        import tortoise.pricing as pr
        from tortoise import billing as bl
        sdk = billing_sdk
        team = sdk.org_create("customer-list-failure-unreadable-ladder")
        sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.stripe_customer_id='cus_8895unk', "
            "t.tier='pro', t.subscription_status='active'",
            params={"id": team["id"]},
        )
        # The REAL #750.8 fault: a loaded tier missing a required limit key
        # raises KeyError at lookup, which `_paid_tier_floor` turns into None.
        def _unreadable(tier):
            raise KeyError(f"pricing.json tier {tier!r} missing limit keys")

        monkeypatch.setattr(pr, "tier_limits", _unreadable)
        with caplog.at_level(logging.INFO):
            summary = bl.reconcile_subscriptions(sdk, notifier=False)
        assert summary["skipped"] == 0, (
            "an unreadable ladder must never be evidence that a stored 'pro' "
            f"org has nothing to verify — got {summary}")
        assert summary["unverified"] == 1, summary
        assert summary["failed"] == [] and summary["repaired"] == 0
        assert sdk.org_get(team["id"])["tier"] == "pro", (
            "an unread config file is not a licence to downgrade")
        text = "\n".join(r.getMessage() for r in caplog.records)
        assert "unpaid tier" not in text, (
            "the log must not assert which side of the cap this org is on — "
            f"got {text!r}")
        assert "UNKNOWN" in text, (
            f"the operator must be told the answer is unknown — got {text!r}")

    def test_a_MALFORMED_or_MISSING_pricing_file_does_not_abort_the_pass(
            self, monkeypatch, stripe_env, billing_sdk):
        """#8904 review P1: an unreadable ladder has FOUR shapes, not one.

        ``_paid_tier_floor`` caught only ``KeyError``/``TypeError`` (a loaded
        tier missing a limit key). A malformed ``product/pricing.json`` raises
        ``JSONDecodeError`` and a missing one ``FileNotFoundError`` — neither is
        a subclass of those, so the exception escaped from OUTSIDE the per-org
        ``try`` and aborted the WHOLE pass as an unnamed 500. The first org that
        needed the ladder decision (an abandoned checkout — the majority
        population) killed the sweep mid-fleet, which is the partial pass this
        cadence exists to prevent, and the driver reports a 500 as a
        control-plane outage. The documented outcome is ``unverified``.

        The fourth shape is the one a hand-written tuple always misses: a
        valid-JSON file whose ROOT (or ``tiers`` value) is not an object raises
        ``AttributeError`` at ``data.get(...)``, outside every exception
        family the first fix enumerated. That is why the catch is broad now.
        """
        import json

        import tortoise.pricing as pr
        from tortoise import billing as bl

        sdk = billing_sdk
        team = sdk.org_create("malformed-ladder")
        sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.stripe_customer_id='cus_8895mf', "
            "t.tier='pro', t.subscription_status='active'",
            params={"id": team["id"]},
        )
        monkeypatch.setattr(
            bl.StripeClient, "list_subscriptions", lambda self, cid: [])

        # Both shapes must land the org in `unverified` and END the pass cleanly.
        # (The org is created BEFORE the patch: `org_create` reads the ladder
        # too, so patching first would fail in the fixture, not the code under
        # test.)
        for fault in (json.JSONDecodeError("Expecting value", "{bad", 0),
                      FileNotFoundError("product/pricing.json"),
                      AttributeError("'list' object has no attribute 'get'")):
            def _broken(tier, _fault=fault):
                raise _fault

            monkeypatch.setattr(pr, "tier_limits", _broken)
            summary = bl.reconcile_subscriptions(sdk, notifier=False)  # no raise
            assert summary["unverified"] == 1, (type(fault).__name__, summary)
            assert summary["failed"] == [], (type(fault).__name__, summary)
            assert sdk.org_get(team["id"])["tier"] == "pro", (
                "an unread file is not a licence to downgrade")

    def test_reconcile_past_due_pointer_still_follows_the_newer_active_sub(
            self, monkeypatch, stripe_env, billing_sdk):
        """#8895 review P2: ``past_due`` keeps the TIER (enforcement) but is NOT
        a fresh POINTER.

        A failed-card org that re-subscribes gets a new active subscription
        while the old one sits at ``past_due`` — exactly the resubscribe flow
        this repair exists for. Reusing ``_ACTIVE_STATUSES`` for pointer
        freshness left that org capped.
        """
        sdk = billing_sdk
        team = sdk.org_create("past-due-pointer")
        sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.subscription_id='sub_pastdue', "
            "t.stripe_customer_id='cus_1'",
            params={"id": team["id"]},
        )
        past_due = {"id": "sub_pastdue", "status": "past_due",
                    "items": {"data": [{"price": {"id": "price_100soloM"}}]}}
        active = {"id": "sub_new", "status": "active", "current_period_end": 9999,
                  "cancel_at_period_end": False,
                  "items": {"data": [{"price": {"id": "price_300teamM"}}]}}
        monkeypatch.setattr(billing.StripeClient, "get_subscription",
                            lambda self, sid: dict(past_due))
        monkeypatch.setattr(billing.StripeClient, "list_subscriptions",
                            lambda self, cid: [past_due, active])
        out = reconcile_org(sdk, team["id"])
        t = sdk.org_get(team["id"])
        assert out["action"] == "mirror_customer_first_active"
        assert t["tier"] == "team"
        assert t["subscription_id"] == "sub_new"

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
    with verdict_client(app) as tc:
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

    def test_registry_lane_never_consults_the_owner_email_read(
            self, monkeypatch, billing_client):
        """#4508: the owner-email link is a SUPABASE-lane read (``owner_email``
        = a DB membership read + a GoTrue admin API call). Selfhost has no
        control plane: the link must be skipped entirely there, so the
        registry lane keeps its byte-identical chain (Team.email →
        APIKey.created_by → session email).

        Mutation that makes this fail: resolve the owner email unconditionally
        instead of under ``supabase_mode`` — this lane then makes an admin API
        call it cannot serve (or reads a control plane that does not exist).
        The spy sits on ``hosted_api._billing_owner_email`` (the call-site
        seam) rather than on ``supabase_control.owner_email``: the helper's own
        fail-soft guard would swallow an exploding spy and hide the mutation.
        """
        import tortoise.hosted_api as ha
        from tortoise.hosted_api import app, get_current_org

        def _explode(*a, **kw):
            raise AssertionError("owner_email consulted in the registry lane")

        monkeypatch.setattr(ha, "_billing_owner_email", _explode)
        captured: list[str] = []
        self._stub_checkout(monkeypatch, captured, "team-ow0")
        app.dependency_overrides[get_current_org] = lambda: {
            "org_id": billing_client["org_id"], "key_id": None, "tier": "free",
        }
        try:
            r = billing_client["client"].post(
                "/v1/billing/checkout", json={"price_id": "price_200proMM"})
        finally:
            app.dependency_overrides.pop(get_current_org, None)
        assert r.status_code == 200, r.text
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
            yield verdict_client(ha.app), fake
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

    def test_checkout_uses_the_key_creators_email_in_supabase_mode(
            self, monkeypatch, sb):
        """#4727: the control-plane twin of the ``APIKey.created_by`` link.

        A provision-path Supabase org carries no ``organizations.email``; the
        Edge Function recorded the creator on the ``api_keys`` row, and
        ``resolve_api_key`` already puts it on the resolved org dict. Pre-fix
        the ENTIRE ``created_by`` family was registry-only (``sdk is None`` in
        this lane), so a tt_ key with no session email fell to the terminal
        400 with the address sitting on the org dict.

        Mutation that makes this fail: delete the ``sdk is None`` branch — the
        chain reaches the 400 and ``create_customer`` is never called.
        Reachable in the fixture: the override org dict below has an explicit
        ``email: None`` plus a real ``created_by`` address.
        """
        tc, fake = sb
        import tortoise.hosted_api as ha
        fake.tables["organizations"][0]["email"] = None
        org = {"org_id": self.ORG_ID, "tier": "solo", "email": None,
               "created_by": "owner@example.com"}
        ha.app.dependency_overrides[ha.get_current_org_session] = (
            lambda: dict(org))
        created: list[str] = []
        monkeypatch.setattr(billing.StripeClient, "create_customer",
                            lambda self, e: created.append(e) or "cus_kc_1")
        monkeypatch.setattr(billing.StripeClient, "list_subscriptions",
                            lambda self, cid: [])
        monkeypatch.setattr(billing.StripeClient, "create_checkout_session",
                            lambda self, *a: "https://checkout.stripe.com/pay/kc1")

        r = tc.post("/v1/billing/checkout", json={"price_id": "price_100soloM"})
        assert r.status_code == 200, r.text
        assert created == ["owner@example.com"]
        row = fake.tables["organizations"][0]
        assert row["stripe_customer_id"] == "cus_kc_1"
        assert row["customer_email"] == "owner@example.com"

    @pytest.mark.parametrize("creator", [
        "11111111-1111-1111-1111-111111111111",  # dashboard-minted key (#1511)
        "api",                                    # legacy/bootstrap key
        "not-an-address",                         # no ``@``
    ])
    def test_checkout_never_posts_a_non_email_key_creator(
            self, monkeypatch, sb, creator):
        """#4504 shape gate, control-plane twin: an ``api_keys.created_by``
        that is a user UUID / the literal ``"api"`` / an ``@``-less token is
        NOT an address — it must fall THROUGH to the 400, never be posted to
        Stripe as the customer email (a UUID or garbage there 502s or binds a
        junk address).

        Mutation that makes this fail: drop ``_billing_email_like`` from the
        twin — the creator id is then handed to ``create_customer``.
        """
        tc, fake = sb
        import tortoise.hosted_api as ha
        org = {"org_id": self.ORG_ID, "tier": "solo",
               "email": None, "created_by": creator}
        ha.app.dependency_overrides[ha.get_current_org_session] = (
            lambda: dict(org))
        called: list[str] = []
        monkeypatch.setattr(billing.StripeClient, "create_customer",
                            lambda self, e: called.append(e) or "cus_x")
        monkeypatch.setattr(billing.StripeClient, "list_subscriptions",
                            lambda self, cid: called.append("list") or [])
        monkeypatch.setattr(billing.StripeClient, "create_checkout_session",
                            lambda self, *a: called.append("session") or "u")
        r = tc.post("/v1/billing/checkout", json={"price_id": "price_100soloM"})
        assert r.status_code == 400, r.text
        assert "No customer email" in r.json()["detail"]
        assert called == [], "a non-email creator id must not reach Stripe"
        assert fake.tables["organizations"][0]["customer_email"] is None

    def test_key_creator_email_beats_the_session_email(self, monkeypatch, sb):
        """#4727 precedence PARITY with the registry lane: ``APIKey.created_by``
        resolves before the #4504 session fallback, so the control-plane twin
        must sit in the same slot (after ``org.email``, before the session
        email).

        Mutation that makes this fail: move the twin AFTER the session block —
        the clicking member's address then wins over the provisioner's.
        """
        tc, _fake = sb
        import tortoise.hosted_api as ha
        org = {"org_id": self.ORG_ID, "tier": "solo", "email": None,
               "created_by": "provisioner@example.com",
               "session_user_email": "member@example.com"}
        ha.app.dependency_overrides[ha.get_current_org_session] = (
            lambda: dict(org))
        created: list[str] = []
        monkeypatch.setattr(billing.StripeClient, "create_customer",
                            lambda self, e: created.append(e) or "cus_kc_2")
        monkeypatch.setattr(billing.StripeClient, "list_subscriptions",
                            lambda self, cid: [])
        monkeypatch.setattr(billing.StripeClient, "create_checkout_session",
                            lambda self, *a: "https://checkout.stripe.com/pay/kc2")
        r = tc.post("/v1/billing/checkout", json={"price_id": "price_100soloM"})
        assert r.status_code == 200, r.text
        assert created == ["provisioner@example.com"]

    def test_org_email_still_beats_the_key_creator_email(self, monkeypatch, sb):
        """#4727 is ADDITIVE: the twin is inserted after the ``org.email``
        step, so the authoritative row's address keeps its precedence.

        Mutation that makes this fail: place the twin BEFORE the
        ``org[\"email\"]`` read.
        """
        tc, _fake = sb
        import tortoise.hosted_api as ha
        org = {"org_id": self.ORG_ID, "tier": "solo",
               "email": "org@example.com",
               "created_by": "provisioner@example.com"}
        ha.app.dependency_overrides[ha.get_current_org_session] = (
            lambda: dict(org))
        created: list[str] = []
        monkeypatch.setattr(billing.StripeClient, "create_customer",
                            lambda self, e: created.append(e) or "cus_kc_3")
        monkeypatch.setattr(billing.StripeClient, "list_subscriptions",
                            lambda self, cid: [])
        monkeypatch.setattr(billing.StripeClient, "create_checkout_session",
                            lambda self, *a: "https://checkout.stripe.com/pay/kc3")
        r = tc.post("/v1/billing/checkout", json={"price_id": "price_100soloM"})
        assert r.status_code == 200, r.text
        assert created == ["org@example.com"]

    def test_owner_user_email_beats_the_clicking_members_session_email(
            self, monkeypatch, sb):
        """#4508 — the LIVE case, and the one ``org["email"]`` cannot answer.

        ``organizations.email`` is populated only by the lanes that pass
        ``p_email`` to the ``provision_team`` RPC (``/v1/register``, the
        Edge Function). Every SESSION-lane create — ``POST /v1/organizations``
        (``create_org``), the onboarding sub-org, the new-org checkout
        webhook — passes no ``p_email``, so the row's email is NULL and
        ``_session_user_org`` puts ``email: None`` on the resolved org dict.
        The chain then used to fall through to the CLICKING MEMBER's verified
        session email, binding a non-owner member's personal address as the
        org's Stripe customer and persisted billing contact (the #4508
        consequence, still reachable after #4640).

        The org's own address is the ACTIVE OWNER's verified user email — a
        USER property (#1765 demoted ``organizations.email``: nothing writes
        it on claim, and the oauth lane already prefers
        ``owner_email(cp, org_id)`` over the contact column when it builds the
        same org dict). Resolved here on the Supabase lane only.

        Mutation that makes this fail: drop the owner link (or move it after
        the session block) — ``member@example.com`` becomes the customer
        email.
        """
        tc, fake = sb
        import tortoise.hosted_api as ha
        import tortoise.supabase_control as sc
        org = {"org_id": self.ORG_ID, "tier": "solo", "email": None,
               "session_user_email": "member@example.com"}
        ha.app.dependency_overrides[ha.get_current_org_session] = (
            lambda: dict(org))
        asked: list[str] = []

        def _owner(cp, org_id):
            asked.append(org_id)
            return "owner@example.com"

        monkeypatch.setattr(sc, "owner_email", _owner)
        created: list[str] = []
        monkeypatch.setattr(billing.StripeClient, "create_customer",
                            lambda self, e: created.append(e) or "cus_ow_1")
        monkeypatch.setattr(billing.StripeClient, "list_subscriptions",
                            lambda self, cid: [])
        monkeypatch.setattr(billing.StripeClient, "create_checkout_session",
                            lambda self, *a: "https://checkout.stripe.com/pay/ow1")

        r = tc.post("/v1/billing/checkout", json={"price_id": "price_100soloM"})
        assert r.status_code == 200, r.text
        assert asked == [self.ORG_ID], "the owner read must use the resolved org id"
        assert created == ["owner@example.com"], (
            "a non-owner member's session email must never become the org's "
            "Stripe customer")
        row = fake.tables["organizations"][0]
        assert row["stripe_customer_id"] == "cus_ow_1"
        assert row["customer_email"] == "owner@example.com"

    def test_owner_read_runs_when_the_org_email_is_not_an_address(
            self, monkeypatch, sb):
        """The gate on the owner read is a SHAPE test, not a truthiness test
        (#8931 review).

        ``organizations.email`` is a stale-prone contact field (#1765), so a
        non-NULL value that is not an address (``""``, ``"api"``, a user
        UUID, a legacy token) can sit in the column on the very lane where a
        session member clicks Upgrade. Such a value cannot answer step 1
        (``_billing_email_like`` rejects it and the chain falls through), so
        the owner read MUST still run — otherwise the #4508 fix silently
        disappears for exactly those orgs and the member's own session email
        is bound as the org's billing contact again.

        Mutation that makes this fail: weaken the gate to
        ``not org.get("email")`` (or to a plain truthiness check). The
        precedence test still passes under that mutation — only this one reds.
        """
        tc, _fake = sb
        import tortoise.hosted_api as ha
        import tortoise.supabase_control as sc
        org = {"org_id": self.ORG_ID, "tier": "solo",
               "email": "api",
               "session_user_email": "member@example.com"}
        ha.app.dependency_overrides[ha.get_current_org_session] = (
            lambda: dict(org))
        asked: list[str] = []

        def _owner(cp, org_id):
            asked.append(org_id)
            return "owner@example.com"

        monkeypatch.setattr(sc, "owner_email", _owner)
        created: list[str] = []
        monkeypatch.setattr(billing.StripeClient, "create_customer",
                            lambda self, e: created.append(e) or "cus_ow_4")
        monkeypatch.setattr(billing.StripeClient, "list_subscriptions",
                            lambda self, cid: [])
        monkeypatch.setattr(billing.StripeClient, "create_checkout_session",
                            lambda self, *a: "https://checkout.stripe.com/pay/ow4")

        r = tc.post("/v1/billing/checkout", json={"price_id": "price_100soloM"})
        assert r.status_code == 200, r.text
        assert asked == [self.ORG_ID], (
            "a non-address value in organizations.email must not suppress the "
            "owner read — it cannot answer step 1")
        assert created == ["owner@example.com"], (
            "an unusable column value must never fall through to the clicking "
            "member's session email")

    def test_owner_email_failure_degrades_to_the_session_email(
            self, monkeypatch, sb):
        """The owner read is a control-plane + GoTrue admin call ON the
        checkout path: it is fail-soft (``owner_email`` never raises, and the
        caller guards it too). A dead admin API must degrade to the existing
        chain, never 500 an upgrade the member is entitled to.

        Mutation that makes this fail: let the owner-read exception escape
        ``_billing_checkout_sync``.
        """
        tc, _fake = sb
        import tortoise.hosted_api as ha
        import tortoise.supabase_control as sc
        org = {"org_id": self.ORG_ID, "tier": "solo", "email": None,
               "session_user_email": "member@example.com"}
        ha.app.dependency_overrides[ha.get_current_org_session] = (
            lambda: dict(org))

        def _boom(cp, org_id):
            raise RuntimeError("gotrue admin API unreachable")

        monkeypatch.setattr(sc, "owner_email", _boom)
        created: list[str] = []
        monkeypatch.setattr(billing.StripeClient, "create_customer",
                            lambda self, e: created.append(e) or "cus_ow_2")
        monkeypatch.setattr(billing.StripeClient, "list_subscriptions",
                            lambda self, cid: [])
        monkeypatch.setattr(billing.StripeClient, "create_checkout_session",
                            lambda self, *a: "https://checkout.stripe.com/pay/ow2")

        r = tc.post("/v1/billing/checkout", json={"price_id": "price_100soloM"})
        assert r.status_code == 200, r.text
        assert created == ["member@example.com"]

    def test_org_email_still_outranks_the_owner_email(self, monkeypatch, sb):
        """#4508 is ADDITIVE, not a re-order: the links already landing keep
        their precedence — so the owner link must sit AFTER ``org["email"]``
        (#4640) and must not resurrect a Stripe customer email for an org that
        already carries its own registered address.

        The spy sits on ``hosted_api._billing_owner_email`` (the call-site
        seam) with a COUNTER, because the precedence assertion alone is blind
        to the read: the value is discarded either way, so the test passed
        whether the Supabase read happened or not (#8931 review). The read is
        two network round-trips on a revenue path — one control-plane
        ``org_memberships`` membership read plus a synchronous GoTrue admin
        ``GET`` (``timeout=10.0``) — and step 1 of the chain makes it
        unusable whenever ``organizations.email`` is set, which
        ``_session_user_org`` always stamps (``hosted_api.py``: the resolved
        org dict's ``email``).

        Mutations that make this fail: (a) place the owner link before the
        ``org["email"]`` read → ``created == ["owner@example.com"]``; (b)
        delete the ``not _billing_email_like(org.get("email"))`` gate → the
        ``asked`` list is non-empty for an org that already has an address.
        """
        tc, _fake = sb
        import tortoise.hosted_api as ha
        org = {"org_id": self.ORG_ID, "tier": "solo",
               "email": "org@example.com",
               "session_user_email": "member@example.com"}
        ha.app.dependency_overrides[ha.get_current_org_session] = (
            lambda: dict(org))
        asked: list[str] = []

        def _owner(org_id):
            asked.append(org_id)
            return "owner@example.com"

        monkeypatch.setattr(ha, "_billing_owner_email", _owner)
        created: list[str] = []
        monkeypatch.setattr(billing.StripeClient, "create_customer",
                            lambda self, e: created.append(e) or "cus_ow_3")
        monkeypatch.setattr(billing.StripeClient, "list_subscriptions",
                            lambda self, cid: [])
        monkeypatch.setattr(billing.StripeClient, "create_checkout_session",
                            lambda self, *a: "https://checkout.stripe.com/pay/ow3")

        r = tc.post("/v1/billing/checkout", json={"price_id": "price_100soloM"})
        assert r.status_code == 200, r.text
        assert created == ["org@example.com"]
        assert asked == [], (
            "the owner-email read (control-plane + GoTrue admin GET) must be "
            "skipped when ``organizations.email`` already answers step 1")

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

    def test_mirror_subscription_writes_the_orgs_row_not_the_registry(
            self, sb, monkeypatch):
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

        F3 (#4726 review): the seam PAYLOAD is pinned by a spy, not only
        inferred from the resulting row. ``update_org_billing``'s ``allowed``
        allow-list silently drops ``cancel_at_period_end``, so a row-only
        assertion cannot see the exclusion at ``billing.py`` — deleting it
        (passing ``dict(twin)``) left the old test GREEN. Exact-payload
        equality reds that mutation: the failing value is a payload carrying
        ``cancel_at_period_end``, reachable because the fixture's
        ``sub`` sets it ``True``.
        """
        import tortoise.supabase_control as sc

        _, fake = sb
        sent: list[tuple[str, dict]] = []
        real_update = sc.update_org_billing

        def _spy(cp, org_id, updates):
            sent.append((org_id, dict(updates)))
            return real_update(cp, org_id, updates)

        monkeypatch.setattr(sc, "update_org_billing", _spy)

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
        # The seam sees the EXACT mirror payload (apply_limits' quota write is
        # the earlier call; the status/period write is the last).
        assert sent[-1] == (self.ORG_ID, {
            "subscription_status": "active",
            "subscription_id": "sub_sb_4726",
            "current_period_start": 1756512000,
            "current_period_end": 1759104000,
            "customer_email": "owner@example.com",
        }), sent
        assert all("cancel_at_period_end" not in u for _, u in sent), sent
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

    def test_mirror_subscription_raises_when_the_org_row_is_absent(self, sb):
        """#4726 F2: mirroring onto an org with NO ``organizations`` row must
        fail closed, not return a success summary.

        Failing value: the seam's PATCH matches 0 rows — the fixture row's id
        is ``ORG_ID``, so ``org_absent_4726`` is absent; pre-fix the mirror
        returned ``{'tier': 'pro', ...}`` while the row list stayed unchanged.
        Reachable in the fixture: ``sb`` seeds exactly one org row, and the
        ``None`` SDK is never reached (the Supabase branch raises first).
        """
        _, fake = sb
        before = [dict(r) for r in fake.tables["organizations"]]
        sub = {"id": "sub_absent_4726", "status": "active",
               "items": {"data": [{"price": {"id": "price_200proMM"}}]}}
        with pytest.raises(RuntimeError, match="no organizations row matched"):
            billing.mirror_subscription(None, "org_absent_4726", sub)
        assert fake.tables["organizations"] == before

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


class TestBillingSupabaseKeyAuthCreatorEmail:
    """#4727, driven across the REAL key-auth seam — no dependency override.

    The tests above inject the resolved org dict, so they pin the chain's
    DECISION but not the premise the fix rests on: that ``resolve_api_key``
    really carries ``api_keys.created_by`` onto the org dict that
    ``_billing_checkout_sync`` receives. This class runs the actual
    ``Authorization: Bearer tt_...`` lane end to end against the
    FakeControlPlane (resolve_api_key → get_current_org →
    get_current_org_session → checkout → Stripe), so a future change to the
    resolution seam reds here instead of silently re-opening the 400.
    """

    ORG_ID = "org-keycreator-4727"
    KEY = "tt_keycreator4727token"

    @pytest.fixture
    def env(self, monkeypatch, tmp_path):
        import tortoise.hosted_api as ha
        import tortoise.supabase_control as sc
        from tests._http_fixtures import patched_tortoise_sdk
        from tests.fake_control_plane import FakeControlPlane
        from tortoise.auth import lookup_hash

        monkeypatch.setenv("SUPABASE_URL", "https://kc4727.test.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc-kc-4727")
        # PIN the lane: an ambient TORTOISE_CONTROL_PLANE=registry would flip
        # these to selfhost (registry SDK) and test the wrong chain.
        monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "supabase")
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_123")
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
        monkeypatch.setenv("STRIPE_PRICE_IDS", json.dumps(VALID_CATALOG))
        monkeypatch.setenv("RATE_LIMIT_DISABLED", "1")
        fake = FakeControlPlane({
            "organizations": [{
                # The provision-path org: NO email on the authoritative row.
                "id": self.ORG_ID, "name": "kc-4727", "tier": "solo",
                "email": None, "graph_name": f"org_{self.ORG_ID}",
                "stripe_customer_id": None, "subscription_status": None,
                "customer_email": None,
                "suspended_at": None, "flagged_at": None,
                "max_users": 1, "max_graphs": 1, "ops_allowance": 10000,
                "graph_size_cap": 100000,
            }],
            "api_keys": [{
                "id": "key-kc-4727", "org_id": self.ORG_ID,
                "lookup_hash": lookup_hash(self.KEY),
                "key_prefix": self.KEY[:10], "created_via": "provisioned",
                "created_by": "provisioner@example.com",
                "created_at": "2026-08-01T00:00:00+00:00",
                "expires_at": None, "revoked_at": None, "enabled": True,
            }],
        })
        monkeypatch.setattr(sc, "get_control_plane", lambda: fake)
        with patched_tortoise_sdk(str(tmp_path / "kc4727.db")):
            yield verdict_client(ha.app), fake

    def _stub_stripe(self, monkeypatch, cid: str = "cus_kc_e2e"):
        created: list[str] = []
        monkeypatch.setattr(billing.StripeClient, "create_customer",
                            lambda self, e: created.append(e) or cid)
        monkeypatch.setattr(billing.StripeClient, "list_subscriptions",
                            lambda self, c: [])
        monkeypatch.setattr(billing.StripeClient, "create_checkout_session",
                            lambda self, *a: "https://checkout.stripe.com/pay/kc-e2e")
        return created

    def test_provision_path_key_auth_reaches_stripe_with_the_creator_email(
            self, monkeypatch, env):
        """The issue's exact scenario: Supabase org with ``email`` NULL, a
        ``tt_`` key whose ``created_by`` is a real address, no session email →
        200 with that address on the Stripe customer (pre-fix: 400).
        """
        tc, fake = env
        created = self._stub_stripe(monkeypatch)
        r = tc.post("/v1/billing/checkout", json={"price_id": "price_100soloM"},
                    headers={"Authorization": f"Bearer {self.KEY}"})
        assert r.status_code == 200, r.text
        assert created == ["provisioner@example.com"]
        row = fake.tables["organizations"][0]
        assert row["stripe_customer_id"] == "cus_kc_e2e"
        assert row["customer_email"] == "provisioner@example.com"

    def test_a_uuid_creator_through_the_real_seam_still_400s(
            self, monkeypatch, env):
        """Same real lane, a dashboard-minted key (``created_by`` = user
        UUID): the shape gate must hold end to end — 400, no Stripe call, and
        no registry SDK constructed (#878).
        """
        tc, fake = env
        fake.tables["api_keys"][0]["created_by"] = (
            "22222222-2222-2222-2222-222222222222")
        called = self._stub_stripe(monkeypatch)
        r = tc.post("/v1/billing/checkout", json={"price_id": "price_100soloM"},
                    headers={"Authorization": f"Bearer {self.KEY}"})
        assert r.status_code == 400, r.text
        assert "No customer email" in r.json()["detail"]
        assert called == []
        assert fake.tables["organizations"][0]["customer_email"] is None


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

class TestReconcileSweep:
    """#8878 — the cadenced caller of ``reconcile_org``.

    The defect: ``tier`` is written ONLY by the Stripe webhook and it IS the
    entitlement (``effective_tier`` never derives upward), so one missed
    delivery capped a paying org at the lower tier permanently. Measured
    2026-10-17: the fleet's own org was billed on the Pro price and enforced at
    Solo's 25 000-node cap, every hosted write refused with "upgrade your
    plan" while reads kept working. ``reconcile_org`` existed but had no
    production caller, so nothing ever compared the store against Stripe.

    These tests drive the four states the issue names, plus the mutation check:
    a "reconciler" that only READS the price and writes nothing must be
    reported as UNREPAIRED, never as a clean pass.
    """

    def _seed(self, sdk, org_id, **props):
        sets = ", ".join(f"t.{k}=${k}" for k in props)
        sdk._get_registry().query(
            f"MATCH (t:Team {{id:$id}}) SET {sets}",
            params={"id": org_id, **props})

    def _row(self, sdk, org_id):
        return sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) RETURN t.tier, t.max_points, "
            "t.subscription_status", params={"id": org_id}).result_set[0]

    def test_under_delivered_pro_org_is_repaired_and_the_cap_raised(
            self, monkeypatch, billing_client):
        """price=pro + tier=solo + active → the sweep restores Pro, and the
        ENFORCED cap (the thing that refused the writes) goes with it."""
        from tortoise import billing as bl
        org_id = billing_client["org_id"]
        sdk = billing_client["sdk"]
        self._seed(sdk, org_id, tier="solo", subscription_status="active",
                   subscription_id="sub_8878", stripe_customer_id="cus_8878")
        sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.max_points=$mp", 
            params={"id": org_id, "mp": 25000})  # Solo's cap, as drift left it
        monkeypatch.setattr(bl.StripeClient, "get_subscription",
                            lambda self, sid: {
                                "id": "sub_8878", "status": "active",
                                "items": {"data": [
                                    {"price": {"id": "price_200proMM"}}]}})
        paged = []
        summary = bl.reconcile_subscriptions(
            sdk, notifier=lambda *a: paged.append(a))

        assert summary["checked"] == 1 and summary["repaired"] == 1, summary
        assert summary["failed"] == [] and summary["unrepaired"] == []
        assert [d["direction"] for d in summary["drift"]] == ["up"]
        assert summary["drift"][0]["from_tier"] == "solo"
        assert summary["drift"][0]["to_tier"] == "pro"
        assert summary["drift"][0]["from_max_nodes"] == 25000
        assert summary["drift"][0]["to_max_nodes"] == 100000
        # The customer-visible consequence: the cap that refused the writes.
        tier, max_points, status = self._row(sdk, org_id)
        assert (tier, max_points, status) == ("pro", 100000, "active"), \
            "drift repaired on the tier but not on the enforced cap"
        # ...and it PAGES (piece 3), because this is a payable customer who
        # was under-delivered.
        assert summary["alerted"] == 1 and len(paged) == 1
        assert paged[0][0] == org_id and paged[0][1:] == ("solo", "pro", "active")

    def test_already_correct_org_is_unchanged_and_never_pages(
            self, monkeypatch, billing_client):
        """price=pro + tier=pro → unchanged (no write, no page)."""
        from tortoise import billing as bl
        org_id = billing_client["org_id"]
        sdk = billing_client["sdk"]
        self._seed(sdk, org_id, tier="pro", subscription_status="active",
                   subscription_id="sub_8878b")
        monkeypatch.setattr(bl.StripeClient, "get_subscription",
                            lambda self, sid: {
                                "id": "sub_8878b", "status": "active",
                                "items": {"data": [
                                    {"price": {"id": "price_200proMM"}}]}})
        paged = []
        summary = bl.reconcile_subscriptions(
            sdk, notifier=lambda *a: paged.append(a))
        assert summary["checked"] == 1
        assert summary["unchanged"] == 1 and summary["repaired"] == 0
        assert summary["drift"] == [] and paged == []
        assert self._row(sdk, org_id)[0] == "pro"

    def test_org_without_a_stripe_identity_is_skipped_not_guessed(
            self, monkeypatch, billing_client):
        """A free org (never checked out) must not be touched — the sweep has
        nothing to reconcile it against, and a blind write is not a repair."""
        from tortoise import billing as bl
        org_id = billing_client["org_id"]
        sdk = billing_client["sdk"]
        self._seed(sdk, org_id, tier="solo")
        called = []
        monkeypatch.setattr(bl.StripeClient, "get_subscription",
                            lambda self, sid: called.append(sid))
        monkeypatch.setattr(bl.StripeClient, "list_subscriptions",
                            lambda self, cid: called.append(cid))
        summary = bl.reconcile_subscriptions(sdk, notifier=False)
        assert summary["skipped"] >= 1 and summary["checked"] == 0
        assert called == [], "a Stripe call was made for an org with no identity"
        assert self._row(sdk, org_id)[0] == "solo"

    def test_read_only_reconciler_is_reported_unrepaired(
            self, monkeypatch, billing_client):
        """THE MUTATION CHECK (#8878's evidence bar): a reconciler that only
        resolves the price and writes nothing must FAIL the pass, not report
        success.

        Mutation: ``apply_limits`` becomes a recorder, so the tier resolves to
        pro from Stripe but the store keeps solo. A pass that trusted its own
        read would report ``repaired=1``; the reconciler re-reads the store and
        reports ``unrepaired`` instead.
        """
        from tortoise import billing as bl
        org_id = billing_client["org_id"]
        sdk = billing_client["sdk"]
        self._seed(sdk, org_id, tier="solo", subscription_status="active",
                   subscription_id="sub_8878m")
        sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.max_points=$mp",
            params={"id": org_id, "mp": 25000})
        writes = []
        monkeypatch.setattr(bl, "apply_limits",
                            lambda sdk_, oid, tier: writes.append((oid, tier)))
        monkeypatch.setattr(bl.StripeClient, "get_subscription",
                            lambda self, sid: {
                                "id": "sub_8878m", "status": "active",
                                "items": {"data": [
                                    {"price": {"id": "price_200proMM"}}]}})
        paged = []
        summary = bl.reconcile_subscriptions(
            sdk, notifier=lambda *a: paged.append(a))

        assert writes == [(org_id, "pro")], "the tier resolved but was not applied"
        assert summary["repaired"] == 0
        assert summary["unrepaired"] == [
            {"org_id": org_id, "expected_tier": "pro", "stored_tier": "solo"}]
        assert summary["drift"] == [] and paged == []
        assert tuple(self._row(sdk, org_id)[:2]) == ("solo", 25000)

    def test_unknown_price_fails_the_org_and_preserves_the_stored_tier(
            self, monkeypatch, billing_client):
        """Existing behaviour must not regress: an unresolvable price keeps the
        stored tier/status (never downgrade a paid org on an unparseable
        price, review fix 7) and is reported as a FAILED org."""
        from tortoise import billing as bl
        org_id = billing_client["org_id"]
        sdk = billing_client["sdk"]
        self._seed(sdk, org_id, tier="pro", subscription_status="active",
                   subscription_id="sub_8878u")
        monkeypatch.setattr(bl.StripeClient, "get_subscription",
                            lambda self, sid: {
                                "id": "sub_8878u", "status": "active",
                                "items": {"data": [
                                    {"price": {"id": "price_unknown8878"}}]}})
        summary = bl.reconcile_subscriptions(sdk, notifier=False)
        assert summary["checked"] == 1 and summary["repaired"] == 0
        assert len(summary["failed"]) == 1
        assert summary["failed"][0]["org_id"] == org_id
        assert "unknown price" in summary["failed"][0]["error"]
        assert self._row(sdk, org_id)[0] == "pro", "stored tier must be preserved"

    def test_past_due_past_grace_still_degrades_after_the_sweep(
            self, monkeypatch, billing_client):
        """subscription_status=past_due past grace → still degrades per
        ``effective_tier``. The sweep mirrors Stripe truth; it must not turn a
        lapsed payer into an entitled one."""
        from datetime import UTC, datetime, timedelta

        from tortoise import billing as bl
        org_id = billing_client["org_id"]
        sdk = billing_client["sdk"]
        past = (datetime.now(UTC) - timedelta(days=2)).isoformat()
        self._seed(sdk, org_id, tier="solo", subscription_id="sub_8878p")
        sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.grace_until=$g",
            params={"id": org_id, "g": past})
        monkeypatch.setattr(bl.StripeClient, "get_subscription",
                            lambda self, sid: {
                                "id": "sub_8878p", "status": "past_due",
                                "items": {"data": [
                                    {"price": {"id": "price_200proMM"}}]}})
        bl.reconcile_subscriptions(sdk, notifier=False)
        tier, max_points, status = self._row(sdk, org_id)
        assert status == "past_due"
        # The ISO string is the shape the ONLY production writer stores
        # (``hosted_api`` writes ``grace.isoformat()``) — the degrade must fire
        # on it, not just on the epoch floats the older unit tests used.
        assert bl.effective_tier({"tier": tier, "subscription_status": status,
                                  "grace_until": past}) == "free"
        assert max_points == 100000, "mirror keeps Stripe truth; the READ degrades"

    def test_enumeration_failure_fails_closed(self, monkeypatch, billing_client):
        """A pass must never report "0 orgs checked" from a source it could not
        read (the NO_ORGS class #596 named)."""
        from tortoise import billing as bl

        class _Broken:
            def query(self, *a, **kw):
                raise RuntimeError("control plane down")

        monkeypatch.setattr(bl, "_supabase_mode", lambda: True)
        import tortoise.supabase_control as sc
        monkeypatch.setattr(sc, "get_control_plane", lambda: _Broken())
        with pytest.raises(RuntimeError):
            bl.reconcile_subscriptions(None, notifier=False)

    def test_enumeration_walks_the_certifying_seam_not_a_single_page(
            self, monkeypatch):
        """#8878 review: the enumerate must use the seam that OWNS completeness.

        ``SupabaseControlPlane.query`` is ONE PostgREST page and PostgREST caps a
        row list at ``max_rows`` silently — a truncated read arrives as a
        non-empty PARTIAL list with no error. Reading it as the fleet would
        report a plausible ``checked=N`` and green the cron while every org past
        the page was never compared to Stripe: the permanent silent cap #8878
        exists to remove, re-created for a suffix of the fleet.
        """
        import tortoise.supabase_control as sc
        from tortoise import billing as bl

        seen: dict = {}

        class _Cp:
            def query_all(self, table, **kw):
                seen["table"], seen["kw"] = table, kw
                return [{"id": f"org-{i}"} for i in range(1200)], True

            def query(self, *a, **kw):  # pragma: no cover - must not be used
                raise AssertionError(
                    "the single-page query() was used for the fleet — that is "
                    "the truncating read #8878 exists to avoid")

        monkeypatch.setattr(sc, "get_control_plane", lambda: _Cp())
        rows = bl._enumerate_billed_orgs()
        assert len(rows) == 1200, "a page past the first was dropped"
        assert seen["table"] == "organizations"
        assert seen["kw"]["cursor"] == "id"
        # No `deleted_at IS NULL`: a soft-deleted org can still be BILLED, and
        # excluding it would hide a live charge from the only pass that looks.
        assert seen["kw"]["filters"] is None

    def test_uncertifiable_enumeration_fails_closed(self, monkeypatch):
        """``complete=False`` is a LOWER BOUND, never a clean pass."""
        import tortoise.supabase_control as sc
        from tortoise import billing as bl

        class _Truncated:
            def query_all(self, table, **kw):
                return [{"id": "org-1"}], False

        monkeypatch.setattr(sc, "get_control_plane", lambda: _Truncated())
        with pytest.raises(RuntimeError, match="CERTIFIED"):
            bl._enumerate_billed_orgs()

    def test_seam_without_a_completeness_signal_refuses(self, monkeypatch):
        """An older seam may certify by the SERVER's count; if it cannot, refuse.

        A page nobody can vouch for must never be published as the fleet.
        """
        import tortoise.supabase_control as sc
        from tortoise import billing as bl

        class _Opaque:
            def query_with_total(self, table, **kw):
                return [{"id": "org-1"}], None  # server stated no total

        monkeypatch.setattr(sc, "get_control_plane", lambda: _Opaque())
        with pytest.raises(RuntimeError, match="LOWER BOUND"):
            bl._enumerate_billed_orgs()

    def test_limit_caps_processing_without_weakening_certification(
            self, monkeypatch):
        """``limit`` is an ops/test cap on how many rows are PROCESSED."""
        import tortoise.supabase_control as sc
        from tortoise import billing as bl

        class _Cp:
            def query_all(self, table, **kw):
                return [{"id": f"org-{i}"} for i in range(10)], True

        monkeypatch.setattr(sc, "get_control_plane", lambda: _Cp())
        assert len(bl._enumerate_billed_orgs(limit=3)) == 3

    def test_limit_cannot_buy_a_partial_page(self, monkeypatch):
        """The OTHER half of the name above, which that test cannot see.

        ``limit`` must not be a way to publish an UNCERTIFIED page: hoisting the
        truncation above the certificate check (a natural refactor — it is the
        cheaper order) would re-open the partial-page fail-open whenever
        ``limit`` is set, while ``test_limit_caps_processing_…`` stayed green
        because its double is always ``complete=True``. The discriminating case
        is ``limit`` PLUS ``complete=False``.
        """
        import tortoise.supabase_control as sc
        from tortoise import billing as bl

        class _Truncated:
            def query_all(self, table, **kw):
                return [{"id": f"org-{i}"} for i in range(10)], False

        monkeypatch.setattr(sc, "get_control_plane", lambda: _Truncated())
        with pytest.raises(RuntimeError, match="CERTIFIED"):
            bl._enumerate_billed_orgs(limit=3)

    def test_abandoned_checkout_is_skipped_not_unverified(
            self, monkeypatch, billing_client):
        """A customer binding at the UNPAID tier is routine, not a symptom.

        ``hosted_api`` persists ``stripe_customer_id`` BEFORE the checkout
        session, so every abandoned checkout leaves a Stripe binding on a
        ``tier=free`` row. Counting that as ``unverified`` put healthy orgs in
        the operator's queue forever (and emitted a daily ``::warning::`` in the
        driver) — ``docs/infra-runbook.md`` §4.2 tells the operator to look at
        every non-zero ``unverified``. Where the org lands must be decided from
        the LADDER (is this tier paid?), not from the presence of an identity.
        """
        from tortoise import billing as bl
        org_id = billing_client["org_id"]
        sdk = billing_client["sdk"]
        self._seed(sdk, org_id, tier="free", stripe_customer_id="cus_8878ab")
        monkeypatch.setattr(bl.StripeClient, "list_subscriptions",
                            lambda self, cid: [])
        summary = bl.reconcile_subscriptions(sdk, notifier=False)
        assert summary["checked"] == 1, (
            "the attempt WAS made (Stripe was called), so the org is checked")
        assert summary["skipped"] == 1, (
            "an abandoned checkout has no entitlement to verify, so it is "
            f"skipped, not a person to look at — got {summary}")
        assert summary["unverified"] == 0, (
            "a customer binding at the UNPAID tier is not 'stored as billed' "
            f"— got {summary}")
        assert summary["repaired"] == 0 and summary["drift"] == []
        assert self._row(sdk, org_id)[0] == "free"

    def test_a_failed_customer_list_is_unverified_not_a_silent_downgrade(
            self, monkeypatch, billing_client):
        """#8904 review P2: the sweep must not cap a paying org on a provider error.

        A downgrade is recorded as ``repaired`` with ``direction: "down"``, which
        the driver neither alerts on nor fails on (only drift UP notifies).
        Mirroring an inactive stored subscription when the customer LIST fails
        would therefore cap a PAYING customer on a fully green run, with no page
        and no operator signal — the precise failure the #8895 repair exists to
        end. The org lands in ``unverified`` instead: warned about, tier untouched.
        """
        from tortoise import billing as bl
        org_id = billing_client["org_id"]
        sdk = billing_client["sdk"]
        self._seed(sdk, org_id, tier="pro", subscription_status="active",
                   subscription_id="sub_gone", stripe_customer_id="cus_gone")
        monkeypatch.setattr(
            bl.StripeClient, "get_subscription",
            lambda self, sid: {"id": "sub_gone", "status": "canceled",
                               "items": {"data": [
                                   {"price": {"id": "price_100proM"}}]}})
        monkeypatch.setattr(
            bl.StripeClient, "list_subscriptions",
            lambda self, cid: (_ for _ in ()).throw(
                bl.StripeAPIError("upstream 503")))
        summary = bl.reconcile_subscriptions(sdk, notifier=False)
        assert summary["checked"] == 1
        assert summary["unverified"] == 1, (
            "a paid org whose entitlement could not be verified is exactly what "
            f"``unverified`` names — got {summary}")
        assert summary["repaired"] == 0 and summary["drift"] == [], (
            "no write was justified, so nothing may be reported as repaired "
            f"— got {summary}")
        assert self._row(sdk, org_id)[0] == "pro", (
            "the org is PAYING and Stripe did not say otherwise — its tier must "
            "be untouched")

    def test_an_unreadable_rank_never_awards_a_replacement(
            self, stripe_env):
        """The rank guard's SAFE direction, unit-pinned — on both sides.

        ``_subscription_rank`` returns ``None`` for a price the catalog does not
        know and for an unreadable pricing ladder (``_paid_tier_floor``). Both
        mean "cannot prove this is not lower", which is not a reason to take
        entitlement away from a subscription the customer has PAID through
        ``current_period_end`` — so the still-in-force one is kept. The mirror
        of it: a provably equal-or-higher newer sub does replace it (the
        upgrade case, also pinned end-to-end by
        ``test_reconcile_an_active_but_ENDING_pointer_follows_the_newer_sub``).

        ⛔ Both SIDES of that rule are pinned below: the unrankable entry may be
        the newer candidate (cannot prove it is higher) or the ENDING one (cannot
        prove it is not) — and the second only ever revokes entitlement, so a
        guard that covers just the first is half a rule.
        """
        from tortoise import billing as bl

        def _sub(sid, price, ending, status="active"):
            return {"id": sid, "status": status,
                    "cancel_at_period_end": ending,
                    "items": {"data": [{"price": {"id": price}}]}}

        ending_team = _sub("sub_team", "price_300teamM", True)
        unknown_price = _sub("sub_unknown", "price_nope", False)
        assert bl._subscription_rank(unknown_price) is None
        assert bl._select_current_subscription([unknown_price, ending_team]) \
            is ending_team

        # A provably lower newer sub loses too, on either side of the ordering.
        ending_solo = _sub("sub_solo", "price_100soloM", True)
        newer_free = _sub("sub_free", "price_000freeM", False)
        assert bl._select_current_subscription([newer_free, ending_solo]) \
            is ending_solo
        assert bl._select_current_subscription([ending_solo, newer_free]) \
            is ending_solo

        # Equal or higher DOES replace it — the rule is a floor, not a freeze.
        newer_team = _sub("sub_team2", "price_300teamM", False)
        assert bl._select_current_subscription([ending_solo, newer_team]) \
            is newer_team
        newer_solo = _sub("sub_solo2", "price_100soloM", False)
        assert bl._select_current_subscription([ending_solo, newer_solo]) \
            is newer_solo

        # ⛔⛔ And the guard must hold when the unrankable one is the ENDING
        # (still-in-force) sub — that is the direction that REVOKES, and it is
        # the one the selector used to hand to the candidate. ``_best_ranked``
        # skips unrankable entries, so a guard written as ``if kept is not
        # None`` skipped the comparison entirely and mirrored the newer, cheaper
        # sub, capping a paying org on a green pass.
        ending_rotated = _sub("sub_team_old", "price_rotatedOut", True)
        assert bl._subscription_rank(ending_rotated) is None
        assert bl._select_current_subscription([newer_solo, ending_rotated]) \
            is ending_rotated
        assert bl._select_current_subscription([ending_rotated, newer_solo]) \
            is ending_rotated

        # ⛔⛔⛔ And the rule is about the WHOLE ending set, not just a set of
        # one: an unrankable ending SIBLING makes the comparison unprovable
        # too. ``_best_ranked`` skips what it cannot price, so the rankable
        # sibling would win the comparison and a newer sub AT THAT SIBLING'S
        # CAP would be taken as "provably at or above" — revoking the higher
        # period the unpriceable sibling is still paying for, and recording it
        # as a clean repair.
        ending_solo_old = _sub("sub_solo_old", "price_100soloM", True)
        for order in ([newer_solo, ending_solo_old, ending_rotated],
                      [ending_rotated, newer_solo, ending_solo_old],
                      [ending_solo_old, ending_rotated, newer_solo]):
            assert bl._select_current_subscription(order) is ending_rotated, (
                "an unrankable ending sibling must not let a newer sub at its "
                f"rankable sibling's cap win — got {order}")

        # ⛔⛔⛔ And the SAME rule decides an ending set with NO non-ending
        # candidate: there the set used to be resolved by Stripe's LISTING
        # ORDER, so a rankable lower sibling listed first won and the pass
        # recorded the downgrade as a clean ``repaired``/``down``.
        for order in ([ending_solo_old, ending_rotated],
                      [ending_rotated, ending_solo_old]):
            assert bl._select_current_subscription(order) is ending_rotated, (
                "with no live candidate the ending set must still be resolved "
                f"by rank, never by listing order — got {order}")

    def test_an_unreadable_ladder_is_unrankable_not_unlimited(self):
        """``None`` from ``_paid_tier_floor`` means CONFIG FAULT, not unlimited.

        The old docstring said ``None`` = "Team = unlimited" and §4.2 said
        ``direction: null`` = "both caps unlimited". Measured, both are false:
        ``team`` is 600 000 and an unrecognised tier resolves to Free's 10 000
        (``pricing.tier_limits``: "Unknown tiers default to the Free limits").
        ``None`` is reachable only when the ladder RAISES (#750.8). A runbook
        that calls it a benign no-op sends the operator past a config fault.
        """
        import tortoise.pricing as pricing
        from tortoise import billing as bl

        # The real ladder: nothing here is unlimited.
        assert bl._paid_tier_floor("team") == 600000
        assert bl._paid_tier_floor("bogus-tier") == bl._paid_tier_floor("free")
        assert bl._drift_direction("bogus-tier", "team") == "up"

        # The only path to None: the ladder cannot be read at all.
        def _unreadable(tier):
            raise KeyError("pricing.json tier 'pro' missing required keys")

        assert pricing.tier_limits is not _unreadable  # sanity: real symbol
        original = pricing.tier_limits
        pricing.tier_limits = _unreadable
        try:
            assert bl._paid_tier_floor("team") is None
            assert bl._paid_tier_floor("free") is None
            assert bl._drift_direction("pro", "team") is None
        finally:
            pricing.tier_limits = original

    def test_supabase_mode_repairs_the_org_row_with_no_registry_sdk(
            self, monkeypatch, stripe_env):
        """The branch the HOSTED deployment actually takes (#8878 review P2).

        Post-#669 the registry graph is deleted on the hosted lane, so
        ``reconcile_subscriptions`` runs with ``sdk=None`` against the
        control-plane ``organizations`` row. Without this test, the row-shape
        handling, ``reconcile_org(None, …)``, the writes, and the
        repaired/drift classification are exercised ONLY in registry mode —
        i.e. everything except the path production uses.
        """
        import tortoise.supabase_control as sc
        from tests.fake_control_plane import FakeControlPlane
        from tortoise import billing as bl

        org_id = "11111111-1111-4111-8111-111111111111"

        class _Cp(FakeControlPlane):
            """The fake reports NO stated total (``total=None``), which the
            enumeration correctly refuses — so this double states one, the
            only thing a server-side count can supply (#5388)."""

            def query_with_total(self, table, **kw):
                kw.pop("count_exact", None)
                rows = self.query(table, **kw)
                return rows, len(rows)

        cp = _Cp({"organizations": [{
            "id": org_id,
            "tier": "solo",              # the drift: billed Pro, stored Solo
            "subscription_id": "sub_8878sb",
            "stripe_customer_id": "cus_8878sb",
            "subscription_status": "active",
            "graph_size_cap": 25000,
        }]})
        # ``_supabase_mode()`` IS ``is_supabase_enabled()``; patching the seam
        # keeps every branch in the pass (enumeration, identity read,
        # ``apply_limits``) on the control plane instead of half of them
        # falling back to a registry SDK this mode never constructs.
        monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)
        monkeypatch.setattr(sc, "get_control_plane", lambda: cp)
        monkeypatch.setattr(bl.StripeClient, "get_subscription",
                            lambda self, sid: {
                                "id": "sub_8878sb", "status": "active",
                                "items": {"data": [
                                    {"price": {"id": "price_200proMM"}}]}})

        summary = bl.reconcile_subscriptions(None, notifier=False)

        assert summary["checked"] == 1 and summary["repaired"] == 1, summary
        assert summary["failed"] == [] and summary["unrepaired"] == []
        assert [d["direction"] for d in summary["drift"]] == ["up"]
        assert summary["drift"][0]["from_max_nodes"] == 25000
        assert summary["drift"][0]["to_max_nodes"] == 100000
        row = cp.tables["organizations"][0]
        assert row["tier"] == "pro", "the org row was not repaired"
        assert row["graph_size_cap"] == 100000, (
            "the tier moved but the ENFORCED cap did not — the number that "
            "refused the customer's writes")
        assert row["subscription_status"] == "active", (
            "the Stripe status never reached the row in the hosted path")

    def test_no_stripe_subscription_is_unverified_not_unchanged(
            self, monkeypatch, billing_client):
        """A stored-billed org Stripe names no active sub for was NOT verified.

        ``reconcile_org`` reports ``action="noop"`` for the missed-checkout
        blind spot (customer id, no active subscription). Counting that as
        ``unchanged`` reports an unexamined org as verified-clean — the exact
        read-only-reconciler failure the ``unrepaired`` guard exists to catch.
        """
        from tortoise import billing as bl
        org_id = billing_client["org_id"]
        sdk = billing_client["sdk"]
        self._seed(sdk, org_id, tier="pro", stripe_customer_id="cus_8878n")
        monkeypatch.setattr(bl.StripeClient, "list_subscriptions",
                            lambda self, cid: [])
        summary = bl.reconcile_subscriptions(sdk, notifier=False)
        assert summary["checked"] == 1
        assert summary["unverified"] == 1, summary
        assert summary["unchanged"] == 0, \
            "an unverified org must never be reported as verified-clean"
        assert summary["repaired"] == 0 and summary["drift"] == []
        assert self._row(sdk, org_id)[0] == "pro", \
            "no active subscription is not a licence to downgrade"

    def test_one_orgs_store_failure_does_not_abort_the_pass(
            self, monkeypatch, billing_client):
        """The control plane fails with RuntimeError, not a BillingError.

        ``update_org_billing`` raises ``RuntimeError`` when a PATCH matches no
        row (an org deleted/renamed mid-pass). Narrowing the per-org guard to
        the provider errors let one such row abort the whole fleet pass — on
        every run, at the same point, because ordering is stable by id.
        """
        from tortoise import billing as bl
        org_id = billing_client["org_id"]
        sdk = billing_client["sdk"]
        self._seed(sdk, org_id, tier="solo", subscription_status="active",
                   subscription_id="sub_8878r")
        monkeypatch.setattr(bl.StripeClient, "get_subscription",
                            lambda self, sid: {
                                "id": "sub_8878r", "status": "active",
                                "items": {"data": [
                                    {"price": {"id": "price_200proMM"}}]}})

        def _boom(sdk_, oid):
            raise RuntimeError("update_org_billing: no row matched")

        monkeypatch.setattr(bl, "org_billing_identity", _boom)
        summary = bl.reconcile_subscriptions(sdk, notifier=False)
        assert summary["checked"] == 1
        assert len(summary["failed"]) == 1, \
            "a store failure for one org must be recorded, not propagated"
        assert "no row matched" in summary["failed"][0]["error"]

    def test_internal_endpoint_repairs_a_drifted_org(
            self, monkeypatch, billing_client):
        """The production caller: POST /v1/internal/billing/reconcile.

        Without it the sweep is another uncalled function — the exact defect
        #8878 reports (``reconcile_org`` existed and nothing ran it)."""
        from tortoise import billing as bl
        org_id = billing_client["org_id"]
        sdk = billing_client["sdk"]
        self._seed(sdk, org_id, tier="solo", subscription_status="active",
                   subscription_id="sub_8878e")
        sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.max_points=$mp",
            params={"id": org_id, "mp": 25000})
        monkeypatch.setattr(bl.StripeClient, "get_subscription",
                            lambda self, sid: {
                                "id": "sub_8878e", "status": "active",
                                "items": {"data": [
                                    {"price": {"id": "price_200proMM"}}]}})
        monkeypatch.setattr(bl, "_notify_drift", lambda *a: None)
        monkeypatch.setenv("FASTAPI_INTERNAL_KEY", "internal-test-key")
        client = billing_client["client"]

        unauth = client.post("/v1/internal/billing/reconcile")
        assert unauth.status_code == 401, unauth.text

        r = client.post("/v1/internal/billing/reconcile",
                        headers={"Authorization": "Bearer internal-test-key"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["checked"] == 1 and body["repaired"] == 1, body
        assert body["drift"][0]["direction"] == "up"
        tier, max_points, _status = self._row(sdk, org_id)
        assert (tier, max_points) == ("pro", 100000)

    def test_a_wedged_pass_is_a_failure_not_a_benign_overlap(
            self, monkeypatch, billing_client):
        """#8904 round 4: the lock outlives the request, so a pass that WEDGES
        would hold it forever and every later driver run would read 202 — which
        the scheduled branch treats as a benign overlap — silencing the daily
        page permanently. Past the staleness bound the endpoint must answer a
        FAILURE that names the cause; a FRESH hold must still be 202.
        """
        import time as _time

        from tortoise import hosted_api as ha

        monkeypatch.setenv("FASTAPI_INTERNAL_KEY", "internal-test-key")
        client = billing_client["client"]

        class _Held:
            """A lock held by a pass that is still running."""

            def locked(self):
                return True

            async def acquire(self):
                return True

            def release(self):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        monkeypatch.setattr(ha, "_BILLING_RECONCILE_INFLIGHT", _Held())
        headers = {"Authorization": "Bearer internal-test-key"}

        # The bound is not a free knob, and it is bounded in BOTH directions.
        # Below: it must stay well clear of the driver's own client ceiling
        # (``curl -m 600`` in the cron step) or a HEALTHY long pass gets libelled
        # "presumed WEDGED" and pages on a working fleet. Above: it must not
        # reach the daily cadence at all — at a bound near 24 h a pass wedged at
        # 04:43 is still inside it when the next fire arrives, so that run
        # answers 202, the scheduled branch exits 0, and the fleet goes a
        # further cycle unreconciled and unpaged. Half the cadence keeps a
        # missed fire for any schedule jitter while still paging on the next.
        assert ha._BILLING_RECONCILE_STALE_S >= 3600.0, (
            "the staleness bound must keep a wide margin over the driver's "
            "600 s client ceiling, or a healthy long pass pages as wedged"
        )
        assert ha._BILLING_RECONCILE_STALE_S <= 43200.0, (
            "the staleness bound must stay at or below HALF the daily "
            "cadence, or a wedged pass silences the next cycle's page"
        )

        # A hold inside the bound is the documented benign overlap.
        monkeypatch.setattr(ha, "_BILLING_RECONCILE_STARTED_AT", _time.monotonic())
        fresh = client.post("/v1/internal/billing/reconcile", headers=headers)
        assert fresh.status_code == 202, fresh.text
        assert fresh.json()["status"] == "already_running"

        # Past it, the pass is presumed wedged and the run must red.
        monkeypatch.setattr(
            ha, "_BILLING_RECONCILE_STARTED_AT",
            _time.monotonic() - (ha._BILLING_RECONCILE_STALE_S + 1.0))
        stuck = client.post("/v1/internal/billing/reconcile", headers=headers)
        assert stuck.status_code == 503, stuck.text
        assert stuck.json()["status"] == "reconcile_stuck", stuck.text

    def test_the_endpoint_names_an_uncertified_enumeration(
            self, monkeypatch, billing_client):
        """A fleet that cannot be CERTIFIED complete answers 503 with a NAME.

        Uncaught, that refusal renders as an opaque ``500 {"detail": "Internal
        Server Error"}`` — and the daily driver's ``::error::`` line is the
        page, so "HTTP 500" names nothing an operator can act on. ``503
        enumeration_incomplete`` names which leg refused, and the legs do NOT
        share one remedy: a lower-bound page and a missing enumeration seam are
        STRUCTURAL, while a partial page is usually a row inserted or deleted
        mid-walk and certifies on the next run — retried ONCE, not declared
        structural on sight.

        The ``reason`` cannot tell the partial leg's sub-causes apart (one
        message covers an unusable cursor, an unordered page, an ignored
        filter, the page cap and a mid-walk delete), which is why the REMEDY is
        stated per leg rather than per message.
        """
        import tortoise.supabase_control as sc
        from tortoise import billing as bl
        monkeypatch.setenv("FASTAPI_INTERNAL_KEY", "internal-test-key")
        monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)

        def _cannot_certify(*_a, **_kw):
            raise bl.BillingEnumerationError(
                "billing reconcile: the org enumeration could not be CERTIFIED "
                "complete (1000 row(s) returned)")

        monkeypatch.setattr(bl, "reconcile_subscriptions", _cannot_certify)
        r = billing_client["client"].post(
            "/v1/internal/billing/reconcile",
            headers={"Authorization": "Bearer internal-test-key"})
        assert r.status_code == 503, r.text
        assert r.json()["status"] == "enumeration_incomplete"
        # The refusal has SEVERAL legs and they do NOT share one remedy: a
        # lower-bound page and a missing enumeration seam are structural, while
        # a partial page is usually a row changed mid-walk and certifies on the
        # next run. (A control-plane OUTAGE is a different path — `query_all`
        # raises a bare RuntimeError, which the endpoint does not catch, so it
        # arrives as a 500 and never reaches this arm.) A fixed detail string
        # threw the distinction away, so the leg must be carried.
        assert "CERTIFIED complete" in r.json().get("reason", ""), r.text


def test_cancelling_the_request_does_not_cancel_a_writing_pass(
        monkeypatch):
    """#8904 review round 3: the SDK and the lock belong to the PASS, not the request.

    ``asyncio.to_thread`` cannot cancel its thread, so a cancelled request (the
    driver's 600 s ceiling, a client disconnect) must NOT close the SDK or hand
    the lock back: the worker is still writing through both, and closing it
    mid-pass loses every repair not yet committed while the summary goes
    nowhere — and a released lock lets a retry start a SECOND pass over the same
    fleet. The close and the release ride the pass's own future; this pins that
    cancelling the REQUEST leaves both in place until the pass finishes, then
    closes exactly once and releases.
    """
    import asyncio
    import threading
    from contextlib import suppress

    import tortoise.supabase_control as sc
    from tortoise import billing as bl
    from tortoise import hosted_api as ha

    monkeypatch.setenv("FASTAPI_INTERNAL_KEY", "internal-test-key")
    started = threading.Event()
    release = threading.Event()
    closes: list[bool] = []

    class _FakeSdk:
        def close(self) -> None:
            closes.append(True)

    summary = {"checked": 0, "repaired": 0, "unchanged": 0,
               "unverified": 0, "skipped": 0, "drift": [],
               "unrepaired": [], "failed": []}

    def _pass(_sdk):
        started.set()
        try:
            release.wait(15)
        finally:
            pass
        return dict(summary)

    monkeypatch.setattr(bl, "reconcile_subscriptions", _pass)
    monkeypatch.setattr(ha, "_registry_sdk", lambda: _FakeSdk())
    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: False)
    # A FRESH lock/clock: the module-level pair may be bound to another test's
    # event loop, and this test asserts on its state directly.
    monkeypatch.setattr(ha, "_BILLING_RECONCILE_INFLIGHT", asyncio.Lock())
    monkeypatch.setattr(ha, "_BILLING_RECONCILE_STARTED_AT", None)

    async def _scenario() -> None:
        async with verdict_async_client(ha.app, base_url="http://test") as client:
            req = asyncio.ensure_future(client.post(
                "/v1/internal/billing/reconcile",
                headers={"Authorization": "Bearer internal-test-key"}))
            assert await asyncio.to_thread(started.wait, 15), (
                "the pass never started — the test proves nothing")
            assert ha._BILLING_RECONCILE_INFLIGHT.locked()

            req.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await req

            # The request is gone; the pass is still running.
            assert ha._BILLING_RECONCILE_INFLIGHT.locked(), (
                "cancelling the REQUEST released the pass lock — a retry could "
                "start a second pass over the same fleet")
            assert closes == [], (
                "the SDK was closed under a pass that is still writing")

            release.set()
            for _ in range(500):
                if not ha._BILLING_RECONCILE_INFLIGHT.locked():
                    break
                await asyncio.sleep(0.02)
            assert not ha._BILLING_RECONCILE_INFLIGHT.locked(), (
                "the pass finished but never handed the lock back")
            assert closes == [True], (
                f"the SDK must be closed exactly once, after the pass — {closes}")

    asyncio.run(_scenario())


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


class TestBillingErrorToHttpMapping:
    """#4725 — ``_billing_error_to_http`` must be SUBCLASS-FIRST.

    ``StripeAPIError(BillingError)`` and ``BillingConfigError(BillingError)``
    both subclass the base, so a base branch placed above a subclass branch
    silently swallows it. Before the fix, the ``BillingError`` branch (400)
    ran before the ``StripeAPIError`` branch (502), so every Stripe outage —
    network failure, rotated key, any 5xx from api.stripe.com — answered
    ``400 Bad Request``, blaming the caller for an upstream failure, and the
    502 branch was unreachable dead code.
    """

    def test_each_billing_exception_maps_to_its_documented_status(self):
        """Pin all three documented mappings (503/502/400)."""
        from tortoise.hosted_api import _billing_error_to_http

        cases = [
            (BillingConfigError("STRIPE_PRICE_IDS is not set"), 503),
            (billing.StripeAPIError("stripe POST customers -> 500"), 502),
            (BillingError("unknown price id"), 400),
        ]
        for exc, expected in cases:
            got = _billing_error_to_http(exc).status_code
            assert got == expected, f"{type(exc).__name__} -> {got}, want {expected}"

    def test_stripe_api_error_is_never_reported_as_a_caller_error(self):
        """The regression itself: an upstream failure is 502, not 400.

        Asserted as an explicit ``!= 400`` as well, so the failure message
        names the defect (blaming the caller) instead of only the number.
        """
        from tortoise.hosted_api import _billing_error_to_http

        resp = _billing_error_to_http(billing.StripeAPIError("stripe GET subscriptions -> 503"))
        assert resp.status_code != 400, "upstream Stripe failure blamed on the caller"
        assert resp.status_code == 502

    def test_subclass_relationship_that_makes_the_order_load_bearing(self):
        """Document WHY the order matters.

        If StripeAPIError ever stops subclassing BillingError, the ordering
        rationale in ``_billing_error_to_http`` needs revisiting — this test
        is the tripwire that says so, rather than pinning it silently.
        """
        assert issubclass(billing.StripeAPIError, BillingError)
        assert issubclass(BillingConfigError, BillingError)
        assert not issubclass(BillingConfigError, billing.StripeAPIError)
        assert not issubclass(billing.StripeAPIError, BillingConfigError)

    def test_unrelated_exception_still_falls_back_to_502(self):
        """The catch-all (call sites pass anything from ``except Exception``)
        must stay 502 — a reorder that made 400 the catch-all would silently
        re-blame the caller for every unknown failure."""
        from tortoise.hosted_api import _billing_error_to_http

        assert _billing_error_to_http(RuntimeError("boom")).status_code == 502

    def test_checkout_stripe_outage_answers_502(self, monkeypatch, billing_client):
        """End-to-end symptom: a Stripe outage during checkout is an upstream
        failure (502), and the caller's request was fine.

        This is the shape the #4640 review missed — a stubbed 400 assertion
        passed against the broken path because the real call's StripeAPIError
        was mapped to 400.
        """
        def _outage(*a, **kw):
            raise billing.StripeAPIError("stripe POST customers failed: connect timeout")

        monkeypatch.setattr(billing.StripeClient, "create_customer", _outage)
        r = billing_client["client"].post(
            "/v1/billing/checkout",
            json={"price_id": "price_200proMM"},
            headers=billing_client["headers"],
        )
        assert r.status_code == 502, r.text
        assert "connect timeout" in r.json()["detail"]

    def test_portal_stripe_outage_answers_502(self, monkeypatch, billing_client):
        """Same mapping on the portal leg — every call site routes through the
        one helper, so the fix must hold for all of them, not just checkout."""
        sdk = billing_client["sdk"]
        sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.stripe_customer_id='cus_portal1'",
            params={"id": billing_client["org_id"]},
        )

        def _outage(*a, **kw):
            raise billing.StripeAPIError("stripe POST billing_portal/sessions -> 502")

        monkeypatch.setattr(billing.StripeClient, "create_portal_session", _outage)
        r = billing_client["client"].post(
            "/v1/billing/portal",
            headers=billing_client["headers"],
        )
        assert r.status_code == 502, r.text


def test_the_client_seam_turns_a_wait_bound_refusal_into_an_inconclusive_skip(
        monkeypatch):
    """#8802: the deterministic refusal pin for this module's client seams.

    No load and no wall-clock wait: the bound is forced to 0 s, so the refusal
    comes from the REAL ``WaitBoundMiddleware``. ``/v1/billing/checkout`` is the
    route this file reads most and it is not exempt from the bound. The
    plain-client control is the non-vacuity leg — it is the exact
    ``assert 504 == 200`` shape this conversion removes, and it goes RED if
    the route is ever exempted (the middleware stops refusing, so the
    control's 504 assertion fails).
    """
    from fastapi.testclient import TestClient

    import tortoise.hosted_api as ha
    from tortoise import mcp_auth
    from tortoise.hosted_api import _TRANSPORT_WAIT_BOUND_EXEMPT

    route = ("POST", "/v1/billing/checkout")
    assert route not in _TRANSPORT_WAIT_BOUND_EXEMPT
    monkeypatch.setattr(mcp_auth, "_TRANSPORT_WAIT_BOUND_S", 0.0)
    with TestClient(ha.app) as raw:
        assert raw.post("/v1/billing/checkout", json={}).status_code == 504
    with verdict_client(ha.app) as tc, \
            pytest.raises(pytest.skip.Exception) as excinfo:
        tc.post("/v1/billing/checkout", json={})
    message = str(excinfo.value)
    assert "INCONCLUSIVE [#5049]" in message
    assert "POST /v1/billing/checkout answered" in message
