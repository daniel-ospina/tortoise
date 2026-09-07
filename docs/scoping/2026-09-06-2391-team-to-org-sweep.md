# #2391 Scope v3 (final) — 'team' chrome contradicts the wizard's Organization vocabulary

Date: 2026-09-06 · Level: task · Complexity: standard · Worktree: `.worktrees/batch-2391` (branch `fix/2391-team-to-org-sweep`)
Pipeline: task-workflow-standard (scope → scope-verify → plan → plan-verify → implement → verify)
Version note: v2 absorbs scope-verify cycle-1 findings (2 verifiers, 17 issues). All P1/P2 adopted; boundary table extended; dist-rebuild deliverable added; orgNameError-reuse approach dropped (see §6).

## 1. Confirmed Problem

The first-run wizard, overview, and settings already speak **Organization** (DE2E-2 — `wizardFlow.test.js` + `wizardArchived.test.js` enforce org copy, zero `team`/`workspace`). One click past the wizard the dashboard **chrome** still says **team** — the account menu the wizard's own #2326 error points users to ("Switch back to it in the account menu") labels itself "Switch team". This is a **vocabulary-migration gap**: the DE2E-2 rename did not reach four older chrome surfaces (account menu, create-org dialog, invite-accept banner, members tab), the rare claim-guard copy, or several adjacent chrome/server error strings that render verbatim next to them.

Coordinated sweep required: `tests/e2e/test_dashboard_identity.py` pins old strings (`Switch team` :257/:286/:523, `+ Create new team` :522/:523/:524/:566, `Team members` :311, `Invalid team name` :533) — UI strings, e2e pins, and the server detail strings the chrome renders verbatim must change in the **same PR**.

## 2. In-scope change list (verified file:line on main dd4fbe4d baseline)

### 2a. Dashboard chrome — `website/apps/dashboard/src/main.jsx`

| # | Copy now | New copy | Loc |
|---|---|---|---|
| C1 | `'No team'` (aria-label, blob, identity fallback) | `'No organization'` | :5779 :5784 :5819 |
| C2 | `'Switch team'` (multi-team label) | `'Switch organization'` | :5837 (+ comment :5834) |
| C3 | `'+ Create new team'` | `'+ Create new organization'` | :5861 |
| C4 | `'Invalid team name — letters, numbers, dash, underscore only'` | `'Invalid organization name — letters, numbers, dash, underscore only'` | :3368 (client inline validation — SOURCE of the dialog error; see §4) |
| C5 | `'Create another team requires a paid plan'` (catch fallback) | `'Create another organization requires a paid plan'` | :3385 |
| C6 | `'Could not create the team'` (catch fallback) | `'Could not create the organization'` | :3388 |
| C7 | `'Welcome to the team! Your membership is active.'` | `'Welcome to the organization! Your membership is active.'` | :2528 |
| C8 | `<h2>Team members</h2>` | `<h2>Members</h2>` | :6626 |
| C9 | placeholder `teammate@example.com` / aria `Teammate email` | `member@example.com` / `Member email` | :6631 :6632 |
| C10 | link `upgrade to add teammates` | `upgrade to add members` (keep `Pro or Team tier` prefix — plan name) | :6651 |
| C11 | client invite-cap fallback `…upgrade to invite teammates.` | `…upgrade to invite members.` (keep `Pro or Team tier`) | :4049 |
| C12 | claim-guard `You have an anonymous team waiting to be claimed — attach your GitHub or Google identity to claim it (same key, same graph).` | `You have an anonymous organization waiting to be claimed — attach your GitHub or Google identity to claim it (same key, same memory space).` | :2397-2398 |
| C13 | render regex `/anonymous team waiting/` | `/anonymous organization waiting/` (must move with C12) | :4954 |
| C14 | `Remove this member from the team?` (window.confirm) | `Remove this member from the organization?` | :4071 |
| C15 | `This email is also used by another team — reach out if that's unexpected.` | `…another organization…` | :1549 (profile tab) |
| C16 | `Could not load your teams — try again.` | `Could not load your organizations — try again.` | :2755 :2762 (mount) |
| C17 | suspended banner fallback `This team has been suspended due to unusual activity.` | `This organization has been suspended…` | :5988 |

### 2b. Server strings the chrome renders VERBATIM (`api()`/`apiErrorText` string-detail-wins — main.jsx:648/:1327-1331) — `tortoise/hosted_api.py`

| # | Endpoint | Copy now → new | Loc |
|---|---|---|---|
| S1 | POST /v1/teams (create-org, supabase lane) | `Team name required` → `Organization name required` | :8401 |
| S2 | POST /v1/teams | `Team name must be ≤ 64 characters` → `Organization name must be ≤ 64 characters` | :8403 |
| S3 | POST /v1/teams | `Invalid team name` → `Invalid organization name` | :8408 |
| S4 | POST /v1/teams | `Team name already exists` (409) → `Organization name already exists` | :8456 :8525 :8565 :8582 (registry except-map) |
| S5 | POST /v1/teams | `Too many teams created — try again later` (429) → `Too many organizations created — try again later` | :8451 :8554 |
| S6 | POST /v1/teams | `Create another team requires a paid plan — upgrade an existing team first` (402) → `Create another organization requires a paid plan — upgrade an existing organization first` | :8466 :8571 |
| S7 | POST /v1/invites (tier-gate 402) | `Invites require the Pro or Team tier — upgrade to invite teammates` → `…upgrade to invite members` (KEEP `Pro or Team tier` — plan name, pinned by test_invites_http.py:159/:167) | :9566 :9638 |
| S8 | POST /v1/onboarding/team (legacy second-org lane; org-create name validation mirrors this endpoint per wizardFlow.js:17) | `Invalid team name` → `Invalid organization name` (its sibling 402 :15982 already org-cased) | :15947 |
| S9 | POST /v1/onboarding/team registry | `Team name already exists` → `Organization name already exists` | :16048 |
| S10 | POST /v1/claim | `Your email is not confirmed — cannot claim an anonymous team. Confirm…` → `…anonymous organization…` | :13369 |
| S13 | POST /v1/invites (seat-cap 402, Pro capacity — renders verbatim in the members-tab invite branch, same catch as S7) | `Team member limit reached — upgrade to invite more` → `Member limit reached — upgrade to invite more` | :9582 :9654 :9952 :10356 :10874 (pins: test_invites_http.py:179/:237 assert substring `member limit` — survives)

### 2c. Server/CLI suspended-banner vocabulary (rendered in dashboard banner + CLI) — keep client (C17) and server mirror in sync

| # | File | Copy now → new | Loc |
|---|---|---|---|
| S11 | tortoise/abuse.py | `This team has been suspended due to unusual activity. ` → `This organization has been suspended…` | :83 |
| S12 | tortoise/__main__.py | `This team has been suspended due to unusual activity.` (CLI suspended-banner fallback — mirrored literal of S11, unpinned) → org | :1703 |
| S14 | tortoise/abuse.py EVENT_SUSPEND alert label (rendered verbatim as `{a.message}` in the Security-alerts feed main.jsx:6013-6014, whose intro is already org-cased) | `Team auto-suspended due to unusual activity` → `Organization auto-suspended due to unusual activity` | :457 (unpinned — test_abuse asserts types) |

### 2d. Test pins — same PR

| # | File | Change |
|---|---|---|
| T1 | tests/e2e/test_dashboard_identity.py | :257/:286/:523 `Switch team` → `Switch organization`; :522/:523/:524/:566 `+ Create new team` → `+ Create new organization`; :311 + :305 docstring `Team members` → `Members`; :533 `Invalid team name` → `Invalid organization name`; :525-528 comment refresh (label is no longer "legacy"); :553-554 402 route-mock detail → org phrasing (fixture hygiene) |
| T2 | tests/test_free_team_entitlement.py | :27 `_UPGRADE_MSG` → `"Create another organization requires a paid plan"` (5 call sites assert substring — safe) |

### 2e. Deliverable: committed dist rebuild

Dashboard render change ⇒ rebuild + commit `website/apps/dashboard/dist/` in the same PR (repo convention, ci.yml:183-197 + website_architecture.md:230 — "render changes ship the dist rebuild in the same PR", #2175 precedent). `dashboard-e2e` CI job serves the committed dist and triggers on `src/` changes; a stale dist tests the old bundle. Steps: `cd website/apps/dashboard && npm run build` → new hashed `dist/assets/index-*.js` (+ unchanged css/html) replaces the stale `index-CMTXyLTN.js`. Local node_modules + vite present.

## 3. Out-of-scope (deliberately kept — exhaustive boundary for the residual audit)

| Surface | Where | Why kept |
|---|---|---|
| API/wire identifiers `team_id`, `team_name`, `/v1/teams`, `/v1/team/keys`, `?team_id=`, `switchTeam`, `loadTeams`, state names | routes/pins/functions | Wire/API contract, guarded by `keyTeamPinsTripwire.test.js`/`mintTripwire.test.js`; renaming = breaking change, zero user-copy value |
| **Plan tier literally named `Team`** | `PLAN_TIERS=['free','solo','pro','team']` pricing.js; tier-badge raw render main.jsx:5825 (`{team.tier}`, e2e pins 'free' :262); `Team tier` badge :5969; `Pro or Team tier` in S7/notice | It is the marketed plan name (`product/pricing.json` $149/mo team plan; product.html mirrors). Renaming a priced plan = pricing decision. Visible word "Team" in the free/solo members-tab notice + tier badge is a **plan-name reference**, not residual chrome copy |
| Billing surface | main.jsx:6693-6699 `Billing — {currentTeamName \|\| 'this team'}`, `aria-label="Billing team"` | Deliberate per-team-billing surface (#1876 test docstring "Billing names its team"); W1/W4 plans list it as not-swept; e2e pins (`get_by_label("Billing team")` :330/:350, `.account-menu .tier-badge`→`free` :254) |
| Team-missing/denied guards | `Unknown team` 404s (:8941/:8953/:8999/:9067/:9070/:9155/:9158/:9254/:9557/:9627), `Team not found` :1964, `No membership in team` (:1875/:4251/:8996/:9251) | Same status-code-contract class as the `No team membership` row — programmatic, not chrome copy |
| Invite-accept endpoint detail strings | `Already a member of this team` :9904, `this team requires a paid plan to join` ~:9945, seat-cap 402 :9952 (covered by S13) | Owned by the invite funnel follow-up (§9) — the server strings behind legacy invite-accept.html; the accept surface is being reworked separately |
| CLI claim instructions | __main__.py:1541 `🔐 Claim your team…`, :1548-1549 `attaches to THIS team — same key, same graph, memories intact.`, :5843 `--claim` help `…the anonymous team` | CLI `tortoise signup --claim` surface — PINNED by tests/test_cli_claim.py:62/:80. Distinct from the dashboard org-create claim-guard the issue enumerates; keep + sweep would double blast radius into CLI + its test for no dashboard-chrome value |
| Runtime demo/seed content | hosted_api.py:4902 `Teams using structured agent memory report…` (epistemic demo seed) | Seeded example data rendered as demo content, not chrome copy |
| 403/401 API guards `No team membership` / `No team membership — create a team first` / `No team context` | hosted_api.py:1927/:5847/:14196/:14426, mcp_server.py | Programmatic contract handled by status code (test_session_key_http.py:552 etc.), not chrome copy |
| SDK `ControlPlaneError` vocabulary (`Invalid team name: …`, `Team name must not be empty`/`≤ 64`, `Team name must be 64 characters or fewer`, `Confirmation must match team name exactly`, sdk.team_create docs) | sdk.py:13158-13164/:13570-13578 | Public Python SDK error vocabulary pinned by test_control_plane.py:95/:139 — not dashboard chrome |
| Onboarding lane sub-team mechanics copy | hosted_api.py:15958 403 `A session user is required to create a sub-team…`, :15972+ `Sub-team already created` (×2), lane docstring | Lane is dormant client-side (no dashboard caller; first-orgs go through tenant-provision). `Invalid team name`(:15947)/dup(:16048) ARE swept (S8/S9 — exact pinned phrases); the sub-team mechanics strings describe internal provisioning and predate the user-vocab rename — sweeping them would half-rename an internal concept with no user-visible benefit |
| Legacy standalone pages | website/invite-accept.html (`You're invited…`, `Welcome to the team` success, `team's memory graph`), website/signup.html:1367 `API-key login is disabled for this team…` | Legacy pre-dashboard pages, unpinned, separate surfaces → **file as separate follow-up issues (don't absorb)** |
| Historical records / content fixtures | docs/, tests/eval transcripts, tests/fixtures (labeled_pairs.jsonl, ask_spotcheck…) | Historical/synthetic content, not live surfaces |
| Comments referencing API/team concepts (except where adjacent to an edited string and factually stale) | main.jsx, hosted_api.py | Non-copy |

## 4. Source-of-truth finding (issue scope bullet: verify server or client)

The create-org dialog's `Invalid team name` is **client-generated**: main.jsx:3366-3369 runs the charset regex inline before POST ("inline error, no POST" per e2e comment at :533). The server's parallel 422 (:8403/:8408) on the same POST /v1/teams endpoint is swept too (S2/S3) because the dialog renders server `detail` verbatim on bypass/non-validation failures. The wizard's org-create step validates with `orgNameError` (wizardFlow.js:141-148, already org-cased) — no wizard change needed.

## 5. Why not `orgNameError` reuse (approach dropped after scope-verify)

Reusing `orgNameError` for the dialog's empty-name branch would delete the `'Organization name required'` literal that `wizardArchived.test.js:47` source-scans (DE2E-2 tripwire) and change the empty-state message to `'Organization name is required'`. Cost/risk > benefit for a copy sweep: keep the inline branches, reword only the invalid-case literal (C4) to wording identical to `orgNameError`'s invalid message. wizardArchived.test.js stays green untouched.

## 6. Root cause & framing (problem diamond)

Not a functional bug — a vocabulary-migration gap (DE2E-2 rename not propagated to 4 chrome surfaces + claim-guard + their verbatim server strings). Framing B (repo-wide rename incl. wire ids/SDK/plan tier/legacy pages) rejected: breaks API contract + tripwires + renames a priced plan. Framing C (constants module) rejected: ~20 strings, no regression benefit.

## 7. Verification plan (revised)

1. Node unit tests (the runner is `node --test src/*.test.js` — the dashboard has NO npm test script; CI runs it via agent-infra node-ci): `cd website/apps/dashboard && node --test src/wizardFlow.test.js src/wizardArchived.test.js` (DE2E-2/DE2E-1 sweeps must stay green) then full `node --test src/*.test.js`.
2. Repo pytest for touched server surfaces:
   `export TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix'` (falkordb running locally), run `tests/test_free_team_entitlement.py tests/test_invites_http.py tests/test_control_plane.py` + targeted `/v1/teams` validation/claim/abuse tests.
3. Dashboard e2e (`tests/e2e/test_dashboard_identity.py`) is opt-in (`RUN_DASHBOARD_E2E=1`) and needs wrangler dev servers — update pin strings regardless; note in the PR that actually running it was not feasible in this env.
4. Rebuild + commit `website/apps/dashboard/dist/` (deliverable §2e) and sanity-grep the rebuilt bundle for residual old copy.
5. Residual audit: grep repo for every pre-change string (the §2 tables + `No team`/`anonymous team`/`Welcome to the team`/`teammates`/`Invalid team name`/`Team members`/`Switch team`/`Create new team`/`Team name required`/`Team name must be`/`Team name already exists`/`Too many teams`/`Create another team`/`Could not create the team`/`Could not load your teams`/`has been suspended due to unusual activity`/`also used by another team`/`Remove this member from the team`/`cannot claim an anonymous team`). Remaining hits must all be in the §3 boundary table (plan names, wire ids, SDK, 403 contracts, onboarding sub-team mechanics, legacy pages, comments/history/fixtures).

## 8. Rejected/absorbed verifier issues (cycle log)

- Cycle 1 (2 verifiers, 17 issues — all P0-P2 absorbed or resolved):
  - P1 wizardArchived.test.js:47 vs orgNameError reuse → approach dropped (§5).
  - P2/P3 absorbed into scope: C5/C6 dialog fallbacks; C14 confirm; C15 profile; C16 mount; C17 suspended + S11/S12; S4 :8582; S5 429s; S10 claim; S8/S9 onboarding name/dup strings; T1 mock/comment refresh; §3 boundary additions (tier badge render, Billing, signup.html).
  - P4: doc §7 step-1 runner corrected (no npm test script). sdk.py citation lines corrected.
  - Dist rebuild (P2): added as deliverable §2e.
- Cycle 2 (2 verifiers, 11 issues — all P2/P3/P4 → pass-through rule: incorporated, no re-launch):
  - P2 S13 seat-cap 402 `Team member limit reached` (5 sites) absorbed.
  - P2 S14 abuse.py EVENT_SUSPEND alert label absorbed.
  - P3 boundary rows added: team-missing/denied guards; invite-accept endpoint strings; CLI claim instructions (kept — pinned); runtime demo seed content.
  - P3 invite-email copy folded into the §9 invite-funnel follow-up (not absorbed — separate transactional surface).
  - P4 citation fixes (S7 endpoint label POST /v1/invites; e2e `free` pin :254; ci quote site), T1 comment refresh extended to the e2e :141 comment.

## 9. Accepted boundary / follow-ups

File separate GitHub issues (do NOT absorb): (1) legacy invite funnel — `website/invite-accept.html` team copy (`You're invited…`, `Welcome to the team` success, `team's memory graph`) + the transactional invite email `tortoise/email_notify.py:180/:190` (`join the team's memory graph`) + the invite-accept endpoint detail strings; (2) legacy `website/signup.html:1367` team copy. These are standalone legacy surfaces outside the dashboard chrome — reviewed + tracked separately.
