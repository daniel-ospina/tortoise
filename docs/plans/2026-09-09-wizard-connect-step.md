---
title: "Wizard connect step — #2698 redesign, #2710 fixes and #2827 follow-up"
type: engineering
domain: platform
doc_status: superseded
subjects.team: organisation-design-team
ownedBy: organisation-design-team
aboutSubjects: onboarding-wizard, dashboard-connect-step
aboutObjects: tortoise-dashboard, tortoise-mcp-server
created: 2026-09-09
updated: 2026-09-11
---

> **SUPERSEDED (2026-09-10, #2710):** two acceptance criteria below are no
> longer the shipped behaviour and must NOT be reinstated:
> 1. **Task 2** — "Clicking a pill opens the key modal ONLY if no key exists
>    yet". The pills are now pure display-mode toggles; clicking one never
>    opens/touches the shared key-create modal.
> 2. **Task 4** — "Auto-open modal on first load" / "Auto-open useEffect fires
>    when reaching step 2 without a key".
>
> Why: the shared key-create modal renders ONLY in the post-welcome dashboard
> tree, so queueing `keyModalOpen` from inside the wizard was invisible during
> the step and then popped a stray "Create new API key" modal (pre-set to the
> 30-day expiry default) on the dashboard after exit. #2710 deleted the
> auto-open effect and the pill→modal path, and replaced them with an inline,
> in-flow `wizardNoKeyAffordance` (mint + paste escape) rendered by every
> harness branch's no-key state — the mint is Never-expiring (the step's own
> "keys embedded in agents should never expire" hint it once matched was
> deleted in #2827, so the Never-only embed policy now lives in the
> paste-rejection reason, not a banner). Pinned by
> `website/apps/dashboard/src/wizardConnectTripwire.test.js` and
> `tests/e2e/test_dashboard_onboarding.py`.

<!-- research-path: skipped — zero third-party deps, no integration boundaries -->

# Wizard Connect Step — Per-Harness Agent-Driven Prompts

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Replace the legacy self-fork connect step (universal command + paste toggle) with per-harness prompt cards, key-included/separate pills, and a three-way copy-card interaction.

**Team:** organisation-design-team
**Role:** product-implementer

**Architecture:** Pure frontend JSX change in `website/apps/dashboard/src/main.jsx`. The existing key modal (`keyModalOpen`) is reused — one line added to hoist the key into `wizardDurableKey` on dismiss. No backend, no new API endpoints, no migrations. The build fork keeps the curl command already partially implemented in the branch.

**Critical architectural note:** The live wizard step 2 (`wizardStep === 2 && !isBuildFork`) currently has NO harness tab JSX rendering — it shows a single universal command + paste toggle. However, the `wizardHarness` state variable (line 984) and `HARNESS_ORDER`/`HARNESS_NAMES` imports (line 6, from `harnesses.js`) ARE already live — the implementer only needs to ADD the tab JSX rendering to the live step 2 block, not the state/constants. The harness tab JSX rendering (`<div className="harness-tabs">`) currently lives only in the `LEGACY_WIZARD_ARCHIVED` dead-code block (~5842+). This plan adds it to the live step 2.

**Reuse summary — what's already live vs what's new:**

| Piece | Status | Source / action |
|---|---|---|
| `wizardHarness` state | ✅ Live | line 984, default `'claude'` — reused as-is |
| `HARNESS_ORDER` / `HARNESS_NAMES` | ✅ Live | imported from `harnesses.js` line 6 — reused |
| Harness tab JSX | ♻️ Copy pattern | in `LEGACY_WIZARD_ARCHIVED` block (~5842+) — copy the `.harness-tabs` structure, filter out `chatgpt` |
| Key modal (`keyModalOpen`, `createKey()`, "Copy & done") | ✅ Live | reused as-is, minus 1-line hoist (Task 1) |
| `wizardMintDurableKey` (silent mint) | ✅ Live | build fork uses it — unchanged |
| `wizardHarnessContinue` (checkpoint) | ✅ Live | reused for Continue button |
| `durableConnectKey` (paste validation) | ✅ Live | imported from `sessionKey.js` — reused in Task 5 |
| `wizardDurableKey` / `wizardDurablePaste` / `wizardDurableError` | ✅ Live | state exists — reused |
| `WizardPromptCard` inline fn | ♻️ Refactor | current live code has an inline function (~5768) — refactor to top-level component (Task 3) |
| Three-way copy interaction | ♻️ Adapt | current self-fork block has click-anywhere + icon + bottom button — adapt into `WizardPromptCard` |
| Key pills (`wizardKeyMode`) | 🆕 New | Task 2 — new state + JSX |
| Per-harness content branching | 🆕 New | Task 4 — new JSX per harness (pi/cursor/claude/codex/desktop/web) |
| Paste escape for members/cap | 🆕 New | Task 5 — live block has NO paste UI; build fresh (validation from `durableConnectKey`) |
| Build fork curl | ✅ Done | already in branch — verify only (Task 6) |

**Existing state used (no new state except `wizardKeyMode`):**
- `wizardHarness` (line 984, defaults to `'claude'`)
- `wizardDurableKey` (line 2201)
- `wizardDurablePaste` (line 2218)
- `wizardDurableError` (existing)
- `wizardConnectBusy`, `wizardConnectError` (existing)
- `keyModalOpen`, `keyModalStage`, `newKey` (existing key modal)
- `capNotice` (existing — set by `createKey()` on 402)
- `isOwnerAdmin` (existing)
- `durableConnectKey` (imported from sessionKey.js)

### Pattern Research

Skipped — plan touches zero third-party dependencies. Pure React JSX + existing state management.

### Integration Surface Map

Skipped — no integration boundaries. The only external interaction is the existing `mintKey` API call (unchanged) and the existing `wizardHarnessContinue` checkpoint API call (unchanged).

---

### Task 1: Hoist the key from modal to wizard on "Copy & done"

**Intent:** The existing key modal's "Copy & done" button nulls `newKey` before closing, so the wizard has no access to the minted key after dismiss. We need it to save to `wizardDurableKey` so the pills/prompts can reference it.

**Acceptance:** After clicking "Copy & done", `wizardDurableKey` holds the plaintext key. The wizard renders prompt cards with the key embedded (if Key included) or shown separately (if Key separate).

**Files:**
- Modify: `website/apps/dashboard/src/main.jsx` (the "Copy & done" button onClick, in the key modal `keyModalStage === 'done'` branch)

**Step 1: Add `setWizardDurableKey(newKey)` before the nulls**

```js
// Current:
<button onClick={() => { navigator.clipboard.writeText(newKey); setNewKey(null); setNewKeyExpiresAt(null); setKeyModalOpen(false) }}>Copy &amp; done</button>

// Updated:
<button onClick={() => { navigator.clipboard.writeText(newKey); setWizardDurableKey(newKey); setNewKey(null); setNewKeyExpiresAt(null); setKeyModalOpen(false) }}>Copy &amp; done</button>
```

**Step 2: Verify the variable exists**

`wizardDurableKey` is already declared at line 2201 (`React.useState('')`). No new state needed.

---

### Task 2: Add harness tabs + wizardKeyMode state + pills to live step 2

**Intent:** The live self-fork connect step currently shows a universal command and paste toggle. Replace it with a harness tab row (Pi / Cursor / Claude Code / Codex / Claude Desktop / Claude Web — **ChatGPT is NOT a wizard harness tab**, it's key-less OAuth handled in the dashboard's ChatGPT tab, so it's filtered out of the tab row). Add `wizardKeyMode` state and the pills row for agent-driven harnesses.

**Acceptance:** User sees a row of harness tabs (`HARNESS_ORDER` minus `chatgpt`). Selecting a tab shows key pills (agent-driven harnesses) or manual config (Desktop, Web). Clicking a pill opens the key modal ONLY if no key exists yet (if a key already exists, clicking a pill just toggles the display mode without re-opening the modal).

**Files:**
- Modify: `website/apps/dashboard/src/main.jsx` (the self-fork `wizardStep === 2 && !isBuildFork` block)
- Modify: `website/apps/dashboard/src/index.css` (`.key-pills`, `.pill`, `harness-tabs` styles)

**Step 1: Add `wizardKeyMode` state**

```js
const [wizardKeyMode, setWizardKeyMode] = React.useState('included')
```

Add near other wizard state declarations (~line 2220).

**Step 2: Add CSS**

In `website/apps/dashboard/src/index.css`:
```css
.key-pills { display: flex; gap: 0.5rem; margin-bottom: 1rem; }
.key-pills button { flex: 1; padding: 0.5rem 0.75rem; border: 1px solid var(--border,#1e293b); border-radius: 8px; background: var(--surface,#0d1a2d); cursor: pointer; text-align: left; display: flex; flex-direction: column; }
.key-pills button.active { border-color: var(--accent,#06b6d4); background: rgba(6,182,212,0.08); }
.key-pills button strong { font-size: 14px; color: var(--text,#e2e8f0); }
.key-pills button span { font-size: 12px; color: var(--dim,#7d8ea3); }
```

**Step 3: Harness fallback for persisted `wizardHarness` values**

`wizardHarness` may persist as `'chatgpt'` (the legacy wizard default) from prior sessions. Add a guard that resets to a valid tab value:

```jsx
React.useEffect(() => {
  if (!['pi', 'cursor', 'claude', 'codex', 'claude-desktop', 'claude-web'].includes(wizardHarness)) {
    setWizardHarness('pi')
  }
}, [])
```

**Step 4: Replace the self-fork block**

The live self-fork block (currently ~55 lines of universal command + "I already have a key — paste it instead" toggle) is replaced with:

```jsx
{wizardStep === 2 && (isBuildFork ? (
  /* BUILD FORK — unchanged */
) : (isOwnerAdmin && !capNotice ? (
  <div className="harness">
    {/* Harness tabs — all except ChatGPT (OAuth, not a wizard harness) */}
    <div className="harness-tabs">
      {HARNESS_ORDER.filter(h => h !== 'chatgpt').map((h) => (
        <button key={h} type="button"
          className={'harness-tab' + (wizardHarness === h ? ' active' : '')}
          aria-pressed={wizardHarness === h}
          onClick={() => { setWizardHarness(h); setWizardCopied(''); setWizardConnectError(''); setWizardDurableError(''); if (h !== 'codex') setWizardCodexDesktop(false) }}>
          {HARNESS_NAMES[h]}
        </button>
      ))}
    </div>

    {/* Pills row — agent-driven harnesses only. If a key already exists,
        clicking a pill ONLY toggles the display mode (no modal re-open). */}
    {['pi', 'cursor', 'claude', 'codex'].includes(wizardHarness) && (
      <div className="key-pills">
        <button className={wizardKeyMode === 'included' ? 'active' : ''}
          onClick={() => { setWizardKeyMode('included'); if (!harnessKey) setKeyModalOpen(true) }}>
          <strong>Key included in prompt</strong>
          <span>Simple — easiest</span>
        </button>
        <button className={wizardKeyMode === 'separate' ? 'active' : ''}
          onClick={() => { setWizardKeyMode('separate'); if (!harnessKey) setKeyModalOpen(true) }}>
          <strong>Key separate from prompt</strong>
          <span>Manual — more secure</span>
        </button>
      </div>
    )}

    {/* Per-harness content — see Task 4 */}
    {wizardHarness === 'pi' && (...)}
    {wizardHarness === 'cursor' && (...)}
    {wizardHarness === 'claude' && (...)}
    {wizardHarness === 'codex' && (...)}
    {wizardHarness === 'claude-desktop' && (...)}
    {wizardHarness === 'claude-web' && (...)}

    {/* Bottom nav */}
    <div className="wizard-nav" style={{ marginTop: '1rem' }}>
      <button type="button" className="ghost" onClick={() => setWizardStep(1)}>← Back</button>
      <div className="wizard-nav-actions">
        {wizardConnectError && <p className="error" role="alert" style={{ margin: '0 0.5rem 0 0', fontSize: 13 }}>{wizardConnectError}</p>}
        <button type="button" className="btn-primary" onClick={wizardHarnessContinue} disabled={wizardConnectBusy}>
          {wizardConnectBusy ? 'Saving…' : (['pi','cursor','claude-desktop','claude-web'].includes(wizardHarness) ? 'Done — Continue to dashboard' : "I've set it up — Continue →")}
        </button>
        <button type="button" className="ghost" onClick={() => { setWizardPaused(true); setWizardStep(3) }}>Skip for now</button>
      </div>
    </div>
  </div>
) : (
  /* Paste escape — see Task 5 */
  <div className="harness">{pasteEscape}</div>
))}
```

---

### Task 3: Create `WizardPromptCard` React component + per-harness prompt text factory

**Intent:** Every prompt card needs the three-way copy interaction: click anywhere, top-right icon, bottom button. Build a proper React component to avoid hooks-in-loop issues.

**Acceptance:** `<WizardPromptCard text="..." />` renders a clickable card with top-right copy icon and bottom COPY button. Shows "Copied ✓" for ~1.6s after copy. A `wizardPromptText(harness, step, key, mode)` function returns the correct prompt string per harness.

**Files:**
- Modify: `website/apps/dashboard/src/main.jsx` (new component + helper function — `WizardPromptCard` defined at TOP LEVEL outside `App`, since it uses only props + local state and references no closure variables)

**Step 1: Create `WizardPromptCard` component**

```jsx
function WizardPromptCard({ text }) {
  const [copied, setCopied] = React.useState(false)
  const doCopy = React.useCallback(() => {
    navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 1600)
  }, [text])
  return (
    <div onClick={doCopy}
      style={{ position: 'relative', cursor: 'pointer', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', wordBreak: 'break-word', maxWidth: '100%', background: 'var(--surface,#0d1a2d)', border: '1px solid var(--border,#1e293b)', borderRadius: 8, padding: '12px 14px', fontFamily: 'var(--mono,ui-monospace,SFMono-Regular,Menlo,monospace)', fontSize: 14, lineHeight: 1.6, color: 'var(--text,#e2e8f0)' }}
      role="button" tabIndex={0} aria-label="Click to copy prompt"
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); doCopy() } }}>
      {text}
      <button onClick={(e) => { e.stopPropagation(); doCopy() }}
        style={{ position: 'absolute', top: 8, right: 8, background: 'var(--surface,#0d1a2d)', border: '1px solid var(--border,#1e293b)', borderRadius: 6, padding: '4px 8px', fontSize: 12, cursor: 'pointer', color: copied ? 'var(--accent,#06b6d4)' : 'var(--dim,#7d8ea3)' }}>
        {copied ? 'Copied ✓' : 'Copy'}
      </button>
      <div style={{ marginTop: '0.6rem', display: 'flex', justifyContent: 'center' }}>
        <button type="button" className={copied ? 'ghost small' : 'btn-primary small'}
          onClick={(e) => { e.stopPropagation(); doCopy() }}>
          {copied ? 'Copied ✓' : 'Copy'}
        </button>
      </div>
    </div>
  )
}
```

**Step 2: Create `wizardPromptText` factory**

```jsx
function wizardPromptText(harness, step, key, mode) {
  const url = 'https://api.premiselabs.co/mcp/'
  const docs = 'Docs: https://tortoise.premiselabs.co/docs'
  const keyLine = mode === 'included' ? `Key: ${key}` : 'I\'ll give you the API key when you need it.'
  const twoStepNote = 'Tell me when to restart'
  const step2Text = `Call tortoise_health to verify the connection, then tortoise_create_point to file my first point.\n${docs}`

  if (harness === 'pi') {
    if (step === 1) return `Give this prompt to your agent to install the MCP server\n\nAdd Tortoise MCP at ${url}.\n${keyLine}\nSave it to my shell profile (~/.zshrc).\n${twoStepNote} Pi.\nThen install the Tortoise skills (how-to-use-tortoise, tortoise-decide, tortoise-file-finding + tortoise-onboarding) from ${SKILLS_INSTALL_URL}.\n${docs}`
    if (step === 2) return `Restart Pi, then give it this prompt to complete setup\n\n${step2Text}`
  }
  if (harness === 'cursor') {
    if (step === 1) return `Give this prompt to your agent to install the MCP server\n\nAdd Tortoise MCP at ${url}.\n${keyLine}\nSave it to my shell profile (export TORTOISE_API_KEY=…) so Cursor can read it from its env.\n${twoStepNote} Cursor.\nThen install the Tortoise skills (how-to-use-tortoise, tortoise-decide, tortoise-file-finding + tortoise-onboarding) from ${SKILLS_INSTALL_URL}.\n${docs}`
    if (step === 2) return `Restart Cursor, then give it this prompt to complete setup\n\n${step2Text}`
  }
  if (harness === 'claude') {
    return `Give this prompt to your agent to set up Tortoise\n\nAdd Tortoise MCP at ${url}.\n${keyLine}\nThen install the Tortoise skills (how-to-use-tortoise, tortoise-decide, tortoise-file-finding + tortoise-onboarding) from ${SKILLS_INSTALL_URL}.\nThen call tortoise_health and tortoise_create_point to file my first point.\n${docs}`
  }
  if (harness === 'codex') {
    return `Give this prompt to your agent to set up Tortoise\n\nAdd Tortoise MCP at ${url}.\n${keyLine}\nSave it to my shell profile (export TORTOISE_API_KEY=…).\nThen install the Tortoise skills (how-to-use-tortoise, tortoise-decide, tortoise-file-finding + tortoise-onboarding) from ${SKILLS_INSTALL_URL}.\nThen call tortoise_health and tortoise_create_point to file my first point.\n${docs}`
  }
  // Desktop and Web are manual — no prompt for step 1, just step 2
  if (harness === 'claude-desktop') {
    if (step === 2) return step2Text
  }
  // claude-web: the connected-agent prompt is WORKFLOWS_PROMPT (imported from
  // harnesses.js) — Claude Web has no local skills; the full workflows content
  // is the whole value. Rendered as a <pre> block in Task 4 Step 5, NOT through
  // this factory. (WORKFLOWS_PROMPT is already imported at top of main.jsx.)
  return ''
}
```

---

### Task 4: Per-harness content rendering + auto-open modal + key display

**Intent:** This is the main rewrite. Render per-harness prompt cards (2-step vs 1-step) below the tabs and pills. Manual harnesses get config blocks. Auto-open modal on first load. **Prompt cards only render when `harnessKey` exists — otherwise a "Create an API key to see the setup prompt" notice shows.**

**Acceptance:** Each harness tab shows correct content. No broken prompt cards with empty keys (guarded by `harnessKey ?`). Key-display row appears in 'separate' mode for agent-driven harnesses. Auto-open useEffect fires when reaching step 2 without a key (with complete deps array).

**Files:**
- Modify: `website/apps/dashboard/src/main.jsx` (the harness-specific content below tabs + pills)

**Step 1: Common guards and helpers (data-driven, DRY)**

```jsx
const agentDriven2Step = ['pi', 'cursor']
const agentDriven1Step = ['claude', 'codex']
const manualHarnesses = ['claude-desktop', 'claude-web']

const keyDisplayRow = harnessKey && wizardKeyMode === 'separate' ? (
  <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', marginTop: '0.5rem' }}>
    <p className="dim small">Your API key (shown once):</p>
    <code style={{ flex: 1, padding: '0.4rem 0.6rem', background: 'var(--surface,#0d1a2d)', border: '1px solid var(--border,#1e293b)', borderRadius: 6, fontSize: 13 }}>{harnessKey}</code>
    <button type="button" className="btn-primary small" onClick={() => navigator.clipboard?.writeText(harnessKey)}>Copy</button>
  </div>
) : null
```

**Step 2: Agent-driven 2-step (Pi, Cursor) — data-driven single block**

```jsx
{agentDriven2Step.includes(wizardHarness) && (
  <div>
    <p className="dim small" style={{ marginBottom: '0.5rem' }}>Give this prompt to your agent to install the MCP server</p>
    {harnessKey ? (
      <WizardPromptCard text={wizardPromptText(wizardHarness, 1, harnessKey, wizardKeyMode)} />
    ) : (
      <p className="dim small">Create an API key to see the setup prompt.</p>
    )}
    {keyDisplayRow}
    <p className="dim small" style={{ textAlign: 'center', margin: '0.75rem 0' }}>
      Restart {HARNESS_NAMES[wizardHarness]}, then give it this prompt to complete setup
    </p>
    {harnessKey && (
      <WizardPromptCard text={wizardPromptText(wizardHarness, 2, harnessKey, wizardKeyMode)} />
    )}
  </div>
)}
```

**Step 3: Agent-driven 1-step (Claude Code, Codex) — data-driven single block**

```jsx
{agentDriven1Step.includes(wizardHarness) && (
  <div>
    <p className="dim small" style={{ marginBottom: '0.5rem' }}>Give this prompt to your agent to set up Tortoise</p>
    {harnessKey ? (
      <WizardPromptCard text={wizardPromptText(wizardHarness, 1, harnessKey, wizardKeyMode)} />
    ) : (
      <p className="dim small">Create an API key to see the setup prompt.</p>
    )}
    {keyDisplayRow}
  </div>
)}
```

**Step 4: Manual 2-step (Claude Desktop)**

```jsx
{wizardHarness === 'claude-desktop' && (
  <div>
    <p className="dim" style={{ margin: 0, lineHeight: 1.6 }}>
      Open Claude Desktop → Settings → Developer → Edit Config. Merge this into mcpServers (don't replace the whole file):
    </p>
    <pre className="snippet" style={{ margin: '0.75rem 0' }}>
{JSON.stringify({
  mcpServers: { tortoise: { type: 'http', url: 'https://api.premiselabs.co/mcp/', headers: { Authorization: `Bearer ${harnessKey}` } } }
}, null, 2)}
    </pre>
    <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', marginTop: '0.5rem' }}>
      <p className="dim small">Your API key (shown once):</p>
      <code style={{ flex: 1, padding: '0.4rem 0.6rem', background: 'var(--surface,#0d1a2d)', border: '1px solid var(--border,#1e293b)', borderRadius: 6, fontSize: 13 }}>{harnessKey}</code>
      <button type="button" className="btn-primary small" onClick={() => navigator.clipboard?.writeText(harnessKey)}>Copy</button>
    </div>
    <p className="dim small" style={{ marginTop: '0.5rem' }}>Save and restart Claude Desktop.</p>
    <p className="dim small" style={{ textAlign: 'center', margin: '0.75rem 0' }}>After restart, start a new chat and give it this prompt to complete setup</p>
    {harnessKey && (
      <WizardPromptCard text={wizardPromptText('claude-desktop', 2, harnessKey, 'separate')} />
    )}
  </div>
)}
```

**Step 5: Manual 2-step (Claude Web)**

```jsx
{wizardHarness === 'claude-web' && (
  <div>
    <p className="dim" style={{ margin: 0, lineHeight: 1.6 }}>
      Go to claude.ai → Settings → Connectors → Add custom connector:
    </p>
    <ul className="dim small" style={{ lineHeight: 1.7, paddingLeft: '1.2rem' }}>
      <li>Name: Tortoise</li>
      <li>Server URL: https://api.premiselabs.co/mcp/</li>
      <li>Headers: Authorization: Bearer {harnessKey}</li>
    </ul>
    <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', marginTop: '0.5rem' }}>
      <p className="dim small">Your API key (shown once):</p>
      <code style={{ flex: 1, padding: '0.4rem 0.6rem', background: 'var(--surface,#0d1a2d)', border: '1px solid var(--border,#1e293b)', borderRadius: 6, fontSize: 13 }}>{harnessKey}</code>
      <button type="button" className="btn-primary small" onClick={() => navigator.clipboard?.writeText(harnessKey)}>Copy</button>
    </div>
    <p className="dim small" style={{ textAlign: 'center', margin: '0.75rem 0' }}>After setting up the connector, start a new chat and paste this prompt (it tells your agent how to work with Tortoise):</p>
    <pre className="snippet" style={{ marginTop: '0.5rem' }}>{WORKFLOWS_PROMPT}</pre>
  </div>
)}
```

**Note:** ChatGPT is NOT a wizard harness tab — it's key-less OAuth handled in the dashboard's ChatGPT tab. No content branch needed in the wizard.

**Step 6: Auto-open key modal (complete deps array)**

```jsx
React.useEffect(() => {
  if (wizardStep === 2 && isOwnerAdmin && !harnessKey && !capNotice) {
    setKeyModalOpen(true)
    setKeyModalStage('form')
  }
}, [wizardStep, wizardHarness, harnessKey, isOwnerAdmin, capNotice])
```

**Note on dismiss-without-create:** if the user dismisses the modal without creating a key (empty `harnessKey`), the effect does NOT re-fire (deps unchanged). The pills remain visible as the manual re-open affordance — a "Create an API key to see the setup prompt" notice shows in the content area. This is the documented behavior.

---

### Task 5: Paste escape (NEW — no existing paste UI in live self-fork block)

**Intent:** Non-owner/admin users can't mint keys. Users with 2 keys hit the 402 cap. Both need a fallback paste input. The live self-fork block currently has NO paste input — this is new code, not a port.

**Acceptance:** When `!isOwnerAdmin || capNotice`, the entire harness-tabs + pills + prompt cards block is replaced by a paste input (with placeholder "Paste an API key (tt_…)") and a "Use this key" button. Paste validation rejects: non-`tt_` prefixes, bootstrap keys, expiring keys, revoked/disabled keys, AND **unknown keys** (prefix matches no row in the org — never embed on unknown, per sessionKey.js).

**Files:**
- Create: paste escape JSX in the self-fork block (the `: (` else branch in Task 2 Step 4's ternary)
- Modify: `website/apps/dashboard/src/main.jsx` — clean up `wizardShowPaste`/`step3PasteAutofocus` references

**Step 1: Paste escape render**

```jsx
{(!isOwnerAdmin || capNotice) ? (
  <div>
    <p className="dim" style={{ margin: '0.9rem 0 0', lineHeight: 1.6 }}>
      {!isOwnerAdmin
        ? 'Only owners and admins can create API keys in this dashboard. Paste an API key below from your agent or an owner/admin.'
        : capNotice}
    </p>
    <div id="wizard-paste-row" style={{ marginTop: '0.75rem', display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
      <input type="password" aria-label="Paste an API key" placeholder="Paste an API key (tt_…)"
        value={wizardDurablePaste}
        onChange={(e) => { setWizardDurablePaste(e.target.value); setWizardDurableError('') }}
        onKeyDown={(e) => { if (e.key === 'Enter' && wizardDurablePaste.trim()) { e.preventDefault(); document.querySelector('[data-paste-use]')?.click() } }}
        style={{ flex: 1, minWidth: 0, padding: '0.5rem 0.65rem', background: 'var(--surface,#0d1a2d)', border: '1px solid var(--border,#1e293b)', borderRadius: 8, fontSize: 13, color: 'inherit' }} />
      <button data-paste-use type="button" className="ghost" disabled={!wizardDurablePaste.trim()}
        onClick={() => {
          const pasted = wizardDurablePaste.trim()
          if (!pasted) return
          if (!/^tt_/.test(pasted)) {
            setWizardDurableError('That does not look like a Tortoise API key (tt_…). Paste the full key from the API Keys tab.')
            return
          }
          const check = durableConnectKey('', pasted, keys)
          if (check.source === 'unknown') {
            setWizardDurableError('That key does not match any key in this organization. Paste a key from this organization\'s API Keys tab, or ask an owner/admin to create one.')
            return
          }
          if (check.source === 'bootstrap') {
            setWizardDurableError(`That key can't be used — it was created for a login session and stops working after 24 hours. ${isOwnerAdmin ? 'Create a new key in the API Keys tab.' : 'Ask an owner or admin to create a new key for you.'}`)
            return
          }
          if (check.source === 'expiring' || check.source === 'revoked' || check.source === 'disabled') {
            setWizardDurableError(`That key can't be used — it expires or is revoked. ${isOwnerAdmin ? 'Rotate it in the API Keys tab and paste the replacement.' : 'Ask an owner or admin to create or rotate a key for you.'}`)
            return
          }
          setWizardDurableKey(pasted)
          setWizardDurablePaste('')
        }}>Use this key</button>
    </div>
    {wizardDurableError && (
      <p className="error" role="alert" style={{ margin: '0.6rem 0 0', fontSize: 13 }}>{wizardDurableError}</p>
    )}
    <div className="wizard-nav" style={{ marginTop: '0.75rem' }}>
      <button type="button" className="ghost" onClick={() => setWizardStep(1)}>← Back</button>
      <div className="wizard-nav-actions">
        {wizardDurableKey && (
          <button type="button" className="btn-primary" onClick={wizardHarnessContinue} disabled={wizardConnectBusy}>
            {wizardConnectBusy ? 'Saving…' : 'Continue to dashboard'}
          </button>
        )}
        <button type="button" className="ghost" onClick={() => { setWizardPaused(true); setWizardStep(3) }}>Skip for now</button>
      </div>
    </div>
  </div>
) : (
  /* Harness tabs + pills + prompt cards — see Task 2 Step 4 */
  ...
)}
```

**Step 2: Clean up `wizardShowPaste` and `step3PasteAutofocus`**

Audit EVERY reference to `wizardShowPaste` / `setWizardShowPaste` outside the replaced block:
- ~line 1187 (comment)
- ~line 1194 (`step3PasteAutofocus` declaration)
- ~line 1200 (comment)
- ~line 2223 (state declaration — KEEP the declaration, it may be referenced)
- ~line 5558 (`setWizardShowPaste(false)` in a welcome-header button)

Remove the disclosure toggle JSX (the "I already have a key — paste it instead" link). Remove `step3PasteAutofocus` (line 1194) and its conditional at line 1196. For the `setWizardShowPaste(false)` at line 5558 — remove that call (it's a no-op now). Keep the `useState` declaration to avoid breaking other potential references, but note it's unused.

---

### Task 6: Build fork verification

**Intent:** The build fork already has the curl command from the earlier iteration. Verify it still works correctly after the modal hoisting change (Task 1).

**Acceptance:** Build fork renders key (shown once) + curl command + "I've set it up — Continue →" button + SDK docs link + Back/Skip nav. The `wizardMintDurableKey` call is unchanged (no modal — silent mint). Build passes with zero errors.

**Files:**
- Verify: `website/apps/dashboard/src/main.jsx` (the `isBuildFork` branch)

**Step 1: Verify build fork is untouched**

The build fork block should still show:
- No `harnessKey` → "Create an API key for {org}" button (calls `wizardMintDurableKey`, no modal) + "Manage API keys →" link
- Has `harnessKey` → key shown once + curl command + "I've set it up — Continue →" + SDK docs link
- No pills, no prompt cards, no harness tabs

**Step 2: Build verification**

```bash
cd website/apps/dashboard && npx vite build --logLevel error
```

Build must pass with zero errors.

---

### Task 7: Full build verification + cleanup

**Intent:** Run the full build, verify everything compiles, and remove any orphaned references.

**Acceptance:** `vite build` passes. No console errors. No orphaned CSS classes.

**Files:**
- Test: build

**Step 1: Build**

```bash
cd website/apps/dashboard && npx vite build --logLevel error
```

**Step 2: Check for orphaned CSS**

Run a quick grep for CSS class names that might be unused. The old `.snippet` usage inside the self-fork block is removed; verify no selectors reference deleted DOM.

**Step 3: Manual smoke test**

Open the built dashboard, run through: sign in → wizard → fork=self → connect step. Verify:
1. Harness tabs appear (6 tabs, no ChatGPT)
2. Clicking Pi shows pills + "Create an API key to see the setup prompt" (no broken prompt)
3. Clicking "Key included" opens modal → create → prompt appears with key embedded
4. Clicking "Key separate" (after key exists) toggles display mode, shows key row below prompt
5. Switching to Claude Desktop shows manual config block
6. Member account shows paste escape only

## Edge Cases & Failure Modes

| Scenario | Expected behavior |
|----------|------------------|
| User lands on connect step with no key (owner/admin) | Harness tabs + pills visible. Modal auto-opens. After create → prompt cards render. |
| User dismisses modal without creating key | No re-open (deps unchanged). Pills remain as manual re-open affordance. "Create an API key to see the setup prompt" notice in content area. |
| User switches harness tabs | Content switches. Modal re-opens if `!harnessKey` (deps include `wizardHarness`). |
| User hits 402 cap (max 2 keys) | Modal errors with cap notice. `capNotice` truthy → entire harness UI replaced by paste escape. |
| User is a member (not owner/admin) | No tabs, no pills, no auto-modal. Paste input only with "ask an owner/admin" copy. |
| User clicks "Key included" → creates key | Key embedded in prompt. One copy-paste gives agent everything. No separate key row. |
| User clicks "Key separate" → creates key | Prompt says "I'll give you the API key when you need it". `keyDisplayRow` shows key below prompt with copy button. |
| User clicks a pill when a key already exists | Pills toggle display mode only — no modal re-open, no double-mint (avoids 2-key cap). |
| User pastes an unknown key (wrong org, typo) | Rejected with "does not match any key in this organization" (never embed on unknown). |
| Persisted `wizardHarness === 'chatgpt'` from legacy | Guard resets to `'pi'` on mount (Task 2 Step 3). |
| User clicks "Done — Continue to dashboard" before step 2 | Auto-complete is the real gate. Checkpoint is a UX gesture. |

## #2827 follow-up

Round-2 review of the connect step (issue #2827) shipped these corrections on
top of the round-1 rewrite:

- **Deleted the unapproved embed hint.** The "Keys embedded in agents should
  never expire…" banner was not in the approved copy; removed. The Never-only
  embedding policy survives as the expiring-paste rejection reason
  (`It expires, and a key embedded in an agent must never expire`).
- **De-duplicated captions.** Each prompt body no longer repeats the caption
  sentence the JSX already renders; verb unified to "connect Tortoise"; the
  single-step tabs (Claude Code / Codex CLI) drop the leading `1.`.
- **Claude Desktop + Claude Web → Connectors.** Both filesystem-less tabs now
  document the Connectors UI (`Server URL` + `Authorization: Bearer <key>`)
  instead of the `claude_desktop_config.json` / `mcpServers` shape, which only
  supports local stdio servers and does nothing for the remote HTTP server.
  The `UNIVERSAL_COMMAND['claude-desktop']` copy and the served onboarding
  skill were brought in line.
- **Copyable workflows card with the verify/file footer.** Claude Desktop/Web
  step 2 renders `WORKFLOWS_PROMPT` + the `tortoise_health` →
  `tortoise_create_point` footer through `WizardPromptCard` (it was a bare,
  non-copyable `<pre>`).
- **Shared step-label style.** One `wizardStepLabelStyle` for every tab
  (numbered labels only where a tab genuinely has two steps); Pi/Cursor step 2
  is gated as one unit so the no-key state has no orphaned "2.".
- **Re-anchored `keyExpiryTripwire`.** Item 5 of
  `keyExpiryTripwire.test.js` now pins the expiring-paste rejection + truthful
  reason (the deleted hint assertion is gone).