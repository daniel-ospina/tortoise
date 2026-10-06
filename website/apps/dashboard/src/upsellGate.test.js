// upsellGate.test.js — #4639. EXECUTION tests for the paid-tier suppression and
// the narrowed upgrade-nudge gate, plus a source-scan pin on the two header /
// banner regions (`main.jsx` has no React runtime harness, so the render gate
// is pinned as source text — mirroring keyConfirmDisclosureTripwire).
//
// These import and RUN the real functions (./upsellGate.js), so a
// behaviour-identical reformat of the module cannot flip them. The
// defect-detection direction is pinned by the cases that FAIL against the
// pre-#4639 implementation: a paid Solo team was eligible for the header
// upsell, and the banner nudged on a billing/portal 404 whose detail merely
// contained "checkout".
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import {
  errorCode,
  errorMessage,
  errorStatus,
  headerUpgradeEligible,
  isPaidTeam,
  normalizeError,
  nudgeRoute,
  shouldNudgeUpgrade,
} from './upsellGate.js'

const mainJsx = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'main.jsx'), 'utf8')

// ── 1. Paid-tier suppression (RED against the pre-#4639 header) ───────────
test('#4639: a PAID tier is never eligible for a header upsell', () => {
  const paid = [
    { tier: 'solo', subscription_status: 'active' },
    { tier: 'pro', subscription_status: 'trialing' },
    { tier: 'team', subscription_status: 'active' },
    // past_due / canceled / unpaid are STILL paid — a lapsed customer is not a
    // prospect to be sold to from the header (the owner's enumeration).
    { tier: 'solo', subscription_status: 'past_due' },
    { tier: 'solo', subscription_status: 'canceled' },
    { tier: 'pro', subscription_status: 'unpaid' },
    // fail-safe: a paid TIER with an absent or unrecognized status stays paid
    { tier: 'solo' },
    { tier: 'team', subscription_status: 'something-new' },
    // fail-safe: a paying customer whose tier has not caught up is still paid
    { tier: 'free', subscription_status: 'active' },
  ]
  for (const team of paid) {
    assert.equal(isPaidTeam(team), true,
      `${team.tier}/${team.subscription_status} must read as paid`)
    assert.equal(headerUpgradeEligible(team), false,
      `${team.tier} must NOT be sold to from the header`)
  }
})

test('#4639: free/anon keep a header upgrade path', () => {
  assert.equal(headerUpgradeEligible({ tier: 'free', subscription_status: null }), true)
  assert.equal(headerUpgradeEligible({ tier: 'free' }), true)
  assert.equal(headerUpgradeEligible({ tier: 'anon' }), true)
  assert.equal(headerUpgradeEligible({ tier: 'anon', subscription_status: 'never' }), true)
  assert.equal(isPaidTeam({ tier: 'anon', subscription_status: 'never' }), false)
})

test('#4639: no team yet → no header control at all', () => {
  assert.equal(headerUpgradeEligible(null), false)
  assert.equal(headerUpgradeEligible(undefined), false)
  assert.equal(isPaidTeam(null), false)
})

// ── 1b. The limit nudge must offer a route that actually works ────────────
test('#4639: the limit nudge routes to checkout for a buyer, the portal for a subscriber, nowhere otherwise', () => {
  // Free/anon with a server-resolved price → start the purchase.
  assert.equal(nudgeRoute({ tier: 'free', checkout_price_id: 'price_x' }), 'checkout')
  assert.equal(nudgeRoute({ tier: 'anon', checkout_price_id: 'price_x' }), 'checkout')
  // An existing Stripe customer → the portal: checkout 409s on an active
  // subscription, so a paid subscriber's nudge must not send them there.
  for (const status of ['active', 'trialing', 'past_due', 'canceled', 'unpaid']) {
    assert.equal(nudgeRoute({ tier: 'solo', subscription_status: status, checkout_price_id: 'price_x' }), 'portal',
      `${status} must route to the portal, not checkout`)
  }
  // No price and no customer → no route → the nudge is not rendered (never a
  // dead button on a deployment without a Stripe catalog).
  assert.equal(nudgeRoute({ tier: 'free' }), null)
  assert.equal(nudgeRoute({ tier: 'solo' }), 'portal',
    'a paid tier with absent status is a payer — portal, never a checkout that 409s')
  assert.equal(nudgeRoute(null), null)
  // The paid-STATUS branch independent of a paid tier: a customer whose tier
  // has not caught up must reach the portal, never the checkout that 409s.
  assert.equal(nudgeRoute({ tier: 'free', subscription_status: 'active', checkout_price_id: 'price_x' }), 'portal')
  assert.equal(nudgeRoute({ tier: 'anon', subscription_status: 'past_due', checkout_price_id: 'price_x' }), 'portal')
  // …and with no price id at all, the status alone still selects the portal.
  assert.equal(nudgeRoute({ tier: 'free', subscription_status: 'canceled' }), 'portal')
})

// ── 2. The narrowed nudge gate ────────────────────────────────────────────
test('#4639: our own billing failure reads as a defect, never an upsell', () => {
  // The exact reported flow: POST /v1/billing/portal 404 whose detail contains
  // "checkout" — the pre-fix regex matched "checkout" and appended "Upgrade plan".
  const portal404 = Object.assign(
    new Error('no Stripe customer for this team — start a checkout first'),
    { status: 404 })
  assert.equal(shouldNudgeUpgrade(portal404), false)

  for (const msg of [
    'no Stripe customer for this team — start a checkout first',
    'Billing is temporarily unavailable — try again.',
    'Could not start checkout',
    'billing error',
    'Upgrade plan',
    // Bare "limit" words are NOT a refusal phrase — the signal is pinned to
    // "limit reached/exceeded", so a walk-back to the broad /limit/ regex
    // would nudge these SDK validation strings.
    'limit must be >= 1, got 0',
    'list_batches: limit must be a positive int',
    'Graph limit is 10 — you have 3',
  ]) {
    assert.equal(shouldNudgeUpgrade(msg), false, `"${msg}" must not nudge`)
  }

  // A 500 from our OWN quota plumbing — "Quota check failed" names a DEFECT,
  // not a limit the user can pay past.
  assert.equal(shouldNudgeUpgrade(Object.assign(
    new Error('Quota check failed: connection reset'), { status: 500 })), false)

  // Throttling is not a plan limit — an upgrade does not relieve it.
  assert.equal(shouldNudgeUpgrade(Object.assign(
    new Error('Rate limit exceeded for write. Please try again later.'), { status: 429 })), false)
  assert.equal(shouldNudgeUpgrade('Rate limit exceeded'), false)
})

test('#4639: a genuine limit refusal still offers the upgrade', () => {
  // The canonical structured 402 (tortoise/quota.py QuotaExceededError shape).
  assert.equal(shouldNudgeUpgrade(Object.assign(
    new Error('Team api_keys limit reached (2). Upgrade your plan to increase it.'),
    { status: 402 })), true)
  // A 402 whose detail carries no "limit" phrase (budget exhausted) still
  // qualifies on the STRUCTURED status.
  assert.equal(shouldNudgeUpgrade(Object.assign(
    new Error('Extraction budget exhausted for this period'), { status: 402 })), true)
  // #4614: a 402 whose `code` names a SPEND cap an upgrade cannot lift must
  // NEVER produce an upsell — while the canonical plan-limit code still does.
  assert.equal(shouldNudgeUpgrade(Object.assign(
    new Error('Cohort LLM spend cap reached'),
    { status: 402, code: 'cohort_cost_cap' })), false)
  assert.equal(shouldNudgeUpgrade(Object.assign(
    new Error('Team points limit reached (25000). Upgrade your plan.'),
    { status: 402, code: 'quota_exceeded' })), true)
  // Legacy string path (client-side 402 copy, no status threaded).
  assert.equal(shouldNudgeUpgrade('Graph limit reached for this tier — upgrade to add more graphs.'), true)
  assert.equal(shouldNudgeUpgrade('Graph limit reached — delete a graph or upgrade.'), true)
  assert.equal(shouldNudgeUpgrade('Team points limit reached: 10 in use'), true)
  // A 409 graph/key cap names a limit the upgrade relieves.
  assert.equal(shouldNudgeUpgrade(Object.assign(
    new Error('Graph limit reached — delete a graph or upgrade.'), { status: 409 })), true)
})

test('#4639: error normalization reads Error objects, {message,status,code}, and strings', () => {
  assert.deepEqual(normalizeError(Object.assign(new Error('boom'), { status: 404 })),
    { message: 'boom', status: 404, code: null })
  assert.deepEqual(normalizeError({ message: 'limit reached', status: 402 }),
    { message: 'limit reached', status: 402, code: null })
  assert.deepEqual(normalizeError('plain'), { message: 'plain', status: null, code: null })
  assert.deepEqual(normalizeError(null), { message: '', status: null, code: null })
  // #4614: the machine-readable refusal category survives normalization.
  assert.deepEqual(normalizeError(Object.assign(
    new Error('x'), { status: 402, code: 'quota_exceeded' })),
  { message: 'x', status: 402, code: 'quota_exceeded' })
  assert.equal(errorMessage(undefined), '')
  assert.equal(errorStatus('plain'), null)
  assert.equal(errorStatus({ status: '402' }), null) // a string status is not a status
  assert.equal(errorCode({ code: 'quota_exceeded' }), 'quota_exceeded')
  assert.equal(errorCode({ code: 402 }), null) // a numeric code is not a code
  assert.equal(errorCode('plain'), null)
})

// ── 3. Render-region pins (main.jsx has no runtime harness) ───────────────
test('#4639: the header region carries no marketing link and no manage button', () => {
  const start = mainJsx.indexOf('<header className="dash-header">')
  assert.notEqual(start, -1, 'the dashboard header must exist')
  const header = mainJsx.slice(start, mainJsx.indexOf('</header>', start))
  assert.doesNotMatch(header, /product\.html#pricing/,
    'the header must never link the marketing pricing page as a CTA')
  assert.doesNotMatch(header, /Manage subscription/,
    'the redundant header plan-management button must stay removed')
  assert.doesNotMatch(header, /tier !== 'team'/,
    'the old "any non-team tier upsells" condition must stay gone')
  assert.match(header, /headerUpgradeEligible\(team\)/,
    'the header upsell must gate on the paid-tier suppression')
  assert.match(mainJsx, /from '\.\/upsellGate\.js'/,
    'main.jsx must consume the pure upsellGate derivations')
})

// A JSX branch's `<span>…</span>` body, starting from its guard marker — so
// co-occurrence (gate + handler + copy) is asserted WITHIN one branch, not
// banner-wide (a banner-wide `match` passes when one branch loses its gate and
// the other keeps it).
function branchSpan(source, marker) {
  const at = source.indexOf(marker)
  assert.notEqual(at, -1, `missing banner branch: ${marker}`)
  // Back up to the branch's own line: the gate (`shouldNudgeUpgrade(error) &&`)
  // sits on the line immediately above the route marker.
  const from = source.lastIndexOf('\n', at) + 1
  const close = source.indexOf('</span>', at)
  assert.notEqual(close, -1, `missing </span> after ${marker}`)
  return source.slice(from, close)
}

test('#4639: the error banner reads the narrowed gate, not the old broad regex', () => {
  // Anchored on the `{error && (` gate — the suspended-team banner above also
  // carries className="error banner".
  const start = mainJsx.indexOf('{error && (')
  assert.notEqual(start, -1, 'the error banner must exist')
  const banner = mainJsx.slice(start, mainJsx.indexOf('</div>', start))
  assert.doesNotMatch(mainJsx, /402\|upgrade\|quota\|limit\|checkout\|billing/,
    'the broad error regex (the defect) must stay deleted')
  // BOTH branches must read the narrowed gate (a banner-wide match passes when
  // one branch loses its gate and the other keeps it).
  assert.equal((banner.match(/shouldNudgeUpgrade\(error\)/g) || []).length, 2,
    'both nudge branches must read the limited-refusal gate')
  // Checkout branch: the gate, the checkout handler, and the upgrade copy must
  // CO-OCCUR inside it.
  const checkoutSpan = branchSpan(banner, "limitNudgeRoute === 'checkout'")
  assert.match(checkoutSpan, /shouldNudgeUpgrade\(error\)/)
  assert.match(checkoutSpan, /onClick=\{upgrade\}/, 'the checkout route must call upgrade()')
  assert.match(checkoutSpan, /Upgrade plan/)
  // Portal branch: same, bound to the portal handler + copy (an active
  // subscriber must never be sent to the checkout that 409s).
  const portalSpan = branchSpan(banner, "limitNudgeRoute === 'portal'")
  assert.match(portalSpan, /shouldNudgeUpgrade\(error\)/)
  assert.match(portalSpan, /onClick=\{manageBilling\}/, 'the portal route must call manageBilling()')
  assert.match(portalSpan, /Manage subscription/)
  assert.match(mainJsx, /const limitNudgeRoute = nudgeRoute\(team\)/,
    'the nudge route must be derived from the team')
})

test('#4639: CapNotice shares the route derivation — a subscriber is never sent to checkout', () => {
  const start = mainJsx.indexOf('function CapNotice(')
  assert.notEqual(start, -1, 'CapNotice must exist')
  const cap = mainJsx.slice(start, mainJsx.indexOf('\n}', start))
  // Bind each handler to its OWN arm (a body-wide match passes when the two
  // handlers are swapped — the exact defect this PR prevents).
  // #4335 merged in: the non-portal arm is the honest UpgradeCta (a real
  // checkout control, a DISABLED control on a catalog outage, or nothing) —
  // never a marketing link, so there is no 'See pricing' fallback any more.
  const portalArm = cap.slice(cap.indexOf("route === 'portal'"), cap.indexOf('target ?'))
  assert.match(portalArm, /onClick=\{onManage\}/, 'the portal arm must call onManage')
  assert.match(portalArm, /Manage subscription/, 'the portal arm must offer Manage subscription')
  assert.doesNotMatch(portalArm, /onUpgrade/, 'the portal arm must never call the checkout handler')
  const checkoutArm = cap.slice(cap.indexOf('target ?'), cap.indexOf('Compare plans'))
  assert.match(checkoutArm, /<UpgradeCta/, 'the checkout arm must render the honest upgrade control')
  assert.match(checkoutArm, /onUpgrade/, 'the checkout arm must call onUpgrade')
  assert.doesNotMatch(cap, /product\.html#pricing/,
    '#4335: a marketing link is never the cap-notice CTA')
  assert.doesNotMatch(cap, /team\?\.checkout_price_id/,
    'the raw price-id gate must not decide the CapNotice route')
  // Both call sites pass the shared derivation AND the correct handlers — a
  // swapped/removed handler prop at the call site must fail here.
  for (const site of [
    mainJsx.slice(mainJsx.indexOf('<CapNotice text={keyModalCapNotice}'),
      mainJsx.indexOf('/>', mainJsx.indexOf('<CapNotice text={keyModalCapNotice}'))),
    mainJsx.slice(mainJsx.indexOf('<CapNotice text={capNotice}'),
      mainJsx.indexOf('/>', mainJsx.indexOf('<CapNotice text={capNotice}'))),
  ]) {
    assert.match(site, /route=\{nudgeRoute\(team\)\}/, 'each call site must pass the shared route')
    assert.match(site, /onUpgrade=\{upgradeToPrice\}/, 'each call site must wire onUpgrade to upgradeToPrice()')
    assert.match(site, /onManage=\{manageBilling\}/, 'each call site must wire onManage to manageBilling()')
    assert.match(site, /billingPending=\{billingPending\}/, 'each call site must pass billingPending')
  }
})
