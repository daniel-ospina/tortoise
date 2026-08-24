# Scoping — #1658: fix embedded_reaper sweep_started_at race (overlapping sweeps under concurrency)

> Double-diamond scoping, proportional to a well-understood concurrency fix
> (problem confirmed by the issue + code research; focus on solution-diverge →
> solution-converge + fix design + regression test). Complexity: **standard**.
> Research backing: `/tmp/followup-docs/1658/research.md`.

## Phase 1/2 — Problem diamond (proportional)

### Confirmed problem

Two concurrent sweep processes on a **shared tempdir** are not mutually
excluded, so they run overlapping sweeps and reap each other's live redislite
sockets. The issue attributes this to a non-atomic `sweep_started_at` marker;
the **current code** instead guards sweeps with a flock whose **scope is the
user's HOME, not the sweep target**:

- Sweep entry (both paths take the lock): conftest `_sweep()` →
  `tests/conftest.py:206-208`; CLI `main()` → `tortoise/embedded_reaper.py:2156-2157`.
- Lock file: `_LOCK_PATH = ~/.tortoise/.reaper.lock` →
  `tortoise/embedded_reaper.py:1749-1750` (**per-HOME**).
- Sweep target: `tempfile.gettempdir()` — machine-global on Linux (`/tmp`).
- Precedent for the correct scope: `ACTIVE_SUITES_DIR` is already tempdir-scoped →
  `embedded_reaper.py:109-110`.

**Race window (line-referenced):** P1 (HOME=/a) acquires `flock` on
`/a/.tortoise/.reaper.lock`; P2 (HOME=/b) acquires `flock` on `/b/.tortoise/
.reaper.lock` (different inode) — both pass `lock.acquire()` (conftest.py:207)
and both run `_run_sweep()` on the same `/tmp` → overlap. The observed
`{'skipped': 'reaper-lock-held'}` (conftest.py:208) is a same-HOME session
correctly skipping while the cross-HOME pair overlaps — consistent with the
#1349 machine-contention logs.

**Falsification check:** if `_LOCK_PATH` were tempdir-scoped, two processes
with different `$HOME` but the same `tempfile.gettempdir()` would contend on
one flock and one would skip. A test asserting exactly this fails on current
code (both acquire) and passes with the fix. Confidence: **90** (root cause is
in-code and line-referenced; residual 10% = the issue's literal marker
mechanism, which does not exist in current code — documented in research.md §0).

### Rejected problem framings

- *"Marker read+set TOCTOU within one process/session"* — rejected: the current
  lock is a single `fcntl.flock` syscall (kernel-atomic); there is no
  read-then-write gap inside `_ReaperLock.acquire()`. The issue's marker
  language describes the reverted #1349 draft, not current code.
- *"The flock is broken/ineffective for same-HOME concurrency"* — rejected:
  `test_cli_singleton_lock_prevents_concurrent` (test_reaper.py:939) proves
  same-HOME serialization works. The gap is scope, not mechanism.

## Phase 4/5 — Solution diamond

### Alternative A — flock-only, scope the lock to the tempdir (RECOMMENDED)

Change `_LOCK_PATH` (embedded_reaper.py:1749-1750) to
`os.path.realpath(tempfile.gettempdir()) + "/.tortoise/.reaper.lock"` —
mirroring `ACTIVE_SUITES_DIR` (line 109-110).

- **Files:** `tortoise/embedded_reaper.py` (1 constant), `tests/test_reaper.py` (new tests).
- **Mechanism:** all sweepers of the same tempdir contend on one flock; the
  flock IS the atomic "marker set" — `acquire()` = read+set atomically, and a
  second sweeper observes it set and skips (issue Indicators 1+2 satisfied
  without inventing a new marker file).
- **Risks/tradeoffs:**
  - OS temp-cleaner could unlink the lock file mid-sweep → new acquirer gets a
    fresh inode while the holder keeps the old one (rare; holder fd remains
    valid, and the sweep holds the lock ≤120s; same exposure already accepted
    for `ACTIVE_SUITES_DIR`). Mitigation (optional): re-stat the path under the
    lock; not required for the common case.
  - The lock file is created under the shared tempdir — must be invisible to the
    reaper's own sweeps. Verified safe: `discover()` walks only
    `redis.socket`/`redis.pid` names (embedded_reaper.py:1107-1111),
    `_sweep_quarantine_dirs` matches `*.reaper-stale-*`, and
    `sweep_stale_index_pid_files` matches `index-*.pid`.
  - macOS: tempdir is per-user, so behavior is unchanged there (safe).
- **Best fit if:** minimal diff, zero new mechanisms, reuses the proven
  SIGKILL-safe flock, and directly closes the observed cross-HOME overlap.

### Alternative B — atomic file marker (O_EXCL claim), no flock

Add a `sweep_started_at` marker file under `<tempdir>/.tortoise/` created with
`os.open(O_CREAT|O_EXCL)` (the repo's `atomic_claim` pattern,
shared_state/concurrency.py:70-103); a second sweeper's O_EXCL create fails →
skip; remove marker in a `finally`.

- **Files:** `tortoise/embedded_reaper.py` (entry wrapper + marker lifecycle),
  `tests/test_reaper.py`, possibly `tests/conftest.py`.
- **Risks/tradeoffs:**
  - Crash/SIGKILL leaves a stale marker → needs age/pid-based staleness
    handling (the repo already has `MARKER_MAX_AGE_S`, pid-identity checks —
    more surface, more failure modes).
  - Marker removal is itself a second write; a marker-cleanup race between two
    sweepers reintroduces the very TOCTOU the issue describes.
  - Matches the issue's literal `sweep_started_at` naming but is **strictly more
    code and strictly weaker** than flock (no kernel auto-release).
- **Best fit if:** the team insists on the literal marker name / wants the
  sweep's start time observable on disk.

### Alternative C — both: tempdir-scoped flock + marker written under the lock

Keep flock as the exclusion authority; write a `sweep_started_at` timestamp
into the lock file (or a sibling file) **inside** the guarded section for
observability/diagnostics.

- **Files:** `tortoise/embedded_reaper.py` (constant + marker write in
  `_ReaperLock.acquire()` or `_run_sweep` entry), tests.
- **Risks/tradeoffs:** slightly more code than A; the marker is informational
  (the flock remains the guard). Good if the cron/scheduling docs
  (`docs/infra/embedded-reaper-cron.md`) want a "last sweep started" record.
- **Best fit if:** observability of sweep start time is a real requirement
  beyond mutual exclusion.

### Convergence — why A

A is the **better outcome**: it fixes the root cause (scope mismatch) with the
fewest moving parts, no new failure modes (no stale markers, no cleanup races),
and the kernel guarantees release on death. B introduces a second write
(marker create+remove) whose cleanup race re-creates the problem class; C adds
observability A lacks but with extra code. Per "quality over convenience": A
handles the edge cases (crash, SIGKILL, temp-cleaner) with the machinery that
already exists and is already tested, rather than a new marker lifecycle that
must be made stale-proof. If a sweep-start timestamp is wanted later, C is a
3-line additive on top of A.

### Fix design (A, concrete)

1. `tortoise/embedded_reaper.py:1749-1750`:
   ```python
   _LOCK_PATH = os.path.join(
       os.path.realpath(tempfile.gettempdir()), ".tortoise", ".reaper.lock")
   ```
   (use `os.path.realpath(tempfile.gettempdir())` — identical to
   `ACTIVE_SUITES_DIR`'s base, embedded_reaper.py:110).
2. No change to `_ReaperLock`, `main()`, or conftest `_sweep()` — they inherit
   the new scope via `_ReaperLock()`'s default argument.
3. Add a docstring note on `_LOCK_PATH`/`_ReaperLock` explaining the tempdir
   scope rationale (cross-HOME shared tempdir) and the self-reap safety.

## Regression-test plan

**Test 1 — cross-HOME race reproduction (the core test; fails on current code):**

```python
def test_reaper_lock_serializes_across_home_on_shared_tempdir(tmp_path, monkeypatch):
    """Two sweepers with DIFFERENT $HOME on the SAME tempdir must contend on
    one flock (issue #1658): the second must fail to acquire."""
    from tortoise.embedded_reaper import _ReaperLock
    # Same tempdir for both "users":
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    # Simulate user A holding the sweep lock:
    monkeypatch.setenv("HOME", str(tmp_path / "homeA"))
    lock_a = _ReaperLock()
    assert lock_a.acquire()
    try:
        # Simulate user B starting a parallel pytest session on the same machine:
        monkeypatch.setenv("HOME", str(tmp_path / "homeB"))
        lock_b = _ReaperLock()
        # FIXME: fails on current code (lock_b.acquire() is True — different
        # ~/.tortoise/.reaper.lock inode); passes once _LOCK_PATH is tempdir-scoped.
        assert not lock_b.acquire(), \
            "overlapping sweeps: second HOME acquired its own reaper lock"
    finally:
        lock_a.release()
```

Mechanics: `_ReaperLock(path=_LOCK_PATH)` is bound at import time from the
constant — the test must also handle the constant being evaluated at import;
either `monkeypatch.setattr(embedded_reaper, "_LOCK_PATH", <tmp-scoped>)` before
constructing, or construct with an explicit tempdir-scoped path and assert the
module constant itself is tempdir-scoped (Test 1b below). The subprocess variant
is the truest reproduction:

```python
def test_reaper_two_home_subprocesses_no_overlap(tmp_path):
    """Subprocess-level reproduction of the #1349 overlap: two processes,
    different HOME, same tempdir — exactly one may hold the reaper lock."""
    # spawn A: HOME=<tmp>/a  → acquire + sleep
    # spawn B: HOME=<tmp>/b  → acquire → must print FAILED
```

**Test 1b — scope invariant (guards the fix itself):**

```python
def test_reaper_lock_path_is_tempdir_scoped():
    """_LOCK_PATH must live under tempfile.gettempdir(), not ~/.tortoise."""
    from tortoise.embedded_reaper import _LOCK_PATH, _real_gettempdir
    assert _real_gettempdir() in _LOCK_PATH  # fails on current code
```

**Test 2 — existing sweep semantics preserved (must stay green):**
`test_cli_singleton_lock_prevents_concurrent` (test_reaper.py:939),
`test_cli_singleton_lock_released_on_sigkill` (test_reaper.py:955),
`test_chaos_singleton_lock_released_on_sigkill` (test_embedded_concurrency.py:602)
— all same-HOME; they exercise the same flock and must pass unchanged.

**Test 3 (optional) — end-sweep TOCTOU (secondary window, not in primary scope):**
assert the `others` decision (conftest.py:296-304) is consistent with the sweep
that follows; file separately if action is desired (see Open Questions).

**Red-green check:** run Test 1/1b against current code → red (both acquire /
HOME-scoped path). Apply the one-constant fix → green. Test 2 stays green both
ways.

## Complexity

| Domain | Rating | Rationale |
|--------|--------|-----------|
| Architecture | **standard** | concurrency fix in a production daemon; single-constant root-cause change; new regression surface is behavioral (lock scope), not structural |

No UX/Ontology/DB surfaces touched. Third-party deps: none (fcntl is stdlib).

## Wiring check

| Touch point | Type | Covered by |
|---|---|---|
| Sweep mutual exclusion (CLI + conftest) | embedded_reaper lock | Test 1/1b + Test 2 |
| Cron/scheduled reaper (`tools/install-reaper-schedule.sh`) | same `_ReaperLock` path | inherits fix; Test 2 |
| `docs/infra/embedded-reaper-cron.md` (lock path mention) | docs | update if path noted |
| `tortoise/shared_state/concurrency.py` | untouched (flock_exclusive remains the shared primitive) | — |

## Open questions

1. **Marker name**: the issue's O/I/T speaks of "the marker" read+set
   atomically; current code has no `sweep_started_at`. Confirm the flock
   (Alternative A) satisfies "the marker" as the lock file, or whether the
   literal marker + timestamp (Alternative C) is wanted for observability.
2. **End-sweep TOCTOU** (conftest.py:296-304): move the `others`/last-suite
   decision inside the flock, or leave as-is (mitigations already in depth)?
   Recommend a separate issue — out of this issue's scope (no overlapping-sweep
   window is the O/I; the TOCTOU does not create overlapping sweeps).
3. **Temp-cleaner unlink**: accept the residual risk (document) or add a
   re-stat-verify in `_ReaperLock.acquire()`? Recommend accept + comment.
4. **Windows**: `fcntl` is POSIX-only; reaper is macOS/Linux by contract
   (concurrency.py docstring) — confirm no Windows story needed.

## Verification gates (proportional)

- problem-verify: problem confirmed from code + issue; alternative framings
  rejected with evidence (see Phases 1/2). No fresh-context verifier dispatched
  — problem was pre-confirmed by the issue and code research; residual risk
  documented (marker-name discrepancy, confidence 90).
- solution-verify: A vs B vs C evaluated on outcome quality (not diff size);
  A chosen because it eliminates the failure-mode class (stale markers,
  cleanup races) rather than moving it. Rejected alternatives documented with
  "when this WOULD have been better" (B if literal marker naming required; C
  if sweep-start observability becomes a requirement).
