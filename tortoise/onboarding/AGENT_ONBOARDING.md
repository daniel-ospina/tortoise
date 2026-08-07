# Tortoise Onboarding — Set up your agent's memory

> **⛔ SINGLE SOURCE OF TRUTH:** This file is the canonical onboarding prompt.
> Issue #496 finalized question wording. Issue #502 deploys this to a stable URL.
> Do NOT create divergent copies — always edit this file.
>
> **Design doc:** `docs/epics/2026-08-07-hosted-onboarding-235/artifacts/02-question-set.md`
>
> **How to use:** Paste this entire block into your agent after adding Tortoise
> to your MCP config. The agent will guide you through 5 yes/no questions and
> set up your memory in under 5 minutes.

---

You are connected to Tortoise via MCP — an epistemic memory graph for agents.
Your goal: get memory set up so Tortoise remembers decisions, evidence, and
beliefs across sessions.

## Rules

- Ask me these questions **ONE AT A TIME**. Do not ask the next until I answer.
- If I say **"yes"**, execute the action immediately using the specified tool.
- If I say **"no"**, skip to the next applicable question.
- If any tool call fails, tell me what went wrong and continue with the remaining
  questions. Do not abort the flow.
- Track completion silently. After all questions, run the verification step.

## Step 0: Confirm connection

Before asking any questions, verify that Tortoise is reachable. Call
`tortoise_health`. If it fails: "Can't connect to Tortoise — check that your
MCP config is set up correctly and your API key is valid." Stop here.

If it succeeds: "✅ Tortoise is connected. Let's set up your memory.
I'll ask you a few questions — answer yes or no."

## Questions

### Q1 — Connect GitHub?

**Ask:** "Would you like to connect GitHub? Tortoise can remember your issues and
PRs, so when you ask about past decisions, your agent will know what happened."

**If yes:**
1. Ask: "What's your GitHub organization or username?"
2. Call `tortoise_onboarding_github_connect(org=<answer>)`. This returns an
   `auth_url` — tell the user to open it in their browser to authorize.
3. After authorization, call `tortoise_onboarding_github_status()` to confirm.
   Show: "✅ GitHub connected — [N] repos found."
4. If the tool is not available (Phase 1): "GitHub integration is being built.
   Visit your dashboard at https://app.premiselabs.co to connect GitHub. I'll
   skip ahead." Skip Q2, go to Q3.

**If no:** "Skipping GitHub. You can connect it later from your dashboard."
Skip Q2.

### Q2 — Index GitHub?

_(Only if Q1 was "yes")_

**Ask:** "Index your GitHub issues and PRs into memory? This runs in the
background — you'll see results in your next session."

**If yes:**
1. Call `tortoise_onboarding_github_index(org=<org from Q1>)`. Returns a
   `job_id`.
2. Show: "✅ Indexing started (job: [job_id]). Issues and PRs will appear in your
   memory shortly. I'll continue with the next question."
3. If the tool is not available: "GitHub indexing is coming soon. I'll skip
   ahead."

**If no:** "Skipping indexing. You can index later."

### Q3 — Record sessions?

**Ask:** "Record your agent sessions automatically? Every conversation will be
filed as memory so your agent remembers what you've discussed."

**If yes:**
1. Call `tortoise_diary_write(agent_name="system", entry="Session recording
   enabled — agent sessions will be filed as memory.", topic="onboarding")`.
2. Show: "✅ Session recording enabled. Your conversations will be saved as
   memory."
3. (Phase 2: replace with `tortoise_onboarding_session_recording(enabled=true)`)

**If no:** "Skipping session recording. You can enable it later."

### Q4 — Demo graph?

**Ask:** "Create a demo epistemic graph? I'll show you what Tortoise memory
looks like — a few decisions connected by evidence and contradictions."

**If yes:**
1. Create 5 demo points using `tortoise_create_point`:
   - `tortoise_create_point(kind="decision", content="Use FalkorDB for graph storage", props={"source": "demo"})`
   - `tortoise_create_point(kind="evidence", content="FalkorDB benchmarks show 10x faster graph queries than vanilla Redis", props={"source": "demo"})`
   - `tortoise_create_point(kind="evidence", content="Existing team has Redis expertise — FalkorDB reuses Redis protocol", props={"source": "demo"})`
   - `tortoise_create_point(kind="decision", content="Deploy on Fly.io for edge distribution", props={"source": "demo"})`
   - `tortoise_create_point(kind="evidence", content="Fly.io cold starts are under 500ms for Python apps", props={"source": "demo"})`
2. Call `tortoise_summarize_structure()` to get graph stats.
3. Show: "✅ Demo graph created — [N] points. This shows how Tortoise models
   decisions with supporting evidence."
4. (Phase 2: replace steps 1-2 with single `tortoise_onboarding_demo_create()`)

**If no:** "Skipping demo graph. You can create one later."

### Q5 — Ingest existing docs?

**Ask:** "Would you like to ingest existing documentation? If you have a
directory of markdown files, I can index them into your memory."

**If yes:**
1. Ask: "What directory contains your markdown files? Provide an absolute path."
2. Call `tortoise_ingest_corpus(directory=<path>)`. Returns
   `{ingested, updated, skipped}`.
3. Show: "✅ Ingested [N] documents into memory."
4. If the tool returns an HTTP-excluded error (hosted users): "Document
   ingestion from a directory requires the CLI. Run `tortoise ingest <directory>`
   from your terminal. I'll skip ahead."
5. (Phase 2: `POST /v1/ingest/docs` for HTTP-safe ingestion)

**If no:** "Skipping document ingestion. You can ingest docs later."

### Q6 — Show me!

_(Always runs — regardless of answers to Q1–Q5)_

1. Call `tortoise_health` to confirm the connection is still healthy.
2. Call `tortoise_session_context` to get a memory digest.
3. Call `tortoise_summarize_structure` to get graph stats.
4. Display the digest:

   ```
   🎉 Tortoise is ready! Here's what your memory looks like:

   [digest from tortoise_session_context]

   ---
   ⏱ Setup complete in under 5 minutes.
   ```

5. If no data sources were connected (all "no"):

   ```
   🎉 Tortoise is connected and ready.

   Your memory is empty — create your first memory:
   `tortoise_create_point(kind="decision", content="Your decision here")`

   Whenever you make a durable decision, tell me and I'll file it.
   ```

---

## Error recovery

If a tool is not available (not yet implemented):

- **Q1/Q2 (GitHub tools):** "GitHub integration is being built. Visit your
  dashboard at https://app.premiselabs.co to connect GitHub. I'll skip ahead."
- **Q3 (Session recording):** Uses `tortoise_diary_write` today — works. If it
  fails: "Couldn't enable session recording right now. You can enable it later."
- **Q4 (Demo graph):** Uses `tortoise_create_point` today — works. If it fails:
  "Couldn't create the demo graph right now. You can try again later."
- **Q5 (Docs ingestion):** If HTTP transport: "Document ingestion from a
  directory requires the CLI. Run `tortoise ingest <directory>` from your
  terminal. I'll skip ahead."

If `tortoise_health` fails at any point: "Lost connection to Tortoise. Check
your network and try again."

---

## For implementers

**This prompt is the FINALIZED version (#496).** Issue #502 deploys this file
to a stable URL. The design doc at
`docs/epics/2026-08-07-hosted-onboarding-235/artifacts/02-question-set.md`
contains the full question specification with execution paths, state tracking,
and error handling.

**Tool dependency table** (what needs to exist in Phase 2):

| Question | Tool (today) | Status | Tool (Phase 2) |
|----------|-------------|--------|-----------------|
| Q0 (connection) | `tortoise_health` | ✅ Live | — |
| Q1 (GitHub connect) | — | 🔜 Phase 2 | `tortoise_onboarding_github_connect` |
| Q1 (GitHub status) | — | 🔜 Phase 2 | `tortoise_onboarding_github_status` |
| Q2 (GitHub index) | — | 🔜 Phase 2 | `tortoise_onboarding_github_index` |
| Q3 (Session recording) | `tortoise_diary_write` | ✅ Live | `tortoise_onboarding_session_recording` |
| Q4 (Demo graph) | `tortoise_create_point` ×5 | ✅ Live | `tortoise_onboarding_demo_create` |
| Q5 (Docs ingestion) | `tortoise_ingest_corpus` | ⚠️ Stdio-only | `POST /v1/ingest/docs` |
| Q6 (Verification) | `tortoise_health`, `tortoise_session_context`, `tortoise_summarize_structure` | ✅ Live | `tortoise_onboarding_state` |

**Fallback behavior:** If a Phase 2 tool is unimplemented, the agent follows
the error recovery section above. This means the prompt works today — Q0, Q3,
Q4, and Q6 produce output; Q1, Q2, and Q5 cleanly skip.
