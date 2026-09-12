// #2784: structural tripwire for the Graphs table's Last-backup column.
//
// Why this file exists: the dashboard's ONLY pre-existing placement guard for
// this surface was vacuous (#3245 — it asserted a string literal that exists
// nowhere and sliced the Overview branch, not the Graphs table). Every
// assertion here must be able to FAIL: each is anchored to a concrete slice
// whose length is asserted non-zero first, so a moved/renamed region breaks
// the anchor instead of silently passing.
//
// The last case is a DIST-SYNC guard: CI never builds the dashboard bundle
// (`ci.yml` documents the blind spot), so a source-only change ships the old
// bundle with a green suite. That case fails until `npm run build` is run.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const mainJsx = readFileSync(join(HERE, 'main.jsx'), 'utf8')
const css = readFileSync(join(HERE, 'index.css'), 'utf8')

// The Graphs table = the thead whose first two columns are Name | Kind
// (the keys table is Name | Prefix | …), through its own </table>.
const GRAPHS_TABLE_ANCHOR = '<th scope="col">Name</th><th scope="col">Kind</th>'
const tableStart = mainJsx.indexOf(GRAPHS_TABLE_ANCHOR)
const tableEnd = mainJsx.indexOf('</table>', tableStart)
const graphsTable = mainJsx.slice(tableStart, tableEnd)

// GraphBackupCell body = the component definition to the next top-level
// `function` (its own slice, so a claim it makes cannot be satisfied by some
// other component's markup).
const cellStart = mainJsx.indexOf('function GraphBackupCell(')
const cellEnd = mainJsx.indexOf('\nfunction ', cellStart + 1)
const cellBody = mainJsx.slice(cellStart, cellEnd)

test('#2784 tripwire: the anchors resolve to real regions (non-vacuity guard)', () => {
  assert.ok(tableStart !== -1, 'graphs-table thead anchor found')
  assert.ok(graphsTable.length > 0, 'graphs-table slice non-empty')
  assert.ok(/No graphs yet/.test(graphsTable), 'the slice is the GRAPHS table, not another table')
  assert.ok(cellStart !== -1 && cellBody.length > 0, 'GraphBackupCell body slice non-empty')
})

test('#2784 tripwire: the Graphs table declares exactly six columns, Last backup before Actions', () => {
  const header = mainJsx.slice(tableStart, mainJsx.indexOf('</thead>', tableStart))
  const ths = header.match(/<th[ >]/g) || []
  assert.equal(ths.length, 6, `expected 6 <th>, got ${ths.length}`)
  assert.match(header, /<th scope="col">Last backup<\/th>/, 'Last backup header present')
  assert.ok(header.indexOf('Last backup') > header.indexOf('Keys'),
    'Last backup sits AFTER Keys — the three e2e locators index the Keys cell positionally (nth(3))')
  assert.match(header, /<span className="sr-only">Actions<\/span><\/th><\/tr>$/, 'Actions stays last')
  const scoped = header.match(/<th scope="col">/g) || []
  assert.equal(scoped.length, 6, `every Graphs <th> must carry scope="col" (got ${scoped.length})`)
})

test('#2784 tripwire: all four empty-state rows carry colSpan="6" and none is left at 5', () => {
  const spans6 = graphsTable.match(/colSpan="6"/g) || []
  const spans5 = graphsTable.match(/colSpan="5"/g) || []
  assert.equal(spans6.length, 4, `expected 4 colSpan="6" rows (loading/denied/error/empty), got ${spans6.length}`)
  assert.equal(spans5.length, 0, 'no empty-state row may keep the 5-column span')
})

test('#2784 tripwire: the data row renders the cell from the row object (identity join, not an index)', () => {
  assert.match(graphsTable, /<GraphBackupCell g=\{g\} summary=\{graphBackups\} status=\{backupsStatus\} \/>/,
    'the cell is fed the row object g — positionally/index-precomputed wiring would mis-attribute every row')
  // The cell sits between the Keys cell and the actions cell.
  assert.ok(graphsTable.indexOf('GraphBackupCell') < graphsTable.indexOf('graph-actions'),
    'the Last-backup cell precedes the actions cell')
})

test('#2784 tripwire: the neutral empty copy is produced ONLY by the pure state function', () => {
  // A regression that hard-codes the label into the component would bypass
  // graphBackupCellState's precedence rules (and could emit it during an
  // outage). The component must never contain the literal.
  assert.ok(!/None recorded/.test(cellBody),
    'GraphBackupCell must not hard-code the empty-state copy — it comes from graphBackupCellState')
  assert.match(cellBody, /graphBackupCellState\(g, summary, status, now\)/, 'the cell renders the derived state')
  assert.ok(!/stale|Stale|threshold/i.test(cellBody),
    'no client-computed staleness verdict — the authoritative threshold is server-side + internal-auth only')
})

test('#2784 tripwire: the cell keeps its own relative-time ticker (never an App-scope interval)', () => {
  assert.match(cellBody, /setInterval\(\(\) => setNow\(Date\.now\(\)\), 30_000\)/, 'component-local 30s ticker (#1894 pattern)')
  assert.match(cellBody, /clearInterval\(t\)/, 'ticker cleanup')
})

test('#2784 tripwire: loadBackups retains the payload and the pool is grouped once per payload', () => {
  assert.match(mainJsx, /backups: list \}/, 'the manifest array is retained in backupInfo')
  assert.match(mainJsx, /setBackupInfo\(list\.length[\s\S]{0,120}backups: list \}/, 'retained on the list path')
  assert.match(mainJsx, /backups: \[\] \}/, 'and cleared to an empty array on the empty path')
  assert.match(mainJsx, /const graphBackups = React\.useMemo\(\s*\n\s*\(\) => graphBackupSummary\(backupInfo && backupInfo\.backups\), \[backupInfo\]\)/,
    'grouped via useMemo keyed to backupInfo (O(pool) per payload, not per row)')
  // The retained array must live INSIDE backupInfo so the team-switch/logout
  // wipes clear it — a separate state would leak the previous team's default
  // row (its bucket key is the literal 'default' in every team).
  assert.match(mainJsx, /setBackupInfo\(null\)/,
    'the backupInfo wipe (team switch / logout) still clears the retained array with the rest of the state')
})

test('#2784 tripwire: the Graphs table is wrapped in the mobile scroller', () => {
  assert.match(mainJsx, /<div className="graphs-table-wrap">/, 'wrapper div present')
  assert.match(css, /\.graphs-table-wrap \{ overflow-x: auto; \}/, 'CSS scroller rule')
  assert.match(css, /\.graphs-table-wrap table \{ min-width: 560px; \}/, 'CSS min-width rule')
  const open = mainJsx.indexOf('<div className="graphs-table-wrap">')
  const close = mainJsx.indexOf('</div>', mainJsx.indexOf('</table>', open))
  assert.ok(open !== -1 && close !== -1 && close > open, 'wrapper opens before and closes after the table')
})

test('#2784 tripwire: the committed dist bundle contains the new column (rebuild guard)', () => {
  const assetsDir = join(HERE, '..', 'dist', 'assets')
  const bundles = readdirSync(assetsDir).filter((f) => /^index-.*\.js$/.test(f))
  assert.ok(bundles.length > 0, 'a built index-*.js bundle exists')
  const bundle = bundles.map((f) => readFileSync(join(assetsDir, f), 'utf8')).join('\n')
  assert.match(bundle, /Last backup/, 'the built bundle ships the Last backup column — run `npm run build`')
  assert.match(bundle, /graph-backup/, 'the built bundle ships the cell')
  assert.match(bundle, /None recorded/, 'the built bundle ships the neutral empty copy')
})
