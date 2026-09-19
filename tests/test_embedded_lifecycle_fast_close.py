"""Unit tests for the #1371 fast interpreter-exit close (ephemeral only).

Covers the gating (flag + ephemeral test-tree + last-client), the durability
contract (fire-and-forget SHUTDOWN SAVE persists the RDB — the cross-process
reopen classes depend on it), and the behavior-identical contract for
explicit close()/__exit__ (the close-recorder tests in this module).
"""
from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from tortoise import embedded_lifecycle
from tortoise.embedded_lifecycle import atexit_fast_close


def _fresh_proj(prefix="tortoise_fast_close_"):
    """FalkorProjection on an ephemeral test-tree DB (mkdtemp under the
    tempdir with a reaper-recognized ephemeral prefix)."""
    from tortoise.projection import FalkorProjection
    db = os.path.join(tempfile.mkdtemp(prefix=prefix), "c.db")
    return FalkorProjection(db, graph_name="test")


def _client(proj):
    return getattr(proj.db, "client", proj.db)


def _pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


@pytest.fixture(autouse=True)
def _fast_flag():
    # Restore the PREVIOUS value on teardown (never pop): conftest / the CI
    # job set TORTOISE_FAST_ATEXIT=1 for the whole suite — popping here
    # silently disabled the fast-close gate for every LATER test in the
    # process (the tier-2 (b) leg exposed it: test_divergence_conformance
    # D15's atexit_fast_close returned False -> "must take the fast-close
    # path"). The explicit unset tests below pop within their own bodies.
    prev = os.environ.get("TORTOISE_FAST_ATEXIT")
    os.environ["TORTOISE_FAST_ATEXIT"] = "1"
    yield
    if prev is None:
        os.environ.pop("TORTOISE_FAST_ATEXIT", None)
    else:
        os.environ["TORTOISE_FAST_ATEXIT"] = prev


def test_fast_close_ephemeral_flag_stops_server():
    proj = _fresh_proj()
    cli = _client(proj)
    pid = cli.pid
    assert _pid_alive(pid), "server should be running"
    handled = atexit_fast_close(cli)
    assert handled is True, "ephemeral + flag must take the fast path"
    # the bounded poll waits for death; small graphs die in ~0.04s
    deadline = time.time() + 8
    while _pid_alive(pid) and time.time() < deadline:
        time.sleep(0.05)
    assert not _pid_alive(pid), "fast close did not stop the server"


def test_fast_close_flag_unset_falls_through():
    os.environ.pop("TORTOISE_FAST_ATEXIT", None)
    proj = _fresh_proj()
    assert atexit_fast_close(_client(proj)) is False, \
        "unset flag must fall through to the normal close"
    proj.close()  # clean up


def test_fast_close_non_ephemeral_falls_through():
    # A user dir OUTSIDE the tempdir must never take the fast path (the
    # durability firewall — user-path servers keep redislite's SAVE close).
    from tortoise.projection import FalkorProjection
    user_dir = tempfile.mkdtemp(prefix="tortoise_fast_close_user_",
                                dir=str(Path.home()))
    try:
        db = os.path.join(user_dir, "user.db")
        proj = FalkorProjection(db, graph_name="test")
        assert atexit_fast_close(_client(proj)) is False, \
            "non-ephemeral path must fall through to the normal close"
        proj.close()
    finally:
        shutil.rmtree(user_dir, ignore_errors=True)


def test_reopen_durability_via_explicit_close():
    """The cross-process reopen classes (test_index_cli, test_flip_gate, ...)
    persist via EXPLICIT close() (redislite's SAVE) — the fast NOSAVE path
    only touches LEAKED servers at interpreter exit. Verify explicit close
    still persists data across reopen (the #1371 durability firewall)."""
    from tortoise.projection import FalkorProjection
    db = os.path.join(tempfile.mkdtemp(prefix="tortoise_fast_close_"), "c.db")
    p1 = FalkorProjection(db, graph_name="test")
    p1.g.query("CREATE (n:Point {id:'durable-x'})")
    p1.close()  # explicit close -> SAVE path (unchanged by the fast atexit)
    p2 = FalkorProjection(db, graph_name="test")
    try:
        rows = p2.g.query(
            "MATCH (n:Point {id:'durable-x'}) RETURN count(n)").result_set
        assert rows and rows[0][0] >= 1, "explicit close must persist data"
    finally:
        p2.close()


def test_fast_close_stops_server_and_closes_pool():
    """A server fast-NOSAVEd at exit is stopped and its pool closed — nothing
    left running to trip the CI orphan check."""
    proj = _fresh_proj()
    cli = _client(proj)
    assert atexit_fast_close(cli) is True
    deadline = time.time() + 8
    while _pid_alive(cli.pid) and time.time() < deadline:
        time.sleep(0.05)
    assert not _pid_alive(cli.pid), "fast close did not stop the server"


def test_exit_still_calls_close_once_with_flag_set():
    """Behavior-identical contract: __exit__ must keep calling close() once
    even with the flag set — the fast path is atexit-seam-only."""
    from tortoise import FalkorDB
    calls = []
    orig_close = FalkorDB.close

    def spy(self):
        calls.append("close")
        return orig_close(self)

    try:
        FalkorDB.close = spy
        proj = _fresh_proj()
        with proj:
            pass
    finally:
        FalkorDB.close = orig_close
    assert calls == ["close"], f"__exit__ must call close() once, got {calls}"


def test_atexit_seams_registered():
    """The three atexit seams route through _atexit_close — the fast path
    is registration-seam-only (never inside close/__exit__)."""
    from tortoise import FalkorDB  # noqa: I001
    from tortoise.sdk import TortoiseSDK
    from tortoise.projection import FalkorProjection

    # All three lifecycle classes expose the seam.
    assert hasattr(FalkorDB, "_atexit_close")
    assert hasattr(FalkorProjection, "_atexit_close")
    assert hasattr(TortoiseSDK, "_atexit_close")

    # FalkorProjection._atexit_close with the fast conditions -> fast path:
    # the server is stopped and the projection is marked closed WITHOUT the
    # (spied) close() being called.
    calls = []
    orig_close = FalkorProjection.close

    def spy_close(self):
        calls.append("close")
        return orig_close(self)

    proj = _fresh_proj()
    pid = _client(proj).pid
    try:
        FalkorProjection.close = spy_close
        proj._atexit_close()
    finally:
        FalkorProjection.close = orig_close
    assert proj._closed is True
    assert calls == [], f"fast path must not call close(), got {calls}"
    deadline = time.time() + 8
    while _pid_alive(pid) and time.time() < deadline:
        time.sleep(0.05)
    assert not _pid_alive(pid), "seam fast path did not stop the server"

    # Fall-through: with the flag unset, _atexit_close must call close().
    os.environ.pop("TORTOISE_FAST_ATEXIT", None)
    calls.clear()
    proj2 = _fresh_proj()
    try:
        FalkorProjection.close = spy_close  # re-apply spy
        proj2._atexit_close()
    finally:
        FalkorProjection.close = orig_close
    assert calls == ["close"], "flag unset -> seam must fall through to close()"


# ── #4214: the exit seam must not stat the temp root once per client ───────
#
# `os.path.realpath` lstats EVERY path component. Once the per-user temp root
# has accumulated tens of thousands of unterminated test dirs (the
# #2875/#3685 leak) the root's OWN stat is the expensive one — measured
# 13 ms–3.4 s (median ≈0.43 s) at nlink 55 k, while a child of it and a
# synthetic 55 000-subdirectory directory on the same APFS volume are both
# 0.00 ms. The exit seam used to call realpath ~9 times per leaked client, so
# `Py_FinalizeEx` spent minutes inside `lstat` — the reported hang that
# `pytest-timeout` cannot interrupt.
#
# The tests below pin the two properties that remove it. Each FAILS on the
# pre-#4214 code (counts are 2–3 per call there) and states its mutation.


def _count_temp_root_stats(monkeypatch, fn, n):
    """Run ``fn(i)`` ``n`` times, counting `os.lstat` calls on the temp root.

    The temp root's own stat is the measured cost driver, so this counts
    precisely that call rather than "some filesystem access".
    """
    root = os.path.abspath(tempfile.gettempdir())
    hits: list[str] = []
    real_lstat = os.lstat

    def spy(path, *args, **kwargs):
        if not isinstance(path, int) and os.fspath(path) == root:
            hits.append(os.fspath(path))
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", spy)
    # The memoized resolve is process-wide; clear it so this test observes the
    # same first-call path a fresh test process would.
    monkeypatch.setattr(embedded_lifecycle, "_TMPDIR_RESOLVED", None)
    for i in range(n):
        fn(i)
    return len(hits)


def test_is_ephemeral_does_not_stat_the_temp_root_per_client(monkeypatch):
    """Mutation: make `_is_ephemeral_test_server` resolve the temp root with a
    fresh `os.path.realpath()` per call (the pre-#4214 shape)."""
    n = 8
    dirs = [tempfile.mkdtemp(prefix="tortoise_4214_") for _ in range(n)]
    try:
        hits = _count_temp_root_stats(
            monkeypatch,
            lambda i: embedded_lifecycle._is_ephemeral_test_server(
                SimpleNamespace(dbdir=os.path.join(dirs[i], "c.db"))),
            n)
    finally:
        for d in dirs:
            shutil.rmtree(d, ignore_errors=True)
    assert hits <= 1, (
        f"the exit-seam classification stat-ed the temp root {hits} times for "
        f"{n} clients — that per-client realpath walk is the #4214 hang")


def test_remove_ephemeral_socket_dir_does_not_stat_the_temp_root(monkeypatch):
    """Same property for the reclamation seam, plus the positive control that
    the cheap path still reclaims the dir.

    Mutation: restore `tmpdir = os.path.realpath(tempfile.gettempdir())` and a
    `os.path.realpath(d)` per candidate in `_remove_ephemeral_socket_dir`.
    """
    n = 8
    dirs = [tempfile.mkdtemp(prefix="tortoise_4214_") for _ in range(n)]
    hits = _count_temp_root_stats(
        monkeypatch,
        lambda i: embedded_lifecycle._remove_ephemeral_socket_dir(
            dirs[i], os.path.join(dirs[i], "redis.socket")),
        n)
    assert hits <= 1, (
        f"reclaiming {n} ephemeral socket dirs stat-ed the temp root {hits} "
        "times — the #4214 per-candidate realpath walk")
    assert not any(os.path.exists(d) for d in dirs), \
        "the cheap path must still reclaim the ephemeral dirs"


def test_ephemeral_classification_is_unchanged_by_the_fast_path():
    """The #4214 fast path is EXACT, not an approximation: every case below
    must agree with the `realpath`-both-sides form it replaced.

    Mutations this catches:
      - drop the `os.path.islink` walk -> the symlinked-escape case classifies
        True and the seam would rmtree a directory outside the temp root;
      - drop the `..` guard -> `<root>/tt_sd/../tt_lit` (with `<root>/tt_sd` a
        symlink out of the root) classifies True, while the realpath form says
        False. That divergence is in the PERMISSIVE direction for a
        destructive call, which is why it is excluded in code rather than
        documented. (Found by the #4214 verification dispatch.)
    """
    from tortoise.embedded_reaper import _is_ephemeral_dir

    root = os.path.abspath(tempfile.gettempdir())
    root_real = os.path.realpath(tempfile.gettempdir())

    # 1. ephemeral: an mkdtemp tree under the temp root
    eph = tempfile.mkdtemp(prefix="tortoise_4214_")
    # 2. non-ephemeral: a home dir, never under the temp root
    home = tempfile.mkdtemp(prefix="tortoise_4214_home_", dir=str(Path.home()))
    # 3. a sibling that only LOOKS like the temp root
    fake = tempfile.mkdtemp(prefix="tortoise_4214_notroot_", dir=str(Path.home()))
    # 4. a symlink BELOW the temp root that escapes it. `tt_` is an ephemeral
    #    prefix, so only resolving the link (the slow path) can tell the truth.
    outside = tempfile.mkdtemp(prefix="tortoise_4214_outside_", dir=str(Path.home()))
    link = os.path.join(root, "tt_4214_escape")
    candidates = []
    try:
        if os.path.lexists(link):
            os.unlink(link)
        os.symlink(outside, link)
        # 5. a `..` spelling that cancels the escaping symlink (the case the
        #    lexical form gets wrong if the `..` guard is dropped).
        candidates = [
            os.path.join(eph, "c.db"),
            os.path.join(home, "c.db"),
            os.path.join(fake, "c.db"),
            os.path.join(link, "c.db"),
            os.path.join(root, "tt_4214_escape", "..", "tt_4214_literal"),
            root,
            os.path.join(root, "nothing_here", "x"),
        ]
        for dbdir in candidates:
            expected = _is_ephemeral_dir(os.path.realpath(dbdir), root_real)
            got = embedded_lifecycle._is_ephemeral_test_server(
                SimpleNamespace(dbdir=dbdir))
            assert got is expected, (
                f"{dbdir}: fast path says {got}, realpath form says {expected}")
    finally:
        for d in (eph, home, fake, outside):
            shutil.rmtree(d, ignore_errors=True)
        with contextlib.suppress(OSError):
            os.unlink(link)
    assert candidates, "the case list must not be empty"


def test_exit_budget_short_circuits_the_seam(monkeypatch):
    """Mutation: drop the ``_atexit_budget_expired()`` guard from
    `atexit_fast_close` (and `_gc_close`). A spent exit budget must stop the
    seam and neutralise redislite's own atexit close so it cannot re-run the
    slow path we just declined.

    ``at_exit=True`` is what marks the cascade — a mid-run call must NOT be
    budgeted (a caller there can still see and fix a slow close).
    """
    stub = SimpleNamespace(
        redis_dir=tempfile.mkdtemp(prefix="tortoise_4214_"),
        dbdir=None,
        socket_file=None,
        pidfile="/nonexistent/redis.pid",
    )
    monkeypatch.setenv("TORTOISE_FAST_ATEXIT", "1")
    monkeypatch.setattr(embedded_lifecycle, "_atexit_budget_expired", lambda: True)
    walked: list[object] = []
    monkeypatch.setattr(
        embedded_lifecycle, "_is_ephemeral_test_server",
        lambda c: (walked.append(c), True)[1])
    try:
        assert atexit_fast_close(stub) is not True, (
            "a mid-run call must not take the exit-budget short-circuit")
        walked.clear()
        assert atexit_fast_close(stub, at_exit=True) is True, \
            "a spent exit budget must report the client handled, not re-probe"
        assert walked == [], (
            "a spent exit budget must not run the classification walk")
        assert stub.pidfile is None, (
            "redislite's own atexit close must be neutralised so the declined "
            "close cannot re-run at exit")
    finally:
        shutil.rmtree(stub.redis_dir, ignore_errors=True)


def test_exit_cascade_still_reclaims_the_socket_dir(monkeypatch):
    """The exit cascade must still reclaim each socket dir it closes.

    This is a deliberate NON-deferral pin. Deferring the removal looks
    attractive (it is the other slow thing inside `Py_FinalizeEx`) but it
    LEAKS: after a clean NOSAVE shutdown redislite has unlinked both
    `redis.socket` and `redis.pid`, and the #4068 reaper's discovery requires
    one of those markers — so a deferred dir is invisible to the reaper and
    survives forever (verified by probe: such a dir contains only
    `redis.config`/`redis.log`).

    Mutation: make the removal conditional on being mid-run (`if not at_exit`)
    — the dirs then survive and the assertion fails.
    """
    from tortoise import embedded_lifecycle as el

    monkeypatch.setenv("TORTOISE_FAST_ATEXIT", "1")
    # A non-shared server whose socket is already gone: the seam reaches its
    # reclamation decision (the `except OSError` branch) with no live peer.
    monkeypatch.setattr(el, "cotenant_holds_server", lambda c: False)
    d = tempfile.mkdtemp(prefix="tortoise_4214_")

    def stub():
        return SimpleNamespace(
            redis_dir=d, dbdir=d, pidfile=None,
            socket_file=os.path.join(d, "redis.socket"),
            connection_pool=SimpleNamespace(disconnect=lambda: None))

    try:
        assert atexit_fast_close(stub(), at_exit=True) is True
        assert not os.path.exists(d), (
            "an exit-seam close must still reclaim its socket dir — deferring "
            "it to the reaper leaks, because a NOSAVEd server unlinks the "
            "socket/pidfile markers the reaper's discovery needs")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_exit_budget_is_per_cascade_not_per_process(monkeypatch):
    """Mutation: drop the silence check and keep one process-lifetime
    deadline. An ``_atexit_close`` called early in a long session (tests call
    it directly; production only ever calls it from ``atexit``) then expires
    the budget for the real exit cascade, which is measured 30 s later — that
    silently truncates the cascade's cleanup, and it made an earlier version
    of this test pass for the wrong reason.
    """
    from tortoise import embedded_lifecycle as el

    monkeypatch.setenv("TORTOISE_ATEXIT_BUDGET", "5")
    monkeypatch.setattr(el, "_atexit_deadline", None)
    monkeypatch.setattr(el, "_atexit_last_seam_call", None)

    assert el._atexit_budget_expired() is False
    assert el._atexit_budget_expired() is False, "a contiguous cascade holds"

    # A silence longer than the budget ends the cascade -> a fresh deadline.
    monkeypatch.setattr(el, "_atexit_last_seam_call",
                        time.monotonic() - 600)
    monkeypatch.setattr(el, "_atexit_deadline", time.monotonic() - 600)
    assert el._atexit_budget_expired() is False, \
        "a later cascade must get its own budget"


_SLOW_ROOT_STAT_SCRIPT = r'''
import os, tempfile, time

from tortoise import embedded_lifecycle as el

ROOT = os.path.abspath(tempfile.gettempdir())
REPORT = os.environ["SLEEP_REPORT"]
DELAY = float(os.environ["SLOW_ROOT_STAT"])
open(REPORT, "w").close()
_real_lstat = os.lstat


def _bump(what):
    with open(REPORT, "a") as fh:
        fh.write(what + "\n")


def slow_lstat(path, *a, **k):
    if not isinstance(path, int) and os.fspath(path) == ROOT:
        _bump("lstat " + os.fspath(path))
        time.sleep(DELAY)
    return _real_lstat(path, *a, **k)


class _Pool:
    def disconnect(self):
        pass


class _Client:
    """A client whose server is gone: the socket and pidfile are absent, so
    avoiding them costs nothing and the seam's decisions are all that is being
    measured. Construction is filesystem-trivial, so the injected slowness can
    only ever be the EXIT seam's."""

    def __init__(self, i):
        d = os.path.join(ROOT, "tortoise_4214_%d" % i)
        self.redis_dir = d
        self.dbdir = d
        self.pidfile = os.path.join(d, "redis.pid")
        self.socket_file = os.path.join(d, "redis.socket")
        self.connection_pool = _Pool()


class _Owner:
    pass


# The exit path under test is the REAL one: `register_gc_close` -> weakref's
# exit pass -> `_gc_close` -> `atexit_fast_close`. Both the owner and its
# clients must stay alive so the FINALIZER (not a mid-run GC) is the path
# exercised — in production redislite's own atexit strong-ref pins the client.
#
# Each client gets an EMPTY owner-record dir: the #3599 signal that says "this
# server has no live owner", which is what lets the seam proceed to the close
# and reclamation decision instead of declining as shared.
owner = _Owner()
_clients = []
for i in range(int(os.environ["N_CLIENTS"])):
    c = _Client(i)
    owners = os.path.join(c.redis_dir, ".tortoise-owners")
    os.makedirs(owners, exist_ok=True)
    # ONE record naming a provably DEAD owner. `_owner_records` returns None
    # for an empty dir ("no evidence -> fail closed"), which would make the
    # seam decline as shared and never reach the work under test; a dead pid
    # is the #3599 proof that no peer holds the server.
    with open(os.path.join(owners, "999999-1000000000"), "w"):
        pass
    _clients.append(c)
    el.register_gc_close(owner, c)

# Install the injected slowness AFTER construction, so only the exit seam can
# pay for it.
os.lstat = slow_lstat

print("READY", flush=True)
'''


def test_interpreter_exits_after_a_slow_temp_root_stat(tmp_path):
    """#4214 end-to-end, through the REAL exit path.

    The temp root's own `lstat` is made slow — measured 0.2–3.4 s on the
    filing box, versus 0.00 ms for a child of it — and the driver is product
    code: the ``weakref`` exit pass calling ``_gc_close``, which derives
    ``at_exit`` itself and hands it to ``atexit_fast_close``. A process owning
    N clients must exit without stat-ing the temp root per client; the
    injected calls are counted, so the assertion does not depend on machine
    speed.

    ``pytest-timeout`` cannot interrupt `Py_FinalizeEx`, so this is the only
    place the "the interpreter must exit" contract can be pinned.

    Mutation: re-add a per-client `os.path.realpath` under the temp root to
    ``_is_ephemeral_test_server`` or ``_remove_ephemeral_socket_dir`` (or drop
    the ``at_exit`` short-circuit order that keeps repeat seam invocations
    from re-walking). Pre-#4214 this counts 2 x N = 12 injected calls for 6
    clients; the fix counts 0.
    """
    n_clients = 6
    delay = 0.2
    report = tmp_path / "sleeps.txt"
    script = tmp_path / "slow_exit_4214.py"
    script.write_text(_SLOW_ROOT_STAT_SCRIPT)
    env = dict(os.environ, SLOW_ROOT_STAT=str(delay), N_CLIENTS=str(n_clients),
               SLEEP_REPORT=str(report), TORTOISE_FAST_ATEXIT="1")
    # A hang is a failure in its own right: `pytest-timeout` cannot interrupt
    # `Py_FinalizeEx`, so the subprocess TIMEOUT below is what pins "the
    # interpreter exits", and the injected-call count pins "it did not have to
    # stat the temp root to do so". No wall-clock assertion: this box's temp
    # root is degraded enough that even an unrelated import takes tens of
    # seconds, so a wall number here would measure the box, not the fix.
    try:
        proc = subprocess.run([sys.executable, str(script)], env=env,
                              capture_output=True, text=True, timeout=180)
    finally:
        # The seam reclaims these dirs; this is a safety net for the failure
        # path, where the assertion never runs.
        tmp_root = os.path.abspath(tempfile.gettempdir())
        for i in range(n_clients):
            shutil.rmtree(os.path.join(tmp_root, f"tortoise_4214_{i}"),
                          ignore_errors=True)
    assert proc.returncode == 0, proc.stderr
    assert "READY" in proc.stdout, proc.stdout
    hits = report.read_text().splitlines()
    assert hits == [], (
        f"the exit seam stat-ed the temp root {len(hits)} times for "
        f"{n_clients} clients ({hits[:4]}…) — that is the per-client "
        f"realpath walk that hangs Py_FinalizeEx (#4214)")


def test_atexit_seams_reclaim_the_socket_dir(monkeypatch):
    """Every ``_atexit_close`` seam must reclaim the dir it just closed.

    This is a deliberate NON-deferral pin (see
    ``test_exit_cascade_still_reclaims_the_socket_dir`` for why deferring
    leaks), asserted through the real seams rather than a call spy: the seams
    import ``atexit_fast_close`` into their own module globals, so patching
    the module attribute would never intercept them — such a spy passes
    vacuously on both sides of the fix.

    RED mutation: make the removal conditional on being mid-run — every
    dir below survives and the assertion fails.
    """
    from tortoise import FalkorDB
    from tortoise.projection import FalkorProjection
    from tortoise.sdk import TortoiseSDK

    monkeypatch.setenv("TORTOISE_FAST_ATEXIT", "1")
    # A server with no live co-tenant whose socket is already gone: the seam
    # reaches its reclamation decision (the `except OSError` branch).
    monkeypatch.setattr(embedded_lifecycle, "cotenant_holds_server",
                        lambda c: False)

    def _client():
        d = tempfile.mkdtemp(prefix="tortoise_4214_")
        return d, SimpleNamespace(
            redis_dir=d, dbdir=d, pidfile=None,
            socket_file=os.path.join(d, "redis.socket"),
            connection_pool=SimpleNamespace(disconnect=lambda: None))

    seams = [
        ("FalkorProjection._atexit_close", FalkorProjection._atexit_close,
         lambda c: SimpleNamespace(db=c)),
        ("TortoiseSDK._atexit_close", TortoiseSDK._atexit_close,
         lambda c: SimpleNamespace(_proj=SimpleNamespace(db=c))),
        ("FalkorDB._atexit_close", FalkorDB._atexit_close,
         lambda c: SimpleNamespace(client=c, _t_release_owner=lambda: None)),
    ]
    made: list[str] = []
    survivors: list[str] = []
    try:
        for name, seam, wrap in seams:
            d, client = _client()
            made.append(d)
            seam(wrap(client))
            if os.path.isdir(d):
                survivors.append(name)
    finally:
        for d in made:
            shutil.rmtree(d, ignore_errors=True)
    assert survivors == [], (
        "these exit seams left their socket dir behind (deferring it to the "
        "reaper leaks — the reaper cannot see a dir whose socket and pidfile "
        f"are gone): {survivors}"
    )

