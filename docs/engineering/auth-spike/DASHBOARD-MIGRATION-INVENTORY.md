# #3501 — dashboard (`main.jsx`) migration inventory

**Status: NOT STARTED.** A partial attempt was made and **deliberately reverted** (see below).
Everything here was enumerated by reading the file; it is a checklist, not a plan.

## Why the first attempt was reverted

An edit pass converted `API_BASE`, removed all three `Bearer ${sessionTokenRef}` sites, and
switched one gate to `/api/session`. On inspection that left the file **half on the old model
and half on the new** — `sessionTokenRef` set but `session` undefined — because the following
are all still live and all depend on a client-side session that no longer exists:

- 3 further `supabaseClient.auth.getSession()` sites (2236, 3181, 3354)
- `session.access_token` threaded into real logic (`acceptStashedInvite`, `performClaim`,
  and a raw `fetch` at ~3508)
- 10 `session.user.*` usages, including `user_metadata.display_name`

**A half-migrated dashboard is strictly worse than an unmigrated one.** It was reverted so the
dashboard keeps working on the current model until this lands as one coherent unit.

## Prerequisite — DONE

`/api/session` now returns `{ user: { id, email, displayName }, expiresAt }`.
The D1 row stores no profile data (§8.1 excludes `user_metadata`), so the BFF asks GoTrue with
the token it holds. A profile-lookup failure is **non-fatal** — identity degrades to id-only;
it must never sign the user out over a cosmetic lookup.
Verified by `tests/auth/test_bff_flow.py::test_session_contract_includes_profile_for_the_chrome`.

## Prerequisite — DONE

The W6 proxy exists: `functions/api/v1/[[path]].ts` (+ `_shared/auth/token.ts`).
Verified by `tests/auth/test_proxy.py` (6 tests).

## Change list

Every item is required; doing a subset reintroduces the revert condition.

1. **`const API_BASE = '/api'`** (was `'https://api.premiselabs.co'`).
2. **Remove all `Authorization: Bearer ${sessionTokenRef.current}`** constructions. A
   client-supplied Authorization is stripped by the proxy anyway; leaving it implies the
   browser holds a credential it does not.
   Sites: ~1503 (dashboard-login PATCH), ~1874, ~1889.
3. **`sessionTokenRef` becomes a PRESENCE sentinel.** Keep the ref and every
   `if (sessionTokenRef.current)` gate working by assigning a constant (e.g. `'session'`)
   instead of a token. Sites: the mount gate, the claim gate, the `onAuthStateChange` handler,
   and the Round-4 reset at ~4006 (that one stays `null`).
4. **Replace 4 × `supabaseClient.auth.getSession()`** with a `/api/session` read.
   Sites: ~2236, ~2448, ~3153, ~3354.
   **Keep 401 and 503 distinct** — `unavailable` must NOT bounce to `/auth`. That is the
   `#3485` loop, and it is the single most likely way to reintroduce the bug here.
5. **Replace the `onAuthStateChange` subscription (~3387).** There is no client-side session to
   change. Re-check on `window.addEventListener('focus', …)` instead.
6. **Re-plumb the `session.access_token` consumers** to the proxy:
   - `acceptStashedInvite(token)` — drops its token argument
   - `performClaim(token, key)` — drops its token argument
   - the raw `fetch` at ~3508 with `Authorization: Bearer ${session.access_token}`
7. **Replace 10 × `session.user.*`** with the `/api/session` profile
   (`user.email`, `user.displayName`, `user.id`). `sessionMetaRef` is the natural seam.
8. **Verify**: `npx vite build` (there is no `tsc` on this app — build catches syntax only, so
   review the logic diffs by hand), then the clickthrough.

## Verification gate

There is **no automated clickthrough for the dashboard** yet. One must be written before or
with this migration — otherwise the only evidence is "the build passed", which would not have
caught the `welcome.html` unconditional-redirect bug found earlier by a real browser.

Suggested shape: reuse `tests/e2e/test_auth_bff_clickthrough.py`'s harness, assert the
dashboard renders authenticated chrome, and assert **zero** `supabase.auth` calls remain.

## Definition of done

- `grep -c 'supabaseClient.auth' website/apps/dashboard/src/main.jsx` → **0**
- `grep -c 'session.access_token' website/apps/dashboard/src/main.jsx` → **0**
- `API_BASE === '/api'`, no `Authorization` header built in the client
- a real-browser clickthrough renders the dashboard signed in
