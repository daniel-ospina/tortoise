---
title: "Epic Plan — Tortoise Product User Journeys"
type: engineering
domain: platform
doc_status: complete
subjects.team: organisation-design-team
created: 2026-08-07
---

<!-- epic: tortoise-user-journeys → issues #518 + #519 -->
<!-- plan: epic-plan 8 substeps — 01 user journeys -->

# Epic Plan — Tortoise Product User Journeys

**Date:** 2026-08-07
**Status:** complete (all 8 substeps, coherence CLEAN; human gate #2 approved 2026-08-07; decomposed into #568-#578)
**Inputs:** Align (PROCEED) · Research brief (14 assumptions) · Scope (13 deliverables, 14 E2E, UX decisions recorded)

---

# SUB-STEP 1 — USER JOURNEYS

## Personas

Two segments: **Use** (Tortoise as memory product — agents remember for you/your team) and **Build** (Tortoise as dev tool — embedded memory inside your own product). Segmented positioning on the landing/pricing page; the journeys are shared.

| Persona | Segment | Description | Primary flow | Secondary flow |
|---|---|---|---|---|
| **P0 — App Builder / Embedder** | Build | Developer embedding Tortoise as memory inside *their own product* (agent apps, copilots, analytics). Aha = **first API call in their app**. Tier driver: Pro (per-team key, usage-based overage scales with their users). | Hosted Pro | Self-host daemon (#338) |
| **P1 — Solo Hacker** | Use | Technical dev; evaluates agent memory for their own agents; can self-host if needed | Hosted Free → Solo | Self-hosted eval |
| **P1b — Vibecoder** | Use | Less-professional dev or non-dev freelancer running a few agents; **will NOT self-host** (too complicated); price-sensitive; entry-tier buyer. Starts Free, upgrades to Solo ($9). Self-hosting stays visible but "Why hosted?" callout explains the advantages. | Hosted Free → Solo | — |
| **P2 — Power User** | Use | Next evolution of P1 — hit Free/Solo caps, needs unlimited graphs + serious usage + multi-team membership | Hosted Pro ($25) | Parallel memberships |
| **P3 — Team Lead / Agency** | Use | Buys Team tier; invites collaborators; RBAC admin | Hosted Team ($149) | — |
| **P3b — Build Team / Advanced Embedder** | Build | Company embedding Tortoise at scale: multiple product teams, white-label ambitions, per-end-user isolation. Team tier + sub-tenancy (future epic). | Hosted Team | Sub-tenancy (future) |
| **P4 — OSS Evaluator** | Use | Wants self-hosted; sovereignty/compliance-minded | Self-hosted (BSL grant) | Hosted free tier |
| **P5 — Existing Self-Host User** | Use | Already runs tortoise locally; considers hosted for zero-ops | Self-hosted → hosted **CTA** (J-5 step 5) | — |

> **P2 correction (owner-confirmed):** NOT a freelancer — just the next evolution of P1 (hit the caps → upgrade). The upgrade path is the tier-limit soft-block (UX-D4) with segment-aware copy (see upgrade-path design below).

> **P5 migration scope note:** v1 "migrate to cloud anytime" is a **CTA + fresh hosted team + connect**, NOT a data-import migration — no engine changes are in scope. The hosted team starts fresh (optionally demo-seeded); the user re-connects their agents. A real graph-import migration is a future epic.

## Upgrade-path design (from personas — segment-aware soft-block copy)

The tier-limit soft-block (UX-D4) is the upgrade *argument surface*. When a cap is hit, the message is segment-aware:

| Cap hit | Segment | Message |
|---|---|---|
| Free 1 graph / 1K ops / 10K nodes | Use (Vibecoder/P1) | "Your agents are working — Solo gives 2 graphs + 10× ops for $9/mo" |
| Solo 2 graphs / 10K ops | Use (P1→P2) | "Stop hitting caps — Pro: unlimited graphs, 100K ops, multi-team membership" |
| Pro 2 collaborators | Use (P2→P3) | "More people — Team: invites, RBAC, shared graphs" |
| App scale (usage) | Build (P0) | "Your app just works — usage-based overage grows with your users; per-graph keys isolate apps" |
| Org scale | Build (P3b) | "Embedded at scale — Team + sub-tenancy (future)" |
| Self-host maintenance burden | Use (P4/P5) | "Zero-ops — migrate to cloud anytime" |

---

## J-1: Hosted Signup → First Memory (PRIMARY FLOW — P0/P1/P1b/P2)

> **Segment note:** for the Use segment (P1/P1b/P2), step 7's primary action is "Connect your agent"; for the Build segment (P0), the same journey's primary action is the API quickstart (create-point snippet — already API-shaped), with "connect your agent" secondary. The journeys share all plumbing; only the primary-CTA copy segments (UX-D3 + pricing-page segmented positioning).

**Entry:** Visitor on tortoise.premiselabs.co (landing hero: "Connect your agent →")
**Exit:** First memory created + visible in dashboard; agent connected

| Step | Surface | User action | System behavior | Edge cases | Telemetry |
|---|---|---|---|---|---|
| 1 | Landing (product.html) | Clicks "Connect your agent →" | → /signup | — | — |
| 2 | Signup (signup.html) | Chooses GitHub OAuth OR email/password (Google OAuth shown in P-6 but **out of v1 scope** — GitHub + email only) | Supabase auth; **if email confirmation ON** → "check your inbox" branch (NEW); if OFF → direct | Duplicate email → "already exists, sign in"; **GitHub OAuth cancel/failure → return to signup with error state** | `user_signed_up` (web, identify user_id) |
| 3 | Provision (server, invisible) | — | auth hook → tenant-provision edge fn → /internal/provision → team + namespace + demo seed + team_memberships row | Hook not wired; env missing; key hash mismatch (E2E-1 finds) | `tenant_provisioned` (server: user_id, team_id, status) |
| 4 | Welcome (welcome.html) — **first visit** | Sees team name + tier + **API key revealed ONCE** + copy button | Polls team_memberships / receives key via provision delivery; key nulled after reveal (A13) | Key "pending" >30s → retry msg; no session → error state | `email_confirmed` (web) |
| 4b | Welcome — **returning visit (UX-D2)** | Provisioned user returns to welcome.html | Key already nulled → **NO re-reveal**; shows "Your team is ready → Open Dashboard" + "Lost your key? Regenerate in dashboard" (→ J-2) | No session → sign-in first; key already consumed → never re-shown | — |
| 5 | Welcome → connect | Copies MCP config (or CLI quickstart) | Copy button; snippet pre-filled with key | — | — |
| 6 | Dashboard (app.premiselabs.co) | Opens "Open Dashboard →" | **Cross-subdomain session cookie** → auto-authed (no key paste) | Session not shared → falls back to API-key login; **session expired mid-use → redirect to sign-in** | `dashboard_opened` (web, user_id) |
| 7 | Dashboard empty state | Clicks "Connect your agent" (primary) or "Create your first point" (secondary) | Quickstart snippet; point creation → live render | Graph empty vs demo graph populated | — |
| 8 | First memory | Runs the snippet / MCP call | Point created → appears in dashboard list/graph | API 500 (regression #292 — verify) | `first_api_call` (server middleware = activation) |

> **Telemetry note:** instrumentation is implemented via issue **#528** (PostHog, out of epic). The epic wires the event hooks (web snippet + server middleware) so #528 can land on top; E2E-7 verifies events fire.

## J-2: Key Recovery (fixes #518 chicken-and-egg — P1/P2/P3)

**Entry:** Provisioned user with Supabase session, lost their `tt_` key
**Exit:** New key in hand; old key revoked

| Step | Surface | User action | System behavior | Edge cases |
|---|---|---|---|---|
| 1 | Dashboard (session-authed) | Clicks "Lost your key? Generate a new one" | **Mints via `POST /v1/session/key` (E1 — session-authed, no pre-existing key required — the auth-bootstrap fix)** | **Per-identity rate limit on key minting (abuse posture)** |
| 2 | Dashboard | New key revealed once + copy | E1 with `purpose='recovery'` returns a PERSISTENT key (expires_at null, revocable — not 24h; only bootstrap keys are 24h); list/revoke via `/v1/team/keys` with the minted key | Key shown once only |
| 3 | Dashboard | (optional) revoke old key | `DELETE /v1/team/keys/{id}` — **immediate revocation (pinned in E2E-3)** | Multiple active keys OK (2–3); **revoking the ONLY remaining key → warn/block (team would be keyless)** |
| 4 | Anywhere | Uses new key | Authenticates against /v1/team | — |

## J-3: Dashboard Session Auth (fixes #519 — P1/P2/P3/P5)

**Entry:** User signed up on tortoise.premiselabs.co (or signed in)
**Exit:** Dashboard authenticated via Supabase session (no key paste)

| Step | Surface | User action | System behavior | Edge cases |
|---|---|---|---|---|
| 1 | app.premiselabs.co | Navigates to dashboard | Dashboard's Supabase client reads **parent-domain cookie** (`Domain=.premiselabs.co`) + PKCE → getSession() succeeds | Cookie not shared (old session) → API-key login fallback |
| 2 | Dashboard | Sees authenticated Overview | Resolves team(s) from session → team switcher populated | Multi-team: shows all memberships |
| 3 | Dashboard | Logs out | `signOut()` clears parent-domain cookie → logged out everywhere | — |
| 4 | Dashboard (alt) | No session: pastes API key | API-key login mode coexists (unchanged) | — |

## J-4: Multi-Team + Multi-Graph (decoupling — P2/P0/P3b)

**Entry:** User with >1 team membership (e.g., own Solo team + client's Team team)
**Exit:** Switches context to the right team/graph; operations scoped correctly

| Step | Surface | User action | System behavior | Edge cases |
|---|---|---|---|---|
| 1 | Dashboard top bar | Selects team from switcher | Session resolves memberships; context switches | Freelancer: own team billed separately from client team |
| 1b | Dashboard | (zero-teams state) | User has 0 team memberships (owner/member removed via E8) → "Create your first team" state | Prevents dead-end. **Team deletion is OUT of v1 scope (no DELETE /v1/teams endpoint)** |
| 2 | Dashboard (team view) | Selects graph from dropdown | Lists graphs in team (1..N per tier) | Free/Solo: cap reached → inline soft-block + upgrade CTA (UX-D4); **zero-graphs state REMOVED (default graph guaranteed — 4.2 backfill + no graph-deletion endpoint; switcher always shows ≥1)** |
| 3 | Dashboard | Creates a point in team A's graph | Goes to team A namespace; key scoping per team | Key from team A fails against team B (E2E-10) |
| 4 | Team tier | Owner invites Bob (member) / Carol (admin) | Invitation flow + RBAC | Free/Solo/Pro: invites disabled |

## J-5: Self-Hosted Flow (P4/P5 — SECONDARY, aligned with #338)

**Entry:** Visitor on tortoise.premiselabs.co, chooses self-hosted
**Exit:** Local tortoise daemon running + first memory

| Step | Surface | User action | System behavior | Edge cases |
|---|---|---|---|---|
| 1 | Landing | Clicks "Self-hosting docs →" (secondary) | → GitHub/docs (#338 T5.1 story) | Hosted CTA remains primary |
| 2 | GitHub/README | `docker compose up` (daemon + falkordb) OR `pip install` | BSL 1.1 + $5M AUG license line | License framing "free under BSL grant" not "free OSS" |
| 3 | Local daemon | `claude mcp add tortoise http://localhost:8000/mcp` | MCP connect to self-host daemon | Daemon down → tortoise_unavailable |
| 4 | Local | Runs `tortoise onboard` / first point | First local memory | Durability caveat (eval mode) |
| 5 | (optional) | "Migrate to cloud anytime" CTA | Self-hosted → hosted zero-loss upgrade | Matches onboarding epic #235 mapping |

## J-6: Pricing Page → Tier Selection (P1/P2/P3/P4)

**Entry:** Any visitor; pricing linked from landing/nav
**Exit:** Tier chosen → signup or upgrade CTA

| Step | Surface | User action | System behavior | Edge cases |
|---|---|---|---|---|
| 1 | Pricing section (tortoise.premiselabs.co) | Toggles monthly/annual (-20%) | Price swap via JS (El Dato component) | Annual default? (research: default annual) |
| 2 | Pricing cards | Reads Free/Solo/Pro/Team ✓/✕ features | Cards from El Dato shape, Tortoise colors | Usage line: "$5 per additional 10k write ops" |
| 3 | Self-hosted section | Reads BSL + $5M AUG license + tradeoff | Sentry/Metabase pattern; migrate-to-cloud CTA | — |
| 4 | CTA | Clicks "Get started" on a tier | → /signup (free) or billing (future) | Billing not built → CTA to signup for now |

---

## Journey Coverage Check (vs scope)

| Scope deliverable | Covered by |
|---|---|
| 1. Dual-flow journey design | J-1..J-6 (this doc) |
| 2. Hosted provisioning E2E + fixes (#518/#527) | J-1 steps 2–4; E2E-1/2 |
| 3. Key recovery via rotation (#518) | J-2; E2E-3 |
| 4. Reveal-once + storage migration (A13) | J-1 step 4; E2E-1 |
| 5. Dashboard session auth (#519) | J-3; E2E-4/5 |
| 6. Dashboard onboarding surface (#519) | J-1 steps 6–8; E2E-6 |
| 7. Funnel analytics (#528 is separate issue; epic wires events) | J-1 (events fired at each step) |
| 8. Security posture | J-2 (rotation); migration in Data Model |
| 9. Email confirmation handling | J-1 step 2; E2E-8 |
| 10. Self-hosted journey depth | J-5; E2E-9 |
| 11. User↔Team↔Graph decoupling | J-4; E2E-10/11/12 |
| 12. Pricing doc | J-6 (renders it); E2E-13 |
| 13. Pricing page + self-hosted section | J-6; E2E-14 |

---

# SUB-STEP 2 — WORKFLOWS

System-level workflows backing the journeys. Automation points, manual triggers, failure modes.

## W-1: Provisioning Pipeline (system → system, drives J-1 steps 2–4)

```
Supabase signup (OAuth/email)
  → auth.users INSERT
  → [DB trigger] handle_new_user() → team_memberships placeholder row (team_id='', key_hash='pending', role='owner')
  → [Auth hook] after_user_created → Edge Function tenant-provision
  → Edge: validate UUID → derive team_name → generate team_id (truncated UUID hex: `crypto.randomUUID().replace(/-/g,"").substring(0,26)` — NOT a real ULID) → generate tt_ key (32B hex)
  → Edge: hashApiKey (PBKDF2-HMAC-SHA256, per-key salt, pepper) — MUST match auth.py
  → POST /internal/provision (FASTAPI_INTERNAL_KEY bearer)
  → FastAPI: validate patterns → create Team node (tier-driven limits) → create APIKey node → Membership (owner)
  → POST /internal/demo (demo seed, best-effort) — **NOTE: edge fn currently calls `/v1/internal/demo` (404; route is `/internal/demo`) → demo seeding dead in prod — fix edge URL or add alias**
  → Edge: update placeholder row (user_id + team_id='') → flip to real team_id + upsert api_key plaintext (to be nulled after reveal, A13) — **idempotent keyed on user_id: skip mint if a non-pending membership exists (race-safe vs reconciliation sweep, P2-9)**
  → welcome.html polls team_memberships (or receives key via one-time delivery) → reveal ONCE → null plaintext
```

**Automation points:** trigger, hook, edge function, internal endpoints — all automated.
**Manual triggers:** none in normal flow. Operator debug path: direct edge-function POST with `{user_id, email}` payload (exists).
**Failure modes:**
| Failure | Detection | Fallback |
|---|---|---|
| Hook not wired on remote | E2E walk shows no membership row | Fix Supabase dashboard config (A1) |
| Provisioning rate-limit (NEW — abuse posture 8b) | repeated provision attempts for one identity | edge-fn per-identity limiter → 429/blocked (contract in E2E-1 negative) |
| Edge fn env missing (pepper, FASTAPI_URL, service key) | Edge 500; welcome stuck "pending" | Set Supabase secrets; retry |
| Provision 502 (FastAPI unreachable) | team_memberships stays pending | Retry edge; alert |
| Key hash mismatch (PBKDF2 divergence) | Key validates against nothing | Fix hashApiKey parity test (exists: test_hosted_auth) |
| Email confirmation ON | signUp returns no session; hook fires PRE-confirmation (keys minted for unconfirmed emails) | J-1 step 2 confirmation branch (E2E-8); **gate key reveal on email_confirmed + cleanup for never-confirmed accounts (abuse vector, A11)** |
| Demo seed failure | Edge calls `/v1/internal/demo` (404 — route is `/internal/demo`); `.catch()` swallows it | **Fix edge URL or add `/v1/internal/demo` alias; E2E walk verifies demo points exist (J-1 step 7)** |

## W-2: Key Lifecycle (drives J-2)

```
Mint:  session-authed user → POST /v1/session/key (E1) → {key, expires_at} → shown once
List:  GET /v1/team/keys → {id, key_prefix, created_at, last_used_at, revoked_at} — hashes only
Revoke: DELETE /v1/team/keys/{id} → revoked_at set → immediate auth failure
Recover (the #518 fix): session-authed user with NO valid key mints via E1 with `purpose='recovery'` (persistent, revocable — NOT 24h)
Rotate: mint new via E1 → grace overlap (optional) → revoke old → clients cut over
```

**Automation:** all CRUD automated. **Manual:** none.
**Failure modes:** rate-limit on minting (abuse posture); revoking only key → warn/block; key_prefix collision (uuid hex — negligible).
**⚠ Dependency (P2-4):** `get_current_team` (hosted_api.py:455) only accepts Bearer `tt_` keys. Recovery requires a **NEW API-side auth path** — Supabase JWT → user_id → Membership → team resolution — so a keyless user can mint via **`POST /v1/session/key` (E1)**, then list/revoke via `/v1/team/keys` with the minted key (`/v1/team/keys` stays strictly `tt_`-authed — no JWT auth there). W-3 covers the dashboard-side cookie bridge, not API auth; this new resolution is a required backend piece of the #518 fix.

## W-2b: Invitation + RBAC (Team tier — NEW, backs J-4 step 4)

```
owner invites bob@example.com (member) / carol@example.com (admin)
  → invitation created (SDK invitation primitives exist; hosted invite API + email link = NEW)
  → invitee accepts → Membership node (BELONGS_TO, role) → role-scoped graph ops
  → owner/admin: manage members (add/remove/role-change); member: read points/sessions + write points (keys/members admin-only)
  → invites are TEAM-tier-only (RBAC); Pro supports 1+ users via the invite path once tiered (v1: Pro single-owner + invited members deferred to billing epic; pricing.md Pro = 1+ users)
  → Free/Solo: single-user (max_users=1)
```

**Automation:** invite accept + role assignment. **Manual:** owner sends invite. **Limits source:** pricing.json (decision 1d).
**Failure modes:** expired invite token → resend; role-escalation attempt → blocked; invitee without account → signup-first then accept.

## W-2c: Team Creation (NEW, backs J-4 step 1b zero-teams state)

```
user with 0 memberships → "Create your first team" state
  → NEW POST /v1/teams (session-authed; per-user creation rate-limit — NOT a tier limit; multi-team is a user capability per pricing semantics)
  → registry Team node + Membership (owner) + graph namespace
  → tier limits from pricing.json (canonical — decision 1d; **no teams-per-tier field — per-user team-creation rate limit (abuse posture); '1 team' is pricing-page copy only**) + per-user creation rate limit (429, not a tier block)
```

**Automation:** team creation. **Manual:** user triggers.
**Failure modes:** per-user team-creation rate-limit hit → retry later (abuse posture, not a tier block); name collision → idempotency key.

## W-3: Session Auth Bridge (drives J-3)

```
Signup on tortoise.premiselabs.co (PKCE) → session cookie Domain=.premiselabs.co
  → dashboard on app.premiselabs.co reads same cookie → getSession() → authenticated
  → dashboard resolves teams via team_memberships junction (M:N) → team switcher
  → API calls: session-scoped tt_ key from POST /v1/session/key (bootstrap) → api.premiselabs.co
  → signOut() clears parent-domain cookie → signed out everywhere
```

**Automation:** cookie + PKCE fully automated. **Manual:** API-key-paste fallback (exists).
**Failure modes:** cookie not shared (stale session) → fallback to key paste; session expiry → sign-in redirect; CORS (A7) → verify preflight.

## W-4: Tier Enforcement (drives J-4, E2E-11/13)

```
team_create / graph_create / membership_create / key_mint
  → read Team.tier → limits from pricing.json (canonical — decision 1d; runtime load)
  → Free: max_graphs=1, max_users=1
  → Solo: max_graphs=2 (loss-leader)
  → Pro: unlimited graphs, 2 users
  → Team: unlimited, invites+RBAC
  → limit exceeded → inline soft-block + upgrade CTA (UX-D4) — NOT hard error
  → (multi-team is user-level, NOT a tier field — no max_teams check)
```

**Current state (accurate, verified):** `_check_team_limit` (hosted_api.py:503) covers only `points | api_keys | sessions` with flat fallbacks (1000/20/1000); **no `max_graphs` check exists**; `max_users` enforced only in SDK `membership_create` (sdk.py:2802); provision hardcodes `max_users:1, max_graphs:1` (hosted_api.py:320).
**NEW work (tier enforcement):** store tier + limits on Team node; add `max_graphs` check; wire membership `max_users` to tier; make key limit tier-driven (not flat 20). **No `max_teams` tier field** — team creation is rate-limited per user (abuse posture), not tier-capped (pricing semantics).
**Automation:** limit checks in SDK/hosted_api (existing `_check_team_limit` extended + new checks). **Manual:** tier assignment (provision defaults to Free; upgrades are future billing).
**Failure modes:** limit check bypass (race) — enforce at registry level (count query per resource); tier not set on legacy teams → default Free.

## W-5: Funnel Analytics (drives E2E-7; impl via #528)

```
web: user_signed_up → identify(user_id)
web: email_confirmed (confirmation branch)
server: tenant_provisioned (edge or FastAPI)
server: first_api_call (FastAPI middleware on /v1/*)
web: dashboard_opened
→ PostHog funnel: signed_up → provisioned → dashboard_opened → first_api_call
→ metric: activation rate (25–35% band), TTFV (<15 min)
```

**Automation:** snippets + middleware. **Manual:** none.
**Failure modes:** ad-blocker (mitigate via CF Worker proxy — #528 research); distinct_id mismatch (web vs server) — join on Supabase user UUID.

## W-6: Provision → Pricing Page (drives J-6)

```
Visitor → pricing section → toggle monthly/annual → card prices swap (JS)
  → CTA per tier → /signup (free) or future billing endpoint
  → self-hosted section → BSL + $5M AUG license line → GitHub/docs
```

**Automation:** toggle JS. **Manual:** none (billing collection is future epic).
**Failure modes:** annual default vs monthly — research says default annual; pricing page renders from **pricing.json** data (canonical — decision 1d).

---

## Workflow Alignment Check

| Journey | Backing workflows | Handoffs clear? | Failure modes documented? |
|---|---|---|---|
| J-1 (hosted signup→memory) | W-1, W-3, W-5 | yes | yes |
| J-2 (key recovery) | W-2 | yes | yes |
| J-3 (session auth) | W-3 | yes | yes |
| J-4 (multi-team/graph) | W-4, W-2b (invites/RBAC), W-2c (team create) | yes | yes |
| J-5 (self-hosted) | (no new system workflow — daemon per #338) | n/a | n/a |
| J-6 (pricing) | W-6 | yes | yes |

---

# SUB-STEP 3 — PROTOTYPE (markdown wireframes)

GUI surfaces are modifications to existing pages + one new pricing section (El Dato component reuse). Wireframes below; states per journeys.

## P-1: Welcome page (welcome.html) — first visit vs returning (J-1 steps 4/4b, UX-D2)

```
┌───────────────────────────────────────────────┐
│ [logo] Tortoise        Product · Docs  [Login] │
├───────────────────────────────────────────────┤
│  FIRST VISIT:                                 │
│  ✓ Your team is ready!                        │
│  Team: <team-name> · Tier: Free               │
│  ┌─ Your API key ──────────────────────────┐  │
│  │ tt_a1b2c3d4e5f6...      [📋 Copy]      │  │
│  │ ⚠ shown once — store it in .env         │  │
│  └────────────────────────────────────────┘  │
│  A · Connect your agent (MCP config block)   │
│  B · CLI quickstart (tortoise init --api-key)│
│  [Open Dashboard →]                          │
│                                              │
│  RETURNING VISIT:                            │
│  ✓ Your team is ready!                       │
│  Your API key was already created.           │
│  [Open Dashboard →]  [Lost your key? → J-2]  │
└───────────────────────────────────────────────┘
```
**States:** loading (polling) · error (no session) · pending>30s · first-visit reveal · returning (no re-reveal).

## P-2: Dashboard (app.premiselabs.co) — authenticated shell + team/graph switcher (J-3/J-4, UX-D1)

```
┌──────────────────────────────────────────────────────┐
│ [Tortoise]  [Team ▾] [Graph ▾]   Overview · Keys · Sessions  [Log out] │
├──────────────────────────────────────────────────────┤
│  OVERVIEW (empty state — first login, UX-D3):        │
│  ┌──────────────────────────────────────────────┐    │
│  │ 👋 Welcome to your Tortoise graph            │    │
│  │ Connect your agent so it remembers why,      │    │
│  │ not just what.  [primary action → snippet]   │    │
│  │ ── or create your first point ──             │    │
│  │ [secondary: curl snippet]                    │    │
│  └──────────────────────────────────────────────┘    │
│  OVERVIEW (populated): Points · API Keys · Sessions · Tier │
│  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐            │
│  │ 128    │ │ 3      │ │ 42     │ │ Pro    │            │
│  │ Points │ │ Keys   │ │ Sess.  │ │ Tier   │            │
│  └────────┘ └────────┘ └────────┘ └────────┘            │
└──────────────────────────────────────────────────────┘
```
**States:** no session → API-key login card · **loading (team/graph resolution spinner)** · **session expired mid-use → notice + redirect to sign-in** · authenticated (team+graph context) · zero-teams → "Create your first team" · tier cap hit → inline soft-block + upgrade CTA. **(zero-graphs state removed — default graph guaranteed; switcher always shows ≥1)**

**Create-team dialog (W-2c companion):** name input + tier display (Free default) + submit; name-collision → idempotency (retry-safe).

## P-3: Pricing section (tortoise.premiselabs.co) — El Dato components, Tortoise colors (J-6)

```
┌──────────────────────────────────────────────────────┐
│  Pricing                                            │
│  [Monthly] [Annual -20%]                            │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐    │
│  │ FREE    │ │ SOLO    │ │ PRO ★  │ │ TEAM    │    │
│  │ $0/mo   │ │ $9/mo   │ │ $25/mo │ │ $149/mo │    │
│  │ ✓ 1 graph│ │ ✓ 2 graphs││ ✓ Unlim. │ │ ✓ Unlim. │    │
│  │ ✓ 1 user │ │ ✓ 1 user │ │ ✓ 1+    │ │ ✓ RBAC   │    │
│  │ ✕ invites│ │ ✕ invites│ │ ✕ invites│ │ ✓ invites│    │
│  │ [Start]  │ │ [Start]  │ │ [Start] │ │ [Start]  │    │
│  └─────────┘ └─────────┘ └─────────┘ └─────────┘    │
│  $5 per additional 10k write ops                    │
│  ┌──────────────────────────────────────────────┐    │
│  │ Self-hosted: BSL 1.1, free <$5M rev,         │    │
│  │ MPL-2.0 in 4 yrs. Your infra, your ops.   │    │
│  │ [GitHub] [Migrate to cloud anytime →]        │    │
│  └──────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────┘
```
**States:** monthly/annual swap (JS) · annual default · self-hosted section (BSL framing) · CTA → /signup.

## P-4: Key recovery (dashboard, J-2)

```
┌────────────────────────────────────────────────┐
│ API Keys                          [+ New key]  │
│ ┌────────┬──────────┬──────┬────────┬──────┐   │
│ │ Prefix │ Created  │ Last │ Status │      │   │
│ │ tt_a1b…│ …        │ …    │ active │[Revoke]│  │
│ └────────┴──────────┴──────┴────────┴──────┘   │
│ Lost your key? [Generate a new one]  ← session │
│   (no existing key required — auth-bootstrap)  │
│ New key (shown once): tt_xxx  [📋 Copy]        │
└────────────────────────────────────────────────┘
```
**States:** session-authed (can mint without key) · rate-limited (abuse posture) · only-key revoke warn.

## P-5: Members + Invites (Team tier, J-4 step 4 / W-2b)

```
┌────────────────────────────────────────────────┐
│ Members                        [+ Invite]      │
│ ┌──────────────┬────────┬──────────┬────────┐  │
│ │ Member       │ Role   │ Status   │        │  │
│ │ Alice (you)  │ owner  │ active   │        │  │
│ │ Bob          │ member │ active   │[Remove]│  │
│ │ Carol        │ admin  │ active   │[Remove]│  │
│ └──────────────┴────────┴──────────┴────────┘  │
│ Invite dialog: email input + role selector     │
│   (admin / member) → send invite link          │
│   (owner is not invitable — single-owner model) │
│ Role-scoped visibility: member sees points/     │
│   sessions only; admin also manages members     │
│ Free/Solo: [+ Invite] hidden/disabled           │
│   (max_users=1, invites off)                    │
│ Pro: invites off in v1 (max_users=1+ per         │
│   pricing; invite path deferred to billing)     │
└────────────────────────────────────────────────┘
```
**States:** Team tier (invites on) · Free/Solo/Pro (invites disabled) · pending invite (awaiting accept) · expired token → resend.

## P-6: Signup states (signup.html — #527 fix surface)

```
┌────────────────────────────────────────────────┐
│ Create your account                            │
│ [Continue with GitHub] [Google — out of v1 ✓]  │
│ Email ___  Password ___  [Create account]      │
│                                                │
│ EMAIL CONFIRMATION ON (E2E-8):                 │
│ ✓ Check your inbox — we sent a link to         │
│   <email>. [Resend] (rate-limited)             │
│                                                │
│ OAuth CANCEL/FAILURE:                          │
│ ✕ GitHub sign-in was cancelled or failed.      │
│   [Try again]                                  │
│                                                │
│ DUPLICATE EMAIL:                               │
│ An account with this email already exists.     │
│   [Sign in instead]                            │
└────────────────────────────────────────────────┘
```
**States:** form · confirmation-branch (resend) · OAuth failure · duplicate email.

## Prototype review note

New/modified GUI: welcome (states added), dashboard (team/graph switcher, empty state, create-team dialog, members/invites), pricing (new, reuses El Dato components), key recovery, signup (confirmation + error states). All reuse the existing dark-slate/cyan design system (no new tokens). Accessibility baseline (dark theme, keyboard nav) inherited. No design-system divergence — components are copied from DMeer/website and re-skinned.

---

# SUB-STEP 4 — DATA MODEL

## 4.1 Supabase `user_teams` → decoupled junction (M:N user↔team)

**Current:** `user_teams` (user_id UNIQUE → 1 team/user). **Target:** rename to `team_memberships` junction; drop UNIQUE(user_id); add role + status + invited_email; (user_id, team_id) unique.

```sql
-- Migration 0003: decouple user↔team (M:N) — product ontology
-- 1) Rename + drop 1:1 constraint
ALTER TABLE public.user_teams RENAME TO team_memberships;
ALTER TABLE public.team_memberships DROP CONSTRAINT uq_user_teams_user;

-- 2) New columns
ALTER TABLE public.team_memberships
  ADD COLUMN role          text NOT NULL DEFAULT 'owner',  -- owner | admin | member
  ADD COLUMN status        text NOT NULL DEFAULT 'active', -- active | invited | removed
  ADD COLUMN invited_email text,                            -- pre-signup invite target
  ADD CONSTRAINT uq_member_team UNIQUE (user_id, team_id),
  ADD CONSTRAINT chk_member_or_invite CHECK (user_id IS NOT NULL OR invited_email IS NOT NULL);

-- 3) Partial unique: one ACTIVE invite per (team, email) — NULLs are distinct in unique
--    constraints, so without this duplicate invites to the same email are allowed.
CREATE UNIQUE INDEX uq_team_invite_email ON team_memberships (team_id, invited_email)
  WHERE status = 'invited';

-- 4) ⛔ RE-CREATE trigger functions — they reference the old table name and would
--    break every signup after the rename (P0-1):
--    handle_new_user(): INSERT INTO team_memberships (user_id, team_id, team_name,
--      key_hash, graph_name, role) VALUES (NEW.id, '', 'provisioning...', 'pending', '', 'owner')
--      ON CONFLICT (user_id, team_id) DO NOTHING;
--    update_user_team(): UPDATE team_memberships SET ... WHERE user_id = p_user_id;
--    (DROP + CREATE both functions in 0003.)

-- 5) ⛔ Application-code references (P1 — NOT covered by ALTER TABLE RENAME; all
--    break after the rename unless updated in the same change):
--    • supabase/functions/tenant-provision/index.ts:123  supabase.from("user_teams") → "team_memberships" (upsert; set role='owner')
--    • website/welcome.html (poll + reveal)              .from("user_teams") → "team_memberships" + call reveal_api_key RPC (4.1b)
--    • J-1 step 4, W-1, W-3 workflow text: "user_teams" → "team_memberships"
--    Grep gate: `grep -rn "user_teams" supabase/ website/ tortoise/` must return zero hits post-migration.

-- 6) ⛔ Placeholder-row M:N semantics (P1): handle_new_user inserts placeholder
--    (user_id, team_id='', key_hash='pending', role='owner'). Under uq_member_team
--    (user_id, team_id), the placeholder is a DISTINCT row from the real membership.
--    Provisioning must UPDATE the placeholder row specifically
--    (WHERE user_id = X AND team_id = '') then flip team_id to the real value in
--    the SAME upsert — no second row, no phantom membership. E6/reveal filter
--    team_id='' defensively. Multi-team E2E asserts no phantom row.

-- 7) ⛔ Column-level api_key protection (P1): RLS filters ROWS, not columns —
--    row-owner SELECT can read api_key directly, bypassing reveal RPC null-once.
REVOKE SELECT (api_key) ON public.team_memberships FROM authenticated;
```

**RLS (owner-row read + invitee + service role):**
- `authenticated` SELECT: `USING (user_id = auth.uid())` — **excludes the `api_key` column** (only the reveal RPC reads it).
- `authenticated` SELECT (invitee): `USING (status='invited' AND invited_email = auth.jwt() ->> 'email')` — SELECT only; post-signup email match. ⚠ GitHub-OAuth JWT may lack the `email` claim → invite resolution gap (flag; fallback = invite link token or manual support).
- `service_role`: ALL.
- **Accept path (P1-4):** authenticated users have NO UPDATE policy. Accept (invited → active, set user_id) MUST go through a service-role FastAPI endpoint (or targeted RLS policy). The accept endpoint routes through SDK `membership_create` (or the same tier-driven `max_users` gate) so Team-tier limits can't be bypassed. **Token-only accept in v1 (decision 1e); invitee email-match SELECT policy retained defensively but not the accept path.**

**Re-invite of removed member (P2-5):** a `status='removed'` row keeps its (user_id, team_id) pair → re-invite conflicts with uq_member_team. Re-invite = `ON CONFLICT (user_id, team_id) DO UPDATE SET status='invited', invited_email=$email`.

**Decision — invite representation:** membership-row-with-status (v1). SDK `invitation_create` exists — align or wrap; accept path uses SDK `membership_create`.

## 4.1b API key reveal-once (A13) — the null-once mechanism (P0-2)

**Current:** welcome.html:423-450 client-SELECTs plaintext `api_key` via RLS; nothing nulls it; `.single()` 406s for users with >1 membership.

**Target (SECURITY DEFINER RPC — atomic reveal + null):**
```sql
CREATE OR REPLACE FUNCTION public.reveal_api_key(p_user_id uuid, p_team_id text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE k text;
BEGIN
  -- ⛔ Authorization guard (P1): SECURITY DEFINER bypasses RLS — the caller MUST
  -- prove they are the row owner, else any authed user could exfiltrate + null
  -- another user's key. auth.uid() is available inside SECURITY DEFINER functions.
  IF auth.uid() IS NULL OR auth.uid() <> p_user_id THEN RETURN NULL; END IF;
  SELECT api_key INTO k FROM public.team_memberships
   WHERE user_id = p_user_id AND team_id = p_team_id AND status = 'active' AND role = 'owner';
  IF k IS NULL OR k = 'pending' THEN RETURN NULL; END IF;
  UPDATE public.team_memberships SET api_key = NULL, updated_at = now()
   WHERE user_id = p_user_id AND team_id = p_team_id;
  RETURN k;  -- shown once; nulled atomically
END; $$;
GRANT EXECUTE ON FUNCTION public.reveal_api_key TO authenticated;
```
- welcome.html calls `reveal_api_key(user_id, team_id)` (no `.single()`, no client SELECT of `api_key`).
- The key remains **team-scoped (owner row only)**; `APIKey` node (hash) is the source of truth for auth.

## 4.2 Control-plane registry (FalkorDB `control_plane` graph)

**Current Team node (hosted_api.py:320):** `max_users:1, max_teams:1, max_graphs:1` hardcoded.

**Target Team node:**
```
(:Team { id, name, tier: 'free'|'solo'|'pro'|'team',
         max_users, max_graphs,               ← from pricing.json (canonical, decision 1d), set at create; NO max_teams field (user-level capability)
         ops_allowance, graph_size_cap,      ← NEW (billing-epic constants now; enforcement later)
         created_at, backup_enabled: false })
```
**New Graph node (team↔graph 1:N):**
```
(:Graph { id, team_id, name, kind: 'default'|'custom', created_at, point_count })
(:Graph)-[:BELONGS_TO]->(:Team)
```
Existing `team_{team_id}` namespace = the team's **default graph** (graph.id='default'). **Custom-graph namespaces are RESERVED but NOT minted in v1** (decision E2E-11 — all writes resolve the default graph until a custom-graph consumer exists); E5 returns `graph_name` as an identifier string only. `max_graphs` enforced by counting `(:Graph {team_id})`.

**Backfill (P2-6):** (a) set `tier='free'` + limits on existing Team nodes; (b) create `(:Graph {id:'default', team_id})` per existing team AND make `/v1/team`/points endpoints resolve the default graph for back-compat (no break); (c) `point_count` is denormalized — document count-query as the source or a maintenance path.

## 4.3 APIKey node (unchanged shape, tier-driven limits)

```
(:APIKey { id, team_id, key_hash, key_prefix, created_by, created_at, revoked_at })
```
Limits: `max_api_keys` per tier (currently flat 20 via `_check_team_limit` fallback — make tier-driven). `points`/`sessions` limits: keep flat fallbacks (1000/1000) in v1 OR fold into `ops_allowance` — **decision: keep points/sessions flat in v1; ops_allowance (write ops) is the billing metric, decided in billing epic** (P2-7).

## 4.4 Integrity constraints

| Constraint | Level | Enforcement |
|---|---|---|
| user↔team unique (user, team) | DB | `uq_member_team` |
| one active invite per (team, email) | DB | partial unique index |
| team↔graph 1:N | App/registry | count `(:Graph {team_id})` vs `max_graphs` |
| max_users per team | SDK | `membership_create` (exists) — tier-driven; **invite-accept path uses it too** |
| max_teams per user | App | **NOT a tier limit** — per-user team-creation rate limit (abuse posture); multi-team is a user capability |
| key scoping | API | `get_current_team` resolves key→team (exists) |
| API key auth bootstrap (session→team) | NEW API | JWT → user_id → membership → team (W-2 dependency) |
| api_key null-once | DB/API | `reveal_api_key` RPC (4.1b) |

## 4.5 Tier limits table (from product/pricing.json — canonical single source, decision 1d; pricing.md is the generated mirror)

> **Per-team semantics (owner-confirmed 2026-08-07):** the tier describes ONE team's resources. **Multi-team is a USER capability, not a tier feature** — any user may be a member of N teams (rate-limited creation for abuse); each team independently selects its plan. No `max_teams` tier field.

| Tier | max_graphs/team | max_users/team | max_api_keys | included ops/mo | max graph nodes | integrations |
|------|-----------------|----------------|--------------|-----------------|-----------------|--------------|
| Free | 1 | 1 | 2 | **10,000** | 10,000 | unlimited |
| Solo | 2 | 1 | 5 | 10,000 | 25,000 | unlimited |
| Pro | ∞ | 2 | 10 | 50,000 | 100,000 | unlimited |
| Team | ∞ | ∞ | 20 | 200,000 | 600,000 | unlimited |

> **Integrations unlimited at ALL tiers (owner-confirmed):** more integrations = more usage = more value; abuse controlled by connection rate-limit, not tier caps.

*(ops_allowance + graph_size_cap numeric values from pricing.json (canonical); points/sessions stay flat 1000/1000 in v1; **planned Pro/Team features — per-graph keys, daily backups, usage dashboard, webhooks, export, audit log, ReBAC — declared intent in pricing.md, tracked as future work, NOT built in this epic**.)*

> **Canonical source (decision 1d):** `product/pricing.json` (machine-parseable tier/limits table incl. max_api_keys) is canonical; `product/pricing.md` is doc-generated from it; the pricing page AND E2E-13/14 assert against the JSON. 4.5 mirrors pricing.json.

---

# SUB-STEP 5 — ARCHITECTURE

## 5.1 Target-state topology

```
┌─────────────────────────────────────────────────────────────────────┐
│ BROWSER                                                             │
│  tortoise.premiselabs.co (CF Pages, static)                         │
│    product.html · signup.html · welcome.html · pricing section      │
│  app.premiselabs.co (CF Pages, React SPA)                           │
│    dashboard: team/graph switcher · overview · keys · sessions ·    │
│                members · create-team dialog                         │
│  Both use Supabase JS client: PKCE + parent-domain cookie            │
│  (Domain=.premiselabs.co) — session shared across subdomains        │
└──────────────┬──────────────────────────────────────────────────────┘
               │ session cookie (same-site, shared)
┌──────────────▼──────────────────────────────────────────────────────┐
│ SUPABASE (ybetwichurajbfswfeqa)                                     │
│  Auth (OAuth + email) · team_memberships (M:N junction, RLS)       │
│  reveal_api_key RPC · auth hook → tenant-provision (edge fn)        │
└──────────────┬──────────────────────────────────────────────────────┘
               │ service-role / internal key
┌──────────────▼──────────────────────────────────────────────────────┐
│ FASTAPI (api.premiselabs.co) — tortoise/hosted_api.py               │
│  /internal/provision · /internal/demo                               │
│  /v1/team · /v1/team/keys (POST/GET/DELETE) · /v1/sessions ·        │
│  /v1/points · /v1/search · /v1/context                              │
│  NEW: session→team auth bootstrap (JWT→user→membership) ·           │
│  /v1/teams (create) · invite accept (uuid4 token — NOT JWT-signed, │
│  token-only in v1) · email-gap = manual support (decision 1e) ·    │
│  tier enforcement · /v1/session/key (session-scoped key mint) ·     │
│  reconciliation sweep for stuck pending provisionings               │
│  MCP sub-app at /mcp (mcp_auth TeamResolutionMiddleware — #338      │
│       auth_mode default "tenant")                                   │
└──────────────┬──────────────────────────────────────────────────────┘
               │ FalkorDB
┌──────────────▼──────────────────────────────────────────────────────┐
│ FALKORDB                                                             │
│  control_plane registry: Team (tier+limits) · Membership (role) ·   │
│    APIKey (hash) · Invitation · Graph (1:N)                         │
│  tenant namespaces: team_{id} (default graph) · team_{id}_{gid}     │
│    (custom graphs — reserved, NOT minted in v1; writes → default)  │
│    (custom graphs)                                                   │
└─────────────────────────────────────────────────────────────────────┘
```

## 5.2 Component boundaries

| Component | Responsibility | Owns | Depends on |
|---|---|---|---|
| Static pages (CF Pages) | Auth UI, key reveal, pricing | signup/welcome/pricing HTML | Supabase JS, reveal RPC |
| Dashboard SPA | Team/graph context, key mgmt, onboarding | React app | Supabase session cookie, hosted API |
| Supabase | Auth, junction table, RLS, reveal RPC | team_memberships | — |
| tenant-provision edge fn | Signup → provision orchestration | provisioning glue | Supabase, FastAPI internal |
| hosted_api (FastAPI) | Multi-tenant API, tier enforcement, key lifecycle | /v1/* + /internal/* | FalkorDB registry |
| MCP sub-app | Agent-facing MCP surface | /mcp (58 tools) | hosted_api auth (shared — in-process middleware stack: `get_current_team` + `TeamResolutionMiddleware` share the FastAPI app; `tt_` key arrives via Authorization header on streamable-HTTP/SSE connect, resolved by TeamResolutionMiddleware) |
| FalkorDB | Registry + tenant graphs | Team/Membership/APIKey/Graph | — |

**Clean boundaries:** browser never touches FalkorDB; dashboard never touches edge fn; edge fn never touches tenant namespaces directly (calls FastAPI). Session auth (Supabase) is orthogonal to API auth (tt_ keys) with the bootstrap bridge.

## 5.3 Key architectural decisions

1. **Cross-subdomain session** (A5): PKCE + parent-domain cookie (`Domain=.premiselabs.co`), custom storage adapter or @supabase/ssr. Both pages configured identically.
2. **Auth model (reconciled, P1):** TWO auth tiers, cleanly separated:
   - **Session endpoints E1–E8** (`/v1/session/key`, `/v1/teams`, `/v1/invites*`, `/v1/graphs`, `/v1/teams/{id}/members*`): authenticated by **Supabase JWT (JWKS-verified server-side)**. These are the account/session surface — the dashboard uses its session JWT here, no `tt_` key needed.
   - **Data-plane endpoints** (`/v1/points`, `/v1/search`, `/v1/sessions`, `/v1/team`, `/v1/team/keys`, MCP): authenticated by **`tt_` key** (unchanged). The dashboard holds a **session-scoped `tt_` key** (minted via E1) in sessionStorage for these.
   - This replaces the earlier "only E1 verifies JWTs" draft (contradicted E2–E8). The bootstrap endpoint (E1) mints the session key; it is NOT the only JWT-verified endpoint.
2b. **JWT verification (P1):** all E1–E8 verify Supabase access tokens via **JWKS fetch from `{SUPABASE_URL}/auth/v1/.well-known/jwks.json`** with issuer + audience (project ref) + `exp` validation, cached with TTL; KID-miss → refetch-then-fail; bounded fetch timeout + retry; alert on E1 5xx/401 rate. Per-identity rate limit on E1 (mint rate limit — J-2 abuse posture). Shared-HMAC rejected.
3. **Key delivery (A13):** provision response carries key → welcome reveal RPC nulls plaintext. Single delivery path.
4. **Tier enforcement:** Team node carries limits; checks in SDK/hosted_api; registry count queries for race safety.
5. **Decoupling:** junction table (M:N) + Graph node (1:N); default graph back-compat.
6. **#338 alignment:** hosted `auth_mode="tenant"` unchanged; self-host daemon = `"static"/"none"` (separate image, no hosted machinery).
7. **Failure handling:** demo seed best-effort with E2E verification — **fix the known 404 first: edge fn calls `/v1/internal/demo` (index.ts:137) but the route is `/internal/demo` (hosted_api.py:784); correct the edge URL or add an alias**; provisioning retry with exponential backoff + jitter + max attempts in the edge fn (thundering-herd guard), pending-state on welcome, server-side reconciliation (cron/ops sweep re-provisions pending rows > N min) — **provision is IDEMPOTENT keyed on user_id: edge fn checks for an existing non-pending team_memberships row before minting; /internal/provision returns existing team if membership already resolved (race-safe vs in-flight edge retries, P2-9)**; abuse posture (rate limit minting, unconfirmed-user cleanup).

## 5.4 Failure modes

| Failure | Detection | Mitigation |
|---|---|---|
| Hook not wired | no membership row | fix Supabase config (A1) |
| Edge env missing | welcome stuck pending | secrets + retry |
| Provision 502 | pending row | retry edge, alert |
| Session cookie not shared | dashboard shows key login | fallback path (exists) |
| CORS preflight fail | dashboard API 4xx | verify allowlist (A7) |
| Reveal RPC not deployed | welcome stuck | deploy migration 0003 first |
| Graph back-compat miss | /v1/team breaks | default-graph resolution (4.2) |
| MCP tenant mode regression | hosted suite fails | #338 additive auth_mode; G1 gate |

---

# SUB-STEP 6 — INTERFACES (contract-first)

> **Standardized auth-error envelope (applies to all /v1/* session endpoints E1–E8 below — JWT-authed, JWKS-verified):**
> `401` missing/invalid JWT (JWKS failure) · `403` no membership/RBAC violation · `422` FastAPI validation · `429` rate limit. Only endpoint-specific errors are listed per contract.
> **Data-plane endpoints (`/v1/points`, `/v1/search`, `/v1/sessions`, `/v1/team`, `/v1/team/keys`, MCP) remain `tt_`-key-authed (unchanged).** The dashboard authenticates to session endpoints with its Supabase JWT and to data-plane endpoints with the session-scoped `tt_` key from E1.

## 6.1 Existing endpoints (contracts UNCHANGED — verify in E2E)

| Endpoint | Method | Auth | Contract |
|---|---|---|---|
| /v1/team | GET | tt_ key | `{team_id, tier, max_users, max_graphs, point_count}` — no max_teams (user-level capability) |
| /v1/team/keys | POST | **tt_ key ONLY (unchanged — see E1 for session mint)** | `{id, key, key_prefix, created_at}` (key shown once) · **gains 402 on tier cap (auth unchanged, additive)** |
| /v1/team/keys | GET | tt_ key | `[{id, key_prefix, created_at, last_used_at, revoked_at}]` |
| /v1/team/keys/{id} | DELETE | tt_ key | 204 · **409 "would leave team keyless" (only-key revoke block, J-2)** |
| /v1/sessions | GET/POST | tt_ key | session list / create |
| /v1/points | GET/POST | tt_ key | point CRUD |
| /v1/search | GET | tt_ key | `{q, ...}` → results |
| /internal/provision | POST | FASTAPI_INTERNAL_KEY | `{team_id, team_name, api_key_hash, created_by}` → `{team_id, api_key, graph_name}` |
| /internal/demo | POST | FASTAPI_INTERNAL_KEY | `{team_id}` → demo seeded |

> **Auth-transition rule (P1-4):** `/v1/team/keys` POST stays `tt_`-authed — **no breaking change**. Session-scoped minting goes through E1 exclusively. J-2 step 2 / W-2 updated: "mint via `POST /v1/session/key`, then list/revoke via `/v1/team/keys` with the minted key".

## 6.2 NEW endpoints (contract-first)

### E1: POST /v1/session/key — session-scoped key mint (PRIMARY dashboard auth)
```
Auth: Supabase JWT (JWKS-verified) → user_id
Body: { team_id?: string, purpose?: 'bootstrap'|'recovery' }   # purpose defaults 'bootstrap'; team_id optional — resolution rule below
200: { key: "tt_...", key_prefix, expires_at: ISO|null, team_id }
     # bootstrap → expires_at 24h; recovery → expires_at null (persistent, revocable)
403: no membership · 402: max_api_keys cap (recovery mint — **unless user has no usable key → auto-revoke oldest orphaned, per E2E-3**) · 429: rate limit
```
**Resolution rule (P2-8):** single membership → that team; multiple → `400 "team_id required"`; zero → `403` with detail pointing to E2 (create team).
**Key lifecycle (P1 — three fixes):**
- **`purpose` param (P1):** `bootstrap` mints the 24h ephemeral session key (dashboard auth); **`recovery` mints a PERSISTENT revocable key (no 24h expiry)** — otherwise the #518 recovery deliverable mints a self-destructing key. Recovery keys count against tier `max_api_keys` (Free 2/Solo 5/Pro 10/Team 20+); bootstrap keys are **EXEMPT** from the cap (counted separately, swept when expired) — else a Free user's 2nd dashboard tab hits 402 and the J-1/J-3 flagship flow breaks.
- **Reuse-before-mint (P1):** E1 reuses an existing unexpired bootstrap key for (user, team) from sessionStorage before minting — no mint-per-load.
- `APIKey` node gains `expires_at` + `created_via:'bootstrap'|'recovery'|'dashboard'|'provision'`; `get_current_team` rejects expired keys. SignOut: client-side discard + 24h server backstop. Expired bootstrap keys swept by the reconciliation job. **Orphaned unrevealed provision keys: reconciliation sweep expires/revoles `created_via='provision'` keys not revealed within N hours.**
**Purpose:** dashboard primary auth (decision 2), key recovery (J-2), powers E6/E2 context.

### E2: POST /v1/teams — team creation (W-2c, J-4 zero-teams)
```
Auth: Supabase JWT (JWKS-verified) → user_id
Body: { name: string (1..64, [a-zA-Z0-9 _-]) }
201: { team_id, graph_name: "team_{team_id}" (default graph, graph.id='default'), tier: 'free' }
409: name collision · **429: team-creation rate-limit (abuse posture — not a tier block)**
```
**Purpose:** create-first-team empty state; tier defaults Free (upgrades = billing epic).

### E3: POST /v1/invites — invite to team (W-2b, J-4 step 4, Team tier)
```
Auth: Supabase JWT → team membership (owner/admin)
Body: { team_id, email, role: 'admin'|'member' }
201: { invite_id, status: 'invited', token, expires_at }
403: not owner/admin · 402: max_users reached · 409: active invite exists
```
**Implementation (P1-3):** wraps SDK `invitation_create` (uuid4 plaintext token, `token_hash` + `expires_at` stored on Invitation node — **NOT a JWT-signed scheme**; align E4 language). The FastAPI handler returns the plaintext `token`; the **client** renders the emailed link (server email-send is out of v1 scope — invite link copied/shown in dashboard + copy-to-clipboard; email delivery deferred to a future email issue). **Token-only resolution in v1 (decision 1e); email-match retained defensively as SELECT-only RLS, NOT a resolution fallback (manual support for email gap).**

### E4: POST /v1/invites/accept — accept invite
```
Auth: Supabase JWT (post-signup user)
Body: { token }                      # plaintext uuid4 token from E3 (token-only accept in v1 — decision 1e)
201: { team_id, role }
400: invalid/expired token · 402: max_users reached at accept · 409: already a member
**Email-match fallback DROPPED for v1 (decision 1e):** GitHub-OAuth email gap documented as a known limitation with manual support path (mirrors deferred invite email delivery).
**Token single-use (P2):** accept CONSUMES the token (Invitation node `consumed_at` set / deleted); second accept of the same token by a different user → 409.
```
**Purpose:** invitee → active member. Routes through SDK `membership_create` (max_users gate, tier-driven).

### E5: POST /v1/graphs — create graph in team (W-4, J-4 step 2)
```
Auth: Supabase JWT → team membership
Body: { team_id, name }
201: { graph_id, graph_name: "team_{tid}_{gid}", kind: 'custom' }
403: no membership in team · 404: unknown team · 409: graph name collision in team · 402: max_graphs reached
```
**Purpose:** team↔graph 1:N; Free/Solo caps enforced here.

### E6: GET /v1/teams — list my memberships (J-3/J-4 team switcher)
```
Auth: Supabase JWT
200: [{ team_id, team_name, tier, role, graph_count, default_graph_id }]   # excludes team_id='' placeholder rows (4.1 step 6)
```
**Purpose:** populates team switcher; drives per-team billing display.

### E7: GET /v1/graphs?team_id= — list graphs in team (P2-7, J-4 switcher)
```
Auth: Supabase JWT → team membership
200: [{ graph_id, name, kind: 'default'|'custom', point_count }]
403: no membership · 404: unknown team
```
**Purpose:** populates the graph dropdown (names + ids).

### E8: Member management (P1-1 — P-5 surface)
| Endpoint | Method | Purpose | Errors |
|---|---|---|---|
| /v1/teams/{team_id}/members | GET | list members | 403 non-member · 404 |
| /v1/teams/{team_id}/members/{user_id} | DELETE | remove member | 403 non-owner/admin · **409 owner cannot be removed/demoted** |
| /v1/teams/{team_id}/members/{user_id} | PATCH | role change (admin/member) | 403 · 409 owner-demotion |
| /v1/invites/{invite_id} | DELETE | cancel invite | 403 · 404 |
| /v1/invites/{invite_id}/resend | POST | resend (rate-limited 429) | 403 · 404 · 429 |

## 6.3 Error contract (standardized)

```
{ "detail": "message" }          # FastAPI default — keep
400 Bad Request                  # E1 team_id required · E4 invalid/expired token
402 Payment Required             # tier limit → client shows soft-block + upgrade CTA
403 Forbidden                    # RBAC violation / no membership
404 Not Found                    # unknown team/invite/member
409 Conflict                     # duplicate (invite, name, graph), owner-removal, only-key revoke
422 Validation Error             # FastAPI validation
429 Too Many Requests            # rate limit (bootstrap mint, invite resend)
```
**Versioning:** URL versioning `/v1/` (existing). No breaking changes to current contracts — new endpoints additive; `/v1/team/keys` POST auth unchanged.

## 6.4 Event schema (funnel analytics — wire hooks, impl #528)

| Event | Emitter | Payload |
|---|---|---|
| user_signed_up | web | user_id, auth_provider, utm |
| email_confirmed | web | user_id |
| tenant_provisioned | server | user_id, team_id, status (`confirmed`|`unconfirmed`) — **carries confirmation state (P2-11)** |
| tenant_cleaned_up | server | user_id, reason: unconfirmed-expired |
| dashboard_opened | web | user_id, team_id |
| session_key_minted | server | user_id, team_id (fires per mint; funnel counts distinct users) |
| first_api_call (activation) | server middleware | user_id, team_id, endpoint, latency |

**Funnel filtering (P2-11):** the `signed_up → provisioned → dashboard_opened → first_api_call` funnel excludes never-confirmed accounts (filter on `tenant_provisioned.status='confirmed'` OR the `tenant_cleaned_up` event).

## 6.5 Supabase RPC surface

| RPC | Purpose | Auth |
|---|---|---|
| reveal_api_key(user_id, team_id) | atomic reveal+null (A13) | SECURITY DEFINER + auth.uid() guard + role='owner' |
| (none other needed — membership CRUD via FastAPI service-role) | | |

## 6.6 APIKey node extension (P1-2)

```
(:APIKey { id, team_id, key_hash, key_prefix, created_by, created_at, revoked_at,
           expires_at,          ← NEW — session keys (E1)
           created_via })        ← NEW — 'bootstrap' | 'recovery' | 'dashboard' | 'provision' (E1 purpose → created_via; 'bootstrap'=24h ephemeral, 'recovery'=persistent revocable)
```
`get_current_team`: reject when `revoked_at IS NOT NULL` **OR** (`expires_at` IS NOT NULL AND `expires_at < now`).

---

# SUB-STEP 7 — DETAILED E2E TEST CASES

Each test: **Setup** (concrete preconditions) · **Steps** (verifiable actions) · **Assertions** (observable outcomes) · **Negative cases**. Mapped to the 14 high-level E2E from scope.

## E2E-1: Hosted signup (email/password) → provision → key revealed once
- **Setup:** remote Supabase with email confirmation **OFF** (assert the actual setting in setup — flipped toggle fails loudly, not confusingly), fresh browser profile, no account for test-email
- **Steps:** 1) land on /signup 2) fill email+password 3) Create account 4) land on /welcome.html 5) read key, refresh page
- **Assertions (user-visible primary):** `tt_` key shown once with copy button · refresh shows returning-user state (no re-reveal) · key authenticates against GET /v1/team. **Supporting (DB-level):** `team_memberships` row exists (pin the post-migration name; role=owner, status=active) · FalkorDB team + default graph exist · demo points present (**after demo-seed 404 fix**) · **service-role check: `api_key IS NULL` after reveal**
- **Poll strategy (async provisioning):** welcome shows the pending/retry state first, then bounded poll — wait for key-reveal or error state within N seconds (deterministic regardless of provisioning latency)
- **Negative:** duplicate email → "already exists, sign in" · wrong password (<6 chars) → inline error · no session on welcome → error state
- **Negative (security, reveal RPC — NEW):** authenticated user B (member of another team) calls `reveal_api_key(user_id=A, team_id=T)` → returns NULL AND does NOT null A's key · non-owner row member calling RPC → NULL · authenticated SELECT on `team_memberships` cannot read `api_key` column (RLS exclusion) · DB-level `api_key IS NULL` after reveal

## E2E-2: Hosted signup (OAuth) → provision → key revealed once
- **Setup:** controlled test GitHub account **with a public verified email + email scope granted on the OAuth app** (pin the fallback: if the account lacks a verified email, skip with flag OR assert via the manual-support/key-delivery path); OAuth app configured
- **Steps:** 1) /signup 2) Continue with GitHub 3) authorize 4) return to /welcome.html
- **Assertions:** provider-verified email (no confirmation) · membership row role=owner · key revealed once · same hardening as E2E-1
- **Negative:** OAuth cancel → signup error state · OAuth failure → error + retry · **missing email claim → documented manual-support/key-delivery path, not a silent fail (no email-match in v1 — decision 1e)**

## E2E-3: Key recovery via rotation (no chicken-and-egg)
- **Setup:** provisioned user, Supabase session, NO remembered tt_ key
- **Steps:** 1) dashboard (session-authed) 2) Lost your key? Generate a new one → `POST /v1/session/key` (E1) 3) copy new key 4) revoke old via /v1/team/keys/{id}
- **Assertions:** new key minted without pre-existing key · shown once · authenticates against /v1/team · **revoked old key fails auth immediately (401) — pin this, no "or grace" ambiguity** · E1 mint at `max_api_keys` cap → 402 (NEW)
- **Negative:** rate limit on mint (429) · expired session → 401 · **expired bootstrap key rejected by get_current_team (backdated expires_at via fixture → 401)** · **only-key revoke in a SEPARATE single-key scenario: revoking the only key → 409 warn**
- **#518 property pinned (P2):** recovery key returns `expires_at: null` AND authenticates against /v1/team after 24h; bootstrap key returns `expires_at = +24h` — a regression to 24h recovery keys fails this test
- **Keyless-at-cap recovery (P2):** user with NO usable key at max_api_keys cap → recovery mint **succeeds** (auto-revokes the oldest orphaned key to free a slot, or exempt from cap with a hard per-identity recovery limit — decision: auto-revoke oldest orphaned) — asserts recovery never dead-ends for the #518 user
- **Bootstrap-key active backstop (P2):** max active bootstrap keys per (user, team) = 3 (swept by reconciliation) — asserted via fixture

## E2E-4: Cross-subdomain session — signup on tortoise → authed on app
- **Setup:** **creates its own session (explicitly depends on the E2E-1 signup flow OR fixture-authenticates the browser via the Supabase auth API)**; parent-domain cookie Domain=.premiselabs.co; **both subdomains must resolve in the test environment (hosts mapping / cookie Domain config)** 
- **Steps:** navigate to app.premiselabs.co
- **Assertions:** dashboard reads cookie → authenticated view (no key paste) · team switcher populated (E6) · signOut clears cookie everywhere
- **Negative:** no cookie (old session) → API-key login fallback · session expired → redirect to sign-in

## E2E-5: Dashboard API-key login coexists with session auth
- **Setup:** user signed out (no session); valid `tt_` key from `provision_test_user` fixture (added to fixture reuse list)
- **Steps:** open app.premiselabs.co → paste valid tt_ key → Connect
- **Assertions:** API-key mode shows Overview/Keys/Sessions · both modes present same data
- **Negative:** invalid key → "Invalid API key" error (verified live today)

## E2E-6: Dashboard empty-state onboarding → first memory rendered
- **Setup:** team provisioned with **demo seeding DISABLED** (`provision_test_user(demo_seed=False)` — NEW fixture param; normal provisioning demo-seeds, so the empty state is otherwise unreachable). Assert BOTH variants: seeded (E2E-1's demo-points assertion) and empty (this test) to cover the J-1 edge.
- **Steps:** open Overview → see empty state → "Connect your agent" (primary) → run quickstart → create first point
- **Assertions:** empty state = ONE primary action (connect-agent) + secondary (create point) · point appears RENDERED in dashboard list (not just toast) · API 200 (regression #292 guard)
- **Negative:** point creation 500 (regression) → surfaced error, no silent fail

## E2E-7: Funnel analytics — signup → first API call tracked
- **Setup:** **GATED: skip/defer until #528 lands** (PostHog instrumentation is out-of-epic; the epic wires event hooks only). Pre-requisite merge: #528.
- **Steps:** complete signup, provision, dashboard open, first API call
- **Assertions:** events `user_signed_up`, `tenant_provisioned(status)`, `dashboard_opened`, `first_api_call` in PostHog joined on user UUID · TTFV computable · never-confirmed accounts excluded from funnel
- **Negative:** ad-blocker → CF Worker proxy path (deterministic simulation only) · distinct_id mismatch → joined correctly
- **Flake control:** poll PostHog event queries with timeout (async ingestion); assert event hooks fire at the emitter level as the deterministic part

## E2E-8: Email confirmation branch (if remote setting ON)
- **Setup:** **flip remote Supabase email-confirmation ON for the run, restore OFF after** (driven from test config; assert the actual setting in setup so E2E-1 fails loudly if the toggle wasn't restored); OR run against a second Supabase project. **E2E-1 (OFF) and E2E-8 (ON) are mutually exclusive on the same project — never both green without the toggle+restore step.**
- **Steps:** signup email/password → check-your-inbox state → click confirmation link → return
- **Assertions:** "check your inbox" + resend (rate-limited) · returning via link completes exchange → welcome with provisioned key · key reveal gated on email_confirmed
- **Negative:** never-confirmed account → `tenant_cleaned_up` after expiry · resend rate-limited (429)

## E2E-9: Self-hosted flow — land → install → first memory (no hosted account)
- **Setup:** fresh machine, no tortoise. **GATED: full daemon assertions (health/MCP/onboard) require parallel epic #338's self-host daemon — v1 asserts smoke-level (docs/GitHub route reachable without hosted account + BSL license line); full daemon assertions land post-#338.**
- **Steps:** landing → "Self-hosting docs →" → GitHub/docs → `docker compose up` OR pip install → `tortoise onboard`
- **Assertions (v1, smoke-level):** self-host route reachable without hosted account · BSL license line visible. **Deferred to post-#338 (daemon-dependent):** daemon /health 200 · MCP connect (`claude mcp add tortoise http://localhost:8000/mcp`) · first local point
- **Negative:** daemon down → tortoise_unavailable graceful

## E2E-10: User↔team decoupling — one user, two teams in parallel
- **Setup:** Alice owns Solo team; client's Team team invites her
- **Steps:** accept invite → switch teams in switcher → use key from team A against team B
- **Assertions:** E6 lists both teams · team switcher works · **key from team A fails against team B (401)** · per-team billing display correct · E1 resolves team_id required (400) on ambiguity
- **Negative:** removed from team → zero-teams state → create-team dialog (E2). **Zero-graphs state removed (P2):** the data model guarantees a default graph per team (4.2 backfill + back-compat) and no graph-deletion endpoint is in scope — the empty state is unreachable; the switcher always shows ≥1 graph.
- **Phantom-row assertion (4.1 step 6):** multi-team user's E6 list contains NO row with team_id='' (placeholder filtered) — asserted in this test
- **Billing display:** assert the tier/labels shown per team (per-team billing semantics visible); actual payment collection is out-of-scope (billing epic) — do NOT assert charges

## E2E-11: Team↔graph 1:N — multiple graphs with tier limits
- **Setup:** Pro team; Free team; Solo team
- **Steps:** create graphs in each; attempt over-cap
- **Assertions:** Pro creates N graphs (E5) · Free blocked at 1 (402 soft-block + upgrade CTA) · Solo blocked at 2 (loss-leader cap) · graph switcher lists all (E7)
- **v1 scope decision (P1):** custom-graph **writes are DEFERRED** — registry rows + switcher (E5/E7) ship; all data-plane writes (points/search/sessions/MCP) resolve the **default graph** in v1; custom namespaces are NOT minted until a consumer exists. E2E-11 asserts graph creation/limits/switcher only, NOT custom-graph point writes (documented as future work).
- **Negative:** graph name collision (409) · no membership (403) · unknown team (404)

## E2E-12: Team-tier collaboration — invites + RBAC
- **Setup:** Team-tier team (Alice owner); **Bob + Carol accounts created via `provision_test_user` (invitee without account → signup-first then accept, per W-2b)**; tester captures the plaintext invite `token` from the E3 response (email delivery out of v1 scope — link copied/shown in dashboard)
- **Steps:** invite Bob (member), Carol (admin) → Bob/Carol accept (E4 with token) → test RBAC
- **Assertions:** members listed (E8 GET) · Bob cannot create/revoke keys or manage members (403) · Carol can manage members · owner cannot be removed/demoted (409) · Free/Solo/Pro cannot invite (invites hidden)
- **Negative:** expired invite token (400) · max_users reached at accept (402) · duplicate invite (409) · **same token accepted by a SECOND different user → rejected (409 — token single-use, decision)** · GitHub-OAuth email-gap → token-only accept with documented manual-support path (no email-match fallback in v1)

## E2E-13: Pricing structure documented and enforced
- **Setup:** product/pricing.json committed (canonical — decision 1d; pricing.md generated from it); provision path live. **Tier injection:** no user-facing tier path exists in v1 (provision defaults Free; upgrades = billing epic) — the `provision_test_user(tier)` fixture (ADDED to reuse list for E2E-13) writes the Team node's tier + limits directly (FalkorDB registry write or test-only internal endpoint).
- **Steps:** create team on each tier path (fixture); query /v1/team
- **Assertions:** tier-driven limits (max_graphs/max_users) match **pricing.json** (canonical — decision 1d) · /v1/team returns tier + limits (no max_teams — user-level) · enforcement matches the JSON (E2/E5 402 paths)
- **Negative:** legacy team without tier → defaults Free

## E2E-14: Pricing page renders hosted tiers + self-hosted section
- **Setup:** pricing page live on tortoise.premiselabs.co
- **Steps:** scroll to pricing; toggle monthly/annual; read self-hosted section
- **Assertions:** Free/Solo/Pro/Team cards with ✓/✕ rows · toggle swaps prices (annual -20% default, from pricing.json display.annual_discount_pct) · **"$5 per additional 10k write ops" visible (pricing.json display.overage_line)** · **segmented positioning renders — "Use Tortoise" AND "Build with Tortoise" (pricing.json display.segments)** · **integrations "unlimited" visible on all cards (pricing.json display.integrations_line)** · self-hosted section: BSL 1.1 + $5M AUG + MPL-2.0-in-4yrs (pricing.json display.license_self_hosted) + "migrate to cloud anytime" CTA · hosted CTA primary
- **Negative:** pricing data drift vs **product/pricing.json** (canonical — decision 1d; fail on mismatch; **static copy sections assert against pricing.json display.* fields, no markdown parsing**)

---

## Testability notes

- **Setup primitives:** helper fixture `provision_test_user(tier, demo_seed=True)` (Supabase test user + edge-fn invoke or direct internal/provision + membership row; `demo_seed=False` skips the demo call for E2E-6; `tier` writes the Team node for E2E-13). Reused across E2E-1/3/4/5/10/11/12/13. Test tiers: Free default, Solo/Pro/Team via direct registry write.
- **Deterministic keys:** test keys minted per-test; assert on prefix not full value.
- **Demo seed:** E2E-1 asserts demo points exist — requires the demo-seed 404 fix (W-1/decision 7) first.
- **Playwright surfaces:** signup/welcome/dashboard/pricing = browser-automated (test-e2e skill); API assertions = httpx against api.premiselabs.co or in-process FastAPI.
- **Additional coverage (wired into concrete tests — do NOT leave as claims-only):**
  - **Provisioning rate-limit (abuse posture, deliverable 8b):** E2E-1 negative extension — repeated provision attempts for the same identity exceed the per-identity limit → 429/blocked.
  - **Demo-graph size cap (deliverable 8b):** E2E-1 negative extension — demo seeding stops or is capped at the size cap.
  - **Stuck-pending → reconciliation (W-1 failure path):** E2E-1 negative extension — simulate hook/edge failure → row stuck at `status='pending'` → welcome shows pending/error → reconciliation sweep re-provisions → resolves to active + key reveals once.
  - **Concurrent-provision race (P2):** fire TWO provision attempts in parallel (in-flight edge retry + sweep) → assert EXACTLY ONE Team node in FalkorDB, ONE APIKey mint, `/internal/provision` returns the existing team on the second call, zero duplicate/phantom team_memberships rows (E6 filter).

---

# SUB-STEP 8 — COHERENCE REVIEW + RISK ANALYSIS

## 8.1 Cross-substep consistency check

| Substep | Key artifacts | Cross-checked against |
|---|---|---|
| 1. Journeys | J-1..J-6, 5 personas | Scope E2E-1..14, UX-D1..D4 |
| 2. Workflows | W-1..W-6 | Journeys (alignment table) |
| 3. Prototype | P-1..P-6 | Journeys, UX decisions, design system |
| 4. Data model | 4.1-4.5 | Workflows (W-1/W-2/W-2b/W-2c/W-4) |
| 5. Architecture | 5.1-5.4 | Data model, interfaces, #338 |
| 6. Interfaces | 6.1-6.6 (E1..E8) | Workflows, data model, architecture |
| 7. Detailed E2E | E2E-1..14 | Scope E2E-1..14, interfaces (E1..E8) |

**Consistency invariants verified:**
- Key recovery routes through **E1 (POST /v1/session/key, purpose='recovery' — persistent)** everywhere (J-2, W-2, E2E-3, 6.1 auth-transition rule) — no stale `/v1/team/keys`-via-session references, no 24h expiry on recovery keys (fixed P1-4).
- `reveal_api_key` RPC has the **auth.uid() guard** in data model (4.1b) AND negative tests in E2E-1.
- Demo-seed 404 bug flagged in W-1, architecture decision 7, AND E2E-1 setup (prerequisite fix).
- `team_memberships` name pinned across migration, workflows, E2E (no `user_teams` chameleon).
- Tier limits single-sourced from **`product/pricing.json`** (canonical — decision 1d; pricing.md is the generated mirror; enforced at runtime from the JSON; data model 4.5, W-4, E2E-13/14 reference it).
- BSL + $5M AUG license framing consistent across pricing.md, pricing page (P-3), E2E-14, #338 alignment.

## 8.2 Risk analysis

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | Cross-subdomain cookie/PKCE session fails in practice (browser nuances) | med | high (#519 core) | Research-verified pattern; E2E-4; fallback API-key login retained |
| R2 | Supabase email-confirmation toggle mismatches code assumption | med | high | E2E-1/E2E-8 assert actual setting loudly; toggle+restore step |
| R3 | Decoupling migration (0003) breaks existing provisioning in prod | med | high | Trigger re-creation (4.1), grep gate zero `user_teams`, E2E-1 |
| R4 | Reveal RPC security bypass | low | critical | auth.uid() guard + role='owner' + negative tests |
| R5 | Demo-seed 404 not fixed → new users see empty graph | high (exists today) | med | W-1/decision 7/E2E-1 flag; edge URL fix is one line |
| R6 | #338 auth_mode refactor regresses hosted MCP | low | high | Additive param, default byte-identical, full hosted suite = G1 gate |
| R7 | Session-key expiry backstop untested | med | med | E2E-3 expired-key rejection test |
| R8 | Invite email delivery deferred → Team-tier onboarding friction | high (v1 scope) | med | Token in API response + dashboard copy; email issue filed separately |
| R9 | Funnel analytics (#528) delays activation measurement | med | low | Hooks wired in epic; #528 gated test |
| R10 | Per-team billing semantics confuse single-team users | low | low | Tier display in switcher (E2E-10) |
| R11 | Pricing page drift from the canonical limits | low | med | E2E-14 fail-on-mismatch against pricing.json (single source, decision 1d) |
| R12 | Thundering herd on provision during FastAPI outage | med | med | Backoff + jitter in edge fn (decision 7) |
| R13 | Session-key churn exhausts tier caps (Free dashboard dead-end) | med | high | Bootstrap keys EXEMPT from max_api_keys + reuse-before-mint (E1) |
| R14 | Recovery keys self-destruct (24h expiry defeats #518) | high (as drafted) | high | E1 `purpose:'recovery'` mints persistent revocable keys |
| R15 | Migration 0003 deploy skew breaks prod signup/dashboard | med | high | Deploy-order checklist + rollback + one-transaction migration (below) |
| R16 | JWKS single point of failure (dashboard auth depends on live fetch) | med | med | KID-miss refetch, bounded timeout+retry, API-key fallback (E2E-5), alert on E1 5xx |
| R17 | Tier-limit enforcement races (concurrent mints/graphs/accepts) | med | med | Serialize per-team checks (advisory lock / atomic counter), unique backstops, parallel E2E |
| R18 | XSS reads session-scoped key from sessionStorage | low | high | Strict CSP on app.premiselabs.co, no dangerouslySetInnerHTML on point content, tight mint rate limit |
| R19 | PostHog availability degrades API (first_api_call middleware) | low | med | Fire-and-forget non-blocking with timeout, drop-on-failure (hook contract in-epic) |
| R20 | Supabase outage takes down entire hosted surface | low | high | Availability alerting, "having trouble — retry" states, status-page entry |

### Deploy order + rollback (R15)

**Order (each step independently verifiable, run 0003 in ONE transaction during low traffic):**
1. **Migration 0003** (rename → columns → unique → REVOKE api_key → trigger re-create → reveal RPC) — one transaction; migration smoke test first.
2. **Edge fn** (team_memberships + role='owner' + demo URL fix) — deploy + verify a test signup.
3. **welcome.html** (team_memberships poll + reveal RPC call) — deploy.
4. **FastAPI** (E1–E8, tier enforcement, session bootstrap) — deploy; verify E1 with a test JWT.
5. **Dashboard SPA** (session auth, E1 mint) — deploy.

**Rollback (mid-flight, per step):** 1) rename back `team_memberships` → `user_teams`, drop new columns/constraints, re-create original functions + RLS; 2–5) revert the artifact + restore prior version. Test rollback of step 1 explicitly before prod window.

## 8.3 Improvement opportunities

1. **provision_test_user fixture** as a shared test primitive — one fixture powers 8 E2E cases; invest in its robustness (tier injection, demo_seed flag, key return).
1b. **Invite/RBAC surface (E3/E4/E8, W-2b, P-5) — KEEP in scope (owner build-now directive):** reviewers flagged it's unreachable by real users until billing ships (tiers only fixture-injectable). Decision: build it now per the owner's "build identified things now while design context is fresh" rule — it ships fixture-tested + admin-testable; the billing epic wires the upgrade path that makes it user-reachable. The data model (role/status/invited_email) is required for decoupling regardless; the API surface is cheap now, costly to retrofit later.
1c. **`user_teams` → `team_memberships` rename — KEEP (decision):** reviewers flagged it as cosmetic churn creating trigger-recreation risk. Decision: the semantic change (1:1 → M:N junction) is real and the name communicates it; the risk is fully mitigated (trigger re-creation + grep gate + rollback step). Renaming later would be MORE costly (contracts/docs/tests already pin it).
1d. **Pricing single-source → mechanical (P2):** create `product/pricing.json` (full tier/limits table incl. max_api_keys 2/5/10/20+ and ops/size placeholders) as an EXPLICIT epic deliverable; pricing.md is doc-generated from it; the pricing page AND E2E-13/14 assert against the JSON; **FastAPI/Team-node creation loads limits from pricing.json at runtime (no hand-edited copies in code)**; no markdown parsing in tests.
1e. **E4 email-match fallback — DROP for v1:** ship token-only accept; document the GitHub-OAuth email gap as a known limitation with manual support path (mirrors deferred invite email delivery).
1f. **Planned Pro/Team features (declared intent in pricing.md, tracked NOT built):** per-graph API keys, daily backups + restore, usage dashboard (ops consumed / overage runway / per-graph), webhooks on memory events, data export (JSONL), Team audit log, per-graph ReBAC (access-policy layer on existing Graph BELONGS_TO Team + Membership — schema already supports it). Each becomes a future issue; the pricing page shows current vs planned features honestly (no over-promise).
2. **E1 session-key mint as the single key path** — simplifies the whole auth story; consider making `/v1/team/keys` POST deprecated-in-docs (still tt_-auth, but dashboard uses E1) to reduce surface.
3. **Demo-seed as idempotent + size-capped** — make the 404 fix also enforce the size cap (abuse posture) in the same change.
3b. **Custom-graph write path** — deferred in v1 (E2E-11 decision): registry + switcher only, all writes to default graph; revisit when a custom-graph consumer exists.
4. **Reconciliation sweep** could double as the expired-key sweeper (one job, two purposes).

## 8.4 Plan readiness

The plan is internally consistent, risks identified with mitigations, and ready for decomposition. **Human gate #2: coherence approved → epic-decompose.**

---

# PLAN COMPLETE — Ready for epic-decompose

---

# IMPLEMENTATION COMPLETE — 2026-08-08

## Deployed to production
- Migration 0003 (M:N decoupling, reveal RPC, column security) — applied
- Edge function tenant-provision — deployed
- Static pages (signup/welcome/signin JS fix + pricing) — deployed to tortoise-landing-v2
- Dashboard (session auth + onboarding) — deployed to tortoise-dashboard

## Merged (all 11 child issues)
| Issue | PR | What |
|---|---|---|
| D1 #568 | #615 | Decoupling + migration + JWKS endpoints |
| D2 #569 | #612 | Signup JS bug fix (#527) |
| D3 #570 | #618 | Session-key mint (E1, #518 fix) |
| D4 #571 | #620 | Reveal-once welcome (RPC) |
| D5 #572 | #623 | Dashboard session auth |
| D6 #573 | #627 | Dashboard onboarding surface |
| D7 #574 | #629 | Invites + RBAC |
| D8 #575 | #594 | Pricing page |
| D9 #576 | #634 | Email confirmation + reconcile |
| D10 #577 | #635 | Analytics hooks |
| D11 #578 | #641 | E2E suite (74 tests) |

## Also shipped
- Dispatch-infra fix (agent-infra PR #130 — skill_declaration + 660s kill)
- product/pricing.json + pricing.md (canonical tiers → features → competitive)
- product/competition/honcho.md (new profile)
- Follow-ups: #528 (PostHog), #529 (harness onboarding), #557 (sub-tenancy), #524 (MCP OAuth criteria)
