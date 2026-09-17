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


def test_socket_walk_reaches_into_a_nested_session_scratch_root(tmp_path):
    """#3752 regression, found in review: the suite nests its scratch one
    level deeper (`<tmpdir>/tt_<8-char random>/<socket dir>/redis.socket` = depth 3),
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

    nested = tmp_path / "tt_abc12345" / "redislite_nested"
    nested.mkdir(parents=True)
    (nested / "redis.socket").write_text("")

    pid_only = tmp_path / "tt_def67890" / "tmpXYZ"
    pid_only.mkdir(parents=True)
    (pid_only / "redis.pid").write_text("1\n")

    found = _find_socket_dirs(str(tmp_path))
    assert str(direct) in found, "depth-2 global pass regressed"
    assert str(nested) in found, \
        "socket dir nested inside a tt_ session root was not reached"
    assert str(pid_only) in found, \
        "redis.pid marker nested inside a tt_ session root was not reached"


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
