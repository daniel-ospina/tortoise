// membersInviteUpsell.test.js — #4392. The Members-tab invite upsell must not
// route a ready buyer to the marketing page.
//
// THE DEFECT: at the invite gate (the notice renders only outside pro/team,
// #1875) the notice's ONLY affordance was
//
//   <a href="https://tortoise.premiselabs.co/product.html#pricing">upgrade to add members</a>
//
// — a brochure, and a DEAD one: that host 301s `product.html` to `/`, so
// `#pricing` was never reached. That is the finding that made #4335 re-pin
// COMPARE_PLANS_URL to `#pricing-section`. #4335 fixed the three billing CTAs
// and #4331 the header badge; this Members notice was outside both scopes.
//
// THE FIX routes in-product: the control switches to the Billing tab — the
// surface that owns plan management and states the truth in every state (a
// real checkout on the Builder/Team card, the disabled control carrying the
// reason during a catalog outage, the portal for a PAYING Solo org, where
// checkout 409s). It deliberately names no checkout target: the next
// purchasable step up from Free is Solo, whose collaborator cap is 1 — buying
// it leaves the gate shut, while the plan grid prints each tier's cap.
//
// WHAT THESE GUARDS CATCH — each is a one-line mutation that fails one:
//   * `onClick={() => setTab('billing')}` → `() => {}` / `'overview'`: the
//     EXECUTED routing test fails (a label with no working handler);
//   * the notice DELETED wholesale (the way a marketing link is "removed"):
//     extractOne throws — a guard satisfied by deleting the site is not one;
//   * the marketing anchor re-imported anywhere in the dashboard: the residual
//     scan fails — that IS #4392 returning;
//   * the tier claim drifting from the pricing it is a claim about (a new
//     tier that lifts the cap, or Solo's cap raised): the parity check fails.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { stripComments } from './testSupport.js'
import { extractOne, evalExpressions } from './jsxSourceProbe.js'

const here = dirname(fileURLToPath(import.meta.url))
const CODE = stripComments(readFileSync(join(here, 'main.jsx'), 'utf8'))

// The dashboard's tier display names, read from the module the app renders
// them from. Read as SOURCE, not imported: pricing.js imports pricing.json,
// which node's ESM loader refuses without an import attribute — the same
// reason limitContact.test.js reads the Python seam out of its source.
const TIER_LABELS = Object.fromEntries(
  [...extractGroup(readFileSync(join(here, 'pricing.js'), 'utf8'),
    /export const TIER_LABELS = \{([^}]*)\}/, '#4392 the dashboard tier labels')
    .matchAll(/(\w+):\s*'([^']+)'/g)].map((m) => [m[1], m[2]]))

// The notice PARAGRAPH, pinned as a SITE (not just as copy): `inviteMember()`
// carries the same sentence in its 402 error path, so a copy-only anchor would
// be satisfied by a string in the error handler rather than by the rendered
// notice (extractOne requires exactly one match).
const NOTICE = extractOne(CODE, /<p className="dim small">Invites require[^]*?<\/p>/,
  '#4392 the Members-tab invite notice')
// The notice's control, anchored on its handler so a stray label is not
// mistaken for the control.
const CONTROL = /<button[^>]*\sonClick=\{([^}]*)\}/

/** The ONE capture group of the single match of `re` in `src` (or throw). */
function extractGroup(src, re, label) {
  return extractOne(src, re, label).match(re)[1]
}

test('#4392: the Members-tab invite upsell lands on the Billing tab (EXECUTED)', async () => {
  const calls = []
  globalThis.__membersInviteSetTab = (v) => calls.push(v)
  const onClick = extractGroup(NOTICE, CONTROL, '#4392 the invite upsell control')
  const [{ value: handler }] = await evalExpressions([`(${onClick})`], {
    bindings: { setTab: 'globalThis.__membersInviteSetTab' },
  })
  assert.equal(typeof handler, 'function', 'the upsell control must be wired')
  handler()
  assert.deepEqual(calls, ['billing'],
    'a ready buyer at the invite gate must reach the in-product plan surface')
})

test('#4392: no dashboard surface routes a buyer to the marketing page', () => {
  // product.html is a 301 to '/' on that host, so any link to it is both an
  // off-product CTA (#4335's target: 0 marketing-link CTAs) and a dead
  // destination. The canonical, test-pinned marketing destination is
  // COMPARE_PLANS_URL (`#pricing-section`), rendered only as the secondary,
  // explicitly-labelled "Compare plans" link.
  assert.doesNotMatch(CODE, /product\.html/,
    'the dashboard must not link the marketing page (#4392 regression)')
  // …and the notice itself is in-product only — never an anchor off-product.
  assert.doesNotMatch(NOTICE, /<a\b/,
    'the invite notice must not be an anchor')
})

test('#4392: the notice names exactly the tiers whose plan lifts the member cap', () => {
  const pricing = JSON.parse(readFileSync(
    join(here, '..', '..', '..', '..', 'product', 'pricing.json'), 'utf8'))
  // The tiers that can hold more than the owner: max_users_per_team > 1 (null
  // = unlimited). This is the fact that decides whether a purchase UNBLOCKS
  // the invite gate — and the reason the notice's control is a trip to the
  // plan grid rather than a checkout for "the next tier up" (Solo, cap 1).
  const lifting = Object.entries(pricing.tiers)
    .filter(([tier, t]) => tier !== 'free' && tier !== 'anon'
      && (t.max_users_per_team == null || t.max_users_per_team > 1))
    .map(([tier]) => tier)
  assert.ok(lifting.length > 0, 'pricing must sell a tier that lifts the member cap')

  const named = extractGroup(NOTICE, /Invites require the ([^<{—]*)/,
    '#4392 the invite notice tier claim')
  for (const tier of lifting) {
    assert.match(named, new RegExp(`\\b${TIER_LABELS[tier]}\\b`),
      `the notice must name ${TIER_LABELS[tier]} — max_users_per_team ${pricing.tiers[tier].max_users_per_team}`)
  }
  // A tier that cannot lift the cap must never be named as the requirement.
  for (const [tier, t] of Object.entries(pricing.tiers)) {
    if (lifting.includes(tier) || tier === 'free' || tier === 'anon') continue
    assert.doesNotMatch(named, new RegExp(`\\b${TIER_LABELS[tier]}\\b`),
      `the notice must not claim ${TIER_LABELS[tier]} lifts the member cap (max_users_per_team ${t.max_users_per_team})`)
  }
})
