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

// #2166 + #2426: durable product keys are the ONLY rows the API Keys page
// shows. The dashboard's OWN access credentials — auto-minted session keys
// (created_via 'bootstrap') — are never presented as product API keys.
// Durable = user/agent-created keys the user can name, toggle, revoke, and
// (since #2426) give a validity window at creation: created_via is the ONLY
// session-credential signal. An EXPIRING durable row (created_via
// 'provisioned'/'recovery'/NULL with expires_at set, e.g. a 30d mint) IS a
// product key — it must stay listed so the table can show its Expires state,
// and #2426's cap-accounting means an expired one is a rotatable tombstone,
// never a hidden row. Disabled (enabled:false) durable rows stay managed —
// the toggle needs them visible.
export function isManagedKey(k) {
  if (!k) return false
  return !(k.created_via === 'bootstrap')
}

// #2246 (ADR-010): rows-source connect resolution. The API Keys table shows
// rows the browser can MANAGE but never HOLD — GET /v1/team/keys returns
// hashes only, so a "usable" durable row can never supply a plaintext key.
// This predicate picks the rows the connect gate may route to as a REUSABLE
// durable — managed (bootstrap-excluded, #2426) ∧ never-expiring (an
// expiring key is not embed-safe: the setup command embeds a key an agent
// authenticates with indefinitely, so the embed policy stays Never-only,
// #2426 decision 2) ∧ ¬revoked ∧ ¬disabled (enabled absent → enabled,
// registry parity), most-recent first by created_at (ISO strings sort
// lexically). Consumers use it for gate copy / routing ONLY — never to derive
// an embeddable key.
export function usableDurableRows(keyRows) {
  return (keyRows || [])
    .filter((k) => k && isManagedKey(k) && !k.expires_at && !k.revoked_at && k.enabled !== false)
    .sort((a, b) =>
      String(b.created_at || b.createdAt || '').localeCompare(String(a.created_at || a.createdAt || '')))
}

// #1998 fold-in (durable connect key): the wizard connect step's universal
// command embeds an API key the user's agent will authenticate with. It must
// be a DURABLE key — a 24h bootstrap session credential (created_via
// 'bootstrap', expires_at = now+24h) stops authenticating within a day,
// killing any agent configured with it. #2426: expiring DURABLE keys are also
// not embed-safe (they stop authenticating at their chosen expiry — the
// connect step is the Never-keys-only embed surface, decision 2), so the
// classifier refuses them exactly like bootstrap rows but reports the
// DISTINCT source 'expiring' so the wizard's paste error can say why (an
// expiring 30d durable is NOT a "login session" key). This classifies the
// key the connect step should embed from server key rows (GET /v1/team/keys
// — which lists ALL rows incl. bootstrap, kept unfiltered in state so prefix
// matching works, #2166). Returns { key, durable, source }:
//   - welcomeKey present → durable (first-time A13 provisioned key) — UNLESS
//     keyRows is a loaded non-empty array and the welcome plaintext's prefix
//     row is absent/revoked/disabled/bootstrap/expiring: the shown-once
//     reveal is then STALE
//     (rotated/revoked/disabled after it was shown) and must fall through to
//     the rows/paste resolution as if welcomeKey were absent (#2246 review)
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
// apiKey param with the pasted plaintext — revoked/disabled/bootstrap/
// expiring rows must still reject (the escape hatch's whole point).
export function durableConnectKey(welcomeKey, apiKey, keyRows) {
  // #2246 (review, P1/P2): row-truth stale check on the in-memory welcome
  // plaintext — post-#2246 the table actions are uniform one-click (rotate /
  // revoke / disable), so a welcomeKey whose prefix row is gone/revoked/
  // disabled must NOT win. P2: a bootstrap/expiring row is dead for embedding
  // too (symmetric with the paste tail below — a 24h session credential, or
  // any key with an expiry, must never back an embed); welcome keys are
  // provisioned without expiry (created_via 'provisioned', expires_at null),
  // so this arm is a harmless belt that keeps the predicate identical to
  // main.jsx's row-truth effect. Check only when keyRows is a LOADED non-empty
  // array (null/[] = not loaded yet — the pre-load welcome reveal must keep
  // working; the provisioned row lands in the same loadAll response). When
  // stale, set the welcome plaintext aside and proceed as if it were absent
  // so the normal rows/paste resolution (rows-durable / none / durable /
  // bootstrap / expiring / revoked / disabled / unknown) runs.
  let welcome = welcomeKey
  if (welcome && Array.isArray(keyRows) && keyRows.length > 0) {
    const row = keyRows.find((k) => k && k.key_prefix && welcome.startsWith(k.key_prefix))
    if (!row || row.revoked_at || row.enabled === false || row.created_via === 'bootstrap' || !!row.expires_at) welcome = ''
  }
  if (welcome) return { key: welcome, durable: true, source: 'welcome' }
  if (!apiKey) {
    const usable = usableDurableRows(keyRows)
    return { key: '', durable: false, source: usable.length ? 'rows-durable' : 'none' }
  }
  const row = (keyRows || []).find((k) => k && k.key_prefix === String(apiKey).slice(0, 10))
  if (!row) return { key: '', durable: false, source: 'unknown' }
  if (row.revoked_at) return { key: '', durable: false, source: 'revoked' }
  if (row.enabled === false) return { key: '', durable: false, source: 'disabled' }
  if (row.created_via === 'bootstrap') return { key: '', durable: false, source: 'bootstrap' }
  if (row.expires_at) return { key: '', durable: false, source: 'expiring' }
  return { key: apiKey, durable: true, source: 'durable' }
}

// #3783: the connect step's KEY-SOURCE GATE. `durableConnectKey` answers
// "which plaintext, if any, may we EMBED" — it returns an empty key for BOTH
// `'none'` (the org holds no key at all) and `'rows-durable'` (a usable
// durable row exists, but its plaintext was displayed once and is not in this
// browser). The wizard rendered ONE affordance for both: the mint CTA. So an
// owner/admin whose organization was provisioned with a key at org creation
// (`created_via: 'provisioned'`, revealed in the org-create→connect window and
// lost by any reload before the connect step) minted a SECOND key at connect —
// spending the free tier's entire 2-key allowance on a key the user never got
// to use — while the Overview banner read the SAME `'rows-durable'` source and
// said an existing key was usable. This gate separates the two so the connect
// step can offer the existing key (rotate reuses its slot) instead of minting.
// Returns { mode, key, existing }:
//   - front-channel plaintext held → { mode: 'embed',  key, existing: null }
//   - usable durable row, no held plaintext → { mode: 'existing', existing: row }
//   - no usable key anywhere → { mode: 'mint', existing: null }
//   - rows not loaded yet → { mode: 'loading', existing: null }
//   - the rows GET FAILED → { mode: 'error', existing: null }
// Consumers may mint ONLY on `'mint'`; `'existing'` must route to a reuse path,
// and `'loading'`/`'error'` must offer NEITHER — neither is a resolved answer.
//
// #3783 (review P2): `keysLoaded` marks whether the rows payload has actually
// ARRIVED. Without it, `keys` initialises to `[]`, so a slow or failed `GET
// /v1/team/keys` made the gate resolve `'mint'` for an organization that may
// already hold a usable row — the UI offered a mint CTA that can burn the free
// tier's last slot in exactly the window it must not. The default is `true` so
// existing callers (and the unit tests) that pass a real rows array keep the
// loaded meaning; `main.jsx` passes the live load state explicitly.
//
// #3783 (review P2, second pass): `keysLoaded` flips on SUCCESS only, so it
// cannot tell "still in flight" from "the GET failed". Collapsing both into
// `'loading'` gave that arm no failure exit — a failed read rendered the wait
// state forever, its only recovery a full page reload. `keysError` (main.jsx's
// recorded failure) therefore resolves `'error'`: still NO mint (an unresolved
// read is not "no key", the slot-burn this gate exists to prevent), but a
// state the caller can act on with a retry. Default `false` keeps the callers
// that never observe a fetch (and the unit tests) unchanged.
export function connectKeyGate(welcomeKey, keyRows, keysLoaded = true, keysError = false) {
  const dc = durableConnectKey(welcomeKey, '', keyRows)
  if (dc.key) return { mode: 'embed', key: dc.key, existing: null }
  if (!keysLoaded) return { mode: keysError ? 'error' : 'loading', key: '', existing: null }
  if (dc.source === 'rows-durable') {
    return { mode: 'existing', key: '', existing: usableDurableRows(keyRows)[0] || null }
  }
  return { mode: 'mint', key: '', existing: null }
}

// #3783: a display identity for a key row the user never named. The org-create
// provisioning mints the organization's first key with `name: null`, so the
// API Keys table rendered it as "—" — the user could see it (the issue's own
// walk did) but could not ACCOUNT for it, and could not tell it apart from any
// other unnamed row. A `'provisioned'` row with no name IS the organization's
// setup key; name it. Review P2: `'recovery'` (the owner-level keyless-mint
// lane) and legacy rows minted before `created_via` existed are ALSO unnamed
// real, slot-consuming keys — leaving them as an unaccountable "—" is the same
// defect one row over, so they get a source-specific label too. The terminal
// fallback covers any future unnamed mint source: every row the API Keys table
// lists is a durable product key (bootstrap rows are excluded by
// `isManagedKey`), so none should render as nothing. Returns null only when
// there is no row at all; a user-named row always wins.
export function keyDisplayName(k) {
  if (!k) return null
  if (k.name) return k.name
  if (k.created_via === 'provisioned') return 'Organization key'
  if (k.created_via === 'recovery') return 'Recovery key'
  return 'API key'
}
