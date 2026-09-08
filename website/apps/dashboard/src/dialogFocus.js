// dialogFocus.js — #2392: minimal a11y focus management for the dashboard's
// dialog family.
//
// The create-team / reveal-key / reauth dialogs declare aria-modal=true but
// (pre-#2392) moved no focus on open and dropped focus to <body> on close;
// the account menu had the same drop on Escape/outside close. Full inert
// focus-trapping is the heavier follow-up — this module implements only the
// issue's minimal set: the caller moves focus INTO the dialog on open
// (autoFocus on the primary control, or an imperative focus at the call
// site) and restores focus to the opening trigger on close via restoreFocus().
//
// Pure-enough for `node --test`: the only DOM reads are optional and guarded
// (element.isConnected, document.activeElement), so the tests exercise the
// logic with plain-object fakes (dialogFocus.test.js).

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
