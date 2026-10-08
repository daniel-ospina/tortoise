# pytest-timeout guard integrity (#7655)

`pytest-timeout`'s default `signal` method arms the per-test guard with
`signal.setitimer(signal.ITIMER_REAL, timeout)` and installs a SIGALRM handler.
`signal.alarm(n)`, `signal.setitimer(ITIMER_REAL, ...)` and
`signal.signal(SIGALRM, ...)` all use the **same** timer/handler, so one such
call from inside a test replaces the guard: the per-test timeout never fires
and a hang is caught only by the coarse shard watchdog — a budget overrun read
as a test failure.

Measured 2026-10-08 (pytest-timeout 2.4.0, `--timeout=3`):

| cell | result |
|---|---|
| `sleep(60)` + `signal.alarm(600)` | **INERT** (outer bound at 15 s) |
| same stall, no alarm (control) | FIRED at 3.1 s |
| `signal.signal(SIGALRM, …)` + `sleep(60)` | **INERT** (outer bound at 20 s) |
| `--timeout-method=thread` instead of `signal` | fires, but writes **no `junit.xml`** and reports no failing nodeid |

The `signal` method is therefore kept (it produces the failing nodeid and JUnit
XML) and the theft is removed.

## The mechanism

`tests/_signal_hygiene.py`, re-exported by `tests/conftest.py` as a suite-wide
autouse fixture:

- `harness_sigalrm_integrity` — while the harness owns the timer, intercepts
  `signal.alarm`, `signal.setitimer` and `signal.signal` and **refuses** any
  call targeting SIGALRM / `ITIMER_REAL`, failing the test at the offending
  line. Calls originating from the `pytest_timeout` module are exempt (its
  `cancel()` legitimately disarms on a failing test); non-SIGALRM handlers
  delegate untouched.
- `harness_safe_sigalrm(seconds, handler)` — the sanctioned way for a **test**
  to use SIGALRM: saves the harness handler + remaining timer and restores both.
- `harness_relinquish_sigalrm()` — the sanctioned way to run in-process
  **product** code that owns its own alarm. The reaper CLI
  (`tortoise/embedded_reaper.py::main`) arms a SIGALRM watchdog; its in-process
  call sites in `tests/test_reaper.py` run through a `_reaper_main()` helper
  wrapping this block.

Run with no `--timeout` armed, the fixture is inert.

## Stated residuals

- A pre-bound alias (`from signal import alarm`), `_signal`, a C extension,
  SIGALRM blocked via `pthread_sigmask`, a sanctioned block open in another
  thread, or a spoofed `signal.getitimer` bypasses the Python-level wrapper.
  The guard stops the ordinary in-process `signal` call; it does not stop code
  that deliberately reaches around the `signal` module.
- A GIL-holding C call that makes SIGALRM undeliverable is #7649's domain.
- `--timeout-func-only` runs are not covered (pytest-timeout arms during the
  call phase; the repo uses none).
- Fail-closed by design: *every* in-process SIGALRM use is refused — including a
  correct save/restore — so a library that arms SIGALRM must run in a
  subprocess or be wrapped in `harness_relinquish_sigalrm`.

Pins: `tests/test_timeout_not_disarmable.py` (the refusals, both sanctioned
restores, the counter-leak guard, and the end-to-end reproducer).

Related: #7649 (C-extension half), #7674 (`faulthandler_timeout` backstop).
