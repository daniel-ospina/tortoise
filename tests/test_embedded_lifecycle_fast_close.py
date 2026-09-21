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
import uuid
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
# 0.00 ms. The exit seam used to call realpath ~5 times per leaked client, so
# `Py_FinalizeEx` spent minutes inside `lstat` — the reported hang that
# `pytest-timeout` cannot interrupt.
#
# The tests below pin the two properties that remove it. On the pre-#4214 tree
# the three call-counting ones fail on their assertion; the differential test
# is a guard for the classification itself.


def _count_temp_root_stats(monkeypatch, fn, n):
    """Run ``fn(i)`` ``n`` times, counting `os.lstat` calls on the temp root.

    The temp root's own stat is the measured cost driver, so this counts
    precisely that call rather than "some filesystem access".

    Both SPELLINGS are counted: on macOS ``tempfile.gettempdir()`` is
    ``/var/folders/…`` while ``realpath`` resolves the ``/var -> /private/var``
    symlink and stats ``/private/var/folders/…``. Keying on the raw spelling
    alone made these tests count 0 on the very platform the bug was filed on,
    so they greened against the pre-fix code (found in review).
    """
    raw_root = tempfile.gettempdir()
    roots = {os.path.abspath(raw_root), os.path.realpath(raw_root)}
    hits: list[str] = []
    real_lstat = os.lstat

    def spy(path, *args, **kwargs):
        if not isinstance(path, int) and os.fspath(path) in roots:
            hits.append(os.fspath(path))
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", spy)
    # The memoized resolve is process-wide; clear it so this test observes the
    # same first-call path a fresh test process would. `raising=False` so the
    # pre-fix tree fails on the ASSERTION, not on a missing attribute.
    monkeypatch.setattr(embedded_lifecycle, "_TMPDIR_RESOLVED", None,
                        raising=False)
    monkeypatch.setattr(embedded_lifecycle, "_TMPDIR_RESOLVED_RAW", None,
                        raising=False)
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
    seam before the EXPENSIVE work (the co-tenant probe and the rmtree) and
    neutralise redislite's own atexit close so it cannot re-run the slow path
    we just declined.

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
    monkeypatch.setattr(embedded_lifecycle, "_atexit_deadline",
                        time.monotonic() - 1)
    monkeypatch.setattr(embedded_lifecycle, "_is_ephemeral_test_server",
                        lambda c: True)
    probed: list[object] = []
    monkeypatch.setattr(embedded_lifecycle, "cotenant_holds_server",
                        lambda c: (probed.append(c), False)[1])
    try:
        assert atexit_fast_close(stub) is not True, (
            "a mid-run call must not take the exit-budget short-circuit")
        assert probed == [stub], (
            "the mid-run path must still probe the co-tenant")
        probed.clear()
        assert atexit_fast_close(stub, at_exit=True) is True, \
            "a spent exit budget must report the client handled"
        assert probed == [], (
            "a spent exit budget must stop BEFORE the co-tenant probe")
        assert stub.pidfile is None, (
            "redislite's own atexit close must be neutralised so the declined "
            "close cannot re-run at exit")
    finally:
        shutil.rmtree(stub.redis_dir, ignore_errors=True)


def test_exit_budget_never_skips_a_non_fast_path_client(monkeypatch):
    """The budget may skip ONLY a client the fast path would have handled
    (flag on AND ephemeral).

    Mutation: check the budget before the `_fast_atexit_enabled()` /
    `_is_ephemeral_test_server` gates (the shipped first revision). A
    path-based or non-ephemeral server then reports "handled" with the flag
    unset and its pidfile neutralised, so its redislite SAVE `_cleanup()`
    never runs — and the #2052 reaper PROTECTS path-based servers by design,
    so a user's DB is stranded and the single-writer embedded file can be
    held into the next run. (Found in review, P0.)
    """
    from tortoise import embedded_lifecycle as el

    keep = "/nonexistent/redis.pid"
    stub = SimpleNamespace(
        redis_dir=tempfile.mkdtemp(prefix="tortoise_4214_user_"),
        dbdir=os.path.join(str(Path.home()), "tortoise-4214-user.db"),
        socket_file=None,
        pidfile=keep,
    )
    monkeypatch.setattr(el, "_atexit_deadline", time.monotonic() - 1)
    try:
        monkeypatch.delenv("TORTOISE_FAST_ATEXIT", raising=False)
        assert atexit_fast_close(stub, at_exit=True) is False, \
            "flag off -> must fall through to the normal close"
        assert stub.pidfile == keep, (
            "a non-fast-path client must not be neutralised")

        monkeypatch.setenv("TORTOISE_FAST_ATEXIT", "1")
        assert atexit_fast_close(stub, at_exit=True) is False, \
            "non-ephemeral -> must fall through to the normal SAVE close"
        assert stub.pidfile == keep, (
            "a non-ephemeral client must not be neutralised")
    finally:
        shutil.rmtree(stub.redis_dir, ignore_errors=True)


def test_gc_close_exit_budget_skips_only_fast_path_clients(monkeypatch):
    """`_gc_close` is the weakref exit seam. Its budget guard is separate from
    `atexit_fast_close`'s and must (a) fire only on the exit pass, (b) skip the
    co-tenant probe, (c) release the owner record, and (d) not fire for a
    client the fast path would not have handled.

    Mutations: drop the guard; or apply it when `_in_weakref_exit_finalizer()`
    is False, which budgets the #1475 close-on-GC contract mid-run.
    """
    from tortoise import embedded_lifecycle as el

    d = tempfile.mkdtemp(prefix="tortoise_4214_")
    stub = SimpleNamespace(
        redis_dir=d, dbdir=d, pidfile="p",
        socket_file=os.path.join(d, "redis.socket"),
        connection_pool=SimpleNamespace(disconnect=lambda: None))
    monkeypatch.setenv("TORTOISE_FAST_ATEXIT", "1")
    monkeypatch.setattr(el, "_atexit_deadline", time.monotonic() - 1)
    probed: list[object] = []
    monkeypatch.setattr(el, "cotenant_holds_server",
                        lambda c: (probed.append(c), True)[1])
    released: list[object] = []
    monkeypatch.setattr(el, "_release_owner_quietly",
                        lambda db: released.append(db))
    try:
        # mid-run GC: the budget must NOT apply, even though it is spent
        monkeypatch.setattr(el, "_in_weakref_exit_finalizer", lambda: False)
        el._gc_close(lambda: stub)
        assert probed == [stub], "mid-run GC must still probe (#1475 contract)"

        # weakref exit pass with a spent budget: stop before the probe
        probed.clear()
        released.clear()
        monkeypatch.setattr(el, "_in_weakref_exit_finalizer", lambda: True)
        el._gc_close(lambda: stub)
        assert probed == [], "a spent exit budget must skip the co-tenant probe"
        assert released == [stub], "the owner record must be released"
    finally:
        shutil.rmtree(d, ignore_errors=True)


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
    # The process budget is global and may already be spent by an earlier
    # test's direct seam call; this test is about the RECLAMATION, so give it
    # an unspent one (monkeypatch restores the module state afterwards).
    # `raising=False` so on a pre-#4214 tree this guard fails on BEHAVIOUR
    # (the `at_exit` kwarg), not on a missing attribute.
    monkeypatch.setattr(el, "_atexit_deadline", None, raising=False)
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


def test_exit_budget_cannot_be_re_armed_by_a_slow_step(monkeypatch):
    """Mutation: re-derive the deadline from the previous call's timestamp (a
    "a silence longer than the budget ends the cascade" heuristic). The thing
    being bounded is the CALLER's own slow step, which this function cannot
    see — so in a cascade where every step exceeds the budget, every call
    reads as a new cascade, re-arms, and returns False forever. The bound then
    never fires in exactly the regime it exists for. (Found in review of the
    first revision.)
    """
    from tortoise import embedded_lifecycle as el

    monkeypatch.setenv("TORTOISE_ATEXIT_BUDGET", "0.05")
    monkeypatch.setattr(el, "_atexit_deadline", None)

    assert el._atexit_budget_expired() is False
    for _ in range(3):
        time.sleep(0.06)  # every step exceeds the budget
        assert el._atexit_budget_expired() is True, (
            "a spent budget must stay spent — a slow cascade cannot re-arm it")


def test_exit_budget_env_parsing(monkeypatch):
    """Mutation: drop the finite/positive check. `nan` parses as a float and
    `now >= nan` is always False, so a typo'd env value silently DISABLES the
    bound that exists to guarantee the process exits. Blank/garbage must keep
    the default; only an explicit 0/negative opts out.
    """
    from tortoise import embedded_lifecycle as el

    default = el._ATEXIT_BUDGET_DEFAULT
    for raw, expected in (("", default), ("garbage", default),
                          ("nan", default), ("inf", default),
                          ("1e999", default), ("0", float("inf")),
                          ("-5", float("inf")), ("2.5", 2.5)):
        monkeypatch.setenv("TORTOISE_ATEXIT_BUDGET", raw)
        assert el._atexit_budget_seconds() == expected, raw


def test_tempdir_memos_follow_a_redirected_tempdir(monkeypatch):
    """Both #4214 memos must follow a redirected ``tempfile.tempdir``.

    Mutation: memoize without keying on the raw value. `tempfile.tempdir` is
    assignable and the reaper's own suite redirects it (the
    ``monkeypatch_tempdir`` tests in `tests/test_reaper.py`), so a sticky memo
    makes those tests read the real root and fail in file order — or, worse,
    pins a pytest tmp_path that is then deleted and poisons every later call
    in the process. (Found in review.)
    """
    from tortoise import embedded_reaper as reaper

    monkeypatch.setattr(embedded_lifecycle, "_TMPDIR_RESOLVED", None)
    monkeypatch.setattr(embedded_lifecycle, "_TMPDIR_RESOLVED_RAW", None)
    monkeypatch.setattr(reaper, "_REAL_TEMPDIR", None)
    monkeypatch.setattr(reaper, "_REAL_TEMPDIR_RAW", None)
    first = tempfile.mkdtemp(prefix="tortoise_4214_root_")
    second = tempfile.mkdtemp(prefix="tortoise_4214_root_")
    try:
        monkeypatch.setattr(tempfile, "tempdir", first)
        assert embedded_lifecycle._resolved_tempdir() == os.path.realpath(first)
        assert reaper._real_gettempdir() == os.path.realpath(first)
        monkeypatch.setattr(tempfile, "tempdir", second)
        assert embedded_lifecycle._resolved_tempdir() == os.path.realpath(second), \
            "the lifecycle memo must follow a redirected tempdir"
        assert reaper._real_gettempdir() == os.path.realpath(second), \
            "the reaper memo must follow a redirected tempdir"
    finally:
        for d in (first, second):
            shutil.rmtree(d, ignore_errors=True)


_SLOW_ROOT_STAT_SCRIPT = r'''
import gc, os, tempfile, time, weakref

from tortoise import embedded_lifecycle as el

# BOTH spellings: `realpath` resolves the `/var -> /private/var` symlink on
# macOS, so keying the injection on the raw spelling alone makes this script
# miss every real root stat on the platform the bug was filed on.
ROOTS = {os.path.abspath(tempfile.gettempdir()),
         os.path.realpath(tempfile.gettempdir())}
REPORT = os.environ["SLEEP_REPORT"]
WEAKREF_REPORT = os.environ["WEAKREF_REPORT"]
DELAY = float(os.environ["SLOW_ROOT_STAT"])
open(REPORT, "w").close()
open(WEAKREF_REPORT, "w").close()
_real_lstat = os.lstat


def _bump(what):
    with open(REPORT, "a") as fh:
        fh.write(what + "\n")


def slow_lstat(path, *a, **k):
    if not isinstance(path, int) and os.fspath(path) in ROOTS:
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
        d = os.path.join(tempfile.gettempdir(),
                         "tortoise_4214_%s_%d" % (os.environ["RUN_TAG"], i))
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


# The weakref exit-pass detector itself, through the real machinery: a
# mid-run finalizer (forced with gc.collect()) must read False, and the exit
# pass must read True. `sys.is_finalizing()` is False in BOTH (measured on
# CPython 3.12.13), which is why the detector reads the frame instead.
class _ProbeOwner:
    pass


def _probe(_db):
    with open(WEAKREF_REPORT, "a") as fh:
        fh.write("exit_pass=%s\n" % el._in_weakref_exit_finalizer())


_probe_owner = _ProbeOwner()
weakref.finalize(_probe_owner, _probe, None)
del _probe_owner
gc.collect()  # mid-run -> must record False

# A SECOND probe owner, kept alive, so its finalizer runs in the weakref EXIT
# pass (a finalizer is one-shot: the one above has already fired).
_exit_owner = _ProbeOwner()
weakref.finalize(_exit_owner, _probe, None)

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
    from re-walking), or use `sys.is_finalizing()` instead of the weakref exit
    frame — pre-#4214 this counts several injected calls per client (observed
    30 for 6 clients); the fix counts 0 and detects the exit pass.
    """
    n_clients = 6
    delay = 0.2
    report = tmp_path / "sleeps.txt"
    weakref_report = tmp_path / "weakref.txt"
    script = tmp_path / "slow_exit_4214.py"
    script.write_text(_SLOW_ROOT_STAT_SCRIPT)
    run_tag = uuid.uuid4().hex[:8]
    env = dict(os.environ, SLOW_ROOT_STAT=str(delay), N_CLIENTS=str(n_clients),
               SLEEP_REPORT=str(report), WEAKREF_REPORT=str(weakref_report),
               RUN_TAG=run_tag, TORTOISE_FAST_ATEXIT="1")
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
            shutil.rmtree(os.path.join(tmp_root,
                                       f"tortoise_4214_{run_tag}_{i}"),
                          ignore_errors=True)
    assert proc.returncode == 0, proc.stderr
    assert "READY" in proc.stdout, proc.stdout
    hits = report.read_text().splitlines()
    assert hits == [], (
        f"the exit seam stat-ed the temp root {len(hits)} times for "
        f"{n_clients} clients ({hits[:4]}…) — that is the per-client "
        f"realpath walk that hangs Py_FinalizeEx (#4214)")
    # The exit-pass detector, through the real machinery: a mid-run finalizer
    # must read False and the weakref exit pass must read True. Mutations:
    # gate on `sys.is_finalizing()` (False in both, measured on CPython
    # 3.12.13 — the first revision's silent no-op), or treat any finalizer as
    # the exit pass (which would budget the #1475 close-on-GC contract).
    probes = weakref_report.read_text().splitlines()
    assert "exit_pass=False" in probes, (
        f"a mid-run finalizer must NOT read as interpreter exit: {probes}")
    assert "exit_pass=True" in probes, (
        f"the weakref exit pass must be detected: {probes}")


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
    # See the note in `test_exit_cascade_still_reclaims_the_socket_dir`: the
    # process budget is global and may already be spent by an earlier test.
    monkeypatch.setattr(embedded_lifecycle, "_atexit_deadline", None,
                        raising=False)

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

