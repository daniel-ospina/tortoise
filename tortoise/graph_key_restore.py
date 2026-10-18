"""A graph-key ``DUMP`` -> ``DEL`` -> ``RESTORE``, bounded at the RESTORE step and
OBSERVED throughout (#7846).

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

1. **Bounds the RESTORE step — and says so**, because the difference is a caller-side
   hazard (#8827 review). ``bound_s`` is a hard caller-side read bound on the
   ``restore`` call only. If the restore has not returned by then, the caller gets an
   error instead of a hang; the worker is a daemon and is abandoned (never joined to
   completion), so the RESTORE call can never hold the caller past the bound. The TAIL
   after it is finite but NOT free, and it is named here rather than folded away
   (#8827 review): the witness join (``witness_join_s``, 5 s by default, because a
   witness blocked inside its own PING is only released when that PING does) plus the
   caller-supplied liveness probe's own timeout (``tcp_is_listening``'s 1 s default),
   plus — on a COMPLETED run — the bounded ``recovery_probe_s`` window (1 s default) in
   which the driver waits for the instance to answer once more. So the tail AFTER
   ``DUMP``/``DEL`` is at most
   ``bound_s + recovery_probe_s + witness_join_s + probe_timeout`` (the
   recovery term is spent only when the restore returned; an overrun spends the whole
   ``bound_s`` instead), and ``elapsed_s`` is THAT TAIL **plus** the caller's own
   ``dump()``/``delete()`` time: it is captured when the observation is published, not
   at the bound, so the caller-held time in the data is the whole call and cannot drift
   below what the caller actually waited. There is no bound that covers the inline
   ``dump()``/``delete()`` — that is the paragraph below, and conflating them understated
   the ceiling (#8827 review).
   ``dump`` and ``delete`` run INLINE on the caller's thread *before* the worker is
   started, so ``bound_s`` does NOT cover them: their only limit is whatever socket
   timeout the caller's own client carries. Give that client a socket timeout below
   ``bound_s`` (the live test's 120 s against a 90 s bound is the shape) — a hung
   ``DUMP`` otherwise reproduces criterion 1's "the caller hangs with no error it can
   act on" OUTSIDE the advertised bound. The driver reports the split so the seam is
   visible in the data and not only in this paragraph: ``elapsed_s`` is how long the
   caller was held by the whole sequence, ``restore_s`` is the bounded step. The
   witness samples from BEFORE ``dump()`` for exactly that reason (a block that begins
   in ``DUMP`` is still measured, and the measured window spans the whole sequence).
2. **Watches the instance from a SECOND connection throughout** — a witness thread
   polls ``ping`` while the restore runs — so the silence is *measured* rather than
   inferred from the operation's duration.
3. **Tells the states apart**, which is the whole point of criterion 5:

   =======================  =============================================  =====================
   state                    evidence                                       what it means
   =======================  =============================================  =====================
   ``SERVED``               restore returned; at least one witness PING   load, if anything
                            was SAMPLED and every one answered
   ``BLOCKED``              restore returned with witness evidence: >=1    the instance was off
                            PING unanswered, or an ANSWERED PING took      the air for the
                            >= ``blocked_latency_ms`` (queued behind the   restore only
                            rebuild); the silence / slow PING ENDS when
                            the restore does
   ``ALIVE_BUT_SILENT``     restore overran ``bound_s``; PINGs unanswered  blocked server: not
                            (or one ANSWERED slowly) AND the port still    load, not a crash
                            ACCEPTS a connection
   ``LIVENESS_UNMEASURED``  restore overran ``bound_s``; PINGs unanswered  the BLOCK is
                            (or one ANSWERED slowly) and liveness was      measured; blocked
                            NOT measured: no ``listening`` callable, or    vs dead is NOT
                            one that raised / returned ``None``
   ``INSTANCE_GONE``        restore overran ``bound_s``; port REFUSED      the process died
                                                                           (cause not proved)
   ``CALLER_STALL``         restore overran ``bound_s`` but EVERY PING      the caller is stuck,
                            answered — the serving thread cannot be        not the engine
                            inside a rebuild while that is true
   ``FAILED``               the RESTORE call itself raised; its own        the graph key was
                            error is RE-RAISED unchanged, with this        ALREADY DELETED — the
                            observation attached as a ``__notes__``        graph is gone, and the
                            entry                                          payload in the caller's
                                                                           hands is the only
                                                                           copy left
   ``UNMEASURED``           the witness produced nothing it can trust:     nothing is known about
                            zero samples inside the window, or a           the instance — this is
                            NON-transport witness failure (WRONGPASS,       NOT a green, and not
                            a DNS error, a probe bug)                      a block verdict either
   =======================  =============================================  =====================

   ``UNMEASURED`` exists because a verdict that was never measured must not be
   published as one (#8827 review): the completed path used to answer ``SERVED`` when
   the witness had recorded nothing at all (its PING outliving the window) or had
   failed for its OWN reasons, and a caller switching on ``status`` read that green on
   a run where nothing answered. ``SERVED`` now requires a measured negative;
   ``observation.measured`` says whether the verdict rests on any measurement.

   ``BLOCKED`` is not a failure — it is the measured fact criterion 2 records as
   unmet, reported instead of hidden. Two signals make it measurable at any scale: an
   unanswered PING (socket timeout expired) and an ANSWERED PING whose latency is the
   block itself (the engine queues the command behind the rebuild on its serving
   thread). The second is why a 0.27 s block at 3 000 nodes is still reported rather
   than greened by a 1 s witness timeout. The overrun states raise
   ``GraphRestoreSilentError`` (``ALIVE_BUT_SILENT``, ``LIVENESS_UNMEASURED``) /
   ``GraphRestoreLostError`` (``INSTANCE_GONE``) / ``GraphRestoreStalledError``
   (``CALLER_STALL``), whose messages carry the measurement and the recovery
   instruction. ``FAILED`` raises nothing of its own — the caller's error is re-raised
   unchanged — and it is in this table for the opposite reason: its CONSEQUENCE is the
   worst one here. The sequence is ``dump()`` → ``delete()`` → ``restore()``, so by the
   time the RESTORE raises the graph key is ALREADY DELETED and the payload exists only
   in the caller's hands. Omitting it left that run described by the served/unmeasured
   text, which could tell the operator the instance "served through the restore" on the
   one run that lost the graph (#8827 review).

   ``LIVENESS_UNMEASURED`` exists because the block and the DEATH are told apart by the
   transport probe alone (#8827 review): with ``listening`` omitted, an overrun that
   measured the silence had no evidence about the process at all, yet it was published
   as ``ALIVE_BUT_SILENT`` and raised an error whose contract said the process was still
   listening. Passing ``listening`` is therefore the DISCRIMINATOR between *blocked* and
   *dead*, not a refinement of the blocked message: omit it and a refused port is
   indistinguishable from a blocked one.
   ``UNMEASURED`` is reachable on BOTH paths, and the two behave differently: on a
   COMPLETED restore it is RETURNED as an observation whose message says the reading is
   unusable (the restore returned, so there is nothing to hang on), while on an OVERRUN
   it RAISES ``GraphRestoreSilentError`` (the caller must not be left hanging) carrying
   that same unusable-reading message rather than an engine verdict. So the exception
   channel is not a measurement channel: no ``GraphRestoreSilentError`` does NOT mean the
   run was measured — read ``observation.measured`` / ``status`` for that. The three
   overrun errors are deliberately DIFFERENT: one restarts nothing and waits, one needs a
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
#: The restore overran its bound and the witness measured a block, but no liveness probe
#: was supplied: the block is measured, blocked-vs-dead is NOT. Its own status because
#: ``ALIVE_BUT_SILENT`` (and ``GraphRestoreSilentError``'s contract) asserts a live
#: process that nothing measured (#8827 review).
LIVENESS_UNMEASURED = "liveness-unmeasured"
#: The restore overran its bound and the port refused: the process is gone.
INSTANCE_GONE = "instance-gone"
#: The restore overran its bound while EVERY witness PING answered: the instance was
#: serving, so the stall is the caller's — not the engine's.
CALLER_STALL = "caller-stall"
#: The restore call itself raised — its own error is re-raised (this status only
#: describes the observation attached to it as a note).
FAILED = "failed"
#: The witness recorded nothing a verdict may rest on: no samples inside the measured
#: window (its own PING outlived the whole operation), or a witness failure that is
#: NOT transport-level (``WRONGPASS``, a DNS error, a probe bug). Neither a green nor
#: the #7846 block — the outage is unattributable (#8827 review).
UNMEASURED = "unmeasured"

# NOTE: there is deliberately no "overrun statuses" set here any more. It existed to let
# ``_verdict_line`` decide "gave up vs returned" from ``status``, and ``UNMEASURED`` is
# reachable on BOTH paths — so a restore that returned in 0.05 s was described as "did
# not return within the 5s caller-side bound" (#8827 review). The driver already knows
# which arm it is on: that fact is published as ``RestoreObservation.restore_returned``,
# and every consequence (which error is raised, whether the recovery probe runs, how the
# account is worded) reads that one flag instead of inferring it from a status.

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


def tcp_is_listening(host: str, port: int, *, timeout_s: float = 1.0) -> bool | None:
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
    except ConnectionRefusedError:
        # The kernel ACTIVELY refused: nothing is listening. This is the only
        # signal that may be read as "the process is gone".
        return False
    except OSError:
        # Timeout / EHOSTUNREACH / ENETUNREACH — we could not MEASURE.
        #
        # `socket.timeout` is a subclass of `OSError`, and a BLOCKED instance whose
        # accept backlog is full drops SYNs, so a connect TIMEOUT is expected
        # precisely in the case this probe exists for. Reporting it as a refusal
        # told the operator "the port REFUSED … the process is gone … restart the
        # instance" about a healthy but blocked box (#8827 review).
        return None


#: Exception types that mean "the instance did not answer" rather than "the witness
#: could not ask". Anything else (WRONGPASS, gaierror, a probe bug) is the instrument
#: failing, and an outage measured that way cannot be attributed to the engine.
_WITNESS_TRANSPORT_ERRORS = frozenset(
    {
        "TimeoutError",
        "OSError",
        "ConnectionError",
        "ConnectionAbortedError",
        "ConnectionRefusedError",
        "ConnectionResetError",
        "BrokenPipeError",
    }
)


def _instrument_failures(error_names: tuple[str, ...]) -> tuple[str, ...]:
    """The witness failure names that are NOT transport-level — empty means none.

    POLARITY IS THE WHOLE POINT (#8827 review). "No fault" must be the POSITIVE claim:
    a run is unattributable only when EVERY failure is a transport error, so ONE
    non-transport name disqualifies it. The previous test asked the opposite question —
    ``not (set(errors) & _WITNESS_TRANSPORT_ERRORS)``, i.e. "is this set DISJOINT from
    transport" — so a MIXED set (``TimeoutError`` from a blocked engine alongside
    ``AuthenticationError`` from the witness) answered "pure transport": the auth
    failure was dropped from the verdict, and the run was published as a trustworthy
    ``BLOCKED`` / do-not-retry while the one error an operator needed was surfaced
    nowhere.

    Names are compared CONCRETELY — the witness records ``type(exc).__name__``, never an
    ``isinstance`` walk: ``redis.exceptions.AuthenticationError`` SUBCLASSES
    ``ConnectionError``, so a subclass test would smuggle an auth failure into the very
    transport set it must never join.

    A module-level function rather than a method because the driver and
    :attr:`RestoreObservation.witness_fault` must not be able to drift: they carry the
    SAME claim — the status the caller switches on, and the account it reads — and they
    were two copies of one expression before this (#8827 review).
    """
    return tuple(name for name in error_names if name not in _WITNESS_TRANSPORT_ERRORS)


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
    #: How long the CALLER was held — from before ``dump()`` to the moment this
    #: observation was PUBLISHED, so it includes the tail that explaining an overrun
    #: needs (the witness join, the liveness probe, and on a completed run the bounded
    #: recovery probe). ``bound_s`` covers the RESTORE step only (``dump``/``delete``
    #: run inline on the caller's thread before the worker starts), so this number and
    #: ``restore_s`` are reported separately rather than one standing for both
    #: (#8827 review). ``elapsed_s >= restore_s`` always; a caller-held time that
    #: stopped at the bound would under-report the call by up to
    #: ``witness_join_s + probe_timeout``, which is the same measurement-vs-operation
    #: drift this driver exists to remove (#8827 review cycle 9).
    elapsed_s: float
    #: How long the RESTORE step itself took — the step ``bound_s`` bounds.
    restore_s: float
    payload_bytes: int
    #: Witness PINGs sampled INSIDE the measured window (from before ``dump()`` to the
    #: restore's return). Samples taken afterwards — the recovery probe — describe
    #: liveness, not attribution, so they are deliberately not counted here.
    witness_samples: int
    witness_failures: int
    #: Distinct exception types the WITNESS raised while sampling. Non-empty with a
    #: non-socket type means the measurement failed on the caller's side and the
    #: outage cannot be attributed to the engine.
    witness_errors: tuple[str, ...]
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
    #: ``True`` = port accepts, ``False`` = refused, ``None`` = not probed. ``None`` on
    #: the overrun path is not a detail: it is what makes the status
    #: ``LIVENESS_UNMEASURED`` instead of ``ALIVE_BUT_SILENT`` (#8827 review).
    instance_listening: bool | None
    #: Whether the RESTORE call itself returned before ``bound_s``. The status cannot
    #: stand in for it: ``UNMEASURED`` is reachable on BOTH paths, so keying the
    #: account's wording on ``status`` made a restore that returned in 0.05 s describe
    #: itself as "did not return within the 5s caller-side bound" (#8827 review). Every
    #: other consequence of the split (which error is raised, whether the recovery probe
    #: runs) already keys on this flag; publishing it puts the measurement in the data.
    restore_returned: bool
    #: The free-text reason behind ``error``/``status`` — the restore's own error on
    #: ``FAILED``, the unmeasured reason on ``UNMEASURED``, or what the liveness probe did
    #: on an overrun.
    error: str | None = None
    #: Set when the caller's ``listening`` probe RAISED. A probe that cannot answer is
    #: ``instance_listening is None`` — "could not measure", never a refusal — so this
    #: field is what lets the account say that instead of claiming no probe was supplied
    #: (#8827 review).
    liveness_error: str | None = None

    @property
    def silent(self) -> bool:
        """True when a witness PING went unanswered — an observed outage, not a slow one."""
        return self.witness_failures > 0

    @property
    def witness_fault(self) -> bool:
        """True when the witness failed for its OWN reasons, so its silence measures nothing.

        A transport error (timeout, reset, refused) is the instance not answering. Anything
        else — ``WRONGPASS``, a DNS ``gaierror``, a bug in the probe — is the INSTRUMENT
        failing, and an outage measured that way cannot be attributed to the engine. ONE
        such failure is enough, even when transport failures appear in the same set: see
        :func:`_instrument_failures` (#8827 review).
        """
        return bool(_instrument_failures(self.witness_errors))

    @property
    def measured(self) -> bool:
        """True when this verdict rests on a witness sample that actually answered or timed out.

        ``False`` means the observation carries no usable measurement: zero in-window
        samples, or an instrument fault. A caller that switches on ``status`` alone must
        never see this as a green — which is why such a run is ``UNMEASURED`` and not
        ``SERVED`` (#8827 review).
        """
        return self.witness_samples > 0 and not self.witness_fault

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
            "restore_s": round(self.restore_s, 3),
            "payload_bytes": self.payload_bytes,
            "witness_samples": self.witness_samples,
            "witness_failures": self.witness_failures,
            "witness_errors": list(self.witness_errors),
            "measured": self.measured,
            "silent_ms": round(self.silent_ms, 1),
            "witness_max_latency_ms": round(self.witness_max_latency_ms, 1),
            "blocked_ms": round(self.blocked_ms, 1),
            "silence_started_s": self.silence_started_s,
            "silence_ended_s": self.silence_ended_s,
            "instance_listening": self.instance_listening,
            "restore_returned": self.restore_returned,
            "error": self.error,
            "liveness_error": self.liveness_error,
        }

    def sentence(self) -> str:
        """One line, for a log record or an exception note."""
        return (
            f"graph-key RESTORE of {self.graph!r}: status={self.status}, "
            f"caller held {self.elapsed_s:.2f}s (RESTORE step {self.restore_s:.2f}s of "
            f"a {self.bound_s:g}s bound), {self.payload_bytes} payload bytes, witness: "
            f"{self.witness_failures}/{self.witness_samples} PINGs unanswered, "
            f"slowest answered PING {self.witness_max_latency_ms:.0f} ms, "
            f"longest silence {self.silent_ms:.0f} ms, "
            f"port listening={self.instance_listening}"
        )

    def _verdict_line(self) -> str:
        """The measurement every diagnosis opens with — never a claim of its own.

        This holds on EVERY arm, the overrun included. The overrun arm used to append
        "so the instance did not serve normally during it", unconditionally, and that
        clause contradicted the driver's own status two layers up (#8827 review): on
        ``CALLER_STALL`` (zero unanswered PINGs, no slow one) the very next sentence
        says the instance WAS serving and the restore is stuck, and on an ``UNMEASURED``
        instrument fault it asserts a verdict about a run the driver then calls
        "nothing was measured". An operator — or an alert rule matching "did not serve"
        — reads the FIRST sentence, which is the mis-blame class cycle 9 fixed inside
        ``diagnosis()``. So the verdict line now reports the two measurements and stops;
        the status-specific paragraph below draws the conclusion (the zero-sample arm
        already worked this way)."""
        if self.status == FAILED:
            # #8827 review round 2: "returned after …s" immediately followed by "The
            # RESTORE call itself FAILED" is a contradiction, and a consumer switching on
            # the flag the docstring names would read a failed run as a returned one. The
            # flag's meaning is "the worker FINISHED inside the bound"; the verdict line
            # says which of the two it was.
            return (
                f"graph-key RESTORE of {self.graph!r} FINISHED after "
                f"{self.restore_s:.2f}s (bound {self.bound_s:g}s) — its RESTORE call "
                f"RAISED, so it did not return; "
                # #8827 review round 3: "0 of 0 witness PINGs went unanswered (slowest
                # answered 0 ms)" reads as a CLEAN witness on a run that measured
                # nothing. The zero-sample arm below exists to prevent exactly that
                # wording, and this arm sat above it, so the vacuous-green shape rounds
                # 1-2 removed came back on the arm that precedes it.
                + (
                    f"{self.witness_failures} of {self.witness_samples} witness PINGs "
                    f"went unanswered (slowest answered "
                    f"{self.witness_max_latency_ms:.0f} ms)."
                    if self.witness_samples
                    else "the witness took NO samples inside the window, so its "
                         "behaviour is not evidence about the instance either way."
                )
            )
        if self.witness_samples == 0:
            gave_up = (
                f"returned after {self.restore_s:.2f}s"
                if self.restore_returned
                else f"did not return within the {self.bound_s:g}s caller-side bound"
            )
            return (
                f"graph-key RESTORE of {self.graph!r} {gave_up}, and the witness took NO "
                f"samples inside the window: nothing was measured, so nothing here is "
                f"evidence about the instance in either direction."
            )
        if not self.restore_returned:
            # Both signals, always: "0 of N went unanswered" on its own reads as "the
            # instance was serving", which is the very inversion the status avoids
            # (#8827 review). The slowest ANSWERED PING is what carries a latency-shaped
            # block on this path. NO conclusion here — see the docstring: the driver has
            # already decided the status, and appending one contradicted it (#8827 review).
            return (
                f"graph-key RESTORE of {self.graph!r} did not return within the "
                f"{self.bound_s:g}s caller-side bound: {self.witness_failures} of "
                f"{self.witness_samples} witness PINGs on a separate connection went "
                f"unanswered (longest silence {self.silent_ms:.0f} ms) and the slowest "
                f"ANSWERED one took {self.witness_max_latency_ms:.0f} ms."
            )
        return (
            f"graph-key RESTORE of {self.graph!r} returned after {self.restore_s:.2f}s "
            f"(bound {self.bound_s:g}s) with {self.witness_failures} of "
            f"{self.witness_samples} witness PINGs unanswered (slowest answered PING "
            f"{self.witness_max_latency_ms:.0f} ms)."
        )

    def _liveness_unmeasured_text(self) -> str:
        """What ``LIVENESS_UNMEASURED`` may say about the probe — and only that.

        The account used to read *"no transport probe was supplied (pass ``listening=...``)"*
        on every arm, which is a false statement about a probe the caller DID supply and
        that RAISED, and about one that returned ``None`` ("could not measure"). Both
        select this status (#8827 review).
        """
        if self.liveness_error is not None:
            return (
                f"LIVENESS WAS NOT MEASURED: {self.liveness_error}, so this account "
                f"cannot tell a BLOCKED process from a DEAD one, and it makes no claim "
                f"either way."
            )
        return (
            "LIVENESS WAS NOT MEASURED: no transport probe supplied an answer (omit "
            "``listening`` and there is none; pass ``listening=tcp_is_listening(host, "
            "port)`` and a probe that returns ``None`` could not measure), so this "
            "account cannot tell a BLOCKED process from a DEAD one, and it makes no "
            "claim either way."
        )

    def diagnosis(self) -> str:
        """The actionable, multi-sentence account used as the exception message.

        Keyed on ``status`` FIRST and the witness counts SECOND. Branching on
        ``witness_failures`` alone was free to assert the OPPOSITE of the verdict the
        driver had just decided (#8827 review), in three reachable states: zero samples
        with an overrun (``witness_failures == 0`` made an unmeasured run read "every
        one of the 0 witness PINGs ANSWERED … Check the CALLER"), zero samples with a
        REFUSED port (the death branch sat below the now-truthy "the instance was
        serving" text, so ``GraphRestoreLostError`` told the operator to check the
        caller while the process was gone), and an instrument fault (``witness_fault``
        recorded the truth in ``error`` and then never read it, so the raised message
        still said "It is a BLOCKED server … Do not retry"). All three are fixed by the
        same ordering: the status selects the account, and ``self.error`` is rendered
        rather than left in a field nobody reads.
        """
        verdict = self._verdict_line()
        if self.status == FAILED:
            # FIRST, and above every witness branch: the graph is gone, which outranks any
            # reading. There was no FAILED branch at all (#8827 review), so this run —
            # the only one whose consequence is a LOST GRAPH — fell through to the
            # served/unmeasured text and could be described as an instance that "served
            # through the restore".
            return (
                f"{verdict} The RESTORE call itself FAILED ({self.error}). The graph was "
                f"NOT restored: the driver's own ``delete()`` ran BEFORE the RESTORE, so "
                f"the graph key is ALREADY DELETED from the instance and the only copy "
                f"of the payload is the one in the caller's hands. Do not exit and do "
                f"not discard that payload — re-issue the RESTORE from it before doing "
                f"anything else, then decide whether the refusal (a ``BUSYKEY``-class "
                f"error, an index rebuild error, a dropped connection) needs a different "
                f"recovery. NOTHING HERE MEASURED THE INSTANCE'S LIVENESS: no transport "
                f"probe runs on this path, so a dropped or reset connection is equally "
                f"consistent with the process having DIED (#8270) — check the port and "
                f"that the KEY exists (``GRAPH.LIST`` / an ``EXISTS``-class check) with "
                f"the restored node count before re-issuing, and do not read a PING "
                f"reply as 'the restore is fine'."
                # #8827 review round 2: this branch must not assert a liveness it never
                # measured. "The instance is fine" was an inference over evidence that
                # was never taken, and the shape that reaches here with a DEAD instance
                # is the SIGSEGV/OOM one this module's own rule documents.
            )
        if self.status == CALLER_STALL:
            # Every witness PING answered: the instance WAS serving, so the story is
            # not "blocked engine" and saying it was would send the operator to the
            # wrong place. (A caller-side stall — a client stuck on the payload, or a
            # socket write that never completed — reads exactly like this.)
            return (
                f"{verdict} Every one of the {self.witness_samples} witness PINGs on a "
                f"separate connection ANSWERED (slowest {self.witness_max_latency_ms:.0f} "
                f"ms). The instance was serving, so this is NOT the #7846 blocked-engine "
                f"signature: the restore itself is stuck. Check the CALLER — the "
                f"client's own socket/serialization, the payload, and the key's state "
                f"(`BUSYKEY`-class refusals) — before suspecting the engine."
            )
        if self.status == INSTANCE_GONE:
            # Before the witness counts: a refused port is evidence in its own right, and
            # it may coexist with an empty sample list.
            return (
                f"{verdict} The port REFUSED a TCP connection, so the server process "
                f"is gone — it died during the restore, and every graph in that instance "
                f"went with it. A refusal proves the DEATH, not its cause (#8827 review): "
                f"if the payload was being restored under a NEW key while the source "
                f"still existed, this is the #8270 trigger (SIGSEGV in "
                f"Index_SetLanguage on falkordb-server v4.20.x) — the shape the ⛔ rule "
                f"above forbids, so a caller who followed it can rule that out; an "
                f"OOM-kill or any other crash is equally consistent with what was "
                f"measured. Restart the instance and do NOT repeat this restore — "
                f"same-name only."
            )
        if self.status == LIVENESS_UNMEASURED:
            # The block WAS measured; the process's liveness was not. Say only that.
            # The blocked message used to be delivered here too, and it asserted "this is
            # not a crash" about a port nothing had probed (#8827 review).
            best = (
                "No witness PING went unanswered, but the slowest ANSWERED one took "
                f"{self.witness_max_latency_ms:.0f} ms (threshold "
                f"{self.blocked_latency_ms:.0f} ms), so the command queued behind the "
                "index rebuild"
                if not self.witness_failures
                else f"{self.witness_failures} of {self.witness_samples} witness PINGs "
                f"on a separate connection went unanswered (longest silence "
                f"{self.silent_ms:.0f} ms)"
            )
            return (
                f"{verdict} {best}. That is the #7846 BLOCK measured from the witness "
                f"side — the instance did not answer normally during the restore. "
                f"{self._liveness_unmeasured_text()} Do not retry (a retry re-blocks the "
                f"instance for another full rebuild), and do not restart on this alone: "
                f"probe the port and PING on a separate connection yourself — a REFUSED "
                f"port means the process is gone and needs a restart; an ACCEPTED one "
                f"means blocked, so wait and re-probe. Either way the KEY is what you are "
                f"recovering: the graph key was DELETED before the RESTORE, so confirm it "
                f"exists again (``GRAPH.LIST`` / an ``EXISTS``-class check) with the "
                f"restored node count — a live instance with no key is still a lost graph."
            )
        if self.status == UNMEASURED or self.witness_fault or not self.witness_samples:
            # The instrument failed, or it never got a reading. Neither is the block, and
            # neither is a green — the ONLY safe instruction is to fix the witness.
            reason = self.error or "the witness produced no reading the driver can use"
            return (
                f"{verdict} {reason}. This is an INSTRUMENT FAULT, not an engine "
                f"verdict: do NOT read it as the #7846 block, and do not restart the "
                f"instance on it. A witness PING whose own socket timeout is longer "
                f"than ``bound_s + witness_join_s`` never returns a sample at all, so "
                f"give the witness client a short timeout (below ``bound_s``), fix its "
                f"credentials/route if the failure above names them, then re-probe the "
                f"instance with a PING on a separate connection — and confirm the graph "
                f"KEY is back (``GRAPH.LIST`` / an ``EXISTS``-class check) AND that its "
                f"node count is the restored one, because a PING says the instance is up, "
                f"not that the RESTORE took, and an existing key holding a partial or "
                f"empty graph passes a key-exists check."
            )
        if self.witness_failures:
            # `instance_listening is None` on the overrun path is LIVENESS_UNMEASURED and
            # never reaches here (it has its own account above). On the COMPLETED path the
            # port is not probed at all, and the restore RETURNING is what establishes that
            # the process was alive — say that instead of implying a probe ran.
            if self.instance_listening:
                liveness = (
                    "The port still ACCEPTS a TCP connection, so the server process is "
                    "alive and listening"
                )
            elif self.restore_returned:
                liveness = (
                    "The RESTORE call RETURNED, so the process was alive at least until "
                    "it did; the port itself was NOT probed on this path"
                )
            else:
                liveness = "Liveness was NOT measured, so blocked and dead are not told apart"
            return (
                f"{verdict} {liveness}: this is not a crash and not load — a loaded "
                f"server still answers, only slower. It is a BLOCKED server, the #7846 "
                f"signature (the vector index is rebuilt on the serving thread, so the "
                f"whole instance is off the air for the rebuild). Do not retry: a retry "
                f"re-blocks the instance for another full rebuild. The abandoned restore "
                f"may still complete, and a PING cannot tell you whether it did — the "
                f"graph key was DELETED before the RESTORE. Check the KEY: that it exists "
                f"again (``GRAPH.LIST`` / an ``EXISTS``-class check) and that its node "
                f"count is the restored one, before restarting anything. A PING answers "
                f"only whether the instance is up."
            )
        if self.witness_max_latency_ms >= self.blocked_latency_ms:
            # Measured, no PING went unanswered — but a slow ANSWERED one IS the block,
            # seen from inside its own duration. Reaching the branch below would say
            # "none was slow enough" about the run whose slow PING is the whole evidence
            # (#8827 review cycle 9), on both the completed BLOCKED path and the overrun
            # ALIVE_BUT_SILENT path this now also covers.
            return (
                f"{verdict} No witness PING went unanswered, but the slowest ANSWERED "
                f"one took {self.witness_max_latency_ms:.0f} ms (threshold "
                f"{self.blocked_latency_ms:.0f} ms): the command waited on the serving "
                f"thread, queued behind the index rebuild, so the instance was NOT "
                f"serving normally. This is the #7846 block measured from inside its own "
                f"duration — not load (a loaded server answers, only slower than this) "
                f"and not the caller. Do not retry: a retry re-blocks the instance for "
                f"another full rebuild. The abandoned restore may still complete, and a "
                f"PING cannot tell you whether it did — the graph key was DELETED before "
                f"the RESTORE. Check the KEY: that it exists again (``GRAPH.LIST`` / an "
                f"``EXISTS``-class check) and that its node count is the restored one, "
                f"before restarting anything. A PING answers only whether the instance "
                f"is up."
            )
        # Measured, every PING answered, no slow one: nothing was wrong with the instance.
        return (
            f"{verdict} Every witness PING answered and none was slow enough to be the "
            f"#7846 block (threshold {self.blocked_latency_ms:.0f} ms), so the instance "
            f"served through the restore."
        )


class GraphRestoreSilentError(RuntimeError):
    """The restore overran its bound and the caller must not be left hanging.

    Three measured situations raise it, and the message says which one it is:

    * ``ALIVE_BUT_SILENT`` — the witness measured a block AND a liveness probe accepted
      the port: the process is alive, so this is blocked, not dead and not loaded.
    * ``LIVENESS_UNMEASURED`` — the block was measured but NO liveness probe was supplied,
      so blocked-vs-dead was not established and the message claims neither.
    * ``UNMEASURED`` — the witness produced no reading a verdict may rest on; the message
      is an instrument report, not an engine verdict.

    Distinct from :class:`GraphRestoreLostError` on purpose: the silent family's recovery
    is "wait and re-probe, do not restart", the other's is "restart". A single error
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
        #: Distinct exception TYPE names raised by the witness's own PING. A
        #: `WRONGPASS`, a DNS `gaierror`, or a bug in the probe is the WITNESS
        #: failing — not the engine staying silent — and must never be reported as
        #: the #7846 block (#8827 review).
        self.unanswered_errors: set[str] = set()

    def run(self) -> None:
        while not self._halt.is_set():
            started = time.perf_counter()
            offset = started - self.t0
            try:
                self._ping()
                answered = True
            except Exception as exc:  # "did not answer" IS the measurement
                answered = False
                # Keep WHICH failure: discarding it made the driver report "It is a
                # BLOCKED server … Do not retry" for an authentication error.
                self.unanswered_errors.add(type(exc).__name__)
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


def _longest_unanswered_run(
    samples: list[tuple[float, bool, float]],
) -> tuple[int, int] | None:
    """``(first_index, last_index)`` of the longest run of unanswered PINGs, or ``None``.

    The run is described by INDEXES into ``samples`` so its end edge can be followed into
    the samples that come after the window (§``_longest_silence``).
    """
    best: tuple[int, int] | None = None
    best_span_s = 0.0
    i = 0
    while i < len(samples):
        if samples[i][1]:
            i += 1
            continue
        j = i
        while j + 1 < len(samples) and not samples[j + 1][1]:
            j += 1
        # The run lasts until its last sample either answered (its latency) or timed out
        # (its latency is the attempt), so this is a span the driver measured.
        span_s = samples[j][0] + samples[j][2] / 1000.0 - samples[i][0]
        if span_s > best_span_s:
            best_span_s, best = span_s, (i, j)
        i = j + 1
    return best


def _longest_silence(
    window: list[tuple[float, bool, float]],
    samples: list[tuple[float, bool, float]],
) -> tuple[float, float | None, float | None]:
    """``(silent_ms, started_s, ended_s)`` of the longest unanswered run INSIDE ``window``.

    ALL THREE numbers describe ONE run, and that is the point (#8827 review): the previous
    version took the magnitude from the in-window list and the end edge from a SECOND
    maximum over the full list, so a longer POST-window silence donated its end to the
    in-window run — publishing a "50 ms" silence that started at 0.10 s and ended at
    2.00 s, a triple no consumer can reconcile.

    ``window`` must be a PREFIX of ``samples`` (the in-operation samples, followed by any
    post-operation recovery-probe ones). ``silent_ms`` stays inside the window, because it
    is the magnitude ATTRIBUTED to the operation (a silence that outlasts the restore is
    not the index rebuild and must not inflate ``blocked_ms``); only ``ended_s`` reaches
    past the window, and only along the run it already identified: ``ended_s`` is the
    first ANSWERED sample after that run — when the instance came back — or ``None`` if it
    had not come back by the last sample.
    """
    run = _longest_unanswered_run(window)
    if run is None:
        return 0.0, None, None
    start_index, last_index = run
    start = samples[start_index][0]
    # Follow THIS run forward through the full list for its recovery edge.
    end_index = last_index + 1
    while end_index < len(samples) and not samples[end_index][1]:
        end_index += 1
    end = samples[end_index][0] if end_index < len(samples) else None
    if end_index < len(window):
        # The run recovered inside the window: the span ends where the instance answered.
        span_ms = (samples[end_index][0] - start) * 1000.0
    else:
        # It had not recovered before the window closed: the span is the measured silence
        # up to the last attempt inside the window.
        tail = samples[len(window) - 1]
        span_ms = (tail[0] + tail[2] / 1000.0 - start) * 1000.0
    return span_ms, start, end


def restore_graph_key(
    *,
    graph: str,
    dump: Callable[[], Any],
    delete: Callable[[], Any],
    restore: Callable[[Any], Any],
    ping: Callable[[], Any],
    bound_s: float,
    listening: Callable[[], bool | None] | None = None,
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
    witness_join_s: float = 5.0,
    recovery_probe_s: float = 1.0,
    blocked_latency_ms: float = DEFAULT_BLOCKED_LATENCY_MS,
) -> RestoreObservation:
    """``DUMP`` -> ``DEL`` -> ``RESTORE``, bounded AT THE RESTORE STEP, and witnessed.

    Returns a :class:`RestoreObservation` for ``SERVED`` / ``BLOCKED`` / ``UNMEASURED``
    (see the module docstring's table; ``UNMEASURED`` is not a green — it says the
    witness produced no reading a verdict may rest on). ``UNMEASURED`` is RETURNED
    here when the restore returned; it is the same status that RAISES
    ``GraphRestoreSilentError`` when the restore overran, so a missing exception is not
    evidence of a measurement — ``observation.measured`` is. A run whose RESTORE
    RAISED is published as ``FAILED``, and that observation is attached to the
    caller's own error rather than replacing it (see ``Raises``): its account is the
    one that says the graph key was ALREADY DELETED (#8827 review).

    ``bound_s`` bounds the ``restore`` call, NOT ``dump``/``delete``: those two run
    inline on the caller's thread here, before the worker starts, so their only limit
    is the caller's own client socket timeout — give it one below ``bound_s``. The
    split is reported rather than hidden (``elapsed_s`` = caller-held time for the whole
    sequence, ``restore_s`` = the bounded step).

    ``listening`` is the BLOCKED-vs-DEAD discriminator, not a refinement of the blocked
    message. Omit it and an overrun publishes ``LIVENESS_UNMEASURED``: the block is
    measured from the witness, but a port that is refusing RIGHT NOW looks exactly like a
    blocked one, so the driver makes no liveness claim at all. Pass
    ``listening=lambda: tcp_is_listening(host, port)`` to get ``ALIVE_BUT_SILENT``
    (do not restart) or ``INSTANCE_GONE`` (restart) instead.

    Raises:
        GraphRestoreSilentError: the restore overran ``bound_s`` and the witness measured
            a block — ``ALIVE_BUT_SILENT`` when a probe accepted the port (blocked, not
            dead), ``LIVENESS_UNMEASURED`` when no probe was supplied (blocked-vs-dead
            NOT established). See the module docstring's table.
        GraphRestoreLostError: the restore overran ``bound_s`` and the port refused —
            the process died.
        GraphRestoreStalledError: the restore overran ``bound_s`` while EVERY witness
            PING answered — the instance was serving, so the stall is the caller's.
        BaseException: whatever ``restore`` itself raised, re-raised unchanged with
            the observation attached as a ``__notes__`` entry (its own error is
            already loud; this driver does not replace it). That observation's status
            is ``FAILED``: ``dump()``/``delete()`` already ran, so the graph key is
            gone and the payload is the only copy left.

    ``ping`` must own its OWN connection. The restore runs on a daemon worker thread
    and is ABANDONED at ``bound_s`` — never joined to completion — so the RESTORE call
    cannot hold the caller past the bound. The tail that follows is finite and named:
    ``witness_join_s`` (a witness blocked inside its own PING is released only when that
    PING is) plus whatever timeout the ``listening`` callable carries, plus — on a
    COMPLETED run — ``recovery_probe_s``, the bounded window in which the driver waits
    for the instance to answer once more (the recovery edge that makes the silence
    attributable; not part of ``bound_s``). So the tail AFTER ``DUMP``/``DEL`` is at most
    ``bound_s + recovery_probe_s + witness_join_s + probe_timeout`` — keep those small
    when the tail matters. ``elapsed_s`` is that tail PLUS the caller's own
    ``dump()``/``delete()`` time, so read it for the whole of the call.
    """
    if bound_s <= 0:
        raise ValueError(f"bound_s must be positive, got {bound_s!r}")

    witness = _Witness(ping, poll_interval_s)
    witness.start()
    # The caller's own clock starts BEFORE `dump()`: `elapsed_s` is how long the caller
    # was held by the whole sequence, which is strictly more than `bound_s` covers
    # (see the docstring). `restore_s` below is the bounded step.
    op_started = time.perf_counter()
    try:
        # Sample BEFORE `dump()`. `DUMP` is O(N) serialisation on the SAME serving
        # thread the issue is about, so a block that began during DUMP was
        # previously unwitnessed and indistinguishable from a quiet instance — the
        # PR's own thesis failing in its own first step (#8827 review).
        payload = dump()
        payload_bytes = (
            len(payload) if isinstance(payload, (bytes, bytearray, memoryview)) else 0
        )
        delete()
    except BaseException:
        witness.finish(deadline_s=witness_join_s, recovery_s=0.0)
        raise

    outcome: dict[str, Any] = {}

    def _run_restore() -> None:
        try:
            outcome["value"] = restore(payload)
        except BaseException as exc:  # re-raised on the caller's thread
            outcome["error"] = exc

    started = time.perf_counter()
    worker = threading.Thread(target=_run_restore, daemon=True, name="graph-key-restore")
    try:
        worker.start()
    except BaseException:
        # If the thread cannot be created the witness must still be halted: it is a
        # daemon PINGing the live instance every `poll_interval_s` for the rest of
        # the process (#8827 review).
        witness.finish(deadline_s=witness_join_s, recovery_s=0.0)
        raise
    worker.join(bound_s)
    restore_s = time.perf_counter() - started
    completed = not worker.is_alive()
    # Boundary in WITNESS time: the restore is over here, so samples taken after
    # this point (the recovery probe) describe liveness, not attribution. Counting
    # them made an instant restore followed by one slow PING report "BLOCKED the
    # instance for 405 ms of its 0.00s" (#8827 review).
    window_end_s = time.perf_counter() - witness.t0

    # `recovery_probe_s` only on the completed path: when the restore overran, the
    # instance is (by definition) not answering, and the liveness probe below is the
    # evidence — waiting for a recovery edge there would only extend the caller's bound.
    samples = witness.finish(
        deadline_s=witness_join_s,
        recovery_s=recovery_probe_s if completed else 0.0,
    )
    # `elapsed_s` is the caller-held time and is captured HERE, after the join and the
    # recovery probe, because those are time the caller also waited (#8827 review).
    elapsed_s = time.perf_counter() - op_started
    # Attribution uses only samples inside the operation window; the recovery probe
    # may still add samples afterwards, and those may not be blamed on the restore.
    attributed = [s for s in samples if s[0] <= window_end_s]
    # The BLOCK MAGNITUDE must come from the window too: `blocked_ms` is
    # `max(silent_ms, witness_max_latency_ms)`, so a silence that began after the
    # restore returned was still published as "BLOCKED the instance for 1006 ms of
    # its 0.15s" (#8827 review cycle 2). The END EDGE is allowed to be post-window
    # (the recovery probe is what measures it), but it is now derived from the SAME
    # silence run as the magnitude — a second independent maximum over the full list
    # let a longer post-window run donate its end to the in-window one, publishing a
    # "50 ms" silence that started at 0.10 s and ended at 2.00 s (#8827 review).
    silent_ms, silence_started_s, silence_ended_s = _longest_silence(attributed, samples)
    failures = sum(1 for _offset, answered, _latency in attributed if not answered)
    # Only ANSWERED samples carry a latency: an unanswered one's latency is the
    # witness client's own socket timeout, not a measurement of the block.
    answered_latencies = [lat for _o, answered, lat in attributed if answered]
    max_latency_ms = max(answered_latencies) if answered_latencies else 0.0
    blocked = failures > 0 or max_latency_ms >= blocked_latency_ms
    # A witness failure that is NOT transport-level is the instrument's own fault
    # (auth, DNS, a probe bug). Reporting that as "It is a BLOCKED server … Do not
    # retry" told the operator to act on a fabricated engine verdict, so the outage
    # is refused rather than blamed on the engine (#8827 review).
    # ⚠️ DECLARED RESIDUAL (#8827 review round 2, not fixed here): this accumulator
    # covers the witness's WHOLE life, while every other attribution number above is
    # computed from `attributed` (the in-window samples). With the corrected polarity
    # ("ANY non-transport name disqualifies") a non-transport failure recorded only
    # AFTER the restore returned — i.e. by the recovery probe — can therefore discard a
    # MEASURED in-window BLOCK and publish UNMEASURED. Snapshotting the names at
    # `window_end_s` was tried and REVERTED: on the completed path the recovery probe is
    # the sample source (an instant restore leaves a ~0 s window), so the snapshot
    # dropped the auth failure this polarity exists to surface, and it broke
    # `test_a_MIXED_witness_fault_is_not_published_as_a_trustworthy_block`. Separating
    # "evidence about the restore" from "evidence about liveness" across the recovery
    # edge is a change to the witness contract, not a line edit here.
    witness_errors = tuple(sorted(witness.unanswered_errors))
    # ONE shared helper, also used by `RestoreObservation.witness_fault`: the status the
    # caller switches on and the account it reads are the same claim, and while this test
    # was written out twice the two were free to disagree (#8827 review). The polarity is
    # "ANY non-transport failure disqualifies", never "the set is disjoint from
    # transport" — the latter read a MIXED fault as pure transport and published a
    # do-not-retry BLOCKED over the caller's auth error.
    instrument_failures = _instrument_failures(witness_errors)
    witness_fault = bool(instrument_failures)
    # A witness fault is not a block, and a run with no reading is not a green either.
    if witness_fault:
        blocked = False
    # `SERVED` is reserved for a MEASURED negative: a verdict that was never measured
    # must not be published as one (#8827 review). Both guards are here rather than in
    # the status expressions so the completed and overrun paths cannot drift apart.
    measured = bool(attributed) and not witness_fault
    unmeasured_reason: str | None = None
    if witness_fault:
        unmeasured_reason = (
            "the WITNESS failed on a NON-transport error "
            f"({', '.join(instrument_failures)}), so its silence measures the instrument, "
            "not the engine — this is NOT evidence of the #7846 blocked-engine "
            "signature"
        )
    elif not attributed:
        unmeasured_reason = (
            "the witness recorded NO samples inside the window (its own PING outlived "
            "the whole operation, which a socket timeout longer than "
            "``bound_s + witness_join_s`` does), so nothing was measured"
        )

    error = outcome.get("error")
    if completed:
        if error is not None:
            status = FAILED
        elif not measured:
            status = UNMEASURED
        elif blocked:
            status = BLOCKED
        else:
            status = SERVED
        observation = RestoreObservation(
            graph=graph,
            status=status,
            bound_s=bound_s,
            elapsed_s=elapsed_s,
            restore_s=restore_s,
            payload_bytes=payload_bytes,
            witness_samples=len(attributed),
            witness_failures=failures,
            witness_errors=witness_errors,
            silent_ms=silent_ms,
            silence_started_s=silence_started_s,
            silence_ended_s=silence_ended_s,
            witness_max_latency_ms=max_latency_ms,
            blocked_latency_ms=blocked_latency_ms,
            instance_listening=None,
            # #8827 review round 2: "returned" means the WORKER FINISHED inside the
            # bound. A RESTORE that RAISED did not return, but it did finish — and the
            # documented meaning of this flag is the returned-vs-gave-up split, which is
            # exactly what "the worker is not still running" answers. The status says
            # which of the two it was; this field must not be read as "the restore
            # succeeded" (``as_dict`` publishes both).
            restore_returned=True,
            error=(
                # The witness fault must SURVIVE into the account on this path: with an
                # error present `unmeasured_reason` used to be dropped, so a run that
                # both raised AND had a non-transport witness failure named the witness
                # nowhere (#8827 review round 2).
                #
                # ⛔ Scoped to `error is not None` and closed with `or None` (#8827 review
                # round 3): `unmeasured_reason` ALREADY names the non-transport failures,
                # so appending them on that arm named the same failure twice; and a
                # `"; ".join(())` is `""`, not `None` — so every healthy SERVED/BLOCKED run
                # published `error=""`, which a consumer testing `error is not None` (the
                # test the `str | None` annotation invites) reads as an error on a clean
                # restore. `as_dict()` publishes this field.
                    "; ".join(
                        part for part in (
                            f"{type(error).__name__}: {error}",
                            ("witness fault: " + ", ".join(instrument_failures)
                             if instrument_failures else None),
                        ) if part
                    ) or None
                    if error is not None
                    else unmeasured_reason
            ),
        )
        logger.info("graph-key restore finished: %s", observation.sentence())
        if error is not None:
            # Keep the caller's own error type; the observation becomes a note. The
            # ACCOUNT is attached too, because ``FAILED`` is the one status whose
            # consequence is a LOST GRAPH and its account is the only text that says the
            # key is already DELETED — reachable nowhere else once the caller's error is
            # re-raised (#8827 review).
            with contextlib.suppress(AttributeError):
                error.add_note(observation.sentence())
                error.add_note(observation.diagnosis())
            raise error
        if observation.status == UNMEASURED:
            logger.warning(
                "graph-key restore could NOT be measured (#7846 criterion 5 is not "
                "answered by this run): %s",
                observation.sentence(),
            )
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
    # `bool(probe)` would turn the tri-state probe's `None` ("could not measure")
    # back into `False` ("refused"), and `False` selects INSTANCE_GONE — telling the
    # operator to restart a healthy box. `None` must survive to the status decision.
    #
    # The probe is CALLER-supplied code and is called UNGUARDED before this fix (#8827
    # review): `listening=lambda: client.ping() is True` — the natural way to wire a
    # command-level probe — raises when the block it is meant to measure is happening,
    # and the raw exception escaped HERE, after `delete()` had already removed the graph
    # key and after the restore worker had been abandoned. The caller lost the typed
    # error and the observation was never published or logged. A probe that cannot
    # ANSWER is not a refusal: it is `instance_listening is None`, the driver's own
    # "could not measure", which selects LIVENESS_UNMEASURED and makes no death claim.
    liveness_error: str | None = None
    if listening is None:
        instance_listening: bool | None = None
    else:
        try:
            probe = listening()
        except Exception as exc:  # a probe that cannot answer is "not measured"
            instance_listening = None
            liveness_error = f"the liveness probe FAILED ({type(exc).__name__}: {exc})"
        else:
            instance_listening = probe if probe is None else bool(probe)
    # Re-measure HERE: the probe (and the witness join above) is time the caller spent
    # waiting for this answer, so `elapsed_s` must include it or the published
    # caller-held time is short by exactly the tail the overrun path adds (#8827
    # review).
    elapsed_s = time.perf_counter() - op_started
    # The order is evidence BEFORE inference (#8827 review): a REFUSED port is a
    # measurement in its own right and outranks "every PING answered", which is only
    # an inference about the polls that happened to be in window. With the inference
    # first, a process that died inside the last poll interval before the bound was
    # reported as ``CALLER_STALL`` — the one status whose message says "do NOT restart,
    # check the CALLER" — while ``instance_listening is False`` sat in the same
    # observation.
    if instance_listening is False:
        # Hardest evidence there is: nothing accepted the connection, so the process is
        # gone. Its message must say so, never "check the caller".
        status = INSTANCE_GONE
    elif not measured:
        # The instrument faulted, or the witness never got a reading: nothing can be
        # attributed, and neither is the block. (`CALLER_STALL` implies `measured`, so
        # this cannot swallow a real caller stall.)
        status = UNMEASURED
    elif not failures and max_latency_ms < blocked_latency_ms:
        # Every sample ANSWERED **and none was slow**: the instance really was serving,
        # so the overrun is the CALLER's. Both conditions are required: with zero samples
        # `not failures` is vacuously true ("every one of the 0 witness PINGs ANSWERED"),
        # and a slow ANSWERED PING is the block itself — a caller who follows this
        # module's own advice (witness timeout below ``bound_s``) lands exactly there, and
        # must not be told to check their client on an instance that was visibly not
        # serving (#8827 review).
        status = CALLER_STALL
    else:
        # The block is measured, but `instance_listening is None` means nothing looked at
        # the PROCESS. `ALIVE_BUT_SILENT` — and `GraphRestoreSilentError`'s contract —
        # asserts a live process, so publishing it here was a verdict over evidence that
        # was never taken, on the DEFAULT configuration (#8827 review). The status is its
        # own so the message can carry only what was measured.
        status = LIVENESS_UNMEASURED if instance_listening is None else ALIVE_BUT_SILENT
    overrun_reason = "restore did not return within the bound"
    if unmeasured_reason is not None and instance_listening is not False:
        overrun_reason = f"{overrun_reason}; {unmeasured_reason}"
    if liveness_error is not None:
        # The observation must say WHY liveness is missing, or the account has to claim
        # no probe was supplied about the probe the caller watched fail (#8827 review).
        overrun_reason = f"{overrun_reason}; {liveness_error}"
    observation = RestoreObservation(
        graph=graph,
        status=status,
        bound_s=bound_s,
        elapsed_s=elapsed_s,
        restore_s=restore_s,
        payload_bytes=payload_bytes,
        witness_samples=len(attributed),
        witness_failures=failures,
        witness_errors=witness_errors,
        silent_ms=silent_ms,
        silence_started_s=silence_started_s,
        silence_ended_s=silence_ended_s,
        witness_max_latency_ms=max_latency_ms,
        blocked_latency_ms=blocked_latency_ms,
        instance_listening=instance_listening,
        restore_returned=False,
        error=overrun_reason,
        liveness_error=liveness_error,
    )
    logger.error("graph-key restore overran its bound: %s", observation.sentence())
    if observation.status == CALLER_STALL:
        raise GraphRestoreStalledError(observation)
    if observation.status == INSTANCE_GONE:
        raise GraphRestoreLostError(observation)
    raise GraphRestoreSilentError(observation)
