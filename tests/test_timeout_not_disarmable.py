"""#7655 — the per-test timeout must not be silently disarmed in-process.

``pytest-timeout``'s ``signal`` method shares ``ITIMER_REAL`` with the code
under test, so one ``signal.alarm()`` (or ``setitimer(ITIMER_REAL, ...)``)
inside a test replaces the per-test alarm and a later hang is only caught by
the coarse shard watchdog.  ``tests/_signal_hygiene.py`` refuses that takeover;
these tests pin the refusal, the sanctioned replacement, and — end to end —
that the issue's own reproducer now fails fast instead of stalling a shard.

The C-extension half of the same root is #7649; this file covers the
in-process theft only.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from tests._signal_hygiene import (
    _REAL_SETITIMER,
    HarnessAlarmStolen,
    _make_guarded_alarm,
    _make_guarded_setitimer,
    harness_safe_sigalrm,
)

pytestmark = pytest.mark.skipif(
    not hasattr(signal, "SIGALRM"), reason="SIGALRM is not available (POSIX only)"
)

_REPO_ROOT = Path(__file__).resolve().parent.parent


def test_guard_refuses_alarm_while_the_harness_owns_the_timer(monkeypatch):
    """`signal.alarm` must not be allowed to replace the harness timer."""
    monkeypatch.setattr(signal, "getitimer", lambda which: (30.0, 0.0))
    with pytest.raises(HarnessAlarmStolen, match="#7655"):
        _make_guarded_alarm()(600)


def test_guard_refuses_setitimer_on_itimer_real(monkeypatch):
    """The same theft through `setitimer(ITIMER_REAL, ...)` is refused too."""
    monkeypatch.setattr(signal, "getitimer", lambda which: (30.0, 0.0))
    with pytest.raises(HarnessAlarmStolen, match="#7655"):
        _make_guarded_setitimer()(signal.ITIMER_REAL, 600)


def test_safe_sigalrm_restores_the_harness_timer():
    """The sanctioned helper must leave the harness timer armed (#7655).

    A passing test is the point: before the fix, the harness alarm was gone
    after the in-test alarm, so nothing fired here.
    """
    outer_timer = signal.getitimer(signal.ITIMER_REAL)
    outer_handler = signal.getsignal(signal.SIGALRM)
    fired: list[bool] = []

    def harness_handler(signum, frame):
        fired.append(True)

    _REAL_SETITIMER(signal.ITIMER_REAL, 0)  # take over from the live harness
    try:
        _REAL_SETITIMER(signal.ITIMER_REAL, 0.4)  # simulate the harness guard
        signal.signal(signal.SIGALRM, harness_handler)
        with harness_safe_sigalrm(0.05, lambda signum, frame: None):
            time.sleep(0.12)  # the in-test alarm fires harmlessly in here
        time.sleep(0.55)  # the RESTORED harness alarm must fire out here
        assert fired, "the harness alarm was not restored after an in-test alarm"
    finally:
        _REAL_SETITIMER(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, outer_handler)
        if outer_timer[0] > 0:
            _REAL_SETITIMER(signal.ITIMER_REAL, outer_timer[0], outer_timer[1])


def test_the_reproducer_fails_fast_instead_of_stalling_the_run(tmp_path):
    """The issue's reproducer, run under the repo's guard, must fail fast.

    Without the guard the inner run reaches `signal.alarm(600)` — which
    silences its own `--timeout=5` — and then sits in `sleep(60)` until an
    outer watchdog kills it. With the guard it fails at the offending line.
    This is the pin: reverting the guard makes it run past the bound.
    """
    test_file = tmp_path / "test_disarm_repro.py"
    test_file.write_text(
        textwrap.dedent(
            """
            import signal, time


            def test_arms_its_own_alarm_and_hangs():
                signal.alarm(600)   # steals ITIMER_REAL from pytest-timeout
                time.sleep(60)
            """
        ),
        encoding="utf-8",
    )
    env = dict(os.environ, PYTHONPATH=str(_REPO_ROOT))
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        str(test_file),
        "-q",
        "-p",
        "tests._signal_hygiene",
        "--timeout=5",
        "--timeout-method=signal",
        "-p",
        "no:cacheprovider",
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(_REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=45,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            "#7655 not fixed: an in-test signal.alarm hung the inner run past "
            "45s — the per-test timeout was silently disarmed"
        )
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, f"the disarm must fail the inner test:\n{out}"
    assert "#7655" in out, f"the guard's refusal was not reported:\n{out}"
