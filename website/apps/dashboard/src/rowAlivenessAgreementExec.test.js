// rowAlivenessAgreementExec.test.js — #8228: the row-aliveness conditions have
// ONE declaration, and the THREE surfaces that ask them agree — EXECUTED over
// one shared fixture matrix, not spelled three times in prose.
//
// WHY THIS FILE EXISTS. #4371 (PR #8227) unified the two *held-plaintext* drivers
// of "is this row still alive?" — `durableConnectKey`'s welcome-stale tail and
// main.jsx's row-truth invalidation effect — behind `heldRowEmbeddable`. It
// deliberately left the rest of the family alone, and the residual was that the
// same four conditions were still declared FOUR times in one module:
//
//   surface                          question
//   -------------------------------  ------------------------------------------
//   `heldRowEmbeddable`              may this row back an EMBED?
//   `usableDurableRows`              which rows may the connect gate ROUTE to?
//   `durableConnectKey` paste tail   which dead SOURCE should we report?
//   (the old inline cascade)         (a fourth spelling, byte-different)
//
// They coincided, and NOTHING kept them coinciding. A new dead-row condition (a
// `disabled_by` stamp, a grace window, a new `created_via` kind) added to one was
// invisible to the others — surfacing as a wrong embed or a wrong routing
// decision and never as an error. That is #4371's failure class one surface over.
//
// WHAT #8228 CHANGED. One atom answers the question and the reason is the
// primitive: `rowDeadReason(row)` is the ONLY place the conditions are spelled,
// `rowAlive(row)` is its totalisation, `heldRowEmbeddable` is a named alias for
// the embed question, and both `usableDurableRows` (routing) and the paste tail
// COMPOSE the atom instead of restating it. `isManagedKey` stays composed into
// routing on purpose — it is the API-Keys-TABLE predicate, so routing remains a
// subset of the atom and a future table relaxation cannot silently widen what may
// be embedded.
//
// WHAT THIS FILE GUARDS (four halves — none is sufficient alone):
//
//   1. AGREEMENT, EXECUTED. One fixture matrix is driven through all three
//      surfaces (plus main.jsx's row-truth effect, the #4371 consumer) and any
//      surface that disagrees with the atom is NAMED in the failure. A matrix
//      that only asserted "the atom says dead" would prove nothing about the
//      consumers.
//   2. ONE DECLARATION, STRUCTURAL. Agreement cannot be proven by behaviour
//      alone: a re-inlined byte-identical cascade would agree today. So the
//      routing slice, the embed body and the paste tail are each required to
//      declare NONE of the four conditions and to consume the atom.
//   3. A CLOSED REASON VOCABULARY. The paste tail reports the atom's own reason
//      as its `source`, and main.jsx's paste handler refuses any classification
//      that is not 'durable'. Together that means a new atom arm can neither
//      fall through to `'durable'` (a dead row embedded as live) nor reach the
//      UI unannounced — it is refused, and this file's vocabulary pin forces the
//      copy decision to be deliberate.
//   4. MUTATION CONTROLS that must RED. For each surface, a dead-row condition
//      is added to that surface ALONE (or a paste arm dropped) by rewriting the
//      REAL module source and re-evaluating it — and the matrix must report that
//      named surface. Plus the converse: a condition added to the ATOM alone must
//      propagate to every surface with no other edit. A control that stops
//      discriminating fails loudly rather than passing vacuously.
//
// NOTE ON THE #2426 TRIPWIRE. `keyExpiryTripwire.test.js` used to pin the
// literal `!k.expires_at` inside `usableDurableRows`. #8228 moved that condition
// into the atom and re-pinned the tripwire there plus the composition that
// carries it into routing — the #2426 guarantee (Never-keys-only embed policy, an
// expired tombstone still listed but never routed) is unchanged, only its home.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { stripComments } from './testSupport.js'
import {
  rowDeadReason, rowAlive, heldRowEmbeddable, usableDurableRows, durableConnectKey,
} from './sessionKey.js'

const here = dirname(fileURLToPath(import.meta.url))
const MODULE_SRC = readFileSync(join(here, 'sessionKey.js'), 'utf8')
const mainJsx = readFileSync(join(here, 'main.jsx'), 'utf8')

// ── the shared fixture matrix ──────────────────────────────────────────────
// ONE set, driven by every surface below. `reason` is the atom's own answer for
// the fixture's resolved row ('' = alive), so each entry pins BOTH the polarity
// and the reported cause — a surface that merely says "dead" for the wrong cause
// (a bootstrap row reported as 'expiring') is caught by the source assertions.
const PLAINTEXT = 'tt_rowalive_abcdefgh'
const PREFIX = PLAINTEXT.slice(0, 10)

const LIVE_ROW = {
  key_id: 'k_rowalive', key_prefix: PREFIX, name: null, created_via: 'provisioned',
  revoked_at: null, enabled: true, expires_at: null,
  created_at: '2026-08-01T00:00:00.000Z',
}
const rowWith = (patch) => ({ ...LIVE_ROW, ...patch })

// The LIVE shapes: every way a row can be alive, incl. the two registry-parity
// shapes that a naive `!exists`/`=== false` spelling would get wrong.
const LIVE_SHAPES = [
  { name: 'live provisioned row', rows: [rowWith({})], reason: '' },
  { name: 'legacy row (no created_via)', rows: [rowWith({ created_via: null })], reason: '' },
  { name: 'row with no enabled field (registry parity: absent → enabled)', rows: [rowWith({ enabled: undefined })], reason: '' },
  { name: "recovery row (the owner-level keyless-mint lane)", rows: [rowWith({ created_via: 'recovery' })], reason: '' },
]

// The DEAD conditions: every arm the atom declares, each applied ALONE to a
// live row (which is what makes each one load-bearing), plus the expired
// tombstone #2426 keeps listed and the ABSENT row (no field to patch).
const DEAD_SHAPES = [
  { name: 'revoked_at', rows: [rowWith({ revoked_at: '2026-09-04T00:00:00Z' })], reason: 'revoked' },
  { name: 'enabled === false', rows: [rowWith({ enabled: false })], reason: 'disabled' },
  { name: "created_via === 'bootstrap'", rows: [rowWith({ created_via: 'bootstrap' })], reason: 'bootstrap' },
  { name: 'expires_at (a chosen lifetime)', rows: [rowWith({ expires_at: '2026-09-04T00:00:00Z' })], reason: 'expiring' },
  { name: 'expires_at in the past (the expired tombstone #2426 keeps listed)', rows: [rowWith({ expires_at: '2020-01-01T00:00:00Z' })], reason: 'expiring' },
  {
    name: 'the row is ABSENT (loaded rows, prefix resolves nothing)',
    rows: [rowWith({ key_id: 'k_other', key_prefix: 'tt_otherrow' })],
    reason: 'absent',
  },
]

const FIXTURES = [...LIVE_SHAPES, ...DEAD_SHAPES]

// Resolve the row exactly as the shipped code resolves it — a fixture matrix
// that hand-passed `null` for the absent case would not exercise the prefix match.
const matchedRow = (rows) =>
  (rows || []).find((k) => k && k.key_prefix && PLAINTEXT.startsWith(k.key_prefix)) || null

// ── module loading, so a CONTROL can mutate ONE surface and re-evaluate it ──
// sessionKey.js is dependency-free and every export is a plain `export function`,
// so the REAL source can be evaluated as-is (one keyword rewrite). Mutations are
// therefore applied to the shipped text, not to a paraphrase of it — the same
// reason `heldRowTruthExec.test.js` executes main.jsx's real callback text.
const EXPORTED = [
  'rowDeadReason', 'rowAlive', 'heldRowEmbeddable', 'isManagedKey', 'usableDurableRows',
  'durableConnectKey', 'connectKeyGate', 'keyDisplayName', 'revealablePlaintext',
  'revealableMintPlaintext',
]
function loadModule(src) {
  assert.equal(src.split('\nexport function ').length - 1, EXPORTED.length,
    'every top-level declaration in sessionKey.js is an export the fixture loader knows — '
    + 'a new non-exported declaration would silently not be loaded')
  for (const name of EXPORTED) {
    assert.ok(src.includes(`\nexport function ${name}(`), `the fixture loader must know ${name} (it is not exported any more)`)
  }
  const factory = new Function(
    `${src.replace(/^export function /gm, 'function ')}\nreturn { ${EXPORTED.join(', ')} }`,
  )
  return factory()
}

// Apply ONE textual mutation, failing loudly if the anchor has moved (a control
// whose anchor no longer matches would otherwise pass without testing anything).
function mutate(src, from, to, label) {
  assert.ok(src.includes(from), `${label}: the mutation anchor must exist in sessionKey.js — got ${JSON.stringify(from)}`)
  const out = src.replace(from, to)
  assert.notEqual(out, src, `${label}: the mutation must actually rewrite the source`)
  return out
}

// Extract one declaration's text (up to the next top-level `export function`).
function fnBody(src, name) {
  const start = src.indexOf(`export function ${name}(`)
  assert.ok(start > -1, `sessionKey.js must declare ${name}`)
  const next = src.indexOf('\nexport function ', start + 10)
  return src.slice(start, next > -1 ? next : src.length)
}

// ── the disagreement reporter ──────────────────────────────────────────────
// Answers "which surface(s) disagree with the shared atom?" for one module and
// the shared matrix. Per-row on purpose: `usableDurableRows` answers a SET
// question, so membership of the fixture's own row is the comparable verdict
// (asking "is the returned array non-empty" would compare a per-row atom against
// a whole-table answer and manufacture a disagreement out of a fixture that
// merely contains a second, live row).
function disagreementsFor(mod, fixtures = FIXTURES) {
  const found = []
  for (const f of fixtures) {
    const row = matchedRow(f.rows)
    const alive = mod.rowAlive(row)
    const rows = row ? [row] : [null]

    if (mod.heldRowEmbeddable(row) !== alive) {
      found.push({ fixture: f.name, surface: 'embed', said: mod.heldRowEmbeddable(row), atom: alive })
    }
    const routed = mod.usableDurableRows(rows).length === 1
    if (routed !== alive) found.push({ fixture: f.name, surface: 'route', said: routed, atom: alive })

    const paste = mod.durableConnectKey('', PLAINTEXT, rows)
    if ((paste.source === 'durable') !== alive) {
      found.push({ fixture: f.name, surface: 'paste', said: paste.source, atom: alive })
    }
  }
  return found
}

const report = (found) => found.map((d) => `${d.surface}@${d.fixture} (said ${d.said}, atom ${d.atom})`).join('; ')

// The distinct surfaces a disagreement report names, sorted — a control asserts on
// WHICH surface(s) drifted, and a surface can legitimately be named by more than
// one fixture (two expiring shapes, one lost arm).
const surfaces = (found) => [...new Set(found.map((d) => d.surface))].sort()

// The mutation controls' extra shape: an arm NO surface declares today (a
// server-side revoke stamp). Every surface reports it ALIVE, so a control that
// adds the arm to exactly one surface has exactly one shape to disagree on — and
// the live matrix cannot be discriminating by accident.
const STAMP = '2026-09-04T00:00:00Z'
const STAMP_FIXTURE = {
  name: 'disabled_by stamp (an arm no surface declares today)',
  rows: [rowWith({ disabled_by: STAMP })],
  reason: '',
}
const CONTROL_FIXTURES = [...FIXTURES, STAMP_FIXTURE]

// ── 1. the agreement, executed ─────────────────────────────────────────────
test('#8228: the embed rule, the routing rule and the paste cascade all agree with the atom across the matrix', () => {
  const found = disagreementsFor(loadModule(MODULE_SRC))
  assert.deepEqual(found, [],
    `every surface must agree with the shared row-aliveness atom — disagreements: ${report(found)}`)
})

test('#8228: the atom answers the fixture matrix with the cause each shape pins', () => {
  // Polarity AND cause. Without the cause half, a surface could report the wrong
  // remedy (a bootstrap row told "it expires") and the agreement test would pass.
  for (const f of FIXTURES) {
    const row = matchedRow(f.rows)
    assert.equal(rowDeadReason(row), f.reason,
      `${f.name}: the atom must report ${JSON.stringify(f.reason)}`)
    assert.equal(rowAlive(row), f.reason === '',
      `${f.name}: alive polarity`)
  }
  // The two registry-parity shapes are the ones a re-spelling gets wrong, so
  // state them outright: an ABSENT `enabled` is enabled, and an ABSENT row is dead.
  assert.equal(rowDeadReason(rowWith({ enabled: undefined })), '',
    'enabled absent → enabled (registry parity: only `enabled === false` disables)')
  assert.equal(rowDeadReason(rowWith({ enabled: null })), '',
    'enabled null → enabled (same parity rule)')
  assert.equal(rowDeadReason(null), 'absent', 'a missing row is dead — never embed or route on unknown')
  assert.equal(rowDeadReason(undefined), 'absent', 'undefined is the same missing-row condition')
})

test('#8228: the paste cascade reports the atom\'s OWN reason — a distinct source per cause', () => {
  // #2426 decision: the wizard's paste error must say WHY ('expiring' is not a
  // "login session" key and must not borrow bootstrap's copy). That is the only
  // reason the cascade exists as a cascade, and it is now derived rather than
  // restated — so this asserts equality with the atom, not a third copy of it.
  for (const f of DEAD_SHAPES) {
    const row = matchedRow(f.rows)
    const paste = durableConnectKey('', PLAINTEXT, row ? [row] : [null])
    if (f.reason === 'absent') {
      assert.equal(paste.source, 'unknown',
        `${f.name}: an unmatched prefix stays 'unknown' (the paste tail's own missing-row arm)`)
      continue
    }
    assert.equal(paste.source, f.reason,
      `${f.name}: the reported source must be the atom's own reason`)
    assert.equal(paste.key, '', `${f.name}: a dead row never yields a key`)
    assert.equal(paste.durable, false, `${f.name}: …nor the durable flag`)
  }
  // And the live shapes embed, with the plaintext verbatim.
  for (const f of LIVE_SHAPES) {
    const paste = durableConnectKey('', PLAINTEXT, f.rows)
    assert.equal(paste.source, 'durable', `${f.name}: a live row embeds`)
    assert.equal(paste.key, PLAINTEXT, `${f.name}: …the plaintext verbatim`)
    assert.equal(paste.durable, true, `${f.name}: …flagged durable`)
  }
})

test('#8228: main.jsx\'s row-truth effect — the third #4371 consumer — rides the same atom', () => {
  // The atom is the embed answer, so the effect that CLEARS a held plaintext must
  // agree with it for every fixture: cleared ⇔ the atom calls the row dead.
  // The effect's wiring is pinned in heldRowTruthExec.test.js; this pins the
  // DECISION against the matrix so all four surfaces are covered by one matrix.
  for (const f of FIXTURES) {
    const alive = rowAlive(matchedRow(f.rows))
    assert.equal(heldRowEmbeddable(matchedRow(f.rows)), alive,
      `${f.name}: the embed rule the effect consumes must agree with the atom`)
  }
})

// ── 2. one declaration, structural ─────────────────────────────────────────
test('#8228: the conditions are declared in the atom ONLY — no consumer re-spells them', () => {
  // The atom is the one place any of the four conditions is read.
  const atom = stripComments(fnBody(MODULE_SRC, 'rowDeadReason'))
  for (const condition of [/row\.revoked_at/, /row\.enabled === false/, /row\.created_via === 'bootstrap'/, /row\.expires_at/]) {
    assert.match(atom, condition, `the atom must declare ${condition}`)
  }

  // `rowAlive` is a totalisation, not a second copy.
  const alive = stripComments(fnBody(MODULE_SRC, 'rowAlive'))
  assert.match(alive, /rowDeadReason\(row\) === ''/, 'rowAlive totalises the atom')
  assert.doesNotMatch(alive, /revoked_at|expires_at|created_via|enabled/,
    'rowAlive must not restate the conditions')

  // The embed rule is a NAMED ALIAS — the #4371 contract, unchanged, no arms.
  const embed = stripComments(fnBody(MODULE_SRC, 'heldRowEmbeddable'))
  assert.match(embed, /return rowAlive\(row\)/, 'heldRowEmbeddable must delegate to the atom')
  assert.doesNotMatch(embed, /revoked_at|expires_at|created_via|enabled/,
    'heldRowEmbeddable must not re-declare the conditions (#4371 kept, #8228 extended)')

  // Routing composes the atom and keeps `isManagedKey` (the TABLE predicate) —
  // so routing stays a SUBSET of the atom: a future `isManagedKey` relaxation can
  // change what the gate ROUTES to, never what may be EMBEDDED (#2426's concern).
  const route = stripComments(fnBody(MODULE_SRC, 'usableDurableRows'))
  assert.match(route, /\.filter\(\(k\) => isManagedKey\(k\) && rowAlive\(k\)\)/,
    'usableDurableRows must compose isManagedKey with the atom')
  assert.doesNotMatch(route, /revoked_at|expires_at|created_via|enabled/,
    'usableDurableRows must not re-declare the row-aliveness conditions')

  // The paste tail reads the atom's reason; its arms are gone.
  const cascade = stripComments(fnBody(MODULE_SRC, 'durableConnectKey'))
  assert.match(cascade, /const dead = rowDeadReason\(row\)/, 'the paste tail must consult the atom')
  assert.match(cascade, /if \(dead\) return \{ key: '', durable: false, source: dead \}/,
    'the paste tail must report the atom\'s reason as its source')
  assert.doesNotMatch(cascade, /row\.revoked_at|row\.expires_at|row\.created_via|row\.enabled/,
    'the paste tail must declare none of the conditions itself — that was the fourth spelling')
})

// ── 3. the closed reason vocabulary ────────────────────────────────────────
const KNOWN_REASONS = ['', 'absent', 'bootstrap', 'disabled', 'expiring', 'revoked']
function reasonVocabulary(src) {
  const body = fnBody(src, 'rowDeadReason')
  return [...body.matchAll(/return '([^']*)'/g)].map((m) => m[1]).sort()
}

test('#8228: the atom\'s reason vocabulary is CLOSED and every reason is exercised by the matrix', () => {
  assert.deepEqual(reasonVocabulary(MODULE_SRC), KNOWN_REASONS,
    'a new atom arm must be a deliberate decision — main.jsx\'s paste handler copies the '
    + 'four known causes and refuses the rest, so the vocabulary is pinned here')

  // Closure the other way: every reason the atom can return is reached by ≥1
  // fixture. Otherwise an arm could be silently unexercised ("the matrix covers
  // every dead condition" would be unproven).
  const exercised = new Set(FIXTURES.map((f) => rowDeadReason(matchedRow(f.rows))))
  for (const reason of KNOWN_REASONS) {
    assert.equal(exercised.has(reason), true,
      `reason ${JSON.stringify(reason)} has no fixture in the shared matrix`)
  }
})

test('#8228: main.jsx\'s paste handler is FAIL-CLOSED — any non-\'durable\' classification is refused', () => {
  // The paste tail reports the atom's reason as `source`; the handler copies the
  // four known causes. Without a net, a FIFTH cause would fall through to the
  // store — and `wizardDurableKey` is the harness snippet's key source, so that
  // would embed a row the atom calls dead.
  const start = mainJsx.indexOf('const pasted = wizardDurablePaste.trim()')
  assert.ok(start > -1, 'the classified paste writer must still exist')
  const storeAt = mainJsx.indexOf('setWizardDurableKey(pasted)', start)
  assert.ok(storeAt > start, 'the paste writer must still store through setWizardDurableKey')
  const handler = stripComments(mainJsx.slice(start, storeAt + 'setWizardDurableKey(pasted)'.length))

  assert.match(handler, /const check = durableConnectKey\('', pasted, keys\)/,
    'the handler must classify the pasted candidate through the pure gate')
  const netAt = handler.indexOf("check.source !== 'durable'")
  assert.ok(netAt > -1,
    'the handler must refuse ANY classification that is not \'durable\' — the fail-closed net')
  assert.ok(netAt < handler.indexOf('setWizardDurableKey(pasted)'),
    'the net must run BEFORE the store (after the store it guards nothing)')
  assert.match(handler.slice(netAt), /capRevokeFirstClause\(team, keys\)/,
    'the net\'s remedy names create/rotate, so it carries the at-cap clause (#4353)')
})

// ── 4. mutation controls — each must RED on its own surface ────────────────
// A condition added to ONE surface is exactly the drift this issue exists to
// catch. Each control rewrites the REAL module source, loads it, and requires the
// matrix to NAME that surface. The added condition is `disabled_by` — a plausible
// future arm (a server-side revoke stamp) that no surface declares today, so the
// live matrix cannot be discriminating by accident.
test('#8228 (control): a dead-row condition added to the EMBED rule alone is reported', () => {
  const mutated = loadModule(mutate(MODULE_SRC,
    'export function heldRowEmbeddable(row) {\n  return rowAlive(row)\n}',
    'export function heldRowEmbeddable(row) {\n  return rowAlive(row) && !row.disabled_by\n}',
    'embed-only condition'))
  const found = disagreementsFor(mutated, CONTROL_FIXTURES)
  assert.deepEqual(surfaces(found), ['embed'],
    `an embed-only condition must be reported against the embed surface — got ${report(found)}`)
  assert.ok(found.every((d) => d.fixture === STAMP_FIXTURE.name),
    `only the stamped row (which the added arm kills) can disagree here — got ${report(found)}`)
})

test('#8228 (control): a dead-row condition added to the ROUTING rule alone is reported', () => {
  const mutated = loadModule(mutate(MODULE_SRC,
    '.filter((k) => isManagedKey(k) && rowAlive(k))',
    '.filter((k) => isManagedKey(k) && rowAlive(k) && !k.disabled_by)',
    'routing-only condition'))
  const found = disagreementsFor(mutated, CONTROL_FIXTURES)
  assert.deepEqual(surfaces(found), ['route'],
    `a routing-only condition must be reported against the route surface — got ${report(found)}`)
  assert.ok(found.every((d) => d.fixture === STAMP_FIXTURE.name),
    `only the stamped row can disagree here — got ${report(found)}`)
})

test('#8228 (control): a paste cascade that loses an arm is reported (the fail-OPEN direction)', () => {
  // The pre-#8228 shape, minus the expiring arm: byte-plausible, and a real
  // regression — an expiring row would report itself 'durable' and be embedded as
  // the harness key. If the matrix could not see this, it guards nothing.
  const mutated = loadModule(mutate(MODULE_SRC,
    "  const dead = rowDeadReason(row)\n  if (dead) return { key: '', durable: false, source: dead }",
    "  if (row.revoked_at) return { key: '', durable: false, source: 'revoked' }\n"
    + "  if (row.enabled === false) return { key: '', durable: false, source: 'disabled' }\n"
    + "  if (row.created_via === 'bootstrap') return { key: '', durable: false, source: 'bootstrap' }",
    'paste cascade without the expiring arm'))
  const found = disagreementsFor(mutated)
  assert.deepEqual(surfaces(found), ['paste'],
    `a paste cascade missing an arm must be reported against the paste surface — got ${report(found)}`)
  assert.ok(found.every((d) => d.fixture.includes('expires_at')),
    `only the expiring fixtures can disagree here — got ${report(found)}`)
  // And the failure mode is stated as it would appear: the dead row reports 'durable'.
  assert.equal(mutated.durableConnectKey('', PLAINTEXT, [rowWith({ expires_at: '2026-09-04T00:00:00Z' })]).source,
    'durable', 'the mutation is the fail-OPEN shape: an expiring row claims to be embeddable')
})

test('#8228 (control): a condition added to the ATOM alone reaches every surface with no other edit', () => {
  // The converse direction, and the whole point of the atom: one arm added in ONE
  // place must propagate. If any surface had to be edited too, the atom is not
  // load-bearing and the duplication is still there.
  const mutated = loadModule(mutate(MODULE_SRC,
    "  if (row.expires_at) return 'expiring'\n  return ''",
    "  if (row.expires_at) return 'expiring'\n  if (row.disabled_by) return 'revoked'\n  return ''",
    'atom-only condition'))
  const byStamp = [rowWith({ disabled_by: STAMP })]
  assert.equal(mutated.rowDeadReason(matchedRow(byStamp)), 'revoked',
    'the mutated atom reports the stamp')
  const found = disagreementsFor(mutated, CONTROL_FIXTURES)
  assert.deepEqual(found, [],
    `every surface must follow a new atom arm automatically — got ${report(found)}`)
  // Explicitly: the three consumers all flipped, with no other edit.
  assert.equal(mutated.heldRowEmbeddable(matchedRow(byStamp)), false, 'embed followed the atom')
  assert.deepEqual(mutated.usableDurableRows(byStamp), [], 'routing followed the atom')
  assert.equal(mutated.durableConnectKey('', PLAINTEXT, byStamp).source, 'revoked',
    'the paste tail followed the atom — and reported its reason, not a fallthrough')
})

test('#8228 (control): a NEW reason from the atom is caught by the vocabulary pin, not shipped silently', () => {
  // The dangerous shape is not a new CONDITION — it is a new SOURCE. `'disabled_by'`
  // would reach main.jsx's paste handler with no copy of its own; the fail-closed
  // net refuses it (so no dead key is embedded), but the user would get the generic
  // remedy. The vocabulary pin makes that a deliberate decision instead.
  const mutated = mutate(MODULE_SRC,
    "  if (row.expires_at) return 'expiring'\n  return ''",
    "  if (row.expires_at) return 'expiring'\n  if (row.disabled_by) return 'disabled_by'\n  return ''",
    'atom-only NEW reason')
  assert.notDeepEqual(reasonVocabulary(mutated), KNOWN_REASONS,
    'the vocabulary pin must RED on a new reason')
  assert.deepEqual(reasonVocabulary(MODULE_SRC), KNOWN_REASONS,
    '…while the shipped atom stays inside the closed set')
})
