// keyMintFailureExec.test.js — #4330.
//
// WHY THIS FILE EXISTS. The reported bug was a create-key mint that FAILED
// (production: a 402 key-cap from POST /v1/team/keys) and left the modal on its
// 'done' reveal stage anyway. `newKey` was still the initial `null`, so the
// reveal rendered an empty `.key-value` box (the owner's "square"), and
// "Copy & done" ran `navigator.clipboard.writeText(null)` — which stringifies
// to the four-character string "null". The 402 message was invisible: the
// handler puts a cap failure on `capNotice`, which rendered on the tab BEHIND
// the modal, and explicitly clears `error`.
//
// WHY EXECUTION, NOT A TEXT SCAN. The lane's exit claim is a BEHAVIOUR: "no
// failed mint can reach the reveal, and no falsy value can reach the
// clipboard". Source text has unbounded spellings — `mk && advance()`,
// `Boolean(mk) ? …`, a helper that advances — and every one of them would
// satisfy a regex pin while keeping the bug. So this file takes the REAL
// `createKey` / `copyNewKey` / `dismissKeyModal` out of `main.jsx`, builds them
// with `new Function(...)` over stub deps, and runs the actual paths. The text
// pins at the bottom stay only as cheap backstops for the DOM gate (a
// behaviour test cannot see JSX).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { stripComments } from './testSupport.js'
// The REAL cap-notice builder, not a stub. A local copy is a second authority:
// its fallback branch can drift from the shipped one, and the assertions would
// then verify the copy instead of the product (review cycle 3, P2).
import { upgradeNoticeFrom } from './keyAllowance.js'

const here = dirname(fileURLToPath(import.meta.url))
const mainJsx = readFileSync(join(here, 'main.jsx'), 'utf8')
const mainJsxCode = stripComments(mainJsx)

// ── token-aware extraction of a real declaration from main.jsx ──────────────
// Strings, template literals and comments are skipped so a brace inside copy
// cannot truncate the slice. A missing declaration THROWS — the test must fail
// loudly, never silently run nothing.
function skipString(src, i) {
  const q = src[i]
  i += 1
  while (i < src.length) {
    const c = src[i]
    if (c === '\\') { i += 2; continue }
    if (q === '`' && c === '$' && src[i + 1] === '{') {
      let depth = 1
      i += 2
      while (i < src.length && depth > 0) {
        const cc = src[i]
        if (cc === '\\') { i += 2; continue }
        if (cc === "'" || cc === '"' || cc === '`') { i = skipString(src, i); continue }
        if (cc === '{') depth++
        else if (cc === '}') depth--
        i++
      }
      continue
    }
    if (c === q) return i + 1
    i += 1
  }
  return i
}

function matchBrace(src, open) {
  let depth = 0
  for (let i = open; i < src.length; i++) {
    const ch = src[i]
    if (ch === '/' && src[i + 1] === '/') {
      i = src.indexOf('\n', i)
      if (i < 0) return -1
      continue
    }
    if (ch === '/' && src[i + 1] === '*') {
      i = src.indexOf('*/', i + 2)
      if (i < 0) return -1
      i += 1
      continue
    }
    if (ch === "'" || ch === '"' || ch === '`') { i = skipString(src, i) - 1; continue }
    if (ch === '{') depth++
    else if (ch === '}') {
      depth--
      if (depth === 0) return i
    }
  }
  return -1
}

// `decl` is the full declaration prefix, e.g. 'async function createKey'.
function extractDeclaration(src, decl) {
  const start = src.indexOf(`${decl}(`)
  assert.ok(start > -1, `main.jsx must declare ${decl}()`)
  const open = src.indexOf('{', start)
  assert.ok(open > -1, `${decl}: the body must open with {`)
  const end = matchBrace(src, open)
  assert.ok(end > -1, `${decl}: the body braces must balance`)
  return src.slice(start, end + 1)
}

// Build the REAL declarations in one scope. Capturing them by name means the
// caller under test calls the implementation, not a copy.
function build(deps, decls) {
  const body = `${decls.map((d) => extractDeclaration(mainJsx, d)).join('\n')}\nreturn { ${decls.map((d) => d.replace(/^async /, '').replace(/^function /, '')).join(', ')} }`
  const params = Object.keys(deps)
  return new Function(...params, body)(...params.map((n) => deps[n]))
}

// ── createKey ───────────────────────────────────────────────────────────────
function createEnv(overrides = {}) {
  const calls = {
    newKey: [], capNotice: [], keyModalCapNotice: [], error: [], loadAll: 0,
  }
  const deps = {
    calls,
    currentOrgId: 'org-A',
    busy: false,
    newKeyExpiryPreset: '30',
    newKeyExpiryDate: '',
    newKeyName: '',
    // The real helper is a pure date→days derivation and is irrelevant to a
    // preset-based mint; a stub keeps this file free of that dependency.
    expiryDaysFromDate: () => 30,
    orgIdRef: { current: 'org-A' },
    team: { checkout_price_id: null },
    mintKey: async () => ({ key: 'tt_ok', expires_at: '2026-10-20T00:00:00+00:00' }),
    loadAll: async () => { calls.loadAll++ },
    upgradeNoticeFrom,
    // #4335: the cap-notice copy's upgrade-tail gate; the sandbox must provide
    // it because createKey passes it. `true` keeps the existing tail asserted
    // below (`/limit of 2 API keys/`).
    teamHasUpgrade: () => true,
    setCapNotice: (v) => calls.capNotice.push(v),
    setKeyModalCapNotice: (v) => calls.keyModalCapNotice.push(v),
    setError: (v) => calls.error.push(v),
    setBusy: () => {},
    setNewKey: (v) => calls.newKey.push(v),
    setKeyCopied: () => {},
    setKeyCopyFailed: () => {},
    setNewKeyExpiresAt: () => {},
    setNewKeyName: () => {},
    setNewKeyExpiryDate: () => {},
    ...overrides,
  }
  return { deps, calls, createKey: build(deps, ['async function createKey']).createKey }
}

test('#4330: a 402 mint returns falsy — the caller cannot advance to the reveal', async () => {
  const { deps, calls, createKey } = createEnv({
    mintKey: async () => {
      const e = new Error('Team api_keys limit reached (2). Upgrade your plan to increase it.')
      e.status = 402
      throw e
    },
  })
  const out = await createKey()
  assert.ok(!out, `a failed mint must return a falsy value, got ${JSON.stringify(out)}`)
  // The reveal state is never written — this is what left `null` on screen.
  assert.deepEqual(calls.newKey, [], 'a failed mint must never write the reveal plaintext')
  assert.equal(calls.loadAll, 0, 'a failed mint must not report a successful refresh')
  // …and the reason is the CAP notice (the tab-level banner is behind the modal).
  // (`createKey` opens by CLEARING both, so the meaningful value is the last write.)
  assert.equal(calls.capNotice.at(-1), calls.capNotice.filter(Boolean).at(-1),
    'a 402 must set a non-empty upgrade notice')
  assert.equal(calls.capNotice.filter(Boolean).length, 1, 'a 402 must set the upgrade notice once')
  assert.match(calls.capNotice.filter(Boolean)[0], /limit of 2 API keys/)
  assert.deepEqual(calls.error, ['', ''], 'the 402 path deliberately clears `error` in favour of capNotice')
  // The DIALOG's own slot carries the same message (and never another
  // surface's remedy — the rotate path's notice is a different string).
  assert.equal(calls.keyModalCapNotice.filter(Boolean).length, 1,
    'the create dialog must get its own cap notice')
  assert.equal(calls.keyModalCapNotice.filter(Boolean)[0], calls.capNotice.filter(Boolean)[0],
    'the dialog notice must be the create-path message, not a rotate remnant')
  void deps
})

test('#4330: any other mint failure returns falsy and surfaces its message as an error', async () => {
  const { calls, createKey } = createEnv({
    mintKey: async () => { const e = new Error('Unauthorized'); e.status = 401; throw e },
  })
  const out = await createKey()
  assert.ok(!out)
  assert.deepEqual(calls.newKey, [], 'a 401 must never write the reveal plaintext')
  assert.deepEqual(calls.error, ['', 'Unauthorized'], 'the leading empty write is the open-time clear')
  assert.deepEqual(calls.capNotice, [''], 'a non-cap failure must not claim a cap')
})

test('#4330: a 2xx mint carrying NO plaintext refuses the reveal (never an empty key box)', async () => {
  // The empty `.key-value` box was the owner's "square". Even a success-status
  // response with no key must not reach the reveal — the secret is
  // unrecoverable, so the only honest outcome is an error.
  const { calls, createKey } = createEnv({ mintKey: async () => ({ id: 'kid', key_prefix: 'tt_abc' }) })
  const out = await createKey()
  assert.ok(!out)
  assert.deepEqual(calls.newKey, [], 'a plaintext-less 2xx must never write a (falsy) reveal')
  assert.equal(calls.error.filter(Boolean).length, 1, 'it must explain why the key cannot be shown')
  assert.match(calls.error.filter(Boolean)[0], /cannot be shown/)
  assert.equal(calls.loadAll, 1, 'the row IS created, so the list must be refreshed')
})

test('#4330: a successful mint returns the plaintext and writes the reveal', async () => {
  const { calls, createKey } = createEnv()
  const out = await createKey()
  assert.equal(out, 'tt_ok')
  assert.deepEqual(calls.newKey, ['tt_ok'])
  assert.equal(calls.loadAll, 1, 'a successful mint refreshes the table (the new row appears)')
})

// ── copyNewKey ──────────────────────────────────────────────────────────────
function copyEnv(newKey, clipboard) {
  const written = []
  const flags = { copied: [], failed: [] }
  // The fallback branch SELECTS the key element for a manual copy. Stubbing
  // querySelector to null (the first cut) made that branch dead code, so the
  // whole manual-copy remedy could be deleted with the suite still green
  // (review P2). The stub now hands back a fake element and records the calls.
  const selected = { selectNodeContents: [], addRange: [], removeAllRanges: 0 }
  const fakeEl = { nodeType: 1, __keyValue: true }
  const deps = {
    newKey,
    navigator: { clipboard: { writeText: async (v) => { written.push(v); return clipboard.writeText(v) } } },
    document: {
      querySelector: (sel) => { assert.equal(sel, '.key-create-modal .key-value'); return fakeEl },
      createRange: () => ({ selectNodeContents: (el) => { selected.selectNodeContents.push(el) } }),
    },
    window: {
      getSelection: () => ({
        removeAllRanges: () => { selected.removeAllRanges++ },
        addRange: (r) => { selected.addRange.push(r) },
      }),
    },
    setWizardDurableKey: () => {},
    setKeyCopied: (v) => flags.copied.push(v),
    setKeyCopyFailed: (v) => flags.failed.push(v),
  }
  return { written, flags, selected, fakeEl, copyNewKey: build(deps, ['async function copyNewKey']).copyNewKey }
}

test('#4330: the clipboard is NEVER handed a non-string (writeText(null) copies "null")', async () => {
  // Reproduce the reported mechanism at the exact seam: the old reveal ran
  // `navigator.clipboard.writeText(newKey)` unconditionally, and `String(null)`
  // is "null".
  assert.equal(String(null), 'null', 'the coercion this bug rode on')
  for (const falsy of [null, undefined, '']) {
    const { written, copyNewKey } = copyEnv(falsy, { writeText: async () => {} })
    await copyNewKey()
    assert.deepEqual(written, [], `writeText must not be reached with ${JSON.stringify(falsy)}`)
  }
  // …and a real key is copied verbatim.
  const { written, flags, copyNewKey } = copyEnv('tt_live_key', { writeText: async () => {} })
  await copyNewKey()
  assert.deepEqual(written, ['tt_live_key'])
  assert.deepEqual(flags.copied, [true])
})

test('#4330: a refused clipboard keeps the key on screen and SELECTS it for a manual copy', async () => {
  const { written, flags, selected, fakeEl, copyNewKey } = copyEnv('tt_live_key', {
    writeText: async () => { throw new Error('clipboard blocked') },
  })
  await copyNewKey()
  assert.deepEqual(written, ['tt_live_key'], 'the write is attempted with the string')
  assert.deepEqual(flags.failed, [true], 'the manual-copy remedy must be surfaced')
  assert.deepEqual(flags.copied, [false], 'never claim a copy that did not happen')
  // The remedy itself: the shown-once key is SELECTED so ⌘/Ctrl-C works.
  assert.deepEqual(selected.selectNodeContents, [fakeEl],
    'the key element must be handed to selectNodeContents')
  assert.equal(selected.addRange.length, 1, 'the range must be installed as the selection')
  assert.equal(selected.removeAllRanges, 1, 'any stale selection is cleared first')
})

// ── dismissKeyModal ─────────────────────────────────────────────────────────
function dismissEnv(newKey, keyCopied, confirmReturn) {
  const closed = []
  const prompts = []
  const deps = {
    newKey,
    keyCopied,
    window: { confirm: (msg) => { prompts.push(msg); return confirmReturn } },
    setWizardDurableKey: () => {},
    setKeyModalOpen: (v) => closed.push(v),
    setNewKey: () => {},
    setNewKeyExpiresAt: () => {},
    setKeyCopied: () => {},
    setKeyCopyFailed: () => {},
    setKeyModalCapNotice: () => {},
  }
  return { closed, prompts, deps, confirmed: () => prompts.length, dismissKeyModal: build(deps, ['function dismissKeyModal']).dismissKeyModal }
}

test('#4330: Done without a copy cannot silently destroy a shown-once key', () => {
  const refused = dismissEnv('tt_live_key', false, false)
  refused.dismissKeyModal()
  assert.equal(refused.confirmed(), 1, 'the un-copied key must be confirmed away')
  assert.deepEqual(refused.closed, [], 'a declined confirm must keep the modal open')
  // Review cycle 4 (P2): the prompt must claim only "this dialog" — the same
  // function hands the plaintext to the connect step, so an unrecoverability
  // claim ("shown once", "cannot be shown again") would be FALSE here, and the
  // repo bans that wording on the connect surface for exactly this reason.
  assert.doesNotMatch(refused.prompts[0], /shown once|cannot be shown|never.*shown/i,
    'the dismiss prompt must not claim the key is unrecoverable')
  assert.match(refused.prompts[0], /dialog/i, 'it must name the scope of the claim (this dialog)')

  const accepted = dismissEnv('tt_live_key', false, true)
  accepted.dismissKeyModal()
  assert.deepEqual(accepted.closed, [false], 'a confirmed dismiss closes the modal')

  const alreadyCopied = dismissEnv('tt_live_key', true, false)
  alreadyCopied.dismissKeyModal()
  assert.equal(alreadyCopied.confirmed(), 0, 'a copied key needs no confirm')
  assert.deepEqual(alreadyCopied.closed, [false])

  const failedMint = dismissEnv(null, false, false)
  failedMint.dismissKeyModal()
  assert.equal(failedMint.confirmed(), 0, 'an empty reveal carries no secret to protect')
})

// ── static backstops for the parts execution cannot reach (JSX) ─────────────
test('#4330: every form→done transition is gated on the mint result', () => {
  const transitions = mainJsxCode.match(/setKeyModalStage\('done'\)/g) || []
  assert.equal(transitions.length, 2, 'the create-key modal has exactly two transitions to done')
  const guarded = mainJsxCode.match(/if \(mk\) setKeyModalStage\('done'\)/g) || []
  assert.equal(guarded.length, 2, 'both create-key transitions must be gated on createKey’s result')
})

// The create-key dialog's own JSX, from its open guard to the panel that
// follows it. Slicing is the only way to scope these pins: OTHER reveals in
// this file legitimately still use a fused "Copy & done" (the rotate
// replacement and the first-timer welcome reveal), so a whole-file count would
// be a false alarm. The slice starts at `{keyModalOpen && (` (unique in the
// file — pinned by wizardConnectTripwire) so the BACKDROP is included.
function createKeyModalJsx() {
  // The slice anchor must be UNIQUE: `indexOf` takes the first match, so a
  // second guard introduced earlier in the file would silently move the slice
  // and weaken every pin below it (#3428's lesson — a scan that can slip is not
  // a pin). `wizardConnectTripwire` pins the uniqueness of the
  // `setKeyModalOpen(true)` CALL SITE, which is a different string.
  assert.equal((mainJsxCode.match(/\{keyModalOpen && \(/g) || []).length, 1,
    'exactly one {keyModalOpen && ( guard may exist — the slice depends on it')
  const start = mainJsxCode.indexOf('{keyModalOpen && (')
  assert.ok(start > -1, 'main.jsx must render the create-key modal')
  const end = mainJsxCode.indexOf('accountMenuOpen &&', start)
  assert.ok(end > start, 'the create-key modal must be followed by the account menu')
  return mainJsxCode.slice(start, end)
}

test('#4330: the reveal renders iff a live non-empty key exists, and carries separate Copy + Done', () => {
  const modal = createKeyModalJsx()
  // ONE derivation gates the reveal, and the FORM is its else-branch — so no
  // state (including a team switch that nulls `newKey` mid-reveal) can render
  // a content-free dialog or an empty `.key-value` box.
  assert.match(mainJsxCode,
    /const newKeyReveal = \(keyModalStage === 'done' && typeof newKey === 'string' && newKey\) \|\| ''/,
    'the reveal key must be derived once from a non-empty string')
  assert.match(modal, /\{!newKeyReveal && \(/, 'the form must be the no-key branch')
  assert.match(modal, /\{newKeyReveal && \(/, 'the reveal must be the has-key branch')
  // The fused control is gone: copy and dismiss are two separate controls.
  assert.equal((modal.match(/Copy &amp; done/g) || []).length, 0,
    'the fused "Copy & done" control must not remain in the create-key modal')
  assert.match(modal, /onClick=\{copyNewKey\}/, 'the reveal must render its own Copy control')
  // Pinned literally: `onClick={dismissKeyModal}` alone would also be satisfied
  // by the form's Cancel button inside the same slice (review cycle 3, P2).
  assert.match(modal, /<button className="ghost" onClick=\{dismissKeyModal\}>Done<\/button>/,
    'the reveal must render its own Done control')
  // The raw state must never be handed to the clipboard, and the un-copied
  // dismiss guard must cover the REVEAL's most common dismiss path (backdrop).
  assert.equal((modal.match(/writeText\(newKey\)/g) || []).length, 0,
    'the raw newKey state must never be written to the clipboard')
  assert.match(modal, /onClick=\{\(\) => \{ if \(!keyModalBusy\) dismissKeyModal\(\) \}\}/,
    'the backdrop must route through the guarded dismiss')
})

test('#4330: the dialog cap notice cannot go stale — cleared on open, on every teardown, and on dismiss', () => {
  // Review cycle 2 (P2 ×2): the notice is team- and attempt-scoped data. A
  // reopen used to render the PREVIOUS attempt's advice with no mint
  // attempted, and `switchTeam` cleared the tab banner but not this sibling.
  //
  // The opener clears BEFORE queuing the modal, which also keeps the #2710
  // queue-site window (setKeyModalOpen(true) → "+ New key") intact.
  assert.match(mainJsxCode,
    /setKeyModalCapNotice\(''\); setKeyModalOpen\(true\)/,
    'the opener must clear the dialog notice before opening')
  // Team switch — the per-team rationale that already guards the tab banner.
  const switchTeamBody = extractDeclaration(mainJsx, 'async function switchTeam')
  assert.match(switchTeamBody, /setKeyModalCapNotice\(''\)/,
    'switchTeam must drop the previous team\'s dialog notice')
  // Dismiss (backdrop / Done / Cancel) clears it; the create path clears it on
  // entry and re-sets it on a 402.
  const dismissBody = extractDeclaration(mainJsx, 'function dismissKeyModal')
  assert.match(dismissBody, /setKeyModalCapNotice\(''\)/, 'dismiss must clear the dialog notice')
  // Every TEARDOWN, per this test's name — the logout clear is easy to drop.
  assert.match(extractDeclaration(mainJsx, 'async function logout'), /setKeyModalCapNotice\(''\)/,
    'logout must drop the dialog notice')
  const createBody = extractDeclaration(mainJsx, 'async function createKey')
  assert.match(createBody, /setKeyModalCapNotice\(''\)/, 'createKey must clear it on entry')
  assert.match(createBody, /setKeyModalCapNotice\(notice\)/, 'and set it from the 402 path')
  // Cancel is a close path: it must go through the guarded dismiss (no secret
  // exists in the form stage, so no confirm fires).
  assert.match(createKeyModalJsx(), /<button className="ghost" onClick=\{dismissKeyModal\} disabled=\{keyModalBusy\}>Cancel<\/button>/,
    'Cancel must route through dismissKeyModal')
})

test('#4330: the cap notice renders INSIDE the create-key modal from its OWN slot', () => {
  // Its own state, so the dialog can never show the rotate path's remedy.
  assert.match(createKeyModalJsx(), /<CapNotice text=\{keyModalCapNotice\}/,
    'the create-key modal must render the cap notice (a 402 must not be invisible behind the dialog)')
  assert.doesNotMatch(createKeyModalJsx(), /<CapNotice text=\{capNotice\}/,
    'the dialog must not read the shared tab notice (rotate-specific advice)')
})
