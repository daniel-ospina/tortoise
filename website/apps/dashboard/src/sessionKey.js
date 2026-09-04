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

// #2167 (Approach B): pure classification/transition helpers for the
// zero-bootstrap-mint rewiring — extracted pure so they run under node
// --test (main.jsx has no component harness — #2178 suite docstring). All
// prefix matching reuses keyIdFromValue's semantics: auth pre-filters on
// token[:10], so match on key_prefix === plaintext.slice(0,10).

// Drop predicate shared by classifyHeldKey: row.revoked_at OR
// enabled === false OR isSessionKey(row). NOT isSessionKey alone — it
// false-short-circuits on revoked_at (L6 above), so a reconcile-swept
// bootstrap or a cross-session-revoked durable would otherwise survive
// mid-session; and enabled:false row-truth is required independently of the
// mount probe because resolve_api_key has an accepted fail-open degrade
// seam (supabase_control.py L537-590) that can let a disabled key auth
// during drift.
function rowTruthBad(row) {
  return !!row && (!!row.revoked_at || row.enabled === false || isSessionKey(row))
}

function prefixOf(value) {
  return value ? String(value).slice(0, 10) : null
}

// H1 — rule-5 classification hook (runs after the loadAll keys landing, the
// SOLE full-payload landing). Classifies the HELD key against server rows;
// slotContent is evaluated INDEPENDENTLY of the held key (P2, phase-7
// reviewer 1): a slot holding row-truth-bad residue is reaped even when
// apiKey state is '' (rule-5e/5f corner — slot retained + completeLogin
// (null) would otherwise leave the residue for the whole session). NEVER
// clears merely because slot !== held — a valid off-team durable slot
// survives reload-pinning (rule 3).
// Returns { drop, reason, clearSlot } where reason ∈ 'revoked'|'disabled'|
// 'bootstrap'|'expiring'|'durable'|'held-not-listed'|'none'. Caller applies
// the state delta (setApiKey('') + apiKeyRef + teamKeysRef[teamId] delete).
export function classifyHeldKey({ held, rows, slotContent }) {
  const keyRows = Array.isArray(rows) ? rows : ((rows && rows.keys) || [])
  const rowFor = (plaintext) => {
    const prefix = prefixOf(plaintext)
    if (!prefix) return null
    return keyRows.find((k) => k && k.key_prefix === prefix) || null
  }
  const slotRow = rowFor(slotContent)
  const clearSlot = !!slotContent && rowTruthBad(slotRow)
  if (!held) return { drop: false, reason: 'none', clearSlot }
  const r = rowFor(held)
  // held-not-listed: held plaintext matches no row — transient tolerance
  // (first landing before rows arrive / stale-guard skip). No drop; a
  // durable-looking field-less row is never dropped (durable-conservative).
  if (!r) return { drop: false, reason: 'held-not-listed', clearSlot }
  if (r.revoked_at) return { drop: true, reason: 'revoked', clearSlot }
  if (r.enabled === false) return { drop: true, reason: 'disabled', clearSlot }
  if (r.created_via === 'bootstrap' || !!r.expires_at) {
    return { drop: true, reason: r.created_via === 'bootstrap' ? 'bootstrap' : 'expiring', clearSlot }
  }
  return { drop: false, reason: 'durable', clearSlot }
}

// H2 — rule-7 revoke leg (falsifier 3): slot-aware clear after a DELETE.
// PREFIX-derived equality (keyIdFromValue semantics) so it is
// row-deletion-proof: the revoked key's row may be gone post-DELETE/loadAll
// — prefix matching never needs it. Falsy slot content ('undefined'/'null'
// residue) never counts as a match.
// Returns { clearSlot, clearCachedKey } (independent booleans).
export function heldKeyClearState({ revokedKeyPrefix, slotContent, cachedAtTeam }) {
  return {
    clearSlot: !!slotContent && prefixOf(slotContent) === revokedKeyPrefix,
    clearCachedKey: !!cachedAtTeam && prefixOf(cachedAtTeam) === revokedKeyPrefix,
  }
}

// H3 — rule-7 regenerate install leg: the replacement lands UNCONDITIONALLY.
// Closes the pre-existing gap where apiKey STATE held the just-revoked key
// until reload (the old install synced apiKeyRef/localStorage but never
// setApiKey) and fixes the slot-conditional write (regenerate must always
// install its replacement).
export function nextRegenInstallState({ newKeyVal }) {
  return { writeSlot: newKeyVal, setApiKey: newKeyVal, cacheKey: newKeyVal }
}

// H4 — rule-5 mount probe branch table (5a-f), unit-testable without a
// browser. selectionSnapshot = teamIdRef.current captured BEFORE the probe
// fetch; selectionNow = teamIdRef.current when the probe returns — 5b
// adopts/pins only when they match (the #1567 mid-probe-switch guard: a
// switch during the fetch must not be clobbered by adoption of the stale
// team's stored key). 403 handling hand-parses the suspension dict from the
// raw-fetch JSON body (the probe is NOT api(), so api()'s err.suspended
// shaping does not apply).
// Returns { action: 'adopt'|'drop'|'keep-suspended'|'keep-session-only',
// teamId?, detail? } — detail carries the suspension dict on keep-suspended.
export function probeClassifyStoredKey({ status, detail, teamsList, selectionSnapshot, selectionNow }) {
  if (status === 200) {
    const t = (detail && typeof detail === 'object' && detail.team_id)
      ? (teamsList || []).find((x) => x && x.team_id === detail.team_id)
      : null
    if (!t) return { action: 'keep-session-only' } // 5f: membership gone — slot retained
    if (selectionNow !== selectionSnapshot) return { action: 'keep-session-only' } // 5b-stale (#1567)
    return { action: 'adopt', teamId: t.team_id }
  }
  if (status === 401) return { action: 'drop' } // 5c: revoked/disabled/expired reject identically
  if (status === 403) {
    const sus = (detail && typeof detail === 'object' && detail.detail && typeof detail.detail === 'object'
      && detail.detail.code === 'SUSPENDED') ? detail.detail : null
    if (sus) return { action: 'keep-suspended', detail: sus } // 5d — LIVE key-lane 403 dict
    return { action: 'drop' } // 5c2: 403 non-suspension (near-unreachable here) — belt-and-braces
  }
  // 5e: network (status 0) / 5xx / rate-limit-retry signals (429/408/425) →
  // transient — destroying a valid durable on a hiccup would be
  // unrecoverable (no adopt-existing UI). Slot retained for next mount.
  if (status === 0 || status >= 500 || status === 429 || status === 408 || status === 425) {
    return { action: 'keep-session-only' }
  }
  return { action: 'drop' } // 5c2: other 4xx — belt-and-braces
}
