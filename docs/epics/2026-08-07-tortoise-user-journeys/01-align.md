---
title: "Strategy Alignment Decision — Tortoise Product User Journeys"
type: engineering
domain: platform
doc_status: approved
subjects.team: organisation-design-team
created: 2026-08-07
---

<!-- epic: tortoise-user-journeys → issues #518 + #519 -->

# Strategy Alignment Decision — Tortoise Product User Journeys

**Date:** 2026-08-07
**Status:** PROCEED — approved (review gate CLEAN, 2026-08-07)
**Epic:** Two product flows for Tortoise — **self-hosted** (OSS) and **hosted** (PRIMARY) — with a well-thought-through user journey from landing to first memory.
**Seed issues:** #518 (no public tenant-provisioning journey) · #519 (dashboard is a non-functional shell)

---

## 0. Grounding — current state (verified live, 2026-08-07)

The seed issues describe a state from earlier the same day. **Live verification shows the journey is partially built already:**

| Surface | Status (verified) |
|---|---|
| Landing `premiselabs.co` | LIVE — links to tortoise + dashboard + self-host GitHub |
| Product page `tortoise.premiselabs.co` (product.html) | LIVE (200) — "Connect your agent →" CTA to /signup, MCP snippet, CLI quickstart, self-hosting link |
| `signup.html` | LIVE (200) — Supabase OAuth (GitHub/Google) + email/password, redirects to /welcome.html |
| `signin.html` | LIVE — exists |
| `welcome.html` | LIVE (200) — polls `user_teams` table, reveals `tt_` key, MCP config copy, dashboard link |
| `tenant-provision` edge function | EXISTS + deployed (405 on GET ⇒ function live) — PBKDF2 hash matching auth.py, upserts user_teams, seeds demo graph |
| Migration `0001_user_teams.sql` | EXISTS — user_teams table + `handle_new_user()` trigger + RLS |
| Hosted API `api.premiselabs.co` | `/internal/provision`, `/v1/team`, `/v1/team/keys` (POST/GET/DELETE), `/v1/sessions`, `/v1/context` — health: pepper + internal key + api auth all configured |
| Dashboard `app.premiselabs.co` | React SPA exists (`premise-labs/apps/dashboard/src/main.jsx`) with **API-key auth** ("Enter your API key" in deployed bundle) + Overview/Keys/Sessions tabs |

### What is still genuinely missing (the real scope)

1. **#518 — Key recovery is not surfaced; security posture undecided.** `POST /v1/team/keys` requires an existing valid key (Bearer auth), but **key recovery IS technically possible today**: nothing nulls `user_teams.api_key` (the migration comment says it *should* be nulled — no code implements it), and RLS lets the owner read their own row (exactly what welcome.html's poll does). The real gaps: (a) no dashboard/self-serve recovery surface, (b) a security decision on null-once vs re-reveal, (c) the key staying plaintext client-readable indefinitely is an unsurfaced exposure (XSS/leaked session → live key).
2. **#518 — End-to-end flow unverified.** Signup → trigger → edge function → provision → welcome reveal has never been walked E2E as a journey (dogfooding T7 is blocked on this). Includes an unverified wrinkle: signup.html's email/password path assumes email confirmation is **disabled** on the remote Supabase project — if enabled, the welcome.html session poll fails for that path.
3. **#519 — Dashboard is API-key-auth only.** No Supabase session integration; a fresh OAuth user must copy the `tt_` key from welcome.html into the dashboard manually. No onboarding, no session persistence beyond localStorage.
4. **No coherent dual-flow journey design.** Self-hosted flow (land → GitHub → `pip install` → `tortoise init/onboard`) exists piecemeal but is not designed as a first-class path; hosted flow is primary but not verified end-to-end.
5. **Funnel is unmeasurable.** No analytics/instrumentation anywhere (signup → provision → welcome → dashboard → first memory is untracked). The profit chain cannot be tested without it.

---

## Step 1 — Adversarial Strategy Test

### Alternatives considered

1. **Fix #518/#519 narrowly (patch-only):** add a key-recovery endpoint + wire Supabase session into the dashboard. *Rejected as insufficient* — it ships two disconnected fixes without a designed journey. The user explicitly asked for "two flows" with a journey "very well thought through"; the value is in the coherent end-to-end design, not the endpoint list.
2. **Hosted-only epic, drop self-hosted flow design:** self-hosted is the OSS acquisition lever and the docs already exist. *Rejected* — the self-hosted path is how developers evaluate before committing to hosted; a muddy self-hosted journey leaks signups at the top of the funnel. It stays in scope but at lower depth than hosted.
3. **Wait for onboarding epic #235 (Phase 2 gate):** #235 covers welcome page v2, yes/no questions, GitHub indexing. *Rejected as the vehicle for this work* — #235 is gated on user signal; #518/#519 are P1 blockers *preceding* that signal. Provisioning + dashboard must work before onboarding polish is even reachable.
4. **Staged: hotfix T7 blocker now, journey design in parallel.** Ship key-recovery + E2E verification as a 2-3 day fix to unblock dogfooding while the journey-design epic runs. *Partially adopted*: verification of the live wiring and a minimal recovery surface are genuinely urgent for T7 and can be front-loaded as the epic's first scope slices (decomposed as early child issues). But the user's explicit ask — two flows with a journey "very well thought through" — is epic-sized design work; staging it into a separate bug-fix-only track would fragment the design ownership and still leak users at the welcome/dashboard boundary. The epic proceeds as one track, with the urgent T7-unblocking work front-loaded.
5. **Full epic: dual-flow journey design + hosted provisioning verification + key recovery + dashboard session auth + funnel analytics.** ✅ *Chosen* — covers the seed issues and produces the thought-through journey the user asked for.

### Argue AGAINST the chosen approach

- **"The pages are already live — is this an epic or a bug fix?"** The plumbing exists but the *journey* doesn't: signup→welcome has never been walked E2E, key recovery is broken (chicken-and-egg), and the dashboard bypasses the Supabase session the user just created. Each of these is a P1; together they are the product's front door. This is epic-scale because it spans 4 surfaces (landing, auth, API, dashboard) and requires journey design, not one-file patches.
- **"Dashboard session auth is over-engineering — API-key paste works."** It works for developers but forces a two-step login (copy from welcome → paste into dashboard) and offers no recovery when the key is lost. The hosted product's *primary* promise is "sign up → your agents remember." A user who can't get back into their own dashboard churns immediately. Session auth is the minimal correct foundation.
- **"We don't know if signups exist yet — building journey polish before traction is premature."** #518/#519 are P1 *because* the funnel can't even be tested (dogfooding T7 is blocked). You cannot measure signup conversion when no new user can complete the flow. Fixing the journey is the precondition for measuring anything — which is why the epic includes funnel analytics, not just plumbing.
- **"Public auto-provisioning is a spam/abuse/cost vector."** True: signup → auto-provision of a FalkorDB namespace + demo graph per user, with no rate limiting, is a resource-exhaustion exposure. The epic must include a minimal abuse posture (rate limit per identity, demo-graph size cap) — surfaced as an assumption below, not hidden.

### Opportunity cost

If we don't do this: dogfooding T7 stays blocked, the swarm's agents can't get `tt_` keys, external signups (if any) silently fail or churn at the welcome/dashboard boundary, and every downstream metric (conversion, retention) remains unmeasurable. The next-highest-leverage alternative is #235's onboarding work — but that *builds on* a working provision→dashboard baseline. This epic is the foundation; #235 is the polish.

---

## Step 2 — Eisenhower Matrix

| | Urgent | Not Urgent |
|---|---|---|
| **Important** | ✅ **This epic — Do now** | Strategy docs, pricing |
| **Not Important** | — | Internal tooling chores |

**Justification:** P1 severity is given in both issues. It is *important* because the hosted product's entire revenue path runs through signup→first-memory; it is *urgent* because T7 dogfooding is actively blocked and each day without a walkable journey accumulates unbounded opportunity cost on a live product surface.

**Best action for profit growth right now?** Yes — this is the only work that unblocks the primary monetization path (hosted) while preserving the OSS acquisition lever (self-hosted).

---

## Step 3 — Profit Growth Alignment

**Causal chain:**
```
Working dual-flow journey
  → hosted: signup → provision → welcome → dashboard → agent connected → first memory
  → self-hosted: land → GitHub → install → tortoise init/onboard → local memory
  → developers see value ("my agent remembers why, not just what")
  → hosted retention + word-of-mouth (swarm dogfooding, MCP ecosystem)
  → future: free tier → paid tier conversion (#296 monetization path)
  → revenue
```

**Is there a faster path to the same outcome?** No — every faster path skips the journey design and ships patches that still leak users at the welcome/dashboard boundary. The funnel is the product.

**Rough magnitude:** No pricing exists yet, so this epic's direct revenue is $0/month. Its value is *enabling* the funnel: it converts an unmeasurable, broken funnel into a walkable one. Estimated downstream: each percentage point of signup→first-memory completion is the multiplier on every future pricing dollar. Order of magnitude: this is the $0→funnel-enabler step, prerequisite to the $100s/month (first hosted tiers) the platform epic targets.

---

## Step 4 — Decision Rationale

## Strategy Alignment Decision

**Feature:** Tortoise Product User Journeys — self-hosted + hosted flows (hosted PRIMARY), landing → first memory, with key recovery + dashboard session auth.

**Decision:** PROCEED

**Alternatives considered:**
1. Patch-only fix of #518/#519 — *rejected*: no designed journey, still leaks users at the welcome/dashboard boundary.
2. Hosted-only scope — *rejected*: self-hosted is the OSS acquisition lever and stays in scope at lower depth.
3. Fold into onboarding epic #235 — *rejected*: #235 is signal-gated; #518/#519 are P1 blockers *before* that signal.
4. Full epic (chosen) — designed dual-flow journey + hosted E2E verification + key recovery + dashboard session auth.

**Profit impact:** Enables the primary monetization funnel (hosted) + preserves OSS acquisition (self-hosted). Direct revenue $0 now; the multiplier for all future hosted pricing. Causal chain: journey works → signups complete → agents connect → value demonstrated → retention → future conversion.

**Eisenhower placement:** Important + Urgent → **Do now**. Honest caveat: the *urgent* driver is internal (T7 dogfooding blocked) — external signups are unconfirmed, so urgency is real but bounded. The *important* driver is the hosted monetization path. The convenient-but-wrong move would be splitting the 2-3 day T7 hotfix into a separate track that ships without the journey design; instead the urgent slice is front-loaded *inside* the epic so both constraints are served.

**Key assumptions:**
- Supabase after_user_created hook → tenant-provision → /internal/provision is wired on the remote project (config.toml comment claims it; MUST verify live) — confidence: **medium**
- The welcome page polling loop resolves a provisioned key within a reasonable window (<30s) — confidence: **medium** (unverified E2E)
- Email/password signup can complete the hosted journey: signup.html assumes email confirmation is **disabled** on the remote Supabase project (no confirmation-branch handling in welcome.html poll). If enabled, that path breaks — MUST verify remote setting — confidence: **medium**
- Key recovery can be self-serve via the existing `user_teams` row (nothing nulls `api_key` today); the epic decides null-once vs re-reveal as a security posture, and must account for the current indefinite-plaintext exposure — confidence: **high** (verified in code)
- Dashboard can add Supabase session auth without breaking the existing API-key login (both modes coexist) — confidence: **medium** (unimplemented; dashboard must point at the SAME Supabase project as signup/welcome)
- Self-hosted flow needs journey *design + links*, not engine work (`tortoise onboard` is robust) — confidence: **high**
- Browser → api.premiselabs.co CORS/auth path works for session-authenticated calls (only bearer-by-paste is verified working) — confidence: **medium** (unverified)
- Public auto-provisioning needs minimal abuse posture (rate limit per identity, demo-graph size cap) to avoid namespace spam — confidence: **medium** (no rate limiting exists)

**Recommendation:** PROCEED. Scope the epic as: (1) dual-flow journey design (hosted primary, self-hosted secondary), (2) hosted provisioning E2E verification + key-recovery surface (fixes #518), (3) dashboard Supabase session auth + onboarding surface (fixes #519), (4) funnel analytics to make the profit chain testable. Research first to verify the live wiring assumptions (hook wiring, email-confirmation setting, CORS) before locking scope. Front-load the T7-unblocking verification/recovery slice as early child issues.

---

## Step 5 — Routing

**PROCEED** → hand off to **epic-research** (via shared/research) for the next pipeline stage.
