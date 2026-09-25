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
  return { deps, calls, createKey: build(deps, ['function revealablePlaintext', 'function revealableMintPlaintext', 'async function createKey']).createKey }
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
  return { written, flags, selected, fakeEl, copyNewKey: build(deps, ['function revealablePlaintext', 'async function copyNewKey']).copyNewKey }
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
    window: { confirm: (msg) => { prompts.push(msg); return confirmReturn } },
    setWizardDurableKey: (v) => fed.push(v),
    setKeyModalOpen: (v) => closed.push(v),
    setNewKey: () => {},
    setNewKeyExpiresAt: () => {},
    setKeyCopied: () => {},
    setKeyCopyFailed: () => {},
    setKeyModalCapNotice: () => {},
  }
  return { closed, prompts, fed, deps, confirmed: () => prompts.length, dismissKeyModal: build(deps, ['function revealablePlaintext', 'function dismissKeyModal']).dismissKeyModal }
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
  return { deps, calls, wizardMintDurableKey: build(deps, ['function revealablePlaintext', 'function revealableMintPlaintext', 'async function wizardMintDurableKey']).wizardMintDurableKey }
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

test('#4359: every create/connect reveal seam reads the shared predicate', () => {
  // SCOPE — this asserts the DECLARED surface (the create/connect seams), not
  // the whole file. Same-class writers outside it are tracked in #4370.
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
  const { revealablePlaintext, revealableMintPlaintext } =
    build({}, ['function revealablePlaintext', 'function revealableMintPlaintext'])
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
