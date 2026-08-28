<!-- research-path: issue #1876 scoping comment (### Axis Research) — standalone, no epic brief -->

# #1876 — Billing Tab Team-Context Dropdown Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Make the Billing tab explicitly state which team's billing is being viewed, with a team dropdown at the top that switches team context (reusing `switchTeam`) and lands back on Billing. Single-team users see the team name without an empty selector.

**Team:** organisation-design-team
**Role:** product-implementer

**Architecture:** UI-only change in the router-less dashboard shell (`main.jsx` Billing tab, ~4406). The Billing section header gains "Billing — {team}" + a native `<select>` (accessible, in-section context selector) listing the user's teams; `onChange` calls the existing `switchTeam` then `setTab('billing')`. No backend, DB, or schema changes.

### Pattern Research

> **Findings date:** 2026-08-28

> Gate skipped: plan touches zero third-party dependencies (React 19 / supabase-js already in-repo; no new libraries). Prior UX research consumed from the #1876 scoping `### Axis Research`:
> - **Per-tenant billing sections must state the tenant explicitly** [canonical]: Vercel's team dashboard shows the team in the section header with a switcher reachable from the same surface; billing/usage pages must make the tenant explicit because the data is tenant-scoped (per-team Stripe customers here). An in-section context selector for a per-tenant section is the established pattern.
> - **Two GLOBAL dropdowns are the anti-pattern** [pitfalls]: designpixil "contextual navigation in the global navigation space is the mistake"; LogRocket: global nav orients, app-level nav does work. This is an in-section selector, not a second global switcher — consistent with the #1874 single-global-switcher decision.
> - **Rejected**: URL-driven team context (`?team=X`) deferred — router-less shell; `switchTeam` state suffices; revisit when deep-linking becomes a requirement (VibeWeek URL pattern is a future nicety).

### Integration Surface Map

| Surface | Test Layer | Expected Verification |
|---------|-----------|----------------------|
| Billing tab header + dropdown | e2e | "Billing — {team}" renders; switch re-loads data + stays on Billing; single-team hides dropdown |

### Journey Test Map

**Journey: "Which team is this billing for?"**
1. Click Billing → **Acceptance:** heading reads "Billing — {current team}" → **Test:** `test_billing_team_context`
2. (Multi-team) Switch team in the dropdown → **Acceptance:** heading + plan data update, tab stays Billing → **Test:** `test_billing_team_context`
3. (Single-team) Open Billing → **Acceptance:** team name shown, no empty selector → **Test:** `test_billing_team_context` (single-team branch)

### Failure Modes
- **Switch mid-render** → expected: `switchTeam` resets team-scoped state + re-fetches (existing behavior, verified in #1874 e2e); `setTab('billing')` keeps the tab → covered by the e2e switch assertion.
- **teams not loaded** (`currentTeamId` null) → expected: select renders only when `teams.length > 1`; heading falls back to `currentTeamName || 'this team'` → covered by the single-team branch.
- **Narrow viewport** → expected: the row already has `flexWrap: wrap`; the select is compact → covered by the existing narrow-viewport test (no overflow).

### UX Design Decisions

Research-backed; no new decisions requiring fresh research (the pattern is settled: in-section selector, per Vercel precedent):

| # | Decision Type | Choice | Rationale |
|---|---|---|---|
| 1 | Selector form | Native `<select>` in the Billing header | Accessible by default, in-section context selector per the research (vs a second global dropdown — the anti-pattern) |
| 2 | Heading | "Billing — {team name}" | The tenant must be explicit on a per-tenant section (Vercel precedent; user: "which team billing are you talking about?") |
| 3 | Single-team | Dropdown hidden; team name alone | No empty selector, no ambiguity (user decision 2026-08-28) |
| 4 | Switch behavior | `switchTeam` + `setTab('billing')` | Reuses the existing team-switch machinery; lands back on Billing |

### Verification Plan

- **e2e (full):** `tests/e2e/test_dashboard_identity.py` — new `test_billing_team_context` + `test_billing_team_context_single_team` (data-re-hydration pinned via `team_reads`/`expect_response`), narrow-viewport extended to open Billing, `RUN_DASHBOARD_E2E=1`, docker lane; existing suite (11 tests) must stay green.
- **Unit:** none (no logic module changes).
- **Backend/integration/pgTAP:** skipped — zero backend surface.
- **Note (review P2-2):** the dashboard is plain JSX — there is NO TypeScript step; `npm run build` (vite) is the only build gate. The issue's "TypeScript build" target is satisfied by the vite build + e2e green.

**Tech Stack:** React 19 (JSX), Vite, Playwright e2e (Python), CSS custom properties.

---

## Task 1: E2E test (red)

**Intent:** Pin the behavior before implementation.
**Acceptance:** `test_billing_team_context` fails until Task 2.

**Files:**
- Modify: `tests/e2e/test_dashboard_identity.py`

**Step 1:** Add the tests using the existing harness (`_seed`/`_wire` with the two-team `teams` param + `team_reads` pin from #1874):
```python
def test_billing_team_context(page: Page):
    """#1876: Billing names its team; multi-team can switch in-tab AND the
    plan data re-hydrates (pinned via team_reads + the Pro-plan badge)."""
    _seed(page)
    teams = [
        {"team_id": "team_a", "team_name": "Alpha", "tier": "free",
         "subscription_status": None, "write_ops_used": 0, "write_ops_limit": 10000},
        {"team_id": "team_b", "team_name": "Bravo", "tier": "pro",
         "subscription_status": "active", "write_ops_used": 100, "write_ops_limit": 50000},
    ]
    team_reads: list = []
    _wire(page, inv=_inventory(login_methods=1), teams=teams, team_reads=team_reads)
    page.goto(DASHBOARD_URL)
    page.get_by_role("button", name="Billing").click()
    expect(page.get_by_role("heading", name="Billing — Alpha")).to_be_visible()
    expect(page.get_by_text("free plan")).to_be_visible()
    select = page.get_by_label("Billing team")
    expect(select).to_be_visible()
    expect(select.locator("option")).to_have_count(2)
    # switch — the ?team_id= pin must reach the API and the card re-renders
    with page.expect_response(lambda r: "/v1/team" in r.url and "team_id=team_b" in r.url,
                              timeout=15000):
        select.select_option("team_b")
    assert "team_b" in team_reads
    expect(page.get_by_role("heading", name="Billing — Bravo")).to_be_visible()
    expect(page.get_by_text("Pro plan")).to_be_visible()
    expect(page.get_by_text("100")).to_be_visible()  # write_ops_used re-hydrated


def test_billing_team_context_single_team(page: Page):
    """#1876 single-team: team name shown, no empty selector."""
    _seed(page)
    _wire(page, inv=_inventory(login_methods=1))
    page.goto(DASHBOARD_URL)
    page.get_by_role("button", name="Billing").click()
    expect(page.get_by_role("heading", name="Billing — E2E")).to_be_visible()
    expect(page.get_by_label("Billing team")).to_have_count(0)
```
(The `_wire` /v1/team handler must pass through extra team fields — extend the handler to `{**t, "anon": False}` so billing fields reach the card.)

**Step 2:** Run to confirm RED:
Run: `RUN_DASHBOARD_E2E=1 TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/e2e/test_dashboard_identity.py::test_billing_team_context -q`
Expected: FAIL (no "Billing — Alpha" heading).

## Task 2: Implement the dropdown

**Intent:** The Billing tab states its team and allows in-tab switching.
**Acceptance:** Heading "Billing — {team}"; select renders for multi-team, hidden for single-team; switch re-hydrates + stays on Billing; full suite green.

**Files:**
- Modify: `website/apps/dashboard/src/main.jsx` (billing header ~4406–4412)
- Modify: `website/apps/dashboard/src/index.css`
- Test: `tests/e2e/test_dashboard_identity.py`

**Step 1:** Replace the Billing header row:
```jsx
            <div className="row">
              <h2>Billing — {currentTeamName || 'this team'}</h2>
              {/* #1876: per-tenant billing — in-section context selector
                  (reuses switchTeam; single-team users get the name only). */}
              {teams.length > 1 && (
                <select
                  className="billing-team-select"
                  aria-label="Billing team"
                  value={currentTeamId || ''}
                  onChange={(e) => { switchTeam(e.target.value); setTab('billing') }}
                >
                  {teams.map((t) => (
                    <option key={t.team_id} value={t.team_id}>{t.team_name}</option>
                  ))}
                </select>
              )}
              {canManageSubscription && (
                <button className="tier-badge tier-manage" onClick={manageBilling} disabled={billingPending}>
                  {billingPending ? 'Opening portal…' : 'Manage subscription'}
                </button>
              )}
            </div>
```

**Step 2:** CSS (`index.css`):
```css
.billing-team-select {
  background: var(--surface, #0f172a); color: var(--text, #e2e8f0);
  border: 1px solid var(--border, #334155); border-radius: 6px;
  padding: 4px 8px; font-size: 13px; cursor: pointer;
}
/* #1876 review P1-2: the billing header row must wrap at narrow widths
   (`.row` has no flex-wrap — with the select + Manage-subscription button
   a 375px viewport would overflow). */
.billing .row { flex-wrap: wrap; gap: 8px; }
```

**Step 3:** Build + run the e2e:
Run: `cd website/apps/dashboard && npm run build`, serve dist on :8790, then
`RUN_DASHBOARD_E2E=1 TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/e2e/test_dashboard_identity.py -q`
Expected: **13 passed** (11 existing + billing context + single-team branch). Also extend `test_no_horizontal_scroll_narrow_viewport` to click Billing at 375px and re-assert no overflow (covers the wrapped header row).

**Step 4:** Commit via `commit-workflow` (commit the rebuilt dist — the repo deploys it directly).
