---
title: "Epic Research Brief — Tortoise Product User Journeys"
type: engineering
domain: platform
doc_status: draft
subjects.team: organisation-design-team
created: 2026-08-07
---

<!-- epic: tortoise-user-journeys → issues #518 + #519 -->
<!-- research-path: docs/epics/2026-08-07-tortoise-user-journeys/01-align.md -->

# Epic Research Brief — Tortoise Product User Journeys (self-hosted + hosted)

**Date:** 2026-08-07
**Status:** draft
**Research depth:** deep (epic scope)
**Domain:** engineering + ux + growth
**Inputs:** Align Decision (PROCEED) · seed issues #518/#519 · onboarding epic #235 research brief (prior art)

---

## 1. Strategy Context

### Market position (reconfirmed)

Tortoise sits at the intersection of agent memory and epistemic graphs. The hosted platform (api.premiselabs.co) is live since #236, exposing 58 MCP tools over Streamable HTTP with tenant-scoped Bearer `tt_` keys. The self-hosted OSS alternative remains for developers who want local FalkorDB.

**Competitors (agent memory):** Agent Memory (agent-memory.dev) — install → start → connect → verify; Mem0 — embedding-based, no graph/EP; Claude Code native memory — file-based `.claude/`. **No competitor offers an epistemic graph with belief propagation** — Tortoise's differentiator is structure + belief, not just recall. This makes onboarding about *explaining value* ("my agent remembers why, not just what"), not just connection.

### Bifurcation positioning (new finding — WEB research, HIGH confidence)

Langfuse/PostHog pattern: **hosted is the default/easiest path; self-host is positioned for control & sovereignty** (secondary CTA/section). Community consensus: new SaaS defaults to hosted unless data-residency/cost constraints. For Tortoise, where hosted is PRIMARY: landing hero leads with hosted ("Connect your agent →"), self-host is a secondary route ("Self-hosting docs →" / GitHub), matching the existing product.html structure — **this validates the current landing page architecture; the gap is journey depth, not hero structure**.

### Business model

No pricing yet. Hosted = monetization path (future free→paid tier, epic #296); self-hosted = free OSS acquisition lever. **Onboarding is the conversion lever** — the funnel must be walkable before it can be measured.

---

## 2. UX Pattern Research

### API-key reveal (WEB research, HIGH confidence)

- **Reveal-once is the dominant norm** (Stripe, Vercel, OpenAI, Supabase): show key exactly once at creation, pair with copy button + "store it now" warning + "you won't see this again" framing.
- **Recovery = rotation, never retrieval.** Lost key → generate new one. Vercel/GitHub/AWS/Broadcom all refuse re-show.
- Zuplo's *hide-until-needed* counterpoint (MEDIUM): for a `tt_` key that is the core artifact, immediate reveal at welcome is justified — but should be the *only* exposure, with a regenerate path on the dashboard.
- **Security framing belongs in the reveal UX:** store in `.env`, never client-side, never in repos.

### Self-hosted vs hosted bifurcation (WEB research, MEDIUM confidence)

Hero CTA = hosted; "Self-host" secondary tab/button + Docs route. Positioning language: "fully managed, easiest way to start" (hosted) vs "control & sovereignty" (self-host). **Anti-pattern: making the choice a decision.** Default to hosted, present self-host as a toggle/alternative.

### Dashboard onboarding surface (WEB research, HIGH confidence)

- **First-login empty state is the highest-risk screen** (highest abandonment of any dashboard screen).
- Canonical empty-state formula: ① explain what appears here + why it matters, ② show the shape of success, ③ ONE obvious primary action, ④ sample data / low-commitment escape hatch. "Two parts instruction, one part delight."
- **Aha-moment pattern for API-key products:** paste key → run literal first call → see live result render in the dashboard. "First memory" should be *rendered visibly*, not abstractly acknowledged.
- Quickstart step-checklists are the standard scaffolding.

### Time-to-first-value (WEB research, HIGH confidence)

- Developer-infra median TTFV: **10–20 minutes** from signup to activation; median activation rate **25–35%**.
- Target: **< 15 min** signup → first success for developer tools.
- Implication for Tortoise hosted flow: zero-config key reveal → copy-paste MCP config → one command creating + showing memory. Every extra step attacks the activation band.

### Anti-patterns / funnel leaks (WEB research, HIGH confidence)

Top leaks: ① no clear first action / dashboard dead-end; ② too many setup steps before value; ③ "I'll get to it later" — low-intent signups that never return if welcome doesn't convert in-session; ④ confusing empty states; ⑤ **key-loss lockout** with no rotation story.

---

## 3. Workflow Pattern Research

### Hosted flow (PRIMARY) — target state

```
land (tortoise.premiselabs.co)
  → signup (Supabase OAuth/email)  [exists]
  → provision (auth hook → tenant-provision → /internal/provision)  [exists, UNVERIFIED E2E]
  → welcome: reveal tt_ key ONCE + copy + "you won't see this again" + MCP config block  [exists, needs reveal hardening]
  → dashboard (app.premiselabs.co): Supabase session auth  [MISSING — currently API-key paste only]
  → dashboard empty-state: "your first point" quickstart + live-rendered first memory  [MISSING]
  → key recovery: dashboard "Lost your key? Generate a new one" (rotation)  [MISSING — POST /v1/team/keys exists but needs existing key to call]
  → funnel analytics: user_signed_up → email_confirmed → tenant_provisioned → dashboard_opened → first_api_call  [MISSING]
```

### Self-hosted flow (secondary) — target state

```
land → "Self-hosting docs →" → GitHub README → pip install → tortoise init → tortoise onboard (init→index→demo→doctor)
```
Existing self-hosted CLI is robust (`_cmd_onboard`: 5-step chain, idempotent, banners). **Gap is journey depth on the landing/docs surface, not engine work.**

### Key recovery pattern (WEB research, HIGH confidence)

**Rotate-on-demand is the industry standard**: dashboard button → mint new key (shown once) → optional 24–72h grace overlap → revoke old. Multiple active keys (2–3) for zero-downtime rotation. This requires **no plaintext storage** — aligns with the existing hash-only `key_hash` design. Compromised keys: revoke-first, audit log.

### Email confirmation UX branch (WEB research, HIGH confidence on default / MEDIUM on pattern)

- Hosted Supabase defaults to **email confirmation ON**; `signUp()` returns `session: null` until the link is clicked. Signup pages must therefore render a **"check your inbox" state** (verify email → resend button with cadence limit → deadline messaging) and complete the auth exchange when the user returns via the confirmation link (PKCE: `?code=` → `exchangeCodeForSession`).
- OAuth users (GitHub/Google) are email-verified by the provider → no confirmation needed (MEDIUM).
- **MUST verify the live remote setting** — signup.html currently assumes confirmation OFF (no check-your-inbox branch; welcome.html poll requires an active session). If ON, the epic adds the confirmation-branch screen; if OFF, the existing flow stands.

### Session→API-auth bridge (the dashboard's API layer) (internal + WEB reasoning, MEDIUM confidence)

The dashboard's Keys/Sessions tabs call `api.premiselabs.co` with bearer `tt_` keys today. Once the dashboard is session-authenticated (Supabase cookie), the bridge between "Supabase session" and "Tortoise API" must be decided — **candidate patterns (unresearched in depth; scope must pick):**

| Pattern | How it works | Pros | Cons | Confidence |
|---------|-------------|------|------|-----------|
| **Session-held key (recommended default)** | Welcome/provision response delivers `tt_` key once; dashboard stores it (sessionStorage/in-memory) and uses it for API calls; rotation via dashboard button | Minimal API change; matches hash-only posture | Key lives in browser memory; re-login re-runs provision response or re-fetch | medium |
| **Supabase JWT → FastAPI verification (JWKS)** | FastAPI verifies the Supabase access token (JWKS) and maps `sub` → team via `user_teams` | No `tt_` key in browser at all; true session-auth | FastAPI needs JWKS/issuer config + user_teams lookup per request; larger change | low (unresearched) |
| **Session → short-lived key exchange** | Dashboard exchanges session for a scoped short-TTL key via a new endpoint | Limited blast radius | New endpoint + key lifecycle | low (unresearched) |

**Validation plan:** prototype the session-held-key bridge first (least change); only if security review demands server-side session verification, invest in JWKS. This decision shapes the dashboard API layer.

---

## 4. Tech Stack Research

### Cross-subdomain Supabase session (WEB research, HIGH confidence)

**The core technical problem:** signup/welcome live on `tortoise.premiselabs.co`, dashboard on `app.premiselabs.co`. supabase-js v2 persists to `localStorage` by default → **origin-scoped → sessions do NOT cross subdomains.**

**The correct pattern:**
1. **Parent-domain cookies** (`Domain=.premiselabs.co; Path=/; SameSite=Lax; Secure`) on BOTH subdomains — via a custom `storage` adapter passed to `createClient`, or `@supabase/ssr`'s framework-agnostic `createBrowserClient`.
2. **PKCE flow** (`flowType: 'pkce'`) on both — tokens live in cookies, not localStorage.
3. Same cookie name on both (`sb-<project-ref>-auth-token`).
4. Explicit `exchangeCodeForSession(code)` on a callback page (PKCE does not honor `detectSessionInUrl` reliably — MEDIUM confidence).
5. Register all subdomains in Supabase Dashboard → URL Configuration.
6. **Public-suffix gotcha:** `Domain=.pages.dev` is rejected by browsers — we're safe on `premiselabs.co` apex.
7. **supabase-js v2 removed built-in cookie handling** — `cookieOptions` alone is insufficient; must supply a storage adapter or use @supabase/ssr.

**Verified-safe:** we own `premiselabs.co` (apex), so parent-domain cookies are viable.

### Plaintext api_key in user_teams (WEB research, HIGH confidence on guidance)

- **Current design is the weakest defensible:** plaintext + hash in same row, RLS-readable. RLS is query-layer only — does not protect against DB dumps/backups/direct access.
- **Industry default: hash-only, show-once, rotate-on-recovery** (Stripe, GitHub, AWS, Broadcom). We already store `key_hash` (PBKDF2 + pepper) — the plaintext column is the liability.
- **If re-reveal is non-negotiable:** envelope encryption (Supabase Vault: `vault.create_secret()`, KMS-wrapped keys) — MEDIUM confidence on current API surface.
- **Decision for the epic:** (a) stop re-revealing, migrate to hash-only + rotate-on-demand (recommended, matches industry + existing code), or (b) encrypt via Supabase Vault for session-authenticated re-reveal.

### Funnel analytics (WEB research, HIGH confidence)

**PostHog** = best fit: native funnels, tiny JS snippet for static pages, official Python SDK for server-side events (critical — `first_api_call` is a server-side event), generous free tier, Cloudflare Worker reverse proxy for first-party + ad-blocker bypass.
- Custom Supabase events table = most control/cheapest, but you build funnel queries yourself.
- Plausible = traffic only, no funnels in Community Edition — wrong tool for activation funnels.
- **Event schema:** `user_signed_up` → `email_confirmed`/`signup_verified` → `tenant_provisioned` (server) → `api_key_created` (server) → `dashboard_opened` → `first_api_call` (server middleware = activation). Identity: `identify(user_id)` web-side at signup; `distinct_id` = Supabase user UUID server-side.
- Activation = `first_api_call` within N days of signup; TTFV = delta.

### Abuse posture (internal finding)

Public auto-provisioning (signup → namespace + demo graph per user) has **no rate limiting** on the provisioning path (the existing 100/min limiter skips `/internal/*`). Epic must include minimal abuse posture: rate limit per identity, demo-graph size cap, cleanup of unconfirmed users.

---

## 5. Assumptions Register

| # | Assumption | Confidence | Source | Validation Plan |
|---|-----------|-----------|--------|-----------------|
| A1 | Supabase after_user_created hook → tenant-provision → /internal/provision wired on remote project | medium | config.toml comment; edge fn deployed (405 on GET) | Live E2E: create test user → verify team + key in user_teams |
| A2 | Welcome page polling loop resolves provisioned key <30s | medium | welcome.html code reads user_teams; unverified E2E | Live E2E walk of signup→welcome |
| A3 | Email confirmation setting on remote Supabase: if ON, email/password path breaks (no session on welcome) | medium | WEB research: hosted default ON; signup.html has no confirmation branch | Check Supabase dashboard / auth.users rows |
| A4 | Key recovery via session-authenticated self-serve is achievable; rotate-on-demand requires no plaintext | high | WEB research (Stripe/GitHub/AWS/Broadcom); /v1/team/keys exists | Implement dashboard rotation button; keep hash-only |
| A5 | Cross-subdomain session auth (parent-domain cookie + PKCE) is viable on premiselabs.co | high | WEB research; apex owned | Implement storage adapter; E2E: signup on tortoise → authed on app |
| A6 | Dashboard can add Supabase session auth coexisting with API-key login | medium | unimplemented; dashboard must use same cookie config | Prototype both modes in dashboard |
| A7 | Browser → api.premiselabs.co CORS/auth works for session-authenticated calls | medium | only bearer-by-paste verified | Test session-authenticated fetch from dashboard |
| A8 | Public auto-provisioning needs minimal abuse posture (rate limit per identity, demo cap) | medium | internal: no rate limit on /internal/*; WEB: abuse norms | Add per-identity rate limit + size caps |
| A9 | Plaintext api_key in user_teams is a security debt requiring a decision (null-once/hash-only vs Vault encryption) | high | verified in code (nothing nulls it); OWASP/WEB guidance | Epic scope decides; migration plan if hash-only |
| A10 | Self-hosted flow needs journey design + links, not engine work | high | `_cmd_onboard` robust; internal code review | Landing/docs journey pass; no CLI changes expected |
| A11 | after_user_created hook fires before email confirmation → teams/keys minted for unconfirmed users | medium | WEB research (auth architecture) | Verify hook timing live; decide lazy-provision vs cleanup |
| A12 | Funnel analytics via PostHog (JS snippet + Python SDK) fits static CF Pages + FastAPI stack | high | WEB research (PostHog docs, CF Worker proxy) | Implement snippet + server events; verify events land |
| A13 | **Provision response must deliver the `tt_` key exactly once; welcome renders from the response, not a later DB read** (null-once/hash-only migration would otherwise break the current welcome reveal, which reads plaintext from user_teams via RLS) | high | internal code analysis (welcome.html poll + migration 0001) | Scope the provision-response flow to return the key; welcome stores/renders from that response; `user_teams.api_key` nulled or removed |
| A14 | Session→API bridge: dashboard holds the `tt_` key from provision response (session-held key) for API calls; no server-side JWT verification in v1 | medium | internal reasoning + candidate patterns | Prototype; security review gate before JWKS investment |

---

## Synthesis — Implications for the Epic

1. **The plumbing is mostly built; the journey isn't.** Signup/welcome/edge function/API exist. The epic's core deliverables: (a) walk the hosted flow E2E and fix what breaks, (b) cross-subdomain session auth for the dashboard, (c) key recovery via rotation (kills the chicken-and-egg), (d) reveal-once hardening on welcome, (e) funnel analytics, (f) self-hosted journey depth on landing/docs.
2. **Security posture decision is required:** hash-only + rotate (recommended) vs Vault-encrypted re-reveal. This drives the migration of `user_teams.api_key`.
3. **The primary funnel metric** is signup → first_api_call (TTFV < 15 min, activation 25–35% band) — only measurable after analytics land.
4. **Email-confirmation setting + hook timing must be verified live** in research→scope transition; they change the welcome flow (check-your-inbox branch) and provisioning strategy (lazy vs eager).
5. **Do not build re-reveal as default** — rotation is the industry standard, cheaper, and matches existing hash-only storage.
6. **Reveal-once hardening is coupled to the storage migration (A13):** welcome currently reads plaintext from `user_teams` via RLS. The hardened design delivers the key exactly once in the provision response (edge-function mint → response → welcome renders it) and then nulls/removes the plaintext column. Scope must treat these as one work item, not two.
7. **#235 boundary (no scope bleed):** #235 owns welcome *v2 onboarding* (yes/no questions, GitHub indexing, demo-graph guided tour) — gated on user signal. This epic owns the welcome *key delivery + reveal hardening* (existing welcome page, not v2). If #235's Phase 2 lands later, it layers the yes/no flow on top of this epic's hardened key delivery.

---

## Raw Research Sources

**UX/Strategy sub-agent** (web research 2026-08-07):
- Stripe keys best practices — docs.stripe.com/keys-best-practices · docs.stripe.com/keys
- Vercel AI Gateway API keys (show-once) — vercel.com/docs/ai-gateway/api-keys · vercel.com/changelog/new-token-formats-and-secret-scanning
- OpenAI key safety — help.openai.com/en/articles/5112595-best-practices-for-api-key-safety
- Zuplo hide-until-needed + rotation — docs.zuplo.com/blog/api-key-authentication
- Langfuse self-hosting (Cloud default) — langfuse.com/self-hosting · PostHog self-host — posthog.com/docs/self-host
- Dashboard empty states — saasui.design/blog/saas-onboarding-ux-examples · useronboard.com/onboarding-ux-patterns/empty-states · zynra.agency/en/blog/saas-dashboard-design-patterns
- TTFV benchmarks — productgrowth.in/insights/saas/saas-onboarding-benchmarks · count.co/metric/time-to-first-value · business.daily.dev/resources/developer-onboarding-marketing-channel
- Funnel leaks — saasfoundersclub.org/blog/why-users-sign-up-but-never-use-your-saas · candystudio.design/blog/why-your-saas-dashboard-confuses-users

**Tech sub-agent** (web research 2026-08-07):
- Supabase cross-subdomain session — github.com/orgs/supabase/discussions/5742 · github.com/supabase/supabase-js/issues/1396 · supabase.com/docs/guides/auth/sessions/pkce-flow · supabase.com/docs/reference/javascript/auth-exchangecodeforsession · staticbot.dev/blog/supabase-auth-multi-nuxt-subdomains · micheleong.com/blog/share-sessions-subdomains-supabase · supabase.com/docs/guides/auth/server-side/creating-a-client
- PKCE detectSessionInUrl caveat — github.com/supabase/supabase-js/issues/931
- Plaintext-at-rest / hashing — OWASP Cryptographic Storage + Key Management + Secrets Management cheat sheets · apikeys.guide/docs/security/hashing-and-storage.md · docs.aws.amazon.com/kms/latest/developerguide/concepts.html (envelope encryption)
- Supabase Vault — supabase.com/docs (vault.create_secret) · key rotation — github.com/docs (PAT regenerate) · Zuplo rotation (mint-then-rotate 24–72h) — zuplo.com/blog/api-key-authentication
- Email confirmation default ON — supabase.com/docs/guides/auth/passwords · supabase.com/docs/guides/auth/sign-up
- PostHog funnels + events + CF proxy — posthog.com/docs/product-analytics/funnels · posthog.com/docs/product-analytics/capture-events · posthog.com/docs/advanced/proxy/cloudflare · PostHog vs Plausible — posthog.com/blog/posthog-vs-plausible
- Activation metrics — tarka.ai/playbook/fundamentals/posthog-custom-events · createmysaas.com/learn/growth/saas-activation-metrics-guide

---

## Source Confidence Summary

| Claim | Tier | Sources |
|-------|------|---------|
| Reveal-once norm (Stripe/Vercel/OpenAI) | HIGH | 5+ independent (vendor docs, Zuplo) |
| Bifurcation: hosted default, self-host secondary | MEDIUM | Langfuse/PostHog positioning; CTA structure inferred |
| Empty-state formula | HIGH | 6+ sources (saasui, useronboard, zynra) |
| TTFV < 15 min / activation 25–35% | HIGH | 5 sources (productgrowth.in, count.co, daily.dev) |
| Top-3 funnel leaks | HIGH | multi-source convergence |
| Rotation-as-recovery (Stripe/GitHub/AWS/Broadcom) | HIGH | uniform across vendors |
| Cross-subdomain cookie+PKCE pattern | HIGH | Supabase maintainer replies, docs, community walkthroughs |
| Email confirmation default ON (hosted) | HIGH | Supabase docs |
| Hook fires before confirmation | MEDIUM | inferred from auth architecture |
| Plaintext-at-rest guidance (OWASP) | HIGH | OWASP cheat sheets, vendor docs |
| PostHog fit + event schema | HIGH | PostHog docs, funnel playbooks |

⚠️ **single-source / emerging tags:** Zuplo hide-until-needed counterpoint (single vendor blog, MEDIUM); Supabase Vault API surface (MEDIUM — verify current docs); detectSessionInUrl unreliable under PKCE (MEDIUM — GitHub issue + docs variance).
