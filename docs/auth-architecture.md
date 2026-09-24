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

> **CURRENT ARCHITECTURE (#4054, 2026-09-18).** For the BFF session the client-side, JS-readable
> parent-domain cookie described in the original 2026-08-19 note has been REMOVED: for that
> session the browser holds only an HttpOnly `__Host-session` opaque handle issued by a
> server-side BFF on the app origin, and the access/refresh tokens live in D1 (`SESSIONS`) and
> never reach the browser. **The removed design is still being issued:** the MCP consent page
> still issues a JS-readable parent-domain cookie, and two surfaces still accept it — the
> blog-admin console (`/admin`, itself an app-origin BFF page) and the marketing-origin blog
> Functions — so tokens DO reach the browser for a consent-page visitor (§2.1 "Legacy cohort",
> where the `OVERRIDES` ruling is violated). §2.1–§2.3 and §5.5 below are the current state;
> §2.4, §3 and §5.1–§5.4 are the historical record of the pre-BFF design and its fixes (each
> carries a superseded note). §4 is historical for items 2 and 4 only — **item 1 is open** and
> item 3 still holds.

### 2.1 The session

- **Provider:** Supabase (GoTrue), JWT access + refresh tokens — held SERVER-side for the BFF
  session. (A consent-page visitor's tokens are in the legacy cookie instead — see the
  "Legacy cohort" bullet below.)
- **Transport (current) — for the BFF session:** a server-side BFF on the app origin. For that
  session the browser receives only an opaque `__Host-session` (plus `__Host-authflow`) cookie:
  `HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=…`, with **no `Domain`
  attribute** — the `__Host-` prefix enforces host-only, so a cookie set on one
  subdomain can never authenticate another. Issued and cleared by
  `website/apps/dashboard/functions/_shared/auth/session.ts`
  (`SESSION_COOKIE = "__Host-session"`, `buildCookie`).
- **Session store:** the opaque handle keys a D1 row (`SESSIONS` binding) holding
  `user_id`, the refresh token, the **session** expiry (`expires_at` — the session's own
  TTL, not the refresh token's), and a cache of the access token (`access_token`,
  `access_token_expires_at`) plus a refresh-cooldown stamp (`token_rejected_at`) — the
  last three added by `_shared/auth/token.ts`, so a read path reuses the cached token
  instead of calling GoTrue per request. The BFF's access token never leaves the server: it is
  minted/refreshed in-process by the Function and is never sent to the browser; the cookie
  is revocable immediately (the row is marked `revoked = 1`; nothing deletes it).
- **OVERRIDES:** the standard cross-subdomain session — a `Domain=.premiselabs.co` cookie shared by
  every subdomain (§1.3) — is **rejected**. It is JS-reachable from any subdomain and forfeits the
  `__Host-` prefix. One session-bearing origin is worth the extra 301. The recorded ruling is the
  auth-topology decision on **#3501 / #4054**; the full rationale lives in the private `premise-labs`
  repo (`engineering/auth/SCOPE.md` §3, §4 W6, §13), which this repo's Functions also cite — named
  here because it is outside this repository and cannot be opened from it.
- **Legacy cohort — a SECOND, LIVE session credential (the ruling above is VIOLATED here).**
  The legacy JS-readable parent-domain cookie
  `sb-tortoise-auth-token` is **not** the session backbone (the canonical session is the HttpOnly
  `__Host-session` above): the bridge that once
  wrote it (`website/assets/supabase-session.js`) is loaded by no BFF page
  (`tests/test_cross_subdomain_cookie_sync.py` pins `PAGES = []`). **But it is still ISSUED and still ACCEPTED** — a real,
  JS-readable Supabase session, not inert scaffolding:
  - **Issued by — the legacy writer, and the UNFIXED EXCEPTION to the ruling above** (tracked, not
    accepted): the MCP consent page in `tortoise/oauth.py`, served live at `/oauth/authorize`
    (`tortoise/hosted_api.py:27035`) — that legacy cookie's production origin is `api.premiselabs.co`.
    Its inline client uses the same name — `COOKIE_NAME = "sb-tortoise-auth-token"` (`:1445`) — and
    writes that **legacy** cookie with `document.cookie` plus a `Domain=.premiselabs.co` attribute
    (`:1498`), i.e. **parent-domain and JS-readable**, after `signInWithPassword` /
    `signInWithOAuth` / `refreshSession`.
    Removing it is **#3524** (`SCOPE.md` §4 W3); `SCOPE.md` §7's ordering guard defers deleting
    this writer until #3524 ships (`_CONSENT_HTML` — `oauth.py:1445` / `:1467` / `:1498`; §7 cites
    the range `1335-1360`, the head of the `_CONSENT_HTML` block that encloses these lines) — so it
    is still issuing today.
  - **Accepted by** two live surfaces:
    1. the blog-admin console's **data layer** — `website/apps/blog-admin/src/lib/supabase.ts`
       (`STORAGE_KEY`, adapter `authStorage`) is the supabase-js storage adapter, and with `persistSession: true`
       supabase-js recovers the session from it on init, so the console's direct Supabase calls
       (11 PostgREST operation entry points over the 5 `.from('blog_posts')` builders —
       `listPosts`, `listQueue`, `getPost`, `createPost`, `updatePost` — plus 2 authenticated
       Storage calls, `uploadBlogImage` and `deleteBlogImage`; the `getPublicUrl` inside
       `uploadBlogImage` builds a URL locally and sends no credential) authenticate off **this**
       cookie rather than the BFF; and
    2. `website/functions/blog/_shared/admin-auth.ts` — `getAccessToken` falls back to this cookie
       when no `Authorization: Bearer` is presented (always, on the marketing origin, which never
       receives the host-only `__Host-session`).

  Because a legacy parent-domain cookie is genuinely in play, the console's data layer **works
  while that consent-page cookie is present** — it is not waiting on a missing credential. What is
  wrong is which credential it trusts: a **legacy** JS-readable parent-domain session rather than
  the BFF.
  Migrating it is **#4178**; the app-origin `/blog/api/*` path is already off this cookie (that
  proxy resolves `__Host-session` and forwards `Authorization: Bearer <access token>`), but its
  marketing-origin upstream endpoints still accept the cookie via the legacy fallback in (2).

### 2.2 The auth surfaces

> **Functions root:** every `functions/…` path in this doc is relative to
> `website/apps/dashboard/functions/` — the BFF moved with the session to the app project (#4054).
> `website/functions/` (the marketing project) now holds only `_middleware.ts` and `blog/**`, so an
> unqualified path read as "the marketing Functions" points at the wrong tree.

- **One auth page** at `app.premiselabs.co/auth` — `website/apps/dashboard/functions/auth/index.ts`
  rewrites `/auth` to the `/signup` asset, preserving the query string (invite
  tokens, `?error=`, `?next=`). The marketing origins converge on the app origin, and **which layer
  does the 301 depends on the host** (`website/functions/_middleware.ts`, `website/_redirects`):
  `/auth` 301s **unconditionally** in the middleware (so it covers `tortoise.*` and previews/dev),
  while `/signup`, `/welcome` and `/invite-accept` are 301'd by the middleware's `APP_ONLY` branch
  **only on the exact `premiselabs.co` host** and, on the tortoise host, by the static `_redirects`.
  `/signin*` is a legacy alias with no route on the app origin — `_redirects` maps it to `/auth` **on
  the tortoise host** (`tortoise.premiselabs.co/signin` → `/auth` → `app.premiselabs.co/auth`); on the
  company host there is one extra hop first
  (`premiselabs.co/signin` → `tortoise.premiselabs.co/signin`), and on the app origin it 404s. The
  card offers GitHub/Google OAuth, API key and email/password.
- **Protected pages:** `/welcome` (decided server-side) and the dashboard — both on
  `app.premiselabs.co`. `/welcome` is **not** a provisioning page: it renders only the recovery
  reset panel. Team + API-key provisioning is the dashboard's first-run onboarding.

### 2.3 The gates (server-side, #4054)

| Surface | Gate | Timing |
|---|---|---|
| `/auth` | `website/apps/dashboard/functions/auth/index.ts` serves the page; no client cookie check | server render |
| `/welcome` | `website/apps/dashboard/functions/welcome.ts`, four session outcomes (below) | server, before the page is served |
| Dashboard | `website/apps/dashboard/functions/api/session.ts` is the single source of session truth (200 `{user}` / 401 not-signed-in / 503 store-unreachable); the SPA asks it instead of reading a cookie | server round-trip |
| API | Bearer-token validation per request (the BFF holds the token) | authoritative |

`/welcome`'s four session outcomes — the first three answer without rendering a page; the fourth
is the only case that renders one:

The table is grouped by outcome, not by the order `welcome.ts` evaluates them in (`?reset` is
tested at :74, before the signed-in redirect at :100).

| Case | Response |
|---|---|
| signed in | 302 `APP_ORIGIN[/?claim=1]` |
| no cookie / dead session | 302 `/auth?next=…&stale=1` |
| store unreachable (`!env.SESSIONS`, or the D1 read fails) | 503, terminal — **never** a redirect |
| **any `reset` parameter present** **and** signed in | serves the reset-panel asset — **the ONE rendered case** (503 `assets unavailable` instead, if `env.ASSETS` is unbound) |

The reset condition is **presence, not value** — `welcome.ts` tests
`url.searchParams.get("reset") !== null`, so a bare `?reset` renders the panel too.

The last two rows are the ones that bite. Omitting the reset case makes `/welcome` look like a pure
redirect — and that is how the panel's corruption stayed invisible: the Function serves the panel via
`env.ASSETS.fetch`, which **re-enters the asset router**, where `_redirects` *does* apply (unlike
inbound routing, which a Function intercepts first). Redirecting on a D1 blip is the other: it reports
a database outage to the user as "you are signed out".

> **Historical (#1498/#1506 era, REMOVED by #4054):** the gates below were
> synchronous client-side head-gate cookie checks (`readValidSession()` +
> `location.replace`) in each page's `<head>`, and the dashboard's `index.html`
> carried the same check before the bundle rendered. Those checks were removed — a synchronous client
> gate cannot see an HttpOnly host-only cookie; one that tries, in a browser holding no legacy
> session, reproduces the #3485 loop.

### 2.4 What was wrong (the user report)

> ⚠️ **Historical (pre-BFF, superseded by #4054).** The report below describes the client-side
> design that the BFF replaced; it is kept as the problem statement, not as current behaviour.

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

> ⚠️ **Superseded by #4054.** The client-side head gates below were replaced by
> the server-side BFF (§2). They are recorded for history; do not reintroduce a
> client gate that reads a session cookie.

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

> ⚠️ **Superseded by #4054** for items 2 and 4: the client `getSession()` refresh risk below is
> gone for BFF pages. **Item 1 is NOT closed** — a second, JS-readable parent-domain session
> cookie (`sb-tortoise-auth-token`) is still issued (the MCP consent page, §2.1) and still accepted
> by two live surfaces. Item 3 (server-side authorization) still holds.

1. **Non-HttpOnly session cookie** — the shared cookie must be JS-readable
   for supabase-js, so XSS in any subdomain can exfiltrate a session. The
   current defense (textContent-only sinks, no user HTML, server-side
   validation) is the practical standard for this architecture; a stricter
   alternative (HttpOnly + server-proxied auth) is a larger refactor.
   **OPEN, NOT CLOSED.** The canonical session cookie *is* now HttpOnly and
   host-only, so the §1.3 parent-domain session for BFF pages is gone. But a
   **second** JS-readable parent-domain session cookie
   (`sb-tortoise-auth-token`) is still **issued** — by the MCP consent page in
   `tortoise/oauth.py`, on `api.premiselabs.co`, with `Domain=.premiselabs.co`
   — and still **accepted** by the two surfaces in §2.1. So the risk this item
   names is **LIVE, not historic**: while a session holder has visited the
   consent page, XSS on ANY `premiselabs.co` subdomain can read a real Supabase
   session. Closing it needs both halves — stop issuing (**#3524**) and stop
   accepting (**#4178**). Note this is exactly the exposure §2.1's `OVERRIDES`
   ruling exists to prevent, surviving on a surface the ruling covers but which is not
   yet fixed.
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

> ⚠️ **Historical (§5.1–§5.4, pre-BFF, superseded by #4054).** These four sections record the
> 2026-08-19 client-side unification — parent-domain cookie transport included. They describe the
> design of their time; §5.5 is the one part still current (see this doc's §2 preamble).

Issue #1511 (2026-08-19/20) closed the remaining gaps: the dashboard could
strand users on a key-only card, `/auth` lacked "Last used" labels, browser
API-key login was broken (a cross-origin localStorage write the dashboard
couldn't read), and stale sessions leaked into `/welcome`. What changed:

> ⚠️ **Mechanically superseded by #4054.** The flows below are described in
> terms of the pre-BFF client-side cookie (the client storing the session into
> `sb-tortoise-auth-token`, the head gate reading it). The BFF moved every one
> of those steps server-side: for the BFF session the browser stores only a
> `__Host-session` handle, the API-key exchange runs in `functions/auth/api-key.ts`, and
> `/welcome` is decided by `functions/welcome.ts` (§2). The product INTENT below
> (one login surface, strict validity, server-side exchange, welcome never
> rendering unauthenticated) still holds; the mechanism is §2.1–§2.3.

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

### 5.5 Shared client helpers (retained; the dashboard copy was removed by #4054)

The shared bridge `website/assets/supabase-session.js` exposed one validity
predicate + clear + last-used + bounce helpers, and was copied into the
dashboard's `public/assets/`. **#4054 removed that dashboard copy** — a BFF page
must not ship a JS-readable session bridge. The shared file itself is retained
(§2.1) and still declares `readValidSession()` / `clearStoredSession()` /
`getLastAuthMethod()` / `setLastAuthMethod()` / `bounceToAuth()` /
`storeSession()`, but NO BFF page loads it. Three writers of
`sb-tortoise-auth-token` therefore never run **from this file on a BFF page** —
`storeSession()`, `migrateLegacyKeysToCookie()` and `migrateLegacySession()` — and
`readValidSession()` is not a pure read either: it calls
`migrateLegacyKeysToCookie()` first, while `migrateLegacySession()` is reached only
from `createTortoiseSupabaseClient()`. That does NOT make the cookie
unissued: the MCP consent page in `tortoise/oauth.py` writes it independently
(§2.1), and the blog-admin console's own adapter writes it too
(`blog-admin/src/lib/supabase.ts`, `writeCookie`). Whether the cookie is still
ACCEPTED as a credential is a third fact, and it is: see the two surfaces in
§2.1, and #4178 for removing them. The dashboard's auth state now comes
from `functions/api/session.ts` and its bounce is a local same-origin
`location.replace("/auth" + search + hash)` in `main.jsx`; the non-secret
`tt_claim_pending` cookie is still written by `signup.html` and `main.jsx`.

### 5.6 Test coverage

- `tests/test_session_login.py` (28) — exchange contract, evaluation order, error tree, rate limit, TOCTOU, expires_at injection, transport 502, session-identity backstop, session-attribution.
- `tests/test_session_login_helpers.py` (7) — mint-target resolution.
- `tests/e2e/test_session_login_flow.py` — two-origin loop regression
  (exchange → cookie → dashboard renders; no cookie → instant redirect;
  ANON → claim funnel) via prod-domain route interception.
- `tests/e2e/test_dashboard_gate.py`, `test_welcome_page.py` (401 →
  clear → `/auth`, no welcome↔/auth loop); `tests/test_cross_subdomain_cookie_sync.py`
  (helper presence + cookie-contract parity — note it is NOT under `tests/e2e/`),
  `test_writer_inventory.py` (`TestGraphSurface` — the session-mint `created_by` /
  `created_by_key_id` assertions) and `tests/test_session_login.py::TestCreateApiKeySessionAttribution`
  (created_by = session UUID).

## 6. The machine-credential model: unified scoped keys (epic #2083)

Epic #2083 (multi-graph tenancy, shipped 2026-09-04) replaced the implicit
"one key = full team access" model with **one unified key table carrying a
graph scope + a flat scope allowlist** — no new credential type, no key
rotation, no forced migration (E2E-5).

### 6.1 What an API key now IS

| Column | Meaning |
|---|---|
| `graph_id` | NULL = **team-wide key** → resolves to the team's DEFAULT graph (the pre-epic behavior — legacy keys untouched); set = bound to ONE custom graph |
| `scopes` | FLAT allowlist array from `{graphs:read, graphs:write}` — the escalation set (`graphs:create/delete`, `keys:manage`, `team:manage`) is owner-mint-only |
| `delegation_depth` | NULL = owner-minted; 0 = minted by another key (can never hold escalation scopes — DB CHECK + resolution) |
| `created_by_key_id` | mint lineage (a delegated key cannot mint) |

**The owner/legacy class (D2):** `delegation_depth IS NULL AND scopes = []`
⇒ `legacy_full_access = True` — a tt_ key minted before/in the legacy shape
keeps byte-identical full-team behavior (all three resolution lanes — REST,
MCP, apikey_verify — derive the same class). The C3-era scoped mint of a
deleg-NULL key with scopes `[]` + a graph is the documented footgun: it
would read as full access while echoing `scopes:[]` — the shrink branch 422s
it (`Per-graph keys require at least one scope.`).

### 6.2 Enforcement surfaces

- **`_require_scope(scope)`** (REST) / the MCP equivalent: reads require
  `graphs:read`, writes require `graphs:write`; `legacy_full_access` (and
  key-less session faces) are exempt. Graph-bound keys can only touch their
  own graph; team-level write surfaces (index/seed/pack/restore/backup)
  reject graph-bound keys (`_reject_graph_bound_*`) because they act on the
  DEFAULT graph.
- **Delegation is one level:** a `deleg=0` child key is minted with
  `graphs:read` by default; children can never hold `keys:manage`, so they
  cannot mint further keys — the DB CHECK `chk_minted_key_no_escalation`
  is the invariant.
- **C5/C6 data-plane gates:** per-graph context/sessions/capture ride the
  key's graph context (fail-closed 403 `GRAPH_NOT_FOUND` on vanished
  graph-bound keys); the session_recording per-graph override (`PATCH
  /v1/graphs/{id} {recording}`) never re-enables a team-opted-out recording.

### 6.3 The #2082 boundary (deliberate)

Key scopes are **capability gates in the control plane**: they decide which
graph + which data-plane verbs a credential may use. They are NOT an agent
policy system — what an agent does with its granted access (prompts,
tool-selection policy, safety rails) lives in the agent layer, outside the
key model. The epic kept that line: scope allowlisting stays flat and
machine-readable; per-graph ACL *users* (C4) are defense-in-depth for graph
mints, not a policy engine.

### 6.4 Supabase storage parity

The supabase `api_keys` table mirrors the registry shape exactly
(`graph_id`, `scopes` jsonb FLAT, `delegation_depth`, `created_by_key_id`,
+ the `chk_minted_key_no_escalation` CHECK + `idx_api_keys_graph_id`).
The C1 migration is pure-additive and drops cleanly (rollback drill in the
C8 runbook — apply → rollback → re-apply passes in CI).
