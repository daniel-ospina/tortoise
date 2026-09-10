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
            if namespace:
                _SDK_BY_NS[namespace] = self

        ha_mod.TortoiseSDK.__init__ = _patched
        ha_mod._FALLBACK_KEEPALIVE.clear()
        _SDK_BY_NS.clear()
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
        # The intended name rides along too: the pre-minted id is NOT the org's
        # real id on the registry (selfhost) lane, so the returning tab matches
        # on either.
        assert seen["success_url"] == (
            "https://app.example.com/team?session_id={CHECKOUT_SESSION_ID}"
            f"&new_org={r.json()['team_id']}&new_org_name=Second%20Org")

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

    def test_requires_a_session(self, monkeypatch, client):
        """#2789 (code-review): the only billing route that does NOT hang off
        team auth is this one — if a refactor ever dropped `get_current_user`
        (or a future SKIP_AUTH list gained the path), anyone could mint Stripe
        sessions and provision PAID orgs owned by the JWT subject."""
        tc, _fake = client   # `client`, not `user_client` → no dependency override
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_123")
        r = tc.post("/v1/billing/checkout/new-org",
                    json={"name": "Second Org", "price_id": _PRO_PRICE})
        assert r.status_code == 401, r.text

    def test_a_spoofed_user_id_in_the_body_is_ignored(self, monkeypatch, user_client):
        """The org owner is the JWT subject — never a caller-supplied field."""
        tc, _fake = user_client
        seen: dict = {}
        import tortoise.billing as billing
        monkeypatch.setattr(
            billing.StripeClient, "create_checkout_session_for_new_org",
            lambda self, **kw: (seen.update(kw), ("cs_x", "https://x"))[1])
        r = tc.post("/v1/billing/checkout/new-org",
                    json={"name": "Second Org", "price_id": _PRO_PRICE,
                          "user_id": "00000000-0000-4000-8000-00000000dead"})
        assert r.status_code == 200, r.text
        assert seen["user_id"] == _U1, "the owner must come from the JWT"


# ── webhook provisioning ────────────────────────────────────────────────────


def _team_rows(fake, team_id):
    return [t for t in fake.query("teams") if t.get("id") == team_id]


def _membership_rows(fake, team_id):
    return [m for m in fake.query("team_memberships")
            if m.get("team_id") == team_id]


# The SDK instance each namespace was last built with (populated by the
# `client` fixture's patched __init__). Reads must go through the SAME instance
# the app wrote with: a second instance on the same embedded (redislite) file
# can attach to a different server and see a stale snapshot — a bare fresh-read
# assertion here flaked ~1 full-file run in 4.
_SDK_BY_NS: dict[str, object] = {}


def _team_meta_count(team_id: str) -> int:
    """Count the org graph's TeamMeta nodes, through the app's own connection."""
    sdk = _SDK_BY_NS.get(team_id)
    if sdk is None:
        from tortoise.hosted_api import _make_sdk
        sdk = _make_sdk(namespace=team_id)
    graph = sdk._get_proj().db.select_graph(f"team_{team_id}")
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
        # …and it belongs to the BUYER (the JWT subject). Without this the org
        # could be provisioned for the wrong tenant and every other assertion
        # here would still pass (the fake writes whatever p_user_id it gets).
        assert mems[0]["user_id"] == _U1
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
        # The suffix must be a name BOTH lanes accept (the registry SDK rejects
        # parentheses) — see TestCollisionNameRule.
        from tortoise.hosted_api import _new_org_collision_name
        assert t["name"] == _new_org_collision_name("Contested", tid)
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

    def test_unresolvable_paid_tier_is_not_acked(self, monkeypatch, user_client):
        """#2789 (code-review): a paying customer must never be ACKed (200) onto
        free limits. If the subscription resolves no tier AND the metadata
        carries no paid tier, the event must 500 so Stripe redelivers (and ops
        can fix the session) — a 200 would mean nothing ever retries."""
        tc, fake = user_client
        import tortoise.billing as billing
        # a subscription response with no price → no resolvable tier
        monkeypatch.setattr(billing.StripeClient, "get_subscription",
                            lambda self, sid: {"id": sid, "items": {"data": []}})
        tid = "u" * 26
        event = _checkout_event(tid, tier="free", event_id="evt_no_tier")
        assert _post_signed(tc, event).status_code == 500
        # and the marker was NOT written, so the redelivery is really re-run
        # (SET-then-marker: the marker comes after a successful apply)
        assert not [e for e in fake.query("webhook_events")
                    if e.get("event_id") == "evt_no_tier"]

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


# ── the REAL Stripe payload (not monkeypatched away) ────────────────────────


class TestNewOrgStripePayload:
    """The checkout-request parameters ARE the money path: `customer_email`
    (no customer) is what makes an abandoned checkout leave nothing, and
    `metadata[new_org|team_id|...]` is what makes the webhook provision at all.
    A code-review pass found these were only exercised through a monkeypatched
    `create_checkout_session_for_new_org` — so dropping a metadata key would
    have shipped green while the webhook silently `_set` billing on a team
    that does not exist (update_team_billing matches 0 rows)."""

    def test_form_encoded_params(self, monkeypatch):
        import tortoise.billing as billing
        from tests.test_billing import _FakeHttpxClient, _last_http_requests
        monkeypatch.setattr("httpx.Client", _FakeHttpxClient)
        org_id = "n" * 26
        client = billing.StripeClient(secret_key="sk_test_123")
        _sid, url = client.create_checkout_session_for_new_org(
            new_org_id=org_id, price_id=_PRO_PRICE, email="owner@example.com",
            user_id=_U1, org_name="Second Org", tier="pro",
            success_url=f"https://app/team?session_id={{CHECKOUT_SESSION_ID}}&new_org={org_id}",
            cancel_url="https://app/team?checkout=cancelled")
        assert url == "https://checkout.stripe.com/pay/abc"
        method, api_url, params = _last_http_requests()[0]
        assert method == "POST" and api_url.endswith("/checkout/sessions")
        assert params["mode"] == "subscription"
        assert params["line_items[0][price]"] == _PRO_PRICE
        assert params["line_items[0][quantity]"] == "1"
        # NO Stripe customer object — Stripe creates one at completion, so an
        # abandoned checkout leaves not even an orphaned customer.
        assert "customer" not in params
        assert params["customer_email"] == "owner@example.com"
        assert params["client_reference_id"] == org_id
        assert params["metadata[new_org]"] == "1"
        assert params["metadata[team_id]"] == org_id
        assert params["metadata[user_id]"] == _U1
        assert params["metadata[org_name]"] == "Second Org"
        assert params["metadata[tier]"] == "pro"

    def test_no_empty_customer_email(self, monkeypatch):
        import tortoise.billing as billing
        from tests.test_billing import _FakeHttpxClient, _last_http_requests
        monkeypatch.setattr("httpx.Client", _FakeHttpxClient)
        client = billing.StripeClient(secret_key="sk_test_123")
        client.create_checkout_session_for_new_org(
            new_org_id="n" * 26, price_id=_PRO_PRICE, email="",
            user_id=_U1, org_name="Second Org", tier="pro",
            success_url="https://app/team", cancel_url="https://app/cancel")
        params = _last_http_requests()[0][2]
        assert "customer_email" not in params, "Stripe rejects an empty customer_email"


# ── the collision-disambiguation name rule ─────────────────────────────────


class TestCollisionNameRule:
    def test_legal_in_both_lanes_at_every_length(self):
        """The name must satisfy the STRICTEST lane: sdk.team_create's
        `^[a-zA-Z0-9][a-zA-Z0-9_ -]*$` and <= 64 chars (the registry SDK
        rejects parentheses, which the first cut used — the retry could never
        succeed and a paying customer was stranded on a 500 loop)."""
        import re as _re

        from tortoise.hosted_api import _new_org_collision_name
        for name in ["A", "Second Org", "Acme Inc", "x" * 57, "y" * 64,
                     "N" * 63 + " Z"]:
            alt = _new_org_collision_name(name, "0123456789abcdef")
            assert len(alt) <= 64, (name, alt)
            assert _re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_ -]*$", alt), (name, alt)
            assert "(" not in alt and ")" not in alt, alt
        # deterministic — a Stripe redelivery must not compute a different name
        assert (_new_org_collision_name("Second Org", "abcdef1234")
                == _new_org_collision_name("Second Org", "abcdef1234"))


# ── registry (selfhost) lane ───────────────────────────────────────────────


@pytest.fixture
def registry_client(monkeypatch):
    """TestClient in REGISTRY mode on a temp embedded DB.

    The registry lane has a distinct paid-new-org contract — `team_create`
    mints its OWN id and the webhook must adopt the effective id for every
    billing write/marker — which Supabase-mode tests cannot reach.
    """
    import tortoise.hosted_api as ha_mod
    for var in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY",
                "SUPABASE_SERVICE_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_123")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", _WEBHOOK_SECRET)
    monkeypatch.setenv("STRIPE_PRICE_IDS", json.dumps(VALID_CATALOG))
    monkeypatch.setenv("RATE_LIMIT_DISABLED", "1")
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "registry-neworg.db")
        _orig = ha_mod.TortoiseSDK.__init__

        def _patched(self, db_path_arg=None, *, namespace=None, **kwargs):
            _orig(self, db_path, namespace=namespace)

        ha_mod.TortoiseSDK.__init__ = _patched
        ha_mod._FALLBACK_KEEPALIVE.clear()
        os.environ["TORTOISE_DB_PATH"] = db_path
        try:
            with TestClient(app) as tc:
                app.dependency_overrides[get_current_user] = lambda: {
                    "user_id": _U1, "email": "owner@example.com"}
                yield tc
        finally:
            os.environ.pop("TORTOISE_DB_PATH", None)
            ha_mod.TortoiseSDK.__init__ = _orig
            _close_keepalive_anchors(ha_mod)
            app.dependency_overrides.clear()


def _registry():
    from tortoise.hosted_api import _make_sdk
    return _make_sdk(namespace="registry")._get_registry()


def _reg_team_by_name(name: str):
    return _registry().query(
        "MATCH (t:Team {name:$n}) RETURN t.id, t.tier, t.idempotency_key",
        params={"n": name}).result_set


class TestRegistryLaneWebhookProvisioning:
    def test_provisions_and_binds_billing_to_the_effective_id(
            self, monkeypatch, registry_client):
        import tortoise.billing as billing
        from tortoise.pricing import tier_limits
        monkeypatch.setattr(billing.StripeClient, "get_subscription",
                            lambda self, sid: FIXTURE_SUB)
        pre = "p" * 26
        r = _post_signed(registry_client,
                         _checkout_event(pre, org_name="Second Org",
                                         event_id="evt_reg_1"))
        assert r.status_code == 200, r.text
        rows = _reg_team_by_name("Second Org")
        assert len(rows) == 1, rows
        eff, tier, idem = rows[0]
        # the lane mints its own id; the pre-minted one is only the replay key
        assert eff and eff != pre
        assert idem == pre
        # and the EFFECTIVE id carries the paid tier + limits (the whole point
        # of returning it from _webhook_apply_event)
        assert tier == "pro"
        lims = _registry().query(
            "MATCH (t:Team {id:$i}) RETURN t.max_graphs", params={"i": eff},
        ).result_set[0][0]
        assert lims == tier_limits("pro")["max_graphs_per_team"]
        # the buyer owns the org that actually exists
        mems = _registry().query(
            "MATCH (m:Membership {team_id:$t}) RETURN m.user_id, m.role, m.status",
            params={"t": eff}).result_set
        assert [list(m) for m in mems] == [[_U1, "owner", "active"]], mems
        # nothing was minted at the pre-minted id — the divergence the client's
        # success-URL matching has to tolerate
        assert _registry().query(
            "MATCH (t:Team {id:$i}) RETURN count(t)",
            params={"i": pre}).result_set[0][0] == 0

    def test_replay_provisions_exactly_once(self, monkeypatch, registry_client):
        import tortoise.billing as billing
        monkeypatch.setattr(billing.StripeClient, "get_subscription",
                            lambda self, sid: FIXTURE_SUB)
        pre = "q" * 26
        event = _checkout_event(pre, org_name="Replay Org",
                                event_id="evt_reg_replay")
        for _ in range(2):
            assert _post_signed(registry_client, event).status_code == 200
        assert len(_reg_team_by_name("Replay Org")) == 1
        assert _registry().query(
            "MATCH (m:Membership {user_id:$u}) RETURN count(m)",
            params={"u": _U1}).result_set[0][0] == 1
        assert _registry().query(
            "MATCH (w:WebhookEvent {event_id:$e}) RETURN count(w)",
            params={"e": "evt_reg_replay"}).result_set[0][0] == 1

    def test_name_collision_retry_succeeds_with_a_legal_name(
            self, monkeypatch, registry_client):
        """The pre-check cannot see a name taken between checkout and payment.
        The retry MUST use a name the SAME lane accepts (no parentheses, <=64) —
        otherwise it raises inside the except, 500s forever, and the paying
        customer never gets an org."""
        import re as _re

        import tortoise.billing as billing
        monkeypatch.setattr(billing.StripeClient, "get_subscription",
                            lambda self, sid: FIXTURE_SUB)
        # someone else took the name in the meantime
        _make = __import__("tortoise.hosted_api", fromlist=["_make_sdk"])
        _make._make_sdk(namespace="registry").team_create("Second Org")
        pre = "c" * 26
        r = _post_signed(registry_client,
                         _checkout_event(pre, org_name="Second Org",
                                         event_id="evt_reg_collide"))
        assert r.status_code == 200, r.text
        names = [n for (n,) in _registry().query(
            "MATCH (t:Team) RETURN t.name").result_set]
        assert names.count("Second Org") == 1, names  # the squatter's
        alt = [n for n in names if n.startswith("Second Org") and n != "Second Org"]
        assert len(alt) == 1, names
        assert _re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_ -]*$", alt[0]), alt
        assert len(alt[0]) <= 64, alt
