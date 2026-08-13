# CLAUDE.md — Premise Labs (Claude Code overrides)

> Primary instructions: Read `AGENTS.md` for all shared project conventions (Python paths, testing, git workflow, Tortoise graph operations). This file contains only Claude-specific differences from the Pi baseline.

## Project Identity

Premise Labs is the company behind **Tortoise** — a Python graph engine backed by FalkorDB that powers agent memory (semantic, epistemic, episodic, procedural). This repo (`daniel-ospina/tortoise`) contains:
- **`tortoise/`** — Python SDK, MCP server, EP belief propagation engine
- **`apps/graph-viz/`** — React ontology visualization frontend
- **`docs/`** — Architecture, ontology, legal, product strategy
- **`website/`** — Company landing page

## Model

- Claude Opus 4.5 for complex reasoning and graph operations, Sonnet for lighter tasks
- Skill `model:` frontmatter (opus/sonnet) is advisory — a reminder, not a requirement

## Sub-agent Dispatch

- Use Claude's Agent tool (not Pi's `task` tool)
- Sub-agents MUST invoke skills when applicable — same rules as main conversation
- When dispatching, include explicit instructions to check for and invoke relevant skills

### Delegation Decision Framework

Decide autonomously — never ask. Announce, then act.

**Use a sub-agent (Agent tool) when:**
- Output would be verbose and only the summary matters (logs, exploration, research)
- Task is self-contained and has a clear return value
- 2+ independent tasks can run in parallel (no shared state)

**Stay in main conversation when:**
- Task needs iterative back-and-forth
- Multiple phases share significant context (plan → implement → test)
- Change is quick and targeted

**Suggest a new conversation when:**
- The task is large and unrelated to current work
- Context has already compacted and quality is degrading
- A clean slate would meaningfully improve the outcome

When suggesting a new conversation, always provide the exact prompt the user can paste.

## Key Differences from Pi

| Claude Code | Pi |
|---|---|
| Agent tool for sub-agents | `task` tool, skills loaded from files |
| `superpowers:skill-name` references | Skill name directly (e.g., `commit-workflow`) |
| WebSearch/WebFetch built-in | `web_search`/`web_fetch` tools |
| `model:` frontmatter respected | Ignored — Pi uses its own model selection |

## Tortoise Graph Operations

- Annotate Source→Point operators (bias, precision, consistency, directness)
- Try mitigation before NAND for logical tension
- At least one disconfirming query before concluding

## Related Repos

- **El Dato (main app):** `daniel-ospina/eldato` — the consumer of Tortoise. Contains `operations/skills/` and the memory system plan (`docs/epics/2026-07-14-memory-system/`).
- **Agent Infra:** `daniel-ospina/agent-infra` — its own repo: Pi extensions, skills, and commit-workflow, code-review, epic-workflow, and other agent skills consume Tortoise via MCP.
