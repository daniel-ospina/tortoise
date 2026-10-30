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
> (`oauth.py:1425,1592`, `hosted_api.py:23409`, `hosted_api.py:23284-23292` are unrelated code today), and
> its `flow-map.md` F14 citation lives in a repo this one cannot open. The authoritative statements are
> **`SCOPE.md` §4 W3, §9 class 4 and §10** (private `daniel-ospina/premise-labs`,
> `engineering/auth/SCOPE.md`), which post-date the issue body and which #3501's own scope comment names as
> the single source of truth. §9 class 4 is the part the issue body does **not** carry:
> **two mitigations are required TOGETHER, and both are assigned to #3524.**

---

## 1. What has to move, and why nothing smaller works

> ⚠️ **Measured state — BEFORE §9 step 4, which DELETED the page this paragraph describes.**
> The `oauth.py:<n>` anchors below are the pre-step-4 line numbers and **do not resolve at head**
> (`oauth.py` is ~2.8k lines now, and the deleted block is gone) — they are kept because the
> measurement is the evidence for the rewrite, not because a reader can follow them. At head:
> `grep -c sb-tortoise tortoise/oauth.py` = **0**.

Today, before step 4, the consent page is the **only** auth flow that works, and it works by an inline
supabase-js client that holds a **JS-readable, parent-domain** session cookie (`sb-tortoise-auth-token`,
written at `oauth.py:2444`, domain literal at `oauth.py:2282`). Measured state (pre-step-4):

| Fact | Evidence |
|---|---|
| The page mints session material client-side | `supabaseClient.auth.signInWithPassword` / `signInWithOAuth`, `oauth.py:2664-2844` |
| The page reads the session back out of the cookie | `storage: cookieStorage, storageKey: COOKIE_NAME`, `oauth.py:2463-2464` |
| The page POSTs the raw `access_token` as a bearer | `/oauth/consent` `oauth.py:2862-2869`; `/oauth/consent/preview` `oauth.py:2540-2546` |
| The AS accepts **only** that bearer — it has no other identity source | `verify_session_jwt(request)` (`session_auth.py:588-643`) reads `request.headers.get("Authorization", "")` (`:600`) and nothing else. ⚠️ Measured BEFORE §9 step 2: `POST /oauth/consent` also accepts a bound consent token now — see §10 *Redemption*. **§9 step 4 gave `/oauth/consent/preview` the same second arm** (`X-Consent-Token`, §7), because **§9 step 4 DELETED the page's bearer arm** (the page holds no credential), so the token is the only source the page can use |
| `/oauth/authorize` carries **no** identity | `GET /oauth/authorize` (`@app.get("/oauth/authorize")` in `hosted_api.py`) — the authorize handler reads query params only |

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
| `jti` | recorded on first redemption; a second redemption of the same `jti` is refused | replay of ONE TOKEN: a leaked assertion cannot yield N authorization codes. ⚠️ It does not bound the APPROVAL — the mint does not consume the attempt, and step 2 costs a re-mint rather than a re-login after a post-spend refusal, so one approval can yield one code per mint call. Closing that needs a single-use mark on the BFF's attempt row (a step-2/step-3 contract change), and `test_the_attempt_can_be_MINTED_AGAIN_for_step_4s_recovery` pins the current behaviour both ways. |
| `exp` | `CONSENT_TOKEN_TTL_S` (proposed **120 s**) from `iat`; verified with ≤30 s leeway | a leaked token stays live indefinitely |
| `sub` | the asserted identity — the ONLY thing the token is allowed to establish | the AS has no identity input at all |
| `iss` | must equal the configured session origin | a token minted by a look-alike origin is accepted |
| `aud` | must equal `tortoise-consent` | a Supabase session token (or any other JWT signed with a shared key) is accepted as a consent assertion |

**TTL is not a substitute for `jti`.** Single-use bounds *replay*; the TTL bounds *exposure*. Both are
required, and neither substitutes for `bind`.

### 2.3 The mint hop, and what "single-use" costs

**Mint site (DELIVERED — this is the shipped contract, not a sketch):** `POST /api/consent/token`
(`website/apps/dashboard/functions/api/consent/token.ts`), JSON body `{state, attempt_key}`, which:

1. enforces an **exact-origin CORS allowlist of the AS origin** (`originVerdict`: the request's
   `Origin`, parsed, must equal the configured AS origin — an ABSENT `Origin` is refused too) with
   `Access-Control-Allow-Credentials: true`, and a refused origin answers **403 `forbidden_origin`**
   with no CORS headers. A binding that does not parse as an absolute http(s) origin is a DIFFERENT
   event and answers **503 `{error: "unavailable", reason: "as_origin_misconfigured"}`** plus one
   `console.error` naming `CONSENT_AS_ORIGIN` — the same treatment `start.ts::parseAsOrigin` gives
   that knob, so an operator mistake is never reported as a rejected caller. That allowlist is what stops a cross-site page from driving the mint with the
   victim's cookie. A request that DOES carry the AS origin must then send
   `Content-Type: application/json`, or it is refused **415 `unsupported_media_type`** — the media-type
   gate is the second layer, so a cross-site form post is answered `403` by the allowlist first;
2. reads `__Host-session` and resolves the credential with `getAccessTokenForSession` (`token.ts:134`);
3. answers **401 `no_session`** when there is no session, **403 `no_authorize_attempt`** when the flow is
   not this caller's, and **503 `unavailable`** when the store is unreachable — never a sign-out
   (property 3, below);
4. takes **`bind` from the RECORDED attempt row**, never from the request: the caller names a `state`, and
   `client_id`/`redirect_uri`/`code_challenge`/`state` come out of `readConsentAttempt`. Passing bind params
   in the request is ignored on purpose — honouring them would make this route a sign-any-bind oracle, which
   is §2.4's fixation attack;
5. requires the **per-attempt key** (`attempt_key`, below) — without it an ownerless row is mintable by
   anyone who names the `state`;
6. mints the token (§2.1) and returns `{token, exp}`. The session's access token never leaves the server.

⚠️ **Step 2 bounds the credential by the PROVIDER token's lifetime; it does NOT revalidate the grant on every mint.**
`getAccessTokenForSession` answers from D1 while the provider-issued access token has more than the 60 s
`REFRESH_SKEW_MS` left, and calls the provider only past that point — the same cache §3 property 2 records as
deliberate ("the 60 s `REFRESH_SKEW_MS` cache keeps the hot path off the provider"). So what this route
actually guarantees is *the session's provider-issued credential was live at its last refresh*, not *the grant
is live right now*: a provider-side revocation (grant revoked, user deleted) is detected at the **next refresh**,
at most one access-token lifetime later, and not at the mint. Re-minting on load — step 4's documented recovery
— is exactly the cached path. Revalidating per mint would be a change of behaviour (an extra provider round trip
on the consent path, with its own failure semantics) and is deliberately **not** made: the residual is recorded
here rather than implied to be closed by a comment claiming more than the code does.

⚠️ It is a `POST`, not the `GET` this section first sketched, and the guard is the route's OWN: the
`originVerdict` allowlist above plus the JSON media type. ⛔ It must NOT use `csrf.ts`'s
`guardStateChangingRequest`: that helper's origin layer is `guardOrigin`, which refuses anything that is not
`appOrigin(env)` — so adding it here would answer `403 forbidden_origin` to every legitimate `api.*` call
and kill the consent arm. The route both returns `{token, exp}` and never takes a bind from the caller — do
not "restore" the earlier sketch, and do not "align" this route with `start.ts` by importing that guard.

⚠️ **The mint does NOT consume the attempt**, so one approval can produce one code per call — that is
deliberate (step 2 costs a re-mint rather than a re-login after a post-spend refusal) and it is pinned by
`test_the_attempt_can_be_MINTED_AGAIN_for_step_4s_recovery`. `jti` bounds a TOKEN, not an approval.

**The attempt row, and why `user_id` alone cannot make a flow private.** `consent_attempts` (keyed by
`state`) is how the mint knows which flow a token is for, and `user_id` is what lets it refuse a flow a
different identity began. That column is NOT sufficient on its own, and the first two attempts at it were
both bypassable, which is worth recording because each looked complete:

- **last-writer-wins** (`COALESCE(excluded, existing)`) let a second signed-in identity overwrite a
  recorded owner with one `GET`;
- **first-known-owner-wins** (`COALESCE(existing, excluded)`) protects an OWNED row — but the
  **ownerless** window is the one the cold browser creates on every entry, so the first signed-in
  identity to name the `state` simply BECAME the owner; and because an ownerless row minted for anyone,
  gating the fill alone re-opened the hole through the mint.

The delivered rule is that **the flow belongs to a browser, not to whoever knows the `state`**:
`/api/consent/start` mints a per-attempt key on the entry leg (32 random bytes), stores **only its
SHA-256** on the row, and carries the raw key in the `selfPath` **return target**; the owner fill and the
mint both require it. The key is INSERT-ONLY — written only when the row is created, never on a
re-entry, so a later caller can never rotate it, because rotation *is* the takeover. A row that predates
the column (its hash is `NULL`) is therefore **dead, never adoptable**: COALESCE-ing a key into it let the
first caller to name the `state` own the flow while the mint read the same row as nobody's — the two legs
disagreeing about one row — so such an attempt is refused and the visitor restarts. A missing, wrong, or
pre-column key answers the SAME `403 no_authorize_attempt` as an unrecorded flow, so the route is not an
oracle for guessing a `state`.

What a DEAD (pre-column) row actually does, stated precisely because "the visitor restarts" is not what the
code shows on the cold leg: the entry leg still answers `302` to `/auth` (it has no owner to compare yet and
its return target carries no key), and the refusal arrives AFTER sign-in as `403 no_authorize_attempt /
flow_not_yours`. Because a keyless re-entry no longer refreshes `expires_at`, that row dies at its original
TTL instead of shadowing every retry — and a client that reuses the same `state` for a new attempt is
reusing a per-attempt value, which an OAuth client does not do (it mints a fresh `state` per authorize
request). Reloading the SAME url does NOT recover: it is refused (`403 flow_not_yours` once signed in,
`302` then the same `403` when cold) until that row expires. Recovery is a NEW authorize request — a new
`state` from the caller.

⚠️ **Where the raw key goes** (stated, because the row's SHA-256 is the narrow claim and is easy to
misread as the whole one): the key rides the `selfPath` return target **and** `authorizeUrl` (the page is
the mint's only caller), so it passes through the AS page URL, the visitor's history, the sign-in hop's
return target — which `auth/start.ts` PERSISTS as `auth_flows.next` in this same D1 database — plus the page's GoTrue `redirectTo` flow state, WHICH WAS THE FIFTH CARRIER AND IS GONE: §9 step 4
deleted the sign-in itself, so the page has no `redirectTo` hop and no `STRIP_PARAMS`
(`grep -c 'signInWithOAuth\|redirectTo\|STRIP_PARAMS' tortoise/oauth.py` = 0). Read access to that (or those flow records) inside the
window is therefore equivalent to holding the key. The two windows are DIFFERENT and neither is the
attempt's: the persisted `auth_flows.next` copy lives `FLOW_MAX_AGE_S` (30 min), while the attempt's own
`expires_at` is `CONSENT_ATTEMPT_TTL_S` (15 min) and is refreshed ONLY by the key's holder — a foreign or
keyless re-entry does not extend it, because a renewable dead row would bound nothing. Add the request logs
of both hops (`/api/consent/start?attempt_key=…`, `/auth?next=…attempt_key=…`) to the carrier list: they
are the cheapest read path and need no database access at all. That is the accepted cost of a carrier that
is not a cookie; §4 records why no cheaper carrier proves the CHANNEL. The reduction that was available
when this was measured — stripping the param on the `redirectTo` hop — **shipped** (§9 step 4 removed that
hop), so the residual is the carriers above and nothing else.

**Recorded exemptions from that no-oracle rule** (they are deliberate, and stated here so a later reader
neither "fixes" them nor reads the sentence above as flow-wide):

- `/api/consent/start` DOES reveal whether a `state` exists — a signed-in visitor who does not hold the
  attempt's key is refused **before** the AS renders consent, rather than advanced into a flow it could
  never mint for. Refusing late would be the "approve, then the flow dies" class; a `state` is
  client-chosen and already in the visitor's own URL, so the disclosure buys an attacker nothing new.
- The *carriage* of the key is exempt in the other direction: it must reach the consent page, which §4
  costs and records.
⛔ A scoped cookie holding the nonce is **not** available here: the bridge's rule is that the two origins
are reconciled **by the token, never by a second cookie**. The return target is the only carrier that
keeps that rule.

**The `jti` store.** Single-use needs shared state, because the AS is multi-instance. Two options were
compared: **(a)** a `consent_tickets` table in the existing D1 `SESSIONS` database (the minting side is
already there and the BFF already bootstraps its own schema idempotently — `ensureSchema`,
`ensureSchemaTokenColumns`), redeemed by the AS through a **server-to-server** call back to the session
origin; or **(b)** a row in the control plane (a Supabase migration) consumed by the AS directly.
**(b) is DECIDED, and shipped** — `ControlPlaneJtiStore` (`tortoise/consent_token.py`) + migration
`20261030000001_oauth_consent_tickets.sql`, §9 step 1. **Why (b) and not (a):** the AS already holds this
seam *at the exact call site* — `hosted_api.py` passes its control-plane client (`cp`) into
`issue_auth_code`, and the whole `oauth_clients`/`oauth_codes`/`oauth_*_tokens` family lives in that
database — so (b) adds no new boundary, while (a) would add a new route on the session origin, a new
server-to-server authentication for it, and a new failure domain on the consent path. (a)'s own argument
(the minting authority owns the nonce) is real but does not survive that cost, because single-use is
bounded at **redemption** — which is where a replay arrives — so only the AS writes and the minting side
needs no write credential and no new secret at all.

**Decision status:** this paragraph previously read "**(a) is preferred** … the one open *mechanism*
choice in this spec … recorded rather than assumed". That was accurate while the choice was open; it is
not any more, and this note is the relay rather than a reversal — no owner ruling was overridden, the
comparison above is kept, and (a) is recorded as **not adopted** for the reason stated. A lane that
finds (a) named first in git history should read this paragraph, not the earlier one.

**OVERRIDES:** §2.3's earlier "(a) is preferred" ordering — a D1 `consent_tickets` table on the
session origin, redeemed over a server-to-server call — because the control plane is the seam the AS
already holds at the redemption call site (`cp` is passed into `issue_auth_code`), so (b) adds no new
route, no server-to-server authentication and no new failure domain on the consent path; relayed to
[issue #3524 (decision comment)](https://github.com/daniel-ospina/tortoise/issues/3524#issuecomment-6502978789)
— where the step table named the step but never the mechanism — and shipped in `20261030000001`.

**Signing key.** `CONSENT_TOKEN_SECRET` — a **dedicated** secret, provisioned on both platforms and
declared in `config/required-bindings.yml` so the deploy preflight fails closed until it exists. It must
**not** be the service-role key (key reuse across purposes) and must **not** be the anon key — public by
construct, independently of this page (before §9 step 4 the consent page embedded it in HTML; the page
now embeds nothing, so the reason is the key's own distribution, not a surface we later remove).
Rotation is `kid`-scoped; two `kid`s are accepted during a
rotation window. ⚠️ **That window is NOT BUILT yet** — it is the goal this paragraph states, and §9 step 1
records the accepted-`kid` allowlist as deliberately unbuilt (one key is configured, so an allowlist of one
is vacuous). Until it lands, a rotation is a VALUE SWAP that must land on both halves together and kills
in-flight assertions for their ≤120 s TTL.

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
| **(i) attested client identity — OPEN** | the remaining half: the *human* decision. Fixation survives (ii) whenever the victim is induced to visit the attacker's authorize URL *fresh*, so the victim must be able to tell **who** they are approving. Today the page renders `client_name` **from the client's own metadata document**: DCR lets an attacker register `client_name: "Claude"` (`register_client`, `oauth.py:1014`) and the page prints it verbatim (`consent_page_html`'s `#client-line` assignment — cited by SYMBOL, not by line: this doc's `oauth.py:<n>` anchors have drifted three times already, twice inside this PR) — *"Claude wants to access your Tortoise MCP surface."* That is the phishing surface. Requires an allowlist/attestation of client identity before the page asserts any name. **Filed as [#8218](https://github.com/daniel-ospina/tortoise/issues/8218)** — `SCOPE.md` §9 class 4(i) is explicitly assigned to #3524 but is absent from the issue body, so a separate ticket is what keeps it tracked. |

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
| 1 | **single-flight** — one in-flight auth attempt per flow, the others await it | **DELETED** by §9 step 4 (page gone; the server side below is now the only implementation): was `let previewInFlight` / `if (previewInFlight) return previewInFlight;` in `_CONSENT_HTML` | `token.ts` — the module-level `inFlightRefreshes` map keyed by handle: concurrent callers for one handle share ONE provider refresh and all await it (same-isolate single-flight); the CAS `… AND refresh_token = ?5` is the cross-isolate half, and its loser **re-reads the winner's token** instead of clobbering it | none |
| 2 | **refresh-once** — a refresh must not be issued twice concurrently | **DELETED** by §9 step 4: was `let staleRefreshes = 0` and its ≤1-per-cycle guard in `_CONSENT_HTML` | `token.ts::invalidateCachedToken` (`cooldownMs = 30_000`) bounds invalidations to one per window; the 60 s `REFRESH_SKEW_MS` cache keeps the hot path off the provider; and `inFlightRefreshes` dedupes concurrent refreshes (property 1) | none — **load-bearing assumption:** two racing ISOLATES are safe only because GoTrue tolerates ~10 s of refresh-token reuse. The in-flight map is per isolate and the CAS makes the write safe, but neither can un-spend a token another isolate has already presented. |
| 3 | **never-sign-out** — a transient failure must never clear the session (the #3485 class) | **DELETED** by §9 step 4: was `showExpiredSignin` and its *"NEVER call the auth logout API here"* comment in `_CONSENT_HTML` (the page calls no logout API at all now — it holds no session to lose) | `token.ts` — `getAccessTokenForSession` (`:134`) and `refreshAndPersist` (`:191`) keep `no_session` (401) and `unavailable` (503) distinct: reason returns at `:152`, `:163`, `:200`, `:206`, `:208`; `isRefreshTokenDead(refreshed.status, refreshed.error)` (`:205`) — a `403`/`404`/`400` from the provider is **infrastructure**, not a dead session | none |
| 4 | **auto-advance** — progress the flow without requiring a manual click | **DELETED** by §9 step 4: was `supabaseClient.auth.onAuthStateChange(` in `_CONSENT_HTML`. ⚠️ The page no longer HAS a session to observe, so the event could not be rebuilt even in principle — the server decision below is the replacement, not a port | **DELIVERED, routed** — `decideConsentAdvance` (`website/apps/dashboard/functions/_shared/auth/consent-flow.ts`) | the bridge replaces the event with a deterministic state: one credentialed mint call decides *signed-in* vs *go to `/auth?next=…`*, and the return to the consent URL **is** the advance. There is no session-change event to wait on, so "the user must not have to reload" becomes a property of the redirect chain, not of a listener. **DELIVERED (#3524 §9 step 3): the route that calls it is `functions/api/consent/start.ts`, and the mint it advances to is `functions/api/consent/token.ts`. Behaviourally tested end-to-end by `tests/test_oauth_consent_bff_routes.py`, including a token minted in TypeScript being accepted by the Python verifier.** |

**Consequence for the implementation — DISCHARGED.** Properties 1–3 were already implemented server-side by the
BFF (#3522/#4054), so the bridge's job for them was to *route the consent page onto that path* and delete
the inline client that re-implemented them. **§9 step 4 did exactly that**: none of the four page-side
implementations survives, and every one of the guarantees is now true of the server path only.
`decideConsentAdvance` in `consent-flow.ts` (pure, no I/O) is property 4's replacement. All four server-side
guarantees are pinned BEHAVIOURALLY — each red when the property is deleted — by
`tests/test_oauth_session_flow_properties.py`; `tests/test_oauth_consent_bridge.py` pins the BFF anchors and
asserts the page-side ones are GONE (a property that drifted back into the page would be a second identity
system again).

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
| **The attempt key transits browser-visible URLs, and one of them is PERSISTED** | §2.3 gives the flow to the BROWSER that created it, and the consent page on `api.*` is the mint's only documented caller — so the key must arrive with the page. It rides the `authorizeUrl` query (`ATTEMPT_KEY_PARAM`) **and** the sign-in hop's return target, and that return target is a value the BFF already stores: `auth/start.ts` writes it verbatim into `auth_flows.next` in the same D1 `SESSIONS` database for `FLOW_MAX_AGE_S`. A third channel follows from the page: `authorizeReturnTo()` preserves the param, so it also reaches the GoTrue `redirectTo` flow state. **Read access to `auth_flows` (or the GoTrue flow records, or the request logs of the two hops) is therefore equivalent to holding the key while those records live** — `auth_flows.next` for `FLOW_MAX_AGE_S` (30 min), and the attempt itself for `CONSENT_ATTEMPT_TTL_S` (15 min, refreshed only by the key's holder, so it is not renewable by a stranger) — an operator, a support tool or a backup is enough. **This is a recorded DECISION, not an oversight:** §2.3 forbids a second *cookie* (identity reconciles by the token) and a URL parameter is not one, and every alternative carrier is either a cookie or a server-side lookup that cannot prove the CHANNEL (a non-secret flow id in the return target would let any browser fill the ownerless row — the exact hole the key closes). The mitigation is scope, not secrecy: per-attempt, 32 random bytes, only the SHA-256 on the row, inert at expiry — and the residual is stated rather than implied. Narrowing it: **§9 step 4 removed the GoTrue channel in full** — the page no longer calls `signInWithOAuth` at all, so it has no `redirectTo` hop, no `STRIP_PARAMS` and no flow state to leave the key in. The `auth_flows.next` channel (the sign-in hop the consent bounce creates) REMAINS, and nothing in this design can remove it. |

## 5. Task 2 — RFC 8414 `authorization_endpoint` stability: VERIFIED

`authorization_server_metadata(base)` (in `tortoise/oauth.py`) emits `"authorization_endpoint": base +
"/oauth/authorize"`, where `base = _oauth_base(request) = str(request.base_url).rstrip("/")`
(`_oauth_base`, a function in `hosted_api.py`), served at both
`@app.get("/.well-known/oauth-authorization-server")` and its `/mcp` twin (both in `hosted_api.py`).

- **Same-origin:** the endpoint is built from the *serving* origin, so it is never cross-origin to the
  issuer — and it cannot become so, because both derive from the same `base`.
- **Not inert:** `GET /oauth/authorize` (`@app.get("/oauth/authorize")`) is a live handler that validates the client
  and the redirect URI before rendering (`validate_authorize_params`), and it is the URL clients actually
  discover and use.
- **Unchanged by this issue:** the bridge leaves the page's *location* alone, so `authorization_endpoint`
  keeps its exact value (`https://api.premiselabs.co/oauth/authorize`). This is the concrete reason the
  token shape is preferred over the relocate-UI shape, which would have made the endpoint a cross-origin
  redirect target.
- **Guard:** `tests/test_oauth_consent_bridge.py::test_rfc8414_authorization_endpoint_stays_same_origin`
  pins the same-origin, exact-path and trailing-slash cases, plus that both well-known routes still
  carry their decorator, so a future `base` change cannot silently cross the origins.

## 6. Task 5 — sign-in fallback origin: **DISCHARGED by §9 step 4** (was: not allowlisted)

> ✅ **DISCHARGED.** §9 step 4 deleted the page's provider sign-in, so the consent page no longer
> redirects anywhere for identity and makes **no** GoTrue `redirect_to` call at all: the origin
> question this section settles has no subject left. `api.premiselabs.co/**` is therefore a recorded
> **non-requirement**, not a missing entry — and the sibling record
> (`docs/ops/auth-email-flows.md` §3) says so in the table row it already carries. The measurement
> below is kept as the record of why the fallback was the wrong shape, and its `oauth.py:2830` anchor
> is a **pre-step-4** line number that no longer resolves (the template has been rewritten).
> The guard it names still runs: `tests/test_oauth_consent_bridge.py::
> test_the_redirect_allowlist_record_covers_the_consent_page`.

**Measured BEFORE §9 step 4.** The consent page's provider sign-in passed `redirectTo:
authorizeReturnTo()` (pre-step-4 `oauth.py:2830`), which was
`window.location.origin + "/oauth/authorize" + <sanitised search>` — i.e. **`https://api.premiselabs.co/
oauth/authorize?…`**. GoTrue refuses a `redirect_to` it does not recognise, and Supabase matches
allowlist entries by string (wildcards only where placed).

`docs/ops/auth-email-flows.md` §3 is this repo's record of the required entries and calls them
"**Required entries**" — and at the time of measurement it did **not** contain any
`api.premiselabs.co` row (this PR adds one, marked NO LONGER REQUIRED as of §9 step 4):

| Entry (recorded) |
|---|
| `https://app.premiselabs.co/auth/confirm` |
| `https://app.premiselabs.co/auth/callback` |
| `https://app.premiselabs.co/**` |
| `https://tortoise.premiselabs.co` |
| `https://tortoise.premiselabs.co/welcome.html` |

**So one of two things is true, and neither is recorded:** either the project's Additional Redirect URLs
carry `https://api.premiselabs.co/**` and the doc's "required entries" table is incomplete (a false
completeness claim), or they do not, in which case **GitHub/Google sign-in on the consent page was
refused by GoTrue** at the time of measurement — the only auth flow that works would have been the one
whose provider leg was broken. This could not be settled from the repository; it needed the dashboard
or the Management API (§8 checklist). **§9 step 4 removed the question with the sign-in itself.**

**Recorded outcome (this PR):** the row is added to `docs/ops/auth-email-flows.md` §3 **marked NO LONGER
REQUIRED as of §3524 §9 step 4** — provider sign-in now happens on `app.*` (`/auth`), covered by
`https://app.premiselabs.co/**`, and the page asks for no identity redirect at all. The guard
(`tests/test_oauth_consent_bridge.py::test_the_redirect_allowlist_record_covers_the_consent_page`) still
fails if the record stops covering the sign-in target, so the entry cannot silently disappear. That the
dependency is unnecessary rather than merely allowlisted is a second, independent reason the bridge is
the right shape.

## 7. Deletion set + ordering guard

`SCOPE.md` §7 lists the consent page's inline adapter family as one of **6 files across 4
families** — and carries the guard: **do NOT delete it before #3524 ships.** The replacement *is* this
bridge plus the page rewrite.

**⛔ DELETED by #3524 §9 step 4.** Every row below is gone from `tortoise/oauth.py`; the table is kept as
the record of *what* was deleted and *why each item was load-bearing*, because the deletion is the part a
future reader is most likely to undo by accident (each item looked like a feature).
`tests/test_oauth_consent_bridge.py::test_the_deleted_page_artefacts_are_GONE` asserts the absence, and
`tests/test_oauth_consent_page_step4.py::test_the_page_offers_and_performs_no_sign_in` asserts it on the
SERVED page.

| Item | Where it was | What it was load-bearing for |
|---|---|---|
| the inline `cookieStorage` adapter + the aux-store probe chain (`const auxStores`, `readAux`, `writeAux`) | `_CONSENT_HTML` | let the page persist a GoTrue session in a JS-readable cookie/localStorage and route the PKCE verifier off it |
| `COOKIE_NAME` / `COOKIE_DOMAIN` / `COOKIE_PATH` / `SIZE_GUARD` / `SIZE_CAP` and both `document.cookie` writers | `_CONSENT_HTML` | the parent-domain `sb-tortoise-auth-token` jar (JS-readable on every `*.premiselabs.co` sibling) |
| the supabase-js CDN `<script>` + `createClient` + `flowType: "pkce"` + `detectSessionInUrl` | `_CONSENT_HTML` | the inline auth client itself |
| `pkceIncapable` / `CAPABILITY` / `renderCapability`, the provider + email sign-in handlers, `showTerminalFallback`, `sanitiseUrl`, `loadParams` / `LOAD_TRANSIENT` | `_CONSENT_HTML` | the page's OWN sign-in flow and its #5734 refusal UX for a store it could not write |
| `onAuthStateChange` (property 4's anchor) | `_CONSENT_HTML` | auto-advance after a sign-in the page itself performed |

**What replaced it** (all of it server-side, none of it a page credential):
`GET /oauth/authorize` 302s an unauthenticated request to `{app}/auth?next=/api/consent/start?{query}`
(`oauth.py::consent_bounce_url`); the page mints a bound single-use assertion from the session origin
(`POST {app}/api/consent/token`) using the per-attempt key carried on its own URL; and `POST /oauth/consent`
redeems that assertion for the code. The page holds no credential, writes no cookie and signs nobody in.

**`SCOPE.md` §10 item:** the hardcoded `COOKIE_DOMAIN = ".premiselabs.co"` is deleted with the cookie, so
the item is discharged in full — parameterising a cookie this issue removes would have been churn against a
doomed line. **Recorded as a decision, not an oversight.**

**The team picker is KEPT, and its data source was widened with it.** #1701 R1's chooser asked
`GET /oauth/consent/preview` which team a grant would bind, and that route was **bearer-only**
(`verify_session_jwt`) — unreachable from a page that no longer holds a bearer. Dropping the picker was
the first cut of this step, and it was wrong twice over: the served harness recipes TELL the reader to
"pick the Organization you are onboarding" because a multi-team account sees a chooser and no named team
(`tests/test_onboarding_variants.py`, `tests/test_harness_mcp_config.py`), so removing the control would
have left every recipe pointing at something that is not there — and the multi-team + bare-resource case
would have become a `400 invalid_resource` a user cannot act on from the consent screen.

The route now has **two identity arms, the same pair `POST /oauth/consent` already accepts**: the bearer
(unchanged, byte for byte) and the **bound consent token in an `X-Consent-Token` HEADER** — a header, not
a query parameter, because the assertion is bearer-equivalent and a query string is copied into access
logs, proxy logs and the `Referer` of the next navigation. The four `bind` fields ride the query and are
**compared** (§2.2) exactly as at redemption, so a token from another flow cannot name a user. ⛔ The
preview does **not** consume the `jti` — `verify_consent_token` is pure and the route performs no write.
That is load-bearing: the preview runs BEFORE the visitor decides, so spending the assertion there would
let a reload burn the credential the approval needs (pinned by
`test_a_preview_does_NOT_spend_the_jti_so_the_visitor_can_still_approve`).

The rendered behaviour is therefore unchanged from R1: a single-team account sees the team NAMED and
Authorize armed; a multi-team account gets the chooser with Authorize **disabled until a pick**, and the
approval posts the PICKED `resource`, never the client-declared one. An unresolvable preview leaves
Authorize inert and offers the retry.

## 8. Operator verification (live — cannot be run from this repo)

- [ ] Additional Redirect URLs contain **`https://api.premiselabs.co/**`** *or* the bridge has shipped and
      provider sign-in no longer leaves the app origin (§6). Record the answer on #3524.
- [ ] `CONSENT_TOKEN_SECRET` exists on **both** the `tortoise-dashboard` Pages project and the hosted API,
      and `config/required-bindings.yml` declares it (the deploy preflight then fails closed until it does).
- [ ] `CONSENT_TOKEN_SECRET` is a **dedicated** value of **at least 32 UTF-8 bytes** (≈32
      characters for an ASCII key) — NOT the
      service-role key and NOT the anon key (§2.3). The length is an implementation floor, not a
      §2.3 requirement: HMAC-SHA256 with a guessable key is the same forgery oracle as a reused one
      (measured with a 1-character secret), so the hosted API refuses a short one with a typed 503
      that names the reason. The hosted API refuses a value equal to any of them that it can see (typed 503 whose
      description names the collision; it reads `SUPABASE_ANON_KEY` itself, for the consent page).
      This box is still the operator's for the case the API cannot see — a Pages project whose env
      carries a value the API's env does not. Skipping it turns the verifier into a forgery oracle
      for anyone who loaded the page.
- [ ] `docs/ops/auth-email-flows.md` §3's table is complete against the live allowlist (it claims to be the
      required set).

## 9. What this change delivered, and what remains

This document and `tests/test_oauth_consent_bridge.py` deliver tasks **1, 2, 4 and 5** of #3524: the token
is specified rather than assumed, `authorization_endpoint` stability is verified and guarded, the mint hop
is costed, and the sign-in fallback origin is settled with its record corrected. The BRIDGE half of task 3
is **DELIVERED** by this diff (`functions/api/consent/start.ts`, `functions/api/consent/token.ts`,
`functions/_shared/auth/consent-bridge.ts`). The *page rewrite* (step 4) was the last unit and it is
**DELIVERED** too (row 4 below) — landed as its own unit, never as one blind pass:

| Remaining step | Why it is its own unit |
|---|---|
| 1. ~~The `jti` store + `CONSENT_TOKEN_SECRET` provisioning + the manifest declaration~~ — **DELIVERED** (`ControlPlaneJtiStore` + migration `20261030000001_oauth_consent_tickets.sql`; the accepted-`kid` allowlist is deliberately NOT BUILT (ONE `CONSENT_TOKEN_SECRET` is configured, so an allowlist of one is vacuous and §2.3’s two-`kid` rotation window does not exist yet — a key rotation is a VALUE SWAP that invalidates in-flight tokens, and the verifier accepts any non-empty `kid` for presence only, so treat that promise in §2.3/`§5` as IMPLEMENTATION WORK, not as a property)) | infrastructure; the mechanism choice is §2.3 |
| 2. ~~`POST /oauth/consent` accepting a consent token alongside the bearer (verify `aud`/`iss`/`exp`/`bind`/`jti`, then `issue_auth_code` with the *bound* params)~~ — **DELIVERED**, see the paragraph after this table | the AS half; testable in Python (`tests/test_oauth_*`), and it had to land **before** any page change so the bearer path stays live meanwhile |
| 3. ~~The `/api/consent/token` mint route + the `/api/consent/start` sign-in bounce~~ — **DELIVERED** (evidence: `tests/test_oauth_consent_bff_routes.py`, incl. the TS→Python token round trip, the per-attempt key that closes the ownerless window, and the startup pin `tests/test_oauth_consent_bridge.py::test_startup_installs_the_SHARED_register`) | the BFF half; proved by `tests/test_oauth_consent_bff_routes.py` (route refusals + a TS-minted token accepted by the Python verifier) |
| 4. ~~The page rewrite~~ — **DELIVERED** (#3524 §9 step 4). The page authenticates nobody: no provider buttons, no password form, no cookie write, no inline client, no bearer. It reads the per-attempt key from its own URL, mints the bound assertion from the session origin, and redeems it at `POST /oauth/consent`. Unauthenticated `GET /oauth/authorize` now 302s to `{app}/auth?next=/api/consent/start?{query}`. Evidence: `tests/test_oauth_consent_page_step4.py` (55 cases / 39 test functions: the bounce, the REAL `signup.html` return-to allowlist executed over the emitted URL, the page's inline script run under node, byte-exact `state`, a bound token redeeming to a live code, and the org chooser — sole-team, multi-team with the pick posted, and an unresolvable preview leaving Authorize inert) | replaces `tests/test_oauth_consent_pkce.py` wholesale — its 22 invariants have no subject once the inline client is gone, so this is a **test-surface migration**, not a patch |
| 5. `SCOPE.md` §9 class 4(i) — attested client identity | a product decision (which clients are attested, and what an unattested one may claim), filed separately |

**What the "together" rule binds, and what it does not.** Steps 2→4 were scoped to land together because
a page that half-reads a token and half-reads the old cookie is the "strictly worse than not starting"
state #3559 recorded for `main.jsx`. That hazard lives in the PAGE — nothing else in the sequence can
half-read anything — so the rule binds **step 4**, which lands last and never in halves. Steps 2 and 3 are
ADDITIVE and INERT on their own: step 2 is the token arm of `/oauth/consent` (bearer path untouched), and
step 3 is `/api/consent/start` + `/api/consent/token` + `_shared/auth/consent-bridge.ts`, which the consent
page (`tortoise/oauth.py::_CONSENT_HTML`) did not reference and could not reach **before step 4 landed**
(it is the mint's only caller now, by design). With step 4 unbuilt no visitor's browser called either route,
so a deployment carrying step 3 without step 4 was a **no-op surface**, not a half-migrated flow — and the routes' own evidence (`tests/test_oauth_consent_bff_routes.py`) runs the
shipped TS, not a page.

**OVERRIDES:** the earlier "Steps 2→4 must land together **or not at all**" read as an operative ordering
rule — because its recorded reason (#3559) is scoped to the page's half-read, and step 3 adds no caller to
the page; relayed to [issue #3524 (decision comment)](https://github.com/daniel-ospina/tortoise/issues/3524#issuecomment-6516505856)
and qualified here, where the rule is read. Nothing reverses the decision that step 4 lands last.

**Step 2 is DELIVERED** by #3524 (`tortoise/consent_token.py` + the token arm of the
`/oauth/consent` handler), and its guarantees are EXECUTED — not merely anchored — by
`tests/test_oauth_consent_bridge.py`. Two conditions an operator must know, both recorded there:

- the arm is live only where **`CONSENT_TOKEN_SECRET`** is set on the hosted API **and is a dedicated
  value of at least 32 UTF-8 bytes** (§2.3; a value equal to a service-role key or to
  `SUPABASE_ANON_KEY` — both are in this process's environment, and the anon key is public by
  construct (it ships to every browser that loads the app, not because this page publishes it) — is
  treated as unprovisioned, and so is a key too short to be one). Otherwise the arm answers a typed `503 temporarily_unavailable` naming the reason,
  and the bearer path is untouched — never a 500, and never a silent fall-through to the bearer;
- single-use holds ACROSS MACHINES: §2.3's shared `jti` store (step 1) is DELIVERED and installed at
  startup from `hosted_api._lifespan` (`ControlPlaneJtiStore` + migration `20261030000001`), so the
  `ProcessLocalJtiStore` register is the seam's fallback for a bare embed and tests, NOT the deployed
  posture. The only residual is a typed `503 temporarily_unavailable` when the register cannot answer —
  never a silent fall-through to per-process state, because the store is installed unconditionally.

Two properties of the arm that a later step must be written against, because they are limits of the
contract rather than of the implementation:

- **the token does not attest the org or the scope.** `bind` is exactly §2.1's four params, so both
  are taken from the redemption REQUEST, and the two are bounded DIFFERENTLY:
  `resource` (the D4 org picker) by `parse_resource` + the membership and suspension checks inside
  `issue_auth_code` — so a token minted while the visitor was approving team A can be redeemed
  naming team B, but only a team the asserted user actually belongs to; `scope` by neither of those,
  only by `validate_scope` against `SCOPES_ACCEPTED` before identity is read, so a redemption body
  may name a different scope than the page displayed. (Do NOT read that as "blocking
  `offline_access` is the mitigation": `mcp` alone already gets a refresh token — the token response
  always emits one — so a divergence in scope is not a capability boundary step 4 can draw.) Step 4's page must therefore present NEITHER as token-attested, and widening the
  bind set (or binding the minted scope to the displayed one) is a spec decision, not an
  implementation one.
- **a failure after the nonce is spent consumes it.** The `jti` is recorded before the code is
  inserted (so one consent cannot yield two codes), and `issue_auth_code` can then still refuse —
  no active team, a suspended org, a transient control-plane failure. The retry answers
  `401 invalid_token` "already redeemed", so the recovery is a **re-mint**, not a re-login. The
  refusals that can be evaluated for free (the assertion, the binding, a malformed `resource`) are
  evaluated first.

Step 4 was the last step, and it is DELIVERED (#3524 §9 step 4): the ordering rule above existed so
that no deployment could ever serve a page that half-reads a token and half-reads the old cookie —
the "strictly worse than not starting" state. That page is gone, not partially migrated: it holds
no cookie and no client.

## 10. Integration docs

| Item | Value |
|---|---|
| **Mint authority** | `app.premiselabs.co` — the single session-bearing origin (`SCOPE.md` §3). |
| **Signing** | HMAC-SHA256, dedicated `CONSENT_TOKEN_SECRET`, `kid`-scoped rotation. Never the service-role key; never the anon key — public by construct, independently of this page (§9 step 4 deleted the inline client that used to embed it, so the page is no longer evidence either way). |
| **Issuer** | `CONSENT_TOKEN_ISSUER` (optional; default `https://app.premiselabs.co`) is the `iss` the AS accepts — the knob a preview deployment sets so it verifies its OWN mint. Read per call, never cached at import. |
| **TTL / replay** | `exp = iat + 120 s` (leeway ≤30 s) **and** a single-use `jti`; neither substitutes for the `bind` set. |
| **The page (§9 step 4)** | `GET /oauth/authorize` renders `tortoise/oauth.py::_CONSENT_HTML` **only when the request carries `attempt_key`**; otherwise it is a `302` to `{app}/auth?next=/api/consent/start?{raw query}` (`consent_bounce_url`), and the sign-in page's own session probe forwards a signed-in visitor straight through. The page then POSTs `{state, attempt_key}` to `{app}/api/consent/token` (`credentials: include`) and, on a `200 {token, exp}`, asks `GET /oauth/consent/preview` which team the grant binds (the token in `X-Consent-Token`, the four bind fields in the query) and renders the org line — or the #1701 R1 chooser — from that ANSWER, then POSTs `consent_token` + the raw authorize params to `/oauth/consent`, navigating the response's `redirect_target` with `code` + the response's `state`. **It holds no credential, writes no cookie, and signs nobody in** — the AS's only render-time evidence is the attempt key the app wrote onto the URL. |
| **Redemption** | `POST /oauth/consent` (`@app.post("/oauth/consent")` in `hosted_api.py`) — TWO identity sources as of §9 step 2: the bearer (`verify_session_jwt`, `session_auth.py:588`) or a `consent_token` in the body verified by `tortoise/consent_token.verify_consent_token`. |
| **Endpoint stability** | `authorization_server_metadata` (in `tortoise/oauth.py`); `_oauth_base(request)` (a function in `hosted_api.py`). Guarded. |
| **CORS** | new, exact-origin to the AS origin, on the mint route only. The BFF carries none today; `/oauth/token` must never become reachable through it (`SCOPE.md` §9 class 3). |
| **Bindings to declare** | `CONSENT_TOKEN_SECRET` (`config/required-bindings.yml`, `tortoise-dashboard`), checked pre-deploy by `tools/check_pages_bindings.py` (the #3616 gate). |
| **Verified by execution** | `tests/test_oauth_consent_bridge.py` (RFC 8414 stability + the allowlist record). |
| **Vendor docs read at** | Supabase Auth redirect allowlist, string-match with placed wildcards (`docs/ops/auth-email-flows.md` §3, 2026-09-22). |

## Still open

- **§9 class 4(i) — attested client identity — [#8218](https://github.com/daniel-ospina/tortoise/issues/8218).**
  `SCOPE.md` lists the class as **OPEN** and requires both mitigations together; (ii) is specified here, (i)
  is filed and unbuilt. **The consent screen can still be made to say "Claude" by anyone who registers that
  name.**
- **#3529** — 4 of the 7 declared threat classes still have no test. The consent token's own surface is
  no longer one of them: as of §9 step 2 it exists and carries its adversarial rows
  (`tests/test_oauth_consent_bridge.py`), so the remaining count is the 7 declared classes only.
