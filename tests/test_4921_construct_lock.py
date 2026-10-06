"""#4921: the per-`<dbdir>/<dbfilename>` construction lock.

The gap: `RedisMixin.__init__` decides a server is not running
(`client.py:449`) and then starts one (`client.py:462`) with no lock between
the two, so two concurrent constructions of one `<dbdir>/<dbfilename>` can each
bring up a server over the SAME RDB — two writers on one file.

These tests pin the three properties the seam forces (reentrant, cross-process,
per-key) plus the key derivation, which is the part that decides WHAT is
serialised. Every wait is bounded, so a regression fails the test instead of
hanging the suite.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.embedded_lifecycle import (
    _CONSTRUCT_LOCKS,
    _construction_key,
    _construction_lock,
    _open_construct_lock,
)


def _key(tmp_path, name="db.rdb"):
    return str(tmp_path / name)


# ── key derivation: WHAT gets serialised ───────────────────────────────────


def test_key_uses_the_positional_db_filename(tmp_path):
    db = str(tmp_path / "db.rdb")
    assert _construction_key((db,), {}) == os.path.realpath(db)


def test_key_uses_the_dbfilename_kwarg(tmp_path):
    db = str(tmp_path / "db.rdb")
    assert _construction_key((), {"dbfilename": db}) == os.path.realpath(db)


def test_key_anchors_a_bare_name_to_the_cwd(tmp_path, monkeypatch):
    """redislite joins a bare `dbfilename` to the cwd (client.py:441)."""
    monkeypatch.chdir(tmp_path)
    assert _construction_key(("db.rdb",), {}) == os.path.realpath(
        os.path.join(str(tmp_path), "db.rdb"))


def test_key_resolves_two_spellings_of_one_file_to_one_lock(tmp_path):
    """A symlinked spelling must not get its own lock — that is the bug."""
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    via_real = _construction_key((str(real / "db.rdb"),), {})
    via_link = _construction_key((str(link / "db.rdb"),), {})
    assert via_real == via_link


@pytest.mark.parametrize("kwargs", [{"host": "127.0.0.1"}, {"port": 6379}])
def test_key_is_none_for_an_external_redis(kwargs, tmp_path):
    """`host`/`port` names a server we never start (client.py:407-412)."""
    assert _construction_key((str(tmp_path / "db.rdb"),), kwargs) is None


def test_key_is_none_when_no_db_file_is_named():
    """No db file: redislite mints its own `mkdtemp()`, unshareable by design."""
    assert _construction_key((), {}) is None
    assert _construction_key((None,), {}) is None
    assert _construction_key((), {"dbfilename": ""}) is None


# ── property 1: reentrant (the constructor nests) ──────────────────────────


def test_lock_is_reentrant_in_one_thread(tmp_path):
    key = _key(tmp_path)
    with _construction_lock(key):
        entry = _CONSTRUCT_LOCKS[key]
        assert entry.depth == 1
        assert entry.fd >= 0, "the flock must be taken at the 0->1 transition"
        with _construction_lock(key):          # the nesting must not deadlock
            assert entry.depth == 2, "the flock must NOT be retaken while nested"
        assert entry.depth == 1, "the flock must survive the inner release"
        assert entry.fd >= 0
    assert entry.depth == 0
    assert entry.fd == -1, "the flock must be released at the 1->0 transition"


def test_lock_releases_the_fd_exactly_once(tmp_path):
    """A leaked fd per construction is the failure this pins."""
    key = _key(tmp_path)
    for _ in range(3):
        with _construction_lock(key):
            pass
    assert _CONSTRUCT_LOCKS[key].fd == -1
    assert _CONSTRUCT_LOCKS[key].depth == 0


# ── property 2: mutual exclusion, in-process and across processes ──────────


def test_lock_excludes_a_second_thread(tmp_path):
    key = _key(tmp_path)
    inside = threading.Event()
    finished = threading.Event()

    def worker():
        with _construction_lock(key):
            inside.set()
            finished.wait(10)

    with _construction_lock(key):
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        assert not inside.wait(0.5), "a second thread entered while the lock was held"
    assert finished.is_set() is False
    finished.set()
    assert inside.wait(10), "the second thread never acquired the released lock"


def test_distinct_keys_do_not_serialise(tmp_path):
    """A global lock would pass every test above and fail this one."""
    a, b = _key(tmp_path, "a.rdb"), _key(tmp_path, "b.rdb")
    entered = threading.Event()

    def worker():
        with _construction_lock(b):
            entered.set()

    with _construction_lock(a):
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        assert entered.wait(10), "an unrelated key was serialised behind this one"
    t.join(5)


_CHILD = """
import sys, time
sys.path.insert(0, {root!r})
from tortoise.embedded_lifecycle import _construction_lock
with _construction_lock({key!r}):
    sys.stdout.write("held\\n")
    sys.stdout.flush()
    time.sleep({hold})
"""


def test_lock_excludes_another_process(tmp_path):
    """The race is CROSS-PROCESS; an in-process-only lock cannot close it."""
    key = _key(tmp_path)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    child = subprocess.Popen(
        [sys.executable, "-c", _CHILD.format(root=root, key=key, hold=3)],
        stdout=subprocess.PIPE, text=True,
        env={**os.environ, "PYTHONPATH": root})
    try:
        assert child.stdout.readline().strip() == "held", "the child never took the lock"
        start = time.monotonic()
        with _construction_lock(key):
            elapsed = time.monotonic() - start
        assert elapsed > 1.0, (
            f"acquired the lock in {elapsed:.2f}s while another process held it — "
            "the flock is not engaging")
    finally:
        child.terminate()
        child.wait(10)


# ── the seam: the installer wraps the constructor that contains the gap ────


def test_installer_wraps_the_constructor_and_is_idempotent():
    import tortoise  # noqa: F401  (arms the redislite guard installer)
    redislite_client = pytest.importorskip("redislite.client")
    mixin = redislite_client.RedisMixin
    assert getattr(mixin, "_tortoise_construct_lock_guard", False), (
        "the construction lock is not installed — #4921's gap is open")
    first = mixin.__init__
    from tortoise.embedded_lifecycle import _install_construct_lock_guard
    _install_construct_lock_guard()
    assert mixin.__init__ is first, "a second install re-wrapped the constructor"


def test_missing_directory_yields_no_lock_instead_of_raising(tmp_path):
    """A gone dbdir is #3653's refusal to make, by name, not this guard's."""
    gone = str(tmp_path / "does-not-exist" / "db.rdb")
    assert _open_construct_lock(gone) == -1
