// billingCta.test.js — #4335. EXECUTION tests for the honest billing CTA.
//
// The defect: when the server resolved no checkout price id, the three
// dashboard billing CTAs in #4335's scope silently rendered a marketing link
// ("See pricing"). A buyer was routed to a brochure instead of being told
// checkout was unavailable. (The header tier badge is #4331; the error-banner
// arm of the pair #4386 was filed for turned out to be ALREADY gated —
// #4639's `nudgeRoute` returns 'checkout' only with a server price id, pinned
// in upsellGate.test.js — so #4386's live site was the Graphs-tab lock line,
// tested at the bottom of this file.)
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
  HEADER_UPGRADE_TIER,
  capNoticeUpgrade,
  checkoutCtaFor,
  headerUpgradeCta,
  nextUpgradeTier,
} from './billingCta.js'
import { nextUpgradePlan } from './nodeUsage.js'
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

// ── #4386: the Graphs-tab lock line's CTA ────────────────────────────────
// Re-measured on this branch: of the two sites #4386 lists, the error-banner
// arm is already safe (#4639 gates it on `nudgeRoute(team) === 'checkout'`,
// which is unreachable without `team.checkout_price_id` — pinned in
// upsellGate.test.js), so the LIVE dead click was the Graphs-tab lock line:
// `<button className="ghost" onClick={upgrade}>` — enabled, and wired to
// `upgradeToPrice(team.checkout_price_id)` whose `if (!priceId) return` made
// it do nothing, silently.
const PLANS = [
  { tier: 'free' }, { tier: 'solo' }, { tier: 'pro' }, { tier: 'team' },
]

test('#4386: a caller-supplied label is honoured in BOTH states (never a link)', () => {
  const enabled = checkoutCtaFor('price_200proMM', { label: 'Upgrade to add more.' })
  assert.equal(enabled.label, 'Upgrade to add more.')
  assert.equal(enabled.disabled, false)
  assert.equal(enabled.reason, '')
  assert.equal(enabled.href, null)
  const disabled = checkoutCtaFor('', { label: 'Upgrade to add more.' })
  assert.equal(disabled.label, 'Upgrade to add more.',
    'the DISABLED control must say what the enabled one would have said')
  assert.equal(disabled.disabled, true)
  assert.equal(disabled.reason, CHECKOUT_UNAVAILABLE_REASON)
  assert.equal(disabled.href, null, 'unavailable is still NOT a marketing link')
  // The default is untouched, so every pre-#4386 site is behaviour-identical.
  assert.equal(checkoutCtaFor('price_x').label, 'Upgrade')
  assert.equal(checkoutCtaFor('').label, 'Upgrade')
})

test('#4386: a free/anon team on a PARTIAL catalog gets a real checkout, not a dead click', () => {
  // The second half of the defect: a solo-only deployment (a supported partial
  // catalog, #2789) resolves NO pro-default price id, so the old target — the
  // pro default — had nothing to sell while solo WAS purchasable.
  const team = { tier: 'free', checkout_price_ids: { solo: 'price_solo' } }
  assert.equal(team.checkout_price_id, undefined)
  assert.equal(checkoutCtaFor(team.checkout_price_id).disabled, true,
    'the old pro-default target is a control with no checkout behind it')
  const target = nextUpgradePlan(PLANS, team)
  assert.equal(target.tier, 'solo', 'the next purchasable plan is solo, not the pro default')
  const priceId = team.checkout_price_ids[target.tier]
  const cta = checkoutCtaFor(priceId)
  assert.equal(cta.disabled, false, 'the next purchasable plan IS a real checkout')
  assert.equal(cta.priceId, 'price_solo')
  // anon is the internal free tier and must behave identically.
  assert.equal(
    nextUpgradePlan(PLANS, { tier: 'anon', checkout_price_ids: { solo: 'price_solo' } }).tier,
    'solo')
})

test('#4386: no purchasable tier at all → the DISABLED control with the outage reason', () => {
  const team = { tier: 'free', checkout_price_ids: {} }
  assert.equal(nextUpgradePlan(PLANS, team), null)
  const cta = checkoutCtaFor(team.checkout_price_id || '', {
    anyConfigured: Object.keys(team.checkout_price_ids).length > 0,
    label: 'Upgrade to add more.',
  })
  assert.equal(cta.disabled, true)
  assert.equal(cta.reason, CHECKOUT_UNAVAILABLE_REASON)
})

test('#4386 (wiring backstop): the lock line renders the honest control, and the choke point fails loud', () => {
  const code = stripComments(readFileSync(join(here, 'main.jsx'), 'utf8'))
  const start = code.indexOf('🔒 Your plan includes')
  assert.notEqual(start, -1, 'the Graphs lock line must exist')
  const end = code.indexOf('{LIMIT_CONTACT}', start)
  assert.notEqual(end, -1, 'the lock line must keep its contact route')
  const region = code.slice(start, end)
  assert.match(region, /<UpgradeCta/, 'the lock-line CTA must render through UpgradeCta')
  assert.doesNotMatch(region, /onClick=\{upgrade\}/,
    'the raw upgrade() handler (the silent no-op) must be gone')
  assert.doesNotMatch(region, /<button/,
    'the lock-line CTA must be the shared control, never a hand-rolled button')
  assert.match(region, /label="Upgrade to add more\."/, 'the lock line keeps its copy')
  assert.match(region, /anyConfigured=\{Object\.keys\(team\?\.checkout_price_ids \|\| \{\}\)\.length > 0\}/,
    'the outage-vs-not-offered reason must come from the server catalog')
  // The target is the next PURCHASABLE plan — a derivation, not a raw id.
  assert.match(code, /const graphUpgrade = nextUpgradePlan\(planOptions\(\), team\)/,
    'the lock line must derive its target from the next purchasable plan')
  assert.match(code, /const graphUpgradePriceId = graphUpgrade/,
    'the plan must be resolved to a server price id before it reaches the control')
  // The choke point: a missing price id fails LOUD instead of returning
  // silently (the class-wide backstop — a future site that forgets the gate).
  const at = code.indexOf('async function upgradeToPrice(priceId) {')
  assert.notEqual(at, -1, 'upgradeToPrice must exist')
  const head = code.slice(at, code.indexOf('setCheckoutPending(true)', at))
  assert.match(head, /if \(!priceId\) \{/, 'a missing price id must not fall through silently')
  assert.match(head, /setError\(CHECKOUT_UNAVAILABLE_REASON\)/,
    'the would-be no-op must surface the honest reason')
  assert.match(head, /if \(checkoutPending\) return/, 'the double-click guard must survive')
})

// ── #4773: the header control's tier→price mapping ────────────────────────
// The defect: the header gated on the SINGULAR `team.checkout_price_id` — the
// server's current default, which is pro TODAY — while naming a tier in its
// label. The P2 from the #4639 review cycle: the moment that default is any
// other tier (or the header grows a per-tier control), the label and the price
// id disagree and checkout opens for a price the control never advertised.
// These tests pin the MAPPING (tier → that tier's own price id), not merely
// the presence of one id — that is what makes the disagreement impossible.
test('#4773: the header control resolves its own tier\'s price id, not the singular default', () => {
  const ids = { solo: 'price_solo', pro: 'price_pro', team: 'price_team' }
  // The advertised tier IS the resolved tier, and the price is THAT tier's id.
  const cta = headerUpgradeCta({ tier: 'free', checkout_price_id: ids.pro, checkout_price_ids: ids })
  assert.equal(cta.tier, HEADER_UPGRADE_TIER)
  assert.equal(cta.priceId, ids.pro, 'the price id must be the advertised tier\'s own')
  // The exact P2 scenario: the server's default has DRIFTED to another tier.
  // A control wired to the singular field would open team's checkout under a
  // label naming pro; the per-tier resolution must ignore the singular field
  // entirely.
  const drifted = headerUpgradeCta({ tier: 'free', checkout_price_id: ids.team, checkout_price_ids: ids })
  assert.equal(drifted.priceId, ids.pro,
    'a drifted singular default must never reach checkout')
  assert.notEqual(drifted.priceId, ids.team)
  // The mapping is per tier, not "the first id that exists": a catalog that
  // sells solo/team but NOT pro yields no header control (never solo's or
  // team's id under a pro promise).
  assert.equal(headerUpgradeCta({ tier: 'free', checkout_price_ids: { solo: 'p_solo', team: 'p_team' } }).priceId, '')
  assert.equal(headerUpgradeCta({ tier: 'anon', checkout_price_ids: { team: 'p_team' } }).priceId, '')
})

test('#4773: no price for the target tier → no control (never a dead click)', () => {
  for (const team of [
    { tier: 'free', checkout_price_ids: {} },
    { tier: 'free' },
    { tier: 'free', checkout_price_ids: { solo: 'p_solo' } },
    { tier: 'anon' },
    null,
  ]) {
    const cta = headerUpgradeCta(team)
    assert.equal(cta.priceId, '', `no resolvable id for ${JSON.stringify(team)}`)
    assert.equal(cta.tier, HEADER_UPGRADE_TIER,
      'the advertised tier is still stated, even when unobtainable')
  }
})

test('#4773 (wiring backstop): the header reads the per-tier resolution and names the advertised tier', () => {
  const code = stripComments(readFileSync(join(here, 'main.jsx'), 'utf8'))
  const start = code.indexOf('<header className="dash-header">')
  assert.notEqual(start, -1, 'the dashboard header must exist')
  const header = code.slice(start, code.indexOf('</header>', start))
  // The singular field must not decide the header's control any more: only the
  // per-tier map (whose ids are the `checkout_price_ids` plural — the negative
  // lookahead keeps that spelling from satisfying the ban).
  assert.doesNotMatch(header, /checkout_price_id(?!s)/,
    'the header control must not gate on the singular checkout_price_id')
  assert.match(code, /const headerUpgrade = headerUpgradeCta\(team\)/,
    'the header target must come from the pure per-tier resolution')
  assert.match(header, /headerUpgradeEligible\(team\)/,
    '#4639 suppression must still gate the header control')
  assert.match(header, /headerUpgrade\.priceId \?/,
    'the control must gate on the RESOLVED price id')
  assert.match(header, /onClick=\{\(\) => upgradeToPrice\(headerUpgrade\.priceId\)\}/,
    'checkout must be opened with the resolved id, never the raw field')
  assert.match(header, /Upgrade to \$\{TIER_LABELS\[headerUpgrade\.tier\]/,
    'the label must name the RESOLVED tier — the acceptance criterion\'s "tier it advertises"')
})
