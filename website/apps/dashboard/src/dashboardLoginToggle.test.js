// dashboardLoginToggle.test.js — run with node --test (Node 20+, zero deps).
// #2475 client-side tripwire: the "API key dashboard login" switch must
// render PURELY from the live /v1/team payload (team.dashboard_key_login),
// on first paint AND after a reload — a reload is just a fresh GET /v1/team
// whose response hydrates team state verbatim (setTeam(t) in refreshTeam /
// completeLogin). The reload snap-back bug lived server-side (the session
// lane hardcoded dashboard_key_login=True), but a client twin is equally
// possible: a future edit that forces the flag true after load, renders the
// switch state from anything other than team.dashboard_key_login, or drops
// the server-authoritative PATCH merge would reproduce #2475 with no server
// involvement. Reads main.jsx as TEXT (no React runtime — overviewSettings
// .test.js / keyTeamPinsTripwire.test.js pattern). Server-side regression
// pins: tests/test_dashboard_login.py +
// tests/test_supabase_control.py::test_session_lane_dkl_carries_stored_flag_and_drift_default.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const __dirname = dirname(fileURLToPath(import.meta.url))
const src = readFileSync(join(__dirname, 'main.jsx'), 'utf8')

// ── toggle handler body (toggleDashboardKeyLogin) ──────────────────────────
const TOGGLE_DEF = 'async function toggleDashboardKeyLogin() {'
// the handler's closing brace is immediately followed by the initialTab
// IIFE (a stable anchor — the tab state init directly after the handler).
const TAB_DEF = 'const initialTab = (() => {'

function toggleBody() {
  const start = src.indexOf(TOGGLE_DEF)
  assert.notEqual(start, -1, 'could not locate toggleDashboardKeyLogin in main.jsx')
  const end = src.indexOf(TAB_DEF, start + 1)
  assert.notEqual(end, -1, 'could not locate end of toggleDashboardKeyLogin')
  return src.slice(start, end)
}

test('#2475: switch state + label derive from team.dashboard_key_login (the /v1/team payload)', () => {
  // aria-checked AND data-on must read the live flag — a hardcoded true here
  // is the exact #2475 render snap-back.
  assert.ok(src.includes('aria-checked={team.dashboard_key_login !== false}'),
    'switch aria-checked must be team.dashboard_key_login !== false')
  assert.ok(src.includes('data-on={team.dashboard_key_login !== false}'),
    'switch data-on must be team.dashboard_key_login !== false')
  // both label states are reachable: "(recommended: disable)" only while the
  // flag is not false, "disabled ✓" only while it IS false (toggle-off must
  // survive reload → the off label must render from a persisted false).
  assert.ok(src.includes("{team.dashboard_key_login !== false && <span style={{ color: 'var(--accent,#06b6d4)' }}>(recommended: disable)</span>}"),
    '(recommended: disable) must be gated on dashboard_key_login !== false')
  assert.ok(src.includes("{team.dashboard_key_login === false && <span style={{ color: 'var(--green,#4ade80)' }}>disabled ✓</span>}"),
    'disabled ✓ must be gated on dashboard_key_login === false')
})

test('#3136: the disable RECOMMENDATION renders only while the setting is ON', () => {
  // Part-1 defect: the paragraph was ungated (permission-only guard), so a
  // team that already had dashboard_key_login === false still read
  // "We recommend disabling…". It must render inside the ON branch only, and
  // the OFF state must explain the consequence instead of nagging.
  const flat = src.replace(/\s+/g, ' ')
  assert.ok(
    flat.includes('{team.dashboard_key_login !== false && ( <p> We recommend disabling your API key as a dashboard sign-in'),
    'recommendation copy must be gated on dashboard_key_login !== false')
  assert.ok(
    flat.includes('{team.dashboard_key_login === false && ( <p> Your API key can no longer sign in to this dashboard.'),
    'the OFF state must render the consequence line, not the recommendation')
  // exactly one occurrence — a second ungated copy would re-open #3136
  assert.equal(
    flat.split('We recommend disabling your API key as a dashboard sign-in').length - 1, 1,
    'the recommendation copy must appear exactly once (inside the ON gate)')
})

test('#2475: toggle handler flips the CURRENT state and merges the server PATCH result (never a literal)', () => {
  const body = toggleBody()
  // next is derived from the live team flag — flipping OFF sends enabled:false.
  assert.ok(body.includes('const next = team.dashboard_key_login === false'),
    'handler must compute next from the current team.dashboard_key_login')
  // optimistic flip uses the derived value, never a hardcoded true.
  assert.ok(body.includes('setTeam({ ...team, dashboard_key_login: next })'),
    'optimistic update must write the derived `next`')
  // server authority: PATCH /v1/team/dashboard-login with the derived value…
  assert.ok(body.includes("fetch(`${API_BASE}/v1/team/dashboard-login`, {"),
    'handler must PATCH /v1/team/dashboard-login')
  assert.ok(body.includes("body: JSON.stringify({ enabled: next }),"),
    'PATCH body must be { enabled: next }')
  // …and the response is MERGED ({ ...prev, ...t }), so the persisted value
  // the server returns becomes the rendered state without a client rewrite.
  assert.ok(body.includes('if (t && t.team_id) setTeam((prev) => ({ ...prev, ...t }))'),
    'successful PATCH must merge the server-returned flag into team state')
  // no client-side literal true anywhere in the handler (or the whole file).
  assert.ok(!src.includes('dashboard_key_login: true'),
    'main.jsx must never hardcode dashboard_key_login: true')
})

test('#2475: reload = fresh GET /v1/team → raw response hydrates team state verbatim', () => {
  // The reload path (refreshTeam + completeLogin both end in a bare setTeam(t)
  // with the /v1/team response) — no reload-time force-true may sit between
  // the fetch and the render. Two call sites today; a third hydrator must be
  // a deliberate addition, so assert the load functions keep the raw merge.
  const sites = [...src.matchAll(/\bsetTeam\(t\)/g)].map((m) => m.index)
  assert.ok(sites.length >= 2,
    `expected bare setTeam(t) hydrators (refreshTeam + completeLogin), got ${sites.length}`)
  // every dashboard_key_login read in main.jsx reads team.dashboard_key_login
  // (live state) — never a stored/global constant that could stale the flag.
  const reads = [...src.matchAll(/dashboard_key_login/g)].map((m) => m.index)
  assert.ok(reads.length > 0, 'dashboard_key_login present in main.jsx')
  // whole-file sentinel: the ONLY writes to dashboard_key_login must be the
  // optimistic `next` and the server merge above (no `= true` assignment).
  assert.ok(!/dashboard_key_login\s*:\s*true/.test(src),
    'no JSX/dict literal forces dashboard_key_login: true')
  assert.ok(!/dashboard_key_login\s*=\s*true/.test(src),
    'no assignment forces dashboard_key_login = true')
})
