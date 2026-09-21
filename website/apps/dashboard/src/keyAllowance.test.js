// keyAllowance.test.js — #3874. EXECUTION tests for the allowance derivations.
//
// These import and RUN the real functions (./keyAllowance.js) — they are not
// source-text scans, so a behaviour-identical reformat of the module (renamed
// locals, reordered statements, whitespace) cannot flip them. That property is
// pinned explicitly in the last test, and the defect-detection direction is
// pinned by the "does not fabricate" / "uses the server value" cases, which
// FAIL against the pre-#3874 implementation (the local main.jsx functions with
// the hardcoded `team_?.max_api_keys ?? '2'` fallback) and against a dashboard
// with no pre-cap line at all.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import {
  allowanceLine,
  capLimitFrom,
  capRevokeFirstClause,
  existingKeyNoteFrom,
  keyAllowance,
  rotateCapNoticeFrom,
  serverKeyLimit,
  upgradeNoticeFrom,
  usedKeySlots,
} from './keyAllowance.js'

// The server's create-path 402 detail (tortoise/quota.py enforce_org_limit,
// reached by POST /v1/team/keys via _check_org_limit). Its number comes from
// the SAME auth-dict field /v1/team now exposes, which is what makes the
// pre-cap line and the at-cap refusal agree.
const capDetail = (n) => `Team api_keys limit reached (${n}). Upgrade your plan to increase it.`

// ── 1. Pre-cap visibility (RED against the pre-#3874 dashboard) ──────────
// The defect was that nothing rendered the allowance until the 402. These
// assertions execute the derivation that gives the surface its number.

test('#3874: the pre-cap line states the server allowance before any refusal', () => {
  const line = allowanceLine({ max_api_keys: 4 }, [{ id: 'a', created_via: 'provisioned' }])
  assert.notEqual(line, null, 'a server allowance must produce a pre-cap line')
  assert.match(line, /\b4\b/, `line must state the server limit: ${line}`)
  assert.match(line, /1 in use/, `line must state usage: ${line}`)
})

test('#3874: the pre-cap line tracks a limit that differs from the old hardcoded 2', () => {
  const line = allowanceLine({ max_api_keys: 5 }, [])
  assert.match(line, /5 API keys/, line)
  assert.doesNotMatch(line, /\b2\b/, `a 5-key plan must not read as the old '2': ${line}`)
})

// ── 2. No fabricated number (RED against the old `?? '2'`) ───────────────

test('#3874: no server allowance → the surface stays silent, never fabricates', () => {
  for (const team of [{}, { max_api_keys: null }, { max_api_keys: undefined }, { max_api_keys: 'x' }]) {
    assert.equal(serverKeyLimit(team), null, `must treat ${JSON.stringify(team)} as unknown`)
    assert.equal(allowanceLine(team, []), null, 'no allowance → no pre-cap line')
  }
})

test('#3874: the notices omit the number when neither the 402 detail nor /v1/team has one', () => {
  // The old implementation defaulted to '2' here and declared a limit the
  // server never enforced. Now BOTH notices must degrade without a number.
  const up = upgradeNoticeFrom('', {})
  const rot = rotateCapNoticeFrom('', {})
  assert.doesNotMatch(up, /\bof \d+ API keys\b/, `upgrade notice must not invent a limit: ${up}`)
  assert.doesNotMatch(up, /\b2\b/, `upgrade notice must not fall back to the hardcoded 2: ${up}`)
  assert.doesNotMatch(rot, /\bof \d+ API keys\b/, `rotate notice must not invent a limit: ${rot}`)
  assert.doesNotMatch(rot, /\b2\b/, `rotate notice must not fall back to the hardcoded 2: ${rot}`)
})

// ── 3. The notices read the server field ─────────────────────────────────

test('#3874: the create notice reads the /v1/team allowance when the detail has no number', () => {
  const up = upgradeNoticeFrom('API key limit reached (legacy mint — upgrade or revoke)',
                               { max_api_keys: 7 })
  assert.match(up, /limit of 7 API keys/, up)
  assert.doesNotMatch(up, /\b2\b/, `must not fall back to the hardcoded 2: ${up}`)
})

test('#3874: the rotate notice reads the /v1/team allowance when the detail has no number', () => {
  const rot = rotateCapNoticeFrom('API key limit reached (legacy mint — upgrade or revoke)',
                                  { max_api_keys: 7 })
  assert.match(rot, /limit of 7 API keys/, rot)
  assert.doesNotMatch(rot, /\b2\b/, rot)
})

test('#3874: the at-cap detail wins — it IS the enforced cap', () => {
  // /v1/team and the 402 come from the same auth dict, but if they ever
  // disagree the refusal is the ground truth a capped user is acting on.
  assert.equal(capLimitFrom(capDetail(4), { max_api_keys: 9 }), '4')
  assert.match(upgradeNoticeFrom(capDetail(4), { max_api_keys: 9 }), /limit of 4 API keys/)
})

// ── 4. Number-stable copy; the create remedy tail is #2699's ─────────────
// #3874 changed the number's SOURCE only. #2699 then changed the create/shared
// notice's REMEDY TAIL: "regenerate an existing key instead" is unreachable at
// the cap (rotate mints the replacement through the SAME capped
// POST /v1/team/keys, so it 402s too). The numbered SENTENCE stays
// byte-identical to the approved #1147 wording; only the tail moves. The
// rotate notice (#2229) is untouched.

test('#3874: numbered notices keep the approved number sentence (source change only)', () => {
  assert.equal(
    upgradeNoticeFrom(capDetail(2), { max_api_keys: 2 }),
    "You've reached your plan's limit of 2 API keys. Revoke an existing key to free a slot — or upgrade to add more.")
  assert.equal(
    rotateCapNoticeFrom(capDetail(2), { max_api_keys: 2 }),
    "You're at your plan's limit of 2 API keys. Rotating creates the replacement before revoking this one, so revoke an unused key first — or upgrade to add more.")
})

// ── 4b. #2699: the at-cap remedy must be ACHIEVABLE ──────────────────────
// The create/shared notice used to advertise "regenerate an existing key
// instead" — but a team AT the cap 402s on the rotate path too (rotate mints
// the replacement through the same capped POST /v1/team/keys before revoking
// the old row). These pin the achievable remedy.
//
// TWO tests, not one, so each branch's failure against the OLD string is
// observable in isolation: in a single shared test the numbered assert.equal
// throws first and the no-number assertion is never evaluated.

test('#2699: the numbered create notice offers the achievable remedy, never regenerate', () => {
  const up = upgradeNoticeFrom(capDetail(2), { max_api_keys: 2 })
  // The enforced number the user needs stays stated.
  assert.match(up, /limit of 2 API keys/, up)
  // The remedy that works AT the cap: revoke frees a slot
  // (quota._count_resource('api_keys') counts only non-revoked, non-expired rows).
  assert.match(up, /Revoke an existing key to free a slot/, up)
  // The dead end is gone — the RED direction (fails against the pre-#2699 string).
  assert.doesNotMatch(up, /regenerate/i, up)
})

test('#2699: the degraded (no-number) create notice offers the achievable remedy, never regenerate', () => {
  const up = upgradeNoticeFrom('', {})
  assert.equal(up,
    "You've reached your plan's API key limit. Revoke an existing key to free a slot — or upgrade to add more.")
  assert.match(up, /Revoke an existing key to free a slot/, up)
  assert.doesNotMatch(up, /regenerate/i, up)
  assert.doesNotMatch(up, /\bof \d+ API keys\b/, `must not invent a limit: ${up}`)
})

// ── 4c. #4353: the connect step's existing-key note is cap-aware ─────────
// Below the cap, rotate is the route that does not grow the count. AT the cap
// it is a dead end: rotate mints the replacement through the SAME capped
// POST /v1/team/keys before revoking the old row, so `regenerateKey` 402s on
// its mint leg and the old key is never revoked. The note therefore returns
// the canonical at-cap remedy (revoke — a revoked row leaves the gate's
// count) exactly as the create-path notice states it.

test('#4353: at the cap the existing-key note returns the canonical cap remedy, never rotate', () => {
  const team = { max_api_keys: 2 }
  const note = existingKeyNoteFrom(team, [{ id: 'a' }, { id: 'b' }])
  // Pinned to the ONE canonical string — a second copy of the remedy here is
  // exactly how the two surfaces desync.
  assert.equal(note, rotateCapNoticeFrom('', team),
    'the note must be the canonical at-cap notice, not a second copy that can drift')
  assert.match(note, /revoke an unused key first/, note)
  assert.doesNotMatch(note, /regenerate/i, `the dead end must not return: ${note}`)
  assert.doesNotMatch(note, /^Rotate the existing key in the API Keys tab/, note)
})

test('#4353: below the cap the existing-key note keeps the rotate sentence', () => {
  const note = existingKeyNoteFrom({ max_api_keys: 2 }, [{ id: 'a' }])
  assert.match(note, /^Rotate the existing key in the API Keys tab/, note)
  assert.match(note, /without adding a key/, note)
  // The fresh-mint cost clause must survive the move out of main.jsx — the
  // below-cap note has to name the price of creating another key here.
  assert.match(note, /Creating a new key here spends another of your plan's key slots/, note)
})

test('#4353: an unknown allowance never fabricates a limit — the note stays the rotate sentence', () => {
  for (const team of [{}, { max_api_keys: null }, { max_api_keys: undefined }, { max_api_keys: 'x' }]) {
    const note = existingKeyNoteFrom(team, [{ id: 'a' }, { id: 'b' }, { id: 'c' }])
    assert.match(note, /^Rotate the existing key in the API Keys tab/,
      `unknown allowance (${JSON.stringify(team)}) must not claim the cap: ${note}`)
  }
})

test('#4353: a revoked row does not hold a slot, so the note stays below-cap', () => {
  const now = Date.parse('2026-09-18T00:00:00Z')
  const rows = [
    { id: 'live' },
    { id: 'revoked', revoked_at: new Date(now - 1000).toISOString() },
  ]
  assert.equal(usedKeySlots(rows, now), 1,
    'a revoked row is an audit tombstone — it leaves the gate count')
  const note = existingKeyNoteFrom({ max_api_keys: 2 }, rows, now)
  assert.match(note, /^Rotate the existing key in the API Keys tab/,
    'one live row on a 2-key plan is not at the cap')
})

// ── 5. Cross-surface agreement (the issue's core invariant) ──────────────

test('#3874: the pre-cap allowance equals the at-cap refusal for the same org', () => {
  // One org, one server field: the surface shown BEFORE the cap and the
  // refusal shown AT it must name the same number.
  const team = { max_api_keys: 3 }
  const pre = allowanceLine(team, [{ id: 'a' }, { id: 'b' }])
  const at = upgradeNoticeFrom(capDetail(serverKeyLimit(team)), team)
  assert.match(pre, /3 API keys/, pre)
  assert.match(at, /limit of 3 API keys/, at)
  const preNum = pre.match(/\b(\d+) API keys\b/)[1]
  const atNum = at.match(/limit of (\d+) API keys/)[1]
  assert.equal(preNum, atNum, 'pre-cap and at-cap allowance must agree')
})

// ── 6. Usage mirrors the mint gate's predicate ───────────────────────────

test('#3874: used-slot count mirrors quota._count_resource (non-revoked, non-expired)', () => {
  const now = Date.parse('2026-09-18T00:00:00Z')
  const iso = (ms) => new Date(now + ms).toISOString()
  const rows = [
    { id: 'live' },                                        // counts
    { id: 'never-expires', created_via: 'provisioned' },   // counts
    { id: 'future', expires_at: iso(86400000) },           // counts
    { id: 'expired', expires_at: iso(-86400000) },         // excluded (#2426)
    { id: 'revoked', revoked_at: iso(-1000) },             // excluded (#2481)
    { id: 'bootstrap-live', created_via: 'bootstrap' },    // counts (gate predicate)
  ]
  assert.equal(usedKeySlots(rows, now), 4)
  const a = keyAllowance({ max_api_keys: 6 }, rows, now)
  assert.deepEqual(a, { limit: 6, used: 4, remaining: 2, exhausted: false })
})

test('#3874: an over-cap legacy org clamps remaining at zero (never negative)', () => {
  const a = keyAllowance({ max_api_keys: 2 }, [{ id: 'a' }, { id: 'b' }, { id: 'c' }])
  assert.deepEqual(a, { limit: 2, used: 3, remaining: 0, exhausted: true })
})

test('#3874: a singular allowance reads as one key', () => {
  assert.match(allowanceLine({ max_api_keys: 1 }, []), /1 API key —/)
})

// ── 7. Green under a behaviour-identical reformat ────────────────────────
// The guard is EXECUTION, not a source scan: two semantically identical inputs
// (different key order, extra irrelevant fields, reordered rows) must produce
// identical output. A source-text guard would instead break on a whitespace or
// rename reformat of keyAllowance.js — the failure direction this pins against.

test('#3874: identical semantics in a differently-shaped input stays green', () => {
  const teamA = { max_api_keys: 4, tier: 'free' }
  const teamB = { tier: 'free', org_id: 'o1', checkout_price_ids: {}, max_api_keys: 4 }
  const rowsA = [{ id: 'a' }, { id: 'b' }]
  const rowsB = [{ id: 'b', name: 'renamed' }, { id: 'a' }]
  assert.equal(allowanceLine(teamA, rowsA), allowanceLine(teamB, rowsB))
  assert.deepEqual(keyAllowance(teamA, rowsA), keyAllowance(teamB, rowsB))
  const detail = capDetail(4)
  assert.equal(upgradeNoticeFrom(detail, teamA), upgradeNoticeFrom(detail, teamB))
  assert.equal(rotateCapNoticeFrom(detail, teamA), rotateCapNoticeFrom(detail, teamB))
})

// ── 8. Wiring backstop (NOT the behavioural guard) ───────────────────────
// The tests above EXECUTE the real derivations, which is where behaviour is
// proven. This extra layer only guards against the render sites being deleted
// from main.jsx (the reviewer's mutation: removing both sites and the import
// left the behavioural suite green while re-opening the exact #3874 gap). It
// is a presence check, deliberately loose, and must never be cited as proof
// that the surface renders — that proof is the execution tests.
test('#3874 (wiring backstop): main.jsx renders the pre-cap allowance on both key surfaces', () => {
  const src = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'main.jsx'), 'utf8')
  const sites = src.match(/data-key-allowance/g) || []
  assert.ok(sites.length >= 2,
    `expected the keys-tab + create-modal render sites, found ${sites.length}`)
  const renderCalls = (src.match(/\{keysLoaded && allowanceLine\(team, keys\)/g) || []).length
  assert.ok(renderCalls >= 2,
    `both surfaces must gate on keysLoaded (no fabricated "0 in use"), found ${renderCalls}`)
})

// ── 9. #4353: the paste-validation at-cap clause ─────────────────────────
// The clause is ADDED to a rejection's owner/admin remedy, never substituted
// for it, and must be empty whenever the server has not said the org is at
// its limit — so no surface can gain an at-cap warning it cannot substantiate.

test('#4353: at the cap the paste-rejection clause names revoke first', () => {
  const clause = capRevokeFirstClause({ max_api_keys: 2 }, [{ id: 'a' }, { id: 'b' }])
  assert.match(clause, /revoke a key in the API Keys tab/,
    `the clause must name the achievable at-cap action: ${clause}`)
  assert.doesNotMatch(clause, /regenerate/i,
    'at the cap a rotate/regenerate mints through the same capped route — it must never be offered')
})

test('#4353: below the cap the paste-rejection clause is empty', () => {
  assert.equal(capRevokeFirstClause({ max_api_keys: 2 }, [{ id: 'a' }]), '')
})

test('#4353: an unknown allowance never fabricates an at-cap clause', () => {
  assert.equal(capRevokeFirstClause({}, []), '')
  assert.equal(capRevokeFirstClause({ max_api_keys: null }, [{ id: 'a' }]), '')
  assert.equal(capRevokeFirstClause({ max_api_keys: 'lots' }, []), '')
  assert.equal(capRevokeFirstClause({ max_api_keys: -3 }, []), '')
  assert.equal(capRevokeFirstClause(undefined, undefined), '')
})
