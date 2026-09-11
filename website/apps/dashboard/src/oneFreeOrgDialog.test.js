// oneFreeOrgDialog.test.js — #2789 static tripwires (CI-run via
// dashboard-js-tests, node --test zero-dep convention, mirroring the other
// main.jsx/index.css tripwires).
//
// #2789 replaces the #1877 gate-on-submit 402 with an OWNERSHIP-based
// entitlement and a server-driven three-option dialog. These checks pin the
// WIRING, which is what regresses silently:
//   1. the account-menu item PRE-CHECKS the cap from the teams list and opens
//      the gate dialog immediately (no name typing before rejection);
//   2. the pre-check mirrors the server rule — owner (not mere member!) and
//      not a paid/active sub, with pending_payment excluded;
//   3. the dialog carries the exact issue copy and all three actions;
//   4. the dialog is selected by the SERVER's structured code
//      (`one_free_org_limit`), never by string-matching the detail — the old
//      free-cap sentence must be gone from the client;
//   5. the third action really reaches the paid-new-org checkout (session
//      scoped — the org does not exist yet) and the success return waits for
//      `?new_org=<id>` to appear before switching to it;
//   6. the #2392 a11y contract (focus return, Escape = Cancel, aria-modal,
//      autoFocus into every dialog mode) survives.
//
// Deliberately out of scope — verified by the DOM lane (#2793): computed
// styles of the dialog's fields (the #2778/#2791 contrast guard owns those)
// and real-browser focus behaviour.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const mainJsx = readFileSync(join(here, 'main.jsx'), 'utf8')
// Comment-stripped + whitespace-collapsed copy so prose/comments/reformatting
// cannot satisfy (or defeat) a structural check.
const flat = mainJsx.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/\/\/[^\n]*/g, ' ').replace(/\s+/g, ' ')

/** Body of the create-org dialog JSX (from its container to the dialog's
 *  closing marker) — the modal is the only place the three-option copy may
 *  live. Starts at the container so the role/aria-modal contract is included. */
function dialogBlock() {
  const start = flat.indexOf('role="dialog" aria-modal="true" aria-label="Create a new organization"')
  assert.notEqual(start, -1, 'the create-organization dialog must still exist')
  const end = flat.indexOf('{team && team.tier !== \'team\'', start)
  assert.notEqual(end, -1, 'the dialog block must end before the tier badge')
  return flat.slice(start, end)
}

test('#2789: the account-menu item pre-checks the cap and opens the gate dialog', () => {
  assert.match(
    flat,
    /className="account-menu-create" onClick=\{openCreateTeamDialog\}/,
    'the account-menu item must route through openCreateTeamDialog (which pre-checks the entitlement)',
  )
  const fn = flat.match(/function openCreateTeamDialog\(\) \{(.*?)\} function/)
  assert.ok(fn, 'openCreateTeamDialog must exist')
  const body = flat.slice(
    flat.indexOf('function openCreateTeamDialog()'),
    flat.indexOf('function upgradeCurrentOrg()'),
  )
  assert.ok(body.length > 0 && body.length < 4000, 'openCreateTeamDialog body must be extractable')
  // At the cap the dialog opens IN THE LIMIT MODE immediately.
  assert.match(body, /const capped = ownedFreeOrgs\[0\] \|\| null/,
    'openCreateTeamDialog must consult the client-side ownership pre-check')
  assert.match(body, /setCreateTeamMode\(capped \? 'limit' : 'name'\)/,
    'a capped user must land on the three-option limit dialog; a user under the cap on the name dialog')
  assert.match(body, /rememberRestoreTarget\(createTeamRestoreRef, accountBlobBtnRef\.current\)/,
    '#2392 focus-return: the blob trigger must be captured as the restore anchor before the menu unmounts')
})

test('#2789: the client pre-check mirrors the server rule (ownership, not membership)', () => {
  const start = flat.indexOf('const ownedFreeOrgs = (teams || []).filter')
  assert.notEqual(start, -1, 'the ownedFreeOrgs pre-check must exist')
  const rule = flat.slice(start, flat.indexOf('const hasActiveSubscription', start))
  // ownership — a collaborator on someone else's free org keeps their own slot
  assert.match(rule, /t\.role === 'owner'/,
    'the count must be OWNER memberships — counting members was the #2789 trap')
  // not on an active/paid plan
  assert.match(rule, /!ACTIVE_STATUSES\.includes\(t\.subscription_status\)/,
    'an active/paid subscription removes the org from the free count')
  // pending_payment is not real yet
  assert.match(rule, /t\.subscription_status !== 'pending_payment'/,
    'a pending_payment org must not consume the allowance (rules table)')
  // the pre-check reads the fields the teams list already carries (role/tier);
  // the server-side `subscription_status` row field is asserted in the python
  // suite (tests/test_one_free_org_entitlement.py) where /v1/teams is served.
  assert.match(flat, /const ownedFreeOrgs = \(teams \|\| \[\]\)\.filter/,
    'the pre-check must read the in-memory teams list (no extra request)',
  )
})

test('#2789: the gate dialog carries the issue copy and exactly three actions', () => {
  const dialog = dialogBlock()
  assert.match(dialog, /You can only have one free organization/,
    'the headline must be the issue copy')
  assert.match(
    dialog,
    /Individual users can create one organization\. To create another, purchase a subscription for it — or upgrade your current organization\./,
    'the body must be the issue copy (no paraphrase)',
  )
  for (const action of [
    'Cancel',
    'Upgrade current organization',
    'Purchase subscription for a new organization',
  ]) {
    assert.ok(dialog.includes(action), `the gate dialog must offer "${action}"`)
  }
  // The old copy/CTA of the #1877 gate must not survive anywhere in the client.
  assert.ok(!mainJsx.includes('The free plan includes one organization'),
    'the superseded #1877 free-cap sentence must be gone')
  assert.ok(!mainJsx.includes('Create another team requires a paid plan'),
    'the client must not string-match (or repeat) the server detail — the dialog is driven by the code')
})

test('#2789: the dialog is driven by the structured code, never by detail string-matching', () => {
  // The 402 handler selects the limit mode from the machine-readable code…
  assert.match(flat, /e\?\.code === 'one_free_org_limit'/,
    'the create handler must branch on the structured `code`')
  // …using the server's org id for the Upgrade action.
  assert.match(flat, /setCreateTeamLimitTeamId\(e\?\.teamId \|\| \(ownedFreeOrgs\[0\]\?\.team_id \?\? ''\)\)/,
    'the blocked detail\'s team_id (the owned free org) must feed the Upgrade action')
  // And api() lifts code/team_id off a dict detail.
  assert.match(flat, /if \(coded && typeof coded\.code === 'string'\) err\.code = coded\.code/,
    'api() must surface detail.code on the thrown Error')
  assert.match(flat, /if \(coded && typeof coded\.team_id === 'string'\) err\.teamId = coded\.team_id/,
    'api() must surface detail.team_id on the thrown Error')
})

test('#2789: the third action reaches the session-scoped paid-new-org checkout', () => {
  assert.match(flat, /api\('\/v1\/billing\/checkout\/new-org', \{/,
    'the purchase flow must call the new-org checkout endpoint')
  assert.match(flat, /body: JSON\.stringify\(\{ name, price_id: priceId \}\)/,
    'the new-org checkout takes the intended name + the server-resolved price id')
  assert.match(flat, /const res = await api\('\/v1\/billing\/checkout\/new-org'/,
    'the pre-minted team_id comes back from the response (it rides the success URL)')
  // The success return waits for the provisioned org, then switches to it.
  assert.match(flat, /const newOrgId = params\.get\('new_org'\)/,
    'the success-return effect must read ?new_org=<id>')
  assert.match(flat, /\.some\(\(t\) => t && t\.team_id === newOrgId\)/,
    'the poll must wait for the webhook-provisioned org to appear in /v1/teams')
})

test('#2789: plan choice in the purchase mode is server-resolved (never hardcoded ids)', () => {
  const block = flat.slice(flat.indexOf("createTeamMode === 'purchase'"))
  assert.ok(block.length > 0, 'the purchase mode must exist')
  assert.match(block, /planOptions\(\)\.filter\(\(p\) => p\.tier !== 'free' && team\.checkout_price_ids\[p\.tier\]\)/,
    'paid plans come from the pricing catalog, filtered to tiers the server has a price id for')
  // No hardcoded Stripe price id anywhere (the server resolves them; the
  // match is quoted-literal only so `checkout_price_ids` is not a false hit).
  assert.ok(!/['"]price_[A-Za-z0-9]{3,}['"]/.test(mainJsx),
    'no Stripe price id may be hardcoded in the client')
})

test('#2789: the dialog keeps the #2392 a11y contract in every mode', () => {
  const dialog = dialogBlock()
  assert.match(dialog, /role="dialog" aria-modal="true"/, 'the dialog must stay role=dialog aria-modal=true')
  assert.match(dialog, /if \(e\.key === 'Escape' && !createTeamBusy\) closeCreateTeam\(\)/,
    'Escape must cancel (while not busy)')
  // one autoFocus per mode (limit / purchase / name) = focus moves INTO the
  // dialog whichever branch renders
  const autofocusCount = (dialog.match(/autoFocus/g) || []).length
  assert.equal(autofocusCount, 3, `every dialog mode must autoFocus its primary control (found ${autofocusCount})`)
  assert.match(flat, /function closeCreateTeam\(\) \{[^}]*restoreFocus\(createTeamRestoreRef\)/,
    'every close path must restore focus to the blob trigger')
  assert.match(mainJsx, /You can only have one free organization/, 'the gate copy must be present verbatim')
})
