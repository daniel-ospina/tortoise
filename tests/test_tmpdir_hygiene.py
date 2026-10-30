"""#4069 — per-test tempfile teardown (`tests/_tmpdir_hygiene.py`).

Order-independent: every test drives the tracker context manager directly
(pytest-randomly may reorder a module, so no test may depend on another's
teardown having run).
"""
from __future__ import annotations

import errno
import logging
import os
import re
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests._tmpdir_hygiene import (
    _ZERO_CLIENT_STATE_PATH_ENV,
    HOST_TMPDIR,
    ROOT_BASE,
    SharedTmpdirScanError,
    TrackedTempfileArtifacts,
    _discard_tolerated_leaks,
    _is_shared_temp_scope,
    _live_server_in_root,
    _marker_owner_provably_dead,
    _protected_reason,
    _read_marker,
    install_scan_guard,
    scan_root,
    session_tmpdir,
    sweep_stale_session_roots,
    tolerated_cleanup_leaks,
    uninstall_scan_guard,
    write_tolerated_cleanup_report,
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


# ── #1005 (epic #1647 E2E-7): the RUN-scoped root registry ─────────────────
# The CI orphan gate may only call a residue a leak if it can attribute the
# server to THIS run, and the attribution key is the run's session temp roots.
# Under `pytest -n` a run owns one root per worker process, and the hygiene
# report is written by whichever worker exits last — so every process appends
# its root to a run-keyed registry at install time, before it can spawn a
# server. These tests pin that contract; without it the last writer would see
# only its own root and a sibling worker's leak would read as foreign.


def test_run_roots_records_the_installed_root_under_the_run_uid():
    from tests._tmpdir_hygiene import (
        run_registry_path,
        run_roots,
        run_uid,
    )

    assert run_uid(), "a run id is always defined (never an empty key)"
    path = run_registry_path()
    assert path.endswith(run_uid())
    root = session_tmpdir()
    assert root is not None
    # #10255 review (P3): read the REGISTRY, not `run_roots()`. `run_roots()`
    # appends the in-memory `_SESSION_TMPDIR` unconditionally (`if current and
    # current not in roots`), so `root in run_roots()` passes even if
    # `record_run_root` were a no-op — the assertion did not test the write path
    # it names. The write path is what makes a SIBLING worker's root visible to
    # whichever process exits last and writes the report.
    recorded = [
        line.strip()
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert os.path.realpath(root) in recorded, (
        "install time must RECORD the session root in the run registry — it is "
        "the attribution key the orphan gate binds to, and a sibling worker's "
        "root reaches the last process to exit only through this file (#1005)"
    )
    assert root in run_roots(), (
        "the installed session root must also be readable as one of this run's "
        "roots, which is how the sweep consumes the registry (#1005)"
    )


def test_record_run_root_actually_writes_the_registry(tmp_path, monkeypatch):
    """#10255 review (P3): the WRITE path, proven discriminating.

    `run_roots()` appends the in-memory `_SESSION_TMPDIR` unconditionally, so
    any assertion of the shape `root in run_roots()` holds even when nothing was
    ever written. This drives `record_run_root` against a registry path of its
    own and reads the FILE, so making the write a no-op reds here — which is the
    failure mode that would silently de-attributed every sibling worker's root
    (a sibling's root reaches the process that writes the report ONLY through
    this file).
    """
    from tests import _tmpdir_hygiene as h

    registry = tmp_path / "run-registry.txt"
    monkeypatch.setattr(h, "run_registry_path", lambda: str(registry))
    fresh = tmp_path / "session-root"
    fresh.mkdir()
    h.record_run_root(str(fresh))
    lines = [ln.strip() for ln in registry.read_text(encoding="utf-8").splitlines()
             if ln.strip()]
    assert os.path.realpath(str(fresh)) in lines, (
        "record_run_root must APPEND the realpath of the root to the run "
        "registry — this is the sibling-worker attribution key (#1005)"
    )


def test_run_roots_is_run_scoped_not_a_shared_pile(tmp_path, monkeypatch):
    """A root recorded by ANOTHER run must never be attributed to this one.

    Two concurrent pytest runs on one host each own their roots; if the
    registry were global, the gate would attribute a neighbour's server to us
    and red a green run — the exact defect the registry exists to prevent.
    """
    import tests._tmpdir_hygiene as hygiene

    monkeypatch.setattr(hygiene, "_RUN_REGISTRY_DIR", str(tmp_path))
    mine = str(tmp_path / "tt_mine")
    theirs = str(tmp_path / "tt_theirs")
    monkeypatch.setenv("PYTEST_XDIST_TESTRUNUID", "run-aaa")
    hygiene.record_run_root(mine)
    monkeypatch.setenv("PYTEST_XDIST_TESTRUNUID", "run-bbb")
    hygiene.record_run_root(theirs)

    monkeypatch.setenv("PYTEST_XDIST_TESTRUNUID", "run-aaa")
    as_aaa = hygiene.run_roots()
    assert mine in as_aaa, "run-aaa's own root must be visible to it"
    assert theirs not in as_aaa, (
        "another run's root is NOT ours to attribute — it is the population "
        "the gate must read as foreign (#1005)"
    )
    assert session_tmpdir() in as_aaa, (
        "the live session root is always included, by design: a failed "
        "registry write must not make this run unable to attribute its OWN "
        "servers (that would silently loosen the gate)"
    )

    monkeypatch.setenv("PYTEST_XDIST_TESTRUNUID", "run-bbb")
    as_bbb = hygiene.run_roots()
    assert theirs in as_bbb and mine not in as_bbb, (
        "the registry is keyed per run in BOTH directions — neither run may "
        "see the other's roots"
    )


def test_record_run_root_is_additive_and_never_raises(tmp_path, monkeypatch,
                                                      caplog):
    """Every worker's root lands in one file; concurrent appends must not lose
    a sibling's root (the O_APPEND contract), and a write failure must not fail
    the suite.

    #10255 review B: a write failure does NOT "degrade to the gate's
    unattributable path" — that was this test's claim and it was false.
    `run_roots()` re-appends the in-memory root unconditionally, so `own_roots`
    in the report is never empty from this cause and the gate takes the
    ATTRIBUTED path with a PARTIAL root set. The assertions below pin the TRUE
    contract: the failure is swallowed, it is recorded where it happened, and
    the process's own root is still reported.
    """
    import logging

    import tests._tmpdir_hygiene as hygiene

    monkeypatch.setattr(hygiene, "_RUN_REGISTRY_DIR", str(tmp_path))
    monkeypatch.setenv("PYTEST_XDIST_TESTRUNUID", "run-ccc")
    hygiene.record_run_root(str(tmp_path / "tt_gw0"))
    hygiene.record_run_root(str(tmp_path / "tt_gw1"))
    roots = hygiene.run_roots()
    assert str(tmp_path / "tt_gw0") in roots
    assert str(tmp_path / "tt_gw1") in roots, (
        "the second worker's append must not clobber the first's — both roots "
        "are needed to attribute the whole run"
    )

    # A directory that cannot be created is an OSError, not a suite failure.
    monkeypatch.setattr(hygiene, "_RUN_REGISTRY_DIR",
                        str(tmp_path / "tt_blocker" / "sub"))
    (tmp_path / "tt_blocker").write_text("not a directory")
    with caplog.at_level(logging.WARNING, logger=hygiene.logger.name):
        hygiene.record_run_root(str(tmp_path / "tt_gw2"))  # must not raise
    assert any("could not record" in rec.getMessage()
               for rec in caplog.records), (
        "a swallowed registry write must be RECORDED where it happened — it "
        "is the only trace, because nothing in the report or the gate can see "
        "it (#10255 review B)"
    )

    # The corrected claim, pinned: a failed append does not empty `own_roots`.
    monkeypatch.setattr(hygiene, "_SESSION_TMPDIR", str(tmp_path / "tt_gw3"))
    roots = hygiene.run_roots()
    assert str(tmp_path / "tt_gw3") in roots, (
        "a failed append does NOT make `own_roots` empty: the in-memory root "
        "is re-appended, so the gate takes the ATTRIBUTED path with a PARTIAL "
        "root set and never reports `unattributable` from this cause — making "
        "this test's earlier claim true would require changing the GATE too"
    )
    assert str(tmp_path / "tt_gw2") not in roots, (
        "the failed append really did not land — otherwise the assertion above "
        "would be vacuous"
    )


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


def test_session_root_pins_the_reaper_zero_client_state_file():
    """#7923: the private session root pins the embedded reaper's persisted
    zero-client state INSIDE itself, so the suite-wide end-sweep (conftest's
    `_redislite_hygiene`) can never write it into the developer's real
    `$HOME/.tortoise`.

    `install_session_tmpdir()` runs at conftest import, so the live process
    environment is the observable under test — same shape as
    `test_host_coordination_is_host_scoped_and_lock_is_sweep_scoped`.
    """
    from tortoise.embedded_reaper import (
        ZERO_CLIENT_STATE_PATH_ENV,
        _zero_client_state_path,
    )

    # The literal mirror in this module must not drift from the reaper's.
    assert _ZERO_CLIENT_STATE_PATH_ENV == ZERO_CLIENT_STATE_PATH_ENV

    override = os.environ.get(ZERO_CLIENT_STATE_PATH_ENV)
    assert override, "the session must export the reaper state override"
    root = os.path.realpath(scan_root())
    assert os.path.realpath(override).startswith(root.rstrip(os.sep) + os.sep), \
        override
    # The reaper resolves to that same contained path (attribute seam unset).
    assert os.path.realpath(_zero_client_state_path()) == \
        os.path.realpath(override)


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


# ── #7735: the tolerant `TemporaryDirectory.cleanup` ────────────────────────────
#
# The wrapper is installed by `tests/conftest.py` at import, so it is already
# live here. These tests pin BOTH halves of the claim: an ENOTEMPTY teardown
# stays green AND is reported, while every other errno still raises. The second
# is the mutation check — if the wrapper were a blanket swallow, it would fail.


def _boom_with(err: int):
    def _boom(path, **kwargs):
        raise OSError(err, os.strerror(err), path)

    return _boom


@contextmanager
def _patch_rmtree(err: int):
    """Make the rmtree `TemporaryDirectory.cleanup` ACTUALLY calls raise `err`.

    `tempfile` resolves rmtree ONE OF TWO WAYS, and which one depends on the
    interpreter PATCH level — so patching `shutil.rmtree` alone is a mutation that
    reaches the cleanup path on some stdlibs and not on others. Read from the primary
    source on both:

    * CPython **3.12.7+ / 3.12.15** — `_rmtree` calls `_shutil.rmtree(name, onexc=…)`, a
      MODULE-ATTRIBUTE lookup at call time, so a `shutil.rmtree` patch reaches it
      (verified against `Lib/tempfile.py` at tag `v3.12.15`).
    * CPython **3.12.0–3.12.6** (measured on this host's 3.12.3) — the same method calls
      the module-level `_rmtree(name, …)`, which `tempfile` bound as
      `_rmtree = _shutil.rmtree` at IMPORT. A `shutil.rmtree` patch never reaches it:
      the real rmtree runs, the directory is really deleted, and these tests report a
      false red on a supported interpreter (measured: the stub was never called and
      `assert os.path.isdir(victim)` failed with "the directory is left for the
      reaper").

    Patching BOTH makes the mutation reach on every supported 3.12.x, so the pins below
    assert the product's behaviour rather than the host's stdlib vintage. Restores every
    attribute it replaced, and only where the stub is still installed.
    """
    boom = _boom_with(err)
    originals: list[tuple[str, object]] = []
    for attr in ("rmtree", "_rmtree"):
        for owner in (shutil, tempfile):
            if hasattr(owner, attr):
                originals.append((attr, getattr(owner, attr)))
                setattr(owner, attr, boom)
    try:
        yield boom
    finally:
        for attr, original in reversed(originals):
            for owner in (shutil, tempfile):
                if getattr(owner, attr, None) is boom:
                    setattr(owner, attr, original)


def test_enotempty_cleanup_is_tolerated_and_STILL_REPORTED(tmp_path, caplog):
    #7735: a live server re-creates an entry during rmtree's final rmdir.
    # The stub is patched GLOBALLY — through both spellings the stdlib uses to reach it
    # (`_patch_rmtree`) — so it is restored BEFORE the test returns; otherwise pytest's
    # own `tmp_path` teardown would hit the stub and ERROR the test that just passed.
    import json
    import tempfile

    victim = None
    with _patch_rmtree(errno.ENOTEMPTY):
        d = tempfile.TemporaryDirectory(dir=str(tmp_path))
        victim = d.name
        with caplog.at_level(logging.WARNING):  # must NOT raise
            d.cleanup()
        assert os.path.isdir(victim), "the directory is left for the reaper"
        # CHANNEL 1 (interactive): the log record is useful when running one test.
        assert "#7735" in caplog.text, "REPORTED, not swallowed"
        assert victim in caplog.text, "the report must name the directory"
        # CHANNEL 2 (CI): capture DISCARDS the record above for a PASSING test
        # (measured: 0 occurrences in the log and 0 in the junit XML under the
        # CI flags), so visibility is pinned on the artifact the workflow dumps.
        assert victim in [e["path"] for e in tolerated_cleanup_leaks()]
        report = write_tolerated_cleanup_report(log_dir=str(tmp_path))
        assert report is not None, "a tolerated leak must write the artifact"
        payload = json.loads(Path(report).read_text())
        assert victim in [
            e["path"] for e in payload["tolerated_cleanup_leaks"]], (
            "the CI-visible artifact must name the tolerated directory")
    if victim is not None:
        # This test is a deliberate probe; keep it out of the session
        # artifact, or every CI run would report a synthetic leak.
        _discard_tolerated_leaks([victim])


def test_write_tolerated_cleanup_report_is_silent_when_nothing_leaked(tmp_path):
    """No tolerated leak → no artifact, so the CI dump step carries signal.

    Order-independent: it seeds nothing, and the writer's guard is on the
    global list, which the ENOTEMPTY test above also drains.
    """
    from tests import _tmpdir_hygiene as hygiene

    saved = list(hygiene._TOLERATED_CLEANUP_LEAKS)
    hygiene._TOLERATED_CLEANUP_LEAKS[:] = []
    try:
        assert write_tolerated_cleanup_report(log_dir=str(tmp_path)) is None
        assert not (tmp_path / "tempdir-hygiene-end.json").exists()
    finally:
        hygiene._TOLERATED_CLEANUP_LEAKS[:] = saved


def test_tolerated_leak_report_merges_a_peer_workers_record(tmp_path):
    """Two writers on the SAME artifact path must union, not truncate.

    CI is SERIAL at this head (``XDIST_WORKERS=0``), so this is the defensive
    branch for a future xdist re-admission: with ``-n 4`` a plain overwrite
    would let the last worker to finish discard a peer's tolerated leak — the
    invisibility F1 names. The writer must union by path rather than truncate.
    """
    import json as _json

    from tests import _tmpdir_hygiene as hygiene

    saved = list(hygiene._TOLERATED_CLEANUP_LEAKS)
    hygiene._TOLERATED_CLEANUP_LEAKS[:] = [{
        "path": "/tmp/tortoise_peer_worker_leak",
        "errno": errno.ENOTEMPTY,
        "reason": "Directory not empty",
    }]
    try:
        artifact = tmp_path / "tempdir-hygiene-end.json"
        artifact.write_text(_json.dumps({"tolerated_cleanup_leaks": [{
            "path": "/tmp/tortoise_earlier_worker_leak",
            "errno": errno.ENOTEMPTY,
            "reason": "Directory not empty",
            "pid": 111,
        }]}))
        report = write_tolerated_cleanup_report(log_dir=str(tmp_path))
        assert report is not None
        payload = _json.loads(Path(report).read_text())
        paths = [e["path"] for e in payload["tolerated_cleanup_leaks"]]
        assert paths == [
            "/tmp/tortoise_earlier_worker_leak",
            "/tmp/tortoise_peer_worker_leak",
        ], paths
    finally:
        hygiene._TOLERATED_CLEANUP_LEAKS[:] = saved


def test_the_tolerated_leak_artifact_is_wired_into_session_teardown():
    """#7735 visibility, WIRED: conftest must call the writer in TEARDOWN.

    The tests above prove the writer works; a writer nobody calls is the very
    failure F1 names (a report no CI surface ever sees). Pin the call to the
    statements AFTER the fixture's ``yield``: at setup the tolerated-leak list
    is necessarily empty, so a call moved before the yield writes no artifact
    while still being "in the fixture" — the regression the earlier
    position-blind walk accepted (#7735 review, F2).
    """
    import ast

    tree = ast.parse((Path(__file__).resolve().parent / "conftest.py").read_text())
    fixture = next(
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "_redislite_hygiene")
    yield_index = next(
        i for i, stmt in enumerate(fixture.body)
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Yield))
    calls: list[ast.Call] = []
    for stmt in fixture.body[yield_index + 1:]:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue  # the atexit path is not the session teardown
        calls.extend(n for n in ast.walk(stmt) if isinstance(n, ast.Call))
    called = {c.func.id for c in calls if isinstance(c.func, ast.Name)}
    assert "write_tolerated_cleanup_report" in called, (
        "tests/conftest.py::_redislite_hygiene must call "
        "write_tolerated_cleanup_report() AFTER its yield (session teardown), "
        "or the tolerated leak reaches no CI surface (#7735 review, F1/F2)")


# The CI jobs this PR makes leak-observable, named EXACTLY.
#
# WHY A FROZEN LIST AND NOT A REPO-WIDE SCAN. Three earlier revisions derived
# "every pytest job" from the workflow text. Four review rounds each found
# another hole in that derivation — bare `pytest`, `pip install … && pytest`,
# `./.venv/bin/pytest`, a path named only in a trailing comment, a mention in an
# unrelated step — because "every pytest job in the repository" is a claim about
# PROCESS, which a text scan cannot certify; each fix only moved the boundary
# (#7735 reviews, rounds 1-4). What this pin asserts instead is the behaviour
# this PR actually delivers: the jobs below surface the artifact. Adding a
# pytest job that runs the unit suite is a deliberate one-line addition here —
# the same act as giving it a dump step.
#
# DELIBERATELY NOT COVERED (recorded, not silently missed): `ci.yml::flip-gate`
# runs `bash .github/scripts/verify-cutover`, which runs pytest on ten unit
# files (incl. tests/test_backup_sweep.py, ~46 `TemporaryDirectory(` sites) from
# INSIDE A SHELL SCRIPT — no step-level scan can see that, and no step there
# surfaces the artifact today. That is follow-up on #7735, not a silent gap.
_LEAK_SURFACED_JOBS = (
    "python-ci.yml::test",
    "python-ci.yml::test-slow",
    "python-ci.yml::test-concurrency-falkor",
    "python-ci.yml::test-track-b",
    "python-ci.yml::test-carve-out",
    "python-ci.yml::test-d14-hosted-api",
    "post-merge-validation.yml::validate",
)
_ARTIFACT = "tempdir-hygiene-end.json"


def _actions_yaml():
    """A SafeLoader whose bool resolver matches the RUNNER's, not YAML 1.1's.

    GitHub Actions parses ``if: yes`` / ``if: on`` as STRINGS — actionlint
    rejects them as undefined variables — while PyYAML's YAML 1.1 resolver maps
    them to the bool ``True``, so a bare ``if: yes`` would read as an always-run
    gate the runner would never run. This is the same PyYAML-typing-is-not-
    runner-typing root that made the round-5 guard a no-op (#7735 reviews,
    rounds 6-7); the resolver is restricted to true/false so the pin's typing
    matches the workflow engine it is reasoning about.
    """

    import yaml

    class _ActionsLoader(yaml.SafeLoader):
        pass

    _ActionsLoader.yaml_implicit_resolvers = {
        ch: [(tag, rx) for tag, rx in resolvers
             if tag != "tag:yaml.org,2002:bool"]
        for ch, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }
    _ActionsLoader.add_implicit_resolver(
        "tag:yaml.org,2002:bool",
        re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
        list("tTfF"),
    )
    return _ActionsLoader


def _always_runs(raw: object) -> bool:
    """True when a step's ``if`` makes it run even after a failing suite.

    PyYAML types a bare ``if: false`` as the BOOL ``False``, so a truthiness
    check (``raw or "always()"``) silently treats a DISABLED step as allowed —
    that bug shipped in round 5 and was caught in round 6, where the mutation it
    exists to catch passed. Absence is NOT accepted: an absent ``if`` is an
    implicit ``success()``, so the step would be skipped on precisely the failing
    runs it exists to report. The exact accepted spellings are defined by
    this predicate's own body and are deliberately NOT enumerated here: a
    prose list re-stales the moment the comparison changes (#7735 review,
    round 8 added case-insensitive matching and made the list stale).
    """
    if raw is None:
        return False
    if raw is True:
        return True
    expr = str(raw).strip()
    if expr.startswith("${{") and expr.endswith("}}"):
        expr = expr[3:-2].strip()
    return expr.casefold() == "always()"


def _surfaces_artifact(step_run: str) -> bool:
    """True when this ONE step names the artifact and contains a `cat`.

    Comment lines and trailing `#` comments are stripped first, because a bare
    substring check was satisfied by a comment that merely mentioned the path —
    certifying "the string occurs" rather than "the artifact is surfaced"
    (#7735 review, round 3). Both halves are required within the SAME step, so
    an unrelated `cat` in another step cannot stand in.

    Boundary — what this does NOT prove (#7735 review, round 5): the `cat` need
    not read the artifact. The delivered shape declares `LOG=…tempdir-hygiene-end.json`
    on one line and `cat "$LOG"` on the next, so "same line" would false-reject
    it; the pin therefore checks co-occurrence inside the step. It also cannot
    prove the shell executed. It requires the `cat` all seven delivered steps
    use — a future rewrite to `jq`/`head` must update this pin.
    """
    lines = [
        ln.split("#", 1)[0]
        for ln in step_run.splitlines()
        if not ln.lstrip().startswith("#")
    ]
    names = any(_ARTIFACT in ln for ln in lines)
    reads = any("cat" in ln.split() for ln in lines)
    return names and reads


def test_suite_jobs_surface_the_tolerated_leak():
    """#7735 review F1: the report is visible only where the workflow dumps it.

    Every job in ``_LEAK_SURFACED_JOBS`` must carry a step that prints
    ``tempdir-hygiene-end.json`` — see that constant for the scope and for the
    surfaces deliberately outside it. The first revision dumped the artifact in
    the ``test`` job alone, so the ``test-slow`` legs #7735 was actually
    measured on stayed blind; a job that only NAMES the path still reports
    nothing, so this asserts the two together — in a step that is also allowed
    to run after a failing suite (see ``_always_runs``); an absent ``if`` is NOT
    enough, because it is an implicit ``success()``. It does not evaluate the
    job's OWN ``if``.
    """
    import yaml

    root = Path(__file__).resolve().parent.parent
    missing: list[str] = []
    for entry in _LEAK_SURFACED_JOBS:
        wf_name, job_name = entry.split("::", 1)
        wf = yaml.load(
            (root / ".github" / "workflows" / wf_name).read_text(),
            Loader=_actions_yaml())
        job = (wf.get("jobs") or {}).get(job_name)
        if job is None:
            missing.append(f"{entry} (no such job in {wf_name})")
            continue
        # The step must both surface the artifact and be ALLOWED TO RUN. The
        # gate is checked by `_always_runs`, not by truthiness: `if: false`
        # parses as the bool False, and `False or "always()"` is truthy, which
        # made an earlier version of this guard a no-op (#7735 review, round 6).
        steps = job.get("steps") or []
        present = [s for s in steps if _surfaces_artifact(s.get("run") or "")]
        if not any(_always_runs(s.get("if")) for s in present):
            missing.append(
                f"{entry} (surfacing step is not allowed to run)"
                if present else entry)
    assert missing == [], (
        "these jobs do not surface the tolerated-leak artifact "
        f"(#7735 review F1): {missing}")


# The pytest legs whose log is resolved PER JOB from `$RUNNER_TEMP`.
#
# `#10028` migrated `test`; `#10280` names this leg (and five others) as still
# writing the host-global `/tmp/pytest.log`, which a co-tenant job on the same
# self-hosted host truncates mid-run. It bit `test-concurrency-falkor` on run
# 40884374618 (job 123539933295): its own `tail` and `pytest-attribution.sh` read
# `tests/test_backfill_sources.py` / `tests/test_billing.py` — files that leg
# never runs — and it reported "test leg INCOMPLETE — no pytest summary; failure
# set unobservable".
#
# `test-slow` is the second of those legs (this PR). Its own measured red is the
# VERDICT-ARTIFACT shape rather than the log shape: run 40921262252, job
# 123636659746 exited pytest with **0** (`814 passed, 3 skipped`) and still
# redded in `Skip-fail guard` with `850 expected nodeid(s) absent from the
# junitxml testcases`, because `/tmp/expected-nodeids.txt` held a co-tenant's
# manifest (its `… and 800 more` list is `tests/test_capture_session.py` and
# `tests/test_3311_multilabel_status_vocab.py`, which this leg never runs) while
# `/tmp/junit.xml` held this leg's. Both runner installs are registrations on
# ONE host, and the leg's uploaded log carries the same sharing independently: a
# foreign session header (`-- /opt/gh-runner-x64/…`) in its first 5 KB against
# this job's own `pythonLocation: /opt/gh-runner-x64-2/…`, and a 62 KB NUL hole
# where its progress lines used to be.
#
# FROZEN LIST, not a repo-wide scan, for the reason `_LEAK_SURFACED_JOBS` gives:
# "every pytest job" is a claim about process that a text scan cannot certify.
# The legs #10280 still owns are NOT here — this pin asserts only what is
# actually migrated, so it cannot go green on a leg that was not fixed.
_PYTEST_LOG_MIGRATED_JOBS = (
    "python-ci.yml::test",
    "python-ci.yml::test-concurrency-falkor",
    "python-ci.yml::test-slow",
)

# A host-global artifact path: `/tmp/pytest.log`, or any other bare `/tmp/...`
# spelling. The runner's per-job temp dir is `$RUNNER_TEMP` / `${{ runner.temp }}`.
#
# ⛔ THE PREFIX IS ONE CHARACTER CLASS — `[\s=>'"]` — not a class followed by
# three literals. It was written `[\s=]>'\"`, where the `]` closes the class
# after `\s` and `=`, so the prefix demanded the literal four-character run
# `<space-or-=> > ' "` before every `/tmp/…`. Nothing in any workflow contains
# that run, so `findall` returned `[]` for all 50 `/tmp/<path>` occurrences on
# the NON-COMMENT lines of `origin/main:.github/workflows/python-ci.yml` (67 over
# all lines; the corpus is the one this pin builds) — half 2 of
# `test_migrated_legs_read_their_OWN_pytest_log` could never fail, which is the
# `#10280` mixed-state regression it exists to catch (resolver step still
# published, one reader reverted to a host-global path).
# `test_the_host_tmp_prefix_matches_every_measured_reader_shape` below pins the
# class form against those measured shapes, so a re-broken prefix reds there.
_HOST_TMP_PATH_RE = re.compile(r"(?:^|[\s=>'\"])(/tmp/[A-Za-z0-9._-]+)")


def _code_lines(text: str) -> list[str]:
    """Non-comment lines — a `#` mention of a path is prose, not a path.

    Round 3 of the #7735 reviews showed a bare substring check certified a
    comment that merely NAMED the artifact, so both pins here read code only.
    """
    return [ln for ln in text.splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")]


def _migrated_leg_problems(entry: str, job: dict | None) -> list[str]:
    """The three checks `test_migrated_legs_read_their_OWN_pytest_log` applies.

    Factored out so a SYNTHETIC job can be put through the same code the real
    pin runs — the only way `test_a_migrated_leg_whose_reader_is_reverted_to_
    host_tmp_reds` can prove the pin is non-vacuous without editing the workflow.
    """
    problems: list[str] = []
    if job is None:
        return [f"{entry} (no such job in {entry.split('::', 1)[0]})"]
    steps = job.get("steps") or []
    # One step must both bound the path to `$RUNNER_TEMP`, failing closed on
    # a missing one (`${RUNNER_TEMP:?...}`), and publish `PYTEST_LOG` from
    # it. Checked per STEP, not per line — the delivered shape assigns
    # `base="${RUNNER_TEMP:?…}"` on one line and `echo "PYTEST_LOG=$base/…"`
    # on the next, so a same-line rule would false-reject it.
    resolves = any(
        "${RUNNER_TEMP:?" in body and "PYTEST_LOG=" in body
        for body in (s.get("run") or "" for s in steps))
    if not resolves:
        problems.append(
            f"{entry} (no step resolves PYTEST_LOG from $RUNNER_TEMP, failing "
            "closed on a missing one)")
    for s in steps:
        body = s.get("run") or ""
        named = sorted(set(_HOST_TMP_PATH_RE.findall("\n".join(_code_lines(body)))))
        if named:
            problems.append(
                f"{entry} step {s.get('name')!r} names host-global {named}")
        path = ((s.get("with") or {}).get("path"))
        if isinstance(path, str) and path.startswith("/tmp/"):
            problems.append(
                f"{entry} step {s.get('name')!r} uploads from host-global {path}")
    return problems


def test_migrated_legs_read_their_OWN_pytest_log():
    """#10280: a migrated leg must not touch the host-global `/tmp/pytest.log`.

    Two halves, because either alone is satisfiable without fixing anything:

    1. some step RESOLVES `PYTEST_LOG` from `$RUNNER_TEMP` and FAILS CLOSED on a
       missing one (`${RUNNER_TEMP:?...}`) — a silent `/tmp` fallback is
       indistinguishable from the bug;
    2. no step's CODE names a host-global `/tmp/...` path at all.

    Read by parsing the workflow, not by trusting the comment that describes it,
    and it reds on the pre-#10280 text (half 2: five `/tmp/pytest.log` lines in
    `test-concurrency-falkor`'s pytest step alone).

    ⛔ Half 2 is only load-bearing because `_HOST_TMP_PATH_RE` can MATCH: it read
    `[\\s=]>'\"` until the #10280 review (the `]` closed the class early), so it
    found 0 of the 50 `/tmp/<path>` occurrences on this workflow's non-comment
    lines — the corpus this test builds — and this pin went green on the mixed
    state below. Both non-vacuity probes are the two tests that follow it.
    """
    import yaml

    root = Path(__file__).resolve().parent.parent
    problems: list[str] = []
    for entry in _PYTEST_LOG_MIGRATED_JOBS:
        wf_name, job_name = entry.split("::", 1)
        wf = yaml.load(
            (root / ".github" / "workflows" / wf_name).read_text(),
            Loader=_actions_yaml())
        problems.extend(_migrated_leg_problems(
            entry, (wf.get("jobs") or {}).get(job_name)))
    assert problems == [], (
        "these legs still read or write the host-shared /tmp pytest artifact, so "
        f"their verdict can be another job's (#10280): {problems}")


# The five reader shapes measured against `origin/main:.github/workflows/python-ci.yml`
# during the #10280 review — every one of them is a real line of that workflow. The
# shipped `[\s=]>'\"` prefix matched NONE of them (all `[]`); the class form matches
# each. Keep the shapes verbatim: they are the evidence, not an illustration.
_MEASURED_HOST_TMP_SHAPES = {
    "> /tmp/pytest.log 2>&1": "/tmp/pytest.log",
    "p=$(grep -c ' PASSED' /tmp/pytest.log 2>/dev/null || true)": "/tmp/pytest.log",
    "tail -n 120 /tmp/pytest.log": "/tmp/pytest.log",
    'bash .github/scripts/pytest-attribution.sh --log /tmp/pytest.log --rc "$rc"':
        "/tmp/pytest.log",
    "tools/skip-guard.py /tmp/pytest.log --junitxml=/tmp/junit.xml "
    "--manifest /tmp/expected-nodeids.txt":
        "/tmp/expected-nodeids.txt",
}


def test_the_host_tmp_prefix_matches_every_measured_reader_shape():
    """#10280 review: the prefix is ONE character class, and it MATCHES.

    Non-vacuity for half 2 of the pin above. The shipped regex
    `(?:^|[\\s=]>'\")(/tmp/...)` let the `]` close the class after `\\s` and `=`,
    so its prefix demanded the literal run `<space-or-=> > ' "` — a shape no
    workflow contains. Measured over the NON-COMMENT lines of
    `origin/main:.github/workflows/python-ci.yml` (the corpus this pin builds):
    50 `/tmp/<path>` occurrences, `findall` = `[]` as shipped, 50 with the class
    form, and no occurrence in that corpus is left unmatched by the class form.
    This asserts the MATCH, per measured shape, so a prefix that closes its
    class early reds here by name instead of silently disarming the pin.
    """
    for line, expected in _MEASURED_HOST_TMP_SHAPES.items():
        found = _HOST_TMP_PATH_RE.findall(line)
        assert expected in found, (
            f"_HOST_TMP_PATH_RE does not match {expected!r} in the measured reader "
            f"shape {line!r} — got {found}. The prefix class must be "
            "`[\\s=>'\"]`, one class: with the class closed early it demands the "
            "literal run `<space>= > ' \"` and matches nothing (#10280 review)."
        )


def test_a_migrated_leg_whose_reader_is_reverted_to_host_tmp_reds():
    """#10280 review: the REALISTIC regression must be RED, not green.

    The mixed state this pin exists to catch is half-migrated, not un-migrated:
    the resolver step survives (so half 1 sees `PYTEST_LOG` published from
    `$RUNNER_TEMP`) while ONE reader is reverted to the host-global literal.
    That is exactly what produced `test-slow (a)`'s `850 expected nodeid(s)
    absent` from a co-tenant's manifest.

    Runs through `_migrated_leg_problems`, i.e. the same code the live pin runs,
    so this cannot pass while the live pin is vacuous: with the pre-fix prefix
    the problems list is empty here and this test fails.
    """
    resolver = {
        "name": "Resolve per-job pytest artifact paths",
        "run": (
            'base="${RUNNER_TEMP:?RUNNER_TEMP is not set}"\n'
            'echo "PYTEST_LOG=$base/pytest.log" >> "$GITHUB_ENV"\n'
        ),
    }
    migrated = _migrated_leg_problems(
        "synthetic.yml::leg",
        {"steps": [resolver,
                   {"name": "Skip-fail guard", "run": 'guard "$PYTEST_LOG"'}]},
    )
    assert migrated == [], f"the control case must be clean, got {migrated}"
    reverted = _migrated_leg_problems(
        "synthetic.yml::leg",
        {"steps": [resolver,
                   {"name": "Skip-fail guard", "run": "tail -n 120 /tmp/pytest.log"}]},
    )
    assert reverted, (
        "a half-migrated leg (resolver present, one reader back on the host-global "
        "`/tmp/pytest.log`) must RED — an empty list means the no-literal half is "
        "inert (#10280 review)"
    )
    assert "/tmp/pytest.log" in reverted[0], reverted


@pytest.mark.parametrize("err", [errno.EACCES, errno.EIO, errno.EBUSY, errno.EROFS])
def test_a_non_enotempty_cleanup_failure_still_raises(tmp_path, err):
    #7735: the tolerance is scoped to the ONE race, not to rmtree in general.
    # A blanket `ignore_cleanup_errors=True` would swallow these too.
    import tempfile

    with _patch_rmtree(err):
        d = tempfile.TemporaryDirectory(dir=str(tmp_path))
        with pytest.raises(OSError) as excinfo:
            d.cleanup()
        assert excinfo.value.errno == err


def test_tolerant_cleanup_install_is_idempotent_and_restorable():
    #7735: a second pytest.main() must not double-wrap, and the install must be
    # reversible like the sibling `install_scan_guard`.
    import tempfile

    from tests._tmpdir_hygiene import (
        install_tolerant_tempdir_cleanup,
        uninstall_tolerant_tempdir_cleanup,
    )

    install_tolerant_tempdir_cleanup()
    first = tempfile.TemporaryDirectory.cleanup
    install_tolerant_tempdir_cleanup()
    assert tempfile.TemporaryDirectory.cleanup is first, "double-install wrapped twice"
    try:
        uninstall_tolerant_tempdir_cleanup()
        assert not getattr(tempfile.TemporaryDirectory.cleanup,
                           "_tortoise_tolerant_cleanup", False), (
            "uninstall left the wrapper in place")
    finally:
        install_tolerant_tempdir_cleanup()
    assert getattr(tempfile.TemporaryDirectory.cleanup,
                   "_tortoise_tolerant_cleanup", False), "reinstall failed"


@pytest.mark.parametrize("gate,expected", [
    (None, False),            # implicit success(): skipped on a failing run
    (False, False),           # YAML bool — the round-6 regression
    ("false", False),
    ("success()", False),
    ("always()", True),
    ("${{ always() }}", True),
    ("always() ", True),
    ("ALWAYS()", True),        # GH expression names are case-insensitive
    ("Always()", True),
    ("yes", False),            # YAML 1.1 bools GH treats as invalid strings
    ("on", False),
    (True, True),
])
def test_a_surfacing_step_must_be_allowed_to_run(gate, expected):
    """#7735 review, round 6: `if: false` parses as the BOOL False.

    A truthiness fallback (`raw or "always()"`) silently treated a disabled step
    as allowed, so the guard added in round 5 was a no-op for the one mutation it
    exists to catch. This pins the predicate against the YAML-typed value, not a
    string the workflow never produces.
    """
    assert _always_runs(gate) is expected


def test_actions_typing_matches_the_runner_not_yaml_1_1():
    """#7735 review, round 7: `if: yes`/`if: on` are STRINGS to GitHub.

    PyYAML's YAML 1.1 resolver makes them the bool True, which would accept a
    gate the runner would never run — the same typing mismatch that produced the
    round-6 P1 (`if: false`). The pin must load workflows with runner typing.
    """
    import yaml

    loader = _actions_yaml()
    assert yaml.load("if: yes", Loader=loader)["if"] == "yes"
    assert yaml.load("if: on", Loader=loader)["if"] == "on"
    assert yaml.load("if: true", Loader=loader)["if"] is True
    assert yaml.load("if: false", Loader=loader)["if"] is False
    assert yaml.load("if: always()", Loader=loader)["if"] == "always()"
