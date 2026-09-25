"""The non-folded-event set — R8/R9 of the identity decision (#2835, #3585).

The projection is a derived view: ``derived tables == replay(the journal)``
(docs/architecture/STORAGE-ARCHITECTURE.md §3). That invariant is only as
strong as its ability to see an event the fold could NOT replay. Two equally
INCOMPLETE projections compare equal, so a fold that silently skips an
unresolvable event manufactures a vacuous green pass — the failure R9 names.

This module is the missing half: a **run-scoped, structured record of every
journal event the fold could not resolve to exactly one node**, plus the
fail-closed assertion that turns a non-empty set into a raised error.

R8 — the projection never guesses
---------------------------------
When a journal event cannot be resolved to exactly one node the fold is NOT
applied, a structured entry is recorded here (journal position, candidates,
failure shape), and **the run fails** — a raised error, not a warning. A merely
loud warning that leaves the run passing is the anti-pattern this exists to
prevent.

The disposition label (``refused`` vs ``journaled-and-flagged``) is the RECORD
of how the fold handled the target's state. It is **never an exemption from the
assertion**: both dispositions fail the run. Only a shape listed in
:data:`EXEMPT_SHAPES` — with its degraded guarantee written down — is allowed to
leave the run green.

Shapes without a recorded exemption are fail-closed BY DEFAULT: a new fold-miss
site that forgets to classify itself still fails the run.

R9 — the invariant asserts the set is empty
-------------------------------------------
:func:`collect_non_folded` is the run boundary. The wipe+replay engines
(``rebuild_all`` / ``rebuild(log)`` / ``recover_from_log``) and
``consistency.check_consistency`` open it, and assert the set is empty on exit.
The collector is a ``ContextVar`` so a fold reached deep inside a run records
into the run's set without a signature change at every call site — and so
concurrent runs in different contexts (threads / asyncio tasks) cannot merge
their sets.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ── Failure shapes ────────────────────────────────────────────────────────
#
# The shape names the MECHANISM that produced the non-folded event, so a
# report identifies the failure, not merely "a miss". Every shape is classified
# below; an unknown shape is treated as refused (fail-closed), never exempt.

#: ``EntityMutated`` state op (rename/restatus/revise) matched no entity.
SHAPE_STATE_OP_MISS = "state-op-miss"
#: ``EntityMutated op=delete`` matched 0 rows.
SHAPE_DELETE_MISS = "delete-miss"
#: An ``EntityMutated`` op the fold has no arm for (recorded on #3299).
SHAPE_UNIMPLEMENTED_OP = "unimplemented-op"
#: An ``EntityMutated`` op outside the recorded vocabulary.
SHAPE_UNKNOWN_OP = "unknown-op"
#: ``ObjectSuperseded`` matched no Object.
SHAPE_OBJECT_SUPERSEDED_MISS = "object-superseded-miss"
#: ``PointSuperseded`` matched no Point.
SHAPE_POINT_SUPERSEDED_MISS = "point-superseded-miss"
#: ``PointRetracted`` matched no Point.
SHAPE_POINT_RETRACTED_MISS = "point-retracted-miss"
#: ``PointInvalidated`` matched no Point.
SHAPE_POINT_INVALIDATED_MISS = "point-invalidated-miss"
#: A belief/annotation write-back matched no Point.
SHAPE_POINT_BELIEF_MISS = "point-belief-miss"
#: A record type outside the projection's recognized vocabulary.
SHAPE_UNKNOWN_EVENT_TYPE = "unknown-event-type"

#: Shapes that are genuinely exempt from the fail-the-run assertion. Each MUST
#: carry its degraded guarantee in writing — an unnamed exemption is a green
#: pass over an unfolded event, which is exactly what R8 exists to prevent.
#: The shapes and their bounds below are ALSO recorded in the identity decision
#: doc (`docs/epics/2026-09-10-2835-capability-registry/identity-decision.md`,
#: §"Stage 0 findings — the named, bounded non-folded exemptions (#3585)") —
#: the artifact R8 designates for them, so the ruling is findable where a later
#: lane would otherwise read an exemption as drift.
#:
#: ``delete-miss`` (recorded decision: `docs/plans/2026-09-22-unjournaled-mutation-class.md`
#: §"Task 4" — Policy, and #4743's disposition): "already absent" IS the
#: delete's desired end state, so a delete matching 0 rows is legitimately
#: idempotent (a retried delete, or an apply-based replay onto a graph that
#: already holds the node). DEGRADED GUARANTEE: this exemption does not hide an
#: unjournaled CREATION — a journal that deletes an entity it never registered
#: is caught by the rebuild's entity census (the journal registers nothing, but
#: the delete is still reported) and by ``check_consistency``'s entity parity
#: leg.
#:
#: ``point-superseded-no-new-id`` (recorded decision: the same plan's §Task 4
#: warning policy): the graph fold treats a ``PointSuperseded`` with no
#: ``new_id`` as a documented no-op (``_fold_journal`` mirrors it), so neither
#: side changes state. DEGRADED GUARANTEE: the malformed record is reported,
#: and a later CORRECTS edge it might have carried is absent on both sides — a
#: bounded, symmetrical loss, not a live/replay divergence.
#:
#: ``supersede-target-deleted`` (recorded decision: #4743's disposition — the
#: supersede/invalidate fold is DEFERRED to a trailing sweep that runs after
#: pass-1b, so a target the journal hard-deleted is legitimately gone by sweep
#: time): ``live`` and ``replay`` both end with the node absent. DEGRADED
#: GUARANTEE: the exemption is granted only for a hard delete of the SAME kind
#: (or the id-wide fallback) that the fold actually applied — a same-kind
#: re-creation anchor that suppressed the delete does not tag it. So a delete
#: of a DIFFERENT kind sharing the id still refuses, an ``Object``/``Point``
#: whose status was merely buried still refuses, and the entity-parity leg
#: still compares any re-created node's status.
EXEMPT_SHAPES: dict[str, str] = {
    SHAPE_DELETE_MISS: (
        "idempotent by construction — 'already absent' is the delete's end "
        "state (recorded decision: plan §Task 4 Policy / #4743)"
    ),
    "point-superseded-no-new-id": (
        "the graph fold treats it as a documented no-op; no state changes on "
        "either side (recorded decision: plan §Task 4 warning policy)"
    ),
    "supersede-target-deleted": (
        "the supersede/invalidate fold is DEFERRED to a trailing sweep, so a "
        "target the journal hard-deleted BEFORE the sweep runs is legitimately "
        "gone — live and replay both end with the node absent. DEGRADED "
        "GUARANTEE: exempt only for a SAME-KIND hard delete (`_hard_deleted_any`; "
        "the id-wide fallback is checked too) that the fold actually applied — "
        "a re-creation anchor that suppressed the delete does not tag it — so a "
        "foreign-kind delete sharing the id still refuses, and the "
        "entity-parity leg still compares a re-created node's status"
    ),
}

#: The two dispositions R8 defines. Both FAIL the run; the label only records
#: how the fold left the target's state.
DISPOSITION_REFUSED = "refused"          # target left untouched
DISPOSITION_FLAGGED = "journaled-and-flagged"  # recorded, run still fails


@dataclass(frozen=True)
class NonFoldedEvent:
    """One journal event that could not be resolved to exactly one node."""

    shape: str
    event_id: str | None = None
    event_type: str | None = None
    label: str | None = None
    id: str | None = None
    op: str | None = None
    seq: int | None = None
    candidates: tuple = ()
    disposition: str = DISPOSITION_REFUSED
    detail: str = ""

    @property
    def exempt(self) -> bool:
        return self.shape in EXEMPT_SHAPES

    @property
    def exemption(self) -> str | None:
        return EXEMPT_SHAPES.get(self.shape)

    def where(self) -> str:
        """The journal locator: seq when known, else the event id."""
        if self.seq is not None:
            return f"seq={self.seq}"
        return f"event_id={self.event_id!r}"

    def __str__(self) -> str:  # pragma: no cover - formatting only
        bits = [f"[{self.shape}]", self.where()]
        if self.event_type:
            bits.append(f"type={self.event_type}")
        if self.label or self.id:
            bits.append(f"target=({self.label!r},{self.id!r})")
        if self.op:
            bits.append(f"op={self.op!r}")
        if self.candidates:
            bits.append(f"candidates={list(self.candidates)!r}")
        if self.detail:
            bits.append(self.detail)
        if self.exempt:
            bits.append(f"(exempt: {self.exemption})")
        return " ".join(bits)


class NonFoldedEventsError(RuntimeError):
    """Raised when a replay run left one or more events unfolded (R8).

    Carries the full structured set: ``.events`` is every non-folded record
    (exempt and refused) and ``.refused`` is the subset that fails the run.
    ``.engine`` names the run that produced it.
    """

    def __init__(self, events, *, engine: str = "rebuild"):
        self.events: tuple[NonFoldedEvent, ...] = tuple(events)
        self.refused: tuple[NonFoldedEvent, ...] = tuple(
            e for e in self.events if not e.exempt)
        self.engine = engine
        named = "; ".join(str(e) for e in self.refused[:5])
        more = "" if len(self.refused) <= 5 else (
            f" (+{len(self.refused) - 5} more)")
        super().__init__(
            f"{engine}: {len(self.refused)} journal event(s) could not be "
            f"resolved to exactly one node and were NOT folded — the "
            f"projection would be silently incomplete (R8/#3585): "
            f"{named}{more}. Reconcile the journal (a missing creation "
            f"event, an out-of-order append, or an unjournaled producer) "
            f"before relying on this graph."
        )


# ── The run-scoped collector ──────────────────────────────────────────────

_COLLECTOR: ContextVar[list | None] = ContextVar(
    "tortoise_non_folded_set", default=None)


@contextmanager
def collect_non_folded():
    """Open a non-folded-event run. Yields the (mutable) list to inspect.

    Nested runs are independent: the inner context gets its own list and the
    outer one resumes on exit.
    """
    entries: list[NonFoldedEvent] = []
    token = _COLLECTOR.set(entries)
    try:
        yield entries
    finally:
        _COLLECTOR.reset(token)


def collector_active() -> bool:
    return _COLLECTOR.get() is not None


def record_non_folded(shape: str, **fields) -> NonFoldedEvent:
    """Record one non-folded event into the active run (if any).

    Always returns the :class:`NonFoldedEvent` so a call site can log it; when
    no run is active the entry is simply not collected (the call site's own
    warning remains the signal — a one-record ``apply()`` has no run boundary).
    """
    event = NonFoldedEvent(shape=shape, **fields)
    entries = _COLLECTOR.get()
    if entries is not None:
        entries.append(event)
    return event


def refused_events(entries) -> list[NonFoldedEvent]:
    """The subset of ``entries`` that fails the run (R8)."""
    return [e for e in entries if not e.exempt]


def assert_no_non_folded(entries, *, engine: str = "rebuild") -> None:
    """Fail closed: raise :class:`NonFoldedEventsError` if the run refused any.

    Exempt shapes are recorded and reported but do not fail the run; they are
    named in :data:`EXEMPT_SHAPES` with their degraded guarantee.

    ``entries`` may be a list OR a collector result. When called with the list
    yielded by :func:`collect_non_folded` the full set (exempt + refused) rides
    on the error, so a caller can inspect every non-folded event, not only the
    ones that failed it.
    """
    refused = refused_events(entries)
    if refused:
        raise NonFoldedEventsError(entries, engine=engine)
    if entries:
        logger.warning(
            "%s: %d non-folded journal event(s) recorded but EXEMPT (run "
            "passes): %s", engine, len(entries),
            "; ".join(str(e) for e in entries[:5]))
