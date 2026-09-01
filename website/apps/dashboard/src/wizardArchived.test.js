// wizardArchived.test.js — run with node --test. Source-scan assertions for
// the #1997 (W1) archived-not-deleted contract (DE2E-1): the legacy #1643
// wizard is NEVER rendered by the live surface, but its JSX + labels remain
// in source (A0 rollback path, epic §8). Reads main.jsx as TEXT — no React
// runtime needed.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const __dirname = dirname(fileURLToPath(import.meta.url))
const src = readFileSync(join(__dirname, 'main.jsx'), 'utf8')

test('legacy #1643 wizard render is gated behind the ARCHIVED flag (never rendered)', () => {
  // The legacy wizard's gate must be the archived flag, not the live path.
  assert.ok(/LEGACY_WIZARD_ARCHIVED\s*&&\s*welcomeOriented\s*&&/.test(src),
    'legacy wizard gated by LEGACY_WIZARD_ARCHIVED && welcomeOriented')
  assert.ok(/const LEGACY_WIZARD_ARCHIVED\s*=\s*false/.test(src),
    'LEGACY_WIZARD_ARCHIVED is false (never rendered)')
})

test('archived marker comment present (rollback drill reference)', () => {
  assert.ok(src.includes('⛔ ARCHIVED — #1997 (W1)'), 'ARCHIVED marker header')
})

test('legacy wizard labels still exist in source (archived-not-deleted)', () => {
  // the legacy labels array uses JS-escaped apostrophes (\')
  for (const label of ["Connect your tool", "Memory sources",
                       "Your agent\\'s toolkit", "Seed your graph", "You\\'re set"]) {
    assert.ok(src.includes(label), `legacy label archived: ${label}`)
  }
})

test('live wizard renders WIZARD_STEPS (the 5 human steps), not legacy labels', () => {
  assert.ok(src.includes('WIZARD_STEPS.map'), 'live wizard maps WIZARD_STEPS')
  // the legacy labels array is retained but the live title reads WIZARD_STEPS
  assert.ok(/<p className="wizard-title">\{WIZARD_STEPS\[wizardStep\]\.label\}<\/p>/.test(src),
    'live wizard title is WIZARD_STEPS[wizardStep].label')
  // the archived block's title still reads wizardSteps (kept for rollback)
  assert.ok(/wizard-title">\{wizardSteps\[wizardStep\]\}/.test(src),
    'archived legacy title retained (wizardSteps)')
})

test('DE2E-2 copy sweep: org-create dialog + wizard copy say Organization', () => {
  assert.ok(src.includes('Create a new organization'), 'create-team dialog header')
  assert.ok(src.includes('Organization name required'), 'validation error copy')
  assert.ok(src.includes('Your Organization is set up'), 'welcome ready copy')
  assert.ok(src.includes('Creating your Organization and API key'), 'provisioning copy')
  assert.ok(src.includes('Your Organization and API key are live'), 're-entry + first-data cards')
})

test('wizardComplete no longer writes onboarding_complete (accept-and-drop, plan T7)', () => {
  assert.ok(!/body:\s*JSON\.stringify\(\{\s*onboarding_complete:\s*true\s*\}\)/.test(src),
    'wizardComplete dropped the PATCH onboarding_complete write')
})
