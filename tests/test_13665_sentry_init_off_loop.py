"""#13665 — Sentry startup must not block the event loop.

#13665's second failure shape is ``HTTP 000``: ``GET /v1/search`` returning NO
response at all. That shape has no handler, no refusal and no ``error_text`` to
read, because the two pieces that would produce one — an ``asyncio`` timer
callback (``WaitBoundMiddleware``'s deadline) and the ``send`` of a response —
are BOTH loop work. A loop held for seconds is therefore not a slow answer, it
is no answer.

⛔ MEASURED ON THE DEPLOYED BOX, NOT HYPOTHESISED. ``/health`` on head
``18d406258f7b`` (the exact head #13665 measured) carries the loop-block
sampler's capture — ``loop_blocks`` → ``max_age_s: 5.212``, also appended to
``/data/loop-blocks.jsonl``::

    <frozen importlib._bootstrap>:1360 in _find_and_load
    ...
    mcp.py:36 in <module>                        # sentry_sdk/integrations/mcp.py
    utils.py:1812 in package_version             # sentry_sdk/utils.py
    utils.py:1801 in _get_installed_modules
    utils.py:1767 in _generate_installed_modules
    __init__.py:444 in metadata                  # importlib.metadata
    __init__.py:819 in read_text
    pathlib.py:1028 in read_text

``sentry_sdk/integrations/mcp.py``'s line 36 is
``MCP_PACKAGE_VERSION = package_version("mcp")`` — a MODULE-LEVEL call that
builds Sentry's whole installed-distribution index by reading every
``*.dist-info/METADATA`` on the image. It runs during ``sentry_sdk.init()``
because ``auto_enabling_integrations`` defaults to True, and ``_lifespan``
called that init SYNCHRONOUSLY on the loop.

What is pinned here is the OUTPUT, not a doc pin: **while the startup init runs,
the loop keeps running.** That is exactly the property a caller needs — a request
arriving during startup gets an answer inside its bound instead of ``HTTP 000``.
The SDK init is stubbed to sleep, so this module does not depend on how many
distributions the runner's image happens to carry; the real cost is measured in
the tracker and recorded in ``tortoise/sentry.py``'s own docstrings.

⛔ THE ON-LOOP CONTROL IS PART OF THE TEST, not commentary. A ticker test that
passes for every implementation measures nothing: the control below drives the
SAME stub through ``init()`` on the loop and asserts the loop is starved, so the
two arms differ only in WHERE the call runs. Its second job is to keep
``init()``'s own contract honest — it is synchronous and slow, and nothing on the
event loop may call it (#13665).
"""

from __future__ import annotations

import asyncio
import time

import pytest

import tortoise.sentry as tsentry

#: Long enough that an ON-LOOP sleep covers many ticker wake-ups (the ticker
#: waits 5 ms per iteration, so a starved loop shows 0-1 ticks and a served loop
#: ~60). Small enough that the whole module stays around a second.
STUB_SECONDS = 0.30


@pytest.fixture(autouse=True)
def _restore_sentry_state():
    """``_enabled`` is module-global; a leaked True would skip every later init."""
    before = tsentry._enabled
    tsentry._enabled = False
    try:
        yield
    finally:
        tsentry._enabled = before


@pytest.fixture()
def slow_init(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Replace ``sentry_sdk.init`` with a slow stub; return its call log.

    The log also counts SUBMISSIONS to the executor (``submits``), by wrapping
    ``asyncio.to_thread`` with a delegating counter. That counter is what makes
    the "does this arm pay a thread hand-off?" claims OBSERVABLE rather than
    asserted in prose — #13665 review round 1, findings 3 and 4.
    """
    calls: dict = {"n": 0, "submits": 0}

    def _slow_init(*args, **kwargs):
        calls["n"] += 1
        time.sleep(STUB_SECONDS)

    real_to_thread = asyncio.to_thread

    def _counting_to_thread(fn, /, *args, **kwargs):
        calls["submits"] += 1
        return real_to_thread(fn, *args, **kwargs)

    import sentry_sdk

    monkeypatch.setattr(sentry_sdk, "init", _slow_init)
    monkeypatch.setattr(asyncio, "to_thread", _counting_to_thread)
    monkeypatch.setenv("SENTRY_DSN", "https://public@example.invalid/1")
    return calls


async def _loop_wake_ups_during(awaitable_factory) -> int:
    """Count loop wake-ups while ``awaitable_factory()`` is awaited.

    The ticker is a plain ``asyncio.sleep`` loop: every iteration requires the
    loop to run. A call that blocks the loop yields 0 wake-ups; a call that runs
    off the loop yields roughly ``STUB_SECONDS / 0.005``. That difference IS the
    assertion.
    """
    ticks = 0

    async def _tick() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.005)
            ticks += 1

    task = asyncio.create_task(_tick())
    try:
        # Let the ticker reach a steady state before sampling, so the count
        # measures the awaited call and not the task-creation ramp.
        await asyncio.sleep(0.03)
        before = ticks
        await awaitable_factory()
        return ticks - before
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def test_init_off_loop_keeps_the_event_loop_serving(slow_init: dict) -> None:
    """THE FIX: a slow startup init does not stop the loop.

    A caller arriving during startup keeps its timer callbacks and its ``send``,
    so it ends in an answer or a legible refusal — never in ``HTTP 000``.
    """
    ticks = asyncio.run(_loop_wake_ups_during(tsentry.init_off_loop))

    assert slow_init["n"] == 1, "init_off_loop must still RUN the SDK init exactly once"
    assert tsentry.enabled() is True
    assert ticks >= 20, (
        "the event loop was starved while Sentry initialised "
        f"({ticks} wake-ups during a {STUB_SECONDS}s init) — startup work is "
        "back ON the loop, which is the #13665 HTTP-000 shape"
    )


async def _call_init_on_the_loop() -> None:
    """The DISCRIMINATING control: ``init()`` awaited from a coroutine."""
    tsentry.init()


def test_the_on_loop_control_is_starved(slow_init: dict) -> None:
    """⛔ THE CONTROL — proves the ticker measures what it claims to.

    ``init()`` is synchronous, so called from a coroutine it holds the loop for
    its whole duration. This arm exists so the assertion above cannot pass
    vacuously: if a refactor ever made ``init()`` non-blocking, THIS test goes
    RED instead of the suite quietly losing its discriminating power.
    """
    ticks = asyncio.run(_loop_wake_ups_during(_call_init_on_the_loop))

    assert slow_init["n"] == 1
    assert ticks < 5, (
        "init() no longer blocks the loop — the discriminator is gone, so "
        f"re-derive this test rather than deleting it ({ticks} wake-ups)"
    )


def test_init_off_loop_is_idempotent_and_skips_the_hand_off(slow_init: dict) -> None:
    """The async entry point's OWN fast path is load-bearing, and pinned here.

    ⛔ THE FIRST VERSION OF THIS ARM WAS VACUOUS (#13665 review round 1, finding
    4): it only asserted ``slow_init["n"] == 1`` across two calls, and that
    number is delivered entirely by ``init()``'s PRE-EXISTING ``if _enabled:``
    guard — so deleting ``init_off_loop``'s own guard left it green. Asserting
    the SUBMISSION COUNT is what makes the new line reachable: the second call
    must return without a second executor hand-off.

    (The earlier docstring also credited the guard to "the lifespan's retry".
    There is no retry — ``_lifespan`` is one ``try``/``await``. The real subject
    is the fast path itself.)
    """

    async def _twice() -> tuple[bool, bool]:
        return await tsentry.init_off_loop(), await tsentry.init_off_loop()

    assert asyncio.run(_twice()) == (True, True)
    assert slow_init["n"] == 1, "the second call re-ran the SDK init"
    assert slow_init["submits"] == 1, (
        "the second call costs a second executor hand-off — delete "
        "`init_off_loop`'s own `if _enabled:` fast path and this is the arm "
        "that reds"
    )


def test_no_dsn_short_circuits_without_a_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No DSN → no SDK init AND no executor submission.

    ⛔ THE FIRST VERSION OF THIS ARM ASSERTED A PROPERTY THE CODE VIOLATED
    (#13665 review round 1, finding 3). It claimed "no worker thread" while
    ``init_off_loop`` submitted UNCONDITIONALLY and only discovered the missing
    DSN *inside* ``init`` — on the worker. The assertion could not see a thread,
    so it passed while its own name was false. The DSN gate now runs BEFORE the
    hand-off, and ``submits`` is what pins that: it would catch the gate sliding
    back inside ``init``.

    This is the arm that keeps local SDK use, self-host and the test suite off
    the slow path — by not paying for a thread they do not need.
    """
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    calls: dict = {"n": 0, "submits": 0}

    def _slow_init(*args, **kwargs):
        calls["n"] += 1

    real_to_thread = asyncio.to_thread

    def _counting_to_thread(fn, /, *args, **kwargs):
        calls["submits"] += 1
        return real_to_thread(fn, *args, **kwargs)

    import sentry_sdk

    monkeypatch.setattr(sentry_sdk, "init", _slow_init)
    monkeypatch.setattr(asyncio, "to_thread", _counting_to_thread)

    assert asyncio.run(tsentry.init_off_loop()) is False
    assert calls["n"] == 0, "the SDK init ran without a DSN"
    assert calls["submits"] == 0, (
        "discovering 'there is no DSN' cost a thread hand-off — the gate slid "
        "back inside init(), i.e. onto the worker"
    )
    assert tsentry.enabled() is False


def test_the_lifespan_uses_the_off_loop_entry_point(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wiring, asserted by WHICH INIT RAN — not by reading the source.

    ⛔ NOT a source grep (a source grep is a doc pin, #13439). The hosted
    lifespan is driven for real through ``TestClient`` entry with BOTH entry
    points replaced by instant recorders, so the arm costs nothing beyond a
    normal lifespan and reports exactly one fact: which function the startup
    half called.
    """
    recorded: dict = {"init": 0, "init_off_loop": 0}

    def _sync(*_a, **_k):
        recorded["init"] += 1
        return True

    async def _off_loop(*_a, **_k):
        recorded["init_off_loop"] += 1
        return True

    monkeypatch.setenv("SENTRY_DSN", "https://public@example.invalid/1")
    monkeypatch.setattr(tsentry, "init", _sync)
    monkeypatch.setattr(tsentry, "init_off_loop", _off_loop)
    tsentry._enabled = False

    from starlette.testclient import TestClient

    import tortoise.hosted_api as ha

    with TestClient(ha.app):
        pass

    assert recorded["init_off_loop"] >= 1, (
        "the hosted lifespan did not await the off-loop Sentry init — startup "
        "is back on the event loop (#13665)"
    )
    assert recorded["init"] == 0, (
        "the hosted lifespan called the SYNCHRONOUS init on the loop — that is "
        "the block that produces #13665's HTTP 000"
    )
