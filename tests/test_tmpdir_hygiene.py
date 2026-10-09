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
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests._tmpdir_hygiene import (
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


def test_enotempty_cleanup_is_tolerated_and_STILL_REPORTED(tmp_path, caplog):
    #7735: a live server re-creates an entry during rmtree's final rmdir.
    # `shutil.rmtree` is patched GLOBALLY, so it is restored in a `finally`
    # BEFORE the test returns — otherwise pytest's own `tmp_path` teardown would
    # hit the stub and ERROR the test that just passed.
    import json
    import tempfile

    victim = None
    real = shutil.rmtree
    shutil.rmtree = _boom_with(errno.ENOTEMPTY)
    try:
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
    finally:
        shutil.rmtree = real
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


# Scope: the workflows whose jobs run the `tests/` UNIT suite and therefore own
# the #7735 leak surface. Deliberately NOT repo-wide. The surfaces this pin does
# NOT cover are named here with their reason, so the gap is deliberate and
# visible instead of resting on a false "those are e2e-only" inference:
#
#   * `ci.yml::welcome-e2e`, `welcome-e2e-monitor.yml` and the `evals-on-demand`
#     lanes run TARGETED file selections (e.g. tests/test_waitlist_form.py,
#     tests/eval/retrieval/test_integration.py), not the suite. They do import
#     `tests/conftest.py` and so install the tolerance, but the files they run
#     construct no `TemporaryDirectory`, so no leak can be tolerated there.
#   * `ci.yml::flip-gate` runs `bash .github/scripts/verify-cutover`, which runs
#     pytest on ten unit files (incl. tests/test_backup_sweep.py, which has 46
#     `TemporaryDirectory(` sites) from INSIDE A SHELL SCRIPT. No step there
#     surfaces the artifact today, and this pin cannot see it because it scans
#     step `run` text. Recorded as a known gap and tracked as follow-up on
#     #7735 — not silently excluded.
#
# A new suite-running workflow must be added here deliberately.
_SUITE_WORKFLOWS = ("python-ci.yml", "post-merge-validation.yml")
# A pytest INVOCATION, not a mention: bare `pytest`, `python -m pytest`,
# `python3 -m pytest`, `uv run pytest`. A literal allowlist of four strings let
# `pytest -q tests/` and bare `pytest` be added blind (#7735 review, round 3).
_PYTEST_CMD = re.compile(
    r"python3?\s+-m\s+pytest|uv\s+run\s+pytest|(?<![\w./-])pytest(?![\w.-])")
# A package install is not an invocation: a `pip install ... pytest-timeout …`
# line must not make a job look like it runs the suite.
_INSTALL_LINE = re.compile(r"\bpip3?\s+install\b")


def _executable_lines(run: str) -> list[str]:
    """The run text minus comment lines.

    A bare substring check over the whole ``run`` was satisfied by a COMMENT
    that merely mentioned the artifact, certifying "the string occurs" rather
    than "the artifact is surfaced" (#7735 review, round 3).
    """
    return [ln for ln in run.splitlines() if not ln.lstrip().startswith("#")]


def _runs_pytest(runs: list[str]) -> bool:
    return any(
        _PYTEST_CMD.search(ln) and not _INSTALL_LINE.search(ln)
        for run in runs for ln in _executable_lines(run))


def _surfaces_artifact(runs: list[str]) -> bool:
    """True when some run both names the artifact and reads it out.

    Both halves are required on EXECUTABLE lines: the pin's job is that a
    tolerated leak is OBSERVABLE, and a run that only names the file in a
    comment (or only `cat`s something else) does not surface it.
    """
    lines = [ln for run in runs for ln in _executable_lines(run)]
    names = any("tempdir-hygiene-end.json" in ln for ln in lines)
    reads = any("cat" in ln.split() for ln in lines)
    return names and reads


def test_every_pytest_job_surfaces_the_tolerated_leak():
    """#7735 review F1: the report is visible only where the workflow dumps it.

    Scope: every job that INVOKES pytest in a workflow listed in
    ``_SUITE_WORKFLOWS`` — NOT every pytest job in the repo; the constant's
    comment names the surfaces deliberately outside it and why.

    A job that runs the suite installs the process-global tolerant cleanup (via
    ``tests/conftest.py``) and can therefore write the artifact — but only a job
    whose workflow surfaces that file can report a tolerated leak. The first
    revision dumped it in the ``test`` job alone, so the ``test-slow`` legs that
    #7735 was actually measured on stayed blind. Derived from the workflow text
    — never a frozen list of job names, which would re-stale the moment a
    pytest job is renamed or added.
    """
    import yaml

    root = Path(__file__).resolve().parent.parent
    blind: list[str] = []
    covered: list[str] = []
    for wf_name in _SUITE_WORKFLOWS:
        wf = yaml.safe_load(
            (root / ".github" / "workflows" / wf_name).read_text())
        for job_name, job in wf["jobs"].items():
            runs = [step.get("run") or "" for step in (job.get("steps") or [])]
            if not _runs_pytest(runs):
                continue
            if _surfaces_artifact(runs):
                covered.append(f"{wf_name}::{job_name}")
            else:
                blind.append(f"{wf_name}::{job_name}")
    assert covered, (
        "no job in the suite workflows runs pytest — the scan found nothing "
        "to certify, so this pin would pass on an empty surface")
    assert blind == [], (
        "these jobs run pytest but never surface the tolerated-leak artifact "
        f"(#7735 review F1): {blind}")


@pytest.mark.parametrize("err", [errno.EACCES, errno.EIO, errno.EBUSY, errno.EROFS])
def test_a_non_enotempty_cleanup_failure_still_raises(tmp_path, err):
    #7735: the tolerance is scoped to the ONE race, not to rmtree in general.
    # A blanket `ignore_cleanup_errors=True` would swallow these too.
    import tempfile

    real = shutil.rmtree
    shutil.rmtree = _boom_with(err)
    try:
        d = tempfile.TemporaryDirectory(dir=str(tmp_path))
        with pytest.raises(OSError) as excinfo:
            d.cleanup()
        assert excinfo.value.errno == err
    finally:
        shutil.rmtree = real


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
