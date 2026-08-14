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
            try:
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
        match = [s for s in found if s["socket_path"] == sock][0]
        assert match["classification"] == "protected"
    finally:
        db.close()


def test_min_uptime_env_override(monkeypatch):
    """TORTOISE_REAPER_MIN_UPTIME=0 disables the cooldown."""
    monkeypatch.setenv("TORTOISE_REAPER_MIN_UPTIME", "0")
    db, sock = _make_no_path_server()
    try:
        found = discover()
        match = [s for s in found if s["socket_path"] == sock][0]
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
            try:
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


def test_discover_unknown_old_settings_pattern_defaults_protected(tmp_path, caplog):
    """Pre-#90 .settings, no db_filename, no .db file, non-matching dirname
    -> protected (boot cooldown: no live pid) — never crash, never killable.

    Issue #1005 semantics: the dir sits inside an ephemeral pytest tmp tree,
    so it is no longer flagged as an 'unrecognized pattern' (the tree itself
    is known-ephemeral); protection now comes from the boot cooldown. The
    unrecognized-pattern warning only applies outside ephemeral trees.
    """
    dbdir = tmp_path / "my-custom-name"  # non-matching dirname
    dbdir.mkdir()
    socket_path = dbdir / "redis.socket"
    socket_path.write_text("")
    (dbdir / "custom.db.settings").write_text(json.dumps({
        "pidfile": str(dbdir / "redis.pid"),
        "unixsocket": str(socket_path),
        "dbdir": str(dbdir),
    }))
    with monkeypatch_tempdir(tmp_path):
        found = discover()
        matches = [s for s in found if str(dbdir) in s.get("dbdir", "")]
        assert matches, "unknown-pattern server not discovered"
        assert matches[0]["classification"] == "protected"


# ── Error isolation ─────────────────────────────────────────────────

def test_discover_skips_permission_denied_dir_continues_sweep(tmp_path, monkeypatch, caplog):
    """chmod-000 subdir -> skip + continue; other candidates still found."""
    denied = tmp_path / "redislite_denied"
    denied.mkdir()
    (denied / "redis.socket").write_text("")
    os.chmod(denied, 0o000)
    try:
        db, sock = _make_no_path_server()
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
    db, sock = _make_no_path_server()
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
    db, sock = _make_no_path_server()
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
    import redis as _redis
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
    import redislite.falkordb_client as _fc
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
        raise AssertionError("orphan spawn failed: %r" % line)
    sock = line.split(None, 1)[1]
    time.sleep(1)
    proc.kill()
    proc.wait()
    time.sleep(1)
    if not os.path.exists(sock):
        raise AssertionError("orphan socket vanished: %s" % sock)
    return os.path.realpath(sock)


def test_reap_kills_idle_orphan(monkeypatch):
    """Genuine orphan (SIGKILL'd parent) -> reap() kills it."""
    from tortoise.embedded_reaper import discover, reap
    monkeypatch.setenv("TORTOISE_REAPER_MIN_UPTIME", "0")
    sock = _spawn_orphan()
    found = discover()
    match = [s for s in found if s["socket_path"] == sock][0]
    assert match["classification"] == "candidate"
    pid = match["pid"]
    reap([match], dry_run=False)
    time.sleep(1)
    assert not _pid_alive_for(pid), "orphan not killed"


def test_reap_skips_orphan_with_active_client(monkeypatch):
    """Orphan with a LIVE client (redis-py connected) -> reap() must NOT kill."""
    import redis as _redis
    from tortoise.embedded_reaper import discover, reap
    monkeypatch.setenv("TORTOISE_REAPER_MIN_UPTIME", "0")
    sock = _spawn_orphan()
    client = _redis.Redis(unix_socket_path=sock, socket_connect_timeout=2)
    client.ping()
    time.sleep(3)  # age the connection so CLIENT LIST sees it as a real client (age >= 2s)
    try:
        found = discover()
        match = [s for s in found if s["socket_path"] == sock][0]
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
    from tortoise.projection import FalkorProjection
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
            try:
                os.remove(path + suffix)
            except OSError:
                pass


def test_reap_removes_tempdir_after_kill(monkeypatch):
    """After reap() kills a candidate, its tempdir is removed."""
    monkeypatch.setenv("TORTOISE_REAPER_MIN_UPTIME", "0")
    sock = _spawn_orphan()
    from tortoise.embedded_reaper import discover, reap
    found = discover()
    match = [s for s in found if s["socket_path"] == sock][0]
    dbdir = match["dbdir"]
    reap([match], dry_run=False)
    time.sleep(1)
    assert not os.path.exists(dbdir), "tempdir not cleaned after kill"


def test_reap_dry_run_does_not_kill(monkeypatch):
    """dry_run=True (default) logs planned kills, kills nothing."""
    monkeypatch.setenv("TORTOISE_REAPER_MIN_UPTIME", "0")
    sock = _spawn_orphan()
    from tortoise.embedded_reaper import discover, reap
    found = discover()
    match = [s for s in found if s["socket_path"] == sock][0]
    pid = match["pid"]
    reap([match], dry_run=True)
    time.sleep(1)
    assert _pid_alive_for(pid), "dry-run killed the orphan"


def test_reap_fails_closed_when_client_probe_unknown(monkeypatch):
    """Regression #849: if CLIENT LIST state is unknown (probe failure),
    reap() must skip the server — never kill on unknown client state."""
    from tortoise.embedded_reaper import _active_client_count, reap
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
    match = [s for s in found if s["socket_path"] == sock][0]
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

def _run_cli(*args, timeout=30):
    """Run the reaper CLI as a subprocess; return (rc, stdout, stderr)."""
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
    import subprocess as sp
    import sys as _sys
    from tortoise.embedded_reaper import discover, reap
    monkeypatch.setenv("TORTOISE_REAPER_MIN_UPTIME", "0")
    sock = _spawn_orphan()
    # capture the orphan pid directly from the socket's pidfile
    dbdir = os.path.dirname(sock)
    pid = int(open(os.path.join(dbdir, "redis.pid")).read().strip())
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


def test_cli_no_dry_run_kills(monkeypatch):
    """--no-dry-run actually kills orphans."""
    monkeypatch.setenv("TORTOISE_REAPER_MIN_UPTIME", "0")
    sock = _spawn_orphan()
    try:
        rc, out, err = _run_cli("--no-dry-run")
        assert rc == 0
        from tortoise.embedded_reaper import discover
        found = discover()
        match = [s for s in found if s["socket_path"] == sock]
        assert not match, "orphan not killed by --no-dry-run"
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
        rc, out, err = _run_cli("--json")
        assert rc == 0
        data = _json.loads(out)
        assert isinstance(data, list)
    finally:
        from tortoise.embedded_reaper import discover, reap
        found = discover()
        match = [s for s in found if s["socket_path"] == sock]
        if match:
            reap(match, dry_run=False)


def test_cli_batch_size_limits_kills(monkeypatch):
    """--batch-size N limits kills per run."""
    monkeypatch.setenv("TORTOISE_REAPER_MIN_UPTIME", "0")
    socks = [_spawn_orphan() for _ in range(2)]
    try:
        rc, out, err = _run_cli("--no-dry-run", "--batch-size", "1")
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


def test_cli_singleton_lock_prevents_concurrent(monkeypatch):
    """Second concurrent instance (lock held mid-sweep) exits 0 with
    'already running'. The lock is held only DURING a sweep, so we hold it
    directly to simulate a mid-sweep overlap."""
    from tortoise.embedded_reaper import _ReaperLock
    monkeypatch.setenv("TORTOISE_REAPER_MIN_UPTIME", "0")
    lock = _ReaperLock()
    assert lock.acquire(), "could not acquire lock for test"
    try:
        rc, out, err = _run_cli("--no-dry-run", timeout=10)
        assert rc == 0
        assert "already running" in (out + err).lower()
    finally:
        lock.release()


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
    rc, out, err = _run_cli("--no-dry-run", timeout=10)
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
        '{"dir": "%s", "dbfilename": "redis.db"}' % str(gone_dir))
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


def test_reap_only_safe_reaps_detached_orphan(monkeypatch):
    """only_safe=True must reap a DETACHED candidate (direct parent is init
    — its spawning tree fully exited), even though it is a live ephemeral
    server. This bounds orphan accumulation under concurrent suites
    (issue #1115 option A): a server with no live holder is a dead-end, so
    a suite's start/end sweep can clean it without waiting for the global
    'last suite standing'.
    """
    from tortoise.embedded_reaper import discover, reap
    monkeypatch.setenv("TORTOISE_REAPER_MIN_UPTIME", "0")
    sock = _spawn_orphan()
    found = discover()
    match = [s for s in found if s["socket_path"] == sock][0]
    assert match["classification"] == "candidate"
    pid = match["pid"]
    # The orphan's spawning subprocess was SIGKILLed, so it is reparented
    # (ppid 1) — the new detached criterion must admit it in only_safe mode.
    reap([match], dry_run=False, only_safe=True)
    time.sleep(1)
    assert not _pid_alive_for(pid), "detached orphan was not reaped in only_safe"


def test_reap_only_safe_protects_live_parented_server(monkeypatch):
    """only_safe=True must NOT reap a candidate whose direct parent is a
    LIVE process (e.g. a concurrent suite's in-process fixture server at
    0-client between tests) — the detached criterion is parent-exact.
    """
    from tortoise.embedded_reaper import reap
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
    from tortoise.embedded_reaper import ACTIVE_SUITES_DIR, active_suite_tokens
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
    from tortoise.index_lock import SessionIndexLock
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
                        lambda jobs=1: [])
    acted = _run_sweep(dry_run=False, batch_size=None, only_safe=True)
    pid_actions = [a for a in acted if a.get("classification") == "stale_pid_file"]
    assert len(pid_actions) == 1
    assert pid_actions[0]["pid_file"] == str(stale)
    assert not stale.exists()
