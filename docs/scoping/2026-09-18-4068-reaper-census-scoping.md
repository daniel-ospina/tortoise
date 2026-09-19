# Scoping — #4068: the orphan census walks all of `$TMPDIR`

> **Issue:** daniel-ospina/tortoise#4068 — `perf(reaper): the orphan census walks all of $TMPDIR — 224k stats at ~41% CPU per call`
> **Tier:** `Level: task` / `complexity:standard` (fail-closed: the issue carried no fractal fields)
> **Worktree:** `.worktrees/4068-reaper-census` on `perf/4068-reaper-census`
> **Domain declaration:** **adversarial** (see `### Adversarial Threat Surface`) → review bounded at 2 cycles

---

## Phase 1 — problem-diverge (measured corrections)

The issue body's framing is directionally right and quantifiably off in three places. Re-measured on this box:

| Issue's claim | Measured | Verdict |
|---|---|---|
| `find … -maxdepth 2 …` cost | 16.0–24.6 s wall (`user` ~0.2 s, `sys` ~5 s) | consistent — but **metadata-I/O bound, not CPU-bound**; "41% CPU" is an Activity-Monitor occupancy snapshot |
| depth-2 traversal space | ~230k entries (38.0k top-level + 192k nested) | consistent |
| "the suite churns the tempdir, so it degrades when the suite is parallel" | **81% of the depth-2 space is one OS-owned dir**, `$TMPDIR/com.apple.MetalPerformanceShadersGraph` (156,140 entries), whose churn is independent of the suite | **contradicted for the dominant term** |
| "most top-level churn is foreign" | 58% of top-level dirs match the reaper's own `EPHEMERAL_PREFIXES` (`tortoise_validity_test_*` 5,725; `tortoise-capture-spool-*` 4,682; `tortoise_w*` 1,343; …) | **contradicted** |
| "0 top-level entries older than 3 days" | 0 (max 2.24 d) — macOS `dirhelper` deletes temp files not accessed in 3 days | validated; cause is the OS cleaner, not teardown |
| "~8 socket dirs, all `tmp`+8" | 6–14 across the session; **100% match `^tmp[a-z0-9_]{8}$`** | validated |
| "the global walk may be redundant entirely" | **half right** — redundant as a *scope*, load-bearing as a *mechanism* | see §Contract |

### Alternative problem framings

- **A1 — foreign search-space inflation.** The walk crosses a namespace it does not own; the dominant term is an OS graphics cache. *Correct as cause, not as a fixable problem* — we cannot police another component's tempdir.
- **A2 — missing write-side hygiene for non-socket temp dirs.** `tests/test_validity_windows.py:27` `mkdtemp`s and never `rmtree`s. *Real, but a separate defect*: removing volume does not remove the traversal term (the depth-2 walk is O(dirs), and the foreign dir remains). → separate issue.
- **A3 — `find` is the wrong primitive.** Full-fidelity `os.scandir` is only ~43% faster than `find` because any depth-2 walk pays ~38.5k `opendir`/`getdents`. *Insufficient alone, right inside a narrower scope.*
- **A4 — discovery-by-search where discovery-by-registration belongs.** Rejected on merit: a manifest only knows dirs created after it ships, only via instrumented spawns, and only if the append survived the kill — i.e. it is blind to exactly the SIGKILLed-suite residue the reaper exists to clean; the walk would have to survive as a fallback anyway.
- **A5 — the census is already past its own timeout (latent correctness bug).** `SOCKET_WALK_TIMEOUT = 20.0` is below the measured 16–24.6 s walk, and the walk's `TimeoutExpired` path returned `[]` with only a warning — under load **pass 2 silently discovered nothing**. Not a scope bug; the scope fix makes it moot for pass 2 but the silent-`[]` pattern must not be re-introduced by the replacement. (Line anchors in this record are deliberately SYMBOLIC — see the note in the proof below.)

### Contract answer (the issue's "Ask")

The global walk is **not redundant as a mechanism**. `discover()` pass 1 (`_pgrep_redis_servers` + `_socket_dir_from_cmdline`) finds only servers with a **live** process, because the dir is derived from the process's argv / `redis.config`. Pass 2 is the only path to a **dead** server's residue — there is no process to pgrep, hence no cmdline to parse. Only the walk's **scope** is redundant: redislite always creates the socket dir with a bare `tempfile.mkdtemp()`.

---

## Phase 2 — problem-converge (confirmed)

> **`discover()` pass 2 performs a depth-2 scan of the entire system tempdir to find socket-bearing directories; its cost is dominated by ~38.5k unrelated subdirectory `opendir`s (16–24.6 s wall, plus the foreign 156k-entry `com.apple.MetalPerformanceShadersGraph` spool) and it can silently return `[]` past its own timeout. The mechanism is required (dead servers have no cmdline, so pass 1 cannot replace it); its scope and primitive are what is wrong.**

**Falsifier:** the framing dies if any production redislite socket directory has a basename outside the ephemeral namespace. Redislite creates every socket dir via `tempfile.mkdtemp()` with no prefix argument (`redislite/client.py`), and no production caller passes `redis_dir`/`socket_file` (only `getattr(inner, "redis_dir", None)` reads exist in `tortoise/embedded_lifecycle.py`). If it ever did, the `--full-scan` hatch (below) restores the pre-#4068 enumeration.

**Confidence:** 86/100 at converge; 100% after the gate's scope-equality proof (below).

### Scope boundary — what is NOT in this issue

| Item | Disposition |
|---|---|
| `tests/test_validity_windows.py:27` never `rmtree`s its `mkdtemp` | **separate issue** (test hygiene; does not fix the traversal term) |
| `tortoise-capture-spool-*` (premise-labs) foreign spool | **separate repo / separate issue** |
| macOS `com.apple.MetalPerformanceShadersGraph` growth | not ours; motivation only |
| Per-process memoisation of the walk | cannot bound the launchd/cron lane (fresh process per run); the cross-process flock already exists |
| Repo-wide env-truthiness vocabulary unification | **separate issue** (8+ divergent sites already in-tree) |
| `redis.socket`/`redis.pid` literal unification across untouched sites | **separate issue** (advisory review finding; new code uses declared constants) |
| Pre-existing destruction-predicate / tempdir-squatting hardening | **explicitly out of scope** (see threat surface) |

---

## Phase 1.5 — Axis research (Architecture = high; UX/Ontology = low; no new deps)

- **canonical** — mature local-service systems use a **known path + registry / process enumeration**, never a shared-tempdir scan: systemd `RuntimeDirectory=` + `systemctl clean --what=runtime`; `XDG_RUNTIME_DIR` / `dbus-cleanup-sockets`; redis writes its pidfile at a known path and unlinks its socket on exit; PostgreSQL's `postmaster.pid` is a lockfile in the data dir. (`systemd.exec(5)`, `unix(7)`, linux-manpages `daemon(7)`, redis signals doc.)
- **competitor/precedent** — Testcontainers/**Ryuk** registers fixtures and prunes **by filter**, with `RYUK_RETRY_OFFSET` re-checking anything created after the pass began; **pytest** retains the last 3 `pytest-of-<user>/pytest-N` bases (`tmp_path_retention_count`); explicit opt-in sweeps exist (`pytest --testcontainers-clean`, agentplane `--deep`); **redislite itself already writes a per-instance `.settings` registry** that the reaper reads (`_registry_for`).
- **pitfalls** — **CWE-377 / SEI CERT FIO21-C**: check-then-act cleanup in a shared world-writable dir is a symlink/TOCTOU sink; the residual here is at the **discovery** step, not the `rmtree` (already realpath/marker/rename-aside guarded). `dirent.d_type == DT_UNKNOWN` silently restores a per-entry `stat`; `DirEntry.stat()` is never free on Unix. A cron/launchd sweep is a **fresh process**, so per-process memoisation cannot bound it.
- **Q1** — `tempfile.mkdtemp()`'s default **prefix** is documented by reference (`tempfile.gettempprefix()` → `"tmp"`); the 8-char `[a-z0-9_]` suffix is **private CPython internals** (`_RandomNameSequence`) — so the design must not depend on the suffix.
- **Q2** — macOS deletes temp files not accessed in **3 days** (`dirhelper`, ~03:35) — corroborates "0 entries > 3 days". The MPSGraph cache's identity/safety is **UNVERIFIED**; it is simply not ours.

**Why the canonical registry pattern is not adopted here:** redislite is a **third-party library** that owns the `mkdtemp()` dir; the reaper cannot make it register a path, and a tortoise-side registry cannot cover uninstrumented spawns.

### Integration Docs

**None — stdlib only** (`os.scandir`, `time.monotonic`, `logging`; the change **removes** the `find(1)` host prerequisite). No `pyproject.toml` / `uv.lock` change.

---

## Phase 2.5 — problem-verify (gate) — CLEAN

Three cycles, each with a fresh verifier; each cycle found a *deeper* concrete defect and the controller fixed it. Final verdict: **`NO ISSUES FOUND`**, with the scope-equality claim adjudicated **SOUND**. Cycle log:

- **Cycle 1 — P1:** "the lossless claim is asserted, not demonstrated." Verified independently that `_classify` does **not** consult the dirname when a registry carrying `dbfilename == "redis.db"` is present (it falls straight to `_cooldown_check`), so an arbitrarily-named dir **is** reap-classifiable in principle. → Fixed: claim downgraded, codebase namespace used, explicit hatch added, naming scope recorded as an intended deviation.
- **Cycle 2 — P1:** "the test-impact analysis is asserted, not demonstrated." (the `my-custom-name` fixture in `tests/test_reaper.py`.) → Fixed: full enumeration of every `discover()` call site and fabricated fixture (including `tests/test_embedded_concurrency.py`) — exactly one test changes.
- **Cycle 2 — P2 (accepted):** "the escape hatch is discovery-only; every REMOVAL path independently gates on `_is_ephemeral_dir` (the KILL verb does not), so an out-of-contract dir can never be removed." → Fixed by replacing the convergence claim with a **positive proof**, below, plus the pass-1 lemma for kills.
- **Cycle 3 — CLEAN.** P3/P4 refinements folded in (depth phrasing; the kill path's second cleaned dir; ordering-agnostic tests).
- **Code-review round (5 agents):** the surviving "detect-only / every destruction path" generalisation was traced to the KILL verb and corrected across code, CLI help, CHANGELOG and these records; completeness is now read fail-closed; `_find_socket_dirs` deleted; the ordering/equivalence/adversarial tests rewritten so they can actually fail.

### Scope-equality proof (the load-bearing invariant)

1. `_is_ephemeral_dir(dir, tmpdir)` = `any(part.startswith(EPHEMERAL_PREFIXES) for part in Path(dir).relative_to(tmpdir).parts)`.
   *(Anchors in this record are SYMBOLIC on purpose: an earlier revision cited bare line numbers, and the #4068 comment block inserted above them made every one stale — the same class of drift the OVERRIDES marker exists to avoid.)*
2. Every pass-2 candidate is a depth-1 dir (`find -maxdepth 2` matches `T/<dir>/<marker>`), so `relative_to(tmpdir).parts == (basename,)` and therefore `_is_ephemeral_dir(dir) ⟺ basename.startswith(EPHEMERAL_PREFIXES)`.
3. Every **removal** path requires exactly that predicate: the kill-path tempdir cleanup, `_remove_stale_socket_dir` Guard 1, and `_sweep_quarantine_dirs` (all in `tortoise/embedded_reaper.py`; cited by symbol, not line, so this record survives edits above them).
4. ⟹ a pass-2 candidate outside the namespace is **never removable**; narrowing discovery to the namespace loses **zero removable records** and loses only *reporting*.
5. **KILLS are covered by the pass-1 lemma, not by the rmtree predicate.** `_classify` DOES admit a live out-of-namespace dir as `candidate` (Signal 1/2 fall through to `_cooldown_check`), and `reap()`'s `_kill` has no ephemerality gate — only the *post-kill cleanup* does. What makes the scoped scan kill-lossless is that **every live server is enumerated by pass 1** (`_pgrep_redis_servers` + `_socket_dir_from_cmdline`), which is name-independent. The lemma is stated at the claim site (`_ephemeral_name`'s docstring) and pinned by `test_live_candidate_is_found_by_pass1_regardless_of_dir_name`.
6. **Symlinked depth-1 entries are skipped** by the new primitive, so its descent set is *identical* to `find` without `-L` — there is no new discovery surface to defend (a symlinked-marker **file** inside a real ephemeral dir is still found, matching `find -name`).
7. **`--full-scan` restores the pre-#4068 un-scoped enumeration** — it is NOT "detect-only" in the strict sense: a live record that only the broad scan surfaces is still subject to normal classification and the kill path, exactly as before #4068. It adds no reachability an earlier release lacked.
8. **Cadence decision:** the scheduled launchd/cron sweep stays **scoped**. Out-of-namespace dirs are never removable, so rendering `--full-scan` into the 10-minute job would buy only a log line at ~1.0–1.3 s per run on the lane this issue is lightening. `--full-scan` is operator-only (`python -m tortoise.embedded_reaper --full-scan`, `tools/embedded_orphans.py --deep`).

---

## Phase 4/5 — solution diamond

Six alternatives were generated; the converge selected on outcome quality:

| # | Approach | Verdict |
|---|---|---|
| **A** | Shared `_iter_candidate_dirs` (`os.scandir` depth 1, name test before any stat) + explicit `full_scan` threaded through the discovery frames | **CHOSEN** |
| B | `_ScanConfig` dataclass + one compiled probe shared by the iterator **and** `_is_ephemeral_dir` | rejected — couples the discovery predicate to the destruction predicate; the destruction site is the one that `rmtree`s |
| C | Inverted polarity: keep `find` as the CLI default, add `--scoped` | rejected — leaves the defect on the operator path (the silent `[]` remains) and contradicts the `--full-scan` opt-in requirement |
| D | Single unified tri-state census; quarantines pre-censused | rejected on a functional bug: `_run_sweep` calls `discover()` **before** `reap()` creates quarantines by rename-aside, so a census inside `discover()` cannot see same-sweep quarantines; also changes `discover()`'s return type |
| E | Discovery-by-registration (the issue body's proposal) | rejected — blind to the residue class it must clean; adds stateful breakage without removing the traversal |
| F | Keep both `find` walks, add an opt-in skip | rejected — does not fix the silent `[]` or the metadata cost on the operator path |

**Advisory duplication review** (verdicts folded in): `unify` for the two duplicated `find` idioms → A *is* that unification; `unify-contract-keep-drivers` for the out-of-module census truncation signal, the env-truthy vocabulary, and the budget encoding; `keep separate` (recorded) for `_AUTOGEN_DIRNAME` vs `EPHEMERAL_PREFIXES`.

---

## Adversarial Threat Surface

This changes the **discovery** step of code that SIGTERMs processes and `rmtree`s directories based on filesystem evidence, in a world-writable temp dir. Declared bound: **2 review cycles**; acceptance = every declared class covered by a test + green CI; residuals filed, not chased.

**IN SCOPE**

| # | Adversarial input | Required behavior |
|---|---|---|
| 1 | An ephemeral-named depth-1 **symlink** pointing outside the tempdir, holding a decoy `redis.socket`/`redis.pid` for a dead pid | **Eliminated by construction:** `_iter_candidate_dirs` skips symlinked entries (parity with `find` without `-L`). A crafted record is refused by CONTAINMENT on the realpath — the target is not under the tempdir — **with the fixture aged past the boot-cooldown guard and carrying a REAL dead socket**, so the refusal cannot come from the probe or age guard (`test_symlinked_decoy_dir_never_reaped`; Guard 1 removed ⇒ the test reddens) |
| 2 | A namespace-matching name whose **realpath escapes** the tempdir | Scoped discovery is necessary-not-sufficient: `_is_ephemeral_dir` all-component containment still refuses action |
| 3 | **Depth-1 flooding** to force the scan budget to expire | Never a silent `[]`: `logger.warning` + the partial set returned; truncation is reported to the census lane |
| 4 | `full_scan=True` used to attempt destruction of a non-ephemeral dir | `full_scan` restores the pre-#4068 un-scoped enumeration; a non-ephemeral dir is containment-refused for every **removal**, and any live record it surfaces is subject to the SAME classification+kill path as before #4068 (no new reachability). `test_full_scan_does_not_add_actions_beyond_scoped` + the live-candidate lemma test |
| 5 | `TORTOISE_REAPER_FULL_SCAN=0` / `=false` / `=""` to enable the broad path | Explicit truthy allowlist: none of these enable it |

**OUT OF SCOPE** (unchanged by this diff; follow-up issue): the pre-existing tempdir-squatting exposure of the destruction predicates themselves (`_is_ephemeral_dir` semantics, `REAPER_OWNED_MARKER`, the 9-guard chain in `_remove_stale_socket_dir`), TOCTOU between discovery and action (the guard chain re-verifies at action time), and command injection (no shell string is built — the change *removes* an argv-construction surface).

---

## Phase 6 — Wiring check

| Touch point | Type | Covered by | Status |
|---|---|---|---|
| `_iter_candidate_dirs` / `_scan_socket_dirs` | internal API | this change (replaces the deleted `_find_socket_dirs`, which had no production caller and dropped `.complete`) | ✅ |
| `_sweep_quarantine_dirs` | internal API | this change | ✅ |
| `_run_sweep` / `main()` CLI | internal + CLI | this change (`--full-scan`) | ✅ |
| `tools/embedded_orphans.py --deep` | operator tool | this change (`full_scan=True` + truncation reporting) | ✅ |
| `tools/install-reaper-schedule.sh` (launchd/cron) | scheduler | **unchanged** — stays scoped; `--full-scan` is operator-only (Design decision 9 / scope-equality proof) | ✅ |
| `tests/conftest.py` sweep | test harness | unchanged (stays scoped) | ✅ |
| `docs/infra/embedded-reaper-cron.md`, `CHANGELOG.md` | docs | this change | ✅ |
| CI surfaces (`config/ci-surfaces.yml`, `python-ci.yml`) | CI | no new file → no registration change; stale comments corrected | ✅ |

---

## OVERRIDES

> **OVERRIDES:** the unconditional full-tempdir socket-dir walk recorded by **#1642 FIX 2** (the pass-2 discovery comment in `tortoise/embedded_reaper.py` — "pollution cannot disable the ONLY path that cleans killed-suite residue" — with the budget intent recorded in the `SOCKET_WALK_TIMEOUT` comment) — pass-2 **discovery** is now scoped to the ephemeral namespace. Reason: for depth-1 candidates that namespace is *exactly* the set every **REMOVAL** path already requires (`_is_ephemeral_dir` — `_remove_stale_socket_dir` Guard 1, the quarantine removal, and the kill-path tempdir cleanup; the KILL verb itself is gated by classification plus the pass-1 lemma that live servers are enumerated name-independently), so scoping loses no removable or killable record while removing the 224k-entry metadata storm. The recorded intent ("never gated on the tempdir's entry count") is preserved — the new gate is a **name** scope, not an entry-count gate — and `--full-scan` restores the pre-#4068 un-scoped enumeration.
