---
title: "#3496 — MCP consent page: migrate the browser auth off the implicit grant to Authorization Code + PKCE"
type: engineering
domain: platform
doc_status: live
subjects.team: organisation-design-team
aboutSubjects: browser-oauth-consent, implicit-grant, authorization-code-pkce
aboutObjects: mcp-oauth, tortoise-consent-page, code-verifier, supabase-js, rfc-10017
created: 2026-09-27
---

# Scoping / design — #3496: browser auth off the implicit grant

**Issue:** [#3496](https://github.com/daniel-ospina/tortoise/issues/3496) — *Browser auth uses the
implicit grant RFC 10017 says MUST NOT be used (surface behind #3485)* · **Level:** task ·
**Complexity:** complex · **Lane:** `lane:c3-onboarding` · **Branch:** `fix/3496-implicit-grant` ·
**Base:** `origin/main` @ `5b6cb9367`

**Search tool (research PREFLIGHT):** none — the decision is grounded in the repository and the RFC
primary texts. The library is vendored in-repo and was **executed**, so the external-verification
skip is justified (see `### Integration Docs`). No SOTA/convergence claim is made; two verifiers
could not falsify the governing-clause ruling, and the contradiction test against recorded
decisions passed (below).

---

## Confirmed Problem (frozen; problem-verify gate PASSED)

The repository's **only live browser OAuth client** — the FastAPI-served MCP consent page
(`GET /oauth/authorize`, `tortoise/hosted_api.py:28395` → `tortoise/oauth.py::consent_page_html`,
live on `api.premiselabs.co`) — requests its access token with the **Implicit** grant:

- the inline supabase-js client (`tortoise/oauth.py:1969-1980`) sets no explicit `flowType`, so it
  inherits the library default `DEFAULT_AUTH_OPTIONS.flowType = 'implicit'` (verified by executing
  `website/apps/dashboard/public/vendor/supabase-2.112.2.min.js`);
- RFC 10017 §7.2 is two-sided (`MUST NOT` use implicit for browser-based clients; the AS `MUST NOT`
  accept it) with **no grandfathering**, and §6.3.2.1 requires PKCE; §1/§7.1 do not exclude this
  surface (two verifiers could not falsify this ruling);
- the flow **already initiates and completes on one origin** (`:2134`:
  `redirectTo = window.location.origin + AUTHORIZE_PATH + window.location.search`), so the #1566
  constraint ("a PKCE verifier is origin-scoped and cannot cross subdomains") is **not violated by a
  migration on that origin**.

**Why the issue body's surface list was corrected.** The body named the dashboard /
`tortoise.premiselabs.co` surface. That surface is **already migrated** — PKCE + a server-held BFF
(`website/apps/dashboard/functions/auth/start.ts:139` s256; `_shared/auth/supabase.ts:127`
`grant_type=pkce`; `_shared/auth/session.ts:23,96` `__Host-session` HttpOnly, no `Domain`). The only
**live** implicit grant in the repository is the consent page above
(`website/assets/supabase-session.js` still sets `flowType:'implicit'` but is **unloaded dead code** —
`tests/test_cross_subdomain_cookie_sync.py` pins `PAGES = []`). The body's prescription
(`detectSessionInUrl:false` + a Pages Function at `/auth/callback`) targets a surface that does not
exist on a FastAPI host. The body's claim that the page "already implements step 4" is **false** — a
correcting comment is posted.

**Contradiction test (recorded BEFORE other tests, per `AGENTS.md`).** #3501 states verbatim: *"The
stated blocker for PKCE was never real. #1566 chose the implicit grant because 'a PKCE code_verifier
is origin-scoped and cannot cross subdomains.' That is true of supabase-js's default verifier
storage (localStorage) — not of PKCE."* No recorded decision contradicts the migration, so the route
is **proceed** (no reopen). #3524 (consent identity bridge + the four session-flow properties under
HttpOnly) is the tracked root for the **session model**, but its title/tasks never name the grant
type, so this slice is **additive with a coupling risk** (a merge-collision region), not a
supersession. `docs/auth-architecture.md:93` records an `OVERRIDES` ruling rejecting the
cross-subdomain session cookie — this change does not touch the session.

---

## Verification Gates

### problem-verify: 4 cycles, clean
(2 problem-diverge → 2 problem-converge → 4 cycles of 2 problem-verifiers; both returned
`GATE: PASS` at cycle 4. V2 wedged once on host load and was re-dispatched. Amendments across the
cycles: sticky-return sanitisation; CDN specifier pin; verifier-home correction; write-path parity;
§6.3.2.1 full quote; test strategy; deferral ledger; the false "already implements step 4" claim;
the B1 delete-`replaceState`-on-load finding; the store-coherent fallback; the jsdelivr byte-identity
account; one specified terminal state; the latch + remove-from-every-store; the version-specifier
derivation; and the `test_cross_subdomain_cookie_sync.py` refactor constraints.)

### solution-verify: 4 cycles, clean
Cycle 1: 4 P1 (aux-chain cookie leg contradicted the router's headline claim; the version pin did
not fire on a vendor-only PR; the ported contract was bounded only by a shape tripwire; `SIZE_CAP`
had no agreement assertion) + 2 P2 + 4 P3. Cycle 2: 1 P1 (the refusal had no observable mechanism) +
6 P2/P3. Cycle 3: 1 P1 (the docs passage lands in a live gate-scanned region) + 10 P2/P3/P4.
**Cycle 4: 0 P0/P1** — one solution-verifier returned `CLEAN`, the other `CLEAN`, and the advisory
duplication-architecture reviewer returned only P2/P3. Per the skill's pass-through rule (verifiers
find only P2/P3/P4 → incorporate → gate passes), the gate passes; the remaining items are
incorporated below.

**[ADVERSARIAL-BOUND] cycles=2 threats=7 covered=7 residuals=R19(library-owned verifier copies)** —
the declared `### Adversarial Threat Surface` (A1–A7) was reviewed by a fresh adversarial-coverage
reviewer. **Cycle 1 returned `THREAT SURFACE NOT COVERED`** (A2/A4 pinned to an invariant that never
observed the return target; A5's declared behaviour reproducible as false; A3's `boundedText`
unspecified — all P2/P3); the fixes (new invariants 11 and 12, a specified `boundedText` with a
paired mutation, a sized probe key, an extended negative list, an explicit out-of-scope block) were
applied, and **cycle 2 returned `THREAT SURFACE COVERED`** with 5 observations, all incorporated
(nested-`redirect_to` parsing for inv 11 + its paired control, the inclusive 300-char bound, the
74-char probe key, the `URLSearchParams.delete`/allowlist hardening note, and the object-lookup
static-check caveat). Per the adversarial-domain bound, the remaining A5 sub-case is a
**library-owned** residual (R19) named and not chased.

---

## Plan

### Chosen solution
Authorization Code + PKCE on that one client, on the origin it already uses, with the `code_verifier`
origin-scoped and the write path made honest.

### 1. Duplication adjudication (load-bearing — both convergers, independently)
A key-routed, verifier-aware storage adapter **already ships in this repo**:
`website/apps/blog-admin/src/lib/supabase.ts:157-189` (`authStorage`), tested by
`website/apps/blog-admin/src/lib/supabase-auth-storage.test.ts:92-107`, which pins exactly the two
cases needed (`getItem(\`${KEY}-code-verifier\`) === null`; `removeItem(\`${KEY}-code-verifier\`)`
must not clear the session cookie). Its predicate is the **complement**: `key === STORAGE_KEY` →
cookie; **every other key** → aux store.

Rev 2–5 of the problem statement specified a `/-code-verifier$/` **suffix denylist** — an open
denylist whose default branch is *into* the credential jar, over a namespace a third party
(supabase-js) owns. **Ruling: port the complement; adopt the allowlist-of-one. Reject the suffix
matcher as an unjustified `keep separate` of a contract the repo already ships and tests.** It is
strictly narrower on cookie writes, rename-proof, and the only divergence on this page is
`${storageKey}-user` — verified **never written** here (`userStorage` is `null`; read only on the
legacy-migration branch; removed by `_removeSession`). Suffix routing would emit a stray cookie
expiry for `…-user` on every **`_removeSession`** (there is **no `signOut()`** on this page; the
trigger is `_removeSession` from an invalid/expired stored session, reached via
`_recoverAndRefresh`); the complement, with the aux chain below, cannot.

**Mechanism:**
1. a **fail-closed allowlist-of-one** in the cookie branch (`key !== COOKIE_NAME` → the aux chain
   and `return`); and
2. an aux chain with **no cookie leg** (Step 4), so no non-session key reaches `document.cookie`
   **by construction** — independently re-enumerated: 8 `document.cookie` statements across 3
   production components, and the vendored bundle contains **zero** (it writes only through the
   supplied adapter, whose oauth-page call paths are all dominated by the Step-3 guard); and
3. a **pre-flight capability guard** (Step 3b) so a refusal is observable locally, **plus** the
   executable agreement tests on the oauth.py side (invariants 3, 4, 12 — **new** code, not a
   pre-existing harness: `tests/test_oauth_mcp.py:439-440` records that this page has no jsdom
   harness today), **plus** a **per-method, polarity-normalised static predicate assertion on both
   copies** with the read path excluded (Step 10(b)) and a method-scoped negative check for a
   suffix/pattern test on `key`.

**Recorded residual:** full cross-language **executable** parity with blog-admin's TS driver is not
in this diff (its adapter is declared via TS arrow properties that `_extract_fn_body` cannot parse,
and executing it needs an ESM/strip-types loader + `import.meta.env` stubs — fragility in a P0
lane). Bounded by mechanisms 1–3, and filed as a follow-up with three named gaps: (i) blog-admin's
adapter has **no CI-gated suite** (`ci.yml:166`); (ii) the parity suite **explicitly exempts**
blog-admin from the helper/attribute/size-guard axes (`:225`, `:549`, `:600`) and it is **absent
from the suite's `surfaces` table**, so its `document.cookie` writes are outside the completeness
scan today; (iii) the third copy (`website/assets/supabase-session.js`, unloaded, writes **any**
key) would reintroduce verifier-into-cookie if the bridge were re-armed.

**Contract passage (gate-compliant target).** `docs/auth-architecture.md` **already** declares this
cookie's issuer/acceptor inventory (§2.1 legacy-cohort register, `:99-136`) and already records the
`OVERRIDES:` ruling (`:93`) plus, at §4 item 1 (`:244-254`), that the surface is OPEN/NOT CLOSED.
What is **unrecorded** is the **storage-adapter key-routing predicate**. Append the passage as
**continuation sentences of the `- **OVERRIDES:**` bullet at `docs/auth-architecture.md:93-98`** —
the claim unit is the bullet **plus its continuation lines**, so:
- **no new list item at any indent** (`_ITEM_START` matches `-`/`*`/`+`/`N.` at any indent), **no
  blank line, no heading, no table row** — any of those splits the unit and the marker stops
  covering the new text (`_DISPOSITION` = `overrides`/`reject`/`superseded`/`deprecated`/`pre-bff`
  must be in the unit's **first sentence**; `_REJECTION_MARKERS` is the 15-word list at
  sentence/unit scope). Meaning: do **not** format the four content items as a sub-bullet list.
- **why not §5.5** — it carries no `_DISPOSITION` marker in the unit the passage would join, and it
  is a named current-state anchor at `:68`.
- **why not a new numbered subsection** — `_CLASSIFIED_SUBSECTIONS` hard-fails any §2/§5 number it
  does not classify (a new `§5.7` is a guaranteed red).
- **anchoring** — anchor to **symbols and tests**, not line numbers; the pre-existing line-number
  parenthetical in that bullet is stale and self-contradictory (`:1445`/`:1467`/`:1498` and the
  `1335-1360` clause) and is **deleted** in the same edit (deletion, not re-narration — a
  line-number claim about code is the class that keeps re-staling).
- **content** — page-scoped (`for the consent page's adapter`), never a global absolute (the
  `#4678` class): the predicate (only the session key may reach the cookie), the session-key
  constant's authority, the deliberately-different **aux destination** (blog-admin `localStorage`-
  only; oauth.py `sessionStorage`-primary → `localStorage`-fallback — a single-use, tab-lifetime
  credential; RFC 10017 §8.5), and the deliberate non-unification with its three-valued reason. The
  divergence is a recorded `keep separate` **on the destination**; the routing contract is
  `unify-contract-keep-drivers`.

### 2. Rejected alternatives (with what each would be better for)
| Alternative | Why rejected |
|---|---|
| **The issue body's prescription** (`detectSessionInUrl:false` + Pages Function at `/auth/callback`) | Targets a surface that does not exist (FastAPI host, no Pages Function); flow-breaking as written (`?code` unconsumed → null session → the #3485 silent sign-in). |
| **Full server-held BFF / §6.2** | The right longer-term answer, but it is #3501's decided topology and #3524's execution — implementing it here executes another issue's plan. Shelf life declared. |
| **Verifier in the existing parent-domain cookie adapter** | The naive `flowType:'pkce'` flip and the explicit verifier-key-into-that-adapter form both land the verifier in the JS-readable `Domain=.premiselabs.co` jar — shared across tabs/subdomains, clobbering → `invalid_grant`. |
| **Verifier in `localStorage` as PRIMARY** | Shared across all tabs and persistent — larger surface for a single-use credential (`sessionStorage` is per-tab, RFC 10017 §8.5). Kept as the *fallback*, not the primary. |
| **Aux chain's last resort = the cookie jar** | The parent-domain jar is readable by every `*.premiselabs.co` origin; a non-session key there is the exposure the verifier-home decision exists to prevent, and it made an earlier revision's own "never reaches `document.cookie`" claim false. Replaced by fail-closed refusal. *Better for:* nothing on this page. |
| **`skipBrowserRedirect: true` + manual `window.location.assign(data.url)`** | Viable and would make a *post-`await`* refusal locally visible. Rejected because the **pre-flight guard** (Step 3b) detects the case *before* any round trip with less code. *Better for:* a page that must surface post-`await` failures from the library. |
| **Extract the client into a `.js` under `tortoise/`, inlined by Python** | Packaging fail-open trap: `[tool.setuptools.package-data]` lists per-file entries and `packages.find` cannot discover a data file, so an omitted entry ships a wheel without the client while every test passes (#1929/#3818/#3819 class). Does **not** fix the duplication (blog-admin is TS/Vite and cannot import it). Its one advantage is bought better: extract from the **rendered** HTML. *Better for:* a consolidation lane introducing a build step. |
| **DOM-free functional core + thin shell** | Extracts the wrong thing — the load-bearing behaviour is inside supabase-js, and the mutation that matters (a verifier key routed into the cookie) is invisible to a pure function. Also breaks the verbatim method-body pins and `_extract_fn_body`'s first-match. *Better for:* a page with a large DOM-independent decision table. |
| **`website/apps/dashboard/src/supabaseVendorPin.test.js`** | **Redundant with the Python derived pin** (Step 1) — not a duplicate of `distBundle.test.js` (#2865), which performs **no version comparison** but does already walk the served dist + every `public/vendor/*.js`. A vendor-only PR already runs `dashboard-js-tests`; only the **Python-side** pin is unselected, which Step 9(b) fixes. |
| **Move the SESSION to `sessionStorage`/`__Host-`** | Breaks #1704's cross-subdomain read; that is #3501/#3524/#3559's decision. |

### 3. Implementation steps (all in `tortoise/oauth.py` unless stated; base `5b6cb9367`)

**Step 1 — pin the library version.** `:1883`: `…/@supabase/supabase-js@2/…` → `…@2.112.2/…`. SRI
(`integrity`/`crossorigin`) stays **out** (item 5 → #3501). *Pin:* assert the page's specifier
version equals the version parsed from
`website/apps/dashboard/public/vendor/supabase-<ver>.min.js`. The **derived** assertion's home is
`tests/test_oauth_mcp.py::TestAuthorizePage` (api-registered), beside the literal `@2.112.2` textual
pin (a literal pin alone cannot catch a vendor bump); the new harness re-asserts it. Both
directions are wired: an `oauth.py`-only diff selects `api`; a vendor-only diff is made to select
`api` by Step 9(b).

**Step 2 — explicit PKCE.** Add `flowType: "pkce"`. **Unchanged:** `storageKey`,
`persistSession: true`, `autoRefreshToken: false`, **`detectSessionInUrl: true`** — retained because
the library gates the PKCE `?code` exchange on it (`_initialize` → `_getSessionFromURL`), NOT to
ingest a token fragment: with `flowType: "pkce"` an implicit-style fragment return is refused by the
bundle. `test_one_fragment_consumer_per_page` pins the string; its assertion MESSAGE still carries
the older fragment-ingestion rationale, which is stale but lives outside this PR's diff (tracked
with the dashboard-bridge comment item).

**Step 3 — the key-identity router.** `:1929-1964`. **Keep the same three method names and
signatures** (`getItem(key) {`, `setItem(key, value) {`, `removeItem(key) {`) and keep **every**
`document.cookie =` inside `setItem`/`removeItem` — the guard is the **first statement** of
`setItem`/`removeItem` and falls through to the existing cookie code for the session key. Predicate:
`if (key !== COOKIE_NAME) { …aux chain…; return; }` — an **allowlist of one**. Comments may name
`code-verifier`. Constraint: **no braces inside comments/strings within those two method bodies**
(the extractor's brace matcher is not string-aware).

**Step 3b — pre-flight capability guard (before the redirect), as landed.**
```js
function pkceIncapable() {                       // null | "no-webcrypto" | "no-store"
  if (!(window.crypto && window.crypto.subtle && typeof TextEncoder !== "undefined")) return "no-webcrypto";
  const payload = "v".repeat(160);               // ≥ the verifier value: a quota that fits the payload fits the verifier
  const sentinel = "__tt_probe-" + Math.random().toString(16).slice(2).padEnd(89, "0");
  // ^ 100 chars, LONGER than the longest real verifier key (`${storageKey}-flow-<32hex>-code-verifier`,
  //   22+6+32+14 = 74). Random, mirroring the library's own `lswt-${Math.random()}…` probe (localStorage
  //   is cross-tab). It deliberately does NOT carry a `-code-verifier` suffix: a store that refuses
  //   removal cannot be cleaned, and the one entry it leaks must not be mistakable for a credential.
  for (const store of auxStores()) {             // the GETTER is inside auxStores()' try (opaque-origin throws on access)
    let wrote = false;
    try {
      store.setItem(sentinel, payload);
      if (store.getItem(sentinel) !== payload) throw 0;
      wrote = true;
      store.removeItem(sentinel);
      if (store.getItem(sentinel) !== null) throw 0;   // silently-ignored removal
      return null;
    } catch (e) {
      if (wrote) return "no-store";             // the store the WRITER would pick cannot be cleaned — refuse
      try { store.removeItem(sentinel); } catch (e2) { /* best effort */ }
    }
  }
  return "no-store";
}
```
The guard is an early-failure optimisation: it must never *be* the invariant, because it probes a
throwaway key with a payload sized for the verifier, and the writer's own choice can differ (a store
whose accepted-size band sits between the probe and the real value). The invariant is enforced where
the real key and value are known — in `writeAux`, which re-proves writability AND cleanability on
each candidate store at write time, with a payload of the REAL length under a throwaway key — a
DUMMY of that length, never the credential itself — and only then writes the credential
(invariant 13):
```js
const writeAux = (key, value) => {
  const v = String(value);
  const probe = "x".repeat(v.length);   // DUMMY, same length — never the credential
  const probeKey = "__tt_wprobe-" + Math.random().toString(16).slice(2).padEnd(32, "0");
  for (const s of auxStores()) {
    try {
      s.setItem(probeKey, probe);
      if (s.getItem(probeKey) !== probe) throw 0;
      s.removeItem(probeKey);
      if (s.getItem(probeKey) !== null) throw 0;   // silently-ignored removal
      s.setItem(key, v);
      if (s.getItem(key) !== v) throw 0;
      return true;
    } catch (e) {
      // Clean BOTH keys: the failure can be the read-back AFTER a successful
      // `setItem(key, v)` (a store that truncates what it accepted), and the
      // next iteration writes the same credential into the next store.
      try { s.removeItem(probeKey); } catch (e2) { /* ignore */ }
      try { s.removeItem(key); } catch (e3) { /* ignore */ }
    }
  }
  return false;   // refuse — never fall through to the cookie jar
};
```
`removeAux` still scans EVERY aux store (it runs on paths where the guard never did, e.g. an
invalid-session load), and `readAux` likewise (a verifier may live in either store). Residual R21: a
store that discriminates by KEY (accepts/removes its own probe key but refuses the `sb-…` key) is
not caught by either probe; no browser storage behaves that way, and a key-aware shim on the origin
is already inside the declared out-of-scope "XSS on the origin" (#3559).
```js
async function signInWithProvider(provider) {
  const incap = pkceIncapable();
  if (incap) { showSignin(); showError(incap === "no-webcrypto"
      ? "This browser cannot complete a secure sign-in here (no WebCrypto). Open this page over HTTPS."
      : "This browser is blocking site storage, so sign-in cannot be completed securely. Enable storage (or leave private browsing) and retry."); return; }
  const { error } = await supabaseClient.auth.signInWithOAuth({ … });   // unchanged
  if (error) showError(error.message);
}
```
Rationale (executed): with `crypto.subtle` absent (but `getRandomValues` present) the bundle
silently downgrades to `code_challenge_method=plain` + `console.warn`, and `isLocal()`
(`:1917-1922`) deliberately admits `http://10.*/192.168.*/172.16-31.*` origins; with the aux store
unavailable the library would still navigate and the verifier would be lost. Failing closed
**before** the redirect avoids a wasted round trip and makes both refusals locally observable.
The guard mirrors the bundle's **full** condition (`crypto.subtle && typeof TextEncoder !==
"undefined"`).

**Step 4 — aux chain: sessionStorage → localStorage, then REFUSE (no cookie leg).** Non-session keys
go to `sessionStorage`; if unavailable, `localStorage`; if both are unavailable, the write is
**refused** (Step 3b prevents reaching here in practice; the adapter still `try`/`catch`es so a
mid-flow failure is never an unhandled rejection). `getItem` reads `sessionStorage` →
`localStorage` → `null`. `removeItem` for a non-session key clears it from **`sessionStorage` and
`localStorage` only — never the cookie leg**, and the cookie-removal code is reachable **only** for
the session key. *Why:* makes A1 true by construction, removes the `aux → cookie → router → aux`
re-entrancy trap, and honours the verifier-home decision. There is **no latch and no cookie
last resort**.

**Step 5 — `authorizeReturnTo()` (defence-in-depth).** New helper; `signInWithProvider` (`:2131`)
uses it instead of the raw `window.location.search`. Strips
`code`,`error`,`error_code`,`error_description`,`error_uri`,`sb_flow_id`,`flow_id`,`type`; origin
and path remain constants. **Not** presented as fixing a confirmed sticky loop: GoTrue's
`prepPKCERedirectURL` uses `q.Set("code", code)` (replaces any existing `code`),
`GetReferrer`/`IsRedirectURLValid` validate only scheme/host/port, and supabase-js's `xr()` keeps
the **last** duplicate — so a stale `code` cannot survive to a second exchange. Justification:
canonicalising the return target so transient params are never echoed back to the provider.

**Step 6 — terminal state + post-resolution sanitisation (one place).** Read the load URL **once,
read-only**, at load, capturing whether it carried a transient and the `error_description`. **Never
mutate `window.location.search` before the library has attempted the code** (B1: `createClient()`
captures `e.code` synchronously inside `_initialize`). In the `onAuthStateChange` callback
(`:2212-2216`), on `INITIAL_SESSION` with **no session** and a transient present:
```js
showSignin();                                   // #view-signin MUST become visible
showError(boundedText(error_description) || "Sign-in failed — please start again.");
```
then, and only then, `history.replaceState` to the sanitised URL. `boundedText(s)` is specified (not
left as a name): strip `[\u0000-\u001F\u007F-\u009F\u2028\u2029]`, then truncate to **300 chars**
(appending `…`). Co-assertion: the rendered `textContent` length is **at most 300 including** the
appended `…` (state it explicitly — `s.slice(0, 300) + "…"` yields 301 and would be a false red). The
refusal cases are handled locally by Step 3b. **`showSignin()` is mandatory** — `#view-consent` is default-visible (`:1854`),
`#view-signin` starts `display:none` (`:1869`), and `runConsentFlow()` calls `disableAuthorize()`
first. **The transient discriminator is load-bearing:** a clean load with no `code`/`error` and a
null session must show `#view-signin` **with the error hidden** — invariant 5's negative control.
`showError` stays `el.textContent` (verified: 0 `innerHTML`/`eval`/`document.write`), length-bounded,
control-chars stripped; renderer unchanged. Message wording: the site's `/auth` page phrases the
same condition (`signup.html:697`); the two are separate apps (dashboard `public/` vs the
FastAPI-rendered consent page — a shared message module is not feasible), so the separation is
recorded and the condition words kept consistent where a user may see both.

**Step 7 — item 6 write-path parity.** In `cookieStorage.setItem`: keep the strip **conditional on
`encoded.length > SIZE_GUARD`** (exactly `website/assets/supabase-session.js:111-135`; `SIZE_GUARD`
in the adapter above); port the non-essential-claim narrowing (`user.identities`; `user_metadata` reduced to
the fields the UI reads; keep `app_metadata`); keep `SIZE_GUARD + 100`; add `COOKIE_LIMIT`/`SIZE_CAP`
**derived** exactly as `supabase-session.js:46-47` (`SIZE_CAP = COOKIE_LIMIT - COOKIE_NAME.length - 1`,
text-extractable by the same regex the bridge suite uses at `:408`) and the **refuse-and-report**
branch (no write). **The landed form of the refusal differs from this sketch:** the branch
`console.warn`s AND reports on the page (`showSignin()` + `showError("Your sign-in session is too
large…")`) and returns — it does NOT read the cookie back, does NOT compare the credential pair, and
does NOT route through the Step-6 terminal state (`showTerminalFallback()` also calls
`sanitiseUrl()`; this branch never does). The bridge's `writeLanded()` read-back (#3503) was **not**
ported. Whether a refused write should render through the terminal state, and that its message
SURVIVES the consent view (a stale session currently lets `showConsentOnce()` `hideError()` it), is
#5734 (per-cause refusal UX).

**Step 8 — harness `tests/test_oauth_consent_pkce.py`.** Render with `consent_page_html(...)`
**directly** (pure function; no app boot, no fixture refactor); extract the inline block from the
**rendered** HTML by the **strict** boundary `/<script nonce="([^"]+)">([\s\S]*?)<\/script>/`
(both tags carry the same nonce, but the CDN tag begins `<script src=`, so only the permissive form
mis-matches it first with an empty body); assert a sentinel is present; assert the **extracted
inline script** (not the document) contains no `</`. Execute under Node `vm` against the
**vendored** bundle with a DOM/`fetch`/storage shim; assert on JSON stdout. Shim surface:
`Headers`, `Request`/`Response`, `FormData`, `Blob`, `WebSocket`, `TextEncoder`/`TextDecoder`,
`btoa`/`atob`, `crypto` (incl. `subtle`; **and a no-`subtle` mode `{ getRandomValues: fn }`**),
`AbortController`, `navigator` (incl. `locks`), capturable `location`,
a **recording** `document.cookie` jar keeping a **raw per-assignment write log in the
`{header, dropped, removed}` shape** — the shape `website/apps/dashboard/src/supabaseSessionBridge.test.js:104-133`'s
`makeCookieJar()` already produces (one record shape, cited precedent), `sessionStorage` +
`localStorage` (working, method-throwing, **and access-time-throwing**), `history.replaceState`,
`getElementById` stubs with a **stateful `classList`**, routed `fetch`. **Shim contract:**
`window === globalThis`, and the `crypto` object the bundle reads (bare global) is the **same**
object the page's guard reads (`window.crypto`). Toolchain: reuse the repo's **FAIL-by-name**
contract (`_require_node`, #3786, defined in `tests/test_session_bridge_fragment_retention.py:254`)
— never `pytest.skip`. The Node child runs under a **hard timeout**
(`subprocess.run(..., timeout=…)`, the only Node precedent, `:291`; the repo has no `reruns` config,
so a timeout is a hard failure) so an inverted-guard mutation surfaces as a bounded failure.

**Step 9 — CI wiring.** (a) register the harness in `config/ci-surfaces.yml` under `api`
(`tortoise/oauth.py` is in `SOURCE_PATTERNS["api"]` + `CORE_ALSO`, so an oauth-only change selects
it; a new `tests/test_*.py` otherwise trips `unlisted_tests`). (b) add
`website/apps/dashboard/public/vendor/` to `SOURCE_PATTERNS["api"]` in `tools/ci_selection.py` — the
`#1349/#3332/#3616` silent-drop class; **the precedent entries live in `onboarding`, and this entry
goes in `api` because the pin tests are api-registered** (executed: unpatched → `surfaces=[]`;
patched → `['api']`; `oauth.py` → `['api','core']`; the derived ratchet
`test_every_source_pattern_is_selectable` auto-passes and no test pins the vendor path to
`surfaces=[]`).

**Step 10 — extend existing suites.**
(a) `tests/test_oauth_mcp.py::TestAuthorizePage`: textual pins for the genuinely textual (explicit
`flowType: "pkce"`, `SIZE_CAP`, the literal `@2.112.2` specifier) **plus the derived version
assertion** (Step 1). The `authorizeReturnTo` **presence** pin is demoted to a secondary name check —
the behavioural proof of A2/A4 is **invariant 11**, not a grep (a presence pin cannot discriminate
an emptied strip list or a helper that stopped being called).
(b) `tests/test_cross_subdomain_cookie_sync.py`: **first extend `_extract_fn_body`'s optional sigil
group to accept `:\s*(…)`** (backward-compatible: covers `name: (k, v) => {` and
`name: function (k) {`) so there is **one** body extractor for both the JS-method and TS-arrow
forms — do **not** fork a second extractor. Then a **per-method, polarity-normalised, exactly-one**
static predicate assertion, **read path excluded**:
  - **oauth.py `setItem` / `removeItem`** (via the extended extractor), with a **brace-matching**
    locator (not `[^}]*return`):
    `assert re.findall(r"\bkey\s*(===|!==)\s*([A-Za-z_$][\w$]*)", body) == [("!==", "COOKIE_NAME")]`
    and assert the `document.cookie` assignment is **dominated by the fall-through** — reachable only
    when the comparison is false, never inside the aux branch. *(Exactly-one, not ∀: a guard-less
    `removeItem` has comparison list `[]` and must fail.)*
  - **a method-scoped negative check on the `setItem` AND `removeItem` bodies of both copies** for a
    **non-comparison** test on `key` — `.endsWith(`, `.startsWith(`, `.slice(`, `.charAt(`,
    `.includes(`, `RegExp.test(key)`, `.match(`, `typeof key`, `key[` — scoped to the bodies so a
    comment is not punished. *(Cycle-4 adversarial: a `key.startsWith(COOKIE_NAME) && key !== COOKIE_NAME`
    router has exactly one `key`-left comparison and would pass the exactly-one assertion.)* *(A `key.endsWith("-code-verifier")`
    router adds no `key`-left comparison and passes the spelling assertion; on oauth.py invariants 3/4
    bound the behaviour, but blog-admin's static anchor is its only bound, so the removal path must be
    covered too.)*
  - **blog-admin `authStorage.setItem` / `removeItem`** via the same (extended) extractor: `setItem`'s
    only comparison is `key !== STORAGE_KEY` with the cookie write outside it; `removeItem`'s only
    comparison is the **positive** `key === STORAGE_KEY` with `clearCookie()` inside it.
  - `getItem` is **explicitly excluded** from the "only comparison" claim (oauth.py's cookie-loop
    `p.slice(0, eq) === key` is not a session-key comparison); its routing is bounded only by the
    session-key-read invariant.
  Define "session-key comparison" as *a comparison whose left operand is `key` against the constant*.
  Add the **`COOKIE_LIMIT`/`SIZE_CAP` derivation agreement** assertion for oauth.py (mirroring
  `tests/test_session_bridge_fragment_retention.py:408`) **without** re-asserting the bridge's own
  derivation.
(c) `tests/test_session_bridge_fragment_retention.py`: it reads `tortoise/oauth.py` as text
(`:80`, `:493`) and is the home of `_require_node` the harness imports. It is **`onboarding`-only**
in `tools/ci_selection.py`, so it does NOT join the affected-tests list when `tortoise/oauth.py`
changes — the `api`-registered harness (Step 8) is what actually runs on an oauth change, and it
exercises the same page. Its assertion MESSAGE still states the pre-#3496 fragment-ingestion
rationale; that text is stale but sits outside this PR's diff.

**Step 11 — the docs passage.** Append to the `- **OVERRIDES:**` bullet at
`docs/auth-architecture.md:93-98` per §1 above (continuation sentences only — no new list item at
any indent, no blank line, no heading, no table row; page-scoped wording; delete the stale
line-number parenthetical rather than correcting it). Add `tests/test_no_legacy_token_path.py` and
`tests/test_ci_selection.py` to the affected-surface list.

### 4. Test invariants (all observable behaviour, executed against the real library)
1. **Grant type actually used** — `signInWithProvider('github')` yields an authorize URL with
   `code_challenge` + `code_challenge_method=s256`; **paired negative control**
   (`flowType:'implicit'`) yields **no** `code_challenge`. *Secure-context only; the no-WebCrypto
   case is invariant 9.*
2. **Single origin (#1566 guard)** — the return target the page **builds at the callback origin**
   (`authorizeReturnTo()`) has the page's own origin+path, with a **paired control** that makes it a
   foreign host; plus the exchange POST being `grant_type=pkce` and carrying a `code_verifier`
   equal to the stored value. **Observed scope:** the harness shims the DOM/stores/`fetch`, so this
   is *the page's* behaviour — the initiation half (the `redirect_to` supabase-js puts on the
   authorize URL) is observed by inv 11's parse of the assign URL. An earlier revision read
   `location.origin` back from the shim, which asserted the shim against itself and stayed green
   with the page returning `https://evil.example`; that is corrected.
3. **Key-identity routing (set)** — verifier **absent from `document.cookie`**, present in
   `sessionStorage`; a session-key read still returns the pre-seeded cookie byte-identically
   (#1704); **`${KEY}-user` and a synthetic `${KEY}-unknown-aux` are written through the adapter and
   produce NO `document.cookie` write** — those two non-verifier shapes are what actually
   discriminates an allowlist from a denylist, since a suffix matcher routes verifier-shaped keys
   correctly by construction, and the cookie log is read by **key identity**, not a name substring.
4. **Removal path** — seed a **structurally invalid** session
   (`'{"access_token":"a"}'` — missing `refresh_token`/`expires_at`, which `_isValidSession`
   requires; an **expired but well-formed** session is NOT removed) **together with a verifier**, so
   the aux-store assertion can fail (without the seed the stores start empty and the assertion held
   whether or not the removal cleared them). Assert on the **raw per-assignment write log**: "the
   **only** `document.cookie` assignment is the session-key expiry; **no** assignment names a
   non-session key" (co-assert the final-state jar). The Node child is timeout-bounded (the inverted
   guard loops `_removeSession`).
5. **Terminal state exactness + negative control + message bounds (A3)** — (positive)
   `#view-signin.style.display === "block"` **and** the error element's `classList` **contains**
   `"visible"` with the message, for both `?error=access_denied` and a failing `?code=`; (negative) a
   **clean** load (no `code`, no `error`, null session) renders `#view-signin` visible with the error
   element's `classList` **not** containing `"visible"`. **Message bounds are asserted:** the
   `boundedText(s)` spec is `[\u0000-\u001F\u007F-\u009F\u2028\u2029]` stripped and truncation to
   **300 chars** (appending `\u2026` when truncated; the bound is **inclusive** of the ellipsis, so
   the natural `slice(0, 300) + "..."` is a false red — use 299 + `\u2026`, and assert `≤ 300`).
   **The control characters sit INSIDE the 299-char window** (at the tail, the bound alone removes
   them and a test that cannot tell the strip from the bound passes with the strip deleted).
   **Paired mutations:** unbounded, and strip-removed, each redden the test. **The fragment channel
   is the same terminal state:** `#error=access_denied&error_description=…` shows the message and is
   sanitised out of the rewritten URL, with a **benign-fragment control** (`#section-2`) that is left
   alone and shows no error. (The message is displayed verbatim, so a crafted `error_description`
   can *spoof the copy* — this row is about **reflection, not injection**, and the spoofing surface is
   named out of scope in §8.)
6. **Store unavailable** — **method-throwing AND access-time-throwing** both stores ⇒ the
   pre-flight guard shows the terminal state **locally**, **no authorize navigation is issued**, and
   **no verifier key is written anywhere**; plus the belt-and-braces mid-flow case (never an
   unhandled rejection).
7. **Item 6** — over-`SIZE_CAP` refused (no cookie) and page-reported; over-`SIZE_GUARD` written
   with the strip; **≤ `SIZE_GUARD` written byte-identically**; **`COOKIE_LIMIT`/`SIZE_CAP` agree
   with `website/assets/supabase-session.js` and are derived the same way**.
8. **Version coupling** — page specifier == vendored filename version == executed bundle; runs in
   **both** directions (a vendor-only PR selects `api` via Step 9(b)).
9. **WebCrypto absent** — with `crypto` = `{ getRandomValues: fn }` (no `subtle`) the page does
   **not** initiate (no authorize URL assigned) and drives the terminal state; the **guard-removed**
   control asserts the URL **does** become `code_challenge_method=plain`.
10. **Aux-scope** — `${KEY}-code-verifier` is written **only** to `sessionStorage`/`localStorage`;
    with both stores unavailable the write is refused and the cookie jar receives **no** non-session
    key.
11. **Return-target canonicalisation (A2 + A4)** — with a hostile initiating search
    `?client_id=x&redirect_uri=<registered>&code=STALE&error=access_denied&error_description=…&sb_flow_id=<id>`,
    parse the **nested** target the page hands to `signInWithOAuth`'s `redirectTo` (equivalently, the
    `redirect_to` param of the `window.location.assign` URL — the assign URL's *own* origin is
    Supabase's and its search carries the transients percent-encoded **inside** `redirect_to`, so
    asserting on the assign URL directly is both a false red and a permanent false green):
    `const inner = new URL(new URL(assignUrl).searchParams.get("redirect_to"))` ⇒ origin+path equal
    the page origin + `/oauth/authorize`, and **none** of `code`/`error`/`error_code`/
    `error_description`/`error_uri`/`sb_flow_id`/`flow_id`/`type` is present. After Step 6 the
    `history.replaceState` argument is likewise transient-free. `redirectBack`'s target equals the
    server-supplied `PARAMS.redirect_uri` verbatim plus encoded params — never a
    `window.location.search`-derived value. **Paired control:** with the strip control removed, the
    inner target **does** carry `code` (mirroring inv 1 and inv 9's paired controls). The strip uses
    `URLSearchParams.delete` (not a string replace) so an encoded transient (`%63ode=STALE`) is
    removed; unknown params are preserved (an **allowlist** of the authorize params the page needs —
    `client_id, redirect_uri, response_type, code_challenge, code_challenge_method, state, scope,
    resource` — is the listed hardening if the denylist is later considered too open).
12. **Aux-store removal (A5)** — after the library's `removeItem` calls (invalid-session load and a
    failed exchange), assert the **aux stores** contain no verifier key for the removed flow, and
    that the cookie jar saw only the session-key expiry. The remaining case — a failed exchange that
    leaves *older* `${KEY}-flow-<id>-code-verifier` copies, or **all** `-flow-<id>-` copies when the
    return carries no `sb_flow_id`, plus `${KEY}-flows-code-verifier` — is a **library-owned**
    residual (R19), not a page defect, and is asserted as such. The two halves are seeded in turn —
    `sessionStorage` then `localStorage` — because "cleared from both aux stores" is only exercised on
    the non-default store by the second seed.
13. **Write-time cleanability (A5, writer half)** — the credential must reach only a store proven able
    to remove it. Pin the size-asymmetric divergence the pre-flight probe cannot see: a first store
    whose accepted-size band sits BETWEEN the probe and the real value (so the guard skips it) and
    which refuses removal must NOT receive the verifier, which must instead be relocated to the next
    store — with a paired control (both stores un-cleanable) asserting the local refusal.

**False-green mitigations:** execute don't grep; extract from the rendered artifact + sentinel;
FAIL by name without node; paired controls so the signal demonstrably discriminates; the
`-user`/unknown-key cases so a suffix denylist cannot pass; the **exactly-one** predicate form so a
guard-less method cannot pass; the **raw-assignment** log so an aux-leg-reaching removal cannot pass
on an identical final jar; the clean-load control; the **stateful `classList`** stub; the hard
timeout; the guard-removed `plain` control; the **probe payload sized to the verifier** so a
quota-truncated store cannot pass the pre-flight; the **probe key sized to the longest real verifier
key**; the **navigate-URL assertion** (not a presence grep) for A2/A4; the **aux-store assertion**
for A5; the **specified `boundedText`** with a control-char/10 kB payload for A3; the **mixed
query-transient + non-param-fragment control** so the fragment branch cannot mangle a fragment it
does not own; the **`throw-remove` store mode** so a pre-flight that accepts an uncleanable store
cannot pass; the **per-store** verifier scan (`sessionVerifierKeys` / `localVerifierKeys`), because
"no verifier anywhere" is a weaker claim than "the credential is in a store that can remove it".

**Mutation check:** each must go red when the production change is reverted (revert `flowType` →
#1; suffix router → #3; aux-leg-reaching `removeItem` → #4; drop the transient discriminator → #5;
remove the pre-flight guard → #6 and #9; restore the aux cookie leg → #3 (and inv 12 — there is no
invariant 10); bump the vendored file
without the specifier → #8; empty the strip list / stop calling `authorizeReturnTo` → #11; an aux
`removeItem` that skips a store → #12; a pre-flight that falls through to a cleaner store instead
of refusing → #6 with the `throw-remove` store; a fragment branch without the pure-param-list guard
→ #5's mixed case; a sentinel that regains the `-code-verifier` suffix → #6, since the un-cleanable
store's one leaked probe entry then reads as a credential).

### Acceptance criteria
1. The consent page's provider initiation produces a `code_challenge_method=s256` authorize URL and
   the token request is `grant_type=pkce` with a `code_verifier` (invariant 1, 2).
2. The `code_verifier` never reaches `document.cookie`; the parent-domain session cookie's semantics
   are unchanged for normal sessions (invariants 3, 4, 7).
3. A failed/declined/refused flow ends on a **visible** sign-in view with a message — never a silent
   dead end, never a spurious error on a clean load, and never a transient echoed back to the
   provider (invariants 5, 6, 9, 11).
4. The persisted artifact and the executed artifact agree on the supabase-js version, in both
   modification directions (invariant 8).
5. No verifier copy reaches the parent-domain cookie, and removed keys are cleared from both aux
   stores (invariants 3, 4, 12, 13).
6. All **13** test invariants have a mutation that reddens them (inv 11: an emptied strip list → the
   navigate URL carries the stale `code`; inv 12: an aux `removeItem` that skips one store → the
   aux-store assertion; inv 13: the writer's cleanability proof removed — and, separately, only its
   removal read-back, which is what the silent-remove store reaches — → the credential lands in the
   store that cannot remove it; inv 5: an unbounded message **and** a strip-removed message → the
   bounds assertion). **Every row of the union table in the PR body was observed red.** That table is
   the single enumeration: it is the UNION of every round's runs — each round re-ran its own rows, and
   the rows added after round 2 were re-run independently by the VGATE verifier and by the
   adversarial-coverage reviewer. It supersedes the running subtotals earlier revisions of this record
   printed (those were per-round counts, which is why they moved). The first round of this plan
   listed two rows that could not fail — inv 3's denylist row (the cookie log was filtered by name
   substring and the non-verifier keys were never written, so the assertion was empty) and inv 2's
   single-origin row (the driver echoed the shim's own `location.origin` back). Both are corrected
   above and re-verified by mutation; the rows are kept in this record because the *claim* was false,
   and a claim about a test's strength is only checkable against the test. The second review round
   added four rows for behaviours that were still unpinned (the refusal store that cannot be cleaned,
   `removeAux` skipping the SECOND store, the fragment branch without its pure-param-list guard, and
   the sentinel's suffix); the third added the writer-side cleanability proof; the fourth added its
   removal read-back as a separate row.

---

## Clarifications
none — no question qualified (research settled every decision; the contradiction test passed; the
one genuinely ambiguous item — the surface to migrate — was settled by evidence, not a human
choice).

---

## External Research (Phase 1.5 artifact)

### Axis Research
No external tool was used, and the justified-skip trigger is recorded rather than assumed:
- the governing rules are **primary texts** (RFC 10017 §7.2 / §6.3.2.1, RFC 7636), read directly;
- the library under change is **vendored in-repo** and was **executed** under Node 22 (below), so
  no vendor-documentation lookup was needed;
- the only external service fact (`GoTrue`'s redirect construction) was read from **primary
  source** (`supabase/auth` `internal/api/verify.go`, `internal/utilities/request.go`);
- there is **no SOTA/convergence claim** in this plan, so no competitor-precedent axis exists.

### Integration Docs
| Dep | Version | Verified how |
|---|---|---|
| `@supabase/supabase-js` (CDN + vendored) | `2.112.2`, now pinned | **Executed** the vendored bundle under Node 22: default ⇒ no `code_challenge`; `flowType:'pkce'` ⇒ `s256` + three verifier keys (`${k}-code-verifier`, `${k}-flow-<id>-code-verifier`, `${k}-flows-code-verifier`); `${k}-user` never written here (`userStorage` null); `_exchangeCodeForSession` removes the verifier on **both** success and failure; on a **failed** exchange `_getSessionFromURL` throws **before** its own `code`-deletion + `replaceState` (URL residue — hence Step 5; **not** a confirmed loop); `signInWithPassword` writes **zero** verifier keys; with `crypto.subtle` absent (but `getRandomValues` present) the challenge method silently becomes `plain`; with the storage adapter swallowing a write, `signInWithOAuth` still navigates and the return performs **no** exchange (`fetches: []`, `INITIAL_SESSION` null). jsdelivr serves a byte-identical body + a prepended banner (292 bytes). |
| RFC 10017 / RFC 7636 | §1, §6.3.2.1, §7.1, §7.2, §7.2.1, §8.1, §8.5, §9.4 | Primary text; two verifiers could not falsify the governing-clause ruling. |
| GoTrue redirect construction | `internal/api/verify.go` `prepPKCERedirectURL`; `internal/utilities/request.go` `GetReferrer`/`IsRedirectURLValid` | Read: `q.Set("code", code)` replaces; validation is scheme/host/port only. |
| CI selector | `tools/ci_selection.py`, `tests/test_ci_selection.py` | Executed `select()` over the three diff shapes; the derived ratchet auto-covers the new entry. |
| Consent-page gate | `tests/test_no_legacy_token_path.py` | Executed green on base; the intended passage placement was simulated (`. `-continuation in the `OVERRIDES` bullet ⇒ green; sub-bullet/paragraph ⇒ red). |
| Analysis-parity reader | `tests/test_cross_subdomain_cookie_sync.py` `_extract_fn_body` | Executed: it **cannot** parse blog-admin's TS arrow properties ⇒ extend it (Step 10(b)). |

---

## Rejected Alternatives
See the table in `### 2. Rejected alternatives`. Every row states what the alternative would be
better for.

---

## Wiring Check

| Touch Point | Type | Covered By | Status |
|---|---|---|---|
| MCP consent page inline auth client (`tortoise/oauth.py::consent_page_html`) | UI / auth | **this change** (Steps 1–7) | ✅ |
| `GET /oauth/authorize` (FastAPI, `tortoise/hosted_api.py:28395`) | API endpoint | **this change** (page render only; no route change) | ✅ |
| CSP `connect-src` for the Supabase token POST | cross-cutting | `tortoise/hosted_api.py:28455-28464` **already** emits `connect-src 'self' <supabase_origin>` — no change | ✅ |
| Parent-domain session cookie `sb-tortoise-auth-token` write path | data | `tests/test_cross_subdomain_cookie_sync.py` (extended, Step 10(b)); semantics unchanged for normal sessions | ✅ |
| Verifier storage (aux chain) | data | the router (Step 3) + harness invariants 3, 4, 12, 13 | ✅ |
| `blog-admin` adapter (the ported contract's other copy) | data | static predicate assertion (extended) — **no CI-gated suite of its own** → follow-up filed | ⚠️ (filed) |
| `website/assets/supabase-session.js` (third copy, unloaded) | data | unloaded; recorded in the contract passage so a re-arm is visible | ⚠️ (recorded) |
| Session model / `__Host-session` BFF (#3524) | auth | **not this change** — coupling comment posted on #3524 | ✅ (deferred) |
| #3501 AS-side RFC clauses | compliance | **not this change** — deferral comment posted | ✅ (deferred) |
| #3559 JS-readable token | compliance | **not this change** — the follow-up carries the blog-admin/third-copy gaps | ✅ (deferred) |
| #3495 stale PKCE comment + #1566 decision record | docs | **not this change** — different surface (dashboard bridge); interaction comment posted | ✅ (deferred) |
| #3529 (#3501's 4 untested BFF threat classes) | security tests | **not this change** — different surface (BFF/session, not the consent page) | ✅ (deferred) |
| #4678 BFF "absolute claim" comments | docs | **not this change** — its lesson bounds this passage's wording (page-scoped) | ✅ (recorded) |
| `docs/auth-architecture.md` §2.1 register | docs | Step 11 (gate-compliant placement) | ✅ |
| CI surface selection (`tools/ci_selection.py`, `config/ci-surfaces.yml`) | CI | Step 9(a)/9(b) | ✅ |
| Provider-denial / refusal UX (both Step 3b refusals) | UX | **new issue** filed; residual R14 | ⚠️ (filed) |
| GoTrue token-endpoint CORS | external service | runtime prerequisite — R1; escalate, never fall back to implicit | ✅ (named) |

No wiring gap is unresolved: every un-covered touch point is either **filed** as a new issue or
**recorded** as a deferral with a named owner (#3501/#3524/#3559/#3495/#3529/#4678).

---

## Review Cycle Log

### diff-time code review (commit-workflow Step 3, standard tier)
Seven fresh-context reviewers were dispatched against the pushed head: guidance/comments, bug scan
(two ordered passes), history+prior-PR comments, security, architecture, config/CI-wiring, and UX.
Security returned `NO ISSUES FOUND`; config/CI-wiring returned `NO ISSUES FOUND` (and measured that
the vendor entry changes exactly 1 of 5,122 tracked paths' surface resolution). The other five
returned 9 findings — **every one of them a defect in THIS PR's own test or comment artifacts, not
in the production change**:

| # | Finding | Fix |
|---|---|---|
| 1 | inv 3's `cookieAuxWrites` was unfalsifiable (substring filter + keys never written), so the denylist mutation survived | the `routing` scenario now writes `${KEY}-user` and `${KEY}-unknown-aux` through the adapter and reads the cookie log by key **identity**; the denylist mutation now reddens it (2 mutations) |
| 2 | inv 2's single-origin assertions echoed the shim back, so a foreign-origin return target stayed green | the assertion is now the return target the page **builds** (`authorizeReturnTo()`), with a foreign-host paired control |
| 3 | inv 4's aux-store assertion was vacuous (no verifier seeded) | seeded; `removeAux`-skips-a-store now reddens it |
| 4 | inv 7's `strippedHasToken` could not detect removal of the #1225 strip (the refusal masked it) | added `strippedWrites >= 1` |
| 5 | `boundedText`'s bound and strip were unpinned — removing either left 10/10 green, and the doc's A3 row claimed otherwise | both asserted, with the control characters moved **inside** the 299-char window (at the tail the bound alone removes them) |
| 6 | the terminal state read only `.search`, so a **fragment**-carried refusal still dead-ended | `loadParams()` merges both channels; `sanitiseUrl()` strips the fragment too; a fragment case and a benign-fragment control added |
| 7 | `docs/auth-architecture.md` still carried a **wrong** line anchor (`tortoise/hosted_api.py:27035`, actually `28395`) in the very bullet whose stale line numbers this PR deletes | anchored to the symbol (`oauth_authorize`) |
| 8 | `tests/test_cross_subdomain_cookie_sync.py`'s docstring still called oauth.py "a faithful inline port of the same adapter" after the routing divergence; the new test's docstring stated one operator where `_ROUTER_CASES` has two | both scoped to the session-cookie contract and to `_ROUTER_CASES` |
| 9 | `tests/test_oauth_mcp.py`'s class comment claimed the harness executes a class of behaviours it does not (the #1701 team-chooser / refresh / in-flight guard) | scoped to what the harness drives; the #1701 behaviours are named static-only |

Also corrected in the harness itself: `_run` parsed stdout with `str.splitlines()`, which splits on
U+2028/U+2029 — characters `JSON.stringify` does not escape and a URL-derived value can carry — so a
long `error_description` truncated the RESULT line and produced a JSON error pointing at the wrong
field. The parse is now an anchored regex. The fixed `sleep()` settle windows were replaced with
condition polling after one flaky failure of inv 11 under host load (1/49 runs).

### diff-time code review ROUND 2 (re-review of the fix commit `8c7df476d`)
A P1 in round 1 forces a fresh re-review. Two reviewers were dispatched against the fix commit — a
bug scan (two ordered passes) and a fresh adversarial-coverage reviewer, the latter required because
the doc declares an `### Adversarial Threat Surface`. Together they returned **5 findings, all P2,
and one reproduced in-scope gap in a declared class (A5)**. Note that round 2 found defects that
round 1's nine fixes had introduced or left:

| # | Finding | Fix |
|---|---|---|
| 10 | the new fragment branch of `sanitiseUrl()` rewrote the hash whenever it was non-empty, so a mixed URL (`?error=…#section-2`) had its benign fragment re-serialised to `#section-2=` | the branch now fires only when the fragment is a pure param list (`h.toString() === u.hash.slice(1)`); a mixed-case control asserts the fragment survives byte-identically (mutation-verified: removing the guard reproduces `['/oauth/authorize#section-2=']`) |
| 11 | A5 was genuinely unpinned: a store that accepts a write but refuses removal passed the pre-flight guard (it fell through to the *next* store), while `writeAux` wrote the real verifier to the un-cleanable one — so `removeAux` orphaned a **credential**, contradicting the A5 row and R3's "(not a credential)" wording | `pkceIncapable()` now refuses when the FIRST writable store cannot be cleaned end-to-end; a `throw-remove` store mode pins it (inv 6), inv 4 seeds the verifier into `localStorage` to pin the second store, and the probe sentinel no longer carries a `-code-verifier` suffix; R3/R20 reworded |
| 12 | the doc's `boundedText` spec carried a literal **NUL byte** (the `\u0000` in the strip class had been written as an actual control character), so the file was binary to `file(1)`/`grep` | replaced with the literal six characters |
| 13 | `tests/test_oauth_mcp.py`'s corrected comment **re-staled into the same overclaim** — it asserted a static "backstop" for behaviours (`authorizeReturnTo`, `boundedText`, `showTerminalFallback`) with no static pin anywhere | the backstop sentence is **deleted**, not reworded; the comment now states only the four shape pins that actually exist in the file and names the #1701 behaviours as static-only (`AGENTS.md` §B2c) |
| 14 | the `try`/`catch` added around the fragment parse is dead code — `new URLSearchParams(<string>)` cannot throw — and its comment named an unreachable failure mode | removed, with the reason stated inline |

The round-2 reviewer also reproduced and explicitly judged **out of scope** (recorded, not fixed):
`?type=foo` / `?sb_flow_id=x` satisfy the map's clean-load definition yet show the terminal message
(the discriminator reuses `STRIP_PARAMS`); an entirely-control-character `error_description` renders
as whitespace; and a store whose `getItem` lies is caught by `writeAux`'s verify step. VGATE re-ran
four of the round-1 mutations independently against the fix commit and confirmed each kill.

### diff-time code review ROUND 3 (re-review of `6d89cba81`)
Two fresh reviewers (bug scan two-pass + adversarial-coverage, the latter required by the declared
threat surface). Result: **the declared surface is COVERED** (14 independent mutations, including
three reverts of the round-2 fix, each reddening the named invariant) — and **two P2 findings, one of
them a reproduced production gap**:

| # | Finding | Fix |
|---|---|---|
| 15 | **The round-2 guard fix did not close A5.** The guard probes with its own key and a 160-byte payload, so it cannot know whether the store the WRITER will pick can be cleaned *for the real key and value*. Reproduced by execution: a first store that rejects the 160-byte probe on size but accepts the ~114-byte verifier **and** refuses removal → the guard skips it and accepts the next store, while `writeAux` writes the credential to the first → the credential is orphaned in the store that cannot remove it (`sessionVerifierKeys` non-empty, `localVerifierKeys` empty). The absolute claim in A5/R3/R20 ("a store that refuses REMOVAL is refused by the guard") was therefore false. | The invariant is now enforced **where the real key and value are known**: `writeAux` re-proves writability AND cleanability on each candidate store at write time, with a payload of the REAL length under a throwaway key, and only then writes the credential — so an uncleanable store never receives it. Invariant 13 pins the exact divergence (with a both-un-cleanable control); R21 records the residual (a store that discriminates by key). The doc's A5 row, R3/R20 and the Step 3b sketch now state the guard's true role (early-failure optimisation, never the invariant). |
| 16 | the doc's Step 3b sketch still showed the **pre-round-2** guard (`padEnd(49) + "-code-verifier"`, a bare `catch` that fell through to the next store) and its comment ("a remove failure is INCAPABLE, never silently skipped") was false of the code below it — a reader copying the sketch would reintroduce the hole | the sketch is replaced with the landed guard and the writer-side proof |
| 17 | the coverage map cited **inv 10**, which does not exist (the free 10th numbering slot was never written; its content is covered by inv 3 + inv 6) | the A1/A5 rows now cite the tests that exist |

### diff-time code review ROUND 4 (re-review of `7d8b79a78`)
One fresh reviewer, covering both the bug scan and admissibility (merge safety). It found **no runtime
defect** in the writer change — no inverted condition, no off-by-one, no dropped-key regression
(`writeAux`'s boolean has one caller and is discarded, and `readAux` scans both stores, so relocation
is read-transparent), no probe collision or misread (the bundle never enumerates storage keys), and no
reachable orphan path — but three findings, one of them a P1 and one of them a **false claim in the
previous round's own fix note**:

| # | Finding | Fix |
|---|---|---|
| 18 | **The writer's removal read-back was unpinned, and its `silent-remove` companion mode was dead code.** Deleting `if (s.getItem(probeKey) !== null) throw 0;` left all 11 tests green: the store mode the round-3 commit added "for it" was never instantiated by any scenario, and a bare `silent-remove` never reaches the writer at all (the guard refuses that store first — its 160-byte probe fits there and ITS read-back-null fires). The line is load-bearing only with an item-size cap: `sessionMode="silent-remove", sessionQuota=130`. Measured: pristine relocates the verifier to `localStorage`; with the line deleted the credential lands in the silent-remove `sessionStorage` (`sessionVerifierKeys` non-empty, `localVerifierKeys` empty). (The reviewer rated it P1 because the mutation survived a suite the round-3 note called 21/21.) | inv 13 now runs that exact scenario, plus a both-`silent-remove` fail-closed control; the mutation reddens it. |
| 19 | **R3 and R20 still attributed the A5 invariant to the GUARD** ("a store that refuses REMOVAL is refused by the guard") — the exact confusion round 3 existed to remove — and the round-3 fix note claimed R3/R20 had been updated when the diff shows neither was touched | both reworded: the invariant lives in `writeAux` (prove removal for the real value, per store); the guard only refuses early when the FIRST writable store is uncleanable |
| 20 | **four** `inv 10` citations survived the round-3 fix (lines 135, 542, 603 — reached then — and 524, missed): the round-3 row below claims three were replaced, which was true only of the ones it listed | all four replaced with the tests that exist (`3, 4, 12`, and `3, 4, 12, 13` for the aux-store criterion) |

### diff-time code review ROUND 5 (re-review of `61e8ad2a7`)
One fresh reviewer on the round-4 fix. It reproduced the P1 premise verbatim (the read-back mutation
SURVIVES at `7d8b79a78` and DIES at `61e8ad2a7`), confirmed the new `silent-remove` scenario reaches the
WRITER rather than the guard (the raw run shows `navs` non-empty with the credential relocated to
`localStorage`, `sessionVerifierKeys == []`), and confirmed the both-`silent-remove` control is not a
control that passes either way (deleting the GUARD's read-back-null reddens it; so does
`pkceIncapable() → null`). Two findings, both record-level, both fixed:

| # | Finding | Fix |
|---|---|---|
| 21 | the mutation total was not reconcilable with the table it pointed at: the PR body at that head still said `20/20` with no invariant-13 row, so "22/22 (the table is in the PR body)" was unsupported in both directions — the same class of self-referential count that had already moved `16 → 20 → 21 → 22` | the PR body carries **the single enumeration** (29 rows, every one observed red, marked with whether the later rounds re-ran it independently) and the doc no longer prints an aggregate subtotal; the historical numbers are kept only as history, with the reason they moved |
| 22 | a FOURTH `inv 10` citation (`restore the aux cookie leg → #10`) had survived the previous round, so that round's "three citations" claim was false by omission | `→ #3 (and inv 12)`, and the round-4 row now says four |

No P0/P1 remained at the end of the round: the two findings were P2 record defects. The reviewer
also judged non-defects, with evidence: the A5/A1 coverage rows under-cite (they do not spell out the
`silent-remove` shape) but their requirement text is satisfied by the code; R3/R20 are now accurate
against the landed script; and the harness module docstring's "Invariants pinned here" list is
pre-existing non-exhaustive.

Also noted, not defects: the first batch invocation of the router mutation once reported GREEN and was
not reproducible in 6 further pytest runs plus 8/8 direct driver iterations (all correctly
vulnerable) — recorded because a security harness that can false-green is worth watching if it
recurs; and a cookie jar that silently drops a write whose *encoded length is ≤ SIZE_CAP* (the harness
cannot model one) is outside A1–A7. The adversarial reviewer's key-prefix divergence is now R21.

---

**problem-verify** (2 problems-diverge / 2 problems-converge / 4 cycles × 2 verifiers):
cycle 4 `GATE: PASS` (both). Amendments A1–A8 + B1–B3 + C1–C7 recorded in
`/tmp/3496-converge-rev{2,3,4,5}*.md`.

**solution-verify** (4 cycles × 2 verifiers + 1 advisory duplication-architecture reviewer):
| cycle | verifier-1 | verifier-2 | duplication-architecture (advisory) | controller action |
|---|---|---|---|---|
| 1 | ISSUES (1 P1) | ISSUES (1 P1) | ISSUES (3 P1 / 2 P2) | fixed all 4 P1 + 6 P2/P3 → rev 2 |
| 2 | ISSUES (1 P1) | **CLEAN** | ISSUES (2 P1 / 3 P2) | fixed 3 P1 + 6 P2/P3 → rev 3 |
| 3 | **CLEAN** | **CLEAN** | ISSUES (1 P1 / 3 P2 / 1 P3) | fixed 1 P1 + 10 P2/P3/P4 → rev 4 |
| 4 | **CLEAN** | **CLEAN** | ISSUES (0 P0/P1; 1 P2 / 1 P2 / 2 P3) | incorporated P2/P3 → rev 5 (this doc) |

Remaining cycle-4 items (all incorporated): the docs target must name the `OVERRIDES` bullet
exactly and state the unit-split constraint; the stale line-number parenthetical is **deleted** (a
claim about code line numbers keeps re-staling — `AGENTS.md` §B2c); `makeCookieJar()` cite
`:104-133`; the negative check extends to `removeItem` on both copies; the brace matcher's
string-awareness constraint stated; `_extract_fn_body` is **extended** rather than forked; the
sentinel-orphan residual named in R3; `tests/test_ci_selection.py` added to the affected surfaces;
R17 states both directions.

**Phase 7 (plan review gates) consolidation.** Per the lane dispatch, Phase 7's parallel gates were
discharged by (a) the four solution-verify rounds above — which exercised the
completeness/wiring/solution-research dimensions the Codebase-&-Docs agent covers — plus the
advisory duplication-architecture reviewer (which covers Agent #1's reuse/duplication axis), and
(b) the adversarial threat-surface coverage review below (Agent #4). The diff-time `code-review`
gate is a strictly stronger, artifact-level review of the same surfaces. This consolidation is
recorded here rather than the gates being silently skipped.

### `### Adversarial Threat Surface` — coverage map (adversarial-domain acceptance)
| class | adversarial input | required behaviour | pinned by |
|---|---|---|---|
| **A1** verifier exfiltration via store routing | a verifier key + an unknown aux key + a `-user` key | never in `document.cookie`; aux stores only | inv 3, 4, 12, 13 (inv 3 writes the non-verifier keys through the adapter and reads the cookie log by key identity) |
| **A2** spent-`code` re-forwarding | a stale `?code=` on the return URL | the returned target carries no transient | **inv 11** (+ Step 10(a) textual as secondary) |
| **A3** reflected-content injection | `?error_description=<script>…` / a 10 kB payload with controls inside the bound window | rendered via `textContent`, bounded to 300 chars, stripped | **inv 5** (both mutations killed: unbounded, strip-removed) |
| **A4** open redirect | a hostile `redirect_uri`/`next`/return target | origin+path are constants; strip only reduces | **inv 11** + **inv 2** (the built return target, with a foreign-host control) |
| **A5** verifier orphaning | a store that fails mid-flow (including one that accepts a WRITE but refuses REMOVAL, and one whose accepted-size band sits between the pre-flight probe and the real value); an abandoned/failed exchange | no verifier copy in `document.cookie`; the credential is written only to a store proven able to remove it; removed keys cleared from both aux stores | **inv 3, 4, 12, 13** + R19 (library-owned copies); inv 6's `throw-remove` mode (the guard refuses a store the writer would pick but `removeAux` could not clean), inv 4's two halves (each store seeded in turn), inv 13's size-asymmetric relocation |
| **A6** silent dead-end | a failed/declined/refused flow, in the **query OR the fragment**; a benign fragment; a mixed query-transient + non-param fragment | visible sign-in view + message; no spurious error; the benign fragment is not read as a transient and not mangled | inv 5 (both channels + the benign and mixed controls), 6, 9 |
| **A7** weak-challenge downgrade | no `crypto.subtle` | refuse to initiate; never `plain` | inv 9 |

**Explicitly OUT of scope (named, not silently dropped).** These are deferred with owners in the
Wiring Check / Deferral ledger, and no claim is made that this change closes them:
- RFC 10017 §7.2 **AS** clause + §6.3.2.1 AS-enforcement half; CDN SRI (**#3501**).
- The JS-readable **session** token / §8.1 (**#3559**), and the session model itself (**#3524**).
- CSRF on `POST /oauth/consent`; consent fixation; the D1-stale CAS/read-replication precondition;
  and the BFF `next=` allowlist (**#3529** — a *different* surface: the BFF/session, not this page).
- **Server-side** `redirect_uri` validation and the registered-URI allowlist (unchanged; the page
  relies on it — see A4).
- Supabase CORS / redirect allowlist configuration (a runtime prerequisite, R1).
- The email/password fallback (verified unaffected: it writes zero verifier keys).
- XSS on the origin (a JS-readable token remains regardless — #3559).
- **Verbatim-message spoofing**: a crafted `?error_description=` is displayed as-is and can imitate
  page copy; it cannot inject (A3), and this is a *reflection/spoofing* residual, not an injection one.
- **Library-owned verifier copies** left by an abandoned/failed exchange (R19).
- **Stale "PKCE" comments + the #1566 decision record** on the *dashboard* bridge (**#3495**).
- **"Absolute claim" BFF comments** (**#4678**).

---

## Complexity
| Domain | Rating |
|--------|--------|
| Code | complex |
| Data | low |
| UX | medium |
| Compliance | medium |
| Security | complex |
| Docs | low |
| **Overall** | **complex** |

---

## Deferral ledger
- **#3501** — AS-side RFC 10017 §7.2 clause + §6.3.2.1 AS half; item 5 (SRI on the CDN script); the
  blog-admin/third-copy shared-declaration follow-up. Comment posted.
- **#3524** — the session model (consent identity bridge; the four session-flow properties under
  HttpOnly); coupling comment posted (shared region: `runConsentFlow` / `showExpiredSignin` / the
  `onAuthStateChange` region).
- **#3559** — the JS-readable session token; comment posted.
- **#3495** — the dashboard bridge's stale "PKCE" comment + the #1566 implicit-vs-PKCE **decision
  record**. Interaction comment posted (this change narrows #3495's ask #1 to the dashboard surface
  and supplies the evidence for its ask #2: nothing about PKCE had to change, only supabase-js's
  default verifier storage).
- **#3529** — #3501's 4 untested BFF/session threat classes (different surface from A1–A7).
- **#4678** — BFF "absolute claim" comments; its lesson bounds this passage's wording.
- **New issue A** — provider-denial / refusal UX (the two Step 3b refusals + R14 + the
  session-too-large message): context, options, recommendation.
- **New issue B** — blog-admin's adapter has no CI-gated suite and is outside the parity completeness
  scan (the shared-declaration follow-up's evidence).

## Risks & residuals
R1 GoTrue token-endpoint CORS refusal — hard external dependency; **escalate, never fall back to
implicit**. R2 cross-context verifier loss (mobile/in-app hand-off) — lands on the terminal state.
R3 **no aux store** (including the access-time-throw and quota classes the sized probe covers) **or no
WebCrypto ⇒ the pre-flight guard refuses locally and sign-in cannot complete**; a mid-flow write
failure after the probe passed (a race) costs a round trip ending in the generic terminal state; a
store that TRUNCATES the write leaks one `__tt_probe-*` entry per attempt (harmless: the probe key
deliberately carries no `-code-verifier` suffix, so it cannot be mistaken for a credential, and the
real verifier is never written there because `writeAux` proves the write and its removal for the
real value first); a store that refuses removal **never receives a credential** — that is enforced by
`writeAux` (write → read back → remove → read back null, per store, for the real value), NOT by the
guard, which only refuses early when the FIRST writable store is uncleanable (inv 6). R4 §8.1 not closed. R5 shelf life (#3524) — the trigger that would flip
the verdict is an open PR on #3524 deleting the inline client. R6 version drift — test-enforced in
both directions. R7 item 6 changes the shared cookie's write semantics — gated, so normal sessions
are byte-identical. R8 the parity suite may trip on the refactor — the Step-3 constraint keeps it
green without weakening. R9 legacy `${KEY}-user` cookie ignored — unreachable. R10
`_recoverAndRefresh` → `_removeSession` emits several removals — aux removals clear the aux stores
only. R11 #3496's own "already implements step 4" claim is false — correcting comment posted.
R12 item 5 not in this diff. R13 the `docs/auth-architecture.md` line-number parenthetical is
deleted by this change. R14 the two Step 3b refusals → the new UX issue. R15 oauth.py's aux
destination deliberately differs from blog-admin's — recorded in §2.1. R16 the three
blog-admin/third-copy gaps — the follow-up. R17 the static predicate assertion pins the two copies'
*spelling*: a semantically-equivalent rewrite reddens it (a change to the boundary must update the
assertion), **and** a suffix/pattern test on `key` would slip past it (bounded on oauth.py by
invariants 3/4, on blog-admin by the method-scoped negative check). R18 the docs region is scanned
by `tests/test_no_legacy_token_path.py`; the passage appends to an already-classified
`OVERRIDES`-marked bullet, but the **claim unit** must not be split by a new list item/blank
line/heading/table row. R19 **library-owned verifier copies** — a failed or abandoned exchange can
leave older `${KEY}-flow-<id>-code-verifier` copies (or all of them when the return carries no
`sb_flow_id`), plus `${KEY}-flows-code-verifier`; these are single-use, tab-scoped, and unusable
without the code, and are supabase-js's behaviour, not this page's (invariant 12 asserts the page's
half). R20 the probe's sentinel is LONGER than the longest real verifier key and deliberately carries
no `-code-verifier` suffix, so an item-size cap cannot slip through while the one entry an
un-cleanable store leaks cannot be mistaken for a credential; the verifier is never written to an
un-cleanable store because `writeAux` proves removal for the real value first (the guard refuses the
FIRST uncleanable store early — inv 6's `throw-remove` mode — and inv 13 pins the relocation for the
size-asymmetric and silent-remove cases the guard cannot see).

---

## Execution record (as-landed)

Landed files and what each carries. Evidence is stated as a command → observed result.

| File | Change |
|---|---|
| `tortoise/oauth.py` | CDN specifier pinned to `@2.112.2`; key-identity router (`getItem`/`setItem`/`removeItem` + `auxStores`/`readAux`/`writeAux`/`removeAux`); `flowType: "pkce"`; `COOKIE_LIMIT`/`SIZE_CAP` derived cap with a page-visible refusal; item-6 claim narrowing; `authorizeReturnTo()`; `pkceIncapable()` pre-flight guard; one terminal state (`showTerminalFallback` + `sanitiseUrl`) |
| `tests/test_oauth_consent_pkce.py` | **new** — behavioural harness: renders the page with the pure renderer, extracts the inline script from the RENDERED HTML (strict nonce form + sentinel), executes it under Node `vm` with a DOM/storage/fetch shim against the VENDORED bundle. 11 tests, invariants 1–13 |
| `tests/test_oauth_mcp.py` | exact-semver shape assertion on the CDN specifier; `flowType: "pkce"`; `SIZE_CAP`; the "no jsdom harness" class comment corrected |
| `tests/test_cross_subdomain_cookie_sync.py` | `_extract_fn_body` sigil group extended (strict superset: TS `prop: (k) => {` now parses); allowlist-of-one predicate assertion for `setItem`/`removeItem` on oauth.py AND blog-admin; method-scoped string-shape denylist; `COOKIE_LIMIT`/`SIZE_CAP` derivation agreement |
| `tools/ci_selection.py` | `website/apps/dashboard/public/vendor/` → `SOURCE_PATTERNS["api"]` so a vendor-only bump selects the surface that runs the version pin |
| `config/ci-surfaces.yml` | `test_oauth_consent_pkce.py` registered under `api` (+ duration) |
| `docs/auth-architecture.md` | continuation sentences on the §2.1 `OVERRIDES` bullet recording the storage-adapter routing predicate, the shared routing contract, the deliberately-different destination and what is being overridden; the stale/self-contradictory line-number parenthetical (and the two inline line refs in the legacy-cohort bullet) deleted and anchored to the symbol instead |

**Verification (all run at base `5b6cb9367` + this diff).**

- `TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest tests/test_oauth_consent_pkce.py tests/test_oauth_mcp.py tests/test_cross_subdomain_cookie_sync.py tests/test_session_bridge_fragment_retention.py tests/test_no_legacy_token_path.py tests/test_ci_selection.py -q` → **341 passed, 2 xfailed**.
- `… pytest tests/test_from_uri_userinfo.py tests/test_harness_mcp_config.py tests/test_mcp_route_challenge.py tests/test_oauth_token_fault.py tests/test_3036_oauth_retention.py test_attribution_actor.py test_control_plane_offload_3498.py test_oauth_redemption_state.py test_user_identity_authority.py -q` → **413 passed**; the 4 reds in that batch (`test_mcp_route_challenge::test_unknown_credential_carries_challenge[tt_deadbeef]`, three in `test_cursor_mcp_exit_evidence.py`) are **reproduced on a clean `origin/main` worktree** — they are the embedded FalkorDB single-writer contention (`Embedded store busy: … is held by a live process`), not this diff. Separate failures, different identities on re-run, so not deterministic under this change.
- `ruff check .` → **All checks passed** (CI pins `ruff==0.16.4`).
- Mutation evidence: **the union table in the PR body — every row observed red, 0 survived.** The set now includes
  the two invariants the first round could not redden (inv 3's denylist row, inv 2's single-origin
  row), the `boundedText` bound and strip separately, the fragment channel, the #1225
  provider-token strip, and six rows added by the later review rounds: the pre-flight falling
  through to a cleaner store instead of refusing (inv 6, `throw-remove`), `removeAux` skipping the
  SECOND store (inv 4's `localStorage` half), the fragment branch without its pure-param-list guard
  (inv 5's mixed control), the sentinel regaining its `-code-verifier` suffix (inv 6), the
  writer's cleanability proof reverted (inv 13 — the credential then lands in the store that refuses
  removal, which no earlier row could see), and that proof's removal read-back deleted alone, which
  the SILENT-remove store reaches (inv 13's third scenario — the row the round-4 reviewer found
  surviving). Earlier revisions of this line printed a running subtotal (`16/16`, `21/21`, `22/22`);
  those were per-round counts of a table that grew, which is exactly the kind of self-referential
  number that re-stales, so the count is now defined only as the PR-body table's own row count. The strip
  mutation is what surfaced that `strippedWrites` had to be asserted before `strippedHasToken`
  (without it, removing the strip fell through to the refusal and the assertion was vacuous).
- Wiring: `select(["website/apps/dashboard/public/vendor/supabase-2.112.2.min.js"], "pull_request", manifest)` → `surfaces == ["api"]` (was tier-1 smoke before the entry).
