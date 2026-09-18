"""#3845 part 2 — the module-fork leak's PRODUCER and the fix that stops it.

Part 1 (#3845) detects and recovers a wedged module-fork slot.  It does not
stop the slot from wedging, so it wedges again on the same conditions.  This
file carries the evidence for the producer and for the mitigation.

Producer (measured; see ``tortoise/fork_safety.py`` for the full write-up)
-------------------------------------------------------------------------
Inside the **redis-server** process — never in Python:

* the fork is FalkorDB's ``Cron_Run`` -> ``_Graph_Copy`` -> ``RedisModule_Fork``;
* the lock is macOS's process-global timezone rwlock, taken by
  ``afterSleep -> localtime_r`` (main thread, EVERY event-loop wakeup — an
  unconditional call in the bundled redis-server 8.6.2) and by
  ``serverLogRaw -> strftime_l`` on any logging thread.

A ``fork()`` landing inside that hold gives the child the rwlock already
taken, and the child's first ``RM_Log`` blocks in it forever.

The mitigation this file proves
-------------------------------
``serverLogRaw`` returns at its first instruction when
``level < server.verbosity`` — *before* ``fopen``/``strftime``.  Pinning the
embedded daemon's verbosity above NOTICE therefore keeps the fork child's
blocking log call off the timezone lock, while ``GRAPH.COPY`` keeps working.

These tests assert OBSERVABLES, never source text or a constant: a real
module-fork child (a direct child of the daemon process, which under this
config can only be a module fork) still alive seconds after a 1-node copy and
having burned no CPU in the meantime, plus redis's own ``total_forks``
counter — so a green run is evidence the fork path RAN, not that it was quiet.

The race is made deterministic by holding the tz rwlock from a thread inside
the server process with ``tests/fixtures/fork_safety/tz_holder.c``: it points
``TZ`` at a zone file it owns and bumps that file before every
``localtime_r``/``strftime``, so every timestamp in the process — the
holder's, redis's ``afterSleep``, and every log line — re-reads the file with
the tz rwlock held exclusively.  Without that holder the leak is
frequency-dependent (roughly one copy in fifty) and untestable.

Layout:

* ``test_fork_child_does_not_hang_with_the_production_config`` — the fix.
* ``test_without_the_fix_the_same_race_hangs_a_child`` — mutation proof: the
  ONLY difference from the test above is the verbosity, and the leak appears.
  It also proves the harness can detect a leak, so a green result above is
  meaningful rather than vacuous.
* ``test_embedded_choke_point_runs_at_the_proven_fork_safe_level`` — wiring:
  the daemon ``tortoise.FalkorDB`` actually starts runs at the level the
  harness above proved safe.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

import pytest

from tortoise.fork_safety import (
    FORK_SAFE_LOGLEVEL,
    enforce_embedded_fork_safety,
    fork_safe_serverconfig,
)

pytestmark = pytest.mark.embedded_only

_FIXTURE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "tests", "fixtures", "fork_safety", "tz_holder.c",
)
#: How long each race runs.  With the holder below the leak lands within
#: 7-20 copies in every measured run; the window stays generous so a loaded
#: machine cannot turn it into a false green.
_RACE_SECONDS = float(os.environ.get("TORTOISE_FORK_RACE_SECONDS", "15"))
#: A copy child for a 1-node graph is sub-millisecond.  One still alive this
#: long, having burned no CPU meanwhile, is parked on the inherited lock.
_PARKED_AFTER_S = 2.0
#: A fork that lands in the lock can arrive at the very end of the window.
#: Give any live child this long to finish (healthy) or freeze (parked).
_SETTLE_S = 6.0


def _redislite_paths():
    import redislite
    server = getattr(redislite, "__redis_executable__", None)
    module = getattr(redislite, "__falkordb_module__", None)
    if not server or not module or not os.path.exists(server):
        pytest.skip("bundled redis-server/falkordb.so not available")
    return server, module


def _build_holder(tmp_path) -> str:
    """Compile the tz-rwlock holder.  macOS-only by construction."""
    if sys.platform != "darwin":
        pytest.skip("the tz-rwlock fork hazard is a macOS libsystem behavior")
    if shutil.which("clang") is None and shutil.which("cc") is None:
        pytest.skip("no C compiler for the tz-holder fixture")
    if not os.path.exists(_FIXTURE):
        pytest.skip("tz-holder fixture missing")
    compiler = shutil.which("clang") or shutil.which("cc")
    out = str(tmp_path / "libtzholder.dylib")
    proc = subprocess.run(
        [compiler, "-dynamiclib", "-O1", "-o", out, _FIXTURE],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0 or not os.path.exists(out):
        pytest.skip(f"could not build tz-holder: {proc.stderr[-400:]}")
    return out


def _cpu_seconds(stamp: str) -> float:
    """``ps -o time=`` (``MM:SS.ss`` or ``HH:MM:SS``) as seconds."""
    try:
        parts = [float(p) for p in stamp.split(":")]
    except ValueError:
        return -1.0
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + part
    return seconds


def _daemon_children(daemon_pid: int):
    """``(pid, cumulative_cpu_seconds)`` for every child of OUR daemon.

    Any child of this daemon is a module fork: the config it runs under turns
    off the RDB and AOF forking children (``save ''``, ``appendonly no``), and
    ``daemonize no`` rules out the startup double-fork.  Keying on the parent
    pid rather than on a process title keeps the harness independent of what a
    child calls itself, and structurally unable to see any other server.
    """
    out = subprocess.run(
        ["ps", "-eo", "pid,ppid,time"],
        capture_output=True, text=True, check=False,
    ).stdout
    found = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) == 3 and parts[1] == str(daemon_pid):
            found.append((int(parts[0]), _cpu_seconds(parts[2])))
    return found


def _parked(children, first_seen: dict, now: float):
    """Children that have been alive ``_PARKED_AFTER_S`` without using CPU."""
    parked = []
    for pid, cpu in children:
        started, cpu0 = first_seen.setdefault(pid, (now, cpu))
        age = now - started
        if age >= _PARKED_AFTER_S and cpu - cpu0 < 0.10:
            parked.append((pid, round(age, 1), round(cpu - cpu0, 3)))
    return parked


def _total_forks(db) -> int | None:
    """redis's fork counter — the observable that the fork path really ran.

    A counter incremented per ``fork()``, not a constant: it is how the tests
    below show their race was live instead of merely quiet.
    """
    info = db.execute_command("INFO")
    if isinstance(info, dict):
        return int(info.get("total_forks", -1))
    for line in str(info).splitlines():
        if line.startswith("total_forks:"):
            return int(line.split(":", 1)[1])
    return None


def _conf_lines(serverconfig: dict | None, sock: str, dbdir: str) -> str:
    lines = [
        "daemonize no", "port 0",
        f"unixsocket {sock}", "unixsocketperm 700",
        f"dir {dbdir}", f"logfile {dbdir}/redis.log",
        # no periodic BGSAVE: the fork under test must be the module fork
        "save ''", "appendonly no",
    ]
    for key, value in (serverconfig or {}).items():
        lines.append(f"{key} {value}")
    return "\n".join(lines) + "\n"


def _race(holder: str, serverconfig: dict | None, dbdir: str) -> dict:
    """Run GRAPH.COPY in a loop against a daemon whose tz rwlock is held.

    Returns the observables: copies completed, the clone's node count, and any
    ``redis-module-fork`` child that stayed parked.
    """
    server_bin, module = _redislite_paths()
    sock = os.path.join(dbdir, "redis.socket")
    conf = os.path.join(dbdir, "redis.conf")
    with open(conf, "w") as fh:
        fh.write(_conf_lines(serverconfig, sock, dbdir))
    env = dict(os.environ)
    env["DYLD_INSERT_LIBRARIES"] = holder
    env["TORTOISE_TZ_HOLDER"] = "1"
    env["TORTOISE_TZ_HOLDER_DELAY"] = "3"
    # More than one tz-lock holder, so SOME thread holds it for most of the
    # wall clock; at the fixture's default of 1 the collision is a few percent
    # per fork, and this harness must not be a coin flip.
    env["TORTOISE_TZ_THREADS"] = "6"
    # The holder makes every localtime_r/strftime in the process re-read this
    # file (and so take the tz rwlock exclusively).  From /usr/share/zoneinfo
    # so it is a real zone file, in our own dir so the holder can bump it.
    zone = os.path.join(dbdir, "zoneinfo")
    shutil.copyfile("/usr/share/zoneinfo/UTC", zone)
    env["TORTOISE_TZ_FILE"] = zone
    proc = subprocess.Popen(
        [server_bin, conf, "--loadmodule", module], env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 20
    while time.time() < deadline and not os.path.exists(sock):
        if proc.poll() is not None:
            with open(os.path.join(dbdir, "redis.log")) as log:
                tail = log.read()[-2000:]
            raise AssertionError(f"embedded test daemon died:\n{tail}")
        time.sleep(0.05)
    assert os.path.exists(sock), "embedded test daemon never opened its socket"

    result = {"copies": 0, "attempts": 0, "errors": 0, "last_err": None,
              "hung": [], "forks": None, "clone_nodes": None,
              "swapped_nodes": None, "verify_err": None}
    try:
        from falkordb import FalkorDB
        db = FalkorDB(unix_socket_path=sock, socket_timeout=4)
        g = db.select_graph("src")
        g.query("CREATE (:P {i:1})")
        forks0 = _total_forks(db)
        time.sleep(4)  # let the holder thread start (it delays past startup)
        first_seen: dict[int, tuple[float, float]] = {}
        t0 = time.time()
        while time.time() - t0 < _RACE_SECONDS:
            try:
                g.copy("clone")
                result["copies"] += 1
                db.select_graph("clone").delete()
            except Exception as exc:  # a wedge surfaces here as a refusal/timeout
                result["errors"] += 1
                result["last_err"] = str(exc)
            result["attempts"] += 1
            children = _daemon_children(proc.pid)
            result["hung"] = _parked(children, first_seen, time.time())
            if result["hung"]:
                break
            time.sleep(0.05)
        # A wedge can land in the last fork of the window; a healthy copy
        # child lives milliseconds, so wait for any live child to either
        # finish or freeze before concluding anything.
        settle = time.time() + _SETTLE_S
        while not result["hung"] and time.time() < settle:
            children = _daemon_children(proc.pid)
            if not children:
                break
            result["hung"] = _parked(children, first_seen, time.time())
            if not result["hung"]:
                time.sleep(0.05)
        forks1 = _total_forks(db)
        result["forks"] = (None if forks0 is None or forks1 is None
                           else forks1 - forks0)
        if not result["hung"]:
            # (c) the legitimate path must still WORK, not merely not-hang.
            # First a plain copy, then the DR restore shape — live -> temp ->
            # live — which is the sequence the drill/backup flows perform and
            # the one that used to die with "could not fork".
            try:
                g.copy("verified")
                rows = db.select_graph("verified").query(
                    "MATCH (n) RETURN count(n)").result_set
                result["clone_nodes"] = int(rows[0][0]) if rows else None
                db.select_graph("src").copy("swap-temp")
                db.select_graph("src").delete()
                db.select_graph("swap-temp").copy("src")
                rows = db.select_graph("src").query(
                    "MATCH (n) RETURN count(n)").result_set
                result["swapped_nodes"] = int(rows[0][0]) if rows else None
            except Exception as exc:
                result["verify_err"] = f"{type(exc).__name__}: {exc}"
    finally:
        # Own-process hygiene only: kill the daemon WE started and the module
        # children carrying ITS pid as parent.  Never a fleet-wide sweep.
        for pid, _cpu in _daemon_children(proc.pid):
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)
        with contextlib.suppress(Exception):
            proc.kill()
            proc.wait(timeout=5)
        # the harness owns this dir (the daemon's socket + log); the failure
        # observables are already in `result`
        shutil.rmtree(dbdir, ignore_errors=True)
    return result


@pytest.fixture(scope="module")
def holder():
    tmp = tempfile.mkdtemp(prefix="fork-safety-holder-")
    try:
        yield _build_holder(__import__("pathlib").Path(tmp))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_fork_child_does_not_hang_with_the_production_config(holder):
    """(b)+(c) — with the fork-safe verbosity the same race produces NO parked
    module-fork child, no fork refusal, and the copy still copies."""
    dbdir = tempfile.mkdtemp(prefix="fork-safety-fixed-")
    result = _race(holder, fork_safe_serverconfig(), dbdir)
    assert result["hung"] == [], (
        f"module-fork child parked {result['hung']} with the fork-safe "
        f"config {fork_safe_serverconfig()!r}; last error "
        f"{result['last_err']!r}")
    # non-vacuity: this asserts the fork path RAN, from redis's own counter,
    # so a green result cannot come from a copy that never forked.
    assert result["forks"] is None or result["forks"] >= result["copies"] > 0, (
        f"the fork path did not run: {result}")
    assert result["copies"] > 0, f"the race never ran: {result}"
    assert result["last_err"] is None, result
    assert result["verify_err"] is None, result
    assert result["clone_nodes"] == 1, result
    assert result["swapped_nodes"] == 1, result


def test_without_the_fix_the_same_race_hangs_a_child(holder):
    """(a)+(d) — MUTATION PROOF.

    The only difference from the test above is the daemon's verbosity (the
    config fragment is absent), and a real module-fork child parks forever,
    exactly as the fleet's leaked children did.  This is also the harness-
    capability proof: the green test above cannot be green because the race
    never happened — the fork counter shows ~70 forks happened there too.
    """
    dbdir = tempfile.mkdtemp(prefix="fork-safety-unfixed-")
    result = _race(holder, None, dbdir)
    assert result["hung"], (
        "held the timezone rwlock and still no module-fork child parked — "
        f"the reproduction is not exercising the producer: {result}")
    assert result["forks"] is None or result["forks"] >= 1, result
    assert result["attempts"] > 0, result
    # the parked child holds redis's single module-fork slot, so the copies
    # stop working — the fleet's own symptom, in either of its two shapes
    # (a refused GRAPH.COPY, or a client command stalled on the blocked
    # server).
    assert result["errors"] > 0, result
    assert result["last_err"], result


def test_embedded_choke_point_runs_at_the_proven_fork_safe_level():
    """Wiring — the daemon ``tortoise.FalkorDB`` starts is the one the race
    test above proved safe.

    Compared against ``fork_safe_serverconfig()`` (the level the live race
    test uses), not against a literal, so the assertion cannot drift into
    restating an implementation constant.
    """
    import tortoise

    dbdir = tempfile.mkdtemp(prefix="fork-safety-wire-")
    db = tortoise.FalkorDB(os.path.join(dbdir, "redis.db"))
    try:
        effective = db.execute_command("CONFIG", "GET", "loglevel")
        value = effective[1] if isinstance(effective, (list, tuple)) else None
        assert value == fork_safe_serverconfig()["loglevel"], (
            "the embedded daemon our choke point starts is not at the level "
            "the #3845 race test proved safe")
        # a daemon that was ALREADY running (redislite registry reuse) is the
        # path serverconfig cannot reach — the live re-assert must cover it
        assert enforce_embedded_fork_safety(db) is True
        again = db.execute_command("CONFIG", "GET", "loglevel")
        value2 = again[1] if isinstance(again, (list, tuple)) else None
        assert value2 == fork_safe_serverconfig()["loglevel"]
    finally:
        with contextlib.suppress(Exception):
            db.close()
        shutil.rmtree(dbdir, ignore_errors=True)


def test_env_override_is_honoured_and_loud(monkeypatch):
    """``TORTOISE_EMBEDDED_LOGLEVEL`` can restore NOTICE logs for debugging —
    and says plainly that doing so re-arms #3845."""
    from tortoise.fork_safety import embedded_loglevel
    monkeypatch.setenv("TORTOISE_EMBEDDED_LOGLEVEL", "notice")
    assert embedded_loglevel() == "notice"
    monkeypatch.setenv("TORTOISE_EMBEDDED_LOGLEVEL", "nonsense")
    assert embedded_loglevel() == FORK_SAFE_LOGLEVEL
