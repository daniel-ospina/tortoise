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
- ONLY when `TORTOISE_FAST_ATEXIT=1` (opt-in; set by tests/conftest.py and
  the CI workflow env — never in hosted/production paths).
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
"""
from __future__ import annotations  # noqa: I001

import os
import contextlib

import shutil
import socket
import tempfile

from tortoise.embedded_reaper import OWNERS_DIRNAME, _is_ephemeral_dir


def _is_ephemeral_test_server(client) -> bool:
    """True when the client's DB dir sits in an ephemeral test tree.

    Mirrors the reaper's containment check on BOTH sides through realpath
    (a symlinked TMPDIR would otherwise fail the strict relative_to test and
    silently disable the fast path — safe direction, but slow).
    """
    dbdir = getattr(client, "dbdir", None)
    if not dbdir:
        return False
    tmpdir_real = os.path.realpath(tempfile.gettempdir())
    dbdir_real = os.path.realpath(dbdir)
    return _is_ephemeral_dir(dbdir_real, tmpdir_real)


def atexit_fast_close(client) -> bool:
    """Fast-close an ephemeral test-tree redislite client at interpreter exit.

    Returns True when the close was handled by the fast path (or there was
    nothing to do); False when the caller must fall through to the normal
    close().

    Gating (all three must hold):
      1. TORTOISE_FAST_ATEXIT=1 (opt-in flag).
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
    if os.environ.get("TORTOISE_FAST_ATEXIT") != "1":
        return False
    if not _is_ephemeral_test_server(client):
        return False

    # Already handled by an earlier seam for the same server (the projection
    # and the db wrapper both register exit handlers) — skip the liveness
    # probe entirely: on a NOSAVEd server redis-py's reconnect-retry on the
    # stale connection costs ~3.9s per server (the CI tail we are removing).
    if getattr(client, "_tortoise_fast_closed", False):
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
    # 10,711 orphaned tempdirs.
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
    """
    tmpdir = os.path.realpath(tempfile.gettempdir())
    candidates = []
    if redis_dir:
        candidates.append(redis_dir)
    if socket_file:
        candidates.append(os.path.dirname(os.path.abspath(socket_file)))
    removed = False
    for d in candidates:
        try:
            real = os.path.realpath(d)
            if not _is_ephemeral_dir(real, tmpdir):
                continue
            shutil.rmtree(real, ignore_errors=True)
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
    client = getattr(db, "client", db)
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
        if atexit_fast_close(client):
            _release_owner_quietly(db)
            return
    except Exception:
        pass  # probe/gating failure -> fall through to the normal close
    try:  # noqa: SIM105
        client._cleanup()
    except Exception:
        pass
    # #3653 F3: redislite's `_cleanup` only rmtrees inside `if self.pid:`,
    # so a server that was already dead (pid 0) strands its dir. Reclaim it
    # here — the fast path above already does this for the CI-default case,
    # this covers the flag-off / non-ephemeral fall-through.
    if not pid_before:
        _remove_ephemeral_socket_dir(rdir, sock_path)
    _release_owner_quietly(db)


def _release_owner_quietly(db) -> None:
    """#3599: release a client's owner claim from GC/**non-raising** contexts.

    Prefers the guarded wrapper's idempotent ``_t_release_owner``; a raw
    redislite client has no such method and falls back to a direct
    ``forget_owner`` on its own socket path.
    """
    try:
        release = getattr(db, "_t_release_owner", None)
        if release is not None:
            release()
            return
        forget_owner(owner_socket_of(db))
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
      1. the #1371 ephemeral fast-close (NOSAVE, only under
         TORTOISE_FAST_ATEXIT=1 for test-tree servers) and
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
        inner = getattr(client, "client", client)
        try:
            if atexit_fast_close(inner):
                closed += 1
                _release_owner(client, inner)
                continue
        except Exception:
            pass  # probe/gating failure -> fall through to the normal close
        t_close = getattr(client, "_t_close", None)
        if t_close is not None:
            try:  # noqa: SIM105
                t_close()
            except Exception:
                pass  # teardown context: never raise
            _release_owner(client, inner)
            closed += 1
            continue
        cleanup = getattr(client, "_cleanup", None)
        if cleanup is not None:
            try:  # noqa: SIM105
                cleanup()
            except Exception:
                pass
            closed += 1
        _release_owner(client, inner)
    return closed


def _release_owner(client, inner) -> None:
    """#3599: release a client's owner-record claim (never raises).

    Prefers the guarded wrapper's idempotent ``_t_release_owner`` (which
    also handles the refcount when one process holds several clients on a
    shared server); a raw redislite client has no such method and falls
    back to a direct ``forget_owner``.
    """
    try:
        release = getattr(client, "_t_release_owner", None)
        if release is not None:
            release()
            return
        forget_owner(owner_socket_of(inner) if inner is not None else None)
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
    """
    inherited = list(_owner_refcounts)
    _owner_refcounts.clear()
    for sock in inherited:
        with contextlib.suppress(Exception):
            record_owner(sock)


if hasattr(os, "register_at_fork"):  # POSIX; absent on Windows
    os.register_at_fork(after_in_child=_adopt_owner_records_after_fork)


def owner_socket_of(client) -> str | None:
    """Socket path of the redislite server a client owns, or None.

    Accepts EITHER shape so record/forget stay symmetric: the guarded
    ``tortoise.FalkorDB`` wrapper (whose redislite server lives on the INNER
    client at ``.client``) and a raw redislite client (which owns
    ``socket_file`` directly). Host/port (server-mode) constructions have no
    ``socket_file`` and correctly yield None — there is no child to reap.
    """
    inner = getattr(client, "client", None) or client
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
    # Import at call time: `_process_start_time` shells out to `ps`, and the
    # reaper module is already a module-level import here — this keeps the
    # acquisition localized and skippable.
    from tortoise.embedded_reaper import _process_start_time
    try:
        start = _process_start_time(os.getpid())
    except Exception:
        start = None
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
