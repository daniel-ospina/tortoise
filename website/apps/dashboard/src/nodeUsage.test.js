// nodeUsage.test.js — #4331. Execution tests for the node-usage derivations
// PLUS static tripwires that bind the rendered Billing surface to them.
//
// The pure half imports and RUNS ./nodeUsage.js — a behaviour-identical
// reformat cannot flip it. The defect-detection direction is pinned by the
// "unknown server number → null" cases (a hardcoded/derived allowance would
// fail them) and by the level thresholds (amber ≥80 / red 100).
//
// The static half reads main.jsx (the repo's node --test tripwire convention):
// a behaviour test cannot see JSX, so the wiring — Nodes card, progress bar,
// nudge, and the header upgrade control reaching Stripe instead of the
// marketing page — is pinned here.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import {
  NODE_NUDGE_PCT,
  nextUpgradePlan,
  nodeBarColor,
  nodeNudge,
  nodeUsage,
} from './nodeUsage.js'
import { stripComments } from './testSupport.js'

const here = dirname(fileURLToPath(import.meta.url))
const mainJsx = readFileSync(join(here, 'main.jsx'), 'utf8')
// Comment-stripped + whitespace-collapsed code, so prose cannot satisfy a
// structural check and reformatting cannot defeat one.
const flat = stripComments(mainJsx).replace(/\s+/g, ' ')

// The header element (the tier-badge upgrade cluster lives here).
const headerStart = flat.indexOf('<header className="dash-header">')
assert.notEqual(headerStart, -1, 'main.jsx must still render the dashboard header')
const headerEnd = flat.indexOf('</header>', headerStart)
assert.notEqual(headerEnd, -1, 'the dashboard header must close')
const header = flat.slice(headerStart, headerEnd)

// ── 1. nodeUsage — the server pair, or silence ───────────────────────────

test('#4331: nodeUsage reads the server fields and computes pct/level', () => {
  const u = nodeUsage({ nodes_used: 8, max_nodes: 10 })
  assert.deepEqual(
    { used: u.used, max: u.max, pct: u.pct, level: u.level },
    { used: 8, max: 10, pct: 80, level: 'near' },
  )
  assert.equal(nodeUsage({ nodes_used: 1, max_nodes: 10 }).level, 'ok')
  assert.equal(nodeUsage({ nodes_used: 79, max_nodes: 100 }).level, 'ok')
  assert.equal(nodeUsage({ nodes_used: 99, max_nodes: 100 }).level, 'near')
  assert.equal(nodeUsage({ nodes_used: 100, max_nodes: 100 }).level, 'at_limit')
  // Over the cap is still capped at 100% — never a bar wider than its track.
  assert.equal(nodeUsage({ nodes_used: 250, max_nodes: 100 }).pct, 100)
  assert.equal(nodeUsage({ nodes_used: 250, max_nodes: 100 }).level, 'at_limit')
  assert.equal(NODE_NUDGE_PCT, 80)
})

test('#4331: a missing/junk server number renders NOTHING, never a fabricated allowance', () => {
  for (const team of [
    null, undefined, {},
    { nodes_used: 5 }, { max_nodes: 100 },
    { nodes_used: null, max_nodes: 100 },
    { nodes_used: 5, max_nodes: null },
    { nodes_used: 5, max_nodes: 0 },
    { nodes_used: 5, max_nodes: -1 },
    { nodes_used: 'x', max_nodes: 100 },
    { nodes_used: 5, max_nodes: 'x' },
    { nodes_used: -1, max_nodes: 100 },
  ]) {
    assert.equal(nodeUsage(team), null, `must be unknown for ${JSON.stringify(team)}`)
    assert.equal(nodeNudge(team), null, 'no usage → no nudge')
  }
})

// ── 2. Colour thresholds ─────────────────────────────────────────────────

test('#4331: bar colour is accent < 80%, amber ≥ 80%, red at 100%', () => {
  assert.equal(nodeBarColor('ok'), 'var(--accent, #06b6d4)')
  assert.match(nodeBarColor('near'), /#f59e0b/, 'amber at ≥80%')
  assert.match(nodeBarColor('at_limit'), /#f87171/, 'red at 100%')
})

// ── 3. The nudge ────────────────────────────────────────────────────────

test('#4331: the nudge is silent below 80% and fires at/above it', () => {
  assert.equal(nodeNudge({ tier: 'free', nodes_used: 79, max_nodes: 100 }), null)
  assert.match(nodeNudge({ tier: 'free', nodes_used: 80, max_nodes: 100 }), /upgrade to keep writing/i)
  assert.match(nodeNudge({ tier: 'free', nodes_used: 100, max_nodes: 100 }), /reached your node limit/i)
})

test('#4331: free vs paid nudges say different things', () => {
  const free = nodeNudge({ tier: 'free', nodes_used: 80, max_nodes: 100 })
  const paid = nodeNudge({ tier: 'pro', nodes_used: 80, max_nodes: 100 })
  assert.match(free, /upgrade to keep writing/i)
  assert.match(paid, /near your node allowance — upgrade for a higher allowance/i)
  assert.notEqual(free, paid)
  // anon is the internal free tier — it must read as free.
  assert.equal(nodeNudge({ tier: 'anon', nodes_used: 80, max_nodes: 100 }), free)
})

test('#4331: the paid nudge promises no chargeable node overage (none is implemented)', () => {
  for (const tier of ['solo', 'pro', 'team']) {
    for (const used of [80, 100]) {
      const hint = nodeNudge({ tier, nodes_used: used, max_nodes: 100 })
      assert.doesNotMatch(hint, /\$|overage|per 10k|charged/i,
        `no price/overage promise may appear: ${hint}`)
    }
  }
})

// ── 4. The next purchasable plan ────────────────────────────────────────

const PLANS = [
  { tier: 'free', label: 'Free', price: 0 },
  { tier: 'solo', label: 'Solo', price: 9 },
  { tier: 'pro', label: 'Pro', price: 25 },
  { tier: 'team', label: 'Team', price: 149 },
]
const IDS = { solo: 'price_solo', pro: 'price_pro', team: 'price_team' }

test('#4331: the header upgrade control targets the NEXT tier, not the top one', () => {
  const team = (tier, ids = IDS) => ({ tier, checkout_price_ids: ids })
  assert.equal(nextUpgradePlan(PLANS, team('free')).tier, 'solo')
  assert.equal(nextUpgradePlan(PLANS, team('solo')).tier, 'pro')
  assert.equal(nextUpgradePlan(PLANS, team('pro')).tier, 'team')
  // anon is the internal free tier.
  assert.equal(nextUpgradePlan(PLANS, team('anon')).tier, 'solo')
})

test('#4331: no purchasable step up → null (the honest no-price-id case)', () => {
  assert.equal(nextUpgradePlan(PLANS, { tier: 'team', checkout_price_ids: IDS }), null)
  assert.equal(nextUpgradePlan(PLANS, { tier: 'free', checkout_price_ids: {} }), null)
  assert.equal(nextUpgradePlan(PLANS, { tier: 'free' }), null)
  assert.equal(nextUpgradePlan([], { tier: 'free', checkout_price_ids: IDS }), null)
  // A missing tier in the catalog is skipped, not invented.
  assert.equal(
    nextUpgradePlan(PLANS, { tier: 'free', checkout_price_ids: { pro: 'price_pro' } }).tier,
    'pro',
  )
})

// ── 5. Wiring: the Billing card ─────────────────────────────────────────

test('#4331: the Billing card shows nodes_used / max_nodes', () => {
  assert.match(flat, /Nodes used/, 'the Billing stats row must have a Nodes card')
  assert.match(flat, /team\.nodes_used/, 'the card value must read the server nodes_used')
  assert.match(flat, /nodeState \? ` \/ \$\{nodeState\.max\.toLocaleString\(\)\}`/,
    'the card label must show the server max_nodes')
})

test('#4331: the node bar shares the write-ops treatment with amber/red thresholds', () => {
  assert.match(flat, /background: nodeBarColor\(nodeState\.level\)/,
    'the bar colour must come from the level derivation')
  assert.match(flat, /width: `\$\{nodeState\.pct\}%`/, 'the bar width must be the capped pct')
})

test('#4331: the at/near-limit nudge renders with an upgrade CTA', () => {
  assert.match(flat, /nodeHint && \(/, 'the nudge must render when nodeHint fires')
  assert.match(flat, /Upgrade to \$\{nodeNext\.label\}/, 'the nudge CTA names the next plan')
})

// ── 6. Wiring: the header upgrade control ───────────────────────────────

test('#4331: the header tier badge opens Stripe, not the marketing page', () => {
  assert.doesNotMatch(header, /product\.html#pricing/,
    'the header must no longer link to the marketing pricing page')
  assert.doesNotMatch(header, /className="tier-badge" href=/,
    'the header tier badge must be a control, not a bare anchor')
  assert.match(header, /upgradeToPrice\(team\.checkout_price_ids\[nodeNext\.tier\]\)/,
    'the header control must open checkout with the next tier price id')
  assert.match(header, /Upgrade to \$\{nodeNext\.label\} · \$\$\{nodeNext\.price\}\/mo/,
    'the header control must show the next tier name and price')
})

test('#4331: the header control degrades honestly when no price id exists', () => {
  assert.match(header, /Upgrade — see plans/, 'no purchasable plan → a truthful label')
  assert.match(header, /onClick=\{\(\) => setTab\('billing'\)\}/,
    'the fallback must route to the in-product Billing tab')
  assert.match(header, /Compare plans/, 'the full comparison lives in the Billing tab')
})

test('#4331: an active subscriber keeps the portal path, not a checkout control', () => {
  assert.match(header, /team\.tier !== 'team' && !canManageSubscription/,
    'checkout must not be offered where the subscription is managed via the portal')
  assert.match(header, /canManageSubscription && \(/, 'the Manage-subscription control remains')
})
