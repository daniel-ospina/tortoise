"""Tortoise — live epistemic-graph extraction from transcripts.

M0 spike: file → extractor → JSONL event log → projection → static grid render.
See ../BUILD_PLAN.md. This is the spine only; storage, streaming, idempotency,
and the eval loop are later milestones.

GAP-15 / #7003: Conversation mining pipeline (mining.py) now wired.


__version__ = "0.2.0"  # mirrors pyproject.toml; programmatic access for SDK clients
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

import os

try:
    from redislite.falkordb_client import FalkorDB as _OriginalFalkorDB
except ModuleNotFoundError:  # pragma: no cover - dep-missing environment
    # falkordblite not installed: do NOT crash at import time, or the CLI
    # install guidance in `tortoise init` can never run (issue #716). The
    # subclass below falls back to a placeholder that raises a clear
    # ImportError at construction instead.
    _OriginalFalkorDB = None  # type: ignore[assignment]

from tortoise.config import RELATIVE_PATH_ERROR
# #1371: eager import registers the batch atexit flush (module-import time,
# before any client construction) so LIFO ordering runs it LAST.
from tortoise.embedded_lifecycle import atexit_fast_close


if _OriginalFalkorDB is not None:

    class FalkorDB(_OriginalFalkorDB):
        """Guarded subclass of redislite's FalkorDB.

        Raises RuntimeError when `path` is relative (never permitted — relative
        paths create per-CWD servers, the Category-3 leak). Absolute paths and
        no-arg construction pass through to the original.

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
                elif not os.path.isabs(path) and not path.startswith("~"):
                    raise RuntimeError(RELATIVE_PATH_ERROR.format(path=path))
            super().__init__(*args, **kwargs)
            import atexit as _atexit
            self._t_closed = False
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
            try:
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
