# AGENTS.base.md — Universal Agent Instructions

> Shared base for all repos using agent-infra. Copy to your repo as `AGENTS.md` and customize. 70% of rules are universal — extend or override in repo-specific sections below.

> ⛔ **Prerequisite:** `AGENT_INFRA_PATH` must be set in your shell profile (e.g., `~/.zshrc`).
> The auto-sync extension, pre-commit version gate, and bootstrap CLI all require it.
> Run `echo $AGENT_INFRA_PATH` to verify. See [agent-infra README](https://github.com/premise-labs/agent-infra#prerequisites) for setup.

---

## ⛔ HARD RULE: Auto-Continue — NEVER PAUSE WITHOUT A REASON

**Default: Your job is to keep moving until you hit a real gate.

Real gates are:

1. A skill explicitly mandates a human gate (sign-off, approval, decision point)
2. P0 consequence risk (data loss, security, unrecoverable cost >$10/mo)
3. Genuinely ambiguous — research was inconclusive (<50% confidence) and you need a decision
4. Another agent is live working on this ("Held" means another agent is live working on something. Previous comments on issues are not enough to conclude something is held)

If none of those apply: **keep going.** The user can interrupt if they disagree.

**Invalid reasons to stop:** Any question whose answer is trivially "yes" e.g.:

- "Ready?" "Proceed?" "Continue?" "Shall I…?" "Want me to…?" "Should I…?" (waste of user time; you should continue)
- "On to the next step?" "Does that look right?" "Everything OK so far?" (unless P0 consequence risk — that is a real gate, so stop — otherwise double-check and continue)
- Any handoff where the user has nothing to decide (no reason to stop)
- Another issue fixes this (then fix that issue)
- context is almost exhausted (context auto-compacts)

**Auto-file rule:** When you encounter a bug, workflow gap, missed edge case, or improvement opportunity → check if the root cause and/or symptoms are already covered by another issue and if yes add to it, or otherwise file a new GitHub issue. Never ask "should I file an issue?" — just file it if in doubt.
Also, when you encounter a **pre-existing bug** (not introduced by your current work).

---

## ⛔ HARD RULE: Process Discipline

Your role is to work within the skills and processes framework we have explicitly designed. The skills, workflows, and tools embed the accumulated learnings from all previous work and should not be bypassed nor hacked. If there are difficulties or inefficiencies, the right process is to do the work as designated regardless and provide feedback in the reflection phase (after the work), for systematic improvement of all future runs. Following this process allows us to treat our system as a product we can evolve and eventually sell, but only if properly used instead of bypassed. If in absolute need, ask for permission to bypass before doing so.

---

## ⛔ HARD RULE: Fix Broken Infrastructure — Never Silently Work Around It

**When a dependency is broken (MCP server down, database unreachable, API returning errors, connection failing, auth broken), you MUST fix the root cause OR get explicit human authorization to change the plan/workflow.** Do NOT silently change approach, point at a different backend, enable a fallback, or "make it work" with a workaround without either (a) fixing the actual breakage, or (b) human sign-off on the change.

**This rule exists because of a real incident (2026-08-05):** the planned FalkorDB Cloud connection was failing. Instead of debugging the connection, an agent silently shipped a self-hosted FalkorDB container on Fly.io with AOF disabled and no off-box backup. That fallback had no durability — a later test run wiped the production graph (5,748 points) and it was only partially recoverable. A single unresolved failure compounded into permanent data loss because the workaround was never flagged for human review.

**The pattern to follow when something is broken:**

1. **Diagnose first** — read the error, trace the root cause, confirm what's actually failing (skills: `debug-workflow`, `find-bugs`)
2. **Fix the root cause** — reconnect, repair config, fix the bug. This is the default.
3. **If you cannot fix it** (needs credentials, external service access, decision) — **STOP and escalate**: report the diagnosis + proposed fallback to the human, get explicit approval BEFORE changing the architecture, backend, or workflow
4. **Never ship a fallback as if it were the plan** — a workaround (embedded DB instead of managed, self-host instead of cloud, local instead of remote) is a red flag that must be surfaced, not absorbed

---

## ⛔ DESIGN PRINCIPLE: Good > Easy

When choosing between two approaches, prefer the one that produces the better outcome (solving root cause) over the one that's easier to implement (band-aids). Quality of result trumps implementation convenience. Easy paths accumulate into brittle systems; good paths cost more upfront but pay back in reliability, extensibility, and user satisfaction.

Avoid bureaucratic bloat (epxnesive/long tests, too many guards, etc.), and instead address root causes and design lean, effective systems. Research best practices often to know what's good (use research skills, even if internal).

---

## ⛔ USER QUESTIONS PROTOCOL: research and ask without jargon

**⛔ ASK THE CONTRADICTION TEST FIRST — BEFORE YOUR OTHER TESTS.** Before adopting anything a research pass returns (a convergent standard, a SOTA pattern, a comparable's practice), ask **"is there a decision this would contradict?"** — and ask it *first*, ahead of cost, quality, convergence strength, or fit. A convergent answer that contradicts a decision **is not a candidate for adoption at all**: not "adopt with a caveat", not "escalate and adopt", not a footnote, and not something to park with the owner as an option. **Convergence describes what the field does. It does not describe what we have decided to be.** An owner decision outranks it — and **if you believe the standard should win, the route is to reopen the decision**: reopen it in its own home (its issue, plan doc, or Tortoise point), **put the evidence in front of the owner, and argue it.** Adopting over a decision is *never* the route — it silently reverses a deliberate choice, and nothing in the change will say so. **Why the edge is sharp:** if a standard could override a decision, the next lane to read a vendor's documentation holds the pen on our product's promises, and the decision survives only until someone else does research — which is not a decision, it is a default that holds until the next pass. **Refusing an adoption is not a verdict on the finding.** A contradicting finding is **accurate and valuable, and it is the evidence for that reopen** — the refusal tests the decision's *authority over the matter*, never the research's *accuracy*. Discarding it is how a reopen loses its case. Not adopting over a decision, and not dropping a decision-free candidate, are the two halves of this contradiction test: a convergent answer that no decision reaches is a live question to be **argued with the owner**, not a candidate to be killed by analogy.

A **recorded decision** means an owner ruling, a decision section in a plan doc, a decision comment on an issue, or a Tortoise point carrying one — not merely an existing practice, and not a thing the code happens to do today.

**⛔ MARK A DELIBERATE DEPARTURE WITH AN `OVERRIDES:` LINE — ON THE ISSUE.** The contradiction test only bites if an adopter can tell a **deliberate ruling against the grain** from **an accident of history** — and a record that states the choice but not *what it overrides* reads identically either way, so the ruling survives only until a helpful reader holding a vendor's page treats it as legacy and tidies it away. Every decision that goes against the common/industry default therefore carries one line:

> **OVERRIDES:** <the default, named concretely — the window, the pattern, the vendor practice> — <one sentence of reason>.

The marker belongs **on the artifact a lane actually reads: the ISSUE** (a comment on the decision issue), with the decision ledger carrying the same line as the index. A marker that lives only in a ledger is invisible to the lane holding the vendor page — and that is exactly the lane that overwrites the ruling. Cost: one line. Effect: the ruling reads as **intentional at the point where adoption happens**, instead of as an accident waiting to be tidied.

**The hard stop is the decision, not the marker — the `OVERRIDES` line only makes the contradiction findable.** A convergent standard that contradicts **any recorded decision, marked or not**, is **not a candidate for adoption at all**; the route is a **reopen** — evidence in front of the owner, argued — never a quiet adoption, never "adopt with a caveat", and never an inference that convergence has made the default right. The marker's job is to make an against-the-grain ruling **visible to the adopter before the research has to rediscover it** — not to decide whether the decision blocks, which it does either way.

When you need to ask the user a question, first research it to ensure it indeed needs the user. If a SOTA solution exists where competitors/comparable implementations converge, **and it contradicts no recorded decision (run the contradiction test above FIRST)**, and is aligned with the rest of our work, use it and don't bother the user. If you need to ask the user, ensure you present: context, options, analysis, and recommendation, all without jargon (specific terms should be canonical, e.g. as per ontology document)

### DECISION RELAY: the answer is recorded where the work is gated

When the owner answers a question, the answer is **relayed to the artifact the next lane reads** — never left in a conversation. An answer that survives only in a chat is invisible to the lane that must obey it, which is the same failure the `OVERRIDES:` marker above exists to prevent. Relay it into one of the recorded homes, and say which one:

- **a comment on the decision issue** — the default; the issue is what a lane actually reads;
- **the decision section of the plan doc** that governs the work;
- **a Tortoise point** carrying the decision, when it outlives the issue;
- **the gate's own record**, where the work is gated by a manifest: the MCP/SDK surface rule records the owner's approval on the row's `approval` field (a PR number and Daniel's handle) in `config/surface-manifest.yml` — `CONTRIBUTING.md` → "The MCP tool surface and public SDK methods cannot grow by accident" is the procedure this rule is cited from.

The relay is the point. A decision that is not recorded on the artifact is not a decision — it is a default that holds until the next lane re-decides it.

---

## ⛔ SESSION RECAP PROTOCOL: don't recount trivia about what happened, present state and decisions.

If you're going to present a recap at the end of a turn or session, don't say things like "Cycle 3 found the worst bug of the whole lane" or "Two corrections I had to make about my own work" unless they're changing the scope, architecture or UX that was agreed. Instead present the state, key design principles/decisions made, and cleanly present any user decisions needed (see USER QUESTIONS PROTOCOL) or next steps. If the next steps are just to continue, do not stop and just continue (see NEVER PAUSE WITHOUT A REASON)

---

## ⛔ HARD RULE: Skill Compliance

**Skills are NON-NEGOTIABLE. No shortcuts, no "I know this one," no skipping because you're in a hurry.**

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

Every review cycle MUST re-review in a FRESH context — via `task` where the skill dispatches one, or the skill's mandated mechanism (its MCP wrapper, or its verifier subagent) and mandate reviewers to use the research skill. The reviewer has no memory of prior cycles, no investment in defending prior fixes. This prevents confirmation bias.

- Same-model self-review in the same conversation degrades without an external signal
- The model defends prior decisions rather than critically re-evaluating
- `task` spawns `pi -p` in a new process with no session memory — the closest available proxy for an independent reviewer

#### Exit Conditions — ALL Must Be True (Clean Completion)

- [ ] Last reviewer response was the skill's clean verdict — `NO ISSUES FOUND`, or the skill's defined equivalent (e.g. the verifier's `PASS`, the loop's `CLEAN`) — verbatim, not paraphrased
- [ ] If cycle 1 found any issues → at least 1 re-review cycle completed
- [ ] Cycle log posted: each cycle's issues and fixes documented

These conditions define a **clean completion** only. A convergence, stall, abort, or cap exit **that leaves issues unresolved** cannot satisfy them: it is an **escalation** exit — document the remaining issues and escalate (see Hard Cap). Such an exit may still be handed on where the skill's own path says so, but it is never reported as clean.

Review cycles are how quality gets produced.

#### FORBIDDEN — These Bypass the Quality Gate Entirely

- ❌ Run review → get issues → fix → declare done without re-dispatching reviewer
  This IS skipping the review. Fixing without re-reviewing = no review.

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

**⛔ Model override prohibition:** Do NOT pass `model: "claude-sonnet"` or any specific model unless explicitly required"

## Batch Implementation & Parallel Dispatch

**Never ask "sequential or parallel?" — always plan the optimal parallelization yourself.** The default is maximum parallelism. The user started the session to get work done, not to manage a task queue.

### Decomposition maps parallelism

- When decomposing work (epic or multi-issue batch), explicitly map what can run in parallel.
- While waiting for a human gate (UX approval, design review) → dispatch sub-agents for other independent work

## Data Access Transparency

Announce with a brief FYI **before** accessing:

1. **External services** — MCP servers, web searches, API calls
2. **Files outside the project directory** — anything not under the current repo
3. **Sensitive files** — `.env`, credentials, keys, tokens, secrets
4. Announce skill invocations: "I'm using the [skill-name] skill to [purpose]."
5. Announce sub-agent dispatches: "Dispatching sub-agent for [purpose]..."

Format: `📡 [source] — [what] — [why]`

Does **not** apply to: routine project file reads, git operations, local shell commands, context7 doc lookups.

---

## Editing Rules

- **Never use sed for multi-line code changes.**
- **Never use `git add -A`** — always stage specific files.
- **Prefer the `edit` tool over `write`** for targeted changes to existing files.
- **Commit messages: always `git commit -F <file>` — never `-m`, never a heredoc.** The message
  message file goes in a **repo- and worktree-unique temp directory**, never a shared
  `/tmp/commit-msg-<branch>.md` — a branch name is unique per repo, not globally, so concurrent
  sessions in different repos silently overwrite each other's message (#729). The path is
  `${TMPDIR:-/tmp}/pi-commit-msg-$(git rev-parse --absolute-git-dir | cksum | cut -d' ' -f1)/$(git rev-parse --abbrev-ref HEAD | tr '/' '-').md` —
  `write` the message there (the write tool creates the directory), then commit with `-F`.
  `--absolute-git-dir` is per-repo AND worktree-aware — a linked worktree gets *its own* gitdir —
  and its `cksum` names the directory, so cross-repo and cross-worktree collisions cannot happen
  in practice — a 32-bit digest makes a clash a ~1-in-4-billion coincidence rather than the
  *guaranteed* clash the old fixed path produced.
  ⛔ **Never put it under `.git/`.** That was the first attempt and it is refused: the
  `main-worktree-guard` extension freezes any `.git/…` write as *hub git-metadata* for every
  unhatched session — the fleet default for `task` children — so the mandated `write` would be
  blocked and the agent left to improvise. `$TMPDIR` keyed by the git-dir checksum gives the same
  uniqueness, entirely outside every checkout. (Two sessions in the *same* worktree on the *same*
  branch still share the file; that case was always racy at the index level anyway.)
  ⛔ **Every bash tool call is a FRESH SHELL, and one call must not both assign and commit.** A
  `MSG=…` set in one call is **unset** in the next, so a later `git commit -F "$MSG"` commits from
  an **empty path** and `rm -f "$MSG"` silently removes nothing (both verified). Assigning `MSG`
  in the same call as the commit is *also* refused by the verification gate ("in-batch mutation
  chain"). So put the substitution **inline in the commit command** — no variable:
  `git commit -F "${TMPDIR:-/tmp}/pi-commit-msg-$(git rev-parse --absolute-git-dir | cksum | cut -d' ' -f1)/$(git rev-parse --abbrev-ref HEAD | tr '/' '-').md"`,
  then delete the message file in a **separate** call, re-deriving the path the same way.
  Both `-m "…"` and heredocs pass the message
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

## Repo-Specific Conventions — Tortoise

### Project Identity

Public repository that houses:

- **Tortoise:** Python graph engine for semantic/epistemic/episodic agent memory and reasoning (SDK, MCP server, EP belief propagation)
- **Strategy docs:** product strategy, competitive analysis, pricing research
- **Internal operations:** agent skills, CI/CD, coordination scripts (shared with premise-labs lineage)
- **Web presence:** company: premiselabs.co , product marketing: tortoise.premiselabs.co , product dashboard: app.premiselabs.co

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
| Starting work on any issue whose root is being investigated anywhere in the fleet — **read the graph BEFORE you derive** | `~/.pi/agent/state/TORTOISE-GRAPH-BRIEF.md` (URI, node ids, read/write recipes, the 8 trap rules) | A lane re-derives a conclusion the map already holds — or already refuted — and reports it as new (#6134: a root re-investigated that the graph already held, and a false "the SDK cannot delete an operator" that was one call away from disproof) |
| Writing an implementation plan | `skills/writing-plans/SKILL.md` | Unplanned code, missed design decisions |
| Scoping an issue | `skills/issue-scoping/SKILL.md` | Unscoped work, missed complexity rating |
| Reviewing a PR | `skills/code-review/SKILL.md` | Unreviewed code in production |
| Finding bugs | `skills/find-bugs/SKILL.md` | Missed regressions |
| Any non-trivial research | `skills/research/SKILL.md` | Shallow analysis, costly rework |
| Dispatching work on any issue (worktree, branch, sub-agent, parallel workstream) | `uv run python tools/collision_preflight.py <N> --repo .` — must exit 0 before dispatch | A second agent duplicates live work; overlapping PRs and a wasted dispatch cycle (#3061) |

### ⛔ HARD RULE: MCP/SDK Surface Approval — Ask Daniel Before You Change the Surface

You may **not** add or remove a tool from the MCP surface, or a method from the SDK surface,
without **human approval from Daniel**. This is a mandated rule, not a suggestion, and it is not
machine-enforced.

- **THE RULE.** The MCP tool surface is `TOOL_REGISTRY` in `tortoise/tool_registry.py`; the SDK
  surface is the public (non-underscore) methods on `TortoiseSDK` in `tortoise/sdk.py`. Adding,
  removing, or renaming either is a surface change.
- **WHY IT EXISTS.** The surface is the contract every agent and customer integration is built
  on — changing it changes what every agent can see and do, so it materially affects customer
  outcomes.
- **WHAT TO DO.** Get Daniel's approval **first**, before you write the change or re-cut the
  baseline, through the "USER QUESTIONS" / "DECISION RELAY" path above: name the tool or method,
  say what it does and why it is needed. Then follow `CONTRIBUTING.md` → "The MCP tool surface and
  public SDK methods cannot grow by accident".
- **THE GATE IS NOT THE APPROVAL.** `tools/surface-guard.py` and `tools/surface_manifest.py check`
  are **drift controls**: a change that updates the code and `config/surface-manifest.yml`
  together **passes both**. They catch an *unrecorded* change and cannot tell an approved addition
  from an unapproved one. A green run is not consent.

Do **not** propose replacing this with a GitHub ruleset, `CODEOWNERS`, a required second approver,
or a separate automation identity — the owner **rejected** that direction on #4282 as
over-engineering.

### ⛔ HARD RULE: Collision Pre-Flight Before Any Dispatch

Before spawning a workstream, opening a worktree, or dispatching a sub-agent for issue **N**,
run the collision pre-flight — **all surfaces queried, and untruncated where truncation could
matter**:

```bash
# from the target repo's worktree (`--repo .` pins the target to THIS repo):
uv run python tools/collision_preflight.py <N> --repo .
# or name the repo explicitly (required when dispatching an issue that lives in
# another repo — the tool RESOLVES the target, it never infers it from the cwd):
uv run python tools/collision_preflight.py <N> --repo owner/name
```

**Consequence of skipping:** a parallel agent duplicates work already in flight — two overlapping
PRs, a wasted dispatch cycle, and a consolidation decision that should never have been needed
(#2985 vs PR #3005, #2952 vs PR #3018 — the incident in #3061). A truncated or partial check is
worse than none: it manufactures false confidence. Never `grep`/`head`/`tail` a completeness check.

### Shared capability — JEV

**JEV is a shared, decision-only capability** — a third-party model (`https://jevtypesafeai.com/docs#apis`)
used by fleet tooling wherever a decision is about **meaning** rather than identity. *Decision-only*
is the property that makes it a shared capability rather than a model call: it can only return a
value inside the schema the caller supplies (`Choice` / `Score` / `Noul` — a calibrated 0–1
probability, mixable in one call), so it cannot invent a name or a label that was not offered.

**The division of labour is the contract: the MODEL labels; the calling code does only
mechanics** — pre-filter, batching, thresholds, caching, and fail-closed degradation. A caller may
not let the model's probability *be* the verdict: the threshold band, the cache identity, and the
failure paths belong to the caller, and they must be **measured and pinned, not assumed**.

- **In-repo use:** `tools/collision_preflight.py` (#5070) — the claim-ownership `Noul` question
  ("is this comment a lane taking ownership of the issue's work?"), with the model pinned to
  `jev-1.13.0` and prompt version `claim-ownership-v1`. CLEAN requires p < 0.50; the band
  `[0.50, 0.70)` is a HIT, never a silent CLEAN; a `model` field that is not exactly the pin is
  rejected. No key, no network, or any JEV error/parse failure degrades to the offline verdict and
  exit code (`COLLISION_PREFLIGHT_JEV=off` forces that path exactly) — a JEV outage can never
  become a false CLEAN.
- **The request shape is pinned by the caller and verified against the live endpoint — it is
  deliberately NOT restated here.** A shape copied out of prose is how the next caller gets a 400
  that blames its own code; copy the working construction in the caller instead (#7866 is the open
  finding on that shape).
- **Rationale and JEV's decision-only properties:** `docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md`
  (§"✅ Jev for classification is cheap, and the seam already exists"; the extraction journey's
  S2.2/S2.3 consumes the same capability) — not this file.

### ⛔ HARD RULE: Confirm the Dispatch Landed — `cmux send` Success Is Not Delivery

Never dispatch to a cmux pane with a bare `cmux send`. **Use `tools/cmux_dispatch.py`** — it is the
only dispatch path that confirms the ARTIFACT rather than the send:

```bash
uv run python tools/cmux_dispatch.py send --workspace <ws> --surface <surf> \
    --label <lane> --file <brief.txt>        # 0 consumed · 4 queued (delivered) · 1 not consumed · 2 usage · 3 transport
```

`cmux send` exits 0 when *bytes were written to the terminal*, which is a different event from *the
message became a conversation message*. Two live failure modes sit downstream of that syscall and
are invisible to any exit code (#4292):

1. **Send-during-boot race** — bytes written before pi's TUI takes over stdin sit unsent in the
   composer (or are discarded).
2. **The boot-block prompt** — a freshly-booted pi can be blocked on `Press any key to continue...`
   (`dist/migrations.js::showDeprecationWarnings`, interactive mode, triggered by a non-fd/rg entry
   under a `tools/` directory). That prompt consumes the bytes as its keypress: the pointer is
   **eaten**, or its prefix is eaten and the remainder submitted as a **truncated turn**.

The dispatcher waits for the pane to be safe to send (dismissing a boot-block prompt instead of
feeding it the brief), sends text + a bare Enter, then confirms on **positive evidence that pi
took the message** — via either `cmux list-workspaces --json` → `latest_submitted_message` (it became
a turn) **or** the pane's pending-turn display (`Steering:` / `Follow-up:`, the queue pi accepted a
mid-turn submission into). It recovers automatically (release the composer with a bare Enter, or
dismiss-and-re-send when the text was eaten). It exits non-zero with `sent-but-not-consumed` when
the message appears in neither — **text still sitting in the composer is the UNSENT state, never a
success** (pi clears the editor before it queues).

A message pi ACCEPTS into its pending queue (`Steering:` / `Follow-up:`) is **delivered but not
consumed**: it exits **4**, not 0 (#7743). pi only drains the queue at the end of the current turn,
so on a lane wedged inside a turn — the pane `Working` with no descendant child and a frozen
transcript, surfaced as `pane_stalled_turn` by `tools/fleet_state.py` — a queued message is never
read. `--json` carries both `delivered` (true — do not re-send, that would duplicate) and `ok`
(false — the turn has not started), so a caller that must not duplicate keys on `delivered` and a
caller that needs the turn to have started keys on `ok` / exit 0.

**One-line check until every caller is migrated:** after dispatching, confirm the lane shows a
`Working` spinner (`cmux read-screen --workspace <ws> --lines 6`) before assuming it started. A pane
showing the pointer text above the status line with `0.0%` and no spinner has NOT started.

**Consequence of skipping:** a silently-dead lane is indistinguishable from a working one until the
work does not happen — or until a corrupted turn runs on a truncated brief. This cost a full
dispatch cycle and was invisible to every pre-existing check; it is also the most likely explanation
for three sends to one pane that were recorded as `OK` and never consumed.

### Key Directories

| Path | Purpose |
|------|---------|
| `tortoise/` | Python SDK, EP engine, MCP server, connectors |
| `tests/` | Test suite (pytest) |
| `graph-scripts/` | Historical graph operations (pricing decisions, migrations, audit) |
| `scripts/` → `$AGENT_INFRA_PATH/scripts` | Agent-infra shared scripts (symlink) |
| `tools/` | In-repo tooling — e.g. `collision_preflight.py` (pre-dispatch in-flight-work check, #3061) |
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
- **`task`-tool / sub-agent dispatch:** DeepSeek ONLY, per the base-head model-override rule. Non-DeepSeek models are never used for sub-agents.

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

> **Private lane instance (#5084) — use this when more than one lane is testing.**
> The docker lane above points *every* lane at ONE long-lived container. Test graphs are
> minted per run and reaped by a session-end sweep whose server-global pass is deferred
> while any peer session is live, so under fleet concurrency the shared instance
> accumulates leftovers: measured 2026-10-06 it had **restarted 213 times** (it was 46
> when #5084 was filed on 2026-09-24), and a single-file run showed graphs appearing from
> *other* lanes' sessions. `tools/test_lane.py` gives the lane its own throwaway container
> — for every test that takes its target from `TORTOISE_DB_URI`, the graphs land in a
> container this tool refuses to address from any other lane, so no lane's run leaves
> residue on another's target.
> The container is not self-cleaning: it lives until an explicit `down`. **Not yet the whole suite**:
> tests that build their URI through `tests/_live_utils.py` key on
> `TORTOISE_TEST_DOCKER_PORT` (default 6379) and a `falkordb` password, so they still address
> the shared instance and their graphs still accumulate there. Closing that seam is #5084's
> remaining work — the tool is the isolation half, not the whole fix.
>
> ```bash
> uri="$(uv run python tools/test_lane.py uri)" || exit 1   # start; capture the export line
> eval "$uri"                                              # two steps on purpose — see USAGE
> uv run pytest tests/ -q
> uv run python tools/test_lane.py status          # it prints the URI again
> uv run python tools/test_lane.py down            # remove it
> ```
>
> Isolation, **not** speed: the same single test measured 90.59 s privately vs 92.66 s on
> the shared container, so use the shared lane when that is what you are testing. The tool
> can only ever act on the container derived from the worktree you run it in — it has no
> `--name` or `--slug` override, so one lane cannot remove (or silently adopt) another
> lane's `fdb-lane-<slug>`.

### Documentation Filing

For topic-to-file routing, see `docs/00_index.md`. When in doubt, open `docs/00_index.md`.
