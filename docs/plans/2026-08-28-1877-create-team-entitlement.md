<!-- research-path: issue #1877 scoping comments (### Axis Research + verification cycles) — standalone, no epic brief -->

# #1877 — Create-Team UI + 1-Free-Team-Per-Person Entitlement Gate

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Let users create additional teams from the dashboard ("Create new team" in the account menu) with a per-person entitlement — at most one team without a paid subscription; a second requires a paid plan on that team (per-team billing), surfaced as a gated-on-click upgrade interaction.

**Team:** organisation-design-team
**Role:** product-implementer

**Architecture:** Mode-aware free-team-count helper + 402 gate in `POST /v1/teams` (both lanes) and `POST /v1/onboarding/team` (the verified P0 bypass — `create_onboarding_team` had NO cap); UI: "Create new team" entry in the account menu (the #1874 menu is on origin/main), a create dialog, and a gated-on-click 402 → upgrade CTA to Billing.

### Pattern Research

> **Findings date:** 2026-08-28

> Gate skipped: plan touches zero third-party dependencies (FastAPI/Supabase/FalkorDB in-repo; no new libraries). Prior UX research consumed from the #1877 scoping `### Axis Research`:
> - **Create-workspace entry placement** [canonical]: Vercel/Linear/Notion put "Create/Add workspace" at the bottom of the workspace switcher dropdown; shadcn multi-tenant navbar block has an org switcher with "Create New"; VibeWeek: "Create new workspace" CTA at the bottom of the switcher.
> - **Per-tenant billing gates** [canonical + pitfalls]: entitlement gates that say "upgrade" at the point of action (not signup) are the standard B2B pattern; the anti-pattern is a silent 403/500 (userpilot: burying primary actions kills activation). 402-with-upgrade-message matches the repo's existing invite-gate precedent (hosted_api.py ~6252 "Invites require the Team tier").
> - **Dialog copy** (user decision): "upgrade a team, then create" — the new team doesn't exist until the gate passes; the CTA lands on the existing team's Billing (#1876's team selector).
> - **MANDATORY:** for any NEW UX decision, web_search UX best practices before deciding; record in the UX Design Decisions table.

### Integration Surface Map

| Surface | Test Layer | Expected Verification |
|---------|-----------|----------------------|
| POST /v1/teams gate (both lanes) | integration | 0 teams OK; all-paid OK; 1 free → 402 upgrade message; free+paid → 402; 409/429 preserved; gate ordering 429→409→402 |
| POST /v1/onboarding/team gate (both lanes) | integration | free-capped session user → 402 (the P0 bypass closed) |
| count_active_free_memberships helper | integration | mode-aware (supabase subscription_status / selfhost tier='free'), active-only, excludes removed |
| Create-team dialog + menu entry (main.jsx) | e2e | entry visible (single-team too), validation matches API, 402 → upgrade CTA, success → switchTeam |

### Journey Test Map

**Journey: "I want a second team"**
1. Account menu → "Create new team" → **Acceptance:** dialog opens → **Test:** e2e create-team
2. Type a name + submit → **Acceptance:** validation matches the API (≤64, [a-zA-Z0-9_-]); free-capped → upgrade message + CTA → **Test:** e2e gate
3. (Eligible) Submit → **Acceptance:** dashboard switches to the new team → **Test:** e2e success

### Failure Modes
- **Free+paid → 402**: the new team would start Free (2 free teams) — blocked; CTA says "upgrade a team, then create" (verified scope).
- **Duplicate name**: 409 preserved (before the 402 — ordering pinned).
- **Abuse**: 429 rate-limit preserved (first).
- **Onboarding bypass**: create_onboarding_team gated too (P0 fix).

### UX Design Decisions

Research-backed + user decisions (2026-08-28); no new decisions requiring fresh research (the entry-placement + gate-on-click patterns are settled):

| # | Decision | Choice | Rationale |
|---|---|---|---|
| 1 | Entry placement | "Create new team" at the bottom of the account-menu switch section, visible for single-team users too | Vercel/Linear/Notion switcher-bottom placement (canonical); the switch label stays hidden for ≤1 team (keeps #1874's single-team test green) |
| 2 | Gate UX | Gated-on-click: 402 (string detail) → message + "Upgrade" CTA → Billing tab | User decision; the repo's invite-gate precedent |
| 3 | Dialog copy | "upgrade a team, then create" on the 402 state | The new team doesn't exist until the gate passes |
| 4 | Validation | Mirror the API in the dialog (≤64 chars, [a-zA-Z0-9_-], spaces rejected) | Client/server parity, immediate feedback |

### Verification Plan

- **Integration:** tests for the helper + gate matrix (0 teams OK, all-paid OK, 1 free 402, free+paid 402, duplicate-name 409 before 402, rate-limit 429 first, onboarding bypass 402) — the existing `tests/test_writer_inventory.py` TestCreateTeam/TestOnboardingTeam harnesses (FakeControlPlane + team_client/user_client fixtures) + registry-lane pattern.
- **e2e:** create-team dialog + gate in `tests/e2e/test_dashboard_identity.py` (harness: query-tolerant `_wire` with `teams`/`team_reads`; add a POST /v1/teams handler).
- **Run:** `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_writer_inventory.py tests/test_user_identity_authority.py -q` + `RUN_DASHBOARD_E2E=1 ... pytest tests/e2e/test_dashboard_identity.py -q`.

**Tech Stack:** Python 3.12, FastAPI, Supabase/FalkorDB, React 19 (JSX), Playwright e2e.

---

## Task 1: Backend helper (TDD)

**Intent:** The mode-aware free-team-count seam both endpoints use.
**Acceptance:** `count_active_free_memberships` counts active memberships in teams without an active subscription (supabase) / tier='free' (selfhost); excludes removed/invited.

**Files:**
- Modify: `tortoise/supabase_control.py` (supabase twin, near membership_count_since ~1889), `tortoise/hosted_api.py` (mode-aware wrapper)
- Test: `tests/test_writer_inventory.py` (or a new focused test)

**Step 1 — Failing tests:** supabase lane (FakeControlPlane): a free team + a paid team → count 1; two free → 2; removed membership excluded; past_due/trialing teams → NOT counted (count 0); a dangling membership (no teams row) → skipped (team_by_id None → not counted, no 500); non-UUID user_id → 0 without querying (shape-gate, #1719). Registry lane (`test_hosted_api.py` env-flip fixture): tier='free' counted, tier='pro' not.

**Step 2 — Implement:** supabase: query active memberships for the user, join team subscription_status via `team_by_id`, count those not in the active set (`{"active", "past_due", "trialing"}`). Registry: Cypher `MATCH (m:Membership {user_id:$uid, status:'active'}) WHERE m.team_id <> '' MATCH (t:Team {id:m.team_id, tier:'free'}) RETURN count(m)`.

**Step 3 — Green.**

## Task 2: Gate POST /v1/teams (both lanes)

**Intent:** Creation blocked when the user already has any active free-team membership.
**Acceptance:** order 429 → 409 → 402 (string detail) → provision; free+paid → 402.

**Files:**
- Modify: `tortoise/hosted_api.py` (create_team ~5925)
- Test: `tests/test_writer_inventory.py` TestCreateTeam

**Step 1 — Failing tests:** supabase lane (test_writer_inventory.py TestCreateTeam): 1 free → 402 with the upgrade message; free+paid → 402; all-paid (incl. past_due/trialing) → 200; 0 teams → 200; duplicate name on a free-capped user → 409 (not 402); rate-limit → 429 first. **Registry lane (test_hosted_api.py — test_writer_inventory is supabase-only by design):** free-capped → 402 BEFORE team_create (assert no Team node minted); free-capped + dup name → 409 (not 402); 429-first.

**Step 2 — Implement:** in the supabase lane, after the 409 check and before provision: if `count_active_free_memberships(user)` ≥ 1 → `raise HTTPException(402, "Create another team requires a paid plan — upgrade an existing team first")`. Registry lane: same, after its 429/409 handling (add a registry dup-name pre-check before the 402 to preserve 429→409→402 ordering).

**Step 3 — Green.**

## Task 3: Gate POST /v1/onboarding/team (both lanes) — the P0 bypass

**Intent:** The onboarding lane had no cap — close it.
**Acceptance:** a free-capped session user calling create_onboarding_team → 402.

**Files:**
- Modify: `tortoise/hosted_api.py` (create_onboarding_team ~9839)
- Test: `tests/test_writer_inventory.py` TestOnboardingTeam

**Step 1 — Failing test:** free-capped user → 402 (currently 200) — both lanes (test_writer_inventory.py supabase + test_hosted_api.py registry).
**Step 2 — Implement:** same gate after the owner-user check, before provision in both lanes. **Shape-gate (#1719 review P2):** the onboarding key-auth branch can carry a non-UUID `created_by` (anon-/reg-* shapes) — the helper's supabase query would 500 on a non-UUID user_id. The helper itself shape-gates (non-UUID → 0); additionally extend the owner check to reject non-UUID created_by (mirrors the #1719 shape tree).
**Step 3 — Green + regression (TestCreateTeam/TestOnboardingTeam existing tests stay green).**

## Task 4: UI — menu entry + dialog + gated-on-click

**Intent:** The dashboard surface for creating teams.
**Acceptance:** entry in the account menu (single-team visible); dialog validates like the API; 402 → upgrade message + CTA to Billing; success → switchTeam.

**Files:**
- Modify: `website/apps/dashboard/src/main.jsx` (account menu ~3846, state near the menu, modal markup)
- Modify: `website/apps/dashboard/src/index.css`
- Test: `tests/e2e/test_dashboard_identity.py` (add a POST /v1/teams handler to `_wire`)

**Step 1 — e2e tests:** `test_create_team_success` (entry → dialog → name → submit → switches to the new team; mock POST /v1/teams → 200 + teams list gains the team) + `test_create_team_free_capped_gate` (mock POST /v1/teams → 402 → upgrade message + CTA visible) + **single-team visibility** (single-team `_wire` → open menu → "Create new team" visible while the switch label stays hidden — the distinguishing UX claim).
**Step 2 — Implement:** a `createTeamOpen` state + dialog (name input, validation mirroring the API), and the "Create new team" button placed AFTER the `teams.length > 1` switch block (UNCONDITIONAL — it's the sole entry for single-team users, review P2), submit → `POST /v1/teams` → success → `loadTeams()` + `switchTeam(team_id)`; 402 → inline upgrade state with a CTA → `setTab('billing')`.
**Step 3 — Build + run the e2e suite** (identity 11 + 2 new; narrow-viewport with the new menu button still fine).
**Step 4 — Commit via `commit-workflow`** (commit the rebuilt dist).
