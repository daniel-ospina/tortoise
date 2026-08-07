# Agent Prompt Deployment + E2E Integration Gate — Implementation Plan

> **Issue:** #502 (child of epic #235 — Hosted Onboarding Journey)
> **Complexity:** STANDARD (Architecture, UX)
> **Role:** Terminal integration gate — ties #495–#501 together with E2E tests and canonical prompt deployment.
> **Dependencies (plan):** #495 (DONE — AGENT_ONBOARDING.md), #496 (DONE — question set), #497 (DONE — welcome page plan), #498 (DONE — API plan), #500 (DONE — demo + MCP tools plan), #499 (PLANNED — GitHub tools), #501 (PLANNED — analytics)
> **Dependencies (execution — MUST be IMPLEMENTED before #502 runs):** #498 (API endpoints), #500 (demo + state tools), #499 (GitHub OAuth + indexing), #497 (welcome page v2), #501 (analytics events)
> **Plan author:** issue-scoping v5.1 double diamond | **Date:** 2026-08-08
> **Inputs:** `AGENT_ONBOARDING.md` (#495), `02-question-set.md` (#496), `03-welcome-page-plan.md` (#497), `04-api-plan.md` (#498), `07-demo-mcp-plan.md` (#500), epic plan `03-plan.md` Tasks 13 + 16 + E2E cases

---

## Confirmed Problem Definition

The hosted onboarding flow (#235) spans five implementation issues (#497–#501) covering: welcome page UI, API endpoints (register, GitHub OAuth, demo graph, state tracking, session recording, team creation, analytics), MCP tool wiring, and funnel analytics. Each issue is verified in isolation. **No artifact proves they work together.**

The three gaps this issue closes:

1. **The canonical onboarding prompt** (`AGENT_ONBOARDING.md`) is committed to the repo but not deployed to a stable URL. Issue #497's welcome page hardcodes a JS fallback `// TODO(#502): replace with fetch from deployed URL`. Claude Code hook `CLAUDE.tortoise.md` has no onboarding entry point. Until the prompt is live, new users must copy raw markdown from a GitHub file.

2. **No cross-surface integration test exists.** Each issue has its own unit/integration tests, but no test proves the complete journey: signup → welcome page → copy artifact → paste into agent → yes/no flow → all 6 questions → memory digest. The epic plan defines 8 E2E journeys (Section "Journey Test Map") that no test file maps to.

3. **The funnel is unverified.** Analytics events from #501 (signup, key_provisioned, onboarding_question_answered, onboarding_complete) are defined but not cross-checked against an actual end-to-end run. Without a funnel gate, there's no proof the onboarding completion event fires.

**Problem statement:** "Does the whole onboarding journey work, soup to nuts, for all 8 journeys, with the deployed canonical prompt, in under 5 minutes, with analytics tracking the funnel?"

---

## Prompt Deployment — Stable URL Decision

### Recommendation: Premise Labs Pages — `premise-labs/onboarding-prompt.md`

**Chosen approach:** Serve `AGENT_ONBOARDING.md` as a static file via Cloudflare Wrangler Pages at `https://tortoise.premiselabs.co/onboarding-prompt.md`.

**Rationale:**
| Option | Pros | Cons |
|--------|------|------|
| **A: static file via Pages** | ✅ Zero backend dependency. ✅ Updates = push to `premise-labs/` + deploy (no API redeploy). ✅ Same domain as welcome page (`tortoise.premiselabs.co`). ✅ Survives API outages. | Versioning is git-based (no `?v=2` parameter). |
| B: `GET /v1/onboarding/prompt` via hosted API | Dynamic personalization possible later. | ❌ API must be deployed before prompt is accessible. ❌ Cold start adds latency. ❌ Unnecessary complexity for a static markdown file. |
| C: GitHub raw URL | Zero deployment. | ❌ Tied to GitHub availability. ❌ URL changes on branch rename. ❌ Raw URLs embeds GitHub's CDN wrapper. |

**Decision:** Option A. The prompt is static markdown. Serving it from the same Pages project as the welcome page keeps the deployment surface simple and decouples prompt updates from API releases.

### Implementation

1. **Copy canonical prompt:** `tortoise/onboarding/AGENT_ONBOARDING.md` → `premise-labs/onboarding-prompt.md` (symlink or periodic sync script — prefer symlink if Cloudflare Pages resolves them; otherwise a copy with a CI check that they don't drift).

2. **Deploy:** `npx wrangler pages deploy premise-labs --project-name=premise-labs` (same command as #497 welcome page). The prompt is served at `https://tortoise.premiselabs.co/onboarding-prompt.md`.

3. **Welcome page integration:** Replace the JS hardcoded fallback in `premise-labs/welcome.html` with a `fetch()` from the deployed URL:
   ```javascript
   // Block B — Onboarding Prompt
   fetch('https://tortoise.premiselabs.co/onboarding-prompt.md')
     .then(r => r.text())
     .then(md => { /* render in snippet area */ })
     .catch(() => { /* fallback to hardcoded content */ });
   ```

4. **Claude Code hooks update:** Add onboarding entry point to `tortoise/claude-hooks/CLAUDE.tortoise.md` and `tortoise/claude-hooks/session-start.sh`:
   - `CLAUDE.tortoise.md`: Add a "## First-time setup" section linking to the prompt URL.
   - `session-start.sh`: On first run (no `.tortoise_initialized` marker), print the prompt URL so the user knows to paste it.

### Single Source of Truth Contract

`tortoise/onboarding/AGENT_ONBOARDING.md` remains the canonical edit location. The Pages copy is a deployment artifact — not an independent source. A CI check (or pre-commit hook) verifies they match. The AGENT_ONBOARDING.md header already declares this policy.

---

## E2E Test Mapping — 8 Epic Journeys → Test Files

The epic plan defines 8 journeys + 5 failure modes. These map to three test categories:

| Category | Tool | What it covers | Runs where |
|----------|------|----------------|------------|
| **Playwright E2E** | `tests/e2e/test_welcome_onboarding.py` | Welcome page states 1–4, copy buttons, harness tabs, API key display, prompt fetch | CI (requires Supabase test user) |
| **API Integration** | `tests/test_onboarding_integration.py` | API endpoints: register → state → demo → session recording → completion. Tool visibility in MCP `tools/list`. | CI (requires FalkorDB + test API key) |
| **Manual Smoke** | `docs/epics/2026-08-07-hosted-onboarding-235/artifacts/10-e2e-test-results.md` | Agent paste flow (not automatable), timing verification, UX feel | Manual (pre-release) |

### E2E-1: New user signs up and receives API key
| Step | Epic step | Test file | Test name |
|------|-----------|-----------|-----------|
| 1 | Supabase session created, redirect to `/welcome` | `test_welcome_onboarding.py` | `test_e2e_signup_to_key` |
| 2 | Key displayed with `tt_` prefix within 10s | `test_welcome_onboarding.py` | `test_welcome_polling_success` |
| 3 | Click copies to clipboard | `test_welcome_onboarding.py` | `test_copy_api_key` |
| 4 | MCP config + onboarding prompt visible | `test_welcome_onboarding.py` | `test_one_artifact_displayed` |

**Playwright test — `test_welcome_onboarding.py`:**
```python
@pytest.mark.e2e
def test_e2e_signup_to_key(page: Page):
    """Journey: signup → welcome → API key displayed."""
    # Requires pre-created test user with known credentials
    page.goto(f"{BASE_URL}/signup.html")
    page.fill("#email", TEST_USER_EMAIL)
    page.fill("#password", TEST_USER_PASSWORD)
    page.click("#btn-signup")
    # Wait for redirect to welcome + polling
    page.wait_for_url(f"{BASE_URL}/welcome.html*", timeout=15000)
    page.wait_for_selector("#api-key", timeout=30000)
    key = page.text_content("#api-key")
    assert key.startswith("tt_"), f"Expected tt_ prefix, got: {key}"
```

### E2E-2: Paste one-artifact → agent connects
| Step | Epic step | Test file | Test name |
|------|-----------|-----------|-----------|
| 1 | Clipboard contains MCP config + prompt | `test_welcome_onboarding.py` | `test_one_artifact_clipboard` |
| 2 | Agent connects to MCP, lists tools | Manual smoke | (manual) |
| 3 | Agent asks first yes/no question | Manual smoke | (manual) |

**Playwright test + Manual gate:** Clipboard content is verifiable via Playwright (`navigator.clipboard.readText()` with permission). The actual agent paste is manual — Claude Code/Pi session verification with screenshot evidence.

### E2E-3: GitHub connected (yes/no flow)
| Step | Epic step | Test file | Test name |
|------|-----------|-----------|-----------|
| 1 | OAuth flow initiated | `test_onboarding_integration.py` | `test_github_oauth_flow` (mock) |
| 2 | `github_connected: true`, repos listed | `test_onboarding_integration.py` | `test_github_connect_state` |

### E2E-4: Indexing → first memory written
| Step | Epic step | Test file | Test name |
|------|-----------|-----------|-----------|
| 1 | Background indexing starts, job ID returned | `test_onboarding_integration.py` | `test_indexing_job_created` |
| 2 | At least one Point created from GitHub | `test_onboarding_integration.py` | `test_indexing_produces_points` |
| 3 | `tortoise_query(kind="observation")` returns results | `test_onboarding_integration.py` | `test_indexed_content_queryable` |

### E2E-5: Demo graph created
| Step | Epic step | Test file | Test name |
|------|-----------|-----------|-----------|
| 1 | 12 Points + 3 Operators created | `test_onboarding_integration.py` | `test_demo_graph_creation` |
| 2 | `tortoise_summarize_structure()` returns counts | `test_onboarding_integration.py` | `test_demo_structure_summary` |
| 3 | Agent describes graph structure | Manual smoke | (manual) |

### E2E-6: Session recording enabled
| Step | Epic step | Test file | Test name |
|------|-----------|-----------|-----------|
| 1 | `session_recording: true` in state | `test_onboarding_integration.py` | `test_session_recording_toggle` |
| 2 | Agent confirms | Manual smoke | (manual) |

### E2E-7: Onboarding complete → memory digest
| Step | Epic step | Test file | Test name |
|------|-----------|-----------|-----------|
| 1 | Agent calls `tortoise_context` | `test_onboarding_integration.py` | `test_context_after_onboarding` |
| 2 | Digest displayed, elapsed < 5 min | `test_onboarding_integration.py` | `test_onboarding_complete_digest` |
| 3 | Funnel event `onboarding_complete` tracked | `test_onboarding_integration.py` | `test_onboarding_complete_analytics` |

### E2E-8: User says "no" to everything
| Step | Epic step | Test file | Test name |
|------|-----------|-----------|-----------|
| 1 | Agent verifies connection via `tortoise_health` | `test_onboarding_integration.py` | `test_all_no_minimal_setup` |
| 2 | Agent shows minimal success message | Manual smoke | (manual) |
| 3 | All steps `skipped`, tools accessible | `test_onboarding_integration.py` | `test_skipped_state_all_accessible` |

### Failure Modes
| Failure | Epic description | Test file | Test name |
|---------|-----------------|-----------|-----------|
| GitHub OAuth fails | Agent reports failure, skips, continues | `test_onboarding_integration.py` | `test_github_oauth_failure_graceful` |
| Indexing timeout | Agent reports background job, continues | `test_onboarding_integration.py` | `test_indexing_timeout_graceful` |
| API key not provisioned (welcome timeout) | "Taking longer than expected" message | `test_welcome_onboarding.py` | `test_provisioning_timeout_message` |
| Agent hallucinates onboarding flow | Structured prompt prevents chaos; MCP idempotent | Manual smoke | (manual — 3-run stability check) |
| MCP connection fails (wrong key) | "Can't connect — check your API key" | `test_onboarding_integration.py` | `test_mcp_connection_failure` |

---

## Funnel Verification — Analytics Cross-Check

The analytics schema (#501) defines 6 events. The E2E suite MUST verify these events fire in the correct order during a full run:

| Event | Triggers when | Verified by |
|-------|--------------|-------------|
| `signup_completed` | Supabase user created | `test_e2e_signup_to_key` |
| `key_provisioned` | API key assigned (polling completes) | `test_welcome_polling_success` |
| `onboarding_started` | First `tortoise_health` in prompt | `test_context_after_onboarding` |
| `onboarding_question_answered` | Each yes/no response (6 calls) | `test_onboarding_complete_analytics` |
| `onboarding_step_completed` | Each MCP tool succeeds | `test_onboarding_complete_analytics` |
| `onboarding_complete` | All questions done + `onboarding_state.completed_at` set | `test_onboarding_complete_analytics` |

**Implementation:**
```python
@pytest.mark.e2e
def test_onboarding_funnel_events(api_client, test_team, analytics_db):
    """Verify the full funnel — all 6 events fire in order."""
    # Run the full onboarding flow via API calls (simulating agent prompt)
    # 1. Health check → onboarding_started
    # 2. Q1: GitHub connect (no) → question_answered
    # 3. Q3: Session recording (yes) → step_completed
    # 4. Q4: Demo graph (yes) → step_completed
    # 5. Q5: Team (no) → question_answered
    # 6. Q6: Completion → onboarding_complete

    events = analytics_db.query(
        "SELECT event_name, properties FROM analytics_events "
        "WHERE team_id = $1 ORDER BY created_at",
        test_team.id
    )

    event_names = [e["event_name"] for e in events]
    assert "onboarding_started" in event_names
    assert "onboarding_question_answered" in event_names
    assert "onboarding_step_completed" in event_names
    assert "onboarding_complete" in event_names

    # Verify ordering: started < questions < steps < complete
    started_idx = event_names.index("onboarding_started")
    complete_idx = event_names.index("onboarding_complete")
    assert started_idx < complete_idx, "onboarding_started must fire before onboarding_complete"

    # Verify elapsed time in onboarding_complete event
    complete_event = events[complete_idx]
    assert complete_event["properties"]["elapsed_time_s"] < 300  # < 5 min
    assert complete_event["properties"]["questions_answered"] >= 2
```

---

## Implementation Tasks

### Task 1: Deploy Canonical Prompt to Stable URL

**Intent:** Copy `tortoise/onboarding/AGENT_ONBOARDING.md` → `premise-labs/onboarding-prompt.md` and deploy to `https://tortoise.premiselabs.co/onboarding-prompt.md`. Add a drift-check script so the two files don't diverge. This is the Single Source of Truth deployment — all consumers (welcome page, agent hooks, docs) reference this URL.

**Acceptance:**
- `https://tortoise.premiselabs.co/onboarding-prompt.md` returns the prompt as `text/markdown` with HTTP 200
- Content matches `tortoise/onboarding/AGENT_ONBOARDING.md` (byte-identical or semantically equivalent)
- A CI/pre-commit check verifies the two files are in sync
- The welcome page `fetch()` hits this URL (replacing the JS hardcoded fallback)

**Files:**
- Copy: `tortoise/onboarding/AGENT_ONBOARDING.md` → `premise-labs/onboarding-prompt.md`
- Modify: `premise-labs/welcome.html` — replace `// TODO(#502)` fallback with `fetch()` from deployed URL
- Create: `scripts/check-prompt-sync.sh` — drift checker (compares the two files, exits non-zero if they differ)

**Steps:**
1. Copy `AGENT_ONBOARDING.md` to `premise-labs/onboarding-prompt.md`
2. Write `scripts/check-prompt-sync.sh`: `diff tortoise/onboarding/AGENT_ONBOARDING.md premise-labs/onboarding-prompt.md`
3. Update `premise-labs/welcome.html` Block B: replace hardcoded JS string with `fetch('onboarding-prompt.md')` (relative URL — same Pages origin)
4. Deploy: `npx wrangler pages deploy premise-labs --project-name=premise-labs`
5. Verify: `curl -s https://tortoise.premiselabs.co/onboarding-prompt.md | head -5` returns the prompt header

### Task 2: Update Claude Code Hooks for Onboarding Entry Point

**Intent:** `tortoise/claude-hooks/CLAUDE.tortoise.md` and `session-start.sh` currently assume the user already has Tortoise configured. Add a "First-time setup" section that points new users to the deployed prompt URL. The session-start hook detects whether Tortoise is initialized and, if not, prints a pointer to the onboarding URL.

**Acceptance:**
- `CLAUDE.tortoise.md` has a `## First-time setup` section with: (a) link to deployed prompt URL, (b) instructions to paste prompt into agent, (c) note that onboarding takes <5 min.
- `session-start.sh` prints the onboarding prompt URL when no `.tortoise_initialized` marker file exists (non-blocking — exits 0).
- Existing behavior (memory digest injection via `tortoise context`) is unchanged.

**Files:**
- Modify: `tortoise/claude-hooks/CLAUDE.tortoise.md` — add `## First-time setup` section
- Modify: `tortoise/claude-hooks/session-start.sh` — add first-run onboarding pointer

**Steps:**
1. Add to `CLAUDE.tortoise.md` (after the intro paragraph):
   ```markdown
   ## First-time setup
   If this is your first session with Tortoise, paste this onboarding prompt
   into your agent chat to set up your memory in under 5 minutes:

   [Onboarding prompt](https://tortoise.premiselabs.co/onboarding-prompt.md)

   The prompt will ask you ≤6 yes/no questions — answer them, and Tortoise
   will be ready with a demo graph and persistent memory.
   ```
2. Add to `session-start.sh` (before the existing context injection):
   ```bash
   # First-run: point user to onboarding prompt.
   TORTOISE_INIT_MARKER="${HOME}/.tortoise_initialized"
   if [ ! -f "$TORTOISE_INIT_MARKER" ]; then
     echo "---"
     echo "🐢 Tortoise: First time? Set up your memory in <5 min:"
     echo "   https://tortoise.premiselabs.co/onboarding-prompt.md"
     echo "   (Paste this into your agent chat, then restart your session.)"
     echo "---"
   fi
   ```
3. Verify: run `session-start.sh` in a clean environment → onboarding pointer printed. Run again with `.tortoise_initialized` present → no pointer, normal digest.

### Task 3: E2E Playwright Tests — Welcome Page

**Intent:** Implement Playwright tests for the welcome page surface covering E2E-1 and E2E-2 (the browser-observable parts). Tests verify all 5 welcome page states, copy buttons, harness tabs, and prompt fetch.

**Acceptance:**
- `tests/e2e/test_welcome_onboarding.py` passes with a real Supabase test user
- Tests cover: state transitions (pre-signup → polling → artifact → post-copy), harness tab switching, copy-config with API key interpolation, copy-prompt with fetched content
- Uses Playwright patterns from `test-e2e` skill (smoke + full depth)
- Provisional tests can be written now against mock Supabase responses (the welcome page states are defined in #497 plan)

**Files:**
- Create: `tests/e2e/test_welcome_onboarding.py`
- Create: `tests/e2e/conftest.py` — Playwright fixtures (test user, Supabase session, base URL)
- Create: `tests/e2e/__init__.py`

**Test inventory (12 tests):**

| # | Test | Tags | Epic step |
|---|------|------|-----------|
| 1 | `test_welcome_no_session_shows_signup_link` | @smoke | E2E-1 step 1 (guard) |
| 2 | `test_welcome_polling_spinner` | @e2e | E2E-1 step 1 |
| 3 | `test_welcome_polling_success` | @e2e | E2E-1 step 2 |
| 4 | `test_welcome_polling_timeout` | @e2e | Failure: provisioning timeout |
| 5 | `test_welcome_shows_api_key` | @smoke | E2E-1 step 2 |
| 6 | `test_welcome_shows_team_info` | @e2e | E2E-1 step 4 |
| 7 | `test_one_artifact_displayed` | @smoke | E2E-1 step 4 |
| 8 | `test_copy_config_button` | @e2e | E2E-2 step 1 |
| 9 | `test_copy_prompt_button` | @e2e | E2E-2 step 1 |
| 10 | `test_harness_tabs_switch` | @e2e | E2E-2 (per-harness config) |
| 11 | `test_prompt_fetched_from_deployed_url` | @e2e | Task 1 verification |
| 12 | `test_post_copy_paste_instructions` | @e2e | State 4 |

**Steps:**
1. Create `tests/e2e/__init__.py` and `tests/e2e/conftest.py` with Playwright fixtures
2. Write provisional tests with mock Supabase (can write now before #497 implements)
3. Tag smoke tests with `@pytest.mark.smoke` for CI fast path
4. Tag full tests with `@pytest.mark.e2e` for full pipeline

### Task 4: API Integration Tests — Full Onboarding Flow

**Intent:** Implement integration tests that simulate the agent prompt's yes/no flow through the API. Covers E2E-3 through E2E-8 plus failure modes. These are the backend equivalents of the agent prompt's Q0–Q6 — they call the same endpoints the MCP tools wrap.

**Acceptance:**
- `tests/test_onboarding_integration.py` passes with a real FalkorDB + test API key
- Tests cover all 6 questions (Q0 health → Q1 GitHub → Q2 index → Q3 recording → Q4 demo → Q5 team → Q6 completion)
- Both "all yes" and "all no" paths verified
- Analytics events verified (funnel completeness)
- Failure modes verified (OAuth failure, indexing timeout, bad key)
- Mock GitHub API for OAuth/index tests (no real GitHub dependency in CI)

**Files:**
- Create: `tests/test_onboarding_integration.py`
- Modify: `tests/conftest.py` — add fixtures for test team + API key

**Test inventory (18 tests):**

| # | Test | Tags | Epic step |
|---|------|------|-----------|
| 1 | `test_health_check` | @integration | Q0 |
| 2 | `test_github_connect_returns_auth_url` | @integration | E2E-3 step 1 |
| 3 | `test_github_connect_state` | @integration | E2E-3 step 2 |
| 4 | `test_github_oauth_failure_graceful` | @integration | Failure: OAuth fail |
| 5 | `test_indexing_job_created` | @integration | E2E-4 step 1 |
| 6 | `test_indexing_produces_points` | @integration | E2E-4 step 2 |
| 7 | `test_indexed_content_queryable` | @integration | E2E-4 step 3 |
| 8 | `test_indexing_timeout_graceful` | @integration | Failure: indexing timeout |
| 9 | `test_demo_graph_creation` | @integration | E2E-5 step 1 |
| 10 | `test_demo_structure_summary` | @integration | E2E-5 step 2 |
| 11 | `test_session_recording_toggle` | @integration | E2E-6 step 1 |
| 12 | `test_context_after_onboarding` | @integration | E2E-7 step 1 |
| 13 | `test_onboarding_complete_digest` | @integration | E2E-7 step 2 |
| 14 | `test_onboarding_complete_analytics` | @integration | E2E-7 step 3 |
| 15 | `test_all_no_minimal_setup` | @integration | E2E-8 step 1 |
| 16 | `test_skipped_state_all_accessible` | @integration | E2E-8 step 3 |
| 17 | `test_mcp_connection_failure` | @integration | Failure: bad key |
| 18 | `test_onboarding_funnel_events` | @integration | Funnel verification |

**Steps:**
1. Add test fixtures to `tests/conftest.py`: `test_team_api_key`, `mock_github_api`, `analytics_db`
2. Write Q0–Q6 flow test (the golden path — all yes)
3. Write all-no path test (the minimal path)
4. Write failure mode tests with mocks
5. Write funnel verification test (all 6 analytics events)
6. Write timing assertion: full flow completes in < 5s (API calls, not manual agent interaction)

### Task 5: Manual Smoke Test Protocol + Results Doc

**Intent:** Document the manual smoke test procedure for the un-automatable parts: pasting into a real agent, verifying agent behavior, timing the full flow. This doc is executed pre-release and updated with results.

**Acceptance:**
- `docs/epics/2026-08-07-hosted-onboarding-235/artifacts/10-e2e-test-results.md` exists with: (a) test protocol, (b) per-harness results (Claude Code, Codex, Cursor, Pi), (c) timing measurements, (d) pass/fail verdict
- At least one harness (Claude Code or Pi) completes the full flow end-to-end

**Files:**
- Create: `docs/epics/2026-08-07-hosted-onboarding-235/artifacts/10-e2e-test-results.md`

**Protocol sections:**
1. **Setup:** Provision a fresh test team, note API key
2. **Welcome page:** Open `https://tortoise.premiselabs.co/welcome.html`, verify artifact display
3. **Copy + Paste:** Copy Claude Code config → paste into terminal → copy prompt → paste into agent
4. **Q0–Q6 flow:** Agent asks all 6 questions. Answer yes to all.
5. **Verification:** Agent displays memory digest. Demo graph has 12 points + 3 operators.
6. **Timing:** Record total elapsed (target < 5 min)
7. **All-no rerun:** New session, answer no to everything. Verify minimal success.
8. **Analytics check:** Query `analytics_events` table for this team — verify funnel events

**Steps:**
1. Create the results doc with the protocol
2. After #497–#501 ship, execute the protocol
3. Record results per harness (screenshots, timings)
4. File bugs for any failures found

### Task 6: Test Infrastructure — Fixtures, Mocks, CI Wiring

**Intent:** Set up the shared test infrastructure both E2E test files need: Supabase test user fixtures, FalkorDB test isolation, GitHub API mocks, analytics table access. Wire into CI so tests run on PRs.

**Acceptance:**
- `tests/e2e/conftest.py` provides Playwright fixtures (authenticated page, base URL)
- `tests/conftest.py` provides API fixtures (test team with key, mock GitHub transport, analytics table)
- CI workflow runs `@smoke` E2E tests on PRs and `@e2e` tests on merge to main
- `@integration` API tests run on every PR (no Playwright needed)

**Files:**
- Modify: `tests/conftest.py` — add `test_team_with_key`, `mock_github_api`, `analytics_events_table`
- Create: `tests/e2e/conftest.py` — Playwright-specific fixtures
- Modify: `.github/workflows/test.yml` — add E2E + integration test jobs

**Steps:**
1. Add `test_team_with_key` fixture: provisions a team via `/internal/provision` or `/v1/register`, returns API key + team_id. Scoped to `function` for isolation.
2. Add `mock_github_api` fixture: monkeypatches `httpx.AsyncClient` for GitHub API calls with realistic responses (repos list, issues, PRs, rate limits, OAuth errors).
3. Add `analytics_events_table` fixture: creates temp analytics table or truncates existing one.
4. Create `tests/e2e/conftest.py` with:
   ```python
   @pytest.fixture
   def authenticated_page(browser: Browser, test_user_credentials):
       """Returns a Playwright page logged into Supabase."""
       ...
   ```
5. Update CI workflow: add job `e2e-smoke` (runs `@smoke` on PR), job `e2e-full` (runs `@e2e` on merge to main), job `integration` (runs `@integration` on every PR).
6. Env vars needed in CI: `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `TORTOISE_HOSTED_API_URL`, `TEST_USER_EMAIL`, `TEST_USER_PASSWORD`, `ANALYTICS_SUPABASE_URL`.

---

## Timing Target

The epic plan's acceptance criterion: **full onboarding completes in < 5 minutes.**

Breakdown:
| Phase | Max time | Measured by |
|-------|----------|-------------|
| Signup → API key provisioned | 30s | `test_welcome_polling_success` |
| Welcome page load + artifact display | 3s | `test_one_artifact_displayed` |
| Copy + paste into agent | 15s (manual) | Smoke test timing |
| Agent Q0 (health check) → Q6 (completion) | 3 min | `test_onboarding_complete_digest` |
| Demo graph creation | 5s | `test_demo_graph_creation` |
| Indexing (background — non-blocking) | N/A | `test_indexing_job_created` |
| **Total target** | **< 5 min** | `test_onboarding_complete_analytics` |

The indexing job is intentionally non-blocking — the agent prompt continues while indexing runs. This keeps the user-perceived onboarding under 5 minutes even for large GitHub orgs.

---

## Dependencies for Execution

### MUST be IMPLEMENTED before #502 execution:

| Issue | What | Why blocking |
|-------|------|--------------|
| **#498** (API endpoints) | `/v1/register`, `/v1/demo`, `/v1/onboarding/*`, `/v1/index/*` | Integration tests need real endpoints to call. Without #498's endpoints, the API tests are purely mock-based and don't verify real behavior. |
| **#500** (Demo + State MCP tools) | `tortoise_onboarding_demo_create`, `tortoise_onboarding_state`, `tortoise_onboarding_session_recording` | These three tools are called in Q3, Q4, Q6. Without them, 50% of the yes/no flow can't be tested. |
| **#499** (GitHub MCP tools) | `tortoise_onboarding_github_connect`, `tortoise_onboarding_github_status`, `tortoise_onboarding_github_index` | E2E-3 and E2E-4 test the GitHub integration. Without #499, these tests must use mocks — acceptable for provisional tests, but final verification needs real or mock-verified endpoints. |
| **#497** (Welcome page v2) | `premise-labs/welcome.html` with artifact display, copy buttons, harness tabs | Playwright E2E tests target the welcome page surface. Without #497, the Playwright tests have nothing to test. |
| **#501** (Analytics events) | `analytics_events` table, event emission on endpoints | Funnel verification (`test_onboarding_funnel_events`) reads from the analytics table. Without #501, this test can't run. |

### Can be written NOW (against mocks / pre-existing contracts):

| What | Why possible now |
|------|-----------------|
| All API integration tests (`test_onboarding_integration.py`) | The endpoint contracts are defined in #498 plan. Tests can be written against mock HTTP responses that match the contract. Flip to real endpoints when #498 ships. |
| Playwright welcome page tests (`test_welcome_onboarding.py`) | The welcome page states are defined in #497 plan. Tests can be written against a local mock page or the existing `welcome.html` with mocked Supabase responses. |
| Prompt deployment (Task 1) | `AGENT_ONBOARDING.md` exists and is finalized (#495, #496). The copy + deploy is mechanical — no code dependencies. |
| Claude hooks update (Task 2) | `CLAUDE.tortoise.md` and `session-start.sh` exist. Adding a first-run pointer is a standalone change. |
| Manual smoke test doc (Task 5) | The protocol is defined. Write the doc now; execute later when all implementations ship. |

### Execution order:

```
Phase A (NOW — write provisional tests, deploy prompt):
  Task 1: Deploy canonical prompt to Pages
  Task 2: Update Claude hooks
  Task 3: Write Playwright E2E tests (provisional — mock Supabase)
  Task 4: Write API integration tests (provisional — mock endpoints)
  Task 5: Write manual smoke test doc
  Task 6: Set up test infrastructure + CI

Phase B (AFTER #497–#501 ship):
  Re-run Task 3 + Task 4 against real endpoints
  Execute Task 5 manual smoke test
  File bugs for any failures
  Mark #502 complete when all 8 E2E journeys pass
```

---

## Verification Plan

### Pre-Deploy Gates

| Gate | Command | Expected |
|------|---------|----------|
| Prompt drift check | `bash scripts/check-prompt-sync.sh` | Exit 0 (files match) |
| Prompt URL live | `curl -s https://tortoise.premiselabs.co/onboarding-prompt.md \| head -5` | Returns `# Tortoise Onboarding — Set up...` |
| Welcome page fetches prompt | Playwright: `test_prompt_fetched_from_deployed_url` | Passes |
| Playwright E2E smoke | `python -m pytest tests/e2e/ -v -m smoke` | All pass |
| API integration | `python -m pytest tests/test_onboarding_integration.py -v` | All pass |
| Hook syntax | `bash -n tortoise/claude-hooks/session-start.sh` | No syntax errors |

### Post-Deploy Verification

1. **Golden path smoke:** Fresh test team → welcome page → copy artifact → paste into agent → all yes → verify memory digest → check analytics events → < 5 min
2. **All-no path:** Fresh test team → all no → verify minimal success → analytics complete
3. **Multi-harness:** Claude Code AND Pi complete the flow (at minimum)
4. **Analytics funnel:** Query `analytics_events` → all 6 events fire in order → `elapsed_time_s` < 300
5. **Prompt sync:** `scripts/check-prompt-sync.sh` passes in CI

---

## Rejected Alternatives

| Alternative | Why rejected |
|-------------|-------------|
| Skip E2E tests, rely on per-issue unit tests | Integration failures between surfaces (welcome page ↔ API ↔ prompt) are invisible. The epic explicitly calls this the "integration gate" for a reason. |
| Deploy prompt via API endpoint (Option B) | Adds backend dependency for a static file. Pages CDN is simpler and decouples prompt updates from API releases. |
| Write only Playwright, skip API integration tests | The agent prompt flow (Q0–Q6) can't be Playwright-tested — it's an MCP conversation. API integration tests are the only automatable way to verify the backend flow. |
| Single monolithic test file | Two surfaces (browser, API) need different fixtures and runtimes. Separation is cleaner and allows parallel CI execution. |
| Deploy prompt as GitHub raw URL (Option C) | Brittle URL, GitHub availability dependency, wrong Content-Type. Pages is the same deployment surface as the welcome page. |
