<!-- research-path: issue #176 scoping comments (research summary + confirmed problem + final plan) -->

# Redislite Process Leak Fix — Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Eliminate the redislite embedded-mode redis-server process leak (650+ orphans, ~1.7GB RSS) via stable-path unification + reaper + lifecycle hardening + regression gates.

**Team:** epistemic-team
**Role:** (none set)

**Architecture:** Three coupled children shipped in order (C1+C2 coupled — reaper without migration is a zombie that sees but can't act on CWD leaks; C3 after):
- **Child 1 (Safety Net):** stateless reaper killing ONLY no-path tempdir orphans (socket-location discriminator — path-based servers incl. stable singleton are NEVER killed) + lifecycle hardening (context manager, atexit, idempotent close, setsid).
- **Child 2 (The Fix):** `TORTOISE_DB_PATH` unification + hard-reject of relative paths + import-time loud-fail guard + ~35-point migration + `tortoise migrate-db` CLI (JSONL rebuild).
- **Child 3 (Hardening):** Docker routing verification + REQUIRED pre-commit/CI grep + CHANGELOG + .gitignore + concurrency/chaos tests.

Key verified facts (2026-08-06, empirical): redislite native path-keyed reuse works (same path cross-process = 1 server); no-path = per-connection tempdir server; `execute_command('CLIENT','LIST')` + redis-py unix-socket CLIENT LIST + PING all work; 4-way concurrent connect to dead path = 1 server (no double-spawn); registry pidfile NOT rewritten after SIGKILL+respawn (hence CLIENT LIST, not pidfile); SIGKILL+reconnect respawns cleanly; current origin/main uses `redislite.falkordb_client.FalkorDB` (not falkordb directly); `tortoise/projection.py` is a re-export SHIM (#166) — all implementation lives in `tortoise/projection/__init__.py`.

### Pattern Research

Skipped per workflow/02 skip rule — redislite behavior was empirically verified in the Research stage (4 Perplexity queries + 4 reproducible tests in `tests/repro/reproduce_redislite_leak.py`, all PASS). No new third-party API surface beyond what's already verified: redislite client exposes `execute_command('CLIENT','LIST')` (verified), standalone redis-py 8.0.1 available as fallback.

### Integration Surface Map

| # | Surface | Test Layer | How | Bug Pattern Flags |
|---|---------|-----------|-----|-------------------|
| S1 | redislite spawn/kill (OS processes) | Integration (process) | `tests/repro/reproduce_redislite_leak.py` (exists) + reaper tests | Orphan accumulation, double-spawn |
| S2 | unix socket CLIENT LIST/PING protocol | Integration | reaper unit tests against live redislite socket | TOCTOU, busy-vs-dead misjudgment |
| S3 | tempdir scan + `*.db.settings` registry | Integration (filesystem) | reaper tests with fabricated orphan dirs | Stale pidfile, false discovery |
| S4 | env config resolution (TORTOISE_DB_PATH, allow_nonstandard, whitelist) | Unit | config resolution unit tests | Env collision (TORTOISE_DB_PATH vs EMBEDDED), default precedence |
| S5 | signal handling (SIGTERM/SIGINT/atexit) | Integration (process) | lifecycle tests: SIGTERM child, check cleanup | Handler leaks, killpg on wrong server |
| S6 | FalkorProjection path validation (hard-reject + escape hatch) | Unit | test_projection.py hard-reject cases | Relative path silently creating per-CWD leak, escape hatch suppressing legitimate use |
| S7 | JSONL event log rebuild (migrate-db) | Integration | migrate-db test: create embedded.db, migrate, verify rebuild | Binary-copy data loss (must use rebuild_all) |
| S8 | CI pre-commit grep | Config (shell) | pre-commit hook test on fixture files | Bypass (-noverify), false positives |

### Journey Test Map

Skipped — no user-facing UI journeys. Consumers are scripts/CLI/agents. Covered by surface map.

### Verification Plan (test-routing, standard tier)

- **Unit:** env config resolution (S4), path validation hard-reject (S6), allow_nonstandard_path semantics
- **Integration:** reaper sweep against live sockets (S1/S2/S3), lifecycle signals (S5), migrate-db rebuild (S7)
- **E2E (process-level):** concurrency stress (5 parallel same-path → 1 server, integrity read-back, 0 orphans after); chaos test (20 no-path servers, 10 with clients → reaper kills only idle 10; SIGKILL mid-query → reaper cleans)
- **Config:** pre-commit hook fixture check (S8)
- **Skipped:** UX (no UI), Content, Research

**Tech Stack:** Python 3.14, redislite 6.2.x (embedded Redis), falkordb client, redis-py 8.0.1 (reaper fallback), pytest, pre-commit, bash

---

## Child 1 — Safety Net: Reaper + Lifecycle Hardening

### Task 1: Reaper module — discovery + classification

**Intent:** The reaper must find orphaned no-path redis-server processes and classify them (killable no-path tempdir orphan vs protected path-based server) using the socket-location discriminator + MIN_UPTIME boot-cooldown protection.

**Acceptance:** `tortoise/embedded_reaper.py` exists; `discover()` returns list of {pid, socket_path, dbdir, client_count, uptime}; path-based servers (socket NOT under `tempfile.gettempdir()`) classified `protected`; tempdir-socket servers with uptime < 30s classified `protected` (boot cooldown — P0 fix: reaper must not kill servers during connection-establishment window); tempdir-socket servers with uptime >= 30s classified `candidate`. **MIN_UPTIME configurable via `TORTOISE_REAPER_MIN_UPTIME` env (default 30) — REQUIRED so tests/chaos don't sleep 30s (C2 P0 fix: the boot-cooldown broke its own tests).** Discovery uses `os.path.realpath()` on both socket and tempdir (symlink-safe — P2 fix). **Secondary classification signal (C2 P0 fix + C3 P0 fix): socket location alone is insufficient — a path-based server with `TORTOISE_DB_PATH=/tmp/...` puts its socket under tempdir and would be misclassified as killable. Read the parent `*.db.settings` registry: if it references a named `db_filename` (path-based server) → `protected`; only ephemeral/autogenerated dbdirs (no named db_filename) → `candidate`. Both signals must agree for `candidate`. **C3 fallback for OLD .settings files (pre-#90 era lack `db_filename`):** if `db_filename` is ABSENT from the registry, fall back to checking the parent dir contents — if it contains a `*.db` file (path-based server) → `protected`; only dirs with NO `.db` file and auto-generated names (`redislite_XXXXXX` / `tmpXXXX` pattern) → `candidate`. Add test `test_discover_protects_path_based_server_with_old_settings()` (fabricate pre-#90 .settings without db_filename).**
**Files:**
- Create: `tortoise/embedded_reaper.py`
- Test: `tests/test_reaper.py`

**Step 1: Write the failing test**
```python
# tests/test_reaper.py
def test_discover_classifies_no_path_orphan():
    # start a no-path FalkorDB -> tempdir socket
    from redislite.falkordb_client import FalkorDB
    db = FalkorDB()  # no path -> tempdir
    found = discover()
    matches = [s for s in found if s['socket_path'] == SOCK]
    assert matches and matches[0]['classification'] == 'candidate'
    db.close()

def test_boot_cooldown_protects_fresh_servers():
    # freshly spawned server (uptime < 30s) must be 'protected', not 'candidate'
    db = FalkorDB()
    found = discover()
    match = [s for s in found if s['socket_path'] == SOCK][0]
    assert match['classification'] == 'protected'  # MIN_UPTIME=30s
    db.close()

def test_min_uptime_env_override():
    # TORTOISE_REAPER_MIN_UPTIME=0 disables cooldown for tests (C2 fix)
    os.environ['TORTOISE_REAPER_MIN_UPTIME'] = '0'
    db = FalkorDB()
    found = discover()
    match = [s for s in found if s['socket_path'] == SOCK][0]
    assert match['classification'] == 'candidate'  # cooldown disabled
    db.close()

def test_discover_protects_path_based_server_under_tempdir():
    # path-based server whose socket IS under tempdir must still be 'protected'
    # (registry db_filename signal overrides socket location) — C2 P0 fix
    proj = FalkorProjection(f'{tempfile.gettempdir()}/canonical-test.db')
    found = discover()
    match = [s for s in found if s['socket_path'] == SOCK][0]
    assert match['classification'] == 'protected'
    proj.close()
```
**Step 2: Run test → FAIL** (`module not found`)
**Step 3: Implement** — scan `tempfile.gettempdir()` (realpath) for `redis.socket` files; for each, read parent `*.db.settings` registry; classify by socket location + uptime (MIN_UPTIME=30s boot cooldown); use `execute_command('CLIENT','LIST')` for client count. **MIN_UPTIME parsing SHARED helper (C5 P2 fix — discover() and the CLI must use the same float→int conversion, or `TORTOISE_REAPER_MIN_UPTIME="30.5"` crashes discover() while the CLI handles it): extract `_parse_min_uptime()` used by both.**
**Step 4: Run test → PASS**
**Step 5: Commit** — `git add tortoise/embedded_reaper.py tests/test_reaper.py && git commit -m "feat(reaper): discovery + socket-location classification + boot cooldown (issue #176)"`

### Task 2: Reaper kill logic — safe verification + NEVER_KILL

**Intent:** Only kill verified-idle no-path orphans; NEVER kill path-based servers (stable singleton or CWD leaks — those are Child 2's job).

**Acceptance:** `reap()` kills only servers classified `candidate` AND CLIENT LIST shows zero active clients (double-check before+after) AND PING 2s×3 fails-or-idle; path-based servers (`NEVER_KILL` set = any socket outside tempdir) are skipped with WARNING log.
**Files:**
- Modify: `tortoise/embedded_reaper.py`
- Test: `tests/test_reaper.py`

**Step 1:** Write test — create no-path server with a live client (CLIENT LIST >0) → reap() must NOT kill it; close client → reap() kills it.
**Step 2:** Run → FAIL (no kill logic)
**Step 3:** Implement — CLIENT LIST check (double-check before+after to narrow TOCTOU), PING 2s×3, SIGTERM → 10s → SIGKILL, `SKIPME yes` for own connection, WARNING log for skipped path-based servers. **Critical (P0 fix):** liveness-first — check `/proc/<pid>` (Linux) or `ps` (macOS) BEFORE connecting; never let a redis-py connect spawn a server (use raw socket protocol or liveness-first check). Set `socket_connect_timeout=2` on all health-check connections (P1 fix: a hung socket must not block the whole sweep — log warning + continue). **Ordered discovery procedure (C3 P0 fix + C5 P2 fix — Phase 1 removal would delete live respawned servers' sockets before the stale-PID probe could run; the two must be one ordered sequence):** Phase 1 = for each registry PID, check liveness via `/proc/<pid>`/ps. If PID dead AND socket file exists → **raw unix-socket connect probe FIRST** (never redis-py — can't spawn; probe = plain socket connect with 2s timeout): (a) probe refused/ECONNREFUSED → classify `stale_socket`, remove socket + tempdir immediately; (b) probe accepts → server is LIVE despite stale registry PID (known: registry pidfile NOT rewritten after SIGKILL+respawn) → derive real PID via `/proc/*/fd` inode scan (Linux) or `lsof` (macOS), reclassify as live candidate for Phase 2; (c) **probe TIMES OUT (2s, neither refused nor accepted) → classify `undetermined` (C5 P2 fix — timeout≠dead; a hung-but-live respawned server's socket must NOT be removed), log WARNING, skip — same three-way principle as Phase 2.** **NEVER remove before probing.** Phase 2 = only connect (CLIENT LIST/PING) to live-PID candidates. **After successful kill, `shutil.rmtree` the orphan's tempdir (C2 P1 fix — otherwise stale sockets accumulate and are rediscovered every sweep).** **Dead-socket connect failures logged WARNING (not ERROR) + socket removed (C2 P1 fix).** **THREE-WAY health-check classification (C4 P1 fix — timeout ≠ dead; a hung-but-live server must NOT be killed): (1) ECONNREFUSED/dead → candidate for kill; (2) timeout/hung → classify `undetermined`, log WARNING, SKIP (never SIGTERM a server whose liveness is unknown); (3) responding → normal CLIENT LIST/PING logic. Add test `test_reap_skips_hung_server_not_dead` (raw socket server that accept()s but never responds).** **Per-file error isolation (C4 P2 fix + C5 P2 fix — one corrupt .settings OR a permission-denied subdir must not crash the whole sweep): wrap the top-level tempdir scan (`os.listdir`/`os.scandir`) in try/except PermissionError → log WARNING with offending path, skip entry, continue; ALSO wrap each .settings read + each socket probe in try/except — corrupt/unreadable file → log WARNING, skip, continue; tests `test_discover_skips_corrupt_settings_continues_sweep` + `test_discover_skips_permission_denied_dir_continues_sweep` (chmod 000 subdir).** **Unknown old-settings dirname (C4 P2 fix — third case in the C3 fallback: no db_filename, no .db file, non-matching dirname): default to `protected` with WARNING `unrecognized dir pattern, treating as protected` — never crash, never default to killable; test `test_discover_unknown_old_settings_pattern_defaults_protected`.**
**Step 4:** Run → PASS
**Step 5:** Commit

### Task 3: Reaper CLI + periodic execution — --dry-run default, --batch-size, --json, cron/launchd wiring

**Intent:** Safe-by-default CLI for bootstrap (650 orphans) AND periodic sweeps (P0 fix — acceptance criteria require 300s periodic execution, not just a run-once bootstrap tool).

**Acceptance:** `python -m tortoise.embedded_reaper` defaults to dry-run (prints planned kills, kills nothing); `--no-dry-run` acts; `--batch-size N` limits kills per run; `--json` outputs machine-readable; exit 0. **Periodic wiring delivered:** install doc + script for cron (`*/5 * * * * python -m tortoise.embedded_reaper --no-dry-run`) or macOS launchd plist (5-min interval) — the reaper is a genuine safety net, not a one-shot tool.
**Files:**
- Modify: `tortoise/embedded_reaper.py` (argparse CLI)
- Create: `docs/infra/embedded-reaper-cron.md` (cron + launchd install instructions)
- Test: `tests/test_reaper.py` (CLI subprocess tests) + cron/launchd config verification

**Step 1:** Write CLI test — subprocess run defaults dry-run, no processes killed; **MIN_UPTIME invalid-value parametrized test (C3 P1 fix + C4 P2 fix): `["-1", "abc", "", "999999", "30.5"]` → parse as float first then truncate to int (int("30.5") raises ValueError — C4 P2 fix); negative treated as 0 (warning), non-numeric/empty → default 30 (warning), huge → accepted with warning if >3600; never crash the reaper on bad env.**
**Step 2:** Run → FAIL
**Step 3:** Implement argparse CLI.
**Step 4:** Run → PASS
**Step 5:** **Reaper singleton lock (C2 P1 fix):** `~/.tortoise/.reaper.lock` with fcntl exclusive lock — second concurrent instance (cron overlap, manual bootstrap vs cron) logs `reaper already running (PID N)` and exits 0. **`--timeout N` with HARD DEFAULT 120s (C3 P1 fix — a hung raw socket probe must not hold the lock forever, silently starving all cron sweeps; implement via signal.SIGALRM or threading.Timer + os._exit, not a cooperative check a hung socket never reaches); `TORTOISE_REAPER_TIMEOUT` env overrides default (CLI --timeout still wins) — needed for slow-NFS bootstrap runs (C4 P2 fix); test: socket that never responds → reaper exits non-zero after timeout with clear log; env-vs-CLI precedence test.** Write cron/launchd install doc (5-min cadence, matching agent_cron's 2-min spawn pattern so orphans are cleaned within 2-3 spawn cycles).
**Step 6:** Commit

### Task 4: Lifecycle hardening — context manager + atexit + idempotent close

**Intent:** Graceful-exit cleanup so SIGTERM/SIGINT/normal exits don't orphan (SIGKILL still needs reaper — accepted).

**Acceptance:** `FalkorProjection` supports `with FalkorProjection(...) as p:`; `close()` idempotent (2nd call no-op); `atexit.register(self.close)`; `weakref.finalize`; no per-instance signal handlers; `setsid` on spawn **CONDITIONAL (C3 P1 fix — acceptance must match Step 3's feasibility check): if redislite exposes a spawn hook (preexec_fn/start_new_session), use it; if not feasible, document in CHANGELOG + Known Accepted Risks — reaper + atexit + context manager are the actual safety net.**
**Files:**
- Modify: `tortoise/projection/__init__.py` (add `__enter__`/`__exit__`, idempotent `close`, atexit/finalize, setsid)
- Test: `tests/test_projection.py` (context manager + rapid open/close no-hang)

**Step 1:** Write test — `with FalkorProjection(...)` closes on exit; double-close no-op; 100 rapid open/close no hang.
**Step 2:** Run → FAIL
**Step 3:** Implement — verify redislite exposes a hook for `preexec_fn`/`start_new_session` on its internal Popen (research first: read redislite source `client.py` `_start_redis`). If a hook exists, use it. If NOT (likely — redislite controls the spawn internally), **downgrade setsid from acceptance to best-effort** with CHANGELOG note: setsid isolation is not feasible without forking redislite; the reaper + atexit + context manager provide the actual safety net. Do NOT block the task on setsid (P1 fix — feasibility verified before committing to it).
**Step 4:** Run → PASS. Also run `pytest tests/test_ingest.py` — the `_noop_close` monkeypatch (line 140-145) may now be removable; if redislite hang persists, keep the patch and add a CHANGELOG known-limitation entry (file upstream redislite issue if warranted) — fold this decision into Task 5.
**Step 5:** Commit

### Task 5: Remove test_ingest.py close monkeypatch (if hang fixed)

**Intent:** The `_noop_close` patch existed because redislite shutdown hung on rapid close; lifecycle hardening should fix this.

**Acceptance:** `tests/test_ingest.py:140-145` monkeypatch removed; ingest tests pass without it.
**Files:**
- Modify: `tests/test_ingest.py` (remove `_noop_close`/`_patch_close`)
- Test: `tests/test_ingest.py`

**Step 1:** Remove the monkeypatch, run tests → PASS (or FAIL if hang persists → keep patch, note in plan).
**Step 2:** Commit

---

## Child 2 — The Fix: Stable Path + Migration + Guards

### Task 6: TORTOISE_DB_PATH unification + config resolution

**Intent:** One env var for the canonical embedded DB path; resolve defaults with correct precedence. Must ALSO reconcile mcp_server.py's URI-vs-path split (P0 fix — mcp_server reads TORTOISE_DB_URI for file paths while hosted_api reads TORTOISE_DB_PATH; two env vars for the same purpose).

**Acceptance:** New `tortoise/config.py`: `resolve_db_path()` returns `TORTOISE_DB_PATH` env → `~/.tortoise/tortoise.db` default; `TORTOISE_DB_PATH` (not EMBEDDED) is the single source; hosted_api.py's existing usage preserved (default `/data/tortoise.db` on Fly). **mcp_server.py:22-45 updated** to use `resolve_db_path()` for the non-docker fallback — no more split personality. **Explicit env precedence rules (C2 P0 fix — three consumers read different env vars):** (1) `TORTOISE_DB_URI` with `docker://` prefix → URI mode (resolve_db_path() NEVER called — add test that docker:// is not resolved to a file path); (2) `TORTOISE_DB_PATH` env → file path; (3) `TORTOISE_DB_URI` without `docker://` → treated as file path (backward compat — mcp_server.py:44 existing behavior); (4) default `~/.tortoise/tortoise.db`. **When both non-docker URI and PATH are set: PATH wins with a logged warning.** Call order in mcp_server: check `docker://` FIRST, then `resolve_db_path()` — never resolve a URI to a file path. **C3 P0 wiring fixes — the SDK is the choke-point and both mcp_server and sdk currently read ONLY TORTOISE_DB_URI, blind to TORTOISE_DB_PATH:** (a) `TortoiseSDK.__init__` (sdk.py:78-86): when `db_path is None` AND `TORTOISE_DB_URI` unset → default `db_path = resolve_db_path()` (test: set only TORTOISE_DB_PATH → `TortoiseSDK()` uses it); (b) `mcp_server.py:22-46`: wire `resolve_db_path()` into the non-docker fallback (currently only checks TORTOISE_DB_URI; with only TORTOISE_DB_PATH set it falls through to no-DB mode — test: set only TORTOISE_DB_PATH → mcp_server uses that path).
**Files:**
- Create: `tortoise/config.py`
- Modify: `tortoise/projection/__init__.py`, `tortoise/sdk.py`, `tortoise/hosted_api.py`, `tortoise/mcp_server.py`
- Test: `tests/test_config.py`

**Step 1:** Write unit tests — env precedence, default, Fly override; **empty-string + whitespace TORTOISE_DB_PATH (C3 P2 fix — `os.environ.get` can't distinguish unset from `""`; resolution strips and rejects empty/whitespace → falls through to default with logged warning, never passes `""` to FalkorProjection).**
**Step 2:** Run → FAIL
**Step 3:** Implement.
**Step 4:** Run → PASS
**Step 5:** Commit

### Task 7: Hard-reject relative paths + allow_nonstandard_path escape hatch

**Intent:** Prevent Category-3 (per-CWD) leaks at the choke-point; fail loudly with actionable error.

**Acceptance:** `FalkorProjection('tortoise.db')` raises ValueError with 3 remedies listed; `allow_nonstandard_path=True` (public kwarg) or env `TORTOISE_ALLOW_NONSTANDARD_PATH=1` bypasses; absolute paths (incl. tempdirs) unaffected; error message enumerates remedies.
**Files:**
- Modify: `tortoise/projection/__init__.py`
- Test: `tests/test_projection.py` (hard-reject + escape hatch cases)

**Step 1:** Write tests — relative path raises; absolute passes; escape hatch bypasses for absolute non-canonical paths ONLY; **relative path raises EVEN WITH escape hatch (P1 fix — `FalkorProjection('tortoise.db', allow_nonstandard_path=True)` MUST still raise ValueError; the escape hatch never permits relative paths — that's the whole point of the fix);** empty-string path raises clear error; `~/path` (tilde, not expanded) treated as relative → raises with tilde-expansion hint (P2 fix); message lists 3 remedies.
```python
# MUST raise DESPITE escape hatch (relative is always a hard block)
with pytest.raises(ValueError):
    FalkorProjection('tortoise.db', allow_nonstandard_path=True)
# Escape hatch allows non-standard ABSOLUTE paths only
proj = FalkorProjection('/tmp/custom.db', allow_nonstandard_path=True)
```
**Step 2:** Run → FAIL
**Step 3:** Implement.
**Step 4:** Run → PASS. Run full `pytest tests/test_projection.py` — ensure temp-path tests (absolute) unaffected. **Add mcp_server process-level error-surface test (P1 fix):** `subprocess.run mcp_server with TORTOISE_DB_PATH=./relative.db → non-zero exit, stderr contains the 3 remedies`.
**Step 5:** Commit

### Task 8: Import-time loud-fail guard for direct redislite imports

**Intent:** Catch future direct `redislite.falkordb_client.FalkorDB` imports that bypass FalkorProjection (the 4 known bypasses + any future code).

**Acceptance:** `tortoise/__init__.py` wraps `redislite.falkordb_client.FalkorDB` → RuntimeError for non-canonical paths; standalone scripts importing tortoise first are guarded. **Mechanism SPECIFIED (P0 fix):** `tortoise/__init__.py` imports `redislite.falkordb_client.FalkorDB`, subclasses/wraps it with an `__init__` that raises RuntimeError when `path` is relative, and re-exports the wrapped class on the module as `tortoise.FalkorDB`. **CRITICAL correction (C2 P0 fix): the wrapper does NOT intercept `projection/__init__.py:131`'s `from redislite.falkordb_client import FalkorDB` — Python resolves that to the ORIGINAL class regardless of what tortoise exports. Protection for the projection path comes from Task 7's hard-reject INSIDE `FalkorProjection.__init__` (which is the real choke-point). The wrapper protects only code importing tortoise's re-export or importing redislite AFTER `import tortoise`.** **Known limitation documented (P1 fix):** if code imports `redislite.falkordb_client.FalkorDB` BEFORE `import tortoise`, its local reference is unwrapped → the guard is best-effort, not security. The pre-commit grep (Task 13) + CI are the source-level enforcement. Do NOT monkeypatch `redislite` module globally (breaks non-tortoise redislite users — P1 fix: scope the guard to tortoise's own import surface).
**Files:**
- Modify: `tortoise/__init__.py`
- Test: `tests/test_guard.py`

**Step 1:** Write tests — (a) `import tortoise` first, then `import tortoise; from tortoise import FalkorDB; FalkorDB('relative.db')` raises RuntimeError (C2 P0 fix — test tortoise's OWN re-export, NOT `redislite.falkordb_client` which is never intercepted); (b) absolute path passes through the wrapper; (c) no-arg `FalkorDB()` passes through; (d) `FalkorProjection('/tmp/test.db')` still works — validates the ARCHITECTURE: Task 7's own hard-reject accepts absolute paths, wrapper doesn't break the projection module (C2 P0 fix — previous test claimed wrapper intercepts projection's import, which is wrong); (e) import `redislite.falkordb_client.FalkorDB` FIRST then tortoise → unwrapped reference = documented bypass, test asserts it does NOT raise (locks in known limitation, pre-commit covers it); (f) `redislite.Redis()` direct import also bypasses wrapper — documented, covered by Task 13 grep (C2 P2 fix).
**Step 2:** Run → FAIL
**Step 3:** Implement wrapper (subclass, not global monkeypatch).
**Step 4:** Run → PASS
**Step 5:** Commit

### Task 9: Migrate graph-scripts (19 files)

**Intent:** Route all 19 embedded graph-scripts to the canonical path; remove redundant direct redislite imports.

**Acceptance:** Grep shows 0 graph-scripts with `FalkorProjection('tortoise.db')` relative calls; all use `FalkorProjection()` (no-arg → canonical) or explicit TORTOISE_DB_PATH; 3 fix_670X scripts lose their redundant `redislite.falkordb_client` imports.
**Files:**
- Modify: 14 relative-path scripts (auto_discovery_*, bp_*, cost_control_*, test_6707_shock, resolve-github), 4 absolute-path scripts (fix_6704/6706/6709, add_convergence_evidence)
- Test: grep-based verification + run 2 representative scripts

**Step 1:** Write verification command (grep). 
**Step 2:** Run migration edits.
**Step 3:** Run grep → 0 matches; run `python graph-scripts/bp_approach_cycle1.py` smoke → works against canonical path.
**Step 4:** Commit

### Task 10: Migrate library code (pipeline_cli, backup, __main__, mcp_server, sdk, ingest, hosted_api, validation/, test_cross_ontology, smoke_test)

**Intent:** Close all remaining embedded connection points (~15 sites across library + validation + cross-repo).

**Acceptance:** Grep for `FalkorProjection('` (relative) and `FalkorProjection(DB_PATH)` with non-canonical paths → 0 in tortoise/ and graph-scripts/; all use `resolve_db_path()` or `allow_nonstandard_path=True` for legit multi-DB cases (restore).
**Files:**
- Modify: `tortoise/pipeline_cli.py:131`, `tortoise/backup.py:75/96/106`, `tortoise/__main__.py:11/788/1164/281/283/304`, `tortoise/mcp_server.py:40-46`, `tortoise/sdk.py`, `tortoise/ingest.py:121`, `tortoise/hosted_api.py`, `tortoise/session_continuity.py:72`, `tortoise/migrate_kinds.py:118`, `tortoise/connectors/github_docs.py:36`, `tortoise/test_cross_ontology.py`, `tortoise/tortoise_client.py:157` (C6 P2 — was prose-only, now catalogued), `graph-scripts/setup.py:962`, `graph-scripts/smoke_test.py:98` (intentional bypass + noqa), `validation/*` (tempdir files NO change)
- Cross-repo: `scripts/restore_graph_issue100.py` (agent-infra + tortoise copies)
- Test: grep verification + `pytest tests/ -x -q` (existing suite green)

**Step 1:** Grep inventory → confirm 0 remaining relative calls after edits. **Split inventory into two categories (C2 P1 fix): (a) relative-path fixes (hard-reject blockers — must change to canonical); (b) already-absolute paths routed through `resolve_db_path()` for consistency (e.g., pipeline_cli.py:131 `str(_PROJECT_ROOT / 'tortoise.db')` is absolute → category (b), not a hard-reject fix).** **Verify graph-scripts/setup.py in the grep inventory (C6 P2 fix — only graph-scripts/setup.py exists, no root setup.py; its :962 uses project-relative `str(PROJECT / "tortoise.db")` and must be edited).**
**Step 2:** Edit each site: category (a) → canonical path; category (b) → `resolve_db_path()`; restore script → `allow_nonstandard_path=True` for /tmp source. **validation/ split (C4 P2 fix — blanket migration would break tempdir test isolation): `validate_tortoise_ep.py` + `svbp_gate4.py` use intentional tempfile.mkdtemp() isolation — NO changes (absolute temp paths already pass hard-reject); only validation files using relative/non-canonical paths get edited. test_cross_ontology.py: (C5 P1 fix — its module-level `DB_PATH = "tortoise-test-s6.db"` at :25 is RELATIVE and would be hard-rejected): change DB_PATH to an absolute temp path or `resolve_db_path()`; ALSO add guard against `--db` pointing at canonical path (line 176 `unlink` is destructive — C4 P2 fix); AND add `tortoise/test_cross_ontology.py` to Task 13 pre-commit exclusions (it lives under tortoise/, not tests/ — C5 P2 fix).** **ingest.py explicit pre-check (C2 P0 fix — the `Path(args.db).exists()` chain means a relative non-existent path falls through to 'Docker unreachable' instead of the clean hard-reject error): add before the exists() check — `if args.db and not os.path.isabs(args.db) and not args.db.startswith('docker://'): raise ValueError(relative-path message with 3 remedies)` + CLI error-surface test.** **__main__.py:281 explicit rename (C2 P1 fix): `~/.tortoise/embedded.db` default → `resolve_db_path()` default (`tortoise.db`) — this is a name change, not just routing.** **__main__.py:283 CONCRETE EDIT (C4 P0 fix — the `_cmd_init` raw `from redislite.falkordb_client import FalkorDB; db = FalkorDB(db_path)` bypasses FalkorProjection entirely so Task 7's hard-reject + Task 4's lifecycle never apply): replace with `from tortoise.projection import FalkorProjection; proj = FalkorProjection(db_path)` — routes init through the choke-point (verify init's create-if-absent behavior still works — C5 P2 fix).** **smoke_test.py:98 — DECISION (C5 P1 fix — earlier C2/C4 text conflicted): smoke_test.py uses a `_SmokeProj` duck-type + raw `select_graph()`/`delete_graph()` handles; it does NOT import tortoise and uses absolute tempdir paths (safe). Document it as an INTENTIONAL bypass: keep the raw redislite import (add `# noqa: redis-guard` for Task 13's hook), route its embedded path through `resolve_db_path()`; do NOT rewrite the duck-type.** **Unlisted consumers CONCRETE EDITS (C4/C5 P0 fix — `tortoise/session_continuity.py:72`, `tortoise/migrate_kinds.py:118`, `tortoise/connectors/github_docs.py:36` all read TORTOISE_DB_URI directly and would ignore a valid TORTOISE_DB_PATH): replace their `os.environ.get("TORTOISE_DB_URI")` reads with `resolve_db_path()` fallback (import from tortoise.config) + update error messages to mention TORTOISE_DB_PATH.** **graph-scripts/setup.py:962 CONCRETE EDIT (C5 P1 fix — uses `str(PROJECT / "tortoise.db")` which is project-relative and would be hard-rejected): change to `resolve_db_path()` or `str(PROJECT.resolve() / "tortoise.db")` (absolute).** **tortoise_client.py:157 (C5 P2 fix — diagnostic payload reads only TORTOISE_DB_URI): change to `os.environ.get("TORTOISE_DB_URI") or os.environ.get("TORTOISE_DB_PATH", "not set")`.** **__main__.py:304 (C6 P2 fix — init sets `os.environ.setdefault("TORTOISE_DB_URI", docker://...)`; DO NOT setdefault TORTOISE_DB_PATH in docker mode — a local file path is semantically wrong when the DB is remote; consumers fall back to the docker URI. Only setdefault TORTOISE_DB_PATH in the embedded-init branch.)**
**Step 3:** Run `pytest tests/ -x -q` → existing suite green. **Add explicit `_cmd_init` create-if-absent test (C6 P2 fix — existing suite may not cover init's create-if-absent behavior after FalkorProjection routing; verify a fresh init still creates the DB).**
**Step 4:** Commit

### Task 11: `tortoise migrate-db` CLI — embedded.db → tortoise.db (data-safe)

**Intent:** One-time, idempotent, JSONL-based migration of legacy `~/.tortoise/embedded.db` to canonical path — NEVER binary copy (data-loss guard). P0 fixes: backup before rebuild, advisory lock against concurrent migration, marker written ONLY after verified rebuild, interrupted-rebuild recovery.

**Acceptance:** `python -m tortoise migrate-db`:
1. **Backup first (P0 fix):** `cp ~/.tortoise/embedded.db ~/.tortoise/embedded.db.bak-<timestamp>` before rebuild (restore point if rebuild fails).
2. **Advisory lock (P0 fix):** `~/.tortoise/.migrate.lock` (fcntl/OS lock) — concurrent migrate-db runs serialize; exactly one wins, others skip with message.
3. JSONL `rebuild_all()` if tortoise.db absent + embedded.db exists; **tortoise.db partially-present + no marker = INCOMPLETE migration (P0 fix)** → delete partial + rebuild cleanly (never treat as no-op).
4. **Marker `~/.tortoise/.migrated-v2` written AFTER successful rebuild + integrity verification** — marker-before-rebuild would block recovery (P1 fix). **Integrity = node-ID-set equality + edge (src,dst,type) tuple equality + content spot-check: 5 random source nodes fetched via GRAPH.QUERY compared byte-for-byte vs target, 3 random edges verified (C2 P1 fix — count-only comparison is shallow and misses corrupt properties/edges).**
5. Handles FileNotFoundError (no-op) + FileExistsError. **Skip-if-marker-present does a DB integrity probe first (C2 P0 fix — marker + corrupt/truncated DB must NOT silently skip; probe = open DB + GRAPH.LIST/count; on failure delete corrupt DB + marker + re-migrate from source, or fail with clear error + `--force` flag to bypass marker).** Uses logging.warning not print (stdio-safe). **Backup failure aborts migration (C2 P1 fix): wrap backup in try/except — on OSError, exit non-zero with `backup failed: <reason>. Migration aborted. Source DB intact.` — never proceed without the restore point.**
**Files:**
- Modify: `tortoise/__main__.py` (add migrate-db command), `tortoise/backup.py` (restore reads DB filename from manifest, not hardcoded `tortoise.db`)
- Test: `tests/test_migrate_db.py`

**Step 1:** Write tests — (a) happy path: create legacy embedded.db with known events (50 PointAdded, 10 OperatorAdded+edges, 5 PointRevised, 3 PointRetracted, 2 PointsMerged), run migrate-db, **assert node-ID-set + edge (src,dst,type) tuples identical source vs target + content spot-check on 5 nodes/3 edges (P0 fix — data completeness, not just 'exit 0') + replay source events through InMemoryProjection and compare full dict vs target (C3 P2 fix + C6 P2 fix — full-dict replay is strictly more comprehensive than the ≥20% property-hash spot-check, so the spot-check is DROPPED to avoid redundancy)**; (b) idempotency: second run skips via marker; (c) **concurrent: two subprocesses migrate simultaneously → exactly one wins (lock)**; (d) **interrupted: SIGKILL mid-rebuild → next run detects partial (marker absent + tortoise.db present) → rebuilds cleanly**; (e) **marker-present-but-DB-missing → re-migrates or clear error, never silent exit 0**; (e2) **marker-present-but-DB-corrupt (truncated/zero-byte) → detects via integrity probe, re-migrates or clear error (C2 P0 fix)**; (e3) **BOTH embedded.db AND tortoise.db exist without marker — DISTINGUISHING RULE (C4 P0 fix + C5 P2 fix — tests (d) and (e3) previously specified opposite behavior for the same state, unexecutable): probe tortoise.db integrity via GRAPH.LIST/open — if corrupt/empty (0 nodes) AND embedded.db has >0 events → treat as interrupted migration → delete partial + rebuild cleanly (test d path); if VALID (has nodes) → genuine conflict → exit with clear error `Both embedded.db and tortoise.db exist without migration marker — resolve manually or use --force`; with --force, rename tortoise.db → tortoise.db.bak-conflict-<ts> before rebuild; **if BOTH are empty/0-events → NO-OP (C5 P2 fix — an intentionally-created empty canonical DB must not be deleted; a 0-event embedded.db + 0-node tortoise.db is a valid no-op, skip with message). The integrity probe + event-log length is the discriminator.**;** (e4) **corrupt JSONL event log (C3 P1 fix — truncated final line or non-JSON mid-file must NOT silently skip): either fail with clear error naming the corrupt line, or migrate valid events AND report skipped/unparseable line count — never silent**; (f) FileNotFoundError no-op; (g) backup file created + **backup source-read error (mock copy2 → OSError(EIO)) → aborts, source intact (C3 P1 fix) + `.bak-*` accumulation: log warning if >3 exist, note cleanup policy**; (h) **backup failure (mock copy2 → OSError) → aborts, source intact (C2 P1 fix)**. **Promote manifest-based restore to P0 within Task 11 (C2 P0 fix — pre-migration backups have `embedded.db` in their manifest; restore hardcodes `tortoise.db` and would fail to find pre-migration backups): `test_restore_reads_db_name_from_manifest` in main test suite, not post-hoc.**
**Step 2:** Run → FAIL
**Step 3:** Implement (backup → **lock FIRST** → both-DBs conflict check UNDER lock → rebuild_all → integrity verify → marker). **C4 P1 fix — the both-DBs conflict check must be INSIDE the lock (two concurrent `--force` migrators would otherwise both rename tortoise.db → the second fails or renames the loser's backup): lock acquired BEFORE the conflict check; add test `test_concurrent_force_both_dbs` (two subprocesses with --force, both-DBs pre-existing → exactly one rename wins, one .bak-conflict-* exists, winner rebuilds intact).** **C4 P2 fix — `--force` rename failure (os.rename → OSError EPERM/ENOSPC): abort cleanly with `rename failed: <reason>. Migration aborted. Resolve manually.`, source + existing tortoise.db intact, never proceed to rebuild; test `test_force_rename_failure_aborts_cleanly`.**
**Step 4:** Run → PASS. **Add standalone backup manifest test (P1 fix):** `test_restore_reads_db_name_from_manifest` — manifest `{"db": "embedded.db"}` + no tortoise.db → restore finds embedded.db; `custom-name.db` manifest → uses it, not hardcoded default. Run `pytest tests/test_backup.py` → green.
**Step 5:** Commit

---

## Child 3 — Hardening: Docker Routing + Regression Gates

### Task 12: Concurrency stress + chaos tests (with C1 feedback gate)

**Intent:** Prove the stable-path contract under load (1 server for N processes, no corruption) and reaper correctness under chaos (kills only idle). P0 fix: explicit feedback-loop gate when chaos tests surface C1 bugs.

**Acceptance:** New `tests/test_embedded_concurrency.py`: (a) 5 parallel subprocesses same canonical path → exactly 1 redis-server, integrity read-back (all keys present/correct), 0 orphans after; **crash-recovery variant (P1 fix): after all 5 write ≥1 key, SIGKILL one mid-write → remaining 4 still read/write, exactly 1 server, all keys from survivors + pre-crash keys from killed writer intact, 0 orphans**; (b) chaos: 20 no-path servers, 10 with live clients → reaper kills only the 10 idle, leaves 10 with clients; SIGKILL mid-query → reaper finds and cleans; **client-retry validation (P1 fix): client in read/write loop survives reaper sweep of its server via reconnect+retry, no data loss**; **reaper-SIGKILL-mid-sweep (P1 fix): SIGKILL reaper after first SIGTERM → second reaper run cleans all remaining, no errors on zombie sockets; + SIGKILL reaper WHILE HOLDING the singleton lock → fcntl auto-releases, second reaper acquires and sweeps (C3 P2 fix — verifies lock release on SIGKILL in the test environment)**.
**Files:**
- Create: `tests/test_embedded_concurrency.py`
- Test: itself (process-level)

**Step 1:** Write stress test → run; MAY fail (surfaces integration gaps between C1 and C2 — failure is a signal, not expected).
**Step 2:** Run → iterate until PASS. **Feedback gate (P0 fix):** if fixes to Child 1 files (reaper/lifecycle) are needed → apply fix → re-run `pytest tests/test_reaper.py tests/test_projection.py` (C1 unit/integration) → re-run Task 12 → gate green. No unverified C1 edits.
**Step 3:** Commit

### Task 13: Pre-commit + CI regression grep (REQUIRED)

**Intent:** Prevent reintroduction of relative-path connections and direct redislite imports — the source-level enforcement that covers the import-order bypass of Task 8's guard.

**Acceptance:** `.pre-commit-config.yaml` exists with a grep hook blocking `FalkorProjection\(['"](?!/|~)` (relative), `from redislite.falkordb_client import FalkorDB`, `from redislite import.*\bRedis\b` / `redislite\.Redis\(` (C2 P2 fix — redislite.Redis parent class spawns servers identically and is a bypass), `^\s*import\s+redislite\.falkordb_client` and `from\s+redislite\.falkordb_client\s+import\s+\*` (C4 P2 fix — module-import and wildcard forms bypass the wrapper guard), and `Path("tortoise.db")` argparse defaults (P2 fix — resolve-github.py:49 uses this pattern, invisible to the first grep); **exclusions for `tests/` and `validation/` dirs (P0 fix — those files legitimately import redislite for availability gating: test_projection.py:55, test_supplementary.py:22, test_extractor_priors.py:37, test_projection_version_gate.py:32, tests/repro/*, validation/test_e2e_extraction_ep.py:43) + explicit per-file exclusion for `tortoise/test_cross_ontology.py` and `graph-scripts/smoke_test.py` (C6 P2 fix — smoke_test is an intentional bypass: use a Python-script hook that understands `# noqa: redis-guard` comments, not raw grep; test_cross_ontology lives under tortoise/ not tests/ so dir-exclusion misses it)**; hook tested on fixture files (rejects bad, accepts good, accepts test-dir imports); CI job runs the same check and **BLOCKS merge (branch protection — P1 fix: CI must be a gate, not advisory, to cover `--no-verify`)**.
**Files:**
- Create: `.pre-commit-config.yaml`, `.github/workflows/redis-guard.yml` (or add to existing CI) + branch protection config
- Test: hook fixture test (shell) — bad relative call fails, test-dir direct import passes, Path-default pattern caught

**Step 1:** Create hook + fixtures (bad file → fails, good file → passes, test-dir import → passes).
**Step 2:** Run pre-commit → correct behavior.
**Step 3:** Add CI job that runs the same grep and fails the PR (branch protection requires it).
**Step 4:** Commit

### Task 14: CHANGELOG + .gitignore + entrypoint/fly verification

**Intent:** Release-note the breaking changes; prevent DB files from being committed; verify hosted wiring.

**Acceptance:** `CHANGELOG.md` (new) with Unreleased section covering: embedded.db→tortoise.db rename + migrate-db, relative-path rejection + remedies, TORTOISE_DB_PATH semantics, reaper CLI, lifecycle changes; `.gitignore` has `*.db`; `fly.toml` `[[mounts]]` destination=/data confirmed; entrypoint.sh priority documented (already correct).
**Files:**
- Create: `CHANGELOG.md`
- Modify: `.gitignore`, `fly.toml` (verify only)
- Test: grep verification

**Step 1:** Write CHANGELOG Unreleased section.
**Step 2:** Add `*.db` to .gitignore.
**Step 3:** Verify fly.toml mounts + entrypoint.
**Step 4:** Commit

### Task 15: Bootstrap the 650 existing orphans (operational) + CWD-leak orphan cleanup

**Intent:** Clean the accumulated orphans safely via the reaper bootstrap protocol. P0 fix: reaper NEVER_KILLs path-based servers, so CWD-leak orphans (from old relative-path scripts) need an EXPLICIT cleanup path — not a silent residual.

**Acceptance:** (a) Reaper run in dry-run (reports planned kills) → human review → `--batch-size 10` → verify → `--batch-size 100` → confirm 0 no-path orphans. (b) **CWD-leak cleanup (P0 fix):** after migration (Child 2) is deployed, run `lsof -i -U | grep redis-server` to enumerate path-based servers (sockets in CWD/dbdir, NOT tempdir); for each, verify CLIENT LIST shows 0 active clients, then SIGTERM manually (they are leftovers from pre-migration scripts — no live clients expected post-migration); log each. Gate criteria: **proceed to ship only if ≤ 5 path-based orphans remain and all no-path orphans are cleaned; > 5 → escalate to manual cleanup before declaring done.**
**Files:**
- None (operational run)
- Test: `ps aux | grep redislite/bin/redis-server | wc -l` → trending to 0; CWD-leak count ≤ 5

**Step 1:** `python -m tortoise.embedded_reaper --dry-run` → review output.
**Step 2:** `--no-dry-run --batch-size 10` → verify.
**Step 3:** `--no-dry-run --batch-size 100` → verify.
**Step 4:** Post-migration: enumerate path-based servers via lsof → verify 0 clients → SIGTERM → verify ≤ 5 remain.
**Step 5:** Confirm final count. Document results in issue #176.

---

## Post-Plan Notes

- **Execution mode:** >8 tasks → Parallel Session (separate session via executing-plans).
- **Task parallelization (P2 fix from review):** linear numbering is for execution clarity; independent batches are — Batch A {Task 1, Task 6} (foundations), Batch B {Task 2, Task 7, Task 8} (depends on A), Batch C {Task 3, Task 4, Task 9, Task 10} (depends on B), Batch D {Task 5, Task 11, Task 12, Task 13, Task 14} (depends on C), Batch E {Task 15} (operational, last).
- **Rollback (P2 fix from review):** (1) delete `~/.tortoise/.migrated-v2` + restore `embedded.db.bak-*` if migrate-db data lost; (2) set `TORTOISE_DB_PATH=~/.tortoise/embedded.db` to revert path; (3) revert CHANGELOG entry.
- **Known accepted risks (from scoping + review):** TOCTOU on no-path kills (transient, client retry — validated in Task 12 chaos); 1 bounded stable-path orphan if SIGKILLed (intentional — NEVER_KILL); setsid infeasibility if redislite lacks spawn hook (downgraded to best-effort, CHANGELOG note); per-CWD old DBs orphaned on disk after migration (JSONL rebuildable — migrate-db note); Task 8 guard is best-effort against import-order bypass (pre-commit + CI are the enforcement).
- **Related issues:** #190 (dead Fly embedded fallback), #191 (hardcoded DB password — rotate credential).
