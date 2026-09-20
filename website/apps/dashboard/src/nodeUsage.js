// nodeUsage.js — #4331. The org's node usage against the plan's ENFORCED node
// allowance, as the SERVER states both (never computed, never hardcoded).
//
// Why this module exists. The Billing surface showed no node figure at all, so
// a user could not see they were at/near the cap the write path enforces, and
// there was no upgrade nudge when they were. Two server numbers must agree
// with what the write gate actually counts, and NEITHER is `point_count`:
//
//   * `team.nodes_used` is `tortoise.quota.count_org_usage(org, 'points')` —
//     the SAME counter the points cap gates: non-episodic Points PLUS
//     Object + Subject nodes (#1911). `point_count` is `:Point`-only AND
//     demo-excluded, so showing it against the node cap would be a lying UI.
//   * `team.max_nodes` is the org's own cap (`max_points`, a stored per-org
//     override included), NOT the tier's nominal default.
//
// When the server has not told us both numbers the surface stays SILENT rather
// than inventing an allowance or dividing by zero.
//
// Pure (no React) — unit-tested by nodeUsage.test.js via `node --test`.

// The nudge threshold (issue #4331): at/above 80% we tell the user, at 100% the
// write path is refusing.
export const NODE_NUDGE_PCT = 80

// { used, max, pct, level } — or null when either server number is
// missing/non-finite. A zero/negative max is "unknown", not "unlimited": the
// enforced cap is always a positive node count, so a junk value must render
// nothing instead of a fabricated bar.
export function nodeUsage(team) {
  const used = team ? team.nodes_used : null
  const max = team ? team.max_nodes : null
  if (typeof used !== 'number' || !Number.isFinite(used) || used < 0) return null
  if (typeof max !== 'number' || !Number.isFinite(max) || max <= 0) return null
  const pct = Math.min(100, Math.round((used / max) * 100))
  return {
    used,
    max,
    pct,
    level: pct >= 100 ? 'at_limit' : pct >= NODE_NUDGE_PCT ? 'near' : 'ok',
  }
}

// Bar colour by level: accent under 80%, amber at >=80%, red at 100% (task
// #4331). Amber has no token in index.css today, so the literal is the
// documented fallback of a named custom property — a theme can override it
// without touching this module.
export function nodeBarColor(level) {
  if (level === 'at_limit') return 'var(--red, #f87171)'
  if (level === 'near') return 'var(--amber, #f59e0b)'
  return 'var(--accent, #06b6d4)'
}

// The nudge shown at/above the threshold — or null below it / when the usage
// is unknown. Free teams must upgrade to KEEP WRITING (points-gated writes 402
// at the cap); paid tiers only run out of allowance.
//
// The paid string deliberately does NOT promise a chargeable overage or name a
// price: node overage is under consideration (~$2/10k nodes/mo at the declared
// basis) but is NOT implemented, so "you'll be charged N per node" would be a
// false promise. It offers only what actually exists today — a higher plan.
export function nodeNudge(team) {
  const u = nodeUsage(team)
  if (!u || u.level === 'ok') return null
  const tier = team ? team.tier : null
  const free = tier == null || tier === 'free' || tier === 'anon'
  if (free) {
    return u.level === 'at_limit'
      ? "You've reached your node limit — upgrade to keep writing."
      : "You're close to your node limit — upgrade to keep writing."
  }
  return u.level === 'at_limit'
    ? "You've reached your node allowance — upgrade for a higher allowance."
    : "You're near your node allowance — upgrade for a higher allowance."
}

// The next plan above `tier` that this deployment can actually check out — a
// plan whose price id the server resolved. Ordered by `plans` (planOptions()).
// Returns null when there is no purchasable step up (top tier, or an empty
// catalog): the caller must then route to the Billing tab rather than pretend
// a checkout exists.
export function nextUpgradePlan(plans, team) {
  const current = team ? team.tier : null
  const ids = (team && team.checkout_price_ids) || {}
  const paid = (plans || []).filter((p) => p && p.tier !== 'free')
  const at = paid.findIndex((p) => p.tier === current)
  const after = at >= 0 ? paid.slice(at + 1) : paid
  return after.find((p) => ids[p.tier]) || null
}
