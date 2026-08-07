# Onboarding Question Set — Finalized Design (#496)

> **Status:** FINALIZED. Consumed by #497 (welcome page), #498 (API endpoints), #502 (prompt deployment).
> **Contract:** #502 deploys whatever wording appears in `tortoise/onboarding/AGENT_ONBOARDING.md` — keep these two files in sync.

## Overview

5 yes/no questions + 1 auto verification step. Each "yes" maps to a real MCP tool from `tortoise/mcp_server.py` (#236 surface). Phase 2 tools that don't exist yet have explicit fallback behavior defined — the prompt works today (only Q0 + Q3 + Q4 + Q6 produce output; Q1, Q2, Q5 cleanly skip with a "coming soon" message).

**Total questions:** 5 yes/no + 1 always-run verification = 6 steps.
**Target completion time:** Under 5 minutes.
**Question dependency:** Q2 gated on Q1=yes. Q1a (text input) runs only if Q1=yes.

---

## Question Flow Diagram

```
Q0: tortoise_health() ──fail──→ "Can't connect" → STOP
  │
  ✓
Q1: "Connect GitHub?" ──yes──→ Q1a: "GitHub org/username?"
  │                             │
  │                        Q2: "Index GitHub?" ──yes──→ tortoise_onboarding_github_index()
  │                             │                        (Phase 2: POST /v1/index/github)
  │                             └──no──→ skip
  │
  ├──no──→ skip Q1a, skip Q2
  │
Q3: "Record sessions?" ──yes──→ tortoise_diary_write() + state marker
  │
Q4: "Create demo graph?" ──yes──→ tortoise_create_point() ×5 + tortoise_summarize_structure()
  │
Q5: "Ingest existing docs?" ──yes──→ CLI fallback (Phase 2: POST /v1/ingest/docs)
  │
Q6: tortoise_health() → tortoise_session_context() → display digest
  │
  └──→ 🎉 Done
```

---

## Question Specifications

### Q0 — Connection Verification (not a question, auto-runs)

| Field | Value |
|-------|-------|
| **Type** | Auto-check |
| **Tool** | `tortoise_health()` |
| **Exists today?** | ✅ Yes — `tortoise/mcp_server.py:624` |
| **HTTP-safe?** | ✅ Yes — routes through `_safe()` |
| **Returns** | `{graph_size, last_ingest, error_count, uptime}` |

**Success path:**
```
✅ Tortoise is connected. Let's set up your memory.
I'll ask you a few questions — answer yes or no.
```

**Failure path:**
```
Can't connect to Tortoise — check that your MCP config is set up
correctly and your API key is valid.
```
→ **STOP.** Do not proceed.

---

### Q1 — Connect GitHub?

| Field | Value |
|-------|-------|
| **Exact wording** | "Would you like to connect GitHub? Tortoise can remember your issues and PRs, so when you ask about past decisions, your agent will know what happened." |
| **Default** | No |
| **Dependency** | None |
| **Order** | 1st (after Q0) |

#### "Yes" execution path

1. Agent asks Q1a: "What's your GitHub organization or username?"
2. Agent calls `tortoise_onboarding_github_connect(org=<answer>)`
3. Tool returns `{auth_url, state}` — agent displays: "Open this link to authorize: [auth_url]"
4. After authorization, agent calls `tortoise_onboarding_github_status()` to confirm
5. Agent displays: "✅ GitHub connected — [N] repos found."
6. State recorded: `github_connected: true, github_org: <org>`

| Execution detail | Value |
|-----------------|-------|
| **Primary tool** | `tortoise_onboarding_github_connect(org: str)` |
| **Status check** | `tortoise_onboarding_github_status()` |
| **Tool exists today?** | ❌ No — Phase 2, Task 8 |
| **Fallback (today)** | Agent says: "GitHub integration is being built. Visit your dashboard at https://app.premiselabs.co to connect GitHub." Skip to Q3. |
| **Endpoint (Phase 2)** | `POST /v1/onboarding/github/connect` → `{auth_url, state}` |
| **State change** | `onboarding.github_connected: true` |

#### "No" execution path

1. Agent says: "Skipping GitHub. You can connect it later from your dashboard."
2. Skip Q1a and Q2.
3. State recorded: `github_connected: false`

#### Error handling

| Error | Behavior |
|-------|----------|
| Tool not available (Phase 1) | "GitHub integration is being built. Visit your dashboard at https://app.premiselabs.co to connect GitHub. I'll skip ahead." → continue to Q3 |
| OAuth denied by user | "GitHub authorization wasn't completed. You can try again later from your dashboard." → continue to Q3 |
| Network timeout | "GitHub connection timed out. You can try again later from your dashboard." → continue to Q3 |
| Org not found | "Couldn't find '[org]' on GitHub. Check the spelling and try again." → re-ask Q1a once, then skip on second failure |

---

### Q2 — Index GitHub Issues/PRs?

> **⚠️ Conditional:** This question is only asked if Q1 was "yes."

| Field | Value |
|-------|-------|
| **Exact wording** | "Index your GitHub issues and PRs into memory? This runs in the background — you'll see results in your next session." |
| **Default** | Yes (if they connected GitHub, indexing is the natural next step) |
| **Dependency** | Q1 = yes |
| **Order** | 2nd (only after Q1=yes) |

#### "Yes" execution path

1. Agent calls `tortoise_onboarding_github_index(org=<org from Q1a>)`
2. Tool returns `{job_id, status: "started"}`
3. Agent displays: "✅ Indexing started (job: [job_id]). Issues and PRs will appear in your memory shortly. I'll continue with the next question."
4. State recorded: `github_indexed: true`

| Execution detail | Value |
|-----------------|-------|
| **Primary tool** | `tortoise_onboarding_github_index(org: str, repo: str \| None)` |
| **Tool exists today?** | ❌ No — Phase 2, Task 9 |
| **Fallback (today)** | Agent says: "GitHub indexing is coming soon. I'll skip ahead." → continue to Q3 |
| **Endpoint (Phase 2)** | `POST /v1/index/github` → `{job_id, status: "started"}` |
| **Polling (Phase 2)** | `GET /v1/index/github/{job_id}` → `{status, points_created, progress}` |
| **State change** | `onboarding.github_indexed: true` |

#### "No" execution path

1. Agent says: "Skipping indexing. You can index later."
2. State recorded: `github_indexed: false`

#### Error handling

| Error | Behavior |
|-------|----------|
| Tool not available (Phase 1) | "GitHub indexing is coming soon. I'll skip ahead." → continue to Q3 |
| Rate limit (GitHub API) | "GitHub rate limit reached. Indexing will resume automatically when the limit resets." → continue to Q3 |
| Indexing timeout | "Indexing is taking longer than expected — it'll continue in the background." → continue to Q3 |

---

### Q3 — Record Agent Sessions?

| Field | Value |
|-------|-------|
| **Exact wording** | "Record your agent sessions automatically? Every conversation will be filed as memory so your agent remembers what you've discussed." |
| **Default** | Yes |
| **Dependency** | None |
| **Order** | 3rd (or 2nd if Q1=no) |

#### "Yes" execution path

1. Agent calls `tortoise_diary_write(agent_name="system", entry="Session recording enabled — agent sessions will be filed as memory.", topic="onboarding")` to create a marker Point.
2. Agent calls `tortoise_session_context()` to confirm the write succeeded.
3. Agent displays: "✅ Session recording enabled. Your conversations will be saved as memory."

**Phase 2 enhancement:** Replace step 1 with `tortoise_onboarding_session_recording(enabled=true)` which toggles the backend flag. The diary-write marker approach works today.

| Execution detail | Value |
|-----------------|-------|
| **Primary tool (today)** | `tortoise_diary_write(agent_name="system", entry="...", topic="onboarding")` |
| **Verification tool** | `tortoise_session_context()` |
| **Tool exists today?** | ✅ Yes — `tortoise/mcp_server.py:597` (diary_write), `:631` (session_context) |
| **HTTP-safe?** | ✅ Yes — both route through `_safe()` |
| **Phase 2 tool** | `tortoise_onboarding_session_recording(enabled: bool)` |
| **Phase 2 endpoint** | `POST /v1/onboarding/session-recording` → `{session_recording: bool}` |
| **State change** | `onboarding.session_recording: true` |

#### "No" execution path

1. Agent says: "Skipping session recording. You can enable it later."
2. State recorded: `session_recording: false`

#### Error handling

| Error | Behavior |
|-------|----------|
| `tortoise_diary_write` fails | "Couldn't enable session recording right now. You can enable it later." → continue to Q4 |
| `tortoise_session_context` returns error | Log it, continue — the diary write marker is sufficient |

---

### Q4 — Create a Demo Graph?

| Field | Value |
|-------|-------|
| **Exact wording** | "Create a demo epistemic graph? I'll show you what Tortoise memory looks like — a few decisions connected by evidence and contradictions." |
| **Default** | Yes |
| **Dependency** | None |
| **Order** | 4th (or 3rd if Q1=no) |

#### "Yes" execution path

Agent creates 5 Points representing a relatable tech decision scenario, then summarizes:

1. `tortoise_create_point(kind="decision", content="Use FalkorDB for graph storage", props={"source": "demo"})`
2. `tortoise_create_point(kind="evidence", content="FalkorDB benchmarks show 10x faster graph queries than vanilla Redis", props={"source": "demo"})`
3. `tortoise_create_point(kind="evidence", content="Existing team has Redis expertise — FalkorDB reuses Redis protocol", props={"source": "demo"})`
4. `tortoise_create_point(kind="decision", content="Deploy on Fly.io for edge distribution", props={"source": "demo"})`
5. `tortoise_create_point(kind="evidence", content="Fly.io cold starts are under 500ms for Python apps", props={"source": "demo"})`
6. `tortoise_summarize_structure()` → displays graph stats

Agent displays: "✅ Demo graph created — 5 points across 2 decisions and 3 evidence items. This shows how Tortoise models decisions with supporting evidence."

| Execution detail | Value |
|-----------------|-------|
| **Primary tool (today)** | `tortoise_create_point(kind, content, props={"source": "demo"})` ×5 |
| **Verification tool** | `tortoise_summarize_structure()` |
| **Tools exist today?** | ✅ Yes — `tortoise/mcp_server.py:214` (create_point), `:280` (summarize_structure) |
| **HTTP-safe?** | ✅ Yes — both route through `_safe()` |
| **Idempotency** | Dedup by content hash (`dedup=True` default). Re-running creates no duplicates. |
| **Phase 2 tool** | `tortoise_onboarding_demo_create()` — single call replacing 5 individual calls |
| **Phase 2 endpoint** | `POST /v1/demo` → `{points_created, operators_created}` |
| **State change** | `onboarding.demo_created: true` |

**Demo graph narrative** (the scenario shown to users):

| # | Kind | Content |
|---|------|---------|
| 1 | decision | "Use FalkorDB for graph storage" |
| 2 | evidence | "FalkorDB benchmarks show 10x faster graph queries than vanilla Redis" |
| 3 | evidence | "Existing team has Redis expertise — FalkorDB reuses Redis protocol" |
| 4 | decision | "Deploy on Fly.io for edge distribution" |
| 5 | evidence | "Fly.io cold starts are under 500ms for Python apps" |

Points 2+3 support decision 1. Decision 1 and decision 4 represent independent infrastructure choices.
Phase 2 adds Operators (SUPPORTS edges) between points 2→1, 3→1.

#### "No" execution path

1. Agent says: "Skipping demo graph. You can create one later."
2. State recorded: `demo_created: false`

#### Error handling

| Error | Behavior |
|-------|----------|
| Any `tortoise_create_point` call fails | "Couldn't create the full demo graph — [N] of 5 points were created. You can clean up and re-create later." → continue to Q5 |
| All create_point calls fail | "Couldn't create the demo graph right now. You can try again later." → continue to Q5 |

---

### Q5 — Ingest Existing Documentation?

| Field | Value |
|-------|-------|
| **Exact wording** | "Would you like to ingest existing documentation? If you have a directory of markdown files, I can index them into your memory." |
| **Default** | No |
| **Dependency** | None |
| **Order** | 5th (or 4th if Q1=no) |

#### "Yes" execution path

1. Agent asks: "What directory contains your markdown files? Provide an absolute path."
2. Agent calls `tortoise_ingest_corpus(directory=<path>)`
3. Tool returns `{ingested, updated, skipped}`
4. Agent displays: "✅ Ingested [N] documents into memory."

**⚠️ Transport note:** `tortoise_ingest_corpus` is HTTP-excluded (path-traversal vector — walks server filesystem). It works only over stdio transport. For hosted (HTTP) users, Phase 2 will provide `POST /v1/ingest/docs` accepting file uploads or directory URLs.

| Execution detail | Value |
|-----------------|-------|
| **Primary tool (today, stdio only)** | `tortoise_ingest_corpus(directory: str)` |
| **Tool exists today?** | ✅ Yes — `tortoise/mcp_server.py:638` |
| **HTTP-safe?** | ❌ No — returns `_http_excluded_error()` for HTTP transport |
| **Fallback (HTTP transport)** | Agent says: "Document ingestion from a directory requires the CLI. Run `tortoise ingest <directory>` from your terminal. I'll skip ahead." |
| **Phase 2 tool** | `tortoise_onboarding_ingest_docs(path: str)` or `POST /v1/ingest/docs` |
| **State change** | `onboarding.docs_ingested: true` |

#### "No" execution path

1. Agent says: "Skipping document ingestion. You can ingest docs later."
2. State recorded: `docs_ingested: false`

#### Error handling

| Error | Behavior |
|-------|----------|
| HTTP transport (tool excluded) | "Document ingestion from a directory requires the CLI. Run `tortoise ingest <directory>` from your terminal. I'll skip ahead." → continue to Q6 |
| Directory not found | "Couldn't find directory '[path]'. Check the path and try again." → re-ask once, then skip on second failure |
| No .md files found | "No markdown files found in '[path]'. Tortoise ingests .md files with YAML frontmatter." → continue to Q6 |

---

### Q6 — Verification & Memory Digest

> **Always runs** — regardless of answers to Q1–Q5.

| Field | Value |
|-------|-------|
| **Type** | Auto-verification |
| **Dependency** | Runs after all questions answered |
| **Order** | Always last |

#### Execution path

1. **Re-verify connection:** `tortoise_health()` → confirms connection is still healthy.
2. **Get memory digest:** `tortoise_session_context()` → returns `{no_prior_sessions, diary_entries, recent_points, recent_events, confidence_changes}`.
3. **Get structure summary:** `tortoise_summarize_structure()` → returns point/operator counts.

#### Display: Data-connected path (≥1 "yes" answered)

```
🎉 Tortoise is ready! Here's what your memory looks like:

[diary entries from tortoise_session_context]
[point counts from tortoise_summarize_structure]

---
⏱ Setup complete in under 5 minutes.
```

#### Display: Minimal path (all "no")

```
🎉 Tortoise is connected and ready.

Your memory is empty — create your first memory:
`tortoise_create_point(kind="decision", content="Your decision here")`

Whenever you make a durable decision, tell me and I'll file it.
```

#### Completion

Agent calls `tortoise_onboarding_state()` (Phase 2) or notes completion internally.

| Execution detail | Value |
|-----------------|-------|
| **Tools used** | `tortoise_health()`, `tortoise_session_context()`, `tortoise_summarize_structure()` |
| **Tools exist today?** | ✅ All three exist |
| **HTTP-safe?** | ✅ All three are HTTP-safe |
| **Phase 2 addition** | `tortoise_onboarding_state()` — records completion |

---

## State Tracking Schema

Each question records state on the team record (Phase 2: Supabase `teams.onboarding_state` JSONB).

```json
{
  "onboarding": {
    "github_connected": true,
    "github_org": "premise-labs",
    "github_indexed": true,
    "session_recording": true,
    "demo_created": true,
    "docs_ingested": false,
    "completed_at": "2026-08-15T14:30:00Z",
    "elapsed_time_s": 187,
    "questions_asked": 5,
    "yes_count": 4
  }
}
```

---

## Tool Dependency Matrix

| Step | Tool (today) | Status | Tool (Phase 2) | Phase 2 Task |
|------|-------------|--------|-----------------|-------------|
| Q0 | `tortoise_health` | ✅ Live | — | — |
| Q1 | — | 🔜 Phase 2 | `tortoise_onboarding_github_connect` | Task 8 |
| Q1a | — | 🔜 Phase 2 | `tortoise_onboarding_github_status` | Task 8 |
| Q2 | — | 🔜 Phase 2 | `tortoise_onboarding_github_index` | Task 9 |
| Q3 | `tortoise_diary_write` | ✅ Live | `tortoise_onboarding_session_recording` | Task 11 |
| Q4 | `tortoise_create_point` | ✅ Live | `tortoise_onboarding_demo_create` | Task 10 |
| Q5 | `tortoise_ingest_corpus` | ⚠️ Stdio-only | `tortoise_onboarding_ingest_docs` | Future |
| Q6 | `tortoise_health` + `tortoise_session_context` + `tortoise_summarize_structure` | ✅ Live | `tortoise_onboarding_state` | Task 11 |

**Today's coverage:** Q0, Q3, Q4, Q6 work end-to-end. Q1, Q2, Q5 cleanly skip with a "coming soon" message. The prompt is functional today — Phase 2 adds the GitHub and docs integrations.

---

## Design Decisions

### Why 5 questions + verification (not 6 questions)?

The draft (#495) proposed 6 questions including "Set up a team?" (Q5). For hosted onboarding, teams are auto-created at signup — asking "set up a team?" is redundant. Instead, Q5 is "Ingest existing docs?" which maps to `tortoise_ingest_corpus` and provides immediate value to users migrating from other memory systems.

### Why "Ingest docs?" over "Set up team?"

1. **Real tool exists:** `tortoise_ingest_corpus` is implemented (though HTTP-excluded, CLI works)
2. **Migration path:** Users coming from Claude Code memory, Mem0, or file-based systems have docs to ingest
3. **Team is auto-created:** Hosted onboarding creates a team at signup — no need to create another
4. **Future:** Team invites belong in the dashboard, not the 5-minute onboarding flow

### Why defaults matter

Defaults reduce decision fatigue. Q3 (sessions) and Q4 (demo) default to "yes" because they're low-cost, high-value. Q1 (GitHub), Q2 (index), and Q5 (docs) default to "no" because they require external dependencies or user input.

### Why idempotency is critical

All "yes" executions are idempotent:
- `tortoise_create_point` deduplicates by content hash (`dedup=True`)
- `tortoise_diary_write` creates a new entry each time (acceptable — marker pattern)
- `tortoise_ingest_corpus` returns `{ingested, updated, skipped}` — re-running only processes new files
- Phase 2 tools (`github_connect`, `github_index`, `demo_create`) must enforce idempotency at the endpoint level

This means users can re-run the onboarding prompt without side effects — a critical property for agent-driven flows where the agent might restart or hallucinate steps.

---

## Cross-Reference

| Consumed by | What it needs |
|-------------|---------------|
| #497 (welcome page) | Question wording for the artifact display |
| #498 (API endpoints) | Tool names + signatures for implementation |
| #502 (prompt deployment) | Final question wording — reads `AGENT_ONBOARDING.md` directly |
| `tortoise/onboarding/AGENT_ONBOARDING.md` | This file IS the canonical prompt — #496 finalizes it |

---

> **No tests needed — design task.** Verification: manual review of question wording against this doc, tool existence verified against `tortoise/mcp_server.py`.
