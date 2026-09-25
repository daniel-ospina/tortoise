// keyRotateEmptyRevealExec.test.js — #4342 (the #4330 defect class on the
// ROTATE surface), extended by #4355 (the single-call rotate primitive).
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
// #4355 changed the SHAPE the guard sits on. Rotate used to be two calls
// (`mintKey` then `revokeKey`) against the capped mint endpoint, which is why a
// team at `max_api_keys` could not rotate at all; it is now ONE call to
// `POST /v1/team/keys/{id}/rotate`, which creates the replacement and revokes
// the displaced row cap-neutrally. The plaintext guard, the refresh ordering,
// the stale-team guard, and the copy seam below are UNCHANGED in intent and
// re-pinned against the new call. What is new: the response's
// `replaced_revoked` / `warning` partial-state disclosure, and the single
// pinned URL/body shape.
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
// #4355: rotate is ONE call now (`POST /v1/team/keys/{id}/rotate`), so the
// harness stubs `api` instead of `mintKey`+`revokeKey`. `response` is the
// rotate 2xx body; `reject` is the error object `api` throws (its `.status`
// drives the 402 branch).
function rotateEnv({ response, reject, loadAll, orgIdRef, apiImpl, lifetimeDays } = {}) {
  const calls = {
    rotatedKey: [], rotateNotice: [], error: [], capNotice: [], loadAll: 0,
    api: [], order: [], welcomeKeys: [], wizardKeys: [],
  }
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
    sessionTokenRef: { current: 'jwt' },
    welcomeKey: '',
    wizardDurableKey: '',
    team: { tier: 'free' },
    _MS_PER_DAY: 86400000,
    // Pure helpers — irrelevant to the plaintext guard; stubs keep the file
    // free of their own dependencies.
    lifetimeDaysFromRow: () => (lifetimeDays === undefined ? null : lifetimeDays),
    fmtExpiryDate: (iso) => String(iso).slice(0, 10),
    keyRowDisclosure: () => 'residue row · tt_live_re · 2026-08-01',
    confirm: () => true,
    rotateCapNoticeFrom: (m) => `rotate cap: ${m}`,
    // #4335: the rotate notice takes the same hasUpgrade flag as the create
    // path, so the rotate handler now calls this. `{tier:'free'}` has a higher
    // tier available → true (the stub only has to be callable here; the
    // copy itself is stubbed above).
    teamHasUpgrade: () => true,
    api: async (url, opts) => {
      calls.order.push('rotate')
      calls.api.push([url, opts])
      if (apiImpl) return apiImpl(url, opts)
      if (reject) throw reject
      return response
    },
    // The stub RETURNS the rows the refresh landed, because #4355's ambiguous
    // failure branch decides its disclosure from the reloaded state (undefined
    // models a refresh that failed or went stale).
    loadAll: async () => { calls.loadAll++; return loadAll ? await loadAll(calls) : undefined },
    setCapNotice: (v) => calls.capNotice.push(v),
    setError: (v) => calls.error.push(v),
    setBusy: () => {},
    setRotatedKey: (v) => calls.rotatedKey.push(v),
    setRotateNotice: (v) => calls.rotateNotice.push(v),
    setWelcomeKey: (v) => calls.welcomeKeys.push(v),
    setWizardDurableKey: (v) => calls.wizardKeys.push(v),
  }
  return { deps, calls, regenerateKey: build(deps, ['function revealablePlaintext', 'function revealableMintPlaintext', 'async function regenerateKey']).regenerateKey }
}

test('#4342: a plaintext-less rotate 2xx latches NO reveal and states the old key is revoked', async () => {
  // The reported mechanism: a 2xx mint carrying only a row id — the secret is
  // unrecoverable. Pre-fix this reached `setRotatedKey({ plaintext: '', … })`.
  const { calls, regenerateKey } = rotateEnv({
    response: { id: 'k-new', key_prefix: 'tt_rot_new', replaced_revoked: true },
  })
  await regenerateKey('k-old')
  assert.deepEqual(calls.rotatedKey, [],
    'a plaintext-less mint must never latch `rotatedKey` (the empty `.key-value` box)')
  assert.deepEqual(calls.order, ['rotate'],
    'the single rotate call revokes the old key server-side — the message must say so')
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
  const { calls, regenerateKey } = rotateEnv({ response: undefined })
  await regenerateKey('k-old')
  assert.deepEqual(calls.rotatedKey, [], 'a missing response must not latch a reveal')
  assert.match(calls.error.filter(Boolean).at(-1) || '', /cannot be shown/)
})

test('#4342: a truthy-but-unrevealable mint (number / object / blank) is refused too', async () => {
  // A bare `!plaintext` check let these through: `mk.key = 42` and
  // `mk.key = '   '` are both NON-falsy, so the reveal rendered a value the
  // user could not use (a blank box, or a copied blank) with no error surfaced.
  for (const value of [42, {}, [], '   ', '\n\t', '\u200b', '\u3164', '\u2800', '\u00ad', '\u061c', '\u180e', '\ufe0f']) {
    const { calls, regenerateKey } = rotateEnv({ response: { key: value } })
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
  // rotate succeeded, the follow-up read did not) would replace the one
  // message that tells the user their old key is gone. The refusal must be
  // surfaced AFTER the refresh — pinned here behaviourally.
  const { calls, regenerateKey } = rotateEnv({
    response: { id: 'k-new', key_prefix: 'tt_rot_new' },
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
    response: { key: 'tt_ok', expires_at: '2026-10-20T00:00:00+00:00', replaced_revoked: true },
  })
  await regenerateKey('k-old')
  assert.deepEqual(calls.rotatedKey,
    [{ plaintext: 'tt_ok', expiresAt: '2026-10-20T00:00:00+00:00' }])
  assert.deepEqual(calls.error, [''], 'the open-time clear is the only error write')
  assert.deepEqual(calls.rotateNotice, [''],
    'a clean rotate clears any previous partial-state notice')
  assert.equal(calls.loadAll, 1)
})

test('#4342/#4359: a team switch during the rotate refusal refresh suppresses the refusal', async () => {
  // Mirrors the create-path guard (`createKey`): the refusal names the row and
  // the already-revoked old key, so a switch mid-refresh must not carry it
  // under the new team's header.
  const orgIdRef = { current: 'org-A' }
  const { calls, regenerateKey } = rotateEnv({
    orgIdRef,
    response: { id: 'k-new', key_prefix: 'tt_rot_new' },
    loadAll: async () => { orgIdRef.current = 'org-B' },
  })
  await regenerateKey('k-old')
  assert.deepEqual(calls.rotatedKey, [], 'still no reveal')
  assert.deepEqual(calls.error.filter(Boolean), [],
    'a refusal whose team changed mid-refresh must not be surfaced under the new team')
})

// ── #4355: the single call, the pin, and the partial-state disclosure ────

test('#4355: rotate is ONE pinned session call — never a mint-then-revoke pair', async () => {
  const { calls, regenerateKey } = rotateEnv({
    response: { key: 'tt_ok', replaced_revoked: true },
  })
  await regenerateKey('k-old')
  assert.equal(calls.api.length, 1, 'exactly one server call per rotate')
  const [url, opts] = calls.api[0]
  assert.equal(url, '/v1/team/keys/k-old/rotate?org_id=org-A',
    'the rotate route, pinned to the SELECTED team (#2230/#2167 rule 4)')
  assert.equal(opts.method, 'POST')
  assert.equal(opts.useSession, true, 'management write → session JWT')
  // The body must never carry a privilege class — the server 422s scopes/
  // graph_id, and the replacement inherits the displaced row's class.
  const body = JSON.parse(opts.body)
  assert.deepEqual(Object.keys(body).sort(), ['name'],
    'only the inherited label (no scopes/graph_id — the server owns the class)')
  assert.equal(body.scopes, undefined, 'the body must never set scopes')
  assert.equal(body.graph_id, undefined, 'the body must never set a graph binding')
})

test('#4355: the replacement body carries only the label + the lifetime span', async () => {
  // #2229/#2426 carry-over rides the SAME single call: the row label and the
  // re-applied lifetime span, and NOTHING that could set privilege class
  // (the server 422s scopes/graph_id and inherits both from the displaced row).
  const { calls, regenerateKey } = rotateEnv({
    response: { key: 'tt_ok', replaced_revoked: true },
    lifetimeDays: 30,
  })
  await regenerateKey('k-old')
  assert.deepEqual(JSON.parse(calls.api[0][1].body),
    { name: 'residue row', expires_in: 30 },
    'the label + lifetime span carry over; no scopes/graph_id is ever sent')
  const body = JSON.parse(calls.api[0][1].body)
  assert.equal(body.scopes, undefined, 'the body must never set scopes')
  assert.equal(body.graph_id, undefined, 'the body must never set a graph binding')
})

test('#4355: replaced_revoked:false surfaces the partial-state notice AND the live replacement', async () => {
  // The worst case is stated by the server, not swallowed: the replacement was
  // created, the displaced row could NOT be revoked, and the rollback failed —
  // BOTH keys are live. The response still carries the replacement's plaintext
  // (never lose a live secret), and the warning must reach the user as its own
  // persistent notice rather than riding the transient `error` slot.
  const warning = 'The replacement was created, but the previous key could not be revoked and the rollback failed — both are currently active.'
  const { calls, regenerateKey } = rotateEnv({
    response: { key: 'tt_ok', replaced_revoked: false, warning },
  })
  await regenerateKey('k-old')
  assert.deepEqual(calls.rotatedKey, [{ plaintext: 'tt_ok', expiresAt: null }],
    'the live replacement is still revealed — the alternative is losing a live secret')
  assert.deepEqual(calls.rotateNotice, [warning],
    'the partial state is disclosed, verbatim from the server')
  assert.deepEqual(calls.error, [''], 'it is NOT an error — the call succeeded')
})

// ── #4355: the AMBIGUOUS non-402 failure (lost/timed-out response) ───────
//
// The single rotate call creates the replacement AND revokes the displaced row
// server-side. A dropped or timed-out reply therefore leaves the client
// holding no plaintext for a live replacement it cannot know about, while the
// table still renders the old row active — a state the pre-#4355 two-call
// shape could not reach from a lost MINT response (the revoke was a separate
// call it never made). Reporting only `e.message` is therefore not merely
// unhelpful, it can be false. The catch must re-read the true state FIRST and
// disclose only what the table shows.

test('#4355: a lost non-402 rotate response re-reads state and discloses a COMPLETED rotate', async () => {
  // The reloaded row is revoked → the server DID complete the rotate: the
  // replacement exists and its value cannot be shown.
  const { calls, regenerateKey } = rotateEnv({
    reject: Object.assign(new Error('Network request failed'), { status: 0 }),
    loadAll: () => [{ id: 'k-old', key_id: 'k-old', revoked_at: '2026-09-21T00:00:00.000Z' }],
  })
  await regenerateKey('k-old')
  assert.equal(calls.loadAll, 1,
    'the true state must be re-read BEFORE the message is chosen')
  assert.deepEqual(calls.rotatedKey, [],
    'a lost response can never latch a reveal — it carried no plaintext')
  const msg = calls.error.filter(Boolean).at(-1) || ''
  assert.match(msg, /may have completed/,
    'the ambiguous failure must say the request may have completed')
  assert.match(msg, /shows as revoked/,
    'the reloaded state is the evidence, and it must be stated')
  assert.match(msg, /cannot be shown/,
    'a replacement exists whose value cannot be shown')
  assert.match(msg, /create a new key/, 'and name the remedy')
  assert.doesNotMatch(msg, /^Network request failed$/, 'never just the transport error')
})

test('#4355: a lost non-402 rotate response whose row is still live states the UNKNOWN outcome', async () => {
  // The reloaded row is still active → the rotate may not have run, but a
  // replacement may still exist. Claiming the row is gone would be a false
  // disclosure; claiming nothing would hide the ambiguity.
  const { calls, regenerateKey } = rotateEnv({
    reject: Object.assign(new Error('Gateway timeout'), { status: 504 }),
    loadAll: () => [{ id: 'k-old', key_id: 'k-old', revoked_at: null }],
  })
  await regenerateKey('k-old')
  assert.equal(calls.loadAll, 1)
  const msg = calls.error.filter(Boolean).at(-1) || ''
  assert.match(msg, /could not be confirmed/,
    'the outcome is unknown and must be presented as such')
  assert.match(msg, /still listed as active/,
    'the reloaded state is stated — the row is NOT reported revoked')
  assert.match(msg, /may have been created/,
    'a replacement may exist even though the row is still live')
  assert.match(msg, /Gateway timeout/, 'the underlying failure is still named')
  assert.doesNotMatch(msg, /shows as revoked/, 'never a false completed-rotate claim')
})

test('#4355: a refresh that cannot report state still discloses the ambiguity', async () => {
  // `loadAll` returning nothing (the refresh failed, or the team went stale)
  // must not collapse to `e.message`: the client still cannot know whether the
  // rotate ran. Fail toward the reader, not toward a false all-clear.
  const { calls, regenerateKey } = rotateEnv({
    reject: Object.assign(new Error('Network request failed'), { status: 0 }),
  })
  await regenerateKey('k-old')
  assert.equal(calls.loadAll, 1)
  const msg = calls.error.filter(Boolean).at(-1) || ''
  assert.match(msg, /could not be confirmed/)
  assert.doesNotMatch(msg, /shows as revoked/)
})

test('#4355: a team switch during the ambiguous-failure refresh suppresses the disclosure', async () => {
  // Same stale-response rule as the plaintext-less branch: the disclosure names
  // THIS team's row, so it must not land under the new team's header.
  const orgIdRef = { current: 'org-A' }
  const { calls, regenerateKey } = rotateEnv({
    orgIdRef,
    reject: Object.assign(new Error('Network request failed'), { status: 0 }),
    loadAll: async () => { orgIdRef.current = 'org-B' },
  })
  await regenerateKey('k-old')
  assert.equal(calls.loadAll, 1, 'the state is still re-read')
  assert.deepEqual(calls.error.filter(Boolean), [],
    'a disclosure whose team changed mid-refresh must not be surfaced')
  assert.deepEqual(calls.rotatedKey, [])
})

test('#4355: a rotate 402 is the OVER-cap case and uses the over-cap copy', async () => {
  const { calls, regenerateKey } = rotateEnv({
    reject: Object.assign(new Error('Team api_keys limit reached (2). Upgrade your plan to increase it.'),
      { status: 402 }),
  })
  await regenerateKey('k-old')
  const notice = calls.capNotice.at(-1) || ''
  assert.match(notice, /rotate cap: /, 'the 402 routes to the rotate-specific notice')
  assert.match(notice, /Team api_keys limit reached/)
  assert.deepEqual(calls.error, ['', ''], 'the cap notice owns the message slot (no raw error)')
})

test('#4355: rotating a row whose prefix we hold clears the held plaintext (row truth)', async () => {
  // #2246: the old path reached this clear through `revokeKey`; the single
  // /rotate call revokes server-side, so `regenerateKey` must do it itself or
  // the welcome/connect surfaces keep embedding a credential this rotate just
  // killed.
  const env = rotateEnv({ response: { key: 'tt_ok', replaced_revoked: true } })
  env.deps.welcomeKey = 'tt_live_re_abc'
  env.deps.wizardDurableKey = 'tt_other'
  const fn = build(env.deps,
    ['function revealablePlaintext', 'function revealableMintPlaintext', 'async function regenerateKey']).regenerateKey
  await fn('k-old')
  assert.deepEqual(env.calls.welcomeKeys, [''],
    'the welcome plaintext belonging to the rotated row is cleared')
  assert.deepEqual(env.calls.wizardKeys, [],
    'an unrelated held plaintext is untouched')
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
