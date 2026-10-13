---
title: "Consent identity bridge — the token contract, the four session-flow properties, and what the mint hop costs (#3524)"
type: engineering
domain: platform
doc_status: live
created: 2026-10-11
subjects.team: epistemic-team
aboutSubjects: mcp-oauth-consent, consent-token, session-flow-properties, supabase-redirect-allowlist
aboutObjects: tortoise-oauth-as, bff-session, auth-re-architecture-3501, issue-3524
---

# Consent identity bridge — the token contract

**Issue:** [#3524](https://github.com/daniel-ospina/tortoise/issues/3524) (child of #3501, `P0-blocker`) ·
**Canonical code:** `tortoise/oauth.py` (`_CONSENT_HTML`), `tortoise/hosted_api.py` (`/oauth/authorize`,
`/oauth/consent`, `/oauth/consent/preview`), `website/apps/dashboard/functions/_shared/auth/*.ts` (the BFF) ·
**Sibling:** `docs/engineering/auth/d1-session-store.md` (#3522) ·
**Guards:** `tests/test_oauth_consent_bridge.py`

**This document specifies the consent token before it is implemented.** #3524's own task 1 says the token
"is a **new security surface** and must be specified, not assumed". That is the mandate this doc discharges:
the contract, why each field exists, the attack the contract *adds*, and the verification results for the
issue's tasks 2, 4 and 5.

> **Provenance — re-measured, because the issue body is stale.** #3524's line anchors do not resolve
> (`oauth.py:1372,1592`, `hosted_api.py:23409`, `hosted_api.py:23284-23292` are unrelated code today), and
> its `flow-map.md` F14 citation lives in a repo this one cannot open. The authoritative statements are
> **`SCOPE.md` §4 W3, §9 class 4 and §10** (private `daniel-ospina/premise-labs`,
> `engineering/auth/SCOPE.md`), which post-date the issue body and which #3501's own scope comment names as
> the single source of truth. §9 class 4 is the part the issue body does **not** carry:
> **two mitigations are required TOGETHER, and both are assigned to #3524.**

---

## 1. What has to move, and why nothing smaller works

Today the consent page is the **only** auth flow that works, and it works by an inline supabase-js client
that holds a **JS-readable, parent-domain** session cookie (`sb-tortoise-auth-token`, written at
`oauth.py:2215`, domain literal at `oauth.py:2053`). Measured state:

| Fact | Evidence |
|---|---|
| The page mints session material client-side | `supabaseClient.auth.signInWithPassword` / `signInWithOAuth`, `oauth.py:2416-2596` |
| The page reads the session back out of the cookie | `storage: cookieStorage, storageKey: COOKIE_NAME`, `oauth.py:2234-2235` |
| The page POSTs the raw `access_token` as a bearer | `/oauth/consent` `oauth.py:2614-2621`; `/oauth/consent/preview` `oauth.py:2292-2298` |
| The AS accepts **only** that bearer — it has no other identity source | `verify_session_jwt(request)` (`session_auth.py:588-643`) reads `request.headers.get("Authorization", "")` (`:600`) and nothing else |
| `/oauth/authorize` carries **no** identity | `hosted_api.py:34756-34757` — the authorize handler reads query params only |

Under the BFF the browser holds one credential it cannot read: a host-only `__Host-session` cookie on
`app.premiselabs.co`. It is **not sent to `api.premiselabs.co`** (host-only by definition, `SCOPE.md` §4 W6
proved it by observation) and **cannot be read by JS anywhere** (HttpOnly). So the consent page cannot
authenticate itself under the target topology, in any candidate design. Identity must therefore be asserted
**about** the page's visitor by a party the AS trusts, and the only such party is the session origin.

Two shapes exist; `SCOPE.md` §9 class 4 requires the first:

| Shape | Why not / why |
|---|---|
| **Relocate the consent UI to `app.*`, POST server-side** | Removes the problem by deleting the page's need for identity, but it changes the origin the `authorization_endpoint` lands on. #3524's body withdrew the *necessity* claim for relocating (the P0 was over-entailed) and `SCOPE.md` §1.6 scopes this issue to the consent surface's **session dependency**, not a page move. Recorded as the fallback if the token contract is rejected. |
| **Mint a signed, bound, single-use consent token on `app.*`** | **This spec.** The page stays in Python on `api.*`; the AS keeps its same-origin `authorization_endpoint`; identity arrives as an assertion the AS verifies locally. `SCOPE.md` §9 class 4(ii). |

**Nothing smaller is sound.** A registry-free "proxy the consent POST through the BFF" shape would move the
session off the page but leaves the consent decision unbound to the flow it approves, so `SCOPE.md` §9
class 4(i)+(ii) would still be open — and it would widen the BFF by a credentialed CORS surface for exactly
the same amount of new boundary. The token is the narrower surface because it is *verifiable offline,
bound, single-use and short-lived* rather than "the session origin can be driven by the AS origin".

## 2. The token contract (task 1)

### 2.1 Shape

An **HMAC-SHA256 signed, compact-JWS-shaped** token (`header.payload.signature`, `alg: HS256`, base64url,
no padding). It is **not** a Supabase JWT and must carry a distinct `aud` so it can never be confused with
one.

```text
header  : {"alg":"HS256","typ":"JWT","kid":"consent-1"}          # kid allows future rotation
payload : {
  "iss": "https://app.premiselabs.co",   # the session origin — the minting authority
  "aud": "tortoise-consent",             # NOT "authenticated" (a Supabase audience)
  "iat", "exp",                          # exp = iat + CONSENT_TOKEN_TTL_S
  "jti": "<32 random bytes, hex>",       # single-use nonce
  "sub": "<supabase user_id>",           # the identity being asserted
  "email": "<user email>",               # display only; never an authorization input
  "bind": {                              # the flow this token is good for — and ONLY this flow
    "client_id", "redirect_uri", "code_challenge", "state"
  }
}
```

### 2.2 Why each field exists — and what fails without it

| Field | Rule | Failure if absent |
|---|---|---|
| `bind.client_id` | must equal the `client_id` presented at redemption | a token minted while approving client A redeems into an approval for client B |
| `bind.redirect_uri` | must equal the `redirect_uri` presented at redemption (after the same `_redirect_uri_matches` normalisation the AS already applies) | **the code is redirected to an attacker origin** — the classic mix-up |
| `bind.code_challenge` | must equal the `code_challenge` presented at redemption | the attacker's own verifier completes a flow the victim approved, i.e. the authorization code is not bound to the client that asked for it |
| `bind.state` | must equal the `state` presented at redemption | cross-flow substitution |
| `jti` | recorded on first redemption; a second redemption of the same `jti` is refused | replay: one consent yields N authorization codes |
| `exp` | `CONSENT_TOKEN_TTL_S` (proposed **120 s**) from `iat`; verified with ≤30 s leeway | a leaked token stays live indefinitely |
| `sub` | the asserted identity — the ONLY thing the token is allowed to establish | the AS has no identity input at all |
| `iss` | must equal the configured session origin | a token minted by a look-alike origin is accepted |
| `aud` | must equal `tortoise-consent` | a Supabase session token (or any other JWT signed with a shared key) is accepted as a consent assertion |

**TTL is not a substitute for `jti`.** Single-use bounds *replay*; the TTL bounds *exposure*. Both are
required, and neither substitutes for `bind`.

### 2.3 The mint hop, and what "single-use" costs

**Mint site:** a new BFF route on the session origin, `GET /api/consent/token?<authorize params>`
(`website/apps/dashboard/functions/api/consent/[[path]].ts`), which:

1. enforces an **exact-origin CORS allowlist of the AS origin** with `Access-Control-Allow-Credentials:
   true` — the allowlist is what stops a cross-site page from driving the mint with the victim's cookie;
2. reads `__Host-session` and resolves the credential with `getAccessTokenForSession` (`token.ts:113`);
3. answers **401 `no_session`** when there is no session and **503 `unavailable`** when the store is
   unreachable — never a sign-out (property 3, below);
4. mints the token (§2.1) and returns `{token, exp}`. The session's access token never leaves the server.

**The `jti` store.** Single-use needs shared state, because the AS is multi-instance. Options, in order of
preference: **(a)** a `consent_tickets` table in the existing D1 `SESSIONS` database (the minting side is
already there and the BFF already bootstraps its own schema idempotently — `ensureSchema`,
`ensureSchemaTokenColumns`), redeemed by the AS through a **server-to-server** call back to the session
origin; or **(b)** a row in the control plane (a Supabase migration) consumed by the AS directly. **(a) is
preferred:** the minting authority owns the nonce, so the AS never holds a second copy of the secret's
state, and no new secret is needed for the redemption leg at all — the AS's redemption call to `app.*` is
authenticated the same way every other AS→app.* call is. This is the one open *mechanism* choice in this
spec, and it is deliberately recorded rather than assumed.

**Signing key.** `CONSENT_TOKEN_SECRET` — a **dedicated** secret, provisioned on both platforms and
declared in `config/required-bindings.yml` so the deploy preflight fails closed until it exists. It must
**not** be the service-role key (key reuse across purposes) and must **not** be the anon key (the consent
page embeds it in HTML, so it is public). Rotation is `kid`-scoped; two `kid`s are accepted during a
rotation window.

### 2.4 Consent fixation — the attack this contract **adds**, and which half defeats it

**The added attack.** Before this bridge, the only way to be the subject of a consent decision was to hold a
live session cookie on the *AS* origin, and the decision was made by the browser that held it. After it,
the AS will accept an identity assertion. An attacker who can make a victim's browser deliver an assertion
into a flow of the attacker's choosing fixes the victim's identity onto the attacker's client:

```text
attacker → sends victim   https://api.premiselabs.co/oauth/authorize?client_id=ATTACKER
                              &redirect_uri=attacker.example&code_challenge=<attacker's>&state=<attacker's>
victim   → page mints a token bound to exactly those values (the victim's own session)
victim   → "Approve" → an authorization code is issued, bound to the attacker's PKCE challenge
attacker → redeems it with the verifier it kept
```

Per `SCOPE.md` §9: **"Single-use + short TTL bounds *replay*, not *fixation*. An attacker's own live link
still fixes the victim. Only the binding addresses that."** So both mitigations are required together, and
the split is:

| Mitigation | What it stops |
|---|---|
| **(ii) the binding above** | the attacker cannot *reuse* an assertion across flows: an assertion minted in flow X is inert in flow Y, so a stolen or replayed token cannot be steered into the attacker's `code_challenge`/`state`. This is the only half that bounds replay **and** cross-flow substitution. |
| **(i) attested client identity — OPEN** | the remaining half: the *human* decision. Fixation survives (ii) whenever the victim is induced to visit the attacker's authorize URL *fresh*, so the victim must be able to tell **who** they are approving. Today the page renders `client_name` **from the client's own metadata document**: DCR lets an attacker register `client_name: "Claude"` (`register_client`, `oauth.py:799`) and the page prints it verbatim (`oauth.py:2360-2361`) — *"Claude wants to access your Tortoise MCP surface."* That is the phishing surface. Requires an allowlist/attestation of client identity before the page asserts any name. **Filed as [#8218](https://github.com/daniel-ospina/tortoise/issues/8218)** — `SCOPE.md` §9 class 4(i) is explicitly assigned to #3524 but is absent from the issue body, so a separate ticket is what keeps it tracked. |

**Not mitigated by design, recorded plainly:** an attacker who can run script on the AS origin can drive the
mint route. That is not new authority — that origin already serves the page that approves consents — but the
bridge does mean an AS-origin XSS now also reaches the session origin's BFF. **This is the price of the
recorded decision to keep the page on `api.*`, and it is why the CORS allowlist must be an exact origin
match, not a suffix rule.**

## 3. The four properties (task 3) — confirmed anchors, and where each lives now

`SCOPE.md` §4 W3 asked for the four line anchors to be **confirmed, not re-anchored**, because the
consolidation dropped their citation block. Confirmed against today's code:

| # | Property (`SCOPE.md` §4 W3) | Authoritative anchor (CONFIRMED) | Server-side equivalent that exists today | Gap |
|---|---|---|---|---|
| 1 | **single-flight** — one in-flight auth attempt per flow, the others await it | `oauth.py:2323` (`let previewInFlight`), `oauth.py:2425` (`if (previewInFlight) return previewInFlight;`), `oauth.py:2447` | `token.ts` — the module-level `inFlightRefreshes` map keyed by handle: concurrent callers for one handle share ONE provider refresh and all await it (same-isolate single-flight); the CAS `… AND refresh_token = ?5` is the cross-isolate half, and its loser **re-reads the winner's token** instead of clobbering it | none |
| 2 | **refresh-once** — a refresh must not be issued twice concurrently | `oauth.py:2325` (`staleRefreshes`), `:2428`, `:2433-2441` (≤1 per stale cycle) | `token.ts::invalidateCachedToken` (`cooldownMs = 30_000`) bounds invalidations to one per window; the 60 s `REFRESH_SKEW_MS` cache keeps the hot path off the provider; and `inFlightRefreshes` dedupes concurrent refreshes (property 1) | none — **load-bearing assumption:** two racing ISOLATES are safe only because GoTrue tolerates ~10 s of refresh-token reuse. The in-flight map is per isolate and the CAS makes the write safe, but neither can un-spend a token another isolate has already presented. |
| 3 | **never-sign-out** — a transient failure must never clear the session (the #3485 class) | `oauth.py:2332` (*"NEVER call the auth logout API here"*; function at `:2331`), `:2445` (`showExpiredSignin`, never a refresh-token revoke) | `token.ts:126-142` — `no_session` (401) and `unavailable` (503) kept distinct (`:128`, `:131`, `:142`); `token.ts:154-158` — a `403`/`404`/`400` from the provider is **infrastructure**, not a dead session (`isRefreshTokenDead(refreshed.status, refreshed.error)`) | none |
| 4 | **auto-advance** — progress the flow without requiring a manual click | `oauth.py:2832-2841` (`onAuthStateChange` `INITIAL_SESSION`/`SIGNED_IN` → `runConsentFlow`), with the in-flight guard held across the stale cycle (`:2436-2441`) | **implemented, not yet routed** — `decideConsentAdvance` (`website/apps/dashboard/functions/_shared/auth/consent-flow.ts`) | the bridge replaces the event with a deterministic state: one credentialed mint call decides *signed-in* vs *go to `/auth?next=…`*, and the return to the consent URL **is** the advance. There is no session-change event to wait on, so "the user must not have to reload" becomes a property of the redirect chain, not of a listener. **Gap (recorded): the decision is implemented and behaviourally tested; the route that calls it is §9 step 3 and is NOT built, because §9 orders steps 2→4 to land together.** |

**Consequence for the implementation.** Properties 1–3 are already implemented server-side by the BFF
(#3522/#4054) — the bridge's job for them is to *route the consent page onto that path* and delete the
inline client that re-implements them. Property 4 is genuine new work, and it is the reason the page cannot
be left alone. Its decision now exists as `decideConsentAdvance` in `consent-flow.ts` (pure, no I/O), and all
four server-side guarantees are pinned BEHAVIOURALLY — each red when the property is deleted — by
`tests/test_oauth_session_flow_properties.py`; `tests/test_oauth_consent_bridge.py` keeps the static anchors.

## 4. Task 4 — the `app.*` mint hop, costed

The mint requires the browser to reach the session origin even though the consent page stays on `api.*`.

| Cost | Detail |
|---|---|
| **Cross-origin credentialed call** | `api.* → app.*` is same-**site** (`premiselabs.co`), so `SameSite=Lax` on `__Host-session` **is** sent. This is not a parent-domain cookie and does not reinstate `Domain=.premiselabs.co`. It does require CORS with credentials on the mint route, allowlisted to the AS origin exactly. |
| **A new CORS surface on the session BFF** | The BFF carries **no** `Access-Control-Allow-Origin` today (deliberate). The mint route is the first exception, and it must stay a *mint-only* route: never general-purpose, never the token endpoint (`/oauth/token` stays on `api.*`, unreachable through it). |
| **Sign-in hop** | An unauthenticated visitor needs a navigation to `app.*/auth?next=…` and back. The return target must be a **same-origin path** on `app.*` (`safeNext` resolves and compares origins, `session.ts:332-352`), so the return is a BFF route (e.g. `/api/consent/start`) that 302s back to the authorize URL rebuilt from the validated params — never a raw cross-origin `next=`. |
| **Latency** | one extra round trip per consent screen; the consent interaction is out-of-band of the token hot path, so this is not on any p95 that matters. |
| **Failure domains** | +1 (the mint route). The `no_session`/`unavailable` split (property 3) is what keeps that addition from becoming a sign-out. |
| **Loop risk** | the navigation must be marked as already-attempted so a session that cannot be established reaches a **terminal, actionable** state instead of bouncing forever — the #3485/#3503 class. The carrier is a NAMED query parameter, `consent_retry=1`, placed on the sign-in hop's return target (`auth_url`'s `next`) by the `sign_in` redirect; the route reads it on return and passes `attempted = true` to `decideConsentAdvance` (`consent-flow.ts::CONSENT_RETRY_PARAM`). It is deliberately NOT `stale=1` (`welcome.ts`, `admin/[[path]].ts`), which marks an out-of-date snapshot and means something different. The existing page already carries the copy and the retry affordance for this (`#5734`), and it must be carried over, not re-derived. |

## 5. Task 2 — RFC 8414 `authorization_endpoint` stability: VERIFIED

`authorization_server_metadata(base)` (`oauth.py:1895-1918`) emits `"authorization_endpoint": base +
"/oauth/authorize"`, where `base = _oauth_base(request) = str(request.base_url).rstrip("/")`
(`hosted_api.py:34675-34678`), served at both `/.well-known/oauth-authorization-server` and
`/.well-known/oauth-authorization-server/mcp` (`hosted_api.py:34748-34749`).

- **Same-origin:** the endpoint is built from the *serving* origin, so it is never cross-origin to the
  issuer — and it cannot become so, because both derive from the same `base`.
- **Not inert:** `GET /oauth/authorize` (`hosted_api.py:34756-34757`) is a live handler that validates the client
  and the redirect URI before rendering (`validate_authorize_params`), and it is the URL clients actually
  discover and use.
- **Unchanged by this issue:** the bridge leaves the page's *location* alone, so `authorization_endpoint`
  keeps its exact value (`https://api.premiselabs.co/oauth/authorize`). This is the concrete reason the
  token shape is preferred over the relocate-UI shape, which would have made the endpoint a cross-origin
  redirect target.
- **Guard:** `tests/test_oauth_consent_bridge.py::test_rfc8414_authorization_endpoint_stays_same_origin`
  pins the same-origin, exact-path and trailing-slash cases, plus that both well-known routes still
  carry their decorator, so a future `base` change cannot silently cross the origins.

## 6. Task 5 — sign-in fallback origin: **NOT allowlisted, and the record is what is wrong**

The consent page's provider sign-in passes `redirectTo: authorizeReturnTo()` (`oauth.py:2582`), which is
`window.location.origin + "/oauth/authorize" + <sanitised search>` — i.e. **`https://api.premiselabs.co/
oauth/authorize?…`**. GoTrue refuses a `redirect_to` it does not recognise, and Supabase matches
allowlist entries by string (wildcards only where placed).

`docs/ops/auth-email-flows.md` §3 is this repo's record of the required entries and calls them
"**Required entries**" — and it does **not** contain any `api.premiselabs.co` row:

| Entry (recorded) |
|---|
| `https://app.premiselabs.co/auth/confirm` |
| `https://app.premiselabs.co/auth/callback` |
| `https://app.premiselabs.co/**` |
| `https://tortoise.premiselabs.co` |
| `https://tortoise.premiselabs.co/welcome.html` |

**So one of two things is true, and neither is recorded:** either the project's Additional Redirect URLs
carry `https://api.premiselabs.co/**` and the doc's "required entries" table is incomplete (a false
completeness claim), or they do not, in which case **GitHub/Google sign-in on the consent page is refused
by GoTrue today** — the only auth flow that works would be the one whose provider leg is broken. This
cannot be settled from the repository; it needs the dashboard or the Management API (§8 checklist).

**Recorded fix (this PR):** the missing row is added to `docs/ops/auth-email-flows.md` §3 with its
checklist item, and `tests/test_oauth_consent_bridge.py::test_the_redirect_allowlist_record_covers_the_consent_page`
fails if the consent page's sign-in target is ever again absent from that table. The bridge itself
**removes** the dependency: under the bridge, provider sign-in happens on `app.*` (`/auth`), which is
already covered by `https://app.premiselabs.co/**`, so the `api.*` entry becomes unnecessary rather than
required. That is a second, independent reason the bridge is the right shape.

## 7. Deletion set + ordering guard

`SCOPE.md` §7 lists `oauth.py:2080-2291` (the inline adapter family) as one of **6 files across 4
families** — and carries the guard: **do NOT delete it before #3524 ships.** The replacement *is* this
bridge plus the page rewrite. Deleted by this issue when the bridge lands (not by this doc):

| Item | Where |
|---|---|
| the inline `cookieStorage` adapter + the aux-store probe chain | `oauth.py:2133-2291` |
| `COOKIE_NAME`/`COOKIE_DOMAIN`/`COOKIE_PATH`/`SIZE_GUARD`/`SIZE_CAP` and both `document.cookie` writers | `oauth.py:2037-2060` (`:2037`, `:2052`, `:2053`, `:2054`, `:2060`), `:2215`, `:2225` |
| the supabase-js CDN `<script>` + `createClient` + `flowType: "pkce"` + `detectSessionInUrl` | `oauth.py:2011-2013`, `:2232-2242` |
| `pkceIncapable`/`CAPABILITY`/`renderCapability`, the provider + email sign-in handlers, `showTerminalFallback`, `sanitiseUrl`, `loadParams`/`LOAD_TRANSIENT` | `oauth.py:2437-2841` |
| `onAuthStateChange` (property 4's anchor) | `oauth.py:2832` |

**`SCOPE.md` §10 item:** `oauth.py:2053` hardcodes `COOKIE_DOMAIN = ".premiselabs.co"`, and Gate 4 required
that scope to be **configuration, not a literal**, so the Python surface and the BFF cannot disagree about
cookie scope. The bridge deletes the literal outright, which discharges the item in full — parameterising a
cookie that this issue removes would be churn against a doomed line. **Recorded as a decision, not an
oversight.**

## 8. Operator verification (live — cannot be run from this repo)

- [ ] Additional Redirect URLs contain **`https://api.premiselabs.co/**`** *or* the bridge has shipped and
      provider sign-in no longer leaves the app origin (§6). Record the answer on #3524.
- [ ] `CONSENT_TOKEN_SECRET` exists on **both** the `tortoise-dashboard` Pages project and the hosted API,
      and `config/required-bindings.yml` declares it (the deploy preflight then fails closed until it does).
- [ ] `docs/ops/auth-email-flows.md` §3's table is complete against the live allowlist (it claims to be the
      required set).

## 9. What remains, and why it is not in this change

This document and `tests/test_oauth_consent_bridge.py` deliver tasks **1, 2, 4 and 5** of #3524: the token
is specified rather than assumed, `authorization_endpoint` stability is verified and guarded, the mint hop
is costed, and the sign-in fallback origin is settled with its record corrected. Task 3's *implementation*
(the bridge + the page rewrite) is **not** delivered here, and should not be attempted as one blind pass:

| Remaining step | Why it is its own unit |
|---|---|
| 1. The `jti` store + `CONSENT_TOKEN_SECRET` provisioning + the manifest declaration | infrastructure, outside a code diff; the mechanism choice is §2.3 |
| 2. `POST /oauth/consent` accepting a consent token alongside the bearer (verify `aud`/`iss`/`exp`/`bind`/`jti`, then `issue_auth_code` with the *bound* params) | the AS half; testable in Python (`tests/test_oauth_*`), and it must land **before** any page change so the bearer path stays live meanwhile |
| 3. The `/api/consent/token` mint route + the `/api/consent/start` sign-in bounce | the BFF half; static-gated only (the repo's existing status for all BFF code) |
| 4. The page rewrite (delete §7's rows, keep the org picker + the #5734 refusal copy, add the mint call and the terminal-loop guard) | replaces `tests/test_oauth_consent_pkce.py` wholesale — its 22 invariants have no subject once the inline client is gone, so this is a **test-surface migration**, not a patch |
| 5. `SCOPE.md` §9 class 4(i) — attested client identity | a product decision (which clients are attested, and what an unattested one may claim), filed separately |

Steps 2→4 must land together or not at all: a page that half-reads a token and half-reads the old cookie is
the "strictly worse than not starting" state #3559 recorded for `main.jsx`. Step 2 alone is safe (additive,
bearer path untouched); step 4 is where the flow changes.

## 10. Integration docs

| Item | Value |
|---|---|
| **Mint authority** | `app.premiselabs.co` — the single session-bearing origin (`SCOPE.md` §3). |
| **Signing** | HMAC-SHA256, dedicated `CONSENT_TOKEN_SECRET`, `kid`-scoped rotation. Never the service-role key; never the anon key (public: the page embeds it). |
| **TTL / replay** | `exp = iat + 120 s` (leeway ≤30 s) **and** a single-use `jti`; neither substitutes for the `bind` set. |
| **Redemption** | `POST /oauth/consent` (`hosted_api.py:34888`) — currently `verify_session_jwt` only, `session_auth.py:588`. |
| **Endpoint stability** | `authorization_server_metadata` (`oauth.py:1895`); `_oauth_base(request)` (`hosted_api.py:34675`). Guarded. |
| **CORS** | new, exact-origin to the AS origin, on the mint route only. The BFF carries none today; `/oauth/token` must never become reachable through it (`SCOPE.md` §9 class 3). |
| **Bindings to declare** | `CONSENT_TOKEN_SECRET` (`config/required-bindings.yml`, `tortoise-dashboard`), checked pre-deploy by `tools/check_pages_bindings.py` (the #3616 gate). |
| **Verified by execution** | `tests/test_oauth_consent_bridge.py` (RFC 8414 stability + the allowlist record). |
| **Vendor docs read at** | Supabase Auth redirect allowlist, string-match with placed wildcards (`docs/ops/auth-email-flows.md` §3, 2026-09-22). |

## Still open

- **§9 class 4(i) — attested client identity — [#8218](https://github.com/daniel-ospina/tortoise/issues/8218).**
  `SCOPE.md` lists the class as **OPEN** and requires both mitigations together; (ii) is specified here, (i)
  is filed and unbuilt. **The consent screen can still be made to say "Claude" by anyone who registers that
  name.**
- **The `jti` store mechanism** (§2.3) — preferred shape recorded; not chosen by an owner.
- **#3529** — 4 of the 7 declared threat classes still have no test (the consent token adds an 8th surface
  with no adversarial test yet, because it does not exist yet).
