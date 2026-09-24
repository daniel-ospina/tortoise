"""Tortoise — live epistemic-graph extraction from transcripts.

M0 spike: file → extractor → JSONL event log → projection → static grid render.
See ../BUILD_PLAN.md. This is the spine only; storage, streaming, idempotency,
and the eval loop are later milestones.

GAP-15 / #7003: Conversation mining pipeline (mining.py) now wired.


Import-time loud-fail guard (issue #176, plan Task 8):
  `tortoise.FalkorDB` SUBCLASSES `redislite.falkordb_client.FalkorDB` and
  raises RuntimeError on relative paths. BEST-EFFORT: only code importing
  tortoise's re-export (or importing redislite AFTER `import tortoise`) is
  guarded. Direct redislite imports BEFORE tortoise are documented bypasses —
  the pre-commit grep (Child 3) is the source-level enforcement. We do NOT
  monkeypatch the redislite module globally (non-tortoise users unaffected).

  NOTE: the projection module imports redislite directly at its own
  choke-point (`projection/__init__.py:131`); Python resolves that to the
  ORIGINAL class regardless of this re-export. Protection for the projection
  path comes from FalkorProjection's hard-reject (Task 7) — this guard covers
  code importing `tortoise.FalkorDB` or importing redislite after tortoise.
"""
from __future__ import annotations

#: mirrors pyproject.toml (single source of truth); programmatic access for SDK clients.
#: #2208: moved OUT of the module docstring — 5a2b4eb4 added it inside the docstring
#: (lines 1-24), so `tortoise.__version__` was doc text, never an executable attr.
__version__ = "0.2.0"

import os

try:
    from redislite.falkordb_client import FalkorDB as _OriginalFalkorDB
except ModuleNotFoundError:  # pragma: no cover - dep-missing environment
    # falkordblite not installed: do NOT crash at import time, or the CLI
    # install guidance in `tortoise init` can never run (issue #716). The
    # subclass below falls back to a placeholder that raises a clear
    # ImportError at construction instead.
    _OriginalFalkorDB = None  # type: ignore[assignment]

from tortoise.config import RELATIVE_PATH_ERROR  # noqa: I001
from tortoise.fork_safety import (
    enforce_embedded_fork_safety,
    fork_safe_serverconfig,
)
# #1371: eager import registers the batch atexit flush (module-import time,
# before any client construction) so LIFO ordering runs it LAST.
from tortoise.embedded_lifecycle import atexit_fast_close


if _OriginalFalkorDB is not None:

    class FalkorDB(_OriginalFalkorDB):
        """Guarded subclass of redislite's FalkorDB.

        Raises RuntimeError when `path` is relative (never permitted — relative
        paths create per-CWD servers, the Category-3 leak). Absolute paths and
        no-arg construction pass through to the original.

        Issue #2204 (first-run): redislite writes its redis-server config with
        ``dir <db-path-parent>`` and the embedded daemon FATALs at config load
        when that directory does not exist (fresh machine — no ~/.tortoise
        yet). The data dir is created HERE, at the single embedded choke-point,
        BEFORE the server config is read — so `tortoise init` / `doctor` /
        any first embedded open on a clean machine works instead of printing a
        raw "FATAL CONFIG FILE ERROR". Idempotent (exist_ok). No-op for
        :memory:. Fails clean (OSError propagates as-is) only when the dir
        cannot be created at all (e.g. unwritable parent) — never a redislite
        subprocess FATAL.

        ``~``-prefixed paths are permitted and expanded via os.path.expanduser
        BEFORE forwarding — redislite derives its config ``dir`` verbatim from
        os.path.dirname(path) and never expands ``~`` itself (issue #2204
        review), so tilde callers previously died with the raw FATAL CONFIG
        error at config load.

        Issue #1005 (lifecycle): context-manager support + idempotent close +
        atexit registration so normal process exit never orphans the server.
        NOTE: no GC-time weakref.finalize here — the object IS the redislite
        client, and finalizer callbacks cannot dereference their own referent
        (it is already dead), so a finalizer could never reach close().
        Deterministic close is via `with`/explicit close; strays are covered
        by the reaper + conftest hygiene sweeps.
        """

        def __init__(self, *args, **kwargs):
            if args and args[0] is not None and isinstance(args[0], str):
                path = args[0]
                if path == ":memory:":
                    # redislite in-memory server — not a file path, exempt
                    # (mirrors config.py; #1005 lifecycle applies to
                    # file-backed servers only)
                    pass
                else:
                    # #2204: expand the path BEFORE anything else reads it.
                    # redislite derives its config ``dir`` verbatim from
                    # os.path.dirname(path) and does NOT expanduser itself, so
                    # an unexpanded "~" would still die with the raw
                    # "FATAL CONFIG FILE ERROR" this guard exists to kill.
                    # Expansion also resolves valid ``~user`` forms; a tilde
                    # that fails to resolve (unknown user) stays non-absolute
                    # and is rejected below like any other relative path.
                    path = os.path.expanduser(path)
                    # Reject relative paths AFTER expansion: the original
                    # relative-path RuntimeError contract is preserved (a
                    # plain relative input is unchanged by expanduser), while
                    # the round-1 expansion makes "~..." absolute and legal.
                    # Never permit a per-CWD relative db (Category-3 leak).
                    if not os.path.isabs(path):
                        raise RuntimeError(RELATIVE_PATH_ERROR.format(path=path))
                    # Create the data dir BEFORE redislite reads its config
                    # (see class docstring). Never fails on an existing dir;
                    # bare filenames (impossible after the absolute reject
                    # above) would no-op via dirname "" → ".".
                    data_dir = os.path.dirname(path)
                    os.makedirs(data_dir or ".", exist_ok=True)
                    args = (path, *args[1:])
            # #3845 part 2: keep the embedded daemon's verbosity above NOTICE
            # so a GRAPH.COPY module-fork child cannot block on the macOS
            # timezone rwlock it inherited held across fork(). See
            # tortoise/fork_safety.py for the measured producer.
            # host=/port= is redislite's server mode: no embedded daemon of
            # ours, so nothing is injected there (its __init__ forwards the
            # remaining kwargs straight to redis-py, which has no
            # serverconfig).
            embedded = "host" not in kwargs and "port" not in kwargs
            if embedded:
                kwargs["serverconfig"] = fork_safe_serverconfig(
                    kwargs.get("serverconfig"))
            super().__init__(*args, **kwargs)
            # #3845 part 2: serverconfig is a COLD-start setting only —
            # redislite reuses a live daemon from its .settings registry
            # without re-reading the config, so a daemon started before this
            # fix (or by an older client) keeps NOTICE and stays exposed.
            # Re-assert on the live connection; no-op when already correct.
            if embedded:
                enforce_embedded_fork_safety(self)
            import atexit as _atexit
            self._t_closed = False
            # #2203: track this client for the terminating-signal teardown
            # (SIGTERM/SIGHUP/ignored-SIGINT close every live embedded server
            # before the parent dies). Path-based constructions only — a
            # host=/port= construction is server mode (no redis child to
            # reap; its pool belongs to a live remote server). Presence check
            # mirrors redislite's own (RedisMixin.__init__: 'host' in kwargs
            # or 'port' in kwargs → server mode). Weak registry: never pins
            # the client, so #1475 close-on-GC stays intact.
            if "host" not in kwargs and "port" not in kwargs:
                from tortoise.embedded_lifecycle import register_embedded_client
                register_embedded_client(self)
                # #3599: record this process as a live OWNER of the server,
                # so the reaper can decide orphanhood per-server ("no live
                # owner") instead of via the global suite-marker gate that a
                # fleet host keeps permanently True. Written in the server's
                # own socket dir; removed by every close seam (_t_close,
                # _atexit_close, close_embedded_clients). owner_socket_of
                # resolves the INNER redislite client — the wrapper itself
                # has no socket_file (redislite's FalkorDB keeps its server
                # on self.client).
                from tortoise.embedded_lifecycle import owner_socket_of
                # Capture the socket path NOW: redislite mutates the inner
                # client during close(), so re-deriving it at release time
                # can yield None and silently strand the record.
                self._t_socket_file = owner_socket_of(self)
                # #4487: the owner RECORD itself is written by the
                # `RedisMixin.__init__` patch in embedded_lifecycle — which
                # covers RAW redislite constructions too, not just this
                # guarded one. Do NOT also call `record_owner` here: the
                # record is refcounted per (process, socket path), so two
                # writers for one client would leave the record (with a LIVE
                # pid) pinning the server after close() released only one
                # claim — a fail-closed leak the reaper could never clear.
            # #1371: route the atexit seam through the fast-close wrapper
            # (ephemeral test servers) so interpreter exit does not spend
            # 3-4s per leaked server on redislite's response-waiting close.
            # _t_close/close/__exit__ are unchanged — the fast path is only
            # reachable via this registration seam.
            _atexit.register(self._atexit_close)

        def _atexit_close(self) -> None:
            """#1371: atexit seam — collect ephemeral test servers for the
            batch flush first.

            Falls through to the normal _t_close when the fast path does not
            apply (non-ephemeral path, flag unset, other clients connected,
            or the socket is unreachable).
            """
            # #4214: `at_exit=True` — this registration is the `atexit`
            # seam only, so a spent exit budget stops the cascade instead of
            # letting it block `Py_FinalizeEx`.
            if atexit_fast_close(getattr(self, "client", self),
                                 at_exit=True):
                self._t_closed = True
                # #3599: the fast path bypasses close()/_t_close — release
                # the owner record here so a normal exit never leaves a
                # live-looking record for a server that is already gone.
                self._t_release_owner()
                return
            self._t_close()

        def _t_release_owner(self) -> None:
            """#3599: release this client's owner record claim (idempotent).

            Refcounted in embedded_lifecycle, so closing ONE of several
            clients on a shared server keeps the record that protects the
            others. The per-client flag makes the release idempotent across
            the three teardown seams (explicit close, atexit, signal), which
            would otherwise decrement twice and drop a record the process
            still needs.
            """
            if getattr(self, "_t_owner_released", False):
                return
            self._t_owner_released = True
            from tortoise.embedded_lifecycle import forget_owner, owner_socket_of
            sock = getattr(self, "_t_socket_file", None) or owner_socket_of(self)
            forget_owner(sock)

        def close(self, *args, **kwargs):
            """#3599: release the owner-record claim on the PUBLIC close seam.

            The wrapper inherits redislite's ``close()`` (a redis-py pool
            disconnect + ``_cleanup``), which has no hook for our owner
            record. Without this override a direct ``db.close()`` bypasses
            ``_t_close`` entirely and strands the record until interpreter
            exit. Idempotent via ``_t_release_owner``'s per-client flag, so
            the ``_t_close`` path (which calls ``close()`` and then
            releases) stays correct.

            #3653: redislite's ``_cleanup`` deletes the socket dir whenever
            its own ``_connection_count() <= 1``, but that count is 0 once
            the shared ``.settings`` registry file is gone — so closing one
            client tore down a LIVE shared server (killing co-tenants'
            seeded state, and surfacing as ``redis.socket ... No such file
            or directory`` / a start-time ``FATAL CONFIG FILE ERROR``). Guard
            the destructive path with the registry-independent co-tenant
            test; a shared server gets a pool disconnect only, exactly like
            redislite's own shared-server branch.

            #3653 F1: the guard above only covers THIS seam. redislite's
            OWN atexit-registered ``_cleanup`` and ``__del__`` still run,
            and with the shared registry file gone its
            ``_connection_count()`` reads 0 — so they would stop the live
            server and ``rmtree`` its socket dir from under a live
            co-tenant after all. Neutralize them on the shared path too.
            #3653 F3: redislite's ``_cleanup`` only rmtrees inside
            ``if self.pid:``, so a server that was already dead strands its
            ephemeral dir — reclaim it here.
            """
            try:
                inner = getattr(self, "client", None)
                if inner is not None:
                    from tortoise.embedded_lifecycle import (
                        _neutralize_redislite_cleanup,
                        _remove_ephemeral_socket_dir,
                        _server_pid,
                        cotenant_holds_server,
                        disconnect_only,
                    )
                    if cotenant_holds_server(inner):
                        disconnect_only(inner)
                        _neutralize_redislite_cleanup(inner)
                        return None
                    rdir = getattr(inner, "redis_dir", None)
                    sock_path = getattr(inner, "socket_file", None)
                    pid_before = _server_pid(inner)
                    try:
                        return super().close(*args, **kwargs)
                    finally:
                        # #3653: `cotenant_holds_server()` above already
                        # dropped this client's pool (its F4 probe). If the
                        # subsequent `_cleanup()` aborts — e.g. the client is
                        # a partially-initialized redislite `Redis` with no
                        # `connection_pool`, or its socket dir has vanished —
                        # the live pidfile is left behind and `__del__` runs
                        # `_cleanup` again and throws. Neutralize
                        # unconditionally so ANY client whose pool the guard
                        # dropped reaches `__del__` already neutralized.
                        # Idempotent: a no-op when `_cleanup` already nulled
                        # the pidfile (the success path).
                        _neutralize_redislite_cleanup(inner)
                        if not pid_before:
                            _remove_ephemeral_socket_dir(rdir, sock_path)
                return super().close(*args, **kwargs)
            finally:
                self._t_release_owner()

        def _t_close(self) -> None:
            """Idempotent close; safe from atexit or __exit__."""
            if getattr(self, "_t_closed", False):
                return
            self._t_closed = True
            try:  # noqa: SIM105
                self.close()
            except Exception:
                pass  # teardown context: never raise
            # #3599: release the owner record on EVERY close path, including
            # the exception path above (a failed shutdown still means this
            # process no longer owns the server).
            self._t_release_owner()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._t_close()
            return False

else:

    class FalkorDB:
        """Placeholder for when falkordblite is absent (issue #716).

        Construction raises ImportError so the CLI's install guidance is
        reachable when the dependency is actually missing, instead of a raw
        traceback at import time.
        """

        def __init__(self, *args, **kwargs):
            raise ImportError(
                "falkordblite is not installed — embedded mode requires it. "
                "Run: pip install falkordblite"
            )
