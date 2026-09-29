"""#4069 — per-test tempfile teardown (`tests/_tmpdir_hygiene.py`).

Order-independent: every test drives the tracker context manager directly
(pytest-randomly may reorder a module, so no test may depend on another's
teardown having run).
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests._tmpdir_hygiene import (
    TrackedTempfileArtifacts,
    _protected_reason,
)


def test_tracker_removes_a_created_directory():
    with TrackedTempfileArtifacts() as tracker:
        path = tempfile.mkdtemp(prefix="ask_hygiene_probe_")
        assert os.path.isdir(path)
        assert path in tracker.created
    assert not os.path.exists(path)


def test_tracker_removes_nested_content():
    with TrackedTempfileArtifacts():
        path = tempfile.mkdtemp(prefix="tortoise_hygiene_probe_")
        nested = os.path.join(path, "a", "b")
        os.makedirs(nested)
        with open(os.path.join(nested, "payload.bin"), "wb") as fh:
            fh.write(b"x" * 1024)
    assert not os.path.exists(path)


def test_tracker_removes_every_directory_it_tracked():
    with TrackedTempfileArtifacts() as tracker:
        paths = [tempfile.mkdtemp(prefix=f"ask_hygiene_{i}_")
                 for i in range(5)]
    assert len(tracker.created) == 5
    assert all(not os.path.exists(p) for p in paths)


def test_tracker_restores_the_original_mkdtemp():
    original = tempfile.mkdtemp
    with TrackedTempfileArtifacts():
        assert tempfile.mkdtemp is not original
    assert tempfile.mkdtemp is original


def test_tracker_is_reentrant_and_restores_the_outer_patch():
    original = tempfile.mkdtemp
    with TrackedTempfileArtifacts() as outer:
        with TrackedTempfileArtifacts() as inner:
            path = tempfile.mkdtemp(prefix="ask_hygiene_nested_")
            assert path in inner.created
        # inner exit restored the OUTER tracker, not the original
        assert tempfile.mkdtemp is not original
        assert path in outer.created
    assert tempfile.mkdtemp is original
    assert not os.path.exists(path)


def test_tracker_is_idempotent_when_the_dir_is_already_gone():
    with TrackedTempfileArtifacts() as tracker:
        path = tempfile.mkdtemp(prefix="ask_hygiene_gone_")
        import shutil
        shutil.rmtree(path)  # simulate a concurrent reaper / the test itself
    assert not os.path.exists(path)
    assert path in tracker.created  # exit still ran cleanly


def test_tracker_leaves_a_live_server_directory_in_place():
    with TrackedTempfileArtifacts() as tracker:
        path = tempfile.mkdtemp(prefix="ask_hygiene_live_")
        with open(os.path.join(path, "redis.pid"), "w") as fh:
            fh.write(str(os.getpid()))  # a live pid (this process)
    # left for the reaper rather than removed under a running server
    assert os.path.isdir(path)
    assert len(tracker.skipped_live) == 1
    skipped_path, reason = tracker.skipped_live[0]
    assert skipped_path == path
    assert "live redis pid" in reason
    os.remove(os.path.join(path, "redis.pid"))
    os.rmdir(path)


def test_tracker_removes_a_dir_whose_pid_is_dead():
    import subprocess
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    with TrackedTempfileArtifacts():
        path = tempfile.mkdtemp(prefix="ask_hygiene_dead_")
        with open(os.path.join(path, "redis.pid"), "w") as fh:
            fh.write(str(proc.pid))
    assert not os.path.exists(path)


def test_tracker_fails_closed_on_an_unparseable_pid():
    """A corrupt/partially-written redis.pid must not expose the dir."""
    with TrackedTempfileArtifacts() as tracker:
        path = tempfile.mkdtemp(prefix="ask_hygiene_corrupt_")
        with open(os.path.join(path, "redis.pid"), "w") as fh:
            fh.write("not-a-pid")
    assert os.path.isdir(path)  # left for the reaper, never removed
    assert [p for p, _ in tracker.skipped_live] == [path]
    os.remove(os.path.join(path, "redis.pid"))
    os.rmdir(path)


def test_protected_reason_is_none_only_without_a_pid_file():
    with TrackedTempfileArtifacts():
        path = tempfile.mkdtemp(prefix="ask_hygiene_pid_")
        assert _protected_reason(path) is None
        with open(os.path.join(path, "redis.pid"), "w") as fh:
            fh.write("not-a-pid")
        assert _protected_reason(path) is not None  # fail closed


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses mode bits")
def test_tracker_fails_closed_on_an_unreadable_pid():
    """The distinct `except OSError` branch (declared threat class 3)."""
    with TrackedTempfileArtifacts() as tracker:
        path = tempfile.mkdtemp(prefix="ask_hygiene_unreadable_")
        pid_file = os.path.join(path, "redis.pid")
        with open(pid_file, "w") as fh:
            fh.write(str(os.getpid()))
        os.chmod(pid_file, 0o000)
    try:
        assert os.path.isdir(path)  # left for the reaper, never removed
        assert [p for p, _ in tracker.skipped_live] == [path]
    finally:
        os.chmod(pid_file, 0o600)
        os.remove(pid_file)
        os.rmdir(path)


def test_tracker_fails_closed_on_a_non_regular_pid_file():
    """A `redis.pid` that exists but is not a regular file must not expose the
    directory: `os.path.isfile` returns False for a FIFO, which read as "no
    pid file" (review finding on this PR; declared threat class 3)."""
    if not hasattr(os, "mkfifo"):
        pytest.skip("mkfifo is POSIX-only")
    with TrackedTempfileArtifacts() as tracker:
        path = tempfile.mkdtemp(prefix="ask_hygiene_fifo_")
        os.mkfifo(os.path.join(path, "redis.pid"))
    try:
        assert os.path.isdir(path)  # left for the reaper, never removed
        assert [p for p, _ in tracker.skipped_live] == [path]
    finally:
        os.remove(os.path.join(path, "redis.pid"))
        os.rmdir(path)


def test_tracker_fails_closed_on_a_dangling_symlink_pid_file():
    with TrackedTempfileArtifacts() as tracker:
        path = tempfile.mkdtemp(prefix="ask_hygiene_dangling_")
        os.symlink(os.path.join(path, "gone"),
                   os.path.join(path, "redis.pid"))
    try:
        assert os.path.isdir(path)
        assert [p for p, _ in tracker.skipped_live] == [path]
    finally:
        os.remove(os.path.join(path, "redis.pid"))
        os.rmdir(path)


def test_tracker_dead_pid_does_not_short_circuit_a_live_second_pid(
        monkeypatch):
    """The dead-pid branch must `continue` to the remaining pid files, not
    `return None` — otherwise a dead first pid disables a live server's
    protection once `_PID_FILENAMES` grows past one entry."""
    import subprocess
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    monkeypatch.setattr(
        "tests._tmpdir_hygiene._PID_FILENAMES", ("redis.pid", "falkor.pid"))
    with TrackedTempfileArtifacts() as tracker:
        path = tempfile.mkdtemp(prefix="ask_hygiene_twopids_")
        with open(os.path.join(path, "redis.pid"), "w") as fh:
            fh.write(str(proc.pid))                  # provably dead
        with open(os.path.join(path, "falkor.pid"), "w") as fh:
            fh.write(str(os.getpid()))               # this process: live
    try:
        assert os.path.isdir(path)  # the LIVE second pid must still protect it
        assert [p for p, _ in tracker.skipped_live] == [path]
    finally:
        os.remove(os.path.join(path, "redis.pid"))
        os.remove(os.path.join(path, "falkor.pid"))
        os.rmdir(path)


def test_tracker_leaves_the_data_dir_of_a_live_registered_server():
    """#4479: the tracker must not delete the configured DATA dir of a live server.

    redislite keeps `redis.pid` in its own instance dir and records that path in
    `<dbfilename>.settings` inside the data dir — so the data dir has no pid file of
    its own, and the guard must follow the registry to the real pid file.
    """
    import json
    import subprocess

    instance = tempfile.mkdtemp(prefix="ask_hygiene_inst_")
    pid_file = os.path.join(instance, "redis.pid")
    with open(pid_file, "w") as fh:
        fh.write(str(os.getpid()))  # a live pid (this process)
    with TrackedTempfileArtifacts() as tracker:
        path = tempfile.mkdtemp(prefix="tortoise_shared_embedded_")
        with open(os.path.join(path, "shared.db.settings"), "w") as fh:
            json.dump({"pidfile": pid_file, "dbfilename": "shared.db"}, fh)
    try:
        assert os.path.isdir(path)  # left for the reaper, never removed
        assert [p for p, _ in tracker.skipped_live] == [path]
    finally:
        shutil.rmtree(path, ignore_errors=True)
        shutil.rmtree(instance, ignore_errors=True)

    # ...and the no-leak-back half: a DEAD registered server must not strand the dir.
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    dead_instance = tempfile.mkdtemp(prefix="ask_hygiene_deadinst_")
    dead_pid = os.path.join(dead_instance, "redis.pid")
    with open(dead_pid, "w") as fh:
        fh.write(str(proc.pid))
    with TrackedTempfileArtifacts() as tracker2:
        dead_path = tempfile.mkdtemp(prefix="tortoise_shared_embedded_dead_")
        with open(os.path.join(dead_path, "shared.db.settings"), "w") as fh:
            json.dump({"pidfile": dead_pid, "dbfilename": "shared.db"}, fh)
    assert not os.path.exists(dead_path)
    assert tracker2.skipped_live == []
    shutil.rmtree(dead_instance, ignore_errors=True)


def test_protected_reason_is_none_for_a_provably_dead_pid():
    import subprocess
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    with TrackedTempfileArtifacts():
        path = tempfile.mkdtemp(prefix="ask_hygiene_deadreason_")
        with open(os.path.join(path, "redis.pid"), "w") as fh:
            fh.write(str(proc.pid))
        assert _protected_reason(path) is None


def test_autouse_fixture_is_wired_into_the_suite(request):
    """The conftest re-export must make the tracker active for every test."""
    assert "track_tempfile_artifacts" in request.fixturenames
