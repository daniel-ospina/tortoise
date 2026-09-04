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

## 6. The machine-credential model: unified scoped keys (epic #2083)

Epic #2083 (multi-graph tenancy, shipped 2026-09-04) replaced the implicit
"one key = full team access" model with **one unified key table carrying a
graph scope + a flat scope allowlist** — no new credential type, no key
rotation, no forced migration (E2E-5).

### 6.1 What an API key now IS

| Column | Meaning |
|---|---|
| `graph_id` | NULL = **team-wide key** → resolves to the team's DEFAULT graph (the pre-epic behavior — legacy keys untouched); set = bound to ONE custom graph |
| `scopes` | FLAT allowlist array from `{graphs:read, graphs:write, team:manage}` (plus the escalation set `graphs:create/delete`, `keys:manage` for owner mints) |
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
  `graphs:read` by default and `keys:manage` children cannot mint further
  keys — the DB CHECK `chk_minted_key_no_escalation` is the invariant.
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
