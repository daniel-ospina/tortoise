// #4335: a missing server-side checkout price id is an OUTAGE, not a pricing
// question. The billing CTAs in this change's scope (Billing plan cards, the
// welcome plan chooser, the API-keys cap notice) must say so truthfully and
// stay in-product; the marketing page is at most a secondary, explicitly-
// labelled "Compare plans" link — never the substitute for an Upgrade CTA.
// Kept as a pure derivation so the decision is executable in node tests (no
// React renderer in this suite).
// Landed since: the header tier badge + error-banner route (#4331 / #4639),
// which now share nudgeRoute/headerUpgradeEligible in upsellGate.js — plus
// #4773's per-tier resolution of the header control itself
// (headerUpgradeCta).

export const CHECKOUT_UNAVAILABLE_REASON =
  'Card checkout is temporarily unavailable — try again shortly'

// A tier the deployment deliberately does NOT sell (the catalog resolved some
// paid tiers, this one is absent — #2789). Distinguishable from an outage, so
// it must not promise a retry that can never succeed.
export const CHECKOUT_NOT_OFFERED_REASON = 'Not offered on this deployment'

// The one permanent, explicitly-secondary destination. It is never the CTA.
export const COMPARE_PLANS_URL = 'https://tortoise.premiselabs.co/#pricing-section'

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
 *
 * `label` is the control's copy and defaults to the historical `Upgrade`, so
 * every pre-#4386 site is unchanged. A caller whose sentence needs different
 * wording (the Graphs lock line reads "Upgrade to add more.") supplies it HERE
 * rather than at the render site — the label belongs to the derivation, so the
 * enabled and disabled arms can never disagree about what the control says.
 */
export function checkoutCtaFor(priceId, { anyConfigured = false, label = 'Upgrade' } = {}) {
  if (priceId) {
    return { label, disabled: false, reason: '', priceId, href: null }
  }
  return {
    label,
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

/**
 * #4773: the tier the header's upgrade control sells.
 *
 * FREE/ANON ONLY — a paid team is suppressed entirely by
 * `headerUpgradeEligible` (#4639, upsellGate.js) — and pinned to `pro`, the
 * server's own default checkout tier (`_default_checkout_price_id`: pro
 * monthly, #310 Task 9 / the #1623 billing plan). Pinning it HERE is what
 * makes the control honest: the price it opens is looked up FOR THIS TIER in
 * the server's per-tier map, so the tier the label names and the price id it
 * posts are the same tier by construction.
 */
export const HEADER_UPGRADE_TIER = 'pro'

/**
 * #4773: the header upgrade control, resolved from the server's PER-TIER map.
 *
 * The defect: the control gated on the singular `team.checkout_price_id` — a
 * field the server fills with whatever its CURRENT default happens to be —
 * while its label named a different tier. The moment that default is any tier
 * but the advertised one (or the header offers a per-tier upgrade), the
 * control opens checkout for the wrong price. Resolving the target tier's OWN
 * id removes the failure mode: the label and the price cannot disagree,
 * because only one tier is involved.
 *
 * `priceId` is `''` when the deployment does not sell the target tier (a
 * legitimately partial catalog, #2789 — pro absent); the caller then renders
 * the plain badge rather than a control with no checkout behind it.
 */
export function headerUpgradeCta(team, tier = HEADER_UPGRADE_TIER) {
  return { tier, priceId: team?.checkout_price_ids?.[tier] || '' }
}

/**
 * The cap-notice upgrade decision, derived from the server-resolved map.
 *
 * - `target`   → the next configured paid tier strictly above the org, or null
 * - `outage`   → a higher tier exists positionally but the catalog resolved
 *                nothing, so the honest DISABLED control must show (never the
 *                marketing link as the substitute CTA)
 * - `hasUpgrade` → whether the "or upgrade to add more" copy is truthful: an
 *                offered upgrade, or one temporarily unavailable; false when
 *                the deployment sells no higher tier (top tier / solo-only).
 */
export function capNoticeUpgrade(team, orderedTiers) {
  const anyConfigured = Object.keys(team?.checkout_price_ids || {}).length > 0
  const target = nextUpgradeTier(team?.tier, team?.checkout_price_ids, orderedTiers)
  const idx = orderedTiers.indexOf(team?.tier)
  const hasHigherTier = idx !== -1 && idx < orderedTiers.length - 1
  const outage = !target && !anyConfigured && hasHigherTier
  return { target, outage, hasUpgrade: Boolean(target) || outage }
}
