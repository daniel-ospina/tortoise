"""#9494: which module files this process loaded, and whether the repo moved on.

A long-lived process imports its module graph **once**. The MCP server, the HTTP
daemon and the standalone health server have no expiry, so a process started six
days ago keeps answering from the files it read then. Measured on 2026-10-21:
four live ``tortoise.mcp_server`` processes, the oldest started **2d 4h before**
``_ledger_record`` was added to ``tortoise/sdk.py`` — so every
``mcp__tortoise__*`` call failed with

    cannot import name '_ledger_record' from 'tortoise.sdk'

which reads as "the code is broken". The code was fine. The *process* was stale,
and nothing anywhere said so — no health field, no log line, and the error named
a symbol that exists in the repo.

This module makes that state OBSERVABLE and nothing more. It does not restart,
reload or refuse: those are runtime decisions (a reload can mid-flight break every
lane sharing the server, and a refuse-to-serve turns a stale process into an
outage). What it does is the part that was missing — a measurement.

## What is measured

A **per-module first-observation baseline**: the first time this process sees a
loaded ``tortoise`` module, its file's ``(mtime_ns, size)`` is recorded; a later
``check()`` re-stats every recorded file and reports the ones that no longer
match. Per-module rather than "the file is newer than process start" is
load-bearing: a module imported at 10:05 that was edited at 10:04 is FRESH, and a
start-time comparison would call it stale and hand the reader a restart that
cannot change anything.

Because mtime alone would false-alarm on a **content-identical rewrite** — which
is routine in this fleet (``git checkout <branch>``, ``git stash``/``lane_stash.py
pop``, ``git rebase`` all rewrite files byte-identically) — a mismatched stat is
then compared by **content digest**. A rewrite that left the bytes identical is
not drift: the process is serving exactly the repo's current code, so the stat
baseline is re-armed and the file is counted in ``mtime_only`` instead of
``changed``. Digest computation is deferred to the first ``check()`` (stat still
matching ⇒ the bytes are the ones that were loaded), so importing this module
costs a handful of ``stat`` calls, never a read of the package.

The baseline covers the graph that the README of this fix cares about: the
serving entrypoints call :func:`observe` at the END of their module body
(``tortoise.mcp_server``), so every module the server loaded at startup is
baselined at startup. Modules imported lazily at call time are baselined by the
next ``check()`` after that import; an edit inside that window is not seen. That
window is documented rather than closed with an import hook: closing it costs a
hook on every import in the process for a case the reported failure does not
need.

## Consumers (see the #9494 wiring)

* ``monitoring.metrics()`` → ``server_code`` — what ``tortoise_health`` and
  ``serve_health`` serve, so the first health call an agent makes can say
  "stale" before the first failing call does.
* ``mcp_server._safe`` — an ``ImportError``/``AttributeError`` that NAMES a
  module which actually drifted is answered with the drift AND the restart,
  instead of a symbol name that sends the reader into a correct file. The
  attribution is by PROVENANCE (the module the failure names), never by exception
  class alone: a ``ModuleNotFoundError`` (an absent extra), a ``NoneType``
  ``AttributeError``, or a symbol miss in a module that did NOT drift keeps
  reading as the code defect it is.

Scope, stated so it is not assumed: the HOSTED app builds its own ``/health``
dict (``hosted_api``) and does not route through ``monitoring.metrics()``, so that
endpoint does not carry ``server_code`` — the surfaces this reaches are
``tortoise_health`` (MCP, stdio and HTTP) and ``monitoring.serve_health``.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import sys
import threading
import time

_log = logging.getLogger("tortoise.code_freshness")

_PACKAGE = "tortoise"

#: When this process began. Reported so a stale report can be read as an AGE.
_STARTED_AT = time.time()

#: ``module name -> (file, mtime_ns, size, content digest | None)`` as first
#: observed by this process. The digest is filled in lazily by ``check()``.
_baseline: dict[str, tuple[str, int, int, str | None]] = {}
_baseline_at: float | None = None
#: ``(module, disk_mtime_ns, disk_size)`` already warned about — one WARNING per
#: distinct drift, never one per health call. Bounded: a process that drifts
#: hundreds of times must not grow this without limit.
_warned: set[tuple[str, int | None, str]] = set()
_WARNED_CAP = 512
#: ``module -> (disk mtime_ns, disk size, digest)`` — the last DISK snapshot we
#: hashed. Without it a persistently drifted module is re-read on every
#: ``check()``, i.e. on every health probe and every failing tool call, while
#: ``sdk.py`` alone is ~2 MB. The cache is keyed by the disk STAT, so a revert
#: (which moves the stat again) is re-read exactly once.
_disk: dict[str, tuple[int, int, str | None]] = {}
_lock = threading.Lock()

#: The sentence a stale report carries. Deliberately names the FIX, because the
#: reported failure mode is a reader who spends the time proving the code is
#: correct (`_ledger_record` was, all along).
HINT = (
    "this process is serving the module graph it imported at start; the files "
    "above changed on disk since. Restart the MCP server so it serves the repo's "
    "current code — the symbol you are looking for may exist on disk already."
)

#: The two symbol-shaped failure messages a stale process produces, and the
#: module each one NAMES (the provenance the hint is gated on).
_CANNOT_IMPORT = re.compile(r"cannot import name .* from '([\w.]+)'")
_NO_ATTRIBUTE = re.compile(r"module '([\w.]+)' has no attribute")


def _stat(path: str) -> tuple[int, int] | None:
    """``(mtime_ns, size)`` for ``path``, or ``None`` when it cannot be read."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _digest(path: str) -> str | None:
    """A content digest for ``path`` (16-byte blake2b), or ``None`` unreadable.

    Only ever called from ``check()`` — never from ``observe()`` — so importing
    this module reads no source files. 16 bytes is ample for equality here: the
    comparison is between two snapshots of the same file, not a security check.
    """
    try:
        with open(path, "rb") as fh:
            return hashlib.blake2b(fh.read(), digest_size=16).hexdigest()
    except OSError:
        return None


def _package_files() -> list[tuple[str, str]]:
    """``(module name, .py file)`` for every loaded ``tortoise`` module.

    Source files only: an extension module (``.so``) has no meaningful "newer
    source" relation, and a namespace module carries no ``__file__``.
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


def observe(deep: bool = False) -> int:
    """Record a baseline for every loaded module not yet observed.

    Returns the number of modules newly recorded. The serving entrypoints call
    this at the END of their module body (``tortoise.mcp_server``) so the whole
    startup graph is baselined at startup rather than at the first health call,
    and :func:`check` calls it again to pick up lazily imported modules.

    ``deep=True`` also hashes each file NOW (it reads the package — ~36 ms for
    the 6 MB startup graph of this repo). The serving entrypoints use it at BOOT,
    where that cost is invisible, because it removes the one window a shallow
    baseline cannot: a module observed with no digest whose file is rewritten
    byte-identically before the first ``check()``. With a boot digest that
    rewrite is recognised as content-identical instead of being reported stale
    (conservatively — see :func:`check`) for the process's whole life.
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
            _baseline[name] = (path, st[0], st[1], _digest(path) if deep else None)
            added += 1
        if added and _baseline_at is None:
            _baseline_at = time.time()
    return added


def _tracked_names() -> list[str]:
    with _lock:
        return sorted(_baseline)


def tracked() -> list[str]:
    """Names of the module files this process has a baseline for.

    Public so a caller (or a test) can assert WHICH graph the verdict covers
    without reaching into ``_baseline`` — the drift map is an implementation
    detail, the coverage is not.
    """
    observe()
    return _tracked_names()


def _display_path(path: str) -> str:
    """The repo-relative spelling of a tracked file, never an absolute one.

    ``metrics()`` is served to TENANTS (the ``tortoise_health`` MCP tool over
    HTTP), so an absolute ``/Users/<operator>/...`` path in the payload leaks the
    host's layout and user name. Two components identify the file
    (``tortoise/sdk.py``) and carry nothing about the host.
    """
    parts = path.replace(os.sep, "/").split("/")
    return "/".join(parts[-2:]) if len(parts) >= 2 else path


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _compose_hint(names: list[str], process_started: str | None) -> str:
    """WHICH modules drifted, how old the process is, and the fix.

    A hint that does not name the drifted module leaves the reader exactly where
    the issue's ``ImportError`` did — guessing.
    """
    shown = ", ".join(sorted(names))
    return (
        f"STALE PROCESS (not stale code): this server imported {shown} at start "
        f"and {'they have' if len(names) > 1 else 'it has'} changed on disk since "
        f"(process started {process_started}). {HINT}"
    )


def _disk_digest(name: str, path: str, st: tuple[int, int] | None) -> str | None:
    """The digest of the file AS IT IS ON DISK now, memoised by its stat.

    Re-reading is the expensive half of a drift check (a module of this repo is
    up to 2 MB), and a persistent drift is exactly the state this module exists
    to describe — so the drifted row must not cost a full read per health probe.
    A revert moves the stat again, which invalidates the memo.
    """
    if st is None:
        return None
    with _lock:
        cached = _disk.get(name)
    if cached is not None and cached[0] == st[0] and cached[1] == st[1]:
        return cached[2]
    fresh = _digest(path)
    with _lock:
        _disk[name] = (st[0], st[1], fresh)
    return fresh


def _drifted() -> tuple[list[dict], int]:
    """The FULL drift list (never capped) plus the content-identical count.

    ``check()`` caps what it publishes; ``staleness_hint()`` needs the whole set
    to decide whether a failure's named module is one that actually drifted.
    """
    observe()
    with _lock:
        items = list(_baseline.items())
    changed: list[dict] = []
    mtime_only = 0
    for name, (path, mtime_ns, size, blake) in items:
        st = _stat(path)
        if st == (mtime_ns, size):
            if blake is None:
                # Deferred: establish the baseline digest now. The stat still
                # matches, so these bytes ARE the bytes that were loaded.
                fresh = _digest(path)
                if fresh is not None:
                    with _lock:
                        _baseline[name] = (path, mtime_ns, size, fresh)
            continue
        disk_digest = _disk_digest(name, path, st)
        if st is not None and blake is not None and disk_digest == blake:
            # A content-identical rewrite (git checkout / stash round-trip /
            # rebase): the bytes did not move, so this is NOT drift. Re-arm the
            # stat baseline so the next real edit is measured from here.
            mtime_only += 1
            with _lock:
                _baseline[name] = (path, st[0], st[1], blake)
            continue
        # `content_verified` is False when this process never confirmed which
        # bytes it loaded (no digest yet — the server has not checked before).
        # Such a row is reported STALE anyway, deliberately: the two failure
        # directions are not symmetric. A conservative false alarm costs a
        # restart; a false pass is precisely the #9494 lie (an authoritative
        # answer computed by code that is not the repo's).
        changed.append({
            "module": name,
            "file": _display_path(path),
            "loaded_mtime": _iso(mtime_ns / 1e9),
            "disk_mtime": _iso(st[0] / 1e9) if st is not None else None,
            "removed": st is None,
            "content_verified": blake is not None,
        })
    return changed, mtime_only


def check() -> dict:
    """Measure the drift between the loaded module graph and the repo's files.

    Returns::

        {"stale": bool,                     # ANY tracked file changed since load
         "process_started_at": iso,         # how long this process has served
         "baseline_at": iso,                # first observation of the graph
         "tracked": int,                    # module files compared
         "changed": [{module, file, loaded_mtime, disk_mtime, removed}],
                                            # `file` is repo-relative, never absolute
         "changed_count": int,              # `changed` is capped at 20
         "mtime_only": int,                 # content-identical rewrites, NOT drift
         "unverified": int,                 # drifted rows whose loaded bytes were
                                            # never hashed (see `content_verified`)
         "hint": str | None}                # which modules drifted + the restart

    ``mtime_only`` exists so the mtime-vs-content distinction is VISIBLE: a
    rewrite that left the bytes identical is reported as such instead of being
    either a false alarm or a silent pass.
    """
    changed, mtime_only = _drifted()
    with _lock:
        tracked_count = len(_baseline)
    for row in changed:
        key = (row["module"], row["disk_mtime"], row["file"])
        # Membership test AND insert under the lock: `check()` is served by a
        # ThreadingHTTPServer and by concurrent tool calls, so an unlocked
        # test-then-add lets a burst of probes emit duplicate STALE PROCESS
        # lines — the invariant this set exists to hold ("one WARNING per
        # distinct drift"). The log call stays outside the lock so logging can
        # never hold it.
        with _lock:
            if key in _warned or len(_warned) >= _WARNED_CAP:
                continue
            _warned.add(key)
        _log.warning(
            "STALE PROCESS: %s changed on disk since this process imported it "
            "(%s -> %s). %s",
            row["module"], row["loaded_mtime"],
            row["disk_mtime"] or "removed", HINT,
        )
    started = _iso(_STARTED_AT)
    return {
        "stale": bool(changed),
        "process_started_at": started,
        "baseline_at": _iso(_baseline_at),
        "tracked": tracked_count,
        "changed": changed[:20],
        "changed_count": len(changed),
        "mtime_only": mtime_only,
        "unverified": sum(1 for row in changed if not row["content_verified"]),
        "hint": _compose_hint([row["module"] for row in changed], started) if changed else None,
    }


def _named_module(exc: BaseException) -> str | None:
    """The MODULE a symbol-shaped failure names — the hint's provenance.

    ``None`` means "this failure names no module we could have loaded", which is
    the fail-safe direction: an unrelated error must never be relabelled.
    """
    # A ``ModuleNotFoundError`` is an ``ImportError``, but it means the module is
    # ABSENT (a ``uv sync`` without the extra), and no restart can create it.
    if isinstance(exc, ModuleNotFoundError):
        return None
    if isinstance(exc, ImportError):
        name = getattr(exc, "name", None)
        if isinstance(name, str) and name:
            return name
        match = _CANNOT_IMPORT.search(str(exc))
        return match.group(1) if match else None
    if isinstance(exc, AttributeError):
        # Only the MODULE-shaped message carries provenance. `'NoneType' object
        # has no attribute 'get'` names no module, and answering it with
        # "restart the server" would steer the reader away from a real bug.
        match = _NO_ATTRIBUTE.match(str(exc))
        return match.group(1) if match else None
    return None


def staleness_hint(exc: BaseException) -> str | None:
    """A restart hint for a failure whose NAMED module has actually drifted.

    The reported failure — ``cannot import name '_ledger_record' from
    'tortoise.sdk'`` — names a symbol that exists in the repo, so the reader is
    sent into a correct file. This answers that failure with the measurement
    instead. ``None`` when the named module is not a drifted one: a symbol miss
    on a FRESH process (or in a module that did not move) is a real code defect
    and must keep reading as one.
    """
    owner = _named_module(exc)
    if owner is None:
        return None
    drifted, _ = _drifted()
    if not any(row["module"] == owner for row in drifted):
        return None
    return _compose_hint([row["module"] for row in drifted], _iso(_STARTED_AT))


def _reset_for_tests() -> None:
    """Forget the baseline and the warned set (tests only — never a serving path)."""
    global _baseline_at
    with _lock:
        _baseline.clear()
        _disk.clear()
        _warned.clear()
        _baseline_at = None


# The earliest observation this module can make, taken the moment a serving
# entrypoint pulls it in (`tortoise.monitoring` imports it, and `mcp_server`
# imports monitoring). SHALLOW: importing a diagnosis module must not read the
# package. The serving entrypoints then call `observe(deep=True)` once at boot
# (`tortoise.mcp_server`: end of module body, `main()`, `create_http_app`), which
# baselines the WHOLE startup graph AND hashes it — without that the 25 modules
# loaded long after monitoring (`tool_registry`, `mcp_auth`, `quota`) would be
# baselined days later, i.e. their edits would be invisible for exactly the
# window #9494 is about.
observe()
