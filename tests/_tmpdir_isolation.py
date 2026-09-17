"""#3752 — per-session private temp root: stop suite debris, scope discovery.

Two defects, one root cause. The suite wrote every scratch dir into the
**shared** system temp dir (``TMPDIR`` grew 12,290 -> 35,195 entries in ~6h;
``tortoise_test_*`` / ``tortoise_sdk_test_*`` / ``tortoise_w2_*`` /
``tortoise-lifecycle-*`` / ``tmp*`` accounted for ~2,000 of them), and
socket/pid discovery then **scanned that same shared tree** — a ``find``
over 12k+ entries at 53% CPU for two minutes, and a lookup that can match
another test's or another session's socket.

This module gives the whole pytest process ONE private temp root:

* ``install_session_tmpdir()`` creates ``<host tempdir>/tt_<8 random>``
  (``tempfile.mkdtemp``'s suffix, which is ``[a-z0-9_]``, not hex) and
  points ``tempfile.tempdir`` **and** ``TMPDIR`` at it. Every
  ``tempfile.mkdtemp()`` in the suite — and every child process that inherits
  the env, redislite servers included — then lands inside that one root.
* the root's basename carries the ``tt_`` prefix, which is already in
  ``tortoise.embedded_reaper.EPHEMERAL_PREFIXES``, so servers rooted there
  keep the exact classification they had under the shared temp dir.
* ``teardown_session_tmpdir()`` removes the root wholesale, so a full suite
  run leaves **zero** new entries in the shared temp dir — the leak class is
  fixed structurally rather than per test file.
* ``scan_root()`` is the asserting accessor test discovery must use: it
  returns the private root and REFUSES to hand back the shared temp dir.
* ``install_scan_guard()`` turns any scan of the **shared** temp dir (or an
  ancestor of it) by ``os.scandir`` / ``os.walk`` / ``os.listdir`` into a
  loud failure naming the offender, so a new test cannot reintroduce one.

Host-global coordination deliberately does NOT move: ``TMPDIR`` is redirected
only for the suite's scratch space, and ``TORTOISE_HOST_TMPDIR`` is exported
so ``tortoise.embedded_reaper.ACTIVE_SUITES_DIR`` (the cross-process
active-suite marker dir a production/cron sweep consults) stays on the host
temp dir. A per-session marker dir would make a live suite invisible to the
host reaper — see ``_host_coordination_tmpdir`` in ``embedded_reaper``.

Import-pure by construction: importing this module must not touch the
filesystem. ``install_session_tmpdir()`` is the explicit side effect, and
``tests/conftest.py`` calls it at import time — BEFORE
``tortoise.embedded_reaper`` is imported, since that module resolves
``ACTIVE_SUITES_DIR`` / ``_LOCK_PATH`` once at import.
"""
from __future__ import annotations

import atexit
import contextlib
import os
import shutil
import tempfile

# ── the host (shared) temp dir, captured BEFORE any redirect ─────────────
# Ordering matters: this is read at import, and the redirect below caches a
# different value into tempfile.tempdir. Everything that must keep referring
# to the real shared temp dir uses HOST_TMPDIR, never tempfile.gettempdir().
HOST_TMPDIR = os.path.realpath(tempfile.gettempdir())

# Marker file written inside the root so a SIGKILLed run's root is
# attributable and reclaimable by the next run (sweep_stale_session_roots).
_PID_MARKER = ".session-pid"

# Prefix that means "this is a pytest scratch dir" — must stay consistent
# with tortoise.embedded_reaper.EPHEMERAL_PREFIXES, which classifies servers
# rooted under the temp dir. "tt_" is already a member of that tuple (the
# long-standing ephemeral-tree convention this module now centralises).
_ROOT_PREFIX = "tt_"

_SESSION_TMPDIR: str | None = None
_GUARD_INSTALLED = False
_ORIGINALS: dict[str, object] = {}

# The unpatched primitives, captured at import (before install_scan_guard).
# Enumerating the shared temp dir is occasionally legitimate — reclaiming a
# SIGKILLed run's root is the one case — and the guard below would otherwise
# refuse the very call that does the reclaiming.
_ORIGINAL_SCANDIR = os.scandir


def host_tempdir_entries():
    """Enumerate the shared temp dir with the UNGUARDED scandir.

    The only sanctioned way to look inside the shared temp dir from test
    code. Every other enumeration is either a #3752 defect (discovery) or
    belongs in a call to this helper.
    """
    return _ORIGINAL_SCANDIR(HOST_TMPDIR)


def session_tmpdir() -> str | None:
    """The private session root, or None when isolation was never installed."""
    return _SESSION_TMPDIR


def install_session_tmpdir() -> str:
    """Create the private session root and redirect temp resolution into it.

    Idempotent — a second call returns the existing root (a test module may
    import this and call it defensively; the suite must never end up with two
    roots, only one of which teardown removes).

    Sets BOTH:
    - ``tempfile.tempdir``: ``gettempdir()`` caches, so an already-primed
      process would ignore a bare ``TMPDIR`` change.
    - ``os.environ['TMPDIR']``: child processes (redislite daemons, the
      subprocess writers/readers, nested pytest runs) resolve their own temp
      dir from the env, and must be contained too.
    """
    global _SESSION_TMPDIR
    if _SESSION_TMPDIR is not None:
        return _SESSION_TMPDIR

    root = os.path.realpath(tempfile.mkdtemp(prefix=_ROOT_PREFIX,
                                             dir=HOST_TMPDIR))
    try:
        with open(os.path.join(root, _PID_MARKER), "w") as fh:
            fh.write(str(os.getpid()))
    except OSError:
        pass

    _SESSION_TMPDIR = root
    # The host coordination dir must stay host-global (see module docstring).
    os.environ["TORTOISE_HOST_TMPDIR"] = HOST_TMPDIR
    os.environ["TMPDIR"] = root
    tempfile.tempdir = root
    atexit.register(teardown_session_tmpdir)
    return root


def teardown_session_tmpdir() -> None:
    """Remove the private root (one rmtree) and drop the redirect.

    Never raises: teardown must not convert a green suite red. Restores the
    process temp resolution so a second ``pytest.main()`` in the same
    interpreter starts clean.
    """
    global _SESSION_TMPDIR
    root = _SESSION_TMPDIR
    _SESSION_TMPDIR = None
    try:
        if os.environ.get("TMPDIR") == root:
            os.environ["TMPDIR"] = HOST_TMPDIR
        tempfile.tempdir = None
    except Exception:
        pass
    if not root:
        return
    with contextlib.suppress(Exception):  # teardown never fails the suite
        shutil.rmtree(root, ignore_errors=True)
    _prune_host_coordination_dir()


def _prune_host_coordination_dir() -> None:
    """Remove ``<host>/.tortoise`` only when this run left it EMPTY.

    The marker dir is host-global by design (see module docstring), so the
    suite would otherwise leave a permanent entry behind on a pristine host.
    ``os.rmdir`` refuses a non-empty dir, which is exactly the guard we want:
    a pre-existing ``.reaper.lock`` (production, or an older run) is never
    touched.
    """
    coordination = os.path.join(HOST_TMPDIR, ".tortoise")
    for path in (os.path.join(coordination, "active_suites"), coordination):
        try:  # noqa: SIM105
            os.rmdir(path)
        except OSError:
            pass


def sweep_stale_session_roots() -> list[str]:
    """Reclaim roots left by a SIGKILLed suite (a ``tt_`` dir whose recorded
    pid is dead). Best-effort, never raises.

    A suite killed by SIGKILL/segfault cannot run its own teardown, so a root
    would otherwise survive. This runs at install time (the next suite
    reclaims it) and mirrors the reaper's own dead-pid reasoning rather than
    an age heuristic: a *live* pid means a concurrent suite, whose root is
    not ours to delete.
    """
    reclaimed: list[str] = []
    try:
        entries = list(host_tempdir_entries())
    except OSError:
        return reclaimed
    for entry in entries:
        if not entry.is_dir(follow_symlinks=False):
            continue
        if not entry.name.startswith(_ROOT_PREFIX):
            continue
        try:
            with open(os.path.join(entry.path, _PID_MARKER)) as fh:
                pid = int(fh.read().strip())
        except (OSError, ValueError):
            continue  # not ours (no marker) — never delete on a guess
        try:
            os.kill(pid, 0)
            continue  # live owner: a concurrent suite's root
        except PermissionError:
            continue  # live process we may not signal
        except ProcessLookupError:
            pass
        shutil.rmtree(entry.path, ignore_errors=True)
        reclaimed.append(entry.path)
    return reclaimed


# ── the scan guard: refuse to discover through the SHARED temp dir ───────

class SharedTmpdirScanError(AssertionError):
    """A test tried to discover files by scanning the shared temp dir.

    Subclasses AssertionError so it surfaces as a test failure with a
    readable message rather than a bare exception.
    """


def _is_under(child: str, parent: str) -> bool:
    return child.startswith(parent.rstrip(os.sep) + os.sep)


def _is_host_tempdir_scope(path) -> bool:
    """True when ``path`` IS the host temp dir or an ANCESTOR of it.

    Descendants are allowed on purpose: ``<host>/.tortoise/active_suites`` is
    the host-global marker dir, and the session root itself lives under the
    host temp dir. Only scanning the shared tree (or above it) is the defect.
    """
    try:
        target = os.path.realpath(os.fspath(path))
    except (TypeError, ValueError):
        return False
    return target == HOST_TMPDIR or _is_under(HOST_TMPDIR, target)


def _guard_reason() -> str:
    return (
        "scanning the SHARED temp dir is forbidden in tests (#3752): this is "
        "the non-hermetic, cross-contaminating, O(whole-host) discovery that "
        f"the private session temp root exists to prevent ({HOST_TMPDIR}). "
        "Use tests._tmpdir_isolation.scan_root() (the private per-session "
        "root), or the test's own tmp_path, instead."
    )


def install_scan_guard() -> None:
    """Fail loudly on any in-process scan of the shared temp dir.

    Patches ``os.scandir`` / ``os.walk`` / ``os.listdir``. ``os.walk``
    resolves ``os.scandir`` at call time, so patching all three is belt and
    braces rather than duplication. Idempotent, and reversible via
    ``uninstall_scan_guard()`` so the guard can be exercised by its own test.
    """
    global _GUARD_INSTALLED
    if _GUARD_INSTALLED:
        return

    original_scandir = os.scandir
    original_listdir = os.listdir
    original_walk = os.walk
    _ORIGINALS.update(scandir=original_scandir, listdir=original_listdir,
                      walk=original_walk)

    def guarded_scandir(path=".", *args, **kwargs):
        if _is_host_tempdir_scope(path):
            raise SharedTmpdirScanError(
                f"os.scandir({os.fspath(path)!r}) — {_guard_reason()}")
        return original_scandir(path, *args, **kwargs)

    def guarded_listdir(path=".", *args, **kwargs):
        if _is_host_tempdir_scope(path):
            raise SharedTmpdirScanError(
                f"os.listdir({os.fspath(path)!r}) — {_guard_reason()}")
        return original_listdir(path, *args, **kwargs)

    def guarded_walk(top, *args, **kwargs):
        if _is_host_tempdir_scope(top):
            raise SharedTmpdirScanError(
                f"os.walk({os.fspath(top)!r}) — {_guard_reason()}")
        return original_walk(top, *args, **kwargs)

    os.scandir = guarded_scandir  # type: ignore[assignment]
    os.listdir = guarded_listdir  # type: ignore[assignment]
    os.walk = guarded_walk  # type: ignore[assignment]
    _GUARD_INSTALLED = True


def uninstall_scan_guard() -> None:
    """Restore the originals (used by the guard's own regression test)."""
    global _GUARD_INSTALLED
    if not _GUARD_INSTALLED:
        return
    os.scandir = _ORIGINALS["scandir"]  # type: ignore[assignment]
    os.listdir = _ORIGINALS["listdir"]  # type: ignore[assignment]
    os.walk = _ORIGINALS["walk"]  # type: ignore[assignment]
    _GUARD_INSTALLED = False


def scan_root() -> str:
    """The root test discovery must scan: the private session temp dir.

    The whole point of #3752 is that discovery aimed at the *shared* temp dir
    can match another test's or another session's ``redis.socket`` /
    ``redis.pid``. This accessor makes that impossible to do by accident: it
    asserts the isolation is installed and hands back the private root, so a
    call site can never silently degrade to the shared tree (there is no
    ``tempfile.gettempdir()`` fallback, deliberately — a fallback is exactly
    the silent degradation the guard exists to stop).
    """
    root = _SESSION_TMPDIR
    if root is None:
        raise SharedTmpdirScanError(
            "scan_root() called with no session temp isolation installed — "
            "tests/conftest.py must call install_session_tmpdir() at import "
            "(#3752); refusing to fall back to the shared temp dir")
    if _is_host_tempdir_scope(root):
        raise SharedTmpdirScanError(
            f"session temp root {root!r} is not private (#3752)")
    return root
