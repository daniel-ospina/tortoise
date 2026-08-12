---
title: "#863 Recovery-Flow Rate-Limit Hardening + Mechanism-Accurate 429 Copy"
type: engineering
domain: platform
doc_status: live
subjects.team: epistemic-team
created: 2026-08-11
aboutSubjects: tortoise
aboutObjects: signin-page, signup-page, welcome-page, hosted-api
---

<!-- research-path: docs/plans/2026-08-10-801-signup-rate-plan.md (pattern ancestor) -->

# #863 Recovery-Flow Rate-Limit Hardening + Mechanism-Accurate 429 Copy — Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Harden the recovery surface against project-wide email-bucket exhaustion (client lockout mirroring #801), make all 429 copy mechanism-accurate (email bucket vs per-IP) on both auth pages, and keep the pins/e2e in lockstep.

**Team:** tortoise
**Role:** implementer

**Architecture:** Three layers, all in-repo:
1. **Client (signin.html):** new "Forgot password?" request-link flow (`auth.resetPasswordForEmail`) + the #801 two-tier lockout machinery (1h email-tier / 60s per-IP tier, sessionStorage-persisted, countdown, early-return guard), with **tier-aware gating** (email tier disables only `#btn-recovery` — sign-in never sends email and must stay usable; short tier disables both). Sign-in 429s get the short tier.
2. **Client (welcome.html):** reset-password landing — recovery links redirect to `origin + /welcome.html` (the same target the #801 signup confirmation flow already uses, so no new GoTrue redirect-allowlist entry is needed). `PASSWORD_RECOVERY` event (supabase-js v2) shows a `#reset-form`; `auth.updateUser({password})` completes the journey. Hash-snapshot fallback covers the documented case where the event is not emitted (supabase/auth#1948).
3. **Client copy + API:** both pages' `AUTH_ERROR_MAP`/`humanizeAuthError` split the rate-limit mapping: `over_email_send_rate_limit` (+ "email rate limit" msg) → **"Signup emails are temporarily exhausted (too many signups right now). Try again in about an hour."** (issue's exact proposal, acceptance (b) contract); `over_request_rate_limit(_ip)`/generic `rate_limit` → network-attribution copy. ⚠️ **Constants must be declared ABOVE the AUTH_ERROR_MAP literal** (TDZ hazard — the map is evaluated at script load; referencing a `const` declared later kills the whole inline script, the #527 regression class). ⚠️ **The email-bucket map entry must come FIRST with a pseudo-code** (`codes: ["over_email_send_rate_limit", "email_rate_limit"]`): the map loop's substring pass would otherwise match the network entry's bare "rate limit" against an "email rate limit exceeded" message (dead-code fallback → wrong copy). `tortoise/hosted_api.py` 429 pass-throughs carry the mechanism (`detail: {message, error_code}`) so the server-first signup path is tier-accurate too; the stale #801-era "per-IP bucket" docstrings are corrected to project-wide.

**Threat-closure map (from scope-verify P2s):** the client lockout closes browser-originated retry loops on the recovery surface (the first live enforcement of the #801 pattern — signup's server-first path bypasses the bucket); direct-API spam is pre-capped by GoTrue per-IP limits (recover 30/5min) and ultimately by the server 429; residual dashboard-level posture (captcha, bucket headroom) is tracked in filed issues, not silently absorbed. Email-change (`PUT /auth/v1/user`, authenticated, no captcha hook, 2 emails/change under `double_confirm_changes`) has no in-repo UI → acceptance (a) for that surface is explicitly amended to "GoTrue server-side limits; client guard tracked in #<filed issue>".

### Pattern Research

Third-party deps: **zero new**. supabase-js v2 (CDN, already loaded by all three pages) is the only external library; the recovery pattern was verified against Supabase docs (2026-08-11): `resetPasswordForEmail(email, { redirectTo })` → recovery-link redirect → `onAuthStateChange` `PASSWORD_RECOVERY` event → `updateUser({ password })`. Known pitfall (supabase/auth#1948): `PASSWORD_RECOVERY` is sometimes not emitted (only `SIGNED_IN`) → plan adds a synchronous `type=recovery` hash snapshot taken **before** `createClient` (supabase-js consumes the hash during init) as a fallback trigger.

The #801 plan (`docs/plans/2026-08-10-801-signup-rate-plan.md`) is the pattern ancestor: same lockout constants, same two-tier split, same sessionStorage key shape, same early-return guard, same monotonic-tier copy rule. This plan diverges only where the surface demands it (tier-aware button gating, page-scoped keys).

**Copy decisions (controller, after scope-verify P3s):**
- Email-tier copy is the issue's exact proposal, used verbatim on both surfaces (single constant `EMAIL_BUCKET_RATE_LIMIT_COPY`). A consumer-neutral variant ("Email sending is temporarily exhausted…") was considered and rejected: acceptance (b) quotes the issue's wording, the bucket is in practice exhausted by signups, and one constant keeps the static pins unambiguous.
- Generic `rate_limit` code / bare "rate limit" message → network copy (conservative; the email bucket always surfaces as `over_email_send_rate_limit` or "email rate limit", which `isEmailBucketError` detects first). The email-bucket substring match is made reachable by the pseudo-code entry (above), not by the explicit fallback branch alone.
- `NETWORK_RATE_LIMIT_COPY` = "Too many attempts from this network. Please wait about an hour and try again." (issue-verbatim) on BOTH pages — signup.html's static map entry **loses the `tortoise signup` CLI pointer** (it stays in the API's 429 `message` for API consumers; the server-429 client path shows tier copy only). Decided: one shared constant, no page-specific pointer variants.
- **Server-429 unknown-code default:** `serverSignup` parses the API's detail (dict `{message, error_code}` after Task 1, or legacy string): `error_code` wins; a code-less STRING detail is hint-scanned — contains "registration" → `over_request_rate_limit_ip` (per-IP short tier, network copy); contains "email"/"rate limit exceeded" → `over_email_send_rate_limit`; otherwise fail-safe default `over_email_send_rate_limit` (1h email tier — errs toward protecting the shared bucket, the issue's primary threat).
- **Register-bucket (3/hr/IP) 429 → short 60s tier, accepted retry cycle:** the #801 per-IP precedent applies; after 60s the user may retry and get another 429 (server holds the hourly bucket) — documented as accepted UX for a defense-in-depth guard. A Retry-After-driven third tier was considered and rejected (over-engineering for a client guard).

### Integration Surface Map

| Surface | Type | Data Flow | Contract | Test Layer |
|---|---|---|---|---|
| GoTrue `POST /auth/v1/recover` | external service | out (client) | `resetPasswordForEmail(email, {redirectTo})` → `{data, error}`; 429 body `{code, error_code, msg}` | e2e (network mocked, RUN_LEGAL_E2E) |
| GoTrue `PUT /auth/v1/user` via `updateUser({password})` | external service | out (client) | returns `{data, error}`; 422 weak_password; session-required | e2e (network mocked) |
| GoTrue `POST /auth/v1/token` (sign-in) | external service | out (client) | `signInWithPassword` 429 `over_request_rate_limit_ip` | e2e (network mocked) |
| hosted_api `POST /v1/signup/email` 429 | API (in-repo) | in (client) / out (server) | `detail: {message, error_code}` — both 429 sites (register-bucket + GoTrue pass-through) | unit (`tests/test_email_signup.py`) + static pin |
| sessionStorage lockout keys | state | internal | `tortoise_signin_rate_limited_until` / `_tier` (page-scoped; signup keys untouched) | e2e (mocked) |
| recovery hash → session (welcome.html) | state | internal | `type=recovery` in URL hash + `PASSWORD_RECOVERY` event | e2e (mocked) |
| Static HTML (signup/signin/welcome) | config | - | copy literals + form safety contract (#527) | unit (`tests/test_signup_form_safety.py`, node --check gate) |

Bug-pattern flags: (1) lockout copy must not decay with remaining time → monotonic tier in storage (mirror #801); (2) a lockout on signin must never disable sign-in for an email-tier event → tier-aware button gating; (3) supabase-js returns (not throws) HTTP errors → destructure `{data, error}` (mirror #801 review P1); (4) recovery link landing must not dead-end → reset panel + expiry error copy.

### Journey Test Map

**Journey: User forgets password → resets it → signs in**
1. Clicks "Forgot password?" on signin.html → recovery form appears → **Test:** e2e recovery-429 lockout test + static pin
2. Submits email → reset link email → **Test:** e2e mocked 429 (no real email)
3. Clicks link → lands welcome.html → reset panel shows → **Test:** e2e recovery-hash test
4. Sets new password → `updateUser` → success message → **Test:** e2e mocked success
5. Signs in with new password → **Test:** existing sign-in path (static pin)

**Failure modes:**
- Recover 429 (email bucket) → email-tier copy + 1h lockout on `#btn-recovery`; sign-in stays usable → **Test:** e2e `test_recover_429_email_bucket_sets_1h_lockout`
- Sign-in 429 (per-IP) → network copy + 60s lockout on both buttons → **Test:** e2e `test_signin_429_per_ip_short_tier`
- Recovery link with expired/invalid session → reset form shows; updateUser error → "link expired — request a new one" → **Test:** e2e (mocked 401)
- PASSWORD_RECOVERY event not emitted → hash snapshot fallback still shows reset panel → **Test:** e2e recovery-hash test (hash-driven, not event-driven)
- Server-first signup 429 → tier-aware copy + lockout (was copy-only) → **Test:** unit (API error_code) + static pin

**Tech Stack:** Python 3.11+ (FastAPI, httpx), static HTML + vanilla JS, supabase-js v2 (CDN), pytest + Playwright (gated e2e), node --check syntax gate.

### UX Design Decisions

| # | Decision Type | User Choice | Rationale |
|---|---|---|---|
| 1 | Copy & messaging | Email-bucket 429 → "Signup emails are temporarily exhausted (too many signups right now). Try again in about an hour." (issue's exact copy) | Acceptance (b) contract; mechanism-accurate; single constant both surfaces |
| 2 | Copy & messaging | Per-IP 429 keeps network-attribution copy (hour for static map / minute for live short-tier countdown) | Mechanism-accurate split per issue |
| 3 | Layout & hierarchy | Recovery = hidden `#recovery-form` toggled by "Forgot password?" link on signin.html; reset = `#reset-form` panel replacing welcome content on recovery landing | Minimal new chrome; mirrors signup's form patterns; welcome.html already the allow-listed redirect target |
| 4 | Visual affordances | Disabled submit + live countdown label during lockout (mirror signup.html) | #801 pattern; explains why the button is off |
| 5 | Behavioral | Email-tier lockout disables only the recovery button (sign-in stays enabled) | Sign-in never sends email; blocking it would strand legitimate users on a 1h lockout |
| 6 | Responsive | No new layout beyond existing single-column forms | Same as both auth pages today |

**Pending:** none.

### Verification Plan

- **Domain:** code. **Layers:** unit (static pins + API 429 contract) + gated e2e (mocked network — zero real emails, mirroring the #801 e2e consumption budget).
- Static: `python -m pytest tests/test_signup_form_safety.py tests/test_email_signup.py -v` (node --check gate included).
- E2E: `RUN_LEGAL_E2E=1 BASE_URL=http://127.0.0.1:8788 python -m pytest tests/e2e/test_signup_form_safety_e2e.py -v` with `cd website && npx wrangler@4 pages dev . --port 8788` (if wrangler available; otherwise documented + CI covers it).
- Skipped: DB, content, config domains (no schema/content changes).

---

### Task 1: hosted_api.py — mechanism-accurate 429 pass-through + stale docstring fix

**Intent:** The server-first signup path is the primary hosted signup surface; its 429 currently folds both mechanisms (per-IP register bucket, project-wide email bucket) into one undifferentiated string, so the client cannot pick the correct tier/copy. Also correct the stale #801-era "per-IP bucket" docstrings (the misattribution class this issue exists to eliminate) in the region being touched.

**Acceptance:** `/v1/signup/email` 429 responses carry `detail: {message, error_code}` with the correct mechanism code for both 429 sources; `tests/test_email_signup.py` asserts the codes; docstrings say project-wide.

**Files:**
- Modify: `tortoise/hosted_api.py` (email_signup endpoint ~L1808-1860, `_supabase_admin_create_user` ~L1755, `_signup_email_confirm` ~L1743)
- Test: `tests/test_email_signup.py` (~L167-215)

**Step 1: Update the failing tests**

In `tests/test_email_signup.py::test_gotrue_429_passthrough_with_cli_pointer`: assert `r.json()["detail"]["message"]` contains "tortoise signup" and `r.json()["detail"]["error_code"] == "over_email_send_rate_limit"`. In `test_shared_ip_bucket_3_per_hour`: assert `r.json()["detail"]["error_code"] == "over_request_rate_limit_ip"`. Add `test_gotrue_429_per_ip_code_passthrough`: GoTrue 429 with `error_code: "over_request_rate_limit"` → detail.error_code passthrough. Add `test_gotrue_429_code_less_msg_heuristic`: GoTrue 429 with NO error_code, `msg: "email rate limit exceeded"` → detail.error_code == "over_email_send_rate_limit"; and a non-email msg ("request rate limit reached") → detail.error_code == "over_request_rate_limit" (covers the msg-heuristic branch). Add `test_gotrue_429_error_code_wins_over_numeric_code`: GoTrue 429 with the REAL body shape `{"code": 429, "error_code": "over_email_send_rate_limit", "msg": "request rate limit reached"}` → detail.error_code must be "over_email_send_rate_limit" (the msg heuristic alone would give "over_request_rate_limit" — this case proves code-wins).

**Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_email_signup.py -v`
Expected: FAIL (detail is a string today).

**Step 3: Implement**

- In `email_signup`: wrap `await _check_register_rate_limit(request)` in try/except HTTPException; on 429 re-raise with `detail={"message": "Too many registration attempts. Please try again later.", "error_code": "over_request_rate_limit_ip"}`, `Retry-After: 3600`.
- ⚠️ **Extraction order trap (real GoTrue bodies):** GoTrue error bodies carry the numeric HTTP status in `code` and the stable code in `error_code` (`{"code": 429, "error_code": "over_email_send_rate_limit", ...}` — the repo's own e2e mocks use this shape). The existing `code = str(gb.get("code") or gb.get("error_code") or "")` therefore ALWAYS yields "429" and any known-code passthrough keyed on `code` is dead code. Specify: `raw = gb.get("error_code") or gb.get("error_description") or ""; code = str(raw).lower()`; only if that is empty, fall back to `gb.get("code")` **skipping all-digit values** (numeric status); `msg = str(gb.get("msg") or gb.get("message") or "").lower()`.
- GoTrue 429 pass-through: `detail={"message": <existing copy with tortoise signup pointer>, "error_code": <code if in ("over_email_send_rate_limit","over_request_rate_limit","over_request_rate_limit_ip") else ("over_email_send_rate_limit" if "email" in msg else "over_request_rate_limit")>}`.
- Fix docstrings: "SMTP per-IP send bucket"/"IP-bucketed (30 sends/hr/IP)" → project-wide bucket (30/hr for ALL users).

**Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_email_signup.py -v`
Expected: PASS (all email-signup tests).

**Step 5: Commit**

```bash
git add tortoise/hosted_api.py tests/test_email_signup.py
git commit -m "fix(863): mechanism-accurate 429 pass-through on /v1/signup/email + stale per-IP docstrings"
```

---

### Task 2: signup.html — mechanism-split copy + server-429 tier-aware lockout

**Intent:** The signup page's 429 copy misattributes the project-wide email bucket as a network problem (finding 2). Split the mapping by mechanism and close the #801 gap where the server-first 429 path showed copy but set no lockout.

**Acceptance:** `signup.html` maps `over_email_send_rate_limit` (+ "email rate limit" msg) to the email-bucket copy and per-IP codes to network copy; the server-429 branch (`outcome === "ratelimited"`) sets a lockout with the tier derived from the API's `error_code` (with string-detail hint-scan fallback); **no intermediate red state on the static suite** (existing pins stay green — see Task 5).

**Files:**
- Modify: `website/signup.html` (~L436-485 humanize, ~L498-580 lockout, ~L715-740 serverSignup, ~L805-815 429 branch)
- Test: `tests/test_signup_form_safety.py` (pin update, in Task 5's file — see Task 5)

**Step 1: Implement the copy split**

- ⚠️ Declare constants `EMAIL_BUCKET_RATE_LIMIT_COPY` and `NETWORK_RATE_LIMIT_COPY` at the TOP of the script, ABOVE the AUTH_ERROR_MAP literal (TDZ hazard — the map is evaluated at load; a `const` referenced before its declaration kills the entire inline script, the #527 regression class; `node --check` will NOT catch it).
- Split the AUTH_ERROR_MAP rate-limit entry into TWO entries, email-bucket entry FIRST with a pseudo-code so the map loop's substring pass matches before the network entry: `{codes: ["over_email_send_rate_limit", "email_rate_limit"], message: EMAIL_BUCKET_RATE_LIMIT_COPY}` then `{codes: ["over_request_rate_limit", "over_request_rate_limit_ip", "rate_limit"], message: NETWORK_RATE_LIMIT_COPY}`.
- Split the substring fallback: "email rate limit" → EMAIL_BUCKET copy; "rate limit" → network copy (belt-and-braces — the pseudo-code entry makes it reachable in the map loop).
- `rateLimitMessage()` email tier returns EMAIL_BUCKET copy.
- Note: signup's static-map network copy loses the `tortoise signup` CLI pointer (uniform NETWORK_RATE_LIMIT_COPY on both pages; pointer stays in the API message).

**Step 2: Make the server-429 branch tier-aware**

- `serverSignup`: on 429, parse `payload.detail` (dict `{message, error_code}` after Task 1, or legacy string); stash `lastServer429Code` + `lastServer429Message` (defaults `""`).
- Mechanism resolution order: (1) `error_code` if it is a known rate-limit code; (2) string hint-scan: detail/message contains "registration" → `"over_request_rate_limit_ip"`; contains "email" or "rate limit exceeded" → `"over_email_send_rate_limit"`; (3) fail-safe default `"over_email_send_rate_limit"` (1h email tier — errs toward protecting the shared bucket; only reachable on stale deployments, since the API always sends a code after Task 1).
- 429 branch: `const err = { code: <resolved code> };` `setRateLimitLockout(err); showError(isEmailBucketError(err) ? EMAIL_BUCKET_RATE_LIMIT_COPY : rateLimitMessage());` (mirrors the legacy-path handling; per-IP server 429s get the 60s short tier — accepted retry cycle documented in Copy decisions).

**Step 3: Syntax + sanity check**

Run: `node --check` on extracted inline scripts (or rely on the pytest gate), `python -m pytest tests/test_signup_form_safety.py -v`.
Expected: node check passes; existing pins stay GREEN (the network copy literal survives the split — the only red state before Task 5/6 is the gated e2e's old-copy assertion).

**Step 4: Commit**

```bash
git add website/signup.html
git commit -m "fix(863): mechanism-split 429 copy + tier-aware lockout on server-first signup 429"
```

---

### Task 3: signin.html — recovery request-link flow + two-tier lockout

**Intent:** Finding 1: `POST /auth/v1/recover` is public and unguarded client-side. Create the recovery surface with the #801 two-tier lockout so a recovery-429 can never burn the shared bucket from this browser, and make the page's copy mechanism-accurate (finding 2 applies to signin too).

**Acceptance:** signin.html has a "Forgot password?" link toggling `#recovery-form` (method=post action=/signin, #527 contract); `resetPasswordForEmail` guarded by the early-return lockout check AND a `recoveryInFlight` double-submit guard (two rapid clicks must not fire two emails against the shared bucket); email-tier lockout disables only `#btn-recovery`; short-tier disables `#btn-submit` and `#btn-recovery`; sign-in 429s set the short tier; keys are `tortoise_signin_rate_limited_until`/`_tier`; countdown + persistence + monotonic tier mirror signup.html; every handler's `finally` re-applies the tier gating (`setLoading(false); applyRateLimitLockout(); if (rateLimitRemainingMs() > 0) showError(rateLimitMessage());` — without it, `setLoading` re-enables buttons mid-lockout).

**Files:**
- Modify: `website/signin.html` (form markup after `#email-form` ~L300; JS: copy split + lockout machinery + recovery handler ~L380-500)
- Test: `tests/test_signup_form_safety.py` (new pins, Task 5)

**Step 1: Add the markup**

`#recovery-form` (hidden, method=post action=/signin) with `#recovery-email`, `#btn-recovery`, `#recovery-note`; "Forgot password?" link `#forgot-link` under `#email-form`; "Back to sign in" link in the recovery form.

**Step 2: Add the lockout machinery + copy split**

⚠️ Declare both constants ABOVE the AUTH_ERROR_MAP literal (map sits ~L381, after the config block — TDZ: a `const` referenced before its declaration kills the whole inline script; `node --check` will NOT catch it). Copy constants (same values as Task 2), split AUTH_ERROR_MAP + substring fallbacks, then the full lockout block (isEmailBucketError, isRateLimitError, rateLimitRemainingMs, setRateLimitLockout, rateLimitMessage, applyRateLimitLockout) with **tier-aware gating**: `update()` iterates both buttons; email tier → only `#btn-recovery` gets disabled+countdown, `#btn-submit` restored; short tier → both. Load-time: active lockout → showError(rateLimitMessage()) + apply.

**Step 3: Recovery + sign-in handlers**

`showRecovery`/`hideRecovery` toggles; `recoverPassword(event)`: `recoveryInFlight` guard + lockout early-return guard, empty-email JS guard (native `required` alone does not run the handler early enough), `resetPasswordForEmail(email, {redirectTo: window.location.origin + WELCOME_URL})`; 429 → setRateLimitLockout + tier copy; success → "If an account exists for that email, a reset link is on its way. Check your inbox (and spam). If the reset page doesn't open, request a new link." (the degrade hint covers a rejected redirectTo falling back to site_url). `signInWithEmail`: rate-limit errors → setRateLimitLockout + tier-aware copy (email-bucket errors are unreachable on sign-in in practice; generic handling still correct). All three handlers (`signInWithEmail`/`signInWithProvider`/`recoverPassword`) end `finally { recoveryInFlight = false; setLoading(false); applyRateLimitLockout(); if (rateLimitRemainingMs() > 0) showError(rateLimitMessage()); }` — **reset the in-flight flag FIRST** (a non-429 recover failure must never leave the form permanently dead), then re-apply tier gating AFTER `setLoading` re-enables buttons, and restore the lockout explanation after `clearError()`.

**Step 4: Syntax + sanity**

Run: `node --check` via pytest gate; `python -m pytest tests/test_signup_form_safety.py -v` (existing pins stay green — new pins arrive in Task 5; no intermediate red state on the static suite).

**Step 5: Commit**

```bash
git add website/signin.html
git commit -m "fix(863): recovery request-link flow with two-tier lockout + mechanism-accurate copy on signin"
```

---

### Task 4: welcome.html — recovery-link reset-password landing

**Intent:** Complete the journey: recovery emails must land somewhere that lets the user set a new password. `origin + /welcome.html` is already the allow-listed redirect target (signup confirmation flow), so no new GoTrue redirect config is needed.

**Acceptance:** welcome.html snapshots `type=recovery` from the hash before client init; a shared `recoveryMode` flag is set by BOTH triggers (hash snapshot AND `PASSWORD_RECOVERY` event — the event handler must also prevent provisioning, not just show the panel); recovery mode SHORT-CIRCUITS `waitForProvisioning()` (a valid recovery link establishes a real session → SIGNED_IN → without this, the provisioning pipeline would run the membership poll, mint a team and reveal an API key mid-password-reset — a real production side effect); the PASSWORD_RECOVERY listener is its own `onAuthStateChange` subscription (NOT inside `waitForSession`, which unsubscribes on settle); `updateUser({password})` success → "Password updated — sign in with your new password" + link to /signin.html; errors render in panel-local `#reset-error` (never the page-level showError/showSuccess); static pin added.

**Files:**
- Modify: `website/welcome.html` (markup + script)
- Test: `tests/test_signup_form_safety.py` (new pins, Task 5)

**Step 1: Read welcome.html's current structure** (session-wait block, content section, provisioning start) and place the reset panel + hook.

**Step 2: Implement**

- Hash snapshot BEFORE createClient: `const recoveryLanding = /[?&#]type=recovery/.test(window.location.hash)` (supabase-js consumes the hash during init — snapshot must run first). A shared `let recoveryMode = recoveryLanding;` flag: the start gate checks it, and the `PASSWORD_RECOVERY` handler ALSO sets `recoveryMode = true` (both triggers must prevent provisioning, and the handler should cancel an in-flight provisioning poll if it somehow started).
- **Gate provisioning:** `if (recoveryMode) { showResetPanel(); } else { waitForProvisioning(); }` — recovery mode must NEVER run the membership poll / team mint / key reveal, and the page-level `showError`/`showSuccess` must not fire in recovery mode.
- Own `onAuthStateChange` subscription for `PASSWORD_RECOVERY` (fires when a recovery link is detected; NOT emitted in some environments — supabase/auth#1948 — hence the hash snapshot fallback).
- Reset panel: `#reset-form` with panel-local `#reset-error` and `#reset-success` elements (distinct from welcome's `#error-state` — never reuse the page-level state machine in recovery mode); submit handler: empty-field guard, password policy ≥8 chars with letter+number+symbol mix (stated as its own decision — matching GoTrue's weak_password copy; signup's laxer 6-char client gate is unchanged), confirm-match check ("Passwords don't match."), `updateUser({password})`; `resetInFlight` double-submit guard mirroring `signupInFlight`.
- **Error mapping (enumerated):** 401 / `invalid_jwt` (server) AND `auth_session_missing` (client-side — supabase-js drops a fake/expired session during init and throws locally rather than surfacing the server 401) → "This reset link has expired or is invalid. Request a new one." + link to /signin.html; `weak_password` → weak-password copy; anything else → generic retry copy. All rendered in `#reset-error`.
- Success: "Password updated — sign in with your new password" + link to /signin.html (in `#reset-success`).
- Recovery-form success copy on signin.html includes a degrade hint: "…If the reset page doesn't open, request a new link." (covers a rejected redirectTo falling back to site_url).

**Step 3: Syntax + sanity**

Run: pytest gate node --check; `python -m pytest tests/test_signup_form_safety.py -v`.

**Step 4: Commit**

```bash
git add website/welcome.html
git commit -m "fix(863): reset-password landing on recovery links (welcome.html)"
```

---

### Task 5: Static pins + API tests update (test_signup_form_safety.py, test_email_signup.py)

**Intent:** Acceptance (c): pins must demand the NEW copy, not the old misattributing literal. (API assertions were updated in Task 1; this task completes the static pins and may absorb any Task 1-4 leftovers.)

**Acceptance:** `tests/test_signup_form_safety.py` passes on the new code: both pages contain BOTH copy literals; signin has recovery+lockout pins; welcome has reset-panel pins; node --check gate green.

**Files:**
- Modify: `tests/test_signup_form_safety.py`
- Test: `tests/test_signup_form_safety.py` itself

**Step 1: Update pins**

- `test_humanize_auth_error_present_with_rate_limit_mapping`: add EMAIL_BUCKET copy literal + NETWORK copy literal to the per-page assertions; add `over_request_rate_limit_ip` to the code literals.
- `test_rate_limit_lockout_guards_present`: extend to SIGNIN (keys, constants, applyRateLimitLockout, early-return guard, resetPasswordForEmail).
- New `test_recovery_flow_present`: SIGNIN contains `id="recovery-form"`, `id="btn-recovery"`, `id="forgot-link"`, `recoveryInFlight`, recovery form is method=post with action=/signin; WELCOME contains `PASSWORD_RECOVERY`, `updateUser`, `id="reset-form"`, `resetInFlight`, `id="reset-error"`, and the expired-link copy literal "This reset link has expired or is invalid. Request a new one."
- New `test_email_bucket_copy_mechanism_split`: both pages contain the email-bucket literal AND the network literal; assert the two strings differ.
- New `test_server_429_tier_parsing_pins` (SIGNUP — the one layer that is neither e2e-testable nor unit-tested): contains `lastServer429Code`, `detail.error_code`, and the fail-safe default `|| "over_email_send_rate_limit"`.
- Fix the stale "SMTP per-IP send bucket" misattribution in `tests/test_email_signup.py`'s module docstring (L1-18) — same correction as Task 1's hosted_api.py docstrings.

**Step 2: Run the static suite**

Run: `python -m pytest tests/test_signup_form_safety.py tests/test_email_signup.py -v`
Expected: PASS.

**Step 3: Commit**

```bash
git add tests/test_signup_form_safety.py tests/test_email_signup.py
git commit -m "test(863): pins updated to mechanism-split 429 copy + recovery-flow contracts"
```

---

### Task 6: e2e updates + new recovery/signin e2e tests

**Intent:** Acceptance (c) for the gated e2e suite; the #801 e2e asserts the old copy literal. All new tests mock the network (zero real emails — mirrors the #801 consumption budget).

**Acceptance:** `test_signup_form_safety_e2e.py` passes under `RUN_LEGAL_E2E=1`: updated email-bucket copy assertion; new recover-429 1h-tier test (incl. double-submit guard); new signin per-IP short-tier test; new recovery-hash reset-panel test (incl. provisioning short-circuit); new expired-link 401 test.

**Files:**
- Modify: `tests/e2e/test_signup_form_safety_e2e.py`
- Test: same file (gated)

**Step 1: Update existing assertions**

`test_429_signup_rate_limit_is_humanized`: expect "temporarily exhausted" (email copy) instead of "Too many attempts from this network". `test_429_short_tier_lockout_60s_then_expiry`: unchanged expectations verified against new copy (minute-tier message retains "Too many attempts from this network", no "about an hour").

**Step 2: Add the new tests**

- `test_recover_429_email_bucket_sets_1h_lockout` (signin page): mock `auth/v1/recover` POST → 429 over_email_send_rate_limit; open recovery form; submit; assert email copy, `#btn-recovery` disabled + countdown, tier=email, `#btn-submit` still enabled, early-return guard (1 request only), reload persistence. Also dispatch the submit twice BEFORE the first 429 response returns (double-click) → exactly 1 recover request (recoveryInFlight guard).
- `test_signin_429_per_ip_short_tier` (signin page): mock `auth/v1/token` POST → 429 over_request_rate_limit_ip; submit sign-in; assert network copy, 60s lockout, tier=short, both buttons disabled; expiry restores.
- `test_recovery_link_hash_shows_reset_panel` (welcome page): goto `/welcome#access_token=<well-formed-fake-jwt>&type=recovery`; assert `#reset-form` visible AND the provisioning short-circuit: `#error-state` AND `#success` stay hidden past the `waitForSession` deadline (>5s), and a route counter proves NO provisioning requests fired (no `team_memberships` / tenant-provision calls) — direct proof the poll was skipped.
- `test_reset_link_expired_shows_request_new_copy` (welcome page): recovery-hash landing; mock the init `GET /auth/v1/user` (200 with a fake user) so the session is valid client-side, then mock the `PUT /auth/v1/user` (updateUser) → 401; submit `#reset-form` → assert `#reset-error` shows "This reset link has expired or is invalid. Request a new one."

**Step 3: Run locally if possible**

Run: `cd website && npx wrangler@4 pages dev . --port 8788 &` then `RUN_LEGAL_E2E=1 BASE_URL=http://127.0.0.1:8788 python -m pytest tests/e2e/test_signup_form_safety_e2e.py -v`
Expected: PASS (or documented skip if wrangler/playwright unavailable locally — CI covers it).

**Step 4: Commit**

```bash
git add tests/e2e/test_signup_form_safety_e2e.py
git commit -m "test(863): e2e assertions updated to mechanism-split copy + recovery/signin lockout tests"
```

---

### Task 7: Full suite + docs + issue comments + labels

**Intent:** Prove no regressions, record the plan, close the scoping/planning label lifecycle, and hand off.

**Acceptance:** Full relevant test suite passes; scope comment + plan comment posted on #863; extra issues filed (email-change surface, server abuse posture); labels: scoping/planning removed, scoped/planned added (implementing stays through delivery).

**Files:**
- Create: this plan doc (done)
- Test: full run

**Step 1: Full test run**

Run: `python -m pytest tests/test_signup_form_safety.py tests/test_email_signup.py tests/test_hosted_api.py -v` (hosted_api regression) + broader `python -m pytest tests/ -v -x -q` if time permits.
Expected: PASS.

**Step 2: File extra issues** (email-change UI gap + server abuse posture, per scope) and post plan comment.

**Step 3: Commit the plan doc**

```bash
git add docs/plans/2026-08-11-863-recovery-rate-limit-plan.md
git commit -m "docs(863): implementation plan — recovery rate-limit hardening + mechanism-accurate 429 copy"
```

## Rejected Alternatives

- **Server-side /v1/recover proxy via Admin API:** password recovery fundamentally requires an email (ownership proof) — cannot bypass the bucket the way #801's server-signup did; duplicates GoTrue's flow. Rejected at scope.
- **Copy-only change:** fails acceptance (a); leaves the public recover endpoint with zero client guard. Rejected at scope.
- **Enabling [auth.captcha] in supabase/config.toml:** only applies to selfhost; hosted is dashboard-managed; would break local e2e. Tracked as posture in filed issue instead.
- **Shared lockout keys across signup/signin:** a signup 1h lockout would disable sign-in on signin.html (confusing, blocks a non-email surface). Page-scoped keys chosen; the server is the real gate for cross-surface abuse.
- **Reset landing on a new /reset page:** would require a new GoTrue redirect-allowlist entry (dashboard action). welcome.html is already the allow-listed target → zero config.
- **Consumer-neutral email-bucket copy:** issue's exact copy wins (acceptance (b) contract, single constant).

## Runtime Prerequisites

- Recovery links redirect to `origin + /welcome.html` — must remain in the GoTrue redirect allowlist (dashboard for hosted; already the case for the signup confirmation flow; local dev `site_url`/`additional_redirect_urls` in `supabase/config.toml`).
- Local-dev recovery emails (inbucket) link to `site_url` (127.0.0.1:3000) — manual local testing of the full link-click journey requires site_url adjustment; e2e mocks the network so it is not a test blocker.
- `RATE_LIMIT_DISABLED=1` test env: register-bucket 429 enrichment is skipped when the limiter is off (unchanged behavior).
