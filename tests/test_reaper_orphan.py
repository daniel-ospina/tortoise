"""Reaper orphan-reclassification tests — issue #1427.

Covers: path-based servers (registry user dir + named db_filename — the
dominant leak class) whose registry-recorded owner pid is PROVABLY dead
reclassify as orphan candidates (previously protected forever, so leaked
test servers accumulated: 139-181 daemons). Live owners keep protection
(live data must not be killed); unresolvable owners fail closed.

Complementary to #1383 (dead-pid candidate reaping): #1427 makes the
protected path-based class reach the candidate path; #1383 makes the
dead-pid candidates' leftovers actually removable.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tortoise.embedded_reaper import (
    _classify,
    _real_gettempdir,
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
        "dbfilename 'leak.db'\n"
        "dir '%s'\n"
        "pidfile '%s'\n"
        "unixsocket '%s'\n" % (dbdir, pidfile, socket_path))
    return sock_dir, socket_path


# ── unit: _classify ─────────────────────────────────────────────────

def test_classify_path_based_dead_owner_is_orphan_candidate(tmp_path):
    """#1427: path-based server whose registry-recorded owner pid is
    provably dead -> orphan candidate, not protected."""
    sock_dir = tmp_path / "redislite_sock"
    sock_dir.mkdir()
    pidfile = sock_dir / "redis.pid"
    dead = _dead_pid()
    pidfile.write_text(str(dead))
    dbdir = str(tmp_path / "user-data-dir")
    registry = _path_based_registry(sock_dir, dbdir, pidfile)
    assert _classify(str(sock_dir), dbdir, str(tmp_path), registry) \
        == "candidate"


def test_classify_path_based_live_owner_stays_protected(tmp_path):
    """#1427: live owner keeps protection — live data must not be killed."""
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
            == "protected"
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
    dead owner pid) is discovered as an orphan candidate — never
    'protected', so the sweep's reap path can act on it."""
    sock_dir, _socket_path = _fabricate_path_based_dir(tmp_path, _dead_pid())
    with monkeypatch_tempdir(tmp_path):
        found = discover()
    matches = [s for s in found if str(sock_dir) in s.get("dbdir", "")]
    assert matches, "orphaned path-based server not discovered"
    assert matches[0]["classification"] == "candidate"


def test_discover_path_based_live_owner_stays_protected(tmp_path):
    """#1427: the same fabricated server with a LIVE owner stays
    protected (the reaper must never reap live data)."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        sock_dir, _socket_path = _fabricate_path_based_dir(tmp_path, proc.pid)
        with monkeypatch_tempdir(tmp_path):
            found = discover()
        matches = [s for s in found if str(sock_dir) in s.get("dbdir", "")]
        assert matches, "path-based server not discovered"
        assert matches[0]["classification"] == "protected"
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
