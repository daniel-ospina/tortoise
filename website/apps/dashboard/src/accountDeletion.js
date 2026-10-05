// #4029 — the personal-account delete control + its confirm popup.
//
// The control is rendered from `main.jsx`'s Profile tab (an earlier header
// claimed `profile.jsx`, which carries no account-deletion code and is not
// touched by this change). It is a `.js` module written with
// `React.createElement` (no JSX) for the SAME reason as `overviewEmptyAction.js`:
// the dashboard suite is `node --test` with no JSX transform and no jsdom, so a
// `.jsx` component cannot be imported and rendered by the guard. Here the guard
// is an EXECUTED render (`react-dom/server`) of the real component — the copy
// and the two actions are read off the element tree React produces, not grepped
// out of source text.
//
// The warning copy is the OWNER-RULED string (decision 1B on #4029) and must
// appear verbatim. It is the whole safeguard for the fact that a solely-owned
// team is deleted with the account, so it is a named export the test binds to.
// Two actions only: a confirm CTA, and a cancel that is a real exit — the
// parent closes the popup with no request and no state change.
import React from 'react'

export const DELETE_ACCOUNT_WARNING =
  "Deleting your personal account will also delete any teams for which you're the only owner"

// The dashboard's support channel, named explicitly (and as a `mailto:` link,
// matching the rest of the dashboard) so the recovery promise is actionable
// where it is made — "support" alone names no surface the user can reach.
const SUPPORT_EMAIL = 'hello@premiselabs.co'

// The confirm popup is destructive, so it wears the dashboard's EXISTING
// destructive-dialog class. `index.css` scopes the red warning
// (`.graph-delete-modal .danger-note`) and the red confirm CTA
// (`.graph-delete-modal button.danger`) to it; a bare `.modal` gets neither,
// which rendered the owner-ruled warning as ordinary text and both destructive
// buttons as default buttons.
const DIALOG_CLASS = 'modal graph-delete-modal'

// #4029 (a11y, #2392 pattern): the DELETE disables BOTH dialog buttons while it
// is in flight and the browser answers by dropping focus to <body>. Move it to
// the dialog container (tabIndex -1) as the confirm gesture starts, so Escape
// still reaches the dialog's own handler and a screen reader never leaves the
// modal. `main.jsx` reclaims focus for the same reason if it lands on <body>
// anyway.
function focusDialogContainer(e) {
  const node = e && e.currentTarget && e.currentTarget.closest
    ? e.currentTarget.closest('[role="dialog"]')
    : null
  if (node && typeof node.focus === 'function') node.focus()
}

/** `hard_delete_after` (ISO-8601) as a readable local date, raw if unparsable. */
function hardDeleteLabel(iso) {
  if (!iso) return 'the end of the recovery window'
  const t = Date.parse(iso)
  return Number.isFinite(t) ? new Date(t).toLocaleString() : String(iso)
}

export function DeleteAccountSection({
  open, busy, error, result, onOpen, onCancel, onConfirm, onDone,
}) {
  const h = React.createElement
  const support = h('a', { href: `mailto:${SUPPORT_EMAIL}` }, SUPPORT_EMAIL)
  const teams = result && Array.isArray(result.teams_deleted) ? result.teams_deleted : []
  const content = result
    // #4029 item 5: confirm WHAT WAS REMOVED (and what remains) to the user
    // before the session ends. Post-logout is too late — the user is signed out
    // and this section is gone.
    ? h(
      React.Fragment,
      null,
      h('h3', null, 'Deletion scheduled'),
      h('p', { className: 'dim small' },
        teams.length
          ? 'Your account is scheduled for deletion, and these teams were deleted with it:'
          : 'Your account is scheduled for deletion. You were not the only owner of any team, '
            + 'so no team was deleted.'),
      teams.length
        ? h('ul', { className: 'dim small' },
          teams.map((id) => h('li', { key: String(id) }, h('code', null, String(id)))))
        : null,
      h('p', { className: 'dim small' },
        'Your account and its data are erased after ',
        hardDeleteLabel(result.hard_delete_after),
        '. Until then the deletion can be reversed by emailing our support team at ',
        support,
        '.'),
      result.note ? h('p', { className: 'dim small' }, result.note) : null,
      h('div', { className: 'new-key-actions', style: { marginTop: '0.8rem' } },
        h('button', { type: 'button', className: 'btn-primary', autoFocus: true, onClick: onDone },
          'Sign out')),
    )
    : h(
      React.Fragment,
      null,
      h('h3', null, 'Delete your personal account?'),
      h('p', { className: 'danger-note' }, DELETE_ACCOUNT_WARNING),
      h('p', { className: 'dim small' },
        // #4029: this says what the endpoint actually does, in two steps — it
        // deletes the solely-owned teams NOW and erases the account at the end
        // of the window. It must not claim the ACCOUNT is gone the moment it is
        // confirmed: nothing gates sign-in for a delete-pending account, so the
        // account is still reachable during the recovery window (see the
        // recorded gap in docs/retention-and-deletion.md).
        'Confirming deletes every team you solely own right now, and revokes '
        + 'their API keys. Your account and its data are erased at the end of the '
        + 'recovery window; until then the deletion can be reversed by emailing our '
        + 'support team at ',
        support,
        '; after that it is permanent. Cancel now and nothing changes.'),
      error ? h('p', { className: 'error', role: 'alert' }, error) : null,
      // #4029 (a11y): the busy transition needs a polite announcement — the
      // confirm button's label swap alone is not reliably read out.
      h('span', { className: 'sr-only', role: 'status', 'aria-live': 'polite' },
        busy ? 'Deleting…' : ''),
      h(
        'div',
        { className: 'new-key-actions', style: { marginTop: '0.8rem' } },
        // #2392: focus moves INTO the dialog on open. Cancel (not the
        // destructive CTA) is the autofocused control, so a stray Enter cannot
        // delete the account.
        h('button', {
          type: 'button', className: 'ghost', autoFocus: true, disabled: busy,
          onClick: () => { if (!busy) onCancel() },
        }, 'Cancel'),
        h('button', {
          type: 'button', className: 'danger', disabled: busy,
          onClick: (e) => { if (busy) return; focusDialogContainer(e); onConfirm() },
        }, busy ? 'Deleting…' : 'Delete my account'),
      ),
    )
  return h(
    'section',
    { className: 'profile-tab', 'aria-labelledby': 'delete-account-heading' },
    h('h3', { id: 'delete-account-heading' }, 'Delete account'),
    h('p', { className: 'dim small' },
      'Permanently delete your personal account and everything you own in Tortoise.'),
    h('button', { type: 'button', className: 'danger', onClick: onOpen, disabled: busy },
      'Delete account'),
    open
      ? h(
        'div',
        { className: 'modal-backdrop', onClick: () => { if (!busy) onCancel() } },
        h(
          'div',
          {
            id: 'delete-account-dialog',
            className: DIALOG_CLASS,
            role: 'dialog',
            'aria-modal': 'true',
            'aria-label': 'Delete account',
            tabIndex: -1,
            onClick: (e) => e.stopPropagation(),
            onKeyDown: (e) => { if (e.key === 'Escape' && !busy) onCancel() },
          },
          content,
        ),
      )
      : null,
  )
}
