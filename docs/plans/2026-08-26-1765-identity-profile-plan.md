<!-- research-path: docs/plans/2026-08-26-1765-identity-profile-scoping.md -->

# #1765 Identity & Profile Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.
<!-- plan-review: status=clean, cycles=3, reviewers=structural+integration+codebase+devil-advocate, issues=P0:0 P1:12 fixed, verdict=execution-ready -->
> **Supersedes:** `2026-08-27-1765-identity-profile-plan.STALE.md` (phase-sliced variant with username + row-lock unlink — superseded by user decision: full implementation, no username, permit-reservation; do NOT execute).

**Goal:** Users can manage login methods (GitHub/Google/email+password) on top of their API key from a profile tab, get a recovery banner when they have only one login method, and `teams.email` is demoted from identity anchor to team contact field.

**Team:** organisation-design-team
**Role:** (unset — AGENT_SESSION_ROLE absent)

**Architecture:** Server-authority identity model. `GET /v1/user/identity` computes the login-method inventory server-side (GoTrue `auth.identities` via a SECURITY DEFINER RPC + `auth.users.encrypted_password` for password capability + `api_keys` tier by `created_by`). Linking goes through server-issued signed intents (re-auth via `last_sign_in_at`, verified-email + newness + ownership checks, audit). Unlink uses permit-reservation with an atomic "never below 2 login methods" floor (partial-unique-index backstop). `teams.email` is demoted (unique index dropped, signup idempotency re-anchored on the reg- identity, consumers re-pointed, invariant guard with explicit allowlist).

### Pattern Research

> **Findings date:** 2026-08-26

**Library docs (preflight)** — supabase-js 2.112.2 (vendored, `website/apps/dashboard/public/vendor/supabase-2.112.2.min.js` — no npm dep). Bundle inspected in scoping Phase 7: exposes `linkIdentity` (5×), `unlinkIdentity`, `getUserIdentities`, `reauthenticate`; `linkIdentityOAuth` performs `window.location.assign` (REDIRECT flow). NOTE (plan-review): main.jsx:78 configures `flowType: 'implicit'` — do NOT chase PKCE semantics; the flowId search-param + sessionStorage marker contract is what matters. GoTrue admin seam verified in-repo: `_gotrue_admin_get_user` (hosted_api.py:3250) returns full user incl. `identities`; `auth.users.encrypted_password` exists but is `json:"-"` (not exposed via GoTrue REST).

> Bucket [library version & API surface] skipped: prior research already triangulated in scoping (### Integration Docs + ### Axis Research, issue comment 5423838206): manual linking disabled by default → `manual_linking_disabled` = 404; `single_identity_not_deletable` = 422 (native floor = identity ROWS, not password); `identity_already_exists` = 422; `email_conflict_identity_not_deletable` = 422; `reauthentication_not_valid` = 422; `reauthentication_needed` 400 = password-update path; #2085: `updateUser({password})` creates NO email identity row (supabase/auth#2085); `reauthenticate()` = GET /reauthenticate → emails OTP nonce, does NOT update `last_sign_in_at` (DA reviewer verified against vendored bundle + GoTrue source).

> Bucket [idiomatic usage patterns] skipped: vendored supabase-js already used in-repo (signInWithOAuth/signInWithPassword/updateUser in main.jsx + signup.html — 2+ examples). New calls (linkIdentity/unlinkIdentity/getUserIdentities) verified present in the exact vendored version by direct bundle inspection (scoping Phase 7).

> Bucket [library/framework pitfalls] skipped: account-linking security pitfalls + #2085 + CVE-2026-31813 applicability documented in scoping ### Axis Research with sources. Remaining unknowns are HOSTED GoTrue behavior — covered by Task 5 staging checks (ship blockers), not research queries.

### Integration Surface Map

| # | Surface | Type | Data Flow | Test Layer | Contract | Key Failure Modes |
|---|---------|------|-----------|-----------|----------|-------------------|
| 1 | `auth.identities` (via `user_identity_inventory` RPC) | DB | Read | SQL suite (PGlite) + integration | `(provider, provider_id)`, `email_confirmed_at` from `auth.users` NOT identities | RLS/service_role leakage; `''` password phantom; unconfirmed-OAuth-email identity rows |
| 2 | `auth.users` (`encrypted_password`, `email_confirmed_at`, `last_sign_in_at`) | DB | Read | SQL suite + integration | `has_password := encrypted_password IS NOT NULL AND <> ''` | `''` phantom password (OAuth users); NULL vs `''` |
| 3 | `api_keys.created_by` tier | DB | Read | Integration | namespaces uuid/anon-/reg-/st_/client/NULL; C10 exclusions (agent principals `st_`/`anon-` + bootstrap-NULL→membership-wide) | Cross-team misattribution; `reg-` in `created_by` from register path |
| 4 | `user_unlink_permits` | DB | Write (atomic) | SQL suite + integration | `(login_methods - pending - 1) >= 2` in ONE transaction + **partial unique index `(user_id) WHERE consumed_at IS NULL`** (READ-COMMITTED backstop; PGlite is single-connection — the SEQUENTIAL double-reserve 23505 IS the race proof, the index rejects regardless of MVCC) + **stale-permit TTL aging inside reserve_unlink (5 min > 15s GoTrue timeout)** | Two-tab race (23505 → `reserve_unlink:floor_violated` 409); permit leak on failure (compensation); **crash between reserve and consume = lockout without the TTL aging** |
| 5 | `link_intents` | DB | Write | SQL suite | signed nonce, TTL 120s, **consumed-once + expired-reject enforced in SQL** (partial unique index on `nonce WHERE consumed_at IS NULL` — mirror the permit pattern; DECISION: SQL, so replay is atomic), RLS service_role-only | Replay; expiry vs slow OAuth; cross-worker signature |
| 6 | `claim_membership` RPC (Step-6 email overwrite removed, created_by migration, 403 lift) | DB | Write | SQL suite | signature `(p_lookup_hash, p_user_id, p_email)` kept; parens in created_by UPDATE | SQL operator precedence (global reg- rewrite); foreign-team keys touched |
| 7 | `team_memberships.identity` reg- UNIQUE partial index | DB | Write (constraint) | SQL suite | `WHERE user_id IS NULL AND role='owner' AND status='active'` | Pre-existing duplicates → pre-scan/abort; 23505 → 409 |
| 8 | `teams.email` demotion (drop uq_teams_email) | DB | Write | SQL suite + integration | column kept as contact, NOT unique; identity flows never write (allowlist: tenant-provision `p_email` + register `p_email` only) | Dead `uq_teams_email` exception branch; existing-suite breakage (20260813000004 suite + 10 test files) |
| 9 | GoTrue admin seam (`_gotrue_admin_get_user`) | External | Out | Integration (FakeControlPlane + patched seam) | full user incl. identities | 5xx; identities array absent; token expiry |
| 10 | `linkIdentity` (vendored supabase-js) | External | Out | E2E (dashboard browser harness) + staging-manual | REDIRECT flow; storage via EXISTING parent-domain cookie adapter (main.jsx:49-71) | 404 manual_linking_disabled; login-wall post-return (#1225); provider round-trip > 120s |
| 11 | GoTrue `DELETE /user/identities/{id}` | External | Out | Integration | server-forwarded validated session token; **no-log transport (proxy redaction)**; native floor 422 | 404 stale-permit double-delete; **token logged by proxy** |
| 12 | `enable_manual_linking` / redirect URLs / email confirm | External config | — | Ops + fail-closed e2e | hosted project dashboard-managed | 404 when off (fail-closed UI + promise-free banner) |
| 13 | `GET /v1/user/identity` + link-intent/commit + unlink + resend-confirmation endpoints | API | Both | Integration (FakeControlPlane) | session-only; `linking_available`; `{"unsupported":true}` registry; per-USER rate limits (named envs) | 401/502; re-auth `now() - last_sign_in_at <= TORTOISE_REAUTH_WINDOW_SECONDS` (NULL last_sign_in_at → fail-closed DENY); 429 UX |
| 14 | Dashboard (main.jsx tab state :273, nav :2641-2649, mount effect :875-925, account blob :2654) | State/UI | Both | Unit (node --test, colocated src/*.test.js) + e2e | profile tab; RecoveryBanner; AddLoginMethodButtons; re-auth dialog; link-commit on return | Stale inventory (refetch on focus/mutation); nav overflow <768px; mount-effect marker restore; commit never fires |
| 15 | audit_events (`identity_link`/`identity_unlink`/`identity_confirm_resend`) | DB | Write | Integration | detail JSONB (provider/email/user_id + adoption signal — team_claim precedent) | Missing audit rows; adoption-signal field untested; PII hygiene |

### Bug Pattern Flags
- **SQL business logic** (surfaces 4,5,6,7): SQL-suite REQUIRED (PGlite harness) — permit predicate + unique-index backstop + TTL aging, intent consumed-once/TTL, created_by UPDATE parens, index pre-scan. TS mocks alone are blocking.
- **Race conditions** (surface 4): two-tab unlink — the partial unique index IS the backstop; assert via SEQUENTIAL double-reserve 23505 in the SQL suite (PGlite single-connection; the index rejects regardless of MVCC) AND FakeControlPlane threading (emulation must enforce the one-pending-permit invariant with the same error string). Concurrent registers — threaded FakeControlPlane test (API mapping) + ONE docker-lane integration test (true concurrency).
- **Conditional guards** (surfaces 3,13): `created_by` namespace classifier + `linking_available` + banner threshold — boundary tests both sides.
- **Silent function skips** (surfaces 6,8): claim Step-6 removal + onboarding PATCH re-point must assert no residual `teams.email` write (invariant guard test with allowlist).

### Checklist Notes
- **Atomic writes:** permit reserve→DELETE→consume compensates on EVERY failure path; unique-index 23505 → 409.
- **Idempotency:** link-commit "newness" check; register 23505 → 409 `already_registered`; stale-permit 404 benign; intent consumed-once.
- **Boundary values:** login_methods 0/1/2/3; OAuth-empty-password `''`; unconfirmed-OAuth-email; password-only (#2085); confirmed-email-no-password. **Harness seed MUST include the check-(b)=YES shape (OAuth user WITH an email identity row) — otherwise the count-FILTER bug ships green.**
- **Concurrent access:** two-tab unlink; two concurrent registers.

### Journey Test Map

### Journey: Add a login method before you lose access
1. **Step:** Single-method user opens dashboard → **Acceptance:** recovery banner shows, CTA visible → **Test:** `tests/e2e/test_dashboard_identity.py::test_banner_single_method`
2. **Step:** Clicks CTA → **Acceptance:** lands on Profile tab → **Test:** `test_dashboard_identity.py::test_banner_cta_lands_profile`
3. **Step:** Clicks "Connect GitHub" → **Acceptance:** provider round-trip, mount effect POSTs link-commit, method listed, banner gone → **Test:** `test_dashboard_identity.py::test_commit_fires_on_return`; full OAuth round-trip = **staging-manual checklist item in the Task 5 runbook** (hosted suite conftest is API-only — no browser)
4. **Step:** Clicks "Connect email and password" → **Acceptance:** branch-on-confirmed logic, password set, same auth.uid after re-login → **Test:** `test_user_identity_authority.py::test_add_password_same_uid` (staging-manual for the live re-login; hermetic parts: verified-email, audit, permit compensation)

### Journey: Remove a login method safely
5. **Step:** User with 2 methods clicks Remove → **Acceptance:** confirm dialog names provider + post-state → **Test:** `test_dashboard_identity.py::test_unlink_confirm`
6. **Step:** Two tabs both remove → **Acceptance:** exactly one succeeds (unique-index backstop), other 409 → **Test:** `test_user_identity_authority.py::test_unlink_two_tab`
7. **Step:** User with 1 method clicks Remove → **Acceptance:** blocked (disabled + 409 backstop) → **Test:** `test_user_identity_authority.py::test_unlink_floor`

### Failure Modes
- Manual linking off → **Expected:** fail-closed UI + promise-free banner → **Test:** `test_dashboard_identity.py::test_fail_closed_manual_linking`
- Inventory fetch fails (502/offline) → **Expected:** no banner, retry affordance → **Test:** `test_user_identity_inventory.py::test_fetch_error_fail_closed`
- Intent expires mid-OAuth round-trip → **Expected:** "already linked — refresh your profile" (audited) → **Test:** `test_user_identity_authority.py::test_link_commit_expired_intent`
- Concurrent register same email → **Expected:** one wins, loser 409 (never 500) → **Test:** `tests/test_email_signup.py::test_register_race_409` (created in Task 3)
- GoTrue DELETE 422 reauth → **Expected:** re-auth dialog round, permit compensated → **Test:** `test_user_identity_authority.py::test_unlink_reauth_round` (dialog in Task 4)
- Change-email on stale session → **Expected:** re-auth required (same REAUTH_WINDOW gate), ATO chain blocked → **Test:** `tests/e2e/test_dashboard_identity.py::test_change_email_requires_reauth` (client-side ReauthDialog gate — Task 4 Step 6; freshness predicate in identity.js truth tables — Task 4 Step 1)

**Tech Stack:** Python 3.12 (FastAPI, httpx, PyJWT 2.13), Supabase (GoTrue + PostgREST service_role seam), Postgres SQL (SECURITY DEFINER RPCs, plain-SQL assertion suites via PGlite), React 19 + Vite (dashboard, vendored supabase-js 2.112.2), Playwright (e2e — dashboard browser harness `RUN_DASHBOARD_E2E`, hosted API harness), node --test (colocated `src/*.test.js`).

---

## Tasks

> **Suite-redness note (plan-review):** Tasks 1–3 intentionally leave legacy surfaces red (claim/email/onboarding suites assert the OLD contract). Per-task gates are scoped to NEW tests only; Task 6 reconciles the legacy surfaces. Task 1 itself flips the `20260813000004` SQL suite (same migration-behavior change) so the PGlite harness is green at the end of Task 1.

### Task 1: Migration `20260827000001_user_identity_profile.sql` + PGlite suite

**Intent:** Create the identity-model substrate: permit/intent tables (with atomic backstop), inventory + reserve RPCs (with CORRECT login_methods SQL), claim RPC changes, `teams.email` demotion with pre-scan, reg- idempotency index.
**Acceptance:** PGlite harness green at task end (new suite + flipped 20260813000004 suite); `uq_teams_email` gone; identity flows can't write `teams.email`; foreign-team keys untouched by claim; permit two-tab backstop enforced by the unique index.

**Files:**
- Create: `supabase/migrations/20260827000001_user_identity_profile.sql`
- Create: `supabase/tests/20260827000001_user_identity_profile.sql` (plain-SQL RAISE assertions — PGlite harness)
- Modify: `supabase/tests/20260813000004_claim_membership.sql` (flip: uq_teams_email exists → gone; Step-6 overwrite A→B → no-write; `email_in_use` raise → gone — SAME migration-behavior change, do it HERE not in Task 6; the 403 lift is API-layer → Task 3, nothing to flip here)
- Modify: `supabase/tests/pglite/validate.mjs` (register new migration + suite; bootstrap `auth.identities` + `last_sign_in_at` + `encrypted_password=''` seeds + **OAuth-user-WITH-email-identity-row seed (check-(b)=YES shape)**)

**Step 1:** Write the SQL assertion suite first (TDD). MUST assert:
- `user_unlink_permits`/`link_intents` RLS deny-by-default (service_role-only).
- `user_identity_inventory` login_methods for the 6 shapes: zero-method (email-identity-only, unconfirmed, no password → 0); OAuth-empty-password → has_password FALSE; unconfirmed-OAuth-email → email_method 0; password-only → 1; confirmed-email-no-password → email_method 1; **OAuth-user WITH email identity row + password → login_methods 2 (NOT 3 — catches the count-FILTER bug)**. Unknown `p_user_id` → 0 methods + empty keys tier, never an error.
- `reserve_unlink` grants at login_methods=3 (one permit); SEQUENTIAL double-reserve at 3 → second raises `reserve_unlink:floor_violated` (unique-index 23505, named code); blocks at 2; bad identity_id → `reserve_unlink:identity_not_found` (zero-row INSERT distinguished); stale permit older than 5 min is aged (released) by the TTL UPDATE inside reserve_unlink.
- `link_intents` consumed-once: second consume of the same nonce → rejected by the partial unique index; expired (>120s) → rejected.
- claim created_by migration touches ONLY the claiming team (foreign reg- keys untouched); claim no longer writes teams.email.
- `uq_teams_email` absent; pre-scan aborts on duplicate identities; claim no longer writes teams.email (flipped suite).

**Step 2:** Run to verify failure: `cd supabase/tests/pglite && npm run validate` → FAIL (new suite asserts migration missing).

**Step 3:** Implement the migration:
- Tables `user_unlink_permits`, `link_intents` — ENABLE RLS, REVOKE from anon/authenticated, GRANT service_role (0006-0009 pattern). **Partial unique indexes (DECISION: SQL enforcement, mirror the permit pattern):** `uq_user_unlink_permits_active ON user_unlink_permits(user_id) WHERE consumed_at IS NULL` (READ-COMMITTED two-tab backstop); `uq_link_intents_nonce_active ON link_intents(nonce) WHERE consumed_at IS NULL` (consumed-once).
- RPCs `user_identity_inventory(p_user_id)` + `reserve_unlink(p_user_id, p_identity_id)` — SECURITY DEFINER, `SET search_path=''`, service_role-only grants.
  - `has_password := encrypted_password IS NOT NULL AND encrypted_password <> ''`
  - `email_method := (users.email IS NOT NULL AND users.email_confirmed_at IS NOT NULL) OR has_password` (boolean)
  - `login_methods := (SELECT count(*) FROM auth.identities i WHERE i.user_id = p_user_id AND i.provider NOT IN ('email')) + email_method::int` — **`count(*) FILTER`/NOT IN, NEVER `count(provider <> 'email')` (counts email rows); cast the boolean**
  - `reserve_unlink`: ONE transaction — (1) TTL aging: `UPDATE user_unlink_permits SET consumed_at = now() WHERE user_id = p_user_id AND consumed_at IS NULL AND created_at < now() - interval '5 minutes'` (sweep for crash-window permits, TTL > 15s GoTrue timeout — prevents permanent unlink lockout); (2) `INSERT INTO user_unlink_permits (user_id, identity_id) SELECT … WHERE (login_methods - pending - 1) >= 2 AND identity belongs to user`; (3) distinguish ZERO-ROW causes: if the identity does not belong to the user → RAISE `reserve_unlink:identity_not_found`, else `reserve_unlink:floor_violated`; (4) catch `unique_violation` → `reserve_unlink:floor_violated` (READ-COMMITTED backstop — partial unique index `uq_user_unlink_permits_active ON user_unlink_permits(user_id) WHERE consumed_at IS NULL`).
- Claim RPC changes: remove Step-6 `teams.email` overwrite; remove `SQLERRM LIKE '%uq_teams_email%'` branch; add created_by migration `UPDATE api_keys SET created_by = p_user_id WHERE team_id = v_team_id AND (created_by LIKE 'anon-%' OR created_by LIKE 'reg-%')` (parens!). KEEP signature `(p_lookup_hash, p_user_id, p_email)`. (The `providers=['email']` 403 lift is an API-layer change → Task 3.)
- Demotion: `DROP INDEX IF EXISTS uq_teams_email`; pre-scan/abort DO block before `CREATE UNIQUE INDEX uq_member_identity_active ON team_memberships(identity) WHERE user_id IS NULL AND role='owner' AND status='active'` — `GROUP BY identity` (reg- is already lowercase hex; the case hole is the app-layer `team_by_email` `=` — noted, not fixable in SQL), RAISE with offenders.
- Flip the 20260813000004 suite assertions to the post-demotion contract (same file, same behavior change).

**Step 4:** Run: `cd supabase/tests/pglite && npm run validate` → PASS (both suites green). Also run `bash supabase/tests/run_schema_tests.sh` (0006-0009 lane stays green — verified it contains no uq_teams_email/claim-email asserts). TDD note: the 20260813000004 flips land with implementation (Step 3) — the new suite's red (Step 2) covers the same behavior change.

**Step 5:** Commit: `git add supabase/migrations/ supabase/tests/ && git commit -m "feat(auth): #1765 identity substrate — permits+intents, inventory RPC, teams.email demotion"`

### Task 2: `supabase_control.py` seam helpers

**Intent:** Expose the new RPCs through the existing control-plane seam (FakeControlPlane-compatible). Owner-email resolution is a PYTHON seam helper (membership query + `_gotrue_admin_get_user`), not a new RPC.
**Acceptance:** `user_identity_inventory`/`reserve_unlink`/`owner_email` callable via the seam (`cp.rpc(fn, body)` dialect — NOT `cp.query("rpc", ...)` which does not exist); `_CLAIM_ERROR_CODES["email_in_use"]` pruned; FakeControlPlane updated.

**Files:**
- Modify: `tortoise/supabase_control.py` (beside `team_email` :1203 / `update_team_email` :1209; prune `_CLAIM_ERROR_CODES["email_in_use"]` :1614 + docstring :1589-1592)
- Modify: `tests/fake_control_plane.py` (RPC dispatch :51; remove claim email_in_use raise :171/:416; add inventory/reserve emulations; claim emulation stops writing teams.email)
- Modify: `tests/test_supabase_control.py` (:1032-1065 team-email seam; :1815-1825 email_in_use test → replaced with no-write assertions; :1690/:1707 claim email-overwrite assert → no-write — flipped HERE since the fake change is here, not Task 6)
- Test: `tests/test_supabase_control.py`

**Step 1:** Write failing tests (FakeControlPlane): inventory helper shape; reserve_unlink emulation; owner_email NULL/zero-owner/owner-deleted handling; email_in_use test removed.
**Step 2:** Run: `uv run pytest tests/test_supabase_control.py -v` → FAIL.
**Step 3:** Implement thin `cp.rpc(...)` wrappers (claim_membership seam at :1618 is the pattern) + owner_email helper (membership user_id → `_gotrue_admin_get_user` email; NULL/zero-owner → None → abuse fallback). Prune `_CLAIM_ERROR_CODES`. Add `_UNLINK_ERROR_CODES` seam (`reserve_unlink:floor_violated` / `reserve_unlink:identity_not_found` → 409) mirroring the claim pattern.
**Step 4:** FIRST update FakeControlPlane: claim emulation stops writing teams.email (invariant guard depends on it) + reserve emulation enforces the one-pending-permit invariant with the SAME error strings as the real RPC (otherwise test_unlink_two_tab passes vacuously) — THEN run the NEW tests scoped: `uv run pytest tests/test_supabase_control.py -k 'inventory or reserve or owner_email' -v` → PASS. (Full-file run stays red until Task 6 — suite-redness note; `test_claim_links_owner_clears_identity_overwrites_email` :1690/:1707 asserts the OLD contract and is flipped in Task 6.)
**Step 5:** Commit.

### Task 3: hosted_api.py — user endpoints + claim 403 lift + register 409 + consumer re-point

**Intent:** Ship the server-authority surface + the API-layer decisions that have no home elsewhere.
**Acceptance:** `GET /v1/user/identity` session-only + `linking_available` + registry `{"unsupported":true}` + 502 fail-soft; link-intent/commit verify re-auth (`now() - last_sign_in_at <= TORTOISE_REAUTH_WINDOW_SECONDS`), nonce, newness, ownership, verified-email, adoption signal, audit; unlink permits + forwarded DELETE (no-log transport) + post-verify + audit; claim `providers=['email']` 403 LIFTED (confirmed-email conjunct kept); register race → 409 (test-first); abuse/onboarding/oauth consumer re-points; `POST /v1/user/identity/resend-confirmation`.

**Files:**
- Modify: `tortoise/hosted_api.py` (claim trio :7616, onboarding GET :8199/PATCH :8208, register :2985, `_gotrue_admin_get_user` :3250, comment :7570-7579)
- Modify: `tortoise/abuse.py` (owner-email resolution re-point via seam; NULL fallback → ops inbox)
- Modify: `tortoise/oauth.py` (:500 team email → user email)
- Create: `tests/test_user_identity_inventory.py`, `tests/test_user_identity_authority.py`
- Modify: `tests/test_email_signup.py` (add `test_register_race_409`)

**Step 1:** Write `test_user_identity_inventory.py` (FakeControlPlane + patched admin seam): 401/502/unsupported; login_methods shapes (incl. OAuth-empty-password `''`, unconfirmed-email, password-only #2085, OAuth+email-row+password → 2); C10 exclusions (st_/anon- excluded, bootstrap-NULL → membership-wide); `linking_available`; invariant guard (new flows never write teams.email, allowlist = tenant-provision + register p_email — comment convention `# sanctioned contact-field write (invariant allowlist)`).
**Step 2:** Run → FAIL. **Step 3:** Implement `GET /v1/user/identity` (Depends `get_current_user`; seam RPC; registry mode `{"unsupported":true}`).
**Step 4:** Run → PASS.
**Step 5:** Write `test_user_identity_authority.py`: intent expiry/replay/newness; re-auth `now() - last_sign_in_at <= TORTOISE_REAUTH_WINDOW_SECONDS` (NOT iat); auth.uid mismatch; **last_sign_in_at present in the inventory contract (drives the client ReauthDialog staleness check)**; audit rows incl. adoption-signal detail; two-tab unlink (unique-index 23505 → 409 + FakeControlPlane threading); permit compensation on every failure; 404 stale-permit benign; error-code mapping incl. `reauthentication_not_valid`/`reauthentication_needed`/`single_identity_not_deletable`/`identity_already_exists`/`email_conflict_identity_not_deletable`; per-USER rate limits on link-intent + unlink (429 copy).
**Step 6:** Run → FAIL. **Step 7:** Implement: `POST /v1/user/identity/link-intent` (HMAC-signed nonce `TORTOISE_LINK_INTENT_SECRET` — **fail-closed 503 when env unset**; TTL 120s; per-USER rate limit; `hmac.compare_digest`); `POST /v1/user/identity/link-commit` (newness + ownership + verified-email + adoption signal surfaced/audited; mark intent consumed); `POST /v1/user/identity/unlink` (re-auth gate `now() - last_sign_in_at <= TORTOISE_REAUTH_WINDOW_SECONDS` — SAME gate as link-intent, scoping applies it to BOTH; reserve via seam → forward validated session token to GoTrue `DELETE /user/identities/{id}` → post-verify `login_methods >= 1` → consume/compensate → audit; keep `reauthentication_not_valid` mapping as defense-in-depth); `POST /v1/user/identity/resend-confirmation` (session-authed; **409/no-op when email already confirmed**; per-auth.uid limits e.g. 1/60s + 5/h via named envs — in-memory buckets are per-process, document multi-worker drift; `identity_confirm_resend` audit). **Token-log hygiene (P2-1):** (a) integration regression asserting the forwarded token never appears in `tortoise.api` logs across success/failure/exception paths; (b) comment on the bare-httpx call: "never add event hooks / set httpx DEBUG logger — headers would leak"; (c) runbook proxy access-log step (Task 5).
**Step 8:** Run → PASS.
**Step 9:** Claim 403 lift (test-first): flip `test_claim_endpoints.py` header :12-13 + :154 ONLY (partition: Task 6 owns :185/:202/:363 + remaining email sites) → success-with-confirmed-email / still-403-without-confirmation; REMOVE the `providers=['email']` rejection in hosted_api.py claim endpoints (keep confirmed-email conjunct); rewrite :7570-7579 comment to the post-demotion invariant. Gate: flipped subset → PASS (full-file claim run stays red until Task 6 — suite-redness note).
**Step 10:** Register race (test-first): add `tests/test_email_signup.py::test_register_race_409` — threaded FakeControlPlane concurrent registers (API mapping) + ONE docker-lane integration test for true concurrency → exactly one 200/409 pair, never 500; implement reg- identity pre-check (`identity = reg-X AND user_id IS NULL AND role='owner' AND status='active'` — match the index predicate) + 23505 → 409 `already_registered` (mirror 0011 dup-name pattern at :5346-5408/:8309-8315). **Deploy-ordering note: migration (Task 1) + this 409 mapping ship together — same window, no 500 gap.**
**Step 11:** Consumer re-points: onboarding PATCH email (session-auth → user anchor write; key-auth → teams.email contact — named in the invariant allowlist) + GET dual-auth (key → teams.email contact; session → user email); abuse owner-notify via `owner_email` seam (NULL → ops-inbox fallback); oauth.py:500 → user email. Add tests: onboarding GET/PATCH dual-auth; oauth.py user payload; abuse owner-deleted/NULL-email/zero-owner.
**Step 12:** Run full file + affected subsets → iterate. **Step 13:** Commit.

### Task 4: Dashboard — profile tab, recovery banner, add-method UI, re-auth dialog, link-commit wiring

**Intent:** The user-facing surface + the client half of the server-authority flows (link-commit MUST fire on OAuth return; re-auth dialog gives the server gate a UI path; add-email+password branches on `email_confirmed_at`).
**Acceptance:** Banner iff `login_methods ≤ 1 AND NOT (email confirmed AND has_password)`, non-anon, fetch OK; promise-free when linking off; CTA lands on Profile; link-commit fires on OAuth return; add-email branches on `email_confirmed_at`; re-auth dialog works for password + OAuth-only (same-provider); fail-closed + loading/empty/error states; no horizontal scroll <768px.

**Files:**
- Create: `website/apps/dashboard/src/profile.jsx` (ProfileTab, RecoveryBanner, AddLoginMethodButtons, ReauthDialog)
- Create: `website/apps/dashboard/src/identity.js` + `website/apps/dashboard/src/identity.test.js` (node --test, COLOCATED — sessionKey.test.js precedent; NOT `tests/` — none exists)
- Modify: `website/apps/dashboard/src/main.jsx` (nav :2641-2649 6th tab; RecoveryBanner mount; mount-effect OAuth-return :875-925 — **POST link-commit on return BEFORE stripping params**; re-auth marker resume; inventory refetch on focus/mutation; vendored supabase-js storage — VERIFY existing cookie adapter :49-71 is used by the linkIdentity flow, extend if not)
- Modify: `website/apps/dashboard/src/index.css` (`.recovery-banner`, nav overflow)
- Modify: `tests/test_cross_subdomain_cookie_sync.py` (extend static anchors if a new adapter module is added)
- Create: `tests/e2e/test_dashboard_identity.py` (browser harness — RUN_DASHBOARD_E2E; the hosted suite conftest is API-only, banners need a browser)

**Step 1:** Write `identity.js` pure module + colocated `identity.test.js` node --test truth tables (banner predicate incl. linking_available false + unconfirmed-email; created_by namespace classifier; refetch rule helpers; **reauth-staleness predicate from the inventory's `last_sign_in_at` — drives the ReauthDialog gate for change-email + unlink**). Run: `node --test website/apps/dashboard/src/identity.test.js` → PASS.
**Step 2:** Implement `profile.jsx` ProfileTab (method list w/ provider+email+confirmation status; keys tier prefix-only; unlink via `window.confirm` naming provider + post-state; Remove disabled when `login_methods - 1 < 2`; loading/error/retry per membersStatus; AddLoginMethodButtons extracted — claim card :2153-2202 + Protect screen :2240-2286 refactored to use it). **Add-email+password handler — port the scoping block verbatim: branch on `email_confirmed_at` (confirmed → `updateUser({password})`; unconfirmed/absent → change-email + confirmation + set-password); map `email_exists` 422; #2085 note (password adds no identity row — `has_password` is the signal); NEVER admin-create (placeholder split) EXCEPT `/v1/claim/email` which keeps its pre-existing admin-create; adoption signal is surfaced on link-commit ONLY (no change-email server hook exists — Task 3 endpoint inventory). ⛔ CHANGE-EMAIL SECURITY (plan-review P1-1): the change-email flow REQUIRES the ReauthDialog first (same REAUTH_WINDOW freshness as link-intent) — without it a stolen session = full takeover (change-email → confirm attacker mailbox → forgot-password → set password → unlink victim identities) AND it manufactures the confirmed-email the claim 403-lift trusts. NEVER weaken `double_confirm_changes` (config.toml:244 — the "old unverified email must not block" workaround is the enabler; behavior is staging-verified, NOT bypassed). Per-user rate-limit the change-email/confirm path.**
**Step 3:** RecoveryBanner (`.recovery-banner` protect-banner family; `role="region"` + `aria-label`; no-dismiss for actionable / TTL-dismiss `tt_recovery_dismissed` for not; Resend-confirmation button → `POST /v1/user/identity/resend-confirmation`; promise-free variant when `linking_available=false`; stacking above transient banners; flex-wrap) + ReauthDialog (password → `signInWithPassword`; provider buttons → OAuth round, SAME-provider enforced; auth.uid-mismatch message; intent-ref marker resume) — **the ReauthDialog ALSO gates change-email (Step 2) and unlink**.
**Step 4:** Wire main.jsx: 6th tab; banner fetch on mount + refetch on window focus + after mutations; **mount-effect OAuth return (intent-ref contract — flowId is PKCE-language, this app is `flowType:'implicit'` main.jsx:78 so flowId is null): pass `redirectTo: <origin><path>?link_flow=<intent-ref>` to `linkIdentityOAuth` options; store the intent-ref in per-tab sessionStorage BEFORE the call; on return the mount effect reads the `link_flow` search param → POST link-commit → on success refetch inventory → `setTab('profile')` → `history.replaceState` strip → stale markers cleared silently (preserve SEARCH, never hash; mirror the proven `?claim=1` flow :875-925)**; NOTE: implicit flow has a pre-existing crafted-URL session-replacement weakness — the server-side ownership check on link-commit is the protection, keep it; re-auth marker resumes pending action.
**Step 5:** `npm --prefix website/apps/dashboard run build` + `node --test website/apps/dashboard/src/` → green.
**Step 6:** Dashboard e2e `tests/e2e/test_dashboard_identity.py` (banner → profile; fail-closed; link-commit fires on return; change-email requires re-auth; <768px no-horizontal-scroll — 375px precedent test_legal_pages.py). Run: `RUN_DASHBOARD_E2E=1 uv run pytest tests/e2e/test_dashboard_identity.py -v`.
**Step 7:** Commit.

### Task 5: Ops runbook + staging verification + env + docs + log hygiene

**Intent:** External prerequisites + ship blockers documented and executable; no plaintext tokens in logs; secret fail-closed.
**Acceptance:** Runbook covers the Supabase dashboard steps + staging checks (a)-(e); **forwarded bearer token never appears in logs (no-log transport asserted)**; `TORTOISE_LINK_INTENT_SECRET` unset → link-intent 503 (fail-closed) + rotation note; `.env.example` + website_architecture.md updated.

**Files:**
- Create: `docs/plans/2026-08-26-1765-ops-runbook.md` (link + steps below)
- Modify: `.env.example` (`TORTOISE_LINK_INTENT_SECRET`, `TORTOISE_REAUTH_WINDOW_SECONDS`, per-user rate-limit envs)

**Step 1:** Write the ops runbook (exact links): `https://supabase.com/dashboard/project/ybetwichurajbfswfeqa/auth/providers` → **Enable Manual Linking** toggle ON → Save. `…/auth/url-configuration` → verify `https://app.premiselabs.co` + `https://tortoise.premiselabs.co` in Additional Redirect URLs. `…/auth/settings` → Confirm email ON (already per #801/#832). Include the "add-email+password same-uid" manual checklist item (Step 3c).
**Step 2:** Staging GoTrue verification (SQL editor, BEFORE finalizing Task 1 seeds/Task 3 predicates where possible): (a) `select email, email_confirmed_at, encrypted_password from auth.users` — OAuth users have `''`; (b) `select provider, count(*) from auth.identities group by 1` — email identity rows for OAuth signups?; (c) change-email behavior on OAuth-only account — **written pass/fail criterion for `double_confirm_changes` (config.toml:244) on unconfirmed-old-email; verify NOT bypassed — this is a ship blocker**; (d) `CreateEmailIdentityOnPasswordSetEnabled` state; (e) `select id, email, last_sign_in_at from auth.users` before/after a provider sign-in AND a password sign-in — confirm the re-auth gate signal updates; (f) hosted `security.reauthentication_time` value — PASS threshold: >= 900s (matches TORTOISE_REAUTH_WINDOW_SECONDS) so the unlink defense-in-depth is meaningful (unlink defense-in-depth). Record results; ANY FAIL = ship blocker surfaced to user.
**Step 3:** Log hygiene (P2-1): (a) integration regression (Task 3 Step 7) asserts the forwarded token never appears in `tortoise.api` logs; (b) runbook: configure proxy access logs WITHOUT `$http_authorization` (or log-off for the GoTrue-forwarded path) + staging grep of THAT log for `Authorization: Bearer eyJ…` — uvicorn never logs headers, so the grep must target the proxy; (c) manual staging check: "add-email+password same-uid" (signOut → signInWithPassword → same auth.uid) recorded in the runbook as a checklist item (hermetic parts in Task 3 tests).
**Step 4:** `.env.example` + secret fail-closed note: `TORTOISE_LINK_INTENT_SECRET` (unset → link-intent 503; rotation → intents invalidate within 120s TTL), `TORTOISE_REAUTH_WINDOW_SECONDS` (default 900; interval semantics: `now() - last_sign_in_at <= <seconds>`; NULL last_sign_in_at → fail-closed deny), per-user rate-limit envs (`TORTOISE_LINK_RATE_LIMIT`, `TORTOISE_UNLINK_RATE_LIMIT`, `TORTOISE_RESEND_RATE_LIMIT` — in-memory buckets are per-process, document multi-worker drift).
**Step 5:** website_architecture.md dashboard section update (Profile tab + recovery banner + login-methods surface).
**Step 6:** Commit.

### Task 6: Existing-test surface reconciliation (legacy contract → post-demotion)

**Intent:** The demotion + 403 lift + claim changes break 10 legacy test surfaces (the 11th — the 20260813000004 SQL suite — was flipped in Task 1). Align them to the post-demotion contract; full suite green.
**Acceptance:** All legacy surfaces updated; full docker-lane suite + PGlite + `vite build` + e2e smoke green; no residual `teams.email` overwrite / `email_in_use` / 403 assertions.

**Files:**
- Modify: `tests/test_claim_endpoints.py` (REMAINDER only — header + :154 owned by Task 3 Step 9; Task 6 owns :185/:202/:363 provider-invariant tests + remaining email sites)
- Modify: `tests/test_email_signup.py:278` (TestEmailSignupClaim), `tests/test_agent_signup.py:332-364`
- Modify: `tests/e2e/hosted/test_13_claim.py:382,386-392` (email assert + 403 assert — TWO breakages)
- Modify: `tests/test_supabase_control.py` (:1815-1825 already in Task 2; remainder), `tests/fake_control_plane.py` (already in Task 2; remainder), `tests/test_onboarding_analytics_patch.py:51`, `tests/test_abuse.py` + `tests/test_abuse_integration.py`
- (test_cross_subdomain_cookie_sync.py owned by Task 4 — no double-edit)

**Step 1:** Grep the legacy surfaces for `teams.email` overwrite / `email_in_use` / `providers=['email']` 403 assertions; update each to the post-demotion contract (claim no longer overwrites; 403 → success-with-confirmed-email; email_in_use pruned). Partition: Task 3 Step 9 already owns `test_claim_endpoints.py` header + :154 — Task 6 owns the REMAINDER (:185/:202/:363 provider-invariant tests whose AMR behavior shifts + remaining email sites). Fix the stale scoping citation: `team_emails` is `tortoise/abuse.py:156` (store attribute), not a test fixture.
**Step 2:** Run: `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_claim_endpoints.py tests/test_email_signup.py tests/test_agent_signup.py tests/test_supabase_control.py tests/test_onboarding_analytics_patch.py tests/test_abuse.py tests/test_abuse_integration.py tests/e2e/hosted/test_13_claim.py -v` → iterate to green.
**Step 3:** Full docker lane + PGlite + `vite build` + e2e smoke (hosted + dashboard).
**Step 4:** Commit.

---

## Verification Plan

- **Per-task gates:** each task's own test suite green (TDD order; Task 1–3 legacy redness acknowledged — see suite-redness note).
- **Task 6:** full docker-lane pytest + PGlite + `vite build` + Playwright (hosted + dashboard) green.
- **Post-deploy (staging):** hosted `enable_manual_linking` on + redirect URLs + `manual_linking_disabled` 404 leak test; post-impl password verification (signOut → signInWithPassword → same auth.uid); manual two-tab unlink (exactly one succeeds); **change-email re-auth round (stolen-session ATO chain test)**; `teams.email` unchanged after every identity flow (integration-asserted + review checklist); claim/onboarding/signup/abuse-notify regression; staging checks (a)-(f) recorded in the runbook.
- **UX (standard):** ux-verification skill at medium depth — component catalog compliance (protect-banner family, membersStatus states), common failure patterns (stale banner, dead CTAs, unconfirmed-email trap, commit-never-fires), a11y basics (role=region banner, keyboard CTA).
- **Non-code domains:** none deferred (config = the documented ops steps only).

## UX Design Decisions

(All decisions made in scoping — Phase 7 UX review + user direction; gate skipped per "all UX decisions already made during issue-scoping".)

| # | Decision | Choice | Rationale |
|---|---|---|---|
| 1 | Profile placement | 6th nav tab | Existing tab state machine; deep-link + OAuth-return works with `setTab('profile')` |
| 2 | Banner family | `.recovery-banner` (protect-banner family) | Persistent security notice ≠ transient green banner |
| 3 | Banner dismissal | No-dismiss if actionable; TTL-dismiss if not | Never trap a user; never nag an unrecoverable state |
| 4 | Banner copy | "Your account is protected by only one login method…" | Existing "account" voice; user-approved "login methods" term |
| 5 | linking off | Promise-free variant + contact-support | Never promise add-methods that can't work (AC6 "suppressed" is STALE — promise-free is canonical) |
| 6 | Unlink UX | `window.confirm` naming provider + post-state; Remove disabled at floor | Dashboard destructive-action convention |
| 7 | Add-method UI | Shared `AddLoginMethodButtons` (claim/protect family) | Third copy of identical markup — extract |
| 8 | States | membersStatus convention; background-hydrate; refetch on focus/mutation | #1567 shell renders immediately; no stale banner |
| 9 | OAuth return | Mount-effect intent-ref contract + **link-commit fires on return** | Server-authority gates must actually run |
| 10 | Re-auth | ReauthDialog (password / same-provider OAuth round) | Server `last_sign_in_at` gate needs a UI path |
| 11 | Add-email+password | Branch on `email_confirmed_at` | GitHub-private dead-address trap (scoping cycle-2) |
| 12 | Change-email | ReauthDialog gate (same REAUTH_WINDOW) + NEVER weaken double_confirm_changes | Stolen-session ATO chain (plan-review P1-1) |
