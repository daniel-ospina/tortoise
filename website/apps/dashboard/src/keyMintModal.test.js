// keyMintModal.test.js — #2480 static tripwires (CI-run via
// dashboard-js-tests, node --test zero-dep convention — same read-main.jsx
// style as keyExpiryTripwire.test.js). #2480 moved new-key creation (label +
// #2426 expiry) OUT of the API Keys page — the lone "Label (e.g. CI,
// staging)" input above the table read as a search box (typing did nothing
// to the rows) — INTO a modal opened by "+ New key". The page-level render
// contract is pinned below so a regression that re-introduces the inline
// fields on the keys page (or drops the dialog wiring / a11y) fails CI.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const mainJsx = readFileSync(join(here, 'main.jsx'), 'utf8')
const indexCss = readFileSync(join(here, 'index.css'), 'utf8')

// 1. The inline create form is GONE — no key-create-form container in the
//    JSX and no dead CSS rule for it. The keys page now shows only the
//    table + the "+ New key" trigger (owner/admin-gated as before).
test('#2480: the keys page no longer renders an inline create form', () => {
  assert.doesNotMatch(mainJsx, /inline-form key-create-form/, 'inline create form container removed from the keys page')
  assert.doesNotMatch(indexCss, /key-create-form/, 'dead .key-create-form CSS rule removed')
  assert.match(mainJsx, /Only owners and admins can create or rotate keys in this dashboard\. Paste an existing key into the setup step to connect an agent\./,
    'member notice stays (members keep the notice + no trigger)')
})

// 2. The create fields exist ONCE each and live inside the mint dialog
//    ({keyMintOpen && …} block) — never rendered on the page itself.
test('#2480: the create fields live only inside the mint dialog', () => {
  assert.match(mainJsx, /aria-label="New key label"/, 'label input still present (inside the dialog)')
  assert.match(mainJsx, /aria-label="New key expiry"/, 'expiry preset select still present (inside the dialog)')
  assert.match(mainJsx, /aria-label="Custom expiry date"/, 'custom date input still present (inside the dialog)')
  assert.equal((mainJsx.match(/aria-label="New key label"/g) || []).length, 1,
    'label input must not be duplicated outside the dialog')
  assert.equal((mainJsx.match(/aria-label="New key expiry"/g) || []).length, 1,
    'expiry select must not be duplicated outside the dialog')
})

// 3. Dialog wiring + a11y: "+ New key" opens the dialog; role=dialog +
//    aria-modal + descriptive aria-label; focus enters on open (autoFocus,
//    wizard/team-create precedent) and returns to the trigger on close;
//    Escape / backdrop-click / Cancel close, never mid-mint (busy).
test('#2480: "+ New key" opens the dialog; Escape/backdrop/Cancel close; focus round-trips', () => {
  assert.match(mainJsx, /keyMintOpen,\s*setKeyMintOpen\]\s*=\s*React\.useState\(false\)/, 'dialog open state exists')
  assert.match(mainJsx, /const newKeyTriggerRef = React\.useRef\(null\)/, 'trigger ref exists (focus return)')
  assert.match(mainJsx, /ref=\{newKeyTriggerRef\}/, 'the "+ New key" trigger carries the ref')
  assert.match(mainJsx, /aria-haspopup="dialog"/, 'trigger announces the dialog')
  assert.match(mainJsx, /onClick=\{openKeyMint\}/, 'trigger opens the dialog')
  assert.match(mainJsx, />\+ New key<\/button>/, 'page trigger label is "+ New key"')
  assert.match(mainJsx, /function closeKeyMint\(\) \{\s*setKeyMintOpen\(false\)\s*if \(newKeyTriggerRef\.current\) newKeyTriggerRef\.current\.focus\(\)/,
    'close returns focus to the trigger')
  assert.match(mainJsx, /className="modal-backdrop" onClick=\{\(\) => \{ if \(!busy\) closeKeyMint\(\) \}\}>/,
    'backdrop click closes (blocked mid-mint)')
  assert.match(mainJsx, /className="modal" role="dialog" aria-modal="true" aria-label="Create a new API key"/,
    'dialog semantics + descriptive aria-label')
  assert.match(mainJsx, /e\.key === 'Escape' && !busy\) closeKeyMint\(\)/, 'Escape closes (blocked mid-mint)')
  assert.match(mainJsx, /autoFocus/, 'focus moves into the dialog on open')
  assert.match(mainJsx, /onClick=\{closeKeyMint\}>Cancel<\/button>/, 'Cancel button closes')
})

// 4. Mint semantics preserved byte-identically: empty-name mint stays
//    allowed (createKey trims to undefined — no required-label validation
//    added), Create is disabled ONLY while busy or with an invalid Custom
//    date, and the dialog auto-closes when the attempt resolves so the
//    shown-once card / error banner / cap notice surface on the page.
test('#2480: mint semantics preserved (empty-name, Custom-date gate, auto-close)', () => {
  assert.match(mainJsx, /mintKey\('', newKeyName\.trim\(\) \|\| undefined, days\)/, 'empty-name mint stays allowed')
  assert.match(mainJsx, /disabled=\{busy \|\| \(newKeyExpiryPreset === 'custom' && !expiryDaysFromDate\(newKeyExpiryDate\)\)\}/,
    'Create disabled only while busy or with an invalid Custom date')
  assert.match(mainJsx, /finally \{\s*setBusy\(false\)[\s\S]*?if \(keyMintOpen\) closeKeyMint\(\)\s*\}/,
    'dialog auto-closes when a mint attempt resolves')
})
