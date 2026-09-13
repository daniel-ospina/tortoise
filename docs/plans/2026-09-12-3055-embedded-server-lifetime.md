# Scope — #3055: an embedded server must not outlive its owner

**Complexity: standard** — one load-bearing module (`tortoise/embedded_lifecycle.py`), a *reconciled* ownership mechanism inside the existing reaper (`tortoise/embedded_reaper.py`), and a construction-choke-point change in `tortoise/__init__.py`. Not micro: it changes a lifetime guarantee under signals, with a co-tenant safety constraint. Not complex: the research settled the design space and the mechanism is measured.

> **Complexity rating provenance (A1-P3 / A2-P2).** The #3055 body carries no `**Complexity Rating**` block and the issue has no `complexity:*` label. `standard` is asserted here from the work shape; it must be recorded on the issue via `issue-creation` (or cited) so the proportional gates it selects are traceable. Tracked as a Phase-8 follow-up, not a blocker.

**Decision (human, this session): options 1 + 2.** Option 3 (lifeline babysitter) rejected — see alternatives. **Controller refinement (verification round 1, below):** option 2 is implemented *inside the existing reaper*, not as a parallel sweep — the fd-ownership signal replaces the reaper's untrustworthy `_orphan_confirmed` heuristic rather than duplicating discovery/escalation. This is the same A+B decision at a corrected seam; it is called out for human visibility.

## Phase 0.5 — Codebase Explorer (facts this scope rests on)

| Fact | Evidence |
|---|---|
| Single embedded construction choke point | `tortoise/__init__.py:50` guarded `FalkorDB`; path-based branch calls `register_embedded_client(self)` at `:127-128` |
| Construction sites, not entry points | `FalkorProjection(` appears **273×** in-tree; entry points are 3 signal-guard installs + others |
| Signal guard installs | `tortoise/__main__.py:6137` (**`main()`**, not `_cmd_index_github`), `tortoise/mcp_server.py:1778`, `tortoise/selfhost.py:100` (module import) |
| **Existing orphan reaper** | `tortoise/embedded_reaper.py` (2253 lines): `_registry_for:287`, `_pid_identity_matches:488`, `_active_client_count:858`, `discover:992`, `_is_path_based:1190`, `reap:1386`, `_kill:1746`, `_ReaperLock:1785`, `_run_sweep:1991` |
| Reaper safety gates | `MIN_UPTIME_DEFAULT=30` (`:67`), `ZERO_CLIENT_CONFIRM_MINUTES=10.0` (`:136`), `only_safe` guard (`:1481`, `:1505`), path-based protection (`:1190`) |
| Reaper schedule | `~/Library/LaunchAgents/com.tortoise.embedded-reaper.plist` → `--no-dry-run --only-safe`, `StartInterval 600`; `launchctl print` = `runs = 116`, `last exit code = 0` |
| Reaper is *running* (controller-verified) | `~/.tortoise/reaper-zero-client.json` mtime = **now**; `~/.tortoise/reaper.log` mtime = **2026-09-02** (logging stale, job live); log tail = `sweep complete: 0 acted`, `WARNING reaper already running (PID …)` |
| Live orphan census (controller-verified, 18:44) | **15** `redislite/bin/redis-server`, PPID=1, oldest 5h39m |
| In-repo `flock` precedents | `tortoise/shared_state/concurrency.py` ("single shared flock implementation for this repo", M7 #1527); `tortoise/index_lock.py` (#280 item 1, flock+PID hybrid) |
| 4th embedded entry point | `tortoise/hosted_api.py` `_resolve_embedded_db_path():333` + anchor `:390`; `Dockerfile.hosted:76` `uvicorn tortoise.hosted_api:app` |
| 5th embedded entry point | `tortoise-ingest` = `tortoise.ingest:main` (`pyproject.toml:42`); `--db` accepts a file path |
| Prior art in this area | #2203/#2228 (signal guard), #2947/PR #2991 (test signal window), **#2052/PR #2075 (`embedded_reaper` + suite-independent sweep)** |

## Phase 1/2 — Problem (diverged, then confirmed)

**Confirmed problem (root-cause form — A2-P2):** the embedded server's lifetime is **inferred from a client count read at an arbitrary moment** instead of being **owned by a process**. The measured in-flight-signal orphan is one *trigger* of that defect, not the defect.

**Trigger condition (scope note):** a terminating signal (`SIGTERM`/`SIGHUP`, plus `SIGINT` when inherited as `SIG_IGN`) arriving while a server command is in flight. `SIGQUIT` runs no Python handler and is covered only by the sweep (A2-P3, enumerated below).

**Mechanism (measured, #3055):** redislite's `_cleanup` shuts the server down only when `_connection_count() <= 1`. During `FalkorProjection.__init__` the process issues server commands, so at signal time the count is 2 — the pool is disconnected, the handler re-raises, the parent dies by signal, the server survives.

**Structural cause (A2-P2):** redislite writes `daemonize yes`, so `redis-server` is **re-parented to PID 1 in its own session** (documented in-repo by the #2203 block: "empirically ppid=1 in its own session, so it is never in the Python parent's process group"). No kernel-level parent linkage exists — which is *why* process-group delivery and PID identity cannot work, and why ownership must be a **held resource** (fd), the research's industry lesson.

**The persistence gap (A1-P1 / A2-P1 / B1-P0 / B2-P0 — the load-bearing round-1 finding):** `embedded_reaper` (#2052/#2075) already discovers (`discover()`), classifies (`_is_path_based`, `_pid_identity_matches`), escalates TERM→KILL (`_kill`), serializes (`_ReaperLock`), and runs on a 600 s schedule. It is *live* (`runs=116`, state file current) and reports **`0 acted`** against 15 live PPID=1 servers, oldest 5h39m. The reaper declines because `--only-safe` never kills a live-pid server that is not `_orphan_confirmed` (`:1481`, `:1505`) and path-based servers require confirmation in every mode. **The plan must not propose a second sweep around a broken signal; it must supply the trustworthy ownership signal the existing guard lacks.** Not supplying it is the workaround pattern AGENTS.md forbids ("Fix Broken Infrastructure — Never Silently Work Around It").

**Alternative problem framings considered:**

| framing | verdict |
|---|---|
| "shutdown is too slow / not called" | **rejected** — #2947 disproved it; the handler *does* run |
| "redislite's cleanup is buggy" | **rejected** — redislite's contract is last-client-closes; we need a stronger guarantee |
| "signal handling is racy" (symptom framing) | **rejected as the definition** — it names the trigger, not the defect |
| "lifetime is inferred, not owned" (chosen) | **chosen** — the count read is the defect; the signal is one trigger |
| original issue framing: "a terminating signal during startup orphans the server" | **retained as a valid candidate row, superseded** by the root-cause form above (it is the user-visible instance, not the class) |
| "the sweep is the whole story; creation doesn't matter" | **rejected** — a sweep cannot clean what a live process still holds; both ends needed |
| **scope framing: should an out-of-process server hold the embedded role at all?** (A2-P2) | **not decided here — escalated as an open question** (below). In-process engines (DuckDB/SQLite/LanceDB/Milvus Lite) are immune by construction; #2200 established embedded is the eval-only fallback. Changing the default is an appetite decision, not a technical one. |

**Assumptions:**

| Assumption | Status | Evidence / falsification |
|---|---|---|
| signal handlers cannot distinguish our in-flight connection from a co-tenant's | [validated] | issue body measurement; `_gc_close` shares-server branch |
| orphans materially consume resources | [validated] | 15 live PPID=1 servers; single-writer file held |
| the sweep can always find the server's directory | **[RESOLVED — was [unverified], A2-P1/B1-P1/B2-P1]** | `embedded_reaper.discover():992` + `_registry_for():287` already enumerate (pgrep + `_config_from_cmdline` + `redis.config`/`.settings` fallback, `_find_socket_dirs` with a time budget). Coverage is **bounded** (socket/pid-bearing dirs, `max_tempdir_entries`), not universal — the plan states the bound, it no longer defers |
| the 3 named call sites are the complete set of owners | **[unverified — and read as false]** | 273 construction sites; 3 is only the set that installs the *signal guard*. Ownership must move to the `tortoise/__init__.py` choke point (F003) |
| `flock` semantics hold on the target filesystem | **[unverified]** | NFS/sandbox caveat → fail-closed rule (Risks) |
| the defer window covers MCP/selfhost too | **[unverified — and read as false]** | both are threaded (`mcp_server.py` threads, `selfhost`/`hosted_api` uvicorn); `pthread_sigmask` is per-thread (F004) |
| "17 of 20 orphans ignored SIGTERM" | **[unverified — needs artifact; causally suspect]** | redis-server handles SIGTERM as graceful shutdown; a non-exit is more likely a stalled SAVE. Reworded; the escalation itself is already implemented in `_kill:1746` and does not depend on the claim |
| census "24 orphans / oldest 5h21m" | **[partially superseded]** | controller re-census 2026-09-12 18:44 = 15 redislite servers, PPID=1, oldest 5h39m; the rate observation is corroborated, the exact count is volatile because sweeps run |

**Boundary:** out of scope — modifying redislite; the hosted/URI (non-embedded) path; Windows parity (POSIX-only, degraded no-op + log); the reaper's own scheduling mechanism (kept); the embedded-vs-Docker scope decision (escalated).

**Stakeholders:** co-tenant processes sharing an embedded server; shared dev/agent boxes (many worktree `.venv`s — controller census shows orphans parented to `.worktrees/*/.venv`); SDK/library embedders (273 construction sites); CI (`tests/conftest.py` session sweeps); hosted_api deployments.

**Challenge Report (Agent B half — A1-P2 / A2-P2, previously absent):**
- **Reverse the problem:** what if the orphans are *expected* and the reaper's refusal is *correct*? Partly true — the `only_safe` guard exists because earlier sweeps (#1005/#1642) killed live servers. That is why the fix is a *trustworthy ownership signal*, not a looser guard.
- **Pre-mortem ×3:** (1) we ship A+B and orphans still appear — the lock is held by a forked uvicorn worker after the parent dies (fd inherited), so a genuine orphan looks owned; (2) the sweep kills a live co-tenant whose spawner exited first; (3) two sweeps with different predicates race and reap each other's targets.
- **Hidden dependency costs:** the reaper's discovery, gates, schedule, and the conftest session sweeps are all load-bearing; ignoring them means two writers of "reap dead-owner servers".

**Falsification check:** this definition is wrong if (a) a server orphaned with `_connection_count()==1`, or (b) the existing reaper already reaps this class and the census is a stale artifact, or (c) an in-flight signal is not the observed trigger in the 9/10 repro.

**Confidence: 88.** High on mechanism (measured twice, independently reproduced by two verifiers); the deduction is for the persistence gap, whose diagnosis is now evidence-backed but whose fix (reaper integration) has not yet been executed.

## Phase 1.5 — Axis Research

> **Trigger assessment:** Architecture = **high** (lifetime/ownership under signals, multi-process); Library-deps = **low** (chosen primitives are stdlib: `signal.pthread_sigmask`, `fcntl.flock`); UX/Ontology = low. Prior-research dedup: the issue comment on #3055 covers the external landscape; findings are restaged here with provenance, and the internal prior-art list is corrected (the reaper was missing).

**Architecture axis — canonical:** POSIX `flock` semantics are **advisory** and tied to the *open file description* (released on process exit incl. `kill -9`, and on the last close of every inherited fd) — `man 2 flock`; `pthread_sigmask` is **per-thread** while CPython delivers Python handlers on the *main* thread — docs.python.org/3/library/signal.html §"Signals and threads".

**Architecture axis — competitor-precedent:** in-process engines (DuckDB, SQLite, LanceDB, Milvus Lite) have no server process, so the class is absent by construction; embedded-postgres wrappers and systemd (`KillMode=control-group`) solve it by holding an exclusive resource (lockfile, cgroup) rather than trusting a PID. *(Rows from the issue comment; canonical tool docs are the primary source — URLs to be attached at Phase 8.)*

**Architecture axis — pitfalls (adversarial):** Casys, "Agents die, processes remain" (2026-07) documents three failed fixes before the one that holds — orphan-hunting scripts ("'this process looks orphaned' is not a reliably observable property"), `PR_SET_PDEATHSIG` (Linux-only, direct child only), and PID-watching watchdogs (**"a PID is a recyclable number, not an identity"**). The fix that holds is an inherited fd. **This is the finding that selects fd-identity over the existing reaper's `(pid, start-time)` heuristic (`_pid_identity_matches`, #1448 FIX 5)** — which is exactly the reaper's weakest link and the reason its guard refuses to act.

**Integration Docs (no new third-party dep):** chosen approach introduces **zero** new dependencies — `signal.pthread_sigmask` + `fcntl.flock` are stdlib, and in-repo `flock` precedents already exist (`tortoise/shared_state/concurrency.py` M7 #1527; `tortoise/index_lock.py` #280). Justified-skip: no external version/API verification owed.

## Phase 4/5 — Solutions (distinct approaches + rejected)

| # | approach | verdict |
|---|---|---|
| **1** | **Defer terminating signals across the construction window** — block signals so they become pending and are delivered when quiescent, where existing cleanup works. **Corrected: must be thread-global, not per-thread** (F004) | **CHOSEN** (paired with 2) |
| **2** | **Fd-based ownership, integrated into the existing reaper** — hold an `flock` on a per-server lock file at the **construction choke point**; the reap predicate becomes *lock acquirable* **AND** *zero active clients*; the signal lands **inside `embedded_reaper.reap()`/`discover()`**, replacing `_orphan_confirmed` as the confirming signal and reusing `_kill`, `_is_path_based`, `MIN_UPTIME`, `_ReaperLock`, and the 600 s schedule | **CHOSEN** — the only approach that also covers crashes and `kill -9`, *and* the only one that repairs the existing guard instead of duplicating it |
| 3 | Lifeline babysitter — helper process holds a pipe; kills on EOF (Casys design) | **REJECTED on outcome, not cost (B1-P2 / B2-P2):** the pipe is owner-death detection via EOF and needs no children — the earlier "inert, redis-server has no children" rationale was wrong. Rejected because fd-identity via `flock` gives the **same** guarantee with no extra process, pipe, or degraded mode; it *would* have been better if we needed to reap process **trees** or wanted zero reliance on a later sweep |
| 4 | Orphan-hunting by PID/heuristic | **REJECTED** — and note this is **what the repo already ships** (`embedded_reaper._pid_identity_matches`). The rejection is therefore "don't extend the PID heuristic", not "don't build one" — the chosen approach replaces that heuristic's signal |
| 5 | `PR_SET_PDEATHSIG` | **REJECTED** — Linux-only (dev machines are macOS) and redislite owns the spawn. *Would have been better if* embedded mode dropped macOS support |
| 6 | Policy only: document + manual cleanup | **REJECTED** — untenable at the measured leak rate |
| 7 *(dropped from the original doc — A1-P3 / B1-P2)* | **Drain our own connections synchronously before the shutdown attempt** (the issue body's option 2) | **REJECTED** — blocking in a signal handler risks deadlock on a wedged socket. Subsumed: with the signal deferred (approach 1), redislite's last-client `_cleanup` performs the drain at a quiescent moment without handler-context risk |
| 8 *(body's option 1)* | PID recorded in the registry + force-shutdown | **REJECTED** — identity must be a held resource, never a PID number (research); and force-shutdown cannot distinguish a co-tenant's live use |

**Why the body's prescribed fix was not adopted (re-derivation, per skill):** the body's option 1 is PID identity — rejected on the industry lesson; its option 2 is now row 7; its option 3 (policy) is row 6. The chosen approach is a *different* pair than the body proposed, selected on evidence.

## Plan

**A. Ownership at the construction choke point (`tortoise/__init__.py`)** — F003
1. `acquire_ownership(db_path)` — open + `flock(LOCK_EX|LOCK_NB)` a per-server lock file beside the server's data; hold the fd for the process lifetime. Record owner pid **for diagnostics only**. Per-server fds (a process may own several servers) — a `dict[db_path] -> fd`, not one global fd (B2-P1). Reuse `tortoise/shared_state/concurrency.py`'s `flock` helper or `_ReaperLock`'s pattern rather than a fourth implementation; if a distinct impl is required, say why (B1-P2).
2. Call it in the guarded `FalkorDB.__init__` path-based branch, **beside `register_embedded_client(self)` (`tortoise/__init__.py:127-128`)** — so all 273 construction sites are covered by construction, not by entry-point installs.
3. `release_ownership(db_path)` on clean shutdown (close the fd).

**B. Thread-global signal deferral (`tortoise/embedded_lifecycle.py`)** — F004
4. `defer_terminating_signals()` — block `SIGTERM`/`SIGHUP`/`SIGINT` **in every thread that can receive delivery**, entered on the main thread *before worker threads are created* where possible. Where a threaded server already exists (`mcp_server`, `selfhost`, `hosted_api` import-time thread pools), iterate `threading.enumerate()` and apply `pthread_sigmask` per thread, or park the signal in a handler and drain with `signal.sigwait` after construction. **The per-thread-only mask is not sufficient** — a sibling thread with an unblocked mask lets the handler run inside the window (B1 reproduced this).
5. Degrade to a **logged** no-op where `pthread_sigmask` is absent (Windows) — never a silent one (B1-P3).
6. Wrap **only the construction window** — never a long-running or blocking operation, so a stop is deferred by seconds, not indefinitely.

**C. Reaper integration (`tortoise/embedded_reaper.py`)** — F001/F002, the round-1 P0 fix
7. Extend `reap()`/`discover()` with the ownership signal: `acquirable ⇒ owner dead` becomes a **confirming** input alongside the existing zero-client confirmation. **Reap predicate = (lock acquirable) AND (`_active_client_count() == 0`, persisted per `ZERO_CLIENT_CONFIRM_MINUTES`)** — `acquirable` alone is *spawner* liveness, not *user* liveness; a co-tenant that attached to a running server never holds the lock, so owner-exit would otherwise reap a live server (B1-P0 / B2-P0).
8. **Fail-closed:** a discovered server with **no ownership record** is *unknown* — never reapable. This closes the no-lock-server kill path (a server started by an un-instrumented older build or a path not yet covered).
9. Reuse, do not reimplement: `discover():992` for enumeration, `_is_path_based():1190` for user-data protection, `_kill():1746` for TERM→wait→KILL, `MIN_UPTIME_DEFAULT:30` for boot cooldown, `_ReaperLock:1785` for sweep serialization, `_run_sweep:1991` + the 600 s schedule as the trigger.
10. **No second sweep.** The new `reap_dead_owners()` is withdrawn; its behavior is folded into the reaper. There is exactly one writer of "reap dead-owner servers".
11. Diagnose and record why the live `--only-safe` sweep reports `0 acted` (the `_orphan_confirmed` gate at `:1481`/`:1505`) — the ownership signal is the repair. Keep the `only_safe` guard; give it a trustworthy discriminator.

**Call sites (corrected — B1-P1 / B2-P3):** the signal-guard installs are `tortoise/__main__.py:6137` (**`main()`, all CLI commands — not `_cmd_index_github`**), `tortoise/mcp_server.py:1778`, `tortoise/selfhost.py:100`. Ownership does **not** live at these three — it lives at the choke point (A). Entry points to be enumerated in Wiring Check: `tortoise` CLI, `tortoise-ingest`, `tortoise-serve`, MCP, selfhost, `hosted_api` (`Dockerfile.hosted`), SDK/library use, `tests/conftest.py`.

## Wiring Check (Phase 6)

| surface | handling |
|---|---|
| POSIX-only primitives (`pthread_sigmask`, `fcntl.flock`) | logged degradation to no-op; POSIX-only stated in acceptance criteria |
| **all construction paths (273 sites)** | covered by the choke point (`tortoise/__init__.py` guarded `FalkorDB`), not by entry-point installs |
| **entry points (7)** | `tortoise` CLI `main()`, `tortoise-ingest`, `tortoise-serve`, MCP, selfhost, `hosted_api`/Dockerfile.hosted, SDK/library + `tests/conftest.py` — each enumerated with whether it constructs embedded |
| concurrent starts / concurrent sweeps | a second process finds the lock held → does not reap → correct by construction; sweep-vs-sweep serialized by `_ReaperLock` (existing) |
| **sweep safety** | never reaps without ownership-acquirable **AND** zero active clients; no ownership record ⇒ unknown ⇒ skip. This is the safety test |
| **co-tenant** | lock is spawner-scoped: owner exit releases it while co-tenants stay live → the zero-client predicate is what protects them |
| **fork/CLOEXEC boundary** (B1-P2) | `flock` lives on the open file description: a **forked** (not exec'd) child — e.g. a uvicorn worker — inherits the fd and keeps the lock after the owner dies, so a genuine orphan looks owned. Documented; test if the fork case is in scope |
| #2203 contract (server dies with parent) | preserved and strengthened; the reaper closes the `kill -9`/crash gap |
| registry/db path discovery | **resolved** — `embedded_reaper.discover():992` / `_registry_for():287`; coverage is bounded (socket/pid-bearing dirs, `max_tempdir_entries`) |
| broken-infrastructure rule | the live sweep's `0 acted` is diagnosed (step 11), not worked around |

## Verification (embedded lane: `TORTOISE_TEST_CARVE_OUT=1`)

1. **Deterministic repro (B2-P3):** block on `<db>.settings` publication, then signal `tortoise index github` mid-construction → server must die. Today 9/10 orphan; must be **0/10** *and* the deterministic single-shot must pass.
2. **Defer window (thread-global — B1-P1):** signal blocked during the window, delivered after (handler runs), mask restored on exception; **plus** a sibling thread with an unblocked mask — no handler-driven close mid-construction.
3. **Ownership:** second process cannot acquire while the owner lives (fd-held, no PID logic); acquires after the owner exits.
4. **Safety — the co-tenant test (F002):** (a) a live co-tenant's server survives the owner's exit and a subsequent sweep; (b) **a live server with *no* lock file is never reaped**; (c) a server with active clients is never reaped even when its lock is acquirable.
5. **Escalation:** a server that does not exit within the wait window is KILLed (reuse `_kill`).
6. **Reaper integration:** the sweep uses the ownership signal and still honors `_is_path_based`, `MIN_UPTIME`, and `_ReaperLock`; `--only-safe` still declines non-confirmed live-pid servers.
7. **Regression:** the quiescent `_SIGNAL_GUARD_CHILD` tests and the #2947 readiness-marker test pass; run the embedded carve-out lane explicitly.
8. **Mutation:** each new test must fail with its fix reverted.

## Acceptance criteria

- [ ] Repro: 0/10 orphans (was 9/10) **and** the deterministic single-shot passes.
- [ ] A live co-tenant's server survives owner exit + a sweep (test-proven).
- [ ] A live server with **no ownership record** is never reaped (test-proven).
- [ ] The reaper reaps a confirmed dead-owner orphan on its existing 600 s schedule, with TERM→KILL escalation, **without** a second sweep existing.
- [ ] Multi-thread deferral proven with an unblocked sibling thread.
- [ ] Existing embedded lifecycle suite passes in the carve-out lane (`TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_embedded_lifecycle.py -v`); new tests are mutation-verified.
- [ ] POSIX-only scope stated; Windows degradation logs a warning.

## Risks

- **Deferring signals widens the window in which a stop is not immediate** — bounded to construction; the stop still happens (pending delivery).
- **`flock` is advisory** — sufficient *only* among cooperating instances. Cross-owner safety rests on the zero-client predicate, **not** on the lock alone.
- **`flock` unavailable (NFS/sandbox) → ownership unknown → never reap (fail-closed).** The sweep must not infer "dead" from an error.
- **Lock file uncreatable (read-only dir)** → proceed without ownership, mark the server unsweepable, warn. Never degrade to fail-open.
- **Fork inheritance** — a forked child keeps the lock after the owner dies; documented, and the zero-client predicate still gates the kill.
- **Sweep latency** — the startup/scheduled sweep is bounded by `_ReaperLock` + discovery budgets; state the time bound.
- **Two writers no longer exist** — the withdrawn `reap_dead_owners()` means the reaper is the single reaper of record.

## Open questions for the human

1. **Reaper integration vs parallel sweep** — the controller chose integration (F001). Confirm, or state why a second sweep is required.
2. **Embedded scope (A2-P2)** — keep the out-of-process embedded engine and pay lifetime complexity, or reduce the supported surface (document Docker as required / change the default)? Appetite decision, not technical.
3. **Complexity rating** — record `standard` on #3055 via `issue-creation` so the proportional gates are traceable.

## Verification round 1 — cycle log (4 independent verifiers)

### problem-verify
- Verifier A1: P0=0, P1=2, P2=5, P3=2, P4=1
- Verifier A2: P0=0, P1=3, P2=9, P3=5, P4=0
- Convergent P1s: (a) existing reaper omitted from problem + research; (b) sweep-safety invariant unsound (co-tenant / no-lock); (c) discovery deferral has no re-check mechanism.
- Controller: **all fixed** — reaper added to mechanism, prior art, and research artifact; safety predicate corrected; discovery resolved with citations; Challenge Report + falsification check + confidence added; confirmed problem restated in root-cause form.

### solution-verify
- Verifier B1: P0=3, P1=4, P2=4, P3=2
- Verifier B2: P0=3, P1=2, P2=2, P3=4
- Convergent P0s: (a) diverge never surveyed the in-repo reaper it duplicates; (b) `acquirable ⇒ reap` kills live servers (no-lock paths; co-tenant after owner exit); (c) wiring: ownership at 3 entry points vs 273 construction sites / unlisted `hosted_api` + `ingest`.
- Controller: **all fixed** — approach "extend the reaper" added and chosen; reap predicate corrected to lock **AND** zero clients with fail-closed unknown-lock rule; ownership moved to the construction choke point; entry points enumerated; call-site map corrected (`main()`, not `_cmd_index_github`).
- **Contradiction resolved by controller:** B2 read `~/.tortoise/reaper.log` mtime (2026-09-02) as "the reaper is not running"; A1/A2 read `runs=116` + current `reaper-zero-client.json` as running. **Controller evidence: the job is running** (state file mtime = now, `runs=116`, `last exit 0`); only the *log* is stale. The plan diagnoses the real issue (`0 acted` under `only_safe`), not the mis-reading.

### Controller decisions
- Fix, not ignore, for every P0/P1 (all confirmed independently).
- Withdrew `reap_dead_owners()` as a parallel sweep (F001).
- Refinement to the human's A+B decision: same primitives, corrected seam. Flagged for visibility.

## Deviation from the skill (declared)

Both diamonds were authored in one pass before the first verifier ran, because the external research (Phase 1.5) had already been completed and published on #3055 and the two diamonds are tightly coupled here. The declared deviation covers merged **gate rounds**, **not** verification depth: each diamond gets 2 independent verifiers, and round 1 was run to convergence with a fix + re-verification cycle. Round 1 additionally surfaced that the merged authoring skipped the Agent-B (devil's-advocate) half of problem-diverge; that output has since been authored (Challenge Report) rather than retro-declared as skipped.
