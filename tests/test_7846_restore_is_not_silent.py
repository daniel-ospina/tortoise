"""#7846 criterion 5 — a restore that blocks the instance must not be SILENT.

Hermetic (no server): the whole point is that the driver's verdict is measurable from
a witness connection, so a fake instance with a switchable "answering" state exercises
every branch deterministically.

WHAT THESE TESTS PIN, and the pre-fix behaviour each one fails on. Before
``tortoise/graph_key_restore.py`` the regression test issued ``RESTORE`` on a bare
client bounded only by its socket timeout (``tests/test_indexed_graph_key_restore_7846.py``
``_dump_delete_restore``), so:

* a restore that overran the bound surfaced as ``redis.exceptions.TimeoutError`` — an
  error that names the socket, not the instance, and cannot be told apart from a dead
  server or from load (tests 2 and 3);
* nothing measured the silence WHILE the restore ran, so "the rebuild blocked the
  instance for 2.5 s" was an inference from the restore's own duration and an
  unattributable silence outlasting the restore could not be seen at all (tests 1, 5);
* the caller had no bound it controlled: the only limit was the client's socket
  timeout, so "the restore is slow" and "the caller gave up" were one event (test 4).

Each test's docstring names the specific pre-fix reading that would fail it.
"""

from __future__ import annotations

import socket
import threading
import time

import pytest

from tortoise.graph_key_restore import (
    ALIVE_BUT_SILENT,
    BLOCKED,
    CALLER_STALL,
    INSTANCE_GONE,
    SERVED,
    GraphRestoreLostError,
    GraphRestoreSilentError,
    GraphRestoreStalledError,
    RestoreObservation,
    restore_graph_key,
    tcp_is_listening,
)

#: Fast enough to sample several times inside a 0.3 s restore, slow enough not to
#: spin. Every test below keeps its windows well above this so the samples are real.
_POLL_S = 0.01


class _FakeInstance:
    """An instance reachable on a separate witness connection.

    ``blocked`` is the switch: while it is set, the witness's PING goes unanswered
    (a blocked FalkorDB completes no command at all), which is exactly the state the
    driver has to measure.
    """

    def __init__(self) -> None:
        self.blocked = threading.Event()
        self.pings = 0

    def ping(self) -> None:
        if self.blocked.is_set():
            raise TimeoutError("witness PING went unanswered")
        self.pings += 1


def _call(*, graph="i7846"):
    return {
        "graph": graph,
        "dump": lambda: b"x" * 32,
        "delete": lambda: None,
    }


def _observe(instance: _FakeInstance, restore, **kwargs) -> RestoreObservation:
    return restore_graph_key(
        **_call(),
        restore=restore,
        ping=instance.ping,
        poll_interval_s=_POLL_S,
        **kwargs,
    )


def test_an_answering_instance_is_reported_served_not_blocked() -> None:
    """Pre-fix: there was no status at all — ``_dump_delete_restore`` returned a bare
    ``(payload, seconds)`` pair, so "the instance kept answering" was never asserted."""
    instance = _FakeInstance()
    observation = _observe(instance, lambda _payload: "OK", bound_s=5.0)

    assert observation.status == SERVED
    assert observation.witness_failures == 0
    assert observation.witness_samples > 0, "the witness never sampled — nothing was measured"
    assert observation.silent is False
    assert observation.silent_ms == 0.0
    assert observation.blocked_ms < observation.blocked_latency_ms


def test_the_silence_during_a_blocking_restore_is_measured_and_ends_with_it() -> None:
    """Pre-fix: the block was inferred from the restore's duration, never measured, and
    "the silence ENDS when the restore does" could not be expressed.

    This is the attributable form of criterion 2: the instance was off the air for the
    rebuild, not for an unrelated reason, because the silence stops at the restore.
    """

    instance = _FakeInstance()

    def restore(_payload):
        instance.blocked.set()
        time.sleep(0.3)
        instance.blocked.clear()
        return "OK"

    observation = _observe(instance, restore, bound_s=5.0)

    assert observation.status == BLOCKED
    assert observation.witness_failures > 0, "a blocked witness PING was counted as answered"
    assert observation.silent_ms >= 200, (
        f"the 300 ms block was measured as {observation.silent_ms:.0f} ms"
    )
    assert observation.silence_started_s is not None
    assert observation.silence_ended_s is not None, (
        "the silence never ended in the measurement, yet the restore returned — an "
        "unattributable silence is exactly what criterion 5 must not report as the block"
    )
    assert observation.silence_ended_s >= observation.silence_started_s


def test_a_restore_that_overruns_fails_loudly_and_names_the_block() -> None:
    """Pre-fix: this was a ``TimeoutError`` from the socket, after the CLIENT's timeout.

    The error must name the blocked servers, state that the process is still alive, and
    say why that is not load — the three facts that make the failure actionable.
    """
    instance = _FakeInstance()
    instance.blocked.set()

    def never_returns(_payload):
        time.sleep(5.0)
        return "OK"

    with pytest.raises(GraphRestoreSilentError) as raised:
        _observe(instance, never_returns, bound_s=0.2, listening=lambda: True)

    message = str(raised.value)
    assert "i7846" in message, "the message does not name the graph"
    assert "0.2s caller-side bound" in message, (
        "the message does not state the bound the caller gave up at"
    )
    assert "ACCEPTS a TCP connection" in message, (
        "the message does not carry the liveness evidence that separates blocked from dead"
    )
    assert "not a crash and not load" in message, (
        "the message does not distinguish the block from load — criterion 5"
    )
    assert "Do not retry" in message, "the message gives no recovery instruction"

    observation = raised.value.observation
    assert observation.status == ALIVE_BUT_SILENT
    assert observation.witness_failures > 0
    assert observation.instance_listening is True
    assert observation.error == "restore did not return within the bound"


def test_a_dead_instance_is_a_DIFFERENT_error_from_a_blocked_one() -> None:
    """Pre-fix: indistinguishable — both were the same socket timeout.

    A blocked instance needs "wait and re-probe"; a dead one needs a restart. The two
    must not share an error type or a message.
    """
    instance = _FakeInstance()
    instance.blocked.set()

    def never_returns(_payload):
        time.sleep(5.0)
        return "OK"

    with pytest.raises(GraphRestoreLostError) as lost:
        _observe(instance, never_returns, bound_s=0.2, listening=lambda: False)

    with pytest.raises(GraphRestoreSilentError) as silent:
        _observe(instance, never_returns, bound_s=0.3, listening=lambda: True)

    assert not isinstance(lost.value, GraphRestoreSilentError), (
        "a dead instance was reported as merely blocked"
    )
    assert lost.value.observation.status == INSTANCE_GONE
    assert "died during the restore" in str(lost.value)
    assert "#8270" in str(lost.value)
    assert "died" not in str(silent.value), (
        "the blocked-instance message claims a death it did not observe"
    )
    assert str(lost.value) != str(silent.value)


def test_the_bound_is_the_CALLER_side_and_a_blocked_restore_cannot_exceed_it() -> None:
    """Pre-fix: the only bound was the client's socket timeout, so the caller could not
    give up earlier than the socket did ("slow" and "gave up" were one event)."""
    instance = _FakeInstance()
    instance.blocked.set()

    def very_slow(_payload):
        time.sleep(3.0)
        return "OK"

    started = time.perf_counter()
    with pytest.raises(GraphRestoreSilentError):
        _observe(instance, very_slow, bound_s=0.1, listening=lambda: True, witness_join_s=1.0)
    elapsed = time.perf_counter() - started

    assert elapsed < 1.0, (
        f"the caller was held for {elapsed:.1f}s on a 0.1s bound — the abandoned restore "
        f"worker was joined instead of abandoned"
    )


def test_a_restore_that_raises_keeps_its_own_error_and_gains_the_measurement() -> None:
    """Pre-fix: an error reply was already loud, but carried no observation.

    The driver must not swallow or replace the engine's error — only annotate it.
    """

    class _EngineSaysNo(RuntimeError):
        pass

    instance = _FakeInstance()

    def refuses(_payload):
        instance.blocked.set()
        time.sleep(0.05)
        instance.blocked.clear()
        raise _EngineSaysNo("BUSYKEY destination key already exists")

    with pytest.raises(_EngineSaysNo) as raised:
        _observe(instance, refuses, bound_s=5.0)

    assert "BUSYKEY" in str(raised.value), "the engine's own message was replaced"
    notes = getattr(raised.value, "__notes__", [])
    assert any("status=failed" in note for note in notes), (
        f"the observation was not attached to the engine's error: {notes!r}"
    )


def test_a_block_shorter_than_the_witness_timeout_is_still_reported() -> None:
    """The 3 000-node case: the restore blocks the instance for 0.3 s and the witness's
    own socket timeout is 1 s, so NOTHING fails to answer — the block appears only as a
    slow PING that was queued behind the rebuild.

    Pre-fix: this is the vacuous-green case, and worse than no measurement — the
    instance was off the air for the whole restore, yet a failures-only reading calls it
    ``served``, which is exactly the false green the issue's own lane hit when its vector
    probe returned no rows either way.
    """

    instance = _FakeInstance()

    def restore(_payload):
        instance.blocked.set()
        time.sleep(0.3)
        instance.blocked.clear()
        return "OK"

    def slow_ping() -> None:
        # Models the engine's serving thread: the command is answered, but only when the
        # block that precedes it ends.
        if instance.blocked.is_set():
            time.sleep(0.3)
        instance.pings += 1

    observation = restore_graph_key(
        **_call(),
        restore=restore,
        ping=slow_ping,
        bound_s=5.0,
        poll_interval_s=_POLL_S,
    )

    assert observation.status == BLOCKED, (
        "a 300 ms block went unreported because every PING eventually answered — the "
        "block must be visible through latency, not only through timeouts"
    )
    assert observation.witness_failures == 0
    assert observation.silent_ms == 0.0
    assert observation.witness_max_latency_ms >= 200, (
        f"the slow PING's latency was not recorded ({observation.witness_max_latency_ms:.0f} ms)"
    )
    assert observation.blocked_ms >= 200
    assert observation.silent is False


def test_a_stalled_restore_on_a_SERVING_instance_does_not_blame_the_engine() -> None:
    """Pre-fix: a stalled caller and a blocked engine were the same ``TimeoutError``.

    Every witness PING answered, which cannot happen while the serving thread is inside a
    rebuild — so the stall is on the caller's side. Reporting "blocked server" here would
    send an operator to restart a healthy instance.
    """
    instance = _FakeInstance()  # never blocked: PINGs always answer

    def never_returns(_payload):
        time.sleep(5.0)
        return "OK"

    with pytest.raises(GraphRestoreStalledError) as raised:
        _observe(instance, never_returns, bound_s=0.2, listening=lambda: True)

    message = str(raised.value)
    assert raised.value.observation.status == CALLER_STALL
    assert raised.value.observation.witness_failures == 0
    assert "ANSWERED" in message
    assert "NOT the #7846 blocked-engine signature" in message
    assert "Check the CALLER" in message
    assert not isinstance(raised.value, GraphRestoreSilentError)
    assert not isinstance(raised.value, GraphRestoreLostError)


def test_tcp_is_listening_measures_the_port_not_the_server() -> None:
    """The discriminator itself: an accepting socket reads True, a closed one False.

    This is what makes "alive but silent" measurable at all, so it is pinned directly
    rather than only through the driver.
    """
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        assert tcp_is_listening("127.0.0.1", port, timeout_s=1.0) is True
    finally:
        server.close()

    assert tcp_is_listening("127.0.0.1", port, timeout_s=0.2) is False, (
        "a closed port read as listening — a dead instance would be misreported as blocked"
    )


def test_a_non_positive_bound_is_refused() -> None:
    """A zero/negative bound would make every restore look like an overrun."""
    instance = _FakeInstance()
    with pytest.raises(ValueError):
        _observe(instance, lambda _payload: "OK", bound_s=0)
