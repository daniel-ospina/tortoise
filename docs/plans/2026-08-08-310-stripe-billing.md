<!-- research-path: self-contained — Stripe research intake embedded in §Pattern Research (2026-08-08) -->

# Implementation Plan: Stripe Billing MVP (#310)

**Status:** Draft
**Complexity:** Standard (tier=Standard; escalation condition from scoping: if Step 2 rewiring exceeds `get_current_team`/`_check_team_limit`, escalate to Complex)
**Issue:** [daniel-ospina/tortoise#310](https://github.com/daniel-ospina/tortoise/issues/310)
**Prerequisites:** `docs/scoping-310-stripe-billing.md` (approved design, Approach A), `product/pricing.json` on main (**already landed** — #662/#675), tier enforcement on main (**already landed** — #568/#615, #329), deploy secrets pattern (#596)
**Written:** 2026-08-08

> **Epic note:** the issue body references `docs/epics/2026-08-03-tortoise-hosted-platform/04-plan.md`. That doc does **not exist in this repo** (migrated from eldato). The **scoping doc (`docs/scoping-310-stripe-billing.md`) is the architecture contract** for this plan. Where main has advanced past the scoping doc, the delta is noted inline as **`MAIN-DELTA`** and resolved in favor of current main unless stated otherwise.

---

## 1. Problem Statement

Tortoise Hosted has no way for customers to pay. The MVP must deliver a working loop: **dashboard Upgrade → Stripe Checkout → webhook → team actually gets higher limits → portal/grace/cancel handled.** Subscription state lives on the FalkorDB registry Team node as a **derived mirror** of Stripe (Stripe = authority for money; registry Team node = authority for enforcement). Metering, overage billing, and the Supabase control-plane migration (#669) are explicitly out of scope.

**Main-state correction (MAIN-DELTA 1):** the scoping doc's root-cause framing — "`tier` is inert, `_check_team_limit` enforces hardcoded defaults, `get_current_team` never returns enforced fields" — has been **substantially fixed on main** since scoping:
- `tortoise/pricing.py` loads `product/pricing.json` and exposes `tier_limits()` (canonical limits source).
- `tortoise/quota.py` provides fail-closed `enforce_team_limit` / `resolve_team_limits`; `_check_team_limit` already raises 402 via quota.
- `get_current_team` already returns `tier, max_users, max_graphs, max_points, max_api_keys, max_sessions` (read from the Team node in one round-trip, with pricing/quota fallbacks).
- `team_update` allowlist already includes `stripe_customer_id`, `subscription_id`, and the `max_*` fields.
- Team creation already writes tier-derived `max_users/max_graphs/max_api_keys/ops_allowance/graph_size_cap`.

What #310 must still build: the **subscription state mirror + webhook surface + billing endpoints + grace/reconcile + notification**, plus three **enforcement wiring gaps** (below) that keep paying teams under-capped. This plan targets the residual gap, not the full Step 2 rewrite the scoping anticipated.

### Remaining enforcement gaps (verified in code)
1. **GAP-A — `max_points`/`max_sessions` never written at team creation.** Team creation writes `max_api_keys/ops_allowance/graph_size_cap` but NOT `max_points`/`max_sessions`. `get_current_team` falls back to `quota.DEFAULT_MAX_POINTS = 1000` — contradicting pricing.json's free-tier `10000` write-ops AND the written `ops_allowance` field. A fresh team is effectively capped at 1,000 graph nodes.
2. **GAP-B — `points` quota maps to the wrong pricing field.** The `points` counter (`quota._count_resource`) counts **graph nodes** (`MATCH (n) RETURN count(n)`), but pricing.json's node cap is `max_graph_nodes` (10k/25k/100k/600k). The scoping doc's `max_points` values (10k/10k/50k/200k) match `included_write_ops_per_month` **exactly** — they are write-ops numbers, not the node count the quota actually enforces. **Decision (MAIN-DELTA 2): `max_points` mirrors `max_graph_nodes`** — the plan writes `max_points := tier_limits(tier)["max_graph_nodes"]` in `apply_limits` and at both Team CREATEs. Enforcement-correct because the points counter counts graph nodes; write-ops-based caps remain a metering prerequisite (#296/#308). Flagged in Open Items for owner confirmation.
3. **GAP-C — stale pricing test.** `tests/test_pricing_tiers.py:43` asserts free `included_write_ops_per_month == 1000`; pricing.json on main says **10000** (post-#662). The test file's last change predates #662 — it is failing on main. Fixed in Task 1.

---

## 2. Proposed Solution — Approach A: Graph-native billing mirror with lazy grace enforcement

**Architecture:** Stripe = authority for *money*; FalkorDB registry Team node = authority for *enforcement*. Single data plane (graph), single process (webhook writes graph; requests read graph), no scheduler, no queue, no second DB. Grace enforced **lazily** at request time in `get_current_team` (`effective_tier`) — no cron. `reconcile_team` repairs drift at boot (non-fatal).

**New modules:**
- `tortoise/billing.py` — `StripeClient` (thin `httpx` wrapper, HMAC webhook verify), price-catalog loader (`STRIPE_PRICE_IDS` env JSON validated against pricing.json; **missing catalog/key secrets degrade lazily via a catchable `BillingConfigError` — never at import/boot**, review fix 12), `effective_tier` (lazy grace), `apply_limits` (atomic tier+limits SET), `reconcile_team`.
- `tortoise/notify.py` — best-effort Resend email + Telegram bot (both channels, never blocks the webhook).

**MAIN-DELTA 3 — no duplicate TIERS map:** the scoping proposed a `TIERS` dict in billing.py + a parity test against pricing.json. On current main, `tortoise.pricing.tier_limits()` **is** that canonical map (reads pricing.json directly). `billing.py` imports from `tortoise.pricing` — no duplicate table, no drift. The parity test instead targets the **price catalog** (`STRIPE_PRICE_IDS` ↔ pricing.json tiers/intervals).

**Team node schema additions:** `subscription_status` (`active|past_due|canceled|trialing|incomplete|unpaid`), `current_period_end` (unix ts), `grace_until` (unix ts), `customer_email` (webhook-sourced, provision-path), plus derived `max_points`/`max_sessions` written alongside `tier` in the same atomic Cypher SET (`apply_limits`).

**Event handling (4 events):** `checkout.session.completed` (link customer + activate), `invoice.payment_failed` (past_due + 72h grace), `customer.subscription.updated` (authoritative state push; `cancel_at_period_end=true` → keep tier until period end), `customer.subscription.deleted` (revert to free). Idempotency: **SET-then-marker** (idempotent Team SET, then `WebhookEvent` marker MERGE; notification/audit fired only when the marker was absent) — resolves the scoping P1-1 retry-drop race.

**Notification (user decision 2026-08-08):** Resend email (premiselabs.co domain verified, key `RESEND_API_KEY` in GH secrets, recipient inbox `BILLING_NOTIFY_TO` — added review fix 8) **and** Telegram (`@Premislabs_notifications_bot`, `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` already in deploy secrets) — BOTH, best-effort, on `billing_upgrade|billing_downgrade|billing_payment_failed|billing_cancel`.

---

### Pattern Research

**Library docs (preflight)** — `stripe-python` exists (sync via `requests`, async via `httpx`; `HTTPXClient` option). Not used — scoping chose a thin `httpx` client; verdict below. `httpx` is already an in-file dependency of `hosted_api.py` (`_track_analytics_event`), currently transitive via `fastmcp` — pinned in Task 3.

**Library version & API surface (Stripe REST API, 2026-08) — 3 calls**
- *Canonical:* Checkout Sessions for subscriptions use `mode: "subscription"` with a recurring Price in `line_items`; `success_url`/`cancel_url` are required redirect URLs (can embed `{CHECKOUT_SESSION_ID}`); custom tracking data belongs in `metadata` (and `client_reference_id` — echoed in webhooks, the canonical team-binding field). Since the 2025-03-31 change, subscription-mode Checkout creates the Subscription **only after payment completes** — `checkout.session.completed` is the completion signal (do not rely on `payment_intent` fields). (docs.stripe.com/payments/subscriptions; docs.stripe.com/billing/quickstart; docs.stripe.com/changelog/basil/2025-03-31)
- *Competitor variance:* reference integrations (stripe-samples) handle completion exclusively via `checkout.session.completed` webhook and read `data.object.customer_details.email` / `session.subscription`; the session's `subscription` field is the Subscription **ID** (object not embedded unless expanded) — a follow-up `GET /v1/subscriptions/{id}` (or `GET /v1/checkout/sessions/{id}?expand[]=line_items`) is required to learn the price id for tier resolution.
- *Known pitfall:* creating a Customer at Checkout via `customer_creation` requires care with existing customers; for subscription Checkout pass `customer` (existing) OR `customer_email` (create-on-checkout). Use `client_reference_id=team_id` + `metadata.team_id` so the webhook can bind the event to the team without trusting email matching.

**Idiomatic usage patterns (webhook signature verification) — 3 calls**
- *Canonical:* `Stripe-Signature` header contains `t=<timestamp>,v1=<sig>[,v1=<sig2>...]` — **split on commas** and accept any matching signature. Signed payload = `f"{t}.{raw_body}"` (raw bytes, never re-serialized JSON); HMAC-SHA256 with the **webhook endpoint secret** (not the API key); `hmac.compare_digest`; **tolerance 300s (5 min)**; reject older timestamps. (docs.stripe.com/webhooks; docs.stripe.com/webhooks/signature; Stack Overflow #68288698)
- *Competitor variance:* all Stripe samples use the SDK's `construct_event()` which wraps exactly this; frameworks that hand-roll (FastAPI/Starlette) must use `await request.body()` for the raw payload — a Pydantic-parsed model will fail verification (re-serialization mismatch).
- *Known pitfall:* verify BEFORE parsing; on failure return 400 so Stripe dashboard shows the error; on processing failure return 5xx so Stripe **retries** (live: up to 3 days exponential backoff; test: ~3 attempts); always return 200 for already-processed events (dedup) to stop retries. Retries of the same event carry the **same `event.id`** with a new signature — event-ID dedup is the canonical idempotency key. (docs.stripe.com/webhooks; docs.stripe.com/webhooks/process-undelivered-events; svix Stripe review)

**Library/framework pitfalls — 3 calls**
- *Canonical:* Customer Portal = `POST /v1/billing_portal/sessions` with `customer` + `return_url` (return_url required unless a default is configured in the dashboard). Portal config (subscription management allowed) is dashboard-side. (docs.stripe.com/api/customer_portal/sessions/create; integrate-customer-portal)
- *Competitor variance:* portal-initiated plan changes emit `customer.subscription.updated` (NOT `checkout.session.completed`) — the mirror must be authoritative on `.updated` for plan changes (scoping AC3).
- *Known pitfall (SDK-vs-raw decision):* the scoping's thin-httpx choice **remains sound** — the MVP touches 5 Stripe endpoints + one signature verify, all stable form-encoded POST/GETs. `stripe-python`'s value (retries, version pinning, webhook helper) is marginal at this surface and would add `requests` as a dependency. **Revisit if** the API surface grows (metering #296/#308, invoices, payment methods) — a swap is contained behind `StripeClient`. Stripe API requests are `application/x-www-form-urlencoded` bodies, not JSON.

**Skipped:** no other third-party deps introduced. Resend + Telegram use plain `httpx`/stdlib `urllib` (existing in-repo Telegram pattern: `tortoise/alert_store.py:telegram_send`).

---

### Integration Surface Map

| # | Surface | Data Flow | Contract / Notes | Test Layer |
|---|---------|-----------|------------------|------------|
| S1 | Registry Team node (tier, max_*, `subscription_status`, `current_period_end`, `grace_until`, `customer_email`) | Read (requests) + Write (webhook/apply_limits/reconcile) | New fields added via `apply_limits` + webhook SET; read in `get_current_team` one round-trip | Integration (FalkorDBLite, `tests/test_billing.py`, `tests/test_hosted_api.py`) |
| S2 | `:WebhookEvent` dedup nodes + indexes | Write | `event_id` unique marker (Task 8 owns `("WebhookEvent","event_id")`); `Team.stripe_customer_id` index (Task 4b owns) | Integration |
| S3 | `get_current_team` + `_check_team_limit` (effective_tier, per-tier limits) | Read path | GAP-A/B fixes; lazy grace; never below free | Integration + unit (`test_effective_tier`) |
| S4 | `POST /webhooks/stripe` (SKIP + SKIP_AUTH + HMAC verify) | Inbound external | Raw body; 400 bad sig; 200 ack; 5xx → Stripe retries | Integration (TestClient, monkeypatched StripeClient) |
| S5 | `POST /v1/billing/checkout`, `POST /v1/billing/portal` | Inbound API → outbound Stripe | price_id validated against catalog; 409 active-sub guard (stored mirror + `list_subscriptions` stale-mirror check); `create_customer(email)` → `customer=<id>` passed to Checkout; sync customer persist | Integration (monkeypatched StripeClient) |
| S6 | `GET /v1/team` + `TeamInfoResponse` | Read | Extended plan/status/limits fields for dashboard | Integration |
| S7 | Stripe external API (Checkout, Portal, Subscriptions, Customers, webhook verify) | Bidirectional | `StripeClient` httpx wrapper; form-encoded; timeouts; verified vs stub | Unit (monkeypatched httpx) + E2E behind `@pytest.mark.stripe` |
| S8 | Secrets: `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_IDS`, `RESEND_API_KEY`, `BILLING_NOTIFY_TO`, `BILLING_SUCCESS_URL/CANCEL_URL` | Config | `.env.example` + deploy-hosted.yml secrets-verify + flyctl secrets set; missing catalog/key secrets degrade lazily (503 at billing endpoints, boot unaffected) | Config validation (deploy-time) + unit (lazy `BillingConfigError`) |
| S9 | `product/pricing.json` + `STRIPE_PRICE_IDS` catalog | Config | Catalog validated against pricing.json (tiers × intervals, annual 20%) | Unit (`tests/test_billing.py`) |
| S10 | Resend API + Telegram Bot API (notify) | Outbound external | Both best-effort; never raise into webhook; in-repo Telegram pattern (alert_store.py) | Unit (monkeypatched HTTP) |
| S11 | `audit_events` (`billing_*`) + `analytics_events` (props allowlist) | Outbound cross-cutting | `_async_audit` + `_track_analytics_event`; add `plan/tier/interval/status` props | Integration |
| S12 | Dashboard SPA (`website/apps/dashboard/src/main.jsx`) | UI | Upgrade CTA, Manage-billing portal, plan/status/limits display, pending-checkout guard | Manual + E2E (fork-mode — no SPA unit harness) |
| S13 | `_lifespan` boot reconcile | Startup | Non-fatal; daemon thread (never awaited before lifespan yield); ~20s wall-clock budget; per-call Stripe timeouts; teams with `subscription_id` OR `stripe_customer_id` | Integration (TestClient startup) |
| S14 | `docs/registry-graph-schema.md` | Docs | Update Team fields + WebhookEvent entity | Docs (schema validation in review) |

**Bug pattern flags:** (1) webhook signature re-serialization mismatch → always verify over `await request.body()` bytes; (2) retry-drop race → SET-then-marker ordering, notifications gated on marker-absent; (3) tier→limit fallback drift → resolve limits from `tier_limits()` when stored fields are None, never from `quota.DEFAULT_MAX_POINTS`; (4) `cancel_at_period_end` misread → check the flag, not the status, before reverting; (5) reconcile fatal-on-outage → wrap in try/except, must not break `/health` or release_command.

### Journey Test Map

```markdown
### Journey: Owner upgrades team to Pro (E2E-3-D, gated behind @pytest.mark.stripe — LIVE leg only, review fix 16c)
1. **Step:** Dashboard "Upgrade" CTA → POST /v1/billing/checkout {price_id: price_pro_monthly} → **Acceptance:** 200 {checkout_url}; stripe_customer_id persisted; 409 if already active → **Test:** tests/test_billing.py::TestCheckoutEndpoint + tests/e2e/test_billing_upgrade.py (live leg)
2. **Step:** Stripe Checkout completes → checkout.session.completed webhook → **Acceptance:** GET /v1/team shows tier=pro, max_points=100000 within seconds (AC1) → **Test:** tests/test_billing.py::TestWebhookEvents (integration, fixture payloads — the 4-event semantics live HERE, not in E2E)
3. **Step:** Replay the same webhook payload → **Acceptance:** processed exactly once (AC2) → **Test:** tests/test_billing.py::test_webhook_replay_dedup
4. **Step:** Portal plan change Solo→Pro → customer.subscription.updated → **Acceptance:** limits update (AC3) → **Test:** test_webhook_subscription_updated
5. **Step:** Cancel via portal → subscription.updated(cancel_at_period_end=true) → deleted later → **Acceptance:** tier kept to period end, then reverted (AC4) → **Test:** test_webhook_cancel_semantics
6. **Step:** Card declined → invoice.payment_failed → **Acceptance:** past_due + grace_until=period_end+72h; effective_tier degrades to free after grace (AC5) → **Test:** test_webhook_payment_failed, test_effective_tier_grace
7. **Step:** Owner checks dashboard after degrade → **Acceptance:** banner + plan display show status; notification fired (AC10) → **Test:** test_notify.py

### Failure Modes
- Stripe outage at boot → **Expected:** reconcile skipped, app healthy → **Test:** tests/test_billing.py::test_boot_reconcile_non_fatal
- Forged webhook (bad signature / expired t=) → **Expected:** 400, no graph write (AC6) → **Test:** test_webhook_bad_signature
- Checkout with active subscription / arbitrary price_id → **Expected:** 409 / 400 (AC8) → **Test:** test_checkout_guard
- Missed first webhook (checkout.session.completed) → **Expected:** boot reconcile repairs via stripe_customer_id (AC7) → **Test:** test_reconcile_repairs_customer_only_team
```

**Tech Stack:** Python 3.11+ (FastAPI, httpx, FalkorDBLite for tests), Stripe REST API v1, Resend REST API, Telegram Bot API, React SPA (fork-mode).

---

## 3. Implementation Plan

> **Execution order follows scoping Steps 1–9.** Tasks are TDD-shaped: write the failing test → run → implement → run green. Full test commands use `python -m pytest tests/<file> -v` (FalkorDBLite embedded; no Docker). Every task ends with a review-readiness checkpoint (no commit — git operations are blocked until the review gate).

### Task 1: Pricing artifact verification + stale-test fix (scoping Step 1 — prerequisite, largely DONE on main)

**Intent:** Confirm the canonical pricing artifact is on main and the limit resolver agrees with it, so every downstream tier decision has a verified source of truth.

**Acceptance:**
- `product/pricing.json` exists on main and matches the owner-confirmed version (free=10k ops, solo $9, pro $25, team $149, annual 20%, overage $5/10k) — verify via `git show origin/main:product/pricing.json` (no changes needed; landed via #662/#675).
- `tests/test_pricing_tiers.py` passes with corrected free-ops assertion (1000 → 10000).
- `tortoise.pricing.tier_limits()` documented as the single limits resolver used by `billing.py` (MAIN-DELTA 3) — no new TIERS map.

**Files:**
- Modify: `tests/test_pricing_tiers.py:43` — stale free-ops assertion
- Test: `tests/test_pricing_tiers.py` (pytest)

**Step 1:** Read `tests/test_pricing_tiers.py`; confirm line 43 asserts `free["included_write_ops_per_month"] == 1000` while `product/pricing.json` says `10000`.

**Step 2:** Update the assertion to `== 10000` (and check the free `max_graph_nodes == 10000` assertion still holds).

**Step 3:** Run `python -m pytest tests/test_pricing_tiers.py -v` → all green.

**Step 4:** Record the GAP-B mapping decision in `tortoise/billing.py` docstring (Task 2): `max_points := tier_limits(tier)["max_graph_nodes"]` (points quota counts graph nodes).

---

### Task 2: `tortoise/billing.py` — StripeClient, price catalog, effective_tier, apply_limits (scoping Steps 1+3 core)

**Intent:** Build the single billing module: Stripe API wrapper, `STRIPE_PRICE_IDS` catalog validated against pricing.json, lazy-grace tier resolution, and the atomic tier+limits writer used by webhook/reconcile.

**Acceptance:**
- `StripeClient` (httpx, form-encoded, timeouts) implements: `create_customer(email) -> str`, `create_checkout_session(team_id, price_id, customer, success_url, cancel_url) -> str` (takes the created Stripe customer id — see Task 5), `create_portal_session(customer_id, return_url) -> str`, `get_subscription(id) -> dict`, `list_subscriptions(customer_id) -> list[dict]`, `get_customer(id) -> dict`, `verify_webhook_signature(payload: bytes, sig_header, secret, tolerance_s=300) -> dict` (t= parse, comma-split multi-signature, HMAC-SHA256 over `f"{t}.{payload}"`, `hmac.compare_digest`, ±300s).
- `PriceCatalog` loads `STRIPE_PRICE_IDS` (JSON: 8 ids — 4 tiers × monthly/annual) and rejects: unknown tier, missing monthly or annual per tier, annual discount ≠ pricing.json `display.annual_discount_pct` (20%), non-`price_` ids. `tier_for_price(price_id, interval)` resolves; an id absent from the catalog raises `BillingError(unknown_price_id)` — the caller decides (Task 7/8: log + ops-notify + **keep stored tier/status**, never downgrade a paid sub on an unparseable price, review fix 7).
- **Lazy config degradation (review fix 12):** missing `STRIPE_PRICE_IDS` / `STRIPE_SECRET_KEY` / `STRIPE_WEBHOOK_SECRET` does NOT fail at import/boot — the catalog and StripeClient construct lazily and raise a catchable `BillingConfigError` at first use (billing endpoints 503; boot/lifespan unaffected). This is the implementation home for Task 3's boot-gating promise.
- `effective_tier(team, now)` — `past_due` past `grace_until` → `free`; else stored tier. Never returns a tier above the stored one. **No defensive "active + `current_period_end` passed → free" branch** (review fix 6: it fired on every auto-renewal before the webhook landed — a recurring false-downgrade; missed-event drift is covered by webhook + boot reconcile, Task 8).
- `apply_limits(sdk, team_id, tier)` — single atomic Cypher SET: `tier`, `max_users`, `max_graphs`, `max_api_keys`, `max_points` (= `max_graph_nodes`), `max_sessions` (1000 flat) from `tortoise.pricing.tier_limits`.
- `reconcile_team(sdk, team_id, force=False)` — subscription_id → `get_subscription` → mirror; elif stripe_customer_id → `list_subscriptions` → first active → mirror; else no-op. Unknown price id → keep stored tier/status (Task 8). Best-effort (raises `BillingError`, caller decides).

**Files:**
- Create: `tortoise/billing.py`
- Test: `tests/test_billing.py` (unit — monkeypatched httpx/StripeClient)

**Step 1:** Write `tests/test_billing.py` (module-level fixtures defined ONCE — `billing_client` + `signed_payload`, see Task 7):
- `test_signature_verify_ok` / `test_signature_tampered_payload_400` / `test_signature_expired_timestamp` (t= older than 300s → reject) / `test_signature_multiple_v1_accepted` (comma-split).
- `test_catalog_loads_8_prices` / `test_catalog_rejects_unknown_tier` / `test_catalog_rejects_wrong_annual_discount` / `test_tier_for_price` / `test_tier_for_price_unknown_id_raises`.
- `test_catalog_missing_env_degrades` (no `STRIPE_PRICE_IDS` → `BillingConfigError` on first use, not at import) / `test_stripe_client_missing_secret_raises_lazy`.
- `test_create_customer` (wrapper POSTs `customers` and returns the id).
- `test_effective_tier_past_due_grace_ok` / `test_effective_tier_past_due_grace_expired_degrades` / `test_effective_tier_active_past_period_keeps_tier` (active sub with period_end in the past must NOT degrade — no defensive branch) / `test_effective_tier_never_upgrades`.
- `test_apply_limits_writes_tier_and_limits_atomically` (assert single query hits Team node with tier + all 6 limit fields; max_points == max_graph_nodes).
- `test_reconcile_subscription_repairs_mirror` / `test_reconcile_customer_only_matches_first_active` (monkeypatched `StripeClient` returning fixture subs).

**Step 2:** Run `python -m pytest tests/test_billing.py -v` → FAIL (module missing).

**Step 3:** Implement `tortoise/billing.py` per the sketch:

```python
# tier/interval → price_id resolution from STRIPE_PRICE_IDS (env JSON)
# StripeClient over httpx; verify_webhook_signature per docs.stripe.com/webhooks/signature
# (t=<ts>,v1=<sig>[,v1=...]; payload = f"{t}." + raw_body; HMAC-SHA256; compare_digest; |now - t| <= 300)
# effective_tier / apply_limits / reconcile_team per acceptance
```

**Step 4:** Run `python -m pytest tests/test_billing.py -v` → green.

---

### Task 3: Secrets, `.env.example`, deploy workflow (scoping Step 3)

**Intent:** Make the Stripe + notification configuration deployable — missing secrets must gate features at boot, not fail the app.

**Acceptance:**
- `.env.example` documents `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_IDS`, `RESEND_API_KEY`, `BILLING_NOTIFY_TO` (ops inbox for billing emails — review fix 8), `BILLING_SUCCESS_URL`, `BILLING_CANCEL_URL` (Telegram pair already documented).
- `deploy-hosted.yml` secrets-verify step requires `STRIPE_SECRET_KEY` + `STRIPE_WEBHOOK_SECRET` (warn-only if `STRIPE_PRICE_IDS`/`RESEND_API_KEY`/`BILLING_NOTIFY_TO` missing — catalog/notify degrade lazily per Task 2); `flyctl secrets set` passes all seven when present.
- `requirements.txt` pins `httpx` (currently transitive via fastmcp).

**Files:**
- Modify: `.env.example`, `.github/workflows/deploy-hosted.yml`, `requirements.txt`
- Test: config review (no unit surface); verify by grepping the workflow

**Step 1:** Add the seven variables to `.env.example` with usage comments (mirror the backup-sweep section style).

**Step 2:** Edit `deploy-hosted.yml`: add `STRIPE_SECRET_KEY`/`STRIPE_WEBHOOK_SECRET` to the hard-require block; add the five optional vars (`STRIPE_PRICE_IDS`, `RESEND_API_KEY`, `BILLING_NOTIFY_TO`, `BILLING_SUCCESS_URL`, `BILLING_CANCEL_URL`) to the `ARGS=` accumulation with `[ -n "${{ secrets.X }}" ]` guards (existing pattern); all five are warn-only/optional, passed unconditionally when present.

**Step 3:** Pin `httpx>=0.27` in `requirements.txt`.

**Step 4:** Grep the workflow: `grep -c "STRIPE_SECRET_KEY\|BILLING_NOTIFY_TO" .github/workflows/deploy-hosted.yml` → ≥2 (verify + set).

**Step 5:** Append the **Ops checklist** (Stripe dashboard, owner-executed — see §Runtime Prerequisites): register webhook endpoint `https://api.premiselabs.co/webhooks/stripe` with the 4 event types, enable Customer Portal config, create the 8 price IDs matching `STRIPE_PRICE_IDS`, keep test/live key separation documented (existing Apresto account — no new Stripe account).

---

### Task 4a: Tier-aware enforcement wiring — GAP-A/B fixes + lazy grace hook (scoping Step 2; split from old Task 4 — review fix 4)

**Intent:** Close the enforcement gaps so a paid tier actually raises limits and `past_due`/expired teams degrade — the "value half" of the loop (already 80% landed; this task finishes it). 4a = enforcement + quota parity; 4b (below) = API surface/docs.

**Acceptance:**
- GAP-A at BOTH Team CREATEs, limits from `tier_limits("free")`:
  - `/internal/provision` CREATE (~427-441): adds `max_points` + `max_sessions` (currently missing).
  - `/v1/register` CREATE (~1086-1093): writes `max_api_keys` + `max_points` + `max_sessions` and aligns `max_users`/`max_graphs` from `tier_limits("free")` — currently hardcoded `1/1/1` with **no** `max_api_keys`, silently leaking the `DEFAULT_MAX_API_KEYS = 20` cap to free teams (review fix 2).
- GAP-B None-fallback **parity across REST and MCP** (review fix 2): pre-existing teams lacking stored `max_points`/`max_api_keys`/`max_sessions` resolve from `tier_limits(tier)` — NOT the `quota.DEFAULT_MAX_*` consts. The invariant documented at hosted_api.py ~624 ("mirrors `quota.resolve_team_limits` so REST and MCP see identical limits") must hold:
  - `get_current_team` (~628-646): replace the `DEFAULT_MAX_*` fallbacks with `tier_limits(eff_tier)` resolution (points fallback = `max_graph_nodes`).
  - `quota.resolve_team_limits` (quota.py ~82-99): same `tier_limits(tier)` fallback for None values; `DEFAULT_MAX_*` consts dropped from the resolver (removed if unreachable after the fallback change — resolve in code).
- Grace hook in `get_current_team`: read `subscription_status, current_period_end, grace_until, customer_email` in the same round-trip; compute `eff_tier = billing.effective_tier(...)`; never below free.
- `_check_team_limit` unchanged behavior: 402 via `quota.enforce_team_limit`; effective tier already baked into the limits dict.
- Test fixtures mirror production semantics: `tests/conftest.py` `provision_test_user` must ALSO SET `max_points`/`max_sessions` from `tier_limits(tier)` (max_points = max_graph_nodes) — review fix 16b.

**MAIN-DELTA (402 detail — review fix 13):** the 402 `detail` from `quota.enforce_team_limit` ("Team {resource} limit reached ({limit}). Upgrade your plan to increase it.") is declared **acceptable as-is** — no plans/portal link appended to the API string. The user-facing upgrade hint (with plans/portal link) is the Task 9 dashboard renderer's job; duplicating env-dependent URLs into the API detail adds coupling for no MVP value.

**Files:**
- Modify: `tortoise/hosted_api.py` (get_current_team ~628-646, provision CREATE ~427-441, register CREATE ~1086-1093)
- Modify: `tortoise/quota.py` (`resolve_team_limits` None-fallback → `tier_limits(tier)`)
- Modify: `tests/conftest.py` (`provision_test_user`)
- Test: `tests/test_hosted_api.py`, `tests/test_quota.py`

**Step 1:** Extend `tests/test_hosted_api.py` — `test_team_points_limit_uses_tier_limits_not_default` (register a fresh team; assert `max_points == 10000` free, not 1000), `test_register_team_writes_free_tier_limits` (fresh `/v1/register` team has `max_api_keys == 2` from pricing.json, NOT 20). Run → FAIL (GAP-A/B present).

**Step 2:** Extend `tests/test_quota.py` — `test_legacy_team_no_stored_limits_resolves_tier_limits`: create a Team node with tier='pro' and NO stored max_* fields; assert `resolve_team_limits` returns tier-derived values (max_points == 100000, max_api_keys == 10, max_sessions) and a REST `get_current_team` via TestClient returns identical numbers (REST/MCP parity — review fix 2). Run → FAIL.

**Step 3:** Fix GAP-A: add `max_points: $max_points, max_sessions: $max_sessions` to the provision CREATE; add `max_api_keys`/`max_points`/`max_sessions` + aligned `max_users`/`max_graphs` to the register CREATE (params from `tier_limits("free")`).

**Step 4:** Fix GAP-B + grace: extend the `get_current_team` registry query with the 4 billing fields; after unpacking compute `eff_tier = billing.effective_tier(...)`; resolve None limits from `tier_limits(eff_tier)` (never `quota.DEFAULT_MAX_POINTS`). Apply the same None-fallback to `quota.resolve_team_limits`.

**Step 5:** Update `tests/conftest.py` `provision_test_user` to SET `max_points`/`max_sessions` from `tier_limits(tier)` so test-provisioned teams match production creation semantics.

**Step 6:** Run `tests/test_hosted_api.py` + `tests/test_quota.py` → green.

---

### Task 4b: API surface + registry schema — TeamInfoResponse, team_update, indexes, docs (scoping Step 6 half; review fix 4)

**Intent:** Expose the extended billing fields over `GET /v1/team`, let the control plane set them, and keep the registry schema/indexes in step with the new fields.

**Acceptance:**
- `TeamInfoResponse` + `GET /v1/team` (`team_info` ~1010, model ~720) return `max_api_keys, max_points, max_sessions, subscription_status, current_period_end, grace_until, customer_email`.
- `team_update` allowlist (~3271) gains `subscription_status, current_period_end, grace_until, customer_email`.
- `_ensure_registry_indexes` (~320) gains `("Team", "stripe_customer_id")` — **Task 4b owns this index** (Task 8 owns only `("WebhookEvent", "event_id")` — review fix 14).
- `docs/registry-graph-schema.md` Team entity updated with `subscription_status, current_period_end, grace_until, customer_email` + new `WebhookEvent` entity (`event_id`, `type`, `received_at`, `team_id`).

**Files:**
- Modify: `tortoise/hosted_api.py` (`TeamInfoResponse` ~720, `team_info` ~1010)
- Modify: `tortoise/sdk.py` (`team_update` allowlist ~3271, `_ensure_registry_indexes` ~320)
- Modify: `docs/registry-graph-schema.md`
- Test: `tests/test_control_plane.py`

**Step 1:** Extend `tests/test_control_plane.py` — `test_team_info_returns_billing_fields` (GET /v1/team includes the 7 fields), `test_team_update_allowlist_billing_fields` (SET `subscription_status/current_period_end/grace_until/customer_email` succeeds). Run → FAIL.

**Step 2:** Extend `TeamInfoResponse` + `team_info`; extend `team_update` allowlist; add `("Team", "stripe_customer_id")` to `_ensure_registry_indexes`.

**Step 3:** Update `docs/registry-graph-schema.md` (Team: `subscription_status`, `current_period_end`, `grace_until`, `customer_email`; new `WebhookEvent` entity: `event_id`, `type`, `received_at`, `team_id`).

**Step 4:** Run `tests/test_control_plane.py` + `tests/test_hosted_api.py` → green.

---

### Task 5: Checkout + Portal endpoints (scoping Step 4)

**Intent:** Let an Owner start a Stripe Checkout for a valid price, persist the Stripe customer before redirect (survives a missed first event), and give existing subscribers a portal route — with a duplicate-subscription guard.

**Acceptance:**
- `POST /v1/billing/checkout` `{price_id}` → validates against catalog (400 unknown), resolves `customer_email` via the **fallback chain** (review fix 1): `Team.email` (set at register) → `APIKey.created_by` (provision-path teams have no `Team.email`; hosted_api.py ~455-464 stores `created_by` on the APIKey node) → **400 last-resort** with a clear message. Calls `StripeClient.create_customer(email)`; **persists `stripe_customer_id` + `customer_email` synchronously before redirect**; creates Checkout Session (`mode=subscription`, `customer=<customer_id>` — passes the created id, review fix 1, `client_reference_id=team_id`, `metadata.team_id`, `success_url`/`cancel_url` env-driven); returns `{checkout_url}`.
- Guard (two layers): (1) stored `subscription_status in {active, past_due, trialing}` → **409** "team already has an active subscription"; (2) **stale-mirror race** (review fix 5) — even with a clean stored mirror, call `list_subscriptions(customer_id)` before creating the session and reject 409 if ANY subscription is `active`/`trialing`/`past_due` (the mirror may read "free" between checkout creation and the webhook landing; Stripe remains the authority for money).
- `POST /v1/billing/portal` → creates portal session for existing customer, returns `{portal_url}`; 404 if no `stripe_customer_id`.
- Both endpoints require team auth (Bearer key) — NOT in SKIP_AUTH.

**Files:**
- Modify: `tortoise/hosted_api.py` (new routes + `CheckoutRequest`/`PortalResponse` models near TeamInfoResponse)
- Test: `tests/test_billing.py` (TestClient with monkeypatched `billing.StripeClient`)

**Step 1:** Write failing tests: `test_checkout_creates_customer_persists_before_redirect` (assert customer creation precedes Checkout call and Team node write), `test_checkout_provision_path_uses_api_key_created_by` (provision-path team with no `Team.email` → customer created with the APIKey node's `created_by` — review fix 1), `test_checkout_rejects_active_subscription_409`, `test_checkout_stale_mirror_race_409` (clean stored mirror but `list_subscriptions` returns an active sub → 409, no Checkout session created — review fix 5), `test_checkout_rejects_unknown_price_400`, `test_checkout_success_url_env_driven`, `test_portal_returns_url`, `test_portal_404_no_customer`. Run → FAIL.

**Step 2:** Implement the two routes + models; wire `create_customer(email)` → `customer=<id>` → sync-persist `stripe_customer_id` → `list_subscriptions` stale-mirror guard → `client_reference_id`/`metadata`/env URLs per acceptance.

**Step 3:** Run `python -m pytest tests/test_billing.py -v` → green.

---

### Task 6: `tortoise/notify.py` — Resend + Telegram (user decision 2026-08-08; prerequisite for Task 7)

**Intent:** Deliver the 4 billing notifications over BOTH agreed channels (Resend email to premiselabs.co + Telegram `@Premislabs_notifications_bot`), best-effort — a notification failure must never block or fail the webhook.

**Acceptance:**
- `notify_billing_event(kind, team, details)` where kind ∈ {`billing_upgrade`, `billing_downgrade`, `billing_payment_failed`, `billing_cancel`}; sends (a) Resend email to **`BILLING_NOTIFY_TO`** (ops inbox env var — review fix 8) via `POST https://api.resend.com/emails` (Bearer `RESEND_API_KEY`, from `billing@premiselabs.co`), (b) Telegram `sendMessage` to `TELEGRAM_CHAT_ID` (reuse `alert_store.telegram_send` pattern or `httpx`).
- Each channel wrapped in try/except + `logger.warning` — routed through `tortoise.security.redact_error` (review fix 9: **never log raw payloads, webhook bodies, `Stripe-Signature` headers, or secret values**); function never raises.
- Both channels gated on their secrets being set (absent secret → skip channel, log once).
- `RESEND_API_KEY` + `BILLING_NOTIFY_TO` + `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` consumed from env (Telegram already deployed; Resend + BILLING_NOTIFY_TO pending Fly secret per user decision — set in Task 3).

**Files:**
- Create: `tortoise/notify.py`
- Test: `tests/test_notify.py` (monkeypatched `httpx.post` / `urllib`)

**Step 1:** Write `tests/test_notify.py`: `test_resend_called_with_email_payload` (assert recipient comes from `BILLING_NOTIFY_TO`, not a hardcoded inbox — review fix 8), `test_telegram_called_with_message`, `test_resend_failure_swallowed`, `test_telegram_failure_swallowed`, `test_missing_secret_skips_channel`, `test_failed_notify_log_redacts_secret` (monkeypatched channel raises; captured log message contains no secret substring, e.g. the `RESEND_API_KEY` value — review fix 9). Run → FAIL.

**Step 2:** Implement `notify.py` (Resend via `httpx.Client`; Telegram via the existing `alert_store.telegram_send` function to avoid duplicating the pattern — import and reuse).

**Step 3:** Run `python -m pytest tests/test_notify.py -v` → green.

---

### Task 7: Webhook handler + event semantics (scoping Step 5 — the mirror's write path)

**Intent:** Receive Stripe's events, verify authenticity, dedup by event id, and apply the 4 event semantics so the registry mirror tracks Stripe exactly — with audit/analytics/notifications on first processing only.

**Acceptance:**
- `POST /webhooks/stripe` added to `RateLimitMiddleware.SKIP` AND `SKIP_AUTH` (public surface, signature-authenticated).
- Raw-body HMAC verify (Task 2) → 400 on bad signature/expired timestamp; 200-ack on unhandled event types.
- **Idempotency (SET-then-marker, resolves scoping P1-1 retry-drop race):** (1) idempotent Team SET (no marker condition); (2) `MERGE (:WebhookEvent {event_id})` with `ON CREATE` notification-flag; audit/analytics/notify fire ONLY when the marker was newly created. Replays → single processing (AC2). 500 on processing failure (Stripe retries; live up to 3 days).
- Semantics (all four, per scoping §Implementation Steps 5):
  - `checkout.session.completed`: bind `stripe_customer_id` (+`customer_email` from `customer_details.email`), set `subscription_id`, status `active`, resolve price via `get_subscription(session.subscription)` → `apply_limits(tier)`; on Stripe fetch failure still persist customer link + status (subscription.updated will confirm tier).
  - `invoice.payment_failed`: status `past_due`; `grace_until = current_period_end + 72h` (fallback now+72h if period missing); notify `billing_payment_failed`.
  - `customer.subscription.updated`: authoritative push — if `cancel_at_period_end == true` **keep tier until period end** (only mirror status/period); **if `status == "canceled"` (with or without `cancel_at_period_end`) revert to free, identical to `subscription.deleted`** (review fix 11); else apply price→tier + status (covers portal plan changes, AC3). Notify on tier/status change.
  - `customer.subscription.deleted`: revert tier→free + free limits, status `canceled` (AC4). Notify `billing_downgrade`/`billing_cancel`.
  - **Unknown price id** (`items[0].price.id` NOT in `STRIPE_PRICE_IDS` — review fix 7): log at error level (via `redact_error`) + fire a `billing_downgrade`-style ops notification (Resend + Telegram, Task 6), **keep stored tier + subscription_status** — never downgrade an active paid sub on an unparseable price; boot reconcile (Task 8) will revisit.
- Audit via `_async_audit` (ops `billing_upgrade/billing_downgrade/billing_payment_failed/billing_cancel`); analytics via `_track_analytics_event` after adding `plan, tier, interval, status` to `_ALLOWED_ANALYTICS_PROPS`.
- Notifications via `notify.notify_billing_event` (Task 6) — best-effort.
- **Log hygiene (review fix 9):** webhook/notify/StripeClient error logs route through `tortoise.security.redact_error`; never log raw webhook bodies, `Stripe-Signature` headers, or secret values.

**Files:**
- Modify: `tortoise/hosted_api.py` (route ~after `/internal/*` block; SKIP sets at ~264/566; `_ALLOWED_ANALYTICS_PROPS` ~2281)
- Test: `tests/test_billing.py::TestWebhook`

**Step 1:** Define the two module-level fixtures in `tests/test_billing.py` ONCE (consumed by Tasks 2/5/7/8 — review fix 16a): `billing_client` (TestClient + team registered through the real `/v1/register`, reusing the `TORTOISE_DB_PATH` embedded-DB pattern from `tests/test_quota.py`) and `signed_payload` (HMAC-SHA256 signature helper building `Stripe-Signature` headers over raw bytes). Then write failing webhook tests (monkeypatched `billing.StripeClient.verify_webhook_signature` + `get_subscription`): `test_webhook_checkout_completed_activates_team`, `test_webhook_replay_dedup_single_processing` (replay same event_id → one apply_limits/notify/audit), `test_webhook_bad_signature_400`, `test_webhook_expired_timestamp_400`, `test_webhook_payment_failed_sets_grace`, `test_webhook_cancel_at_period_end_keeps_tier`, `test_webhook_subscription_updated_canceled_reverts` (status `canceled` without `cancel_at_period_end` → revert to free — review fix 11), `test_webhook_unknown_price_keeps_tier_and_notifies` (price id not in catalog → tier/status preserved, ops notification fired — review fix 7), `test_webhook_deleted_reverts_free`, `test_webhook_unhandled_type_200`, `test_webhook_processing_failure_500`, `test_webhook_audit_and_analytics_recorded`, `test_webhook_failure_log_redacts_secret` (failed notify path's logged message contains no secret substring — review fix 9). Run → FAIL.

**Step 2:** Implement the route: read `await request.body()`, `request.headers["stripe-signature"]`, verify → dispatch by `event["type"]` → per-event handler functions (module-level in hosted_api or `billing.handle_event`). Wire SKIP lists, analytics allowlist, audit calls, notify calls.

**Step 3:** Run `python -m pytest tests/test_billing.py -v` → green (plus no regression in `tests/test_hosted_api.py`).

---

### Task 8: Boot reconcile + registry indexes (scoping Step 7)

**Intent:** Repair mirror drift at startup — including teams that only have `stripe_customer_id` (missed `checkout.session.completed` blind spot) — without ever breaking boot/health.

**Acceptance:**
- `_lifespan` (hosted_api.py ~89) runs a best-effort reconcile pass for each team with `subscription_id` OR `stripe_customer_id`, `reconcile_team(...)`; whole pass wrapped in try/except → `logger.warning` (via `redact_error`), never raises (health check / release_command unaffected — AC7).
- **Never awaited before lifespan yield** (review fix 3, #545 lesson — delayed bind breaks Fly health-grace/release_command): the pass MUST run in a daemon thread (mirror the `_prewarm_embeddings` pattern) or `asyncio.create_task` — never awaited inline before the lifespan yields. Budget: hard wall-clock cap (~20s) + per-call Stripe timeouts on `StripeClient`; a hanging Stripe API must not extend startup.
- `_ensure_registry_indexes` gains `("WebhookEvent", "event_id")` only — **Task 8 owns this index**; `("Team", "stripe_customer_id")` is owned by Task 4b (no duplicate wording — review fix 14).
- Unknown price id during reconcile (review fix 7): log at error level + ops notification, keep stored tier + `subscription_status` (Task 7 semantics).

**Files:**
- Modify: `tortoise/hosted_api.py` (`_lifespan`), `tortoise/sdk.py` (`_ensure_registry_indexes`)
- Test: `tests/test_billing.py` (uses the `billing_client`/`signed_payload` fixtures from Task 7)

**Step 1:** Write `test_boot_reconcile_repairs_drift` (out-of-band tier change corrected) + `test_boot_reconcile_repairs_customer_only_team` + `test_boot_reconcile_non_fatal_on_stripe_error` (monkeypatched client raises → lifespan completes, app boots) + `test_boot_reconcile_hanging_stripe_never_blocks_boot` (monkeypatched client sleeps past the budget → lifespan completes in <1s; daemon thread not awaited — review fix 3). Run → FAIL.

**Step 2:** Implement the reconcile pass in `_lifespan` (daemon thread mirroring `_prewarm_embeddings`; wall-clock budget ~20s; per-call Stripe timeouts; try/except → `redact_error` log) + add the `("WebhookEvent", "event_id")` index.

**Step 3:** Run `python -m pytest tests/test_billing.py -v` → green.

---

### Task 9: Dashboard UI — upgrade CTA, portal, plan display (scoping Step 8; UX fork-mode)

**Intent:** Give the Owner the visible upgrade surface and the post-purchase plan/status display — the user-facing half of the loop, per the UX gate (fork-mode: the component IS the implementation).

**Acceptance:**
- Tier card shows plan (`team.tier`), subscription status, and enforced limits (`max_points`, `max_api_keys`) from the extended `GET /v1/team` (S6).
- "Upgrade" CTA rendered only when no active subscription (`subscription_status` not in `{active, past_due, trialing}`); clicking → `POST /v1/billing/checkout {price_id}` (monthly default price) → opens `checkout_url`; button disabled while a checkout is pending (duplicate-subscription guard) — pending cleared when returning via `?checkout=cancelled` or on the next team refresh.
- "Manage billing" link (active subscribers) → `POST /v1/billing/portal` → opens `portal_url` in new tab.
- **Create the 402 → upgrade-hint renderer** (review fix 10a — verified: it does NOT exist in main.jsx today; only the tier card at main.jsx:390 exists). Any 402 from quota renders the upgrade hint (plan/status + plans/portal link); the hint is also reachable from the checkout guard's 409/400 responses (final verification P2 — checkout returns 409/400, not 402).
- **Success-return path** (review fix 10b): `BILLING_SUCCESS_URL` template includes `?session_id={CHECKOUT_SESSION_ID}`, `BILLING_CANCEL_URL` includes `?checkout=cancelled` (env-driven, Task 3). On mount, parse `window.location.search` for both params: `session_id` present → trigger a team refetch with a short retry loop (e.g. 5 × 2s) until `subscription_status` flips to `active` (webhook lands seconds later) — then clear `session_id` from the URL; `checkout=cancelled` present → clear pending + drop the param.
- **Grep-verifiable anchors** (review fix 10b): `upgrade()` and `manageBilling()` handlers call `/v1/billing/checkout` and `/v1/billing/portal` respectively; CTA/portal render gated by `subscription_status` guards (`!== 'active' && !== 'past_due' && !== 'trialing'` for CTA; inverse for Manage billing); retry loop present in the mount effect.

**Files:**
- Modify: `website/apps/dashboard/src/main.jsx` (tier card + handlers + new 402 → upgrade-hint renderer; ~460 lines)
- Test: manual clickthrough (fork-mode — no SPA test harness in repo); E2E-3-D covered in Task 10

**Step 1:** Add `team` plan/status/limits render to the overview tier card; add `upgrade()` + `manageBilling()` handlers calling the new endpoints; add the 402 → upgrade-hint renderer (new block on the shared error path — does not exist today).

**Step 2:** Add the CTA/portal buttons with the active-subscription and pending-checkout guards; parse `window.location.search` for `session_id` (refetch loop until `subscription_status` flips) and `checkout=cancelled` (clear pending); strip both params from the URL after handling.

**Step 3:** Verify against the dev deploy (`npm run build` in `website/apps/dashboard` per its local README; manual clickthrough with a test team) — acceptance via the Task 10 E2E and the coordinator's app-test pass.

---

### Task 10: E2E-3-D + regression sweep (scoping Step 9)

**Intent:** Prove the full money loop end-to-end (upgrade → webhook → enforcement → portal) against Stripe test mode, and confirm zero regression across the suite.

**Acceptance:**
- `tests/e2e/test_billing_upgrade.py` marked `@pytest.mark.stripe` (mirrors the existing `postgres` marker pattern): **genuinely live leg only** (review fix 16c) — registers a team through the real `/v1/register`, creates a real Checkout session against Stripe test mode, asserts HTTP 200 + `checkout_url` returned + `stripe_customer_id` persisted on the Team node. **No simulated webhooks here.**
- The **4-event semantics stay in Task 7 integration tests** (fixture payloads + monkeypatched StripeClient) — do NOT re-test simulated webhooks twice. AC1–AC5 verification lives in `tests/test_billing.py::TestWebhook*`, not E2E.
- Skipped cleanly (no error) when `STRIPE_TEST_SECRET_KEY` / `STRIPE_TEST_WEBHOOK_SECRET` absent (skip-guard on `STRIPE_TEST_*` keys).
- Full regression: `python -m pytest tests/ -m "not postgres and not stripe" -v` green; live test-mode run (with keys) green.

**Files:**
- Create: `tests/e2e/test_billing_upgrade.py`
- Test: pytest (marked)

**Step 1:** Write the E2E: register → real `POST /v1/billing/checkout` → assert 200 + `checkout_url` + persisted `stripe_customer_id`; `@pytest.mark.stripe` marker + skip guard on missing `STRIPE_TEST_*` keys.

**Step 2:** Run the marker-gated subset with test keys → the upgrade flow passes (tier=pro active, limits raised); run `-m "not postgres and not stripe"` → no regressions.

**Step 3:** Update `MEMORY.md` only if a coding gotcha surfaced (≤150-line budget — do not log this plan).

---

## 4. Verification Plan

Per `test-routing` (domain: code, complexity Standard): unit + integration are the primary layers; E2E gated behind `@pytest.mark.stripe` (Stripe test-mode keys — mirror the existing `postgres` marker pattern); UX verification = dashboard clickthrough (fork-mode, no SPA harness); config/research domains not applicable beyond the workflow edits (verified by grep in Task 3).

| Layer | Scope | Command |
|-------|-------|---------|
| Unit | billing.py (catalog, signature math, effective_tier, limits, notify) | `python -m pytest tests/test_billing.py tests/test_notify.py -v` |
| Integration | webhook endpoint, checkout/portal, tier enforcement + REST/MCP quota parity, reconcile (FalkorDBLite) | `python -m pytest tests/test_billing.py tests/test_hosted_api.py tests/test_control_plane.py tests/test_quota.py -v` |
| Regression | full suite | `python -m pytest tests/ -m "not postgres and not stripe" -v` |
| E2E (gated) | E2E-3-D **live checkout leg** (webhook semantics covered in Task 7 integration) | `python -m pytest tests/e2e/test_billing_upgrade.py -v` (needs `STRIPE_TEST_*` keys) |

---

## 5. Acceptance Criteria (scoping AC1–AC10 → tasks)

| AC | Criterion | Covered by |
|----|-----------|-----------|
| AC1 | `GET /v1/team` shows upgraded plan + limits within seconds of `checkout.session.completed` | Tasks 4a/4b, 5, 7 (`test_webhook_checkout_completed_activates_team`, `test_team_info_returns_billing_fields`) |
| AC2 | Replayed webhook → processed exactly once; no double subscription | Task 7 (`test_webhook_replay_dedup_single_processing`) |
| AC3 | Portal plan change → limits update via `subscription.updated` | Task 7 (`test_webhook_cancel_at_period_end_keeps_tier` + plan-change case) |
| AC4 | Cancel keeps tier to period end, reverts on `subscription.deleted` | Task 7 |
| AC5 | `past_due` + grace → degraded after `grace_until`; upgrade flips back | Tasks 2, 4a, 7 (`test_effective_tier_*`, `test_webhook_payment_failed_sets_grace`) |
| AC6 | Bad signature/expired → 400; unknown event → 200-ack | Task 7 |
| AC7 | Boot reconcile repairs out-of-band change + customer-only team | Task 8 |
| AC8 | Checkout with active subscription → 409; arbitrary price_id → 400 | Task 5 |
| AC9 | Per-tier limits enforced (free 10k cap, pro raises it); never below free | Tasks 1, 2, 4a (GAP-A/B) |
| AC10 | `billing_*` audit + analytics events recorded | Task 7 |

---

## 6. Runtime Prerequisites

| Prerequisite | Status | Notes |
|--------------|--------|-------|
| Stripe account | **EXISTING** Apresto Internal gmail (same as El Dato) — **do NOT create a new account** | Test + live mode keys; owner documents test→live switch ownership |
| Webhook endpoint registered | **Ops (owner)** | `https://api.premiselabs.co/webhooks/stripe`; events: `checkout.session.completed`, `invoice.payment_failed`, `customer.subscription.updated`, `customer.subscription.deleted` |
| Customer Portal enabled | **Ops (owner)** | Dashboard portal config; return_url fallback set |
| 8 price IDs created | **Ops (owner)** | 4 tiers × monthly/annual; annual = −20%; ids pasted into `STRIPE_PRICE_IDS` env JSON; Free prices display-only (never hit Checkout) |
| `STRIPE_SECRET_KEY` / `STRIPE_WEBHOOK_SECRET` / `STRIPE_PRICE_IDS` | Fly secrets via deploy-hosted.yml | Task 3 |
| `RESEND_API_KEY` + `BILLING_NOTIFY_TO` | GH secret (set 2026-08-08) → **pending Fly secret** + `.env.example` | Task 3 |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Already deployed (backup sweep #596) | Reused by notify.py |
| `httpx` pinned | Task 3 | Currently transitive via fastmcp |
| Single-worker assumption | Documented | Reconcile + in-process patterns; reconcile non-fatal |

---

## 7. Rejected Alternatives (condensed from scoping — full rationale in `docs/scoping-310-stripe-billing.md`)

- **Approach B — Supabase-first billing ledger (dual-write):** heavier MVP; #669 schema undecided; dual-write is a consistency bug source.
- **Approach C — Stripe-hosted stateless read-through (Payment Links + TTL cache):** cannot bind server-side team_id; couples enforcement hot path to Stripe; no durable record.
- **Inline "webhook → tier field only" (issue's own framing):** tier had no causal power at scoping time; now the enforcement half exists but the value still only flows through `apply_limits` + GAP-A/B fixes — a bare field write remains worthless.

---

## 8. UX Design Decisions

Recorded per the UX gate (01.5) — decisions were made during issue-scoping + user approval (2026-08-08); fork-mode (the prototype IS the implementation — modify `main.jsx` directly, no separate HTML prototype):

| # | Decision Type | User Choice | Rationale |
|---|---|---|---|
| 1 | Upgrade affordance | Upgrade CTA on tier card when no active subscription; "Manage billing" → portal for subscribers | Standard billing UX; duplicate-subscription guard (disable CTA while checkout pending) |
| 2 | Plan display | Tier + subscription status + enforced limits from extended `GET /v1/team` | Transparency drives upgrade (limits are the value) |
| 3 | Grace UX | Local enforcement window (72h) — service degrades after `grace_until`; dashboard shows status | Owner decision 3 (2026-08-08): grace = local enforcement, not display-only |
| 4 | Notification | Resend email + Telegram bot — BOTH channels | Owner decision 2: best-effort, never blocks webhook |
| 5 | Prototype mode | Fork-mode — component is the implementation | UX_RATING=medium; no new HTML prototype |

**Pending:** none — all UX decisions resolved at scoping/user gate.

---

## 9. Notes / Open Items

1. **GAP-B mapping confirmation (owner):** `max_points := max_graph_nodes` (10k/25k/100k/600k) because the `points` quota counts graph nodes. Premise note (review fix 15): the scoping's 10k/10k/50k/200k values DO match `included_write_ops_per_month` exactly (free/solo/pro/team = 10k/10k/50k/200k) — the mapping to `max_graph_nodes` is **enforcement-correct** for the node-counting points counter, not a pricing mismatch. If the owner prefers write-ops-based caps, metering (#296/#308) is a prerequisite — out of scope here.
2. **No defensive period-end degrade (Task 2, review fix 6):** the former "`active` + `current_period_end` passed → free" branch was **removed** — Stripe auto-renews, so it fired on every renewal before the webhook landed (a recurring false-downgrade outage). Missed-event drift is covered by the webhook (seconds) + boot reconcile (Task 8); `past_due`/`grace_until` remains the only time-based degrade.
3. **Webhook marker race (Task 7):** SET-then-marker is race-safe against the retry-drop case; two *simultaneous* deliveries of the same event could double-notify (Stripe delivers sequentially; single worker). Documented, accepted for MVP.
4. **Price resolution after Checkout (Task 7):** `checkout.session.completed` payload does not embed line items; tier comes from `get_subscription(session.subscription)` with a graceful fallback to the subsequent `customer.subscription.updated` event. First payment → limits may take one extra event round-trip in the worst case (still within AC1's "seconds").
5. **Stale test on main:** `tests/test_pricing_tiers.py:43` (`== 1000`) fails against main's pricing.json (`10000`) — pre-existing, fixed in Task 1.
6. **Stripe dashboard ops checklist** (owner, before go-live): register webhook (4 event types), enable portal config, create 8 price IDs, set `STRIPE_PRICE_IDS`, add `RESEND_API_KEY` to Fly secrets, confirm test→live key switch ownership (shared-account governance with El Dato: statement descriptor, disputes, key rotation).
7. **Escalation watch (scoping):** if Task 4a touches auth beyond `get_current_team`/`_check_team_limit`, escalate to Complex rather than compressing.
8. **Adjacent issues (do NOT absorb):** metering/overage (#296/#308), #669 Supabase migration, MCP write limits, `last_used_at` TODO, fail-open→fail-closed count audit — filed separately per scoping §Discovery.
9. **Unknown price id (review fix 7):** webhook/reconcile treat an unparseable `items[0].price.id` as "leave the paid mirror alone" — error log (via `redact_error`) + ops notification (Resend + Telegram), stored tier/status preserved. An unrecognized price never downgrades an active sub; if it persists across reconcile cycles, the owner resolves the catalog mapping.

---

## Execution Handoff

- **Plan-review:** NOT run here — the coordinator runs `plan-review docs/plans/2026-08-08-310-stripe-billing.md --issue 310 --tier standard` (workflow 05). Do not execute before `<!-- plan-review: status=clean -->` is present.
- **Execution mode:** **Parallel session** — 10 tasks (>8 threshold per workflow 05). Open a new session in the `feat/310-stripe-billing` worktree and use the `executing-plans` skill on this doc.
- **Worktree:** `.worktrees/310-stripe-billing` (branch `feat/310-stripe-billing`, based on origin/main @ 62d8477). No git operations were performed during planning (review gate).
- **Label:** apply `planned` after plan-review passes (remove `planning`).
- **Review cycle log:** plan-review cycle 1: 2 P1 + 16 P2 → fixed.
<!-- plan-review: status=clean (cycle 2: 2 P1 + 16 P2 fixed, final verification CLEAN after 6 spec-precision edits) -->
