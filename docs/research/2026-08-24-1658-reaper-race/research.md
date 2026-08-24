# Research — #1658: embedded_reaper sweep_started_at race (overlapping sweeps under concurrency)

> Scope: read `tortoise/embedded_reaper.py` (sweep entry path, marker read/write,
> lock primitives), `tortoise/shared_state/concurrency.py`, `tests/conftest.py`
> (the `_redislite_hygiene` sweep), `tests/test_reaper.py` (lock tests).
> Worktree: `/private/tmp/tw-1349` (feat/1349-embedder-swap @ b63b17f1, origin/main @ a8cfa8a).

## 0. Headline finding — `sweep_started_at` does NOT exist in the current code

The issue body says: *"The current code (tortoise/embedded_reaper.py): the
`sweep_started_at` file marker is checked and written non-atomically"*.

**This is factually stale for the current codebase.** Exhaustive search found zero
occurrences of `sweep_started_at`:

- `grep -rn` across the worktree, `origin/main`, and the GitHub raw files → 0 hits
- `git log --all -S sweep_started_at`, `git rev-list --all` blob scan (22,139 objects),
  dangling commits (`git fsck`), all 18 stashes, and the full 440-commit pre-squash
  #1349 branch ancestry (`9d419f75..1201bef9`) → 0 hits
- GitHub code search API → only #1658 itself and issue #1349 title match

**Interpretation:** during #1349, a fix was *drafted* (the issue says "drafted but
reverted to keep PR1's zero-production-changes boundary"). The draft used a
`sweep_started_at` marker; it was never committed (PR #1619's merge diff contains
zero reaper/conftest changes, and its body explicitly lists "Embedded reaper
race-window fix (reverted from this branch — separate concern)"). The issue body
was written from memory of that draft. **The real current code uses a flock
(`_ReaperLock`), and the race is real but lives in a different (verifiable) place
— the lock's HOME scope vs the machine-global sweep target.** See §3.

## 1. The actual sweep entry path (current code)

The reaper has two sweep entry points, both of which DO take a lock today:

### 1a. `tests/conftest.py::_redislite_hygiene` (the observed `[redislite-hygiene]` path)

`tests/conftest.py:204-245` — session-scoped autouse fixture. `_sweep()`:

```python
def _sweep(only_safe: bool) -> dict:
    try:
        lock = _ReaperLock()                      # conftest.py:206
        if not lock.acquire():
            return {"skipped": "reaper-lock-held"}# conftest.py:208
        try:
            ...
            while True:
                acted = _run_sweep(dry_run=False, batch_size=SWEEP_BATCH_SIZE,
                                   only_safe=only_safe, jobs=8, kill_pacing=0.4)
            ...
        finally:
            lock.release()                        # conftest.py:245
```

- Session **start**: `_sweep(only_safe=True)` — logs `[redislite-hygiene] start sweep: ...` (conftest.py:252)
- Session **end**: `_sweep(only_safe=bool(others))` — logs `[redislite-hygiene] end sweep ...` (conftest.py:304-305)

### 1b. CLI `main()`

`tortoise/embedded_reaper.py:2155-2175`:

```python
lock = _ReaperLock()
if not lock.acquire():
    logger.warning("reaper already running (PID %s)", _lock_holder_pid())
    return 0
try:
    signal.signal(signal.SIGALRM, _alarm_handler); signal.alarm(timeout)
    acted = _run_sweep(...)
finally:
    lock.release()
```

### 1c. `_run_sweep` itself is lock-free

`embedded_reaper.py:1960-1971` — docstring explicitly states the contract:

> "NOTE: the reaper singleton lock is held by main() (CLI); direct callers
> (tests, conftest session hygiene) run unlocked — pre-existing contract,
> unchanged by #1383."

This docstring is **stale**: conftest's `_sweep()` has acquired `_ReaperLock`
since commit `41d1ddf7` (#1020, fix(1005)). The lock-free-contract comment was
not updated.

## 2. The lock primitives available

### 2a. `_ReaperLock` — `tortoise/embedded_reaper.py:1747-1790` (the reaper's own lock)

```python
_LOCK_PATH = os.path.join(os.path.expanduser("~"), ".tortoise", ".reaper.lock")  # :1749-1750
TIMEOUT_DEFAULT = 120

class _ReaperLock:
    """fcntl-based exclusive lock; auto-released on process exit (incl. SIGKILL)."""
    def acquire(self) -> bool:
        import fcntl
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._fh = open(self.path, "a")
        try:
            fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)   # non-blocking, atomic
            self._fh.seek(0); self._fh.truncate()
            self._fh.write(str(os.getpid())); self._fh.flush()
            return True
        except OSError:
            self._fh.close(); self._fh = None
            return False
    def release(self) -> None:
        import fcntl
        if self._fh:
            try: fcntl.flock(self._fh, fcntl.LOCK_UN)
            except OSError: pass
            self._fh.close(); self._fh = None
```

- `fcntl.flock(LOCK_EX | LOCK_NB)` is **atomic at the kernel level** — a correct
  mutual-exclusion primitive, auto-released on process death (tested by
  `test_cli_singleton_lock_released_on_sigkill`, test_reaper.py:955).
- **The defect is the lock's *scope*, not its mechanism:** `_LOCK_PATH` is
  keyed to the user's HOME (`~/.tortoise/.reaper.lock`).

### 2b. `flock_exclusive` — `tortoise/shared_state/concurrency.py:18-43` (shared primitive)

```python
@contextlib.contextmanager
def flock_exclusive(path: Path, *, timeout_ms: float = 5000.0) -> Iterator[int]:
    """Acquire an exclusive advisory flock on ``path`` (created if absent)...
    POSIX-only (fcntl) — macOS/Linux eval env, no Windows story."""
```

- Same flock mechanism, blocking-with-timeout flavor. Used by
  `locked_append` (concurrency.py:47) and `tools/longmem_eval/run.py:597,702`.
- Not currently used by the reaper. It is a *context-manager* variant of the
  same primitive; `_ReaperLock` is the reaper-specific non-blocking flavor.
- `atomic_claim` (concurrency.py:70-103) shows the repo's O_EXCL atomic-create
  pattern (used for card claiming) — the "atomic marker" alternative vehicle.

### 2c. Tempdir-scoped state precedent — `ACTIVE_SUITES_DIR`

`embedded_reaper.py:109-110`:

```python
ACTIVE_SUITES_DIR = os.path.join(
    os.path.realpath(tempfile.gettempdir()), ".tortoise", "active_suites")
```

**The repo already has a tempdir-scoped coordination directory**
(`<tempdir>/.tortoise/`). Suite markers live there, machine-global. The sweep
lock does not — it lives under the per-user HOME. This asymmetry is the root
cause (§3).

## 3. The exact race window (line-referenced)

### Primary race: lock scope (HOME) ≠ sweep target (shared tempdir)

```
Sweep target:      tempfile.gettempdir()                  (machine-global on Linux: /tmp)
Lock file:         ~/.tortoise/.reaper.lock               (per-user HOME)
                   ^^^^^^^^^^^  embedded_reaper.py:1749-1750
Active-suite dir:  <tempdir>/.tortoise/active_suites      (machine-global, :109-110)
```

On a **shared machine** (Linux CI runner, dev box with multiple agents/containers,
multiple users), the tempdir `/tmp` is shared by every user, but each user/agent
has a **different `$HOME`** → a different `.reaper.lock` file. Concretely:

1. Sweeper P1 (HOME=/Users/a) calls `_ReaperLock().acquire()` →
   opens `/Users/a/.tortoise/.reaper.lock` → `flock` succeeds.
2. Sweeper P2 (HOME=/Users/b, same machine, same `/tmp`) calls
   `_ReaperLock().acquire()` → opens `/Users/b/.tortoise/.reaper.lock` →
   **a different inode** → `flock` also succeeds.
3. Both run `_run_sweep()` against the **same shared tempdir** concurrently →
   overlapping sweeps → P1 reaps a server P2 considers live and vice versa
   ("reaping each other's live sockets").

This is exactly the observed scenario: *"parallel pytest sessions on a shared
tempdir — observed heavily during #1349's test runs under machine contention"*
and the paradoxical log `[redislite-hygiene] start sweep: {'skipped':
'reaper-lock-held'}` (a same-HOME third session correctly skipping) **coexisting
with** overlapping sweeps (the cross-HOME pair not serialized). The lock works
only for same-HOME concurrency; the issue's observed overlap is the cross-HOME
case.

On macOS the default tempdir is per-user (`/var/folders/...`), so the HOME-scoped
lock happens to coincide with the tempdir scope — the bug is masked. On Linux
(`/tmp` shared) it is live. CI + multi-agent dev boxes are Linux.

### Secondary window: the end-sweep "last suite" decision is made outside the lock

`tests/conftest.py:296-304`:

```python
others = [t for t in active_suite_tokens() if t != token]   # read OUTSIDE flock
...
end_result = _sweep(only_safe=bool(others))                  # lock taken inside
```

The `others` decision (full sweep vs only_safe) is a TOCTOU: suite A reads
`others=[]` (believes it is last), suite B registers its marker concurrently,
and A then runs a **full** end-sweep (`only_safe=False`, `TORTOISE_REAPER_MIN_UPTIME=0`)
against B's live between-tests server. This is mitigated in depth (boot cooldown
30s, `_mark_orphan_confirmation` 10-min 0-client window, #1642 FIX 3/4) but the
decision is not atomic with the sweep. **Not the primary fix target** — the
marker-based foreign-suite detection is deliberate (#1642 FIX 4 replaced the
pgrep check), but worth an open question.

## 4. Existing tests (what already covers the lock)

- `tests/test_reaper.py:939` `test_cli_singleton_lock_prevents_concurrent` —
  second CLI instance while lock held → exits 0 "already running". Same-HOME.
- `tests/test_reaper.py:955` `test_cli_singleton_lock_released_on_sigkill` —
  SIGKILL holder → kernel auto-releases → second acquires. Same-HOME.
- `tests/test_embedded_concurrency.py:602` `test_chaos_singleton_lock_released_on_sigkill` —
  chaos-context variant.
- **No test exists for cross-HOME/cross-user concurrency on a shared tempdir** —
  the actual race. `tests/test_reaper.py:1243` `test_run_sweep_includes_stale_pid_files`
  calls `_run_sweep` directly (unlocked, per the documented contract).

## 5. Minimal correct fix (candidate — full evaluation in scoping.md)

**Make the reaper lock tempdir-scoped, mirroring `ACTIVE_SUITES_DIR`:**

```python
# embedded_reaper.py:1749-1750
_LOCK_PATH = os.path.join(
    os.path.realpath(tempfile.gettempdir()), ".tortoise", ".reaper.lock")
```

- Both entry points (`main()` and conftest `_sweep()`) inherit the change via
  `_ReaperLock()`'s default — **one constant change, no new primitive**.
- All sweepers of the **same tempdir** (any HOME/user) now contend on **one
  flock** → the second sweeper observes the marker set and skips →
  "no overlapping-sweep window remains" (issue Target 2).
- The flock is the atomic guard: read+set is a single `flock` syscall,
  kernel-released on crash (SIGKILL-safe, already tested).
- The lock file lives under `<tempdir>/.tortoise/` — the same dir the reaper's
  own `ACTIVE_SUITES_DIR` uses, so it is invisible to `discover()`'s socket-dir
  walk (`find -maxdepth 2 -name redis.socket -o -name redis.pid`),
  `_sweep_quarantine_dirs` (`*reaper-stale-*`), and
  `sweep_stale_index_pid_files` (`index-*.pid`) → **self-reap safe**.
- Edge case to document: an OS temp-cleaner (`tmpreaper`) deleting the lock file
  mid-sweep would split the flock across inodes (holder keeps old fd, new
  acquirer creates a new file). Same exposure already accepted for
  `ACTIVE_SUITES_DIR` markers; a defensive re-stat/verify could be added but is
  not required for the common case (lock held ≤ 120s).

Alternatives (flock-only-scope vs atomic-file-marker vs both) are evaluated in
`scoping.md` §3.
