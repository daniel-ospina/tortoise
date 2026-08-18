---
title: "Implementation Plan #1383 — reaper discover/reap production semantics: dead-pid stale_socket + bounded probe retry"
type: engineering
domain: platform
doc_status: live
created: 2026-08-18
subjects.team: epistemic-team
aboutSubjects: tortoise-embedded
aboutObjects: redislite-lifecycle, reaper, test-isolation, concurrency-guard
---

<!-- research-path: issue-scoping comment on #1383 (comment-5328717005, double-diamond v5.1, all gates clean) -->

# Reaper Discover/Reap Semantics #1383 — Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Make `discover()` output honest — every `'candidate'` record reapable — and stop the load-sensitive fail-closed probe from stranding live orphans, by giving the dead-pid `'stale_socket'` classification a first-class guarded-rmtree action in `reap()` and adding a bounded probe retry.

**Team:** epistemic-team
**Role:** product-implementer

**Architecture:** Approach A — reap() as the two-verb action engine (chosen over B: two-pass sweep entrenches the dead-end reap() contract; over C: probe-first classification has live-pass perf cost and recreates the phantom via its own age gate — see scoping comment for the full rationale). Pipeline stays `discover() → phase1_probe() → reap()`; reap() admits `candidate | stale_socket`. The `stale_socket` action is a 9-guard guarded rmtree: containment → pidfile re-read → ECONNREFUSED-only socket re-probe (short timeout) → mtime age guard → atomic rename-aside → post-rename re-probe → rmtree only the renamed path, else leave quarantine. Plus: Z-aware `_pid_alive`, bounded read-timeout-only retry in `_raw_resp_client_list`, `_probe_socket` 4-status contract, `client_count=None` on probe failure, per-sweep stale budget, `_run_sweep` integration, quarantine-resweep convergence.

### Pattern Research

> Gate skipped: plan touches zero third-party dependencies — stdlib `socket`/`os`/`shutil`/`subprocess`/`pathlib` only; no lockfile drift. All external best-practice findings (pidfile-advisory, zombie `/proc` discriminator, systemd-tmpfiles socket-liveness verification, TOCTOU check-then-delete discipline, atomic rename-aside) are embedded with provenance in the scoping comment's `### Axis Research` (canonical / competitor-precedent / pitfalls framings, findings-date 2026-08-18).

### Integration Surface Map

| # | Surface | Layer | Test | Bug-pattern flags (≥2 per surface) |
|---|---------|-------|------|-----------------------------------|
| S1 | `_probe_socket` return contract (3→4 statuses: `dead`/`missing`/`alive`/`undetermined`) | unit | Task 1 probe-contract tests | (1) ECONNREFUSED vs ENOTSOCK platform divergence (regular-file socket → ENOTSOCK/"undetermined" on macOS, ECONNREFUSED on Linux) — fixtures must use real bind+close sockets; (2) FileNotFoundError conflation (missing = mid-startup, must NOT classify stale) |
| S2 | Classification (`discover`/`_classify_dir`/`_classify`/`_cooldown_check`/`_classify_live`) | unit + integration | Task 2 classification tests | (1) live-pass regression: stale registry pidfile on a LIVE pgrep'd server must never classify stale_socket (known_pid pass-through); (2) dead-pid phantom: dead authoritative pid must classify stale_socket, not candidate |
| S3 | `reap()` action engine + `_remove_stale_socket_dir` guards | unit (real dead sockets) + integration | Task 3 guard tests | (1) TOCTOU respawn window — probe is a point-in-time snapshot; rename-aside + post-rename re-probe close the delete-race; (2) containment: semi-public reap() must refuse non-tempdir records; (3) partial rmtree leftovers must converge (quarantine resweep); (4) quarantine-dir re-entry: `redislite_x.reaper-stale-*` matches EPHEMERAL_PREFIXES via startswith → discover pass 2 must SKIP reaper-owned quarantine dirs, else a guard-7-preserved LIVE server is killed by the next sweep's candidate path (plan-review P1) |
| S3b | Quarantine dirs (`*.reaper-stale-*`) ownership | unit + integration | Task 4 skip + re-entry tests | (1) discover pass 2 excludes them (handled exclusively by `_sweep_quarantine_dirs`); (2) `_remove_stale_socket_dir` rejects dbdirs containing the suffix |
| S4 | `_run_sweep` pipeline + quarantine sweep | integration | Task 4 pipeline tests | (1) SIGALRM 120s budget — stale work must be budgeted (STALE_SWEEP_BUDGET) and short-probed (ECONNREFUSED-fast); (2) lock assumption: only `main()` holds `_ReaperLock`; direct `_run_sweep` callers (conftest) are unlocked — documented, unchanged |
| S5 | Probe retry (`_raw_resp_client_list`) | unit (threaded fake socket server) | Task 5 retry tests | (1) retry must fire on read-timeout only — refused/missing are reliable verdicts; (2) timing flake risk — server helpers must be deterministic (first_delay 0.6s, never_reply <0.5s per conn, per-connection thread, try/finally teardown) |
| S6 | `_pid_alive` zombie Z-check | unit | Task 1 zombie tests | (1) Linux-only (/proc) — macOS gap documented, matches test-helper precedent; (2) ripple across all 8 call sites / 5 functions is benign (zombie ≡ dead for every liveness gate: `active_suite_tokens`, `_derive_real_pid`×3, `_classify_dir`, `phase1_probe`, reap gate, `_kill`; `_is_detached` uses `ps` ppid, not `_pid_alive`); (3) comm-with-spaces stat parsing must use the rfind(')') form |
| S7 | Concurrency-suite regression (`tests/test_embedded_concurrency.py`) | integration | chaos tests stay green | (1) SIGKILL'd parents → zombie windows on Linux; (2) delta fixtures must not see the reaper's own stale cleanup as interference |
| S8 | conftest session hygiene (`_run_sweep(only_safe=True)` at session start) | integration | full test file green | (1) stale rmtree activates at every pytest session — guards are load-bearing from day one; (2) strictly stricter than the module `_sweep_stale_residue` fixtures (age + probes) — no behavior surprise; (3) STALE_SWEEP_BUDGET bounds the serial stale work (no SIGALRM in conftest's direct `_run_sweep` call) |
| S9 | CLI `--json` output contract | integration | Task 4/6 CLI test | (1) new classifications `stale_socket`/`stale_quarantine` emitted; (2) quarantine entries must carry `quarantine_dir` (main() emitter updated); stale acted records carry the renamed `dbdir` |

### Verification Plan

Domain: **code** (Python, no migrations/auth/UI/content). Complexity: standard (Architecture=medium, UX=low, Ontology=low).

- **Unit layer** (majority — the guards and probe contract are pure logic with injectable seams): `uv run pytest tests/test_reaper.py -q -p no:cacheprovider --timeout=300`
- **Integration layer** (real redislite orphans + real dead unix sockets): reap end-to-end tests + chaos regression: `uv run pytest tests/test_embedded_concurrency.py -q -p no:cacheprovider --timeout=300 -k chaos`
- **Cross-suite regression:** `uv run pytest tests/test_embedded_lifecycle.py tests/test_embedded_lifecycle_fast_close.py -q` if present (fast-close/atexit seams untouched — no expected impact)
- **Full regression before commit (commit-workflow preflight):** `uv run pytest tests/test_reaper.py tests/test_embedded_concurrency.py -q -p no:cacheprovider --timeout=300`
- **Skip:** no pgTAP (no migrations), no e2e/UX (no UI), no config/content/research verification.
- **Environment note:** the machine is under load (~30 load avg) and another suite may hold `~/.tortoise/.reaper.lock` — "reaper already running" failures are environmental; retry. Expect slow runs; `--timeout=300` per file.

## Task 1: Foundation — `_probe_socket` 4-status contract + Z-aware `_pid_alive`

**Intent:** Split the conflated probe verdicts (ECONNREFUSED "dead" vs FileNotFoundError "missing" vs timeout) so the stale action can fail closed on mid-startup dirs, and make `_pid_alive` treat zombies as dead (the in-repo #1365 precedent), before any destructive action depends on them.
**Acceptance:** `_probe_socket` returns exactly one of `"dead"|"missing"|"alive"|"undetermined"`; `_pid_alive` returns False for a Linux zombie; existing tests pass unchanged (no direct `_probe_socket` test callers exist — verified).
**Files:**
- Modify: `tortoise/embedded_reaper.py` (`_probe_socket` ~392-413, `_pid_alive` ~260-268)
- Test: `tests/test_reaper.py`

**Step 1: Write the failing tests**

```python
# tests/test_reaper.py
def test_probe_socket_dead_socket_file_exists():
    """A real dead unix socket (bind+close, file persists) -> 'dead'."""
    from tortoise.embedded_reaper import _probe_socket
    d = tempfile.mkdtemp(prefix="reaper_probe_")
    try:
        sp = os.path.join(d, "redis.socket")
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(sp)
        s.close()  # file persists, no listener -> ECONNREFUSED
        assert _probe_socket(sp) == "dead"
    finally:
        shutil.rmtree(d, ignore_errors=True)

def test_probe_socket_missing_file():
    """No socket file at all -> 'missing' (NOT 'dead' — mid-startup window)."""
    from tortoise.embedded_reaper import _probe_socket
    assert _probe_socket("/nonexistent/reaper-missing.sock") == "missing"

def test_probe_socket_alive_listener():
    """A live listener -> 'alive'."""
    from tortoise.embedded_reaper import _probe_socket
    d = tempfile.mkdtemp(prefix="reaper_probe_")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sp = os.path.join(d, "live.sock")
    srv.bind(sp); srv.listen(1)
    try:
        assert _probe_socket(sp) == "alive"
    finally:
        srv.close(); shutil.rmtree(d, ignore_errors=True)

def test_pid_alive_zombie_returns_false():
    """Linux-only: a real zombie (forked child that exited, never waited)
    is NOT alive — the /proc stat Z check (#1365 precedent)."""
    import pytest as _p
    if not os.path.exists("/proc"):
        _p.skip("no /proc on this platform")
    from tortoise.embedded_reaper import _pid_alive
    pid = os.fork()
    if pid == 0:
        os._exit(0)  # child dies immediately; parent never waits -> zombie
    # reap only when the child has exited (becomes a zombie)
    deadline = time.time() + 10
    while time.time() < deadline:
        with open(f"/proc/{pid}/stat") as fh:
            state = fh.read().split()[2]
        if state == "Z":
            break
        time.sleep(0.05)
    else:
        os.waitpid(pid, 0)
        _p.fail("child never became a zombie")
    try:
        assert not _pid_alive(pid), "zombie reported alive"
    finally:
        os.waitpid(pid, 0)  # reap the zombie
```

**Step 2: Run to verify they fail** — `uv run pytest tests/test_reaper.py -q -p no:cacheprovider --timeout=300 -k "probe_socket or pid_alive"` — expect FAIL (current `_probe_socket` returns "dead" for missing files and has no 4-status contract; `_pid_alive` reports zombies alive).

**Step 3: Implement**

```python
def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False
    # #1383: a zombie answers kill(0) but its fds are gone — treat as dead
    # (precedent: test_embedded_concurrency._pid_alive, #1365). Linux-only:
    # macOS has no /proc; plain kill(0) behavior there is documented.
    try:
        stat_text = Path(f"/proc/{pid}/stat").read_text()
        # stat = "pid (comm) state ..." — comm may contain spaces, so the
        # state field is the first token AFTER the closing paren.
        state = stat_text[stat_text.rfind(")") + 2:].split()[0]
        if state == "Z":
            return False
    except (OSError, IndexError, ValueError):
        pass  # macOS: no /proc — fall back to kill(0) semantics
    return True


def _probe_socket(socket_path: str, timeout: float = PROBE_TIMEOUT) -> str:
    """Raw unix-socket connect probe (never redis-py — can't spawn).

    Four verdicts (#1383 — FileNotFoundError must NOT collapse into 'dead':
    a missing socket file is the mid-startup window and must fail closed):
      - 'dead'         (ECONNREFUSED — socket FILE EXISTS, no listener)
      - 'missing'      (FileNotFoundError — no socket file at all)
      - 'alive'        (accepts connections)
      - 'undetermined' (timeout / other error)
    """
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            s.connect(socket_path)
            return "alive"
        except ConnectionRefusedError:
            return "dead"
        except FileNotFoundError:
            return "missing"
        except socket.timeout:
            return "undetermined"
        except OSError:
            return "undetermined"
        finally:
            s.close()
    except OSError:
        return "undetermined"
```

**Step 4: Run to verify they pass** — same command; expect PASS.

**Step 5: Commit** — `git add tortoise/embedded_reaper.py tests/test_reaper.py` + commit via `commit-workflow` (message: `fix(1383): probe contract 4-status + zombie-aware _pid_alive`).

## Task 2: Classification honesty — dead-pid → `stale_socket`, `known_pid` pass-through, `client_count=None`

**Intent:** Make `discover()` output honest (indicator a): a dead authoritative pid classifies `stale_socket`, never the phantom `candidate`; LIVE pass-1 servers must be immune to stale registry pidfiles; probe-failed client counts record None, not a false 0.
**Acceptance:** `_cooldown_check(registry, pid=None)` returns `"stale_socket"` when the authoritative pid is dead (Z-aware); `_classify_dir(dbdir, socket_path, known_pid=None)` uses `known_pid` when provided; `_classify_live` passes the pgrep pid so live servers never classify stale_socket; `_classify_dir` sets `client_count=None` on probe failure; existing discover tests green.
**Files:**
- Modify: `tortoise/embedded_reaper.py` (`_cooldown_check` ~871-884, `_classify` ~803-869, `_classify_dir` ~764-801, `_classify_live` ~686-712, `_discover_from_live`)
- Test: `tests/test_reaper.py`

**Step 1: Write the failing tests**

```python
# tests/test_reaper.py
def _make_dead_pid_dir(tmp_path, name="redislite_deadpid"):
    """Synthetic leftover dir with a real dead socket + registry pointing at
    a provably-dead pid. Returns (dbdir, socket_real)."""
    from tortoise.embedded_reaper import _registry_for  # noqa: F401
    dbdir = tmp_path / name
    dbdir.mkdir()
    sp = dbdir / "redis.socket"
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(str(sp))
    s.close()  # real dead socket file
    (dbdir / "redis.pid").write_text("99999999\n")  # > pid_max everywhere
    (dbdir / "x.settings").write_text(json.dumps({
        "pidfile": str(dbdir / "redis.pid"),
        "unixsocket": str(sp),
        "dbdir": str(dbdir),
        "dbfilename": "redis.db",
    }))
    return dbdir, os.path.realpath(str(sp))

def test_discover_classifies_dead_pid_dir_stale_socket(tmp_path):
    """Indicator (a): a dead-pid leftover dir -> 'stale_socket', NOT
    the phantom 'candidate' (reap() can never act on dead-pid candidates)."""
    from tortoise.embedded_reaper import discover
    dbdir, sock = _make_dead_pid_dir(tmp_path)
    with monkeypatch_tempdir(tmp_path):
        found = discover()
        matches = [s for s in found if str(dbdir) in s.get("dbdir", "")]
        assert matches, "dead-pid dir not discovered"
        assert matches[0]["classification"] == "stale_socket"

def test_classify_live_never_stale_socket_for_respawned_server(monkeypatch):
    """Regression (PM3): a LIVE pgrep'd server whose registry pidfile is
    stale must NOT classify stale_socket — the known_pid pass-through keeps
    classification based on the authoritative live pid."""
    from tortoise.embedded_reaper import _classify_dir, _cooldown_check
    monkeypatch.setenv("TORTOISE_REAPER_MIN_UPTIME", "0")
    dbdir, sock = _make_dead_pid_dir(Path(tempfile.mkdtemp(prefix="tortoise_live_")), name="redislite_x")
    try:
        rec = _classify_dir(str(dbdir), sock, known_pid=os.getpid())
        assert rec["classification"] in ("candidate", "protected")
        assert rec["classification"] != "stale_socket"
        # and the no-known-pid path (pass-2 walk) still sees stale_socket
        rec2 = _classify_dir(str(dbdir), sock)
        assert rec2["classification"] == "stale_socket"
    finally:
        shutil.rmtree(dbdir, ignore_errors=True)

def test_classify_dir_client_count_none_on_probe_failure(monkeypatch, tmp_path):
    """Probe-failed client count records None, not a false 0 — must FAIL
    pre-fix (old behavior records 0) and pin the fix."""
    from tortoise.embedded_reaper import _classify_dir
    monkeypatch.setenv("TORTOISE_REAPER_MIN_UPTIME", "0")
    dbdir, sock = _make_dead_pid_dir(tmp_path)
    # Force probe failure on a live-pid candidate record.
    monkeypatch.setattr(
        "tortoise.embedded_reaper._active_client_count", lambda _s: None)
    with monkeypatch_tempdir(tmp_path):
        # known_pid live -> candidate path -> probe fails -> None
        rec = _classify_dir(str(dbdir), sock, known_pid=os.getpid())
        assert rec is not None
        assert rec["classification"] == "candidate"
        assert rec["client_count"] is None, "probe failure must not record 0"
```

**Step 2: Run to verify they fail** — `uv run pytest tests/test_reaper.py -q -p no:cacheprovider --timeout=300 -k "dead_pid or live_never_stale or client_count_none"` — expect FAIL (dead-pid dirs classify candidate; client_count 0).

**Step 3: Implement**

```python
def _classify_dir(dbdir: str, socket_path: str,
                  known_pid: int | None = None) -> dict | None:
    """Classify a single candidate dir. Returns record or None (skip)."""
    registry = _registry_for(dbdir)
    dbdir_real = os.path.realpath(dbdir)
    socket_real = os.path.realpath(socket_path)
    tmpdir_real = _real_gettempdir()

    # Authoritative pid: pass-1 live servers supply the pgrep pid (always
    # live); pass-2 walk dirs fall back to the registry pidfile.
    pid = known_pid
    if pid is None and registry and registry.get("pidfile"):
        try:
            pid = int(Path(registry["pidfile"]).read_text().strip())
        except (OSError, ValueError):
            pid = None

    classification = _classify(socket_real, dbdir_real, tmpdir_real,
                               registry, pid=pid)
    reg_dir = (registry or {}).get("dir", (registry or {}).get("dbdir", ""))
    dir_missing = _dir_missing_on_disk(reg_dir)

    uptime = _uptime_seconds(pid) if pid else None
    client_count = None  # None = probe failed/unknown; 0 = verified zero
    if classification == "candidate" and pid and _pid_alive(pid):
        cc = _active_client_count(socket_real)
        if cc is not None:
            client_count = cc
    return {...}  # unchanged fields


def _classify(socket_real, dbdir_real, tmpdir_real, registry,
              pid: int | None = None) -> str:
    ...  # existing signals unchanged; both `return _cooldown_check(...)`
    # call sites pass pid through:
    return _cooldown_check(registry, pid=pid)


def _cooldown_check(registry: dict | None, pid: int | None = None) -> str:
    """Boot-cooldown: fresh servers (uptime < MIN_UPTIME) are protected.

    #1383: a DEAD authoritative pid (Z-aware) classifies 'stale_socket' —
    a leftover dir no process owns, reapable by guarded rmtree — instead of
    a phantom 'candidate' reap()'s liveness-first gate can never act on.
    """
    if pid is None and registry and registry.get("pidfile"):
        try:
            pid = int(Path(registry["pidfile"]).read_text().strip())
        except (OSError, ValueError):
            pid = None
    if pid is not None and not _pid_alive(pid):
        return "stale_socket"
    uptime = _uptime_seconds(pid) if pid else 0.0
    min_uptime = _parse_min_uptime()
    if uptime is not None and uptime < min_uptime:
        return "protected"
    return "candidate"


def _classify_live(pid: int) -> dict | None:
    sock_dir = _socket_dir_from_cmdline(pid)
    if not sock_dir:
        return None
    socket_path = os.path.join(sock_dir, "redis.socket")
    rec = _classify_dir(sock_dir, socket_path, known_pid=pid)
    if rec is None:
        return None
    rec["pid"] = pid  # pgrep pid is authoritative for live servers
    rec["_live"] = True
    return rec
```

**Step 4: Run to verify they pass** — same command; expect PASS.

**Step 5: Commit** — message: `fix(1383): dead-pid dirs classify stale_socket (known_pid pass-through; client_count None on probe failure)`.

## Task 3: reap() stale branch — `_remove_stale_socket_dir` guarded rmtree + budget + log fix

**Intent:** Give `stale_socket` records the reap action the 2026-08-06 plan promised and reap() never wired — a 9-guard guarded rmtree that can never delete a live server's data — while keeping the candidate kill path byte-for-byte equivalent in semantics and fixing the misleading liveness-gate log.
**Acceptance:** `reap()` processes `stale_socket` records via `_remove_stale_socket_dir`; every guard aborts without partial delete (the stat-OSError paths at guard 5 return silently; guard 1 returns acted at INFO — the WARNING convention covers the probe/pid/age abort paths); stale removals don't consume the kill `batch_size` and are capped by `STALE_SWEEP_BUDGET`; `only_safe` admits stale removal (guards are the safety); budget exhaustion stops candidate kills but not stale cleanup; dry-run reports without mutating; log says "dead pid, skipping".
**Files:**
- Modify: `tortoise/embedded_reaper.py` (constants ~38-67; `reap` ~925-1006; new `_remove_stale_socket_dir`; `_cleanup_tempdir` reuse)
- Test: `tests/test_reaper.py`

**Step 1: Write the failing tests**

```python
# tests/test_reaper.py
def _backdate_dir(dbdir, seconds=120):
    old = time.time() - seconds
    os.utime(dbdir, (old, old))

def _stale_record(dbdir, sock):
    return {
        "pid": 99999999,  # dead everywhere (> pid_max)
        "socket_path": sock,
        "dbdir": str(dbdir),
        "client_count": None,
        "uptime": None,
        "classification": "stale_socket",
        "settings": None,
    }

def test_reap_removes_stale_socket_dir(tmp_path):
    """Old dead-pid dir -> rmtree'd; acted list contains it with the
    renamed path recorded for --json correlation."""
    from tortoise.embedded_reaper import reap
    dbdir, sock = _make_dead_pid_dir(tmp_path)
    _backdate_dir(dbdir)
    acted = reap([_stale_record(dbdir, sock)], dry_run=False)
    assert any(a["dbdir"] == str(dbdir) for a in acted)
    assert any(".reaper-stale-" in a.get("removed_dir", "") for a in acted), \
        "acted record must carry the renamed path in removed_dir"
    assert not os.path.exists(dbdir), "stale dir not removed"

def test_reap_stale_socket_dry_run_reports_without_mutating(tmp_path):
    from tortoise.embedded_reaper import reap
    dbdir, sock = _make_dead_pid_dir(tmp_path)
    _backdate_dir(dbdir)
    acted = reap([_stale_record(dbdir, sock)], dry_run=True)
    assert acted  # reported as would-act
    assert os.path.exists(dbdir), "dry-run must not mutate"

def test_reap_stale_socket_aborts_when_pidfile_pid_alive(tmp_path):
    """Respawn window: pidfile now holds a LIVE pid -> abort, keep dir."""
    from tortoise.embedded_reaper import reap
    dbdir, sock = _make_dead_pid_dir(tmp_path)
    _backdate_dir(dbdir)
    (dbdir / "redis.pid").write_text(f"{os.getpid()}\n")  # live pid
    acted = reap([_stale_record(dbdir, sock)], dry_run=False)
    assert acted == []
    assert os.path.exists(dbdir)

def test_reap_stale_socket_aborts_when_socket_alive(tmp_path):
    """A live listener on the socket -> abort, keep dir."""
    from tortoise.embedded_reaper import reap, _probe_socket
    dbdir, sock = _make_dead_pid_dir(tmp_path)
    _backdate_dir(dbdir)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock); srv.listen(1)
    try:
        acted = reap([_stale_record(dbdir, sock)], dry_run=False)
        assert acted == []
        assert os.path.exists(dbdir)
    finally:
        srv.close()

def test_reap_stale_socket_aborts_when_socket_missing(tmp_path):
    """No socket file (mid-startup window) -> abort, keep dir."""
    from tortoise.embedded_reaper import reap
    dbdir, sock = _make_dead_pid_dir(tmp_path)
    _backdate_dir(dbdir)
    os.remove(sock)  # socket file gone -> 'missing' verdict
    acted = reap([_stale_record(dbdir, sock)], dry_run=False)
    assert acted == []
    assert os.path.exists(dbdir)


def test_reap_stale_socket_already_gone_reported_acted(tmp_path):
    """Guard 1: an already-vanished dir is reported acted (no error)."""
    from tortoise.embedded_reaper import reap
    dbdir, sock = _make_dead_pid_dir(tmp_path)
    _backdate_dir(dbdir)
    shutil.rmtree(dbdir, ignore_errors=True)  # dir genuinely gone (non-empty
    # -> os.rmdir would raise; plan-review cycle 2 P1)
    acted = reap([_stale_record(dbdir, sock)], dry_run=False)
    assert any(a["dbdir"] == str(dbdir) for a in acted)


def test_reap_stale_socket_rename_failure_aborts(tmp_path, monkeypatch):
    """Guard 6: os.rename OSError -> abort, dir intact, WARN logged."""
    from tortoise.embedded_reaper import reap
    dbdir, sock = _make_dead_pid_dir(tmp_path)
    _backdate_dir(dbdir)
    real_rename = os.rename
    def _boom(src, dst):
        raise OSError("simulated rename failure")
    monkeypatch.setattr("tortoise.embedded_reaper.os.rename", _boom)
    try:
        acted = reap([_stale_record(dbdir, sock)], dry_run=False)
    finally:
        monkeypatch.setattr("tortoise.embedded_reaper.os.rename", real_rename)
    assert acted == []
    assert os.path.exists(dbdir)


def test_reap_stale_socket_quarantine_probe_alive_leaves_dir(tmp_path, monkeypatch):
    """Guard 7: post-rename re-probe 'alive' (respawn in the window) ->
    leave quarantine, NEVER delete. The TOCTOU closer, pinned."""
    from tortoise.embedded_reaper import reap
    dbdir, sock = _make_dead_pid_dir(tmp_path)
    _backdate_dir(dbdir)
    calls = {"n": 0}
    def _seq_probe(path, timeout=2.0):
        calls["n"] += 1
        return "dead" if calls["n"] == 1 else "alive"  # guard 4 dead, guard 7 alive
    monkeypatch.setattr("tortoise.embedded_reaper._probe_socket", _seq_probe)
    acted = reap([_stale_record(dbdir, sock)], dry_run=False)
    assert acted == []
    assert not os.path.exists(dbdir)  # renamed away
    import glob as _g
    quars = _g.glob(str(dbdir) + ".reaper-stale-*")
    assert quars, "quarantine dir must exist (not deleted)"


def test_reap_stale_socket_quarantine_moved_pid_alive_aborts(tmp_path, monkeypatch):
    """Guard 8: a live pid written into the MOVED pidfile during the
    window (backlog-full hardening) -> leave quarantine, never delete."""
    from tortoise.embedded_reaper import reap
    dbdir, sock = _make_dead_pid_dir(tmp_path)
    _backdate_dir(dbdir)
    real_rename = os.rename
    def _rename_and_rewrite(src, dst):
        real_rename(src, dst)
        Path(dst, "redis.pid").write_text(f"{os.getpid()}\n")  # live pid now
    monkeypatch.setattr("tortoise.embedded_reaper.os.rename", _rename_and_rewrite)
    acted = reap([_stale_record(dbdir, sock)], dry_run=False)
    assert acted == []
    import glob as _g
    assert _g.glob(str(dbdir) + ".reaper-stale-*"), "quarantine kept"


def test_reap_stale_socket_rejects_quarantine_dir(tmp_path):
    """Re-entry guard: a dbdir already containing the quarantine suffix is
    never re-renamed (discover pass 2 also skips them — plan-review P1)."""
    from tortoise.embedded_reaper import reap
    dbdir, sock = _make_dead_pid_dir(tmp_path)
    _backdate_dir(dbdir)
    q = str(dbdir) + ".reaper-stale-999"
    os.rename(dbdir, q)
    acted = reap([_stale_record(q, os.path.join(q, "redis.socket"))], dry_run=False)
    assert acted == []
    assert os.path.exists(q)


def test_reap_stale_budget_caps_removals(tmp_path, monkeypatch):
    """STALE_SWEEP_BUDGET caps stale removals per reap() call; the rest
    stay for the next sweep."""
    from tortoise.embedded_reaper import reap, STALE_SWEEP_BUDGET
    monkeypatch.setattr("tortoise.embedded_reaper.STALE_SWEEP_BUDGET", 5)
    n = 10  # budget + 5; decoupled from the production constant
    dirs = []
    for i in range(n):
        dbdir, sock = _make_dead_pid_dir(tmp_path, name=f"redislite_b{i}")
        _backdate_dir(dbdir)
        dirs.append((dbdir, sock))
    acted = reap([_stale_record(d, s) for d, s in dirs], dry_run=False)
    assert len(acted) == 5
    remaining = [d for d, _ in dirs if os.path.exists(d)]
    assert len(remaining) == 5, "budget cap must leave the remainder"


def test_reap_stale_does_not_call_client_list(tmp_path, monkeypatch):
    """Stale removal never probes CLIENT LIST (no server exists to list)."""
    from tortoise.embedded_reaper import reap
    dbdir, sock = _make_dead_pid_dir(tmp_path)
    _backdate_dir(dbdir)
    def _boom(_s):
        raise AssertionError("CLIENT LIST must not be probed for stale records")
    monkeypatch.setattr("tortoise.embedded_reaper._active_client_count", _boom)
    acted = reap([_stale_record(dbdir, sock)], dry_run=False)
    assert any(a["dbdir"] == str(dbdir) for a in acted)

def test_reap_stale_socket_age_guard_protects_fresh_dir(tmp_path):
    """Dir younger than STALE_SOCKET_MIN_AGE_DEFAULT -> abort (boot window)."""
    from tortoise.embedded_reaper import reap
    dbdir, sock = _make_dead_pid_dir(tmp_path)  # fresh mtime
    acted = reap([_stale_record(dbdir, sock)], dry_run=False)
    assert acted == []
    assert os.path.exists(dbdir)

def test_reap_stale_socket_refuses_non_ephemeral_dir(tmp_path):
    """Containment: a non-tempdir/non-ephemeral dbdir is never removed."""
    from tortoise.embedded_reaper import reap
    dbdir, sock = _make_dead_pid_dir(tmp_path)
    _backdate_dir(dbdir)
    rec = _stale_record(dbdir, sock)
    rec["dbdir"] = "/some/user/path/not-under-tmpdir"  # attacker/crafted
    acted = reap([rec], dry_run=False)
    assert acted == []
    assert os.path.exists(dbdir)

def test_reap_stale_socket_does_not_consume_kill_budget(tmp_path):
    """Stale removals don't increment killed: batch_size=0 still removes."""
    from tortoise.embedded_reaper import reap
    dbdir, sock = _make_dead_pid_dir(tmp_path)
    _backdate_dir(dbdir)
    acted = reap([_stale_record(dbdir, sock)], dry_run=False, batch_size=0)
    assert any(a["dbdir"] == str(dbdir) for a in acted)
    assert not os.path.exists(dbdir)

def test_reap_only_safe_acts_on_stale_socket(tmp_path):
    """only_safe admits stale_socket removal (guards are the safety)."""
    from tortoise.embedded_reaper import reap
    dbdir, sock = _make_dead_pid_dir(tmp_path)
    _backdate_dir(dbdir)
    acted = reap([_stale_record(dbdir, sock)], dry_run=False, only_safe=True)
    assert any(a["dbdir"] == str(dbdir) for a in acted)
    assert not os.path.exists(dbdir)

def test_reap_dead_pid_candidate_log_wording(monkeypatch, caplog):
    """The liveness-gate log now says 'dead pid', not 'dead socket connect'."""
    from tortoise.embedded_reaper import reap
    rec = {"classification": "candidate", "pid": 99999999,
           "socket_path": "/tmp/x.sock", "dbdir": "/tmp/x"}
    reap([rec], dry_run=False)
    assert "dead pid" in caplog.text
    assert "dead socket connect" not in caplog.text


def test_reap_mixed_list_budget_exhaustion_stale_still_processed(tmp_path, monkeypatch):
    """Ordering: interleaved dead-pid candidates (discarded at the
    liveness gate) must NOT block later stale removals, and stale removals
    never consume the kill budget. (Branch-ordering property; the literal
    budget-exhaustion path is pinned by the batch_size=0 test.)"""
    from tortoise.embedded_reaper import reap
    s1, _ = _make_dead_pid_dir(tmp_path, name="redislite_s1")
    s2, _ = _make_dead_pid_dir(tmp_path, name="redislite_s2")
    _backdate_dir(s1); _backdate_dir(s2)
    cand = {"classification": "candidate", "pid": 99999999,
            "socket_path": "/tmp/c.sock", "dbdir": "/tmp/c"}
    acted = reap([cand, _stale_record(s1, os.path.join(s1, "redis.socket")),
                  cand, _stale_record(s2, os.path.join(s2, "redis.socket"))],
                 dry_run=False, batch_size=1)
    # candidate dead-pid is skipped at the liveness gate; both stales removed
    assert len([a for a in acted if a.get("classification") == "stale_socket"]) == 2
    assert not os.path.exists(s1) and not os.path.exists(s2)


def test_stale_dir_reuse_pidfile_rewrite_does_not_refresh_dir_mtime(tmp_path):
    """Pins the documented assumption: an in-place pidfile rewrite updates
    the FILE mtime, not the DIR mtime — the age guard alone does NOT catch
    a reused old dir; guards 3/4 (pidfile re-read + socket probe) carry it."""
    dbdir, sock = _make_dead_pid_dir(tmp_path)
    _backdate_dir(dbdir)
    before = os.stat(dbdir).st_mtime_ns
    time.sleep(0.02)
    Path(dbdir, "redis.pid").write_text(f"{os.getpid()}\n")  # in-place rewrite
    after = os.stat(dbdir).st_mtime_ns
    assert before == after, "dir mtime must not refresh on file rewrite"
    # and the guards must still catch the live server: reap aborts via
    # guard 3 (pidfile now holds the live pytest pid)
    from tortoise.embedded_reaper import reap
    rec = _stale_record(dbdir, sock)
    rec["classification"] = "stale_socket"
    acted = reap([rec], dry_run=False)
    assert acted == []
    assert os.path.exists(dbdir)
```

**Step 2: Run to verify they fail** — `uv run pytest tests/test_reaper.py -q -p no:cacheprovider --timeout=300 -k "stale_socket or dead_pid_candidate or stale_budget or stale_does_not or stale_dir_reuse or mixed_list"` — expect FAIL (stale_socket is skipped today; log wording wrong). (The filter is a subset; the full file runs at Task 6.)

**Step 3: Implement**

```python
STALE_SOCKET_MIN_AGE_DEFAULT = 30  # seconds; boot-window shield for the
# stale-dir removal path (#1383). Mirrors INDEX_PID_MIN_AGE_DEFAULT (30s)
# and the boot-cooldown philosophy; deliberately NOT coupled to
# TORTOISE_REAPER_MIN_UPTIME (live-server boot cooldown) — different
# semantics. (CI's `find -mmin +30` is MINUTES and tmp* only — unrelated.)
STALE_SWEEP_BUDGET = 200  # max stale removals per reap() call (#1383);
# the quarantine sweep uses the same constant as its OWN counter, so one
# sweep can remove up to 2xSTALE_SWEEP_BUDGET total (plan-review cycle 2).
# Bounds the SERIAL stale work vs the 120s SIGALRM. Common case: dead
# sockets answer ECONNREFUSED instantly, so 200 removals cost <1s. Worst
# case (every probe hangs at 0.5s, 3 probes per stale + 1 per quarantine)
# is ~400s > 120s — the SIGALRM may abort mid-sweep, which is idempotent
# (next sweep converges); the budget is the backstop for the common case,
# not a hard latency cap.
STALE_QUARANTINE_SUFFIX = ".reaper-stale-"


def reap(records, dry_run=True, batch_size=None, sigterm_timeout=10.0,
         kill_pacing=KILL_PACING_DEFAULT, only_safe=False) -> list[dict]:
    acted = []
    killed = 0
    stale_removed = 0  # #1383: stale removals budgeted separately from kills
    for record in records:
        classification = record.get("classification")
        if classification == "stale_socket":
            if stale_removed >= STALE_SWEEP_BUDGET:
                continue  # budget exhausted — remainder converges next sweep
            # #1383: dead-pid leftover dir — no process to kill; guarded
            # rmtree (see _remove_stale_socket_dir). Safe under only_safe
            # by construction (the guards re-verify deadness at action time).
            acted_rec = _remove_stale_socket_dir(record, dry_run)
            if acted_rec is not None:
                acted.append(acted_rec)
                stale_removed += 1
            continue
        if classification != "candidate":
            if classification in ("protected", "undetermined"):
                logger.warning(
                    "skipping path-based/non-candidate server: %s",
                    record.get("socket_path"))
            continue
        # Kill budget: bounds process kills only (bgsave-storm semantics).
        # Stale cleanup above is budgeted separately (STALE_SWEEP_BUDGET).
        if batch_size is not None and killed >= batch_size:
            continue  # budget exhausted — skip remaining candidates, keep
            # cleaning stale dirs (they don't kill processes)
        if only_safe and not (record.get("dir_missing")
                              or _is_detached(record.get("pid") or 0)):
            logger.info(
                "concurrent-suite guard: skipping live ephemeral candidate %s",
                record.get("socket_path"))
            continue
        # Liveness-first: never kill a dead PID's leftovers via connect.
        if not record.get("pid") or not _pid_alive(record["pid"]):
            logger.warning("dead pid, skipping: %s", record.get("socket_path"))
            continue
        ...  # CLIENT LIST double-check + _kill + _cleanup_tempdir unchanged
    return acted


def _remove_stale_socket_dir(record: dict, dry_run: bool) -> dict | None:
    """Reap a stale_socket record: guarded rmtree of the leftover dir.

    #1383 — the FIRST reap() action gated on negative evidence, so the
    chain re-verifies deadness at action time (TOCTOU discipline, #1231
    template). Every abort = WARNING + no partial delete (re-verified next
    sweep). The rename-aside + post-rename re-probe convert the worst case
    (delete a live server's data) into 'leave a quarantined dir'.
    """
    dbdir = record.get("dbdir")
    socket_path = record.get("socket_path")
    if not dbdir or not socket_path:
        logger.warning("stale_socket record missing dbdir/socket, skipping")
        return None
    dbdir_real = os.path.realpath(dbdir)
    # Re-entry guard (plan-review P1): a dir that ALREADY carries the
    # quarantine suffix is reaper-owned — handled exclusively by
    # _sweep_quarantine_dirs; never rename it a second time.
    if STALE_QUARANTINE_SUFFIX in os.path.basename(dbdir_real):
        logger.warning("quarantine dir passed to stale action, skipping: %s",
                       dbdir_real)
        return None
    # Guard 1: already gone
    if not os.path.exists(dbdir_real):
        logger.info("stale dir already gone: %s", dbdir_real)
        return record
    # Guard 2: containment — reap() is semi-public; never delete outside
    # the ephemeral tempdir tree (classification already enforces this for
    # discover() records; guard against crafted/errant direct records).
    if not _is_ephemeral_dir(dbdir_real, _real_gettempdir()):
        logger.warning("stale dir outside ephemeral tempdir, skipping: %s",
                       dbdir_real)
        return None
    # Guard 3: re-read the pidfile — a now-live pid means respawn/pid-reuse
    pid = None
    pidfile = os.path.join(dbdir_real, "redis.pid")
    try:
        pid = int(Path(pidfile).read_text().strip())
    except (OSError, ValueError):
        pid = None
    if pid is not None and _pid_alive(pid):
        logger.warning("stale dir pidfile now alive (%s), skipping: %s",
                       pid, dbdir_real)
        return None
    # Guard 4: re-probe the socket with the SHORT timeout. Only
    # 'dead' (ECONNREFUSED — socket file exists, no listener) proceeds;
    # 'missing' (mid-startup), 'alive', 'undetermined' all fail closed.
    probe = _probe_socket(socket_path, timeout=PROBE_SOCKET_TIMEOUT)
    if probe != "dead":
        logger.warning("stale dir socket probe %s, skipping: %s",
                       probe, dbdir_real)
        return None
    # Guard 5: mtime age guard (boot window) + re-stat right before rename
    # (the second stat narrows the create-during-guard-chain window)
    try:
        age = time.time() - os.stat(dbdir_real).st_mtime
    except OSError:
        return None
    if age < STALE_SOCKET_MIN_AGE_DEFAULT:
        logger.info("stale dir too young (%.1fs), skipping: %s", age, dbdir_real)
        return None
    try:  # re-stat immediately before the irreversible rename
        age = time.time() - os.stat(dbdir_real).st_mtime
        if age < STALE_SOCKET_MIN_AGE_DEFAULT:
            logger.info("stale dir mtime changed mid-chain, skipping: %s",
                        dbdir_real)
            return None
    except OSError:
        return None
    if dry_run:
        logger.warning("[DRY-RUN] would remove stale socket dir %s", dbdir_real)
        return record
    # Guard 6/7: atomic rename-aside then re-verify BEFORE the irreversible
    # rmtree. The renamed dir is the quarantine — if re-verification fails,
    # the dir stays for operator inspection / next-sweep convergence.
    renamed = dbdir_real + STALE_QUARANTINE_SUFFIX + str(time.time_ns())
    try:
        os.rename(dbdir_real, renamed)
    except OSError as exc:
        logger.warning("stale dir rename failed (%s), skipping: %s",
                       exc, dbdir_real)
        return None
    renamed_sock = os.path.join(renamed, "redis.socket")
    if not os.path.exists(renamed_sock):
        logger.warning("quarantined socket vanished, leaving dir: %s", renamed)
        return None  # leave quarantine (next sweep re-probes)
    if _probe_socket(renamed_sock, timeout=PROBE_SOCKET_TIMEOUT) != "dead":
        logger.warning("quarantined socket live, leaving dir: %s", renamed)
        return None  # live server in the moved dir — do NOT delete
    # Guard 8: pidfile written during the window (backlog-full ECONNREFUSED
    # hardening — a live server that refuses connects can still write its pid)
    try:
        moved_pid = int(Path(os.path.join(renamed, "redis.pid")).read_text().strip())
    except (OSError, ValueError):
        moved_pid = None
    if moved_pid is not None and _pid_alive(moved_pid):
        logger.warning("quarantined pidfile now alive (%s), leaving dir: %s",
                       moved_pid, renamed)
        return None
    _cleanup_tempdir(renamed)
    if os.path.exists(renamed):
        logger.warning("partial rmtree leftover, will re-probe next sweep: %s",
                       renamed)
    logger.warning("removed stale socket dir %s (was %s)", renamed, dbdir_real)
    # Acted record: `dbdir` stays the ORIGINAL (pre-rename) path so all
    # existing acted assertions hold; the renamed/quarantined path rides in
    # `removed_dir` for --json correlation (plan-review cycle 2).
    return {**record, "removed_dir": renamed}
```

**Step 4: Run to verify they pass** — same command; expect PASS.

**Step 5: Commit** — message: `fix(1383): reap() stale_socket guarded-rmtree action (9 guards, quarantine rename-aside)`.

## Task 4: Pipeline integration — `_run_sweep` includes stale_socket; quarantine resweep

**Intent:** Wire the stale action into the sweep: `phase1_probe` resolves stale-pid records (dead→stale_socket, alive→candidate with real pid, missing/undetermined→undetermined) and reap() receives both classes; partial-rmtree quarantine leftovers converge on later sweeps.
**Acceptance:** `_run_sweep` reaps stale_socket records end-to-end; `_sweep_quarantine_dirs` removes dead quarantine leftovers and WARNs on live ones; `phase1_probe("missing")` → undetermined (not stale_socket); existing `test_run_sweep_includes_stale_pid_files` green.
**Files:**
- Modify: `tortoise/embedded_reaper.py` (`phase1_probe` ~897-923, `_run_sweep` ~1161-1188, new `_sweep_quarantine_dirs`)
- Test: `tests/test_reaper.py`

**Step 1: Write the failing tests**

```python
# tests/test_reaper.py
def test_discover_skips_quarantine_dirs(tmp_path):
    """Plan-review P1: discover pass 2 must skip *.reaper-stale-* dirs —
    they are reaper-owned and handled exclusively by the quarantine sweep.
    Without the skip, a guard-7-preserved LIVE server in a quarantine dir
    would classify 'candidate' next sweep and be killed."""
    from tortoise.embedded_reaper import discover
    dbdir, sock = _make_dead_pid_dir(tmp_path)
    _backdate_dir(dbdir)
    q = str(dbdir) + ".reaper-stale-777"
    os.rename(dbdir, q)
    with monkeypatch_tempdir(tmp_path):
        found = discover()
        assert all(str(dbdir) not in s.get("dbdir", "") for s in found)
        assert all(".reaper-stale-" not in s.get("dbdir", "") for s in found)


def test_phase1_probe_missing_socket_undetermined(tmp_path):
    """#1383: a vanished socket (mid-startup) fails closed to
    'undetermined' — never 'stale_socket' (which licenses removal)."""
    from tortoise.embedded_reaper import phase1_probe
    dbdir = tmp_path / "redislite_missing"
    dbdir.mkdir()
    rec = {"pid": 99999999, "socket_path": os.path.realpath(
        str(dbdir / "redis.socket")), "dbdir": os.path.realpath(str(dbdir)),
        "classification": "candidate"}
    assert phase1_probe(rec)["classification"] == "undetermined"

def test_reap_phase1_stale_socket_end_to_end(tmp_path):
    """discover() -> phase1_probe -> reap() removes a dead-pid leftover dir."""
    from tortoise.embedded_reaper import discover, phase1_probe, reap
    dbdir, sock = _make_dead_pid_dir(tmp_path)
    _backdate_dir(dbdir)
    with monkeypatch_tempdir(tmp_path):
        found = discover()
        matches = [s for s in found if str(dbdir) in s.get("dbdir", "")]
        assert matches and matches[0]["classification"] == "stale_socket"
        resolved = phase1_probe(matches[0])
        assert resolved["classification"] == "stale_socket"
        acted = reap([resolved], dry_run=False)
        assert any(a["dbdir"] == str(dbdir) for a in acted)
        assert not os.path.exists(dbdir)

def test_run_sweep_removes_stale_socket_record(tmp_path, monkeypatch):
    """_run_sweep (the conftest/CLI entry) reaps stale_socket records.
    sweep_pid_files=False so the test never touches ~/.tortoise; wrapped in
    monkeypatch_tempdir so the quarantine sweep stays inside the test tree."""
    from tortoise.embedded_reaper import _run_sweep
    dbdir, sock = _make_dead_pid_dir(tmp_path)
    _backdate_dir(dbdir)
    monkeypatch.setenv("TORTOISE_INDEX_LOCK_DIR", str(tmp_path / "locks"))
    monkeypatch.setattr(
        "tortoise.embedded_reaper.discover",
        lambda jobs=1: [_stale_record(dbdir, sock)])
    with monkeypatch_tempdir(tmp_path):
        acted = _run_sweep(dry_run=False, batch_size=None, only_safe=True,
                           sweep_pid_files=False)
    assert any(a.get("dbdir") == str(dbdir) for a in acted)
    assert not os.path.exists(dbdir)


def test_run_sweep_live_quarantine_not_killed(tmp_path):
    """Plan-review P1 pin: a guard-7 live quarantine dir must NOT be killed
    or deleted by a full _run_sweep — the quarantine is reaper-owned."""
    from tortoise.embedded_reaper import _run_sweep, discover
    dbdir, sock = _make_dead_pid_dir(tmp_path)
    _backdate_dir(dbdir)
    q = str(dbdir) + ".reaper-stale-888"
    os.rename(dbdir, q)
    # A live listener bound on the moved socket (server moved with the dir)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(os.path.join(q, "redis.socket")); srv.listen(1)
    try:
        with monkeypatch_tempdir(tmp_path):
            acted = _run_sweep(dry_run=False, batch_size=None, only_safe=True,
                               sweep_pid_files=False)
        assert os.path.exists(q), "live quarantine must be kept"
        assert not any(a.get("dbdir") == str(q) for a in acted)
    finally:
        srv.close()


def test_run_sweep_pass1_live_server_in_quarantine_not_killed(tmp_path, monkeypatch):
    """Cycle-2 P2-2 pin: a pgrep-able LIVE server whose dir was renamed to
    quarantine is re-discovered by pass 1 at its ORIGINAL (now-gone)
    cmdline path. With the known_pid pass-through it classifies 'candidate'
    — safety depends on reap()'s CLIENT LIST failing closed on the moved
    socket (FileNotFoundError). Pins the interplay so a future
    retry-widening can't convert it into a wrongful kill."""
    from tortoise.embedded_reaper import _run_sweep, _classify_dir
    dbdir, sock = _make_dead_pid_dir(tmp_path)
    _backdate_dir(dbdir)
    q = str(dbdir) + ".reaper-stale-900"
    os.rename(dbdir, q)
    # A live listener on the MOVED socket (server moved with its dir)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(os.path.join(q, "redis.socket")); srv.listen(1)
    live_pid = 42424242
    # _pid_alive is real in this test — make ONLY the fake live pid 'alive'
    monkeypatch.setattr("tortoise.embedded_reaper._pid_alive",
                        lambda pid: pid == live_pid)
    monkeypatch.setattr("tortoise.embedded_reaper._PROC_INFO_CACHE", {
        live_pid: {"cmdline": f"/x/redis-server unixsocket:{sock}",
                   "etime": "01:00:00"}})
    def _fake_socket_dir(pid):
        return str(dbdir)  # original (gone) path from the cmdline
    monkeypatch.setattr("tortoise.embedded_reaper._socket_dir_from_cmdline",
                        _fake_socket_dir)
    monkeypatch.setattr(
        "tortoise.embedded_reaper._pgrep_redis_servers", lambda: [live_pid])
    rec = _classify_dir(str(dbdir), sock, known_pid=live_pid)
    assert rec["classification"] == "candidate"  # the dangerous shape
    try:
        with monkeypatch_tempdir(tmp_path):
            acted = _run_sweep(dry_run=False, batch_size=None, only_safe=False,
                               sweep_pid_files=False)
        # reap()'s CLIENT LIST fails closed on the moved socket -> no kill,
        # and the quarantine sweep keeps the live dir (live socket probe)
        assert os.path.exists(q), "live quarantine must survive the sweep"
        assert not any(a.get("pid") == live_pid for a in acted)
    finally:
        srv.close()


def test_run_sweep_combined_quarantine_and_pid_files(tmp_path, monkeypatch):
    """One _run_sweep performs BOTH the quarantine sweep and the index-pid
    sweep; both classes appear in acted (regression guard)."""
    from tortoise.embedded_reaper import _run_sweep
    dbdir, sock = _make_dead_pid_dir(tmp_path)
    _backdate_dir(dbdir)
    q = str(dbdir) + ".reaper-stale-999"
    os.rename(dbdir, q)  # dead quarantine -> removed
    locks = tmp_path / "locks"
    locks.mkdir()
    stale_pid = locks / "index-crashed.pid"
    stale_pid.write_text("999999999 0\n")
    old = time.time() - 120
    os.utime(stale_pid, (old, old))
    monkeypatch.setenv("TORTOISE_INDEX_LOCK_DIR", str(locks))
    monkeypatch.setattr("tortoise.embedded_reaper.discover", lambda jobs=1: [])
    with monkeypatch_tempdir(tmp_path):
        acted = _run_sweep(dry_run=False, batch_size=None, only_safe=True)
    classes = {a.get("classification") for a in acted}
    assert "stale_quarantine" in classes
    assert "stale_pid_file" in classes
    assert not os.path.exists(q)
    assert not stale_pid.exists()


def test_cli_json_emits_stale_socket_shape(tmp_path, monkeypatch):
    """S9: the CLI --json contract carries the new classification + path
    keys for stale actions. Reaper lock monkeypatched so a dev-box cron
    reaper can't make the test non-hermetic (cycle 2)."""
    from tortoise.embedded_reaper import main
    dbdir, sock = _make_dead_pid_dir(tmp_path)
    _backdate_dir(dbdir)
    monkeypatch.setenv("TORTOISE_INDEX_LOCK_DIR", str(tmp_path / "locks"))
    monkeypatch.setattr("tortoise.embedded_reaper._ReaperLock.acquire",
                        lambda self: True)
    monkeypatch.setattr("tortoise.embedded_reaper._ReaperLock.release",
                        lambda self: None)
    monkeypatch.setattr(
        "tortoise.embedded_reaper.discover",
        lambda jobs=1: [_stale_record(dbdir, sock)])
    import io
    import json as _json
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    with monkeypatch_tempdir(tmp_path):
        rc = main(["--no-dry-run", "--json", "--timeout", "60"])
    assert rc == 0
    data = _json.loads(out.getvalue())
    stale = [d for d in data if d.get("classification") == "stale_socket"]
    assert stale, "stale_socket missing from --json output"
    assert stale[0].get("removed_dir"), \
        "stale acted record must carry removed_dir"
    assert not os.path.exists(dbdir)


def test_sweep_quarantine_dirs_dry_run_reports_without_mutating(tmp_path):
    """Quarantine dry-run reports would-remove without touching the dir."""
    from tortoise.embedded_reaper import _sweep_quarantine_dirs
    dbdir, sock = _make_dead_pid_dir(tmp_path)
    q = str(dbdir) + ".reaper-stale-123"
    os.rename(dbdir, q)
    with monkeypatch_tempdir(tmp_path):
        removed = _sweep_quarantine_dirs(dry_run=True)
    assert q in removed
    assert os.path.exists(q), "dry-run must not mutate"

def test_sweep_quarantine_dirs_removes_dead_leftover(tmp_path):
    """Partial-rmtree / respawn leftovers under *.reaper-stale-* converge."""
    from tortoise.embedded_reaper import _sweep_quarantine_dirs
    dbdir, sock = _make_dead_pid_dir(tmp_path)
    q = str(dbdir) + ".reaper-stale-123"
    os.rename(dbdir, q)  # simulate a quarantine from a prior sweep
    with monkeypatch_tempdir(tmp_path):
        removed = _sweep_quarantine_dirs(dry_run=False)
        assert q in removed
        assert not os.path.exists(q)

def test_sweep_quarantine_dirs_keeps_live_leftover(tmp_path):
    """A quarantine whose socket is live is WARNed and kept (forensic)."""
    from tortoise.embedded_reaper import _sweep_quarantine_dirs
    dbdir, sock = _make_dead_pid_dir(tmp_path)
    q = str(dbdir) + ".reaper-stale-456"
    os.rename(dbdir, q)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(os.path.join(q, "redis.socket")); srv.listen(1)
    try:
        with monkeypatch_tempdir(tmp_path):
            removed = _sweep_quarantine_dirs(dry_run=False)
            assert q not in removed
            assert os.path.exists(q)
    finally:
        srv.close()


def test_sweep_quarantine_dirs_skips_symlink(tmp_path):
    """Symlinked *.reaper-stale-* entries are never probed or removed
    (mirror discover pass 2; cycle-2 P2-4)."""
    from tortoise.embedded_reaper import _sweep_quarantine_dirs
    dbdir, sock = _make_dead_pid_dir(tmp_path)
    link = tmp_path / "redislite_x.reaper-stale-999"
    link.symlink_to(dbdir, target_is_directory=True)
    with monkeypatch_tempdir(tmp_path):
        removed = _sweep_quarantine_dirs(dry_run=False)
    assert str(link) not in removed
    assert os.path.islink(link)
    assert os.path.exists(dbdir)


def test_sweep_quarantine_dirs_removes_socketless_partial(tmp_path):
    """A partial-rmtree quarantine that lost its socket (SIGALRM interrupt)
    converges: rmtree'd once its dir mtime passes the age gate."""
    from tortoise.embedded_reaper import _sweep_quarantine_dirs
    dbdir, sock = _make_dead_pid_dir(tmp_path)
    q = str(dbdir) + ".reaper-stale-654"
    os.rename(dbdir, q)
    os.remove(os.path.join(q, "redis.socket"))  # socket gone (partial rmtree)
    old = time.time() - 120
    os.utime(q, (old, old))  # aged shell
    with monkeypatch_tempdir(tmp_path):
        removed = _sweep_quarantine_dirs(dry_run=False)
    assert q in removed
    assert not os.path.exists(q)
```

**Step 2: Run to verify they fail** — `uv run pytest tests/test_reaper.py -q -p no:cacheprovider --timeout=300 -k "phase1_probe_missing or end_to_end or run_sweep_removes or sweep_quarantine or skips_quarantine or cli_json"`. (The filter is a subset; the full file runs at Task 6.)

**Step 3: Implement**

```python
def phase1_probe(record: dict) -> dict:
    """Ordered discovery: resolve stale-PID sockets via raw probe FIRST.

    #1383 contract update: only a confirmed 'dead' socket (ECONNREFUSED)
    classifies 'stale_socket'; 'missing' (vanished socket — mid-startup)
    and 'undetermined' fail closed. An 'alive' socket upgrades to
    'candidate' with the real pid derived (live orphan — kill semantics).
    A record whose pid became alive since discovery is reclassified
    'candidate' (a live server must never stay stale_socket).
    """
    if record.get("pid") and _pid_alive(record["pid"]):
        if record.get("classification") != "candidate":
            record["classification"] = "candidate"
        return record
    if not record.get("socket_path"):
        return record
    probe = _probe_socket(record["socket_path"])
    if probe == "dead":
        record["classification"] = "stale_socket"
    elif probe == "alive":
        real_pid = _derive_real_pid(record["socket_path"], record.get("pid"))
        if real_pid:
            record["pid"] = real_pid
            record["classification"] = "candidate"
        else:
            record["classification"] = "undetermined"
    else:  # missing / undetermined
        record["classification"] = "undetermined"
    return record


def _sweep_quarantine_dirs(dry_run: bool = False,
                           budget: int = STALE_SWEEP_BUDGET) -> list[str]:
    """Re-probe *.reaper-stale-* quarantine leftovers and remove dead ones.

    #1383 convergence: a partial-rmtree or respawn-during-rename leaves a
    renamed dir. discover() pass 2 SKIPS quarantine dirs (reaper-owned —
    plan-review P1), so this pass is their only handler: re-probe the
    moved socket (a server moved with its dir retains its socket inode, so
    the probe is authoritative) and remove only dead ones. Same budget
    CONSTANT as reap()'s stale branch but a SEPARATE counter — one sweep
    can remove up to 2xSTALE_SWEEP_BUDGET (plan-review cycle 2: the
    'shared budget' claim was wrong). Gated on the same tempdir walk-size
    guard; symlinked entries are skipped (mirror discover pass 2).
    """
    tmpdir = _real_gettempdir()
    try:
        entry_count = os.stat(tmpdir).st_nlink
    except OSError:
        entry_count = 0
    if entry_count > 5000:  # same perf guard as discover() pass 2
        return []
    removed = []
    try:
        entries = list(os.scandir(tmpdir))
    except (PermissionError, OSError):
        return removed
    for entry in entries:
        if not entry.is_dir() or entry.is_symlink():
            continue  # symlink safety mirrors discover pass 2 (cycle 2)
        if STALE_QUARANTINE_SUFFIX not in entry.name:
            continue
        if len(removed) >= budget:
            break
        q = entry.path
        qsock = os.path.join(q, "redis.socket")
        # Guard-8 equivalent (cycle-3 P1): a LIVE backlog-full server
        # answers ECONNREFUSED ('dead') — the moved pidfile is the
        # discriminator. A guard-8-preserved quarantine left by reap() in
        # the SAME sweep must never be rmtree'd here.
        try:
            qpid = int(Path(os.path.join(q, "redis.pid")).read_text().strip())
        except (OSError, ValueError):
            qpid = None
        if qpid is not None and _pid_alive(qpid):
            logger.warning("quarantined dir pidfile live (%s), leaving: %s",
                           qpid, q)
            continue
        if not os.path.exists(qsock):
            # Partial-rmtree shell (SIGALRM interrupt deleted the socket
            # first): only a server that unlinked its socket leaves a
            # socket-less quarantine — and an unlinked socket serves
            # nobody. Remove once aged (mtime guard) so the shell
            # converges instead of leaking (plan-review P1).
            try:
                qage = time.time() - os.stat(q).st_mtime
            except OSError:
                continue
            if qage < STALE_SOCKET_MIN_AGE_DEFAULT:
                continue
            if dry_run:
                logger.warning("[DRY-RUN] would remove quarantined dir %s", q)
                removed.append(q)
                continue
            _cleanup_tempdir(q)
            removed.append(q)
            logger.warning("removed socket-less quarantined dir %s", q)
            continue
        if _probe_socket(qsock, timeout=PROBE_SOCKET_TIMEOUT) != "dead":
            logger.warning("quarantined dir socket live, leaving: %s", q)
            continue
        if dry_run:
            logger.warning("[DRY-RUN] would remove quarantined dir %s", q)
            removed.append(q)
            continue
        _cleanup_tempdir(q)
        removed.append(q)
        logger.warning("removed quarantined dir %s", q)
    return removed


# #1383 plan-review P1: discover pass 2 must SKIP reaper-owned quarantine
# dirs. Apply this change to `_discover_from_live`'s pass-2 loop
# (REQUIRED — the tests test_discover_skips_quarantine_dirs and
# test_run_sweep_live_quarantine_not_killed depend on it):
#
#     for entry in entries:
#         if not entry.is_dir():
#             continue
#         if STALE_QUARANTINE_SUFFIX in entry.name:
#             continue  # reaper-owned quarantine — quarantine sweep handles it
#         ...
#
# Without it, `redislite_x.reaper-stale-*` matches EPHEMERAL_PREFIXES via
# startswith -> registry path -> a guard-7-preserved LIVE server in the
# moved dir classifies 'candidate' -> the next sweep's candidate path
# KILLS it (the exact delete the guard chain exists to prevent).
#
# #1383 S9 (CLI --json contract): apply this change to main()'s emitter
# (REQUIRED — test_cli_json_emits_stale_socket_shape depends on it):
#
#     if args.json:
#         print(_json.dumps([
#             {"pid": r.get("pid"), "socket_path": r.get("socket_path"),
#              "pid_file": r.get("pid_file"),
#              "dbdir": r.get("dbdir"),
#              "removed_dir": r.get("removed_dir"),
#              "quarantine_dir": r.get("quarantine_dir"),
#              "classification": r.get("classification")}
#             for r in acted
#         ]))
#
# Without the keys, a `stale_quarantine` acted record emits
# {pid: null, socket_path: null, pid_file: null, ...} — the quarantine
# path is dropped entirely (plan-review P2).


def _run_sweep(dry_run: bool, batch_size: int | None, only_safe: bool = False,
               jobs: int = 8, kill_pacing: float = KILL_PACING_DEFAULT,
               sweep_pid_files: bool = True) -> list[dict]:
    """Discover + classify + reap; return acted-upon records.

    NOTE: the reaper singleton lock is held by main() (CLI); direct callers
    (tests, conftest session hygiene) run unlocked — pre-existing contract,
    unchanged by #1383.
    """
    records = discover(jobs=jobs)
    # #1383: reapable classes are candidate (live orphan -> kill) and
    # stale_socket (dead-pid leftover dir -> guarded rmtree). Phase 1
    # resolves stale-pid records before any action.
    reapables = [r for r in records
                 if r["classification"] in ("candidate", "stale_socket")]
    resolved = [phase1_probe(r) for r in reapables]
    acted = reap(resolved, dry_run=dry_run, batch_size=batch_size,
                 kill_pacing=kill_pacing, only_safe=only_safe)
    # #1383: quarantine convergence (partial-rmtree/respawn leftovers)
    try:
        for q in _sweep_quarantine_dirs(dry_run=dry_run):
            acted.append({"pid": None, "quarantine_dir": q,
                          "classification": "stale_quarantine"})
    except Exception as exc:  # never fail the sweep over hygiene
        logger.warning("quarantine sweep failed: %s", exc)
    if sweep_pid_files:
        ...  # unchanged #1231 index-pid sweep
    return acted
```

**Step 4: Run to verify they pass.**

**Step 5: Commit** — message: `fix(1383): _run_sweep reaps stale_socket; quarantine resweep convergence`.

## Task 5: Bounded probe retry — `_raw_resp_client_list` timeout-phase retry (read or connect)

**Intent:** Stop the load-sensitive fail-closed skip (indicator b): a single 0.5s timeout must not strand a live orphan — one bounded retry (2×0.5s) before the fail-closed None, while reliable verdicts (refused/missing) never retry. ("Read-timeout-only" in the scoping was refined to read-OR-connect timeout at plan-review: a full connect backlog is equally load-sensitive.)
**Acceptance:** `_raw_resp_client_list` retries exactly once on a `socket.timeout` in the read or connect phase; `ConnectionRefusedError`/`FileNotFoundError`/other OSErrors return immediately; exhausted retries return None; existing fail-closed tests green.
**Files:**
- Modify: `tortoise/embedded_reaper.py` (`_raw_resp_client_list` ~449-474, new `_raw_resp_probe_once`)
- Test: `tests/test_reaper.py`

**Step 1: Write the failing tests**

```python
# tests/test_reaper.py
class _DelayedRespServer:
    """Threaded fake unix-socket server with per-connection handling.
    Modes: first_delay (bool) HOLDS conn 1 until the client's read times
    out and closes (deterministic — no sleep-vs-timeout margin), then
    serves conn 2 immediately; never_reply (bool) holds every connection."""
    def __init__(self, payload=b"$11\r\nid=1 age=5\n\r\n",
                 first_delay=True, never_reply=False):
        self.payload = payload
        self.first_delay = first_delay
        self.never_reply = never_reply
        self.accepts = 0
        self._srv = None
        self._tmp = None
        self._lock = threading.Lock()

    def __enter__(self):
        import tempfile as _tf
        self._tmp = _tf.mkdtemp(prefix="reaper_probe_")
        self.sock_path = os.path.join(self._tmp, "delay.sock")
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._srv.bind(self.sock_path)
        self._srv.listen(4)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self

    def wait_accepts(self, n, timeout=3.0):
        """Bounded poll for the accept counter (no timing assumptions)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if self.accepts >= n:
                    return
            time.sleep(0.02)
        with self._lock:
            raise AssertionError(
                f"expected {n} accepts, got {self.accepts}")

    def _serve(self):
        try:
            while True:
                conn, _ = self._srv.accept()
                with self._lock:
                    self.accepts += 1
                    n = self.accepts
                threading.Thread(
                    target=self._handle, args=(conn, n), daemon=True).start()
        except OSError:
            pass

    def _handle(self, conn, n):
        try:
            conn.recv(4096)  # consume CLIENT LIST
            if self.never_reply or (n == 1 and self.first_delay):
                # Hold conn 1 until the client's read-times-out and closes
                # (recv returns b"" on client close) — DETERMINISTIC first
                # attempt timeout regardless of scheduling. Then serve conn 2
                # immediately (no timing margins; plan-review P1).
                while conn.recv(4096):
                    time.sleep(0.05)
                return
            conn.sendall(self.payload)
        except OSError:
            pass  # client closed first — the hold produced the timeout
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def __exit__(self, *exc):
        try:
            self._srv.close()
        except OSError:
            pass
        self._thread.join(timeout=5)
        import shutil as _sh
        _sh.rmtree(self._tmp, ignore_errors=True)
        return False


def test_raw_resp_client_list_retries_on_read_timeout():
    """First read times out (conn 1 held, deterministic), retry succeeds
    -> clients parsed, exactly 2 connections (bounded retry, indicator b)."""
    from tortoise.embedded_reaper import _raw_resp_client_list
    with _DelayedRespServer(first_delay=True, never_reply=False) as srv:
        result = _raw_resp_client_list(srv.sock_path)
        assert result is not None
        assert any(c.get("id") == "1" for c in result)
        # The client parses the payload only after conn-2's handler thread
        # sent it — which runs only after accept() #2 incremented the
        # counter (payload receipt implies accept #2; bounded poll, no
        # timing assumption).
        srv.wait_accepts(2)

def test_raw_resp_client_list_retries_exhausted_returns_none():
    """never_reply: both attempts time out -> None (fail closed)."""
    from tortoise.embedded_reaper import _raw_resp_client_list
    with _DelayedRespServer(first_delay=True, never_reply=True) as srv:
        result = _raw_resp_client_list(srv.sock_path)
        assert result is None
        srv.wait_accepts(2)  # both attempts were made, bounded

def test_raw_resp_client_list_does_not_retry_dead_socket():
    """ConnectionRefusedError is a reliable verdict — no retry, None."""
    from tortoise.embedded_reaper import _raw_resp_client_list
    d = tempfile.mkdtemp(prefix="reaper_probe_")
    try:
        sp = os.path.join(d, "dead.sock")
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(sp); s.close()  # dead socket file
        assert _raw_resp_client_list(sp) is None
    finally:
        shutil.rmtree(d, ignore_errors=True)

def test_raw_resp_client_list_does_not_retry_missing_socket():
    """FileNotFoundError (mid-startup) — no retry, None."""
    from tortoise.embedded_reaper import _raw_resp_client_list
    assert _raw_resp_client_list("/nonexistent/reaper-missing.sock") is None
```

**Step 2: Run to verify they fail** — `uv run pytest tests/test_reaper.py -q -p no:cacheprovider --timeout=300 -k "raw_resp_client_list"`.

**Step 3: Implement**

```python
RAW_RESP_PROBE_ATTEMPTS = 2  # bounded retry before fail-closed skip (#1383)


def _raw_resp_client_list(socket_path: str) -> list[dict] | None:
    """CLIENT LIST via raw RESP over a plain unix socket.

    Non-destructive by construction (issue #849). #1383: bounded retry —
    RAW_RESP_PROBE_ATTEMPTS attempts of PROBE_SOCKET_TIMEOUT each, but only
    when the attempt failed on a socket.timeout in the READ or CONNECT
    phase (a loaded single-threaded server queuing CLIENT LIST / filling
    its backlog — both are load-sensitive, both retryable). Reliable
    verdicts (ECONNREFUSED / missing / other errors) never retry.
    Exhausted -> None -> callers fail closed unchanged.
    """
    for attempt in range(RAW_RESP_PROBE_ATTEMPTS):
        clients, status = _raw_resp_probe_once(socket_path)
        if status != "timeout":  # ok, refused, missing, error
            return clients
        # read-phase timeout: retry (bounded)
    return None


def _raw_resp_probe_once(socket_path: str) -> tuple[list[dict] | None, str]:
    """One probe attempt; returns (parsed clients or None, status).
    status ∈ {ok, refused, missing, timeout, error} — 'timeout' (read OR
    connect phase) is the only retryable outcome (socket.timeout ⊂ OSError
    on 3.10+, so it must be caught BEFORE the generic OSError handler).
    The socket is ALWAYS closed (outer finally — the connect-phase early
    returns must not leak FDs; plan-review cycle 2 P1)."""
    s = None
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(PROBE_SOCKET_TIMEOUT)
        try:
            s.connect(socket_path)
        except ConnectionRefusedError:
            return None, "refused"
        except FileNotFoundError:
            return None, "missing"
        except socket.timeout:
            return None, "timeout"  # connect-phase timeout — retryable too
        except OSError:
            return None, "error"
        try:
            s.sendall(b"*2\r\n$6\r\nCLIENT\r\n$4\r\nLIST\r\n")
            raw = _read_resp_reply(s)
        except socket.timeout:
            return None, "timeout"  # read-phase timeout — the retry target
        except OSError:
            return None, "error"
    finally:
        if s is not None:
            try:
                s.close()
            except OSError:
                pass
    if raw is None:
        return None, "error"  # malformed/truncated reply — not timing
    return _parse_client_list(raw), "ok"
```

**Step 4: Run to verify they pass.**

**Step 5: Commit** — message: `fix(1383): bounded timeout-phase retry in raw-RESP CLIENT LIST probe`.

## Task 6: Docs + verification + changelog

**Intent:** Keep the module's documented contract truthful (module docstring, `reap()` docstring, cron doc) and record the `--json` output-shape change for operators; run the mandated verification suites to green.
**Acceptance:** `docs/infra/embedded-reaper-cron.md` documents the age-gated stale-dir removal (fires in all modes incl. `--only-safe`); module + reap docstrings updated; CHANGELOG notes `--json` may emit `classification: "stale_socket"` (dead/None pid); `tests/test_reaper.py` and `tests/test_embedded_concurrency.py -k chaos` green.
**Files:**
- Modify: `tortoise/embedded_reaper.py` (module docstring ~3-30, reap docstring ~925-946), `docs/infra/embedded-reaper-cron.md`, `CHANGELOG.md` (if present)
- Test: full suites below

**Step 1: Update docs**
- Module docstring: add `- registry pidfile pid dead -> stale_socket (dead-pid leftover dir — guarded rmtree; never a killable 'candidate')` to the classification list, and note stale_socket removal happens without CLIENT LIST (no server exists to list).
- `reap()` docstring: document the stale_socket branch (guarded rmtree, quarantine rename-aside, mode-independent, only_safe-admitted).
- `docs/infra/embedded-reaper-cron.md` Safety section: one bullet — `--no-dry-run` also rmtrees age-gated (≥30s, ECONNREFUSED-verified, rename-aside quarantined) dead-pid leftover dirs, in all modes including `--only-safe`; dry-run reports them; `--json` now carries `dbdir`/`quarantine_dir` keys.
- CHANGELOG: `--json` may emit `"classification": "stale_socket"` (dead or None pid; `dbdir` stays the original path, `removed_dir` carries the renamed/quarantined path) and `"stale_quarantine"` entries (with `quarantine_dir`); the emitter gains `removed_dir`/`dbdir`/`quarantine_dir` keys.

**Step 2: Run verification**
```bash
uv run pytest tests/test_reaper.py -q -p no:cacheprovider --timeout=300
uv run pytest tests/test_embedded_concurrency.py -q -p no:cacheprovider --timeout=300 -k chaos
```
Expected: all green. (Environment: heavy load + possible `~/.tortoise/.reaper.lock` contention → "reaper already running" failures are environmental; retry.)

**Step 3: Commit** — message: `docs(1383): reaper stale_socket semantics — cron safety, module docstring, changelog`.

## Runtime Prerequisites

- Python 3.12+ (repo `.python-version`); stdlib only — no lockfile drift, no new deps.
- FalkorDBLite embedded (no Docker) for the redislite-spawning tests.
- `uv sync --extra test --extra embeddings` before the first run.

## Acceptance Criteria (from issue O/I/T)

- **(a)** Dead-pid leftover dirs classify `'stale_socket'` (not `'candidate'`) — Task 2; verified by `test_discover_classifies_dead_pid_dir_stale_socket` + end-to-end Task 4 test.
- **(b)** `reap()` no longer strands live-but-unprobeable candidates on a single 0.5s probe failure — Task 5; verified by `test_raw_resp_client_list_retries_on_read_timeout` (2 connections, parsed result).
- **(c)** `tests/test_reaper.py` + `tests/test_embedded_concurrency.py` stay green — Task 6.
- **Regression:** `only_safe` guard behavior for LIVE candidates unchanged — existing `test_reap_only_safe_skips_live_ephemeral_without_killing` / `test_reap_only_safe_reaps_detached_orphan` / `test_reap_only_safe_protects_live_parented_server` must stay green unchanged.
- **Non-regression:** the #1005/#1115 concurrency contract (live ephemeral candidates never killed under only_safe; detached orphans reaped) — same three tests + chaos suite.

<!-- plan-review: cycles=3, status=clean, version=2.3.0 -->
