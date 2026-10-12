"""Per-org RETRIEVAL work measurement (#4489).

WHY THIS EXISTS. Reads are free by DECISION (``product/pricing.json`` →
``billing.reads_free: true``), so the engine cost of answering a query had no
meter at all — the only per-org dimension was write-ops, and a read-heavy org
and an idle one were indistinguishable. This module measures retrieval work
(queries, wall time, and WHICH legs ran) on the SAME durable per-(org, period)
ledger the ask/capture/embedding lanes use, so the reading needs no new store
and no new read path.

WHAT IT IS NOT. It records WORKLOAD, never price, tier or quota (#4489's
explicit out-of-scope list: "Any price level, tier boundary, or read quota").
Nothing here can refuse, throttle or bill. The figure is descriptive.

WHERE IT IS READ. ``metering.get_retrieval_usage(org_id)`` directly, and — on the
product surface — rendered on the existing ``GET /v1/team`` response next to
``write_ops_used``, which is what makes #4489 indicator 3 (distinguish a
read-heavy org from a write-heavy one) a read of ONE row rather than a diff of
two systems.

SHAPE. A mutable :class:`RetrievalTally` lives in a :class:`contextvars.ContextVar`.
The read funnel — :meth:`tortoise.sdk.TortoiseSDK.tortoise_fts_query`, the ONE
entry point every product read surface funnels through (``GET /v1/search``, the
MCP recall tools, the ask lane, ``volunteer_context``) — MUTATES it in place:
O(1), no I/O, no network round trip. An ARMED note takes ONE short-lived
module-global mutex (:data:`_LOCK`) around the integer bump — the snapshot in
:func:`flush_tally` must observe a tally that no pool thread is editing — so
armed notes on one process serialize on that mutex, never on I/O. The UNARMED
path (every non-product caller) is a single ``ContextVar.get()`` and no lock at
all. A WORK-OWNING boundary arms / notes, then takes-and-resets the tally and
writes it once through ``metering.record_retrieval_usage``:

* :class:`RetrievalMeteringMiddleware` — every HTTP request on the hosted app,
  REST and the mounted MCP app alike. The org is resolved AT FLUSH TIME from
  ``scope["state"]["org_id"]`` (the same dict the auth dependencies write into,
  and the dict ``ComputeAttributionMiddleware`` reads for #4490), never from a
  client-supplied header or query param.

WHY A TALLY RATHER THAN A WRITE PER QUERY. The ledger write is a blocking RPC.
One per query would put a network round-trip on every read — and on the MCP lane
that write is on the event loop (the #4451 class). A tally makes the read path
an in-place integer bump and moves the single write to a boundary that flushes
OFF the loop. #4488's encode lane chose the same shape for the same reason; the
parent (#5045 §"Write-path non-interference") asks for exactly this bound.

WHY A MUTABLE TALLY RATHER THAN A COPY-ON-WRITE VALUE. The read funnel can run in
a pool thread whose context was copied (``hosted_api``'s ``asyncio.to_thread``
for ``GET /v1/search``, and the same pattern elsewhere). A ContextVar set in the
request context is visible to that copy, but a REBIND (``set``) inside the worker
is not propagated back — mutating a shared object IS. Same reasoning as the
sibling ``embed_metering`` tally (PR #5345) and the ``graph_ops`` counter (#5292).

TOTAL, NEVER RAISES. ``note_query`` / ``bind_org`` are total: an allocation fault
on the measurement path must never fail a read. ``flush`` absorbs every failure
and reports the drops it can DETECT — a **window-unresolvable** window and a
non-empty tally with **no resolvable org** — to the operator through
``metering.report_unmetered_increment`` (this module is the ``retrieval`` lane).
A failure of the increment RPC ITSELF is logged at WARNING inside
``metering.record_retrieval_usage`` and is the shared #3824 residual; a note
landing after the tally was consumed is the same cancellation residual the embed
lane declares.

MEASURED VS EXCLUDED — stated, not implied:

* MEASURED: the wall time of the retrieval call itself (every leg plus the RRF
  fusion), noted once per query at the read funnel's exit, and each leg execution
  noted by the leg helper that ran it. The leg names are the RECORDED vocabulary
  (``fts`` / ``vector`` / ``structural``, see ``search_engine._trace_entry``) —
  this module mints none, and a name it does not know (e.g. the ``fallback``
  entry a degraded read appends) advances the leg TOTAL and no per-leg column, so
  an unknown leg can never grow the row.
* The legs are noted from ``search_engine``'s per-leg ``_record``, NOT read back
  from the caller's leg trace. The trace is OPT-IN, and its mere presence
  switches on extra declaration work inside the vector leg (the #4199/#5287
  scope probe), so a measurement that required it would either record no legs on
  the default product path or make every read pay for a declaration it never
  asked for. The product funnel passes NO trace, so ``note_leg`` is the ONLY
  leg source there and a leg can never be counted twice;
  :meth:`RetrievalTally.note_query`'s optional ``legs`` argument exists only for
  a caller that ALREADY holds a trace, and must not be combined with the per-leg
  hooks on the same tally (that would double-count ``leg_runs``).
* EXCLUDED, and why: the query ENCODE (it is the embedding lane's figure, #4488);
  post-retrieval annotation / dedup / assembly (not retrieval); writes and
  background extraction reads (the write lanes' figures — this dimension is the
  cost base behind the READS promise); and any read served by a store that could
  not be opened, which fails before a leg runs and is therefore not a query this
  lane ever served.
* NOT ATTRIBUTED: an MCP (``/mcp``) request, whose org lives in the
  ``mcp_auth`` ContextVar this ASGI layer cannot see. Its retrieval is measured,
  then DROPPED rather than filed under an invented org: :func:`flush_tally`
  writes no row and reports the drop to the operator on the ``retrieval`` lane
  when a non-empty tally has no resolvable org. Nothing is written to an
  unattributed (``""``) row — there is no such row. This is the same documented
  limit the sibling compute (#4490) and egress (#4491) dimensions carry; a
  future MCP dispatch seam that binds the org removes it.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass, field

_logger = logging.getLogger(__name__)

#: The three legs of the RECORDED leg-trace vocabulary that carry a per-leg
#: ledger column. Anything else is folded into the totals (see the module
#: docstring) — the row shape is fixed, so a new leg needs a migration, not a
#: silently growing label set.
_KNOWN_LEGS: tuple[str, ...] = ("fts", "vector", "structural")

#: The active tally for the current context, or None outside any boundary.
#: ``default=None`` keeps the unarmed path to one cheap ``get()`` per query.
_ACTIVE: ContextVar[RetrievalTally | None] = ContextVar(
    "tortoise_retrieval_tally", default=None
)

#: Guards the tally's consumed transition and its snapshot (one critical
#: section), and the take-and-reset. Never held across the ledger RPC.
_LOCK = threading.Lock()


@dataclass
class RetrievalTally:
    """One work unit's retrieval tally. Mutable by design (see the docstring)."""

    queries: int = 0
    wall_ms: float = 0.0
    leg_runs: int = 0
    leg_degraded: int = 0
    #: Per-leg counts for the KNOWN leg names only.
    legs: dict[str, int] = field(default_factory=dict)
    org_id: str | None = None
    #: Set by :func:`flush_tally` — a second flush of the same object is a
    #: no-op (two boundaries in one request record once).
    consumed: bool = False

    def note_query(self, *, wall_ms: float, legs: object = None) -> None:
        """Accumulate ONE retrieval query and the legs its trace recorded.

        Total — never raises. ``legs`` is the leg trace (a sequence of the
        ``search_engine._trace_entry`` dicts); anything that is not a mapping is
        ignored rather than trusted. A leg entry counts as a RUN when its
        ``ran`` is truthy and as DEGRADED when its ``degraded`` is truthy — the
        trace's own two axes, read as recorded, never re-derived.

        The mutation holds :data:`_LOCK`: the snapshot in :func:`flush_tally`
        depends on the counters being read as one section, or a pool thread
        still running at boundary time could be observed mid-increment.
        """
        with _LOCK:
            self.queries += 1
            self.wall_ms += max(0.0, float(wall_ms))
            if legs is None:
                return
            try:
                entries = list(legs)  # type: ignore[call-overload]
            except TypeError:
                return
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                if entry.get("ran"):
                    self.leg_runs += 1
                    leg = entry.get("leg")
                    if isinstance(leg, str) and leg in _KNOWN_LEGS:
                        self.legs[leg] = self.legs.get(leg, 0) + 1
                if entry.get("degraded"):
                    self.leg_degraded += 1

    def note_leg(self, leg: str, *, ran: bool, degraded: bool) -> None:
        """Accumulate ONE leg execution. Total — never raises.

        Called by the leg helpers themselves (``search_engine``'s per-leg
        ``_record``), NOT from the caller's leg trace — the trace is OPT-IN and
        its presence switches on extra declaration work (the #4199/#5287 scope
        probe), so a measurement that required it would either measure nothing on
        the default product path or make every read pay for a declaration it did
        not ask for. The leg name is the recorded vocabulary; a name outside it
        advances the total and no per-leg counter (the bounded fold).
        """
        with _LOCK:
            if ran:
                self.leg_runs += 1
                if leg in _KNOWN_LEGS:
                    self.legs[leg] = self.legs.get(leg, 0) + 1
            if degraded:
                self.leg_degraded += 1

    def bind_org(self, org_id: str | None) -> None:
        """Attach the org this work belongs to. No-op for a falsy id."""
        if org_id:
            with _LOCK:
                self.org_id = org_id

    def is_empty(self) -> bool:
        """True when this tally carries nothing worth a ledger row."""
        return not (self.queries or self.leg_runs or self.leg_degraded)


def arm(org_id: str | None = None) -> RetrievalTally:
    """Install (once) and return the active tally; optionally bind the org.

    Idempotent: a second call in the same context returns the SAME tally rather
    than starting a fresh one (two boundaries in one request share one figure).
    """
    tally = _ACTIVE.get()
    if tally is None:
        tally = RetrievalTally()
        _ACTIVE.set(tally)
    if org_id:
        tally.bind_org(org_id)
    return tally


def current_tally() -> RetrievalTally | None:
    """The active tally, or None when unarmed (tests/diagnostics)."""
    return _ACTIVE.get()


def bind_org(org_id: str | None) -> None:
    """Bind *org_id* to the active tally. Total; no-op when unarmed or falsy."""
    tally = _ACTIVE.get()
    if tally is not None and org_id:
        tally.bind_org(org_id)


def note_query(*, wall_ms: float, legs: object = None) -> None:
    """Note one retrieval query against the active tally. Unarmed → no-op."""
    tally = _ACTIVE.get()
    if tally is not None:
        try:
            tally.note_query(wall_ms=wall_ms, legs=legs)
        except Exception:  # noqa: BLE001, RUF100 — measurement never fails a read
            _logger.debug("retrieval tally note failed", exc_info=True)


def note_leg(leg: str, *, ran: bool, degraded: bool) -> None:
    """Note one leg execution against the active tally. Unarmed → no-op.

    The hot-path form: one ``ContextVar.get()`` and an in-place integer bump —
    no lock when unarmed, no I/O, never raises.
    """
    tally = _ACTIVE.get()
    if tally is not None:
        try:
            tally.note_leg(leg, ran=ran, degraded=degraded)
        except Exception:  # noqa: BLE001, RUF100 — measurement never fails a read
            _logger.debug("retrieval leg note failed", exc_info=True)


def metered_query(fn):
    """Wrap a read ENTRY POINT so ONE call measures ONE retrieval query.

    Applied to ``TortoiseSDK.tortoise_fts_query`` — the one funnel every product
    read surface goes through — so the query count and its wall time are
    recorded at exactly the boundary the docstring names, with no caller
    cooperation and no per-call-site bookkeeping. The wrapper is TOTAL: the
    measurement sits in a ``finally`` and can never change the read's result or
    mask its exception.

    The UNARMED path (no tally in this context — every non-product caller)
    returns ``fn``'s result after one ``ContextVar.get()`` and takes no clock
    reading, so a default deployment pays one dict-free lookup per read.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if _ACTIVE.get() is None:
            return fn(*args, **kwargs)
        started = time.monotonic()
        try:
            return fn(*args, **kwargs)
        finally:
            note_query(wall_ms=(time.monotonic() - started) * 1000.0)

    return wrapper


def bind_worker_context(fn):
    """Wrap ``fn`` so a WORKER THREAD runs it under this thread's retrieval tally.

    ContextVars do not propagate to a new thread in CPython, and
    ``search_engine.degradation_chain`` runs the retrieval legs in a
    ``ThreadPoolExecutor`` — so without this the legs would note their work into
    a context that has no tally and the leg half of the figure would be
    permanently, silently zero (the exact failure mode
    ``graph_ops.bind_metering_context`` documents for graph ops, PR-pinned there
    by measurement).

    Only the tally is installed in the worker, never the caller's whole context:
    the legs' behaviour must not start depending on unrelated contextvars. The
    tally is a mutable OBJECT shared by reference, so the worker's increments
    land in the caller's tally. On the common path (no tally armed — every
    non-product caller) ``_ACTIVE`` is unset and ``fn`` is returned UNWRAPPED,
    so nothing changes.
    """
    tally = _ACTIVE.get()
    if tally is None:
        return fn

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        token = _ACTIVE.set(tally)
        try:
            return fn(*args, **kwargs)
        finally:
            _ACTIVE.reset(token)

    return wrapper


def take_and_reset() -> RetrievalTally | None:
    """Atomically claim the active tally and clear it. Returns None if unarmed."""
    with _LOCK:
        tally = _ACTIVE.get()
        if tally is None:
            return None
        _ACTIVE.set(None)
        return tally


def flush_tally(tally: RetrievalTally | None,
                org_id: str | None = None) -> dict | None:
    """Write one tally to the ledger. TOTAL — never raises.

    Returns the writer's summary dict, or None when there was nothing to write,
    when the org is unresolvable, or when the write was dropped. An empty tally
    and an already-consumed tally both return None.
    """
    if tally is None:
        return None
    # The consumed transition AND the counter snapshot share ONE critical
    # section: two boundaries (a middleware and a caller) can reach the same
    # tally from different threads, and a check-then-set race would double-write
    # it while a snapshot read after the release could miss an increment.
    with _LOCK:
        if tally.consumed:
            return None
        tally.consumed = True
        snap = {
            "queries": tally.queries,
            "wall_ms": tally.wall_ms,
            "leg_runs": tally.leg_runs,
            "leg_degraded": tally.leg_degraded,
            "legs": dict(tally.legs),
            # Read INSIDE the section: `bind_org` also takes the lock, so a
            # late bind can no longer land between the snapshot and its use.
            "org_id": tally.org_id,
        }
    if not (snap["queries"] or snap["leg_runs"] or snap["leg_degraded"]):
        return None
    org = org_id or snap["org_id"]
    if not org:
        # A non-empty tally with no org is a BOOKKEEPING fault of ours: the
        # reads happened and cannot be attributed. Same lane, same alert.
        _report(org_id=None, error=RuntimeError(
            "retrieval tally has no resolvable org — the retrieval work was "
            "done but cannot be attributed to a ledger row"
        ))
        return None
    legs = snap["legs"]
    try:
        from tortoise import metering
        return metering.record_retrieval_usage(
            org,
            queries=snap["queries"],
            leg_runs=snap["leg_runs"],
            leg_degraded=snap["leg_degraded"],
            wall_ms=snap["wall_ms"],
            leg_fts=int(legs.get("fts", 0)),
            leg_vector=int(legs.get("vector", 0)),
            leg_structural=int(legs.get("structural", 0)),
        )
    except Exception as e:
        _report(org_id=org, error=e)
        return None


def flush(org_id: str | None = None) -> dict | None:
    """Take-and-reset the active tally and write it. TOTAL — never raises."""
    return flush_tally(take_and_reset(), org_id)


def _report(*, org_id: str | None, error: BaseException) -> None:
    """Announce a dropped increment on the ``retrieval`` lane. Never raises.

    The ``lane=`` KEYWORD FORM is load-bearing: the swallow-site completeness
    fence censuses literal ``report_unmetered_increment(lane="…")`` calls
    (``tests/test_metering_window_admission.py``), so a positional call would
    make this lane invisible to it.
    """
    with contextlib.suppress(Exception):
        from tortoise import metering
        metering.report_unmetered_increment(
            lane="retrieval", org_id=org_id, error=error
        )


class RetrievalMeteringMiddleware:
    """Pure-ASGI middleware: arm every request, flush once on exit.

    Registered INSIDE ``InFlightMiddleware`` (and therefore inside the
    WaitBound-owned task) and — like the sibling compute/egress/embed boundaries
    — inside ``WaitBoundMiddleware``, which keeps its documented outermost seat:
    a request the bound refuses must not be credited with retrieval work.

    Armed on EVERY HTTP method, unlike ``EmbedMeteringMiddleware`` (which arms
    non-GET only, because an encode is a WRITE-lane activity): a read is a GET
    (``GET /v1/search``) as well as a POST, so method-gating here would measure
    the wrong half of the surface. An armed request that retrieves nothing pays
    one ContextVar set and one ``is_empty()`` check — no lock, no dict, no
    thread hop (see the ``is_empty`` guard below).

    The org is resolved at FLUSH time, after the handler has run: the auth
    dependencies write ``scope["state"]["org_id"]`` DURING the request and
    ``scope`` is the same dict by then. A lane whose org never reaches scope
    state (MCP, session-JWT, health probes) is DROPPED and alerted on the
    ``retrieval`` lane rather than being filed under an invented org.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        arm()
        try:
            await self.app(scope, receive, send)
        finally:
            pending = take_and_reset()
            # ⛔ NO ``return`` IN THIS ``finally``: a return here would DISCARD
            # any exception the app raised, turning every failing request into a
            # silent success. The guard is an ``if`` for that reason, not style.
            if pending is not None and not pending.is_empty():
                state = scope.get("state")
                org = pending.org_id or (
                    state.get("org_id") if isinstance(state, dict) else None)
                with contextlib.suppress(Exception):
                    # Awaited on a worker thread (never fire-and-forget): the
                    # ledger write is blocking, so it must not run on the loop,
                    # and the row is committed before the wrap-up completes.
                    await asyncio.to_thread(flush_tally, pending, org)
