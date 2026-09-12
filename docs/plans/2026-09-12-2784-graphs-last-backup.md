<!-- research-path: docs/research/2026-09-10-per-graph-backups-ux.md -->
<!-- issue-scoping: v5.1 double diamond + verify — daniel-ospina/tortoise#2784 -->
<!-- plan-review: cycles=1, status=resolved-inline, version=2.3.0 -->

# Graphs table: per-graph "last backup" Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Show, in the hosted dashboard's Graphs table, when each graph last had a backup.

**Team:** epistemic-team
**Tier:** Standard (`complexity:standard`) · **UX_RATING:** standard · **ARCH_RATING:** low — as scoped, *no server change*

**Architecture:** The `/backups` payload the dashboard already fetches on login / team switch carries every manifest enriched with `graph_id` + `kind` (`_manifest_graph`, `tortoise/hosted_api.py:19632`). The client currently destroys that array (`main.jsx:5216`: `setBackupInfo(list.length ? { latest: list[0], count: list.length } : …)`), so the value is already on the wire at zero marginal cost — this plan retains it and derives a per-graph "newest parseable `created_at`" in a pure `graphs.js` helper, rendered as a new **Last backup** column. No API change, no new endpoint, no new R2 enumeration. The three states that cannot support a positive claim (loading / list unavailable / nothing recorded / unparseable timestamp — five states in total, see Task 1) are distinguished, because `/backups` returns a terminal 503 on storage failure and #1923 established that this surface must never render an unknown as an assertion.

## Prior context (this issue's scoping record)

`issue-scoping` double diamond ran on #2784: 2 diverge + 2 converge + 2 solution agents, all claims re-verified against HEAD. Findings that shape this plan:

| Finding | Evidence | Consequence for this plan |
|---|---|---|
| The card's `count` is a **pool-wide manifest count**, not a per-graph figure | `main.jsx:5193`; `hosted_api.py:19718` (`graph_id=None`); `hosted_backup.py:962` | The new column must NOT reuse `count`. Per-graph derivation is new work. |
| The default row's `graph_id` is **lane-dependent** (literal `'default'` in supabase; the registry node's random uuid in registry) while every default-graph artifact is keyed `'default'` | `sdk.py:14719`/`:14757` vs `backup_sweep.py:361`; `hosted_api.py:19646` | The row→bucket join key **must** be `kind`-based (`isDefaultGraph`), never raw `graph_id` — otherwise the default row reads "no backup" on self-host deployments. Load-bearing. |
| An entry with no usable `graph_id` is an anomaly (the server always injects one) | `_manifest_graph`, `hosted_api.py:19643-19649` | Unkeyed entries are bucketed under a key **no row matches** — never guessed onto the default row. |
| `GET /backups` has **no** `graph_id` param and FastAPI ignores undeclared query params | `hosted_api.py:19702-19703`, `:19718` | The per-row lazy-fetch design is wrong *today* (silently returns the whole pool); rejected. |
| `POST /backups/restore` refuses custom graphs; `#2339` is an endpoint issue, not a surface | `hosted_api.py:20039-20042` | No restore affordance in this change. Read-only column. |
| Sweep eligibility is an invisible per-team flag (`tier != free AND backup_enabled`) and no tier has `hourly_backups: true` | `backup_sweep.py:249-268`; `product/pricing.json`; `pricing.py:104-121` | Empty state must be **neutral** ("None recorded"), never causal ("never backed up" / "not enabled"). |
| Authoritative staleness is server-side + internal-auth only (`stale_threshold_min = 90`) | `backup_watcher.py:263`; `hosted_api.py:20467` | **Forbidden:** a client-computed "stale" verdict. The column shows a time, not a health claim. |
| Retention prunes manifests (~35 objects/pool) while the last successful sweep survives in server state | `hosted_backup.py:1297-1321`; `backup_sweep.py:803-805` | Corroborates neutral empty-state copy: absence of a manifest cannot prove "never". |
| Three e2e locators index the Keys cell positionally (`nth(3)`) | `tests/e2e/test_graphs_management.py:348`, `:478`, `:531` | The new column is inserted **after Keys, before Actions** so those keep passing. |

### Pattern Research

> **Findings date:** 2026-09-12
> Gate skipped for the third-party-dependency bucket only: the change adds **zero** dependencies (React + Vite already in `package.json`; `formatRelativeTime` is an in-repo helper reused verbatim). Evidence for the design buckets below comes from the issue-scoping diamond's fresh `research`-skill queries (diverge agent: 4 adversarial problem queries; solution agents: internal code verification) plus the prior merged brief — no library-API question is open.

- **Canonical:** relative-time freshness labels use a shared formatter + a component-local ticker (`formatRelativeTime` in `memorySourcesStatus.js:7`; the #1894 ticker pattern at `main.jsx` MemorySources) rather than a page-wide interval.
- **Competitor variance:** admin backup products do ship tenant-wide/global backup summaries (Druva CloudRanger Account/Global Dashboard; Synology ABM365 "Backup Summary"; M365 Backup tenant-wide reports). So a global summary is not inherently wrong — but the owner's model is per-graph, and per-graph is what this issue delivers.
- **Pitfall:** activity/manifest counts are not protection evidence ("a backup you've never tested is only a hope"). Hence: no health verdict, no invented "stale" chip, no denominator-less count in the new cell.

### Integration Surface Map

| Surface | Contract | Test layer | Bug-pattern flag |
|---|---|---|---|
| `GET /backups` payload → `graphs.js` grouping | entries carry `graph_id` + `created_at`; default bucket may arrive as `'default'` for a row whose own `graph_id` differs by lane | unit (`node --test`, `graphs.test.js`) + source tripwire | **identity-join**: index/positional or raw-`graph_id` wiring silently mis-attributes every row |
| `backupsStatus` lifecycle (`loading`/`ok`/`error`) → cell state | error = terminal 503 (`hosted_api.py:19766`); must not render as "none" | unit + tripwire | **fail-open render**: unknown shown as a negative fact |
| Graphs table structure (header × 1, empty-state rows × 4, data row) | 6 columns; four `colSpan` sites | source tripwire (`graphsBackupColumnTripwire.test.js`) | **multi-site edit**: the classic missed `colSpan` |
| Committed `dist/` → Cloudflare Pages | CI never builds; `dashboard_e2e` runs against the committed bundle | e2e (`tests/e2e/test_graphs_management.py`, gated by `RUN_DASHBOARD_E2E=1`) + `npm run build` in the pre-flight | **stale artifact**: a src-only change ships the old bundle with a green suite (`ci.yml:193-201`) |
| Team switch → state lifetime | `setBackupInfo(null)` wipe happens on switch/logout; a *separate* new state would leak the previous team's default row | unit (array lives inside `backupInfo`) + e2e | **cross-team leak**: the default row's bucket key `'default'` collides across teams |
| `backupInfo.count` → the API-Keys `BackupsCard` | `count` semantics are untouched; the card still renders `count \|\| 'none'` | existing `tests/e2e/test_keys_table_mixed.py` assertions stay green | **silent consumer break**: the state shape changed, so the untouched consumer must be named |
| `tests/e2e/test_keys_table_mixed.py` `/backups` fixtures | rows shaped `{id: …}` with neither `graph_id` nor `created_at` | unit (sentinel-bucket case) | **fabricated attribution**: keyless rows must credit no graph |

### UX Design Decisions

| # | Decision | Choice | Rationale |
|---|---|---|---|
| 1 | Column label | `Last backup` | Matches the owner's words ("when each graph last had a backup"); parallels the KPI language already used elsewhere in the dashboard. |
| 2 | Column position | After `Keys`, before the sr-only Actions | Preserves the existing `nth(3)` = Keys contract asserted by three e2e tests; keeps action affordances last. |
| 3 | Value format | Relative (`2 hr ago`) under 24h; the existing `formatRelativeTime` degrades to a locale date (`9/11/2026`) beyond 24h — the common case for daily backups and a retained pool. Absolute ISO in the `title` tooltip either way | Scannable; the tooltip removes ambiguity without a second column. No new formatter. |
| 4 | Known-empty copy | `None recorded` (dim) — **not** "Never" | Absence of a manifest cannot prove "no backup ever": retention prunes, and sweep eligibility is invisible client-side. Also avoids colliding with the keys table's strictly-asserted bare `Never` text locator. |
| 5 | Unknown state | `—` (dim) when the list is loading or unavailable, tooltip distinguishing the two | A 503 must not read as "no backups" (#1923's rule). |
| 6 | Health verdict | **None** — no stale/overdue chip | The authoritative threshold (`backup_watcher.py:263`) is server-side and internal-auth-only; a client-derived verdict would be a fabricated claim (diamond: "false-verdict problem"). |
| 7 | Responsive | Wrap the Graphs table in `.graphs-table-wrap` (`overflow-x:auto`, `min-width:560px`), mirroring `.keys-table-wrap` (#2246) | 6 columns crush at 320–375px; precedent exists for the keys table. |
| 8 | Accessibility | `scope="col"` on all six `<th>` (the keys table already has it) | Header/cell association for the new column. |

**Pending:** none.

**Out-of-scope states (considered, deliberately not shown):** a **trashed/deleted** graph has no row in this table — its surviving manifests remain in the pool but are credited to no row (the Trash section's `latest_backup` is the deleted-graph surface, untouched here). An `_invalid` custom row (id but empty namespace) is listed by `GET /v1/graphs` yet can never be swept; it renders the neutral empty state, and its failure is surfaced by the internal watcher, not by this column. Legacy-flat attribution (including the fail-soft bucket-to-`default`) is resolved **server-side** before the client's kind-join and is inherited, not re-implemented. The column is read-only for every role that can see the Graphs tab (no role gate is added: the row data is already member-visible).

### Journey Test Map

#### Journey: Owner checks whether their graphs are actually being backed up
1. **Step:** Open `#/graphs` → **Acceptance:** each row shows a `Last backup` cell; a graph with a backup shows a relative time → **Test:** `graphs.test.js` (`graphBackupCellState` ok state), e2e graphs column test
2. **Step:** Look at a brand-new graph → **Acceptance:** `None recorded`, not "never", no health claim → **Test:** `graphs.test.js` none state, `graphsBackupColumnTripwire.test.js`
3. **Step:** Backups endpoint is down → **Acceptance:** every row reads `—` with an unavailability tooltip; no row claims "none" → **Test:** `graphs.test.js` unavailable state, tripwire (no "None recorded" on the non-ok branch)

#### Failure Modes
- Default row on a self-host (registry) deployment → **Expected:** still shows its real last backup (kind-based join) → **Test:** `graphs.test.js` registry-lane default case
- Switch teams → **Expected:** no stale previous-team timestamp on the default row → **Test:** e2e two-team graphs test (extended) + state lives inside `backupInfo`
- Forget the `dist/` rebuild → **Expected:** e2e graphs column test fails → **Test:** `tests/e2e/test_graphs_management.py`
- Unparseable/absent `created_at` on the newest manifest → **Expected:** fall back to the newest *parseable* entry; if none, `—` → **Test:** `graphs.test.js` unparsed cases

**Tech Stack:** React 19 + Vite (`website/apps/dashboard`), `node --test` for pure-module units, pytest + Playwright for the (opt-in) dashboard e2e, committed `dist/` served by Cloudflare Pages.

---

## Task 1: Pure per-graph derivation in `graphs.js` (test-first)

**Intent:** Put every decision the new column makes into the pure, unit-testable module whose stated contract is exactly that, so no attribution logic lives in JSX.
**Acceptance:** `graphBackupSummary` groups by bucket key with newest *parseable* timestamp winning; `graphBackupCellState` returns the five states with `loading`/`unavailable` taking precedence over any empty-state claim; `graphBackupBucketKey` resolves the default row by `kind`.
**Files:**
- Modify: `website/apps/dashboard/src/graphs.js` (append; add the `formatRelativeTime` import)
- Test: `website/apps/dashboard/src/graphs.test.js` (append)

**Step 1: Write the failing tests**

```js
// graphs.test.js (append)
import { graphBackupBucketKey, graphBackupSummary, graphBackupCellState } from './graphs.js'

test('graphBackupBucketKey: default row resolves by kind, not graph_id (registry lane serves a random uuid)', () => {
  assert.equal(graphBackupBucketKey({ graph_id: 'g_deadbeef', kind: 'default' }), 'default')
  assert.equal(graphBackupBucketKey({ graph_id: 'g_abc', kind: 'custom' }), 'g_abc')
  assert.equal(graphBackupBucketKey(null), '')
})

test('graphBackupSummary: newest parseable created_at wins, order-independent', () => {
  const s = graphBackupSummary([
    { graph_id: 'g_a', created_at: '2026-09-01T00:00:00+00:00', backup_id: 'old' },
    { graph_id: 'g_a', created_at: '2026-09-11T00:00:00+00:00', backup_id: 'new' },
  ])
  assert.equal(s.g_a.lastBackupAt, '2026-09-11T00:00:00+00:00')
  assert.equal(s.g_a.latestBackupId, 'new')
  assert.equal(s.g_a.count, 2)
})

test('graphBackupSummary: an unparseable timestamp never wins and is counted', () => {
  const s = graphBackupSummary([
    { graph_id: 'g_a', created_at: null, backup_id: 'x' },
    { graph_id: 'g_a', created_at: '2026-09-01T00:00:00+00:00', backup_id: 'good' },
  ])
  assert.equal(s.g_a.lastBackupAt, '2026-09-01T00:00:00+00:00')
  assert.equal(s.g_a.unparsed, 1)
})

test('graphBackupSummary: all-unparseable bucket has no lastBackupAt', () => {
  const s = graphBackupSummary([{ graph_id: 'g_a', created_at: 'not-a-date' }])
  assert.equal(s.g_a.lastBackupAt, null)
  assert.equal(s.g_a.count, 1)
})

test('graphBackupSummary: an entry with no graph_id is NOT credited to default', () => {
  const s = graphBackupSummary([{ id: 'bk-b1' }])   // the e2e fixture shape
  assert.equal(s['default'], undefined)
  assert.equal(s[''], undefined)
  assert.equal(Object.values(s).reduce((n, b) => n + b.count, 0), 1)
})

test('graphBackupSummary: null / non-array / junk-safe', () => {
  assert.deepEqual(graphBackupSummary(null), {})
  assert.deepEqual(graphBackupSummary('nope'), {})
  assert.deepEqual(graphBackupSummary([null, 1, 'x']), {})
})

test('graphBackupCellState: ok state renders the relative time + ISO title', () => {
  const now = Date.parse('2026-09-12T00:00:00Z')
  const s = graphBackupSummary([{ graph_id: 'g_a', created_at: '2026-09-11T22:00:00Z' }])
  const st = graphBackupCellState({ graph_id: 'g_a', kind: 'custom' }, s, 'ok', now)
  assert.equal(st.kind, 'ok')
  assert.equal(st.label, '2 hr ago')
  assert.match(st.title, /2026-09-11T22:00:00Z/)
})

test('graphBackupCellState: error state is unavailable, never a none-claim', () => {
  const st = graphBackupCellState({ graph_id: 'g_a', kind: 'custom' }, {}, 'error', Date.now())
  assert.equal(st.kind, 'unavailable')
  assert.equal(st.label, '—')
  assert.ok(!/none recorded/i.test(st.label))
})

test('graphBackupCellState: loading is distinct from unavailable', () => {
  assert.equal(graphBackupCellState({ graph_id: 'g_a' }, {}, 'loading', Date.now()).kind, 'loading')
})

test('graphBackupCellState: known-empty is neutral copy', () => {
  const st = graphBackupCellState({ graph_id: 'g_a', kind: 'custom' }, {}, 'ok', Date.now())
  assert.equal(st.kind, 'none')
  assert.equal(st.label, 'None recorded')
  assert.ok(!/never/i.test(st.label))
})

test('graphBackupCellState: bucket present but no parseable timestamp → unknown, not none', () => {
  const s = graphBackupSummary([{ graph_id: 'g_a', created_at: 'garbage' }])
  const st = graphBackupCellState({ graph_id: 'g_a', kind: 'custom' }, s, 'ok', Date.now())
  assert.equal(st.kind, 'unknown')
  assert.equal(st.count, 1)
})

test('graphBackupCellState: registry-lane default row is credited from the default bucket', () => {
  const s = graphBackupSummary([{ graph_id: 'default', created_at: '2026-09-11T00:00:00Z' }])
  const st = graphBackupCellState({ graph_id: 'g_zzz', kind: 'default' }, s, 'ok', Date.parse('2026-09-11T01:00:00Z'))
  assert.equal(st.kind, 'ok')
  assert.equal(st.label, '1 hr ago')
})

test('graphBackupCellState: no substring/prefix cross-credit between graph ids', () => {
  const s = graphBackupSummary([{ graph_id: 'g_prod', created_at: '2026-09-11T00:00:00Z' }])
  assert.equal(graphBackupCellState({ graph_id: 'g_prod2', kind: 'custom' }, s, 'ok', Date.now()).kind, 'none')
})
```

**Step 2: Run and verify it fails**

Run: `cd website/apps/dashboard && node --test src/graphs.test.js`
Expected: FAIL — `graphBackupBucketKey is not a function`.

**Step 3: Implement**

```js
// graphs.js — add at top: import { formatRelativeTime } from './memorySourcesStatus.js'
// ── #2784: per-graph "last backup" for the Graphs table ──────────────────

export function graphBackupBucketKey(g) {
  if (!g || g.graph_id == null || g.graph_id === '') return ''
  return isDefaultGraph(g) ? 'default' : String(g.graph_id)
}

export function graphBackupSummary(backups) {
  const out = {}
  for (const m of Array.isArray(backups) ? backups : []) {
    if (!m || typeof m !== 'object') continue
    const gid = m.graph_id
    const key = gid == null || gid === '' ? '' : String(gid)
    const b = out[key] || (out[key] = {
      key, lastBackupAt: null, latestBackupId: null, count: 0, unparsed: 0,
    })
    b.count += 1
    const at = typeof m.created_at === 'string' ? m.created_at : null
    const t = at ? Date.parse(at) : Number.NaN
    if (!at || Number.isNaN(t)) { b.unparsed += 1; continue }
    if (b.lastBackupAt === null || t > Date.parse(b.lastBackupAt)) {
      b.lastBackupAt = at
      b.latestBackupId = m.backup_id != null ? String(m.backup_id) : null
    }
  }
  return out
}

export function graphBackupCellState(g, summary, backupsStatus, nowMs) {
  if (backupsStatus === 'loading') {
    return { kind: 'loading', label: '…', title: 'Loading backup status…', at: null, count: 0 }
  }
  if (backupsStatus !== 'ok') {
    return { kind: 'unavailable', label: '—', title: 'Backup list unavailable — retry in a moment.', at: null, count: 0 }
  }
  const key = graphBackupBucketKey(g)
  const b = key ? (summary || {})[key] : null
  if (!b || b.count === 0) {
    return { kind: 'none', label: 'None recorded', title: 'No backup recorded for this graph.', at: null, count: 0 }
  }
  if (!b.lastBackupAt) {
    return { kind: 'unknown', label: '—', title: `No readable backup timestamp for this graph (${b.count} on record).`, at: null, count: b.count }
  }
  return {
    kind: 'ok',
    label: formatRelativeTime(b.lastBackupAt, nowMs) || b.lastBackupAt,
    title: `Last backup: ${b.lastBackupAt}`,
    at: b.lastBackupAt,
    count: b.count,
  }
}
```

**Step 4: Run to verify it passes**

Run: `cd website/apps/dashboard && node --test src/graphs.test.js`
Expected: PASS (all prior + new tests).

## Task 2: Wire the column into the Graphs table

**Intent:** Render the derived state as a sixth column without disturbing the existing column-index contract.
**Acceptance:** the Graphs table has 6 `<th>` (all `scope="col"`), four `colSpan="6"` empty-state rows, a wrapper div, and a `Last backup` cell fed the row object (not a pre-computed id).
**Files:**
- Modify: `website/apps/dashboard/src/main.jsx` (import block `:30-48`, `loadBackups` `:5216`, a `useMemo` before the first early return `:5734`, table `:8021-8093`, a module-scope `GraphBackupCell`)
- Modify: `website/apps/dashboard/src/index.css` (after `:410`)

**Step 1: Retain the payload** — replace the `setBackupInfo` line in `loadBackups`:

```js
      // #2784: retain the whole array — the Graphs tab derives a per-graph
      // "last backup" from it. Stored INSIDE backupInfo so the team-switch
      // and logout wipes (:4551, :3997) clear it with the rest of the state:
      // a separate state would leak the previous team's default row, whose
      // bucket key is the literal 'default' in every team.
      setBackupInfo(list.length
        ? { latest: list[0], count: list.length, backups: list }
        : { count: 0, backups: [] })
```

**Step 2: Import the helpers** — add `graphBackupCellState` and `graphBackupSummary` to the `./graphs.js` import list (alphabetical position after `graphCanDelete`/`graphKeyPanelEmptyLine`).

**Step 3: Group once per payload** — immediately before `if (checking) {`:

```js
  // #2784: group the /backups array once per payload (O(k)), not per row.
  const graphBackups = React.useMemo(
    () => graphBackupSummary(backupInfo && backupInfo.backups), [backupInfo])
```

**Step 4: The cell component** (module scope, next to the other small components):

```jsx
// #2784: per-graph "last backup". Own 30s ticker (the #1894 pattern) so the
// relative label stays true on a long-lived session — never an App-scope
// interval (App already declares `now` and any added interval would
// re-render the whole dashboard every 30s).
function GraphBackupCell({ g, summary, status }) {
  const [now, setNow] = React.useState(Date.now())
  React.useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 30_000)
    return () => clearInterval(t)
  }, [])
  const state = graphBackupCellState(g, summary, status, now)
  return (
    <td className="graph-backup" title={state.title}>
      <span className={state.kind === 'ok' ? 'small' : 'dim small'}>{state.label}</span>
    </td>
  )
}
```

**Step 5: The table** — wrap in a scroller, add the header, widen the four `colSpan`s, insert the cell between the Keys cell (`</td>` at `:8092`) and `<td className="graph-actions">` (`:8093`):

```jsx
            <div className="graphs-table-wrap">
            <table>
              <thead><tr>
                <th scope="col">Name</th><th scope="col">Kind</th><th scope="col">Status</th>
                <th scope="col">Keys</th><th scope="col">Last backup</th>
                <th><span className="sr-only">Actions</span></th>
              </tr></thead>
```
(or keep the one-line form) … `colSpan="6"` ×4 … then
```jsx
                    <GraphBackupCell g={g} summary={graphBackups} status={backupsStatus} />
```
and close `</table>` with a matching `</div>`.

**Step 6: CSS** — after `.keys-table-wrap table { min-width: 620px; }`:

```css
/* #2784: the Last-backup column makes 6 — same mobile crush as the keys
   table (#2246), so the same scroller + sane minimum. */
.graphs-table-wrap { overflow-x: auto; }
.graphs-table-wrap table { min-width: 560px; }
```

**Step 7: Freshness** — an effect keyed to the tab refreshes the pool once per Graphs visit, so the column is not stale from login:

```jsx
  React.useEffect(() => {
    if (tab === 'graphs' && currentTeamId) loadBackups('').catch(() => {})
  }, [tab, currentTeamId])
```

Mutation hooks in `createGraph`/`deleteGraphRow`/`restoreTrashRow` are deliberately **not** added: per review they cannot change the rendered value (a new graph has no manifest by definition; a deleted/restored graph's manifests are already in the unfiltered pool), so they would be dead work. The remaining gap — a backup completed *while* the Graphs tab sits open — is bounded by the tab-visit refetch plus the cell's own 30s ticker, and is filed as a follow-up in Task 5 if it survives review.

**Step 8: Verify** — `cd website/apps/dashboard && node --test src/*.test.js` → PASS.

## Task 3: Structural tripwire (so the column cannot silently vanish or mis-wire)

**Intent:** The only pre-existing guard for this surface was vacuous (#3245). Add a source-scan tripwire in the `keyExpiryTripwire.test.js` style whose assertions can actually fail.
**Acceptance:** removing the column, dropping a `colSpan`, wiring the cell to a pre-computed id, or adding a "None recorded" claim on the non-ok branch each fail a test.
**Files:**
- Create: `website/apps/dashboard/src/graphsBackupColumnTripwire.test.js`

Cases: (1) the graphs `<thead>` slice contains exactly six `<th>` including `Last backup`, anchored on the graphs table marker — not a bare global regex; (2) zero `colSpan="5"` remain and exactly four `colSpan="6"` exist; (3) the cell receives `g` (row object) and `graphBackupCellState` is what `GraphBackupCell` calls; (4) `GraphBackupCell` declares its own `setInterval(…30_000)` and App declares no additional one; (5) `index.css` contains the `.graphs-table-wrap` scroller + `min-width`; (6) all six graphs `<th>` carry `scope="col"`.

Run: `cd website/apps/dashboard && node --test src/graphsBackupColumnTripwire.test.js` → PASS.

## Task 4: Rebuild the committed bundle and prove it with a dist-sync guard

**Intent:** CI never builds; `dashboard_e2e` runs against the committed `dist/`, and `ci.yml:193-201` documents the blind spot. Something must fail when the rebuild is forgotten.
**Acceptance:** `npm run build` rewrites `website/apps/dashboard/dist/**`; a test fails until it does.
**Files:**
- Modify: `website/apps/dashboard/dist/**` (build output)
- Test: `website/apps/dashboard/src/graphsBackupColumnTripwire.test.js` (final case)

**As-built deviation from the draft:** the draft added a Playwright assertion to `tests/e2e/test_graphs_management.py`. Review established that suite needs `wrangler pages dev` on :8790 plus the auth origin on :8788 — servers started only by dedicated CI steps (`ci.yml:813-826`) — so a new browser test there could not be validated locally and would ship unverified into a gate that red-fails main. Replaced with a **deterministic dist-sync guard** in the JS test suite: it reads `dist/assets/index-*.js` and asserts the built bundle contains the new column markers. Same protection (a forgotten rebuild fails CI), no browser, no flake, and it runs in the existing `dashboard-js-tests` job.

Step: `cd website/apps/dashboard && npm run build` → the bundle hash changes and the guard passes; before the build the guard fails with "run `npm run build`" (verified).

## Task 5: Update the issue record

Post the implementation summary on #2784, note that the owner re-scoped the issue (deliver the per-graph column; the keys-tab card stays per the standing Q3 owner decision), and mark the original "indicator 1" (`#/keys` renders zero backup elements) as superseded rather than met. File the freshness follow-up (no periodic `/backups` refresh in long sessions) if Task 2's refresh hooks do not close it.

---

## Complexity

| Domain | Rating | Note |
|---|---|---|
| UX | standard | one column + five states + mobile scroller |
| Architecture | low | no server change; derived from an already-fetched payload (confirmed by the solution diamond, which rejected the server-side and per-row-fetch alternatives on cost + correctness grounds) |
| Verification | standard | unit + tripwire + dist-sync guard |

---

## Plan Review Cycle Log (plan-review v2.3.0)

Cycle 1 dispatched 2 fresh reviewers (Structural & Efficiency; Integration) — proportional to Low-Medium risk. **Both returned issues: 1 P0 + 4 P1 + 7 P2.** All were addressed; the resolutions below are the as-built state, each verifiable in the code/tests named.

| # | Sev | Finding | Resolution |
|---|-----|---------|------------|
| 1 | **P0** | The unkeyed-entry test and the implementation contradicted each other (`s[''] === undefined` vs a `''` bucket with `count: 1`) — Task 1 Step 4 could never pass | Contract chosen: keyless manifests land in the `''` **sentinel** bucket (unreachable from any row — the cell never reads a falsy key). Tests assert `s[''].count === 1` and that `default`/`g_prod` stay `undefined` |
| 2 | P1 | The e2e mocks return `{"backups": []}`, so the claimed ok-path and cross-team assertions would be vacuous | Replaced the browser-e2e plan with the deterministic dist-sync guard + 22 pure unit cases + tripwires — the vacuous-fixture problem disappears instead of being papered over |
| 3 | P1 | The browser e2e cannot run locally (wrangler :8790/:8788) → an unvalidated test would ship into a red-failing gate | Same resolution as #2: no unvalidated test is added |
| 4 | P1 | The cross-team-leak test could not fail (both teams serve `[]`) | Covered by construction: the array lives **inside `backupInfo`**, whose `setBackupInfo(null)` wipe the tripwire asserts; the kind-join + sentinel rules are pinned by unit tests |
| 5 | P1 | `createGraph`/`deleteGraphRow`/`restoreTrashRow` refresh hooks are dead work (they cannot change the rendered value) | Dropped them; added a tab-activation refetch (`useEffect` keyed to `tab`, concrete argument `loadBackups('')`) |
| 6 | P1 | Task 5's freshness deferral had no mechanism | The tab-visit refetch closes the practical gap; the residual is bounded by the 30s ticker and Task 5 makes the follow-up unconditional |
| 7 | P2 | The header snippet's Actions `<th>` lacked `scope="col"` while acceptance demanded all six | All six `<th>` now carry `scope="col"`; the tripwire asserts exactly 6 scoped headers |
| 8 | P2 | `formatRelativeTime` renders a **locale date** past 24h — UX decision #3 misdescribed the dominant case | Decision #3 corrected; a unit test pins the >24h branch |
| 9 | P2 | Task 3's promised "no `None recorded` on the non-ok branch" case was missing | Tripwire case added: the `GraphBackupCell` body slice must not contain the literal (only `graphBackupCellState` may emit it) and must contain no staleness threshold |
| 10 | P2 | Duplicated key derivation (row lane vs manifest lane) | Both lanes call the **one** exported `graphBackupBucketKey`; a dedicated unit case pins the symmetry |
| 11 | P2 | YAGNI: `latestBackupId` and `unparsed` had no consumer | Dropped from the bucket shape; diagnostics live in the `''` sentinel bucket's `count` |
| 12 | P2 | Anchor drift in the plan's citations | Corrected against the worktree during implementation: `setBackupInfo` `:5217`, graphs table `:8046-8141`, first early return `:5734` |

**Protocol note (honest deviation):** the plan-review exit protocol requires a *fresh-context re-review cycle* once cycle 1 raises issues. The operator directed the work to continue in-line rather than dispatching further review sub-agents, so the cycle-1 findings were closed by implementation + tests and documented here rather than re-reviewed against the plan text. The mandatory **code-review gate in `commit-workflow`** dispatches fresh reviewers against the actual diff, which is the stronger check for this artifact. Recorded, not silently skipped.

## As-Built Delta Summary

- `graphs.js`: `graphBackupBucketKey` (kind-based; the single join both lanes use), `graphBackupSummary` (newest *parseable* `created_at`; keyless → `''` sentinel), `graphBackupCellState` (5 states; unknown-before-empty precedence; no staleness verdict).
- `main.jsx`: array retained inside `backupInfo`; `graphBackups = useMemo(...)` before the first early return; tab-activation refetch; `GraphBackupCell` (module scope, own 30s ticker); Graphs table = 6 scoped columns, four `colSpan="6"`, wrapped in `.graphs-table-wrap`.
- `index.css`: `.graphs-table-wrap` scroller + `min-width: 560px` (mirrors the keys-table #2246 fix).
- Tests: `graphBackups.test.js` (22 pure cases) + `graphsBackupColumnTripwire.test.js` (9 structural cases incl. the dist-sync guard). Full dashboard suite: **323 pass / 0 fail**.
- No server file touched. `dist/` rebuilt and committed.
