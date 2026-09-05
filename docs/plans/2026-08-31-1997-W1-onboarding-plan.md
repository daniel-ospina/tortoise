<!-- research-path: docs/epics/2026-08-29-agent-driven-onboarding-1976/02-research-brief.md -->

# W1 Wizard Shrink Implementation Plan

> **Issue:** #1997 (W1 of epic #1976, agent-driven onboarding) · **Branch:** feat/1997-W1-onboarding
> **Complexity:** standard (UX standard / Architecture low) — no plan reviewers dispatched per issue-scoping (standard tier).

**Goal:** Shrink the 5-step #1643 wizard to EXACTLY 5 human steps (orientation → org-create/join → fork → connect-consent → done), archive (not delete) the legacy wizard render, sweep user-facing team→Organization copy on wizard + org-create + connected surfaces, and activate the W5 T7 accept-and-drop pin (removing wizardComplete = the cross-PR ordering pin).

**Team:** epistemic-team

## Design decisions

### D1 — New wizard render (main.jsx welcomeMode block)
Replace the legacy `.wizard` render block (steps 0-4: harness chooser / memory sources / skills / seed / done) with the 5 human steps, driven by a new pure module `wizardFlow.js` (step ids + labels + subs + fork options + org-name validation). State stays on the existing vars (`wizardStep`, `wizardHarness`, `welcomeMode`, …) — no rename churn for W4.

| Step | Content | Writes |
|---|---|---|
| 0 Orientation | "What you're setting up" list (moved from the `welcomeOriented` pre-card; the pre-card gate is dropped — orientation IS a wizard step now) | none |
| 1 Create/Join Organization | Org-create form: name REQUIRED with editable prefill (email-prefix / current org name), client validation mirroring the server (`wizardFlow.orgNameError`); submit → `POST /v1/onboarding/team` (the Q5 wizard lane — one-shot `team_created`, no free-tier dead-end); 200 → loadTeams + switchTeam; 409 (already created) → advance; 402 → upgrade surface (never silent; W9 owns enforcement). Join leg: pending invites inline (existing `acceptPendingInvite` flow — legacy `/v1/invites*` at launch, W7 polish follow-on) | org-create fires onboarding (W5 eager node init at sub-team create — verified by test) |
| 2 Fork card | `onboarding.fork` set → show current choice (build → placeholder catalog + mark); unset → 2 options (self-use / build). Pick → `POST /v1/onboarding/state/checkpoint {fork}`; 200 advance; 409 set-once conflict → advance with inline note; 503 → inline error, stay. Build branch renders a static placeholder catalog (W8 owns the real one); the placeholder RENDER marks the catalog-presented step edge via checkpoint `{step:'catalog-presented'}` (surface 4 write contract — FWW, replay no-op). Fork SEMANTICS owned by W2 — W1 renders only | checkpoint fork (set-once); checkpoint catalog-presented on build placeholder render |
| 3 Connect your agent | Universal command — 6 harness tabs (harnesses.js `HARNESS_ORDER`/`HARNESS_NAMES` preserved), `HARNESS_INTRO`/`HARNESS_STEPS`/`HARNESS_INSTALL` snippet, Copy (existing `wizardCopy` analytics beacon), "I've set it up — Continue" / "Skip for now" | none (W2 owns harness-connected) |
| 4 Done | "Your agent takes over" copy; Overview + Setup guide card handoff; [Open my dashboard →] = setWizardDone + exit welcome + `finishWelcomeLoads` | NONE — `wizardComplete` (PATCH onboarding_complete) is REMOVED (archived) |

### D2 — Archive (not delete) the legacy wizard
The legacy 5-step render JSX (harness chooser / memory sources / skills / seed / done) moves into an explicit **ARCHIVED section inside main.jsx** — a never-invoked nested function `LegacyWizardArchived()` under a `⛔ ARCHIVED — #1997 (W1)` header comment noting the A0-gate rollback path (epic §8: partial revert restores the #1643 wizard). Closure-heavy JSX stays in scope (zero refactor risk); the legacy labels array (`wizardSteps`) + `wizardSeedGraph`/`wizardComplete` helpers remain referenced by the archived block, so shared state stays stable. The DE2E-1 archived-not-deleted assertion = a JS source-text test greps the marker + legacy labels.

### D3 — Server: accept-and-drop (W5 T7 pin, plan T7)
`_ACCEPT_AND_DROP` flips True; the PATCH handler drops a client `onboarding_complete` write when the org's node is present (accepted 200, echo = node-governed wire — the legacy jsonb flag is inert there). Node-absent (grandfathered pre-backfill) orgs keep the jsonb writer (their fallback). Implemented in the PATCH handler only — internal writers (`_update_onboarding_state` direct calls, e.g. test_mcp_http gating) unchanged. W5 carve-out tests that pinned the pre-W1 PATCH-completes behavior are updated to the new contract.

### D4 — Copy sweep (user-facing only)
team→Organization on: wizard (all new copy via wizardFlow.js), org-create dialog (create-team modal + its error strings), welcome provisioning/key-reveal/claim-error, Overview re-entry + graph-missing cards, suspended-banner fallback, invite-accepted banner, and harnesses.js connect copy ("your team switches it off" ×2). NOT swept (outside the DE2E-2 Overview/Settings surface): account-blob "Switch team", Members tab, Billing, API-keys errors.

### D5 — Fork placeholder catalog-presented mechanism
The build-branch placeholder catalog render fires `POST /v1/onboarding/state/checkpoint {step:'catalog-presented'}` (fire-and-forget, .catch → noop). FWW keyed-MERGE → replay no-op; W8 later replaces the placeholder SOURCE, not the mechanism (MECE fix). Launch-slice build-fork gate (org-anchor + connected + catalog-once) becomes evaluable.

## Tasks

### Task 1: wizardFlow.js (new pure module) + unit tests
**Intent:** Single source of truth for the 5 human steps, fork options, org-name validation, and legacy labels — unit-testable without React (repo pattern: setupGuide.js).
**Acceptance:** `wizardFlow.js` exports WIZARD_STEPS (5, exact order), WIZARD_FORK_OPTIONS (self/build), LEGACY_LABELS (5 old labels), orgNameError (required + charset mirror of server); `wizardFlow.test.js` green: 5 steps exact, copy-sweep (no team/workspace in any step/fork copy), name validation, legacy labels archived.

### Task 2: main.jsx — new 5-step wizard render + archive legacy block
**Intent:** Render exactly the 5 human steps; zero legacy #1643 form screens.
**Acceptance:** welcomeMode wizard renders WIZARD_STEPS; legacy render JSX moved into `LegacyWizardArchived()` (never invoked) under the ARCHIVED header; wizardComplete no longer called by the live wizard; Setup-header + re-entry-card re-open behavior preserved.

### Task 3: main.jsx — org-create + join + fork + connect + done handlers
**Intent:** Wire the 5 steps' actions: org-create submit (`POST /v1/onboarding/team`, 402/409 handling), fork checkpoint writes, build placeholder catalog + catalog-presented mark, connect copy (reuses harnesses.js + wizardCopy), done exit (no completion write).
**Acceptance:** org-create name required with editable prefill; 402 upgrade surface; fork set-once conflict handled; catalog-presented checkpoint fired on build placeholder render; done exits without PATCHing onboarding_complete.

### Task 4: Copy sweep (main.jsx + harnesses.js)
**Intent:** DE2E-2/issue scope: wizard + org-create + connected surfaces say Organization.
**Acceptance:** swept strings above; `wizardArchived.test.js` asserts Organization copy on the org-create dialog + wizard copy sweep (no team/workspace in new wizard copy).

### Task 5: Server accept-and-drop + test updates
**Intent:** Activate W5 T7's cross-PR pin (removes wizardComplete → the legacy jsonb completion writer is inert on node-present orgs).
**Acceptance:** `_ACCEPT_AND_DROP = True`; PATCH onboarding_complete dropped on node-present orgs (200, wire stays node-driven); node-absent fallback preserved; updated tests: test_onboarding_state_split.py (carve-out echo → accept-and-drop contract; poisoned-false guard + grandfathered-first-write seed jsonb via raw writer; NEW TestAcceptAndDrop), test_onboarding_integration.py (complete via node gate), tests/e2e/hosted/test_14 (grandfathered → accept-and-drop; NEW org-create endpoint node-init + checkpoint catalog-presented build gate).

### Task 6: Test extension — surfaces 3/4/16
**Intent:** Issue verification checklist coverage.
**Acceptance:** wizardFlow.test.js (5 steps, copy sweep, name required) — surface 16/3; wizardArchived.test.js (archived-not-deleted marker + legacy labels present, Organization copy) — surface 16/DE2E-1; test_onboarding_state_split.py TestAcceptAndDrop + test_onboarding_integration.py (accept-and-drop, node gate) — surface 1/2/16 server leg; hosted test_14 (org-create endpoint node init = "onboarding fires here"; checkpoint catalog-presented build gate) — surfaces 3/4.

### Task 7: Verify
**Intent:** CI gates green.
**Acceptance:** `uv run ruff check .` clean (ruff 0.16 — no RUF059 etc.); docker lane `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/ -q` clean (test_markers.py guards pass — no new registry/select_graph literals); `node --test src/*.test.js` in website/apps/dashboard clean; dashboard dist rebuilt + committed (merges carry dist).

## Key constraints honored
- Legacy wizard ARCHIVED-not-deleted (A0 rollback path) — never deleted.
- No W2 fork semantics, W8 catalog endpoint, W9 entitlement enforcement, W7 invite fusion.
- Internal state names stable (wizardStep/wizardHarness/welcomeMode/…); copy sweep = user-facing labels only.
- Join leg defers to legacy `/v1/invites*` at launch.

## Status (2026-08-31)
All 7 tasks implemented + verified locally:
- wizardFlow.js + wizardFlow.test.js (13 cases: 5 steps, copy sweep, org-name validation, fork options, build placeholder, legacy labels archived).
- main.jsx: 5 human steps render (orientation → org-create/join → fork → connect → done); legacy #1643 wizard gated behind `LEGACY_WIZARD_ARCHIVED` (byte-identical JSX retained, A0 rollback) + `wizardArchived.test.js` source-scan assertions (DE2E-1 archived-not-deleted, DE2E-2 Organization copy).
- Org-create: name REQUIRED + editable prefill (orgNameError mirror), POST /v1/onboarding/team, 409 advance, 402 upgrade surface; join leg = pending invites inline.
- Fork card: set-once checkpoint; build → BUILD_CATALOG_PLACEHOLDER render marks catalog-presented (render-time effect, re-entry covered).
- Server: `_ACCEPT_AND_DROP = True` — PATCH onboarding_complete dropped on node-present orgs (node-governed echo), node-absent keeps jsonb writer. Tests updated + TestAcceptAndDrop node-absent branch added.
- wizardComplete: PATCH write removed (done step hands off to the graph).
- Copy sweep: wizard (wizardFlow.js), org-create dialog, welcome provisioning, re-entry + first-data cards, claim strings, suspended fallback, harnesses.js connect copy.
- Hosted E2E test_14 updated (accept-and-drop contract); dashboard e2e test_dashboard_onboarding.py rebaselined to the 5 human steps.

**Verify:** 202 py tests pass (docker lane), 5 hosted E2E pass, 104 JS tests pass (13 new), ruff clean, vite build clean, dist rebuilt.
