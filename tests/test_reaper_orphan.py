"""Reaper orphan-reclassification tests — issue #1427 + #1642 FIX 3/5.

Covers: path-based servers (registry user dir + named db_filename — the
dominant leak class) whose registry-recorded owner pid is PROVABLY dead
reclassify as orphan candidates (previously protected forever, so leaked
test servers accumulated: 139-181 daemons). Live owners keep protection
(live data must not be killed); unresolvable owners fail closed.

#1642 FIX 3: redislite's registry pidfile is the server's OWN pid, so a
live server always reported 'owner alive' (the #1427 owner=self-pid
circularity) and orphaned path-based servers stayed protected forever.
A LIVE server now classifies 'candidate' after the boot cooldown, and
orphanhood is decided by reap()'s orphan-confirmation + double-checked
CLIENT LIST gates (tests here cover classification only). #1642 FIX 5: a
live NON-redis pid read from the registry is a recycled number — treated
as dead (stale_socket), never a protected owner.

Complementary to #1383 (dead-pid candidate reaping): #1427/#1642 make the
protected path-based class reach the candidate/stale path; #1383 makes the
dead-pid candidates' leftovers actually removable.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path  # noqa: F401

import pytest

from tortoise.embedded_reaper import (
    _classify,
    _real_gettempdir,  # noqa: F401
    discover,
    phase1_probe,
)

pytest.importorskip("redislite")


class monkeypatch_tempdir:
    """Context manager temporarily redirecting tempfile.gettempdir()
    (mirrors the same helper in tests/test_reaper.py)."""

    def __init__(self, tmp_path):
        self.tmp_path = str(tmp_path)
        self._orig = None

    def __enter__(self):
        import tempfile as tf
        self._orig = tf.gettempdir
        tf.gettempdir = lambda: self.tmp_path
        return self

    def __exit__(self, *exc):
        import tempfile as tf
        tf.gettempdir = self._orig
        return False


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _dead_pid() -> int:
    """Deterministic provably-dead pid: spawn a child and SIGKILL it.

    wait() reaps the child (no zombie), and the pid cannot be live until
    the OS reuses it — the check right after is the guard. More robust
    than a magic large pid (Linux pid_max can exceed 1M on CI).
    """
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    proc.kill()
    proc.wait()
    assert not _pid_alive(proc.pid), "spawned pid still alive after kill"
    return proc.pid


def _path_based_registry(sock_dir, dbdir, pidfile,
                         dbfilename: str = "leak.db") -> dict:
    """Registry dict for a path-based server: Signal 1 (non-ephemeral user
    dir) + Signal 2 (named db_filename) both fire."""
    return {"dir": str(dbdir), "dbfilename": dbfilename,
            "pidfile": str(pidfile)}


def _fabricate_path_based_dir(tmp_path, pid, sub="redislite_orphan"):
    """Socket dir under the (monkeypatched) tempdir holding a redis.config
    describing a path-based server. Returns (sock_dir, socket_path)."""
    sock_dir = tmp_path / sub
    sock_dir.mkdir()
    socket_path = sock_dir / "redis.socket"
    socket_path.write_text("")
    pidfile = sock_dir / "redis.pid"
    pidfile.write_text(str(pid))
    dbdir = str(tmp_path / "user-data-dir")  # non-ephemeral user dir
    (sock_dir / "redis.config").write_text(
        "dbfilename 'leak.db'\n"  # noqa: UP031
        "dir '%s'\n"
        "pidfile '%s'\n"
        "unixsocket '%s'\n" % (dbdir, pidfile, socket_path))
    return sock_dir, socket_path


# ── unit: _classify ─────────────────────────────────────────────────

def test_classify_path_based_dead_owner_is_orphan_candidate(tmp_path):
    """#1427: path-based server whose registry-recorded owner pid is
    provably dead -> orphan, not protected.

    #1383: the dead-pid classification is now 'stale_socket' (honest label —
    a dead-pid leftover dir has no live server, so it is reapable via the
    guarded-rmtree action _remove_stale_socket_dir, never a killable
    'candidate'). The #1427 contract — dead owner -> reapable, NOT
    protected — is unchanged.
    """
    sock_dir = tmp_path / "redislite_sock"
    sock_dir.mkdir()
    pidfile = sock_dir / "redis.pid"
    dead = _dead_pid()
    pidfile.write_text(str(dead))
    dbdir = str(tmp_path / "user-data-dir")
    registry = _path_based_registry(sock_dir, dbdir, pidfile)
    assert _classify(str(sock_dir), dbdir, str(tmp_path), registry) \
        == "stale_socket"


def test_classify_path_based_live_owner_stays_protected(tmp_path, monkeypatch):
    """#1642 FIX 3/#1427 contract: a path-based server whose registry
    pidfile names a LIVE process is no longer auto-'protected' — the
    registry pidfile is the server's OWN pid (redislite), so the old #1427
    'owner alive' check was circular (every live server protected itself
    forever). The new contract:
      - a LIVE REDIS pid (the real server) -> candidate; orphanhood is
        decided by reap()'s orphan-confirmation + CLIENT LIST gates.
      - a LIVE NON-redis pid (a recycled number) -> stale_socket (FIX 5).
    This test pins the recycled-pid direction: the fabricated 'owner' pid is
    this live python process — provably NOT the recorded server, so the
    leftover is reapable (guarded rmtree), never protected forever.
    """
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        sock_dir = tmp_path / "redislite_sock_live"
        sock_dir.mkdir()
        pidfile = sock_dir / "redis.pid"
        pidfile.write_text(str(proc.pid))
        dbdir = str(tmp_path / "user-data-dir")
        registry = _path_based_registry(sock_dir, dbdir, pidfile)
        assert _classify(str(sock_dir), dbdir, str(tmp_path), registry) \
            == "stale_socket"  # live but non-redis = recycled (#1642 FIX 5)
    finally:
        proc.kill()
        proc.wait()


def test_classify_path_based_live_redis_server_is_candidate(tmp_path, monkeypatch):
    """#1642 FIX 3: a path-based server whose registry pidfile is a LIVE
    REDIS process (the server itself — redislite writes its own pid to
    redis.pid) classifies 'candidate' after the boot cooldown, NOT
    'protected' — the #1427 owner=self-pid circularity is gone. Safety
    moved to reap()'s gates (orphan confirmation + double-checked CLIENT
    LIST), which this test does not exercise (classification only).
    """
    monkeypatch.setenv("TORTOISE_REAPER_MIN_UPTIME", "0")
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        sock_dir = tmp_path / "redislite_sock_live_redis"
        sock_dir.mkdir()
        pidfile = sock_dir / "redis.pid"
        pidfile.write_text(str(proc.pid))
        dbdir = str(tmp_path / "user-data-dir")
        registry = _path_based_registry(sock_dir, dbdir, pidfile)
        # The pidfile names a real redis-server (monkeypatched: the process
        # IS a redis-server for the purpose of this unit classification).
        monkeypatch.setattr("tortoise.embedded_reaper._pid_is_redis",
                            lambda pid: pid == proc.pid)
        # pid passed like _classify_dir derives it (discover always resolves
        # the registry pidfile before classification).
        assert _classify(str(sock_dir), dbdir, str(tmp_path), registry,
                         pid=proc.pid) == "candidate"
    finally:
        proc.kill()
        proc.wait()


def test_classify_path_based_unresolvable_owner_fails_closed(tmp_path):
    """#1427: pidfile missing/unreadable -> owner unknown -> protected
    (fail closed — never risk killing a server whose owner is unknown)."""
    sock_dir = tmp_path / "redislite_sock_unresolved"
    dbdir = str(tmp_path / "user-data-dir")
    registry = _path_based_registry(
        sock_dir, dbdir, sock_dir / "missing.pid")
    assert _classify(str(sock_dir), dbdir, str(tmp_path), registry) \
        == "protected"


# ── integration: discover ───────────────────────────────────────────

def test_discover_classifies_orphaned_path_based_server_as_candidate(tmp_path):
    """#1427: a leaked path-based server (registry dir + named db_filename,
    dead owner pid) is discovered as an orphan — never 'protected', so the
    sweep's reap path can act on it. #1383: dead-pid leftovers classify
    'stale_socket' (guarded-rmtree reapable), not 'candidate' (killable)."""
    sock_dir, _socket_path = _fabricate_path_based_dir(tmp_path, _dead_pid())
    with monkeypatch_tempdir(tmp_path):
        found = discover()
    matches = [s for s in found if str(sock_dir) in s.get("dbdir", "")]
    assert matches, "orphaned path-based server not discovered"
    assert matches[0]["classification"] == "stale_socket"


def test_discover_path_based_live_owner_recycled_pid_is_stale(tmp_path):
    """#1642 FIX 3/5: the same fabricated path-based server with a LIVE
    NON-redis pid (a recycled number — the fabricated python 'owner') is
    discovered as stale_socket, never protected: an alive-but-not-redis pid
    is provably not the recorded server. A REAL live redis-server (the
    true #1427 case) classifies candidate; reap()'s confirmation + CLIENT
    LIST gates then decide the kill."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        sock_dir, _socket_path = _fabricate_path_based_dir(tmp_path, proc.pid)
        with monkeypatch_tempdir(tmp_path):
            found = discover()
        matches = [s for s in found if str(sock_dir) in s.get("dbdir", "")]
        assert matches, "path-based server not discovered"
        assert matches[0]["classification"] == "stale_socket"
    finally:
        proc.kill()
        proc.wait()


# ── phase1 handoff (complementary to #1383) ─────────────────────────

def test_phase1_resolves_dead_owner_candidate_to_stale_socket(tmp_path):
    """#1427 -> #1383 handoff: an orphaned path-based server classifies as
    candidate; phase1's socket probe resolves the dead-pid record to
    'stale_socket' — the leftover dir #1383's guarded rmtree removes."""
    dead = _dead_pid()
    sock_dir, socket_path = _fabricate_path_based_dir(tmp_path, dead)
    record = {
        "pid": dead,
        "socket_path": os.path.realpath(str(socket_path)),
        "dbdir": os.path.realpath(str(sock_dir)),
        "classification": "candidate",
    }
    updated = phase1_probe(record)
    assert updated["classification"] in ("stale_socket", "undetermined")
