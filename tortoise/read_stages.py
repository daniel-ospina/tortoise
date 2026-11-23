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
#: ``rrf_fusion`` — pure CPU over the candidate pool. ⛔ BRACKETS ONLY THE FUSION
#: CALL. The graph-bracketing reads that follow it (recency weights, kind /
#: status / claim filters, the echo fold, the rerank pool fetch) reopen
#: ``retrieval``, because they are graph round trips and a breach inside one is
#: retrieval work that ran late, never "the CPU fusion is stuck" (#10893 F1).
STAGE_FUSION = "fusion"
#: ``annotate_ep_batch`` — the EP breakdown decoration read.
#: ⛔ BRACKETS ONLY THE ANNOTATION READ (#13294). The step-7 batch content fetch
#: and the epistemic-state read that follow it reopen ``retrieval``: they are
#: graph round trips, and a breach inside one is retrieval work that ran late,
#: never "the annotation took ¬". Same rule as ``fusion`` (#10893 F1), and the
#: reason this stage closes at its own read rather than at the ordering pass.
STAGE_EP_ANNOTATION = "ep_annotation"
#: The post-retrieval ordering pass. On ``order_by="graph"`` this is
#: ``GraphRanker.rerank`` plus the W4 enrichment — real graph round trips, not
#: response assembly — and on ``order_by="confidence"`` the belief sort.
#: It exists as its own stage because #10893's review found the graph rerank
#: being charged to ``ep_annotation`` after that stage had already returned:
#: the rerank reads the graph, so a breach inside it is retrieval work that
#: happened to run late, never "the annotation took ¬".
STAGE_ORDERING = "ordering"
#: Every marked stage finished; the remaining time is response assembly. Also the
#: terminal stage, so a breach that lands here says "the work is done, the answer
#: is late" rather than blaming a stage that had already returned. It must be
#: reached on EVERY return path — a path that skips it publishes the last real
#: stage as the cause of a breach that happened after it finished.
STAGE_RESPONSE = "response"

#: #14232 — the CAPTURE path's stages (`POST /v1/sessions`), declared beside the
#: read path's because they share ONE timeline and ONE refusal line.
#:
#: They exist because that refusal said `stage=unmarked spent_ms={}` in
#: production: the capture ran the whole model call inside the request and
#: nothing on the path marked a boundary, so a breach could not name its cause.
#: The split those marks serve (ack + completion) is #14232's; the marks are what
#: makes the NEXT breach legible.
#:
#: ``capture_gates`` — admission, consent, quota/estimate, rate window, spend cap
#: (before any write). ``capture_write`` — the record: the `:Session` MERGE + turn
#: `:Point`s, the sessionCaptured Event mint and the `:Source` materialization.
#: ``capture_extract`` — the model call on a capture-pool worker.
#: ``capture_tail`` — what the extraction makes reportable: the provenance stamp,
#: the entity-link pass, the receipt write and the `capture_ok` record.
#:
#: ⛔ The first two are the ones a 504 can still blame: after #14232 the response
#: is bounded by the ack deadline, so a breach can only land in the record phase.
#: The last two are marked anyway — they are where the budget goes, they publish
#: in `spent_ms` on any breach that overlaps them, and an instrument with holes in
#: it is how `unmarked` happened in the first place.
STAGE_CAPTURE_GATES = "capture_gates"
STAGE_CAPTURE_WRITE = "capture_write"
STAGE_CAPTURE_EXTRACT = "capture_extract"
STAGE_CAPTURE_TAIL = "capture_tail"

STAGES: tuple[str, ...] = (
    STAGE_UNMARKED,
    STAGE_PRE_HANDLER,
    STAGE_EMBED_GET,
    STAGE_EMBED_ENCODE,
    STAGE_RETRIEVAL,
    STAGE_FUSION,
    STAGE_EP_ANNOTATION,
    STAGE_ORDERING,
    STAGE_RESPONSE,
    # ── #14232: the CAPTURE path's stages ────────────────────────────────
    # Appended AFTER the read path's terminal stage on purpose: `response` ends
    # the read timeline and the vocabulary test pins the read ORDER
    # (`ep_annotation < ordering < response`), so a capture stage inserted above
    # would silently re-order a timeline it does not belong to. ONE vocabulary,
    # two surfaces — the refusal's log suffix and its analytics prop are both
    # gated on `STAGE_SET`, so a second timeline could not publish at all. That
    # is why the capture path marks HERE.
    #
    # `unmarked` keeps its narrow meaning for this path too: a capture breach
    # that named no stage is an absence of measurement, never a claim that the
    # bound was spent before the handler ran.
    STAGE_CAPTURE_GATES,
    STAGE_CAPTURE_WRITE,
    STAGE_CAPTURE_EXTRACT,
    STAGE_CAPTURE_TAIL,
)

#: ``frozenset`` companion for membership tests at a call site that validates an
#: inbound stage name (the analytics prop round-trip).
STAGE_SET = frozenset(STAGES)

#: The names in :data:`STAGES` that a caller may never MARK — they are DERIVED
#: states this object computes, not stages a read path enters.
#:
#: ⛔ WHY THIS IS A GUARD AND NOT A CONVENTION (#14086 review F5 / round-2 F4).
#: ``mark()``'s closed-vocabulary check accepts anything in :data:`STAGE_SET`, and
#: both of these are in it because readers group on them and ``spent()`` /
#: ``snapshot()`` publish them. But marking either produces a state the rest of
#: the module says is impossible:
#:
#: * ``pre_handler`` — a first ``mark("pre_handler")`` arms and banks the
#:   interval correctly, but leaves ``_stage == "pre_handler"``, so the NEXT mark
#:   takes ``mark()``'s ``else`` branch and banks a SECOND ``pre_handler``
#:   interval that no part covers: ``sum(parts) < pre_handler`` while
#:   ``snapshot()`` publishes both as if they reconciled.
#: * ``unmarked`` — its own docstring defines it as "no read stage was recorded",
#:   and ``snapshot()`` publishes it as such; on an ARMED timeline a
#:   ``mark("unmarked")`` closes the open stage and makes ``current()`` report
#:   "unmarked" for a request whose stages were measured, so a breached caller's
#:   body would state an unsupported inference.
#:
#: Refusing here is one frozenset membership test on a path that already does
#: one, and it costs nothing: nothing in the tree marks either name (measured:
#: every ``_mark_read_stage`` call site uses a real stage).
_NOT_MARKABLE = frozenset({STAGE_UNMARKED, STAGE_PRE_HANDLER})
#: The decomposition of ``pre_handler`` — a SECOND closed vocabulary, disjoint
#: from :data:`STAGES` on purpose.
#:
#: ⛔ WHY THIS EXISTS (#10893, second cycle). ``pre_handler`` is ONE number, and
#: one number cannot be acted on. Measured on the production box after the first
#: cycle: ``pre_handler 5996.5`` — six seconds before ``/v1/search``'s handler
#: executed — and three different mechanism classes produce exactly that number:
#:
#: * the event loop was SATURATED by another request's synchronous work, so this
#:   request's task did not get scheduled (the ``#2988``/``#3498`` class);
#: * the credential/org dependency stack was slow (an awaited JWKS or control
#:   plane call — real work, not queueing);
#: * the handler itself was waiting for a READ-LANE worker (the ``#13528``
#:   shape, where eight workers parked in ``EmbeddingModel.get()`` made reading
#:   requests "pay the wait a second time before their first stage mark").
#:
#: Each needs the OPPOSITE fix (offload the blocking work / repoint the
#: dependency / widen or re-arm the lane), so the attribution must be a
#: measurement rather than an inference from the total. The parts below are the
#: boundaries this process can honestly observe; they sum to ``pre_handler``
#: exactly, because both are accumulated from the same cursor.
#:
#: ⛔ A PART IS CLOSED BY :func:`note`, AND THE INTERVAL IT CLOSES IS THE ONE
#: BEFORE IT — ``note("sdk_open")`` says "everything up to now belongs to the
#: SDK open", never "the SDK open starts now". That direction is the contract:
#: a note is taken at the END of the interval it names, from the call site that
#: just finished the work.
#:
#: ⛔ PARTS ARE TELEMETRY ONLY, never wire. A caller cannot act on "4 s of my
#: budget was spent before my handler ran" — only an operator can — so
#: :meth:`ReadStages.wire_snapshot` still publishes the same keys (it DROPS
#: ``pre_handler_parts`` before returning — that one line is what keeps the
#: decomposition off the wire, so it must never be "simplified" back to
#: ``return self.snapshot()``) and a breached caller's body stays byte-identical.
PRE_HANDLER_PARTS: tuple[str, ...] = (
    #: Arrival → the handler task created. ⛔ This is the OUTERMOST transport
    #: middleware's own preamble, plus any time this request spent waiting for
    #: the loop BEFORE its task existed — NOT the inner middleware chain. Those
    #: middlewares run INSIDE the created task (``WaitBoundMiddleware`` is
    #: registered LAST, so it is OUTERMOST and wraps the rest), which is why
    #: their time lands in ``dispatch`` (measured, #14086 review round 2).
    "middleware",
    #: Task created → the handler's first statement. ⛔ THIS INCLUDES THE
    #: REMAINING MIDDLEWARE CHAIN (inner middlewares execute in the task, after
    #: the ``middleware`` note), plus the queue for the event loop and the
    #: credential/organisation dependency stack — this is the part that grows
    #: when the loop is blocked by another request's work.
    "dispatch",
    #: ``_data_sdk_offloaded`` — the graph lane's SDK open and, where the route
    #: asks for it, the cold projection attach (TCP/TLS/``Is_Sentinel``).
    "sdk_open",
    #: The handler's own work between the SDK open and the read-lane hand-off.
    "read_submit",
    #: The read-lane hand-off → the read path's FIRST mark: the wait for a
    #: worker slot (``#13528``'s starvation shape) plus the worker's own
    #: start-up. Only ever present when ``read_submit`` was noted, so it can
    #: never be filled from an unrelated interval.
    "read_lane",
    #: The remainder when the last note was NOT the read hand-off — time before
    #: the read started that no call site accounted for. Kept as its own name so
    #: the table never claims an attribution it did not measure.
    "other",
)

#: ``frozenset`` companion — the part vocabulary is closed for the same reason
#: :data:`STAGE_SET` is: an unknown name must never publish as a part no reader
#: groups on.
PRE_HANDLER_PART_SET = frozenset(PRE_HANDLER_PARTS)


def _published(values: dict[str, float]) -> dict[str, float]:
    """Round a reading for publication, omitting entries that accrued no time.

    ONE definition, because both of its rules are load-bearing and they were
    previously spelled twice (``spent_ms`` and ``parts_ms``) — the drift risk
    this module's "one accumulation" rule exists to remove:

    * ROUND to 0.1 ms for the wire, and
    * FILTER ON THE UNROUNDED VALUE. ``round(0.04, 1)`` is ``0.0``, so filtering
      the rounded number would hide an entry that measured non-zero, which is
      the opposite error from publishing a phantom zero. An entry that accrued
      SOME time and still rounds to ``0.0`` is published as ``0.0``: on the
      >= 10 s records this table serves, that really did cost nothing.
    """
    return {name: round(ms, 1) for name, ms in values.items() if ms > 0.0}


class ReadStages:
    """The stage timeline of ONE read request, shared by reference.

    Constructed by a transport seam on the request's own thread, bound into the
    context (:func:`request_stages`) before the handler task is created, mutated
    by :func:`mark` wherever the read path passes a known boundary, and read by
    the same seam when its bound expires.
    """

    __slots__ = ("_armed", "_last_part", "_lock", "_parts", "_parts_t0",
                 "_spent", "_stage", "_stage_t0", "_started_at")

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
        #: The ``pre_handler`` decomposition (#10893 second cycle): the cursor
        #: the open interval is measured from, the closed parts so far, and the
        #: part the last note named — the last one is what decides whether the
        #: remainder at the first mark may be called ``read_lane``.
        self._parts_t0 = self._started_at
        self._parts: dict[str, float] = {}
        self._last_part: str | None = None

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
        if stage in _NOT_MARKABLE:
            # ⛔ `pre_handler` is DERIVED, never marked: it is the interval from
            # arrival to the first mark, closed by the arming branch below. It is
            # in `STAGES` (readers group on it, and `spent()` and the breach row
            # publish it), so the guard above ACCEPTS the name — and marking it
            # would break the one invariant this decomposition rests on. A first
            # `mark("pre_handler")` arms and banks the interval correctly, but it
            # then leaves `_stage == "pre_handler"`, so the NEXT mark takes the
            # `else` branch and adds a SECOND interval to `_spent["pre_handler"]`
            # that no part covers: `sum(parts) < pre_handler` while `snapshot()`
            # publishes both as if they reconciled. Nothing calls it today, which
            # is exactly why it is worth one line — the "parts sum by
            # construction" claim should not depend on nobody ever typing it
            # (#14086 review F5).
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
                # …and the SAME interval closes the parts cursor, so the parts
                # sum to `pre_handler` by construction rather than by a test
                # that reconciles two independently accumulated numbers.
                self._close_part_unlocked(
                    "read_lane" if self._last_part == "read_submit" else "other",
                    now)
            else:
                self._spent[self._stage] = (
                    self._spent.get(self._stage, 0.0)
                    + (now - self._stage_t0) * 1000.0)
            self._stage = stage
            self._stage_t0 = now

    def note(self, part: str) -> None:
        """Attribute the interval that JUST ENDED to ``part`` (#10893).

        Taken at the END of the interval it names — ``note("dispatch")`` means
        "everything since the previous note (or since arrival) belongs to the
        dispatch wait".

        A no-op once the read path has started: ``pre_handler`` is closed at the
        first :func:`mark`, so a note landing later would attribute read-path
        time to a pre-handler part. A no-op for an unknown name (closed
        vocabulary, the same rule as :func:`mark`) and for an unbound process —
        this is an instrument on the request path and must never raise.
        """
        if part not in PRE_HANDLER_PART_SET:
            return
        now = time.monotonic()
        with self._lock:
            if self._armed:
                return
            self._close_part_unlocked(part, now)
            self._last_part = part

    def _close_part_unlocked(self, part: str, now: float) -> None:
        """Move the parts cursor to ``now``, banking the interval on ``part``.

        Caller holds ``self._lock``. Shared by :meth:`note` (the named
        boundaries) and the first :func:`mark` (the remainder), so the two can
        never disagree about where the cursor is.
        """
        self._parts[part] = (self._parts.get(part, 0.0)
                             + (now - self._parts_t0) * 1000.0)
        self._parts_t0 = now

    def parts_ms(self) -> dict[str, float]:
        """The ``pre_handler`` decomposition, rounded, zero-parts omitted.

        Empty until the read path has started: before that the interval is
        still OPEN, and publishing a partial sum under a total it does not equal
        is the same false attribution :data:`PRE_HANDLER_PARTS` exists to
        prevent.

        Same filter rule as :meth:`spent_ms`, and the SAME conclusion:
        ``round(0.04, 1)`` is ``0.0``, so the filter asks whether the part
        accrued ANY time rather than whether that time survived rounding — a part
        under 0.05 ms (the band that rounds to ``0.0``) IS published as ``0.0``,
        and that is deliberate. It is the honest reading here for the same reason
        it is in ``spent_ms``: the records this table serves are >= 10 s breaches,
        where a part that rounds to ``0.0`` really did cost effectively nothing.
        (Filtering the ROUNDED value instead would hide a part that measured
        non-zero, which is the opposite error.)
        """
        with self._lock:
            if not self._armed:
                return {}
            parts = dict(self._parts)
        return _published(parts)

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
        return _published(spent)

    def snapshot(self) -> dict:
        """``{"stage": …, "stages_ms": {…}}`` — the TELEMETRY publication shape.

        One construction site, so the analytics props and the log line cannot
        drift into two shapes. ⛔ This is NOT the shape that goes on the wire —
        see :meth:`wire_snapshot` for why the refusal body is stricter.

        ⛔ ONE LOCK ACQUISITION, not three (#14086 review, P2). Composing
        :meth:`current` + :meth:`spent_ms` + :meth:`parts_ms` took ``_lock`` once
        per accessor, so a ``mark()`` landing between the second and the third
        call published ``pre_handler_parts`` under a ``stages_ms`` that did not
        contain ``pre_handler`` — a decomposition with no total, built from
        three readings of one timeline that were never one observation. The
        three readings below are taken under a SINGLE acquisition; the
        accessors keep their own for callers that want one reading only.
        """
        with self._lock:
            stage = self._stage
            spent = _published(self._spent)
            parts = _published(self._parts) if self._armed else {}
        view = {"stage": stage, "stages_ms": spent}
        if parts:
            # #10893 second cycle: the decomposition rides the SAME snapshot, so
            # the analytics row and the breach log line carry one shape. ABSENT
            # (not empty) when nothing was noted — an empty table would read as
            # "measured, cost nothing" rather than "no boundary was observed".
            view["pre_handler_parts"] = parts
        return view

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
        view = self.snapshot()
        # #10893 second cycle: the decomposition is TELEMETRY ONLY. The wire is a
        # caller-facing contract and a caller can act on neither "time went by
        # before my handler ran" nor any other part, so publishing one would be
        # noise on a wire contract that three shipped tests pin byte for byte.
        view.pop("pre_handler_parts", None)
        return view


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


def note(part: str) -> None:
    """Note a ``pre_handler`` boundary on the bound timeline (#10893).

    The module-level twin of :func:`mark` — safe to call from anywhere, a no-op
    when nothing is bound, and it never raises. See :meth:`ReadStages.note` for
    the direction of the interval it closes.
    """
    stages = _stages.get()
    if stages is not None:
        stages.note(part)


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
    # #10893 second cycle: the `pre_handler` decomposition rides the same tail.
    # A six-second `pre_handler` is otherwise one opaque number, and the three
    # mechanisms that produce it need opposite fixes.
    parts = view.get("pre_handler_parts")
    suffix = "" if not parts else f" pre_handler_parts={parts}"
    return (f" (stage={view.get('stage')} spent_ms={view.get('stages_ms')}"
            f"{suffix})")
