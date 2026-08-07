<!-- parent-epic: docs/epics/2026-08-07-hosted-onboarding-235/03-plan.md -->
<!-- upstream-plan: docs/epics/2026-08-07-hosted-onboarding-235/artifacts/04-api-plan.md -->
<!-- question-contract: docs/epics/2026-08-07-hosted-onboarding-235/artifacts/02-question-set.md -->

# GitHub OAuth + Issue Indexing — Implementation Plan

> **Issue:** #499 (child of epic #235) | **Complexity:** COMPLEX (Architecture, Security, External API)
> **Dependencies:** #498 (API plan — defines endpoint contract), #496 (question set — Q1/Q2 wording)
> **Plan author:** issue-scoping v5.1 double diamond | **Date:** 2026-08-07

## Confirmed Problem Definition

When a user answers "yes" to Q1 ("Connect GitHub?") and Q2 ("Index your issues and PRs?") during hosted onboarding (#235), the backend must:

1. **Q1 — Initiate GitHub OAuth:** The MCP tool `tortoise_onboarding_github_connect` must return an authorization URL that the user opens in their browser. The browser completes the OAuth dance and lands on a callback endpoint that exchanges the code for a token and stores it securely.

2. **Q2 — Index issues/PRs:** The MCP tool `tortoise_onboarding_github_index` must spawn a background job that fetches the user's GitHub issues and PRs via the REST API and writes them into the team's FalkorDB graph as Points.

**Current state:** Zero GitHub OAuth infrastructure exists. The `tortoise/connectors/github.py` module uses the `gh` CLI for polling and has a `webhook_secret` env-var pattern (#324) — but no OAuth flow, no token storage, and no REST API-based indexing. The `gh` CLI approach is incompatible with hosted (Fly.io) deployments where the `gh` binary isn't available inside the container.

**Root cause:** The connector was built for local/CLI use. Hosted onboarding needs a server-side OAuth flow and direct GitHub REST API access.

### What #498 Defines (Contract)

From [04-api-plan.md](./04-api-plan.md), the GitHub-related endpoints are:

| # | Endpoint | Method | Purpose | MCP Tool |
|---|----------|--------|---------|----------|
| 2 | `/v1/onboarding/github/connect` | POST | Initiate GitHub OAuth, return `{auth_url, state}` | `tortoise_onboarding_github_connect` |
| 3 | `/v1/onboarding/github/callback` | GET | GitHub OAuth callback (browser redirect) | — (browser-only) |
| 4 | `/v1/onboarding/github/status` | GET | Check GitHub connection + repos | `tortoise_onboarding_github_status` |
| 5 | `/v1/index/github` | POST | Start background indexing, return `{job_id}` | `tortoise_onboarding_github_index` |
| 6 | `/v1/index/github/{job_id}` | GET | Poll indexing progress | — (polling) |

The MCP tools are thin wrappers around these REST endpoints. This is correct — OAuth callbacks MUST be browser GET endpoints.

### What Q1/Q2 Require (#496 Contract)

From [02-question-set.md](./02-question-set.md):

**Q1 "yes" path:**
1. Agent asks Q1a: "What's your GitHub organization or username?"
2. Agent calls `tortoise_onboarding_github_connect(org=<answer>)`
3. Tool returns `{auth_url, state}` — agent displays the link
4. User authorizes in browser → callback fires → token stored
5. Agent calls `tortoise_onboarding_github_status()` to confirm
6. State: `github_connected: true, github_org: <org>`

**Q2 "yes" path:**
1. Agent calls `tortoise_onboarding_github_index(org=<org from Q1a>)`
2. Tool returns `{job_id, status: "started"}`
3. Agent displays confirmation, continues to Q3
4. Background job fetches issues/PRs, creates Points
5. State: `github_indexed: true` on completion

---

## OAuth Flow Design

### Sequence Diagram

```
Agent (MCP)          hosted_api.py         GitHub.com           Browser (user)
    │                     │                     │                     │
    │──connect(org)──────>│                     │                     │
    │                     │──generate state────>│                     │
    │                     │──store state+org───>│ (Team node)         │
    │<──{auth_url,state}──│                     │                     │
    │                     │                     │                     │
    │ "Open this link:"   │                     │                     │
    │──────────────────────────────────────────────────────────────>│
    │                     │                     │<──GET /authorize───│
    │                     │                     │──redirect─────────>│
    │                     │<──GET /callback?code=&state=────────────│
    │                     │──verify state────────│                     │
    │                     │──POST /access_token─>│                     │
    │                     │<──{access_token}─────│                     │
    │                     │──encrypt token──────>│ (Team node)         │
    │                     │──redirect welcome───>│                     │
    │                     │                     │                     │
    │──status()──────────>│                     │                     │
    │                     │──decrypt token──────>│                     │
    │                     │──GET /user/repos────>│                     │
    │<──{connected,repos}─│                     │                     │
```

### Step Detail

**Step 1: Initiate (`POST /v1/onboarding/github/connect`)**
- Auth: Bearer `tt_` (existing `get_current_team` dependency)
- Input: `{org: str}` — GitHub organization or username
- Action: Generate CSRF `state` (32 random hex bytes via `secrets.token_hex`), store `{state, team_id, org}` in FalkorDB Team node with TTL (10 min expiry — if callback doesn't arrive within 10 min, state is invalid)
- Response: `{auth_url: "https://github.com/login/oauth/authorize?client_id=...&state=...&scope=repo", state: "..."}`

**Step 2: Authorization (browser)**
- User opens `auth_url` in browser
- GitHub prompts: "Tortoise wants to access your repositories" with scopes listed
- User approves → GitHub redirects to `GITHUB_CALLBACK_URL` (e.g., `https://api.premiselabs.co/v1/onboarding/github/callback`) with `?code=...&state=...`
- User denies → GitHub redirects with `?error=access_denied`

**Step 3: Callback (`GET /v1/onboarding/github/callback`)**
- Auth: None (CSRF `state` param is the security gate)
- Query params: `code`, `state` (and `error` on denial)
- Action:
  1. If `error=access_denied` → redirect to welcome page with `?github=denied`
  2. Validate `state` against stored state on Team node → reject on mismatch (404, "invalid or expired state")
  3. Exchange `code` for token: `POST https://github.com/login/oauth/access_token` with `client_id`, `client_secret`, `code`
  4. Encrypt `access_token` and store on Team node: `SET t.github_token = <encrypted>`, `SET t.github_org = <org>`, `SET t.onboarding_state.github_connected = true`, clear stored `state`
  5. Redirect browser to welcome page with `?github=connected&org=<org>`

**Step 4: Status (`GET /v1/onboarding/github/status`)**
- Auth: Bearer `tt_`
- Action: Read encrypted token from Team node, decrypt, call `GET /user/repos` with token → return `{connected: true, org: "...", repos_count: N}`. If no token: `{connected: false}`
- MCP tool `tortoise_onboarding_github_status` wraps this endpoint.

---

## Token Storage Decision

### Decision: Encrypted at rest on FalkorDB Team node

| Option | Rationale |
|--------|-----------|
| **Env var (per #324 webhook pattern)** | ❌ **Rejected.** Env vars are global per deployment — can't store per-team tokens. The webhook secret is a single shared secret; GitHub OAuth tokens are per-team. Doesn't scale beyond 1 team. |
| **Supabase `teams.github_token`** | ❌ **Rejected.** `hosted_api.py` doesn't talk to Supabase today. `get_current_team` queries the FalkorDB registry graph. Adding Supabase as a dependency for one column introduces a new failure domain — same reasoning as the API plan's rejection of Supabase for onboarding state. |
| **FalkorDB Team node (registry graph)** | ✅ **Chosen.** The Team node is already queried on every authenticated request via `get_current_team`. GitHub token lives alongside onboarding state on the same node. Encrypted at rest — the raw token is never stored in plaintext. |

### Encryption Scheme

```
Encryption: Fernet (symmetric, AES-128-CBC + HMAC-SHA256, from cryptography library)
Key: TORTOISE_ENCRYPTION_KEY env var (32-byte base64, generated once via: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
Storage: Team node property `github_token_encrypted` (base64 string)
```

**Why Fernet:**
- Standard library (`cryptography.fernet`) — zero new dependencies (cryptography is already in the stack via Supabase/Httpx)
- Symmetric — same key for encrypt/decrypt, ideal for server-side token storage
- Includes HMAC authentication — detects tampering
- The raw token never touches disk or logs in plaintext

**Key management:**
- `TORTOISE_ENCRYPTION_KEY` is a Fly.io secret — never in `.env`, never committed
- Key rotation: decrypt all tokens with old key, re-encrypt with new key (migration script, out of scope for v1)
- If the key is lost, all GitHub tokens are unrecoverable — users must re-authorize. Acceptable for v1.

### Comparison with #324 (webhook_secret pattern)

| Aspect | Webhook secret (#324) | GitHub OAuth token (#499) |
|--------|----------------------|--------------------------|
| Cardinality | One per deployment | One per team |
| Storage | Env var (`GITHUB_WEBHOOK_SECRET`) | FalkorDB Team node (encrypted) |
| Rotation | Redeploy | Re-authorize (v1) / key rotation script (v2) |
| Plaintext in logs? | Never | Never (decrypted only in-memory, never logged) |

Both follow the principle of "secrets never in code" — they differ in storage location because of cardinality.

---

## Indexing Approach

### Decision: In-process asyncio background task with in-memory job tracker

| Option | Rationale |
|--------|-----------|
| **External queue (Redis, Celery)** | ❌ **Rejected.** Adds infrastructure complexity. V1 expected load: 1-10 concurrent indexing jobs. An external queue is overengineering for this volume. |
| **Fly.io Machines API (ephemeral workers)** | ❌ **Rejected.** Would require a separate Fly app + inter-service auth. Too much infrastructure for v1. |
| **In-process asyncio.create_task + in-memory dict** | ✅ **Chosen.** Same pattern the API plan recommends for the dreaming queue. `asyncio.create_task` spawns the indexer in the same event loop; an in-memory `_INDEX_JOBS: dict[str, dict]` tracks progress. |

### Indexing Flow

```
POST /v1/index/github {org, repo?}
  │
  ├─ Validate org, resolve repos (GET /orgs/{org}/repos or /users/{org}/repos)
  ├─ Create job_id (uuid4)
  ├─ Store in _INDEX_JOBS: {job_id: {status: "running", progress: 0, points_created: 0, ...}}
  ├─ Return {job_id, status: "started"}
  │
  └─ asyncio.create_task(_run_indexing(job_id, team_id, org, repos, token))
       │
       ├─ For each repo:
       │    ├─ GET /repos/{org}/{repo}/issues?state=all&per_page=100 (paginated)
       │    ├─ GET /repos/{org}/{repo}/pulls?state=all&per_page=100 (paginated)
       │    ├─ For each issue/PR:
       │    │    └─ SDK.create_point(kind="observation", content="...", source="github",
       │    │       props={"github_url": "...", "repo": "...", "number": N, "state": "..."})
       │    └─ Update _INDEX_JOBS[job_id].progress
       │
       ├─ On completion: _INDEX_JOBS[job_id].status = "completed"
       │                  Auto-update onboarding_state.github_indexed = true
       │
       └─ On error: _INDEX_JOBS[job_id].status = "failed", .error = str(e)
```

### GitHub REST API Details

- **Base URL:** `https://api.github.com`
- **Auth:** `Authorization: Bearer <oauth_token>`
- **Rate limit:** 5000 requests/hour for authenticated users
- **Pagination:** Link header (`rel="next"`), max 100 per page
- **Issues endpoint:** `GET /repos/{owner}/{repo}/issues?state=all&per_page=100&page=N`
  - Note: GitHub's issues endpoint returns both issues AND PRs. Filter PRs out by checking for `pull_request` key.
- **PRs endpoint:** `GET /repos/{owner}/{repo}/pulls?state=all&per_page=100&page=N`

### Point Schema for Indexed Items

```python
# Issue → Point
Point(
    kind="observation",
    source="github",
    content=f"[#{number}] {title}\n\n{body[:5000]}",  # Truncate to 5000 chars
    props={
        "github_url": html_url,
        "github_number": number,
        "github_state": state,
        "github_repo": repo,
        "github_type": "issue",
        "github_author": author_login,
        "github_created_at": created_at,
        "github_labels": labels,
    }
)

# PR → Point
Point(
    kind="observation",
    source="github",
    content=f"[PR #{number}] {title}\n\n{body[:5000]}",
    props={
        "github_url": html_url,
        "github_number": number,
        "github_state": state,  # open/closed/merged
        "github_repo": repo,
        "github_type": "pull_request",
        "github_author": author_login,
        "github_created_at": created_at,
        "github_merged_at": merged_at,
    }
)
```

### Rate Limit Handling

```python
# GitHub returns X-RateLimit-Remaining header on every response
# Strategy: exponential backoff + jitter when approaching limit

async def _check_rate_limit(response):
    remaining = int(response.headers.get("X-RateLimit-Remaining", 5000))
    reset_at = int(response.headers.get("X-RateLimit-Reset", 0))
    if remaining < 100:  # Buffer: stop at 100 remaining
        wait = max(reset_at - time.time(), 0) + 1  # +1s buffer
        logger.warning(f"GitHub rate limit approaching: {remaining} remaining, waiting {wait}s")
        await asyncio.sleep(wait)

# On 429: exponential backoff with jitter
# Retry: 1s, 2s, 4s, 8s, 16s (max 5 retries, then mark job failed)
```

### Idempotency

- **Dedup key:** `github_url` in Point props — SDK's native dedup (`dedup=True` default in `create_point`) prevents duplicates
- **Re-running indexing:** Creates no new Points for already-indexed items. Adds Points for new items since last run.
- **Job tracking:** Jobs are in-memory — lost on restart. Re-indexing after restart re-processes all items but dedup prevents duplicates.

### Job Retention

- **In-memory dict `_INDEX_JOBS`:** Lost on restart. Acceptable for v1 — indexing is a fire-and-forget operation during onboarding.
- **Completed jobs:** Retained for 1 hour in memory (poll window), then evicted.
- **Failed jobs:** Retained for 1 hour with error details for debugging.

---

## Security Design

### CSRF Protection (OAuth State)

```
state = secrets.token_hex(32)  # 256-bit random
Stored on Team node: {state: {team_id, org, created_at}} with 10-min TTL
Callback validates: state exists, not expired, team_id matches the one from the state
After exchange: state is deleted (one-time use)
```

- **Replay resistance:** State is single-use — consumed on first valid callback
- **Expiry:** 10 minutes. State is cleaned up by FalkorDB TTL
- **Binding:** State is bound to `team_id` — prevents cross-team state injection

### Token Scopes (Minimal)

Requested scope: `repo` (read-only access to repositories)

> **Why `repo` and not narrower?** GitHub doesn't offer an "issues-only" scope. The `repo` scope grants read access to issues, PRs, and repository metadata. This is the minimum viable scope for reading issues/PRs. Token is stored encrypted and only used server-side — the user's browser never sees it.

**What `repo` grants (that we don't need):**
- Read/write code (we only read issues/PRs — but scope can't be narrowed)
- Deploy keys, webhooks (not used)

**Mitigation:** The token is never exposed to the user or agent. It's stored encrypted and used only server-side for indexing. We explicitly scope to `repo` (not `user`, `admin:org`, etc.) and document that Tortoise only reads issues and PR metadata.

### No Token in Logs

```python
# ✅ Correct — token redacted
logger.info(f"GitHub callback processed for team={team_id}, org={org}")

# ❌ Never do this
logger.info(f"Token: {token}")
```

- All log statements referencing GitHub omit the token
- The encrypted token (`github_token_encrypted`) is safe to log (but still shouldn't be — avoid by policy)
- Audit events (`_async_audit`) record "github_connected" with team_id + org — never the token

### PII Considerations

- **GitHub issues/PRs:** May contain PII (email addresses, names in issue bodies). These are written into the team's private FalkorDB graph — same security boundary as all other team data.
- **Author metadata:** Stored in Point props (`github_author`) — this is public GitHub data (every issue author is public).
- **Token storage:** Encrypted at rest. Decrypted only in-memory during API calls.

### Defense in Depth

| Layer | Measure |
|-------|---------|
| Transport | HTTPS only (enforced by GitHub OAuth + Fly.io TLS termination) |
| Authentication | Bearer `tt_` for connect/status/index endpoints; CSRF state for callback |
| Authorization | GitHub token scoped to `repo` only |
| Storage | Fernet encryption at rest on FalkorDB Team node |
| Logging | Token never logged; audit events record team_id + org without secrets |
| Rate limiting | Inherits existing 100 req/min per-key for connect/status/index; GitHub API rate limit handling in indexer |

---

## Implementation Plan — TDD Tasks

### Task 1: GitHub OAuth Infrastructure (Encryption + Env Vars)

**Intent:** Set up the encryption layer and environment variables needed by Tasks 2-4. No endpoints yet — pure infrastructure.

**Acceptance:**
- `TORTOISE_ENCRYPTION_KEY` env var is documented in `.env.example`
- `_encrypt_token(token: str) -> str` and `_decrypt_token(encrypted: str) -> str` helper functions exist and are tested
- `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `GITHUB_CALLBACK_URL` env vars are documented
- Encryption round-trip test passes: `_decrypt_token(_encrypt_token("test")) == "test"`
- Bad ciphertext raises `ValueError` (tamper detection)

**Files:**
- Create: `tortoise/crypto.py` — encryption helpers
- Create: `tests/test_crypto.py` — encryption tests
- Modify: `.env.example` — add `TORTOISE_ENCRYPTION_KEY`, `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `GITHUB_CALLBACK_URL`

**Steps:**
1. Add `cryptography` to dependencies (already in stack via Supabase, verify it's available)
2. Implement `_encrypt_token` / `_decrypt_token` using Fernet
3. Write tests: round-trip, tamper detection, empty string, unicode token
4. Add env var documentation to `.env.example`
5. Run tests → red → green → refactor

---

### Task 2: GitHub OAuth Connect + Callback Endpoints

**Intent:** Implement the OAuth dance — connect (returns auth URL) and callback (exchanges code, stores token). This is the core of #499.

**Acceptance:**
- `POST /v1/onboarding/github/connect` accepts `{org}`, returns `{auth_url, state}`, requires Bearer `tt_`
- `GET /v1/onboarding/github/callback` handles `?code=&state=`, exchanges for token, stores encrypted token, redirects to welcome page
- CSRF state is single-use, expires in 10 min, bound to team_id
- Token is encrypted before storage, never logged
- On `?error=access_denied`: redirect with `?github=denied`
- On invalid/missing state: 404 (don't leak whether state existed)
- Audit events emitted for connect + callback
- Auto-updates `onboarding_state.github_connected: true` + `onboarding_state.github_org`

**Files:**
- Modify: `tortoise/hosted_api.py` — add connect + callback endpoints
- Create: `tests/test_github_connect.py`

**Steps:**
1. Define Pydantic models: `GitHubConnectRequest`, `GitHubConnectResponse`
2. Write failing tests: `test_connect_returns_auth_url`, `test_connect_requires_auth`, `test_callback_valid_exchange`, `test_callback_rejects_bad_state`, `test_callback_rejects_expired_state`, `test_callback_handles_denial`, `test_callback_stores_encrypted_token`, `test_callback_auto_updates_onboarding_state`
3. Implement `POST /v1/onboarding/github/connect`:
   - Generate `state`, store `{state: {team_id, org, created_at}}` on Team node
   - Build GitHub authorize URL: `https://github.com/login/oauth/authorize?client_id=...&redirect_uri=...&scope=repo&state=...`
   - Return `{auth_url, state}`
4. Implement `GET /v1/onboarding/github/callback`:
   - Extract `code`, `state`, `error` from query params
   - Validate state: exists, not expired, team_id matches
   - If error: redirect with `?github=denied`
   - Exchange code: `POST https://github.com/login/oauth/access_token` with httpx
   - Encrypt token, store on Team node, clear state, update onboarding_state
   - Redirect to welcome page
5. Add audit events for connect + callback
6. Run tests → red → green → refactor

---

### Task 3: GitHub Status Endpoint + MCP Tool Wiring

**Intent:** Add the status endpoint so the agent can verify the GitHub connection succeeded, and wire both connect + status MCP tools.

**Acceptance:**
- `GET /v1/onboarding/github/status` returns `{connected: bool, org: str|null, repos_count: int|null}`, requires Bearer `tt_`
- If connected: decrypts token, calls `GET /user/repos` to count repos
- If not connected: returns `{connected: false, org: null, repos_count: null}`
- MCP tool `tortoise_onboarding_github_connect` wraps `POST /v1/onboarding/github/connect`
- MCP tool `tortoise_onboarding_github_status` wraps `GET /v1/onboarding/github/status`
- MCP tools handle auth failures gracefully (return error string, don't crash)

**Files:**
- Modify: `tortoise/hosted_api.py` — add status endpoint
- Modify: `tortoise/mcp_server.py` — add `tortoise_onboarding_github_connect` + `tortoise_onboarding_github_status`
- Create: `tests/test_github_status.py`

**Steps:**
1. Write failing tests: `test_status_connected`, `test_status_not_connected`, `test_status_requires_auth`, `test_status_handles_github_api_error`
2. Implement `GET /v1/onboarding/github/status`:
   - Read encrypted token from Team node
   - If no token: return `{connected: false}`
   - Decrypt token, call `GET https://api.github.com/user/repos?per_page=1` (just for count from headers/link)
   - Return `{connected: true, org: ..., repos_count: N}`
3. Implement `tortoise_onboarding_github_connect` MCP tool:
   - Follows existing `_safe()` / `_get_team_sdk()` pattern
   - Calls `POST /v1/onboarding/github/connect` internally (or direct SDK call since same process)
   - Returns `{auth_url, state}` to agent
4. Implement `tortoise_onboarding_github_status` MCP tool:
   - Calls `GET /v1/onboarding/github/status` internally
   - Returns connection status to agent
5. Run tests → red → green → refactor

---

### Task 4: GitHub Indexer Module

**Intent:** Build the background indexing engine that fetches issues/PRs from GitHub and creates Points. Module only — no endpoint wiring yet.

**Acceptance:**
- `GitHubIndexer` class with `async index_issues(sdk, org, token, repo=None) -> dict`
- Fetches issues and PRs via GitHub REST API (httpx)
- Creates Points with `source="github"`, maps to `kind="observation"`
- Rate-limit aware: checks `X-RateLimit-Remaining`, exponential backoff on 429
- Pagination: follows `Link` header `rel="next"`
- Idempotent: re-running same org doesn't create duplicate Points (SDK dedup by `github_url`)
- Max 5000 chars per issue/PR body (prevents huge Points)
- Returns `{points_created, repos_processed, errors: [...]}`

**Files:**
- Create: `tortoise/indexer/github_indexer.py`
- Create: `tests/test_github_indexer.py`

**Steps:**
1. Write `GitHubIndexer` class with `__init__(token: str, httpx_client: httpx.AsyncClient | None = None)`
2. Implement `async index_issues(sdk, org, repo=None)`:
   - Resolve repos: `GET /orgs/{org}/repos` or `GET /users/{org}/repos` (try org, fall back to user)
   - For each repo, paginate through issues + PRs
   - For each item, call `sdk.create_point(kind="observation", content=..., source="github", props={...})`
   - Handle rate limits (check headers, exponential backoff)
   - Handle errors (log, add to errors list, continue)
3. Write tests with mock httpx transport:
   - `test_index_issues_creates_points`
   - `test_index_handles_pagination`
   - `test_index_rate_limit_backoff`
   - `test_index_dedup_no_duplicates`
   - `test_index_handles_network_error`
   - `test_index_truncates_large_bodies`
4. Run tests → red → green → refactor

---

### Task 5: Indexing Endpoints + Job Runner

**Intent:** Wire the indexer into the API with async job tracking, and add the MCP tool.

**Acceptance:**
- `POST /v1/index/github` accepts `{org, repo?}`, returns `{job_id, status: "started"}`, requires Bearer `tt_`
- `GET /v1/index/github/{job_id}` returns `{job_id, status, progress, points_created, error?}`
- Background job runs via `asyncio.create_task`, tracked in `_INDEX_JOBS` dict
- On completion: `onboarding_state.github_indexed: true`
- MCP tool `tortoise_onboarding_github_index` wraps `POST /v1/index/github`
- Jobs evicted from `_INDEX_JOBS` after 1 hour

**Files:**
- Modify: `tortoise/hosted_api.py` — add index endpoints + job runner
- Modify: `tortoise/mcp_server.py` — add `tortoise_onboarding_github_index`
- Create: `tests/test_github_index_endpoints.py`

**Steps:**
1. Define Pydantic models: `GitHubIndexRequest`, `GitHubIndexResponse`, `IndexJobStatus`
2. Add `_INDEX_JOBS: dict[str, dict] = {}` module-level dict
3. Implement `_run_indexing(job_id, team_id, sdk, org, repo, token)` async function:
   - Creates `GitHubIndexer`, runs `index_issues`
   - Updates `_INDEX_JOBS[job_id]` with progress
   - On completion: auto-update onboarding state
   - Schedule eviction after 1 hour: `asyncio.get_event_loop().call_later(3600, lambda: _INDEX_JOBS.pop(job_id, None))`
4. Write failing tests: `test_index_creates_job`, `test_poll_returns_progress`, `test_poll_completed_job`, `test_index_requires_auth`, `test_index_invalid_org`
5. Implement `POST /v1/index/github`:
   - Validate org, read encrypted token from Team node
   - Create job_id, spawn `asyncio.create_task(_run_indexing(...))`
   - Return `{job_id, status: "started"}`
6. Implement `GET /v1/index/github/{job_id}`:
   - Lookup job in `_INDEX_JOBS`, return status or 404
7. Implement `tortoise_onboarding_github_index` MCP tool
8. Run tests → red → green → refactor

---

### Task 6: Integration — End-to-End GitHub Flow

**Intent:** Verify the full flow works end-to-end: connect → callback → status → index → queryable Points in graph.

**Acceptance:**
- Full OAuth flow works with mock GitHub API (mock httpx transport for `github.com/login/oauth/access_token` and `api.github.com/*`)
- Onboarding state reflects `github_connected: true` after callback
- Indexed Points are queryable via SDK (`sdk.query(kind="observation", source="github")`)
- Error paths graceful: denied OAuth, bad state, rate limit, network error
- MCP tools return correct responses at each step

**Files:**
- Create: `tests/test_github_integration.py`

**Steps:**
1. Write integration test `test_full_github_flow`:
   - Mock GitHub OAuth (token exchange) + REST API (issues, PRs)
   - Simulate connect → callback → status → index
   - Verify Points created, onboarding state updated
2. Write error path tests: `test_oauth_denied`, `test_callback_bad_state`, `test_index_rate_limited`, `test_index_network_error`
3. Verify audit events emitted for each step
4. Run tests → red → green → refactor

---

## Integration Surface Map

| # | Surface | System A | System B | Type | Test Layer | Key Risk |
|---|---------|----------|----------|------|------------|----------|
| 1 | `POST /v1/onboarding/github/connect` → State store | `hosted_api.py` | FalkorDB registry (Team node) | Graph write | Unit (FalkorDBLite) | State collision on concurrent connects |
| 2 | `GET /v1/onboarding/github/callback` → State verify | Browser (GitHub redirect) | `hosted_api.py` | HTTP GET + OAuth | Integration (mock HTTP) | CSRF state replay, expiry |
| 3 | `GET /v1/onboarding/github/callback` → Token exchange | `hosted_api.py` | `github.com/login/oauth/access_token` | REST (external) | Integration (mock HTTP) | GitHub API downtime, bad client secret |
| 4 | `GET /v1/onboarding/github/callback` → Token store | `hosted_api.py` | FalkorDB registry (Team node) | Graph write (encrypted) | Unit | Encryption key missing, Fernet error |
| 5 | `GET /v1/onboarding/github/status` → GitHub API | `hosted_api.py` | `api.github.com/user/repos` | REST (external) | Integration (mock HTTP) | Rate limit, token revoked |
| 6 | `POST /v1/index/github` → Token read | `hosted_api.py` | FalkorDB registry (Team node) | Graph read (decrypt) | Unit | Token missing, decryption failure |
| 7 | `POST /v1/index/github` → GitHub issues API | `github_indexer.py` | `api.github.com/repos/*/issues` | REST (external, paginated) | Integration (mock HTTP) | Rate limit 5000/hr, large repos |
| 8 | `POST /v1/index/github` → GitHub PRs API | `github_indexer.py` | `api.github.com/repos/*/pulls` | REST (external, paginated) | Integration (mock HTTP) | Rate limit, pagination edge cases |
| 9 | `POST /v1/index/github` → Point creation | `github_indexer.py` → SDK | FalkorDB tenant graph | Graph write (async) | Integration | Tenant isolation, Point schema |
| 10 | `GET /v1/index/github/{job_id}` → Job tracker | `hosted_api.py` | In-memory `_INDEX_JOBS` | In-process dict | Unit | Jobs lost on restart |
| 11 | MCP `tortoise_onboarding_github_connect` → hosted API | `mcp_server.py` | `hosted_api.py` (same process) | In-process call | Unit | N/A |
| 12 | MCP `tortoise_onboarding_github_status` → hosted API | `mcp_server.py` | `hosted_api.py` (same process) | In-process call | Unit | N/A |
| 13 | MCP `tortoise_onboarding_github_index` → hosted API | `mcp_server.py` | `hosted_api.py` (same process) | In-process call | Unit | N/A |
| 14 | All endpoints → Audit log | `hosted_api.py` | PostgreSQL `audit_events` | Async thread pool | Unit | Audit event missing on error paths |

---

## Verification Plan

### Unit Tests

```bash
# Encryption layer
python -m pytest tests/test_crypto.py -v

# OAuth connect + callback
python -m pytest tests/test_github_connect.py -v

# Status endpoint
python -m pytest tests/test_github_status.py -v

# Indexer module (with mock httpx)
python -m pytest tests/test_github_indexer.py -v

# Indexing endpoints + job runner
python -m pytest tests/test_github_index_endpoints.py -v

# Full integration (mock GitHub API)
python -m pytest tests/test_github_integration.py -v
```

### Manual Smoke Tests

1. **Real OAuth flow (requires GitHub OAuth App in dev):**
   - Set `GITHUB_CLIENT_ID` + `GITHUB_CLIENT_SECRET` (dev app) + `GITHUB_CALLBACK_URL=http://localhost:8000/v1/onboarding/github/callback`
   - Start hosted API locally
   - `curl -X POST http://localhost:8000/v1/onboarding/github/connect -H "Authorization: Bearer tt_TESTKEY" -d '{"org":"premise-labs"}'`
   - Open returned `auth_url` in browser, authorize
   - Verify redirect lands on welcome page with `?github=connected`
   - `curl http://localhost:8000/v1/onboarding/github/status -H "Authorization: Bearer tt_TESTKEY"` → `{connected: true}`

2. **Real indexing (small org, requires dev OAuth app):**
   - `curl -X POST http://localhost:8000/v1/index/github -H "Authorization: Bearer tt_TESTKEY" -d '{"org":"premise-labs"}'`
   - Poll `GET /v1/index/github/{job_id}` until completed
   - Verify Points created via SDK query

3. **MCP tool verification:**
   - Connect to hosted MCP server with test key
   - Call `tortoise_onboarding_github_connect(org="test")` → returns auth_url
   - Call `tortoise_onboarding_github_status()` → returns status
   - Call `tortoise_onboarding_github_index(org="test")` → returns job_id

---

## Acceptance Criteria

1. **OAuth flow works end-to-end:** connect → browser authorize → callback → token stored → status reports connected
2. **Token is encrypted at rest:** raw token never stored in plaintext on Team node or in logs
3. **CSRF state is single-use + expiring:** state cannot be replayed, expires in 10 min
4. **Indexing creates Points:** issues and PRs from GitHub become `kind="observation"` Points with `source="github"`
5. **Indexing is idempotent:** re-running creates no duplicate Points
6. **Rate limit aware:** indexer respects `X-RateLimit-Remaining`, backs off on 429
7. **All endpoints reject unauthenticated requests** with 401
8. **All endpoints emit audit events**
9. **MCP tool names match AGENT_ONBOARDING.md exactly:** `tortoise_onboarding_github_connect`, `tortoise_onboarding_github_status`, `tortoise_onboarding_github_index`
10. **Onboarding state auto-updates:** `github_connected: true` after callback, `github_indexed: true` after indexing completes
11. **Error paths graceful:** denied OAuth, revoked token, rate limit, network errors — all handled without crashing
12. **PII aware:** token never logged, issue bodies stay within team's private graph

---

## Dependencies on #498 (API Plan)

This plan implements Tasks 4 & 5 from #498 (GitHub OAuth + Indexing endpoints). It inherits:
- Endpoint naming: `/v1/onboarding/github/connect`, `/v1/onboarding/github/callback`, `/v1/onboarding/github/status`, `/v1/index/github`, `/v1/index/github/{job_id}`
- MCP tool naming: `tortoise_onboarding_github_connect`, `tortoise_onboarding_github_status`, `tortoise_onboarding_github_index`
- Auth pattern: Bearer `tt_` via `get_current_team` (except callback which uses CSRF state)
- Onboarding state: stored on FalkorDB Team node, auto-updated by endpoints
- Audit pattern: `_async_audit` calls on all endpoints

**If #498 changes:** grep-replace endpoint paths and MCP tool names in this plan's Task 2-5. The architecture (encrypted token on Team node, asyncio background indexing, Fernet encryption) is independent of endpoint naming.

---

## Rejected Alternatives

| Alternative | Why rejected |
|-------------|-------------|
| **PAT-based auth (no OAuth)** | Worse UX — user must create a PAT in GitHub settings, copy-paste it. OAuth is the standard flow users expect. PAT also can't be scoped to `repo` only without user creating a fine-grained token. |
| **`gh` CLI for indexing (reuse github.py connector)** | `gh` binary is NOT available in the Fly.io Docker container. The hosted API runs as a Python process, not a shell environment. Direct REST API access is required. |
| **PyGithub library** | Adds a heavy dependency for what amounts to 4 REST endpoints (`/authorize` isn't even REST — it's a browser redirect). Plain `httpx` is sufficient and already in the stack. |
| **Celery/Redis for indexing queue** | Overengineering for v1 (1-10 concurrent jobs). `asyncio.create_task` + in-memory dict follows the same pattern as the dreaming queue in `mcp_server.py`. |
| **Store tokens in Supabase** | `hosted_api.py` doesn't talk to Supabase today. `get_current_team` queries FalkorDB registry graph. Adding Supabase as a dependency for one column introduces a new failure domain — consistent with #498's rejection of Supabase for onboarding state. |
| **Store tokens in env vars (per #324 pattern)** | Env vars are global per deployment. GitHub tokens are per-team. Doesn't scale beyond 1 team. |
| **No encryption (plaintext in FalkorDB)** | FalkorDB has no built-in field-level encryption. If the DB is compromised, all GitHub tokens are exposed. Fernet encryption adds a defense-in-depth layer — attacker needs both the DB AND `TORTOISE_ENCRYPTION_KEY`. |
| **Separate Fly.io Machines for indexing** | Adds infrastructure complexity (separate app, inter-service auth, deployment coordination). The indexing workload is bursty and low-CPU (HTTP API calls + graph writes) — it can run in-process without impacting request latency (asyncio is non-blocking). |

