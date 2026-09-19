"""#4069 — per-test tempfile teardown (`tests/_tmpdir_hygiene.py`).

Order-independent: every test drives the tracker context manager directly
(pytest-randomly may reorder a module, so no test may depend on another's
teardown having run).
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests._tmpdir_hygiene import (
    TrackedTempfileArtifacts,
    _live_pid_in,
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
    assert tracker.skipped_live == [(path, os.getpid())]
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


def test_live_pid_in_ignores_missing_and_unparseable():
    with TrackedTempfileArtifacts():
        path = tempfile.mkdtemp(prefix="ask_hygiene_pid_")
        assert _live_pid_in(path) is None
        with open(os.path.join(path, "redis.pid"), "w") as fh:
            fh.write("not-a-pid")
        assert _live_pid_in(path) is None


def test_autouse_fixture_is_wired_into_the_suite(request):
    """The conftest re-export must make the tracker active for every test."""
    assert "track_tempfile_artifacts" in request.fixturenames
