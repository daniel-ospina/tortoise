// githubDisconnect.test.js — #4946 item 3.
//
// #4946's own scope note deferred the UI/copy half while `main.jsx` was
// congested (#5104 + three unlanded lane-3 PRs). That congestion has cleared,
// and this guard covers the increment's two claims:
//
//   * the CONSEQUENCE COPY is present and honest — the issue exists because the
//     pre-#1924 "disconnect" was cosmetic, so a disconnect that does not state
//     its consequence (a new OAuth to reconnect) reproduces the defect in prose;
//   * the WIRING reaches the endpoint — #5598 shipped
//     `POST /v1/onboarding/github/disconnect` with no caller at all.
//
// Both halves are EXECUTED, not grepped. The control is a `createElement` module
// (importable without a JSX transform), rendered with `react-dom/server`; the
// `main.jsx` call site and the `disconnectGithub` handler are compiled and run
// through the shared `jsxSourceProbe` (the #4637 lesson: five review cycles of
// text pins passed while the rendered card said something else).
//
// Class-B mutation evidence (run, observed RED, reverted) is listed in the PR.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import {
  GithubDisconnectControl, GITHUB_DISCONNECT_CONSEQUENCE,
  disconnectOutcomeMessage, disconnectReasonText,
} from './githubDisconnect.js'
import { importsFromMain, probeTags, evalExpressions } from './jsxSourceProbe.js'

const HERE = dirname(fileURLToPath(import.meta.url))
const mainJsx = readFileSync(join(HERE, 'main.jsx'), 'utf8')

const baseProps = {
  open: false, busy: false, error: '', result: null,
  onOpen() {}, onClose() {}, onConfirm() {},
}

function render(props) {
  return renderToStaticMarkup(React.createElement(GithubDisconnectControl, props))
    .replace(/&#x27;/g, "'")
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
  const button = collect(GithubDisconnectControl(props), 'button')
    .find((b) => textOf(b.props.children) === label)
  assert.ok(button, `no button labelled ${JSON.stringify(label)} was rendered`)
  return button
}

// ── the copy of record ─────────────────────────────────────────────────────
test('#4946: the consequence copy states the re-authorization consequence', () => {
  // Spelled out here as the independent copy of record — a change to the
  // module's constant alone must fail this test.
  assert.ok(/re-?connecting requires authoriz\w+ github/i.test(GITHUB_DISCONNECT_CONSEQUENCE),
    `the copy must state the consequence (a new GitHub authorization) — got: ${GITHUB_DISCONNECT_CONSEQUENCE}`)
  // It must not claim indexed data is removed — a disconnect clears no memory.
  assert.ok(/stay in your graph/i.test(GITHUB_DISCONNECT_CONSEQUENCE),
    'the copy must say indexed issues/docs survive the disconnect')
})

test('#4946: the open dialog renders the consequence copy verbatim', () => {
  const html = render({ ...baseProps, open: true })
  assert.ok(html.includes(GITHUB_DISCONNECT_CONSEQUENCE),
    'the confirm dialog must render the consequence copy verbatim')
})

// ── the control's shape ────────────────────────────────────────────────────
test('#4946: the dialog renders only when opened, and offers exactly two ruled actions', () => {
  assert.ok(!render({ ...baseProps, open: false }).includes('role="dialog"'),
    'no dialog before the control is used')
  const open = render({ ...baseProps, open: true })
  assert.ok(open.includes('role="dialog"'), 'the confirm must be a real dialog')
  const labels = collect(GithubDisconnectControl({ ...baseProps, open: true }), 'button')
    .map((b) => textOf(b.props.children))
  assert.deepEqual(labels, ['Disconnect GitHub', 'Cancel', 'Disconnect'],
    'exactly the opener plus the two actions')
})

test('#4946: the opener only opens — it never disconnects', () => {
  let opens = 0
  let confirms = 0
  buttonByLabel({
    ...baseProps, onOpen: () => { opens += 1 }, onConfirm: () => { confirms += 1 },
  }, 'Disconnect GitHub').props.onClick()
  assert.equal(opens, 1)
  assert.equal(confirms, 0, 'the opener must not make the request')
})

test('#4946: confirm requests the disconnect; cancel does NOT', () => {
  let confirms = 0
  let closes = 0
  const props = { ...baseProps, open: true, onConfirm: () => { confirms += 1 }, onClose: () => { closes += 1 } }
  buttonByLabel(props, 'Disconnect').props.onClick({ currentTarget: null })
  assert.equal(confirms, 1)
  assert.equal(closes, 0, 'confirm must not route through close')

  buttonByLabel(props, 'Cancel').props.onClick()
  assert.equal(closes, 1)
  assert.equal(confirms, 1, 'cancel must NOT request the disconnect')
})

test('#4946: a failed disconnect surfaces inside the still-open dialog', () => {
  const html = render({ ...baseProps, open: true, error: 'Could not disconnect GitHub — try again.' })
  assert.ok(html.includes('role="alert"'), 'the failure must be announced')
  assert.ok(html.includes('Could not disconnect GitHub'), 'the reason must be shown')
  assert.ok(html.includes(GITHUB_DISCONNECT_CONSEQUENCE),
    'the confirm copy stays while the retry is armed')
})

// ── the honest outcome (the whole point of the issue) ──────────────────────
test('#4946: a confirmed revocation reads as clean; an unconfirmed one does NOT', () => {
  const clean = disconnectOutcomeMessage({ connected: false, revoked: true, revoke_reason: 'revoked' })
  assert.ok(/revoked/i.test(clean), `a confirmed revoke must say so — got: ${clean}`)

  const unconfirmed = disconnectOutcomeMessage({ connected: false, revoked: false, revoke_reason: 'http_422' })
  assert.ok(!/^GitHub is disconnected —/.test(unconfirmed),
    `an unconfirmed revocation must not open like a clean one — got: ${unconfirmed}`)
  assert.ok(/not confirmed/i.test(unconfirmed), 'the unconfirmed outcome must say so')
  assert.ok(/422/.test(unconfirmed), 'the unconfirmed outcome must name GitHub\'s answer')
  assert.ok(/GitHub → Settings → Applications/.test(unconfirmed),
    'the unconfirmed outcome must point at GitHub\'s own settings')
})

test('#4946: every server revoke_reason degrades to honest words, never to undefined', () => {
  for (const reason of ['not_connected', 'undecryptable', 'not_configured', 'network', 'http_404', 'something_new']) {
    const text = disconnectReasonText(reason)
    assert.ok(typeof text === 'string' && text.length > 0, `${reason} must render words`)
    assert.ok(!/undefined/.test(text), `${reason} must not render "undefined"`)
  }
  assert.equal(disconnectReasonText(''), 'the endpoint reported no reason')
  assert.ok(/404/.test(disconnectReasonText('http_404')), 'the HTTP arm names the status')
  assert.ok(/"something_new"/.test(disconnectReasonText('something_new')),
    'an unknown reason is reported verbatim rather than dropped')
})

test('#4946: the outcome panel replaces the confirm actions and offers a close', () => {
  const labels = collect(GithubDisconnectControl({
    ...baseProps, open: true, result: { revoked: true, revoke_reason: 'revoked' },
  }), 'button').map((b) => textOf(b.props.children))
  assert.deepEqual(labels, ['Disconnect GitHub', 'Close'],
    'after the response the confirm/cancel pair gives way to a single close')
  const html = render({ ...baseProps, open: true, result: { revoked: true, revoke_reason: 'revoked' } })
  assert.ok(!html.includes(GITHUB_DISCONNECT_CONSEQUENCE),
    'the confirm copy is gone once the outcome is known')
})

// ── a11y (the #2392 / #4029 dialog conventions) ────────────────────────────
test('#4946 (a11y): the dialog autoFocuses Cancel, never the affirmative CTA', () => {
  const buttons = collect(GithubDisconnectControl({ ...baseProps, open: true }), 'button')
  const cancel = buttons.find((b) => textOf(b.props.children) === 'Cancel')
  const confirm = buttons.find((b) => textOf(b.props.children) === 'Disconnect')
  assert.equal(cancel.props.autoFocus, true, 'Cancel must take focus on open (#2392)')
  assert.ok(!confirm.props.autoFocus,
    'the affirmative CTA must not be autofocused — a stray Enter must not disconnect')
})

test('#4946 (a11y): the busy transition is announced on a polite live region', () => {
  const statusOf = (props) => collect(GithubDisconnectControl(props), 'span')
    .find((s) => s.props.role === 'status')
  const idle = statusOf({ ...baseProps, open: true })
  assert.ok(idle, 'the dialog needs a role=status live region')
  assert.equal(idle.props['aria-live'], 'polite', 'the announcement must be polite')
  assert.equal(textOf(idle.props.children), '', 'nothing is announced before the request')
  assert.equal(textOf(statusOf({ ...baseProps, open: true, busy: true }).props.children), 'Disconnecting…',
    'the in-flight request must be announced')
})

// ── the wiring main.jsx ships, compiled and EXECUTED ───────────────────────
const MAIN_IMPORTS = importsFromMain(mainJsx, ['GithubDisconnectControl'])

async function wiringProbe(state) {
  const calls = { open: 0, close: 0, confirm: 0 }
  globalThis.__gdWiring = {
    state,
    open: () => { calls.open += 1 },
    close: () => { calls.close += 1 },
    confirm: () => { calls.confirm += 1 },
  }
  const probes = await probeTags(mainJsx, {
    tag: 'GithubDisconnectControl',
    imports: MAIN_IMPORTS,
    bindings: {
      githubDisconnect: 'globalThis.__gdWiring.state',
      onOpenGithubDisconnect: 'globalThis.__gdWiring.open',
      onCloseGithubDisconnect: 'globalThis.__gdWiring.close',
      onConfirmGithubDisconnect: 'globalThis.__gdWiring.confirm',
    },
  })
  assert.equal(probes.length, 1, 'main.jsx must render exactly one <GithubDisconnectControl .../>')
  return { props: probes[0].props, calls }
}

test('#4946 wiring: the three action props are distinct, real handlers', async () => {
  const { props, calls } = await wiringProbe({ open: true, busy: false, error: '', result: null })
  assert.equal(typeof props.onOpen, 'function')
  assert.equal(typeof props.onClose, 'function')
  assert.equal(typeof props.onConfirm, 'function')
  props.onOpen()
  props.onClose()
  props.onConfirm()
  assert.deepEqual(calls, { open: 1, close: 1, confirm: 1 },
    'each action must reach its own handler — a swapped pair would show here')
})

test('#4946 wiring: the control\'s props are driven by the disconnect state', async () => {
  const { props } = await wiringProbe({
    open: true, busy: true, error: 'boom', result: { revoked: false, revoke_reason: 'network' },
  })
  assert.equal(props.open, true, 'open state must reach the control')
  assert.equal(props.busy, true, 'busy state must reach the control')
  assert.equal(props.error, 'boom', 'the error must reach the control')
  assert.deepEqual(props.result, { revoked: false, revoke_reason: 'network' },
    'the endpoint response must reach the control for the honest outcome panel')
})

/**
 * The source of the `function <name>(…) { … }` declaration in main.jsx, by
 * brace matching. A delimiter-based extractor (`indexOf('\n  async function ')`)
 * is wrong here: `disconnectGithub` is followed by two non-async helpers, so it
 * would slice across their boundaries and hand esbuild a syntax error.
 */
function extractFunction(name) {
  const start = mainJsx.search(new RegExp(`(?:async\\s+)?function ${name}\\(`))
  assert.notEqual(start, -1, `${name} must exist in main.jsx`)
  const open = mainJsx.indexOf('{', mainJsx.indexOf(')', start))
  assert.notEqual(open, -1, `${name} must have a body`)
  let depth = 0
  let mode = null
  let i = open
  for (; i < mainJsx.length; i += 1) {
    const c = mainJsx[i]
    const n = mainJsx[i + 1]
    if (mode === null) {
      if (c === "'" || c === '"' || c === '`') { mode = c; continue }
      if (c === '/' && n === '/') { mode = '//'; i += 1; continue }
      if (c === '/' && n === '*') { mode = '/*'; i += 1; continue }
      if (c === '{') depth += 1
      else if (c === '}') { depth -= 1; if (depth === 0) break }
    } else if (mode === '//') { if (c === '\n') mode = null }
    else if (mode === '/*') { if (c === '*' && n === '/') { mode = null; i += 1 } }
    else if (mode === "'" || mode === '"') { if (c === '\\') i += 1; else if (c === mode || c === '\n') mode = null }
    else if (mode === '`') { if (c === '\\') i += 1; else if (c === '`') mode = null }
  }
  return mainJsx.slice(start, i + 1)
}

async function runDisconnectGithub(apiImpl) {
  const seen = []
  const updates = []
  globalThis.__gdApi = apiImpl
  globalThis.__gdSet = (updater) => {
    updates.push(typeof updater === 'function'
      ? updater({ open: true, busy: false, error: 'stale', result: null })
      : updater)
  }
  globalThis.__gdRefresh = () => { seen.push(['refresh']); return Promise.resolve() }
  const [{ value: fn }] = await evalExpressions([`(${extractFunction('disconnectGithub')})`], {
    bindings: {
      api: 'globalThis.__gdApi',
      setGithubDisconnect: 'globalThis.__gdSet',
      refreshOnboarding: 'globalThis.__gdRefresh',
      onboardingTeamQ: "() => '?org_id=org_1'",
    },
  })
  assert.equal(typeof fn, 'function')
  await fn()
  return { updates }
}

test('#4946 wiring: disconnectGithub POSTs the landed endpoint and refreshes (EXECUTED)', async () => {
  const seen = []
  const { updates } = await runDisconnectGithub(async (path, opts) => {
    seen.push([path, opts])
    return { connected: false, revoked: true, revoke_reason: 'revoked' }
  })
  assert.deepEqual(seen[0], ['/v1/onboarding/github/disconnect?org_id=org_1',
    { method: 'POST', useSession: true }],
  'the handler must POST the endpoint #5598 landed, with the team scope')
  assert.equal(updates[0].busy, true, 'the request arms the busy state')
  assert.equal(updates[0].error, '', 'a new attempt clears the stale error')
  assert.deepEqual(updates[1], {
    open: true, busy: false, error: 'stale', result: { connected: false, revoked: true, revoke_reason: 'revoked' },
  }, 'the response body is kept for the honest outcome panel')
})

test('#4946 wiring: a failed disconnect keeps the dialog open with the reason (EXECUTED)', async () => {
  const { updates } = await runDisconnectGithub(async () => { throw new Error('HTTP 500') })
  const last = updates[updates.length - 1]
  assert.equal(last.busy, false, 'the in-flight state must clear on failure')
  assert.equal(last.error, 'HTTP 500', 'the server reason must reach the dialog')
  assert.equal(last.open, true, 'a failed attempt keeps the dialog open for a retry')
  assert.equal(last.result, null, 'a failure must not produce an outcome panel')
})
