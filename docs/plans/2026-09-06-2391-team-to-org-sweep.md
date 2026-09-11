<!-- research-path: docs/scoping/2026-09-06-2391-team-to-org-sweep.md -->

# #2391 Implementation Plan — team→organization vocabulary sweep (dashboard chrome)

**Goal:** Complete the DE2E-2 team→Organization vocabulary migration on the dashboard chrome surfaces (account menu, create-org dialog, invite-accept banner, members tab, claim-guard) + the server error strings they render verbatim + the e2e/pytest pins + committed dist — in one coordinated PR.

**Team:** epistemic-team · **Level:** task · **Complexity:** standard

**Architecture:** Copy-only sweep, no behavior/schema/API-route changes. UI copy changes in `website/apps/dashboard/src/main.jsx`; server `detail` strings in `tortoise/hosted_api.py` (+ `abuse.py`, `__main__.py`) that the flagged chrome surfaces render verbatim via the string-detail-wins fetch layer; e2e pin strings in `tests/e2e/test_dashboard_identity.py` and `_UPGRADE_MSG` in `tests/test_free_team_entitlement.py` updated in the SAME PR; committed `dist/` rebuilt so `dashboard-e2e` CI serves the new bundle. Wire identifiers (`team_id`, `/v1/teams`, `?team_id=`, `switchTeam`…), the literal `Team` plan tier, SDK error vocabulary, 403/404 status-code contracts, Billing, CLI claim print, and legacy pages stay (boundary in scope §3).

### Pattern Research

Skipped — zero third-party dependencies; pure in-repo string edits (skip rule applies). Patterns derived from the verified scope doc + codebase precedent (the wizard's own org-cased copy in wizardFlow.js is the canonical wording; `orgNameError` invalid message `Invalid organization name — letters, numbers, dash, underscore only` is the target wording for the dialog).

### UX Design Decisions

| # | Decision Type | User Choice | Rationale |
|---|---|---|---|
| 1 | copy/messaging | 'Team members' heading → 'Members' (not 'Organization members') | Issue scope mandates 'Members'; shortest clear label for a tab |
| 2 | copy/messaging | Account menu 'Switch team' → 'Switch organization'; '+ Create new team' → '+ Create new organization'; 'No team' → 'No organization' | Issue scope mandates; matches wizard #2326 "account menu" reference |
| 3 | copy/messaging | 'Invalid team name' → 'Invalid organization name — letters, numbers, dash, underscore only' | Byte-identical to the DE2E-3 wizard `orgNameError` invalid message — cross-surface consistency |
| 4 | copy/messaging | 'Welcome to the team!' → 'Welcome to the organization!' | Issue scope |
| 5 | copy/messaging | Claim-guard de-jargon: 'same key, same graph' → 'same key, same memory space' | Issue scope ('graph → the org's memory space') |
| 6 | copy/messaging | `teammates`→`members` tails only; KEEP 'Pro or Team tier' + 'Team tier' badge + Billing 'team' vocabulary | 'Team' is the marketed plan name + Billing is a deliberate per-team surface (#1876) |

**Pending:** none — all copy decisions come from the issue body + verified scope. Legacy invite funnel (invite-accept.html + invite email) and signup.html are filed as separate follow-up issues (scope §9), not absorbed.

### Integration Surface Map

| Surface | Change | Test layer | Notes |
|---|---|---|---|
| dashboard chrome (main.jsx) | C1-C17 copy | node unit (`node --test src/*.test.js` — wizardFlow/wizardArchived DE2E sweeps stay green) + opt-in dashboard e2e pins | e2e pins updated in same PR (T1); e2e suite opt-in (RUN_DASHBOARD_E2E=1, wrangler servers) |
| hosted create-org/invite/claim/onboarding endpoints | S1-S10, S13 server detail | pytest `test_free_team_entitlement.py`, `test_invites_http.py`, `test_control_plane.py`, claim/abuse tests | substring pins (`_UPGRADE_MSG`, `Pro or Team tier`, `member limit`, `already exists`, `paid plan`, `already created`) survive org-cased renames |
| abuse suspended/alerts | S11, S14 (+C17) | pytest abuse tests | unpinned (type-level asserts) |
| __main__ CLI suspended fallback | S12 | — | mirrored literal of S11, unpinned |
| committed dist | rebuild | CI dashboard-e2e serves committed dist on :8790 | convention: render changes ship dist rebuild same PR (#2175 precedent) |

---

### Task 1: Dashboard chrome copy sweep — `website/apps/dashboard/src/main.jsx`

**Intent:** Every user-facing 'team' word in the four chrome surfaces + claim-guard reads Organization (matching the wizard vocabulary).
**Acceptance:** After edits, grepping main.jsx for the old strings (C1-C17 list) finds only comments/identifiers that the §3 boundary allows; no layout/markup structure changed; wizardArchived.test.js:47 pin ('Organization name required' literal at :3366) untouched.
**Files:**
- Modify: `website/apps/dashboard/src/main.jsx` (lines per table)

Apply these exact copy changes (file:line on the current worktree HEAD):

| Id | Old (must exist verbatim) | New |
|---|---|---|
| C1 | `'No team'` in aria-label `:5779`, `<span className="account-name">` `:5784`, identity-name fallback `:5819` | `'No organization'` |
| C2 | `<div className="account-menu-label">Switch team</div>` `:5837` | `Switch organization` (also refresh the adjacent comment `:5834` "Switch team → No team" → "Switch organization → No organization") |
| C3 | `+ Create new team` `:5861` (button text) | `+ Create new organization` |
| C4 | `'Invalid team name — letters, numbers, dash, underscore only'` `:3368` | `'Invalid organization name — letters, numbers, dash, underscore only'` (do NOT touch the `:3366` 'Organization name required' literal — wizardArchived.test.js:47 source-scans it) |
| C5 | `'Create another team requires a paid plan'` `:3385` | `'Create another organization requires a paid plan'` |
| C6 | `'Could not create the team'` `:3388` | `'Could not create the organization'` |
| C7 | `'Welcome to the team! Your membership is active.'` `:2528` | `'Welcome to the organization! Your membership is active.'` |
| C8 | `<h2>Team members</h2>` `:6626` | `<h2>Members</h2>` |
| C9 | placeholder `teammate@example.com` `:6631`, aria-label `Teammate email` `:6632` | `member@example.com` / `Member email` |
| C10 | `>upgrade to add teammates</a>` `:6651` | `>upgrade to add members</a>` (keep the `Pro or Team tier` prefix) |
| C11 | `'Invites require the Pro or Team tier — upgrade to invite teammates.'` `:4049` | `'…tier — upgrade to invite members.'` |
| C12 | `:2397-2398` claim-guard strings | `'You have an anonymous organization waiting to be claimed — attach your ' + 'GitHub or Google identity to claim it (same key, same memory space).'` |
| C13 | `{/anonymous team waiting/.test(welcomeProvisionError) ? (` `:4954` | `{/anonymous organization waiting/.test(welcomeProvisionError) ? (` (must move with C12 or the claim CTA dead-ends) |
| C14 | `if (!confirm('Remove this member from the team?')) return` `:4071` | `'Remove this member from the organization?'` |
| C15 | `"This email is also used by another team — reach out if that's unexpected."` `:1549` | `"…another organization…"` |
| C16 | `'Could not load your teams — try again.'` `:2755` and `:2762` | `'Could not load your organizations — try again.'` |
| C17 | `'This team has been suspended due to unusual activity.'` `:5988` (banner fallback) | `'This organization has been suspended due to unusual activity.'` |

Then:
1. `grep -n "No team\|Switch team\|Create new team\|Invalid team name\|Team members\|teammate\|teammates\|Welcome to the team\|anonymous team\|same key, same graph\|Could not create the team\|Create another team\|Could not load your teams\|Remove this member from the team\|another team\|This team has been suspended" website/apps/dashboard/src/main.jsx` — every remaining hit must be a comment, identifier, or §3-boundary keep (tier badge, Billing). Update the e2e-mirroring comment at `tests/e2e/test_dashboard_identity.py:141` (done in Task 3).
2. Commit: `git add website/apps/dashboard/src/main.jsx && git commit -m "fix(vocab): sweep dashboard chrome 'team' copy to Organization (#2391)"`

---

### Task 2: Server + CLI copy — `tortoise/hosted_api.py`, `tortoise/abuse.py`, `tortoise/__main__.py`

**Intent:** The server `detail` strings the swept chrome renders verbatim (create-org dialog, invite members branch, claim error, suspended banner/alerts) use the same Organization vocabulary — coordinated with the UI.
**Acceptance:** Grep of each old string across tortoise/ finds only §3-boundary hits (SDK sdk.py, 403/404 contracts, onboarding sub-team mechanics, seed content); pytest substring pins survive.
**Files:**
- Modify: `tortoise/hosted_api.py`, `tortoise/abuse.py`, `tortoise/__main__.py`

| Id | File:line | Old | New |
|---|---|---|---|
| S1 | hosted_api.py:8401 | `detail="Team name required"` | `detail="Organization name required"` |
| S2 | hosted_api.py:8403 | `detail="Team name must be ≤ 64 characters"` | `detail="Organization name must be ≤ 64 characters"` |
| S3 | hosted_api.py:8408 | `detail="Invalid team name"` | `detail="Invalid organization name"` |
| S4 | hosted_api.py:8456, :8525, :8565, :8582 | `detail="Team name already exists"` | `detail="Organization name already exists"` |
| S5 | hosted_api.py:8451, :8554 | `detail="Too many teams created — try again later"` | `detail="Too many organizations created — try again later"` |
| S6 | hosted_api.py:8466, :8571 | `detail="Create another team requires a paid plan — upgrade an existing team first"` | `detail="Create another organization requires a paid plan — upgrade an existing organization first"` |
| S7 | hosted_api.py:9566, :9638 | `"…Pro or Team tier — upgrade to invite teammates"` | `"…Pro or Team tier — upgrade to invite members"` (KEEP `Pro or Team tier`) |
| S8 | hosted_api.py:15947 | `detail="Invalid team name"` | `detail="Invalid organization name"` |
| S9 | hosted_api.py:16048 | `detail="Team name already exists"` | `detail="Organization name already exists"` |
| S10 | hosted_api.py:13369 | `"…cannot claim an anonymous team. Confirm…"` | `"…cannot claim an anonymous organization. Confirm…"` |
| S13 | hosted_api.py:9582, :9654, :9952, :10356, :10874 | `detail="Team member limit reached — upgrade to invite more"` | `detail="Member limit reached — upgrade to invite more"` |
| S11 | abuse.py:83 | `"This team has been suspended due to unusual activity. "` | `"This organization has been suspended due to unusual activity. "` |
| S14 | abuse.py:457 | `EVENT_SUSPEND: "Team auto-suspended due to unusual activity"` | `EVENT_SUSPEND: "Organization auto-suspended due to unusual activity"` |
| S12 | __main__.py:1703 | `"This team has been suspended due to unusual activity."` | `"This organization has been suspended due to unusual activity."` |

Then:
1. Re-run the grep sweep over `tortoise/` for the old strings — remaining hits must be only: sdk.py (`Team name must be…`, `Invalid team name:`, `Confirmation must match team name`), 403/404 contracts (`No team membership…`, `Unknown team`, `Team not found`, `No membership in team`), onboarding sub-team mechanics (`A session user is required to create a sub-team`, `Sub-team already created`), CLI claim print (`Claim your team`, `attaches to THIS team`), runtime seed (`Teams using structured agent memory`). All are documented §3 keeps.
2. Commit: `git add tortoise/hosted_api.py tortoise/abuse.py tortoise/__main__.py && git commit -m "fix(vocab): org-cased server error strings rendered by dashboard chrome (#2391)"`

---

### Task 3: Test pin updates — `tests/e2e/test_dashboard_identity.py`, `tests/test_free_team_entitlement.py`

**Intent:** The coordinated sweep moves the pins so a naive future sweep cannot regress the e2e suite.
**Acceptance:** All old-string pins in the two files replaced with the new copy; test names/identifiers unchanged; mock fixture + comments refreshed; no other test file pins the old strings (verified by grep).
**Files:**
- Modify: `tests/e2e/test_dashboard_identity.py`, `tests/test_free_team_entitlement.py`

`tests/e2e/test_dashboard_identity.py`:
- :257 `get_by_text("Switch team")` → `get_by_text("Switch organization")`
- :286 `get_by_text("Switch team")` → `get_by_text("Switch organization")`
- :305 docstring `Members section reads Team members.` → `reads Members.`
- :311 `get_by_role("heading", name="Team members")` → `name="Members"`
- :522/:523/:524/:566 `"+ Create new team"` → `"+ Create new organization"` (button-name locators + clicks)
- :525-528 comment: replace "The account-menu entry keeps the legacy '+ Create new team' label." with the post-sweep reality (entry label is '+ Create new organization')
- :533 `to_contain_text("Invalid team name")` → `to_contain_text("Invalid organization name")`
- :141 comment `blob "No team"` → `blob "No organization"` (cosmetic accuracy)
- :553-554 402 route-mock detail `"Create another team requires a paid plan — upgrade an existing team first"` → org phrasing (fixture hygiene; only the fixed free-cap client copy at :573 is asserted)

`tests/test_free_team_entitlement.py`:
- :27 `_UPGRADE_MSG = "Create another team requires a paid plan"` → `"Create another organization requires a paid plan"` (5 call sites assert `_UPGRADE_MSG in detail`; S6 detail starts with that substring — safe)

Then:
1. Grep the whole repo (tests/, tortoise/, website/) for every old string in Tasks 1-3 — remaining hits must be only §3 keeps + this task's updated lines.
2. Commit: `git add tests/e2e/test_dashboard_identity.py tests/test_free_team_entitlement.py && git commit -m "test(e2e): move team→org copy pins with the sweep (#2391)"`

---

### Task 4: Rebuild + commit dashboard dist

**Intent:** Honor the committed-dist convention (#2175): the `dashboard-e2e` CI job serves the committed bundle and triggers on `src/` changes — a stale dist silently tests the pre-sweep copy.
**Acceptance:** `website/apps/dashboard/dist/` contains a fresh build whose JS bundle no longer contains the old strings and whose stale hashed `index-CMTXyLTN.js` asset is removed.
**Files:**
- Modify: `website/apps/dashboard/dist/` (regenerated assets + index.html)

Steps:
1. `cd website/apps/dashboard && npm run build` (vite present in node_modules; node v22). Expect a new hashed `dist/assets/index-*.js` + unchanged `index.html`/css + deletion of the old `index-CMTXyLTN.js`.
2. `grep -c "Switch team\|Team members\|No team" dist/assets/index-*.js` → expect 0 hits (minified copy may appear only in string form if any survived — must be 0 for the swept strings; a couple of §3 keeps like 'Billing team'/'Team tier' may legitimately remain).
3. Commit: `git add -A website/apps/dashboard/dist && git commit -m "chore(dashboard): rebuild committed dist for org-vocab sweep (#2391)"`

---

### Task 5: Verification

**Intent:** Proof the sweep is complete and green before commit-workflow.
**Acceptance:** Unit + targeted pytest green; residual audit passes; no old pinned string remains outside the §3 allowlist.
**Files:**
- Run (no edits except fixes if a check fails)

1. **Node unit tests** (CI runner is `node --test src/*.test.js` via agent-infra node-ci — there is no npm test script):
   `cd website/apps/dashboard && node --test src/wizardFlow.test.js src/wizardArchived.test.js` then full `node --test src/*.test.js`. Expect all green (DE2E-1/2 copy sweeps + tripwires).
2. **Python pytest** for touched server surfaces (falkordb is running locally; docker lane):
   `export TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix'`
   Run `uv run pytest tests/test_free_team_entitlement.py tests/test_invites_http.py tests/test_control_plane.py tests/test_cli_claim.py -v` (cli_claim guards the §3-kept CLI copy — must stay green untouched). If a file is carve-out/embedded-only (17-file set), run it under `TORTOISE_TEST_CARVE_OUT=1` instead. Add targeted `/v1/teams` validation + claim + abuse tests if cheaply runnable.
3. **Dashboard e2e** (`tests/e2e/test_dashboard_identity.py`): opt-in via RUN_DASHBOARD_E2E=1 and needs wrangler dev servers + route interception — **not feasible in this env**; pins updated in Task 3. Note this in the PR body.
4. **Residual audit:** for each old string in Tasks 1-3 (`No team`, `Switch team`, `Create new team`, `Invalid team name`, `Team members`, `teammates`, `Welcome to the team`, `anonymous team`, `same key, same graph`, `Team name required`, `Team name must be`, `Team name already exists`, `Too many teams`, `Create another team`, `Could not create the team`, `Could not load your teams`, `member limit reached`, `auto-suspended due to unusual`, `has been suspended due to unusual activity`, `also used by another team`, `Remove this member from the team`, `cannot claim an anonymous team`, `add teammates`, `invite teammates`) grep the whole repo. Every remaining hit must be inside the scope §3 allowlist (sdk.py, 403/404 contracts, onboarding sub-team mechanics, CLI claim print, Billing, plan tier, legacy pages, docs/eval/fixtures/history, comments).
5. Report results in the PR body.

---

### Follow-ups (filed separately, NOT in this PR)

- Legacy invite funnel: `website/invite-accept.html` + `tortoise/email_notify.py` invite template (`join the team's memory graph`) + invite-accept endpoint detail strings → separate issue.
- Legacy `website/signup.html:1367` (`API-key login is disabled for this team`) → separate issue.
