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
//
// #4359 (this file's second class). #4330 above closed the FALSY mint failure;
// a TRUTHY-but-unrevealable plaintext was still latched by three seams, because
// `!plaintext` and `typeof x === 'string'` both accept `42`, `{}`, `[]` and
// `'   '`. The same file now drives every create/connect reveal seam through
// the #4342 `revealablePlaintext` predicate (non-empty, non-blank STRING) and
// asserts the truthy-unrevealable shapes at each one: the create latch
// (`createKey`), the create render gate (`newKeyReveal`), the create copy
// (`copyNewKey`), the create dismiss/feed (`dismissKeyModal`), and the connect
// latch (`wizardMintDurableKey`).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { stripComments } from './testSupport.js'
// #4370 (A6): the REAL predicate, imported from its module-scope home in the
// pure module. `main.jsx` imports it too (it is no longer declared in the
// component), so this file hands the implementation to the sandbox instead of
// extracting a copy.
import { revealablePlaintext, revealableMintPlaintext } from './sessionKey.js'
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
    revealablePlaintext,
    revealableMintPlaintext,
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
  return { deps, calls, createKey: build(deps, ['function revealRefusalFor', 'async function createKey']).createKey }
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
  // #4639: the banner state now carries the STRUCTURED error object (so the
  // nudge gate can read its HTTP status), so pin the surfaced MESSAGE.
  assert.deepEqual(calls.error.map((e) => (e instanceof Error ? e.message : e)), ['', 'Unauthorized'],
    'the leading empty write is the open-time clear')
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

test('#4359: a truthy-but-unrevealable create mint is refused too (number / object / blank)', async () => {
  // The #4330 guard was a bare `!plaintext`: `mk.key = 42`, `{}`, `[]` and
  // `'   '` are all NON-falsy, so the modal advanced to 'done' with a
  // non-string (a silent mint — the key is live, never shown, and a second
  // click mints another) or with whitespace (a blank `.key-value` box and a
  // `writeText('   ')` plus a false "Copied ✓").
  for (const bad of [42, {}, [], '   ', '\n\t', '\u200b', '\u3164', '\u2800', '\u00ad', '\u061c', '\u180e', '\ufe0f', '\u200b\u2800 ']) {
    for (const field of ['key', 'api_key']) {
      const { calls, createKey } = createEnv({ mintKey: async () => ({ [field]: bad }) })
      const out = await createKey()
      assert.ok(!out, `a mint carrying ${field}=${JSON.stringify(bad)} must return falsy, got ${JSON.stringify(out)}`)
      assert.deepEqual(calls.newKey, [],
        `a mint carrying ${field}=${JSON.stringify(bad)} must never latch the reveal`)
      const msg = calls.error.filter(Boolean).at(-1) || ''
      assert.match(msg, /cannot be shown/, `the failure must be surfaced for ${field}=${JSON.stringify(bad)}`)
      assert.equal(calls.loadAll, 1, 'the row IS created server-side, so the list must be refreshed')
    }
  }
})

test('#4359: the reveal predicate accepts every legitimate key shape (prefix / api_key fallback)', async () => {
  // Positive control for the `prefix`/`mk.api_key` fallback the issue warns
  // about: a real `tt_`/`tk_` key must still pass, including via the API_KEY
  // leg and when the primary leg is an empty string.
  const { calls, createKey } = createEnv({ mintKey: async () => ({ key: 'tt_live_key_value' }) })
  assert.equal(await createKey(), 'tt_live_key_value')
  assert.deepEqual(calls.newKey, ['tt_live_key_value'])

  const fallback = createEnv({ mintKey: async () => ({ api_key: 'tk_durable_value' }) })
  assert.equal(await fallback.createKey(), 'tk_durable_value')
  assert.deepEqual(fallback.calls.newKey, ['tk_durable_value'])

  const emptyPrimary = createEnv({ mintKey: async () => ({ key: '', api_key: 'tt_from_api_key' }) })
  assert.equal(await emptyPrimary.createKey(), 'tt_from_api_key')
  assert.deepEqual(emptyPrimary.calls.newKey, ['tt_from_api_key'])
})

test('#4359: a failing refresh cannot overwrite the create refusal', async () => {
  // `loadAll` owns the same `error` slot and writes it from its own catch, so a
  // compound failure (the mint carried no revealable plaintext AND the follow-up
  // read failed) would REPLACE the one message that tells the user a live,
  // unrevoked key exists. The refusal is therefore surfaced AFTER the refresh —
  // the identical fix #4342 made on rotate.
  const { calls, createKey } = createEnv({
    mintKey: async () => ({ id: 'kid', key_prefix: 'tt_abc' }),
    loadAll: async () => { calls.loadAll++; calls.error.push('NetworkError: Failed to fetch') },
  })
  await createKey()
  const msg = calls.error.filter(Boolean).at(-1) || ''
  assert.match(msg, /cannot be shown/, 'the refusal must survive a failing refresh')
  assert.match(msg, /revoke any unlabeled key/, 'the remedy must survive too')
  assert.doesNotMatch(msg, /Failed to fetch/, 'a network error must not replace the create truth')
})

test('#4359: a revealable fallback is not shadowed by an unrevealable primary', async () => {
  // `mk.key || mk.api_key` selected the truthy primary first, so a malformed
  // `key` shadowed a valid `api_key` and the refusal claimed "the server did not
  // return the value" while a valid value was in hand.
  const mixed = createEnv({ mintKey: async () => ({ key: {}, api_key: 'tt_valid' }) })
  assert.equal(await mixed.createKey(), 'tt_valid')
  assert.deepEqual(mixed.calls.newKey, ['tt_valid'])
  assert.deepEqual(mixed.calls.error, [''], 'no refusal — a valid value existed')

  const blankPrimary = createEnv({ mintKey: async () => ({ key: '   ', api_key: 'tk_valid' }) })
  assert.equal(await blankPrimary.createKey(), 'tk_valid')

  const wizardMixed = wizardEnv({ mintKey: async () => ({ key: [], api_key: 'tk_connect' }) })
  await wizardMixed.wizardMintDurableKey()
  assert.deepEqual(wizardMixed.calls.wizardDurableKey, ['tk_connect'])
  assert.deepEqual(wizardMixed.calls.error, [''], 'no refusal on the connect latch either')
})

test('#4359: a team switch during the refusal refresh suppresses the refusal', async () => {
  // The refusal refreshes FIRST and then re-checks identity: its message names
  // THIS team's live, unrevoked key, so it must not land under the new team's
  // header after a switch mid-refresh.
  const orgIdRef = { current: 'org-A' }
  const { calls, createKey } = createEnv({
    orgIdRef,
    mintKey: async () => ({ id: 'kid', key_prefix: 'tt_abc' }),
    loadAll: async () => { calls.loadAll++; orgIdRef.current = 'org-B' },
  })
  const out = await createKey()
  assert.equal(out, null, 'a stale refusal still refuses (no reveal)')
  assert.deepEqual(calls.error.filter(Boolean), [],
    'a refusal whose team changed mid-refresh must not be surfaced under the new team')
  assert.equal(calls.loadAll, 1, 'the refresh still happened')
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
    revealablePlaintext,
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

test('#4359: a whitespace-only newKey is never copied and never claims "Copied ✓"', async () => {
  // Pre-fix: `typeof newKey === 'string' ? newKey : ''` let `'   '` through —
  // `writeText('   ')` RESOLVES (a clipboard holding whitespace), the connect
  // step was fed whitespace, and the handler set `keyCopied = true`: a false
  // "Copied ✓".
  const writes = []
  for (const blank of ['   ', '\n\t', ' \n ', '\u200b', '\u3164', '\u2800', '\u00ad', '\u061c', '\u180e', '\ufe0f']) {
    const { written, flags, copyNewKey } = copyEnv(blank, { writeText: async (v) => writes.push(v) })
    await copyNewKey()
    assert.deepEqual(written, [], `writeText must not be reached with ${JSON.stringify(blank)}`)
    assert.deepEqual(flags.copied, [], `never claim a copy of ${JSON.stringify(blank)}`)
    assert.deepEqual(flags.failed, [], 'a no-write is not a clipboard failure either')
  }
  assert.deepEqual(writes, [], 'the clipboard must stay untouched')
})

// ── dismissKeyModal ─────────────────────────────────────────────────────────
function dismissEnv(newKey, keyCopied, confirmReturn) {
  const closed = []
  const prompts = []
  const fed = []
  const deps = {
    newKey,
    keyCopied,
    revealablePlaintext,
    window: { confirm: (msg) => { prompts.push(msg); return confirmReturn } },
    setWizardDurableKey: (v) => fed.push(v),
    setKeyModalOpen: (v) => closed.push(v),
    setNewKey: () => {},
    setNewKeyExpiresAt: () => {},
    setKeyCopied: () => {},
    setKeyCopyFailed: () => {},
    setKeyModalCapNotice: () => {},
  }
  return { closed, prompts, fed, deps, confirmed: () => prompts.length, dismissKeyModal: build(deps, ['function dismissKeyModal']).dismissKeyModal }
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

test('#4359: a blank newKey is not a secret — no confirm, no connect-step feed', () => {
  for (const blank of ['   ', '\n\t', '', '\u200b', '\u2800', '\u061c', '\ufe0f']) {
    const env = dismissEnv(blank, false, false)
    env.dismissKeyModal()
    assert.equal(env.confirmed(), 0, `a blank reveal (${JSON.stringify(blank)}) carries no secret to protect`)
    assert.deepEqual(env.fed, [],
      `a blank (${JSON.stringify(blank)}) must not be fed to the connect step`)
    assert.deepEqual(env.closed, [false], 'and it closes without a prompt')
  }
  // Positive control: a real key IS fed (the #2735 contract this guard sits
  // beside) — without this the guard could be satisfied by never feeding at all.
  const real = dismissEnv('tt_live_key', true, false)
  real.dismissKeyModal()
  assert.deepEqual(real.fed, ['tt_live_key'], 'a copied real key is handed to the connect step')
})

// ── wizardMintDurableKey (the connect-step latch, #4359) ────────────────────
function wizardEnv(overrides = {}) {
  const calls = { wizardDurableKey: [], error: [], capped: [], paste: [], loadAll: 0 }
  const deps = {
    calls,
    revealablePlaintext,
    revealableMintPlaintext,
    wizardDurableBusy: false,
    setWizardDurableBusy: () => {},
    setWizardDurableError: (v) => calls.error.push(v),
    setWizardDurableCapped: (v) => calls.capped.push(v),
    setWizardShowPaste: (v) => calls.paste.push(v),
    setWizardDurableKey: (v) => calls.wizardDurableKey.push(v),
    currentOrgId: 'org-A',
    orgIdRef: { current: 'org-A' },
    welcomeTeamReady: false,
    welcomeOrgName: '',
    team: { org_name: 'Team A' },
    teams: [],
    keys: [],
    isBuildFork: false,
    // Pure naming helper — irrelevant to the plaintext guard; a stub keeps this
    // file free of its own dependencies.
    durableKeyName: () => 'key for Team A 2026-01-01 00:00 UTC',
    mintKey: async () => ({ key: 'tt_connect_ok' }),
    loadAll: async () => { calls.loadAll++ },
    ...overrides,
  }
  return { deps, calls, wizardMintDurableKey: build(deps, ['function revealRefusalFor', 'async function wizardMintDurableKey']).wizardMintDurableKey }
}

test('#4359: the connect mint latches only a revealable plaintext (truthy non-string / blank refused)', async () => {
  // Pre-fix this was `setWizardDurableKey((mk && (mk.key || mk.api_key)) || '')`
  // — no guard at all. `42` was stored verbatim, embedded in the connect
  // snippet, and read downstream by `.startsWith(...)`: the row-truth effect
  // throws `TypeError: …startsWith is not a function` as soon as the mint's own
  // refresh publishes a non-empty `keys`, and the revoke prefix-clear is a
  // second reader of the same state.
  for (const bad of [42, {}, [], '   ', '\n\t', '\u200b', '\u3164', '\u2800', '\u00ad', '\u061c', '\u180e', '\ufe0f']) {
    for (const field of ['key', 'api_key']) {
      const { calls, wizardMintDurableKey } = wizardEnv({
        mintKey: async () => ({ [field]: bad }),
      })
      // Must not reject — the refusal is a surfaced error, not a throw.
      await wizardMintDurableKey()
      assert.deepEqual(calls.wizardDurableKey, [],
        `a mint carrying ${field}=${JSON.stringify(bad)} must not be stored as the connect key`)
      const msg = calls.error.filter(Boolean).at(-1) || ''
      assert.match(msg, /cannot be shown/, `the failure must be surfaced for ${field}=${JSON.stringify(bad)}`)
      // The remedy must name the row the mint actually created: the connect
      // mint ALWAYS labels it (`durableKeyName`), so pointing at "an unlabeled
      // key" (the create-path wording) would be false here.
      assert.match(msg, /key for Team A 2026-01-01 00:00 UTC/,
        'the connect refusal must name the created row, not an unlabeled key')
      assert.doesNotMatch(msg, /unlabeled/,
        'the create-path "unlabeled key" remedy is false on the labeled connect mint')
      assert.equal(calls.loadAll, 1, 'the row IS created server-side, so the list must be refreshed')
    }
  }
  const missing = wizardEnv({ mintKey: async () => undefined })
  await missing.wizardMintDurableKey()
  assert.deepEqual(missing.calls.wizardDurableKey, [], 'a missing response must not latch a key')
  assert.match(missing.calls.error.filter(Boolean).at(-1) || '', /cannot be shown/)
})

test('#4359: the connect mint still latches every legitimate key shape', async () => {
  const keyed = wizardEnv({ mintKey: async () => ({ key: 'tt_new_connect_key' }) })
  await keyed.wizardMintDurableKey()
  assert.deepEqual(keyed.calls.wizardDurableKey, ['tt_new_connect_key'])
  assert.equal(keyed.calls.loadAll, 1)

  const fallback = wizardEnv({ mintKey: async () => ({ api_key: 'tk_durable_connect' }) })
  await fallback.wizardMintDurableKey()
  assert.deepEqual(fallback.calls.wizardDurableKey, ['tk_durable_connect'])
  assert.ok(fallback.calls.wizardDurableKey.every((v) => typeof v === 'string'),
    'the connect latch is always a string, so `startsWith` on it can never throw')
})

test('#4359: the connect refusal does not clear a plaintext already held (#2735 class)', async () => {
  // The refusal returns BEFORE the latch — it must not write '' either, which
  // would destroy a shown-once key a previous mint/paste put in memory.
  const { calls, wizardMintDurableKey } = wizardEnv({ mintKey: async () => ({ key: '   ' }) })
  await wizardMintDurableKey()
  assert.deepEqual(calls.wizardDurableKey, [],
    'no empty-string write may drop the previously-held plaintext')
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
  // a content-free dialog or an empty `.key-value` box. #4359: the derivation
  // reads the shared predicate, so a truthy non-string / whitespace-only value
  // is the form branch too.
  assert.match(mainJsxCode,
    /const newKeyReveal = \(keyModalStage === 'done' && revealablePlaintext\(newKey\)\) \|\| ''/,
    'the reveal key must be derived once from a non-blank string')
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

test('#4359/#4370: every create/connect reveal seam reads the shared predicate', () => {
  // SCOPE — #4359 declared the create/connect surface only. #4370 closed the
  // remaining seams and D6 makes the WHOLE writer set the assertion (below),
  // so this arm now pins the create/connect writers inside that larger set.
  const createBody = extractDeclaration(mainJsx, 'async function createKey')
  assert.match(createBody,
    /const plaintext = revealableMintPlaintext\(mk\)/,
    'createKey must latch through the shared predicate')
  assert.match(createBody, /if \(!plaintext\) \{/, 'createKey must refuse a non-revealable mint')

  const connectBody = extractDeclaration(mainJsx, 'async function wizardMintDurableKey')
  assert.match(connectBody,
    /const plaintext = revealableMintPlaintext\(mk\)/,
    'the connect latch must read the shared predicate, not a bare `|| \'\'`')
  assert.match(connectBody, /if \(!plaintext\) \{/,
    'the connect mint must refuse a non-revealable response instead of storing it')
  assert.equal((connectBody.match(/setWizardDurableKey\(\(mk && \(mk\.key \|\| mk\.api_key\)\) \|\| ''\)/g) || []).length, 0,
    'the unguarded connect latch shape must be gone')

  const copyBody = extractDeclaration(mainJsx, 'async function copyNewKey')
  assert.match(copyBody, /const plaintext = revealablePlaintext\(newKey\)/,
    'copyNewKey must read the same predicate as the gate')
  const dismissBody = extractDeclaration(mainJsx, 'function dismissKeyModal')
  assert.match(dismissBody, /const plaintext = revealablePlaintext\(newKey\)/,
    'dismissKeyModal must read the same predicate as the gate')

  // No create-reveal seam may keep the weaker `typeof x === 'string'` check
  // (which accepts whitespace) — the predicate replaced every one of them.
  assert.equal((mainJsxCode.match(/typeof newKey === 'string'/g) || []).length, 0,
    'no create-reveal seam may keep the weaker typeof check')
})

test('#4359: the shared predicate refuses every visually-blank shape and never mutates a real key', () => {
  // The single gateway, exercised directly. `trim()` alone is not a blankness
  // test: a mint carrying only a zero-width / invisible / visually-blank
  // character must not latch a reveal, render a blank `.key-value`, or set
  // `keyCopied`. The class is the Unicode FORMAT + DEFAULT-IGNORABLE properties
  // plus BRAILLE PATTERN BLANK (U+2800), which is in neither property.
  const blanks = ['', '   ', '\n\t', '\u200b', '\u3164', '\u2800', '\u00ad',
    '\u061c', '\u180e', '\u115f', '\u1160', '\uffa0', '\ufe0f', '\u200b\u2800 ']
  for (const blank of blanks) {
    assert.equal(revealablePlaintext(blank), '',
      `${JSON.stringify(blank)} must not be revealable`)
  }
  for (const bad of [null, undefined, 42, {}, [], false, NaN]) {
    assert.equal(revealablePlaintext(bad), '', `${JSON.stringify(bad)} is not a revealable string`)
  }
  // A real key passes VERBATIM — the decision uses the stripped copy, the
  // returned value is always the secret (never a mutated one).
  for (const key of ['tt_live_key', 'tk_durable', 'tt_abc-123_XYZ', '  tt_padded  ']) {
    assert.equal(revealablePlaintext(key), key, `${key} must pass verbatim`)
  }
  assert.equal(revealablePlaintext('tt_ok\u200b'), 'tt_ok\u200b',
    'a real key carrying an invisible char is returned verbatim, not stripped')

  // Leg composition: the `key` leg wins when revealable, and an UNREVEALABLE
  // primary falls through to `api_key` instead of shadowing it (and falsely
  // reporting "the server did not return the value").
  assert.equal(revealableMintPlaintext({ key: 'tt_a', api_key: 'tt_b' }), 'tt_a')
  for (const primary of [{}, [], '   ', 42, '\u2800']) {
    assert.equal(revealableMintPlaintext({ key: primary, api_key: 'tt_b' }), 'tt_b',
      `an unrevealable primary (${JSON.stringify(primary)}) must not shadow the fallback`)
  }
  for (const missing of [null, undefined, {}, { key: '' }]) {
    assert.equal(revealableMintPlaintext(missing), '', `${JSON.stringify(missing)} carries no plaintext`)
  }
})

// ════════════════════════════════════════════════════════════════════════════
// #4370 — the three REMAINING reveal/embed latches.
//
// #4342 (rotate) and #4359 (create/connect) routed their seams through the
// shared `revealablePlaintext` predicate. Three same-class latches were left
// outside that declared surface and are closed here, each driven over stub
// deps exactly like the seams above:
//
//   1. `createGraph`'s nested-envelope latch (`b.key_plaintext`) — a presence
//      check, so `42` / `{}` / `[]` / `'   '` mounted the modal on a blank
//      `.key-value` whose copy wrote whitespace and then dismissed, destroying
//      the only view of a live key.
//   2. `mintGraphKey`'s per-graph latch (`resp.key || resp.api_key`) — the same
//      object/number/blank shapes, now refused with the shared contract (D2)
//      instead of the divergent `Mint response did not include a key` throw.
//   3. the provisioned first-timer `welcomeKey` (`provisionInApp`'s envelope +
//      the `setWelcomeKey` write site) — a truthy non-string reached state, was
//      embedded in the connect snippet, and made the row-truth effect and the
//      revoke prefix-clear throw `TypeError: …startsWith is not a function`.

// ── createGraph (the C7 "graph created" envelope) ───────────────────────────
function createGraphEnv({ body, status = 201, orgIdRef, onPost } = {}) {
  const calls = { revealKey: [], error: [], busy: [], graphName: [], loadGraphs: 0, loadTeams: 0 }
  const ref = orgIdRef || { current: 'org-A' }
  const deps = {
    calls,
    currentOrgId: 'org-A',
    busy: false,
    newGraphName: 'g1',
    API_BASE: '/api',
    // The REAL predicate + the REAL refusal builder (never a stub — a stub
    // would verify the stub, not the product).
    revealablePlaintext,
    orgIdRef: ref,
    sessionTokenRef: { current: 'jwt' },
    revealRestoreRef: { current: null },
    rememberFocusedTrigger: () => {},
    withLimitContact: (s) => s,
    fetch: async () => { if (onPost) onPost(ref); return { ok: status < 400, status, json: async () => body } },
    loadGraphs: async () => { calls.loadGraphs++ },
    loadTeams: async () => { calls.loadTeams++ },
    setBusy: (v) => calls.busy.push(v),
    setError: (v) => calls.error.push(v),
    setNewGraphName: (v) => calls.graphName.push(v),
    setRevealKey: (v) => calls.revealKey.push(v),
  }
  return { ref, deps, calls, createGraph: build(deps, ['function revealRefusalFor', 'async function createGraph']).createGraph }
}

test('#4370: a truthy-but-unrevealable create-graph envelope never latches the reveal', async () => {
  // Pre-fix the guard was `if (b && b.key_plaintext)`: all four shapes are
  // TRUTHY (or a non-blank-shaped string), so `setRevealKey` was written with a
  // value the modal could not render or copy. The graph and its first key WERE
  // created, so the honest outcome is a surfaced refusal naming both facts.
  for (const bad of [42, {}, [], '   ', '\n\t', '\u200b', '\u2800']) {
    const { calls, createGraph } = createGraphEnv({ body: { graph: { name: 'G' }, key_plaintext: bad } })
    await createGraph()
    assert.deepEqual(calls.revealKey, [],
      `an envelope carrying key_plaintext=${JSON.stringify(bad)} must never latch the reveal`)
    const msg = calls.error.filter(Boolean).at(-1) || ''
    assert.match(msg, /cannot be shown/, `the refusal must be surfaced for ${JSON.stringify(bad)}`)
    assert.match(msg, /graph was created/, 'and must state that the graph itself WAS created')
    assert.equal(calls.loadGraphs, 1, 'the graph list is refreshed — the graph exists')
  }
})

test('#4370: the create-graph reveal still latches every legitimate envelope', async () => {
  // Positive control: without this the guard could be satisfied by never
  // latching at all.
  const real = createGraphEnv({ body: { graph: { name: 'G' }, key_plaintext: 'tt_graph_first' } })
  await real.createGraph()
  assert.deepEqual(real.calls.revealKey,
    [{ plaintext: 'tt_graph_first', title: 'Graph G created' }])
  assert.deepEqual(real.calls.error, [''], 'no refusal — a revealable plaintext existed')

  // No plaintext leg at all keeps the pre-#4370 behaviour (skip the reveal,
  // no refusal): the envelope legitimately omits it when nothing was minted.
  const absent = createGraphEnv({ body: { graph: { name: 'G' } } })
  await absent.createGraph()
  assert.deepEqual(absent.calls.revealKey, [])
  assert.deepEqual(absent.calls.error, [''])
})

test('#4370: a team switch during the create-graph POST cannot re-latch the previous team’s plaintext', async () => {
  // Pre-existing sibling of the same class: the latch ran BEFORE this handler's
  // own Round-16 identity guard, so a switch mid-POST (which clears `revealKey`)
  // was immediately undone by the stale continuation — the previous team's
  // plaintext mounted under the new team's header. `mintGraphKey` already
  // guarded its latch; this makes the two writers agree.
  const { calls, createGraph } = createGraphEnv({
    body: { graph: { name: 'G' }, key_plaintext: 'tt_stale' },
    onPost: (ref) => { ref.current = 'org-B' },
  })
  await createGraph()
  assert.deepEqual(calls.revealKey, [], 'a stale response must not latch a reveal under the new team')
  assert.deepEqual(calls.error, [''], 'and must not surface a refusal under the new team either')
})

// ── mintGraphKey (the per-graph key panel) ──────────────────────────────────
function panelMintEnv({ response, orgIdRef } = {}) {
  const calls = { revealKey: [], graphMsg: [], graphBusy: [], graphKeyName: [], refresh: 0, api: [] }
  const ref = orgIdRef || { current: 'org-A' }
  const deps = {
    calls,
    panelGraphId: 'g-1',
    graphBusy: false,
    currentOrgId: 'org-A',
    orgIdRef: ref,
    graphPanelReqRef: { current: 0 },
    graphKeyName: 'label',
    revealRestoreRef: { current: null },
    rememberFocusedTrigger: () => {},
    revealableMintPlaintext,
    graphMintBody: (gid, name) => ({ graph_id: gid, scopes: ['graph:read'], ...(name ? { name } : {}) }),
    api: async (url, opts) => {
      calls.api.push([url, opts])
      if (response instanceof Error) throw response
      return response
    },
    graphNameFor: () => 'G',
    refreshPanelAndCounts: async () => { calls.refresh++ },
    setGraphBusy: (v) => calls.graphBusy.push(v),
    setGraphMsg: (v) => calls.graphMsg.push(v),
    setGraphKeyName: (v) => calls.graphKeyName.push(v),
    setRevealKey: (v) => calls.revealKey.push(v),
  }
  return { ref, deps, calls, mintGraphKey: build(deps, ['function revealRefusalFor', 'async function mintGraphKey']).mintGraphKey }
}

test('#4370: a truthy-but-unrevealable panel mint never latches the reveal', async () => {
  for (const bad of [42, {}, [], '   ', '\n\t', '\u200b', '\u2800']) {
    for (const field of ['key', 'api_key']) {
      const { calls, mintGraphKey } = panelMintEnv({ response: { [field]: bad } })
      await mintGraphKey()
      assert.deepEqual(calls.revealKey, [],
        `a mint carrying ${field}=${JSON.stringify(bad)} must never latch the reveal`)
      const msg = calls.graphMsg.filter(Boolean).at(-1) || ''
      assert.match(msg, /cannot be shown/,
        `the refusal must use the shared contract for ${field}=${JSON.stringify(bad)} (D2)`)
      assert.equal(calls.refresh, 1,
        'the row IS created server-side, so the panel must be refreshed so it can be revoked')
    }
  }
})

test('#4370: the panel mint still latches every legitimate response', async () => {
  const key = panelMintEnv({ response: { key: 'tt_panel_new' } })
  await key.mintGraphKey()
  assert.deepEqual(key.calls.revealKey,
    [{ plaintext: 'tt_panel_new', title: 'Key for G created' }])
  assert.deepEqual(key.calls.graphMsg, [''])
  assert.equal(key.calls.refresh, 1)

  // A revealable `api_key` leg is still reachable, and an unrevealable primary
  // does not shadow it.
  const fallback = panelMintEnv({ response: { api_key: 'tt_panel_fallback' } })
  await fallback.mintGraphKey()
  assert.deepEqual(fallback.calls.revealKey,
    [{ plaintext: 'tt_panel_fallback', title: 'Key for G created' }])

  const shadowed = panelMintEnv({ response: { key: {}, api_key: 'tt_panel_shadowed' } })
  await shadowed.mintGraphKey()
  assert.deepEqual(shadowed.calls.revealKey,
    [{ plaintext: 'tt_panel_shadowed', title: 'Key for G created' }])
  assert.deepEqual(shadowed.calls.graphMsg, [''],
    'an unrevealable primary must not produce a refusal while a valid value was in hand')
})

test('#4370: a team switch during the panel mint suppresses both the reveal and the refusal', async () => {
  const { ref, calls, mintGraphKey } = panelMintEnv({
    response: { key: '   ' },
  })
  // The stub api() hands the response back; the switch has already happened.
  ref.current = 'org-B'
  await mintGraphKey()
  assert.deepEqual(calls.revealKey, [])
  assert.deepEqual(calls.graphMsg, [''], 'the refusal names THIS team’s key — it must not follow a switch')
  assert.equal(calls.refresh, 0, 'and no refresh runs for another team’s panel')
})

// ── provisionInApp + the welcomeKey write site ──────────────────────────────
function provisionInAppEnv({ body, ok = true, status = 201 } = {}) {
  const calls = { provisioning: [], provisionError: [] }
  const deps = {
    calls,
    revealablePlaintext,
    session: { user: { id: 'u1' } },
    readSession: async () => ({ kind: 'signed-in', user: { id: 'u1', email: 'a@b.c', displayName: 'A' } }),
    document: { cookie: '' },
    API_BASE: '/api',
    fetch: async () => ({ ok, status, json: async () => body }),
    window: { location: { search: '' } },
    bounceToAuth: () => {},
    oauthErrorHash: () => '',
    setWelcomeProvisioning: (v) => calls.provisioning.push(v),
    setWelcomeProvisionError: (v) => calls.provisionError.push(v),
  }
  return { deps, calls, provisionInApp: build(deps, ['async function provisionInApp']).provisionInApp }
}

test('#4370: the provision envelope hands over only a revealable plaintext (the org still resolves)', async () => {
  for (const bad of [42, {}, [], '   ', '\u200b']) {
    const { provisionInApp } = provisionInAppEnv({ body: { org_id: 'o-1', org_name: 'Org', api_key: bad } })
    const out = await provisionInApp({ user: { id: 'u1' } }, 'Org')
    assert.equal(out && out.api_key, '',
      `an envelope carrying api_key=${JSON.stringify(bad)} must resolve to '' (the connect step re-gates on the rows)`)
    assert.equal(out && out.org_name, 'Org', 'the org itself IS committed, so it must still resolve')
  }
  const real = provisionInAppEnv({ body: { org_id: 'o-1', org_name: 'Org', api_key: 'tt_provisioned' } })
  assert.equal((await real.provisionInApp({ user: { id: 'u1' } }, 'Org')).api_key, 'tt_provisioned')
})

function welcomeCreateOrgEnv({ provisioned }) {
  const calls = { welcomeKey: [], orgName: [], graphName: [], teamReady: [], step: [] }
  const deps = {
    calls,
    revealablePlaintext,
    wizardOrgBusy: false,
    wizardOrgName: 'Org',
    orgNameError: () => '',
    teams: [],
    sessionRef: { current: { user: { id: 'u1' } } },
    provisionInApp: async () => provisioned,
    setWizardOrgError: () => {},
    setWizardOrgName: (v) => calls.orgName.push(v),
    setWelcomeKey: (v) => calls.welcomeKey.push(v),
    setWelcomeOrgName: (v) => calls.orgName.push(v),
    setWelcomeGraphName: (v) => calls.graphName.push(v),
    setWelcomeTeamReady: (v) => calls.teamReady.push(v),
    setWizardStep: (v) => calls.step.push(v),
    setWizardOrgBusy: () => {},
    setWelcomeProvisioning: () => {},
    loadTeams: async () => {},
    refreshTeam: async () => ({ org_id: 'o-1' }),
    teamRefreshSeqRef: { current: 0 },
    loadAlerts: () => {},
  }
  return { deps, calls, handleWizardCreateOrg: build(deps, ['async function handleWizardCreateOrg']).handleWizardCreateOrg }
}

test('#4370: the welcomeKey write site stores only a revealable string (no later startsWith TypeError)', async () => {
  // The write site is the LAST line of defence: even if a producer handed over
  // a non-string, `welcomeKey` must never hold one — that state feeds
  // `snippetKey`, `durableConnectKey`'s `welcome.startsWith(...)` and the revoke
  // prefix-clear. An empty value is the deliberate fall-through to the rows
  // re-gate path (the connect step offers a rotate of the key just minted).
  // Every shape is awaited IN TURN (not `return`ed out of the loop): a `return`
  // here exited the test on the first shape, silently skipping `{}`/`[]`/blank
  // and making the positive control below dead code.
  for (const bad of [42, {}, [], '   ', '\u200b']) {
    const env = welcomeCreateOrgEnv({ provisioned: { api_key: bad, org_name: 'Org', graph_name: 'g' } })
    await env.handleWizardCreateOrg()
    assert.deepEqual(env.calls.welcomeKey, [''],
      `a producer handing api_key=${JSON.stringify(bad)} must not put it in welcomeKey`)
  }
  const real = welcomeCreateOrgEnv({ provisioned: { api_key: 'tt_welcome', org_name: 'Org', graph_name: 'g' } })
  await real.handleWizardCreateOrg()
  assert.deepEqual(real.calls.welcomeKey, ['tt_welcome'],
    'a valid provisioned key must still reach setWelcomeKey')
})

// ── the graph reveal modal's derived gate + guarded copy ────────────────────
function revealCopyEnv(revealKeyReveal, clipboard = { writeText: async () => {} }) {
  const written = []
  const closed = []
  const selected = { selectNodeContents: [], addRange: [], removeAllRanges: 0 }
  const fakeEl = { nodeType: 1, __keyValue: true }
  const deps = {
    revealKeyReveal,
    navigator: {
      clipboard: {
        writeText: async (v) => { written.push(v); return (clipboard.writeText || (async () => {}))(v) },
      },
    },
    document: {
      querySelector: (sel) => { assert.equal(sel, '.modal .key-value'); return fakeEl },
      createRange: () => ({ selectNodeContents: (el) => { selected.selectNodeContents.push(el) } }),
    },
    window: {
      getSelection: () => ({
        removeAllRanges: () => { selected.removeAllRanges++ },
        addRange: (r) => { selected.addRange.push(r) },
      }),
    },
    closeRevealKey: () => closed.push(true),
  }
  return { written, closed, selected, fakeEl, copyRevealKey: build(deps, ['async function copyRevealKey']).copyRevealKey }
}

test('#4370: the graph reveal Copy reads the derived gate and never writes a non-string', async () => {
  // The old inline handler ran `writeText(revealKey.plaintext)` unconditionally
  // and then dismissed: `writeText('   ')` resolves, so the only view of a live
  // key was destroyed by a silent no-op copy.
  for (const bad of ['', null, undefined, 0]) {
    const { written, closed, copyRevealKey } = revealCopyEnv(bad)
    await copyRevealKey()
    assert.deepEqual(written, [], `writeText must not be reached with ${JSON.stringify(bad)}`)
    assert.deepEqual(closed, [], 'and the modal must not be dismissed')
  }
  const real = revealCopyEnv('tt_graph_reveal')
  await real.copyRevealKey()
  assert.deepEqual(real.written, ['tt_graph_reveal'])
  assert.deepEqual(real.closed, [true], 'a successful copy is the #2392 contract — close only after it resolves')
  assert.equal(String({}), '[object Object]', 'the coercion the object shapes would have ridden')
})

test('#4370: a refused graph-reveal clipboard keeps the key on screen and SELECTS it', async () => {
  const env = revealCopyEnv('tt_graph_reveal', { writeText: async () => { throw new Error('blocked') } })
  await env.copyRevealKey()
  assert.deepEqual(env.written, ['tt_graph_reveal'], 'the write is attempted with the string')
  assert.deepEqual(env.closed, [],
    'a failed write must NOT dismiss the only view of a live key (#2735 class)')
  assert.deepEqual(env.selected.selectNodeContents, [env.fakeEl],
    'the key element must be handed to selectNodeContents')
  assert.equal(env.selected.addRange.length, 1)
  assert.equal(env.selected.removeAllRanges, 1)
})

test('#4370: the graph reveal modal is gated on the derived string, not the truthy object', () => {
  // ONE derivation gates the modal, mirroring `newKeyReveal` /
  // `rotatedKeyReveal`; `{revealKey && …}` was truthy for `{}`/`42`/`[]` and
  // rendered a blank `.key-value`.
  assert.match(mainJsxCode,
    /const revealKeyReveal = \(revealKey && revealablePlaintext\(revealKey\.plaintext\)\) \|\| ''/,
    'the graph reveal key must be derived once from a non-blank string')
  assert.match(mainJsxCode, /\{revealKeyReveal && \(/,
    'the JSX must gate on the derived string, not the truthy object')
  assert.equal((mainJsxCode.match(/\{revealKey && \(/g) || []).length, 0,
    'the raw-object gate must be gone')
  assert.equal((mainJsxCode.match(/writeText\(revealKey\.plaintext\)/g) || []).length, 0,
    'the raw state must never be handed to the clipboard')
  assert.match(mainJsxCode, /<code className="key-value">\{revealKeyReveal\}<\/code>/,
    'the modal must render the derived value it copies')
  assert.match(mainJsxCode, /onClick=\{copyRevealKey\}>Copy &amp; done<\/button>/,
    'the reveal Copy must call the guarded `copyRevealKey`')
})

// ── D6: the writer-set ratchet ──────────────────────────────────────────────
test('#4370 (D6): the plaintext-writer set is closed, and every member reads the shared predicate', () => {
  // #4359's completeness assertion was NAME-scoped (`typeof newKey === 'string'`
  // appeared nowhere), so a seam landing on a DIFFERENT plaintext state — or on
  // a bare-truthy spelling — was invisible. This is the whole-set version.
  //
  // (a) THE RATCHET: every store of reveal/embed plaintext that is NOT a clear
  // (`''` / null / false / undefined). A new seam changes this count and fails
  // here, so it must be classified by function below instead of landing
  // silently. Comment-stripped source, so a commented-out seam cannot satisfy it.
  // #4429: the rotate writer stores into the per-team map via a functional
  // updater (`setRotateOutcomes((prev) => ({ … plaintext … }))`), not the
  // single-slot `setRotatedKey({…})` this ratchet originally saw. The
  // block-bodied clear in `copyRotatedKey` (`(prev) => {`) and the `{}` reset
  // in `switchTeam` are NOT stores, so the alternation matches only the
  // object-literal store.
  const NON_CLEAR_STORE = /\bset(?:RevealKey|NewKey|RotatedKey|WizardDurableKey|WelcomeKey)\((?!\s*(?:''|""|null|false|undefined)\s*\))|setRotateOutcomes\(\(prev\) => \(\{/g
  const stores = mainJsxCode.match(NON_CLEAR_STORE) || []
  assert.equal(stores.length, 9,
    `the plaintext-writer set changed (${stores.length} non-clear stores). Classify the new/removed writer here and in this test's table:\n${stores.join('\n')}`)

  // (b) EVERY writer reads the predicate. The stored expression and the
  // predicate read are pinned per writer, by function — never by counting
  // `typeof` occurrences (name-scoping is what let the class land before).
  const WRITERS = [
    ['async function createKey', /setNewKey\(plaintext\)/, /const plaintext = revealableMintPlaintext\(mk\)/],
    ['async function wizardMintDurableKey', /setWizardDurableKey\(plaintext\)/, /const plaintext = revealableMintPlaintext\(mk\)/],
    ['async function copyNewKey', /setWizardDurableKey\(plaintext\)/, /const plaintext = revealablePlaintext\(newKey\)/],
    ['function dismissKeyModal', /if \(plaintext\) setWizardDurableKey\(plaintext\)/, /const plaintext = revealablePlaintext\(newKey\)/],
    ['async function regenerateKey', /setRotateOutcomes\(\(prev\) => \(\{/, /const plaintext = revealableMintPlaintext\(mk\)/],
    ['async function createGraph', /setRevealKey\(\{/, /const plaintext = revealablePlaintext\(b\.key_plaintext\)/],
    ['async function mintGraphKey', /setRevealKey\(\{ plaintext, title:/, /const plaintext = revealableMintPlaintext\(resp\)/],
    ['async function handleWizardCreateOrg', /setWelcomeKey\(revealablePlaintext\(provisioned\.api_key\)\)/, null],
    ['async function provisionInApp', /api_key: revealablePlaintext\(body\.api_key\)/, null],
  ]
  for (const [fn, store, predicate] of WRITERS) {
    const body = extractDeclaration(mainJsx, fn)
    assert.match(body, store, `${fn} must still store the plaintext into reveal/embed state`)
    if (predicate) {
      assert.match(body, predicate, `${fn} must derive that value through the shared predicate`)
    } else {
      assert.match(body, /revealablePlaintext\(/, `${fn} must read the shared predicate`)
    }
  }

  // The 9th store is the wizard's PASTE box, which is classified differently
  // and deliberately so: it is a USER-typed candidate, not a server envelope.
  // `pasted` is a trimmed string (`wizardDurablePaste.trim()`), prefix-checked
  // (`/^tt_/`) and row-verified (`durableConnectKey('', pasted, keys)` must not
  // resolve 'unknown'/'bootstrap'/'expiring'/'revoked'/'disabled') BEFORE it is
  // stored, so no non-string can reach it. Pin all three guards so a future edit
  // cannot drop them and keep this arm green.
  const pastedStart = mainJsxCode.indexOf('const pasted = wizardDurablePaste.trim()')
  const pastedEnd = mainJsxCode.indexOf('setWizardDurableKey(pasted)', pastedStart)
  assert.ok(pastedStart > -1 && pastedEnd > pastedStart,
    'the classified paste writer must still exist')
  const pasted = mainJsxCode.slice(pastedStart, pastedEnd + 'setWizardDurableKey(pasted)'.length)
  assert.match(pasted, /setWizardDurableKey\(pasted\)/,
    'the paste writer must still be the classified site')
  assert.match(pasted, /const pasted = wizardDurablePaste\.trim\(\)/,
    'the pasted value is a trimmed string')
  assert.match(pasted, /if \(!\/\^tt_\/\.test\(pasted\)\)/,
    'and it is prefix-checked before it is stored')
  assert.match(pasted, /durableConnectKey\('', pasted, keys\)/,
    'and row-verified (never embedded on unknown)')
})

test('#4370 (D2): the "cannot be shown" refusal is ONE contract across every reveal seam', () => {
  // The seams' remedies legitimately differ, so only the prefix is shared —
  // before this, `mintGraphKey` was the one seam with divergent wording, which
  // is why the class could not be asserted as a whole.
  const refusal = extractDeclaration(mainJsx, 'function revealRefusalFor')
  assert.match(refusal, /The server did not return the \$\{subject\}, so it cannot be shown\./,
    'the shared prefix must be the one authority')
  assert.equal((mainJsxCode.match(/The server did not return the /g) || []).length, 1,
    'no seam may re-inline the prefix (the builder is the only copy)')
  for (const fn of ['async function createKey', 'async function wizardMintDurableKey',
    'async function regenerateKey', 'async function mintGraphKey', 'async function createGraph']) {
    assert.match(extractDeclaration(mainJsx, fn), /revealRefusalFor\(/,
      `${fn} must surface its refusal through the shared contract`)
  }
  // The one seam that is deliberately NOT the "server did not return" shape: the
  // ambiguous rotate failure may not have completed, so its disclosure says so.
  assert.match(extractDeclaration(mainJsx, 'async function regenerateKey'),
    /The rotate request did not return a usable response/,
    'the ambiguous rotate disclosure keeps its own (legitimately different) sentence')
})
