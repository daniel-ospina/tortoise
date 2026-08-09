# AGENTS.md — Premise Labs

> Private repository for strategy, research, internal operations, and the Tortoise epistemic graph engine.
> Extends [AGENTS.base.md](https://github.com/premise-labs/agent-infra/blob/main/templates/AGENTS.base.md) principles. Universal rules (Auto-Continue, Process Discipline, Skill Compliance, Research Discipline, Debugging Discipline) are inherited — this file adds repo-specific conventions only.

## Project Identity

Premise Labs is the internal R&D and strategy hub. It houses:
- **Tortoise:** Python graph engine for semantic/epistemic agent memory (SDK, MCP server, EP belief propagation)
- **Strategy docs:** Product strategy, competitive analysis, pricing research
- **Internal operations:** Agent skills, CI/CD, coordination scripts
- **Premise Labs landing:** `website/index.html`

## Language & Runtime Conventions

### Python (Tortoise SDK)

- Python 3.11+. No build step — interpreted.
- Install: `pip install -e .`
- Imports: prefer `from pathlib import Path` for path resolution — never hardcode absolute paths
- Type hints: `from __future__ import annotations` at top of all modules
- Run tests: `python -m pytest tests/ -v`

### TypeScript / Node.js (CI, Scripts, Tooling)

- Node.js 20+. Scripts are plain CJS (no build step).
- No `package.json` at root — scripts are standalone with zero npm dependencies
- `agent-infra/` provides shared CI tooling and bootstrap scripts

### Paths

- **Repo root:** `Path(__file__).resolve().parent.parent` (from tests/) or `Path(__file__).resolve().parent` (from tortoise/)
- **Import tortoise:** `sys.path.insert(0, str(Path(__file__).resolve().parent))` from graph-scripts/ or tests

## ⛔ HARD RULE: Skill Compliance

| Trigger | Must invoke | Consequence of skipping |
|---|---|---|
| Any git operation (commit, push, merge) | `skills/commit-workflow/SKILL.md` | No review gate, unreviewed code in production |
| Any Tortoise graph write (create point, operator, mitigation, NAND, supersede, annotate) | `skills/how-to-use-tortoise/SKILL.md` | EP weights nuked by batch-connected mitigations, orphaned NANDs |
| Writing an implementation plan | `skills/writing-plans/SKILL.md` | Unplanned code, missed design decisions |
| Scoping an issue | `skills/issue-scoping/SKILL.md` | Unscoped work, missed complexity rating |
| Reviewing a PR | `skills/code-review/SKILL.md` | Unreviewed code in production |
| Finding bugs | `skills/find-bugs/SKILL.md` | Missed regressions |
| Any non-trivial research | `skills/research/SKILL.md` | Shallow analysis, costly rework |

**Review gates are mandatory, not suggestions.** When a skill describes a review cycle, run it to convergence.

## Key Directories

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

## Environment

- Copy `.env.example` to `.env` before running
- `TORTOISE_DB_URI` — FalkorDB connection string (`docker://` or `bolt://`)
- `AGENT_INFRA_PATH` — Path to agent-infra repo (required for bootstrap, pre-commit version gate)
- See `.env.example` for all variables

## Model Selection (Pi)

- **Most tasks:** `deepseek-v4-flash` (base default)
- **Graphics/visual tasks:** `qwen3.8-max` (Qwen 3.8)
- **Highly complex / tricky tasks:** `qwen3.8-max` (Qwen 3.8)

## Git Workflow

- **Before any commit:** invoke `commit-workflow` skill
- Branch naming: `feat/`, `fix/`, `chore/` prefixes
- PRs auto-merge by default (no staging hold unless `deploy:staging` label)
- Pre-commit hook enforces agent-infra version sync via `.husky/pre-commit`

## Testing

```bash
# Run all tests with FalkorDBLite (embedded, no Docker needed)
python -m pytest tests/ -v

# Run specific test file
python -m pytest tests/test_directional_impl_fix.py -v

# Run with live FalkorDB (Docker required)
docker compose -f ../eldato/operations/memory/docker-compose.yml up -d
python -m pytest tests/ -v -m "not slow"
```

## Documentation Filing

For topic-to-file routing, see `docs/00_index.md`. When in doubt, open `docs/00_index.md`.

### Memory Hygiene

- `MEMORY.md` must stay under 150 lines.
- `MEMORY.md` = raw coding gotchas only. Not an implementation log.

## Key Differences from Claude Code

| Claude Code | Pi |
|---|---|
| Agent tool / Skill tool | `task` tool for sub-agents, skills loaded from files |
| `model: sonnet/opus` frontmatter | Ignored — Pi uses its own model selection |
| `allowed-tools` with granular Bash | Use Pi's tool names: `read write edit bash grep find web_search web_fetch todo_write task` |
| MCP servers via `.mcp.json` | MCP tools available via mcp-client extension |
| `superpowers:skill-name` references | Use skill name directly (e.g., `commit-workflow`) |

---
> **DO NOT EDIT BELOW THIS LINE** — managed by agent-infra update
> agent-infra version: 0.1.0
