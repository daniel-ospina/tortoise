"""#10893 — WHICH stage a read was in when the transport wait bound expired.

The defect this exists for is not the slowness, it is the **silence**. A breached
request has always come back with the same sentence —

    "The server's wait budget for this request was exceeded before a response
     was ready. The work may still complete on the server. …"

— and three facts the caller and the operator both need were never in it:

* **which stage** consumed the budget (the embedding encode? the graph round
  trip? the fusion? or no stage at all, because the event loop was blocked by
  some OTHER request's work while this one merely waited for its first
  instruction — the ``pre-handler`` answer),
* **how long the stages that DID finish took**, so a breach on a read whose
  legs were fine is visibly not this request's own retrieval,
* and therefore whether the fix belongs in the read path at all.

That missing attribution is why #10893 accumulated a dozen re-measurements that
each concluded a different thing ("search only", "the whole data plane", "the
query shape", "a BERT forward pass on the main thread") from the same error
text. Every one of those conclusions was reached by hand, from outside the
server, because the server did not say. This module makes it say.

⛔ **IT MEASURES; IT DOES NOT BOUND.** No deadline, no refusal, no policy lives
here. The bound is ``mcp_auth._TRANSPORT_WAIT_BOUND_S`` and its two seams
(``hosted_api.WaitBoundMiddleware`` for REST, ``mcp_server
._await_under_mcp_wait_bound`` for MCP tool calls). Feeding a measurement into
a control decision from here would put a second owner on the bound.

⛔ **UNBOUND IS THE CHEAP PATH, AND IT IS THE DEFAULT.** ``mark()`` reads a
``ContextVar`` whose default is ``None`` and returns. Every non-search call
(every write, every point read, every CLI/test invocation) pays one ContextVar
read per call site and nothing else — no allocation, no lock, no clock read.
That matters because the call sites sit in ``tortoise_fts_query``, which is on
the hot read path this issue is about; an instrument that costs latency while
measuring latency is the defect wearing a lab coat.

⛔ **THE COUNTER IS A SHARED MUTABLE OBJECT, NOT A CONTEXTVAR VALUE.** The
stages are marked on the thread that RUNS them — for ``/v1/search`` that is a
read-lane worker (``hosted_api._read_offload``), reached through a context COPY
(``monitoring.confine_context``). Writing the stage INTO the ContextVar from
that worker would mutate the worker's copy and never reach the middleware that
needs to read it at the deadline. So the ContextVar carries a *reference* to one
:class:`ReadStages` instance created on the request's own thread; the worker
mutates the object, and the middleware reads the object. The same reasoning the
``#4055`` load-owner token and the retrieval ledger's shared tally use.

⛔ **THE CLOCK IS ``time.monotonic``, THE BOUND'S OWN CLOCK.** The two numbers
this module reports end up next to the bound's elapsed time on one log line, and
a wall clock that can step (NTP, suspend) would publish a stage that outlived
the request that reported it. ``hosted_api.WaitBoundMiddleware`` and
``mcp_server._wait_bound_arrival`` both read ``time.monotonic``; so does this.
"""
from __future__ import annotations

import contextlib
import contextvars
import threading
import time

#: The vocabulary. ONE closed set — an emitted stage is always one of these, so a
#: dashboard grouping by stage cannot be split by a typo, and a test can assert
#: completeness without guessing at future names.
#:
#: ``unmarked`` is the DEFAULT, and its meaning is deliberately narrow: **no read
#: stage was recorded before the bound expired.** It is not a claim about where
#: the time went. That distinction is #10893 review finding F4: the timeline is
#: bound for ALL `data` traffic but only ``sdk.tortoise_fts_query`` marks anything,
#: so a breach on `/v1/points/{id}`, `/v1/context` or any other read route accrues
#: nothing — and publishing ``pre_handler`` for those would assert "the time went
#: to middleware / auth / the event loop", which is TRUE for a search that never
#: started and FALSE for a route that simply records no stages. One word cannot
#: mean both, so the unattributed case says only what is knowable.
#:
#: ⛔ The loop-blocked-before-the-read signal is NOT lost, it MOVED: once the read
#: does start, ``pre_handler`` appears in ``spent_ms()`` with its real duration, so
#: a breach during ``embed_get`` carrying ``pre_handler: 8100.0`` still says "8.1 s
#: went by before the read began". What is gone is the unsupported inference from
#: silence.
STAGE_UNMARKED = "unmarked"
#: A stage that COMPLETED before the read path started: the bound's own pre-SSE
#: cost — middleware, auth, org resolution, queueing for a worker slot — plus any
#: time the request spent waiting for the event loop. Only ever present in
#: ``spent_ms()``, never in ``current()``: it is by definition over by the time a
#: later stage opens.
STAGE_PRE_HANDLER = "pre_handler"
#: ``EmbeddingModel.get()`` — the lazy one-time load (or the wait for another
#: request's in-flight load). Separated from the encode because they are the two
#: halves a previous investigation conflated: a COLD LOAD is setup the
#: ``#4055`` exemption already excuses, an ENCODE is work this request owns.
STAGE_EMBED_GET = "embed_get"
#: ``model.encode([query])`` — the BERT forward pass. The only stage that is
#: pure CPU inside this process, and the one the ``loop_blocks`` breadcrumb in
#: ``/health`` named by file:line from outside.
STAGE_EMBED_ENCODE = "embed_encode"
#: ``degradation_chain`` — all three retrieval legs (fts / vector / structural)
#: plus whatever the graph answers. Deliberately ONE stage, not three: the legs
#: run in parallel under a per-leg deadline, so their wall times overlap and a
#: per-leg sum would report a number larger than the stage that contains it.
#: Per-leg attribution already exists on a different channel and for a different
#: question (``leg_trace``'s entries), and is not duplicated here.
STAGE_RETRIEVAL = "retrieval"
#: ``rrf_fusion`` — pure CPU over the candidate pool.
STAGE_FUSION = "fusion"
#: ``annotate_ep_batch`` — the EP breakdown decoration read.
STAGE_EP_ANNOTATION = "ep_annotation"
#: Every marked stage finished; the remaining time is response assembly. Also the
#: terminal stage, so a breach that lands here says "the work is done, the answer
#: is late" rather than blaming a stage that had already returned.
STAGE_RESPONSE = "response"

STAGES: tuple[str, ...] = (
    STAGE_UNMARKED,
    STAGE_PRE_HANDLER,
    STAGE_EMBED_GET,
    STAGE_EMBED_ENCODE,
    STAGE_RETRIEVAL,
    STAGE_FUSION,
    STAGE_EP_ANNOTATION,
    STAGE_RESPONSE,
)

#: ``frozenset`` companion for membership tests at a call site that validates an
#: inbound stage name (the analytics prop round-trip).
STAGE_SET = frozenset(STAGES)


class ReadStages:
    """The stage timeline of ONE read request, shared by reference.

    Constructed by a transport seam on the request's own thread, bound into the
    context (:func:`request_stages`) before the handler task is created, mutated
    by :func:`mark` wherever the read path passes a known boundary, and read by
    the same seam when its bound expires.
    """

    __slots__ = ("_armed", "_lock", "_spent", "_stage", "_stage_t0", "_started_at")

    def __init__(self, started_at: float | None = None) -> None:
        now = time.monotonic()
        #: The reference the bound's own elapsed time is measured from — the
        #: transport ARRIVAL time when the seam has one (it does), so
        #: ``sum(spent_ms)`` is directly comparable to ``latency_ms`` on the same
        #: breach record. A default of "now" keeps an off-HTTP caller working.
        self._started_at = now if started_at is None else started_at
        self._stage = STAGE_UNMARKED
        #: True once the read path has marked ANY stage. It is what makes
        #: ``unmarked`` (nothing recorded) distinguishable from ``pre_handler``
        #: (recorded, and the read had not started yet) — see STAGE_UNMARKED.
        self._armed = False
        self._stage_t0 = self._started_at
        self._spent: dict[str, float] = {}
        self._lock = threading.Lock()

    # ── writing (the read path) ─────────────────────────────────────────────

    def mark(self, stage: str) -> None:
        """Enter ``stage``, closing the one that was open.

        A re-mark of the stage already open is a no-op rather than a second
        entry: the same stage can legitimately be reached twice in one read
        (a dense EXPANSION pass re-enters retrieval), and folding it keeps
        ``spent()`` a per-stage total instead of a per-visit list — the question
        a breach asks is "how much of the budget went to the encode", not "how
        many times did we enter it".
        """
        if stage not in STAGE_SET:
            # A closed vocabulary is the point; an unknown name would publish as
            # a stage no reader groups on. Refuse quietly — this is an instrument
            # on the read path and must never raise into a request.
            return
        now = time.monotonic()
        with self._lock:
            if stage == self._stage:
                return
            if not self._armed:
                # The FIRST mark is what promotes this request from `unmarked`
                # to a timed read: everything so far belongs to `pre_handler`,
                # and from here on the timeline describes the read path.
                self._armed = True
                self._spent[STAGE_PRE_HANDLER] = (
                    self._spent.get(STAGE_PRE_HANDLER, 0.0)
                    + (now - self._stage_t0) * 1000.0)
            else:
                self._spent[self._stage] = (
                    self._spent.get(self._stage, 0.0)
                    + (now - self._stage_t0) * 1000.0)
            self._stage = stage
            self._stage_t0 = now

    # ── reading (the transport seam, at the deadline) ───────────────────────

    def current(self) -> str:
        """The stage that was OPEN when the bound expired.

        ``unmarked`` when the read path never marked anything — see
        :data:`STAGE_UNMARKED`: that is an absence of an observation, never a
        claim about where the time went.
        """
        with self._lock:
            return self._stage

    def spent_ms(self) -> dict[str, float]:
        """Completed stages and their milliseconds, rounded for publication.

        Only stages that FINISHED appear. The open stage is deliberately absent
        from this mapping and present in :meth:`current` instead: its elapsed
        time is not an observation, it is the incident.

        ``pre_handler`` is omitted when it accrued no time — a request whose
        first instruction was the read path should not publish a zero for a
        stage it never occupied.

        ⛔ FILTERED ON THE UNROUNDED VALUE. ``round(0.04, 1)`` is ``0.0``, and
        publishing a zero on a per-stage table would read as "this stage was
        entered and cost nothing" — a different claim from "this stage was not
        entered". The filter therefore asks whether the stage accrued ANY time,
        not whether that time survived rounding: on the breach records this
        serves (>= 10 s) a stage that rounds to ``0.0`` really did cost
        effectively nothing, and saying so is the truth.
        """
        with self._lock:
            spent = dict(self._spent)
        return {name: round(ms, 1) for name, ms in spent.items() if ms > 0.0}

    def snapshot(self) -> dict:
        """``{"stage": …, "stages_ms": {…}}`` — the TELEMETRY publication shape.

        One construction site, so the analytics props and the log line cannot
        drift into two shapes. ⛔ This is NOT the shape that goes on the wire —
        see :meth:`wire_snapshot` for why the refusal body is stricter.
        """
        return {"stage": self.current(), "stages_ms": self.spent_ms()}

    def wire_snapshot(self) -> dict | None:
        """The refusal-body shape, or ``None`` when there is nothing to say.

        ⛔ THE WIRE IS STRICTER THAN TELEMETRY, deliberately. ``unmarked`` is the
        honest answer for an operator ("no read stage was recorded — this budget
        did not go to any stage the server can name") and a useless one for a
        CALLER, which has nothing to act on. Three shipped tests pin a breached
        REST refusal's body to exactly ``{"detail": …}``, and those bodies come
        from routes that run no read path at all. Publishing
        ``{"stage": "unmarked", "stages_ms": {}}`` on them would be noise on a
        wire contract, so the wire carries the attribution only once the read
        actually started; telemetry and the log line carry it always.
        """
        if not self._armed:
            return None
        return self.snapshot()


#: The in-flight timeline. Default ``None`` is the unbindable cheap path — see
#: the module docstring: every non-search caller pays one read and nothing else.
_stages: contextvars.ContextVar[ReadStages | None] = contextvars.ContextVar(
    "tortoise_read_stages", default=None)


def current_stages() -> ReadStages | None:
    """The timeline bound to this request, or ``None`` when none is bound."""
    return _stages.get()


@contextlib.contextmanager
def request_stages(stages: ReadStages):
    """Bind ``stages`` for the duration of the block, restoring on exit.

    Entered by the transport seam AROUND the creation of the handler task, so
    the task — and every context-copying hand-off below it (``_read_offload``'s
    ``monitoring.confine_context``, ``asyncio.to_thread``) — inherits the
    reference. Exiting restores the previous value so a bound timeline cannot
    leak into the next request the same context serves.
    """
    token = _stages.set(stages)
    try:
        yield stages
    finally:
        _stages.reset(token)


def begin(started_at: float | None = None) -> ReadStages:
    """Construct a timeline without binding it (the seam binds it itself)."""
    return ReadStages(started_at)


def mark(stage: str) -> None:
    """Mark ``stage`` on the bound timeline; a NO-OP when nothing is bound.

    This is the only call the read path makes, and it must be safe to call from
    anywhere: an unbound process (a CLI, a unit test, an SDK user's own process)
    is the overwhelmingly common case and pays a ContextVar read.
    """
    stages = _stages.get()
    if stages is not None:
        stages.mark(stage)


def stage_view() -> dict | None:
    """``snapshot()`` of the bound timeline, or ``None`` when none is bound.

    The seams publish this only when it is not ``None``, so an unbounded request
    (stdio MCP, an in-process SDK call) keeps its refusal body byte-identical to
    the pre-#10893 shape. This is the TELEMETRY/log shape — the refusal BODY
    uses :func:`stage_wire_view`.
    """
    stages = _stages.get()
    return None if stages is None else stages.snapshot()


def stage_wire_view() -> dict | None:
    """The refusal-BODY shape: the attribution, or ``None`` when nothing ran.

    See :meth:`ReadStages.wire_snapshot` — ``unmarked`` with no accrued time
    means the bound expired before the read started, and a caller can act on
    that only as "retry", which it already knew.
    """
    stages = _stages.get()
    return None if stages is None else stages.wire_snapshot()


def log_suffix(view: dict | None) -> str:
    """The `` (stage=… spent_ms=…)`` tail both breach WARNINGs append.

    One construction site: the REST seam and the MCP seam log the same refusal,
    and two hand-rolled format strings is how they drift into reporting the same
    stall two different ways. ``""`` for an unbound timeline keeps both log
    lines byte-identical to the pre-#10893 shape.
    """
    if not view:
        return ""
    return f" (stage={view.get('stage')} spent_ms={view.get('stages_ms')})"
