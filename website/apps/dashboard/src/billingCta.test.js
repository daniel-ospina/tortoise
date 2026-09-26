// billingCta.test.js — #4335. EXECUTION tests for the honest billing CTA.
//
// The defect: when the server resolved no checkout price id, the three
// dashboard billing CTAs in #4335's scope silently rendered a marketing link
// ("See pricing"). A buyer was routed to a brochure instead of being told
// checkout was unavailable. (Two further live CTAs no-op silently — #4386 —
// and the header tier badge is #4331; neither is this change's scope.)
//
// These import and RUN checkoutCtaFor (./billingCta.js), so a behaviour-
// identical reformat cannot flip them. The "must not be a link" property is
// pinned in BOTH states, not just the unavailable one.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import {
  CHECKOUT_NOT_OFFERED_REASON,
  CHECKOUT_UNAVAILABLE_REASON,
  COMPARE_PLANS_URL,
  capNoticeUpgrade,
  checkoutCtaFor,
  nextUpgradeTier,
} from './billingCta.js'
import { stripComments } from './testSupport.js'

const here = dirname(fileURLToPath(import.meta.url))

test('#4335: a real price id yields the normal Upgrade CTA (never a link)', () => {
  const cta = checkoutCtaFor('price_200proMM')
  assert.equal(cta.label, 'Upgrade')
  assert.equal(cta.disabled, false)
  assert.equal(cta.priceId, 'price_200proMM')
  assert.equal(cta.href, null, 'the CTA must never be an href')
  assert.equal(cta.reason, '')
})

test('#4335: a missing price id yields a DISABLED Upgrade + the honest reason', () => {
  for (const missing of ['', null, undefined]) {
    const cta = checkoutCtaFor(missing)
    assert.equal(cta.label, 'Upgrade', `input ${String(missing)}`)
    assert.equal(cta.disabled, true, `input ${String(missing)}`)
    assert.equal(cta.reason, CHECKOUT_UNAVAILABLE_REASON, `input ${String(missing)}`)
    assert.equal(cta.href, null, 'unavailable is NOT a marketing link')
  }
})

test('#4335: the reason is the honest unavailability copy, not a pricing pitch', () => {
  assert.match(CHECKOUT_UNAVAILABLE_REASON, /temporarily unavailable/i)
  assert.doesNotMatch(CHECKOUT_UNAVAILABLE_REASON, /see pricing|learn more/i)
})

test('#4335: the compare-plans destination is the canonical pricing anchor', () => {
  // product.html 301s to '/' on the tortoise host; '#pricing' is a dead
  // fragment — the canonical, test-pinned anchor is '#pricing-section'.
  assert.equal(COMPARE_PLANS_URL, 'https://tortoise.premiselabs.co/#pricing-section')
  // No checkoutCtaFor state ever routes the primary CTA off-product.
  for (const priceId of ['price_x', '']) {
    assert.equal(checkoutCtaFor(priceId).href, null)
  }
})

test('#4335: cap-notice upgrade decision distinguishes outage from not-sold', () => {
  const order = ['free', 'solo', 'pro', 'team']
  // A configured higher tier → real target.
  assert.deepEqual(capNoticeUpgrade({ tier: 'solo', checkout_price_ids: { pro: 'p_pro' } }, order),
    { target: { tier: 'pro', priceId: 'p_pro' }, outage: false, hasUpgrade: true })
  // Empty catalog + a higher tier exists → outage control, "or upgrade" still true.
  assert.deepEqual(capNoticeUpgrade({ tier: 'solo', checkout_price_ids: {} }, order),
    { target: null, outage: true, hasUpgrade: true })
  // No higher tier sold (solo-only) → no CTA, copy must not promise upgrade.
  assert.deepEqual(capNoticeUpgrade({ tier: 'solo', checkout_price_ids: { solo: 'p_solo' } }, order),
    { target: null, outage: false, hasUpgrade: false })
  // Top tier → no CTA, no upgrade copy.
  assert.deepEqual(capNoticeUpgrade({ tier: 'team', checkout_price_ids: { team: 'p_team' } }, order),
    { target: null, outage: false, hasUpgrade: false })
})

test('#4335: a not-offered tier reads as not-offered, not as a retryable outage', () => {
  // The catalog resolved SOME paid tier, so this tier's absence is deliberate
  // (#2789) — the copy must not promise a retry that can never succeed.
  const cta = checkoutCtaFor('', { anyConfigured: true })
  assert.equal(cta.disabled, true)
  assert.equal(cta.reason, CHECKOUT_NOT_OFFERED_REASON)
  assert.doesNotMatch(cta.reason, /try again|temporarily/i)
  assert.equal(cta.href, null)
  // …while a genuinely-empty catalog keeps the outage copy.
  assert.equal(checkoutCtaFor('', { anyConfigured: false }).reason,
    CHECKOUT_UNAVAILABLE_REASON)
})

test('#4335: the cap-notice target is the next configured tier strictly above the org (never downgrade)', () => {
  const order = ['free', 'solo', 'pro', 'team']
  const ids = { solo: 'p_solo', pro: 'p_pro', team: 'p_team' }
  assert.deepEqual(nextUpgradeTier('free', ids, order), { tier: 'solo', priceId: 'p_solo' })
  assert.deepEqual(nextUpgradeTier('solo', ids, order), { tier: 'pro', priceId: 'p_pro' })
  assert.deepEqual(nextUpgradeTier('pro', ids, order), { tier: 'team', priceId: 'p_team' })
  // Top tier → no higher plan exists.
  assert.equal(nextUpgradeTier('team', ids, order), null)
  // Partial catalog (solo only): a solo org has nothing to upgrade to, and a
  // team org must never be offered a cheaper plan.
  assert.equal(nextUpgradeTier('solo', { solo: 'p_solo' }, order), null)
  assert.equal(nextUpgradeTier('team', { solo: 'p_solo' }, order), null)
})

// ── Wiring backstop (presence, NOT behavioural proof) ─────────────────────
// The execution tests above prove the decision. This guards against the render
// sites being deleted or reverting to the marketing-link fallback in main.jsx.
// NOTE: the welcome plan chooser lives inside the archived (A0) wizard block
// (LEGACY_WIZARD_ARCHIVED = false) — #4335 still has to fix it so a rollback
// cannot resurrect the marketing link. The `no See pricing` scan covers that
// site; the two assertions below pin the LIVE render sites so dead code alone
// cannot satisfy this backstop.
test('#4335 (wiring backstop): the fallbacks render the disabled CTA, never "See pricing"', () => {
  const src = readFileSync(join(here, 'main.jsx'), 'utf8')
  // Scan the STRIPPED source — a comment that quotes the old copy (e.g. the
  // A0 contract note recording the #4335 departure) is not a rendered CTA.
  const code = stripComments(src)
  assert.doesNotMatch(code, /See pricing/,
    'no billing CTA may render the old marketing-link fallback')
  const sites = (code.match(/<UpgradeCta\b/g) || []).length
  assert.ok(sites >= 3,
    `expected the cap notice + welcome chooser + billing cards, found ${sites}`)
  // Live site 1 — the API-keys cap notice (tab + create-key modal via CapNotice).
  assert.match(code, /className="ghost small" \/>/,
    'the cap-notice CTA must render through UpgradeCta')
  // Live site 2 — the Billing tab plan cards (and the welcome chooser): the
  // disabled button must fill the card like the enabled one, so both .plan-card
  // sites pass `block`.
  assert.match(code, /className="btn-primary" block \/>/,
    'the billing plan-card CTA must render through UpgradeCta')
  assert.ok((code.match(/ block \/>/g) || []).length >= 2,
    'both plan-card UpgradeCta sites must pass block')
  // a11y contract: native disabled, reason associated via aria-describedby,
  // and NO contradictory aria-disabled.
  assert.match(code, /disabled title=\{cta\.reason\} aria-describedby=\{reasonId\}/,
    'the disabled control must carry the honest reason as its title/description')
  assert.match(code, /id=\{reasonId\}/,
    'the reason span must be the aria-describedby target')
  const upgradeBlock = code.slice(code.indexOf('function UpgradeCta'),
    code.indexOf('function CapNotice'))
  assert.ok(upgradeBlock.length > 0, 'UpgradeCta must exist')
  assert.doesNotMatch(upgradeBlock, /aria-disabled/,
    'a native disabled control must not also claim aria-disabled')
  assert.match(code, /title=\{cta\.reason\}/,
    'the disabled control must carry the honest reason as its title')
  assert.match(code, /Free — no card needed/,
    'the $0 plan card must not render the outage CTA')
  assert.match(code, />Compare plans<\/a>/,
    'the secondary pricing link must be explicitly labelled "Compare plans"')
})
