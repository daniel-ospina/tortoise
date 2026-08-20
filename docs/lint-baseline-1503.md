# Lint Baseline (#1503) — ruff + mypy full-pass baseline

> Issue: #1503 — "ruff/mypy config + baseline for tortoise — enable the shared full lint"

The shared agent-infra python-ci lint (ci.yml `agent-infra-ci` job, #303) previously ran a
narrow syntax-class pass (`ruff check . --select E9,F63,F7`) because tortoise had no
ruff/mypy config and the full pass reported ~19,000 violations. This change adds the config
and records a baseline so the **full** pass runs green in CI and cleanup is measurable.

## Config

- `pyproject.toml` → `[tool.ruff]` + `[tool.ruff.lint]` + `[tool.mypy]`
  (agent-infra convention: `pyproject.toml` holds `[tool.ruff]` / `[tool.mypy]`, see
  agent-infra `docs/ci-centralization-plan.md`).
- Rule surface is **explicit** (`select = ["E","F","B","UP","I","SIM","RUF"]`) — ruff 0.16
  expanded its *default* surface to 413 rules (up from the classic E4/E7/E9/F 59), so relying
  on the default would drift the baseline with every ruff bump.
- `line-length = 100` — matches the codebase's actual distribution (812 `tortoise/` lines
  > 100 vs 1,830 > 88) and the historical flake8 `max-line-length = 100`.
- `ignore = ["E501","RUF001","RUF002","RUF003"]` — deferred style rules, never enforced:
  E501 duplicates `ruff format`'s line-length handling; RUF001/2/3 (ambiguous unicode) are
  noisy on this repo's multi-language comment surface (Latin-1 quotes).
- mypy: `python_version = "3.12"`, `ignore_missing_imports = true`,
  `check_untyped_defs = true` (the agent-infra workflow's flags, moved into config).

## Baseline (measured 2026-08-20, origin/main, ruff 0.16.3 + mypy 2.3.1)

| Tool | Command | Before | After |
|------|---------|--------|-------|
| ruff | `ruff check .` (select above, line-length 100) | **3,753** violations / 534 files | **0** |
| mypy | `mypy tortoise/` | **406** errors / 58 files (123 checked) | **0** |

Combined pre-config violation surface: **4,159** (the ~19k figure in #1503 was a wider rule
estimate; the measured baseline under the pinned config is what cleanup tracks).

## How the baseline is recorded

- **ruff** — per-line `# noqa: <codes>` directives auto-applied with
  `ruff check . --add-noqa` (3,442 directives + 7 manual fixes). The full rule set stays
  **active for new code**: only already-violated lines are grandfathered. Cleanup = fix a
  violation, delete its `# noqa` directive.
- **mypy** — config-level `disable_error_code` for every code present in the first pass
  (mypy has no `# type: ignore` auto-add equivalent; a per-line ignore sweep across 58 files
  was judged noisier than the config baseline). Per-code counts below.

### mypy baseline per error code (before → after)

| Error code | Count | Meaning |
|------------|------:|---------|
| `attr-defined` | 120 | attribute missing on typed value |
| `arg-type` | 99 | argument incompatible with parameter type |
| `assignment` | 50 | incompatible assignment |
| `name-defined` | 31 | name not defined |
| `return-value` | 18 | function expected a return value |
| `union-attr` | 16 | attribute missing on some union member |
| `var-annotated` | 15 | unannotated variable |
| `index` | 13 | invalid index type |
| `operator` | 9 | invalid operator operand |
| `no-redef` | 9 | name redefined |
| `misc` | 8 | misc errors (no dedicated code) |
| `dict-item` | 6 | invalid dict item |
| `call-arg` | 6 | missing/extra call argument |
| `list-item` | 2 | invalid list item |
| `call-overload` | 2 | no overload matches |
| `override` | 1 | signature incompatible with base |
| `func-returns-value` | 1 | function returns a value where none expected |
| **Total** | **406** | |

## Cleanup (follow-up issue)

Remove a code from `[tool.ruff.lint] ignore` / `[tool.mypy] disable_error_code`, fix the
surfaced violations, re-measure, and update this table. Suggested order: start with the
mechanical ruff fixes (`--fix` handles I001/F401/F841/E401/UP017...), then the mypy
low-count codes (`override`, `func-returns-value`, `call-overload`, `list-item`).
