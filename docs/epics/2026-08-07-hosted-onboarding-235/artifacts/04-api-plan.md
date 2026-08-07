<!-- research-path: docs/epics/2026-08-07-hosted-onboarding-235/01-research-brief.md -->

# API Gap Analysis + New Hosted Endpoints — Implementation Plan

> **Issue:** #498 (child of epic #235) | **Complexity:** COMPLEX (Architecture, Ontology, UX)
> **Dependencies:** #495 (DONE — AGENT_ONBOARDING.md draft), #496 (IN FLIGHT — finalizes prompt wording)
> **Plan author:** issue-scoping v5.1 double diamond | **Date:** 2026-08-07

## Confirmed Problem Definition

The hosted onboarding flow (#235) requires an agent prompt (from #495) to drive ≤6 yes/no questions. That prompt invokes MCP tools (`tortoise_onboarding_*`) that need backend support. The current `hosted_api.py` surface (19 endpoints) lacks:
- **Self-service key provisioning** (only `/internal/provision` exists, gated behind `FASTAPI_INTERNAL_KEY`)
- **GitHub OAuth connect/status** — no OAuth flow at all
- **Background indexing** endpoint — no async job infrastructure
- **Public demo graph** endpoint — only `/internal/demo` (same internal-key gate)
- **Session recording toggle** — no persistence for the toggle
- **Onboarding state tracking** — no CRUD surface for per-team onboarding progress
- **Hosted team creation** — `tortoise_team_create` MCP tool is stdio-only; no hosted equivalent

The agent prompt's contract (AGENT_ONBOARDING.md) requires these MCP tool names:
`tortoise_onboarding_github_connect`, `tortoise_onboarding_github_status`,
`tortoise_onboarding_github_index`, `tortoise_onboarding_demo_create`,
`tortoise_onboarding_session_recording`, `tortoise_onboarding_create_team`,
`tortoise_onboarding_state`, plus existing `tortoise_health` and `tortoise_context`.

**Root cause:** The hosted platform was designed for operator provisioning (internal-key gate). The onboarding self-service flow removes the operator from the loop — requiring public equivalents of `/internal/provision`, `/internal/demo`, and new onboarding-specific state trackers.

## Gap Analysis

### Existing Endpoint Inventory

| Endpoint | Method | Auth | Purpose | Reuse in onboarding? |
|----------|--------|------|---------|----------------------|
| `/health` | GET | None | Liveness + DB check | ✅ Reuse (Q0 health check) |
| `/health/security` | GET | None | Security posture | ✅ Reuse (debug) |
| `/v1/points` | POST | Bearer `tt_` | Create Point | ✅ Reuse (indexing writes points) |
| `/v1/points` | GET | Bearer `tt_` | List/query Points | ✅ Reuse |
| `/v1/points/{id}` | GET | Bearer `tt_` | Get single Point | ✅ Reuse |
| `/v1/search` | GET | Bearer `tt_` | Hybrid search | ✅ Reuse |
| `/v1/team` | GET | Bearer `tt_` | Team info + usage | ✅ Reuse |
| `/v1/team/keys` | POST | Bearer `tt_` | Create API key | ✅ Reuse (key rotation) |
| `/v1/team/keys` | GET | Bearer `tt_` | List API keys | ✅ Reuse |
| `/v1/team/keys/{id}` | DELETE | Bearer `tt_` | Revoke key | ✅ Reuse |
| `/v1/sessions` | POST | Bearer `tt_` | Capture session | ✅ Reuse (session recording) |
| `/v1/sessions` | GET | Bearer `tt_` | List sessions | ✅ Reuse |
| `/v1/context` | GET | Bearer `tt_` | Memory digest | ✅ Reuse (Q6 verification) |
| `/v1/dream` | POST | Bearer `tt_` | EP stabilization | ✅ Reuse |
| `/internal/provision` | POST | Internal key | Provision tenant | 🔧 Needs public variant |
| `/internal/demo` | POST | Internal key | Create demo graph | 🔧 Needs public variant |
| `/mcp` | Mount | Bearer `tt_` | 54 MCP tools | ✅ Reuse (Q0-Q6 via MCP) |

### New Endpoints Needed

| # | Endpoint | Method | Purpose | MCP Tool | Priority |
|---|----------|--------|---------|----------|----------|
| 1 | `/v1/register` | POST | Self-service key provisioning | — (web-only) | P0 |
| 2 | `/v1/onboarding/github/connect` | POST | Initiate GitHub OAuth | `tortoise_onboarding_github_connect` | P0 |
| 3 | `/v1/onboarding/github/callback` | GET | GitHub OAuth callback | — (browser redirect) | P0 |
| 4 | `/v1/onboarding/github/status` | GET | Check GitHub connection | `tortoise_onboarding_github_status` | P0 |
| 5 | `/v1/index/github` | POST | Start background indexing | `tortoise_onboarding_github_index` | P1 |
| 6 | `/v1/index/github/{job_id}` | GET | Poll indexing status | — (polling) | P1 |
| 7 | `/v1/demo` | POST | Create/reset demo graph (PUBLIC) | `tortoise_onboarding_demo_create` | P0 |
| 8 | `/v1/onboarding/session-recording` | POST | Toggle session recording | `tortoise_onboarding_session_recording` | P1 |
| 9 | `/v1/onboarding/state` | GET | Get onboarding state | `tortoise_onboarding_state` | P0 |
| 10 | `/v1/onboarding/state` | PATCH | Update onboarding state | `tortoise_onboarding_state` | P0 |
| 11 | `/v1/onboarding/team` | POST | Create team (hosted) | `tortoise_onboarding_create_team` | P1 |
| 12 | `/v1/onboarding/complete` | POST | Mark onboarding complete + fire analytics | — (triggers from prompt Q6) | P2 |

### What Goes Through MCP vs REST

**Decision: New REST endpoints serve as the canonical backend; MCP tools wrap them.**

| Layer | What lives here | Why |
|-------|----------------|-----|
| **MCP tools** | `tortoise_onboarding_*` — agent-facing contract | The agent prompt (#495) invokes these. Must match the names in AGENT_ONBOARDING.md exactly. |
| **REST endpoints** | `/v1/onboarding/*`, `/v1/demo`, `/v1/index/*`, `/v1/register` | Needed for: (a) OAuth callback URLs (browser, not MCP), (b) welcome page polling, (c) idempotency + auth gating, (d) async job polling |
| **SDK (direct)** | Existing `tortoise_health`, `tortoise_context`, `tortoise_create_point` — already in MCP | No new REST needed; MCP tools call the SDK directly |

**MCP tools that are thin wrappers** (call REST endpoints):
- `tortoise_onboarding_github_connect` → `POST /v1/onboarding/github/connect`
- `tortoise_onboarding_github_status` → `GET /v1/onboarding/github/status`
- `tortoise_onboarding_github_index` → `POST /v1/index/github`
- `tortoise_onboarding_demo_create` → `POST /v1/demo`
- `tortoise_onboarding_session_recording` → `POST /v1/onboarding/session-recording`
- `tortoise_onboarding_create_team` → `POST /v1/onboarding/team`
- `tortoise_onboarding_state` → `GET/PATCH /v1/onboarding/state`

**Why REST, not MCP-only:** Several onboarding actions need REST endpoints regardless:
1. **OAuth callback** — GitHub redirects the browser to a URL; this MUST be a GET endpoint (not MCP)
2. **Welcome page** — polls for key provisioning and state; browser JS can't call MCP
3. **Async job polling** — indexing is background; the agent polls via REST or MCP, but REST is simpler
4. **Future web dashboard** — will need these endpoints without MCP in the loop

Having REST endpoints as the canonical layer means the MCP tools and any future web dashboard share the same backend — no duplicated logic.

### Auth & Rate-Limit Approach

| Endpoint Group | Auth | Rate Limit | Notes |
|---------------|------|------------|-------|
| `/v1/register` | None (creates account) | IP-based: 3/hour | Prevent abuse; email verification gate |
| `/v1/onboarding/github/callback` | OAuth `state` param (CSRF) | None | Standard OAuth 2.0 flow |
| All other `/v1/onboarding/*`, `/v1/demo`, `/v1/index/*` | Bearer `tt_` (existing `get_current_team`) | Inherits existing 100/min per-key | Same auth as `/v1/points` |

The existing `get_current_team` dependency handles Bearer `tt_` auth via registry graph lookup. New endpoints reuse this dependency. The rate limiter already skips `/health`, `/docs`, `/openapi.json`, and `/internal/*`. New public onboarding endpoints are NOT skipped — they inherit the 100 req/min per-key limit.

For `/v1/register`: a separate IP-based rate limit (3 registrations/hour/IP) prevents abuse. This is a new concern not covered by the existing per-key limiter.

### Onboarding State Data Model

**Decision: Store onboarding state as a JSON property on the Team node in the FalkorDB registry graph.**

**Rejected alternatives:**

| Option | Why rejected |
|--------|-------------|
| Supabase `teams.onboarding_state` JSONB | hosted_api.py doesn't talk to Supabase today. `get_current_team` queries the registry graph. Adding Supabase as a dependency for one column introduces a new failure domain. |
| Separate Point in team graph | Requires graph traversal for every state read. The team graph could be empty (new user). Too heavyweight for a flat key-value store. |
| In-memory dict | State lost on restart. Unacceptable. |

**Chosen approach:** Extend the existing Team node in the registry graph with an `onboarding_state` property (JSON object). The Team node is already queried on every authenticated request via `get_current_team` — adding one more property costs nothing extra.

```json
// Team node onboarding_state shape
{
  "github_connected": false,
  "github_org": null,
  "github_connected_at": null,
  "github_indexed": false,
  "github_index_job_id": null,
  "session_recording": false,
  "demo_created": false,
  "team_created": false,
  "completed_at": null
}
```

**Migration:** Add `SET t.onboarding_state = $default_state` to `/internal/provision` (and the new `/v1/register`) so all new teams start with the default state. Existing teams get the default on first read (lazy init).

**Read path:** `GET /v1/onboarding/state` → queries Team node in registry graph → returns `onboarding_state` JSON.
**Write path:** `PATCH /v1/onboarding/state` → merges provided fields into existing state → writes back to Team node.
**Auto-update path:** `/v1/onboarding/github/callback` → auto-sets `github_connected: true` + `github_org`. `/v1/demo` → auto-sets `demo_created: true`.

---

## Solution Approach

### Architecture Decision: New endpoints in hosted_api.py, new MCP tools in mcp_server.py

The onboarding backend has two integration surfaces:

1. **hosted_api.py** — 12 new REST endpoints. All carry the same patterns as existing endpoints (Pydantic models, `get_current_team` auth, `_async_audit` logging). The async job system for indexing is a new concern (in-memory job dict + asyncio background tasks — same pattern as the dreaming queue).

2. **mcp_server.py** — 8 new MCP tools. Each is a thin wrapper that calls the SDK or a hosted API endpoint. All follow the existing `_safe()` / `_get_team_sdk()` pattern. Auth is handled by `TeamResolutionMiddleware` (already in the MCP sub-app).

### Why not a separate onboarding module?

The onboarding endpoints are tightly coupled to existing auth (`get_current_team`), the registry graph (Team nodes), and the SDK factory (`_make_sdk`). Extracting them into a separate module would require:
- Duplicating the auth dependency
- Passing the SDK factory through a shared module
- Managing CORS/middleware across multiple FastAPI apps

For 12 endpoints, the complexity of a separate module outweighs the organizational benefit. They live in `hosted_api.py` under a clear `# ── Onboarding ──` section.

### Rejected Alternative: MCP-only (no new REST)

**Why rejected:** The GitHub OAuth callback is a browser redirect — it MUST be a GET endpoint. The welcome page polls for state via JS — browsers can't call MCP. Async job polling needs a stable URL. Building REST endpoints is necessary for these surfaces; having them also serve the MCP tools avoids duplicating logic.

---

## Implementation Plan — TDD Tasks

### Task 1: Self-Service Key Provisioning (`POST /v1/register`)

**Intent:** Let users create Tortoise accounts without operator intervention. Currently, `/internal/provision` requires `FASTAPI_INTERNAL_KEY` — this endpoint is the public equivalent with email verification.
**Acceptance:** `POST /v1/register` accepts email+password, validates format, creates Supabase user + team + API key, returns `{api_key, team_id, graph_name}`. Idempotent: re-registering same email returns `{message: "already_registered"}` without re-exposing the key. Rate limited at 3/hour/IP.
**Files:**
- Modify: `tortoise/hosted_api.py` — add `POST /v1/register`
- Create: `tests/test_hosted_register.py`

**Steps:**
1. Write contract (Pydantic `RegisterRequest` / `RegisterResponse` models)
2. Write failing tests: `test_register_new_user`, `test_register_duplicate_email`, `test_register_rate_limit`, `test_register_invalid_email`
3. Implement endpoint: validate inputs → call Supabase Admin API → provision tenant (reuse `/internal/provision` logic) → return key
4. Add IP-based rate limiter for `/v1/register` path
5. Run tests → red → green → refactor

### Task 2: Public Demo Graph (`POST /v1/demo`)

**Intent:** Expose the demo graph creation behind Bearer `tt_` auth (currently only `/internal/demo` with internal key). The AGENT_ONBOARDING.md Q4 calls `tortoise_onboarding_demo_create` which needs this.
**Acceptance:** `POST /v1/demo` creates the same 4-layer demo graph as `/internal/demo` but uses `get_current_team` auth. Idempotent: second call overwrites (deletes previous demo points with `source='demo'`, recreates). Returns `{points_created, operators_created}`.
**Files:**
- Modify: `tortoise/hosted_api.py` — add `POST /v1/demo` (extract shared logic from `/internal/demo`)
- Modify: `tortoise/mcp_server.py` — add `tortoise_onboarding_demo_create` MCP tool
- Create: `tests/test_demo_graph.py`

**Steps:**
1. Extract demo graph creation logic from `/internal/demo` into `_create_demo_graph(team_id)` helper
2. Write failing test: `test_demo_creates_points`, `test_demo_idempotent`, `test_demo_requires_auth`
3. Implement `POST /v1/demo` using `get_current_team` + extracted helper
4. Implement `tortoise_onboarding_demo_create` MCP tool (wraps `POST /v1/demo` or calls helper directly)
5. Auto-update onboarding state: `demo_created: true`
6. Run tests → red → green → refactor

### Task 3: Onboarding State Endpoints (`GET/PATCH /v1/onboarding/state`)

**Intent:** Track onboarding progress per team. The agent prompt's Q6 calls `tortoise_onboarding_state()` to record completion; welcome page polls for progress display.
**Acceptance:** `GET /v1/onboarding/state` returns `{onboarding: {...}}`. `PATCH /v1/onboarding/state` merges provided fields. State persists on the Team node in the registry graph. Default state created during provisioning. Missing state auto-initializes on first read.
**Files:**
- Modify: `tortoise/hosted_api.py` — add state endpoints + extend `/internal/provision` and `/v1/register` to set default state
- Modify: `tortoise/mcp_server.py` — add `tortoise_onboarding_state` MCP tool
- Create: `tests/test_onboarding_state.py`

**Steps:**
1. Define Pydantic models: `OnboardingStateResponse`, `OnboardingStatePatchRequest`
2. Extend `/internal/provision` and `/v1/register` to set `t.onboarding_state = $default_state`
3. Write failing tests: `test_get_state_default`, `test_patch_state_merge`, `test_patch_state_invalid_key`, `test_get_state_requires_auth`
4. Implement `GET /v1/onboarding/state` — query Team node in registry graph
5. Implement `PATCH /v1/onboarding/state` — merge + validate + write back
6. Implement `tortoise_onboarding_state` MCP tool (wraps both GET and PATCH)
7. Run tests → red → green → refactor

### Task 4: GitHub OAuth Connect + Status

**Intent:** Let users authorize Tortoise to read their GitHub issues/PRs via OAuth. The agent prompt Q1 calls `tortoise_onboarding_github_connect`. The browser handles the callback.
**Acceptance:** `POST /v1/onboarding/github/connect` returns `{auth_url, state}`. `GET /v1/onboarding/github/callback` exchanges code for token, stores encrypted token on Team node. `GET /v1/onboarding/github/status` returns `{connected, org, repos_count}`. State param prevents CSRF. Token stored encrypted at rest.
**Files:**
- Modify: `tortoise/hosted_api.py` — add connect + callback + status endpoints
- Modify: `tortoise/mcp_server.py` — add `tortoise_onboarding_github_connect` + `tortoise_onboarding_github_status` MCP tools
- Create: `tests/test_github_connect.py`

**Steps:**
1. Add env vars: `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `GITHUB_CALLBACK_URL`
2. Write failing tests: `test_connect_returns_auth_url`, `test_callback_rejects_bad_state`, `test_callback_stores_token`, `test_status_requires_auth`, `test_callback_auto_updates_onboarding_state`
3. Implement `POST /v1/onboarding/github/connect` — generate state, build auth URL
4. Implement `GET /v1/onboarding/github/callback` — verify state, exchange code, encrypt token, store on Team node, auto-set `onboarding_state.github_connected: true`
5. Implement `GET /v1/onboarding/github/status` — check token exists, return org info
6. Implement MCP tools `tortoise_onboarding_github_connect` + `tortoise_onboarding_github_status`
7. Run tests → red → green → refactor

### Task 5: Background GitHub Indexing

**Intent:** When user says "yes" to indexing (Q2), start a background job that fetches their GitHub issues/PRs and creates Points. Async — agent gets a job ID for polling.
**Acceptance:** `POST /v1/index/github` accepts `{org}`, returns `{job_id, status: "started"}`. `GET /v1/index/github/{job_id}` returns progress. Background task fetches issues/PRs, creates Points with `source: "github"`. Rate limit handling: exponential backoff on 429, max 500 items per run.
**Files:**
- Create: `tortoise/indexer/github_indexer.py`
- Modify: `tortoise/hosted_api.py` — add index endpoints + asyncio job runner
- Modify: `tortoise/mcp_server.py` — add `tortoise_onboarding_github_index`
- Create: `tests/test_github_indexer.py`

**Steps:**
1. Write GitHub indexer class with `index_issues(org, sdk, token)` method
2. Write failing tests with mock GitHub API: `test_index_creates_job`, `test_poll_returns_progress`, `test_indexed_issues_become_points`, `test_rate_limit_handling`
3. Implement async job system (in-memory dict `_INDEX_JOBS`, asyncio.create_task pattern)
4. Implement `POST /v1/index/github` — validate org, create job, spawn background task
5. Implement `GET /v1/index/github/{job_id}` — return status + progress
6. Implement `tortoise_onboarding_github_index` MCP tool
7. Auto-update onboarding state on completion: `github_indexed: true`
8. Run tests → red → green → refactor

### Task 6: Session Recording Toggle

**Intent:** Toggle whether agent sessions are automatically recorded. The agent prompt Q3 calls `tortoise_onboarding_session_recording`.
**Acceptance:** `POST /v1/onboarding/session-recording` accepts `{enabled: bool}`, updates onboarding state, returns new state. The existing `/v1/sessions` endpoint is already recording — this toggle gates whether session capture is automatic.
**Files:**
- Modify: `tortoise/hosted_api.py` — add recording endpoint
- Modify: `tortoise/mcp_server.py` — add `tortoise_onboarding_session_recording`
- Create: `tests/test_session_recording.py`

**Steps:**
1. Write failing tests: `test_enable_recording`, `test_disable_recording`, `test_toggle_requires_auth`
2. Implement `POST /v1/onboarding/session-recording` — update `onboarding_state.session_recording`
3. Implement `tortoise_onboarding_session_recording` MCP tool
4. Run tests → red → green → refactor

### Task 7: Hosted Team Creation

**Intent:** `tortoise_team_create` is stdio-only (excluded from HTTP per #236). The agent prompt Q5 calls `tortoise_onboarding_create_team` — need a hosted equivalent. Creates a sub-team in the user's namespace.
**Acceptance:** `POST /v1/onboarding/team` accepts `{name}`, creates a team, returns `{team_id, name, invite_url}`. MCP tool `tortoise_onboarding_create_team` wraps it.
**Files:**
- Modify: `tortoise/hosted_api.py` — add `POST /v1/onboarding/team`
- Modify: `tortoise/mcp_server.py` — add `tortoise_onboarding_create_team`
- Create: `tests/test_onboarding_team.py`

**Steps:**
1. Write failing tests: `test_create_team`, `test_team_name_validation`, `test_team_requires_auth`
2. Implement `POST /v1/onboarding/team` — create team, set `onboarding_state.team_created: true`
3. Implement `tortoise_onboarding_create_team` MCP tool
4. Run tests → red → green → refactor

### Task 8: MCP Tool Wiring — Onboarding Tool Set

**Intent:** Wire all 8 new MCP tools into `mcp_server.py` and register them. Ensure each tool follows the existing pattern (`_safe()`, `_get_team_sdk()`, auth via `TeamResolutionMiddleware`). Verify they appear in the MCP tool list.
**Acceptance:** All 8 `tortoise_onboarding_*` tools are registered and callable via MCP. Each tool handles auth failures gracefully. Tool descriptions match the names in AGENT_ONBOARDING.md.
**Files:**
- Modify: `tortoise/mcp_server.py` — add 8 tool functions + registration
- Create: `tests/test_mcp_onboarding_tools.py` — verify tool registration

**Steps:**
1. Add each tool function to `mcp_server.py` with proper docstrings and type hints
2. Register all 8 tools with the MCP server
3. Write test: verify all 8 tools appear in `list_tools()` response
4. Write test: verify each tool rejects unauthenticated requests
5. Verify tool names match AGENT_ONBOARDING.md exactly

### Task 9: Integration — Onboarding State Auto-Update

**Intent:** Ensure endpoints auto-update onboarding state so the agent prompt doesn't need explicit state calls after every action. The agent prompt already tracks completion — this task ensures the backend reflects what happened.
**Acceptance:** `POST /v1/demo` → auto-sets `demo_created: true`. `POST /v1/onboarding/github/callback` → auto-sets `github_connected: true`. `POST /v1/index/github` completion → auto-sets `github_indexed: true`. `POST /v1/onboarding/session-recording` → auto-sets `session_recording`. `POST /v1/onboarding/team` → auto-sets `team_created: true`.
**Files:**
- Modify: `tortoise/hosted_api.py` — add state auto-update to each endpoint

**Steps:**
1. Add `_update_onboarding_state(team_id, **fields)` helper
2. Call it from each endpoint after successful operation
3. Write integration test: `test_demo_auto_updates_state`, etc.
4. Verify state reads back correctly via `GET /v1/onboarding/state`

---

## Integration Surface Map

| # | Surface | System A | System B | Type | Test Layer |
|---|---------|----------|----------|------|------------|
| 1 | `/v1/register` → Supabase Auth | `hosted_api.py` | Supabase Admin API (`createUser`) | REST | Integration test with mocked Supabase |
| 2 | `/v1/register` → FalkorDB registry | `hosted_api.py` | FalkorDB (Team + APIKey nodes) | Graph write | Unit test with FalkorDBLite |
| 3 | `/v1/register` → FalkorDB tenant | `hosted_api.py` | FalkorDB (tenant graph creation) | Graph write | Unit test with FalkorDBLite |
| 4 | `/v1/onboarding/github/callback` → GitHub OAuth | `hosted_api.py` | `github.com/login/oauth/access_token` | REST (external) | Integration test with mock HTTP |
| 5 | `/v1/onboarding/github/callback` → FalkorDB registry | `hosted_api.py` | FalkorDB (Team node update) | Graph write | Unit test |
| 6 | `/v1/index/github` → GitHub REST API | `github_indexer.py` | `api.github.com` (issues, PRs) | REST (external, async) | Integration test with mock |
| 7 | `/v1/index/github` → FalkorDB tenant | `github_indexer.py` → SDK | FalkorDB (Point creation) | Graph write (async) | Integration test |
| 8 | `/v1/demo` → FalkorDB tenant | `hosted_api.py` → SDK | FalkorDB (Points + Operators) | Graph write | Unit test |
| 9 | `/v1/onboarding/state` → FalkorDB registry | `hosted_api.py` | FalkorDB (Team node property) | Graph read/write | Unit test |
| 10 | MCP tools → hosted API | `mcp_server.py` | `hosted_api.py` (same process) | In-process call | MCP tool unit test |
| 11 | MCP tools → SDK (health, context) | `mcp_server.py` | `TortoiseSDK` | In-process call | Existing tests |
| 12 | `/v1/onboarding/*` → Audit log | `hosted_api.py` | PostgreSQL (audit_events) | Async thread pool | Unit test |

## Verification Plan

**Unit tests:** Each endpoint tested with FalkorDBLite (embedded, no Docker). Mock external services (GitHub, Supabase).
**Integration tests:** Mock GitHub API responses via `responses` or `httpx` mock transport. Test OAuth flow end-to-end with mock HTTP.
**Manual smoke tests:**
1. Paste AGENT_ONBOARDING.md into an agent connected to the hosted MCP server
2. Answer "yes" to all questions → verify all tools execute, state updates, digest displays
3. Answer "no" to all questions → verify all skip, health check passes, empty digest shown
4. Test with tool failures (simulate GitHub API down) → verify agent recovers per error recovery section

**Test commands:**
```bash
# Unit + integration tests (embedded FalkorDBLite)
python -m pytest tests/test_hosted_register.py tests/test_demo_graph.py tests/test_onboarding_state.py tests/test_github_connect.py tests/test_github_indexer.py tests/test_session_recording.py tests/test_onboarding_team.py -v

# MCP tool registration
python -m pytest tests/test_mcp_onboarding_tools.py -v
```

## Acceptance Criteria

1. All 8 MCP tool names in AGENT_ONBOARDING.md are implemented and callable
2. `POST /v1/register` creates accounts without operator intervention
3. GitHub OAuth flow works: connect → authorize → callback → token stored → status reports connected
4. Demo graph is idempotent: running twice doesn't duplicate
5. Onboarding state persists across restarts and is queryable at any time
6. Background indexing creates Points without blocking the agent
7. All endpoints reject unauthenticated requests with 401
8. All endpoints emit audit events
9. Rate limiter protects `/v1/register` at 3/hour/IP
10. AGENT_ONBOARDING.md tool names are the exact contract — zero divergence

## Dependencies on #496

#496 finalizes the question wording in AGENT_ONBOARDING.md. This plan assumes:
- The MCP tool names in the current draft (e.g., `tortoise_onboarding_github_connect`) are stable
- The question flow (≤6 questions, Q2 depends on Q1, Q6 always runs) is stable
- If #496 changes tool names: grep-replace in this plan's Task 8 (MCP tool wiring) and the test file

**Mitigation:** The MCP tool registry pattern in Task 8 uses a mapping dict — changing a tool name is a one-line change.

## Rejected Alternatives

| Alternative | Why rejected |
|------------|-------------|
| MCP-only (no new REST endpoints) | GitHub OAuth callback is a browser redirect → MUST be GET endpoint. Welcome page polls via JS → no MCP. Async job polling needs stable URL. |
| Separate FastAPI app for onboarding | 12 endpoints isn't enough to justify a new app. Auth dependency (`get_current_team`) and SDK factory (`_make_sdk`) are shared. Splitting duplicates these. |
| Supabase for onboarding state | `get_current_team` already queries FalkorDB registry. Adding Supabase as a new dependency for one JSONB column adds a failure domain. |
| Store onboarding state in team graph | New users have empty graphs. State queries shouldn't require graph traversal. Team node is the natural home. |
| PostgreSQL for async job queue | Adds infrastructure complexity. In-memory dict + asyncio tasks (same pattern as dreaming queue) is sufficient for v1 indexing (expected: 1-10 concurrent jobs, not 1000s). |
| Full GitHub SDK (PyGithub) | Adds dependency. REST API with `httpx` is sufficient for read-only issue/PR access. |
