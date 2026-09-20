// #4335: a missing server-side checkout price id is an OUTAGE, not a pricing
// question. The billing CTAs in this change's scope (Billing plan cards, the
// welcome plan chooser, the API-keys cap notice) must say so truthfully and
// stay in-product; the marketing page is at most a secondary, explicitly-
// labelled "Compare plans" link — never the substitute for an Upgrade CTA.
// Kept as a pure derivation so the decision is executable in node tests (no
// React renderer in this suite).
// Out of scope here: the header tier badge (#4331) and the error-banner /
// Graphs-tab Upgrade buttons (silent no-ops when no price id — tracked
// separately).

export const CHECKOUT_UNAVAILABLE_REASON =
  'Card checkout is temporarily unavailable — try again shortly'

// A tier the deployment deliberately does NOT sell (the catalog resolved some
// paid tiers, this one is absent — #2789). Distinguishable from an outage, so
// it must not promise a retry that can never succeed.
export const CHECKOUT_NOT_OFFERED_REASON = 'Not offered on this deployment'

// The one permanent, explicitly-secondary destination. It is never the CTA.
export const COMPARE_PLANS_URL = 'https://tortoise.premiselabs.co/product.html#pricing'

/**
 * Derive the billing CTA for a tier from the server-resolved price id.
 *
 * - a real price id → the normal `Upgrade` button (enabled, opens checkout)
 * - empty/missing    → a DISABLED `Upgrade` control carrying the honest reason.
 *   `anyConfigured` (the catalog resolved ≥1 paid tier, so this absence is a
 *   deliberate not-offered tier — #2789) selects the non-temporal reason;
 *   otherwise the catalog itself is down.
 *
 * Returns `href: null` in BOTH states on purpose: the CTA is never a link.
 * The secondary "Compare plans" link is rendered separately by the caller.
 */
export function checkoutCtaFor(priceId, { anyConfigured = false } = {}) {
  if (priceId) {
    return { label: 'Upgrade', disabled: false, reason: '', priceId, href: null }
  }
  return {
    label: 'Upgrade',
    disabled: true,
    reason: anyConfigured ? CHECKOUT_NOT_OFFERED_REASON : CHECKOUT_UNAVAILABLE_REASON,
    priceId: '',
    href: null,
  }
}

/**
 * The next configured paid tier STRICTLY ABOVE the org's current tier, in
 * `orderedTiers` order — or null when the deployment sells no higher tier.
 * A cap-notice "Upgrade" must never target the current (or a lower) plan.
 * `orderedTiers` is `planOptions()` order (cheapest first) so the derivation
 * needs no import of the pricing module (and stays executable under node).
 */
export function nextUpgradeTier(currentTier, priceIds, orderedTiers) {
  const currentIdx = orderedTiers.indexOf(currentTier)
  if (currentIdx === -1) return null
  for (let i = currentIdx + 1; i < orderedTiers.length; i++) {
    const tier = orderedTiers[i]
    const priceId = priceIds?.[tier]
    if (priceId) return { tier, priceId }
  }
  return null
}
