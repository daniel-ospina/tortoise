# AGENTS.base.md — Universal Agent Instructions

> Shared base for all repos using agent-infra. Copy to your repo as `AGENTS.md` and customize. 70% of rules are universal — extend or override in repo-specific sections below.

> ⛔ **Prerequisite:** `AGENT_INFRA_PATH` must be set in your shell profile (e.g., `~/.zshrc`).
> The auto-sync extension, pre-commit version gate, and bootstrap CLI all require it.
> Run `echo $AGENT_INFRA_PATH` to verify. See [agent-infra README](https://github.com/premise-labs/agent-infra#prerequisites) for setup.

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

## ⛔ HARD RULE: Fix Broken Infrastructure — Never Silently Work Around It

**When a dependency is broken (MCP server down, database unreachable, API returning errors, connection failing, auth broken), you MUST fix the root cause OR get explicit human authorization to change the plan/workflow.** Do NOT silently change approach, point at a different backend, enable a fallback, or "make it work" with a workaround without either (a) fixing the actual breakage, or (b) human sign-off on the change.

**This rule exists because of a real incident (2026-08-05):** the planned FalkorDB Cloud connection was failing (#7795). Instead of debugging the connection, an agent silently shipped a self-hosted FalkorDB container on Fly.io with AOF disabled and no off-box backup. That fallback had no durability — a later test run wiped the production graph (5,748 points) and it was only partially recoverable. A single unresolved failure compounded into permanent data loss because the workaround was never flagged for human review.

**The pattern to follow when something is broken:**
1. **Diagnose first** — read the error, trace the root cause, confirm what's actually failing (skills: `debug-workflow`, `find-bugs`)
2. **Fix the root cause** — reconnect, repair config, fix the bug. This is the default.
3. **If you cannot fix it** (needs credentials, external service access, decision) — **STOP and escalate**: report the diagnosis + proposed fallback to the human, get explicit approval BEFORE changing the architecture, backend, or workflow
4. **Never ship a fallback as if it were the plan** — a workaround (embedded DB instead of managed, self-host instead of cloud, local instead of remote) is a red flag that must be surfaced, not absorbed

**Signs you are working around instead of fixing:**
- Changing which backend/service a system points at (cloud → self-hosted, remote → local, prod → test) to make a test pass or a deploy succeed
- Enabling a "fallback mode" that wasn't in the approved plan
- "It works now" after switching to a different service, with no explanation of why the original failed
- Disabling a failing check instead of repairing the cause

**If you catch yourself doing any of these, STOP.** Diagnose the original failure, fix it, or get human approval for the deviation. A silently-switched backend is how "temporary" becomes "production" — and how data dies.

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

Skill length is not an excuse — reading a 700-line skill is cheaper than bypassing a pre-flight check. Skills with review loops have mandatory quality gates. **Review cycles are not optional.** When a skill describes a review-fix loop, you run it to convergence. Fixing issues and self-declaring "done" without re-dispatching a fresh reviewer is a bypass — not a review. Only "NO ISSUES FOUND" from a fresh-context reviewer — or the skill's own defined clean verdict — is a **clean completion**; convergence and cap exits **that leave issues unresolved** are escalation exits, never completions (see Hard Cap).

### Review Loop Protocol — MANDATORY

Skills that describe review cycles contain **mandatory quality gates**, not suggestions. Do not skip review cycles. Do not emit a plan or content as "done" until all review cycles pass clean, or the skill's own escalation path (cap, convergence, stall, or abort) is followed with the remaining issues documented — a capped exit is never reported as clean.

#### Fresh-Context Task Dispatch

Every review cycle MUST re-review in a FRESH context — via `task` where the skill dispatches one, or the skill's mandated mechanism (its MCP wrapper, or its verifier subagent). The reviewer has no memory of prior cycles, no investment in defending prior fixes. This prevents confirmation bias.

- Same-model self-review in the same conversation degrades without an external signal
- The model defends prior decisions rather than critically re-evaluating
- `task` spawns `pi -p` in a new process with no session memory — the closest available proxy for an independent reviewer

#### Exit Conditions — ALL Must Be True (Clean Completion)

- [ ] Last reviewer response was the skill's clean verdict — `NO ISSUES FOUND`, or the skill's defined equivalent (e.g. the verifier's `PASS`, the loop's `CLEAN`) — verbatim, not paraphrased
- [ ] If cycle 1 found any issues → at least 1 re-review cycle completed
- [ ] Cycle log posted: each cycle's issues and fixes documented

These conditions define a **clean completion** only. A convergence, stall, abort, or cap exit **that leaves issues unresolved** cannot satisfy them: it is an **escalation** exit — document the remaining issues and escalate (see Hard Cap). Such an exit may still be handed on where the skill's own path says so, but it is never reported as clean.

#### Hard Cap

**The skill's own bound always governs — this file only supplies a fallback.** The `proportional-gates` skill holds the **canonical** proportional table (Low → skip; Low-Medium → **3**; Medium-High → **5**; High → **10**), and most convergence-gated skills use a **10-cycle safety cap** (`code-review`, `test-review`, `epic-plan`, `verification-before-completion` are examples — **not an exhaustive list**). Other skills carry their own bounds, all of them governing over the fallback (`prototype-review` 5, 3 in React-diff mode; the `research` Step-5.5 verifier 2; the second-model gates in `code-review`, `plan-review` and `issue-scoping` 2; `codebase-audit` 3). The `subagent-driven-development` final reviewer is also a second-model gate but states no cycle bound of its own, so the fallback **10** governs it. A skill that says "no hard cap" but states a safety cap is **still governed by that cap** — "no hard cap" means no quality-gate ceiling, not no runaway guard. Only when a skill states no bound of any kind does the fallback **10** apply, and a skill that **explicitly declares itself uncapped** (`carousel-designer` — "No Cycle Cap … No arbitrary cap") is never capped by this file, and its own stop rules govern.

This is a **runaway guard, not a quality gate** — review cycles are how quality gets produced, so do not treat the cap as a target, and do not stop early because the count "feels high". Stop on (a) a clean exit (the skill's clean verdict — `NO ISSUES FOUND`, or its defined equivalent), (b) **convergence as the running skill defines it** (issues are a strict subset of the previous cycle's — no new dimensions or files; some skills define this only at their safety cap, e.g. `epic-plan`), (c) a stall signal the skill defines (`fingerprint-stall`, `honest-stuck`, `zero-progress`, stall-guard), (d) an abort the skill defines (`tool-unavailable`, `git-error`, `pr-closed`, fixer push failure), or (e) the bound. Never apply a bound tighter than the skill's own.

**(b)–(e) are escalation exits, not completions.** They do not satisfy the Exit Conditions above, and the loop must never be reported or handed off as clean or complete **while issues remain** (if the skill's own recovery path resolves them all and a fresh reviewer returns the clean verdict, that is a clean completion under (a)). (Where a skill labels its *zero-issue* exit "convergence" — e.g. `prototype-review` — that is a clean exit under (a), not this rule.) On a non-clean exit → **escalate** — to the orchestrator agent, or to a human wherever a skill requires one (the Auto-Continue pause conditions apply in addition). Document the remaining issues, then follow the skill's own path for that exit **first** — including any mandatory orchestrator recovery (`code-review` Step 6.5, `plan-review`'s deep-fix attempt) — and post **the exact marker the skill's own exit table defines at the point that path specifies**, where it defines one (`code-review` cap → `⚠️ Auto-fix reached the 10-cycle safety cap — unresolved issues remain; escalate to a human`; `test-review` cap → `⚠️ Test review capped at 10 cycles — N issues remain:`). Where the skill defines no marker for that exit, post `⚠️ <the skill's own name for the exit> after N cycles — M issues remain` and record the exit under the skill's own name. Use the skill's own label verbatim — do not invent a cap label for a convergence or stall exit, and do not relabel an exit the skill itself names otherwise:

- **Paths that require a human** (non-exhaustive: `plan-review` → Requires Human Input; `carousel-b2b-copy` → BLOCKED; `code-review` → its convergence exit needs human acknowledgement and its cap/stall exit surfaces to a human via its Step 6.5 recovery; `test-writing` → halts while a P0 remains): **do not proceed past that skill's own halt point.**
- **Paths that log-and-proceed** (`epic-plan`, `test-review`, `meta-framework-research`'s 3-cycle stall rule, `test-writing` when only P1/P2 remain — **non-exhaustive**): continue **only** with the skill's own marker posted where it defines one, and the remaining issues — including any P0 — recorded in the artifact, exactly as the skill directs. A capped exit is never described as clean or complete.

#### FORBIDDEN — These Bypass the Quality Gate Entirely

- ❌ Run review → get issues → fix → declare done without re-dispatching reviewer
  This IS skipping the review. Fixing without re-reviewing = no review.

- ❌ Self-declare "I addressed the feedback" as completion
  Only "NO ISSUES FOUND" (or the skill's own clean verdict) from a fresh reviewer is a valid **clean-completion** signal. Convergence, stall, abort, and cap exits **that leave issues unresolved** close the loop only with the remaining issues documented and the skill's own path for that exit followed (see Hard Cap); a skill labelling its *zero-issue* exit "convergence" (e.g. `prototype-review`) is a clean exit.

- ❌ Re-review in the same conversation context
  Confirmation bias makes same-context re-review unreliable.
  Use the skill's mandated fresh-context dispatch (`task`, its MCP wrapper, or its verifier subagent).

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

**⛔ Model override prohibition:** Do NOT pass `model: "claude-sonnet"` or any non-DeepSeek model to the `task` tool. Only DeepSeek is configured for general use; see the second-model-gate exception below. Overriding will cause the sub-agent to fail with "No API key found for anthropic."

**Second-model gate exception (#284):** the second-model review gates (issue-scoping §5.6 coherence check, code-review §6.6 + plan-review §4.5 final gates, subagent-driven-development final code reviewer) may dispatch `model` = `$SECOND_MODEL` (env; default `deepseek/deepseek-v4-pro` — provider-qualified, unambiguous). Non-DeepSeek second models (e.g., kimi-k3, qwen3.8-max after re-enable) are permitted ONLY via an explicit `$SECOND_MODEL` override; when the configured second model is unavailable, dispatch the tool default and annotate `[SECOND-MODEL-GATE] stand-in` — never silently substitute, and never use a non-DeepSeek second model without the env override.

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
- **Commit messages: always `git commit -F <file>` — never `-m`, never a heredoc.** Write the
  message with the `write` tool to `/tmp/commit-msg-<branch>.md`, then
  `git commit -F /tmp/commit-msg-<branch>.md`. Both `-m "…"` and heredocs pass the message
  through the shell first — backticked spans run as command substitution, `$VAR`/`$(…)` expand,
  `${…}`/`{{ }}` break — and the failure is **silent**: the substitution yields an empty string,
  git accepts the mangled result, and only a human reading the log sees the hole. The
  `commit-msg` hook warns on the signature (unbalanced backticks, or a doubled space where inline
  code should be) when husky hooks are installed — do not rely on it running (#672). On a hit:
  amend **before** pushing; if it is already pushed, post a correction note instead of silently
  force-pushing. Worked example: `skills/commit-workflow/workflow/02-commit-pr.md`.

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

<!-- REPO-SPECIFIC: Reference your ontology doc for entity types and predicates. Canonical ontology: tortoise repo `docs/ONTOLOGY.md` (v3.1) — fetch: `gh api repos/daniel-ospina/tortoise/contents/docs/ONTOLOGY.md --jq .content | base64 -d` (§1.1 types, §2.2 predicates). In repos that keep a docs/teams tree (eldato layout), reference `docs/teams/<team>/domains (S1)/<domain>/ONTOLOGY.md` if present. -->

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
| Bug fixed | `docs/teams/<team>/domains (S1)/<domain>/gotchas.md` (eldato layout) + `MEMORY.md` |
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

<!-- AGENTS-BASE-END -->

## Repo-Specific Conventions — Tortoise

### Project Identity

Public repository that houses:
- **Tortoise:** Python graph engine for semantic/epistemic agent memory (SDK, MCP server, EP belief propagation)
- **Strategy docs:** product strategy, competitive analysis, pricing research
- **Internal operations:** agent skills, CI/CD, coordination scripts (shared with premise-labs lineage)
- **Web presence:** premise-labs / product landing pages under `website/`

### Language & Runtime Conventions

#### Python (Tortoise SDK)

- Python 3.12+ (see `.python-version`). No build step — interpreted.
- Install (canonical dev env — includes the extras the real battery lanes need): `uv sync --extra embeddings --extra parity` (min uv 0.6.0). The committed `uv.lock` is the dev-environment source of truth; `uv lock --check` gates lockfile drift in CI.
  - ⚠️ `uv sync` with an explicit `--extra` is EXACT: it SILENTLY REMOVES every extra you do NOT name (`uv sync --extra embeddings` drops `parity`/pyarrow — how a measurement run broke mid-investigation, #2985). Name every extra the lane needs in ONE command, or use `--all-extras`.
  - Plain `uv sync` (no extras) yields a **KEYWORD-ONLY product**: `EmbeddingModel.get()` returns None, the dense retrieval leg is never submitted, and retrieval silently degrades to FTS-only. Real lanes fail closed on this via `battery/runner/retrieval_preflight.py::require_hybrid_retrieval` (#2985) — do not "fix" a refusal by dropping the extra.
- Run commands/tests: `uv run <cmd>` — e.g. `uv run pytest tests/ -v`
- `pip install -e .` remains the legacy/CI install path (python-ci.yml); uv is canonical for local dev.
- Imports: prefer `from pathlib import Path` for path resolution — never hardcode absolute paths
- Type hints: `from __future__ import annotations` at top of all modules

#### TypeScript / Node.js (CI, Scripts, Tooling)

- Node.js 20+. Scripts are plain CJS (no build step).
- No `package.json` at root — scripts are standalone with zero npm dependencies
- `agent-infra/` provides shared CI tooling and bootstrap scripts

### Paths

- **Repo root:** `Path(__file__).resolve().parent.parent` (from tests/) or `Path(__file__).resolve().parent` (from tortoise/)
- **Import tortoise:** `sys.path.insert(0, str(Path(__file__).resolve().parent))` from graph-scripts/ or tests/

### Skill Compliance Table

| Trigger | Must invoke | Consequence of skipping |
|---|---|---|
| Any git operation (commit, push, merge) | `skills/commit-workflow/SKILL.md` | No review gate, unreviewed code in production |
| Any Tortoise graph write (create point, operator, mitigation, NAND, supersede, annotate) | `skills/how-to-use-tortoise/SKILL.md` | EP weights nuked by batch-connected mitigations, orphaned NANDs |
| Writing an implementation plan | `skills/writing-plans/SKILL.md` | Unplanned code, missed design decisions |
| Scoping an issue | `skills/issue-scoping/SKILL.md` | Unscoped work, missed complexity rating |
| Reviewing a PR | `skills/code-review/SKILL.md` | Unreviewed code in production |
| Finding bugs | `skills/find-bugs/SKILL.md` | Missed regressions |
| Any non-trivial research | `skills/research/SKILL.md` | Shallow analysis, costly rework |

### Key Directories

| Path | Purpose |
|------|---------|
| `tortoise/` | Python SDK, EP engine, MCP server, connectors |
| `tests/` | Test suite (pytest) |
| `graph-scripts/` | Historical graph operations (pricing decisions, migrations, audit) |
| `scripts/` → `$AGENT_INFRA_PATH/scripts` | Agent-infra shared scripts (symlink) |
| `config/` | YAML configs (routing, pipelines) |
| `docs/` | Architecture, ontology, legal, strategy docs |
| `data/` | Event logs, extracted documents, ontology |
| `product/` | Product strategy, competition, pricing |
| `website/` | Landing page (`website/index.html`) |
| `validation/` | Schema validation rules |
| `skills/` | Agent skill definitions (shared with main repo) |
| `operations/` | Internal operations and coordination |

### Environment

- Copy `.env.example` to `.env` before running
- `TORTOISE_DB_URI` — FalkorDB connection string (`docker://` or `bolt://`)
- `AGENT_INFRA_PATH` — Path to agent-infra repo (required for bootstrap, pre-commit version gate)
- See `.env.example` for all variables

### Model Selection (Pi)

- **Most tasks:** `deepseek-v4-flash` (base default)
- **Graphics/visual tasks:** `qwen3.8-max` (Qwen 3.8) — interactive session only, where configured
- **Highly complex / tricky tasks:** `qwen3.8-max` (Qwen 3.8) — interactive session only, where configured
- **`task`-tool / sub-agent dispatch:** DeepSeek ONLY, per the base-head model-override rule ($SECOND_MODEL gate; see AGENTS.md base head, "Sub-agent Dispatch"). Non-DeepSeek models are never used for sub-agents without the env override.

### Git Workflow

- **Before any commit:** invoke `commit-workflow` skill
- Branch naming: `feat/`, `fix/`, `chore/` prefixes
- PRs auto-merge by default (no staging hold unless `deploy:staging` label)
- Pre-commit hook enforces agent-infra version sync via `.husky/pre-commit`

### Testing

> Epic #1647 (P4): `pytest` now defaults to the DOCKER lane — a URI-less
> run fails unless `TORTOISE_TEST_CARVE_OUT=1` is set (the carve-out's 17
> embedded-only files). Docker: `docker compose -f ../eldato/operations/memory/docker-compose.yml up -d` + the URI below.

```bash
# Default (docker FalkorDB — epic #1647 P4):
export TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix'
uv run pytest tests/ -v

# Embedded carve-out (the 17 embedded-only files; URI-less opt-in):
TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_embedded_lifecycle.py tests/test_guard.py -v

# Run specific test file (docker lane)
TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_directional_impl_fix.py -v
```

### Documentation Filing

For topic-to-file routing, see `docs/00_index.md`. When in doubt, open `docs/00_index.md`.
