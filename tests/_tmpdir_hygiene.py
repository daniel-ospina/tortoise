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
* A directory still holding a **live** `redis.pid` is left for the reaper
  rather than removed out from under a running embedded server (a buggy test
  is not made worse by this fixture). The skip is logged.
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


def _live_pid_in(path: str) -> int | None:
    """The live redis pid inside `path`, or None. Fail-closed -> None here.

    A pid file that exists but is unreadable/unparseable is treated as NOT
    live (the process is gone as far as we can tell); the reaper's own
    classification is the authority for the ambiguous case.
    """
    for name in _PID_FILENAMES:
        pid_file = os.path.join(path, name)
        if not os.path.isfile(pid_file):
            continue
        try:
            with open(pid_file, encoding="utf-8", errors="replace") as fh:
                pid = int(fh.read().strip())
        except (OSError, ValueError):
            return None
        if pid <= 0:
            return None
        try:
            os.kill(pid, 0)
        except OSError:
            return None
        return pid
    return None


class TrackedTempfileArtifacts:
    """Context manager: record and remove `tempfile.mkdtemp` directories.

    Usable directly in a test (see `tests/test_tmpdir_hygiene.py`) and as the
    body of the autouse fixture below. Reentrant — a nested tracker restores
    the outer one's patched `mkdtemp` rather than the original.
    """

    def __init__(self) -> None:
        self.created: list[str] = []
        self.skipped_live: list[tuple[str, int]] = []
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
            pid = _live_pid_in(path)
            if pid is not None:
                self.skipped_live.append((path, pid))
                logger.warning(
                    "#4069: leaving %s in place — live embedded server pid %d "
                    "(the reaper owns it)", path, pid)
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
