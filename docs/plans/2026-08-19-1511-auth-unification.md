---
title: "#1511 Auth Unification Implementation Plan"
type: decisions
domain: operations
doc_status: live
created: 2026-08-19
ownedBy: epistemic-team
---

<!-- research-path: docs/scoping/2026-08-19-1511-auth-unification.md -->

# #1511 Auth Unification Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Unify all auth onto /auth (dashboard never shows auth UI; browser API-key login works via a server-side key→session exchange; /auth shows "Last used"; welcome never renders unauthenticated).

**Architecture:** A new hosted-API endpoint (`POST /v1/session/login`) exchanges a validated `tt_` API key for a real Supabase session minted for the key's CREATOR (a team member with an auth user) — NOT the team owner — which eliminates the member-key→owner escalation while still supporting member key logins (each key mints its own user's session). Mint via GoTrue admin generate_link + service-role /verify (the only supported session-mint path; verified from supabase/auth source: no email sent, phantom-user auto-create avoided by GET-first ordering + post-verify membership check). The session lands in the existing `.premiselabs.co` parent-domain cookie via supabase-session.js, so the dashboard is authenticated without the raw key ever crossing origins. Shared gate/validity/clear helpers in `supabase-session.js` give all three pages + the dashboard ONE validity predicate (presence→validity) — loop-safety by construction. The dashboard's key-only card is deleted; the claim flow stays (D2).

> **Code-review round 1 drifts (2026-08-20):** (1) the mount-effect session check now requires STRICT validity (`!session.expires_at || past` = invalid — the shared predicate), not the lenient presence check; (2) claim-intent is IN-FLIGHT ONLY: `?claim=1` OR (`tt_claim_key` AND `tt_claim_pending`) — a bare stale key/marker redirects to /auth (the claim-paste screen has a "Back to sign in" escape link); (3) `loginWithApiKey` keeps the modal disabled+spun across the exchange await (no double-submit) and the 429 shows the hour-scale server copy; (4) GoTrue transport exceptions map to 502 (never a raw 500) + the post-verify backstop asserts `session.user.id == mint target`; (5) logout → `bounceToAuth`; (6) last-used migration fires from the dashboard mount via the shared helper.

### Pattern Research
> **Findings date:** 2026-08-19 (scoping Phase 1.5 + solution-diverge agents verified against supabase/auth source and the repo).
- **Canonical (GoTrue admin session surface):** NO admin session-mint endpoint exists (supabase/auth `internal/api/api.go` admin routes: users CRUD, generate_link, audit, sso). The only supported path is `POST /auth/v1/admin/generate_link {type:magiclink}` → `POST /auth/v1/verify`. `adminGenerateLink` does NOT send email (`internal/api/mail.go`); returns `email_otp`/`hashed_token`/`action_link`; token is single-use; `/verify` with `{token_hash, type:"magiclink"}` (NOT `type:"email"` — the token-hash branch is keyed on `mail.MagicLinkVerification`) returns the full `AccessTokenResponse` (access+refresh+user) via `issueRefreshToken`. The admin user fetch is **`GET /auth/v1/admin/users/{id}`** (GET only — POST would 405). **Footgun:** for a missing email, generate_link silently flips to a signup flow and auto-creates an unconfirmed phantom user (random password) — mitigated by resolving the email from the admin user row (GET first) and a post-verify membership sanity check (the mint-target is an ACTIVE member of the key's team — no role requirement) to kill the GET→generate_link TOCTOU. token_hash requests must NOT carry an `email` field.
- **Pitfalls (cross-origin):** localStorage/sessionStorage are origin-scoped (SOP) — the /auth (tortoise) key write is invisible to the dashboard (app). The session ALREADY bridges via the parent-domain cookie (supabase-session.js). The raw key must NEVER go in a cookie or URL (#1082 P1-2). Rate-limit precedents: register 3/hr/IP, claim 2/24h/IP; the exchange uses `_check_ip_bucket_rate_limit` (real per-IP via ClientIPMiddleware — `RateLimitMiddleware.PATH_LIMITS`/`_bucket_key` buckets on the Fly proxy IP and would be GLOBAL; a per-IP limit of 5/hr is pinned in Task 2).
- **Competitor-precedent (member-key escalation + the api-created_by problem):** keys can be minted by any session member; `created_by` is owner/member UUID | the literal `"api"` (POST /v1/team/keys — the dashboard Keys tab, THE primary human key-mint surface) | an anon `identity` string | legacy NULL. **Fix (in this plan):** `create_api_key` (POST /v1/team/keys, hosted_api.py:3043) switches `created_by` from `"api"` to the session user's UUID (plumb get_current_user; cover the analytics ripple at hosted_api.py:1204 + the audit actor) so dashboard-minted keys DO exchange. Legacy `"api"`/NULL keys → 403 `KEY_NOT_USER_MINTED` with accurate copy ("this older key type can't be used to sign in — mint a new key in the dashboard or use GitHub/Google"). The exchange mints the session for the key's CREATOR user (no escalation: a member's key mints the member's session). Anon teams (identity keys, no owner) → `ANON_TEAM_NO_OWNER` claim funnel; identity-string keys on a CLAIMED team → `KEY_NOT_USER_MINTED` (decision tree pinned in Task 2).
- **Skip justifications:** the scoping research (docs/scoping/2026-08-19-1511-auth-unification.md + docs/auth-architecture.md) covers the remaining axes; no new third-party deps.

### Integration Surface Map

| Surface | Layer | Test |
|---|---|---|
| `POST /v1/session/login` (hosted_api.py) | API | `tests/test_session_login.py` (FakeControlPlane + monkeypatched httpx; mirrors test_claim_endpoints.py) |
| GoTrue admin GET user / generate_link / verify calls | API (external) | monkeypatched httpx: assert **GET** /users/{id}, generate_link body, `/verify {token_hash, type:"magiclink"}` body; phantom-owner TOCTOU; double-submit retryable |
| Key validation parity (`resolve_api_key`) | API | invalid/revoked/expired/disabled → 401; suspended → 403 |
| `dashboard_key_login` gate (forced via split reason fn) | API | 403 for `dashboard_login_disabled` teams |
| Mint target = key creator (team member with auth user); api/identity/NULL created_by → 403 KEY_NOT_USER_MINTED; anon → ANON_TEAM_NO_OWNER | API | per-created_by-shape tests |
| `supabase-session.js` shared helpers | Client | `tests/test_cross_subdomain_cookie_sync.py` (helper presence + gate-predicate pins + dashboard public/ parity) |
| /auth loginWithApiKey → exchange → setSession | Client | form-safety e2e (exchange path); loop-regression e2e |
| Dashboard gate + !authed render (claim-paste only) | Client | `tests/e2e/test_dashboard_gate.py` (harness: see Task 5) |
| Welcome validity + 401 → clear → /auth | Client | welcome-e2e (validity + no-loop) |
| Last-used cookie + pills | Client | static pins in test_cross_subdomain_cookie_sync.py + e2e |

### Journey Test Map

### Journey: Browser API-key login
1. Paste a `tt_` key minted for a team member (owner/member UUID `created_by`) on /auth's API-key modal → **Acceptance:** session created for that user (cookie), redirected to the dashboard, which renders (not a key card / not a redirect back) → **Test:** `tests/e2e/test_session_login_flow.py` (also covers the api/identity-key rejection copy)
2. Paste an anon-team key → **Acceptance:** routed to the dashboard claim card (tt_claim_pending set) → **Test:** same e2e, anon branch
3. Paste an invalid key → **Acceptance:** "Invalid API key" in the modal, no redirect → **Test:** form-safety e2e

### Journey: Unauthenticated navigation
4. Visit app.premiselabs.co with no session/claim → **Acceptance:** instant redirect to /auth (no key card, no checking screen) → **Test:** test_dashboard_gate.py
5. Visit /welcome with a stale/expired session → **Acceptance:** cleared + redirected to /auth (no welcome render, no loop) → **Test:** welcome-e2e validity

### Failure Modes
- Cookie-blocked browser after exchange → **Expected:** clear "cookies required" error (readValidSession() post-check) → **Test:** unit/e2e
- GoTrue generate_link double-submit → **Expected:** retryable (not fatal) → **Test:** test_session_login.py
- Mint-target user deleted between GET and mint (TOCTOU) → **Expected:** ACCOUNT_MISSING error, no session returned → **Test:** test_session_login.py
- Callback hash on welcome 401 → **Expected:** hash stripped + session cleared before /auth → **Test:** welcome-e2e

**Tech Stack:** FastAPI (hosted_api.py), httpx (GoTrue admin), supabase-js v2 (pinned UMD), vanilla JS (supabase-session.js), React (dashboard), pytest + Playwright.

---

## Task 1: API — owner resolution + session-mint helpers

**Intent:** Give the exchange endpoint its two primitives: resolve the MINT-TARGET user for a key (its creator, a team member with an auth user — canonical email from the GoTrue user row, never `teams.email`) and mint a session server-side via GoTrue admin generate_link + /verify.
**Acceptance:** `tortoise/supabase_control.py` exposes `mint_target_user_for_key(cp, key_created_by, team_id)` → `user_id | None` (control-plane fact ONLY, FakeControlPlane-testable; None covers the anon/identity/api/NULL shapes — the endpoint branches on the key's `created_by` shape first); `tortoise/hosted_api.py` exposes `_gotrue_admin_get_user(user_id)` (service-role **GET** `/auth/v1/admin/users/{id}`; 404 → None) and `_gotrue_admin_mint_session(email)` (admin POST generate_link {type:magiclink} → **`token_hash` = the `hashed_token` field verbatim** → service-role POST /verify {token_hash, type:"magiclink"} → session dict; double-submit/otp_expired surfaced as a retryable error, not a crash). Unit tests cover the created_by shapes + the mint call chain (exact methods/bodies asserted).
**Files:**
- Create: `tests/test_session_login_helpers.py`
- Modify: `tortoise/supabase_control.py`, `tortoise/hosted_api.py`

**Step 1: Write the failing helper tests** (`tests/test_session_login_helpers.py`): `mint_target_user_for_key` (created_by = owner/member UUID → user_id; created_by = "api"/identity/NULL → None) with a FakeControlPlane; `_gotrue_admin_get_user` (monkeypatched httpx: assert **GET** /users/{id}, service-role bearer; 404 → None); `_gotrue_admin_mint_session` (assert generate_link POST body `{type:"magiclink", email}`; assert /verify POST body `{token_hash, type:"magiclink"}` with `token_hash` = the response's `hashed_token` verbatim; returns the session dict; a double-submit simulate — second generate_link then /verify returns otp_expired → the helper surfaces it as a retryable error, not a crash).
**Step 2:** Run `uv run pytest tests/test_session_login_helpers.py -v` — expect FAIL (helpers absent).
**Step 3:** Implement `mint_target_user_for_key` in `supabase_control.py` (key `created_by` is a UUID on team_memberships → user_id; api/identity/NULL → None); the GoTrue helpers in `hosted_api.py` (mirror `_supabase_admin_create_user`'s httpx pattern; **GET** for the user; generate_link → `/verify` with `type:"magiclink"`; `token_hash` verbatim).
**Step 4:** Run — expect PASS.
**Step 5:** Commit.

## Task 2: API — POST /v1/session/login endpoint

**Intent:** The exchange endpoint: key parity, suspension, forced dashboard_key_login gate, member-key→owner gating, rate limit, audit, and the response shapes.
**Acceptance:** `POST /v1/session/login` (key in the JSON body) returns the full session JSON on success; 401 invalid/revoked/expired/disabled key; 403 suspended; 403 `dashboard_login_disabled` (forced gate); **evaluation order pinned: (1) key validity → (2) suspension → (3) forced dashboard-login gate → (4) created_by decision tree: UUID → mint-target = that user; "api"/NULL → 403 `KEY_NOT_USER_MINTED`; identity string → `is_anon_team(team_id)` ? 403 `ANON_TEAM_NO_OWNER` : 403 `KEY_NOT_USER_MINTED` → (5) membership pre-check (mint-target is an active member of the team; else 403 `KEY_NOT_USER_MINTED`) → (6) GoTrue fetch: 404 → 403 `ACCOUNT_MISSING`; mint → (7) post-verify membership sanity check (TOCTOU backstop) → reject if absent**; 429 rate-limited. GoTrue transport/5xx → 502; otp_expired/token-consumed → retryable 503 (client does NOT feed it into the lockout bucket). Rate limit: `_check_ip_bucket_rate_limit` with a **5/hr/IP window** (real per-IP via ClientIPMiddleware, mirroring the register/claim limiters — PATH_LIMITS buckets on the Fly proxy IP and would be global); registered in `SKIP_AUTH`; audit `session_mint`. **Task 2 also fixes the api-created_by problem:** `create_api_key` (POST /v1/team/keys, hosted_api.py:3043) records the session user's UUID — the mechanism: attach `session_user_id` in `get_current_team_session`'s SESSION branch (a second `get_current_user` dependency would 401 key-auth mints; key-auth mints keep `created_by="api"` and are 403'd by the decision tree). The analytics at hosted_api.py:1204 is VERIFY-ONLY — `first_api_call(team.get("created_by") or team_id, …)` already forwards created_by; no edit needed. The recovery-cap auto-revoke guard (hosted_api.py:6728-6746, `created_by != user_id`) now correctly protects the minting user's own dashboard keys — beneficial; check the writer-inventory/recovery tests in Task 7. **Update `tests/test_writer_inventory.py:192` to the session-UUID assertion — fixture prerequisites: clear the `get_current_team` override, send an `eyJ`-prefixed bearer, and seed FakeControlPlane memberships so `_session_user_team` resolves (the override branch keeps `created_by="api"`); keep `"api"` for the key-auth/override path; add the file to Task 7's battery.**
**Files:**
- Create: `tests/test_session_login.py`
- Modify: `tortoise/hosted_api.py`, `tests/test_writer_inventory.py` (created_by assertion)

**Step 1: Write `tests/test_session_login.py`** (mirror test_claim_endpoints.py: FakeControlPlane, monkeypatched httpx): success 200 — **member-minted key (created_by = member UUID) → 200 with `session.user.id` == the key's created_by (no escalation test)**; dashboard-minted key (created_by = session UUID after the create_api_key fix) → 200; invalid key 401; suspended 403; `dashboard_login_disabled` 403 (forced gate); NULL-created_by key 403 KEY_NOT_USER_MINTED; "api"-created_by key 403 KEY_NOT_USER_MINTED; anon (identity, no owner) 403 ANON_TEAM_NO_OWNER; identity on a claimed team 403 KEY_NOT_USER_MINTED; removed-creator (UUID but not an active member) 403 KEY_NOT_USER_MINTED pre-mint; creator-404 403 ACCOUNT_MISSING; rate-limit 429 (bucket cleared between tests); TOCTOU (creator deleted between GET and mint → no session returned).
**Step 2:** Run — expect FAIL (no endpoint).
**Step 3:** Implement: `resolve_api_key` parity (via `_get_current_team_supabase`); **forced** `dashboard_key_login` gate — split `_check_dashboard_key_login` into a reason fn `_dashboard_key_login_reason(team) -> str|None` called unconditionally (the header-sniffing behavior stays for the OTHER management endpoints); the pinned evaluation order (acceptance) with the membership PRE-check before the mint; mint + post-verify sanity check; **`create_api_key` fix (created_by = the session user UUID read from `team['session_user_id']`, attached in `get_current_team_session`'s session branch — NOT a second `get_current_user` dependency, which would 401 key-auth mints; analytics + audit ripple)**; return the session; register in SKIP_AUTH + the per-IP `_check_ip_bucket_rate_limit` bucket; `_async_audit` session_mint.
**Step 4:** Run — expect PASS.
**Step 5:** Commit.

## Task 3: Shared client helpers (supabase-session.js)

**Intent:** ONE validity predicate + clear + last-used helpers shared by all three pages and the dashboard — loop-safety by construction.
**Acceptance:** `supabase-session.js` exposes `window.readValidSession()`, `window.clearStoredSession()`, `window.getLastAuthMethod()`, `window.setLastAuthMethod()`, `window.bounceToAuth(search, hash)` (null-safe, ES5). `readValidSession`'s legacy-key derivation covers `sb-ybetwichurajbfswfeqa-auth-token` AND `sb-127-…` (the welcome e2e's `_seed_local_session` uses the local key — the hardened gates must not redirect a seeded session). `bounceToAuth` resolves the target: on the app origin (`https://app.premiselabs.co`) → absolute `https://tortoise.premiselabs.co/auth` + search/hash; elsewhere → relative `/auth` + search/hash (with a `window.__AUTH_BASE_URL` test seam — set via `page.addInitScript` in the e2e, default empty → real origin). The file is copied to `apps/dashboard/public/assets/supabase-session.js`. `test_cross_subdomain_cookie_sync.py` extended: **helper-presence pins + `public/assets/supabase-session.js` byte-parity + the cookie write/remove templates unchanged** (the dashboard `index.html` PAGES entry is DEFERRED to Task 5, where the script tag + gate swap land — adding it here would make Task 3's "expect PASS" unreachable since index.html doesn't load the shared script until Task 5). **Load order pinned:** wherever a page's inline head gate calls the shared helpers, the `<script src="/assets/supabase-session.js">` tag must precede it (move it to <head>), with `typeof window.readValidSession === 'function'` guards + an inline fallback.
**Files:**
- Create: `website/apps/dashboard/public/assets/supabase-session.js` (copy)
- Modify: `website/assets/supabase-session.js`, `tests/test_cross_subdomain_cookie_sync.py`

**Step 1: Write the static-sync test updates** (helper names present; public/ copy byte-identical; cookie write/remove templates unchanged). The dashboard `index.html` PAGES entry is deferred to Task 5 (its script tag + gate swap land there).
**Step 2:** Run — expect FAIL (helpers absent).
**Step 3:** Implement the helpers (readValidSession: cookie→legacy, strict access_token + expires_at present + future; clearStoredSession: remove cookie with the Domain/Secure logic + the legacy localStorage key; get/setLastAuthMethod: `tt_last_auth_method` parent-domain cookie, one-time migration from the dashboard's legacy `tortoise_last_auth_method`; bounceToAuth: origin-aware target + search/hash preservation). Copy to the dashboard public/.
**Step 4:** Run — expect PASS.
**Step 5:** Commit.

## Task 4: /auth — exchange flow + Last-used + gate hardening

**Intent:** loginWithApiKey becomes a real session login; the gates stop trusting presence; Last-used pills appear.
**Acceptance:** On /auth (signup.html): pasting a key calls `POST /v1/session/login`, on 200 does `setSession` + verifies `readValidSession()` (cookie-blocked → clear "cookies required" error) + writes `tt_last_auth_method=apikey` + `location.replace(DASHBOARD_URL)`. Error mapping (modal, no redirect unless noted): 401 invalid key; 403 suspended; 403 `dashboard_login_disabled` → "use GitHub/Google" copy; 403 `ANON_TEAM_NO_OWNER` → set tt_claim_pending + redirect to `app.premiselabs.co/?claim=1`; 403 `KEY_NOT_USER_MINTED` → "this key can't be used to sign in — mint a new key in the dashboard or use GitHub/Google"; 403 `ACCOUNT_MISSING` → "account missing — contact support"; 429 → the existing login lockout; 502/503 → transient "try again" (NOT fed into the lockout). **The ANON funnel needs a `setClaimPendingMarker()` helper on /auth (signup.html has no writer today — copy main.jsx's parent-domain cookie write, ~line 27).** **setSession shape note:** the GoTrue AccessTokenResponse (access_token, refresh_token, expires_in, token_type, user) maps directly to supabase-js `setSession`; the shared cookie write + SIZE_GUARD strip must not drop fields setSession validates. The head gate AND the async `getSession` bounce both require a valid session (expires_at present + future — via the shared helpers). "Last used" pill on the matching option button; written on every success path (OAuth pre-redirect, email, exchange). The dead tortoise-origin `tortoise_api_key` write is removed + any residue cleared.
**Files:**
- Modify: `website/signup.html`, `tests/e2e/test_signup_form_safety_e2e.py`, `tests/e2e/test_legal_pages.py`, `tests/test_cross_subdomain_cookie_sync.py` (gate-predicate pins)

**Step 1: Write/extend the tests** (exchange path via mocked /v1/session/login; head-gate validity pin in test_cross_subdomain_cookie_sync.py; last-used cookie + pill pins; the async-bounce validity pin).
**Step 2:** Run — expect FAIL.
**Step 3:** Implement (rewrite loginWithApiKey; harden both gates via the shared helpers; last-used wiring in setAuthMode/success paths; remove the tortoise-origin key write + clear residue).
**Step 4:** Run — expect PASS.
**Step 5:** Commit.

## Task 5: Dashboard — remove key-only card; claim-paste only; shared gate

**Intent:** The dashboard never shows auth UI; the shared helpers power its gate.
**Acceptance:** `index.html` loads the shared supabase-session.js (from the public/ copy → dist/ via the build, in <head> BEFORE the inline gate) and its gate uses `readValidSession()` + claim-intent exemptions (tt_claim_key + tt_claim_pending/?claim=1) — **no storedKey exemption**. **KEY_STORAGE precise cut:** delete (1) the gate's storedKey exemption, (2) the key-only card JSX (~1589-1620), (3) the key-paste `login()` handler (~752), (4) the `authMode==='apikey'` unauthenticated paths, (5) the AUTHED apikey-mode wrappers (the claimed-team redirect ~1640-1657); **KEEP the session-authed bootstrap block (~563-619: reuse + mint + cache) AND all session-authed KEY_STORAGE writers (switchTeam 1026/1050, rotation 1347, revoke re-mint 1418, createKey 1502) untouched** — they require a valid session. **The anon Protect screen (~1659+) is REPURPOSED as the claim-paste screen** (claimSignIn/claimEmailPassword + the email+password claim option move onto it — /v1/claim/email stays live); its render condition is a **claim-intent predicate** (tt_claim_key + tt_claim_pending/?claim=1) evaluated BEFORE the (deleted) `!authed` key-card branch — claim-intent users have no session/team. **The claim-paste input must be wired to `setApiKey` + `apiKeyRef.current`** (claimSignIn/claimEmailPassword at ~783/830 read `apiKeyRef.current || localStorage.getItem(KEY_STORAGE)` — the ANON funnel can't transport the key cross-origin, so the screen re-collects it; without this wiring every claim fails "Your API key is missing from this session"). **Claim-failure display:** `claimError` renders ON the claim-paste screen (markers are cleared on failure → claim-intent evaporates; a sessionless user must still see the error). **SignOut fallback:** `logout()` calls `bounceToAuth()` AFTER `signOut()` resolves (the mount effect runs once at load — a same-session signOut must drive its own redirect), with a signOut → /auth assertion added to `test_dashboard_gate.py`. The **mount effect's no-session branch exempts tt_claim_key AND tt_claim_pending/?claim=1** (rendering the claim-paste screen for that state — otherwise the ANON funnel from /auth dead-ends back at /auth), else `bounceToAuth()` (origin-aware → absolute `https://tortoise.premiselabs.co/auth`); the mount effect validity-checks getSession. **Last-auth sync:** main.jsx's last-method writers (~686, 741, 760, 851) migrate to the shared `setLastAuthMethod` (the legacy `tortoise_last_auth_method` key stays read-only for the one-time migration). dist rebuilt (assert `dist/assets/supabase-session.js` exists and is byte-identical to the shared file).
**E2E harness (pinned):** serve `dist/` with `npx wrangler@4 pages dev dist --port 8790`; `page.addInitScript(() => { window.__AUTH_BASE_URL = 'https://tortoise.premiselabs.co' })` before first goto so the gate emits the absolute target; intercept `https://app.premiselabs.co/*` and `https://tortoise.premiselabs.co/*` via Playwright `route` — the handler REWRITES the URL to the local `wrangler pages dev` origin and re-fetches (`page.request.get`) so the app actually renders; opt-in env `RUN_DASHBOARD_E2E=1` (mirrors RUN_LEGAL_E2E).
**Files:**
- Modify: `website/apps/dashboard/index.html`, `website/apps/dashboard/src/main.jsx`, `website/apps/dashboard/dist/` (rebuild), `tests/test_cross_subdomain_cookie_sync.py` (add the dashboard `index.html` PAGES entry — **assert the shared-script tag + a gate-helper reference (`readValidSession(`/`bounceToAuth(`), NOT `createTortoiseSupabaseClient(` — the dashboard builds its client in main.jsx, so index.html never contains that call**)
- Create: `tests/e2e/test_dashboard_gate.py`

**Step 1: Write `tests/e2e/test_dashboard_gate.py`** (RUN_DASHBOARD_E2E opt-in; harness per above): no session/claim → redirect to /auth; claim in flight (tt_claim_key only, tt_claim_pending only, and ?claim=1 only — three separate assertions) → claim paste shows; stored key alone → redirect to /auth; failed claim → claimError visible on the claim-paste screen.
**Step 2:** Run — expect FAIL.
**Step 3:** Implement (index.html gate + shared script; main.jsx card removal + claim-only render + mount-effect validity + KEY_STORAGE disposition; rebuild dist; assert the dist copy; edit `tests/test_cross_subdomain_cookie_sync.py` — add the dashboard `index.html` PAGES entry with the per-page variant assertion: shared-script tag + `readValidSession(`/`bounceToAuth(` reference, NOT `createTortoiseSupabaseClient(`). **Claim-paste render predicate:** keep a stable local claim-state fallback (claimError/claim-mode state) so the screen survives marker-clear on failure and the error renders.
**Step 4:** Run — expect PASS.
**Step 5:** Commit.

## Task 6: Welcome — validity + 401 → clear → /auth

**Intent:** Welcome never renders for unauthenticated users, including stale-session holders; no loops.
**Acceptance:** The head gate + async waitForSession reject missing/expired expires_at (via the shared helpers); provisioning 401 → strip hash + clearStoredSession() + location.replace('/auth'). Callback hash exemptions preserved.
**Files:**
- Modify: `website/welcome.html`, `tests/e2e/test_welcome_page.py`

**Step 1: Write/extend welcome e2e** (target: `WELCOME_URL` overridable, default live; stale session → /auth, no welcome render, no loop; 401 → cleared + /auth — the provisioning call is route-intercepted to return 401; callback hashes still work). Reuse `_seed_local_session`. **Update `test_welcome_provision_failure_retries_once_then_contact_support` (test_welcome_page.py:539): under the new contract a 401 redirects, so the retry-once + #error-state path is re-pinned to a NON-401 failure (route-intercept the provisioning call as a 500) alongside a NEW test for the 401 → cleared + /auth contract.**
**Step 2:** Run — expect FAIL.
**Step 3:** Implement (gate predicate via readValidSession; 401 handler clears + replaces; async path validity).
**Step 4:** Run — expect PASS.
**Step 5:** Commit.

## Task 7: Loop-regression + full-battery verification

**Intent:** Prove the user's four flows end-to-end and the whole suite stays green.
**Acceptance:** A loop-regression e2e (key login → exchange → cookie → dashboard renders; missing cookie → redirect) passes; the full static + e2e batteries pass; the four live flows verified in a browser.
**Files:**
- Create: `tests/e2e/test_session_login_flow.py`

**Harness (pinned):** the loop e2e covers the DASHBOARD half of the journey — the exchange endpoint itself is covered by test_session_login.py. Two origins: site root at `wrangler@4 pages dev . --port 8788` (the /auth page) + dashboard dist at `:8790` (Task 5 harness); cross-rewrites via Playwright route (intercepted `https://tortoise.premiselabs.co/auth` → the :8788 server; `https://app.premiselabs.co/*` → the :8790 server), `__AUTH_BASE_URL` addInitScript, and `RUN_DASHBOARD_E2E=1` opt-in. Flows: (a) mock `POST /v1/session/login` on :8788 → paste key on /auth → assert the session cookie is written and :8790 (dashboard) renders; (b) no cookie → :8790 redirects to /auth; (c) anon-team exchange error → tt_claim_pending set + redirected to `?claim=1` → claim-paste shows.

**Step 1:** Write the loop-regression e2e.
**Step 2:** Run — expect FAIL.
**Step 3:** Wire the mocked exchange + bridge; make it pass.
**Step 4:** Run the full battery (`uv run pytest tests/test_signup_form_safety.py tests/test_cross_subdomain_cookie_sync.py tests/test_website_static.py tests/test_welcome_url_consolidation.py tests/test_session_login.py tests/test_session_login_helpers.py tests/test_writer_inventory.py -q` + the legal/form-safety/welcome e2e suites + the dashboard e2e with the harness + any recovery-cap tests touched by the created_by change).
**Step 5:** Browser-verify the four flows against wrangler dev. Commit.

## Task 8: Docs + commit-workflow

**Intent:** Document the exchange + the architecture update; ship through the review gates.
**Acceptance:** `docs/auth-architecture.md` updated (the exchange endpoint + the cross-origin key-transport fix + the async-bounce loop); the scoping doc `docs/scoping/2026-08-19-1511-auth-unification.md` is AUTHORED from this plan's research content (it does not yet exist in the repo — write it from the Pattern Research + architecture above, with the mint-target design, the error codes, and `_check_ip_bucket_rate_limit` — NOT PATH_LIMITS) and committed; issue #1511 labels transition; PR through commit-workflow (code review + second-model gate) → merge → deploy.
**Files:**
- Create: `docs/scoping/2026-08-19-1511-auth-unification.md`
- Modify: `docs/auth-architecture.md`

**Step 1:** Update the doc (new endpoint, the D1a design, the residual-risks additions from the scoping).
**Step 2:** `commit-workflow` (preflight, PR, code-review gate, merge, deploy).
**Step 3:** Post-deploy browser verification of the four user flows; close the issue.
