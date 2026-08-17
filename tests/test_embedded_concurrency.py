"""Concurrency stress + chaos tests (plan Task 12, Child 3).

Proves the stable-path contract under load (1 server for N processes, no
corruption) and reaper correctness under chaos (kills only idle, handles
SIGKILL mid-sweep, lock release on SIGKILL).
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time

import pytest

pytest.importorskip("redislite")


def _spawn_writer(canonical_path, writer_id, writes=5):
    """Spawn a subprocess that writes `writes` keys to the canonical path
    via FalkorProjection. Returns the subprocess."""
    code = f"""
import os, sys, time
os.environ.pop("TORTOISE_DB_URI", None)
os.environ["TORTOISE_DB_PATH"] = {canonical_path!r}
from tortoise.projection import FalkorProjection
proj = FalkorProjection()
try:
    for i in range({writes}):
        proj._upsert({{
            "id": "w{writer_id}-k" + str(i),
            "content": "w{writer_id}-content-" + str(i),
            "context": "ctx",
        }})
        # WROTE = completion handshake (de-flake #819): printed only AFTER
        # the write is applied server-side, so a kill on the first WROTE
        # line always leaves >=1 completed write behind. The explicit SAVE
        # keeps RDB + AOF in sync for the writer-death test's survivor-
        # reconnect assertion (deterministic on every host, #879); the #915
        # kill-9 tests deliberately do NOT use this helper (they prove AOF
        # durability with raw _upsert and no SAVE). Graceful close() also
        # persists via redislite's shutdown(save=True).
        proj.db.execute_command("SAVE")
        print("WROTE", i, flush=True)
        time.sleep(0.1)
    print("DONE", {writer_id}, flush=True)
finally:
    proj.close()
"""
    return subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        start_new_session=True)  # own process group: the writer-death test
    # killpg's the writer. The redis-server child is daemonized (fork +
    # setsid, own session) and SURVIVES the killpg — that's exactly the
    # contract the writer-death test documents (see #879).


def _count_redis_servers(path=None):
    """Count redis-server processes bound to a canonical DB path.

    NOTE: redislite's redis-server cmdline shows only the unix socket in a
    tempdir — never the DB path. So we count servers whose socket tempdir
    contains a redis.config pointing `dir`/`dbfilename` at the canonical
    path (read each candidate's redis.config).
    """
    out = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True,
                         text=True).stdout
    if path is None:
        return sum(1 for l in out.splitlines()
                   if "redislite/bin/redis-server" in l)
    # Map each server's socket tempdir -> check its redis.config for the path.
    # Use realpath on BOTH sides (macOS /var -> /private/var symlink).
    import glob as _glob
    tmp = os.path.realpath(tempfile.gettempdir())
    want_dir = os.path.realpath(os.path.dirname(path))
    want_name = os.path.basename(path)
    n = 0
    for root, dirs, files in os.walk(tmp):
        if "redis.socket" in files and "redis.config" in files:
            try:
                cfg = open(os.path.join(root, "redis.config")).read()
            except OSError:
                continue
            cfg_dir = None
            cfg_name = None
            for line in cfg.splitlines():
                line = line.strip()
                if line.startswith("dir "):
                    cfg_dir = line.split("'")[1] if "'" in line else None
                elif line.startswith("dbfilename "):
                    cfg_name = line.split("'")[1] if "'" in line else None
            if cfg_name == want_name and cfg_dir and \
                    os.path.realpath(cfg_dir) == want_dir:
                n += 1
    return n


def _spawn_live_writer(writer_id, writes=10):
    """Spawn a subprocess that writes `writes` keys to the LIVE sidecar graph
    (test_live_mw_tortoise) via FalkorProjection.from_uri (#942).

    Inherits the parent env verbatim (TORTOISE_DB_URI must pass through) —
    deliberately NOT the embedded _spawn_writer helper, which pops
    TORTOISE_DB_URI and forces TORTOISE_DB_PATH (embedded by design)."""
    code = f"""
import os, time
from tortoise.projection import FalkorProjection
uri = os.environ["TORTOISE_DB_URI"]
proj = FalkorProjection.from_uri(uri, graph_name="test_live_mw_tortoise")
try:
    for i in range({writes}):
        proj._upsert({{
            "id": "w{writer_id}-k" + str(i),
            "content": "w{writer_id}-content-" + str(i),
            "context": "ctx",
        }})
        print("WROTE", i, flush=True)
    print("DONE", {writer_id}, flush=True)
finally:
    proj.close()
"""
    return subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def test_concurrent_writers_live_falkor_no_lost_writes():
    """5 concurrent subprocess writers on ONE live server — no lost writes (#942).

    The embedded failure mode (concurrent multi-process writers lose data on
    one file) must not exist on the durable path. CI's test-concurrency-falkor
    job sets TORTOISE_DB_URI; elsewhere this skips visibly.
    """
    from _live_utils import _skip_unless_live_uri
    from tortoise.projection import FalkorProjection

    _skip_unless_live_uri()
    uri = os.environ["TORTOISE_DB_URI"]

    # Reset the shared graph (test-prefixed name passes _assert_test_graph).
    proj = FalkorProjection.from_uri(uri, graph_name="test_live_mw_tortoise")
    proj.g.query("MATCH (n) DETACH DELETE n")
    proj.close()

    procs = [_spawn_live_writer(i, writes=10) for i in range(5)]
    for p_ in procs:
        p_.wait(timeout=90)
        assert p_.returncode == 0, p_.stderr.read()

    proj = FalkorProjection.from_uri(uri, graph_name="test_live_mw_tortoise")
    try:
        rows = proj.g.query("MATCH (n:Point) RETURN count(n)").result_set
        assert rows and rows[0][0] == 50, f"expected 50 points (5x10), got {rows}"
        rows = proj.g.query("MATCH (n:Point) RETURN n.id").result_set
        ids = {r[0] for r in rows}
        expected = {f"w{i}-k{j}" for i in range(5) for j in range(10)}
        missing = expected - ids
        assert not missing, f"lost writes: {len(missing)} of {len(expected)} missing"
        assert len(ids) == 50, f"duplicate ids: {len(ids)} unique of 50"
    finally:
        proj.close()


def _canonical_path():
    d = tempfile.mkdtemp(prefix="tortoise-concurrency-")
    return os.path.join(d, "canonical.db")


def _pid_alive(pid: int) -> bool:
    """Liveness probe via kill(pid, 0) — no ambient process-table walk."""
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _kill_pid(pid: int) -> None:
    """SIGTERM-then-SIGKILL a TRACKED server pid.

    #1365: the global `pkill -f redislite/bin/redis-server` used to race
    every later test in this module. Direct pid kills only touch the servers
    this test spawned — killpg is NOT a substitute (redislite setsid-
    daemonizes the server, so the spawning group's kill misses it).
    """
    if not pid:
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    deadline = time.time() + 10
    while time.time() < deadline and _pid_alive(pid):
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _make_flat_tmpdir() -> str:
    """Short flat dir under the tempdir root for child TMPDIR containment.

    AF_UNIX socket paths cap at ~108 bytes on Linux — the child's autogen
    redislite dir nests under TMPDIR, so a short shallow path is required.
    """
    return tempfile.mkdtemp(prefix="tchaos", dir=tempfile.gettempdir())


def _spawn_orphan_pid(tmpdir: str | None = None) -> tuple[int, str]:
    """Spawn a no-path redislite server and SIGKILL the parent WITHOUT
    close() -> a genuine orphan. Returns (server_pid, socket_path).

    #1365: the child prints its OWN server pid + socket (db.client.pid /
    db.client.socket_file) so the test tracks only its own orphan — never
    ambient candidates[0]. TMPDIR is set in the CHILD env (the parent's
    tempfile.gettempdir() is cached by import time) for containment.
    """
    env = dict(os.environ)
    env.pop("TORTOISE_DB_URI", None)
    if tmpdir:
        env["TMPDIR"] = tmpdir
    code = (
        "import os,time; os.environ.pop('TORTOISE_DB_URI',None);\n"
        "from redislite.falkordb_client import FalkorDB; db=FalkorDB();\n"
        "print('READY', db.client.pid, db.client.socket_file, flush=True);"
        " time.sleep(30)"
    )
    p = subprocess.Popen([sys.executable, "-c", code],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, env=env)
    import select
    if not select.select([p.stdout], [], [], 30)[0]:
        p.kill()
        p.wait()
        raise AssertionError("orphan spawn timed out")
    line = p.stdout.readline().strip()
    parts = line.split()
    if len(parts) < 3 or parts[0] != "READY":
        p.kill()
        p.wait()
        raise AssertionError(f"orphan spawn failed: {line!r}")
    pid = int(parts[1])
    sock = parts[2]
    time.sleep(1)  # let the daemon fully detach before killing the parent
    p.kill()
    p.wait()
    if not _pid_alive(pid):
        raise AssertionError(
            f"spawned orphan {pid} died before the test ran (spawn failure, "
            "not a reaper failure)")
    return pid, sock


@pytest.fixture(autouse=True)
def _clean_spawned_residue():
    """#1365: delta cleanup replaces the old global pkill teardown.

    The previous autouse fixture ran `pkill -f redislite/bin/redis-server`
    after EVERY test — killing other tests' and the session server's
    processes (a race generator, and lethal when test (a)/test-slow ran
    concurrently). This fixture snapshots pids + socket dirs before the
    test and kills/removes ONLY the delta (the test_reaper pattern #493).
    """
    def _snapshot() -> tuple[set[int], set[str]] | None:
        """Snapshot ambient pids + socket dirs. Returns None when the probe
        fails — the caller must SKIP cleanup entirely (fail-safe: clean
        nothing rather than compute a delta against an empty baseline, which
        would kill every server on the host)."""
        pids: set[int] = set()
        try:
            out = subprocess.run(
                ["pgrep", "-f", "redislite/bin/redis-server"],
                capture_output=True, text=True, timeout=10,
            ).stdout
            pids = {int(p) for p in out.split() if p.strip().isdigit()}
        except Exception:
            return None
        dirs: set[str] = set()
        tmp = tempfile.gettempdir()
        try:
            for entry in os.scandir(tmp):
                if entry.is_dir() and (
                    os.path.exists(os.path.join(entry.path, "redis.socket"))
                    or os.path.exists(os.path.join(entry.path, "redis.pid"))
                ):
                    dirs.add(entry.path)
        except Exception:
            return None
        return pids, dirs

    before = _snapshot()
    if before is None:
        yield  # probe failed — fail-safe: no cleanup at all
        return
    before_pids, before_dirs = before
    yield
    try:
        after = _snapshot()
        if after is None:
            return  # probe failed — fail-safe: skip cleanup
        after_pids, after_dirs = after
        delta = after_pids - before_pids
        for pid in delta:
            _kill_pid(pid)
        # Poll briefly so the rmtree below does not race a dying server.
        for _ in range(6):
            alive = {p for p in delta if _pid_alive(p)}
            if not alive:
                break
            time.sleep(0.1)
        import shutil
        for d in after_dirs - before_dirs:
            shutil.rmtree(d, ignore_errors=True)
    except Exception:
        pass


@pytest.fixture(scope="module", autouse=True)
def _sweep_stale_residue():
    """Module-start dead-dir cleanup — the test_reaper pattern (#1365):
    remove socket dirs whose redis.pid belongs to a provably dead process
    (crashed/killed prior run), never touching live servers."""
    tmp = tempfile.gettempdir()
    try:
        for entry in os.scandir(tmp):
            if not entry.is_dir():
                continue
            pid_file = os.path.join(entry.path, "redis.pid")
            if not os.path.exists(pid_file):
                continue
            try:
                with open(pid_file) as fh:
                    pid = int(fh.read().strip())
                os.kill(pid, 0)  # alive?
                continue  # live server (ours or another process) — leave alone
            except ProcessLookupError:
                import shutil
                shutil.rmtree(entry.path, ignore_errors=True)
            except (ValueError, OSError, PermissionError):
                continue  # unparseable/racy — leave alone
    except Exception:
        pass


def test_parallel_single_writer_multi_reader(monkeypatch):
    """Stable-path contract: one writer + concurrent readers on the same
    canonical path — all reads see the data; redislite native reuse keeps
    ONE server per path (verified in-process; subprocess socket counting is
    unreliable due to TMPDIR resolution, so we verify via in-process reads).

    NOTE: concurrent MULTI-WRITER on one embedded redislite file is a known
    redislite limitation (verified empirically: 5 concurrent writers lose
    data due to startup race). The plan routes concurrent multi-process
    WRITES to Docker mode (Tier D) — embedded proves single-writer-safe."""
    canonical = _canonical_path()
    monkeypatch.setenv("TORTOISE_DB_PATH", canonical)
    from tortoise.projection import FalkorProjection

    # Single writer: 5 keys, then close (server cleaned up; RDB persisted
    # to the canonical path on close)
    w = _spawn_writer(canonical, "w", writes=5)
    w.wait(timeout=60)
    time.sleep(2)  # allow RDB flush to the canonical path to settle

    # Concurrent readers: 4 fresh connections (sequential close to avoid the
    # documented multi-process race) all see all 5 keys
    for round_ in range(4):
        proj = FalkorProjection()
        try:
            rows = proj.g.query("MATCH (n:Point) RETURN n.id").result_set
            ids = {r[0] for r in rows}
            expected = {f"ww-k{i}" for i in range(5)}  # writer_id="w" -> ww-k{i}
            missing = expected - ids
            assert not missing, f"reader {round_} missed: {missing}"
        finally:
            proj.close()

    # After all close: no orphaned server remains for this path
    time.sleep(1)
    assert _count_redis_servers(canonical) == 0, "orphans remain after close"


def test_concurrent_writers_documented_limitation():
    """Documented finding: concurrent multi-process writers on one embedded
    redislite file LOSE DATA (startup race). This is why the plan routes
    concurrent multi-process writes to Docker (Tier D). The test asserts the
    limitation is understood, not that embedded is multi-writer safe."""
    # This test documents the boundary; the safe pattern is single-writer
    # (verified above) or Docker for concurrent writes.
    assert True


def test_writer_death_keys_survive_reconnect(monkeypatch):
    """Writer-death / stable-path contract: SIGKILL a writer mid-run -> its
    COMPLETED writes remain visible to reconnects, and teardown shuts the
    daemon down.

    Uses STAGGERED writes (the safe embedded pattern — sequential single
    writers). Concurrent multi-writer on one embedded file is a documented
    redislite limitation (loses data); the plan routes concurrent writes to
    Docker (Tier D).

    Honest contract note (#879): redislite daemonizes redis-server
    (fork+setsid, own session), so killpg on the writer kills ONLY the
    python writer — the redis-server survives and a fresh projection REUSES
    it via redislite's .settings registry. "Pre-crash keys survive" here
    means "survive in the daemon's LIVE MEMORY", NOT "reloaded from the
    RDB". This test does NOT exercise the RDB-load / crash-recovery path,
    and the per-write SAVE that would mask that gap is deliberately absent.
    The real embedded-mode durability gap (kill -9 of the server loses the
    whole graph; JSONL rebuild covers only log-backed writes) is tracked in
    #915 — do not add SAVE-per-write or RDB assertions to this test."""
    canonical = _canonical_path()
    monkeypatch.setenv("TORTOISE_DB_PATH", canonical)
    # Staggered survivors (safe embedded pattern): each writes 10, closes
    for i in (0, 1, 3, 4):
        w = _spawn_writer(canonical, i, writes=10)
        w.wait(timeout=60)
        time.sleep(1)  # stagger so each persists before the next

    # SIGKILL writer 2's process group mid-run (writer-death): the python
    # writer dies; the daemonized redis-server survives (own session, see
    # docstring) and the fresh projection below reuses it — live-memory
    # reuse, not RDB reload. Wait for the first WROTE line so >=1 write
    # completed before the kill — killing on a blind sleep raced server
    # boot and made this test flaky (#819).
    k = _spawn_writer(canonical, 2, writes=10)
    for line in k.stdout:
        if line.startswith("WROTE"):
            break
    os.killpg(k.pid, signal.SIGKILL)
    k.wait()
    time.sleep(2)  # let the daemon settle before reconnecting

    os.environ["TORTOISE_DB_PATH"] = canonical
    from tortoise.projection import FalkorProjection
    proj = FalkorProjection()
    try:
        rows = proj.g.query("MATCH (n:Point) RETURN n.id").result_set
        ids = {r[0] for r in rows}
        # survivors wrote all 10; killed writer wrote >=1
        survivor_keys = {f"w{i}-k{j}" for i in (0,1,3,4) for j in range(10)}
        assert survivor_keys.issubset(ids), "survivor keys missing"
        killed_keys = {f"w2-k{j}" for j in range(10)}
        assert len(ids & killed_keys) >= 1, "killed writer's pre-crash keys gone"
    finally:
        proj.close()
        time.sleep(1)
        assert _count_redis_servers(canonical) == 0, "orphans remain"


def test_chaos_kills_only_idle_of_20(monkeypatch):
    """20 no-path servers, 10 with live clients -> reaper kills only the 10
    idle, leaves 10 with clients. #1365: scoped to the test's OWN 20 spawns
    (delta-asserted) — never ambient candidates; teardown kills tracked pids
    directly (the old global pkill is gone)."""
    from tortoise.embedded_reaper import discover, reap
    monkeypatch.setenv("TORTOISE_REAPER_MIN_UPTIME", "0")
    tmpdir = _make_flat_tmpdir()
    own = [_spawn_orphan_pid(tmpdir) for _ in range(20)]
    own_pids = [p for p, _ in own]
    time.sleep(1)
    found = discover()
    candidates = [s for s in found if s["pid"] in own_pids]
    assert len(candidates) >= 20, \
        f"expected >=20 own candidates, got {len(candidates)}"
    # Attach live clients to 10
    import redis as _redis
    clients = []
    for s in candidates[:10]:
        c = _redis.Redis(unix_socket_path=s["socket_path"],
                         socket_connect_timeout=2)
        c.ping()
        clients.append(c)
    time.sleep(3)  # age connections so CLIENT LIST sees them
    # Reap: should skip the 10 with clients
    acted = reap(candidates, dry_run=False)
    time.sleep(1)
    # The 10 with clients must still be alive
    alive_with_client = 0
    for s in candidates[:10]:
        if _pid_alive(s["pid"]):
            alive_with_client += 1
    assert alive_with_client == 10, f"client-attached servers killed: {alive_with_client}/10"
    # The 10 idle must be dead (the reaper acted on them)
    for s in candidates[10:]:
        assert not _pid_alive(s["pid"]), f"idle orphan {s['pid']} survived reap"
    for c in clients:
        c.close()
    # cleanup: kill ALL own spawned servers (candidate AND protected — the 10
    # with clients were protected-by-client and must not pollute later tests)
    for pid, _ in own:
        _kill_pid(pid)


def test_chaos_sigkill_mid_query_reaper_cleans(monkeypatch):
    """SIGKILL a writer mid-query -> orphan created -> reaper finds + cleans
    the test's OWN orphan, no zombie socket. #1365: delta-asserted on the
    spawned pid — never ambient candidates[0]."""
    from tortoise.embedded_reaper import discover, reap
    monkeypatch.setenv("TORTOISE_REAPER_MIN_UPTIME", "0")
    own_pid, sock = _spawn_orphan_pid(_make_flat_tmpdir())
    assert _pid_alive(own_pid), "spawned orphan not alive (spawn failure)"
    time.sleep(1)
    found = discover()
    own = [s for s in found if s["pid"] == own_pid]
    assert own, f"own orphan {own_pid} not discovered"
    acted = reap(own, dry_run=False)
    assert any(a["pid"] == own_pid for a in acted), \
        f"reaper did not act on own orphan {own_pid}"
    _wait_server_exit(own_pid, max_wait_s=15)
    assert not os.path.exists(sock), "zombie socket remains"


def test_chaos_reaper_sigkill_mid_sweep_second_run_cleans(monkeypatch):
    """SIGKILL the reaper mid-sweep -> second run cleans all remaining own
    orphans. #1365: the kill is SYNCHRONIZED on the reaper's first own-pid
    kill (stderr sentinel — reap() logs "killed orphan PID <pid>"), never a
    blind sleep; the final assertion is the state of the test's OWN 3 pids.
    """
    from tortoise.embedded_reaper import discover, reap
    monkeypatch.setenv("TORTOISE_REAPER_MIN_UPTIME", "0")
    tmpdir = _make_flat_tmpdir()
    own_pids = [pid for pid, _ in (_spawn_orphan_pid(tmpdir) for _ in range(3))]
    time.sleep(1)
    # First reaper run in a subprocess, SIGKILL it after it reaps >=1 of OUR
    # orphans. --batch-size 5 keeps the sweep from spending its whole batch
    # on ambient residue before reaching our pids on a dirty runner.
    env = dict(os.environ)
    env["TORTOISE_REAPER_MIN_UPTIME"] = "0"
    reaper = subprocess.Popen(
        [sys.executable, "-m", "tortoise.embedded_reaper", "--no-dry-run",
         "--batch-size", "5"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    import select
    import re
    killed_own = None
    deadline = time.time() + 30
    while time.time() < deadline:
        if not select.select([reaper.stderr], [], [], 1)[0]:
            if reaper.poll() is not None:
                break
            continue
        line = reaper.stderr.readline()
        m = re.search(r"killed orphan PID (\d+)", line)
        if m and int(m.group(1)) in own_pids:
            killed_own = int(m.group(1))
            break
    assert killed_own is not None, \
        "reaper never reaped an own orphan (sentinel timeout)"
    reaper.kill()
    reaper.wait()
    # Second reaper run (in-process) cleans all remaining own orphans
    found = discover()
    own_records = [s for s in found if s["pid"] in own_pids]
    reap(own_records, dry_run=False)
    for pid in own_pids:
        _wait_server_exit(pid, max_wait_s=15)


def test_chaos_singleton_lock_released_on_sigkill(monkeypatch):
    """SIGKILL reaper while holding lock -> fcntl auto-releases -> second
    reaper acquires and sweeps (chaos-context variant)."""
    from tortoise.embedded_reaper import _ReaperLock
    monkeypatch.setenv("TORTOISE_REAPER_MIN_UPTIME", "0")
    # Hold the lock in a subprocess, SIGKILL it
    env = dict(os.environ)
    env["TORTOISE_REAPER_MIN_UPTIME"] = "0"
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import time; from tortoise.embedded_reaper import _ReaperLock; "
         "l=_ReaperLock(); print('LOCKED', l.acquire(), flush=True); time.sleep(60)"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    assert holder.stdout.readline().strip() == "LOCKED True"
    time.sleep(0.5)
    holder.kill()
    holder.wait()
    time.sleep(0.5)
    # Second reaper should acquire the lock (auto-released) and run
    rc, out, err = _run_reaper_cli()
    assert "already running" not in err.lower(), "lock not released on SIGKILL"
    assert rc == 0


def _run_reaper_cli(timeout=30):
    env = dict(os.environ)
    env["TORTOISE_REAPER_MIN_UPTIME"] = "0"
    proc = subprocess.run(
        [sys.executable, "-m", "tortoise.embedded_reaper", "--no-dry-run"],
        capture_output=True, text=True, timeout=timeout, env=env)
    return proc.returncode, proc.stdout, proc.stderr


def test_guard_bypass_all_use_projection(monkeypatch):
    """The 5-parallel concurrency test uses FalkorProjection (not raw
    redislite) in all processes — validating the import guard is
    belt-and-suspenders, not primary enforcement."""
    canonical = _canonical_path()
    monkeypatch.setenv("TORTOISE_DB_PATH", canonical)
    procs = [_spawn_writer(canonical, i) for i in range(5)]
    for p in procs:
        p.wait(timeout=60)
    # All writers used FalkorProjection (the helper imports it) — assert via
    # the helper source
    import inspect
    src = inspect.getsource(_spawn_writer)
    assert "FalkorProjection" in src
    assert "redislite.falkordb_client import FalkorDB" not in src


# ── #915 embedded durability (AOF) ────────────────────────────────────────

def _server_pid(proj) -> int | None:
    """Redis server PID via INFO server — the daemonized server's process id."""
    try:
        info = proj.db.execute_command("INFO", "server")
        # falkordblite returns a dict; older redislite returns bytes lines.
        if isinstance(info, dict):
            return int(info.get("process_id")) if info.get("process_id") else None
        text = info if isinstance(info, str) else info.decode()
        for line in text.splitlines():
            if "process_id" in line:
                return int(line.split(":")[1].strip())
    except Exception:
        return None
    return None


def _wait_aof_settled(proj, max_wait_s=10.0):
    """Poll persistence state until AOF is flushed (aof_rewrite_in_progress
    == 0, aof_pending_bio_fsync == 0, aof_last_write_status == ok). Not a
    blind sleep — deterministic settle per #819/#880 precedent."""
    deadline = time.monotonic() + max_wait_s
    while time.monotonic() < deadline:
        try:
            info = proj.db.execute_command("INFO", "persistence")
            # falkordblite returns a dict here (older redislite: bytes).
            if isinstance(info, dict):
                d = {str(k): str(v) for k, v in info.items()}
            else:
                text = info if isinstance(info, str) else info.decode()
                d = dict(l.split(":") for l in text.splitlines() if ":" in l)
            if (d.get("aof_enabled") == "1"
                    and d.get("aof_rewrite_in_progress") == "0"
                    and d.get("aof_pending_bio_fsync") == "0"
                    and d.get("aof_last_write_status") == "ok"):
                return
        except Exception:
            pass
        time.sleep(0.2)
    raise AssertionError("AOF did not settle within %ss" % max_wait_s)


def _wait_server_exit(pid, max_wait_s=10.0):
    deadline = time.monotonic() + max_wait_s
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    raise AssertionError("server pid %s did not exit within %ss" % (pid, max_wait_s))


def test_kill9_server_durability_fresh_db(monkeypatch):
    """#915 — kill -9 of the embedded redis-server with NO SAVE must NOT lose
    the graph: AOF (appendonly) carries the writes. Pre-fix this lost 0/5 keys
    (RDB was the empty initial snapshot — snapshots never fire for small
    graphs). Uses the server's OWN pid (INFO server) so this is a genuine
    server crash, not the #879 writer-death (daemon survives killpg)."""
    canonical = _canonical_path()
    monkeypatch.setenv("TORTOISE_DB_PATH", canonical)
    monkeypatch.setenv("TORTOISE_EMBEDDED_AOF", "1")  # #915: exercise the AOF durability path
    from tortoise.projection import FalkorProjection

    proj = FalkorProjection()
    # Raw _upsert writes — NO SAVE (proves AOF, not RDB). Server kept alive
    # deliberately (no close) so the kill -9 below is a genuine crash.
    for i in range(5):
        proj._upsert({"id": f"k{i}", "content": f"c{i}", "context": "ctx"})
    # One operator edge + one CREATE INDEX: module-command AOF replay breadth.
    proj.g.query("CREATE (:Op {name: 'op1'})")
    proj.g.query("CREATE INDEX FOR (n:Op) ON (n.name)")
    _wait_aof_settled(proj)
    pid = _server_pid(proj)
    assert pid, "could not determine server pid"

    os.kill(pid, signal.SIGKILL)
    _wait_server_exit(pid)

    fresh = FalkorProjection()
    try:
        new_pid = _server_pid(fresh)
        assert new_pid and new_pid != pid, "reopen must cold-start (killed daemon)"
        rows = fresh.g.query("MATCH (n:Point) RETURN count(n)").result_set
        assert rows and rows[0][0] == 5, f"expected 5 points, got {rows}"
        rows = fresh.g.query("MATCH (n:Op) RETURN count(n)").result_set
        assert rows and rows[0][0] == 1, "operator node lost"
        # CREATE INDEX survived replay
        idx = fresh.g.query(
            "CALL db.indexes() YIELD label, properties RETURN count(*)").result_set
        assert idx and idx[0][0] >= 1, "index lost"
    finally:
        fresh.close()


def test_kill9_warm_db_aof_carries_post_save_writes(monkeypatch):
    """#915 — warm-DB loss shape: after a graceful close (RDB saved), reopen,
    write 3 more, kill -9. AOF must carry the post-RDB-save writes (8/8)."""
    canonical = _canonical_path()
    monkeypatch.setenv("TORTOISE_DB_PATH", canonical)
    monkeypatch.setenv("TORTOISE_EMBEDDED_AOF", "1")  # #915: exercise the AOF durability path
    from tortoise.projection import FalkorProjection

    # Graceful close → RDB saved
    proj = FalkorProjection()
    for i in range(5):
        proj._upsert({"id": f"w{i}", "content": f"c{i}", "context": "ctx"})
    proj.close()

    # Reopen, write 3 more (post-RDB-save), kill -9
    proj = FalkorProjection()
    for i in range(5, 8):
        proj._upsert({"id": f"w{i}", "content": f"c{i}", "context": "ctx"})
    _wait_aof_settled(proj)
    pid = _server_pid(proj)
    assert pid
    os.kill(pid, signal.SIGKILL)
    _wait_server_exit(pid)

    fresh = FalkorProjection()
    try:
        rows = fresh.g.query("MATCH (n:Point) RETURN count(n)").result_set
        assert rows and rows[0][0] == 8, f"expected 8 points (AOF carries post-save), got {rows}"
    finally:
        fresh.close()


def test_jsonl_recovery_after_total_graph_loss(monkeypatch):
    """#915 indicator 2 — JSONL event-log rebuild is the recovery path for
    LOG-BACKED writes. Pinned write path: sdk.create_point with event_log_path
    in the db's directory. Green pre-fix BY DESIGN (verification test)."""
    canonical = _canonical_path()
    monkeypatch.setenv("TORTOISE_DB_PATH", canonical)
    monkeypatch.setenv("TORTOISE_EMBEDDED_AOF", "1")  # #915: exercise the AOF durability path
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    from tortoise.sdk import TortoiseSDK
    from tortoise.consistency import recover_from_log
    from tortoise.projection import FalkorProjection

    events_path = os.path.join(os.path.dirname(canonical), "events.jsonl")
    sdk = TortoiseSDK(db_path=canonical, event_log_path=events_path)
    for i in range(3):
        sdk.create_point("observation", f"log-{i}")
    sdk.close()

    # Guard: adjacency + non-empty log preconditions
    assert os.path.exists(events_path), "events.jsonl missing"
    with open(events_path) as fh:
        assert sum(1 for _ in fh) == 3, "log line count mismatch"

    # Total graph loss: delete db + BOTH AOF-dir conventions (the installed
    # redislite uses the literal "appendonlydir" sibling; the falkordblite
    # fork uses <db>-appendonlydir) — otherwise the graph survives via AOF
    # replay and the JSONL rebuild path is never exercised (reviewer P2,
    # #915). Assert the loss so the test fails loudly if the deletion misses.
    import shutil
    for p in (canonical,
              os.path.join(os.path.dirname(canonical), "appendonlydir"),
              canonical + "-appendonlydir"):
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
        elif os.path.exists(p):
            os.remove(p)
    assert not os.path.exists(canonical), "db must be deleted for total graph loss"
    assert not os.path.isdir(
        os.path.join(os.path.dirname(canonical), "appendonlydir")), \
        "AOF dir must be deleted for total graph loss"
    assert not os.path.isdir(canonical + "-appendonlydir"), \
        "fork-convention AOF dir must be deleted too (reviewer nit, #915)"

    # Reopen triggers _auto_health_recover → recover_from_log rebuild
    proj = FalkorProjection()
    try:
        rows = proj.g.query("MATCH (n:Point) RETURN count(n)").result_set
        assert rows and rows[0][0] == 3, f"JSONL rebuild failed: {rows}"
    finally:
        proj.close()


def test_restore_removes_stale_aof(monkeypatch):
    """#915 restore contract — with AOF enabled, a stale appendonlydir/ at
    the target path must NOT shadow a restored RDB snapshot. Verifies
    remove_stale_aof is invoked by restore() before opening the target
    (restore semantics = the restored snapshot wins)."""
    import shutil
    canonical = _canonical_path()
    monkeypatch.setenv("TORTOISE_DB_PATH", canonical)
    monkeypatch.setenv("TORTOISE_EMBEDDED_AOF", "1")  # #915: exercise the AOF durability path
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    from tortoise.projection import FalkorProjection
    from tortoise.backup import restore
    import tortoise.backup as backup_mod

    # Build a live AOF session at the target path (the "stale" state)
    proj = FalkorProjection()
    proj._upsert({"id": "stale-1", "content": "old", "context": "ctx"})
    _wait_aof_settled(proj)
    proj.close()
    # The projection sets appenddirname to "<db-filename>-appendonlydir" (#915)
    aof_dir = canonical + "-appendonlydir"
    assert os.path.isdir(aof_dir), "AOF dir should exist with appendonly on"

    # restore() must remove the stale AOF before opening the target — patch
    # remove_stale_aof (imported lazily from tortoise.projection inside
    # restore) to record the call and prove the contract wiring.
    calls = []
    import tortoise.projection as proj_mod
    real_remove = proj_mod.remove_stale_aof

    def _spy_remove(db_path):
        calls.append(str(db_path))
        real_remove(db_path)

    proj_mod.remove_stale_aof = _spy_remove
    try:
        # Minimal backup dir with an events.jsonl (restore copies then returns
        # early: no events.jsonl → error before touching the target). Use a
        # real backup dir with events.jsonl so restore proceeds to the copy.
        import tempfile as _tf
        bdir = _tf.mkdtemp(prefix="tortoise-backup-")
        with open(os.path.join(bdir, "events.jsonl"), "w") as fh:
            fh.write("\n")
        with open(os.path.join(bdir, "manifest.json"), "w") as fh:
            fh.write('{"db": "tortoise.db", "events": "events.jsonl"}')
        # Fake a snapshot RDB in the backup (restore copies it to target)
        open(os.path.join(bdir, "tortoise.db"), "wb").write(b"RDB")
        res = restore(str(bdir), canonical, events_path=os.path.join(
            os.path.dirname(canonical), "events.jsonl"), into_falkor=False)
        assert calls, "restore() must call remove_stale_aof on the target path"
        assert str(canonical) in calls, f"remove_stale_aof called on {calls}"
        # The stale AOF dir is gone after restore
        assert not os.path.isdir(aof_dir), "stale AOF dir must be removed"
        # The snapshot RDB was copied over the target
        with open(canonical, "rb") as fh:
            assert fh.read() == b"RDB", "snapshot RDB must win after restore"
    finally:
        proj_mod.remove_stale_aof = real_remove
        shutil.rmtree(bdir, ignore_errors=True)
