// #1708 D8: session-key classification from server-provided API data,
// extracted pure so it's unit-testable with node --test (no harness).
// (k: key row, activeKey: the current session's plaintext key, or null)
// NOTE (#2166): isSessionKey is NOT imported by main.jsx anymore — the API
// Keys page renders durable keys only (isManagedKey below). Retained for the
// #2167 browser-auth workstream + registry-lane stale-cache handling — not on
// the current UI path. isActiveKey is the live protection in main.jsx.
export function isSessionKey(k, activeKey) {
  if (!k || k.revoked_at) return false
  if (k.created_via === 'bootstrap' || !!k.expires_at) return true
  if (k.created_via == null) {
    // API fields absent (stale cached responses / registry lane pre-#1709):
    // keep the old active-key guard so the live session key can't be revoked
    return !!activeKey && (k.key_prefix === String(activeKey).slice(0, 10))
  }
  return false // durable (created_via 'provisioned'/'recovery'/etc.)
}
// separate guard — the live data-plane key must NEVER be revocable from the
// UI, even when it is a durable key (created_via 'provisioned'):
export function isActiveKey(k, activeKey) {
  return !!activeKey && !k.revoked_at && (k.key_prefix === String(activeKey).slice(0, 10))
}
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

// #1998 fold-in (durable connect key): the wizard connect step's universal
// command embeds an API key the user's agent will authenticate with. It must
// be a DURABLE key — the 24h bootstrap session credential minted at login
// (created_via 'bootstrap', expires_at = now+24h) stops authenticating within
// a day, killing any agent configured with it. This classifies the key the
// connect step should embed from server key rows (GET /v1/team/keys — which
// lists ALL rows incl. bootstrap, kept unfiltered in state so prefix matching
// works, #2166). Returns { key, durable, source }:
//   - welcomeKey present → durable (first-time A13 provisioned key)
//   - apiKey matches a durable row (created_via != bootstrap, no expires_at,
//     not revoked, not disabled)
//   - apiKey bootstrap/expiring/revoked/disabled/absent/UNKNOWN (no row — keys
//     not loaded yet or stale) → { key: '', durable: false } so the caller
//     shows the durable gate instead of embedding a possibly-24h or dead key.
//     Never embed on unknown.
export function durableConnectKey(welcomeKey, apiKey, keyRows) {
  if (welcomeKey) return { key: welcomeKey, durable: true, source: 'welcome' }
  if (!apiKey) return { key: '', durable: false, source: 'none' }
  const row = (keyRows || []).find((k) => k && k.key_prefix === String(apiKey).slice(0, 10))
  if (!row) return { key: '', durable: false, source: 'unknown' }
  if (row.revoked_at) return { key: '', durable: false, source: 'revoked' }
  if (row.enabled === false) return { key: '', durable: false, source: 'disabled' }
  if (row.created_via === 'bootstrap' || !!row.expires_at) {
    return { key: '', durable: false, source: 'bootstrap' }
  }
  return { key: apiKey, durable: true, source: 'durable' }
}
