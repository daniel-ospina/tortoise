# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added — battery harness core (#1406)

The Agent-Reasoning Eval Battery harness core (epic #1402): a `battery/` package extending `tools/longmem_eval` runner patterns — episode runner (trajectory logging, seed pinning, model-call outcome tracking, batch scenario setup), CLI (`run|parity|calibrate|validate-judge|report` + exit codes 0/1/2/3/4/5), and `battery/config/` YAML loaders (corpus/thresholds/arms/budget with [cal] table lock + gold sha256 verification).

- **`battery run`** (mock arms, no API keys): per-scenario `run_artifact.json` (run_id = seed+arm+scenario, model-call outcome enum {ok, rate_limited, timeout, fallback_cached, failed}, ep_outcome) + `summary.json`; budget guard (exit 1); empty corpus refuses with exit 5 (E2E-1.4); all-failed → exit 4 after artifacts.
- **`--batch-setup`** (N+1 fix): batched scenario graph writes at ≤2 DB round-trips per scenario (2·N total) at the query boundary, with batch==naive graph-state equivalence and idempotent MERGE setup.
- **Determinism (E2E-7.1)**: same seed + temp 0 + PYTHONHASHSEED-pinned subprocess runs → |Δ| ≤ 1e-6 across metric values; per-attempt `TORTOISE_DB_PATH` isolation.
- **Contracts for child issues**: `ArmAdapter` protocol + `ArmUnavailable`, `Scorer` seam (`ScorerResult{metrics, ep_outcome}`), contract exceptions (`JudgeGateBlocked`→2, `InconclusiveRun`→3, `IsolationBreach`→4, `EmptyCorpus`→5), run_artifact/summary schemas v1.0, sealed-gold boundary at the corpus loader.

### Added — selfhost→hosted export→import migration path (#1230)

A first-class migration path: `tortoise export` produces a versioned, encrypted artifact (`tortoise-export-v1`, AES-256-GCM, encrypt-by-default) from any selfhost graph (Docker FalkorDB or embedded FalkorDBLite), and the hosted `POST /v1/teams/{team_id}/import` endpoint ingests it into a team graph — preserving **Point IDs and edge topology** (belief scores are derived; EP recomputes server-side). Import is owner-scoped with streaming size/rate caps, a fail-closed validation chain (format → blob sha256 → key fingerprint → decrypt → payload sha256 → counts), temp-graph verify-before-atomic-swap, quarantine on failure (live graph never touched), and a `last_import_sha256` idempotency ledger (re-import → 200 `already:true`).

- **`tortoise export`** (CLI): envelope + encrypt-by-default; `TORTOISE_BACKUP_KEY` or an ephemeral key printed once on the stdout JSON line (`key_b64`); `--no-encrypt` warns loudly; one-line JSON stdout contract.
- **`POST /v1/teams/{team_id}/import`** (hosted): owner-scoped session auth (mirrors the export surface), raw-artifact (`X-Tortoise-Import-Key` header) or JSON-body wire forms, graph-name import-mode override (selfhost graph name never matched server-side).
- **E2E parity**: `test_parity_export_import` in the E2E-12-D suite beats the replay baseline (content-presence) with structure parity — node/edge counts, every source Point ID, and operator edge topology match the source after the round-trip.
- **Docs**: both quickstarts' migration sections now lead with the automated export→import path; manual replay stays the documented fallback.

### Added — thin client package (#526)

The physical client/server split: `pip install` no longer embeds the engine.

- **`tortoise-client` (new distribution, Apache-2.0):** thin MCP network driver built from the same repo via the Prefect `prefect-client` pattern (`client/` build directory — `client/build_client.sh` stages the client subset of `tortoise/`; `client/verify_client.sh` runs the acceptance gate). Ships only `tortoise/mcp_client.py` (driver) + `tortoise/config.py` + `tortoise/exceptions.py` (shared config/types) + a `tortoise_client` re-export shim with a minimal `tortoise-client` CLI (`status` / `list-tools` / `call`).
- **Dependency split:** client pulls only `fastmcp-slim[client]==3.4.6` + `httpx>=0.27` — no falkordb / falkordblite / numpy / scipy / fastapi (enforced by CI gate).
- **No breakage:** `tortoise-graph` (BSL-1.1) is byte-identical in behavior — it still ships the full `tortoise.*` tree, daemon, and MCP server; `tortoise.mcp_client` remains importable from the server package.
- **Version coupling:** both dists release in lockstep with the same version; a client of minor `X.Y` targets a server of minor `X.Y` (documented in docs/client-server-split.md).
- **CI:** per-PR `client-build` job (build + acceptance gate) and tagged-release `build-client`/`publish-client` jobs in publish-pypi.yml (Trusted Publishing against a new `pypi-client` environment).

### Self-hosted trust (#942)

The durable multi-writer path is now the documented default; embedded
FalkorDBLite is honestly bounded to single-writer eval.

- **Docs flip**: README quickstart, docs/quickstart-selfhosted.md,
  website/self-hosted.html, and infra-runbook §4.5 lead with
  `docker compose up -d` (daemon + FalkorDB sidecar: AOF, named volume,
  healthcheck, loopback-published sidecar port). Embedded is labeled
  single-agent eval only everywhere; the self-hosted.html comparison table
  is now a decision table; `.env.example`/canonical host URI unified on
  `docker://:falkordb@localhost:6379/tortoise`.
- **CI proof**: new pre-merge job `test-concurrency-falkor` runs the TRUE
  cross-worker concurrency tests against a real `falkordb/falkordb-server`
  service container (`test_seq_is_monotonic_under_concurrency_live_falkor`
  — 8 workers, one shared graph, contiguous global seqs; and
  `test_concurrent_writers_live_falkor_no_lost_writes` — 5 processes, no
  lost writes). Both skip visibly when `TORTOISE_DB_URI` is unset; the job
  fails if they skip (anti-vacuity guard) and enforces the docs flip with a
  consistency grep.
- **Honest guard**: embedded mode now emits a loud SINGLE-WRITER / EVAL-ONLY
  banner at every runtime entrypoint — `serve --http` (any auth mode), the
  daemon, the stdio MCP entrypoint (`tortoise serve` /
  `python -m tortoise.mcp_server`), `tortoise key create` (the team-mode
  minting moment), and `tortoise init`. `--auth tenant` on embedded is
  marked single-agent eval only (decision: WARN, not refuse — documented in
  #942).

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
