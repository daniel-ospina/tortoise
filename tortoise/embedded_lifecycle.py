"""Fast interpreter-exit close for ephemeral embedded servers (#1371).

At interpreter exit, ~200 leaked embedded redislite servers are closed by
their atexit handlers. redislite's `close()` is slow (~3-4s/server): it runs
`shutdown(save=True)` via redis-py's `execute_command`, which blocks waiting
for the server's +OK response, then polls the process. With hundreds of
leaked servers this is a 10-15 minute tail that counts inside the CI fast
gate's 45m watchdog.

This module replaces that with a fire-and-forget `SHUTDOWN NOSAVE` at the
interpreter-exit seam for the *ephemeral test-tree* case. NOSAVE is safe for
test-tree servers: the RDB snapshot is a fallback, not the persistence
contract — the cross-process reopen/restore classes persist via EXPLICIT
close()/SAVE (test_index_cli, test_flip_gate, test_ingest_rebuild_durability
CLI subprocesses all call proj.close() in their main flows), and leaked
servers' in-memory state is discarded with the process. Each send is ~0.00s
and the servers die in parallel (~0.05s), so 200 leaks exit in seconds
instead of 13 minutes. redislite's own `_cleanup` atexit handler then finds
the server dead and no-ops fast.

Scope (deliberately narrow — the safety boundary):
- ONLY at the interpreter-exit seam (each tortoise atexit handler routes
  through `_atexit_close`, which calls `atexit_fast_close` first).
- ONLY when `TORTOISE_FAST_ATEXIT` resolves truthy through the declared contract
  (`1`/`true`/`yes`/`on`, any case — `tortoise/env_truthy.py`; opt-in, set by
  tests/conftest.py and the CI workflow env — never in hosted/production paths).
- ONLY for servers whose dbdir is an ephemeral test tree
  (`embedded_reaper._is_ephemeral_dir` + `EPHEMERAL_PREFIXES` — the same
  classification the reaper uses for reap-safety).
- Explicit `close()` / `__exit__` keep redislite's exact semantics (SAVE).
- A client that fails the gating (or a send failure) falls through to the
  normal close (never skip close — the #1005 hygiene contract).

#3653 — a LIVE co-tenant must never be torn down. redislite decides
"last client" from its own `_connection_count()`, which is **0** whenever
`_is_redis_running()` is False — and that is exactly what happens once the
shared `<dbdir>/<dbname>.settings` registry file is removed by an earlier
close. `0 <= 1` then reads as "last client", so the close SHUTDOWNs the live
server and `shutil.rmtree`s its socket dir out from under every live
co-tenant (the observed `redis.socket. No such file or directory` /
`FATAL CONFIG FILE ERROR ... 'dir '/tmp/tmpXXXX''`). The guard below is the
registry-INDEPENDENT co-tenant test used by every teardown seam, and it is
fail-CLOSED in every ambiguous case.

Residual limits (declared, not silently carried):
- Two processes that begin exiting at the SAME instant can each still see
the other as a live owner and both decline the shutdown, orphaning the
server (and its dir). The reaper + owner records are the backstop; this is
the same window redislite's own count has.
- A SIGKILLed (or OOM-killed, or crashed) process runs no teardown at all —
its owner record reads as a provably dead owner and the reaper reclaims the
server and its dir. The in-process seams cannot run for a process that is
gone.
- An uninstrumented peer (a raw redislite client that never went through the
guarded `tortoise.FalkorDB` constructor) has no owner record; the guard
fails closed on a missing record dir, but a peer that attaches AFTER the
record dir is dropped is caught only by the CLIENT LIST probe.
- `RedisMixin.__init__` starts the embedded server BEFORE it builds its
redis-py `ConnectionPool`. If that window fails (a vanished `<db>.settings`
parent), the client is left with a live pidfile and no pool; redislite's
own atexit `_cleanup`/`__del__` then abort and leak one server each. Those
clients never reach a `tortoise.FalkorDB` close seam, so redislite's own
`_cleanup` is guarded too (`_install_partial_init_cleanup_guard`) — it
reclaims the orphan over the raw socket instead of raising.
"""
from __future__ import annotations  # noqa: I001

import math
import os
import contextlib
import fcntl

import shutil
import signal
import socket
import stat
import sys
import tempfile
import time

from tortoise.embedded_reaper import (
    OWNERS_DIRNAME,
    OWNER_LOCK_NAME,
    _is_ephemeral_dir,
)
from tortoise.env_truthy import is_truthy  # #4097: the declared truthy contract

# ── #4214: the exit seam must not stat the temp root once per client ───────
#
# `os.path.realpath()` `lstat`s EVERY path component. On a box whose per-user
# temp root has accumulated tens of thousands of unterminated test dirs (the
# #2875/#3685 leak) that single stat is not free. Measured on the filing box:
# `lstat('/var/folders/…/T')` = 13 ms–3.4 s (median ≈0.43 s) at nlink 55 k,
# while `lstat` of a CHILD of it — and of a synthetic 55 000-subdirectory
# directory on the same APFS volume — is 0.00 ms. So the cost is the temp
# root's own stat, and every `realpath` of any path under the root pays it.
#
# The teardown seams below called realpath ~9 times PER LEAKED CLIENT
# (measured: 9 calls / 8.4 s for one client, all of it after the suite had
# already printed its summary). With the hundreds of clients a suite leaks,
# that is the multi-minute tail in `Py_FinalizeEx` — flat CPU, `STAT=U`,
# stacks pinned in `os_lstat`. `pytest-timeout` cannot interrupt
# interpreter finalisation, so the process simply never exits: the observed
# "hung pytest" that wedges an agent child until an outer bound kills it.
#
# Two changes remove it: resolve the temp root at most ONCE per process (it
# cannot change mid-process), and decide containment from the raw spelling
# whenever no symlink sits below the root — so the common case needs no
# `lstat` of the root at all.
_TMPDIR_RESOLVED: str | None = None
_TMPDIR_RESOLVED_RAW: str | None = None


def _resolved_tempdir() -> str:
    """``os.path.realpath(tempfile.gettempdir())``, memoized per process.

    #4214: the resolve is a full per-component ``lstat`` walk and the temp
    root's own stat is the expensive one on a leak-degraded box. The value is
    a process constant — but ``tempfile.tempdir`` is assignable (the reaper's
    own suite redirects it), so the memo is keyed on the raw value and is
    recomputed when that changes. A pure memo with no key made the fast path
    mix a new-root candidate with a stale resolved root.
    """
    global _TMPDIR_RESOLVED, _TMPDIR_RESOLVED_RAW
    raw = tempfile.gettempdir()
    if _TMPDIR_RESOLVED is None or raw != _TMPDIR_RESOLVED_RAW:
        try:
            _TMPDIR_RESOLVED = os.path.realpath(raw)
        except Exception:  # realpath is non-raising in practice; fail open
            _TMPDIR_RESOLVED = os.path.abspath(raw)
        _TMPDIR_RESOLVED_RAW = raw
    return _TMPDIR_RESOLVED


def _containment_pair(candidate: str) -> tuple[str, str]:
    """``(path, temp_root)`` to feed :func:`_is_ephemeral_dir` — the raw
    spelling when no ``lstat`` of the temp root is needed (#4214).

    Every ephemeral test dir is built by ``tempfile.mkdtemp()`` as
    ``join(gettempdir(), <prefix>…)``, so ``realpath(candidate)`` and
    ``realpath(gettempdir())`` share their whole ancestor chain and the part
    below the temp root is unchanged — UNLESS a component below the root is a
    symlink, which is the only way the two spellings can disagree. So: walk
    the part below the raw root with cheap ``islink`` calls (final component
    small → 0.00 ms) and classify raw-vs-raw; fall back to the original
    realpath-both-sides form the moment the path is not lexically under the
    raw root, a component below it IS a symlink, the link check itself fails,
    or the spelling carries a ``..`` component. The classification is
    therefore exactly the pre-#4214 one, not an approximation of it.

    The ``..`` guard is load-bearing, not defensive noise: ``abspath``
    collapses ``..`` LEXICALLY, while ``realpath`` resolves a symlink BEFORE
    applying ``..`` — so ``<root>/link/../x`` (with ``<root>/link`` a symlink
    out of the root) lands outside on the realpath side and inside on the
    lexical side. That divergence is in the PERMISSIVE direction for a
    destructive call, so it is excluded rather than documented. redislite's
    own paths are ``mkdtemp()`` products and never contain ``..``.
    """
    raw = os.fspath(candidate)
    if ".." in raw.replace("\\", os.sep).split(os.sep):
        return os.path.realpath(candidate), _resolved_tempdir()
    cand = os.path.abspath(candidate)
    base = os.path.abspath(tempfile.gettempdir())
    if cand == base or cand.startswith(base + os.sep):
        rel = cand[len(base):].lstrip(os.sep)
        parts = [p for p in rel.split(os.sep) if p] if rel else []
        cur = base
        for part in parts:
            cur = os.path.join(cur, part)
            try:
                link = os.path.islink(cur)
            except OSError:
                link = True  # cannot tell -> take the slow path
            if link:
                break
        else:
            return cand, base
    return os.path.realpath(candidate), _resolved_tempdir()


def _is_ephemeral_test_server(client) -> bool:
    """True when the client's DB dir sits in an ephemeral test tree.

    Mirrors the reaper's containment check on BOTH sides through realpath
    (a symlinked TMPDIR would otherwise fail the strict relative_to test and
    silently disable the fast path — safe direction, but slow). #4214: the
    realpath walk is now taken only when the raw spellings cannot decide (see
    :func:`_containment_pair`) — it used to re-stat the temp root on every
    call, which is what made interpreter exit unbounded.
    """
    dbdir = getattr(client, "dbdir", None)
    if not dbdir:
        return False
    return _is_ephemeral_dir(*_containment_pair(dbdir))


# ── #4214: bound the interpreter-exit seam ────────────────────────────────
#
# `Py_FinalizeEx` runs atexit handlers synchronously and in-process; nothing
# at the Python level can preempt a slow one, and `pytest-timeout` cannot
# interrupt finalisation at all. The fixes above remove the measured slow
# operation, and this budget is the belt: it caps the AGGREGATE work the exit
# seam may do, so a future regression (or a slower box) degrades into "a few
# servers are left for the reaper" instead of "the child never exits".
#
# Only the EXIT-path teardown is bounded: the `atexit` seams, `weakref`'s
# exit pass, and the #2203 terminating-signal handler (the process is about
# to die there). Explicit `close()` / `__exit__` and ordinary mid-run GC stay
# unbounded — a caller there can still observe and fix a slow path, and
# truncating a deliberate close would be a correctness change.
_ATEXIT_BUDGET_DEFAULT = 30.0
_atexit_deadline: float | None = None


def _in_weakref_exit_finalizer() -> bool:
    """True when this call runs from ``weakref.finalize``'s exit pass.

    ``weakref`` runs its remaining finalizers at interpreter exit from its own
    atexit handler, ``weakref._exitfunc``; a GC-time finalizer never carries
    that frame. This is the reliable "we are exiting" signal for the
    ``_gc_close`` seam — **not** ``sys.is_finalizing()``, which CPython 3.12.13
    still reports as ``False`` inside atexit callbacks AND inside these
    finalizers (measured). The first cut of the #4214 fix guarded on it and
    was therefore a silent no-op.
    """
    try:
        frame = sys._getframe(1)
    except ValueError:  # no Python caller (a C atexit dispatch)
        return False
    while frame is not None:
        if (frame.f_code.co_name == "_exitfunc"
                and frame.f_globals.get("__name__") == "weakref"):
            return True
        frame = frame.f_back
    return False


def _atexit_budget_seconds() -> float:
    """``TORTOISE_ATEXIT_BUDGET`` seconds, or the 30 s default.

    Blank/garbage, and anything that is not a finite positive number
    (``nan``, ``inf``), keeps the default — never "unbounded by accident".
    An explicit ``0`` or negative value is the deliberate opt-out.
    """
    try:
        val = float(os.environ.get("TORTOISE_ATEXIT_BUDGET", ""))
    except (TypeError, ValueError):
        return _ATEXIT_BUDGET_DEFAULT
    if val <= 0:
        return float("inf")  # deliberate opt-out
    return val if math.isfinite(val) else _ATEXIT_BUDGET_DEFAULT


def _atexit_budget_expired() -> bool:
    """True once this process's interpreter-exit teardown has spent the
    wall-clock budget.

    Anchored ONCE, on the first exit-seam call, and **never re-armed**.
    That is deliberate: an earlier revision tried a gap heuristic ("a silence
    longer than the budget ends the cascade") so that a stray mid-run
    ``_atexit_close`` could not consume a later cascade's budget — but the
    thing being bounded is the CALLER's own slow step, which this function
    cannot see. Every call in a slow cascade arrives more than `budget` after
    the previous one, so each would read as "a new cascade", re-arm, and
    return False forever: the bound would never fire in exactly the regime it
    exists to bound. (Found in review; the closure that makes it real is
    ``test_exit_budget_cannot_be_re_armed_by_a_slow_step``.)

    The consequence of the stricter form is conservative in the safe
    direction: a mid-run ``_atexit_close`` consumes the process's exit budget
    early, which can only make the exit MORE bounded (skipped clients keep
    their `redis.socket`/`redis.pid`, so the reaper still finds them), never
    less.
    """
    global _atexit_deadline
    if _atexit_deadline is None:
        _atexit_deadline = time.monotonic() + _atexit_budget_seconds()
    return time.monotonic() >= _atexit_deadline


def _fast_atexit_enabled() -> bool:
    """The ``TORTOISE_FAST_ATEXIT`` opt-in (#1371), through the declared contract.

    #4097: truthy spellings (1/true/yes/on) enable it; unset/blank/falsy/garbage
    stay OFF. Previously only the exact string ``"1"`` enabled it, so
    ``=true``/``=yes``/``=on`` silently fell through to the slow close. The fast
    path is additionally gated by `_is_ephemeral_test_server` + an ephemeral
    socket dir, so widening cannot reach a production data file.
    """
    return is_truthy(os.environ.get("TORTOISE_FAST_ATEXIT"))


def atexit_fast_close(client, *, at_exit: bool = False) -> bool:
    """Fast-close an ephemeral test-tree redislite client at interpreter exit.

    ``at_exit`` marks a call made from the interpreter-exit cascade (the
    ``_atexit_close`` seams, and ``weakref``'s exit pass for ``_gc_close``).
    It enables the wall-clock budget, which is what makes the process able to
    exit: `Py_FinalizeEx` runs these handlers synchronously and
    `pytest-timeout` cannot interrupt it, so a per-client cost that is merely
    slow (the #4214 `os.lstat` walk) is otherwise indistinguishable from a
    hang. Mid-run calls are unbounded as before — a caller there can still
    observe and fix a slow close.

    The budget can only ever short-circuit a client that WOULD have taken the
    fast path (flag on + ephemeral). A path-based/non-ephemeral server runs
    redislite's normal SAVE close and the #2052 reaper protects it by design —
    so skipping it would strand a user's DB and could hold the single-writer
    embedded file into the next run. (Found in review: an earlier revision
    checked the budget before those gates.)

    Returns True when the close was handled by the fast path (or there was
    nothing to do); False when the caller must fall through to the normal
    close().

    #4214: at interpreter exit the seam is additionally bounded by
    ``TORTOISE_ATEXIT_BUDGET`` (default 30 s, see
    :func:`_atexit_budget_expired`). Once that budget is spent this returns
    True without doing the close — the client's redislite atexit `_cleanup`
    is neutralised so it cannot re-run the slow path, and the server is left
    to the #2052 reaper. **Nothing is stranded by that skip:** a client this
    seam never touched still has its on-disk `redis.socket`/`redis.pid` —
    the marker the reaper's discovery requires — and the caller releases its
    owner record, so the server reads as a provable orphan (#3599). A
    bounded "leave it to the reaper" beats an unbounded interpreter exit.

    Gating (all three must hold):
      1. TORTOISE_FAST_ATEXIT truthy (1/true/yes/on, opt-in flag).
      2. Ephemeral test-tree dbdir.
      3. NO live co-tenant — decided by `cotenant_holds_server()`, NOT by
         redislite's registry-based `_connection_count()` (#3653). A failed
         probe falls through to the normal close (safe direction): the old
         `_connection_count() > 1` test read 0 as "last client" the moment
         the shared registry file was gone, and this CI-default path then
         sent SHUTDOWN NOSAVE to a peer's live server.

    The close itself is a fire-and-forget `SHUTDOWN NOSAVE` over the unix
    socket (~0.00s send; the server exits in ~0.05s). We do NOT wait for a
    response — that wait (plus redislite's serialized per-server polls) is
    the 3-4s/server cost this module eliminates. redislite's own atexit
    `_cleanup` then finds the server dead and no-ops fast; the ephemeral
    socket dir is reclaimed here because redislite only rmtrees it from
    inside `if self.pid:` and never touches a dead server's dir (#3653 F3).
    """
    # #4214: the budget is checked AFTER the two gates that decide whether
    # this client is fast-closeable at all, so it can only ever skip work the
    # fast path would have done. Both checks are cheap (the classification is
    # lexical — that is the whole point of `_containment_pair`).
    if not _fast_atexit_enabled():
        return False

    # Already handled by an earlier seam for the same server (the projection
    # and the db wrapper both register exit handlers) — skip the liveness
    # probe entirely: on a NOSAVEd server redis-py's reconnect-retry on the
    # stale connection costs ~3.9s per server (the CI tail we are removing).
    # #4214: this short-circuit comes FIRST — `_is_ephemeral_test_server` is
    # a filesystem walk, and running it before this test made every repeat
    # seam invocation pay for a client that was already handled.
    if getattr(client, "_tortoise_fast_closed", False):
        return True
    if not _is_ephemeral_test_server(client):
        return False

    # #4214: only now may a spent budget short-circuit this client. It is
    # neutralised so redislite's own atexit `_cleanup` (registered
    # independently at construction) cannot re-run the slow close we just
    # declined. Nothing is stranded: the client's on-disk
    # `redis.socket`/`redis.pid` are untouched, so the reaper's discovery
    # still sees it, and the caller releases the owner record — the #3599
    # "no live owner" signal that makes it a provable orphan. Same declared
    # residual this module already carries for SIGKILL.
    if at_exit and _atexit_budget_expired():
        _neutralize_redislite_cleanup(client)
        return True

    # #3653 F2: a live co-tenant must never be SHUTDOWN. Use the
    # registry-independent co-tenant test instead of redislite's own
    # registry-based `_connection_count() > 1`; a probe failure falls through
    # to the normal close rather than assuming "last client".
    try:
        if cotenant_holds_server(client):
            # #3653 F1: this path is only one of redislite's teardown seams.
            # Neutralize the atexit-registered `_cleanup` and `__del__` too,
            # or they rmtree the live co-tenant's dir / stop the daemon at
            # our exit even though this seam declined the shutdown.
            # `cotenant_holds_server` may return True from its in-process
            # refcount / owner-record branches WITHOUT touching the pool, so
            # disconnect here unconditionally — a stale connection left open
            # would otherwise be seen as a "peer" by the LAST client's close
            # and the server would never shut down.
            disconnect_only(client)
            _neutralize_redislite_cleanup(client)
            return True
    except Exception:
        return False  # cannot reason about sharing -> normal close

    rdir = getattr(client, "redis_dir", None)
    sock_path = getattr(client, "socket_file", None)
    if not sock_path:
        return False  # cannot reach the server socket -> normal close
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(0.5)
        s.connect(sock_path)
        s.sendall(b"*2\r\n$8\r\nSHUTDOWN\r\n$6\r\nNOSAVE\r\n")
        s.close()
    except OSError:
        # Server already gone / unreachable (including socket.timeout, an
        # OSError subclass). Disconnect the pool and neutralize redislite's
        # atexit _cleanup (below) so it cannot poll a zombie — the #1005
        # "never skip close" contract is satisfied because there is nothing
        # left to close.
        client._tortoise_fast_closed = True
        _neutralize_redislite_cleanup(client)
        try:  # noqa: SIM105
            client.connection_pool.disconnect()
        except Exception:
            pass
        # #3653 F3: reclaim the ephemeral dir only when the socket is
        # provably gone (a connect TIMEOUT means the server may still be
        # alive — never unlink a live server's socket dir).
        if not os.path.exists(sock_path):
            _remove_ephemeral_socket_dir(rdir, sock_path)
        return True

    # Fire-and-forget: the server exits in ~0.05s. Do NOT send SIGTERM —
    # redis's SIGTERM handler performs a graceful SAVE, which would
    # reintroduce the slow per-server save we are eliminating.
    # Neutralize redislite's atexit _cleanup: it would otherwise see the
    # (now-dying) server's pidfile and poll the zombie for up to 10s per
    # server. Nulling pidfile makes `pid = self.pid` return 0 and the
    # whole cleanup block skip.
    client._tortoise_fast_closed = True
    _neutralize_redislite_cleanup(client)
    try:  # noqa: SIM105
        client.connection_pool.disconnect()
    except Exception:
        pass
    # #3653 F3: we are the last client and just sent SHUTDOWN NOSAVE, so
    # redislite's dead-pid `_cleanup` will never rmtree this dir. Reclaim it
    # here — this is the CI-default path and the source of the measured
    # 10,711 orphaned tempdirs. #4214: this is not deferred. After this
    # NOSAVE the server unlinks its socket and pidfile, and the #4068
    # reaper's discovery needs one of those markers — so a dir left here
    # would be invisible to the reaper and leak forever. The exit cascade's
    # BUDGET is what bounds the aggregate cost of this call.
    _remove_ephemeral_socket_dir(rdir, sock_path)
    return True


def _neutralize_redislite_cleanup(client) -> None:
    """Make redislite's own atexit `_cleanup` a fast no-op for this client.

    `_cleanup` reads `self.pid` (a property over `self.pidfile`); with the
    pidfile gone it returns 0 and skips the ENTIRE destructive block — the
    shutdown, the zombie poll, and the `shutil.rmtree(self.redis_dir)`.
    Two uses:
      - the fast path (server already NOSAVEd — skip the redundant slow
        shutdown + zombie poll), and
      - #3653 F1: a SHARED server this client declined to tear down. The
        guard lives in the close seam we patched; redislite's OWN
        atexit-registered `_cleanup` and `__del__` still run, and with the
        shared registry file gone `_connection_count()` reads 0 -> they
        would stop the live server and delete its socket dir from under a
        live co-tenant. Nulling the pidfile makes both no-ops.

    Idempotent and safe to call repeatedly (setting `pidfile = None` twice
    is a no-op): every teardown seam,
    `_reclaim_partial_init_server`, and the guarded redislite `_cleanup` may
    each call it for the same client.
    """
    try:  # noqa: SIM105
        client.pidfile = None
    except Exception:
        pass


def _server_pid(client) -> int:
    """The client's server pid, or 0 when it cannot be read (dead/unknown).

    redislite's ``pid`` property is a pidfile read; it returns 0 for a dead
    process and may raise on a mid-write pidfile. Wrapped so teardown paths
    can use it as a plain boolean.
    """
    try:
        return int(getattr(client, "pid", 0) or 0)
    except Exception:
        return 0


def _remove_ephemeral_socket_dir(redis_dir, socket_file=None) -> bool:
    """#3653 F3: reclaim the ephemeral socket dir of a server we stopped.

    redislite's `_cleanup` only rmtrees `self.redis_dir` INSIDE
    `if self.pid:` — and `self.pid` is 0 once the server is dead. So every
    server that dies BEFORE `_cleanup` runs (the #1371 fire-and-forget
    NOSAVE path, an externally killed server, a crash) strands its socket
    dir forever. Measured on this box: 10,834 embedded tempdirs, 10,711 of
    them dead/orphaned, oldest ~2h — the "left for the reaper" claim was
    false in practice.

    Both the dir the creator made (`redis_dir`) and the dir the socket
    actually lives in are considered: a co-tenant loaded from the registry
    has `redis_dir is None` but still knows the shared socket path, and the
    LAST client of a shared server owns that dir's reclamation.

    The deletion is gated on the SAME ephemeral-temp-tree classification the
    fast-close gate and the reaper use, so a user-path server is never
    touched. Never raises.

    #4214 note: this is NOT made conditional on being at interpreter exit.
    Removing a direct child of a leak-degraded temp root is expensive there
    (measured 14 ms–4.5 s on the filing box), but it is also the ONLY path
    that reclaims such a dir: after a clean NOSAVE shutdown the server has
    unlinked both `redis.socket` and `redis.pid`, and the #4068 reaper's
    discovery requires one of those markers — so a deferred dir is invisible
    to the reaper and leaks permanently. Bounding the exit CASCADE (see
    `_atexit_budget_expired`) is the correct lever; skipping the removal is
    not.
    """
    candidates = []
    if redis_dir:
        candidates.append(redis_dir)
    if socket_file:
        candidates.append(os.path.dirname(os.path.abspath(socket_file)))
    removed = False
    for d in candidates:
        try:
            # #4214: `_containment_pair` returns the raw spelling when no
            # symlink sits below the temp root, so this no longer re-stats
            # the (potentially 55 k-entry) temp root once per candidate. The
            # rmtree target is that same pair member: rmtree is agnostic to
            # the shared `/var` → `/private/var` ancestor spelling, and the
            # realpath form is still used whenever a symlink below the root
            # could make the raw form point elsewhere.
            target, tmpdir = _containment_pair(d)
            if not _is_ephemeral_dir(target, tmpdir):
                continue
            shutil.rmtree(target, ignore_errors=True)
            removed = True
        except Exception:
            continue
    return removed


def disconnect_only(client) -> None:
    """Drop THIS client's connection without touching the shared server.

    The safe half of redislite's teardown — exactly its own shared-server
    branch (``client.py`` `_cleanup`'s ``else``: connection_pool.disconnect()),
    with none of the kill/rmtree.
    """
    try:  # noqa: SIM105
        client.connection_pool.disconnect()
    except Exception:
        pass


def cotenant_holds_server(client) -> bool:
    """True when another live client still shares this embedded server (#3653).

    redislite's ``RedisMixin._cleanup()`` deletes the server's socket dir
    (``shutil.rmtree(self.redis_dir)``) whenever its own
    ``_connection_count() <= 1`` — and that returns **0** the moment
    ``_is_redis_running()`` is False, i.e. once the shared ``.settings``
    registry file is missing (a previous close removes it: ``_cleanup`` does
    ``os.remove(self.settingregistryfile)``), and also for a client built with
    an explicit ``unix_socket_path``. Zero is then read as "last client", so
    closing ONE client tears the server down and deletes its socket dir out
    from under every live co-tenant — mid-test, surfacing as
    ``Error 2 connecting to /tmp/tmpXXXX/redis.socket. No such file or
    directory`` / ``FATAL CONFIG FILE ERROR ... 'dir '/tmp/tmpXXXX''``.

    This is the registry-INDEPENDENT co-tenant test that decision needs
    (fail CLOSED in every ambiguous case — the cost is a socket dir left for
    the reaper, the cost of failing open is the #3653 data loss):

    - in-process: the #3599 per-process owner refcount counts THIS process's
      live clients on the socket (recorded at construction, released at every
      close seam). ``> 1`` -> the process itself holds a co-tenant.
    - cross-process: the #3599 per-server owner RECORDS name every owning
      process; ``live_owners > 1`` -> another process holds a co-tenant.
    - an uninstrumented spawn (no owner-record dir) cannot be reasoned about
      -> co-tenant.
    - finally, a raw non-destructive ``CLIENT LIST`` on the socket
      (``embedded_reaper._client_list`` — never builds a redislite client,
      #849) catches an unrecorded live client; a probe failure falls back to
      the raw socket verdict and only a provably dead/missing socket lets
      the teardown proceed.

    #3653 F4: this probe is AGE-INDEPENDENT. The previous cut used
    ``embedded_reaper._active_client_count``, whose SKIPME heuristic counts
    only connections older than 2s (or named) — a peer that attached in the
    last two seconds, or carries no name, was MISSED and the teardown then
    proceeded against a live co-tenant. Instead we disconnect THIS client's
    pool first, so the only connection we still own is the transient probe
    itself: a CLIENT LIST of more than one entry is a live co-tenant of ANY
    age.

    NOTE (caller contract): the in-process refcount and owner-record branches
    return WITHOUT disconnecting this client's pool (they prove sharing and
    stop). Every caller must still call ``disconnect_only(client)`` on the
    shared path; leaving a stale connection open makes the LAST client's
    probe see a phantom "peer" and the server is then never shut down.
    """
    sock = getattr(client, "socket_file", None)
    if not sock:
        return False  # server mode / :memory: — no child socket dir to delete
    key = os.path.abspath(sock)
    try:
        if _owner_refcounts.get(key, 0) > 1:
            return True  # an in-process co-tenant holds the server
    except Exception:
        return True  # cannot reason about sharing -> fail closed
    from tortoise.embedded_reaper import (
        _client_list,
        _owner_records,
        _probe_socket_any,
    )
    try:
        owners = _owner_records(key)
    except Exception:
        return True
    if owners is None:
        return True  # uninstrumented spawn -> cannot prove last client
    live_owners, _total = owners
    if live_owners > 1:
        return True  # another PROCESS holds a co-tenant
    # #3653 F4: decide from the SERVER, not from ages. redislite's own count
    # and `embedded_reaper._active_client_count` both parse CLIENT LIST and
    # deliberately exclude connections younger than an age floor — a peer
    # that attached in the last two seconds (or carries no name) is MISSED,
    # and the teardown then proceeds against a live co-tenant. Detect it
    # without an age heuristic: drop THIS client's pool first, then a raw
    # CLIENT LIST sees exactly one connection of our own (the transient
    # probe). More than one means a live co-tenant of ANY age.
    disconnect_only(client)
    try:
        clients = _client_list(key)
    except Exception:
        return True
    if clients:
        return len(clients) > 1
    # The probe failed, or reported zero clients while accepting our own
    # connection (the server is going down). Fall back to a raw socket
    # verdict: only a provably dead/missing socket means nothing live is
    # left to protect.
    try:
        verdict = _probe_socket_any(key)
    except Exception:
        return True
    return verdict not in ("dead", "missing")


# ── #3653: reclaim a partially-initialized client's orphaned server ────────
#
# `RedisMixin.__init__` starts the embedded server (and writes its pidfile)
# BEFORE it constructs the redis-py `ConnectionPool` (redislite `client.py`:
# `_start_redis()` runs ahead of `super().__init__()`, which is what sets
# `self.connection_pool`). If anything in that window fails — the
# `<db>.settings` parent vanished under the tempdir race, a registry read
# lost its file — the object is left with a live pidfile and NO
# `connection_pool`. redislite's own atexit-registered `_cleanup` and its
# `__del__` then both run it and abort at `self.shutdown(...)` with
# ``AttributeError: 'Redis' object has no attribute 'connection_pool'``, so
# the server is never stopped: one orphan per aborted `__del__` (CI measured
# 43).
#
# These objects never pass through any `tortoise.FalkorDB` close seam (the
# wrapper registers its handlers only AFTER `super().__init__()` returns),
# so per-seam neutralization cannot reach them. Cover redislite's own
# teardown seam instead: reclaim the orphan and make the
# connection-pool-dependent body a no-op.
_ORIGINAL_REDISLITE_CLEANUP = None


def _reclaim_partial_init_server(client) -> None:
    """#3653: reap a server whose `Redis.__init__` aborted after start.

    A partial client knows `socket_file`/`redis_dir`/`pidfile` but has no
    `connection_pool`, so redislite's destructive teardown cannot run. Stop
    the server over the raw unix socket (the same #1371 fire-and-forget
    `SHUTDOWN NOSAVE` — no pool needed), fall back to `SIGTERM` on the
    readable pid, neuter the client's own atexit/`__del__`, and reclaim the
    ephemeral socket dir. Idempotent; never raises.
    """
    sock = getattr(client, "socket_file", None)
    pid = _server_pid(client)
    if sock:
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(0.5)
            s.connect(sock)
            s.sendall(b"*2\r\n$8\r\nSHUTDOWN\r\n$6\r\nNOSAVE\r\n")
            s.close()
        except OSError:
            pass
    if pid:
        try:  # noqa: SIM105
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    rdir = getattr(client, "redis_dir", None)
    _neutralize_redislite_cleanup(client)
    _remove_ephemeral_socket_dir(rdir, sock)


def _install_partial_init_cleanup_guard() -> None:
    """#3653: make redislite's `_cleanup` safe for partial clients (once).

    A client with no `connection_pool` cannot run redislite's teardown (it
    raises before reaching the server), so the guard reclaims its orphaned
    server instead. Every complete client delegates to the original
    `_cleanup` unchanged.
    """
    global _ORIGINAL_REDISLITE_CLEANUP
    try:
        from redislite.client import RedisMixin
    except Exception:  # redislite absent — nothing to guard
        return
    if getattr(RedisMixin, "_tortoise_partial_init_guard", False):
        return
    original = RedisMixin._cleanup
    _ORIGINAL_REDISLITE_CLEANUP = original

    def _cleanup(self, *args, **kwargs):
        if not hasattr(self, "connection_pool"):
            _reclaim_partial_init_server(self)
            return None
        return original(self, *args, **kwargs)

    RedisMixin._cleanup = _cleanup
    RedisMixin._tortoise_partial_init_guard = True


# Installed at import (before any client is constructed). Redislite's own
# `atexit.register(self._cleanup, ...)` resolves `self._cleanup` through the
# class, so both its atexit seam and `__del__` pick up the guarded version.
_install_partial_init_cleanup_guard()


# ── Issue #1475: deterministic close-on-GC (lifecycle finalize) ────────────
#
# Leaked (never-explicitly-closed) SDK/projection objects used to be pinned
# alive until interpreter exit by atexit strong-refs (both ours and
# redislite's internal `atexit.register(self._cleanup, ...)` at client.py),
# so ~200 embedded servers/suite accumulated and were only closed at exit.
# Close-on-GC cuts that surface mid-suite.
#
# The weakref.finalize dead-referent constraint ("finalizer callbacks cannot
# dereference their own referent — it is already dead") is worked around
# here: the finalizer is attached to the OWNER (the SDK/projection layer),
# which is kept collectable by the deref-and-call atexit seams
# (register_atexit_close). It never touches its own (dead) referent — it
# derefs a weakref to the DB client, which redislite's own atexit pin keeps
# alive for the process lifetime, and closes via that client. The captured
# weakref IS the "registry entry"; the pinned client is the liveness anchor.

import weakref as _weakref  # noqa: E402


def register_atexit_close(obj) -> None:
    """#1475: register ``obj._atexit_close`` at exit WITHOUT pinning ``obj``
    alive until exit.

    A plain ``atexit.register(obj._atexit_close)`` holds a strong bound-method
    ref and would make close-on-GC impossible (the object could never be
    collected). ``weakref.WeakMethod`` cannot be registered directly either:
    atexit invokes the registered callable and discards the return value,
    and ``WeakMethod.__call__`` RETURNS the bound method instead of invoking
    it — the seam would silently no-op for BOTH live and dead objects. This
    wrapper derefs a plain weakref and invokes the method only while the
    object is still alive — behavior for live objects is byte-identical to
    the pre-#1475 strong registration.
    """
    import atexit as _atexit
    _atexit.register(_atexit_call_if_alive, _weakref.ref(obj))


def _atexit_call_if_alive(ref) -> None:
    """atexit callback: invoke ``_atexit_close`` only if the referent is
    still alive (collected objects were already closed by their finalizer
    on GC — no double close; collected or not, the exit close stays exactly
    as it was before #1475 for live objects)."""
    obj = ref()
    if obj is not None:
        obj._atexit_close()


def register_gc_close(owner, db) -> None:
    """Register a finalizer that closes `db` deterministically when `owner`
    is garbage-collected (issue #1475).

    `owner` is the collectable layer (FalkorProjection); `db` is its
    embedded FalkorDB client. The finalizer never dereferences its own
    (dead) referent — it derefs the captured weakref to the pinned client
    (kept alive by redislite's own atexit) and closes through that. The
    #1371 shared-server guard and ephemeral fast-close gating apply
    unchanged. Idempotent; never raises.

    #4214: reaching here during the exit cascade, the per-client dir removal
    is the only remaining filesystem work and the exit budget bounds how much
    of it the cascade may do before it stops.

    NOTE: host-mode (docker/URI) clients are not pinned by redislite — the
    captured weakref derefs to None when `owner` is collected and the
    finalizer no-ops (safe by construction).
    """
    if owner is None or db is None:
        return
    _weakref.finalize(owner, _gc_close, _weakref.ref(db))


def _gc_close(db_ref) -> None:
    """GC-time close callback (issue #1475).

    Mirrors redislite's own last-client close semantics so leaked servers
    die deterministically mid-suite while shared servers are never killed
    out from under a live co-tenant:
      - explicitly closed clients (``_t_closed``) -> strict no-op (the
        server stays under its remaining owners and dies at exit — the
        exact pre-#1475 semantics; never double-close)
      - probe failure (pool already disconnected by a bare close()) -> no-op
        (never assume last-client on a failed probe at GC time — that
        shortcut is only safe at interpreter exit, where every client of
        this process is dying anyway)
      - shared server (count > 1) -> disconnect our pool only; the last
        owner's close/GC/exit shuts the server down
      - last client -> #1371 fast-close (ephemeral test-tree + flag:
        fire-and-forget NOSAVE) else redislite's normal close
        (``_cleanup``: SAVE + pidfile/socket cleanup)
    Never raises (GC context).
    """
    db = db_ref()
    if db is None:
        return  # not a redislite-pinned client — nothing to do
    # #4487 review: `getattr(db, "client", db)` is NOT safe — a raw embedded
    # `Redis` exposes `.client` as a BOUND METHOD (its self-constructing clone
    # helper), so the old form treated that method as the inner client and
    # bailed at the `socket_file is None` guard below, leaving the owner
    # record (which the #4487 patch now writes) unreleased. A callable is
    # never an inner client.
    _inner = getattr(db, "client", None)
    client = _inner if (_inner is not None and not callable(_inner)) else db
    # #4214: `_gc_close` runs both on ordinary mid-run collection and on
    # `weakref`'s exit pass. Only the latter may skip work: mid-run GC-time
    # reclamation is the #1475 close-on-GC contract and must keep running.
    at_exit = _in_weakref_exit_finalizer()
    # Explicit close()/__exit__ are routed through db._t_close by the
    # projection, which sets _t_closed — the finalizer is a strict no-op
    # then (the socket_file guard below would not catch it: redislite's
    # close() is a redis-py pool disconnect that keeps the server and the
    # socket path intact; only _cleanup nulls socket_file).
    if getattr(db, "_t_closed", False):
        return
    if getattr(client, "_tortoise_fast_closed", False):
        return  # already fast-closed by an earlier seam (NOSAVE)
    if getattr(client, "socket_file", None) is None:
        return  # server already shut down
    # #4214: a spent exit budget skips the co-tenant probe too (the probe is
    # bounded per call, but N clients x probe is the same aggregate tail the
    # budget exists to cap). Gated on the same eligibility as the fast path:
    # only a fast-closeable client may be skipped, because a path-based server
    # runs redislite's normal SAVE close and is reaper-PROTECTED by design.
    if (at_exit and _fast_atexit_enabled()
            and _is_ephemeral_test_server(client)
            and _atexit_budget_expired()):
        _neutralize_redislite_cleanup(client)
        _release_owner_quietly(db)
        return

    # #3653: redislite's own count is registry-based and reads 0 once the
    # shared registry file is gone — a GC-time close would then SHUTDOWN the
    # live server and delete its socket dir under a live co-tenant. Use the
    # registry-independent co-tenant test instead (strictly stronger: it also
    # covers an unrecorded/uninstrumented peer, fail closed).
    try:
        shared = cotenant_holds_server(client)
    except Exception:
        return  # cannot determine sharing -> leave the server alone
    if shared:
        # Other clients (this process or another) share the server — drop
        # our connection only; the last owner's close/GC/exit shuts it down.
        disconnect_only(client)
        # #3653 F1: redislite's OWN atexit `_cleanup` + `__del__` are not
        # covered by this seam; neutralise them so our exit cannot stop the
        # live co-tenant's server (the registry-based `_connection_count`
        # reads 0 once the shared registry file is gone).
        _neutralize_redislite_cleanup(client)
        # #3599: release THIS client's owner-record claim. A shared server
        # would otherwise keep the record (and the per-process refcount)
        # until process exit, so a later construct/close on the same socket
        # path could never drive the refcount to 0 and drop the record.
        _release_owner_quietly(db)
        return
    rdir = getattr(client, "redis_dir", None)
    sock_path = getattr(client, "socket_file", None)
    pid_before = _server_pid(client)
    try:
        if atexit_fast_close(client, at_exit=at_exit):
            _release_owner_quietly(db)
            return
    except Exception:
        pass  # probe/gating failure -> fall through to the normal close
    try:  # noqa: SIM105
        client._cleanup()
    except Exception:
        pass
    # #3653: `cotenant_holds_server()` above already dropped this client's
    # pool (its F4 probe). A `_cleanup()` that aborts (a partially-created
    # redislite client with no `connection_pool`, a vanished socket dir)
    # leaves the live pidfile in place, and redislite's own atexit
    # `_cleanup` + `__del__` then run it again and throw. Neutralize every
    # such client here so it reaches `__del__` already neutralized
    # (idempotent — a no-op when `_cleanup` succeeded).
    # #3653 F3: redislite's `_cleanup` only rmtrees inside `if self.pid:`,
    # so a server that was already dead (pid 0) strands its dir. Reclaim it
    # here — the fast path above already does this for the CI-default case,
    # this covers the flag-off / non-ephemeral fall-through.
    _neutralize_redislite_cleanup(client)
    if not pid_before:
        _remove_ephemeral_socket_dir(rdir, sock_path)
    # #4487 review: `_cleanup()` can null `socket_file`, so hand the socket we
    # captured BEFORE it to the release fallback (a raw client has no
    # idempotent `_t_release_owner` to fall back on).
    _release_owner_quietly(db, sock_path)


def _release_owner_quietly(db, sock: str | None = None) -> None:
    """#3599: release a client's owner claim from GC/**non-raising** contexts.

    Prefers the guarded wrapper's idempotent ``_t_release_owner``; a raw
    redislite client has no such method and falls back to a direct
    ``forget_owner`` on ``sock`` (the caller-captured path) or its own socket
    path.
    """
    try:
        release = getattr(db, "_t_release_owner", None)
        if release is not None:
            release()
            return
        forget_owner(sock or owner_socket_of(db))
    except Exception:  # GC/teardown context: never raise
        pass


# ── Issue #2203: terminating-signal teardown guard ────────────────────────
#
# redislite's redis-server child DAEMONIZES at startup (`daemonize yes` in
# the config redislite writes; empirically ppid=1 in its own session), so it
# is never in the Python parent's process group: a terminating signal that
# kills the parent can never reach the server by process-group delivery.
# Python's DEFAULT disposition for SIGTERM/SIGHUP is immediate process death
# WITHOUT running atexit handlers — and SIGINT is IGNORED for the whole
# process lifetime when the interpreter started with stdin not a tty
# (CPython sets SIG_IGN; the reported "tortoise index github ignores
# Ctrl-C/kill -INT" symptom). Every OTHER exit path (normal return, sys.exit,
# uncaught exception, interactive KeyboardInterrupt) runs the registered
# atexit teardown — redislite's own `_cleanup` and the
# register_atexit_close / register_gc_close seams above — which shuts each
# server down with last-client semantics (SAVE on the last client,
# disconnect-only for a shared server). A process killed with
# SIGTERM/SIGHUP, or SIGINT-ed while ignored, skipped all of it and orphaned
# its redis-server: the 2026-09-03 first-run trial left orphans outliving
# (a) the selfhost daemon (SIGTERM), (b) the stdio MCP server (client
# disconnect → harness kill), and (c) `tortoise index github`. Orphans hold
# the single-writer embedded file for the next run.
#
# The guard below closes the gap. It does NOT raise SystemExit from the
# handler (the obvious "unwind to atexit" move): inside an asyncio event
# loop the raised exception can land inside a task and be swallowed as a
# never-retrieved task exception, leaving the server process running with
# its embedded server still open (observed against the MCP stdio transport).
# Instead the handler (1) closes every live embedded client INLINE — the
# pool lock redis-py uses is an RLock, so a handler preempting the main
# thread mid-command re-acquires it reentrantly instead of deadlocking, and
# redislite's cleanup falls back to pid SIGTERM/SIGKILL if the shutdown
# command errors — then (2) restores the default disposition and re-raises
# the signal, so the process dies with the conventional signal semantics
# (WIFSIGNALED; shell status 143/130/129) regardless of what the preempted
# code was doing. No reliance on unwinding, no asyncio task to swallow the
# exception, no dependency on atexit running.
#
# To close the clients the guard tracks them in a process-wide weak registry
# (`register_embedded_client`, called from the guarded tortoise.FalkorDB
# subclass at construction). Weak refs keep the #1475 close-on-GC semantics
# intact (a leaked client is still collectable; the finalizer still closes
# it) — the registry is only a liveness view, not a pin.
#
# Coverage (idempotent install — call from the entry points that own the
# process; NEVER from client construction — see
# tests/test_projection_lifecycle.py::test_no_per_instance_signal_handlers):
#   SIGTERM, SIGHUP — replaced while still at the default disposition. A
#       host that installed its OWN handler keeps it during its run — e.g.
#       uvicorn replaces SIGTERM with its graceful-shutdown handler while
#       serving, then RESTORES the pre-existing disposition (this guard) and
#       re-raises the signal at the end of the drain, so the embedded close
#       still runs before the final death. The drain is bounded by
#       timeout_graceful_shutdown so a `docker stop` cannot SIGKILL the
#       process before that close finishes.
#   SIGINT — only when the interpreter started with it IGNORED. Interactive
#       SIGINT keeps raising KeyboardInterrupt, which reaches atexit.
#
# Residual boundary (unchanged by design): SIGKILL and hard crashes cannot
# run Python, so nothing in-process can clean up — those orphans are the
# reaper's job (embedded_reaper + the conftest/CI hygiene sweeps, #2052;
# reaper pidfile-identity work, #1448).
import signal as _signal  # noqa: E402
import weakref as _weakref_registry  # noqa: E402

_TERM_HANDLED_SIGNALS = ("SIGTERM", "SIGHUP")
_signal_guard_installed = False
# Live embedded redislite clients this process opened (weak: never pins a
# client — #1475 close-on-GC stays intact; dead referents vanish on
# iteration). Registered by tortoise.FalkorDB.__init__ (the guarded subclass
# every embedded construction funnels through).
_embedded_clients: _weakref_registry.WeakSet = _weakref_registry.WeakSet()


def register_embedded_client(client) -> None:
    """Track a live embedded redislite client for the #2203 signal teardown.

    Weak (WeakSet) — registration must never pin the client alive (that
    would defeat the #1475 close-on-GC finalizer for leaked clients). Called
    from the guarded ``tortoise.FalkorDB`` subclass after the server starts;
    never registers host/port (server-mode) constructions — they have no
    redis child to reap and their pools belong to a live remote server.
    """
    try:  # noqa: SIM105
        _embedded_clients.add(client)
    except TypeError:
        pass  # unweakrefable client — teardown just skips it


def close_embedded_clients() -> int:
    """Close every live embedded client this process opened (issue #2203).

    Routes each client through the SAME idempotent seams normal teardown
    uses (redislite last-client semantics: the final close shuts the server
    down with a save; shared servers survive for their other clients):
      1. the #1371 ephemeral fast-close (NOSAVE, only for test-tree servers
         whose TORTOISE_FAST_ATEXIT resolves truthy) and
      2. the guarded subclass ``_t_close`` (``FalkorDB.close`` →
         redislite ``_cleanup``) — the raw-client fallback mirrors that.
    Signal-handler-safe in practice: redis-py's pool lock is an RLock, so a
    handler that preempted the main thread mid-command re-enters instead of
    deadlocking, and ``_cleanup`` escalates to pid SIGTERM/SIGKILL when the
    shutdown command itself errors. Never raises. Returns the number of
    clients closed.
    """
    closed = 0
    for client in list(_embedded_clients):
        # The registry holds the guarded ``tortoise.FalkorDB`` wrapper; the
        # #1371 fast-close probe reads ``dbdir``/``socket_file`` off the
        # INNER redislite client (the wrapper has neither — it only owns
        # ``close()`` → ``client._cleanup()``).
        # #4487 review: a raw embedded `Redis` exposes `.client` as a bound
        # method; never treat a callable as the inner client.
        _c = getattr(client, "client", None)
        inner = _c if (_c is not None and not callable(_c)) else client
        # #4487 review (cycle 2): capture the socket BEFORE any teardown —
        # redislite's `_cleanup()` nulls `socket_file`, and a raw client has
        # no idempotent `_t_release_owner` to fall back on, so a post-teardown
        # `owner_socket_of(inner)` resolves None and STRANDS the claim (the
        # refcount then short-circuits `record_owner` forever, leaving a later
        # live server on this path uninstrumented — the #4487 class).
        sock_before = getattr(inner, "socket_file", None) or owner_socket_of(inner)
        try:
            # #4214: this is the #2203 terminating-signal teardown — the
            # process is about to die (`os.kill(self, signum)` follows), so
            # it takes the exit-seam semantics: a spent budget stops the
            # close rather than delaying the death it exists to perform.
            if atexit_fast_close(inner, at_exit=True):
                closed += 1
                _release_owner(client, inner, sock_before)
                continue
        except Exception:
            pass  # probe/gating failure -> fall through to the normal close
        t_close = getattr(client, "_t_close", None)
        if t_close is not None:
            try:  # noqa: SIM105
                t_close()
            except Exception:
                pass  # teardown context: never raise
            _release_owner(client, inner, sock_before)
            closed += 1
            continue
        cleanup = getattr(client, "_cleanup", None)
        if cleanup is not None:
            try:  # noqa: SIM105
                cleanup()
            except Exception:
                pass
            # #3653: same contract as `_gc_close` — a raw client whose
            # `_cleanup` aborted must not re-run it from `__del__`.
            _neutralize_redislite_cleanup(inner)
            closed += 1
        _release_owner(client, inner, sock_before)
    return closed


def _release_owner(client, inner, sock: str | None = None) -> None:
    """#3599: release a client's owner-record claim (never raises).

    Prefers the guarded wrapper's idempotent ``_t_release_owner`` (which
    also handles the refcount when one process holds several clients on a
    shared server); a raw redislite client has no such method and falls
    back to a direct ``forget_owner`` on ``sock`` (the caller-captured path,
    taken BEFORE teardown nulls ``socket_file``) or its own socket path.
    """
    try:
        release = getattr(client, "_t_release_owner", None)
        if release is not None:
            release()
            return
        forget_owner(sock if sock is not None else owner_socket_of(inner))
    except Exception:  # teardown context: never raise
        pass


def _embedded_term_handler(signum, _frame) -> None:
    """Terminating-signal handler (issue #2203).

    Restore the default disposition FIRST (a second signal during teardown
    means "hurry up and die" — instant default death), then close every
    live embedded client (see close_embedded_clients — inline, never
    raises), then re-raise the original signal so the process dies with the
    conventional signal semantics (WIFSIGNALED; shell status 128+signum)
    no matter what the preempted code was doing. The re-raised signal is
    blocked while the handler runs (Python installs handlers without
    SA_NODEFER), so the default-action death lands the moment this handler
    returns.
    """
    try:  # noqa: SIM105
        _signal.signal(signum, _signal.SIG_DFL)
    except (ValueError, OSError, RuntimeError):
        pass
    try:
        close_embedded_clients()
    finally:
        try:
            os.kill(os.getpid(), signum)
        except (OSError, ValueError):
            # Signal delivery failed (e.g. signum became invalid) — never
            # leave the process alive after a termination request.
            os._exit(128 + signum)


def install_embedded_signal_cleanup() -> bool:
    """Wire terminating signals to embedded-server teardown (issue #2203).

    Installs the #2203 guard on the signals whose DEFAULT disposition would
    kill the process without closing its embedded redis-server children:
      - SIGTERM / SIGHUP: replaced only while still SIG_DFL — a host that
        installed its own handler keeps it (its graceful path also ends in
        interpreter exit → atexit).
      - SIGINT: replaced only when the interpreter started with SIG_IGN
        (stdin not a tty) — a real Ctrl-C on a tty keeps raising
        KeyboardInterrupt (which already reaches atexit).

    Idempotent per process. Safe to call from every owning entry point (CLI
    main, MCP stdio main, selfhost daemon import) — the second call no-ops.
    Never raises; returns True when at least one disposition was replaced
    (False when everything was already handled or the platform lacks the
    signal).
    """
    global _signal_guard_installed
    if _signal_guard_installed:
        return False

    replaced = False
    for name in _TERM_HANDLED_SIGNALS:
        signum = getattr(_signal, name, None)
        if signum is None:
            continue  # platform lacks the signal (e.g. SIGHUP on Windows)
        try:
            if _signal.getsignal(signum) in (_signal.SIG_DFL, None):
                _signal.signal(signum, _embedded_term_handler)
                replaced = True
        except (ValueError, OSError, RuntimeError):
            pass  # non-main thread or unsupported — skip, never raise
    # SIGINT: only the ignored-at-startup case (non-tty). Interactive SIGINT
    # (KeyboardInterrupt) is left alone — it already reaches atexit.
    try:
        if _signal.getsignal(_signal.SIGINT) is _signal.SIG_IGN:
            _signal.signal(_signal.SIGINT, _embedded_term_handler)
            replaced = True
    except (ValueError, OSError, RuntimeError):
        pass
    _signal_guard_installed = True
    return replaced


# ── #3599: per-server owner records ────────────────────────────────────
# The reaper's only_safe mode could not distinguish a SIGKILLed suite's
# orphan from a live suite's between-tests idle server on pid/detachment
# alone (#1557 — every redislite server daemonizes to ppid=1), so #1642
# gated orphan confirmation on a GLOBAL condition (`not suites_active`).
# On a host running a fleet of concurrent sessions that condition is never
# true, so `_orphan_confirmed` was never set and the only_safe reaper
# (launchd cron + the conftest end-sweep) was permanently a no-op — every
# SIGKILLed lane left its servers behind (#3599: 527 orphans, load 98 on
# 10 CPUs).
#
# Fix: record the OWNERS of each server — one file per owning process, in
# the server's OWN socket dir — so orphanhood becomes a per-server
# question ("does this server still have a live owner?") that needs no
# reference to any other suite. A shared server is safe by construction:
# every constructor writes its own record, so a co-tenant that attaches
# after the creator has exited keeps its own live entry and the server is
# never confirmed.
#
# The reaper owns the format and the constant (OWNERS_DIRNAME); this module
# is the only writer. Identity is (pid, process start time) — the #1642
# FIX 5 recycled-pid defence — so a record left behind by a SIGKILLed owner
# reads as provably dead instead of aliasing a later process.


def owner_record_dir(socket_file: str) -> str:
    """Dir holding the owner records for the server at ``socket_file``."""
    return os.path.join(os.path.dirname(os.path.abspath(socket_file)),
                        OWNERS_DIRNAME)


#: Per-process refcount of live clients per socket path (see record_owner/
#: forget_owner — several clients in ONE process share a single record, so a
#: record is only dropped when the last of them closes).
_owner_refcounts: dict[str, int] = {}

#: #4577: per-process fd holding the SHARED ``flock`` on each socket's owner
#: dir ``.lock``, keyed by the same abspath key as `_owner_refcounts`. The fd
#: is kept OPEN for the process lifetime and closed only when the refcount
#: reaches 0, so the kernel holds the lock exactly as long as this process is
#: a live owner. Kept in lockstep with `_owner_refcounts` so no fd leaks
#: (every key is popped and closed on the last `forget_owner`; the at-fork
#: hook re-acquires fresh descriptors, see `_adopt_owner_records_after_fork`).
_owner_lock_fds: dict[str, int] = {}


def _acquire_owner_lock(socket_file: str) -> int | None:
    """Take and HOLD a shared ``flock`` on the owner dir's ``.lock`` (#4577).

    The reaper's liveness question ("does this server still have a live
    owner?") becomes a KERNEL FACT when it is answered by a held lock: the
    kernel releases the lock when the last fd referring to the open file
    description is closed, i.e. when this process dies. That is strictly
    stronger than the pid+start inference (#3599 / #1642 FIX 5), which needs
    a ``ps`` read and still has a recycled-pid / unreadable-start failure
    class (an inference can be wrong; a held lock cannot).

    ``O_NOFOLLOW`` so a symlink planted at ``.lock`` in a shared tempdir is
    never followed (#4098 discipline): the open fails with ELOOP and this
    process simply carries no lock, which the reader treats as "unknown" and
    falls back to the records. Never raises — a lock we cannot take is a
    missing optimisation, never a construction failure.

    Returns the held fd (the caller keeps it open for the process lifetime)
    or None when no lock could be taken.
    """
    path = os.path.join(owner_record_dir(socket_file), OWNER_LOCK_NAME)
    try:
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    except OSError:
        return None
    try:
        # #4577 review, #4098 discipline: reject a planted NON-REGULAR file.
        # On Linux `flock` on a FIFO SUCCEEDS, so without this the writer
        # would "hold" a lock the reader must then refuse to open (which,
        # unguarded, blocks forever). No lock -> the records stay the
        # fallback signal.
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            with contextlib.suppress(OSError):
                os.close(fd)
            return None
    except OSError:
        with contextlib.suppress(OSError):
            os.close(fd)
        return None
    try:
        fcntl.flock(fd, fcntl.LOCK_SH)
    except OSError:
        # A lock we cannot take is "no lock"; close the fd rather than leak
        # it — the record files remain the fallback liveness signal.
        with contextlib.suppress(OSError):
            os.close(fd)
        return None
    return fd


def _release_owner_lock(key: str) -> None:
    """Drop this process's shared ``flock`` for ``key`` (last owner only).

    Closing the fd is what releases the lock (the kernel drops it with the
    open file description); the explicit ``LOCK_UN`` just makes the release
    immediate and self-documenting. Never raises.

    Then reclaim the ``.lock`` file so the owner dir can be removed when this
    process was the LAST owner on the host — otherwise every close would
    strand a ``.lock``-only ``.tortoise-owners`` dir (the temp-dir leak class
    #3599 exists to fight). The reclaim is gated on an EXCLUSIVE
    non-blocking lock taken AFTER our own release: a competing live owner's
    shared lock makes the attempt fail, and the file is then left in place
    for it. (Unlinking would be safe even then — a live owner always has a
    record file, which is the fallback signal — but keeping the stronger
    signal whenever any other owner exists is strictly better.)
    """
    fd = _owner_lock_fds.pop(key, None)
    if fd is None:
        return
    with contextlib.suppress(OSError):
        fcntl.flock(fd, fcntl.LOCK_UN)
    with contextlib.suppress(OSError):
        os.close(fd)
    path = os.path.join(owner_record_dir(key), OWNER_LOCK_NAME)
    try:
        probe = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    except OSError:
        return  # already gone / unreadable — nothing to reclaim
    try:
        try:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return  # another live owner still holds it -> leave the file
        with contextlib.suppress(OSError):
            os.unlink(path)
    finally:
        with contextlib.suppress(OSError):
            os.close(probe)

#: Cache of THIS process's start time, keyed by pid (#4487).
#: `record_owner` runs on EVERY construction now, and `_process_start_time`
#: shells out to `ps` — a fork+exec per client, which on a loaded runner is
#: real load and was implicated in the carve-out lane's time-bounded waits.
#: A process's own start time never changes, so resolve it once; the pid key
#: keeps this correct across `fork()` (a child sees its own pid).
_own_start_cache: dict[int, float | None] = {}


def _own_start_time() -> float | None:
    """This process's start time, resolved at most ONCE per pid (#4487)."""
    pid = os.getpid()
    if pid not in _own_start_cache:
        from tortoise.embedded_reaper import _process_start_time
        try:
            _own_start_cache[pid] = _process_start_time(pid)
        except Exception:
            _own_start_cache[pid] = None
    return _own_start_cache[pid]


def _adopt_owner_records_after_fork() -> None:
    """Re-establish owner records for inherited clients in a forked child.

    #3599 adversarial review (fail-open): `_owner_refcounts` is inherited
    across `fork()` but the child is a DIFFERENT process, so the parent's
    record does not name it. Without this hook the child's `record_owner`
    would short-circuit on the inherited count and write no
    `<child-pid>-<start>` file; when the parent was then SIGKILLed (no
    `forget_owner` runs) the child — a live owner holding the inherited
    connection — would be invisible, `_owner_records` would report 0 live
    owners, and the reaper would kill the server out from under it.

    So in the child: drop the parent's counts and re-record every socket the
    parent had claimed, making the child an explicit owner in its own right.

    RESIDUAL (narrower, documented): the child cannot know HOW MANY clients
    it inherited, so the re-adopted refcount is 1 per socket. Closing one of
    two inherited clients would drop the record while the other is still
    live. The load-bearing property — a parent SIGKILL cannot make a forked
    child's live server look orphaned — does hold.

    #4487 review: `_own_start_cache` is inherited too, and a stale entry for
    a pid the KERNEL later reassigns to this child would make `record_owner`
    stamp the child's record with a dead ancestor's start — `_owner_records`
    compares it against the real start, reads the record DEAD, and the reaper
    kills a live owner's server (the #1642 FIX 5 fail-open class). Drop it so
    the child resolves its own start fresh, exactly as the counts are redone.
    """
    _own_start_cache.clear()
    inherited = list(_owner_refcounts)
    _owner_refcounts.clear()
    # #4577: the child inherits the parent's lock fds, which refer to the
    # SAME open file descriptions. Those must not stay in the child's map:
    # `flock(LOCK_UN)` on a duplicate releases the lock for the PARENT too
    # (a lock belongs to the open file description, not the fd), so a child
    # `forget_owner` could unlock a still-live parent's server — the exact
    # #1557 fail-open this lock exists to prevent. Re-acquire a fresh
    # description for every inherited socket FIRST (a second SHARED lock is
    # compatible, so it succeeds while the inherited one is still held),
    # then close the inherited fds — which never leaves a free-lock window
    # for a concurrent reaper.
    inherited_locks = dict(_owner_lock_fds)
    _owner_lock_fds.clear()
    # Re-acquire for the union: a held fd whose refcount entry was somehow
    # missing must still get a fresh descriptor, or the child would drop a
    # lock it inherited without replacing it.
    for sock in dict.fromkeys([*inherited, *inherited_locks]):
        with contextlib.suppress(Exception):
            record_owner(sock)
    for fd in inherited_locks.values():
        with contextlib.suppress(OSError):
            os.close(fd)


if hasattr(os, "register_at_fork"):  # POSIX; absent on Windows
    os.register_at_fork(after_in_child=_adopt_owner_records_after_fork)


def owner_socket_of(client) -> str | None:
    """Socket path of the redislite server a client owns, or None.

    Accepts EITHER shape so record/forget stay symmetric: the guarded
    ``tortoise.FalkorDB`` wrapper (whose redislite server lives on the INNER
    client at ``.client``) and a raw redislite client (which owns
    ``socket_file`` directly). Host/port (server-mode) constructions have no
    ``socket_file`` and correctly yield None — there is no child to reap.

    #4487 review: the client's OWN ``.socket_file`` is read FIRST. An embedded
    redislite ``Redis`` exposes ``.client`` as a BOUND METHOD (its
    self-constructing clone helper), so the old
    ``getattr(client, "client", None) or client`` resolved a raw client's
    inner to that method, found no ``socket_file`` there, and returned None —
    silently breaking every RELEASE fallback for raw clients (the record the
    #4487 patch writes was then never released). A callable ``.client`` is
    never an inner client.
    """
    sock = getattr(client, "socket_file", None)
    if isinstance(sock, str) and sock:
        return sock
    inner = getattr(client, "client", None)
    if inner is None or callable(inner):
        return None
    sock = getattr(inner, "socket_file", None)
    return sock if isinstance(sock, str) and sock else None


def record_owner(socket_file: str | None) -> bool:
    """Record THIS process as a live owner of the server at ``socket_file``.

    Called from the guarded ``tortoise.FalkorDB`` constructor (the single
    embedded choke-point). Reference-counted PER PROCESS: several clients
    in one process share one record, so closing the first of two clients on
    a shared server must not drop the record that still protects the
    second (that would let the reaper kill a live co-tenant's server).
    Returns True when a record was created. Never raises — an unwritable
    socket dir simply leaves the server uninstrumented, and the reaper
    falls back to its global gate (fail closed).
    """
    if not socket_file:
        return False
    key = os.path.abspath(socket_file)
    if _owner_refcounts.get(key, 0) > 0:
        _owner_refcounts[key] += 1  # this process already owns the record
        return False
    try:
        os.makedirs(owner_record_dir(socket_file), exist_ok=True)
    except OSError:
        return False
    # #4487: this process's own start time is invariant — resolve it once
    # (see `_own_start_time`), not a `ps` fork on every construction.
    start = _own_start_time()
    # An undeterminable start time is stamped 'unknown'; _owner_records
    # treats that as LIVE (fail closed) — never as a dead owner.
    stamp = f"{os.getpid()}-{'unknown' if start is None else int(start)}"
    try:
        fd = os.open(os.path.join(owner_record_dir(socket_file), stamp),
                     os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    except FileExistsError:
        pass  # already recorded by this process' first client
    except OSError:
        return False
    _owner_refcounts[key] = _owner_refcounts.get(key, 0) + 1
    # #4577: hold the shared liveness lock alongside the record file. The
    # `key not in` guard keeps the fd map consistent with the refcount map —
    # re-acquiring when a descriptor is already held would overwrite (and
    # leak) it. A None fd is "no lock": the records stay the fallback.
    if key not in _owner_lock_fds:
        fd = _acquire_owner_lock(socket_file)
        if fd is not None:
            _owner_lock_fds[key] = fd
    return True


def forget_owner(socket_file: str | None) -> bool:
    """Release one client's claim on this process's owner record.

    Idempotent per client at the CALLER (the guarded wrapper's
    ``_t_release_owner``); here it decrements the per-process refcount and
    removes the record only when the LAST client on that server releases
    it. A shared server keeps the records of its other owner PROCESSES, so
    forgetting one never orphans it.
    """
    if not socket_file:
        return False
    key = os.path.abspath(socket_file)
    held = _owner_refcounts.get(key, 0)
    if held > 1:
        _owner_refcounts[key] = held - 1
        return False  # another client in this process still owns it
    _owner_refcounts.pop(key, None)
    # #4577: the LAST client in this process drops this process's shared
    # lock — the kernel-visible signal that this owner is gone. Released
    # here (after the count, before the record unlink) so the migration of
    # the two signals always overlaps; either order is safe because a free
    # lock only ever falls back to the records.
    _release_owner_lock(key)
    d = owner_record_dir(socket_file)
    try:
        names = os.listdir(d)
    except OSError:
        return False
    prefix = f"{os.getpid()}-"
    removed = False
    for n in names:
        if not n.startswith(prefix):
            continue  # '123-' never matches '1234-...' — the dash is the guard
        with contextlib.suppress(OSError):
            os.unlink(os.path.join(d, n))
            removed = True
    # server-scoped dir: drop it once the last owner is gone (another
    # owner's records may remain, so a failure here is expected).
    with contextlib.suppress(OSError):
        os.rmdir(d)
    return removed


# ── #4487: instrument EVERY redislite construction, not just the guarded one ─
#
# `record_owner` is called from the guarded `tortoise.FalkorDB` constructor
# (tortoise/__init__.py), so a spawn that goes through that choke-point is
# instrumented. But a RAW `redislite.falkordb_client.FalkorDB(...)` or
# `redislite.client.Redis(...)` bypasses the guard entirely and writes NO
# owner record. Measured 2026-09-21 on this host: an active lane's raw
# reproduction script left a live, detached redis-server with no
# `.tortoise-owners` dir — exactly the class the reaper cannot confirm under
# `--only-safe` while any suite is live (#4487).
#
# Patch redislite's OWN constructor seam — `RedisMixin.__init__`, the base of
# both `Redis` and `FalkorDB` — so EVERY construction in a process that
# imports tortoise records an owner, including raw ones. This mirrors the
# existing #3653 `_cleanup` patch above: installed once at import, additive,
# and it leaves a process that never imports tortoise untouched (the
# documented "non-tortoise users unaffected" boundary is preserved — the
# patch is a property of importing tortoise, not of importing redislite).
#
# The guarded constructor's own `record_owner` call is REMOVED in the same
# change: `record_owner` is refcounted per (process, socket path), so two
# writers for one client would make close() release only one claim and leave
# the record (with a LIVE pid) pinning the server forever — a fail-closed
# leak the reaper could never clear. ONE writer only.
_ORIGINAL_REDISLITE_INIT = None


def _install_owner_record_patch() -> None:
    """#4487: record an owner for every redislite construction (once).

    Wraps `RedisMixin.__init__` so that a client constructed by ANY caller —
    guarded or raw — records this process as an owner of the server it just
    started. Runs AFTER the original init (redislite sets `socket_file`
    inside it); a construction that aborts mid-init records nothing, matching
    the guard's previous behaviour. Never raises — a `record_owner` I/O
    failure must never break client construction.
    """
    global _ORIGINAL_REDISLITE_INIT
    try:
        from redislite.client import RedisMixin
    except Exception:  # redislite absent — nothing to patch
        return
    if getattr(RedisMixin, "_tortoise_owner_record_patch", False):
        return
    original = RedisMixin.__init__
    _ORIGINAL_REDISLITE_INIT = original

    def _init(self, *args, **kwargs):
        original(self, *args, **kwargs)
        # `record_owner` / `owner_socket_of` are defined above and resolved
        # at call time; the guard keeps construction unconditional.
        #
        # Resolve the socket from the object we are patching FIRST: this
        # seam fires on the object that OWNS the server (redislite's `Redis`,
        # including the inner client a `FalkorDB` wrapper builds), whose own
        # `.socket_file` is authoritative. `owner_socket_of` is the fallback
        # for any wrapper shape — it is NOT the primary read here because an
        # inner embedded `Redis` carries its own `.client` attribute, and
        # `owner_socket_of`'s `getattr(client, 'client', ...)` would then
        # follow that to a client with no `socket_file` and wrongly report
        # None (measured: the first cut of this patch wrote no record).
        try:
            sock = getattr(self, "socket_file", None)
            if not (isinstance(sock, str) and sock):
                sock = owner_socket_of(self)
            record_owner(sock)
        except Exception:
            pass

    RedisMixin.__init__ = _init
    RedisMixin._tortoise_owner_record_patch = True


# Installed at import, at the END of the module so `record_owner` and
# `owner_socket_of` are defined first. `tortoise/__init__.py` imports this
# module before it defines the guarded `FalkorDB`, so the patch is always in
# place before any tortoise construction.
_install_owner_record_patch()
