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

import tortoise.graph_key_restore
from tortoise.graph_key_restore import (
    ALIVE_BUT_SILENT,
    BLOCKED,
    CALLER_STALL,
    DEFAULT_BLOCKED_LATENCY_MS,
    FAILED,
    INSTANCE_GONE,
    LIVENESS_UNMEASURED,
    SERVED,
    UNMEASURED,
    GraphRestoreLostError,
    GraphRestoreSilentError,
    GraphRestoreStalledError,
    RestoreObservation,
    _longest_silence,
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
    # #8827 review round 3: a healthy run must publish `error is None`, not `""`.
    # The completed-path `error` is built by a `"; ".join(...)` that once had no
    # `or None`, so `join(())` published an EMPTY STRING on every SERVED/BLOCKED run.
    # A consumer distinguishing "no reason recorded" by identity — `if obs.error is
    # not None`, which is what the `str | None` annotation invites — then took the
    # error branch on a clean restore, and `as_dict()["error"] == ""` was
    # indistinguishable from "an error was recorded with empty text".
    # FAILS IF: the join loses its `or None` on either arm.
    assert observation.error is None, (
        f"a SERVED run recorded an error: {observation.error!r}")
    assert observation.as_dict()["error"] is None


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
    # A LOWER BOUND, not the block itself: the run is clipped at the last sample inside
    # the window, so a starved poll (0.01 s nominal) can lose up to one interval at the
    # tail — measured 199.7 ms for this same 300 ms block on a loaded host. The claim
    # being pinned is that the silence was MEASURED rather than inferred from the
    # restore's duration, so the margin is coarse on purpose.
    assert observation.silent_ms >= 150, (
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
    assert "off the air" in message, (
        "the status paragraph no longer draws the conclusion that the instance was not "
        "serving — removing it from the verdict line must not delete it from the account"
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
    lost_message = str(lost.value)
    assert "died during the restore" in lost_message
    # #8827 review (cycle 12): a REFUSED port proves the death, NOT its cause. The
    # message used to assert the #8270 SIGSEGV as the cause unconditionally, which a
    # refused connect cannot establish — and which the module's own ⛔ rule makes the
    # LEAST likely cause for a caller who followed it. The cause is now named as one
    # conditional candidate, so the measurement and the inference are visibly split.
    assert "proves the DEATH, not its cause" in lost_message, (
        f"the death message asserts a cause the refused port cannot establish: "
        f"{lost_message}"
    )
    assert "#8270" in lost_message and "if the payload" in lost_message, (
        f"the #8270 trigger is no longer named as a CONDITIONAL cause: {lost_message}"
    )
    assert "died" not in str(silent.value), (
        "the blocked-instance message claims a death it did not observe"
    )
    assert str(lost.value) != str(silent.value)


def test_an_overrun_without_a_liveness_probe_makes_NO_liveness_claim() -> None:
    """#8827 review (cycle 12), P2: the DEFAULT configuration published a liveness it
    never measured.

    ``listening`` is optional, and with it omitted an overrun that measured the silence
    was still published as ``ALIVE_BUT_SILENT`` and raised an error whose contract said
    *"the process is still listening (blocked, not dead, not loaded)"* — about a port no
    probe had touched. A process that DIED reads exactly the same from the witness alone,
    so the driver told the operator "do not restart, wait and re-probe" for a dead box.

    Both measured block shapes are pinned (unanswered PINGs, and answered-but-slow), so
    the status cannot be manufactured through the other arm either. Pre-fix this fails on
    the status (``alive-but-silent``) and on every "makes no claim" assertion.
    """
    instance = _FakeInstance()
    instance.blocked.set()

    def never_returns(_payload):
        time.sleep(5.0)
        return "OK"

    with pytest.raises(GraphRestoreSilentError) as raised:
        _observe(instance, never_returns, bound_s=0.2)  # no `listening` at all

    observation = raised.value.observation
    assert observation.witness_failures > 0, "the premise is a measured silence"
    assert observation.measured is True, "the BLOCK itself was measured here"
    assert observation.instance_listening is None, "no probe was supplied"
    assert observation.status == LIVENESS_UNMEASURED, (
        f"a run whose liveness was never probed was published as {observation.status!r}, "
        f"which asserts a live process nothing measured"
    )
    assert observation.as_dict()["status"] == LIVENESS_UNMEASURED

    message = str(raised.value)
    assert "LIVENESS WAS NOT MEASURED" in message, (
        f"the message does not say liveness was not measured: {message}"
    )
    assert "still listening" not in message, (
        f"the message claims a listening process no probe measured: {message}"
    )
    assert "still ACCEPTS" not in message
    assert "not a crash" not in message, (
        f"the message rules out a crash it never looked for: {message}"
    )
    assert "died" not in message, (
        "the message must not swing to a death claim either — nothing was probed"
    )
    # The block IS measured, so the message must still carry it and still say don't retry.
    assert "BLOCK" in message.upper(), f"the measured block vanished: {message}"
    assert "Do not retry" in message
    assert not isinstance(raised.value, GraphRestoreLostError)

    # The other measured-block arm: nothing failed, one ANSWERED PING was slow.
    def slow_ping() -> None:
        time.sleep(0.3)  # queued behind the rebuild, but it does answer
        instance.pings += 1

    with pytest.raises(GraphRestoreSilentError) as slow:
        restore_graph_key(
            **_call(),
            restore=never_returns,
            ping=slow_ping,
            bound_s=0.6,
            witness_join_s=1.0,
            poll_interval_s=_POLL_S,
        )

    slow_observation = slow.value.observation
    assert slow_observation.witness_failures == 0
    assert slow_observation.witness_max_latency_ms >= slow_observation.blocked_latency_ms
    assert slow_observation.status == LIVENESS_UNMEASURED, (
        f"the answered-but-slow block was published as {slow_observation.status!r} with no "
        f"liveness probe"
    )
    assert "LIVENESS WAS NOT MEASURED" in str(slow.value)
    assert "still listening" not in str(slow.value)


def test_the_silent_error_does_not_promise_a_liveness_it_may_not_have() -> None:
    """The class docstring is the error's CONTRACT — it must match the statuses it serves.

    It read *"the restore overran its bound while the instance answered nothing, but the
    process is still listening (blocked, not dead, not loaded)"*, which is false for the
    two statuses that reach it without a probe (``LIVENESS_UNMEASURED``) or without any
    reading (``UNMEASURED``). Pre-fix this fails on both assertions.
    """
    doc = " ".join((GraphRestoreSilentError.__doc__ or "").split())
    assert "the process is still listening" not in doc, (
        f"the error still promises a liveness it may not have measured: {doc!r}"
    )
    assert "LIVENESS_UNMEASURED" in doc, (
        f"the error's contract does not name the status that reaches it without a probe: {doc!r}"
    )


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
    # #8827 review cycle 9: the completed BLOCKED path with no unanswered PING fell
    # through to the "every PING answered and none was slow enough" account — the
    # opposite of the verdict, about the one run whose slow PING is the whole evidence.
    diagnosis = observation.diagnosis()
    assert "none was slow enough" not in diagnosis, (
        f"a latency-measured block is reported as an unblocked instance: {diagnosis}"
    )
    assert f"{observation.witness_max_latency_ms:.0f} ms" in diagnosis, (
        f"the diagnosis does not report the slow PING that IS the block: {diagnosis}"
    )


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
    # #8827 review (cycle 11): the verdict line carried an unconditional tail — "so the
    # instance did not serve normally during it" — that contradicts this very status, whose
    # definition is "the instance WAS serving". An operator (or an alert rule matching
    # "did not serve") reads the first sentence and is sent to the engine, which is the
    # mis-blame the status exists to prevent. Pre-fix this assertion fails.
    assert "did not serve normally" not in message, (
        f"the opening sentence asserts a conclusion the CALLER_STALL verdict contradicts: "
        f"{message}"
    )
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

    # #8827 review: a connect TIMEOUT is "could not measure", never a refusal.
    # `socket.timeout` is an `OSError`, so the two-valued probe reported a blocked
    # instance with a full accept backlog (which DROPS SYNs) as "the port REFUSED …
    # the process is gone … restart the instance" — the exact case it exists for.
    # Exercised by monkeypatching the syscall, not by dialling an address that happens
    # to be unroutable HERE: this file runs on every `core` selection, so its PASS may
    # not depend on the runner's routing table (§`test_a_connect_timeout_is_…`).


def test_a_connect_timeout_is_unmeasurable_not_a_refusal(monkeypatch) -> None:
    """#8827 review: the timeout arm must be pinned where the code decides it.

    The previous assertion dialled ``10.255.255.1:6379`` and expected ``None``, which
    holds only where that private-range address DROPS the SYN. On a runner with 10/8
    routed — the common cluster/VPN case — the far side RSTs (``False``, i.e. "the
    process is gone") or something actually answers (``True``), and this file, which is
    registered on the ``core`` surface, went red on a correct tree.
    """

    def times_out(*_args, **_kwargs):
        # `socket.timeout` IS `TimeoutError` on py3.12, and it is an `OSError` — which is
        # what the probe has to tell apart from an active refusal.
        raise TimeoutError("timed out")

    monkeypatch.setattr(socket, "create_connection", times_out)
    assert tcp_is_listening("192.0.2.1", 6379, timeout_s=0.2) is None, (
        "an unmeasurable connect read as a refusal — a healthy but blocked instance "
        "would be reported as gone, and the operator told to restart it"
    )


def test_only_an_active_refusal_reads_as_the_process_being_gone(monkeypatch) -> None:
    """The other half of the tri-state: RST is the ONLY death signal."""

    def refused(*_args, **_kwargs):
        raise ConnectionRefusedError(61, "Connection refused")

    monkeypatch.setattr(socket, "create_connection", refused)
    assert tcp_is_listening("192.0.2.1", 6379, timeout_s=0.2) is False, (
        "an actively refused connect must stay distinguishable from an unmeasurable "
        "one, or `INSTANCE_GONE` becomes unreachable"
    )


def test_a_zero_sample_overrun_never_claims_the_CALLER_stalled() -> None:
    """F1(a), #8827 review: zero samples + overrun must not manufacture a verdict.

    The first witness PING is still in flight at the bound (its socket timeout outlives
    ``bound_s + witness_join_s``), so no sample is ever recorded. The status was already
    guarded against this; the MESSAGE was not — it read "every one of the 0 witness
    PINGs on a separate connection ANSWERED … Check the CALLER", which is the opposite
    of what was measured. Pre-fix this test fails on the status (ALIVE_BUT_SILENT) and
    on both message assertions.
    """
    instance = _FakeInstance()

    def ping_far_too_slow() -> None:
        time.sleep(1.0)  # longer than bound_s + witness_join_s: no sample is recorded

    def never_returns(_payload):
        time.sleep(1.0)
        return "OK"

    with pytest.raises(GraphRestoreSilentError) as raised:
        restore_graph_key(
            **_call(),
            restore=never_returns,
            ping=ping_far_too_slow,
            bound_s=0.2,
            witness_join_s=0.1,
            poll_interval_s=_POLL_S,
            listening=lambda: True,
        )

    observation = raised.value.observation
    assert observation.witness_samples == 0
    assert observation.measured is False
    assert observation.status == UNMEASURED, (
        f"nothing was measured, yet the run was given the verdict {observation.status!r}"
    )
    message = str(raised.value)
    assert "ANSWERED" not in message, (
        f"the message claims the witness answered when it recorded nothing: {message}"
    )
    assert "Check the CALLER" not in message
    assert "NO samples" in message or "INSTRUMENT FAULT" in message, (
        f"the message does not say that nothing was measured: {message}"
    )
    assert instance.pings == 0


def test_a_refused_port_with_zero_samples_is_still_reported_as_a_death() -> None:
    """F1(b), #8827 review: the death branch must not sit below a caller-stall text.

    ``INSTANCE_GONE`` with an empty sample list used to carry "The instance was serving …
    Check the CALLER" — telling the operator to look at their own client while the
    server process was gone. Pre-fix this fails on both message assertions (the status
    was already right, which is exactly why the test pins the message).
    """

    def ping_far_too_slow() -> None:
        time.sleep(1.0)

    def never_returns(_payload):
        time.sleep(1.0)
        return "OK"

    with pytest.raises(GraphRestoreLostError) as raised:
        restore_graph_key(
            **_call(),
            restore=never_returns,
            ping=ping_far_too_slow,
            bound_s=0.2,
            witness_join_s=0.1,
            poll_interval_s=_POLL_S,
            listening=lambda: False,
        )

    assert raised.value.observation.status == INSTANCE_GONE
    message = str(raised.value)
    assert "died during the restore" in message, (
        f"a refused port must report a death, not a stall: {message}"
    )
    assert "Check the CALLER" not in message, (
        f"the death message sends the operator to the caller: {message}"
    )
    assert "Restart the instance" in message


def test_a_witness_fault_on_the_overrun_path_does_not_assert_the_block() -> None:
    """F1(c), #8827 review: an instrument fault must not be delivered as an engine verdict.

    ``WRONGPASS`` is the witness failing, not the instance staying silent, and
    ``witness_fault`` already recorded that in ``observation.error`` — which nothing
    read. Pre-fix the raised message still said "It is a BLOCKED server, the #7846
    signature … Do not retry", on the exact channel an operator acts on.
    """

    def wrongpass() -> None:
        raise PermissionError("WRONGPASS invalid username-password pair")

    def never_returns(_payload):
        time.sleep(1.0)
        return "OK"

    with pytest.raises(GraphRestoreSilentError) as raised:
        restore_graph_key(
            **_call(),
            restore=never_returns,
            ping=wrongpass,
            bound_s=0.2,
            poll_interval_s=_POLL_S,
            listening=lambda: True,
        )

    observation = raised.value.observation
    assert observation.witness_errors == ("PermissionError",)
    assert observation.witness_fault is True
    assert observation.status == UNMEASURED
    message = str(raised.value)
    assert "It is a BLOCKED server" not in message, (
        f"the instrument's own failure was reported as the engine's block: {message}"
    )
    assert "INSTRUMENT FAULT" in message
    # #8827 review (cycle 11): on a non-transport witness fault every attempt raises, so
    # ``witness_failures == witness_samples`` and the pre-fix verdict line opened by
    # asserting the instance did not serve normally — about a run this same message calls
    # "nothing was measured". Pre-fix this assertion fails.
    assert "did not serve normally" not in message, (
        f"an unmeasured instrument fault still asserts the instance did not serve: {message}"
    )
    assert "PermissionError" in message, "the message does not name the witness failure"


def test_a_witness_fault_on_the_completed_path_is_not_a_green_SERVED() -> None:
    """F2(b), #8827 review: silence the block flag by all means — but not into a green.

    A restore that returned while every witness PING failed on ``WRONGPASS`` returned
    ``SERVED`` (``blocked`` forced ``False``), i.e. a caller switching on ``status`` read
    the vacuous green this driver exists to remove. Pre-fix this fails: status is SERVED.
    """

    def wrongpass() -> None:
        raise PermissionError("WRONGPASS invalid username-password pair")

    def returns_shortly(_payload):
        time.sleep(0.05)  # long enough for the witness to record several samples
        return "OK"

    observation = restore_graph_key(
        **_call(),
        restore=returns_shortly,
        ping=wrongpass,
        bound_s=5.0,
        poll_interval_s=_POLL_S,
    )

    assert observation.witness_samples > 0, "no sample was attributed — wrong window"
    assert observation.witness_fault is True
    assert observation.status == UNMEASURED, (
        f"nothing was measured, yet the run was published as {observation.status!r}"
    )
    assert observation.measured is False
    assert observation.error is not None and "PermissionError" in observation.error
    assert observation.as_dict()["measured"] is False
    assert "It is a BLOCKED server" not in observation.diagnosis()
    # The restore RETURNED (0.05 s of a 5 s bound), so the account must say so: keyed on
    # `status in _OVERRUN_STATUSES` alone this diagnosis opened with "did not return
    # within the 5s caller-side bound" about a call that returned (#8827 review).
    assert "did not return within" not in observation.diagnosis(), (
        f"a COMPLETED unmeasured run is described as having overrun its bound: "
        f"{observation.diagnosis()}"
    )


def test_a_zero_sample_completed_restore_is_not_a_green_SERVED() -> None:
    """F2(a), #8827 review: the completed path had no ``and samples`` guard.

    The witness PING outlives the whole operation, so the window contains nothing; the
    status was ``SERVED`` with ``silent_ms == 0`` — a restore that could have blocked the
    instance for its entire duration read GREEN. Pre-fix this fails: status is SERVED.
    """

    def ping_far_too_slow() -> None:
        time.sleep(1.0)

    observation = restore_graph_key(
        **_call(),
        restore=lambda _payload: "OK",
        ping=ping_far_too_slow,
        bound_s=5.0,
        witness_join_s=0.05,  # shorter than the PING: the sample never lands
        poll_interval_s=_POLL_S,
    )

    assert observation.witness_samples == 0
    assert observation.status == UNMEASURED, (
        f"a restore nothing was measured for was published as {observation.status!r}"
    )
    assert observation.measured is False
    assert observation.error is not None and "NO samples" in observation.error
    assert observation.restore_returned is True
    assert "did not return within" not in observation.diagnosis(), (
        f"the restore returned immediately, yet the account claims it overran the "
        f"bound: {observation.diagnosis()}"
    )


def test_the_three_silence_fields_describe_ONE_run() -> None:
    """F3, #8827 review: one run, three numbers — never a second independent maximum.

    The in-window silence (0.10 s → 0.15 s, 50 ms) is followed, AFTER the window closed,
    by a much longer one (1.00 s → 2.00 s). Taking the end edge from a second maximum
    over the full list published ``silent_ms=50``, ``started=0.10``, ``ended=2.00``: a
    triple that describes no run at all, and that the live test asserts on.
    """
    samples = [
        (0.00, True, 1.0),
        (0.10, False, 50.0),
        (0.15, True, 1.0),  # the instance came back: the in-window run ended HERE
        (1.00, False, 900.0),  # post-window, much longer, and irrelevant to attribution
        (2.00, True, 1.0),
    ]
    window = samples[:3]

    silent_ms, started, ended = _longest_silence(window, samples)

    assert (started, ended) == (0.10, 0.15), (
        f"the end edge ({ended}) was taken from a different run than the start ({started})"
    )
    assert silent_ms == pytest.approx(50.0, abs=1.0)
    assert ended is not None and started is not None
    assert (ended - started) * 1000 == pytest.approx(silent_ms, abs=1.0), (
        "the three fields do not describe one silence"
    )

    # A run that only recovers AFTER the window keeps its own end edge — that recovery
    # probe is what makes the silence attributable — without letting the magnitude run on
    # to the recovery edge (it would then inflate `blocked_ms` past the operation).
    clipped = [
        (0.00, True, 1.0),
        (0.10, False, 900.0),
        (1.00, False, 900.0),
        (5.00, True, 1.0),  # the instance came back long after the operation ended
    ]
    silent_ms, started, ended = _longest_silence(clipped[:3], clipped)
    assert (started, ended) == (0.10, 5.00), (
        "the recovery edge is post-window and must still be reported as the end"
    )
    assert silent_ms < (ended - started) * 1000, (
        "the magnitude ran to the recovery edge instead of stopping inside the window"
    )
    assert silent_ms == pytest.approx(1800.0, abs=1.0), (
        "the magnitude must be the in-window silence (to the last in-flight attempt), "
        f"not the recovery edge: got {silent_ms:.0f} ms"
    )


def test_the_bound_covers_the_RESTORE_step_and_the_split_is_visible() -> None:
    """F4, #8827 review: `bound_s` bounds the RESTORE call, not ``dump``/``delete``.

    The previous title and docstring item 1 read as a bound on the whole sequence, so a
    caller could pass an unbounded client and be held by a hung ``DUMP`` outside any
    bound at all. The seam is now documented AND reported: a 0.2 s ``dump`` is caller-held
    time (``elapsed_s``) that is not restore time (``restore_s``). Pre-fix this fails on
    both — ``restore_s`` does not exist, and ``elapsed_s`` excluded the dump.
    """
    instance = _FakeInstance()

    def slow_dump() -> bytes:
        time.sleep(0.2)
        return b"x" * 32

    observation = restore_graph_key(
        graph="i7846",
        dump=slow_dump,
        delete=lambda: None,
        restore=lambda _payload: "OK",
        ping=instance.ping,
        bound_s=5.0,
        poll_interval_s=_POLL_S,
    )

    assert observation.restore_s < 0.05, (
        f"the 0.2 s DUMP was counted as restore time ({observation.restore_s:.3f}s)"
    )
    assert observation.elapsed_s >= 0.2, (
        f"the caller held the DUMP for 0.2 s, but elapsed_s says {observation.elapsed_s:.3f}s"
    )
    assert observation.as_dict()["restore_s"] < 0.05
    assert "RESTORE step" in observation.sentence()


def test_the_module_states_the_bound_seam_exactly() -> None:
    """F4's documentation half: the seam must be stated where a caller reads it."""
    module = tortoise.graph_key_restore
    title = (module.__doc__ or "").split("\n\n")[0]
    assert "bounded at the RESTORE step" in title, (
        f"the module title still reads as a bound on the whole sequence: {title!r}"
    )
    driver_doc = restore_graph_key.__doc__ or ""
    assert "NOT ``dump``/``delete``" in driver_doc, (
        "restore_graph_key no longer says that bound_s excludes dump/delete"
    )


def test_a_non_positive_bound_is_refused() -> None:
    """A zero/negative bound would make every restore look like an overrun."""
    instance = _FakeInstance()
    with pytest.raises(ValueError):
        _observe(instance, lambda _payload: "OK", bound_s=0)


def test_an_overrun_with_a_REFUSED_port_is_a_death_even_though_every_PING_answered() -> None:
    """#8827 review cycle 9: hard transport evidence must outrank the witness inference.

    The process dies inside the last poll interval before the bound, so no witness PING
    is in flight and NONE fails — ``failures == 0`` with a non-empty sample list. The
    refusal is the same observation's own ``instance_listening is False``.

    Pre-fix the caller-stall branch came first, so this run raised
    ``GraphRestoreStalledError`` and told the operator "the instance was serving … check
    the CALLER … before suspecting the engine" about a server process that was gone.
    """
    instance = _FakeInstance()  # PINGs answer: the witness records no failure at all

    def never_returns(_payload):
        time.sleep(5.0)
        return "OK"

    with pytest.raises(GraphRestoreLostError) as raised:
        _observe(instance, never_returns, bound_s=0.2, listening=lambda: False)

    observation = raised.value.observation
    assert observation.witness_samples > 0, "no sample was attributed — wrong window"
    assert observation.witness_failures == 0, (
        "the premise is a run where every in-window PING answered"
    )
    assert observation.instance_listening is False
    assert observation.status == INSTANCE_GONE, (
        f"a REFUSED port was reported as {observation.status!r}, so the operator is told "
        f"to check their own client while the process is gone"
    )
    message = str(raised.value)
    assert "died during the restore" in message
    assert "Check the CALLER" not in message
    assert not isinstance(raised.value, GraphRestoreStalledError), (
        "a refused port was raised as a caller-side stall"
    )


def test_an_overrun_whose_answered_PINGs_were_SLOW_is_not_blamed_on_the_caller() -> None:
    """#8827 review cycle 9: a slow ANSWERED PING is the block, on the overrun path too.

    The caller gives the witness a timeout below ``bound_s`` (this module's own advice),
    so nothing fails to answer while the serving thread sits inside the rebuild — every
    PING merely takes ~300 ms. Nothing fails, yet the instance is visibly not serving.

    Pre-fix the overrun path discarded the latency signal entirely and reported
    ``CALLER_STALL``: "the instance was serving, so this is NOT the #7846 blocked-engine
    signature … Check the CALLER".
    """
    instance = _FakeInstance()

    def slow_ping() -> None:
        # Answered — but only once the work queued ahead of it on the serving thread is
        # done, which is exactly what the rebuild does to the witness.
        time.sleep(0.3)
        instance.pings += 1

    def never_returns(_payload):
        time.sleep(5.0)
        return "OK"

    with pytest.raises(GraphRestoreSilentError) as raised:
        restore_graph_key(
            **_call(),
            restore=never_returns,
            ping=slow_ping,
            bound_s=0.2,
            poll_interval_s=_POLL_S,
            witness_join_s=5.0,
            listening=lambda: True,
        )

    observation = raised.value.observation
    assert observation.witness_failures == 0, "the slow PING answered — nothing failed"
    assert observation.witness_max_latency_ms >= observation.blocked_latency_ms, (
        f"the answered-but-slow PING was not recorded ({observation.witness_max_latency_ms:.0f} ms)"
    )
    assert observation.blocked_ms >= observation.blocked_latency_ms
    assert observation.status == ALIVE_BUT_SILENT, (
        f"the block was measured and then reported as {observation.status!r} — the caller "
        f"is blamed for an instance that was queued behind its own rebuild"
    )
    message = str(raised.value)
    assert "Check the CALLER" not in message
    assert "NOT the #7846 blocked-engine signature" not in message
    assert f"{observation.witness_max_latency_ms:.0f} ms" in message, (
        f"the measurement that decided this verdict is not in the message: {message}"
    )
    assert not isinstance(raised.value, GraphRestoreStalledError)


def test_the_caller_held_time_reports_the_whole_call_including_the_TAIL() -> None:
    """#8827 review cycle 9: ``elapsed_s`` must not stop at the bound.

    On the overrun path the caller is held for ``bound_s`` PLUS the witness join (a
    witness blocked inside its own PING is only released when that PING is) PLUS the
    liveness probe's own timeout. ``elapsed_s`` was captured at the bound, before both,
    so an observation could say "caller held 0.2s" about a call the caller waited ~0.9s
    for — the same measurement-vs-operation drift this driver exists to remove.

    Pre-fix the ``elapsed_s`` assertion below fails (it reads ~0.2s against a ~0.9s
    call); the docstring's promise of "never past its own bound" is corrected alongside
    it, to the additive term asserted here.
    """
    instance = _FakeInstance()

    def ping_that_never_lands() -> None:
        time.sleep(1.5)  # still in flight when the witness is joined and abandoned
        instance.pings += 1  # ... and only then does the sample land, past the window

    def slow_probe() -> bool:
        time.sleep(0.2)  # a real tcp probe carries a timeout; this models its cost
        return True

    bound_s, join_s, probe_s = 0.2, 0.5, 0.2

    def never_returns(_payload):
        time.sleep(5.0)
        return "OK"

    started = time.perf_counter()
    with pytest.raises(GraphRestoreSilentError) as raised:
        restore_graph_key(
            **_call(),
            restore=never_returns,
            ping=ping_that_never_lands,
            bound_s=bound_s,
            witness_join_s=join_s,
            poll_interval_s=_POLL_S,
            listening=slow_probe,
        )
    held = time.perf_counter() - started
    observation = raised.value.observation

    assert held >= bound_s + join_s + probe_s - 0.15, (
        f"the scenario did not exercise the tail: the caller was held only {held:.2f}s"
    )
    # The documented ceiling: the bound, plus the two named tail terms the caller owns.
    assert held <= bound_s + join_s + probe_s + 0.6, (
        f"the caller was held {held:.2f}s, past the bound + witness join + probe timeout "
        f"({bound_s + join_s + probe_s:.2f}s) — the abandoned restore worker was joined "
        f"to completion instead of abandoned"
    )
    assert observation.restore_s >= bound_s * 0.9, (
        f"the RESTORE step is reported as {observation.restore_s:.2f}s on a {bound_s}s "
        f"bound: the overrun vanished from the split"
    )
    assert observation.elapsed_s >= observation.restore_s + join_s * 0.8, (
        f"elapsed_s ({observation.elapsed_s:.2f}s) does not carry the tail the caller "
        f"also waited for (RESTORE step {observation.restore_s:.2f}s + a {join_s}s "
        f"witness join)"
    )
    assert observation.elapsed_s >= held - 0.2, (
        f"the caller waited {held:.2f}s but elapsed_s reports {observation.elapsed_s:.2f}s — "
        f"the published caller-held time excludes the tail the operation needed"
    )
    assert abs(held - observation.elapsed_s) < 0.35, (
        f"elapsed_s ({observation.elapsed_s:.2f}s) and the wall time the caller actually "
        f"waited ({held:.2f}s) do not describe the same call"
    )
    assert observation.elapsed_s >= observation.restore_s


def test_the_account_of_an_UNMEASURED_run_follows_the_MEASUREMENT_not_the_status() -> None:
    """#8827 review: ``UNMEASURED`` is reachable on BOTH paths, so status cannot word it.

    ``_OVERRUN_STATUSES`` contained ``UNMEASURED``, and ``_verdict_line`` keyed "gave up
    vs returned" on it. But ``UNMEASURED`` is also set on the COMPLETED path (a witness
    fault, or zero in-window samples), so a restore that returned in 0.05 s described
    itself as "did not return within the 5s caller-side bound" — the account asserting
    an event that did not happen, on the very channel an operator acts on. Both arms are
    pinned here so the correction cannot overshoot in either direction.

    Pre-fix this FAILS on every assertion: ``restore_returned`` does not exist
    (``AttributeError``), and the completed run's diagnosis contains "did not return
    within".
    """

    def wrongpass() -> None:
        raise PermissionError("WRONGPASS invalid username-password pair")

    completed = restore_graph_key(
        **_call(),
        restore=lambda _payload: "OK",
        ping=wrongpass,
        bound_s=5.0,
        poll_interval_s=_POLL_S,
    )

    assert completed.status == UNMEASURED
    assert completed.restore_returned is True, (
        "the RESTORE call returned, but the observation says the caller gave up"
    )
    assert completed.as_dict()["restore_returned"] is True, (
        "the published data omits the fact the restore returned"
    )
    assert "did not return within" not in completed.diagnosis(), (
        f"a completed run is described as an overrun: {completed.diagnosis()}"
    )
    assert "returned after" in completed.diagnosis(), (
        f"the completed run's own duration is not in the account: {completed.diagnosis()}"
    )

    def ping_far_too_slow() -> None:
        time.sleep(1.0)

    def never_returns(_payload):
        time.sleep(1.0)
        return "OK"

    with pytest.raises(GraphRestoreSilentError) as raised:
        restore_graph_key(
            **_call(),
            restore=never_returns,
            ping=ping_far_too_slow,
            bound_s=0.2,
            witness_join_s=0.1,
            poll_interval_s=_POLL_S,
            listening=lambda: True,
        )

    overran = raised.value.observation
    assert overran.status == UNMEASURED
    assert overran.restore_returned is False, (
        "the restore never returned, but the observation says it did"
    )
    assert overran.as_dict()["restore_returned"] is False
    assert "did not return within the 0.2s caller-side bound" in overran.diagnosis(), (
        f"an overrun no longer says the caller gave up: {overran.diagnosis()}"
    )


def _doc_sentences(text: str) -> list[str]:
    """The doc text as sentences, newlines folded away (prose wraps mid-sentence)."""
    return [" ".join(s.split()) for s in text.replace("\n", " ").split(".") if s.strip()]


def test_the_module_states_the_UNMEASURED_split_and_the_WHOLE_caller_held_ceiling() -> None:
    """#8827 review: the module prose was the wrong half of two documented facts.

    ``UNMEASURED`` was documented as *raising* ``GraphRestoreSilentError`` unconditionally,
    while on the COMPLETED path the driver logs and RETURNS — a caller pattern-matching on
    the prose ("an unmeasured run raises, so no exception means it was measured") reads an
    unmeasured run as success, which is the vacuous green this module exists to remove.
    And the caller-held ceiling omitted ``recovery_probe_s`` (1 s by default), which the
    completed path spends waiting for the recovery edge. Both are prose-only, so they are
    pinned here rather than by a behavioural test — behaviour was already correct.

    #8827 review (cycle 12) added the SECOND half of the ceiling: it also omitted the
    inline ``DUMP``/``DEL``, which run on the caller's thread OUTSIDE every bound, so
    "``elapsed_s`` reports THAT number" was false by exactly the ``dump()``/``delete()``
    time — the same paragraph says those two are bounded only by the caller's own client
    timeout. The ceiling is now stated for the tail AFTER ``DUMP``/``DEL``, and
    ``elapsed_s`` is stated as that tail PLUS the caller's own dump/delete time.

    Pre-fix this FAILS every arm: the ceiling sentence reads
    ``bound_s + witness_join_s + probe_timeout`` (no ``recovery_probe_s``), no sentence
    places ``UNMEASURED`` on both paths, the ceiling sentence never mentions
    ``DUMP``/``DEL``, and no ``elapsed_s`` sentence says that it carries the dump/delete
    time. The cycle-12 arms fail on ``b84f66dec`` too.

    The ``UNMEASURED`` arm is deliberately measured as ONE span naming BOTH paths: the
    prose wraps across lines and the status table has no full stops, so a looser test
    ("some sentence mentioning UNMEASURED also says *return*") passes on the pre-fix
    text — the ``SERVED`` row's "restore returned" lands in the same span. That is a
    vacuous guard, so the test asks for the split itself.
    """
    for where, doc in (
        ("module docstring", tortoise.graph_key_restore.__doc__ or ""),
        ("restore_graph_key docstring", restore_graph_key.__doc__ or ""),
    ):
        sentences = _doc_sentences(doc)
        ceiling = [s for s in sentences if "at most" in s]
        assert ceiling, f"the {where} no longer states a caller-held ceiling"
        tailed = [
            s
            for s in ceiling
            if all(term in s for term in ("bound_s", "recovery_probe_s", "witness_join_s"))
        ]
        assert tailed, (
            f"the {where}'s ceiling omits ``recovery_probe_s``, which the completed path "
            f"spends before publishing: {ceiling}"
        )
        assert any("DUMP" in s for s in tailed), (
            f"the {where}'s ceiling does not say it starts AFTER ``DUMP``/``DEL``, which "
            f"run outside every bound — so it reads as a bound on the whole call: {tailed}"
        )
        elapsed = [s for s in sentences if "elapsed_s" in s]
        assert any("dump" in s.lower() and "tail" in s.lower() for s in elapsed), (
            f"the {where} never says ``elapsed_s`` is the bounded tail PLUS the caller's "
            f"own ``dump()``/``delete()`` time: {elapsed}"
        )

        if where == "module docstring":
            unmeasured = [s for s in sentences if "UNMEASURED" in s]
            split = [
                s
                for s in unmeasured
                if "completed" in s.lower() and "overrun" in s.lower()
            ]
            assert split, (
                "the module does not say that ``UNMEASURED`` behaves differently on the "
                f"COMPLETED and OVERRUN paths (it is reachable on both): {unmeasured}"
            )
            assert any("return" in s.lower() for s in split), (
                "the module never says a COMPLETED unmeasured run RETURNS — a caller "
                f"reading it will expect an exception it never sees: {split}"
            )
            assert any("raise" in s.lower() for s in split), (
                f"the module never says an OVERRUN unmeasured run RAISES: {split}"
            )


def test_a_MIXED_witness_fault_is_not_published_as_a_trustworthy_block() -> None:
    """F1, #8827 review: ``witness_fault`` asked the wrong question about a MIXED set.

    ``witness_fault`` read ``bool(errors) and not (set(errors) & _WITNESS_TRANSPORT_ERRORS)``
    — "is this set DISJOINT from transport" — so a run that saw a ``TimeoutError`` from a
    blocked engine *and* an ``AuthenticationError`` from the witness answered "pure
    transport". The auth failure was then dropped from the verdict entirely: on the
    completed path the run was published as a trustworthy ``BLOCKED``, and on the overrun
    path it raised "It is a BLOCKED server … Do not retry" while naming no auth error at
    all. The polarity is now "ANY non-transport failure disqualifies".

    Pre-fix this fails on every arm: ``witness_fault`` is ``False``, the statuses are
    ``BLOCKED`` / ``ALIVE_BUT_SILENT``, and the auth error appears in no message.
    """
    from redis.exceptions import AuthenticationError

    def mixed_ping() -> None:
        # Both at once, which is the realistic shape: the instance is blocked (so the
        # PING times out) AND the caller's credential expired on the witness connection.
        if int(time.perf_counter() * 1000) % 2:
            raise TimeoutError("witness PING went unanswered")
        raise AuthenticationError("WRONGPASS invalid username-password pair")

    # (a) COMPLETED path: the restore returned, so there is nothing to hang on — the run
    # must be an instrument report, not a block verdict.
    completed = restore_graph_key(
        **_call(),
        restore=lambda _payload: "OK",
        ping=mixed_ping,
        bound_s=5.0,
        poll_interval_s=_POLL_S,
    )
    assert "AuthenticationError" in completed.witness_errors, "the premise was not built"
    assert "TimeoutError" in completed.witness_errors, "the premise was not built"
    assert completed.witness_fault is True, (
        "a mixed witness fault read as pure transport — the auth failure was dropped from "
        f"the verdict: {completed.witness_errors}"
    )
    assert completed.status == UNMEASURED, (
        f"a run whose witness also failed on auth was published as {completed.status!r}"
    )
    assert "AuthenticationError" in completed.diagnosis(), (
        f"the account does not surface the auth failure: {completed.diagnosis()}"
    )
    assert "served through the restore" not in completed.diagnosis()

    # (b) OVERRUN path: pre-fix this raised "It is a BLOCKED server … Do not retry", which
    # is the instruction an operator acts on.
    def never_returns(_payload):
        time.sleep(1.0)
        return "OK"

    with pytest.raises(GraphRestoreSilentError) as raised:
        restore_graph_key(
            **_call(),
            restore=never_returns,
            ping=mixed_ping,
            bound_s=0.2,
            witness_join_s=0.2,
            poll_interval_s=_POLL_S,
            listening=lambda: True,
        )
    overran = raised.value.observation
    assert overran.witness_fault is True
    assert overran.status == UNMEASURED
    assert overran.measured is False
    message = str(raised.value)
    assert "It is a BLOCKED server" not in message, (
        f"the engine was blamed for the witness's own auth failure: {message}"
    )
    assert "INSTRUMENT FAULT" in message
    assert "AuthenticationError" in message


def test_a_liveness_probe_that_RAISES_is_a_typed_error_not_the_raw_exception() -> None:
    """F2, #8827 review: the caller-supplied probe was called UNGUARDED.

    ``listening=lambda: client.ping() is True`` is the natural way to wire a command-level
    probe, and it raises exactly when the block it is meant to measure is happening. The
    raw exception escaped the driver AFTER ``delete()`` had removed the graph key and
    after the restore worker had been abandoned: the caller got their own ``RuntimeError``
    instead of the observation, and nothing was published or logged.

    A probe that cannot answer is not a refusal — it is "could not measure", which is
    ``instance_listening is None`` / ``LIVENESS_UNMEASURED``, never ``INSTANCE_GONE``.
    Pre-fix this fails: the ``RuntimeError`` propagates out of ``restore_graph_key``.
    """
    instance = _FakeInstance()
    instance.blocked.set()  # the block IS measured; the liveness reading is what fails

    def never_returns(_payload):
        time.sleep(1.0)
        return "OK"

    def probe_that_cannot_answer() -> bool:
        raise RuntimeError("ping() blew up in the caller's probe")

    with pytest.raises((GraphRestoreSilentError, GraphRestoreLostError)) as raised:
        _observe(
            instance,
            never_returns,
            bound_s=0.2,
            witness_join_s=0.2,
            listening=probe_that_cannot_answer,
        )

    observation = raised.value.observation
    assert observation.instance_listening is None, (
        "a probe that raised was read as a REFUSAL, which selects INSTANCE_GONE and tells "
        "the operator to restart a healthy box"
    )
    assert observation.status == LIVENESS_UNMEASURED
    assert observation.liveness_error is not None
    assert "RuntimeError" in observation.liveness_error
    assert observation.error is not None and "probe FAILED" in observation.error, (
        f"the observation does not say why liveness is missing: {observation.error!r}"
    )
    assert observation.as_dict()["liveness_error"] == observation.liveness_error
    message = str(raised.value)
    assert "the liveness probe FAILED" in message, (
        f"the account does not say the probe could not answer: {message}"
    )
    assert "no transport probe supplied an answer" not in message, (
        f"the account claims no probe was supplied about the probe the caller watched "
        f"fail: {message}"
    )


def test_a_FAILED_restore_says_the_graph_key_was_already_DELETED() -> None:
    """F3, #8827 review: ``FAILED`` had no branch, no table row and no contract entry.

    It is the one status whose consequence is a LOST GRAPH — the sequence is
    ``dump()`` → ``delete()`` → ``restore()``, so when the RESTORE raises the key is
    already gone and the payload exists only in the caller's hands. With no branch it fell
    through to the served/unmeasured text, which on a run whose witness saw nothing wrong
    read "…so the instance served through the restore": the exact opposite of what
    happened, on the one channel an operator acts on.

    Pre-fix this fails: ``FAILED`` is not in the module's state table, the account says
    the instance served, and the note attached to the caller's error carries no account
    at all.
    """

    class _EngineSaysNo(RuntimeError):
        pass

    instance = _FakeInstance()

    def refuses(_payload):
        raise _EngineSaysNo("BUSYKEY destination key already exists")

    with pytest.raises(_EngineSaysNo) as raised:
        _observe(instance, refuses, bound_s=5.0)

    notes = getattr(raised.value, "__notes__", [])
    assert any("status=failed" in note for note in notes), (
        f"the observation was not attached to the engine's error: {notes!r}"
    )
    account = "\n".join(notes)
    assert "ALREADY DELETED" in account, (
        f"the FAILED account does not say the graph key was already deleted: {account!r}"
    )
    assert "only copy" in account, (
        f"the FAILED account does not say the payload is the only copy left: {account!r}"
    )
    assert "re-issue the restore" in account.lower(), (
        f"the FAILED account gives the operator no recovery: {account!r}"
    )
    assert "served through the restore" not in account, (
        f"the run that lost the graph is described as an instance that served: {account!r}"
    )

    # The account is reachable in its own right, for a caller that logs the observation.
    observation = RestoreObservation(
        graph="i7846",
        status=FAILED,
        bound_s=5.0,
        elapsed_s=0.01,
        restore_s=0.01,
        payload_bytes=32,
        witness_samples=3,
        witness_failures=0,
        witness_errors=(),
        silent_ms=0.0,
        silence_started_s=None,
        silence_ended_s=None,
        witness_max_latency_ms=1.0,
        blocked_latency_ms=DEFAULT_BLOCKED_LATENCY_MS,
        instance_listening=None,
        restore_returned=True,
        error="_EngineSaysNo: BUSYKEY",
    )
    assert observation.status == FAILED
    assert "ALREADY DELETED" in observation.diagnosis()
    assert "served through the restore" not in observation.diagnosis()

    # ... and the contract states it, where a caller reads the statuses.
    table = " ".join((tortoise.graph_key_restore.__doc__ or "").split())
    assert "``FAILED``" in table, "the module's state table still omits FAILED"
    driver_doc = " ".join((restore_graph_key.__doc__ or "").split())
    assert "FAILED" in driver_doc, (
        "restore_graph_key's contract never says a raising RESTORE is published as FAILED"
    )


def test_the_recovery_instruction_names_the_KEY_not_only_a_PING() -> None:
    """F4, #8827 review: the recovery instruction answered the wrong question.

    Both blocked/silent accounts ended with "check with a PING on a separate connection
    before restarting anything". At that moment the graph key has ALREADY been deleted, so
    a PING reply says the server is up — not that the restore completed. An operator
    following it literally leaves the graph missing. The check that answers the question is
    a key-level one (``GRAPH.LIST`` / an ``EXISTS``-class check), plus the restored node
    count for a completed-but-silent run; a PING is only the liveness half.

    Pre-fix this fails on both assertions: the account names no key check and offers the
    PING as the thing to check.
    """
    instance = _FakeInstance()

    def restore(_payload):
        instance.blocked.set()
        time.sleep(0.3)
        instance.blocked.clear()
        return "OK"

    observation = _observe(instance, restore, bound_s=5.0)
    assert observation.status == BLOCKED
    account = observation.diagnosis()
    assert "GRAPH.LIST" in account and "EXISTS" in account, (
        f"the blocked account never names a key-level check: {account}"
    )
    assert "node count" in account, (
        f"the blocked account never says to confirm the restored node count: {account}"
    )
    assert "check with a PING" not in account, (
        f"the account still offers a PING as the check to perform: {account}"
    )
    # The same wording is carried by the overrun/silent arm; both must stay consistent.
    silent = RestoreObservation(
        graph="i7846",
        status=LIVENESS_UNMEASURED,
        bound_s=5.0,
        elapsed_s=5.0,
        restore_s=5.0,
        payload_bytes=32,
        witness_samples=6,
        witness_failures=4,
        witness_errors=("TimeoutError",),
        silent_ms=200.0,
        silence_started_s=0.1,
        silence_ended_s=None,
        witness_max_latency_ms=900.0,
        blocked_latency_ms=DEFAULT_BLOCKED_LATENCY_MS,
        instance_listening=None,
        restore_returned=False,
    )
    silent_account = silent.diagnosis()
    assert "GRAPH.LIST" in silent_account and "EXISTS" in silent_account, (
        f"the silent/overrun account never names a key-level check: {silent_account}"
    )
    assert "check with a PING on a separate connection before restarting" not in silent_account
