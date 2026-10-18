// packs.test.js — #2728 Configured packs: the pure derivations behind the
// Settings home + modal. Run with `node --test src/*.test.js` (Node 20+, zero
// deps — the dashboard's own runner). The render/wiring half is pinned by
// packsModalTripwire.test.js; this file pins the DECISIONS.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  ADD_LIST_DEGRADED_LINE,
  ADD_LIST_EMPTY_LINE,
  PACK_CONSEQUENCE,
  PACK_SETTINGS_SECTION_ID,
  PACK_STATUS_LABEL,
  addListEmptyLine,
  addToDraft,
  addableFromDraft,
  addablePacks,
  defaultPacksGraphId,
  draftFromBody,
  draftIsDirty,
  draftRows,
  draftStatus,
  graphLabel,
  kindCountLabel,
  packCountLine,
  packErrorState,
  packRows,
  packSaveBody,
  removeFromDraft,
  revisionMovedAfterSave,
  selectorGraphs,
  sourceLabel,
  toggleDraftStatus,
} from './packs.js'

const SRC = dirname(fileURLToPath(import.meta.url))

const ROW = (ns, status = 'active', extra = {}) => ({
  namespace: ns,
  name: ns.toUpperCase(),
  version: '1.0.0',
  tier: 'free',
  description: `${ns} description`,
  status,
  source: 'starter',
  installed_at: '2026-10-14T00:00:00+00:00',
  kind_count: 3,
  ...extra,
})

const BODY = (over = {}) => ({
  graph_id: 'default',
  revision: 2,
  configured: true,
  packs: [ROW('dev'), ROW('marketing', 'disabled')],
  available: [
    { namespace: 'venture', name: 'Venture', version: '1.0.0', tier: 'free',
      description: 'venture', kind_count: 7, status: 'available' },
    { namespace: 'pm', name: 'PM', version: '1.0.0', tier: 'free',
      description: 'pm', kind_count: 4, status: 'available' },
    { namespace: 'dev', name: 'DEV', version: '1.0.0', tier: 'free',
      description: 'dev', kind_count: 3, status: 'available' },
  ],
  ...over,
})

test('#2728: the Settings home id follows the #3890 heading convention', () => {
  assert.equal(PACK_SETTINGS_SECTION_ID, 'settings-packs-heading')
  assert.match(PACK_SETTINGS_SECTION_ID, /^settings-.+-heading$/)
  // The #3890 deep-link contract is a CLOSED registry (overviewEmptyAction.js
  // SECTION_HASHES), so the id alone does NOT make the home addressable — the
  // source comment used to claim it did. Pin the honest wording so the claim
  // cannot creep back without the registry entry that would make it true.
  const src = readFileSync(join(SRC, 'packs.js'), 'utf8')
  assert.match(src, /LOCAL anchor only/,
    'the id must be documented as a local anchor until SECTION_HASHES registers it')
  assert.doesNotMatch(src, /the id is what makes the home addressable/)
})

test('packErrorState: the code the SERVER sends is error_code, and it is read', () => {
  // The packs routes raise `detail={"error_code": …}` (hosted_api.py), and
  // main.jsx's api() now attaches it as `err.error_code`. A classification
  // pinned ONLY on `err.code` tests a shape this route never produces.
  assert.equal(packErrorState({ error_code: 'PACK_SET_REVISION_CONFLICT', status: 200 }).kind,
    'conflict', 'error_code alone (no matching status) must classify')
  assert.equal(packErrorState({ error_code: 'GRAPH_SCOPED_TEAM_SURFACE', status: 200 }).kind,
    'forbidden')
  // Both fields still work — `code` is the older convention and neither may
  // regress into a status-only fallback.
  assert.equal(packErrorState({ code: 'PACK_SET_REVISION_CONFLICT', status: 200 }).kind,
    'conflict')
})

test('packRows: sorts ACTIVE first then OFF, then by namespace', () => {
  const rows = packRows(BODY())
  assert.deepEqual(rows.map((r) => r.namespace), ['dev', 'marketing'])
  assert.equal(rows[1].statusLabel, 'Off')
  assert.equal(rows[0].statusLabel, 'Active')
})

test('packRows: an unknown/absent status reads as active, never as a fake third state', () => {
  const rows = packRows({ packs: [ROW('a', undefined), ROW('b', null), ROW('c', 'weird')] })
  assert.deepEqual(rows.map((r) => r.status), ['active', 'active', 'active'])
})

test('packRows: never fabricates a kind count — an omitted count is "—"', () => {
  const rows = packRows({ packs: [ROW('a', 'active', { kind_count: null }), ROW('b')] })
  assert.equal(rows[0].kindCount, null)
  assert.equal(rows[0].kindCountLabel, '—')
  assert.equal(rows[1].kindCountLabel, '3 kinds')
})

test('kindCountLabel: singular/plural and 0', () => {
  assert.equal(kindCountLabel(0), '0 kinds')
  assert.equal(kindCountLabel(1), '1 kind')
  assert.equal(kindCountLabel(2), '2 kinds')
  assert.equal(kindCountLabel(undefined), '—')
  assert.equal(kindCountLabel('nope'), '—')
})

test('sourceLabel: starter vs custom vs manual', () => {
  assert.equal(sourceLabel('starter'), 'Starter')
  assert.equal(sourceLabel('custom'), 'Custom')
  assert.equal(sourceLabel(undefined), 'Manual')
})

test('toRow: the Source badge and its label never disagree', () => {
  // FAIL-ON: `source: meta.source || 'starter'` beside
  // `sourceLabel: sourceLabel(meta.source)` labelled a row 'starter' and
  // 'Manual' at the same time — and after a Save the column flipped, because
  // the server then writes source='manual' for a non-starter. One value now
  // feeds both fields.
  const body = BODY({
    available: [
      // what the server now sends for a catalog row: the value the write will
      // store (starter) or 'manual' for a non-starter
      { namespace: 'pm', name: 'PM', kind_count: 4, status: 'available', source: 'manual' },
      { namespace: 'dev2', name: 'DEV2', kind_count: 2, status: 'available', source: 'starter' },
      // a legacy/older row with no source at all must not invent 'Starter'
      { namespace: 'legacy', name: 'Legacy', kind_count: 1, status: 'available' },
    ],
  })
  const rows = draftRows(body, [
    { namespace: 'pm', status: 'active' },
    { namespace: 'dev2', status: 'active' },
    { namespace: 'legacy', status: 'active' },
  ])
  for (const r of rows) {
    assert.equal(r.sourceLabel, sourceLabel(r.source),
      `row ${r.namespace}: the badge (${r.source}) and the label (${r.sourceLabel}) must agree`)
  }
  assert.equal(rows.find((r) => r.namespace === 'pm').sourceLabel, 'Manual')
  assert.equal(rows.find((r) => r.namespace === 'dev2').sourceLabel, 'Starter')
  assert.equal(rows.find((r) => r.namespace === 'legacy').sourceLabel, 'Manual',
    'an unknown source must never be rendered as the confident claim "Starter"')
})

test('addListEmptyLine: an unreadable catalog is never claimed complete', () => {
  // FAIL-ON: the modal rendered "Every pack in the catalog is already on this
  // graph." for `available: []` whatever the reason, including the server's
  // deliberate degradation (`_pack_catalog_rows` returns {} on a registry
  // failure → also `catalog_ok: false`).
  assert.equal(addListEmptyLine({ catalog_ok: true }), ADD_LIST_EMPTY_LINE)
  assert.equal(addListEmptyLine({}), ADD_LIST_EMPTY_LINE)
  const degraded = addListEmptyLine({ catalog_ok: false })
  assert.equal(degraded, ADD_LIST_DEGRADED_LINE)
  assert.doesNotMatch(degraded, /already on this graph/,
    'a failed catalog read must not read as a confident claim about the catalog')
  assert.match(degraded, /could not be read/)
})

test('addablePacks: excludes namespaces already on the graph, and never duplicates', () => {
  const offered = addablePacks(BODY())
  assert.deepEqual(offered.map((r) => r.namespace), ['pm', 'venture'])
  assert.ok(!offered.some((r) => r.namespace === 'dev'),
    'a listed namespace must never be offered for adding')
})

test('addablePacks: a tombstoned namespace is offered back, marked as removed', () => {
  const body = BODY({
    available: [{ namespace: 'custom-thing', name: 'Custom', kind_count: 2,
                  status: 'removed' }],
  })
  const offered = addablePacks(body)
  assert.equal(offered.length, 1)
  assert.equal(offered[0].wasRemoved, true)
  assert.match(offered[0].label, /was removed/,
    'removal must not read as "never existed" — the add-list says what happened')
})

test('addablePacks: tolerates a payload with no available list (degraded catalog)', () => {
  assert.deepEqual(addablePacks({ packs: [] }), [])
  assert.deepEqual(addablePacks(null), [])
})

test('draftRows: the table is the DRAFT — an added pack appears, a removed one does not', () => {
  const body = BODY()
  const draft = draftFromBody(body)
  // The ADD direction: `venture` is only in `available`, so the row's
  // display-only fields come from the label index, never from `body.packs`.
  const withAdded = addToDraft(draft, 'venture')
  const added = draftRows(body, withAdded)
  // active-first, then namespace: the added row is ACTIVE, the disabled one sorts last
  assert.deepEqual(added.map((r) => r.namespace), ['dev', 'venture', 'marketing'])
  const venture = added.find((r) => r.namespace === 'venture')
  assert.equal(venture.status, 'active')
  assert.equal(venture.name, 'Venture', 'a draft-added row still shows the real pack name')
  assert.equal(venture.kindCountLabel, '7 kinds')
  // FAIL-ON: pre-fix the table came from `packRows(body)`, so the added pack
  // was never rendered — Add produced no visible row at all.
  assert.ok(!packRows(body).some((r) => r.namespace === 'venture'))
  // The REMOVE direction: the row is gone from the table the moment it leaves
  // the draft (pre-fix it kept rendering, reading "Active", with a live
  // Remove button — the click appeared to do nothing).
  const withRemoved = removeFromDraft(draft, 'marketing')
  assert.deepEqual(draftRows(body, withRemoved).map((r) => r.namespace), ['dev'])
  assert.ok(packRows(body).some((r) => r.namespace === 'marketing'),
    'the server read still lists it — the draft is what decides')
})

test('draftRows: a status toggle is visible before the save', () => {
  const body = BODY()
  const off = toggleDraftStatus(draftFromBody(body), 'dev')
  const rows = draftRows(body, off)
  assert.equal(rows.find((r) => r.namespace === 'dev').status, 'disabled')
  assert.equal(rows.find((r) => r.namespace === 'dev').statusLabel, 'Off')
})

test('draftRows: sorts active-first and tolerates an empty draft', () => {
  assert.deepEqual(draftRows(BODY(), []), [])
  assert.deepEqual(draftRows(null, [{ namespace: 'z', status: 'active' }]).map((r) => r.namespace),
    ['z'])
  const rows = draftRows(BODY(), [
    { namespace: 'marketing', status: 'disabled' },
    { namespace: 'dev', status: 'active' },
  ])
  assert.deepEqual(rows.map((r) => r.namespace), ['dev', 'marketing'])
})

test('addableFromDraft: a pack the draft just removed is offered back (undo without reload)', () => {
  const body = BODY()
  const draft = removeFromDraft(draftFromBody(body), 'marketing')
  // FAIL-ON: `addablePacks(body)` diffs against the LAST READ, so MARKETING is
  // still "listed" and is NOT offered — the only recovery was Reload, which
  // discards every other edit (the one-way door this surface exists to close).
  assert.ok(!addablePacks(body).some((p) => p.namespace === 'marketing'))
  const offered = addableFromDraft(body, draft)
  assert.ok(offered.some((p) => p.namespace === 'marketing'),
    'a removed-but-unsaved pack must be re-addable')
  // …and a namespace already in the draft is never offered again (a duplicate
  // is a 422 at the server).
  assert.ok(!offered.some((p) => p.namespace === 'dev'))
  const added = addToDraft(draft, 'venture')
  assert.ok(!addableFromDraft(body, added).some((p) => p.namespace === 'venture'),
    'a pack just added must leave the picker, or Add silently no-ops')
})

test('addablePacks: the read-only form is the draft form of the server set', () => {
  const body = BODY()
  assert.deepEqual(addablePacks(body), addableFromDraft(body, draftFromBody(body)))
})

test('packCountLine: counts active, shows the off count, and names the unconfigured caveat', () => {
  assert.equal(packCountLine(BODY()), '1 active pack · 1 off.')
  assert.equal(packCountLine({ configured: true, packs: [] }), '0 active packs.')
  // FAIL-ON: an unconfigured graph resolves to the catalog union. A count line
  // that read "5 active packs" would describe a state the graph does not have.
  const un = packCountLine({ configured: false, packs: [ROW('dev')] })
  assert.match(un, /Not configured yet/)
  assert.match(un, /full catalog applies/)
})

test('selectorGraphs: DEFAULT graph first, then by name', () => {
  const rows = selectorGraphs([
    { graph_id: 'g2', name: 'zeta', kind: 'custom' },
    { graph_id: 'default', name: 'default', kind: 'default' },
    { graph_id: 'g1', name: 'alpha', kind: 'custom' },
  ])
  assert.deepEqual(rows.map((r) => r.graph_id), ['default', 'g1', 'g2'])
})

test('defaultPacksGraphId: the default graph, else the first; null for an org with none', () => {
  assert.equal(defaultPacksGraphId([
    { graph_id: 'g1', name: 'alpha', kind: 'custom' },
    { graph_id: 'default', name: 'default', kind: 'default' },
  ]), 'default')
  assert.equal(defaultPacksGraphId([{ graph_id: 'g1', name: 'alpha', kind: 'custom' }]), 'g1')
  assert.equal(defaultPacksGraphId([]), null)
  assert.equal(defaultPacksGraphId(undefined), null)
})

test('graphLabel: falls back to the id, never to an empty label', () => {
  assert.equal(graphLabel({ graph_id: 'g1', name: 'alpha' }), 'alpha')
  assert.equal(graphLabel({ graph_id: 'g1' }), 'g1')
  assert.equal(graphLabel(null), 'this graph')
})

test('draftFromBody: the draft is the WHOLE set with statuses only', () => {
  assert.deepEqual(draftFromBody(BODY()), [
    { namespace: 'dev', status: 'active' },
    { namespace: 'marketing', status: 'disabled' },
  ])
})

test('toggleDraftStatus: flips active <-> disabled and leaves other rows alone', () => {
  const draft = draftFromBody(BODY())
  const off = toggleDraftStatus(draft, 'dev')
  assert.equal(draftStatus(off, 'dev'), 'disabled')
  assert.equal(draftStatus(off, 'marketing'), 'disabled')
  const on = toggleDraftStatus(off, 'dev')
  assert.equal(draftStatus(on, 'dev'), 'active')
  // an unknown namespace never mints a row
  assert.deepEqual(toggleDraftStatus(draft, 'nope'), draft)
})

test('removeFromDraft / addToDraft: omission is removal, re-add is active', () => {
  const removed = removeFromDraft(draftFromBody(BODY()), 'marketing')
  assert.deepEqual(removed.map((d) => d.namespace), ['dev'])
  const added = addToDraft(removed, 'venture')
  assert.deepEqual(added, [
    { namespace: 'dev', status: 'active' },
    { namespace: 'venture', status: 'active' },
  ])
  // FAIL-ON: a duplicate namespace is a 422 at the server (duplicate namespace)
  assert.deepEqual(addToDraft(added, 'venture'), added)
  assert.deepEqual(addToDraft(added, ''), added)
})

test('packSaveBody: always carries expected_revision (the server 422s without it)', () => {
  const body = packSaveBody(draftFromBody(BODY()), 2)
  assert.deepEqual(body, {
    packs: [{ namespace: 'dev', status: 'active' },
            { namespace: 'marketing', status: 'disabled' }],
    expected_revision: 2,
  })
  // an unknown status is normalised to active, never forwarded as-is
  assert.deepEqual(packSaveBody([{ namespace: 'x', status: 'weird' }], 0).packs,
    [{ namespace: 'x', status: 'active' }])
  // FAIL-ON: `removed` is not an input status — the server refuses it (422).
  assert.deepEqual(packSaveBody([{ namespace: 'x', status: 'removed' }], 0).packs,
    [{ namespace: 'x', status: 'active' }])
  assert.equal(packSaveBody([], 0).expected_revision, 0)
})

test('draftIsDirty: a no-op edit is not dirty; a status flip or a removal is', () => {
  const body = BODY()
  assert.equal(draftIsDirty(body, draftFromBody(body)), false)
  assert.equal(draftIsDirty(body, toggleDraftStatus(draftFromBody(body), 'dev')), true)
  assert.equal(draftIsDirty(body, removeFromDraft(draftFromBody(body), 'dev')), true)
  assert.equal(draftIsDirty(body, addToDraft(draftFromBody(body), 'venture')), true)
  // row ORDER alone is not a change (the PUT is a set)
  const reordered = draftFromBody(body).reverse()
  assert.equal(draftIsDirty(body, reordered), false)
})

test('packErrorState: one distinct state per failure, keyed on code or status', () => {
  assert.equal(packErrorState({ status: 409 }).kind, 'conflict')
  assert.equal(packErrorState({ code: 'PACK_SET_REVISION_CONFLICT', status: 200 }).kind,
    'conflict')
  // FAIL-ON: a 403 must NOT read as a conflict — its remedy is a role, not a reload
  const forbidden = packErrorState({ status: 403 })
  assert.equal(forbidden.kind, 'forbidden')
  assert.match(forbidden.message, /owner or admin/)
  assert.equal(packErrorState({ status: 404 }).kind, 'missing')
  assert.equal(packErrorState({ status: 503 }).kind, 'unavailable')
  assert.equal(packErrorState({ status: 429 }).kind, 'unavailable')
  const generic = packErrorState(new Error('boom'))
  assert.equal(generic.kind, 'error')
  assert.equal(generic.message, 'boom')
  assert.match(packErrorState(undefined).message, /Couldn/)
})

test('revisionMovedAfterSave: an out-of-band write is surfaced, not swallowed', () => {
  assert.equal(revisionMovedAfterSave(3, { revision: 3 }), false)
  assert.equal(revisionMovedAfterSave(3, { revision: 4 }), true)
  assert.equal(revisionMovedAfterSave(3, null), false)
  // The predicate's input is a FRESH read taken after the save. The PUT
  // response's own revision is one ahead by construction (the server bumps on
  // every effective write), so feeding it here reports a false conflict on
  // every save — the modal, not this predicate, owns that distinction
  // (packsModalTripwire pins it).
  assert.equal(revisionMovedAfterSave(1, { revision: 2 }), true)
})

test('consequence copy: one vocabulary, each verb names what happens to existing data', () => {
  for (const k of ['enable', 'disable', 'remove', 'add', 'unconfigured']) {
    assert.equal(typeof PACK_CONSEQUENCE[k], 'string', `${k} consequence copy`)
    assert.ok(PACK_CONSEQUENCE[k].length > 20, `${k} copy must be a real sentence`)
  }
  // The #2361 vocab anchor is DECIDED: the graph's contents are "memories" on
  // user-facing surfaces, never "points". The consequence copy is user-facing.
  assert.doesNotMatch(PACK_CONSEQUENCE.disable, /\bpoints?\b/)
  assert.doesNotMatch(PACK_CONSEQUENCE.remove, /\bpoints?\b/)
  // The verbs must describe the RESOLUTION CONTRACT, not a stronger promise:
  // `pack_state.resolved_pack_set` (marker present) is
  // `active ∪ the namespaces already in the graph's data` — a NAMESPACE set,
  // consumed whole by both the write gate (compile_vocab) and the extraction
  // classifier, so every kind of an admitted namespace stays classifiable.
  // "Stops new classification into this pack's kinds." is false for precisely
  // the packs whose existing memories the next clause is about, and a
  // KIND-granular qualifier would be false for the same reason — the copy must
  // speak in PACKS (namespaces), which is what the gates actually decide.
  //
  // FAIL-ON (pre-fix): the copy read "Stops new classification into this
  // pack’s kinds. Memories already extracted stay searchable." — the two
  // assertions below red on that unqualified full stop and on the missing
  // namespace-level qualification.
  assert.match(PACK_CONSEQUENCE.disable, /holds no memories from yet/,
    'disable must be qualified at NAMESPACE granularity: a pack with any data here is re-admitted whole and stays classifiable')
  assert.doesNotMatch(PACK_CONSEQUENCE.disable, /kinds\.\s/,
    'no unconditional "stops new classification into this pack’s kinds."')
  assert.match(PACK_CONSEQUENCE.disable, /kinds already here stay searchable and writable/)
  // …and the qualification must NOT be kind-scoped: a pack with any data is
  // re-admitted whole, so "kinds this graph has no memories of yet" is false.
  assert.doesNotMatch(PACK_CONSEQUENCE.disable, /kinds (this|the) graph\b/)
  // Same overclaim on the stronger verb: removal does not delete memories and
  // does not stop classifying kinds the graph already holds — it takes the
  // namespace out of the APPROVED set.
  assert.match(PACK_CONSEQUENCE.remove, /leaves the approved set/)
  assert.doesNotMatch(PACK_CONSEQUENCE.remove, /no longer approved/,
    'removal is not "unapproved data": the data leg keeps the namespace writable')
  assert.match(PACK_CONSEQUENCE.remove, /no memory is deleted/)
  assert.equal(PACK_STATUS_LABEL.disabled, 'Off',
    'disable-first: the row verb is Off, not "uninstalled"')
})
