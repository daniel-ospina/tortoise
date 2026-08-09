"""Stripe billing mirror for Tortoise Hosted (#310).

Architecture: Stripe is the authority for *money*; the FalkorDB registry
Team node is the authority for *enforcement*. This module provides the thin
Stripe API wrapper (``StripeClient`` over httpx — form-encoded, timeouts), the
validated price catalog (``PriceCatalog`` from the ``STRIPE_PRICE_IDS`` env
JSON), lazy-grace tier resolution (``effective_tier``), the atomic tier+limits
writer (``apply_limits``), and boot mirror repair (``reconcile_team``).

Config degradation is LAZY by design (review fix 12): missing
``STRIPE_PRICE_IDS`` / ``STRIPE_SECRET_KEY`` / ``STRIPE_WEBHOOK_SECRET`` never
fails at import/boot — ``PriceCatalog()`` and ``StripeClient()`` raise a
catchable ``BillingConfigError`` at first use (billing endpoints answer 503;
boot/lifespan is unaffected).

GAP-B mapping decision (Task 1 Step 4, plan MAIN-DELTA 2): ``max_points``
mirrors ``max_graph_nodes`` from pricing.json (10k/25k/100k/600k) because the
``points`` quota counter counts GRAPH NODES (``MATCH (n) RETURN count(n)``),
not write ops. The scoping doc's 10k/10k/50k/200k values match
``included_write_ops_per_month`` exactly — write-ops numbers, not the node
count the quota enforces. Write-ops-based caps remain a metering prerequisite
(#296/#308).

No third-party deps beyond httpx (already an in-file dependency of
hosted_api.py, pinned in requirements.txt by Task 3).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time

__all__ = [
    "BillingError", "BillingConfigError", "StripeAPIError",
    "PriceCatalog", "StripeClient",
    "effective_tier", "apply_limits", "subscription_plan",
    "mirror_subscription", "reconcile_team",
]

_STRIPE_API = "https://api.stripe.com/v1"
_ACTIVE_STATUSES = ("active", "trialing", "past_due")
_MAX_SESSIONS = 1000  # flat across tiers (matches today's effective default)


class BillingError(Exception):
    """Base billing error — the caller decides how to surface it."""


class BillingConfigError(BillingError):
    """Missing/invalid billing configuration — raised lazily at first use."""


class StripeAPIError(BillingError):
    """A Stripe API call failed (network, auth, 4xx/5xx)."""


# ── Secret scrubbing (log hygiene, review fix 9) ────────────────────────────

_SECRET_PATTERNS = [
    re.compile(r"(sk|rk)_(?:live|test)_[A-Za-z0-9]+"),
    re.compile(r"whsec_[A-Za-z0-9]+"),
    re.compile(r"re_[A-Za-z0-9]{10,}"),
]


def _scrub_secrets(text: str) -> str:
    """Redact obvious secret values (Stripe keys, webhook secrets) from text."""
    out = text
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub("***", out)
    return out[:200]


# ── Price catalog ───────────────────────────────────────────────────────────

class PriceCatalog:
    """Validated catalog of the 8 Stripe price ids (4 tiers × monthly/annual).

    ``STRIPE_PRICE_IDS`` env JSON shape (8 ids):

    .. code-block:: json

        {
          "free": {"monthly": {"id": "price_...", "amount_usd": 0},
                   "annual": {"id": "price_...", "amount_usd": 0}},
          "solo": {"monthly": {"id": "price_...", "amount_usd": 900},
                   "annual": {"id": "price_...", "amount_usd": 8640}},
          "pro":  {"monthly": {"id": "price_...", "amount_usd": 2500},
                   "annual": {"id": "price_...", "amount_usd": 24000}},
          "team": {"monthly": {"id": "price_...", "amount_usd": 14900},
                   "annual": {"id": "price_...", "amount_usd": 143040}}
        }

    Entries may be plain id strings (``"price_..."``) — then the numeric
    annual-discount check is skipped for that tier (amounts unknown). When
    ``amount_usd`` is present (USD cents), the annual discount is validated
    against pricing.json's ``display.annual_discount_pct`` (20%).

    Validation rejects: unknown tier names, a tier missing monthly or annual,
    non-``price_`` ids, and a wrong annual discount (when amounts are given).
    """

    def __init__(self, raw: str | None = None):
        if raw is None:
            raw = os.environ.get("STRIPE_PRICE_IDS")
        if not raw:
            raise BillingConfigError(
                "STRIPE_PRICE_IDS is not set — price catalog unavailable "
                "(billing endpoints will answer 503 until configured)"
            )
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise BillingConfigError(f"STRIPE_PRICE_IDS is not valid JSON: {_scrub_secrets(str(e))}")
        if not isinstance(data, dict) or not data:
            raise BillingConfigError("STRIPE_PRICE_IDS must be a JSON object of tier → intervals")

        from tortoise.pricing import _load as _pricing_load

        known_tiers = set(_pricing_load().get("tiers", {}).keys())
        unknown = set(data.keys()) - known_tiers
        if unknown:
            raise BillingError(f"unknown tier(s) in STRIPE_PRICE_IDS: {sorted(unknown)}")

        self._prices: dict[str, tuple[str, str]] = {}  # price_id → (tier, interval)
        self._tiers: dict[str, dict[str, str]] = {}    # tier → interval → price_id
        expected_discount = _pricing_load().get("display", {}).get("annual_discount_pct", 20) / 100.0

        for tier, intervals in data.items():
            if not isinstance(intervals, dict):
                raise BillingError(f"STRIPE_PRICE_IDS[{tier!r}] must be {{monthly: ..., annual: ...}}")
            missing = {"monthly", "annual"} - set(intervals.keys())
            if missing:
                raise BillingError(f"tier {tier!r} missing interval(s) in STRIPE_PRICE_IDS: {sorted(missing)}")
            entry = {}
            for interval in ("monthly", "annual"):
                price_id, amount_usd = self._parse_entry(tier, interval, intervals[interval])
                if not price_id.startswith("price_"):
                    raise BillingError(
                        f"STRIPE_PRICE_IDS[{tier!r}][{interval!r}] id {price_id!r} must start with 'price_'"
                    )
                if price_id in self._prices:
                    raise BillingError(f"duplicate price id {price_id!r} in STRIPE_PRICE_IDS")
                self._prices[price_id] = (tier, interval)
                entry[interval] = price_id
            self._tiers[tier] = entry
            self._validate_annual_discount(tier, entry, intervals, expected_discount)

    @staticmethod
    def _parse_entry(tier: str, interval: str, raw) -> tuple[str, float | None]:
        if isinstance(raw, str):
            return raw, None
        if isinstance(raw, dict) and isinstance(raw.get("id"), str):
            amount = raw.get("amount_usd")
            return raw["id"], (float(amount) if amount is not None else None)
        raise BillingError(
            f"STRIPE_PRICE_IDS[{tier!r}][{interval!r}] must be a price id string "
            "or {'id': ..., 'amount_usd': ...}"
        )

    def _validate_annual_discount(self, tier, entry, intervals, expected_discount) -> None:
        from tortoise.pricing import tier_price
        if tier_price(tier) <= 0:
            return  # free tier: no meaningful discount math
        monthly_amount = None
        annual_amount = None
        monthly_raw = intervals.get("monthly")
        annual_raw = intervals.get("annual")
        if isinstance(monthly_raw, dict):
            monthly_amount = monthly_raw.get("amount_usd")
        if isinstance(annual_raw, dict):
            annual_amount = annual_raw.get("amount_usd")
        if monthly_amount is None or annual_amount is None:
            return  # amounts not provided → numeric discount check skipped
        monthly = float(monthly_amount)
        annual = float(annual_amount)
        if monthly <= 0:
            return
        actual = 1.0 - (annual / (monthly * 12.0))
        if abs(actual - expected_discount) > 0.01:
            raise BillingError(
                f"tier {tier!r} annual discount is {actual * 100:.0f}%, "
                f"expected {expected_discount * 100:.0f}% (pricing.json)"
            )

    def price_ids(self) -> list[str]:
        return list(self._prices.keys())

    def price_for(self, tier: str, interval: str) -> str | None:
        """Reverse lookup: the price id for (tier, interval). None if absent."""
        for pid, (t, iv) in self._prices.items():
            if t == tier and iv == interval:
                return pid
        return None

    def tiers(self) -> list[str]:
        return list(self._tiers.keys())

    def tier_for_price(self, price_id: str, interval: str | None = None) -> str:
        """Resolve the tier for a price id. Unknown id → BillingError."""
        hit = self._prices.get(price_id)
        if hit is None:
            raise BillingError(f"unknown price id: {price_id!r} (not in STRIPE_PRICE_IDS)")
        tier, actual_interval = hit
        if interval is not None and interval != actual_interval:
            raise BillingError(
                f"price id {price_id!r} is the {actual_interval} price, not {interval}"
            )
        return tier

    def interval_for_price(self, price_id: str) -> str:
        hit = self._prices.get(price_id)
        if hit is None:
            raise BillingError(f"unknown price id: {price_id!r} (not in STRIPE_PRICE_IDS)")
        return hit[1]


# ── Stripe API client ───────────────────────────────────────────────────────

class StripeClient:
    """Thin httpx wrapper over the Stripe REST API (form-encoded bodies).

    Constructed lazily — raises ``BillingConfigError`` when ``STRIPE_SECRET_KEY``
    is unset at construction time (first use), never at import/boot.
    """

    BASE = _STRIPE_API

    def __init__(self, *, secret_key: str | None = None,
                 webhook_secret: str | None = None, timeout: float = 10.0):
        self._secret_key = secret_key if secret_key is not None else os.environ.get("STRIPE_SECRET_KEY")
        if not self._secret_key:
            raise BillingConfigError(
                "STRIPE_SECRET_KEY is not set — Stripe calls unavailable "
                "(billing endpoints will answer 503 until configured)"
            )
        self._webhook_secret = (
            webhook_secret if webhook_secret is not None else os.environ.get("STRIPE_WEBHOOK_SECRET")
        )
        self._timeout = timeout

    # ── low-level ───────────────────────────────────────────────────

    def _request(self, method: str, path: str, *, params: dict | None = None):
        import httpx

        from .security import redact_error

        url = f"{self.BASE}/{path}"
        headers = {"Authorization": f"Bearer {self._secret_key}"}
        try:
            with httpx.Client(timeout=self._timeout) as client:
                if method == "POST":
                    resp = client.post(url, data=params or {}, headers=headers)
                else:
                    resp = client.get(url, params=params or {}, headers=headers)
        except Exception as e:  # noqa: BLE001 — httpx errors of every kind
            raise StripeAPIError(f"stripe {method} {path} failed: {redact_error(e)}") from e
        if resp.status_code >= 400:
            raise StripeAPIError(
                f"stripe {method} {path} -> {resp.status_code}: {_scrub_secrets(resp.text or '')}"
            )
        try:
            return resp.json()
        except Exception as e:  # noqa: BLE001
            raise StripeAPIError(f"stripe {method} {path} returned non-JSON: {redact_error(e)}") from e

    # ── Stripe resources ────────────────────────────────────────────

    def create_customer(self, email: str) -> str:
        """POST /v1/customers → Stripe customer id."""
        data = self._request("POST", "customers", params={"email": email})
        cid = data.get("id")
        if not cid:
            raise StripeAPIError("stripe create_customer returned no id")
        return cid

    def create_checkout_session(self, team_id: str, price_id: str, customer: str,
                                success_url: str, cancel_url: str) -> str:
        """POST /v1/checkout/sessions (mode=subscription) → checkout url.

        Binds the team via ``client_reference_id`` + ``metadata.team_id`` so
        the webhook can map the session to a team without trusting email.
        """
        data = self._request("POST", "checkout/sessions", params={
            "mode": "subscription",
            "customer": customer,
            "line_items[0][price]": price_id,
            "line_items[0][quantity]": "1",
            "client_reference_id": team_id,
            "metadata[team_id]": team_id,
            "success_url": success_url,
            "cancel_url": cancel_url,
        })
        url = data.get("url")
        if not url:
            raise StripeAPIError("stripe checkout session returned no url")
        return url

    def create_portal_session(self, customer_id: str, return_url: str) -> str:
        """POST /v1/billing_portal/sessions → customer portal url."""
        data = self._request(
            "POST", "billing_portal/sessions",
            params={"customer": customer_id, "return_url": return_url},
        )
        url = data.get("url")
        if not url:
            raise StripeAPIError("stripe portal session returned no url")
        return url

    def get_subscription(self, subscription_id: str) -> dict:
        return self._request("GET", f"subscriptions/{subscription_id}")

    def list_subscriptions(self, customer_id: str) -> list[dict]:
        data = self._request(
            "GET", "subscriptions", params={"customer": customer_id, "status": "all"}
        )
        return data.get("data", [])

    def get_customer(self, customer_id: str) -> dict:
        return self._request("GET", f"customers/{customer_id}")

    # ── webhook signature verification ──────────────────────────────

    def verify_webhook_signature(self, payload: bytes, sig_header: str | None,
                                 secret: str | None = None, tolerance_s: int = 300) -> dict:
        """Verify a Stripe-Signature header over the RAW payload bytes.

        Stripe's header is ``t=<ts>,v1=<sig>[,v1=<sig2>...]`` — split on commas
        and accept any matching v1 signature. Signed payload is
        ``f"{ts}." + raw_body`` (never re-serialized JSON), HMAC-SHA256 with
        the WEBHOOK ENDPOINT secret, constant-time ``compare_digest``, ±300s
        timestamp tolerance. Returns the parsed event dict; raises
        ``BillingError`` (bad signature / expired / malformed) or
        ``BillingConfigError`` (webhook secret unset).
        """
        secret = secret if secret is not None else self._webhook_secret
        if not secret:
            raise BillingConfigError(
                "STRIPE_WEBHOOK_SECRET is not set — webhook verification unavailable"
            )
        if not payload or not sig_header:
            raise BillingError("missing webhook payload or Stripe-Signature header")
        items = [p.split("=", 1) for p in sig_header.split(",") if "=" in p]
        t_values = [v for k, v in items if k == "t"]
        if not t_values:
            raise BillingError("Stripe-Signature header missing t= timestamp")
        ts = t_values[-1]
        try:
            timestamp = int(ts)
        except (TypeError, ValueError):
            raise BillingError("malformed Stripe-Signature timestamp")
        if abs(int(time.time()) - timestamp) > tolerance_s:
            raise BillingError(
                f"webhook timestamp outside {tolerance_s}s tolerance — rejecting stale event"
            )
        signed_payload = f"{ts}.".encode() + payload
        for key, sig in items:
            if not key.startswith("v1"):
                continue
            expected = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
            if hmac.compare_digest(sig, expected):
                try:
                    return json.loads(payload)
                except (ValueError, TypeError) as e:
                    raise BillingError(f"webhook payload is not valid JSON: {e.__class__.__name__}")
        raise BillingError("webhook signature verification failed")


# ── Tier resolution / mirror writes ─────────────────────────────────────────

def effective_tier(team: dict, now: float | None = None) -> str:
    """Lazy grace resolution: the ONLY time-based degrade is ``past_due`` past
    ``grace_until`` → ``free``.

    Deliberately NO 'active + current_period_end passed → free' branch (review
    fix 6): Stripe auto-renews, so that branch fired on every renewal before
    the webhook landed — a recurring false-downgrade outage. Missed-event drift
    is covered by the webhook (seconds) + boot reconcile (Task 8).

    Never returns a tier above the stored one.
    """
    if now is None:
        now = time.time()
    stored = team.get("tier") or "free"
    if team.get("subscription_status") == "past_due":
        grace_until = team.get("grace_until")
        if grace_until is not None:
            try:
                if now > float(grace_until):
                    return "free"
            except (TypeError, ValueError):
                pass  # unparseable grace_until → keep stored tier
    return stored


def apply_limits(sdk, team_id: str, tier: str) -> None:
    """Atomic tier + limits SET on the registry Team node (one Cypher write).

    GAP-B mapping: ``max_points := tier_limits(tier)["max_graph_nodes"]`` —
    the points quota counter counts graph nodes (see module docstring).
    ``max_sessions`` is 1000 flat across tiers.
    """
    from tortoise.pricing import tier_limits

    lim = tier_limits(tier)
    sdk._get_registry().query(
        "MATCH (t:Team {id:$id}) SET t.tier=$tier, t.max_users=$max_users, "
        "t.max_graphs=$max_graphs, t.max_api_keys=$max_api_keys, "
        "t.max_points=$max_points, t.max_sessions=$max_sessions",
        params={
            "id": team_id,
            "tier": tier,
            "max_users": lim["max_users_per_team"],
            "max_graphs": lim["max_graphs_per_team"],
            "max_api_keys": lim["max_api_keys"],
            "max_points": lim["max_graph_nodes"],
            "max_sessions": _MAX_SESSIONS,
        },
    )


def subscription_plan(sub: dict) -> tuple[str, str]:
    """Resolve (tier, interval) from a Stripe subscription's items[0].price.id.

    An id absent from the catalog raises ``BillingError`` — the caller decides
    (log + ops-notify + KEEP stored tier/status; never downgrade a paid sub on
    an unparseable price, review fix 7).
    """
    try:
        price = sub["items"]["data"][0]["price"]
        price_id = price["id"]
    except (KeyError, IndexError, TypeError) as e:
        raise BillingError(
            f"subscription payload missing items[0].price.id: {e.__class__.__name__}"
        ) from e
    catalog = PriceCatalog()
    return catalog.tier_for_price(price_id), catalog.interval_for_price(price_id)


def mirror_subscription(sdk, team_id: str, sub: dict, *,
                        customer_email: str | None = None) -> dict:
    """Authoritative push of a Stripe Subscription onto the Team mirror.

    Order: resolve price→tier (raises on unknown price BEFORE any write) →
    ``apply_limits`` → idempotent status/period SET. Used by boot reconcile and
    the ``customer.subscription.updated`` webhook handler.

    Returns {"tier", "interval", "status"} for audit/analytics.
    """
    tier, interval = subscription_plan(sub)
    apply_limits(sdk, team_id, tier)
    params: dict = {
        "id": team_id,
        "status": sub.get("status") or "active",
        "period_end": sub.get("current_period_end"),
        "cancel_at_period_end": bool(sub.get("cancel_at_period_end")),
    }
    set_fields = (
        "SET t.subscription_status=$status, t.current_period_end=$period_end, "
        "t.cancel_at_period_end=$cancel_at_period_end"
    )
    if sub.get("id"):
        set_fields += ", t.subscription_id=$subscription_id"
        params["subscription_id"] = sub["id"]
    if customer_email:
        set_fields += ", t.customer_email=$customer_email"
        params["customer_email"] = customer_email
    sdk._get_registry().query(
        f"MATCH (t:Team {{id:$id}}) {set_fields}", params=params
    )
    return {"tier": tier, "interval": interval, "status": sub.get("status") or "active"}


def reconcile_team(sdk, team_id: str, force: bool = False) -> dict:
    """Best-effort mirror repair from Stripe (boot reconcile, Task 8).

    - Team has ``subscription_id`` → GET subscription → mirror.
    - elif Team has ``stripe_customer_id`` (missed checkout event blind spot)
      → LIST subscriptions → first active/trialing/past_due → mirror.
    - else no-op.

    Best-effort by contract: raises ``BillingError`` (unknown price — caller
    logs + keeps stored tier/status) and ``StripeAPIError`` / ``BillingConfigError``
    (outage / unconfigured — caller catches and logs; never breaks boot).

    ``force`` is accepted for signature compatibility; reconcile always repairs
    from Stripe truth.
    """
    reg = sdk._get_registry()
    rows = reg.query(
        "MATCH (t:Team {id:$id}) RETURN t.subscription_id, t.stripe_customer_id",
        params={"id": team_id},
    ).result_set
    if not rows:
        raise BillingError(f"reconcile_team: team {team_id!r} not found in registry")
    sub_id, customer_id = rows[0]
    if not sub_id and not customer_id:
        return {"team_id": team_id, "action": "noop"}
    client = StripeClient()
    if sub_id:
        sub = client.get_subscription(sub_id)
        summary = mirror_subscription(sdk, team_id, sub)
        return {"team_id": team_id, "action": "mirror_subscription", **summary}
    subs = client.list_subscriptions(customer_id)
    for sub in subs:
        if sub.get("status") in _ACTIVE_STATUSES:
            summary = mirror_subscription(sdk, team_id, sub)
            return {"team_id": team_id, "action": "mirror_customer_first_active", **summary}
    return {"team_id": team_id, "action": "noop"}
