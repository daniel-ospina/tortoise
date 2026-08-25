# Scope — #1709: Server-side idempotency + keyless recovery for agent signup

> issue-scoping v5.1 double diamond — **Complex tier**, project level, standalone epic.
> Problem diamond VALIDATED by #1708's verification gates (2 fresh-context verifiers) — this scope runs the SOLUTION diamond only.
> Solution diamond: 2 diverge agents (security lens + product/recovery lens) both independently ranked **Approach C** first; controller converged on C with the quality-over-convenience rule. See `## Verification Gates` for the solution-verify cycle log.

## Confirmed Problem

**POST /v1/agent/signup (hosted_api.py:6856) mints a fresh team + `tt_` key on every call; making it idempotent per signup_token (post first mint — see the O/I-T deviation block below for the identity→token re-anchoring) must NOT weaken the #741(a) invariant and must NOT create an existence oracle, a lockout primitive, a TOCTOU race, or an email-registration oracle — and a keyless recovery path is a prerequisite** (hash-only key storage means a dedupe-hit can never return the key; anon users have no recovery channel today).

### O/I-T Scope Deviation (REQUIRES RATIFICATION)

C amends issue O/I-T **Indicators 1-2** and **Targets 2-3** — the issue as written assumes an identity-based dedupe ("same device identity → 1 team + 1 key"; "one-team-per-identity unique constraint"). Under C there is **no client identity at all** (that is the point — #741(a) is preserved by never trusting one):

| Issue text | C amendment | Why |
|---|---|---|
| "concurrent signups with the same device **identity** → 1 team" | "same **signup_token** after first mint → 1 team" | A pre-mint identity is impossible without reversing #741(a) (the issue's own red line). Parallel no-token first mints → N teams == today, IP-bounded 2/24h |
| "exactly 1 team + **1 key**" | "1 team; keys ≤ `max_api_keys` (2 on free). Recovery rotates (revokes-oldest-non-bootstrap at cap); concurrent recovery is serialized by the token-row lock so the cap cannot overshoot" | Keys are not recoverable by design (hash-only); concurrent recovery is a key mint, not a dedupe violation |
| "one-team-per-identity enforced at the DB level" | "**one-token-per-team** enforced at the DB level (uq_agent_signup_tokens_team)" | There is no identity column to constrain |
| "keyless config-loss recovery path" | Token-possession recovery (POST /v1/agent/recover) + support runbook floor | Unchanged in intent |

**Ratification — BLOCKING go/no-go:** the issue owner must approve this re-anchoring (or reword the O/I-T) as a **pre-implementation checkpoint** before any code lands — NOT a trailing Phase 8 note. If the owner instead prefers approach A (server-derived identity), the whole plan invalidates; the gate must fire before implementation. It is surfaced as a blocking checkpoint in the Phase 8 plan comment and in the issue body amendment.

## Chosen Approach — C: Server-issued `signup_token` (capability-based idempotency + recovery)

**The device never presents an identity. It presents a server-issued 256-bit bearer token (`st_<64 hex>`), minted at first signup, stored hash-only server-side.** Re-presenting the token is simultaneously the dedupe check AND the recovery credential: there is no separate "dedupe-hit" response, because an unauthenticated request either mints (no token) or gets a uniform 422 (invalid token). **The issue's premise that "server-side dedupe requires reversing #741(a)" is false for this approach — #741(a) is preserved literally** (identity stays server-minted `anon-{uuid12}`, `test_client_identity_ignored` passes unchanged).

### Why C wins (quality over convenience)

| Issue constraint | C mechanism |
|---|---|
| #741(a) not weakened | Preserved literally — client identity AND x-device-id still ignored; no client-chosen anchor exists |
| Existence oracle | Vacuous: no unauthenticated dedupe path exists. No-token → always mints; bad token → uniform 422 `invalid_token` (same body for format-error and not-found) |
| Lockout primitive | None by construction: a token exists only after the victim's mint and only the victim holds it. There is no identity namespace to pre-squat; 128-bit tokens are unguessable |
| TOCTOU | No pre-check-then-mint anywhere: team mint occurs only on the no-token path; token rows are unique-constrained inserts inside the atomic provision_team RPC; recovery never creates a team |
| reg- collision oracle | Impossible — no client-supplied string is ever used as an identity; nothing can collide with `reg-{sha256(email)[:12]}` (hosted_api.py:2993) |
| Keyless recovery | Token IS the recovery credential: `POST /v1/agent/recover {signup_token}` → verifies hash → mints a NEW key on the SAME team (created_via='recovery', already admitted by the 0007 CHECK enum) — no 409 dead-end |
| Post-claim survival | `agent_signup_tokens` is team-scoped and claim (20260813000004) touches only team_memberships + teams.email → the token survives claim; recovery keeps working post-claim |
| Concurrency E2E | Parallel token-present calls → 1 team (≤2 keys, within free cap, mirroring #750.10 revoke-oldest-other semantics). Parallel no-token mints → N teams (== today, IP-bounded 2/24h) |
| Broken phantom-key precedent (sdk.py:10792) | Never copied: recovery returns a PERSISTED key minted through the real api_keys insert path; no "existing" path fabricates a key |
| Registry lane parity | SignupToken node (token_hash unique) + APIKey node gains created_via/expires_at + `apikey_verify` gains expires_at filtering (sdk.py:11299 — currently ignores it) |

### Top risk (weakest assumption, second-model)

The recovery story assumes an out-of-band human save-point ("type YES you saved it"), but the primary agent_signup client is headless and ephemeral CI has no persistence — the "config lost AND token not saved" boundary covers a LARGE share of the target population. Mitigation: the save-prompt UX (confirm-prompt, one artifact), claim-before-loss posture (already in the CLI), and the per-IP limiter remaining the control for ephemeral installs. This is the design's known weakest point; the recovery VALUE concentrates in the human-assisted-agent niche.

### What the user experiences

- **First mint** — `tortoise signup` → `{key, signup_token, team_id, team_name, graph_name, tier}`; CLI persists both to `~/.tortoise/credentials.json` (0600) and prints "RECOVERY TOKEN — save this: it is the only way back into this team if your key is lost" (confirm-prompt UX; the token is the single save point, mirroring the key's shown-once contract).
- **Config intact, key revoked/expired** — re-signup presents the stored token → recovery mints a fresh key on the same team; CLI rewrites config. No support, no 409.
- **Config lost, token saved** — `tortoise recover --token st_...` → new key for the same team (data intact).
- **Claimed team, token saved** — token survives claim; recovery still rotates a key; dashboard session-key path remains the alternative.
- **Config lost AND token not saved** — honest boundary (documented in O/I/T): the per-IP limiter permits a fresh mint of a new team (old data orphaned), or support runbook (audit_events detail JSONB gives IP/claim history; `appeal_url()` exists). No approach can recover an unprovable anon ownership — the token-save prompt makes this the exception, not the rule.
- **Ephemeral CI** — no persistence → no token → dedupe never fires → per-IP limiter (2/24h, #1081) is the control, unchanged.

### Known limitation (documented, accepted)

**Lost-response window:** if the mint commits server-side but the response never reaches the client, the client has no token AND no key — the recover endpoint is NOT an escape hatch here (it requires the token that was just lost). The honest escape hatch is: wait out the 2/24h window → fresh mint (old team orphaned), or support runbook. Bounded by: (a) #1708's reuse-before-mint (signup fires only when no valid config exists), (b) the 2/24h per-IP limiter — **note the retry consumes the second 2/24h slot, so a subsequent same-day mint is 429'd (== today's behavior, not a regression)**, (c) the orphaned team is empty and harmless. (The "recover endpoint (own bucket)" escape hatch applies to the DIFFERENT scenario: token-in-hand but mint 429'd.) Closing the window FULLY requires a client-held **pre-mint** anchor (B/A — the #741(a) reversal, refused); a server-issued two-step (begin/complete) SHRINKS but does not close it (the begin token itself can be lost) and was rejected as over-machinery for a window already bounded by #1708 reuse + the IP limiter (see Rejected Alternatives).

## Implementation Plan

### 1. Migration — `supabase/migrations/20260814000001_agent_signup_tokens.sql` (timestamp style; add to `supabase/tests/pglite/validate.mjs` files list — the list is currently stale and omits 20260813000006)

⛔ **Placement rule (solution-verify P1, Cycle 2 — empirically verified):** this is a NEW timestamped file. It does NOT edit 0010 (applied in prod; the `migration-append-only` CI gate rejects edits to merged migrations). **`CREATE OR REPLACE` with a trailing param is NOT used — it creates a second OVERLOAD on PG (proven on PG16), leaving the old 15-arg `provision_team` live and making old-arity PostgREST calls ambiguous; the new overload would also inherit Supabase's default function ACL (anon/authenticated executable — an unauthenticated mint primitive).** Instead: `provision_team` stays untouched at 15 args, and the signup path calls a NEW-named wrapper. The file contains the table + THREE functions:

```sql
CREATE TABLE public.agent_signup_tokens (
    token_hash   text PRIMARY KEY,          -- SHA-256(PEPPER + token), computed in tortoise/auth.py (never in SQL)
    team_id      text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    last_used_at timestamptz,
    revoked_at   timestamptz
);
CREATE UNIQUE INDEX uq_agent_signup_tokens_team ON public.agent_signup_tokens (team_id) WHERE revoked_at IS NULL;
REVOKE ALL ON public.agent_signup_tokens FROM public, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON public.agent_signup_tokens TO service_role;

-- (1) provision_team_with_token(...) — NEW-named wrapper (NO overload of provision_team):
--     15 params mirroring provision_team + p_signup_token_hash text DEFAULT NULL.
--     SECURITY DEFINER, SET search_path = '', service_role ONLY:
--     BEGIN
--       PERFORM public.provision_team(p_user_id => ..., p_identity => ...,
--                                    ... p_graph_size_cap => ...);  -- NAMED-ARG notation:
--                                    immune to param reordering (all 15, verified in 0010)
--       IF p_signup_token_hash IS NOT NULL THEN
--         INSERT INTO public.agent_signup_tokens (token_hash, team_id)
--         VALUES (p_signup_token_hash, p_team_id)
--         ON CONFLICT (token_hash) DO NOTHING;
--       END IF;
--     END;  -- exception in either statement rolls back the WHOLE mint (1 team + 1 token atomic)
--     ⛔ Atomicity contract: depends on provision_team RAISING on every failure mode (it does
--     today: RETURNS void, no internal EXCEPTION handler, RAISE on invalid params). Pin with a
--     PGlite test: a failed provision → no token row. All table refs are public.-qualified
--     (empty search_path — mirror 0010). Signup path only; other callers keep calling
--     provision_team (unchanged, 15 args).

-- (2) resolve_signup_token(p_token_hash text) RETURNS text — SECURITY DEFINER, SET search_path='',
--     service_role ONLY:
--     SELECT team_id FROM public.agent_signup_tokens
--     WHERE token_hash = p_token_hash AND revoked_at IS NULL;
--     UPDATE public.agent_signup_tokens SET last_used_at = now()
--     WHERE token_hash = p_token_hash;
--     (single tx: token-verify + last_used_at touch atomic; caller checks team suspended/deleted after)

-- (3) recover_team_key(p_token_hash text, p_team_id text, p_api_key text, p_key_hash text,
--                      p_lookup_hash text, p_key_prefix text)
--     RETURNS text — SECURITY DEFINER, SET search_path='', service_role ONLY, ONE transaction:
--     - SELECT team_id FROM public.agent_signup_tokens WHERE token_hash = p_token_hash
--       AND revoked_at IS NULL AND team_id = p_team_id FOR UPDATE;   -- ⛔ row lock: serializes
--       concurrent recoveries for the same token (READ COMMITTED check-then-insert race closed)
--       ⛔ IF NO ROWS RETURNED → RAISE EXCEPTION (revoke-race or team-mismatch — fail closed;
--       caller maps to the uniform 422). Never mint on a zero-row lock.
--     - reject suspended teams (403 signal via caller) / soft-deleted teams (public.teams.deleted_at)
--     - cap: count active keys (created_via != 'bootstrap'); at max_api_keys, revoke the OLDEST
--       non-bootstrap key (deterministic; mirrors #750.10 revoke-oldest-other — the token path has
--       no "presenter's own")
--       ⛔ 402 is unreachable today on the token path, full stop (the cap check counts
--       created_via != 'bootstrap', so a bootstrap-only team is under-cap and never hits it;
--       once count ≥ max_api_keys there is always a non-bootstrap key to revoke — the token
--       path has no "own" key). Add the 402 if a future rule protects st_-attributed keys.
--       The E2E contract (non-bootstrap ≤ max_api_keys) is authoritative.
--     - INSERT INTO public.api_keys (..., created_via='recovery',
--       created_by='st_' || left(p_token_hash, 12))   -- derived INSIDE the RPC (not caller-supplied)
--     - RETURN p_team_id

-- ⛔ Grant hygiene for ALL THREE new functions (Supabase's ALTER DEFAULT PRIVILEGES grants
-- ALL ON FUNCTIONS to anon/authenticated — an anon-executable resolve_signup_token is a direct
-- token-existence + team-association oracle; an anon-executable wrapper is an unauthenticated
-- mint). Mirror 0010:185-186 for each:
--   REVOKE ALL ON FUNCTION public.provision_team_with_token FROM public, anon, authenticated;
--   GRANT EXECUTE ON FUNCTION public.provision_team_with_token TO service_role;
--   (same pair for resolve_signup_token and recover_team_key)
```

- token_hash construction: `SHA-256(PEPPER_BYTES + token.encode())` — byte-identical to the `lookup_hash` construction (auth.py:119-131; `_PEPPER_BYTES` is the encoded pepper). Domain separation from api-key lookup hashes is by prefix (`st_` vs `tt_`) — note in the plan. Python-only (no TS mirror needed — no edge function touches agent signup tokens).
- Registry lane precedent for hashed-token lookup: `sdk.py:11394` `_verify_hashed_lookup("Invitation", "token_hash", ...)` — reuse the pattern for the SignupToken node.
- Unique constraint requirement from the issue ("one-team-per-identity") → reframed as **one-token-per-team** via `uq_agent_signup_tokens_team` (see O/I-T deviation block above).

### 2. `agent_signup` (hosted_api.py:6856)
- Parse optional `signup_token` from the BODY (never a header — proxies log headers; X-Device-Id stays ignored per #741(a)).
- **Rate-limit ordering (solution-verify P1):** the 2/24h mint limiter (hosted_api.py:2060) currently runs pre-parse at :6876. The mint bucket must bound **minting only** — parse the body first and apply `_check_signup_ip_rate_limit` ONLY on the no-token path. A token-present request is a recovery (possession-authenticated), never a mint, and must not consume or be blocked by the mint bucket. ⛔ **But the token-present branch gets a COMPENSATING recovery limiter (Cycle-2 P1):** it performs the same `recover_team_key` mint as `/v1/agent/recover`, so it SHARES the recovery rate limiter (per-IP bucket + per-token attempt cap + recovery-velocity feed) via the same `_check_recovery_rate_limit` helper — a stolen token must not enable unbounded mint/revoke churn on that surface. Lock test: an IP over the recovery per-IP cap is 429'd on token-present signup too.
- **Token present:** validate format (`st_` + 64 hex), hash, `resolve_signup_token` (RPC):
  - *Design note (second-model):* the canonical recovery surface is `/v1/agent/recover`; the token-present signup branch exists ONLY as an orphan-prevention safety net for legacy/buggy clients that re-signup while holding a token (recover instead of orphaning the team). Both call the same RPCs + shared limiter.
  - found + not revoked + team not deleted/suspended → **keyless recovery** via `recover_team_key` RPC (FOR-UPDATE serialized cap-check + key insert + #750.10 revoke-oldest-non-bootstrap in ONE transaction; `created_by` derived inside the RPC as `'st_' || left(token_hash, 12)`) → return `{key, team_id, team_name, graph_name, tier}`. Token-authenticated → team echo is possession-based, NOT an oracle; the CLI config write needs team_id. (O/I-T reconciliation: this response contains a NEWLY MINTED key, not a dedupe-hit replay — "dedupe-hit contains no key" means no fabricated/unpersisted key and no key to unauthenticated parties; a token-holder's key is a recovery mint.)
  - **suspended team (valid token)** → **403 `_suspended_detail()`** — possession-authenticated (the presenter's valid token already proves the team exists, so 403 adds no oracle), and consistent with the platform convention (hosted_api.py:7485); the CLI fails closed (exit 1, no fresh mint).
  - not found / revoked / malformed / **team soft-deleted** → **uniform 422** `{"error_code": "invalid_signup_token"}` (identical body for all four — no existence signal; a deleted team is indistinguishable from never-existed, so 422 is correct there).
  - Success feed: token-present recovery fires the **recovery-velocity feed** (NOT `record_signup` — ops metrics must not conflate recoveries with mints).
- **Token absent:** current mint (identity `anon-{uuid12}` unchanged) + `provision_team_with_token(<15 named args>, p_signup_token_hash=hash(st_<64hex>))` (the NEW-named wrapper — NOT provision_team with a phantom param) + response gains `signup_token` (additive → backward-compatible).
- Response-shape note (contract hygiene): mint returns `identity`; token-present recovery omits it. Documented, harmless (CLI doesn't consume `identity`); kept minimal.

### 3. New endpoint — `POST /v1/agent/recover` (hosted_api.py, alongside agent_signup)
- Body `{signup_token}`; same verification as the token-present signup path (shared helper `_resolve_signup_token(cp, token) -> team_id | None` wrapping the RPC).
- **Rate limiting — its own bucket, NOT the 2/24h signup bucket:** shared `_check_recovery_rate_limit` helper used by BOTH `/v1/agent/recover` AND the token-present signup branch: per-IP bucket (e.g., 5/24h, `_RECOVER_BUCKETS` pattern) + per-token attempt cap (e.g., 10/h, keyed on the token **hash**, not the raw token) + recovery-velocity feed (mirror SignupVelocityTracker at abuse.py:678; ops email + dashboard alert parity). Precision (Cycle-3 P4): bucket counts **attempts** (invalid-token probes burn the per-IP bucket — acceptable given the uniform 422, but decided); 429 body error_code = `over_recovery_ip_rate_limit` (mirrors `over_signup_ip_rate_limit`); **IP extraction is IDENTICAL to `_check_signup_ip_rate_limit`** (`request.state.client_ip` fallback chain — otherwise the shared bucket splits into two half-caps; unit test asserts the same key from both endpoints).
- Returns `{key, team_id, team_name, graph_name, tier}`; CLI writes config. Registry lane: same flow against the SignupToken node (sdk.py `signup_token_lookup` / `signup_token_recover` methods).
- **Token revocation lifecycle (solution-verify P1 + Cycle-2 P2/Cycle-3 P3):** `revoked_at` is written by the **support runbook only** — documented steps: (1) verify ownership via audit_events detail JSONB (claim/IP history) + `appeal_url()` channel; (2) audited SQL revoke of the token; (3) **revoke keys minted via this token** — `created_via='recovery' AND created_by = 'st_'||left(token_hash,12)`, correlated with audit_events timestamps (keys minted after the last owner-confirmed mint / after the reported compromise); note `created_by` is **token-attributable by design** — it identifies the token, not the human, so the correlation is timestamp-based, not owner-based; (4) **re-credential the verified owner** — mechanism MUST be chosen: (a) an internal audit-gated support tool (app context, has the pepper) that generates a fresh token + key and writes both rows, or (b) explicitly out of the floor: "post-revoke the owner must fresh-signup (new team, old data orphaned) or restore from backup" — the pepper lives in app code, NOT SQL, so plain SQL cannot mint a fresh token/key; pick one in the plan; (5) confirm. Registry lane (selfhost): `MATCH (s:SignupToken {token_hash}) SET s.revoked_at = $now` — operator DB access (documented). A leaked token is the same compromise class as a leaked key (0600 file, hash-only at rest) and the runbook closes it. Token rotation on recovery (mint a fresh token each recovery, revoke the old) is REJECTED: the lost-response window would strand a user whose rotated token never arrived. A user-facing revoke action (post-claim dashboard/CLI "revoke recovery token") is a follow-up issue.

### 4. Registry lane parity (selfhost, FalkorDB)
- Mint path (hosted_api.py:6980-7000): add `CREATE (:SignupToken {token_hash, team_id, created_at})` + APIKey node gains `created_via: 'provisioned'` and `expires_at: NULL` props (parity with Supabase lane — owned by THIS issue per the task brief). ⛔ **The SignupToken node creation MUST be added to the #741(c) rollback block** (hosted_api.py:7010-7020 DETACH DELETE Team/APIKey/Membership + graph drop) — a failed registry mint must not leave an orphan SignupToken pointing at a deleted team. Test the partial-failure case.
- `sdk.py apikey_verify` (:11299-11315): add `expires_at` filtering with **NULL-as-never-expires semantics** — `expires_at IS NULL OR expires_at > now` — mirroring the REST path (hosted_api.py:1214-1225). Without the NULL clause, every legacy selfhost key (no expires_at prop) would stop authenticating. Add a legacy-node test.
- Existing legacy APIKey nodes lack created_via/expires_at → display fallback: created_via NULL → "legacy", expires_at NULL → "never" (dashboard heuristic); AC-7 pins the NEW-key behavior only. A one-shot backfill pass is optional/out of scope.
- Recovery: `signup_token_lookup` + `signup_token_recover` sdk methods against the SignupToken node (reuse the `_verify_hashed_lookup("Invitation", ...)` precedent at sdk.py:11394). Concurrency: token rows are new nodes (no pre-existing duplicate ambiguity — unlike identity-based dedupe, which would need a reconciliation pass over the 14-key incident history).

### 5. CLI (`tortoise/__main__.py` + credentials file)
- ⛔ **Sequencing contract (solution-verify P1):** #1708 currently has ZERO committed code (branch == main; plan-only). Merge order is **#1708 → #1709**. This issue defines the token-persistence contract INDEPENDENTLY so it cannot strand on #1708 details: field `signup_token` in the credentials store (0600; `~/.tortoise/credentials.json` once #1708 lands, else the current `.tortoise` config — the field is additive in either case). E2E-7 (list_api_keys created_via/expires_at) is a #1708 deliverable — this issue VERIFIES it, doesn't build it.
- Persist `signup_token` in the credentials store; print the recovery-token prompt at mint (confirm-prompt "type YES you saved it").
- Re-signup path: if credentials contain a signup_token, send it; on 422 `invalid_signup_token` → **warn the user first** ("your recovery token is invalid — this will create a NEW team; the old team will be unreachable") and require confirmation before clearing the token + minting fresh (a revoked or truncated token must not silently orphan the original team — solution-verify P3); on 403 (suspended team) → **fail closed, exit 1** with the suspended message — no fresh mint, no orphan prompt (Cycle-2 P3: the CLI must not push a suspended-team holder toward orphaning their team); on success (recovery) → rewrite config.
- New subcommand `tortoise recover --token st_...` (config-loss-with-token case) → POST /v1/agent/recover → write config. ⛔ **On the recover path the CLI MUST persist the token into the credentials store** (same 0600 `signup_token` field as the mint path) — the recover endpoint does not re-issue tokens (rotation rejected), so without persistence the recovery surface is one-shot-only and the NEXT key-loss incident would silently fresh-mint and orphan the recovered team.
- No change to the reuse-before-mint logic from #1708 (it makes signup rare; this is the backstop).

### 6. Testing
- `tests/test_agent_signup.py`:
  - Mint returns `signup_token` (starts `st_`, 64+ chars); re-signup with token → same team_id, NEW key, no second team (sequential).
  - **Uniform 422 across FOUR cases** (assert identical body): malformed, unknown, revoked, and valid-token-but-team-soft-deleted — the oracle-free contract. **Suspended team is a distinct 403** `_suspended_detail()` (possession-authenticated; platform convention).
  - **Token-present recovery bypasses the mint limiter** (an IP at the 2/24h limit can still recover with a valid token) **AND is bound by the recovery limiter** (an IP over the recovery per-IP cap is 429'd on token-present signup too — the compensating control lock).
  - `test_client_identity_ignored` and the rest of the #741(a) suite **unchanged** (C preserves them byte-for-byte — the deliverable proof that no reversal happened).
  - Registry-lane variants (default lane — no TORTOISE_CONTROL_PLANE set).
- **Concurrency E2E (new — none exists today):** REAL parallel dispatch via `asyncio.gather` of N client POSTs (the `_UniqueViolation` pattern at test_writer_inventory.py:468 is a FakeControlPlane error-mapping SEAM, not a concurrency mechanism — cite it only for the fake's atomic token-insert emulation). Assertions: same-token parallel → exactly 1 team, all responses share team_id, **active non-bootstrap keys ≤ max_api_keys (2 on free)** — the FOR-UPDATE row lock in `recover_team_key` serializes concurrent recoveries so the cap cannot overshoot; an over-cap recovery revokes the oldest non-bootstrap key and mints (402 only in the bootstrap-only edge). Parallel no-token mints → N teams (documented = today's behavior).
- `tests/fake_control_plane.py`: extend `rpc()` for `p_signup_token_hash` (insert token row in the same emulated transaction) + `resolve_signup_token` + `recover_team_key` (atomic cap-check + insert).
- `tests/test_writer_inventory.py`: pin the new rpc params (rpc_calls[0]) for agent_signup.
- CLI tests (`tests/test_cli_signup.py`): token persisted; 422 → warning + confirm before clear+re-mint; `recover` subcommand happy path (mock urlopen).
- SQL (PGlite harness): token insert atomic with provision (a FAILED provision → NO token row); unique token_hash; unique team_id; resolve/recover RPC semantics (cap + revoke-oldest-non-bootstrap; FOR-UPDATE zero-row → error); **grant assertions via `has_function_privilege`: anon/authenticated DENIED EXECUTE on all three RPCs, service_role granted, table REVOKE'd from anon/authenticated** (ALTER DEFAULT PRIVILEGES in the harness grants EXECUTE to anon/authenticated — validate.mjs:30 — so this must be asserted explicitly). Add the new migration to `supabase/tests/pglite/validate.mjs` files list.
- Recovery rate limiter unit tests (per-IP bucket + per-token cap; identical IP key across both surfaces).
- Concurrency E2E N stays BELOW the per-token attempt cap (10/h) so the burst does not trip the limiter mid-assertion.
- Registry legacy-node test: APIKey node without expires_at prop still verifies (NULL-as-never-expires).

### Acceptance Criteria (E2E)
1. `tortoise signup` twice, same machine/config: 2nd run reuses (no call, #1708) OR re-presents token → **1 team total, key rotated, 0 new teams**.
2. Parallel POSTs, same signup_token → **1 team, 1 graph**; responses resolve to the same team_id.
3. Config lost, token saved → `tortoise recover --token st_...` → new key, same team, memories intact (Supabase + registry lanes).
4. Invalid token → 422, body identical to not-found and malformed (no oracle).
5. `test_client_identity_ignored` (and all #741(a) tests) pass **without modification**.
6. Post-claim re-signup with token → recovery on the SAME (claimed) team — no second team.
7. `list_api_keys` shows `created_via`/`expires_at` in both lanes; registry `apikey_verify` rejects expired keys (parity).
8. Ephemeral CI (no persisted token): behavior unchanged; per-IP limiter (2/24h) is the control.

## Rejected Alternatives

### B — Client-chosen opaque device_id + format whitelist + recovery code (the #741(a) reversal)
Client keeps sending `{identity: device_id}` (CLI already does); server validates format (never `reg-`), dedupes on it, issues a recovery code at mint. **Rejected:** the ONLY approach that actually reverses #741(a) (the issue's red line), creates a pre-squat lockout primitive (attacker who obtains the pre-mint device_id — a 48-bit handle in request headers/logs — mints first → victim's mint dead-ends), stores the secret-equivalent at rest as the identity, needs a permanent format-whitelist maintenance burden against every future identity namespace, and its dedupe anchor is NULLed by claim (20260813000004) → post-claim second-team mint. **When this WOULD have been better:** if the team explicitly approves the #741(a) reversal and prioritizes the smallest client diff + server-side dedupe of the fresh-device concurrent race (C's lost-response window) over posture — and accepts the recovery-code-as-sole-safeguard tradeoff.

### A — HMAC-derived identity from a client device_secret + recovery code
Client persists a high-entropy `device_secret`; server derives `dev-<HMAC-SHA256(PEPPER, secret)[:24]>`; secret never at rest server-side; recovery = secret + server-issued code. **Rejected:** statistically equal to C on oracle/TOCTOU and better than B on hygiene, but (a) the secret pre-exists the mint → pre-squat lockout in substance (attacker with the secret derives the identity and squats — the HMAC does not help), (b) the identity anchor is NULLed by claim → same post-claim gap as B (needs a dual-store anchor, converging on C's table), (c) response contract churn (dev- prefix breaks the locked `startswith("anon-")` assertions), (d) pepper-rotation blast radius on the dedupe guarantee, (e) registry reconciliation pass over the incident-history duplicate identities, (f) two conceptual artifacts (device identity vs code) for the user. **When this WOULD have been better:** if the control plane must attribute teams to devices via a stable server-derived identity (audit/analytics/dashboard correlations), or if reviewers reject "no identity at all" as a doctrine — A is the conservative fallback that keeps "identity is server-derived" in spirit.

### Support-runbook-only recovery (no credential issued at mint)
Dedupe by token but recovery = contact support (audit trail → manual rotate). **Rejected:** the O/I/T explicitly demands "config lost → user recovers without support escalation"; the runbook remains the last-resort floor, not the design.

### KMS-envelope recoverable keys (from #1708's rejected list)
Store keys encrypted so a dedupe-hit can return the ORIGINAL key. **Rejected in #1708 for the correct reason (security):** turns the dedupe-hit into a key-retrieval oracle — anyone with the identity retrieves a live key, violating #1082's key-possession gate. Not revived here.

### Two-step reserve-then-commit (`POST /signup/begin` → token, then `POST /signup/complete`)
**Shrinks but does not close** C's lost-response window under a server-issued-token model (the begin token is server-issued — NOT a #741(a) reversal; the client persists it before the team mint; a retry completes the same mint IF the begin response arrived). **Rejected:** new endpoint + orphan-token GC + two-step client contract for a narrow window already bounded by #1708 client reuse + the 2/24h IP limiter; the orphaned team is empty/harmless. **When this WOULD have been better:** if the fresh-device concurrent-first-mint race must dedupe server-side (C cannot — no anchor exists pre-mint).

### Explicitly OUT (filed separately)
- **`create_onboarding_team` orphan mint (hosted_api.py:7713-7760)** — mints a key whose plaintext is never returned + a fresh `anon-{uuid12}` per call. Auth-gated (session team), low harm (dead credential), and fixing it requires either returning the key (one-line response change + test) or relaxing the RPC's NOT NULL key contract (blast radius into register_user/teams/claim tests). **Out of #1709 scope; filed as follow-up issue.** Rationale: "file extra issues, don't silently absorb" — it is a distinct endpoint with a distinct fix, and #1708 already flagged it as noted-not-blocking. Dedupe-by-session-user for onboarding is also OUT (session users have user_id; the dedupe story is the claim/session path, not signup).
- **Dedupe-by-session-user for onboarding** — OUT (auth-gated; different identity model).

## Wiring Check

| Touch Point | Type | Covered By | Status |
|---|---|---|---|
| POST /v1/agent/signup (mint + token issuance + token-present recovery) | API | Plan §2 | ✅ in scope |
| POST /v1/agent/recover (new) | API | Plan §3 | ✅ in scope |
| `agent_signup_tokens` table + unique indexes | Data | Plan §1 migration | ✅ in scope |
| `provision_team` RPC `p_signup_token_hash` | Data | Plan §1 | ✅ in scope |
| registry lane (SignupToken node + APIKey created_via/expires_at + apikey_verify expires_at) | Data | Plan §4 | ✅ in scope |
| CLI (`__main__.py` + #1708 credentials.json): token persist, 422 handling, `recover` subcommand | Client | Plan §5 | ✅ in scope |
| per-IP signup limiter (#1081) | Auth | bounds MINTING only; token-present path bypasses (Plan §2) | ✅ |
| recovery rate limiter (per-IP + per-token) | Auth | Plan §3 | ✅ in scope |
| `resolve_signup_token` + `recover_team_key` + `provision_team_with_token` RPCs | Data | Plan §1 (new migration; grants service_role-only; public.-qualified) | ✅ in scope |
| shared recovery limiter (`_check_recovery_rate_limit`) | Auth | Plans §2 + §3 (both surfaces) | ✅ in scope |
| `supabase_control.py` helpers (resolve/recover wrappers) | Data | Plan §3 | ✅ in scope |
| sdk.py registry methods (signup_token_lookup/recover + rollback) | Data | Plan §4 | ✅ in scope |
| token revocation (support runbook, audited) | Ops | Plan §3 | ✅ documented; user-facing revoke = follow-up |
| abuse/notify (recovery-velocity feed) | Cross-cutting | Plan §3 | ✅ in scope |
| #741(a) locked tests | Tests | unchanged (proof of no reversal) | ✅ |
| FakeControlPlane rpc() | Tests | Plan §6 | ✅ in scope |
| Concurrency E2E (new) | Tests | Plan §6 | ✅ in scope |
| SQL/PGlite suite + validate.mjs files list | Tests/CI | Plan §6 (also fixes stale list) | ✅ in scope |
| `create_onboarding_team` orphan mint | API | **OUT** — follow-up issue filed (return-minted-key fix) | ⚠️ follow-up |
| user-facing token revoke (post-claim dashboard/CLI) | API/UI | **OUT** — follow-up issue | ⚠️ follow-up |
| `list_api_keys` created_via/expires_at (dashboard heuristic) | API/UI | #1708 already covers; verify in E2E-7 | ✅ #1708 |
| team_create phantom-key (sdk.py:10792) | API | #1710 / PR #1712 (already in flight) | ✅ #1710 |

## Complexity (domain-aware)

| Domain | Rating | Rationale |
|---|---|---|
| Architecture | complex | New table + RPC param + new endpoint + registry parity + CLI changes + recovery/concurrency semantics |
| Ontology | medium | One new table (agent_signup_tokens); no changes to existing identity/membership semantics |
| UX | low | CLI output + recover subcommand; no UI components (prototype gate skipped) |
| Config | low | Recovery rate-limit env knobs (mirror TORTOISE_SIGNUP_* pattern) |

Overall tier: **complex** (matches issue label).

## Verification Gates

### problem-verify
VALIDATED by #1708's gates (2 fresh-context verifiers) — not re-run per the task brief.

### solution-verify
- **Cycle 1:** 2 fresh-context verifiers dispatched. **P0 = 0 both.** Distinct P1s: 6 (rate-limit contradiction on token-present path; token revocation lifecycle; undefined recovery data-access layer; provision_team param placement vs append-only gate; #1708 zero-code sequencing; O/I-T re-anchoring needs ratification). P2s: 5 (concurrency key-count math; suspended/deleted-team branch; recovery created_by; apikey_verify NULL semantics; #1708 CLI coupling). P3s/P4s: 7 (two-step misclassification; O/I-T key-material reconciliation sentence; CLI 422 silent orphan; registry rollback missing SignupToken; legacy-node display; concurrency-test parallel mechanism; token_hash spelling + Invitation precedent; response-shape asymmetry).
- **Controller action (Cycle 1):** ALL 6 P1s FIXED (limiter bypass on token-present path + lock test; revocation runbook + follow-up issue; resolve_signup_token/recover_team_key RPCs; CREATE OR REPLACE in new migration; sequencing contract + independent token-persistence contract; explicit O/I-T deviation block). ALL P2/P3/P4 incorporated. Re-dispatching both verifiers (Cycle 2).
- **Cycle 2:** Both verifiers RE-DISPATCH verdict — P0=0, P1=3 (deduped): (1) CREATE OR REPLACE trailing-param mechanism is empirically false on PG16 — creates an OVERLOAD (old-arity calls ambiguous; new overload inherits Supabase default ACL = anon-executable mint primitive); (2) concurrency cap math wrong (free max_api_keys=2, not 3) + "same transaction" ≠ serialized (no lock specified); (3) token-present signup branch has no compensating recovery limiter/feed. P2s: RPC grant hygiene (anon-executable resolve_signup_token = oracle), revocation runbook strands owner (attacker keys never cleaned), suspended-team 403-vs-422, 402-clause contradiction. P4s: param count (15 not 16), phantom check-migration-order.cjs reference, registry-lane revocation line, ratification tracking, created_by caller-supplied.
- **Controller action (Cycle 2):** ALL 3 P1s FIXED: (1) new-named `provision_team_with_token` wrapper replaces CREATE OR REPLACE (zero overload, zero DROP risk, atomic same-tx; REVOKE/GRANT service_role-only + SET search_path for ALL THREE new functions); (2) `recover_team_key` gains `SELECT ... FOR UPDATE` on the token row (serializes concurrent recoveries) + corrected math (≤ max_api_keys=2; over-cap revokes-oldest-non-bootstrap; 402 only bootstrap-only edge); (3) shared `_check_recovery_rate_limit` (per-IP + per-token + velocity feed) on BOTH the token-present signup branch and /v1/agent/recover, with a lock test for both. ALL P2/P3/P4 incorporated (grant assertions in PGlite; runbook adds attacker-key revoke + re-credentialing; suspended team → distinct 403 with CLI fail-closed; param count fixed; gate names corrected; created_by derived inside RPC; registry-lane revocation line; ratification surfaced in Phase 8 comment). Re-dispatching both verifiers (Cycle 3).
- **Cycle 3:** Both verifiers **PASS — P0 = 0, P1 = 0**. All three Cycle-2 fixes verified real and code-grounded (wrapper no-overload; FOR-UPDATE lock ordering + cap math; shared limiter). Residual P2s (stale `provision_team(p_signup_token_hash)` call-site in §2/wiring/Integration Docs; recover_team_key zero-row FOR UPDATE abort unspecified; unqualified table refs under SET search_path=''; positional PERFORM) + P3s (unreachable 402 clause; PGlite grant assertions unspecified; runbook re-credentialing mechanism undefined — pepper in app code not SQL; token-attributable created_by; NULL token_hash guard; limiter IP-key pinning; wrapper atomicity contract) + P4s (recovery-limiter accounting/error_code/per-token-key; E2E N vs per-token cap) — **ALL incorporated** into the doc (see Plan §1-§3, §6). No re-dispatch (P2+ only per the gate).
- **Phase 5.6 second-model coherence check:** NO P0/P1 — diamonds cohere. Incorporated P2s (lost-response "escape hatch" wording corrected — recover needs the token the lost-response case doesn't have; `tortoise recover` must persist the token into credentials or recovery is one-shot-only) + P3s (token-present branch framed as legacy-client orphan-prevention safety net; ratification upgraded to a BLOCKING pre-implementation go/no-go; weakest-assumption risk surfaced — headless/ephemeral majority can't save tokens) + P4s (problem statement relabeled to "per signup_token (post first mint)"; 402-clause simplified). Research cross-check: NO ISSUES FOUND (all dependency claims verified against code).
- **Phase 7 (parallel review gates):** consolidated into the solution-verify cycles (2 fresh-context verifiers × 3 cycles, each instructed to act as devil's advocate on the chosen approach) + the second-model coherence check (standalone epic — epic-alignment agent N/A). Documented as a deliberate consolidation; no separate 4-agent dispatch.
- **Cycle 3:** …

## Review Cycle Log

### solution-verify — Cycle 1 (2026-08-14)
- Verifier A: P0=0, P1=6, P2=2, P3=3, P4=2. Verifier B: P0=0, P1=3 (P1-1=P1-X, P1-2=P1-Y dup of A, P1-3=P1-Z dup), P2=5, P3=4, P4=1.
- Controller merge: 6 distinct P1s (deduped across verifiers) — all fixed in the doc (see Verification Gates). P2+ incorporated. Both verifiers confirmed divergence genuine, convergence quality-driven, zero new deps, no better approach rejected for convenience.
- **Re-dispatch (Cycle 2) in progress.**

## External Research (Phase 1.5 artifact)

### Axis Research
- **Architecture (high) — idempotency patterns:** Stripe Idempotency-Key = client-chosen key, server stores key→stored-response, retry replays the SAME response (canonical; bytebytego, algomaster, dzone). Pitfalls confirmed: reusing a user ID as an idempotency key, short TTLs, non-atomic reservation. Postgres insert-or-fetch: `ON CONFLICT DO UPDATE + RETURNING` is the atomic form; plain `DO NOTHING + RETURNING` returns nothing for the conflict row (postgres docs; dba.stackexchange concurrency caveat) — our design avoids the pre-check-then-mint entirely (token-path never creates a team). [canonical + pitfalls]
- **Ontology (medium) — one-anchor-per-resource:** in-repo precedent `tenant-provision/index.ts:335-352` (deterministic team_id = SHA-256(user_id)[:26] + upsert = idempotent re-invocation) and `uq_teams_name`/`uq_teams_email`/`uq_member_owner` partial unique indexes with pre-check-as-fast-path + constraint-as-authoritative (POST /v1/teams at hosted_api.py:5170). [precedent]
- **UX (low) — keyless recovery:** industry mitigants for anonymous recovery: backup/recovery codes + registered multiple auth methods (Twilio MFA recovery), device-bound tokens + trusted-device management (Keyless), FIDO2 recovery evaluation (arXiv 2105.12477); honest finding: no-multifactor anon recovery is impossible without a second credential or runbook — our single saved token + support runbook floor matches the standard posture. [competitor-precedent + pitfalls]

### Integration Docs
- **New deps: NONE.** `tortoise/auth.py` already imports `hmac`, `secrets`, `hashlib`; token hash reuses the `lookup_hash` construction (SHA-256(PEPPER + token)) — no new package, no TS mirror (server-side-only derivation; `supabase/functions/_shared/lookup.ts` + `lookup_parity.test.mjs` untouched).
- **Supabase:** NEW `provision_team_with_token` wrapper (15 named args + p_signup_token_hash) in the new migration — 0010 untouched; `api_keys.created_via` CHECK enum (0007) already admits `'recovery'`; new table + all three RPCs granted service_role-only with `public.`-qualified refs + `SET search_path=''` (mirror 0010:185-186).
- **Migration plumbing:** timestamp-style filename (`check-migration-append-only` prefix/diff gate in ci.yml + `check-migration-drift` #1095 deploy gate — the strict-increasing/prefix-uniqueness enforcement; no `check-migration-order.cjs` exists); must add the file to `supabase/tests/pglite/validate.mjs` files list (currently stale — missing 20260813000006).
