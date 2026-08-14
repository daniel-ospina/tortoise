<!-- research-path: (none — epic research docs 04-plan.md/05-test-design.md lost in migration; design reconstructed from repo state, see Reconstruction) -->

# Hosted E2E Test Suite (#303) — Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Reconstruct and implement the lost 12-case hosted-platform E2E design as an automated pytest-playwright suite at `tests/e2e/hosted/` that boots the REAL deployed server artifact (`uvicorn tortoise.hosted_api:app`) hermetically, runs the full customer journey chain in <5 min with zero secrets, and ships as a `hosted-e2e` CI job.

**Architecture:** A session-scoped fixture boots the hosted API as a **subprocess** (`sys.executable -m uvicorn tortoise.hosted_api:app`) on a free port with embedded FalkorDBLite (`TORTOISE_DB_PATH`), registry control plane, and a test env contract. Twelve per-case modules (E2E-1-D..E2E-12-D) drive the journey over real HTTP using pytest-playwright's `APIRequestContext` (`playwright.request`), with a session-JWT fixture (local JWKS mock + minted RS256 JWTs) for session-auth endpoints. Gated `RUN_HOSTED_E2E=1` (module-level skip otherwise, `RUN_LEGAL_E2E` precedent) with optional `E2E_BASE_URL` remote mode + `ALLOW_PROD=1` https guard.

## Reconstruction (lost design docs — stated per issue #303)

`docs/epics/2026-08-03-tortoise-hosted-platform/04-plan.md` and `05-test-design.md` were **lost during the eldato→tortoise migration** (only `plans/7714-data-model.md` survived). The 12-case design below is reconstructed from: the #303 issue body + capstone #291 journey; surviving `-D` markers in code (`E2E-3-D` tests/e2e/test_billing_upgrade.py, `E2E-6-D` tests/test_export_delete.py + tortoise/hosted_api.py:3379, `E2E-7-D` tests/test_hosted_auth.py:271, `E2E-4-D/8-D` docs/epics/2026-08-03-tortoise-hosted-platform/plans/7714-data-model.md:45-47); and the surviving user-journeys E2E designs (`docs/epics/2026-08-07-tortoise-user-journeys/05-plan.md` sub-step 7). E2E-5-D (absent everywhere in the repo) is reconstructed from its journey position between billing (E2E-3-D) and export/delete (E2E-6-D): backup → restore.

**Problem definition (issue-scoping, double-diamond verified, 0 P0/P1):** the platform has 380+ unit/integration tests but nothing that (a) boots the FULL `hosted_api:app` artifact on real sockets with its complete middleware stack (only `test_bridge_mcp.py` socket-boots the MCP sub-app alone), (b) runs the journey as one stateful chain, (c) runs hermetic in CI. Live-prod browser clickthrough was REJECTED as infeasible (no staging env; GitHub OAuth/CAPTCHA not automatable; shared 30/hr email bucket #801; register 3/hr/IP; welcome suite documents that minting prod tenants is forbidden — no cleanup endpoint). Confidence 80.

**Solution selection (quality over convenience):** 12 per-case modules + ONE session-scoped uvicorn subprocess + tenant factory. Rejected: in-process uvicorn-on-a-thread (fails the deployed-artifact requirement — no exec boundary); phase-grouped modules (weaker 1:1 case audit trail for capstone #291 consumption); skip-guard-only backup positive leg (guts E2E-5-D).

**Capstone #291 note:** #291's indicator "12 E2E click-through scenarios pass on production" predates this reconstruction; this suite runs hermetically (with an optional remote leg). #291 is dispatched separately after this merges — its acceptance language should be re-scoped to match (flagged in PR; NOT closed here).

## Pattern Research

Skipped — zero new third-party deps. Everything used is in-repo or already in the `[test]` extra (pytest-playwright >=0.5, pyproject.toml:41). Playwright `APIRequestContext` (no browser binaries needed), in-repo `MemoryStorage` (hosted_backup.py:414), in-repo `_sign()` HMAC precedent (test_billing.py), `RATE_LIMIT_DISABLED` escape-hatch precedent (hosted_api.py:408-411), `TORTOISE_PRICING_PATH` override hook (pricing.py:18-19), `RUN_LEGAL_E2E` gating precedent (test_signup_form_safety_e2e.py).

## Integration Surface Map

| Surface | Endpoint(s) | Test layer | Cases | Bug patterns guarded |
|---|---|---|---|---|
| Tenant provisioning | `POST /v1/register`, `POST /internal/provision` | HTTP black-box (real server) | 1-D, 6-D | 409 dup, 422 validation (register limiter itself is disabled by RATE_LIMIT_DISABLED — covered by unit tests) |
| Points CRUD | `/v1/points` | HTTP | 1-D, 4-D, 12-D | cross-tenant leak, kind validation |
| Quota | points/api-keys limits | HTTP | 2-D | 402 fail-closed, cap math vs pricing.json |
| Billing | `/v1/billing/checkout`, `/webhooks/stripe` | HTTP + signed payloads | 3-D, 5-D, 8-D | sig verify (tampered → 400), idempotent apply, checkout unconfigured → 503, webhook unconfigured → 500 |
| Tenant isolation | points/keys/sessions across 2 tenants | HTTP | 4-D | foreign-key 401, empty foreign reads |
| Backup/restore | `/backups`, `/backups/restore` | HTTP (memory storage seam) | 5-D | 402 free gate, confirm guard, integrity |
| Export/delete | `/v1/teams/{id}/export`, `DELETE /v1/teams/{id}` | HTTP (session JWT) | 6-D | 403 non-owner, 401 no-auth, 202 grace |
| Security baseline | auth matrix, `/health/security`, headers | HTTP | 7-D | 401 matrix, HSTS, pepper/hash posture |
| Multi-team | `/v1/teams`, `/v1/invites`, members | HTTP (session JWT) | 8-D | RBAC 403, 409 dup invite |
| GitHub | `/v1/onboarding/github/*`, `/v1/index/github` | HTTP | 9-D | 503 unconfigured, 404 bad state |
| Sessions | `/v1/sessions` | HTTP | 10-D | turn cap 400, quota 402 |
| MCP | `/mcp` JSON-RPC over HTTP/SSE | HTTP | 11-D | 401 unauth, tool scoping |
| Selfhost migration | selfhost daemon + hosted register | HTTP (2 servers) | 12-D | key non-portability, parity |

## Journey Test Map

### Journey: New customer adopts Tortoise Hosted end-to-end (capstone #291)

1. **Step:** Register (signup equivalent) → **Acceptance:** team + tt_ API key + graph provisioned → **Test:** E2E-1-D
2. **Step:** Create first Point via API → **Acceptance:** point stored & retrievable → **Test:** E2E-1-D
3. **Step:** Hit free-tier caps → **Acceptance:** 402 with upgrade message, fail-closed → **Test:** E2E-2-D
4. **Step:** Upgrade to Pro (checkout → webhook) → **Acceptance:** tier=pro, limits applied → **Test:** E2E-3-D
5. **Step:** Enable backups, restore after mutation → **Acceptance:** graph restored byte-faithful → **Test:** E2E-5-D
6. **Step:** Export data / delete team → **Acceptance:** owner-only export; soft delete → **Test:** E2E-6-D
7. **Step:** Attacker probes auth boundaries → **Acceptance:** 401/403 everywhere, HSTS on → **Test:** E2E-7-D
8. **Step:** Second tenant + invites → **Acceptance:** isolation + RBAC hold → **Test:** E2E-4-D, E2E-8-D
9. **Step:** Connect GitHub → **Acceptance:** auth_url + state; gated cleanly without creds → **Test:** E2E-9-D
10. **Step:** Agent session captured → **Acceptance:** turns extracted as Points (LLM mock mode) → **Test:** E2E-10-D
11. **Step:** MCP client connects → **Acceptance:** initialize→tools/list→tools/call writes a Point → **Test:** E2E-11-D
12. **Step:** Self-hoster migrates to cloud → **Acceptance:** hosted parity of selfhost graph → **Test:** E2E-12-D

### Failure Modes

- Server boots with missing env → **Expected:** readiness poll fails fast with captured stderr → **Test:** conftest fixture
- Port collision → **Expected:** free-port probe, never fixed port → **Test:** conftest fixture
- Hosted env not configured (no RUN_HOSTED_E2E) → **Expected:** module-level skip, clear message → **Test:** all modules

**Tech Stack:** Python 3.12, pytest, pytest-playwright (APIRequestContext), uvicorn subprocess, embedded FalkorDBLite, `cryptography` (RS256 mint, transitive dep — verified in uv.lock).

## The 12 cases (reconstructed design — every case has >=2 negatives)

| Case | Positive core | Negative cases (>=2 each) |
|---|---|---|
| E2E-1-D signup→provision→key→Point | register → key authenticates `/v1/team` → create+get Point | dup email 409; short password 422; malformed email 422 |
| E2E-2-D free tier limits | free caps visible in `/v1/team` (`tier=free`, `max_graphs=1`, `write_ops_limit` per pricing.json); api-key cap enforced behaviorally | exceed `max_api_keys` (3rd key on free cap 2) → 402; exceed `max_points` on a dedicated `e2e_small`-tier tenant (fixture tier, `max_graph_nodes=8`, reached via webhook tier bump) → 402 fail-closed |
| E2E-3-D Pro upgrade/billing | hermetic tier bump: signed `checkout.session.completed` (client_reference_id + `customer`, no `subscription` → zero Stripe network) + signed `customer.subscription.updated` (price in local STRIPE_PRICE_IDS catalog) → `/v1/team` tier=pro + pro limits | tampered signature → 400; checkout unconfigured (bare server) → 503; checkout unknown price_id → 400; webhook unknown price → 200 + tier preserved (review-fix-7 semantics) |
| E2E-4-D tenant isolation | tenant A points invisible to tenant B | B's key on A's point id → not found/empty; list shows only own; revoked key → 401 |
| E2E-5-D backup→restore | Pro tenant (webhook bump; pricing fixture `daily_backups:true`): POST /backups 201 → GET /backups lists → mutate graph → restore(confirm=true) → original content back | free tenant → 402; restore without confirm → 400; restore unknown backup_key → 400 |
| E2E-6-D export+delete | tenant provisioned via `/internal/provision` (Team+APIKey+owner Membership, `created_by` = JWT sub — register creates NO Membership, so export's `_require_owner` would 403 otherwise); points written with the tt_ key; session-JWT owner export returns schema_version payload incl. points; DELETE team → 202 + grace semantics (subsequent reads 410/degraded) | export without JWT → 401; API-key (non-session) export → 401; foreign-owner JWT → 403 |
| E2E-7-D security baseline | `/health/security` posture ok; HSTS header on responses; valid key → 200 | auth matrix: missing header/empty bearer/wrong prefix/invalid key → 401 (4 legs); `/internal/provision` without internal key → 401 |
| E2E-8-D multi-team membership | session user creates two teams via POST /v1/teams (owner membership each); GET /v1/teams lists both; team bumped to `team` tier via signed webhook (STRIPE_PRICE_IDS has a team price) → invite → accept (JWT) → member listed; `/v1/session/key` mints a tt_ key for a session-created team | non-owner member cannot list/remove members (403); foreign-team key revocation → 403 ("Not your API key"); duplicate invite → 409; bad invite token → 400 |
| E2E-9-D GitHub integration | connect (fake GITHUB_CLIENT_ID set) → auth_url + state; status → not-connected cleanly | connect on bare server (no GITHUB_CLIENT_ID) → 503; callback bad state → 404; index/github without connection → 400 |
| E2E-10-D session capture | POST /v1/sessions (LLM mock mode) → session stored; GET /v1/sessions(+/{id}) shows turns→Points extraction | turn cap exceeded → 400; oversized turn content → 422; unauthenticated → 401 |
| E2E-11-D MCP connect | initialize → tools/list (tortoise_* visible) → tools/call create_point → point readable via REST | no token → 401; non-tt_ token → 401; unknown tool → JSON-RPC error |
| E2E-12-D selfhost migration | selfhost daemon (2nd subprocess, own DB, static key) serves points → register hosted tenant → replay points → hosted query parity | selfhost static key wrong → 401; hosted key rejected by selfhost → 401; dup register 409 |

**Remote mode (`E2E_BASE_URL` set):** no server boot; tenants registered via API with timestamped emails; cases whose hermetic seams don't apply skip per-test with clear reasons: E2E-5-D (memory storage local-only), E2E-12-D (selfhost server local-only), E2E-3-D webhook leg only with a locally-controlled webhook secret. Register rate limit (3/hr/IP server-side) limits remote runs to shared-session tenants — documented; local CI mode is the primary target.

## Env contract (hosted server subprocess)

`TORTOISE_DB_PATH=<abs session tmp>/hosted.db` (absolute — bare filename silently falls back to shared tempdir, hosted_api.py:82-88), `TORTOISE_CONTROL_PLANE=registry` (defense-in-depth pin — supabase mode needs SUPABASE_URL AND a service key (supabase_control.py:96-98), so the JWKS-mock URL alone cannot flip mode; the pin guards against a service-key env leak silently flipping it), `SUPABASE_URL=http://127.0.0.1:<jwks_port>` (session_auth.py:27-31 reads at import: `{SUPABASE_URL}/auth/v1/.well-known/jwks.json`), `TORTOISE_SECRET_PEPPER`, `FASTAPI_INTERNAL_KEY`, `RATE_LIMIT_DISABLED=1`, `TORTOISE_SESSION_LLM_MOCK=1` (#822 — extraction is LLM-default: offline MockModel, the regex mode knob is removed; mirrors conftest.py:25), `TORTOISE_BACKUP_KEY=<base64 32B>`, `TORTOISE_BACKUP_STORAGE=memory` (NEW seam), `BACKUP_WATCHER_DISABLED=1`, `TORTOISE_PRICING_PATH=<fixture pricing — product/pricing.json copy with pro/team features.daily_backups=true>` (pricing.py:18-19 hook; shipped pricing.json has no tier with daily_backups=true so the public /backups gate is otherwise 402 for everyone), `STRIPE_WEBHOOK_SECRET=whsec_e2e_local`, `STRIPE_PRICE_IDS={"pro":{"monthly":"price_e2e_pro_monthly","annual":"price_e2e_pro_annual"},"team":{"monthly":"price_e2e_team_monthly","annual":"price_e2e_team_annual"},"e2e_small":{"monthly":"price_e2e_small_monthly","annual":"price_e2e_small_annual"}}` (local PriceCatalog — no network; PriceCatalog requires BOTH monthly+annual per tier, billing.py:77-90; `team` price needed for E2E-8-D invites; `e2e_small` for the E2E-2-D cap breach), `GITHUB_CLIENT_ID=e2e_client_id` (no secret → exchange stays gated).

**JWKS mock:** in-process `http.server` thread serving RSA JWKS at `/auth/v1/.well-known/jwks.json` (URL pinned at server import, first fetch lazy on the first session-auth request — the mock must be up before the first JWT request, not before server boot); conftest mints RS256 JWTs (`cryptography`; claims: `iss={SUPABASE_URL}/auth/v1`, `aud=authenticated`, `sub=<user_id>`, `exp=+1h`, `kid` matching; `_verify_rs256` = PKCS1v15+SHA256, exp leeway 30s). The pytest process NEVER opens the server's DB file (redislite single-writer hazard, tests/conftest.py:96-101) — all verification is HTTP.

**Subprocess env hygiene:** the child env is built explicitly — `{**os.environ, **CONTRACT}` then POP `TORTOISE_DB_URI` (`_make_sdk` prefers URI over TORTOISE_DB_PATH — a stale exported URI silently flips the server to network mode and hangs the readiness poll; test_bridge_mcp.py:14-15 documents the exact hazard) and `SUPABASE_SERVICE_KEY`/`SUPABASE_ANON_KEY` (belt-and-braces under the registry pin).

**Crash detection:** session-autouse fixture probes `/health/ready` before each case (sub-ms); first non-response fails fast with the server's stderr tail instead of ~30 raw connection-refused errors. Boot retries once on bind failure (free-port probe TOCTOU).

**Bare server (`bare_hosted_server`):** a second minimal-env uvicorn subprocess (no STRIPE_PRICE_IDS, no STRIPE_WEBHOOK_SECRET, no GITHUB_CLIENT_ID) for the unconfigured-negative legs: checkout → 503 (BillingConfigError), webhook → 500 "webhook not configured", github connect → 503. ~5s extra boot, within budget.

**Point budgets:** E2E-2-D e2e_small tenant cap = 8 nodes (TeamMeta + headroom); its breach loop creates <=10 points. All other tenants run under free caps (10,000) — E2E-12-D replay is sized at 3 points.

**Backup seam (~6 lines, hosted_api.py `_backup_storage()`):** `TORTOISE_BACKUP_STORAGE=memory` → module-singleton `MemoryStorage()`; unknown value → fail-closed RuntimeError; startup `_logger.warning` when active (silent-durability footgun mitigation); default unchanged `R2Storage()`. Precedent: `RATE_LIMIT_DISABLED`.

## CI job (`hosted-e2e` in .github/workflows/ci.yml, legal-e2e pattern)

ubuntu-latest; `timeout-minutes: 15`; `concurrency: {group: hosted-e2e, cancel-in-progress: true}`; checkout → setup-python 3.12 → `pip install -e '.[test]'` (NO `playwright install` — APIRequestContext needs no browser binaries; loud comment) → `RUN_HOSTED_E2E=1 python -m pytest tests/e2e/hosted/ -q -rs -p no:cacheprovider`. No secrets consumed. Warn step (welcome-e2e pattern) when the suite reports all-skipped. Does not re-collect other tests/e2e files (python-ci.yml/post-merge-validation.yml exclude tests/e2e — unchanged).

## Runtime budget

Boot ~4-8s + JWKS mock <1s + ~40 tests x sub-second HTTP + E2E-12 second server ~3s => **~60-120s total** (cap: 5 min; measured in Task 8).

---

## Tasks

### Task 1: Backup storage seam + pricing fixture

**Intent:** make the E2E-5-D backup→restore journey runnable hermetically through the real HTTP surface (subprocess boundary forbids monkeypatch; shipped pricing.json gates /backups 402 for all tiers).
**Acceptance:** `TORTOISE_BACKUP_STORAGE=memory` makes `/backups` use MemoryStorage; unknown value fails closed (RuntimeError, never silent R2); startup warning logged when the knob is active; `tests/e2e/hosted/fixtures/pricing-e2e.json` exists — canonical product/pricing.json PLUS pro/team `features.daily_backups: true` PLUS an `e2e_small` tier (all `_REQUIRED_LIMIT_KEYS` present; `max_graph_nodes: 8`) for the E2E-2-D cap breach; `cryptography>=42` added to the `[test]` extra (JWKS/RS256 mint — it is NOT in the current `.[test]` closure) and `uv.lock` regenerated (`uv lock`); full `python -m pytest tests/ -q` stays green (seam default = R2, zero behavior change when unset).
**Files:**
- Modify: `tortoise/hosted_api.py` (`_backup_storage()`, ~line 4885)
- Modify: `pyproject.toml` (`[test]` extra) + `uv.lock` (regen)
- Create: `tests/e2e/hosted/fixtures/pricing-e2e.json`
- Test: `tests/test_hosted_api.py` (small class asserting seam selection logic)

### Task 2: conftest — server fixture, JWKS mock, tenant factory, gating

**Intent:** the reusable substrate: real-server boot, session-JWT mint, disposable tenants, RUN_HOSTED_E2E gate with E2E_BASE_URL/ALLOW_PROD handling.
**Acceptance:** `hosted_server` (session) boots uvicorn subprocess on a free port, ready on `/health/ready` (60s cap, stderr tail on failure), torn down reliably; `bare_hosted_server` (session, lazy) boots the minimal-env variant for unconfigured negatives (checkout 503 / webhook 500 / github 503); `jwks_mock` serves the RSA JWKS before the first JWT request; `session_jwt(user_id)` mints valid tokens; `tenant_factory` registers tenants via `/v1/register` with unique emails (remote mode: 3 shared tenants max — server-side register limit is 3/hr/IP — cases reuse them); without `RUN_HOSTED_E2E` every module skips with a clear message; `E2E_BASE_URL` remote mode skips server boot; https without `ALLOW_PROD=1` skips.
**Files:**
- Create: `tests/e2e/hosted/conftest.py`

### Task 3: Cases E2E-1-D..E2E-3-D (journey start, quota, billing)

**Intent:** cover signup→provision→key→Point, free-tier fail-closed limits, and the hermetic Pro upgrade (signed webhooks, zero Stripe network).
**Acceptance:** `test_01_signup_provision.py`, `test_02_free_tier_limits.py`, `test_03_billing_upgrade.py` pass against the local server; each case >=2 negative tests; E2E-3-D asserts tier=pro + pro limits in `/v1/team` after two signed events, plus tampered-sig 400 / unconfigured-checkout 503 / unknown-price 400.
**Files:**
- Create: `tests/e2e/hosted/test_01_signup_provision.py`, `test_02_free_tier_limits.py`, `test_03_billing_upgrade.py`

### Task 4: Cases E2E-4-D..E2E-6-D (isolation, backup/restore, export/delete)

**Intent:** tenant isolation on real sockets; the full backup→restore journey through the public endpoints (memory seam + pricing fixture); owner-only export + team deletion via session JWTs.
**Acceptance:** `test_04_tenant_isolation.py`, `test_05_backup_restore.py`, `test_06_export_delete.py` pass; E2E-5-D restores mutated graph to original content and asserts integrity; E2E-6-D provisions its tenant via `/internal/provision` (created_by = the JWT sub — `/v1/register` creates NO Membership node, so `_require_owner` would 403 for register-created teams), writes points with the returned-provision key path or session-minted key, then uses minted JWTs; asserts 401 without session and 403 for foreign owner.
**Files:**
- Create: `tests/e2e/hosted/test_04_tenant_isolation.py`, `test_05_backup_restore.py`, `test_06_export_delete.py`

### Task 5: Cases E2E-7-D..E2E-9-D (security, multi-team, GitHub)

**Intent:** security baseline posture over the wire; multi-team membership + RBAC via session JWTs; GitHub connect surface (hermetic positives, skip-guarded exchange).
**Acceptance:** `test_07_security_baseline.py`, `test_08_multi_team.py`, `test_09_github_integration.py` pass; auth matrix 4 legs 401; HSTS present; `/health/security` ok; E2E-8-D bumps its team to tier=team via the signed-webhook helper BEFORE inviting (invites 402 below team tier, hosted_api.py:3055), invite accept flow works on registry plane with real JWTs; connect returns auth_url+state; callback bad state 404.
**Files:**
- Create: `tests/e2e/hosted/test_07_security_baseline.py`, `test_08_multi_team.py`, `test_09_github_integration.py`

### Task 6: Cases E2E-10-D..E2E-12-D (sessions, MCP, selfhost migration)

**Intent:** agent session capture with LLM mock-mode extraction; MCP Streamable-HTTP handshake + tool call (SSE framing); selfhost→hosted migration parity with a second real server.
**Acceptance:** `test_10_session_capture.py`, `test_11_mcp_connect.py`, `test_12_selfhost_migration.py` pass; MCP tools/call creates a Point readable via REST; selfhost daemon boots on its own DB path with static key auth; migration asserts query parity; cross-surface keys rejected.
**Files:**
- Create: `tests/e2e/hosted/test_10_session_capture.py`, `test_11_mcp_connect.py`, `test_12_selfhost_migration.py`

### Task 7: CI job + docs

**Intent:** wire the suite into CI following legal-e2e/welcome-e2e patterns; document the reconstruction + run instructions.
**Acceptance:** `hosted-e2e` job in `.github/workflows/ci.yml` (no secrets, no chromium install, 15m cap, concurrency group, skip-warning step); `tests/e2e/hosted/README.md` documents run modes, env contract, the 12-case map, and the reconstruction note.
**Files:**
- Modify: `.github/workflows/ci.yml`
- Create: `tests/e2e/hosted/README.md`

### Task 8: Verification sweep + runtime budget

**Intent:** proof the whole contract holds: suite green, main suite green, runtime <5 min, skip behavior correct.
**Acceptance:** `RUN_HOSTED_E2E=1 python -m pytest tests/e2e/hosted/ -q -rs` green locally; `python -m pytest tests/ -q` green (embedded, no Docker); plain `python -m pytest tests/e2e/hosted/ -q` without the env = all skipped with clear reason; total runtime reported <5 min.
**Files:**
- Test: (no new files)

---

## Verification Plan

1. Seam unit check + full `tests/` suite green (Tasks 1-6 gates).
2. `RUN_HOSTED_E2E=1 python -m pytest tests/e2e/hosted/ -v -rs` — all 12 cases green, `-rs` shows skip reasons for credentialed legs only.
3. Negative-path proof: unset `RUN_HOSTED_E2E` → 12 modules skip with message.
4. Runtime: `--durations=15` total < 300s.
5. CI YAML validity: `python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"`.

## Out of scope (documented)

- Dashboard SPA clickthrough (website/apps/dashboard: hardcoded prod `API_BASE`, localhost absent from CORS allowlist — a local browser leg cannot reach a local API without product changes). Adjacent gap to file once gh rate-limit clears: dashboard E2E + configurable API base.
- Live-prod signup/OAuth/email-confirmation legs (welcome-e2e + legal-e2e own the website browser surface; this suite is API-journey).
- Stripe real-checkout / R2 / GitHub token-exchange positive legs — skip-guarded per-leg on `STRIPE_TEST_*` / `R2_*` / `GITHUB_CLIENT_SECRET` (E2E-3-D precedent), loud skips in CI.


---

<!-- plan-review: cycles=2 status=clean tier=standard reviewers=structural+integration+efficiency
cycle-1: structural 5×P1 (membership gap→/internal/provision+POST /v1/teams path; e2e_small tier spec; team price id; bare server for unconfigured negatives; unknown-price semantics) + integration 4×P1 (invites team-tier gate; register-no-membership; cryptography NOT in .[test] closure; webhook unknown-price=200) + 8×P2/P3 — all fixed in doc
cycle-2: efficiency P1 (TORTOISE_DB_URI scrub) + P2 (crash detection) + 2×P3 — fixed above
-->
