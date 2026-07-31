# AGENTS.md — Premise Labs (Tortoise)

> Graph engine for semantic, epistemic, episodic, and procedural agent memory.

## Project Identity

Tortoise is a Python graph engine backed by FalkorDB. It powers the El Dato agent memory system:
- **SDK:** `tortoise/` — Python API for graph operations
- **MCP Server:** Agent tool exposure via MCP
- **EP Engine:** Expectation Propagation belief propagation
- **Connectors:** GitHub, Linear, Slack ingestion

## Conventions

### Python

- Python 3.11+. No build step — interpreted.
- Install: `pip install -e .`
- Imports: prefer `from pathlib import Path` for path resolution — never hardcode absolute paths
- Type hints: `from __future__ import annotations` at top of all modules

### Paths

- **Repo root:** `Path(__file__).resolve().parent.parent` (from tests/) or `Path(__file__).resolve().parent` (from tortoise/)
- **Import tortoise:** `sys.path.insert(0, str(Path(__file__).resolve().parent))` from scripts or tests

### Testing

```bash
# Run all tests with FalkorDBLite (embedded, no Docker needed)
python -m pytest tests/ -v

# Run specific test file
python -m pytest tests/test_directional_impl_fix.py -v

# Run with live FalkorDB (Docker required)
docker compose -f ../eldato/operations/memory/docker-compose.yml up -d
python -m pytest tests/ -v -m "not slow"
```

### Environment

- Copy `.env.example` to `.env` before running
- `TORTOISE_DB_URI` — FalkorDB connection string (`docker://` or `bolt://`)
- See `.env.example` for all variables

## Key Directories

| Path | Purpose |
|------|---------|
| `tortoise/` | SDK, EP engine, MCP server, clients |
| `tests/` | Test suite (pytest) |
| `scripts/` | Utility scripts (pricing decisions, migrations) |
| `config/` | YAML configs (routing, pipelines) |
| `docs/` | Architecture, ontology, legal docs |
| `premise-labs/` | Landing page (`premise-labs/index.html`) |
| `data/` | Event logs, extracted documents |
| `validation/` | Schema validation rules |

## Git Workflow

- **Before any commit:** invoke `commit-workflow` skill — see `operations/skills/commit-workflow/SKILL.md`
- Branch naming: `feat/`, `fix/`, `chore/` prefixes
- PRs auto-merge by default (no staging hold unless `deploy:staging` label)

## Tortoise Graph Operations

- **Before any graph write** (create_point, create_operator, mitigate, NAND, supersede, annotate): read `skills/how-to-use-tortoise/SKILL.md`
- Graph writes are structural — wrong edge types nuke EP propagation
