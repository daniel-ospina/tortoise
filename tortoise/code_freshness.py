"""#9494: which module files this process loaded, and whether the repo moved on.

A long-lived process imports its module graph **once**. The MCP server, the
HTTP daemon and the standalone health server have no expiry, so a process
started six days ago keeps answering from the files it read then. Measured on
2026-10-21: four live ``tortoise.mcp_server`` processes, the oldest started
**2d 4h before** ``_ledger_record`` was added to ``tortoise/sdk.py`` — so every
``mcp__tortoise__*`` call failed with

    cannot import name '_ledger_record' from 'tortoise.sdk'

which reads as "the code is broken". The code was fine. The *process* was
stale, and nothing anywhere said so — no health field, no log line, and the
error named a symbol that exists in the repo.

This module makes that state OBSERVABLE and nothing more. It does not restart,
reload or refuse: those are runtime decisions (a reload can mid-flight break
every lane sharing the server, and a refuse-to-serve turns a stale process into
an outage). What it does is the part that was missing — a measurement.

The measurement is a **per-module first-observation baseline**: the first time
this process sees a loaded ``tortoise`` module, its file's ``(mtime_ns, size)``
is recorded; ``check()`` re-stats every recorded file and reports the ones that
no longer match. Comparing against a per-module baseline (rather than "the file
is newer than process start") is what keeps a LAZILY imported module honest: a
module imported at 10:05 that was edited at 10:04 is FRESH, and a start-time
comparison would wrongly call it stale.

Known, documented window: a module that is imported and then edited *before*
this process's first ``check()`` after that import is recorded at its edited
state, so that edit is not seen. Shrinking it to zero would need an import
hook; the smallest fix for the reported failure (a symbol added days into a
long-lived server) does not, and ``observe()`` is called at import time of
``tortoise.monitoring`` and ``tortoise.mcp_server`` so the window is
call-scoped, never session-scoped.

Consumers (see the #9494 wiring):

* ``monitoring.metrics()`` → ``server_code`` — what ``tortoise_health`` and
  ``serve_health`` serve, so the first health call an agent makes can say
  "stale".
* ``mcp_server._safe`` — an ``ImportError``/``AttributeError`` raised by a
  stale process is answered with the measured drift AND the restart, instead of
  a symbol name that sends the reader into a correct file.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time

_log = logging.getLogger("tortoise.code_freshness")

_PACKAGE = "tortoise"

#: When this process began. Reported so a stale report can be read as an AGE.
_STARTED_AT = time.time()

#: ``module name -> (file, mtime_ns, size)`` as first observed by this process.
_baseline: dict[str, tuple[str, int, int]] = {}
_baseline_at: float | None = None
#: ``(module, disk_mtime_ns, disk_size)`` already warned about — one WARNING
#: per distinct drift, never one per health call.
_warned: set[tuple[str, int | None, int | None]] = set()
_WARNED_CAP = 512
_lock = threading.Lock()

#: The sentence a stale report carries. Deliberately names the FIX, because the
#: reported failure mode is a reader who spends the time proving the code is
#: correct (`_ledger_record` was, all along).
HINT = (
    "this process is serving the module graph it imported at start; the files "
    "above changed on disk since. Restart the MCP server so it serves the repo's "
    "current code — the symbol you are looking for may exist on disk already."
)


def _compose_hint(names: list[str], process_started: str | None) -> str:
    """The report's ``hint``: WHICH modules drifted, how old the process is,
    and the fix. A hint that does not name the drifted module leaves the reader
    exactly where the issue's ``ImportError`` did — guessing."""
    shown = ", ".join(sorted(names))
    return (
        f"STALE PROCESS (not stale code): this server imported {shown} at start "
        f"and {'they have' if len(names) > 1 else 'it has'} changed on disk since "
        f"(process started {process_started}). {HINT}"
    )


def _stat(path: str) -> tuple[int, int] | None:
    """``(mtime_ns, size)`` for ``path``, or ``None`` when it cannot be read."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _package_files() -> list[tuple[str, str]]:
    """``(module name, .py file)`` for every loaded ``tortoise`` module.

    Source files only: an extension module (``.so``) has no meaningful
    "newer source" relation, and a namespaceless module carries no ``__file__``.
    """
    out: list[tuple[str, str]] = []
    for name, module in list(sys.modules.items()):
        if name != _PACKAGE and not name.startswith(_PACKAGE + "."):
            continue
        path = getattr(module, "__file__", None)
        if not path or not path.endswith(".py"):
            continue
        out.append((name, path))
    return out


def observe() -> int:
    """Record a baseline for every loaded module not yet observed.

    Returns the number of modules newly recorded. Called at import time by the
    serving entrypoints (``tortoise.monitoring``, ``tortoise.mcp_server``) and
    at the start of every :func:`check`.
    """
    global _baseline_at
    added = 0
    with _lock:
        for name, path in _package_files():
            if name in _baseline:
                continue
            st = _stat(path)
            if st is None:
                continue
            _baseline[name] = (path, st[0], st[1])
            added += 1
        if added and _baseline_at is None:
            _baseline_at = time.time()
    return added


def tracked() -> list[str]:
    """Names of the module files this process has a baseline for.

    Public so a caller (or a test) can assert WHICH graph the verdict covers
    without reaching into ``_baseline`` — the drift map is an implementation
    detail, the coverage is not.
    """
    observe()
    with _lock:
        return sorted(_baseline)


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def check() -> dict:
    """Measure the drift between the loaded module graph and the repo's files.

    Returns::

        {"stale": bool,                     # ANY tracked file changed since load
         "process_started_at": iso,         # how long this process has served
         "baseline_at": iso,                # first observation of the graph
         "tracked": int,                    # module files compared
         "changed": [{module, file, loaded_mtime, disk_mtime, removed}],
         "changed_count": int,              # len(changed) is capped at 20
         "hint": str | None}                # the restart, when stale
    """
    observe()
    with _lock:
        items = list(_baseline.items())
    changed: list[dict] = []
    fresh_drifts: list[tuple[str, int | None, int | None, str | None]] = []
    for name, (path, mtime_ns, size) in items:
        st = _stat(path)
        if st is not None and st == (mtime_ns, size):
            continue
        disk_mtime = st[0] if st else None
        disk_size = st[1] if st else None
        loaded_iso = _iso(mtime_ns / 1e9)
        changed.append({
            "module": name,
            "file": path,
            "loaded_mtime": loaded_iso,
            "disk_mtime": _iso(disk_mtime / 1e9) if disk_mtime is not None else None,
            "removed": st is None,
        })
        fresh_drifts.append((name, disk_mtime, disk_size, loaded_iso))
    for name, disk_mtime, disk_size, loaded_iso in fresh_drifts:
        key = (name, disk_mtime, disk_size)
        if key in _warned or len(_warned) >= _WARNED_CAP:
            continue
        _warned.add(key)
        _log.warning(
            "STALE PROCESS: %s changed on disk since this process imported it "
            "(%s -> %s). %s",
            name,
            loaded_iso or "?",
            _iso(disk_mtime / 1e9) if disk_mtime is not None else "removed",
            HINT,
        )
    started = _iso(_STARTED_AT)
    return {
        "stale": bool(changed),
        "process_started_at": started,
        "baseline_at": _iso(_baseline_at),
        "tracked": len(items),
        "changed": changed[:20],
        "changed_count": len(changed),
        "hint": _compose_hint([row["module"] for row in changed], started) if changed else None,
    }


def staleness_hint(exc: BaseException) -> str | None:
    """A restart hint for a symbol-shaped failure **only when drift is measured**.

    The reported failure — ``cannot import name '_ledger_record' from
    'tortoise.sdk'`` — names a symbol that exists in the repo, so the reader is
    sent into a correct file. This answers the same failure with the measurement
    instead. ``None`` when the process is not stale: an ``ImportError`` on a
    FRESH process is a real code defect and must keep reading as one.
    """
    if not isinstance(exc, (ImportError, AttributeError)):
        return None
    report = check()
    if not report["stale"]:
        return None
    return report["hint"]


def _reset_for_tests() -> None:
    """Forget the baseline and the warned set (tests only — never a serving path)."""
    global _baseline_at
    with _lock:
        _baseline.clear()
        _warned.clear()
        _baseline_at = None


# The earliest observation this module can make: everything already imported
# when the serving entrypoint pulls this in (``tortoise.monitoring`` imports it,
# and ``mcp_server`` imports monitoring, so this runs before either serves).
observe()
