"""#2789 — the paid-new-org path ("purchase a subscription for a new organization").

Design 2 (the issue's option 2, chosen because it leaves NO user-visible
half-created org): the org does not exist until `checkout.session.completed`
provisions it. Nothing is written at checkout time — no team row, no
membership, no graph, no Stripe customer — so an abandoned checkout leaves
literally nothing behind and does not consume the one-free-org allowance.

Covers:
- POST /v1/billing/checkout/new-org validation + the zero-write guarantee;
- the webhook provisioning path (happy, replay-idempotent, retry-after-failure,
  name-collision, metadata-tier fallback);
- the abandoned-checkout / entitlement-invariance assertions.

Supabase lane only (FakeControlPlane + a temp embedded graph, mirroring
tests/test_free_team_entitlement.py); the entitlement matrix across BOTH lanes
lives in that file.
"""
from __future__ import annotations

import hashlib
import hmac as hmac_mod
import json
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from tests.fake_control_plane import FakeControlPlane
from tests.test_billing import FIXTURE_SUB, VALID_CATALOG
from tests.test_supabase_control import FREE_TEAM, _membership_row
from tortoise.hosted_api import app, get_current_user

_U1 = "9f2c1a40-0000-4a00-8000-000000000001"
_PRO_PRICE = "price_200proMM"
_FREE_PRICE = "price_000freeM"
_WEBHOOK_SECRET = "whsec_test"


# ── fixtures (mirror tests/test_free_team_entitlement.py) ───────────────────


@pytest.fixture
def fake() -> FakeControlPlane:
    return FakeControlPlane({
        "teams": [dict(FREE_TEAM)],
        "team_memberships": [],
        "api_keys": [],
        "webhook_events": [],
    })


@pytest.fixture
def supabase_env(monkeypatch, fake) -> FakeControlPlane:
    import tortoise.supabase_control as sc
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc_role_key_test")
    monkeypatch.setattr(sc, "get_control_plane", lambda: fake)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_123")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", _WEBHOOK_SECRET)
    monkeypatch.setenv("STRIPE_PRICE_IDS", json.dumps(VALID_CATALOG))
    monkeypatch.setenv("RATE_LIMIT_DISABLED", "1")
    return fake


def _close_keepalive_anchors(module) -> None:
    """Deterministic keepalive-anchor close (mirrors test_hosted_api.py)."""
    for ns in list(module._FALLBACK_KEEPALIVE):
        anchor = module._FALLBACK_KEEPALIVE.pop(ns, None)
        if anchor is not None:
            try:  # noqa: SIM105
                anchor.close()
            except Exception:
                pass


@pytest.fixture
def client(monkeypatch, supabase_env):
    """TestClient in Supabase mode with a temp embedded graph store."""
    import tortoise.hosted_api as ha_mod
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "neworg.db")
        _orig = ha_mod.TortoiseSDK.__init__

        def _patched(self, db_path_arg=None, *, namespace=None, **kwargs):
            _orig(self, db_path, namespace=namespace)

        ha_mod.TortoiseSDK.__init__ = _patched
        ha_mod._FALLBACK_KEEPALIVE.clear()
        # #1950: pin TORTOISE_DB_PATH so _make_sdk's keepalive anchor path
        # matches (the anchor is reused, never evicted mid-test).
        os.environ["TORTOISE_DB_PATH"] = db_path
        try:
            with TestClient(app) as tc:
                yield tc, supabase_env
        finally:
            os.environ.pop("TORTOISE_DB_PATH", None)
            ha_mod.TortoiseSDK.__init__ = _orig
            _close_keepalive_anchors(ha_mod)
            app.dependency_overrides.clear()


@pytest.fixture
def user_client(client):
    tc, fake = client
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": _U1, "email": "owner@example.com"}
    return tc, fake


def _seed_team(fake, team_id: str, tier: str = "free",
               subscription_status=None) -> None:
    fake.seed("teams", [dict(FREE_TEAM, id=team_id, name=team_id, tier=tier,
                             subscription_status=subscription_status)])


def _seed_membership(fake, team_id: str, role: str = "owner") -> None:
    fake.seed("team_memberships", [
        _membership_row(user_id=_U1, team_id=team_id, role=role)])


def _checkout_event(team_id: str, *, org_name: str = "Second Org",
                    tier: str = "pro", event_id: str = "evt_new_org_1",
                    user_id: str = _U1, subscription: str | None = "sub_1",
                    metadata_extra: dict | None = None) -> dict:
    meta = {"new_org": "1", "team_id": team_id, "user_id": user_id,
            "org_name": org_name, "tier": tier}
    meta.update(metadata_extra or {})
    return {
        "id": event_id,
        "type": "checkout.session.completed",
        "data": {"object": {
            "client_reference_id": team_id,
            "customer": "cus_new_1",
            "customer_details": {"email": "owner@example.com"},
            "subscription": subscription,
            "metadata": meta,
        }},
    }


def _post_signed(tc, event: dict):
    """POST a genuinely HMAC-signed webhook (the route verifies the raw body —
    a monkeypatched verifier would leave the real signature path untested).
    The timestamp must be current: verification enforces a ±300s tolerance."""
    import time as _time
    raw = json.dumps(event).encode()
    ts = str(int(_time.time()))
    sig = hmac_mod.new(
        _WEBHOOK_SECRET.encode(), f"{ts}.".encode() + raw,
        hashlib.sha256).hexdigest()
    return tc.post("/webhooks/stripe", content=raw,
                   headers={"stripe-signature": f"t={ts},v1={sig}"})


# ── POST /v1/billing/checkout/new-org ───────────────────────────────────────


class TestNewOrgCheckout:
    def test_capped_owner_can_purchase_paid_new_org(self, monkeypatch, user_client):
        """Rules table: an owner at the free cap REQUESTS A PAID org → allowed."""
        tc, fake = user_client
        _seed_team(fake, "team-free-a")
        _seed_membership(fake, "team-free-a")
        seen: dict = {}

        def _fake_session(self, **kwargs):
            seen.update(kwargs)
            return "cs_test_1", "https://checkout.stripe.com/cs_test_1"

        import tortoise.billing as billing
        monkeypatch.setattr(billing.StripeClient,
                            "create_checkout_session_for_new_org", _fake_session)
        r = tc.post("/v1/billing/checkout/new-org",
                    json={"name": "Second Org", "price_id": _PRO_PRICE})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["checkout_url"] == "https://checkout.stripe.com/cs_test_1"
        tid = body["team_id"]
        # the pre-minted id binds the session — the webhook provisions from it
        assert seen["new_org_id"] == tid
        assert len(tid) == 26
        assert seen["user_id"] == _U1
        assert seen["org_name"] == "Second Org"
        assert seen["tier"] == "pro"
        assert seen["email"] == "owner@example.com"
        # NOTHING is written server-side (design 2): no team, no membership.
        assert not [t for t in fake.query("teams") if t.get("id") == tid]
        assert not [m for m in fake.query("team_memberships")
                    if m.get("team_id") == tid]

    def test_success_url_carries_the_pre_minted_org(self, monkeypatch, user_client):
        """The returning tab switches to ?new_org=<id> once the webhook lands;
        the env template (session id placeholder) must survive verbatim."""
        tc, _fake = user_client
        monkeypatch.setenv(
            "BILLING_SUCCESS_URL",
            "https://app.example.com/team?session_id={CHECKOUT_SESSION_ID}")
        seen: dict = {}
        import tortoise.billing as billing
        monkeypatch.setattr(
            billing.StripeClient, "create_checkout_session_for_new_org",
            lambda self, **kw: (seen.update(kw),
                                ("cs_x", "https://checkout.stripe.com/x"))[1])
        r = tc.post("/v1/billing/checkout/new-org",
                    json={"name": "Second Org", "price_id": _PRO_PRICE})
        assert r.status_code == 200, r.text
        assert seen["success_url"] == (
            "https://app.example.com/team?session_id={CHECKOUT_SESSION_ID}"
            f"&new_org={r.json()['team_id']}")

    def test_duplicate_name_409_before_any_stripe_call(self, monkeypatch, user_client):
        """teams.name is globally unique (0011) — a collision must fail BEFORE
        money moves (a webhook-time collision would strand a paying customer)."""
        tc, fake = user_client
        fake.seed("teams", [dict(FREE_TEAM, id="t-taken", name="Acme")])
        calls: list = []
        import tortoise.billing as billing
        monkeypatch.setattr(
            billing.StripeClient, "create_checkout_session_for_new_org",
            lambda self, **kw: calls.append(kw) or ("cs_x", "https://x"))
        r = tc.post("/v1/billing/checkout/new-org",
                    json={"name": "Acme", "price_id": _PRO_PRICE})
        assert r.status_code == 409, r.text
        assert calls == [], "no Stripe session may be created for a taken name"

    def test_free_price_rejected_400(self, user_client):
        tc, _fake = user_client
        r = tc.post("/v1/billing/checkout/new-org",
                    json={"name": "Second Org", "price_id": _FREE_PRICE})
        assert r.status_code == 400, r.text
        assert "paid plan" in r.json()["detail"]

    def test_unknown_price_400(self, user_client):
        tc, _fake = user_client
        r = tc.post("/v1/billing/checkout/new-org",
                    json={"name": "Second Org", "price_id": "price_nope"})
        assert r.status_code == 400, r.text

    def test_invalid_name_422(self, user_client):
        tc, _fake = user_client
        r = tc.post("/v1/billing/checkout/new-org",
                    json={"name": "bad@name!", "price_id": _PRO_PRICE})
        assert r.status_code == 422, r.text

    def test_unconfigured_catalog_503(self, monkeypatch, user_client):
        tc, _fake = user_client
        monkeypatch.delenv("STRIPE_PRICE_IDS", raising=False)
        r = tc.post("/v1/billing/checkout/new-org",
                    json={"name": "Second Org", "price_id": _PRO_PRICE})
        assert r.status_code == 503, r.text


# ── webhook provisioning ────────────────────────────────────────────────────


def _team_rows(fake, team_id):
    return [t for t in fake.query("teams") if t.get("id") == team_id]


def _membership_rows(fake, team_id):
    return [m for m in fake.query("team_memberships")
            if m.get("team_id") == team_id]


def _team_meta_count(team_id: str) -> int:
    from tortoise.hosted_api import _make_sdk
    graph = _make_sdk(namespace=team_id)._get_proj().db.select_graph(
        f"team_{team_id}")
    return graph.query("MATCH (m:TeamMeta) RETURN count(m)").result_set[0][0]


class TestWebhookProvisioning:
    def test_completed_checkout_provisions_a_working_paid_org(
            self, monkeypatch, user_client):
        tc, fake = user_client
        import tortoise.billing as billing
        monkeypatch.setattr(billing.StripeClient, "get_subscription",
                            lambda self, sid: FIXTURE_SUB)
        _seed_team(fake, "team-free-a")
        _seed_membership(fake, "team-free-a")
        tid = "t" * 26
        event = _checkout_event(tid, org_name="Second Org")

        r = _post_signed(tc, event)
        assert r.status_code == 200, r.text

        rows = _team_rows(fake, tid)
        assert len(rows) == 1, rows
        t = rows[0]
        assert t["name"] == "Second Org"
        assert t["tier"] == "pro"
        assert t["subscription_status"] == "active"
        assert t["stripe_customer_id"] == "cus_new_1"
        assert t["subscription_id"] == "sub_1"
        assert t["graph_name"] == f"team_{tid}"
        from tortoise.pricing import tier_limits
        assert t["max_graphs"] == tier_limits("pro")["max_graphs_per_team"]
        # exactly one OWNER membership — the org is enterable by its buyer
        mems = _membership_rows(fake, tid)
        assert len(mems) == 1
        assert mems[0]["role"] == "owner" and mems[0]["status"] == "active"
        # keyless by policy (#1716/#1921): the org mints keys via
        # POST /v1/session/key, never a dead credential at provision time
        assert not [k for k in fake.query("api_keys") if k.get("team_id") == tid]
        # the graph is real (switcher/graph_list/key-scope all resolve it)
        assert _team_meta_count(tid) == 1
        # a PAID org does not consume the free-org allowance: the buyer can
        # still create their one free org later
        from tortoise.supabase_control import owned_free_org_ids
        assert owned_free_org_ids(fake, _U1) == ["team-free-a"]

    def test_replay_of_the_same_event_is_idempotent(self, monkeypatch, user_client):
        tc, fake = user_client
        import tortoise.billing as billing
        monkeypatch.setattr(billing.StripeClient, "get_subscription",
                            lambda self, sid: FIXTURE_SUB)
        tid = "r" * 26
        event = _checkout_event(tid, event_id="evt_replay_1")
        for _ in range(2):
            assert _post_signed(tc, event).status_code == 200
        assert len(_team_rows(fake, tid)) == 1
        assert len(_membership_rows(fake, tid)) == 1
        assert len([e for e in fake.query("webhook_events")
                    if e.get("event_id") == "evt_replay_1"]) == 1
        assert _team_meta_count(tid) == 1, "no duplicate TeamMeta on replay"

    def test_retry_after_a_failed_provision_converges(self, monkeypatch, user_client):
        """Stripe retries the SAME event id after a 500. The org row is still
        absent on the retry, so the eager graph init re-runs — it must not mint
        a second TeamMeta (the idempotency guard)."""
        tc, fake = user_client
        import tortoise.billing as billing
        import tortoise.supabase_control as sc
        monkeypatch.setattr(billing.StripeClient, "get_subscription",
                            lambda self, sid: FIXTURE_SUB)
        real = sc.provision_team
        state = {"n": 0}

        def _flaky(cp, **kwargs):
            state["n"] += 1
            if state["n"] == 1:
                raise RuntimeError("control plane down")
            return real(cp, **kwargs)

        monkeypatch.setattr(sc, "provision_team", _flaky)
        tid = "f" * 26
        event = _checkout_event(tid, event_id="evt_flaky_1")
        assert _post_signed(tc, event).status_code == 500
        assert _team_rows(fake, tid) == []
        # Stripe retries with the same event id
        assert _post_signed(tc, event).status_code == 200
        assert len(_team_rows(fake, tid)) == 1
        assert _team_meta_count(tid) == 1

    def test_metadata_tier_carries_when_subscription_fetch_fails(
            self, monkeypatch, user_client):
        """A paying customer must never be left on free limits by a flaky
        Stripe read — the tier was server-resolved at checkout time."""
        tc, fake = user_client
        import tortoise.billing as billing
        monkeypatch.setattr(billing.StripeClient, "get_subscription",
                            lambda self, sid: (_ for _ in ()).throw(
                                RuntimeError("stripe down")))
        tid = "m" * 26
        assert _post_signed(tc, _checkout_event(tid)).status_code == 200
        t = _team_rows(fake, tid)[0]
        assert t["tier"] == "pro"
        assert t["subscription_status"] == "active"
        from tortoise.pricing import tier_limits
        assert t["max_users"] == tier_limits("pro")["max_users_per_team"]

    def test_name_taken_at_payment_disambiguates(self, monkeypatch, user_client):
        """The checkout-time pre-check cannot see a name created while the
        customer was paying — provision deterministically instead of leaving a
        paying customer with no org."""
        tc, fake = user_client
        import tortoise.billing as billing
        monkeypatch.setattr(billing.StripeClient, "get_subscription",
                            lambda self, sid: FIXTURE_SUB)
        fake.seed("teams", [dict(FREE_TEAM, id="t-taken", name="Contested")])
        tid = "c" * 26
        assert _post_signed(
            tc, _checkout_event(tid, org_name="Contested")).status_code == 200
        t = _team_rows(fake, tid)[0]
        assert t["name"] == f"Contested ({tid[:4]})"
        assert t["tier"] == "pro"

    def test_abandoned_checkout_leaves_nothing_and_does_not_consume_allowance(
            self, monkeypatch, user_client):
        """The acceptance criterion that motivated design 2: start a checkout,
        never complete it → no org anywhere, and the free-org allowance is
        untouched (the user is still at, not over, the cap)."""
        tc, fake = user_client
        _seed_team(fake, "team-free-a")
        _seed_membership(fake, "team-free-a")
        import tortoise.billing as billing
        monkeypatch.setattr(
            billing.StripeClient, "create_checkout_session_for_new_org",
            lambda self, **kw: ("cs_abandoned", "https://checkout.stripe.com/ab"))
        before_teams = len(fake.query("teams"))
        r = tc.post("/v1/billing/checkout/new-org",
                    json={"name": "Never Bought", "price_id": _PRO_PRICE})
        assert r.status_code == 200, r.text
        tid = r.json()["team_id"]
        # no webhook arrived → nothing exists
        assert len(fake.query("teams")) == before_teams
        assert _team_rows(fake, tid) == []
        assert _membership_rows(fake, tid) == []
        assert not [k for k in fake.query("api_keys") if k.get("team_id") == tid]
        # the allowance is unchanged: still exactly the one owned free org
        from tortoise.supabase_control import owned_free_org_ids
        assert owned_free_org_ids(fake, _U1) == ["team-free-a"]
        # and the free-org gate still behaves exactly as before the attempt
        assert tc.post("/v1/teams", json={"name": "nope"}).status_code == 402

    def test_missing_metadata_is_loud_and_retryable(self, monkeypatch, user_client):
        """A can't-happen event (no org_name) must 500 so Stripe retries —
        never silently provision a nameless org."""
        tc, fake = user_client
        tid = "z" * 26
        event = _checkout_event(tid, metadata_extra={"org_name": ""})
        assert _post_signed(tc, event).status_code == 500
        assert _team_rows(fake, tid) == []


class TestEagerGraphIdempotency:
    def test_eager_provision_org_graph_never_duplicates_team_meta(self, client):
        """#2789 review P1: the pre-#2789 eager init was a bare CREATE — a
        retry (Stripe redelivery after a failed RPC) would have left a second
        TeamMeta node in the same tenant graph."""
        from tortoise.hosted_api import _eager_provision_org_graph
        _tc, fake = client
        tid = "g" * 26
        assert _eager_provision_org_graph(fake, tid, "First", _U1) == f"team_{tid}"
        assert _eager_provision_org_graph(fake, tid, "Second", _U1) == f"team_{tid}"
        assert _team_meta_count(tid) == 1
