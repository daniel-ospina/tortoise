"""Per-session index lock (#280 item 1) — flock+PID hybrid semantics.

Covers: exclusive acquisition, same-process contention (flock is per open
file description, so a second open() in the same process contends), stale
attribution via kill-0, age-based force release (>10 min, dead/unreadable
holder), live-holder non-eviction, sanitization, and context-manager use.
"""
from __future__ import annotations

import os
import time

import pytest

from tortoise.index_lock import (
    SessionIndexLock,
    lock_path_for,
    sanitize_session_id,
)


@pytest.fixture
def lock_dir(tmp_path):
    d = tmp_path / "locks"
    d.mkdir()
    return str(d)


def test_lock_path_contract(lock_dir):
    """Issue path contract: ~/.tortoise/index-{session_id}.pid."""
    p = lock_path_for("abc123", lock_dir)
    assert p.name == "index-abc123.pid"
    assert str(p.parent) == lock_dir


def test_sanitize_session_id_blocks_traversal():
    assert sanitize_session_id("../../etc/passwd") == "etc_passwd"
    assert sanitize_session_id("..") == "session"
    assert sanitize_session_id("") == "session"
    assert sanitize_session_id("  ") == "session"
    assert sanitize_session_id("a/b\\c:d") == "a_b_c_d"
    assert sanitize_session_id("ok-session_1.x") == "ok-session_1.x"


def test_acquire_release(lock_dir):
    lock = SessionIndexLock("sess-1", lock_dir)
    assert lock.acquire() == "acquired"
    assert lock.path.exists()
    assert int(lock.path.read_text().split()[0]) == os.getpid()
    lock.release()
    # Release is idempotent
    lock.release()


def test_second_open_contends(lock_dir):
    """flock is per open-file-description: a second open() contends even in
    the same process — mirrors two concurrent writer processes."""
    a = SessionIndexLock("sess-2", lock_dir)
    b = SessionIndexLock("sess-2", lock_dir)
    assert a.acquire() == "acquired"
    assert b.acquire() == "held"
    assert b.held_by()["pid"] == os.getpid()
    assert b.held_by()["alive"] is True
    a.release()
    # After release the second contender acquires cleanly
    assert b.acquire() == "acquired"
    b.release()


def test_live_holder_never_evicted_by_age(lock_dir, monkeypatch):
    """A lock held by a live process is reported held even past 10 min —
    align decision P3: never evict live holders."""
    a = SessionIndexLock("sess-3", lock_dir, stale_age_seconds=600)
    b = SessionIndexLock("sess-3", lock_dir, stale_age_seconds=600)
    assert a.acquire() == "acquired"
    # Fake the file as > 10 min old
    old = time.time() - 700
    os.utime(a.path, (old, old))
    assert b.acquire() == "held"
    assert "alive" in b.detail
    a.release()


def test_stale_pid_recovered(lock_dir):
    """Dead-holder attribution: flock is kernel-released on death, so a
    failed acquire whose recorded PID is gone recovers on retry."""
    a = SessionIndexLock("sess-4", lock_dir)
    assert a.acquire() == "acquired"
    # Simulate holder death: release the flock (kernel behavior) but leave a
    # pid for a process that no longer exists.
    a.release()
    a.path.write_text("999999999 0\n")  # PID 999999999 is dead
    b = SessionIndexLock("sess-4", lock_dir)
    assert b.acquire() == "stale-recovered"
    assert b.held_by()["pid"] == os.getpid()
    b.release()


def test_dead_pid_with_old_age_force_released(lock_dir):
    """Dead holder + old file → the lock is force-released and re-acquired
    (E2E-13 observable recovery)."""
    a = SessionIndexLock("sess-5", lock_dir)
    assert a.acquire() == "acquired"
    a.release()
    a.path.write_text("999999999 0\n")  # dead pid
    old = time.time() - 700
    os.utime(a.path, (old, old))
    b = SessionIndexLock("sess-5", lock_dir, stale_age_seconds=600)
    assert b.acquire() == "stale-recovered"
    b.release()


def test_unreadable_pid_fresh_is_not_stale(lock_dir):
    """Garbage attribution with a fresh file is not stale (age is the
    fallback only) — the lock is simply taken as acquired."""
    a = SessionIndexLock("sess-6", lock_dir)
    assert a.acquire() == "acquired"
    a.release()
    a.path.write_text("not-a-pid\n")
    b = SessionIndexLock("sess-6", lock_dir)
    assert b.acquire() == "acquired"
    b.release()


def test_unreadable_pid_old_is_stale(lock_dir):
    """Garbage attribution past the age threshold → stale recovery
    (the align P3 attribution fallback)."""
    a = SessionIndexLock("sess-6b", lock_dir)
    assert a.acquire() == "acquired"
    a.release()
    a.path.write_text("not-a-pid\n")
    old = time.time() - 700
    os.utime(a.path, (old, old))
    b = SessionIndexLock("sess-6b", lock_dir, stale_age_seconds=600)
    assert b.acquire() == "stale-recovered"
    b.release()


def test_force_release_refuses_live_holder(lock_dir):
    a = SessionIndexLock("sess-7", lock_dir)
    assert a.acquire() == "acquired"
    assert a.force_release() is False  # live holder — refuse
    assert a.path.exists()
    a.release()


def test_force_release_removes_stale(lock_dir):
    a = SessionIndexLock("sess-8", lock_dir)
    assert a.acquire() == "acquired"
    a.release()
    a.path.write_text("999999999 0\n")  # dead holder
    b = SessionIndexLock("sess-8", lock_dir)
    assert b.force_release() is True
    assert not b.path.exists()


def test_context_manager(lock_dir):
    with SessionIndexLock("sess-9", lock_dir) as lock:
        assert lock.status == "acquired"
        # Re-entrant contention during the with-block
        other = SessionIndexLock("sess-9", lock_dir)
        assert other.acquire() == "held"
        other.release()
    # Released after the block
    again = SessionIndexLock("sess-9", lock_dir)
    assert again.acquire() == "acquired"
    again.release()


def test_sanitized_id_uses_same_file(lock_dir):
    """Unsafe IDs collapse to the same sanitized lock (composition safety)."""
    p1 = lock_path_for("../evil", lock_dir)
    p2 = lock_path_for("evil", lock_dir)
    assert p1 == p2
    assert ".." not in str(p1)


def test_sanitize_caps_length():
    """A hostile 300-char frontmatter sessionId must not raise ENAMETOOLONG
    (would abort the batch sweep / crash the hook — exit-0 contract)."""
    long_id = "x" * 300
    assert len(sanitize_session_id(long_id)) == 128
    # and a lock with the long ID acquires without error
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        lk = SessionIndexLock(long_id, lock_dir=d)
        assert lk.acquire() == "acquired"
        assert len(lk.path.name) <= 128 + len("index-.pid")
        lk.release()


# ── #280 review P2: security (symlink) + force_release TOCTOU ────────


def test_acquire_refuses_planted_symlink(lock_dir, tmp_path):
    """Security (#280 review P2): a symlink planted at the lock path must
    never be followed — acquire() surfaces a retryable OSError (ELOOP from
    O_NOFOLLOW) and the attacker-chosen target is never truncated/overwritten.
    """
    import errno
    target = tmp_path / "victim.txt"
    target.write_text("precious data")
    lock = SessionIndexLock("sess-symlink", lock_dir)
    lock.path.symlink_to(target)
    with pytest.raises(OSError) as ei:
        lock.acquire()
    assert ei.value.errno == errno.ELOOP
    # The target file content is intact — no truncate-through-symlink.
    assert target.read_text() == "precious data"


def test_lock_dir_created_private(tmp_path):
    """Security (#280 review P2): a freshly created lock dir is 0700 so no
    other local user can plant symlinks inside it."""
    d = tmp_path / "a" / "b"  # parent chain does not exist yet
    lock = SessionIndexLock("sess-dirmode", d)
    assert lock.acquire() == "acquired"
    try:
        assert (os.stat(d).st_mode & 0o777) == 0o700
    finally:
        lock.release()


def test_force_release_race_contender_not_evicted(lock_dir):
    """TOCTOU regression (#280 review P2): a contender that acquires the
    flock between force_release's stale probe and its unlink must NOT be
    evicted — the check-then-unlink window is closed by flock-first.
    """
    import fcntl
    a = SessionIndexLock("sess-race", lock_dir)
    assert a.acquire() == "acquired"
    a.release()
    a.path.write_text("999999999 0\n")  # stale attribution (dead pid)
    # Contender acquires the lock, but its attribution write has not landed
    # yet — the exact TOCTOU window where the file still reads stale/dead
    # while a live flock holder owns the lock.
    fd = os.open(a.path, os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        b = SessionIndexLock("sess-race", lock_dir)
        assert b.force_release() is False   # live contender — refuse
        assert b.path.exists()              # live holder's lock NOT unlinked
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def test_acquire_inode_guard_detects_replaced_path(lock_dir):
    """Review follow-up P2: a contender whose open() predates a force_release
    unlink must not trust a flock won on the dead inode (double-hold with a
    fresh acquirer). acquire() verifies the locked inode still matches the
    lock path and reports the lock as held instead.
    """
    import fcntl
    a = SessionIndexLock("sess-dbl", lock_dir)
    assert a.acquire() == "acquired"
    a.release()
    a.path.write_text("999999999 0\n")  # stale
    # Contender C opens the file BEFORE force_release unlinks it (dead inode).
    fd_c = os.open(a.path, os.O_RDWR | os.O_NOFOLLOW)
    f = SessionIndexLock("sess-dbl", lock_dir)
    assert f.force_release() is True
    assert not a.path.exists()
    # A fresh acquirer D owns the path again (new inode).
    d = SessionIndexLock("sess-dbl", lock_dir)
    assert d.acquire() == "acquired"
    try:
        # C's flock on the dead inode succeeds (flock is inode-scoped)...
        fcntl.flock(fd_c, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # ...but the inode guard must refuse to treat it as the lock file.
        c = SessionIndexLock("sess-dbl", lock_dir)
        c._fh = os.fdopen(fd_c, "r+b", buffering=0)
        assert c._fh_matches_path() is False
        # End-to-end: acquire() (which reopens the path) reports held while
        # D owns the lock — never a second "acquired".
        assert c.acquire() == "held"
        assert "replaced" not in (c.detail or "")
    finally:
        try:
            fcntl.flock(fd_c, fcntl.LOCK_UN)
        except OSError:
            pass


def test_lock_file_created_private(lock_dir):
    """Review follow-up P2: the lock file itself is chmod 0600 (pid +
    timestamp observability content stays private to the owner)."""
    lock = SessionIndexLock("sess-priv", lock_dir)
    assert lock.acquire() == "acquired"
    try:
        assert (os.stat(lock.path).st_mode & 0o777) == 0o600
    finally:
        lock.release()
