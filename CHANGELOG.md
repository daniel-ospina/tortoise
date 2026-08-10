# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added — service model (#338)

Tortoise is repositioned from a pip library to a **service** (MongoDB-style):
run it, connect your tools over MCP.

- **Self-host daemon** (`tortoise.selfhost`): thin single-tenant service —
  MCP Streamable HTTP at `/mcp` + `/health` + `/health/ready`; `auth_mode`
  param on `create_http_app` (`tenant` default = hosted byte-identical;
  `static` = API key; `none` = localhost-bound eval); `tortoise-serve http`
  CLI.
- **MCP client** (`tortoise.mcp_client`): thin driver over fastmcp's built-in
  client — zero new deps; graceful degradation (daemon down → skip).
- **Connectors**: twenty bridge converted SDK→MCP (`integrations/` now has
  zero engine imports).
- **Docker**: `Dockerfile.selfhost` + GHCR publish workflow +
  `docker-compose.yml` (daemon + FalkorDB sidecar, AOF on) — durable
  self-host reference.
- **License**: Business Source License 1.1 — free self-hosted production use
  under $5M annual revenue; MPL 2.0 conversion after 4 years; hosted =
  commercial with free tier. See `docs/license-notes.md` (clause → precedent).
- **`.mcp.json`**: tortoise entry points at the daemon (`http://localhost:8000/mcp`).

### Fixed — EP NAND under-propagation (#855)

Restored genuine cascade propagation through IMPL chains. Two root causes:
- **NAND base weight**: plain NAND carried the generic weight 1.0 vs
  `phi_nand`'s documented w=8.0 default → 8× weaker contradiction potential.
  Now `NAND_BASE_WEIGHT = 8.0` (mitigated NAND 8×2=16 → clamped 10.0).
- **`phi_impl` coupling**: product coupling `exp(w·ca·cb)` was insensitive to
  source strength — a contradicted source's weakness didn't reach its
  dependents. Changed to **difference (level-matching) coupling**
  `exp(-w·(ca-cb)²)`: the target tracks the source's level, so damage
  cascades downstream (C1 drop 0.001 → 0.022; B feedback 0.000 → 0.006).

**Also ships:** NAND factor messages no longer receive the evidence-scaled
proportional boost (would crush weak claims at w=8); EP default convergence
tolerance tightened 1e-3 → 1e-4 (more iterations per run).

**Behavior note:** difference coupling adds a small (~0.008) downward drag on
strong sources supporting weak targets (deliberate level-matching semantics,
per the canonical SVBP reference). The #86 bidirectional-IMPL NAND-style
back-message hack was removed — its role is now handled by the coupling.

### Fixed — redislite embedded process leak (issue #176)

The embedded (redislite) mode leaked one `redis-server` OS process per
connection path — 650+ orphans (~1.7 GB RSS) accumulated on dev machines.
Root cause: redislite spawns a dedicated server per non-reusable path, and
orphaned them when parents were SIGKILL'd. Fixed via three layers:

**Reaper (`tortoise.embedded_reaper`)**
- New `python -m tortoise.embedded_reaper` CLI:
  - `--no-dry-run` (default is **dry-run** — reports, kills nothing)
  - `--batch-size N` (limit kills per run)
  - `--json` (machine-readable output)
  - `--timeout N` (default 120s; env `TORTOISE_REAPER_TIMEOUT`)
  - Singleton lock (`~/.tortoise/.reaper.lock`) prevents cron/manual overlap
  - `TORTOISE_REAPER_MIN_UPTIME` env (default 30s) boot-cooldown
- Kills ONLY no-path tempdir orphans; path-based servers (stable singleton,
  CWD leaks) are NEVER touched (dual-signal classification via
  `redis.config` `dbfilename` + `dir`)
- Cron/launchd 5-min periodic install: `docs/infra/embedded-reaper-cron.md`

**Stable-path unification (`tortoise.config`)**
- New `TORTOISE_DB_PATH` env var — the single canonical embedded DB path
  (default `~/.tortoise/tortoise.db`); `resolve_db_path()` resolves with
  explicit precedence (`docker://` URI > `TORTOISE_DB_PATH` > non-docker URI
  as file > default). SDK + mcp_server wired; consumers (session_continuity,
  migrate_kinds, github_docs, tortoise_client) no longer dead-end on
  `Set TORTOISE_DB_URI` when only `TORTOISE_DB_PATH` is set.
- `FalkorProjection()` no-arg now resolves the canonical path (graph-scripts
  migrated to it)

**Relative-path rejection (breaking)**
- `FalkorProjection('tortoise.db')` now raises `ValueError` with 3 remedies.
  Relative paths are NEVER permitted — they silently created per-directory
  servers (Category-3 leak). Use `allow_nonstandard_path=True` (or env
  `TORTOISE_ALLOW_NONSTANDARD_PATH=1`) for **absolute** non-canonical paths
  only (restore/migration tools).
- `tortoise.FalkorDB` import guard: raises `RuntimeError` for relative paths
  (best-effort; pre-commit hook is the enforcement — see below)

**Lifecycle hardening**
- `FalkorProjection` now: context manager (`with ... as p:`), idempotent
  `close()`, `weakref.finalize` (GC cleanup), `atexit` (normal process exit
  never orphans). No per-instance signal handlers.
- `test_ingest.py` close-monkeypatch removed (hang fixed by lifecycle work)
- Known limitation: redislite's `close()` is inherently slow (~4s); setsid
  isolation infeasible (no preexec hook in redislite's subprocess spawn)

**Migration CLI**
- New `python -m tortoise migrate-db [--force]`: data-safe migration of
  legacy `~/.tortoise/embedded.db` → canonical `tortoise.db`. Backup-first
  (abort on backup failure), advisory lock, 3-way conflict discriminator,
  marker written only after verified rebuild, JSONL rebuild (never binary
  copy; RDB snapshot fallback only when no event log exists).
- `--force` bypasses the marker / overwrites a conflicting `tortoise.db`

**Regression gates**
- New `tools/redis-guard.py` pre-commit/CI hook blocks reintroduction of:
  relative-path `FalkorProjection` calls, direct
  `redislite.falkordb_client` imports, `redislite.Redis` bypass, and
  `Path("tortoise.db")` defaults. `# noqa: redis-guard` for documented
  intentional bypasses (smoke_test). CI job + branch protection require it.
- `.gitignore` now ignores `*.db`

### Known limitation
- **Concurrent multi-process WRITERS on one embedded redislite file lose
  data** (startup race — verified empirically). The safe embedded pattern is
  single-writer/multi-reader; route concurrent multi-process writes to
  Docker FalkorDB (`TORTOISE_DB_URI=docker://...`).
