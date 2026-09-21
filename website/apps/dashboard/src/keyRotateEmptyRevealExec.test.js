// keyRotateEmptyRevealExec.test.js — #4342 (the #4330 defect class on the
// ROTATE surface).
//
// WHY THIS FILE EXISTS. `regenerateKey` mints the replacement, revokes the OLD
// key, and then latches the reveal with
//   setRotatedKey({ plaintext: (mk && (mk.key || mk.api_key)) || '', … })
// `setRotatedKey({…})` is unconditionally truthy, so a 2xx mint that carried NO
// plaintext still rendered the reveal (`{rotatedKey && (…)}`) as an empty
// `<code class="key-value">` box, and its copy ran `writeText('')` — a silent
// no-op that then cleared the only view of the live replacement. By then the
// old key was ALREADY revoked, so the user lost a working credential AND got a
// blank replacement. Both sibling mint sites refuse this response
// (`mintGraphKey` throws; #4330 hardened `createKey`); this is the one mint
// site with no guard.
//
// WHY EXECUTION, NOT A TEXT SCAN. The exit claim is a BEHAVIOUR — "a
// plaintext-less rotate never latches the reveal, and no falsy value reaches
// the clipboard". Source shapes are unbounded (`mk && setRotatedKey(…)`,
// `Boolean(mk) && …`, a helper that decides), and each would satisfy a regex
// while keeping the bug. So this file takes the REAL `regenerateKey` and
// `copyRotatedKey` out of `main.jsx`, builds them with `new Function(...)` over
// stub deps, and runs the actual paths. The text pins at the bottom stay only
// as cheap backstops for the DOM gate (a behaviour test cannot see JSX).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { stripComments } from './testSupport.js'

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

// `decl` is the full declaration prefix, e.g. 'async function regenerateKey'.
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

// ── regenerateKey ───────────────────────────────────────────────────────────
function rotateEnv({ mintKey, loadAll, orgIdRef } = {}) {
  const calls = { rotatedKey: [], error: [], capNotice: [], loadAll: 0, revoke: [], order: [] }
  const rows = [
    { id: 'k-old', key_id: 'k-old', name: 'residue row', key_prefix: 'tt_live_re',
      created_at: '2026-08-01T00:00:00.000Z' },
  ]
  const deps = {
    calls,
    busy: false,
    keys: rows,
    currentOrgId: 'org-A',
    orgIdRef: orgIdRef || { current: 'org-A' },
    team: { tier: 'free' },
    _MS_PER_DAY: 86400000,
    // Pure helpers — irrelevant to the plaintext guard; stubs keep the file
    // free of their own dependencies.
    lifetimeDaysFromRow: () => null,
    fmtExpiryDate: (iso) => String(iso).slice(0, 10),
    keyRowDisclosure: () => 'residue row · tt_live_re · 2026-08-01',
    confirm: () => true,
    rotateCapNoticeFrom: (m) => `rotate cap: ${m}`,
    mintKey: async (...args) => {
      calls.order.push('mint')
      return mintKey(...args)
    },
    revokeKey: async (id, opts) => {
      calls.order.push('delete')
      calls.revoke.push([id, opts])
    },
    loadAll: async () => { calls.loadAll++; if (loadAll) await loadAll(calls) },
    setCapNotice: (v) => calls.capNotice.push(v),
    setError: (v) => calls.error.push(v),
    setBusy: () => {},
    setRotatedKey: (v) => calls.rotatedKey.push(v),
  }
  return { deps, calls, regenerateKey: build(deps, ['function revealablePlaintext', 'function revealableMintPlaintext', 'async function regenerateKey']).regenerateKey }
}

test('#4342: a plaintext-less rotate 2xx latches NO reveal and states the old key is revoked', async () => {
  // The reported mechanism: a 2xx mint carrying only a row id — the secret is
  // unrecoverable. Pre-fix this reached `setRotatedKey({ plaintext: '', … })`.
  const { calls, regenerateKey } = rotateEnv({
    mintKey: async () => ({ id: 'k-new', key_prefix: 'tt_rot_new' }),
  })
  await regenerateKey('k-old')
  assert.deepEqual(calls.rotatedKey, [],
    'a plaintext-less mint must never latch `rotatedKey` (the empty `.key-value` box)')
  assert.deepEqual(calls.order, ['mint', 'delete'],
    'the old key IS revoked on this path — the message must say so')
  assert.equal(calls.loadAll, 1,
    'the replacement row exists server-side — the table must be refreshed')
  const msg = calls.error.filter(Boolean).at(-1) || ''
  assert.match(msg, /cannot be shown/, 'the failure must say the replacement cannot be shown')
  assert.match(msg, /already been revoked/,
    'and that the OLD key is already revoked (the rotate-specific remedy)')
  assert.match(msg, /create a new key/, 'and name the remedy')
  // Never an empty string latched, and never a non-string in the error slot.
  assert.ok(!calls.rotatedKey.some((v) => !v || !v.plaintext))
})

test('#4342: a mint returning nothing at all is refused the same way', async () => {
  const { calls, regenerateKey } = rotateEnv({ mintKey: async () => undefined })
  await regenerateKey('k-old')
  assert.deepEqual(calls.rotatedKey, [], 'a missing response must not latch a reveal')
  assert.match(calls.error.filter(Boolean).at(-1) || '', /cannot be shown/)
})

test('#4342: a truthy-but-unrevealable mint (number / object / blank) is refused too', async () => {
  // A bare `!plaintext` check let these through: `mk.key = 42` and
  // `mk.key = '   '` are both NON-falsy, so the reveal rendered a value the
  // user could not use (a blank box, or a copied blank) with no error surfaced.
  for (const value of [42, {}, [], '   ', '\n\t', '\u200b', '\u3164', '\u2800', '\u00ad', '\u061c', '\u180e', '\ufe0f']) {
    const { calls, regenerateKey } = rotateEnv({ mintKey: async () => ({ key: value }) })
    await regenerateKey('k-old')
    assert.deepEqual(calls.rotatedKey, [],
      `a mint carrying ${JSON.stringify(value)} must not latch the reveal`)
    const msg = calls.error.filter(Boolean).at(-1) || ''
    assert.match(msg, /cannot be shown/,
      `the failure must be surfaced for ${JSON.stringify(value)}`)
  }
})

test('#4342: a failing refresh cannot overwrite the already-revoked message', async () => {
  // `loadAll` owns the same `error` slot and overwrites it from its own catch
  // (main.jsx). If the refusal message were set FIRST, a compound failure (the
  // rotate legs succeeded, the follow-up read did not) would replace the one
  // message that tells the user their old key is gone. The refusal must be
  // surfaced AFTER the refresh — pinned here behaviourally.
  const { calls, regenerateKey } = rotateEnv({
    mintKey: async () => ({ id: 'k-new', key_prefix: 'tt_rot_new' }),
    loadAll: (c) => { c.error.push('Failed to fetch') },
  })
  await regenerateKey('k-old')
  assert.deepEqual(calls.rotatedKey, [], 'still no reveal')
  const msg = calls.error.filter(Boolean).at(-1) || ''
  assert.match(msg, /has already been revoked/,
    'the rotate truth must survive a refresh failure, not be replaced by the network error')
  assert.doesNotMatch(msg, /Failed to fetch/)
})

test('#4342: a successful rotate still latches the plaintext and its expiry echo', async () => {
  // Positive control: the guard must not swallow the working path (#2426 must
  // keep riding `rotatedKey`).
  const { calls, regenerateKey } = rotateEnv({
    mintKey: async () => ({ key: 'tt_ok', expires_at: '2026-10-20T00:00:00+00:00' }),
  })
  await regenerateKey('k-old')
  assert.deepEqual(calls.rotatedKey,
    [{ plaintext: 'tt_ok', expiresAt: '2026-10-20T00:00:00+00:00' }])
  assert.deepEqual(calls.error, [''], 'the open-time clear is the only error write')
  assert.equal(calls.loadAll, 1)
})

test('#4342/#4359: a team switch during the rotate refusal refresh suppresses the refusal', async () => {
  // Mirrors the create-path guard (`createKey`): the refusal names the row and
  // the already-revoked old key, so a switch mid-refresh must not carry it
  // under the new team's header.
  const orgIdRef = { current: 'org-A' }
  const { calls, regenerateKey } = rotateEnv({
    orgIdRef,
    mintKey: async () => ({ id: 'k-new', key_prefix: 'tt_rot_new' }),
    loadAll: async () => { orgIdRef.current = 'org-B' },
  })
  await regenerateKey('k-old')
  assert.deepEqual(calls.rotatedKey, [], 'still no reveal')
  assert.deepEqual(calls.error.filter(Boolean), [],
    'a refusal whose team changed mid-refresh must not be surfaced under the new team')
})

// ── copyRotatedKey ──────────────────────────────────────────────────────────
function copyEnv(rotatedKey, clipboard = {}) {
  const written = []
  const cleared = []
  // The fallback branch SELECTS the key element for a manual copy. Stubbing
  // querySelector to null would make that branch dead code, so the whole
  // manual-copy remedy could be deleted with the suite still green.
  const selected = { selectNodeContents: [], addRange: [], removeAllRanges: 0 }
  const fakeEl = { nodeType: 1, __keyValue: true }
  const deps = {
    rotatedKey,
    navigator: {
      clipboard: {
        writeText: async (v) => { written.push(v); return (clipboard.writeText || (async () => {}))(v) },
      },
    },
    document: {
      querySelector: (sel) => { assert.equal(sel, '.new-key .key-value'); return fakeEl },
      createRange: () => ({ selectNodeContents: (el) => { selected.selectNodeContents.push(el) } }),
    },
    window: {
      getSelection: () => ({
        removeAllRanges: () => { selected.removeAllRanges++ },
        addRange: (r) => { selected.addRange.push(r) },
      }),
    },
    setRotatedKey: (v) => cleared.push(v),
  }
  return { written, cleared, selected, fakeEl, copyRotatedKey: build(deps, ['function revealablePlaintext', 'async function copyRotatedKey']).copyRotatedKey }
}

test('#4342: the rotate copy NEVER hands a non-string/empty value to the clipboard', async () => {
  // `writeText('')` is the reported silent no-op (it resolves and then the old
  // handler cleared the reveal). Reproduce the coercion at the exact seam.
  assert.equal(String(''), '', 'the coercion this bug rode on: an empty writeText is a no-op')
  for (const bad of [null, undefined, {}, { plaintext: '' }, { plaintext: null },
    { plaintext: undefined }, { plaintext: 42 }, { plaintext: '   ' }, { plaintext: '\n\t' },
    { plaintext: '\u200b' }, { plaintext: '\u3164' }, { plaintext: '\u2800' },
    { plaintext: '\u061c' }, { plaintext: '\u00ad' }, { plaintext: '\ufe0f' }]) {
    const { written, cleared, copyRotatedKey } = copyEnv(bad)
    await copyRotatedKey()
    assert.deepEqual(written, [], `writeText must not be reached with ${JSON.stringify(bad)}`)
    assert.deepEqual(cleared, [], 'and the reveal must not be cleared')
  }
  const { written, cleared, copyRotatedKey } = copyEnv({ plaintext: 'tt_rot_new_x' })
  await copyRotatedKey()
  assert.deepEqual(written, ['tt_rot_new_x'], 'a real key is copied verbatim')
  assert.deepEqual(cleared, [null], 'the reveal clears only after the write resolves')
})

test('#4342: a refused clipboard keeps the rotate replacement visible and SELECTS it', async () => {
  const { written, cleared, selected, fakeEl, copyRotatedKey } = copyEnv(
    { plaintext: 'tt_rot_new_x' },
    { writeText: async () => { throw new Error('clipboard blocked') } },
  )
  await copyRotatedKey()
  assert.deepEqual(written, ['tt_rot_new_x'], 'the write is attempted with the string')
  assert.deepEqual(cleared, [],
    'a failed write must NOT clear the only copy of a live replacement (#2392 class)')
  assert.deepEqual(selected.selectNodeContents, [fakeEl],
    'the key element must be handed to selectNodeContents')
  assert.equal(selected.addRange.length, 1, 'the range must be installed as the selection')
  assert.equal(selected.removeAllRanges, 1, 'any stale selection is cleared first')
})

// ── static backstops for the parts execution cannot reach (JSX) ─────────────
test('#4342: the rotate reveal is gated on a derived non-empty string, and the raw state never hits the clipboard', () => {
  // ONE derivation gates the reveal, mirroring `newKeyReveal` — so a falsy
  // `rotatedKey.plaintext` can never render the empty `.key-value` box.
  assert.match(mainJsxCode,
    /const rotatedKeyReveal = \(rotatedKey && revealablePlaintext\(rotatedKey\.plaintext\)\) \|\| ''/,
    'the reveal key must be derived once from a non-empty, non-blank string')
  assert.match(mainJsxCode,
    /function revealablePlaintext\(value\) \{\s*return \(typeof value === 'string' && value\.replace\([^\n]*\)\.trim\(\)\) \? value : ''/,
    'the shared predicate must reject a blank (including zero-width/invisible) string, not just a falsy one')
  assert.match(mainJsxCode, /\{rotatedKeyReveal && \(/,
    'the JSX must gate on the derived string, not the truthy object')
  assert.equal((mainJsxCode.match(/\{rotatedKey && \(/g) || []).length, 0,
    'the raw-object gate must be gone — `{rotatedKey && …}` is truthy with an empty plaintext')
  assert.equal((mainJsxCode.match(/writeText\(rotatedKey\.plaintext\)/g) || []).length, 0,
    'the raw state must never be handed to the clipboard')
  assert.match(mainJsxCode, /onClick=\{copyRotatedKey\}/,
    'the reveal Copy must call the guarded `copyRotatedKey`')
})
