// packs.test.js — #2728 Configured packs: the pure derivations behind the
// Settings home + modal. Run with `node --test src/*.test.js` (Node 20+, zero
// deps — the dashboard's own runner). The render/wiring half is pinned by
// packsModalTripwire.test.js; this file pins the DECISIONS.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  PACK_CONSEQUENCE,
  PACK_SETTINGS_SECTION_ID,
  PACK_STATUS_LABEL,
  addToDraft,
  addablePacks,
  defaultPacksGraphId,
  draftFromBody,
  draftIsDirty,
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

test('#2728: the Settings home id follows the #3890 deep-link convention', () => {
  assert.equal(PACK_SETTINGS_SECTION_ID, 'settings-packs-heading')
  assert.match(PACK_SETTINGS_SECTION_ID, /^settings-.+-heading$/)
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
})

test('consequence copy: one vocabulary, each verb names what happens to existing data', () => {
  for (const k of ['enable', 'disable', 'remove', 'add', 'unconfigured']) {
    assert.equal(typeof PACK_CONSEQUENCE[k], 'string', `${k} consequence copy`)
    assert.ok(PACK_CONSEQUENCE[k].length > 20, `${k} copy must be a real sentence`)
  }
  // The #2361 vocab anchor is DECIDED: the graph's contents are "memories" on
  // user-facing surfaces, never "points". The consequence copy is user-facing.
  assert.match(PACK_CONSEQUENCE.disable, /Memories already extracted stay searchable/)
  assert.doesNotMatch(PACK_CONSEQUENCE.disable, /\bpoints?\b/)
  assert.doesNotMatch(PACK_CONSEQUENCE.remove, /\bpoints?\b/)
  assert.match(PACK_CONSEQUENCE.remove, /no longer approved/)
  assert.equal(PACK_STATUS_LABEL.disabled, 'Off',
    'disable-first: the row verb is Off, not "uninstalled"')
})
