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
"""
from __future__ import annotations  # noqa: I001

import os

import socket
import tempfile

from tortoise.embedded_reaper import _is_ephemeral_dir


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
      3. This client is the last connection to the server (replicating
         redislite's own `_cleanup` guard; a failed probe is treated as
         last-client — the fire-and-forget then fails fast on a dead socket
         and the no-op is safe).

    The close itself is a fire-and-forget `SHUTDOWN NOSAVE` over the unix
    socket (~0.00s send; the server exits in ~0.05s). We do NOT wait for a
    response — that wait (plus redislite's serialized per-server polls) is
    the 3-4s/server cost this module eliminates. redislite's own atexit
    `_cleanup` then finds the server dead and no-ops fast.
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

    # Other clients still connected -> redislite's fast disconnect path
    # (no shutdown). The last of this server's clients does the shutdown.
    try:
        if client._connection_count() > 1:
            try:  # noqa: SIM105
                client.connection_pool.disconnect()
            except Exception:
                pass
            return True
    except Exception:
        pass  # probe failed -> assume last client (fail toward fast path)

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
        return True

    # Fire-and-forget: the server exits in ~0.05s. Do NOT send SIGTERM —
    # redis's SIGTERM handler performs a graceful SAVE, which would
    # reintroduce the slow per-server save we are eliminating.
    # Neutralize redislite's atexit _cleanup: it would otherwise see the
    # (now-dying) server's pidfile and poll the zombie for up to 10s per
    # server. Nulling pidfile makes `pid = self.pid` return None and the
    # whole cleanup block skip.
    client._tortoise_fast_closed = True
    _neutralize_redislite_cleanup(client)
    try:  # noqa: SIM105
        client.connection_pool.disconnect()
    except Exception:
        pass
    return True


def _neutralize_redislite_cleanup(client) -> None:
    """Make redislite's own atexit `_cleanup` a fast no-op for this client.

    `_cleanup` reads `self.pid` (a property over `self.pidfile`); with the
    pidfile gone it returns None and skips the shutdown + zombie poll that
    would otherwise cost ~3-10s per server at interpreter exit. The server
    is already dead (NOSAVE) — this only prevents the redundant slow path.
    """
    try:  # noqa: SIM105
        client.pidfile = None
    except Exception:
        pass


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
    # Probe the server's client count BEFORE touching the pool.
    try:
        count = client._connection_count()
    except Exception:
        return  # cannot determine sharing -> leave the server alone
    if count > 1:
        # Other clients (this process or another) share the server — drop
        # our connection only; the last owner's close/GC/exit shuts it down.
        try:  # noqa: SIM105
            client.connection_pool.disconnect()
        except Exception:
            pass
        return
    try:
        if atexit_fast_close(client):
            return
    except Exception:
        pass  # probe/gating failure -> fall through to the normal close
    try:  # noqa: SIM105
        client._cleanup()
    except Exception:
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
                continue
        except Exception:
            pass  # probe/gating failure -> fall through to the normal close
        t_close = getattr(client, "_t_close", None)
        if t_close is not None:
            try:  # noqa: SIM105
                t_close()
            except Exception:
                pass  # teardown context: never raise
            closed += 1
            continue
        cleanup = getattr(client, "_cleanup", None)
        if cleanup is not None:
            try:  # noqa: SIM105
                cleanup()
            except Exception:
                pass
            closed += 1
    return closed


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
