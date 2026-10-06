# Archive Gate 1 — CLOSED (retired, not passed)

**Date:** 2026-09-15 · **Issue:** #3501 · **Branch:** `feat/3501-auth-session`

## The gate as written

> Pass/fail Pages-Function spike: does `@supabase/ssr` work under `nodejs_compat` in a
> Cloudflare Pages Function? Fallback `@supabase/server`. Upstream #37592.

## What the recon found

| Check | Result |
|---|---|
| `website/package.json` | **does not exist** |
| root `package.json` | **does not exist** |
| `node_modules` anywhere | **none** |
| npm imports in any Pages Function | **zero** |
| `nodejs_compat` configured | **nowhere** |
| how `/admin` (the existing BFF) calls Supabase | raw `fetch()` to `/auth/v1/user`, `/rest/v1/…` |
| how CI builds Functions | `wrangler pages dev website/` (wrangler 4.127.0) |

So the gate's premise was wrong in a more useful way than a pass/fail: **there is no
mechanism to add `@supabase/ssr` at all.** Adopting it would require introducing a
`package.json` + `node_modules` + `nodejs_compat` — a convention change this project
deliberately avoids, and which `SCOPE.md` §10 ⚠️b had already flagged as an open conflict.

## The finding that closes it

**The BFF does not need `@supabase/ssr`.** Every capability W1 requires is reachable with
built-in Workers globals, via the pattern `/admin` already uses:

- PKCE exchange → `POST /auth/v1/token?grant_type=pkce` (fetch)
- `verifyOtp` → `POST /auth/v1/verify` (fetch)
- refresh → `POST /auth/v1/token?grant_type=refresh_token` (fetch)
- JWT verification → **WebCrypto** (native), replacing `session_auth.py`'s Python-only verifier
- session store → **D1 binding** (built-in)

Dropping the SDK also drops the `nodejs_compat` requirement and the `package.json`
conflict in one move.

## Evidence — not asserted, executed

Spike in `docs/engineering/auth-spike/functions/api/` (throwaway, kept as evidence).

**Bundling:** `wrangler pages functions build` → `✨ Compiled Worker successfully`, exit 0.

**Runtime:** `wrangler pages dev` → `/api/gate1` returned
`{webcrypto: true, atob: true, fetch: true}`.

**End-to-end ES256** — the decisive test, `/api/es256`:

```json
{
  "jwkExported": true,
  "jwkCurve": "P-256",
  "signatureLength": 64,
  "signatureIsRawRs": true,
  "verified": true,
  "sub": "spike-user",
  "tamperRejected": true,
  "verdict": "PASS — ES256 verify works, tampering rejected"
}
```

**The load-bearing detail:** WebCrypto ECDSA emits **raw `r||s` (64 bytes)**, which *is* the
JWS ES256 wire format — no DER unwrapping. This was the main technical risk in
re-implementing `session_auth.py`'s verifier in TypeScript, and it is now resolved.

**The negative control matters:** a verifier that only ever returns `true` is not a
verifier. A tampered payload was rejected (`tamperRejected: true`).

## Verdict

**CLOSED — retired.** Not "passed with a fallback": the dependency is unnecessary, so the
gate no longer exists as a risk. This removes a blocking item and simplifies W1.

---

# Archive Gate 1b — D1 without `wrangler.toml` — CLOSED (passed)

**The `SCOPE.md` §10 ⚠️b / §8.1 conflict:** the D1 binding was believed to require `wrangler.toml`,
which this project deliberately avoids — making the whole session-store design (shape D)
topology-blocked.

## What the recon found

`wrangler pages dev` exposes bindings as **CLI flags**: `--d1`, `--kv`, `--r2`, `--do`, `--binding`.
No config file is required to bind D1 locally.

## Evidence — executed

`wrangler pages dev . --d1 SESSIONS` → `/api/d1`:

```json
{
  "d1Binding": true,
  "schema": true,
  "readAfterWrite": true,
  "casRevokeChanges": 1,
  "casIdempotent": true,
  "bulkRevokeChanges": 1,
  "allRevoked": true,
  "verdict": "PASS — D1 contract works without wrangler.toml"
}
```

What that establishes against the §8.1 contract:

| W1 requirement | Result |
|---|---|
| `sessions` schema, `handle` PK, indexed `user_id` | ✅ created |
| Read-after-write consistency (primary-only default) | ✅ — the §10 precondition holds |
| **CAS revoke** (conditional update) | ✅ `meta.changes` = 1, then **0** on retry — atomic *and* idempotent |
| **Bulk revoke by `user_id`** (F15) | ✅ 1 change, all sessions dead |

`meta.changes` is confirmed **reliable** — the CAS logic and F15's bulk revoke both depend on it,
and an unreliable count would have silently broken revocation.

## Verdict

**CLOSED — passed.** The binding works with a CLI flag; `wrangler.toml` is not required. The
D1 session store (shape D) is **unblocked**, and the §10 ⚠️b conflict is resolved for both the
SDK and the binding.

**Still open and NOT covered here:** production D1 provisioning, the read-replication-off
setting as a deployment invariant (a *silent* precondition — replication must be off or
read-after-write breaks), and the ~1k qps/database ceiling.
