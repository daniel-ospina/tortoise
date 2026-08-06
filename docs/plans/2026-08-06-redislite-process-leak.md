<!-- research-path: issue #176 scoping comments (research summary + confirmed problem + final plan) -->

# Redislite Process Leak Fix — Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Eliminate the redislite embedded-mode redis-server process leak (650+ orphans, ~1.7GB RSS) via stable-path unification + reaper + lifecycle hardening + regression gates.

**Team:** epistemic-team
**Role:** (none set)

> **Fix-annotation legend:** `"Cn Px fix"` (e.g. `C4 P2 fix`) = fix applied in **review cycle n, priority x** (C1..C6 = the 6 plan-review cycles). These are NOT the architectural Children (C1/C2/C3 = reaper+lifecycle / migration+guards / hardening — the shipping units).

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
| S9 | Reaper self-protection (singleton lock + timeout) | Integration (process) | lock acquisition/release, SIGKILL-while-locked, timeout enforcement (R1#14 fix — reaper self-protection was unmapped) | Lock held forever on hung socket, cron starvation, lock not released on SIGKILL |

### Journey Test Map

Skipped — no user-facing UI journeys. Consumers are scripts/CLI/agents. Covered by surface map.

### Verification Plan (test-routing, standard tier)

- **Unit:** env config resolution (S4), path validation hard-reject (S6), allow_nonstandard_path semantics
- **Integration:** reaper sweep against live sockets (S1/S2/S3), lifecycle signals (S5), migrate-db rebuild (S7)
- **E2E (process-level):** concurrency stress (5 parallel same-path → 1 server, integrity read-back, 0 orphans after); chaos test (20 no-path servers, 10 with clients → reaper kills only idle 10; SIGKILL mid-query → reaper cleans); **S9 reaper self-protection (R3 fix — was unmapped): Integration = lock acquisition/release + timeout enforcement (Task 3 Step 1 tests); E2E = SIGKILL-while-locked in chaos test (Task 12)**
- **Config:** pre-commit hook fixture check (S8)
- **Skipped:** UX (no UI), Content, Research

**Tech Stack:** Python 3.14, redislite 6.2.x (embedded Redis), falkordb client, redis-py 8.0.1 (reaper fallback), pytest, pre-commit, bash

---

## Child 1 — Safety Net: Reaper + Lifecycle Hardening

### Task 1: Reaper module — discovery + classification

**Intent:** The reaper must find orphaned no-path redis-server processes and classify them (killable no-path tempdir orphan vs protected path-based server) using the socket-location discriminator + MIN_UPTIME boot-cooldown protection.

**Acceptance:** `tortoise/embedded_reaper.py` exists; `discover()` returns list of {pid, socket_path, dbdir, client_count, uptime}; path-based servers (socket NOT under `tempfile.gettempdir()`) classified `protected`; tempdir-socket servers with uptime < 30s classified `protected` (boot cooldown — P0 fix: reaper must not kill servers during connection-establishment window); tempdir-socket servers with uptime >= 30s classified `candidate`. **MIN_UPTIME configurable via `TORTOISE_REAPER_MIN_UPTIME` env (default 30) — REQUIRED so tests/chaos don't sleep 30s (C2 P0 fix: the boot-cooldown broke its own tests).** Discovery uses `os.path.realpath()` on both socket and tempdir (symlink-safe — P2 fix). **Secondary classification signal (C2 P0 fix + C3 P0 fix): socket location alone is insufficient — a path-based server with `TORTOISE_DB_PATH=/tmp/...` puts its socket under tempdir and would be misclassified as killable. Read the parent `*.db.settings` registry: if it references a named `db_filename` (path-based server) → `protected`; only ephemeral/autogenerated dbdirs (no named db_filename) → `candidate`. Both signals must agree for `candidate`. **(added in review cycle 3) fallback for OLD .settings files (pre-#90 era lack `db_filename`) — R7 fix: bare 'C3' was ambiguous (Child 3 vs cycle 3); now explicit:** if `db_filename` is ABSENT from the registry, fall back to checking the parent dir contents — if it contains a `*.db` file (path-based server) → `protected`; only dirs with NO `.db` file and auto-generated names (`redislite_XXXXXX` / `tmpXXXX` pattern) → `candidate`. Add test `test_discover_protects_path_based_server_with_old_settings()` (fabricate pre-#90 .settings without db_filename).**
**Files:**
- Create: `tortoise/embedded_reaper.py`
- Test: `tests/test_reaper.py`

**Step 1: Write the failing test** (boot-cooldown test uses `TORTOISE_REAPER_MIN_UPTIME=0` override to avoid a 30s wait, or `@pytest.mark.slow` — R2 P3 fix). **NAMING RULE (R8 fix — every test description in every Step 1 block must carry a `test_*` function name; anonymous descriptions (e.g., Task 4's 'with FalkorProjection closes on exit', Task 7's eight hard-reject scenarios, Task 8's (a)-(f), Task 11's (a)-(f), Task 12's parallel/crash-recovery variants) get explicit names at implementation time following the pattern `test_<behavior>_<condition>` (e.g., `test_relative_path_raises`, `test_absolute_path_passes`, `test_escape_hatch_allows_absolute_only`, `test_context_manager_closes_on_exit`, `test_double_close_noop`, `test_rapid_open_close_no_hang`, `test_parallel_five_servers_exactly_one`, `test_crash_recovery_keys_intact`). Executing agent assigns names before writing each test.**
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

def test_discover_protects_path_based_server_with_old_settings():
    # fabricate pre-#90 .settings WITHOUT db_filename field (R3 fix — was
    # Acceptance-only, now in Step 1 TDD cycle)
    # .db file present in parent dir -> 'protected' despite socket under tempdir
    ...

def test_discover_handles_symlinked_tempdir():
    # symlink pointing at a tempdir with an orphan -> still found + classified
    # correctly (R2 P3 fix, relocated from Step 3 into Step 1)
    ...

def test_reaper_excludes_own_connection_from_client_count():
    # SKIPME — reaper's own health-check connection not counted as a client
    # (R2 P3 fix, relocated from Step 3 into Step 1)
    ...

def test_discover_skips_permission_denied_dir_continues_sweep():
    # chmod-000 subdir -> skip + continue; restore perms in try/finally so
    # pytest tmp_path teardown doesn't cascade-fail (R2 P3 fix, relocated from
    # Step 3 prose into Step 1 — R7 fix: annotation completed)
    ...

def test_discover_skips_corrupt_settings_continues_sweep():
    # one corrupt/unreadable .settings must not crash the sweep (R4 fix — was
    # only in Task 2 Step 3 prose, now in Task 1 Step 1 where discovery lives)
    ...

def test_discover_unknown_old_settings_pattern_defaults_protected():
    # pre-#90 .settings, no db_filename, no .db file, non-matching dirname
    # -> 'protected' with WARNING, never crash, never killable (R5 fix — was
    # Step 3 prose, now in Task 1 Step 1)
    ...

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
**Step 3: Implement** — scan `tempfile.gettempdir()` (realpath) for `redis.socket` files; for each, read parent `*.db.settings` registry; classify by socket location + uptime (MIN_UPTIME=30s boot cooldown); use `execute_command('CLIENT','LIST')` for client count. **MIN_UPTIME parsing SHARED helper (C5 P2 fix — discover() and the CLI must use the same float→int conversion, or `TORTOISE_REAPER_MIN_UPTIME="30.5"` crashes discover() while the CLI handles it): extract `_parse_min_uptime()` defined in `tortoise/embedded_reaper.py`, **used by** both discover() and the CLI (R5 fix — same-file function, not an import; wording corrected).** (The symlink / SKIPME / permission-denied tests were relocated to Step 1 — R3 fix.)
**Step 4: Run test → PASS**
**Step 5: Commit** — `git add tortoise/embedded_reaper.py tests/test_reaper.py && git commit -m "feat(reaper): discovery + socket-location classification + boot cooldown (issue #176)"`

### Task 2: Reaper kill logic — safe verification + NEVER_KILL

**Intent:** Only kill verified-idle no-path orphans; NEVER kill path-based servers (stable singleton or CWD leaks — those are Child 2's job).

**Acceptance:** `reap()` kills only servers classified `candidate` AND CLIENT LIST shows zero active clients (double-check before+after) AND PING 2s×3 fails-or-idle; **servers classified `protected` by discover() are NEVER killed (R3 fix — NEVER_KILL = any server discover() marks protected via the DUAL signal: socket outside tempdir OR registry has db_filename / .db-file-present signal, matching Task 1's classification rules — NOT merely 'socket outside tempdir')** and are skipped with WARNING log.
**Files:**
- Modify: `tortoise/embedded_reaper.py`
- Test: `tests/test_reaper.py`

**Step 1:** Write tests — **`test_reap_skips_server_with_active_client`** (R4 fix — named; create no-path server with a live client, CLIENT LIST >0 → reap() must NOT kill it) + **`test_reap_kills_idle_server`** (R4 fix — named; close client → reap() kills it); **`test_reap_skips_hung_server_not_dead` (R3 fix — relocated from Step 3 prose into Step 1 TDD cycle: raw socket server that accept()s but never responds → classified `undetermined`, WARNING logged, NO SIGTERM sent, continue to next candidate)**; **`test_reap_skips_path_based_server` (R2 P2 fix — reap()-skips-protected was untested): start FalkorProjection with an absolute path, confirm discover() classifies protected, run reap() → server still running**; **`test_reap_escalates_to_sigkill_after_sigterm_timeout` (R2 P2 fix — 10s escalation untested): delay SIGTERM handling past 10s window, assert SIGKILL sent**; **`test_reap_removes_tempdir_after_kill` (R2 P2 fix — tempdir cleanup untested): assert tempdir gone from disk after reap() kills a candidate**; **Phase 1 tests (R5 fix — were Step 3 prose, now in Step 1): `test_phase1_removes_stale_socket_on_econnrefused` (ECONNREFUSED → stale_socket → remove) + `test_phase1_reclassifies_live_respawned_server` (probe accepts → derive real PID → reclassify live candidate; `@pytest.mark.skipif(platform != 'linux')` for inode-scan path) + `test_phase1_derives_real_pid_from_live_socket` (stale registry PID + live socket → derived real PID correct)**.
**Step 2:** Run → FAIL (no kill logic)
**Step 3:** Implement — CLIENT LIST check (double-check before+after to narrow TOCTOU), PING 2s×3, SIGTERM → 10s → SIGKILL, `SKIPME yes` for own connection, WARNING log for skipped path-based servers. **caplog assertions (R2 + R3 fix — canonical WARNING-site list, each must appear in caplog.text for ≥1 test): (1) 'skipping path-based server' (reap-skips-protected); (2) 'undetermined' hung-server skip; (3) 'permission denied' dir skip; (4) 'corrupt settings' file skip; (5) 'unrecognized dir pattern, treating as protected'; (6) 'stale socket removed'; (7) 'dead socket connect failure' — **mapped to `test_phase1_removes_stale_socket_on_econnrefused` (asserts BOTH #6 and #7 caplog entries; non-ECONNREFUSED connect errors fold into this path — R4 fix); (8) 'hung socket timeout' — **mapped alongside #2 to `test_reap_skips_hung_server_not_dead` (both 'undetermined' and 'hung socket timeout' WARNINGs emitted by the three-way timeout classification, asserted in the same test — R6 fix)**; (9) 'reaper already running' (singleton) → **`test_reaper_singleton_lock_prevents_concurrent_runs` (Task 3 Step 1 — R10 fix: explicit cross-reference)**; (10) MIN_UPTIME invalid-value warnings → **Task 3 Step 1(b) parametrized test (R10 fix: explicit cross-reference).** **Critical (P0 fix):** liveness-first — check `/proc/<pid>` (Linux) or `ps` (macOS) BEFORE connecting; never let a redis-py connect spawn a server (use raw socket protocol or liveness-first check). Set `socket_connect_timeout=2` on all health-check connections (P1 fix: a hung socket must not block the whole sweep — log warning + continue). **Ordered discovery procedure (C3 P0 fix + C5 P2 fix — Phase 1 removal would delete live respawned servers' sockets before the stale-PID probe could run; the two must be one ordered sequence):** Phase 1 = for each registry PID, check liveness via `/proc/<pid>`/ps. If PID dead AND socket file exists → **raw unix-socket connect probe FIRST** (never redis-py — can't spawn; probe = plain socket connect with 2s timeout): (a) probe refused/ECONNREFUSED → classify `stale_socket`, remove socket + tempdir immediately; (b) probe accepts → server is LIVE despite stale registry PID (known: registry pidfile NOT rewritten after SIGKILL+respawn) → derive real PID via `/proc/*/fd` inode scan (Linux) or `lsof` (macOS), reclassify as live candidate for Phase 2; (c) **probe TIMES OUT (2s, neither refused nor accepted) → classify `undetermined` (C5 P2 fix — timeout≠dead; a hung-but-live respawned server's socket must NOT be removed), log WARNING, skip — same three-way principle as Phase 2.** **NEVER remove before probing.** Phase 2 = only connect (CLIENT LIST/PING) to live-PID candidates. **After successful kill, `shutil.rmtree` the orphan's tempdir (C2 P1 fix — otherwise stale sockets accumulate and are rediscovered every sweep).** **Dead-socket connect failures logged WARNING (not ERROR) + socket removed (C2 P1 fix).** (Phase 1 tests live in Step 1 — R10 fix: residual Step 3 duplicate removed.) **THREE-WAY health-check classification (C4 P1 fix — timeout ≠ dead; a hung-but-live server must NOT be killed): (1) ECONNREFUSED/dead → candidate for kill; (2) timeout/hung → classify `undetermined`, log WARNING, SKIP (never SIGTERM a server whose liveness is unknown); (3) responding → normal CLIENT LIST/PING logic. (`test_reap_skips_hung_server_not_dead` lives in Step 1 — R5 fix, residual Step 3 copy removed.)** **Per-file error isolation (C4 P2 fix + C5 P2 fix — one corrupt .settings OR a permission-denied subdir must not crash the whole sweep): wrap the top-level tempdir scan (`os.listdir`/`os.scandir`) in try/except PermissionError → log WARNING with offending path, skip entry, continue; ALSO wrap each .settings read + each socket probe in try/except — corrupt/unreadable file → log WARNING, skip, continue. (Tests live in Task 1 Step 1 — R4 fix, removed the cross-task duplicate references.)** **Unknown old-settings dirname (C4 P2 fix — third case in the C3 fallback: no db_filename, no .db file, non-matching dirname): default to `protected` with WARNING `unrecognized dir pattern, treating as protected` — never crash, never default to killable; test `test_discover_unknown_old_settings_pattern_defaults_protected`.**
**Step 4:** Run → PASS
**Step 5:** Commit

### Task 3: Reaper CLI + periodic execution — --dry-run default, --batch-size, --json, cron/launchd wiring

**Intent:** Safe-by-default CLI for bootstrap (650 orphans) AND periodic sweeps (P0 fix — acceptance criteria require 300s periodic execution, not just a run-once bootstrap tool).

**Acceptance:** `python -m tortoise.embedded_reaper` defaults to dry-run (prints planned kills, kills nothing); `--no-dry-run` acts; `--batch-size N` limits kills per run; `--json` outputs machine-readable; exit 0. **Periodic wiring delivered:** install doc + script for cron (`*/5 * * * * python -m tortoise.embedded_reaper --no-dry-run`) or macOS launchd plist (5-min interval) — the reaper is a genuine safety net, not a one-shot tool.
**Files:**
- Modify: `tortoise/embedded_reaper.py` (argparse CLI)
- Create: `docs/infra/embedded-reaper-cron.md` (cron + launchd install instructions)
- Test: `tests/test_reaper.py` (CLI subprocess tests) + **cron/launchd config verification (R10 fix — DOCUMENTATION-ONLY deliverable, not a TDD test: `plutil -lint` the .plist + crontab line-format validation are documented in the install doc as post-install verification steps the operator runs, not automated tests — removed from the Test field's implied TDD scope)**

**Step 1:** Write CLI tests — (a) subprocess run defaults dry-run, no processes killed; **`test_reaper_singleton_lock_prevents_concurrent_runs` (R3 fix — two subprocess instances, second exits 0 with `reaper already running (PID N)`; relocated from Step 5 into Step 1) + `test_reaper_singleton_lock_released_on_sigkill` (R3 fix — SIGKILL lock-holder → fcntl auto-releases, second acquires; relocated from Step 5)**; (b) **MIN_UPTIME invalid-value parametrized test (C3 P1 fix + C4 P2 fix): `["-1", "abc", "", "999999", "30.5"]` → parse as float first then truncate to int (int("30.5") raises ValueError — C4 P2 fix); negative treated as 0 (warning), non-numeric/empty → default 30 (warning), huge → accepted with warning if >3600; never crash the reaper on bad env**; (c) **`test_json_output()` — `--json` emits parseable machine-readable output (R1#6 fix)**; (d) **`test_batch_size_limits_kills()` — 20 candidates + `--batch-size 5` → exactly 5 killed, 15 remain (R1#6 fix)**; (e) **`test_timeout_enforcement()` — socket that never responds → reaper exits non-zero after `--timeout` (R1#6 fix — formalized from Step 5 prose)**; (f) **`test_timeout_env_vs_cli_precedence` (R5 fix — was Step 5 prose, now in Step 1: env=300 + `--timeout 60` → CLI wins → 60s) + `test_timeout_respects_default_120s` (R5 fix — no --timeout, no env → exits after 120s default on hung socket)**. **All CLI `subprocess.run` calls use `timeout=30` (R2 fix — hung reaper must not hang the test harness).**
**Step 2:** Run → FAIL
**Step 3:** Implement argparse CLI.
**Step 4:** Run → PASS
**Step 5:** **Reaper singleton lock (C2 P1 fix — implementation):** `~/.tortoise/.reaper.lock` with fcntl exclusive lock — second concurrent instance (cron overlap, manual bootstrap vs cron) logs `reaper already running (PID N)` and exits 0. Tests live in Step 1 (R3 fix — removed the duplicate R2 lock-test paragraph and the stale 'moved from Task 12' annotation). **`--timeout N` with HARD DEFAULT 120s (C3 P1 fix — a hung raw socket probe must not hold the lock forever, silently starving all cron sweeps; implement via signal.SIGALRM or threading.Timer + os._exit, not a cooperative check a hung socket never reaches); `TORTOISE_REAPER_TIMEOUT` env overrides default (CLI --timeout still wins) — needed for slow-NFS bootstrap runs (C4 P2 fix); test: socket that never responds → reaper exits non-zero after timeout with clear log (Tests live in Step 1 — R8 fix: residual Step 5 duplicate of `test_timeout_env_vs_cli_precedence` removed.** Write cron/launchd install doc (5-min cadence, matching agent_cron's 2-min spawn pattern so orphans are cleaned within 2-3 spawn cycles).
**Step 6:** Commit

### Task 4: Lifecycle hardening — context manager + atexit + idempotent close

**Intent:** Graceful-exit cleanup so SIGTERM/SIGINT/normal exits don't orphan (SIGKILL still needs reaper — accepted).

**Acceptance:** `FalkorProjection` supports `with FalkorProjection(...) as p:`; `close()` idempotent (2nd call no-op); `atexit.register(self.close)`; `weakref.finalize`; no per-instance signal handlers; `setsid` on spawn **CONDITIONAL (C3 P1 fix — acceptance must match Step 3's feasibility check): if redislite exposes a spawn hook (preexec_fn/start_new_session), use it; if not feasible, document in CHANGELOG + Known Accepted Risks — reaper + atexit + context manager are the actual safety net.**
**Files:**
- Modify: `tortoise/projection/__init__.py` (add `__enter__`/`__exit__`, idempotent `close`, atexit/finalize, setsid)
- Test: `tests/test_projection.py` (context manager + rapid open/close no-hang)

**Step 1:** Write tests — `with FalkorProjection(...)` closes on exit; double-close no-op; 100 rapid open/close no hang; **`test_finalize_cleans_on_gc()` — drop last reference without explicit close, gc.collect(), verify server cleaned (R1#7 fix — weakref.finalize was untested)**; **`test_atexit_cleanup_fires_on_process_exit()` — subprocess opens FalkorProjection without `with`, exits normally, assert no orphaned server (R2 fix — atexit needs subprocess-level test)**; **conditional: if redislite spawn hook found in Step 3 research → add `test_setsid_child_process_group()`; else skip (documented limitation) (R1#7 fix)**; **`test_no_per_instance_signal_handlers` (R4 fix — Acceptance criterion 'no per-instance signal handlers' had zero coverage: create 100 instances in a loop, assert signal-handler count does not grow).**
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

**Step 1:** Remove the monkeypatch, run tests → PASS (or FAIL if hang persists → keep patch + add CHANGELOG known-limitation entry — R1#5 fix: destination is CHANGELOG, matching Task 4 Step 4, NOT 'note in plan').
**Step 2:** Commit

---

## Child 2 — The Fix: Stable Path + Migration + Guards

### Task 6: TORTOISE_DB_PATH unification + config resolution

**Intent:** One env var for the canonical embedded DB path; resolve defaults with correct precedence. Must ALSO reconcile mcp_server.py's URI-vs-path split (P0 fix — mcp_server reads TORTOISE_DB_URI for file paths while hosted_api reads TORTOISE_DB_PATH; two env vars for the same purpose).

**Acceptance:** New `tortoise/config.py`: `resolve_db_path()` returns `TORTOISE_DB_PATH` env → `~/.tortoise/tortoise.db` default; `TORTOISE_DB_PATH` (not EMBEDDED) is the single source; hosted_api.py's existing usage preserved (default `/data/tortoise.db` on Fly). **mcp_server.py:22-46 updated** to use `resolve_db_path()` for the non-docker fallback — no more split personality. **Explicit env precedence rules (C2 P0 fix — three consumers read different env vars):** (1) `TORTOISE_DB_URI` with `docker://` prefix → URI mode (resolve_db_path() NEVER called — add test that docker:// is not resolved to a file path); (2) `TORTOISE_DB_PATH` env → file path; (3) `TORTOISE_DB_URI` without `docker://` → treated as file path (backward compat — mcp_server.py:44 existing behavior); (4) default `~/.tortoise/tortoise.db`. **When both non-docker URI and PATH are set: PATH wins with a logged warning.** Call order in mcp_server: check `docker://` FIRST, then `resolve_db_path()` — never resolve a URI to a file path. **C3 P0 wiring fixes — the SDK is the choke-point and both mcp_server and sdk currently read ONLY TORTOISE_DB_URI, blind to TORTOISE_DB_PATH:** (a) `TortoiseSDK.__init__` (sdk.py:78-86): when `db_path is None` AND `TORTOISE_DB_URI` unset → default `db_path = resolve_db_path()` (test: set only TORTOISE_DB_PATH → `TortoiseSDK()` uses it); (b) `mcp_server.py:22-46`: wire `resolve_db_path()` into the non-docker fallback (currently only checks TORTOISE_DB_URI; with only TORTOISE_DB_PATH set it falls through to no-DB mode — test: set only TORTOISE_DB_PATH → mcp_server uses that path).
**Files:**
- Create: `tortoise/config.py`
- Modify: `tortoise/projection/__init__.py`, `tortoise/sdk.py`, `tortoise/hosted_api.py`, `tortoise/mcp_server.py`
- Test: `tests/test_config.py`

**Step 1:** Write unit tests — env precedence, default, Fly override; **`test_sdk_defaults_to_db_path_env` (R3 P1 fix — set only TORTOISE_DB_PATH → `TortoiseSDK()` uses it) + `test_mcp_server_uses_db_path_env` (R3 P1 fix — set only TORTOISE_DB_PATH → mcp_server connects to that path; these are the P0-specified wiring tests from Acceptance, previously missing from Step 1) + `test_docker_uri_never_resolved_to_file` (R4 fix — Acceptance says resolve_db_path() NEVER called for docker://; asserts it) + `test_path_wins_over_non_docker_uri` (R4 fix — both non-docker URI + PATH set → PATH wins, warning logged) + `test_non_docker_uri_treated_as_file` (R4 fix — backward-compat: bare non-docker URI → file path)**; **`test_invalid_db_path_env_falls_through_to_default[empty|whitespace]` (R7 fix — named; C3 P2 fix: `os.environ.get` can't distinguish unset from `""`; resolution strips and rejects empty/whitespace → falls through to default with logged warning, never passes `""` to FalkorProjection)**; **`test_hosted_api_fly_default` (R2 P2 fix — hosted_api's `/data/tortoise.db` Fly default untested): assert hosted_api resolves to /data/tortoise.db when TORTOISE_DB_PATH unset**; **env isolation (R2 P2 fix — TORTOISE_* env vars leak between tests): all env-touching tests use pytest monkeypatch.setenv, or an autouse fixture saves/restores the environment.**
**Step 2:** Run → FAIL
**Step 3:** Implement.
**Step 4:** Run → PASS
**Step 5:** Commit

### Task 7: Hard-reject relative paths + allow_nonstandard_path escape hatch

**Intent:** Prevent Category-3 (per-CWD) leaks at the choke-point; fail loudly with actionable error.

**Acceptance:** `FalkorProjection('tortoise.db')` raises ValueError with 3 remedies listed; `allow_nonstandard_path=True` (public kwarg) or env `TORTOISE_ALLOW_NONSTANDARD_PATH=1` bypasses canonical-path enforcement for **ABSOLUTE non-standard paths only** — relative paths are NEVER permitted regardless of escape hatch (C6 P2 fix — acceptance now matches Step 1's constraint); absolute paths (incl. tempdirs) unaffected; error message enumerates remedies.
**Files:**
- Modify: `tortoise/projection/__init__.py`, `tortoise/config.py` (R6 fix — add RELATIVE_PATH_ERROR constant; cross-task dependency: Task 6 creates config.py first)
- Test: `tests/test_projection.py` (hard-reject + escape hatch cases)

**Step 1:** Write tests — relative path raises; absolute passes; escape hatch bypasses for absolute non-canonical paths ONLY; **relative path raises EVEN WITH escape hatch (P1 fix — `FalkorProjection('tortoise.db', allow_nonstandard_path=True)` MUST still raise ValueError; the escape hatch never permits relative paths — that's the whole point of the fix);** empty-string path raises clear error; `~/path` (tilde, not expanded) treated as relative → raises with tilde-expansion hint (P2 fix); message lists 3 remedies; **`test_mcp_server_error_surface` (R3 fix — relocated from Step 4 into Step 1 TDD cycle: subprocess.run mcp_server with `TORTOISE_DB_PATH=./relative.db` → non-zero exit, stderr contains the 3-remedies message)**. **EXACT 3-remedies message text (R2 P2 fix — was referenced in Task 7, Task 10 ingest, and mcp_server error-surface test but never specified; now defined once, shared as a constant `RELATIVE_PATH_ERROR` in tortoise/config.py):** `"Relative DB path {path!r} rejected. Use (1) the canonical path (no-arg FalkorProjection() or TORTOISE_DB_PATH), (2) an absolute path, or (3) allow_nonstandard_path=True (env TORTOISE_ALLOW_NONSTANDARD_PATH=1) for absolute non-canonical paths. Relative paths are never permitted."` — all call sites (FalkorProjection hard-reject, ingest.py pre-check, mcp_server error surface) reference this constant so they cannot drift.**
```python
# MUST raise DESPITE escape hatch (relative is always a hard block)
with pytest.raises(ValueError):
    FalkorProjection('tortoise.db', allow_nonstandard_path=True)
# Escape hatch allows non-standard ABSOLUTE paths only
proj = FalkorProjection('/tmp/custom.db', allow_nonstandard_path=True)
```
**Step 2:** Run → FAIL
**Step 3:** Implement.
**Step 4:** Run → PASS. Run full `pytest tests/test_projection.py` — ensure temp-path tests (absolute) unaffected. (`test_mcp_server_error_surface` lives in Step 1 — R4 fix, residual Step 4 copy removed.)
**Step 5:** Commit

### Task 8: Import-time loud-fail guard for direct redislite imports

**Intent:** Catch future direct `redislite.falkordb_client.FalkorDB` imports that bypass FalkorProjection (the 4 known bypasses + any future code).

**Acceptance:** `tortoise/__init__.py` wraps `redislite.falkordb_client.FalkorDB` → RuntimeError for non-canonical paths; standalone scripts importing tortoise first are guarded. **Mechanism SPECIFIED (P0 fix):** `tortoise/__init__.py` imports `redislite.falkordb_client.FalkorDB` and **subclasses it** (R1#15 fix — acceptance now matches Step 3's 'subclass, not wrapper') with an `__init__` that raises RuntimeError when `path` is relative, and re-exports the subclass on the module as `tortoise.FalkorDB`. **CRITICAL correction (C2 P0 fix): the wrapper does NOT intercept `projection/__init__.py:131`'s `from redislite.falkordb_client import FalkorDB` — Python resolves that to the ORIGINAL class regardless of what tortoise exports. Protection for the projection path comes from Task 7's hard-reject INSIDE `FalkorProjection.__init__` (which is the real choke-point). The wrapper protects only code importing tortoise's re-export or importing redislite AFTER `import tortoise`.** **Known limitation documented (P1 fix):** if code imports `redislite.falkordb_client.FalkorDB` BEFORE `import tortoise`, its local reference is unwrapped → the guard is best-effort, not security. The pre-commit grep (Task 13) + CI are the source-level enforcement. Do NOT monkeypatch `redislite` module globally (breaks non-tortoise redislite users — P1 fix: scope the guard to tortoise's own import surface).
**Files:**
- Modify: `tortoise/__init__.py`
- Test: `tests/test_guard.py`

**Step 1:** Write tests — (a) `import tortoise` first, then `import tortoise; from tortoise import FalkorDB; FalkorDB('relative.db')` raises RuntimeError (C2 P0 fix — test tortoise's OWN re-export, NOT `redislite.falkordb_client` which is never intercepted); (b) absolute path passes through the wrapper; (c) no-arg `FalkorDB()` passes through; (d) `FalkorProjection('/tmp/test.db')` still works — validates the ARCHITECTURE: Task 7's own hard-reject accepts absolute paths, wrapper doesn't break the projection module (C2 P0 fix — previous test claimed wrapper intercepts projection's import, which is wrong); (e) import `redislite.falkordb_client.FalkorDB` FIRST then tortoise → unwrapped reference = documented bypass, test asserts it does NOT raise (locks in known limitation, pre-commit covers it); (f) `redislite.Redis()` direct import also bypasses wrapper — **explicit test (R9 fix — was ambiguously phrased as a doc note): `import tortoise` then `from redislite import Redis; Redis()` → does NOT raise, locking in the documented bypass (pre-commit grep covers it at source level); if it DID raise, the assertion fails, surfacing an unexpected wrapper expansion**.
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
**Step 3:** Run grep → 0 matches; run **two representative scripts (R2 P3 fix — now specified): `python graph-scripts/bp_approach_cycle1.py` (was relative-path fix, category a) + `python graph-scripts/fix_6704_operational_grounding.py` (was absolute-path, category b)** → both work against canonical path.
**Step 4:** Commit

### Task 10: Migrate library code (pipeline_cli, backup, __main__, mcp_server, sdk, ingest, hosted_api, validation/, test_cross_ontology, smoke_test)

**Intent:** Close all remaining embedded connection points (~15 sites across library + validation + cross-repo).

**Acceptance:** Grep for `FalkorProjection('` (relative) and `FalkorProjection(DB_PATH)` with non-canonical paths → 0 in tortoise/ and graph-scripts/; all use `resolve_db_path()` or `allow_nonstandard_path=True` for legit multi-DB cases (restore).
**Files:**
- Modify: `tortoise/pipeline_cli.py:131`, `tortoise/backup.py:75/96/106`, `tortoise/__main__.py:11/788/1164/281/283/304`, `tortoise/mcp_server.py:40-46`, `tortoise/sdk.py`, `tortoise/ingest.py:121`, `tortoise/hosted_api.py`, `tortoise/session_continuity.py:72`, `tortoise/migrate_kinds.py:118`, `tortoise/connectors/github_docs.py:36`, `tortoise/test_cross_ontology.py`, `tortoise/tortoise_client.py:157` (C6 P2 — was prose-only, now catalogued), `graph-scripts/setup.py:962`, `graph-scripts/smoke_test.py:98` (intentional bypass + noqa), `validation/*` (tempdir files NO change)
- Cross-repo: `scripts/restore_graph_issue100.py` — **full agent-infra path `/Users/home/agent-infra/scripts/restore_graph_issue100.py`; raw redislite clients (no FalkorProjection); one-off migration script (R1#11 fix — path + edit policy now explicit): file a follow-up issue for the agent-infra copy; tortoise-local copy routes through `FalkorProjection(path=..., allow_nonstandard_path=True)`; cross-repo migration tracked separately — do NOT block Task 10 on it.**
- Test: grep verification + `pytest tests/ -x -q` (existing suite green)
- Modify (R3 fix — stale-env sweep target, per Step 1 prose): any file containing `TORTOISE_EMBEDDED_PATH` (discovered via `rg`; replace with `TORTOISE_DB_PATH`)

**Step 1:** Grep inventory → confirm 0 remaining relative calls after edits. **(R4 fix + R10 fix — Task 10 is grep-inventory-first, NOT a TDD task; the named tests below (docker-mode env, canonical-db guard, diagnostic payload, consumer smoke, init create-if-absent) are written in a separate `tests/test_migration_consumers.py` as part of Step 3's implementation, then run to green — explicit deviation from the 5-step TDD pattern because Step 1 is a verification sweep, not a test-authoring step. Step 1 INVENTORY (R10 fix — all named here so Step 1 readers see them): `test_init_docker_mode_does_not_set_db_path_env`, `test_cross_ontology_rejects_canonical_db_path`, `test_tortoise_client_diagnostic_reports_db_path`, `test_session_continuity_resolves_db_path`, `test_migrate_kinds_resolves_db_path`, `test_github_docs_connector_resolves_db_path`, `test_cmd_init_create_if_absent_after_falkorprojection_routing`.)** **Split inventory into two categories (C2 P1 fix): (a) relative-path fixes (hard-reject blockers — must change to canonical); (b) already-absolute paths routed through `resolve_db_path()` for consistency (e.g., pipeline_cli.py:131 `str(_PROJECT_ROOT / 'tortoise.db')` is absolute → category (b), not a hard-reject fix).** **Verify graph-scripts/setup.py in the grep inventory (C6 P2 fix — only graph-scripts/setup.py exists, no root setup.py; its :962 uses project-relative `str(PROJECT / "tortoise.db")` and must be edited).** **Stale env-name sweep (R2 P2 fix — Task 6 says TORTOISE_DB_PATH 'not EMBEDDED' is the single source, but no task replaces old references): add to Task 10 grep inventory — `rg TORTOISE_EMBEDDED_PATH` across repo, replace all with TORTOISE_DB_PATH, verify 0 remaining matches.**
**Step 2:** Edit each site: category (a) → canonical path; category (b) → `resolve_db_path()`; restore script → `allow_nonstandard_path=True` for /tmp source. **validation/ split (C4 P2 fix — blanket migration would break tempdir test isolation): `validate_tortoise_ep.py` + `svbp_gate4.py` use intentional tempfile.mkdtemp() isolation — NO changes (absolute temp paths already pass hard-reject); only validation files using relative/non-canonical paths get edited. test_cross_ontology.py: (C5 P1 fix — its module-level `DB_PATH = "tortoise-test-s6.db"` at :25 is RELATIVE and would be hard-rejected): change DB_PATH to an absolute temp path or `resolve_db_path()`; ALSO add guard against `--db` pointing at canonical path (line 176 `unlink` is destructive — C4 P2 fix); AND add `tortoise/test_cross_ontology.py` to Task 13 pre-commit exclusions (it lives under tortoise/, not tests/ — C5 P2 fix).** **ingest.py explicit pre-check (C2 P0 fix — the `Path(args.db).exists()` chain means a relative non-existent path falls through to 'Docker unreachable' instead of the clean hard-reject error): add before the exists() check — `if args.db and not os.path.isabs(args.db) and not args.db.startswith('docker://'): raise ValueError(relative-path message with 3 remedies)` + CLI error-surface test.** **__main__.py:281 explicit rename (C2 P1 fix): `~/.tortoise/embedded.db` default → `resolve_db_path()` default (`tortoise.db`) — this is a name change, not just routing.** **__main__.py:11/788/1164 (R3 fix — these had no prose description): all are `FalkorProjection(args.db)` / `FalkorProjection(path=args.db)` call sites receiving user-supplied `--db`; route each through `resolve_db_path()` when args.db is None/empty, else validate it passes the hard-reject (absolute or docker://) — category (b) consistency routing.** **__main__.py:283 CONCRETE EDIT (C4 P0 fix — the `_cmd_init` raw `from redislite.falkordb_client import FalkorDB; db = FalkorDB(db_path)` bypasses FalkorProjection entirely so Task 7's hard-reject + Task 4's lifecycle never apply): replace with `from tortoise.projection import FalkorProjection; proj = FalkorProjection(db_path)` — routes init through the choke-point (verify init's create-if-absent behavior still works — C5 P2 fix).** **smoke_test.py:98 — DECISION (C5 P1 fix + R5 P1 fix — earlier text was self-contradictory: 'does NOT import tortoise' vs 'route through resolve_db_path()' which REQUIRES importing tortoise.config): smoke_test.py uses a `_SmokeProj` duck-type + raw `select_graph()`/`delete_graph()` handles and absolute tempdir paths (safe). Document it as an INTENTIONAL bypass with the CONCRETE mechanism: keep the raw redislite import + add `# noqa: redis-guard` for Task 13's hook, AND `import tortoise.config` (a lightweight module import, acceptable even for the independence-oriented smoke test) to route its embedded path through `resolve_db_path()`. Do NOT rewrite the duck-type.** **Unlisted consumers CONCRETE EDITS (C4/C5 P0 fix — `tortoise/session_continuity.py:72`, `tortoise/migrate_kinds.py:118`, `tortoise/connectors/github_docs.py:36` all read TORTOISE_DB_URI directly and would ignore a valid TORTOISE_DB_PATH): replace their `os.environ.get("TORTOISE_DB_URI")` reads with `resolve_db_path()` fallback (import from tortoise.config) + update error messages to mention TORTOISE_DB_PATH.** **graph-scripts/setup.py:962 CONCRETE EDIT (C5 P1 fix — uses `str(PROJECT / "tortoise.db")` which is project-relative and would be hard-rejected): change to `resolve_db_path()` or `str(PROJECT.resolve() / "tortoise.db")` (absolute).** **tortoise_client.py:157 (C5 P2 fix — diagnostic payload reads only TORTOISE_DB_URI): change to `os.environ.get("TORTOISE_DB_URI") or os.environ.get("TORTOISE_DB_PATH", "not set")` + `test_tortoise_client_diagnostic_reports_db_path` (R2 P3 fix — payload includes resolved path when only TORTOISE_DB_PATH set).** **__main__.py:304 (C6 P2 fix — init sets `os.environ.setdefault("TORTOISE_DB_URI", docker://...)`; DO NOT setdefault TORTOISE_DB_PATH in docker mode — a local file path is semantically wrong when the DB is remote; consumers fall back to the docker URI. Only setdefault TORTOISE_DB_PATH in the embedded-init branch.)** **Consumer smoke tests NAMED (R9 fix — R2 P2 fix was unnamed): `test_session_continuity_resolves_db_path`, `test_migrate_kinds_resolves_db_path`, `test_github_docs_connector_resolves_db_path` — check existing tests exercise their DB-connection path; if not, add these smoke tests verifying each resolves TORTOISE_DB_PATH after the edit. `test_init_docker_mode_does_not_set_db_path_env` (R2 P2 fix — asserts TORTOISE_DB_PATH NOT in env after docker-mode init). `test_cross_ontology_rejects_canonical_db_path` (R2 P2 fix — subprocess with `--db=$TORTOISE_DB_PATH` → non-zero exit, stderr warns about destructive unlink).**
**Step 3:** Run `pytest tests/ -x -q` → existing suite green. **Add explicit `test_cmd_init_create_if_absent_after_falkorprojection_routing` (R7 fix — named; C6 P2 fix: existing suite may not cover init's create-if-absent behavior after FalkorProjection routing; verify a fresh init still creates the DB).**
**Step 4:** Commit. **mcp_server dependency note (R2 P3 fix): Task 10's mcp_server.py:40-46 edits run AFTER Task 6's mcp_server.py:22-45 wiring — same file, two tasks; if Task 6 changed those lines, re-grep before editing.**

### Task 11: `tortoise migrate-db` CLI — embedded.db → tortoise.db (data-safe)

**Intent:** One-time, idempotent, JSONL-based migration of legacy `~/.tortoise/embedded.db` to canonical path — NEVER binary copy (data-loss guard). P0 fixes: backup before rebuild, advisory lock against concurrent migration, marker written ONLY after verified rebuild, interrupted-rebuild recovery.

**Acceptance:** `python -m tortoise migrate-db`:
1. **Backup first (P0 fix):** `cp ~/.tortoise/embedded.db ~/.tortoise/embedded.db.bak-<timestamp>` before rebuild (restore point if rebuild fails).
2. **Advisory lock (P0 fix):** `~/.tortoise/.migrate.lock` (fcntl/OS lock) — concurrent migrate-db runs serialize; exactly one wins, others skip with message.
3. JSONL `rebuild_all()` if tortoise.db absent + embedded.db exists; **tortoise.db present (any state) + no marker = resolve via the 3-way discriminator (R8 fix — reconciles R7's 'partially-present' wording with tests (d)/(e3)/(e5)): `rebuild_all()` is documented as ALL-OR-NOTHING per event-batch (each batch applies atomically; interruption leaves either 0 applied batches or all) → 0-node/corrupt = interrupted → delete + rebuild cleanly [test (d)]; 0 < count < source = partial → delete + rebuild cleanly WITHOUT --force [test (e5)]; count >= source AND valid = genuine conflict → error/--force [test (e3)].**
4. **Marker `~/.tortoise/.migrated-v2` written AFTER successful rebuild + integrity verification** — marker-before-rebuild would block recovery (P1 fix). **Integrity = node-ID-set equality + edge (src,dst,type) tuple equality + full-dict replay through InMemoryProjection comparing ALL node properties (C6 P2 fix — supersedes the earlier 5-node/3-edge spot-check, which was dropped as redundant).**
5. Handles FileNotFoundError (no-op) + FileExistsError. **Skip-if-marker-present does a DB integrity probe first (C2 P0 fix — marker + corrupt/truncated DB must NOT silently skip; probe = open DB + GRAPH.LIST/count; on failure delete corrupt DB + marker + re-migrate from source, or fail with clear error + `--force` flag to bypass marker).** Uses logging.warning not print (stdio-safe). **Backup failure aborts migration (C2 P1 fix): wrap backup in try/except — on OSError, exit non-zero with `backup failed: <reason>. Migration aborted. Source DB intact.` — never proceed without the restore point.**
**Files:**
- Modify: `tortoise/__main__.py` (add migrate-db command), `tortoise/backup.py` (restore reads DB filename from manifest, not hardcoded `tortoise.db`)
- Test: `tests/test_migrate_db.py` + `tests/test_backup.py` (R5 fix — cross-suite manifest test (l) lives in test_backup.py)

**Step 1:** Write tests — (a) happy path: create legacy embedded.db with known events (50 PointAdded, 10 OperatorAdded+edges, 5 PointRevised, 3 PointRetracted, 2 PointsMerged), run migrate-db, **assert node-ID-set + edge (src,dst,type) tuples identical source vs target + replay source events through InMemoryProjection and compare full dict vs target for ALL node properties (R1#2 fix — removed the contradictory 'content spot-check on 5 nodes/3 edges' clause; full-dict replay is the sole deep-integrity check, superseding the earlier spot-check and property-hash variants)**; (b) idempotency: second run skips via marker; (c) **concurrent: two subprocesses migrate simultaneously → exactly one wins (lock)**; (d) **interrupted: SIGKILL mid-rebuild → next run detects partial (marker absent + tortoise.db present with **0 nodes / corrupt**) → rebuilds cleanly (R8 fix — 3-WAY DISCRIMINATOR, reconciled after R7 introduced a (d)-vs-(e5) contradiction): (1) 0 nodes / corrupt → interrupted → auto-rebuild [test (d)]; (2) 0 < node count < source event count → partial migration → delete + rebuild cleanly WITHOUT --force [test (e5)]; (3) node count >= source event count AND valid → genuine conflict → error/--force [test (e3)]**; (e1) **marker-present-but-DB-missing → re-migrates or clear error, never silent exit 0** (R1#16 fix — renamed from (e) for consistent sub-lettering); (e2) **marker-present-but-DB-corrupt (truncated/zero-byte) → detects via integrity probe, re-migrates or clear error (C2 P0 fix)**; (e3) **BOTH embedded.db AND tortoise.db exist without marker — DISTINGUISHING RULE (C4 P0 fix + C5 P2 fix — tests (d) and (e3) previously specified opposite behavior for the same state, unexecutable): probe tortoise.db integrity via GRAPH.LIST/open — if corrupt/empty (0 nodes) AND embedded.db has >0 events → treat as interrupted migration → delete partial + rebuild cleanly (test d path); if VALID with node count >= source event count → genuine conflict → exit with clear error `Both embedded.db and tortoise.db exist without migration marker — resolve manually or use --force`; with --force, rename tortoise.db → tortoise.db.bak-conflict-<ts> before rebuild (R8 fix — 'VALID' now means count >= source, so it cannot collide with (d)/(e5) rebuild paths); **if BOTH are empty/0-events → NO-OP (C5 P2 fix — an intentionally-created empty canonical DB must not be deleted; a 0-event embedded.db + 0-node tortoise.db is a valid no-op, skip with message). The integrity probe + event-log length vs source count is the 3-way discriminator. (e4) **corrupt JSONL event log (C3 P1 fix — truncated final line or non-JSON mid-file must NOT silently skip): either fail with clear error naming the corrupt line, or migrate valid events AND report skipped/unparseable line count — never silent**; (e5) **partial-with-nodes (R7/R8 fix — 0 < node count < source event count + no marker → incomplete migration, NOT genuine conflict): delete partial + rebuild cleanly WITHOUT --force; named test `test_migrate_partial_with_nodes_rebuilds` — asserts a tortoise.db with SOME nodes but fewer than source events is rebuilt, not error-exited (middle branch of the 3-way discriminator — R8 fix: reconciled with (d) and (e3); requires rebuild_all batch-atomicity doc from Acceptance item 3)**; (f) FileNotFoundError no-op; (f2) **`test_file_exists_error_handled` (R10 fix — lettered; R8+R9 fix: trigger = `shutil.copy2` raises FileExistsError on the backup timestamp race; expected = retry fresh timestamp, clear `migration aborted: file exists: <path>` after N=3 retries; source + target intact)**; (g) **backup-failure test MERGED (R1#3 fix — former (g)/(h) were near-duplicates): single parametrized `test_migrate_aborts_on_backup_failure[EIO|generic-OSError]` — mock copy2 → OSError → aborts non-zero, source intact, no rebuild**; (g2) **`test_backup_created_before_rebuild` (R10 fix — lettered; R8 fix: affirmative backup-first test: happy path creates `.bak-<timestamp>` whose content matches source embedded.db BEFORE rebuild_all() runs; the failure-only path wasn't sufficient)**; (h) **`.bak-*` accumulation: `test_migrate_warns_on_bak_accumulation` — pre-create 4 .bak files → warning logged (R2 fix)**. **(i) `test_restore_reads_db_name_from_manifest` — moved into Step 1 inventory (R1#4 fix — was Step 4 prose, now part of the TDD cycle): manifest `{"db": "embedded.db"}` + no tortoise.db → restore finds embedded.db; `custom-name.db` manifest → uses it, not hardcoded default. (Manifest-based restore is a P0 within Task 11 — pre-migration backups have `embedded.db` in their manifest; restore hardcodes `tortoise.db` and would fail to find them.) (j) `test_concurrent_force_both_dbs` (R4 fix — moved from Step 3 into Step 1 TDD cycle: two subprocesses with --force, both-DBs pre-existing → exactly one rename wins, one .bak-conflict-* exists, winner rebuilds intact; R6 fix — reordered after (i) for logical sequence) + (k) `test_force_rename_failure_aborts_cleanly` (R4 fix — mock os.rename → OSError(EPERM) → exits non-zero, `rename failed: ... Migration aborted. Resolve manually.`, source + existing tortoise.db intact, no rebuild) (l) `test_backup_restore_reads_db_name_from_manifest` (R4 fix — the test_backup.py cross-suite variant with a DISTINCT name to avoid duplicate-test confusion; defined here in Step 1, cross-suite re-run referenced in Step 4).**
**Step 2:** Run → FAIL
**Step 3:** Implement (backup → **lock FIRST** → both-DBs conflict check UNDER lock → rebuild_all → integrity verify → marker). **Verify InMemoryProjection importable (R2 P2 fix — full-dict replay depends on it): confirm `from tortoise.projection import InMemoryProjection` works; if absent, add its creation to scope.** **C4 P1 fix — the both-DBs conflict check must be INSIDE the lock (two concurrent `--force` migrators would otherwise both rename tortoise.db → the second fails or renames the loser's backup): lock acquired BEFORE the conflict check. C4 P2 fix — `--force` rename failure (os.rename → OSError EPERM/ENOSPC): abort cleanly with `rename failed: <reason>. Migration aborted. Resolve manually.`, source + existing tortoise.db intact, never proceed to rebuild. (Both tests live in Step 1 as (j)/(k) — R5 fix, residual Step 3 directives removed.)**
**Step 4:** Run → PASS. **Cross-suite manifest test (R3 fix — the Step 1 (i) test lives in test_migrate_db.py; this is the test_backup.py variant with a DISTINCT name to avoid duplicate-test-name confusion): `test_backup_restore_reads_db_name_from_manifest` — manifest `{"db": "embedded.db"}` + no tortoise.db → restore finds embedded.db; `custom-name.db` manifest → uses it, not hardcoded default. Run `pytest tests/test_backup.py` → green (cross-validates Step 1 (i)).**
**Step 5:** Commit

---

## Child 3 — Hardening: Docker Routing + Regression Gates

### Task 12: Concurrency stress + chaos tests (with C1 feedback gate)

**Intent:** Prove the stable-path contract under load (1 server for N processes, no corruption) and reaper correctness under chaos (kills only idle). P0 fix: explicit feedback-loop gate when chaos tests surface C1 bugs.

**Acceptance:** New `tests/test_embedded_concurrency.py`: (a) 5 parallel subprocesses same canonical path → exactly 1 redis-server, integrity read-back (all keys present/correct), 0 orphans after; **crash-recovery variant (P1 fix): after all 5 write ≥1 key, SIGKILL one mid-write → remaining 4 still read/write, exactly 1 server, all keys from survivors + pre-crash keys from killed writer intact, 0 orphans**; (b) chaos: 20 no-path servers, 10 with live clients → reaper kills only the 10 idle, leaves 10 with clients; **explicit `test_chaos_sigkill_mid_query_reaper_cleans` (R2 P3 fix — was prose, now a delineated test with assertions: reaper finds the orphan, cleans it, no zombie socket)**; **client-retry validation (P1 fix): client in read/write loop survives reaper sweep of its server via reconnect+retry, no data loss**; **reaper-SIGKILL-mid-sweep (P1 fix): SIGKILL reaper after first SIGTERM → second reaper run cleans all remaining, no errors on zombie sockets; + SIGKILL reaper WHILE HOLDING the singleton lock → fcntl auto-releases, second reaper acquires and sweeps (C3 P2 fix — chaos-context variant of the unit-level `test_reaper_singleton_lock_released_on_sigkill` in Task 3 Step 1; R3 fix — annotation now says 'chaos variant', not 'moved', so both are intentional and non-duplicative)**.
**Files:**
- Create: `tests/test_embedded_concurrency.py`
- Test: itself (process-level)

**Step 1:** Write stress test → run; MAY fail (surfaces integration gaps between C1 and C2 — failure is a signal, not expected). **Named scenarios (R4 fix — delineated but unnamed): `test_chaos_kills_only_idle_of_20`, `test_chaos_client_retry_survives_sweep`, `test_chaos_reaper_sigkill_mid_sweep_second_run_cleans`, + `test_chaos_sigkill_mid_query_reaper_cleans` (R5 fix — the Acceptance-named test was missing from Step 1: SIGKILL a WRITER mid-query → orphan created → reaper finds + cleans, no zombie socket; distinct from the reaper-SIGKILL variant), + `test_chaos_singleton_lock_released_on_sigkill` (R6 fix — the singleton-lock chaos variant now has its OWN name: SIGKILL reaper while holding lock → fcntl auto-releases, second reaper acquires + sweeps; chaos-context complement to Task 3's unit-level test).** **Guard-bypass note (R2 P3 fix): the 5-parallel test should confirm all 5 processes use FalkorProjection (not raw redislite), validating the import guard is belt-and-suspenders, not primary enforcement. All test files use `pytest.importorskip("redislite")` (R2 P2 fix — clean skip if redislite missing, no import crash).**
**Step 2:** Run → iterate until PASS. **Feedback gate (P0 fix):** if fixes to Child 1 files (reaper/lifecycle) are needed → apply fix → re-run `pytest tests/test_reaper.py tests/test_projection.py` (C1 unit/integration) → re-run Task 12 → gate green. No unverified C1 edits. **HARD CAP (R1#12 fix): max 3 fix cycles; if not PASS after 3, escalate to plan-review for re-scoping — no unbounded loops.** **Subprocess timeouts (60s) + `@pytest.mark.slow` + module-level teardown fixture killing redis-servers from the test process group (R2 fix).**
**Step 3:** Commit

### Task 13: Pre-commit + CI regression grep (REQUIRED)

**Intent:** Prevent reintroduction of relative-path connections and direct redislite imports — the source-level enforcement that covers the import-order bypass of Task 8's guard.

**Acceptance:** `.pre-commit-config.yaml` exists with a grep hook blocking `FalkorProjection\(['"](?!/|~)` (relative), `from redislite.falkordb_client import FalkorDB`, `from redislite import.*\bRedis\b` / `redislite\.Redis\(` (C2 P2 fix — redislite.Redis parent class spawns servers identically and is a bypass), `^\s*import\s+redislite\.falkordb_client` and `from\s+redislite\.falkordb_client\s+import\s+\*` (C4 P2 fix — module-import and wildcard forms bypass the wrapper guard), and `Path("tortoise.db")` argparse defaults (P2 fix — resolve-github.py:49 uses this pattern, invisible to the first grep); **UNIFIED mechanism (R5 fix — Task 13 previously specified BOTH raw-grep-with-per-file-exclusions AND a Python-script hook with # noqa, which are incompatible: raw grep cannot process inline comments): adopt a SINGLE Python-script hook (`scripts/redis-guard.py`) that (a) understands `# noqa: redis-guard` inline annotations, (b) has built-in allowlist dirs (`tests/`, `validation/`) + allowlist files (`tortoise/test_cross_ontology.py`, `tortoise/projection/__init__.py`, `tortoise/__init__.py`), (c) catches the grep patterns (relative FalkorProjection calls, direct redislite imports incl. module/wildcard forms, `redislite.Redis(`, `Path("tortoise.db")` defaults). No separate per-file grep exclusions needed — the hook's allowlist replaces them.**; hook tested on fixture files (rejects bad, accepts good, accepts test-dir imports); CI job runs the same check and **BLOCKS merge (branch protection — P1 fix: CI must be a gate, not advisory, to cover `--no-verify`)**.
**Files:**
- Create or Modify (R1#13 fix — merge into existing config, never overwrite): `.pre-commit-config.yaml`, `.github/workflows/redis-guard.yml` (or add to existing CI) + branch protection config; **Create: `scripts/redis-guard.py` (the unified hook — R8 fix: was absent from Files despite being the core deliverable), `.github/settings.yml` (R8 fix: branch-protection config, conditional on probot-settings app; else document manual repo-settings step), hook fixture test files (R9 fix — paths + contents specified): `tests/fixtures/redis-guard/bad_relative_path.py` (contains `FalkorProjection('tortoise.db')` → hook must REJECT), `tests/fixtures/redis-guard/good_absolute_path.py` (contains `FalkorProjection('/abs/path')` → hook must ACCEPT), `tests/fixtures/redis-guard/good_test_dir_import.py` (test-dir direct redislite import → hook must ACCEPT via allowlist)**
- Test: hook fixture test (shell) — bad relative call fails, test-dir direct import passes, Path-default pattern caught

**Step 1:** Create hook + fixtures (bad file → fails, good file → passes, test-dir import → passes). **Pre-hook audit (R2 P3 fix — ensure no LEGITIMATE `redislite.Redis(` calls exist in tortoise/ that the hook would wrongly block): `rg 'redislite\.Redis\(' tortoise/` before wiring the hook; any hits → add `# noqa: redis-guard` or refactor.**
**Step 2:** Run pre-commit → correct behavior.
**Step 3:** Add CI job that runs `scripts/redis-guard.py` (R6 fix — terminology updated from 'grep' to the unified Python-script hook) and fails the PR. **Branch-protection mechanism (R3 fix — GitHub branch protection is NOT a committable file; it's configured in repo settings or via probot-settings `.github/settings.yml`): create `.github/settings.yml` with `branches: [{name: main, protection: {required_status_checks: {contexts: [redis-guard]}}}]` (if probot-settings app installed) OR document the manual repo-settings step: Settings → Branches → main → Require status checks → select the redis-guard check. Without this, the CI job is advisory and `--no-verify` + merge bypasses the gate.**
**Step 4:** Commit

### Task 14: CHANGELOG + .gitignore + entrypoint/fly verification

**Intent:** Release-note the breaking changes; prevent DB files from being committed; verify hosted wiring.

**Acceptance:** `CHANGELOG.md` (new or modify — R1#13 fix: merge into existing if present) with Unreleased section covering: embedded.db→tortoise.db rename + migrate-db, relative-path rejection + remedies, TORTOISE_DB_PATH semantics, **reaper CLI full surface (R1#10 fix): `--dry-run` default, `--batch-size`, `--json`, `--timeout`, singleton lock, `TORTOISE_REAPER_MIN_UPTIME`, `TORTOISE_REAPER_TIMEOUT` env vars**, **`allow_nonstandard_path` escape hatch (R1#10)**, **pre-commit/CI grep hook (R1#10)**, lifecycle changes, close-hang known limitation (if Task 5 keeps patch), **migrate-db `--force` flag (R4 fix — user-facing bypass for corrupt-marker scenarios, documented nowhere else)**, **import guard: `tortoise.FalkorDB` raises RuntimeError for relative paths (R5 fix — user-visible breaking change; best-effort, pre-commit is enforcement)**; `.gitignore` has `*.db`; `fly.toml` `[[mounts]]` destination=/data confirmed; entrypoint.sh priority documented (already correct). **Test (R2 + R3 + R7 fix — Task 14 had no concrete verification): `git check-ignore tortoise.db` → exits 0; `grep 'destination.*"/data"' fly.toml` → found; `grep -E 'TORTOISE_DB_URI|FALKORDB_CLOUD_URI' entrypoint.sh` → found (R3 fix — entrypoint.sh had an acceptance claim but no verification command); `grep -cE 'migrate-db|TORTOISE_DB_PATH|allow_nonstandard_path|embedded_reaper|redis-guard|RuntimeError|lifecycle|close-hang|\.bak-|--force' CHANGELOG.md` → ≥ 10 (R10 fix — re-added `--force` per Acceptance; `--dry-run|--batch-size|--json|--timeout|TORTOISE_REAPER_MIN_UPTIME|TORTOISE_REAPER_TIMEOUT` verified by the implementing agent manually against the Acceptance list, since the `embedded_reaper` pattern only provides coarse proximity coverage — noted as a coarse floor, not exhaustive).**
**Files:**
- Create or Modify (R3 fix — matches Acceptance's merge-into-existing semantics): `CHANGELOG.md`
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
- Create: `docs/infra/cwd-leak-cleanup.md` (R1#8 fix — repeatable cleanup procedure must not live only in this plan doc)
- Test: `ps aux | grep redislite/bin/redis-server | wc -l` → trending to 0; CWD-leak count ≤ 5

**Step 1:** `python -m tortoise.embedded_reaper --dry-run` → review output.
**Step 2:** `--no-dry-run --batch-size 10` → verify.
**Step 3:** `--no-dry-run --batch-size 100` → verify.
**Step 4:** Post-migration: enumerate path-based servers via `lsof -i -U | grep redis-server` → for each socket in output run `redis-cli -s <socket_path> CLIENT LIST` (R2 P2 fix — exact invocation now specified; expect 0 rows = 0 active clients) → SIGTERM → **if not dead within 10s, follow up with SIGKILL (R2 P2 fix — manual cleanup must match reaper escalation; log any SIGKILL-ed CWD orphans for post-mortem)** → verify ≤ 5 remain.
**Step 4.5:** **Gate (R3 fix — operationalized, was Acceptance-only): if > 5 path-based orphans remain → STOP, escalate to manual cleanup, do NOT proceed to Step 5. The gate FAILS the task, not just notes the count.**
**Step 5:** Confirm final count. Document results in issue #176.

---

## Post-Plan Notes

- **Execution mode:** >8 tasks → Parallel Session (separate session via executing-plans).
- **Task parallelization (P2 fix from review):** linear numbering is for execution clarity; independent batches are — Batch A {Task 1, Task 6} (foundations), Batch B {Task 2, Task 7, Task 8} (depends on A), Batch C {Task 3, Task 4, Task 9, Task 10} (depends on B), Batch D {Task 5, Task 11, Task 12, Task 13, Task 14} (depends on C), Batch E {Task 15} (operational, last).
- **Rollback (P2 fix from review + R1#9/R2 expansion):** (1) delete `~/.tortoise/.migrated-v2` + restore `embedded.db.bak-*` if migrate-db data lost; (2) set `TORTOISE_DB_PATH=~/.tortoise/embedded.db` to revert path; (3) revert CHANGELOG entry; (4) **disable reaper cron/launchd: `crontab -e` remove line / `launchctl unload` the plist (R1#9)**; (5) **delete `~/.tortoise/.reaper.lock` (R1#9)**; (6) **revert `.pre-commit-config.yaml` additions, or `git commit --no-verify` as emergency bypass (R1#9 + R2)**; (7) **revert `.gitignore` `*.db` if it causes unexpected exclusions (R1#9)**; (8) **remove tortoise/__init__.py FalkorDB wrapper if guard causes false positives (R2)**; (9) **delete `~/.tortoise/.migrate.lock` if present (R4 fix — stale migrate-db advisory lock would block future migrations)**; (10) **revert `.github/settings.yml` branch-protection additions (or manually remove the `redis-guard` required status check from repo Settings → Branches → main) (R7 fix — removing the CI workflow without removing the branch-protection rule blocks ALL PRs waiting for a check that never runs)**.
- **Known accepted risks (from scoping + review):** TOCTOU on no-path kills (transient, client retry — validated in Task 12 chaos); 1 bounded stable-path orphan if SIGKILLed (intentional — NEVER_KILL); setsid infeasibility if redislite lacks spawn hook (downgraded to best-effort, CHANGELOG note); per-CWD old DBs orphaned on disk after migration (JSONL rebuildable — migrate-db note); Task 8 guard is best-effort against import-order bypass (pre-commit + CI are the enforcement).
- **Related issues:** #190 (dead Fly embedded fallback), #191 (hardcoded DB password — rotate credential).

<!-- plan-review: cycles=6, status=clean, version=2.2.0 (6 formal plan-review cycles + R7/R8/R9 user-requested P2/P3 clearing passes) -->
