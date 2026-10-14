// #2784: per-graph "last backup" derivations — pure, node --test.
// The Graphs table's Last-backup column is driven entirely by these three
// functions; main.jsx only renders their output.
import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  BACKUP_LOADING_FLOOR_MS,
  graphBackupBucketKey,
  graphBackupSummary,
  graphBackupCellState,
  teamBackupsEnabled,
} from './graphs.js'

const NOW = Date.parse('2026-09-12T00:00:00Z')

// ── graphBackupBucketKey ────────────────────────────────────────────────

test('#2784 bucketKey: the default row resolves by kind, not graph_id', () => {
  // Registry lane serves the default row with the node's random uuid while
  // every default-graph artifact is keyed 'default'. A raw graph_id join
  // would show the default row as unbacked-up on self-host deployments.
  assert.equal(graphBackupBucketKey({ graph_id: 'g_9f3a11bc', kind: 'default' }), 'default')
})

test('#2784 bucketKey: a custom row keys on its own graph_id', () => {
  assert.equal(graphBackupBucketKey({ graph_id: 'g_prod', kind: 'custom' }), 'g_prod')
})

test('#2784 bucketKey: manifests and rows resolve through the same function', () => {
  // The payload lane and the row lane must be provably symmetric — the
  // server injects kind on every manifest (_manifest_graph), including the
  // legacy-flat manifests it buckets to the default graph.
  assert.equal(graphBackupBucketKey({ graph_id: 'default', kind: 'default' }), 'default')
  assert.equal(graphBackupBucketKey({ graph_id: 'g_prod', kind: 'custom' }), 'g_prod')
})

test('#2784 bucketKey: unattributable input maps to the sentinel, never a real key', () => {
  assert.equal(graphBackupBucketKey(null), '')
  assert.equal(graphBackupBucketKey(undefined), '')
  assert.equal(graphBackupBucketKey({}), '')
  assert.equal(graphBackupBucketKey({ graph_id: '' }), '')
})

// ── graphBackupSummary ──────────────────────────────────────────────────

test('#2784 summary: newest parseable created_at wins, regardless of input order', () => {
  // list_backups sorts newest-first, but a manifest with no created_at sorts
  // LAST rather than being rejected — so input order must never be trusted.
  const s = graphBackupSummary([
    { graph_id: 'g_a', kind: 'custom', created_at: '2026-09-01T00:00:00+00:00' },
    { graph_id: 'g_a', kind: 'custom', created_at: '2026-09-11T00:00:00+00:00' },
    { graph_id: 'g_a', kind: 'custom', created_at: '2026-09-05T00:00:00+00:00' },
  ])
  assert.equal(s.g_a.lastBackupAt, '2026-09-11T00:00:00+00:00')
  assert.equal(s.g_a.count, 3)
})

test('#2784 summary: an unreadable/absent timestamp never wins', () => {
  const s = graphBackupSummary([
    { graph_id: 'g_a', kind: 'custom', created_at: null },
    { graph_id: 'g_a', kind: 'custom', created_at: 'not-a-date' },
    { graph_id: 'g_a', kind: 'custom', created_at: '2026-09-01T00:00:00Z' },
  ])
  assert.equal(s.g_a.lastBackupAt, '2026-09-01T00:00:00Z')
  assert.equal(s.g_a.count, 3)
})

test('#2784 summary: a bucket with only unreadable timestamps has no lastBackupAt (not null-vs-empty confusion)', () => {
  const s = graphBackupSummary([{ graph_id: 'g_a', kind: 'custom', created_at: 'garbage' }])
  assert.equal(s.g_a.lastBackupAt, null)
  assert.equal(s.g_a.count, 1)
})

test('#2784 summary: per-graph buckets never cross-credit (no prefix/substring match)', () => {
  const s = graphBackupSummary([
    { graph_id: 'g_prod', kind: 'custom', created_at: '2026-09-11T00:00:00Z' },
    { graph_id: 'g_prod2', kind: 'custom', created_at: '2026-09-02T00:00:00Z' },
  ])
  assert.equal(s.g_prod.lastBackupAt, '2026-09-11T00:00:00Z')
  assert.equal(s.g_prod2.lastBackupAt, '2026-09-02T00:00:00Z')
})

test('#2784 summary: a legacy-flat manifest keys to the default bucket', () => {
  const s = graphBackupSummary([{ backup_id: 'team/20260911T000000Z_ab12', graph_id: 'default', kind: 'default', created_at: '2026-09-11T00:00:00Z' }])
  assert.equal(s.default.count, 1)
})

test('#2784 summary: an entry with NO graph_id is never credited to a real graph', () => {
  // The server injects graph_id on every entry, so a keyless manifest is an
  // anomaly: guessing provenance would fabricate attribution. It lands in
  // the '' sentinel bucket, which no row can read.
  const s = graphBackupSummary([{ id: 'bk-b1' }])   // the keys-tab e2e fixture shape
  assert.equal(s.default, undefined)
  assert.equal(s.g_prod, undefined)
  assert.equal(s[''].count, 1)
})

test('#2784 summary: null / non-array / junk-safe', () => {
  // Spread: the map has no prototype (Object.create(null)), so a strict deep
  // compare against {} would fail on the prototype, not on the contents.
  assert.deepEqual({ ...graphBackupSummary(null) }, {})
  assert.deepEqual({ ...graphBackupSummary(undefined) }, {})
  assert.deepEqual({ ...graphBackupSummary('nope') }, {})
  assert.deepEqual({ ...graphBackupSummary([null, 1, 'x']) }, {})
})

test('#2784 summary: a prototype-named graph_id cannot pollute Object.prototype', () => {
  const s = graphBackupSummary([
    { graph_id: '__proto__', kind: 'custom', created_at: '2026-09-11T00:00:00Z' },
    { graph_id: 'constructor', kind: 'custom', created_at: '2026-09-11T00:00:00Z' },
  ])
  assert.equal(s['__proto__'].count, 1)
  assert.equal(s.constructor.count, 1)
  assert.equal(({}).count, undefined, 'Object.prototype must be untouched')
})

// ── graphBackupCellState ────────────────────────────────────────────────

test('#2784 cell: a backed-up graph renders the relative time + a localized/ISO title', () => {
  const s = graphBackupSummary([{ graph_id: 'g_prod', kind: 'custom', created_at: '2026-09-11T22:00:00Z' }])
  const st = graphBackupCellState({ graph_id: 'g_prod', kind: 'custom' }, s, 'ok', NOW)
  assert.equal(st.kind, 'ok')
  assert.equal(st.label, '2 hr ago')
  assert.equal(st.at, '2026-09-11T22:00:00Z')
  // Localized for reading, wire value in parentheses (the keys-table
  // Last-used precedent — a bare ISO reads as ambiguous to a non-UTC user).
  assert.match(st.title, /^Last backup: .+\(2026-09-11T22:00:00Z\)$/)
})

test('#2784 cell: ≥24h degrades to an absolute date (the retained-pool case)', () => {
  // formatRelativeTime returns a locale date past 24h — daily backups are the
  // common case, so this branch is the headline, not an edge.
  const s = graphBackupSummary([{ graph_id: 'g_prod', kind: 'custom', created_at: '2026-09-01T00:00:00Z' }])
  const st = graphBackupCellState({ graph_id: 'g_prod', kind: 'custom' }, s, 'ok', NOW)
  assert.equal(st.kind, 'ok')
  assert.equal(st.label, new Date(Date.parse('2026-09-01T00:00:00Z')).toLocaleDateString())
  assert.ok(!/ago/.test(st.label))
  assert.match(st.title, /2026-09-01T00:00:00Z/)
})

test('#2784 cell: the registry-lane default row is credited from the default bucket', () => {
  const s = graphBackupSummary([{ graph_id: 'default', kind: 'default', created_at: '2026-09-11T23:00:00Z' }])
  const st = graphBackupCellState({ graph_id: 'g_9f3a11bc', kind: 'default' }, s, 'ok', NOW)
  assert.equal(st.kind, 'ok')
  assert.equal(st.label, '1 hr ago')
})

test('#2784 cell: an unavailable list is NOT an empty-graph claim', () => {
  const st = graphBackupCellState({ graph_id: 'g_prod', kind: 'custom' }, {}, 'error', NOW)
  assert.equal(st.kind, 'unavailable')
  assert.equal(st.label, '—')
  assert.ok(!/none recorded/i.test(st.label))
  assert.match(st.title, /unavailable/i)
})

test('#2784 cell: an unknown status (null/undefined) is treated as unavailable, not empty', () => {
  assert.equal(graphBackupCellState({ graph_id: 'g_a' }, {}, null, NOW).kind, 'unavailable')
  assert.equal(graphBackupCellState({ graph_id: 'g_a' }, {}, undefined, NOW).kind, 'unavailable')
})

test('#2784 cell: loading is distinct from unavailable and from empty', () => {
  const st = graphBackupCellState({ graph_id: 'g_prod', kind: 'custom' }, {}, 'loading', NOW)
  assert.equal(st.kind, 'loading')
  assert.equal(st.label, '…')
  assert.ok(!/none recorded/i.test(st.label))
})

test('#2784 cell: known-empty copy is neutral — never a causal "never"', () => {
  const st = graphBackupCellState({ graph_id: 'g_prod', kind: 'custom' }, {}, 'ok', NOW)
  assert.equal(st.kind, 'none')
  assert.equal(st.label, 'None recorded')
  assert.ok(!/never/i.test(st.label))
  assert.ok(!/enabled|plan|entitled/i.test(st.label))
})

test('#2784 cell: a graph with no bucket reads empty even when another graph has backups', () => {
  const s = graphBackupSummary([{ graph_id: 'g_other', kind: 'custom', created_at: '2026-09-11T00:00:00Z' }])
  assert.equal(graphBackupCellState({ graph_id: 'g_prod', kind: 'custom' }, s, 'ok', NOW).kind, 'none')
})

test('#2784 cell: a bucket with no parseable timestamp gets its OWN label, not the unavailable dash', () => {
  const s = graphBackupSummary([{ graph_id: 'g_prod', kind: 'custom', created_at: 'garbage' }])
  const st = graphBackupCellState({ graph_id: 'g_prod', kind: 'custom' }, s, 'ok', NOW)
  assert.equal(st.kind, 'unknown')
  assert.equal(st.label, 'No timestamp')
  assert.notEqual(st.label, graphBackupCellState({ graph_id: 'g_prod', kind: 'custom' }, {}, 'error', NOW).label,
    'unknown and unavailable must not render the same glyph')
  assert.equal(st.count, 1)
  assert.match(st.title, /1 on record/)
})

test('#2784 cell: the sentinel bucket is unreachable from any row', () => {
  const s = graphBackupSummary([{ id: 'bk-b1' }])
  assert.equal(graphBackupCellState({ graph_id: 'default', kind: 'default' }, s, 'ok', NOW).kind, 'none')
  assert.equal(graphBackupCellState({ graph_id: 'g_prod', kind: 'custom' }, s, 'ok', NOW).kind, 'none')
})

test('#2784 cell: a falsy row is empty-safe, never a crash', () => {
  assert.equal(graphBackupCellState(null, {}, 'ok', NOW).kind, 'none')
})

// ── #3376: team eligibility + the shared loading floor ───────────────────

test('#3376 teamBackupsEnabled: free is ineligible regardless of the stored flag', () => {
  // The sweep predicate is `tier != 'free' AND backup_enabled` — a free org can
  // never be swept, so a stale/odd backup_enabled=true must not read as enabled.
  assert.equal(teamBackupsEnabled({ tier: 'free', backup_enabled: true }), false)
  assert.equal(teamBackupsEnabled({ tier: 'free', backup_enabled: false }), false)
  // `anon` is the derived tier for an unclaimed free org (same ineligibility).
  assert.equal(teamBackupsEnabled({ tier: 'anon', backup_enabled: true }), false)
})

test('#3376 teamBackupsEnabled: a PAID team reads the server flag (the case tier alone cannot answer)', () => {
  assert.equal(teamBackupsEnabled({ tier: 'pro', backup_enabled: true }), true)
  assert.equal(teamBackupsEnabled({ tier: 'pro', backup_enabled: false }), false)
  assert.equal(teamBackupsEnabled({ tier: 'solo', backup_enabled: false }), false)
})

test('#3376 teamBackupsEnabled: an absent flag is UNKNOWN, never a disabled claim', () => {
  // An older /v1/team payload or an additive read that degraded must not be
  // reported as "backups are off" — the column keeps the neutral empty copy.
  assert.equal(teamBackupsEnabled({ tier: 'pro' }), null)
  assert.equal(teamBackupsEnabled({ tier: 'pro', backup_enabled: null }), null)
  assert.equal(teamBackupsEnabled(null), null)
  assert.equal(teamBackupsEnabled(undefined), null)
})

test('#3376 cell: an ineligible team states that backups are off, not "None recorded"', () => {
  const st = graphBackupCellState({ graph_id: 'g_prod', kind: 'custom' }, {}, 'ok', NOW, false)
  assert.equal(st.kind, 'disabled')
  assert.equal(st.label, 'Not enabled')
  assert.match(st.title, /aren't enabled for this team/i)
  assert.notEqual(st.label, graphBackupCellState({ graph_id: 'g_prod', kind: 'custom' }, {}, 'ok', NOW, true).label)
})

test('#3376 cell: disabled is team-level — the same on every row, default and custom', () => {
  const s = graphBackupSummary([])
  assert.equal(graphBackupCellState({ graph_id: 'default', kind: 'default' }, s, 'ok', NOW, false).kind, 'disabled')
  assert.equal(graphBackupCellState({ graph_id: 'g_a', kind: 'custom' }, s, 'ok', NOW, false).kind, 'disabled')
})

test('#3376 cell: eligible and unknown eligibility BOTH keep the neutral "None recorded"', () => {
  const row = { graph_id: 'g_prod', kind: 'custom' }
  assert.equal(graphBackupCellState(row, {}, 'ok', NOW, true).kind, 'none')
  assert.equal(graphBackupCellState(row, {}, 'ok', NOW, null).kind, 'none')
  assert.equal(graphBackupCellState(row, {}, 'ok', NOW, undefined).kind, 'none')
  assert.equal(graphBackupCellState(row, {}, 'ok', NOW).kind, 'none')
})

test('#3376 cell: a recorded backup outranks the disabled state (never hide real evidence)', () => {
  const row = { graph_id: 'g_prod', kind: 'custom' }
  const s = graphBackupSummary([{ graph_id: 'g_prod', kind: 'custom', created_at: '2026-09-11T22:00:00Z' }])
  const st = graphBackupCellState(row, s, 'ok', NOW, false)
  assert.equal(st.kind, 'ok')
  assert.equal(st.label, '2 hr ago')
})

test('#3376 cell: the loading state has a terminal floor at the shared window', () => {
  const row = { graph_id: 'g_prod', kind: 'custom' }
  // Inside the window: the honest loading glyph.
  const loading = graphBackupCellState(row, {}, 'loading', NOW, undefined, false)
  assert.equal(loading.kind, 'loading')
  assert.equal(loading.label, '…')
  // Past the floor: the SAME unavailable state a failed list renders — never an
  // eternal `…`, and never a negative claim about the graph.
  const floored = graphBackupCellState(row, {}, 'loading', NOW, undefined, true)
  assert.equal(floored.kind, 'unavailable')
  assert.equal(floored.label, '—')
  assert.match(floored.title, /unavailable/i)
})

test('#3376: the shared loading floor is the BackupsCard\'s 15s window', () => {
  assert.equal(BACKUP_LOADING_FLOOR_MS, 15_000)
})
