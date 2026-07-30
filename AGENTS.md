# AGENTS.base.md — Universal Agent Instructions

> Shared base for all repos using agent-infra. Copy to your repo as `AGENTS.md` and customize. 70% of rules are universal — extend or override in repo-specific sections below.

---

## ⛔ HARD RULE: Auto-Continue — NEVER PAUSE WITHOUT A REASON

**Default: GO.** Do not stop. Do not ask. Do not wait. The session is the user's authorization — they already said "do the thing" by starting it. Your job is to keep moving until you hit a real gate.

**Forbidden:** Any question whose answer is trivially "yes" — this means:
- "Ready?" "Proceed?" "Continue?" "Shall I…?" "Want me to…?" "Should I…?"
- "On to the next step?" "Does that look right?" "Everything OK so far?"
- Any handoff where the user has nothing to decide

**Only pause if at least one is true:**
1. A skill explicitly mandates a human gate (sign-off, approval, decision point)
2. P0 consequence risk (data loss, security, unrecoverable cost >$10/mo)
3. Genuinely ambiguous — research was inconclusive (<50% confidence) and you need a decision

If none of those apply: **keep going.** The user can interrupt if they disagree.

**Auto-file rule:** When you encounter a bug, workflow gap, missed edge case, or improvement opportunity → file a GitHub issue immediately. Never ask "should I file an issue?" — just file it.

---

## ⛔ HARD RULE: Process Discipline

Your role is to work within the skills and processes framework we have explicitly designed. The skills, workflows, and tools embed the accumulated learnings from all previous work and should not be bypassed nor hacked. If there are difficulties or inefficiencies, the right process is to do the work as designated regardless and provide feedback in the reflection phase (after the work), for systematic improvement of all future runs. Following this process allows us to treat our system as a product we can evolve and eventually sell, but only if properly used instead of bypassed. If in absolute need, ask for permission to bypass before doing so.

---

## ⛔ DESIGN PRINCIPLE: Good > Easy

When choosing between two approaches, prefer the one that produces the better outcome over the one that's easier to implement. Quality of result trumps implementation convenience. Easy paths accumulate into brittle systems; good paths cost more upfront but pay back in reliability, extensibility, and user satisfaction.

---

<!-- REPO-SPECIFIC: Add your skill compliance table here. Map trigger → skill → consequence of skipping. -->

## ⛔ HARD RULE: Skill Compliance

**Skills are NON-NEGOTIABLE. No shortcuts, no "I know this one," no skipping because you're in a hurry.**

<!--
| Trigger | Must invoke | Consequence of skipping |
|---|---|---|
| Any git operation | `skills/commit-workflow/SKILL.md` | ... |
| ... | ... | ... |
-->

**Review gates are mandatory, not suggestions.** When a skill describes a review cycle, you MUST run it to convergence. Skipping a review cycle is equivalent to skipping a test suite. Fixing issues without re-dispatching the reviewer is not a review — it's a bypass. No review = no ship.

**Skill length is never an excuse.** Reading a 700-line skill costs less than missing a pre-flight check. Pi's progressive disclosure only shows skill descriptions; the `read` tool loads the full workflow with all quality gates. You do not know a workflow until you have read its SKILL.md.

---

## Skill Reading Protocol

**Skills are the ONLY path to quality-gated workflows. You MUST read them before acting.**

Every operation has mandatory quality gates in its skill file — pre-flight checks, review cycles, safety verification. Skipping the skill means skipping those gates. Pi's progressive disclosure puts skill descriptions (not content) in the system prompt. The `read` tool loads the full workflow. **Never assume you know a workflow from the description alone.**

Skill length is not an excuse — reading a 700-line skill is cheaper than bypassing a pre-flight check. Skills with review loops have mandatory quality gates. **Review cycles are not optional.** When a skill describes a review-fix loop, you run it to convergence. Fixing issues and self-declaring "done" without re-dispatching a fresh reviewer is a bypass — not a review. Only "NO ISSUES FOUND" from a fresh-context reviewer ends the cycle.

### Review Loop Protocol — MANDATORY

Skills that describe review cycles contain **mandatory quality gates**, not suggestions. Do not skip review cycles. Do not emit a plan or content as "done" until all review cycles pass clean.

#### Fresh-Context Task Dispatch

Every review cycle MUST dispatch a FRESH `task` sub-agent. The reviewer has no memory of prior cycles, no investment in defending prior fixes. This prevents confirmation bias.

- Same-model self-review in the same conversation degrades without an external signal
- The model defends prior decisions rather than critically re-evaluating
- `task` spawns `pi -p` in a new process with no session memory — the closest available proxy for an independent reviewer

#### Exit Conditions — ALL Must Be True

- [ ] Last `task` reviewer response was "NO ISSUES FOUND" (verbatim, not paraphrased)
- [ ] If cycle 1 found any issues → at least 1 re-review cycle completed
- [ ] Cycle log posted: each cycle's issues and fixes documented

#### Hard Cap

4 cycles maximum per reviewer (unless skill specifies otherwise). On cap → document remaining issues, post with `⚠️ capped at N cycles — M issues remain`, proceed.

#### FORBIDDEN — These Bypass the Quality Gate Entirely

- ❌ Run review → get issues → fix → declare done without re-dispatching reviewer
  This IS skipping the review. Fixing without re-reviewing = no review.

- ❌ Self-declare "I addressed the feedback" as completion
  Only "NO ISSUES FOUND" from a fresh reviewer is a valid exit signal.

- ❌ Re-review in the same conversation context
  Confirmation bias makes same-context re-review unreliable.
  Always use `task` for a fresh session.

---

## Response Conventions

- Begin every response with current time in `[HH:MM AM/PM]` format
- Announce skill invocations: "I'm using the [skill-name] skill to [purpose]."
- Announce sub-agent dispatches: "Dispatching sub-agent for [purpose]..."
- Announce data access before hitting external services / files outside the repo / sensitive files

---

## Research Discipline

**⛔ DO NOT call `web_search` directly. Route through the `research` skill instead.**

`research` is non-optional for any investigation that involves comparing, evaluating, deciding, or understanding something new. It provides problem reframing, adversarial queries, domain detection, and — critically — the cost gate. `web_search` has `sonar-deep-research` and `sonar-reasoning-pro` which cost $5–40+/call. The `research` skill defaults to $0.005 tools. Calling `web_search` directly bypasses this gate.

**Only exception — trivial single-fact lookup:** "What version is X?" "What port does Y use?" One answer, no analysis needed. For everything else: `research`.

**Sub-agents inherit this rule.** When dispatching sub-agents, instruct them to use the `research` skill — never let a sub-agent call `web_search` directly.

---

## Debugging Discipline

When encountering any bug, test failure, or unexpected behavior:

1. **Stop.** Do not attempt to fix it. Do not run commands to "investigate." Invoke the `debug-workflow` skill first — this applies systematic root-cause methodology. Guessing at a fix without structured diagnosis is the #1 source of regressions.
2. Present the diagnosed root cause and proposed fix for explicit approval **before writing any code.**
3. Do not proceed to implementation until the user confirms the diagnosis and approach.

This applies even for "obvious" fixes — the cost of a wrong diagnosis is higher than the cost of verification. Apparent symptoms routinely mislead; the skill enforces the methodology that finds what actually broke.

---

## Sub-agent Dispatch

Use Pi's `task` tool for all sub-agent work. Sub-agents have isolated context → construct their prompts with exactly what they need.

**⛔ Model override prohibition:** Do NOT pass `model: "claude-sonnet"` or any non-DeepSeek model to the `task` tool. Only DeepSeek is configured. Overriding will cause the sub-agent to fail with "No API key found for anthropic."

<!-- REPO-SPECIFIC: Add tool-specific exceptions here (e.g., design_reviewer for Claude Opus) -->

## Batch Implementation & Parallel Dispatch

**Never ask "sequential or parallel?" — always plan the optimal parallelization yourself.** The default is maximum parallelism. The user started the session to get work done, not to manage a task queue.

### Decomposition maps parallelism

When decomposing work (epic or multi-issue batch), explicitly map what can run in parallel:
- Scope/plan multiple independent issues simultaneously via sub-agents
- Run one issue end-to-end while scoping others in parallel
- Launch an issue as soon as its blocker is done — don't wait for the full batch

### Maximize sub-agent utilization

- While waiting for a human gate (UX approval, design review) → dispatch sub-agents for other independent work
- Non-blocking research, scoping, or implementation on unrelated issues runs in background
- The controller (you) handles human interaction; sub-agents handle everything else

### Dependency-aware launch

The `**Depends on:**` field in any child issue body (or the `Depends on` column in `epic-decompose` output) is the parallelism map — if no dependency is listed, the issue is safe for parallel dispatch.
When issue B depends on issue A's scoping/plan but not its implementation:
1. Launch issue A's scoping + issues C, D, E scoping in parallel
2. As soon as issue A's scoping returns → immediately launch issue B
3. Don't wait for C/D/E to finish — B's blocker is gone, B starts now

Implement issues directly inline where practical. Group related micro-issues into a single batch PR. For cross-session epic batches, use `epic-executor/SKILL.md`.

---

## Data Access Transparency

Announce with a brief FYI **before** accessing:

1. **External services** — MCP servers, web searches, API calls
2. **Files outside the project directory** — anything not under the current repo
3. **Sensitive files** — `.env`, credentials, keys, tokens, secrets

Format: `📡 [source] — [what] — [why]`

Does **not** apply to: routine project file reads, git operations, local shell commands, context7 doc lookups.

---

## File Pre-Existing Bugs

When you encounter a **pre-existing bug** (not introduced by your current work), **file a GitHub issue for it.** Do not treat "out of scope" as a reason to skip. Known bugs carried silently forward accumulate into build rot.

---

## Editing Rules

- **Never use sed for multi-line code changes.**
- **Never use `git add -A`** — always stage specific files.
- **Prefer the `edit` tool over `write`** for targeted changes to existing files.

## Tool Quality & Retirement

- **Two-strikes rule:** If any pipeline tool or script requires >1 manual-fix cycle per use, file a retirement issue. Don't accumulate patches.

---

## Documentation Filing Protocol

Before recording any information, find the correct home first:

1. **Behavioral rule for agents?** → in this file (`AGENTS.md`)
2. **Does a `docs/` file already cover this topic?** → Check your docs index and update that file
3. **New concept with no existing doc?** → Prefer extending an existing `docs/` file over creating a new one. If a new file is genuinely needed, register it in your docs index
4. **Raw coding gotcha** (trips you up mid-code, no natural docs home)? → One concise line in `MEMORY.md`

<!-- REPO-SPECIFIC: Add your doc routing rules (e.g., "For topic-to-file routing, see docs/00_index.md") -->

### Entity Annotation

When writing or updating any doc in `docs/`, auto-populate entity metadata from session context:

- `aboutSubjects` — from session team context, `ownedBy` in frontmatter, team detected from file path
- `aboutObjects` — from governing agreement, parent epic reference, repo name
- If ambiguous, ask: "This doc references entity X — is that correct?"
- Never leave entity fields empty when context is available

<!-- REPO-SPECIFIC: Reference your ontology doc for entity types and predicates (e.g., ONTOLOGY.md §1.1 for types, §2.2 for predicates) -->

## Memory Hygiene

- `MEMORY.md` must stay under 150 lines.
- `MEMORY.md` = raw coding gotchas only (things that bite mid-code). Not an implementation log, not a docs index.
- Format: `[category]: [what broke] → [root cause] → [the fix]`

## Memory Contracts

After key triggers, write back to the correct target. **Verifier-triggered, not agent-triggered.** Append, never rewrite. Contradictions escalate via `⚠️ CONTRADICTION:` prefix. Cross-domain: explicit only.

Format: `[category]: [what broke] → [root cause] → [the fix]`

<!-- REPO-SPECIFIC: Add your repo's triggers/targets here.
| Trigger | Target |
|---------|--------|
| Task complete (code gotcha) | `MEMORY.md` (cap 150 lines) |
| Task complete (no gotcha) | Plan doc `## Learnings` |
| Bug fixed | `docs/teams/<team>/<domain>/gotchas.md` + `MEMORY.md` |
| Session complete | Your session postmortem + `MEMORY.md` for friction patterns |
-->

<!-- REPO-SPECIFIC: Add human-gated vs agent-autonomous filing rules here -->

## Key Differences from Claude Code

| Claude Code | Pi |
|---|---|
| Agent tool / Skill tool | `task` tool for sub-agents, skills loaded from files |
| `model: sonnet/opus` frontmatter | Ignored — Pi uses its own model selection |
| `allowed-tools` with granular Bash | Use Pi's tool names: `read write edit bash grep find web_search web_fetch todo_write task` |
| MCP servers via `.mcp.json` | MCP tools available via mcp-client extension |
| `superpowers:skill-name` references | Use skill name directly (e.g., `commit-workflow`) |

---

<!-- 
REPO-SPECIFIC — Add below this line:
- Skill compliance table (trigger | skill | consequence)
- Repo-specific gates (Tortoise, DB migrations, deploys, worktrees)
- Component catalog references
- UX design gate
- Migration conventions
- CI pipeline references
- Tool-specific exceptions (design_reviewer, etc.)
- Memory contracts and filing targets
- Ponytail mode / session hooks
-->
