<!-- research-path: issue #1875 scoping comments (4 verification cycles — authoritative) — standalone, no epic brief -->

# #1875 — Invite Tier Fix (Pro=2) + In-App Pending Invites

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Fix the invite tier gate so Pro teams can invite one member (max 2 users) and Team invites unlimited — today `POST /v1/invites` 402s every tier below Team — and surface pending invites (list + accept + decline) in the dashboard account menu.

**Team:** organisation-design-team
**Role:** product-implementer

**Architecture:** Backend tier/capacity gate (both lanes), invitee-side pending/accept/decline endpoints reading the AUTHORITATIVE invitations source (NOT team_memberships — cycle-1 P1), a token-less accept (cycle-3 P1), the #1880 ghost-cleanup helper in decline, and a dashboard account-menu "Invites" section.

### Pattern Research

> **Findings date:** 2026-08-28

> Gate skipped: zero third-party dependencies (FastAPI/Supabase/FalkorDB in-repo). UX research consumed from the #1875 scoping `### Axis Research`:
> - **Team capacity by tier** [canonical]: tier-capped seat counts with in-product upgrade prompts at the capacity boundary (Vercel/Linear; the repo's pricing config free=1, solo=1, pro=2, team=∞ — the backend gate must match it).
> - **Pending-invites surface** [canonical]: invitees expect a visible pending-invites list in the account/workspace switcher (Slack/GitHub/Notion workspace-switcher precedent) — the account menu is the established home.
> - **MANDATORY:** any NEW UX decision → web_search first; record in the UX Design Decisions table.

### Integration Surface Map

| Surface | Test Layer | Expected Verification |
|---------|-----------|----------------------|
| POST /v1/invites gate (both lanes) | integration | Free/Solo → 402 upgrade; Pro → 1 allowed then blocked at capacity; Team unlimited; capacity = active members + pending invitations (authoritative source, both modes); None-skip for Team |
| POST /v1/invites/accept free-cap (both accept paths) | integration | invitee with a free team blocked when the target team lacks an active subscription (mode-aware #1877 helper) |
| GET /v1/invites/pending (new) | integration | invitations/Invitation records, email-filtered, pending + not expired, team name + inviter + expires_at, both modes |
| POST /v1/invites/pending/{id}/accept (new, token-less) | integration | email-match authz, reuses accept internals (NOT the unguarded registry SDK method) |
| DELETE /v1/invites/pending/{id} (new) | integration | email-match authz, idempotent, calls _delete_fake_invite_membership (#1880) |
| Account-menu Invites section (main.jsx) | e2e | list renders, accept lands on the team, decline removes, empty state hides, renders res.detail (not the hardcoded main.jsx:2480 message) |

### Journey Test Map

**Journey: "I was invited to a team"**
1. Open the account menu → "Invites" → **Acceptance:** pending invites list with team name + inviter → **Test:** e2e pending invites
2. Accept → **Acceptance:** lands on the team (switchTeam) → **Test:** e2e accept-from-list
3. Decline → **Acceptance:** invite removed, no ghost member → **Test:** e2e decline + registry integration

### Failure Modes
- **Token-less accept**: the pending list cannot carry a token (hash-only storage) — accept must be token-less with email-match authz (cycle-3 P1).
- **Capacity source**: active members + PENDING INVITATIONS (authoritative) — never team_memberships(status='invited') (cycle-1 P1: supabase never writes those rows; registry leaves stale fakes).
- **Decline backend**: net-new invitee revoke (cycle-1 P1 — no invitee-side decline existed).
- **Free-cap on accept**: mode-aware #1877 helper; only when the target team lacks an active subscription (cycle-2 P2).
- **Existing test breakage**: `test_free_tier_402` asserts "Team tier" in detail — message changes → update (cycle-2 P2).

### UX Design Decisions

Research-backed + user decisions (2026-08-28); no new decisions requiring fresh research (the account-menu placement + gate-on-click are settled):

| # | Decision | Choice | Rationale |
|---|---|---|---|
| 1 | Pending-invites surface | Account-menu "Invites" section (list + Accept/Decline) | Slack/GitHub/Notion workspace-switcher precedent; user: "pending invites in the account menu" |
| 2 | Invite gate UX | Members-tab invite input stays visible; 402/at-capacity renders res.detail + CTA | Gate-on-click (user decision); the hardcoded main.jsx:2480 message is replaced |
| 3 | Accept/Decline | Token-less endpoints authz by email match | The list cannot carry tokens; decline mirrors accept's email guard |
| 4 | Pro capacity | active members + pending invitations < 2 | Authoritative invitations source (both modes) |

### Verification Plan

- **Integration:** invite gate matrix (free/solo 402 upgrade, pro 1-then-blocked, team unlimited, capacity-after-accept regression), accept free-cap, pending authz (own invites only), token-less accept authz + consumed-invite error, decline authz/idempotency — in `tests/test_invites_http.py` (+ the #1880 decline-ghost test).
- **e2e:** account-menu Invites section (list/accept/decline/empty) in `tests/e2e/test_dashboard_identity.py`.
- **Run:** `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_invites_http.py tests/test_free_team_entitlement.py -q` + the dashboard e2e.

**Tech Stack:** Python 3.12, FastAPI, Supabase/FalkorDB, React 19 (JSX), Playwright e2e.

---

## Task 1: Invite tier + capacity gate (both lanes)

**Intent:** Pro can invite 1 member; Team unlimited; Free/Solo upgrade-gated.
**Acceptance:** the pricing gate matches free=1, solo=1, pro=2, team=∞; capacity counts active members + pending invitations (authoritative) in both modes; Team None-skip.

**Files:**
- Modify: `tortoise/hosted_api.py` (invite_to_team ~6296)
- Modify: `tortoise/supabase_control.py` (a pending-invitations count helper if needed)
- Test: `tests/test_invites_http.py`

**Step 1 — Failing tests:** free/solo → 402 with the UPGRADE message; pro → 1 invite OK, 2nd blocked; team → unlimited; supabase pro lane; capacity counts active + pending (a consumed invite frees the seat); expired pending invites NOT counted. **Existing test updates named:** `test_free_tier_402` (asserts `"Team tier" in detail` @ ~175) + the module docstring (@ ~9) updated for the new message.

**Step 2 — Implement:** replace `tier != "team"` checks with: `tier in (free, solo)` → 402 upgrade; `tier == "pro"` → capacity = active members (supabase: team_memberships active; registry: Membership active) + pending invitations (supabase: invitations pending+not-expired; registry: Invitation nodes pending) < max_users(2) → else 402 at-capacity message; `tier == "team"` or max_users None → unlimited. Supabase branch gains the capacity check (had none); registry replaces `_check_team_limit(limits, "users")` (active-only) with the active+pending count for Pro.

**Step 3 — Green.**

## Task 2: Pending / accept / decline endpoints (new)

**Intent:** Invitee-side surfaces reading the authoritative invitations source.
**Acceptance:** GET /v1/invites/pending (own pending, both modes); POST /v1/invites/pending/{id}/accept (token-less, email-match, reuses a SHARED accept internal with all checks preserved, consumed-invite → clear error); DELETE /v1/invites/pending/{id} (email-match revoke + #1880 ghost cleanup).

**Files:**
- Modify: `tortoise/hosted_api.py` (three new endpoints + a SHARED registry accept internal used by BOTH the token branch and the by-id branch — resolves the review P2s: ghost cleanup, preserved checks, free-cap on both entry points, consistent semantics)
- Modify: `tortoise/supabase_control.py` (new email-scoped helpers: pending-by-email, accept-by-id, decline-by-email)
- Test: `tests/test_invites_http.py` (both modes)

**Step 1 — Failing tests:** pending list (own invites only, BOTH modes, expired excluded); token-less accept (email-match authz + consumed error, BOTH modes); decline (authz + idempotent + ghost row gone, BOTH modes); expired-invite reject on by-id accept.

**Step 2 — Implement:**
- **Shared registry accept internal** (used by the token branch AND by-id): preserves the FIVE checks (pending-status rejection, expiry, email-match, existing-membership 409, max_users quota gate), the free-cap pre-check (Task 3), marks accepted → membership_create → `_delete_fake_invite_membership` (#1880 — success AND the 402 path, mirroring the token branch), single-use semantics. The by-id accept pre-checks the free-cap BEFORE the accepted_at write (NON-consuming — the invitee can leave their free team and re-accept; documented divergence from the token branch's consumed-on-402).
- **Supabase email-scoped seams**: pending-by-email (invitations where email = session email, status pending, not expired, join team name + inviter + expires_at), accept-by-id (id-keyed, preserving the invitation_accept checks incl. the max_users quota gate ~1015), decline-by-email (email-scoped revoke).
- Register the decline route before the generic /v1/invites/{invitation_id} (convention, not correctness — cycle-4 P3).

**Step 3 — Green + token-accept regression:** `tests/test_invites_email_http.py` (stashed-flow + invite-accept.html token accept) + the E2E-3 registry mint→accept→role flow must stay green — the shared internal refactor touches the token branch.

## Task 3: Accept-side free-cap (both modes, both registry entry points)

**Intent:** "One free team per person" holds on the join side.
**Acceptance:** a free-capped invitee accepting into a team without an active subscription → blocked (mode-aware #1877 helper); joining a subscribed team always allowed. The free-cap applies to BOTH registry accept entry points (token + by-id) via the shared internal, and to the supabase invitation_accept core (before the single-use PATCH — already safe).

**Files:**
- Modify: `tortoise/hosted_api.py` (the shared registry accept internal), `tortoise/supabase_control.py` (invitation_accept core)
- Test: `tests/test_invites_http.py`

**Step 1 — Failing tests:** free-capped invitee + target team no sub → 402/blocked (both modes); subscribed target → OK; by-id + token registry paths behave identically.

**Step 2 — Implement:** in the shared internal + the supabase core, when the target team lacks an active subscription (supabase: subscription_status not in the active set; registry: tier='free'), check the invitee's `_count_active_free_memberships` ≥ 1 → blocked with a clear message. Supabase: before the PATCH (non-consuming). Registry by-id: before the accepted_at write (non-consuming). Registry token: matches its existing consumed-on-402 precedent (documented).

**Step 3 — Green.**

## Task 4: Dashboard Invites section + gate message (UI)

**Intent:** The account menu shows pending invites; the Members-tab invite gate renders the API message.
**Acceptance:** "Invites" section (list + Accept/Decline), empty state hides, renders res.detail; the hardcoded main.jsx:2480 message replaced.

**Files:**
- Modify: `website/apps/dashboard/src/main.jsx` (account menu + Members-tab invite submit)
- Modify: `website/apps/dashboard/src/index.css`
- Test: `tests/e2e/test_dashboard_identity.py` (+ the harness gets /v1/invites/pending + accept/decline mocks)

**Step 1 — e2e tests:** pending list renders in the menu; accept → lands on the team; decline → removed; empty state hides; Members-tab invite 402 renders res.detail.

**Step 2 — Implement:** an "Invites" section in the account menu (fetch on open, list team name + inviter, Accept/Decline buttons); the Members-tab invite submit surfaces `res.detail` on 402 instead of the hardcoded string (@ ~2623), AND the STATIC Members-tab notice (@ ~4458 — `Invites require the Team tier — upgrade to add teammates.` which renders for Pro too) is gated to `tier !== 'pro' && tier !== 'team'` with the new wording (review P2: Pro can invite up to capacity; the old notice contradicted the working invite form).

**Step 3 — Build + run the e2e suite; commit via `commit-workflow` (dist committed).**
