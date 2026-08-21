---
title: "Auth architecture — standard patterns vs Tortoise (research + audit)"
type: operations
domain: operations
doc_status: live
created: 2026-08-19
ownedBy: epistemic-team
---

# Auth architecture: standard patterns vs Tortoise

Research note (2026-08-19, issues #1498/#1506). User report: auth checks felt
slow ("checking session" flash), protected pages (welcome/dashboard) were
reachable without auth, and the flow used multiple different auth screens.
This documents the standard architecture, what Tortoise actually does, the
gaps, and the fixes.

## 1. The standard architecture for web auth (2025 practice)

### 1.1 Three session primitives (Supabase, canonical)

The Supabase auth SDK exposes three functions with distinct trust/cost
profiles — the pattern generalizes to any JWT-based auth:

| Function | Source | Trust | Cost |
|---|---|---|---|
| `getSession()` | storage read (cookie/localStorage) | **NOT trusted** — the embedded user object isn't re-validated | ~0ms (sync) |
| `getClaims()` / local JWT verify | storage read + local verification (WebCrypto + cached JWKS) | trusted identity | ~0ms (sync, no network) |
| `getUser()` | network call to the auth server | authoritative | ~1 round-trip (slow) |

**The rule:** `getSession` is for *routing decisions* (fast UX), never for
*authorization* (the server validates the token on every request it protects).

### 1.2 The three layers of a standard auth architecture

1. **Client-side route guard (instant)** — read the session synchronously
   from storage on every protected page/route; if absent, redirect
   immediately (before first paint). Never block first paint on a network
   call. `onAuthStateChange` reacts to sign-in/out.
2. **Server-side authorization (authoritative)** — the API validates the
   Bearer token (JWT signature/expiry) on every protected request. The
   client's session object is only a *claim*; the server is the source of
   truth.
3. **Session transport (shared, cookie-based)** — the session lives in an
   `HttpOnly`-where-possible cookie (`Secure`, `SameSite=Lax`) so it
   survives reloads and is shareable across subdomains via a parent-domain
   cookie. Client-side auth libs that need the token read it from the
   cookie (or a non-HttpOnly mirror) — with the XSS caveat that any
   JS-readable session must be paired with strict output encoding.

### 1.3 The cross-subdomain pattern

A shared parent-domain cookie (`Domain=.example.com`) is the standard way to
share a session across `app.example.com` and `www.example.com`. Each
subdomain's auth client reads/writes the same cookie; the server validates
the token regardless of which subdomain presented it.

## 2. What Tortoise has

### 2.1 The session

- **Provider:** Supabase (GoTrue), JWT access + refresh tokens.
- **Transport:** a custom storage adapter (`website/assets/supabase-session.js`)
  persists the supabase-js session to a **parent-domain cookie**
  (`sb-tortoise-auth-token` on `.premiselabs.co`) so `app.premiselabs.co`
  (dashboard) and `tortoise.premiselabs.co` (auth pages, welcome) share one
  session. Non-HttpOnly (JS reads it) — mitigated by textContent-only
  rendering and server-side token validation.
- **Legacy cohort:** pre-#1225 sessions still in origin-scoped localStorage
  (`sb-ybetwichurajbfswfeqa-auth-token`) are migrated to the cookie on load
  (supabase-session.js `migrateLegacySession`, runs on the tortoise origin).
  The dashboard's head gate reads only the parent-domain cookie — a
  legacy-cohort holder hitting the dashboard directly gets one bounce to
  `/auth` (where migration runs and they're redirected back). Self-healing;
  the cohort shrinks as legacy sessions are migrated/expired.

### 2.2 The auth surfaces (after the #1498 consolidation)

- **One auth page** at `tortoise.premiselabs.co/auth` (combined Log in /
  Sign up card: GitHub/Google OAuth + API-key and email modals). `/signin*`
  → 301; `/signup` is a legacy alias.
- **Protected pages:** `/welcome` (post-auth provisioning) and the dashboard
  (`app.premiselabs.co`).

### 2.3 The gates (after #1498/#1506)

| Surface | Gate | Timing |
|---|---|---|
| `/auth` | synchronous head-gate cookie check → `location.replace` to welcome/dashboard for signed-in visitors | instant (before paint) |
| `/welcome` | synchronous head-gate cookie check → `location.replace` to `/auth` when no session (callback hashes exempt) | instant (before paint) |
| Dashboard | **NEW (#1506):** synchronous head-gate cookie check in `index.html` → `location.replace` to `/auth` when no session/key/claim | instant (before the app bundle renders) |
| API | Bearer-token validation per request | authoritative |

### 2.4 What was wrong (the user report)

1. **The dashboard checked the session asynchronously** via
   `supabaseClient.auth.getSession()` inside the React mount effect — which
   can trigger a network token refresh (supabase-js v2 auto-refreshes
   near-expiry tokens) and takes ~0.7–1s — while rendering a "Checking your
   session…" card. No-session visitors saw that card **flash** before the
   redirect, and the redirect was (until #1498) a history-pushing `href`
   so **Back returned to the dashboard**.
2. **The dashboard hosted its own embedded login/signup card** — a second,
   different auth screen ("the wrong screen").
3. **No synchronous gate on the dashboard at all** — it depended on the
   async React check.

## 3. The fixes

1. **#1498 (merged):** one `/auth` page; `/welcome` + `/auth` head gates
   (synchronous cookie read, `location.replace` = Back-proof); the
   dashboard's embedded login/signup card removed.
2. **#1506 (this change):** the dashboard's `index.html` now has the same
   synchronous head gate — no session + no stored API key + no claim in
   flight → `location.replace` to `/auth` **before the React bundle loads**.
   No "checking session" flash for unauthenticated visitors; the
   "No active session" card is gone (the only in-dashboard auth is the
   API-key paste, reachable only by key/claim holders).

## 4. Residual risks / recommendations

1. **Non-HttpOnly session cookie** — the shared cookie must be JS-readable
   for supabase-js, so XSS in any subdomain can exfiltrate a session. The
   current defense (textContent-only sinks, no user HTML, server-side
   validation) is the practical standard for this architecture; a stricter
   alternative (HttpOnly + server-proxied auth) is a larger refactor.
2. **The dashboard's post-mount `getSession()` can still refresh the token
   over the network** for genuine session holders near expiry — by design
   (keeps sessions alive). Since #1567 the app chrome renders immediately
   for session holders and the mint/loads hydrate in the background (no
   "Checking your session…" card on the happy path); token-refresh latency
   only affects data hydration, never first paint.
3. **Authorization is server-side** (the API validates Bearer tokens) —
   correct per §1.2. The client gates are routing conveniences only.
4. **Stale legacy localStorage sessions** after a dashboard sign-out are a
   narrow transient (migrateLegacySession clears them on first load) — no
   security boundary (server-side revocation governs).

## 5. #1511 — auth unification: one page, strict validity, key→session exchange

Issue #1511 (2026-08-19/20) closed the remaining gaps: the dashboard could
strand users on a key-only card, `/auth` lacked "Last used" labels, browser
API-key login was broken (a cross-origin localStorage write the dashboard
couldn't read), and stale sessions leaked into `/welcome`. What changed:

### 5.1 The dashboard never shows auth UI

- The embedded key-only card and its handlers/state are **gone**. The
  dashboard's only `!authed` surface is the **claim-paste** screen
  (paste `tt_` → OAuth → claim), reachable exclusively by genuine
  claim-intent (`tt_claim_key` sessionStorage, `tt_claim_pending` cookie,
  or `?claim=1`).
- Everything else redirects **instantly** to `/auth` — the synchronous
  head gate (shared `readValidSession`) + the mount effect's
  origin-aware `bounceToAuth` (`location.replace`, Back-proof).
- The old "stored key = credential" exemption is gone: a stored
  `tortoise_api_key` is a "Last used" *hint* on `/auth`, never a
  dashboard credential.

### 5.2 `/auth` is the only login surface, with "Last used"

- Four options on one card: **GitHub, Google, API key, email/password**
  (email via modal). A non-secret parent-domain cookie
  (`tt_last_auth_method`, one-time migration from the dashboard's legacy
  `tortoise_last_auth_method`) labels the option used last.
- Both gates (synchronous head + async `getSession` bounce) enforce
  **strict validity** — `access_token` present **and** `expires_at`
  present + future. Missing/past `expires_at` = not authed (the
  presence-over-validity class of bug is gone).

### 5.3 Browser API-key login: the server-side exchange

The raw `tt_` key never crosses origins (it can't ride a cookie — it's a
graph credential; it can't be written cross-origin — SOP). Instead:

1. `/auth` pastes the key → `POST /v1/session/login` (JSON body) →
   the server validates it via the normal key-resolution path (parity
   with `/v1/team`), applies a **forced** dashboard-key-login gate, and
   mints a real Supabase session **server-side**: GoTrue admin
   `generate_link {type:magiclink}` (no email is sent) + service-role
   `/verify` → the full `AccessTokenResponse` (+ injected `expires_at`,
   which GoTrue's `/verify` does not ship).
2. The mint target is the key's **creator** (an active team member — no
   member-key escalation). `created_by`-attribution was fixed so
   dashboard-minted keys record the session user's UUID; "api"/NULL
   creators are 403 `KEY_NOT_USER_MINTED`; ownerless (anon) teams funnel
   to the dashboard claim flow (`tt_claim_pending` + `?claim=1`).
3. The client stores the returned session **directly into the parent-domain
   cookie** (supabase-js `auth.setSession` does a network round trip — not
   instant, not mockable), verifies the write (`storeSession` →
   `readValidSession`), sets the last-used marker, and lands on the
   dashboard.
4. Guards: per-IP rate-limit bucket (5/hr, real client IP), audit
   `session_mint`, post-verify membership backstop (TOCTOU), distinct
   error codes (`ANON_TEAM_NO_OWNER`, `KEY_NOT_USER_MINTED`,
   `ACCOUNT_MISSING`, `dashboard_login_disabled`), 502/503 transient
   (never fed into the login lockout bucket).

### 5.4 Welcome never renders unauthenticated

- The head gate + `waitForSession` use strict validity. A provisioning
  **401** (stale/invalid session) now strips the callback hash, clears the
  session (cookie + legacy keys — so `/auth`'s gate can't re-bounce it),
  and redirects to `/auth`. Non-401 failures keep the retry-once +
  contact-support error state.

### 5.5 Shared client helpers

`website/assets/supabase-session.js` (copied to the dashboard's
`public/assets/`) exposes one validity predicate + clear + last-used +
bounce helpers — loop-safety by construction across all three pages:

- `readValidSession()` — strict cookie→legacy read
- `clearStoredSession()` — cookie + both legacy localStorage keys
- `get/setLastAuthMethod()` — `tt_last_auth_method`
- `bounceToAuth(search, hash)` — origin-aware absolute/relative target
- `storeSession(session)` — direct parent-cookie write (the exchange path)

### 5.6 Test coverage

- `tests/test_session_login.py` (18) — exchange contract, evaluation order, error tree, rate limit, TOCTOU, expires_at injection, transport 502, session-identity backstop, session-attribution.
- `tests/test_session_login_helpers.py` (6) — mint-target resolution.
- `tests/e2e/test_session_login_flow.py` — two-origin loop regression
  (exchange → cookie → dashboard renders; no cookie → instant redirect;
  ANON → claim funnel) via prod-domain route interception.
- `tests/e2e/test_dashboard_gate.py`, `test_welcome_page.py` (401 →
  clear → `/auth`, no welcome↔/auth loop), `test_cross_subdomain_cookie_sync.py`
  (helper presence + byte parity), `test_writer_inventory.py` /
  `TestCreateApiKeySessionAttribution` (created_by = session UUID).
