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
* A directory whose `redis.pid` is live — or unreadable/unparseable — is
  left for the reaper rather than removed out from under a running embedded
  server (fail closed, mirroring the sweep: a pid that cannot be proven dead
  is treated as live). The skip is logged.
* `shutil.rmtree` never follows symlinks; removal is `ignore_errors` so an
  already-cleaned directory (or a concurrent reaper) is a no-op — the fixture
  is idempotent.

This does not replace the reaper: a SIGKILLed or watchdog-killed process runs
no teardown at all, and that residue is still the reaper's job
(`docs/infra/embedded-reaper-cron.md`). It removes the *normal-exit* leak.
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
from collections.abc import Iterator

import pytest

logger = logging.getLogger(__name__)

_PID_FILENAMES = ("redis.pid",)

# ⚠️ TEMPORARY DIAGNOSTIC PROBE (PR #4076 blocker) — REMOVE BEFORE LANDING.
# A redislite instance directory IS a `tempfile.mkdtemp()` result
# (redislite/client.py:209), so it lands in the tracked set; the pid guard
# below cannot see the window before redis-server writes its pidfile. This
# probe leaves every redislite instance dir to the reaper, to measure whether
# the tracker's removal of those dirs is what reds test_pack_state in CI.
_REDISLITE_INSTANCE_MARKERS = ("redis.config", "redis.socket")


def _protected_reason(path: str) -> str | None:
    """Why `path` must NOT be removed, or None when teardown is safe.

    Fail-CLOSED, mirroring `tools/tmpdir_sweep.py::_live_pid_protects`: a
    `redis.pid` that is live, unreadable, unparseable, non-positive, or whose
    probe fails for any reason is treated as a running server and the
    directory is left for the reaper. Only a provably dead pid (or no pid
    file at all) permits removal — the reaper cannot reclassify a directory
    this fixture has already deleted.
    """
    # ⚠️ TEMPORARY DIAGNOSTIC PROBE (PR #4076 blocker) — REMOVE BEFORE LANDING.
    for marker in _REDISLITE_INSTANCE_MARKERS:
        if os.path.lexists(os.path.join(path, marker)):
            return f"redislite instance dir ({marker} present) — left to the reaper"

    for name in _PID_FILENAMES:
        pid_file = os.path.join(path, name)
        if not os.path.isfile(pid_file):
            continue
        try:
            with open(pid_file, encoding="utf-8", errors="replace") as fh:
                raw = fh.read().strip()
        except OSError as exc:
            return f"unreadable {name} ({exc}) — treated as live"
        try:
            pid = int(raw)
        except ValueError:
            return f"unparseable {name} — treated as live"
        if pid <= 0:
            return f"nonsensical pid {pid} in {name}"
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return None  # provably dead -> safe to remove
        except PermissionError:
            return f"pid {pid} alive (no permission to signal)"
        except OverflowError:
            return f"pid {pid} out of range — treated as live"
        except OSError as exc:
            return f"pid {pid} probe failed ({exc}) — treated as live"
        return f"live redis pid {pid}"
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

    ⚠️ TEMPORARY DIAGNOSTIC PROBE (PR #4076 blocker) — REMOVE BEFORE LANDING.
    Inert on purpose: the suite runs with NO per-test tracking, to measure
    whether the tracker is implicated in the CI red at all. The redislite-dir
    protection probe already exonerated the tracker's removal of redislite
    instance dirs; this settles the tracker as a whole.
    """
    # probe: the tracker is never entered for this run
    yield
