# Scope — #1708: Signup/key-minting non-idempotent

> issue-scoping v5.1 double diamond — Standard tier. Root cause confirmed with evidence (14 `tt_` keys minted 13–25 Aug from dev/install activity).
> **REVISED after verification gates** — controller merge of 2 fresh-context verifiers. See `## Verification Gates`.

## Confirmed Problem

**Client-side provisioning is non-idempotent, and key visibility is poor:**
1. **CLI never reuses** — `tortoise/__main__.py:632` `_cmd_signup` generates `device_id = f"anon-{uuid.uuid4().hex[:12]}"` **fresh every run** and POSTs `/v1/agent/signup` unconditionally — no check for an existing key. Config saved to `Path.cwd() / ".tortoise"` (per-directory, so keys scatter and are never found again). Running from `~` crashes the save (`IsADirectoryError` — `~/.tortoise` is the data home) **after** minting → orphaned key + retry mints another.
2. **No visibility** — `list_api_keys` (`hosted_api.py:3700`) returns no `created_via`/`expires_at`, so the dashboard can't distinguish durable keys from ephemeral session keys (`created_via='bootstrap'`, 24h expiry) except a fragile frontend heuristic.
3. **Server stays permissive by design** — `#741(a)` (documented at `hosted_api.py:6871`, locked by `tests/test_agent_signup.py`): identity is ALWAYS server-side, client identity/X-Device-Id ignored, per-IP limiter 2/24h is the only backstop. **This is a deliberate security invariant — not a bug.** Reversing it (server-side dedupe by client identity) requires a reviewed identity model and is scoped OUT of this issue → follow-up #1709.

Result: every CLI signup run/retry creates a permanent new key+team; 14 keys accumulated in 12 days from dev+install testing alone.

## Scope (this issue — client-side idempotency + visibility)

### A. CLI signup: reuse + stable identity + crash fix (`__main__.py`)
1. **Stable persisted `device_id`** — generate once on first mint, store in the credentials file, reuse thereafter (server keeps ignoring it — no security change; it anchors client-side reuse only).
2. **Global credentials file** `~/.tortoise/credentials.json` (0600; `~/.tortoise` mkdir 0700). Write there instead of CWD. Keep reading `./.tortoise` (legacy) + `TORTOISE_API_KEY` env for compat.
3. **Reuse-before-mint** — if a config/key exists (env → global → cwd), validate it (lightweight authed call; 401/403 → treat as invalid) and exit 0 with a reuse message; **0 new keys**.
4. **`--force`** escape hatch to mint fresh (env-var footgun escape).
5. **Fix `~` write crash** (`IsADirectoryError` on `~/.tortoise` dir) and `./.tortoise`-is-a-dir on the **read** path (guard both).
6. **Shared resolver** across the CLI call sites that read `cwd/.tortoise` (768, 1178, 1232, 1327) with precedence env → global → cwd (cwd wins for legacy projects) — enumerate at plan time.

### B. Key visibility (`hosted_api.py` + dashboard)
7. `list_api_keys` (Supabase + registry lanes) returns `created_via` + `expires_at`; registry lane sets these props at mint time (parity).
8. `website/apps/dashboard/src/main.jsx` — replace `isSessionKey` prefix heuristic with `created_via`/`expires_at` (null → durable).

### C. Incident remediation (ops)
9. Revoke the 14 incident keys (per `api_keys.revoked_at` / dashboard). Offered to the user; manual ops action.

### Explicitly OUT of scope (follow-ups filed)
- **#1709** — server-side identity dedupe + keyless recovery model (reverses #741(a); needs: oracle-free `existing` response [no team_id/name echo], one-team-per-identity unique constraint + insert-or-fetch (TOCTOU), concurrency E2E, identity-format validation vs `reg-{sha256(email)[:12]}` collision, recovery channel for config-loss).
- **#1710** — `team_create` `idempotency_key` phantom-key bug (`sdk.py:10792` returns a freshly-minted key that was never persisted on the `existing:true` path — dead key handed to callers).

## Acceptance Criteria (E2E)
1. `tortoise signup` twice, same env → second run reuses, exits 0, **0 new APIKey rows**.
2. `tortoise signup` from `~` → no crash; config lands in `~/.tortoise/credentials.json` (0600).
3. `tortoise signup` with revoked stored key → validates, detects 401, re-mints (or `--force`).
4. Signup from a different CWD → finds global config, reuses.
5. `list_api_keys` returns `created_via` + `expires_at` in both lanes; registry mint writes the props.
6. Dashboard renders session keys from `created_via` (not heuristic) — unit/component check.
7. Existing suites green: `test_agent_signup`, `test_writer_inventory`, `test_dashboard_login`, `test_session_login`, `test_hosted_api` + new CLI tests. **`test_agent_signup` stays unchanged** (client identity still ignored server-side).

## Wiring Check
| Touch Point | Type | Covered By | Status |
|---|---|---|---|
| `tortoise signup` CLI (`_cmd_signup`) | Client | reuse + stable identity + global config + `--force` | ✅ in scope |
| CLI config call sites (768/1178/1232/1327) | Client | shared resolver | ✅ in scope |
| `~/.tortoise/credentials.json` | Data | new file (0600, dir 0700) — no DB schema change | ✅ in scope |
| GET /v1/team/keys (`list_api_keys`) | API | created_via/expires_at both lanes | ✅ in scope |
| registry APIKey mint (agent_signup registry path) | Data | add created_via/expires_at props | ✅ in scope |
| `website/apps/dashboard/src/main.jsx` | UI | created_via-based rendering | ✅ in scope |
| POST /v1/agent/signup (server dedupe) | API | **deferred to #1709** (reverses #741(a)) | ⚠️ follow-up |
| `team_create` idempotency_key | API | **deferred to #1710** (phantom key bug) | ⚠️ follow-up |
| per-IP signup limiter (#1081) | Auth | unchanged | ✅ |

## Complexity (domain-aware)
| Domain | Rating | Rationale |
|---|---|---|
| Architecture | standard | Multi-file (CLI resolver, hosted_api list, registry mint, frontend); no security invariant reversal |
| Ontology | low | No DB schema change (additive API fields + file) |
| UX | low | CLI messaging + dashboard annotation |
| Config | low | New credentials path; perms handling |

Overall tier: **standard**.

## Verification Gates
### problem-verify — 1 cycle, 0 issues remain
Verifier confirmed problem diamond solid (framing B root cause; A/C/D evaluated; evidence verified). 
### solution-verify — 1 cycle → RE-SCOPED after verifier findings (P0/P1 → controller fix → re-dispatch not needed: re-scope is a structural change, re-verified by second verifier's independent verdict)
- P0 (verifier): server dedupe unimplementable as described — `#741(a)` makes identity server-side; `test_agent_signup.py` asserts the opposite. → **Deferred to #1709.**
- P1 (verifier): onboarding-team phantom mint (`create_onboarding_team`) — unrecoverable orphan key. → Flagged; partially covered by #1709 scope; noted.
- P1 (verifier): revoked-key dead end + oracle/lockout/TOCTOU in server dedupe. → Deferred to #1709 with the analysis.
- P2/P3: rate-limiter ordering, registry NULL parity, dashboard heuristic replacement, `~/.tortoise` perms, `./.tortoise`-dir read crash → **incorporated** (AC 1–7, wiring).
### Devil's-advocate (Phase 7) — verdict: split the issue
"Client half + list_api_keys metadata captures ~90% of the value with zero server security change. Ship that; treat server-side identity dedupe as a separate issue gated on a reviewed identity model." → Adopted as the re-scope.

## Rejected Alternatives
- **A1 (client-only) as originally rejected** — re-adopted AFTER verification: the server half (A2) requires reversing a deliberate security invariant (#741(a)) and introduces an unauthenticated existence oracle + lockout primitive + TOCTOU race, while not fixing the stated multi-machine harm (device-scoped ≠ user-scoped) nor ephemeral installs (CI/containers can't persist identity). Documented in #1709.
- **Server stores recoverable key material (KMS envelope), returns same key on dedupe-hit** — feasible but REJECTED on security: turns the dedupe-hit path into a key-retrieval oracle (anyone with the identity retrieves a live key; exactly what #1082's key-possession gate blocks). The original scope's rejection ("impossible — hash-only") was wrong reason; correct reason is security.
- **Rate-limiter tightening instead** — complementary backstop, not a fix; unchanged.
- **Stale-key GC** — product decision (retention policy); separate issue, not filed yet.
