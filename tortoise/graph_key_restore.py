"""A bounded, OBSERVED graph-key ``DUMP`` -> ``DEL`` -> ``RESTORE`` (#7846).

WHY THIS EXISTS. A graph-key ``RESTORE`` of a graph that carries an in-graph VECTOR
INDEX takes the whole FalkorDB instance off the air for the duration of the index
rebuild, which the engine performs on its serving thread. Measured 2026-10-12/13 and
re-measured here (``docs/evidence/7846-indexed-graph-restore/``):

    same graph, 20 000 nodes, one engine:  no index -> restore ~0.08-0.17 s,
    bystander worst PING ~50 ms  |  with index -> restore ~1.3-2.6 s and the
    bystander's worst PING IS the restore duration (2 249-2 497 ms); 90 000 nodes
    exceeds a 10 s client read bound, and the caller sees an instance that answers
    nothing, with no progress, no log line and no error.

The issue was filed as a *permanent* wedge; it is not one — the restore does return
and the index does survive. What is real, and what this module addresses, is the
SILENCE: from the caller's side a long index rebuild, a wedged server and a dead
process are indistinguishable, so a legitimate rebuild is misread as a hang, and a
hang is misread as load.

WHAT THIS MODULE CANNOT FIX, AND DOES NOT CLAIM. The blocking itself is a property of
the engine's restore path: acceptance criteria 2 (*"the instance continues to serve
other graphs throughout the restore"*) and the engine half of 5 remain upstream, as
the evidence doc's criteria table states. Nothing here makes a blocked instance serve.

WHAT IT DOES FIX (criterion 5 at our boundary, and criterion 1's "or fails with an
explicit error the caller can act on"). ``restore_graph_key``:

1. **Bounds the operation.** ``bound_s`` is a hard caller-side read bound. If the
   restore has not returned by then, the caller gets an error instead of a hang; the
   worker is a daemon and is abandoned (never joined to completion), so a blocked
   restore can never block the caller past its own bound.
2. **Watches the instance from a SECOND connection throughout** — a witness thread
   polls ``ping`` while the restore runs — so the silence is *measured* rather than
   inferred from the operation's duration.
3. **Tells the states apart**, which is the whole point of criterion 5:

   ======================  =============================================  =====================
   state                   evidence                                       what it means
   ======================  =============================================  =====================
   ``SERVED``              restore returned, every witness PING answered  load, if anything
   ``BLOCKED``             restore returned with witness evidence: >=1    the instance was off
                           PING unanswered, or an ANSWERED PING took      the air for the
                           >= ``blocked_latency_ms`` (queued behind the   restore only
                           rebuild); the silence / slow PING ENDS when
                           the restore does
   ``ALIVE_BUT_SILENT``    restore overran ``bound_s``; PINGs unanswered  blocked server: not
                           and the port still ACCEPTS a connection        load, not a crash
   ``INSTANCE_GONE``       restore overran ``bound_s``; port REFUSED      the process died
                                                                          (the #8270 class)
   ``CALLER_STALL``        restore overran ``bound_s`` but EVERY PING      the caller is stuck,
                           answered — the serving thread cannot be        not the engine
                           inside a rebuild while that is true
   ======================  =============================================  =====================

   ``BLOCKED`` is not a failure — it is the measured fact criterion 2 records as
   unmet, reported instead of hidden. Two signals make it measurable at any scale: an
   unanswered PING (socket timeout expired) and an ANSWERED PING whose latency is the
   block itself (the engine queues the command behind the rebuild on its serving
   thread). The second is why a 0.27 s block at 3 000 nodes is still reported rather
   than greened by a 1 s witness timeout. The three overrun states raise
   ``GraphRestoreSilentError`` / ``GraphRestoreLostError`` / ``GraphRestoreStalledError``
   respectively, whose messages carry the measurement and the recovery instruction.
   They are deliberately DIFFERENT errors: one restarts nothing and waits, one needs a
   restart, and one is not the engine's fault at all.

THE TRANSPORT PROBE IS THE DISCRIMINATOR, and it is deliberately transport-level. A
server blocked on its serving thread still accepts connections (the kernel completes
the handshake into the backlog) while no command is ever answered; a dead process
refuses. So "connect succeeds, commands do not answer" is measured evidence of
*blocked*, not of load and not of death — ``tcp_is_listening`` is that probe.

SEAMS, AND WHAT THE CALLER OWNS. ``ping`` MUST own its own connection: it is polled
from the witness thread while ``restore`` runs on another, so sharing one client would
measure the client's lock, not the instance. ``dump``/``delete``/``restore`` are
callables so the driver never needs a credential-fetching or decoding policy of its
own; ``dump`` should return a binary-safe payload (a ``decode_responses=True`` client
raises ``UnicodeDecodeError`` on it) and ``RESTORE`` needs ``REPLACE`` — the engine
keeps the graph in its module registry after the Redis key is deleted, so a bare
``RESTORE`` fails ``BUSYKEY``.

⛔ **Do not use this driver to restore an indexed payload under a DIFFERENT graph
name while the source still exists.** On the engine this repo's CI pins
(``falkordb-server:v4.20.6``, graph module 42006) that SEGFAULTS the instance
(``Index_SetLanguage``, signal 11) — filed as #8270. The driver would report it
faithfully as ``INSTANCE_GONE``, but the crash is avoidable and the driver is not the
place to re-trigger it.
"""

from __future__ import annotations

import contextlib
import logging
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: The restore returned and every witness PING answered.
SERVED = "served"
#: The restore returned; the instance was off the air for part of it (a run of
#: unanswered witness PINGs, or an answered PING queued behind the rebuild) and the
#: outage ends when the restore does — the index rebuild on the serving thread
#: (criterion 2).
BLOCKED = "blocked"
#: The restore overran its bound and the port still accepts connections: blocked, not
#: load, not a crash.
ALIVE_BUT_SILENT = "alive-but-silent"
#: The restore overran its bound and the port refused: the process is gone.
INSTANCE_GONE = "instance-gone"
#: The restore overran its bound while EVERY witness PING answered: the instance was
#: serving, so the stall is the caller's — not the engine's.
CALLER_STALL = "caller-stall"
#: The restore call itself raised — its own error is re-raised (this status only
#: describes the observation attached to it as a note).
FAILED = "failed"

#: Default witness poll interval. Cheap enough not to load the instance, fine enough
#: to bound the measured silence to ±this plus the witness client's own timeout.
DEFAULT_POLL_INTERVAL_S = 0.1

#: An ANSWERED witness PING that took at least this long is itself evidence of the
#: block, even when the witness's socket timeout was long enough that nothing failed.
#: Provenance: measured 2026-10-12/13, an idle/local PING on this stack is ~0.8-3 ms and
#: the index-FREE control restore shows a worst PING of 53 ms, while the index-bearing
#: restore shows a worst PING equal to its own duration (2 249-2 497 ms). 250 ms is a
#: ~4.7x margin over the control and ~10x below the defect, so it cannot be reached by
#: ordinary latency and is always reached by a rebuild longer than a quarter second.
#: A caller on a busier host can raise it; the measurement itself (`blocked_ms`) is
#: always reported either way.
DEFAULT_BLOCKED_LATENCY_MS = 250.0


def tcp_is_listening(host: str, port: int, *, timeout_s: float = 1.0) -> bool:
    """Whether ``host:port`` ACCEPTS a TCP connection — process alive, no more.

    Deliberately transport-level. A FalkorDB instance blocked on its serving thread
    still completes the TCP handshake (the kernel accepts into the backlog) while
    answering no command at all, so this probe is what separates "blocked" from
    "dead": a dead process refuses the connection. It says nothing about whether the
    server is *serving* — that is the witness PING's job.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


@dataclass(frozen=True)
class RestoreObservation:
    """What ``restore_graph_key`` measured, including the parts that are not its own.

    ``silent_ms`` is the LONGEST contiguous run of unanswered witness PINGs, so a
    caller can compare the silence against the restore's own duration: for the #7846
    block the two are equal (the instance is unavailable exactly as long as the
    rebuild runs), while an unattributable silence outlasting the restore is a
    different fault.
    """

    graph: str
    status: str
    bound_s: float
    elapsed_s: float
    payload_bytes: int
    witness_samples: int
    witness_failures: int
    silent_ms: float
    silence_started_s: float | None
    silence_ended_s: float | None
    #: The slowest ANSWERED witness PING. A blocked engine queues the command on its
    #: serving thread, so a PING issued at the start of the restore answers only when
    #: the block ends — its latency IS the block, which is the only signal available
    #: when the block is shorter than the witness's own socket timeout.
    witness_max_latency_ms: float
    #: At or above this, an answered-but-slow witness PING counts as a block.
    blocked_latency_ms: float
    #: ``True`` = port accepts, ``False`` = refused, ``None`` = not probed (the
    #: restore returned, or no probe was supplied).
    instance_listening: bool | None
    error: str | None = None

    @property
    def silent(self) -> bool:
        """True when a witness PING went unanswered — an observed outage, not a slow one."""
        return self.witness_failures > 0

    @property
    def blocked_ms(self) -> float:
        """How long the instance was off the air, by the better of the two signals.

        ``silent_ms`` (an unanswered run) is the hard evidence; a slow ANSWERED PING is
        the same block seen from inside its own duration when the witness timeout is
        longer than the block.
        """
        return max(self.silent_ms, self.witness_max_latency_ms)

    def as_dict(self) -> dict[str, Any]:
        return {
            "graph": self.graph,
            "status": self.status,
            "bound_s": self.bound_s,
            "elapsed_s": round(self.elapsed_s, 3),
            "payload_bytes": self.payload_bytes,
            "witness_samples": self.witness_samples,
            "witness_failures": self.witness_failures,
            "silent_ms": round(self.silent_ms, 1),
            "witness_max_latency_ms": round(self.witness_max_latency_ms, 1),
            "blocked_ms": round(self.blocked_ms, 1),
            "silence_started_s": self.silence_started_s,
            "silence_ended_s": self.silence_ended_s,
            "instance_listening": self.instance_listening,
            "error": self.error,
        }

    def sentence(self) -> str:
        """One line, for a log record or an exception note."""
        return (
            f"graph-key RESTORE of {self.graph!r}: status={self.status}, "
            f"restore {self.elapsed_s:.2f}s of a {self.bound_s:g}s bound, "
            f"{self.payload_bytes} payload bytes, witness: "
            f"{self.witness_failures}/{self.witness_samples} PINGs unanswered, "
            f"slowest answered PING {self.witness_max_latency_ms:.0f} ms, "
            f"longest silence {self.silent_ms:.0f} ms, "
            f"port listening={self.instance_listening}"
        )

    def diagnosis(self) -> str:
        """The actionable, multi-sentence account used as the exception message."""
        if not self.witness_failures:
            # Every witness PING answered: the instance WAS serving, so the story is
            # not "blocked engine" and saying it was would send the operator to the
            # wrong place. (A caller-side stall — a client stuck on the payload, or a
            # socket write that never completed — reads exactly like this.)
            return (
                f"graph-key RESTORE of {self.graph!r} did not return within the "
                f"{self.bound_s:g}s caller-side bound, but every one of the "
                f"{self.witness_samples} witness PINGs on a separate connection "
                f"ANSWERED (slowest {self.witness_max_latency_ms:.0f} ms). The "
                f"instance was serving, so this is NOT the #7846 blocked-engine "
                f"signature: the restore itself is stuck. Check the CALLER — the "
                f"client's own socket/serialization, the payload, and the key's "
                f"state (`BUSYKEY`-class refusals) — before suspecting the engine."
            )
        header = (
            f"graph-key RESTORE of {self.graph!r} did not return within the "
            f"{self.bound_s:g}s caller-side bound, and the instance did not serve "
            f"during it: {self.witness_failures} of {self.witness_samples} witness "
            f"PINGs on a separate connection went unanswered (longest silence "
            f"{self.silent_ms:.0f} ms)."
        )
        if self.status == INSTANCE_GONE:
            return (
                f"{header} The port REFUSED a TCP connection, so the server process "
                f"is gone — it died during the restore. That is the #8270 class "
                f"(SIGSEGV in Index_SetLanguage on falkordb-server v4.20.x when an "
                f"indexed payload is restored under a new key while the source still "
                f"exists); every graph in that instance went with it. Restart the "
                f"instance and do NOT repeat this restore — same-name only."
            )
        liveness = (
            "The port still ACCEPTS a TCP connection, so the server process is alive and listening"
            if self.instance_listening
            else "Liveness could NOT be measured (no transport probe was supplied)"
        )
        return (
            f"{header} {liveness}: this is not a crash and not load — a loaded server "
            f"still answers, only slower. It is a BLOCKED server, the #7846 signature "
            f"(the vector index is rebuilt on the serving thread, so the whole "
            f"instance is off the air for the rebuild). Do not retry: a retry "
            f"re-blocks the instance for another full rebuild. The abandoned restore "
            f"may still complete — check with a PING on a separate connection before "
            f"restarting anything."
        )


class GraphRestoreSilentError(RuntimeError):
    """The restore overran its bound while the instance answered nothing, but the
    process is still listening (blocked, not dead, not loaded).

    Distinct from :class:`GraphRestoreLostError` on purpose: this one's recovery is
    "wait and re-probe, do not restart", the other's is "restart". A single error
    type would make an operator pick one and be wrong half the time.
    """

    def __init__(self, observation: RestoreObservation):
        self.observation = observation
        super().__init__(observation.diagnosis())


class GraphRestoreLostError(RuntimeError):
    """The restore overran its bound and the instance's port refused a connection —
    the process died during the restore."""

    def __init__(self, observation: RestoreObservation):
        self.observation = observation
        super().__init__(observation.diagnosis())


class GraphRestoreStalledError(RuntimeError):
    """The restore overran its bound while the instance kept SERVING — so the stall is
    on the caller's side, not the engine's.

    This error exists to stop the driver mis-blaming the engine: every witness PING
    answered, which cannot happen while the serving thread is inside a rebuild, so
    "blocked instance" would be the wrong diagnosis and the wrong recovery.
    """

    def __init__(self, observation: RestoreObservation):
        self.observation = observation
        super().__init__(observation.diagnosis())


class _Witness(threading.Thread):
    """Own connection (via ``ping``), sampled while the restore runs.

    Records ``(t_since_start, answered, latency_ms)`` per sample. A sample that
    outlives its own socket timeout is recorded as unanswered: the instance did not
    answer inside the caller's own bound for that probe, which is the fact the
    observation needs.
    """

    def __init__(self, ping: Callable[[], Any], interval_s: float):
        super().__init__(daemon=True, name="graph-key-restore-witness")
        self._ping = ping
        self._interval_s = interval_s
        # NOT `_stop`: that name is `threading.Thread._stop()` on py3.12, and shadowing
        # it makes `Thread.join()` raise `TypeError: 'Event' object is not callable`.
        self._halt = threading.Event()
        #: Set when a sample ANSWERED after an unanswered one — the instance came back.
        self._recovered = threading.Event()
        self.t0 = time.perf_counter()
        self.samples: list[tuple[float, bool, float]] = []

    def run(self) -> None:
        while not self._halt.is_set():
            started = time.perf_counter()
            offset = started - self.t0
            try:
                self._ping()
                answered = True
            except Exception:  # "did not answer" IS the measurement
                answered = False
            if answered:
                if self.samples and not self.samples[-1][1]:
                    self._recovered.set()
            else:
                # Track the TAIL: a mid-restore recovery followed by a second silence
                # must not look like the instance came back.
                self._recovered.clear()
            self.samples.append((offset, answered, (time.perf_counter() - started) * 1000.0))
            self._halt.wait(self._interval_s)

    def finish(
        self, *, deadline_s: float = 5.0, recovery_s: float = 0.0
    ) -> list[tuple[float, bool, float]]:
        """Stop sampling and return the samples.

        ``recovery_s`` > 0 keeps sampling — bounded — until the instance ANSWERS once
        more after an unanswered sample. That recovery edge is what makes the silence
        attributable to the restore instead of merely long; without it a restore that
        blocked the instance is indistinguishable from one that ran while the instance
        was already silent for another reason.
        """
        if recovery_s > 0 and self.samples and not self.samples[-1][1]:
            self._recovered.wait(recovery_s)
        self._halt.set()
        self.join(timeout=deadline_s)
        return self.samples


def _longest_silence(
    samples: list[tuple[float, bool, float]],
) -> tuple[float, float | None, float | None]:
    """``(silent_ms, started_s, ended_s)`` of the longest run of unanswered pings.

    ``ended_s`` is the timestamp of the first ANSWERED sample after the run — i.e.
    when the instance came back — or ``None`` when it had not come back by the last
    sample. That is what lets a caller check the silence ENDS with the restore
    (attributable) rather than outlasting it (a different fault, or a wedge).
    """
    best_ms = 0.0
    best_start: float | None = None
    best_end: float | None = None
    i = 0
    while i < len(samples):
        if samples[i][1]:
            i += 1
            continue
        j = i
        while j + 1 < len(samples) and not samples[j + 1][1]:
            j += 1
        start = samples[i][0]
        end = samples[j + 1][0] if j + 1 < len(samples) else None
        last_attempt = samples[j][0] + samples[j][2] / 1000.0
        span_ms = ((end if end is not None else last_attempt) - start) * 1000.0
        if span_ms > best_ms:
            best_ms, best_start, best_end = span_ms, start, end
        i = j + 1
    return best_ms, best_start, best_end


def restore_graph_key(
    *,
    graph: str,
    dump: Callable[[], Any],
    delete: Callable[[], Any],
    restore: Callable[[Any], Any],
    ping: Callable[[], Any],
    bound_s: float,
    listening: Callable[[], bool] | None = None,
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
    witness_join_s: float = 5.0,
    recovery_probe_s: float = 1.0,
    blocked_latency_ms: float = DEFAULT_BLOCKED_LATENCY_MS,
) -> RestoreObservation:
    """``DUMP`` -> ``DEL`` -> ``RESTORE``, bounded and witnessed.

    Returns a :class:`RestoreObservation` for ``SERVED`` / ``BLOCKED``.

    Raises:
        GraphRestoreSilentError: the restore overran ``bound_s`` and the instance
            answered nothing but its port still accepts — blocked (see the module
            docstring's table).
        GraphRestoreLostError: the restore overran ``bound_s`` and the port refused —
            the process died.
        GraphRestoreStalledError: the restore overran ``bound_s`` while EVERY witness
            PING answered — the instance was serving, so the stall is the caller's.
        BaseException: whatever ``restore`` itself raised, re-raised unchanged with
            the observation attached as a ``__notes__`` entry (its own error is
            already loud; this driver does not replace it).

    ``ping`` must own its OWN connection. The restore runs on a daemon worker thread
    and is ABANDONED at ``bound_s`` — never joined to completion — so a blocked
    restore cannot hold the caller past its own bound. ``recovery_probe_s`` is the
    extra, bounded window in which a completed restore waits for the instance to
    answer once more (the recovery edge that makes the silence attributable); it is
    not part of ``bound_s``.
    """
    if bound_s <= 0:
        raise ValueError(f"bound_s must be positive, got {bound_s!r}")

    payload = dump()
    payload_bytes = len(payload) if isinstance(payload, (bytes, bytearray, memoryview)) else 0
    delete()

    witness = _Witness(ping, poll_interval_s)
    witness.start()

    outcome: dict[str, Any] = {}

    def _run_restore() -> None:
        try:
            outcome["value"] = restore(payload)
        except BaseException as exc:  # re-raised on the caller's thread
            outcome["error"] = exc

    started = time.perf_counter()
    worker = threading.Thread(target=_run_restore, daemon=True, name="graph-key-restore")
    worker.start()
    worker.join(bound_s)
    elapsed_s = time.perf_counter() - started
    completed = not worker.is_alive()

    # `recovery_probe_s` only on the completed path: when the restore overran, the
    # instance is (by definition) not answering, and the liveness probe below is the
    # evidence — waiting for a recovery edge there would only extend the caller's bound.
    samples = witness.finish(
        deadline_s=witness_join_s,
        recovery_s=recovery_probe_s if completed else 0.0,
    )
    silent_ms, silence_started_s, silence_ended_s = _longest_silence(samples)
    failures = sum(1 for _offset, answered, _latency in samples if not answered)
    # Only ANSWERED samples carry a latency: an unanswered one's latency is the
    # witness client's own socket timeout, not a measurement of the block.
    answered_latencies = [lat for _o, answered, lat in samples if answered]
    max_latency_ms = max(answered_latencies) if answered_latencies else 0.0
    blocked = failures > 0 or max_latency_ms >= blocked_latency_ms

    error = outcome.get("error")
    if completed:
        observation = RestoreObservation(
            graph=graph,
            status=FAILED if error is not None else (BLOCKED if blocked else SERVED),
            bound_s=bound_s,
            elapsed_s=elapsed_s,
            payload_bytes=payload_bytes,
            witness_samples=len(samples),
            witness_failures=failures,
            silent_ms=silent_ms,
            silence_started_s=silence_started_s,
            silence_ended_s=silence_ended_s,
            witness_max_latency_ms=max_latency_ms,
            blocked_latency_ms=blocked_latency_ms,
            instance_listening=None,
            error=None if error is None else f"{type(error).__name__}: {error}",
        )
        logger.info("graph-key restore finished: %s", observation.sentence())
        if error is not None:
            # Keep the caller's own error type; the observation becomes a note.
            with contextlib.suppress(AttributeError):
                error.add_note(observation.sentence())
            raise error
        if observation.status == BLOCKED:
            logger.warning(
                "graph-key restore BLOCKED the instance for %.0f ms of its %.2fs "
                "(#7846 criterion 2 remains unmet: the index rebuild runs on the "
                "serving thread): %s",
                observation.blocked_ms,
                observation.elapsed_s,
                observation.sentence(),
            )
        return observation

    # The restore did not return inside the bound: the caller must fail loudly, with
    # the measurement, not hang. Liveness is probed AFTER the bound so it describes
    # the instance at the moment the caller gives up.
    instance_listening = None if listening is None else bool(listening())
    if not failures:
        status = CALLER_STALL
    elif instance_listening is False:
        status = INSTANCE_GONE
    else:
        status = ALIVE_BUT_SILENT
    observation = RestoreObservation(
        graph=graph,
        status=status,
        bound_s=bound_s,
        elapsed_s=elapsed_s,
        payload_bytes=payload_bytes,
        witness_samples=len(samples),
        witness_failures=failures,
        silent_ms=silent_ms,
        silence_started_s=silence_started_s,
        silence_ended_s=silence_ended_s,
        witness_max_latency_ms=max_latency_ms,
        blocked_latency_ms=blocked_latency_ms,
        instance_listening=instance_listening,
        error="restore did not return within the bound",
    )
    logger.error("graph-key restore overran its bound: %s", observation.sentence())
    if observation.status == CALLER_STALL:
        raise GraphRestoreStalledError(observation)
    if observation.status == INSTANCE_GONE:
        raise GraphRestoreLostError(observation)
    raise GraphRestoreSilentError(observation)
