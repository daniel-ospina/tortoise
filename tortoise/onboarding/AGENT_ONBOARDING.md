# Tortoise Onboarding — Set up your agent's memory

> **⛔ SINGLE SOURCE OF TRUTH:** This file is the canonical onboarding prompt.
> Issue #496 finalizes question wording. Issue #502 deploys this to a stable URL.
> Do NOT create divergent copies — always edit this file.
>
> **How to use:** Paste this entire block into your agent after adding Tortoise
> to your MCP config. The agent will guide you through ≤6 yes/no questions and
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

**If no:** "Skipping GitHub. You can connect it later." Skip Q2.

### Q2 — Index GitHub?

_(Only if Q1 was "yes")_

**Ask:** "Index your GitHub issues and PRs into memory? This runs in the
background — you'll see results in your next session."

**If yes:**
1. Call `tortoise_onboarding_github_index(org=<org from Q1>)`. Returns a
   `job_id`.
2. Show: "✅ Indexing started (job: [job_id]). Issues and PRs will appear in your
   memory shortly. I'll continue with the next question."

**If no:** "Skipping indexing. You can index later."

### Q3 — Record sessions?

**Ask:** "Record your agent sessions automatically? Every conversation will be
filed as memory so your agent remembers what you've discussed."

**If yes:**
1. Call `tortoise_onboarding_session_recording(enabled=true)`.
2. Show: "✅ Session recording enabled. Your conversations will be saved as
   memory."

**If no:** "Skipping session recording. You can enable it later with
`tortoise_onboarding_session_recording(enabled=true)`."

### Q4 — Demo graph?

**Ask:** "Create a demo epistemic graph? I'll show you what Tortoise memory
looks like — a few decisions connected by evidence and contradictions."

**If yes:**
1. Call `tortoise_onboarding_demo_create()`. Returns counts.
2. Show: "✅ Demo graph created — [N] points, [M] operators. This shows how
   Tortoise models decisions with supporting and contradicting evidence."

**If no:** "Skipping demo graph. You can create one later with
`tortoise_onboarding_demo_create()`."

### Q5 — Team?

**Ask:** "Set up a team? You can invite collaborators to share memory."

**If yes:**
1. Call `tortoise_onboarding_create_team(name="My Team")`. Returns an invite
   link.
2. Show: "✅ Team created. Invite link: [link]"

**If no:** "Skipping team setup. You're using Tortoise solo — you can create
a team later."

### Q6 — Show me!

_(Always runs — regardless of answers to Q1–Q5)_

1. Call `tortoise_health` to confirm the connection is still healthy.
2. Call `tortoise_context` to get a memory digest. If `tortoise_context` is
   not available, fall back to `tortoise_diary_read` or
   `tortoise_summarize_structure`.
3. Display the digest:

   ```
   🎉 Tortoise is ready! Here's what your memory looks like:

   [digest from tortoise_context]

   ---
   ⏱ Setup complete in under 5 minutes.
   ```

4. If no data sources were connected (all "no"):

   ```
   🎉 Tortoise is connected and ready.

   Your memory is empty — create your first point:
   `tortoise_create_point(kind="decision", content="Your decision here")`

   Whenever you make a durable decision, tell me and I'll file it.
   ```

5. Call `tortoise_onboarding_state()` and note the completion.

---

## Error recovery

If a tool is not available (not yet implemented):

- **Q1/Q2 (GitHub tools):** "GitHub integration is coming soon. I'll skip ahead."
- **Q3 (Session recording):** "Session recording is coming soon. I'll skip ahead."
- **Q4 (Demo graph):** "Demo graph is coming soon. I'll skip ahead."
- **Q5 (Team):** "Team setup is coming soon. I'll skip ahead."

If `tortoise_health` fails at any point: "Lost connection to Tortoise. Check
your network and try again."

---

## For implementers

**This prompt is the DRAFT (#495).** Issue #496 finalizes the question wording
and flow. Issue #502 deploys this file to a stable URL.

**Tool dependency table** (what needs to exist in Phase 2):

| Question | Tool | Status |
|----------|------|--------|
| Q0 (connection) | `tortoise_health` | ✅ Exists (MCP tool) |
| Q1 (GitHub connect) | `tortoise_onboarding_github_connect` | 🔜 Phase 2 (Task 8) |
| Q1 (GitHub status) | `tortoise_onboarding_github_status` | 🔜 Phase 2 (Task 8) |
| Q2 (GitHub index) | `tortoise_onboarding_github_index` | 🔜 Phase 2 (Task 9) |
| Q3 (Session recording) | `tortoise_onboarding_session_recording` | 🔜 Phase 2 (Task 11) |
| Q4 (Demo graph) | `tortoise_onboarding_demo_create` | 🔜 Phase 2 (Task 10) |
| Q5 (Team) | `tortoise_onboarding_create_team` | 🔜 Phase 2 (Task 11) |
| Q6 (Verification) | `tortoise_health`, `tortoise_context` | ✅ Exist |
| Completion | `tortoise_onboarding_state` | 🔜 Phase 2 (Task 11) |

**Fallback behavior:** If a tool is unimplemented, the agent follows the error
recovery section above — reports the tool is coming soon and continues. This
means the prompt works even before Phase 2 tools are built (only Q0 + Q6
produce output; Q1–Q5 cleanly skip).
