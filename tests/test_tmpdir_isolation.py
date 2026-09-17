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
import time
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
    """The REAL worst case, not a one-level approximation (#3752 review
    cycle 4 — the approximation is why an AF_UNIX regression shipped): the
    suite's scratch nests two levels, `<root>/<scratch>/<redislite autogen>/
    redis.socket`, and the redislite autogen dir is its default `tmp` prefix
    plus 8 random chars. The kernel needs the bind path to stay UNDER 104 bytes
    on macOS, and a child process that sets its OWN TMPDIR
    (tests/test_embedded_concurrency.py) gets one more level of the same
    shape. Guard the arithmetic here rather than discovering it as a flaky
    'socket path too long' at test time.
    """
    root = scan_root()
    # The deepest path the suite itself creates: a test scratch dir inside the
    # root, then redislite's autogen dir, then the socket. The autogen dir name
    # is DERIVED from tempfile's own prefix (the redislite typos this replaced
    # — `tmps` vs `tmp` — both satisfied the old hand-written bound), and its
    # length is asserted so a prefix change fails by name.
    autogen = tempfile.gettempprefix() + "x" * 8
    assert len(autogen) == 11, \
        f"redislite autogen dir shape changed: {autogen!r} is {len(autogen)}"
    deepest = os.path.join(root, "" + "x" * 8, autogen, "redis.socket")
    assert len(deepest) < 104, (
        f"private temp root makes socket paths too long: {deepest!r} "
        f"({len(deepest)} >= 104)")
    # A child that inherits a TMPDIR nested one level under the root
    # (test_embedded_concurrency._make_flat_tmpdir) must still fit: its bind
    # path is its TMPDIR + "/<autogen>/redis.socket", so the child TMPDIR has
    # to stay at or below 78 bytes.
    child_tmpdir = os.path.join(root, "" + "x" * 8)
    child_bind = os.path.join(child_tmpdir, autogen, "redis.socket")
    assert len(child_bind) < 104, (
        f"child TMPDIR under the private root leaves no AF_UNIX room: "
        f"{child_bind!r} ({len(child_bind)} >= 104)")
    assert len(child_tmpdir) <= 78, (
        f"child TMPDIR budget is {len(child_tmpdir)} bytes > 78 — a longer "
        f"HOST_TMPDIR would break the spawned-server tests")


def test_install_is_idempotent():
    """A defensive second install must not create a second root — teardown
    only removes the one it knows about.

    Scoped to THIS pid's roots rather than to a before/after snapshot of the
    whole host temp dir: other pi sessions and the box's cruft sweeper add and
    remove entries in that window, which made the snapshot version fail for
    reasons that have nothing to do with the isolation.
    """
    mine = _session_roots_owned_by(os.getpid())
    assert len(mine) == 1, f"expected exactly one root for this pid, got {mine}"
    again = install_session_tmpdir()
    assert again == scan_root()
    assert _session_roots_owned_by(os.getpid()) == mine, \
        "second install created a second temp root for this pid"


def _session_roots_owned_by(pid: int) -> set[str]:
    """Host-tempdir `tt_*` roots whose marker names ``pid``. Read-only, and
    filtered to this process, so concurrent suites cannot perturb it."""
    owned: set[str] = set()
    for entry in host_tempdir_entries():
        marker = os.path.join(entry.path, ".session-pid")
        try:
            with open(marker) as fh:
                text = fh.read()
        except OSError:
            continue
        if f"pid={pid}\n" in text:
            owned.add(entry.name)
    return owned


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


def test_socket_walk_reserves_budget_for_the_global_pass(tmp_path, monkeypatch):
    """#3752 review cycle 2/3: nested session roots are walked FIRST, so N of
    them could consume the single shared deadline and silently skip the GLOBAL
    (tempdir) pass — the only pass that reaches non-session dirs.

    Behavioural, not arithmetic: the nested roots' `find` is made to spend its
    WHOLE timeout, and the test asserts the global root is still invoked with
    a POSITIVE timeout. With the reserve disabled the first nested root spends
    the entire budget and the global root is never invoked — which this test
    proves by running that case too (so it does not merely mirror the
    constant), and it is independent of GLOBAL_WALK_RESERVE_S' value.
    """
    from tortoise import embedded_reaper as reaper

    nested = [str(tmp_path / "tt_aaaaaaaa"), str(tmp_path / "tt_bbbbbbbb")]
    for r in nested:
        os.makedirs(r, exist_ok=True)
    global_root = str(tmp_path)
    monkeypatch.setattr(reaper, "_socket_walk_roots",
                        lambda _tmpdir: [*nested, global_root])
    monkeypatch.setattr(reaper, "SOCKET_WALK_TIMEOUT", 0.5)
    monkeypatch.setattr(reaper, "GLOBAL_WALK_RESERVE_S", 0.25)
    called: list[tuple[str, float]] = []

    def slow_nested_run(cmd, *a, **kw):
        root, timeout = str(cmd[1]), float(kw.get("timeout", -1))
        called.append((root, timeout))
        if root != global_root:
            time.sleep(max(timeout, 0.0) + 0.05)  # spend the whole cap
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(reaper.subprocess, "run", slow_nested_run)
    reaper._find_socket_dirs(str(tmp_path))
    assert called and called[-1][0] == global_root, \
        f"the reserved global root was never walked: {called!r}"
    assert called[-1][1] > 0, f"the global pass got no budget: {called!r}"

    # And the counter-case: with the reserve disabled the nested root eats the
    # budget and the global pass IS starved — so the assertion above is
    # load-bearing rather than an artefact of the cap.
    monkeypatch.setattr(reaper, "GLOBAL_WALK_RESERVE_S", 0.0)
    starved: list[str] = []

    def starving_run(cmd, *a, **kw):
        root, timeout = str(cmd[1]), float(kw.get("timeout", -1))
        starved.append(root)
        if root != global_root:
            time.sleep(max(timeout, 0.0) + 0.05)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(reaper.subprocess, "run", starving_run)
    reaper._find_socket_dirs(str(tmp_path))
    assert global_root not in starved, (
        "without the reserve the global pass should have been starved — the "
        "behavioural test no longer pins anything"
    )


def test_walk_deadline_starts_after_root_discovery(tmp_path, monkeypatch):
    """#3752 review cycle 3/4: `_socket_walk_roots` does real I/O (a scandir
    plus a stat per `tt_*` child). Charging it to the walk budget let a slow
    discovery issue ZERO `find` calls — the pollution-disables-cleanup failure
    the nested pass exists to prevent. Covered for BOTH walk call sites.

    The 20 s constant is monkeypatched: the ordering being asserted is
    scale-invariant, so sleeping the real budget would burn 20 s of wall clock
    for nothing.
    """
    from tortoise import embedded_reaper as reaper

    monkeypatch.setattr(reaper, "SOCKET_WALK_TIMEOUT", 0.5)

    def slow_roots(tmpdir):
        time.sleep(0.5 + 0.2)  # longer than the (patched) budget
        return [str(tmpdir)]

    seen: list[str] = []

    def recording_run(cmd, *a, **kw):
        seen.append(str(cmd[1]))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(reaper, "_socket_walk_roots", slow_roots)
    monkeypatch.setattr(reaper, "_real_gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(reaper.subprocess, "run", recording_run)
    reaper._find_socket_dirs(str(tmp_path))
    assert str(tmp_path) in seen, \
        f"_find_socket_dirs: discovery time was charged to the walk {seen!r}"

    seen.clear()
    reaper._sweep_quarantine_dirs(dry_run=True)
    assert str(tmp_path) in seen, \
        f"_sweep_quarantine_dirs: discovery charged to the walk {seen!r}"


def test_env_spelling_of_the_shared_tempdir_is_blocked():
    """#3752 review cycles 4-6: on macOS `$TMPDIR` is `/var/folders/...` while
    the realpath is `/private/var/folders/...`, and on GitHub's ubuntu runners
    `$TMPDIR` is UNSET entirely (so the spelling is
    `tempfile.gettempdir()`'s own fallback). A string branch matching only the
    realpath fails OPEN on both — the canonical spelling is the one the
    issue's own `find <shared TMPDIR>` used.

    Every assertion is driven from a spelling that EXISTS on the running host,
    so the test is meaningful with or without `$TMPDIR`.
    """
    from tests import _tmpdir_isolation as iso

    # The string branch must recognise every spelling it advertises. Whether
    # the RAW fallback (`/tmp` when `$TMPDIR` is unset — the GitHub-runner case)
    # is among them is pinned non-vacuously by
    # test_spelling_list_is_not_vacuous, which re-imports in a subprocess with
    # TMPDIR='' rather than reading the tuple it asserts about.
    if iso._ENV_TMPDIR_AT_IMPORT:
        assert iso._ENV_TMPDIR_AT_IMPORT in iso._TMPDIR_SPELLINGS
        assert os.path.realpath(iso._ENV_TMPDIR_AT_IMPORT) \
            in iso._TMPDIR_SPELLINGS
    for spelling in iso._TMPDIR_SPELLINGS:
        assert iso._command_touches_host_tempdir(f"find {spelling}") is not None, \
            f"spelling {spelling!r} is not recognised by the string branch"

    spellings = ([iso._ENV_TMPDIR_AT_IMPORT] if iso._ENV_TMPDIR_AT_IMPORT
                 else ["/tmp", os.path.realpath("/tmp")])
    for spelling in spellings:
        with pytest.raises(SharedTmpdirScanError):
            subprocess.run(["find", spelling, "-maxdepth", "2"],
                           capture_output=True, check=False)
        with pytest.raises(SharedTmpdirScanError):
            subprocess.run(f"find {spelling} -maxdepth 2", shell=True,
                           capture_output=True, check=False)
        with pytest.raises(SharedTmpdirScanError):
            os.system(f"ls {spelling}")
    # ...while the session root (a DESCENDANT, reachable via the redirect) must
    # still be allowed — the guard blocks the shared tree, not our own scratch.
    assert iso._command_touches_host_tempdir(f"find {scan_root()}") is None


def test_spelling_list_is_not_vacuous(monkeypatch):
    """#3752 review cycle 6: the loop above iterates the tuple under test, so
    it passes for ANY contents. This pins the raw-fallback entry from OUTSIDE:
    a subprocess with `TMPDIR=""` must advertise both `/tmp` and its realpath.
    """
    code = (
        "import sys; sys.path.insert(0, '.');"
        "from tests import _tmpdir_isolation as iso;"
        "assert '/tmp' in iso._TMPDIR_SPELLINGS, iso._TMPDIR_SPELLINGS;"
        "assert iso._command_touches_host_tempdir('find /tmp -maxdepth 2');"
        "print('OK')"
    )
    env = dict(os.environ, TMPDIR="", TORTOISE_TEST_CARVE_OUT="1")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, env=env, cwd=str(Path.cwd()))
    assert out.returncode == 0, (
        f"raw-fallback spelling not recognised with TMPDIR unset:\n"
        f"stdout={out.stdout!r}\nstderr={out.stderr[-2000:]}")


def test_teardown_after_fail_closed_abort_keeps_operator_env(tmp_path,
                                                            monkeypatch):
    """#3752 review cycle 5: a teardown that consumed no install (the extra
    atexit registration left by the fail-closed abort) must not touch the
    environment — restoring before the `if not root` guard destroyed an
    operator-supplied TORTOISE_HOST_TMPDIR."""
    from tests import _tmpdir_isolation as iso

    real_root, real_host = iso._SESSION_TMPDIR, iso.HOST_TMPDIR
    real_env = os.environ.get("TORTOISE_HOST_TMPDIR")
    real_env_tmpdir = os.environ.get("TMPDIR")
    real_prev = iso._PREV_HOST_TMPDIR_ENV
    os.environ["TORTOISE_HOST_TMPDIR"] = "/OPERATOR/VALUE"
    iso._SESSION_TMPDIR = None
    iso.HOST_TMPDIR = str(tmp_path)

    def boom(_root):
        raise OSError("read-only temp dir")

    monkeypatch.setattr(iso, "_write_marker", boom)
    try:
        with pytest.raises(iso.SessionIsolationError):
            iso.install_session_tmpdir()
        # The abort already cleaned up; a SECOND teardown is what the extra
        # atexit registration does at interpreter exit.
        iso.teardown_session_tmpdir()
        assert os.environ.get("TORTOISE_HOST_TMPDIR") == "/OPERATOR/VALUE", \
            "a no-op teardown destroyed the operator's TORTOISE_HOST_TMPDIR"
    finally:
        monkeypatch.undo()
        iso._SESSION_TMPDIR = real_root
        iso.HOST_TMPDIR = real_host
        iso._PREV_HOST_TMPDIR_ENV = real_prev
        if real_env is None:
            os.environ.pop("TORTOISE_HOST_TMPDIR", None)
        else:
            os.environ["TORTOISE_HOST_TMPDIR"] = real_env
        if real_env_tmpdir is not None:
            os.environ["TMPDIR"] = real_env_tmpdir
        tempfile.tempdir = real_root


def test_probe_socket_falls_back_to_tmp_for_a_link_that_cannot_fit(
        tmp_path, monkeypatch):
    """#3752 review cycle 3: the over-long-socket fallback must pick a link
    directory whose FULL path fits in sun_path — the private session root can
    eat most of that budget, and a failed symlink would make every probe
    'undetermined' (quarantines never converge). Also pins that the link is
    unlinked again."""
    from tortoise import embedded_reaper as reaper

    long_dir = tmp_path / ("d" * 60)
    long_dir.mkdir()
    sock = str(long_dir / "redis.socket")
    assert len(sock.encode()) > 100, "fixture must exercise the long-path branch"
    monkeypatch.setattr(reaper, "_real_gettempdir", lambda: str(long_dir))
    linked: list[str] = []
    unlinked: list[str] = []

    monkeypatch.setattr(reaper.os, "symlink",
                        lambda src, dst: (linked.append(dst), None)[1])
    monkeypatch.setattr(reaper.os, "unlink", lambda p: unlinked.append(p))
    monkeypatch.setattr(reaper, "_probe_socket", lambda p, timeout=None: "dead")

    assert reaper._probe_socket_any(sock) == "dead"
    assert linked, "no link was created at all"
    assert linked[0].startswith("/tmp" + os.sep), \
        f"the fallback did not leave the too-deep temp dir: {linked[0]!r}"
    assert len(linked[0].encode()) <= 100, \
        f"the fallback link itself would not fit: {linked[0]!r}"
    assert unlinked == linked, "the fallback link was not unlinked"


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


def test_marker_write_failure_fails_closed(tmp_path, monkeypatch):
    """#3752 review cycle 2: an unmarked root is invisible to the reaper's
    nested pass (it requires SESSION_ROOT_MARKER) AND unreclaimable by
    sweep_stale_session_roots (no marker -> keep) — a permanent leak after a
    SIGKILL. So a marker-write failure must abort loudly and leave nothing
    behind, never run the suite unisolated against an unusable root."""
    from tests import _tmpdir_isolation as iso

    real_root, real_host = iso._SESSION_TMPDIR, iso.HOST_TMPDIR
    real_env_tmpdir = os.environ.get("TMPDIR")
    real_env_host = os.environ.get("TORTOISE_HOST_TMPDIR")
    iso._SESSION_TMPDIR = None
    iso.HOST_TMPDIR = str(tmp_path)

    def boom(_root):
        raise OSError("read-only temp dir")

    monkeypatch.setattr(iso, "_write_marker", boom)
    try:
        with pytest.raises(iso.SessionIsolationError) as excinfo:
            iso.install_session_tmpdir()
        assert "marker" in str(excinfo.value)
        # host_tempdir_entries() is the UNGUARDED scan; it is the sanctioned
        # way to look into the (here: patched) host temp dir — tmp_path
        # itself cannot be iterdir'd while HOST_TMPDIR points at it.
        leftovers = [e.name for e in iso.host_tempdir_entries()]
        assert not any(n.startswith("tt_") for n in leftovers), \
            f"a root survived the fail-closed abort: {leftovers}"
        assert os.environ.get("TORTOISE_HOST_TMPDIR") != str(tmp_path), \
            "the aborted install left a stale TORTOISE_HOST_TMPDIR behind"
    finally:
        monkeypatch.undo()
        iso._SESSION_TMPDIR = real_root
        iso.HOST_TMPDIR = real_host
        if real_env_tmpdir is not None:
            os.environ["TMPDIR"] = real_env_tmpdir
        if real_env_host is not None:
            os.environ["TORTOISE_HOST_TMPDIR"] = real_env_host
        tempfile.tempdir = real_root
    assert tempfile.gettempdir() == real_root, \
        "the session redirect was not restored after the abort"


def test_guard_blocks_shutil_rmtree_of_the_shared_temp_dir(tmp_path):
    """The dangerous case: on macOS `shutil._use_fd_functions` is True, so
    rmtree scans an int FD — invisible to a path-only guard — and
    `shutil.rmtree(<shared temp dir>)` would delete the whole shared tree.
    Verified against a FAKE host temp dir, never the real one."""
    import shutil

    from tests import _tmpdir_isolation as iso

    fake = tmp_path / "fakehost"
    (fake / "a" / "b").mkdir(parents=True)
    (fake / "a" / "b" / "f").write_text("")
    real = iso.HOST_TMPDIR
    iso.HOST_TMPDIR = os.path.realpath(str(fake))
    try:
        with pytest.raises(SharedTmpdirScanError):
            shutil.rmtree(str(fake))
    finally:
        iso.HOST_TMPDIR = real
    assert (fake / "a" / "b" / "f").exists(), \
        "rmtree was refused only after it had already deleted files"


def test_guard_blocks_fd_scandir_of_the_shared_temp_dir():
    """An fd is a legal `os.scandir` argument, so the guard must resolve it
    rather than skip it (an int has no fspath — it must not raise TypeError
    while reporting the violation either)."""
    from tests import _tmpdir_isolation as iso

    fd = os.open(iso.HOST_TMPDIR, os.O_RDONLY)
    try:
        with pytest.raises(SharedTmpdirScanError):
            os.scandir(fd)
    finally:
        os.close(fd)
    private_fd = os.open(scan_root(), os.O_RDONLY)
    try:
        assert list(os.scandir(private_fd)) is not None, \
            "an fd into the PRIVATE root must still be usable"
    finally:
        os.close(private_fd)


def test_guard_blocks_shelling_out_to_the_shared_temp_dir():
    """The in-process patch cannot see a subprocess, and the shell-out is the
    exact shape of the original #3752 defect (`find <shared T> -maxdepth 2`).
    The audit hook must reject the argv form, the `shell=True`/string form and
    `os.system` — a one-line shell-out is otherwise a total bypass — while
    still allowing the private root (a DESCENDANT) as an argv token."""
    allowed = subprocess.run(["find", scan_root(), "-maxdepth", "1"],
                             capture_output=True, check=False)
    assert allowed.returncode == 0
    with pytest.raises(SharedTmpdirScanError):
        subprocess.run(["find", HOST_TMPDIR, "-maxdepth", "2"],
                       capture_output=True, check=False)
    with pytest.raises(SharedTmpdirScanError):
        subprocess.run(f"find {HOST_TMPDIR} -maxdepth 2", shell=True,
                       capture_output=True, check=False)
    with pytest.raises(SharedTmpdirScanError):
        os.system(f"ls {HOST_TMPDIR}")


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
    # The shell-out forms (#3752 review cycles 2-3): the original defect was a
    # `find <shared T>` subprocess, which the in-process patch cannot see, so
    # the static layer names the shape too — but ANCHORED to the shared-dir
    # token, so a correctly scoped `find <tmp_path>`/`find <scan_root()>` is
    # not reported as a shared-tempdir scan (cycle-4 review).
    '"find", HOST_TMPDIR',
    '"find", tempfile.gettempdir()',
    '"find", os.path.realpath(tempfile.gettempdir())',
    "f\"find {HOST_TMPDIR",
    "f\"find {tempfile.gettempdir()",
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
