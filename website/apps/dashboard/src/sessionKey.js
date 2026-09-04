// #1708 D8: session-key classification from server-provided API data,
// extracted pure so it's unit-testable with node --test (no harness).
// (k: key row, activeKey: the current session's plaintext key, or null)
// #2246 (ADR-010): the dashboard is session-only — the browser never holds an
// API key in session mode. The held-key machinery (isSessionKey, isActiveKey,
// classifyHeldKey, heldKeyClearState, nextRegenInstallState,
// probeClassifyStoredKey) was deleted with its only consumer (main.jsx); the
// anon/key-login carve-out (claim-paste/Protect flows) never imported them.
// This module now holds the surviving pure core: the durable-row table
// predicate (isManagedKey), the connect-step key classifier (durableConnectKey
// — welcome plaintext, paste validation, rows-routed gate), and the rows
// resolution helper (usableDurableRows) the session connect gate sources from.

// #2166: durable product keys are the ONLY rows the API Keys page shows.
// Auto-minted session credentials (created_via 'bootstrap', or any row with an
// expiry set) are the dashboard's own access keys — never presented as API
// keys for using the product. Durable = user/agent-created keys the user can
// name, toggle, revoke, and (later) scope or give a validity window. Disabled
// (enabled:false) durable rows stay managed — the toggle needs them visible.
export function isManagedKey(k) {
  if (!k) return false
  return !(k.created_via === 'bootstrap' || !!k.expires_at)
}

// #2246 (ADR-010): rows-source connect resolution. The API Keys table shows
// rows the browser can MANAGE but never HOLD — GET /v1/team/keys returns
// hashes only, so a "usable" durable row can never supply a plaintext key.
// This predicate picks the durable rows that could be re-used IF the user had
// their plaintext (durable ∧ ¬revoked ∧ ¬disabled; enabled absent → enabled,
// registry parity), most-recent first by created_at (ISO strings sort
// lexically). Consumers use it for gate copy / routing ONLY — never to derive
// an embeddable key.
export function usableDurableRows(keyRows) {
  return (keyRows || [])
    .filter((k) => k && isManagedKey(k) && !k.revoked_at && k.enabled !== false)
    .sort((a, b) =>
      String(b.created_at || b.createdAt || '').localeCompare(String(a.created_at || a.createdAt || '')))
}

// #1998 fold-in (durable connect key): the wizard connect step's universal
// command embeds an API key the user's agent will authenticate with. It must
// be a DURABLE key — a 24h bootstrap session credential (created_via
// 'bootstrap', expires_at = now+24h) stops authenticating within a day,
// killing any agent configured with it. This classifies the key the connect
// step should embed from server key rows (GET /v1/team/keys — which lists
// ALL rows incl. bootstrap, kept unfiltered in state so prefix matching
// works, #2166). Returns { key, durable, source }:
//   - welcomeKey present → durable (first-time A13 provisioned key)
//   - apiKey (session-held plaintext OR a pasted candidate) matches a durable
//     row (created_via != bootstrap, no expires_at, not revoked, not disabled)
//     → { key: apiKey, durable: true, source: 'durable' } — the connect step
//     may embed it (session-held: pre-#2246 reuse; paste: self-paste trust)
//   - apiKey bootstrap/expiring/revoked/disabled/absent/UNKNOWN (no row — keys
//     not loaded yet or stale) → { key: '', durable: false } so the caller
//     shows the durable gate instead of embedding a possibly-24h or dead key.
//     Never embed on unknown.
// #2246 (session-mode): session users pass apiKey '' — no plaintext is ever
// held — so the gate resolves from the ROWS (usableDurableRows): a usable
// durable exists → source 'rows-durable' (gate copy routes to create/rotate;
// the plaintext was shown once at mint and is unrecoverable from rows);
// none → source 'none' (create-one). Paste validation keeps using the
// apiKey param with the pasted plaintext — revoked/disabled/bootstrap rows
// must still reject (the escape hatch's whole point).
export function durableConnectKey(welcomeKey, apiKey, keyRows) {
  if (welcomeKey) return { key: welcomeKey, durable: true, source: 'welcome' }
  if (!apiKey) {
    const usable = usableDurableRows(keyRows)
    return { key: '', durable: false, source: usable.length ? 'rows-durable' : 'none' }
  }
  const row = (keyRows || []).find((k) => k && k.key_prefix === String(apiKey).slice(0, 10))
  if (!row) return { key: '', durable: false, source: 'unknown' }
  if (row.revoked_at) return { key: '', durable: false, source: 'revoked' }
  if (row.enabled === false) return { key: '', durable: false, source: 'disabled' }
  if (row.created_via === 'bootstrap' || !!row.expires_at) {
    return { key: '', durable: false, source: 'bootstrap' }
  }
  return { key: apiKey, durable: true, source: 'durable' }
}
