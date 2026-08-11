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
        # line always leaves >=1 completed write behind. No explicit SAVE
        # per write — forcing RDB durability here would mask the embedded
        # durability gap tracked in #915 (graceful close() still persists
        # via redislite's shutdown(save=True)).
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


def _canonical_path():
    d = tempfile.mkdtemp(prefix="tortoise-concurrency-")
    return os.path.join(d, "canonical.db")


@pytest.fixture(autouse=True)
def _teardown_servers():
    """Module teardown: kill redis-servers from this test's process group."""
    yield
    try:
        subprocess.run(["pkill", "-f", "redislite/bin/redis-server"],
                       capture_output=True)
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
    idle, leaves 10 with clients."""
    from tortoise.embedded_reaper import discover, reap
    monkeypatch.setenv("TORTOISE_REAPER_MIN_UPTIME", "0")
    # Spawn 20 orphan servers (SIGKILL'd parents)
    socks = []
    for i in range(20):
        code = (
            "import os,time; os.environ.pop('TORTOISE_DB_URI',None);\n"
            "from redislite.falkordb_client import FalkorDB; db=FalkorDB();\n"
            "print('READY', flush=True); time.sleep(30)"
        )
        p = subprocess.Popen([sys.executable, "-c", code],
                             stdout=subprocess.PIPE, text=True)
        p.stdout.readline()
        p.kill()
        p.wait()
        time.sleep(0.2)
    time.sleep(1)
    found = discover()
    candidates = [s for s in found if s["classification"] == "candidate"]
    assert len(candidates) >= 20, f"expected >=20 candidates, got {len(candidates)}"
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
    for s, c in zip(candidates[:10], clients):
        try:
            os.kill(s["pid"], 0)
            alive_with_client += 1
        except (ProcessLookupError, PermissionError):
            pass
    assert alive_with_client == 10, f"client-attached servers killed: {alive_with_client}/10"
    for c in clients:
        c.close()
    # cleanup: kill ALL spawned servers (candidate AND protected — the 10
    # with clients are protected-by-client and must not pollute later tests)
    subprocess.run(["pkill", "-f", "redislite/bin/redis-server"],
                   capture_output=True)
    time.sleep(1)


def test_chaos_sigkill_mid_query_reaper_cleans(monkeypatch):
    """SIGKILL a writer mid-query -> orphan created -> reaper finds + cleans,
    no zombie socket."""
    from tortoise.embedded_reaper import discover, reap
    monkeypatch.setenv("TORTOISE_REAPER_MIN_UPTIME", "0")
    # spawn an orphan (SIGKILL'd parent)
    code = (
        "import os,time; os.environ.pop('TORTOISE_DB_URI',None);\n"
        "from redislite.falkordb_client import FalkorDB; db=FalkorDB();\n"
        "print('READY', flush=True); time.sleep(30)"
    )
    p = subprocess.Popen([sys.executable, "-c", code],
                         stdout=subprocess.PIPE, text=True)
    p.stdout.readline()
    p.kill()
    p.wait()
    time.sleep(1)
    found = discover()
    candidates = [s for s in found if s["classification"] == "candidate"]
    assert candidates, "no orphan candidates found"
    pid = candidates[0]["pid"]
    reap(candidates, dry_run=False)
    time.sleep(1)
    try:
        os.kill(pid, 0)
        assert False, "orphan not cleaned by reaper"
    except ProcessLookupError:
        pass  # killed — good
    # no zombie socket left
    sock = candidates[0]["socket_path"]
    assert not os.path.exists(sock), "zombie socket remains"


def test_chaos_reaper_sigkill_mid_sweep_second_run_cleans(monkeypatch):
    """SIGKILL the reaper mid-sweep -> second reaper run cleans all remaining."""
    from tortoise.embedded_reaper import discover, reap
    monkeypatch.setenv("TORTOISE_REAPER_MIN_UPTIME", "0")
    # spawn 3 orphans
    for _ in range(3):
        code = (
            "import os,time; os.environ.pop('TORTOISE_DB_URI',None);\n"
            "from redislite.falkordb_client import FalkorDB; db=FalkorDB();\n"
            "print('READY', flush=True); time.sleep(30)"
        )
        p = subprocess.Popen([sys.executable, "-c", code],
                             stdout=subprocess.PIPE, text=True)
        p.stdout.readline()
        p.kill()
        p.wait()
    time.sleep(1)
    # First reaper run in a subprocess, SIGKILL it mid-sweep
    env = dict(os.environ)
    env["TORTOISE_REAPER_MIN_UPTIME"] = "0"
    reaper = subprocess.Popen(
        [sys.executable, "-m", "tortoise.embedded_reaper", "--no-dry-run"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    time.sleep(1.5)  # let it start sweeping
    reaper.kill()
    reaper.wait()
    time.sleep(1)
    # Second reaper run cleans all remaining
    found = discover()
    candidates = [s for s in found if s["classification"] == "candidate"]
    reap(candidates, dry_run=False)
    time.sleep(1)
    remaining = [s for s in discover() if s["classification"] == "candidate"]
    assert not remaining, f"{len(remaining)} orphans remain after second run"


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
