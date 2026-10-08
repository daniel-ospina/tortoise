"""#7649/#7655/#3719 — a stalled test must dump its own frame.

The cheap per-test guard (`pytest-timeout --timeout=300`, its default `signal`
method) fires on SIGALRM, dumps every thread EXCEPT the stuck one, and is
silently disarmed by any in-process `signal.alarm()` (shared ``ITIMER_REAL``,
#7655). pytest's built-in faulthandler watchdog is a thread: it needs no signal
and no ITIMER, which is why neither the GIL-holding stall (#7649) nor the alarm
collision (#7655) can quiet it, and its dump names the stuck thread.

``faulthandler_timeout`` is the one line that turns that on. These tests pin the
config invariant (the line must fire *before* ``--timeout``, or the dump timer
is cancelled when pytest-timeout ends the item) and reproduce the stall: a test
that holds the GIL continuously still gets a named frame.
"""

from __future__ import annotations

import re
import signal
import subprocess
import sys
import time
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"

# Catastrophic regex backtracking: a pure-C loop that holds the GIL. The
# ``signal`` method of pytest-timeout does reach it (the re engine checks
# signals), but the ``thread`` method starves on the GIL — which is why the
# faulthandler watchdog thread (no GIL needed) is the mechanism that works.
_GIL_STALL = 're.match(r"(a+)+$", "a" * 32 + "b")'


def test_faulthandler_timeout_is_armed_below_the_per_test_budget():
    """Without the pyproject line a stall produces no frame — so this REDs.

    The bound must be strictly below pytest-timeout's. ``_pytest/faulthandler``
    arms ``dump_traceback_later`` per item and CANCELS it in a ``finally``; if
    pytest-timeout fires first the item ends and the dump timer is cancelled
    before it can write, making ``faulthandler_timeout >= --timeout`` a silent
    no-op rather than a slower dump.
    """
    with PYPROJECT.open("rb") as fh:
        ini = tomllib.load(fh)["tool"]["pytest"]["ini_options"]
    configured = ini.get("faulthandler_timeout")
    assert configured is not None, (
        "faulthandler_timeout is not set in pyproject.toml (#7649/#7655/#3719): "
        "a stalled test would produce a red with no frame"
    )
    # `faulthandler_timeout` is global, so every workflow's pytest leg must be
    # checked — a shorter `--timeout` added to any of them would silently make
    # the dump timer a no-op on that leg. Backslash continuations are folded
    # first, or a flag on a continuation line has no `pytest` beside it.
    text = "\n".join(
        path.read_text(encoding="utf-8").replace("\\\n", " ")
        for path in sorted(WORKFLOWS_DIR.glob("*.yml"))
    )
    budgets = [
        int(m)
        for line in text.splitlines()
        if not line.lstrip().startswith("#") and "pytest" in line
        for m in re.findall(r"--timeout[= ](\d+)", line)
    ]
    assert budgets, "no workflow arms pytest-timeout (`--timeout=`)"
    assert 0 < float(configured) < min(budgets), (
        f"faulthandler_timeout={configured} must fire before pytest-timeout's "
        f"smallest --timeout={min(budgets)}, or the dump timer is cancelled first"
    )


def test_a_stall_dumps_its_own_frame(tmp_path):
    """A GIL-holding stall must produce a named frame, not a 15-minute silence.

    The dump is produced with a 1s bound so the test is fast; the stall itself
    never returns, so the still-stuck child is ended as soon as the dump has
    been observed. Asserting the *frame* (not just the header) is the point:
    pytest-timeout's own dump omits the stuck thread, so a bare "Timeout" banner
    would not prove this guard fired.
    """
    (tmp_path / "test_stall.py").write_text(
        "import re\n\n\ndef test_stall():\n    " + _GIL_STALL + "\n",
        encoding="utf-8",
    )
    log = tmp_path / "stall.log"
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "-o",
        "faulthandler_timeout=1",
        "--timeout=300",
        "test_stall.py",
    ]
    with log.open("w", encoding="utf-8") as fh:
        proc = subprocess.Popen(cmd, cwd=tmp_path, stdout=fh, stderr=subprocess.STDOUT)
        try:
            _wait_for_text(log, "Timeout (0:00:01)!", proc, deadline_s=30)
        finally:
            if proc.poll() is None:
                proc.send_signal(signal.SIGINT)
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
    output = log.read_text(encoding="utf-8", errors="replace")
    assert "Timeout (0:00:01)!" in output, (
        f"faulthandler did not fire for the GIL-holding stall:\n{output}"
    )
    assert "in test_stall" in output and "test_stall.py" in output, (
        f"faulthandler fired but did not name the stuck frame:\n{output}"
    )


def _wait_for_text(path: Path, needle: str, proc: subprocess.Popen, deadline_s: float) -> None:
    """Return once ``needle`` is in the (separately reopened) child log."""
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline and proc.poll() is None:
        if path.exists() and needle in path.read_text(encoding="utf-8", errors="replace"):
            return
        time.sleep(0.2)
