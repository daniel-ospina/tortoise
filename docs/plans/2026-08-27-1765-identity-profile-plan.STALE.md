<!-- research-path: docs/plans/2026-08-26-identity-linking-solution-approaches.md (divergence) -->

# Issue #1765 — Identity profile + recovery banner: convergence & implementation plan

> **For Pi:** use `executing-plans` to implement this plan task-by-task.

**Goal:** Ship a session-authed profile page (add GitHub/Google/email+password login methods over time + set username) and a dashboard recovery banner for single-login-method users, with server-owned identity gates — without any identity flow writing `teams.email`.

**Architecture:** Server-authority core (`GET /v1/user/identity` inventory + `/v1/user/identity/link-intent|link-commit|unlink` mutations), phase-sliced delivery (P1 read-only inventory + banner + username ships first; P2 linking gated by the `enable_manual_linking` capability probe). GoTrue remains the only identity-row executor (vendored supabase-js `linkIdentity`, OTP, `updateUser({password})` — never admin-create, C6). The unlink floor is atomic (per-user row lock held across the GoTrue delete + post-state re-check). The `teams.email` invariant ("identity flows never write `teams.email`") is enforced and tested; the conditional demotion (drop `uq_teams_email`) is a separate probe-gated follow-up, NOT part of this delivery.

**Team:** organisation-design-team · **Role:** platform

### Pattern Research

Skipped — zero new third-party dependencies. supabase-js 2.112.2 is vendored (C13, `linkIdentity` unused, verified present in `website/apps/dashboard/public/vendor/supabase-2.112.2.min.js`). GoTrue manual-linking behavior verified via web (Supabase docs `auth-identity-linking`; `linkIdentity` returns `422 manual_linking_disabled` when `GOTRUE_SECURITY_MANUAL_LINKING_ENABLED=false`, config.toml:173 = false locally).

### Integration Surface Map

| Surface | Test layer | Bug pattern flags |
|---|---|---|
| `GET /v1/user/identity` (session JWT → service-role reads) | pytest (FakeControlPlane) + e2e hosted | leak across users (must be auth.uid()-bounded); agent-principal exclusion (C10); registry-mode drift |
| `POST /v1/user/identity/link-intent` (re-auth freshness, signed nonce) | pytest unit (intent expiry/replay) | replay, nonce reuse, session-rotation mid-intent |
| `POST /v1/user/identity/link-commit` (verify new identity row, ownership, audit) | pytest + e2e mocked GoTrue | accepting a stale/pre-existing identity row (must be new since intent) |
| `POST /v1/user/identity/unlink` (row lock + floor + GoTrue delete + re-check) | pytest two-tab simulation (threading lock) + pgTAP floor RPC | LAST_METHOD race; server-as-user-agent scope creep |
| `PATCH /v1/profile/username` (uniqueness pre-check + admin seam write) | pytest + SQL unique-index backstop | display_name clobber (#1691); cross-user collision |
| Banner predicate (pure JS) | `node --test` (sessionKey.js precedent #1708) | anon/keyless false positives; stale inventory |
| `link_intents` table + `identity_floor_lock` RPC | supabase/tests PGlite (pgTAP-style `tests.assert`) | service_role-only enforcement; RLS deny-by-default |

### Journey Test Map

1. **User with one login method sees the recovery banner and fixes it**
   - Step: log in with GitHub only → **Acceptance:** banner shows "Add another login method"; CTA routes to Profile tab → **Test:** `tests/e2e/test_dashboard_profile.py::test_single_method_banner_routes_to_profile`
2. **User adds a second method from the profile**
   - Step: Profile → Add Google → **Acceptance:** identity row appears in inventory; banner clears → **Test:** `tests/e2e/hosted/test_14_profile.py` + `tests/test_user_identity_authority.py`
3. **User adds email+password**
   - Step: Profile → Add email+password → **Acceptance:** post-impl verification passes (signOut → signInWithPassword → same uid); `password_capable=true`; no `team_id=''` placeholder row (C6) → **Test:** `tests/test_user_identity_authority.py::test_add_password_no_placeholder_fires`
4. **User sets a username**
   - Step: Profile → username field → **Acceptance:** `user_metadata.username` set; precedence `username > display_name > email-prefix`; duplicate → 409 → **Test:** `sessionKey.test.js`-style node test + `tests/test_user_identity_inventory.py`
5. **User removes the second method**
   - Step: Profile → Remove Google → **Acceptance:** floor ≥1 enforced; unlinking the last method → 409 LAST_METHOD with guidance → **Test:** `tests/test_user_identity_authority.py::test_unlink_last_method_refused`

### Failure Modes

- `enable_manual_linking` off (hosted) → **Expected:** capability probe → 422 `manual_linking_disabled` → add-method UI hidden, fail-closed message, banner "contact support" → **Test:** `tests/test_user_identity_authority.py::test_422_probe_fail_closed`
- GoTrue re-auth 422 on unlink → **Expected:** REAUTH_REQUIRED → client re-auth round → retry → **Test:** `tests/test_user_identity_authority.py::test_reauth_required_round`
- Two tabs unlink concurrently → **Expected:** exactly one succeeds (row lock + floor re-check) → **Test:** `tests/test_user_identity_authority.py::test_unlink_two_tab_race`
- Registry/selfhost mode → **Expected:** 400 hosted-only (claim precedent) → **Test:** `tests/test_user_identity_inventory.py::test_registry_mode_400`
- Newly-linked email equals another team's `teams.email` → **Expected:** adoption signal surfaced on profile; `teams.email` untouched (invariant) → **Test:** `tests/test_user_identity_authority.py::test_teams_email_invariant_on_link`

**Tech Stack:** Python 3.12 (FastAPI hosted_api), supabase-js 2.112.2 vendored, React single-file dashboard (main.jsx), Postgres (PGlite test harness), pytest + Playwright, node --test.

---

## 1. Problem statement (confirmed)

Identity facts are conflated across three tables:

1. **`teams.email`** — a globally-unique TEAM attribute (`uq_teams_email` partial unique index, 20260813000004:107) used as the signup idempotency key (`team_by_email`, hosted_api:3007) and written by identity-ish flows (claim_membership Step 6, onboarding PATCH :8178-8210).
2. **The user anchor** — `team_memberships.user_id` (nullable FK to auth.users, 0009) + GoTrue `auth.identities` (not browser-queryable without RLS/RPC, C14).
3. **`api_keys.created_by`** — mixed attribution (bootstrap-NULL, agent principals, real user ids, C10).

Every identity-adding operation collides with team-scoped uniqueness. **User deliverables:** (1) profile page — add login methods (GitHub/Google/email+password over time) + set username; (2) dashboard recovery banner for single-login-method users routing to the profile page.

**Demotion is CONDITIONAL** on falsification probes; **DEFAULT FALLBACK = bounded visibility slice off GoTrue `auth.identities` + credential-inventory predicate.** This plan implements the fallback (bounded slice via a server-owned inventory surface) and runs the probes; the demotion is a separate gated follow-up issue.

## 2. Convergence — evaluation of the four families

Scored on the six required axes. (Diff size and speed explicitly excluded.)

| Axis | 1. CLIENT-FIRST (SDK-native tab + `get_my_identity_inventory()` RPC) | 2. SERVER-AUTHORITY (`/v1/user/identity` + `/v1/profile`) | 3. DATA-PLANE (SECURITY DEFINER view + standalone page) | 4. SCHEMA-FIRST (user_emails mirror + demote-or-invariant) |
|---|---|---|---|---|
| **Outcome quality** | Full deliverable; banner is client-pure → can lie on stale data; gates advisory | Full deliverable; banner + profile from one authoritative source; team-aware | Full deliverable visually; banner/floor are the weakest (pure client function over a view) | Best long-term model, but same UX as 2; C1's churn buys nothing visible |
| **Edge-case handling** | TOCTOU two-tab unlink race persists; stale inventory; popup/session interplay is client's problem | Row lock + post-state re-check closes the two-tab race; intent TTL/replay/session-rotation handled; GoTrue re-auth complement documented | View handles reads only; unlink still client-side with client floor; `reg-` identity rows with no auth.users (C15) can confuse a view | Mirror trigger drift window + backfill job; C1 one-way door needs dedup pass |
| **Failure-mode coverage** | 422 probe fail-closed is shared; but client gates bypassable (compromise of client JS = advisory); GoTrue still enforces verified-email/re-auth at ITS layer | Full audit trail (`audit_events.detail`); every gate server-owned; rate limits (claim precedent); replay-safe intents | No server gates; relies entirely on GoTrue's own enforcement; a bug in identity.js is a security bug | Same flow security as 1/2 (orthogonal); adds trigger-drift failures |
| **Future extensibility** | Tab-driven profile is extensible; client RPC inventory does not scale to team-aware inventory | `/v1/user/identity` is the natural home for future identity features (rename, email change, MFA, security keys); gates evolve server-side without shipping JS | Zero backend surface; every future identity feature = client change + new view | Mirror is the permanent C14 fix; but the flow security still needs 2's mechanics |
| **Security posture of gates** | Advisory client gates; the ONE gate GoTrue does not enforce (identity-count floor on unlink) is client-side → a buggy/malicious client can drop the last method | Strongest: atomic floor held by the code that performs the removal; re-auth verified server-side; audit | Weakest: floor is a client pre-read; nothing server-side beyond GoTrue's own | Same as 1/2 for flows; the data model adds no gate security |
| **Delivery risk** | Low; P2 blocked on external toggle (C5) | Medium; intent state machine + server-as-user-agent, but P1 ships first with low risk | Low to ship; ships the weakest security and a second entry page (C16 parity cost) | C1: highest blast radius (one-way door, migration train); C2 low but invariant needs guarding |

**Verdict.** GoTrue itself enforces verified-email-only linking and (provider, provider_id) keying, and GoTrue enforces recent-login on identity deletion — so family 1's and 3's client gates are not catastrophic. But the two gates GoTrue does NOT enforce are precisely the ones that matter most to this product: (a) the **identity-count floor on unlink** ("never leave zero ways in") and (b) the **recovery banner's truthfulness** (users act on it; a client-computed banner over an RPC can be stale or fail silently). Both need the server.

## 3. Decision

### Winner: **Family 2 (SERVER-AUTHORITY) core, delivered in Family-1's phase slices, with the Family-4 C2 invariant adopted and Family-4 C1 demotion demoted to a probe-gated follow-up.**

Concretely:

- **P1 (ships alone, read-only):** `GET /v1/user/identity` (server-computed inventory: `auth.identities` via the existing `_gotrue_admin_get_user` seam, password-capability via `encrypted_password` (#2085/C4), keys by `created_by` across the user's memberships (C10 exclusions)) → **banner** (pure JS predicate over the server inventory — node --test-able) → **profile tab** (read-only method list + username editor).
- **P2 (gated by the capability probe):** `link-intent` / `link-commit` (re-auth freshness, signed intent), add-OAuth (GoTrue `linkIdentity` popup), add-email+password (`updateUser({password})` + post-impl verification; NEVER admin-create — C6), `unlink` (atomic floor: per-user row lock in `identity_floor_lock` RPC held across the GoTrue delete + post-state re-check; audit via `audit_events.detail`).
- **Invariant (always):** identity flows never write `teams.email` — enforced by design (no code path) + test + lint guard. The claim path's Step-6 write stays (documented anon-adoption exception); it is re-pointed ONLY on the demotion branch.
- **Probes (Phase 0):** falsification probes 1–4 run as a prerequisite; the demotion (drop `uq_teams_email`, re-anchor signup idempotency) becomes a SEPARATE follow-up issue fired iff probe-1 ≥ 1 pair OR probe-4 ≥ 1 user (internal/test emails excluded).

### Why this combination

1. **The atomic floor cannot be client-side.** Family 1's server RPC backstop is a pre-state read — the two-tab race survives (both tabs read floor=2, both delete). Family 2's design closes it: the RPC that acquires the lock is the RPC that computes the floor, and the transaction stays open across the GoTrue delete with a re-check before commit. Since the inventory surface must be built anyway (banner authority), the marginal cost of owning the floor server-side is small — this is why the server core, not the client-first tab, wins.
2. **Banner authority.** The banner must be team-aware (keys-by-`created_by` requires a service-role join across `api_keys` + memberships — `api_keys` RLS is GUC-based, not user-based, so a browser-side inventory RPC cannot compute it correctly). The server is the only place that can.
3. **Phasing de-risks C5.** The external `enable_manual_linking` toggle (config.toml:173=false, hosted state unknown) is a hard external dependency for P2 but irrelevant to P1. Phase-slicing ships the user's banner+username value immediately and isolates the linking risk behind the ops flip + capability probe (Family 1's best idea).
4. **The data model stays honest without the one-way door.** Family 4's C1 is probe-gated for a reason (dropping `uq_teams_email` re-anchors the entire signup path — one-way). The C2 invariant gives the same root-cause discipline (identity facts live in `auth.identities`, not `teams.email`) at zero schema risk, and the probes are what will justify C1 if real multi-team demand exists.

### Rejected alternatives — when each WOULD have been better

- **1. CLIENT-FIRST would have been better if:** the hosted `enable_manual_linking` state were already verified ON, the team accepted advisory gate posture for a low-stakes rollout, and there were no multi-team banner requirement. It is the cheapest correct UX; its floor race is the dealbreaker. **Rejected:** the unlink floor and banner authority are exactly where this product's failure classes live (#1691 clobber, conflation bug).
- **3. DATA-PLANE would have been better if:** this were a read-only display feature with zero mutation risk (e.g., "show my login methods") and the product accepted that the profile never manages credentials. The SECURITY DEFINER view is a fine read acceleration — and we reuse its shape for the P1 inventory RPC — but it cannot own unlink, so a credential-management page built on it would be a client-gated credential manager: the exact failure class this issue exists to fix. **Rejected.**
- **4a. SCHEMA-FIRST / C1 (demotion) would have been better if:** the falsification probes confirm real multi-team-per-identity demand AND a migration train is acceptable. It is the "correct" long-term model. It is NOT this delivery: the probes are a Phase-0 prerequisite and the demotion is a separate follow-up issue with its own migration + re-anchor plan. **Deferred, probe-gated.**
- **4b. SCHEMA-FIRST / C2 (mirror table) would have been better if:** the product needed browser-side identity queries at scale or multiple consumers of identity facts (a permanent C14 fix). The trigger-on-`auth.identities` drift risk and backfill job buy nothing for this issue's single consumer (the profile page reads through the server anyway). The mirror is a reasonable future migration; not needed now. **Rejected for this delivery.**

### Open decision carried forward (owner confirmation, not blocking P1)

**Floor semantics:** the banner "ways in" and the unlink floor count STRICT login methods only (distinct identity providers + password-capability). API keys are shown as a separate "credentials" tier on the profile but are NOT counted — a key is a machine credential (losing the browser still locks you out), so it must not make the banner disappear or permit dropping the last identity. Default is strict; confirm with the owner at P2 review.

## 4. Proposed solution — architecture

```
auth.identities ──service-role──> GET /v1/user/identity ──> dashboard banner + profile tab
auth.users.encrypted_password (#2085 password signal)
api_keys.created_by (C10-filtered, across memberships)

P2:
[client] --link-intent{provider}--> [hosted_api]  (session verify → re-auth freshness → signed intent, TTL 120s)
[client] --supabase.linkIdentity(provider)--> [GoTrue]  (OAuth popup; manual linking ON; only GoTrue executes)
[client] --link-commit{intent,provider}--> [hosted_api]  (verify intent + NEW identity row + ownership + audit; never writes teams.email)
[client] --updateUser({password})--> [GoTrue]  (add-email+password; no admin-create; post-impl verification signOut→signInWithPassword→same uid)
[client] --unlink{identity_id}--> [hosted_api]  (identity_floor_lock RPC: row lock + floor → forward DELETE /user/identities/{id} w/ user token → re-check → audit)
[client] --PATCH /v1/profile/username--> [hosted_api]  (uniqueness pre-check → admin update_user → user_metadata.username; unique index backstop)
```

**New backend surface (hosted_api.py):**
- `GET /v1/user/identity` — session-authed (`get_current_user`), hosted-only (`is_supabase_enabled`, 400 otherwise — claim precedent). Returns `{user_id, email, username, display_name, identities[], password_capable, credentials[], per_team[]}`.
- `POST /v1/user/identity/link-intent` — `{provider ∈ {github, google}}`; re-auth freshness (`iat ≥ now − TORTOISE_IDENTITY_REAUTH_WINDOW`, default 3600s, matching GoTrue's recent-login window); rate-limited (claim limiter shape); returns signed intent `{jti, user_id, provider, exp}` (HMAC-SHA256, `TORTOISE_LINK_INTENT_SECRET`, `hmac.compare_digest` discipline per mcp_auth.py).
- `POST /v1/user/identity/link-commit` — `{intent, provider}`; verify signature/expiry/replay (jti consumed in `link_intents`), admin-get user, find the identity row for `provider` created AFTER intent issuance (never accept a stale/pre-existing row), verify it belongs to the session user, verify provider-verified email (C2 — the claim path's `app_metadata.providers` invariant shape), adoption-signal check (new identity email matching another team's `teams.email` → surfaced on profile, never written, never blocked), audit `identity_link` via `_async_audit` (:813, `audit_events.detail`), return refreshed inventory.
- `POST /v1/user/identity/unlink` — `{identity_id}`; session verify + re-auth freshness; call `identity_floor_lock(p_user_id, p_identity_id)` RPC (SECURITY DEFINER, service_role-only — C18): row-locks the user's `auth.users` row and returns `{floor_after}`; if `floor_after < 1` → 409 `LAST_METHOD`; else forward `DELETE {SUPABASE_URL}/auth/v1/user/identities/{identity_id}` with THIS request's validated access token (server-as-user-agent, bounded to this one endpoint, token never stored); on GoTrue 422 reauth → 403 `REAUTH_REQUIRED`; re-read identities in the same transaction, commit, audit `identity_unlink`. Pooling caveat: the lock transaction needs a pinned DB session across the remote call (single-worker deployment precedent; documented in code).
- `PATCH /v1/profile/username` — `{username}`; format validation (3–32 chars, `[a-z0-9_-]`, not an email); uniqueness pre-check (service-role scan of `raw_user_meta_data->>'username'`, excluding self); admin `PUT /auth/v1/admin/users/{id}` merge into `user_metadata`; 409 on collision; audit `profile_username_update`. Backstop: unique partial index on `auth.users ((raw_user_meta_data->>'username')) WHERE ... IS NOT NULL`.

**New SQL (single migration `20260827000001_identity_profile.sql`):**
- `public.link_intents (jti text PRIMARY KEY, user_id uuid NOT NULL, provider text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL, used_at timestamptz)`; RLS deny-all public roles; service_role only (audit_events precedent).
- `identity_floor_lock(p_user_id uuid, p_identity_id text) RETURNS jsonb` — SECURITY DEFINER, service_role-only, `SET search_path=''`: `SELECT ... FROM auth.users WHERE id = p_user_id FOR UPDATE`; floor = `count(auth.identities WHERE user_id=...) + (encrypted_password IS NOT NULL) − 1`; returns `{floor_after, target_provider}`. (`FOR UPDATE` row lock is held by the CALLER's transaction across the GoTrue delete — documented contract.)
- Username unique partial index (above).
- NO changes to `uq_teams_email`, `claim_membership`, `provision_team`, or `teams.email` semantics (C1 demotion is the separate follow-up).

**Frontend (main.jsx, router-less — tab state :273, nav :2641-2649, banner :2624-2634):**
- New `profile` tab (nav addition + `setTab('profile')`), session-gated like billing (the `authMode !== 'session'` gates at :2206/:2226/:2873).
- `identity.js` extracted pure module: `buildInventory(json)`, `waysIn(inventory)`, `showBanner(inventory, teamId)`, `displayName(inventory)` precedence (`username > display_name > email-prefix`, #1691 discipline) — node --test-able (sessionKey.js precedent #1708).
- Banner: renders when `showBanner(...)` (ways_in ≤ 1 AND ≥ 1 method AND team not anon); CTA → `setTab('profile')`. Never shown for anon teams (full-page Protect :2137/:2235 already covers, C8) or keyless-anon cohort (#1716 out of scope; banner must not promise a fix).
- Profile tab: method list (identities + password-capable + credentials tier), Add GitHub/Google (P2, capability-gated), Add email+password (P2), Remove method (P2, floor-gated), username editor (P1).

## 5. Key flows

### 5.1 Banner computation (P1)
1. Dashboard mount with session → `GET /v1/user/identity` (Bearer session token, :348 `api()` helper, `useSession`).
2. Server: `_gotrue_admin_get_user(user_id)` (:3237) → identities; password-capable = `encrypted_password IS NOT NULL` (C4); credentials = `api_keys` where `created_by = user_id` across active memberships, `created_via ≠ bootstrap`-NULL rows attributed membership-wide, agent principals (`st_*`, `api`, NULL) excluded (C10, `mint_target_user_for_key` shape supabase_control.py:684); `per_team[]` = ways_in per membership.
3. Client: `waysIn = identities.length + (password_capable ? 1 : 0)`; `showBanner = sessionMode && team && !team.anon && waysIn >= 1 && waysIn <= 1`.
4. Render banner (:2624 pattern) with CTA → `setTab('profile')`. Recompute after every identity mutation (P2) by refetching the inventory.

### 5.2 Add OAuth (P2)
1. Profile → "Add GitHub/Google" → capability check (click-time probe, §7): if a prior probe recorded `manual_linking_disabled` → hide affordance, fail-closed message.
2. `POST /v1/user/identity/link-intent {provider}` → server re-auth freshness check (`iat` window) → 403 `REAUTH_REQUIRED` → client re-auth round (signInWithPassword) → retry.
3. `supabase.auth.linkIdentity({provider})` → OAuth popup → GoTrue links (manual linking ON).
4. `POST /v1/user/identity/link-commit {intent, provider}` → server verifies intent + NEW identity row ownership + provider-verified email → adoption-signal check (email vs other teams' `teams.email` — surface only) → audit → refreshed inventory.
5. Re-render profile + banner.

### 5.3 Add email+password (P2)
1. Profile → "Add email+password" → client `supabase.auth.updateUser({password})` (never admin-create — C6; no `auth.users` INSERT → `handle_new_user` placeholder never fires).
2. Post-impl verification: `signOut()` → `signInWithPassword({email: currentEmail, password})` → assert `user.id === original uid`.
3. Success → `password_capable = true` in inventory; banner clears. Failure (or the account needs a NEW email identity) → **degrade to verified-identity linking only** (C2): OTP to the target email (`signInWithOtp({email, shouldCreateUser:false})` + verify) — never a second admin-create path.
4. Audit `identity_link_password`; re-render.

### 5.4 Unlink (P2)
1. Profile → "Remove X" → `POST /v1/user/identity/unlink {identity_id}`.
2. Server: `identity_floor_lock` RPC → row lock + floor. `floor_after < 1` → 409 `LAST_METHOD` + guidance ("add another method first"). GoTrue reauth 422 → 403 `REAUTH_REQUIRED` → client re-auth round → retry.
3. Forward `DELETE /auth/v1/user/identities/{id}` with the request's validated access token → post-state re-check in the same transaction → commit → audit `identity_unlink`.

### 5.5 Username (P1)
1. Profile → username field → `PATCH /v1/profile/username {username}`.
2. Server: format → uniqueness pre-check → admin update_user merge into `user_metadata` (single-writer = profile; #1691 wizard keeps writing `display_name` only, main.jsx:578-581 — untouched) → 409 on collision (pre-check OR the unique-index backstop, mapped to 409).
3. Display precedence `username > display_name > email-prefix` via the shared `displayName()` helper everywhere usernames render (helper + test prevent a one-line #1691 resurrection).

## 6. Implementation plan

### Phase 0 — Prerequisites & probes (no code)

**Task 0.1: Falsification probes**
- Create `docs/plans/1765-falsification-probes.md` + `graph-scripts/probes_1765.py` (or SQL against the control plane) implementing:
  - probe-1: dup team emails (email pairs mapping to ≥2 teams/users),
  - probe-2: claimed users with <2 identities (banner demand sizing),
  - probe-3: hosted manual-linking state (ops dashboard check),
  - probe-4: secondary-identity email collisions vs other teams' `teams.email`.
  - Exclusion predicate: internal/test emails (`@premise-labs.dev`, `@premiselabs.co` test accounts, `@example.*`).
- Run against prod control plane; record counts in the doc.
- **Gate:** demotion follow-up issue fires iff probe-1 ≥ 1 pair OR probe-4 ≥ 1 user; otherwise C2 invariant stands. Either way, P1/P2 proceed.

**Task 0.2: Ops preflight**
- Local: `supabase/config.toml:173` → `enable_manual_linking = true` (C17).
- Hosted: runbook step — enable manual linking in Supabase Auth settings (or `GOTRUE_SECURITY_MANUAL_LINKING_ENABLED=true` selfhost); verify GitHub/Google providers + redirect URLs cover the dashboard origin; record result (feeds probe-3).
- `.env.example`: add `TORTOISE_LINK_INTENT_SECRET`, `TORTOISE_IDENTITY_REAUTH_WINDOW` (default 3600), `TORTOISE_IDENTITY_RATE_LIMIT` (reuse claim limiter defaults).

### Phase 1 — Read-only inventory + banner + profile tab + username (ships alone)

**Task 1.1: Inventory endpoint**
- Files: Modify `tortoise/hosted_api.py` (new `GET /v1/user/identity` near the claim trio), Modify `tortoise/supabase_control.py` (service-role helpers: `identity_inventory_for_user`, `password_capable`, `credentials_by_creator`), Test `tests/test_user_identity_inventory.py` (FakeControlPlane + patched `_gotrue_admin_get_user`).
- Steps: failing test (inventory shape, C10 exclusion, 401 without session, registry-mode 400) → implement → pass → commit.

**Task 1.2: Banner predicate (pure JS)**
- Files: Create `website/apps/dashboard/src/identity.js`, Test `website/apps/dashboard/src/identity.test.js` (node --test, sessionKey.test.js pattern).
- Steps: failing test (`waysIn`, `showBanner` truth table incl. anon/keyless false positives) → implement → pass → commit.

**Task 1.3: Banner UI + profile tab (read-only)**
- Files: Modify `website/apps/dashboard/src/main.jsx` (nav :2641-2649 add Profile; banner :2624 area; inventory fetch at mount; session gates :2206/:2226 pattern; account-blob "Profile" entry), Modify `website/apps/dashboard/src/index.css` (profile section styles; reuse `.card`/`.banner` tokens).
- Steps: e2e failing test first (below) → render → pass.

**Task 1.4: Username**
- Files: Modify `tortoise/hosted_api.py` (`PATCH /v1/profile/username`), Modify `tortoise/supabase_control.py` (uniqueness scan + admin update_user merge), Create `supabase/migrations/20260827000001_identity_profile.sql` (username unique index — Phase-1 slice), Test `tests/test_user_identity_inventory.py::TestUsername` + `supabase/tests/20260827000001_identity_profile.sql` (duplicate insert → unique violation), Modify `website/apps/dashboard/src/main.jsx` (username editor), Create `website/apps/dashboard/src/identity.js` `displayName()` + test.
- Steps: SQL test → migration → endpoint test → endpoint → UI → commit.

**Phase 1 exit:** banner live for single-method users; username settable; zero identity writes anywhere.

### Phase 2 — Linking (gated by capability probe)

**Task 2.1: link_intents table + identity_floor_lock RPC**
- Files: Modify `supabase/migrations/20260827000001_identity_profile.sql`, Test `supabase/tests/20260827000001_identity_profile.sql` (floor math, LAST_METHOD, service_role-only enforcement, RLS deny).
- Steps: failing SQL test → migration → pass → commit.

**Task 2.2: link-intent + link-commit**
- Files: Modify `tortoise/hosted_api.py`, Modify `tortoise/supabase_control.py` (intent sign/verify), Test `tests/test_user_identity_authority.py` (intent expiry/replay, session-rotation mid-intent, stale-identity-row rejection, provider-verified-email check, audit rows, teams.email invariant on link, adoption signal).
- Steps: tests → endpoints → commit.

**Task 2.3: Add-email+password**
- Files: Modify `website/apps/dashboard/src/main.jsx` (profile flow: updateUser password → verification → degrade), Modify `tortoise/hosted_api.py` (audit + inventory refresh hook), Test `tests/test_user_identity_authority.py::test_add_password_no_placeholder_fires` (assert no `team_id=''` membership created), `tests/e2e/hosted/test_14_profile.py` (mocked GoTrue).
- Steps: tests → implementation → commit.

**Task 2.4: Unlink**
- Files: Modify `tortoise/hosted_api.py`, Modify `tortoise/supabase_control.py` (GoTrue user-token DELETE forwarder — bounded), Test `tests/test_user_identity_authority.py` (LAST_METHOD, two-tab race via the FakeControlPlane threading-lock pattern, reauth 422 → REAUTH_REQUIRED, post-state re-check, audit).
- Steps: tests → implementation → commit.

**Task 2.5: Capability probe + UI wiring**
- Files: Modify `website/apps/dashboard/src/main.jsx` (click-time probe: `linkIdentity` attempt → 422 `manual_linking_disabled` → session-persisted fail-closed state; add-method affordances + unlink buttons wired), Modify `website/apps/dashboard/src/identity.js` (+test).
- Steps: node test → UI → commit.

## 7. Testing strategy (mapped to existing infra)

| Layer | Harness | Files | Covers |
|---|---|---|---|
| Pure JS | `node --test` (sessionKey.test.js precedent) | `identity.test.js` | banner truth table, waysIn, displayName precedence |
| Python unit/integration | pytest + FakeControlPlane (`tests/fake_control_plane.py`) + patched `_gotrue_admin_get_user` | `tests/test_user_identity_inventory.py`, `tests/test_user_identity_authority.py` | endpoint shapes, C10 exclusions, 401/400, intent replay/expiry, floor atomicity (two-tab simulation), 422 probe fail-closed, audit rows, teams.email invariant |
| SQL | PGlite harness (`npm --prefix supabase/tests/pglite run validate`, `tests.assert` style) | `supabase/tests/20260827000001_identity_profile.sql` | floor RPC math + LAST_METHOD + service_role-only, username unique index, link_intents RLS |
| Hosted e2e | pytest-playwright, `tests/e2e/hosted/` suite (fixtures + mocked GoTrue admin seam) | `tests/e2e/hosted/test_14_profile.py` | inventory shape, add-password no-placeholder, unlink floor, banner data |
| Dashboard e2e | `RUN_DASHBOARD_E2E=1` route-interception (test_session_login_flow.py pattern) | `tests/e2e/test_dashboard_profile.py` | single-method banner → Profile route, username set + 409, fail-closed add-method UI when capability off |

## 8. Verification plan

1. **Phase 0:** probe report recorded; local toggle on; hosted toggle status documented (probe-3).
2. **Phase 1:** dashboard e2e shows banner for a single-method user and routes to Profile; username round-trip + duplicate 409; inventory endpoint verified in hosted e2e.
3. **Phase 2 pre-enable:** click-time capability probe in the hosted environment returns linking-capable BEFORE the add-method UI is exposed (fail-closed if 422).
4. **Post-impl verification (add-email+password):** `signOut → signInWithPassword → same auth.uid` assertion; on failure, degrade path exercised to verified-identity linking (OTP) with no admin-create.
5. **Unlink floor:** manual two-tab browser test — exactly one unlink succeeds when both target the same last-method removal; LAST_METHOD guidance shown.
6. **Invariant:** after every P2 flow, `teams.email` unchanged for the user's teams (asserted in integration tests; audited in code review).
7. Per `verification-before-completion`: full suite (docker lane, §AGENTS.md) green before "done".

## 9. Acceptance criteria (testable)

1. `GET /v1/user/identity` returns the inventory for the session user only; 401 without a valid session; 400 in registry mode; agent-principal keys never appear (C10).
2. Banner shows iff a session-authed, non-anon-team user has exactly one way in (identities + password-capability); CTA routes to the Profile tab; banner never renders for anon teams or the keyless-anon cohort.
3. Username set → `user_metadata.username`; display precedence `username > display_name > email-prefix` everywhere; duplicate → 409; #1691 wizard still writes `display_name` only (no regression test).
4. Add-OAuth creates exactly one new identity row via GoTrue; link-commit verifies intent + ownership + provider-verified email; audit `identity_link` row written; no `teams.email` write.
5. Add-email+password uses `updateUser({password})` only — no admin-create — and passes post-impl verification; on failure the flow degrades to verified-identity (OTP) linking; no `team_id=''` placeholder row ever appears for the user (C6).
6. Unlink with floor=1 refused (`409 LAST_METHOD`); concurrent two-tab unlink → exactly one succeeds; GoTrue reauth surfaced as `REAUTH_REQUIRED` with a working re-auth round; audit `identity_unlink` row written.
7. `enable_manual_linking` off → add-method UI hidden after the click-time probe (fail-closed); banner shows "contact support" fallback.
8. Identity flows never write `teams.email` (invariant — integration-test asserted + code-review checklist item).
9. Probes 1–4 run; demotion follow-up filed iff the decision rule fires, else documented as not triggered.

## 10. Runtime prerequisites

- **Ops toggle (external, C5):** hosted `enable_manual_linking = true` (Supabase Auth settings / `GOTRUE_SECURITY_MANUAL_LINKING_ENABLED=true`); local `supabase/config.toml:173 = true`. P2 is gated on this + the click-time probe.
- **OAuth:** GitHub/Google providers enabled; redirect URLs include the dashboard origin (existing claim OAuth shares the authorize redirect — verify).
- **Email confirmations ON** (`enable_confirmations = true`, config.toml:249) — required for the OTP degrade path.
- **env (`.env.example`):** `TORTOISE_LINK_INTENT_SECRET` (HMAC intent signing), `TORTOISE_IDENTITY_REAUTH_WINDOW` (default 3600s), rate-limit env (reuse claim limiter defaults). Existing: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`.
- **Probes:** Phase-0 run against the prod control plane (service-role read access), recorded in `docs/plans/1765-falsification-probes.md`.

## 11. Scope boundaries

- **In:** session-authed dashboard users on non-anon teams; profile tab; banner; username; P2 linking (OAuth, email+password, unlink) gated by capability.
- **Out (explicitly):** anon teams (full-page Protect #1148 covers — banner must not duplicate); keyless-anon cohort (#1716 separate recovery item; banner never promises a fix it cannot deliver); registry/selfhost mode (endpoints 400 hosted-only); agent principals (C10); `teams.email` demotion (probe-gated follow-up issue); MFA/security keys/phone identities (future — the `/v1/user/identity` surface is their home); `display_name` semantics (unchanged, #1691).
- **teams.email exception:** the claim path's Step-6 write (20260813000004) is the documented anon-adoption exception; re-pointed only on the demotion branch.

## 12. Handoff

- Plan-review gate per `plan-review` skill (proportional reviewers: architectural-soundness, contract-completeness, security posture of the server-as-user-agent surface, integration). Then `executing-plans` with `execution-intent` profile selection. P2 must not start before the P1 exit gate.
