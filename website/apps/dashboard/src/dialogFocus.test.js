// dialogFocus.test.js — run with node --test (zero deps: the #2392 focus-
// restore helpers only read element.isConnected / document.activeElement,
// both optional and injected, so tests use plain-object fakes — no jsdom).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { rememberRestoreTarget, rememberFocusedTrigger, restoreFocus, focusableWithin, containTabFocus, applyBackgroundInert } from './dialogFocus.js'

function makeFake({ id = 'el', connected = true } = {}) {
  const calls = []
  const el = { id, isConnected: connected, focus() { calls.push('focus') } }
  return { el, calls }
}

test('restoreFocus no-ops and clears when nothing was captured', () => {
  const holder = { current: null }
  assert.equal(restoreFocus(holder), null)
  assert.equal(holder.current, null)
  // missing/empty holder is safe too
  assert.equal(restoreFocus(null), null)
  assert.equal(restoreFocus({ current: undefined }), null)
})

test('restoreFocus focuses the captured trigger and is one-shot', () => {
  const { el, calls } = makeFake()
  const holder = { current: el }
  assert.equal(restoreFocus(holder), el)
  assert.deepEqual(calls, ['focus'])
  assert.equal(holder.current, null) // consumed
  // a second close path must not re-focus (no redundant focus() call)
  assert.equal(restoreFocus(holder), null)
  assert.deepEqual(calls, ['focus'])
})

test('restoreFocus skips a disconnected (unmounted) trigger', () => {
  const { el, calls } = makeFake({ connected: false })
  const holder = { current: el }
  assert.equal(restoreFocus(holder), null)
  assert.deepEqual(calls, [])
  assert.equal(holder.current, null)
})

test('restoreFocus does not re-focus an element that already owns focus', () => {
  const { el, calls } = makeFake()
  const holder = { current: el }
  // activeElementOverride === el: focus is already on the trigger — no call
  assert.equal(restoreFocus(holder, el), el)
  assert.deepEqual(calls, [])
  // but a DIFFERENT active element does get displaced back to the trigger
  const other = makeFake()
  holder.current = el
  assert.equal(restoreFocus(holder, other.el), el)
  assert.deepEqual(calls, ['focus'])
})

test('rememberRestoreTarget stores only focusable elements', () => {
  const holder = { current: 'stale' }
  rememberRestoreTarget(holder, null)
  assert.equal(holder.current, null)
  rememberRestoreTarget(holder, 'not-an-element')
  assert.equal(holder.current, null)
  const { el } = makeFake()
  rememberRestoreTarget(holder, el)
  assert.equal(holder.current, el)
})

test('rememberFocusedTrigger captures activeElement but never the body', () => {
  const holder = { current: null }
  const body = makeFake().el
  const trigger = makeFake().el
  // a real focused control is captured as the restore target
  rememberFocusedTrigger(holder, { activeElement: trigger, body })
  assert.equal(holder.current, trigger)
  // the <body> (focus dropped behind a disabled trigger) is never captured
  rememberFocusedTrigger(holder, { activeElement: body, body })
  assert.equal(holder.current, null)
  // no active element at all → nothing captured
  rememberFocusedTrigger(holder, { activeElement: null, body })
  assert.equal(holder.current, null)
  // no document (node env) → nothing captured, no throw
  rememberFocusedTrigger(holder)
  assert.equal(holder.current, null)
})

// ── #2577 APG containment helpers ────────────────────────────────────────
// Same zero-dep convention: the helpers read only element.isConnected /
// hasAttribute / getAttribute / parentElement / focus, all optional, so the
// DOM is modelled with plain-object fakes (no jsdom in this suite).

function fakeEl(id, attrs = {}) {
  const el = {
    id,
    disabled: false,
    parentElement: null,
    attrs: { ...attrs },
    focusCount: 0,
    hasAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attrs, name) },
    getAttribute(name) { return this.hasAttribute(name) ? this.attrs[name] : null },
    setAttribute(name, value) { this.attrs[name] = value },
    removeAttribute(name) { delete this.attrs[name] },
    focus() { this.focusCount += 1 },
  }
  return el
}

// A container whose querySelectorAll always returns `items` — the selector
// string itself is not re-implemented (the DOM test lane proves that half).
function fakeContainer(items) {
  return { querySelectorAll: () => items }
}

test('focusableWithin excludes disabled, tabindex=-1 and inert-subtree controls', () => {
  const ok = fakeEl('ok')
  const disabledProp = fakeEl('disabledProp'); disabledProp.disabled = true
  const disabledAttr = fakeEl('disabledAttr', { disabled: '' })
  const negTab = fakeEl('negTab', { tabindex: '-1' })
  const zeroTab = fakeEl('zeroTab', { tabindex: '0' })
  const hiddenInput = fakeEl('hiddenInput', { type: 'hidden' })
  const inertAncestor = fakeEl('inertAncestor')
  const insideInert = fakeEl('insideInert'); insideInert.parentElement = inertAncestor
  inertAncestor.attrs.inert = ''
  const container = fakeContainer([ok, disabledProp, disabledAttr, negTab, zeroTab, hiddenInput, insideInert])
  assert.deepEqual(focusableWithin(container).map((e) => e.id), ['ok', 'zeroTab'])
  // a non-DOM container (no querySelectorAll) yields no items, not a throw
  assert.deepEqual(focusableWithin({}), [])
  assert.deepEqual(focusableWithin(null), [])
})

test('containTabFocus wraps Tab at the last control and Shift+Tab at the first', () => {
  const a = fakeEl('a')
  const b = fakeEl('b')
  const c = fakeEl('c')

  // forward from the last → first, and the caller must preventDefault
  let container = fakeContainer([a, b, c])
  assert.equal(containTabFocus(container, { activeElement: c }), true)
  assert.equal(a.focusCount, 1)
  // forward from the middle → the browser's own move is left alone
  assert.equal(containTabFocus(container, { activeElement: b }), false)
  assert.equal(b.focusCount, 0)
  // backward from the first → last
  assert.equal(containTabFocus(container, { shiftKey: true, activeElement: a }), true)
  assert.equal(c.focusCount, 1)
  // backward from the middle → untouched
  assert.equal(containTabFocus(container, { shiftKey: true, activeElement: b }), false)
})

test('containTabFocus pulls focus in when it sits outside the dialog', () => {
  const first = fakeEl('first')
  const last = fakeEl('last')
  const container = fakeContainer([first, last])
  // focus on the container itself (tabIndex -1 after a busy reclaim) → first
  assert.equal(containTabFocus(container, { activeElement: null }), true)
  assert.equal(first.focusCount, 1)
  // focus on a background element → forward pulls to first, backward to last
  const outside = fakeEl('outside')
  assert.equal(containTabFocus(container, { activeElement: outside }), true)
  assert.equal(first.focusCount, 2)
  assert.equal(containTabFocus(container, { shiftKey: true, activeElement: outside }), true)
  assert.equal(last.focusCount, 1)
})

test('containTabFocus keeps an all-disabled dialog on its container', () => {
  const disabled = fakeEl('disabled'); disabled.disabled = true
  const container = fakeContainer([disabled])
  container.focusCount = 0
  container.focus = function () { this.focusCount += 1 }
  assert.equal(containTabFocus(container, { activeElement: null }), true)
  assert.equal(container.focusCount, 1)
  // and a container that cannot even take focus reports "not handled"
  assert.equal(containTabFocus(fakeContainer([disabled]), { activeElement: null }), false)
})

test('applyBackgroundInert marks every sibling on the path, and restore is exact', () => {
  const script = fakeEl('script')
  const header = fakeEl('header')
  const otherDialog = fakeEl('otherDialog', { inert: '' }) // pre-existing inert
  const backdrop = fakeEl('backdrop')
  const modal = fakeEl('modal')
  const root = fakeEl('root'); root.children = [header, otherDialog]
  const body = fakeEl('body'); body.children = [script, root]
  const d = { body, documentElement: fakeEl('html') }
  backdrop.children = [modal]
  modal.parentElement = backdrop
  backdrop.parentElement = root
  header.parentElement = root
  otherDialog.parentElement = root
  root.parentElement = body
  body.parentElement = d.documentElement

  const restore = applyBackgroundInert(modal, d)
  assert.equal(script.hasAttribute('inert'), true, 'body sibling inert')
  assert.equal(header.hasAttribute('inert'), true, 'ancestor sibling inert')
  assert.equal(otherDialog.hasAttribute('inert'), true, 'pre-existing inert untouched')
  // the modal and its own path are NEVER inert — that is the whole point
  for (const el of [modal, backdrop, root]) {
    assert.equal(el.hasAttribute('inert'), false, `${el.id} must stay reachable`)
  }

  restore()
  assert.equal(script.hasAttribute('inert'), false, 'restored')
  assert.equal(header.hasAttribute('inert'), false, 'restored')
  assert.equal(otherDialog.hasAttribute('inert'), true, 'pre-existing inert preserved')
  restore() // one-shot: a double cleanup must not strip anything else
  assert.equal(script.hasAttribute('inert'), false)
})

test('applyBackgroundInert refcounts a shared sibling across two open dialogs', () => {
  const header = fakeEl('header')
  const backdropA = fakeEl('backdropA')
  const backdropB = fakeEl('backdropB')
  const modalA = fakeEl('modalA')
  const modalB = fakeEl('modalB')
  const root = fakeEl('root'); root.children = [header, backdropA, backdropB]
  const body = fakeEl('body'); body.children = [root]
  const d = { body, documentElement: fakeEl('html') }
  backdropA.children = [modalA]; modalA.parentElement = backdropA
  backdropB.children = [modalB]; modalB.parentElement = backdropB
  backdropA.parentElement = root; backdropB.parentElement = root
  header.parentElement = root; root.parentElement = body; body.parentElement = d.documentElement

  const restoreA = applyBackgroundInert(modalA, d)
  const restoreB = applyBackgroundInert(modalB, d)
  assert.equal(header.hasAttribute('inert'), true)
  restoreA()
  assert.equal(header.hasAttribute('inert'), true, 'B still needs the background inert')
  restoreB()
  assert.equal(header.hasAttribute('inert'), false, 'last owner restores it')
  // a detached node / no document is a safe no-op, not a throw
  assert.equal(typeof applyBackgroundInert(null, d), 'function')
  assert.equal(typeof applyBackgroundInert(modalA, null), 'function')
})
