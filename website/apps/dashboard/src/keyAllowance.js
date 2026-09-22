// keyAllowance.js — #3874. The org's API-key allowance as the SERVER states
// it, surfaced BEFORE the cap.
//
// The gap #3874 closes: `/v1/team` did not return `max_api_keys`, so nothing
// in the UI could say "you get N keys" until N had already been reached and
// POST /v1/team/keys returned its 402. The dashboard's only fallback was a
// hardcoded '2' (main.jsx upgradeNoticeFrom / rotateCapNoticeFrom), which
// would silently drift from the server's real limit.
//
// Two rules this module exists to keep:
//   1. The allowance is READ from the server (`team.max_api_keys`, resolved
//      by the auth lane from the same limits source the mint gate enforces).
//      It is never computed and never hardcoded. When the server has not
//      told us a limit, the surface stays SILENT rather than inventing one.
//   2. The pre-cap line and the at-cap notices derive from the same server
//      field, so a change to one cannot desync the other.
//
// Pure (no React) — node --test unit-tested.

// The number of key rows that OCCUPY a slot on the mint gate's predicate.
//
// Mirrors tortoise.quota._count_resource('api_keys') — the ONE count shared
// by the standalone max_api_keys mint gates (the POST /v1/team/keys mint
// this dashboard's create/rotate flow calls, plus the per-graph mint and the
// REST limit checks): a row counts when it is NOT revoked, NOT expired
// (#2426), and NOT a bootstrap session credential (#4140/R13 — bootstrap
// keys are cap-EXEMPT in the server count, so the display must not count
// them either). A revoked row is an audit tombstone; an expired row can
// never authenticate (#2426); a 24h bootstrap session credential is never a
// manageable product key. Counting any of them would hold a slot for a
// credential the user cannot manage and desync the display from the gate.
//
// `rows` is the RAW GET /v1/team/keys payload (bootstrap rows included — the
// server still lists them), and `created_via` rides that payload in both
// lanes, so the display filters bootstrap exactly as the gate does.
export function usedKeySlots(rows, now = Date.now()) {
  return (rows || []).filter((k) => {
    // Server parity: the count treats a row as revoked when `revoked_at IS
    // NULL` is false — i.e. ANY non-null value (an anomalous '' or 0
    // included). A bare falsy check would count '' as live and over-state
    // usage against a gate that has already freed the slot.
    if (!k || k.revoked_at != null) return false
    // #4140 (R13): checked BEFORE expiry — the server excludes bootstrap
    // regardless of expiry, so a bootstrap row with an absent or unparseable
    // expires_at must still be excluded (#4140 adversarial T6).
    if (k.created_via === 'bootstrap') return false
    if (!k.expires_at) return true
    const t = Date.parse(k.expires_at)
    // An unparseable expiry is treated as NOT expired (conservative: it
    // counts, matching the server's `expires_at > now` string comparison
    // shape rather than silently freeing a slot the gate holds).
    return Number.isNaN(t) || t > now
  }).length
}

// The server's allowance, or null when it has not told us. Only a genuine
// finite non-negative number is accepted — absent / null / junk / a
// negative all mean "unknown", and the caller must not fabricate a limit.
export function serverKeyLimit(team) {
  const raw = team ? team.max_api_keys : null
  if (typeof raw === 'number' && Number.isFinite(raw) && raw >= 0) return Math.floor(raw)
  return null
}

// { limit, used, remaining, exhausted } — or null when the server has not
// supplied an allowance (the surface then renders nothing).
export function keyAllowance(team, rows, now = Date.now()) {
  const limit = serverKeyLimit(team)
  if (limit === null) return null
  const used = usedKeySlots(rows, now)
  return {
    limit,
    used,
    remaining: Math.max(0, limit - used),
    exhausted: used >= limit,
  }
}

// The pre-cap line — "you get N keys" stated plainly wherever keys are shown
// (owner decision D2 (a)). Returns null when the server has not supplied an
// allowance, so a degraded/legacy response renders nothing instead of a
// fabricated number.
export function allowanceLine(team, rows, now = Date.now()) {
  const a = keyAllowance(team, rows, now)
  if (!a) return null
  const noun = a.limit === 1 ? 'API key' : 'API keys'
  return `Your plan includes ${a.limit} ${noun} — ${a.used} in use.`
}

// The at-cap 402 detail carries the server's real limit:
//   'Team api_keys limit reached (N). Upgrade your plan to increase it.'
// The detail's N IS the enforced cap, so prefer it. When the detail is
// absent/unparseable, fall back to the /v1/team allowance — and when NEITHER
// is available, omit the number entirely (the old hardcoded '2' fallback is
// what let the surface drift from a real limit).
export function capLimitFrom(message, team) {
  const m = String(message || '').match(/limit reached \((\d+)\)/)
  if (m) return m[1]
  const limit = serverKeyLimit(team)
  return limit === null ? null : String(limit)
}

// #1147: create-path cap notice. The numbered sentence — "You've reached
// your plan's limit of N API keys." — is the approved wording; #3874 changed
// only the number's SOURCE and added the no-number-degraded sentence.
//
// #2699: the remedy TAIL is not the approved #1147 one. It read "or regenerate
// an existing key instead", which is unreachable AT the cap: rotate mints the
// replacement through the SAME capped POST /v1/team/keys (see
// rotateCapNoticeFrom), so "regenerate" 402s exactly like "create". The tail
// now names the no-cost action that does work at the cap — revoke an existing
// key (a revoked row leaves the gate's count, freeing a slot) — or upgrade.
// It deliberately does NOT say "unused" (which presumes a spare slot-holder
// exists; the count can be held by rows the table hides), does NOT promise a
// SINGLE revoke always suffices (an over-cap org stays at/over the gate until
// enough rows are revoked), and does NOT promise the upgrade frees a key
// immediately.
export function upgradeNoticeFrom(message, team, hasUpgrade = true) {
  const limit = capLimitFrom(message, team)
  const head = limit === null
    ? "You've reached your plan's API key limit"
    : `You've reached your plan's limit of ${limit} API keys`
  // #4335: the "or upgrade" tail is only truthful when an upgrade path exists
  // (a configured higher tier, or one temporarily unavailable). Callers pass
  // hasUpgrade=false for the top tier / a deployment selling no higher tier.
  return `${head}. Revoke an existing key to free a slot${hasUpgrade ? ' — or upgrade to add more.' : '.'}`
}

// #2229: rotate-path cap notice. Rotate mints the REPLACEMENT before revoking
// the old key, so a team AT max_api_keys 402s on the mint leg. Unlike the
// create-path notice above, this one also states WHY the obvious move fails
// (the replacement needs a free slot before the old key is revoked) — that
// mechanism clause is what keeps rotate's notice its own string (#2699 left
// it byte-identical). Truthful escape: revoke an unused key first, or upgrade.
export function rotateCapNoticeFrom(message, team, hasUpgrade = true) {
  const limit = capLimitFrom(message, team)
  const head = limit === null
    ? "You're at your plan's API key limit"
    : `You're at your plan's limit of ${limit} API keys`
  return `${head}. Rotating creates the replacement before revoking this one, so revoke an unused key first${hasUpgrade ? ' — or upgrade to add more.' : '.'}`
}

// #4353: the connect step's existing-key note. Below the cap, rotate is the
// route that does not grow the count. AT the cap it is a dead end for the
// reason rotateCapNoticeFrom states — the replacement is minted through the
// SAME capped POST /v1/team/keys before the old row is revoked — so the note
// returns the canonical at-cap remedy instead of sending the user there.
export function existingKeyNoteFrom(team, rows, now = Date.now()) {
  const a = keyAllowance(team, rows, now)
  if (a && a.exhausted) return rotateCapNoticeFrom('', team)
  return "Rotate the existing key in the API Keys tab to get a value you can use — rotating replaces it without adding a key. Creating a new key here spends another of your plan's key slots."
}

// #4353: the paste-validation rejections tell an owner/admin how to get a key
// they can actually use. AT the cap every one of those routes — create and
// rotate alike — needs a slot the gate has already spent: `create` is refused
// by _check_org_limit before the mint, and `rotate` mints its replacement
// through that SAME capped route before the old row is revoked. So the remedy
// must name revoke first. Empty below the cap, and empty when the server has
// not supplied a limit: the clause is added, never substituted, so no surface
// gains a promise it cannot keep.
export function capRevokeFirstClause(team, rows, now = Date.now()) {
  const a = keyAllowance(team, rows, now)
  if (!a || !a.exhausted) return ''
  return " You are at your plan's key limit, so revoke a key in the API Keys tab first — a create needs a free slot, and a rotate mints its replacement before the old key is freed."
}
