"""#4069 — teardown-tracked tempfile artifacts for the test suite.

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
