# Scoping — #4178: blog-admin data layer off the legacy session cookie

**Issue:** #4178 (`complexity:standard`) · **Parent epic:** #3501 · **Tier:** Standard
**Branch:** `fix/4178-blog-admin-data-layer` · **Record of the decision it implements:** `premise-labs:engineering/auth/SCOPE.md` §4 W2, §7, §12
**Status:** scope — revised after the problem-verify gate (findings F1–F10 applied)

---

## Phase 0 — Tech-debt pre-flight

No blocking tech debt in the touched surfaces. One pre-existing gate weakness is repaired here
because this change is what flips it — and the flip is **not** what a first reading suggests
(corrected by the problem-verify gate, F1):

- `tests/test_no_legacy_token_path.py` — `test_client_data_layer_does_not_import_the_legacy_supabase_client`
  (decorator `:323`, `def` `:324`) asserts the data layer does not import the legacy client. **This is
  the marker that goes live**, keyed on the import at `blog-api.ts:16`.
- `test_no_legacy_js_readable_token_anywhere` (decorator `:177`, `def` `:178`) **stays xfailed.** It
  scans every browser source (`_browser_sources()` — 177 files under `website/`), and **three of its nine**
  marker-offenders are cleared by this change: `supabase.ts:6` and `supabase-auth-storage.test.ts`
  (commit 1, both deleted), and `functions/blog/_shared/admin-auth.ts:26` (commit 2). **Six remain in
  three files** — the retained bridge and the #3559 dashboard surfaces — so the marker cannot go
  live here. Removing it would convert a passing suite into
  a failing one that this change cannot fix.

## Phase 1 — Problem diamond (diverge)

### The problem as the issue states it

> "its 11 direct PostgREST calls and 3 Storage calls still use the legacy JS-readable
> `sb-tortoise-auth-token` cookie … So the console loads but its **data surface is
> unauthenticated** (RLS denies writes / returns published only)."

### Correction — the stated problem is not the actual problem

**The data surface is not unauthenticated, and there is no legacy *cohort* that reaches it.** Both
halves of the issue's framing are wrong, and the truth is smaller and sharper (F2, F4).

**Reachability first.** The console is *only ever served* by the BFF gate: `admin/[[path]].ts:289`
reads `readCookie(request, SESSION_COOKIE)` (`__Host-session`) and answers 403 for a non-admin
(`:257`), and the SPA redirects when there is no session (`App.tsx:125`). A user holding the
MCP-consent cookie but **no BFF session cannot open the console at all.** So:

- **The legacy cookie is not a population's transport — it is a second, non-revocable credential
  that a BFF admin may additionally hold.** It is not an alternative to the session; it rides
  alongside it.
- The console's server gate already resolves `__Host-session`. The defect is therefore **a duplicate
  and non-revocable authorization path**, not a missing credential. Revoking the D1 session cannot
  reach a Supabase access token minted from the legacy cookie (`admin-auth.ts:202-209` refuses a
  fallback only when a BFF cookie was *presented*).

**What actually happens on a data call (mechanism corrected, F4).** supabase-js resolves its bearer
from `authStorage`, the adapter keyed on `sb-tortoise-auth-token` (`supabase.ts:48`, `:156-166`,
`:191-199`). With that cookie absent there is no session, and the client sends **both**
`apikey: <anon>` and `Authorization: Bearer <anon>` — not "no bearer". The role resolves to `anon`
either way, so the outcome the issue describes is still real, but for a different reason:

| Operation | What `anon` gets | Loud or silent |
|---|---|---|
| read (`listPosts`, `listQueue`) | policy `blog_posts_anon_read_published` (`supabase/migrations/20260827000001_blog_cms.sql:162-164`) — `status='published' AND hold_for_review=false` | **silent** — a partially-filled UI |
| read (`getPost`) | same policy, and `.maybeSingle()` (`blog-api.ts:67`) returns `null` with **no error** | **silent** |
| write (`createPost`, `updatePost`) | no anon INSERT/UPDATE policy (`:170-176`) → PostgREST `42501` → thrown | **loud** |

### A live defect the issue does not name (F6)

**The write is authorized as one identity and attributed to another.** The audit fields
`created_by` / `published_by` / `reviewed_by` are written from the **BFF** session's `user.id`
(`PostEditor.tsx:259-263` → `session.ts:59-62` via `/api/session`; `save.ts:19,32-34`;
`blog-api.ts:112,123,135`), while the RLS authorization for those same writes comes from the **legacy
cookie's** `sub`. If the two identities differ, the console attributes a publish to one admin and
authorizes it as another. This is worse than a partial read, it is not covered by the issue's
framing, and it is why the migration's acceptance must include attribution.

### The property to test

**One revocable authorization path, and the identity that authorizes a write is the identity the row
records.** The issue's acceptance set has no revocation case and no attribution case (F2, F6).

### Assumptions (each falsifiable)

1. RLS is the only thing refusing the writes. *Falsified by:* a write failing with a non-RLS error
   path. **Not yet checked** — and this is honest: acceptance case 2 ("assert a write lands") is
   itself the discharge for it, not a design input.
2. The BFF session's `sub` resolves to the same `blog_admins.user_id` as the legacy cookie's.
   **Not yet checked**, and *not* a neutral prerequisite — F6 shows its falsifier is a live
   attribution defect. It is an acceptance prerequisite **and** an acceptance case (case 6).
3. ~~No other caller depends on `blog-admin/src/lib/supabase.ts`.~~ **FALSE — corrected.** The first
   draft checked only the data layer. Six further consumers exist, and deleting the module breaks the
   SPA build, the Vitest suite, four Python guards, and a CI ratchet. Full list in the deletion
   table (see *Why the deletion is not one file*).

### Boundary

**In scope:** the console's PostgREST + Storage credential path; the browser-side reader and the
token-refresh write; the deletion of the adapter and its own test; the removal of the legacy
**cookie arm** of the server-side fallback (not the whole fallback — see the deletions table).

**Out of scope:** retiring the consent page's cookie (`tortoise/oauth.py:1445`) — a different surface
with a different owner; the dashboard's own client migration (#3559); `posts/[[path]].ts`
(`X-Agent-Key`, untouched); any change to RLS policies.

### Falsification check (replaced, F8)

The first draft tested one leg only ("does it work without the cookie"). The framing rests on a
**population** claim that no file:line can settle, so the settle must be a runtime matrix against
the **built** root (`dist/`):

| # | BFF session | legacy cookie | settles |
|---|---|---|---|
| 1 | ✗ | ✗ | is the console reachable at all without a session? |
| 2 | ✓ | ✗ | **the central claim** — the data layer's behaviour for a BFF user with no legacy cookie |
| 3 | ✓ | ✓ | the legacy cookie as an *additional* credential |
| 4 | ✗ | ✓ | reachability (F2) |

It falsifies the whole scope if cell 2 shows the data surface already working: then there is no
defect and no work. Log per cell whether each request carries `sb-tortoise-auth-token` and/or a
bearer, and the outcome of `listPosts` and `createPost`. Confidence that the defect is real: **high**
(mechanism verified in the client config; only the population claim needed the matrix).

## Phase 1.5 — External research

**Not required, and this is a judgement call, not a skipped step.** The decision this implements is
already recorded and is not reopened: the same-origin Token Handler proxy is canonical
(`SCOPE.md` §4 W6) and the move to the app origin is recorded (§12/F12). Every open question here is
local — what the proxy must forward, where the media-type gate applies, which arm of the fallback is
removable — and is answered from this repo's own code. Phases 2.5 and 5.5 remain the gates.

## Phase 2 — Problem diamond (converge)

### Confirmed problem definition

**The blog-admin console resolves its own PostgREST and Storage credential from a JS-readable
cookie, instead of consuming the `__Host-session` the BFF established. That cookie is absent for
BFF users who never visited the MCP consent page, and its absence is silent on reads (an `anon` role
sees the published subset; `.maybeSingle()` turns a denied draft into `null`) and loud only on
writes. Because the cookie is also accepted directly by the blog endpoints, it is a second
authorization path that revocation cannot close — and where its `sub` differs from the BFF session's,
a write is authorized as one admin and recorded as another.**

The property the change establishes: **the console authorizes with the BFF session, and only with
it.**

### What "done" means (acceptance)

A console signed in through the app BFF, in a browser with **no** `sb-tortoise-auth-token`:
1. lists posts and the review queue (a real read — not the published subset),
2. updates a post (a real write, `is_admin()` satisfied by the session's `sub`),
3. uploads an image (multipart) and deletes one,
4. issues **no** request carrying `sb-tortoise-auth-token`, and writes no such cookie,
5. is still refused when the session is not an admin — the migration must not widen access,
6. **attributes the write to the acting BFF user** — `published_by`/`reviewed_by` equals
   `session.user.id`, not some other admin (F6),
7. **revocation closes the path** — revoking the D1 session makes the console's data calls fail,
   even in a browser that still holds the legacy cookie (the property the whole epic is for).

## Phase 3 — Solution diamond (diverge)

### Option A — Extend the Token Handler to a Supabase-directed surface *(chosen)*

Add a same-origin proxy on the app origin forwarding to `{SUPABASE_URL}/rest/v1/**` and
`/storage/v1/**`, resolving the session from `__Host-session` exactly as the existing blog proxy
does, and setting `Authorization: Bearer <server-side token>` plus `apikey`.

Point the console's supabase-js client at that proxy base with
`persistSession:false, autoRefreshToken:false, detectSessionInUrl:false` and **no storage adapter**,
so the client holds no credential and performs no refresh.

- **Cost:** one new Function + a client-config change. The 11 operations and their filters are
  unchanged.
- **Effect:** the browser holds zero session material; the adapter and its refresh-write are deleted;
  the pattern is the recorded one.

### Option B — Replace supabase-js with server-side blog Functions

Move the 11 operations behind new `/blog/api/*` endpoints calling Supabase server-side.

- **Cost:** reimplement 11 operations including the `or=(status.eq.draft,…)` filter and the
  `Prefer: return=representation` / `Accept: vnd.pgrst.object+json` semantics of `.single()`, and
  stream a multi-megabyte binary through a Worker (the `/blog/api/**` JSON-only restriction belongs
  to a *different* route and is not the cost here).
- **Effect:** removes supabase-js from the browser entirely (stronger), but reimplements PostgREST's
  query surface by hand. Deferred on those grounds, not on convenience.

### Option C — Return the access token from `/api/session` *(rejected)*

**Rejected on the contradiction test, and this is decisive.** The recorded decision
(`SCOPE.md` §12/F12; `docs/auth-architecture.md` §2) is that the session token is **never** exposed
to JS so a script cannot read it. Handing the console the token reinstates exactly the property the
epic removes. Not a candidate — not "with a caveat".

## Phase 4 — Solution diamond (converge)

**Option A.** It implements the recorded pattern, keeps the 11 operations out of scope, and removes
the browser-side credential outright.

### The one hard constraint, and its resolution

The existing `/blog/api/**` proxy applies the **full** CSRF guard
(`blog/api/[[path]].ts:93` → `guardStateChangingRequest`), which returns **415** for any
state-changing body that is not `application/json`. The image upload is `multipart/form-data`, and
the route's upstream is `${blogOrigin}/blog/api/${rest}`, so Supabase is unreachable behind it
either way.

**Resolution — a separate route with the lighter guard.** `/api/v1/[[path]].ts:90` already
establishes the precedent: where a body may not be JSON, use `guardOrigin` (origin-only).

**The argument, stated completely.** `guardOrigin` refuses any state-changing request whose
`Origin` is not ours (`csrf.ts:100-109`) — and every browser-generated cross-site POST/PATCH/DELETE
carries `Origin`. The one cookie-bearing request shape that has **no** `Origin` is a **top-level GET
navigation** (`SameSite=Lax` sends cookies there). So the guard is sufficient **only because no
proxied operation is a safe-method mutation**: every write in the console is POST/PATCH/DELETE
(`blog-api.ts:76,87,444,477`) and there is no `.rpc(` call. **If a future operation mutates over
GET, this argument must be re-run** — the route is prefix-wide, so "no GET writes" is a property of
today's call sites, not of the route.

**And the route must be SCOPED, not a general gateway.** The existing `/blog/api/**` proxy needs no
admin check because its upstream applies one. A Supabase-directed proxy has no upstream gate, so a
prefix-wide `/rest/v1/**` + `/storage/v1/**` surface would turn the app origin into **a general
authenticated Supabase gateway for every signed-in dashboard user** — a posture change no recorded
decision covers, and the acceptance-5 case ("still refused when the session is not an admin") would
rest on RLS alone. **The route therefore carries an explicit allowlist** — the `blog_posts` table and
the `blog-images` bucket, matching exactly what the console uses — **plus an `is_admin()` check
before minting**, so a non-admin session is refused at the prox (the same posture as
`admin/[[path]].ts:257`). This is strictly narrower than the alternative, contradicts no recorded
decision, and is the least-privilege direction; it is a requirement of this scope, not a plan
choice. `apikey` must be added to the strip/override set (the built client sets it on every
request), so the anon key cannot be used to reach Supabase past the proxy.

### What must be preserved (from the call-site inventory)

- **Query strings** — all filters ride them (`select`, `id=eq.…`, `status=neq.archived`, `or=(…)`,
  `order=updated_at.desc`). Forward `url.search` verbatim, as the existing proxy does (`:143`). The
  encoded-separator rejection applies to `params.path`, not the query string.
- **`Prefer` / `Accept`** — `.insert().select().single()` and `.update().select().single()` send
  `Prefer: return=representation` and `Accept: application/vnd.pgrst.object+json`; these must reach
  Supabase unchanged. `authorization` and `cookie` must be **stripped** from client headers so a
  client-supplied bearer can never win.
- **Body streaming** — forward `request.body` for non-GET/HEAD; never buffer binary uploads.
- **The public-URL path — and the trap in it (P1).** `getPublicUrl` derives its URL from the
  **client's own base** (`${this.url}/object/public/…`), not from the imported `SUPABASE_URL`. So
  simply pointing the client at the proxy would make every uploaded cover's stored
  `cover_image_url` a **session-gated app-origin** URL — and that column is rendered on the
  **public** blog for anonymous readers (`functions/blog/[[path]].ts:53,155-156` for the hero and
  `:129,138` + `_lib.ts:308` for `og:image`). The fix must therefore keep the client's `supabaseUrl`
  the real project URL and rewrite only `rest/v1` and `storage/v1` through a `global.fetch`, **or**
  build the public URL in `uploadBlogImage` from the rehomed `SUPABASE_URL`. Acceptance case 3 must
  assert the returned URL's origin **is the Supabase project origin**, not the app origin — otherwise
  the migration silently breaks the public site.
- `deleteBlogImage`'s origin + path-prefix guard (`blog-api.ts:471-473`) must be retained in whatever
  form the delete takes, or it becomes an arbitrary-object delete.

### Deletions, with the ordering guard

`premise-labs:engineering/auth/SCOPE.md` §7 requires: **do not delete the legacy reader before its
replacement ships.** Applied here, and **corrected by the verifier on the decisive point — the
`getAccessToken` fallback is not removable, because the Bearer arm is how the migrated console
authenticates upstream** (F3):

| Change | When | Precondition / blast radius |
|---|---|---|
| `website/apps/blog-admin/src/lib/supabase.ts` — **deleted** (the adapter, `readCookie` `:65`, `writeCookie` `:104`, `createClient` `:191-199`). `SUPABASE_URL` (`:57` export; declared `:54`) must be rehomed, since `blog-api.ts:16,471` still needs it for `getPublicUrl` and the delete guard (F5) | commit 1 | the proxy surface is live and the client points at it — same PR, proxy first |
| `website/apps/blog-admin/src/lib/supabase-auth-storage.test.ts` — **deleted** (it `await import('@/lib/supabase')` and pins five adapter behaviours) | commit 1 | follows the module (F5) |
| the legacy **cookie arm** of `getAccessToken` (`admin-auth.ts:25-35`) — **only this arm** | commit 2 | **the Bearer arm (`:22-24`) and the `:209-215` legacy path MUST STAY** — the proxy strips `cookie` and sets `Authorization`, so the three blog endpoints are reached through exactly that path |
| `tests/e2e/auth/test_blog_purge_admin_gate.py` | **no change required** | its four cases drive the **Bearer** header (`_purge_with_bearer` `:215-223`) — the arm that stays. Nothing asserts the cookie arm (F3 corrected this row: the first draft ordered these assertions updated, which would have removed the only coverage of the *retained* path) |
| a NEW regression test for the removed cookie arm | commit 2 | nothing asserts it today: no test in that file sends `sb-tortoise-auth-token`. The removal must be proved, not assumed |

#### Why the deletion is not one file (F5 — my Assumption 3 was wrong)

Deleting `website/apps/blog-admin/src/lib/supabase.ts` breaks six further consumers, all of which
must move in the same commit:

| Consumer | Why it breaks | Resolution |
|---|---|---|
| `src/hooks/useAuth.ts:22` — `import { AUTH_URL } from '@/lib/supabase'` | **production import**; `tsc`/`vite build` fails | rehome `AUTH_URL` and `SUPABASE_URL` to a module that is not the legacy client |
| `src/lib/blog-api.test.ts:39-40` — `vi.mock('@/lib/supabase', …)` | mocks a module that no longer exists | repoint the mock at the new module |
| `tests/test_cross_subdomain_cookie_sync.py:98-100,116-118` — `BLOG_ADMIN = …/supabase.ts` then `_read` does `assert path.exists()` | `FileNotFoundError` in **three** tests (`:248`, `:310`, `:509`) | update or delete those cases; the adapter they pinned is gone |
| `tests/test_session_bridge_fragment_retention.py:79,483` — `BLOG_ADMIN.read_text(…)` | `FileNotFoundError` | same |
| `tools/ci_selection.py:218` — a `SOURCE_PATTERNS` entry naming `…/lib/supabase.ts` | `tests/test_ci_selection.py::test_source_patterns_all_name_something_real` (`:164-213`) **fails on a dead entry** — the file's own comment says so (`:190-191`) | **delete** the entry; keep the `blog-api.ts` / `useAuth.ts` / `admin-auth.ts` entries |
| `website/apps/blog-admin/.env.example:7,9-14` · `README.md:15` · `src/vite-env.d.ts:5` · `website/website_architecture.md:71,78` | document the module and the legacy cookie contract | update in the same commit |

Retiring the cookie arm removes **direct, non-console API access** — a caller sending the legacy
cookie with no BFF cookie and no Bearer. That is the intended effect, and it is a behaviour
removal, so it lands as its own commit with its own test.

### Wiring check

| Touch point | Change |
|---|---|
| `website/apps/dashboard/functions/sb/[[path]].ts` (**path proposed**; final name is a plan decision) | the Supabase-directed proxy — new Function on the app-origin Pages project; allowlisted to `blog_posts` + `blog-images`, `is_admin()` checked before minting |
| `website/apps/blog-admin/src/lib/supabase.ts` + `supabase-auth-storage.test.ts` | **deleted** |
| `website/apps/blog-admin/src/lib/blog-api.ts` | client construction + the import site at `:16`; the 11 operations' *queries* are untouched, but `uploadBlogImage`'s returned URL is not — the client base must keep the project origin (see the `getPublicUrl` trap above) |
| `website/functions/blog/_shared/admin-auth.ts` | the cookie arm only (commit 2) |
| `tests/e2e/auth/test_blog_purge_admin_gate.py` | update the legacy-bearer assertions (commit 2) |
| `tests/test_no_legacy_token_path.py` | the **import** check goes live; the js-readable-token xfail **stays** |
| `website/apps/blog-admin/src/vite-env.d.ts` · `README` | the env contract delta (F7) |
| `tools/ci_selection.py` (**not** `config/ci-surfaces.yml`, which maps test-file→surface) | **delete** the dead `SOURCE_PATTERNS` entry for `…/lib/supabase.ts` (`:218`); add an entry for the new route only if it lands outside `website/apps/dashboard/functions/` (already covered by the directory entry at `:192`) |
| `docs/auth-architecture.md` §2.1 · `website/website_architecture.md` | the legacy-cohort note is corrected again: the *console* stops resolving the legacy credential; the consent page still issues it |
| `SCOPE.md` §4 W2 | marked done, with what W6's pattern now covers |

### Env contract after the change (F7)

- **`VITE_SUPABASE_URL`** — still required in the browser: `getPublicUrl` builds the public URL from
  it and `deleteBlogImage` uses its **origin** as the arbitrary-object guard. Unchanged.
- **`VITE_SUPABASE_ANON_KEY`** — after the change it has **no browser purpose**: its consumers are
  `supabase.ts:55,59,61,191` (including the `if (!SUPABASE_URL || !SUPABASE_ANON_KEY) throw …`
  guard at `:59-63`, which must be rewritten if the var is dropped), plus `.env.example:7`,
  `README.md:15`, `vite-env.d.ts:5`. All four move with it. `createClient` still requires a key argument, so the choice must
  be stated and is a decision for the plan: either pass a placeholder and strip it server-side, or
  drop the var and update `vite-env.d.ts` + the README. **`VITE_SUPABASE_ANON_KEY` must not remain a
  load-bearing requirement** in either case.

### E2E

Key journey only (standard tier), against the **built** root (`dist/`): the 2×2 falsification matrix
in Phase 1 as the diagnostic, plus the seven acceptance cases — including the two the issue's own
framing omits (attribution, revocation) and the negative case (non-admin still refused).

## Phase 5 — Complexity ratings

| Component | Rating | Why |
|---|---|---|
| New Supabase-directed proxy route | **standard** | new auth-bearing surface; header/query/body forwarding; a guard choice with a security argument |
| Client rewire + adapter + its test deleted | **standard** | removes a credential path; must not leave a second reader behind |
| Legacy cookie arm retirement | **standard** | a deliberate access removal on a shared module, with CI assertions that currently assert the opposite |
| Static-gate conversion (import marker → live) | **micro** | mechanical, but wrong-once: the wrong marker reddens CI |
| E2E + the 2×2 matrix | **standard** | two origins, a real session, a multipart upload, a revocation case |
| **Overall** | **standard** | consistent with the `complexity:standard` label |

## Residuals / out of scope

1. **Retiring the consent page's cookie** (`tortoise/oauth.py:1445`) — the issuer, not the reader.
   The console stops resolving the legacy credential here; the browser still holds it until that
   change lands, so **this issue does not close the exposure it is named after.**
2. **`/blog/api/**`'s open prefix** — the proxy forwards any path under the prefix. Not introduced
   or worsened here; noted for separate hardening.
3. **Option B** (no supabase-js in the browser) — deliberately deferred, and the deferral has a real
   price worth recording: under Option A the app origin keeps an authenticated Supabase surface
   (scoped, per the allowlist above) that Option B would not have. That least-privilege delta is
   accepted here, not overlooked.
4. **`purgePostCache`'s silent fail-open** (`blog-api.ts:330-332`) and the two generation calls
   surfacing `error`/`detail` as a generic message — pre-existing UX defects on the now-correct
   transport; not repaired here.
