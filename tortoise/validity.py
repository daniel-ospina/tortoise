"""E-prec (#2350): the validity-window PRECISION vocabulary + 3-state end.

THE ONE HOME for the per-end precision layer that extends #1538's canonical
``validFrom``/``validTo`` fields — ADDITIVELY, never re-stamping an existing
window and never minting a parallel validity field. The window predicate
itself (ordering + inversion) lives in
``commit_schema.validate_validity_window``; this module owns only what that
predicate deliberately does not: HOW PRECISELY each end is known, and whether
an absent end means "still true" or "ended, but I cannot date it".

Why the distinction exists (the anti-pattern this prevents). An absent
``validTo`` has always read as an OPEN end (``sdk._covers``): the window
covers every instant after its start. That is correct for a still-true claim
and WRONG for a claim whose end is simply unknown — reading the second as the
first fabricates coverage, the "half-honest dates are worse than none" bug
class this epic adopts from deeplethe/utopia (#2349). The 3-state end makes
the difference REPRESENTABLE and READABLE:

  * ``END_OPEN``    — no end is asserted: ``validTo`` absent, no precision.
  * ``END_KNOWN``   — the end is dated: ``validTo`` present AND orderable
                      (precision year/month/day; a legacy bare-ISO bound reads
                      as ``day`` with no migration — ``effective_precision``).
  * ``END_UNKNOWN`` — the window ENDED, but at a time we cannot pin. TWO
                      spellings, one meaning: the DECLARED marker
                      (``validToPrecision == "unknown"`` with NO ``validTo``),
                      or a STORED end that names no instant (``validTo =
                      "TBD"`` / ``""`` / ``"2026-02-30"`` — #9202). Since the
                      2026-10-26 ruling the guarded write doors SHAPE a new
                      caller end into the first spelling (``read_caller_end``)
                      rather than storing the second, so the second survives
                      only on rows written before it — which are never
                      re-validated or migrated, and read identically. A date is
                      never fabricated to fill the hole.

Why the second spelling is ``END_UNKNOWN`` and not ``END_OPEN`` (#9202, owner
ruling of 2026-10-25): an end column carrying a value is an ASSERTED end, so
"no end was recorded" is a claim the record contradicts; and reading it as
still-true is exactly the fabricated-coverage hazard this vocabulary exists to
prevent (#2349/#2350). ``END_OPEN`` therefore stays reserved for a bound that
is ABSENT, and BOTH un-datable spellings fire the reply-level
``end_state_unknown`` / ``indeterminate_end_ids`` flags, so a caller is told
the window cannot be excluded rather than being told it is still true.

The three-state is END-ONLY. ``validFromPrecision`` may not be ``"unknown"``:
a window with no datable start is not a window, and the 3-state vocabulary
this issue adds is about the end (open / closed-at-known-date /
closed-unknown).

Legacy compatibility is total: a window written by #1538 carries no precision
property, ``effective_precision`` reports ``"day"`` for its present, ORDERABLE
bounds — and no precision (declared token or not) for a present bound that
names no instant (#9201 on the undeclared default, #9244 on the declared
token: a declaration ANNOTATES a bound, it cannot summon one; the #6140 ruling
reads an unparseable START as "no start was recorded") — and ``end_state``
reports ``END_OPEN`` for its open end, byte-identical reads.
The ONE reading #9202 changes for already-persisted data is a STORED end that
names no instant: ``known`` -> ``unknown``, plus the flags it fires.
"""
from __future__ import annotations

import logging
import re
from datetime import MINYEAR, date

_logger = logging.getLogger(__name__)

#: Concrete granularity a caller may DECLARE for a bound it supplies. The
#: stored bound stays whatever the caller wrote (the vocabulary is metadata
#: about the claim's precision, not a second date format — ``_created_sort_key``
#: keeps ordering the same value it always did).
BOUND_PRECISIONS: tuple[str, ...] = ("year", "month", "day")

#: The end-only "closed, un-datable" marker (utopia's ``valid_to_precision``
#: pattern). Never legal for a start.
PRECISION_UNKNOWN = "unknown"

PRECISION_VALUES: tuple[str, ...] = (*BOUND_PRECISIONS, PRECISION_UNKNOWN)

#: The 3-state window end.
END_OPEN = "open"
END_KNOWN = "known"
END_UNKNOWN = "unknown"
END_STATES: tuple[str, ...] = (END_OPEN, END_KNOWN, END_UNKNOWN)

#: Node property names (camelCase, matching ``validFrom``/``validTo``).
FROM_PRECISION_PROP = "validFromPrecision"
TO_PRECISION_PROP = "validToPrecision"

#: #9202 (owner ruling, 2026-10-26): the vocabulary a caller may send for an end
#: it cannot date. Case- and whitespace-insensitive, and ONLY these — ``none`` /
#: ``null`` / ``nil`` are serialisation mistakes, not human uncertainty, so they
#: are REFUSED with the vocabulary named rather than absorbed into it (§7 trap 5:
#: absorb uncertainty, refuse mistakes).
END_UNKNOWN_MARKERS: tuple[str, ...] = (
    "tbd", "tba", "tbc", "unknown", "n/a", "na", "-", "--", "—", "?",
)
_END_UNKNOWN_MARKER_SET = frozenset(END_UNKNOWN_MARKERS)

#: #9202: the MEASUREMENT. The ruling requires shape and refuse to be counted as
#: well as logged, because its own test for a vocabulary that is still too narrow
#: is "the same refusal recurring for the same caller shape" — and only a count
#: can show that. Process-local (the log line carries the value for anything
#: durable); read it with ``dict(END_WRITE_OUTCOMES)``.
END_WRITE_OUTCOMES: dict[str, int] = {"shaped": 0, "refused": 0}

#: A date the read measure CANNOT order but whose INTENT is unambiguous: year
#: first, 1-2 digit month/day, ONE consistent separator. ``_created_sort_key``
#: requires ``len(s) >= 10`` plus a ``-``/``T``, so ``2024-3-1`` (8 chars) and
#: ``2024/03/01`` name no instant to it — representational variance, NORMALISED
#: silently rather than refused (ONTOLOGY §4.7, #9202). A day/month-first form
#: (``03/04/2024``) deliberately does NOT match: it parses both ways, so it is
#: "legible but ambiguous" and belongs in the refuse column (§0).
#:
#: The DATE part, not the whole string: un-padded fields beside a TIME
#: (``2024-3-1T12:00:00``) are the SAME human sloppiness and name the SAME
#: instant, so they are normalised by the same rule rather than refused. Only
#: the date is re-spelled — ``rest`` (the time-of-day, if any) is the caller's
#: and is kept verbatim — and the result is accepted only when the read measure
#: can order it, so this tolerance cannot smuggle a value past
#: ``_names_an_instant``. (Review P3: a padded date normalised while the padded
#: date-TIME beside it was refused — one sloppiness, two verdicts.)
_DATE_VARIANT_RE = re.compile(
    r"^(?P<year>\d{4})(?P<sep>[-/])(?P<month>\d{1,2})(?P=sep)(?P<day>\d{1,2})"
    r"(?P<rest>[T ]\S*)?$"
)

#: An epoch supplied as a STRING. ``_created_sort_key`` reads an epoch from a
#: NUMBER, never from a string, so this names no instant while its intent is
#: unmistakable — #9202's named fault, refused with the number named as the fix.
_EPOCH_AS_STRING_RE = re.compile(r"^-?\d{9,}$")

#: The span inside which offering `pass the NUMBER <n>` is a TRUE repair (#10079
#: review P2): 1900-01-01T00:00:00Z .. 2100-01-01T00:00:00Z, in SECONDS — which
#: is the unit the field reads a NUMBER as. An end is an authored belief about
#: when a fact stopped being true, so a bound outside this span is not a date
#: anyone authored; at those magnitudes the digits are a millisecond epoch or a
#: compact timestamp, and prescribing the number would store a confidently wrong
#: instant instead of the intended one. Bounded rather than derived from
#: `date.fromtimestamp` because that raises PlatformError/OverflowError on some
#: platforms for precisely the values this guard has to classify.
_EPOCH_SECONDS_SANE_MIN = -2208988800
_EPOCH_SECONDS_SANE_MAX = 4102444800

#: The forms the field accepts, quoted into EVERY refusal: a refusal that does
#: not name the repair manufactures a retry loop (§7 trap 2).
#:
#: The open-end repair is spelled PER DOOR and never as "None/omitted" (#9202
#: review P2): ``omitted`` is an OPEN end on ``create_point`` only — on
#: ``update_point`` a partial write that omits ``validTo`` leaves the STORED end
#: exactly as it was, so a caller who followed the old instruction was refused
#: again with the junk end still in place. Naming the door costs one sentence and
#: is the difference between a repair and a second failure.
_END_ACCEPTED_FORMS = (
    "an ISO-8601 instant ('2026-02-28', '2026-02-28T12:00:00+00:00'), a "
    "year-first date ('2024-3-1', '2024/03/01'), the NUMBER for a Unix epoch "
    "(1719792000), an uncertainty marker ("
    + ", ".join(END_UNKNOWN_MARKERS)
    + ") or blank to say 'ended, un-datable', or validTo=None to CLEAR the end "
    "(an OPEN window). OMITTING validTo clears the end on create_point, but on "
    "update_point it leaves the STORED end untouched — pass None there"
)


def normalize_precision(value: object, *, field: str = "precision") -> str | None:
    """Validate ONE precision token, or ``None`` when the bound declares none.

    ``None`` and the empty string BOTH mean "no precision declared" — the
    empty string is the codebase's undated convention (``when=""``), so it is
    normalized away rather than refused. Everything else must be a lowercase
    token in ``PRECISION_VALUES``: an unknown word is a caller error, not a
    bound to guess at, so it raises ``ValueError`` naming the field.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(
            f"{field} must be a string in {PRECISION_VALUES} (got {value!r})"
        )
    token = value.strip().lower()
    if not token:
        return None
    if token not in PRECISION_VALUES:
        raise ValueError(
            f"{field} {value!r} is not a validity precision — "
            f"expected one of {PRECISION_VALUES}"
        )
    return token


def _names_an_instant(bound: object) -> bool:
    """``True`` iff ``bound`` ORDERS on the measure the read path orders with.

    ONE home for "does this stored value point at an instant?", shared by the two
    halves of the same read block — ``effective_precision`` (#9201: a bound that
    names no instant gets no granularity) and ``end_state`` (#9202: an end that
    names no instant is ``END_UNKNOWN``, not ``END_KNOWN``). Keeping them on one
    predicate is why the emitted triple cannot contradict itself: a ``validTo``
    of ``"TBD"`` cannot report ``valid_to_precision: null`` beside
    ``end_state: "known"`` ("the end is dated") any more.

    The measure is ``search_engine._created_sort_key``, imported lazily (no
    import cycle) — never a second notion of "parseable". A NUMERIC bound
    (``int``/``float``, and so ``bool``) is an instant without consulting it: an
    integer too large for ``float()`` would otherwise raise ``OverflowError`` out
    of a read path, and ``nan``/``inf`` keep their pre-#9201 reading (#9201
    review P3). ``retrieval._bounds_an_instant`` is the render-side twin of this
    predicate (same measure, plus a falsey short-circuit for the marker).
    """
    if isinstance(bound, (int, float)):
        return True
    from .search_engine import _created_sort_key  # lazy — no import cycle
    return _created_sort_key(bound)[0] == 0


def effective_precision(bound: object, declared: object) -> str | None:
    """The precision a READ path should report for ``bound``.

    A declared token ANNOTATES the bound, it cannot summon one (#9244): the
    declaration is reported only when the bound it annotates names an instant.
    Presence is not precision (#9201): a bound that names no instant — ``""``,
    ``"TBD"``, ``"not-a-date"``, and every row written before the 2026-10-26
    shaping ruling — is a PRESENT value that ``_created_sort_key`` cannot order,
    and the #6140 ruling makes an unparseable START mean *no start was
    recorded*. A ``"day"`` claim about an absent datum is the defect #9201
    records; a ``"year"`` claim about it is the same defect one step further
    (#9244 — the write boundary accepts a declaration on a PRESENT-but-orderable
    bound, so a read path reporting that declaration would state a granularity
    for a start the ruling reads as never recorded, with no warning). Both the
    legacy default AND the declared token therefore sit behind the SAME
    orderability gate — the same ``_created_sort_key`` the read path orders with
    (``key[0] == 0`` ⇒ a parseable instant), never a second notion of
    "parseable". A numeric epoch ``0`` (or ``False``) IS an instant —
    1970-01-01T00:00:00Z — so it keeps ``"day"``.

    An ABSENT bound is not gated: there is nothing to order, and the
    declaration is the only datum there is. An absent bound with no declared
    token has no precision (``None``); an absent bound with ``"unknown"`` is
    the closed-unknown end (#2350's ``END_UNKNOWN`` marker). An absent bound
    with a concrete token is refused at the write boundary
    (``validate_validity_precision``), so reading it back as the caller
    declared it is not a lie about a stored instant.

    A NUMERIC bound (``int``/``float``) names an instant without consulting the
    ordering primitive — see ``_names_an_instant`` (#9201 review P3).
    """
    declared_token = normalize_precision(declared, field="precision")
    if bound is None:
        return declared_token
    if not _names_an_instant(bound):
        return None
    return declared_token or "day"


def end_state(valid_to: object, valid_to_precision: object) -> str:
    """The 3-state end of a window — ``END_OPEN`` | ``END_KNOWN`` | ``END_UNKNOWN``.

    ``END_UNKNOWN`` has TWO spellings with one meaning ("the window ended, but at
    an instant we cannot name"):

    * the DECLARED marker — ``validToPrecision == "unknown"`` with no
      ``validTo`` (a caller saying so explicitly);
    * a STORED end that names no instant — ``validTo = "TBD"`` / ``""`` /
      ``"2026-02-30"`` (#9202). Every row written before the 2026-10-26 shaping
      ruling can carry one, and presence is not datability.

    Otherwise a PRESENT, ORDERABLE ``validTo`` is ``END_KNOWN`` and an ABSENT one
    is ``END_OPEN``.

    Why a stored un-datable end is ``unknown`` — and not ``known`` or ``open``
    (#9202, owner ruling 2026-10-25, option B):

    * ``known`` claims the end is DATED — and since #9201 the same block reports
      ``valid_to_precision: None`` for that very value, so ``known`` contradicts
      its own triple. ``END_KNOWN`` now requires datability, not presence.
    * ``open`` claims NO END WAS ASSERTED — false: the column holds a value the
      author put there, and reading it as still-true is the fabricated-coverage
      anti-pattern this module's headline names (#2349/#2350). ``open`` stays
      reserved for an ABSENT bound, so the open/un-datable distinction survives.

    ``""`` is treated like any other un-datable value rather than as absent: the
    read path's ONE presence convention is ``is not None``, and ``_covers``
    cannot exclude either shape, so both windows cover every later instant.

    Neither coverage nor the READ of an existing row moves with this reading: an
    un-datable ``validTo`` names no instant, so it still bounds nothing (#9408),
    and a row that carries one is reported ``unknown`` exactly as it was a day
    earlier. What the 2026-10-26 ruling DID move is the write door — a caller
    value that declares no date is now SHAPED into the declared spelling rather
    than stored verbatim (``read_caller_end``), and one that attempts an instant
    and fails is REFUSED — so the second spelling is a legacy-row shape, not a
    shape this door produces. Rows written before that ruling are never
    re-validated or migrated: the read path does not care which form a row
    carries, which is what keeps the two dialects byte-identical here.

    An explicitly declared concrete precision on an absent end is a
    contradiction and is refused by ``validate_validity_precision``, so it is
    not silently resolved here.
    """
    token = normalize_precision(valid_to_precision, field=TO_PRECISION_PROP)
    if token == PRECISION_UNKNOWN:
        return END_UNKNOWN
    if valid_to is None:
        return END_OPEN
    if not _names_an_instant(valid_to):
        return END_UNKNOWN
    return END_KNOWN


def _end_refusal(value: object, fault: str) -> ValueError:
    """Count, log and describe ONE refused caller end (#9202).

    Every refusal names three things — the fault, the repair, and the forms the
    field accepts — because a refusal that does not is a retry loop (§7 trap 2).
    Returned (not raised) so the caller's ``raise ... from None`` keeps the
    caller-facing traceback free of this module's internals.
    """
    END_WRITE_OUTCOMES["refused"] += 1
    message = (
        f"validTo {value!r} was refused: {fault}. Pass {_END_ACCEPTED_FORMS}. "
        "The end is an authored belief about WHEN a fact stopped being true, so "
        "it is not guessed (ONTOLOGY §4.7, #9202)."
    )
    _logger.warning("validTo refused at the write door: %s", message)
    return ValueError(message)


def read_caller_end(value: object, declared_precision: object = None,
                    ) -> tuple[object, str | None]:
    """#9202: read a caller's ``validTo`` for INTENT on the way in.

    THE ONE HOME for the write door's accept / shape / refuse decision, so the
    two guarded doors (``create_point``, ``update_point``) cannot disagree about
    what a value means, and so no second notion of "is that an instant" appears
    next to ``_names_an_instant`` — the measure every read path already orders
    with. Returns ``(stored_value, shaped_token)``:

    * ``shaped_token is None`` — write ``stored_value`` as the end. Either the
      caller's value is a real orderable instant (returned UNTOUCHED: a stored
      instant is never re-spelled), or it is representational variance the read
      measure could not order (``2024-3-1``, ``2024/03/01``, and the same
      un-padded date beside a time: ``2024-3-1T12:00:00``), normalised to the
      ISO form **silently** — same instant, same meaning, nothing to report.
      Only the DATE is re-spelled; a time-of-day is kept as the caller wrote it,
      and the pair is accepted only when the read measure can order it.
    * ``shaped_token is not None`` — the value DECLARES NO DATE (blank, or one of
      ``END_UNKNOWN_MARKERS``). The caller must store the canonical un-datable
      form instead: no end value plus ``validToPrecision = "unknown"``, which
      reads ``END_UNKNOWN`` — NEVER ``END_OPEN``. Mapping a marker to ``open``
      would alter a reading the 2026-10-25 ruling pinned, i.e. smuggle a second
      decision in as a side effect, so it is an invariant of this function that
      every shaped token ends up ``unknown``.

    Everything else RAISES ``ValueError`` naming the fault and the repair: an
    attempted instant that fails (``2026-02-30``, month 13), an epoch supplied as
    a STRING, or no recoverable intent (``"not-a-date"``, and with it any
    day/month-first form such as ``03/04/2024`` — legible but ambiguous, so
    choosing silently would be betrayal dressed as competence, §0).

    ``declared_precision`` is the caller's own ``validToPrecision`` when it sent
    one: a CONCRETE token beside a marker is a contradiction ("day" cannot
    describe an end that declares no date) and is refused rather than silently
    dropped — dropping it would be the un-announced substitution this whole
    door exists to prevent.

    Why shaping a marker is not leniency (§0): "ended, and I cannot say when" is
    exactly what the caller said, so there is no better value they could supply —
    the filled-in meaning and the caller's meaning are the SAME CLAIM. Filling in
    is legitimate here and nowhere else.

    Pre-existing rows are untouched by design: this runs only on a value a
    caller supplies NOW, so a stored ``validTo = "TBD"`` keeps its literal end
    and keeps reading ``END_UNKNOWN`` (``end_state``). No backfill, no
    re-validation — a toaster that migrated rows would be a NEW decision.
    """
    if value is None:
        return None, None          # absent = OPEN end, not an un-datable one
    if _names_an_instant(value):
        return value, None         # a real instant: accepted, never re-spelled
    if isinstance(value, str):
        token = value.strip()
        if not token or token.lower() in _END_UNKNOWN_MARKER_SET:
            _refuse_concrete_precision_beside_marker(value, declared_precision)
            END_WRITE_OUTCOMES["shaped"] += 1
            _logger.warning(
                "validTo %r declares no date — stored as the canonical "
                "UN-DATABLE end (no validTo + validToPrecision='unknown'), so "
                "the record says 'ended, un-datable' instead of carrying text "
                "no reader can order; the reply reports end_state='unknown' "
                "(ONTOLOGY §4.7, #9202). Pass an ISO-8601 instant, the NUMBER "
                "for an epoch, or None for an OPEN end, if that is what you "
                "meant.",
                value,
            )
            return None, value
        match = _DATE_VARIANT_RE.match(token)
        if match:
            year, month, day = (int(match["year"]), int(match["month"]),
                                int(match["day"]))
            # The YEAR is checked FIRST so the refusal names the component that is
            # actually out of range (review P3): ``date()`` raises one bare
            # ValueError for every bad component, and the day arm below would
            # otherwise blame the day for ``0000-01-01`` — a year the proleptic
            # calendar has no year 0 for. 1-4 digits reach here, so MINYEAR is
            # the only bound that can be crossed.
            if year < MINYEAR:
                raise _end_refusal(
                    value,
                    f"year {year:04d} is not a calendar year (the earliest is "
                    f"{MINYEAR:04d})",
                ) from None
            if not 1 <= month <= 12:
                raise _end_refusal(
                    value,
                    f"month {month} is not a calendar month (a month runs "
                    "01-12)",
                ) from None
            try:
                canonical = date(year, month, day).isoformat()
            except ValueError:
                raise _end_refusal(
                    value,
                    f"{year}-{month:02d} has no day {day}, so this is not a "
                    "calendar day",
                ) from None
            rest = match["rest"] or ""
            if not rest:
                return canonical, None
            # Un-padded DATE fields beside a TIME: the padding is the whole
            # tolerance, so the date is finished and the caller's time is kept
            # verbatim — then the read measure decides whether the pair names an
            # instant at all (it does NOT re-implement "is that a time").
            if _names_an_instant(canonical + rest):
                return canonical + rest, None
            raise _end_refusal(
                value,
                f"the date part reads as {canonical} but {rest!r} is not an "
                "ISO-8601 time-of-day, so the whole value names no instant",
            ) from None
        if _EPOCH_AS_STRING_RE.match(token):
            # ⛔ THE REPAIR IS ONLY OFFERED WHEN IT LANDS SOMEWHERE SANE
            # (#10079 review P2). This arm used to prescribe
            # `pass the NUMBER {int(token)}` unconditionally, which is correct
            # only for a SECONDS epoch. Measured: `"1719792000000"` (13 digits,
            # a JS `Date.now()` in MILLISECONDS) was refused and handed the
            # repair `pass the NUMBER 1719792000000` — which the door then
            # ACCEPTS as seconds and stores at year 56,468, reading
            # `end_state="known"` with no error, and any render of that bound
            # raises `ValueError: year 56468 is out of range`. A repair that
            # manufactures the wrong instant is the retry-loop class this door
            # exists to prevent (§7 trap 2), so a digit-count match is not
            # enough to recommend a number.
            seconds = int(token)
            if _EPOCH_SECONDS_SANE_MIN <= seconds <= _EPOCH_SECONDS_SANE_MAX:
                raise _end_refusal(
                    value,
                    f"this is a Unix epoch supplied as a STRING, which names no "
                    f"instant — pass the NUMBER {seconds} instead of a string",
                ) from None
            # Name THIS input's shape, not a menu of every shape: a refusal that
            # lists both magnitudes tells a caller with 13 digits nothing that
            # distinguishes their case, and it cannot be pinned by a test (the
            # #10079 review round 2 P2 — the first version named both, so a
            # swapped 13<->14 mapping stayed green).
            ndigits = len(token.lstrip("-"))
            if ndigits == 13:
                shape = "that is a MILLISECOND epoch — divide by 1000 to get seconds"
            elif ndigits == 14:
                shape = ("that is usually a compact YYYYMMDDHHMMSS, which is not an "
                         "epoch at all")
            else:
                shape = ("at that magnitude the number would not name the instant "
                         "you meant")
            raise _end_refusal(
                value,
                f"this is a {ndigits}-digit numeric string, which names no "
                f"instant, and it is NOT offered back as a number: {shape} — while "
                "the field reads a NUMBER as SECONDS since 1970, so following that "
                "repair would store a confidently wrong instant",
            ) from None
    raise _end_refusal(
        value,
        "no date could be recovered from it (an orderable date is read "
        "year-first; a day/month-first form like '03/04/2024' parses both ways, "
        "so it is refused rather than guessed)",
    ) from None


def _refuse_concrete_precision_beside_marker(value: object,
                                             declared_precision: object) -> None:
    """Refuse a concrete ``validToPrecision`` beside an end that declares no date.

    ``validTo = "TBD"`` with ``validToPrecision = "day"`` states two different
    things about one bound. Shaping could overwrite the token and say nothing,
    which is precisely the silent substitution §0 forbids — so the pair is
    refused and the caller picks one.
    """
    token = normalize_precision(declared_precision, field=TO_PRECISION_PROP)
    if token is None or token == PRECISION_UNKNOWN:
        return
    END_WRITE_OUTCOMES["refused"] += 1
    message = (
        f"validTo {value!r} declares NO date, so {TO_PRECISION_PROP}={token!r} "
        f"cannot describe it — the two disagree. Pass "
        f"{TO_PRECISION_PROP}='{PRECISION_UNKNOWN}' with this value (the "
        "canonical un-datable end), or a real date alongside the granularity "
        f"you meant (ONTOLOGY §4.7, #9202)."
    )
    _logger.warning("%s", message)
    raise ValueError(message)


def validate_validity_precision(
    valid_from: object,
    valid_to: object,
    valid_from_precision: object,
    valid_to_precision: object,
) -> None:
    """Refuse a precision declaration inconsistent with the bounds it annotates.

    THE ONE HOME for the rule, called by every Point write that can persist a
    precision property (``sdk._refuse_inconsistent_validity_precision``). It
    checks the pair it is GIVEN; the writer resolves the pair a PARTIAL update
    will actually persist (caller bound merged with the stored opposite) before
    delegating — the same split ``validate_validity_window`` declares.

    Refused states, each of which would otherwise make a read path produce an
    answer the data does not support:

    * an unknown word as either precision (a typo must not be stored and then
      silently ignored by ``end_state``);
    * ``validFromPrecision == "unknown"`` — the 3-state vocabulary is END-only;
    * ``validToPrecision == "unknown"`` with a PRESENT ``validTo`` — the marker
      means "no date", so a coexisting date is a fabrication;
    * a concrete precision (year/month/day) with an ABSENT opposite bound — a
      granularity cannot attach to a bound that does not exist.

    A declaration on a PRESENT-but-UNORDERABLE bound is deliberately NOT
    refused: it is stored verbatim. The read path answers it honestly instead
    (`effective_precision` reports ``None``, #9244) — a ``"year"`` for a start
    that names no instant is the #9201 lie one step further. Refusing the pair
    here would narrow what ``create_point`` accepts, a wider contract change
    than that read fix.

    Raises ``ValueError``; callers that want a 422/ValueError boundary get one.
    """
    from_token = normalize_precision(valid_from_precision, field=FROM_PRECISION_PROP)
    to_token = normalize_precision(valid_to_precision, field=TO_PRECISION_PROP)

    if from_token == PRECISION_UNKNOWN:
        raise ValueError(
            f"{FROM_PRECISION_PROP}='unknown' is not legal — the 3-state "
            "vocabulary (open / closed-at-known-date / closed-unknown) "
            "describes the window END only; a window needs a datable start"
        )
    if to_token == PRECISION_UNKNOWN:
        if valid_to is not None:
            raise ValueError(
                f"{TO_PRECISION_PROP}='unknown' with a validTo "
                f"({valid_to!r}) is a contradiction — the marker means the "
                "end is un-datable, so no date may be fabricated alongside it"
            )
    elif to_token is not None and valid_to is None:
        raise ValueError(
            f"{TO_PRECISION_PROP}={to_token!r} requires a validTo — a "
            "granularity cannot describe a bound that does not exist"
        )
    if from_token is not None and valid_from is None:
        raise ValueError(
            f"{FROM_PRECISION_PROP}={from_token!r} requires a validFrom — a "
            "granularity cannot describe a bound that does not exist"
        )


def window_read_fields(
    valid_from: object,
    valid_to: object,
    valid_from_precision: object,
    valid_to_precision: object,
) -> dict:
    """The additive precision block every window-consuming READ path emits.

    ONE HOME for the read projection so ``search``/``recall`` annotation,
    ``assembly``, and ``get_historical_knowledge`` cannot drift in how they
    name or resolve the precision of a window. The returned dict is meant to
    be merged into an existing entry; the legacy default (no precision props,
    a dated bound) yields ``day`` for a present bound and ``open`` for an
    absent end — the pre-#2350 reading.

    Callers pass the bounds RAW. The presence gate is ``is not None`` (inside
    ``effective_precision``/``end_state``), so a falsey-but-present bound —
    ``""``, ``0`` — is a REAL bound and keeps its block. Pre-normalizing at a
    call site (``bound or None``) makes the same stored window resolve
    differently per surface, which is the drift this home exists to prevent
    (#2350 review P2).

    The block ALWAYS carries the three keys: a present-but-unorderable bound
    yields ``None`` for its precision — declared token or not (#9201, #9244) —
    and the key is still emitted, so no consumer has to distinguish "no
    precision" from "this surface did not report it". This holds on BOTH ends,
    and the triple never contradicts itself: a present-but-unorderable
    ``validTo`` reports ``valid_to_precision: None`` beside ``end_state:
    "unknown"`` (#9202 — the end is not dated, so it is neither ``known`` nor
    ``open``; see ``end_state``).
    """
    return {
        "valid_from_precision": effective_precision(valid_from, valid_from_precision),
        "valid_to_precision": effective_precision(valid_to, valid_to_precision),
        "end_state": end_state(valid_to, valid_to_precision),
    }
