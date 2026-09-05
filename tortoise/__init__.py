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
            super().__init__(*args, **kwargs)
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
            if atexit_fast_close(getattr(self, "client", self)):
                self._t_closed = True
                return
            self._t_close()

        def _t_close(self) -> None:
            """Idempotent close; safe from atexit or __exit__."""
            if getattr(self, "_t_closed", False):
                return
            self._t_closed = True
            try:  # noqa: SIM105
                self.close()
            except Exception:
                pass  # teardown context: never raise

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
