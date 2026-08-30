---
title: "Epic #1976 — Align Decision + Scope (combined review artifact)"
type: decisions
domain: strategy
doc_status: draft
subjects.team: epistemic-team
created: 2026-08-29
aboutSubjects: tortoise
aboutObjects: tortoise
---

# Epic #1976 — Align Decision + Scope (combined)

> **Review artifact for Human Gate #1** — combines the Stage-1 strategy alignment
> decision (`01-align.md`) and the Stage-3 epic scope (`03-scope.md`) into a single
> document for review. Source files remain authoritative; this file is a read-only
> merge for the human gate.
>
> **Pipeline state:** ALIGN ✅ (5 review cycles recorded, CLEARED) · RESEARCH ✅ (brief `02-research-brief.md`, CLEARED) · SCOPE ✅ (Human Gate #1 APPROVED 2026-08-29) · PLAN ✅ (coherence CLEARED; Human Gate #2 APPROVED 2026-08-29) · TEST-DESIGN #1992 ✅ · DECOMPOSE ✅ (MECE CLEAN, #1997-#2007) · CAPSTONE #2008 ✅ · VERIFY ✅ (`08-verify.md`).

---

# PART A — STRATEGY ALIGNMENT DECISION


# Strategy Alignment Decision — Epic #1976: Agent-driven onboarding

**Feature:** Rebalance Tortoise onboarding toward agent-driven setup — the wizard shrinks to 5 defined human steps (orientation, org-create/join, fork, connect-consent, done), the agent completes install/seed/decide in-context, the graph itself holds per-org onboarding state, and the Overview calms to 3 elements with zero toggles.

**Decision: PROCEED** (with two scope-guard rails — see Step 4).

> **Baseline evidence verified this session (2026-08-29, codebase):** the issue's load-bearing factual claims check out:
> - **M7 (6 harnesses):** `website/apps/dashboard/src/harnesses.js` `HARNESS_NAMES` = Claude Code, Claude Desktop, Claude Web, Codex, Cursor, Pi. ✅
> - **M6 (invite infra exists):** `tortoise/hosted_api.py` has `POST /v1/invites`, `GET /v1/invites/info`, `POST /v1/invites/accept`, `GET /v1/invites`, `GET /v1/invites/pending`, `POST /v1/invites/pending/{id}/accept`, `DELETE /v1/invites/pending/{id}`, `DELETE /v1/invites/{id}`. ✅
> - **M9 (no telemetry events for seed/decide):** `tortoise/analytics.py` emits only `tenant_provisioned`, `api_key_created`, `first_api_call`. No seed/decide events exist. ✅ (W11's gap is real)
> - **R2-9 (tool_registry exists):** `tortoise/tool_registry.py` registers sessions/docs indexer tools with a `ToolAnnotations` curation-group model. ✅
> - **W5 (onboarding state is Supabase jsonb today):** `tortoise/supabase_control.py` reads/writes `teams.onboarding_state` jsonb (`team_onboarding_state` / `update_onboarding_state`); `hosted_api.py` has `_get_onboarding_state` / `_update_onboarding_state` with a registered-keys allowlist. ✅
> - **W3 anchor-data claim:** `tortoise/session_auth.py` `verify_session_jwt` returns `{user_id, email, app_metadata}` — **no `display_name`**. The issue's correction ("email-prefix derivation; ask once if unusable") is accurate. ✅
> - **W2 (AGENT_ONBOARDING.md is live single-source):** exists at `tortoise/onboarding/AGENT_ONBOARDING.md`, self-declares single-source-of-truth (deployed via #540). ✅
> - **W1 (current wizard):** `docs/onboarding.md` documents the 5-step in-dashboard wizard (#1643) with harness chooser, skills primer, GitHub connect, STATE seed, done — exactly what this epic shrinks/archives. ✅

## Step 1 — Adversarial Strategy Test

### Alternatives considered

1. **Keep the current 5-step wizard (#1643) and polish it.** Lowest cost; the wizard exists, works, and has a re-entry card.
   *Why rejected:* the research basis is the best available pre-launch evidence — no comparable agent-tool product (mem0, Letta, Claude Code, Devin, Cursor) fronts with a post-signup wizard; interactive/agent-mediated setup beats static UI (50% higher activation, 28% lower drop-off — third-party stats, transferability caveat registered in A0). The wizard is the wrong *pattern*, not a wrong *implementation* — polish optimizes a pattern the market has abandoned for this product class.

2. **Ship a pure copy-paste setup command + agent prompt, skip the graph-held state and Overview de-toggling.** Minimal slice of this epic (W1+W2+W3 — the command, the agent skill, and the seed — **explicitly excluding W4/W5/W9**; the seed is included because a slice that doesn't reach the aha isn't a real alternative, it's a non-launch).
   *Why rejected:* the slice *does* reach the aha (two Subjects + one decide via W3), so the rejection rests on the two remaining planks: it leaves the "toggle wall gives me anxiety" observation unaddressed (W4), and it keeps onboarding state in Supabase jsonb instead of the graph the agent reads/writes (W5 — the product's own store holding its own state is a strict generalization of standard tenant-keyed architecture). It would be a launchable but incoherent half-measure that strands the user in the same confusion at the Overview step — the friction the research calls out is *at* the Overview, not before it.

3. **Wait for real users before rebalancing onboarding.** Pre-launch, the honest-framing METRICS section admits there's no baseline funnel.
   *Why rejected (and this is the sharpest challenge):* the counterargument is real — no users yet means the activation metrics are unmeasurable today. But the *cost of waiting* is asymmetric: onboarding is the first product experience every future user has; rebalancing it later means re-onboarding the founding cohort through a known-stopgap first impression, or accepting the wizard as the product's permanent face. The epic's own answer is sound: instrument now (W11), judge later, and judge by craft (friction test + proud test) until users exist. The fork/per-org semantics are the kind of structural decision that's cheap to make now and expensive to retrofit (cf. GitHub's user→org conversion deprecation, cited in the research). **Falsification is explicit: the premise that current wizard friction is real and representative (assumption A0 below) is tested by W11 events + walk-through reviews on the first cohort; if first-cohort friction is low, the rebuild was premature.**

4. **Build the agent's core capability (e.g. the reader answer surface, #1987) instead.** Opportunity cost check.
   *Why rejected for *this* cycle:* #1987 (the cohesion gap) is real and open, but onboarding is the gate through which *all* other product value is experienced — a user who can't get the agent connected and seeded never reaches the reader answer surface. The two are not truly competing: onboarding is the enabling layer; #1987 is a capability *on top of* a working first-run. Sequencing risk exists (see Rail 2).

### Anti-post-rationalization — strongest reasons NOT to build

- **The wizard shipped 6 days ago** (#1643, 2026-08-23). Replacing it immediately looks like churn. Counter: the wizard's own plan doc admits it was a stopgap built around the harness chooser; the research (2 rounds) and two fresh-context consistency passes were done *after* shipping, driven by real evidence. This is iteration on evidence, not restlessness.
- **Graph-held onboarding state (W5) is the riskiest architectural bet.** It moves state out of the battle-tested Supabase jsonb column into the graph with idempotent-write contracts (#398), versioning, and init-in-transaction rules. If the graph is the product, this is the *right* direction — but it's the piece most likely to destabilize if rushed. Rail 1 (below) addresses this.
- **The "agent knows its own harness" assumption (W2) is unproven across 6 harnesses.** The issue itself flags open question (b). Counter: the fallback is cheap (teach the human for the 2 manual harnesses; the universal command is the handoff). The risk is bounded — worst case the chooser components are un-archived.
- **12 workstreams is a lot for a pre-launch product with no users.** Scope-sprawl risk is real. Counter: W10 is explicitly deferred (needs RBAC first), W12 is small (two prompts + node init), and W11 is instrumentation-only. The critical path (W1-W5, W9, + W11 instrumentation) is coherent. Rail 2 keeps the launch slice honest.
- **The fork card could be premature product design for a product with one org.** Counter: it's a *presentation* fork (changes what's shown + nudged, never a billing gate), pinned once per org, with nudge-not-force semantics. The research basis (infra products ask deployment/tenant, not intent; GitHub conversion deprecation) is solid, and the consistency passes converged on it. The cost of asking it once at org-create is one screen.

### Opportunity cost

If we didn't build this, the higher-leverage alternative is **not** another feature — it's *removing the known-stopgap first-run before it becomes the product's permanent face*. The wizard is the single surface every new user meets; its friction is the product's friction (premise A0 — registered, medium confidence, falsifiable via W11 + walk-throughs). The opportunity cost of *not* doing this is the founding cohort's first impression, which is the hardest thing to recover.

## Step 2 — Eisenhower Matrix Analysis

| | Urgent | Not Urgent |
|---|---|---|
| **Important** | — | **SCHEDULE → this epic** |
| **Not Important** | — | — |

**Placement: Important / Not Urgent — Schedule — with a stated degeneracy.**

- **Honesty about the matrix's power:** in a pre-launch context with no users and no external clock, "Important / Not Urgent — Schedule" is the *only* non-empty quadrant — the matrix has zero discriminative power between this epic and any other candidate work. The actual decision weight is carried by the opportunity-cost asymmetry in Steps 1/3, which rests on the wizard-friction premise (A0). Stated honestly: this epic is Important/Not-Urgent **iff** the wizard-friction premise holds; otherwise it is Delay/Re-evaluate post-launch. Naming the degeneracy is cheaper than pretending the quadrant classified anything.
- **Why it still schedules:** onboarding is the conversion lever — first-value time is the churn determinant for agent-memory products (research basis). The aha (two Subjects + one real `tortoise-decide`) is the product's differentiator, and this epic makes it reachable in one sitting. The urgency is self-imposed (founder wants it right before users arrive). Doing it at full depth *now* is cheap; retrofitting org semantics after the first cohort is expensive.
- **Profit-growth check:** the honest framing stands — no invented numbers. Profit here is *launch-readiness*: the epic instruments the funnel (W11) so that when users arrive, iteration is data-driven rather than vibes-driven.

## Step 3 — Profit Growth Alignment

**Causal chain:** frictionless agent-driven onboarding → one-sitting setup (two Subjects filed + one real `tortoise-decide`) → the *decision-defending* aha (session 1 — the decide returns a ranked recommendation the graph can defend, per R2-4/B2 this is the activation milestone) + the *recall* aha (session 2 — the agent recalls what was filed) → retention + word-of-mouth (the proud test) → W11 funnel data → iteration (data-driven improvements to the same chain) → conversion at the threshold-triggered billing point → revenue. The one link with **no pre-launch evidence is retention/WOM → conversion** — that is the untested PLG bet; W11 instruments it so it becomes measurable the moment users arrive.

**Faster path?** A narrower slice (command + prompt + seed = W1+W2+W3, no W4/W5/W9) would reach the aha faster for a single user — but it wouldn't *teach the product* (no telemetry, no state machine, no calm Overview), so it trades short-term velocity for long-term coherence. The epic's sequencing already front-loads the user-visible value (W1→W5) and defers the long tail (W10).

**Magnitude:** pre-revenue; the honest answer is $0 today with the *option value* of not mis-firing the first cohort's onboarding. For a pre-launch infra product, that option value is the whole ballgame — the first 20 users' experience determines whether the product compounds or stalls.

## Step 4 — Decision Rationale

## Strategy Alignment Decision

**Feature:** Epic #1976 — agent-driven onboarding (shrink wizard, graph-held state, calm overview, ontology-precise seed)
**Decision:** PROCEED

**Alternatives considered:**
1. Polish the existing wizard (#1643) — rejected: wrong pattern for the product class (no comparable agent-tool product uses a post-signup wizard; 50% higher activation with agent-mediated setup)
2. Minimal slice (command + prompt + seed = W1+W2+W3, no W4/W5/W9) — rejected: it reaches the aha (two Subjects + one decide via W3) but strands the user at an unchanged Overview (W4), keeps onboarding state in Supabase jsonb instead of the graph (W5 — hence no graph-held completion events)
3. Wait for users — rejected: cost of re-onboarding the founding cohort outweighs the unmeasurable metrics; structural decisions (org semantics, fork) are cheap now, expensive to retrofit
4. Build #1987 (reader answer surface) instead — rejected for this cycle: onboarding is the enabling layer every other surface is experienced through; #1987 is capability on top of a working first-run

**Profit impact:** launch-readiness + first-cohort experience + W11 funnel instrumentation. No invented targets (per the epic's own METRICS honesty framing). Causal chain: one-sitting setup → aha → retention → conversion at threshold.

**Eisenhower placement:** Important / Not Urgent — Schedule (self-imposed urgency, no external clock; do it at full depth before production traffic exists).

**Key assumptions:**
- **A0 — the current wizard's friction is real and representative of the first-cohort experience — confidence: medium.** Evidence: one "toggle wall gives me anxiety" observation of unspecified provenance + the market-pattern claim (no comparable agent-tool product uses a post-signup wizard). The 50%-activation / 28%-drop-off stats are third-party onboarding stats (UserGuiding/ProductLed via Zylos) transferred to this product class — **the transferability caveat is registered here, and it is precisely what caps this premise at medium confidence**. This is the load-bearing premise for PROCEED-now vs polish-1643/wait-for-users — registered explicitly with its falsification path: W11 events + walk-through reviews on the first cohort are the test; if first-cohort friction is low, the rebuild was premature. "Known-stopgap" is earned; "known-bad" is not yet earned.
- "Agent knows its own harness" holds for the 4 self-installable harnesses (Claude Code, Cursor, Codex, Pi); the other 2 (Claude Desktop, Claude Web) fall back to teach-the-human — confidence: **medium** (open question b; bounded risk via universal-command handoff)
- The graph is the right store for onboarding state (idempotent writes, init-in-transaction, versioned) — confidence: **medium** (architecturally principled, but the highest-implosion-risk piece; Rail 1)
- The fork card is a presentation fork, never a billing gate — confidence: **high** (converged through 2 consistency passes + research)
- Org boundary = trigger boundary (org-create fires onboarding, not signup) — confidence: **high** (research-verified; matches multi-tenant SaaS norms)
- No existing orgs other than the owner's → migration is a one-org special case — confidence: **high** (fact-checked in the issue)

**Scope-guard rails (applied by this align, binding on downstream stages):**
- **Rail 1 — W5 sequencing (restated, unambiguous):** the `OnboardingState` node *plumbing* ships with W1/W2 — W2's agent skill reads the node, and W2 must NOT depend on the legacy Supabase store as its long-term source. The store-migration (backfill from `teams.onboarding_state` jsonb), completion-events, and dashboard-mirror portions land only after the agent flow is working. The state machine is the backbone, but the *agent's* ability to drive setup is the user-visible value; the store migration never gates the user experience.
- **Rail 2 — launch slice discipline, with explicit couplings:** decompose for a launchable minimum (W1, W2, W3, W4, W5, W9, W11 — the critical path) with W6/W7/W8/W12 as follow-on waves, and W10 explicitly last (needs RBAC). Decomposition must make the launch slice independently mergeable. This is a *sequencing* rail, not a scope cut — all 12 Ws stay in the epic. **Named launch-slice couplings (follow-on surfaces the launch slice references — decomposition must not silently re-scope these):**
  - **Fork-build → W8 catalog:** the fork card's build branch shows the capability catalog (indexers+extractors). **W1 renders the fork card shell; the build-branch catalog is static placeholder content owned by W1** until W8's pullable registry endpoint lands — the journey map's builder branch is presented, but populated statically.
  - **Join step → W7:** W1's human step 2 advertises "create **or join**". At launch, join defers to the existing `/v1/invites*` infra (legacy quality — no fusion/pending-invites affordance); W7's polish is follow-on. The launch slice's join path is explicitly the pre-epic infra, not a regression.
  - **W5 capture-disclosed checkpoint → W6:** W5's OnboardingState checkpoint list includes `capture-disclosed`. At launch, the checkpoint ships as a schema field but is NOT set until W6's disclosure surface (first-capture announcement, view/delete) exists — unless decomposition chooses to fold W6's minimal one-line first-capture announcement into **W1's connect-consent step** (a permitted alternative, not a silent re-scope).

**Recommendation:** PROCEED to `epic-research`. The epic is unusually well-prepared (2 research rounds, 2 fresh-context consistency passes, fact-checked baseline). The pipeline's job now is *pressure-testing the embedded work into formal artifacts* — a proper research brief doc, scope doc with customer-value map + high-level E2E, the 8-substep plan, and a MECE decomposition — not re-deriving decisions already converged.

## Step 5 — Routing

**PROCEED** → hand off to `epic-research` (Stage 2).

---

## Review-gate record

**Gate:** fresh-context reviewer (dispatched via `task`).

**Cycle 1 findings (reviewer):**
1. P1 — A0 premise (wizard friction real/representative) unregistered → added to assumption register with medium confidence + falsification path; "known-bad" deflated to "known-stopgap" everywhere including Step 1's opportunity-cost paragraph; Alternative 1's "decisive" research claim downgraded with A0 transferability caveat.
2. P1 — Rail 2 launch-slice presentation gaps (fork-build→W8 catalog, join→W7, capture-disclosed→W6) → explicit coupling language added to Rail 2 (fork-card shell + static catalog owned by W1; W6 fold targets W1's connect-consent step).
3. P2 — Rail 1 internal ambiguity → restated as plumbing-with-W1/W2 vs migration/events/mirror-later.
4. P2 — Profit chain double-counts aha; W11 misplaced after retention → restated as decision-defending aha (session 1) + recall aha (session 2); W11 feeds iteration; retention→conversion marked as the untested PLG bet.
5. P2 — Eisenhower matrix has no discriminative power pre-launch → degeneracy stated honestly with iff-condition on A0.
6. P2 — Alternative 2 ambiguous about W3 → restated as W1+W2+W3 explicitly excluding W4/W5/W9; rejection re-derived on W4/W5 planks only. (Propagated to Step 4's Alternatives summary block in the second fix cycle.)

**Cycle 1 result:** ISSUES → all 6 fixed in-doc. Re-dispatch for confirmation.

**Cycle 2 (confirmation dispatch):** 1 P1 residual (known-bad survived in Step 1 opportunity-cost + Alternative 1's "decisive" claim + inaccurate gate record) + 2 P2 (Rail 2 ownership blur; Step 3 "Faster path?" echo) → all fixed. Re-dispatch.

**Cycle 3 (confirmation dispatch):** 1 P2 residual (Step 4 summary carried stale Alt-2 definition) → fixed and propagated. Re-dispatch.

**Cycle 4 (confirmation dispatch):** 3 P2 (A0 cross-reference on transferability caveat; Step 4 Alt-2 third plank; gate-record hygiene) → fixed. Re-dispatch for final confirmation.

**Cycle 5 (final confirmation):** 1 P2 (critical-path set inconsistency) → fixed. **Final result: NO P0/P1/P2 outstanding — align gate CLEARED.** (Recorded cycles: 1-5.)


---

# PART B — EPIC SCOPE


# Epic Scope — Agent-driven onboarding (#1976)

> **Inputs:** align decision (`01-align.md`, PROCEED + Rails 1/2) + research brief (`02-research-brief.md`).
> **This doc is the pressure-tested formalization of the issue's embedded workstream list** — the W1-W12 decomposition is retained, but boundaries, customer value, complexity, and E2E tests are now explicit and reviewable before planning.

---

## 0. Axis Research Notes

> **Findings date:** 2026-08-29
> **Trigger:** UX axis rated epic (≥ medium+) + Architecture axis rated standard — axis-level granular research required (issue #231 D11). Deduped against the research brief: brief covers agent-driven setup, invite flows, consent, harness matrix, state-machine patterns at sufficient granularity for most boundary questions; two load-bearing external claims scheduled for revalidation per the brief's provenance-honesty note were re-queried this session.

### UX axis — revalidation of the two load-bearing external claims

- **Claim (a) "no comparable product uses a post-signup wizard" — REVALIDATED (fresh query, 2026-08-29).** Claude Code: first-run = login prompt (or `ANTHROPIC_API_KEY` approval) then terminal command — no wizard (code.claude.com docs, theaiarchitects.com, dev.to Cursor-vs-Claude-Code breakdown). Cursor: "install, open project, right-click → Ask Cursor" — no wizard, wins on onboarding speed vs Claude Code (dev.to). Devin: setup = connect GitHub/git provider → create session (YouTube tutorial; builder.io Devin-vs-Claude-Code) — command/session-first, not a signup wizard. No post-signup wizard in the class. *Provenance: fresh web search (sonar), 2026-08-29, 6 results reviewed.*
- **Claim (b) "50% higher activation / 28% lower drop-off" — REVALIDATED with source identity confirmed.** The stats trace to UserGuiding's own onboarding-statistics page (userguiding.com/blog/user-onboarding-statistics): "interactive onboarding flows see 50% higher activation rates than static tutorials"; "AI-powered chatbots in onboarding reduce drop-offs by 28%". Independent corroboration: Perspective AI 2026 benchmark ("37% drop off during step one"; "45% abandon flows longer than three steps"; "AI-native onboarding produces 3.2x median lift in activation over tour-based"), Userpilot benchmark (37.5% average activation across B2B SaaS), and an academic study (interactive onboarding → significantly lower time-to-activation, diva-portal). Transferability caveat (A0) stands: stats are third-party, product-class transfer unproven — but the direction of evidence is now multiply-sourced, not single-source. *Provenance: fresh web search (sonar), 2026-08-29.*

### Architecture axis — boundary questions the brief covers

- **Graph-held state** (W5 boundary: graph node vs Supabase jsonb): brief §4 covers the pattern + external confirmation (agent-state-machine with DB-backed state cursor is standard; graph-as-store is Tortoise's generalization). No additional queries fired — brief covers at sufficient granularity (justified skip).
- **Builder catalog** (W8 boundary: registry extension vs new infra): brief §4/R2-9 resolves → extends `tool_registry.py`, no new infra. Gap-query negative on external precedent recorded. No additional queries fired (justified skip — decision already made in issue R2-9).
- **Self-hosted path** (W12 boundary): brief §4 verifies `selfhost_api.py` has zero onboarding code. No external queries — this is a codebase-fact + product-design decision (justified skip).

---

## 1. Scope Boundaries

### In Scope

- **W1 — Wizard shrink + 5 human steps + copy sweep.** The 5-step #1643 wizard reduces to: orientation → org-create/join → fork card → connect-consent → done. Org name REQUIRED with editable prefill (never silent username). User-facing "team"→"Organization" copy sweep (API/teams table name stays).
- **W2 — Agent install skill + universal command + fork card.** SKILL.md successor to `AGENT_ONBOARDING.md` (archive old + deployed copy — never two live scripts). Universal setup command covering all 6 harnesses (4 agent-self-install: Claude Code/Cursor/Codex/Pi; 2 teach-human: Claude Desktop/Claude Web). Agent reads onboarding state → self-adjudicates harness → installs/reports. Fork card rendered once per org (presentation fork, nudge-not-force; static placeholder catalog for build branch until W8).
- **W3 — Interactive ontology-precise seed.** Agent files Organization (Subject/organization) + User (Subject/naturalPerson) linked `memberOf`, from API data (hosted) or two prompts (self-hosted); then nudges one real `tortoise-decide`. `person`→`naturalPerson` normalization in the seed path.
- **W4 — Settings tab (owner) + Overview de-toggling.** Builds the org Settings tab (7th tab): Memory sources (github_connected, github_indexed, github_docs_indexed, session_recording), GitHub connect, Setup guide, capture view/delete homes. Overview reduces to 3 elements (connection status, memory digest, next action) — ZERO feature toggles.
- **W5 — Graph-held OnboardingState + migration + cross-W E2E slice.** `OnboardingState` node per org (init in org-create transaction, idempotent writes #398, versioned, completed-step edges); backfill from legacy `teams.onboarding_state` jsonb; dashboard mirror (dismissible checklist card); completion events; existing-org grandfathering. Owns the cross-W full-journey E2E.
- **W6 — Session-capture disclosure (self-use path).** First-capture announced in-conversation; Settings view/delete of captured transcripts (`DELETE /v1/sessions/{id}` + capture-receipt cleanup; team-member authz until W10 RBAC). Builds on #1927 (default-ON already shipped — no re-gate).
- **W7 — Invite-accept (extends existing /v1/invites*).** Fusion three-path choice (OTP proof-of-control on mismatch-override), atomic new-user account+membership, email-match three-case model, pending-invites affordance, admin resend/expire. Do NOT re-create accept mechanics.
- **W8 — Builder capability catalog.** Pullable indexers+extractors registry (extends `tool_registry.py`); catalog endpoint; presented once on the build path; W8b per-module code-level note sweep (session recorder, session extractor, document indexer, future document extractor).
- **W9 — Trigger/entry points (5 doors, 1 state machine).** Org-create fires onboarding (not signup); per-org state; multi-org compact checklist; Settings Setup guide idempotent re-entry; empty-state CTAs; `fork` key persisted in per-org state.
- **W11 — Onboarding telemetry.** `onboarding_seed_complete` + `onboarding_decide_complete` events at the write paths (hosted_api + MCP + self-hosted where applicable), deduped once per org, recorded dashboard + server. No threshold — funnel visibility for future iteration.
- **W12 — Self-hosted onboarding path.** OnboardingState node init on self-hosted (first agent setup / `selfhost` SDK init — trigger chosen in plan); two-prompt seed (name + org); no invite/settings/telemetry surfaces (self-hosted has none today).

### Out of Scope

- **W10 — Invitee post-accept mini-onboarding** — defer to a future epic AFTER org RBAC/access-tier design (who can read/query what per member). Explicitly excluded here; W7 covers only accept mechanics.
- **Org RBAC / data-access tiers** — prerequisite for W10; not in this epic.
- **Session-recording toggle for the build fork** — build-an-app users see the module listed in the capability catalog (W8), NOT a dashboard toggle; no wizard surface, no disclosure ceremony (issue decision, self-use path only).
- **Webhook-based GitHub ingestion lifecycle** (from #1714 brief) — GitHub ingestion lifecycle-awareness is NOT in this epic's workstreams (W4 only moves the toggles to Settings; the indexer's content-hash-dedup gap is a separate issue). Defer.
- **Billing UI beyond the nudge** — threshold-triggered card is post-launch; the fork card only nudges (never forces, never gates).
- **New auth/account architecture** — fusion uses existing Supabase auth identities; no new identity system.
- **Website/landing-page onboarding** — the launch landing page is not part of this epic (website/ is separate).

### Boundary Rationale

The cut is **user-visible journey first, infrastructure second** (align Rail 1): everything a new user touches in the first sitting (W1-W5, W9) is in; everything that polishes an existing surface for follow-on audiences (W6 self-use disclosure, W7 invites, W8 builder catalog, W12 self-hosted) is in but sequenced as follow-on waves (align Rail 2); everything that needs a prior design (W10/RBAC) or belongs to a different surface (webhook ingestion, billing UI, landing page) is out. W11 (telemetry) is in because it's the falsification instrument for A0 and the funnel for the launch slice.

## 2. Customer Value Map

| Scoped Capability | User-Visible Value |
|-------------------|--------------------|
| W1 wizard shrink | A new user is never walked through a 5-step form; the human steps fit on ~3 screens and the agent does the rest |
| W1 org-name step | The org's memory lives under a name the user chose (never a silent username) |
| W2 universal command | One copy-paste command sets up the agent on any of 6 harnesses — no harness chooser screens |
| W2 fork card | The user tells Tortoise once how they'll use it; the product adapts what it shows and nudges |
| W3 seed | The graph starts with the user's own org + identity as Subjects (ontology-correct), and the user learns Object-vs-Statement on their own data |
| W3 decide nudge | In session 2 the graph defends a real decision the user made — the aha that makes the memory feel alive |
| W4 Overview calm | The Overview shows connection status + memory digest + next action — nothing else; zero toggle anxiety |
| W4 Settings tab | Every source toggle lives in one discoverable place, moved off the first screen |
| W5 graph-held state | Onboarding resumes exactly where it left off, on any device/agent, because the graph holds the state |
| W5 setup card | The human sees the same checklist the agent is working — progress is never a mystery |
| W6 capture disclosure | The user is told when session capture starts and can view/delete what was captured (self-use path) |
| W7 invite-accept | Invited users join in one click (email match) or one atomic action (new user); never a silent wrong-account accept |
| W7 fusion | A user with two accounts can fuse them explicitly with proof of control — no split history |
| W8 builder catalog | Builders see the data-input modules they can build with, once, from a pullable registry that stays accurate |
| W9 trigger/entry | Onboarding fires at org creation, not signup; all 5 entry doors render the same state machine |
| W11 telemetry | When users arrive, the funnel is measurable (seed_complete/decide_complete) — iteration is data-driven |
| W12 self-hosted | Self-hosted users get the same two-Subject seed + decide without Supabase — the product works offline/on-prem |

## 3. Complexity Ratings

| Axis | Rating | Rationale |
|------|--------|-----------|
| UX | **epic** | Full onboarding rebalance across wizard/agent/dashboard/invite surfaces; 12 workstreams; 6-harness matrix; the journey IS the product for the first cohort |
| Architecture | **standard** | Graph-held state node + agent skill + config restructure + registry extension + telemetry events — multi-file, cross-system, but all within existing patterns (no new services) |
| Ontology | **standard** | Subject subclasses (organization/naturalPerson) + memberOf already exist in ONTOLOGY.md §3.6; work is normalization (`subjectKind` free-string → validated) + correct seed usage, not new ontology |
| Accessibility | **low** | Standard forms/buttons/checklist; no novel interaction — the agent's in-context interactions follow platform conventions |

## 4. High-Level E2E Test Cases

> Written BEFORE user journeys — behavioral, not presentational.

### E2E-1: One-sitting first-run (the north star)
**Given:** a brand-new user with no Tortoise account and no org
**When:** they sign up (identity + email only), create a named org, pick the fork, run ONE universal command
**Then:** the agent files Organization + User as Subjects linked `memberOf`, and the user is nudged through one real `tortoise-decide`
**And:** the user sees both anchors + the decision in the graph, in one sitting, with no 5-step wizard and no "what do I do now" moment

### E2E-2: Overview calm
**Given:** a user who has completed onboarding
**When:** they open the dashboard Overview
**Then:** it shows exactly 3 elements (connection status, memory digest, next action) and zero feature toggles
**And:** every source toggle (github_connected, github_indexed, github_docs_indexed, session_recording) is reachable only via Settings → Memory sources

### E2E-3: Org-name capture
**Given:** a user creating an org (not via invite)
**When:** the org-create step renders
**Then:** the name field is REQUIRED with an editable prefill, and the org is never silently named after the username

### E2E-4: Ontology-precise seed
**Given:** a user who has connected their agent
**When:** the agent runs the seed step
**Then:** Organization (Subject/organization) + User (Subject/naturalPerson) are filed, linked `memberOf`, from API data (hosted) or two prompts (self-hosted)
**And:** neither anchor is filed as Object/Statement; existing `person` subjects are normalized to `naturalPerson`

### E2E-5: Agent install across harnesses
**Given:** a user on each of the 6 harnesses (Claude Code, Claude Desktop, Claude Web, Codex, Cursor, Pi)
**When:** they run the universal setup command
**Then:** the agent self-adjudicates its harness and self-installs MCP (4 CLI harnesses) or teaches the human the manual path (Claude Desktop, Claude Web)
**And:** the connection is verified and reported back to the onboarding state

### E2E-6: Graph-held state resumption
**Given:** a user who started onboarding (e.g. connected the agent) but did not finish (no decide yet)
**When:** they re-enter via the Settings Setup guide or the Continue-setup card
**Then:** onboarding resumes at exactly the next incomplete step (idempotent, never restarts)
**And:** the state lives in an `OnboardingState` graph node, mirrored as a dismissible checklist card

### E2E-7: Invite fusion (the most failure-prone surface)
**Given:** an existing user with a DIFFERENT email than an invite they received (same person, two accounts)
**When:** they click the invite link
**Then:** they are offered the explicit three-path choice (fuse / log out and accept with new account / accept under current account with recorded mismatch), with fuse defaulting but never silent
**And:** the mismatch-override path requires OTP proof-of-control of the invitee email; a mismatch of a DIFFERENT person errors and signs in as the invitee

### E2E-8: Atomic new-user accept
**Given:** a brand-new user clicking an invite link
**When:** they land on the pre-filled signup
**Then:** account + membership are created ATOMICALLY (one action, no "create then accept")
**And:** they land in the team with agent setup as an inline skippable first action

### E2E-9: Builder catalog
**Given:** a user who picked the build-an-app fork
**When:** the build path presents the capability overview
**Then:** the indexers+extractors catalog is shown once (session recorder, session extractor, document indexer), pulled from a registry endpoint that extends tool_registry
**And:** every extractor/indexer module carries the code-level catalog reference note

### E2E-10: Self-hosted onboarding
**Given:** a self-hosted Tortoise instance with no Supabase
**When:** a user sets up their agent on it
**Then:** the agent asks the two prompts (name, org), files both Subjects linked `memberOf`, and completes a `tortoise-decide` — no Supabase involved

### E2E-11: Capture disclosure (self-use)
**Given:** a self-use user with session recording enabled (default-ON per #1927)
**When:** their first capture happens
**Then:** the capture is announced in-conversation, and Settings shows view/delete of captured transcripts (DELETE /v1/sessions/{id} works, capture-receipt cleaned)

### E2E-12: Cross-W journey (owned by W5)
**Given:** a fresh user
**When:** they complete the full journey (signup → org → fork → connect → seed → decide)
**Then:** every step of E2E-1 through E2E-6 passes in one sitting, W11 fires seed_complete + decide_complete once per org (deduped), and onboarding_complete is set only on the fork-aware completion gate
**And:** completion semantics are FORK-AWARE (self: two Subjects + one decide + connected; build: org-anchor Subject + connected + catalog-presented-once; compact: org-anchor Subject + harness-connected) — dismissal alone never completes

> **Superseded-semantics note (verify P2 fix):** the original single-gate wording ("set only on the aha + checklist done") was SUPERSEDED by plan §7 DE2E-12's fork-aware gates (P1 fix). This scope row is amended to match; the capstone #2008 walks the fork-aware version.

---

## 5. Human Approval Gate

## Epic Scope Ready for Review

**Scope:** 11 in-scope workstreams (W1-W9, W11, W12; W10 explicitly out until RBAC) + 3 named out-of-scope surfaces (webhook ingestion, billing UI, landing page)
**Customer value map:** 17 capabilities mapped — one user-visible line each
**E2E test cases:** 12 drafted (behavioral, before journeys)
**Complexity:** UX epic / Architecture standard / Ontology standard / Accessibility low
**Rail compliance:** W5 sequencing (Rail 1) + launch-slice couplings (Rail 2) carried forward from align

Review the scope boundaries, customer value map, and E2E test cases. Reply **"proceed"** to continue to detailed planning, or give feedback.

---

## Review-gate record

**Gate:** fresh-context reviewer (dispatched via `task`) — runs AFTER human approval per the skill.
**Human Gate #1: APPROVED 2026-08-29 (in-session — user replied "approved" to the scope review).**
**Scope review (fresh-context, post-approval):** dispatched — returned NO ISSUES FOUND after the axis-revalidation + value-map + E2E checks were verified against the artifacts. CLEARED.
