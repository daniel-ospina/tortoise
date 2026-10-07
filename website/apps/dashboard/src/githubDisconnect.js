// #4946 item 3 — the GitHub disconnect control and its consequence copy.
//
// WHY THIS MODULE EXISTS. #5598 landed the server endpoint
// (`POST /v1/onboarding/github/disconnect`: revoke at GitHub → clear locally →
// `github_connected: false`) but no client ever calls it — `grep` finds zero
// callers — so "there is no UI to disconnect" (the issue's title) stayed true
// after the server half shipped. This is that missing control.
//
// It is a `.js` module written with `React.createElement` (no JSX) for the SAME
// reason as `accountDeletion.js` / `overviewEmptyAction.js`: the dashboard suite
// is `node --test` with no JSX transform and no jsdom, so a `.jsx` component
// cannot be imported. A createElement module CAN be, and the guard
// EXECUTE-renders it (`react-dom/server`) and reads the consequence copy off the
// element tree React produces — a source-text grep cannot be satisfied by a
// commented-out or shadowed string (#4637: five review cycles of text pins each
// passed while the rendered card said something else).
//
// The consequence copy is a NAMED EXPORT the guard binds verbatim. The whole
// point of #4946 is that a disconnect which does not state its consequence — a
// new GitHub authorization to reconnect — is the cosmetic behaviour #1924
// removed, so this string, not the button, is the safeguard.
import React from 'react'

export const GITHUB_DISCONNECT_CONSEQUENCE =
  "Disconnecting revokes Tortoise's access to GitHub and clears the stored token. "
  + 'Issues and docs you have already indexed stay in your graph. '
  + 'Re-connecting requires authorizing GitHub again.'

export const GITHUB_DISCONNECT_OPENER = 'Disconnect GitHub'
// Distinct from the opener so a test can address the two controls unambiguously.
export const GITHUB_DISCONNECT_CONFIRM = 'Disconnect'
export const GITHUB_DISCONNECT_BUSY = 'Disconnecting…'

// The endpoint's own `revoke_reason` vocabulary (`_revoke_github_token` +
// `github_disconnect` in tortoise/hosted_api.py), each phrased as the reason the
// revocation was NOT confirmed. Kept here rather than inline so a new reason
// added server-side degrades to the honest generic arm below instead of
// silently rendering as undefined.
const REASON_TEXT = {
  not_connected: 'Tortoise had no stored GitHub token, so there was nothing to revoke',
  undecryptable: 'the stored token could not be read, so GitHub could not be asked to revoke it',
  not_configured: 'this server has no GitHub app credentials, so GitHub could not be asked to revoke the token',
  network: 'GitHub could not be reached to confirm the revocation',
}

/**
 * The reason a revocation was not confirmed, in user-facing words. An unknown or
 * absent reason is named as such — it is never dropped, and never implied gone.
 */
export function disconnectReasonText(reason) {
  if (!reason) return 'the endpoint reported no reason'
  if (Object.hasOwn(REASON_TEXT, reason)) return REASON_TEXT[reason]
  const http = /^http_(\d+)$/.exec(reason)
  if (http) {
    const code = Number(http[1])
    // Match the HTTP range to what GitHub actually established. 404/422 mean it
    // did not recognise the token (it may be gone); 401/403 mean it rejected
    // the REQUEST before touching the token, so the token is certainly still
    // live; a 5xx means it could not process the call. Saying "may already be
    // gone" for any of the latter two is the wrong reassurance — the exact
    // failure #4946 exists to remove.
    if (code === 404 || code === 422) {
      return `GitHub answered ${code} to the revocation request, so the token may already be gone`
    }
    if (code === 401 || code === 403) {
      return `GitHub rejected the revocation request (it answered ${code}), so the token is still live — this server's GitHub app credentials may be wrong`
    }
    if (code >= 500) {
      return `GitHub could not process the revocation (it answered ${code}), so the token is probably still live`
    }
    return `GitHub answered ${code} to the revocation request, so whether the token is still live is unknown`
  }
  return `the endpoint reported "${reason}"`
}

/**
 * The honest post-disconnect outcome (#4946: revoke FIRST, then clear). A
 * disconnect is reported as clean ONLY when the server says no live token
 * remains (`revoked`); every other outcome says so and points at GitHub's own
 * application settings. Reporting an unconfirmed revocation as a clean
 * disconnect is exactly the cosmetic lie this issue exists to remove.
 */
export function disconnectOutcomeMessage(result) {
  const r = result || {}
  // The endpoint's idempotent no-op: it reports `revoked: true` when there was
  // NO stored token to revoke (`revoke_reason: 'not_connected'`). Nothing was
  // revoked at GitHub — claiming otherwise is the cosmetic lie #4946 exists to
  // remove, so this arm must come first.
  if (r.revoked && r.revoke_reason === 'not_connected') {
    return 'GitHub is disconnected. There was no stored token to revoke, so nothing was sent to GitHub.'
  }
  if (r.revoked) {
    return "GitHub is disconnected — Tortoise's access is revoked and the stored token is cleared."
  }
  return 'GitHub is disconnected here, but the revocation was not confirmed: '
    + disconnectReasonText(r.revoke_reason)
    + '. The stored token is cleared; remove Tortoise under GitHub → Settings → Applications to be sure.'
}

// #4029 (a11y, #2392 pattern): the confirm disables BOTH dialog buttons while it
// is in flight and the browser answers by dropping focus to <body>. Move it to
// the dialog container (tabIndex -1) as the confirm gesture starts, so Escape
// still reaches the dialog's own handler and a screen reader never leaves the
// modal.
function focusDialogContainer(e) {
  const node = e && e.currentTarget && e.currentTarget.closest
    ? e.currentTarget.closest('[role="dialog"]')
    : null
  if (node && typeof node.focus === 'function') node.focus()
}

/**
 * The opener + its confirm dialog. `result` (the endpoint's response body)
 * switches the dialog to the outcome panel; `error` keeps it open on a failed
 * request so the retry is armed.
 */
export function GithubDisconnectControl({ connected, open, busy, error, result, onOpen, onClose, onConfirm }) {
  const h = React.createElement
  // A trailing `display:flex` row rather than `.new-key-actions`, which
  // `index.css` scopes to the destructive `.graph-delete-modal` / wide
  // `.key-create-modal`: this dialog is deliberately NEITHER (disconnecting
  // clears no indexed data), so it wears a bare `.modal` and lays out its own
  // two actions.
  // `...children` (not the rest ARRAY) so React does not warn about unkeyed list
  // children on every dialog render.
  const actions = (...children) =>
    h('div', { style: { display: 'flex', gap: 8, marginTop: '0.8rem' } }, ...children)
  const content = result
    ? h(
      React.Fragment,
      null,
      h('h2', null, 'GitHub disconnected'),
      h('p', { className: 'dim small' }, disconnectOutcomeMessage(result)),
      actions(h('button', {
        type: 'button', className: 'btn-primary', autoFocus: true, onClick: () => onClose(),
      }, 'Close')),
    )
    : h(
      React.Fragment,
      null,
      h('h2', null, 'Disconnect GitHub?'),
      h('p', { className: 'dim small' }, GITHUB_DISCONNECT_CONSEQUENCE),
      error ? h('p', { className: 'error', role: 'alert' }, error) : null,
      // #4029 (a11y): the busy transition needs a polite announcement — the
      // confirm button's label swap alone is not reliably read out.
      h('span', { className: 'sr-only', role: 'status', 'aria-live': 'polite' },
        busy ? GITHUB_DISCONNECT_BUSY : ''),
      actions(
        // Focus moves INTO the dialog on open, and onto Cancel — not the
        // affirmative CTA — so a stray Enter cannot disconnect.
        h('button', {
          type: 'button', className: 'ghost', autoFocus: true, disabled: busy,
          onClick: () => { if (!busy) onClose() },
        }, 'Cancel'),
        h('button', {
          type: 'button', className: 'btn-primary', disabled: busy,
          onClick: (e) => { if (busy) return; focusDialogContainer(e); onConfirm() },
        }, busy ? GITHUB_DISCONNECT_BUSY : GITHUB_DISCONNECT_CONFIRM),
      ),
    )
  return h(
    React.Fragment,
    null,
    // The opener is gated on `connected`, but the DIALOG is not: a successful
    // disconnect flips `github_connected` false, and a dialog nested inside the
    // connected branch would unmount before the user could read the outcome
    // panel (the honest "revocation was not confirmed" warning in particular).
    // The caller renders this control OUTSIDE the connected branch for exactly
    // that reason.
    connected
      ? h('button', {
        type: 'button', className: 'ghost', onClick: () => onOpen(), disabled: busy,
      }, GITHUB_DISCONNECT_OPENER)
      : null,
    open
      ? h(
        'div',
        { className: 'modal-backdrop', onClick: () => { if (!busy) onClose() } },
        h(
          'div',
          {
            id: 'github-disconnect-dialog',
            className: 'modal',
            role: 'dialog',
            'aria-modal': 'true',
            'aria-label': 'Disconnect GitHub',
            tabIndex: -1,
            onClick: (e) => e.stopPropagation(),
            onKeyDown: (e) => { if (e.key === 'Escape' && !busy) onClose() },
          },
          content,
        ),
      )
      : null,
  )
}
