---
title: "ADR-012: Browser OAuth grant — the #1566 implicit grant and its retirement for Authorization Code + PKCE"
type: decisions
domain: platform
doc_status: live
subjects.team: epistemic-team
aboutSubjects: tortoise
aboutObjects: tortoise-dashboard, tortoise-consent-page, implicit-grant, authorization-code-pkce, code-verifier
created: 2026-10-12
---

# ADR-012: Browser OAuth grant — the #1566 implicit grant and its retirement for Authorization Code + PKCE

**Status:** **Superseded** — the decision below was Accepted as part of **#1566**, and is **no longer in
force**: both browser OAuth clients now run Authorization Code + PKCE. The record is kept because the
reason implicit was chosen was believed, until **#3501**, to be a live constraint *on PKCE* — and an
adopter who finds the retirement recorded nowhere will re-derive it as one.
**Date:** 2026-10-12 (records a decision taken 2026-08; superseded 2026-09-28 → 2026-10-08)
**Issues:** #1566 (the decision) · #3501 (the falsified premise) · #3496 / #5753 (consent page) · #4104 / #3559 (dashboard BFF, bridge deletion) · #3495 (this record)
**Owner:** epistemic-team

## Context

Issue #1566 moved sign-in so the OAuth client was **initiated on `tortoise.premiselabs.co`** (the marketing
origin, where the sign-up button lived) and **completed on `app.premiselabs.co`** (the app origin,
where the claim/provisioning branch sent the user with `redirectTo`).

supabase-js obtains the PKCE `code_verifier` through its configured storage adapter, and that
adapter's **default is `localStorage`** — which is origin-scoped. A verifier written on
`tortoise.*` is therefore absent on `app.*`, so a code→token exchange on the landing origin would
have had no verifier to present (`invalid_grant`).

The conclusion was recorded **only as a code comment** on the shared session bridge — in the general
form *"a PKCE `code_verifier` is origin-scoped and cannot cross subdomains"* — which survived in
`website/assets/supabase-session.js` and its `website/apps/dashboard/public/assets/` copy. No ADR,
plan or issue carried the decision itself, so the constraint outlived its own explanation.

## Decision (as taken, #1566)

Use the **implicit grant** for the browser clients — supabase-js's default `flowType: 'implicit'` —
and hand the session across origins as a **URL fragment**, materialised into a
`Domain=.premiselabs.co` cookie by the shared `supabase-session.js` bridge.

Implicit is the grant RFC 10017 §7.2 says a browser-based client **MUST NOT** use, and the recorded
reason was a constraint on PKCE. So this was a deliberate, reasoning-bearing departure from the
standard — **not** an oversight. The departure is **RETIRED**: it is recorded here so the reasoning
is not lost, but no surface uses implicit today and citing this ADR to re-adopt it would reverse a
superseded decision whose premise #3501 falsified (see *Supersession*).

## Why the premise was wrong (#3501)

The claim conflated PKCE with **one storage adapter's default**. PKCE does not require
`localStorage`: the verifier is a value the client chooses where to keep, and it can live in a
cookie — or any store the flow's origins share — or, better, never reach a browser at all.

Verified in `@supabase/ssr@0.12.7` source: its browser client hardcodes `flowType: "pkce"` and
writes the verifier through the **cookie** adapter
(`<storageKey>-flow-<flowId>-code-verifier`, `cookies.js`). And in the canonical flow the verifier
never has to cross an origin at all, because initiation and completion happen on **one** origin.

So the constraint was real only for the specific combination #1566 happened to have — a
**split-origin client whose verifier was left in `localStorage`** — and not for PKCE, and not for the
origin model. **Nothing about PKCE had to change to retire implicit.** The corrected statement is
the weaker one: the *default* verifier storage is origin-scoped — not PKCE.

## What moving to Authorization Code + PKCE required

Three things, none of them a change to PKCE:

1. **A verifier home that is origin-scoped and is not the session store.**
   - **Consent page** (`tortoise/oauth.py`): an origin-scoped aux chain —
     `sessionStorage` first, `localStorage` second, **fail-closed refusal** if neither accepts; the
     verifier is routed there by an **allowlist-of-one** on the cookie branch (only the session-key
     constant may reach `document.cookie`), so it cannot enter the JS-readable parent-domain jar.
     `sessionStorage` is preferred because a verifier is single-use and must not outlive the tab
     (RFC 10017 §8.5).
   - **Dashboard**: the exchange moved **server-side**. `POST/GET /auth/start` mints the verifier and
     keeps it in a D1 `auth_flows` row, `GET /auth/callback` performs the exchange, and the browser
     holds only the opaque `__Host-session` HttpOnly cookie — the verifier is never in a browser
     store, so origin-scoping does not arise.
2. **An explicit grant type** in the client config — `flowType: "pkce"` (with
   `detectSessionInUrl` retained for the `?code` exchange, not for fragment ingestion).
3. **One origin per flow.** The consent page already initiated and completed on its own origin, so
   this was already satisfied there; the dashboard's split-origin client was retired by the
   `app.premiselabs.co` BFF topology (#3501/#4054).

No change to the origin model was required, and none to PKCE itself.

## Supersession

| Surface | Before | After | Landed by |
|---|---|---|---|
| Dashboard OAuth | implicit + fragment + parent-domain cookie bridge | server-side PKCE (`auth/start` → `auth/callback`), `__Host-session` HttpOnly | #4054 / #4104 |
| MCP consent page (`tortoise/oauth.py`) | implicit (library default) | explicit `flowType: "pkce"` + origin-scoped aux verifier store | #3496 / #5753 |
| `website/assets/supabase-session.js`, `website/apps/dashboard/public/assets/supabase-session.js` | carried the stale "PKCE-agnostic"/implicit comments | **DELETED** — no page loads them; the guards prove `PAGES = []` | #3559 (shared) · #4104 (dashboard copy) |

**Contradiction test (recorded in #3496's scoping record, run before that migration).** Proceeding
to PKCE contradicts no recorded decision: #3501 itself rules that the stated blocker was never real,
and the `OVERRIDES:` ruling in `docs/auth-architecture.md` (rejecting a cross-subdomain session
cookie) concerns the **session**, which this change does not touch. The route was therefore
*proceed*, not *reopen*.

## Consequences

- **Good:** both clients conform to RFC 10017; the session credential is no longer JS-readable from
  any subdomain; the code→token exchange happens where the verifier lives (or server-side), so the
  cross-subdomain handoff that motivated implicit no longer exists.
- **Retired:** the implicit grant, the URL-fragment session handoff, and the shared
  `supabase-session.js` bridge. The parent-domain `sb-tortoise-auth-token` cookie remains issued and
  accepted **only** by the consent page's own legacy reader, tracked separately (#3524); that is a
  session-model item, not a grant-type one.
- **Recorded so it cannot re-stale:** the reason implicit was chosen, the falsified premise, and the
  three steps the migration actually took. A future reader who finds the retired constraint cited
  somewhere should treat this ADR as the resolution, not re-derive the constraint from the citation.

**Implementation:** #3496, #5753, #4104, #3559 · **Premise correction:** #3501 · **This record:** #3495
