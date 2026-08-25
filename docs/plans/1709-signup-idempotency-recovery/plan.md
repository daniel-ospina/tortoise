<!-- research-path: docs/plans/1709-signup-idempotency-recovery/scope.md -->

# #1709 — Server-side signup idempotency + keyless recovery (Approach C) — Implementation Plan

> **Approved scope:** `docs/plans/1709-signup-idempotency-recovery/scope.md` (the design contract; 3 solution-verify cycles, P0=0 P1=0; O/I-T re-anchoring RATIFIED by the controller 2026-08-25).
> **For Pi:** plan-inline (task-workflow-standard, gate already passed at scope) → implement directly. Commit per commit-workflow pre-flight through PR creation; controller merges.

**Goal:** Server-issued 256-bit `st_<64hex>` signup token minted at first signup (hash-only at rest), re-presenting the token IS the dedupe check AND the recovery credential. #741(a) preserved literally — `tests/test_agent_signup.py` stays BYTE-IDENTICAL (proof of no reversal).

**Team:** epistemic-team · **Role:** product-implementer

---

## Design Decisions (inline; all traced to scope)

- **D1 — new tests live in a NEW file `tests/test_agent_signup_idempotency.py`** (task brief: test_agent_signup.py BYTE-IDENTICAL; the scope's §6 bullet headers name behaviors, not files).
- **D2 — `recover_team_key` RPC returns team_id; caller pre-reads the teams row** for team_name/tier/suspension. Deleted team → uniform 422 (caller pre-check); the RPC's own deleted/zero-row RAISE is the fail-closed backstop mapped to 422 on known codes, unknown → 500.
- **D3 — recovery limiter ordering:** `_check_recovery_rate_limit(request, token_hash)` runs per-IP FIRST then per-token; malformed tokens (no valid hash) still burn the per-IP bucket but skip the per-token cap (key is the well-formed hash; a malformed string has no stable bucket).
- **D4 — recovery-velocity feed** = new `RecoveryVelocityTracker` in abuse.py mirroring `SignupVelocityTracker` (threshold 5/24h, notify kind `abuse_recovery_velocity`, dashboard event `recovery_velocity`). Fired on BOTH recovery surfaces, never on mint.
- **D5 — registry lane serialization:** `signup_token_recover` wraps check+mint+revoke in a process-local `threading.Lock` (FalkorDB has no transactions; parity with the Supabase FOR UPDATE).
- **D6 — CLI non-interactive behavior:** the "type YES you saved it" prompt and the 422 warn+confirm prompt only fire on a TTY (`sys.stdin.isatty()`); non-TTY → warning printed, mint proceeds (save-prompt) / fail-closed exit 1 (422 orphan-guard, per scope P3: a revoked token must not silently orphan the team).
- **D7 — CLI config path:** main's current shape (`cwd/.tortoise`) stays; `signup_token` is an ADDITIVE field. Read of the stored token checks the active config path (cwd, else `~/.tortoise/credentials.json` if present — forward-compat with #1708's global file). `recover` writes the SAME shape and persists the token (scope: recovery surface is not one-shot-only).

---

## Task 1 — Migration `supabase/migrations/20260814000001_agent_signup_tokens.sql`

**Intent:** `agent_signup_tokens` table (hash-only, one-token-per-team) + three service_role-only SECURITY DEFINER RPCs (new-named wrapper — NO overload of `provision_team`; CREATE OR REPLACE with trailing param empirically creates an overload on PG16, cycle-2 P1).

**Acceptance:** PGlite harness applies the migration + new SQL suite passes (grants asserted via `has_function_privilege`; anon/authenticated DENIED).

- Table: `token_hash text PRIMARY KEY`, `team_id text NOT NULL`, `created_at timestamptz DEFAULT now()`, `last_used_at timestamptz`, `revoked_at timestamptz`; partial unique `uq_agent_signup_tokens_team ON (team_id) WHERE revoked_at IS NULL`; `REVOKE ALL ... FROM public, anon, authenticated`; `GRANT SELECT, INSERT, UPDATE ... TO service_role`.
- `provision_team_with_token(...)` — 15 provision_team params + `p_signup_token_hash text DEFAULT NULL`; `PERFORM public.provision_team(p_user_id => ..., ...)` (NAMED args, all 15) then token `INSERT ... ON CONFLICT (token_hash) DO NOTHING`; whole body one tx (a failed provision rolls back the token insert).
- `resolve_signup_token(p_token_hash) RETURNS text` — `SELECT team_id ... WHERE token_hash=... AND revoked_at IS NULL` + `UPDATE ... SET last_used_at=now()` (one tx).
- `recover_team_key(p_token_hash, p_team_id, p_api_key, p_key_hash, p_lookup_hash, p_key_prefix) RETURNS text` — `SELECT ... FOR UPDATE` (serializes same-token recovery); zero rows → RAISE; reject `teams.deleted_at`; cap = count `created_via <> 'bootstrap'` active keys; at `max_api_keys` revoke OLDEST non-bootstrap; INSERT api_keys `created_via='recovery', created_by='st_'||left(p_token_hash,12)`; RETURN p_team_id.
- REVOKE/GRANT service_role-only for all three functions.
- **Files:** Create `supabase/migrations/20260814000001_agent_signup_tokens.sql`; Create `supabase/tests/20260814000001_agent_signup_tokens.sql` (assert-RAISE suite per 0010 conventions, suffix `-1709`); Modify `supabase/tests/pglite/validate.mjs` (files list += 20260813000006 + the new migration; suites += new test file; spot-check additions for grant assertions — assert via `has_function_privilege` inside the SQL suite instead).

## Task 2 — `tortoise/supabase_control.py` wrappers

**Intent:** Python seam for the three RPCs (mirror `provision_team`/`claim_membership` styles).

**Acceptance:** wrappers call `cp.rpc` with the exact param names; `resolve_signup_token` returns `str | None`; `recover_team_key` returns team_id or raises RuntimeError with the RPC code embedded.

- `provision_team_with_token(cp, **params)` → `cp.rpc("provision_team_with_token", params)` (keeps the post-RPC pack activation hook pattern? — NO, the wrapper is signup-only; pack activation stays on the plain `provision_team` path for other callers; signup's `ensure_tenant_packs` call is unchanged via its own post-step).
- `resolve_signup_token(cp, token_hash) -> str | None` — tolerant parse of PostgREST scalar encoding (dict {fn: val} / list / bare scalar); None for not-found.
- `recover_team_key(cp, *, token_hash, team_id, api_key, key_hash, lookup_hash, key_prefix) -> str` — RPC; map nothing here (hosted_api maps codes).

## Task 3 — `tests/fake_control_plane.py`

**Intent:** fake `rpc()` emulates the three functions over the in-memory rows (the concurrency E2E depends on `recover_team_key` being atomic in the fake).

**Acceptance:** `provision_team_with_token` writes the token row in the same call as the provision; `resolve_signup_token` returns team_id or None; `recover_team_key` does cap-check + revoke-oldest + insert atomically (under a `threading.Lock` — mirrors the FOR UPDATE serialization).

## Task 4 — `tortoise/abuse.py` recovery-velocity feed

**Intent:** ops signal for the recovery surface (scope: "ops metrics must not conflate recoveries with mints").

**Acceptance:** `RecoveryVelocityTracker` (threshold default 5/24h, env `TORTOISE_ABUSE_RECOVER_THRESHOLD`/`TORTOISE_RECOVER_IP_LIMIT` fallback), `record_recovery(ip, team_id)`, module-level `RECOVERY_TRACKER`, notify kind `abuse_recovery_velocity`, dashboard event `recovery_velocity`.

## Task 5 — `tortoise/sdk.py` registry lane

**Intent:** SignupToken node CRUD + expires_at parity (scope §4).

**Acceptance:**
- `signup_token_lookup(plaintext) -> dict | None` via `_verify_hashed_lookup("SignupToken", "token_hash", ...)` filtered `revoked_at IS NULL` (Invitation precedent).
- `signup_token_recover(plaintext) -> dict` — team deleted check + process-lock serialized cap-check + mint `created_via='recovery'`, `created_by='st_'+left(token_hash,12)`, `expires_at=None`; returns `{api_key, team_id, team_name, tier, graph_name}`.
- `apikey_verify`: add `(expires_at IS None or expires_at > now)` filter (NULL-as-never-expires; legacy nodes keep authenticating).

## Task 6 — `tortoise/hosted_api.py`

**Intent:** agent_signup rework + new recover endpoint + registry mint parity (scope §2-§4).

**Acceptance:**
- Parse body FIRST; `signup_token` only from BODY (never headers).
- Token-present branch → shared `_agent_recover_flow`: `_check_recovery_rate_limit` (per-IP 5/24h + per-token 10/h, IP extraction identical to `_check_signup_ip_rate_limit`), `_resolve_signup_token`, uniform 422 `invalid_signup_token` for malformed/unknown/revoked/deleted, 403 `_suspended_detail()` for suspended, keyless recovery via `recover_team_key`, audit `agent_signup_recover`, recovery feed (NOT record_signup). Response `{key, team_id, team_name, graph_name, tier}`.
- No-token mint: `_check_signup_ip_rate_limit` (unchanged 2/24h), `provision_team_with_token` (15 named + token hash), response gains `signup_token`; registry lane adds SignupToken node + APIKey `created_via:'provisioned'`/`expires_at:null` + SignupToken in the #741(c) rollback block.
- `POST /v1/agent/recover` — body `{signup_token}`, same flow, same shared limiter.
- New module helpers: `_RECOVER_BUCKETS`/`_RECOVER_TOKEN_BUCKETS`/locks, `_check_recovery_rate_limit(request, token_hash)`, `_hash_signup_token`, `_resolve_signup_token(cp, token)`.

## Task 7 — CLI (`tortoise/__main__.py`)

**Intent:** token persistence + recovery UX (scope §5).

**Acceptance:**
- `_cmd_signup`: persists `signup_token` from mint response; prints the recovery-token save prompt (TTY-only confirm); re-signup reads stored token → includes in body; 422 → warn + (TTY confirm | non-TTY fail-closed exit 1) before clearing token + fresh mint; 403 suspended → fail-closed exit 1.
- New `tortoise recover --token st_...` subcommand → POST /v1/agent/recover → writes config incl. persisted `signup_token`.
- Recovery-success rewrite keeps the stored token.

## Task 8 — Tests

**Intent:** prove the E2E acceptance criteria (scope §6).

**Acceptance:**
- `tests/test_agent_signup_idempotency.py` (NEW): mint returns `st_` token; sequential token re-signup → same team_id + NEW key + no second team; uniform 422 across malformed/unknown/revoked/deleted; suspended → 403; token-present bypasses mint limiter; token-present bound by recovery limiter (lock test); `/v1/agent/recover` happy path; registry-lane variants; concurrency E2E via `asyncio.gather` + `httpx.AsyncClient(ASGITransport)` (N=5 same-token → 1 team, shared team_id, non-bootstrap keys ≤ 2); parallel no-token mints → N teams.
- `tests/test_cli_signup.py`: token persisted; 422 warn+confirm path; `recover` subcommand happy path (mock urlopen).
- `tests/test_writer_inventory.py`: pin `rpc_calls[0]` = `provision_team_with_token` + `p_signup_token_hash` + token row landed.
- Registry legacy-node test: APIKey node without `expires_at` still verifies.
- PGlite SQL suite (Task 1).
- `tests/test_agent_signup.py` UNTOUCHED (byte-identical check via `git diff`).

## Verification
- Docker lane: `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_agent_signup.py tests/test_agent_signup_idempotency.py tests/test_writer_inventory.py tests/test_dashboard_login.py tests/test_session_login.py tests/test_hosted_api.py -v` + full-suite regression.
- Carve-out: `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_embedded_lifecycle.py tests/test_guard.py -v`.
- `npm --prefix supabase/tests/pglite run validate`.
- `ruff` on changed files.
