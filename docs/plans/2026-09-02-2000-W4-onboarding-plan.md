<!-- research-path: docs/epics/2026-08-29-agent-driven-onboarding-1976/02-research-brief.md -->

# W4 Settings Tab + Overview Calm Implementation Plan

> **Issue:** #2000 (W4 of epic #1976, agent-driven onboarding) · **Branch:** feat/2000-W4-onboarding
> **Complexity:** standard (UX standard / Architecture low) — no plan reviewers dispatched per issue-scoping (standard tier; the epic already ran the double-diamond — scope anchor is the issue body + 03-scope.md/06-plan.md §P2/P3).

**Goal:** Build the org **Settings tab** (owner of Memory sources, GitHub connect, Setup guide, capture view/delete homes) and calm the **Overview** to exactly 3 elements (connection status, memory digest, next action) with ZERO feature toggles. Every source toggle (github_connected, github_indexed, github_docs_indexed, session_recording) becomes reachable only via Settings → Memory sources. User-facing copy on Overview/Settings says "Organization" (never team/workspace). Single-owner discipline (R2-10): W6/W9 consume this tab later — W4 builds clean homes with named seams, no speculative W6/W9 behavior.

**Team:** epistemic-team

## Design decisions

### D1 — Overview = exactly 3 elements, zero toggles (DE2E-2)
Replace the populated-Overview grid (SetupGuideCard + 6 stat cards) and remove the `MemorySources` panel. The Overview `.cards` grid renders EXACTLY three cards, derived from a new pure module `overview.js`:

| # | Element | Source | States |
|---|---|---|---|
| 1 | Connection status | merged onboarding FLOW state (`harness-connected` ∈ completed_steps; every fork's completion gate requires harness-connected, so `status==='complete'`/`onboarding_complete` ⇒ connected too) | loading (skeleton) · connected ✓ · not connected · unavailable (graph-down markers → honest, never a false "connected") |
| 2 | Memory digest | `team.point_count` (the honest in-graph memory count; no fabricated object/statement split — no server surface exists for it) | loading · N points · "No memories yet" handled by the existing empty-state branches (digest grid only renders point_count > 0) |
| 3 | Next action | `setupGuide(state)` (setupGuide.js) current step | loading · degraded (honest unavailable, no false checklist) · complete/collapsed ("You're all set") · active → current-step label + single CTA **"Open Setup guide →"** (`setTab('settings')`) |

- The SetupGuideCard (full checklist card) and `MemorySources` (all toggles) LEAVE the Overview → Settings (D3).
- Stat-card info relocates to existing tabs (it already lives there): Data points/Graphs/Users/Write-ops → Billing tab usage cards; Graphs list → Graphs tab; Members list → Members tab; API-keys count → the keys table itself; Plan → Billing + header tier badge. **Backups** was Overview-only → a compact "Backups" summary line moves into the API Keys tab (below the keys table) so the count stays reachable (`backupInfo.count`).
- team===null loading/frame-stale branch: skeleton grid shrinks from 6 cards to the same 3 element labels (connection/digest/next action) so LOADING renders the same calm shape.
- Empty/re-entry/graph-missing branches are unchanged (already calm, single-CTA, no toggles).

### D2 — New pure module `overview.js` (+ `overview.test.js`)
Pure derivations (repo pattern: setupGuide.js/captureStatus.js, node --test, zero deps):
- `OVERVIEW_ELEMENTS = ['connection-status', 'memory-digest', 'next-action']` — the DE2E-2 exactly-3 contract.
- `overviewConnection(state)` → `{kind:'loading'|'connected'|'disconnected'|'unavailable'}` (+ label/value/detail copy, Organization wording, no fabrication on graph-down).
- `overviewDigest(points)` → `{kind, value, detail}`.
- `overviewNextAction(g)` where g = `setupGuide(state)` → `{kind:'loading'|'degraded'|'done'|'active', label, step}`.
Unit tests pin the DE2E-2 contract (exactly 3 ids; no `switch`/toggle vocabulary in the module; unavailable never reads "connected"; copy sweep: no team/workspace, Organization present).

### D3 — Settings tab (7th tab; R2-11) with the four homes (P3)
- Nav: new `<button data-tab="settings">Settings</button>` after Billing (tab order: Overview | API Keys | Graphs | Members | Billing | Settings; Profile stays menu-accessed — Settings is the new 7th tab in the canonical list incl. Profile). Also hooks the setup-header "Setup" affordance region semantics: unchanged (opens wizard).
- `{tab === 'settings' && team && (...)}` section renders FOUR labeled homes:
  1. **Setup guide** — full `SetupGuideCard` (same graph-held state — DE2E-6) + a "Resume setup →" affordance when the flow is mid-flight (`setupGuide()` status active & not degraded/collapsed). Resume opens the wizard (same idempotent-safe re-entry the Overview re-entry card uses: wizard step 0 → org-create 409-advance → fork replay (set-once 'same' 200) → connect). **W9 owns the fork-aware step-mapped resume; W4 names the seam** (code comment).
  2. **GitHub connect** — status + Connect button (reuses `wizardConnectGithub` + `wizardGithub` busy state). Connected → "Connected" + repos available (wizardGithub.repos or the loaded reposList length) + note that scope/re-index live under Memory sources. Not connected → explanation + primary Connect CTA. This is the "GitHub connect" home (the connect flow that lived in the wizard step surface).
  3. **Memory sources** — the `MemorySources` component (issues/docs/sessions toggles + inline connect CTAs), instantiated with the SAME props the Overview used (wizardHarness=null). This is the ONLY live home of the four memory-source toggles → DE2E-2 reachability.
  4. **Captured sessions** — the capture view/delete HOME (DE2E-11 seam). Renders session_recording state (default ON) + the loaded `/v1/sessions` list (id/created/turns/extracted). **W6 consumes this home** (adds transcript view + `DELETE /v1/sessions/{id}`); W4 builds the honest skeleton — no dead buttons (no DELETE endpoint exists yet), no fabricated list.
- Owner-scope guard (R2-10): no W6 capture-announcement, no W9 entitlement/re-entry state machine here — the four homes exist, seams commented `#2000 (W4)` / `W6 consumer` / `W9 consumer`.

### D4 — `MemorySources` relocation mechanics
The component body is untouched (it already reads everything from props/state — zero coupling to the Overview). Its two instantiation sites become: Settings (live) + the ARCHIVED legacy wizard block (dead, A0 rollback — byte-identical, gated by `LEGACY_WIZARD_ARCHIVED`). The Overview instantiation is deleted. `wizardHarness={null}` stays for Settings (the Settings surface never knows the user's harness — no spurious "current" highlight).

### D5 — Copy sweep (DE2E-2, Overview + Settings surfaces; mirrors W1 D4 scope discipline)
Sweep user-facing copy on the Overview/Settings surfaces:
- Overview alerts banner "…detected on this team. Revoke any key…" → "…detected on this Organization…".
- Overview graph-missing branch "Your team is live — create an API key…" → "Your Organization is live…".
- Wizard done-step sub (wizardFlow.js) "Follow the Setup guide card on the Overview…" → "…follow the Setup guide in **Settings**…" (the card moved).
- All NEW Overview/Settings copy written in wizardFlow.js style (Organization, no team/workspace) — enforced by tests.
NOT swept (outside DE2E-2's Overview/Settings surface — W1 D4 precedent): account-blob "Switch team", Members tab header, Billing, API-keys error copy.

### D6 — Server: none. Python tests = toggle-persistence integration (surface 10)
No hosted_api/sdk.py changes (W4 = UI + existing state endpoints; tier-2 PR). New docker-lane integration test module `tests/test_onboarding_w4_settings.py` mirrors test_onboarding_state_split.py (module-level skip when `TORTOISE_DB_URI` unset):
- The four operational keys (github_connected/github_indexed/github_docs_indexed/session_recording) PATCH round-trip through `/v1/onboarding/state` and persist across a re-read (multi-round writes, readback via GET).
- They are jsonb-side keys: NEVER in FLOW_KEYS / never in the graph node (registration-split pin), i.e. they can never be written by the checkpoint surface — reachability is state-surface-only.
- session_recording default-ON (#1927) preserved; a false write flips it off and back.

## Tasks

### Task 1: `overview.js` (new pure module) + `overview.test.js`
**Intent:** Single source of truth for the Overview's exactly-3-element derivation (DE2E-2), unit-testable without React.
**Acceptance:** exports OVERVIEW_ELEMENTS (exactly 3 ids in order) + overviewConnection/overviewDigest/overviewNextAction; tests green: 3-element contract, connected semantics (harness-connected OR complete), unavailable never connected, digest honest (N points / empty), next action from setupGuide (active step / done / degraded), copy sweep (Organization, no team/workspace).

### Task 2: main.jsx — Overview calm render
**Intent:** DE2E-2: populated Overview = exactly 3 cards, zero toggles, no MemorySources/SetupGuideCard on Overview.
**Acceptance:** populated branch renders 3 `.card`s (Connection status / Memory digest / Next action) from overview.js; MemorySources + SetupGuideCard instantiations removed from the Overview; team-null skeleton branch uses the 3 labels; alerts + empty branches unchanged; no `role="switch"` inside `.overview`.

### Task 3: main.jsx — Settings tab (nav + four homes)
**Intent:** P3/R2-11: Settings = 7th tab owning Memory sources + GitHub connect + Setup guide + capture view/delete homes.
**Acceptance:** Settings nav button (data-tab="settings") after Billing; `tab==='settings'` section renders the four homes; SetupGuideCard shows same graph-held state + Resume (active only); GitHub connect surface works from Settings (wizardConnectGithub); MemorySources renders with the exact Overview prop set (wizardHarness null); Captured-sessions home lists sessions + recording state (W6 seam comments).

### Task 4: Backups relocation + copy sweep
**Intent:** Stat-card info stays reachable (Backups was Overview-only) + DE2E-2 copy.
**Acceptance:** compact Backups summary on the API Keys tab (`backupInfo.count` — same expression the Overview card used); alerts + graph-missing Overview copy says Organization; wizardFlow.js done-step sub points to Settings.

### Task 5: Python tests — toggle persistence (surface 10)
**Intent:** Issue target "toggle persistence integration tests" on the docker lane (tier-2: module-level skip when TORTOISE_DB_URI unset).
**Acceptance:** tests/test_onboarding_w4_settings.py green on the docker lane; skips cleanly URI-less; ruff clean.

### Task 6: JS tests — source-scan + derivations
**Intent:** DE2E-2/DE2E-6 assertions runnable in CI (node --test, no React runtime).
**Acceptance:**
- `overview.test.js` (Task 1).
- New `settingsArchived.test.js`-style source-scan on main.jsx: (a) `<MemorySources` appears ONLY in the settings-tab section + the ARCHIVED legacy block (2 sites; Overview site gone); (b) a `tab === 'settings'` section exists with the four homes (markers); (c) the Overview populated branch renders no MemorySources/SetupGuideCard; (d) copy sweep markers on new surfaces.
- wizardFlow.test.js: update for the done-step copy (Settings reference) — copy sweep still green.

### Task 7: Verify
**Intent:** CI gates green.
**Acceptance:** `node --test src/*.test.js` in website/apps/dashboard clean; `uv run ruff check` clean; docker lane `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_onboarding_w4_settings.py tests/test_onboarding_state_split.py -q` clean; URI-less (`TORTOISE_TEST_CARVE_OUT=1`) run of the new module skips cleanly; dashboard dist rebuilt + committed (merges carry dist — W1 precedent).

## Key constraints honored
- Single-owner R2-10: Settings built by W4 only; W6/W9 consume later (no parallel-merge-conflict surface).
- Store split untouched: operational keys stay jsonb-side; FLOW keys stay graph-side; no re-architecture.
- No fabricated Overview content (graph-down → unavailable, never a false checklist/connected).
- Legacy wizard ARCHIVED-not-deleted; MemorySources stays referenced by the archived block (rollback path).
- Tier-2 PR: no shared python modules touched (sdk.py/ep.py/exceptions.py/tool_registry.py/mcp_server.py/projection/conftest.py all untouched).

## Status

Implemented + verified locally (see PR body):
- overview.js + overview.test.js (17 cases) green; overviewSettings.test.js (5 source-scan cases) green — full dashboard suite 127/127.
- main.jsx: Overview calm (3 elements, zero toggles), Settings 7th tab + four homes, Backups relocated to API Keys (own loading floor), copy sweep + review-round P2 fixes (connect-error visibility, SetupGuideCard/next-action/capture honest error states, default-ON copy truth); wizardFlow.js done-step copy points at Settings; dist rebuilt + committed.
- tests/test_onboarding_w4_settings.py (5 cases) green on the docker lane; URI-less skip verified; ruff clean.
- NOTE (pre-existing, out of scope): tests/e2e/test_dashboard_onboarding.py is stale at base a39eff70 — asserts wizard copy ("What you're setting up") the merged W1 wizard never shipped, then fails at org-create prefill (space-in-name validation). Both tests fail identically at base (verified on a detached a39eff70 worktree); untouched by this PR.
