# Epic Research Brief — Hosted Onboarding Journey (#235)

**Date:** 2026-08-07
**Status:** draft
**Research depth:** deep (epic scope)
**Domain:** engineering + ux

---

## 1. Strategy Context

### Market Position
Tortoise sits at the intersection of agent memory and epistemic graphs. The hosted platform (api.premiselabs.co) is live as of #236 (2026-08-07), exposing 58 MCP tools over Streamable HTTP with tenant-scoped Bearer `tt_` keys. The self-hosted OSS alternative remains available for developers who want local FalkorDB.

**Competitive landscape (agent memory):**
- **Agent Memory (agent-memory.dev):** Persistent memory for coding agents. Setup flow: install → start server → connect agent → verify status. No epistemic graph, no belief propagation. Simpler but thinner.
- **Mem0:** Embedding-based memory. Different paradigm (no graph, no EP).
- **Claude Code native memory:** Project-level .claude/ memory files. File-based, no graph, no cross-session belief tracking.

**Key insight:** No competitor offers an epistemic graph with belief propagation. Tortoise's differentiator is structure, not just recall. But this means onboarding has to explain value, not just connection.

### Business Model
No pricing or paid tier exists yet. Revenue path: hosted subscriptions (team-managed memory) → enterprise. The hosted platform IS the monetization path; self-hosted is free/OSS. Onboarding is the conversion lever — users who don't connect never see value, never convert.

### User Profile (inferred)
- Technical users who configure MCP servers in Claude Code, Codex, or Cursor
- Already comfortable with terminal, config files, API keys
- Likely evaluating multiple agent memory solutions
- Time-to-value is the critical metric — if they don't see "my agent remembers" in the first session, they churn

---

## 2. UX Pattern Research

### MCP Onboarding Patterns (industry)

**Claude Code standard flow:**
```
claude mcp add --transport http tortoise https://api.premiselabs.co/mcp
```
This is the canonical "copy-paste" onboarding for MCP. The user runs one command, the agent discovers tools. No guided flow exists beyond this.

**Codex flow:**
```
codex mcp add tortoise https://api.premiselabs.co/mcp --bearer-token-env-var TORTOISE_API_KEY
```
Same pattern — one command, agent discovers tools.

**Cursor flow:** Manual `.mcp.json` or `cursor mcp add` equivalent.

**Key finding:** No MCP server in the wild has a "guided onboarding flow" beyond the config-snippet copy-paste. The pattern is: configure → tools appear → user explores. Tortoise's yes/no flow would be novel in the MCP ecosystem.

### Agent-Driven Workflow Patterns

**The paste-trigger problem:** Pasting an MCP config gives the agent tool access but does NOT make it execute a workflow. The agent needs to be told what to do. Three trigger models:

| Trigger | Mechanism | Reliability | UX |
|---------|-----------|-------------|-----|
| Config paste + prompt | User pastes config + types "start onboarding" | Medium — user must remember to type the prompt | 2-step, fragile |
| Auto-detect on connect | Agent detects new tools and offers onboarding | Low — no standard MCP "on-connect" hook exists | Best UX if it worked |
| One combined artifact | User pastes ONE thing that does both (config + workflow instruction) | Medium — depends on agent | Simplest for user |

**The one-artifact challenge across harnesses:**

| Harness | Paste mechanism | Can one artifact trigger both config + workflow? |
|---------|----------------|------------------------------------------------|
| Claude Code | `claude mcp add` CLI + prompt in chat | No — config is CLI, workflow is chat. Two surfaces. |
| Codex | `codex mcp add` CLI + prompt | Same — CLI + chat separation |
| Cursor | `.mcp.json` file + rules file | Closer — both are files, but `.mcp.json` is config, `.cursor/rules/` is instructions |
| Pi | `.pi/mcp.json` or extension | Extension can bundle config + instructions |

**Recommendation:** The one-artifact likely needs to be TWO things the user copies at once — an MCP config command AND an onboarding prompt. The welcome page should present them as a single block: "Copy both and paste them."

### Self-Hosted → Hosted Mapping

| Self-hosted step | Hosted equivalent | Keep? |
|-----------------|-------------------|-------|
| `tortoise init` | API key provisioning + `.tortoise` config write | YES — but simplified (no Docker/FalkorDB detection) |
| Index repo (.md files) | Connect GitHub → index issues/PRs/docs | YES — core use case |
| First memory demo | Demo graph / first point creation | YES — critical for "aha" moment |
| Doctor (health check) | `tortoise_health` MCP tool + connection verify | YES — already exists (#236 `GET /mcp` self-test) |
| `tortoise setup` (role config) | ❌ DROP | Hosted should NOT ask about memory_filter, role, harness selection |
| Model selection | ❌ DROP | Hosted has no model selection |
| Docker/FalkorDB setup | ❌ DROP | Hosted manages infrastructure |

**What remains:** Connect → Index → Demo → Verify. That's 4 steps, mappable to ≤6 yes/no questions.

### Yes/No Question Design (proposed set)

Based on the self-hosted mapping and whether each question is appropriate for hosted:

1. **"Connect GitHub?"** → yes/no
   - If yes: "Enter your GitHub org or username: ____"
   - Executes: GitHub app OAuth or PAT-based repo access → index issues/PRs/docs
   - Maps to: `tortoise index github` (self-hosted)

2. **"Index your GitHub issues and PRs into memory?"** → yes/no
   - If yes: "Starting indexing now — this runs in the background."
   - Executes: Background indexing of issues/PRs from connected repos
   - Maps to: `tortoise onboard` Step 3 (index repo)

3. **"Record agent sessions automatically?"** → yes/no
   - If yes: "Session capture enabled. Every conversation will be filed as memory."
   - Executes: Enable session recording (agent-side hook or MCP tool)
   - Maps to: New hosted capability (self-hosted has `tortoise session capture`)

4. **"Create a demo graph so you can see how it works?"** → yes/no
   - If yes: Creates demo points/operators showing graph structure
   - Executes: `tortoise_create_point` × N + `tortoise_create_operator` × M
   - Maps to: `tortoise demo` (self-hosted Step 4)

5. **"Set up your first team?"** → yes/no
   - If yes: Creates a team, generates invite link
   - Executes: Team creation via control-plane API
   - Maps to: New hosted capability

6. **"Show me what Tortoise already remembers?"** → yes/no (always yes after setup)
   - Executes: `tortoise context` → prints memory digest
   - Maps to: `tortoise context` (#231)

**Total: 6 questions (meets the ≤6 target).**

---

## 3. Workflow Pattern Research

### Self-Hosted Onboarding Flow (mined from tortoise/__main__.py)

```
tortoise onboard:
  Step 1/5: Ensure SDK installed
  Step 2/5: Initialize graph (auto-detect Docker → embedded fallback)
  Step 3/5: Index repository (detect git repo, count .md files, index)
  Step 4/5: First memory demo (create sample points)
  Step 5/5: Health check (write/read/delete test)
  → "Onboarding complete. Agents can now: query, file decisions, auto-capture"
  → "Next: tortoise serve (MCP) | tortoise setup (role config)"
```

Key properties:
- **Non-interactive:** `--yes` flag skips prompts, idempotent
- **5 banners:** Clean progress display
- **Auto-detection:** Git repo, .md files, Docker vs embedded
- **Prints next steps:** Clear handoff to MCP server + role config

### What Changes for Hosted

| Property | Self-hosted | Hosted |
|----------|------------|--------|
| Init | Docker/embedded FalkorDB detection | API key validation → .tortoise config |
| Index | Local .md files | GitHub issues/PRs (remote API) |
| Demo | Create sample points locally | Create demo graph via MCP/API |
| Doctor | Local write/read/delete | `GET /mcp` health + `tortoise_health` tool |
| Setup | Interactive role memory_filter | ❌ Not needed (hosted simplifies) |
| Harness | Pi/Claude/Codex/Cursor detection | One artifact works across all |
| Idempotency | Re-running skips done steps | Same — state tracked via API |

### Agent-Side Workflow Execution

The agent needs to run the onboarding workflow. Options:

**Option A: MCP tool (`tortoise_onboard_hosted`)**
- One tool call triggers the entire yes/no flow
- Pros: Simple, one surface, works across harnesses
- Cons: MCP tools can't ask questions interactively (they return results, not prompt users). The agent would need to orchestrate the Q&A using the tool's output.

**Option B: Agent skill/prompt**
- A CLAUDE.md block or agent prompt that the agent follows
- Pros: Natural agent interaction, no new tool needed
- Cons: Fragile — agent may skip steps, hallucinate, or deviate

**Option C: Hybrid (MCP tool + agent prompt)**
- MCP tool does the heavy lifting (GitHub connect, indexing, demo creation)
- Agent prompt provides the conversational flow (asking yes/no, interpreting answers)
- Pros: Best of both — reliable backend + natural UX
- Cons: More complex to design

**Recommendation:** Option C. The MCP tool handles stateful operations (API calls, key management, indexing). The agent handles the conversation. The paste-able artifact includes both the MCP config AND the onboarding prompt.

---

## 4. Tech Stack Research

### Current Infrastructure (post-#236)

| Component | Status | Notes |
|-----------|--------|-------|
| MCP Server | ✅ Live | 58 tools, Streamable HTTP, Bearer auth |
| API (`api.premiselabs.co`) | ✅ Live | `/mcp` (GET, no auth), `/v1/team`, `/v1/points`, `/v1/context` |
| Key provisioning | ⚠️ Operator-only | `POST /internal/provision` with `FASTAPI_INTERNAL_KEY` |
| Self-service key delivery | ❌ Gap | #235 must design this |
| Welcome page | ⚠️ Minimal | Static HTML, MCP config snippet, no guided flow |
| GitHub integration | ❌ Not built | No GitHub OAuth, no issue/PR indexing for hosted |
| Session recording | ⚠️ CLI exists | `tortoise session capture` works for local, hosted endpoint unclear |
| Team management | ⚠️ In progress | Control-plane data model (#7714), registry graph |

### What Needs Building (Phase 2 candidates)

1. **Self-service key delivery:** Users need API keys without operator intervention. `POST /v1/register` → returns API key. Email verification optional for v1.

2. **GitHub OAuth app:** Tortoise needs a GitHub OAuth app for repo access. User authorizes → Tortoise gets read-only access to issues/PRs. No PAT management burden on user.

3. **Background indexing endpoint:** `POST /v1/index/github` — accepts org/repo, indexes issues/PRs as Points in the background. Returns job ID for status polling.

4. **Demo graph endpoint:** `POST /v1/demo` — creates a curated set of Points and Operators showing the graph's capabilities. Idempotent (re-running overwrites, doesn't duplicate).

5. **Onboarding state tracking:** API needs to track what steps a user has completed. Simple JSON state on the user/team record: `{"onboarding": {"github_connected": true, "indexed": true, "demo_created": false}}`.

6. **Welcome page v2:** Dynamic page that shows onboarding progress, generates the one-artifact, and guides the user through the flow. Replaces current static HTML.

7. **Onboarding agent prompt:** A markdown block (like `CLAUDE.tortoise.md`) that the user's agent follows. Contains the yes/no flow logic, tool calls, and verification steps.

### Integration Points

```
User signup → Welcome page → One-artifact (MCP config + prompt)
    → User pastes into agent → Agent follows prompt
    → Agent calls MCP tools (tortoise_health, tortoise_create_point, etc.)
    → Agent calls onboarding-specific tools (GitHub connect, demo create)
    → Agent verifies: tortoise_context → shows memory digest
    → Done: first memory written, funnel tracked
```

---

## 5. Assumptions Register

| # | Assumption | Confidence | Source | Validation Plan |
|---|-----------|-----------|--------|-----------------|
| A1 | Users will paste the MCP config AND the onboarding prompt as a single action | LOW | Align decision | Prototype the artifact, test with 1-2 users. If users only paste the config (not the prompt), the yes/no flow never starts. |
| A2 | Agents reliably execute multi-step workflows from pasted prompts | LOW | Align decision | Test with Claude Code + Codex. Measure: does the agent complete all 6 yes/no questions or stop halfway? |
| A3 | Hosted users exist in sufficient volume to justify building onboarding automation | LOW | Align decision; very early stage (single-digit users) | Phase 2 gate: ≥5 signups/week or committed acquisition push before build. |
| A4 | Onboarding smoothness is the binding constraint — not product value clarity | MEDIUM/LOW | Align decision | White-glove onboard 2-3 users. If they churn despite smooth setup, it's a product problem, not an onboarding problem. |
| A5 | GitHub issues/PRs and session recording are the right first data sources | LOW | Epic body | User research: ask early users what they'd want Tortoise to remember. Alternatives: documents, Slack, Linear/Jira, manual points. |
| A6 | MCP-over-HTTP works reliably across Claude Code, Codex, and Cursor | MEDIUM | #236 research section ("partially verified") | Test MCP connection + tool calls on all 3 harnesses with hosted API. |
| A7 | 58 MCP tools won't overwhelm users after onboarding | LOW | Tool count | Track which tools get called in first session. If <5 tools account for >90% of calls, consider namespace or progressive disclosure. |
| A8 | Self-hosted `tortoise onboard` is a valid model for hosted onboarding | MEDIUM | Epic body | Evidence-appropriateness caveat: self-hosted users are OSS developers; hosted population is unproven and different. Validate with real hosted users. |
| A9 | ≤6 yes/no questions hit the right UX sweet spot | LOW | Epic body (target: ≤6) | Prototype and A/B test: 6 questions vs 3 questions vs "just do it all automatically." |
| A10 | One combined artifact (MCP config + prompt) works across Pi, Claude Code, Codex, and Cursor | LOW | Design assumption | Test on each harness. Different paste mechanisms may require different artifacts. |
| A11 | The welcome page is the right surface for the one-artifact | MEDIUM | Current UX | A/B test: welcome page vs email vs in-app modal. Welcome page is the natural first touch after signup. |
| A12 | Bearer `tt_` key auth is sufficient for onboarding (OAuth not needed for v1) | HIGH | #236 delivered this, working | Already validated — MCP self-test passes. OAuth is future enhancement, not launch blocker. |
| A13 | GitHub API indexing (issues/PRs) is technically feasible at acceptable cost/complexity | LOW | New capability — no prior implementation | Spike: index a real org's issues/PRs. Measure: rate limit hits, API cost, noise ratio (how many issues produce useful memory vs spam). GitHub OAuth app registration is a prerequisite — has administrative overhead. |
| A14 | The specific 6-question set (GitHub connect, index, sessions, demo, team, context) is the RIGHT set for first-time users | LOW | Design intuition, not validated | User research: ask early users which of these they'd value. Alternative: auto-enable everything, then let users disable what they don't want (opt-out instead of opt-in). |
| A15 | Time-to-first-memory < 5 min is achievable with the proposed flow | LOW | Epic target, untested | Clock a prototype: signup → paste → first memory. Identify bottlenecks (GitHub OAuth, indexing latency, key provisioning). The <5 min target may be unachievable if GitHub OAuth requires app approval. |

---

## Summary of Key Findings

1. **No MCP server in the wild has a guided onboarding flow.** Tortoise's yes/no flow would be novel. This is both an opportunity (differentiation) and a risk (untested pattern).

2. **The "one paste-able artifact" is likely TWO things:** an MCP config command AND an onboarding prompt. Different harnesses have different paste surfaces (CLI vs chat vs file). One true single artifact may not be achievable.

3. **The self-hosted flow maps cleanly to 4 hosted steps:** Connect → Index → Demo → Verify. The 6 yes/no questions add GitHub, team, and session-recording as hosted-specific additions.

4. **Three trigger models exist,** none ideal: (a) config + separate prompt, (b) auto-detect on connect (no standard exists), (c) one combined artifact. Option (a) is the most reliable; Option (c) is the UX north star.

5. **Self-service key delivery is the #1 gap.** Users can't get API keys without operator intervention today. This blocks any self-serve onboarding flow.

6. **58 MCP tools may cause discoverability problems.** No progressive disclosure exists. Users see all tools immediately. An onboarding flow that guides users to the first 3-5 tools would help.
