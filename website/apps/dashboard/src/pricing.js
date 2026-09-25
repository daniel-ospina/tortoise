// #1623: plan display data for the dashboard Billing page — a build-time
// import of product/pricing.json (single source of truth, same file the
// server's tier limits and the marketing product.html pricing grid read).
// The imported DATA cannot drift — this IS the file. The tier DISPLAY names
// below (TIER_LABELS) are a separate hand-maintained map, guarded against
// product.html's `labels` map by
// tests/test_website_static.py::TestDisplayLabelParity (#4336).
//
// Price ids are NOT here: they stay server-resolved (STRIPE_PRICE_IDS via
// /v1/team's checkout_price_ids — the client never hardcodes Stripe ids,
// #310/#1623).
import pricing from '../../../../product/pricing.json'

export const PLAN_TIERS = ['free', 'solo', 'pro', 'team']

// anon = unclaimed zero-email teams (internal tier, #1082) — never shown in
// the grid; the current-plan card humanizes it as Free.
// #4336: the `pro` tier renders as "Builder" — the internal tier key stays
// `pro` (Stripe price ids, pricing.json keys, quota/tier code).
export const TIER_LABELS = {
  free: 'Free',
  solo: 'Solo',
  pro: 'Builder',
  team: 'Team',
  anon: 'Free',
}

export const STATUS_LABELS = {
  active: 'Active',
  trialing: 'Trial',
  past_due: 'Past due',
  canceled: 'Canceled',
  unpaid: 'Unpaid',
}

// Public plan options for the grid (anon excluded — internal quota tier).
export function planOptions() {
  return PLAN_TIERS.map((tier) => {
    const t = pricing.tiers[tier]
    if (!t) return null
    const nodes = t.max_graph_nodes == null ? 'Unlimited nodes' : `${fmtInt(t.max_graph_nodes)}-node graph`
    const graphs = t.max_graphs_per_team == null ? 'Unlimited graphs' : `${fmtInt(t.max_graphs_per_team)} graph${t.max_graphs_per_team > 1 ? 's' : ''}`
    const users = t.max_users_per_team == null ? 'Unlimited collaborators' : `${fmtInt(t.max_users_per_team)} collaborator${t.max_users_per_team > 1 ? 's' : ''}`
    return {
      tier,
      label: TIER_LABELS[tier],
      price: t.price_usd_monthly ?? 0,
      limits: [graphs, users, `${fmtInt(t.included_write_ops_per_month ?? 0)} write ops/mo`, nodes, `${fmtInt(t.max_api_keys ?? 0)} API keys`],
      overage: Boolean(t.overage),
      // #4815: the disclosure a metered tier must show on the plan card the
      // user upgrades from. The STRING is pricing.json's own
      // `display.overage_line` (never a second hardcoded copy of the price);
      // null for a non-metered tier, so the card renders no line at all.
      overageLine: t.overage ? (pricing.display?.overage_line ?? null) : null,
      popular: tier === 'pro',
    }
  }).filter(Boolean)
}

function fmtInt(n) {
  return Number(n).toLocaleString('en-US')
}

// Limits for the current-plan card (fall back to free when the tier is
// unknown/anon — the server already resolves stored caps; this is display).
export function planForTier(tier) {
  return pricing.tiers[tier] || pricing.tiers.free
}
