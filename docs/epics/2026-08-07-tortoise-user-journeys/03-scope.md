---
title: "Epic Scope — Tortoise Product User Journeys"
type: engineering
domain: platform
doc_status: draft
subjects.team: organisation-design-team
created: 2026-08-07
---

<!-- epic: tortoise-user-journeys → issues #518 + #519 -->
<!-- research-path: docs/epics/2026-08-07-tortoise-user-journeys/02-research-brief.md -->

# Epic Scope — Tortoise Product User Journeys (self-hosted + hosted)

**Date:** 2026-08-07
**Status:** draft (awaiting human approval)
**Inputs:** Align Decision (PROCEED) + Research Brief (14 assumptions, 5 sections)

---

## 1. Scope Boundaries

### In Scope

1. **Dual-flow journey design** — a thought-through map of BOTH flows from landing to first memory:
   - **Hosted (PRIMARY):** land → signup → provision → welcome (key reveal-once) → dashboard (session auth) → connect agent → first memory
   - **Self-hosted:** land → "Self-hosting docs →" → GitHub → install → `tortoise init`/`onboard` → local memory
   - Delivered as a journey map in the epic plan; implemented via the child issues below.

2. **Hosted provisioning E2E verification + fixes (fixes #518, #527)** — walk signup → auth hook → tenant-provision → /internal/provision → user_teams → welcome E2E with a real test user. Fix what breaks: (a) **the duplicate-identifier JS bug (#527 root cause)** — supabase-js v2 UMD declares global `var supabase` and the inline `let supabase` in signup/welcome/signin kills every inline script at parse time (one-line fix per file); (b) hook wiring, env/secrets, demo seed, key hash mismatch. Deliverable: a documented, repeatable E2E walk that dogfooding T7 can rely on.

3. **Key recovery via rotation (fixes #518 chicken-and-egg)** — dashboard surface: "Lost your key? Generate a new one" → mints new `tt_` key (shown once) via existing `POST /v1/team/keys` → optional grace overlap → revoke old. Requires resolving the **auth bootstrap**: a session-authenticated user (Supabase) can call key management WITHOUT a pre-existing valid key. This is the core of #518.

4. **Welcome page reveal-once hardening + key-delivery redesign (A13)** — key shown exactly once at welcome (copy button + "you won't see this again" + `.env` guidance + regenerate path). **Coupled with the storage migration**: currently welcome reads plaintext from `user_teams` via RLS; hardening requires delivering the key via the provision response/one-time mechanism and nulling/removing the plaintext column. Treated as ONE work item.

5. **Dashboard Supabase session auth (fixes #519)** — cross-subdomain session via parent-domain cookie (`Domain=.premiselabs.co`) + PKCE, so a user who signed up on tortoise.premiselabs.co lands authenticated on app.premiselabs.co. API-key login mode coexists. Dashboard resolves its team + a working key without re-pasting.

6. **Dashboard onboarding surface (fixes #519)** — first-login empty state per the canonical formula (explain → shape of success → ONE primary action → sample data), quickstart checklist, live-rendered first memory ("your first point" created from the dashboard and shown in the graph/list).

7. **Funnel analytics** — PostHog (JS snippet on static pages + Python SDK on FastAPI server): `user_signed_up` → `email_confirmed` → `tenant_provisioned` → `dashboard_opened` → `first_api_call` (activation, server-side). Makes the profit chain testable.

8. **Security posture decisions + implementation** — (a) plaintext `api_key` column: hash-only + rotate-on-demand (recommended) vs Supabase Vault re-reveal — DECISION with migration; (b) abuse posture: per-identity rate limit on provisioning, demo-graph size cap, unconfirmed-user cleanup.

9. **Email confirmation handling** — verify the remote Supabase setting (ON by default on hosted); if ON, add "check your inbox" branch to signup + handle confirmation-link return on welcome (PKCE `exchangeCodeForSession`); decide lazy-provision vs cleanup for unconfirmed users (A11).

10. **Self-hosted journey depth** — landing/docs pass: make the self-hosted path (GitHub → install → onboard) a first-class route on tortoise.premiselabs.co, without engine changes.

11. **User↔Team↔Graph decoupling — PRODUCT ONTOLOGY FOUNDATION (owner-confirmed)** — the many-to-many user↔team and one-to-many team↔graph relationships are critical to the product ontology (not the graph ontology). Concretely:
    - **Supabase `user_teams` 1:1 → M:N junction:** drop `UNIQUE(user_id)`; a user belongs to many teams; a team has many members (role on the membership).
    - **Team→Graph 1:N:** replace the single `team_{id}` namespace assumption with a graph entity per team (create/list/select graphs within a team); `max_graphs` tier limit enforced at graph creation.
    - **Tier-driven limits:** replace hardcoded `max_users: 1, max_teams: 1, max_graphs: 1` (hosted_api.py:320) with limits from `product/pricing.md` (Free 1/1/1 · Solo 1/2/1 · Pro ∞/∞/1+ · Team ∞/∞/∞).
    - **Billing is per TEAM, not per user:** a user can be a freelancer paying for their own Solo/Pro team AND a member of a client's Team-tier team in parallel. The subscription attaches to the Team entity; the tier is a Team property.
    - **Invites + RBAC (Team tier):** invitation flow + role-based access (owner/admin/member) — the SDK primitives exist (`membership_create`, `invitation`); the hosted surface (dashboard invite UI, API) is built here.
    - **Key scoping under decoupling:** API keys belong to a team; a user's dashboard session resolves which team(s) they can access and which graph within each.
    - Why now (not deferred): every hosted layer hard-encodes 1:1:1 today. Building Pro-tier on top of the current model would force a user_teams migration + provision-path rework the moment multi-team ships — the painful-later trap the owner's rule forbids.

12. **Pricing structure doc** — create `product/pricing.md` (canonical, survives graph wipes): Free $0 (1 team/1 graph, very restricted, self-hosted + hosted) · Solo $9/mo (max 2 graphs, loss-leader) · Pro $25/mo (unlimited graphs, unlimited agents) · Team $149/mo (collaboration: RBAC, invites) · usage $5 per additional 10k write ops. Per-team billing, no per-seat charges. **Caps align to cost drivers: graphs + collaborators + operations + graph size — NOT agents** (API-access model). Owner-confirmed 2026-08-07 from YC application; supersedes the per-seat graph-filed decision.

13. **Pricing page on tortoise.premiselabs.co** — the tier structure above rendered as a real pricing section:
    - **Component shape:** reuse the El Dato pricing components (DMeer/website `#pricing`): monthly/annual toggle (-20% badge), pricing cards with ✓ included / ✕ excluded feature rows, "Most popular" tag — re-skinned with Tortoise colors (dark slate/cyan/green/gold).
    - **Segmented positioning (owner-confirmed):** two value props above the cards — "**Use Tortoise**" (memory for you and your team: connect your agents) and "**Build with Tortoise**" (embedded memory via API for your product) — same pricing cards, segmented framing. Personas P0/P3b (Build) and P1–P5 (Use).
    - **Hosted cards:** Free / Solo / Pro / Team per `product/pricing.json` (canonical), with the "$5 per additional 10k write ops" usage line and "unlimited integrations" on all cards.
    - **Self-hosted section (Sentry/Metabase pattern, research-verified):** honest tradeoff framing — "your infra, your ops" vs "managed, zero-ops" — with the "Migrate to cloud anytime" conversion CTA (self-hosted → hosted as zero-loss upgrade). Hosted remains the primary CTA; self-hosted is a clearly-labeled secondary route. No fear-framing (Sentry 2019 "hidden costs" is the anti-pattern). BSL 1.1 + $5M AUG license line per #338 D3.
    - **"Why hosted?" callout (Vibecoder segment):** no setup, no servers, managed backups, upgrade as you grow — self-host stays visible for P4/P5.
    - **Landing bifurcation:** hero CTA stays hosted ("Connect your agent →"); "Self-hosting docs →" secondary link deepens (already present — extend).

### Out of Scope

| Item | Reason | Defer to |
|------|--------|----------|
| Welcome page v2 onboarding (yes/no questions, GitHub indexing, guided tour) | #235's Phase 2, gated on user signal; layers ON TOP of this epic's hardened key delivery | Onboarding epic #235 (issues #495–#502) |
| Server-side Supabase JWT verification (JWKS) on FastAPI | Session-held-key bridge is sufficient for v1; JWT verification is a security hardening | Future epic (security hardening) |
| Stripe billing / payment processing / credit metering | Decoupled team/graph model + tier limits are IN scope (foundation); actual payment collection + write-op metering is a billing epic | Hosted platform epic #296 / future billing epic |
| Full abuse-prevention system (CAPTCHA, anomaly detection) | Only minimal posture (rate limit, size caps) in scope | Hosted platform #308 |
| OAuth-based MCP authentication | Bearer `tt_` keys sufficient for v1; OAuth is additive (no login-breaking change); design criteria captured on #524; build AFTER decoupling lands | #524 (OAuth 2.1, complex) |
| Sub-tenancy (per-end-user isolation for embedders) | Requires the decoupled model first; embedder's end-users become tenants under the embedder's account | **#557 (epic, filed)** |
| Planned Pro/Team features (per-graph keys, backups, usage dashboard, webhooks, export, audit log, ReBAC) | Declared intent in pricing.md; future work — capacity stays the pricing lever | Future issues (tracked in plan §8.3 1f) |
| Harness-specific onboarding variants (Pi/Claude/Codex/Cursor) | Filed as follow-up epic after the generalized one-artifact cracks (#235) | **#529 (epic, filed)** |
| Funnel analytics instrumentation (PostHog) | Filed separately; includes hosted-vs-self-host research gate | **#528 (filed)** |
| `tortoise onboard` CLI changes | Self-hosted CLI is robust; only journey links in scope | N/A |
| PostHog self-hosting / EU-hosted data residency | Research in #528 (hosted-vs-self-host decision) | #528 |

### Boundary Rationale

**The guiding principle:** the plumbing is mostly built — signup, welcome, edge function, API all exist. This epic makes the journey *real*: verifies it E2E, fixes the chicken-and-egg, gives the dashboard a session, hardens the key reveal, and instruments the funnel. Two owner-confirmed additions raise the ceiling: **(1) user↔team↔graph decoupling is a product-ontology foundation built NOW** (per-team billing, freelancer-in-multiple-teams scenario, tier-driven limits) — not deferred, because every hosted layer hard-encodes 1:1:1 and deferring creates a migration trap; **(2) pricing structure is documented canonically** (survives graph wipes). Everything that is a *new capability* built on a working journey (onboarding wizard, GitHub indexing, payment collection, OAuth-MCP, analytics) is explicitly deferred to its owning epic/issue, most with design criteria captured now.

---

## 2. Complexity Ratings

| Axis | Rating | Rationale |
|------|--------|-----------|
| UX | **high** | Dual-flow journey design, reveal-once UX, dashboard empty state, confirmation branches, cross-surface consistency (landing/signup/welcome/dashboard) — the core deliverable is a designed journey |
| Architecture | **high** | Cross-subdomain session auth (cookie + PKCE), key-delivery redesign coupled with storage migration, dashboard API bridge, server-side analytics events across 3 layers, **plus user↔team M:N + team↔graph 1:N decoupling with tier-driven limits and per-team billing semantics** |
| Ontology | **medium** | No graph-ontology changes (epistemic layer untouched) — but the PRODUCT ontology gains explicit User↔Team (M:N) and Team↔Graph (1:N) relationships with tier-driven constraint enforcement |
| Accessibility | **medium** | New/modified pages (signup branch, welcome, dashboard) must be keyboard-navigable and contrast-consistent with the existing dark theme; existing pages set the baseline |

---

## 3. High-Level E2E Test Cases

### E2E-1: Hosted signup (email/password) → provision → key revealed once
**Given:** A new visitor on tortoise.premiselabs.co/signup
**When:** They sign up with email/password (email confirmation OFF, as verified on the remote project)
**Then:** A Supabase user + `user_teams` row + FalkorDB team + demo graph are provisioned
**And:** The welcome page reveals a `tt_` API key exactly once, with a copy button and "won't show again" framing
**And:** Refreshing welcome does NOT re-reveal the key

### E2E-2: Hosted signup (OAuth) → provision → key revealed once
**Given:** A new visitor on tortoise.premiselabs.co/signup
**When:** They sign up via GitHub OAuth
**Then:** Provisioning completes (provider-verified email, no confirmation needed)
**And:** The welcome page reveals the `tt_` key once with the same hardening as E2E-1

### E2E-3: Key recovery via rotation (no chicken-and-egg)
**Given:** A provisioned user with a Supabase session and NO remembered `tt_` key
**When:** They open the dashboard and click "Generate a new key"
**Then:** A new `tt_` key is minted and shown once without requiring an existing valid key
**And:** The old key is revoked (immediately or after grace window)
**And:** The new key authenticates against /v1/team and /v1/team/keys

### E2E-4: Cross-subdomain session — signup on tortoise → authed on app
**Given:** A user with an active Supabase session from signup on tortoise.premiselabs.co
**When:** They navigate to app.premiselabs.co
**Then:** The dashboard reads the shared parent-domain session cookie and shows the authenticated view (no key paste)
**And:** Signing out on the dashboard clears the session everywhere

### E2E-5: Dashboard API-key login coexists with session auth
**Given:** A user with NO Supabase session (or signed out)
**When:** They open app.premiselabs.co and paste a valid `tt_` key
**Then:** The dashboard authenticates in API-key mode and shows the same Overview/Keys/Sessions tabs

### E2E-6: Dashboard empty-state onboarding → first memory rendered
**Given:** A freshly provisioned team with an empty graph (or demo graph)
**When:** The user opens the dashboard overview
**Then:** The empty state shows ONE primary action (**"Connect your agent"** — the aha-moment per UX-D3) with the MCP/quickstart snippet, and a secondary "create your first point" action
**And:** Executing the primary or secondary action creates a Point that appears rendered in the dashboard (list/graph), not just a success toast

### E2E-7: Funnel analytics — signup → first API call tracked
**Given:** Analytics instrumentation deployed (PostHog web + server)
**When:** A user completes signup, provisioning, dashboard open, and a first API call
**Then:** Events `user_signed_up`, `tenant_provisioned`, `dashboard_opened`, `first_api_call` appear in PostHog, joined on the user UUID
**And:** TTFV (signup → first_api_call delta) is computable

### E2E-8: Email confirmation branch (if remote setting is ON)
**Given:** Remote Supabase has email confirmation enabled
**When:** A user signs up with email/password
**Then:** signup shows a "check your inbox" state with resend
**And:** Returning via the confirmation link completes the exchange and lands on welcome with the provisioned key

### E2E-9: Self-hosted flow — land → install → first memory (no hosted account)
**Given:** A developer lands on tortoise.premiselabs.co
**When:** They choose the self-hosted path
**Then:** They reach the GitHub/docs route, install, and run `tortoise onboard` to first local memory
**And:** The self-hosted route is reachable from the landing without creating a hosted account

### E2E-10: User↔team decoupling — one user, two teams in parallel
**Given:** A provisioned user (Alice) with a Solo team she owns
**When:** She is added as a member of a second team (a client's Team-tier team) via invite
**Then:** Alice's session resolves BOTH teams; she can switch between them in the dashboard
**And:** Her billing attaches to her own team only; the client's team bills independently
**And:** API keys are scoped per team — a key created under team A does not authenticate against team B

### E2E-11: Team↔graph 1:N — multiple graphs per team with tier limits
**Given:** A Pro-tier team with unlimited graphs
**When:** The owner creates graph #1 and graph #2 in the same team
**Then:** Both graphs exist and are addressable within the team
**And:** A Free-tier team is blocked at 1 graph with a clear upgrade message
**And:** A Solo-tier team is blocked at 2 graphs (loss-leader cap) with an upgrade prompt

### E2E-12: Team-tier collaboration — invites + RBAC
**Given:** A Team-tier team owned by Alice
**When:** Alice invites Bob (member role) and Carol (admin role)
**Then:** Bob and Carol appear as members with their roles; the invitation flow completes
**And:** Bob (member) cannot create/revoke API keys or delete the team; Carol (admin) can manage members
**And:** A Free/Solo/Pro-tier team cannot invite members (single-user per the pricing model)

### E2E-13: Pricing structure documented and enforced
**Given:** `product/pricing.md` committed
**When:** The provision path creates a team
**Then:** The team's tier-driven limits (max_teams/max_graphs/max_users) come from the pricing doc's table
**And:** `/v1/team` returns the tier + limits, and limit enforcement matches the doc

### E2E-14: Pricing page renders hosted tiers + self-hosted section
**Given:** The pricing page live on tortoise.premiselabs.co
**When:** A visitor scrolls to pricing
**Then:** Hosted cards render (Free/Solo/Pro/Team) with monthly/annual toggle and ✓/✕ feature rows, "$5 per additional 10k write ops" visible
**And:** A self-hosted section presents the OSS option with honest tradeoff framing + "migrate to cloud anytime" CTA
**And:** The hosted CTA is primary; self-hosted is secondary but clearly discoverable

---

## 4. Human Approval Gate

## Epic Scope Ready for Review

**Scope:** 13 in-scope deliverables (hosted E2E verification, key recovery via rotation, reveal-once + storage migration, dashboard session auth, dashboard onboarding surface, security posture, email confirmation handling, dual-flow journey design, self-hosted journey depth, **user↔team↔graph decoupling with tier-driven limits + per-team billing**, **pricing doc**, **pricing page on tortoise.premiselabs.co incl. self-hosted section**). Out of scope: onboarding v2 (#235), JWKS, payment collection/billing epic, full abuse system, OAuth-MCP (#524, design criteria captured), harness variants (**#529 filed**), funnel analytics (**#528 filed**).

**E2E test cases:** 14 drafted (E2E-1..14) — behavioral, UI-independent.

**Complexity:** UX high · Architecture high · Ontology medium (product ontology) · Accessibility medium.

Review the scope boundaries and E2E test cases.
Reply "proceed" to continue to detailed planning, or give feedback.

---

## 5. UX Design Decisions (recorded 2026-08-07, UX gate)

| # | Decision Type | User Choice (default) | Rationale |
|---|---|---|---|
| 1 | Dashboard nav (multi-team/graph) | Team switcher (top bar) + graph dropdown | Decoupled model is the product ontology; per-team billing makes team-switching the primary mental model |
| 2 | Welcome page (returning users) | "Your team is ready → Open Dashboard"; no re-reveal; "Lost your key? Regenerate in dashboard" | Matches reveal-once + rotation pattern; dashboard becomes the hub |
| 3 | First-login empty state primary action | "Connect your agent" primary + "create a point" secondary | Aha-moment = agent connection (epistemic memory differentiator); <15min TTFV |
| 4 | Tier-limit presentation | Inline soft-block + upgrade CTA | Supabase/Metabase usage-transparency pattern; converts limit into monetization moment |
| — | Pricing page components | El Dato shape (toggle, ✓/✕ cards, popular tag) + Tortoise colors | Owner directive |
| — | Self-hosted section | Sentry/Metabase honest-tradeoff + migrate-to-cloud CTA | Research-verified |
