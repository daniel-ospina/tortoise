<!-- issue-scoping: v5.1 double diamond + verify -->
## Confirmed Problem

**#310 ships a working MVP billing loop — Checkout → tier-aware enforcement → webhook-driven subscription state mirror — so that paying a Stripe subscription actually changes runtime team limits, with a billing portal and grace/revert behavior. Metering, overage billing, and the Supabase control-plane migration (#669) are explicitly out of scope (file separately).**

**Root cause (verified in code):** the `tier` field is **inert**. `_check_team_limit` (hosted_api.py:505) enforces hardcoded defaults (`points=1000, api_keys=20, sessions=1000`) and never reads `tier`; `get_current_team` (hosted_api.py:491) returns only `tier, max_users, max_graphs, max_teams` — never the fields `_check_team_limit` actually enforces. Team nodes are created with `tier:'free'/1/1/1` from constants, not pricing. So the issue as written — "webhook sets `team.subscription_tier`" — would move real money through Stripe while delivering **zero user-visible change**: a paying team still hits the invisible 1000-point ceiling. Shipping #310 without the tier-aware enforcement fix is a chargeback/refund liability, not a feature.

**Quality over convenience:** The easy path (issue's own framing) is writing `tier` from a webhook. The right path is fixing the tier→limits lever (the value half of the loop) *and* building the subscription state mirror (Stripe as billing authority, registry Team node as enforcement mirror), with idempotent webhook handling.

### Why This Framing
- The issue's framing ("bolt Stripe onto the tier field") was **rejected** — E1 proves the field has no causal power. Both diverge agents independently landed this kill-shot.
- Full F2 (5-event idempotent set + reconciliation jobs + dunning) over-scopes a standard project — trimmed to MVP event set (below) with a single reconcile path.
- "Supabase-first" (F4) rejected for #310: #669 is an OPEN complex epic with no landing date; the confirmed problem must not block revenue on it. (Note: #669 *depends on* this epic's tier state — #296 → #310 — so shipping billing state in the registry first is the correct ordering; the sound rejection rationale is that #669's Supabase schema is undecided, making Stripe-against-Supabase speculative.)
- "Metering-first" (F5) rejected for #310: the $5/10k write-op overage is the largest published revenue lever but nothing counts write ops; metering is a platform investment for #296/#308, not a standard project. #310 flips *limits*, it does not *count usage*.
- "Manual provisioning + receipts" (F3) rejected: same pay-for-nothing failure, plus churn leakage (no revert), no portal, no grace.

### Falsification Check
This definition is wrong if any of:
1. A tier→limits mapping exists in any reachable code path (falsifies E1 → collapses toward F1). **Not found.**
2. `max_api_keys/max_points/max_sessions` are settable via team creation/update (shrinks enforcement work to config). **Not found** — `team_update` allowlist (sdk.py:2697) excludes them.
3. Stripe keys already present in deploy env or an existing webhook/durable-queue infra. **Not found** — deploy secrets are `FASTAPI_INTERNAL_KEY, TORTOISE_SECRET_PEPPER, FALKORDB_CLOUD_URI` only.
4. #669 lands first → F4 becomes viable and the FalkorDB mirror is obsolete. **#669 OPEN as of scoping.**

### Confidence: 78
Core diagnosis (inert tier, no metering, no pricing home, no webhook infra, identity gap) is directly code-verified. Boundary uncertainty: exact webhook lifecycle depth, Apresto Stripe account transferability (unverifiable from this repo), owner-bridge acceptability.

## Verification Gates

### problem-verify: 1 cycle — NO P0; 3 P1s incorporated
- **problem-diverge:** 2 sub-agents (alternatives + devil's advocate). 5 framings generated; adversarial kill-shot (inert tier field) verified correct.
- **problem-converge:** 1 sub-agent; trimmed F2 selected, confidence 78; F1/F3/F4/F5 rejected with rationale.
- **P1s incorporated (controller):** (1) enforcement mechanism plumbing must be explicit — `get_current_team` must return the enforced fields and `_check_team_limit` must resolve limits from a tier→limits map; (2) idempotency/orphan guard was dropped in converge — restored: event-ID dedup + one-active-subscription guard on Checkout creation; (3) owner bridge made honest — solve identity at point of truth (`checkout.session.completed.customer_details.email` → store on Team node) and name the notification channel explicitly.

### solution-verify: 1 cycle — NO P0; 4 P1s incorporated
- **solution-diverge:** 1 sub-agent; 3 architecturally distinct approaches (graph mirror / Supabase ledger / stateless read-through).
- **solution-converge (controller):** Approach A selected (below). B and C rejected with rationale.
- **P1s incorporated:** (1) atomic dedup-vs-SET ordering (retry-drop race) — single atomic Cypher write guarded by `WHERE NOT EXISTS (WebhookEvent {event_id})`, or marker-last with idempotent SET; (2) persist `stripe_customer_id` **synchronously at Checkout creation** (before redirect) so reconcile survives a missed first event; (3) `customer.subscription.updated` with `cancel_at_period_end=true` keeps tier until period end vs `customer.subscription.deleted` reverts — distinct semantics; (4) `.github/workflows/deploy-hosted.yml` must add `STRIPE_SECRET_KEY` + `STRIPE_WEBHOOK_SECRET` (and `STRIPE_PRICE_IDS` if env-driven) to both the secrets-verify step and `flyctl secrets set`.

## Plan

### Problem Statement
Tortoise Hosted has no way for customers to pay. The Stripe integration as scoped in #310 would write a `tier` field that nothing reads — enforcement is hardcoded and tier-blind, so paid tiers would deliver no benefit. The MVP must deliver a loop: dashboard Upgrade → Stripe Checkout → webhook → team actually gets higher limits → portal/grace/cancel handled. Subscription state lives on the FalkorDB registry Team node (already has `stripe_customer_id`/`subscription_id` fields) as a **derived mirror** of Stripe, with Stripe as billing authority. Billing state is designed lift-and-shift for #669.

### Proposed Solution — Approach A: Graph-native billing mirror with lazy grace enforcement

**Architecture:** Stripe = authority for *money*; FalkorDB registry Team node = authority for *enforcement*. Single data plane (graph), single process (webhook writes graph; requests read graph), no scheduler, no queue, no second DB. Grace is enforced **lazily** at request time in `get_current_team`/`_check_team_limit` — no cron. Reconcile repairs drift at boot.

**New module: `tortoise/billing.py`**
```python
# ── Tier → limits mapping (canonical pricing — mirrors product/pricing.json) ──
TIERS = {
    "free": {"max_graphs": 1, "max_users": 1, "max_api_keys": 2,
             "max_points": 10_000, "max_sessions": 1_000, "overage": False},
    "solo": {"max_graphs": 2, "max_users": 1, "max_api_keys": 5,
             "max_points": 10_000, "max_sessions": 1_000, "overage": False},
    "pro":  {"max_graphs": None, "max_users": 2, "max_api_keys": 10,
             "max_points": 50_000, "max_sessions": 1_000, "overage": True},
    "team": {"max_graphs": None, "max_users": None, "max_api_keys": 20,
             "max_points": 200_000, "max_sessions": 1_000, "overage": True},
}
# Price catalog: price_id → (tier, interval). Loaded from STRIPE_PRICE_IDS env
# JSON (8 entries: 4 tiers × monthly/annual, annual = -20%, annual_default: true).

class StripeClient:  # thin httpx wrapper; signature verify via hmac.compare_digest
    def create_checkout_session(team_id, price_id, customer_email, success_url, cancel_url) -> str
    def create_portal_session(customer_id, return_url) -> str
    def get_subscription(subscription_id) -> dict
    def list_subscriptions(customer_id) -> list[dict]
    def get_customer(customer_id) -> dict
    def verify_webhook_signature(payload, sig_header, secret, tolerance_s=300) -> dict

def effective_tier(team: dict, now=None) -> str:
    """Lazy grace: past_due + now > grace_until → 'free';
    current_period_end passed with no webhook yet → defensive 'free'."""
def limits_for_tier(tier: str) -> dict: ...
def apply_limits(sdk, team_id: str, tier: str) -> None: ...
def reconcile_team(sdk, team_id: str, force: bool = False) -> None:
    """Fetch subscription from Stripe; repair mirror (idempotent absolute SETs)."""
```

**Team node schema additions** (registry graph, via `team_update` allowlist extension + direct webhook writes):
`stripe_customer_id` (exists), `subscription_id` (exists), **`subscription_status`** (`active|past_due|canceled|trialing|incomplete|unpaid`), **`current_period_end`**, **`grace_until`** (set on `invoice.payment_failed` = `current_period_end + 72h` fallback), plus derived **`max_api_keys`/`max_points`/`max_sessions`** written alongside `tier` in the same Cypher SET.

### Implementation Steps

**Step 1 — Canonical pricing on main (prerequisite, absorbs E4):** Land `product/pricing.json` (owner-confirmed version: Free 10k write ops post-#662, Solo $9, Pro $25, Team $149, annual 20%, overage $5/10k) onto main and mirror the tier→limits table in `tortoise/billing.py` with a test asserting parity. The server currently reads no pricing artifact — the 8 price IDs need a committed home (`STRIPE_PRICE_IDS` env JSON, validated against the catalog).

**Step 2 — Tier-aware enforcement (the value half):** Extend `get_current_team`'s registry query to return `tier, max_users, max_graphs, max_teams, max_api_keys, max_points, max_sessions, subscription_status, current_period_end, grace_until`. Rewire `_check_team_limit` to resolve limits from `limits_for_tier(team["tier"])` instead of hardcoded `or 1000/20/1000` defaults. Extend `TeamInfoResponse` with the same fields for dashboard display. Add 402 detail with upgrade guidance + portal link. Never degrade below `free`.

**Step 3 — Stripe client + secrets + deps:** Add `tortoise/billing.py` (`StripeClient` over `httpx`, already imported in-file; pin `httpx` in requirements.txt — currently transitive/unpinned via fastmcp). Add `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_IDS` to `.env.example`, `deploy-hosted.yml` secrets-verify step, and the `flyctl secrets set` command. Ops checklist: register webhook endpoint in Stripe dashboard with event types `checkout.session.completed`, `invoice.payment_failed`, `customer.subscription.updated`, `customer.subscription.deleted`; enable Customer Portal config; create 8 price IDs (4 tiers × monthly/annual, annual −20%, `annual_default: true`). **Do NOT create a new Stripe account** — use the existing Apresto Internal gmail account (same as El Dato); document test/live key separation.

**Step 4 — Checkout + Portal endpoints (hosted_api.py):**
- `POST /v1/billing/checkout` — body `{price_id}`; validates price_id against catalog; **persists `stripe_customer_id` synchronously before redirect** (create-or-fetch Customer via `Team.email` — set at `/v1/register`; `APIKey.created_by` fallback; `customer_details.email` from webhook for provision-path teams); **guards: one active subscription per team** (reject if `subscription_status in {active, past_due, trialing}`); returns `{checkout_url}`. `success_url`/`cancel_url` env-driven → dashboard.
- `POST /v1/billing/portal` — creates portal session for existing customer, returns `{portal_url}` (existing subscribers route here, not new checkout).
- Extended `GET /v1/team` already returns plan/status (Step 2).

**Step 5 — Webhook handler (hosted_api.py):**
`POST /webhooks/stripe` — added to `RateLimitMiddleware.SKIP` and `SKIP_AUTH` (public surface). Flow:
1. Verify `Stripe-Signature` (HMAC over raw body, `STRIPE_WEBHOOK_SECRET`, `hmac.compare_digest`, **±5 min timestamp tolerance** — pattern from `_check_internal` at hosted_api.py:277).
2. **Idempotency:** single atomic Cypher write — `MERGE/CREATE (:WebhookEvent {event_id:$id})` + Team SET **guarded by `WHERE NOT EXISTS (WebhookEvent {event_id})`** so a retry mid-write cannot drop the upgrade (fixes retry-drop race). Return 200 only after durable write; 500 on processing failure (Stripe retries); 400 on bad signature; 200-ack on unhandled event types.
3. Event semantics:
   - `checkout.session.completed` (mode=subscription) → link customer↔team (store `customer_details.email`), set `subscription_status='active'`, `tier` + all limits.
   - `invoice.payment_failed` → `subscription_status='past_due'`, `grace_until = current_period_end + 72h` (fallback to `current_period_end` if missed); notify Owner.
   - `customer.subscription.updated` → authoritative state push: if `cancel_at_period_end=true` **keep tier until period end**; else apply status/plan changes (covers portal-initiated plan changes — which fire this event, NOT session.completed).
   - `customer.subscription.deleted` → revert `tier='free'` + free limits at period end.
4. Audit each event via `_async_audit` (`billing_upgrade`, `billing_downgrade`, `billing_payment_failed`, `billing_cancel`) + analytics via `_track_analytics_event` (add `plan`/`tier`/`interval` to `_ALLOWED_ANALYTICS_PROPS`).

**Step 6 — Grace/revert enforcement (lazy):** In `get_current_team`/`_check_team_limit`: `effective_tier(team)` degrades to free when `past_due` past `grace_until`, or `current_period_end` passed with no webhook (defensive). No scheduler. Grace semantics documented: **grace is a local enforcement window, not Stripe's dunning** — Stripe retries internally; local window is the app's own "keep service during payment trouble" period. (If MVP wants grace display-only, state that explicitly — default is local enforcement.)

**Step 7 — Boot reconcile:** In `_lifespan` (hosted_api.py:71 seam): best-effort, **non-fatal** on Stripe outage (must not break health check / release_command); for teams with `subscription_id` **or `stripe_customer_id`** (first-event blind spot: a missed `checkout.session.completed` leaves no `subscription_id`; customer-based match repairs it), fetch subscription(s) and repair mirror. Add `Team.stripe_customer_id` + `WebhookEvent.event_id` to `_ensure_registry_indexes`.

**Step 8 — Dashboard UI (website/apps/dashboard/src/main.jsx):** Upgrade CTA on the tier card (only when no active subscription; route existing subscribers to portal, disable CTA while a checkout is pending to prevent duplicate subscriptions); "Manage billing" → `POST /v1/billing/portal`; render current plan + status + enforced limits from extended `GET /v1/team`.

**Step 9 — Tests:** New `tests/test_billing.py` (monkeypatched `StripeClient`, FalkorDBLite): signature verify (+tampered payload, +expired timestamp), webhook idempotency (replay → single processing), event semantics (all 4 events + cancel_at_period_end), lazy grace degrade, limits resolution per tier, checkout guard (active subscription → 409), reconcile repair. Extend `tests/test_hosted_api.py` (tier-aware 402 + plan display) and `tests/test_control_plane.py` (new `team_update` allowlist fields). E2E (E2E-3-D, designed by #7738): upgrade flow → Pro tier active → subscription billing.

### Acceptance Criteria
1. **AC1:** Paying team's `GET /v1/team` shows upgraded plan + limits within seconds of `checkout.session.completed`.
2. **AC2:** Replayed webhook event → processed exactly once (event-ID dedup); no double subscription creation.
3. **AC3:** Portal plan change (Solo→Pro) → limits update (via `subscription.updated`).
4. **AC4:** Cancel → tier kept until period end (`cancel_at_period_end`) → reverted on `subscription.deleted`.
5. **AC5:** Failed payment → `past_due` + grace window → degraded to free after `grace_until`; upgrade during grace flips back on next event.
6. **AC6:** Bad signature / expired timestamp → 400; unknown event type → 200-ack.
7. **AC7:** Boot reconcile repairs an out-of-band Stripe change (and a team with only `stripe_customer_id`).
8. **AC8:** Checkout with an active subscription → rejected (409); arbitrary price_id → 400.
9. **AC9:** `_check_team_limit` enforces per-tier limits (free hits 10k-point cap; pro raises it); never degrades below free.
10. **AC10:** `billing_*` audit events + analytics events recorded.

### Testing Strategy
- **Unit:** `tests/test_billing.py` — StripeClient mock, signature math, effective_tier, limits_for_tier, pricing-parity test.
- **Integration:** `tests/test_hosted_api.py` — webhook endpoint with monkeypatched client; tier-aware 402; plan display.
- **Regression:** `tests/test_control_plane.py`, existing hosted tests pass unchanged.
- **E2E:** E2E-3-D (upgrade flow → Pro active) per #7738 design; Stripe test-mode keys.

### Runtime Prerequisites
- Stripe account (EXISTING Apresto Internal gmail account — no new account), test-mode + live-mode keys, webhook endpoint registered (4 event types), Customer Portal enabled, 8 price IDs created.
- `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_IDS` as Fly secrets via `deploy-hosted.yml`.
- `product/pricing.json` landed on main (canonical, owner-confirmed 2026-08-07: Free 10k ops post-#662).
- `httpx` pinned in requirements.txt.
- Single-worker assumption (reconcile + in-process patterns) remains documented; reconcile must be non-fatal.

## Rejected Alternatives

### Approach B — Supabase-first billing ledger mirrored to graph (dual-write)
**Rejected because:** Heavier MVP for a standard project (2 new tables + RLS + dual-write discipline + background reconciler). The durable-ledger advantage (survives #669, queryable billing records) is real but conditional on #669's timeline, which is OPEN and undecided on schema. The confirmed problem's scope (metering out, #669 out) makes the ledger speculative insurance, and dual-write is a genuine source of consistency bugs.
**When this WOULD have been better:** If #669 lands within ~a quarter — then billing built directly in Supabase is the end-state and the graph mirror becomes vestigial. **Migration path if A is later abandoned:** graph billing fields seed future Supabase tables via the same reconcile pattern; event history reconstructible from Stripe's List Events API.

### Approach C — Stripe-hosted, stateless read-through (Payment Links + TTL cache)
**Rejected because:** Structurally cannot close the payment→team loop — Payment Links cannot bind server-side `team_id` metadata (no customer creation, no metadata association), violating the confirmed problem's "webhook-driven subscription state mirror." Also couples the enforcement hot path to Stripe API latency/availability (fail-open vs fail-closed decision with product consequences), leaves no durable billing record (orphans undetectable), and Payment Links are less expressive (no backend checkout customization).
**When this WOULD have been better:** If #669 landed immediately AND the team accepted read-through enforcement with an explicit fail-open decision AND no event-driven behavior was needed. None hold.

### Inline-extension of the issue's own approach (webhook → tier field only)
**Rejected because:** The tier field has no causal power (E1). Shipping it = real money for zero value = chargeback liability. This is the convenience path the double diamond exists to reject.

## Wiring Check

| Touch Point | Type | Covered By | Status |
|-------------|------|------------|--------|
| Registry Team node (`stripe_customer_id`, `subscription_id` + new `subscription_status`, `current_period_end`, `grace_until`, `max_*`) | Graph DB | Steps 2, 5 (webhook SET) + `team_update` allowlist ext (Step 1) | ✅ |
| `:WebhookEvent` dedup nodes + indexes (`event_id`, `Team.stripe_customer_id`) | Graph DB | Step 5, 7 (`_ensure_registry_indexes`) | ✅ |
| `get_current_team` + `_check_team_limit` (tier-aware limits, lazy grace) | API/auth path | Step 2 | ✅ |
| `POST /webhooks/stripe` (SKIP + SKIP_AUTH + signature verify) | API | Step 5 | ✅ |
| `POST /v1/billing/checkout`, `POST /v1/billing/portal` | API | Step 4 | ✅ |
| `GET /v1/team` + `TeamInfoResponse` (plan/status/limits) | API | Step 2 | ✅ |
| Stripe external API (Checkout, Portal, Subscriptions, webhook verify) | External | Steps 3–5 (`tortoise/billing.py`) | ✅ |
| Secrets: `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_IDS` | Deploy | Step 3 (`deploy-hosted.yml` + `.env.example` + fly secrets) | ✅ |
| `product/pricing.json` committed to main + parity test | Config | Step 1 | ✅ |
| Dashboard UI (upgrade CTA, portal link, plan display) | UI | Step 8 | ✅ |
| `audit_events` (`billing_*` ops) + `analytics_events` (props allowlist) | Cross-cutting | Step 5 | ✅ |
| Owner identity/email (`Team.email` / `customer_details.email`) | Cross-cutting | Steps 4–5 | ✅ |
| Registry schema docs (`docs/registry-graph-schema.md`, 7714-data-model) | Docs | In-plan (same PR) | ✅ |
| `httpx` pinned | Deps | Step 3 | ✅ |
| MCP write limits (pre-existing gap — REST-only enforcement today) | Boundary | Documented as known boundary; NOT in scope | ⚠️ known |
| Notification channel for "notify Owner" | Cross-cutting | **Open question** — no email infra exists (no Resend/SMTP); MVP: dashboard-banner + audit event, or adopt Resend (#307 parallel issue) | ⚠️ user decision |
| E2E-3-D | Tests | #7738 test design; AC1–AC10 | ✅ |

## Review Cycle Log

**Cycle 1:**
- problem-diverge (2 sub-agents): 5 framings; devil's advocate landed inert-tier kill-shot (verified against code).
- problem-converge (1 sub-agent): trimmed F2, confidence 78.
- **problem-verify** (2 verifiers): NO P0. P1s: enforcement plumbing, dropped idempotency/orphan guard, half-honest owner bridge + no delivery channel. → Controller incorporated all 3.
- solution-diverge (1 sub-agent): A/B/C architecturally distinct.
- **solution-verify** (2 verifiers): NO P0. P1s: atomic dedup-vs-SET, sync `stripe_customer_id` at checkout, `cancel_at_period_end` semantics, deploy-workflow secrets. → Controller incorporated all 4 (P1-1: retry-drop race; P1-2: first-event blind spot; P1-3: cancel semantics; P1-4: secrets wiring). P2s incorporated: timestamp tolerance, state machine coverage, pricing parity source, non-fatal reconcile, index additions, httpx pinning, TeamInfoResponse fields, ops checklist, dashboard duplicate-subscription guard, schema docs, analytics allowlist, MCP boundary, `_lifespan` seam.

## Complexity

| Domain | Rating |
|--------|--------|
| Tier | **Standard** (confirmed — issue's rating). Multi-surface (billing module, auth/enforcement path, registry mirror, dashboard SPA, secrets, tests) but each surface is small and well-bounded; single new module + one file's middleware extension. **Escalation condition:** if the enforcement plumbing (Step 2) turns out to touch auth broadly beyond `get_current_team`/`_check_team_limit`, escalate to complex rather than compressing. |
| UX_RATING | medium — new upgrade CTA + billing portal link + plan/status display in the dashboard SPA (user-visible flow change). UX Prototype Gate: fork-mode (modify `main.jsx` component — prototype IS the implementation); no new HTML prototype needed. |
| ONTOLOGY_RATING | medium — Team node gains `subscription_status`, `current_period_end`, `grace_until`, `max_*` fields; new `:WebhookEvent` node label. Update `docs/registry-graph-schema.md` in same PR. |
| ARCH_RATING | medium-high — new `billing.py` module, external-service integration (Stripe), webhook security surface, reconcile path, pricing parity constraint. No new infra (no queue/scheduler/DB). |

## Discovery: Adjacent Issues to File (do NOT absorb)

📋 Extra issues identified during scoping of #310 (NOT filed — reported for user action):

1. **Usage metering + overage billing** — nothing counts write ops; $5/10k Pro/Team overage is unenforceable/unbillable. This is the largest published revenue lever. #296/#308 territory; **hard dependency for post-MVP revenue, soft dependency for #310** (limits work without metering).
2. **Supabase control-plane migration** — already tracked as #669; link as related. #310's Team-node billing fields are lift-and-shift inputs; #669 should treat them as such.
3. **Land `product/pricing.json` on main** — canonical pricing is branch-only (feat/575-pricing etc.); free-tier ops changed 1k→10k in #662 branch-only. Server reads no pricing artifact → guaranteed drift. (Absorbed into #310 as Step 1 prerequisite, but a standalone doc-hygiene issue is warranted: enforce "pricing.json must exist on main" as a CI check.)
4. **`max_users`/`max_graphs`/`max_teams` stored but never enforced** — downgrade-over-limit (Pro→Free with 2 memberships) has no handling; dashboard displays limits that aren't enforced. Adjacent enforcement gap.
5. **MCP write limits are tier-free** — `mcp_server.py` has zero `_check_team_limit` calls; a paid upgrade changes REST limits but not MCP limits (pre-existing gap, exposed by this work).
6. **API-key `last_used_at` not updated** — documented TODO in `tortoise/hosted_middleware.py`; `get_current_team` never SETs it. Adjacent bug in touched area.
7. **Owner identity / notification channel** — no email delivery infra exists (no Resend/SMTP/in-app table). #307 (Email Integration — Resend for Invitations + Key Recovery) partially covers the channel; "notify Owner on payment failure" needs the channel decision (see open questions).
8. **`_check_team_limit` fail-open on count errors** — currently returns (fail-open) if counting fails; with money at stake, revisit fail-closed vs fail-open explicitly.
9. **get_current_team O(keys) scan** — API-key verification iterates all non-revoked keys per request; scales poorly with paid growth. Tech debt, not this issue.

## Open Scoping Questions (for user)

1. **Stripe account access:** Confirm the existing Apresto Internal gmail Stripe account is accessible to the implementer with API key + webhook secret rights, and confirm test-mode → live-mode switch ownership. Shared-account governance with El Dato (statement descriptor, disputes, API-key rotation) needs an owner.
2. **"Notify Owner" channel:** No email infra exists. Options: (a) dashboard banner + audit event only (zero new infra), (b) Resend email via #307's parallel work, (c) in-app notification table. Which for v1?
3. **Grace period semantics:** Confirm 72h grace (`grace_until = current_period_end + 72h`) as local enforcement window, with `past_due` teams degraded to free after expiry. Or is grace display-only (revert rides solely on Stripe dunning → `subscription.deleted`)?
4. **Price catalog home:** `STRIPE_PRICE_IDS` env JSON vs checked-in catalog module. And confirm all **8** prices (4 tiers × monthly/annual) should be created even though Free ($0) never goes through Checkout — Free prices are display-only.
5. **Reconcile cadence:** Boot-only (recommended for MVP) vs periodic background loop in the single worker. Boot-only keeps it standard-complexity.
6. **Escalation check-in:** If Step 2 (enforcement rewiring) exceeds `get_current_team`/`_check_team_limit`, escalate complexity to complex rather than compressing — confirm this is acceptable.
7. **E2E scope:** E2E-3-D (upgrade flow) requires Stripe test-mode keys in CI — confirm availability, or gate the E2E behind `@pytest.mark.stripe` like the existing `postgres` marker pattern.

## User Decisions (2026-08-08) — recorded after human approval gate

| # | Question | Decision |
|---|----------|----------|
| 2 | "Notify Owner" channel | **Resend email + Telegram bot — BOTH, for Stripe events.** Resend: domain `premiselabs.co` verified (test send OK 2026-08-08, key restricted to sending only), key stored as GitHub Actions secret `RESEND_API_KEY` (2026-08-08 19:15Z) + pending Fly secret + `.env.example`. Telegram: bot `@Premislabs_notifications_bot` (token stored as GH secret `TELEGRAM_BOT_TOKEN`, chat id `551595722` stored as `TELEGRAM_CHAT_ID` — user `MrJackalop`, verified via getMe/getUpdates + test sendMessage OK 2026-08-08 19:19Z). Implementer wires both channels in the billing notify path (`billing_upgrade`, `billing_downgrade`, `billing_payment_failed`, `billing_cancel`), email via Resend API, Telegram via Bot API (`sendMessage`), each failure-tolerant (notify best-effort, never blocks the webhook). |
