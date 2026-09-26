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
// missing/non-finite/negative. A stored `max_nodes === 0` is NOT unknown: it
// is a real (and absolute) enforced cap — `enforce_org_limit` counts
// `used >= 0`, so every points write is refused — and is reported as
// at-limit rather than hidden. The pct/level decision uses the UNROUNDED
// ratio so 99.6% does not round up into "reached".
export function nodeUsage(team) {
  const used = team ? team.nodes_used : null
  const max = team ? team.max_nodes : null
  if (typeof used !== 'number' || !Number.isFinite(used) || used < 0) return null
  if (typeof max !== 'number' || !Number.isFinite(max) || max < 0) return null
  if (max === 0) return { used, max: 0, pct: 100, level: 'at_limit' }
  const ratio = used / max
  // Floor (never round up) and cap below 100: the DISPLAYED percentage must
  // not cross a threshold the level has not — 99.6% may not read "100%" with
  // a near-level amber bar, and 79.6% may not read "80%" with no nudge.
  const pct = ratio >= 1 ? 100 : Math.min(99, Math.floor(ratio * 100))
  return {
    used,
    max,
    pct,
    level: ratio >= 1 ? 'at_limit' : ratio >= NODE_NUDGE_PCT / 100 ? 'near' : 'ok',
  }
}

// The bar caption. A 0-cap plan has no defined percentage, so state the
// blocked condition rather than "0 / 0 nodes used (100%)".
export function nodeUsageText(u) {
  if (!u) return null
  if (u.max === 0) return 'Node limit reached — no node allowance on this plan'
  return `${u.used.toLocaleString()} / ${u.max.toLocaleString()} nodes used (${u.pct}%)`
}

// Bar colour by level: accent under 80%, amber at >=80%, red at 100% (task
// #4331). `--amber` is defined in index.css `:root` (the same #fbbf24 the
// keys surface already uses), so the literal is only a fallback.
export function nodeBarColor(level) {
  if (level === 'at_limit') return 'var(--red, #f87171)'
  if (level === 'near') return 'var(--amber, #fbbf24)'
  return 'var(--accent, #06b6d4)'
}

// The nudge shown at/above the threshold — or null below it / when the usage
// is unknown. Free teams must upgrade to KEEP WRITING (points-gated writes 402
// at the cap); paid tiers only run out of allowance.
//
// `hasUpgrade` (the caller's `nextUpgradePlan(...) !== null`) keeps the copy
// honest: at the top tier, or with a catalog that sells nothing above the
// current plan, the nudge states the cap WITHOUT promising an upgrade that
// cannot be bought. It also never promises a chargeable overage or names a
// price: the owner DECIDED node overage (option B, ~$2/10k nodes/mo above cap
// on Solo/pro/Team, 2026-09-20) but it is NOT implemented, so a charge promise
// would be false.
export function nodeNudge(team, hasUpgrade = true) {
  const u = nodeUsage(team)
  if (!u || u.level === 'ok') return null
  const tier = team ? team.tier : null
  const free = tier == null || tier === 'free' || tier === 'anon'
  const at = u.level === 'at_limit'
  if (free) {
    const base = at ? "You've reached your node limit." : "You're close to your node limit."
    return hasUpgrade ? `${base} Upgrade to keep writing.` : base
  }
  const base = at ? "You've reached your node allowance." : "You're near your node allowance."
  return hasUpgrade ? `${base} Upgrade for a higher allowance.` : base
}

// The next plan above `tier` that this deployment can actually check out — a
// plan whose price id the server resolved. Ordered by `plans` (planOptions()).
// Returns null when there is no purchasable step up (top tier, or an empty
// catalog): the caller must then route to the Billing tab rather than pretend
// a checkout exists. A tier NOT present in the catalog never falls back to the
// lowest plan — that would sell a downgrade as an "upgrade"; only free-like
// tiers start at the bottom of the list.
export function nextUpgradePlan(plans, team) {
  const current = team ? team.tier : null
  const ids = (team && team.checkout_price_ids) || {}
  const paid = (plans || []).filter((p) => p && p.tier !== 'free')
  const freeLike = current == null || current === 'free' || current === 'anon'
  const at = paid.findIndex((p) => p.tier === current)
  const after = at >= 0 ? paid.slice(at + 1) : (freeLike ? paid : [])
  return after.find((p) => ids[p.tier]) || null
}
