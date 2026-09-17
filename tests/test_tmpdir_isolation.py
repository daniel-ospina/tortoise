"""#3752 — isolation property tests for the private session temp root.

These assert the *property* the suite now depends on, rather than assuming
it: scratch dirs land inside one private root, child processes inherit it,
and discovery can no longer reach the shared system temp dir.

The regression guard is two-layered:

* runtime — ``os.scandir`` / ``os.walk`` / ``os.listdir`` on the shared temp
  dir (or an ancestor) raise ``SharedTmpdirScanError`` for the whole session,
  so a new test that reintroduces a scan fails loudly and by name;
* static — ``test_no_test_file_scans_the_shared_temp_dir`` catches the idiom
  even in a file that is skipped or not collected, at review time.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from tests._tmpdir_isolation import (
    HOST_TMPDIR,
    SharedTmpdirScanError,
    host_tempdir_entries,
    install_scan_guard,
    install_session_tmpdir,
    scan_root,
    session_tmpdir,
    uninstall_scan_guard,
)

_TESTS_DIR = Path(__file__).resolve().parent


# ── the root is installed, private, and short enough for AF_UNIX ─────────

def test_session_temp_root_is_installed_and_private():
    """tempfile.gettempdir() IS the private root — not the shared temp dir."""
    root = scan_root()
    assert tempfile.gettempdir() == root
    assert session_tmpdir() == root
    assert os.path.realpath(root) != HOST_TMPDIR, \
        "the session temp root must not BE the shared temp dir"
    assert root.startswith(os.path.realpath(HOST_TMPDIR) + os.sep), \
        "the session temp root must live inside the host temp dir (the\n" \
        "reaper's ephemeral classification is containment-based)"
    assert os.path.basename(root).startswith("tt_")


def test_session_temp_root_prefix_stays_reaper_recognised():
    """The root's prefix must remain in EPHEMERAL_PREFIXES — renaming it
    would silently reclassify every embedded server rooted there from
    'ephemeral/disposable' to the fail-closed 'protected' branch, and the
    session-end sweep would stop cleaning them up."""
    from tortoise.embedded_reaper import EPHEMERAL_PREFIXES
    assert os.path.basename(scan_root()).startswith(EPHEMERAL_PREFIXES), \
        "session temp root prefix is not in embedded_reaper.EPHEMERAL_PREFIXES"


def test_session_temp_root_is_short_enough_for_af_unix():
    """redislite nests <TMPDIR>/redislite_<rand>/redis.socket; the macOS
    AF_UNIX cap is 104 bytes. Guard the arithmetic rather than discovering
    it as a flaky 'socket path too long' at test time."""
    root = scan_root()
    worst_case = os.path.join(root, "redislite_" + "x" * 10, "redis.socket")
    assert len(worst_case) <= 104, (
        f"private temp root makes socket paths too long: {worst_case!r} "
        f"({len(worst_case)} > 104)")


def test_install_is_idempotent():
    """A defensive second install must not create a second root — teardown
    only removes the one it knows about."""
    before = {e.name for e in host_tempdir_entries()}
    again = install_session_tmpdir()
    assert again == scan_root()
    after = {e.name for e in host_tempdir_entries()}
    assert after - before == set(), \
        f"second install created a new temp root: {sorted(after - before)}"


# ── scratch created by the suite lands inside the root ───────────────────

def test_mkdtemp_lands_inside_the_private_root():
    """The leak class (#3752): every `tempfile.mkdtemp(prefix="tortoise_*")`
    in the suite resolves to the private root, so it is removed with it."""
    d = tempfile.mkdtemp(prefix="tortoise_test_")
    assert Path(d).resolve().is_relative_to(Path(scan_root()).resolve()), \
        f"mkdtemp escaped the private root: {d}"


def test_temporary_directory_and_named_file_land_inside_the_private_root():
    """The other two shapes the suite uses must be contained as well."""
    with tempfile.TemporaryDirectory(prefix="tortoise-lifecycle-") as d:
        assert Path(d).resolve().is_relative_to(Path(scan_root()).resolve())
    with tempfile.NamedTemporaryFile(prefix="tortoise_sdk_test_") as fh:
        assert Path(fh.name).resolve().is_relative_to(
            Path(scan_root()).resolve())


def test_child_process_inherits_the_private_root():
    """Containment must survive process boundaries: redislite daemons and the
    suite's subprocess writers/readers resolve TMPDIR from the env, and a
    child that fell back to the shared temp dir would re-open the leak (and
    put its socket where another session scans)."""
    out = subprocess.run(
        [sys.executable, "-c",
         "import tempfile; print(tempfile.gettempdir())"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert os.path.realpath(out) == os.path.realpath(scan_root()), \
        f"child process temp dir escaped containment: {out}"


# ── the scan guard ───────────────────────────────────────────────────────

@pytest.mark.parametrize("shared_path", [
    pytest.param(lambda: HOST_TMPDIR, id="host-root"),
    pytest.param(lambda: os.path.realpath(HOST_TMPDIR), id="realpath"),
    # an ANCESTOR is equally non-hermetic (it contains the whole shared tree)
    pytest.param(lambda: os.path.dirname(HOST_TMPDIR), id="ancestor"),
])
def test_guard_rejects_scandir_of_the_shared_temp_dir(shared_path):
    """The defect the issue measured: `find <shared T> -maxdepth 2 -name
    redis.socket`. Any in-process equivalent must fail, not silently walk."""
    with pytest.raises(SharedTmpdirScanError) as exc:
        list(os.scandir(shared_path()))
    assert "shared temp dir" in str(exc.value).lower()
    assert "scan_root()" in str(exc.value)


def test_guard_rejects_listdir_and_walk_of_the_shared_temp_dir():
    with pytest.raises(SharedTmpdirScanError):
        os.listdir(HOST_TMPDIR)
    with pytest.raises(SharedTmpdirScanError):
        list(os.walk(HOST_TMPDIR))


def test_guard_allows_the_private_root_and_descendants():
    """Legitimate scopes must keep working: the private root itself and the
    HOST-GLOBAL coordination dir (`<host>/.tortoise`, which the reaper's
    marker lookup and conftest's marker write both use)."""
    assert list(os.scandir(scan_root())) is not None
    coordination = os.path.join(HOST_TMPDIR, ".tortoise")
    os.makedirs(coordination, exist_ok=True)
    assert list(os.scandir(coordination)) is not None


def test_guard_is_installed_for_the_whole_session():
    """A regression can only be caught if the guard is actually installed —
    assert the live `os` attributes are the guarded wrappers, not a
    re-imported stdlib function."""
    import tests._tmpdir_isolation as iso
    assert iso._GUARD_INSTALLED is True
    assert os.scandir is not iso._ORIGINALS["scandir"]
    assert os.listdir is not iso._ORIGINALS["listdir"]
    assert os.walk is not iso._ORIGINALS["walk"]


def test_scan_root_never_falls_back_to_the_shared_temp_dir(monkeypatch):
    """With no isolation installed there is NO silent fallback: the accessor
    raises rather than handing back the shared tree (a fallback is exactly
    the degradation the guard exists to prevent)."""
    import tests._tmpdir_isolation as iso
    monkeypatch.setattr(iso, "_SESSION_TMPDIR", None)
    with pytest.raises(SharedTmpdirScanError) as exc:
        scan_root()
    assert "refusing to fall back" in str(exc.value)


def test_guard_can_be_removed_and_reinstalled():
    """The guard must be reversible (its own testability), and restoring must
    put the ORIGINAL functions back — not a re-wrapped copy."""
    import tests._tmpdir_isolation as iso
    originals = dict(iso._ORIGINALS)
    try:
        uninstall_scan_guard()
        assert os.scandir is originals["scandir"]
        # while uninstalled, the shared dir is reachable again — this is the
        # pre-#3752 behaviour the guard exists to prevent
        assert list(os.scandir(HOST_TMPDIR)) is not None
    finally:
        install_scan_guard()
    assert os.scandir is not originals["scandir"]
    with pytest.raises(SharedTmpdirScanError):
        list(os.scandir(HOST_TMPDIR))


# ── discovery itself is scoped ───────────────────────────────────────────

def test_find_socket_dirs_is_called_with_the_private_root(monkeypatch):
    """The scoped-discovery assertion: the reaper's socket/pid walk gets the
    PRIVATE root. This is the call that produced the 2-minute `find` over the
    shared temp dir at 53% CPU."""
    from tortoise import embedded_reaper as reaper
    seen: list[str] = []
    monkeypatch.setattr(reaper, "_find_socket_dirs",
                        lambda root: seen.append(root) or [])
    reaper.discover()
    assert seen, "_find_socket_dirs was never called — test proves nothing"
    assert all(os.path.realpath(r) == os.path.realpath(scan_root())
               for r in seen), f"discovery scanned {seen!r}, expected the "\
                               f"private root {scan_root()!r}"


def _make_session_root(parent: Path, name: str) -> Path:
    """A directory that _socket_walk_roots must recognise as a session root:
    the `tt_` prefix AND the marker file the harness writes."""
    from tortoise.embedded_reaper import SESSION_ROOT_MARKER
    root = parent / name
    root.mkdir(parents=True)
    (root / SESSION_ROOT_MARKER).write_text(f"pid={os.getpid()}\n")
    return root


def test_marker_name_matches_the_reaper_contract():
    """tests/_tmpdir_isolation._PID_MARKER is the name
    tortoise.embedded_reaper.SESSION_ROOT_MARKER requires. If these drift,
    every session root silently stops being swept AND silently stops being
    recognised — both failures are invisible without this pin."""
    from tests import _tmpdir_isolation
    from tortoise import embedded_reaper
    assert _tmpdir_isolation._PID_MARKER == \
        embedded_reaper.SESSION_ROOT_MARKER
    assert os.path.exists(os.path.join(scan_root(), _tmpdir_isolation._PID_MARKER)), \
        "the live session root must carry the marker the reaper looks for"


def test_socket_walk_reaches_into_a_nested_session_scratch_root(tmp_path):
    """#3752 regression, found in review: the suite nests its scratch one
    level deeper (`<tmpdir>/tt_<8-char>/<socket dir>/redis.socket` = depth 3),
    which the walk's maxdepth-2 global pass cannot reach — so a SIGKILLed
    suite's dead socket dirs would stop being swept by the host reaper.

    Widening the single walk to depth 3 is not the fix: measured on a
    23k-entry tempdir, depth 2 took 6.2s and depth 3 took 53.3s, over
    `SOCKET_WALK_TIMEOUT` — and a timed-out walk returns [], re-opening the
    #1449 pollution-disables-cleanup hole. The bounded nested pass this
    asserts costs one `find` per recognised session root instead."""
    from tortoise.embedded_reaper import _find_socket_dirs

    direct = tmp_path / "redislite_direct"
    direct.mkdir()
    (direct / "redis.socket").write_text("")

    session = _make_session_root(tmp_path, "tt_abc12345")
    nested = session / "redislite_nested"
    nested.mkdir()
    (nested / "redis.socket").write_text("")

    pid_session = _make_session_root(tmp_path, "tt_def67890")
    pid_only = pid_session / "tmpXYZ"
    pid_only.mkdir()
    (pid_only / "redis.pid").write_text("1\n")

    found = _find_socket_dirs(str(tmp_path))
    assert str(direct) in found, "depth-2 global pass regressed"
    assert str(nested) in found, \
        "socket dir nested inside a tt_ session root was not reached"
    assert str(pid_only) in found, \
        "redis.pid marker nested inside a tt_ session root was not reached"


def test_socket_walk_ignores_legacy_tt_prefixed_scratch_dirs(tmp_path):
    """#3752 review finding: `tt_` is ALSO the long-standing prefix of plain
    test-scratch dirs (`tt_211_`, `tt_1162_`, `tt_395_`, ...). Matching the
    prefix alone made `_socket_walk_roots` return 252 roots on the polluted
    host this was measured on, 207 of which were skipped once the shared walk
    budget ran out — starving the real session root the nested pass exists
    for. Only a root carrying SESSION_ROOT_MARKER is a session root."""
    from tortoise.embedded_reaper import _find_socket_dirs, _socket_walk_roots

    legacy = tmp_path / "tt_211_deadbeef"
    (legacy / "redislite_legacy").mkdir(parents=True)
    (legacy / "redislite_legacy" / "redis.socket").write_text("")

    roots = _socket_walk_roots(str(tmp_path))
    assert str(legacy) not in roots, \
        "a bare tt_* test-scratch dir was treated as a session root"
    assert roots[-1] == str(tmp_path), "the tempdir root must still be walked"
    assert str(legacy / "redislite_legacy") not in \
        _find_socket_dirs(str(tmp_path)), \
        "a legacy tt_* scratch dir's socket must not be reached as a session root"


def test_socket_walk_orders_nested_roots_before_the_tempdir(tmp_path):
    """The nested roots come first so a slow global pass cannot consume the
    shared walk budget before the dirs only the nested pass can reach."""
    from tortoise.embedded_reaper import _socket_walk_roots

    _make_session_root(tmp_path, "tt_aaaaaaaa")
    roots = _socket_walk_roots(str(tmp_path))
    assert roots[-1] == str(tmp_path)
    assert roots[0] == str(tmp_path / "tt_aaaaaaaa"), \
        f"nested root must precede the tempdir, got {roots!r}"


def test_quarantine_walk_reaches_nested_session_roots(tmp_path, monkeypatch):
    """#3752 review finding: the rename-aside is IN PLACE, so a quarantined
    dir under a nested session root is invisible to a `-maxdepth 1` walk of
    the tempdir root alone and would never converge."""
    from tortoise import embedded_reaper as reaper

    session = _make_session_root(tmp_path, "tt_abc12345")
    q = session / f"redislite_live{reaper.STALE_QUARANTINE_SUFFIX}123"
    q.mkdir()
    (q / reaper.REAPER_OWNED_MARKER).write_text("")
    (q / "redis.socket").write_text("")
    (q / "redis.pid").write_text("1\n")

    monkeypatch.setattr(reaper, "_real_gettempdir", lambda: str(tmp_path))
    argv_log: list[list[str]] = []
    real_run = subprocess.run

    def recording_run(cmd, *a, **kw):
        argv_log.append([str(c) for c in cmd])
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(reaper.subprocess, "run", recording_run)
    reaper._sweep_quarantine_dirs(dry_run=True)
    roots = [argv[1] for argv in argv_log if "-maxdepth" in argv]
    assert str(session) in roots, \
        f"the nested session root was not walked for quarantines: {roots!r}"
    assert str(tmp_path) in roots, "the tempdir root walk regressed"


def test_discover_never_shells_out_to_the_shared_temp_dir(monkeypatch):
    """The `find` walk is a subprocess, so the in-process guard cannot see
    it. Record every argv and assert no subprocess is ever aimed at the
    shared temp dir (or an ancestor of it)."""
    from tests._tmpdir_isolation import _is_host_tempdir_scope
    from tortoise import embedded_reaper as reaper
    real_run = subprocess.run
    argv_log: list[list[str]] = []

    def recording_run(cmd, *a, **kw):
        argv_log.append([str(c) for c in cmd] if isinstance(cmd, (list, tuple))
                        else [str(cmd)])
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(reaper.subprocess, "run", recording_run)
    reaper.discover()
    assert argv_log, "no subprocess recorded — test proves nothing"
    offenders = [argv for argv in argv_log
                 if any(_is_host_tempdir_scope(tok) for tok in argv)]
    assert not offenders, \
        f"discovery shelled out to the shared temp dir: {offenders}"


# ── stale-root reclaim: pid + start time, never a bare pid ───────────────

def _stale_root(tmp_path: Path, marker: str) -> Path:
    root = tmp_path / "tt_deadbeef"
    root.mkdir()
    (root / ".session-pid").write_text(marker)
    return root


def test_sweep_reclaims_root_of_a_dead_process(tmp_path, monkeypatch):
    """A SIGKILLed suite cannot run its own teardown; the next suite must
    reclaim its root."""
    from tests import _tmpdir_isolation as iso

    root = _stale_root(tmp_path, "pid=999999\nstart=1.0\n")
    monkeypatch.setattr(iso, "HOST_TMPDIR", str(tmp_path))
    assert iso.sweep_stale_session_roots() == [str(root)]
    assert not root.exists()


def test_sweep_preserves_root_of_a_live_concurrent_suite(tmp_path, monkeypatch):
    """A live pid with a MATCHING start time is a concurrent suite — its root
    must never be deleted."""
    from tests import _tmpdir_isolation as iso

    start = iso._process_start_time(os.getpid())
    assert start is not None, "cannot verify start time; test proves nothing"
    root = _stale_root(tmp_path, f"pid={os.getpid()}\nstart={start}\n")
    monkeypatch.setattr(iso, "HOST_TMPDIR", str(tmp_path))
    assert iso.sweep_stale_session_roots() == []
    assert root.exists()


def test_sweep_reclaims_root_whose_pid_was_recycled(tmp_path, monkeypatch):
    """#1642 FIX 5: `os.kill(pid, 0)` alone proves nothing — the pid may now
    belong to an unrelated process, pinning a dead suite's root forever. A
    live pid whose start time does NOT match is reclaimable."""
    from tests import _tmpdir_isolation as iso

    start = iso._process_start_time(os.getpid())
    assert start is not None
    root = _stale_root(tmp_path, f"pid={os.getpid()}\nstart={start - 3600}\n")
    monkeypatch.setattr(iso, "HOST_TMPDIR", str(tmp_path))
    assert iso.sweep_stale_session_roots() == [str(root)]
    assert not root.exists()


def test_sweep_keeps_root_when_the_owner_is_undeterminable(tmp_path, monkeypatch):
    """Every undeterminable case fails SAFE: a pid-only (legacy) marker, and
    an unparseable marker, must both survive."""
    from tests import _tmpdir_isolation as iso

    pid_only = _stale_root(tmp_path, f"pid={os.getpid()}\n")
    monkeypatch.setattr(iso, "HOST_TMPDIR", str(tmp_path))
    assert iso.sweep_stale_session_roots() == []
    assert pid_only.exists()

    garbage = tmp_path / "tt_garbage0"
    garbage.mkdir()
    (garbage / ".session-pid").write_text("not-a-marker\n")
    assert iso.sweep_stale_session_roots() == []
    assert garbage.exists()


def test_sweep_ignores_tt_dirs_without_a_marker(tmp_path, monkeypatch):
    """Legacy `tt_*` test scratch is not ours; no marker means no delete."""
    from tests import _tmpdir_isolation as iso

    foreign = tmp_path / "tt_211_legacy"
    foreign.mkdir()
    monkeypatch.setattr(iso, "HOST_TMPDIR", str(tmp_path))
    assert iso.sweep_stale_session_roots() == []
    assert foreign.exists()


# ── bytes paths must not blow up the process-wide guard ──────────────────

def test_guard_tolerates_bytes_and_pathlike_shared_paths():
    """os.scandir / os.listdir / os.walk all accept bytes, and the guard sees
    EVERY call in the session, so a bytes path must be decoded rather than
    reaching str.startswith as bytes (that raised a misleading TypeError from
    inside the guard, process-wide)."""
    from tests._tmpdir_isolation import _is_host_tempdir_scope

    assert _is_host_tempdir_scope(os.fsencode(HOST_TMPDIR)) is True
    assert _is_host_tempdir_scope(Path(HOST_TMPDIR)) is True
    assert _is_host_tempdir_scope(os.fsencode(scan_root())) is False
    assert _is_host_tempdir_scope(12345) is False


# ── static guard: the idiom cannot come back unnoticed ───────────────────

_SHARED_SCAN_IDIOMS = (
    "scandir(tempfile.gettempdir())",
    "walk(tempfile.gettempdir())",
    "listdir(tempfile.gettempdir())",
    "scandir(os.path.realpath(tempfile.gettempdir()))",
    "walk(os.path.realpath(tempfile.gettempdir()))",
    "listdir(os.path.realpath(tempfile.gettempdir()))",
    "scandir(_real_gettempdir())",
    "walk(_real_gettempdir())",
    "listdir(_real_gettempdir())",
)


def test_no_test_file_scans_the_shared_temp_dir():
    """Static companion to the runtime guard: the runtime guard only fires on
    a test that actually runs, so this catches the idiom in a skipped or
    uncollected file, and points at file:line for the fix."""
    hits: list[str] = []
    for path in sorted(_TESTS_DIR.rglob("*.py")):
        # Both this file and _tmpdir_isolation.py necessarily SPELL the
        # forbidden idioms (this one detects them, that one documents and
        # guards them); neither can call them.
        if path.name in ("_tmpdir_isolation.py", Path(__file__).name):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if line.lstrip().startswith("#"):
                continue
            for idiom in _SHARED_SCAN_IDIOMS:
                if idiom in line:
                    hits.append(
                        f"{path.relative_to(_TESTS_DIR.parent)}:{lineno}: {line.strip()}")
    assert not hits, (
        "test(s) scan the SHARED temp dir — use "
        "tests._tmpdir_isolation.scan_root() or the test's own tmp_path "
        "(#3752):\n  " + "\n  ".join(hits))
