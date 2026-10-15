// dialogFocus.js — #2392: minimal a11y focus management for the dashboard's
// dialog family.
//
// The create-team / reveal-key / reauth dialogs declare aria-modal=true but
// (pre-#2392) moved no focus on open and dropped focus to <body> on close;
// the account menu had the same drop on Escape/outside close.
//
// TWO LAYERS, both here (the #2577 remainder of the APG dialog-modal pattern):
//
//   1. The #2392 minimal set — the caller moves focus INTO the dialog on open
//      (autoFocus on the primary control, or an imperative focus at the call
//      site) and restores it to the opening trigger on close via restoreFocus().
//   2. The APG containment set — while a dialog is open the tab sequence must
//      not leave it (containTabFocus) and the background must be inert, so a
//      screen reader's virtual cursor cannot reach it either (applyBackgroundInert,
//      the `inert` attribute — the APG's own recommendation where the native
//      `<dialog>` element's top-layer/showModal() is not used).
//
// Pure-enough for `node --test`: the only DOM reads are optional and guarded
// (element.isConnected, document.activeElement, hasAttribute/parentElement), so
// the tests exercise the logic with plain-object fakes (dialogFocus.test.js).
// The helpers take their document/element inputs explicitly and never touch a
// global at import time.

// Save the element focus should return to when a surface closes. Used with an
// explicit element when the restore target is NOT the one that currently owns
// focus — the '+ Create new organization' item lives inside the account menu,
// which unmounts under the create-team dialog, so the always-mounted blob
// trigger button is the real anchor (see main.jsx account menu).
export function rememberRestoreTarget(holderRef, el) {
  holderRef.current = (el && typeof el.focus === 'function') ? el : null
}

// Capture whatever control currently owns focus as the restore target
// (document.activeElement) — but never the <body>. When the opening action
// disables its own trigger mid-flight the browser drops focus to <body>;
// restoring to <body> would recreate the exact bug this module removes. The
// call site passes its own `doc` in tests; browsers default to the global.
export function rememberFocusedTrigger(holderRef, doc) {
  const d = doc || (typeof document !== 'undefined' ? document : null)
  if (!d) { holderRef.current = null; return }
  const el = d.activeElement
  holderRef.current = (el && el !== d.body && typeof el.focus === 'function') ? el : null
}

// Best-effort focus restore on close. One-shot: consumes the holder so a
// later restore (e.g. a double close path) is a no-op. Returns the restored
// element, or null when nothing was captured, the element left the DOM while
// the surface was open, the element already owns focus, or it is not
// focusable.
export function restoreFocus(holderRef, activeElementOverride) {
  const el = holderRef ? holderRef.current : null
  if (holderRef) holderRef.current = null
  if (!el || typeof el.focus !== 'function') return null
  // Detached-node guard: a trigger can unmount while the surface is open
  // (account-menu items under the create-team dialog, an unlinked identity
  // row under ReauthDialog). Focusing a detached node is a silent no-op
  // anyway, but the guard keeps the decision explicit.
  if (typeof el.isConnected === 'boolean' && !el.isConnected) return null
  const active = activeElementOverride !== undefined
    ? activeElementOverride
    : (typeof document !== 'undefined' ? document.activeElement : null)
  if (active && active === el) return el // focus already on the trigger
  el.focus()
  return el
}

// ── APG containment (#2577) ──────────────────────────────────────────────
//
// The APG dialog-modal pattern: "Modal dialogs contain their tab sequence" and
// "Windows under a modal dialog are inert". With a custom (non-`<dialog>`)
// modal these are two separate obligations, and BOTH are needed: a JS Tab wrap
// alone still leaves the background in the accessibility tree, so a screen
// reader's virtual cursor reaches content behind the dialog (TPGi). `inert`
// removes a subtree from the a11y tree *and* the tab order, which is the half a
// keydown handler cannot do.

// Elements the dialog's tab sequence can reach. `disabled`/`tabindex="-1"`
// controls and anything inside a `[hidden]`/`[inert]` subtree are filtered at
// match time (isFocusableElement) — a pure CSS selector cannot express the
// ancestor conditions, and a background made inert must not re-enter the scan.
export const FOCUSABLE_SELECTOR = [
  'a[href]', 'area[href]', 'button', 'input', 'select', 'textarea',
  'iframe', 'object', 'embed', 'audio[controls]', 'video[controls]',
  '[contenteditable]', '[tabindex]',
].join(',')

// True when el, or any ancestor up to (but excluding) root, is hidden or inert.
function inHiddenSubtree(el, root) {
  let node = el
  while (node && node !== root) {
    if (typeof node.hasAttribute === 'function'
      && (node.hasAttribute('hidden') || node.hasAttribute('inert'))) return true
    node = node.parentElement
  }
  return false
}

function isFocusableElement(el, root) {
  if (!el || typeof el.focus !== 'function') return false
  if (el.disabled === true) return false
  if (typeof el.hasAttribute !== 'function') return false
  if (el.hasAttribute('disabled')) return false
  const ti = typeof el.getAttribute === 'function' ? el.getAttribute('tabindex') : null
  if (ti !== null && ti !== undefined && ti !== '') {
    const n = Number(ti)
    if (!Number.isFinite(n) || n < 0) return false
  }
  if ((el.getAttribute('type') || '').toLowerCase() === 'hidden') return false
  return !inHiddenSubtree(el, root)
}

// The tabbable descendants of container, in document order. Returns [] when
// container has no querySelectorAll (a plain-object fake without one, or a
// detached node) — containment then falls back to focusing the container.
export function focusableWithin(container) {
  if (!container || typeof container.querySelectorAll !== 'function') return []
  const out = []
  for (const el of container.querySelectorAll(FOCUSABLE_SELECTOR)) {
    if (isFocusableElement(el, container)) out.push(el)
  }
  return out
}

// Enforce the APG Tab/Shift+Tab wrap for one keydown. Returns true when focus
// was moved (the caller MUST call preventDefault() so the browser's default
// move — out of the dialog — does not also run). A no-focusable dialog is kept
// on its container (tabIndex -1, e.g. a busy state with every control
// disabled), which is still inside the dialog; returns false only when even
// that is impossible.
//
// `activeElement` is injectable so the wrap is testable without a live focus
// model; browsers default to container.ownerDocument.activeElement.
export function containTabFocus(container, opts) {
  const shiftKey = !!(opts && opts.shiftKey)
  const active = opts && 'activeElement' in opts
    ? opts.activeElement
    : (container && container.ownerDocument ? container.ownerDocument.activeElement : null)
  const items = focusableWithin(container)
  if (!items.length) {
    if (container && typeof container.focus === 'function') { container.focus(); return true }
    return false
  }
  const first = items[0]
  const last = items[items.length - 1]
  // Focus is on the container itself, on a background element, or nowhere:
  // pull it to the near end of the sequence rather than letting Tab leave.
  if (!active || !items.includes(active)) {
    ;(shiftKey ? last : first).focus()
    return true
  }
  if (!shiftKey && active === last) { first.focus(); return true }
  if (shiftKey && active === first) { last.focus(); return true }
  return false
}

// Elements this call marked — refcounted, so two dialogs open at once (or a
// re-render whose cleanup has not run yet) cannot un-inert a subtree the other
// still needs inert, and a pre-existing `inert` the page already carried is
// never removed on restore.
const inertOwners = new Map()

// Make everything outside `modalNode` inert by marking the siblings of each
// ancestor on the path up to <body> — the same isolation strategy as the
// `aria-hidden` library's hideOthers/inertOthers and react-modal's setAppElement,
// and the reason no portal is needed: the dialog stays where React rendered it.
//
// Returns a restore() for the effect's cleanup. It is one-shot and removes ONLY
// the markers this call added (`inert` a sibling already carried is preserved).
export function applyBackgroundInert(modalNode, doc) {
  const d = doc || (typeof document !== 'undefined' ? document : null)
  const touched = []
  const restore = () => {
    if (!touched.length) return
    for (const el of touched) {
      const rec = inertOwners.get(el)
      if (!rec) continue
      rec.count -= 1
      if (rec.count > 0) continue
      inertOwners.delete(el)
      if (!rec.preExisting && typeof el.removeAttribute === 'function') el.removeAttribute('inert')
    }
    touched.length = 0
  }
  if (!d || !modalNode) return restore
  const mark = (el) => {
    if (!el) return
    if (typeof el.setAttribute !== 'function') return
    const existing = inertOwners.get(el)
    if (existing) { existing.count += 1; touched.push(el); return }
    const preExisting = typeof el.hasAttribute === 'function' && el.hasAttribute('inert')
    if (!preExisting) el.setAttribute('inert', '')
    inertOwners.set(el, { count: 1, preExisting })
    touched.push(el)
  }
  let node = modalNode
  let parent = modalNode.parentElement
  while (parent) {
    const siblings = parent.children
    if (siblings) for (const sib of siblings) { if (sib !== node) mark(sib) }
    if (parent === d.body || parent === d.documentElement) break
    node = parent
    parent = parent.parentElement
  }
  return restore
}
