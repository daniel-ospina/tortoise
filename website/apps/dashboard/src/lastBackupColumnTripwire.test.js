// lastBackupColumnTripwire.test.js — run with node --test (Node 20+, zero
// deps). #3136 structural tripwire for Part 2: backups are PER GRAPH, so the
// team-wide count card must never come back to the API Keys tab, and the
// Graphs table must carry a per-graph "Last backup" column fed by the pure
// derivations (graphs.js), member-visible and honest about load failures.
//
// Reads main.jsx as TEXT (keyExpiryTripwire.test.js /
// graphRenameDeleteTripwire.test.js pattern — main.jsx has no component
// harness). The pure logic itself is unit-tested in graphs.test.js; this file
// pins the WIRING so a future edit cannot silently re-derive the column, drop
// the error disclosure, or re-add the wrong-metric count.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const __dirname = dirname(fileURLToPath(import.meta.url))
const src = readFileSync(join(__dirname, 'main.jsx'), 'utf8')

test('#3136: the team-wide BackupsCard (wrong metric, wrong tab) is gone', () => {
  assert.ok(!src.includes('function BackupsCard'),
    'BackupsCard component must be deleted — backups are per graph')
  assert.ok(!src.includes('<BackupsCard'),
    'no orphan <BackupsCard render may survive the removal')
  // the wrong-metric projection must not come back
  assert.ok(!src.includes('count: list.length'),
    'the {latest,count} projection must not return — count is the wrong metric')
  assert.ok(!src.includes('{ latest: list[0], count:'),
    'the {latest,count} projection must not return')
})

test('#3136: Graphs table renders the Last backup column before Actions', () => {
  const start = src.indexOf('<thead><tr><th>Name</th>')
  assert.notEqual(start, -1, 'Graphs <thead> anchor missing')
  const end = src.indexOf('</thead>', start)
  const thead = src.slice(start, end)
  const order = ['<th>Name</th>', '<th>Kind</th>', '<th>Status</th>', '<th>Keys</th>', 'Last backup', 'sr-only">Actions']
  let at = -1
  for (const cell of order) {
    const i = thead.indexOf(cell)
    assert.notEqual(i, -1, `Graphs <thead> is missing ${cell}`)
    assert.ok(i > at, `Graphs <thead> column order broke at ${cell}`)
    at = i
  }
})

test('#3136: Graphs placeholder rows span the new 6-column table', () => {
  // Exactly the four empty/loading/denied/error placeholder rows carry the
  // span; a stale colSpan="5" would leave a short row beside the new column.
  assert.equal((src.match(/colSpan="6"/g) || []).length, 4,
    'the four Graphs placeholder rows must span all 6 columns')
  assert.ok(!src.includes('colSpan="5"'),
    'no Graphs placeholder row may still span the old 5 columns')
})

test('#3136: the cell is fed by the pure helpers, memoized', () => {
  assert.ok(src.includes('lastBackupCell(g, backupByGraph, backupsStatus,'),
    'the row cell must call the pure lastBackupCell helper')
  assert.ok(src.includes('React.useMemo(\n    () => lastBackupAtByGraph(backupManifests)'),
    'backupByGraph must be a memoized lastBackupAtByGraph derivation')
  assert.ok(src.includes('useState(null)') && src.includes('backupManifests'),
    'the raw manifest list must be the source of truth')
})

test('#3136: the Last backup cell is NOT role-gated (member parity)', () => {
  // The removed BackupsCard was ungated; wrapping the new cell in
  // isOwnerAdmin would REDUCE member visibility below the old baseline. Slice
  // backward to the preceding <td so a role gate wrapping the whole cell
  // cannot evade the check.
  const anchor = src.indexOf('lastBackupCell(g, backupByGraph,')
  assert.notEqual(anchor, -1, 'cell anchor missing')
  const cellStart = src.lastIndexOf('<td', anchor)
  const cellEnd = src.indexOf('graph-actions', anchor)
  assert.ok(cellStart !== -1 && cellEnd !== -1 && cellStart < cellEnd, 'could not isolate the cell')
  const cell = src.slice(cellStart, cellEnd)
  assert.ok(!cell.includes('isOwnerAdmin'),
    'the Last backup cell must not acquire an isOwnerAdmin role gate')
})

test('#3136: a failed /backups load is disclosed in the header, not read as "none"', () => {
  assert.ok(src.includes("backupsStatus === 'error' && <span className=\"dim\"> (couldn't load)</span>"),
    'the Last backup header must name the load failure')
})

test('#3136: the #1923 overview invariant is untouched (mount-time fetch kept)', () => {
  assert.ok(src.includes("overviewDataComplete = !!team && graphsStatus !== 'loading' && membersStatus !== 'loading' && backupsStatus !== 'loading'"),
    'backupsStatus must stay a term of overviewDataComplete — a lazy fetch refactor must not drop it')
})
