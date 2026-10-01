// accountDeleteConfirm.test.js — #4029.
//
// The owner ruled (2026-09-30, decision 1B) that a departing person's
// solely-owned teams are deleted WITH the account, and that the safeguard is a
// confirmation popup carrying a fixed warning and two actions: confirm and
// cancel. This guard is EXECUTED: it imports the real `DeleteAccountSection`
// (a createElement module, importable without a JSX transform) and renders it
// with react-dom/server, then reads the copy and the two actions off the
// element tree React produces. A source-text grep could be satisfied by a
// commented-out or shadowed copy; this cannot.
//
// The WIRING half (which handler main.jsx passes for each action) is executed
// too — see the `probeTags` tests near the bottom. A source pin such as
// `onCancel={() => setDeleteAccountOpen(false)}` cannot constrain a handler
// BODY: rewriting it to `onCancel={() => deleteAccount()}` (cancel now deletes
// the account) or `onOpen={() => deleteAccount()}` (the popup is bypassed)
// satisfied every textual guard while all seven tests stayed green. `probeTags`
// compiles the real `<DeleteAccountSection …/>` call site with the dashboard's
// own JSX transform (esbuild) and returns the EFFECTIVE props, so the spies
// below run the actual handler bodies main.jsx ships. `deleteAccount` itself is
// extracted from main.jsx and executed against an injected `api`/`logout`.
//
// Class-B mutation evidence (run, observed RED, reverted):
//   * change a word of `DELETE_ACCOUNT_WARNING` (e.g. "personal" → "user") →
//     tests 1 and 2 fail;
//   * swap the Cancel button's onClick to `onConfirm` → the cancel test fails;
//   * main.jsx `onCancel={() => setDeleteAccountOpen(false)}` →
//     `() => deleteAccount()` → the wired-cancel test fails;
//   * main.jsx `onOpen={() => { … }}` → `() => deleteAccount()` → the
//     wired-opener test fails;
//   * main.jsx `onConfirm={deleteAccount}` → `onConfirm={() => {}}` → the
//     wired-confirm test fails;
//   * drop `method: 'DELETE'` from the extracted `deleteAccount` body → the
//     executed-request test fails.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { DeleteAccountSection, DELETE_ACCOUNT_WARNING } from './accountDeletion.js'
import { importsFromMain, probeTags, evalExpressions } from './jsxSourceProbe.js'

const HERE = dirname(fileURLToPath(import.meta.url))
const mainJsx = readFileSync(join(HERE, 'main.jsx'), 'utf8')

// The ruling's copy, spelled out here as the independent copy of record — a
// change to the module's constant alone must fail this test.
const REQUIRED_COPY =
  "Deleting your personal account will also delete any teams for which you're the only owner"

const baseProps = {
  open: false,
  busy: false,
  error: '',
  onOpen() {},
  onCancel() {},
  onConfirm() {},
}

function render(props) {
  return renderToStaticMarkup(React.createElement(DeleteAccountSection, props))
}

/** Walk a React element tree and collect every element of `type`. */
function collect(node, type, acc = []) {
  if (node === null || node === undefined || typeof node === 'boolean') return acc
  if (Array.isArray(node)) {
    for (const child of node) collect(child, type, acc)
    return acc
  }
  if (typeof node !== 'object' || !('props' in node)) return acc
  if (node.type === type) acc.push(node)
  collect(node.props.children, type, acc)
  return acc
}

/** The visible text of an element whose children are strings/nodes. */
function textOf(node) {
  if (node === null || node === undefined || typeof node === 'boolean') return ''
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(textOf).join('')
  if (typeof node === 'object' && 'props' in node) return textOf(node.props.children)
  return ''
}

function buttonByLabel(props, label) {
  const tree = DeleteAccountSection(props)
  const button = collect(tree, 'button').find((b) => textOf(b.props.children) === label)
  assert.ok(button, `no button labelled ${JSON.stringify(label)} was rendered`)
  return button
}

test('#4029: the warning copy is the owner-ruled string, verbatim', () => {
  assert.equal(DELETE_ACCOUNT_WARNING, REQUIRED_COPY)
})

test('#4029: the open popup renders the warning copy verbatim', () => {
  const html = render({ ...baseProps, open: true }).replace(/&#x27;/g, "'")
  assert.ok(html.includes(REQUIRED_COPY),
    'the confirm popup must render the owner-ruled warning copy verbatim')
})

test('#4029: the popup renders only when opened, and offers exactly confirm + cancel', () => {
  const closed = render({ ...baseProps, open: false })
  assert.ok(!closed.includes('role="dialog"'), 'no dialog before the control is used')

  const open = render({ ...baseProps, open: true })
  assert.ok(open.includes('role="dialog"'), 'the confirm popup must be a real dialog')
  const labels = collect(DeleteAccountSection({ ...baseProps, open: true }), 'button')
    .map((b) => textOf(b.props.children))
  assert.deepEqual(labels, ['Delete account', 'Cancel', 'Delete my account'],
    'exactly the opener plus the two ruled actions')
})

test('#4029: confirm requests the deletion; cancel does NOT', () => {
  let confirms = 0
  let cancels = 0
  const props = {
    ...baseProps, open: true,
    onConfirm: () => { confirms += 1 },
    onCancel: () => { cancels += 1 },
  }
  buttonByLabel(props, 'Delete my account').props.onClick()
  assert.equal(confirms, 1)
  assert.equal(cancels, 0, 'the confirm action must never route through cancel')

  buttonByLabel(props, 'Cancel').props.onClick()
  assert.equal(cancels, 1)
  assert.equal(confirms, 1, 'cancel must NOT request the deletion')
})

test('#4029: the opener only opens the popup (no request, no confirm)', () => {
  let opens = 0
  let confirms = 0
  buttonByLabel({ ...baseProps, onOpen: () => { opens += 1 }, onConfirm: () => { confirms += 1 } },
    'Delete account').props.onClick()
  assert.equal(opens, 1)
  assert.equal(confirms, 0, 'the opener must not delete anything by itself')
})

test('#4029: a failed delete surfaces inside the still-open popup', () => {
  const html = render({ ...baseProps, open: true, error: 'Could not delete your account — try again.' })
  assert.ok(html.includes('role="alert"'))
  assert.ok(html.includes('Could not delete your account'))
})

// ── FIX 2: the dialog must state the real path, not promise a restore ───────
// There is no self-service restore for an account (docs/retention-and-deletion.md
// §"The three deletion paths": restore is support/operational), so the ONE
// screen where the user decides may not offer one. The copy must name the
// support channel and must not carry a window number at all — the account
// window is the server's (`grace_hours` / `hard_delete_after`) and the client
// has no authoritative source for it before the request is issued.
test('#4029: the popup states the support-recovery path and promises no in-product restore', () => {
  const tree = DeleteAccountSection({ ...baseProps, open: true })
  const body = collect(tree, 'p').map(textOf).join('\n')
  assert.ok(!/\brestore\b/i.test(body),
    `the dialog must not promise a restore the product does not offer — got: ${body}`)
  assert.ok(/support/i.test(body),
    `the dialog must name the recovery channel — got: ${body}`)
  assert.ok(!/\b\d+\s*(day|days|hour|hours|week|weeks)\b/i.test(body),
    `the dialog must not state a window number it cannot source — got: ${body}`)
})

// The section must not accept a day count at all — a `graceDays` prop sourced
// from `graphs.js`'s TRASH_GRACE_DAYS is the graph-trash window, not the
// account window, and the client has no authoritative value to pass.
test('#4029: the section takes no window prop (the graph-trash constant is not it)', () => {
  const tree = DeleteAccountSection({ ...baseProps, open: true, graceDays: 999 })
  const body = collect(tree, 'p').map(textOf).join('\n')
  assert.ok(!body.includes('999'),
    'a `graceDays` prop must not reach the copy — the account window is server-owned')
})

// ── FIX 1: the WIRING main.jsx ships, compiled and EXECUTED ─────────────────
const MAIN_IMPORTS = importsFromMain(mainJsx, ['DeleteAccountSection'])

/**
 * Compile main.jsx's real `<DeleteAccountSection …/>` call site and hand back
 * the effective props plus the spies those props run against. Each handler
 * closure captures the spies of ITS probe, so a later probe cannot
 * cross-contaminate an earlier one.
 */
async function wiringProbe(open) {
  const calls = { delete: 0, logout: 0, setOpen: [], setError: [] }
  globalThis.__acctWiring = {
    deleteAccount: () => { calls.delete += 1; return Promise.resolve({}) },
    logout: () => { calls.logout += 1; return Promise.resolve() },
    setOpen: (v) => { calls.setOpen.push(v) },
    setError: (v) => { calls.setError.push(v) },
  }
  const probes = await probeTags(mainJsx, {
    tag: 'DeleteAccountSection',
    imports: MAIN_IMPORTS,
    bindings: {
      deleteAccountOpen: String(Boolean(open)),
      deleteAccountBusy: 'false',
      deleteAccountError: "''",
      setDeleteAccountOpen: 'globalThis.__acctWiring.setOpen',
      setDeleteAccountError: 'globalThis.__acctWiring.setError',
      deleteAccount: 'globalThis.__acctWiring.deleteAccount',
    },
  })
  assert.equal(probes.length, 1, 'main.jsx must render exactly one <DeleteAccountSection .../>')
  return { props: probes[0].props, calls }
}

test('#4029 wiring: main.jsx cancel is a real exit — it never requests the deletion', async () => {
  const { props, calls } = await wiringProbe(true)
  assert.equal(typeof props.onCancel, 'function')
  await props.onCancel()
  assert.equal(calls.delete, 0, 'cancel must not call the delete handler')
  assert.equal(calls.logout, 0, 'cancel must not end the session')
  assert.deepEqual(calls.setOpen, [false], 'cancel only closes the popup')
  assert.deepEqual(calls.setError, [], 'cancel must not touch the error state')
})

test('#4029 wiring: main.jsx opener only opens — it never requests the deletion', async () => {
  const { props, calls } = await wiringProbe(false)
  assert.equal(typeof props.onOpen, 'function')
  await props.onOpen()
  assert.equal(calls.delete, 0, 'the opener must not delete')
  assert.equal(calls.logout, 0)
  assert.deepEqual(calls.setOpen, [true], 'the opener opens the popup')
  assert.deepEqual(calls.setError, [''], 'the opener clears any stale error')
})

test('#4029 wiring: main.jsx confirm is the delete handler, and the popup is state-driven', async () => {
  const closed = await wiringProbe(false)
  assert.equal(closed.props.open, false, 'closed state must reach the section')

  const opened = await wiringProbe(true)
  assert.equal(opened.props.open, true, 'open state must reach the section')

  await closed.props.onConfirm()
  assert.equal(closed.calls.delete, 1, 'confirm calls the delete handler exactly once')
  assert.equal(opened.calls.delete, 0, 'a different probe must not share the counter')

  // The window is not passed as a graph-trash constant (FIX 2).
  assert.ok(!('graceDays' in closed.props), 'main.jsx must not pass graceDays')
})

test('#4029: main.jsx deleteAccount is a session DELETE that ends the session (EXECUTED)', async () => {
  const seen = []
  globalThis.__acctApi = async (path, opts) => { seen.push([path, opts]); return {} }
  globalThis.__acctLogout = async () => { seen.push(['logout']) }
  globalThis.__acctSetBusy = () => {}
  globalThis.__acctSetError = () => {}
  globalThis.__acctSetOpen = () => {}

  const start = mainJsx.indexOf('async function deleteAccount(')
  assert.notEqual(start, -1, 'deleteAccount must exist in main.jsx')
  const next = mainJsx.indexOf('\n  async function ', start + 1)
  const src = mainJsx.slice(start, next === -1 ? mainJsx.length : next)

  const [{ value: fn }] = await evalExpressions([`(${src})`], {
    bindings: {
      api: 'globalThis.__acctApi',
      logout: 'globalThis.__acctLogout',
      setDeleteAccountBusy: 'globalThis.__acctSetBusy',
      setDeleteAccountError: 'globalThis.__acctSetError',
      setDeleteAccountOpen: 'globalThis.__acctSetOpen',
    },
  })
  assert.equal(typeof fn, 'function')
  await fn()
  assert.deepEqual(seen[0], ['/v1/user/account', { method: 'DELETE', useSession: true }],
    'deleteAccount must DELETE the session account')
  assert.deepEqual(seen[1], ['logout'], 'a scheduled deletion must end the session')
})
