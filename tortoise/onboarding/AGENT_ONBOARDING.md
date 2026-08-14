# Tortoise Onboarding — Set up your agent's memory

> **⛔ SINGLE SOURCE OF TRUTH:** This file is the canonical onboarding prompt.
> Issue #496 finalized question wording. Issue #540 deploys this to a stable URL
> (tortoise.premiselabs.co/onboarding-prompt.md).
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
   Show: "✅ GitHub connected."
4. If the tool errors (e.g. "No team context" — GitHub OAuth is hosted-mode
   only): "GitHub connect isn't available in this mode. Visit your dashboard
   at https://app.premiselabs.co to connect GitHub. I'll skip ahead." Skip Q2,
   go to Q3.

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
3. If the tool errors: "Couldn't start indexing right now. You can index later
   from your dashboard."

**If no:** "Skipping indexing. You can index later."

### Q3 — Record sessions?

**Ask:** "Record your agent sessions automatically? Every conversation will be
filed as memory so your agent remembers what you've discussed."

**If yes:**
1. Call `tortoise_onboarding_session_recording(enabled=true)`.
2. Show: "✅ Session recording enabled. Your conversations will be saved as
   memory."
3. If the tool errors (stdio mode): fall back to `tortoise_diary_write(
   agent_name="system", entry="Session recording enabled — agent sessions will
   be filed as memory.", topic="onboarding")`.

**If no:** "Skipping session recording. You can enable it later."

### Q4 — Demo graph?

**Ask:** "Create a demo epistemic graph? I'll show you what Tortoise memory
looks like — a few decisions connected by evidence and contradictions."

**If yes:**
1. Create the demo graph with `tortoise_onboarding_demo_create()` — idempotent,
   builds a 4-layer demo (decisions + evidence + operators).
2. Call `tortoise_summarize_structure()` to get graph stats.
3. Show: "✅ Demo graph created — [N] points. This shows how Tortoise models
   decisions with supporting evidence."
4. If the tool errors (stdio mode): fall back to creating 5 demo points with
   `tortoise_create_point`:
   - `tortoise_create_point(kind="decision", content="Use FalkorDB for graph storage", props={"source": "demo"})`
   - `tortoise_create_point(kind="evidence", content="FalkorDB benchmarks show 10x faster graph queries than vanilla Redis", props={"source": "demo"})`
   - `tortoise_create_point(kind="evidence", content="Existing team has Redis expertise — FalkorDB reuses Redis protocol", props={"source": "demo"})`
   - `tortoise_create_point(kind="decision", content="Deploy on Fly.io for edge distribution", props={"source": "demo"})`
   - `tortoise_create_point(kind="evidence", content="Fly.io cold starts are under 500ms for Python apps", props={"source": "demo"})`

**If no:** "Skipping demo graph. You can create one later."

### Q5 — Ingest existing docs?

**Ask:** "Would you like to ingest existing documentation? If you have a
directory of markdown files, I can index them into your memory."

**If yes:**
1. Ask: "What directory contains your markdown files? Provide an absolute path."
2. Set expectations BEFORE calling: "Indexing can take a few minutes for large
   corpora (a few hundred files ≈ minutes on embedded; it runs in the
   background — do NOT report it as stuck)."
3. Call `tortoise_ingest_corpus(directory=<path>)`. Returns
   `{ingested, updated, skipped}`.
4. Show: "✅ Ingested [N] documents into memory."
5. If the tool returns an HTTP-excluded error (hosted users): "Document
   ingestion from a directory isn't available in hosted mode. Run
   `tortoise serve` locally and connect your agent to it (stdio) to use
   `tortoise_ingest_corpus`, or run `tortoise onboard` for the self-hosted
   setup. I'll skip ahead."
6. (HTTP-transport ingest of a directory is not supported — see error recovery)

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

## Beta feedback & bug reports

Beta tester? If something breaks or feels off, file it — feedback drives the beta.
Two channels (full guide + triage path: [docs/beta-feedback.md](../../docs/beta-feedback.md)):

- **Bug / unexpected behavior:** open [a bug report](https://github.com/daniel-ospina/tortoise/issues/new?template=bug_report.yml) —
  include the surface, expected vs actual, and the graph JSON/payload where relevant.
- **Questions, ideas, general feedback:** [GitHub Discussions](https://github.com/daniel-ospina/tortoise/discussions).

Reports are acknowledged within 2 business days.

## Error recovery

If a tool fails at runtime:

- **Q1/Q2 (GitHub tools):** "GitHub connect/index isn't available in this
  mode. Visit your dashboard at https://app.premiselabs.co to connect GitHub.
  I'll skip ahead."
- **Q3 (Session recording):** Uses `tortoise_onboarding_session_recording` (or
  `tortoise_diary_write` fallback). If it fails: "Couldn't enable session
  recording right now. You can enable it later."
- **Q4 (Demo graph):** Uses `tortoise_onboarding_demo_create` (or
  `tortoise_create_point` ×5 fallback). If it fails: "Couldn't create the demo
  graph right now. You can try again later."
- **Q5 (Docs ingestion):** If HTTP transport: "Document ingestion from a
  directory isn't available in hosted mode. Run `tortoise serve` locally and
  connect your agent to it (stdio) to use `tortoise_ingest_corpus`, or run
  `tortoise onboard` for the self-hosted setup. I'll skip ahead."

If `tortoise_health` fails at any point: "Lost connection to Tortoise. Check
your network and try again."

---

## For implementers

**This prompt is the FINALIZED version (#496).** Issue #540 deploys this file
to a stable URL (tortoise.premiselabs.co/onboarding-prompt.md) via the
`deploy-pages` workflow. The design doc at
`docs/epics/2026-08-07-hosted-onboarding-235/artifacts/02-question-set.md`
(kept on branch feat/496-onboarding-questions) contains the full question
specification with execution paths, state tracking, and error handling.

**Tool dependency table** (all onboarding tools live as of epic #235 delivery):

| Question | Tool (hosted) | Status | Stdio/local fallback |
|----------|---------------|--------|----------------------|
| Q0 (connection) | `tortoise_health` | ✅ Live | same |
| Q1 (GitHub connect) | `tortoise_onboarding_github_connect` | ✅ Live (HTTP) | unavailable (dashboard) |
| Q1 (GitHub status) | `tortoise_onboarding_github_status` | ✅ Live (HTTP) | unavailable (dashboard) |
| Q2 (GitHub index) | `tortoise_onboarding_github_index` | ✅ Live (HTTP) | unavailable (dashboard) |
| Q3 (Session recording) | `tortoise_onboarding_session_recording` | ✅ Live (HTTP) | `tortoise_diary_write` |
| Q4 (Demo graph) | `tortoise_onboarding_demo_create` | ✅ Live (HTTP) | `tortoise_create_point` ×5 |
| Q5 (Docs ingestion) | `tortoise_ingest_corpus` | ⚠️ Stdio-only | `tortoise serve` locally |
| Q6 (Verification) | `tortoise_health`, `tortoise_session_context`, `tortoise_summarize_structure` | ✅ Live | same |

**Fallback behavior:** If a tool fails, the agent follows the error recovery
section above. GitHub tools require hosted (HTTP) mode — in stdio/local mode
they return "No team context (HTTP mode required)" and the agent skips ahead
to the dashboard. Everything else works in both modes.
