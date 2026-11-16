"""#11824 S0 — THE write door for the lifecycle ``status`` word.

Every **knowledge kind** (``:Point``, ``:Object``, ``:Subject``) carries ONE
lifecycle word from a closed set of four — ``draft`` · ``live`` · ``deprecated``
· ``superseded`` — and this module is the one place that decides whether a
caller's value is that word. Design of record:
``docs/plans/2026-11-15-state-lifecycle-and-pipelines.md`` §3 (the four words)
and §3.2 (what is deliberately not a word). Issue of record: #11824.

WHY THIS EXISTS. ``:Point`` and ``:Subject`` had a write-side vocabulary and
``:Object`` had **none** — ``commit_ops`` recorded that as a constraint
(``"The OBJECT family STILL has no write-side vocabulary guard"``), because the
declared words differed per pack and a single shared set would refuse real ones.
The model answers that by moving a pack's own words onto the **position** axis
(its pipeline stages) and closing the **lifecycle** axis at four, so one set is
correct for every knowledge kind. The failure this door prevents is measured:
``tortoise_create_entity`` / ``tortoise_update_entity`` accepted
``status='outdated'``, ``'archived'`` or ``'garbage'`` on an Object, and a
reader's filter set was then the only thing standing between a junk word and a
row that reads as CURRENT (``assembly._RECALL_OBJECT_EXCLUDED_STATUSES``
carried ``outdated`` for exactly that reason — a read-side defence for a write
door that did not validate).

THE FOUR OUTCOMES, IN THIS ORDER (``docs/ax/ax-principles.md`` §1–§2, §5; the
shape is copied from ``tortoise/validity.py::read_caller_end`` /
``validate_validity_precision``, which apply the same ruling to the window end):

1. **a different spelling of a legal word** — case, surrounding whitespace
   (``'LIVE'``, ``' Live '``). The intent is unambiguous and only the form is
   off, so it is normalised and the write proceeds, **silently**. A receipt for
   a re-spelling asks the caller to re-express something we already understood
   (AX §1).
2. **a declared synonym** — ``archived`` or ``retracted``: both mean *ended, and
   nothing replaced it*, which is exactly ``deprecated``. The word is stored as
   ``deprecated``, the write proceeds, **silently** (AX §1: a synonym supplies no
   meaning we did not already have, so there is nothing to echo).
3. **``outdated``** — REFUSED. It says *ended* without saying whether anything
   replaced it, so it names one of two different lifecycle words. AX §2:
   **never pick a meaning for the caller** — the refusal names both, and both
   repairs are writable.
4. **a position word written as a status** — ``in_progress``, ``completed``,
   ``active``, ``paused``. REFUSED, with a **per-word** repair, because each
   names a different place in the pipeline (§4 of the design): position lives on
   the stage axis, never on the lifecycle one.
5. **anything else** — REFUSED, third part naming the four words.

**The admission test (AX §5)** is what makes this admissible: every refusal
above has a *writable* third part — the caller recovers by resubmitting a legal
word, by choosing between two named ones, or by advancing a stage. No row
refuses where the fix cannot be written, and no row refuses on a spelling.

**The counting device** is ``validity.END_WRITE_OUTCOMES``' own shape: a
process-local dict with one key per outcome. ``normalised`` and ``refused`` are
**different counts**, never one label (AX §2: the reported state must not
flatten different failures into one label) — a silent normalisation is a
successful write and a refusal is not, so a single "invalid status" counter
could never answer *"is the vocabulary too narrow?"* (which shows up as the same
refusal recurring) or *"how many calls arrive in the wrong spelling?"* (which
shows up as normalisations). Read it with
``dict(STATUS_WRITE_OUTCOMES)``.

**Not in scope, deliberately:** an ``:Event`` has no lifecycle (design §8 —
``#11825``), and operational node kinds (``:PipelineStage``, ``:Batch``) keep
their own vocabularies. This module is imported only by the knowledge-kind
write doors, so neither is reached by it.
"""
from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)

#: THE lifecycle vocabulary — one closed write set for every knowledge kind.
#: Order is the design's §3 table order (promotion order), and it is what the
#: refusals render, so a caller reads the same list everywhere.
LIFECYCLE_STATUS_VALUES: tuple[str, ...] = (
    "draft", "live", "deprecated", "superseded",
)

LIFECYCLE_STATUS_SET: frozenset[str] = frozenset(LIFECYCLE_STATUS_VALUES)

#: DECLARED SYNONYMS — a legal intent under another spelling, stored as the
#: canonical word. Both mean *ended, and nothing replaced it* (design §3.1's
#: verb table: retire / withdraw / retract / reject / abandon / drop all land on
#: ``deprecated``). ``archived`` additionally carried the *"do not show me this"*
#: collision the model removes (§3.2): hiding is a reader's filter, so the ending
#: is what the record keeps.
#:
#: ⚠️ One-way, and the direction is the point: a caller may WRITE ``archived``
#: or ``retracted``; nothing reads them back as distinct words. Stored rows keep
#: whatever they hold (no backfill, #11824 §1), which is why the families' READ
#: sets still name the legacy words as deltas — a read vocabulary answers "which
#: STORED states are dead", a write vocabulary answers "which words may be
#: minted", and the net stays wider than the door (#2977).
LIFECYCLE_STATUS_SYNONYMS: dict[str, str] = {
    "archived": "deprecated",
    "retracted": "deprecated",
}

#: A **position** word is a stage, not a lifecycle state (design §3.2). Each
#: carries its OWN repair, because each names a different place in the pipeline:
#: the refusal is only performable if it says which place (§4.1 — the slot is
#: what carries the meaning, so a word that names a slot needs the stage named
#: for it, and a word that is only ever a slot name says so).
_POSITION_WORD_REPAIRS: dict[str, str] = {
    "in_progress": (
        "`in_progress` is a POSITION, not a lifecycle word — advance the work "
        "instead: `advance_stage(<id>, <pipelineKey>, 'in_progress')`, a stage "
        "in the `active` slot. The `status` field keeps one of the four "
        "lifecycle words, and a thing in progress is still `live`."
    ),
    "completed": (
        "`completed` is a POSITION, not a lifecycle word — advance the work "
        "instead: `advance_stage(<id>, <pipelineKey>, 'done')`, the `terminal` "
        "stage whose outcome is `completed`. An achievement or a finish is "
        "never written as a lifecycle ending, because a terminal stage can be "
        "reopened (`advance_stage(..., reopen=True)`) while a lifecycle word "
        "cannot."
    ),
    "paused": (
        "`paused` is an INTERRUPTION, not a lifecycle word — advance the work "
        "instead: `advance_stage(<id>, <pipelineKey>, <the stage in the "
        "`suspended` slot>)`. A paused thing has not ended, so its `status` "
        "stays `live`."
    ),
    "active": (
        "`active` is a SLOT (`entry` / `active` / `suspended` / `terminal`), "
        "not a lifecycle word AND not a stage name — name the stage you mean "
        "and advance it: `advance_stage(<id>, <pipelineKey>, <stageId>)`. The "
        "`status` field keeps one of the four lifecycle words, and a thing "
        "being worked on is `live`."
    ),
}

#: The retired word that used to be a lifecycle state and now names a CHOICE
#: between two of the four (design §3.2: *"the past name of the Point 'no longer
#: counts' mechanism"*). Refused, never normalised — see ``read_caller_status``.
_OUTDATED_REPAIR = (
    "`outdated` is retired and names TWO different lifecycle words — it says "
    "the record ENDED without saying whether anything replaced it. Resubmit as "
    "`deprecated` (nothing replaced it — `archived` is its synonym) or "
    "`superseded` (name the successor in `supersededBy`; a Point names it with "
    "`supersede_point`)."
)

#: The forms the field accepts, quoted into EVERY refusal — a refusal that does
#: not name the repair manufactures a retry loop (§7 trap 2 of the validity
#: door's ledger; the same rule, the same reason).
_ACCEPTED_FORMS = (
    "every knowledge kind accepts `draft`, `live`, `deprecated` (and its "
    "synonyms `archived` / `retracted`, which are stored as `deprecated`) and "
    "`superseded` — and the same word in another case, or with surrounding "
    "whitespace, is accepted"
)

#: THE MEASUREMENT (#11824 S0, AX §2). One key per OUTCOME, because a silent
#: normalisation and a refusal are different facts:
#:
#: * ``normalised`` counts a write the door UNDERSTOOD and stored as the
#:   canonical word — a re-spelling (``'LIVE'``) or a synonym (``archived``).
#:   The write succeeded; nothing is reported to the caller.
#: * ``refused`` counts a write the door declined, with the repair named.
#:
#: A single counter would have made the two indistinguishable, which is exactly
#: what AX §2 forbids. Process-local (the ``caplog`` line carries the value for
#: anything durable); read it with ``dict(STATUS_WRITE_OUTCOMES)``.
STATUS_WRITE_OUTCOMES: dict[str, int] = {"normalised": 0, "refused": 0}


def _refusal(value: object, fault: str, repair: str) -> ValueError:
    """Count, log and describe ONE refused caller status (#11824 S0).

    Three parts, in this order (AX rule 2): that it refused, exactly what
    failed, and exactly what to do about it. Returned (not raised) so the
    caller's ``raise ... from None`` keeps the caller-facing traceback free of
    this module's internals — the same device ``validity._end_refusal`` uses.
    """
    STATUS_WRITE_OUTCOMES["refused"] += 1
    message = (
        f"status {value!r} was refused: {fault}. {repair} "
        f"({_ACCEPTED_FORMS}; ONTOLOGY §3 of #11824's model.)"
    )
    _logger.warning("status refused at the write door: %s", message)
    return ValueError(message)


def read_caller_status(value: object, *, kind: str = "") -> str:
    """Read a caller's ``status`` for INTENT on the way in — THE one home.

    ``kind`` only shapes the message (``":Point"``, ``":Object"``, ``":Subject"``)
    — the vocabulary is the same for every knowledge kind, which is the whole
    point of the model. Returns the word TO STORE. Raises ``ValueError`` (three
    parts) for anything the door refuses.

    The order of the arms is the ruling's order, and each arm's reporting is
    decided by what it does to the MEANING (§1):

    * whitespace/case around a declared word, or a declared synonym → the meaning
      is unchanged, so the write proceeds **silently**;
    * ``outdated`` → the meaning is ambiguous *between two declared words*, so it
      is refused and both are named (never picked, §2);
    * a position word → the meaning belongs to ANOTHER AXIS, so it is refused
      with the stage named (§3.2, §4);
    * anything else → refused with the vocabulary named.

    ⛔ A NON-STRING IS REFUSED, not coerced. ``status=['live']`` used to be
    STORED on an ``:Object`` (it is not a dead word, so the read families
    treated the row as current), and coercing it here would mint a word the
    caller never wrote. The refusal names the vocabulary, which is a performable
    third part.
    """
    if not isinstance(value, str):
        raise _refusal(
            value,
            f"a status must be a plain string, and {type(value).__name__} is "
            f"not one",
            "Pass the word itself, as a string.",
        ) from None
    token = value.strip().lower()
    if token in LIFECYCLE_STATUS_SET:
        if token != value:
            # A re-spelling of a legal word: same meaning, stored canonically,
            # reported to nobody (AX §1). Counted, because the ratio of
            # re-spellings to clean writes is the measurement that says whether
            # callers are guessing at the vocabulary.
            STATUS_WRITE_OUTCOMES["normalised"] += 1
        return token
    synonym = LIFECYCLE_STATUS_SYNONYMS.get(token)
    if synonym is not None:
        # A declared synonym: the meaning is one we already have, so the store
        # gets the canonical word and the caller gets no receipt (AX §1).
        STATUS_WRITE_OUTCOMES["normalised"] += 1
        return synonym
    if token == "outdated":
        raise _refusal(
            value,
            "it says the record ended but not whether anything replaced it, so "
            "it does not name a lifecycle word",
            _OUTDATED_REPAIR,
        ) from None
    position = _POSITION_WORD_REPAIRS.get(token)
    if position is not None:
        raise _refusal(
            value,
            "it is a position word written as a status, and position and "
            "lifecycle are different fields",
            position,
        ) from None
    raise _refusal(
        value,
        f"it is not a lifecycle word for {kind or 'any knowledge kind'}",
        "Pass a lifecycle word.",
    ) from None


def status_refusal_reason(value: object, *, kind: str = "") -> str | None:
    """``read_caller_status`` as a QUESTION: the repair text, or ``None``.

    For a door that has to report a refusal it did not raise — the ingest bundle
    validator (``sdk._validate_bundle``) refuses an item at PHASE 1 so a bad
    status cannot abort step 3 after the point writes committed (#7925's
    partial-mutation class). It must ask exactly the question the writer asks,
    so it calls this rather than re-spelling the vocabulary; ``None`` means the
    writer would accept the value.
    """
    try:
        read_caller_status(value, kind=kind)
    except ValueError as exc:
        return str(exc)
    return None
