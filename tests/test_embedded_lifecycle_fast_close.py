"""Unit tests for the #1371 fast interpreter-exit close (ephemeral only).

Covers the gating (flag + ephemeral test-tree + last-client), the durability
contract (fire-and-forget SHUTDOWN SAVE persists the RDB — the cross-process
reopen classes depend on it), and the behavior-identical contract for
explicit close()/__exit__ (the close-recorder tests in this module).
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
from pathlib import Path

import pytest

from tortoise.embedded_lifecycle import atexit_fast_close


def _fresh_proj(prefix="tortoise_fast_close_"):
    """FalkorProjection on an ephemeral test-tree DB (mkdtemp under the
    tempdir with a reaper-recognized ephemeral prefix)."""
    from tortoise.projection import FalkorProjection
    db = os.path.join(tempfile.mkdtemp(prefix=prefix), "c.db")
    return FalkorProjection(db, graph_name="test")


def _client(proj):
    return getattr(proj.db, "client", proj.db)


def _pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


@pytest.fixture(autouse=True)
def _fast_flag():
    os.environ["TORTOISE_FAST_ATEXIT"] = "1"
    yield
    os.environ.pop("TORTOISE_FAST_ATEXIT", None)


def test_fast_close_ephemeral_flag_stops_server():
    proj = _fresh_proj()
    cli = _client(proj)
    pid = cli.pid
    assert _pid_alive(pid), "server should be running"
    handled = atexit_fast_close(cli)
    assert handled is True, "ephemeral + flag must take the fast path"
    # the bounded poll waits for death; small graphs die in ~0.04s
    deadline = time.time() + 8
    while _pid_alive(pid) and time.time() < deadline:
        time.sleep(0.05)
    assert not _pid_alive(pid), "fast close did not stop the server"


def test_fast_close_flag_unset_falls_through():
    os.environ.pop("TORTOISE_FAST_ATEXIT", None)
    proj = _fresh_proj()
    assert atexit_fast_close(_client(proj)) is False, \
        "unset flag must fall through to the normal close"
    proj.close()  # clean up


def test_fast_close_non_ephemeral_falls_through():
    # A user dir OUTSIDE the tempdir must never take the fast path (the
    # durability firewall — user-path servers keep redislite's SAVE close).
    from tortoise.projection import FalkorProjection
    user_dir = tempfile.mkdtemp(prefix="tortoise_fast_close_user_",
                                dir=str(Path.home()))
    try:
        db = os.path.join(user_dir, "user.db")
        proj = FalkorProjection(db, graph_name="test")
        assert atexit_fast_close(_client(proj)) is False, \
            "non-ephemeral path must fall through to the normal close"
        proj.close()
    finally:
        shutil.rmtree(user_dir, ignore_errors=True)


def test_reopen_durability_preserved_after_fast_close():
    """The #1371 P1 regression class: a server fast-closed at exit must have
    its data on disk — a later process reopening the same path sees it."""
    from tortoise.projection import FalkorProjection
    db = os.path.join(tempfile.mkdtemp(prefix="tortoise_fast_close_"), "c.db")
    p1 = FalkorProjection(db, graph_name="test")
    p1.g.query("CREATE (n:Point {id:'durable-x'})")
    assert atexit_fast_close(_client(p1)) is True
    deadline = time.time() + 8
    while _pid_alive(_client(p1).pid if hasattr(p1, 'db') else None) \
            and time.time() < deadline:
        time.sleep(0.05)
    p2 = FalkorProjection(db, graph_name="test")
    try:
        rows = p2.g.query(
            "MATCH (n:Point {id:'durable-x'}) RETURN count(n)").result_set
        assert rows and rows[0][0] >= 1, "data lost across fast close + reopen"
    finally:
        p2.close()


def test_exit_still_calls_close_once_with_flag_set():
    """Behavior-identical contract: __exit__ must keep calling close() once
    even with the flag set — the fast path is atexit-seam-only."""
    from tortoise import FalkorDB
    calls = []
    orig_close = FalkorDB.close

    def spy(self):
        calls.append("close")
        return orig_close(self)

    try:
        FalkorDB.close = spy
        proj = _fresh_proj()
        with proj:
            pass
    finally:
        FalkorDB.close = orig_close
    assert calls == ["close"], f"__exit__ must call close() once, got {calls}"


def test_atexit_seams_registered():
    """The three atexit seams route through _atexit_close — the fast path
    is registration-seam-only (never inside close/__exit__)."""
    from tortoise import FalkorDB
    from tortoise.sdk import TortoiseSDK
    from tortoise.projection import FalkorProjection

    # All three lifecycle classes expose the seam.
    assert hasattr(FalkorDB, "_atexit_close")
    assert hasattr(FalkorProjection, "_atexit_close")
    assert hasattr(TortoiseSDK, "_atexit_close")

    # FalkorProjection._atexit_close with the fast conditions -> fast path:
    # the server is stopped and the projection is marked closed WITHOUT the
    # (spied) close() being called.
    from tortoise.embedded_lifecycle import atexit_fast_close as _fast
    calls = []
    orig_close = FalkorProjection.close

    def spy_close(self):
        calls.append("close")
        return orig_close(self)

    proj = _fresh_proj()
    pid = _client(proj).pid
    try:
        FalkorProjection.close = spy_close
        proj._atexit_close()
    finally:
        FalkorProjection.close = orig_close
    assert proj._closed is True
    assert calls == [], f"fast path must not call close(), got {calls}"
    deadline = time.time() + 8
    while _pid_alive(pid) and time.time() < deadline:
        time.sleep(0.05)
    assert not _pid_alive(pid), "seam fast path did not stop the server"

    # Fall-through: with the flag unset, _atexit_close must call close().
    os.environ.pop("TORTOISE_FAST_ATEXIT", None)
    calls.clear()
    proj2 = _fresh_proj()
    try:
        FalkorProjection.close = spy_close  # re-apply spy
        proj2._atexit_close()
    finally:
        FalkorProjection.close = orig_close
    assert calls == ["close"], "flag unset -> seam must fall through to close()"
