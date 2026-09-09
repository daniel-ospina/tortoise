<!-- research-path: skipped — zero third-party deps, no integration boundaries -->

# Wizard Connect Step — Per-Harness Agent-Driven Prompts

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Replace the legacy self-fork connect step (universal command + paste toggle) with per-harness prompt cards, key-included/separate pills, and a three-way copy-card interaction.

**Team:** organisation-design-team
**Role:** product-implementer

**Architecture:** Pure frontend JSX change in `website/apps/dashboard/src/main.jsx`. The existing key modal (`keyModalOpen`) is reused — one line added to hoist the key into `wizardDurableKey` on dismiss. No backend, no new API endpoints, no migrations. The build fork keeps the curl command already partially implemented in the branch.

### Pattern Research

Skipped — plan touches zero third-party dependencies. Pure React JSX + existing state management.

### Integration Surface Map

Skipped — no integration boundaries. The only external interaction is the existing `mintKey` API call (unchanged) and the existing `wizardHarnessContinue` checkpoint API call (unchanged).

---
## Tasks

### Task 1: Hoist the key from modal to wizard on "Copy & done"

**Intent:** The existing key modal's "Copy & done" button nulls `newKey` before closing, so the wizard has no access to the minted key after dismiss. We need it to save to `wizardDurableKey` so the pills/prompts can reference it.

**Accpetance:** After clicking "Copy & done", `wizardDurableKey` holds the plaintext key. The wizard renders prompt cards with the key embedded (if Key included) or shown separately (if Key separate).

**Files:**
- Modify: `website/apps/dashboard/src/main.jsx:6261` (the "Copy & done" button onClick)

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

### Task 2: Add `wizardKeyMode` state and pills row

**Intent:** The config-writing harnesses (Pi, Cursor, Claude Code, Codex CLI) show a pills row above the prompt cards: "Key included in prompt" / "Key separate from prompt". Selecting either opens the key modal.

**Accpetance:** Pills row renders for agent-driven harnesses only (not Desktop/Web). Clicking a pill sets `wizardKeyMode` and opens `setKeyModalOpen(true)`. Default: `'included'`.

**Files:**
- Modify: `website/apps/dashboard/src/main.jsx` (add state near line 2201, add pills render in the wizard step 2 self-fork branch)

**Step 1: Add state**

```js
const [wizardKeyMode, setWizardKeyMode] = React.useState('included')
```

Add near other wizard state declarations (~line 2220).

**Step 2: Add CSS for pills**

In `website/apps/dashboard/src/index.css`, add:
```css
.key-pills { display: flex; gap: 0.5rem; margin-bottom: 1rem; }
.key-pills button { flex: 1; padding: 0.5rem 0.75rem; border: 1px solid var(--border,#1e293b); border-radius: 8px; background: var(--surface,#0d1a2d); cursor: pointer; text-align: left; display: flex; flex-direction: column; }
.key-pills button.active { border-color: var(--accent,#06b6d4); background: rgba(6,182,212,0.08); }
.key-pills button strong { font-size: 14px; color: var(--text,#e2e8f0); }
.key-pills button span { font-size: 12px; color: var(--dim,#7d8ea3); }
```

**Step 3: Render pills row in self-fork connect step**

In the `wizardStep === 2 && !isBuildFork` branch, before the harness-specific content:

```jsx
const isAgentDriven = ['pi', 'cursor', 'claude', 'codex'].includes(wizardHarness)
{isAgentDriven && (
  <div className="key-pills">
    <button className={wizardKeyMode === 'included' ? 'active' : ''}
      onClick={() => { setWizardKeyMode('included'); setKeyModalOpen(true) }}>
      <strong>Key included in prompt</strong>
      <span>Simple — easiest</span>
    </button>
    <button className={wizardKeyMode === 'separate' ? 'active' : ''}
      onClick={() => { setWizardKeyMode('separate'); setKeyModalOpen(true) }}>
      <strong>Key separate from prompt</strong>
      <span>Manual — more secure</span>
    </button>
  </div>
)}
```

Auto-open the modal on first load if no `harnessKey` exists yet (for Desktop/Web too):
```jsx
React.useEffect(() => {
  if (wizardStep === 2 && !harnessKey && isOwnerAdmin) {
    setKeyModalOpen(true)
  }
}, [wizardStep])
```

---

### Task 3: Create `promptCard` helper component

**Intent:** Every prompt card needs the three-way copy interaction: click anywhere, top-right icon, bottom button. Build a reusable inline component to avoid repetition across 6 harnesses × 1–2 cards.

**Accpetance:** Calling `promptCard('text')` returns a JSX element with click-anywhere copy, top-right icon, and bottom COPY button. Shows "Copied ✓" for ~1.6s after copy.

**Files:**
- Modify: `website/apps/dashboard/src/main.jsx` (inline helper function before the wizard render)

**Step 1: Add the helper function**

```jsx
function wizardPromptCard(text) {
  const [copied, setCopied] = React.useState(false)
  const doCopy = () => {
    navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 1600)
  }
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

**Placement:** Before the main render return, after all hook declarations (~line 5540, just before the `isBuildFork`/`wizardConnectHarness` render variables).

---

### Task 4: Rewrite the self-fork connect step (main task)

**Intent:** Replace the entire `wizardStep === 2 && !isBuildFork` branch with per-harness content. Each harness tab renders its own set of prompt cards (1-step or 2-step) with the three-way copy design.

**Accpetance:** Selecting each harness tab shows the correct prompts per the issue spec. 2-step harnesses show two cards with a "Restart X…" instruction between them. 1-step harnesses show one card. Desktop/Web show manual config blocks with key below.

**Files:**
- Modify: `website/apps/dashboard/src/main.jsx:5706-6109` (the entire self-fork block, currently 404 lines)

**Step 1: Prompt text factory per harness**

Build a function that returns the prompt text for each harness + step:

```jsx
function wizardPromptText(harness, step, key, mode) {
  const url = 'https://api.premiselabs.co/mcp/'
  const docs = 'Docs: https://tortoise.premiselabs.co/docs'
  const keyLine = mode === 'included' ? `Key: ${key}` : 'I\'ll give you the API key when you need it.'
  const twoStepNote = 'Tell me when to restart'

  if (harness === 'pi') {
    if (step === 1) return `Give this prompt to your agent to install the MCP server\n\nAdd Tortoise MCP at ${url}.\n${keyLine}\nSave it to my shell profile (~/.zshrc).\n${twoStepNote} Pi.\n${docs}`
    if (step === 2) return `Restart Pi, then give it this prompt to complete setup\n\nCall tortoise_health to verify the connection, then tortoise_create_point to file my first point.\n${docs}`
  }
  if (harness === 'cursor') {
    if (step === 1) return `Give this prompt to your agent to install the MCP server\n\nAdd Tortoise MCP at ${url}.\n${keyLine}\nSave it to my shell profile (~/.zshrc).\n${twoStepNote} Cursor.\n${docs}`
    if (step === 2) return `Restart Cursor, then give it this prompt to complete setup\n\nCall tortoise_health to verify the connection, then tortoise_create_point to file my first point.\n${docs}`
  }
  if (harness === 'claude') {
    return `Give this prompt to your agent to set up Tortoise\n\nAdd Tortoise MCP at ${url}.\n${keyLine}\nThen call tortoise_health and tortoise_create_point to file my first point.\n${docs}`
  }
  if (harness === 'codex') {
    return `Give this prompt to your agent to set up Tortoise\n\nAdd Tortoise MCP at ${url}.\n${keyLine}\nSave it to my shell profile (export TORTOISE_API_KEY=…).\nThen call tortoise_health and tortoise_create_point to file my first point.\n${docs}`
  }
  // Desktop and Web are manual — no prompt for step 1, just step 2
  if (harness === 'claude-desktop' || harness === 'claude-web') {
    if (step === 2) return `Call tortoise_health to verify the connection, then tortoise_create_point to file my first point.\n${docs}`
  }
  return ''
}
```

**Step 2: Harness-specific render per tab**

Remove the current universal command / ChatGPT branch / key-gate branch structure. Replace with simple branching per harness:

```jsx
{(wizardHarness === 'pi' || wizardHarness === 'cursor') && (
  <div>
    {wizardPromptCard(wizardPromptText(wizardHarness, 1, harnessKey, wizardKeyMode))}
    <p className="dim small" style={{ textAlign: 'center', margin: '0.75rem 0' }}>
      Restart {wizardHarness === 'pi' ? 'Pi' : 'Cursor'}, then give it this prompt to complete setup
    </p>
    {wizardPromptCard(wizardPromptText(wizardHarness, 2, harnessKey, wizardKeyMode))}
  </div>
)}
{(wizardHarness === 'claude' || wizardHarness === 'codex') && (
  <div>
    {wizardPromptCard(wizardPromptText(wizardHarness, 1, harnessKey, wizardKeyMode))}
  </div>
)}
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
    {keySeparateField}
    <p className="dim small">Save and restart Claude Desktop.</p>
    <p className="dim small" style={{ textAlign: 'center', margin: '0.75rem 0' }}>
      After restart, start a new chat and give it this prompt to complete setup
    </p>
    {wizardPromptCard(wizardPromptText('claude-desktop', 2, harnessKey, 'separate'))}
  </div>
)}
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
    {keySeparateField}
    <p className="dim small" style={{ textAlign: 'center', margin: '0.75rem 0' }}>
      After setting up the connector, start a new chat and give it this prompt to complete setup
    </p>
    {wizardPromptCard(wizardPromptText('claude-web', 2, harnessKey, 'separate'))}
  </div>
)}
```

Where `keySeparateField` renders:
```jsx
<div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', marginTop: '0.75rem' }}>
  <code style={{ flex: 1, padding: '0.5rem 0.75rem', ... }}>{harnessKey}</code>
  <button type="button" className="btn-primary" onClick={() => navigator.clipboard?.writeText(harnessKey)}>Copy</button>
</div>
```

**Step 3: Continue button**

After the harness-specific content, render the common bottom nav:
```jsx
<div className="wizard-nav" style={{ marginTop: '1rem' }}>
  <button type="button" className="ghost" onClick={() => setWizardStep(1)}>← Back</button>
  <div className="wizard-nav-actions">
    <button type="button" className="btn-primary" onClick={wizardHarnessContinue} disabled={wizardConnectBusy}>
      {wizardConnectBusy ? 'Saving…' : (['pi','cursor','claude-desktop','claude-web'].includes(wizardHarness) ? 'Done — Continue to dashboard' : "I've set it up — Continue →")}
    </button>
    <button type="button" className="ghost" onClick={() => { setWizardPaused(true); setWizardStep(3) }}>Skip for now</button>
  </div>
</div>
```

**Step 4: Auto-open key modal**

```jsx
React.useEffect(() => {
  if (wizardStep === 2 && !harnessKey && isOwnerAdmin && ['pi','cursor','claude','codex','claude-desktop','claude-web'].includes(wizardHarness)) {
    setKeyModalOpen(true)
    setKeyModalStage('form')
  }
}, [wizardStep, wizardHarness])
```

This ensures the modal opens immediately when the user reaches the connect step or switches harness tabs.

---

### Task 5: Paste escape for members and cap scenarios

**Intent:** Non-owner/admin users can't mint keys. Users with 2 keys hit the 402 cap. Both need a fallback paste input.

**Accpetance:** When `!isOwnerAdmin || capNotice` shows, the pills/auto-open-modal are replaced by a paste input with validation (existing logic). The owner/admin disclosure toggle (`wizardShowPaste`) is removed.

**Files:**
- Modify: `website/apps/dashboard/src/main.jsx` (paste escape render)

**Step 1: Conditional render**

At the top of the self-fork block, before any pills or prompt cards:
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
        value={wizardDurablePaste} onChange={(e) => setWizardDurablePaste(e.target.value)} ... />
      <button data-paste-use type="button" className="ghost" disabled={!wizardDurablePaste.trim()}
        onClick={() => { /* existing paste validation */ }}>Use this key</button>
    </div>
  </div>
) : (
  /* pills + prompt cards + nav */
)}
```

The paste validation logic is copied from the existing code (~line 5961-6040) — keep it verbatim.

**Step 2: Remove `wizardShowPaste` toggle**

Remove the owner/admin disclosure toggle and `setWizardShowPaste(false)` calls. Keep the `wizardShowPaste` state declaration (it may be referenced elsewhere) but it's no longer toggled for owners/admins. The paste input appears automatically when `!isOwnerAdmin || capNotice`.

---

### Task 6: Build fork (already partially done)

The build fork already has the curl command from the earlier iteration. Verify it's correct and test:

```jsx
{/* Already implemented in branch — verify */}
{!harnessKey ? (
  /* Create key button */
) : (
  /* Key shown once + curl command + Continue button */
)}
```

No changes needed beyond the modal hoisting from Task 1 (the build fork uses `wizardMintDurableKey` which creates silently — no modal).

---

### Task 7: Remove dead code

**Intent:** After the rewrite, several state variables and UI elements are no longer used by the owner/admin path.

**Accpetance:** Removing them doesn't break anything.

**Files:**
- Modify: `website/apps/dashboard/src/main.jsx`

**Step 1: Check what can be removed**

- `wizardShowPaste` — keep declaration (may be referenced), no longer toggled by owners/admins
- `step3PasteAutofocus` — references `wizardShowPaste`, can be simplified
- `wizardDurablePaste` — keep for member/cap path
- `wizardCopyStep`, `wizardCopyFailed` — still used by ChatGPT legacy branch? Check line 5874

Actually, be conservative: **don't remove anything** that might be referenced. The paste toggle stays in the DOM but hidden. Cleanup can happen in a follow-up.

**Step 2: Verify build**

```bash
cd website/apps/dashboard && npx vite build --logLevel error
```

---

### Task 8: Update tests if needed

**Intent:** Ensure existing E2E tests still pass with the new UI.

**Accpetance:** All relevant tests pass.

**Files:**
- Test: `tests/e2e/test_dashboard_onboarding.py` (check for wizard step selectors)

**Step 1: Check test selectors**

Look for any test that references `#wizard-paste-row`, `.harness-tabs`, `.snippet`, or `.connect-build`. If selectors changed, update them.

## Journey Test Map

Skipped — no user-facing journeys changed in behavior. The underlying API calls (`POST /v1/onboarding/state/checkpoint`, `POST /v1/team/keys`) are unchanged.

## Edge Cases & Failure Modes

| Scenario | Expected behavior |
|----------|------------------|
| User lands on connect step with no key | Modal auto-opens, user creates key then sees prompts |
| User switches harness tabs | Modal re-opens (auto-trigger on !harnessKey) |
| User hits 402 cap (max 2 keys) | Modal errors, paste input appears as fallback |
| User is a member (not owner/admin) | No pills, no auto-modal — just paste input + "ask an owner/admin" |
| User clicks "Key separate" | Prompt says "I'll give you the API key" and key is shown below in a copy field |
| User clicks "Done — Continue to dashboard" before step 2 | That's fine — auto-complete is the real gate. Checkpoint is just a UX gesture |