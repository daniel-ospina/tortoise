---
title: "D1 session store — the lifecycle contract: schema, migration path, and failure semantics (#3522)"
type: engineering
domain: platform
doc_status: live
created: 2026-10-10
subjects.team: epistemic-team
aboutSubjects: d1-session-store, bff-session, cloudflare-pages-functions
aboutObjects: tortoise-sessions, auth-re-architecture-3501, issue-3522
---

# D1 session store — the lifecycle contract

**Issue:** [#3522](https://github.com/daniel-ospina/tortoise/issues/3522) (child of #3501) ·
**Canonical code:** `website/apps/dashboard/functions/_shared/auth/session.ts`, `token.ts` ·
**Migration:** `website/migrations/0001_auth_sessions.sql` ·
**Deploy config:** `config/required-bindings.yml` · **Gate:** `tools/check_pages_bindings.py`

Issue **#3522** is the one that made the D1 store's *lifecycle* explicit rather than discovered:
"D1 is the load-bearing new dependency of the auth re-architecture. Leaving its lifecycle
implicit is how the #3485 class recurs in a new form."

**Most of #3522 landed with #3501's implementation.** This doc is written from the code and
from the executed Gate 1b evidence, not from the plan — the issue body is stale in one place
(it says D1 has "zero in-repo precedent"), and the parts it prescribes are already shipped:

| #3522 task | State | Evidence |
|---|---|---|
| 1. Schema, and what is *not* stored | **landed** | `website/migrations/0001_auth_sessions.sql`; `ensureSchema` / `ensureSchemaTokenColumns`; §1 |
| 2. Migration path with no `wrangler.toml` | **landed** | Gate 1b (`docs/engineering/auth-spike/GATE1-RESULT.md`); `config/required-bindings.yml` + deploy preflight; §2 |
| 3. Read-replication-off precondition, documented **and guarded** | **this doc + §3 guard** | §3; `tests/test_pages_bindings.py::test_no_d1_sessions_api_is_used` |
| 4. CAS atomicity + `meta.changes` reliability | **landed** (code) + recorded | §4; Gate 1b `casRevokeChanges`/`casIdempotent` |
| 5. Failure semantics — terminal unavailable, never a sign-out | **landed** | §5; every D1 fault site answers `503 session_store_unavailable` |
| 6. Capacity note + the documented scale-out path | **this doc** | §6 |

---

## 1. Schema, and what is deliberately NOT stored

`website/migrations/0001_auth_sessions.sql` (base table + index); the three token-cache columns
are added by `ensureSchemaTokenColumns` (`token.ts`) because `CREATE TABLE IF NOT EXISTS` cannot
evolve a table that already exists.

| Column | Type | Why |
|---|---|---|
| `handle` | TEXT PRIMARY KEY | The opaque value in `__Host-session`. 32 random bytes, hex (`randomHandle`). |
| `user_id` | TEXT NOT NULL | Supabase user id. Indexed (`idx_sessions_user`) — F15 "sign out everywhere" is a per-user scan. |
| `refresh_token` | TEXT NOT NULL | Rotating single-use GoTrue refresh token. **Server-only.** |
| `revoked` | INTEGER NOT NULL DEFAULT 0 | Immediate revocation. Nothing deletes the row; revocation is a flag, so the CAS stays observable. |
| `created_at`, `expires_at` | INTEGER NOT NULL | `expires_at` is the SESSION's own TTL (`SESSION_MAX_AGE_S` = 400 days), not the access token's. Enforced on every read path, including the W6 proxy (`getAccessTokenForSession`). |
| `access_token`, `access_token_expires_at` | TEXT / INTEGER | Cache for the W6 token-handler proxy. Refreshing per proxied request would stampede GoTrue's rotating single-use tokens. |
| `token_rejected_at` | INTEGER | Cooldown stamp (`invalidateCachedToken`), so a persistent upstream 401 cannot become unbounded refresh traffic. |

**Not stored — by ruling, not by omission:** `provider_token`, `identities`, `user_metadata`
(the fat fields whose size produced the `SIZE_GUARD = 3800` defect on the cookie the browser
used to hold), and any profile data. `api/profile.ts` asks GoTrue with the server-held token
when the UI needs a display name, and a profile-lookup failure degrades to id-only rather than
signing the user out. The distinction the amendment preserves: the cached `access_token` is the
**session** access token, never a provider token (`token.ts`, header comment).

Pre-auth flows are a second table in the same database: `auth_flows` (`flow_id`, `verifier`,
`kind`, `client_id`, `redirect_uri`, `state`, `next`, timestamps) — the PKCE verifier lives
server-side and the `__Host-authflow` cookie is *bound* to a live row, which is what makes
"cookie exists" insufficient. `email_flow_pending` is the third.

## 2. Migration path — this project has no `wrangler.toml`, deliberately

The #3522 body treats "`wrangler.toml` is required for a D1 binding" as a collision to resolve.
It was resolved by measurement, not by adopting a config file: Gate 1b
(`docs/engineering/auth-spike/GATE1-RESULT.md`) proved `wrangler pages dev . --d1 SESSIONS`
exposes the binding as a **CLI flag**, and Pages projects carry their bindings as platform
configuration, not as a repo file. There is still no `wrangler.toml` and no `package.json` at
the root — that is a kept convention, not an accident (it is also why `@supabase/ssr` was not
adopted; Gate 1).

Four legs, all live:

1. **Runtime bootstrap (idempotent, deliberate).** `ensureSchema` / `ensureSchemaTokenColumns`
   (sessions + token columns + `idx_sessions_user`), `ensureFlowTable` (`/auth/start`),
   `ensurePendingTable` (`/auth/confirm`), `ensureLinkFlowTable` (`/auth/link`),
   `ensureAuthFlowIndexes`. Only `CREATE … IF NOT EXISTS` and `ALTER TABLE ADD COLUMN` — never a
   destructive change. A fresh local database is self-establishing instead of mysteriously broken.
2. **Explicit apply, for a real migration.** `wrangler d1 execute SESSIONS --file website/migrations/0001_auth_sessions.sql`
   (`--local` for dev). Production's tables were created by leg 1, so the migration file and the
   runtime bootstrap must agree — that is why leg 1 creates `idx_auth_flows_expires` too, which a
   runtime-created database would otherwise never receive.
3. **Local/e2e:** `wrangler pages dev . --d1 SESSIONS` — the e2e auth suite applies the migration
   straight to the sqlite file.
4. **Deploy gate (the #3616 fix).** `config/required-bindings.yml` declares `SESSIONS` as a
   `required` `d1_databases` binding for **both** Pages projects, in `production` **and** `preview`;
   `.github/workflows/deploy-pages.yml` runs `tools/check_pages_bindings.py --project …` **before**
   the deploy and fails the job if it is absent. #3501 shipped the BFF without the binding, the
   workflow went green, and every visitor got `503 session_store_unavailable` — a total sign-in
   outage whose requirement lived only in a PR body. The manifest is in `config/` (not `website/`)
   because the Pages upload path published anything under `website/`.

Two traps this path encodes: `CREATE TABLE IF NOT EXISTS` cannot add a column to an existing
table (hence `ensureColumn`, which swallows **only** the duplicate-column error), and a *read*
path must create the base table before adding columns to it — `ensureSchemaTokenColumns` calls
`ensureSchema` first, because `ALTER TABLE` on a missing table is not a duplicate-column error
and made every read path 503 with "no such table: sessions".

## 3. Read-replication-off is a silent precondition — and it is now guarded

**The vendor fact (Cloudflare D1 "Global read replication", verified 2026-10-10):** *"To use read
replication, you must use the D1 Sessions API, otherwise all queries will continue to be executed
only by the primary database."* Replicas serve **reads only**; enabling replication is inert until
code calls `env.DB.withSession(bookmark)`, and a session started `first-unconstrained` may read a
replica that has not yet caught up.

**Why that matters here — and why "off" is not the whole story.** Every read in this store must be
primary-only *read-after-write*: `createSession` writes a row and the very next request reads it
back (`getSession`, `resolveSession`, `getAccessTokenForSession`). A read served from a lagging
replica returns **no session**, which the BFF must answer as `401` → the browser is told it is
signed out → the #3485 mechanism (F10: gate sees no session → bounced to `/auth…&stale=1` → the
stale branch clears the arriving token → the sign-in revokes itself). The CAS *writes* are not at
risk — D1 routes writes to the primary regardless — so the exposure is entirely on the read leg.

The precondition therefore has two halves:

- **(a) Deployment invariant:** read replication stays **disabled** for `tortoise-sessions`.
  This is a platform setting with no repo-visible representation.
- **(b) Code invariant (the guard):** no source may call the D1 Sessions API (`withSession(`).
  This is the half that can be violated by a diff, so it is machine-checked:
  `tests/test_pages_bindings.py::test_no_d1_sessions_api_is_used` scans every `.ts` under
  `website/` for `withSession(`, asserts it finds none, and asserts non-vacuity by requiring the
  scan to have seen real D1 usage (`prepare(`/`exec(`) at the same time. A gate that scans
  nothing passes, so it must prove it scanned something.

**If replication is ever wanted, do not simply enable it.** Either (i) leave this store alone and
put a locally-verifiable layer *in front of* it — the hybrid JWT shape of §6, which needs no
architecture change because the cookie already carries an opaque handle and the store stays
authoritative — or (ii) constrain **every** session to `first-primary` so the read cannot be served
from a replica. A bare `withSession()` in this codebase is a defect, not a performance knob.

## 4. CAS: the exact predicates, and why `meta.changes` is trustworthy

Two conditional updates carry the design; both are in the code today:

| Site | Predicate | Contract |
|---|---|---|
| `revokeSession` (session.ts) | `WHERE handle = ?1 AND revoked = 0` | `meta.changes === 1` → we won; `0` → already revoked or never existed (idempotent, so a double logout is not an error). |
| `getAccessTokenForSession` (token.ts) | `WHERE handle = ?1 AND revoked = 0 AND refresh_token = ?5` | CAS on the **old** refresh token: `0` means a concurrent request already rotated it → **re-read** the winner's token instead of clobbering it. Without that branch the loser overwrites the live session's token. |

`refresh_token` does not appear in the revoke predicate: revocation is idempotent on `revoked`,
and the rotation race is guarded where the rotation happens. (The re-read branch is
regression-covered only by the code path itself — a test is tracked by **#5355**.)

**Verified by execution, not asserted** — Gate 1b ran these on a real `wrangler pages dev` +
`--d1`: `casRevokeChanges: 1`, then `casIdempotent: true` (retry → 0), `bulkRevokeChanges: 1`,
`allRevoked: true`. `meta.changes` is therefore reliable for both the CAS and F15.

**Why atomicity is structural, not hoped for:** D1 "is inherently single-threaded, and processes
queries one at a time", and each database "is backed by a single Durable Object" (D1 limits, read
2026-10-10). A conditional `UPDATE` on one database cannot interleave with another statement on
that database. The CAS is atomic because the execution engine is serialized — this is the same
property that makes read-replication-off load-bearing.

## 5. Failure semantics — every D1 failure is terminal, and never a sign-out

The rule, stated once: **"not signed in" (401) and "session store unreachable" (503) must not be
conflated.** Returning 401 while the database is merely down *is* the #3485 loop. Every site that
touches the store follows it:

- `env.SESSIONS` missing → `503 session_store_unavailable` (`/welcome`, `/auth/confirm`,
  `/auth/password`, …) — never a redirect to `/auth`.
- A **throw** from `prepare/bind/first/run` → `503 session_store_unavailable`
  (`getAccessTokenForSession` returns `{ok:false, reason:"unavailable"}`; `resolveSession` returns
  `{unavailable:true}` for the same case). The convenience wrapper `getSessionUserId` collapses
  both to `null` **only** for endpoints whose failure mode is already a plain 401/403 — and that
  collapsing is documented at the call site.
- A genuine provider fault maps to `503 provider_unavailable`, and only the `invalid_grant`
  family (`isRefreshTokenDead`) is treated as a dead session. A 403 from an edge/WAF, a 404 from a
  misconfigured `SUPABASE_URL` or an unexpected 400 is infrastructure — classifying those as dead
  signs every user out during an outage.
- A session past `expires_at` is a **dead** session (401), not a refreshable one: the TTL is
  enforced on the data path too, or the proxy keeps serving authenticated data with a 200 while
  `/api/session` answers 401 for the same cookie.
- Housekeeping never escalates: `sweepExpiredFlows` and `invalidateCachedToken` absorb their own
  failures (logged / best-effort). A collection problem must not become a 503 on sign-in.

## 6. Capacity, and the documented scale-out path

Per database (D1 limits, read 2026-10-10): **inherently single-threaded, queries processed one at
a time**, ≤ 10 GB, ~ **1,000 queries/second if the average query is 1 ms** (100 ms/query → ~10 qps).
A database receiving too many concurrent requests queues them, and returns an **"overloaded"**
error once the queue is full. With read replication (which §3 rules out here) each replica is a
separate Durable Object and the guidance applies per replica — not to the primary that must serve
every session read.

**The constraint near 1M MAU is therefore the per-request session lookup**, not storage: this
design reads the row on every authenticated request (`resolveSession`, `getAccessTokenForSession`,
`/api/session`), and one database serializes all of them. The token-cache columns exist for exactly
this reason — they remove the *GoTrue round-trip* from the hot path, they do not remove the D1 read.

**Documented scale-out path (no architecture change):** add the hybrid JWT shape *in front of* the
handle store — a locally verifiable, short-lived assertion minted from the handle row, with D1
consulted only on refresh and on revocation. The handle store stays authoritative and the cookie
contract (`HttpOnly`, opaque) does not change, so this can be introduced without re-opening the
session design. Until then, treat the ~1k qps figure as a **design bound, not a measurement**:
this store has never been load-tested.

---

## Integration Docs

| Item | Value |
|---|---|
| **Dependency** | Cloudflare D1, consumed through the Pages Functions runtime **binding** `env.SESSIONS` — not a package. No `package.json`, no `node_modules`, no `nodejs_compat`, no `wrangler.toml` (Gate 1 / Gate 1b). |
| **Version** | The Pages runtime's D1 binding API. **Deliberately unpinned in-repo** (no config file); the local/e2e harness runs `wrangler pages dev` (wrangler 4.127.0 at Gate 1). |
| **API surface used** | `db.prepare(sql).bind(...).first<T>()` / `.run()`, `db.exec(ddl)`, `res.meta.changes`. |
| **API surface deliberately NOT used** | `withSession()` (the Sessions API — §3), `batch()`, `dump()`, `raw()`. |
| **Database** | `tortoise-sessions` — id `c16d027e-8c66-4f3d-aeda-5772ea54e101`, created 2026-09-16. |
| **Bindings declared** | `config/required-bindings.yml`: `SESSIONS` / `d1_databases` / `required` for `premise-labs` **and** `tortoise-dashboard`, envs `production` + `preview`. Plus `SUPABASE_URL`, `SUPABASE_ANON_KEY` (`production`) for the token exchange the store's refresh leg performs. |
| **Gate** | `tools/check_pages_bindings.py --manifest config/required-bindings.yml --project <name>`, run **before** deploy in `.github/workflows/deploy-pages.yml`; exit 2 = "could not determine" = fail closed. |
| **Tables** | `sessions`, `auth_flows`, `email_flow_pending` (all created by the runtime bootstraps; `website/migrations/0001_auth_sessions.sql` mirrors them). |
| **Verified by execution** | Gate 1b: read-after-write, `casRevokeChanges: 1`, `casIdempotent: true`, `bulkRevokeChanges: 1`. Evidence: `docs/engineering/auth-spike/GATE1-RESULT.md`. |
| **Vendor docs read at** | `developers.cloudflare.com/d1/best-practices/read-replication/`, `developers.cloudflare.com/d1/platform/limits/` (2026-10-10). |

## Still open (not this issue's deliverable)

- **Production D1 provisioning itself** — the database exists; the *invariant that read replication
  is off* is a dashboard setting nothing in-repo reads back (§3(a)).
- **The ~1k qps bound has never been load-tested** against this store.
- **#5355** — the concurrent-rotation CAS re-read has no test.
- **#8032** — `sessions` and `email_flow_pending` have no expiry sweep (the rest of the #3647 class).
- **#3529** — 4 of the 7 declared threat classes have no test.
