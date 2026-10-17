"""Stripe billing mirror for Tortoise Hosted (#310).

Architecture: Stripe is the authority for *money*; the FalkorDB registry
Org node is the authority for *enforcement*. This module provides the thin
Stripe API wrapper (``StripeClient`` over httpx — form-encoded, timeouts), the
validated price catalog (``PriceCatalog`` from the ``STRIPE_PRICE_IDS`` env
JSON), lazy-grace tier resolution (``effective_tier``), the atomic tier+limits
writer (``apply_limits``), per-org mirror repair (``reconcile_org``), and the
fleet-wide cadence that actually RUNS it (``reconcile_subscriptions``, #8878).

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
import logging
import os
import re
import time

__all__ = [  # noqa: RUF022
    "BillingError", "BillingConfigError", "StripeAPIError",
    "BillingEnumerationError",
    "PriceCatalog", "StripeClient",
    "effective_tier", "apply_limits", "subscription_plan",
    "subscription_period_bounds",
    "mirror_subscription", "reconcile_org", "reconcile_subscriptions",
    "org_billing_identity",
]

_logger = logging.getLogger(__name__)

_STRIPE_API = "https://api.stripe.com/v1"
_ACTIVE_STATUSES = ("active", "trialing", "past_due")


class BillingError(Exception):
    """Base billing error — the caller decides how to surface it."""


class BillingConfigError(BillingError):
    """Missing/invalid billing configuration — raised lazily at first use."""


class StripeAPIError(BillingError):
    """A Stripe API call failed (network, auth, 4xx/5xx)."""


class BillingEnumerationError(BillingError, RuntimeError):
    """The fleet could not be enumerated as a CERTIFIED-COMPLETE set.

    The fail-CLOSED refusal behind ``reconcile_subscriptions``: a partial page
    must never be published as a clean pass (#8878/#5388). It is given a name
    of its own so the INTERNAL endpoint can answer a 5xx that SAYS what
    happened (``503 enumeration_incomplete``) instead of an opaque 500 — the
    daily driver's log is the page, and `billing reconcile returned HTTP 500`
    names nothing an operator can act on.

    It subclasses ``RuntimeError`` as well, deliberately: this IS a
    control-plane seam failure (``SupabaseControlPlane`` raises ``RuntimeError``
    for every transport/HTTP fault), so a caller that already fails closed on
    that shape keeps working unchanged.
    """


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
    # P2 (Qwen review): catch keys with underscores/hyphens (e.g. re_test_...)
    out = re.sub(r"(?<![A-Za-z0-9])(?:sk|rk|whsec|re)_[A-Za-z0-9_-]{8,}", "***", out)
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
            raise BillingConfigError(f"STRIPE_PRICE_IDS is not valid JSON: {_scrub_secrets(str(e))}")  # noqa: B904
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
                price_id, amount_usd = self._parse_entry(tier, interval, intervals[interval])  # noqa: RUF059
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
            try:
                return raw["id"], (float(amount) if amount is not None else None)
            except (TypeError, ValueError) as e:  # P2 (Qwen): malformed env degrades, not 500
                raise BillingConfigError(
                    f"STRIPE_PRICE_IDS[{tier!r}][{interval!r}] amount_usd is not numeric"
                ) from e
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
        except Exception as e:  # noqa: BLE001, RUF100
            raise StripeAPIError(f"stripe {method} {path} failed: {redact_error(e)}") from e
        if resp.status_code >= 400:
            raise StripeAPIError(
                f"stripe {method} {path} -> {resp.status_code}: {_scrub_secrets(resp.text or '')}"
            )
        try:
            return resp.json()
        except Exception as e:  # noqa: BLE001, RUF100
            raise StripeAPIError(f"stripe {method} {path} returned non-JSON: {redact_error(e)}") from e

    # ── Stripe resources ────────────────────────────────────────────

    def create_customer(self, email: str) -> str:
        """POST /v1/customers → Stripe customer id."""
        data = self._request("POST", "customers", params={"email": email})
        cid = data.get("id")
        if not cid:
            raise StripeAPIError("stripe create_customer returned no id")
        return cid

    def create_checkout_session(self, org_id: str, price_id: str, customer: str,
                                success_url: str, cancel_url: str) -> str:
        """POST /v1/checkout/sessions (mode=subscription) → checkout url.

        Binds the org via ``client_reference_id`` + ``metadata.org_id`` so
        the webhook can map the session to an org without trusting email.
        """
        data = self._request("POST", "checkout/sessions", params={
            "mode": "subscription",
            "customer": customer,
            "line_items[0][price]": price_id,
            "line_items[0][quantity]": "1",
            "client_reference_id": org_id,
            "metadata[org_id]": org_id,
            "success_url": success_url,
            "cancel_url": cancel_url,
        })
        url = data.get("url")
        if not url:
            raise StripeAPIError("stripe checkout session returned no url")
        return url

    def create_checkout_session_for_new_org(
        self, *, new_org_id: str, price_id: str, email: str | None,
        user_id: str, org_name: str, tier: str,
        success_url: str, cancel_url: str,
    ) -> tuple[str, str]:
        """#2789: Checkout for an organization that does NOT exist yet.

        Deliberately not ``create_checkout_session``: there is no org row to
        bind and no reusable Stripe customer. Two differences matter:

        - ``customer_email`` instead of ``customer`` — in subscription mode
          Stripe CREATES the Customer at completion. An ABANDONED checkout
          therefore leaves literally nothing behind: no org row, no
          membership, no graph, not even an orphaned Stripe customer.
        - the pre-minted org id rides BOTH ``client_reference_id`` (the
          existing webhook binding — the event echoes it back) and metadata
          (``user_id`` / ``org_name`` / ``tier`` / ``new_org=1``), so
          ``checkout.session.completed`` can provision the org idempotently
          before applying billing state. ``tier`` is the server-resolved
          catalog tier for ``price_id`` so a failed subscription fetch cannot
          leave a paying customer on free limits.

        Returns ``(checkout_session_id, checkout_url)``.
        """
        params = {
            "mode": "subscription",
            "line_items[0][price]": price_id,
            "line_items[0][quantity]": "1",
            "client_reference_id": new_org_id,
            "metadata[new_org]": "1",
            "metadata[org_id]": new_org_id,
            "metadata[user_id]": user_id,
            "metadata[org_name]": org_name,
            "metadata[tier]": tier,
            "success_url": success_url,
            "cancel_url": cancel_url,
        }
        if email:
            # Not an empty string: Stripe rejects an empty customer_email.
            params["customer_email"] = email
        data = self._request("POST", "checkout/sessions", params=params)
        url = data.get("url")
        if not url:
            raise StripeAPIError("stripe checkout session returned no url")
        return str(data.get("id") or ""), url

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
            raise BillingError("malformed Stripe-Signature timestamp")  # noqa: B904
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
                    raise BillingError(f"webhook payload is not valid JSON: {e.__class__.__name__}")  # noqa: B904
        raise BillingError("webhook signature verification failed")


# ── Tier resolution / mirror writes ─────────────────────────────────────────

def effective_tier(org: dict, now: float | None = None) -> str:
    """Lazy grace resolution: the ONLY time-based degrade is ``past_due`` past
    ``grace_until`` → ``free``.

    Deliberately NO 'active + current_period_end passed → free' branch (review
    fix 6): Stripe auto-renews, so that branch fired on every renewal before
    the webhook landed — a recurring false-downgrade outage. Missed-event drift
    is covered by the webhook (seconds) + ``reconcile_subscriptions`` (#8878,
    the cadenced caller of ``reconcile_org`` — before #8878 the docstring's
    "boot reconcile" had no production caller at all, so a missed delivery was
    permanent).

    Never returns a tier above the stored one. That is deliberate and it is
    why drift must be repaired at the WRITE (the reconciler), not derived
    here: the only server-side evidence of what an org pays is its Stripe
    subscription (there is no price column on the org row, and the
    ``checkout_price_id`` on ``/v1/team`` is the CATALOG's default CTA price
    from ``_default_checkout_price_id()`` — pro for every org — so deriving
    from it would upgrade every active org for free). See #8878.
    """
    if now is None:
        now = time.time()
    stored = org.get("tier") or "free"
    if org.get("subscription_status") == "past_due":
        grace_until = org.get("grace_until")
        if grace_until is not None:
            expiry = _grace_expiry_epoch(grace_until)
            if expiry is not None and now > expiry:
                return "free"
    return stored


def _grace_expiry_epoch(value) -> float | None:
    """``grace_until`` as an epoch float, or ``None`` when unparseable.

    #8878: the ONLY production writer of ``grace_until`` is the
    ``invoice.payment_failed`` handler, which stores
    ``datetime.isoformat()`` (``hosted_api._webhook_apply_event``). The
    original ``float(grace_until)`` therefore raised ValueError on EVERY
    production row and fell through to "keep the stored tier" — so even a
    caller of this resolver could never observe the grace expiry. Both shapes
    are accepted here: ISO-8601 (production) and a bare epoch number (the
    shape ``effective_tier``'s unit tests pass). An unparseable value still
    degrades to ``None`` → keep the stored tier (never a downgrade on a
    malformed field).

    ⚠️ SCOPE — this fixes the PARSING, not the enforcement: ``effective_tier``
    still has no production caller (enforcement reads the stored columns via
    ``quota.derived_tier``/``resolve_org_limits``), so no org's tier degrades
    on grace today. Wiring the rule into the enforcement resolver is a
    behaviour change owed its own decision — tracked in #8893.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    from datetime import UTC, datetime
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def apply_limits(sdk, org_id: str, tier: str) -> None:
    """Atomic tier + limits SET on the registry Org node (one Cypher write).

    GAP-B mapping: ``max_points := tier_limits(tier)["max_graph_nodes"]`` —
    the points quota counter counts graph nodes (see module docstring).
    ``max_sessions`` is written as **NULL (unlimited)** for every tier: the
    flat 1000 was an inherited code fallback, never a ratified cap (#4010 —
    see the module comment in ``tortoise/quota.py``). Writing the NULL here
    also CLEARS any stored cap on the next tier change — the data half of the
    same fix (one-shot sweep: graph-scripts/clear_max_sessions_4010.py).

    #771 review P1: Supabase mode PATCHes the orgs row (tier + the quota
    columns 0006 carries: max_users/max_graphs/ops_allowance/graph_size_cap;
    max_api_keys falls back to pricing and max_sessions is unlimited in
    quota.py) — the registry twin only for selfhost.
    """
    from tortoise.pricing import tier_limits

    lim = tier_limits(tier)
    try:
        from tortoise.supabase_control import (  # noqa: I001
            get_control_plane, is_supabase_enabled, update_org_billing,
        )
        if is_supabase_enabled():
            update_org_billing(get_control_plane(), org_id, {
                "tier": tier,
                "max_users": lim["max_users_per_team"],
                "max_graphs": lim["max_graphs_per_team"],
                "ops_allowance": lim["included_write_ops_per_month"],
                "graph_size_cap": lim["max_graph_nodes"],
            })
            return
    except ImportError:
        pass
    sdk._get_registry().query(
        "MATCH (t:Team {id:$id}) SET t.tier=$tier, t.max_users=$max_users, "
        "t.max_graphs=$max_graphs, t.max_api_keys=$max_api_keys, "
        "t.max_points=$max_points, t.max_sessions=$max_sessions",
        params={
            "id": org_id,
            "tier": tier,
            "max_users": lim["max_users_per_team"],
            "max_graphs": lim["max_graphs_per_team"],
            "max_api_keys": lim["max_api_keys"],
            "max_points": lim["max_graph_nodes"],
            # #4010: unlimited for every tier — NULL, never a number.
            "max_sessions": None,
        },
    )


def _supabase_mode() -> bool:
    """True when billing must use the Supabase control plane (#669).

    The lazy import mirrors ``apply_limits``: a selfhost/minimal install
    without ``supabase_control`` stays registry-only and never pays the import.
    Explicit ``TORTOISE_CONTROL_PLANE=supabase`` with missing creds returns
    True (fail-closed) — the seam's ``get_control_plane()`` then raises, so a
    Supabase-only deployment can never silently fall back to the registry.
    """
    try:
        from tortoise.supabase_control import is_supabase_enabled
    except ImportError:
        return False
    return is_supabase_enabled()


def _subscription_items(sub: dict) -> list:
    """A Stripe subscription's item rows, for either payload shape.

    Stripe returns ``items`` as a ``{'data': [...]}`` envelope; fixtures and
    older payloads use a flat list. Anything else (a scalar, a missing key,
    ``None``) yields ``[]`` — NEVER an ``AttributeError``: this helper runs on
    webhook payloads, so a malformed shape must not raise. (#4216 review: the
    first version called ``.get`` on any truthy non-dict and 500'd the
    checkout route, before the metadata-tier fallback could run.)
    """
    # Guard the SUBJECT as well as the value: a non-dict ``sub`` must yield
    # ``[]`` too (``hosted_api._price_id_from`` passes the raw payload in).
    items = sub.get("items") if isinstance(sub, dict) else None
    if isinstance(items, list):
        rows = items
    elif isinstance(items, dict):
        rows = items.get("data") or []
    else:
        rows = []
    return rows if isinstance(rows, list) else []


def subscription_period_bounds(sub: dict) -> tuple:
    """The subscription's billing period bounds — ``(start, end)`` — or
    ``(None, None)`` when the payload carries neither.

    #4216: Stripe API ``2025-03-31.basil`` moved ``current_period_start`` /
    ``current_period_end`` OFF the top-level Subscription resource and onto its
    subscription ITEMS. Read the top level first (pre-Basil, and the shape the
    registry twin has always stored), then fall back to ``items[0]``. WITHOUT
    the fallback a Basil-or-later account silently writes NO window and the
    paying org stays unmeterable — the defect this fixes.

    Values are returned AS-IS (whatever the API version emitted: a Unix epoch
    int pre-Basil, an ISO-8601 string on newer versions, or ``None``); each
    caller's own truthiness/None guard decides whether to write. A non-dict
    ``sub`` yields ``(None, None)``.
    """
    if not isinstance(sub, dict):
        return None, None
    start = sub.get("current_period_start")
    end = sub.get("current_period_end")
    if start is None or end is None:
        item = next(iter(_subscription_items(sub)), {})
        if not isinstance(item, dict):
            item = {}
        if start is None:
            start = item.get("current_period_start")
        if end is None:
            end = item.get("current_period_end")
    return start, end


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


def mirror_subscription(sdk, org_id: str, sub: dict, *,
                        customer_email: str | None = None) -> dict:
    """Authoritative push of a Stripe Subscription onto the Org mirror.

    Order: resolve price→tier (raises on unknown price BEFORE any write) →
    ``apply_limits`` → idempotent status/period write. The ONLY caller is
    ``reconcile_org`` (boot reconcile was removed in #4262; the live
    ``customer.subscription.updated`` handler inlines its own ``_set``).

    The status/period write is seam-aware exactly like the webhook's ``_set``
    and ``apply_limits`` (#4726): Supabase mode PATCHes the authoritative
    ``organizations`` row — #669 deletes the registry graph there, so the old
    unconditional ``MATCH (t:Team) SET`` matched 0 rows and reported success
    (a silent billing loss) and, worse, RESURRECTED the deleted graph by
    executing on it (#878). Registry/selfhost mode keeps the ``:Team`` twin.

    Returns {"tier", "interval", "status"} for audit/analytics.
    """
    status = sub.get("status") or "active"
    # P0 (code-review): gate the tier on subscription status — Stripe keeps
    # items[].price on canceled/unpaid/incomplete_expired subs, so an
    # ungated mirror re-applies paid limits at every boot/reconcile. Terminal
    # or unpaid statuses revert to free (matching the webhook semantics).
    if status in ("canceled", "unpaid", "incomplete_expired", "incomplete"):
        tier, interval = "free", "monthly"
    else:
        tier, interval = subscription_plan(sub)
    apply_limits(sdk, org_id, tier)
    # #4216: read the bounds through the top-level-then-item helper, so a
    # Basil-or-later Stripe account (period fields on the subscription ITEMS)
    # still writes a window. Each bound is written ONLY when the payload
    # carries it, so a partial subscription object can never NULL OUT a bound
    # already stored.
    #
    # A payload that carries ONE bound and not the other leaves a half-known
    # anchor. That is REPORTED, not silent — ``metering._current_period``
    # refuses a half-known window and ``cohort_cost`` raises the #3981 alert —
    # and is COMPLETED by the next authoritative push carrying the missing
    # bound. ``20260919000001`` additionally repairs the Supabase
    # ``organizations`` row, which this writer now updates in Supabase mode
    # (#4726).
    period_start, period_end = subscription_period_bounds(sub)
    # One field set, derived once for both stores. ``cancel_at_period_end`` is
    # a REGISTRY-TWIN property only: ``organizations`` has no such column
    # (0012 adds subscription_status / customer_email / grace_until /
    # current_period_end; 20260918000001 adds current_period_start) and no
    # reader consumes it. It is therefore absent from the control-plane dict
    # rather than passed and silently dropped by ``update_org_billing``'s
    # allow-list — the exact silent-drop class this change removes.
    twin: dict = {
        "subscription_status": status,
        "cancel_at_period_end": bool(sub.get("cancel_at_period_end")),
    }
    if sub.get("id"):
        twin["subscription_id"] = sub["id"]
    if period_start:
        twin["current_period_start"] = period_start
    if period_end:
        twin["current_period_end"] = period_end
    if customer_email:
        twin["customer_email"] = customer_email

    if _supabase_mode():
        from tortoise.supabase_control import get_control_plane, update_org_billing
        update_org_billing(
            get_control_plane(), org_id,
            {k: v for k, v in twin.items() if k != "cancel_at_period_end"},
        )
    else:
        set_fields = "SET " + ", ".join(f"t.{k}=${k}" for k in twin)
        sdk._get_registry().query(
            f"MATCH (t:Team {{id:$id}}) {set_fields}",
            params={"id": org_id, **twin},
        )
    return {"tier": tier, "interval": interval, "status": status}


def reconcile_org(sdk, org_id: str, force: bool = False) -> dict:
    """Best-effort mirror repair from Stripe (boot reconcile, Task 8).

    - Org has ``subscription_id`` → GET subscription → mirror.
    - elif Org has ``stripe_customer_id`` (missed checkout event blind spot)
      → LIST subscriptions → first active/trialing/past_due → mirror.
    - else no-op.

    #4726: the identifiers are read from the SAME store ``mirror_subscription``
    writes — the authoritative ``organizations`` row via the control-plane
    seam in Supabase mode (#669 deletes the registry graph there, so the old
    registry read found nothing and any write resurrected it, #878); the
    ``:Team`` node in registry/selfhost mode. ``sdk`` may be ``None`` in
    Supabase mode: this path must never construct a registry-namespaced SDK.

    Best-effort by contract: raises ``BillingError`` (unknown price — caller
    logs + keeps stored tier/status, and org-not-found) and ``StripeAPIError``
    / ``BillingConfigError`` (outage / unconfigured — caller catches and logs;
    never breaks boot).

    ``force`` is accepted for signature compatibility; reconcile always repairs
    from Stripe truth.
    """
    if _supabase_mode():
        from tortoise.supabase_control import get_control_plane, org_billing_state
        row = org_billing_state(get_control_plane(), org_id)
        if not row:
            raise BillingError(f"reconcile_org: org {org_id!r} not found")
        sub_id = row.get("subscription_id")
        customer_id = row.get("stripe_customer_id")
    else:
        reg = sdk._get_registry()
        rows = reg.query(
            "MATCH (t:Team {id:$id}) RETURN t.subscription_id, t.stripe_customer_id",
            params={"id": org_id},
        ).result_set
        if not rows:
            raise BillingError(f"reconcile_org: org {org_id!r} not found in registry")
        sub_id, customer_id = rows[0]
    if not sub_id and not customer_id:
        return {"org_id": org_id, "action": "noop"}
    client = StripeClient()
    if sub_id:
        sub = client.get_subscription(sub_id)
        summary = mirror_subscription(sdk, org_id, sub)  # gates tier on status
        return {"org_id": org_id, "action": "mirror_subscription", **summary}
    subs = client.list_subscriptions(customer_id)
    for sub in subs:
        if sub.get("status") in _ACTIVE_STATUSES:
            summary = mirror_subscription(sdk, org_id, sub)
            return {"org_id": org_id, "action": "mirror_customer_first_active", **summary}
    return {"org_id": org_id, "action": "noop"}


# ── Fleet-wide reconcile cadence (#8878) ────────────────────────────────────
#
# #8878: the STORED ``tier`` is the entitlement (``effective_tier`` never
# derives upward) and its only writer was the Stripe webhook. A single missed
# or failed delivery therefore capped a paying org at the lower tier FOREVER:
# ``reconcile_org`` existed but had no production caller, so nothing ever
# compared the store against Stripe. A paid Pro org was enforced as Solo
# (25 000 nodes), every write refused with "upgrade your plan", and no error
# was surfaced anywhere.
#
# The repair is deliberately NOT derive-on-read. A read cannot supply two
# things this cadence has: (a) the only server-side evidence of what an
# org pays is its Stripe subscription — there is no price column on the org row,
# and ``/v1/team``'s ``checkout_price_id`` is the CATALOG's default CTA price
# (``_default_checkout_price_id()`` → pro for EVERY org), so deriving from it
# would hand every active org the Pro plan for free; and (b) a Stripe API call
# on the enforcement path would make every write depend on Stripe's latency and
# availability. So the store stays the read model and this cadence keeps it
# true — the same "external driver, independent failure domain" shape the
# backup pipeline chose (#596).

#: Billing-identity columns of an org row / ``:Team`` node. Deliberately the
#: 0006-BASE subset (``tier`` + the two Stripe identifiers) — no 0012 additive
#: column, so the enumeration cannot 400 on a pre-0012 schema.
_IDENTITY_FIELDS = ("id", "tier", "subscription_id", "stripe_customer_id")


def org_billing_identity(sdk, org_id: str) -> dict:
    """Stored tier + Stripe identity for one org, from the store the mirror writes.

    Same seam discipline as ``reconcile_org``/#4726: the authoritative
    ``organizations`` row in Supabase mode (#669 deletes the registry graph
    there — a registry read would find nothing), the ``:Team`` node in
    registry/selfhost mode. ``sdk`` may be ``None`` in Supabase mode.

    Returns ``{}`` when the org does not exist. Raises on a store failure —
    the caller decides (this is a READ; a silent ``{}`` would make a broken
    control plane read as "no such org").
    """
    if _supabase_mode():
        from tortoise.supabase_control import get_control_plane
        rows = get_control_plane().query(
            "organizations", select=list(_IDENTITY_FIELDS),
            filters=[("id", "eq", org_id)])
        return dict(rows[0]) if rows else {}
    rows = sdk._get_registry().query(
        "MATCH (t:Team {id:$id}) RETURN t.id, t.tier, t.subscription_id, "
        "t.stripe_customer_id",
        params={"id": org_id},
    ).result_set
    if not rows:
        return {}
    return dict(zip(_IDENTITY_FIELDS, rows[0], strict=True))


def _paid_tier_floor(tier: str | None) -> int | None:
    """The node cap a tier entitles: RANKS tiers for drift, and is reported.

    Two jobs, both about the cap: it orders the before/after pair so a repair
    can be classified up/down, and the same figure is published on the drift
    row as ``from_max_nodes``/``to_max_nodes`` — the enforcement number an
    operator reads, which is why it must come from the real ladder and not a
    hardcoded tier order. The ladder is read from ``pricing.json`` (the single
    source of truth for limits); the harm #8878 measured is the CAP, not the
    label. So the ladder is the ONLY input: an unrecognised tier resolves to
    Free's cap (``pricing.tier_limits``: "Unknown tiers default to the Free
    limits") and ``team`` resolves to its real 600 000 — neither is special.

    ``None`` therefore does NOT mean "unlimited". It means the ladder could not
    be READ at all (``tier_limits`` raised on a malformed ``pricing.json`` —
    #750.8, a config fault), which is the only path that reaches it. Consumers
    must treat ``None`` as "unrankable", never as "pays the most": the whole
    point of ranking on the cap is that an unreadable ladder must not clear
    (pinned by
    ``tests/test_billing.py::TestReconcileSweep::test_an_unreadable_ladder_is_unrankable_not_unlimited``).
    """
    from tortoise.pricing import tier_limits
    try:
        return tier_limits(tier or "free")["max_graph_nodes"]
    except (KeyError, TypeError):
        return None


def _is_paid_tier(tier: str | None) -> bool:
    """Whether a stored ``tier`` claims MORE entitlement than Free — from the
    ladder, never from the presence of an identity.

    The distinction decides ``unverified`` vs ``skipped`` in
    ``reconcile_subscriptions``: a Stripe customer binding with no active
    subscription is a routine state (``hosted_api`` persists
    ``stripe_customer_id`` BEFORE the checkout session, so every abandoned
    checkout leaves one) and holds no entitlement to verify, while a PAID tier
    in that state is the thing a person must look at.

    Uses ``_paid_tier_floor``, so an unrecognised tier (which resolves to
    Free's cap) is not paid, and an UNRANKABLE ladder (``None``) is not treated
    as paid either — the same "never claim what cannot be proved" direction
    ``_drift_direction`` takes.
    """
    floor = _paid_tier_floor(tier)
    free_floor = _paid_tier_floor("free")
    if floor is None or free_floor is None:
        return False
    return floor > free_floor


def _drift_direction(from_tier: str | None, to_tier: str | None) -> str | None:
    """``"up"`` when the repaired tier raises the enforced cap, ``"down"`` otherwise.

    An UNRANKABLE pair — both floors ``None``, i.e. the pricing ladder could not
    be read (a malformed ``pricing.json``, not an unrecognised tier: that
    resolves to Free's cap and ranks normally) — returns ``None``. A third
    value, deliberately distinct from ``"down"`` because it is not a downgrade
    either, and neither pages: the caller pages only ``"up"``, and a change we
    cannot prove RAISED the customer's entitlement must never page as a
    payable-customer-under-delivered event. ``None`` here is a CONFIG FAULT to
    look at, not a no-op.
    ``docs/infra-runbook.md`` §4.2 documents all three values.
    """
    before = _paid_tier_floor(from_tier)
    after = _paid_tier_floor(to_tier)
    if before is None and after is None:
        return None
    if before is None:  # unlimited → finite: a real reduction
        return "down"
    if after is None or after > before:
        return "up"
    return "down"


def _notify_drift(org_id: str, from_tier: str | None, to_tier: str | None,
                  status: str | None) -> None:
    """Page on a repaired UPWARD drift — the payable-customer-under-delivered
    event (#8878 piece 3). Best-effort: ``notify_billing_event`` never raises,
    and a broken alert channel must not abort a reconcile pass."""
    try:
        from tortoise.notify import notify_billing_event
        notify_billing_event(
            "billing_upgrade",
            {"org_id": org_id, "tier": to_tier, "name": org_id},
            {
                "tier": to_tier,
                "subscription_status": status,
                "message": (
                    f"reconciler repaired tier drift {from_tier} → {to_tier} "
                    f"(the org was capped at {from_tier}'s limits while its "
                    "subscription was active)"
                ),
            },
        )
    except Exception as e:  # noqa: BLE001, RUF100 — the pass must complete
        _logger.warning("billing reconcile: drift notify failed for %s: %s",
                        org_id, _scrub_secrets(str(e)))


def _enumerate_billed_orgs(limit: int | None = None) -> list[dict]:
    """Every org row in the store, **certified complete** — or raise.

    The fail-CLOSED half of ``reconcile_subscriptions``' contract, and the
    reason it is its own function: ``SupabaseControlPlane.query`` is a SINGLE
    PostgREST page and PostgREST caps a row list at ``max_rows``
    (``PAGE_READ_SIZE``) *silently* — a truncated read arrives as a non-empty
    PARTIAL list with no error. Reading it as the fleet would report a
    plausible ``checked=N``, green the cron, and leave every org past the page
    never compared to Stripe: the permanent silent cap #8878 exists to remove,
    re-created for a suffix of the fleet. So this walks the seam that OWNS the
    completeness contract — ``query_all``: keyset cursor on ``id``, an EMPTY
    page as the only end-of-walk signal, ``(rows, complete)``, raising on
    outage. ``retention_indicator`` reads the fleet the same way for the same
    reason (#4038/#5388).

    A seam object that predates ``query_all`` (a test double, an older host)
    may still certify by the SERVER's own row count; anything it cannot vouch
    for RAISES rather than being published as a pass.

    Deliberately NO ``deleted_at IS NULL`` filter. A soft-deleted org can still
    be BILLED, and excluding it would hide a live charge from the only pass
    that compares the store against Stripe — the same direction
    ``retention_indicator._fleet_rows`` records for the #3839 counters. The
    write on such a row is ``apply_limits`` rewriting the values it already
    holds (idempotent, values from Stripe truth).

    ``limit`` caps how many rows this pass PROCESSES (ops/tests); it never
    relaxes the certification — the walk always covers the whole table first.
    """
    from tortoise.supabase_control import get_control_plane

    cp = get_control_plane()
    walker = getattr(cp, "query_all", None)
    if callable(walker):
        rows, complete = walker(
            "organizations", select=list(_IDENTITY_FIELDS), filters=None,
            cursor="id")
        if not complete:
            raise BillingEnumerationError(
                "billing reconcile: the org enumeration could not be CERTIFIED "
                f"complete ({len(rows)} row(s) returned) — refusing to publish "
                "a partial pass as a clean one (the NO_ORGS class #596, #8878)"
            )
        candidates = [dict(r) for r in rows]
    else:
        total_aware = getattr(cp, "query_with_total", None)
        if not callable(total_aware):
            raise BillingEnumerationError(
                "billing reconcile: the control-plane seam exposes neither "
                "query_all nor query_with_total, so the fleet cannot be "
                "certified complete — refusing to reconcile a partial page"
            )
        rows, total = total_aware(
            "organizations", select=list(_IDENTITY_FIELDS), order="id",
            count_exact=True)
        if total is None or total != len(rows):
            raise BillingEnumerationError(
                "billing reconcile: the enumeration is a LOWER BOUND (server "
                f"stated {total!r}, page returned {len(rows)}) — refusing to "
                "publish a partial pass as a clean one (#8878)"
            )
        candidates = [dict(r) for r in rows]
    if limit is not None:
        candidates = candidates[: int(limit)]
    return candidates


def reconcile_subscriptions(sdk=None, *, limit: int | None = None,
                            notifier=None) -> dict:
    """The cadenced caller of ``reconcile_org`` (#8878): pull Stripe truth for
    EVERY org that carries a billing identity and repair the mirror.

    Why this exists at all: ``reconcile_org`` is per-org and had no production
    caller, so a missed webhook delivery was permanent. This is the fleet-wide
    pass the ``effective_tier`` docstring promised and nothing ran.

    Fail-soft PER ORG, fail-CLOSED on enumeration. One org's failure — a
    Stripe outage, an unknown price, or a control-plane write that matched no
    row (``update_org_billing`` raises ``RuntimeError`` for that race) — is
    recorded in ``failed`` AND logged, and the pass continues; a per-org error
    must never abort a fleet pass, or one bad row (ordering is stable, by id)
    would block every org after it on every subsequent run. A broken or
    uncertifiable ENUMERATION raises instead: a pass that reported "0 orgs
    checked" from a source that could not be read would read as a clean pass
    (the NO_ORGS class #596 named).

    Returns a summary dict:

    - ``checked`` — orgs with a billing identity this pass attempted
    - ``skipped`` — orgs with nothing to reconcile: no ``subscription_id`` /
      ``stripe_customer_id`` (a free org that never checked out), or a Stripe
      binding that never became a paid subscription (an abandoned checkout —
      ``tier`` at or below Free's cap, so there is no entitlement to verify).
      The second kind DID cost an attempt, so it is counted in ``checked`` as
      well: ``checked`` is how many identity-bearing orgs the pass attempted,
      and ``skipped`` says how many of them (plus the never-checked-out ones)
      had nothing to verify. Counted so a silent enumeration shrink is visible
    - ``repaired`` / ``unchanged`` — stored tier changed / did not
    - ``unverified`` — Stripe answered but named NO active subscription for an
      org whose stored tier is PAID — i.e. one that claims more entitlement
      than Free (``_is_paid_tier``; ``reconcile_org`` reports ``action:
      "noop"``). Counted SEPARATELY from ``unchanged`` because nothing was
      verified against a subscription: folding it into ``unchanged`` would
      report an unexamined org as verified-clean, which is exactly the
      read-only-reconciler failure the ``unrepaired`` guard exists to catch.
      Decided from the LADDER, not from the presence of an identity: a customer
      binding at the unpaid tier is an abandoned checkout, not a symptom, and
      lands in ``skipped``.
      Deliberately NOT auto-downgraded — deciding that a stored-paid org with
      no active Stripe subscription loses its tier is a billing decision, not
      a reconcile detail.
    - ``drift`` — the repaired changes, each with ``direction``: ``up`` =
      the org was being under-delivered, ``down`` = a late downgrade/cancel
      reached the mirror, ``None`` = the pair could not be ranked because the
      pricing ladder could not be READ (a config fault, not an unrecognised
      tier — that ranks at Free's cap); neither ``down`` nor ``None`` pages
      (see ``_drift_direction``). Each row also carries
      ``from_max_nodes``/``to_max_nodes``: the enforced node cap before and
      after, the number that actually refused the customer's writes.
    - ``unrepaired`` — orgs where Stripe resolved a tier the store did NOT end
      up holding. This is the mutation guard made observable: a "reconciler"
      that only READS the price and writes nothing lands here instead of
      reporting success.
    - ``failed`` — per-org errors (Stripe outage, unknown price, missing org)
    - ``alerted`` — upward drifts that were paged (``notifier`` may replace
      the default ``notify.notify_billing_event``; ``notifier=False`` disables)
    """
    if notifier is None:
        notifier = _notify_drift

    if _supabase_mode():
        candidates = _enumerate_billed_orgs(limit)
    else:
        query = ("MATCH (t:Team) RETURN t.id, t.tier, t.subscription_id, "
                 "t.stripe_customer_id ORDER BY t.id")
        if limit is not None:
            query += f" LIMIT {int(limit)}"
        result = sdk._get_registry().query(query).result_set
        candidates = [dict(zip(_IDENTITY_FIELDS, r, strict=True)) for r in result]

    summary: dict = {
        "checked": 0, "skipped": 0, "repaired": 0, "unchanged": 0,
        "unverified": 0, "drift": [], "unrepaired": [], "failed": [],
        "alerted": 0,
    }
    for row in candidates:
        org_id = str(row.get("id") or "")
        if not org_id:
            continue
        if not (row.get("subscription_id") or row.get("stripe_customer_id")):
            # No Stripe identity means no checkout ever completed — a free org
            # is not drift. Never a blind write (the class #4262 warned about).
            summary["skipped"] += 1
            continue
        summary["checked"] += 1
        before_tier = row.get("tier")
        try:
            outcome = reconcile_org(sdk, org_id)
            # The re-read is INSIDE the guard: ``org_billing_identity``
            # explicitly raises on a store failure ("the caller decides"), and
            # a store failure for one org must be recorded like any other
            # per-org failure — not abort the pass.
            after = org_billing_identity(sdk, org_id)
        except Exception as e:  # noqa: BLE001, RUF100 — one org never aborts the pass
            # Caught broadly ON PURPOSE. The provider errors are
            # ``BillingError``/``StripeAPIError``, but the control-plane write
            # path fails with ``RuntimeError`` (a PATCH matching no row, a
            # transport failure) — narrowing this to the provider errors let a
            # single deleted/renamed row abort the whole fleet pass, on every
            # run, at the same point (ordering is stable by id). Recorded AND
            # logged: ``subscription_plan``'s contract asks the caller to
            # "log that, keep the stored tier, and notify" — a silent count is
            # a permanent unrepaired org nobody is told about.
            summary["failed"].append({
                "org_id": org_id,
                "error": f"{e.__class__.__name__}: {_scrub_secrets(str(e))}",
            })
            _logger.error("billing reconcile: %s failed: %s", org_id,
                          f"{e.__class__.__name__}: {_scrub_secrets(str(e))}")
            continue
        if outcome.get("action") == "noop":
            # Stripe answered and named no ACTIVE subscription. Whether that is a
            # symptom depends on what the STORE CLAIMS, so it is decided from
            # the ladder and not from the presence of an identity:
            # ``hosted_api`` persists ``stripe_customer_id`` BEFORE the checkout
            # session, so EVERY abandoned checkout leaves a customer binding at
            # ``tier=free``. That is a routine state with no entitlement to
            # verify — counting it as ``unverified`` put healthy orgs in the
            # operator's queue forever (and emitted a daily ``::warning::`` in
            # the driver). ``unverified`` is kept for the case it names: a PAID
            # tier with no active subscription.
            if not _is_paid_tier(before_tier):
                summary["skipped"] += 1
                _logger.info(
                    "billing reconcile: %s has a Stripe binding but no active "
                    "subscription and is stored at the unpaid tier %r — "
                    "nothing to verify (abandoned checkout)",
                    org_id, before_tier)
                continue
            summary["unverified"] += 1
            _logger.warning(
                "billing reconcile: %s is stored as billed (tier=%r) but "
                "Stripe names no active subscription — not verified, not "
                "downgraded", org_id, before_tier)
            continue
        expected_tier = outcome.get("tier")
        after_tier = after.get("tier")
        if expected_tier is not None and after_tier != expected_tier:
            # A read-only/partial reconciler is a FAILED repair, not a silent
            # success (#8878's mutation check).
            summary["unrepaired"].append({
                "org_id": org_id, "expected_tier": expected_tier,
                "stored_tier": after_tier,
            })
            _logger.error(
                "billing reconcile: %s expected tier %r from Stripe but the "
                "store holds %r — the repair did not take",
                org_id, expected_tier, after_tier)
            continue
        if after_tier == before_tier:
            summary["unchanged"] += 1
            continue
        summary["repaired"] += 1
        direction = _drift_direction(before_tier, after_tier)
        event = {
            "org_id": org_id, "from_tier": before_tier, "to_tier": after_tier,
            "direction": direction,
            # The Stripe status. ``_IDENTITY_FIELDS`` deliberately excludes
            # ``subscription_status`` (no 0012 additive column), so there is no
            # stored value to prefer here — a second read would only ever
            # produce ``None``.
            "subscription_status": outcome.get("status"),
            "from_max_nodes": _paid_tier_floor(before_tier),
            "to_max_nodes": _paid_tier_floor(after_tier),
        }
        summary["drift"].append(event)
        if direction == "up" and notifier:
            notifier(org_id, before_tier, after_tier,
                     event["subscription_status"])
            summary["alerted"] += 1

    repaired_up = sum(1 for d in summary["drift"] if d["direction"] == "up")
    if summary["checked"]:
        # #8878 piece 2: "a metric for 'reconciler changed N orgs'" — one line
        # per pass so drift is observable rather than inferred from a support
        # ticket.
        _logger.info(
            "billing reconcile: checked=%d repaired=%d (up=%d) unchanged=%d "
            "unverified=%d skipped=%d failed=%d unrepaired=%d",
            summary["checked"], summary["repaired"], repaired_up,
            summary["unchanged"], summary["unverified"], summary["skipped"],
            len(summary["failed"]), len(summary["unrepaired"]))
    return summary
