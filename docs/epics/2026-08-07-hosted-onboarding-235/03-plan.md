---
title: "Epic Plan — Hosted Onboarding Journey (#235)"
type: engineering
domain: platform
doc_status: draft
subjects.team: organisation-design-team
created: 2026-08-07
---

<!-- research-path: docs/epics/2026-08-07-hosted-onboarding-235/01-research-brief.md -->

# Hosted Onboarding Journey — Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.
> **Epic-level plan.** After plan-review, decompose into child issues via `epic-decompose`.

**Goal:** Build the hosted Tortoise onboarding journey so a new user goes from "I signed up" to "my agent remembers decisions and GitHub/session data is flowing into memory" — triggered by one paste-able artifact, guided by ≤6 yes/no questions, in under 5 minutes.

**Team:** organisation-design-team
**Role:** (unset)

**Architecture:** The onboarding flow spans four surfaces: (1) a dynamic welcome page that presents the one-artifact after signup, (2) an agent-side onboarding prompt (markdown block) that drives the yes/no conversation, (3) new MCP tools and REST endpoints for GitHub connect, demo graph **verification**, and onboarding state tracking, and (4) funnel analytics events. The welcome page (premise-labs/) talks to Supabase for auth/key delivery; the agent prompt talks to the hosted API (tortoise/hosted_api.py) via MCP tools (registry-driven, per #454); the hosted API manages onboarding state as properties on the Team node in the registry graph. Phase 1 produces design artifacts only (no code); Phase 2 implements all build components per the owner override removing the traction gate.

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
| 3 | Agent prompt → MCP server | Agent (Claude/Codex/Cursor/Pi) | `tortoise/mcp_server.py` + `tortoise/tool_registry.py` (**58 registry entries; 54 HTTP-visible** — registry-driven per #454) | MCP Streamable HTTP | Manual smoke test (no MCP test harness) + unit tests for new tools | Agent hallucinates tool names; auth fails silently |
| 4 | Agent prompt → Hosted API (new endpoints) | Agent via MCP tools | `tortoise/hosted_api.py` | MCP → FastAPI → FalkorDB | Integration test (`test-integration`) with test tenant | Tenant isolation leak; rate limit on GitHub API |
| 5 | GitHub OAuth → Hosted API | GitHub OAuth App | `tortoise/hosted_api.py` (callback) | OAuth 2.0 redirect → REST | Manual + Playwright for callback flow | CSRF state mismatch; token expiry; app not approved |
| 6 | GitHub indexing → FalkorDB | GitHub REST API | `tortoise/hosted_api.py` → FalkorProjection | REST → Graph DB | Integration test with mock GitHub responses | Rate limit 5000/hr; large repos time out; noise ratio |
| 7 | Demo graph → FalkorDB | `tortoise/hosted_api.py` | FalkorDB (per-tenant graph) | Internal API → Graph DB | Unit test for idempotency | Duplicate points on re-run; cross-tenant leakage |
| 8 | Onboarding state → Team record | `tortoise/hosted_api.py` | **FalkorDB registry graph (Team node properties)** — NOT Supabase (hosted_api has zero Supabase connectivity) | Internal state write | Unit test for state transitions + `test_default_state_returned` + `test_concurrent_patch_different_keys` | State corruption on concurrent writes; missing default state |
| 9 | Analytics events → Analytics backend | `tortoise/hosted_api.py` + JS | **Existing `audit_events` table (`TORTOISE_AUDIT_DSN` + `AuditLogger`) with `properties` JSONB + rate-limited `/v1/analytics/events` sink** | HTTP POST events | Manual verify via dashboard + unit tests | Missing events on error paths; PII in event payloads; double-counted `onboarding_complete` |
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
1. **Step:** User answers "yes" to GitHub connect → **Acceptance:** Agent calls `tortoise_onboarding_github_connect`, displays auth_url → **Test:** `test_github_oauth_flow` (manual OAuth) / `test_github_connect_state` (integration with mock GitHub)
2. **Step:** User opens auth_url in browser, authorizes, confirms in chat → **Acceptance:** Agent **awaits authorization**: polls `tortoise_onboarding_github_status` every 5s until `connected: true` (3-min timeout) — does NOT proceed to Q2 before connection (P0-B) → **Test:** `test_github_status_poll_until_connected` (integration)
3. **Step:** Authorization succeeds → **Acceptance:** `github_connected: true` in onboarding state, repos listed → **Test:** `test_github_connect_state` (integration with mock GitHub)
4. **Step:** OAuth times out / user abandons → **Acceptance:** Agent records `github_connected: false, github_error: "oauth not completed"`, continues with remaining questions → **Test:** `test_github_oauth_timeout_graceful` (integration)
5. **Step:** Indexing attempted before OAuth completes → **Acceptance:** Clear 409 error ("GitHub not connected — complete OAuth first") → **Test:** `test_index_before_oauth_returns_error` (integration)

#### Journey: Indexing → first memory written (E2E-4)
1. **Step:** User answers "yes" to indexing → **Acceptance:** Background indexing starts, job ID returned → **Test:** `test_indexing_job_created` (integration)
2. **Step:** Indexing completes → **Acceptance:** At least one Point created from GitHub content → **Test:** `test_indexing_produces_points` (integration with mock GitHub API)
3. **Step:** Agent queries for indexed content → **Acceptance:** `tortoise_query(kind="observation")` returns results → **Test:** `test_indexed_content_queryable` (integration)

#### Journey: Demo graph created (E2E-5)
1. **Step:** User answers "yes" to demo graph → **Acceptance:** Agent calls `tortoise_onboarding_demo_create` (verify mode — respects the `_demo_sentinel`; backfills only if missing, NEVER deletes-and-overwrites) → **Test:** `test_demo_graph_verification` (unit)
2. **Step:** Agent calls `tortoise_summarize_structure()` → **Acceptance:** Returns N points, M operators (≥ 5 points, ≥ 3 operators incl. supports/contradicts/mitigates) → **Test:** `test_demo_structure_summary` (unit) + `test_demo_has_operators`
3. **Step:** Agent explains demo graph content → **Acceptance:** Agent describes graph structure naturally → **Test:** Manual smoke test

#### Journey: Session recording enabled (E2E-6)
1. **Step:** User answers "yes" to session recording → **Acceptance:** `session_recording: true` in onboarding state → **Test:** `test_session_recording_toggle` (unit)
2. **Step:** Agent confirms → **Acceptance:** Agent shows confirmation with the scoped wording ("Tortoise will remember this agent's sessions") + per-harness capture note → **Test:** Manual smoke test
3. **Step:** Conversation ends with recording on → **Acceptance:** Agent files the conversation end via `POST /v1/sessions` (the capture contract — P1-4) → **Test:** `test_session_capture_filed` (integration)

#### Journey: Onboarding complete → memory digest (E2E-7)
1. **Step:** All questions answered → **Acceptance:** Agent calls `tortoise_context` (MCP tool wrapping `GET /v1/context`) → **Test:** `test_context_after_onboarding` (integration)
2. **Step:** Memory digest displayed → **Acceptance:** Digest shows what Tortoise remembers, elapsed < 5 min → **Test:** `test_onboarding_complete_digest` (integration)
3. **Step:** No memories exist yet → **Acceptance:** Agent auto-creates the welcome Point (first-memory fallback, P1-14) so the digest is never empty → **Test:** `test_first_memory_fallback` (integration)
4. **Step:** Funnel event `onboarding_complete` tracked → **Acceptance:** Server-side single producer — fires when `tortoise_onboarding_complete` sets `completed_at`; event carries elapsed time → **Test:** `test_onboarding_complete_analytics` (unit) + `test_onboarding_complete_fired_once`

#### Journey: User says "no" to everything (E2E-8)
1. **Step:** User answers "no" to all 5 yes/no questions → **Acceptance:** Agent records EVERY answer via `tortoise_onboarding_answer` (no answers are never silently dropped), verifies connection via `tortoise_health` → **Test:** `test_all_no_minimal_setup` (integration)
2. **Step:** Agent shows minimal success → **Acceptance:** "Tortoise is connected. You can create your first memory..." (welcome Point auto-created as first memory) → **Test:** Manual smoke test
3. **Step:** Onboarding state records all skipped → **Acceptance:** All steps `false`/`skipped` with `q1..q5: "no"` recorded, all **HTTP-visible** tools still accessible (64 tools on the streamable-http surface; 4 privilege-bound tools remain excluded) → **Test:** `test_skipped_state_all_accessible` (integration)

#### Failure Modes
- **GitHub OAuth fails (user denies, network error, or poll timeout)** → **Expected behavior:** Agent reports failure after the 3-min `tortoise_onboarding_github_status` poll times out (or immediately on user denial), records `github_connected: false, github_error: "reason"` via `tortoise_onboarding_answer`, and continues with remaining questions. → **Test:** `test_github_oauth_failure_graceful` + `test_github_oauth_timeout_graceful` (integration with error/slow mock)
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

**Key-substitution requirement (plan-review P1):** the artifact NEVER ships with the literal `tt_YOUR_KEY` placeholder as a copyable value. The welcome page must render the config block with the user's **real API key already substituted** (the page holds it from `user_teams`). If the key is not yet provisioned, the copy action is disabled with a "key still provisioning" state rather than copying the placeholder. The `tt_YOUR_KEY` token is documentation-only (shown as an example in the design doc), and every E2E clipboard assertion (Task 16) must verify the copied config contains the real `tt_` key, not the placeholder (see `test_copy_contains_real_key`).

**Path note (plan-review P2):** `premise-labs/` may be renamed to `website/` by implementation time (PR #206). All path references in this plan use `premise-labs/`; introduce a single path variable in the design doc (e.g., `$WEB_DIR`) and note the possible rename so the artifact build does not hardcode the old folder name.

**Step 2: Draft the onboarding prompt block**

Write the exact markdown prompt the user copies alongside the MCP config. The prompt must be self-contained — when pasted into ANY agent, it must trigger the yes/no flow without additional user instruction.

The prompt should:
- Begin with: "You are now connected to Tortoise — an epistemic memory graph for agents. I want to set up my memory. Ask me these questions one at a time..."
- Probe the connection FIRST: call `tortoise_health`; if it fails, stop and report "Can't connect to Tortoise — check your API key" (P1-16)
- List the 5 yes/no questions (Q1–Q5) + Q1a free-text org prompt, with what each "yes" executes (Q5 = "coming soon" teaser, no tool)
- Record EVERY answer with `tortoise_onboarding_answer(q_id, answer)` (P1-11)
- Await GitHub authorization after Q1=yes (poll `tortoise_onboarding_github_status`) before moving to Q2 (P0-B)
- Include error handling: "If any tool call fails, tell me what went wrong and continue with the remaining questions"
- End with Verification (Q6): `tortoise_health` → `tortoise_context` → digest → first-memory fallback → `tortoise_onboarding_complete`

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

**Intent:** Finalize the question set — 5 yes/no (Q1–Q5) + 1 free-text org prompt (Q1a) + final Verification step (Q6) — define exactly what each "yes" executes (API calls, state changes), and what each "no" skips. This is the contract between the agent prompt and the backend. (Q5 team is a "coming soon" teaser with no tool/endpoint; Q6 is the Verification step, renamed from "Show me!" per plan-review.)
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
| 4 | "See the demo graph? It was seeded when you signed up — I'll show you what an epistemic graph looks like." | None |
| 5 | "Set up a team? (coming soon — you'll be able to invite collaborators later.)" | None |
| 6 | **Verification** — "Ready! Let me verify your setup and show you what Tortoise already remembers." | Always (runs regardless of answers) |

**Plan-review fix (2026-08-07):** Q5 (team) is OUT of scope per the scope doc's "single-team flow only" decision. It is reduced to a **"coming soon" teaser** — the agent still asks the question and records the answer as an interest signal (`team_interested: yes/no`), but it has **no tool, no endpoint, and no action**. Q6 is renamed **"Verification"** (it is a final step, not a question). Question count: **5 yes/no (Q1–Q5) + 1 free-text org prompt (Q1a) + final Verification step (Q6)** — within the ≤6-question target.

**Step 2: Define execution paths for each "yes"**

For each question, define the exact tool/endpoint call and expected state change:

| Question | "Yes" action | Tool/Endpoint | State change |
|----------|-------------|---------------|--------------|
| Q1 | Initiate GitHub OAuth, display auth_url, await authorization | `tortoise_onboarding_github_connect` (new MCP tool) → `tortoise_onboarding_github_status` poll | `onboarding.github_connected: true` (false + `github_error` on timeout) |
| Q2 | Start background indexing | `POST /v1/index/github` (new endpoint) | `onboarding.github_indexed: true` (set on job completion) |
| Q3 | Enable session recording | `tortoise_onboarding_session_recording` (new MCP tool) | `onboarding.session_recording: true` |
| Q4 | **Verify/show the seeded demo graph** (do NOT delete-and-overwrite) | `tortoise_onboarding_demo_create` (verify mode) → `tortoise_summarize_structure` | `onboarding.demo_verified: true` |
| Q5 | None — "coming soon" teaser (interest signal only) | — (no tool, no endpoint) | `onboarding.team_interested: true` |
| Q6 | Verification: health + digest + complete signal | `tortoise_health` → `tortoise_context` → `tortoise_onboarding_complete` | `onboarding.completed_at: <iso>` |

**Q4 demo reconciliation (plan-review P1):** the tenant-provision Supabase edge function already auto-seeds a demo graph at signup (calls `/internal/demo`, which is sentinel-idempotent — skips when `_demo_sentinel` exists). Therefore Q4=yes does NOT create a graph; it **verifies and shows** the seeded one (`tortoise_summarize_structure` + natural-language walkthrough). Delete-and-overwrite is explicitly out (it would violate the sentinel). If the seeded graph is missing (sentinel absent), the agent calls the demo endpoint once to backfill, then verifies.

**Step 3: Define skip paths for each "no"**

| Question | "No" action | State change |
|----------|------------|--------------|
| Q1 | Skip GitHub entirely (Q2 also skipped) | `onboarding.github_connected: false` |
| Q2 | (Hidden if Q1=no) | `onboarding.github_indexed: false` |
| Q3 | Skip session recording | `onboarding.session_recording: false` |
| Q4 | Skip demo walkthrough (graph still seeded) | `onboarding.demo_verified: false` |
| Q5 | Skip team teaser | `onboarding.team_interested: false` |

**Every answer is persisted (plan-review P1):** after EACH yes/no answer the agent calls `tortoise_onboarding_answer(q_id, answer)` so `question_answered` analytics + per-question state (`{q1: yes/no, q1a: "org", ...}`) are recorded even when the answer is "no". "No" answers are never silently dropped.

**Step 4: Design the verification step (Q6 — "Verification")**

Regardless of answers, Q6 always runs:
1. Agent calls `tortoise_health` to confirm connection
2. Agent calls `tortoise_context` (MCP tool wrapping `GET /v1/context`) to get memory digest
3. Agent presents digest: "Here's what Tortoise remembers: [digest]"
4. If no data sources were connected (all "no"): "Tortoise is connected but empty. Create your first memory with `tortoise_create_point()`."
5. **First-memory fallback (plan-review P1):** if the digest shows zero memories, the agent auto-creates a welcome Point — `tortoise_create_point(content="Tortoise is set up for team <team> on <date> — onboarding complete.", kind="observation")` (mirrors self-hosted `_cmd_init`) — so the user always leaves onboarding with at least one memory.
6. After the digest is shown, the agent calls **`tortoise_onboarding_complete`** — the single producer of the `onboarding_complete` funnel event: it sets `onboarding.completed_at` server-side and fires the event with `{elapsed_time_s, questions: {q1: yes/no, q1a: "org", q2: ...}, steps_completed: N}`.
7. The agent tells the user the welcome page has updated (return path — see Task 3 Step 3).

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

**Detection mechanism (plan-review P1):** the browser cannot hold the API key persistently, so define an explicit read path:
- The page polls a **client-safe progress endpoint** — `GET /v1/onboarding/state/progress` (new; returns only `{steps_completed, completed_at, github_connected, ...}` — no tokens, no raw state internals) using the `tt_` key the page already holds in memory for its display lifetime, at a **5s polling cadence** (back off to 15s after 2 min; stop at the existing 30s provisioning timeout only for key display, not for state polling).
- The Q6 digest flow must give the user a **return path to /welcome**: the prompt's final message includes "Return to your welcome page (or refresh it) to see your setup summary" and the digest block includes a link to `{BASE_APP_URL}/welcome`.
- **Intermediate UI state for the OAuth callback:** the GitHub callback redirects to `{BASE_APP_URL}/welcome?github=connected`. The page must show an intermediate "GitHub authorization received — updating your setup…" state while the agent's status poll catches up, then render the checkmark. A `?github=connected` query that arrives with no matching state yet must not flash an error.

**Step 3b: Mobile/accessibility note (plan-review P2)**
- API keys are long strings: wrap them with `overflow-wrap: anywhere` so they never overflow on narrow viewports; keep copy buttons ≥ 44×44px tap targets; harness tabs must be keyboard-accessible (arrow-key navigation + visible focus).

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
**Acceptance:** A deployable markdown file (`tortoise/onboarding/AGENT_ONBOARDING.md`) that: (a) is self-contained (works when pasted alone), (b) lists the 5 yes/no questions + 1 free-text org prompt (Q1a) + final Verification step, each with its tool call(s), (c) handles errors gracefully (including a first-step `tortoise_health` probe), (d) includes per-answer recording (`tortoise_onboarding_answer`) and the completion signal (`tortoise_onboarding_complete`), (e) passes the Phase 1 validation gate (1–2 real harness sessions before freezing), (f) is tested manually against Claude Code and at least one other harness.
**Files:**
- Create: `tortoise/onboarding/AGENT_ONBOARDING.md`
- Create: `docs/epics/2026-08-07-hosted-onboarding-235/07-agent-prompt-design.md` (design rationale doc)

**Step 1: Write the onboarding prompt**

Write the markdown block following the agent-instruction pattern from `CLAUDE.tortoise.md`. The prompt must:

```markdown
# Tortoise Onboarding — Set up your agent's memory

You are connected to Tortoise via MCP. Your goal: get my memory set up in under 5 minutes.

## Rules
- FIRST STEP: call `tortoise_health`. If it fails, STOP and report: "Can't connect to Tortoise — check your API key." Do not continue.
- Ask me these questions ONE AT A TIME. Do not ask the next until I answer.
- If I say "yes," execute the action immediately using the specified tool.
- If I say "no," skip to the next question.
- After EACH answer, call `tortoise_onboarding_answer(q_id=<id>, answer=<yes|no>)` so my progress is recorded even when the answer is "no".
- If any tool call fails, tell me what went wrong and continue with the remaining questions.
- If session recording was enabled, at the end of our conversation file it via the session-capture contract: `POST /v1/sessions` (or the hosted-appropriate tool for the harness) so this conversation is filed as memory.
- Track completion. After all questions, run verification.

## Questions

### 1. Connect GitHub?
Ask: "Would you like to connect GitHub? Tortoise can remember your issues and PRs."
If yes → ask for org/username, then call `tortoise_onboarding_github_connect(org=...)`.
It returns an auth_url. Display it to the user and say: "Open this link in your browser and authorize Tortoise, then tell me when you're done."
AWAIT AUTHORIZATION (do not move to Q2 yet): poll `tortoise_onboarding_github_status()` every 5s until `connected: true`, or until 3 minutes pass. On timeout: record `github_connected: false, github_error: "oauth not completed"` (via `tortoise_onboarding_answer`) and continue — do not loop forever.
If no → skip to Q3 (skip Q2)

### 2. Index GitHub?
(Only if Q1 was yes)
Ask: "Index your GitHub issues and PRs into memory? This runs in the background."
If yes → call `tortoise_onboarding_github_index(org=...)` — it returns a job ID. Report: "Indexing started in background — results will appear in your next session." Optionally poll `GET /v1/index/github/{job_id}` once; do not block the flow on it.
If no → continue

### 3. Record sessions?
Ask: "Tortoise will remember this agent's sessions automatically. Enable that?" (per-harness note: this captures conversations filed via the session endpoints — the exact capture mechanism depends on the harness, see the onboarding docs.)
If yes → call `tortoise_onboarding_session_recording(enabled=true)`
If no → continue

### 4. Demo graph?
Ask: "Want me to show you the demo graph? It was seeded when you signed up — Tortoise has a ready-made example of decisions and evidence."
If yes → call `tortoise_onboarding_demo_create()` (verify mode — it does NOT delete/overwrite; it backfills only if the sentinel is missing), then `tortoise_summarize_structure()` and walk me through what the graph shows.
If no → continue

### 5. Team?
Ask: "Set up a team? This is coming soon — you'll be able to invite collaborators later." (Teaser only — no tool, no action. Record my answer as interest.)

### 6. Verification (final step)
Call `tortoise_health` to verify connection.
Then call `tortoise_context` to get my memory digest.
Display: "Here's what Tortoise remembers: [digest]"
If nothing remembered: create a welcome memory via `tortoise_create_point(content="Tortoise is set up for team <name> on <date> — onboarding complete.", kind="observation")`, then show it as your first memory.
Then call `tortoise_onboarding_complete()` — this marks onboarding done and fires the completion signal.
Finally: "Return to your welcome page (or refresh it) to see your setup summary."
```

**Step 2: Write the design rationale doc**

Document: why markdown prompt vs MCP tool vs skill file, how the prompt handles error paths, what happens if the agent deviates from the script, and how to update the prompt when new questions are added.

**Step 3: Manual test plan + Phase 1 validation gate (plan-review P2)**

Test against at minimum:
1. Paste into Claude Code (with MCP already configured) — does the agent follow the script?
2. Paste into Codex or Cursor — same verification
3. Test the "all no" path — does the agent skip correctly?
4. Test with tool call failures — does the agent recover?
5. **Phase 1 validation gate:** before the prompt is frozen for Phase 2, paste the draft artifact into **1–2 real harness sessions** (Claude Code + one other) and record deviations; fold the observed fixes back into this file. This gate is part of Task 4's acceptance — the prompt is NOT final until the validation runs are clean.

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
| `POST /internal/provision` | Key provisioning (operator-only, called by Supabase tenant-provision edge function) | Reuse as-is — do NOT re-implement in `/v1/register` |
| `POST /internal/demo` | Demo graph seeding (sentinel-idempotent) | Reuse as-is — Q4 verifies, does not re-create |
| `GET /health` | Health check | Reuse as-is |
| `POST /v1/points` | Create points | Reuse as-is |
| `GET /v1/context` | Memory digest (#231) | Reuse as-is (wrapped by new `tortoise_context` MCP tool) |
| `POST /v1/sessions` | Capture sessions | Reuse as-is (session-recording capture contract) |
| `GET /v1/team` | Team info | Reuse as-is |
| `POST /v1/team/keys` | Create API keys | Reuse as-is (admin path) |
| `tortoise_health` (MCP) | MCP self-test | Reuse as-is |
| `tortoise_session_context` (MCP) | Query memory (exists) | Reuse as-is |
| `tortoise_create_point` (MCP) | Create points | Reuse as-is |
| `tortoise_summarize_structure` (MCP) | Graph stats | Reuse as-is |

> **Plan-review fix (P1-5):** `tortoise_context` does NOT exist as an MCP tool today (only `GET /v1/context` REST + `tortoise_session_context` MCP). This plan **adds** `tortoise_context` as a real MCP tool (Task 15) wrapping `GET /v1/context` in-process, so the prompt's digest step is a genuine tool call.

**Step 2: Enumerate new endpoints needed**

| New Endpoint | Method | Purpose | Inputs | Outputs | Auth |
|-------------|--------|---------|--------|---------|------|
| `/v1/register` | POST | Self-service key provisioning (creates Supabase user ONLY — key comes from the existing webhook provisioning path) | email, password (or OAuth token) | `{status: "pending"\|"provisioned"\|409, team_id?}` | None (creates account) |
| `/v1/onboarding/github/connect` | POST | Initiate GitHub OAuth | `{org, redirect_uri}` | `{auth_url}` | Bearer `tt_` |
| `/v1/onboarding/github/callback` | GET | GitHub OAuth callback | `code`, `state` | redirect to `{BASE_APP_URL}/welcome?github=connected` | None (OAuth state param) |
| `/v1/onboarding/github/status` | GET | Check GitHub connection status (agent poll) | — | `{connected, repos_count, org, error?}` | Bearer `tt_` |
| `/v1/index/github` | POST | Start background indexing | `{org, repo?}` | `{job_id, status: "started"}` | Bearer `tt_` |
| `/v1/index/github/{job_id}` | GET | Poll indexing status | — | `{job_id, status, points_created, progress}` | Bearer `tt_` |
| `/internal/demo` (existing) | POST | Create/backfill demo graph (sentinel-idempotent) | `{team_id}` | `{status: "created"\|"already_seeded"}` | Internal key |
| `/v1/onboarding/state` | GET | Get onboarding state | — | `{onboarding: {...}}` | Bearer `tt_` |
| `/v1/onboarding/state` | PATCH | Update onboarding state (per-key last-write-wins) | `{step: value}` | `{onboarding: {...}}` | Bearer `tt_` |
| `/v1/onboarding/state/progress` | GET | Client-safe progress (no tokens/raw internals) | — | `{steps_completed, completed_at, github_connected, ...}` | Bearer `tt_` (page-held key) |
| `/v1/analytics/events` | POST | Rate-limited client-side event sink (PII-scrubbed) | `{event_name, properties}` | `{ok: true}` | Bearer `tt_` + rate limit |

**Step 3: Enumerate new MCP tools needed**

| New MCP Tool | Purpose | Maps to Endpoint |
|-------------|---------|-----------------|
| `tortoise_onboarding_github_connect` | Start GitHub OAuth from agent (returns auth_url) | `POST /v1/onboarding/github/connect` |
| `tortoise_onboarding_github_status` | Poll GitHub connection status (P0-B resumption) | `GET /v1/onboarding/github/status` |
| `tortoise_onboarding_github_index` | Start indexing | `POST /v1/index/github` |
| `tortoise_onboarding_demo_create` | Verify/backfill demo graph (sentinel-respecting) | `POST /internal/demo` (internal) via in-process SDK |
| `tortoise_onboarding_session_recording` | Toggle session recording | `PATCH /v1/onboarding/state` (`session_recording`) |
| `tortoise_onboarding_state` | Get/set onboarding state | `GET/PATCH /v1/onboarding/state` |
| `tortoise_onboarding_answer` | Record one yes/no answer (P0-A) | `PATCH /v1/onboarding/state` |
| `tortoise_onboarding_complete` | Set `completed_at` + fire `onboarding_complete` (P0-A) | `PATCH /v1/onboarding/state` (`completed_at`) |
| `tortoise_onboarding_health` | Extended health (connection + onboarding status) | Composite: `GET /health` + `GET /v1/onboarding/state/progress` |
| `tortoise_context` | Memory digest (P1-5 — new tool wrapping `GET /v1/context`) | `GET /v1/context` |

**Note (plan-review P1-15):** no `tortoise_onboarding_create_team` tool — `tortoise_team_create` already exists and is deliberately **HTTP-excluded** (`http_policy=False` in the registry) for the privilege boundary; team creation stays out of the onboarding flow (Q5 teaser only).

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
| `signup_completed` | **Welcome page fires this after successful Supabase auth** (the real signup path is Supabase Auth — NOT only `/v1/register`, which is an API-only path) | `{method: "email"\|"github", timestamp}` |
| `key_provisioned` | API key generated and displayed | `{team_id, elapsed_from_signup_s}` |
| `artifact_copied` | User clicks "Copy" on welcome page | `{harness: "claude"\|"codex"\|"cursor"\|"pi", section: "config"\|"prompt"\|"both"}` |
| `agent_connected` | Agent successfully calls `tortoise_health` (MCP) | `{harness, elapsed_from_copy_s}` |
| `question_answered` | Agent records an answer via `tortoise_onboarding_answer` (yes AND no) | `{question_id, answer: "yes"\|"no"}` |
| `github_connected` | GitHub OAuth completes | `{org, repo_count}` |
| `indexing_started` | Background indexing begins | `{job_id, org, repo?}` |
| `indexing_completed` | Background indexing finishes | `{job_id, points_created, elapsed_s}` |
| `demo_verified` | Demo graph verified/shown (Q4) | `{points, operators}` |
| `session_recording_enabled` | Session recording toggled on | `{enabled: true}` |
| `onboarding_complete` | **Server-side ONLY**, when `completed_at` is set by `tortoise_onboarding_complete` (single producer — see Task 14) | `{elapsed_time_s, questions: {q1: "yes"\|"no", q1a: "org", ...}, steps_completed: N}` |
| `welcome_post_onboarding_viewed` | Welcome page renders the post-onboarding state (replaces any client-side `onboarding_complete` emission — no duplicate) | `{steps_completed}` |
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

**Recommendation (plan-review P1):** reuse the **existing audit pipeline** — `TORTOISE_AUDIT_DSN` + `AuditLogger` (`tortoise/audit_events.py`) already persists control-plane events to Postgres with a JSONL fallback. Add a `properties JSONB` column to the existing `audit_events` table (migration) and record analytics events as `operation = event_name` rows. This avoids a second table, a second writer, and duplicate RLS surface. Do NOT create a separate `analytics_events` table unless the audit table is deemed unsuitable during implementation — if so, the separate table MUST ship with RLS policies and a documented justification in the migration. No external dependency (no PostHog) for v1.

**Step 4: Save design doc**

---

## Phase 2 — Build Tasks

> Phase 2 is IN SCOPE per owner override (2026-08-08). No traction gate.
> Build tasks produce working code, deployed to the hosted platform.

### Task 7: Self-Service Key Provisioning Endpoint (`POST /v1/register`)

**Intent:** Let users get API keys without operator intervention. Currently only `POST /internal/provision` exists (operator-only, requires `FASTAPI_INTERNAL_KEY`), and Supabase's `after_user_created` auth hook ALREADY triggers tenant-provision → `/internal/provision` (creates Team + APIKey + seeds the demo graph). This task adds a public registration endpoint that does NOT double-provision.
**Acceptance:** `POST /v1/register` accepts email+password, creates a Supabase user, and returns the key produced by the existing webhook provisioning path (or a "pending" state while that runs). The existing `welcome.html` polling flow continues to work. Re-registering the same email returns 409 (no key re-exposure). Per-IP and per-email rate limiting protects the endpoint.
**Files:**
- Modify: `tortoise/hosted_api.py` — add `POST /v1/register` endpoint
- Modify: `premise-labs/welcome.html` — optionally update to use new endpoint directly
- Create: `tests/test_hosted_register.py` — integration tests
- Create: `supabase/functions/register/` — Supabase edge function (optional, alternative to direct API call)

**Step 0: Analyze the existing provisioning path (before writing any code)**
1. Read `supabase/functions/tenant-provision/index.ts` — the `after_user_created` auth hook calls `/internal/provision`, writes the plaintext key to `user_teams`, and seeds the demo graph.
2. Confirm the demo-seed call uses the WRONG path: the edge function posts to `/v1/internal/demo` but the FastAPI route is `/internal/demo` — add a task step to verify and fix that path (either the edge function URL or an alias route).
3. Conclusion: `/v1/register` must NOT call `/internal/provision` itself. Single-provisioning is the rule.

**Step 1: Write failing test**

```python
# tests/test_hosted_register.py
def test_register_new_user_creates_supabase_user():
    # mock supabase auth admin.createUser
    response = client.post("/v1/register", json={"email": "test@example.com", "password": "securepass123"})
    assert response.status_code == 201
    assert response.json()["status"] in ("pending", "provisioned")

def test_register_existing_user_returns_409():
    # Register twice → second call returns 409 and does NOT re-expose a key
    ...

def test_register_rate_limited():
    # > N requests per IP / per email within window → 429
    ...
```

**Step 2: Implement `/v1/register` in hosted_api.py**

The endpoint must:
1. Validate email format + password strength
2. **Rate limit per-IP and per-email** (e.g., 10/hour) — abuse protection; return 429 with Retry-After. Note in the plan: without this, the public endpoint is an open signup/email-bombing surface.
3. Call Supabase Admin API to create user (`supabase.auth.admin.createUser`) — **only if `after_user_created` does not already fire for admin-created users** (verify; if it does, createUser alone triggers provisioning)
4. **Return the key from the existing provisioning path** — poll the Supabase `user_teams` row (or the registry graph) for the APIKey the webhook created; do NOT generate a new key and do NOT re-run `/internal/provision`
5. Return `{status: "provisioned", api_key, team_id, graph_name}` once provisioned, or `{status: "pending", team_id?}` while the webhook is still running (the welcome page already polls — keep the polling contract; see Task 11 for the "provisioning in progress" state)
6. If the user already exists: return **409** `{message: "already registered"}` — never re-expose the key (resolves the plan's earlier contradiction between "returns the existing key" and "don't re-expose"; 409 + no key wins)

**Supabase credential + RLS analysis (plan-review P1):** hosted_api.py has ZERO Supabase connectivity today. Adding `/v1/register` introduces a `SUPABASE_SERVICE_ROLE_KEY` (or `SUPABASE_ADMIN_API_KEY`) secret — document it in the env section (`.env.example`) and the security section: service-role key must be Fly-secret-only (never client-side), and the `user_teams` table needs RLS policies reviewed (currently the edge function writes it server-side; if `/v1/register` reads it, reads must be scoped to the authenticated user or internal-only).

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
5. Scopes (v1 — **public-only decision**): `read:org, public_repo` (read-only access to issues/PRs on public repos; no `repo` scope in v1 — private repos are a future extension that adds `repo` and a consent re-flow)
6. Save client ID + client secret to Fly.io secrets (`GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`)

**Scope alignment (plan-review P1):** Step 2's auth URL MUST list the same scopes as the app registration (`read:org,public_repo`). The earlier draft contradicted itself (`read:org, read:user, repo:status` at registration vs `read:org,read:user` in the URL); `read:user` and `repo:status` are dropped — they are not needed to read issues/PRs of public repos, and `repo:status` implies private-repo access we are deliberately excluding in v1.

**Step 2: Implement connect endpoint**

```python
@app.post("/v1/onboarding/github/connect")
async def github_connect(body: GitHubConnectRequest, team: dict = Depends(get_current_team)):
    """Generate GitHub OAuth URL for the user to authorize."""
    state = secrets.token_urlsafe(32)
    # Store state → team_id mapping in the SHARED REGISTRY-GRAPH STATE STORE
    # (Task 11), NOT in-memory: Fly runs multiple replicas and in-memory
    # state would 401 every callback that lands on a different replica.
    set_state("github_oauth", {state: team["team_id"]}, ttl_minutes=15)
    auth_url = (
        f"https://github.com/login/oauth/authorize"
        f"?client_id={GITHUB_CLIENT_ID}"
        f"&redirect_uri={CALLBACK_URL}"
        f"&state={state}"
        f"&scope=read:org,public_repo"
    )
    return {"auth_url": auth_url, "state": state}
```

**Async completion contract (plan-review P0-B / P1-8):**
- `connect` returns `{auth_url, state}` immediately — it does NOT block on the browser.
- The agent displays the URL, asks the user to authorize and confirm in chat, then polls `tortoise_onboarding_github_status` (every 5s, timeout 3 min).
- The callback (Step 3) completes the flow asynchronously; the status poll observes `github_connected: true`.
- If the poll times out: record `github_connected: false, github_error: "oauth not completed"` and continue (never block the rest of onboarding).
- Indexing (`POST /v1/index/github`) called before OAuth completes must return a clear error (`409` with `detail: "GitHub not connected — complete OAuth first"`), covered by integration test `test_index_before_oauth_returns_error`.

**Step 3: Implement callback endpoint**

```python
@app.get("/v1/onboarding/github/callback")
async def github_callback(code: str, state: str):
    """Exchange OAuth code for token, store in team record."""
    # 1. Look up state in the registry-graph state store (Task 11) — 400 if unknown/expired
    # 2. Exchange code for token (GitHub token endpoint, client secret)
    # 3. Store the token ENCRYPTED (see token security below); never plaintext
    # 4. Update onboarding state: github_connected: true, github_error: null
    # 5. Fire github_connected analytics event
    return RedirectResponse(url=f"{BASE_APP_URL}/welcome?github=connected")
```

**Token security + revocation (plan-review P1-8):**
- Store the GitHub access token **encrypted at rest** (existing connector-secrets encryption pattern — see #324 connector secrets encryption) on the Team node in the registry graph, NOT in onboarding state properties.
- Revocation path: a `POST /v1/onboarding/github/disconnect` endpoint (or `PATCH /v1/onboarding/state` with `github_connected: false`) that deletes the encrypted token and records the revoke; document manual revocation via GitHub settings too.
- The token is only used by the indexer job (Task 9); the status endpoint returns `connected: bool` + repo count — never the token.

**Step 4: Add MCP tool**

Add a `ToolDefinition` entry for `tortoise_onboarding_github_connect` in `tortoise/tool_registry.py` and its handler in `tortoise/mcp_server.py` (registry-driven, in-process — see Task 15):

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

**Step 1: Audit `_cmd_index_github` FIRST (plan-review P1-17)**

`tortoise/__main__.py:_cmd_index_github` already implements the core pattern: idempotent shallow clone + `.md` walk with **content-hash dedup** (keyed via `idempotency.document_key`) so re-running the same repo skips already-indexed files. Reuse that logic rather than writing a new fetcher:
- Extract/reuse the clone + walk + dedup path; the hosted version swaps the clone source for the GitHub API (issues/PRs as JSON → same content-hash dedup).
- The v1 endpoint indexes **issues + PRs** (not just `.md` docs): issue → `kind="observation"`, PR → `kind="decision"`, with `source: "github", org, repo, issue_number` metadata.
- Indexing called before OAuth completes returns 409 (see Task 8 — integration test `test_index_before_oauth_returns_error`).

**Step 1b: Write failing test**

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

**Named rate-limit tests (mock GitHub — plan-review P1-17):**
- `test_429_backoff_retries`: mocked 429 responses → indexer backs off exponentially and eventually succeeds
- `test_etag_conditional_fetch`: `ETag`/`If-None-Match` sent; unchanged resources return 304 and consume no rate limit
- `test_500_issue_cap`: an org with >500 issues indexes at most 500 (configurable limit) without error
- `test_index_before_oauth_returns_error`: no OAuth token → clear 409 error (P0-B)

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

**Intent:** The tenant-provision Supabase edge function ALREADY auto-seeds a demo graph at signup by calling the FastAPI demo endpoint; `/internal/demo` (hosted_api.py) is sentinel-idempotent — it writes `_demo_sentinel` LAST and skips when the sentinel exists. This task therefore does NOT build a create-from-scratch public endpoint; it (a) verifies/fixes the seeding path, (b) provides the agent-side "show me / verify" step (Q4), and (c) makes the demo graph's Operator nodes explicit.
**Acceptance:** Demo seeding is verified working end-to-end (signup → `/internal/demo` → sentinel set). Q4=yes verifies and walks the user through the existing seeded graph (`tortoise_summarize_structure`). The demo graph includes at least 5 Points and 3 Operators (supports / contradicts / mitigates). Re-running NEVER deletes-and-overwrites an already-seeded graph (sentinel respected).
**Files:**
- Modify: `tortoise/hosted_api.py` — (a) fix/alias the demo route path, (b) add Operator-node creation if missing from the seed, (c) add verify-mode behavior
- Modify: `supabase/functions/tenant-provision/index.ts` — **fix the demo URL path**: it posts to `/v1/internal/demo`, but the FastAPI route is `/internal/demo` (no `/v1`) — align one way (preferred: add a `/v1/internal/demo` alias route so the edge function keeps working, or fix the edge function URL)
- Create: `tests/test_demo_graph.py`
- Modify: `tortoise/tool_registry.py` — add `tortoise_onboarding_demo_create` tool (verify mode)

**Step 1: Verify the existing seeding path**
1. Read `supabase/functions/tenant-provision/index.ts` — confirm it calls the demo endpoint with the wrong path (`/v1/internal/demo` vs route `/internal/demo`).
2. Fix the mismatch (alias route or edge-function URL change) so signup-time seeding actually lands.
3. Confirm sentinel behavior: re-running `/internal/demo` returns `{status: "already_seeded"}` and does not duplicate points.

**Step 1b: Design the demo graph narrative (kept from original plan — verify it matches the seed)**
- Point 1 (decision): "Use FalkorDB for graph storage"
- Point 2 (evidence): "FalkorDB benchmarks show 10x faster graph queries than vanilla Redis"
- Point 3 (evidence): "Existing team has Redis expertise — FalkorDB reuses Redis protocol"
- Point 4 (decision): "Deploy on Fly.io for edge distribution"
- Point 5 (evidence): "Fly.io cold starts are < 500ms for Python apps"
- Operator 1: Point 2 SUPPORTS Point 1
- Operator 2: Point 3 SUPPORTS Point 1
- Operator 3: Point 1 CONTRADICTS Point 4 (different infrastructure decisions)

**Step 1c: Specify Operator-node creation explicitly (plan-review P1-3)**
The acceptance requires Operators (supports/contradicts/mitigates). The seed must create them as explicit Operator nodes with the right relationship types (`SUPPORTS`, `CONTRADICTS`, `MITIGATES`), not just Points. Verify the current seed does this; if not, extend the seed and its tests to assert `operators_created ≥ 3`.

**Step 2: Implement verify-mode endpoint behavior**

```python
# On /internal/demo (existing): already sentinel-idempotent — keep.
# New: the MCP tool / Q4 step verifies, does not recreate:
@app.get("/v1/onboarding/demo/status")
async def demo_status(team: dict = Depends(get_current_team)):
    """Return demo-graph presence + stats. Sentinel check, no writes."""
    sdk = _make_sdk(namespace=team["team_id"])
    has_sentinel = sdk._get_proj().g.query(
        "MATCH (p:Point {id: '_demo_sentinel'}) RETURN p.id"
    ).result_set
    return {"seeded": bool(has_sentinel), "stats": summarize_structure(...)}
```

Backfill rule: if `seeded: false` and the user says yes to Q4, call `/internal/demo` once (it is idempotent) then verify. Never delete-and-overwrite.

**Step 3: Add MCP tool `tortoise_onboarding_demo_create`**

Wraps the verify/backfill flow; returns graph stats so the agent can describe what was created/seeded.

**Step 4: Write tests, commit**
- `test_demo_sentinel_idempotent`: second call → `already_seeded`, point count unchanged
- `test_demo_has_operators`: ≥ 3 Operator nodes with correct relationship types
- `test_demo_status_unseeded`: missing sentinel → `seeded: false`
- `test_edge_function_demo_path`: edge function hits a route that exists (the alias or fixed URL)

---

### Task 11: Onboarding State Tracking

**Intent:** Track what onboarding steps a user has completed so the agent prompt and welcome page can show progress. Store state as **properties on the Team node in the registry graph** (NOT Supabase — hosted_api.py has zero Supabase connectivity; `get_current_team` reads Team/APIKey from the FalkorDB registry graph, so state must live there too).
**Acceptance:** `GET /v1/onboarding/state` returns current onboarding state for the team. `PATCH /v1/onboarding/state` updates individual keys with **per-key last-write-wins** semantics. State is a flat JSON object: `{github_connected, github_indexed, session_recording, demo_verified, team_interested, q1..q5, q1a, completed_at}`. A default state exists for teams that have never called the endpoint. The MCP tools update state automatically.
**Files:**
- Modify: `tortoise/hosted_api.py` — add state endpoints (registry-graph backed)
- Modify: `tortoise/tool_registry.py` — add `tortoise_onboarding_state`, `tortoise_onboarding_answer`, `tortoise_onboarding_complete` tools
- Create: `tests/test_onboarding_state.py`

**Step 1: Store state on the Team node (registry graph)**

```python
# Read: MATCH (t:Team {id: $id}) RETURN t.onboarding_state
# Write: MATCH (t:Team {id: $id}) SET t.onboarding_state = $state
#   (state serialized as a JSON string property; same registry SDK path
#    get_current_team already uses — sdk._get_registry())
```

- **Default state:** teams with no `onboarding_state` property get the default `{github_connected: false, github_indexed: false, session_recording: false, demo_verified: false, team_interested: null, q1..q5: null, completed_at: null}` — the GET endpoint returns the default (never 404). Test: `test_default_state_returned`.
- **Why not Supabase:** hosted_api.py has no Supabase client today; the team record is the FalkorDB registry `Team` node. Using Supabase would add a second source of truth and a second credential surface (see Task 7). This also resolves the "FalkorDB OR Supabase" ambiguity in the Integration Surface Map (row 8) — **registry-graph-only**.
- **Concurrency (plan-review P1-2):** PATCH semantics are **per-key last-write-wins** — each PATCH carries a single `{key: value}` pair and the server reads-modifies-writes that key, so concurrent PATCHes of different keys do not clobber each other. (Version+409 is the alternative if per-key LWW proves insufficient during implementation.) Test: `test_concurrent_patch_different_keys`.

**Step 2: Implement endpoints**

```python
@app.get("/v1/onboarding/state")
async def get_onboarding_state(team: dict = Depends(get_current_team)):
    return {"onboarding": read_state(team["team_id"])}

@app.patch("/v1/onboarding/state")
async def update_onboarding_state(body: OnboardingStateUpdate, team: dict = Depends(get_current_team)):
    # Single {key: value} pair; merge into Team node property (per-key LWW)
    ...
```

**Step 3: Integrate state updates into other endpoints (auto-integrations — plan-review P1-11)**
- `POST /v1/onboarding/github/callback` → auto-sets `github_connected: true` (and `github_error: null`)
- `POST /v1/index/github` completion → auto-sets `github_indexed: true`
- Q4 verify (demo) → auto-sets `demo_verified: true`
- `tortoise_onboarding_session_recording` → auto-sets `session_recording: true|false`
- Q5 teaser → auto-sets `team_interested: true|false`
- `tortoise_onboarding_answer(q_id, answer)` → auto-sets `q<id>: yes|no` (and `q1a` for the free-text org) — covers ALL questions including "no" answers
- `tortoise_onboarding_complete` → auto-sets `completed_at: <iso>` (server-side `onboarding_complete` event fires here — see Task 14)

**Step 4: Write tests, commit**
- `test_default_state_returned`, `test_patch_updates_key`, `test_concurrent_patch_different_keys`, `test_complete_sets_completed_at`, `test_no_answer_recorded`

---

### Task 12: Welcome Page v2 (Dynamic, with Onboarding Artifact)

**Intent:** Update `premise-labs/welcome.html` to include the one-artifact block, per-harness instructions, and a visual onboarding flow. Replace the current "MCP config" + "Quickstart" sections with the unified onboarding artifact.
**Acceptance:** The updated welcome page shows: (a) API key card (kept), (b) the one-artifact block with per-harness tabs and a single "Copy onboarding setup" button that copies BOTH blocks with the **real API key substituted** (never the `tt_YOUR_KEY` placeholder), (c) a numbered flow diagram (1→2→3→4), (d) post-onboarding state showing completion checkmarks, driven by polling the client-safe `GET /v1/onboarding/state/progress` endpoint (5s cadence). No JavaScript framework dependency — keep the current vanilla HTML/CSS/JS pattern.
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

When the user returns to the welcome page after completing onboarding (detected by polling `GET /v1/onboarding/state/progress` — the page holds the `tt_` key in memory for its display lifetime; cadence 5s, backing off to 15s after 2 min):

```
┌─────────────────────────────────────────┐
│ 🎉 Tortoise is all set up!               │
│                                          │
│ ✅ GitHub connected (12 repos)           │
│ ✅ 847 issues indexed as memory          │
│ ✅ Session recording active              │
│ ✅ Demo graph verified (5 points, 3 ops) │
│                                          │
│ 📊 Open Dashboard →                      │
│ 📖 Read the docs →                       │
└─────────────────────────────────────────┘
```

- **OAuth callback intermediate state:** when the page loads with `?github=connected` (the GitHub callback redirect — see Task 8 Step 3), show "GitHub authorization received — updating your setup…" while the progress poll catches up; never flash an error if the state is momentarily stale.
- **Return path:** the Q6 digest (Task 4) tells the user to return/refresh `/welcome`; the page renders this state on arrival.
- **Key substitution in copy (plan-review P1-12):** the "Copy onboarding setup" action builds the clipboard text from the rendered blocks, which ALREADY contain the real key. Add an E2E assertion (`test_copy_contains_real_key`, Task 16) that the copied config contains the user's actual `tt_` key and not the placeholder.

**Step 3: Implement harness tabs**

Use CSS-only tabs (no JS framework). Each tab shows:
- The exact MCP config for that harness (Streamable HTTP format)
- The onboarding prompt (same for all harnesses, or slightly adapted)
- Harness-specific steps

**Step 4: Add analytics instrumentation**

Fire `artifact_copied` on copy button clicks (include harness + section info — section is `both` for the single-copy action). Fire `welcome_page_viewed` on page load (ready state) and `welcome_post_onboarding_viewed` when the post-onboarding state renders. The client sends events to the rate-limited `POST /v1/analytics/events` sink (PII-scrubbed — see Task 14); it must NOT emit `onboarding_complete` (that is server-side only, single producer).

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

**Step 3b: Per-harness session-capture contract (plan-review P1-4)**

The Q3 "record sessions" promise must map to a real mechanism per harness. Define in the prompt's deployment docs:
- **Capture contract:** when `session_recording=true`, the onboarding prompt instructs the agent to file each conversation end via `POST /v1/sessions` (the hosted-appropriate capture path — reuses the existing session-capture surface).
- **Scope the wording:** the user-facing question says "Tortoise will remember this agent's sessions" — with a per-harness note that capture coverage depends on the harness (Pi extensions and Claude hooks capture automatically; CLI harnesses file via the session endpoint when the prompt is active).
- The canonical prompt file must carry these per-harness notes so the Q3 answer is not a false promise (aligns E2E-6 acceptance).

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
**Acceptance:** All events from the analytics schema fire at the correct moments. Events are stored in the existing `audit_events` table (via `TORTOISE_AUDIT_DSN` + `AuditLogger`, `operation` = event name, `properties` JSONB — plan-review P1-10) with the client sink `POST /v1/analytics/events` for browser-side events. A simple dashboard query shows funnel conversion rates. No PII in event payloads.
**Files:**
- Modify: `tortoise/hosted_api.py` — add analytics event emission on key endpoints
- Modify: `premise-labs/welcome.html` — add client-side analytics events
- Create: `tests/test_analytics_events.py` — verify events fire
- Create: Postgres migration — `ALTER TABLE audit_events ADD COLUMN properties JSONB` (extend existing table; see Step 1)

**Step 1: Extend the existing audit_events table (migration) — plan-review P1-10**

Reuse the existing audit pipeline (`TORTOISE_AUDIT_DSN` + `AuditLogger` in `tortoise/audit_events.py`) instead of a new table:

```sql
ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS properties JSONB DEFAULT '{}';
CREATE INDEX IF NOT EXISTS idx_audit_event_name ON audit_events(operation, created_at);
```

Analytics events are written as `operation = event_name` rows with `properties` carrying the event payload (PII-scrubbed). This keeps one writer, one table, and the existing JSONL fallback. Only if the audit table proves unsuitable during implementation, create a separate `analytics_events` table — and then it MUST ship with RLS policies plus a documented justification in the migration.

**Step 2: Instrument hosted API**

Add a helper `_track_event(team_id, event_name, properties)` built on the existing `AuditLogger` (`_async_audit` pattern). Call it at:
- `tortoise_onboarding_complete` / state `completed_at` set → `onboarding_complete` (**server-side ONLY — single producer**; see Step 3 — the client never fires this event)
- `POST /v1/team/keys` (key displayed) → `key_provisioned`
- `POST /v1/onboarding/github/callback` → `github_connected`
- `POST /v1/index/github` → `indexing_started`
- Index job completion → `indexing_completed`
- Q4 demo verify → `demo_verified`
- Any error path → `onboarding_error`
- Add the client sink endpoint `POST /v1/analytics/events` — rate-limited (per-team, e.g. 120/min), accepts `{event_name, properties}`, **scrubs PII** (no emails, no keys, no free-text content) before persisting.

**Step 3: Instrument welcome.html**

Add client-side event tracking (sent to the rate-limited `/v1/analytics/events` sink):
- **`signup_completed` fires from the welcome page after successful Supabase auth** — the real signup path is Supabase Auth, not `/v1/register`; the client emission is the single source for this event
- Copy button clicks → `artifact_copied` (section: `both`)
- Page load (ready state) → `welcome_page_viewed`
- Post-onboarding state detected → `welcome_post_onboarding_viewed` (NOT `onboarding_complete` — that event is server-side only when `completed_at` is set, so the funnel has exactly one producer and no double-counting)

**Step 4: Write tests, commit**
- `test_onboarding_complete_fired_once` (server-side single producer)
- `test_analytics_events_pii_scrubbed`
- `test_analytics_sink_rate_limited`
- `test_signup_completed_from_welcome` (Playwright)

---

### Task 15: New MCP Tools Registration (registry-driven, per #454)

**Intent:** Register all new onboarding MCP tools in the **canonical tool registry** so they're available to the agent when it connects. The current MCP architecture is registry-driven (depends on **#454 canonical tool registry** — the branch base; `tortoise/tool_registry.py` exists at 678f694 with 58 `ToolDefinition` entries, 4 HTTP-excluded, registered programmatically via `FastMCPAdapter`). New tools are ONE `ToolDefinition` entry in `tortoise/tool_registry.py` + a handler function in `tortoise/mcp_server.py` — both MCP and REST surfaces derive from the registry automatically.

**Architecture rules (plan-review P1-1 — corrections to the earlier draft):**
- Hosted tools run **IN-PROCESS** against the team-scoped SDK (`_get_team_sdk()` — team resolved from the `TeamResolutionMiddleware` ContextVar, transport mode checked). There is **NO HTTP round-trip** from a tool back into the hosted API: no `make_request`, and NO passing `Authorization: Bearer tt_...` headers inside tool handlers — that would create a self-referential HTTP loop (the MCP server IS the hosted API process; the token is already resolved by middleware before the tool runs).
- Endpoints that are genuinely REST-facing (OAuth callback, status polling, analytics sink) stay in `hosted_api.py`; the tools call the same in-process logic directly.

**Acceptance:** 10 new MCP tools are registered and discoverable: `tortoise_onboarding_github_connect`, `tortoise_onboarding_github_status`, `tortoise_onboarding_github_index`, `tortoise_onboarding_demo_create`, `tortoise_onboarding_session_recording`, `tortoise_onboarding_state`, `tortoise_onboarding_answer`, `tortoise_onboarding_complete`, `tortoise_onboarding_health`, `tortoise_context`. Each has a docstring, typed inputs, and error handling. Tool-count totals: **58 existing registry entries (54 HTTP-visible — `tortoise_ingest_corpus`, `tortoise_team_create`, `tortoise_index_sessions`, `tortoise_backfill_v25` are HTTP-excluded) + 10 new = 68 registry entries / 64 HTTP-visible**. No `tortoise_onboarding_create_team` (Q5 is a teaser; `tortoise_team_create` exists and stays HTTP-excluded for the privilege boundary).
**Files:**
- Modify: `tortoise/tool_registry.py` — add 10 `ToolDefinition` entries
- Modify: `tortoise/mcp_server.py` — add handler functions for the new entries
- Modify: `tortoise/hosted_api.py` — add the REST side where the definition declares a `RestSpec` (state endpoints, github status, analytics sink)

**Step 1: Add ToolDefinition entries**

Each new tool is a `ToolDefinition` in `tortoise/tool_registry.py` (name, description, annotations, `http_policy`, `sdk_method` or `handler_override`, optional `rest_spec`). Handlers live in `mcp_server.py` and run in-process via `_get_team_sdk()` — no HTTP:

```python
def tortoise_onboarding_github_connect(org: str, redirect_uri: str | None = None) -> dict:
    """Start GitHub OAuth for Tortoise onboarding. Returns an auth_url to open in browser."""
    ...  # in-process: write oauth state to registry-graph store, build auth_url

def tortoise_onboarding_github_status() -> dict:
    """Poll GitHub connection status. Returns connected, repos_count, org, error?."""
    ...  # in-process: read Team node state + encrypted token presence

def tortoise_onboarding_github_index(org: str, repo: str | None = None) -> dict:
    """Index GitHub issues/PRs into Tortoise memory. Returns a job_id for polling."""
    ...  # in-process: enqueue async job (reuses _cmd_index_github pattern)

def tortoise_onboarding_demo_create() -> dict:
    """Verify (or backfill) the seeded demo graph. Never deletes an existing seed."""
    ...  # in-process: sentinel check via _get_team_sdk()

def tortoise_onboarding_session_recording(enabled: bool) -> dict:
    """Enable or disable automatic session recording."""
    ...

def tortoise_onboarding_state() -> dict:
    """Get the current onboarding progress for this team."""
    ...

def tortoise_onboarding_answer(q_id: str, answer: str) -> dict:
    """Record one yes/no answer (or free-text for q1a). Called after EVERY question."""
    ...  # P0-A: per-question state + question_answered analytics

def tortoise_onboarding_complete() -> dict:
    """Mark onboarding complete: set completed_at and fire the onboarding_complete event."""
    ...  # P0-A: single producer of the funnel's terminal event

def tortoise_onboarding_health() -> dict:
    """Extended health check: connection status + onboarding progress."""
    ...

def tortoise_context() -> dict:
    """Memory digest — wraps GET /v1/context in-process (new tool per plan-review P1-5)."""
    ...  # in-process: same path as the /v1/context REST handler
```

**Step 2: Auth is handled by middleware — do NOT pass Bearer headers inside tools**

In the hosted deployment the request is already authenticated by `TeamResolutionMiddleware` before any tool runs; handlers resolve the team-scoped SDK via `_get_team_sdk()`. Tool handlers must fail closed if the transport mode / team is unset (mirror the existing registered tools' fail-closed pattern). No tool constructs an HTTP call to the same app.

**Step 3: Run MCP self-test**

```bash
python -m tortoise.mcp_server --self-test
```
Verify all 10 new tools appear in the tool list (**total 68 registry entries; 64 HTTP-visible** on the streamable-http surface — the HTTP tool filter hides the 4 excluded tools). Pre-deploy gate uses the same counts (see Verification Plan).

**Step 4: Commit**

---

### Task 16: E2E Integration Test (Playwright — Welcome Page + Agent Prompt Smoke Test)

**Intent:** Verify the complete flow from welcome page → copy artifact → agent connection works end-to-end. This is a manual-assisted test since agent interactions can't be fully automated.
**Acceptance:** A test script or documented test run that: (a) loads the welcome page and verifies the one-artifact is displayed, (b) verifies the copy button works, (c) manually pastes the artifact into a real agent session and verifies the agent connects, (d) verifies at least one yes/no question works.
**Files:**
- Create: `tests/e2e/test_welcome_page.py` — Playwright test for welcome page
- Create: `docs/epics/2026-08-07-hosted-onboarding-235/10-e2e-test-results.md` — manual test results doc

**Step 0: Centralize environment URLs (plan-review P1-18)**

`premise-app.fly.dev` does NOT exist — the Fly app is **`tortoise-y4mjjq.fly.dev`** (per `fly.toml`). Define ONE constants block (in the test module and mirrored in the design docs) and reference it everywhere:

```python
# tests/e2e/constants.py
BASE_APP_URL = "https://tortoise-y4mjjq.fly.dev"     # Fly app (fly.toml: app = "tortoise-y4mjjq")
API_URL = "https://api.premiselabs.co"               # custom domain / API
MCP_URL = "https://api.premiselabs.co/mcp"
OAUTH_CALLBACK = f"{API_URL}/v1/onboarding/github/callback"
WELCOME_URL = f"{BASE_APP_URL}/welcome"
```

All Playwright tests use these constants (never a hardcoded host).

**Step 1: Write Playwright tests for welcome page**

```python
from constants import WELCOME_URL

def test_welcome_shows_api_key(page):
    page.goto(WELCOME_URL)
    page.wait_for_selector("#api-key")
    key = page.text_content("#api-key")
    assert key.startswith("tt_")

def test_artifact_copy_button_works(page):
    page.goto(WELCOME_URL)
    page.click("#btn-copy-mcp")
    # Verify clipboard content
    ...

def test_copy_contains_real_key(page):
    # P1-12: copied artifact contains the REAL tt_ key, never tt_YOUR_KEY
    page.goto(WELCOME_URL)
    page.click("#btn-copy-setup")
    clipboard = page.evaluate("navigator.clipboard.readText()")
    assert "tt_" in clipboard and "tt_YOUR_KEY" not in clipboard

def test_harness_tabs_switch(page):
    # Click each harness tab, verify content changes
    ...

def test_post_onboarding_state(page):
    # Mock onboarding_state/progress as completed, verify checkmarks shown
    ...
```

**Step 2: Manual smoke test protocol**

Document the manual test steps (URLs from the constants block):
1. Sign up at premiselabs.co
2. Verify welcome page loads with key and artifact
3. Click "Copy onboarding setup" (single copy — config + prompt with real key)
4. Paste into Claude Code per the harness instructions
5. Verify agent lists tortoise tools
6. Verify agent calls `tortoise_health` first, then asks Q1 ("Connect GitHub?")
7. Answer "no" to all, verify agent records each answer and calls `tortoise_health`
8. Verify agent shows memory digest (first-memory fallback applies on empty graphs)
9. Refresh the welcome page → post-onboarding state visible

**Step 3: Run tests, document results**

---

## Child Issue Candidates (Epic-Decompose — NEXT STAGE)

After plan-review approves this plan, the `epic-decompose` skill should create these child issues:

| # | Child Issue Title | Maps to Tasks | Complexity | Dependencies |
|---|-------------------|---------------|------------|--------------|
| 1 | **One-artifact trigger design + prompt** | Tasks 1, 4 | standard | None |
| 2 | **Yes/no question set + execution paths** | Task 2 | standard | #1 |
| 3 | **Welcome page v2 (design + build)** | Tasks 3, 12 | standard | #1, #2 |
| 4 | **API gap analysis + endpoints + state store** | Tasks 5, 7, 11 | complex | #2 |
| 5 | **GitHub OAuth + indexing** | Tasks 8, 9 | complex | #4 |
| 6 | **Demo graph + MCP tools** | Tasks 10, 15 | standard | #4 |
| 7 | **Analytics instrumentation** | Tasks 6, 14 | standard | #4 |
| 8 | **Agent prompt deployment + E2E test** | Tasks 13, 16 | standard | #1, #4, #6 |

**Open-issue reconciliation (plan-review P1-7 — resolve BEFORE decompose):**

| Existing issue | Covered by | Disposition |
|---------------|-----------|-------------|
| **#311** (Onboarding Tour + Demo Graph + Guided Experience) | Child issues 1, 2, 3, 6 (tour = welcome page + agent prompt; demo graph = Task 10) | **Absorb** into #3 (tour) + #6 (demo graph) — close #311 as superseded by this epic |
| **#314** (MCP Server + Skills + Agent Tools Installation) | Child issues 1, 6 (one-artifact MCP config = Tasks 1/4; tool registration = Task 15) | **Absorb** into #1 + #6 — close #314 as superseded |
| **#7727 / #7730 / #7711** (legacy numbers referenced in the spec) | These are stale identifiers from an earlier issue-tracking system; no live GitHub issues exist under these numbers | **Superseded by this epic** — drop from any spec references; the current canonical links are #235 (epic), #311, #314 |

**Decomposition rationale:**
- Issues #1–3 are design-forward; they can run in parallel once the research brief is absorbed
- Issues #4–6 are build-forward; they depend on design tasks completing
- Issue #7 (analytics) can run parallel to #4–6
- Issue #8 is the integration gate — it ties everything together with E2E tests
- **Tool-registry dependency (plan-review P1-1):** child issue #6 depends on **#454 canonical tool registry** being merged (registry exists at 678f694 on feat/454 — the branch base for this epic). All MCP-tool work in #4–#6 must target the registry-driven architecture (Task 15), never the pre-registry `mcp_server.py` pattern.

**MECE check:** Each child issue covers a distinct surface (welcome page, agent prompt, API, GitHub integration, demo, analytics, deployment) with no overlap. Collectively they cover all 16 tasks. The open-issue reconciliation above removes overlap with pre-existing issues #311/#314.

---

## Verification Plan

### Pre-Deploy Gates

| Gate | Command/Tool | Expected |
|------|-------------|----------|
| Typecheck (Python) | `python -m py_compile tortoise/*.py` | No errors |
| Unit tests | `python -m pytest tests/ -v -k "register or github or demo or onboarding or index"` | All pass |
| MCP self-test | `python -m tortoise.mcp_server --self-test` | **68 tools listed (58 existing + 10 new); 64 HTTP-visible on the streamable-http surface** (4 tools HTTP-excluded) |
| Welcome page test | `python -m pytest tests/e2e/test_welcome_page.py -v` | All pass (URLs from the constants block — `tortoise-y4mjjq.fly.dev`, never `premise-app.fly.dev`) |
| API schema check | `curl https://api.premiselabs.co/health` | `{"status": "ok"}` |

### Post-Deploy Smoke Tests

| Test | How | Pass Criteria |
|------|-----|---------------|
| Signup → key | Sign up via premiselabs.co | Welcome page shows `tt_` key within 10s |
| MCP connect | Paste MCP config into agent | `tortoise_health` returns OK (agent probes it FIRST) |
| Onboarding prompt | Paste prompt into agent | Agent calls `tortoise_health`, then asks Q1 |
| All "no" path | Answer "no" to all | Each answer recorded via `tortoise_onboarding_answer`; agent shows "Tortoise is connected"; first-memory welcome Point created on empty graph |
| Demo graph | Verify signup-time seeding | `/internal/demo` returns `already_seeded` on re-run; `tortoise_summarize_structure` shows ≥ 5 points / ≥ 3 operators |
| Context digest | Call `GET /v1/context` (and `tortoise_context` MCP tool) | Returns memory digest |
| Analytics | Check `audit_events` (operation = event name, `properties` JSONB) | `onboarding_complete` event exists, exactly one per completed onboarding |
| OAuth resumption | Q1=yes → authorize → poll | Agent polls `tortoise_onboarding_github_status` until connected; timeout path records `github_connected: false` |

### Owner Acceptance Gate

Before labeling as `complete`:
1. Owner (danielospina) walks through the full flow: signup → paste → yes/no → memory digest
2. Time-to-first-memory is measured and compared to the < 5 min target
3. At least one harness (Claude Code or Pi) completes the flow end-to-end

---

## Phase 2 Inclusion Confirmation

> ⛔ **OVERRIDE (2026-08-08):** Phase 2 is **NOT gated** on user signal. All build tasks (Tasks 7–16) are **IN SCOPE** and ship as part of this epic. The owner explicitly stated: "planning is more expensive than implementing; design decisions made here should not be re-litigated later."

Phase 1 design artifacts (Tasks 1–6) inform but do not gate Phase 2. Build tasks can begin as soon as the relevant design artifact is complete. **Partial order (plan-review P2 — replaces "Tasks 7–15 can start in parallel"):**
1. **First wave (foundations):** Task 7 (`/v1/register` + provisioning-path analysis) and Task 11 (onboarding state store — registry-graph) first — every other build task reads or writes state.
2. **Second wave (GitHub + demo):** Task 8 (OAuth, depends on state store for the state mapping) and Task 9 (indexing, depends on OAuth) and Task 10 (demo path fix, independent).
3. **Third wave (surfaces):** Task 15 (MCP tools — after their endpoints/state exist) and Task 14 (analytics — after the sink endpoint + state store exist).
4. **Fourth wave (frontend):** Task 12 (welcome page v2) and Task 13 (prompt deployment) — depend on earlier waves for the endpoints they call; Task 16 (E2E) last.

---

## Plan Review Fixes (2026-08-07)

> Changelog of the plan-review convergence pass applied to this plan (2 P0, 16 P1, 14 P2). Severity tags reference the review verdict. The same changes are reflected in `02-scope.md` where a stated decision changed (Q5 teaser, demo behavior, E2E wording).

**P0 fixes**
- **P0-A — Completion-signal producer:** the funnel's terminal event now has a producer. Task 4's prompt calls `tortoise_onboarding_answer(q_id, answer)` after EACH question and `tortoise_onboarding_complete` after the digest; Task 11 Step 3 auto-integrates per-question answers + `completed_at`; Task 14 fires `onboarding_complete` server-side (single producer) when `completed_at` is set. Target metric (>60% completion) is now measurable.
- **P0-B — GitHub OAuth resumption:** Task 4's prompt adds an "await authorization" step after Q1=yes — display auth_url, user authorizes + confirms, agent polls `tortoise_onboarding_github_status` (5s, 3-min timeout; timeout records `github_connected: false, github_error: "oauth not completed"`). E2E-3 journey map includes the poll; integration test `test_index_before_oauth_returns_error` covers indexing-before-OAuth.

**P1 fixes**
1. **Task 15 registry rewrite:** tools are `ToolDefinition` entries in `tortoise/tool_registry.py` (58 entries, 4 `http_policy=False` → 54 HTTP-visible), registered via `FastMCPAdapter`; hosted tools run IN-PROCESS via `_get_team_sdk()` — no `make_request`, no Bearer headers inside handlers. Declared the #454 dependency (registry at 678f694). All tool counts corrected (56→58 registry / 54 HTTP-visible; +10 new = 68 registry / 64 HTTP-visible; pre-deploy gate and E2E-8 updated; E2E-8 now says "all HTTP-visible tools").
2. **Task 11 state store:** onboarding state is properties on the Team node in the registry graph (hosted_api has zero Supabase connectivity); the "FalkorDB OR Supabase" OR in the Integration Surface Map (row 8) resolved to registry-graph-only; per-key last-write-wins PATCH semantics + default-state and concurrent-PATCH tests added.
3. **Task 10 / Q4 demo reconciliation:** kept signup-time seeding (tenant-provision edge function → `/internal/demo`, sentinel-idempotent); Q4 is now "show me / verify the demo graph" (no delete-and-overwrite); Operator-node creation made explicit (≥3 operators: supports/contradicts/mitigates); added a task step to fix the edge function's `/v1/internal/demo` → `/internal/demo` path mismatch.
4. **Q3 session recording:** capture contract defined — prompt files conversation end via `POST /v1/sessions` when `session_recording=true`; user-facing wording scoped ("Tortoise will remember this agent's sessions") with a per-harness note in Task 13; E2E-6 acceptance aligned.
5. **`tortoise_context`:** added as a real MCP tool in Task 15's list (wraps `GET /v1/context` in-process); Task 4 prompt, Task 5 inventory, and E2E-7 updated to use it (`tortoise_session_context` remains as-is).
6. **Task 7 `/v1/register`:** single-provisioning — creates the Supabase user only; the key comes from the existing `after_user_created` webhook provisioning (no re-provision); per-IP/email rate limiting + abuse note; Supabase service-role credential + RLS analysis added to env/security; re-registration = 409 + no key re-exposure; partial-provision "pending" state defined.
7. **Child Issue Candidates:** reconciliation table added — #311 and #314 absorbed into child issues; #7727/#7730/#7711 marked superseded (stale identifiers) — resolve BEFORE decompose; child #6 depends on #454.
8. **OAuth state + token security (Task 8):** state mapping lives in the registry-graph state store (not in-memory — Fly multi-replica); async completion contract defined (connect returns auth_url, agent polls status); token stored encrypted at rest (#324 pattern) with a disconnect/revocation path; GitHub scopes settled (v1 public-only: `read:org, public_repo`) and Step 1 vs Step 2 aligned.
9. **Welcome-page post-onboarding detection:** `GET /v1/onboarding/state/progress` client-safe endpoint + 5s polling cadence; Q6 digest includes return path to `/welcome`; intermediate UI state for the `?github=connected` callback redirect.
10. **Analytics (Task 14):** `signup_completed` fires from the welcome page after successful Supabase auth; `onboarding_complete` deduped to ONE server-side producer (client emits `welcome_post_onboarding_viewed`); client sink = rate-limited `/v1/analytics/events` (PII-scrubbed); storage reuses `audit_events` + `properties JSONB` (existing `TORTOISE_AUDIT_DSN` + `AuditLogger` pattern) — separate table only with RLS + justification.
11. **"No" answers persisted:** prompt records `{question_id: yes/no}` after every answer; Task 11 Step 3 auto-integrations cover session-recording and team steps too.
12. **`tt_YOUR_KEY` substitution:** the copy action substitutes the real key; `test_copy_contains_real_key` (Playwright/E2E) asserts the copied config has the real key, never the placeholder.
13. **One-copy contradiction:** Option A (single "Copy onboarding setup" with both blocks) is now primary; `test_one_artifact_clipboard` kept; per-harness paste instructions noted.
14. **First-memory fallback:** welcome Point auto-created at completion when the graph is empty (mirrors self-hosted `_cmd_init`); "indexing in progress — results will appear next session" message + optional job-poll added to Q2; E2E-8 success definition reconciled.
15. **Q5 (team):** dropped to a "coming soon" teaser (no tool, no endpoint); `tortoise_onboarding_create_team` removed from Task 15 (`tortoise_team_create` exists, HTTP-excluded); question count updated (5 yes/no + 1 free-text org + Verification step); Q6 renamed "Verification"; E2E-8 updated.
16. **Connection probe:** prompt's first step is a `tortoise_health` probe — on failure, stop and report "Can't connect to Tortoise — check your API key".
17. **Task 9 indexer:** audit + reuse `_cmd_index_github` (idempotent clone+walk with content-hash dedup exists); named tests for 429 backoff, ETag conditional fetch, 500-issue cap (mock GitHub).
18. **Task 16 URLs:** `premise-app.fly.dev` (does not exist) → `tortoise-y4mjjq.fly.dev` (fly.toml); environment URLs centralized in one constants block (base app URL, api.premiselabs.co, MCP URL, OAuth callback redirect) used by all Playwright tests.

**P2 fixes (applied)**
- Tool counts corrected everywhere (56→58 registry / 54 HTTP-visible; 63→65-68 family replaced by explicit totals; HTTP-visible language added).
- URLs centralized (P1-18) + note added that `premise-labs/` may be renamed to `website/` by implementation time (PR #206) — path variable note at Task 1.
- "Tasks 7–15 can start in parallel" prose replaced with an explicit 4-wave partial order (state store + register first; GitHub/demo next; MCP tools + analytics after their endpoints; frontend + E2E last).
- GitHub scopes aligned (Step 1 vs Step 2); Q6 → "Verification"; mobile/accessibility note (long-string wrapping, tab targets) added to Task 3; Phase 1 validation step (paste draft artifact into 1–2 real harness sessions before freezing the prompt) added to Task 4.
