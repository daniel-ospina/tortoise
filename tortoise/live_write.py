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

   ⛔ ON A CUT-OVER GRAPH THERE IS NO LINE TO DISAGREE WITH (#14002): see
   *THE PER-GRAPH CUTOVER* below — the write is recorded by the RECORD alone,
   so this quantity is `0` and the write's own outcome (`applied`) is the whole
   answer.

3. **That disagreement is NAMED, not silent.** Every outcome this function
   RETURNS says whether the record took the write (`LiveWrite.record`), and a
   failure leaves `unprojected` set with the journal position. ⛔ NOT ON THE
   RECORD-ONLY ARM (#14002): a skipped append has no line, so `unprojected` is
   `0` there by contract and the loss signal is the PAIR
   `appended=False, applied=False` — do not build a "was this write lost?"
   check from `unprojected` alone. The named
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

⚠️ AND THE ONE PLACE IT DOES NOT APPEND IS NOT A REFUSAL (#14002): a CUT-OVER
graph's write never appends, because the record owns the ordinal and there is no
second allocator to agree with. That is the healthy path, not a withheld write —
the RECORD is the truth there, and it is written. The paragraph above still
governs every graph whose record is NOT the allocator.

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

⛔ AND ONCE ARMED, THE JSONL IS OFF THE WRITE PATH (#14002). Every step above
used to leave TWO allocators on the live door — the file append's position and
the counter's allocation — which had to AGREE, and under concurrent writers they
cannot: the journal is ordered by append and the counter by allocation, so two
in-flight writes can be numbered in the opposite order to their lines and the
guard refuses one of them. With 20+ writers that is steady state, not an edge
case: `record.ok: false`, `status: degraded`, and a stream of
`position_divergence` refusals. So this chokepoint now asks
`record_writer.record_owns_position` BEFORE the append, and when the graph is
cut over and armed it does not append at all — ONE number space, the counter's.
Consequences, all of them intended:

* the JSONL stops growing — and it becomes OPTIONAL, not yet an EXPORT. Nothing
  on the write path depends on the file any more, so writing it is a policy
  question; but nothing WRITES it either (there is no drainer, and
  `event_id`/`initiated_by`/`projection_version` have no column, so a line
  rendered from the record would be a different journal — see
  `docs/evidence/14002-record-only-journal/README.md` §5). On a cut-over graph
  the file is a FROZEN PREFIX: the append stops when the BOX's
  `TORTOISE_RECORD_ALLOCATE` is rolled (with the graph's row `active`) — NOT at
  the per-graph `arm`, so the arm→roll window is normal and the file still grows
  in it — and after the roll it keeps whatever it had. Say that, not "export",
  until §A7.8 item 6 and a drainer (§A7.5.6) exist;
* `position_divergence` is unreachable on the production path, while the guard
  itself is untouched — it still refuses every disagreeing ordinal a direct
  caller hands it;
* `unprojected_journal` / `record-reconcile --journal` become the wrong
  question for such a graph (the file is behind the record by construction, and
  an enumerated replay there is refused). The record's own coverage readers are
  the ones to use;
* the ordering guarantee changes hands, and it does not weaken: for that write
  the RECORD's commit is the commit point, so a record row still cannot exist
  for a write that did not land, and the fold still runs last.

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

#: The record half: `(record, seq, *, journal_skipped) -> RecordWrite | None`.
#: The default is `record_writer.write_live_record`; `TortoiseSDK._emit_event`
#: supplies its own wrapper so the door's import guard and durability read keep
#: ONE home.
#:
#: ⛔ THE THIRD ARGUMENT IS NOT OPTIONAL IN PRACTICE (#14002). On a CUT-OVER graph
#: this chokepoint does not append (:func:`record_writer.record_owns_position`),
#: so the record does not receive the journal's ordinal — and the seam cannot tell
#: "no line, the counter owns the number" from the `#9307` "the append could not
#: prove a position" state without being told. A door that supplies its own
#: callable and DROPS the flag turns every write on a cut-over graph into a
#: `position_unverifiable` refusal (counted, WARNed, permanent), which is why
#: `tools/record_wiring_check.py::live_call_sites_forward_the_journal_skip` holds
#: every door to forwarding it.
Project = Callable[..., Any]

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


def _record_owns_position(*, durable: bool, tenant: tuple[str, str] | None,
                          ) -> bool:
    """Does the RECORD own this write's ordinal? Never raises (#14002).

    The chokepoint's ONE use of :func:`record_writer.record_owns_position`: it is
    asked BEFORE the append, so nothing has committed and a raise here would hand
    the producer an exception for a write that has not happened yet — a
    behaviour change on every unarmed deployment the moment the record module
    failed to import. So the import AND the call are both guarded, and every
    failure answers ``False`` (= append, today's path).

    ``False`` is the fail-open direction and it is chosen deliberately: the
    append is the durable copy, so the worst case of a failed measurement is the
    pre-#14002 behaviour (both allocators, guarded by the divergence refusal),
    while assuming the record owns it would skip the line for a graph whose
    allocation then refuses — a write in neither store.
    """
    try:
        from . import record_writer

        return record_writer.record_owns_position(
            tenant=tenant, durable=durable)
    except Exception:  # noqa: BLE001, RUF100
        return False


def _record_armed() -> bool:
    """Is a record DSN configured? ``False`` on every doubt (#14222 review P1-3).

    The same question `write_live_record` asks first, asked HERE because the
    deferral decision needs it: a queued write on an unarmed record is a write
    whose fold will answer "nothing was armed", so the ack would have been
    deferred for a projection nobody was going to run. Guarded and fail-closed to
    INLINE, which is the pre-#14185 path and therefore always correct — a
    malformed DSN raises in `record_dsn`, and an unimportable `record_writer` is
    a fault the inline arm already handles one frame down.
    """
    try:
        from . import record_writer

        return record_writer.record_dsn() is not None
    except Exception:  # noqa: BLE001, RUF100
        return False


def _submitted_to_worker(*, event: Mapping[str, Any], seq: int | None,
                         project: Any, durable: bool,
                         journal_skipped: bool,
                         tenant: tuple[str, str] | None,
                         record_sink: list | None) -> bool:
    """Hand this record's fold to the projection worker (#14185). Never raises.

    ``True`` ⇒ the fold is QUEUED and this write must not wait for it. ``False``
    ⇒ the caller folds inline, exactly as before the worker existed.

    ⛔ FIVE OF THE SEVEN CONDITIONS ARE STRUCTURAL — FOUR CORRECTNESS plus an
    unimportable loop — AND THE OTHER TWO ARE THE SWITCHES, and each of the four
    correctness ones is a fact only this chokepoint holds (#14222 review cycle 9,
    P2: this header said "four of the six", which lost the ``except`` below and
    made the family look closed at six):

    1. **A journal LINE exists** (``not journal_skipped``). On a cut-over graph
       the record IS the write's durable copy (#14002) — there is no line to
       replay — so deferring would acknowledge a write that exists in neither
       store for the length of the lag. This arm always folds inline.
    2. **No door reads the outcome** (``record_sink is None``). The capture
       writer branches on ``RecordWrite.applied`` to decide whether the turn
       lives in the record or must be written to the graph (#10000/#10021). A
       deferred write has not applied, so handing that consumer a deferred
       outcome would put the turn in the graph AND the record; handing it
       ``applied=True`` would be an attestation nothing has verified. Neither is
       acceptable, so a write whose outcome is consumed is never deferred.
    3. **The log is a JOURNAL** (``durable``). A scratch buffer is declined by the
       seam itself (``no_durable_journal``), so queueing it buys nothing and only
       risks the row being filed from a lane that a replay can never reproduce
       (#9231).
    4. **The tenant is carried BY VALUE** (``tenant is not None``). ⛔ THIS ONE
       IS NOT PARANOIA: a ``threading.Thread`` does not inherit the caller's
       contextvars, so a write whose tenant would be resolved AMBIENTLY
       (``record_writer.record_tenant_effective``: carried > scope > env pair) is
       resolved AGAIN in the worker — and a ``contextvars``-carried tenant is
       simply ABSENT there, leaving the env pair to answer. That is either a
       ``no_tenant`` refusal (fail-closed, safe) or, on a single-tenant box, a row
       filed under the env tenant rather than the request's — the #9192
       mis-scoping class, reached through a thread instead of a parameter. So a
       door that does not pass its tenant cannot be deferred; it folds inline,
       which is where its tenant resolution already worked. The SDK door passes
       ``tenant=self._record_tenant`` and defers; ``api.EventAPI._emit`` does not
       and does not.
    5. **The RECORD is armed** (a `TORTOISE_RECORD_DSN`) — ⛔ A SEPARATE SWITCH
       FROM THE WORKER FLAG (#14222 review P1-3). `write_live_record` returns
       ``None`` when no DSN is configured, so on a deployment with the loop
       flagged on and the record off, the fold answers "nothing was armed" — and
       the deferred arm would have had to report either ``armed=False`` (a lie
       about a queued write) or ``armed=True``/``unprojected=1`` (a loss claim
       for a write nothing was ever going to take). Both are wrong, and both
       would have MOVED every write on such a box from "nothing claimed it,
       nothing lost" to a permanent false alarm in
       :attr:`LiveWrite.unprojected` — read by `tools/claim_dense_leg_13732.py`
       as an unprojected position. Asking the record's own arming as a CONDITION
       keeps that deployment byte-identical: it folds inline and reports exactly
       what it reported before.
    6. **An operator armed the loop and the queue accepted the item.** Unset or
       typo'd reads OFF (:func:`tortoise.env_truthy.env_flag`), which keeps every
       unarmed deployment byte-identical to the pre-#14185 inline fold. The flag
       is asked HERE as well as in :func:`projection_worker.submit` because it is
       the CHEAPEST test of the seven — a bare env read — so asking it before
       condition 5 stops the default (loop off) deployment paying an uncached
       `record_writer.record_dsn()` parse per write for a fold it was never going
       to defer (#14222 review, finding 8). The conjunction is unchanged.

    ⛔ EVERY FAILURE ANSWERS ``False`` (fold inline). The journal has already
    committed by the time this is called, so a raise here would hand the producer
    an exception for a write that persisted AND skip the fold — the write would
    be missing from the record with nothing queued to put it there. Inline is the
    safe direction: it is only slower, and it is what the deployment did before
    this worker existed.
    """
    if journal_skipped or record_sink is not None or not durable:
        return False
    if tenant is None:
        return False
    try:
        from . import projection_worker
    except Exception:  # noqa: BLE001, RUF100 — an unimportable loop is not a fault
        return False
    # ⛔ ASK THE FREE SWITCH FIRST (#14222 review, finding 8, and P1 of the round
    # that read this hunk before it was committed — the check was added in the
    # WRONG PLACE first, BELOW `_record_armed()`). `record_writer.record_dsn()` is
    # UNCACHED — an env read, a scheme check and the host rule — so the default
    # deployment (loop OFF, record armed) paid that parse on every ACK before the
    # gate could decline, which is the ACK path this worker exists to keep short.
    # `projection_worker.armed()` is a bare `env_flag` read, `submit` asks it again,
    # and the conjunction is the same one — only its cheapest test moved in front
    # of its most expensive.
    if not projection_worker.armed():
        return False
    if not _record_armed():
        return False
    try:
        return projection_worker.submit(
            record=event, seq=seq, tenant=tenant,
            journal_skipped=journal_skipped, project=project)
    except Exception:  # noqa: BLE001, RUF100
        return False


@dataclass(frozen=True)
class LiveWrite:
    """What ONE write did, in the order the contract requires it.

    ``appended`` is ``True`` on every returned value EXCEPT the record-only arm
    (#14002): a cut-over graph's write does not touch the JSONL, so
    ``appended=False`` is a measurement and not a failure — the RECORD is the
    durable copy and its counter issued the number. An append that raises
    propagates out of :func:`write` (the callers' existing contract — a failed
    append is the truth store's failure, not a record refusal), so a caller never
    receives an outcome that claims a write whose line is missing.

    ⛔ ``appended=False`` TOGETHER WITH ``applied=False`` MEANS THE WRITE IS IN
    NEITHER STORE — the one shape this module cannot report as a disagreement,
    because there is no line for the record to have missed. Read the two booleans
    together on the record-only arm; `unprojected` stays the line-vs-row
    quantity it is documented to be, and is ``0`` there (a skipped append is not
    an unprojected line). The door's WARNING and `record_write_failures` are what
    an operator sees for it.

    ``record`` is ``None`` on FOUR arms, and they are NOT the same fact
    (#13345 review P3-1/P3-3; the fourth arrived with #14185 and this list said
    THREE until #14222's round-9 review found it): the UNARMED deployment (no
    DSN) — the one case `write_live_record` returns ``None`` for; the
    ``projection_raised`` arm, where the seam never ANSWERED at all (it raised,
    or could not be imported), so whether this deployment is armed is
    UNMEASURED there and :attr:`LiveWrite.armed` answers ``None``; a door that
    supplies its own ``project=`` whose callable returns ``None`` (the SDK's
    ``seam_unimportable`` arm is the live one), where :attr:`armed` answers
    ``False`` — because from this module's position the record was never asked,
    which is a different fact from "nothing was armed"; and the ``deferred`` arm,
    where the record has not ANSWERED **YET** — the line is committed and the fold
    is queued, so ``armed`` is ``True`` (deferral requires an armed record) and
    :attr:`LiveWrite.deferred` is ``True``. ⛔ Never infer
    "unarmed" from ``record is None``: read :attr:`armed`, which is the
    three-state answer for this module's own path and is ``False`` — never
    ``None`` — on a door's ``None``-returning project. A DECLINE is a
    ``RecordWrite``
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
    #: The DERIVED cache's over-cap verdict for this write, when there was one
    #: (#13829). ``None`` for every write whose door published no denial, which
    #: includes every un-gated lane (stdio/operator, unarmed, an unlimited tier)
    #: and every door that has not declared its write truth-carried.
    #:
    #: ⚠️ It is NOT a failure and NOT a disagreement about the truth. The journal
    #: line is committed and the record took it; the CACHE declined this one write
    #: because the org's derived layer is at/over its hard stop. Reads for it fall
    #: back to the record until a replay folds it — projection lag.
    cache_fold: Any | None = None
    #: ⛔ The record's fold was HANDED TO THE PROJECTION WORKER instead of run
    #: here (#14185). ``True`` means: the journal line is committed, the fold is
    #: QUEUED, and ``record`` is ``None`` because nothing has ANSWERED yet — not
    #: because nothing is armed. ⛔ ``record`` IS ``None`` on this arm too — it is
    #: the fourth one (the class docstring lists all four) — and this field exists
    #: so the arm is DISTINGUISHABLE, not so that ``record`` carries a different
    #: value. An earlier revision of this comment said the field was "a separate
    #: field rather than a FOURTH meaning of ``record is None``", which its own
    #: next parenthetical disproved; the contradiction is corrected here rather
    #: than renumbered (round-9 said "both now count four" while the sentence still
    #: denied it — round-14, P3).
    #:
    #: A deferred write is a LANDED write (the line exists, and
    #: ``derived = replay(journal)`` guarantees the record can be rebuilt to
    #: include it) whose record row appears after a measured lag. The lag is
    #: `monitoring`'s ``record_projection_*`` gauges; the gap it leaves is the
    #: same ``unprojected`` position `journal_coverage` counts, so the state is
    #: observable without this process's memory.
    #:
    #: ⚠️ ``applied`` is ``False`` on this arm — the record has not applied it
    #: yet, and reporting ``True`` would be an attestation nothing has verified.
    #: That is why a write whose outcome a door CONSUMES is never deferred: see
    #: ``live_write._submitted_to_worker`` condition 2.
    deferred: bool = False

    @property
    def applied(self) -> bool:
        """Did the RECORD take this write? ``False`` for unarmed and for refused."""
        return bool(getattr(self.record, "applied", False))

    @property
    def armed(self) -> bool | None:
        """Was the RECORD armed for this write? ``True``/``False``/``None`` (unmeasured).

        ``True`` — the record is armed for this write, by ONE of two evidence
        routes, and the docstring below used to claim only the first:

        1. the seam ANSWERED, and only an armed call answers: an unarmed
           deployment returns ``None`` from ``write_live_record``, so any
           ``RecordWrite`` held here — APPLIED, any refusal, or the healthy
           ``no_durable_journal`` decline — is an armed record; **or**
        2. the fold is QUEUED (``deferred``, #14185) and the seam has simply not
           answered yet. This is not an inference: :func:`_submitted_to_worker`
           refuses to defer unless ``_record_armed()`` holds, so the deployment's
           arming is already established when the write is queued. Answering
           ``False`` here would make a deferred write read as an UNARMED
           deployment, which is the one thing it is not.

        ⛔ Keep both routes in view when editing this property: the contract text
        above was written for route 1 alone and went stale the moment ``deferred``
        existed — the "stated-but-false invariant" class this branch keeps closing.

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
        if self.deferred:
            # Armed, and the fold is queued — the seam simply has not answered.
            # Answering ``False`` here would let a deferred write read as an
            # UNARMED deployment, which is the one thing it is not.
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
            "deferred": self.deferred,
            # ``None`` = the fold was not asked, or it applied. A dict = the
            # cache declined and NAMES why (dimension + the figures compared).
            "cache_fold": (None if self.cache_fold is None
                           else self.cache_fold.as_dict()),
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
    (the `#7951` ordering contract) and after the record ATTEMPT — ⛔ the
    ATTEMPT, not the ROW: with `TORTOISE_PROJECTION_WORKER` armed the record
    write is QUEUED and applied by another thread, so this cache fold can LEAD
    the record by the queue's lag. ⛔ The CACHE is never reordered —
    `projection.apply` folds one event in caller-thread order — but the RECORD
    seam is: under `TORTOISE_PROJECTION_QUEUE_MAX`'s declared residual an inline
    fallback can let a predecessor position that is still queued land AFTER this
    write, so THIS write's record row can be overwritten BY its predecessor
    (`.env.example` and `projection_worker` state the same direction; an earlier
    cut of this paragraph inverted it and attributed it to the cache). What the
    ordering here guarantees is CALLER-THREAD order, not store order; a deferred
    deployment accepts that trade because the cache is what the read path serves
    today and the record is healed by a replay of the journal
    (`docs/architecture/STORAGE-ARCHITECTURE.md` §3).

    ``fold=False`` lets the SDK's door keep its own fold decision while still
    getting the append→record ordering and the named outcome from here.

    ⛔ ``tenant`` IS CONSUMED ON EVERY ARM SINCE #14002, and the paragraph that
    used to sit here said the opposite ("consumed ONLY by the DEFAULT project …
    IGNORED … reads as load-bearing while doing nothing", naming the SDK door as
    the example). The pre-append cutover decision is asked with THIS pair
    (:func:`record_writer.record_owns_position`), and it must be asked about the
    graph the write is going to land on — so a door that supplies its own
    ``project=`` must pass ``tenant=`` AS WELL AS carrying the pair through its
    callable. The SDK door is the example of the CORRECT shape: it passes
    ``tenant=self._record_tenant`` and ``project=self._write_record_projection``,
    which reads the same attribute, so one request's decision and its allocation
    agree. ⛔ AND THE SCOPE OF THAT AGREEMENT IS THE SDK'S LIFETIME, NOT THIS CODE
    (#14222 review, finding 2): the attribute is read TWICE, so a shared SDK
    re-bound between the two reads — which a deferred fold widens from one call
    frame to the queue's lag — would decide on one graph and allocate on another.
    It cannot happen today because a per-request SDK is the rule
    (``hosted_api._make_sdk`` builds a fresh one and caches nothing, and
    `_bind_record_tenant`'s docstring names the cached-SDK shape that would break
    it), and the residual is stated where the deferral is decided
    (``projection_worker.PendingProjection``), together with why the pair is not
    threaded through the ``project`` protocol every door and every double
    implements. Closing it properly is #14002's two-read window, not a wording
    change here; this paragraph exists so the next reader does not read the
    agreement as a property of the seam. Removing that argument (the conclusion #13345
    P3-2 originally reached — it was true then) makes the decision resolve the
    AMBIENT tenant while the seam allocates for the door's own pair: the append is
    skipped for graph A while the row is allocated on graph B — #9192's
    mis-scoping class, or a write in neither store, filed silently. When
    ``tenant`` is ``None`` the resolution is the seam's own
    (`record_tenant_effective`: carried > scope > env pair), the same one the
    default project uses, so the two halves still agree.

    ⛔ A FAILED APPEND PROPAGATES. The callers already turn it into their own
    accounting (`_emit_event` counts `tortoise_journal_write_failures_total`
    and returns ``False``); swallowing it here would make a lost line look like
    a successful write with a pending record, which is the opposite of the
    direction this module guarantees.

    ⛔ ON A CUT-OVER GRAPH THE APPEND DOES NOT HAPPEN AT ALL (#14002). Before
    the append, :func:`record_writer.record_owns_position` is asked whether the
    RECORD owns this write's ordinal; when it answers yes, the file append is
    skipped and the counter issues the number. That is what removes the SECOND
    allocator, and with it the `position_divergence` refusal that two allocators
    ordered by different events (append vs allocation) produce under concurrent
    writers. Nothing about the ordering contract moves: the record's commit is
    the commit point for that write, the fold still runs last, and the answer is
    forwarded to the seam as ``journal_skipped=`` so the two `seq is None`
    states stay distinguishable.

    ⛔ AND THE UNMEASURED ANSWER IS "APPEND". `record_owns_position` fails open
    (no DSN, an unreadable cursor row, an open breaker ⇒ ``False``): the
    pre-#14002 path then runs unchanged, which costs at worst the divergence
    refusals the guard already handles. Assuming the record owns it instead
    would skip the append for a graph whose allocation then refuses — the write
    would be in NEITHER store, which is not a failure mode any refusal can
    represent. The direction is chosen, not inherited.
    """
    if (log is None) == (append is None):
        raise TypeError(
            "live_write.write needs exactly one of `log=` or `append=` — the "
            "journal append is the commit point, so which callable performs it "
            "must be unambiguous (#13331)")
    if durable is None:
        durable = getattr(log, "durable", True) if log is not None else True

    journal_skipped = _record_owns_position(durable=durable, tenant=tenant)

    # ── 1. THE TRUTH. The append is the commit point (#7951) — unless the
    #      record owns the ordinal, in which case the counter is (#14002).
    # ⛔ A TERNARY IS REFUSED HERE ON PURPOSE (`SIM108` is suppressed for this
    # statement): the wiring gate reads this guard from the AST —
    # `tools/record_wiring_check.py::the_chokepoint_skips_the_append` asserts the
    # append sits inside an `if` whose test references the decision, so a
    # one-line conditional expression would either defeat the check or force it
    # to parse expressions. The block form IS the contract, not a style choice.
    seq: int | None
    if journal_skipped:  # noqa: SIM108 — see above
        seq = None
    else:
        seq = append() if append is not None else log.append(event)

    # ── 2. THE RECORD, on ONE transaction, after the append. ───────────────
    deferred = False
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
                                 seq: int | None, *,
                                 journal_skipped: bool = False) -> Any:
                return record_writer.write_live_record(
                    record, durable=durable, seq=seq, tenant=tenant,
                    journal_skipped=journal_skipped)

            project = _default_project
        outcome = None
        # ⛔ #14185: THE FOLD MOVES OFF THIS THREAD WHEN IT MAY. The append is the
        # commit point (#7951), so once the line exists the record's fold is
        # bookkeeping the caller need not await; `_submitted_to_worker` decides
        # whether THIS write may hand it over (and says why not when it may not),
        # and answers False on every doubt — which folds inline, the pre-#14185
        # shape. The lag this creates is EXPORTED
        # (`monitoring`'s `record_projection_*` gauges) and DECLARED by every
        # reader that compares the journal with the record (#2952's rule):
        # `journal_coverage`, `record-reconcile --journal`, `record_runner
        # status`.
        if _submitted_to_worker(
                event=event, seq=seq, project=project, durable=durable,
                journal_skipped=journal_skipped, tenant=tenant,
                record_sink=record_sink):
            deferred = True
        else:
            outcome = project(event, seq, journal_skipped=journal_skipped)
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
        #
        # ⛔ ON THE RECORD-ONLY ARM THERE IS NO LINE TO DISAGREE WITH (#14002).
        # The message must not claim a committed position that does not exist,
        # and it must not offer a replay of a journal that never received the
        # record: a raise here means the write reached NEITHER store.
        if journal_skipped:
            LOGGER.warning(
                "live_write: the record projection RAISED on the RECORD-ONLY "
                "arm (%s: %s) — the journal append was SKIPPED because the "
                "record owns the ordinal (#14002), and the record did not take "
                "the write, so this record is in NEITHER store and no replay "
                "can heal it. The remedy is the record itself: "
                "`tools/record_allocator_cli.py status` (the counter's mode), "
                "then a re-emit once it is healthy",
                type(exc).__name__, exc)
        else:
            LOGGER.warning(
                "live_write: the record projection RAISED after the journal "
                "append committed (%s: %s) — the journal holds position %s and "
                "the record does not. This is a NAMED disagreement: "
                "`record_projection.journal_coverage` counts it, `python -m "
                "tortoise record-reconcile --journal <path>` reports it as "
                "unprojected_journal, and `tools/record_runner.py apply` heals "
                "it idempotently (#13331)",
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
    #
    # ⛔ EXCEPT ON THE RECORD-ONLY ARM (#14002), where there is no line for the
    # record to have missed: `unprojected` is the JOURNAL-vs-RECORD quantity and
    # a skipped append is neither a line nor a miss. It stays 0 and the loss, if
    # the record refused, is the pair (`appended=False`, `applied=False`) — which
    # reads as "in neither store" rather than as "one replay away". Setting 1
    # here would make a LOST write indistinguishable from a recoverable one.
    unprojected = 0
    if deferred:
        # ⛔ A LINE THE RECORD HAS NOT TAKEN *YET* — which is the deferred arm's
        # whole point, so it is spelled as the same quantity rather than as a
        # second one. It is NOT a loss and must not be read as one: the item is
        # QUEUED, the loop's own ``failures`` counter is the loss signal, and
        # `record_reconcile` watches the same positions converge as the queue
        # drains. Setting 0 here would make a lagging record look caught-up.
        unprojected = 1
    elif (outcome is not None and not getattr(outcome, "applied", False)
            and not _is_durable_decline(outcome)):
        unprojected = 0 if journal_skipped else 1
        refusal = refusal or "write_failed"
    if raised:
        unprojected = 0 if journal_skipped else 1
        refusal = PROJECTION_RAISED

    # ⛔ UNREACHABLE ON THE DEFERRED ARM BY CONSTRUCTION, not by luck: a write
    # whose outcome a door consumes is never deferred (`_submitted_to_worker`
    # condition 2), so `outcome` is a real `RecordWrite` here whenever a sink
    # exists. Appending on the deferred arm would hand the capture writer the
    # `None` that means UNARMED and send a record-held turn to the graph.
    if record_sink is not None:
        record_sink.append(outcome)

    # ── 3. THE CACHE, last — after the record ATTEMPT, in CALLER-THREAD order.
    # ⛔ NOT "derived never leads truth": with the worker armed the record write
    # is QUEUED, so this fold can lead the record by the queue's lag (declared,
    # and healed by a replay). See this module's `projection` docstring and
    # `projection_worker`'s declared residual.
    folded = False
    denial = None
    if fold and projection is not None:
        # ⚠️ OUTSIDE THE `try` ABOVE, DELIBERATELY (#13345 review, note). Same SHAPE
        # as the record seam's import, opposite RULE: a RECORD fault reports and
        # continues (the journal line is committed and the record is a derived
        # store), while a FOLD fault propagates — `projection.apply` already
        # propagates, and swallowing ITS import failure would leave the caller
        # believing a fold it asked for happened. Do not "close" this arm by
        # catching it without first deciding what `folded` then means.
        #
        # ⛔ THE CAP OF THE DERIVED STORE IS ASKED HERE, AT THE FOLD (#13829).
        # The door no longer refuses an over-cap `graph_storage` for a write it
        # has declared truth-carried; it publishes that verdict instead
        # (`allowance_gate.cache_fold_denial`), and THIS is the leg that consumes
        # it. The comparison therefore decides whether the CACHE takes the write —
        # never whether the journal and the record do, which are already done by
        # the time control reaches this line.
        #
        # What the tenant sees: the write SUCCEEDS (durable in the record), the
        # cache stops growing, and reads for it are served from the record. That
        # is projection lag — the prescribed degradation for a derived read model
        # (`STORAGE-ARCHITECTURE.md` §2.0/§3 make the cache physically droppable,
        # so it must not be able to refuse the record) — not a lost write.
        #
        # ⚠️ The import is guarded and the arm is FAIL-OPEN to the fold: if the
        # gate cannot be imported at all, asking is impossible and the cache
        # behaves exactly as it did before this change. The alternative —
        # refusing the fold because the meter's module is missing — would turn a
        # packaging fault into a silently frozen cache.
        try:
            from .allowance_gate import cache_fold_denial
        except Exception:  # noqa: BLE001, RUF100 — an unimportable gate must not stop the fold
            denial = None
        else:
            denial = cache_fold_denial()
        if denial is not None:
            # ⛔ The store that holds this write, spelled for the arm that ran: on
            # a cut-over graph no journal line exists (#14002), so the original
            # sentence would name a position that was never written.
            holder = ("The journal append was SKIPPED (the record owns the "
                      "ordinal)" if journal_skipped
                      else f"The journal holds position {seq}")
            LOGGER.warning(
                "live_write: the DERIVED cache DECLINED this write (#13829) — "
                "%s. %s and the record took the write; the cache is at/over "
                "its cap, so it lags until a replay folds this record. Reads "
                "are served from the record.",
                getattr(denial, "detail", denial), holder)
        else:
            from .projection import _live_write_scope

            with _live_write_scope(scope_label):
                projection.apply(event)
            folded = True

    return LiveWrite(event=event, seq=seq, appended=not journal_skipped,
                     record=outcome, folded=folded, refusal=refusal,
                     unprojected=unprojected, cache_fold=denial,
                     deferred=deferred)
