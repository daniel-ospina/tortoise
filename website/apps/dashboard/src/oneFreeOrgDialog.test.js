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
 *  live. Starts at the modal's aria-labelledby expression (the dialog's own
 *  identity since #2789 dropped the static aria-label), so the role/aria-modal
 *  contract is included. */
function dialogBlock() {
  const start = flat.indexOf('role="dialog" aria-modal="true" aria-labelledby={createTeamMode')
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
  const rule = flat.slice(start, flat.indexOf('const newOrgPlanOptions', start))
  // ownership — a collaborator on someone else's free org keeps their own slot
  assert.match(rule, /t\.role === 'owner'/,
    'the count must be OWNER memberships — counting members was the #2789 trap')
  // the registry (selfhost) lane's tier proxy — without it the client counts a
  // paid-tier row with no subscription status as free and hides free-create
  assert.match(rule, /\(t\.tier === 'free' \|\| t\.tier == null\)/,
    'the pre-check must mirror the registry lane tier predicate (free OR unset)')
  // not on an active/paid plan
  assert.match(rule, /!ACTIVE_STATUSES\.includes\(t\.subscription_status\)/,
    'an active/paid subscription removes the org from the free count')
  // pending_payment is not real yet
  assert.match(rule, /t\.subscription_status !== 'pending_payment'/,
    'a pending_payment org must not consume the allowance (rules table)')
  // the pre-check reads the fields the teams list already carries (role/tier/
  // subscription_status) — no extra request. The server-side row fields are
  // asserted in the python suite (tests/test_one_free_org_entitlement.py).
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
  // The client does NOT consume the response's team_id — the PRE-MINTED id
  // rides the success URL because the SERVER built that URL. What the client
  // must get right is the MATCH: the poll compares against the URL params.
  assert.match(flat, /const newOrgId = params\.get\('new_org'\)/,
    'the success-return effect must read ?new_org=<id>')
  assert.match(flat, /const newOrgName = params\.get\('new_org_name'\)/,
    'the success-return effect must read ?new_org_name=<name>')
  assert.match(flat, /\.find\(\(t\) => t && \(t\.team_id === newOrgId \|\| \(newOrgName && t\.team_name === newOrgName\)\)\)/,
    'the poll must match the id OR the intended name — the pre-minted id is not the real id on the registry (selfhost) lane')
  assert.match(flat, /if \(switches === 1\) switchTeam\(match\.team_id\)/,
    'the switch must use the MATCHED team id (lane-agnostic) and fire once')
  assert.match(flat, /Your new organization is being set up/,
    'a poll that gives up must TELL the user (paid + no switch) instead of silently staying put')
  // …and the notice must be REACHABLE: it renders on the Billing tab, while
  // the checkout success return opens on the overview tab.
  assert.match(flat, /setBillingNotice\("Your new organization is being set up[^"]*"\) setTab\('billing'\)/,
    'the give-up branch must land on the tab that renders the notice')
})

test('#2789: plan choice in the purchase mode is server-resolved (never hardcoded ids)', () => {
  const block = flat.slice(flat.indexOf("createTeamMode === 'purchase'"))
  assert.ok(block.length > 0, 'the purchase mode must exist')
  assert.match(block, /newOrgPlanOptions\(team\)\.map\(\(p\) => \(/,
    'paid plans come from the pricing catalog, filtered to tiers the server has a price id for')
  assert.match(flat, /const newOrgPlanOptions = \(t\) => planOptions\(\)\.filter\(\(p\) => p\.tier !== 'free'/,
    'the plan list helper must exist and exclude free')
  assert.match(flat, /const newOrgDefaultPrice = \(t\) => \{/,
    'the default plan must be derived, not hardcoded')
  // The default is the FIRST available plan — never an assumed `pro` (a
  // deployment may sell solo/team only; defaulting to a missing pro both
  // refuses checkout and contradicts the plans on screen).
  assert.match(flat, /const first = newOrgPlanOptions\(t\)\[0\]/,
    'the purchase dialog must default to the first AVAILABLE paid plan')
  assert.doesNotMatch(flat, /checkout_price_ids\?\.pro/,
    'no code path may assume the pro price id exists')
  // No hardcoded Stripe price id anywhere (the server resolves them; the
  // match is quoted-literal only so `checkout_price_ids` is not a false hit).
  assert.ok(!/['"]price_[A-Za-z0-9]{3,}['"]/.test(mainJsx),
    'no Stripe price id may be hardcoded in the client')
})

test('#2789: the dialog keeps the #2392 a11y contract in every mode', () => {
  const dialog = dialogBlock()
  assert.match(dialog, /role="dialog" aria-modal="true"/, 'the dialog must stay role=dialog aria-modal=true')
  // The accessible NAME is the current mode's heading (not a generic label),
  // so a screen reader hears the gate copy in limit mode.
  assert.match(dialog, /create-org-title-limit/, 'limit mode must label the dialog from its own heading')
  assert.match(dialog, /create-org-title-purchase/, 'purchase mode must label the dialog from its own heading')
  assert.match(dialog, /create-org-title-name/, 'name mode must label the dialog from its own heading')
  assert.match(dialog, /<h3 id="create-org-title-limit">You can only have one free organization<\/h3>/,
    'the limit heading must be the element the dialog is labelled by (copy + id together)')
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
