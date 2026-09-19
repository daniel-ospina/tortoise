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
// by every max_api_keys mint gate (the standalone POST /v1/team/keys mint
// this dashboard's create/rotate flow calls, plus the per-graph mint and the
// REST/MCP limit checks): a row counts when it is NOT revoked AND NOT
// expired. A revoked row is an audit tombstone; an expired row can never
// authenticate (#2426), so counting either would hold a slot for a dead
// credential and wedge the org at its cap.
//
// `rows` is the RAW GET /v1/team/keys payload, NOT the table's managedKeys
// filter: the server gate counts every live row (bootstrap session
// credentials included), so the display counts the same set and the
// "N of M used" it shows agrees with what the next create will do.
export function usedKeySlots(rows, now = Date.now()) {
  return (rows || []).filter((k) => {
    if (!k || k.revoked_at) return false
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

// #1147: create-path cap notice. The numbered sentence is the approved
// wording; only the number's SOURCE changes (#3874) and only the
// no-number-degraded sentence is new.
export function upgradeNoticeFrom(message, team) {
  const limit = capLimitFrom(message, team)
  if (limit === null) {
    return "You've reached your plan's API key limit. Upgrade to add more — or regenerate an existing key instead."
  }
  return `You've reached your plan's limit of ${limit} API keys. Upgrade to add more — or regenerate an existing key instead.`
}

// #2229: rotate-path cap notice. Rotate mints the REPLACEMENT before revoking
// the old key, so a team AT max_api_keys 402s on the mint leg — the generic
// notice's "regenerate instead" tail would loop here (regenerating needs the
// same free slot). Truthful escape: revoke an unused key first, or upgrade.
export function rotateCapNoticeFrom(message, team) {
  const limit = capLimitFrom(message, team)
  if (limit === null) {
    return "You're at your plan's API key limit. Rotating creates the replacement before revoking this one, so revoke an unused key first — or upgrade to add more."
  }
  return `You're at your plan's limit of ${limit} API keys. Rotating creates the replacement before revoking this one, so revoke an unused key first — or upgrade to add more.`
}
