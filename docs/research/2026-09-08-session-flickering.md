# Session Flickering Analysis: Cross-Subdomain OAuth Loop

**Date:** 2026-09-08

**Report:** Cross-subdomain session flickering between `app.premiselabs.co` and `tortoise.premiselabs.co/auth` during Google OAuth sign-in.

---

## 1. Flow Trace: Normal (Expected) Path

### Step-by-step (what *should* happen):

1. **User lands on `tortoise.premiselabs.co/auth`**, enters beta code `betatester`, clicks "Sign up with Google"
2. `signInWithProvider('google')` calls `supabaseClient.auth.signInWithOAuth()` with `redirectTo: 'https://app.premiselabs.co'`
3. Google redirects to `https://app.premiselabs.co#access_token=...&refresh_token=...&expires_at=...&provider_token=...`
4. **Dashboard `index.html` loads** at `app.premiselabs.co#access_token=...`
5. `<script src="/assets/supabase-session.js">` — the **IIFE at the bottom** executes:
   - Sees `#access_token=...`
   - Parses fragment → constructs **minimal session** (access_token, refresh_token, expires_at — **NO `user` object, NO `identities`**)
   - Calls `storeSession(session)` → calls `supabaseStorage.setItem(COOKIE_NAME, JSON.stringify(minimalSession))`
   - The minimal session is small (~1-2KB) — well under SIZE_GUARD (3800 bytes), so **no stripping occurs**
   - **Strips the hash**: `history.replaceState(null, '', window.location.pathname + window.location.search)`
6. **Head gate runs immediately after**:
   - `readValidSession()` → reads cookie → finds minimal session → `hasSession = true` → **does NOT bounce**
7. **Dashboard SPA boots** (`main.jsx`):
   - `supabaseClient = window.supabase.createClient(...)` with `detectSessionInUrl: true`, `storage: supabaseStorage`
   - `const landingHash = window.location.hash` → **empty** (already stripped by IIFE)
   - Mount effect: `supabaseClient.auth.getSession()` → reads cookie → finds minimal session → token is valid → proceeds
   - supabase-js internally calls `_getUser()` → fetches full user from GoTrue API
   - Then calls `_saveSession()` → **writes FULL session** (with `user.identities`, `user.user_metadata`, `app_metadata`) via `supabaseStorage.setItem()`

This is where the problem starts.

---

## 2. Root Cause Analysis

### Primary Root Cause: SIZE_GUARD Discrepancy Between the Two Storage Adapters

There are **two storage adapters** that write to the `sb-tortoise-auth-token` cookie, and they have **different SIZE_GUARD behavior**:

| Storage Adapter | Strips `provider_token` | Strips `provider_refresh_token` | Strips `user.identities` | Compresses `user.user_metadata` |
|---|---|---|---|---|
| **`website/assets/supabase-session.js`** (shared bridge, used by signup/welcome/dashboard head) | ✅ | ✅ | ✅ | ✅ (keeps only display_name, avatar_url, full_name, name) |
| **`website/apps/dashboard/src/main.jsx`** (dashboard SPA's own adapter) | ✅ | ✅ | ❌ **NOT STRIPPED** | ❌ **NOT COMPRESSED** |

**The consequence:**

When supabase-js on the dashboard SPA saves the full session (after `getSession()` + `_getUser()`), it writes a cookie with:
- `user.identities` — a potentially large array (~500-2000+ bytes depending on number of linked identities)
- `user.user_metadata` — full object with all Google profile fields (iss, sub, picture URL, etc.)

The total encoded cookie value can exceed **4096 bytes** (the cookie size limit). When the write exceeds this, the browser **silently rejects** the `Set-Cookie` directive per RFC 6265. The cookie is **not updated**.

### The Flickering Cycle Mechanism

The cycle requires two asymmetric conditions:

| Subdomain | `readValidSession()` result | Action |
|---|---|---|
| `tortoise.premiselabs.co/auth` | Session found (minimal cookie from IIFE) | Redirects to `app.premiselabs.co` |
| `app.premiselabs.co` | **Session NOT found** (cookie was lost) | Bounces to `tortoise.premiselabs.co/auth` via `bounceToAuth()` |

**The cookie loss happens as follows:**

1. **Dashboard SPA boot:** supabase-js reads the minimal session from cookie → validates → `getSession()` returns it → `_getUser()` fetches user → `_saveSession()` writes full session
2. **The full session exceeds 4096 bytes** (main.jsx's SIZE_GUARD does NOT strip `user.identities` or `user.user_metadata`)
3. **Cookie write is silently rejected** by the browser — the old minimal session is gone
4. **BUT** — on some browser implementations, the **failed Set-Cookie for a domain cookie may also delete the existing cookie** (browser-specific behavior: some agents process the set-cookie as "remove then add" — the remove succeeds but the add fails)
5. **On next page load**, `readValidSession()` finds no cookie → `hasSession = false` → dashboard head gate bounces to `tortoise.premiselabs.co/auth`
6. **On `tortoise.premiselabs.co/auth`**, signup.html's head gate checks `readValidSession()` → **this reads the LEGACY localStorage keys** (`sb-ybetwichurajbfswfeqa-auth-token`) → might find a stale session there → valid → redirects back to `app.premiselabs.co`
7. **Back at `app.premiselabs.co`**, the legacy key is not readable (localStorage is origin-scoped) → cookie still missing → bounce again
8. **Cycle repeats** until either:
   - The legacy key session expires
   - The cookie write eventually succeeds for some reason
   - The user's browser caches the session in memory

### Secondary Issue: Fragment Stripping Before supabase-js

The #2508 IIFE at the bottom of `supabase-session.js` runs **before** supabase-js initializes:

```javascript
(function () {
  // ...parses #access_token=...
  storeSession(session);  // stores to cookie
  history.replaceState(null, '', window.location.pathname + window.location.search);  // STRIPS HASH
})();
```

This means `supabaseClient.auth` with `detectSessionInUrl: true` will **never see the OAuth callback fragment**. It initializes as a "returning user" rather than a "fresh sign-in". While this technically works (the IIFE stored the session), it changes supabase-js's internal initialization path — skipping the `SIGNED_IN` notification that would normally fire during an OAuth return detection.

---

## 3. Hypothesis Evaluation

| Hypothesis | Verdict | Evidence |
|---|---|---|
| **(a)** Cookie too large → gate sees no session → bounce to auth → fragment parsed → cookie written → redirect → cookie too large again → loop | **PRIMARY** | SIZE_GUARD discrepancy confirmed. main.jsx does NOT strip `user.identities` or compress `user.user_metadata`, causing cookie write failures for users with large Google profiles. |
| **(b)** Synchronous fragment IIFE writes session and strips hash, then supabase-js fails detectSessionInUrl → state mismatch | **CONTRIBUTING** | The IIFE strips the hash before supabase-js can process it. This changes initialization semantics but alone wouldn't cause a loop — the session is still stored in the cookie. |
| **(c)** Race condition between two session-storage flows | **UNLIKELY** | The IIFE runs synchronously in `<head>` before any other scripts. No race with supabase-js loading. |

---

## 4. Fix Options (Ranked by Simplicity + Effectiveness)

### Fix A: Align SIZE_GUARD in main.jsx with supabase-session.js (RECOMMENDED)

**Complexity:** Very Low (add ~10 lines)
**Effectiveness:** High

Update the `setItem` SIZE_GUARD in `website/apps/dashboard/src/main.jsx` to match `website/assets/supabase-session.js`:

```javascript
if (encoded.length > SIZE_GUARD) {
  try {
    const obj = JSON.parse(value)
    delete obj.provider_token
    delete obj.provider_refresh_token
    // ADD: strip user.identities and compress user.user_metadata
    if (obj.user) {
      delete obj.user.identities
      if (obj.user.user_metadata) {
        const keep = {}
        if (obj.user.user_metadata.display_name) keep.display_name = obj.user.user_metadata.display_name
        if (obj.user.user_metadata.avatar_url) keep.avatar_url = obj.user.user_metadata.avatar_url
        if (obj.user.user_metadata.full_name) keep.full_name = obj.user.user_metadata.full_name
        if (obj.user.user_metadata.name) keep.name = obj.user.user_metadata.name
        obj.user.user_metadata = keep
      }
    }
    encoded = encodeURIComponent(JSON.stringify(obj))
  } catch { /* not JSON — leave as-is */ }
  if (encoded.length > SIZE_GUARD + 100) {
    console.warn(`${COOKIE_NAME} session exceeds cookie size cap (${encoded.length} bytes)`)
  }
}
```

**Why this works:** Prevents the full session write from exceeding 4096 bytes. `user.identities` and full `user.user_metadata` are not needed for auth validation — only `access_token`, `refresh_token`, and `expires_at` are required for `readValidSession()`.

### Fix B: Make storeSession Skip Provider Tokens Entirely in the IIFE

**Complexity:** Very Low
**Effectiveness:** Medium (partial fix)

The IIFE at the bottom of `supabase-session.js` includes `provider_token` and `provider_refresh_token` in the session it stores. These are only needed by the initiating OAuth flow, not by the dashboard. Excluding them would make the initial cookie smaller:

```javascript
// Don't add provider_token/provider_refresh_token to the session object
// They're stripped by setItem anyway if over SIZE_GUARD
```

This reduces the first-write size but doesn't address the main.jsx write path.

### Fix C: Store Only `access_token` + `refresh_token` + `expires_at` in Cookie

**Complexity:** Medium
**Effectiveness:** High

Refactor the cookie to store only the minimum needed for `readValidSession()` — a thrified session object with exactly `access_token`, `refresh_token`, `expires_at` — and keep the full session in memory only. This would require changes to both adapters.

### Fix D: Have supabase-js On the Dashboard Call `readValidSession()` Instead of `getSession()` for the Head Gate

**Complexity:** Low
**Effectiveness:** Medium (addresses symptom, not cause)

The dashboard mount effect already checks `getSession()` which goes through the full supabase-js validation path. The head gate uses the lighter `readValidSession()`. If `readValidSession()` returns valid but `getSession()` fails, the head gate and the mount effect are inconsistent.

---

## 5. Recommended Action Plan

### P0: Align SIZE_GUARDs (Fix A above)

1. Copy the `user.identities` strip + `user.user_metadata` compress logic from `website/assets/supabase-session.js` into the `setItem` in `website/apps/dashboard/src/main.jsx`
2. Verify the static sync test `tests/test_cross_subdomain_cookie_sync.py` covers this
3. Deploy and monitor

### P1: Add Cookie Write Confirmation After Full Session Save

1. After supabase-js calls `_saveSession()` (internally), verify the cookie was actually written by checking `readCookie(COOKIE_NAME)` returns the expected value
2. If the write failed, log a warning and fall back to keeping the session in memory only (supabase-js already does this internally via `this.inMemoryAuth`)

### P2: Audit OAuth Error Fragment Forwarding

1. Verify that `#error=...` fragments from denied OAuth round-trips are properly forwarded through the cycle (the dashboard's `oauthErrorHash()` and `oauthErrorParams()` logic handles this, but the cycle could lose the error)

### P3: Test With Realistic Google Profiles

1. Add E2E test coverage for a Google OAuth flow with a profile that has:
   - Multiple `identities` (e.g., GitHub-linked + Google-linked)
   - Large avatar URLs (Google avatar URLs can be 200+ characters)
   - Full `user_metadata` payload
   - Provider tokens (Google access token ~1000 bytes)

---

## 6. Files Examined

| File | Role |
|---|---|
| `website/signup.html` | Auth card page (beta gate + OAuth buttons) |
| `website/assets/supabase-session.js` | Shared session bridge + IIFE fragment parser + gate helpers |
| `website/apps/dashboard/public/assets/supabase-session.js` | Byte-identical copy of above |
| `website/apps/dashboard/index.html` | Head gate + SPA bootstrap |
| `website/apps/dashboard/src/main.jsx` | Dashboard SPA (supabase client + cookie adapter + auth mount logic) |
| `website/welcome.html` | Post-auth landing page (gate helpers + password reset) |