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
import shlex
import shutil
import sys
import tempfile

# ── the host (shared) temp dir, captured BEFORE any redirect ─────────────
# Ordering matters: this is read at import, and the redirect below caches a
# different value into tempfile.tempdir. Everything that must keep referring
# to the real shared temp dir uses HOST_TMPDIR, never tempfile.gettempdir().
#
# _TMPDIR_SPELLINGS additionally carries the $TMPDIR spelling captured at
# import, because on macOS $TMPDIR is /var/folders/... while its realpath is
# /private/var/folders/... — matching only the realpath in a command STRING
# made the audit hook fail open on the canonical spelling (#3752 review
# cycle 4). The decision is always the realpath comparison; these are only the
# cheap substring pre-filter's needles.
_ENV_TMPDIR_AT_IMPORT = (os.environ.get("TMPDIR") or "").rstrip(os.sep)
HOST_TMPDIR = os.path.realpath(tempfile.gettempdir())

def _tmpdir_spellings() -> tuple[str, ...]:
    """Every spelling a command STRING might name the shared temp dir with,
    plus each one's PARENT (an ancestor of the temp dir is in scope too).

    The raw `tempfile.gettempdir()` matters because with `$TMPDIR` unset the
    fallback is `/tmp` on macOS while its realpath is `/private/tmp` — and
    `$TMPDIR` IS unset on GitHub's ubuntu runners. The parents matter because
    macOS spells the same directory `/var/folders/...` and
    `/private/var/folders/...`, so carrying only the realpath's parent left the
    raw ancestor form failing open (#3752 review cycle 7).
    """
    spellings: list[str] = []
    for value in (HOST_TMPDIR, tempfile.gettempdir(),
                  os.path.realpath(tempfile.gettempdir()),
                  _ENV_TMPDIR_AT_IMPORT,
                  os.path.realpath(_ENV_TMPDIR_AT_IMPORT)
                  if _ENV_TMPDIR_AT_IMPORT else ""):
        if not value:
            continue
        spellings.append(value)
        spellings.append(os.path.dirname(value))
    # A filesystem ROOT is not a usable NEEDLE. `'/' in command` is true of
    # essentially every command line, so carrying it turns this cheap
    # pre-filter into "always yes" and routes every string through the token
    # scan — which is how a `python -c` script's `/` (a Python DIVISION
    # operator) came to be judged an ancestor of the temp dir and a legitimate
    # wheel smoke-test subprocess was rejected on the ubuntu runner, where
    # `$TMPDIR` is unset so the temp dir IS `/tmp` and its parent IS `/`. On
    # macOS the parent is a long `/var/folders/...` and stays in the list.
    #
    # RESIDUAL, stated as plainly as it can be: a command that names the ROOT
    # — `find / …`, in argv form or behind an opaque shell string — is caught
    # by NO layer of the subprocess guard. It was only ever caught by accident
    # (the `/` needle), and keeping it meant the pre-filter fired on every
    # command line, so the guard rejected legitimate `python -c` scripts and
    # `git -C / …`. What DOES still catch a real walk of the root is the
    # IN-PROCESS guard, which rejects `os.scandir`/`listdir`/`walk` of it as a
    # test runs. Losing a whole-filesystem `find` is the price of a pre-filter
    # that means something; a gate that false-blocks gets disabled.
    return tuple(dict.fromkeys(s for s in spellings if s and s != os.sep))


_TMPDIR_SPELLINGS = _tmpdir_spellings()

# Marker file written inside the root so a SIGKILLed run's root is
# attributable and reclaimable by the next run (sweep_stale_session_roots),
# and so the reaper can tell a session root apart from the plain `tt_*`
# test-scratch dirs that have used that prefix for years. The name is a
# CONTRACT with tortoise.embedded_reaper.SESSION_ROOT_MARKER (required by its
# _socket_walk_roots) — pinned by tests/test_tmpdir_isolation.py so the two
# cannot drift.
_PID_MARKER = ".session-pid"

# (pid, start-time) identity tolerance, mirroring
# embedded_reaper._pid_identity_matches' default: a recycled pid has a
# different start time, so a bare pid is not a liveness proof (#1642 FIX 5).
_START_TOLERANCE_S = 2.0

# Prefix that means "this is a pytest scratch dir" — must stay consistent
# with tortoise.embedded_reaper.EPHEMERAL_PREFIXES, which classifies servers
# rooted under the temp dir. "tt_" is already a member of that tuple (the
# long-standing ephemeral-tree convention this module now centralises).
_ROOT_PREFIX = "tt_"

_SESSION_TMPDIR: str | None = None
# Value of $TORTOISE_HOST_TMPDIR before the install (usually unset), restored on
# teardown so the fail-closed abort leaves no stale host path behind.
_PREV_HOST_TMPDIR_ENV: str | None = None
_GUARD_INSTALLED = False
_AUDIT_HOOK_ADDED = False
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


class SessionIsolationError(RuntimeError):
    """The private session temp root could not be established.

    Raised at conftest import, i.e. it fails the whole collection loudly
    rather than letting the suite run UNISOLATED (silently polluting the
    shared temp dir again) or run against a root the reaper cannot recognise
    (a silent leak). Isolation is a hard requirement of #3752, so it fails
    closed.
    """


def _write_marker(root: str) -> None:
    """Record ``pid`` (and start time, when available) inside the root.

    The marker is what makes the root a SESSION ROOT: `_socket_walk_roots`
    requires it (that is what keeps the reaper off the ~250 legacy `tt_*`
    scratch dirs) and `sweep_stale_session_roots` refuses to reclaim a root
    without one. A root that exists but has no marker is therefore invisible
    to both — permanently polluting the temp dir after a SIGKILL — so a
    failure to write it is escalated by the caller, never swallowed.

    The probe imports ``tortoise.embedded_reaper`` (stdlib-only itself, but it
    loads the ``tortoise`` package initializer, i.e. redislite and
    embedded_lifecycle). That is harmless ONLY because
    ``install_session_tmpdir`` installs the redirect first — the import-time
    constants resolve against the private root.
    """
    start = _process_start_time(os.getpid())
    with open(os.path.join(root, _PID_MARKER), "w") as fh:
        fh.write(f"pid={os.getpid()}\n")
        if start is not None:
            fh.write(f"start={start}\n")


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

    # The redirect comes FIRST, before anything below can import
    # tortoise.embedded_reaper (the start-time probe does, lazily). That
    # module resolves _LOCK_PATH once at import from the temp dir — and this
    # lock must stay in the SWEEP DOMAIN, which under the suite IS the private
    # root (#1658 / #3752), so the import has to happen after the redirect.
    # ACTIVE_SUITES_DIR resolves via TORTOISE_HOST_TMPDIR, set here too.
    _SESSION_TMPDIR = root
    global _PREV_HOST_TMPDIR_ENV
    _PREV_HOST_TMPDIR_ENV = os.environ.get("TORTOISE_HOST_TMPDIR")
    os.environ["TORTOISE_HOST_TMPDIR"] = HOST_TMPDIR
    os.environ["TMPDIR"] = root
    tempfile.tempdir = root
    atexit.register(teardown_session_tmpdir)

    try:
        _write_marker(root)
    except OSError as exc:
        # Fail closed: undo the redirect and remove the unmarked root, then
        # abort loudly. An unmarked root would be invisible to the reaper's
        # nested pass (it requires SESSION_ROOT_MARKER) and unreclaimable by
        # sweep_stale_session_roots (no marker -> keep) — a permanent leak
        # that only a loud failure can prevent (#3752 review cycle 2).
        teardown_session_tmpdir()
        raise SessionIsolationError(
            f"could not write the {_PID_MARKER} marker in the private session "
            f"temp root {root!r}; the temp-dir isolation cannot be trusted "
            f"without it (an unmarked root is invisible to the reaper and "
            f"unreclaimable after a kill) — fix the temp dir's permissions "
            f"rather than running the suite unisolated") from exc
    return root


def teardown_session_tmpdir() -> None:
    """Remove the private root (one rmtree) and drop the redirect.

    Never raises: teardown must not convert a green suite red. Restores the
    process temp resolution AND the exported `TORTOISE_HOST_TMPDIR` so a
    second ``pytest.main()`` in the same interpreter — or the fail-closed
    abort in ``install_session_tmpdir`` — starts clean.

    Everything is inside the ``if root`` guard: a teardown that consumed no
    install (the extra ``atexit`` registration left by the fail-closed abort,
    or a test that tears down twice) must not touch the environment at all, or
    it would destroy an operator-supplied ``TORTOISE_HOST_TMPDIR``.
    """
    global _SESSION_TMPDIR, _PREV_HOST_TMPDIR_ENV
    root = _SESSION_TMPDIR
    _SESSION_TMPDIR = None
    if not root:
        return
    try:
        if os.environ.get("TMPDIR") == root:
            os.environ["TMPDIR"] = HOST_TMPDIR
        tempfile.tempdir = None
        if _PREV_HOST_TMPDIR_ENV is None:
            os.environ.pop("TORTOISE_HOST_TMPDIR", None)
        else:
            os.environ["TORTOISE_HOST_TMPDIR"] = _PREV_HOST_TMPDIR_ENV
    except Exception:
        pass
    _PREV_HOST_TMPDIR_ENV = None
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


def _process_start_time(pid: int) -> float | None:
    """Epoch-seconds start time of ``pid`` from the reaper's own helper.

    Lazy import on purpose: this module must stay import-cheap and the reaper
    resolves ``ACTIVE_SUITES_DIR`` / ``_LOCK_PATH`` once, at its import, from
    the temp dir. Every caller runs AFTER ``install_session_tmpdir`` has
    installed the redirect and exported ``TORTOISE_HOST_TMPDIR`` — the marker
    write below, and ``sweep_stale_session_roots``, which conftest calls after
    the install — so the reaper's import-time constants resolve exactly as
    ``tests/conftest.py`` requires (it is import-pure — stdlib only — so the
    early import is safe).
    """
    try:
        from tortoise.embedded_reaper import _process_start_time as impl
        return impl(pid)
    except Exception:
        return None


def _read_marker(marker_path: str) -> tuple[int, float | None] | None:
    """Parse a ``.session-pid`` marker: ``pid=<int>`` plus an optional
    ``start=<float>``.

    Returns None for anything unreadable, or without a parseable pid — an
    unrecognised marker must never authorise a delete.
    """
    try:
        with open(marker_path) as fh:
            text = fh.read()
    except OSError:
        return None
    pid: int | None = None
    start: float | None = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("pid="):
            try:
                pid = int(line[4:])
            except ValueError:
                return None
        elif line.startswith("start="):
            try:
                start = float(line[6:])
            except ValueError:
                start = None  # recorded-but-degraded -> keep (fail safe)
    if pid is None:
        return None
    return pid, start


def _marker_owner_provably_dead(marker_path: str) -> bool:
    """True only when the marker's owner is PROVABLY not the same process.

    Three cases must not be conflated (#1642 FIX 5):
      * pid dead -> reclaim;
      * pid alive with a matching start time -> a concurrent suite, never
        touch it;
      * pid alive with a non-matching start time -> a RECYCLED pid, i.e. the
        owner is gone -> reclaim (a bare `os.kill(pid, 0)` would pin a dead
        suite's root forever here).

    Every undeterminable case (no marker, unparseable, permission denied, no
    start recorded, `ps` unavailable) fails SAFE and keeps the root.

    Deliberately NOT ``embedded_reaper._pid_identity_matches``: that helper is
    the OWNER-ADOPTION predicate and fails CLOSED (returns False) when the
    start time cannot be determined, which here would delete a live suite's
    root. This is the mirror-image question — "may I delete?" — so it is
    fail-safe instead.
    """
    rec = _read_marker(marker_path)
    if rec is None:
        return False
    pid, start = rec
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True  # provably dead
    except (PermissionError, OSError):
        return False  # alive-but-unsignalable, or an indeterminate probe
    if start is None:
        return False  # legacy/pid-only record -> keep
    current = _process_start_time(pid)
    if current is None:
        return False  # cannot verify -> fail safe
    return abs(current - start) >= _START_TOLERANCE_S


def sweep_stale_session_roots() -> list[str]:
    """Reclaim roots left by a SIGKILLed suite (a ``tt_`` dir whose recorded
    pid is dead). Best-effort, never raises.

    A suite killed by SIGKILL/segfault cannot run its own teardown, so a root
    would otherwise survive. This runs at install time (the next suite
    reclaims it) and mirrors the reaper's own pid+start-time reasoning rather
    than an age heuristic: a *live* pid with a matching start time means a
    concurrent suite, whose root is not ours to delete. Undeterminable cases
    keep the root (see _marker_owner_provably_dead).
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
        if not _marker_owner_provably_dead(
                os.path.join(entry.path, _PID_MARKER)):
            continue  # ours-but-live, or undeterminable — never delete on a guess
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
    # Type-guarded: the guard is process-wide and sees EVERY scandir / listdir
    # / walk in the session, so a bytes path (legal for all three) must never
    # reach str.startswith with a mismatched type — that raised a misleading
    # `TypeError: a bytes-like object is required` from inside the guard.
    if not isinstance(child, str) or not isinstance(parent, str):
        return False
    return child.startswith(parent.rstrip(os.sep) + os.sep)


def _fd_target_path(fd) -> str | None:
    """Resolve an already-open file descriptor to its path, or None.

    Required because ``os.scandir`` accepts an int fd, and on macOS
    ``shutil.rmtree`` uses that path (``shutil._use_fd_functions`` is True) —
    so without this the guard is bypassed by exactly the call that deletes a
    whole tree.
    """
    if not isinstance(fd, int):
        return None
    try:  # macOS / BSD
        import fcntl
        raw = fcntl.fcntl(fd, fcntl.F_GETPATH, b"\0" * 1024)
        path = raw.decode("utf-8", "surrogateescape").rstrip("\0")
        return path or None
    except Exception:
        pass  # fall through to the Linux form
    try:  # Linux
        return os.readlink(f"/proc/self/fd/{fd}")
    except OSError:
        return None


def _is_host_tempdir_scope(path) -> bool:
    """True when ``path`` IS the host temp dir or an ANCESTOR of it.

    Descendants are allowed on purpose: ``<host>/.tortoise/active_suites`` is
    the host-global marker dir, and the session root itself lives under the
    host temp dir. Only scanning the shared tree (or above it) is the defect.

    Accepts str, bytes, PathLike AND an int file descriptor (all four are
    legal for ``os.scandir``); bytes is decoded with ``os.fsdecode`` rather
    than compared as-is, so a bytes-path caller still gets the original
    stdlib behaviour instead of a TypeError raised from inside the guard, and
    an fd is resolved through ``_fd_target_path`` so ``shutil.rmtree`` (which
    scans an fd on macOS) cannot slip through.
    """
    if isinstance(path, int):
        resolved = _fd_target_path(path)
        if resolved is None:
            return False  # unidentifiable fd — cannot judge, must not guess
        path = resolved
    try:
        raw = os.fspath(path)
    except TypeError:
        return False
    if isinstance(raw, bytes):
        raw = os.fsdecode(raw)
    try:
        target = os.path.realpath(raw)
    except (TypeError, ValueError, OSError):
        return False
    # The root is deliberately NOT special-cased HERE: `_is_under(child, '/')`
    # legitimately means "every absolute path", and the ancestor rule is what
    # makes `os.scandir(dirname(HOST_TMPDIR))` — a real ancestor scan — fail
    # closed. It IS special-cased in `_subprocess_touches_host_tempdir`, whose
    # callers pass raw argv elements and shlex-split script text, where a bare
    # `/` is a division operator or an ordinary argument rather than a scan.
    # Two different questions, so two different predicates.
    return target == HOST_TMPDIR or _is_under(HOST_TMPDIR, target)


def _fmt_path(path) -> str:
    """repr for a guard message; an int fd has no fspath (and must not
    raise while the guard is reporting a violation)."""
    if isinstance(path, int):
        return f"fd {path}"
    try:
        return repr(os.fspath(path))
    except TypeError:
        return repr(path)


def _guard_reason() -> str:
    return (
        "scanning the SHARED temp dir is forbidden in tests (#3752): this is "
        "the non-hermetic, cross-contaminating, O(whole-host) discovery that "
        f"the private session temp root exists to prevent ({HOST_TMPDIR}). "
        "Use tests._tmpdir_isolation.scan_root() (the private per-session "
        "root), or the test's own tmp_path, instead."
    )


def _subprocess_touches_host_tempdir(args) -> str | None:
    """Return the offending argv token when a command scans the shared temp
    dir, else None.

    Needed because the shell-out form is INVISIBLE to the in-process guard and
    yet is the exact shape of the original #3752 defect
    (``find <shared T> -maxdepth 2 (...)`` run at 53% CPU). Only an argv token
    that IS the host temp dir (or an ancestor) is rejected: a DESCENDANT — the
    private session root, ``<host>/.tortoise/...`` — is legitimate and must
    keep working, and a flag such as ``-maxdepth`` is not a path at all.
    """
    if not args:
        return None
    for token in args:
        if isinstance(token, bytes):
            token = os.fsdecode(token)
        if not isinstance(token, (str, os.PathLike)):
            continue
        raw = token if isinstance(token, str) else os.fspath(token)
        # A bare filesystem ROOT is not evidence of a temp-dir scan. `/` is
        # also a Python division operator and a routine argv element
        # (`git -C / status`); on a GitHub runner `$TMPDIR` is unset so
        # HOST_TMPDIR IS `/tmp` and its PARENT IS `/`, which made the root an
        # "ancestor" of the temp dir and rejected both shapes. A real walk of
        # the root is still caught — by the IN-PROCESS guard, which keeps
        # rejecting `os.scandir`/`listdir`/`walk` of it (see
        # `_is_host_tempdir_scope`, deliberately unchanged).
        try:
            if os.path.realpath(raw) == os.sep:
                continue
        except (TypeError, ValueError, OSError):
            pass
        if _is_host_tempdir_scope(token):
            return raw
    return None


def _command_touches_host_tempdir(command) -> str | None:
    """Return the offending token when a COMMAND LINE names the shared temp
    dir, else None.

    For the opaque-string forms of a shell-out (``subprocess.Popen("find …",
    shell=True)``, ``os.system``, the ``os.exec*`` family), whose arguments
    arrive as one string — and for the ``shell=True`` case, where CPython hands
    the hook ``['/bin/sh', '-c', '<the whole command>']``. The string is
    shlex-split and each resulting token is judged by the SAME realpath-based
    scope test the argv path uses, so a descendant (the private session root,
    or ``find <session root>`` — the reaper's own legitimate walk) still passes
    while ``find <host T>`` does not.

    The fast-path substring test uses every spelling of the temp dir captured
    at import (the raw `tempfile.gettempdir()`, its realpath, the `$TMPDIR`
    spelling and ITS realpath, and the temp dir's parent — on macOS `$TMPDIR`
    is `/var/folders/...` while the realpath is `/private/var/folders/...`, so
    testing only the realpath made the shell form fail OPEN on the canonical
    spelling; `$TMPDIR` is also unset on GitHub's ubuntu runners, where the
    spelling is `tempfile.gettempdir()`'s own fallback). A hit then routes the
    string to the per-token realpath scope test.

    The list is deliberately NOT complete: a filesystem ROOT is excluded as a
    needle (see `_tmpdir_spellings`), so a pre-filter MISS on a root-only
    command is expected and is documented there as the residual. A miss
    otherwise means the string cannot be naming the temp dir in a known
    spelling.

    Still unguarded (inherent to a static-content check on a command STRING):
    a command SUBSTITUTION that computes an ancestor or the temp dir at run
    time (`sh -c 'echo $(dirname <T>)'`) and `$VAR` indirection. The argv /
    `os.exec` layers see the resolved path in those cases only when the path
    is passed literally.
    """
    if isinstance(command, bytes):
        command = os.fsdecode(command)
    if isinstance(command, os.PathLike):
        command = os.fspath(command)
    if not isinstance(command, str):
        return None
    if not any(spelling in command for spelling in _TMPDIR_SPELLINGS):
        return None  # fast path: cannot be naming it in any known spelling
    try:
        parts = shlex.split(command)
    except ValueError:  # unbalanced quotes — split crudely rather than skip
        parts = command.split()
    return _subprocess_touches_host_tempdir(parts)


def _argv_embeds_host_tempdir(argv) -> str | None:
    """A str argv token that EMBEDS a command line naming the shared temp dir
    (the ``shell=True`` shape: ``['/bin/sh', '-c', 'find <T> …']``)."""
    if not argv:
        return None
    for token in argv:
        if isinstance(token, (str, bytes, os.PathLike)):
            hit = _command_touches_host_tempdir(token)
            if hit is not None:
                return hit
    return None


def _audit_hook(event: str, args: tuple) -> None:
    """Process-wide audit hook: refuse to SHELL OUT at the shared temp dir.

    Installed via ``sys.addaudithook`` (cannot be removed, so
    ``uninstall_scan_guard`` flips ``_GUARD_INSTALLED`` and this no-ops
    instead). Covers the argv form (``subprocess.Popen``, ``os.posix_spawn``,
    ``os.exec``) AND the opaque-string form (``os.system``, ``shell=True``) —
    a one-line shell-out is otherwise a complete bypass of an in-process
    patch. Never raises from an unrelated event: only these four events are
    inspected.
    """
    if not _GUARD_INSTALLED:
        return
    if event == "os.system":
        cmd = args[0] if args else None
        if _command_touches_host_tempdir(cmd) is not None:
            raise SharedTmpdirScanError(
                f"os.system({cmd!r}) targets the SHARED temp dir — "
                f"{_guard_reason()}")
        return
    if event not in ("subprocess.Popen", "os.exec", "os.posix_spawn"):
        return
    argv = args[1] if len(args) > 1 else None
    if argv is None and args and isinstance(args[0], (list, tuple)):
        argv = args[0]
    if isinstance(argv, (str, bytes)):
        token = _command_touches_host_tempdir(argv)
    else:
        token = _subprocess_touches_host_tempdir(argv) \
            or _argv_embeds_host_tempdir(argv)
    if token is not None:
        raise SharedTmpdirScanError(
            f"{event} argv {argv!r} targets the SHARED temp dir "
            f"({token!r}) — {_guard_reason()}")


def install_scan_guard() -> None:
    """Fail loudly on any in-process scan of the shared temp dir.

    Patches ``os.scandir`` / ``os.walk`` / ``os.listdir``. ``os.walk``
    resolves ``os.scandir`` at call time, so patching all three is belt and
    braces rather than duplication. An int fd (which ``shutil.rmtree`` passes
    on macOS) is resolved rather than ignored, and a ``subprocess.Popen``
    audit hook covers the shell-out form the in-process patch cannot see.
    Idempotent, and reversible via ``uninstall_scan_guard()`` so the guard can
    be exercised by its own test.
    """
    global _GUARD_INSTALLED, _AUDIT_HOOK_ADDED
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
                f"os.scandir({_fmt_path(path)}) — {_guard_reason()}")
        return original_scandir(path, *args, **kwargs)

    def guarded_listdir(path=".", *args, **kwargs):
        if _is_host_tempdir_scope(path):
            raise SharedTmpdirScanError(
                f"os.listdir({_fmt_path(path)}) — {_guard_reason()}")
        return original_listdir(path, *args, **kwargs)

    def guarded_walk(top, *args, **kwargs):
        if _is_host_tempdir_scope(top):
            raise SharedTmpdirScanError(
                f"os.walk({_fmt_path(top)}) — {_guard_reason()}")
        return original_walk(top, *args, **kwargs)

    os.scandir = guarded_scandir  # type: ignore[assignment]
    os.listdir = guarded_listdir  # type: ignore[assignment]
    os.walk = guarded_walk  # type: ignore[assignment]
    if not _AUDIT_HOOK_ADDED:
        # sys.addaudithook is irreversible — install it exactly once per
        # process and gate it on _GUARD_INSTALLED instead.
        sys.addaudithook(_audit_hook)
        _AUDIT_HOOK_ADDED = True
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
