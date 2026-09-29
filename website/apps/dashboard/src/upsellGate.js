// upsellGate.js — #4639. Where the dashboard may SELL, and when.
//
// Three owner-reported defects (2026-09-21, on a paid Solo org) that this
// module exists to keep fixed:
//
//   1. The header still upsold on a PAID tier: `team.tier !== 'team'` rendered
//      "<tier> tier · Upgrade" for solo/pro — so a paying Solo user saw
//      "Solo tier · Upgrade". Hitting a limit should do the nudging, at the
//      right time, for the right reason — never a permanent header upsell.
//   2. The header carried a redundant "Manage subscription" (the Billing tab
//      already owns plan management).
//   3. The error banner appended "— Upgrade plan" to ANY error matching
//      /402|upgrade|quota|limit|checkout|billing/i. The billing/portal 404
//      detail contains "checkout", so a fault in OUR OWN billing was answered
//      with an upsell — the worst possible moment.
//
// The derivations are pure and are unit-tested by EXECUTION (upsellGate.test.js)
// so a behaviour-identical reformat cannot flip them, mirroring the
// keyAllowance.js convention. (The module is named upsellGate.js, not
// billingCta.js: PR #4389 owns that path — a distinct name avoids an add/add
// collision and duplicate ownership of the same module.)

// Tiers reachable only by paying. `anon` (internal, unclaimed zero-email teams,
// #1082) and `free` are the only genuinely free tiers.
export const PAID_TIERS = ['solo', 'pro', 'team']

// Subscription statuses that mean a Stripe customer exists — the owner's
// enumeration (#4639): active/trialing/past_due/canceled/unpaid. past_due,
// canceled and unpaid are included DELIBERATELY: a lapsed customer is still
// not a prospect to be sold to from the header.
export const PAID_STATUSES = ['active', 'trialing', 'past_due', 'canceled', 'unpaid']

// A team that has paid. The UNION is deliberate (fail-safe): a paid TIER whose
// subscription_status is absent or unrecognized, and a paying customer whose
// tier has not caught up, both count as paid. The only teams treated as free
// are genuinely free/anon — neither signal present. This direction is the one
// that matters: the reported defect is upselling a PAYER, so an ambiguous team
// must fall on the "don't sell" side.
export function isPaidTeam(team) {
  if (!team) return false
  return PAID_TIERS.includes(team.tier) || PAID_STATUSES.includes(team.subscription_status)
}

// Whether the header may offer an upgrade at all. Free/anon → yes (an upgrade
// path is fine); paid → no (the tier stays VISIBLE, but with no marketing link
// and no "Upgrade"). This is the single gate the header reads, so a paid tier
// cannot regain an upsell through an unrelated render condition.
export function headerUpgradeEligible(team) {
  return Boolean(team) && !isPaidTeam(team)
}

// Where the banner's limit nudge should send the user — or null when this
// team/deployment has no working route, in which case the nudge is not
// rendered at all (never a dead control; mirrors the header gate).
//   'checkout' — free/anon with a server-resolved price id: start the purchase
//   'portal'   — a team that already has a Stripe customer (active/trialing/
//                past_due/canceled/unpaid): checkout 409s on an active
//                subscription, so the portal is the route that works
//   null       — no price id and no customer: nothing the nudge could do
export function nudgeRoute(team) {
  if (!team) return null
  if (headerUpgradeEligible(team) && team.checkout_price_id) return 'checkout'
  // Route on the SAME fail-safe the header uses: a PAID TIER whose status is
  // absent/unrecognized (e.g. a manually granted solo) is still a payer, and
  // checkout 409s on an active subscription — so it must reach the portal, not
  // a button that can only fail.
  if (isPaidTeam(team)) return 'portal'
  return null
}

// The display text for a banner value: plain strings (client-side notices) and
// Error/`{message}` objects both arrive here. Anything else renders empty
// rather than "[object Object]".
export function errorMessage(error) {
  if (error == null) return ''
  if (typeof error === 'string') return error
  if (typeof error.message === 'string') return error.message
  return ''
}

// The HTTP status a banner value carries, when it carries one. `api()` throws
// Error objects with `.status` attached; plain strings from client-side checks
// have none (null) — an absent status is NOT a limit signal, it only means the
// message must speak for itself.
export function errorStatus(error) {
  if (error == null || typeof error === 'string') return null
  return typeof error.status === 'number' ? error.status : null
}

// The machine-readable refusal CATEGORY a banner value carries, when it
// carries one. `api()` attaches `detail.code` from a structured error body
// (#2789); a client-side notice or a legacy string detail has none (null).
// #4614 makes this load-bearing: a 402 is no longer always a plan limit.
export function errorCode(error) {
  if (error == null || typeof error === 'string') return null
  return typeof error.code === 'string' ? error.code : null
}

export function normalizeError(error) {
  return {
    message: errorMessage(error),
    status: errorStatus(error),
    code: errorCode(error),
  }
}

// The refusal phrasings our servers actually emit for a limit that an upgrade
// would relieve — every QuotaExceededError detail is "… limit reached (N).
// Upgrade your plan to increase it." (tortoise/quota.py), and the graph/key/
// member/invite gates follow the same shape. This is DELIBERATELY NOT a
// billing vocabulary: "checkout", "billing", "customer", "upgrade" alone and
// "quota check failed" (a 500 defect) must never qualify.
const LIMIT_SIGNAL_RE = /\blimit (?:reached|exceeded)\b/i

// "Rate limit exceeded" (429) is throttling, not a plan limit — an upgrade
// does not relieve it, so it must never produce an upsell.
const RATE_LIMIT_RE = /\brate[ -]?limit\b/i

// 402s that are NOT plan limits, so an upgrade cannot relieve them and the
// banner must never sell one (#4614). `cohort_cost_cap` is a SPEND ceiling
// (tortoise/cohort_cost.py) — buying a bigger plan does not lift it, so
// nudging an upgrade would be an upsell the user can pay for and still hit.
const NON_PLAN_402_CODES = new Set(['cohort_cost_cap'])

// The banner's "— Upgrade plan" affordance.
//
// A STRUCTURED 402 is the canonical plan-limit refusal and always qualifies
// (it also covers 402 details that do not contain the literal phrase, e.g. a
// budget-exhausted reason) — EXCEPT a 402 whose `code` names a cap an upgrade
// cannot lift (#4614). Otherwise the message must carry an explicit
// "limit reached/exceeded" signal.
//
// A generic billing/checkout/portal error — most importantly our own billing
// 404/500 — carries neither, so it reads as a defect, never a sales prompt.
export function shouldNudgeUpgrade(error) {
  const { message, status, code } = normalizeError(error)
  if (status === 402) return !NON_PLAN_402_CODES.has(code)
  if (RATE_LIMIT_RE.test(message)) return false
  return LIMIT_SIGNAL_RE.test(message)
}
