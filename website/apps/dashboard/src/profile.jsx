// #1765: Profile tab + RecoveryBanner + shared add-method UI.
// Presentational — all state/handlers come from main.jsx (the router-less
// app shell). ExtractAddLoginMethodButtons mirrors the claim-card / Protect-
// screen markup (the third copy — now shared).
import React from 'react'
import { bannerCopy, unlinkAllowed } from './identity.js'

// ── RecoveryBanner ─────────────────────────────────────────────────────────
// Persistent security notice — .recovery-banner (protect-banner family),
// NOT the transient green .banner div. role="region" + keyboard CTA.
// Dismissal: no-dismiss when actionable (single confirmed method, linking
// on); TTL-dismiss when NOT actionable (unconfirmed email / linking off).
export function RecoveryBanner({ inv, onCta, dismissed, onDismiss, onResend, resendBusy }) {
  if (!inv) return null
  const actionable = !!inv.linking_available && !!inv.email_confirmed_at
  const showResend = !inv.email_confirmed_at
  return (
    <div className="recovery-banner" role="region" aria-label="Account recovery">
      <div className="recovery-banner-copy">{bannerCopy(inv)}</div>
      <div className="recovery-banner-actions">
        <button type="button" className="btn-submit" onClick={onCta}>
          Add a login method
        </button>
        {showResend && (
          <button type="button" className="ghost small" onClick={onResend} disabled={resendBusy}>
            {resendBusy ? 'Sending…' : 'Resend confirmation email'}
          </button>
        )}
        {!actionable && (
          <button type="button" className="ghost small" onClick={onDismiss} aria-label="Dismiss">
            ✕
          </button>
        )}
      </div>
    </div>
  )
}

// ── AddLoginMethodButtons ──────────────────────────────────────────────────
// The claim/protect "attach a login" family (3 buttons + collapsible
// email+password form + busy/error states). handlers as props:
//   onOAuth(provider), onEmail(email, password)
export function AddLoginMethodButtons({ busy, onOAuth, onEmail, error }) {
  const [showEmail, setShowEmail] = React.useState(false)
  const [email, setEmail] = React.useState('')
  const [password, setPassword] = React.useState('')
  return (
    <div className="claim-actions">
      <button onClick={() => onOAuth('github')} disabled={busy}>
        {busy ? 'Redirecting…' : 'Connect GitHub'}
      </button>
      <button onClick={() => onOAuth('google')} disabled={busy}>
        Connect Google login
      </button>
      <button className="ghost" onClick={() => setShowEmail(!showEmail)} disabled={busy}>
        Connect email and password
      </button>
      {showEmail && (
        <form
          className="inline-form claim-email-form"
          onSubmit={(e) => { e.preventDefault(); onEmail(email, password) }}
        >
          <input
            type="email" placeholder="you@example.com" aria-label="Email"
            value={email} onChange={(e) => setEmail(e.target.value)}
            autoComplete="email"
          />
          <input
            type="password" placeholder="Password (min 6 chars)" aria-label="Password"
            value={password} onChange={(e) => setPassword(e.target.value)}
            autoComplete="new-password" minLength={6}
          />
          <button type="submit" disabled={busy || !email.includes('@') || password.length < 6}>
            {busy ? 'Connecting…' : 'Connect email & password'}
          </button>
        </form>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </div>
  )
}

// ── ReauthDialog ───────────────────────────────────────────────────────────
// Re-auth gate UX (plan Task 4): password field → signInWithPassword;
// provider buttons → same-provider OAuth round. Covers the server's
// REAUTH_REQUIRED + the change-email + unlink gates.
export function ReauthDialog({ open, busy, onClose, onPassword, onProvider, error, providers, passwordMode }) {
  const [password, setPassword] = React.useState('')
  if (!open) return null
  const available = providers && providers.length ? providers : []
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" role="dialog" aria-modal="true" aria-label="Confirm it's you"
           onClick={(e) => e.stopPropagation()}>
        <h2>Confirm it's you</h2>
        <p className="dim">{passwordMode
          ? 'You re-authenticated — now choose your new password.'
          : 'For security, sign in again before changing login methods.'}</p>
        <form className="inline-form claim-email-form" onSubmit={(e) => { e.preventDefault(); onPassword(password) }}>
          <input
            type="password" placeholder={passwordMode ? 'New password' : 'Password'}
            aria-label={passwordMode ? 'New password' : 'Password'}
            value={password} onChange={(e) => setPassword(e.target.value)}
            autoComplete={passwordMode ? 'new-password' : 'current-password'}
          />
          <button type="submit" disabled={busy || password.length < 6}>
            {busy ? 'Saving…' : (passwordMode ? 'Set new password' : 'Confirm')}
          </button>
        </form>
        <div className="claim-actions">
          {/* #1765 review P1: SAME-provider only — a different provider with a
              private email would auto-link a NEW user (account split). */}
          {available.includes('github') && (
            <button onClick={() => onProvider('github')} disabled={busy}>Sign in with GitHub</button>
          )}
          {available.includes('google') && (
            <button onClick={() => onProvider('google')} disabled={busy}>Sign in with Google</button>
          )}
          {available.length === 0 && <p className="dim">Sign in again with your password above.</p>}
        </div>
        {error && <p className="error" role="alert">{error}</p>}
        <button className="ghost small" onClick={onClose} aria-label="Close">✕</button>
      </div>
    </div>
  )
}

// ── ProfileTab ─────────────────────────────────────────────────────────────
// Session-gated method list + API-key tier + add-method section. States per
// the membersStatus convention (status-classified, dim rows, retry).
export function ProfileTab({
  inv, loading, error, onRetry,
  onUnlink, unlinkBusy,
  onAddOAuth, onAddEmail, addBusy, addError,
  onResend, resendBusy,
  onOpenReauth,
}) {
  if (loading) return <p className="dim">Loading login methods…</p>
  if (error) {
    return (
      <div className="error-state">
        <p className="error">{error}</p>
        <button className="ghost small" onClick={onRetry}>Try again</button>
      </div>
    )
  }
  if (!inv) return null
  const methods = inv.methods || []
  const keysTier = inv.keys_tier || 0
  const removeDisabled = (n) => !unlinkAllowed(n) || unlinkBusy
  return (
    <section className="profile-tab">
      <h2>Login methods</h2>
      <p className="dim">
        Ways to sign in to your account. Add more than one so you can always get
        back in — API keys are for graph operations, not account recovery.
      </p>
      {methods.length === 0 && <p className="dim">No login methods yet.</p>}
      <table className="members-table">
        <thead><tr><th>Method</th><th>Status</th><th /></tr></thead>
        <tbody>
          {methods.map((m) => (
            <tr key={`${m.provider}:${m.provider_id}`}>
              <td>{m.provider === 'email' ? 'Email + password' : m.provider}</td>
              <td>
                {m.provider === 'email' ? 'Password sign-in' : (inv.email_confirmed_at ? 'Connected' : 'Email unconfirmed')}
              </td>
              <td>
                {m.provider !== 'email' && (
                  <button
                    type="button" className="ghost small"
                    disabled={removeDisabled(inv.login_methods)}
                    onClick={() => {
                      const keep = inv.login_methods - 1
                      if (window.confirm(
                        `Remove ${m.provider} login from your account? You'll still have ${keep} way(s) to sign in.`
                      )) {
                        onUnlink(m.id ?? m.provider_id)  // unlink by the IDENTITY ROW id
                      }
                    }}
                  >
                    Remove
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3>Add a login method</h3>
      {inv.linking_available ? (
        <AddLoginMethodButtons busy={addBusy} onOAuth={onAddOAuth} onEmail={onAddEmail} error={addError} />
      ) : (
        <p className="dim">Adding login methods is not enabled yet — contact hello@premiselabs.co.</p>
      )}

      <h3>Dashboard credentials</h3>
      <p className="dim">
        {keysTier} API key{keysTier === 1 ? '' : 's'} minted by your account — manage them on the
        {' '}<button className="ghost small" onClick={() => document.querySelector('[data-tab="keys"]')?.click()}>API Keys</button>{' '}
        tab.
      </p>

      {!inv.email_confirmed_at && (
        <button className="ghost small" onClick={onResend} disabled={resendBusy}>
          {resendBusy ? 'Sending…' : 'Resend confirmation email'}
        </button>
      )}

      <p className="dim small" style={{ marginTop: 16 }}>
        <button className="ghost small" onClick={onOpenReauth}>Re-authenticate now</button>
        {' '}— needed when your last sign-in is older than the security window.
      </p>
    </section>
  )
}
