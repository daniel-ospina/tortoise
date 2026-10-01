// #4029 — the personal-account delete control + its confirm popup.
//
// Extracted from `profile.jsx` into a `.js` module written with
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

export function DeleteAccountSection({
  open, busy, error, onOpen, onCancel, onConfirm,
}) {
  const h = React.createElement
  return h(
    'section',
    { className: 'profile-tab danger-zone', 'aria-labelledby': 'delete-account-heading' },
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
            className: 'modal',
            role: 'dialog',
            'aria-modal': 'true',
            'aria-label': 'Delete account',
            onClick: (e) => e.stopPropagation(),
            onKeyDown: (e) => { if (e.key === 'Escape' && !busy) onCancel() },
          },
          h('h3', null, 'Delete your personal account?'),
          h('p', { className: 'danger-note' }, DELETE_ACCOUNT_WARNING),
          h('p', { className: 'dim small' },
            'Confirming removes your account now. Within the recovery window it can '
            + 'be recovered by contacting support; after that it is permanently '
            + 'erased. Cancel now and nothing changes.'),
          error ? h('p', { className: 'error', role: 'alert' }, error) : null,
          h(
            'div',
            { className: 'new-key-actions', style: { marginTop: '0.8rem' } },
            h('button', { type: 'button', className: 'ghost', disabled: busy, onClick: onCancel },
              'Cancel'),
            h('button', { type: 'button', className: 'danger', disabled: busy, onClick: onConfirm },
              busy ? 'Deleting…' : 'Delete my account'),
          ),
        ),
      )
      : null,
  )
}
