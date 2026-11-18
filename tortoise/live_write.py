"""THE write chokepoint — journal append → record projection → cache fold (#13331).

Epic **#7879** surface **S2** (`docs/epics/2026-10-10-7879-postgres-record/
01-test-design.md`): *"one entry: append the journal event → apply the record
projection → apply/enqueue the cache projection"*, and surface **S1**: *"every
write goes to the truth store first."* Issue **#13331** is hole 3 of `#13210`'s
§7 — new memories did not reach the record at write time.

WHY THIS MODULE EXISTS. Before it, three live doors each performed the same
three steps by hand — `TortoiseSDK._emit_event`, `EventAPI._emit`,
`ConversationMiner._emit_event` — and each spelled the record hand-off with its
own durability and outcome handling. That is the shape S2 names as the defect:
*"a bypass ⇒ a derived cache that is also written to directly is not derived"*.
The steps are now ONE function, so a fourth door cannot add a fourth spelling,
and the ORDER is a property of this module rather than a convention three
callers are trusted to repeat.

──────────────────────────────────────────────────────────────────────────────
WHAT THE ONE TRANSACTION IS, AND WHAT IT CANNOT BE
──────────────────────────────────────────────────────────────────────────────

`docs/architecture/STORAGE-ARCHITECTURE.md` §3: *"One transaction covers the
journal and its projection — no dual write between them."* That sentence is the
design's requirement and it cannot be satisfied literally by this deployment:
the journal is a JSONL FILE and the record is Postgres, so no transaction spans
both (#13210 §7 states it, and this module does not pretend otherwise).

What IS true here, and what the falsifiers measure:

1. **The record's writes are ONE transaction.** The layer rows, the
   `public.journal_event` position row, and — on a cut-over graph — the
   allocator's counter increment are all executed by `record_projection.replay`
   on ONE connection and committed ONCE, by `record_writer.write_live_record`.
   A failure rolls all of them back together: no record row can exist whose
   position row does not.

2. **The disagreement can only be JOURNAL AHEAD OF RECORD.** The append is the
   COMMIT POINT (`#7951`'s journal-ordering contract): the projection is handed
   the append's own ordinal and runs only after the append returned, so a
   record row can never exist for a journal line that is not there. The reverse
   — a line whose projection failed — is the direction `derived =
   replay(journal)` was built to heal.

3. **That disagreement is NAMED, not silent.** Every outcome this function
   RETURNS says whether the record took the write (`LiveWrite.record`), and a
   failure leaves `unprojected` set with the journal position. The named
   disagreement is readable from the two stores without this process's memory:
   `record_projection.journal_coverage` compares the journal's own enumeration
   (`EventLog.position()` or the read-only `position_readonly()`) with
   `public.journal_event`, `python -m tortoise record-reconcile --journal
   <path>` reports it as `unprojected_journal`, and `tools/record_runner.py
   status` already reports the resume point. The repair is one idempotent
   `record_runner apply`.

   ⛔ WHAT "NAMED" DOES **NOT** MEAN, STATED SO THE CLAIM CANNOT DRIFT (#13345
   review F3). The returned `LiveWrite` is *handed to the caller* and it is a
   fact about the write; it is **not** a report any production door reads. All
   three doors discard the value (`api.EventAPI._emit`, `mining.
   ConversationMiner._emit_event`, `sdk.TortoiseSDK._emit_event`), and the one
   thing that IS consumed from the record attempt is the `RecordWrite` the SDK
   door collects through ``record_sink`` for its store decision (#10021). So
   the per-write `unprojected`/`refusal` names the disagreement to a CALLER
   that asks, and tests ask. The PRODUCTION signal for a refused armed write is
   the door's own counter plus its WARNING (`record_writer._count_write_failure`
   / `_report_live_write_refusal`), and the durable signal is the three readers
   above. Making a door branch on `refusal` is #8447's work — `record_writer.
   write_live_record`'s docstring records the same boundary ("Consuming
   ``refusal`` where the store decision is not at stake is #8447's work, not
   this change's") — so do not read this module as claiming a fourth surface.

⛔ **WHY THE APPEND IS NOT WITHHELD WHEN THE RECORD IS NOT READY.** A tempting
"refuse rather than half-write" is to abort the whole write when the record
cannot take it. That would be the FORBIDDEN direction: several doors have
already written the graph by the time they emit (and `_create_entity`'s blocks
still fold before their append), so withholding the journal line would leave a
derived store AHEAD of the truth — the window `#7951` exists to forbid, which no
replay can repair. The truth store never waits for a derived store. So this
module refuses BEFORE the append only when the CALLER has not committed
anything yet, and otherwise it appends and reports — never appends conditionally.

──────────────────────────────────────────────────────────────────────────────
THE PER-GRAPH CUTOVER (#13331 requirement 4)
──────────────────────────────────────────────────────────────────────────────

`record_projection.replay` refuses an ENUMERATED position on a graph that holds
a `public.record_cursor` row (`_refuse_an_enumerated_replay`, #9190) — the row,
not the mode, is the fact. On the live path that refusal is correct and it is
the one the chokepoint reports as a named disagreement; it is not a silent
renumbering. The ORDER a tenant is moved in is the allocator CLI's, and it is
not this module's to restate beyond naming it: `sweep` → `seed --journal <path>`
→ `arm` (per graph) → THEN `TORTOISE_RECORD_ALLOCATE=1` for the box. A graph
holding a cursor row refuses the enumerated replay whether or not that flag is
set, which is exactly why the flag is rolled AFTER every graph is seeded and
armed (`tools/record_allocator_cli.py`'s `CUTOVER_ACK_TEXT` is the authority).

──────────────────────────────────────────────────────────────────────────────
THE DENSE LEG'S ARMING (#13331 requirement 5)
──────────────────────────────────────────────────────────────────────────────

`TORTOISE_RECORD_VECTOR_LEG` stays the deployment switch
(`tortoise/record_vector.py::ARMING_ENV`), and it is NOT made per-tenant here.
What this issue adds is the condition that keeps it honest:
`record_vector.searcher` consults :func:`journal_coverage` for the request's
tenant and keeps the graph branch while any journal position is unprojected —
so a tenant whose backfill is incomplete is not served from a record that does
not hold its corpus. Wiring live writes is what makes that condition reachable
for a FRESH tenant: its positions are consumed at write time with no backfill.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

LOGGER = logging.getLogger(__name__)

__all__ = ["LiveWrite", "write"]

#: The append a caller hands this module. It MUST return the record's journal
#: position (1-based) or ``None`` when the append succeeded but could not PROVE
#: one — the two states `tortoise.log.EventLog.append` documents. ``None`` is a
#: fact, never a cue to invent a number (#9190).
Append = Callable[[], int | None]

#: The record half: `(record, seq) -> RecordWrite | None`. The default is
#: `record_writer.write_live_record`; `TortoiseSDK._emit_event` supplies its own
#: wrapper so the door's import guard and durability read keep ONE home.
Project = Callable[[Mapping[str, Any], int | None], Any]

#: This module's OWN refusal token for the one arm where the record seam never
#: ANSWERED — it raised, or could not be imported, after the append committed.
#: Minted here, never by the seam, which is why :attr:`LiveWrite.armed` can read
#: it and answer ``None`` (unmeasured) rather than ``False`` (#13345 review I2).
PROJECTION_RAISED = "projection_raised"


def _is_durable_decline(outcome: Any) -> bool:
    """Is this the seam's HEALTHY-ARMED DECLINE (a scratch buffer, #9231)?

    ``RecordWrite.applied`` publishes the rule this obeys: *"A consumer must
    branch on ``refusal``, not on ``applied is False``, before calling a decline
    an error."* ``no_durable_journal`` is the M2 session-capture lane — a scratch
    buffer with no journal by design — so nothing a replay could restore is
    missing, and there is no journal line the record failed to take. Reading
    ``applied is False`` alone reported a lost write for every ``capture_session``
    emit on an armed record (#13345 review I1).

    The TOKEN is read from the seam that mints it, never respelled here: a second
    copy of a closed vocabulary is how a rename stops matching silently. The
    import is lazy and swallowed for the same reason the seam itself is imported
    inside ``write``'s ``try`` — an unimportable ``record_writer`` must not raise
    out of a classification. A failed import answers ``False``, the fail-closed
    direction: a decline reported as a loss is a false alarm; a loss reported as
    a decline is a silent gap.
    """
    try:
        from .record_writer import NON_DURABLE_JOURNAL_REFUSAL
    except Exception:
        return False
    return getattr(outcome, "refusal", None) == NON_DURABLE_JOURNAL_REFUSAL


@dataclass(frozen=True)
class LiveWrite:
    """What ONE write did, in the order the contract requires it.

    ``appended`` is always ``True`` on a returned value: an append that raised
    propagates out of :func:`write` (the callers' existing contract — a failed
    append is the truth store's failure, not a record refusal), so a caller
    never receives an outcome that claims a write whose line is missing.

    ``record`` is ``None`` only when the record was UNARMED (no DSN) — the ONE
    case `write_live_record` returns ``None`` for. A DECLINE is a ``RecordWrite``
    like any other armed answer (#8447): the scratch-buffer decline
    (``no_durable_journal``) is ``applied=False`` carrying that token as its
    ``refusal``, and it is NOT an unprojected disagreement — see ``unprojected``.
    It is a ``RecordWrite`` otherwise, whose ``applied`` answers whether the
    record holds the row.

    ``unprojected`` is the named disagreement: ``1`` when the journal holds a
    line the record did NOT take, ``0`` otherwise. A DECLINE is ``0``: a scratch
    buffer has no journal, so no line exists for the record to have missed
    (#13345 review I1). It is deliberately an
    integer over this one record rather than a boolean, so `record_reconcile`'s
    count and this value are the same quantity at different scopes. ⚠️ It is
    returned to the caller and read by no production door — see the module
    docstring's §3 note and #8447; the durable readers are `journal_coverage`,
    `record-reconcile --journal` and `record_runner status`.
    """

    event: Mapping[str, Any]
    seq: int | None
    appended: bool = True
    record: Any | None = None
    folded: bool = False
    refusal: str | None = None
    unprojected: int = 0

    @property
    def applied(self) -> bool:
        """Did the RECORD take this write? ``False`` for unarmed and for refused."""
        return bool(getattr(self.record, "applied", False))

    @property
    def armed(self) -> bool | None:
        """Was the RECORD armed for this write? ``True``/``False``/``None`` (unmeasured).

        ``True`` — the seam ANSWERED, and only an armed call answers: an unarmed
        deployment returns ``None`` from ``write_live_record``, so any
        ``RecordWrite`` held here — APPLIED, any refusal, or the healthy
        ``no_durable_journal`` decline — is an armed record.

        ``False`` — nothing came back and nothing is at stake: the unarmed path.

        ``None`` — **UNMEASURED**, and the third state is the point (#13345 review
        I2). In the ``projection_raised`` arm the seam never answered (it raised,
        or could not be imported), so arming is *unknown*: this deployment may or
        may not hold a DSN. Deriving ``armed`` from ``record is not None`` made
        that arm report ``False`` WHILE ``unprojected == 1`` — "the record was not
        armed" and "the journal holds a line the record did not take" asserted
        together, though the second is precisely why the first cannot be claimed.
        An unknown answer must not be spelled as a measurement — the same rule
        ``JournalCoverage.unprojected_unknown_reason`` enforces one surface over.
        A consumer needing a boolean reads ``record is not None``
        (armed-and-answered) or ``unprojected`` (did a line go untaken); those two
        ARE measured.
        """
        if self.record is not None:
            return True
        if self.refusal == PROJECTION_RAISED:
            return None
        return False

    def as_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "appended": self.appended,
            "record": None if self.record is None else self.record.outcome,
            "refusal": self.refusal,
            "folded": self.folded,
            "unprojected": self.unprojected,
        }


def write(event: Mapping[str, Any], *,
          log: Any | None = None,
          append: Append | None = None,
          projection: Any | None = None,
          durable: bool | None = None,
          tenant: tuple[str, str] | None = None,
          fold: bool = True,
          record_sink: list | None = None,
          project: Project | None = None,
          scope_label: str = "live_write.write",
          ) -> LiveWrite:
    """Append the journal record, project it into the record, then fold the cache.

    ``log``/``append`` choose the journal: pass ``log`` (an `EventLog`-like
    object) and this module calls its ``append``; pass ``append`` instead when
    the door must wrap the call (the SDK counts append failures around it).
    Exactly one is required.

    ``projection`` is the cache fold (``FalkorProjection``), or ``None`` for a
    door that does not fold here. It runs ONLY after the append committed
    (the `#7951` ordering contract) and after the record attempt, because the
    cache is the derived store and must never lead the truth.

    ``fold=False`` lets the SDK's door keep its own fold decision while still
    getting the append→record ordering and the named outcome from here.

    ⛔ A FAILED APPEND PROPAGATES. The callers already turn it into their own
    accounting (`_emit_event` counts `tortoise_journal_write_failures_total`
    and returns ``False``); swallowing it here would make a lost line look like
    a successful write with a pending record, which is the opposite of the
    direction this module guarantees.
    """
    if (log is None) == (append is None):
        raise TypeError(
            "live_write.write needs exactly one of `log=` or `append=` — the "
            "journal append is the commit point, so which callable performs it "
            "must be unambiguous (#13331)")
    if durable is None:
        durable = getattr(log, "durable", True) if log is not None else True

    # ── 1. THE TRUTH. The append is the commit point (#7951). ──────────────
    seq: int | None = (append() if append is not None else log.append(event))

    # ── 2. THE RECORD, on ONE transaction, after the append. ───────────────
    try:
        # ⛔ THE SEAM IS IMPORTED *INSIDE* THIS TRY, not above it (#13345 review
        # P2). A door that passes no `project=` reaches the real
        # `record_writer`, and an import of it can fail (a wheel missing the
        # module, a partially-shipped install, an import-time error in its
        # dependencies) — which is a RECORD fault, so it takes the record
        # fault's rules. Imported above the `try`, that failure raised to the
        # producer for a write the journal had ALREADY committed, and skipped
        # the fold: the write was gone from both derived stores, the exact
        # outcome the comment below says this module prevents. Cost of the
        # guard: one dict lookup on the import path, already cached by CPython.
        if project is None:
            from . import record_writer

            def _default_project(record: Mapping[str, Any],
                                 seq: int | None) -> Any:
                return record_writer.write_live_record(
                    record, durable=durable, seq=seq, tenant=tenant)

            project = _default_project
        outcome = project(event, seq)
    except Exception as exc:
        # ⛔ NEVER RAISE AFTER THE APPEND. The line is committed; a raise would
        # hand the producer an exception for a write that persisted, and would
        # skip the cache fold below — losing the write from BOTH derived stores
        # for a record fault. The disagreement is reported instead, which is the
        # same posture `write_live_record` takes for its own refusals.
        #
        # This arm covers the record SEAM as well as the record CALL: the import
        # above is inside the `try`, so an unimportable `record_writer` lands
        # here as `projection_raised` rather than as an exception out of the
        # chokepoint.
        LOGGER.warning(
            "live_write: the record projection RAISED after the journal append "
            "committed (%s: %s) — the journal holds position %s and the record "
            "does not. This is a NAMED disagreement: `record_projection."
            "journal_coverage` counts it, `python -m tortoise record-reconcile "
            "--journal <path>` reports it as unprojected_journal, and "
            "`tools/record_runner.py apply` heals it idempotently (#13331)",
            type(exc).__name__, exc, seq)
        outcome = None
        raised = True
    else:
        raised = False

    refusal = None if outcome is None else getattr(outcome, "refusal", None)
    # `record is None` = UNARMED: nothing claimed the write, nothing lost. The
    # seam's healthy-armed DECLINE is that same verdict by another route — a
    # scratch buffer has no journal, so there is no line it failed to take
    # (#13345 review I1). Every OTHER `applied is False` IS a line the record did
    # not take, and `refusal` keeps the seam's own token or names the loss.
    unprojected = 0
    if (outcome is not None and not getattr(outcome, "applied", False)
            and not _is_durable_decline(outcome)):
        unprojected = 1
        refusal = refusal or "write_failed"
    if raised:
        unprojected = 1
        refusal = PROJECTION_RAISED

    if record_sink is not None:
        record_sink.append(outcome)

    # ── 3. THE CACHE, last — derived never leads truth. ────────────────────
    folded = False
    if fold and projection is not None:
        # ⚠️ OUTSIDE THE `try` ABOVE, DELIBERATELY (#13345 review, note). Same SHAPE
        # as the record seam's import, opposite RULE: a RECORD fault reports and
        # continues (the journal line is committed and the record is a derived
        # store), while a FOLD fault propagates — `projection.apply` already
        # propagates, and swallowing ITS import failure would leave the caller
        # believing a fold it asked for happened. Do not "close" this arm by
        # catching it without first deciding what `folded` then means.
        from .projection import _live_write_scope

        with _live_write_scope(scope_label):
            projection.apply(event)
        folded = True

    return LiveWrite(event=event, seq=seq, appended=True, record=outcome,
                     folded=folded, refusal=refusal, unprojected=unprojected)
