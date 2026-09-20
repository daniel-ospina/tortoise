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

// The one permanent, explicitly-secondary destination. It is never the CTA.
export const COMPARE_PLANS_URL = 'https://tortoise.premiselabs.co/product.html#pricing'

/**
 * Derive the billing CTA for a tier from the server-resolved price id.
 *
 * - a real price id → the normal `Upgrade` button (enabled, opens checkout)
 * - empty/missing    → a DISABLED `Upgrade` control carrying the honest reason
 *
 * Returns `href: null` in BOTH states on purpose: the CTA is never a link.
 * The secondary "Compare plans" link is rendered separately by the caller.
 */
export function checkoutCtaFor(priceId) {
  if (priceId) {
    return { label: 'Upgrade', disabled: false, reason: '', priceId, href: null }
  }
  return {
    label: 'Upgrade',
    disabled: true,
    reason: CHECKOUT_UNAVAILABLE_REASON,
    priceId: '',
    href: null,
  }
}
