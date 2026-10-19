// keyRotateEmptyRevealExec.test.js — #4342 (the #4330 defect class on the
// ROTATE surface), extended by #4355 (the single-call rotate primitive) and
// #4429 (the destructive response is never discarded).
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
// #8255 completes the retention for the catch's OTHER branch. #8249 fixed the
// plaintext-less 2xx by latching its disclosure on the per-team record BEFORE
// the team-identity guard; `regenerateKey`'s catch still began with that guard,
// so a switch across the RTT of a FAILED (non-402) rotate returned before the
// re-read and the user was told nothing — the same silent-destruction outcome on
// the branch that exists to disclose a lost response. The catch now latches the
// WEAKER honest form (no row truth is readable for a team you have left) on the
// record and renders it through the same team-scoped reader; the same-team path
// supersedes it with the richer row-truth message.
//
// #4429 supersedes the STORAGE shape of the outcome, not the guard. Because the
// rotate is destructive server-side, its response is the only delivery of the
// replacement's plaintext, so the outcome is now kept PER TEAM and latched
// BEFORE the stale-team guard: a team switch landing on the RTT must not destroy
// the secret along with the old key (`rotateOutcomeFor` is the one reader that
// scopes a retained outcome to the team that owns it). The `copyRotatedKey`
// cases below cover the matching clear semantics.
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
// #4370 (A6): the REAL predicate, imported from its module-scope home in the
// pure module — not a copy extracted out of main.jsx. `main.jsx` now IMPORTS it
// too, so a test that extracted a local declaration would fail loudly rather
// than silently stop guarding the seams.
import { revealablePlaintext, revealableMintPlaintext } from './sessionKey.js'
// #4721 (review P2): the banner's message normaliser, IMPORTED from its real
// home (upsellGate.js) rather than stubbed — a stub with different null
// semantics would let the composed banner be tested against the stub.
import { errorMessage } from './upsellGate.js'

const here = dirname(fileURLToPath(import.meta.url))
const mainJsx = readFileSync(join(here, 'main.jsx'), 'utf8')
const mainJsxCode = stripComments(mainJsx)
// #4370 (A6): the predicate's body now lives here, so the static pin reads it
// from its real home instead of a copy inside `main.jsx`.
const sessionKeyJs = readFileSync(join(here, 'sessionKey.js'), 'utf8')

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

// ── #8249: reading the latched disclosure the way the RENDER does ──────
// The plaintext-less refusal is not written to the `error` slot any more: it
// is latched on the per-team outcome record and read back through
// `rotateOutcomeFor` (the active team's record only). These helpers go through
// the REAL declarations so a test cannot pass by reading a shape the app never
// renders. #4370 (A6)/merge: `revealablePlaintext` lives at module scope in
// `sessionKey.js` now, so it is IMPORTED (line 53) rather than extracted from
// `main.jsx`; only `rotateOutcomeFor` is still declared there.
const { rotateOutcomeFor: _rotateOutcomeFor } =
  build({}, ['function rotateOutcomeFor'])

// The record the app would resolve for `orgId` after the last latch.
function recordOf(calls, orgId = 'org-A') {
  return _rotateOutcomeFor(calls.rotateOutcomes.at(-1) || {}, orgId) || {}
}

// The disclosure the app would render for `orgId` — '' means "dropped".
function refusalOf(calls, orgId = 'org-A') {
  return recordOf(calls, orgId).refusal || ''
}

// #8255: the fourth disclosure — the ambiguous-failure notice — read the same
// way the banner reads it: through the active team's record only.
function ambiguousOf(calls, orgId = 'org-A') {
  return recordOf(calls, orgId).ambiguous || ''
}

// #8255: the FIRST latch of the ambiguous notice — what a team switch would
// have retained. On the same-team path the fallback is latched and then
// superseded by the richer row-truth message, so `recordOf` (the last state)
// is legitimately empty there; this reads the state the switch would have seen.
function latchedAmbiguityOf(calls, orgId = 'org-A') {
  const first = calls.rotateOutcomes.at(0) || {}
  return (_rotateOutcomeFor(first, orgId) || {}).ambiguous || ''
}

// The reveal the app would render for `orgId` (the real derivation, over the
// real predicate) — a refusal record must always resolve to ''.
function derivedReveal(calls, orgId = 'org-A') {
  return revealablePlaintext(recordOf(calls, orgId).plaintext) || ''
}

// ── regenerateKey ───────────────────────────────────────────────────────────
// #4355: rotate is ONE call now (`POST /v1/team/keys/{id}/rotate`), so the
// harness stubs `api` instead of `mintKey`+`revokeKey`. `response` is the
// rotate 2xx body; `reject` is the error object `api` throws (its `.status`
// drives the 402 branch).
function rotateEnv({ response, reject, loadAll, orgIdRef, apiImpl, lifetimeDays, keys } = {}) {
  const calls = {
    rotateOutcomes: [], error: [], capNotice: [], loadAll: 0,
    api: [], order: [], welcomeKeys: [], wizardKeys: [],
    // The pre-#4429 store (`setRotatedKey`) is kept as a LEGACY PROBE: these two
    // deps are unused by the current implementation on purpose, so the #4429
    // regression tests can also be run against the pre-fix `main.jsx` and fail
    // for the SUBSTANTIVE reason (the outcome was dropped across the switch)
    // rather than for a missing identifier. The tests assert they stay unused.
    legacyRotatedKey: [], legacyRotateNotice: [],
  }
  const rows = keys || [
    { id: 'k-old', key_id: 'k-old', name: 'residue row', key_prefix: 'tt_live_re',
      created_at: '2026-08-01T00:00:00.000Z' },
  ]
  // #4429: the rotate outcome is ONE per-team record map. The stub models the
  // real state (applying the functional updater, not just pushing it), so the
  // merge semantics — a latch for another team must not wipe a retained
  // outcome — are exercised rather than assumed.
  let outcomesState = {}
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
    // #4370 (A6): the REAL shared predicate + refusal builder, imported/extracted
    // rather than stubbed — a stub here would verify the stub (the repo's
    // "real helper, not a copy" standard).
    revealablePlaintext,
    revealableMintPlaintext,
    // #8255: the ambiguous-failure latch composes its notice through the real
    // message normaliser (the same one `errorBannerText` uses), so a thrown
    // string / statusless object is worded exactly as the app words it.
    errorMessage,
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
    setRotatedKey: (v) => calls.legacyRotatedKey.push(v),
    setRotateNotice: (v) => calls.legacyRotateNotice.push(v),
    setRotateOutcomes: (v) => {
      const next = typeof v === 'function' ? v(outcomesState) : v
      // #8255: React BAILS OUT of a state update when the updater returns the
      // same reference — no re-render, no new state. The stub mirrors that, so a
      // guarded clear that finds nothing to clear (the `#8255` supersede hooks)
      // is not recorded as a write, exactly as it is not one in the app.
      if (next === outcomesState) return
      outcomesState = next
      calls.rotateOutcomes.push(outcomesState)
    },
    setWelcomeKey: (v) => calls.welcomeKeys.push(v),
    setWizardDurableKey: (v) => calls.wizardKeys.push(v),
  }
  return { deps, calls, regenerateKey: build(deps, ['function revealRefusalFor', 'function clearRotateAmbiguity', 'async function regenerateKey']).regenerateKey }
}

test('#4342/#8249: a plaintext-less rotate 2xx latches NO reveal and states the old key is revoked', async () => {
  // The reported mechanism: a 2xx mint carrying only a row id — the secret is
  // unrecoverable. Pre-fix this reached `setRotatedKey({ plaintext: '', … })`.
  // #8249: the disclosure it produces is latched on the PER-TEAM record (it
  // used to ride the transient `error` slot — the reason a team switch could
  // drop it).
  const { calls, regenerateKey } = rotateEnv({
    response: { id: 'k-new', key_prefix: 'tt_rot_new', replaced_revoked: true },
  })
  await regenerateKey('k-old')
  const rec = recordOf(calls)
  assert.equal(rec.plaintext, '',
    'a plaintext-less mint must never latch a plaintext (the empty `.key-value` box)')
  assert.equal(derivedReveal(calls), '',
    'and the derivation the JSX gates on must resolve empty — the reveal can never render')
  assert.deepEqual(calls.order, ['rotate'],
    'the single rotate call revokes the old key server-side — the message must say so')
  assert.equal(calls.loadAll, 1,
    'the replacement row exists server-side — the table must be refreshed')
  const msg = refusalOf(calls)
  assert.match(msg, /cannot be shown/, 'the failure must say the replacement cannot be shown')
  assert.match(msg, /already been revoked/,
    'and that the OLD key is already revoked (the rotate-specific remedy)')
  assert.match(msg, /create a new key/, 'and name the remedy')
  assert.deepEqual(calls.error.filter(Boolean), [],
    '#8249: the refusal is not a second, un-keyed copy in the `error` slot')
})

test('#4342: a mint returning nothing at all is refused the same way', async () => {
  const { calls, regenerateKey } = rotateEnv({ response: undefined })
  await regenerateKey('k-old')
  assert.equal(recordOf(calls).plaintext, '', 'a missing response must not latch a reveal')
  assert.match(refusalOf(calls), /cannot be shown/)
})

test('#4342: a truthy-but-unrevealable mint (number / object / blank) is refused too', async () => {
  // A bare `!plaintext` check let these through: `mk.key = 42` and
  // `mk.key = '   '` are both NON-falsy, so the reveal rendered a value the
  // user could not use (a blank box, or a copied blank) with no error surfaced.
  for (const value of [42, {}, [], '   ', '\n\t', '\u200b', '\u3164', '\u2800', '\u00ad', '\u061c', '\u180e', '\ufe0f']) {
    const { calls, regenerateKey } = rotateEnv({ response: { key: value } })
    await regenerateKey('k-old')
    assert.equal(recordOf(calls).plaintext, '',
      `a mint carrying ${JSON.stringify(value)} must not latch the reveal`)
    assert.equal(derivedReveal(calls), '',
      `the derived reveal must be empty for ${JSON.stringify(value)}`)
    assert.match(refusalOf(calls), /cannot be shown/,
      `the failure must be surfaced for ${JSON.stringify(value)}`)
  }
})

test('#8249: a plaintext-less response that also carries a warning retains BOTH disclosures', async () => {
  // The server's contract says `replaced_revoked:false` also ships the live
  // replacement's plaintext, so this shape should not occur — but if it ever
  // does, the branch must not drop the partial state on its way to recording
  // the lost secret. #8249's defect statement is explicit that the stale path
  // lost "no warning" too, so both live on the record.
  const warning = 'The previous key could not be revoked and the rollback failed — both are currently active.'
  const { calls, regenerateKey } = rotateEnv({
    response: { id: 'k-new', key_prefix: 'tt_rot_new', replaced_revoked: false, warning },
  })
  await regenerateKey('k-old')
  const rec = recordOf(calls)
  assert.equal(rec.warning, warning, 'the partial state is retained alongside the refusal')
  assert.match(rec.refusal || '', /cannot be shown/, 'and so is the lost-secret disclosure')
  assert.equal(rec.plaintext, '', 'still no reveal')
})

test('#4342/#8249: a failing refresh cannot overwrite the already-revoked message', async () => {
  // `loadAll` owns the `error` slot and overwrites it from its own catch
  // (main.jsx). The refusal used to be ordered AFTER the refresh on purpose —
  // if it were set first, a compound failure (the rotate succeeded, the
  // follow-up read did not) would replace the one message that tells the user
  // their old key is gone. #8249 removes that ordering dependency: the refusal
  // is latched on the team-scoped record BEFORE the refresh, so no refresh
  // outcome can reach it at all.
  const { calls, regenerateKey } = rotateEnv({
    response: { id: 'k-new', key_prefix: 'tt_rot_new' },
    loadAll: (c) => { c.error.push('Failed to fetch') },
  })
  await regenerateKey('k-old')
  assert.equal(derivedReveal(calls), '', 'still no reveal')
  const msg = refusalOf(calls)
  assert.match(msg, /has already been revoked/,
    'the rotate truth must survive a refresh failure, not be replaced by the network error')
  assert.doesNotMatch(msg, /Failed to fetch/)
  assert.deepEqual(calls.error.filter(Boolean), ['Failed to fetch'],
    'a refresh failure is its own disclosure — the refusal does not share the transient slot')
})

test('#8249 (review P2): the composed banner shows the refusal, not the refresh error', async () => {
  // The existing field checks above assert the record and the `error` slot
  // SEPARATELY — they pass while the user still sees `Failed to fetch`,
  // because the banner preferred `error`. This executes the banner's OWN
  // composed decision over the real `errorBannerText` helper: the compound
  // failure (rotate succeeded, follow-up `loadAll('')` failed) must render the
  // destroyed-key message. On the pre-fix main.jsx the helper does not exist,
  // so the extraction throws — the test cannot pass vacuously.
  //
  // #4721 (review P2): "outranks" is a PRECEDENCE, not a discard — the refusal
  // renders FIRST and the transient text after it, so no truth is dropped
  // (see the latched-refusal/409 case below).
  const { calls, regenerateKey } = rotateEnv({
    response: { id: 'k-new', key_prefix: 'tt_rot_new' },
    loadAll: (c) => { c.error.push('Failed to fetch') },
  })
  await regenerateKey('k-old')
  assert.deepEqual(calls.error.filter(Boolean), ['Failed to fetch'],
    'the refresh failure did land in the error slot (the precondition for the displacement)')
  const refusal = refusalOf(calls)
  const { errorBannerText } = build(
    { errorMessage },
    ['function errorBannerText'])
  const composed = errorBannerText('Failed to fetch', refusal)
  assert.ok(composed.startsWith(refusal),
    'the retained refusal OUTRANKS the refresh error — it renders FIRST, so it is never hidden')
  assert.match(composed, /Failed to fetch/,
    'and the transient text is appended rather than discarded — the refusal is a precedence, not a filter')
  assert.equal(errorBannerText('Failed to fetch', ''), 'Failed to fetch',
    'an absent refusal leaves the error slot authoritative — the three-branch banner is unchanged')
  assert.equal(errorBannerText(null, 'only a refusal'), 'only a refusal',
    'and a refusal with no error renders alone')
  assert.match(mainJsxCode, /\{errorBannerText\(error, rotateRefusalText, rotateAmbiguousText\)\}/,
    'the JSX banner must render the composed message, not `error ? errorMessage(error) : rotateRefusalText`')
})

test('#4721 (review P2): a LATCHED refusal does not swallow a newer definite 4xx detail', async () => {
  // The sequence the #8249 refusal's own remedy invites: it ends "Refresh the
  // list, revoke the unused replacement row, and create a new key". A user who
  // instead retries the rotate on the now-dead row gets the non-occupying 409 —
  // and until this fix `errorBannerText` returned the latched refusal and the
  // 409 detail (the one thing the definite-4xx branch exists to restore) never
  // reached the screen. Both are true, so both must render, refusal first.
  let call = 0
  const env = rotateEnv({
    // 1st: the plaintext-less 2xx that latches the #8249 refusal for org-A.
    // 2nd: the definite non-occupying 409 on the row that 1st rotate killed.
    apiImpl: async () => {
      call += 1
      if (call === 1) return { id: 'k-new', key_prefix: 'tt_rot_one' }
      throw Object.assign(new Error(ROTATE_409_NOT_OCCUPYING), { status: 409 })
    },
    loadAll: revokedReload,
  })
  await env.regenerateKey('k-old')
  const refusal = refusalOf(env.calls)
  assert.match(refusal, /has already been revoked/,
    'precondition: the plaintext-less rotate latched the destroyed-key refusal')
  await env.regenerateKey('k-old')
  const surfaced = env.calls.error.filter(Boolean).at(-1)
  assert.equal(surfaced, ROTATE_409_NOT_OCCUPYING,
    'the definite 409 detail is the LAST write to the transient slot')
  assert.equal(refusalOf(env.calls), refusal,
    'and the refusal is not cleared — a refusal can never erase the destruction record')
  const { errorBannerText } = build(
    { errorMessage },
    ['function errorBannerText'])
  const banner = errorBannerText(surfaced, refusal)
  assert.ok(banner.startsWith(refusal),
    'the retained destruction record still renders FIRST (#8249 precedence kept)')
  assert.match(banner, /rotate requires a live key/,
    'and the newer definite 4xx detail is REACHABLE in the banner (#4721)')
  // Non-vacuity: the pre-fix helper is `refusalText || errorMessage(error)`.
  const preFix = (e, r) => r || (typeof e === 'string' ? e : (e && e.message) || 'error')
  assert.doesNotMatch(preFix(surfaced, refusal), /rotate requires a live key/,
    'the pre-fix banner DROPS the detail this case is about')
})

test('#8249: a team switch during the rotate RTT retains the plaintext-less refusal', async () => {
  // THE DEFECT this file gained this case for. A 2xx with no revealable
  // plaintext means the displaced row is revoked and the replacement's secret
  // is unrecoverable, so the refusal is the ONLY thing that can tell the user.
  // It used to be written after the team-identity guard, so a switch landing
  // on the RTT dropped it: no reveal (there is none), no warning, no refusal.
  // The pre-#8249 assertion of this file pinned exactly that drop (`calls.error
  // .filter(Boolean)` empty, `rotateOutcomes` empty) — the latent bug.
  const orgIdRef = { current: 'org-A' }
  const { calls, regenerateKey } = rotateEnv({
    orgIdRef,
    apiImpl: async () => {
      // The switch lands DURING the RTT: by the time the response is in hand
      // the displaced row is already revoked server-side.
      orgIdRef.current = 'org-B'
      return { id: 'k-new', key_prefix: 'tt_rot_new', replaced_revoked: true }
    },
  })
  await regenerateKey('k-old')
  const rec = recordOf(calls, 'org-A')
  assert.match(rec.refusal || '', /has already been revoked/,
    '#8249: the refusal must SURVIVE the switch — it is the only notice that a key was destroyed')
  assert.match(rec.refusal || '', /cannot be shown/)
  assert.equal(rec.plaintext, '', 'and it is not a reveal: no plaintext was ever delivered')
  assert.equal(derivedReveal(calls, 'org-A'), '', 'a refusal record must never render a reveal')
  // AC2 — attribution: the retained refusal belongs to org-A and is not
  // retrievable through any other team's record.
  assert.equal(refusalOf(calls, 'org-B'), '', 'team B must NEVER render team A\u2019s refusal')
  assert.equal(refusalOf(calls, 'org-C'), '')
  assert.equal(calls.loadAll, 0,
    'a stale response must not refresh the NEW team\u2019s key list')
  assert.deepEqual(calls.error.filter(Boolean), [],
    'and the refusal must not be re-surfaced through the shared, un-keyed error slot')
  assert.deepEqual(calls.welcomeKeys, [],
    'nor may its row-truth prefix clear write into the new team\u2019s held plaintexts')
})

test('#8249: the refusal latch is a MERGE too — one team\u2019s lost secret cannot wipe another team\u2019s outcome', async () => {
  // Same rule as the plaintext latch (#4429): a retained outcome is the only
  // copy of a live credential, or the only notice that one was destroyed — a
  // refusal for one team must never come at the cost of another team\u2019s state.
  const { deps, calls, regenerateKey } = rotateEnv({
    response: { id: 'k-new', key_prefix: 'tt_rot_new' },
  })
  const heldForC = { plaintext: 'tt_held_c', expiresAt: null, warning: '' }
  deps.setRotateOutcomes({ 'org-C': heldForC })
  await regenerateKey('k-old')
  assert.deepEqual(calls.rotateOutcomes.at(-1), {
    'org-C': heldForC,
    'org-A': {
      plaintext: '', expiresAt: null, warning: '',
      refusal: calls.rotateOutcomes.at(-1)['org-A'].refusal,
    },
  }, 'the refusal latch must merge into the per-team map, never replace it')
  assert.equal(recordOf(calls, 'org-C').plaintext, 'tt_held_c',
    'team C\u2019s retained replacement is untouched')
})

test('#8249 (review P1): a plaintext-less rotate cannot wipe the SAME team\u2019s live unread replacement', async () => {
  // One record per team, and that record is ALSO the reveal's only home. A
  // plaintext-less rotate delivers NO secret, so writing `plaintext: ''` would
  // destroy a live, already-minted, not-yet-copied replacement from an earlier
  // successful rotate on this same team — the server is hash-only and can
  // never re-deliver it ("shown once"). The cross-team merge case above does
  // not cover this: the clobber is same-team, so it survived every test that
  // seeded only OTHER teams.
  const { deps, calls, regenerateKey } = rotateEnv({
    response: { id: 'k-new', key_prefix: 'tt_rot_new' },
  })
  const heldForA = {
    plaintext: 'tt_live_prior',
    expiresAt: '2026-10-20T00:00:00+00:00',
    warning: 'both keys are live',
  }
  deps.setRotateOutcomes({ 'org-A': heldForA })
  await regenerateKey('k-old')
  const rec = recordOf(calls, 'org-A')
  assert.equal(rec.plaintext, 'tt_live_prior',
    'the earlier live replacement must SURVIVE a later plaintext-less rotate on the same team')
  assert.equal(rec.expiresAt, '2026-10-20T00:00:00+00:00',
    'and its expiry echo rides with the carried-forward plaintext')
  assert.equal(rec.warning, 'both keys are live',
    'and a warning-less plaintext-less response does not drop the prior partial-state notice')
  assert.equal(derivedReveal(calls, 'org-A'), 'tt_live_prior',
    'so the reveal the user has still not copied is not silently destroyed')
  assert.match(rec.refusal || '', /cannot be shown/,
    'while the NEW lost-secret disclosure is still recorded')
})

// ── #4355: the single call, the pin, and the partial-state disclosure ────

test('#4342: a successful rotate still latches the plaintext and its expiry echo', async () => {
  // Positive control: the refusal latch must not swallow the working path
  // (#2426 must keep riding the outcome), and the two latches are mutually
  // exclusive — a reveal record carries no `refusal`.
  const { calls, regenerateKey } = rotateEnv({
    response: { key: 'tt_ok', expires_at: '2026-10-20T00:00:00+00:00', replaced_revoked: true },
  })
  await regenerateKey('k-old')
  assert.deepEqual(calls.rotateOutcomes,
    [{ 'org-A': { plaintext: 'tt_ok', expiresAt: '2026-10-20T00:00:00+00:00', warning: '' } }])
  assert.deepEqual(calls.error, [''], 'the open-time clear is the only error write')
  assert.equal(calls.loadAll, 1)
  assert.equal(derivedReveal(calls), 'tt_ok', 'the working reveal is still derived from the record')
  assert.equal(refusalOf(calls), '', 'a successful rotate latches no refusal')
})

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
  assert.deepEqual(calls.rotateOutcomes,
    [{ 'org-A': { plaintext: 'tt_ok', expiresAt: null, warning } }],
    'the live replacement is still revealed — the alternative is losing a live secret — and the warning rides the same record')
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
  // #8255: the switch-safe fallback is latched BEFORE the identity guard and
  // the re-read, with the weaker honest form (no row truth is known yet); the
  // same-team path then SUPERSEDES it with the richer row-truth message in the
  // transient slot, so exactly one disclosure reaches the screen.
  const fallback = calls.rotateOutcomes[0]['org-A']
  assert.match(fallback.ambiguous || '', /could not be confirmed/,
    '#8255: the ambiguous outcome is latched before the re-read')
  assert.match(fallback.ambiguous || '', /Network request failed/,
    'and the underlying failure is named in the retained fallback too')
  assert.doesNotMatch(fallback.ambiguous || '', /shows as revoked/,
    '#8255: the fallback may not claim a row truth it has not read')
  assert.equal(derivedReveal(calls), '',
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

test('#8255: a team switch during the ambiguous-failure RTT RETAINS the disclosure for the owning team', async () => {
  // THE DEFECT this case was re-pointed at. #4355's catch ran the team-identity
  // guard as its FIRST statement, so a switch landing on the RTT returned before
  // the re-read and the user was told NOTHING: `rotate` revokes the displaced row
  // server-side and delivers the replacement once, so an unanswered call is
  // exactly the lost-response state the branch exists to disclose. #8249 fixed
  // the sibling (the plaintext-less 2xx) by latching the disclosure on the team
  // that owns the row, BEFORE the guard, and reading it through the team-scoped
  // reader — this applies the same pattern to the catch.
  //
  // The pre-#8255 assertion of this test pinned the DROP (`calls.error` empty,
  // `calls.rotateOutcomes` empty) as intended behaviour, on the rationale #8249
  // superseded: "the disclosure names THIS team's row, so it must not land under
  // the new team's header". Team scoping is the RECORD KEY's job, not the
  // guard's — the guard may not decide WHETHER the user is told.
  //
  // Non-vacuity: restoring `if (orgIdRef.current !== _teamAtCall) return` as the
  // catch's first statement reddens the `ambiguousOf(calls, 'org-A')` assertion.
  const orgIdRef = { current: 'org-A' }
  const { calls, regenerateKey } = rotateEnv({
    orgIdRef,
    // The switch lands at the RTT itself, before the rejection reaches the catch.
    apiImpl: async () => {
      orgIdRef.current = 'org-B'
      throw Object.assign(new Error('Network request failed'), { status: 0 })
    },
  })
  await regenerateKey('k-old')
  const notice = ambiguousOf(calls, 'org-A')
  assert.match(notice, /could not be confirmed/,
    '#8255: the ambiguity must SURVIVE the switch \u2014 it is the only notice that a destructive rotate has an unknown outcome')
  assert.match(notice, /Network request failed/, 'and the failure that produced it is still named')
  assert.match(notice, /residue row/, 'as is the row the user has to re-check')
  assert.match(notice, /refresh the key list before relying on/, 'with a remedy the user can act on')
  assert.doesNotMatch(notice, /shows as revoked/,
    'the WEAKER honest form only \u2014 the row truth is unreadable for this team once the team has switched')
  assert.doesNotMatch(notice, /still listed as active/, 'never a row claim of either polarity')
  // AC2 \u2014 attribution: the retained notice belongs to org-A and is not
  // retrievable through any other team's record.
  assert.equal(ambiguousOf(calls, 'org-B'), '', 'team B must NEVER render team A\u2019s notice')
  assert.equal(ambiguousOf(calls, 'org-C'), '')
  assert.equal(ambiguousOf(calls, null), '', 'and no team selected renders nothing')
  assert.equal(calls.loadAll, 0,
    'a stale response must not refresh anyone\u2019s key list \u2014 `loadAll` pins `orgIdRef.current` at ITS call time, so after the switch it would read team B\u2019s rows')
  assert.deepEqual(calls.error.filter(Boolean), [],
    'and the notice must not ride the shared, un-keyed `error` slot')
  assert.equal(derivedReveal(calls, 'org-A'), '', 'no plaintext was ever delivered beyond the lost response')

  // Same defect, one step later: the switch lands DURING the re-read instead of
  // at the RTT. The notice must still be retained (it was latched BEFORE the
  // refresh), and the post-refresh guard must still stop every cross-team write.
  const ref2 = { current: 'org-A' }
  const env2 = rotateEnv({
    orgIdRef: ref2,
    reject: Object.assign(new Error('Gateway timeout'), { status: 504 }),
    loadAll: async () => {
      ref2.current = 'org-B'
      return [{ id: 'k-old', key_id: 'k-old', revoked_at: '2026-09-21T00:00:00.000Z' }]
    },
  })
  await env2.regenerateKey('k-old')
  assert.equal(env2.calls.loadAll, 1, 'the state is still re-read')
  assert.match(ambiguousOf(env2.calls, 'org-A'), /could not be confirmed/,
    '#8255: a switch across the REFRESH keeps the retained notice too')
  assert.doesNotMatch(ambiguousOf(env2.calls, 'org-A'), /shows as revoked/,
    'and the re-read row truth it can no longer attribute is NOT claimed')
  assert.deepEqual(env2.calls.error.filter(Boolean), [],
    'the reloaded row truth does not ride the shared, un-keyed slot under the new team\u2019s header')
})

// ── #8255: the ambiguous-failure disclosure across a team switch ──────────
//
// The catch's ambiguous branch is the rotate family's fourth disclosure and the
// last one with no retained home (#8249 gave the plaintext-less 2xx one). The
// cases below pin the retention, its team scoping, its merge semantics, and the
// supersede rule that keeps the same-team surface to ONE message.

test('#8255: the ambiguous latch MERGES — a live unread replacement survives it', async () => {
  // The record is also the reveal's only home, so a lost response must not blank
  // a live, not-yet-copied replacement minted by an earlier rotate on this same
  // team (the #8249 review-P1 rule, applied to the fourth disclosure). The
  // server is hash-only — a blanked plaintext is unrecoverable data loss.
  const orgIdRef = { current: 'org-A' }
  const env = rotateEnv({
    orgIdRef,
    apiImpl: async () => {
      orgIdRef.current = 'org-B'
      throw Object.assign(new Error('Network request failed'), { status: 0 })
    },
  })
  env.deps.setRotateOutcomes({
    'org-A': { plaintext: 'tt_live_prior', expiresAt: '2026-10-20T00:00:00+00:00', warning: 'both keys are live' },
  })
  await env.regenerateKey('k-old')
  const rec = recordOf(env.calls, 'org-A')
  assert.equal(rec.plaintext, 'tt_live_prior',
    'the earlier live replacement must SURVIVE the ambiguous latch')
  assert.equal(rec.expiresAt, '2026-10-20T00:00:00+00:00', 'and its expiry echo with it')
  assert.equal(rec.warning, 'both keys are live', 'and the prior partial-state notice')
  assert.match(rec.ambiguous || '', /could not be confirmed/,
    'while the NEW notice is added beside the retained outcome, not instead of it')
  assert.equal(derivedReveal(env.calls, 'org-A'), 'tt_live_prior',
    'so the reveal the user still has not copied is not destroyed by a failed rotate')
})

test('#8255: a same-team ambiguity renders ONE message — the retained fallback is superseded', async () => {
  // The fallback exists for the switch case. While the team is still ours the
  // re-read produces a strictly RICHER message (it names the row truth), so the
  // fallback must be dropped — otherwise the banner renders two variants of the
  // same notice and the user cannot tell whether one key or two are in doubt.
  const { calls, regenerateKey } = rotateEnv({
    reject: Object.assign(new Error('Network request failed'), { status: 0 }),
    loadAll: revokedReload,
  })
  await regenerateKey('k-old')
  assert.match(latchedAmbiguityOf(calls), /could not be confirmed/,
    'the fallback was latched before the re-read (so a switch there would keep it)')
  assert.equal(recordOf(calls, 'org-A').ambiguous, '',
    'and it is cleared once the row truth is in hand')
  const surfaced = calls.error.filter(Boolean).at(-1) || ''
  assert.match(surfaced, /shows as revoked/, 'the transient slot carries the STRONGER, row-truth form')
  const { errorBannerText } = build({ errorMessage }, ['function errorBannerText'])
  assert.equal(errorBannerText(surfaced, refusalOf(calls), recordOf(calls, 'org-A').ambiguous), surfaced,
    'the banner renders exactly that one message — no appended fallback variant')
})

test('#8255: a fresh rotate attempt supersedes a retained notice — for its own team only', async () => {
  // The retained notice's own remedy is "refresh the key list before relying on
  // this key". A new attempt performs exactly that, so leaving the old notice
  // latched would pair a stale "could not be confirmed" with the new outcome.
  // Two shapes, because the stale notice pairs differently with each:
  //  - a DEFINITE 4xx writes only the transient `error` slot, so nothing would
  //    drop a retained notice — the banner would show a stale ambiguity beside
  //    the server's precise detail (the clear runs AFTER that branch's refresh,
  //    which is the remedy the notice asked for — see the #8255 review-P2 cases
  //    below for the outcomes that resolve nothing and must NOT clear);
  //  - a success rewrites the record (which carries no `ambiguous` field), so
  //    the notice goes with it.
  // Retention is per team: another team's notice is not this attempt's to
  // discard. The seeded records carry their SUBJECT (`ambiguousKeyId`, written by
  // the latch) because the clear is row-scoped since the #8255 review P2.
  const stale = { plaintext: '', expiresAt: null, warning: '', ambiguous: 'outcome could not be confirmed', ambiguousKeyId: 'k-old' }
  const heldC = { plaintext: 'tt_held_c', expiresAt: null, warning: '', ambiguous: 'C team notice', ambiguousKeyId: 'k-c' }

  const refused = rotateEnv({ reject: Object.assign(new Error(ROTATE_409_NOT_OCCUPYING), { status: 409 }) })
  refused.deps.setRotateOutcomes({ 'org-A': stale, 'org-C': heldC })
  await refused.regenerateKey('k-old')
  assert.equal(ambiguousOf(refused.calls, 'org-A'), '',
    'the retry supersedes its own team\u2019s stale notice')
  assert.equal(refused.calls.error.filter(Boolean).at(-1), ROTATE_409_NOT_OCCUPYING,
    'so the fresh definite-4xx detail is the banner\u2019s only rotate message')
  assert.equal(ambiguousOf(refused.calls, 'org-C'), 'C team notice',
    'another team\u2019s retained notice is untouched')
  assert.equal(recordOf(refused.calls, 'org-C').plaintext, 'tt_held_c')

  const ok = rotateEnv({ response: { key: 'tt_ok', replaced_revoked: true } })
  ok.deps.setRotateOutcomes({ 'org-A': stale })
  await ok.regenerateKey('k-old')
  assert.equal(ambiguousOf(ok.calls, 'org-A'), '', 'a successful retry clears it too')
  assert.equal(recordOf(ok.calls, 'org-A').plaintext, 'tt_ok')
  assert.equal(derivedReveal(ok.calls, 'org-A'), 'tt_ok',
    'and the cleared notice does not cost the new replacement')
})

// ── #8255 (review P2): the supersede hook may not retire a notice it does not
// resolve ──────────────────────────────────────────────────────────────────
//
// The first cut of the supersede hook cleared the team's retained ambiguity at
// ATTEMPT START — before the request, before any outcome was known. The notice
// is ROW-scoped copy (`… refresh the key list before relying on <row>`) and it
// is the only record that a destructive call has an UNKNOWN outcome, so two
// attempt shapes discarded a disclosure nothing re-established:
//   - a 402 sets the cap notice and performs NO refresh;
//   - a definite 4xx that loses the team at its identity guard returns before
//     that branch's refresh too.
// And the team-scoped clear took the notice for key A the moment the user
// rotated key B. The three cases below are the pins; each reds on the pre-fix
// shape because the attempt-start clear fired unconditionally.

test('#8255 P2: a 402 retry does NOT retire a retained ambiguity — it resolves nothing', async () => {
  const stale = {
    plaintext: '', expiresAt: null, warning: '',
    ambiguous: 'The rotate request failed, and its outcome could not be confirmed — refresh the key list before relying on residue row.',
    ambiguousKeyId: 'k-old',
  }
  const env = rotateEnv({
    reject: Object.assign(new Error('Team api_keys limit reached (2).'), { status: 402 }),
  })
  env.deps.setRotateOutcomes({ 'org-A': stale })
  await env.regenerateKey('k-old')
  const { calls } = env
  assert.equal(calls.loadAll, 0, 'a 402 performs no refresh — the notice\u2019s remedy did NOT run')
  assert.match(ambiguousOf(calls, 'org-A'), /could not be confirmed/,
    'so the earlier unknown outcome must still be disclosed: dropping it would silence a rotate whose result was never confirmed')
  assert.match(calls.capNotice.at(-1) || '', /rotate cap: /, 'and the over-cap notice still renders beside it')
})

test('#8255 P2: a definite 4xx retry on ANOTHER row does not retire this row\u2019s notice', async () => {
  const stale = {
    plaintext: '', expiresAt: null, warning: '',
    ambiguous: 'The rotate request failed, and its outcome could not be confirmed — refresh the key list before relying on residue row.',
    ambiguousKeyId: 'k-old',
  }
  const rows = [
    { id: 'k-old', key_id: 'k-old', name: 'residue row', key_prefix: 'tt_live_re', created_at: '2026-08-01T00:00:00.000Z' },
    { id: 'k-other', key_id: 'k-other', name: 'agent key', key_prefix: 'tt_agent', created_at: '2026-08-02T00:00:00.000Z' },
  ]
  const env = rotateEnv({
    keys: rows,
    reject: Object.assign(new Error(ROTATE_409_NOT_OCCUPYING), { status: 409 }),
  })
  env.deps.setRotateOutcomes({ 'org-A': stale })
  await env.regenerateKey('k-other')
  assert.equal(env.calls.loadAll, 1, 'the 4xx branch did refresh the list')
  assert.match(ambiguousOf(env.calls, 'org-A'), /residue row/,
    'but the notice names residue row, and this attempt was about agent key \u2014 a refresh of the list is not a resolution of THAT row\u2019s unknown outcome')
  assert.equal(env.calls.error.filter(Boolean).at(-1), ROTATE_409_NOT_OCCUPYING,
    'the fresh refusal detail still renders beside it \u2014 the banner composes both (they answer different rows)')
})

// ── #8255 (review P2, second cut): the rule holds on the WRITE side too ────
//
// The notice names ONE row (`… before relying on <row>`), so the latch may not
// overwrite a retained notice that names a DIFFERENT, still-unresolved row. The
// first cut wrote `ambiguous`/`ambiguousKeyId` unconditionally, which reached the
// harm the row-scoped clear had just stopped — and needs no team switch: two
// lost responses on one team's two rows suffice. Pre-fix the case below reds at
// `ambiguousKeyId` (the record is re-pointed at `agent key`), and the
// team-switch variant reds at `/residue row/` (row A's unknown outcome is gone
// and no surface re-establishes it).

test('#8255 P2: a lost-response rotate on ANOTHER row does not OVERWRITE this row\u2019s notice', async () => {
  const stale = {
    plaintext: '', expiresAt: null, warning: '',
    ambiguous: 'The rotate request failed, and its outcome could not be confirmed \u2014 refresh the key list before relying on residue row.',
    ambiguousKeyId: 'k-old',
  }
  const rows = [
    { id: 'k-old', key_id: 'k-old', name: 'residue row', key_prefix: 'tt_live_re', created_at: '2026-08-01T00:00:00.000Z' },
    { id: 'k-other', key_id: 'k-other', name: 'agent key', key_prefix: 'tt_agent', created_at: '2026-08-02T00:00:00.000Z' },
  ]
  const env = rotateEnv({
    keys: rows,
    reject: Object.assign(new Error('Network request failed'), { status: 0 }),
  })
  env.deps.setRotateOutcomes({ 'org-A': stale })
  await env.regenerateKey('k-other')
  const { calls } = env
  assert.equal(recordOf(calls, 'org-A').ambiguousKeyId, 'k-old',
    'the retained notice still names the row it was written for \u2014 this attempt was about agent key')
  assert.match(ambiguousOf(calls, 'org-A'), /residue row/,
    'so the unresolved row keeps its disclosure: overwriting it would silently retire an unknown outcome nothing re-establishes')
  assert.doesNotMatch(ambiguousOf(calls, 'org-A'), /agent key/,
    'and the slot is not re-pointed at the row this attempt touched')
  assert.equal(calls.rotateOutcomes.filter((m) => m['org-A'] && m['org-A'].ambiguous).length, 1,
    'the latch BAILED OUT (React Object.is) rather than rewriting the record with the same notice under a new subject')
  // Nothing is lost on the same-team path: this attempt\u2019s own unknown outcome
  // is the transient row-truth message, and the banner composes the two.
  const surfaced = calls.error.filter(Boolean).at(-1) || ''
  assert.match(surfaced, /agent key/,
    'the newer attempt is still disclosed \u2014 through the transient slot, which is its correct home while the team is ours')
  assert.match(surfaced, /could not be confirmed/, 'and it carries the unknown-outcome wording')
  const { errorBannerText } = build({ errorMessage }, ['function errorBannerText'])
  const banner = errorBannerText(surfaced, refusalOf(calls), ambiguousOf(calls, 'org-A'))
  assert.match(banner, /residue row/, 'the banner renders BOTH \u2014 the retained notice')
  assert.match(banner, /agent key/, 'and the fresh one; they answer different rows')
})

test('#8255 P2: a team switch during that second attempt keeps the FIRST row\u2019s notice', async () => {
  // The documented single-slot trade-off: with the team gone, the newer attempt
  // has no honest place to be disclosed (the transient slot is un-keyed and must
  // not be written across teams), so the older unresolved notice is what
  // survives. Pinned so a later "fix" back to overwrite has to argue with it.
  const stale = {
    plaintext: '', expiresAt: null, warning: '',
    ambiguous: 'The rotate request failed, and its outcome could not be confirmed \u2014 refresh the key list before relying on residue row.',
    ambiguousKeyId: 'k-old',
  }
  const rows = [
    { id: 'k-old', key_id: 'k-old', name: 'residue row', key_prefix: 'tt_live_re', created_at: '2026-08-01T00:00:00.000Z' },
    { id: 'k-other', key_id: 'k-other', name: 'agent key', key_prefix: 'tt_agent', created_at: '2026-08-02T00:00:00.000Z' },
  ]
  const orgIdRef = { current: 'org-A' }
  const env = rotateEnv({
    keys: rows,
    orgIdRef,
    apiImpl: async () => {
      orgIdRef.current = 'org-B'
      throw Object.assign(new Error('Network request failed'), { status: 0 })
    },
  })
  env.deps.setRotateOutcomes({ 'org-A': stale })
  await env.regenerateKey('k-other')
  assert.match(ambiguousOf(env.calls, 'org-A'), /residue row/,
    'the older unresolved notice survives a switch across a later rotate on another row')
  assert.doesNotMatch(ambiguousOf(env.calls, 'org-A'), /agent key/)
  assert.equal(env.calls.loadAll, 0, 'and no refresh lands under the new team')
  assert.deepEqual(env.calls.error.filter(Boolean), [],
    'nor does the notice ride the un-keyed transient slot under the new team\u2019s header')
})

test('#8255 P2: a definite 4xx whose team switch lands at the RTT keeps the notice', async () => {
  const stale = {
    plaintext: '', expiresAt: null, warning: '',
    ambiguous: 'The rotate request failed, and its outcome could not be confirmed — refresh the key list before relying on residue row.',
    ambiguousKeyId: 'k-old',
  }
  const orgIdRef = { current: 'org-A' }
  const env = rotateEnv({
    orgIdRef,
    apiImpl: async () => {
      orgIdRef.current = 'org-B'
      throw Object.assign(new Error(ROTATE_409_NOT_OCCUPYING), { status: 409 })
    },
  })
  env.deps.setRotateOutcomes({ 'org-A': stale })
  await env.regenerateKey('k-old')
  assert.equal(env.calls.loadAll, 0,
    'the identity guard returns before the 4xx branch\u2019s refresh, so that branch resolves nothing')
  assert.match(ambiguousOf(env.calls, 'org-A'), /could not be confirmed/,
    'the notice therefore stays latched for its owning team \u2014 the attempt neither confirmed nor superseded it')
})

test('#8255: only a genuinely ambiguous failure latches — a definite 4xx / 402 does not', async () => {
  // #4721 split the branches by STATUS; #8255 must read the SAME split, or the
  // two would drift (a 409 latching an "outcome could not be confirmed" notice
  // is the #4721 lie, now retained across a switch).
  for (const status of [402, 400, 403, 409, 422]) {
    const { calls, regenerateKey } = rotateEnv({
      reject: Object.assign(new Error(`refused ${status}`), { status }),
    })
    await regenerateKey('k-old')
    assert.deepEqual(calls.rotateOutcomes.filter((m) => m['org-A'] && m['org-A'].ambiguous), [],
      `a ${status} reply is in hand — the server said what happened, so nothing is latched`)
  }
  for (const status of [0, 500, 503, undefined]) {
    const { calls, regenerateKey } = rotateEnv({
      reject: Object.assign(new Error(`lost ${status}`), { status }),
    })
    await regenerateKey('k-old')
    assert.match(latchedAmbiguityOf(calls), /could not be confirmed/,
      `a ${status} (no reply, or a server fault) may be a LOST RESPONSE — it must be retained`)
  }
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

// ── #4721: a DEFINITE refusal is not an ambiguous one ──────────────────────
//
// The disclosure above exists for failures where the client cannot know what
// the server did. Two rotate 409s are the opposite: the reply is in hand, and
// it says nothing was left behind — the lost-claim race ROLLED BACK the
// replacement, and the non-occupying refusal minted nothing at all. Both
// arrive with the target row `revoked_at` set (a concurrent revoke or rotate
// really did kill it), so the re-read the ambiguous branch performs finds a
// revoked row and reports a COMPLETED rotate — telling the user to revoke a
// replacement that does not exist. These cases pin the server's own detail.
const ROTATE_409_LOST_CLAIM =
  'API key is no longer active — it was revoked while this rotation ran; the replacement was rolled back. Retry or rotate another key.'
const ROTATE_409_NOT_OCCUPYING =
  'API key is not active — rotate requires a live key that occupies a key slot'
// The state that turns the ambiguous branch into the false completed-rotate
// claim: the target row is dead, so `after.revoked_at` is set.
const revokedReload = () => [
  { id: 'k-old', key_id: 'k-old', revoked_at: '2026-09-21T00:00:00.000Z' },
]
// Every disclosure the branch must NOT make about a rotate that minted
// nothing: no completion claim, no replacement to go revoke, no ambiguity.
function assertNoReplacementClaim(msg) {
  assert.doesNotMatch(msg, /may have completed/, 'the rotate provably did not complete')
  assert.doesNotMatch(msg, /replacement key exists/, 'no replacement exists — none was minted')
  assert.doesNotMatch(msg, /shows as revoked/, 'the old row is dead, but that is not evidence of a completed rotate')
  assert.doesNotMatch(msg, /could not be confirmed/, 'the server stated the outcome precisely')
  assert.doesNotMatch(msg, /Revoke the unused replacement/, 'nothing is left to revoke')
}

test('#4721: a rotate 409 (lost claim) surfaces the server detail, never a completed-rotate claim', async () => {
  // The row IS revoked on re-read (a concurrent rotate/revoke won the row), so
  // a branch that re-reads and reports "it may have completed" is describing
  // an outcome the server already denied, and naming a replacement this
  // request rolled back.
  const { calls, regenerateKey } = rotateEnv({
    reject: Object.assign(new Error(ROTATE_409_LOST_CLAIM), { status: 409 }),
    loadAll: revokedReload,
  })
  await regenerateKey('k-old')
  const surfaced = calls.error.filter(Boolean)
  assert.deepEqual(surfaced, [ROTATE_409_LOST_CLAIM],
    "a definite refusal keeps the server's own detail — it is the precise account of what happened")
  assert.equal(calls.loadAll, 1,
    'the table must show what the refusal describes — the target row IS revoked server-side')
  assert.deepEqual(calls.rotateOutcomes, [],
    'a refusal can never latch a reveal — no plaintext was delivered')
  assertNoReplacementClaim(surfaced.join('\n'))
})

test('#4721: a rotate 409 (non-occupying row) surfaces the server detail and mints nothing', async () => {
  // The other deterministic 409: the target was already revoked/expired/
  // bootstrap, so the cap never charged it and the server minted nothing.
  const { calls, regenerateKey } = rotateEnv({
    reject: Object.assign(new Error(ROTATE_409_NOT_OCCUPYING), { status: 409 }),
    loadAll: revokedReload,
  })
  await regenerateKey('k-old')
  const surfaced = calls.error.filter(Boolean)
  assert.deepEqual(surfaced, [ROTATE_409_NOT_OCCUPYING],
    "the server's refusal detail is the disclosure — that is the pre-#4355 behaviour this branch restores")
  assert.equal(calls.loadAll, 1,
    'the table still re-reads — a definite refusal says the row is not live, so the row must not render as live')
  assert.deepEqual(calls.rotateOutcomes, [],
    'and a refusal still latches no reveal — nothing was minted')
  assertNoReplacementClaim(surfaced.join('\n'))
})

test('#4721: the definite-4xx branch refreshes FIRST, so a failing refresh cannot clobber the server detail', async () => {
  // The rejected alternative was "no reload: the refresh owns the same `error`
  // slot and would overwrite the one message that says why the rotate was
  // refused". `loadAll` DOES own that slot on its own failure — which is why
  // the ORDER is the guarantee here: the refresh runs first and the server's
  // detail is written last, so it is the last word. Reverse the two statements
  // and this case fails on its final assertion (the network error wins), which
  // is exactly the regression the ordering protects against.
  const { calls, regenerateKey } = rotateEnv({
    reject: Object.assign(new Error(ROTATE_409_LOST_CLAIM), { status: 409 }),
    loadAll: (c) => { c.error.push('Failed to fetch') },
  })
  await regenerateKey('k-old')
  assert.equal(calls.loadAll, 1, 'the definite branch still refreshes the table')
  assert.deepEqual(calls.error.filter(Boolean), ['Failed to fetch', ROTATE_409_LOST_CLAIM],
    'the refresh lands first and the server detail is written after it — it must survive a refresh failure')
  assert.equal(calls.error.at(-1), ROTATE_409_LOST_CLAIM,
    'the server detail is the LAST write to the shared error slot')
})

test('#4721: the 409/5xx split is decided by the STATUS, not by the reloaded row', async () => {
  // Same server-side state, same refresh stub — only the status differs. A 4xx
  // is a reply in hand (the server decided); a 5xx is not (the request may have
  // been executed before the failure). This is the NON-VACUITY pair: collapse
  // the branch back to `setError(e.message)` for every non-402 and the 5xx half
  // fails; drop the definite branch and the 409 half fails.
  const definite = rotateEnv({
    reject: Object.assign(new Error(ROTATE_409_LOST_CLAIM), { status: 409 }),
    loadAll: revokedReload,
  })
  await definite.regenerateKey('k-old')
  const ambiguous = rotateEnv({
    reject: Object.assign(new Error('Bad gateway'), { status: 502 }),
    loadAll: revokedReload,
  })
  await ambiguous.regenerateKey('k-old')
  assert.deepEqual(definite.calls.error.filter(Boolean), [ROTATE_409_LOST_CLAIM],
    'a 4xx keeps the server detail verbatim')
  assert.equal(ambiguous.calls.loadAll, 1, 'a 5xx still re-reads the true state')
  const ambiguousMsg = ambiguous.calls.error.filter(Boolean).at(-1) || ''
  assert.match(ambiguousMsg, /may have completed/,
    'a 5xx may have completed server-side — the disclosure must stay')
  assert.match(ambiguousMsg, /cannot be shown/)
})

test('#4721: every definite 4xx refusal keeps the server detail (not only 409)', async () => {
  // The class, not the instance: the same rule must hold for a POLICY 403, a
  // 404, or a 422 — the server answered, so its message is the truth.
  for (const status of [400, 403, 404, 422, 429]) {
    const detail = `refused ${status}`
    const { calls, regenerateKey } = rotateEnv({
      reject: Object.assign(new Error(detail), { status }),
      loadAll: revokedReload,
    })
    await regenerateKey('k-old')
    assert.deepEqual(calls.error.filter(Boolean), [detail],
      `${status} must surface the server detail, not an inferred outcome`)
    assert.equal(calls.loadAll, 1,
      `${status} must still refresh the table — the message does not replace the truth`)
    assert.deepEqual(calls.rotateOutcomes, [], `${status}: no reveal`)
  }
})

test('#4355: rotating a row whose prefix we hold clears the held plaintext (row truth)', async () => {
  // #2246: the old path reached this clear through `revokeKey`; the single
  // /rotate call revokes server-side, so `regenerateKey` must do it itself or
  // the welcome/connect surfaces keep embedding a credential this rotate just
  // killed.
  const env = rotateEnv({ response: { key: 'tt_ok', replaced_revoked: true } })
  env.deps.welcomeKey = 'tt_live_re_abc'
  env.deps.wizardDurableKey = 'tt_other'
  const fn = build(env.deps, ['function revealRefusalFor', 'function clearRotateAmbiguity', 'async function regenerateKey']).regenerateKey
  await fn('k-old')
  assert.deepEqual(env.calls.welcomeKeys, [''],
    'the welcome plaintext belonging to the rotated row is cleared')
  assert.deepEqual(env.calls.wizardKeys, [],
    'an unrelated held plaintext is untouched')
})

// ── #4429: the destructive response is never discarded ─────────────────────
//
// `POST /v1/team/keys/{id}/rotate` mints the replacement AND revokes the
// displaced row in ONE server operation (#4355), so its response is the ONLY
// delivery of the replacement's plaintext (hash-only server-side, "shown
// once"). A team switch across that RTT used to hit the stale-response guard
// and drop the plaintext: the old key was revoked, its applications stopped
// working, and the replacement was unrecoverable — the user lost a working
// credential and got nothing. The outcome is now latched BEFORE the guard,
// tagged with the team that owns it, and the derive block's
// `rotateOutcomeFor` is the ONE reader that scopes it.

test('#4429: a team switch during the rotate RTT retains the replacement', async () => {
  const orgIdRef = { current: 'org-A' }
  const response = {
    key: 'tt_rot_kept', expires_at: '2026-10-20T00:00:00+00:00', replaced_revoked: true,
  }
  const { calls, regenerateKey } = rotateEnv({
    orgIdRef,
    // The switch lands DURING the RTT: by the time the response is in hand the
    // displaced row is already revoked server-side.
    apiImpl: async () => { orgIdRef.current = 'org-B'; return response },
  })
  await regenerateKey('k-old')
  assert.deepEqual(calls.rotateOutcomes.at(-1), {
    'org-A': { plaintext: 'tt_rot_kept', expiresAt: '2026-10-20T00:00:00+00:00', warning: '' },
  }, '#4429: the replacement must SURVIVE the switch — it is the only copy of a live secret')
  assert.deepEqual(calls.error.filter(Boolean), [],
    'and the outcome must not be re-surfaced in the shared error slot on the new team')
  assert.equal(calls.loadAll, 0,
    '#4429: a stale response must not refresh the NEW team\u2019s key list')
  assert.deepEqual(calls.welcomeKeys, [],
    '#4429: nor may its row-truth prefix clear write into the new team\u2019s held plaintexts')
  assert.deepEqual(calls.legacyRotatedKey, [],
    '#4429: the single-slot `setRotatedKey` store must be gone — the outcome is per team now')
})

test('#4429: a partial-state warning is retained with its team, not dropped by the switch', async () => {
  const orgIdRef = { current: 'org-A' }
  const warning = 'The replacement was created, but the previous key could not be revoked and the rollback failed — both are currently active.'
  const { calls, regenerateKey } = rotateEnv({
    orgIdRef,
    apiImpl: async () => {
      orgIdRef.current = 'org-B'
      return { key: 'tt_ok', replaced_revoked: false, warning }
    },
  })
  await regenerateKey('k-old')
  assert.deepEqual(calls.rotateOutcomes.at(-1),
    { 'org-A': { plaintext: 'tt_ok', expiresAt: null, warning } },
    '#4429: BOTH keys are live — the user must still be told, so the warning rides the retained outcome')
})

test('#4429: the latch is a MERGE — one team\u2019s rotate cannot wipe another team\u2019s outcome', async () => {
  // A retained outcome is the only copy of a live credential, so a later
  // rotate on a DIFFERENT team must not destroy it (the shape this would have
  // taken with a single slot: rotate A mid-switch, switch, rotate B → A\u2019s
  // secret gone).
  const { deps, calls, regenerateKey } = rotateEnv({
    response: { key: 'tt_ok', replaced_revoked: true },
  })
  const heldForC = { plaintext: 'tt_held_c', expiresAt: null, warning: '' }
  deps.setRotateOutcomes({ 'org-C': heldForC })
  await regenerateKey('k-old')
  assert.deepEqual(calls.rotateOutcomes.at(-1), {
    'org-C': heldForC,
    'org-A': { plaintext: 'tt_ok', expiresAt: null, warning: '' },
  }, 'the latch must merge into the per-team map, never replace it')
})

test('#4429: the two outcome readers never surface another team\u2019s record', () => {
  // The scoping rule EXECUTED, over the real declarations: `rotateOutcomeFor`
  // resolves the active team\u2019s record, and the reveal/notice derivations
  // (main.jsx\u2019s, reproduced verbatim) read only through it.
  const rec = { plaintext: 'tt_rot_kept', expiresAt: '2026-10-20T00:00:00+00:00', warning: 'both keys are live' }
  const outcomes = { 'org-A': rec }
  // #4370 (A6): `revealablePlaintext` is imported at module scope now, so only
  // `rotateOutcomeFor` is extracted from `main.jsx`.
  const { rotateOutcomeFor } = build({}, ['function rotateOutcomeFor'])
  const revealOf = (orgId) => {
    const o = rotateOutcomeFor(outcomes, orgId)
    return (o && revealablePlaintext(o.plaintext)) || ''
  }
  const noticeOf = (orgId) => {
    const o = rotateOutcomeFor(outcomes, orgId)
    return (o && o.warning) || ''
  }
  // #8249: the THIRD reader — the plaintext-less refusal. Same scoping rule:
  // the retained disclosure belongs to the team whose row the message names.
  const refusalOf = (orgId) => {
    const o = rotateOutcomeFor(outcomes, orgId)
    return (o && o.refusal) || ''
  }
  assert.equal(revealOf('org-A'), 'tt_rot_kept', 'the owning team still sees its replacement')
  assert.equal(noticeOf('org-A'), 'both keys are live')
  assert.equal(revealOf('org-B'), '', 'team B must NEVER render team A\u2019s retained plaintext')
  assert.equal(noticeOf('org-B'), '')
  assert.equal(revealOf(null), '', 'no team selected → nothing to render')
  assert.equal(revealOf(undefined), '')
  assert.equal(rotateOutcomeFor(null, 'org-A'), null, 'no outcomes map → no record')
  assert.equal(rotateOutcomeFor(outcomes, 'org-Z'), null, 'an unrelated team has no record')
  // #8249: over a record that carries the refusal instead of a reveal.
  const refused = { 'org-A': { plaintext: '', expiresAt: null, warning: '', refusal: 'a key was destroyed' } }
  const refusedRefusalOf = (orgId) => {
    const o = rotateOutcomeFor(refused, orgId)
    return (o && o.refusal) || ''
  }
  const refusedRevealOf = (orgId) => {
    const o = rotateOutcomeFor(refused, orgId)
    return (o && revealablePlaintext(o.plaintext)) || ''
  }
  assert.equal(refusedRefusalOf('org-A'), 'a key was destroyed',
    'the owning team retrieves the retained refusal')
  assert.equal(refusedRefusalOf('org-B'), '',
    'team B must NEVER render team A\u2019s refusal (AC2: attributed to the team whose row it names)')
  assert.equal(refusedRevealOf('org-A'), '',
    'a refusal record renders NO reveal on its own team either — there is no secret')
  // #8255: the FOURTH reader — the ambiguous-failure notice. Same scoping rule:
  // it belongs to the team whose row it names, so a retained notice for team A
  // can never render under team B\u2019s header.
  const ambiguousRecord = {
    'org-A': { plaintext: '', expiresAt: null, warning: '', ambiguous: 'outcome could not be confirmed' },
  }
  const ambiguityOf2 = (orgId) => {
    const o = rotateOutcomeFor(ambiguousRecord, orgId)
    return (o && o.ambiguous) || ''
  }
  assert.equal(ambiguityOf2('org-A'), 'outcome could not be confirmed',
    'the owning team retrieves the retained ambiguous-failure notice')
  assert.equal(ambiguityOf2('org-B'), '',
    'team B must NEVER render team A\u2019s ambiguous-failure notice (AC2: attributed to the team whose row it names)')
  assert.equal(ambiguityOf2('org-Z'), '')
  assert.equal(ambiguityOf2(null), '', 'no team selected → nothing to render')
})

// ── copyRotatedKey ──────────────────────────────────────────────────────────
function copyEnv(outcome, clipboard = {}) {
  const written = []
  const cleared = []
  // The fallback branch SELECTS the key element for a manual copy. Stubbing
  // querySelector to null would make that branch dead code, so the whole
  // manual-copy remedy could be deleted with the suite still green.
  const selected = { selectNodeContents: [], addRange: [], removeAllRanges: 0 }
  const fakeEl = { nodeType: 1, __keyValue: true }
  // #4429: the copy writes through the per-team map, so the stub models the
  // real state and records the map after the update (the merge/clear semantics
  // are the thing under test, not the call shape).
  let outcomesState = outcome ? { 'org-A': outcome } : {}
  const deps = {
    rotateOutcome: outcome,
    currentOrgId: 'org-A',
    revealablePlaintext,
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
    setRotateOutcomes: (v) => {
      outcomesState = typeof v === 'function' ? v(outcomesState) : v
      cleared.push(outcomesState)
    },
  }
  return { written, cleared, selected, fakeEl, copyRotatedKey: build(deps, ['async function copyRotatedKey']).copyRotatedKey }
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
  assert.deepEqual(cleared, [{ 'org-A': { plaintext: '', expiresAt: null } }],
    'the reveal clears only after the write resolves')
})

test('#4429: the rotate copy clears the ACTIVE team\u2019s plaintext only, and keeps its warning', async () => {
  // A retained outcome is not this button\u2019s to destroy, and the
  // partial-state warning is not a secret — clearing it would erase the user\u2019s
  // only notice that a key is still live.
  const { written, cleared, copyRotatedKey } = copyEnv(
    { plaintext: 'tt_rot_new_x', expiresAt: '2026-10-20T00:00:00+00:00', warning: 'both keys are live' })
  await copyRotatedKey()
  assert.deepEqual(written, ['tt_rot_new_x'])
  assert.deepEqual(cleared, [{ 'org-A': { plaintext: '', expiresAt: null, warning: 'both keys are live' } }],
    'the plaintext is dropped, the warning outlives the copy')
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
test('#4342/#4429/#8249: the rotate reveal is gated on a derived non-empty string, and the raw state never hits the clipboard', () => {
  // ONE derivation gates the reveal, mirroring `newKeyReveal` — so a falsy
  // `rotateOutcome.plaintext` can never render the empty `.key-value` box.
  // #4429: the derivation reads the ACTIVE team\u2019s record (the scoping rule
  // executed in the test above), so a retained outcome cannot render here.
  assert.match(mainJsxCode,
    /const rotatedKeyReveal = \(rotateOutcome && revealablePlaintext\(rotateOutcome\.plaintext\)\) \|\| ''/,
    'the reveal key must be derived once from the active team\u2019s non-empty, non-blank string')
  assert.match(mainJsxCode,
    /const rotateNoticeText = \(rotateOutcome && rotateOutcome\.warning\) \|\| ''/,
    'the partial-state notice must render from the same team-scoped record (#4429)')
  // #4370 (A6): the predicate moved to module scope in the pure module, so the
  // body pin follows it there (it is no longer declared in main.jsx).
  assert.match(sessionKeyJs,
    /export function revealablePlaintext\(value\) \{\s*return \(typeof value === 'string' && value\.replace\([^\n]*\)\.trim\(\)\) \? value : ''/,
    'the shared predicate must reject a blank (including zero-width/invisible) string, not just a falsy one')
  assert.match(mainJsxCode,
    /import \{[^}]*revealablePlaintext[^}]*\} from '\.\/sessionKey\.js'/,
    'main.jsx must import the shared predicate from its module-scope home')
  assert.match(mainJsxCode, /\{rotatedKeyReveal && \(/,
    'the JSX must gate on the derived string, not the truthy object')
  assert.match(mainJsxCode, /\{rotateNoticeText && \(/,
    'the notice must gate on the derived team-scoped string')
  assert.equal((mainJsxCode.match(/\{rotatedKey && \(/g) || []).length, 0,
    'the raw-object gate must be gone — `{rotatedKey && …}` is truthy with an empty plaintext')
  assert.equal((mainJsxCode.match(/writeText\(rotatedKey\.plaintext\)/g) || []).length, 0,
    'the raw state must never be handed to the clipboard')
  assert.equal((mainJsxCode.match(/\{rotateNotice && \(/g) || []).length, 0,
    'the raw outcome object must not gate the notice either')
  assert.match(mainJsxCode, /onClick=\{copyRotatedKey\}/,
    'the reveal Copy must call the guarded `copyRotatedKey`')
  // #8249: the refusal is derived from the SAME team-scoped record and reaches
  // the surface through the error-banner element — the element is unchanged
  // (its `.error.banner` locator is pinned by the e2e suite), the SOURCE is not
  // the un-keyed `error` slot any more, which is what let a team switch drop
  // it. The latch must also precede the identity guard (the pin lives in
  // keyExpiryTripwire, beside #4429's).
  assert.match(mainJsxCode,
    /const rotateRefusalText = \(rotateOutcome && rotateOutcome\.refusal\) \|\| ''/,
    '#8249: the refusal must be derived from the active team\u2019s record, like the other two disclosures')
  // #8255: and the fourth — the ambiguous-failure notice — through the same
  // reader, so it can only ever surface on the team that owns the record.
  assert.match(mainJsxCode,
    /const rotateAmbiguousText = \(rotateOutcome && rotateOutcome\.ambiguous\) \|\| ''/,
    '#8255: the ambiguous-failure notice must be derived from the active team\u2019s record too')
  assert.match(mainJsxCode, /\{\(error \|\| rotateRefusalText \|\| rotateAmbiguousText\) && \(/,
    '#8249/#8255: the retained disclosures must render through the banner element, sourced from the record')
  assert.equal((mainJsxCode.match(/setError\(`The server did not return the replacement key/g) || []).length, 0,
    '#8249: the refusal must not be written to the un-keyed, transient `error` slot')
})
