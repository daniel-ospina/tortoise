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
:func:`collect_non_folded` is the run boundary. Five engines open it:
``rebuild_all`` / ``rebuild(log)`` (through ``_fail_closed``), ``backup.restore``,
``recover_from_log`` and ``consistency.check_consistency`` — across four ``with``
sites, since the two rebuild engines share ``_fail_closed``. The first three RAISE
on a non-empty refusal set; ``recover_from_log`` and ``check_consistency``
return a failed verdict instead (``recovered=False`` / ``ok=False,
divergence="non-folded"``).
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
#: ``ObjectSuperseded`` matched no Object, and the JOURNAL cannot ADDRESS the
#: record — it carries at least one address (a name, an id, or both) and the
#: journal established NONE of the addresses it carries: a name by no
#: `ObjectRegistered`, no `EntityMutated` state op, no `EventRecorded` mention
#: stub; an id by no registration, no mention stub's deterministic id, no state
#: op's target id (#3590 D2, as amended on #10204). ⛔ The record must carry an
#: address but NOT specifically a name: an id-only record IS this shape when
#: nothing established that id. Distinct from
#: ``SHAPE_OBJECT_SUPERSEDED_MISS``, which keeps the fail-closed refusal for a
#: target the journal DID establish.
SHAPE_OBJECT_SUPERSEDED_UNREGISTERED = "object-superseded-unregistered"
#: ``ObjectSuperseded`` carrying NO address it could be looked up by — no
#: ``name`` and no ``id`` that is a non-empty ``str`` (``{}``,
#: ``{supersedes_by: …}``, ``{"name": ""}``,
#: ``{"id": 123}``). REFUSED, never exempt, and
#: it exists so the report can tell this apart from
#: ``SHAPE_OBJECT_SUPERSEDED_MISS``: nothing in such a record is checkable, so
#: "the journal never established it" is unfalsifiable (#3590 D2, #10204) and
#: the fail-closed default must stand — while a genuine established-then-missed
#: target keeps that shape's own remedy (reconcile the journal). The two are
#: epistemically different records and used to share one label, which is the
#: defect shape the END-side ruling on #9202 fixed.
SHAPE_OBJECT_SUPERSEDED_NO_ADDRESS = "object-superseded-no-address"
#: ``ObjectSuperseded`` matched no Object.
SHAPE_OBJECT_SUPERSEDED_MISS = "object-superseded-miss"
#: The three answers the JOURNAL can give about an ``ObjectSuperseded`` miss's
#: address — the ``object_address=`` decision ``classify_terminalizer_miss``
#: takes for that type (#10204). ⛔ The value is the JOURNAL's answer, never a
#: graph probe: "no live holder" cannot tell "never established" from
#: "established then hard-deleted", and a graph probe would exonerate a graph
#: whose journal is simply incomplete.
#:
#: * :data:`OBJECT_ADDRESS_NONE` — NEITHER ``name`` nor ``id`` is a non-empty
#:   ``str``. ⚠️ Symmetric on purpose: a record carrying ``{"name": 123}`` or
#:   ``{"name": ""}`` DOES carry a name, so "no ``name``" was false for it.
#:   PRESENCE, not writability: a NUL-bearing id IS an address here, so this is
#:   not "an address the graph could not store".
#: * :data:`OBJECT_ADDRESS_UNESTABLISHED` — carries ≥1 address, the journal
#:   established none of them → the exempt shape.
#: * :data:`OBJECT_ADDRESS_ESTABLISHED` — carries an address the journal DID
#:   establish → the refusing shape. ⚠️ A caller with NO journal to consult (the
#:   one-record LIVE path) must send this too: it is the fail-CLOSED answer.
OBJECT_ADDRESS_NONE = "none"
OBJECT_ADDRESS_UNESTABLISHED = "unestablished"
OBJECT_ADDRESS_ESTABLISHED = "established"
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
#: ``PointSuperseded`` carrying NO ``new_id`` — the documented no-op (#3585).
SHAPE_POINT_SUPERSEDED_NO_NEW_ID = "point-superseded-no-new-id"
#: A supersede/invalidate whose target the journal hard-deleted — the deferred
#: sweep's legitimate 0-row match (#3585, #4743).
SHAPE_SUPERSEDE_TARGET_DELETED = "supersede-target-deleted"
#: A terminalizer type the classifier does not know. Fail-CLOSED (never
#: exempt) — a NEW class of terminalizer miss must not slip through as a
#: green pass. Distinct from :data:`SHAPE_UNKNOWN_EVENT_TYPE`, which
#: ``_apply_one`` already emits for a different mechanism (an unrecognized
#: record type), so a report can tell the two apart.
SHAPE_UNCLASSIFIED_TERMINALIZER = "unclassified-terminalizer"
#: #5380: an ``EdgeMessageChanged op=set`` matched no edge. Exempt — the
#: write-back's edge can be legitimately absent: a hard-deleted edge is gone
#: live too, and on the one-record ``apply()`` leg a plain direct edge is still
#: unfolded (``DirectEdgeCreated`` sits in ``_APPLY_WARN_ONLY_TYPES``; that
#: engine has no pass-2b — see the ``SHAPE_EDGE_MESSAGE_MISS`` entry below).
#: ⛔ The ORIGINAL justification — "``DirectEdgeCreated`` is not folded by the
#: replay engines, A10 #1048, so the edge is lost by a gap this record does not
#: cause" — is RETIRED for ``rebuild_all``, which now folds it, so on a
#: ``rebuild_all`` run every occurrence is evidence of a REAL divergence.
#: Recorded (not merely warned) so the miss is reported by the run boundary
#: (``assert_no_non_folded`` warns and names every exempt record) and by
#: ``rebuild_all``'s post-replay dressed-edge report. NOT by
#: ``check_consistency``: its journal side is ``_apply_one``-based and
#: ``EdgeMessageChanged`` is in ``_NO_POINT_FOLD``, so that surface never
#: calls the edge-message fold and cannot emit this shape.
SHAPE_EDGE_MESSAGE_MISS = "edge-message-miss"
#: #5380: an ``EdgeMessageChanged op=clear`` the fold could NOT replay — an
#: unrecognised ``scope``, or a known scope whose ``point_ids`` are missing or
#: unusable. The rebuilt graph therefore KEEPS the seeds the live
#: ``invalidate_messages`` / ``invalidate_factor_messages`` REMOVEd.
SHAPE_EDGE_MESSAGE_UNREPLAYED = "edge-message-unreplayed"

#: Shapes that are genuinely exempt from the fail-the-run assertion. Each MUST
#: carry its degraded guarantee in writing — an unnamed exemption is a green
#: pass over an unfolded event, which is exactly what R8 exists to prevent.
#: The shapes and their bounds below are ALSO recorded in the identity decision
#: doc (`docs/epics/2026-09-10-2835-capability-registry/identity-decision.md`,
#: §"Stage 0 findings — the named, bounded non-folded exemptions (#3585)",
#: including the post-Stage-0 `#5380` additions) — the artifact R8 designates
#: for them, so the ruling is findable where a later lane would otherwise read
#: an exemption as drift.
#:
#: ``delete-miss`` (recorded decision: `docs/plans/2026-09-22-unjournaled-mutation-class.md`
#: §"Task 4" — Policy, and #4743's disposition): "already absent" IS the
#: delete's desired end state, so a delete matching 0 rows is legitimately
#: idempotent (a retried delete, or an apply-based replay onto a graph that
#: already holds the node). DEGRADED GUARANTEE: this exemption does not hide an
#: unjournaled CREATION — a journal that deletes an entity it never registered
#: is caught by ``check_consistency``'s entity parity leg: ``_compare_entities``
#: walks the journal's hard-deleted ``{(label, id)}`` set and reports any key
#: still PRESENT in the graph as a ``presence`` divergence
#: (``tortoise/consistency.py``, ``for key in deleted: if key in graph_entities``),
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
    SHAPE_POINT_SUPERSEDED_NO_NEW_ID: (
        "the graph fold treats it as a documented no-op; no state changes on "
        "either side (recorded decision: plan §Task 4 warning policy)"
    ),
    SHAPE_SUPERSEDE_TARGET_DELETED: (
        "the supersede/invalidate fold is DEFERRED to a trailing sweep, so a "
        "target the journal hard-deleted BEFORE the sweep runs is legitimately "
        "gone — live and replay both end with the node absent. DEGRADED "
        "GUARANTEE: exempt only for a SAME-KIND hard delete (`_hard_deleted_any`; "
        "the id-wide fallback is checked too) that the fold actually applied — "
        "a re-creation anchor that suppressed the delete does not tag it — so a "
        "foreign-kind delete sharing the id still refuses, and the "
        "entity-parity leg still compares a re-created node's status. ⚠️ And on "
        "the one-record `apply()` engines (`rebuild`/`recover_from_log`/"
        "`restore`) AND in `check_consistency`'s reference fold this exemption "
        "is currently SILENT: the `_deleted` arm skips before recording, and the "
        "reference fold's `_object_hard_deleted` arm `pass`es by design, so the "
        "record #5380 requires is ABSENT on those four surfaces while "
        "`rebuild_all` records it (pre-existing, #10209 names all four)"
    ),
    SHAPE_EDGE_MESSAGE_MISS: (
        "the write-back's edge is legitimately absent because it was HARD-"
        "DELETED live: the `set` fold runs AFTER every edge-creating pass, so "
        "an edge the journal removed is already gone when its write-back is "
        "replayed. ⛔ The ORIGINAL justification — 'the engines do not fold "
        "`DirectEdgeCreated` (A10 #1048), so the edge is lost by a pre-existing "
        "gap this record does not cause' — is RETIRED **for `rebuild_all`**, "
        "which now folds it (see `direct_edge_created_events` in "
        "projection/__init__.py). ⚠️ It is NOT retired for the one-record "
        "`apply()` engine (`rebuild(log)` / `recover_from_log`), which has no "
        "pass-2b and keeps `DirectEdgeCreated` in `_APPLY_WARN_ONLY_TYPES` — so "
        "ON THAT LEG a plain direct edge is still legitimately absent for a "
        "missing-creation-path reason. On a `rebuild_all` run, every remaining "
        "occurrence is evidence of a REAL divergence — a producer whose edge "
        "was never journaled, or an endpoint-kind leg that dropped it — so "
        "this exemption must never be read as licence to ignore one. "
        "DEGRADED GUARANTEE: the miss is still REPORTED (the call "
        "site warns with src/tgt/rel; the record names the edge), and "
        "`rebuild_all`'s post-replay dressed-edge comparison reports the "
        "aggregate drop (#5380). Adding a fifth slot here does not relax this: "
        "the edge-message fold owns no edge creation"
    ),
    SHAPE_EDGE_MESSAGE_UNREPLAYED: (
        "a `clear` record whose scope/ids the fold cannot map to a live query "
        "is NOT replayed. Refusing would make a recovery run un-runnable for a "
        "journal a future producer could legitimately extend. DEGRADED "
        "GUARANTEE: the record is still emitted (with the offending scope), "
        "and the rebuilt graph may keep seeds the live graph dropped — "
        "reconcile the producer before relying on the rebuilt edge state"
    ),
    SHAPE_OBJECT_SUPERSEDED_UNREGISTERED: (
        "#3590 D2 as AMENDED on #10204 (owner decision, #3590 comment "
        "6453059051): an `ObjectSuperseded` the journal cannot ADDRESS. The "
        "bound is now about ADDRESSES, not about a name: the record must carry "
        "at least one address — a `name`, an `id`, or both — and the journal "
        "must have established NONE of the addresses it carries. NAME sources: "
        "`ObjectRegistered`, an `EntityMutated` state op's `SET`ting the merge "
        "key — ⛔ ANY of `rename`/`restatus`/`revise`/`supersede`, NOT a rename "
        "alone (`journal_object_established` reads `state.name` for every op in "
        "`_ENTITY_MUTATION_STATE_OPS`; a rename-only illustration under-states "
        "the source and reads as a `restatus`-carried name being exempt — the "
        "FAIL-OPEN direction) — and an `EventRecorded` mention stub. "
        "ID sources: a "
        "registration's id, a mention stub's DETERMINISTIC id, a state op's "
        "TARGET id. An address the journal DID establish is a real target, so "
        "that record keeps REFUSING — but ⛔ the cause is a DISJUNCTION, and "
        "`object-superseded-miss` is NOT a synonym for \"a genuine "
        "divergence\" (#10211 review rounds 5-7). An ESTABLISHED address does "
        "not imply a creation (a state op's `SET` — its target id, or a name it "
        "writes, whatever the op — with a target that never existed, are "
        "addresses the journal holds without creating a node; ⚠️ a mention "
        "stub's deterministic id is NOT "
        "one of these — the stub is MERGEd `ON CREATE SET s.id=$id`, so it DOES "
        "create the node, and a later registration re-ids it, which is the "
        "collapse/rename limb), and one "
        "class diverges on NEITHER side: a target the journal registered and "
        "then HARD-DELETED, superseded by NAME (no `id`), refuses here because "
        "the anchored `supersede-target-deleted` bound reads the record's own "
        "id — live folded nothing either, so the refusal is conservative rather "
        "than a divergence. Name-keyed delete recognition is `#10224` (it must "
        "not exempt on duplicate names). "
        "Because nothing could have produced the address, live folded nothing "
        "either and live/replay end in the SAME state — so this is NOT the tier "
        "of the neighbouring `edge-message-miss` exemption, which still "
        "absorbs a missing-creation-path absence on the one-record `apply()` "
        "leg; for any journal "
        "whose establishing lines are intact this exemption tolerates NO "
        "divergence at all (see the RESIDUAL for the case where the lines are "
        "not intact). "
        "⛔ DELIBERATE DATA-INTEGRITY DECISION, not merely a rule — the standard "
        "a permanent skip must meet (Causet's bar, recorded on #3590 comment "
        "6453059051): a record nothing can address is skipped "
        "PERMANENTLY, so the skip is recorded (this entry), the decision is "
        "recorded where the gate is read (`identity-decision.md`), and the "
        "loss is bounded to a supersede that could not have landed. The "
        "deciding reason is AX, not convenience (#10183): a refusal must name "
        "what the caller can DO, and for a target the journal never "
        "established there is no creation to add, nothing out of order, no "
        "producer to journal — retrying reproduces it. No remedy ⇒ no refusal. "
        "DEGRADED GUARANTEE: what is lost is the supersede a journal could not "
        "address — no entity is invented on either side, the miss is still "
        "RECORDED and reported by the run boundary (an exemption still "
        "requires the record, #5380), and the exemption covers ONLY the "
        "unaddressable case: ANY established address (a registered/renamed/"
        "mentioned name, or a registered/stub/state-op id) keeps refusing as "
        "`object-superseded-miss`, so a supersede that could have landed never "
        "becomes skippable; a hard-deleted target keeps its own anchored "
        "`supersede-target-deleted` bound; an established-then-deleted address "
        "is NOT absorbed; and a record carrying NO address at all is refused "
        "as `object-superseded-no-address`, never exempted here. ⚠️ RESIDUAL: the "
        "predicate reads the JOURNAL, so it cannot tell \"never established\" "
        "from \"the establishing line is not in the journal\" — and the "
        "mechanisms include: a DAMAGED line (the #5612 salvage SKIPS it "
        "and COUNTS it); a TORN trailing line (counted as "
        "`torn_trailing_count` on the restore/backup report); a line that is "
        "PRESENT and parses but whose discriminator is unusable (a non-string "
        "`type`), which the fold's malformed-envelope guard skips and — on the "
        "replay engines, which open no malformed-envelope collector — counts "
        "NOWHERE (only `recover_from_log` tallies it, as `malformed_skipped`); "
        "and an establishing line that was NEVER WRITTEN — an unjournaled "
        "capture SDK or a legacy raw producer, the class `commit_ops` journals "
        "a supersede for with a synthesised id and no registration — which "
        "also leaves NO count anywhere, only this exempt record. On every "
        "one of them, live folded "
        "the supersede and replay does not, so the \"live folded nothing "
        "either\" claim holds for a journal-faithful, untorn writer only. That "
        "residual is pre-existing for the name limb and the amendment applies "
        "it to the id spelling"
    ),
}


def classify_terminalizer_miss(
    event_type: str,
    *,
    has_successor: bool = False,
    target_deleted: bool = False,
    object_address: str | None = None,
) -> str:
    """The failure SHAPE for a Point/Object terminalizer that matched 0 rows.

    The ONE classifier the graph replay engines (``rebuild_all``,
    ``apply_journal_point_restamp``) and the reference fold
    (``consistency._fold_journal``) share, so the same journal yields the same
    disposition on every surface — the identity decision's own closing
    requirement (``docs/epics/2026-09-10-2835-capability-registry/
    identity-decision.md`` §"Stage 0 findings").

    ``has_successor`` is consulted ONLY for ``PointSuperseded``, and it is
    ``bool(ev.get("new_id"))`` — NEVER ``corrected_by``. The two fields belong
    to different folds (``_fold_point_superseded`` reads ``new_id`` alone;
    ``_fold_point_invalidated`` reads ``corrected_by`` alone), so reading
    ``corrected_by`` here would let a supersede with no ``new_id`` escape the
    records the fold actually makes.

    ``target_deleted`` means the journal hard-deleted the target — the
    caller's decision, from the anchor-gated ``hard_deleted_pairs`` map (or,
    in the reference fold, its ordered ``anchors`` boundary as an additional
    exemption). This module has no journal, so it cannot decide it.

    ``object_address`` is the sibling caller-decision for ``ObjectSuperseded``
    only (#3590 D2 as amended on #10204): one of :data:`OBJECT_ADDRESS_NONE`,
    :data:`OBJECT_ADDRESS_UNESTABLISHED`, :data:`OBJECT_ADDRESS_ESTABLISHED`,
    as answered by the JOURNAL. The bound is about ADDRESSES, not about a name:
    a record carrying at least one address whose addresses the journal
    established NONE of is exempt; a record carrying NO address is refused
    under its own shape (nothing in it is checkable, so "never established" is
    unfalsifiable); a record carrying an address the journal DID establish is
    refused as a genuine miss. ⛔ The legacy ``name_unregistered`` boolean is
    GONE — it encoded the retired "a name is the least identity it must carry"
    clause, and a name-only reading exempts a supersede that could have landed.
    Also a journal question this module cannot answer — passed in for the same
    reason as ``target_deleted``, and it must NOT be inferred from the graph,
    because a live-holder probe is exactly the ambiguity the exemption
    excludes. It is ignored for every other ``event_type``; ``None`` (the
    default) is the fail-CLOSED answer, so a caller that forgets it refuses.

    The default arm is fail-CLOSED: a terminalizer type outside the four
    named below returns :data:`SHAPE_UNCLASSIFIED_TERMINALIZER`, which is NOT
    in :data:`EXEMPT_SHAPES`.
    """
    if event_type == "PointSuperseded":
        if not has_successor:
            return SHAPE_POINT_SUPERSEDED_NO_NEW_ID
        if target_deleted:
            return SHAPE_SUPERSEDE_TARGET_DELETED
        return SHAPE_POINT_SUPERSEDED_MISS
    if event_type == "PointInvalidated":
        if target_deleted:
            return SHAPE_SUPERSEDE_TARGET_DELETED
        return SHAPE_POINT_INVALIDATED_MISS
    if event_type == "PointRetracted":
        # Never exempt: a retraction tombstones the node, so a 0-row match is
        # a genuine miss whatever the target's hard-delete state.
        return SHAPE_POINT_RETRACTED_MISS
    if event_type == "ObjectSuperseded":
        # `target_deleted` FIRST: it has its own anchor-gated bound and must
        # not be absorbed by the newer exemption (#3590 D2).
        if target_deleted:
            return SHAPE_SUPERSEDE_TARGET_DELETED
        # #10204: the unrepairable case gets its OWN label, because today it
        # shares `object-superseded-miss` with a genuine divergence and a
        # caller cannot tell which one it is looking at (the #9202 defect
        # shape). It keeps REFUSING — it is deliberately NOT in EXEMPT_SHAPES.
        if object_address == OBJECT_ADDRESS_NONE:
            return SHAPE_OBJECT_SUPERSEDED_NO_ADDRESS
        if object_address == OBJECT_ADDRESS_UNESTABLISHED:
            return SHAPE_OBJECT_SUPERSEDED_UNREGISTERED
        return SHAPE_OBJECT_SUPERSEDED_MISS
    return SHAPE_UNCLASSIFIED_TERMINALIZER


#: The dispositions R8 defines. Both FAIL the run; the label only records how
#: the fold left the target's state. Only ``refused`` is produced today —
#: ``disposition`` defaults to it and NO ``record_non_folded`` call site passes
#: the keyword, so ``flagged`` names a fold that records the gap and then
#: proceeds, which no site does yet.
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
            f"{named}{more}. {refusal_remedy(self.refused)}"
        )


#: The two remedies a refusal can carry (#10183's third part). Each is ONE
#: sentence, and `refusal_remedy` is their ONE home — the raised
#: :class:`NonFoldedEventsError` and `check_consistency`'s `non-folded` action
#: both read it, so the two surfaces cannot give one record contradictory
#: instructions (which they did before #10204: the abort was split by shape
#: while the health gate's action still prescribed the retired repair).
_RECONCILE_REMEDY = (
    "Reconcile the journal (a missing creation event, an out-of-order append, "
    "or an unjournaled producer) before relying on this graph."
)
#: The no-address shape's ACTUAL repair (#10183's third part), and the ONE
#: home for it. The per-record receipt in `projection/_object_supersede_miss_detail`
#: renders this same instruction, so the run-boundary remedy and the per-record
#: detail cannot drift into two different instructions for one record — the
#: defect class this module's remedies exist to remove, one level down.
#: ⚠️ What the pins actually hold (#10211 review round 2): one asserts the
#: RENDERED per-record `detail` contains this constant, and one asserts the
#: boundary sentence interpolates it. A relationship between the two constants
#: alone is NOT the tie — measured, drifting the receipt left every test green.
NO_ADDRESS_INSTRUCTION = (
    "supply the target's name or id (fix the producing write, or amend the "
    "journal line)"
)
_NO_ADDRESS_REMEDY = (
    "A record of shape "
    f"`{SHAPE_OBJECT_SUPERSEDED_NO_ADDRESS}` has neither a `name` nor an `id` "
    "that is a non-empty string, so there is no creation THIS RECORD names "
    "to add and no ordering it reports "
    # ⛔ SCOPED TO THE RECORD, not to the journal (#10211 review round 4): a
    # record with no address establishes NOTHING about the journal — its target
    # is unknown, so a missing creation for that unknown target is exactly what
    # cannot be ruled out. "the only repair is to fix the producing write" even
    # concedes that lines can be missing. Claiming "nothing out of order" for
    # the journal was an inference this record cannot support.
    # ⛔ "ITS repair", not "the only repair": `refusal_remedy` emits BOTH
    # sentences for a MIXED set, and an unscoped exclusivity claim then tells
    # the caller to reconcile the journal AND that reconciling it is not the
    # repair — the contradiction this split exists to end.
    f"— its repair is to {NO_ADDRESS_INSTRUCTION} and re-run."
)
#: The MISS shape's SECOND sentence (#10211 review round 8). "Reconcile the
#: journal" is the right instruction for the *divergence* half of the miss
#: shape, but it is WRONG for case (3) of the per-record receipt in
#: ``projection.rebuild_all`` — a record whose carrier the journal HARD-DELETED
#: under an ``id`` the record does not carry (it carries the NAME only). Nothing
#: is out of order there and no creation is missing: the record simply cannot be
#: recognised by the anchored delete bound, which reads only the record's own
#: ``id``. The receipt already says so ("not to reconcile the journal", #10224),
#: so leaving the run boundary prescribing reconcile would print two
#: contradictory instructions for ONE record — the exact defect
#: :func:`refusal_remedy` exists as the single home to remove, one level up.
#: ⛔ SCOPED to the sub-case, not swapped in for the reconcile sentence: the
#: miss shape also covers genuine divergences, which reconcile DOES repair. Both
#: sentences therefore appear together rather than one displacing the other.
MISS_NAME_ONLY_DELETED_INSTRUCTION = (
    "If the record carries the target's NAME only and the journal hard-deleted "
    "that target, carry the deleted target's id instead (#10224)."
)
_MISS_NAME_ONLY_DELETED_REMEDY = (
    "For the conservative sub-case — a name-only record over a target the "
    f"journal hard-deleted — {MISS_NAME_ONLY_DELETED_INSTRUCTION}"
)


def refusal_remedy(refused) -> str:
    """The third part of a refusal: what the caller can actually DO.

    ⛔ THREE limbs share the fail-the-run set and they do NOT share a remedy
    (#10183's admission test, #10204, #10211 review round 8):

    * a genuine divergence — the journal established the target and the fold
      missed it — is REPAIRABLE, and "reconcile the journal" names the three
      ways that happens;
    * a record carrying NO address at all is not: there is no creation to add,
      nothing out of order, and no producer to journal, so prescribing
      "reconcile the journal" is a remedy the caller cannot perform. Its real
      remedy is to SUPPLY the address, which is why it is still allowed to
      refuse rather than being exempted;
    * the `object-superseded-miss` shape carries BOTH halves above — a genuine
      divergence AND a CONSERVATIVE sub-case (a name-only record over a target
      the journal HARD-DELETED under an id the record does not carry) that
      reconciling does not repair. So the miss shape receives the reconcile
      sentence **joined by** the deleted-target-id sentence, never one swapped
      for the other: the divergence half is still repaired by reconciling, and
      the conservative half still needs the id.

    So the reconcile prescription is emitted for every shape EXCEPT a refused
    set that is solely the no-address shape; such a set never prescribes a
    repair that does not exist — and a MIXED set carries every applicable
    sentence. ⚠️ The miss limb is necessarily SET-scoped: `refused` is a set of
    records whose causes are not distinguishable from the shape alone, so this
    home cannot emit the deleted-target-id sentence for only the conservative
    ones. That is why the per-record receipt in `projection.rebuild_all`
    enumerates the same three causes and names case (3) itself — the two homes
    must agree about the sub-case, not merely coexist (`#10224`).
    ⚠️ NOT "only when a record could be reconciled" (#10211 review round 7):
    `unknown-event-type`, `unimplemented-op` and `unknown-op` also receive the
    reconcile sentence, and nothing in them is reconcilable — the repair is to
    upgrade the replayer or the writer. That is a PRE-EXISTING AX gap on a
    different axis, filed as `#10225` rather than absorbed by this split.

    ``refused`` is any iterable of :class:`NonFoldedEvent` (the caller passes
    the REFUSED subset, never the exempt one: an exempt record prescribes no
    repair at all). An EMPTY set returns no remedy rather than the reconcile
    sentence — there is no record to reconcile, and prescribing one would be
    the same "a remedy the caller cannot perform" defect this splits on.
    """
    shapes = {e.shape for e in refused}
    if not shapes:
        return ""
    parts = []
    if shapes != {SHAPE_OBJECT_SUPERSEDED_NO_ADDRESS}:
        parts.append(_RECONCILE_REMEDY)
    if SHAPE_OBJECT_SUPERSEDED_MISS in shapes:
        # #10211 review round 8: the miss shape has TWO causes and only one of
        # them is reconciled. Emitting the reconcile sentence alone contradicts
        # the per-record receipt's case (3) for the same record. JOINED, not
        # swapped — see this function's docstring for the limb and why the
        # sentence is set-scoped rather than per-record.
        parts.append(_MISS_NAME_ONLY_DELETED_REMEDY)
    if SHAPE_OBJECT_SUPERSEDED_NO_ADDRESS in shapes:
        parts.append(_NO_ADDRESS_REMEDY)
    return " ".join(parts)


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


# ── The identity-skip collector (#8702) ───────────────────────────────────
#
# A SKIP is not a refusal. R8's set above records a record the fold could not
# resolve to a node and FAILS the run; a skip is the #7406 boundary's deliberate
# degrade — the fold drops a record whose identity the store cannot take (a NUL
# or lone-surrogate-bearing id) rather than aborting a replay that has already
# wiped the graph. That posture is correct and is NOT changed here: a corrupt
# journal must not make the recovery gate un-runnable on every retry.
#
# What it needs is ACCOUNTING. Before this, the only trace of a skip was a log
# line, so `recover_from_log`'s `applied += 1` counted a dropped record as
# REPLAYED and reported a complete, faithful recovery over a journal from which
# it had dropped a record (#8702). The same context-var shape as the R8
# collector, for the same reason: a fold reached deep inside a run records here
# without a signature change at every call site.

#: The identity was ABSENT (``None``/``""``) — nothing to merge on.
IDENTITY_ABSENT = "absent"
#: The identity is PRESENT but the store cannot take it as a parameter (a
#: non-``str``, or a ``str`` carrying a NUL / lone surrogate). The cause #8702
#: exists to name: it is not "missing".
IDENTITY_UNWRITABLE = "present-but-unwritable"


def identity_absent(value) -> bool:
    """True when the identity is NOT THERE: absent (``None``) or empty (``""``).

    ⛔ PRESENCE, not truthiness (#8840 review P3). ``if not value`` sends ``0``,
    ``False``, ``[]`` and ``{}`` down the ABSENT branch, so a record whose id IS
    present is reported as "carries NO point id at all" — the same #8018 class
    this module exists to close (a report naming a cause that is false for the
    record it describes). Those values are PRESENT and the store still refuses
    them; they are classified as unwritable and their shape is named.
    """
    return value is None or (isinstance(value, str) and not value)


def _unwritable_shape(value) -> str:
    """Name WHY the store refuses this PRESENT value (the true cause).

    ``_writable_id`` refuses three shapes: a non-``str``, a ``str`` carrying a
    NUL, and a ``str`` carrying a lone surrogate. A message that asserted
    "NUL/lone-surrogate bearing" for all of them was FALSE for the non-``str``
    class — a live journal shape (``#331`` review r4 kept ``isinstance(..., str)``
    precisely because non-str ids occur) — so the shape is named here, in ONE
    home, and both the log line and ``reason`` read it (#8840 review P3).

    A FOURTH shape comes from ``_writable_container_id``, which adds the BLANK
    refusal for the SESSION-ID gate (#9599) — the gate BOTH session folds apply
    (``_link_session`` for a ``contains_session`` field, ``_fold_session_recorded``
    for a ``SessionRecorded`` id). It must be named here too: without this branch
    a whitespace-only id fell through to the surrogate message and told the
    operator the value carried an encoding corruption it does not have — the
    #8018 "a cause that is false for the record it describes" class, and it
    shipped into ``consistency``'s returned ``reason`` as well (#9553 review).
    ``_writable_id`` itself still ADMITS a blank on purpose (it gates entity
    NAMES, where widening was measured harmful and reverted, #9599), so the two
    predicates are not in conflict: this function is MESSAGE-ONLY (its sole call
    site is ``IdentitySkip.describe``).
    """
    if not isinstance(value, str):
        return f"it is a {type(value).__name__}, not a str"
    if "\x00" in value:
        return "it carries a NUL"
    if not value.strip():
        return "it is BLANK (whitespace only) — no indexed lookup matches it (#9599)"
    return "it carries a lone surrogate"


@dataclass(frozen=True)
class IdentitySkip:
    """One identity the fold could not write — and whether the RECORD died.

    ``state`` distinguishes the two causes so a report states the TRUE one —
    #8702's second half is that "missing point id" was reported for an id that
    is present, which sends an operator to look in the wrong place.

    ``drops_record`` is the GRANULARITY (#8840 review P1). This collector sees
    every refused identity, and most of them are nested: a healthy ``PointAdded``
    whose ``aboutEntities`` entry or ``extractedFrom`` URL is refused still
    writes its node — only the edge/stub is lost. A replay accounting that read
    *any* entry as "this record was dropped" therefore under-counted ``applied``
    and could flip ``recovered`` to ``False`` and WIPE a replay that succeeded.
    Only the creation anchors — the sites where the fold ``return``/``continue``s
    and the record is genuinely not written — pass ``drops_record=True``.
    """

    what: str
    value: object = None
    label: str = "identity"
    event_id: str | None = None
    where: str = ""
    drops_record: bool = False

    @property
    def state(self) -> str:
        return IDENTITY_ABSENT if identity_absent(self.value) else (
            IDENTITY_UNWRITABLE)

    def describe(self) -> str:
        """The cause, in the record's own terms (never "missing" for a present id)."""
        if identity_absent(self.value):
            return f"{self.what} carries NO {self.label} at all"
        return (f"{self.what} carries a {self.label} {self.value!r} that "
                f"is PRESENT but not writable ({_unwritable_shape(self.value)})")

    def __str__(self) -> str:  # pragma: no cover - formatting only
        where = f"{self.where}: " if self.where else ""
        loc = f" (event_id={self.event_id!r})" if self.event_id else ""
        return f"{where}{self.describe()}{loc}"


_IDENTITY_SKIPS: ContextVar[list | None] = ContextVar(
    "tortoise_identity_skip_set", default=None)


@contextmanager
def collect_identity_skips():
    """Open an identity-skip run. Yields the (mutable) list to inspect.

    Nested runs are independent, exactly like :func:`collect_non_folded`.
    """
    entries: list[IdentitySkip] = []
    token = _IDENTITY_SKIPS.set(entries)
    try:
        yield entries
    finally:
        _IDENTITY_SKIPS.reset(token)


def record_identity_skip(what: str, value, label: str = "identity", *,
                         event_id: str | None = None,
                         where: str = "",
                         drops_record: bool = False) -> IdentitySkip:
    """Record one identity skip into the active run (if any).

    The LOG LINE is not the record (R8's rule, applied to skips): a caller that
    must account for a dropped record reads the returned entry, never the log.
    Outside a run this is a no-op and the call site's own warning remains the
    only signal — a one-record ``apply()`` has no run boundary.
    """
    entry = IdentitySkip(what=str(what), value=value, label=label,
                         event_id=event_id, where=where,
                         drops_record=bool(drops_record))
    entries = _IDENTITY_SKIPS.get()
    if entries is not None:
        entries.append(entry)
    return entry


# ── Malformed ENVELOPES: the OTHER record-drop channel (#8843) ─────────────
#
# #8702 gave a dropped record a record-drop signal, but only for identity
# refusals. ``apply()`` ALSO drops records that carry no identity at all — the
# ``#331`` guard (a missing/non-string ``type``) and the ``#325`` guard (a
# non-dict ``point`` on a creation record). Both ``return`` normally, so
# ``recover_from_log``'s per-record ``else`` arm incremented ``applied`` and the
# only trace was a ``logger.warning``. MEASURED at 521a3fc78: a journal of
# ``[healthy PointAdded, {"event_id": "ev-x", "type": None}]`` reported
# ``replayed 2 events`` (``applied == 2``) over a graph holding ``db_points == 1``,
# with no bucket naming the dropped record. ⛔ Note what is and is not wrong there:
# ``log_points`` is 2 and ``applied`` is 2, so the arithmetic
# ``reads = applied + identity_skipped + torn + refused`` (documented at that
# head) BALANCED — the falsehood was one level down, in ``applied`` itself, which
# counted a dropped record as a replay and so made the sum agree with a
# recovery that had not happened. That is the false PASS this channel closes:
# a reconciliation is only as honest as its largest term.
#
# ⛔ WHY THIS IS NOT ``identity_skipped``. Folding it there would conflate two
# causes that send an operator to different places: an identity skip means the
# record was well-formed and its key was unwritable; a malformed envelope means
# there IS no record to key. The bucket is therefore its own, and the shape is
# named in :meth:`MalformedEnvelope.describe` — the #8018 class this module
# already guards against.
#
# ⛔ WHY THE GUARD ITSELF RECORDS. On the replay-ACCOUNTING path the predicate
# has one home — ``apply()``'s two guards — and the replay loop must not
# re-derive it: a second reader of "what is a malformed envelope" drifts from
# the first (the repo's "ONE list" rule). ⚠️ Scope, stated exactly rather than
# as "ONLY there" (review cycle 3 on "only here", completed cycle 4): the same
# two ``isinstance`` guards also live in ``_apply_one`` — the in-memory fold
# behind ``fold()`` / ``InMemoryProjection`` / ``rebuild()`` — and in
# ``consistency._fold_journal``, the reference view ``check_consistency``
# compares the graph against. NEITHER records, because neither has an accounting
# channel. So the invariant is "one RECORDER per accounting path", not "one
# guard in the package": a new engine that needs this counted reads
# ``collect_malformed_envelopes`` and records from its own guard, and never
# calls a re-derived test. A fold reached deep inside a run records here without
# a signature change at every call site — the same context-var shape #8702's
# identity collector uses.

#: The record's normalised discriminator is unusable: ``type`` is missing, or it
#: is present and not a ``str`` (the ``#331`` guard). WHICH of the two is the
#: record's own fact and must not be guessed — see ``field_present``.
MALFORMED_MISSING_TYPE = "missing/non-string type"
#: A creation record's ``point`` payload is missing or is not a dict (the
#: ``#325`` guard).
MALFORMED_NON_DICT_POINT = "non-dict point"
#: The ENVELOPE was well-formed and a NESTED payload shadowed it (review cycle
#: 4). ``apply()`` classifies on ``_norm(event)``, which is
#: ``{**ev, **ev["point"]}`` — so a ``point`` payload carrying its own ``type``
#: key replaces a perfectly good envelope ``type``, and the record is dropped for
#: a discriminator the envelope never had. Reporting that as "the envelope is
#: malformed" names a cause that is FALSE for the record (the #8018 class, which
#: is the whole reason this channel exists), so it gets its own cause and its own
#: sentence. The SAME shadowing the module already reads around for
#: ``contains_session`` (:meth:`FalkorProjection.apply`), one field over.
MALFORMED_SHADOWED = "shadowed by a nested payload"


#: How much of a refused VALUE the report may render. A malformed ``point``
#: payload is a record's whole point, embedding included — an unbounded ``repr``
#: turned one operator-facing ``reason`` into 7,226 characters across three
#: exemplars (review cycle 5). Long enough to recognise the value, short enough
#: to read.
_VALUE_REPR_LIMIT = 120


def _short(value) -> str:
    """``repr(value)``, bounded — a report must stay readable to be a report."""
    text = repr(value)
    if len(text) <= _VALUE_REPR_LIMIT:
        return text
    return f"{text[:_VALUE_REPR_LIMIT]}… ({len(text)} chars)"


@dataclass(frozen=True)
class MalformedEnvelope:
    """One record ``apply()`` DROPPED because it could not be read.

    (Not "because its discriminator is unreadable": TWO of the four causes —
    a non-dict ``point`` on a ``PointAdded``/``OperatorAdded``, and a nested
    ``point`` shadowing the payload — leave the discriminator perfectly
    readable. The payload is what is bad, and the class docstring must not
    repeat the false aggregate sentence the reason clause used to carry;
    review cycle 7.)

    Distinct from :class:`IdentitySkip` by CAUSE, not by severity: a record
    with no usable ``type`` (or a creation record whose ``point`` is not a
    dict) carries no identity to attribute, so nothing here can be mistaken for
    "the key could not be written".

    ``what`` is the record family as the guard saw it and is RENDERED, so the
    reason clause says WHICH family was dropped (the sibling
    :meth:`IdentitySkip.describe` does the same). ``cause`` is one of the
    ``MALFORMED_*`` constants.

    ⛔ ``field_present`` is the FACT the guard read from the record, and it is
    NOT inferred from ``value`` (review cycle 1). ``ev.get("type")`` returns
    ``None`` both for an ABSENT key and for a present ``type: null``, so a
    report that branched on the value alone said "carries no 'type' at all"
    about a record whose ``type`` key was right there — the #8018 class, at the
    very site this channel exists to keep honest. The same shape applies to
    ``point``: a missing key and a ``point: "…"`` are different records with
    different repairs.

    ⛔ ``envelope_value`` exists so :data:`MALFORMED_SHADOWED` can name BOTH
    values. Without it the report can only say "not a str", which is true but
    leaves the operator editing the wrong line.

    ⛔ ``value`` is rendered through :func:`_short`, never with a bare ``repr``
    (review cycle 5). A malformed ``point`` payload is a record's whole point
    — id, content, and a 384-float embedding — so an unbounded ``repr`` made the
    operator-facing ``reason`` **7,226 characters** from three exemplars, and
    the collector then held a reference to every such payload for the whole run.
    A report has to be readable to be a report.
    """

    what: str
    cause: str
    field: str = "type"
    field_present: bool = True
    value: object = None
    envelope_value: object = None
    event_id: str | None = None
    where: str = ""

    def describe(self) -> str:
        """The cause, in the record's own terms (never "missing" for a present field).

        The FIELD NOUN comes from ``self.field``, not from ``cause``: both call
        sites name it (``"type"`` / ``"point"``) and a report that derived the
        noun from the cause instead would silently describe the wrong field for
        any other pairing — text that looks authoritative while being about
        something else (review cycle 2).
        """
        if self.cause == MALFORMED_SHADOWED:
            if self.field == "point":
                # ⛔ Its OWN sentence, not the type template (review cycle 5).
                # There is no "envelope point value" to compare against: the
                # collision is a nested KEY inside the payload, so the template
                # printed the whole payload dict as the envelope's value and
                # compared two things that are not the same quantity.
                # ⛔ "the envelope record is well-formed" is asserted ONLY when
                # the envelope really carries a usable discriminator (review
                # cycle 9): with no `type` of its own the record is dropped for
                # a payload collision AND an absent discriminator, and claiming
                # the envelope is fine is then simply false.
                if isinstance(self.envelope_value, str):
                    _tail = "the envelope record is well-formed"
                else:
                    _tail = ("the envelope record is ALSO dropped for having "
                             "no usable 'type' of its own "
                             f"({_short(self.envelope_value)})")
                return (f"{self.what}: the 'point' payload carries a nested "
                        f"'point' key ({_short(self.value)}), and `_norm` "
                        f"splices it over the payload — so the fold reads "
                        f"that as the payload itself; {_tail}")
            return (f"{self.what}: the NESTED payload's '{self.field}' "
                    f"({_short(self.value)}) shadows the envelope's "
                    f"({_short(self.envelope_value)}), so the fold reads no "
                    f"usable discriminator — the envelope itself is "
                    f"well-formed")
        if self.cause == MALFORMED_MISSING_TYPE:
            if not self.field_present:
                return f"{self.what} carries no '{self.field}' key at all"
            return (f"{self.what}'s '{self.field}' is {_short(self.value)} — a "
                    f"{type(self.value).__name__}, not a str — so the "
                    f"fold has no discriminator to select on")
        if not self.field_present:
            return f"{self.what} has no '{self.field}' key at all"
        return (f"{self.what}'s '{self.field}' payload is {_short(self.value)} "
                f"— a {type(self.value).__name__}, not a dict — so there is "
                f"nothing to fold")

    def __str__(self) -> str:  # pragma: no cover - formatting only
        where = f"{self.where}: " if self.where else ""
        loc = f" (event_id={self.event_id!r})" if self.event_id else ""
        return f"{where}{self.describe()}{loc}"


_MALFORMED_ENVELOPES: ContextVar[list | None] = ContextVar(
    "tortoise_malformed_envelope_set", default=None)


@contextmanager
def collect_malformed_envelopes():
    """Open a malformed-envelope run. Yields the (mutable) list to inspect.

    Nested runs are independent, exactly like :func:`collect_identity_skips`.
    """
    entries: list[MalformedEnvelope] = []
    token = _MALFORMED_ENVELOPES.set(entries)
    try:
        yield entries
    finally:
        _MALFORMED_ENVELOPES.reset(token)


def record_malformed_envelope(what: str, cause: str, *,
                              field: str = "type",
                              field_present: bool = True,
                              value=None,
                              envelope_value=None,
                              event_id: str | None = None,
                              where: str = "") -> MalformedEnvelope:
    """Record one malformed-envelope drop into the active run (if any).

    Same contract as :func:`record_identity_skip`: the LOG LINE is not the
    record, and outside a run this is a no-op, so a one-record ``apply()`` keeps
    its existing warning as the only signal.

    ``field_present`` MUST come from the RAW ENVELOPE — ``"type" in raw`` —
    never inferred from ``value`` (see :class:`MalformedEnvelope`) and never
    read off the ``_norm`` splice: inside ``apply()``, ``ev`` is
    ``{**event, **event["point"]}``, so ``"type" in ev`` is TRUE for a record
    whose envelope carries no ``type`` at all. That is precisely the
    present-vs-absent bug this flag exists to prevent, and an earlier draft of
    this very docstring recommended it (review cycle 8) — a new recorder written
    from the wrong advice reintroduces the regression, and no end-to-end pin can
    see a DIFFERENT call site.
    """
    entry = MalformedEnvelope(what=str(what), cause=str(cause),
                              field=str(field),
                              field_present=bool(field_present),
                              value=value, envelope_value=envelope_value,
                              event_id=event_id, where=where)
    entries = _MALFORMED_ENVELOPES.get()
    if entries is not None:
        entries.append(entry)
    return entry


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
