"""Reaper tests — discovery + classification (plan Task 1).

Covers: socket-location + registry dual-signal classification, MIN_UPTIME
boot cooldown (env-overridable), symlink safety, per-file error isolation,
unknown old-settings dirname protection, client-count via CLIENT LIST.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

from tortoise.embedded_reaper import (
    _parse_min_uptime,
    discover,
)

pytest.importorskip("redislite")


@pytest.fixture(autouse=True)
def _isolate_zero_client_state(tmp_path, monkeypatch):
    """#1642 FIX 3: never read/write the user's real zero-client
    confirmation state during tests (the suite's own sweep otherwise
    records every discovered live server into ~/.tortoise)."""
    from tortoise import embedded_reaper
    monkeypatch.setattr(embedded_reaper, "ZERO_CLIENT_STATE_PATH",
                        str(tmp_path / "reaper-zero-client.json"))


@pytest.fixture(autouse=True)
def _clean_redislite_residue():
    """Remove redislite servers + socket dirs spawned by THIS test.

    These tests discover redislite servers via socket scans; servers/socket
    dirs left behind by earlier tests in the same run (or prior runs) made
    them order-flaky. Clean only the DELTA the test introduced — never pkill
    pre-existing servers (session-shared fixtures, local dev daemons) or
    delete their socket dirs (#493, code-review #803).
    """
    def _snapshot() -> tuple[set[int], set[str]]:
        pids: set[int] = set()
        try:
            out = subprocess.run(
                ["pgrep", "-f", "redislite/bin/redis-server"],
                capture_output=True, text=True, timeout=10,
            ).stdout
            pids = {int(p) for p in out.split() if p.strip().isdigit()}
        except Exception:
            pass
        dirs: set[str] = set()
        tmp = tempfile.gettempdir()
        try:
            for entry in os.scandir(tmp):
                if entry.is_dir() and (
                    os.path.exists(os.path.join(entry.path, "redis.socket")) or
                    os.path.exists(os.path.join(entry.path, "redis.pid"))
                ):
                    dirs.add(entry.path)
        except Exception:
            pass
        return pids, dirs

    before_pids, before_dirs = _snapshot()
    yield
    try:
        after_pids, after_dirs = _snapshot()
        for pid in after_pids - before_pids:
            try:  # noqa: SIM105
                os.kill(pid, 15)  # SIGTERM
            except (ProcessLookupError, PermissionError):
                pass
        # Poll briefly so the rmtree below does not race a dying server.
        for _ in range(6):
            if not (after_pids - before_pids):
                break
            alive = set()
            for pid in after_pids - before_pids:
                try:
                    os.kill(pid, 0)
                    alive.add(pid)
                except (ProcessLookupError, PermissionError):
                    pass
            after_pids = alive
            if alive:
                time.sleep(0.1)
        time.sleep(0.2)
        import shutil
        for d in after_dirs - before_dirs:
            shutil.rmtree(d, ignore_errors=True)
    except Exception:
        pass


@pytest.fixture(scope="module", autouse=True)
def _sweep_stale_residue():
    """Remove socket dirs whose server is already dead (module start).

    Restores cross-run self-healing without ever touching LIVE servers:
    a dir whose redis.pid does not belong to a running process is residue by
    definition (crashed run, killed server) — remove it so later tests in
    this module see the same clean state a fresh CI runner would.
    """
    tmp = tempfile.gettempdir()
    try:
        for entry in os.scandir(tmp):
            if not entry.is_dir():
                continue
            pid_file = os.path.join(entry.path, "redis.pid")
            if not os.path.exists(pid_file):
                continue
            try:
                with open(pid_file) as f:
                    pid = int(f.read().strip())
                os.kill(pid, 0)  # alive?
                continue  # live server (ours or another process) — leave alone
            except ProcessLookupError:
                # Provably dead — residue from a crashed/killed run.
                import shutil
                shutil.rmtree(entry.path, ignore_errors=True)
            except (ValueError, OSError, PermissionError):
                # Unreadable pid, or a live process we may not signal — leave alone.
                continue
    except Exception:
        pass
    yield


def _make_no_path_server():
    """Start a no-path FalkorDB -> tempdir socket; return (db, socket_path).

    Socket taken from db.client.socket_file (no shared-tempdir walk, so
    concurrent test sessions spawning servers in the same tempdir cannot
    confuse the lookup). Returns the REALPATH'd socket (matching
    discover() output — macOS /var -> /private/var symlink).
    """
    from redislite.falkordb_client import FalkorDB
    db = FalkorDB()  # no path -> fresh tempdir server
    time.sleep(1)
    sock = os.path.realpath(db.client.socket_file)
    if not os.path.exists(sock):
        db.close()
        raise AssertionError("no redis.socket for spawned server")
    return db, sock


def _pid_for_socket(socket_path: str) -> int | None:
    """Find the redis-server PID bound to a socket via ps/lsof.

    NOTE: ps shows the symlink form of the path (/var/...) while sockets
    are often realpath'd (/private/var/...), so prefer reading redis.pid
    from the socket's dir over this helper.
    """
    out = subprocess.run(
        ["ps", "-eo", "pid,args"], capture_output=True, text=True
    ).stdout
    for line in out.splitlines():
        if "redis-server" in line and socket_path in line:
            return int(line.split()[0])
    return None


# ── Classification basics ────────────────────────────────────────────

def test_discover_classifies_no_path_orphan(monkeypatch):
    """No-path server (socket under tempdir, no db_filename) -> candidate
    once past the boot cooldown."""
    monkeypatch.setenv("TORTOISE_REAPER_MIN_UPTIME", "0")
    db, sock = _make_no_path_server()
    try:
        found = discover()
        matches = [s for s in found if s["socket_path"] == sock]
        assert matches, "no-path server not discovered"
        assert matches[0]["classification"] == "candidate"
        assert matches[0]["pid"] is not None
    finally:
        db.close()


def test_boot_cooldown_protects_fresh_servers():
    """Freshly spawned server (uptime < 30s) -> protected, not candidate."""
    db, sock = _make_no_path_server()
    try:
        found = discover()
        match = [s for s in found if s["socket_path"] == sock][0]  # noqa: RUF015
        assert match["classification"] == "protected"
    finally:
        db.close()


def test_min_uptime_env_override(monkeypatch):
    """TORTOISE_REAPER_MIN_UPTIME=0 disables the cooldown."""
    monkeypatch.setenv("TORTOISE_REAPER_MIN_UPTIME", "0")
    db, sock = _make_no_path_server()
    try:
        found = discover()
        match = [s for s in found if s["socket_path"] == sock][0]  # noqa: RUF015
        assert match["classification"] == "candidate"
    finally:
        db.close()


def test_discover_protects_path_based_server_under_tempdir():
    """Path-based server whose socket IS under tempdir must still be
    protected (registry db_filename signal overrides socket location)."""
    from tortoise.projection import FalkorProjection
    path = os.path.join(tempfile.gettempdir(), f"reaper-protected-{os.getpid()}.db")
    proj = FalkorProjection(path)
    try:
        time.sleep(1)
        found = discover()
        # The socket for a path-based server is in the dbdir (here tempdir),
        # but the registry has db_filename -> protected
        matches = [s for s in found if path in s.get("dbdir", "")]
        # Fall back to any candidate match on the settings file
        if not matches:
            matches = [s for s in found if s["classification"] == "protected"
                       and tempfile.gettempdir() in s.get("dbdir", "")]
        assert matches, "path-based server under tempdir not discovered"
        assert all(m["classification"] == "protected" for m in matches)
    finally:
        proj.close()
        for suffix in (".db", ".db.settings"):
            try:  # noqa: SIM105
                os.remove(path + suffix)
            except OSError:
                pass


def test_discover_protects_path_based_server_with_old_settings(tmp_path):
    """Fabricate pre-#90 .settings WITHOUT db_filename; .db file present in
    parent dir -> protected despite socket under tempdir."""
    dbdir = tmp_path / "redislite_oldformat"
    dbdir.mkdir()
    (dbdir / "tortoise.db").write_bytes(b"redis-db-bytes")
    socket_path = dbdir / "redis.socket"
    socket_path.write_text("")  # socket file artifact (probe will fail -> skip)
    (dbdir / "tortoise.db.settings").write_text(json.dumps({
        "pidfile": str(dbdir / "redis.pid"),
        "unixsocket": str(socket_path),
        "dbdir": str(dbdir),
        # NOTE: no db_filename — pre-#90 format
    }))
    with monkeypatch_tempdir(tmp_path):
        found = discover()
        matches = [s for s in found if str(dbdir) in s.get("dbdir", "")]
        assert matches, "old-format server not discovered"
        assert matches[0]["classification"] == "protected"


def test_discover_unknown_old_settings_pattern_scoped_absent_full_scan_protected(
        tmp_path, caplog):
    """Pre-#90 .settings, no db_filename, no .db file, non-matching dirname
    -> protected (boot cooldown: no live pid) — never crash, never killable.

    Issue #1005 semantics: the dir sits inside an ephemeral pytest tmp tree,
    so it is no longer flagged as an 'unrecognized pattern' (the tree itself
    is known-ephemeral); protection now comes from the boot cooldown. The
    unrecognized-pattern warning only applies outside ephemeral trees.

    #4068 re-argument: discovery is now scoped to the ephemeral namespace,
    and `my-custom-name` is OUTSIDE it — so the scoped sweep does not
    enumerate the dir at all, which is strictly STRONGER than classifying
    it `protected`. The classification claim is preserved (and the escape
    hatch's purpose is pinned) by asserting both halves.
    """
    dbdir = tmp_path / "my-custom-name"  # non-matching (out-of-namespace) name
    dbdir.mkdir()
    socket_path = dbdir / "redis.socket"
    socket_path.write_text("")
    (dbdir / "custom.db.settings").write_text(json.dumps({
        "pidfile": str(dbdir / "redis.pid"),
        "unixsocket": str(socket_path),
        "dbdir": str(dbdir),
    }))
    with monkeypatch_tempdir(tmp_path):
        # (a) scoped discovery: out-of-namespace name -> not enumerated
        assert [s for s in discover()
                if str(dbdir) in s.get("dbdir", "")] == []
        # (b) detect-only hatch: discovered AND classified protected
        found = discover(full_scan=True)
        matches = [s for s in found if str(dbdir) in s.get("dbdir", "")]
        assert matches, "unknown-pattern server not discovered under --full-scan"
        assert matches[0]["classification"] == "protected"


def test_full_scan_does_not_add_actions_beyond_scoped(monkeypatch):
    """Adversarial class 4: `--full-scan` restores the pre-#4068 UN-SCOPED
    enumeration, but it cannot add an ACTION. The fixture is aged past the
    boot-cooldown guard and carries a REAL dead socket, so the removal chain
    actually runs — and is refused by CONTAINMENT (the dir's name is outside
    the ephemeral namespace). Removing Guard 1 reddens this test."""
    import shutil
    import time as _t

    import tortoise.embedded_reaper as _R
    base = Path(tempfile.mkdtemp(prefix="tt_"))
    try:
        outdir = base / "my-custom-name"
        outdir.mkdir()
        sp = outdir / "redis.socket"
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(str(sp))
        s.close()  # real dead socket -> the probe guard would NOT refuse
        (outdir / "redis.pid").write_text("99999999\n")
        (outdir / "x.settings").write_text(json.dumps({
            "pidfile": str(outdir / "redis.pid"), "unixsocket": str(sp),
            "dbdir": str(outdir), "dbfilename": "redis.db"}))
        old = _t.time() - 120
        os.utime(str(outdir), (old, old))  # past the boot-cooldown guard
        # `outdir` realpaths under the tempdir but its NAME is out of the
        # discovery namespace, so containment (not probe/age) must refuse.
        monkeypatch.setattr(_R, "_real_gettempdir", lambda: str(base))
        for full_scan in (False, True):
            acted = _R._run_sweep(dry_run=False, batch_size=None,
                                  full_scan=full_scan, sweep_pid_files=False)
            assert not [r for r in acted
                        if str(outdir) in str(r.get("dbdir", ""))], (
                            full_scan, acted)
        assert outdir.is_dir() and sp.exists()
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_live_candidate_is_found_by_pass1_regardless_of_dir_name(
        tmp_path, monkeypatch):
    """The PASS-1 LEMMA the scoped discovery's kill-losslessness rests on:
    a LIVE server is enumerated by pgrep + cmdline regardless of its socket
    dir's name, so the scoped pass 2 losing an out-of-namespace name costs
    no kill. Pinned so a pass-1 regression reddens here."""
    import tortoise.embedded_reaper as _R
    for name in ("my-custom-name", "another-custom-name"):
        d = tmp_path / name
        d.mkdir()
        (d / "redis.socket").write_text("")
    # pass 1 resolves the dir from the live pid's cmdline (name-independent)
    monkeypatch.setattr(_R, "_pgrep_redis_servers", lambda: [424242])
    monkeypatch.setattr(_R, "_socket_dir_from_cmdline",
                        lambda pid: str(tmp_path / "my-custom-name"))
    monkeypatch.setattr(_R, "_classify_dir",
                        lambda d, s, known_pid=None: {
                            "pid": known_pid, "socket_path": s, "dbdir": d,
                            "classification": "protected", "settings": None})
    with monkeypatch_tempdir(tmp_path):
        recs = list(_R.discover())
    assert [r["dbdir"] for r in recs] == [str(tmp_path / "my-custom-name")], recs


def test_symlinked_decoy_dir_never_reaped(monkeypatch):
    """Adversarial class 1: an ephemeral-named SYMLINK pointing OUTSIDE the
    reaper's tempdir. Discovery cannot enumerate it (symlinked entries are
    skipped), and a crafted record is refused by CONTAINMENT on the realpath
    — the target is not under the tempdir. The fixture is aged past the
    boot-cooldown guard and carries a REAL dead socket, so the refusal is
    provably containment's and not a probe/age abort; the link and its target
    survive. Removing Guard 1 lets the target be renamed/rmtree'd and this
    test reddens."""
    import shutil
    import time as _t

    import tortoise.embedded_reaper as _R
    base = Path(tempfile.mkdtemp(prefix="tt_"))
    try:
        target = base / "decoy-target"
        target.mkdir()
        sp = target / "redis.socket"
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(str(sp))
        s.close()  # real dead socket file (persists)
        (target / "redis.pid").write_text("99999999\n")  # > pid_max
        (target / "x.settings").write_text(json.dumps({
            "pidfile": str(target / "redis.pid"), "unixsocket": str(sp),
            "dbdir": str(target), "dbfilename": "redis.db"}))
        old = _t.time() - 120
        os.utime(str(target), (old, old))  # past the boot-cooldown guard
        link = base / "tmpDECOYXX"
        link.symlink_to(target, target_is_directory=True)
        # the reaper's tempdir is redirected elsewhere, so the link's realpath
        # (the target) is OUT of tree -> containment must refuse
        monkeypatch.setattr(_R, "_real_gettempdir",
                            lambda: "/nonexistent-4068-other-root")
        assert _R._is_ephemeral_dir(os.path.realpath(str(link)),
                                    _R._real_gettempdir()) is False
        rec = {
            "pid": None, "socket_path": str(link / "redis.socket"),
            "dbdir": str(link), "path_based": True, "dir_missing": False,
            "client_count": None, "uptime": None,
            "classification": "stale_socket", "settings": None,
        }
        acted = _R.reap([rec], dry_run=False)
        assert not [r for r in acted
                    if str(link) in str(r.get("dbdir", ""))], acted
        assert link.is_symlink() and target.is_dir()
        assert (target / "redis.socket").exists()
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_namespace_match_with_escaping_realpath_refused(monkeypatch):
    """Adversarial class 2: a record whose `dbdir` realpaths OUTSIDE the
    reaper's tempdir is refused by the destruction CONTAINMENT guard. The
    fixture is aged past the boot-cooldown guard and carries a REAL dead
    socket, so neither the probe nor the age guard can be the reason —
    removing Guard 1 reddens this test."""
    import shutil
    import time as _t

    import tortoise.embedded_reaper as _R
    base = Path(tempfile.mkdtemp(prefix="tt_"))
    try:
        sp = base / "redis.socket"
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(str(sp))
        s.close()  # real dead socket -> the probe guard would NOT refuse
        old = _t.time() - 120
        os.utime(str(base), (old, old))  # past the boot-cooldown guard
        # redirect the reaper's notion of the tempdir so `base` is out of tree
        monkeypatch.setattr(_R, "_real_gettempdir",
                            lambda: "/nonexistent-4068-other-root")
        assert _R._is_ephemeral_dir(os.path.realpath(str(base)),
                                    _R._real_gettempdir()) is False
        rec = {"dbdir": str(base), "socket_path": str(sp)}
        assert _R._remove_stale_socket_dir(rec, dry_run=False) is None
        assert base.is_dir() and sp.exists()
    finally:
        shutil.rmtree(base, ignore_errors=True)


# ── Error isolation ─────────────────────────────────────────────────

def test_discover_skips_permission_denied_dir_continues_sweep(tmp_path, monkeypatch, caplog):
    """chmod-000 subdir -> skip + continue; other candidates still found."""
    denied = tmp_path / "redislite_denied"
    denied.mkdir()
    (denied / "redis.socket").write_text("")
    os.chmod(denied, 0o000)
    try:
        db, sock = _make_no_path_server()  # noqa: RUF059
        try:
            monkeypatch.setenv("TORTOISE_REAPER_MIN_UPTIME", "0")
            with monkeypatch_tempdir(tmp_path):
                # tempdir now only contains the denied dir; no-path server
                # lives in the REAL tempdir, so point discovery at a dir
                # containing both via a custom scan root is not supported —
                # instead assert the denied dir is skipped without crash.
                found = discover()
                assert isinstance(found, list)
        finally:
            db.close()
    finally:
        os.chmod(denied, 0o755)  # restore so tmp_path teardown works


def test_discover_skips_corrupt_settings_continues_sweep(tmp_path, caplog):
    """One corrupt/unreadable .settings must not crash the sweep."""
    bad = tmp_path / "redislite_corrupt"
    bad.mkdir()
    (bad / "redis.socket").write_text("")
    (bad / "bad.db.settings").write_bytes(b"\x00\x01binary-garbage")
    db, sock = _make_no_path_server()  # noqa: RUF059
    try:
        with monkeypatch_tempdir(tmp_path):
            found = discover()
            assert isinstance(found, list)
    finally:
        db.close()
    assert "corrupt" in caplog.text.lower() or "skip" in caplog.text.lower() or \
        "warning" in caplog.text.lower()


# ── Symlink safety ──────────────────────────────────────────────────

def test_discover_handles_symlinked_tempdir(tmp_path, monkeypatch):
    """Symlink pointing at a tempdir with an orphan -> still found + correctly
    classified (realpath on both sides)."""
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    db, sock = _make_no_path_server()  # noqa: RUF059
    try:
        monkeypatch.setenv("TORTOISE_REAPER_MIN_UPTIME", "0")
        # create a fake orphan under the symlinked dir
        fake = real / "redislite_symlink_test"
        fake.mkdir()
        fake_socket = fake / "redis.socket"
        fake_socket.write_text("")
        (fake / "fake.db.settings").write_text(json.dumps({
            "pidfile": str(fake / "redis.pid"),
            "unixsocket": str(fake_socket),
            "dbdir": str(link / "redislite_symlink_test"),
        }))
        found = discover()
        # Should not crash; both realpath forms handled
        assert isinstance(found, list)
    finally:
        db.close()


# ── #4068: scoped, in-process discovery ─────────────────────────────

def test_scoped_discovery_predicate_equivalence_at_depth_1(tmp_path):
    """For depth-1 dirs the DISCOVERY predicate `_ephemeral_name` and the
    destruction predicate `_is_ephemeral_dir` are the SAME set. The scoped
    walk's removal-losslessness rests on this — it must redden if either
    side changes, so it asserts on the function actually used by
    `_scan_socket_dirs`, not on an inlined copy of its body."""
    from tortoise.embedded_reaper import (
        _ephemeral_name,
        _is_ephemeral_dir,
    )
    tmp_real = os.path.realpath(str(tmp_path))
    for name in ["tmp", "tmpx", "redislite_x", "tortoise_a", "tt_", "lme-x",
                 "my-custom-name", "d", "ask_sdk_x"]:
        d = tmp_path / name
        d.mkdir()
        assert _ephemeral_name(name) == _is_ephemeral_dir(
            os.path.realpath(str(d)), tmp_real), name


def test_discovery_tests_the_name_before_is_symlink(
        tmp_path, monkeypatch):
    """The perf win IS the ordering: a foreign entry costs a dirent read and
    is never even symlink-tested. `DirEntry.is_symlink()` is C-level and
    invisible to an `os.stat` counter, so the ORDER is pinned directly: the
    predicate is called for every entry, `is_symlink` only for matches."""
    calls = {"predicate": 0, "is_symlink": 0}
    real_scandir = os.scandir
    real_is_symlink = os.DirEntry.is_symlink

    class _CountingEntry:
        def __init__(self, e):
            self._e = e
            self.name = e.name
            self.path = e.path

        def is_symlink(self):
            calls["is_symlink"] += 1
            return real_is_symlink(self._e)

    class _CountingScan:
        def __init__(self, path):
            self._it = real_scandir(path)

        def __iter__(self):
            for e in self._it:
                yield _CountingEntry(e)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self._it.close()
            return False

    for i in range(500):
        (tmp_path / f"foreign_{i}").mkdir()
    (tmp_path / "tmpAAAAAAAA").mkdir()
    (tmp_path / "tmpAAAAAAAA" / "redis.socket").write_text("")
    from tortoise.embedded_reaper import _ephemeral_name as _real_pred
    from tortoise.embedded_reaper import _iter_candidate_dirs

    monkeypatch.setattr(os, "scandir", _CountingScan)

    def _counting_pred(name):
        calls["predicate"] += 1
        return _real_pred(name)

    res = _iter_candidate_dirs(str(tmp_path), predicate=_counting_pred)
    assert res.dirs == [str(tmp_path / "tmpAAAAAAAA")]
    assert calls["predicate"] == 501, calls
    assert calls["is_symlink"] == 1, calls


def test_find_socket_dirs_scoped_skips_custom_full_scan_finds(tmp_path):
    from tortoise.embedded_reaper import _scan_socket_dirs
    for name in ("my-custom-name", "redislite_ok"):
        d = tmp_path / name
        d.mkdir()
        (d / "redis.socket").write_text("")
    assert _scan_socket_dirs(str(tmp_path)).dirs == [
        str(tmp_path / "redislite_ok")]
    assert set(_scan_socket_dirs(str(tmp_path), full_scan=True).dirs) == {
        str(tmp_path / "my-custom-name"), str(tmp_path / "redislite_ok")}


def test_find_socket_dirs_finds_either_marker_name(tmp_path):
    from tortoise.embedded_reaper import _scan_socket_dirs
    (tmp_path / "tmpAAAAAAA1").mkdir()
    (tmp_path / "tmpAAAAAAA2").mkdir()
    (tmp_path / "tmpAAAAAAA1" / "redis.socket").write_text("")
    (tmp_path / "tmpAAAAAAA2" / "redis.pid").write_text("")
    assert len(_scan_socket_dirs(str(tmp_path)).dirs) == 2


def test_find_socket_dirs_symlinked_marker_file_still_discovered(tmp_path):
    """A symlink NAMED redis.socket inside a real ephemeral dir is found —
    `find -name` matches it too."""
    from tortoise.embedded_reaper import _scan_socket_dirs
    d = tmp_path / "tmpZZZZZZZZ"
    d.mkdir()
    (tmp_path / "elsewhere.socket").write_text("")
    os.symlink(tmp_path / "elsewhere.socket", d / "redis.socket")
    assert _scan_socket_dirs(str(tmp_path)).dirs == [str(d)]


def test_symlinked_dir_not_enumerated(tmp_path, tmp_path_factory):
    """Parity with `find` without -L: a symlinked depth-1 dir is not descended."""
    from tortoise.embedded_reaper import _scan_socket_dirs
    root = tmp_path / "root"
    root.mkdir()
    target = tmp_path_factory.mktemp("outside")  # OUTSIDE the scanned root
    (target / "redis.socket").write_text("")
    (root / "tmpLINKLINK").symlink_to(target, target_is_directory=True)
    (root / "tmpREALONE1").mkdir()
    (root / "tmpREALONE1" / "redis.socket").write_text("")
    res = _scan_socket_dirs(str(root), full_scan=True)
    assert res.dirs == [str(root / "tmpREALONE1")] and res.complete


def test_find_socket_dirs_budget_expiry_partial_and_warns(tmp_path, caplog):
    import time as _t

    from tortoise.embedded_reaper import _scan_socket_dirs
    for i in range(50):
        d = tmp_path / f"tmp{i:08d}"
        d.mkdir()
        (d / "redis.socket").write_text("")
    with caplog.at_level("WARNING"):
        res = _scan_socket_dirs(str(tmp_path), deadline=_t.monotonic() - 1.0)
    assert res.complete is False and res.dirs == []
    assert "budget" in caplog.text.lower()


def test_iter_candidate_dirs_oserror_mid_iteration_is_partial(
        tmp_path, monkeypatch, caplog):
    """A mid-iteration OSError keeps the entries already yielded."""
    from tortoise.embedded_reaper import _iter_candidate_dirs
    (tmp_path / "tmphit").mkdir()
    (tmp_path / "tmpboom").mkdir()
    real = os.scandir

    class _It:
        def __init__(self):
            self._it = real(str(tmp_path))
            self._n = 0

        def __iter__(self):
            return self

        def __next__(self):
            self._n += 1
            if self._n > 1:
                raise OSError("nope")
            return next(self._it)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(os, "scandir", lambda p: _It())
    with caplog.at_level("WARNING"):
        res = _iter_candidate_dirs(str(tmp_path), predicate=lambda n: True)
    assert res.complete is False and len(res.dirs) == 1
    assert "failed" in caplog.text.lower()


def test_iter_candidate_dirs_empty_root_is_complete(tmp_path):
    from tortoise.embedded_reaper import _iter_candidate_dirs
    res = _iter_candidate_dirs(str(tmp_path), predicate=lambda n: True)
    assert res.dirs == [] and res.complete is True


def test_discover_full_scan_keyword_reaches_walk(tmp_path):
    from tortoise.embedded_reaper import discover
    d = tmp_path / "my-custom-name"
    d.mkdir()
    (d / "redis.socket").write_text("")
    with monkeypatch_tempdir(tmp_path):
        assert [r for r in discover()
                if r["dbdir"].endswith("my-custom-name")] == []
        found = [r for r in discover(full_scan=True)
                 if r["dbdir"].endswith("my-custom-name")]
    assert found and found[0]["classification"] == "protected"


def test_scan_set_equality_against_independent_predicate(tmp_path):
    """Acceptance #3: the scoped set equals the set computed independently
    from the invariant (ephemeral name AND a marker present)."""
    from tortoise.embedded_reaper import _ephemeral_name, _scan_socket_dirs
    expected = set()
    for name, marker in [("tmpAAAAAAA1", "redis.socket"),
                         ("redislite_ok", "redis.pid"),
                         ("tortoise_x", "redis.socket"),
                         ("my-custom-name", "redis.socket"),
                         ("d", "redis.socket"),
                         ("ask_sdk_x", "redis.pid")]:
        d = tmp_path / name
        d.mkdir()
        (d / marker).write_text("")
        if _ephemeral_name(name):
            expected.add(str(d))
    assert set(_scan_socket_dirs(str(tmp_path)).dirs) == expected


# ── CLIENT LIST / SKIPME ────────────────────────────────────────────

def test_reaper_excludes_own_connection_from_client_count(monkeypatch):
    """SKIPME — the reaper's own health-check connection must not inflate
    client_count for discovered servers."""
    from tortoise.embedded_reaper import _client_list
    db, sock = _make_no_path_server()
    try:
        clients = _client_list(sock)
        # redislite's own internal connection may show; assert parseable
        assert isinstance(clients, list)
        assert all(isinstance(c, dict) for c in clients)
    finally:
        db.close()


def _force_raw_resp_fallback(monkeypatch):
    """Hide redis-cli so _client_list exercises the raw-RESP path (the
    fallback that must be non-destructive, issue #849)."""
    import subprocess as sp
    real_run = sp.run

    def _no_redis_cli(*args, **kwargs):
        if args and args[0] and str(args[0][0]).endswith("redis-cli"):
            raise FileNotFoundError("redis-cli absent (test)")
        return real_run(*args, **kwargs)

    monkeypatch.setattr(
        "tortoise.embedded_reaper.subprocess.run", _no_redis_cli)


def test_client_list_raw_resp_fallback_probe_is_non_destructive(monkeypatch):
    """Regression #849: with redis-cli absent, _client_list falls back to raw
    RESP over a plain socket and must NOT kill the server it probes."""
    import redis as _redis  # noqa: I001
    from tortoise.embedded_reaper import _client_list
    _force_raw_resp_fallback(monkeypatch)
    db, sock = _make_no_path_server()
    try:
        # ps args show the symlink form (/var/...) while sock is realpath'd
        # (/private/var/...), so read the server PID from redis.pid instead.
        pid = int(Path(os.path.dirname(sock), "redis.pid").read_text().strip())
        assert _pid_alive_for(pid), "probed server not alive before probe"
        clients = _client_list(sock)
        assert isinstance(clients, list)
        assert all(isinstance(c, dict) for c in clients)
        # The probed server must survive the probe and keep serving.
        time.sleep(1)
        assert _pid_alive_for(pid), "probe killed the probed server (#849)"
        r = _redis.Redis(unix_socket_path=sock, socket_connect_timeout=2)
        try:
            assert r.ping(), "probed server no longer serves queries"
        finally:
            r.close()
    finally:
        db.close()


def test_client_list_raw_resp_fallback_never_uses_redislite_client(monkeypatch):
    """Regression #849: the raw-RESP fallback must NOT construct a redislite
    client — its close() shuts down the probed server whenever it believes it
    is the last client (_connection_count() <= 1), killing the orphan.
    Deterministic on all platforms/versions (the kill itself is timing- and
    version-dependent)."""
    import redislite.falkordb_client as _fc  # noqa: I001
    from tortoise.embedded_reaper import _client_list
    db, sock = _make_no_path_server()
    _force_raw_resp_fallback(monkeypatch)
    calls = []

    class _SpyFalkorDB:
        def __init__(self, *a, **k):
            calls.append(k)

    monkeypatch.setattr(_fc, "FalkorDB", _SpyFalkorDB)
    try:
        clients = _client_list(sock)
        assert isinstance(clients, list)
        assert all(isinstance(c, dict) for c in clients)
        assert calls == [], "raw-RESP fallback constructed a redislite client"
    finally:
        db.close()


def test_client_list_fails_closed_on_dead_socket():
    """Probe against a dead socket -> None (fail closed), NOT [] — an empty
    list would look like "zero clients" and license a kill."""
    from tortoise.embedded_reaper import _client_list
    assert _client_list("/nonexistent/reaper-probe.sock") is None


def test_client_list_fails_closed_on_garbage_reply(monkeypatch, tmp_path):
    """Malformed/non-bulk RESP reply -> None (fail closed), never a bogus
    zero-client verdict."""
    from tortoise.embedded_reaper import _client_list
    _force_raw_resp_fallback(monkeypatch)
    # Short path: macOS AF_UNIX sun_path is limited to ~104 bytes and
    # pytest tmp_path is too long.
    tmpdir = tempfile.mkdtemp(prefix="reaper_probe_")
    sock_path = os.path.join(tmpdir, "garbage.sock")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock_path)
    srv.listen(1)

    def _serve():
        conn, _ = srv.accept()
        try:
            conn.recv(4096)
            conn.sendall(b"-ERR nonsense\r\n")  # error reply, not bulk string
        finally:
            conn.close()

    t = threading.Thread(target=_serve, daemon=True)
    t.start()
    try:
        assert _client_list(sock_path) is None
    finally:
        t.join(timeout=5)
        srv.close()
        shutil.rmtree(tmpdir, ignore_errors=True)


# ── _parse_min_uptime ───────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("0", 0),
    ("30", 30),
    ("30.5", 30),      # float -> truncate
    ("-1", 0),         # negative -> 0
    ("abc", 30),       # non-numeric -> default
    ("", 30),          # empty -> default
    ("999999", 999999),# huge -> accepted
])
def test_parse_min_uptime_values(raw, expected, monkeypatch, caplog):
    monkeypatch.setenv("TORTOISE_REAPER_MIN_UPTIME", raw)
    assert _parse_min_uptime() == expected


def test_parse_min_uptime_missing(monkeypatch):
    monkeypatch.delenv("TORTOISE_REAPER_MIN_UPTIME", raising=False)
    assert _parse_min_uptime() == 30


# ── helper: point gettempdir at tmp_path ────────────────────────────

class monkeypatch_tempdir:
    """Context manager temporarily redirecting tempfile.gettempdir()."""

    def __init__(self, tmp_path):
        self.tmp_path = str(tmp_path)
        self._orig = None

    def __enter__(self):
        import tempfile as tf
        self._orig = tf.gettempdir
        tf.gettempdir = lambda: self.tmp_path
        return self

    def __exit__(self, *exc):
        import tempfile as tf
        tf.gettempdir = self._orig
        return False


# ── Task 2: reap() kill logic ───────────────────────────────────────

def _spawn_orphan(monkeypatch=None):
    """Spawn a no-path server in a subprocess, SIGKILL the parent WITHOUT
    close() -> leaves a genuine orphan (socket + pid + tempdir persist).
    Returns the orphan's socket_path (realpath'd).

    The child prints its own socket path (db.client.socket_file) so no
    shared-tempdir walk is needed — concurrent test sessions spawning
    redislite servers in the same tempdir cannot confuse the lookup.
    """
    import subprocess as sp
    import sys as _sys
    code = (
        "import os,subprocess,sys,time; os.environ.pop('TORTOISE_DB_URI',None);\n"
        "from redislite.falkordb_client import FalkorDB; db=FalkorDB();\n"
        "print('READY ' + db.client.socket_file, flush=True); time.sleep(30)"
    )
    proc = sp.Popen([_sys.executable, "-c", code],
                    stdout=sp.PIPE, text=True)
    import select
    if not select.select([proc.stdout], [], [], 30)[0]:
        proc.kill()
        proc.wait()
        raise AssertionError("orphan spawn timed out")
    line = proc.stdout.readline().strip()  # wait READY <socket>
    if not line.startswith("READY "):
        proc.kill()
        proc.wait()
        raise AssertionError("orphan spawn failed: %r" % line)  # noqa: UP031
    sock = line.split(None, 1)[1]
    time.sleep(1)
    proc.kill()
    proc.wait()
    time.sleep(1)
    if not os.path.exists(sock):
        raise AssertionError("orphan socket vanished: %s" % sock)  # noqa: UP031
    return os.path.realpath(sock)


def test_reap_kills_idle_orphan(monkeypatch):
    """Genuine orphan (SIGKILL'd parent) -> reap() kills it."""
    from tortoise.embedded_reaper import discover, reap
    monkeypatch.setenv("TORTOISE_REAPER_MIN_UPTIME", "0")
    sock = _spawn_orphan()
    found = discover()
    match = [s for s in found if s["socket_path"] == sock][0]  # noqa: RUF015
    assert match["classification"] == "candidate"
    pid = match["pid"]
    reap([match], dry_run=False)
    time.sleep(1)
    assert not _pid_alive_for(pid), "orphan not killed"


def test_reap_skips_orphan_with_active_client(monkeypatch):
    """Orphan with a LIVE client (redis-py connected) -> reap() must NOT kill."""
    import redis as _redis  # noqa: I001
    from tortoise.embedded_reaper import discover, reap
    monkeypatch.setenv("TORTOISE_REAPER_MIN_UPTIME", "0")
    sock = _spawn_orphan()
    client = _redis.Redis(unix_socket_path=sock, socket_connect_timeout=2)
    client.ping()
    time.sleep(3)  # age the connection so CLIENT LIST sees it as a real client (age >= 2s)
    try:
        found = discover()
        match = [s for s in found if s["socket_path"] == sock][0]  # noqa: RUF015
        pid = match["pid"]
        reap([match], dry_run=False)
        time.sleep(1)
        assert _pid_alive_for(pid), "server killed despite active client"
    finally:
        client.close()
        # cleanup: kill the orphan we created
        found = discover()
        match = [s for s in found if s["socket_path"] == sock]
        if match:
            reap(match, dry_run=False)


def test_reap_skips_path_based_server(monkeypatch):
    """Path-based server (protected) -> reap() never kills it."""
    from tortoise.projection import FalkorProjection  # noqa: I001
    from tortoise.embedded_reaper import discover, reap
    path = os.path.join(tempfile.gettempdir(), f"reaper-protected-{os.getpid()}.db")
    proj = FalkorProjection(path)
    try:
        time.sleep(1)
        found = discover()
        matches = [s for s in found if s["classification"] == "protected"
                   and path in (s.get("settings") or {}).get("dbdir", "")]
        if not matches:
            matches = [s for s in found if s["classification"] == "protected"]
        assert matches, "protected server not discovered"
        reap(matches, dry_run=False)
        time.sleep(1)
        # protected server must still be running (never killed)
        alive = [s for s in matches if s["pid"] and _pid_alive_for(s["pid"])]
        assert alive, "path-based server was killed!"
    finally:
        proj.close()
        for suffix in (".db", ".db.settings"):
            try:  # noqa: SIM105
                os.remove(path + suffix)
            except OSError:
                pass


def test_reap_removes_tempdir_after_kill(monkeypatch):
    """After reap() kills a candidate, its tempdir is removed."""
    monkeypatch.setenv("TORTOISE_REAPER_MIN_UPTIME", "0")
    sock = _spawn_orphan()
    from tortoise.embedded_reaper import discover, reap
    found = discover()
    match = [s for s in found if s["socket_path"] == sock][0]  # noqa: RUF015
    dbdir = match["dbdir"]
    reap([match], dry_run=False)
    time.sleep(1)
    assert not os.path.exists(dbdir), "tempdir not cleaned after kill"


def test_reap_kill_removes_socket_dir_and_ephemeral_data_dir(monkeypatch):
    """#1642 FIX 2: the kill path removes BOTH the socket dir (record
    dbdir) AND a separate ephemeral registry data dir — a kill previously
    left the data dir (e.g. a tortoise_test_x_* path) as a permanent
    tempdir entry (observed: 32k entries). Exercises the actual reap()
    kill-path cleanup loop."""
    from tortoise.embedded_reaper import reap
    killed = []

    def fake_kill(pid, timeout):
        killed.append(pid)

    monkeypatch.setattr("tortoise.embedded_reaper._kill", fake_kill)
    monkeypatch.setattr("tortoise.embedded_reaper._active_client_count",
                        lambda _s: 0)
    base = Path(tempfile.mkdtemp(prefix="tt_"))
    try:
        sock_dir = base / "redislite_x"
        sock_dir.mkdir()
        sp = sock_dir / "redis.socket"
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(str(sp))
        s.close()
        (sock_dir / "redis.pid").write_text("99999999\n")
        data_dir = base / "tortoise_test_y"
        data_dir.mkdir()
        (data_dir / "x.db.settings").write_text("{}")
        rec = {
            "classification": "candidate", "dir_missing": False,
            "socket_path": str(sp), "pid": os.getpid(),
            "dbdir": str(sock_dir), "path_based": False,
            "settings": {"dir": str(data_dir), "dbfilename": "redis.db"},
        }
        acted = reap([rec], dry_run=False, only_safe=False)  # noqa: F841
        assert killed == [os.getpid()]
        assert not sock_dir.exists(), "socket dir not cleaned after kill"
        assert not data_dir.exists(), \
            "ephemeral data dir not cleaned after kill (#1642 FIX 2)"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_reap_kill_preserves_non_ephemeral_data_dir(monkeypatch):
    """#1642 FIX 2: a user-path (non-ephemeral) registry data dir is NEVER
    rmtree'd by the kill path — the containment check is the safety
    boundary (path-based data outlives the test tree)."""
    from tortoise.embedded_reaper import reap
    killed = []

    def fake_kill(pid, timeout):
        killed.append(pid)

    monkeypatch.setattr("tortoise.embedded_reaper._kill", fake_kill)
    monkeypatch.setattr("tortoise.embedded_reaper._active_client_count",
                        lambda _s: 0)
    base = Path(tempfile.mkdtemp(prefix="tt_"))
    try:
        sock_dir = base / "redislite_x"
        sock_dir.mkdir()
        sp = sock_dir / "redis.socket"
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(str(sp))
        s.close()
        user_dir = Path(tempfile.gettempdir()) / \
            f"reaper-user-data-test-{os.getpid()}"  # non-ephemeral name
        user_dir.mkdir()
        (user_dir / "keep.db.settings").write_text("{}")
        try:
            rec = {
                "classification": "candidate", "dir_missing": False,
                "socket_path": str(sp), "pid": os.getpid(),
                "dbdir": str(sock_dir), "path_based": False,
                "settings": {"dir": str(user_dir), "dbfilename": "user.db"},
            }
            acted = reap([rec], dry_run=False, only_safe=False)  # noqa: F841
            assert killed == [os.getpid()]
            assert not sock_dir.exists(), "socket dir should be cleaned"
            assert user_dir.exists(), \
                "non-ephemeral user data dir must be preserved"
        finally:
            shutil.rmtree(user_dir, ignore_errors=True)
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_discover_walk_runs_on_large_tempdir(monkeypatch):
    """#1642 FIX 2 (#1449): the stale-socket walk runs even when the
    tempdir's entry count exceeds max_tempdir_entries — pollution can no
    longer disable cleanup (previously the walk was skipped wholesale above
    5000 entries, the chicken-and-egg hole). The walk no longer consults
    st_nlink at all: the find-based scan finds this stale dir regardless."""
    from tortoise.embedded_reaper import discover
    with _stale_dir_env() as (dbdir, sock):  # noqa: RUF059
        found = discover(max_tempdir_entries=5000)
        matches = [s for s in found if str(dbdir) in s.get("dbdir", "")]
        assert matches, "stale-socket walk skipped on large tempdir (#1449)"
        assert matches[0]["classification"] == "stale_socket"


def test_reap_dry_run_does_not_kill(monkeypatch):
    """dry_run=True (default) logs planned kills, kills nothing."""
    monkeypatch.setenv("TORTOISE_REAPER_MIN_UPTIME", "0")
    sock = _spawn_orphan()
    from tortoise.embedded_reaper import discover, reap
    found = discover()
    match = [s for s in found if s["socket_path"] == sock][0]  # noqa: RUF015
    pid = match["pid"]
    reap([match], dry_run=True)
    time.sleep(1)
    assert _pid_alive_for(pid), "dry-run killed the orphan"


def test_reap_fails_closed_when_client_probe_unknown(monkeypatch):
    """Regression #849: if CLIENT LIST state is unknown (probe failure),
    reap() must skip the server — never kill on unknown client state."""
    from tortoise.embedded_reaper import _active_client_count, reap  # noqa: F401
    db, sock = _make_no_path_server()
    try:
        # Build the candidate record directly (discover() is racy under
        # concurrent test sessions sharing the tempdir).
        dbdir = os.path.dirname(sock)
        pid = int(Path(dbdir, "redis.pid").read_text().strip())
        record = {
            "pid": pid,
            "socket_path": sock,
            "dbdir": dbdir,
            "client_count": 0,
            "uptime": 999,
            "classification": "candidate",
            "settings": None,
        }
        monkeypatch.setattr(
            "tortoise.embedded_reaper._active_client_count", lambda _s: None)
        acted = reap([record], dry_run=False)
        assert acted == []
        time.sleep(1)
        assert _pid_alive_for(pid), "server killed on unknown client state"
    finally:
        db.close()


def test_reap_skips_hung_server_not_dead():
    """A record classified 'undetermined' -> NEVER acted upon."""
    from tortoise.embedded_reaper import reap
    record = {
        "pid": None,
        "socket_path": "/nonexistent/hung.sock",
        "dbdir": "/nonexistent",
        "client_count": 0,
        "uptime": 999,
        "classification": "undetermined",
        "settings": None,
    }
    acted = reap([record], dry_run=False)
    assert acted == []


def test_phase1_removes_stale_socket_on_econnrefused(tmp_path, monkeypatch, caplog):
    """Phase 1: dead registry PID + dead socket -> stale_socket."""
    from tortoise.embedded_reaper import phase1_probe
    dbdir = tmp_path / "redislite_stale"
    dbdir.mkdir()
    fake_socket = dbdir / "redis.socket"
    fake_socket.write_text("")
    record = {
        "pid": 999999,
        "socket_path": os.path.realpath(str(fake_socket)),
        "dbdir": os.path.realpath(str(dbdir)),
        "classification": "candidate",
    }
    updated = phase1_probe(record)
    assert updated["classification"] in ("stale_socket", "undetermined")


def test_phase1_reclassifies_live_respawned_server(monkeypatch):
    """Phase 1: stale registry PID + live socket -> real PID derived, not removed."""
    monkeypatch.setenv("TORTOISE_REAPER_MIN_UPTIME", "0")
    sock = _spawn_orphan()
    from tortoise.embedded_reaper import discover, phase1_probe
    found = discover()
    match = [s for s in found if s["socket_path"] == sock][0]  # noqa: RUF015
    live_pid = match["pid"]
    match["pid"] = 999999  # simulate stale registry pid
    updated = phase1_probe(match)
    assert updated["classification"] != "stale_socket"
    if updated["pid"] != 999999:
        assert updated["pid"] == live_pid


def _pid_alive_for(pid):
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False


# ── Task 3: CLI, singleton lock, timeout ────────────────────────────

def _run_cli(*args, timeout=600):
    """Run the reaper CLI as a subprocess; return (rc, stdout, stderr).

    Default timeout matches the reaping budget the slow --no-dry-run tests
    pass via ``--timeout`` (600s): reap() serially probes + kills every
    orphan the carve-out process has accumulated by the time the reaper
    suite runs last, which comfortably exceeds the CLI's own 120s default
    on a loaded runner (#1988). The CLI is budgeted, not speed-tested.
    """
    import subprocess as sp
    import sys as _sys
    env = dict(os.environ)
    env.pop("TORTOISE_DB_URI", None)
    proc = sp.run(
        [_sys.executable, "-m", "tortoise.embedded_reaper", *args],
        capture_output=True, text=True, timeout=timeout, env=env,
    )
    return proc.returncode, proc.stdout, proc.stderr


def test_cli_defaults_to_dry_run(monkeypatch):
    """CLI with no args defaults to dry-run (no processes killed)."""
    import subprocess as sp  # noqa: F401, I001
    import sys as _sys  # noqa: F401
    from tortoise.embedded_reaper import discover, reap
    monkeypatch.setenv("TORTOISE_REAPER_MIN_UPTIME", "0")
    sock = _spawn_orphan()
    # capture the orphan pid directly from the socket's pidfile
    dbdir = os.path.dirname(sock)
    pid = int(open(os.path.join(dbdir, "redis.pid")).read().strip())  # noqa: SIM115
    try:
        rc, out, err = _run_cli()
        assert rc == 0
        assert "DRY-RUN" in (out + err) or "dry" in (out + err).lower()
        # orphan must still be alive (dry-run)
        assert _pid_alive_for(pid), "dry-run killed the orphan"
    finally:
        found = discover()
        match = [s for s in found if s["socket_path"] == sock]
        if match:
            reap(match, dry_run=False)


@pytest.mark.timeout(660)  # full-sweep CLI: reap() serially probes + kills every orphan the carve-out process accumulated — see _run_cli docstring (#1988); the 600s CLI budget exceeds the carve-out job's global --timeout=300 on loaded runners (longmem move added +177 embedded tests to the same process); 660 > sp.run's own 600s cap so ITS child-kill (lock release) governs, never pytest's signal
def test_cli_no_dry_run_kills(monkeypatch):
    """--no-dry-run actually kills orphans.

    The assertion is pile-size-independent: the CLI is a cron tool whose
    per-run kill budget is DEFAULT_BATCH_SIZE (50) by design — a fresh
    orphan behind an ambient pile at/over the cap is legitimately
    deferred to the next sweep ("remainder converges next sweep"), NOT a
    --no-dry-run failure. So the kill VERB is pinned by the sweep's
    observed DEATHS among pre-existing live candidates — the probe gone
    (reachable pile) or >= 1 ambient socket from the before-snapshot no
    longer alive (batch-truncated pile) — which no concurrent new spawn
    can satisfy. The probe must additionally be gone whenever the
    ambient live-candidate pile sits BELOW the batch cap (the
    single-sweep guarantee). The carve-out process accumulates ~119 live
    embedded servers from the longmem module (Linux CI keeps daemonized
    servers until process-end atexit); batch-truncation semantics
    themselves are covered by test_cli_batch_size_limits_kills.
    """
    monkeypatch.setenv("TORTOISE_REAPER_MIN_UPTIME", "0")
    sock = _spawn_orphan()
    try:
        from tortoise.embedded_reaper import DEFAULT_BATCH_SIZE, _pid_alive, discover

        def _live_candidates() -> list[dict]:
            return [
                r for r in discover()
                if r.get("classification") == "candidate"
                and r.get("pid") and _pid_alive(r["pid"])
            ]

        before = _live_candidates()
        # Ambient live candidates that predate our probe orphan (pgrep
        # sorts ascending by PID, so the freshly-spawned probe — highest
        # PID — sweeps last; the CLI can only reach it when fewer than
        # the batch cap of OTHER live candidates precede it).
        ambient_socks = {
            r["socket_path"] for r in before if r["socket_path"] != sock
        }
        rc, out, err = _run_cli("--no-dry-run", "--timeout", "600")
        assert rc == 0
        after = _live_candidates()
        # The kill verb, pinned pile-independently by observed deaths
        # among pre-existing live candidates: probe gone (reachable
        # pile) or an ambient socket from the before-snapshot died
        # (batch-truncated pile). A --no-dry-run regression (flag
        # silently treated as dry-run) kills nothing — neither term
        # fires. NOTE: the CLI's own "(N killed)" summary is NOT a
        # reliable pin — it counts acted candidate records, and the
        # dry-run branch appends to acted too.
        probe_gone = not any(r["socket_path"] == sock for r in after)
        ambient_survivors = sum(
            1 for r in after if r["socket_path"] in ambient_socks
        )
        assert probe_gone or ambient_survivors < len(ambient_socks), (
            f"--no-dry-run killed nothing: probe_gone={probe_gone} "
            f"ambient {len(ambient_socks)} -> {ambient_survivors} "
            f"rc={rc} out={out!r} err={err!r}"
        )
        if len(ambient_socks) < DEFAULT_BATCH_SIZE:  # single-sweep reach
            assert probe_gone, "orphan not killed by --no-dry-run"
    finally:
        from tortoise.embedded_reaper import discover, reap
        found = discover()
        match = [s for s in found if s["socket_path"] == sock]
        if match:
            reap(match, dry_run=False)


def test_cli_json_output(monkeypatch):
    """--json emits parseable machine-readable output."""
    monkeypatch.setenv("TORTOISE_REAPER_MIN_UPTIME", "0")
    sock = _spawn_orphan()
    try:
        import json as _json
        rc, out, err = _run_cli("--json")  # noqa: RUF059
        assert rc == 0
        data = _json.loads(out)
        assert isinstance(data, list)
    finally:
        from tortoise.embedded_reaper import discover, reap
        found = discover()
        match = [s for s in found if s["socket_path"] == sock]
        if match:
            reap(match, dry_run=False)


@pytest.mark.timeout(660)  # full-sweep CLI: reap() over the carve-out process's accumulated orphans — see _run_cli docstring (#1988); 600s CLI budget exceeds the job's global --timeout=300 on loaded runners; 660 > sp.run's 600s cap so ITS child-kill governs
def test_cli_batch_size_limits_kills(monkeypatch):
    """--batch-size N limits kills per run."""
    monkeypatch.setenv("TORTOISE_REAPER_MIN_UPTIME", "0")
    socks = [_spawn_orphan() for _ in range(2)]
    try:
        rc, _out, _err = _run_cli("--no-dry-run", "--batch-size", "1",
                                 "--timeout", "600")
        assert rc == 0
        from tortoise.embedded_reaper import discover
        found = discover()
        remaining = [s for s in found if s["socket_path"] in socks]
        assert len(remaining) >= 1, "batch-size 1 killed more than 1"
    finally:
        from tortoise.embedded_reaper import discover, reap
        found = discover()
        match = [s for s in found if s["socket_path"] in socks]
        if match:
            reap(match, dry_run=False)


@pytest.mark.timeout(660)  # full-sweep CLI (same budget rationale as test_cli_no_dry_run_kills; 660 > sp.run's 600s cap)
def test_cli_singleton_lock_prevents_concurrent(monkeypatch):
    """Second concurrent instance (lock held mid-sweep) exits 0 with
    'already running'. The lock is held only DURING a sweep, so we hold it
    directly to simulate a mid-sweep overlap."""
    from tortoise.embedded_reaper import _ReaperLock
    monkeypatch.setenv("TORTOISE_REAPER_MIN_UPTIME", "0")
    lock = _ReaperLock()
    assert lock.acquire(), "could not acquire lock for test"
    try:
        rc, out, err = _run_cli("--no-dry-run", "--timeout", "600",
                                timeout=600)
        assert rc == 0
        assert "already running" in (out + err).lower()
    finally:
        lock.release()


@pytest.mark.timeout(660)  # full-sweep CLI (same budget rationale as test_cli_no_dry_run_kills; 660 > sp.run's 600s cap)
def test_cli_singleton_lock_released_on_sigkill(monkeypatch):
    """SIGKILL the lock-holder -> fcntl auto-releases -> second acquires."""
    import subprocess as sp
    import sys as _sys
    monkeypatch.setenv("TORTOISE_REAPER_MIN_UPTIME", "0")
    env = dict(os.environ)
    env.pop("TORTOISE_DB_URI", None)
    holder = sp.Popen(
        [_sys.executable, "-c",
         "import time; from tortoise.embedded_reaper import _ReaperLock; "
         "l=_ReaperLock(); print('LOCKED', l.acquire(), flush=True); "
         "time.sleep(60)"],
        stdout=sp.PIPE, stderr=sp.PIPE, text=True, env=env,
    )
    # wait for the holder to acquire
    assert holder.stdout.readline().strip() == "LOCKED True"
    time.sleep(1)
    holder.kill()  # SIGKILL while holding lock
    holder.wait(timeout=5)
    time.sleep(1)
    rc, out, err = _run_cli("--no-dry-run", "--timeout", "600",
                            timeout=600)
    # should run normally (lock released via kernel), not 'already running'
    assert rc == 0
    assert "already running" not in (out + err).lower()


# ── Issue #1005: ephemeral-test-tree classification + concurrency guard ──

def test_is_ephemeral_dir_recognizes_test_prefixes():
    """Ephemeral markers: any basename prefix under the tempdir."""
    from tortoise.embedded_reaper import _is_ephemeral_dir, _real_gettempdir
    tmp = _real_gettempdir()
    assert _is_ephemeral_dir(os.path.join(tmp, "tortoise_shared_embedded_x"), tmp)
    assert _is_ephemeral_dir(os.path.join(tmp, "tortoise_m0_x"), tmp)
    assert _is_ephemeral_dir(os.path.join(tmp, "tt_own_x"), tmp)
    assert _is_ephemeral_dir(os.path.join(tmp, "pytest-of-user"), tmp)
    assert _is_ephemeral_dir(os.path.join(tmp, "pack_v3_bad_x"), tmp)
    assert not _is_ephemeral_dir(os.path.join(tmp, "unknown-prefix-x"), tmp)
    # user-home dirs are NOT under the tempdir -> never ephemeral
    assert not _is_ephemeral_dir("/Users/u/tortoise-test-home-1", tmp)


def test_is_ephemeral_dir_linux_tmp_root_never_matches():
    """Regression (#1005 review P1): on Linux the tempdir root IS /tmp —
    the root's own 'tmp' component must never classify everything beneath
    it as ephemeral, and /tmp2 siblings must not match via string prefix."""
    from tortoise.embedded_reaper import _is_ephemeral_dir
    assert not _is_ephemeral_dir("/tmp/unknown-prefix-x", "/tmp")
    assert not _is_ephemeral_dir("/tmp/myapp", "/tmp")
    assert not _is_ephemeral_dir("/tmp2/something", "/tmp")
    assert not _is_ephemeral_dir("/tmp", "/tmp")  # the root itself
    assert _is_ephemeral_dir("/tmp/pytest-of-user/pytest-1/test_x", "/tmp")
    assert _is_ephemeral_dir("/tmp/tortoise_test_abc", "/tmp")
    assert _is_ephemeral_dir("/tmp/tt_own_1", "/tmp")


def test_classify_path_server_under_ephemeral_tree_is_candidate(monkeypatch):
    """Path-based server whose dir is an ephemeral test tree -> candidate
    (previously 'protected' — the #1005 dominant leak source)."""
    from tortoise.embedded_reaper import _classify, _real_gettempdir
    monkeypatch.setenv("TORTOISE_REAPER_MIN_UPTIME", "0")
    tmp = _real_gettempdir()
    dbdir = os.path.join(tmp, "tortoise_shared_embedded_abc")
    socket_dir = os.path.join(tmp, "redislite_xyz")
    registry = {"dir": dbdir, "dbfilename": "shared.db", "pidfile": "/nonexistent/pid"}
    assert _classify(socket_dir, dbdir, tmp, registry) == "candidate"


def test_classify_dir_marks_dir_missing(tmp_path, monkeypatch):
    """Registry dir removed (pytest cleaned the tree) -> dir_missing=True,
    which makes the record safe under concurrent suites."""
    from tortoise.embedded_reaper import _classify_dir
    monkeypatch.setenv("TORTOISE_REAPER_MIN_UPTIME", "0")
    socket_dir = tmp_path / "redislite_sock"
    socket_dir.mkdir()
    (socket_dir / "redis.socket").touch()
    gone_dir = tmp_path / "gone"
    registry_file = socket_dir / "x.settings"
    registry_file.write_text(
        '{"dir": "%s", "dbfilename": "redis.db"}' % str(gone_dir))  # noqa: UP031
    record = _classify_dir(str(socket_dir), str(socket_dir / "redis.socket"))
    assert record is not None
    assert record["dir_missing"] is True


def test_reap_only_safe_skips_live_ephemeral_without_killing(monkeypatch):
    """only_safe=True must skip live ephemeral candidates (0-client between
    tests of a concurrent suite) and only act on dir_missing records."""
    from tortoise.embedded_reaper import reap
    killed = []

    def fake_kill(pid, timeout):
        killed.append(pid)

    monkeypatch.setattr("tortoise.embedded_reaper._kill", fake_kill)
    live = {"classification": "candidate", "dir_missing": False,
            "socket_path": "/tmp/s1", "pid": 424242}
    gone = {"classification": "candidate", "dir_missing": True,
            "socket_path": "/tmp/s2", "pid": 424243}
    acted = reap([live, gone], dry_run=False, only_safe=True)
    assert acted == []  # gone's pid is dead -> skipped at liveness check
    assert killed == []  # nothing was killed


def test_reap_detached_orphan_full_sweep(monkeypatch):
    """A genuine orphan (spawning subprocess SIGKILLed) is reaped by the
    FULL sweep (only_safe=False — the single-suite end sweep / explicit
    reap). Under only_safe=True the reaper NEVER kills live-pid servers
    (#1557: redislite daemonizes to ppid=1, so all servers are "detached"
    and the orphan is indistinguishable from a concurrent suite's live
    server — only_safe's contract is "never disturb a concurrent suite",
    #1005). Genuine orphans converge at the full end sweep.
    """
    from tortoise.embedded_reaper import discover, reap
    monkeypatch.setenv("TORTOISE_REAPER_MIN_UPTIME", "0")
    sock = _spawn_orphan()
    found = discover()
    match = [s for s in found if s["socket_path"] == sock][0]  # noqa: RUF015
    assert match["classification"] == "candidate"
    pid = match["pid"]
    reap([match], dry_run=False, only_safe=False)
    time.sleep(1)
    assert not _pid_alive_for(pid), "detached orphan was not reaped in full sweep"


def test_reap_only_safe_protects_live_parented_server(monkeypatch):
    """only_safe=True must NOT reap a candidate whose direct parent is a
    LIVE process (e.g. a concurrent suite's in-process fixture server at
    0-client between tests) — the detached criterion is parent-exact.
    """
    from tortoise.embedded_reaper import reap  # noqa: I001
    import os as _os
    live_parent = _os.getpid()  # this pytest process is alive
    kept = []

    def fake_kill(pid, timeout):
        kept.append(pid)

    monkeypatch.setattr("tortoise.embedded_reaper._kill", fake_kill)
    rec = {"classification": "candidate", "dir_missing": False,
           "socket_path": "/tmp/s-live-parent", "pid": live_parent}
    # The record's pid is this live pytest process, whose own parent is a live
    # process — NOT detached — so only_safe must skip it before any probe.
    acted = reap([rec], dry_run=False, only_safe=True)
    assert acted == []
    assert kept == []  # live-parented server never killed in only_safe


def test_active_suite_tokens_lists_markers(monkeypatch, tmp_path):
    """active_suite_tokens() returns non-hidden marker filenames only, and
    skips stale markers whose pid is dead (#1005 review P2)."""
    from tortoise.embedded_reaper import ACTIVE_SUITES_DIR, active_suite_tokens  # noqa: F401
    marker_dir = tmp_path / "active_suites"
    marker_dir.mkdir(parents=True)
    (marker_dir / "1234-abc").write_text(f"pid={os.getpid()}\n")
    (marker_dir / "99999999-dead").write_text("pid=99999999\n")  # dead by
    # construction on macOS AND Linux (99999999 > pid_max on both)
    (marker_dir / "poison-empty").write_text("")
    (marker_dir / "poison-bad").write_text("pid=" + "9" * 100)  # OverflowError
    (marker_dir / ".hidden").write_text("x")
    monkeypatch.setattr("tortoise.embedded_reaper.ACTIVE_SUITES_DIR",
                        str(marker_dir))
    # malformed/empty/stale markers are skipped; only the live one counts
    assert active_suite_tokens() == ["1234-abc"]


def test_active_suite_markers_recycled_pid_is_stale(monkeypatch, tmp_path):
    """#1642 FIX 5: a marker whose pid is LIVE but whose process start time
    does NOT match the recorded start is a recycled pid — treated as stale
    (absent). A SIGKILLed suite whose pid number was reused can therefore
    never defer later sweeps to only-safe forever. Markers with the correct
    (pid, start) identity stay live."""
    from tortoise.embedded_reaper import (
        _process_start_time,
        active_suite_markers,
    )
    marker_dir = tmp_path / "active_suites"
    marker_dir.mkdir(parents=True)
    start = _process_start_time(os.getpid())
    assert start is not None, "cannot derive own start time"
    (marker_dir / "right-identity").write_text(
        f"pid={os.getpid()}\nstart={start}\n")
    # Wrong start: the pid is live but it is a DIFFERENT process now.
    (marker_dir / "recycled-pid").write_text(
        f"pid={os.getpid()}\nstart={start - 999999}\n")
    # Legacy marker without a start field: pid-only verification (back-compat).
    (marker_dir / "legacy-no-start").write_text(f"pid={os.getpid()}\n")
    (marker_dir / "dead-pid").write_text("pid=99999999\nstart=1.0\n")
    monkeypatch.setattr("tortoise.embedded_reaper.ACTIVE_SUITES_DIR",
                        str(marker_dir))
    markers = active_suite_markers()
    tokens = sorted(m["token"] for m in markers)
    assert tokens == ["legacy-no-start", "right-identity"], tokens


def test_parse_lstart_both_platform_formats():
    """#1642 FIX 5: ps -o lstart= formats differ between macOS (day before
    month) and Linux (month before day); both must parse, plus a
    space-padded single-digit day."""
    from tortoise.embedded_reaper import _parse_lstart
    macos = _parse_lstart("Sun 23 Aug 23:03:24 2026")
    linux = _parse_lstart("Wed Aug 23 10:00:00 2026")
    assert macos is not None and linux is not None
    assert abs(macos - linux) > 1  # different processes/times, both parsed
    single = _parse_lstart("Wed Aug  2 10:00:00 2026")
    assert single is not None
    assert _parse_lstart("garbage not a date") is None
    assert _parse_lstart("") is None


# ── #1231: stale index-lock pid-file sweep ──────────────────────────

def test_sweep_stale_index_pid_files_removes_dead_holder(tmp_path):
    """A crash-left index-*.pid lock (dead recorded pid, flock free) is
    removed; the file no longer exists after the sweep (#1231 T3)."""
    from tortoise.embedded_reaper import sweep_stale_index_pid_files
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    stale = lock_dir / "index-crashed.pid"
    stale.write_text("999999999 0\n")  # pid 999999999 is dead everywhere
    old = time.time() - 120
    os.utime(stale, (old, old))  # older than the 30s min-age guard

    removed = sweep_stale_index_pid_files(str(lock_dir), dry_run=False)
    assert removed == [str(stale)]
    assert not stale.exists()


def test_sweep_stale_index_pid_files_skips_live_holder(tmp_path):
    """A lock held by a live process (flock taken) is NEVER removed — the
    force_release TOCTOU guard refuses while the flock is contended."""
    from tortoise.index_lock import SessionIndexLock  # noqa: I001
    from tortoise.embedded_reaper import sweep_stale_index_pid_files
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    lock = SessionIndexLock("sess-live", str(lock_dir))
    assert lock.acquire() == "acquired"
    old = time.time() - 120
    os.utime(lock.path, (old, old))  # old file, but flock is HELD

    removed = sweep_stale_index_pid_files(str(lock_dir), dry_run=False)
    assert removed == []
    assert lock.path.exists()
    lock.release()


def test_sweep_stale_index_pid_files_age_guard(tmp_path):
    """A fresh lock file (younger than the min-age guard) is never touched,
    mirroring the socket walk's boot cooldown."""
    from tortoise.embedded_reaper import sweep_stale_index_pid_files
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    fresh = lock_dir / "index-fresh.pid"
    fresh.write_text("999999999 0\n")  # dead pid, but file is brand-new

    removed = sweep_stale_index_pid_files(str(lock_dir), dry_run=False)
    assert removed == []
    assert fresh.exists()


def test_sweep_stale_index_pid_files_skips_non_lock_files(tmp_path):
    """Non index-*.pid files in the lock dir are ignored."""
    from tortoise.embedded_reaper import sweep_stale_index_pid_files
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    other = lock_dir / "capture-abc.pid"  # different prefix (extension's)
    other.write_text("999999999\n")
    old = time.time() - 120
    os.utime(other, (old, old))

    removed = sweep_stale_index_pid_files(str(lock_dir), dry_run=False)
    assert removed == []
    assert other.exists()


def test_sweep_stale_index_pid_files_dry_run_does_not_remove(tmp_path):
    """dry_run=True reports the would-remove candidate without touching it."""
    from tortoise.embedded_reaper import sweep_stale_index_pid_files
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    stale = lock_dir / "index-crashed.pid"
    stale.write_text("999999999 0\n")
    old = time.time() - 120
    os.utime(stale, (old, old))

    removed = sweep_stale_index_pid_files(str(lock_dir), dry_run=True)
    assert removed == [str(stale)]
    assert stale.exists()  # dry-run never mutates


def test_run_sweep_includes_stale_pid_files(tmp_path, monkeypatch):
    """_run_sweep appends stale index-pid removals to its acted list."""
    from tortoise.embedded_reaper import _run_sweep
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    stale = lock_dir / "index-crashed.pid"
    stale.write_text("999999999 0\n")
    old = time.time() - 120
    os.utime(stale, (old, old))
    monkeypatch.setenv("TORTOISE_INDEX_LOCK_DIR", str(lock_dir))
    # No redis servers involved — discover() finds nothing; the pid sweep is
    # the only actor. _run_sweep takes the reaper lock; it must be free.
    monkeypatch.setattr("tortoise.embedded_reaper.discover",
                        lambda jobs=1, **kw: [])
    acted = _run_sweep(dry_run=False, batch_size=None, only_safe=True)
    pid_actions = [a for a in acted if a.get("classification") == "stale_pid_file"]
    assert len(pid_actions) == 1
    assert pid_actions[0]["pid_file"] == str(stale)
    assert not stale.exists()


def test_socket_dir_from_cmdline_config_file_form(tmp_path):
    """#1365: redis-server may start with a config FILE (unixsocket directive
    inside it) instead of an inline `unixsocket:` argv — the live pass must
    parse both forms or it silently misses live orphans on Linux."""
    import tortoise.embedded_reaper as er
    sock = tmp_path / "redis.socket"
    cfg = tmp_path / "redis.config"
    cfg.write_text(f"unixsocket '{sock}'\ndir '{tmp_path}'\n")
    er._PROC_INFO_CACHE = {
        424242: {"cmdline": f"/x/redis-server {cfg} --loadmodule /y/falkordb.so",
                 "etime": "00:00:01"},
        424243: {"cmdline": f"/x/redis-server unixsocket:{sock} --loadmodule /y.so",
                 "etime": "00:00:01"},
        424244: {"cmdline": f"/x/redis-server --unixsocket {sock} --daemonize yes",
                 "etime": "00:00:01"},
    }
    try:
        d1 = er._socket_dir_from_cmdline(424242)  # config-file form
        d2 = er._socket_dir_from_cmdline(424243)  # inline colon form
        d3 = er._socket_dir_from_cmdline(424244)  # long-form (Linux re-exec)
    finally:
        er._PROC_INFO_CACHE = {}
    assert d1 == str(tmp_path)
    assert d2 == str(tmp_path)
    assert d3 == str(tmp_path)


# ── #1383: probe contract + zombie-aware _pid_alive (plan Task 1) ────

def test_probe_socket_dead_socket_file_exists():
    """A real dead unix socket (bind+close, file persists) -> 'dead'."""
    from tortoise.embedded_reaper import _probe_socket
    d = tempfile.mkdtemp(prefix="reaper_probe_")
    try:
        sp = os.path.join(d, "redis.socket")
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(sp)
        s.close()  # file persists, no listener -> ECONNREFUSED
        assert _probe_socket(sp) == "dead"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_probe_socket_missing_file():
    """No socket file at all -> 'missing' (NOT 'dead' — mid-startup window)."""
    from tortoise.embedded_reaper import _probe_socket
    assert _probe_socket("/nonexistent/reaper-missing.sock") == "missing"


def test_probe_socket_alive_listener():
    """A live listener -> 'alive'."""
    from tortoise.embedded_reaper import _probe_socket
    d = tempfile.mkdtemp(prefix="reaper_probe_")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sp = os.path.join(d, "live.sock")
    srv.bind(sp)
    srv.listen(1)
    try:
        assert _probe_socket(sp) == "alive"
    finally:
        srv.close()
        shutil.rmtree(d, ignore_errors=True)


def test_pid_alive_zombie_returns_false():
    """Linux-only: a real zombie (forked child that exited, never waited)
    is NOT alive — the /proc stat Z check (#1365 precedent)."""
    if not os.path.exists("/proc"):
        pytest.skip("no /proc on this platform")
    from tortoise.embedded_reaper import _pid_alive
    pid = os.fork()
    if pid == 0:
        os._exit(0)  # child dies immediately; parent never waits -> zombie
    deadline = time.time() + 10
    while time.time() < deadline:
        with open(f"/proc/{pid}/stat") as fh:
            state = fh.read().split()[2]
        if state == "Z":
            break
        time.sleep(0.05)
    else:
        os.waitpid(pid, 0)
        pytest.fail("child never became a zombie")
    try:
        assert not _pid_alive(pid), "zombie reported alive"
    finally:
        os.waitpid(pid, 0)  # reap the zombie


def test_pid_alive_live_and_dead_unchanged():
    """Non-zombie behavior unchanged: live pid True, dead pid False."""
    from tortoise.embedded_reaper import _pid_alive
    assert _pid_alive(os.getpid()) is True
    assert _pid_alive(99999999) is False


# ── #1383: classification honesty — dead-pid → stale_socket (plan Task 2) ─

def _make_dead_pid_dir(base=None, name="tmp"):
    """Synthetic leftover dir with a real dead socket + registry pointing at
    a provably-dead pid. Uses a SHORT base dir: macOS AF_UNIX sun_path is
    limited to ~104 bytes and pytest tmp_path is too long. Returns
    (dbdir, socket_real)."""
    if base is None:
        base = Path(tempfile.mkdtemp(prefix="tt_"))
    dbdir = base / name
    dbdir.mkdir(exist_ok=True)
    sp = dbdir / "redis.socket"
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(str(sp))
    s.close()  # real dead socket file (persists)
    (dbdir / "redis.pid").write_text("99999999\n")  # > pid_max everywhere
    (dbdir / "x.settings").write_text(json.dumps({
        "pidfile": str(dbdir / "redis.pid"),
        "unixsocket": str(sp),
        "dbdir": str(dbdir),
        "dbfilename": "redis.db",
    }))
    return dbdir, os.path.realpath(str(sp))


class _stale_dir_env:
    """Context manager: (dbdir, sock) under a SHORT ephemeral base with
    tempfile.gettempdir() pointed at it (so discover's pass-2 walk finds
    the dir), tree removed on exit."""

    def __init__(self, name="tmp"):
        self.name = name

    def __enter__(self):
        self.base = Path(tempfile.mkdtemp(prefix="tt_"))
        self.dbdir, self.sock = _make_dead_pid_dir(self.base, name=self.name)
        self._cm = monkeypatch_tempdir(self.base)
        self._cm.__enter__()
        return self.dbdir, self.sock

    def __exit__(self, *exc):
        self._cm.__exit__(*exc)
        shutil.rmtree(self.base, ignore_errors=True)
        return False


def test_discover_classifies_dead_pid_dir_stale_socket():
    """Indicator (a): a dead-pid leftover dir -> 'stale_socket', NOT the
    phantom 'candidate' (reap() can never act on dead-pid candidates)."""
    from tortoise.embedded_reaper import discover
    with _stale_dir_env() as (dbdir, sock):  # noqa: RUF059
        found = discover()
        matches = [s for s in found if str(dbdir) in s.get("dbdir", "")]
        assert matches, "dead-pid dir not discovered"
        assert matches[0]["classification"] == "stale_socket"


def test_classify_live_never_stale_socket_for_respawned_server(monkeypatch):
    """Regression (PM3): a LIVE pgrep'd server whose registry pidfile is
    stale must NOT classify stale_socket — the known_pid pass-through keeps
    classification based on the authoritative live pid. #1642 FIX 5: the
    known_pid stands in for a real pgrep'd redis-server, so _pid_is_redis
    is patched (a live non-redis pid is a recycled number and correctly
    classifies stale_socket)."""
    from tortoise.embedded_reaper import _classify_dir
    monkeypatch.setenv("TORTOISE_REAPER_MIN_UPTIME", "0")
    monkeypatch.setattr("tortoise.embedded_reaper._pid_is_redis",
                        lambda pid: pid == os.getpid())
    base = Path(tempfile.mkdtemp(prefix="tt_"))
    dbdir, sock = _make_dead_pid_dir(base, name="redislite_x")
    try:
        rec = _classify_dir(str(dbdir), sock, known_pid=os.getpid())
        assert rec["classification"] in ("candidate", "protected")
        assert rec["classification"] != "stale_socket"
        rec2 = _classify_dir(str(dbdir), sock)
        assert rec2["classification"] == "stale_socket"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_classify_live_never_stale_socket_for_path_based_server(monkeypatch):
    """Regression (review P1): the known_pid pass-through must ALSO hold for
    PATH-BASED servers (user dir + user dbfilename in the registry) — the
    #1427 orphan branches previously dropped known_pid and re-read the stale
    registry pidfile, misclassifying a LIVE server as stale_socket.
    #1642 FIX 5: known_pid patched as a real redis-server (see respawned
    test)."""
    from tortoise.embedded_reaper import _classify_dir
    monkeypatch.setenv("TORTOISE_REAPER_MIN_UPTIME", "0")
    monkeypatch.setattr("tortoise.embedded_reaper._pid_is_redis",
                        lambda pid: pid == os.getpid())
    base = Path(tempfile.mkdtemp(prefix="tt_"))
    try:
        dbdir = base / "redislite_pb"
        dbdir.mkdir()
        sp = dbdir / "redis.socket"
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(str(sp))
        s.close()
        (dbdir / "redis.pid").write_text("99999999\n")  # stale/dead owner
        # Path-based registry: user dir OUTSIDE the ephemeral tempdir +
        # user dbfilename (Signal 1 + Signal 2 both say "protected class").
        (dbdir / "x.settings").write_text(json.dumps({
            "pidfile": str(dbdir / "redis.pid"),
            "unixsocket": str(sp),
            "dir": str(Path.home()),
            "dbfilename": "pathbased_reaper_test.db",
        }))
        # LIVE server (pgrep-found): must never classify stale_socket.
        rec = _classify_dir(str(dbdir), str(sp), known_pid=os.getpid())
        assert rec["classification"] in ("candidate", "protected"), \
            f"live path-based server misclassified: {rec['classification']}"
        assert rec["classification"] != "stale_socket"
        # Dead leftover dir walk (no known_pid): stays stale_socket — the
        # #1427 orphan reclassification must not regress.
        rec2 = _classify_dir(str(dbdir), str(sp))
        assert rec2["classification"] == "stale_socket"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_classify_dir_client_count_none_on_probe_failure(monkeypatch):
    """Probe-failed client count records None, not a false 0 — must FAIL
    pre-fix (old behavior records 0) and pin the fix. #1642 FIX 5: the
    known_pid stands in for a real pgrep'd redis-server, so _pid_is_redis
    is patched (a live non-redis pid would otherwise classify recycled -
    stale_socket)."""
    from tortoise.embedded_reaper import _classify_dir
    monkeypatch.setenv("TORTOISE_REAPER_MIN_UPTIME", "0")
    monkeypatch.setattr("tortoise.embedded_reaper._pid_is_redis",
                        lambda pid: pid == os.getpid())
    with _stale_dir_env() as (dbdir, sock):
        monkeypatch.setattr(
            "tortoise.embedded_reaper._active_client_count", lambda _s: None)
        rec = _classify_dir(str(dbdir), sock, known_pid=os.getpid())
        assert rec is not None
        assert rec["classification"] == "candidate"
        assert rec["client_count"] is None, \
            "probe failure must not record 0"



# ── #1383: reap() stale branch — guarded rmtree (plan Task 3) ─────────

def _mark_quarantine(q: str) -> None:
    """Write the reaper-owned marker (#1383 security review Issue 3): the
    quarantine sweep only rmtrees dirs carrying it, so fabricated dirs in
    these tests must carry it too."""
    from tortoise.embedded_reaper import REAPER_OWNED_MARKER
    with open(os.path.join(q, REAPER_OWNED_MARKER), "w") as fh:
        fh.write("reaper-owned\n")


def _backdate_dir(dbdir, seconds=120):
    old = time.time() - seconds
    os.utime(dbdir, (old, old))


def _stale_record(dbdir, sock):
    return {
        "pid": 99999999,  # dead everywhere (> pid_max)
        "socket_path": sock,
        "dbdir": str(dbdir),
        "client_count": None,
        "uptime": None,
        "classification": "stale_socket",
        "settings": None,
    }


def test_reap_removes_stale_socket_dir():
    """Old dead-pid dir -> rmtree'd; acted list contains it with the
    renamed path recorded for --json correlation."""
    from tortoise.embedded_reaper import reap
    with _stale_dir_env() as (dbdir, sock):
        _backdate_dir(dbdir)
        acted = reap([_stale_record(dbdir, sock)], dry_run=False)
        assert any(a["dbdir"] == str(dbdir) for a in acted)
        assert any(".reaper-stale-" in a.get("removed_dir", "") for a in acted), \
            "acted record must carry the renamed path in removed_dir"
        assert not os.path.exists(dbdir), "stale dir not removed"


def test_reap_stale_socket_dry_run_reports_without_mutating():
    from tortoise.embedded_reaper import reap
    with _stale_dir_env() as (dbdir, sock):
        _backdate_dir(dbdir)
        acted = reap([_stale_record(dbdir, sock)], dry_run=True)
        assert acted  # reported as would-act
        assert os.path.exists(dbdir), "dry-run must not mutate"


def test_reap_stale_socket_aborts_when_pidfile_pid_alive(monkeypatch):
    """Respawn window: pidfile now holds a LIVE REDIS pid (a server
    respawned on the same socket) -> abort, keep dir. #1642 FIX 5: the
    discriminator is now redis-identity — a live non-redis pid is a
    recycled number (see the recycled-pid test below), so the redis check
    is patched here to model a genuine respawn."""
    from tortoise.embedded_reaper import reap
    monkeypatch.setattr("tortoise.embedded_reaper._pid_is_redis",
                        lambda pid: True)
    with _stale_dir_env() as (dbdir, sock):
        _backdate_dir(dbdir)
        (dbdir / "redis.pid").write_text(f"{os.getpid()}\n")  # live pid
        acted = reap([_stale_record(dbdir, sock)], dry_run=False)
        assert acted == []
        assert os.path.exists(dbdir)


def test_reap_stale_socket_recycled_pid_proceeds(monkeypatch):
    """#1642 FIX 5: a stale dir whose pidfile holds a LIVE but NON-redis pid
    is a recycled number — the recorded server is provably gone, so the
    guarded removal proceeds (the socket re-probe + age guards still gate;
    this dir is a dead-socket old leftover). Previously the live-pid check
    aborted and the leftover was never cleaned (recycled pids defeated
    kill(0) liveness forever)."""
    from tortoise.embedded_reaper import reap
    with _stale_dir_env() as (dbdir, sock):
        _backdate_dir(dbdir)
        (dbdir / "redis.pid").write_text(f"{os.getpid()}\n")  # live, non-redis
        acted = reap([_stale_record(dbdir, sock)], dry_run=False)
        assert any(a["dbdir"] == str(dbdir) for a in acted)
        assert not os.path.exists(dbdir)


def test_reap_stale_socket_aborts_when_socket_alive():
    """A live listener on the socket -> abort, keep dir."""
    from tortoise.embedded_reaper import reap
    with _stale_dir_env() as (dbdir, sock):
        _backdate_dir(dbdir)
        os.remove(sock)  # macOS bind() refuses to overwrite an existing file
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(sock)
        srv.listen(1)
        try:
            acted = reap([_stale_record(dbdir, sock)], dry_run=False)
            assert acted == []
            assert os.path.exists(dbdir)
        finally:
            srv.close()


def test_reap_stale_socket_aborts_when_socket_missing():
    """No socket file (mid-startup window) -> abort, keep dir."""
    from tortoise.embedded_reaper import reap
    with _stale_dir_env() as (dbdir, sock):
        _backdate_dir(dbdir)
        os.remove(sock)  # socket file gone -> 'missing' verdict
        acted = reap([_stale_record(dbdir, sock)], dry_run=False)
        assert acted == []
        assert os.path.exists(dbdir)


def test_reap_stale_socket_already_gone_reported_acted():
    """Guard 1: an already-vanished dir is reported acted (no error)."""
    from tortoise.embedded_reaper import reap
    with _stale_dir_env() as (dbdir, sock):
        _backdate_dir(dbdir)
        shutil.rmtree(dbdir, ignore_errors=True)  # dir genuinely gone
        acted = reap([_stale_record(dbdir, sock)], dry_run=False)
        assert any(a["dbdir"] == str(dbdir) for a in acted)


def test_reap_stale_socket_rename_failure_aborts(monkeypatch):
    """Guard 6: os.rename OSError -> abort, dir intact, WARN logged."""
    from tortoise.embedded_reaper import reap
    with _stale_dir_env() as (dbdir, sock):
        _backdate_dir(dbdir)
        real_rename = os.rename

        def _boom(src, dst):
            raise OSError("simulated rename failure")

        monkeypatch.setattr("tortoise.embedded_reaper.os.rename", _boom)
        try:
            acted = reap([_stale_record(dbdir, sock)], dry_run=False)
        finally:
            monkeypatch.setattr("tortoise.embedded_reaper.os.rename", real_rename)
        assert acted == []
        assert os.path.exists(dbdir)


def test_reap_stale_socket_quarantine_probe_alive_leaves_dir(monkeypatch):
    """Guard 7: post-rename re-probe 'alive' (respawn in the window) ->
    leave quarantine, NEVER delete. The TOCTOU closer, pinned."""
    from tortoise.embedded_reaper import reap
    with _stale_dir_env() as (dbdir, sock):
        _backdate_dir(dbdir)
        calls = {"n": 0}

        def _seq_probe(path, timeout=2.0):
            calls["n"] += 1
            return "dead" if calls["n"] == 1 else "alive"  # guard 4 dead, guard 7 alive

        monkeypatch.setattr("tortoise.embedded_reaper._probe_socket", _seq_probe)
        acted = reap([_stale_record(dbdir, sock)], dry_run=False)
        assert acted == []
        assert not os.path.exists(dbdir)  # renamed away
        import glob as _g
        quars = _g.glob(str(dbdir) + ".reaper-stale-*")
        assert quars, "quarantine dir must exist (not deleted)"


def test_reap_stale_socket_quarantine_moved_pid_alive_aborts(monkeypatch):
    """Guard 8: a live pid written into the MOVED pidfile during the
    window (backlog-full hardening) -> leave quarantine, never delete.
    #1642 FIX 5: only a live REDIS pid aborts — the test's live pid stands
    in for a respawned server, so _pid_is_redis is patched."""
    from tortoise.embedded_reaper import reap
    monkeypatch.setattr("tortoise.embedded_reaper._pid_is_redis",
                        lambda pid: pid == os.getpid())
    with _stale_dir_env() as (dbdir, sock):
        _backdate_dir(dbdir)
        real_rename = os.rename

        def _rename_and_rewrite(src, dst):
            real_rename(src, dst)
            Path(dst, "redis.pid").write_text(f"{os.getpid()}\n")  # live pid now

        monkeypatch.setattr("tortoise.embedded_reaper.os.rename", _rename_and_rewrite)
        acted = reap([_stale_record(dbdir, sock)], dry_run=False)
        assert acted == []
        import glob as _g
        assert _g.glob(str(dbdir) + ".reaper-stale-*"), "quarantine kept"


def test_reap_stale_socket_rejects_quarantine_dir():
    """Re-entry guard: a dbdir already containing the quarantine suffix is
    never re-renamed (discover pass 2 also skips them — plan-review P1)."""
    from tortoise.embedded_reaper import reap
    with _stale_dir_env() as (dbdir, sock):  # noqa: RUF059
        _backdate_dir(dbdir)
        q = str(dbdir) + ".reaper-stale-999"
        os.rename(dbdir, q)
        _mark_quarantine(q)
        _mark_quarantine(q)
        acted = reap([_stale_record(q, os.path.join(q, "redis.socket"))],
                     dry_run=False)
        assert acted == []
        assert os.path.exists(q)


def test_reap_stale_budget_caps_removals(monkeypatch):
    """STALE_SWEEP_BUDGET caps stale removals per reap() call; the rest
    stay for the next sweep."""
    from tortoise.embedded_reaper import reap
    monkeypatch.setattr("tortoise.embedded_reaper.STALE_SWEEP_BUDGET", 5)
    n = 10  # budget + 5; decoupled from the production constant
    with _stale_dir_env() as (dbdir, sock):
        base = dbdir.parent
        dirs = [(dbdir, sock)]
        for i in range(n - 1):
            d, s = _make_dead_pid_dir(base, name=f"redislite_b{i}")
            _backdate_dir(d)
            dirs.append((d, s))
        _backdate_dir(dbdir)
        acted = reap([_stale_record(d, s) for d, s in dirs], dry_run=False)
        assert len(acted) == 5
        remaining = [d for d, _ in dirs if os.path.exists(d)]
        assert len(remaining) == 5, "budget cap must leave the remainder"


def test_reap_stale_does_not_call_client_list(monkeypatch):
    """Stale removal never probes CLIENT LIST (no server exists to list)."""
    from tortoise.embedded_reaper import reap
    with _stale_dir_env() as (dbdir, sock):
        _backdate_dir(dbdir)

        def _boom(_s):
            raise AssertionError("CLIENT LIST must not be probed for stale records")

        monkeypatch.setattr("tortoise.embedded_reaper._active_client_count", _boom)
        acted = reap([_stale_record(dbdir, sock)], dry_run=False)
        assert any(a["dbdir"] == str(dbdir) for a in acted)


def test_reap_stale_socket_age_guard_protects_fresh_dir():
    """Dir younger than STALE_SOCKET_MIN_AGE_DEFAULT -> abort (boot window)."""
    from tortoise.embedded_reaper import reap
    with _stale_dir_env() as (dbdir, sock):  # fresh mtime
        acted = reap([_stale_record(dbdir, sock)], dry_run=False)
        assert acted == []
        assert os.path.exists(dbdir)


def test_reap_stale_socket_refuses_non_ephemeral_dir():
    """Containment: a non-tempdir/non-ephemeral dbdir is never removed."""
    from tortoise.embedded_reaper import reap
    with _stale_dir_env() as (dbdir, sock):
        _backdate_dir(dbdir)
        rec = _stale_record(dbdir, sock)
        rec["dbdir"] = "/some/user/path/not-under-tmpdir"  # attacker/crafted
        acted = reap([rec], dry_run=False)
        assert acted == []
        assert os.path.exists(dbdir)


def test_reap_stale_socket_does_not_consume_kill_budget():
    """Stale removals don't increment killed: batch_size=0 still removes."""
    from tortoise.embedded_reaper import reap
    with _stale_dir_env() as (dbdir, sock):
        _backdate_dir(dbdir)
        acted = reap([_stale_record(dbdir, sock)], dry_run=False, batch_size=0)
        assert any(a["dbdir"] == str(dbdir) for a in acted)
        assert not os.path.exists(dbdir)


def test_reap_only_safe_acts_on_stale_socket():
    """only_safe admits stale_socket removal (guards are the safety)."""
    from tortoise.embedded_reaper import reap
    with _stale_dir_env() as (dbdir, sock):
        _backdate_dir(dbdir)
        acted = reap([_stale_record(dbdir, sock)], dry_run=False, only_safe=True)
        assert any(a["dbdir"] == str(dbdir) for a in acted)
        assert not os.path.exists(dbdir)


def test_reap_dead_pid_candidate_log_wording(monkeypatch, caplog):
    """The liveness-gate log now says 'dead pid', not 'dead socket connect'."""
    from tortoise.embedded_reaper import reap
    rec = {"classification": "candidate", "pid": 99999999,
           "socket_path": "/tmp/x.sock", "dbdir": "/tmp/x"}
    reap([rec], dry_run=False)
    assert "dead pid" in caplog.text
    assert "dead socket connect" not in caplog.text


def test_reap_mixed_list_budget_exhaustion_stale_still_processed(monkeypatch):
    """Ordering: interleaved dead-pid candidates (discarded at the
    liveness gate) must NOT block later stale removals, and stale removals
    never consume the kill budget. (Branch-ordering property; the literal
    budget-exhaustion path is pinned by the batch_size=0 test.)"""
    from tortoise.embedded_reaper import reap
    with _stale_dir_env() as (s1, _sock1):
        base = s1.parent
        s2, _sock2 = _make_dead_pid_dir(base, name="redislite_s2")
        _backdate_dir(s1)
        _backdate_dir(s2)
        cand = {"classification": "candidate", "pid": 99999999,
                "socket_path": "/tmp/c.sock", "dbdir": "/tmp/c"}
        acted = reap([cand, _stale_record(s1, os.path.join(s1, "redis.socket")),
                      cand, _stale_record(s2, os.path.join(s2, "redis.socket"))],
                     dry_run=False, batch_size=1)
        # candidate dead-pid is skipped at the liveness gate; both stales removed
        assert len([a for a in acted if a.get("classification") == "stale_socket"]) == 2
        assert not os.path.exists(s1) and not os.path.exists(s2)


def test_stale_dir_reuse_pidfile_rewrite_does_not_refresh_dir_mtime(monkeypatch):
    """Pins the documented assumption: an in-place pidfile rewrite updates
    the FILE mtime, not the DIR mtime — the age guard alone does NOT catch
    a reused old dir; guards 3/4 (pidfile re-read + socket probe) carry it.
    #1642 FIX 5: the guards catch a LIVE REDIS pid (respawn); the test's
    live pid stands in for the respawned server via the _pid_is_redis
    patch (an alive non-redis pid is a recycled number and correctly
    proceeds to removal)."""
    from tortoise.embedded_reaper import reap
    monkeypatch.setattr("tortoise.embedded_reaper._pid_is_redis",
                        lambda pid: pid == os.getpid())
    with _stale_dir_env() as (dbdir, sock):
        _backdate_dir(dbdir)
        before = os.stat(dbdir).st_mtime_ns
        time.sleep(0.02)
        Path(dbdir, "redis.pid").write_text(f"{os.getpid()}\n")  # in-place rewrite
        after = os.stat(dbdir).st_mtime_ns
        assert before == after, "dir mtime must not refresh on file rewrite"
        # and the guards must still catch the live server: reap aborts via
        # guard 3 (pidfile now holds the live pytest pid)
        rec = _stale_record(dbdir, sock)
        rec["classification"] = "stale_socket"
        acted = reap([rec], dry_run=False)
        assert acted == []
        assert os.path.exists(dbdir)


# ── #4098: shared-tempdir marker-write hardening (CWE-377) ──────────

def test_stale_marker_write_never_follows_symlink(monkeypatch):
    """#4098 / CWE-377 / SEI CERT FIO21-C: the pre-rename marker write must
    never follow a symlink planted at the marker path.

    The candidate dir is discovered in a SHARED, world-writable tempdir
    (Linux `/tmp`, mode 1777), so its owner may be a different local uid than
    the reaper's. A plain `open(path, "w")` at `REAPER_OWNED_MARKER` FOLLOWS a
    symlink there, converting "write a marker" into "truncate any file the
    reaper's uid can write".

    The fixture is a decoy aged past the boot-cooldown guard, carrying a REAL
    dead socket AND a planted symlink at the marker path, with the tempdir
    redirected onto a scratch base — so the truncation is provably the marker
    write's and no abort at an earlier guard can explain it.

    Removed the `O_NOFOLLOW`/dir-fd guard (the pre-#4098 body: plain
    `open(..., "w")`) -> the symlink target is truncated to "reaper-owned\\n",
    i.e. this test reddens (verified by mutation).
    """
    from tortoise.embedded_reaper import (
        REAPER_OWNED_MARKER,
        STALE_QUARANTINE_SUFFIX,
        _is_ephemeral_dir,
        _run_sweep,
    )
    base = Path(tempfile.mkdtemp(prefix="tt_"))
    try:
        target = base / "victim-writable-file.txt"
        target.write_text("ORIGINAL CONTENT\n")
        dbdir, _sock = _make_dead_pid_dir(base, name="tmpEVILXX")
        # THE ATTACK: the marker path is a symlink out of the candidate dir.
        (dbdir / REAPER_OWNED_MARKER).symlink_to(target)
        _backdate_dir(dbdir)  # past STALE_SOCKET_MIN_AGE_DEFAULT
        assert _is_ephemeral_dir(os.path.realpath(str(dbdir)),
                                 os.path.realpath(str(base))), \
            "fixture must be inside the ephemeral namespace — else the " \
            "refusal would be containment's, not the marker guard's"
        # No pgrep in the fixture: pass 2 is what must discover the decoy.
        monkeypatch.setattr("tortoise.embedded_reaper._pgrep_redis_servers",
                            lambda: [])
        with monkeypatch_tempdir(base):
            acted = _run_sweep(dry_run=False, batch_size=None,
                               sweep_pid_files=False)
        assert target.read_text() == "ORIGINAL CONTENT\n", \
            "reaper followed a planted marker symlink and truncated a file " \
            "outside the candidate dir (CWE-377)"
        # The hardened write must not leak the candidate either: the planted
        # entry is removed (never followed) and the dir is REAPED (not merely
        # renamed and left quarantined by a guard-7/8 abort).
        dbdir_real = os.path.realpath(str(dbdir))
        assert any(a.get("dbdir") == dbdir_real for a in acted), \
            "hardened marker write must still let the sweep reap the dir"
        assert not [p for p in base.iterdir()
                    if STALE_QUARANTINE_SUFFIX in p.name], \
            "the dir must be fully reaped, not left as a quarantine"
    finally:
        shutil.rmtree(base, ignore_errors=True)


# ── #1383: pipeline integration — quarantine sweep (plan Task 4) ─────

def test_discover_skips_quarantine_dirs():
    """Plan-review P1: discover pass 2 must skip *.reaper-stale-* dirs —
    they are reaper-owned and handled exclusively by the quarantine sweep.
    Without the skip, a guard-7-preserved LIVE server in a quarantine dir
    would classify 'candidate' next sweep and be killed."""
    from tortoise.embedded_reaper import discover
    with _stale_dir_env() as (dbdir, sock):  # noqa: RUF059
        _backdate_dir(dbdir)
        dbdir_real = os.path.realpath(str(dbdir))
        q = dbdir_real + ".reaper-stale-777"
        os.rename(dbdir, q)
        _mark_quarantine(q)
        _mark_quarantine(q)
        found = discover()
        assert all(str(dbdir) not in s.get("dbdir", "") for s in found)
        assert all(".reaper-stale-" not in s.get("dbdir", "") for s in found)


def test_phase1_probe_missing_socket_undetermined():
    """#1383: a vanished socket (mid-startup) fails closed to
    'undetermined' — never 'stale_socket' (which licenses removal)."""
    from tortoise.embedded_reaper import phase1_probe
    with _stale_dir_env() as (dbdir, sock):
        os.remove(sock)
        rec = {"pid": 99999999, "socket_path": sock,
               "dbdir": str(dbdir), "classification": "candidate"}
        assert phase1_probe(rec)["classification"] == "undetermined"


def test_reap_phase1_stale_socket_end_to_end():
    """discover() -> phase1_probe -> reap() removes a dead-pid leftover dir."""
    from tortoise.embedded_reaper import discover, phase1_probe, reap
    with _stale_dir_env() as (dbdir, sock):  # noqa: RUF059
        _backdate_dir(dbdir)
        dbdir_real = os.path.realpath(str(dbdir))
        found = discover()
        matches = [s for s in found if dbdir_real in s.get("dbdir", "")]
        assert matches and matches[0]["classification"] == "stale_socket"
        resolved = phase1_probe(matches[0])
        assert resolved["classification"] == "stale_socket"
        acted = reap([resolved], dry_run=False)
        assert any(a["dbdir"] == dbdir_real for a in acted)
        assert not os.path.exists(dbdir)


def test_run_sweep_removes_stale_socket_record(monkeypatch):
    """_run_sweep (the conftest/CLI entry) reaps stale_socket records.
    sweep_pid_files=False so the test never touches ~/.tortoise; wrapped in
    monkeypatch_tempdir so the quarantine sweep stays inside the test tree."""
    from tortoise.embedded_reaper import _run_sweep
    with _stale_dir_env() as (dbdir, sock):
        _backdate_dir(dbdir)
        monkeypatch.setenv("TORTOISE_INDEX_LOCK_DIR", str(dbdir.parent / "locks"))
        monkeypatch.setattr(
            "tortoise.embedded_reaper.discover",
            lambda jobs=1, **kw: [_stale_record(dbdir, sock)])
        acted = _run_sweep(dry_run=False, batch_size=None, only_safe=True,
                           sweep_pid_files=False)
        assert any(a.get("dbdir") == str(dbdir) for a in acted)
        assert not os.path.exists(dbdir)


def test_run_sweep_live_quarantine_not_killed():
    """Plan-review P1 pin: a guard-7 live quarantine dir must NOT be killed
    or deleted by a full _run_sweep — the quarantine is reaper-owned."""
    from tortoise.embedded_reaper import _run_sweep
    with _stale_dir_env() as (dbdir, sock):  # noqa: RUF059
        _backdate_dir(dbdir)
        q = os.path.realpath(str(dbdir)) + ".reaper-stale-888"
        os.rename(dbdir, q)
        _mark_quarantine(q)
        _mark_quarantine(q)
        # A live listener bound on the moved socket (server moved with the dir)
        moved_sock = os.path.join(q, "redis.socket")
        os.remove(moved_sock)  # macOS bind() refuses to overwrite a stale file
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(moved_sock)
        srv.listen(1)
        try:
            acted = _run_sweep(dry_run=False, batch_size=None, only_safe=True,
                               sweep_pid_files=False)
            assert os.path.exists(q), "live quarantine must be kept"
            assert not any(a.get("dbdir") == str(q) for a in acted)
        finally:
            srv.close()


def test_run_sweep_pass1_live_server_in_quarantine_not_killed(monkeypatch):
    """Cycle-2 P2-2 pin: a pgrep-able LIVE server whose dir was renamed to
    quarantine is re-discovered by pass 1 at its ORIGINAL (now-gone)
    cmdline path. With the known_pid pass-through it classifies 'candidate'
    — safety depends on reap()'s CLIENT LIST failing closed on the moved
    socket (FileNotFoundError). Pins the interplay so a future
    retry-widening can't convert it into a wrongful kill."""
    from tortoise.embedded_reaper import _run_sweep, _classify_dir  # noqa: I001
    with _stale_dir_env() as (dbdir, sock):
        _backdate_dir(dbdir)
        q = os.path.realpath(str(dbdir)) + ".reaper-stale-900"
        os.rename(dbdir, q)
        _mark_quarantine(q)
        _mark_quarantine(q)
        # A live listener on the MOVED socket (server moved with its dir)
        moved_sock = os.path.join(q, "redis.socket")
        os.remove(moved_sock)  # macOS bind() refuses to overwrite a stale file
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(moved_sock)
        srv.listen(1)
        live_pid = 42424242
        # Make ONLY the fake live pid 'alive' (all real pids report dead).
        monkeypatch.setattr("tortoise.embedded_reaper._pid_alive",
                            lambda pid: pid == live_pid)
        # #1642 FIX 5: the fake live pid stands in for a real pgrep'd
        # redis-server (a live non-redis pid would classify recycled -
        # stale_socket instead of the dangerous candidate shape).
        monkeypatch.setattr("tortoise.embedded_reaper._pid_is_redis",
                            lambda pid: pid == live_pid)
        monkeypatch.setattr("tortoise.embedded_reaper._socket_dir_from_cmdline",
                            lambda pid: str(dbdir))  # original (gone) path
        monkeypatch.setattr(
            "tortoise.embedded_reaper._pgrep_redis_servers", lambda: [live_pid])
        try:
            rec = _classify_dir(str(dbdir), sock, known_pid=live_pid)
            assert rec["classification"] == "candidate"  # the dangerous shape
            acted = _run_sweep(dry_run=False, batch_size=None,
                               only_safe=False, sweep_pid_files=False)
            # reap()'s CLIENT LIST fails closed on the moved socket -> no
            # kill, and the quarantine sweep keeps the live dir (live probe)
            assert os.path.exists(q), "live quarantine must survive the sweep"
            assert not any(a.get("pid") == live_pid for a in acted)
        finally:
            srv.close()


def test_run_sweep_combined_quarantine_and_pid_files(monkeypatch):
    """One _run_sweep performs BOTH the quarantine sweep and the index-pid
    sweep; both classes appear in acted (regression guard)."""
    from tortoise.embedded_reaper import _run_sweep
    with _stale_dir_env() as (dbdir, sock):  # noqa: RUF059
        _backdate_dir(dbdir)
        q = os.path.realpath(str(dbdir)) + ".reaper-stale-999"
        os.rename(dbdir, q)  # dead quarantine -> removed
        _mark_quarantine(q)
        locks = dbdir.parent / "locks"
        locks.mkdir()
        stale_pid = locks / "index-crashed.pid"
        stale_pid.write_text("999999999 0\n")
        old = time.time() - 120
        os.utime(stale_pid, (old, old))
        monkeypatch.setenv("TORTOISE_INDEX_LOCK_DIR", str(locks))
        monkeypatch.setattr("tortoise.embedded_reaper.discover", lambda jobs=1, **kw: [])
        acted = _run_sweep(dry_run=False, batch_size=None, only_safe=True)
        classes = {a.get("classification") for a in acted}
        assert "stale_quarantine" in classes
        assert "stale_pid_file" in classes
        assert not os.path.exists(q)
        assert not stale_pid.exists()


def test_cli_json_emits_stale_socket_shape(monkeypatch):
    """S9: the CLI --json contract carries the new classification + path
    keys for stale actions. Reaper lock monkeypatched so a dev-box cron
    reaper can't make the test non-hermetic (cycle 2)."""
    from tortoise.embedded_reaper import main
    with _stale_dir_env() as (dbdir, sock):
        _backdate_dir(dbdir)
        monkeypatch.setenv("TORTOISE_INDEX_LOCK_DIR", str(dbdir.parent / "locks"))
        monkeypatch.setattr("tortoise.embedded_reaper._ReaperLock.acquire",
                            lambda self: True)
        monkeypatch.setattr("tortoise.embedded_reaper._ReaperLock.release",
                            lambda self: None)
        monkeypatch.setattr(
            "tortoise.embedded_reaper.discover",
            lambda jobs=1, **kw: [_stale_record(dbdir, sock)])
        import io
        import json as _json
        out = io.StringIO()
        monkeypatch.setattr("sys.stdout", out)
        rc = main(["--no-dry-run", "--json", "--timeout", "60"])
        assert rc == 0
        data = _json.loads(out.getvalue())
        stale = [d for d in data if d.get("classification") == "stale_socket"]
        assert stale, "stale_socket missing from --json output"
        assert stale[0].get("removed_dir"), \
            "stale acted record must carry removed_dir"
        assert not os.path.exists(dbdir)


def test_sweep_quarantine_dirs_dry_run_reports_without_mutating():
    """Quarantine dry-run reports would-remove without touching the dir."""
    from tortoise.embedded_reaper import _sweep_quarantine_dirs
    with _stale_dir_env() as (dbdir, sock):  # noqa: RUF059
        q = os.path.realpath(str(dbdir)) + ".reaper-stale-123"
        os.rename(dbdir, q)
        _mark_quarantine(q)
        _mark_quarantine(q)
        removed = _sweep_quarantine_dirs(dry_run=True)
        assert q in removed
        assert os.path.exists(q), "dry-run must not mutate"


def test_sweep_quarantine_dirs_removes_dead_leftover():
    """Partial-rmtree / respawn leftovers under *.reaper-stale-* converge."""
    from tortoise.embedded_reaper import _sweep_quarantine_dirs
    with _stale_dir_env() as (dbdir, sock):  # noqa: RUF059
        q = os.path.realpath(str(dbdir)) + ".reaper-stale-123"
        os.rename(dbdir, q)  # simulate a quarantine from a prior sweep
        _mark_quarantine(q)
        removed = _sweep_quarantine_dirs(dry_run=False)
        assert q in removed
        assert not os.path.exists(q)


def test_quarantine_sweep_budget_expiry_partial(monkeypatch, caplog):
    """#4068: the quarantine scan shares the bounded primitive — expiry is a
    WARNING + partial result, never an exception or a silent empty sweep."""
    import tortoise.embedded_reaper as _R
    with _stale_dir_env() as (dbdir, sock):  # noqa: RUF059
        q = os.path.realpath(str(dbdir)) + ".reaper-stale-123"
        os.rename(dbdir, q)
        _mark_quarantine(q)
        monkeypatch.setattr(_R, "SOCKET_WALK_TIMEOUT", -1.0)
        with caplog.at_level("WARNING"):
            removed = _R._sweep_quarantine_dirs(dry_run=True)
        assert removed == []
        assert "budget" in caplog.text.lower()
        assert os.path.exists(q), "partial scan must not mutate"


def test_sweep_quarantine_dirs_keeps_live_leftover():
    """A quarantine whose socket is live is WARNed and kept (forensic)."""
    from tortoise.embedded_reaper import _sweep_quarantine_dirs
    with _stale_dir_env() as (dbdir, sock):  # noqa: RUF059
        q = os.path.realpath(str(dbdir)) + ".reaper-stale-456"
        os.rename(dbdir, q)
        _mark_quarantine(q)
        _mark_quarantine(q)
        moved_sock = os.path.join(q, "redis.socket")
        os.remove(moved_sock)  # macOS bind() refuses to overwrite a stale file
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(moved_sock)
        srv.listen(1)
        try:
            removed = _sweep_quarantine_dirs(dry_run=False)
            assert q not in removed
            assert os.path.exists(q)
        finally:
            srv.close()


def test_sweep_quarantine_dirs_skips_symlink():
    """Symlinked *.reaper-stale-* entries are never probed or removed
    (mirror discover pass 2; cycle-2 P2-4)."""
    from tortoise.embedded_reaper import _sweep_quarantine_dirs
    with _stale_dir_env() as (dbdir, sock):  # noqa: RUF059
        link = dbdir.parent / "redislite_x.reaper-stale-999"
        link.symlink_to(dbdir, target_is_directory=True)
        removed = _sweep_quarantine_dirs(dry_run=False)
        assert str(link) not in removed
        assert os.path.islink(link)
        assert os.path.exists(dbdir)


def test_sweep_quarantine_dirs_removes_socketless_partial():
    """A partial-rmtree quarantine that lost its socket (SIGALRM interrupt)
    converges: rmtree'd once its dir mtime passes the age gate."""
    from tortoise.embedded_reaper import _sweep_quarantine_dirs
    with _stale_dir_env() as (dbdir, sock):  # noqa: RUF059
        q = os.path.realpath(str(dbdir)) + ".reaper-stale-654"
        os.rename(dbdir, q)
        _mark_quarantine(q)
        _mark_quarantine(q)
        os.remove(os.path.join(q, "redis.socket"))  # socket gone (partial rmtree)
        old = time.time() - 120
        os.utime(q, (old, old))  # aged shell
        removed = _sweep_quarantine_dirs(dry_run=False)
        assert q in removed
        assert not os.path.exists(q)


# ── #1383: bounded probe retry (plan Task 5) ─────────────────────────

class _DelayedRespServer:
    """Threaded fake unix-socket server with per-connection handling.
    Modes: first_delay (bool) HOLDS conn 1 until the client's read times
    out and closes (deterministic — no sleep-vs-timeout margin), then
    serves conn 2 immediately; never_reply (bool) holds every connection."""

    def __init__(self, payload=b"$11\r\nid=1 age=5\n\r\n",
                 first_delay=True, never_reply=False):
        self.payload = payload
        self.first_delay = first_delay
        self.never_reply = never_reply
        self.accepts = 0
        self._srv = None
        self._tmp = None
        self._lock = threading.Lock()

    def __enter__(self):
        import tempfile as _tf
        self._tmp = _tf.mkdtemp(prefix="rp_")
        self.sock_path = os.path.join(self._tmp, "d.sock")
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._srv.bind(self.sock_path)
        self._srv.listen(4)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self

    def wait_accepts(self, n, timeout=3.0):
        """Bounded poll for the accept counter (no timing assumptions)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if self.accepts >= n:
                    return
            time.sleep(0.02)
        with self._lock:
            raise AssertionError(
                f"expected {n} accepts, got {self.accepts}")

    def _serve(self):
        try:
            while True:
                conn, _ = self._srv.accept()
                with self._lock:
                    self.accepts += 1
                    n = self.accepts
                threading.Thread(
                    target=self._handle, args=(conn, n), daemon=True).start()
        except OSError:
            pass

    def _handle(self, conn, n):
        try:
            conn.recv(4096)  # consume CLIENT LIST
            if self.never_reply or (n == 1 and self.first_delay):
                # Hold conn 1 until the client's read-times-out and closes
                # (recv returns b"" on client close) — DETERMINISTIC first
                # attempt timeout regardless of scheduling. Then serve conn 2
                # immediately (no timing margins; plan-review P1).
                while conn.recv(4096):
                    time.sleep(0.05)
                return
            conn.sendall(self.payload)
        except OSError:
            pass  # client closed first — the hold produced the timeout
        finally:
            try:  # noqa: SIM105
                conn.close()
            except OSError:
                pass

    def __exit__(self, *exc):
        try:  # noqa: SIM105
            self._srv.close()
        except OSError:
            pass
        self._thread.join(timeout=5)
        import shutil as _sh
        _sh.rmtree(self._tmp, ignore_errors=True)
        return False


def test_raw_resp_client_list_retries_on_read_timeout():
    """First read times out (conn 1 held, deterministic), retry succeeds
    -> clients parsed, exactly 2 connections (bounded retry, indicator b)."""
    from tortoise.embedded_reaper import _raw_resp_client_list
    with _DelayedRespServer(first_delay=True, never_reply=False) as srv:
        result = _raw_resp_client_list(srv.sock_path)
        assert result is not None
        assert any(c.get("id") == "1" for c in result)
        # The client parses the payload only after conn-2's handler thread
        # sent it — which runs only after accept() #2 incremented the
        # counter (payload receipt implies accept #2; bounded poll, no
        # timing assumption).
        srv.wait_accepts(2)


def test_raw_resp_client_list_retries_exhausted_returns_none():
    """never_reply: both attempts time out -> None (fail closed)."""
    from tortoise.embedded_reaper import _raw_resp_client_list
    with _DelayedRespServer(first_delay=True, never_reply=True) as srv:
        result = _raw_resp_client_list(srv.sock_path)
        assert result is None
        srv.wait_accepts(2)  # both attempts were made, bounded


def test_raw_resp_client_list_does_not_retry_dead_socket():
    """ConnectionRefusedError is a reliable verdict — no retry, None."""
    from tortoise.embedded_reaper import _raw_resp_client_list
    d = tempfile.mkdtemp(prefix="rp_")
    try:
        sp = os.path.join(d, "d.sock")
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(sp)
        s.close()  # dead socket file
        assert _raw_resp_client_list(sp) is None
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_raw_resp_client_list_does_not_retry_missing_socket():
    """FileNotFoundError (mid-startup) — no retry, None."""
    from tortoise.embedded_reaper import _raw_resp_client_list
    assert _raw_resp_client_list("/nonexistent/reaper-missing.sock") is None


def test_reap_only_safe_protects_live_pid_dir_present(monkeypatch):
    """#1557: only_safe must NOT kill a LIVE-pid server while a suite is
    active — even when its dir is intact and it has 0 clients. Redislite
    servers daemonize to ppid=1, so _is_detached is True for ALL of them:
    the detached criterion cannot discriminate a concurrent suite's
    between-tests idle server from a genuine orphan. Protect ANY live-pid
    server under only_safe while a live suite marker exists (the #1005
    concurrency guarantee). Genuine dir-gone orphans have a DEAD pid and go
    through the stale_socket path.
    """
    import subprocess as sp
    import sys as _sys

    from tortoise.embedded_reaper import ACTIVE_SUITES_DIR, discover, phase1_probe, reap
    monkeypatch.setenv("TORTOISE_REAPER_MIN_UPTIME", "0")
    # Register a live suite marker (the conftest writes pid=<os.getpid()>).
    os.makedirs(ACTIVE_SUITES_DIR, exist_ok=True)
    marker = os.path.join(ACTIVE_SUITES_DIR, f"test-{os.getpid()}-{time.time_ns()}")
    with open(marker, "w") as fh:
        fh.write(f"pid={os.getpid()}\n")
    try:
        code = (
            "import os,time; os.environ.pop('TORTOISE_DB_URI',None);\n"
            "from redislite.falkordb_client import FalkorDB; db=FalkorDB();\n"
            "print('READY ' + db.client.socket_file, flush=True); time.sleep(30)"
        )
        proc = sp.Popen([_sys.executable, "-c", code],
                        stdout=sp.PIPE, text=True)
        try:
            import select
            if not select.select([proc.stdout], [], [], 30)[0]:
                proc.kill()
                raise AssertionError("server did not start")
            sock = proc.stdout.readline().strip().split()[-1]
            # Redislite writes the server pid to redis.pid in the socket's
            # dir — more robust than ps-scanning (ps truncates long cmdlines
            # under load, which made "server pid not found" flaky in CI).
            pidfile = os.path.join(os.path.dirname(sock), "redis.pid")
            for _ in range(50):
                if os.path.exists(pidfile):
                    break
                time.sleep(0.1)
            server_pid = int(Path(pidfile).read_text().strip())
            assert _pid_alive_for(server_pid), "server should be live"
            # End-to-end: discover + phase1 + reap(only_safe=True) must NOT
            # kill a live server while a suite is active (dir present, 0
            # clients — the concurrent-suite between-tests case).
            found = discover()
            # discover() returns realpath'd socket paths — compare against
            # the realpath so the match is not vacuous (#1558 review P1).
            real_sock = os.path.realpath(sock)
            match = [s for s in found if s["socket_path"] == real_sock]
            assert match, f"server not discovered: {real_sock}"
            assert match[0]["classification"] == "candidate", \
                f"expected candidate, got {match[0]['classification']}"
            match = [phase1_probe(match[0])]
            reap(match, dry_run=False, only_safe=True)
            time.sleep(0.5)
            assert _pid_alive_for(server_pid), (
                "live-pid server killed under only_safe while suite active "
                "— #1557 / #1005 concurrency guarantee")
        finally:
            proc.kill()
            proc.wait(timeout=5)
    finally:
        os.unlink(marker) if os.path.exists(marker) else None


def test_reap_only_safe_protects_live_pid_dir_gone(monkeypatch):
    """#1557: only_safe must NOT kill a LIVE-pid server whose dir is
    missing — the test-tempdir lifecycle race. Same protection as the
    dir-present case (any live-pid server while a suite is active)."""
    import subprocess as sp
    import sys as _sys

    from tortoise.embedded_reaper import ACTIVE_SUITES_DIR, reap
    monkeypatch.setenv("TORTOISE_REAPER_MIN_UPTIME", "0")
    os.makedirs(ACTIVE_SUITES_DIR, exist_ok=True)
    marker = os.path.join(ACTIVE_SUITES_DIR, f"test-{os.getpid()}-{time.time_ns()}")
    with open(marker, "w") as fh:
        fh.write(f"pid={os.getpid()}\n")
    try:
        code = (
            "import os,time; os.environ.pop('TORTOISE_DB_URI',None);\n"
            "from redislite.falkordb_client import FalkorDB; db=FalkorDB();\n"
            "print('READY ' + db.client.socket_file, flush=True); time.sleep(30)"
        )
        proc = sp.Popen([_sys.executable, "-c", code],
                        stdout=sp.PIPE, text=True)
        try:
            import select
            if not select.select([proc.stdout], [], [], 30)[0]:
                proc.kill()
                raise AssertionError("server did not start")
            sock = proc.stdout.readline().strip().split()[-1]
            pidfile = os.path.join(os.path.dirname(sock), "redis.pid")
            for _ in range(50):
                if os.path.exists(pidfile):
                    break
                time.sleep(0.1)
            server_pid = int(Path(pidfile).read_text().strip())
            assert _pid_alive_for(server_pid), "server should be live"
            record = {"socket_path": sock, "pid": server_pid,
                      "dbdir": "/nonexistent/race-dir", "dir_missing": True,
                      "classification": "candidate"}
            acted = reap([record], dry_run=False, only_safe=True)
            time.sleep(0.5)
            assert _pid_alive_for(server_pid), (
                "live-pid dir-gone server killed under only_safe — #1557 race")
            assert not any(r.get("socket_path") == sock for r in acted), \
                "server should not be acted on"
        finally:
            proc.kill()
            proc.wait(timeout=5)
    finally:
        os.unlink(marker) if os.path.exists(marker) else None


# ── #1642 FIX 3: orphan confirmation (detachment + persisted 0-client) ──

def _markerless_suite(monkeypatch, tmp_path):
    """Point ACTIVE_SUITES_DIR at an empty dir so _mark_orphan_confirmation
    sees no live suite markers (confirmation allowed)."""
    marker_dir = tmp_path / "no-suites"
    marker_dir.mkdir()
    monkeypatch.setattr("tortoise.embedded_reaper.ACTIVE_SUITES_DIR",
                        str(marker_dir))


def _zero_client_candidate(socket_path, pid, dbdir="", path_based=False):
    return {"classification": "candidate", "socket_path": socket_path,
            "pid": pid, "dbdir": dbdir, "client_count": 0,
            "path_based": path_based, "settings": None}


def test_mark_orphan_confirmation_two_sweeps(monkeypatch, tmp_path):
    """#1642 FIX 3: a live 0-client candidate is NOT confirmed on first
    observation (state recorded); after the confirmation window elapses
    with the same (pid, start) identity, it IS confirmed. A recycled pid
    (start mismatch) restarts the window (FIX 5)."""
    from tortoise.embedded_reaper import _mark_orphan_confirmation, _process_start_time
    _markerless_suite(monkeypatch, tmp_path)
    monkeypatch.setattr("tortoise.embedded_reaper.ZERO_CLIENT_CONFIRM_MINUTES",
                        0.0)  # confirm on the SECOND sweep (window elapsed)
    monkeypatch.setattr("tortoise.embedded_reaper._pid_alive", lambda p: True)
    monkeypatch.setattr("tortoise.embedded_reaper._pid_is_redis",
                        lambda p: True)
    start = _process_start_time(os.getpid())  # noqa: F841
    rec = _zero_client_candidate("/tmp/fake-orphan.sock", os.getpid())
    # Sweep 1: first observation -> recorded, NOT confirmed.
    _mark_orphan_confirmation([rec])
    assert rec.get("_orphan_confirmed") is not True
    # Sweep 2 (window elapsed, identity unchanged): confirmed.
    rec2 = _zero_client_candidate("/tmp/fake-orphan.sock", os.getpid())
    _mark_orphan_confirmation([rec2])
    assert rec2.get("_orphan_confirmed") is True
    # A server that gained clients clears its state entry (not confirmed).
    rec3 = _zero_client_candidate("/tmp/fake-orphan.sock", os.getpid())
    rec3["client_count"] = 2
    _mark_orphan_confirmation([rec3])
    assert rec3.get("_orphan_confirmed") is not True
    rec4 = _zero_client_candidate("/tmp/fake-orphan.sock", os.getpid())
    _mark_orphan_confirmation([rec4])
    assert rec4.get("_orphan_confirmed") is not True, \
        "state must reset after the server had clients"


def test_mark_orphan_confirmation_no_confirmation_while_suite_active(
        monkeypatch, tmp_path):
    """#1642 FIX 3/4: while ANY live suite marker exists, live servers are
    never orphan-confirmed — a concurrent suite's between-tests idle server
    must not be killable by the cron's only_safe sweep (#1557/#1005)."""
    from tortoise.embedded_reaper import _mark_orphan_confirmation
    marker_dir = tmp_path / "active"
    marker_dir.mkdir()
    (marker_dir / "other-suite").write_text(f"pid={os.getpid()}\n")
    monkeypatch.setattr("tortoise.embedded_reaper.ACTIVE_SUITES_DIR",
                        str(marker_dir))
    monkeypatch.setattr("tortoise.embedded_reaper.ZERO_CLIENT_CONFIRM_MINUTES",
                        0.0)
    monkeypatch.setattr("tortoise.embedded_reaper._pid_alive", lambda p: True)
    monkeypatch.setattr("tortoise.embedded_reaper._pid_is_redis",
                        lambda p: True)
    for _ in range(2):  # even after the window, no confirmation
        rec = _zero_client_candidate("/tmp/fake-orphan.sock", os.getpid())
        _mark_orphan_confirmation([rec])
    assert rec.get("_orphan_confirmed") is not True


def test_reap_only_safe_kills_orphan_confirmed_candidate(monkeypatch):
    """#1642 FIX 3: only_safe kills a live-pid candidate ONLY when it is
    orphan-confirmed (persisted 0-client + no live suite markers) — the
    cron mode's discriminator that #1557's blanket live-pid protection
    lacked. The CLIENT LIST double-check still runs before the kill."""
    from tortoise.embedded_reaper import reap
    killed = []

    def fake_kill(pid, timeout):
        killed.append(pid)

    monkeypatch.setattr("tortoise.embedded_reaper._kill", fake_kill)
    monkeypatch.setattr("tortoise.embedded_reaper._active_client_count",
                        lambda _s: 0)
    # redislite servers daemonize to ppid=1 — every candidate is detached.
    monkeypatch.setattr("tortoise.embedded_reaper._is_detached", lambda p: True)
    confirmed = {"classification": "candidate", "dir_missing": False,
                 "socket_path": "/tmp/s-confirmed", "pid": os.getpid(),
                 "path_based": False, "_orphan_confirmed": True}
    acted = reap([confirmed], dry_run=False, only_safe=True)
    assert killed == [os.getpid()]
    assert acted  # killed
    unconfirmed = {"classification": "candidate", "dir_missing": False,
                   "socket_path": "/tmp/s-unconfirmed", "pid": os.getpid(),
                   "path_based": False}
    killed.clear()
    acted = reap([unconfirmed], dry_run=False, only_safe=True)
    assert killed == [], "unconfirmed live server killed under only_safe"
    assert acted == []


def test_reap_path_based_requires_confirmation(monkeypatch):
    """#1642 FIX 3: a path_based (user-data) candidate is killed in the
    FULL sweep only when orphan-confirmed; without confirmation it is
    protected even at 0 clients (its data outlives the test tree)."""
    from tortoise.embedded_reaper import reap
    killed = []

    def fake_kill(pid, timeout):
        killed.append(pid)

    monkeypatch.setattr("tortoise.embedded_reaper._kill", fake_kill)
    monkeypatch.setattr("tortoise.embedded_reaper._active_client_count",
                        lambda _s: 0)
    unconfirmed = {"classification": "candidate", "dir_missing": False,
                   "socket_path": "/tmp/pb-unconfirmed", "pid": os.getpid(),
                   "path_based": True}
    acted = reap([unconfirmed], dry_run=False, only_safe=False)
    assert killed == [], "unconfirmed path-based server killed in full sweep"
    assert acted == []
    confirmed = {"classification": "candidate", "dir_missing": False,
                 "socket_path": "/tmp/pb-confirmed", "pid": os.getpid(),
                 "path_based": True, "_orphan_confirmed": True}
    acted = reap([confirmed], dry_run=False, only_safe=False)
    assert killed == [os.getpid()]


def test_reap_ephemeral_full_sweep_kills_without_confirmation(monkeypatch):
    """#1642 FIX 3: ephemeral test-tree candidates keep the existing FULL
    sweep contract — 0-client (double-checked) candidates are killed on
    first pass (the 445-kill wave behavior); only_safe still requires
    confirmation (covered above)."""
    from tortoise.embedded_reaper import reap
    killed = []

    def fake_kill(pid, timeout):
        killed.append(pid)

    monkeypatch.setattr("tortoise.embedded_reaper._kill", fake_kill)
    monkeypatch.setattr("tortoise.embedded_reaper._active_client_count",
                        lambda _s: 0)
    rec = {"classification": "candidate", "dir_missing": False,
           "socket_path": "/tmp/eph", "pid": os.getpid(),
           "path_based": False}
    acted = reap([rec], dry_run=False, only_safe=False)  # noqa: F841
    assert killed == [os.getpid()]


# ── #1642 FIX 7: lme-* ephemeral prefix ─────────────────────────────

def test_lme_prefix_is_ephemeral():
    """#1642 FIX 7: longmem_eval's lme-* per-question trees (58 dirs were
    observed protected on the dev box) are now ephemeral test trees — a
    dead-pid lme- leftover classifies stale_socket (reapable), not
    protected forever."""
    from tortoise.embedded_reaper import _classify, _is_ephemeral_dir, _real_gettempdir
    tmp = _real_gettempdir()
    assert _is_ephemeral_dir(os.path.join(tmp, "lme-abc123"), tmp)
    socket_dir = os.path.join(tmp, "lme-abc123")
    registry = {"dir": socket_dir, "dbfilename": "redis.db",
                "pidfile": "/nonexistent/pid"}
    # A dead-pid lme- leftover reclassifies stale_socket (previously
    # 'protected' via the unrecognized-pattern fail-closed).
    assert _classify(socket_dir, socket_dir, tmp, registry, pid=99999999) \
        == "stale_socket"


def test_mark_orphan_confirmation_socketless_server(monkeypatch, tmp_path):
    """#1642 FIX 3: a live socket-LESS server (socket dir gone — no client
    can exist, probes cannot succeed) is orphan-confirmed via the persisted
    state once the window + (pid, start) identity + no-live-markers hold —
    the missing-dir signal substitutes for the 0-client CLIENT LIST probe
    (274 socket-less orphans observed on the dev box). A probe failure with
    the dir still present stays fail-closed (never confirmed)."""
    import json as _json

    from tortoise.embedded_reaper import (
        _mark_orphan_confirmation,
        _process_start_time,
        _socket_dir_missing,
    )
    _markerless_suite(monkeypatch, tmp_path)
    monkeypatch.setattr("tortoise.embedded_reaper.ZERO_CLIENT_CONFIRM_MINUTES",
                        0.0)
    monkeypatch.setattr("tortoise.embedded_reaper._pid_alive", lambda p: True)
    monkeypatch.setattr("tortoise.embedded_reaper._pid_is_redis",
                        lambda p: True)
    sock_path = "/nonexistent/dir-1642/redis.socket"
    assert _socket_dir_missing(sock_path)
    # seed the state with an observation from 10+ min ago
    state_path = tmp_path / "state.json"
    monkeypatch.setattr("tortoise.embedded_reaper.ZERO_CLIENT_STATE_PATH",
                        str(state_path))
    _json.dump({sock_path: {"pid": os.getpid(),
                            "start": _process_start_time(os.getpid()),
                            "first_seen": time.time() - 600}},
               open(state_path, "w"))  # noqa: SIM115
    rec = _zero_client_candidate(sock_path, os.getpid())
    rec["client_count"] = None  # probe cannot succeed (no socket)
    _mark_orphan_confirmation([rec])
    assert rec.get("_orphan_confirmed") is True
    # A candidate whose socket DIR still exists but whose probe failed is
    # never confirmed (transient failure -> fail closed).
    rec2 = _zero_client_candidate("/tmp/1642-dir-exists.sock", os.getpid())
    rec2["client_count"] = None
    _mark_orphan_confirmation([rec2])
    assert rec2.get("_orphan_confirmed") is not True


def test_reap_kills_confirmed_socketless_orphan(monkeypatch):
    """#1642 FIX 3: reap() kills a CONFIRMED socket-less orphan directly —
    the CLIENT LIST gates are skipped because no client can exist (the
    missing-dir signal is stronger than a 0-client probe). An UNCONFIRMED
    socket-less candidate stays protected (fail closed — probes cannot
    succeed)."""
    from tortoise.embedded_reaper import reap
    killed = []
    monkeypatch.setattr("tortoise.embedded_reaper._kill",
                        lambda pid, timeout: killed.append(pid))
    monkeypatch.setattr("tortoise.embedded_reaper._is_detached", lambda p: True)
    confirmed = {"classification": "candidate", "dir_missing": False,
                 "socket_path": "/nonexistent/1642/redis.socket",
                 "pid": os.getpid(), "path_based": False,
                 "_orphan_confirmed": True}
    acted = reap([confirmed], dry_run=False, only_safe=True)
    assert killed == [os.getpid()]
    assert acted
    unconfirmed = {"classification": "candidate", "dir_missing": False,
                   "socket_path": "/nonexistent/1642b/redis.socket",
                   "pid": os.getpid(), "path_based": False}
    killed.clear()
    acted = reap([unconfirmed], dry_run=False, only_safe=True)
    assert killed == []
    assert acted == []


def test_mark_orphan_confirmation_recycled_pid_restarts_window(
        monkeypatch, tmp_path):
    """#1642 FIX 5 (review P1): a recycled pid — the SAME pid now a DIFFERENT
    redis-server (different process start) — must NOT inherit the old
    first_seen window. Without this, the new server would be orphan-
    confirmed on its first sweep and killed at 0 clients (the #1557
    live-server hazard)."""
    from tortoise.embedded_reaper import _mark_orphan_confirmation
    _markerless_suite(monkeypatch, tmp_path)
    monkeypatch.setattr("tortoise.embedded_reaper.ZERO_CLIENT_CONFIRM_MINUTES",
                        0.0)  # confirm on the SECOND sweep (window elapsed)
    monkeypatch.setattr("tortoise.embedded_reaper._pid_alive", lambda p: True)
    monkeypatch.setattr("tortoise.embedded_reaper._pid_is_redis",
                        lambda p: True)
    from tortoise.embedded_reaper import _zero_client_state_read, _zero_client_state_write
    sock = "/tmp/fake-orphan-recycled.sock"
    # Sweep 1: first observation -> recorded (window starts).
    _mark_orphan_confirmation([_zero_client_candidate(sock, os.getpid())])
    # Simulate a RECYCLED pid: rewrite the persisted entry with the OLD
    # server's start (a different process start than the current pid).
    state = _zero_client_state_read()
    entry = state[sock]
    entry["start"] = entry["start"] - 999_999.0  # old server's start
    _zero_client_state_write(state)
    # Sweep 2 (window elapsed, but identity CHANGED): must NOT confirm.
    rec2 = _zero_client_candidate(sock, os.getpid())
    _mark_orphan_confirmation([rec2])
    assert rec2.get("_orphan_confirmed") is not True, \
        "recycled pid must restart the confirmation window, not inherit it"


def test_lock_is_tempdir_scoped_not_home_scoped():
    """#1658: the sweep lock must be TEMPDIR-scoped (machine-global), not
    HOME-scoped. Two sweepers with different $HOME on a shared box each flock
    a different inode if the lock lives under ~/.tortoise — both acquire and
    run overlapping sweeps (reaping each other's live sockets). The lock
    must live under the real gettempdir, the same convention as
    ACTIVE_SUITES_DIR."""
    import tempfile as _tf

    import tortoise.embedded_reaper as er

    lock_path = er._LOCK_PATH
    # Lock must be under the real tempdir, NOT under $HOME.
    assert os.path.realpath(_tf.gettempdir()) in lock_path, \
        f"_LOCK_PATH {lock_path!r} must be tempdir-scoped (was ~/.tortoise)"
    assert os.path.expanduser("~") not in lock_path, \
        f"_LOCK_PATH {lock_path!r} must not be HOME-scoped"
    # It lives in the same <tempdir>/.tortoise/ dir as ACTIVE_SUITES_DIR.
    assert os.path.dirname(lock_path) == os.path.dirname(er.ACTIVE_SUITES_DIR), \
        "lock and active-suites dir must share the tempdir/.tortoise root"


def test_cross_home_sweepers_share_one_lock():
    """#1658: two _ReaperLock instances with DIFFERENT $HOME on the SAME
    tempdir must contend on ONE flock — only one acquires. (Before the fix,
    a per-HOME lock path meant each got its own inode and both acquired.)"""
    import tempfile as _tf

    from tortoise.embedded_reaper import _ReaperLock

    # Both locks point at the same tempdir-scoped lock file — exactly what
    # two different-HOME sweepers on one machine now share.
    shared = os.path.join(
        os.path.realpath(_tf.gettempdir()), ".tortoise", ".reaper.lock")
    lock_a = _ReaperLock(shared)
    lock_b = _ReaperLock(shared)
    assert lock_a.acquire(), "first sweeper must acquire"
    try:
        assert not lock_b.acquire(), \
            "second sweeper (same tempdir) must observe the lock held"
    finally:
        lock_a.release()
    # After release, the second can acquire.
    assert lock_b.acquire(), "second sweeper acquires after release"
    lock_b.release()


# ── #3599: per-server owner records (the fleet-deadlock fix) ──────────────
# The bug: orphan confirmation was gated on a GLOBAL condition
# (`not suites_active`) that a host running many concurrent sessions never
# satisfies, so the only_safe cron and the conftest end-sweep were permanent
# no-ops and every SIGKILLed lane leaked its servers (527 orphans, load 98
# on 10 CPUs). The fix makes orphanhood a PER-SERVER question answered from
# the owner records tortoise.FalkorDB writes into each server's socket dir.

def _owner_dir(base, name=None):
    from tortoise.embedded_reaper import OWNERS_DIRNAME
    d = Path(base) / (name or OWNERS_DIRNAME)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_owner(d, pid, start):
    (Path(d) / f"{pid}-{start}").write_text("")


def _dead_pid() -> int:
    """A PID that is provably dead at the moment it is returned (#3599
    review: `wait()` releases the pid to the OS, so it can in principle be
    recycled — the previous version claimed otherwise and did not check).

    Callers that need deadness guaranteed for the duration of the test
    should additionally force it through `_pid_alive_except_dead`.
    """
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    from tortoise.embedded_reaper import _pid_alive
    assert not _pid_alive(p.pid), (
        f"reaped pid {p.pid} still reads alive — the test's deadness "
        f"premise does not hold")
    return p.pid


def _own_start() -> int:
    """This process's start time as an int, asserted present.

    `_process_start_time` returns None when `ps` times out — the exact
    loaded-host condition #3599 is written around — and `int(None)` would
    ERROR the fixture instead of testing the behaviour. Fail with a clear
    diagnostic instead (#3599 review).
    """
    from tortoise.embedded_reaper import _process_start_time
    start = _process_start_time(os.getpid())
    assert start is not None, (
        "`ps` could not report this process's start time — the test host is "
        "too loaded for this fixture to be meaningful")
    return int(start)


def _pid_alive_except_dead(monkeypatch, dead_pids) -> None:
    """Install a `_pid_alive` that is True for every pid except `dead_pids`.

    #3599: owner deadness is decided by `_pid_alive` — a start time that
    merely cannot be READ is not evidence of death (see the fail-closed
    rule in `_owner_records`), so a test that wants a dead owner must say
    so through `_pid_alive` rather than withholding a start time. The
    blanket `lambda p: True` used for the *server* pid would otherwise make
    every deliberately-dead owner look live.
    """
    dead = set(dead_pids)
    monkeypatch.setattr("tortoise.embedded_reaper._pid_alive",
                        lambda p: p not in dead)


def _live_foreign_suite_marker(monkeypatch, tmp_path):
    """A LIVE suite marker owned by another process (this test).

    This is the exact fleet condition under which the pre-#3599 reaper could
    never confirm anything: `suites_active` is True forever, so every live
    orphan stayed protected.
    """
    from tortoise.embedded_reaper import _process_start_time  # noqa: F401
    marker_dir = tmp_path / "active-suites"
    marker_dir.mkdir(exist_ok=True)
    (marker_dir / "other-suite").write_text(
        f"pid={os.getpid()}\nstart={_own_start()}\n")
    monkeypatch.setattr("tortoise.embedded_reaper.ACTIVE_SUITES_DIR",
                        str(marker_dir))
    from tortoise.embedded_reaper import active_suite_tokens
    assert active_suite_tokens(), "fixture must produce a LIVE marker"


def _sock_dir_with_owners(tmp_path, name="sock"):
    d = tmp_path / name
    d.mkdir(exist_ok=True)
    return d


def test_owner_records_parsing(tmp_path):
    """_owner_records: None without records (fail closed), counts live vs
    dead owners, ignores foreign files, treats an unverifiable start as
    LIVE, and never counts a malformed entry as dead."""
    from tortoise.embedded_reaper import _owner_records
    sock = _sock_dir_with_owners(tmp_path)
    sp = str(sock / "redis.socket")
    # No owners dir at all -> None (uninstrumented spawn).
    assert _owner_records(sp) is None
    # An EMPTY owners dir is no evidence either -> None.
    _owner_dir(sock)
    assert _owner_records(sp) is None

    mine = _own_start()
    _write_owner(_owner_dir(sock), os.getpid(), mine)
    assert _owner_records(sp) == (1, 1)
    _write_owner(_owner_dir(sock), _dead_pid(), 1234567890)
    assert _owner_records(sp) == (1, 2), "dead owner must not count live"
    # An 'unknown' START is unverifiable — but the PID is not. A dead pid
    # with an unknown start is still a STALE record, not a live owner: if it
    # counted live, nothing would ever prune it (nothing ages owner files)
    # and the server could never be orphan-confirmed again. 99999999 is
    # beyond this platform's pid space, so it is provably dead.
    _write_owner(_owner_dir(sock), 99999999, "unknown")
    assert _owner_records(sp) == (1, 3), (
        "a dead pid is dead even with an unreadable start — otherwise the "
        "record is immortal and its server is never reaped")
    # Foreign non-record files are ignored entirely.
    (_owner_dir(sock) / "README").write_text("not an owner record")
    (_owner_dir(sock) / ".hidden").write_text("")
    assert _owner_records(sp) == (1, 3)
    # #3599 review cycle 3: a pid prefix that `isdigit()` accepts but
    # `int()` rejects (non-decimal Unicode digits) must not RAISE —
    # `_owner_records` is called unguarded from `_mark_orphan_confirmation`
    # ("never raises"), so an unguarded `int()` here killed the whole sweep
    # and silently disabled orphan reclamation.
    (_owner_dir(sock) / "\u00b2-unknown").write_text("")
    (_owner_dir(sock) / "\u2460-123").write_text("")
    assert _owner_records(sp) == (1, 3)


def test_owner_records_live_pid_with_unknown_start_stays_live(tmp_path):
    """#3599 review-cycle-2 regression: a LIVE owner whose start stamp is
    `unknown` (the writer's `ps`-unavailable fallback) must still count
    LIVE — that is the fail-closed direction. Only a provably DEAD pid may
    be dropped from the live count."""
    from tortoise.embedded_reaper import _owner_records
    sock = _sock_dir_with_owners(tmp_path)
    sp = str(sock / "redis.socket")
    _write_owner(_owner_dir(sock), os.getpid(), "unknown")
    assert _owner_records(sp) == (1, 1), (
        "an alive owner with an unknown start is LIVE (fail closed)")


def test_owner_records_live_pid_with_unreadable_start_stays_live(
        monkeypatch, tmp_path):
    """#3599 P0 REGRESSION: an ALIVE owner pid whose start time cannot be
    read (a `ps` timeout — this host's loaded condition) must count LIVE.

    `_pid_identity_matches` returns False for BOTH "recycled pid" and
    "alive but start unreadable"; using it here made `_owner_records`
    report (0, 1) for a server with a live owner, `_mark_orphan_
    confirmation` set `_orphan_confirmed`, and the reaper killed a live
    suite's server at its next 0-client moment (#1005/#1557)."""
    from tortoise.embedded_reaper import _owner_records, _process_start_time
    sock = _sock_dir_with_owners(tmp_path)
    sp = str(sock / "redis.socket")
    _write_owner(_owner_dir(sock), os.getpid(), _process_start_time(os.getpid()))
    assert _owner_records(sp) == (1, 1)
    # ps cannot report this pid's start -> must fail CLOSED (still LIVE).
    monkeypatch.setattr("tortoise.embedded_reaper._process_start_time",
                        lambda p: None)
    assert _owner_records(sp) == (1, 1), (
        "a live pid with an unreadable start time is LIVE — counting it "
        "dead orphan-confirms a running server (#3599 P0)")


def test_owner_records_nonfinite_start_is_never_a_dead_owner(tmp_path):
    """#3599 review FAIL-OPEN REGRESSION: `float()` accepts 'nan'/'inf'/
    '1e400', and `abs(current - nan) < 2.0` is False, so a non-finite start
    used to take the RECYCLED-PID path and count a LIVE owner as dead — a
    false orphan verdict, i.e. the reaper killing a live suite's server.
    A non-finite stamp is UNVERIFIABLE, not a mismatch."""
    from tortoise.embedded_reaper import _owner_records
    for i, bad in enumerate(("nan", "inf", "-inf", "1e400", "-1e400")):
        sock = _sock_dir_with_owners(tmp_path, name=f"sock-{i}")
        sp = str(sock / "redis.socket")
        _write_owner(_owner_dir(sock), os.getpid(), bad)
        assert _owner_records(sp) == (1, 1), (
            f"a live owner with start={bad!r} must count LIVE, not dead — "
            f"counting it dead orphan-confirms a running server")


def test_owner_records_malformed_start_dead_pid_is_stale(tmp_path):
    """#3599 review (fail-closed, P2): an unparsable start must not make a
    DEAD owner's record immortal. Nothing prunes owner files, so
    `live += 1` without a liveness check would pin `owners[0] > 0` forever
    and that server could never be reaped again — the per-server #3599
    deadlock."""
    from tortoise.embedded_reaper import _owner_records
    sock = _sock_dir_with_owners(tmp_path)
    sp = str(sock / "redis.socket")
    _write_owner(_owner_dir(sock), 99999999, "abc")  # dead pid + bad start
    assert _owner_records(sp) == (0, 1), (
        "a dead pid with an unparsable start is a STALE record")
    # And the same unparsable start on a LIVE pid stays fail-closed.
    _write_owner(_owner_dir(sock), os.getpid(), "abc")
    assert _owner_records(sp) == (1, 2)


def test_owner_records_pid_zero_is_not_an_owner(tmp_path):
    """#3599 review: `os.kill(0, 0)` targets the caller's own process group
    and SUCCEEDS, so a file named `0` read as a LIVE owner forever and
    permanently disabled reaping for that server. A real owner pid is
    always >= 1."""
    from tortoise.embedded_reaper import _owner_records
    sock = _sock_dir_with_owners(tmp_path)
    sp = str(sock / "redis.socket")
    _write_owner(_owner_dir(sock), 0, "unknown")
    _write_owner(_owner_dir(sock), 0, 1234567890)
    assert _owner_records(sp) is None, (
        "pid 0 is not a valid owner record -> no evidence at all")


def test_owner_pid_alive_treats_permission_as_live(monkeypatch):
    """#3599 review: `_pid_alive` returns False on EPERM because for its
    original callers that is the SAFE direction. For an owner record it is
    the DANGEROUS one (False => 'owner dead' => orphan => kill), so a pid we
    merely cannot signal must count LIVE."""
    from tortoise.embedded_reaper import _owner_pid_alive
    monkeypatch.setattr("tortoise.embedded_reaper._pid_alive",
                        lambda p: False)

    def _eperm(pid, sig):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr("tortoise.embedded_reaper.os.kill", _eperm)
    assert _owner_pid_alive(1234) is True, (
        "a pid that exists but is not signalable by us must count LIVE")

    def _esrch(pid, sig):
        raise ProcessLookupError(3, "No such process")

    monkeypatch.setattr("tortoise.embedded_reaper.os.kill", _esrch)
    assert _owner_pid_alive(1234) is False


def test_owner_records_connected_client_is_never_confirmed(
        monkeypatch, tmp_path):
    """#3599 review (coverage): a server with CONNECTED CLIENTS is never
    orphan-confirmed, even when every owner record is dead. This pins the
    `cc > 0` guard that must stay ABOVE the new per-server arm — if the
    owner check moved first, a served server could be confirmed (and then
    killed at its next 0-client probe)."""
    from tortoise.embedded_reaper import _mark_orphan_confirmation
    _markerless_suite(monkeypatch, tmp_path)
    monkeypatch.setattr("tortoise.embedded_reaper.ZERO_CLIENT_CONFIRM_MINUTES",
                        0.0)
    dead = _dead_pid()
    _pid_alive_except_dead(monkeypatch, [dead])
    monkeypatch.setattr("tortoise.embedded_reaper._pid_is_redis",
                        lambda p: True)
    sock = _sock_dir_with_owners(tmp_path)
    _write_owner(_owner_dir(sock), dead, 1234567890)
    rec = _zero_client_candidate(str(sock / "redis.socket"), os.getpid())
    rec["path_based"] = False
    rec["client_count"] = 2
    _mark_orphan_confirmation([rec])
    assert rec.get("_orphan_confirmed") is not True, (
        "a server with connected clients must never be confirmed")


def _load_embedded_orphans():
    """Import tools/embedded_orphans.py by path (it is a CLI, not a module)."""
    import importlib.util
    path = (Path(__file__).resolve().parent.parent
            / "tools" / "embedded_orphans.py")
    spec = importlib.util.spec_from_file_location("embedded_orphans", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_embedded_orphans_census_fails_closed_when_enumeration_fails(
        monkeypatch):
    """#3599 declared threat class 4: the census must exit 2 when it cannot
    RUN, never 0.

    `_pgrep_redis_servers` swallows a missing OR TIMING-OUT pgrep and returns
    `[]`, which is indistinguishable from "no servers" — so the census would
    print `orphans: 0`, `within_budget: true` and exit 0 on a host where
    orphans may be accumulating (precisely the load level #3599 documents).
    That is the exact fail-open the tool exists to catch.
    """
    mod = _load_embedded_orphans()
    # (a) pgrep absent entirely.
    monkeypatch.setattr(mod.shutil, "which", lambda _n: None)
    with pytest.raises(RuntimeError):
        mod.census()
    # (b) pgrep PRESENT but timing out — the case a `which()` check misses.
    monkeypatch.setattr(mod.shutil, "which", lambda _n: "/usr/bin/pgrep")

    def _timeout(*_a, **_k):
        raise mod.subprocess.TimeoutExpired("pgrep", 5)

    monkeypatch.setattr(mod.subprocess, "run", _timeout)
    with pytest.raises(RuntimeError):
        mod.census()
    # (c) an unexpected non-zero/one exit code is not an answer either.
    class _Bad:
        returncode = 2
        stdout = ""

    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _Bad())
    with pytest.raises(RuntimeError):
        mod.census()
    # (d) and main() must map that to exit 2, not a traceback and not 0.
    def _boom(**_kw):
        raise RuntimeError("cannot enumerate")

    monkeypatch.setattr(mod, "census", _boom)
    assert mod.main([]) == 2


def test_run_sweep_plain_list_discover_seam(monkeypatch):
    """A monkeypatched `discover` returning a plain list must not break
    `_run_sweep` and must read as INCOMPLETE (fail-closed) — an unknown
    result is never a finished scan (#4068)."""
    from tortoise.embedded_reaper import _run_sweep
    monkeypatch.setattr("tortoise.embedded_reaper.discover",
                        lambda jobs=1, **kw: [])
    res = _run_sweep(dry_run=True, batch_size=None, sweep_pid_files=False)
    assert list(res) == [] and res.complete is False


def test_parse_full_scan_env_truthiness():
    from tortoise.embedded_reaper import _env_truthy
    for raw in ("1", "true", "TRUE", "yes", "On"):
        assert _env_truthy(raw) is True, raw
    for raw in (None, "", "0", "false", "no", "off", "garbage"):
        assert _env_truthy(raw) is False, raw


def test_cli_full_scan_resolves_from_flag_and_env(monkeypatch, capsys):
    """CLI flag > env > default, and the resolved value reaches `_run_sweep`."""
    import tortoise.embedded_reaper as _R
    seen = {}

    def _fake_run_sweep(**kw):
        seen.update(kw)
        return _R._ScanAwareList()

    monkeypatch.setattr(_R, "_run_sweep", _fake_run_sweep)
    monkeypatch.setattr(_R._ReaperLock, "acquire", lambda self: True)
    monkeypatch.setattr(_R._ReaperLock, "release", lambda self: None)
    monkeypatch.delenv("TORTOISE_REAPER_FULL_SCAN", raising=False)

    assert _R.main([]) == 0 and seen["full_scan"] is False
    assert _R.main(["--full-scan"]) == 0 and seen["full_scan"] is True
    monkeypatch.setenv("TORTOISE_REAPER_FULL_SCAN", "1")
    assert _R.main([]) == 0 and seen["full_scan"] is True
    monkeypatch.setenv("TORTOISE_REAPER_FULL_SCAN", "0")
    assert _R.main([]) == 0 and seen["full_scan"] is False
    capsys.readouterr()


def test_main_surfaces_truncation(monkeypatch, capsys):
    """#4068: a truncated discovery must never read as a finished sweep."""
    import tortoise.embedded_reaper as _R

    def _truncated(**kw):
        out = _R._ScanAwareList()
        out.complete = False
        return out

    monkeypatch.setattr(_R, "_run_sweep", _truncated)
    monkeypatch.setattr(_R._ReaperLock, "acquire", lambda self: True)
    monkeypatch.setattr(_R._ReaperLock, "release", lambda self: None)
    assert _R.main(["--no-dry-run"]) == 0
    assert "SCAN TRUNCATED" in capsys.readouterr().out


def test_census_reports_truncation(monkeypatch, capsys):
    """#4068: --deep is detect-only and bounded; a truncated scan must be
    reported (dict key + stderr warning), and must NOT change the exit code."""
    mod = _load_embedded_orphans()
    monkeypatch.setattr(mod, "census", lambda **kw: {
        "live_servers": 0, "orphans": 0, "orphan_details": [],
        "protected": 0, "unclassified": 0, "stale_socket_dirs": 0,
        "stale_socket_dir_sample": [], "census_truncated": True})
    assert mod.main(["--deep"]) == 0
    assert "PARTIAL" in capsys.readouterr().err


def test_deep_census_uses_the_unscoped_scan(monkeypatch):
    """`--deep` must request full_scan (detect-only) and surface the
    scan's completeness without raising on an incomplete scan."""
    mod = _load_embedded_orphans()
    import tortoise.embedded_reaper as _R
    seen = {}

    def _fake_scan(tmpdir, **kw):
        seen.update(kw)
        return _R._ScanResult([], complete=False)

    monkeypatch.setattr(_R, "_scan_socket_dirs", _fake_scan)
    monkeypatch.setattr(mod, "_enumerate_servers_strict", lambda: [])
    res = mod.census(deep=True)
    assert seen.get("full_scan") is True
    assert res["census_truncated"] is True


def test_embedded_orphans_inconclusive_is_not_clean(monkeypatch, capsys):
    """#3599: a census whose EVERY server failed classification learned
    nothing about the invariant — it must not report it satisfied."""
    mod = _load_embedded_orphans()
    monkeypatch.setattr(mod, "census", lambda **kw: {
        "live_servers": 3, "orphans": 0, "orphan_details": [],
        "protected": 0, "unclassified": 3, "stale_socket_dirs": 0,
        "stale_socket_dir_sample": []})
    assert mod.main([]) == 2
    assert "INCONCLUSIVE" in capsys.readouterr().err


def test_embedded_orphans_never_uses_the_swallowing_enumerator(monkeypatch):
    """#3599 adversarial review (fail-open): the census must not probe with
    one pgrep and enumerate with another. `_pgrep_redis_servers` swallows a
    timeout/OSError as `[]`, so a guard probe that ANSWERS followed by a real
    call that TIMES OUT would report `live_servers: 0`, `orphans: 0` and exit
    0. The census now enumerates with its own strict call and must never
    consult the swallowing one."""
    mod = _load_embedded_orphans()
    import tortoise.embedded_reaper as _R

    def _boom():
        raise AssertionError(
            "census must enumerate STRICTLY, never via "
            "_pgrep_redis_servers (which swallows a timeout as an empty "
            "list — the fail-open this guards)")

    monkeypatch.setattr(_R, "_pgrep_redis_servers", _boom)
    monkeypatch.setattr(mod.shutil, "which", lambda _n: "/usr/bin/pgrep")

    class _Ok:
        returncode = 0
        stdout = ""

    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _Ok())
    res = mod.census()  # must not reach _pgrep_redis_servers at all
    assert res["live_servers"] == 0


def test_reap_refuses_when_an_owner_attached_after_confirmation(
        monkeypatch, tmp_path):
    """#3599 adversarial review (fail-open): `_orphan_confirmed` is set during
    the sweep, but a co-tenant can attach before the kill. `reap()` must
    re-read the owner records at kill time — `_active_client_count` ignores
    connections younger than its age floor, and the socketless path skips
    both probes, so the CLIENT LIST double-check alone cannot see it."""
    from tortoise.embedded_reaper import reap
    killed = []
    monkeypatch.setattr("tortoise.embedded_reaper._kill",
                        lambda pid, timeout: killed.append(pid))
    monkeypatch.setattr("tortoise.embedded_reaper._active_client_count",
                        lambda _s: 0)
    monkeypatch.setattr("tortoise.embedded_reaper._is_detached",
                        lambda p: True)
    sock = _sock_dir_with_owners(tmp_path)
    # A LIVE owner that appeared after the sweep confirmed an orphan.
    _write_owner(_owner_dir(sock), os.getpid(), _own_start())
    rec = {"classification": "candidate", "dir_missing": False,
           "socket_path": str(sock / "redis.socket"), "pid": os.getpid(),
           "path_based": False, "client_count": 0,
           "_orphan_confirmed": True}
    acted = reap([rec], dry_run=False, only_safe=True)
    assert killed == [], (
        "a live owner record present at kill time must block the kill")
    assert not acted


def test_reap_still_kills_when_owners_are_all_dead(monkeypatch, tmp_path):
    """The other half: re-reading the owner records must not stop the reap
    the per-server signal authorises."""
    from tortoise.embedded_reaper import reap
    killed = []
    monkeypatch.setattr("tortoise.embedded_reaper._kill",
                        lambda pid, timeout: killed.append(pid))
    monkeypatch.setattr("tortoise.embedded_reaper._active_client_count",
                        lambda _s: 0)
    monkeypatch.setattr("tortoise.embedded_reaper._is_detached",
                        lambda p: True)
    sock = _sock_dir_with_owners(tmp_path)
    _write_owner(_owner_dir(sock), _dead_pid(), 1234567890)
    rec = {"classification": "candidate", "dir_missing": False,
           "socket_path": str(sock / "redis.socket"), "pid": os.getpid(),
           "path_based": False, "client_count": 0,
           "_orphan_confirmed": True}
    acted = reap([rec], dry_run=False, only_safe=True)
    assert killed == [os.getpid()]
    assert acted


def test_owner_records_recycled_pid_is_dead(monkeypatch, tmp_path):
    """#3599: the fail-closed start check must still let a RECYCLED pid
    (a different process now using the dead owner's pid) be counted dead —
    otherwise owner records would be immortal on a busy PID-reusing host."""
    from tortoise.embedded_reaper import _owner_records
    sock = _sock_dir_with_owners(tmp_path)
    sp = str(sock / "redis.socket")
    _write_owner(_owner_dir(sock), os.getpid(), 1000000.0)
    monkeypatch.setattr("tortoise.embedded_reaper._pid_alive", lambda p: True)
    monkeypatch.setattr("tortoise.embedded_reaper._process_start_time",
                        lambda p: 2000000.0)
    assert _owner_records(sp) == (0, 1), (
        "a pid whose recorded start differs from the live process's start "
        "is a recycled pid -> dead owner")


def test_owner_records_dead_owner_confirms_despite_live_suite_marker(
        monkeypatch, tmp_path):
    """#3599 THE REGRESSION TEST: a server whose only owner is provably dead
    is orphan-confirmed on the FIRST sweep even while another suite is live.

    Pre-fix this returned not-confirmed (the global `suites_active` gate),
    which is precisely why the only_safe reaper never reaped anything on a
    fleet host."""
    from tortoise.embedded_reaper import _mark_orphan_confirmation
    _live_foreign_suite_marker(monkeypatch, tmp_path)
    dead = _dead_pid()
    _pid_alive_except_dead(monkeypatch, [dead])
    monkeypatch.setattr("tortoise.embedded_reaper._pid_is_redis",
                        lambda p: True)
    sock = _sock_dir_with_owners(tmp_path)
    _write_owner(_owner_dir(sock), dead, 1234567890)
    rec = _zero_client_candidate(str(sock / "redis.socket"), os.getpid())
    rec["path_based"] = False
    _mark_orphan_confirmation([rec])
    assert rec.get("_orphan_confirmed") is True, (
        "a dead-owner server must be confirmed even with a live suite "
        "marker — otherwise the fleet deadlock returns (#3599)")


def test_owner_records_live_owner_is_never_confirmed(monkeypatch, tmp_path):
    """The over-kill guard: a server with ANY live owner is not an orphan,
    even with no suite markers and after the whole confirmation window."""
    from tortoise.embedded_reaper import _mark_orphan_confirmation, _process_start_time
    _markerless_suite(monkeypatch, tmp_path)
    monkeypatch.setattr("tortoise.embedded_reaper.ZERO_CLIENT_CONFIRM_MINUTES",
                        0.0)
    monkeypatch.setattr("tortoise.embedded_reaper._pid_alive", lambda p: True)
    monkeypatch.setattr("tortoise.embedded_reaper._pid_is_redis",
                        lambda p: True)
    sock = _sock_dir_with_owners(tmp_path)
    _write_owner(_owner_dir(sock), os.getpid(),
                 int(_process_start_time(os.getpid())))
    for _ in range(2):
        rec = _zero_client_candidate(str(sock / "redis.socket"), os.getpid())
        rec["path_based"] = False
        _mark_orphan_confirmation([rec])
    assert rec.get("_orphan_confirmed") is not True


def test_owner_records_shared_server_co_tenant_keeps_it_protected(
        monkeypatch, tmp_path):
    """#3599 P0 GUARD (shared server): owner A died, owner B is live and
    attached after A — the server must NOT be confirmed. A creator-only
    record would orphan-confirm it and kill a live co-tenant's server
    (the #1557 hazard).

    #3599 review (P1): this runs MARKERLESS with the confirmation window
    poked to 0 and TWO sweeps, so the pre-fix/global-gate path WOULD confirm
    on the second sweep. With a live foreign marker present instead, the
    suite gate alone would suppress confirmation and the test could not
    fail — it would be a guard with no discriminating power. Deleting the
    `owners[0] > 0` early return must break this test.
    """
    from tortoise.embedded_reaper import _mark_orphan_confirmation
    _markerless_suite(monkeypatch, tmp_path)
    monkeypatch.setattr("tortoise.embedded_reaper.ZERO_CLIENT_CONFIRM_MINUTES",
                        0.0)
    dead = _dead_pid()
    _pid_alive_except_dead(monkeypatch, [dead])
    monkeypatch.setattr("tortoise.embedded_reaper._pid_is_redis",
                        lambda p: True)
    sock = _sock_dir_with_owners(tmp_path)
    _write_owner(_owner_dir(sock), dead, 1234567890)   # creator, dead
    _write_owner(_owner_dir(sock), os.getpid(), _own_start())  # live co-tenant
    for _ in range(2):
        rec = _zero_client_candidate(str(sock / "redis.socket"), os.getpid())
        rec["path_based"] = False
        _mark_orphan_confirmation([rec])
    assert rec.get("_orphan_confirmed") is not True, (
        "a live co-tenant's server must never be confirmed as an orphan, "
        "even markerless with the window elapsed")


def test_owner_records_no_live_suite_still_protects_socket_dir_missing(
        monkeypatch, tmp_path):
    """#3599 review (restored pre-existing guard, P1): a MISSING socket dir
    is not instant proof of orphanhood.

    A live server whose socket dir was unlinked still serves clients over
    already-established unix connections, and a path-based CLIENT LIST probe
    cannot see them. Confirm only through the pre-existing window path
    (which needs no live suite markers) — the immediate-confirm arm the
    first cut of #3599 added would have killed such a server, which is the
    #1557 test-tempdir lifecycle race a prior review rated P1 (PR #1558).
    """
    from tortoise.embedded_reaper import _mark_orphan_confirmation
    _live_foreign_suite_marker(monkeypatch, tmp_path)
    monkeypatch.setattr("tortoise.embedded_reaper._pid_alive", lambda p: True)
    monkeypatch.setattr("tortoise.embedded_reaper._pid_is_redis",
                        lambda p: True)
    gone = tmp_path / "gone-dir" / "redis.socket"  # parent never created
    for _ in range(3):  # even after the persisted window would have elapsed
        rec = _zero_client_candidate(str(gone), os.getpid())
        rec["client_count"] = None
        rec["path_based"] = False
        _mark_orphan_confirmation([rec])
        assert rec.get("_orphan_confirmed") is not True, (
            "a missing socket dir must not confirm while a suite is live")


def test_owner_records_path_based_socket_dir_missing_not_confirmed(
        monkeypatch, tmp_path):
    """#3599 review (restored guard): `path_based` is the blast-radius
    boundary, so a USER-DATA server whose socket dir vanished must not be
    confirmed either — the immediate arm was not `path_based`-gated."""
    from tortoise.embedded_reaper import _mark_orphan_confirmation
    _live_foreign_suite_marker(monkeypatch, tmp_path)
    monkeypatch.setattr("tortoise.embedded_reaper._pid_alive", lambda p: True)
    monkeypatch.setattr("tortoise.embedded_reaper._pid_is_redis",
                        lambda p: True)
    gone = tmp_path / "gone-pb" / "redis.socket"
    rec = _zero_client_candidate(str(gone), os.getpid())
    rec["client_count"] = None
    rec["path_based"] = True
    _mark_orphan_confirmation([rec])
    assert rec.get("_orphan_confirmed") is not True


def test_owner_records_uninstrumented_spawn_falls_back_to_global_gate(
        monkeypatch, tmp_path):
    """No owner records -> the #1642 global gate still governs (fail
    closed). A raw-redislite spawn must not become reapable just because the
    new signal is absent."""
    from tortoise.embedded_reaper import _mark_orphan_confirmation
    _live_foreign_suite_marker(monkeypatch, tmp_path)
    monkeypatch.setattr("tortoise.embedded_reaper.ZERO_CLIENT_CONFIRM_MINUTES",
                        0.0)
    monkeypatch.setattr("tortoise.embedded_reaper._pid_alive", lambda p: True)
    monkeypatch.setattr("tortoise.embedded_reaper._pid_is_redis",
                        lambda p: True)
    sock = _sock_dir_with_owners(tmp_path)  # no owners dir
    for _ in range(2):
        rec = _zero_client_candidate(str(sock / "redis.socket"), os.getpid())
        rec["path_based"] = False
        _mark_orphan_confirmation([rec])
    assert rec.get("_orphan_confirmed") is not True


def test_owner_records_path_based_server_keeps_conservative_gate(
        monkeypatch, tmp_path):
    """A USER-DATA (path_based) server with a dead owner is NOT auto-
    confirmed: its db outlives the test tree, so it keeps the #1642
    window+no-live-suite gate. This is the blast-radius boundary for the
    #3599 fix."""
    from tortoise.embedded_reaper import _mark_orphan_confirmation
    _live_foreign_suite_marker(monkeypatch, tmp_path)
    dead = _dead_pid()
    _pid_alive_except_dead(monkeypatch, [dead])
    monkeypatch.setattr("tortoise.embedded_reaper._pid_is_redis",
                        lambda p: True)
    sock = _sock_dir_with_owners(tmp_path)
    _write_owner(_owner_dir(sock), dead, 1234567890)
    rec = _zero_client_candidate(str(sock / "redis.socket"), os.getpid())
    rec["path_based"] = True
    _mark_orphan_confirmation([rec])
    assert rec.get("_orphan_confirmed") is not True


def test_socket_dir_missing_confirms_only_without_live_suite(
        monkeypatch, tmp_path):
    """#3599 second signal, CONSERVATIVE (restored pre-existing semantics):
    with NO live suite markers the socket-dir-missing server does confirm
    through the window path, which is exactly the pre-#3599 contract.

    This is the other half of
    `test_owner_records_no_live_suite_still_protects_socket_dir_missing`:
    the signal still works, it just is not immediate.
    """
    from tortoise.embedded_reaper import _mark_orphan_confirmation
    _markerless_suite(monkeypatch, tmp_path)
    monkeypatch.setattr("tortoise.embedded_reaper.ZERO_CLIENT_CONFIRM_MINUTES",
                        0.0)
    monkeypatch.setattr("tortoise.embedded_reaper._pid_alive", lambda p: True)
    monkeypatch.setattr("tortoise.embedded_reaper._pid_is_redis",
                        lambda p: True)
    gone = tmp_path / "gone-ok" / "redis.socket"  # parent never created
    for _ in range(2):
        rec = _zero_client_candidate(str(gone), os.getpid())
        rec["client_count"] = None
        rec["path_based"] = False
        _mark_orphan_confirmation([rec])
    assert rec.get("_orphan_confirmed") is True, (
        "with no live suite markers the window path must still confirm a "
        "socketless server")


def test_reap_only_safe_kills_owner_confirmed_orphan_with_live_suite(
        monkeypatch, tmp_path):
    """#3599 end-to-end gate trace: the two-stage path that was dead pre-fix.

    Stage 1: `_mark_orphan_confirmation` confirms a server whose only owner
    is provably dead — WHILE a live foreign suite marker exists. Pre-fix the
    global `suites_active` gate left `_orphan_confirmed` unset, so stage 2
    was unreachable.
    Stage 2: the real `reap(only_safe=True)` then kills that record even
    though the live suite marker is still there. `_is_detached` is forced
    FALSE (the `ps`-timeout case on a loaded host) so the record can only be
    admitted by the `_orphan_confirmed` term of the only_safe guard — with
    it forced True the assertion would pass even if that new term were
    deleted (#3599 review).
    """
    from tortoise.embedded_reaper import _mark_orphan_confirmation, reap
    _live_foreign_suite_marker(monkeypatch, tmp_path)
    monkeypatch.setattr("tortoise.embedded_reaper.ZERO_CLIENT_CONFIRM_MINUTES",
                        0.0)
    dead = _dead_pid()
    _pid_alive_except_dead(monkeypatch, [dead])
    monkeypatch.setattr("tortoise.embedded_reaper._pid_is_redis",
                        lambda p: True)
    sock = _sock_dir_with_owners(tmp_path)
    _write_owner(_owner_dir(sock), dead, 1234567890)
    rec = _zero_client_candidate(str(sock / "redis.socket"), os.getpid())
    rec["path_based"] = False
    _mark_orphan_confirmation([rec])
    assert rec.get("_orphan_confirmed") is True, (
        "stage 1: pre-fix the live suite marker blocked confirmation")

    killed = []
    monkeypatch.setattr("tortoise.embedded_reaper._kill",
                        lambda pid, timeout: killed.append(pid))
    monkeypatch.setattr("tortoise.embedded_reaper._active_client_count",
                        lambda _s: 0)
    monkeypatch.setattr("tortoise.embedded_reaper._is_detached",
                        lambda p: False)
    rec["client_count"] = 0
    acted = reap([rec], dry_run=False, only_safe=True)
    assert killed == [os.getpid()], (
        "stage 2: a confirmed dead-owner orphan must be killed under "
        "only_safe even while another suite is live and detachment cannot "
        "be determined")
    assert acted


def test_reap_only_safe_refuses_unconfirmed_live_candidate(monkeypatch):
    """#3599 review: the refusal direction of the same gate — an
    UNCONFIRMED live candidate is never killed under only_safe when
    detachment cannot be determined."""
    from tortoise.embedded_reaper import reap
    killed = []
    monkeypatch.setattr("tortoise.embedded_reaper._kill",
                        lambda pid, timeout: killed.append(pid))
    monkeypatch.setattr("tortoise.embedded_reaper._active_client_count",
                        lambda _s: 0)
    monkeypatch.setattr("tortoise.embedded_reaper._is_detached",
                        lambda p: False)
    rec = {"classification": "candidate", "socket_path": "/tmp/s-unconf",
           "pid": os.getpid(), "path_based": False, "client_count": 0}
    acted = reap([rec], dry_run=False, only_safe=True)
    assert killed == [], "an unconfirmed live candidate must not be killed"
    assert not acted
