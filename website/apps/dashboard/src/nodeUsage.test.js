// nodeUsage.test.js — #4331. Execution tests for the node-usage derivations
// PLUS static tripwires that bind the rendered Billing surface to them.
//
// The pure half imports and RUNS ./nodeUsage.js — a behaviour-identical
// reformat cannot flip it. The defect-detection direction is pinned by the
// "unknown server number → null" cases (a hardcoded/derived allowance would
// fail them) and by the level thresholds (amber ≥80 / red 100).
//
// The static half reads main.jsx (the repo's node --test tripwire convention):
// a behaviour test cannot see JSX, so the wiring — the Billing Nodes card, the
// usage bar, and the nudge — is pinned here.
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
  nodeUsageText,
} from './nodeUsage.js'
import { stripComments } from './testSupport.js'

const here = dirname(fileURLToPath(import.meta.url))
const mainJsx = readFileSync(join(here, 'main.jsx'), 'utf8')
// Comment-stripped + whitespace-collapsed code, so prose cannot satisfy a
// structural check and reformatting cannot defeat one.
const flat = stripComments(mainJsx).replace(/\s+/g, ' ')

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
    { nodes_used: 5, max_nodes: -1 },
    { nodes_used: 'x', max_nodes: 100 },
    { nodes_used: 5, max_nodes: 'x' },
    { nodes_used: -1, max_nodes: 100 },
  ]) {
    assert.equal(nodeUsage(team), null, `must be unknown for ${JSON.stringify(team)}`)
    assert.equal(nodeNudge(team), null, 'no usage → no nudge')
  }
})

test('#4331: a stored max_nodes=0 is a REAL absolute cap, not "unknown"', () => {
  // Tests pin that `_org_limits_from_node` preserves an explicit 0 override
  // (tests/test_quota.py) and `enforce_org_limit` refuses every write at it.
  const u = nodeUsage({ nodes_used: 0, max_nodes: 0 })
  assert.deepEqual({ used: u.used, max: u.max, pct: u.pct, level: u.level },
    { used: 0, max: 0, pct: 100, level: 'at_limit' })
  assert.match(nodeNudge({ tier: 'free', nodes_used: 0, max_nodes: 0 }), /reached your node limit/i)
})

test('#4331: the displayed pct never crosses a threshold the level has not', () => {
  // 99.5% is near, NOT reached: display must not say 100% over a near bar.
  assert.equal(nodeUsage({ nodes_used: 995, max_nodes: 1000 }).pct, 99)
  assert.equal(nodeUsage({ nodes_used: 995, max_nodes: 1000 }).level, 'near')
  // 79.5% is still ok: display must not say 80% with no nudge.
  assert.equal(nodeUsage({ nodes_used: 795, max_nodes: 1000 }).pct, 79)
  assert.equal(nodeUsage({ nodes_used: 795, max_nodes: 1000 }).level, 'ok')
  assert.equal(nodeUsage({ nodes_used: 800, max_nodes: 1000 }).pct, 80)
  assert.equal(nodeUsage({ nodes_used: 800, max_nodes: 1000 }).level, 'near')
  assert.equal(nodeUsage({ nodes_used: 1000, max_nodes: 1000 }).pct, 100)
  assert.equal(nodeUsage({ nodes_used: 1000, max_nodes: 1000 }).level, 'at_limit')
})

test('#4331: nodeUsageText renders the usage, and a 0-cap as a blocked state', () => {
  assert.equal(nodeUsageText(null), null)
  assert.equal(nodeUsageText(nodeUsage({ nodes_used: 999, max_nodes: 1000 })),
    '999 / 1,000 nodes used (99%)')
  const zero = nodeUsageText(nodeUsage({ nodes_used: 0, max_nodes: 0 }))
  assert.doesNotMatch(zero, /\d+\s*\/\s*0|100%/, `0-cap must not render a ratio: ${zero}`)
  assert.match(zero, /limit reached/i, zero)
})

// ── 2. Colour thresholds ─────────────────────────────────────────────────

test('#4331: bar colour is accent < 80%, amber ≥ 80%, red at 100%', () => {
  assert.equal(nodeBarColor('ok'), 'var(--accent, #06b6d4)')
  assert.match(nodeBarColor('near'), /var\(--amber, #fbbf24\)/, 'amber at ≥80%')
  assert.match(nodeBarColor('at_limit'), /#f87171/, 'red at 100%')
})

// ── 3. The nudge ────────────────────────────────────────────────────────

test('#4331: the nudge is silent below 80% and fires at/above it', () => {
  assert.equal(nodeNudge({ tier: 'free', nodes_used: 79, max_nodes: 100 }), null)
  assert.match(nodeNudge({ tier: 'free', nodes_used: 80, max_nodes: 100 }), /upgrade to keep writing/i)
  assert.match(nodeNudge({ tier: 'free', nodes_used: 100, max_nodes: 100 }), /reached your node limit/i)
})

test('#4331: free vs paid nudges say different things', () => {
  const free = nodeNudge({ tier: 'free', nodes_used: 80, max_nodes: 100 }, true)
  const paid = nodeNudge({ tier: 'pro', nodes_used: 80, max_nodes: 100 }, true)
  assert.match(free, /upgrade to keep writing/i)
  assert.match(paid, /near your node allowance/i)
  assert.match(paid, /upgrade for a higher allowance/i)
  assert.notEqual(free, paid)
  // anon is the internal free tier — it must read as free.
  assert.equal(nodeNudge({ tier: 'anon', nodes_used: 80, max_nodes: 100 }, true), free)
})

test('#4331: no purchasable upgrade → the nudge states the cap, never promises an upgrade', () => {
  for (const tier of ['team', 'pro', 'free']) {
    for (const used of [80, 100]) {
      const hint = nodeNudge({ tier, nodes_used: used, max_nodes: 100 }, false)
      assert.doesNotMatch(hint, /upgrade/i,
        `must not promise an upgrade that cannot be bought: ${hint}`)
      assert.match(hint, /node (limit|allowance)/i, hint)
    }
  }
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
  // An unrecognised PAID tier must never fall back to the lowest plan (that
  // would sell a downgrade as an "upgrade").
  assert.equal(
    nextUpgradePlan(PLANS, { tier: 'enterprise', checkout_price_ids: IDS }),
    null,
  )
})

// ── 5. Wiring: the Billing card ─────────────────────────────────────────

test('#4331: the Billing card shows nodes_used / max_nodes and never fabricates a 0', () => {
  assert.match(flat, /Nodes used/, 'the Billing stats row must have a Nodes card')
  assert.match(flat, /nodeState \? nodeState\.used\.toLocaleString\(\) : '—'/,
    'the card value must be suppressed (not 0) when the count is unknown')
  assert.match(flat, /nodeState && nodeState\.max > 0 \? ` \/ \$\{nodeState\.max\.toLocaleString\(\)\}`/,
    'the card label must show the server max_nodes (and no ratio for a 0-cap)')
  assert.match(flat, /team\.graph_ready !== false \? nodeUsage\(team\) : null/,
    'an unreadable graph must make the node figure unknown, not zero')
  assert.match(flat, /nodeUsageText\(nodeState\)/, 'the bar caption comes from the shared helper')
  // The sibling Memories card must not fabricate a 0 on a broken graph either.
  assert.match(flat, /team\.graph_ready === false \? '—' : \(team\.point_count \?\? 0\)/,
    'the Memories card must mirror the node card\'s uncertainty')
})

test('#4331: the node bar colour is graduated (accent < 80%, amber ≥ 80%, red at 100%)', () => {
  assert.match(flat, /background: nodeBarColor\(nodeState\.level\)/,
    'the bar colour must come from the level derivation')
  assert.match(flat, /width: `\$\{nodeState\.pct\}%`/, 'the bar width must be the capped pct')
})

test('#4331: the nudge copy is purchasability-aware and never promises an unbuyable upgrade', () => {
  assert.match(flat, /nodeNudge\(team, Boolean\(nodeNext\)\)/,
    'the nudge must know whether a purchasable upgrade exists')
})

test('#4331: the nudge remedy matches the plan cards — portal for Stripe customers, checkout otherwise', () => {
  assert.match(flat, /nodeHint && \(/, 'the nudge must render when nodeHint fires')
  // The CTA is inside a canManageSubscription ? portal : nodeNext ? checkout
  // : null chain — an active subscriber must never get the 409-ing checkout.
  const start = flat.indexOf('{nodeHint && (')
  assert.notEqual(start, -1, 'the nudge block must exist')
  const nudge = flat.slice(start, start + 1400)
  assert.match(nudge, /canManageSubscription \? \(/, 'Stripe customers get the portal path')
  assert.match(nudge, /onClick=\{manageBilling\}/, 'the portal control manages the subscription')
  assert.match(nudge, /: nodeNext \? \(/, 'only a non-managed team gets the checkout CTA')
  assert.match(nudge, /Upgrade to \$\{nodeNext\.label\}/, 'the checkout CTA names the next plan')
})
