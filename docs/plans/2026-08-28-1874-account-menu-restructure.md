<!-- research-path: issue #1874 scoping comment (### Axis Research) — standalone, no epic brief -->

# #1874 — Account Menu Restructure Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Restructure the dashboard header so the avatar menu is the personal-account surface (identity block, Profile entry, separated Log out) and the primary nav holds only team-scoped tabs — Profile tab removed from nav, Members section retitled "Team members".

**Team:** organisation-design-team
**Role:** product-implementer

**Architecture:** UI-only restructure of the router-less dashboard shell (`main.jsx`). The avatar menu becomes a three-section disclosure group (identity → account → workspace → sign out) per the GitHub/Vercel/Linear pattern; the Profile tab render target (`tab === 'profile'`, `main.jsx:3950`) stays but is reachable only via the menu entry; the nav loses the Profile button. No backend, DB, or schema changes.

### Pattern Research

> **Findings date:** 2026-08-28

> Gate skipped: plan touches zero third-party dependencies (React 19 / supabase-js already in-repo and used identically throughout `main.jsx`; no new libraries). Prior external research consumed from the #1874 scoping `### Axis Research` (Vercel/Linear/GitHub/kanso-protocol/UX Movement/Codely/shacdn/SaaSUI — avatar dropdown as personal-account command center; workspace switching co-located with hard separation; anti-pattern of person-scoped settings in team nav).

### Integration Surface Map

Standard-tier condensed map (test-design skill — proportional application: the change is a single shared component; e2e layer already exists for this surface).

| Surface | Test Layer | Expected Verification |
|---------|-----------|----------------------|
| Avatar menu (restructured) | e2e/ux | identity block renders (display_name/email/plan); Profile opens login methods; single-team menu meaningful; Log out separated; anon fallback shows team name |
| Nav (Profile tab removed) | e2e | no Profile button in nav; RecoveryBanner CTA + login-link flows still land on the profile surface |
| Members section heading | e2e | "Team members" heading renders |
| Dashboard gate flow | e2e (regression) | `test_dashboard_gate.py` logout path (`.account-blob-btn` → `.account-menu-logout`) unaffected |

### Journey Test Map

**Journey: "I want to add a second login method to my account"**
1. Click avatar → **Acceptance:** menu shows identity block + Profile entry → **Test:** `test_recovery_banner_shows_and_routes_to_profile` (adapted), `test_account_menu_identity_block_single_team` (new)
2. Click Profile → **Acceptance:** Login methods surface renders → **Test:** existing profile tests (navigation adapted to menu entry)
3. (Single-team user) Click avatar → **Acceptance:** Profile + Log out present, no empty switch section → **Test:** `test_account_menu_identity_block_single_team`

**Journey: "I want to know which team I'm working in"**
1. Nav shows team-scoped tabs only → **Acceptance:** no "Profile" button in nav → **Test:** nav assertion (new)
2. Members tab → **Acceptance:** heading reads "Team members" → **Test:** heading assertion (new)

### Failure Modes
- **Nav Profile button removed but a flow still clicks the nav-tab by name** → expected: tests navigate via the account menu; selectors scoped to `.account-menu` while the nav button still exists (strict-mode double-match) → covered by Task 1/2 ordering.
- **Session user with empty `display_name`** → expected: email-prefix fallback (existing pattern `main.jsx:1227`) — team name must NOT be shown as personal identity (that's the original bug) → Task 2 fallback.
- **Anon key-login / no-session rendering** → expected: NOT e2e-tested (no-session redirects to /auth; claim-intent shows the claim-paste screen; key-login anon teams get the Protect screen — the account menu never renders without a session in the current architecture). The `currentTeamName` fallback in the identity block is **defensive dead code** — annotated in Task 2, not asserted.
- **Harness mismatch** (`_session()` has no `user_metadata`; mocks use `name` not `team_name`) → expected: harness updated first so assertions are real → Task 1.
- **e2e modules skip silently** (opt-in `RUN_DASHBOARD_E2E`) → expected: env var set on every run; red phase must FAIL, not skip → Task 1/4 commands.
- **Narrow viewport nav overflow** → expected: fewer nav items now (5 vs 6) → `test_no_horizontal_scroll_narrow_viewport` still passes.

### UX Design Decisions

All decisions made interactively with the product owner (2026-08-28) — recorded, no gate needed:

| # | Decision Type | User Choice | Rationale |
|---|---|---|---|
| 1 | Menu placement | Profile lives in the avatar menu, not a nav tab | Avatar dropdown = personal account command center (research); user: "profile should live in the foldout menu" |
| 2 | Workspace switch | Stays in the avatar menu, divider-separated | kanso-protocol primary rule set aside deliberately; co-location-with-separation matches GitHub/Linear (PO-approved branch) |
| 3 | Context scope | Single global switcher (team); no graph dropdown | Global vs contextual separation (research); user confirmed after research |
| 4 | Members heading | Nav label "Members"; section heading "Team members" | User: "more clear" |
| 5 | Billing | Team dropdown in Billing tab (separate issue #1876) | User: "which team billing are you talking about?" |

### Verification Plan

(test-routing, proportional — UI-only change)
- **e2e (full):** `tests/e2e/test_dashboard_identity.py` (adapted navigation + new assertions), `tests/e2e/test_dashboard_gate.py` (regression), narrow-viewport test — all with `RUN_DASHBOARD_E2E=1`
- **Unit:** none (no logic module changes; `identity.js` untouched)
- **Backend/integration/pgTAP:** skipped — zero backend surface (documented in surface map)
- **UX verification:** dashboard e2e covers the visible changes; no design-system surface (standalone component styles)

**Tech Stack:** React 19 (JSX), Vite, Playwright e2e (Python), CSS custom properties.

---

## Task 1: E2E harness + menu-based navigation tests (red)

**Intent:** Fix the test harness so assertions are real (not vacuous), route existing profile navigation through the account menu, and write the new menu/nav/heading assertions first — driving the restructure.
**Acceptance:** `RUN_DASHBOARD_E2E=1` runs FAIL (not skip) on the new assertions; harness carries `user_metadata.display_name` and `team_name` mocks.

**Files:**
- Modify: `tests/e2e/test_dashboard_identity.py`
- Test: same file

**Step 1 — Harness fixes:**
- `_session()` (~lines 47–63): add `"user_metadata": {"display_name": "danielospinabotero"}` (mirror `test_dashboard_gate.py:217` which already uses `user_metadata`).
- `_wire` `/v1/teams` + `/v1/team` mocks: use `team_name` (main.jsx reads `t.team_name` at :452 — the `name` field renders empty). E.g. `{"team_id": "team_e2e", "team_name": "E2E", "tier": "free"}`.
- Add a two-team variant for the multi-team test: key team-scoped responses (`/v1/team`, `/v1/session/key`) by the `team_id` query param so switching actually re-hydrates.

**Step 2 — Navigation helper (regex match — the blob aria-label is `Account menu — {team}`):**
```python
def _open_account_menu(page: Page):
    page.get_by_role("button", name=/Account menu/).click()

def _open_profile_via_menu(page: Page):
    page.locator(".account-menu").get_by_role("button", name="Profile").click()
```
Update the two existing nav-tab clicks (`test_dashboard_identity.py:209, 245`) to `_open_account_menu(page)` + `_open_profile_via_menu(page)`.

**Step 3 — New assertions (fail until Task 2):**
```python
def test_account_menu_identity_block_single_team(page: Page):
    _seed(page); _wire(page, inv=_inventory(login_methods=1))
    page.goto(DASHBOARD_URL)
    _open_account_menu(page)
    expect(page.locator(".account-menu").get_by_text("danielospinabotero")).to_be_visible()
    expect(page.locator(".account-menu").get_by_role("button", name="Profile")).to_be_visible()
    expect(page.locator(".account-menu").get_by_role("button", name="Log out")).to_be_visible()
    expect(page.locator(".account-menu").get_by_text("Switch team")).to_have_count(0)

def test_account_menu_multi_team_switch(page: Page):
    # REGRESSION GUARD — the switch section already renders in committed code, so this
    # goes GREEN in Task 1 (not a red target). Two-team _wire keys team-scoped responses
    # (/v1/team, /v1/session/key) by the requested team_id; assert both teams render,
    # aria-current on active, and switching re-hydrates the new team's data.

def test_members_heading_and_nav(page: Page):
    expect(page.locator("nav").get_by_role("button", name="Profile")).to_have_count(0)
    page.get_by_role("button", name="Members").click()
    expect(page.get_by_role("heading", name="Team members")).to_be_visible()

def test_account_menu_email_prefix_fallback(page: Page):
    # session WITHOUT display_name: identity block shows the email-prefix (live branch)
    # _session seeded with user_metadata.display_name omitted
    _open_account_menu(page)
    expect(page.locator(".account-menu").get_by_text("identity-e2e")).to_be_visible()
```
(`test_members_heading_and_nav` partially fails until Task 2/3 — split the nav assertion and heading assertion if needed so each task has a clean red target.)

**Step 4 — Run to confirm RED (not skip):**
Run: `RUN_DASHBOARD_E2E=1 TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/e2e/test_dashboard_identity.py -q`
Expected: FAIL on the new menu assertions (Profile not in menu yet).

## Task 2: Account menu restructure + nav Profile removal

**Intent:** Make the avatar menu the personal-account command center; remove the nav Profile button in the SAME task so Playwright selectors resolve (menu entry + nav tab would otherwise double-match under strict mode).
**Acceptance:** Menu shows identity block (display_name/email-prefix fallback/plan; team-name fallback for anon), Profile entry → `setTab('profile')`, workspace switch section, separated Log out. Nav has no Profile button.

**Files:**
- Modify: `website/apps/dashboard/src/main.jsx` (account blob block ~3664–3725; nav button at :3654)
- Modify: `website/apps/dashboard/src/index.css` (~207–242)
- Test: `tests/e2e/test_dashboard_identity.py`

**Step 1 — Identity block (in `.account-menu`, above the switch section):**
```jsx
{/* #1874: identity block — the PERSON. Session: display_name → email-prefix fallback
    (pattern main.jsx:1227). Anon (no sessionMetaRef): team name, no email. */}
<div className="account-identity" role="group" aria-label="Account identity">
  <span className="account-avatar" aria-hidden="true">
    {(sessionMetaRef.current?.display_name ||
      (sessionMetaRef.current?.email ? sessionMetaRef.current.email.split('@')[0] : '') ||
      currentTeamName || 'T').charAt(0).toUpperCase()}
  </span>
  <div className="account-identity-text">
    <span className="account-identity-name">
      {sessionMetaRef.current?.display_name ||
        (sessionMetaRef.current?.email ? sessionMetaRef.current.email.split('@')[0] : '') ||
        currentTeamName || 'No team'}
    </span>
    {/* currentTeamName fallback: DEFENSIVE — the account menu never renders without a
        session in the current architecture (no-session → /auth, anon → Protect screen). */}
    {sessionMetaRef.current?.email && (
      <span className="account-identity-email">{sessionMetaRef.current.email}</span>
    )}
  </div>
  {team?.tier && <span className="tier-badge">{team.tier}</span>}
</div>
<div className="account-menu-divider" />
<button className="account-menu-profile" onClick={() => { setTab('profile'); setAccountMenuOpen(false) }}>
  Profile
</button>
<div className="account-menu-divider" />
```
(Verify `sessionMetaRef.current` shape at `main.jsx:1552–1555` — `{ display_name, email }`.)

**Step 2 — Keep the existing "Switch team" section (`teams.length > 1` guard) and Log out unchanged.**

**Step 3 — Delete the nav Profile button** (`main.jsx:3654`, the only `data-tab="profile"` consumer — verified). Nav = Overview · API Keys · Graphs · Members · Billing.

**Step 4 — CSS additions + fix dead menu-button styles (`index.css` near `.account-menu`):**
The committed DOM uses `role="group"` + plain buttons (P2-1 a11y change), so the existing `.account-menu button[role='menuitem']` rules (index.css:232–238) are DEAD — menu buttons fall through to the global filled-accent `button` rule (index.css:40). Replace them with a live disclosure-row style and add the identity block:
```css
.account-identity { display: flex; align-items: center; gap: 10px; padding: 10px 12px; }
.account-identity-text { display: flex; flex-direction: column; min-width: 0; flex: 1; }
.account-identity-name { font-weight: 600; font-size: 14px; }
.account-identity-email { font-size: 12px; opacity: 0.6; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.account-menu button { width: 100%; text-align: left; background: none; border: none; color: var(--text-dim); padding: 8px 12px; border-radius: 6px; font-weight: 400; cursor: pointer; }
.account-menu button:hover { background: rgba(6,182,212,0.1); color: var(--text, #e2e8f0); }
.account-menu button.active { color: var(--accent, #06b6d4); }
.account-menu .account-check { margin-left: auto; }
.account-menu-logout { color: #f87171 !important; }
```
(This replaces the dead `button[role='menuitem']` selectors; covers switch rows + Profile + Log out.)

**Step 5 — Run:**
Run: `RUN_DASHBOARD_E2E=1 TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/e2e/test_dashboard_identity.py -q`
Expected: menu assertions pass; adapted profile flows pass via the menu helper. `test_members_heading_and_nav` STILL FAILS on the heading assertion — cleared in Task 3.

## Task 3: Members heading + verification sweep

**Intent:** Members section names its scope; confirm the full surface.
**Acceptance:** Members `<h2>` reads "Team members" (`main.jsx:4164`); heading + nav assertions green; no dead references.

**Files:**
- Modify: `website/apps/dashboard/src/main.jsx` (Members heading at :4164)

**Step 1:** Change `<h2>Members</h2>` → `<h2>Team members</h2>` (nav button label stays "Members").

**Step 2:** Grep for dead profile references: `grep -rn 'data-tab="profile"' website/apps/dashboard/src/` — expect **zero** matches (nav button deleted in Task 2; menu entry has no data-tab).

**Step 3:** Run the heading + nav assertions:
Run: `RUN_DASHBOARD_E2E=1 TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/e2e/test_dashboard_identity.py::test_members_heading_and_nav -q`
Expected: PASS.

## Task 4: Full suite + build + commit

**Intent:** Regression sweep across the dashboard e2e surface and ship the bundle.
**Acceptance:** All dashboard e2e green (identity adapted + new, gate logout regression, narrow viewport); vite build regenerates `dist`; commit via `commit-workflow`.

**Files:**
- Test: `tests/e2e/test_dashboard_identity.py`, `tests/e2e/test_dashboard_gate.py`

**Step 1:** Full dashboard e2e:
Run: `RUN_DASHBOARD_E2E=1 TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/e2e/test_dashboard_identity.py tests/e2e/test_dashboard_gate.py -q`
Expected: all pass (gate logout path exercises the restructured menu — `.account-blob-btn`/`.account-menu-logout` classes preserved; narrow-viewport test passes with 5 nav items).

**Step 2:** Build the dashboard bundle (dist must include the new menu for the deploy):
Run: `cd website/apps/dashboard && npm run build`
Expected: `dist/assets/index-*.js` regenerated with the restructure.

**Step 3:** Identity unit tests: `node --test website/apps/dashboard/src/identity.test.js` (via the repo's node --test convention) — expected pass (identity.js untouched).

**Step 4:** Commit via `commit-workflow` (mandatory review gates; the PR carries the plan doc + dist).

<!-- plan-review: cycles=3, status=clean, version=2.3.0 -->
