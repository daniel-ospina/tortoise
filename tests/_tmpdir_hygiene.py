"""Temp-dir hygiene for the test suite — per-test teardown (#4069) and the
private per-session temp root + shared-tempdir scan guard (#3752).

#4069 — teardown-tracked tempfile artifacts.

`$TMPDIR` churned to 362,962 entries / 3.8 GB with nothing older than three
days: the suite's many `tempfile.mkdtemp(prefix=...)` call sites (435 of
them, e.g. `ask_sdk_`, `ask_reg_`, `ask2070_`, `tortoise_validity_test_`,
`tortoise_w2_`/`w3_`/`w4_`) create a directory per invocation and never
remove it. The leak is *structural*, so the fix is too: an autouse fixture
(re-exported by `tests/conftest.py`) patches `tempfile.mkdtemp` for the
duration of each test, records every directory the test creates, and rmtrees
them at teardown.

Why patch the call sites rather than edit 435 of them:

* **Complete by construction.** New call sites are covered the day they are
  written; a per-site audit is stale the moment someone adds `mkdtemp`.
* **Test-scoped by construction.** Only directories created *during* the
  test's call phase are tracked. A module-/session-scoped fixture's directory
  is created before the (function-scoped) autouse fixture installs the patch,
  so it is never recorded and never removed under a later test's feet.
* **No scan.** The suite must not walk the shared temp dir per test — that
  walk is itself the load this issue is about. The tracked list is exact.

Safety properties:

* Directories only (`tempfile.mkdtemp`); `mkstemp` files are not touched.
* A directory whose `redis.pid` is live — or unreadable, unparseable,
  non-positive, out-of-range, or present but not a regular file — is left
  for the reaper rather than removed out from under a running embedded
  server (fail closed, mirroring the sweep: a pid that cannot be proven dead
  is treated as live; the two guards are pinned equal by
  `tests/test_tmpdir_sweep.py::test_the_two_guards_agree_on_every_shape`).
  The skip is logged.
* `shutil.rmtree` never follows symlinks; removal is `ignore_errors` so an
  already-cleaned directory (or a concurrent reaper) is a no-op — the fixture
  is idempotent.

This does not replace the reaper: a SIGKILLed or watchdog-killed process runs
no teardown at all, and that residue is still the reaper's job
(`docs/infra/embedded-reaper-cron.md`). It removes the *normal-exit* leak.
"""
from __future__ import annotations

import atexit
import contextlib
import errno
import functools
import json
import logging
import os
import shutil
import stat
import tempfile
from collections.abc import Iterator

import pytest

logger = logging.getLogger(__name__)

_PID_FILENAMES = ("redis.pid",)


def _pid_file_reason(pid_file: str, name: str) -> tuple[bool, str | None]:
    """Probe one pid file. Returns `(present, reason)`.

    `present` False means there is no such file. With `present` True, a `None`
    reason means the pid is PROVABLY DEAD (removal is safe) and a string means
    the directory must be left for the reaper.

    Fail-CLOSED: a pid file that is present but cannot be proven dead protects
    the directory — unreadable, unparseable, non-positive, out-of-range, or
    present-but-not-a-regular-file. `os.lstat`, NOT `os.path.isfile`: `isfile`
    answers False for a path that EXISTS but is not a regular file (FIFO,
    dangling symlink, device, directory) and for a path whose parent cannot be
    stat-ed — all of which must read as "present but unprovable".

    This is the deliberate MIRROR of `tools/tmpdir_sweep.py::_pid_file_reason`;
    the two must decide identically (`test_the_two_guards_agree_on_every_shape`).
    """
    try:
        st = os.lstat(pid_file)
    except FileNotFoundError:
        return False, None
    except OSError as exc:
        return True, f"unstattable {name} ({exc}) — treated as live"
    if not stat.S_ISREG(st.st_mode):
        return True, f"non-regular {name} — treated as live"
    try:
        with open(pid_file, encoding="utf-8", errors="replace") as fh:
            raw = fh.read().strip()
    except OSError as exc:
        return True, f"unreadable {name} ({exc}) — treated as live"
    try:
        pid = int(raw)
    except ValueError:
        return True, f"unparseable {name} — treated as live"
    if pid <= 0:
        return True, f"nonsensical pid {pid} in {name}"
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True, None  # provably dead -> safe to remove
    except PermissionError:
        return True, f"pid {pid} alive (no permission to signal)"
    except OverflowError:
        return True, f"pid {pid} out of range — treated as live"
    except OSError as exc:
        return True, f"pid {pid} probe failed ({exc}) — treated as live"
    return True, f"live redis pid {pid}"


# redislite records the instance's pid-file path in `<dbfilename>.settings`
# INSIDE the configured data dir, while the pid file itself lives in a separate
# `tempfile.mkdtemp()` instance dir. So a data dir with no pid file of its own
# can still belong to a LIVE server (#4479).
_SETTINGS_SUFFIX = ".settings"


def _registry_pidfile(path: str) -> str | None:
    """The pid file of the server that declares `path` its data dir.

    Reads the redislite settings registry (JSON) written into the data dir and
    returns its `pidfile` value, or None when there is no readable registry.

    A None is deliberately NOT a protection: the registry of a DEAD instance
    survives in its data dir, so treating it as a live owner would strand every
    orphaned data dir — trading the reaper's backlog back for this guard. The
    registry only ever SUPPLIES a pid file for the ordinary liveness probe to
    judge, and a non-regular/malformed registry is skipped rather than trusted.
    """
    try:
        entries = sorted(os.listdir(path))
    except OSError:
        return None
    for entry in entries:
        if not entry.endswith(_SETTINGS_SUFFIX):
            continue
        registry = os.path.join(path, entry)
        try:
            st = os.lstat(registry)
        except OSError:
            continue
        if not stat.S_ISREG(st.st_mode):
            continue
        try:
            with open(registry, encoding="utf-8", errors="replace") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        pidfile = data.get("pidfile") if isinstance(data, dict) else None
        if isinstance(pidfile, str) and pidfile:
            return pidfile
    return None


def _protected_reason(path: str) -> str | None:
    """Why `path` must NOT be removed, or None when teardown is safe.

    Fail-CLOSED, mirroring `tools/tmpdir_sweep.py::_live_pid_protects`: a pid
    file that is live, unreadable, unparseable, non-positive, present but not a
    regular file, or whose probe fails for any reason is treated as a running
    server and the directory is left for the reaper. Only a provably dead pid
    (or no live owner at all) permits removal — the reaper cannot reclassify a
    directory this fixture has already deleted.

    This function is the deliberate MIRROR of the sweep's guard; the two must
    decide identically on every shape, which
    `tests/test_tmpdir_sweep.py::test_the_two_guards_agree_on_every_shape`
    pins. A change here must be made there too.
    """
    for name in _PID_FILENAMES:
        present, reason = _pid_file_reason(os.path.join(path, name), name)
        if present and reason is not None:
            return reason
    # No pid file of its own (or only provably dead ones). The directory may
    # still be a live server's DATA dir — see `_registry_pidfile`.
    declared = _registry_pidfile(path)
    if declared is not None:
        present, reason = _pid_file_reason(declared, os.path.basename(declared))
        if present and reason is not None:
            return reason
    return None


class TrackedTempfileArtifacts:
    """Context manager: record and remove `tempfile.mkdtemp` directories.

    Usable directly in a test (see `tests/test_tmpdir_hygiene.py`) and as the
    body of the autouse fixture below. Reentrant — a nested tracker restores
    the outer one's patched `mkdtemp` rather than the original.
    """

    def __init__(self) -> None:
        self.created: list[str] = []
        self.skipped_live: list[tuple[str, str]] = []
        self._previous = None

    def __enter__(self) -> TrackedTempfileArtifacts:
        self._previous = tempfile.mkdtemp
        real = tempfile.mkdtemp
        created = self.created

        def _tracking_mkdtemp(*args, **kwargs) -> str:
            path = real(*args, **kwargs)
            created.append(path)
            return path

        tempfile.mkdtemp = _tracking_mkdtemp
        return self

    def __exit__(self, *exc_info) -> bool:
        if self._previous is not None:
            tempfile.mkdtemp = self._previous
            self._previous = None
        for path in self.created:
            reason = _protected_reason(path)
            if reason is not None:
                self.skipped_live.append((path, reason))
                logger.warning(
                    "#4069: leaving %s in place — %s (the reaper owns it)",
                    path, reason)
                continue
            shutil.rmtree(path, ignore_errors=True)
        # `created` / `skipped_live` are deliberately left populated: the
        # tracker object is discarded with the fixture, and tests assert on
        # the record after the `with` block.
        return False


@pytest.fixture(autouse=True)
def track_tempfile_artifacts() -> Iterator[None]:
    """#4069: remove every per-test `tempfile.mkdtemp` directory at teardown.

    Autouse for the whole suite (re-exported by `tests/conftest.py`). The
    leak the suite had was normal-exit churn, so teardown removal is the
    complete fix for it; killed runs remain the reaper's domain.
    """
    with TrackedTempfileArtifacts():
        yield


# ══════════════════════════════════════════════════════════════════════
# #3752 — private per-session temp root + shared-tempdir scan guard
#
# The suite used to scratch directly in the SHARED system temp dir and
# socket/pid discovery then scanned that same tree (the `find <T> -maxdepth
# 2 -name redis.socket` caught in flight at 53% CPU for two minutes). This
# section gives the pytest process ONE private root, an asserting accessor
# for discovery (`scan_root`), and a guard that fails any scan of a shared
# temp root. Lives beside the #4069 tracker because both are the same
# concern — the suite's temp-dir hygiene — and this file is already in
# `SHARED_MODULES` (a change here must run the full CI matrix).
# ══════════════════════════════════════════════════════════════════════
# ── the host (shared) temp dir, captured BEFORE any redirect ─────────────
# Read at import, before the redirect caches a different value into
# tempfile.tempdir. Everything that must keep referring to the real shared temp
# dir (the coordination override, the scan guard) uses HOST_TMPDIR, never
# tempfile.gettempdir().
HOST_TMPDIR = os.path.realpath(tempfile.gettempdir())


def _short_temp_base() -> str:
    """A SHORT writable base for the private session root.

    The root must be short. This host's ``$TMPDIR`` is already ~56 chars, and
    adding a ~12-byte ``tt_`` level pushed ordinary scratch paths past the
    AF_UNIX ``sun_path`` cap (~104 bytes): ``mkdtemp(prefix="tt_")"/sub/redis.socket``
    became 105 and every socket bind failed ENAMETOOLONG. ``/tmp`` (realpath
    ``/private/tmp``, 12 chars) restores ~44 bytes of headroom, so no call site
    needs an AF_UNIX workaround.

    Rule: the FIRST of ``/tmp``, ``/var/tmp`` that is a writable directory
    strictly shorter than the host temp dir; otherwise the host temp dir. (The
    host temp dir IS ``/tmp`` on a Linux box with ``$TMPDIR`` unset, so the
    rule then returns it unchanged.)
    """
    host = os.path.realpath(tempfile.gettempdir())
    for candidate in ("/tmp", "/var/tmp"):
        real = os.path.realpath(candidate)
        if not os.path.isdir(real) or not os.access(real, os.W_OK):
            continue
        if len(real) < len(host):
            return real
    return host


# Where session roots are created. Deliberately may differ from HOST_TMPDIR:
# only the SCRATCH space moves to the short base, while host-global coordination
# stays on the per-user temp dir (see TORTOISE_HOST_TMPDIR below).
ROOT_BASE = _short_temp_base()

# Every shared temp root the scan guard protects: the per-user temp dir AND the
# (short) session-root base, so a scan of EITHER shared tree fails. Descendants
# — the session root itself, pytest's tmp trees — are legitimate.
SHARED_TEMP_ROOTS = tuple(dict.fromkeys(
    root for root in (HOST_TMPDIR, ROOT_BASE) if root and root != os.sep))

# Prefix that marks a private session root. ``tt_`` is already a member of
# ``tortoise.embedded_reaper.EPHEMERAL_PREFIXES``, so servers rooted there keep
# the classification they had under the shared temp dir (and a SIGKILLed root
# is still nameable by an operator). The marker below distinguishes a session
# root from the long-standing plain ``tt_*`` test-scratch dirs.
_ROOT_PREFIX = "tt_"
# Marker written inside the root so a SIGKILLed run's root is attributable and
# reclaimable by ``sweep_stale_session_roots``. A contract with the operator
# surface, pinned by tests/test_tmpdir_hygiene.py.
_PID_MARKER = ".session-pid"
# (pid, start-time) identity tolerance, mirroring embedded_reaper: a recycled
# pid has a different start time, so a bare pid is not a liveness proof.
_START_TOLERANCE_S = 2.0

# #7923: the env override the embedded reaper resolves its persisted
# zero-client state file from. A LITERAL mirror of
# ``tortoise.embedded_reaper.ZERO_CLIENT_STATE_PATH_ENV`` — this module must not
# import embedded_reaper at module scope (the redirect below has to land before
# that module resolves `_LOCK_PATH`/`ACTIVE_SUITES_DIR` at import). Drift is
# pinned by tests/test_tmpdir_hygiene.py.
_ZERO_CLIENT_STATE_PATH_ENV = "TORTOISE_ZERO_CLIENT_STATE_PATH"

_SESSION_TMPDIR: str | None = None
_PREV_HOST_TMPDIR_ENV: str | None = None
# Value of $TMPDIR before the install (usually unset on Linux CI), restored
# exactly on teardown.
_PREV_TMPDIR_ENV: str | None = None
# #7923: value of $TORTOISE_ZERO_CLIENT_STATE_PATH before the install, restored
# exactly on teardown (a long-lived harness may call pytest.main() twice).
_PREV_ZERO_CLIENT_STATE_PATH_ENV: str | None = None
_GUARD_INSTALLED = False
_ORIGINALS: dict[str, object] = {}
#: #7735 — the pre-install ``TemporaryDirectory.cleanup``, kept so the tolerant
#: wrapper can be uninstalled by its own test (the other installs here have the
#: same escape hatch).
_ORIGINAL_TEMPDIR_CLEANUP = None
#: #7735 — the ENOTEMPTY teardown leaks tolerated this session, as
#: ``{"path", "errno", "reason"}`` dicts (the artifact adds the writing
#: process's ``pid``). A per-leak ``logger.warning`` is
#: DISCARDED by pytest capture for a PASSING test (measured under the CI flags
#: this PR was reviewed with: 0 occurrences in ``/tmp/pytest.log`` and 0 in the
#: junit XML), so the record is accumulated here and flushed at session end by
#: ``write_tolerated_cleanup_report`` — the file the CI workflow dumps, exactly
#: the #1103 shape this module's sibling report already uses.
_TOLERATED_CLEANUP_LEAKS: list[dict[str, object]] = []

# The unpatched primitive, captured at import (before install_scan_guard).
# Enumerating the shared temp dir is occasionally legitimate — reclaiming a
# SIGKILLed prior run's root is the one case — and the guard would otherwise
# refuse the very call that does the reclaiming.
_ORIGINAL_SCANDIR = os.scandir


class SessionIsolationError(RuntimeError):
    """The private session temp root could not be established.

    Raised at conftest import, so it fails collection loudly rather than
    letting the suite run UNISOLATED (silently polluting the shared temp dir
    again). Isolation is a hard requirement of #3752, so it fails closed.
    """


class SharedTmpdirScanError(AssertionError):
    """A test tried to discover files by scanning the shared temp dir.

    Subclasses AssertionError so it surfaces as a test failure with a readable
    message rather than a bare exception.
    """


def root_base_entries():
    """Enumerate the session-root base with the UNGUARDED scandir.

    The only sanctioned way to look for session roots from test code.
    """
    return _ORIGINAL_SCANDIR(ROOT_BASE)


def session_tmpdir() -> str | None:
    """The private session root, or None when isolation was never installed."""
    return _SESSION_TMPDIR


def scan_root() -> str:
    """The root test discovery must scan: the private session temp dir.

    The whole point of #3752 is that discovery aimed at the *shared* temp dir
    can match another test's or another session's ``redis.socket`` /
    ``redis.pid``. This accessor makes that impossible to do by accident: it
    raises when isolation is not installed and hands back only the private
    root. There is deliberately NO ``tempfile.gettempdir()`` fallback — a
    fallback is exactly the silent degradation the guard exists to stop.
    """
    root = _SESSION_TMPDIR
    if root is None:
        raise SharedTmpdirScanError(
            "scan_root() called with no session temp isolation installed — "
            "tests/conftest.py must call install_session_tmpdir() at import "
            "(#3752); refusing to fall back to the shared temp dir")
    if _is_shared_temp_scope(root):
        raise SharedTmpdirScanError(
            f"session temp root {root!r} is not private (#3752)")
    return root


# ── install / teardown ───────────────────────────────────────────────────

def _write_marker(root: str) -> None:
    """Record ``pid`` (and start time, when available) inside the root.

    A root that exists but has no marker would be unreclaimable after a kill,
    so a failure to write it is escalated by the caller, never swallowed.
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

    Sets BOTH ``tempfile.tempdir`` (gettempdir caches, so an already-primed
    process would ignore a bare ``$TMPDIR`` change) and ``os.environ['TMPDIR']``
    (child processes resolve their own temp dir from the env, and must be
    contained too).

    #7923: also pins the embedded reaper's persisted zero-client state file
    (``TORTOISE_ZERO_CLIENT_STATE_PATH``) inside the private root. The
    suite-wide end-sweep writes that file on any run where a live embedded
    server is discovered, and without the pin it landed in the developer's real
    ``$HOME/.tortoise``. Contained per session, it is thrown away with the root.
    """
    global _SESSION_TMPDIR, _PREV_HOST_TMPDIR_ENV, _PREV_TMPDIR_ENV
    global _PREV_ZERO_CLIENT_STATE_PATH_ENV
    if _SESSION_TMPDIR is not None:
        return _SESSION_TMPDIR

    root = os.path.realpath(
        tempfile.mkdtemp(prefix=_ROOT_PREFIX, dir=ROOT_BASE))

    # The redirect comes FIRST, before anything below can import
    # tortoise.embedded_reaper (the start-time probe does, lazily): that module
    # resolves _LOCK_PATH once, at import, from the temp dir, and the lock must
    # stay in the sweep domain (which under the suite IS the private root).
    _SESSION_TMPDIR = root
    _PREV_HOST_TMPDIR_ENV = os.environ.get("TORTOISE_HOST_TMPDIR")
    # Save $TMPDIR so teardown restores the EXACT prior state: on the Linux CI
    # default it is UNSET, and restoring it to HOST_TMPDIR would leak a new
    # env var into a long-lived harness that calls pytest.main() more than once.
    _PREV_TMPDIR_ENV = os.environ.get("TMPDIR")
    _PREV_ZERO_CLIENT_STATE_PATH_ENV = os.environ.get(
        _ZERO_CLIENT_STATE_PATH_ENV)
    os.environ["TORTOISE_HOST_TMPDIR"] = HOST_TMPDIR
    os.environ["TMPDIR"] = root
    os.environ[_ZERO_CLIENT_STATE_PATH_ENV] = os.path.join(
        root, ".tortoise", "reaper-zero-client.json")
    tempfile.tempdir = root
    atexit.register(teardown_session_tmpdir)

    try:
        _write_marker(root)
    except OSError as exc:
        # Fail closed: undo the redirect and remove the unmarked root, then
        # abort loudly. An unmarked root is unreclaimable by
        # sweep_stale_session_roots, i.e. a permanent leak — only a loud
        # failure can prevent it.
        teardown_session_tmpdir()
        raise SessionIsolationError(
            f"could not write the {_PID_MARKER} marker in the private session "
            f"temp root {root!r}; the temp-dir isolation cannot be trusted "
            f"without it — fix the temp dir's permissions rather than running "
            f"the suite unisolated") from exc
    return root


def teardown_session_tmpdir() -> None:
    """Remove the private root (one rmtree) and drop the redirect.

    Never raises: teardown must not convert a green suite red. Restores the
    process temp resolution, the exported ``TORTOISE_HOST_TMPDIR`` and the
    ``TORTOISE_ZERO_CLIENT_STATE_PATH`` pin (#7923) so a second
    ``pytest.main()`` in the same interpreter — or the fail-closed abort in
    ``install_session_tmpdir`` — starts clean.
    """
    global _SESSION_TMPDIR, _PREV_HOST_TMPDIR_ENV, _PREV_TMPDIR_ENV
    global _PREV_ZERO_CLIENT_STATE_PATH_ENV
    root = _SESSION_TMPDIR
    _SESSION_TMPDIR = None
    if not root:
        return
    try:
        if os.environ.get("TMPDIR") == root:
            if _PREV_TMPDIR_ENV is None:
                os.environ.pop("TMPDIR", None)
            else:
                os.environ["TMPDIR"] = _PREV_TMPDIR_ENV
        tempfile.tempdir = None
        if _PREV_HOST_TMPDIR_ENV is None:
            os.environ.pop("TORTOISE_HOST_TMPDIR", None)
        else:
            os.environ["TORTOISE_HOST_TMPDIR"] = _PREV_HOST_TMPDIR_ENV
        # #7923: restore the reaper state override exactly (only when we are
        # still the installer — a test may have redirected it meanwhile).
        if os.environ.get(_ZERO_CLIENT_STATE_PATH_ENV, "").startswith(root):
            if _PREV_ZERO_CLIENT_STATE_PATH_ENV is None:
                os.environ.pop(_ZERO_CLIENT_STATE_PATH_ENV, None)
            else:
                os.environ[_ZERO_CLIENT_STATE_PATH_ENV] = (
                    _PREV_ZERO_CLIENT_STATE_PATH_ENV)
    except Exception:
        pass
    _PREV_HOST_TMPDIR_ENV = None
    _PREV_TMPDIR_ENV = None
    _PREV_ZERO_CLIENT_STATE_PATH_ENV = None
    # A live embedded server that deliberately OUTLIVED the suite (the
    # `only_safe` end-sweep defers kills while another suite is active) holds
    # its redis.socket/redis.pid inside this root. Removing it would make the
    # still-running server unclassifiable to the reaper — the #4248/#1005
    # orphan class. Leave the whole (marked) root for the reaper instead of
    # destroying the evidence; `sweep_stale_session_roots` applies the same
    # check before it reclaims one.
    live = _live_server_in_root(root)
    if live is not None:
        logger.warning(
            "#3752: leaving the private session temp root %s in place — a "
            "live embedded server holds %s (the reaper owns it)", root, live)
    else:
        with contextlib.suppress(Exception):
            shutil.rmtree(root, ignore_errors=True)
    _prune_host_coordination_dir()


def _live_server_in_root(root: str) -> str | None:
    """Why ``root`` must NOT be removed, or None when no server in it is LIVE.

    Deliberately NARROWER than `_protected_reason` (which is fail-CLOSED: an
    unreadable/unparseable/non-regular pid protects a dir). That predicate
    answers "may I delete THIS dir?"; this one answers "may I garbage-collect
    the whole session root?" — and an unparseable pid must not answer that with
    "never": the #4069 guard tests deliberately build dirs with bogus
    `redis.pid` content, so a fail-closed rule here would pin the root forever
    and defeat the private-root guarantee. Only a pid that is provably RUNNING
    defers.
    """
    try:
        entries = os.scandir(root)
    except OSError:
        return None
    with entries:
        for entry in entries:
            if not entry.is_dir(follow_symlinks=False):
                continue
            reason = _live_pid_reason(os.path.join(entry.path, "redis.pid"))
            if reason is None:
                declared = _registry_pidfile(entry.path)
                if declared is not None:
                    reason = _live_pid_reason(declared)
            if reason is not None:
                return f"{entry.path}: {reason}"
    return None


def _live_pid_reason(pid_file: str) -> str | None:
    """A reason string ONLY when the pid in ``pid_file`` is provably running."""
    try:
        st = os.lstat(pid_file)
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    try:
        with open(pid_file, encoding="utf-8", errors="replace") as fh:
            pid = int(fh.read(64).strip())
    except (OSError, ValueError):
        return None
    if pid <= 0:
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except (PermissionError, OverflowError, OSError):
        return f"pid {pid} possibly alive (probe inconclusive)"
    return f"live redis pid {pid}"


def _prune_host_coordination_dir() -> None:
    """Remove ``<host>/.tortoise`` only when this run left it EMPTY.

    The marker dir is host-global by design, so the suite would otherwise leave
    a permanent entry behind on a pristine host. The guard is that ``os.rmdir``
    refuses a NON-EMPTY dir — a concurrent suite's marker, or any production
    file under ``.tortoise``, keeps the directory. (The reaper lock is NOT in
    this dir: since #4098 it lives at
    ``<tempdir>/.tortoise-reaper-<euid>/.reaper.lock``.)

    Residual: a concurrent suite that has created ``active_suites`` but not yet
    written its marker can have that empty dir removed between its ``makedirs``
    and its write; its marker write then fails-closed (logged, not fatal) and
    it runs marker-less for the session. Narrow, and the alternative is a
    permanent empty host entry on every run.
    """
    coordination = os.path.join(HOST_TMPDIR, ".tortoise")
    for path in (os.path.join(coordination, "active_suites"), coordination):
        try:  # noqa: SIM105
            os.rmdir(path)
        except OSError:
            pass


# ── SIGKILLed prior run: reclaim our own stale root ──────────────────────

def _process_start_time(pid: int) -> float | None:
    """Epoch-seconds start time of ``pid`` from the reaper's own helper."""
    try:
        from tortoise.embedded_reaper import _process_start_time as impl
        return impl(pid)
    except Exception:
        return None


def _read_marker(marker_path: str) -> tuple[int, float | None] | None:
    """Parse a ``.session-pid`` marker: ``pid=<int>`` plus optional ``start``.

    Returns None for anything unreadable, or without a parseable POSITIVE pid —
    an unrecognised marker must never authorise a delete. Hardened against the
    shared, world-writable `ROOT_BASE` exactly like the reaper's own marker
    reads: `lstat` (no symlink/FIFO follow — an attacker-planted FIFO would
    otherwise block `open()` forever at conftest import), require a REGULAR
    file, read a BOUNDED number of bytes, and reject `pid <= 0` (os.kill(0, 0)
    probes a process group; a negative pid signals one).
    """
    try:
        st = os.lstat(marker_path)
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    try:
        with open(marker_path) as fh:
            text = fh.read(4096)
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
    if pid is None or pid <= 0:
        return None
    return pid, start


def _marker_owner_provably_dead(marker_path: str) -> bool:
    """True only when the marker's owner is PROVABLY not the same process.

    Three cases must not be conflated: pid dead -> reclaim; pid alive with a
    matching start time -> a concurrent suite, never touch it; pid alive with a
    non-matching start time -> a RECYCLED pid, i.e. the owner is gone ->
    reclaim (a bare ``os.kill(pid, 0)`` would pin a dead suite's root forever).
    Every undeterminable case fails SAFE and keeps the root.

    ``OverflowError`` is caught explicitly: a marker holding a pid wider than
    C ``long`` makes ``os.kill`` raise it, and it is neither an ``OSError`` nor
    a ``ProcessLookupError``. The three sibling pid guards
    (`tortoise.embedded_reaper._pid_alive`, this module's `_pid_file_reason`,
    `tools/tmpdir_sweep._live_pid_protects`) all catch it; this fourth copy
    must not drift.
    """
    rec = _read_marker(marker_path)
    if rec is None:
        return False
    pid, start = rec
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True  # provably dead
    except (PermissionError, OverflowError, OSError):
        return False  # alive-but-unsignalable, or an indeterminate probe
    if start is None:
        return False  # legacy/pid-only record -> keep
    current = _process_start_time(pid)
    if current is None:
        return False  # cannot verify -> fail safe
    return abs(current - start) >= _START_TOLERANCE_S


def sweep_stale_session_roots() -> list[str]:
    """Reclaim roots left by a SIGKILLed suite (a marked root whose pid is dead).

    Best-effort, never raises. A suite killed by SIGKILL/segfault cannot run
    its own teardown, so its root would otherwise survive. Runs at install time
    (the next suite reclaims it) and mirrors the reaper's pid+start-time
    reasoning rather than an age heuristic: a live pid with a matching start
    time means a concurrent suite, whose root is not ours to delete.
    """
    reclaimed: list[str] = []
    try:
        entries = list(root_base_entries())
    except OSError:
        return reclaimed
    for entry in entries:
        if not entry.is_dir(follow_symlinks=False):
            continue
        if not entry.name.startswith(_ROOT_PREFIX):
            continue
        # Provenance: only reclaim a dir owned by THIS euid (#4098/#4236
        # convention). `root_base_entries()` uses the unguarded scandir and
        # the base is world-writable on Linux, so a planted cross-uid dir must
        # never be removed by us.
        try:
            if entry.stat(follow_symlinks=False).st_uid != os.geteuid():
                continue
        except OSError:
            continue
        if not _marker_owner_provably_dead(
                os.path.join(entry.path, _PID_MARKER)):
            continue  # ours-but-live, or undeterminable — never delete on a guess
        if _live_server_in_root(entry.path) is not None:
            continue  # a live server still holds evidence — leave for the reaper
        shutil.rmtree(entry.path, ignore_errors=True)
        reclaimed.append(entry.path)
    return reclaimed


# ── the scan guard: refuse to discover through the SHARED temp dir ───────

def _is_under(child: str, parent: str) -> bool:
    if not isinstance(child, str) or not isinstance(parent, str):
        return False
    return child.startswith(parent.rstrip(os.sep) + os.sep)


def _fd_target_path(fd) -> str | None:
    """Resolve an already-open file descriptor to its path, or None.

    Required because ``os.scandir`` accepts an int fd, and on macOS
    ``shutil.rmtree`` uses that path (``shutil._use_fd_functions`` is True) —
    so without this the guard could be bypassed by exactly the call that walks
    a whole tree.
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


def _path_is_shared(target: str) -> str | None:
    """The matched shared root when ``target`` IS it or an ANCESTOR of it.

    Returns the ROOT (truthy) rather than a bare bool so the guard's failure
    message can name the tree that actually matched instead of always naming
    ``HOST_TMPDIR`` — which on macOS is the WRONG tree for a scan of
    ``ROOT_BASE`` (``/private/tmp``).
    """
    for root in SHARED_TEMP_ROOTS:
        if target == root or _is_under(root, target):
            return root
    return None


def _is_shared_temp_scope(path) -> str | None:
    """The matched shared temp root when ``path`` is one or an ANCESTOR.

    Descendants are allowed on purpose: the session root itself, pytest's tmp
    trees, and the host-global ``<host>/.tortoise/active_suites`` marker dir
    all live under a shared root. Only scanning the shared tree (or above it)
    is the defect. Returns the matched root (truthy) or None.

    Accepts str, bytes, PathLike AND an int file descriptor (all four are legal
    for ``os.scandir``); bytes is decoded with ``os.fsdecode`` so a bytes-path
    caller still gets the original stdlib behaviour instead of a TypeError
    raised from inside the guard, and an fd is resolved so ``shutil.rmtree``
    cannot slip through.

    An int fd is judged WITHOUT ``realpath``: ``F_GETPATH`` / ``/proc/self/fd``
    already return a canonical, symlink-resolved path, and this is the hot path
    — ``shutil.rmtree`` scans through an fd on macOS, so a ``realpath`` here
    re-stats the temp root once per removal, which is exactly the #4214
    per-candidate walk the exit seam was fixed to avoid.
    """
    if isinstance(path, int):
        resolved = _fd_target_path(path)
        if resolved is None:
            return None  # unidentifiable fd — cannot judge, must not guess
        return _path_is_shared(os.path.abspath(resolved))
    try:
        raw = os.fspath(path)
    except TypeError:
        return None
    if isinstance(raw, bytes):
        raw = os.fsdecode(raw)
    try:
        # A STRING path needs realpath: a symlink in the path can point at the
        # shared tree (`scandir(<tmp>/link)` where link -> $TMPDIR).
        target = os.path.realpath(raw)
    except (TypeError, ValueError, OSError):
        return None
    return _path_is_shared(target)


def _fmt_path(path) -> str:
    """repr for a guard message; an int fd has no fspath and must not raise."""
    if isinstance(path, int):
        return f"fd {path}"
    try:
        return repr(os.fspath(path))
    except TypeError:
        return repr(path)


def _guard_reason(matched_root: str) -> str:
    return (
        "scanning a SHARED temp dir is forbidden in tests (#3752): this is "
        "the non-hermetic, cross-contaminating, O(whole-host) discovery that "
        f"the private session temp root exists to prevent ({matched_root}). "
        "Use tests._tmpdir_hygiene.scan_root() (the private per-session "
        "root), or the test's own tmp_path, instead."
    )


def install_scan_guard() -> None:
    """Fail loudly on any in-process scan of the shared temp dir.

    Patches ``os.scandir`` / ``os.walk`` / ``os.listdir``. ``os.walk`` resolves
    ``os.scandir`` from the module namespace at call time, so patching all
    three is belt and braces rather than duplication. An int fd (which
    ``shutil.rmtree`` passes on macOS) is resolved rather than ignored.
    Idempotent, and reversible via ``uninstall_scan_guard()`` so the guard can
    be exercised by its own test (the positive control).
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
        matched = _is_shared_temp_scope(path)
        if matched:
            raise SharedTmpdirScanError(
                f"os.scandir({_fmt_path(path)}) — {_guard_reason(matched)}")
        return original_scandir(path, *args, **kwargs)

    def guarded_listdir(path=".", *args, **kwargs):
        matched = _is_shared_temp_scope(path)
        if matched:
            raise SharedTmpdirScanError(
                f"os.listdir({_fmt_path(path)}) — {_guard_reason(matched)}")
        return original_listdir(path, *args, **kwargs)

    def guarded_walk(top, *args, **kwargs):
        matched = _is_shared_temp_scope(top)
        if matched:
            raise SharedTmpdirScanError(
                f"os.walk({_fmt_path(top)}) — {_guard_reason(matched)}")
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


def install_tolerant_tempdir_cleanup() -> None:
    """Keep a live server's temp dir from reddening a green shard (#7735).

    WHY (measured, #7735). ``TemporaryDirectory.__exit__`` calls ``cleanup()``,
    which calls ``shutil.rmtree(..., ignore_errors=False)``. A directory that is
    still held by an embedded server which deliberately OUTLIVES the suite — the
    live-redis case this module already handles for the private session root
    (#3752) — cannot be removed atomically: ``rmtree`` empties it and the server
    re-creates an entry before ``os.rmdir``, which then raises

        OSError: [Errno 39] Directory not empty

    That exception escapes ``__exit__``, so pytest reports it as a test ERROR
    and the shard goes red even though every test in it PASSED — observed as
    ``2054 passed, 15 skipped, 1 error``, on three consecutive runs of the same
    commit with a different nodeid each time (the error follows whichever test
    is tearing down, not the test's subject).

    That is the same failure of the same invariant this module already states
    for its own teardown — "teardown must not convert a green suite red".

    STRUCTURAL, NOT PER-FILE. The suite has several hundred
    ``TemporaryDirectory(`` call sites — ``git grep -o "TemporaryDirectory("
    -- tests/ | wc -l`` counts them, and it counts OCCURRENCES, not lines
    (one line of ``tests/`` can hold two calls). No literal count is quoted
    here on purpose: that command counts the module comments that quote it
    too, so any number written into this sentence re-stales on the very edit
    that adds it (#7735 review, F3a). Patching them one at a time would be a band-aid on
    a shared lifecycle bug, and the next test written would reintroduce it. So
    the fix is applied once, here, next to the other tempdir policy.

    NARROW AND REPORTED — deliberately NOT ``ignore_cleanup_errors=True``.
    The blanket stdlib lever would swallow EVERY ``rmtree`` failure (``EROFS``,
    ``EACCES``, ``EBUSY``, ``EIO`` …), not only this race. Tolerating only
    ``ENOTEMPTY`` keeps the shard green; the directory is named in a warning and
    left for the reaper. Everything else still raises.

    WHAT THE TRACKER DOES NOT CATCH — corrected; an earlier clause said the
    tracker "cannot see a context-managed directory", which is BACKWARDS.
    ``TemporaryDirectory`` resolves the module-global ``tempfile.mkdtemp``, which
    the autouse ``TrackedTempfileArtifacts`` patches, so a directory it creates
    in a test body IS recorded in ``tracker.created`` (verified). What the
    tracker does NOT do is ASSERT on it: its teardown rmtrees with
    ``ignore_errors=True`` and only LOGS the live-server paths it leaves for the
    reaper, so a leftover directory never reddens the run. The ``ENOTEMPTY``
    ``ERROR`` was therefore the only HARD signal this case produced — which is
    why tolerating it must be paired with an explicit report
    (``write_tolerated_cleanup_report``), not only a log record.
    """
    global _ORIGINAL_TEMPDIR_CLEANUP
    original_cleanup = tempfile.TemporaryDirectory.cleanup
    if getattr(original_cleanup, "_tortoise_tolerant_cleanup", False):
        return  # idempotent: a second pytest.main() must not double-wrap
    if _ORIGINAL_TEMPDIR_CLEANUP is None:
        _ORIGINAL_TEMPDIR_CLEANUP = original_cleanup

    @functools.wraps(original_cleanup)
    def _cleanup(self):
        try:
            return original_cleanup(self)
        except OSError as exc:
            if exc.errno != errno.ENOTEMPTY:
                raise
            # #7735: a live server re-created an entry between rmtree's
            # emptying and its final rmdir, so the directory cannot be removed
            # atomically. Stay green — but NOT silent. The log record alone is
            # invisible in CI (capture discards a passing test's records), so
            # the path is ALSO accumulated for the session-end artifact.
            _TOLERATED_CLEANUP_LEAKS.append({
                "path": self.name,
                "errno": errno.ENOTEMPTY,
                "reason": exc.strerror or str(exc),
            })
            logger.warning(
                "#7735: leaving %s in place — a live server still holds it "
                "(%s); the reaper owns it. Recorded in "
                "tempdir-hygiene-end.json, not swallowed.",
                self.name, exc.strerror or exc)
            return None

    _cleanup._tortoise_tolerant_cleanup = True  # type: ignore[attr-defined]
    tempfile.TemporaryDirectory.cleanup = _cleanup  # type: ignore[method-assign]


def uninstall_tolerant_tempdir_cleanup() -> None:
    """Restore the original ``cleanup`` (used by the fix's own test)."""
    if _ORIGINAL_TEMPDIR_CLEANUP is None:
        return
    tempfile.TemporaryDirectory.cleanup = _ORIGINAL_TEMPDIR_CLEANUP  # type: ignore[method-assign]


def tolerated_cleanup_leaks() -> list[dict[str, object]]:
    """The #7735 ENOTEMPTY teardown leaks tolerated so far this session."""
    return [dict(entry) for entry in _TOLERATED_CLEANUP_LEAKS]


def _discard_tolerated_leaks(paths: list[str]) -> None:
    """Drop probe entries a test added, so the session artifact stays real.

    This module's own test deliberately drives the ENOTEMPTY path; without
    this, ``tests/test_tmpdir_hygiene.py`` would put a synthetic path in every
    CI run's artifact and the channel would read as permanent noise.
    """
    drop = set(paths)
    _TOLERATED_CLEANUP_LEAKS[:] = [
        e for e in _TOLERATED_CLEANUP_LEAKS if e["path"] not in drop]


def write_tolerated_cleanup_report(log_dir: str | None = None) -> str | None:
    """Mirror the #7735 tolerated-leak record to a JSON artifact (#1103 shape).

    WHY A FILE, NOT JUST THE LOG RECORD (measured). A ``logger.warning`` from a
    PASSING test is discarded by pytest capture: under this CI's own flags
    (``-v --timeout=300 -r fEs --junitxml=… -o junit_family=xunit1``, and with
    xdist) the #7735 warning appears 0 times in ``/tmp/pytest.log`` and 0 times
    in the junit XML. ``warnings.warn`` would be visible, but pytest dedupes it
    per code location (so only the FIRST of N leaks is shown) and any future
    ``filterwarnings = error`` would turn the report into a RED. So — exactly
    like the #1103 redislite end-sweep decision — the record is accumulated and
    written to a file the workflow dumps.

    Best-effort: writing the report never fails the suite. Written ONLY when at
    least one leak was tolerated, so a clean xdist worker never overwrites a
    peer worker's record with an empty one. The write MERGES with whatever is
    already there keyed by path instead of truncating, so IF xdist is
    re-admitted, every worker's OWN session teardown against this one artifact
    path cannot let the last worker to finish discard a peer's leak — the
    invisibility this report exists to remove. That branch is INERT at this
    head: CI runs SERIAL (``XDIST_WORKERS=0`` in ``python-ci.yml``, with the
    only non-zero admission behind an inert ``if false``), so a single session
    owns the path today. (A merge can still lose one record if two workers
    write in the same instant; the artifact then still names the defect, so the
    leak stays observable.)
    """
    if not _TOLERATED_CLEANUP_LEAKS:
        return None
    try:
        if log_dir is None:
            log_dir = os.environ.get("RUNNER_TEMP") or tempfile.gettempdir()
        log_path = os.path.join(log_dir, "tempdir-hygiene-end.json")
        by_path: dict[str, dict[str, object]] = {}
        try:
            with open(log_path) as fh:
                prior = json.load(fh)
            for entry in prior.get("tolerated_cleanup_leaks", []):
                if isinstance(entry, dict) and "path" in entry:
                    by_path[str(entry["path"])] = entry
        except (OSError, ValueError, AttributeError, TypeError):
            pass  # no prior artifact, or an unreadable/garbled one — start fresh
        for entry in tolerated_cleanup_leaks():
            entry.setdefault("pid", os.getpid())
            by_path[str(entry["path"])] = entry
        with open(log_path, "w") as fh:
            json.dump({
                "tolerated_cleanup_leaks": [
                    by_path[path] for path in sorted(by_path)],
            }, fh, indent=2)
    except OSError:
        return None
    return log_path
