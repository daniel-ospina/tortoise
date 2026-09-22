---
title: "UX Design Decisions Record — Epic #1976: Agent-driven onboarding"
type: decisions
domain: ux
doc_status: draft
subjects.team: epistemic-team
created: 2026-08-29
aboutSubjects: tortoise
aboutObjects: tortoise
---

# UX Design Decisions Record — Epic #1976

> Produced by the UX Design Review gate (epic-workflow, between Scope and Plan; UX_RATING = epic ≥ medium). This gate **classifies and records** — it does not design. Most decisions below were already made during the epic's creation (issue body, 2 fresh-context consistency passes, product-owner confirmation) — they are recorded with citations, not re-litigated. Residual open items are surfaced at the end for optional user choice; the gate does not block.

## Step 1 — Scan: user-facing surfaces touched

| # | Proposed change | User-facing surface |
|---|---|---|
| 1 | Wizard shrink to 5 human steps (orientation, org-create/join, fork, connect-consent, done) | Dashboard wizard |
| 2 | Org name REQUIRED with editable prefill | Org-create form |
| 3 | Fork card (self-use vs build-an-app, once per org) | Dashboard post-org-create |
| 4 | Universal setup command + agent self-adjudication | Agent install surface (all 6 harnesses) |
| 5 | Overview = 3 elements (connection status, memory digest, next action), zero toggles | Dashboard Overview |
| 6 | Settings tab (7th tab): Memory sources + GitHub connect + Setup guide + capture view/delete | New Settings tab |
| 7 | Setup guide / Continue-setup card (idempotent re-entry) | Settings + Overview |
| 8 | Invite-accept: 3-path fusion choice, atomic new-user accept, pending-invites affordance | Invite landing + team switcher |
| 9 | Capture disclosure: first-capture announcement + view/delete | In-conversation + Settings |
| 10 | Builder capability catalog presented once on build path | Build path (fork branch) |
| 11 | "team"→"Organization" copy sweep | All user-facing copy |
| 12 | Seed: agent files two Subjects + nudges one real decide | Agent in-context interactions |

## Step 2 — Classification against the 6 UX decision types

| # | UX Decision Type | Applies? | Where decided |
|---|---|---|---|
| 1 | Metrics & data display | ✅ (Overview 3 elements) | Epic body "Overview — CALM" + R2/M4 (zero toggles; sources → Settings) |
| 2 | Layout & hierarchy | ✅ (wizard steps, Settings tab, Setup guide) | Epic body "The human steps, defined" (W1) + R2-10 (Settings single-owner) + R2-11 (7th tab named) |
| 3 | Copy & messaging | ✅ (org name, fork card, fusion choice copy, Organization sweep) | Epic body: org-name required (W1), fork nudge-not-force copy, fusion consequence-visible copy (M10), Organization terminology (decided) |
| 4 | Subscription gating | ✅ (fork = presentation, never billing gate) | Epic body "Fork-dependent behavior" + R2-7 (fork stored, nudge-not-force) |
| 5 | Visual affordances | ✅ (fork card, Setup guide card, pending-invites affordance) | Epic body (dismissible card, GitHub-style pending affordance) |
| 6 | Responsive behavior | ❌ (no novel mobile-specific surfaces; dashboard is desktop-first) | — |

All 6 classes touched except responsive — the epic's embedded decisions cover them. No P0-consequence decisions (no data loss / security / irreversible) — the gate proceeds without escalation (human-input-framework).

## Step 3/4 — Decision record (decided, with citations)

| # | Decision Type | Decision (recorded) | Source |
|---|---|---|---|
| 1 | Metrics display | Overview = connection status + memory digest + next action; ZERO feature toggles; stat cards relocate to existing tabs | Issue "Overview — CALM"; R2 M4 |
| 2 | Layout: wizard | 5 human steps: orientation → org-create/join → fork → connect-consent → done; agent does everything else | Issue "The human steps, defined"; product-owner confirmed orientation opening |
| 2 | Layout: Settings | 7th tab named "Settings" (R2-11); owns Memory sources + GitHub connect + Setup guide + capture view/delete; W6/W9 consume (single-owner) | R2-10, R2-11 |
| 2 | Layout: Setup guide | Persistent Settings item, idempotent (resume never restart); completed orgs collapse to compact status or dismissible-hide with re-open path | Issue "Onboarding trigger + entry points" |
| 3 | Copy: org name | REQUIRED with editable prefill; never silent username; 100% of non-invited orgs | Issue W1 acceptance |
| 3 | Copy: fork | "Use it for your own agents" vs "build an application on top"; presentation fork; nudge-not-force | Issue fork section; R2-7 |
| 3 | Copy: fusion | 3-path choice with fuse default, consequence-visible copy (permissions per-account), never silent | Issue "Account fusion" (M10) |
| 3 | Copy: terminology | "Organization" everywhere user-facing (never team/workspace); API/teams table name stays | Issue "Terminology — Organization (decided)" |
| 4 | Gating | Fork is NEVER a billing gate; invites may require paid plan but surfaced at invite-time, not fork consequence | Issue "Fork-dependent behavior" |
| 5 | Affordance: fork | Once per org, after org-create, before agent flow arms | Issue "Fork card (once, per org...)" |
| 5 | Affordance: setup card | Dashboard renders the SAME state as a dismissible checklist card; disappears when complete | Issue step 8 + W9 |
| 5 | Affordance: pending invites | Subtle affordance in team switcher (GitHub-style); admin resend/expire (Slack-style) | Issue invite section |
| 6 | Responsive | Not applicable — no novel mobile surfaces (dashboard desktop-first) | — |

## Residual open items (surface for choice; gate does NOT block)

These are the genuinely open UX questions the research could not resolve (issue "Open questions") — directionally decided in the epic, but with an option to revisit now:

1. **Open (a) — team-name-required vs auto-derive.** Epic decision: REQUIRED with editable prefill (user preference, Slack/Notion precedent). *Research is thin on external evidence; the epic says usability-test it.*
   - **Option 1 (recorded):** Required with prefill — ship and usability-test in W1 walk-through.
   - **Option 2:** Auto-derive from email with editable override (less friction, weakens the "explicit step" indicator #3).
   - **Recommendation:** Option 1 (already recorded; the epic's indicator #3 makes it a feature requirement, true-by-construction).

2. **Open (b) — agent-knows-its-own-harness vs chooser-as-primary (Claude Web).** Epic decision: agent self-adjudicates; Claude Web = teach-human fallback; harness chooser archived.
   - **Option 1 (recorded):** Self-adjudication with teach-human fallback; test with real harnesses in W2.
   - **Option 2:** Keep chooser as primary for manual harnesses (more UI, less agent-driven — against the north star).
   - **Recommendation:** Option 1 (north-star aligned; bounded risk).

3. **Setup-guide end state when complete:** collapse-to-status vs dismissible-hide.
   - **Option 1 (recorded):** Collapse to compact setup-status (keeps re-open path).
   - **Option 2:** Dismissible-hide with re-open path in Settings.
   - **Recommendation:** Option 1 (Stripe/Linear pattern; completed orgs keep a minimal status signal).

**Disposition:** all three are directionally decided and recorded. If no feedback, planning proceeds with the recorded decisions; the walk-through/friction tests at each W are the enforcement. No P0 — gate does NOT block.

## Step 5 — Hand back

UX Design Decisions recorded. Hand back to epic-workflow → Stage 4 (epic-plan). The recorded decisions inform user journeys (sub-step 1) and the prototype (sub-step 3) but do not gate them.
