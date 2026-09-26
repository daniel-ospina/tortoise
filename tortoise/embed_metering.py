"""Per-org EMBEDDING-ENCODE workload measurement (#4488).

WHY THIS EXISTS. The cost meter (#3359/#3824) covers LLM PROVIDER calls, which
have a billable token count. A local ``sentence-transformers`` encode consumes
CPU seconds and RAM and produces no such count — so embedding work is invisible
to every existing figure. This module measures it on the SAME durable
per-(org, period) ledger the ask/capture lanes use, so the reading needs no new
store and no new read path: texts, characters, wall time and the model identity
that produced them, keyed by org.

WHAT IT IS NOT. It records WORKLOAD, never price, tier or quota (#4488's
explicit out-of-scope list). Nothing here can refuse, throttle or bill. The
figure is descriptive.

SHAPE. A mutable :class:`EmbedTally` lives in a :class:`contextvars.ContextVar`.
The encode funnel — :func:`tortoise.embeddings.compute_embeddings` — MUTATES it
in place: O(1), no I/O, no lock on the hot path. A WORK-OWNING boundary arms /
notes, then takes-and-resets the tally and writes it once through
``metering.record_embedding_usage``:

* :class:`EmbedMeteringMiddleware` — every non-GET HTTP request, on every auth
  lane that resolves an org (the key dependencies AND session auth write
  ``scope["state"]["org_id"]`` during the request);
* ``mcp_server._quota_gated`` — the MCP write lane;
* :func:`meted` — work born in a DETACHED task (background index jobs, the dream
  worker, the onboarding/starter seed runners) installs a FRESH tally and flushes
  when it exits. The hosted CAPTURE body is NOT a ``meted`` site: a capture is a
  REQUEST, so the middleware owns it — the pool thread's copied context shares
  the request's tally object, and the boundary flush picks the notes up.

WHY A MUTABLE TALLY RATHER THAN A COPY-ON-WRITE VALUE. The encode can run in a
pool thread whose context was copied (``hosted_api._submit_off_loop`` uses
``contextvars.copy_context()``). A ContextVar set in the request context is
visible to that copy, but a REBIND (``set``) inside the worker is not propagated
back — mutating a shared object IS. Same reasoning as the sibling ``graph_ops``
counter (PR #5292).

TOTAL, NEVER RAISES. ``note_encode``/``note_skip``/``bind_org`` are total: an
allocation fault on the measurement path must never fail a write. ``flush``
absorbs every failure and reports the drops it can DETECT — a
**window-unresolvable** window and a non-empty tally with **no resolvable org**
— to the operator through ``metering.report_unmetered_increment``, the same
lane-visible signal the seven swallow sites use (this module is the ``embed``
lane). A failure of the increment RPC ITSELF is logged at WARNING and is the
#3824 residual, shared with every other lane (see
``metering.record_embedding_usage``); a note landing after the tally was
consumed is the capture-cancellation residual. Those two are the ones this
module cannot alert on — a declared limitation, not a claim of silence.

NOT ON THE WRITE PATH. There is deliberately NO flush from
``hosted_api._record_write_op``: that site is SYNCHRONOUS and runs ON the event
loop (the #4451 residual), and flushing there would add a SECOND blocking
ledger write per write op — lengthening every response on a transport that
carries a hard wait bound. The middleware flush is offloaded
(``asyncio.to_thread``) and the runner flushes are offloaded too, so the
measurement never sits between a write and its response.

MEASURED VS EXCLUDED. Only :func:`tortoise.embeddings.compute_embeddings` — the
store-vector funnel — is hooked. ``_encode``/``search_points``/``kind_index``
pass ``show_progress_bar``, which the active encoder does not accept, so they
raise-and-degrade to TF-IDF and run no model work today (#5321); read/query-side
paths are out of scope. If #5321 is fixed, the same hook extends to them.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Literal

_logger = logging.getLogger(__name__)

#: The active tally for the current context, or None outside any boundary.
#: ``default=None`` keeps the unarmed path to one cheap ``get()`` per encode.
_ACTIVE: ContextVar[EmbedTally | None] = ContextVar(
    "tortoise_embed_tally", default=None
)

#: Guards the tally's consumed transition and its counter snapshot (one critical
#: section), and the take-and-reset. Never held across the ledger RPC — a
#: blocking write under this lock would serialize concurrent encodes, which is
#: exactly the hot path this module exists to keep free.
_LOCK = threading.Lock()


@dataclass
class EmbedTally:
    """One work unit's embedding-encode tally.

    Mutable by design (see the module docstring). ``model``/``revision`` are the
    identity of the FIRST real encode observed; ``identity_mixed`` latches True
    when a real encode under a DIFFERENT identity is observed inside the same
    tally (a mid-request swap — rare, but it must not be invisible).
    """

    calls: int = 0
    texts: int = 0
    chars: int = 0
    wall_ms: float = 0.0
    skipped: int = 0
    model: str | None = None
    revision: str | None = None
    identity_mixed: bool = False
    org_id: str | None = None
    #: Set by :func:`flush_tally` — a second flush of the same object is a
    #: no-op (two boundaries in one request record once).
    consumed: bool = False
    #: True once a real encode has stamped an identity (distinguishes "never
    #: encoded" from "encoded under an identity that happens to be None").
    _identity_seen: bool = field(default=False, repr=False)

    def note_encode(self, *, texts: int, chars: int, wall_ms: float) -> None:
        """Accumulate one real model encode. Total — never raises.

        ⛔ The mutation holds :data:`_LOCK`, and ``_resolve_identity`` is called
        OUTSIDE it. That is the whole point of the split: the resolver may
        import and consult the embeddings module, and holding the lock across
        that would put unrelated work inside a section :func:`flush_tally` also
        needs. The four counters and the identity fields are then updated as ONE
        section — which is exactly the property ``flush_tally``'s snapshot
        depends on. Without it the snapshot's claim is only half true: the
        reader is locked but the WRITER was not, so a pool thread still running
        at boundary time (the declared cancellation residual) could be read
        mid-increment and produce an internally inconsistent row —
        ``embed_calls`` up and ``embed_texts`` not. Found in review.
        """
        model, revision = _resolve_identity()
        with _LOCK:
            self.calls += 1
            self.texts += texts
            self.chars += chars
            self.wall_ms += wall_ms
            if not self._identity_seen:
                self._identity_seen = True
                self.model, self.revision = model, revision
            elif (model, revision) != (self.model, self.revision):
                # Keep the FIRST identity: the writer compares the incoming
                # pair against the STORED one, and this flag carries the
                # in-tally change.
                self.identity_mixed = True

    def note_skip(self, n: int = 1) -> None:
        """Count encode attempts that ran no model work (model unavailable)."""
        with _LOCK:
            self.skipped += n

    def bind_org(self, org_id: str | None) -> None:
        """Attach the org this work belongs to. No-op for a falsy id.

        Locked for the same reason as :meth:`note_encode`: ``flush_tally``
        reads the org, and an unlocked bind could land between its snapshot and
        that read.
        """
        if org_id:
            with _LOCK:
                self.org_id = org_id

    def is_empty(self) -> bool:
        """True when this tally carries nothing worth a ledger row."""
        return not (self.calls or self.skipped)


def _resolve_identity() -> tuple[str | None, str | None]:
    """Resolve ``(model, revision)`` at CALL time (rebinding-friendly)."""
    try:
        from tortoise.embeddings import embedding_identity
        return embedding_identity()
    except Exception:  # pragma: no cover — identity must never raise
        return None, None


def arm(org_id: str | None = None) -> EmbedTally:
    """Install (once) and return the active tally; optionally bind the org.

    Idempotent: a second call in the same context returns the SAME tally rather
    than starting a fresh one (two boundaries in one request share one figure).
    """
    tally = _ACTIVE.get()
    if tally is None:
        tally = EmbedTally()
        _ACTIVE.set(tally)
    if org_id:
        tally.bind_org(org_id)
    return tally


def current_tally() -> EmbedTally | None:
    """The active tally, or None when unarmed (tests/diagnostics)."""
    return _ACTIVE.get()


def bind_org(org_id: str | None) -> None:
    """Bind *org_id* to the active tally. Total; no-op when unarmed or falsy."""
    tally = _ACTIVE.get()
    if tally is not None and org_id:
        tally.bind_org(org_id)


def note_encode(*, texts: int, chars: int, wall_ms: float) -> None:
    """Note one real encode against the active tally. Unarmed → no-op."""
    tally = _ACTIVE.get()
    if tally is not None:
        tally.note_encode(texts=texts, chars=chars, wall_ms=wall_ms)


def note_skip(n: int = 1) -> None:
    """Note *n* encode attempts that performed no model work."""
    tally = _ACTIVE.get()
    if tally is not None:
        tally.note_skip(n)


def take_and_reset() -> EmbedTally | None:
    """Atomically claim the active tally and clear it. Returns None if unarmed.

    One of the operations under :data:`_LOCK` (the other is
    :func:`flush_tally`'s consumed transition + snapshot). The claimed tally is
    marked consumed by :func:`flush_tally`, so a second flush of the same object
    is a no-op.
    """
    with _LOCK:
        tally = _ACTIVE.get()
        if tally is None:
            return None
        _ACTIVE.set(None)
        return tally


def flush_tally(tally: EmbedTally | None, org_id: str | None = None) -> dict | None:
    """Write one tally to the ledger. TOTAL — never raises.

    Returns the writer's summary dict, or None when there was nothing to write,
    when the org is unresolvable, or when the write was dropped.

    A DROP IS ALERTED WHEN THIS MODULE CAN TELL IT APART — an unresolvable
    metering window, or a non-empty tally with no org, both go to the operator on
    the ``embed`` lane. Two drops are NOT alerted here, and that is a declared
    limitation rather than a claim: a failure of the increment RPC ITSELF is
    absorbed by ``metering.record_embedding_usage`` (WARNING — the shared #3824
    residual), and a note that lands in a tally this call has already CONSUMED
    (the capture-cancellation residual) is not attributed to any row.
    """
    if tally is None:
        return None
    # The consumed transition AND the counter snapshot share ONE critical
    # section. Two boundaries (a runner and the request middleware) can reach the
    # same tally from different threads: a check-then-set race would double-write
    # it, and a snapshot read after the release could miss an increment that
    # landed in between.
    with _LOCK:
        if tally.consumed:
            return None
        tally.consumed = True
        snap = {
            "calls": tally.calls,
            "texts": tally.texts,
            "chars": tally.chars,
            "wall_ms": tally.wall_ms,
            "skipped": tally.skipped,
            "model": tally.model,
            "revision": tally.revision,
            "identity_mixed": tally.identity_mixed,
            # Read INSIDE the section: `bind_org` also takes the lock, so a
            # late bind can no longer land between the snapshot and its use.
            "org_id": tally.org_id,
        }
    if not (snap["calls"] or snap["skipped"]):
        return None
    org = org_id or snap["org_id"]
    if not org:
        # A non-empty tally with no org is a BOOKKEEPING fault of ours: the
        # work happened and cannot be attributed. Same lane, same alert.
        _report(org_id=None, error=RuntimeError(
            "embedding tally has no resolvable org — the encode work was done "
            "but cannot be attributed to a ledger row"
        ))
        return None
    try:
        from tortoise import metering
        return metering.record_embedding_usage(
            org,
            calls=snap["calls"],
            texts=snap["texts"],
            chars=snap["chars"],
            wall_ms=snap["wall_ms"],
            skipped=snap["skipped"],
            model=snap["model"],
            revision=snap["revision"],
            identity_mixed=snap["identity_mixed"],
        )
    except Exception as e:
        _report(org_id=org, error=e)
        return None


def flush(org_id: str | None = None) -> dict | None:
    """Take-and-reset the active tally and write it. TOTAL — never raises."""
    return flush_tally(take_and_reset(), org_id)


def _report(*, org_id: str | None, error: BaseException) -> None:
    """Announce a dropped increment on the ``embed`` lane. Never raises.

    The ``lane=`` KEYWORD FORM is load-bearing: the swallow-site completeness
    fence censuses literal ``report_unmetered_increment(lane="…")`` calls
    (``tests/test_metering_window_admission.py``), so a positional call would
    make this lane invisible to it.
    """
    with contextlib.suppress(Exception):
        from tortoise import metering
        metering.report_unmetered_increment(
            lane="embed", org_id=org_id, error=error
        )


class _Meted:
    """Context manager installing a FRESH tally and flushing it on exit.

    Fresh, never inherited: a detached work unit owns its own figure, so it can
    neither double-count a request-scoped tally nor steal one. Usable as a sync
    CM (MCP ``_quota_gated``, the seed runners) or an async CM (the background
    index jobs and the dream worker) — the async arm offloads the blocking
    ledger write, and the sync arm offloads it too when it is entered from a
    running loop, so the loop is never stalled by metering.
    """

    __slots__ = ("_org", "_tally", "_token")

    def __init__(self, org_id: str | None) -> None:
        self._org = org_id
        self._token = None
        self._tally: EmbedTally | None = None

    # ── shared ──────────────────────────────────────────────────────────────
    def _install(self) -> EmbedTally:
        tally = EmbedTally()
        tally.bind_org(self._org)
        self._tally = tally
        self._token = _ACTIVE.set(tally)
        return tally

    def _finish(self) -> None:
        if self._token is not None:
            with contextlib.suppress(Exception):
                _ACTIVE.reset(self._token)
        tally, org = self._tally, self._org
        # An empty tally has nothing to write (``flush_tally`` returns
        # immediately), so skip the whole flush: no lock, no dict, no thread.
        # A decorated runner that encoded NOTHING — the common case for the
        # seed runners, and every OPTIONS preflight at the middleware — must
        # not pay for a measurement it does not have.
        if tally.is_empty():
            return
        # ⛔ NEVER BLOCK THE EVENT LOOP. The sync arm is reached from "sync"
        # runners that an ``async def`` handler invokes DIRECTLY
        # (``_run_onboarding_seed`` / ``_run_starter_seed``) and from FastMCP's
        # on-loop tool dispatch, so flushing inline would run a blocking ledger
        # write under the loop — the #4451 class this change deliberately kept
        # OFF the write path. Off the loop (a genuine pool thread) inline is
        # correct and cheapest; on the loop the write is handed to a thread.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is None:
            flush_tally(tally, org)
            return
        with contextlib.suppress(Exception):
            # Fire-and-forget by design: the measurement must never sit between
            # a write and its response. ``take_and_reset`` is atomic, so if the
            # request boundary also flushes, exactly one of the two writes wins.
            loop.create_task(asyncio.to_thread(flush_tally, tally, org))

    # ── sync ────────────────────────────────────────────────────────────────
    def __enter__(self) -> EmbedTally:
        return self._install()

    def __exit__(self, *exc) -> Literal[False]:
        with contextlib.suppress(Exception):
            self._finish()
        return False

    # ── async ───────────────────────────────────────────────────────────────
    async def __aenter__(self) -> EmbedTally:
        return self._install()

    async def __aexit__(self, *exc) -> Literal[False]:
        tally, org, token = self._tally, self._org, self._token
        if token is not None:
            with contextlib.suppress(Exception):
                _ACTIVE.reset(token)
        if tally.is_empty():
            return False                      # nothing to write — see _finish
        try:
            # Offload: the ledger write is blocking (FalkorDB/HTTP) and must not
            # stall the loop — the same discipline as the write paths.
            await asyncio.to_thread(flush_tally, tally, org)
        except Exception:
            _logger.debug("embed meted flush failed", exc_info=True)
        return False


def meted(org_id: str | None) -> _Meted:
    """Install a FRESH tally for *org_id* and flush it on exit.

    Use where work is BORN (a detached task or a runner that owns its lane), so
    the figure is attributed to the org that caused it even when no HTTP
    boundary will ever flush it.
    """
    return _Meted(org_id)


class EmbedMeteringMiddleware:
    """Pure-ASGI middleware: arm non-GET requests, flush once on exit.

    Registered INSIDE ``InFlightMiddleware`` (and therefore inside the
    WaitBound-owned task) but outside every route, so it sees the request that
    caused the work — not the pool thread that ran it.

    The org is resolved at FLUSH time, after the handler has run: every auth
    dependency that resolves an org (key auth AND session auth) writes
    ``scope["state"]["org_id"]`` during the request, and ``scope`` is the same
    dict by then. Routes whose org never reaches scope state (the internal seed
    lanes) are covered by ``meted`` at the runner instead — see the call sites.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http" or scope.get("method") == "GET":
            await self.app(scope, receive, send)
            return
        tally = arm()
        try:
            await self.app(scope, receive, send)
        finally:
            org = tally.org_id
            if not org:
                state = scope.get("state")
                org = state.get("org_id") if isinstance(state, dict) else None
            # ``take_and_reset`` runs synchronously on the loop, so an UNARMED
            # request (or a handler that replaced the tally) is settled here.
            pending = take_and_reset() or tally
            # Skip the offload when there is nothing to write. ``flush_tally``
            # would return immediately anyway, but the ``to_thread`` hop is
            # ALREADY SPENT by then — and it lands on the same small default
            # executor the real ledger writes use, so under saturation the
            # request's ``finally`` queues behind them for a call that does
            # nothing. Measured: that no-op hop IS essentially the whole
            # per-request cost of this middleware, and it is paid by every
            # non-GET request, OPTIONS preflight included.
            if not pending.is_empty():
                await asyncio.to_thread(flush_tally, pending, org)
