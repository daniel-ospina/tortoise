// dialogFocus.test.js — run with node --test (zero deps: the #2392 focus-
// restore helpers only read element.isConnected / document.activeElement,
// both optional and injected, so tests use plain-object fakes — no jsdom).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { rememberRestoreTarget, rememberFocusedTrigger, restoreFocus } from './dialogFocus.js'

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
