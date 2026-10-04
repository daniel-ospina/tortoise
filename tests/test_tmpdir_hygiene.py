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
    HOST_TMPDIR,
    ROOT_BASE,
    SharedTmpdirScanError,
    TrackedTempfileArtifacts,
    _is_shared_temp_scope,
    _live_server_in_root,
    _marker_owner_provably_dead,
    _protected_reason,
    _read_marker,
    install_scan_guard,
    scan_root,
    session_tmpdir,
    sweep_stale_session_roots,
    uninstall_scan_guard,
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


# ══════════════════════════════════════════════════════════════════════
# #3752 — the private-session-temp-root isolation property, and a guard
# that can FIRE. Lives here (not in a new file) because
# tests/test_tmpdir_hygiene.py is already registered in ci-surfaces.yml's
# `core` surface, while a new test file would be CI-selection drift.
# ══════════════════════════════════════════════════════════════════════
def test_session_root_is_private_and_not_the_shared_tempdir():
    root = scan_root()
    assert session_tmpdir() == root
    assert os.path.realpath(root) != os.path.realpath(HOST_TMPDIR)
    # A descendant of the SHORT session-root base, deliberately: that base keeps
    # scratch paths under the AF_UNIX sun_path cap.
    assert root.startswith(ROOT_BASE.rstrip(os.sep) + os.sep)
    assert os.path.isdir(root)
    # The root is called `tt_` so an operator (and the reaper) can name it.
    assert os.path.basename(root).startswith("tt_")
    # And it is marked, so a SIGKILLed run is reclaimable rather than permanent.
    assert _read_marker(os.path.join(root, ".session-pid")) is not None


def test_scan_root_refuses_host_scope():
    root = scan_root()
    assert not _is_shared_temp_scope(root), (
        "scan_root() must never hand back a shared-tempdir-scoped path")


@pytest.mark.parametrize("scanner", ["scandir", "listdir", "walk"])
def test_scanning_the_shared_tempdir_raises(scanner):
    fn = getattr(os, scanner)
    with pytest.raises(SharedTmpdirScanError) as exc:
        fn(HOST_TMPDIR)
    assert "#3752" in str(exc.value)


@pytest.mark.parametrize("scanner", ["scandir", "listdir", "walk"])
def test_scanning_an_ancestor_of_the_shared_tempdir_raises(scanner):
    """Descendants are allowed; ANCESTORS are not — `os.walk('/')` is exactly
    the O(whole-host) shape the guard exists to stop."""
    fn = getattr(os, scanner)
    with pytest.raises(SharedTmpdirScanError):
        fn(os.path.dirname(HOST_TMPDIR))


def test_scanning_through_a_symlink_to_the_shared_tempdir_raises(tmp_path):
    """macOS spells the same dir `/var/folders/...` and
    `/private/var/folders/...`; the guard resolves realpath, so a symlink or
    the raw spelling cannot fail open."""
    link = tmp_path / "shared-tmp-link"
    os.symlink(HOST_TMPDIR, str(link))
    with pytest.raises(SharedTmpdirScanError):
        os.scandir(str(link))


@pytest.mark.parametrize("scanner", ["scandir", "listdir", "walk"])
def test_scanning_a_descendant_is_allowed(scanner, tmp_path):
    """The session root and pytest's tmp trees are descendants — legitimate."""
    fn = getattr(os, scanner)
    fn(scan_root())
    fn(str(tmp_path))


def test_mkdtemp_lands_in_the_private_root_not_the_host_tree():
    import tempfile

    d = tempfile.mkdtemp(prefix="guard_probe_")
    try:
        assert os.path.realpath(d).startswith(
            os.path.realpath(scan_root()).rstrip(os.sep) + os.sep), (
            f"mkdtemp({d!r}) escaped to the shared temp dir")
        # NOT directly in the host tree.
        assert os.path.dirname(os.path.realpath(d)) != os.path.realpath(
            HOST_TMPDIR)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_guard_can_fire():
    """POSITIVE CONTROL: the guard is what fails the scan.

    With the guard uninstalled the same shared-dir scan succeeds; with it
    reinstalled it raises. This proves (a) the guard can fire and (b) the
    failure in the tests above comes from the guard, not from the shared dir
    being unreadable or absent.
    """
    with pytest.raises(SharedTmpdirScanError):
        os.scandir(HOST_TMPDIR)

    uninstall_scan_guard()
    try:
        # Deliberate shared-dir scan with the guard OFF — the reintroduced
        # defect shape. It must succeed, or this control proves nothing.
        # Do NOT materialize the listing: the shared tree is huge by design
        # (that is the defect), and creating the iterator is the scan.
        os.scandir(HOST_TMPDIR).close()
    finally:
        install_scan_guard()

    with pytest.raises(SharedTmpdirScanError):
        os.scandir(HOST_TMPDIR)


def test_sweep_reclaims_a_dead_marked_root_and_keeps_a_live_one():
    """A SIGKILLed suite leaves a marked root; the next suite reclaims it —
    but never a root whose owner is still alive (a concurrent suite)."""
    dead = os.path.join(ROOT_BASE, "tt_dead" + os.urandom(3).hex())
    live = os.path.join(ROOT_BASE, "tt_live" + os.urandom(3).hex())
    os.mkdir(dead)
    os.mkdir(live)
    try:
        with open(os.path.join(dead, ".session-pid"), "w") as fh:
            fh.write("pid=99999999\n")  # > pid_max everywhere
        with open(os.path.join(live, ".session-pid"), "w") as fh:
            fh.write(f"pid={os.getpid()}\n")
        reclaimed = sweep_stale_session_roots()
        assert dead in reclaimed
        assert not os.path.exists(dead)
        assert os.path.exists(live), "a live suite's root must not be reclaimed"
    finally:
        shutil.rmtree(dead, ignore_errors=True)
        shutil.rmtree(live, ignore_errors=True)


def test_host_coordination_is_host_scoped_and_lock_is_sweep_scoped():
    """The two surfaces #3752 must keep distinct.

    ACTIVE_SUITES_DIR is read by a production/cron sweep to defer kills while a
    suite runs, so it must stay on the HOST temp dir (via TORTOISE_HOST_TMPDIR).
    The reaper lock belongs to the sweep domain, which under the suite IS the
    private root.
    """
    from tortoise.embedded_reaper import _LOCK_PATH, ACTIVE_SUITES_DIR

    assert os.environ["TORTOISE_HOST_TMPDIR"] == HOST_TMPDIR
    assert os.path.realpath(ACTIVE_SUITES_DIR).startswith(
        HOST_TMPDIR.rstrip(os.sep) + os.sep), ACTIVE_SUITES_DIR
    assert os.path.realpath(_LOCK_PATH).startswith(
        os.path.realpath(scan_root()).rstrip(os.sep) + os.sep), _LOCK_PATH


def test_marker_read_is_hardened_against_the_shared_base(tmp_path):
    """The marker read runs over a world-writable base at import — lstat +
    regular-file + bounded read, and pid<=0 is unparseable."""
    p = tmp_path / ".session-pid"
    p.write_text("pid=0\n")
    assert _read_marker(str(p)) is None, "pid 0 probes a process GROUP"
    p.write_text("pid=-1\n")
    assert _read_marker(str(p)) is None, "a negative pid signals a process group"
    p.write_text("pid=123\n")
    assert _read_marker(str(p)) == (123, None)

    real = tmp_path / "real-marker"
    real.write_text("pid=123\n")
    link = tmp_path / "linked-marker"
    os.symlink(str(real), str(link))
    assert _read_marker(str(link)) is None, "a symlinked marker must not be read"

    if hasattr(os, "mkfifo"):
        fifo = tmp_path / "fifo-marker"
        os.mkfifo(fifo)
        assert _read_marker(str(fifo)) is None, "a FIFO must not be opened"


def test_marker_owner_check_fails_safe_on_an_overflowing_pid(tmp_path):
    """A pid wider than C long makes os.kill raise OverflowError — that must
    read as 'keep the root', not escape and abort conftest import."""
    p = tmp_path / ".session-pid"
    p.write_text("pid=" + "9" * 30 + "\n")
    assert _marker_owner_provably_dead(str(p)) is False


def test_scope_returns_the_matched_root_not_a_bare_bool():
    assert _is_shared_temp_scope(HOST_TMPDIR) == HOST_TMPDIR
    assert _is_shared_temp_scope(scan_root()) is None


def test_guard_message_names_the_matched_root():
    with pytest.raises(SharedTmpdirScanError) as exc:
        os.scandir(ROOT_BASE)
    assert ROOT_BASE in str(exc.value), (
        "the failure must name the tree the caller actually scanned")


def test_live_server_in_root_protects_an_outliving_server(tmp_path):
    """The atexit teardown must not rmtree a root a live embedded server
    deliberately outlived (the only_safe defer path) — that would orphan it.

    But an UNPARSEABLE pid must NOT defer forever: the #4069 guard tests build
    dirs with bogus `redis.pid` content, and a fail-closed rule here would pin
    the whole session root permanently.
    """
    root = tmp_path / "tt_probe"
    sock = root / "tmpAAAA"
    sock.mkdir(parents=True)
    (sock / "redis.pid").write_text("not-a-pid")  # unparseable -> reclaimable
    assert _live_server_in_root(str(root)) is None
    (sock / "redis.pid").write_text("99999999")  # provably dead
    assert _live_server_in_root(str(root)) is None
    (sock / "redis.pid").write_text(str(os.getpid()))  # THIS process -> live
    assert _live_server_in_root(str(root)) is not None
