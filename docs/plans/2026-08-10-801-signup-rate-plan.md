---
title: "#801 Signup Rate-Limit (429) — SMTP-Era Verification + Client Lockout"
type: engineering
domain: platform
doc_status: draft
subjects.team: epistemic-team
created: 2026-08-10
aboutSubjects: tortoise
aboutObjects: supabase-auth, signup-page, welcome-page, ci
---

<!-- research-path: #801 solution-diverge session (2026-08-10) + gh issue #832/#839 + docs/epics/2026-08-07-tortoise-user-journeys/05-plan.md (E2E-1/E2E-8) -->

# #801 Signup Rate-Limit (429) — SMTP-Era Verification + Client Lockout Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Close #801 by (1) proving the SMTP-era production signup no longer 429s via a per-push live smoke, (2) repairing the by-design-failing live acceptance test for the confirmations-ON prod state, (3) adding a graceful 1h client lockout on the signup page so a 429 can never burn the shared project-wide email bucket again, and (4) rewording the contradictory acceptance criteria on close.

**Team:** epistemic-team
**Role:** — (no AGENT_SESSION_ROLE)

**Architecture:**
- **Live no-429 monitor (Approach C):** the live E2E becomes a *smoke* — real signup against prod must reach `#confirmation-required` (confirmations are ON post-#832) with `#error` hidden and HTTP 200 on `auth/v1/signup`; teardown deletes the created auth user via the GoTrue Admin API (FK cascade clears the placeholder membership row). No sign-in, no provisioning, no key reveal in the live test — those stay covered by the mocked welcome suite (green in CI today) and #839's deploy verification.
- **Client 429 lockout:** on a rate-limit error, `signup.html` persists a 1h lockout timestamp in `sessionStorage`, disables `#btn-submit`/`#btn-resend`, shows a live `Try again in MM:SS` countdown, and early-returns from `signUpWithEmail` before any request. Static pins + the extended mocked 429 e2e prove it.
- **CI wiring:** the signup-safety e2e suite (currently only runnable locally) joins the `legal-e2e` CI job and the post-deploy `verify-legal` job; the `welcome-e2e` job gains a warn-only check that the live smoke is not silently skipping (it has been — see Pattern Research).

### Pattern Research

Skipped — plan touches zero third-party dependencies (inline JS in checked-in HTML + stdlib-only Python). One external API surface was verified via search (GoTrue Admin API, supabase.com self-hosting auth docs + netlify/gotrue):

- `GET /auth/v1/admin/users?filter=<email>` — `filter` is an email ILIKE substring query; returns `{"users": [...]}`. Requires `Authorization: Bearer <service_role_key>` (+ `apikey` header).
- `DELETE /auth/v1/admin/users/{id}` — deletes the auth user; `team_memberships.user_id` FK is `ON DELETE CASCADE` (migration 0001, preserved through 0003/0009 renames) → the placeholder row created by `handle_new_user()` is cleaned too. Teams/api_keys/FalkorDB graphs are NOT cascaded (this is why Approach A was rejected).

**Repo-verified facts the plan depends on:**
- #832 closed 2026-08-10: Resend→Supabase SMTP wired, confirmation emails verified, confirmations ON in prod. GoTrue email-send limiter is **project-wide** (built-in 2/hr; custom SMTP 30/hr), not per-IP as the issue claimed.
- ⚠️ `SUPABASE_URL` / `SUPABASE_SERVICE_KEY` are **NOT configured** as repo secrets (`gh secret list` — only RESEND_API_KEY etc.). The `welcome-e2e` job references them → unset → the live test has been **silently skipping** (CI: "25 passed, 3 skipped" — the skips are the live test + the two module-level opt-in suites). Making the live run map to acceptance requires adding both secrets (owner action, see Runtime Prerequisites).
- `website/signup.html` already renders `#confirmation-required` on signup success (shipped pre-#832) → the live smoke is valid against the *currently deployed* page, pre-deploy and post-deploy.
- `tests/e2e/test_signup_form_safety_e2e.py` runs **nowhere in CI** (legal-e2e job runs only `test_legal_pages.py`; welcome-e2e runs without `RUN_LEGAL_E2E`). Its extended 429-lockout test needs a home → Task 4.
- `supabase/config.toml` local: `enable_confirmations = false`, `email_sent = 2`/hr. No repo test depends on local confirmations being OFF (no test touches `:54321`).
- Issue #801 has no `issue-scoping:` signature and no `complexity:` label → **tier = Complex (safest default)**; reworded acceptance replaces the contradictory originals.

### Integration Surface Map

| Surface | Change | Test layer | Bug pattern flags |
|---|---|---|---|
| `website/signup.html` inline JS | 429 lockout (storage, disable, countdown, early-return) | Static pin (main suite) + mocked 429 e2e (legal-e2e job) + `node --check` gate | Regression of #527 pins: no `let supabase`, no `showError(error.message)`, literal codes, watchdog intact |
| `website/signin.html` | none (no email-burning path; pins already demand humanized copy) | Static pins (unchanged) | — |
| `tests/e2e/test_welcome_page.py` live test | Rewrite to confirmations-ON smoke + Admin DELETE teardown | CI welcome-e2e (needs secrets) | Skip-without-creds must stay (local runs) |
| `tests/e2e/supabase_admin.py` (new) | Delete-only GoTrue Admin helper (urllib, zero-dep) | Executed by live smoke; collected locally without creds | Network errors must be best-effort (never fail the smoke) |
| `tests/e2e/test_signup_form_safety_e2e.py` | Extend 429 test (disabled btn, countdown, storage, early-return, reload persistence) | CI legal-e2e (wrangler dev) + post-deploy verify-legal (prod, ALLOW_PROD=1) | Route counter must prove zero retry requests |
| `tests/test_signup_form_safety.py` | New static pin for lockout literals | Main pytest suite (python-ci job) | Pins must stay literal (no regex overreach) |
| `supabase/config.toml` | `enable_confirmations = true` (local parity) | Local `supabase start` + inbucket manual check | Local email bucket 2/hr — same 429 UX exercisable locally |
| CI `ci.yml` legal-e2e job | Add signup-safety file to pytest invocation | CI | Wrangler dev server already started by job |
| CI `deploy-pages.yml` verify-legal job | Add signup-safety file | Post-deploy prod run | `ALLOW_PROD=1` already set |
| CI `ci.yml` welcome-e2e job | `-rs` + warn-only skip-detection step | CI | Warn, never block (green-with-annotation repo pattern) |
| GitHub secrets | Add `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` | Owner action | Without them the smoke skips — warning surfaces it |
| Issue #801 + epic 05-plan | Reword acceptance on close; annotate E2E-1/E2E-8 | Doc | — |

### Journey Test Map

### Journey: Real visitor signs up on premiselabs.co/signup (email/password)
1. **Step:** Fill email+password → Create account → **Acceptance:** check-your-inbox state, URL clean, no error → **Test:** `test_live_signup_no_429_confirmation_required` (live smoke, CI welcome-e2e)
2. **Step:** (Rate-limit hit — shared bucket exhausted) submit again → **Acceptance:** friendly copy + button disabled with countdown, no further requests → **Test:** `test_429_signup_rate_limit_is_humanized` (extended)
3. **Step:** Click confirmation link → **Acceptance:** welcome page provisions + reveals `tt_` key once → **Test:** mocked `test_welcome_provisions_via_edge_function_when_no_membership` + `test_reveal_shows_key_once_then_returning_state` (existing, green)

### Failure Modes
- Prod email bucket trips during a CI run (parallel PRs / real traffic) → **Expected:** smoke fails with the 429 body in the assertion message — this is the monitored signal, not flake → **Test:** live smoke's `signup_status == 200` assert
- Secrets missing → **Expected:** smoke skips + `::warning::` in welcome-e2e log → **Test:** skip-detection step (Task 3; env-var check, NOT pytest output parsing — `--collect-only` never prints skip status, verified)
- Admin DELETE fails (network/4xx) → **Expected:** smoke still passes, cleanup logged as warning. An orphaned unconfirmed auth user + placeholder `team_memberships` row would persist until manual cleanup — **the `tenant_cleaned_up` auto-expiry is only an epic spec, NOT implemented** (verified: no such function exists in supabase/functions/; GoTrue auto-expires only anonymous users). Harmless but permanent; a weekly Admin-API sweep of `e2e-live-*@premise-labs.dev` users is the optional mitigation → **Test:** best-effort wrapper
- Pre-confirmation team minting (junk premise) → **Expected:** NO teams/keys/graphs are minted by the smoke. Resolved: even though `supabase/config.toml` carries a stale "Remote state: hook_after_user_created_enabled=true" comment, #832 (2026-08-10) states the hook is DISABLED and `AUTH_HOOK_SECRET` was removed from `tenant-provision` — Path 2 of the function fails CLOSED (401) without the secret (verified in `index.ts`), so even a live hook could not mint. The only minting path (Path 1, user JWT from the welcome page) is never exercised by the smoke. Post-merge verification: confirm no new `e2e-live-*` teams appear after the first CI run
- Per-push bucket consumption → **Expected:** each welcome-e2e run consumes exactly 1 email-send from the shared 30/hr prod bucket (real traffic + parallel PRs could trip it — red CI with the 429 body is the diagnosable signal). **Escalation threshold: if the smoke fails with a genuine 429 (not a code bug) more than 3 times in a rolling 7-day window, move it to a merge-to-main or `schedule` trigger** (coalescing). v1 keeps it per-push (the issue's "consecutive signups" evidence) → **Test:** live smoke
- Password-reset / email-change surfaces share the same project-wide email bucket but are NOT protected by the client lockout (signin.html is out of scope for #801 — no password-reset UI exists on the signin page today; the attack vector exists via direct `/auth/v1/recover` calls). **Tracked as a follow-up issue** (recovery-surface hardening + mechanism-accurate error copy) → **Test:** none in this plan
- `setLoading` re-enables the button after the error branch → **Expected:** `applyRateLimitLockout()` runs in `finally` after `setLoading(false)` (ordering pinned in Task 1) → **Test:** disabled-button assertion in extended 429 e2e

**Tech Stack:** Vanilla inline JS (signup.html), pytest + pytest-playwright, stdlib urllib (helper), Cloudflare Pages (deploy), GoTrue Admin API (cleanup).

---

### UX Design Decisions

| # | Decision Type | User Choice | Rationale |
|---|---|---|---|
| 1 | Copy | Keep existing humanized message "Too many attempts from this network. Please wait about an hour and try again." (from #527) | Static pins require the literal; scope decision from solution-diverge |
| 2 | Affordance | Disable `#btn-submit` + `#btn-resend` with live `Try again in MM:SS` countdown (1h, sessionStorage-persisted) | Matches the 1h bucket; prevents shared-bucket burns |
| 3 | Scope | OAuth buttons stay enabled during lockout | OAuth uses a different rate bucket; email lockout must not block it |
| 4 | Scope | Lockout is signup-page only (`signin.html` unchanged) | Password sign-in sends no email; `over_email_send_rate_limit` cannot fire there |
| 5 | Limitation | `sessionStorage` (per-tab) per shared scope; multi-tab bypass documented | Bucket-protection threat model is the normal user mashing submit, not adversarial multi-tab; localStorage upgrade is a one-line change if ever needed |

---

## Tasks

### Task 1: 429 lockout — signup.html + extended e2e + static pin (TDD)

**Intent:** A 429 from the project-wide email bucket must lock the browser out of email signup for 1h — no instant re-enable, no repeated submits, no further requests.
**Acceptance:** `signup.html` contains the pinned literals (`RATE_LIMIT_LOCKOUT_MS`, `applyRateLimitLockout`, `tortoise_signup_rate_limited_until`, `sessionStorage`, `rateLimitRemainingMs() > 0`); the extended `test_429_signup_rate_limit_is_humanized` passes against wrangler dev (disabled button, countdown, storage timestamp in ~1h window, zero retry requests after guard, reload persistence); the new static pin passes; the EXISTING 60s resend cooldown is preserved (no regression); all pre-existing pins in `tests/test_signup_form_safety.py` stay green; `node --check` parses the inline script.

**Files:**
- Modify: `website/signup.html` (inline script: constants + `isRateLimitError`/`rateLimitRemainingMs`/`setRateLimitLockout`/`applyRateLimitLockout`; guard in `signUpWithEmail` + `resendConfirmation`; `applyRateLimitLockout()` in the `finally` of both handlers)
- Modify: `tests/e2e/test_signup_form_safety_e2e.py` (`test_429_signup_rate_limit_is_humanized` — extend)
- Modify: `tests/test_signup_form_safety.py` (new pin `test_rate_limit_lockout_guards_present`)

**Step 1: Write the failing pin + extend the e2e test (red)**

Add to `tests/test_signup_form_safety.py`:

```python
def test_rate_limit_lockout_guards_present() -> None:
    """#801: after a 429 (project-wide email bucket) the client must lock
    out email signup for ~1h — sessionStorage timestamp, disabled submit,
    countdown label, early-return guard. Literal pins (no regex)."""
    assert "tortoise_signup_rate_limited_until" in SIGNUP
    assert "RATE_LIMIT_LOCKOUT_MS" in SIGNUP
    assert "SHORT_RATE_LIMIT_LOCKOUT_MS" in SIGNUP  # two-tier: per-IP limits ≠ email bucket
    assert "applyRateLimitLockout" in SIGNUP
    assert "sessionStorage" in SIGNUP
    # the guard runs before any request: top-of-handler early return
    assert "rateLimitRemainingMs() > 0" in SIGNUP
```

Extend `test_429_signup_rate_limit_is_humanized` in `tests/e2e/test_signup_form_safety_e2e.py` (add `import time`; **define `calls = {"n": 0}` at TEST SCOPE next to `console_errors`, and increment it inside the existing route handler** before `route.fulfill`; **REPLACE the test's final `assert console_errors == []` with the block below** — the block's own final assert covers console errors after the reload, which is the meaningful check):

```python
    # ── #801 lockout: disabled button + countdown + storage + early return ──
    btn = page.locator("#btn-submit")
    expect(btn).to_be_disabled(timeout=5_000)
    assert re.search(r"Try again in \d{2}:\d{2}", btn.inner_text()), btn.inner_text()
    # resend is force-disabled during the lockout (shared email bucket, #801)
    expect(page.locator("#btn-resend")).to_be_disabled(timeout=5_000)
    until = page.evaluate(
        "parseInt(sessionStorage.getItem('tortoise_signup_rate_limited_until') || '0', 10)")
    now_ms = time.time() * 1000
    assert now_ms < until <= now_ms + 3_600_000 + 5_000, f"lockout until={until}"
    # two-tier check: the mocked 429 is over_email_send_rate_limit (email bucket)
    # → lockout must be the 1h tier (SHORT_RATE_LIMIT_LOCKOUT_MS would fail this)
    # early-return guard: re-dispatching submit must NOT fire a second request
    page.evaluate("document.getElementById('email-form')"
                  ".dispatchEvent(new Event('submit', {cancelable: true}))")
    page.wait_for_timeout(500)
    assert calls["n"] == 1, f"lockout failed to guard: {calls['n']} signup requests"
    # persistence across reload
    page.reload(wait_until="domcontentloaded")
    expect(page.locator("#btn-submit")).to_be_disabled(timeout=5_000)
    assert re.search(r"Try again in \d{2}:\d{2}", btn.inner_text()), "countdown lost on reload"
    assert console_errors == [], f"page JS errors: {console_errors}"
```

**Step 2: Run both to verify they fail**

```bash
python -m pytest tests/test_signup_form_safety.py::test_rate_limit_lockout_guards_present -v
# expected: FAIL (pins absent from signup.html)

cd website && npx wrangler@4 pages dev . --port 8788 --ip 127.0.0.1 --compatibility-date=2024-09-23 &
RUN_LEGAL_E2E=1 BASE_URL=http://127.0.0.1:8788 \
  python -m pytest tests/e2e/test_signup_form_safety_e2e.py::test_429_signup_rate_limit_is_humanized -v
# expected: FAIL (button re-enables after 429 — the current bug)
```

**Step 3: Implement the lockout in `website/signup.html`**

Insert after the `humanizeAuthError` block (uses the same code list — single source of truth for rate-limit codes):

```js
    // ── Rate-limit lockout (#801) ──────────────────────────────────────────
    // The GoTrue email-send bucket is PROJECT-WIDE (custom SMTP: 30 sends/hr
    // for ALL users) — a client that re-submits after a 429 burns the shared
    // bucket for every other visitor. On an EMAIL-bucket rate-limit error,
    // lock THIS browser out of email signup for 1h (sessionStorage-persisted),
    // disable submit/resend, and show a live countdown. Per-IP auth-attempt
    // limits (over_request_rate_limit*) self-reset in seconds/minutes — they
    // get a short 60s lockout only (two-tier; do NOT flatten them into the
    // 1h email-bucket lockout).
    const RATE_LIMIT_CODES = ["over_email_send_rate_limit", "over_request_rate_limit", "over_request_rate_limit_ip", "rate_limit"];
    const RATE_LIMIT_LOCKOUT_MS = 60 * 60 * 1000;   // email bucket — 1h (matches the hourly window)
    const SHORT_RATE_LIMIT_LOCKOUT_MS = 60 * 1000;  // per-IP auth-attempt limits — 60s
    const RATE_LIMIT_KEY = "tortoise_signup_rate_limited_until";
    let rateLimitTimer = null;
    let resendCooldownUntil = 0;   // #527 60s resend cooldown (shared bucket, #801)

    function isEmailBucketError(error) {
      if (!error) return false;
      const code = error.code || "";
      const msg = (error.message || error.msg || error.error_description || "").toLowerCase();
      return code === "over_email_send_rate_limit"
        || msg.includes("email rate limit")
        || (code === "rate_limit" && msg.includes("email"));
    }

    function isRateLimitError(error) {
      if (!error) return false;
      const code = error.code || "";
      const msg = (error.message || error.msg || error.error_description || "").toLowerCase();
      if (RATE_LIMIT_CODES.includes(code)) return true;
      for (const c of RATE_LIMIT_CODES) {
        if (msg.includes(c.replace(/_/g, " "))) return true;
      }
      return msg.includes("email rate limit") || msg.includes("rate limit");
    }

    function rateLimitRemainingMs() {
      try {
        const until = parseInt(sessionStorage.getItem(RATE_LIMIT_KEY) || "0", 10);
        return Math.max(0, until - Date.now());
      } catch (e) { return 0; }
    }

    function setRateLimitLockout(error) {
      // Two-tier: email-bucket exhaustion → 1h; per-IP auth-attempt limits → 60s.
      const ms = isEmailBucketError(error) ? RATE_LIMIT_LOCKOUT_MS : SHORT_RATE_LIMIT_LOCKOUT_MS;
      try { sessionStorage.setItem(RATE_LIMIT_KEY, String(Date.now() + ms)); } catch (e) {}
      applyRateLimitLockout();
    }

    function applyRateLimitLockout() {
      if (rateLimitTimer) { clearInterval(rateLimitTimer); rateLimitTimer = null; }
      const btn = document.getElementById("btn-submit");
      const resend = document.getElementById("btn-resend");
      const update = function () {
        const ms = rateLimitRemainingMs();
        if (ms <= 0) {
          if (btn && btn.dataset.originalLabel) {
            btn.textContent = btn.dataset.originalLabel;
            delete btn.dataset.originalLabel;
          }
          if (btn) btn.disabled = false;
          // #btn-resend: re-enable ONLY when no 60s resend cooldown is pending
          // (resendCooldownUntil set by resendConfirmation). Never re-enable it
          // blindly — a blind re-enable would defeat the #527 cooldown and
          // re-open the shared-bucket burn (#801); leaving it disabled forever
          // would strand users after lockout expiry (disabled buttons swallow
          // clicks, so the cooldown timer alone could never recover it).
          if (resend) resend.disabled = (Date.now() < resendCooldownUntil);
          if (rateLimitTimer) { clearInterval(rateLimitTimer); rateLimitTimer = null; }
          return;
        }
        const totalSec = Math.ceil(ms / 1000);
        const label = "Try again in "
          + String(Math.floor(totalSec / 60)).padStart(2, "0") + ":"
          + String(totalSec % 60).padStart(2, "0");
        if (btn) {
          if (!btn.dataset.originalLabel) btn.dataset.originalLabel = btn.textContent;
          btn.textContent = label;
          btn.disabled = true;
        }
        if (resend) resend.disabled = true;
      };
      update();
      rateLimitTimer = setInterval(update, 1000);
    }
    // On load: honor a persisted lockout (and explain why the button is off).
    if (rateLimitRemainingMs() > 0) {
      showError("Too many attempts from this network. Please wait about an hour and try again.");
      applyRateLimitLockout();
    }
```

`signUpWithEmail`: add the early-return guard as the FIRST statement after `event.preventDefault(); if (!supabaseClient) ...`:

```js
      if (rateLimitRemainingMs() > 0) {
        showError("Too many attempts from this network. Please wait about an hour and try again.");
        applyRateLimitLockout();
        return;
      }
```

In the `if (error)` branch (before `showError`):

```js
        if (error) {
          if (isRateLimitError(error)) setRateLimitLockout(error);
          showError(humanizeAuthError(error));
          return;
        }
```

Change the handler's `finally` — **ordering is load-bearing**: `setLoading(false)` restores and re-enables the button, so the lockout must re-apply AFTER it:

```js
      } finally {
        setLoading(false);
        applyRateLimitLockout();
      }
```

`resendConfirmation`: same guard at the top (re-apply lockout and return — do NOT showError, the inbox state is the visible surface), and in `catch`:

```js
    async function resendConfirmation() {
      const email = document.getElementById("confirm-email").textContent;
      const btn = document.getElementById("btn-resend");
      if (!email || !btn) return;
      if (rateLimitRemainingMs() > 0) { applyRateLimitLockout(); return; }
      btn.disabled = true;
      try {
        await supabaseClient.auth.resend({
          type: 'signup',
          email: email,
          options: { emailRedirectTo: window.location.origin + WELCOME_URL },
        });
        document.getElementById("resend-note").textContent = "Resent — check your inbox (limit applies).";
      } catch (e) {
        if (isRateLimitError(e)) {
          setRateLimitLockout(e);
          document.getElementById("resend-note").textContent = "Email limit reached — try again in about an hour.";
        } else {
          document.getElementById("resend-note").textContent = "Could not resend — try again shortly.";
        }
      } finally {
        // ⛔ SET THE COOLDOWN FIRST so applyRateLimitLockout()'s update()
        // sees it when deciding whether to re-enable #btn-resend — setting it
        // after would let the first click's cooldown go unenforced (#527/#801).
        resendCooldownUntil = Date.now() + 60000;
        applyRateLimitLockout();
        // Guarded re-enable: never re-enable during an active lockout
        // (the interval would re-disable on the next tick, but avoid the
        // flicker); never re-enable before the 60s cooldown is over.
        setTimeout(() => {
          if (rateLimitRemainingMs() <= 0 && Date.now() >= resendCooldownUntil) {
            btn.disabled = false;
          }
        }, 60000); // rate-limit resend
      }
    }
```

The primary block above is the single source of truth for `update()` — its `ms <= 0` branch re-enables `#btn-submit` unconditionally but re-enables `#btn-resend` only when no resend cooldown is pending (see `resendCooldownUntil`). Do not simplify this back to a blind re-enable (defeats the #527 cooldown) or to a never-re-enable (strands users after lockout expiry).

Also add `applyRateLimitLockout()` to `signInWithProvider`'s `finally` — `setLoading` re-enables ALL buttons including `#btn-submit`; the lockout must re-apply so the countdown label does not flicker when a user clicks an OAuth button during a lockout (OAuth buttons stay enabled by design — different rate bucket):

```js
      } finally {
        setLoading(false);
        applyRateLimitLockout();   // re-apply the lockout state (#801)
      }
```

**Step 4: Run everything to verify green**

```bash
python -m pytest tests/test_signup_form_safety.py -v                      # pins incl. new one
RUN_LEGAL_E2E=1 BASE_URL=http://127.0.0.1:8788 \
  python -m pytest tests/e2e/test_signup_form_safety_e2e.py -v            # 5 tests incl. extended 429
RUN_LEGAL_E2E=1 BASE_URL=http://127.0.0.1:8788 TORTISE_HOST_CHECK=1 \
  python -m pytest tests/e2e/test_legal_pages.py -q                      # regression
python -m pytest tests/e2e/test_welcome_page.py -q                       # mocked suite regression (live skips)
# expected: all green; node --check gate (part of pins) passes
```

**Step 5: Commit**

```bash
git add website/signup.html tests/e2e/test_signup_form_safety_e2e.py tests/test_signup_form_safety.py
git commit -m "feat(801): 1h client rate-limit lockout on signup (shared email bucket)"
```

### Task 2: Live smoke rewrite — `supabase_admin.py` + `test_welcome_page.py`

**Intent:** The live E2E must assert the ACTUAL prod state (confirmations ON → check-your-inbox), proving the no-429 property per run, and must leave no junk (Admin-API user deletion).
**Acceptance:** `tests/e2e/supabase_admin.py` imports cleanly and is best-effort; the rewritten live test (renamed `test_live_signup_no_429_confirmation_required`) is gated on `LIVE_SIGNUP`, asserts HTTP 200 on `auth/v1/signup` + `#confirmation-required` visible + `#confirm-email` == typed email + `#error` hidden + URL clean, and deletes the created user in `finally`; `python -m pytest tests/e2e/test_welcome_page.py -q` passes locally (13 passed, 1 skipped). Junk premise resolved: no pre-confirmation minting possible (hook disabled per #832 + `AUTH_HOOK_SECRET` removed → function Path 2 fails closed; Path 1 never exercised by the smoke).

**Files:**
- Create: `tests/e2e/supabase_admin.py`
- Modify: `tests/e2e/test_welcome_page.py` (module docstring, `LIVE_SIGNUP` block, rename + rewrite the live test)

**Step 1: Write the delete-only helper** (`tests/e2e/supabase_admin.py` — stdlib urllib only; httpx IS a base dependency, but a zero-dep helper is simpler and keeps the e2e extras unbloated):

```python
"""GoTrue Admin API helpers for live e2e cleanup (#801).

Stdlib-only (urllib). (httpx IS a base dependency, but a zero-dep helper is
simpler and keeps the e2e extras unbloated.) All helpers are BEST-EFFORT:
cleanup failures are logged, never raised, so a hygiene failure can never
fail the test that created the resource.

Reference: GET /auth/v1/admin/users?filter=<email> (ILIKE email search),
DELETE /auth/v1/admin/users/{id} — Authorization: Bearer <service_role_key>.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request


def delete_user_by_email(base_url: str, service_key: str, email: str) -> bool:
    """Delete the auth user with the exact given email (no-op when absent).

    Cascades to team_memberships (FK ON DELETE CASCADE, migration 0001).
    Returns True if a user was deleted."""
    headers = {
        "Authorization": f"Bearer {service_key}",
        "apikey": service_key,
        "Accept": "application/json",
    }
    try:
        req = urllib.request.Request(
            f"{base_url}/auth/v1/admin/users?filter={email}", headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"[supabase_admin] list users failed: HTTP {e.code} {e.reason}")
        return False
    except Exception as e:  # network/parse — best-effort
        print(f"[supabase_admin] list users failed: {e!r}")
        return False

    deleted = False
    for user in data.get("users", []):
        if user.get("email") != email:
            continue
        try:
            req = urllib.request.Request(
                f"{base_url}/auth/v1/admin/users/{user['id']}",
                method="DELETE", headers=headers)
            with urllib.request.urlopen(req, timeout=15):
                deleted = True
            print(f"[supabase_admin] deleted user {user['id']} ({email})")
        except urllib.error.HTTPError as e:
            print(f"[supabase_admin] delete {user['id']} failed: HTTP {e.code} {e.reason}")
        except Exception as e:
            print(f"[supabase_admin] delete {user['id']} failed: {e!r}")
    if not deleted:
        print(f"[supabase_admin] no user found for {email} (already cleaned up?)")
    return deleted
```

**Step 2: Rewrite the live test in `tests/e2e/test_welcome_page.py`**

Update the module docstring line about the live E2E ("per-push no-429 smoke; user deleted in teardown"). Replace `test_live_signup_redirects_to_welcome_with_key` with:

```python
@LIVE_SIGNUP
def test_live_signup_no_429_confirmation_required(page: Page) -> None:
    """#801 live no-429 monitor (Approach C, per-push smoke).

    Real signup against PROD (confirmations ON since #832). The signup POST
    must return 200 and the page must reach the check-your-inbox state —
    NOT the 429 over_email_send_rate_limit error. If the project-wide email
    bucket is exhausted (real traffic / parallel CI), this test FAILS with
    the server body in the message: that is the monitored signal.

    No sign-in / provisioning / key reveal here — the mocked welcome suite
    owns those (green in CI); a live key-reveal would mint an un-deletable
    prod team + api_keys row + FalkorDB graph (no cleanup endpoint in-repo).

    Teardown deletes the created auth user via the Admin API (best-effort;
    the FK cascade removes the placeholder team_memberships row)."""
    signup = {"status": None, "body": ""}

    def _on_response(resp):
        if "auth/v1/signup" in resp.url and resp.request.method == "POST":
            signup["status"] = resp.status
            signup["body"] = resp.text()[:400]

    page.on("response", _on_response)
    email = f"e2e-live-{uuid.uuid4().hex[:8]}@premise-labs.dev"
    password = f"E2eLivePass-{uuid.uuid4().hex[:8]}!"
    try:
        page.goto("https://premiselabs.co/signup", wait_until="domcontentloaded", timeout=30_000)
        page.locator("#email").fill(email)
        page.locator("#password").fill(password)
        page.locator("#btn-submit").click()
        # Direct no-429 proof: the server must accept the signup.
        expect.poll(lambda: signup["status"] is not None, timeout=30_000).to_be_truthy()
        assert signup["status"] == 200, (
            f"live signup returned {signup['status']} — rate-limited or error: {signup['body']!r}")
        # User-visible truth: check-your-inbox with the typed email.
        expect(page.locator("#confirmation-required")).to_be_visible(timeout=30_000)
        expect(page.locator("#confirm-email")).to_have_text(email)
        # No 429 copy / no other error surfaced.
        expect(page.locator("#error")).to_be_hidden(timeout=5_000)
        assert "email=" not in page.url and "password=" not in page.url, \
            f"credentials echoed into URL: {page.url}"
    finally:
        from supabase_admin import delete_user_by_email
        delete_user_by_email(os.environ["SUPABASE_URL"],
                             os.environ["SUPABASE_SERVICE_KEY"], email)
```

**Step 3: Verify locally (without creds)**

```bash
python -m pytest tests/e2e/test_welcome_page.py -q
# expected: 13 passed, 1 skipped (live smoke — reason lists SUPABASE_URL/KEY)
python -c "import sys; sys.path.insert(0, 'tests/e2e'); import supabase_admin; print('imports OK')"
```

Cannot be run green locally — no prod creds (see Runtime Prerequisites). The first real execution is the CI run post-merge. **Note:** `import re` becomes unused after the rewrite (the old live test was the only consumer in the module) — drop it from the imports.

**Step 4: Commit**

```bash
git add tests/e2e/supabase_admin.py tests/e2e/test_welcome_page.py
git commit -m "test(801): live signup smoke — confirmations-ON assertion + Admin-API cleanup"
```

### Task 3: CI wiring — run the signup-safety suite + skip-detection

**Intent:** The extended 429-lockout e2e must run in CI (currently nowhere), and the live smoke must never silently skip again.
**Acceptance:** legal-e2e job runs `test_legal_pages.py` + `test_signup_form_safety_e2e.py` together; deploy-pages verify-legal job likewise; welcome-e2e prints a `::warning::` when `SUPABASE_URL`/`SUPABASE_SERVICE_KEY` are unset (env-var check — pytest `--collect-only` output never contains "skipped", so output parsing is NOT used; verified empirically).

**Files:**
- Modify: `.github/workflows/ci.yml` (legal-e2e job pytest line; welcome-e2e `-rs` + warn step)
- Modify: `.github/workflows/deploy-pages.yml` (verify-legal job pytest line)

**Step 1: legal-e2e job — add the signup-safety file**

```yaml
      - name: Run legal pages E2E suite (#657)
        run: >
          RUN_LEGAL_E2E=1 BASE_URL=http://127.0.0.1:8788
          TORTISE_HOST=http://127.0.0.1:8788 TORTISE_HOST_CHECK=1
          LEGAL_E2E_SKIP_EXTERNAL_CRAWL=1
          python -m pytest tests/e2e/test_legal_pages.py tests/e2e/test_signup_form_safety_e2e.py
          -q -p no:cacheprovider
```

(Comment update: the job now also runs the #527/#801 signup-safety suite — same module-level `RUN_LEGAL_E2E` gate, same wrangler dev server.)

**Step 2: welcome-e2e job — surface the skip**

```yaml
      - name: Run welcome page E2E + waitlist static tests (#373)
        run: python -m pytest tests/e2e/ tests/test_waitlist_form.py -q -rs -p no:cacheprovider
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_KEY: ${{ secrets.SUPABASE_SERVICE_KEY }}
      - name: Warn if live signup smoke is skipping (no-429 monitor off)
        if: always()
        run: |
          # ⛔ env-var check, NOT pytest output parsing: `--collect-only` never
          # prints "skipped" (verified empirically) — grep on it can never match.
          if [ -z "$SUPABASE_URL" ] || [ -z "$SUPABASE_SERVICE_KEY" ]; then
            echo "::warning::live signup smoke is SKIPPED — SUPABASE_URL/SUPABASE_SERVICE_KEY secrets missing; the #801 no-429 monitor is OFF"
          else
            echo "secrets present — live signup smoke will run"
          fi
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_KEY: ${{ secrets.SUPABASE_SERVICE_KEY }}
```

**Step 3: deploy-pages verify-legal job — post-deploy prod run of the backoff test**

```yaml
      - name: Run legal pages E2E suite
        run: python -m pytest tests/e2e/test_legal_pages.py tests/e2e/test_signup_form_safety_e2e.py -v
```

(`ALLOW_PROD=1 BASE_URL=https://premiselabs.co` already set; the 429-mock test intercepts the request locally in the browser — no real signup against prod, but it verifies the DEPLOYED backoff code.)

**Step 4: Commit**

```bash
git add .github/workflows/ci.yml .github/workflows/deploy-pages.yml
git commit -m "ci(801): run signup-safety e2e in legal-e2e + verify-legal; warn when live smoke skips"
```

### Task 4: Local prod parity — `config.toml` confirmations ON

**Intent:** Local `supabase start` should exercise the confirmation branch (prod parity) so the 429 UX and check-your-inbox state are testable locally with inbucket.
**Acceptance:** `supabase/config.toml` has `enable_confirmations = true` under `[auth.email]`; the stale "Remote state: hook_after_user_created_enabled=true" comment is corrected to reflect #832 (hook disabled; AUTH_HOOK_SECRET removed — function fails closed); no repo test depends on local confirmations being OFF (verified: zero `:54321` consumers in tests/).

**Files:**
- Modify: `supabase/config.toml` (`enable_confirmations = false` → `true` under `[auth.email]`; correct the stale remote-hook comment ~L275; **leave `[auth.sms]`'s `enable_confirmations = false` untouched** — it is a different setting)

**Step 1: Flip the toggle + correct the stale hook comment**

```toml
[auth.email]
...
# If enabled, users need to confirm their email address before signing in.
enable_confirmations = true
```

Also update the stale remote-state comment (~L275) to match #832 (2026-08-10):

```toml
# Remote state (2026-08-10, #832): hook_after_user_created is DISABLED on this
# plan (its Standard-Webhooks signature could never be verified) and
# AUTH_HOOK_SECRET was removed from tenant-provision — the function's hook
# path fails CLOSED (401). Client-side provisioning (welcome page JWT path)
# is the production path (#802/#839).
```

**Step 2: Verify locally (manual, needs Docker)**

```bash
supabase start        # inbucket receives confirmation emails; local bucket = 2/hr
# browser → http://127.0.0.1:54321 → signup → check-your-inbox → open inbucket
# → click confirmation link → welcome
```

**Step 3: Commit**

```bash
git add supabase/config.toml
git commit -m "chore(801): enable local email confirmations (prod parity, inbucket)"
```

### Task 5: Docs + issue acceptance reword

**Intent:** Retire the contradictory acceptance criteria; record the SMTP-era reality in the epic; hand the owner the secret/verification checklist.
**Acceptance:** `docs/epics/2026-08-07-tortoise-user-journeys/05-plan.md` carries the STALE/STATUS annotations under E2E-1 and E2E-8; the scoping comment with the reworded acceptance is posted on #801 (comment only — closing is a separate decision after CI proves the smoke green with secrets).

**Files:**
- Modify: `docs/epics/2026-08-07-tortoise-user-journeys/05-plan.md` (E2E-1 setup note + E2E-8 status)
- GitHub: `gh issue comment 801` (reworded acceptance below — **comment only; do NOT close**; closing is a separate decision after CI proves the smoke green with secrets)

**Step 1: Annotate E2E-1/E2E-8 in the epic plan** — add one line under E2E-1 Setup: "STALE 2026-08-10: prod confirmations are ON (SMTP-era, #832); the OFF variant is retired for hosted prod — the mocked welcome suite + live smoke own this journey (see #801 plan)." Under E2E-8: "STATUS 2026-08-10: prod setting is ON — E2E-8 is the live reality; the live smoke asserts its entry state."

**Step 2: Reword the issue acceptance** (post the comment; **do NOT close** — closing is a separate decision after CI proves the smoke green with secrets, per the Task 5 Acceptance):

> **Acceptance (reworded 2026-08-10 — originals contradicted prod state; #832 fixed the root cause):**
> 1. **No-429 monitor (live, per CI run):** `tests/e2e/test_welcome_page.py::test_live_signup_no_429_confirmation_required` passes in CI with `SUPABASE_URL`/`SUPABASE_SERVICE_KEY` set — a fresh prod signup returns HTTP 200, the page shows `#confirmation-required` with the typed email, `#error` is hidden, the URL stays clean, and the created user is deleted via the Admin API. Consecutive CI runs = consecutive fresh signups (the "two consecutive" evidence). A 429 anywhere in the funnel = red CI with the server body in the message.
> 2. **Client 429 lockout:** `test_429_signup_rate_limit_is_humanized` (legal-e2e CI job + post-deploy verify-legal) asserts the humanized copy, disabled submit with live countdown, sessionStorage lockout timestamp (~1h), zero retry requests after lockout, and reload persistence. Static pin `test_rate_limit_lockout_guards_present` (main suite) pins the literals.
> 3. **Key-reveal journey stays covered:** mocked welcome suite (provision-via-edge-function, reveal-once, returning state) green in CI; live key-reveal remains owned by deploy verification (#839) — a live full journey is NOT run per-push because it mints permanent prod teams/graphs with no cleanup endpoint.

**Step 3: Commit**

```bash
git add docs/epics/2026-08-07-tortoise-user-journeys/05-plan.md
git commit -m "docs(801): annotate E2E-1/E2E-8 for SMTP-era confirmations-ON reality"
```

### Task 6: Full verification pass + PR

**Intent:** Prove every gate green before `commit-workflow`.
**Acceptance:** All Task 1–5 commands green in the worktree (pins, extended e2e via wrangler dev, mocked welcome suite, waitlist suite); `docs/plans/2026-08-10-801-signup-rate-plan.md` passes `scripts/check-doc-affiliation.cjs`; PR opened with deploy steps + post-merge verification table; `commit-workflow` review gate clean.

**Files:** (none — verification)

**Step 1: Local gates**

```bash
python -m pytest tests/test_signup_form_safety.py -v                       # static pins
python -m pytest tests/e2e/test_welcome_page.py -q                        # mocked suite (live skips)
RUN_LEGAL_E2E=1 BASE_URL=http://127.0.0.1:8788 python -m pytest tests/e2e/test_signup_form_safety_e2e.py -v
RUN_LEGAL_E2E=1 BASE_URL=http://127.0.0.1:8788 TORTISE_HOST_CHECK=1 python -m pytest tests/e2e/test_legal_pages.py -q
python -m pytest tests/test_waitlist_form.py -q                           # welcome-e2e job companion
```

**Step 2: PR + review gate** — invoke `skills/commit-workflow/SKILL.md` (mandatory pre-commit), then `skills/code-review/SKILL.md` for the PR (this is a Standard+ surface: live-test semantics + inline JS + CI wiring).

---

## Acceptance Criteria (reworded — replaces the contradictory originals)

1. **Live no-429 property is a per-run CI monitor:** `test_live_signup_no_429_confirmation_required` passes in `welcome-e2e` with secrets set (HTTP 200 on `auth/v1/signup` + `#confirmation-required` + `#confirm-email` == typed email + `#error` hidden + URL clean + Admin-API user deletion in teardown). If it skips (secrets missing), the job prints a `::warning::` — it must never silently skip again.
2. **Client lockout:** after a 429, `signup.html` disables submit/resend with a live `Try again in MM:SS` countdown, persists a ~1h `sessionStorage` timestamp, early-returns before any request, and survives reload — proven by the extended mocked 429 e2e (runs in CI legal-e2e + post-deploy verify-legal) and the new static pin (main suite).
3. **Key-reveal journey unchanged and covered:** all existing mocked welcome tests stay green; the live journey's key-reveal step is explicitly owned by the mocked suite + #839 deploy verification, not by a per-push live provisioning run (no prod junk).
4. **Local parity:** `supabase/config.toml` has `enable_confirmations = true` (inbucket), so the confirmation branch and the 429 UX are exercisable locally.
5. **Coherent acceptance posted:** #801's acceptance reworded per Task 5 Step 2 (scoping comment posted; the 429 claim corrected to project-wide, not per-IP; **closing deferred until CI proves the smoke green with secrets**).

## Runtime Prerequisites

- **GitHub secrets (owner action — currently MISSING, verified `gh secret list`):** add `SUPABASE_URL` (`https://ybetwichurajbfswfeqa.supabase.co`) and `SUPABASE_SERVICE_KEY` (regenerate from Supabase dashboard if the value is lost). Until added, the live smoke skips with a warning — all other work is unaffected.
- Local: `npx wrangler@4 pages dev . --port 8788 --ip 127.0.0.1 --compatibility-date=2024-09-23` for the signup-safety suite; `supabase start` (Docker) for the config.toml parity check.
- No new CI secrets beyond the two above (Approach C adds none — Approach B's `SUPABASE_ACCESS_TOKEN` is explicitly NOT needed).
- No migrations, no edge-function deploys.

## What CANNOT be verified locally (no prod creds) — post-merge verification

| Item | Where it gets verified |
|---|---|
| Live no-429 property (SMTP-era prod signup) | First `welcome-e2e` run after merge + secrets added — the live smoke's HTTP-200 assertion |
| Admin DELETE helper against real prod | Same run (teardown log line `deleted user …`); behavior is standard GoTrue (verified docs) |
| `#confirmation-required` + `#confirm-email` against live prod | Same run |
| Post-deploy backoff code on prod page | `verify-legal` job in `deploy-pages.yml` after the website deploy |
| Secret presence | `gh secret list` + the new skip-warning step |

## Deploy Steps (website change)

- `website/signup.html` ships via the existing `deploy-pages.yml` (push to main, `paths: website/**` → `wrangler pages deploy . --project-name=premise-labs` → live at premiselabs.co/signup). No manual Pages step; no DNS change.
- `verify-legal` (same workflow, after deploy) will run the signup-safety suite against prod — this is the post-deploy gate for the lockout code.
- `supabase/config.toml` is local-only (no deploy; prod auth config unchanged — confirmations stay ON).
- Pre-deploy CI runs test the PR's page code via wrangler dev (legal-e2e) and the old-but-sufficient live page for the smoke (confirmation state shipped pre-#832) — no deploy-order flakiness.

---

## Code-Review Deltas (2026-08-10, applied before merge)

Deviations from the task bodies above, applied after the code-review gate (all verified):

1. **Task 2 — `expect.poll` removed:** playwright-python has no `expect.poll` (JS-only) — the live smoke now polls the captured response with a manual deadline loop (`time.time()` + 250ms waits, 30s cap).
2. **Task 1 — resend 429 handling fixed (P1):** supabase-js v2 `auth.resend()` RETURNS `{data, error}` on HTTP 429 (never throws). The handler now destructures the return and applies `setRateLimitLockout(error)` + tier-aware note; `catch` remains for network failures. Previously a 429 showed the false "Resent — check your inbox" and never locked out.
3. **Task 1 — interval only while locked:** `applyRateLimitLockout` starts the 1s ticker only when `rateLimitRemainingMs() > 0` (an idle interval's tick could re-enable the submit button mid-request → double-signup → bucket burn).
4. **Task 1 — tier-aware copy:** new `rateLimitMessage()` — short (per-IP) tier shows "Please try again in a minute." instead of the pinned "about an hour" literal; the error branch overrides `humanizeAuthError` for non-email-bucket codes. Pinned literals unchanged (static pins green).
5. **Task 1 — never-shrink + storage-fail-open:** `setRateLimitLockout` mirrors the deadline in memory (`rateLimitUntil`) and takes `Math.max(existing, now+ms)` — a later 60s lockout can never truncate an active 1h one, and the lockout holds even when sessionStorage is unavailable; NaN stored values are ignored (`Number.isFinite`).
6. **Task 1 — CDN-guard interaction:** the on-load lockout restore is skipped when `window.supabaseClient` is falsy (#527's "temporarily unavailable" state wins).
7. **Tests — discriminating two-tier asserts:** the 1h test now asserts `until - now_ms >= 50min` (a 60s-tier regression fails); three new e2e tests: short-tier 60s + expiry recovery, non-rate-limit error writes no lockout key, resend-429 → lockout + note + no false success.
8. **Task 3 — welcome-e2e `concurrency` group** (`cancel-in-progress: true`) so parallel PRs cannot exhaust the shared prod email bucket.
9. **PR/plan wording:** "#839 owns live key-reveal" corrected — no automated live key-reveal run exists; a weekly scheduled live journey with server-side cleanup is documented as future work (tracked in the plan's post-merge table).
10. **Issue close:** #801's close is deferred (per Task 5) — the PR references it without the auto-close keyword.
