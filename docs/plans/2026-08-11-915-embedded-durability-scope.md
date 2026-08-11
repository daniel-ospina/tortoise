<!-- issue-scoping: v5.1 double diamond + verify -->

# Scope — Issue #915: embedded-mode durability gap (kill -9 loses graph)

## Phase 1 — problem-diverge (2 sub-agents: framing explorer + devil's advocate)

### Alternative framings surfaced
1. **JSONL "source of truth" is opt-in and unreachable in the default config** — `TortoiseSDK(event_log_path=None)` by default (sdk.py:234,269); zero production callers set it (mcp_server.py:284, mcp_auth.py:69, 10+ CLI sites in __main__.py). The documented recovery path (indicator 2) verifies a path nothing runs by default. ≥6 write paths bypass it even when enabled (`_create_entity` sdk.py:4624 claims "event log + FalkorDB" but only calls `proj.apply()`; connectors linear.py:138 / slack.py:104 / github.py:238 call `proj.apply` directly; EP belief writes are raw).
2. **Silent-loss observability gap** — after kill -9, `_auto_health_recover`'s probe PASSES (fresh empty RDB boots) and with no adjacent log it silently continues with an empty graph (projection/__init__.py:382-393). No hard signal.
3. **Original framing (valid, with precision fixes)** — "loses entire graph" is the fresh-DB worst case: redislite's graceful close DOES SAVE (`shutdown(save=True)` verified in redislite/client.py `_cleanup`), so warm DBs lose only writes since the last RDB save. The mechanics framing is empirically sound and testable.

### Assumption map (validated)
| Assumption | Status | Evidence |
|---|---|---|
| JSONL log is the embedded safety net | FALSIFIED as stated | Opt-in only; default config logs nothing |
| Direct graph writes permanently lost on kill -9 | VALIDATED | probe: 0/5 keys (fresh DB, no SAVE); AOF probe: 5/5 |
| RDB snapshots never fire for small graphs | VALIDATED (short-lived) | save 900 1 / 300 100 / 60 200 / 15 1000; holds for sessions <15min |
| Graceful close persists the graph | VALIDATED | redislite `_cleanup` → `shutdown(save=True)` |
| Current crash test proves durability | FALSIFIED | SAVE-per-write + daemon survives killpg → live-memory reuse (#879 premise) |
| Hosted/prod unaffected | VALIDATED | FLY_APP_NAME guard; embedded-only scope correct |

### Devil's advocate — strongest challenges (all verified by controller)
1. **Daemon-reuse trap**: redislite `Redis.__init__` line 69-70 — when a daemon already runs for a path, `_load_setting_registry()` SKIPS `_start_redis()` → serverconfig applies only at COLD start. **Verified real. Benign for the loss scenario**: the reopen after kill -9 is always cold — AOF applies exactly when needed. Warm-daemon reuse holds live memory (no data at risk).
2. **Loss shape is partial, not "entire graph"** — verified: fresh-DB = 0/5; warm DB (any prior graceful close) loses only post-save writes. The honest test must cover both shapes.
3. **AOF is the least architecture-aligned option** — counterpoint: it is the only option that closes the gap for ALL write paths at the storage layer with 1 line; JSONL-contract (c) done properly is a multi-file change with a destructive failure mode (divergence rebuild) and weaker outcome (write-then-append crash window).

## Phase 2 — problem-converge

**Confirmed problem:** The embedded-mode durability contract is undefined and unverified. A kill -9 of the embedded redis-server daemon loses the graph (fresh DB: ALL of it; warm DB: writes since last RDB save) because there is no AOF, RDB snapshots don't fire for small graphs, and no write path forces persistence. The JSONL rebuild net is opt-in (absent in the default config) and the current crash test masks the gap via SAVE-per-write + daemon live-memory reuse. O/I/T requires: (1) an honest kill -9 test, (2) verified JSONL recovery for log-backed writes, (3) direct writes durable OR documented, decision documented in code.

**Falsification check:** If kill -9 of the server PID (no SAVE) with a fresh DB followed by reopen showed >0 keys under the CURRENT code, the gap would not exist. Probe shows 0/5 → gap confirmed.

**Disposition of Framing 2 (silent-loss observability):** subsumed — AOF removes the silent-loss window for all write paths in scope (post-kill -9 reopen shows the pre-kill graph, not an empty one). The remaining "no adjacent log → silent continue" path (probe passes, no recovery possible) is unchanged by design and out of scope.

**Confidence: 90** (empirically verified in this environment, two independent diverge analyses agree on mechanics).

## Phase 4 — solution-diverge (1 sub-agent; 3 distinct approaches)

- **(a) AOF via `serverconfig={'appendonly':'yes'}`** — engine-level journaling; verified 5/5 keys survive kill -9. All write paths covered (raw _upsert, SDK, entities, connectors). 1-line product change + comment. AOF everysec fsync = ≤1s residual window. appendonlydir/ is a new artifact; backup.py restore is RDB-snapshot-based (contract unchanged, no regression). Daemon-reuse trap benign (cold start = crash recovery).
- **(b) Coalesced/sync SAVE at the `_GuardedGraph` chokepoint** — RDB stays the contract, kept fresh. Sync SAVE serializes the server per write (O(n) per write, scales badly); coalesced reintroduces an app-level window that must be documented+tested at its boundary; re-creates the #879 masking anti-pattern unless carefully instrumented. Backup/restore untouched (RDB fresher — a minor win).
- **(c) JSONL as the durability contract (default-on, fsynced, full write-path coverage + divergence-triggered rebuild)** — largest surface: default log path wiring (config.py), coverage audit across sdk/connectors/EP, fsync policy (log.py), warm-DB divergence trigger in recover_from_log (consistency.py) — and the divergence rebuild is DESTRUCTIVE (must follow complete coverage or it deletes SDK-only nodes; consistency.py:57-58's own safety comment refuses db>0 rebuilds today). O(graph) rebuild-on-open. Best "when you can complete the write-path audit in-scope" — it is NOT a bounded task-level change.

## Phase 5 — solution-converge

**Chosen: (a) AOF via serverconfig — `quality over convenience`.**

Rationale (outcome quality, not diff size):
1. **Closes the gap completely for ALL write paths** — storage-layer durability covers raw `_upsert`, SDK, entities, connectors, graph-scripts. (b) only covers wrapped paths; (c) needs an unbounded write-path audit.
2. **Empirically verified in this environment** — 5/5 keys survive kill -9 with AOF; 0/5 without. No speculation.
3. **The honest test is deterministic** — everysec fsync settles in ≤1.5s; cold-start reopen after kill -9 is guaranteed (old PID dead + socket teardown polled).
4. **(c) is NOT cheaper when done properly** — the investigation leaned (c) on cost, but code validation shows (c) requires default-on logging + entity/connector/EP coverage + destructive divergence rebuild: more surface, more risk, weaker outcome (log-after-write crash window remains). Quality-over-convenience rejects the easy-sounding option.
5. **(b) is dominated** — synchronous SAVE per write is O(graph) per write and re-imports the masking anti-pattern #879 removed.
6. Residual risk (AOF everysec ≤1s window; appendonlydir/ artifact; daemon-reuse applies only at cold start) is documented in code + tests — satisfying indicator 3's documentation branch and target 2's "decision documented in code". NOTE: the appendonlydir/ artifact introduces ONE real integration regression (restore/migrate stale-AOF shadowing) — fixed in plan v2 via remove_stale_aof (see Plan item 2).

**Rejected alternatives (with when they'd be better):**
- (b) would be better if redislite lacked serverconfig/AOF support or graphs were tiny AND write rates trivial — it keeps RDB as the single artifact.
- (c) would be better if the goal were zero new persistence artifacts AND a full write-path audit were in scope — the log is the architecture's stated source of truth (consistency.py:1-4, backup.py, #548).

## Plan (draft) — v2 (scope-verify cycle 1: P1 restore-shadowing fixed + P2/P3 incorporated)

1. **Code** — `tortoise/projection/__init__.py:269`: `FalkorDB(path, serverconfig={"appendonly": "yes"} if path != ":memory:" else None)` + module-level `remove_stale_aof(db_path)` helper (rmtree of adjacent `appendonlydir/`) + durability-contract comment covering: AOF binds at daemon COLD start (restart long-running embedded daemons after deploy); incremental AOF grows unboundedly below the 64mb auto-rewrite threshold (acceptable for embedded); `appendonlydir/` is a live-durability artifact, NOT a backup artifact; class docstring note for embedded mode.
2. **Restore/migrate contract fix (P1 from verify cycle 1 — empirically confirmed)** — with AOF on, Redis loads the AOF in preference to the RDB: a stale `appendonlydir/` at the target path makes `backup.restore()` silently serve pre-restore data (`restored_via: "rdb"` falsely reported), and `migrate_db`'s `_count_nodes` 3-way discriminator + delete-partial + RDB-copy fallback all misbehave. Fix: `backup.restore()` and `migrate_db.migrate()` call `remove_stale_aof()` on the target path before any open/copy (restore semantics = "the restored snapshot wins").
3. **Tests** — `tests/test_embedded_concurrency.py`:
   - `test_kill9_server_durability_fresh_db`: raw `_upsert` writes (NO SAVE) + one operator edge + one `CREATE INDEX` statement (locks module-command AOF replay breadth), WROTE handshake, settle via **INFO persistence poll** (aof_rewrite_in_progress==0, aof_pending_bio_fsync==0, aof_last_write_status==ok; 10s cap — NOT a blind sleep; repo precedent #819/#880), server PID via `INFO server` process_id, kill -9, poll daemon exit + socket teardown, reopen, **assert new server PID != killed PID** (locks cold-start property, kills #879 live-memory masking), assert 5/5 points + edge + index survive. Docstring documents pre-fix 0/5 gap + AOF ≤1s residual.
   - `test_kill9_warm_db_aof_carries_post_save_writes`: graceful close (RDB saved) → reopen → write 3 → same settle poll → kill -9 → reopen → assert 8/8 (proves AOF carries post-RDB-save writes — the warm-DB loss shape).
   - `test_jsonl_recovery_after_total_graph_loss`: **pinned write path** `sdk.create_point` with `event_log_path` IN the db's directory → close → **assert log line count == N before deletion** (guards adjacency + non-empty-log preconditions) → delete db file + appendonlydir → reopen → `_auto_health_recover` → `recover_from_log` rebuild → assert points present. Docstring notes: green pre-fix BY DESIGN (verification test for indicator 2; only the two kill -9 tests are red pre-fix).
   - `test_restore_removes_stale_aof` (restore-contract regression): AOF session writes nodes → backup → restore into the SAME path (which has a stale appendonlydir) → assert restored data wins (proves remove_stale_aof in restore).
   - Update `_spawn_writer` SAVE comment to reference the AOF contract (SAVE stays for deterministic crash-test semantics).
4. **Docs** — decision comment in code; README "⚠️ not durable" line **unconditionally** corrected (1 line: embedded file-backed mode is AOF-durable to ≤1s; delete appendonlydir + db to reset); `consistency.py` + `_auto_health_recover` docstrings updated (lost-graph trigger now requires db AND appendonlydir deleted, or first-ever cold start).
5. **Adjacent findings (NOT absorbed, rate-limited filing — noted in plan/report)**: `_create_entity` docstring falsely claims "event log + FalkorDB" (sdk.py:4624); entity writes absent from JSONL log; event_log_path opt-in default.

## Wiring Check
| Touch Point | Type | Covered By | Status |
|---|---|---|---|
| projection/__init__.py serverconfig | code | this issue | ✅ |
| backup.py restore (stale AOF shadowing) | integration | **change required** — remove_stale_aof at target + regression test (P1) | ✅ fixed |
| migrate_db.py _count_nodes / delete-partial / RDB-copy fallback | integration | **change required** — remove_stale_aof before opens/copies (P1) | ✅ fixed |
| tests/test_embedded_concurrency.py | test | this issue | ✅ |
| tests/test_ops_safety.py recovery semantics | integration | verified — fresh-dir rebuild tests hold under AOF (AOF preload == prior graph state; refusals unchanged) | ✅ |
| embedded_reaper discovery (redis.config) | integration | unaffected — reads dir/dbfilename lines only | ✅ |
| _find_local_jsonl_dir adjacency scan | integration | unaffected — filters *.jsonl only | ✅ |
| :memory: mode (test_open_kinds) | integration | exempt from serverconfig | ✅ |
| README durability claim | docs | unconditional 1-line correction (falsified by change) | ✅ |
| consistency.py lost-graph docstrings | docs | trigger now db AND appendonlydir deleted | ✅ |

## Complexity
| Domain | Rating |
|--------|--------|
| Architecture | standard |
