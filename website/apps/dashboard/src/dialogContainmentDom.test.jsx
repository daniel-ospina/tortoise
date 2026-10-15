// dialogContainmentDom.test.jsx — #2577: the containment helpers against a
// REAL jsdom document, not plain-object fakes.
//
// WHY THIS FILE EXISTS ALONGSIDE dialogFocus.test.js. The node --test suite
// proves the helper LOGIC with fakes (it cannot see `querySelectorAll`, the
// `inert` attribute reflection, or a live focus model). This lane renders the
// real DOM shapes those helpers were written for, so the two halves the APG
// modal pattern needs are each observed end to end:
//
//   1. Tab containment — containTabFocus wraps at the boundary and pulls focus
//      back in when it sits outside (document.activeElement is asserted, not a
//      call counter).
//   2. Background inertness — applyBackgroundInert marks the ancestors'
//      siblings and restore() removes exactly its own markers.
//
// jsdom does NOT implement `inert` as a PROPERTY (verified: `'inert' in
// HTMLElement.prototype` is false) and does not implement
// HTMLDialogElement.showModal — which is why the module writes the attribute
// and why the native-<dialog> route is untestable here. The attribute is the
// contract this lane can see, and it is the one browsers act on.
import { test, expect, afterEach } from 'vitest'
import { focusableWithin, containTabFocus, applyBackgroundInert } from './dialogFocus.js'

const mounted = []
afterEach(() => {
  for (const el of mounted.splice(0)) el.remove()
})

function mount(...nodes) {
  const root = document.createElement('div')
  for (const n of nodes) root.appendChild(n)
  document.body.appendChild(root)
  mounted.push(root)
  return root
}

test('applyBackgroundInert marks the background and restore removes only its own markers', () => {
  const background = document.createElement('section')
  const bgButton = document.createElement('button')
  bgButton.textContent = 'background'
  background.appendChild(bgButton)
  const alreadyInert = document.createElement('div')
  alreadyInert.setAttribute('inert', '')

  const backdrop = document.createElement('div')
  const modal = document.createElement('div')
  modal.setAttribute('role', 'dialog')
  modal.setAttribute('aria-modal', 'true')
  const first = document.createElement('button')
  const last = document.createElement('button')
  modal.append(first, last)
  backdrop.appendChild(modal)

  mount(background, alreadyInert, backdrop)

  const restore = applyBackgroundInert(modal, document)
  expect(background.hasAttribute('inert')).toBe(true)
  expect(alreadyInert.hasAttribute('inert')).toBe(true)
  // everything on the dialog's own path stays reachable — that is the point
  expect(modal.hasAttribute('inert')).toBe(false)
  expect(backdrop.hasAttribute('inert')).toBe(false)

  restore()
  expect(background.hasAttribute('inert')).toBe(false)
  expect(alreadyInert.hasAttribute('inert')).toBe(true) // pre-existing, preserved
})

test('containTabFocus wraps Tab/Shift+Tab on the real document.activeElement', () => {
  const first = document.createElement('button')
  const last = document.createElement('button')
  const disabled = document.createElement('button')
  disabled.disabled = true
  const modal = document.createElement('div')
  modal.append(first, last, disabled)
  mount(modal)

  // Tab from the last focusable → the first (the disabled control is skipped)
  last.focus()
  expect(containTabFocus(modal, { shiftKey: false })).toBe(true)
  expect(document.activeElement).toBe(first)

  // Shift+Tab from the first → the last
  expect(containTabFocus(modal, { shiftKey: true })).toBe(true)
  expect(document.activeElement).toBe(last)

  // a mid-sequence Tab is left to the browser (the handler must not preventDefault)
  first.focus()
  expect(containTabFocus(modal, { shiftKey: false })).toBe(false)
})

test('containTabFocus lands on the container when every control is disabled (busy state)', () => {
  const busy = document.createElement('button')
  busy.disabled = true
  const modal = document.createElement('div')
  modal.setAttribute('tabindex', '-1')
  modal.setAttribute('role', 'dialog')
  modal.appendChild(busy)
  mount(modal)

  // focus dropped to <body> when the control was disabled: the container must
  // be able to hold it, so Tab cannot leave the dialog — the reason the three
  // dialogs carry tabIndex={-1}, exactly like the #4029 disconnect dialogs.
  expect(document.activeElement).not.toBe(modal)
  expect(containTabFocus(modal, { shiftKey: false })).toBe(true)
  expect(document.activeElement).toBe(modal)
  expect(containTabFocus(modal, { shiftKey: true })).toBe(true)
  expect(document.activeElement).toBe(modal)
})

test('focusableWithin skips disabled controls and anything inside an inert subtree', () => {
  const modal = document.createElement('div')
  const ok = document.createElement('button')
  const disabled = document.createElement('button')
  disabled.disabled = true
  const negative = document.createElement('button')
  negative.setAttribute('tabindex', '-1')
  const inertWrap = document.createElement('div')
  inertWrap.setAttribute('inert', '')
  const insideInert = document.createElement('button')
  inertWrap.appendChild(insideInert)
  modal.append(ok, disabled, negative, inertWrap)
  mount(modal)

  expect(focusableWithin(modal)).toEqual([ok])
})
