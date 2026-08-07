<!-- research-path: docs/epics/2026-08-07-hosted-onboarding-235/01-research-brief.md -->

# Hosted Onboarding Journey — Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.
> **Epic-level plan.** After plan-review, decompose into child issues via `epic-decompose`.

**Goal:** Build the hosted Tortoise onboarding journey so a new user goes from "I signed up" to "my agent remembers decisions and GitHub/session data is flowing into memory" — triggered by one paste-able artifact, guided by ≤6 yes/no questions, in under 5 minutes.

**Team:** organisation-design-team
**Role:** (unset)

**Architecture:** The onboarding flow spans four surfaces: (1) a dynamic welcome page that presents the one-artifact after signup, (2) an agent-side onboarding prompt (markdown block) that drives the yes/no conversation, (3) new MCP tools and REST endpoints for GitHub connect, demo graph creation, and onboarding state tracking, and (4) funnel analytics events. The welcome page (premise-labs/) talks to Supabase for auth/key delivery; the agent prompt talks to the hosted API (tortoise/hosted_api.py) via MCP tools; the hosted API manages onboarding state on the Team record. Phase 1 produces design artifacts only (no code); Phase 2 implements all build components per the owner override removing the traction gate.

### Pattern Research

**Bucket 1 — Canonical approaches (the known-good foundation):**
- **Self-hosted `tortoise onboard`** (`__main__.py:_cmd_onboard`): 5-step chain (init → index → demo → doctor) with banners, idempotency, and auto-detection. Maps cleanly to hosted: Connect → Index → Demo → Verify.
- **MCP config paste:** Industry-standard pattern — `claude mcp add` / `codex mcp add` / `.mcp.json`. The current `welcome.html` already presents MCP config copy-paste. No guided flow beyond this exists in any MCP server in the wild.
- **SessionStart hooks:** `tortoise/claude-hooks/session-start.sh` + `CLAUDE.tortoise.md` — proven pattern for injecting memory digests at session start. The onboarding prompt can follow the same markdown-block deployment pattern.

**Bucket 2 — Competitor variance (how others solve it differently):**
- **Agent Memory (agent-memory.dev):** Install → start server → connect agent → verify status. Simpler (no epistemic graph) but the linear "connect then verify" pattern is proven.
- **Mem0:** Embedding-based; no guided setup. Different paradigm — not comparable.
- **Claude Code native memory:** File-based `.claude/` memory. No guided onboarding — users discover it or don't.

**Bucket 3 — Pitfalls (what the research brief flagged):**
- **A1/A2 (LOW confidence):** Users may not paste both the MCP config AND the onboarding prompt. The one-artifact design must make this failure-visible — if only the config is pasted, the welcome page should make the prompt impossible to miss.
- **A3 (OVERRIDDEN):** The traction gate (≥5 signups/week) is removed by owner directive. Phase 2 builds immediately.
- **A10 (LOW confidence):** One artifact across Pi, Claude Code, Codex, and Cursor may require harness-specific variants. Pi extensions can bundle both; Cursor can use `.cursor/rules/` + `.mcp.json`; Claude Code and Codex require CLI + chat separation. The plan acknowledges this and provides a fallback: the welcome page presents both the config and the prompt as a single block with clear instructions per harness.
- **A13 (LOW confidence):** GitHub issue/PR indexing feasibility. The plan includes a spike task in Phase 1 before committing to full build.

**Library docs preflight:** No new third-party dependencies beyond what's already in the stack (FastAPI, falkordb, redislite, Supabase). GitHub API is external but accessed via REST — no SDK needed for v1.

### Integration Surface Map

| # | Surface | System A | System B | Type | Test Layer | Bug Flags |
|---|---------|----------|----------|------|------------|-----------|
| 1 | Welcome page → Supabase Auth | `premise-labs/welcome.html` | Supabase Auth + `user_teams` table | Browser JS → REST | Playwright E2E (`test-e2e`) | Auth session expiry during polling; `user_teams` row not yet provisioned |
| 2 | Welcome page → API key display | `premise-labs/welcome.html` | Supabase `user_teams.api_key` | JS polling → DB read | Playwright E2E | Key shows "pending" past 30s timeout; key is null |
| 3 | Agent prompt → MCP server | Agent (Claude/Codex/Cursor/Pi) | `tortoise/mcp_server.py` (56 tools) | MCP Streamable HTTP | Manual smoke test (no MCP test harness) + unit tests for new tools | Agent hallucinates tool names; auth fails silently |
| 4 | Agent prompt → Hosted API (new endpoints) | Agent via MCP tools | `tortoise/hosted_api.py` | MCP → FastAPI → FalkorDB | Integration test (`test-integration`) with test tenant | Tenant isolation leak; rate limit on GitHub API |
| 5 | GitHub OAuth → Hosted API | GitHub OAuth App | `tortoise/hosted_api.py` (callback) | OAuth 2.0 redirect → REST | Manual + Playwright for callback flow | CSRF state mismatch; token expiry; app not approved |
| 6 | GitHub indexing → FalkorDB | GitHub REST API | `tortoise/hosted_api.py` → FalkorProjection | REST → Graph DB | Integration test with mock GitHub responses | Rate limit 5000/hr; large repos time out; noise ratio |
| 7 | Demo graph → FalkorDB | `tortoise/hosted_api.py` | FalkorDB (per-tenant graph) | Internal API → Graph DB | Unit test for idempotency | Duplicate points on re-run; cross-tenant leakage |
| 8 | Onboarding state → Team record | `tortoise/hosted_api.py` | FalkorDB team graph OR Supabase `teams` table | Internal state write | Unit test for state transitions | State corruption on concurrent writes; missing default state |
| 9 | Analytics events → Analytics backend | `tortoise/hosted_api.py` + JS | PostHog / custom analytics | HTTP POST events | Manual verify via dashboard | Missing events on error paths; PII in event payloads |
| 10 | CLI `tortoise init --api-key` → Hosted API | `tortoise/__main__.py:_cmd_init` | `tortoise/hosted_api.py` | CLI → REST | Unit test for config write | Plaintext key in `.tortoise`; file permissions |

### Journey Test Map

Maps the 8 E2E cases from `02-scope.md` to specific test scenarios and the tasks that implement them.

#### Journey: New user signs up and receives API key (E2E-1)
1. **Step:** User completes signup (email/OAuth) → **Acceptance:** Supabase session created, redirect to `/welcome` → **Test:** `test_e2e_signup_to_key` (Playwright)
2. **Step:** Welcome page polls `user_teams` for API key → **Acceptance:** Key displayed with `tt_` prefix within 10s → **Test:** `test_welcome_polling_success` (Playwright)
3. **Step:** User copies API key → **Acceptance:** Click copies to clipboard → **Test:** `test_copy_api_key` (Playwright + clipboard permission)
4. **Step:** User sees the one-artifact block → **Acceptance:** MCP config + onboarding prompt visible, per-harness instructions → **Test:** `test_one_artifact_displayed` (Playwright)

#### Journey: Paste one-artifact → agent connects (E2E-2)
1. **Step:** User copies one-artifact block → **Acceptance:** Clipboard contains both MCP config and prompt → **Test:** `test_one_artifact_clipboard` (Playwright + clipboard)
2. **Step:** User pastes into agent → **Acceptance:** Agent connects to MCP server, lists tools → **Test:** Manual smoke test (Claude Code, Codex, Cursor) — not automatable
3. **Step:** Agent begins onboarding prompt → **Acceptance:** Agent asks first yes/no question → **Test:** Manual smoke test

#### Journey: Yes/no flow → GitHub connected (E2E-3)
1. **Step:** User answers "yes" to GitHub connect → **Acceptance:** OAuth flow initiated or PAT accepted → **Test:** `test_github_oauth_flow` (manual OAuth) / `test_github_pat_connect` (integration)
2. **Step:** Authorization succeeds → **Acceptance:** `github_connected: true` in onboarding state, repos listed → **Test:** `test_github_connect_state` (integration with mock GitHub)

#### Journey: Indexing → first memory written (E2E-4)
1. **Step:** User answers "yes" to indexing → **Acceptance:** Background indexing starts, job ID returned → **Test:** `test_indexing_job_created` (integration)
2. **Step:** Indexing completes → **Acceptance:** At least one Point created from GitHub content → **Test:** `test_indexing_produces_points` (integration with mock GitHub API)
3. **Step:** Agent queries for indexed content → **Acceptance:** `tortoise_query(kind="observation")` returns results → **Test:** `test_indexed_content_queryable` (integration)

#### Journey: Demo graph created (E2E-5)
1. **Step:** User answers "yes" to demo graph → **Acceptance:** Curated Points + Operators created in tenant graph → **Test:** `test_demo_graph_creation` (unit)
2. **Step:** Agent calls `tortoise_summarize_structure()` → **Acceptance:** Returns N points, M operators → **Test:** `test_demo_structure_summary` (unit)
3. **Step:** Agent explains demo graph content → **Acceptance:** Agent describes graph structure naturally → **Test:** Manual smoke test

#### Journey: Session recording enabled (E2E-6)
1. **Step:** User answers "yes" to session recording → **Acceptance:** `session_recording: true` in onboarding state → **Test:** `test_session_recording_toggle` (unit)
2. **Step:** Agent confirms → **Acceptance:** Agent shows confirmation message → **Test:** Manual smoke test

#### Journey: Onboarding complete → memory digest (E2E-7)
1. **Step:** All questions answered → **Acceptance:** Agent calls `tortoise_context` / `GET /v1/context` → **Test:** `test_context_after_onboarding` (integration)
2. **Step:** Memory digest displayed → **Acceptance:** Digest shows what Tortoise remembers, elapsed < 5 min → **Test:** `test_onboarding_complete_digest` (integration)
3. **Step:** Funnel event `onboarding_complete` tracked → **Acceptance:** Event in analytics with elapsed time → **Test:** `test_onboarding_complete_analytics` (unit)

#### Journey: User says "no" to everything (E2E-8)
1. **Step:** User answers "no" to all 6 questions → **Acceptance:** Agent verifies connection via `tortoise_health` → **Test:** `test_all_no_minimal_setup` (integration)
2. **Step:** Agent shows minimal success → **Acceptance:** "Tortoise is connected. You can create your first memory..." → **Test:** Manual smoke test
3. **Step:** Onboarding state records all skipped → **Acceptance:** All steps `skipped`, tools still accessible → **Test:** `test_skipped_state_all_accessible` (integration)

#### Failure Modes
- **GitHub OAuth fails (user denies, network error)** → **Expected behavior:** Agent reports failure, skips GitHub step, continues with remaining questions. Onboarding state records `github_connected: false, github_error: "reason"`. → **Test:** `test_github_oauth_failure_graceful` (integration with error mock)
- **Indexing times out (large repo)** → **Expected behavior:** Agent reports "Indexing started in background — you'll see results in your next session." Onboarding continues. → **Test:** `test_indexing_timeout_graceful` (integration with slow mock)
- **API key not provisioned yet (welcome page timeout)** → **Expected behavior:** Welcome page shows "Taking longer than expected — refresh or contact support." Not a dead end. → **Test:** `test_provisioning_timeout_message` (Playwright)
- **Agent hallucinates onboarding flow** → **Expected behavior:** The onboarding prompt is structured enough that even a hallucinating agent produces a recognizable flow. MCP tools are idempotent. → **Test:** Manual smoke test with 3 runs
- **MCP connection fails (wrong key, network)** → **Expected behavior:** `tortoise_health` returns error, agent reports "Can't connect to Tortoise — check your API key." → **Test:** `test_mcp_connection_failure` (integration with bad key)

**Tech Stack:** Python 3.11+ (FastAPI, tortoise SDK), JavaScript (welcome page, browser), Supabase (auth, edge functions), FalkorDB (graph), GitHub REST API (indexing), MCP Streamable HTTP (agent tools), PostHog or custom (analytics)

---

## Phase 1 — Design Tasks

> Phase 1 produces DESIGN ARTIFACTS ONLY. No code ships. No endpoints deployed.
> Each task outputs a document or prototype committed to `docs/epics/2026-08-07-hosted-onboarding-235/`.

### Task 1: One-Artifact Trigger Design (Design Doc)

**Intent:** Design the paste-able artifact that triggers the onboarding flow, accounting for the fact that MCP config and agent prompt are pasted on different surfaces (CLI vs chat) in most harnesses. The world needs a single artifact even if it's technically two blocks — the UX collapses them.
**Acceptance:** A design document (`04-one-artifact-design.md`) that specifies: (a) the exact content of the one-artifact block (MCP config + onboarding prompt), (b) per-harness paste instructions (Claude Code, Codex, Cursor, Pi), (c) a fallback for harnesses where single-paste isn't achievable, (d) how the welcome page presents and copies it, (e) a prototype HTML snippet showing the artifact block on the welcome page.
**Files:**
- Create: `docs/epics/2026-08-07-hosted-onboarding-235/04-one-artifact-design.md`

**Step 1: Draft the MCP config block for each harness**

Write the exact MCP config the user needs for each of the 4 supported harnesses. Pull existing configs from:
- Current `welcome.html` MCP config block (stdio transport with env var)
- #236 MCP Streamable HTTP — the artifact should use Streamable HTTP (`url` + `headers`) not stdio, since hosted users don't need the Python package
- Claude Code: `claude mcp add --transport http tortoise https://api.premiselabs.co/mcp --header "Authorization: Bearer tt_YOUR_KEY"`
- Codex: `codex mcp add tortoise https://api.premiselabs.co/mcp --bearer-token-env-var TORTOISE_API_KEY`
- Cursor: `.cursor/mcp.json` file snippet
- Pi: `.pi/mcp.json` snippet

**Step 2: Draft the onboarding prompt block**

Write the exact markdown prompt the user copies alongside the MCP config. The prompt must be self-contained — when pasted into ANY agent, it must trigger the yes/no flow without additional user instruction.

The prompt should:
- Begin with: "You are now connected to Tortoise — an epistemic memory graph for agents. I want to set up my memory. Ask me these yes/no questions one at a time..."
- List all 6 questions with what each "yes" executes
- Include error handling: "If any tool call fails, tell me what went wrong and continue with the remaining questions"
- End with verification: call `tortoise_context` or `GET /v1/context` and show the digest

**Step 3: Design the combined artifact presentation**

Design how the welcome page presents both blocks as a single "copy this" action:
- Option A: "Copy onboarding setup" button that copies BOTH (config + prompt) to clipboard with a separator
- Option B: Two separate "Copy" buttons, prominently stacked, with numbered steps
- Recommendation: Option B (numbered steps) is more reliable since harnesses have different paste surfaces

**Step 4: Write per-harness paste instructions**

For each harness, write the exact steps:
- Claude Code: Step 1: Run this CLI command. Step 2: Paste this prompt in chat.
- Codex: Step 1: Run this CLI command. Step 2: Paste this prompt.
- Cursor: Step 1: Add this to `.cursor/mcp.json`. Step 2: Add this to `.cursor/rules/tortoise-onboarding.md`.
- Pi: Step 1: Install the tortoise-context extension. Step 2: Paste this prompt.

**Step 5: Save design doc**

Save to `docs/epics/2026-08-07-hosted-onboarding-235/04-one-artifact-design.md`.

---

### Task 2: Yes/No Question Set Finalization + Execution Paths (Design Doc)

**Intent:** Finalize the 6 yes/no questions, define exactly what each "yes" executes (API calls, state changes), and what each "no" skips. This is the contract between the agent prompt and the backend.
**Acceptance:** A design doc (`05-question-set.md`) that specifies: (a) each question's exact wording, (b) the execution path for "yes" (which MCP tool/endpoint, with expected inputs), (c) the skip path for "no" (what state is recorded), (d) question order and dependencies (e.g., "Index?" only appears if "Connect GitHub?" was "yes"), (e) the final verification step for all paths.
**Files:**
- Create: `docs/epics/2026-08-07-hosted-onboarding-235/05-question-set.md`

**Step 1: Finalize question wording and order**

Based on the research brief's proposed set, finalize each question's exact user-facing text:

| # | Question | Dependency |
|---|----------|-------------|
| 1 | "Would you like to connect GitHub? Tortoise can remember your issues and PRs." | None |
| 1a | (If yes) "What's your GitHub organization or username?" | Q1=yes |
| 2 | "Index your GitHub issues and PRs into memory? This runs in the background." | Q1=yes |
| 3 | "Record your agent sessions automatically? Every conversation will be filed as memory." | None |
| 4 | "Create a demo graph? I'll show you what an epistemic graph looks like with sample data." | None |
| 5 | "Set up a team? You can invite collaborators to share memory." | None |
| 6 | "Ready! Let me show you what Tortoise already remembers." | Always (runs regardless of answers) |

Note: Q5 (team) is low-priority and may be deferred per scope doc's "single-team flow only." Consider making it: "Set up a team (coming soon)" as a teaser.

**Step 2: Define execution paths for each "yes"**

For each question, define the exact tool/endpoint call and expected state change:

| Question | "Yes" action | Tool/Endpoint | State change |
|----------|-------------|---------------|--------------|
| Q1 | Initiate GitHub OAuth | `tortoise_onboarding_github_connect` (new MCP tool) → redirect user | `onboarding.github_connected: true` |
| Q2 | Start background indexing | `POST /v1/index/github` (new endpoint) | `onboarding.github_indexed: true` |
| Q3 | Enable session recording | `tortoise_onboarding_session_recording` (new MCP tool) | `onboarding.session_recording: true` |
| Q4 | Create demo graph | `POST /v1/demo` (new endpoint) | `onboarding.demo_created: true` |
| Q5 | Create team + invite | `tortoise_onboarding_create_team` (new MCP tool) | `onboarding.team_created: true` |

**Step 3: Define skip paths for each "no"**

| Question | "No" action | State change |
|----------|------------|--------------|
| Q1 | Skip GitHub entirely (Q2 also skipped) | `onboarding.github_connected: false` |
| Q2 | (Hidden if Q1=no) | `onboarding.github_indexed: false` |
| Q3 | Skip session recording | `onboarding.session_recording: false` |
| Q4 | Skip demo graph | `onboarding.demo_created: false` |
| Q5 | Skip team setup | `onboarding.team_created: false` |

**Step 4: Design the verification step (Q6)**

Regardless of answers, Q6 always runs:
1. Agent calls `tortoise_health` to confirm connection
2. Agent calls `tortoise_context` (via `GET /v1/context`) to get memory digest
3. Agent presents digest: "Here's what Tortoise remembers: [digest]"
4. If no data sources were connected (all "no"): "Tortoise is connected but empty. Create your first memory with `tortoise_create_point()`."
5. Funnel event `onboarding_complete` fires with `{elapsed_time_s, questions_answered: {q1: yes/no, ...}}`

**Step 5: Save design doc**

---

### Task 3: Welcome Page Flow Design (Design Doc + Wireframe)

**Intent:** Design the updated welcome page (`premise-labs/welcome.html`) that presents the one-artifact, shows onboarding progress, and guides the user through the paste flow. The current page shows API key + MCP config + quickstart — it needs the onboarding artifact and clearer flow.
**Acceptance:** A design doc (`06-welcome-page-design.md`) with: (a) wireframe/sketch of the updated welcome page layout, (b) the three states (loading → ready → post-onboarding), (c) content for all sections, (d) how the one-artifact is presented and copied, (e) what changes from the current `welcome.html`.
**Files:**
- Create: `docs/epics/2026-08-07-hosted-onboarding-235/06-welcome-page-design.md`

**Step 1: Map current welcome.html states**

The current page has three states: loading (spinner, "Provisioning your Tortoise..."), error ("try signing up again"), success (API key card + MCP config + quickstart + dashboard button).

**Step 2: Design the updated "Ready" state**

The ready state should add:
1. The API key card (keep existing — it works)
2. The one-artifact section replacing the current "MCP config" + "Quickstart" sections:
   - "Copy to your agent" block with BOTH the MCP config and onboarding prompt
   - Per-harness tabs or accordion (Claude Code / Codex / Cursor / Pi)
   - "Copy setup" button
3. A visual flow: "1. Copy → 2. Paste into agent → 3. Answer questions → 4. Done"
4. The existing dashboard link moves below

**Step 3: Design the "Post-Onboarding" state (new)**

After the user completes onboarding (detected via polling or redirect), the welcome page shows:
- ✅ "Tortoise is set up!" with checkmark for each completed step
- Memory digest summary ("Your graph has N points, M operators")
- Link to dashboard
- "What's next?" — links to docs, API reference, MCP tools list

**Step 4: Design per-harness presentation**

Each harness gets its own section/tab:
- Claude Code: CLI command block + prompt text block + numbered steps
- Codex: CLI command block + prompt text block + numbered steps
- Cursor: JSON file snippet + `.cursor/rules/` file snippet + steps
- Pi: Extension install + prompt + steps

**Step 5: Save design doc**

---

### Task 4: Agent-Side Onboarding Prompt (Deliverable Markdown Block)

**Intent:** Write the actual markdown block that the user copies and pastes into their agent. This is the prompt that drives the yes/no flow. It must be robust enough to survive agent hallucination and work across Claude Code, Codex, Cursor, and Pi.
**Acceptance:** A deployable markdown file (`tortoise/onboarding/AGENT_ONBOARDING.md`) that: (a) is self-contained (works when pasted alone), (b) lists all 6 questions with tool calls for each, (c) handles errors gracefully, (d) includes the final verification step, (e) is tested manually against Claude Code and at least one other harness.
**Files:**
- Create: `tortoise/onboarding/AGENT_ONBOARDING.md`
- Create: `docs/epics/2026-08-07-hosted-onboarding-235/07-agent-prompt-design.md` (design rationale doc)

**Step 1: Write the onboarding prompt**

Write the markdown block following the agent-instruction pattern from `CLAUDE.tortoise.md`. The prompt must:

```markdown
# Tortoise Onboarding — Set up your agent's memory

You are connected to Tortoise via MCP. Your goal: get my memory set up in under 5 minutes.

## Rules
- Ask me these questions ONE AT A TIME. Do not ask the next until I answer.
- If I say "yes," execute the action immediately using the specified tool.
- If I say "no," skip to the next question.
- If any tool call fails, tell me what went wrong and continue.
- Track completion. After all questions, run verification.

## Questions

### 1. Connect GitHub?
Ask: "Would you like to connect GitHub? Tortoise can remember your issues and PRs."
If yes → ask for org/username → call `tortoise_onboarding_github_connect(org=...)`
If no → skip to Q3 (skip Q2)

### 2. Index GitHub?
(Only if Q1 was yes)
Ask: "Index your GitHub issues and PRs into memory? This runs in the background."
If yes → call `tortoise_onboarding_github_index(org=...)` — it returns a job ID
If no → continue

### 3. Record sessions?
Ask: "Record your agent sessions automatically? Every conversation will be filed as memory."
If yes → call `tortoise_onboarding_session_recording(enabled=true)`
If no → continue

### 4. Demo graph?
Ask: "Create a demo graph? I'll show you what an epistemic graph looks like."
If yes → call `tortoise_onboarding_demo_create()` — shows graph stats
If no → continue

### 5. Team?
Ask: "Set up a team? You can invite collaborators."
If yes → call `tortoise_onboarding_create_team(name="My Team")`
If no → continue

### 6. Show me!
Call `tortoise_health` to verify connection.
Then call `tortoise_context` (via `GET /v1/context` if available, or `tortoise_diary_read` as fallback).
Display: "Here's what Tortoise remembers: [digest]"
If nothing remembered: "Tortoise is connected and ready. Create your first memory with tortoise_create_point()."
```

**Step 2: Write the design rationale doc**

Document: why markdown prompt vs MCP tool vs skill file, how the prompt handles error paths, what happens if the agent deviates from the script, and how to update the prompt when new questions are added.

**Step 3: Manual test plan**

Test against at minimum:
1. Paste into Claude Code (with MCP already configured) — does the agent follow the script?
2. Paste into Codex or Cursor — same verification
3. Test the "all no" path — does the agent skip correctly?
4. Test with tool call failures — does the agent recover?

**Step 4: Save both files**

---

### Task 5: API Gap Analysis (Design Doc — Enumerate, Don't Build)

**Intent:** Document every new API endpoint and MCP tool needed for the onboarding flow. This becomes the spec for Phase 2 build tasks. The analysis covers: endpoint signature, inputs/outputs, auth requirements, error conditions, and idempotency guarantees.
**Acceptance:** A design doc (`08-api-gap-analysis.md`) listing all new endpoints/tools with complete signatures, including: (a) what already exists (reuse vs build), (b) what needs building, (c) migration/change notes for `welcome.html`.
**Files:**
- Create: `docs/epics/2026-08-07-hosted-onboarding-235/08-api-gap-analysis.md`

**Step 1: Inventory existing endpoints that support onboarding**

| Existing | Purpose | Reuse? |
|----------|---------|--------|
| `POST /internal/provision` | Key provisioning (operator-only) | Needs public variant |
| `GET /health` | Health check | Reuse as-is |
| `POST /v1/points` | Create points | Reuse as-is |
| `GET /v1/context` | Memory digest (#231) | Reuse as-is |
| `POST /v1/sessions` | Capture sessions | Reuse as-is |
| `GET /v1/team` | Team info | Reuse as-is |
| `POST /v1/team/keys` | Create API keys | Reuse as-is |
| `tortoise_health` (MCP) | MCP self-test | Reuse as-is |
| `tortoise_context` / `tortoise_diary_read` (MCP) | Query memory | Reuse as-is |
| `tortoise_create_point` (MCP) | Create points | Reuse as-is |
| `tortoise_summarize_structure` (MCP) | Graph stats | Reuse as-is |

**Step 2: Enumerate new endpoints needed**

| New Endpoint | Method | Purpose | Inputs | Outputs | Auth |
|-------------|--------|---------|--------|---------|------|
| `/v1/register` | POST | Self-service key provisioning | email, password (or OAuth token) | `{api_key, team_id, graph_name}` | None (creates account) |
| `/v1/onboarding/github/connect` | POST | Initiate GitHub OAuth | `{org, redirect_uri}` | `{auth_url}` | Bearer `tt_` |
| `/v1/onboarding/github/callback` | GET | GitHub OAuth callback | `code`, `state` | redirect to welcome page | None (OAuth state param) |
| `/v1/onboarding/github/status` | GET | Check GitHub connection status | — | `{connected, repos_count, org}` | Bearer `tt_` |
| `/v1/index/github` | POST | Start background indexing | `{org, repo?}` | `{job_id, status: "started"}` | Bearer `tt_` |
| `/v1/index/github/{job_id}` | GET | Poll indexing status | — | `{job_id, status, points_created, progress}` | Bearer `tt_` |
| `/v1/demo` | POST | Create demo graph | — | `{points_created, operators_created}` | Bearer `tt_` |
| `/v1/onboarding/state` | GET | Get onboarding state | — | `{onboarding: {...}}` | Bearer `tt_` |
| `/v1/onboarding/state` | PATCH | Update onboarding state | `{step: value}` | `{onboarding: {...}}` | Bearer `tt_` |
| `/v1/onboarding/session-recording` | POST | Toggle session recording | `{enabled: bool}` | `{session_recording: bool}` | Bearer `tt_` |

**Step 3: Enumerate new MCP tools needed**

| New MCP Tool | Purpose | Maps to Endpoint |
|-------------|---------|-----------------|
| `tortoise_onboarding_github_connect` | Start GitHub OAuth from agent | `POST /v1/onboarding/github/connect` |
| `tortoise_onboarding_github_index` | Start indexing | `POST /v1/index/github` |
| `tortoise_onboarding_demo_create` | Create demo graph | `POST /v1/demo` |
| `tortoise_onboarding_session_recording` | Toggle session recording | `POST /v1/onboarding/session-recording` |
| `tortoise_onboarding_create_team` | Create team + invite link | `POST /v1/teams` (new or existing) |
| `tortoise_onboarding_state` | Get/set onboarding state | `GET/PATCH /v1/onboarding/state` |
| `tortoise_onboarding_health` | Extended health (connection + onboarding status) | Composite: `GET /health` + `GET /v1/onboarding/state` |

**Step 4: Document what changes in welcome.html**

The current page calls `POST /internal/provision` via Supabase edge function. For self-service:
- Replace or supplement the Supabase edge function → `POST /v1/register` called directly or via new edge function
- The polling logic (`waitForProvisioning`) stays but now polls a public endpoint
- Add the one-artifact section (designed in Task 1)

**Step 5: Save design doc**

---

### Task 6: Funnel Analytics Design (Design Doc — Define Schema, Don't Implement)

**Intent:** Define the analytics events, properties, and funnel stages for the onboarding flow. This schema drives implementation in Phase 2 and ongoing measurement.
**Acceptance:** A design doc (`09-analytics-schema.md`) with: (a) event taxonomy (all events, when they fire, what properties they carry), (b) funnel stages and expected conversion rates, (c) implementation notes (where to instrument, what analytics backend to use).
**Files:**
- Create: `docs/epics/2026-08-07-hosted-onboarding-235/09-analytics-schema.md`

**Step 1: Define event taxonomy**

| Event | Fires when | Properties |
|-------|-----------|------------|
| `signup_completed` | User completes signup (account created) | `{method: "email"\|"github", timestamp}` |
| `key_provisioned` | API key generated and displayed | `{team_id, elapsed_from_signup_s}` |
| `artifact_copied` | User clicks "Copy" on welcome page | `{harness: "claude"\|"codex"\|"cursor"\|"pi", section: "config"\|"prompt"\|"both"}` |
| `agent_connected` | Agent successfully calls `tortoise_health` (MCP) | `{harness, elapsed_from_copy_s}` |
| `question_answered` | User answers a yes/no question | `{question_number, question_id, answer: "yes"\|"no"}` |
| `github_connected` | GitHub OAuth completes | `{org, repo_count}` |
| `indexing_started` | Background indexing begins | `{job_id, org, repo?}` |
| `indexing_completed` | Background indexing finishes | `{job_id, points_created, elapsed_s}` |
| `demo_created` | Demo graph creation completes | `{points_created, operators_created}` |
| `session_recording_enabled` | Session recording toggled on | `{enabled: true}` |
| `onboarding_complete` | All questions answered, verification done | `{elapsed_time_s, questions: {q1: "yes"\|"no", ...}, steps_completed: N}` |
| `onboarding_error` | Any step fails | `{step, error_message, error_type}` |

**Step 2: Define funnel stages**

```
signup_completed (100%)
  → key_provisioned (expected: 95%)
  → artifact_copied (expected: 70%)
  → agent_connected (expected: 50%)
  → question_answered × N (expected: 80% of connected)
  → onboarding_complete (target: 60% of signups)
```

**Step 3: Choose analytics backend**

Recommendation: PostHog (auto-capture + custom events, generous free tier) or custom events table in Supabase if PostHog is too heavy. For v1, a simple JSONL log file in the hosted API + a Supabase `analytics_events` table is sufficient and avoids external dependencies.

**Step 4: Save design doc**

---

## Phase 2 — Build Tasks

> Phase 2 is IN SCOPE per owner override (2026-08-08). No traction gate.
> Build tasks produce working code, deployed to the hosted platform.

### Task 7: Self-Service Key Provisioning Endpoint (`POST /v1/register`)

**Intent:** Let users get API keys without operator intervention. Currently only `POST /internal/provision` exists (operator-only, requires `FASTAPI_INTERNAL_KEY`). This task adds a public registration endpoint.
**Acceptance:** `POST /v1/register` accepts email+password, creates a Supabase user + team + API key, returns the key. Existing `welcome.html` polling flow continues to work. Idempotent: re-registering the same email returns the existing key (or sends verification).
**Files:**
- Modify: `tortoise/hosted_api.py` — add `POST /v1/register` endpoint
- Modify: `premise-labs/welcome.html` — optionally update to use new endpoint directly
- Create: `tests/test_hosted_register.py` — integration tests
- Create: `premise-labs/functions/register/` — Supabase edge function (optional, alternative to direct API call)

**Step 1: Write failing test**

```python
# tests/test_hosted_register.py
def test_register_new_user_returns_api_key():
    response = client.post("/v1/register", json={"email": "test@example.com", "password": "securepass123"})
    assert response.status_code == 201
    data = response.json()
    assert data["api_key"].startswith("tt_")
    assert "team_id" in data

def test_register_existing_user_is_idempotent():
    # Register twice → second call returns same key or "already registered"
    ...
```

**Step 2: Implement `/v1/register` in hosted_api.py**

The endpoint must:
1. Validate email format + password strength
2. Call Supabase Admin API to create user (`supabase.auth.admin.createUser`)
3. Provision tenant graph (reuse `/internal/provision` logic, but public)
4. Generate `tt_` API key (reuse `/v1/team/keys` logic)
5. Return `{api_key, team_id, graph_name}`
6. If user exists: return `{message: "already registered", api_key: null}` (security: don't re-expose key)

**Step 3: Update welcome.html if needed**

The current page polls `user_teams` table via Supabase JS client. If we add a direct signup path (email+password on welcome page), add:
- Signup form on index.html or a new signup flow
- Or keep existing Supabase Auth + edge function flow (less work, already working)

Decision: Keep the existing Supabase Auth flow for signup. Add `/v1/register` as an API-only path for programmatic access.

**Step 4: Run tests, commit**

---

### Task 8: GitHub OAuth App + Connect Flow

**Intent:** Let users connect GitHub to Tortoise via OAuth (not PAT). The agent prompt triggers OAuth; the user authorizes in browser; Tortoise gets read-only access to issues/PRs.
**Acceptance:** A GitHub OAuth app is registered. `POST /v1/onboarding/github/connect` returns an auth URL. `GET /v1/onboarding/github/callback` exchanges the code for a token, stores it, and records `github_connected: true`. The flow works from both the agent prompt (returns URL for user to click) and the welcome page.
**Files:**
- Create: GitHub OAuth app registration (manual — document in plan)
- Modify: `tortoise/hosted_api.py` — add connect + callback endpoints
- Create: `tests/test_github_connect.py`
- Modify: `tortoise/mcp_server.py` — add `tortoise_onboarding_github_connect` tool

**Step 1: Register GitHub OAuth App**

Manual step (document in `docs/epics/2026-08-07-hosted-onboarding-235/`):
1. Go to GitHub Settings → Developer settings → OAuth Apps → New
2. Name: "Tortoise"
3. Homepage URL: `https://premiselabs.co`
4. Callback URL: `https://api.premiselabs.co/v1/onboarding/github/callback`
5. Scopes: `read:org, read:user, repo:status` (read-only access to issues/PRs)
6. Save client ID + client secret to Fly.io secrets

**Step 2: Implement connect endpoint**

```python
@app.post("/v1/onboarding/github/connect")
async def github_connect(body: GitHubConnectRequest, team: dict = Depends(get_current_team)):
    """Generate GitHub OAuth URL for the user to authorize."""
    state = secrets.token_urlsafe(32)
    # Store state → team_id mapping (in-memory or DB)
    auth_url = (
        f"https://github.com/login/oauth/authorize"
        f"?client_id={GITHUB_CLIENT_ID}"
        f"&redirect_uri={CALLBACK_URL}"
        f"&state={state}"
        f"&scope=read:org,read:user"
    )
    return {"auth_url": auth_url, "state": state}
```

**Step 3: Implement callback endpoint**

```python
@app.get("/v1/onboarding/github/callback")
async def github_callback(code: str, state: str):
    """Exchange OAuth code for token, store in team record."""
    # Verify state, exchange code, store token
    ...
    # Update onboarding state
    return RedirectResponse(url=f"https://app.premiselabs.co/welcome?github=connected")
```

**Step 4: Add MCP tool**

Add `tortoise_onboarding_github_connect` to `tortoise/mcp_server.py`:
```python
def tortoise_onboarding_github_connect(org: str, redirect_uri: str | None = None) -> dict:
    """Initiate GitHub OAuth flow. Returns auth_url for the user to open."""
    ...
```

**Step 5: Write tests, commit**

---

### Task 9: Background Indexing Endpoint + GitHub Issue/PR Indexer

**Intent:** When a user says "yes" to indexing, start a background job that fetches issues/PRs from their GitHub repos and creates Points in their tenant graph. The indexing runs asynchronously; the agent gets a job ID for polling.
**Acceptance:** `POST /v1/index/github` accepts an org name, returns a job ID. A background task (asyncio) fetches issues/PRs via GitHub API, creates Points. `GET /v1/index/github/{job_id}` returns status and progress. Idempotent: re-indexing the same org updates Points, doesn't duplicate.
**Files:**
- Create: `tortoise/indexer/github_indexer.py` — indexing logic
- Modify: `tortoise/hosted_api.py` — add index endpoints + background task runner
- Create: `tests/test_github_indexer.py` — with mock GitHub API
- Modify: `tortoise/mcp_server.py` — add `tortoise_onboarding_github_index` tool

**Step 1: Write failing test**

```python
def test_index_github_creates_job():
    response = client.post("/v1/index/github", json={"org": "test-org"}, headers=auth_header)
    assert response.status_code == 202
    data = response.json()
    assert "job_id" in data
    assert data["status"] == "started"

def test_poll_job_returns_status():
    # Create job, poll it, verify progress
    ...

def test_indexed_issues_become_points():
    # Create job, wait for completion, query points
    ...
```

**Step 2: Implement GitHub indexer**

```python
# tortoise/indexer/github_indexer.py
class GitHubIndexer:
    def __init__(self, sdk: TortoiseSDK, token: str, org: str):
        self.sdk = sdk
        self.token = token
        self.org = org

    async def index_issues(self, repo: str | None = None) -> int:
        """Fetch issues from GitHub, create Points. Returns count created."""
        ...
        # For each issue: create Point(kind="observation", content=issue.title + body[:500])
        # For each PR: create Point(kind="decision", content=pr.title + body[:500])
        # Set source metadata: {"source": "github", "org": org, "repo": repo, "issue_number": N}
        ...
```

**Step 3: Implement async job system**

Use asyncio background tasks (no Celery/RQ — keep it simple for v1):
```python
# In hosted_api.py
_INDEX_JOBS: dict[str, dict] = {}  # job_id → {status, progress, result}

@app.post("/v1/index/github")
async def start_indexing(body: GitHubIndexRequest, team: dict = Depends(get_current_team)):
    job_id = f"idx_{uuid.uuid4().hex[:12]}"
    _INDEX_JOBS[job_id] = {"status": "started", "progress": 0, "points_created": 0}
    asyncio.create_task(_run_indexing(job_id, team, body.org))
    return {"job_id": job_id, "status": "started"}
```

**Step 4: Add rate limit handling**

GitHub API rate limit is 5000/hr. Implement:
- Conditional requests (ETag/If-None-Match) to avoid consuming rate limit on unchanged resources
- Exponential backoff on 429 responses
- Max 500 issues/PRs per indexing run (configurable)

**Step 5: Add MCP tool, tests, commit**

---

### Task 10: Demo Graph Endpoint

**Intent:** Create a curated demo graph (Points + Operators) that shows new users what Tortoise's epistemic graph looks like. Pattern: a few decisions connected by evidence/contradiction operators — a mini "here's what agent memory looks like."
**Acceptance:** `POST /v1/demo` creates a demo graph in the user's tenant. Returns counts. Idempotent: re-running overwrites the demo (deletes previous demo points, creates new ones). The demo graph includes at least 5 Points and 3 Operators showing different relationships (supports, contradicts, mitigates).
**Files:**
- Modify: `tortoise/hosted_api.py` — enhance existing `POST /internal/demo` or create public endpoint
- Create: `tests/test_demo_graph.py`
- Modify: `tortoise/mcp_server.py` — add `tortoise_onboarding_demo_create` tool

**Step 1: Design the demo graph narrative**

A relatable scenario for developers evaluating agent memory:
- Point 1 (decision): "Use FalkorDB for graph storage"
- Point 2 (evidence): "FalkorDB benchmarks show 10x faster graph queries than vanilla Redis"
- Point 3 (evidence): "Existing team has Redis expertise — FalkorDB reuses Redis protocol"
- Point 4 (decision): "Deploy on Fly.io for edge distribution"
- Point 5 (evidence): "Fly.io cold starts are < 500ms for Python apps"
- Operator 1: Point 2 SUPPORTS Point 1
- Operator 2: Point 3 SUPPORTS Point 1
- Operator 3: Point 1 CONTRADICTS Point 4 (different infrastructure decisions)

**Step 2: Implement endpoint**

```python
@app.post("/v1/demo")
async def create_demo_graph(team: dict = Depends(get_current_team)):
    """Create or reset a demo graph for the tenant."""
    sdk = _make_sdk(namespace=team["team_id"])

    # Delete previous demo points (idempotent)
    sdk._get_proj().g.query(
        "MATCH (p:Point) WHERE p.source = 'demo' DELETE p"
    )

    # Create demo points and operators
    ...
    return {"points_created": N, "operators_created": M}
```

**Step 3: Add MCP tool `tortoise_onboarding_demo_create`**

Wraps `POST /v1/demo`, returns graph stats so the agent can describe what was created.

**Step 4: Write tests, commit**

---

### Task 11: Onboarding State Tracking

**Intent:** Track what onboarding steps a user has completed so the agent prompt and welcome page can show progress. Store state on the Team record (either in FalkorDB or Supabase `teams` table).
**Acceptance:** `GET /v1/onboarding/state` returns current onboarding state for the team. `PATCH /v1/onboarding/state` updates individual steps. State is a flat JSON object: `{github_connected, github_indexed, session_recording, demo_created, team_created, completed_at}`. The MCP tools update state automatically.
**Files:**
- Modify: `tortoise/hosted_api.py` — add state endpoints
- Modify: `tortoise/mcp_server.py` — add `tortoise_onboarding_state` tool
- Create: `tests/test_onboarding_state.py`

**Step 1: Choose storage backend**

Options:
- **FalkorDB team graph:** Store as Point with kind="onboarding_state". Requires graph query for every read.
- **Supabase teams table:** Add `onboarding_state` JSONB column. Simple, fast reads.
- **Recommendation:** Supabase `teams.onboarding_state` JSONB column. The hosted API already has a `team` dependency that reads team info. Add the column to the query.

**Step 2: Implement endpoints**

```python
@app.get("/v1/onboarding/state")
async def get_onboarding_state(team: dict = Depends(get_current_team)):
    return {"onboarding": team.get("onboarding_state", {})}

@app.patch("/v1/onboarding/state")
async def update_onboarding_state(body: OnboardingStateUpdate, team: dict = Depends(get_current_team)):
    # Merge body into existing state, save to Supabase
    ...
```

**Step 3: Integrate state updates into other endpoints**

- `POST /v1/onboarding/github/callback` → auto-sets `github_connected: true`
- `POST /v1/index/github` completion → auto-sets `github_indexed: true`
- `POST /v1/demo` → auto-sets `demo_created: true`

**Step 4: Write tests, commit**

---

### Task 12: Welcome Page v2 (Dynamic, with Onboarding Artifact)

**Intent:** Update `premise-labs/welcome.html` to include the one-artifact block, per-harness instructions, and a visual onboarding flow. Replace the current "MCP config" + "Quickstart" sections with the unified onboarding artifact.
**Acceptance:** The updated welcome page shows: (a) API key card (kept), (b) the one-artifact block with per-harness tabs and "Copy" button, (c) a numbered flow diagram (1→2→3→4), (d) post-onboarding state showing completion checkmarks. No JavaScript framework dependency — keep the current vanilla HTML/CSS/JS pattern.
**Files:**
- Modify: `premise-labs/welcome.html` — add one-artifact section, harness tabs, post-onboarding state

**Step 1: Rebuild the "Ready" state layout**

Replace the two existing cards (MCP config + Quickstart) with:

```
┌─────────────────────────────────────────┐
│ ✅ Your Tortoise is ready!               │
│ Team: My Team · Tier: Free              │
├─────────────────────────────────────────┤
│ 🔑 Your API key: tt_abc123...  [Copy]   │
├─────────────────────────────────────────┤
│ 🚀 Set up your agent                     │
│                                          │
│ [Claude Code] [Codex] [Cursor] [Pi]     │ ← Harness tabs
│                                          │
│ Step 1: Copy your MCP config             │
│ ┌──────────────────────────────────┐    │
│ │ {mcp config block}               │    │
│ └──────────────────────────────────┘    │
│ [Copy config]                            │
│                                          │
│ Step 2: Copy the onboarding prompt       │
│ ┌──────────────────────────────────┐    │
│ │ # Tortoise Onboarding...          │    │
│ └──────────────────────────────────┘    │
│ [Copy prompt]                            │
│                                          │
│ Step 3: Paste both into your agent       │
│ Step 4: Answer yes/no questions          │
├─────────────────────────────────────────┤
│ 📊 Open Dashboard →                      │
└─────────────────────────────────────────┘
```

**Step 2: Add post-onboarding state**

When the user returns to the welcome page after completing onboarding (detected via polling `onboarding_state`):

```
┌─────────────────────────────────────────┐
│ 🎉 Tortoise is all set up!               │
│                                          │
│ ✅ GitHub connected (12 repos)           │
│ ✅ 847 issues indexed as memory          │
│ ✅ Session recording active              │
│ ✅ Demo graph created (5 points, 3 ops)  │
│                                          │
│ 📊 Open Dashboard →                      │
│ 📖 Read the docs →                       │
└─────────────────────────────────────────┘
```

**Step 3: Implement harness tabs**

Use CSS-only tabs (no JS framework). Each tab shows:
- The exact MCP config for that harness (Streamable HTTP format)
- The onboarding prompt (same for all harnesses, or slightly adapted)
- Harness-specific steps

**Step 4: Add analytics instrumentation**

Fire `artifact_copied` event on copy button clicks. Include harness + section info.

**Step 5: Test, commit**

---

### Task 13: Agent Onboarding Prompt Deployment

**Intent:** Deploy the onboarding prompt so it's accessible from the welcome page and can be updated independently of the welcome page code. The prompt is a markdown file served as static content or stored as a snippet.
**Acceptance:** The onboarding prompt is served at a stable URL (e.g., `https://premiselabs.co/onboarding-prompt.md` or embedded in the hosted API at `GET /v1/onboarding/prompt`). The welcome page links to it. The prompt content matches what was designed in Task 4. Updating the prompt does not require redeploying the API.
**Files:**
- Create: `premise-labs/onboarding-prompt.md` — the canonical prompt file
- Modify: `premise-labs/welcome.html` — reference the prompt from the artifact section
- Modify: `tortoise/hosted_api.py` — optional: serve prompt via API
- Modify: `tortoise/claude-hooks/CLAUDE.tortoise.md` — add a link to the onboarding prompt

**Step 1: Save canonical prompt**

Write the final prompt from Task 4 to `premise-labs/onboarding-prompt.md`.

**Step 2: Serve the prompt**

Option A: Serve as static file from premise-labs (simplest).
Option B: Serve via hosted API at `GET /v1/onboarding/prompt` (allows dynamic personalization later).

**Step 3: Update welcome.html**

The "Copy prompt" button in the one-artifact section should fetch and display the prompt content from the stable URL.

**Step 4: Update CLAUDE.tortoise.md**

Add a section pointing new users to the onboarding flow:
```markdown
## First-time setup
If this is your first session with Tortoise, paste the onboarding prompt:
[Onboarding prompt URL]
```

**Step 5: Manual verification**

Paste the prompt into a fresh Claude Code session. Verify the agent follows the yes/no flow.

---

### Task 14: Analytics Instrumentation

**Intent:** Implement the analytics events designed in Task 6. Track the onboarding funnel from signup → complete. Use a lightweight approach for v1 (no external analytics dependency).
**Acceptance:** All events from the analytics schema fire at the correct moments. Events are stored in a Supabase `analytics_events` table (or a JSONL log). A simple dashboard query shows funnel conversion rates. No PII in event payloads.
**Files:**
- Modify: `tortoise/hosted_api.py` — add analytics event emission on key endpoints
- Modify: `premise-labs/welcome.html` — add client-side analytics events
- Create: `tests/test_analytics_events.py` — verify events fire
- Create: Supabase migration — `analytics_events` table

**Step 1: Create analytics events table (Supabase migration)**

```sql
CREATE TABLE analytics_events (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    team_id TEXT NOT NULL,
    event_name TEXT NOT NULL,
    properties JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_analytics_team ON analytics_events(team_id, created_at);
CREATE INDEX idx_analytics_event ON analytics_events(event_name);
```

**Step 2: Instrument hosted API**

Add a helper function `_track_event(team_id, event_name, properties)` that inserts into `analytics_events`. Call it at:
- `POST /v1/register` → `signup_completed`
- `POST /v1/team/keys` (key displayed) → `key_provisioned`
- `POST /v1/onboarding/github/callback` → `github_connected`
- `POST /v1/index/github` → `indexing_started`
- Index job completion → `indexing_completed`
- `POST /v1/demo` → `demo_created`
- Onboarding state `completed_at` set → `onboarding_complete`
- Any error path → `onboarding_error`

**Step 3: Instrument welcome.html**

Add client-side event tracking for:
- Copy button clicks → `artifact_copied`
- Page load (ready state) → `welcome_page_viewed`
- Post-onboarding state detected → `onboarding_complete` (client-side verification)

Use a simple `fetch` POST to the hosted API or Supabase insert.

**Step 4: Write tests, commit**

---

### Task 15: New MCP Tools Registration

**Intent:** Register all new MCP tools in `tortoise/mcp_server.py` so they're available when the agent connects. These tools wrap the new endpoints and provide the agent-side interface for the onboarding flow.
**Acceptance:** 7 new MCP tools are registered and discoverable: `tortoise_onboarding_github_connect`, `tortoise_onboarding_github_index`, `tortoise_onboarding_demo_create`, `tortoise_onboarding_session_recording`, `tortoise_onboarding_create_team`, `tortoise_onboarding_state`, `tortoise_onboarding_health`. Each has a docstring, typed inputs, and error handling.
**Files:**
- Modify: `tortoise/mcp_server.py` — add 7 new tool functions

**Step 1: Write each tool function**

Follow the existing MCP tool pattern (typed inputs, `make_request` to hosted API, return JSON-safe dict):

```python
def tortoise_onboarding_github_connect(org: str, redirect_uri: str | None = None) -> dict:
    """Start GitHub OAuth for Tortoise onboarding. Returns an auth_url to open in browser."""
    ...

def tortoise_onboarding_github_index(org: str, repo: str | None = None) -> dict:
    """Index GitHub issues/PRs into Tortoise memory. Returns a job_id for polling."""
    ...

def tortoise_onboarding_demo_create() -> dict:
    """Create a demo epistemic graph showing how Tortoise models decisions and evidence."""
    ...

def tortoise_onboarding_session_recording(enabled: bool) -> dict:
    """Enable or disable automatic session recording."""
    ...

def tortoise_onboarding_create_team(name: str) -> dict:
    """Create a new team with an invite link."""
    ...

def tortoise_onboarding_state() -> dict:
    """Get the current onboarding progress for this team."""
    ...

def tortoise_onboarding_health() -> dict:
    """Extended health check: connection status + onboarding progress."""
    ...
```

**Step 2: Ensure all tools use Bearer auth**

Each tool call must pass the `Authorization: Bearer tt_...` header to the hosted API. The MCP server already has API key handling from env var — reuse that pattern.

**Step 3: Run MCP self-test**

```bash
python -m tortoise.mcp_server --self-test
```
Verify all 7 new tools appear in the tool list (total should be 63: 56 existing + 7 new).

**Step 4: Commit**

---

### Task 16: E2E Integration Test (Playwright — Welcome Page + Agent Prompt Smoke Test)

**Intent:** Verify the complete flow from welcome page → copy artifact → agent connection works end-to-end. This is a manual-assisted test since agent interactions can't be fully automated.
**Acceptance:** A test script or documented test run that: (a) loads the welcome page and verifies the one-artifact is displayed, (b) verifies the copy button works, (c) manually pastes the artifact into a real agent session and verifies the agent connects, (d) verifies at least one yes/no question works.
**Files:**
- Create: `tests/e2e/test_welcome_page.py` — Playwright test for welcome page
- Create: `docs/epics/2026-08-07-hosted-onboarding-235/10-e2e-test-results.md` — manual test results doc

**Step 1: Write Playwright tests for welcome page**

```python
def test_welcome_shows_api_key(page):
    page.goto("https://premise-app.fly.dev/welcome")
    page.wait_for_selector("#api-key")
    key = page.text_content("#api-key")
    assert key.startswith("tt_")

def test_artifact_copy_button_works(page):
    page.goto("https://premise-app.fly.dev/welcome")
    page.click("#btn-copy-mcp")
    # Verify clipboard content
    ...

def test_harness_tabs_switch(page):
    # Click each harness tab, verify content changes
    ...

def test_post_onboarding_state(page):
    # Mock onboarding_state as completed, verify checkmarks shown
    ...
```

**Step 2: Manual smoke test protocol**

Document the manual test steps:
1. Sign up at premiselabs.co
2. Verify welcome page loads with key and artifact
3. Copy Claude Code config + prompt
4. Paste into Claude Code
5. Verify agent lists tortoise tools
6. Verify agent asks Q1 ("Connect GitHub?")
7. Answer "no" to all, verify agent calls `tortoise_health`
8. Verify agent shows memory digest

**Step 3: Run tests, document results**

---

## Child Issue Candidates (Epic-Decompose — NEXT STAGE)

After plan-review approves this plan, the `epic-decompose` skill should create these child issues:

| # | Child Issue Title | Maps to Tasks | Complexity | Dependencies |
|---|-------------------|---------------|------------|--------------|
| 1 | **One-artifact trigger design + prompt** | Tasks 1, 4 | standard | None |
| 2 | **Yes/no question set + execution paths** | Task 2 | standard | #1 |
| 3 | **Welcome page v2 (design + build)** | Tasks 3, 12 | standard | #1, #2 |
| 4 | **API gap analysis + endpoints** | Tasks 5, 7, 11 | complex | #2 |
| 5 | **GitHub OAuth + indexing** | Tasks 8, 9 | complex | #4 |
| 6 | **Demo graph + MCP tools** | Tasks 10, 15 | standard | #4 |
| 7 | **Analytics instrumentation** | Tasks 6, 14 | standard | #4 |
| 8 | **Agent prompt deployment + E2E test** | Tasks 13, 16 | standard | #1, #4, #6 |

**Decomposition rationale:**
- Issues #1–3 are design-forward; they can run in parallel once the research brief is absorbed
- Issues #4–6 are build-forward; they depend on design tasks completing
- Issue #7 (analytics) can run parallel to #4–6
- Issue #8 is the integration gate — it ties everything together with E2E tests

**MECE check:** Each child issue covers a distinct surface (welcome page, agent prompt, API, GitHub integration, demo, analytics, deployment) with no overlap. Collectively they cover all 16 tasks.

---

## Verification Plan

### Pre-Deploy Gates

| Gate | Command/Tool | Expected |
|------|-------------|----------|
| Typecheck (Python) | `python -m py_compile tortoise/*.py` | No errors |
| Unit tests | `python -m pytest tests/ -v -k "register or github or demo or onboarding or index"` | All pass |
| MCP self-test | `python -m tortoise.mcp_server --self-test` | 63 tools listed (56 existing + 7 new) |
| Welcome page test | `python -m pytest tests/e2e/test_welcome_page.py -v` | All pass |
| API schema check | `curl https://api.premiselabs.co/health` | `{"status": "ok"}` |

### Post-Deploy Smoke Tests

| Test | How | Pass Criteria |
|------|-----|---------------|
| Signup → key | Sign up via premiselabs.co | Welcome page shows `tt_` key within 10s |
| MCP connect | Paste MCP config into agent | `tortoise_health` returns OK |
| Onboarding prompt | Paste prompt into agent | Agent asks Q1 |
| All "no" path | Answer "no" to all | Agent shows "Tortoise is connected" |
| Demo graph | Call `POST /v1/demo` | Returns points_created ≥ 5 |
| Context digest | Call `GET /v1/context` | Returns memory digest |
| Analytics | Check `analytics_events` table | `onboarding_complete` event exists |

### Owner Acceptance Gate

Before labeling as `complete`:
1. Owner (danielospina) walks through the full flow: signup → paste → yes/no → memory digest
2. Time-to-first-memory is measured and compared to the < 5 min target
3. At least one harness (Claude Code or Pi) completes the flow end-to-end

---

## Phase 2 Inclusion Confirmation

> ⛔ **OVERRIDE (2026-08-08):** Phase 2 is **NOT gated** on user signal. All build tasks (Tasks 7–16) are **IN SCOPE** and ship as part of this epic. The owner explicitly stated: "planning is more expensive than implementing; design decisions made here should not be re-litigated later."

Phase 1 design artifacts (Tasks 1–6) inform but do not gate Phase 2. Build tasks can begin as soon as the relevant design artifact is complete. In practice: Tasks 1–4 design docs should be drafted first; Tasks 7–15 can then start in parallel based on those designs.
