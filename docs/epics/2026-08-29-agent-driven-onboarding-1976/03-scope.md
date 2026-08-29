---
title: "Epic Scope — #1976: Agent-driven onboarding (shrink wizard, graph-held state, calm overview, ontology-precise seed)"
type: decisions
domain: capability
doc_status: draft
subjects.team: epistemic-team
created: 2026-08-29
aboutSubjects: tortoise
aboutObjects: tortoise
---

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
**Then:** every step of E2E-1 through E2E-6 passes in one sitting, W11 fires seed_complete + decide_complete once per org (deduped), and onboarding_complete is set only on the aha + checklist done

---

## 5. Human Approval Gate

## Epic Scope Ready for Review

**Scope:** 12 in-scope workstreams (W1-W9, W11, W12; W10 explicitly out until RBAC) + 3 named out-of-scope surfaces (webhook ingestion, billing UI, landing page)
**Customer value map:** 17 capabilities mapped — one user-visible line each
**E2E test cases:** 12 drafted (behavioral, before journeys)
**Complexity:** UX epic / Architecture standard / Ontology standard / Accessibility low
**Rail compliance:** W5 sequencing (Rail 1) + launch-slice couplings (Rail 2) carried forward from align

Review the scope boundaries, customer value map, and E2E test cases. Reply **"proceed"** to continue to detailed planning, or give feedback.

---

## Review-gate record

**Gate:** fresh-context reviewer (dispatched via `task`) — runs AFTER human approval per the skill.
**Status:** pending human approval (Gate #1).
