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
// `main.jsx` call site and the `disconnectGithub` / open / close handlers are
// compiled and run through the shared `jsxSourceProbe` (the #4637 lesson: five
// review cycles of text pins passed while the rendered card said something else).
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
import { stripComments } from './testSupport.js'

const HERE = dirname(fileURLToPath(import.meta.url))
const mainJsx = readFileSync(join(HERE, 'main.jsx'), 'utf8')

const baseProps = {
  connected: true,
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

const labelsOf = (props) => collect(GithubDisconnectControl(props), 'button').map((b) => textOf(b.props.children))

function buttonByLabel(props, label) {
  const button = collect(GithubDisconnectControl(props), 'button')
    .find((b) => textOf(b.props.children) === label)
  assert.ok(button, `no button labelled ${JSON.stringify(label)} was rendered`)
  return button
}

/** The dialog element of a rendered control. */
const dialogOf = (props) => collect(GithubDisconnectControl(props), 'div')
  .find((d) => d.props.role === 'dialog')
const backdropOf = (props) => collect(GithubDisconnectControl(props), 'div')
  .find((d) => d.props.className === 'modal-backdrop')

// ── the copy of record ─────────────────────────────────────────────────────
// Spelled out here as the INDEPENDENT copy of record (the accountDeletion
// `DELETE_ACCOUNT_WARNING` pattern): a change to the module's own constant
// alone must fail this test. A regex pair is not enough — it left the
// token-clearing promise (the substance of #4946's revoke-then-clear decision)
// unpinned.
const REQUIRED_COPY =
  "Disconnecting revokes Tortoise's access to GitHub and clears the stored token. "
  + 'Issues and docs you have already indexed stay in your graph. '
  + 'Re-connecting requires authorizing GitHub again.'

test('#4946: the consequence copy is the copy of record, verbatim', () => {
  assert.equal(GITHUB_DISCONNECT_CONSEQUENCE, REQUIRED_COPY)
})

test('#4946: the open dialog renders the consequence copy verbatim', () => {
  const html = render({ ...baseProps, open: true })
  assert.ok(html.includes(REQUIRED_COPY),
    'the confirm dialog must render the consequence copy verbatim')
})

// ── the control's shape ────────────────────────────────────────────────────
test('#4946: the dialog renders only when opened, and offers exactly two ruled actions', () => {
  assert.ok(!render({ ...baseProps, open: false }).includes('role="dialog"'),
    'no dialog before the control is used')
  const open = render({ ...baseProps, open: true })
  assert.ok(open.includes('role="dialog"'), 'the confirm must be a real dialog')
  assert.deepEqual(labelsOf({ ...baseProps, open: true }),
    ['Disconnect GitHub', 'Cancel', 'Disconnect'],
    'exactly the opener plus the two actions')
})

test('#4946: rendering the dialog emits no React key warning', () => {
  // `actions(...children)` passing the rest ARRAY as a single child made React
  // warn "Each child in a list should have a unique key" on every render; the
  // spread fixes it and nothing else would notice.
  const errors = []
  const orig = console.error
  console.error = (...args) => { errors.push(String(args[0])) }
  try {
    render({ ...baseProps, open: true })
    render({ ...baseProps, open: true, result: { revoked: true, revoke_reason: 'revoked' } })
  } finally {
    console.error = orig
  }
  const keyWarnings = errors.filter((e) => /unique "key"/.test(e))
  assert.deepEqual(keyWarnings, [], `unkeyed list children warn on every render — got: ${keyWarnings}`)
})

test('#4946: disconnecting hides the opener but keeps the outcome dialog open', () => {
  // The opener must not survive the connection flipping false...
  assert.deepEqual(labelsOf({ ...baseProps, connected: false, open: false }), [],
    'no opener once GitHub is disconnected')
  // ...but the DIALOG must. main.jsx renders this control OUTSIDE the connected
  // branch precisely because the post-disconnect refresh flips
  // `github_connected` false while the outcome panel is still on screen; nesting
  // it in that branch unmounted the honest warning before it could be read.
  const html = render({
    ...baseProps, connected: false, open: true, result: { revoked: false, revoke_reason: 'network' },
  })
  assert.ok(html.includes('role="dialog"'), 'the outcome dialog survives the disconnect')
  assert.ok(/not confirmed/i.test(html), 'the honest warning is reachable after the flip')
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
  assert.ok(html.includes(REQUIRED_COPY), 'the confirm copy stays while the retry is armed')
})

// ── the honest outcome (the whole point of the issue) ──────────────────────
test('#4946: a confirmed revocation reads as clean; an unconfirmed one does NOT', () => {
  const clean = disconnectOutcomeMessage({ connected: false, revoked: true, revoke_reason: 'revoked' })
  assert.ok(/access is revoked/i.test(clean), `a confirmed revoke must say so — got: ${clean}`)

  const unconfirmed = disconnectOutcomeMessage({ connected: false, revoked: false, revoke_reason: 'http_422' })
  assert.ok(!/^GitHub is disconnected —/.test(unconfirmed),
    `an unconfirmed revocation must not open like a clean one — got: ${unconfirmed}`)
  assert.ok(/not confirmed/i.test(unconfirmed), 'the unconfirmed outcome must say so')
  assert.ok(/422/.test(unconfirmed), 'the unconfirmed outcome must name GitHub\'s answer')
  assert.ok(/GitHub → Settings → Applications/.test(unconfirmed),
    'the unconfirmed outcome must point at GitHub\'s own settings')
})

test('#4946: the idempotent no-op does not claim a revocation that never happened', () => {
  // The endpoint reports `revoked: true` with `revoke_reason: 'not_connected'`
  // when NO token was stored — nothing was sent to GitHub. Rendering the clean
  // "access is revoked" string here would be exactly the cosmetic lie #4946
  // exists to remove.
  const noop = disconnectOutcomeMessage({ connected: false, revoked: true, revoke_reason: 'not_connected' })
  assert.ok(!/access is revoked/i.test(noop), `nothing was revoked — got: ${noop}`)
  assert.ok(/no stored token to revoke/i.test(noop), `the no-op must say what happened — got: ${noop}`)
})

test('#4946: every server revoke_reason degrades to honest words, never to undefined', () => {
  // Includes Object.prototype keys (a JSON body can carry any string): an
  // unguarded `REASON_TEXT[reason]` lookup would return a native function and
  // render it into the copy.
  for (const reason of ['not_connected', 'undecryptable', 'not_configured', 'network', 'http_404',
    'constructor', 'toString', '__proto__', 'something_new']) {
    const text = disconnectReasonText(reason)
    assert.ok(typeof text === 'string' && text.length > 0, `${reason} must render words`)
    assert.ok(!/undefined/.test(text), `${reason} must not render "undefined"`)
    assert.ok(!/native code/.test(text), `${reason} must not leak a prototype member: ${text}`)
  }
  assert.equal(disconnectReasonText(''), 'the endpoint reported no reason')
  // 422 is GitHub's "the token is not valid" answer — it may be gone.
  assert.ok(/may already be gone/.test(disconnectReasonText('http_422')),
    '422 means GitHub rejected the token as no longer valid')
  // 404 on this route says the REQUEST or the app was not recognised, which
  // establishes nothing about the token — it must take the neutral arm, never
  // the "may already be gone" reassurance.
  const notFound = disconnectReasonText('http_404')
  assert.ok(/404/.test(notFound), 'the arm names the status')
  assert.ok(/unknown/.test(notFound), '404 must say the liveness is unknown')
  assert.ok(!/may already be gone/.test(notFound),
    '404 establishes nothing about the token, so it must not claim it may be gone')
  // 401/403: the REQUEST was rejected before the token was touched — it is live.
  // Assert the SPECIFIC arm, not merely the words "still live", which the
  // generic fallback also contains.
  for (const code of ['http_401', 'http_403']) {
    const text = disconnectReasonText(code)
    assert.ok(!/may already be gone/.test(text), `${code} must not imply the token is gone`)
    assert.ok(/app credentials/.test(text), `${code} must name the rejected request: ${text}`)
  }
  // A 5xx means GitHub could not process the call, so the token is most likely live.
  assert.ok(/probably still live/.test(disconnectReasonText('http_500')),
    'a 5xx must read as probably still live, not as an unknown or as gone')
  assert.ok(/"something_new"/.test(disconnectReasonText('something_new')),
    'an unknown reason is reported verbatim rather than dropped')
})

test('#4946: the outcome panel replaces the confirm actions and offers a close', () => {
  assert.deepEqual(labelsOf({ ...baseProps, open: true, result: { revoked: true, revoke_reason: 'revoked' } }),
    ['Disconnect GitHub', 'Close'],
    'after the response the confirm/cancel pair gives way to a single close')
  const html = render({ ...baseProps, open: true, result: { revoked: true, revoke_reason: 'revoked' } })
  assert.ok(!html.includes(REQUIRED_COPY), 'the confirm copy is gone once the outcome is known')
})

test('#4946: the outcome panel\'s Close only closes — it must not re-request the disconnect', () => {
  let closes = 0
  let confirms = 0
  const props = {
    ...baseProps, open: true, result: { revoked: true, revoke_reason: 'revoked' },
    onClose: () => { closes += 1 }, onConfirm: () => { confirms += 1 },
  }
  buttonByLabel(props, 'Close').props.onClick()
  assert.equal(closes, 1, 'Close must dismiss')
  assert.equal(confirms, 0, 'Close must not re-POST the disconnect')
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

test('#4946 (a11y): the dialog is a focusable, Escape-closable modal with a backdrop close', () => {
  const dialog = dialogOf({ ...baseProps, open: true })
  assert.ok(dialog, 'the dialog element must render')
  // tabIndex -1 is load-bearing: BOTH the busy-reclaim effect and
  // focusDialogContainer call .focus() on this div, which is a no-op without it.
  assert.equal(dialog.props.tabIndex, -1, 'the dialog container must be focusable')
  assert.equal(dialog.props['aria-modal'], 'true')
  let closes = 0
  dialog.props.onKeyDown({ key: 'Enter' })
  assert.equal(closes, 0, 'a non-Escape key must not close')

  const props = { ...baseProps, open: true, onClose: () => { closes += 1 } }
  dialogOf(props).props.onKeyDown({ key: 'Escape' })
  assert.equal(closes, 1, 'Escape must close the dialog')
  backdropOf(props).props.onClick()
  assert.equal(closes, 2, 'the backdrop must close the dialog')
})

test('#4946 (a11y): no close path, and no second confirm, fires while the request is in flight', () => {
  // The `!busy` guards: a user who dismisses mid-request would abandon a dialog
  // whose POST still completes and still flips the connection — the inverse of
  // the honest control #4946 exists to enforce.
  let closes = 0
  let confirms = 0
  const busy = {
    ...baseProps, open: true, busy: true,
    onClose: () => { closes += 1 }, onConfirm: () => { confirms += 1 },
  }
  dialogOf(busy).props.onKeyDown({ key: 'Escape' })
  backdropOf(busy).props.onClick()
  buttonByLabel(busy, 'Cancel').props.onClick()
  buttonByLabel(busy, 'Disconnecting…').props.onClick({ currentTarget: null })
  assert.equal(closes, 0, 'no close path may fire while the request is in flight')
  assert.equal(confirms, 0, 'a second confirm must not fire while in flight')
  // …and the controls are disabled, so a click cannot even reach the guards.
  for (const label of ['Cancel', 'Disconnecting…']) {
    assert.equal(buttonByLabel(busy, label).props.disabled, true, `${label} must be disabled while busy`)
  }
  assert.equal(buttonByLabel(busy, 'Disconnect GitHub').props.disabled, true,
    'the opener must be disabled while busy')
})

test('#4946 (a11y): confirming moves focus into the dialog container (#4029 reclaim)', () => {
  let focused = 0
  let confirms = 0
  const container = { focus: () => { focused += 1 } }
  const event = { currentTarget: { closest: (sel) => (sel === '[role="dialog"]' ? container : null) } }
  buttonByLabel({ ...baseProps, open: true, onConfirm: () => { confirms += 1 } }, 'Disconnect')
    .props.onClick(event)
  assert.equal(focused, 1, 'the confirm must focus the dialog container (which is why tabIndex -1 is load-bearing)')
  assert.equal(confirms, 1, 'the confirm must still make the request')
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

// The GitHub-connect home as ONE JSX expression, so the control's placement can
// be EXECUTED rather than reasoned about from text. A structural text guard
// ("is it after the ternary?") is defeated by a differently-spelled gate —
// `githubConnected ? (…) : null`, `!githubConnected || (…)`, or nesting in the
// OTHER arm — all of which reintroduce the #4946 P1 unmount. Rendering the
// region cannot be satisfied by any of them.
const GH_SECTION = (() => {
  const stripped = stripComments(mainJsx)
  const start = stripped.indexOf('<section className="settings-home" aria-labelledby="settings-github-heading">')
  assert.ok(start > -1, 'the GitHub-connect section must exist in main.jsx')
  const end = stripped.indexOf('</section>', start)
  assert.ok(end > start, 'the GitHub-connect section must close')
  return stripped.slice(start, end + '</section>'.length)
})()

async function renderGithubSection(connected, state) {
  globalThis.__gdSection = {
    loading: false, githubConnected: connected, reposNote: '3 repos available',
    onConnectGithub: () => {}, github: { busy: false }, githubError: '',
    githubDisconnect: state,
    onOpenGithubDisconnect: () => {}, onCloseGithubDisconnect: () => {}, onConfirmGithubDisconnect: () => {},
  }
  const [{ html }] = await evalExpressions([`(${GH_SECTION})`], {
    imports: MAIN_IMPORTS,
    bindings: {
      loading: 'globalThis.__gdSection.loading',
      githubConnected: 'globalThis.__gdSection.githubConnected',
      reposNote: 'globalThis.__gdSection.reposNote',
      onConnectGithub: 'globalThis.__gdSection.onConnectGithub',
      github: 'globalThis.__gdSection.github',
      githubError: 'globalThis.__gdSection.githubError',
      githubDisconnect: 'globalThis.__gdSection.githubDisconnect',
      onOpenGithubDisconnect: 'globalThis.__gdSection.onOpenGithubDisconnect',
      onCloseGithubDisconnect: 'globalThis.__gdSection.onCloseGithubDisconnect',
      onConfirmGithubDisconnect: 'globalThis.__gdSection.onConfirmGithubDisconnect',
    },
  })
  return html
}

test('#4946 (P1 placement, EXECUTED): the dialog survives github_connected flipping false', async () => {
  const connected = await renderGithubSection(true, { open: false, busy: false, error: '', result: null })
  assert.ok(connected.includes('>Disconnect GitHub</button>'),
    'the opener must render while connected — any gate that hides it makes the disconnect unreachable')
  assert.ok(!connected.includes('role="dialog"'), 'no dialog before the opener is used')

  // The state the disconnect itself produces: the flag flipped false while the
  // outcome panel is on screen. Nesting the control in EITHER ternary arm fails
  // one of these two assertions.
  const flipped = await renderGithubSection(false, {
    open: true, busy: false, error: '', result: { revoked: false, revoke_reason: 'network' },
  })
  assert.ok(flipped.includes('role="dialog"'),
    'the dialog must SURVIVE github_connected flipping false — the outcome panel unmounts otherwise (#4946 P1)')
  assert.ok(/not confirmed/i.test(flipped), 'the honest warning stays readable after the flip')
  assert.ok(!flipped.includes('>Disconnect GitHub</button>'), 'the opener is gone once disconnected')
})

test('#4946 (a11y): main.jsx reclaims focus to the dialog while the request is in flight', () => {
  // A React effect the node suite cannot execute; pin the two facts that make it
  // work — that it is keyed on the disconnect busy state, and the id it targets.
  const src = stripComments(mainJsx)
  const at = src.indexOf('if (!githubDisconnect.open || !githubDisconnect.busy) return')
  assert.ok(at > -1, 'the busy-reclaim effect must exist in main.jsx')
  assert.match(src.slice(at, at + 600), /getElementById\('github-disconnect-dialog'\)/,
    'the reclaim must target the dialog container (the reason tabIndex -1 is load-bearing)')
})

async function wiringProbe(state, connected = true) {
  const calls = { open: 0, close: 0, confirm: 0 }
  globalThis.__gdWiring = {
    state,
    connected,
    open: () => { calls.open += 1 },
    close: () => { calls.close += 1 },
    confirm: () => { calls.confirm += 1 },
  }
  const probes = await probeTags(mainJsx, {
    tag: 'GithubDisconnectControl',
    imports: MAIN_IMPORTS,
    bindings: {
      githubConnected: 'globalThis.__gdWiring.connected',
      githubDisconnect: 'globalThis.__gdWiring.state',
      onOpenGithubDisconnect: 'globalThis.__gdWiring.open',
      onCloseGithubDisconnect: 'globalThis.__gdWiring.close',
      onConfirmGithubDisconnect: 'globalThis.__gdWiring.confirm',
    },
  })
  assert.equal(probes.length, 1, 'main.jsx must render exactly one <GithubDisconnectControl .../>')
  return { props: probes[0].props, calls }
}

test('#4946 wiring: each action prop IS its own handler (identity, not just counts)', async () => {
  // A count-based assertion is a bijection: swapping onOpen/onClose passes it.
  // Assert identity against the three distinct sentinels instead.
  const { props } = await wiringProbe({ open: true, busy: false, error: '', result: null })
  assert.equal(props.onOpen, globalThis.__gdWiring.open, 'onOpen must BE the open handler')
  assert.equal(props.onClose, globalThis.__gdWiring.close, 'onClose must BE the close handler')
  assert.equal(props.onConfirm, globalThis.__gdWiring.confirm, 'onConfirm must BE the confirm handler')
})

test('#4946 wiring: the control\'s props are driven by the disconnect state, not literals', async () => {
  const { props } = await wiringProbe({
    open: true, busy: true, error: 'boom', result: { revoked: false, revoke_reason: 'network' },
  })
  assert.equal(props.open, true, 'open state must reach the control')
  assert.equal(props.busy, true, 'busy state must reach the control')
  assert.equal(props.error, 'boom', 'the error must reach the control')
  assert.deepEqual(props.result, { revoked: false, revoke_reason: 'network' },
    'the endpoint response must reach the control for the honest outcome panel')
  assert.equal(props.connected, true, 'the connection state must reach the control')

  // The CLOSED half too — without it a hardcoded `open={true}` (a permanently
  // open confirm dialog whenever GitHub is connected) survives every test.
  const closed = await wiringProbe({ open: false, busy: false, error: '', result: null }, false)
  assert.equal(closed.props.open, false, 'the closed state must reach the control')
  assert.equal(closed.props.connected, false, 'a disconnected control must be told so')
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
  const updates = []
  const refreshes = []
  globalThis.__gdApi = apiImpl
  // The setter CHAINS each updater onto the previous result, the way React
  // does. A frozen seed would hand every updater `busy:false`, so an assertion
  // that the in-flight state CLEARS would be satisfied by the seed rather than
  // by the handler — and a permanent busy-lock (every close path is
  // `!busy`-guarded and every button is `disabled={busy}`, so the modal can
  // never be dismissed) went unnoticed.
  let state = { open: true, busy: false, error: 'stale', result: null }
  globalThis.__gdSet = (updater) => {
    state = typeof updater === 'function' ? updater(state) : updater
    updates.push(state)
  }
  globalThis.__gdRefresh = () => { refreshes.push(1); return Promise.resolve() }
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
  return { updates, refreshes }
}

test('#4946 wiring: disconnectGithub POSTs the landed endpoint and refreshes (EXECUTED)', async () => {
  const { updates, refreshes } = await runDisconnectGithub(async () =>
    ({ connected: false, revoked: true, revoke_reason: 'revoked' }))
  assert.equal(updates[0].busy, true, 'the request arms the busy state')
  assert.equal(updates[0].error, '', 'a new attempt clears the stale error')
  assert.deepEqual(updates[1], {
    open: true, busy: false, error: '', result: { connected: false, revoked: true, revoke_reason: 'revoked' },
  }, 'the response body is kept for the honest outcome panel, and the in-flight state clears')
  assert.equal(updates[1].busy, false, 'the success arm must clear busy — a permanent lock is undismissable')
  assert.equal(refreshes.length, 1,
    'a successful disconnect must refresh the onboarding projection so the card stops saying Connected')
})

test('#4946 wiring: the request goes to the disconnect endpoint with the team scope (EXECUTED)', async () => {
  const seen = []
  await runDisconnectGithub(async (path, opts) => {
    seen.push([path, opts])
    return { connected: false, revoked: true, revoke_reason: 'revoked' }
  })
  assert.deepEqual(seen[0], ['/v1/onboarding/github/disconnect?org_id=org_1',
    { method: 'POST', useSession: true }],
  'the handler must POST the endpoint #5598 landed, with the team scope')
})

test('#4946 wiring: a failed disconnect keeps the dialog open with the reason (EXECUTED)', async () => {
  const { updates, refreshes } = await runDisconnectGithub(async () => { throw new Error('HTTP 500') })
  const last = updates[updates.length - 1]
  assert.equal(last.busy, false, 'the in-flight state must clear on failure')
  assert.equal(last.error, 'HTTP 500', 'the server reason must reach the dialog')
  assert.equal(last.open, true, 'a failed attempt keeps the dialog open for a retry')
  assert.equal(last.result, null, 'a failure must not produce an outcome panel')
  // The endpoint clears locally before it answers, so a lost response must not
  // leave the card stale: the failure arm re-reads the server projection.
  assert.equal(refreshes.length, 1,
    'a failed disconnect must still re-read the onboarding projection (the endpoint clears unconditionally)')
})

/** Run the open/close handlers against a capturing setter and focus spies. */
async function runOpenClose({ restoreReturns = {}, result = null } = {}) {
  const calls = { remember: 0, restore: 0 }
  const states = []
  globalThis.__gdFocus = {
    remember: () => { calls.remember += 1 },
    // `'restore' in …` (not `?? {}`) so an explicit `null` return — the
    // detached-opener case — is honoured rather than swallowed by nullish
    // coalescing.
    restore: () => { calls.restore += 1; return 'restore' in restoreReturns ? restoreReturns.restore : {} },
  }
  globalThis.__gdSetState = (v) => {
    states.push(typeof v === 'function' ? v({ open: false, busy: true, error: 'e', result: 'r' }) : v)
  }
  const [{ value: openFn }, { value: closeFn }] = await evalExpressions([
    `(${extractFunction('openGithubDisconnect')})`,
    `(${extractFunction('closeGithubDisconnect')})`,
  ], {
    bindings: {
      rememberFocusedTrigger: 'globalThis.__gdFocus.remember',
      restoreFocus: 'globalThis.__gdFocus.restore',
      setGithubDisconnect: 'globalThis.__gdSetState',
      githubDisconnect: JSON.stringify({ result }),
      githubDisconnectRestoreRef: '{}',
    },
  })
  return { openFn, closeFn, calls, states }
}

test('#4946 (a11y) wiring: opening captures the trigger and actually opens (EXECUTED)', async () => {
  const { openFn, calls, states } = await runOpenClose()
  openFn()
  assert.equal(calls.remember, 1, 'the opener must capture the trigger for focus restore (#2392)')
  assert.equal(calls.restore, 0, 'opening must not restore focus')
  assert.deepEqual(states[0], { open: true, busy: true, error: '', result: null },
    'opening must set open and clear stale error/result — a no-op leaves the dialog unopenable')
})

test('#4946 (a11y) wiring: closing restores focus and fully resets state (EXECUTED)', async () => {
  const { closeFn, calls, states } = await runOpenClose()
  closeFn()
  assert.equal(calls.restore, 1, 'every close must try to hand focus back to the opener (#2392)')
  assert.deepEqual(states[0], { open: false, busy: false, error: '', result: null },
    'closing must fully reset — a no-op leaves the dialog unclosable')
})

test('#4946 (a11y) wiring: a close after the opener unmounted parks focus on the heading (EXECUTED)', async () => {
  // After a SUCCESSFUL disconnect the opener has unmounted, and `restoreFocus`
  // deliberately returns null for a detached node — so the close must fall back
  // to the GitHub-connect heading instead of dropping focus onto <body>.
  const focused = []
  globalThis.document = {
    getElementById: (id) => (id === 'settings-github-heading' ? { focus: () => focused.push(id) } : null),
  }
  try {
    const { closeFn } = await runOpenClose({ restoreReturns: { restore: null } })
    closeFn()
    assert.deepEqual(focused, ['settings-github-heading'],
      'a detached opener must fall back to the heading, not <body>')
  } finally {
    delete globalThis.document
  }
})

test('#4946 (a11y) wiring: a close with the opener still mounted does NOT jump to the heading (EXECUTED)', async () => {
  // The other half of the fallback branch: when `restoreFocus` SUCCEEDS (the
  // opener is still mounted, i.e. every Cancel / backdrop / Escape close),
  // focus must go to the opener — never to the section heading. Without this,
  // an unconditional fallback would silently land focus on the heading and
  // regress #2392 on the ordinary close path.
  const focused = []
  globalThis.document = {
    getElementById: (id) => ({ focus: () => focused.push(id) }),
  }
  try {
    const { closeFn, calls, states } = await runOpenClose({ restoreReturns: { restore: {} } })
    closeFn()
    assert.equal(calls.restore, 1, 'the close must still ask the opener to take focus back')
    assert.deepEqual(focused, [], 'a live opener must not be followed by a heading jump')
    assert.deepEqual(states[0], { open: false, busy: false, error: '', result: null },
      'the reset must still happen on the live-opener path')
  } finally {
    delete globalThis.document
  }
})

test('#4946 (a11y) wiring: a close after a COMPLETED disconnect parks focus on the heading even with the opener still mounted (EXECUTED)', async () => {
  // The endpoint ALWAYS clears the local credential, so `github_connected` goes
  // false once `refreshOnboarding()` lands. Closing the outcome panel before
  // that refresh resolves must NOT restore focus to an opener that is about to
  // unmount — that is the #2392 drop-to-<body> path, and it survives any test
  // that only exercises the already-detached case.
  const focused = []
  globalThis.document = {
    getElementById: (id) => ({ focus: () => focused.push(id) }),
  }
  try {
    const { closeFn, calls, states } = await runOpenClose({
      restoreReturns: { restore: {} }, result: { revoked: true, revoke_reason: 'revoked' },
    })
    closeFn()
    assert.equal(calls.restore, 1, 'the close still releases the captured trigger')
    assert.deepEqual(focused, ['settings-github-heading'],
      'a completed disconnect must land on the heading, not on a doomed opener')
    assert.deepEqual(states[0], { open: false, busy: false, error: '', result: null },
      'the reset must still happen on the completed path')
  } finally {
    delete globalThis.document
  }
})

test('#4946 (a11y): the GitHub-connect heading is a programmatic focus target', async () => {
  // Pin the heading on the EXECUTED render, not a source regex: a regex is
  // satisfied by the pattern parked in a comment or in a string literal
  // (#4637), while the rendered heading cannot be. The fallback itself is
  // pinned by the two tests above.
  const html = await renderGithubSection(true, { open: false, busy: false, error: '', result: null })
  assert.match(html, /<h3 id="settings-github-heading" tabindex="-1">/,
    'the heading must carry tabIndex -1 so the close fallback can focus it (#3890 pattern)')
})
