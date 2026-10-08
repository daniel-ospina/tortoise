"""#7655 — do not let in-process ``signal.alarm`` silently disarm the guard.

``pytest-timeout``'s default ``signal`` method arms the per-test guard with
``signal.setitimer(signal.ITIMER_REAL, timeout)`` and installs a SIGALRM
handler.  ``signal.alarm(n)`` — and ``signal.setitimer(ITIMER_REAL, ...)`` —
use the **same** ``ITIMER_REAL``, and ``signal.signal(SIGALRM, ...)`` replaces
the handler, so any in-process call from a test takes the per-test alarm away:
the guard never fires and the hang is only caught by the coarse shard watchdog,
i.e. a budget overrun read as a test failure.  Measured on 2026-10-08: a
``time.sleep(60)`` after ``signal.alarm(600)`` ran to a 15 s outer bound instead
of failing at ``--timeout=3``, while the same stall **without** the alarm failed
at 3.1 s (issue #7655; the C-extension half of the pair is #7649).

This module makes the takeover loud and immediate instead of silent:

* The autouse fixture :func:`harness_sigalrm_integrity` intercepts
  ``signal.alarm``, ``signal.setitimer`` and ``signal.signal`` for the duration
  of a test, **only while the harness has its timer armed**, and refuses any
  call that targets SIGALRM / ``ITIMER_REAL``.  The test fails at the offending
  line — naming #7655 — before it can hang.
* :func:`harness_safe_sigalrm` is the sanctioned way for a **test** to use
  SIGALRM: it saves the harness handler and the remaining timer and restores
  **both** on exit, so the per-test timeout survives.
* :func:`harness_relinquish_sigalrm` is the sanctioned way to run in-process
  **product** code that owns its own alarm (the reaper CLI arms a SIGALRM
  watchdog; ``tortoise/embedded_reaper.py``): it silences the guard for the
  block and restores the harness handler + timer afterwards.
* Calls made by ``pytest_timeout`` itself are allowed: its ``cancel()`` zeroes
  ``ITIMER_REAL`` when a test fails or enters pdb, and a run with no timeout
  armed (``pyproject.toml`` sets none; CI passes ``--timeout=300``) is
  untouched.
* ``--timeout-func-only`` runs are **not** covered: pytest-timeout arms its
  timer during the call phase, after this fixture's setup has already observed
  no timer.  Stated rather than silently assumed.
* A call made through a **pre-bound** alias (``from signal import alarm``) or by
  a **C extension** bypasses the Python-level guard; that is #7649's domain.
"""

from __future__ import annotations

import signal
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest

_HAVE_SIGALRM = hasattr(signal, "SIGALRM") and hasattr(signal, "ITIMER_REAL")
_REAL_ALARM: Callable[[int], Any] | None = getattr(signal, "alarm", None)
_REAL_SETITIMER: Callable[..., Any] | None = getattr(signal, "setitimer", None)
_REAL_SIGNAL: Callable[..., Any] | None = getattr(signal, "signal", None)

# Depth counter for the sanctioned blocks below: while it is > 0 the guard
# delegates, because the caller has explicitly taken ownership of SIGALRM.
_relinquished = 0

REFUSAL = (
    "#7655: {call} would silently DISARM the pytest-timeout guard. "
    "pytest-timeout's 'signal' method shares signal.ITIMER_REAL with any "
    "in-process alarm, so this call REPLACES the per-test timeout and a later "
    "hang is never caught (only the coarse shard watchdog remains). Use "
    "`with harness_safe_sigalrm(<seconds>, <handler>)` from "
    "tests._signal_hygiene (it saves and restores the harness timer), or run "
    "the alarming code in a subprocess. See #7655."
)


class HarnessAlarmStolen(AssertionError):
    """An in-process call tried to take pytest-timeout's SIGALRM/ITIMER_REAL."""


def harness_alarm_remaining() -> float:
    """Seconds left on ITIMER_REAL, or 0.0 when no timer is armed."""
    if not _HAVE_SIGALRM:
        return 0.0
    return float(signal.getitimer(signal.ITIMER_REAL)[0])


def _from_pytest_timeout() -> bool:
    """True when the current call stack belongs to pytest-timeout's own timer.

    The guard must not refuse pytest-timeout's ``set_timer``/``cancel`` — they
    are the harness legitimately (re)programming its own timer.
    """
    frame = sys._getframe(1)
    while frame is not None:
        if frame.f_globals.get("__name__") == "pytest_timeout":
            return True
        frame = frame.f_back
    return False


def _guard_active() -> bool:
    """True when the harness owns SIGALRM and no sanctioned block is open."""
    return (
        _HAVE_SIGALRM
        and _relinquished == 0
        and not _from_pytest_timeout()
        and harness_alarm_remaining() > 0
    )


def _make_guarded_alarm() -> Callable[[int], Any]:
    """A ``signal.alarm`` that refuses to steal the harness timer."""
    assert _REAL_ALARM is not None

    def guarded_alarm(seconds: int) -> Any:
        if _guard_active():
            raise HarnessAlarmStolen(
                REFUSAL.format(call=f"signal.alarm({seconds!r})")
            )
        return _REAL_ALARM(seconds)

    return guarded_alarm


def _make_guarded_setitimer() -> Callable[..., Any]:
    """A ``signal.setitimer`` that refuses to steal the harness timer."""
    assert _REAL_SETITIMER is not None

    def guarded_setitimer(which: int, seconds: float, interval: float = 0.0) -> Any:
        if which == signal.ITIMER_REAL and _guard_active():
            raise HarnessAlarmStolen(
                REFUSAL.format(call="signal.setitimer(ITIMER_REAL, ...)")
            )
        return _REAL_SETITIMER(which, seconds, interval)

    return guarded_setitimer


def _make_guarded_signal() -> Callable[..., Any]:
    """A ``signal.signal`` that refuses to replace the harness SIGALRM handler.

    Replacing the handler defeats the guard just as completely as stealing the
    timer: the alarm still fires, but into the wrong handler, so pytest-timeout
    never sees it.
    """
    assert _REAL_SIGNAL is not None

    def guarded_signal(signalnum: int, handler: Any) -> Any:
        if signalnum == signal.SIGALRM and _guard_active():
            raise HarnessAlarmStolen(
                REFUSAL.format(call="signal.signal(SIGALRM, ...)")
            )
        return _REAL_SIGNAL(signalnum, handler)

    return guarded_signal


@contextmanager
def harness_safe_sigalrm(
    seconds: float, handler: Callable[..., Any]
) -> Iterator[None]:
    """Arm SIGALRM for a block WITHOUT losing the per-test guard (#7655).

    pytest-timeout owns ``ITIMER_REAL`` for the current item; a bare
    ``signal.alarm()`` clears it and only the coarse shard watchdog survives.
    Save the harness handler plus the remaining timer and restore **both** on
    exit, so a hang anywhere else in the test is still caught on time.
    """
    if not _HAVE_SIGALRM:
        raise RuntimeError("SIGALRM is not available on this platform")
    _require_real_primitives()
    global _relinquished
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    # Use the real primitives directly: the sanctioned helper is exactly the
    # place the guard must not refuse.
    _relinquished += 1
    _REAL_SIGNAL(signal.SIGALRM, handler)
    _REAL_SETITIMER(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        _relinquished -= 1
        _restore_sigalrm(previous_handler, previous_timer)


@contextmanager
def harness_relinquish_sigalrm() -> Iterator[None]:
    """Run in-process code that owns its OWN alarm, restoring the guard after.

    ``tortoise.embedded_reaper.main()`` arms a SIGALRM watchdog and cancels it
    on exit — correct when the reaper is its own process, but in-process it
    leaves the pytest-timeout guard disarmed for the rest of the test.  Wrap the
    call in this block: the guard stands down for its duration and the harness
    handler + remaining timer are restored on exit (#7655).
    """
    if not _HAVE_SIGALRM:
        yield
        return
    _require_real_primitives()
    global _relinquished
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    _relinquished += 1
    try:
        yield
    finally:
        _relinquished -= 1
        _restore_sigalrm(previous_handler, previous_timer)


def _require_real_primitives() -> None:
    assert _REAL_SIGNAL is not None and _REAL_SETITIMER is not None


def _restore_sigalrm(handler: Any, timer: tuple[float, float]) -> None:
    _require_real_primitives()
    _REAL_SETITIMER(signal.ITIMER_REAL, 0)
    _REAL_SIGNAL(signal.SIGALRM, handler)
    if timer[0] > 0:
        _REAL_SETITIMER(signal.ITIMER_REAL, timer[0], timer[1])


@pytest.fixture(autouse=True)
def harness_sigalrm_integrity(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Refuse any in-process attempt to take pytest-timeout's SIGALRM/ITIMER_REAL.

    Enforced only while the harness has a timer armed, so a run without
    ``--timeout`` (there is none in ``pyproject.toml``; CI passes
    ``--timeout=300``) is unaffected.
    """
    if not _HAVE_SIGALRM or harness_alarm_remaining() <= 0:
        yield
        return
    if _REAL_ALARM is not None:
        monkeypatch.setattr(signal, "alarm", _make_guarded_alarm(), raising=False)
    if _REAL_SETITIMER is not None:
        monkeypatch.setattr(
            signal, "setitimer", _make_guarded_setitimer(), raising=False
        )
    if _REAL_SIGNAL is not None:
        monkeypatch.setattr(signal, "signal", _make_guarded_signal(), raising=False)
    yield
