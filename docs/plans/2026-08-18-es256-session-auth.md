---
title: "Implementation Plan — Issue #1460: ES256 session-auth fix (PyJWT swap + cache hardening)"
type: engineering
domain: platform
doc_status: live
created: 2026-08-18
subjects.team: epistemic-team
aboutObjects: tortoise-session-auth, tortoise-hosted-api
---

<!-- research-path: issue #1460 scoping comment (https://github.com/daniel-ospina/tortoise/issues/1460#issuecomment-5331136304) -->

# ES256 Session-Auth Fix Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Make `verify_session_jwt` accept Supabase ES256-signed access tokens so session-plane endpoints (`/v1/teams`, `/v1/session/key`, member management, invites, MCP OAuth authorize) stop 500ing and the dashboard login completes.

**Team:** epistemic-team
**Role:** (not set)

**Architecture:** Replace the hand-rolled RS256-only verification core in `tortoise/session_auth.py` with PyJWT (`pyjwt[crypto] 2.13.0` — already shipped in the hosted image via the `mcp` chain). Retain and harden the repo's `_JWKSCache` (TTL/stale-serve/5s-bound). PyJWKClient's own fetch is sync-urllib (blocks the async loop) and has no stale-serve on outage, so the repo cache stays. Per request: kid lookup → `PyJWK.from_dict(jwk)` → `jwt.decode(..., key=pyjwk_obj, algorithms=["RS256","ES256"], audience="authenticated", issuer=<supabase>/auth/v1, leeway=30, options={"require":["sub","exp","iat","iss"], "verify_iat": True, "strict_aud": True})` — **note: `"iss"` in require is an implementation addition over the originally reviewed sketch (redundant with `issuer=` — pyjwt already requires iss presence — but test-pinned: missing-iss → 401; code-review #1467 flagged the drift, implementation is authoritative)**, with a fail-closed boundary catching `jwt.PyJWTError` + `(KeyError, ValueError, TypeError, binascii.Error)` → 401, and JWKS fetch failures → HTTPException 503. Token-size cap is **repo-enforced at 16,000 bytes — BELOW the server's ~16KB header-line limit** (uvicorn/h11 `max_incomplete_event_size`) so the repo guard, not a raw server 400/431, is the first line of rejection (code-review #1467 P2); pyjwt 2.13 has no `max_length` param. Passing the `PyJWK` object (not `jwk.key`) keeps PyJWT's alg/kty-confusion defense (`alg != key.algorithm_name` → InvalidAlgorithmError).

**Issue:** #1460 · **Tier:** Standard

---

### Pattern Research

**Library-docs preflight:** verified against the **installed source** (`pyjwt 2.13.0`, `cryptography 50.0.0`) by solution-verify + plan-review cycles, not web docs:
- `PyJWKClient.from_keys` does not exist in 2.13 → use `PyJWK.from_dict(jwk)`; `jwt.decode` accepts `AllowedPublicKeys | PyJWK | str | bytes`.
- `InvalidKeyError`, `PyJWKError`, `PyJWKSetError`, `PyJWKClientError`, `MissingCryptographyError` subclass `PyJWTError` directly (NOT `InvalidTokenError`).
- **`PyJWK.from_dict` failure modes are NOT all PyJWTError**: oct JWK → `KeyError('k')`; bad EC point → `ValueError`; non-base64url `x` → `binascii.Error`; wrong-typed `x` → `TypeError`. Wrong-typed `exp`/`iat`/`nbf` claims also raise `TypeError`. **All five caught → 401.**
- `cryptography` `ec.ECDSA()` returns **DER (72 bytes)**, not raw r‖s; PyJWT's `ECAlgorithm.verify` expects raw → mint via `jwt.encode(..., algorithm="ES256", headers={"kid": kid})` (DER→raw internal).
- `aud` **required + membership-matched** when `audience=` set; **list-form `aud` (`["authenticated","evil"]`) passes membership match** → set `strict_aud: True` to restore string-exact semantics (old code rejected non-string aud).
- `sub` **presence-only** (`payload.get(claim) is None`) — but `verify_sub` defaults **True** in 2.13, so non-string subs (`0`, `123`, `["abc"]`) are already rejected by PyJWT (`InvalidSubjectError`, verified); the explicit `isinstance(str)` + truthiness guard's remaining value is the **empty/whitespace-string** case (`""`, `" "` — accepted by PyJWT, must be rejected by the guard).
- `verify_iat` **and `verify_nbf` default True** (`iat`/`nbf` future → `ImmatureSignatureError`); old code ignored both. Kept ON as hardening; documented + tested. `exp`/`iat` presence is **not required by default** → add `"exp"` and `"iat"` to `require`.
- `exp` validation **inclusive** (`exp <= now - leeway`); old code strict `>` — `exp == now - 30` flips to 401. `iat`/`nbf` use strict `>` (boundary at `== now + 30` still accepted).
- **`max_length` does NOT exist in pyjwt 2.13** (verified: zero matches in installed package; the kwarg is dropped with a `RemovedInPyjwt3Warning` no-op) → the 16KB token cap is **repo-enforced only**, via the early length guard in `_decode_header`.
- **Out-of-range numeric time claims (`exp`/`iat`/`nbf` = `Infinity`) raise `OverflowError`** (verified empirically: `int(float("inf"))`; pyjwt's validators catch only ValueError) → must be added to the fail-closed catch tuple.
- **Header-cap reality**: uvicorn/h11's `max_incomplete_event_size` (~16KB per header line) rejects Bearer tokens ≳16,362 bytes at the server with a raw 400/431 BEFORE the repo guard — the repo guard is **defense-in-depth for in-process callers**; the effective HTTP token-size cap is the server's. Documented; raising the server limit is out of scope.
- **OKP/EdDSA JWKs**: cached (cache filters on kid presence), then `PyJWK.from_dict` binds "EdDSA" → any ES256/RS256 token → InvalidAlgorithmError → 401 (fail-closed); EdDSA tokens rejected by the allowlist. **ES384/ES512 (P-384/P-521)**: `PyJWK.__init__` prefers `jwk["alg"]` over crv-based binding — a P-384 sibling entry WITHOUT `alg` binds to "ES384" (ES256 token → InvalidAlgorithmError); the plan's `build_ec_jwks` helper ALWAYS emits `alg: ES256`, so a P-384 sibling built by the helper binds to ES256 and the 401 arrives via signature-verify failure against the P-384 key, not InvalidAlgorithmError. Both mechanisms fail closed (verified); the test pins the outcome, not the mechanism.
- **HTTP 200 with garbage JWKS body** (HTML error page, top-level `[]`, `{"keys": null}`): `resp.json()`/comprehension fails INSIDE the fetch try → cold 503 / warm stale-serve — distinct partial-failure class, tested.
- `cryptography==50.0.0` pinned; `pyjwt 2.13.0` + crypto extra in uv.lock (via mcp).

**Skip note (Perplexity gate):** library API surface verified against actual installed package source by five independent fresh-context reviewers — stronger than web triangulation; writing-plans allows citing upstream-verified findings. Supabase ES256-default context already gathered during research.

### Integration Surface Map

| Surface | Boundary | Test Layer | Failure Modes |
|---------|----------|------------|---------------|
| `verify_session_jwt` (auth boundary) | FastAPI dependency → Supabase JWKS + JWT crypto | unit (`tests/test_session_auth.py`) | wrong sig; malformed JWK (short x, oct kty, bad point, non-base64url x, wrong-typed x, OKP missing x); alg-confusion (ES256↔RSA, OKP/EdDSA, **ES384/ES512, unknown kty**); unknown alg; **alg-absent header (`{"kid": "K1"}`) → 401 (InvalidAlgorithmError path); RFC 7797 `b64: false` header → 401; unsupported `crit` list header → 401 (all via catch tuple; fetch-count 1 for the alg-absent case)**; expired (leeway boundary incl. exact `now-30`); future iat/nbf (exact `now+30` accepted); **out-of-range exp/iat/nbf (Infinity → OverflowError caught)**; missing exp/iat (require); wrong/missing/trailing-slash iss; wrong/missing/list-form aud (strict); missing/empty/non-string sub; **wrong-typed email/app_metadata (shape guards)**; oversized token (repo guard; effective HTTP cap is the server's header limit); malformed token (non-dict segments); no-kid/whitespace-kid/truthy-non-string-kid (zero fetch); kid-miss refetch; fetch-failure → 401/503 |
| `_JWKSCache` | in-process TTL cache + failure cooldown + single-flight (kid-aware) | unit | fetch failure (stale-serve + cooldown, no evict-on-failure); first-fetch 200-empty → 401 (never 500); **200-empty records cooldown (failure semantics) — no per-request refetch storm; recovery after cooldown/TTL**; 200-empty on warm cache (stale-on-empty); duplicate-kid (first-wins); kid-less keys dropped; malformed key entry fails fetch closed; **HTTP 200 with garbage body (HTML/`[]`/`{"keys": null}) → cold 503 / warm stale-serve, no eviction**; cooldown-skipped fetch with no last-good → 503 (never None-crash); JWKS body >64KB → treated as fetch failure (stale/503); concurrent burst — one fetch per cooldown window, lock-coalesced, **success and failure paths** (kid-aware single-flight re-check after lock); first-fetch failure → 503 |
| Direct `verify_session_jwt` call sites | `hosted_api.py:1298` (`get_current_team`), `:3254` (dashboard-login toggle), `:6332` (`/v1/claim`), `:6511` (`/v1/claim/status`), `:8614` (`/oauth/consent/preview`), `:8652` (`/oauth/consent`) + `Depends(get_current_user)` (~20 sites) | e2e | none — no signature change; return shape `{user_id, email, app_metadata}` preserved (app_metadata shape-guarded) |
| Build/deploy contract | `pyproject.toml` → `uv lock` → `uv export --frozen --no-dev --no-editable` → `requirements.txt` | CI parity gate (`deploy-hosted.yml`) | requirements.txt drift → deploy blocked; **`uv export --frozen` silently exports a stale lock** — always `uv lock` after pyproject edits |
| E2E harness JWKS mints | `tests/e2e/hosted/conftest.py::_JWKSKeys`, `test_13_claim.py::_JWKS` | e2e | currently RSA/RS256-only — must mint EC/ES256 (the CI gap that let this ship) |
| claim path | `app_metadata.providers` asserted on returned dict | e2e | return shape must keep `app_metadata` key; **wrong-typed app_metadata rejected at verify (401) — no downstream AttributeError** |
| Test state hygiene | module-level `_jwks` singleton | unit | autouse fixture snapshots/restores `_keys`/`_fetched_at`/`_lock` + stubs httpx — order-independence |

**scope note (explicit decision):** the ~10-line bug fix rides with a cache redesign. The hardening is IN SCOPE because the touched code has a live unauthenticated JWKS-fetch amplification hole — today every kid-miss request does `_jwks._keys = None; get()` = N requests → N upstream fetches with no cooldown — and hardening while rewriting the cache is cheaper than a second pass. The concurrency matrix's stub-counted assertions are non-flaky and justify the surface; deferring would ship the amplification hole with a new code path. (Per-process semantics: the deployment is single-worker — no `--workers`; cache docstring notes it, mirroring `hosted_api.py:7120`.)

### Verification Plan

**Operability:** `_JWKSCache` logs a rate-limited `logger.warning` on fetch-failure / 200-empty / cooldown-arm — **the rate limit is by construction: log only when arming `_last_failure_at` (once per cooldown window)** — so an outage is distinguishable from invalid tokens; `_COOLDOWN_S` reads from env (`TORTOISE_JWKS_COOLDOWN`, default 30s) matching `_JWKS_TTL`/`_FETCH_TIMEOUT` configurability; the cache docstring notes per-process single-worker semantics.

- **Unit** (primary): full ES256 + RS256 + cache-hardening matrix in `tests/test_session_auth.py`.
- **E2E**: harness JWKS mints converted to EC/ES256 via a shared mint helper; claim flow (`test_13_claim`) passes.
- **Post-deploy live check**: real Supabase session token (dashboard login via GitHub OAuth) → `/v1/teams` 200 + `/v1/session/key` mints a key (dashboard login completes).
- Deferred (separate issues, not absorbed): ACAO-on-unhandled-500 hygiene.

---

### Task 1: Swap session_auth verify core to PyJWT (ES256 + RS256) + harden cache

**Intent:** Kill the `KeyError('n')` → 500 → CORS-wall bug class by delegating JWT verification to PyJWT and making every verify-path failure a clean HTTPException (401/503, CORS-stamped) instead of an unhandled exception.

**Acceptance:** `verify_session_jwt` verifies real ES256 **and** RS256 tokens and returns `{user_id, email, app_metadata}` (shape-guarded); every negative case → HTTPException 401/503, **never** a raw 500; `_JWKSCache` stale-serves on fetch failure AND empty-key responses, never evicts last-good keys on failed refetch (incl. R16 force path), coalesces concurrent fetches (single-flight, in-lock failure cooldown) on success and failure paths; RS256 regression green.

**Files:**
- Modify: `tortoise/session_auth.py`
- Create: `tests/_session_jwt_utils.py` (shared mint helper)
- Test: `tests/test_session_auth.py`

**Step 1: Write the shared mint helper** — `tests/_session_jwt_utils.py`:
- **Import contract:** `tests/`, `tests/e2e/`, `tests/e2e/hosted/` have no `__init__.py` — the unit side resolves via pytest basedir insertion; the hosted e2e modules resolve via **namespace-package** (`from tests._session_jwt_utils import ...`), relying on `tests/conftest.py:29`'s `sys.path.insert` having already run (order-dependent; breaks under `--import-mode=importlib` — accepted, documented; do not add `__init__.py`).
- `make_ec_keypair()` / `make_rsa_keypair()`.
- `build_ec_jwks(public_key, kid)` → `{keys: [{kty: EC, crv: P-256, kid, alg: ES256, x, y}]}`; `build_rsa_jwks(public_key, kid)` → `{keys: [{kty: RSA, kid, alg: RS256, n, e}]}`.
- `mint_es256_token(private_key, kid, payload, iss=None)` / `mint_rs256_token(..., iss=None)` → `jwt.encode(payload, private_key, algorithm=..., headers={"kid": kid})` — **PyJWT encodes DER→raw internally** (do NOT sign with `cryptography` `ec.ECDSA()` directly: it returns 72-byte DER, which PyJWT rejects). **`iss` is an explicit parameter; when omitted it defaults to `sa._SUPABASE_URL.rstrip("/") + "/auth/v1"`** (unit-test call site — can never drift from the module). **The e2e harness MUST pass the mock JWKS URL as `iss`** (`iss=jwks_url + "/auth/v1"`) — the server subprocess boots with `SUPABASE_URL = jwks_url`, the new verifier does EXACT issuer match, and the test process's `sa._SUPABASE_URL` is the real URL; without the override every e2e session request 401s.

**Step 2: Write the failing tests** — `tests/test_session_auth.py`:
- **Happy paths:** ES256 token verifies → `{user_id, email, app_metadata}`; **RS256 token verifies → same shape** (regression green — shared RSA helper).
- **Negative matrix (all → HTTPException 401, never 500):** wrong signature; short-`x` JWK; oct-kty JWK; non-base64url `x`; wrong-typed `x` (`123`); bad EC point; alg=ES256 vs RSA JWK; alg=RS256 vs EC JWK; alg `none`/unknown; **EdDSA/OKP + future-curve family: OKP JWK + EdDSA-signed token → 401; OKP JWK missing `x` → 401; JWKS with valid EC + sibling OKP → EC token for the EC kid still verifies 200 (no poisoning); JWKS with valid P-256 + sibling P-384/ES384 → P-256 token 200, ES384-signed token 401, ES256 token presented with the P-384 kid → 401; unknown kty (`X25519`) → 401**; expired (`exp = now - 61` → 401; `exp = now - 20` → 200; **`exp = now - 30` exact → 401, pinned**); future `iat` (`now + 3600` → 401; `now + 10` → 200; **`iat == now + 30` exact → 200**); future `nbf` (`now + 3600` → 401; `now - 10` → 200; **`nbf == now + 30` exact → 200**); **missing `exp` → 401 (require); missing `iat` → 401 (require)**; wrong-typed `exp`/`iat` (`[9999999999]`, `{"a":1}`) → 401 not 500; wrong issuer; missing issuer; trailing-slash issuer; wrong audience; missing aud; **list-form aud (`["authenticated","evil"]`) → 401 (strict_aud)**; missing sub; empty/non-string sub (`""`, `" "`, `0`, `123`, `["abc"]`) → 401; **wrong-typed claims from a VALID token (`app_metadata: "x"`, `email: 123`) → 401 (shape guards incl. email); missing email → 200 with `email: None`**; **malformed-RSA-JWK regression pin (the #1460 incident class): JWKS RSA entry with kid present but missing `n`/`e` → token with that kid → 401 never 500; `n` wrong-typed (`123`) → 401 never 500**; **out-of-range numeric time claims (`exp`/`iat`/`nbf` = `float("inf")`/`-inf`) → 401 (OverflowError caught)**; oversized token — **exact boundary: 16,000-byte token → 200, 16,001-byte → 401 (repo guard BELOW the server's ~16KB header cap; assert `warnings.catch_warnings` shows no DeprecationWarning from decode — no unsupported kwargs)**; malformed token `"not-a-jwt"`; non-dict header/payload segments (`WzEsMl0`, `MTIz`, `Ingi` → 401, never AttributeError); **no-kid header → 401 with ZERO `_jwks.get` calls (stub-counted); whitespace-only kid (`"  "`) and truthy non-string kid (`123`, `true`) → 401 with ZERO `_jwks.get` calls (guard is `isinstance(kid, str) and kid.strip()`), incl. cold-start variant → 401 not 503**; kid-miss → refetch (stubbed) → 401 "Unknown signing key".
- **Cache hardening:** fetch-failure with warm cache → 200 (stale-serve); fetch-failure with `_keys = None` → 503 (not raw 500); **cooldown-skipped fetch with `_keys = None` → 503 (never a None-crash)**; first-fetch 200-empty → 401 (never 500); kid-miss + failing refetch → 401 AND last-good `_keys` preserved; **positive R16 rotation: warm cache old kid, token with new kid, stubbed refetch returns new kid → 200 + cache updated**; removed-kid-after-successful-refetch → 401; rotation-under-outage: warm K1, upstream removes K1 + fetch fails → K1 token still verifies 200 (documented bounded-revocation-window tradeoff); after successful refetch → 401; 200-`{keys: []}` on warm cache → old keys served (stale-on-empty); duplicate-kid JWKS → first-wins pinned; kid-less keys dropped; malformed key entry (string in `keys`) fails fetch closed; **JWKS body >64KB → fetch-failure semantics (cold → 503, warm → stale-serve), zero body parse** — **note: the cap is post-buffer (httpx fully materializes the body before the `len(resp.content)` check); documented as post-buffer defense-in-depth, pinned with a test asserting the cap applies to the decompressed content length before `.json()` (incremental/streaming download is out of scope)**; **HTTP 200 garbage bodies (HTML, top-level `[]`, `{"keys": null}`) → cold 503 / warm stale-serve / zero eviction**; **post-200-empty: first-fetch 200-empty → 401, immediate second kid-miss → ZERO additional fetches (200-empty records cooldown); after cooldown with healthy upstream → token verifies 200 (recovery via force path — the named mechanism)**; **non-string kid VALUE in JWKS (`{"kid": 123}`): treated as zero-usable → failure cooldown recorded, N sequential kid-miss requests → exactly ONE fetch, all 401 (never 503), recovery after cooldown**; **success does not re-arm cooldown: force-refetch succeeds (returns {K1,K2}) → K2 verifies 200; immediately force-refetch K3 → a NEW fetch occurs (stub-count 2) and K3 honored**; **pristine-state cold-start (unset `_last_failure_at` sentinel, `_keys = None`) → force-refetch fires exactly ONE fetch and succeeds (fetch-count 1, result 200) — an unarmed cooldown never blocks a legitimate first fetch**; **hand-encoded ~1,200-deep nested payload segment (bypasses `json.dumps`; ~2-3KB, under the 16KB guard) → 401 never 500 — pins the fail-closed boundary for adversarial nesting on C-json (DecodeError path) AND pure-python-json runtimes (RecursionError path)**; **concurrency (failure, non-force): warm cache, TTL expired, failing fetch, 20 concurrent → exactly ONE fetch, all stale-served, no 5s serialization; concurrency (failure, force): warm cache K1, 20 concurrent kid-miss K2, failing upstream → exactly ONE fetch, all 401, wall-clock << 100s; concurrency (success, force): warm cache K1, 20 concurrent kid-miss K2, upstream returns {K1,K2} → exactly ONE fetch, all 200 (kid-aware single-flight: `get(force=True, kid=K2)` re-checks `K2 in self._keys` after lock acquisition); **concurrency (unknown-kid miss, healthy upstream): warm cache K1, upstream healthy but returns {K1} only (never K2), 20 concurrent forged-kid K2 requests → exactly ONE fetch, all 401 (miss arms cooldown — no per-request amplification); sequential variant: 50 unknown-kid tokens → fetch-count ≤ 1 (bounded, not 50)**; **TTL-refresh + miss-refetch double-fetch bound: warm cache K1 with `_fetched_at` backdated past TTL, ONE unknown-kid token → stub fetch-count == 2 (TTL refresh + miss refetch), then a second unknown-kid token → 0 additional fetches (cooldown armed); mixed burst: warm K1, TTL expired, 10 valid K1 + 10 forged K2 concurrent, upstream returns {K1} only → all valid 200, all forged 401, total fetch-count ≤ 2 (one TTL-refresh + one miss-refetch per window, then cooldown-bounded)**; **concurrency (success, non-force TTL): warm `_keys` K1, TTL expired, healthy upstream, 20 concurrent valid K1 tokens → exactly ONE fetch, all 200 (double-checked-TTL success-path coalescing)**; cold-start: `_keys=None`, failing fetch, 20 concurrent → all 503, one fetch**; kid-miss refetch within cooldown window → zero fetch attempts.
- **Autouse fixture:** snapshot/restore `sa._jwks._keys`, `_fetched_at`, `_last_failure_at`; **pin `sa._SUPABASE_URL` / `sa._JWKS_URL` to fixed constants**; **REPLACE (not restore) `sa._jwks._lock` with a fresh `asyncio.Lock()` per test** — `asyncio.Lock` binds to the first event loop that awaits it and raises `RuntimeError: bound to a different event loop` when reused across tests that each call `asyncio.run()` (new loop per test); add `_COOLDOWN_S` to the snapshot/restore list if patched. **Time-control for cooldown tests:** tests monkeypatch `sa._COOLDOWN_S = 0` (or backdate `sa._jwks._last_failure_at`) to defeat the 30s window — never sleep. Add a state-restoration test running two cache-touching tests back-to-back (fetch-failure cold-start → force-refetch success) asserting no RuntimeError.
- **Replace** `test_jwk_public_key_der_builds` (imports removed `_public_key_der`).

**Step 3: Run — expect FAIL** on ES256/RS256 happy paths + cache cases.

**Step 4: Implement** — `tortoise/session_auth.py`:
- Module top-level: `from cryptography.exceptions import UnsupportedAlgorithm` (⛔ NOT inside the except body — see the tuple comment; except-clause names evaluate before the body runs); **`import logging` + `logger = logging.getLogger(__name__)`; `_COOLDOWN_S = float(os.environ.get("TORTOISE_JWKS_COOLDOWN", "30"))`** (matches the `TORTOISE_JWKS_TTL`/`TORTOISE_JWKS_TIMEOUT` env pattern).
- Remove `_verify_rs256`, `_public_key_der`, **dead `_PROJECT_REF`, and unused `urllib.request` import**.
- `_decode_header` (was `_decode_jwt`): parse the **header only** (PyJWT owns payload parse + shape check internally — manual payload `json.loads` is dead work per request); add shape check (header must be `dict` → else 401); add the **repo-enforced token length guard** (token bytes > 16KB → 401) BEFORE decode — defense-in-depth (effective HTTP cap is the server's ~16KB header-line limit; unit boundary test validates the guard itself).
- `_JWKSCache`:
  - `get(force=False, kid=None)`: fetch wrapped in its own try — **`len(resp.content) > 65536` checked BEFORE `resp.json()`** (inside the try; oversized body → failure semantics: stale-serve/503, zero parse); network/HTTP failure → `HTTPException(503, "Session verification unavailable")` if no last-good keys; serve stale if last-good exists. **Never evict last-good keys** on ANY failure (exception, 200-with-empty/`kid`-less keys, malformed entries, **body >64KB**, **200-garbage bodies**); record `_last_failure_at` on ANY failure **including 200-with-zero-usable-keys** (cooldown = failure semantics → no per-request refetch storm on a degraded-but-200 upstream).
  - **Single-flight + failure/miss cooldown** (`_COOLDOWN_S = 30`), both **inside the lock after acquisition**, before the fetch branch (mirrors the double-checked TTL): **if `kid is not None and self._keys is not None and kid in self._keys: return self._keys`** (kid-aware single-flight success path — concurrent force callers share one fetch); a cooldown-skipped fetch with `_keys is None` **raises 503** (never returns None). `force` bypasses TTL but NOT the cooldown. **`_last_failure_at` initializes to `None`; the cooldown check requires `_last_failure_at is not None`** — a fresh process (monotonic < 30s) must never have a falsely-armed cooldown that 503s every cold-start request. **`_last_failure_at` is set ONLY on an actual fetch failure/miss and NEVER refreshed on success or on a cooldown-skipped path** (cooldown-skipped paths perform zero fetches and must not extend the window). **The force path ALSO arms the cooldown when the requested kid is STILL absent after a successful refetch (miss = failure semantics)** — otherwise a healthy-but-kid-absent upstream lets every forged-kid request refetch (unauthenticated JWKS amplification: N requests → N fetches); repeated unknown-kid requests are bounded to one fetch per cooldown window. **Documented tradeoff (rotation-starvation): an attacker flood of forged-kid tokens can keep re-arming the cooldown, delaying a legitimately rotated key's refetch by ≤ cooldown — rotated tokens may 401 during the flood; bounded, acceptable, documented alongside the revocation-window note.**
  - First-fetch semantics: fetch success with zero usable keys → `self._keys = {}` (kid-miss → 401 "Unknown signing key", never a None-attribute crash); fetch failure with no last-good → 503. **200-with-zero-usable-keys is a FAILURE: `_fetched_at` is NOT refreshed** (last-good TTL preserved — consistent with never-evict) and `_last_failure_at` IS recorded; recovery happens via the force path after the cooldown lapses, or via TTL expiry.
  - Usable-keys filter: keep only entries with a **string** `kid` (`isinstance(k.get("kid"), str)` — kid-less entries are DROPPED, fetch still succeeds); keys with wrong-typed kid VALUES (`123`, `true`, `123.5`) are dropped — a JWKS whose usable keys are zero records the failure cooldown (prevents the per-request refetch storm on a degraded-but-200 upstream). Duplicate kids: **first-wins via an explicit loop** (`if kid not in result: result[kid] = k` — note the current comprehension `{k["kid"]: k ...}` is LAST-wins and must be replaced). **The filter + first-wins loop run INSIDE the fetch try** (a string-in-`keys` entry raising TypeError on `isinstance` shares the stale-serve/503 failure semantics — never a raw 500).
- `verify_session_jwt`:
  ```python
  import jwt as pyjwt
  import binascii

  header = _decode_header(token)                # header-only parse, dict-shape check, 16KB length guard (repo-enforced); 401 on malformed
  kid = header.get("kid")
  if not isinstance(kid, str) or not kid.strip():   # zero network I/O on missing/whitespace/non-string kid
      raise HTTPException(status_code=401, detail="Invalid session token")   # early guard — zero network I/O

  # JWKS fetch boundary — 503 on unreachable/no-last-good, stale-serve if last-good exists (inside get())
  try:
      keys = await _jwks.get()
      jwk = keys.get(kid)
      if jwk is None:
          keys = await _jwks.get(force=True, kid=kid)   # R16: kid-aware single-flight, cooldown-aware, eviction-free refetch
          jwk = keys.get(kid)
      if jwk is None:
          raise HTTPException(status_code=401, detail="Unknown signing key")
  except HTTPException:
      raise

  try:
      key = pyjwt.PyJWK.from_dict(jwk)
      claims = pyjwt.decode(
          token, key=key, algorithms=["RS256", "ES256"],
          audience="authenticated",
          issuer=_SUPABASE_URL.rstrip("/") + "/auth/v1",
          leeway=30,
          options={"require": ["sub", "exp", "iat", "iss"], "verify_iat": True, "verify_nbf": True, "strict_aud": True},
      )
  except pyjwt.PyJWTError as e:
      raise HTTPException(status_code=401, detail="Invalid session token") from e
  except (KeyError, ValueError, TypeError, binascii.Error, OverflowError, RecursionError, UnsupportedAlgorithm) as e:
      # PyJWK.from_dict / wrong-typed / out-of-range claim failures are not PyJWTError — fail closed
      # (binascii.Error is a ValueError subclass — kept for self-documentation; RecursionError is
      #  unreachable-defensive: CPython C-json trips it only at ~10k nesting (>16KB guard), but
      #  pure-python json runtimes trip it ~1k levels — keep the entry, no test;
      #  UnsupportedAlgorithm: cryptography backend-constrained envs (FIPS/ancient OpenSSL) —
      #  unreachable on the hosted image, kept for exhaustive fail-closed. ⛔ imported at MODULE
      #  TOP, never inside the except body — except-clause names are evaluated at match time,
      #  before the body runs; an in-body import raises NameError → raw 500 (the exact bug class).
  except Exception as e:
      # Fail-closed completeness: the enumerated tuple above cannot be proven exhaustive against
      # the "never a raw 500" acceptance (an unforeseen class from from_jwk/decode would escape).
      # Auth boundary: ANY unhandled exception → 401. (Nothing in this try raises HTTPException,
      #  so a trailing Exception clause cannot mask it — the "Unknown signing key" 401 lives in
      #  the outer fetch try, which re-raises HTTPException first.)
      raise HTTPException(status_code=401, detail="Invalid session token") from e

  user_id = claims.get("sub")
  if not isinstance(user_id, str) or not user_id.strip():
      raise HTTPException(status_code=401, detail="Invalid session token")    # preserve old truthiness guard; whitespace sub rejected
  app_metadata = claims.get("app_metadata")
  if app_metadata is not None and not isinstance(app_metadata, dict):
      raise HTTPException(status_code=401, detail="Invalid session token")    # no downstream AttributeError
  email = claims.get("email")
  if email is not None and not isinstance(email, str):
      raise HTTPException(status_code=401, detail="Invalid session token")    # consumers do string ops on user["email"]
  return {"user_id": user_id, "email": email,
          "app_metadata": app_metadata or {}}
  ```
- Update stale references: module docstring ("verify RS256 signature" → "verify RS256/ES256 via JWKS"), `oauth.py:15` comment, **`hosted_api.py:8650` OAuth-consent comment** — then **re-grep "RS256" repo-wide, EXCLUDING the e2e harness files (`tests/e2e/hosted/conftest.py`, `test_13_claim.py`, `README.md` — those are Task 3's, still legitimately RS256 at this point)** and fix any remaining stale references.
- **Documented behavior deltas** (comment + issue note): aud REQUIRED + strict (list-form rejected; was: missing aud passed, non-string aud rejected anyway); issuer exact-match (was: substring); `verify_iat` + `verify_nbf` ON (was: both ignored); `exp`/`iat` presence REQUIRED (was: missing exp passed); `exp` leeway inclusive (`exp == now-30` → 401); token size cap 16KB repo-enforced. All intended hardening; Supabase always sets aud/iss/iat/exp and never `nbf`.

**Step 5: Run — expect PASS** for the full matrix.

**Proportionality note:** exact-tick boundary pins (`exp == now-30`, `iat/nbf == now+30`) verify **upstream pyjwt semantics** (confirmed against installed 2.13.0) — annotate them as upstream-semantics regression pins (with a version note) so a pyjwt patch bump doesn't trigger a false alarm. **Wall-clock bounds are NOT asserted** — the stub-counted "exactly ONE fetch" assertions are the source of truth (wall-clock asserts are flaky on loaded CI and redundant).

---

### Task 2: Promote pyjwt to a direct dependency

**Intent:** Make the load-bearing verification dependency explicit (today it's transitive via `mcp` — a future mcp bump could drop it silently). **Dependency-free w.r.t. Task 1/3** — may run in parallel or first.

**Acceptance:** `pyjwt[crypto]` in pyproject `dependencies`; uv.lock updated; requirements.txt regenerated without drift; `uv lock --check` clean.

**Files:**
- Modify: `pyproject.toml`, `requirements.txt`

**Step 1:** Add `pyjwt[crypto]>=2.13,<3` to `[project] dependencies` (alphabetical per existing style).

**Step 2:** Run **`uv lock`** — mandatory: `uv export --frozen` silently exports the OLD lock if uv.lock is stale (verified on uv 0.12.3); without `uv lock` the promotion never lands and the CI parity gate (`uv lock --check` first step in deploy-hosted.yml) fails at deploy.

**Step 3:** `uv export --frozen --no-dev --no-editable > requirements.txt` (matches Dockerfile.hosted).

**Step 4:** Verify: `uv lock --check` clean; `git diff requirements.txt` shows pyjwt direct (was `# via mcp`) with no other churn.

---

### Task 3: Convert E2E harness JWKS mints to EC/ES256

**Intent:** Close the CI gap — the harness must mint the same alg real Supabase uses. **Depends on Task 1 Step 1** (shared helper) **for implementation and Task 1 Step 5 for Step 2's e2e run** (ES256 tokens only verify after the PyJWT swap lands) — do not run Task 3 Step 2 before Task 1 completes.

**Acceptance:** `tests/e2e/hosted/conftest.py::_JWKSKeys` and `tests/e2e/hosted/test_13_claim.py::_JWKS` mint EC P-256 keys + sign ES256 via the shared helper; e2e claim flow passes; constructor/API shape used by callers (conftest `session_jwt` fixture, test_06/08/12, claim tests) unchanged.

**Files:**
- Modify: `tests/e2e/hosted/conftest.py`, `tests/e2e/hosted/test_13_claim.py`, `tests/e2e/hosted/README.md` (stale "minted RS256 JWTs" line)

**Step 1:** Replace RSA keypair + RS256 signing with the shared `tests/_session_jwt_utils.py` helper (EC keypair, `build_ec_jwks`, `mint_es256_token` — PyJWT-encoded, so raw r‖s is correct). Update the JWT header `alg` to ES256 and any `alg` assertions. Keep `_JWKSKeys`/`_JWKS` class APIs intact.

**Step 2:** Run the hosted e2e (claim) suite — expect PASS.

---

### Task 4: Verification (post-commit, post-deploy)

**Acceptance:** The live dashboard login completes end-to-end.

**Steps:**
1. Local: `uv run pytest tests/test_session_auth.py -v` green.
2. PR: code-review gate + CI (fast suite incl. new unit tests).
3. Post-deploy: with a real Supabase session token (dashboard login via GitHub OAuth), confirm `/v1/teams` returns 200 and the dashboard no longer shows the login wall.

### Rejected Alternatives
- **A (in-place `_verify_es256`)**: bespoke raw r‖s→DER ECDSA owned in-house — the incident class; every future alg is new bespoke code.
- **C (GoTrue `/auth/v1/user` per request)**: per-request RTT + availability coupling + selfhost regression; contradicts Supabase JWKS guidance for asymmetric projects.
- **PyJWKClient-native fetch**: sync urllib on the async path + no stale-serve.
- **Dropping RS256 from the allowlist**: would orphan older RS256 Supabase projects/selfhost installs; dual-alg + an RS256 positive test is the better outcome at negligible cost.

### Deferred (explicit, not absorbed)
- **ACAO-on-unhandled-500 hygiene** (stamp CORS headers on the server's unhandled-exception path so future raw 500s are visible as 500s): separate follow-up issue — the verify-path fix converts this bug class to clean HTTPExceptions, but OTHER routes can still produce raw 500s the browser keeps misreading as CORS failures.
- **Bounded revocation window**: a rotated-away key keeps verifying until TTL + cooldown + refetch timeout during an upstream outage (availability-vs-security tradeoff, asserted by test + comment). No separate issue — deliberate, documented.

<!-- plan-review: cycles=9, status=clean, version=2.3.0 -->
