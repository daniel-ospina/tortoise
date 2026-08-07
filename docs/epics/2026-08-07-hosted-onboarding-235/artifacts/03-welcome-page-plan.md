<!-- research-path: docs/epics/2026-08-07-hosted-onboarding-235/01-research-brief.md -->

# Welcome Page v2 — Scope + Implementation Plan

> **Issue:** #497 (child of epic #235 — Hosted Onboarding Journey)
> **Complexity:** STANDARD (UX)
> **Dependencies:** #495 (DONE — artifact format), #496 (DONE — question set), #498 (DONE — API plan), #502 (FUTURE — deploys prompt URL)
> **Plan author:** issue-scoping v5.1 double diamond | **Date:** 2026-08-08
> **Inputs:** `01-artifact-format.md` (#495), `AGENT_ONBOARDING.md` (#495), `02-question-set.md` (#496), `04-api-plan.md` (#498), existing `premise-labs/welcome.html`

---

## Confirmed Problem Definition

The current welcome page (`premise-labs/welcome.html`) shows an API key, a stdio MCP config, and a pip-install quickstart. This was built for the "install Python package" era. The hosted onboarding flow (#235) replaces that model entirely:

1. **Users no longer install pip packages** — they use the hosted MCP server at `api.premiselabs.co` over Streamable HTTP transport.
2. **Users need a guided onboarding flow** — not just "here's your key, go figure it out." The two-block artifact design (#495) delivers MCP config (per harness) + onboarding prompt as a unified UX.
3. **The page is the bridge** between "I signed up" and "I pasted the artifact and my agent is asking me onboarding questions."

**Root cause:** The current welcome page was built for stdio transport + local install. The hosted onboarding model requires Streamable HTTP configs, harness-specific tabs, and a copy-prompt button that fetches the canonical onboarding prompt — none of which exist today.

**Problem statement:** "I just signed up for Tortoise. Now what?" → The welcome page must answer that in two copy actions, across four harnesses, in under 30 seconds.

---

## Page States

The welcome page has five states — three visible to the user, two transitional:

### State 1: Pre-signup (direct navigation guard)

```
┌─────────────────────────────────────────┐
│ Tortoise.                               │
│                                         │
│ No active session                       │
│ [Sign up to get started →]              │
└─────────────────────────────────────────┘
```

**Reached when:** User navigates to `welcome.html` without a Supabase session.
**Behavior:** Show a message with a link to `/signup.html`. Do NOT start polling.
**Why exists:** Guard against direct URL navigation. Welcome page is only meaningful post-signup.

### State 2: Key-pending (polling)

```
┌─────────────────────────────────────────┐
│ Tortoise.                               │
│                                         │
│ 🌀 Setting up your Tortoise memory…     │
│ [spinner]                               │
│ This should take less than 30 seconds.  │
└─────────────────────────────────────────┘
```

**Reached when:** User arrives after signup (has Supabase session) but `user_teams.api_key` is `null` or `"pending"`.
**Behavior:** Poll `user_teams` every 1s for up to 30 attempts (30s max). Show spinner with status text. If timeout, show "Still setting up… refresh" with a manual refresh button.
**Transition:** → State 3 when `api_key` returns (not null, not "pending").

### State 3: Key-ready (the artifact view)

```
┌─────────────────────────────────────────┐
│ Tortoise.                    [Dashboard]│
│                                         │
│ ✅ Your Tortoise is ready!              │
│ Team: Acme Inc · Tier: Free             │
│                                         │
│ ╔═══════════════════════════════════════╗
│ ║ 🚀 Set up your agent                 ║
│ ║                                      ║
│ ║ [Claude Code] [Codex] [Cursor] [Pi] ║ ← harness tabs
│ ║                                      ║
│ ║ Step 1: Add Tortoise to your agent   ║
│ ║ ┌──────────────────────────────────┐ ║
│ ║ │ [harness-specific MCP config]    │ ║
│ ║ │ …with tt_YOUR_KEY interpolated… │ ║
│ ║ └──────────────────────────────────┘ ║
│ ║ [📋 Copy config]                     ║
│ ║                                      ║
│ ║ Step 2: Start onboarding             ║
│ ║ ┌──────────────────────────────────┐ ║
│ ║ │ [AGENT_ONBOARDING.md content]    │ ║
│ ║ │ …fetched from prompt URL…       │ ║
│ ║ └──────────────────────────────────┘ ║
│ ║ [📋 Copy prompt]                     ║
│ ║                                      ║
│ ║ Step 3: Paste both into your agent   ║
│ ║ Step 4: Answer ≤6 questions (<5 min) ║
│ ╚═══════════════════════════════════════╝
│                                         │
│ Your API key: tt_••••••••••••  [📋]    │
│ ⚠️ Save this key — it won't be shown   │
│     again here.                        │
└─────────────────────────────────────────┘
```

**Reached when:** `api_key` is provisioned and displayed.
**Behavior:** Show the two-block artifact. Harness tabs default to "Claude Code" (or detected from user-agent). Copy-config inserts the user's real API key into the config snippet. Copy-prompt fetches markdown from the prompt URL (deployed by #502) and copies it to clipboard.

**Key design decisions:**
- The API key is shown at the bottom as secondary info (the artifact is the primary call-to-action)
- The "Save this key" warning persists from the current welcome page
- Harness tabs are a row of pill-shaped buttons — selected tab gets accent background
- Both copy buttons have the same "Copied!" feedback pattern as the existing page

### State 4: Paste-artifact (post-copy confirmation)

```
┌─────────────────────────────────────────┐
│ ✅ Config copied!                       │
│ ✅ Prompt copied!                       │
│                                         │
│ Paste both into your agent now:         │
│ • Config → terminal/settings            │
│ • Prompt → agent chat                   │
│                                         │
│ [harness-specific instructions]         │
└─────────────────────────────────────────┘
```

**Reached when:** User has clicked both "Copy" buttons (tracked via local state, not persisted).
**Behavior:** Show per-harness paste instructions from the artifact format doc (#495). This is a progressive disclosure — only shown after both copies.
**Transition:** Non-blocking. User can ignore this and just go to their agent.

### State 5: Post-onboarding (completion — Phase 2)

```
┌─────────────────────────────────────────┐
│ 🎉 Tortoise is up and running!          │
│                                         │
│ Memory: 5 points · 2 decisions ·        │
│ 3 evidence items                        │
│                                         │
│ [Open Dashboard →]                      │
│ [Create a new Point →]                  │
└─────────────────────────────────────────┘
```

**Reached when:** `GET /v1/onboarding/state` returns `completed_at` not null (Phase 2 — after #498 endpoints ship).
**Behavior:** Show memory digest from the backend. This state is a Phase 2 enhancement — v1 ships with states 1-4 only. The welcome page polls for completion state but gracefully handles the endpoint not existing yet.

---

## The Two-Block Artifact Display

### Block A: MCP Config (per harness)

Each harness tab shows **Streamable HTTP transport config** (not stdio). The user's API key is interpolated into the snippet. Configs are taken verbatim from the artifact format doc (#495):

| Harness | Format | Copy target |
|---------|--------|-------------|
| Claude Code | CLI command: `claude mcp add --transport http tortoise …` | Terminal |
| Codex | CLI command + env var export | Terminal |
| Cursor | JSON for `.cursor/mcp.json` | File |
| Pi | JSON for `.pi/mcp.json` | File |

**Tab behavior:**
- Default selection: detect from user-agent (Claude Code / Codex / Cursor not detectable — default to Claude Code; Pi IS detectable via UA)
- Click to switch tabs — transitions config snippet instantly
- "Copy config" button copies the currently selected harness config with the user's real API key

**Why Streamable HTTP, not stdio:** Hosted users do not install the Python package. The agent talks to `api.premiselabs.co` over HTTP. The existing welcome page shows stdio config (command + args) — this is wrong for the hosted flow and must be replaced entirely.

### Block B: Onboarding Prompt

A single copy target — the canonical `AGENT_ONBOARDING.md` content. Same prompt works across all harnesses.

**Fetch mechanism:**
- The prompt lives at a stable URL deployed by #502 (e.g., `https://tortoise.premiselabs.co/onboarding-prompt.md` or `https://api.premiselabs.co/v1/onboarding-prompt`)
- On page load, fetch the prompt and render it in the Block B snippet area
- The "Copy prompt" button copies the fetched markdown to clipboard
- **TODO(#502):** Until #502 deploys the prompt URL, use a hardcoded fallback (the current `AGENT_ONBOARDING.md` content embedded as a JS string literal) with a comment `// TODO(#502): replace with fetch from deployed URL`

**Why fetch, not embed:** The prompt is the single source of truth (#495). Embedding it in HTML creates a divergent copy that drifts when #496 updates question wording. Fetching from the canonical URL means the welcome page is always in sync.

---

## Copy-Config + Copy-Prompt Buttons

### Copy-Config Button

- **Label:** "📋 Copy config" (changes to "✅ Copied!" for 2s)
- **Behavior:** Copies the currently selected harness config snippet with `tt_YOUR_KEY` replaced by the user's actual API key
- **Implementation:** Same `navigator.clipboard.writeText()` + fallback pattern as existing page

### Copy-Prompt Button

- **Label:** "📋 Copy prompt" (changes to "✅ Copied!" for 2s)
- **Behavior:** Copies the full AGENT_ONBOARDING.md content to clipboard
- **Dependency:** The prompt content comes from the URL deployed by #502
- **Fallback:** Until #502 ships, the prompt is hardcoded as a JS string (synced manually with `tortoise/onboarding/AGENT_ONBOARDING.md`). A code comment marks the spot for the #502 migration.

### Paste-Instructions (State 4)

After both copies are done, show per-harness instructions:
- **Claude Code:** Terminal command → then paste prompt in chat
- **Codex:** Terminal command + env var → then paste prompt in chat
- **Cursor:** Edit `.cursor/mcp.json` → paste prompt in chat (or save to `.cursor/rules/`)
- **Pi:** Edit `.pi/mcp.json` → paste prompt in chat

---

## Key Polling (Supabase user_teams.api_key)

The existing polling logic works and does not need major changes:

```javascript
// Existing pattern (keep, refine timeout)
for (let attempt = 0; attempt < POLL_MAX_ATTEMPTS; attempt++) {
  const { data } = await supabase
    .from("user_teams")
    .select("team_id, team_name, api_key, graph_name")
    .eq("user_id", userId)
    .single();
  if (data?.api_key && data.api_key !== "pending") {
    showArtifact(data);  // was showSuccess
    return;
  }
  await sleep(1000);
}
```

**Changes from existing:**
1. Increase `POLL_MAX_ATTEMPTS` from 30 → 45 (45s max, accounts for cold-start provisioning via #498's `/v1/register`)
2. Add a "Taking longer than expected" message at attempt 30 (instead of waiting until timeout)
3. Add a manual "Check again" button that resets the poll counter
4. Extract `team_name`, `graph_name` for display (already selected, just add to UI)

**What stays the same:**
- Supabase client init with anon key
- `getSession()` → `user_id` flow
- `user_teams` table query
- Local/remote URL detection

---

## Mobile Responsiveness

The existing welcome page is desktop-first (max-width: 560px container). v2 must work on mobile where users may sign up and configure their agent on the same device.

### Breakpoints

| Breakpoint | Behavior |
|-----------|----------|
| ≥ 640px | Two-column layout for artifact blocks (config left, prompt right) — optional enhancement |
| < 640px | Single column, full-width cards. Harness tabs scroll horizontally. Copy buttons full-width. |
| < 380px | Reduce padding, smaller font sizes (14px → 13px body). Harness tabs stack 2×2. |

### Mobile-specific considerations

1. **Harness tabs:** `overflow-x: auto` with `-webkit-overflow-scrolling: touch`. No horizontal page scroll.
2. **Copy buttons:** Full-width on mobile for easy tap targets (min 44px height).
3. **Snippet blocks:** `max-height: 200px` with `overflow-y: auto` on mobile to prevent long configs from pushing the prompt block off-screen.
4. **API key display:** `word-break: break-all` and `user-select: all` (already in existing — keep).
5. **Tested on:** iPhone SE (375px), iPhone 14 (390px), Pixel 5 (393px), iPad Mini (768px).

---

## Deploy: Wrangler Pages (premise-labs project)

The welcome page lives in `premise-labs/welcome.html` and is deployed via Cloudflare Wrangler Pages as part of the `premise-labs` project.

**Deploy command:**
```bash
npx wrangler pages deploy premise-labs --project-name=premise-labs
```

**Routing:** `welcome.html` is served at `tortoise.premiselabs.co/welcome.html`. The signup flow redirects to this URL after email confirmation.

**What ships:**
- `premise-labs/welcome.html` — the complete welcome page (all states, both blocks, harness tabs, copy buttons)
- No backend changes (this is a pure frontend issue)

**Post-deploy verification:**
1. Visit `https://tortoise.premiselabs.co/welcome.html` without session → State 1 (redirect to signup)
2. Sign up → redirected to welcome page → State 2 (polling spinner) → State 3 (artifact)
3. Click "Copy config" → verify clipboard contains correct harness config with real API key
4. Click "Copy prompt" → verify clipboard contains AGENT_ONBOARDING.md content
5. Switch harness tabs → verify config snippet updates
6. Test on mobile viewport → verify layout adapts

---

## Test Approach: Playwright E2E

### Test File

`tests/e2e/test_welcome_page.py` (or added to existing E2E suite if one exists)

### Test Scenarios

| Test | Description | Tags |
|------|-------------|------|
| `test_welcome_no_session_shows_signup_link` | Navigate without Supabase session → see "Sign up to get started" | @smoke, @e2e |
| `test_welcome_polling_spinner` | Navigate with session but no key yet → see spinner, transitions to artifact when key arrives | @e2e |
| `test_welcome_polling_timeout` | Mock 45s no-key → see timeout message + manual refresh button | @e2e |
| `test_welcome_shows_api_key` | Key provisioned → API key displayed, team name shown | @smoke, @e2e |
| `test_copy_config_claude` | Default tab (Claude Code) → click "Copy config" → clipboard has `claude mcp add` with real key | @smoke, @e2e |
| `test_copy_config_cursor` | Switch to Cursor tab → click "Copy config" → clipboard has JSON with real key | @e2e |
| `test_copy_prompt` | Click "Copy prompt" → clipboard contains AGENT_ONBOARDING.md content | @smoke, @e2e |
| `test_harness_tab_switch` | Click each harness tab → config snippet updates accordingly | @e2e |
| `test_mobile_responsive` | Set viewport to 375×812 → verify layout is single-column, tabs scrollable | @e2e |
| `test_paste_instructions_appear` | Click both copy buttons → paste instructions appear (State 4) | @e2e |
| `test_copy_feedback` | Click copy button → button shows "Copied!" → reverts after 2s | @e2e |

### Test Fixtures

- **Supabase session:** Mocked `getSession()` returning a valid user ID. `user_teams` query mocked to return `{api_key: "tt_test_key_123", team_name: "Test Team"}`.
- **Prompt fetch:** Mocked `fetch()` for the prompt URL returning `AGENT_ONBOARDING.md` content.
- **No backend required:** All tests mock Supabase and fetch calls. Zero FalkorDB dependency.

### Running Tests

```bash
# Install Playwright (one-time)
npx playwright install chromium

# Run E2E tests
python -m pytest tests/e2e/test_welcome_page.py -v -m e2e

# Run smoke tests only
python -m pytest tests/e2e/test_welcome_page.py -v -m smoke
```

---

## Implementation Tasks

### Task 1: Refactor HTML structure — page states

**Intent:** Replace the three-state structure (loading/success/error) with five states (pre-signup/key-pending/key-ready/paste-artifact/post-onboarding). The post-onboarding state is a Phase 2 placeholder.
**Acceptance:**
- Five state containers with `hidden` class toggling
- State machine: `checkSession()` → route to state 1 or 2 → poll → state 3 → (copy both → state 4) → (Phase 2: state 5)
- No visible layout shift during state transitions (same container width)
- Error state shows actionable message, not just "contact support"
**Files:**
- Modify: `premise-labs/welcome.html` (HTML structure + JS state machine)

**Steps:**
1. Define state enum in JS: `States = { PRE_SIGNUP: 0, KEY_PENDING: 1, KEY_READY: 2, PASTE_ARTIFACT: 3, POST_ONBOARDING: 4 }`
2. Create five container divs with meaningful IDs
3. Implement `showState(state)` that hides all, shows one
4. Wire up `checkSession()` → state 0 or state 1
5. Wire up polling → state 2 or state 3 (on timeout: error with retry)
6. Wire up copy tracking → state 3. Both copies done → state 4
7. Add Phase 2 placeholder for state 5 (hidden, commented)

### Task 2: Build harness tab component + Block A (MCP config)

**Intent:** Replace the single stdio MCP config snippet with four harness-specific Streamable HTTP configs behind a tab switcher.
**Acceptance:**
- Four tab buttons: Claude Code, Codex, Cursor, Pi
- Clicking a tab updates the config snippet and highlights the active tab
- Config snippet interpolates the user's API key in the correct position per harness
- "Copy config" button copies the currently selected harness config
- Visual feedback on copy (same pattern as existing)
- Tabs are keyboard-accessible (Tab to focus, Enter/Space to select)
**Files:**
- Modify: `premise-labs/welcome.html`

**Steps:**
1. Add harness config templates as JS constants (four variants from #495 artifact format doc)
2. Build tab button row with `data-harness` attributes
3. Implement `selectHarness(harness)` — updates active tab style + config snippet
4. Implement `renderConfigSnippet(harness, apiKey)` — interpolates `tt_YOUR_KEY` placeholder
5. Wire "Copy config" button to copy the rendered snippet with real key
6. Add CSS for tab active/inactive states, horizontal scroll on mobile
7. Test all four harness configs produce correct output

### Task 3: Build Block B — onboarding prompt with copy button

**Intent:** Display the canonical AGENT_ONBOARDING.md content and provide a copy button. Fetch from the URL deployed by #502, with a hardcoded fallback.
**Acceptance:**
- On page load (State 3), fetch the prompt from the deploy URL
- If fetch succeeds: render prompt in the Block B snippet area, enable "Copy prompt"
- If fetch fails (network error, 404 before #502 deploys): fall back to hardcoded prompt
- "Copy prompt" copies the full markdown to clipboard
- Visual feedback on copy (same pattern)
- Code comment marks the `TODO(#502)` migration point
**Files:**
- Modify: `premise-labs/welcome.html`

**Steps:**
1. Define `PROMPT_URL` constant (final URL TBD by #502 — use placeholder: `"https://tortoise.premiselabs.co/onboarding-prompt.md"`)
2. Embed `FALLBACK_PROMPT` as a JS string literal (current AGENT_ONBOARDING.md content, synced manually with a comment) — TODO(#502): remove after deploy
3. Implement `fetchPrompt()` — try PROMPT_URL, fall back to FALLBACK_PROMPT on error
4. Render fetched prompt in the Block B snippet area (wrap in `<pre><code>`)
5. Wire "Copy prompt" button to copy the rendered prompt text
6. Show loading indicator while fetching (small spinner in the snippet area)
7. Show error state if both fetch AND fallback fail (unlikely — fallback is hardcoded)

### Task 4: Paste-instructions + copy tracking (State 4)

**Intent:** Track when both copies are done, then show per-harness paste instructions.
**Acceptance:**
- Track `configCopied` and `promptCopied` in local state (not persisted — resets on page reload, which is fine)
- When both are true, show State 4 with per-harness instructions
- Instructions change when harness tab changes (post-copy tab switching updates instructions)
- Instructions are not a modal — they appear below the artifact blocks as a natural flow continuation
**Files:**
- Modify: `premise-labs/welcome.html`

**Steps:**
1. Add `copiedState = { config: false, prompt: false }` tracking object
2. In copy-config handler: set `copiedState.config = true`, check if both → show State 4
3. In copy-prompt handler: set `copiedState.prompt = true`, check if both → show State 4
4. Define per-harness paste instructions as JS constants (from #495)
5. Render instructions for the currently selected harness
6. Update instructions when harness tab changes (even after copies done)

### Task 5: Mobile responsive CSS + polish

**Intent:** Ensure the welcome page works on mobile viewports (320px–768px). All interactions remain usable.
**Acceptance:**
- Single-column layout on viewports < 640px
- Harness tabs scroll horizontally without page overflow
- Copy buttons are full-width on mobile (min 44px tap target)
- Snippet blocks are capped at 200px with scroll on mobile
- No horizontal page scroll at any viewport ≥ 320px
- Font sizes adjust for readability on small screens
**Files:**
- Modify: `premise-labs/welcome.html`

**Steps:**
1. Add `@media (max-width: 640px)` breakpoint with single-column overrides
2. Add `@media (max-width: 380px)` breakpoint with reduced padding/font
3. Set harness tab container to `overflow-x: auto; scrollbar-width: none`
4. Set copy buttons to `width: 100%; min-height: 44px` on mobile
5. Set snippet blocks to `max-height: 200px; overflow-y: auto` on mobile
6. Test with device emulation in Playwright (iPhone SE, iPhone 14, Pixel 5)

### Task 6: Playwright E2E tests

**Intent:** Write E2E tests covering the critical user flow: no-session guard, polling, artifact display, copy buttons, harness tabs, mobile responsive.
**Acceptance:**
- 11 test scenarios (see Test Approach section above)
- All smoke tests pass (< 30s combined)
- All E2E tests pass
- Tests use mocked Supabase + fetch (no real backend required)
**Files:**
- Create: `tests/e2e/test_welcome_page.py`

**Steps:**
1. Set up Playwright test fixtures (mock Supabase `getSession`, mock `user_teams` query)
2. Write smoke tests: no-session guard, API key display, copy-config (Claude), copy-prompt
3. Write E2E tests: polling spinner, timeout, all harness tabs, paste instructions, mobile viewport
4. Write copy feedback test (button text changes + reverts)
5. Run tests → red (no page yet) → green (after Tasks 1-5 implement the page)

### Task 7: Deploy via Wrangler Pages

**Intent:** Deploy the updated welcome page to Cloudflare Pages.
**Acceptance:**
- `premise-labs/welcome.html` is live at `tortoise.premiselabs.co/welcome.html`
- Existing pages (index.html, signup.html, etc.) are unaffected
- Playwright smoke tests pass against the deployed URL
**Files:**
- Deploy: `premise-labs/welcome.html` (via wrangler CLI)

**Steps:**
1. Verify welcome.html works locally (`python -m http.server 8080` + browser test)
2. Run Playwright smoke tests against local version
3. Deploy: `npx wrangler pages deploy premise-labs --project-name=premise-labs`
4. Verify deployed URL loads correctly
5. Run Playwright smoke tests against deployed URL

---

## Integration Surface Map

| # | Surface | System A | System B | Type | Test Layer |
|---|---------|----------|----------|------|------------|
| 1 | Supabase `getSession()` | `welcome.html` JS | Supabase Auth | REST (client-side) | E2E mock |
| 2 | Supabase `user_teams` query | `welcome.html` JS | Supabase DB | REST (client-side) | E2E mock |
| 3 | Prompt fetch (#502 URL) | `welcome.html` JS | Cloudflare Pages / API | HTTP GET | E2E mock |
| 4 | `navigator.clipboard.writeText()` | `welcome.html` JS | Browser clipboard API | Browser API | E2E (Playwright `grantPermissions`) |
| 5 | Wrangler Pages deploy | CLI | Cloudflare Pages | Deploy | Manual smoke test |

**Note:** This is a pure frontend issue. Zero backend changes (Supabase is existing, the prompt URL is deployed separately by #502). The integration surfaces are all client-side and testable with mocks.

---

## Acceptance Criteria

1. **No-session guard:** Navigating to `welcome.html` without a Supabase session shows a "Sign up to get started" message with a link to `/signup.html`.
2. **Key polling:** After signup, the page polls `user_teams.api_key` every 1s for up to 45s. Spinner shown during polling. Timeout shows manual refresh button.
3. **API key display:** Key is shown in a readable format (`tt_••••`) with copy button. "Save this key" warning persists.
4. **Harness tabs:** Four tabs (Claude Code, Codex, Cursor, Pi) switch the MCP config snippet. Default is Claude Code. Configs use Streamable HTTP transport, not stdio.
5. **Copy-config button:** Copies the selected harness config with the user's real API key interpolated. Button shows "Copied!" for 2s.
6. **Copy-prompt button:** Copies the canonical AGENT_ONBOARDING.md content. Fetched from the #502 deploy URL; falls back to hardcoded content if fetch fails. Button shows "Copied!" for 2s.
7. **Paste instructions:** After both copies, per-harness instructions appear below the artifact blocks. Instructions update when harness tab changes.
8. **Mobile responsive:** Page is usable on viewports from 320px to 768px. Single-column layout, scrollable tabs, full-width buttons. No horizontal page scroll.
9. **Deployed:** Page is live at `tortoise.premiselabs.co/welcome.html`. Existing pages unaffected.
10. **E2E tests:** 11 Playwright scenarios pass. Smoke tests complete in < 30s.

---

## Dependencies on Other Issues

| Issue | What #497 needs | What #497 does NOT block on | Mitigation |
|-------|----------------|---------------------------|------------|
| **#495** (artifact format) | MCP config snippets per harness, artifact layout design | — | DONE. Configs are copy-pasted from the artifact format doc. |
| **#496** (question set) | Finalized AGENT_ONBOARDING.md wording for the prompt | — | DONE. The prompt content is what it is. |
| **#498** (API plan) | `/v1/register` endpoint existence for key provisioning | — | DONE for planning. The welcome page polls `user_teams` in Supabase, not the API. |
| **#502** (prompt deploy) | Stable URL for the onboarding prompt | Copy-prompt button works with hardcoded fallback | Hardcoded AGENT_ONBOARDING.md as JS string. Code comment `TODO(#502)` marks the migration. When #502 deploys, change one URL constant — no logic changes. |

**#502 coordination contract:**
- #497 ships with `PROMPT_URL = "https://tortoise.premiselabs.co/onboarding-prompt.md"` (or whatever URL #502 chooses)
- #497 ships with `FALLBACK_PROMPT` containing the current `AGENT_ONBOARDING.md` content
- When #502 deploys the prompt to the stable URL, the fetch starts succeeding and the fallback becomes dead code
- A follow-up PR removes the fallback after #502 is confirmed deployed

---

## Rejected Alternatives

| Alternative | Why rejected |
|-------------|-------------|
| Embed prompt as HTML (no fetch) | Divergent copies when #496 updates wording. The prompt must have a single source of truth. |
| Single MCP config for all harnesses (no tabs) | JSON config for Cursor/Pi ≠ CLI command for Claude Code/Codex. One format doesn't work everywhere. |
| Modal for paste instructions | Extra click to dismiss. Inline below artifact is more fluid — user reads it and goes. |
| Persist copy state to localStorage | Over-engineering. The welcome page is a one-time flow. If user reloads, seeing the artifact again is fine. |
| Full Supabase session management for State 1 | The existing signup flow handles auth. welcome.html just checks `getSession()` — if it fails, redirect. No need for login forms. |
| Build a separate onboarding app/page | Welcome page is one HTML file. Extracting it into an SPA adds build complexity with no UX benefit for a single-page flow. |
| Server-side rendering | Welcome page is static HTML + client JS (Supabase polling). No server needed beyond static file serving (Cloudflare Pages). |
