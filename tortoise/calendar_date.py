"""#11646 — the ONE resolver for a stored date value's calendar day.

Three shipped seams read a stored date by assuming ISO, so the ingest
producer's real shape — ``2023/05/20 (Sat) 02:21`` — silently parsed to
``None``: the assembly spine dated every eval row UNDATED, the shipped
valid-time selection evaluated a *present* question date at the wall clock,
and the C6 dense anchor injected nothing. Each had grown its own parser and
they had drifted; this module is the single home for the shape set.

The shape set is measured, not assumed: the LongMemEval corpus emits
``YYYY/MM/DD (Day) HH:MM`` for **500/500** questions and for every
``haystack_dates`` entry — **0** ISO (re-measured for #11646; #3011 measured
the same shape 23,867/23,867 on the frozen dataset). Around it, this repo's
own writers add ISO dates (``YYYY-MM-DD``), ISO timestamps (``Z`` or an
offset), and the legacy compact (``YYYYMMDD``) / ISO-week (``YYYY-Www-D``)
spellings ``date.fromisoformat`` accepts; ``supersede_point`` can copy a
numeric epoch into a window. All of them are accepted here, and the result is
always validated through ``date``, so ``2026-13-45`` is undated rather than a
fabricated date.

⛔ THE RULE IS A UNION — over the values whose leading calendar date is
UNAMBIGUOUS. Three of the replaced parsers read a calendar-date PREFIX and one
was as tolerant as ``datetime.fromisoformat``, so this resolver deliberately
keeps BOTH tolerances as documented fallbacks: the whole-value grammar first (it
knows about offsets, which is what the UTC truncation needs), then a prefix
reading for a value whose TAIL is unreadable (``2023-05-20 02:21 PDT``,
``2023/05/20 (Sat) 02:21 (approx)``, ``2023-05-20T+05:00``) — an unparseable tail
means the exact INSTANT is unknown, not that the stated DAY is.

⚠ FOUR DELIBERATE EXCLUSIONS from that union, all of them the fabricated-day
class this module exists to end, stated so a later reader does not "restore the
union" by re-adding them:

* a THREE-DIGIT day (``2023/04/105``, ``2023-05-072``) — the old ``s[:10]`` +
  ``fromisoformat`` path truncated it to a real date; that is a day nobody
  wrote;
* a pure 9+-digit number read as a date (``20230520.3``, ``202305201112``) —
  the same truncation artifact seen from the other side. Both are now UNDATED,
  by the same digit-count rule that refuses a 9+-digit epoch STRING (see the
  epoch rule below); neither is read as an epoch — the pointer this line used
  to carry was false for both of its own examples;
* a compact / ISO-week date with a MALFORMED offset tail (``20230520+5:00``,
  ``20230520+050``, ``2023W206+5:00``) — ``origin/main`` dated these by
  truncating at ten characters, and the offset gate below deliberately accepts
  only a WELL-FORMED offset (``[+-]\\d{2}`` and up), because a loose gate is the
  same truncation artifact wearing an offset's clothes. No in-repo writer emits
  any of them; if one ever does, widen the gate to ``[+-]\\d{1,2}`` and say so
  here, rather than letting the prefix reading guess a day;
* a DASHED / slashed date with a DIGIT immediately after the day field
  (``2023-05-201``, ``2023-05-2012:00``, ``2023-05-20123``) — same class, other
  side: ``origin/main`` cut at ten characters and dated the day ``20``, but the
  text cannot say whether ``201`` is a three-digit day or day ``20`` with a
  glued ``1``. This is deliberately the OTHER half of the three-digit-day
  exclusion above, which is why the ``(?!\\d)`` guard in
  ``_CALENDAR_SHAPE_RE`` is load-bearing: loosening it to keep ``2023-05-201``
  as 2023-05-20 would also re-date ``2023-05-072`` as 2023-05-07, i.e. re-add
  the fabrication. A ``HH:MM``-suffixed spelling that genuinely needs the day
  gets its own explicit arm, never a loosened guard.

⛔ MONTH GRANULARITY IS NOT COERCED. A value naming only ``YYYY/MM`` (or
``2026-09``) has NO day, and this resolver returns ``None`` rather than
inventing the 1st. Inventing one would silently place a month-granularity
value at a specific position inside that month — ahead of every row dated
within it — and no in-repo producer emits that shape, so the "fix" could only
ever invent days for hand-built input. Undated is the honest answer; the
caller decides what undated means (#8710's rule: unresolvable ⇒ no bound,
never matches-nothing).

TWO DAY SEMANTICS, both required, so the caller states which it wants:

* :func:`calendar_date` — the calendar day **as written**. The default
  (``utc_truncate=False``) is the prefix reading every already-fixed surface
  uses (``coverage_loop._calendar_date``, ``sdk._iso_date10``): a session
  stamped ``2026-09-01T00:30+02:00`` is the 1st, the day the dataset says.
* ``utc_truncate=True`` — an instant truncates in **UTC**, which is what the
  assembly ladder needs for its as-of day boundary (a host-local boundary
  would scramble the equality day and the Task-5 byte-goldens). Date-only
  values and naive timestamps keep their written day either way; only an
  OFFSET-bearing instant can differ, and only across a day boundary.

:func:`calendar_datetime` is the instant view, for a consumer that ORDERS
rather than buckets (``search_engine._created_sort_key``). It mirrors the
#3982 anchoring rule: a date-only value is UTC midnight (a stated offset
wins), while a zone-less date-TIME keeps the LOCAL reading that rule
deliberately left alone.

⛔ THE EPOCH ASYMMETRY IS A RECORDED RULING, not an oversight. A NUMERIC epoch
is a date to both functions. An epoch spelled as a STRING (``"1900000000"``)
is a date to :func:`calendar_date` — ``time_aware._as_date`` has always read
it that way, and naming a day makes no instant claim — but NOT to
:func:`calendar_datetime`, because #9202 (owner ruling, ``ONTOLOGY.md`` §4.7)
makes a present-but-un-orderable window end ``END_UNKNOWN`` and names the
number as the fix; dating the string here would be a fourth notion of "names
an instant", the exact defect #9202 exists to remove. The string-epoch reading
is BOUNDED by DIGIT COUNT, so a pure number is never guessed: 8 digits is a
compact DATE (``20230520``), 9-10 digits is a SECONDS epoch
(``1684549260``), and 11+ digits is undated — that is the millisecond shape
``validity`` names and refuses at the write door (#10079), not something this
module should turn into a year-8380 date. Two consequences stated rather than
left to be discovered: **1-7 digit strings are undated** (a bare ``"2023"`` is
not a year-shaped date; the NUMBER ``2023`` is still the epoch it always was, as
``time_aware._as_date`` reads it), and a 10-digit ``YYYYMMDDHH`` spelling
(``2023052011``) is read as an EPOCH, not as a date-with-hour — the old
``_norm_date`` read it as 2023-05-20 while ``_as_date`` read it as the epoch, so
the two disagreed; one documented rule (digits, not ambiguity) settles it.

⛔ SO THE UNION HAS ONE MORE STATED EXCEPTION — the "leading calendar date is
UNAMBIGUOUS" qualifier above is where a reader is otherwise misled into
thinking that no value the replaced parsers read may stop parsing at all. The
exception is a pure-digit COMPACT TIMESTAMP, ``YYYYMMDDHH`` / ``YYYYMMDDHHMM`` /
``YYYYMMDDHHMMSS``:
``date.fromisoformat`` reads the LEADING EIGHT digits of such a string as a
day (it ignores the unreadable tail), so ``origin/main``'s ``assembly._norm_date``
dated them, and this module does not. Measured at the value level, against
``origin/main``'s own body:

    ``2023052011``      main ``_norm_date`` 2023-05-20 · here 2034-02-08 (a 10-digit epoch)
    ``202305201122``    main ``_norm_date`` 2023-05-20 · here ``None`` (11+ digits)
    ``20230520112233``  main ``_norm_date`` 2023-05-20 · here ``None``

**The two readings cut the as-of window in OPPOSITE directions, which is why
this is named rather than left as a footnote.** In ``assembly.collect_slices``
a *dated* row after the question is EXCLUDED from the as-of slice, while an
*undated* row is RETAINED and sorted last. So the 10-digit reading above drops
a row ``main`` displayed, and the 11+ reading moves a row that was dated to the
end of the spine while letting it escape the as-of cut. Neither direction is
silently safe. No in-repo producer writes any of these shapes (the ingest lane
writes ``2023/05/20 (Sat) 02:21``; ``validity.read_caller_end`` refuses a 9+
digit numeric string at the write door), so reachability is a directly written
graph property or a legacy row — the legacy class this module still serves. Nor
is this the compact-with-OFFSET family (``20230520-0500``, ``2026W243-05:00``):
that reads through :func:`_compact_date_offset` on a WELL-FORMED offset as its
gate, never on trailing digits, so it stays dated — while a MALFORMED offset
tail is one of the three stated exclusions above. The digit-count rule is
deliberate and pinned
(``tests/test_11646_producer_date_shape.py::test_a_pure_number_is_never_truncated_into_a_fabricated_day``);
reversing it is a REOPEN of that rule, not a bug fix.

⛔ THIS ENUMERATION IS NOT EXHAUSTIVE, and it is stated that way rather than as a
closed list because measurement found three more classes in the same DIRECTION
(value was dated on ``origin/main``, undated here) that no cycle had named:

    ``_norm_date("2023-W43-2x")``            main 2023-10-24 · here ``None``
    ``_norm_date("2023-W43-2(Sat)02:21")``   main 2023-10-24 · here ``None``
    ``time_aware._as_date("1e9")``           main '2001-09-09' · here ``None``
    ``time_aware._as_date("1.9e9")``         main '2030-03-17' · here ``None``

The first two are the ISO-WEEK analogue of the compact-timestamp exception (the
old ``s[:10]`` truncated to a complete week date); the last two are ``float()``
epoch SPELLINGS, which ``_as_date`` used to accept and the digit rule now refuses
(a pure number still names an epoch; an exponential spelling of one does not).
Every class found so far moves in the SAFE direction — a value is undated rather
than future-dated — which is why none is repaired here: repairing any of them
would widen the parse union past what the recorded digit rule allows. What this
paragraph exists to prevent is the opposite: a reader who takes the three named
exclusions as the WHOLE of the exception surface, and therefore trusts the union
claim absolutely.

The undated sentinel (``tools/longmem_eval/ingest.UNDATED_SENTINEL``) is a
POSITIVE date that means "no date". It is EXPORTED, never applied: callers
differ deliberately — the assembly ladder floors ``<= 1970-01-01`` (any
pre-1970 artifact is unusable there), ``coverage_loop`` rejects exactly that
date, and the ``time_aware`` seams strip nothing.

Deliberately NOT routed here: ``subgraph_render._parse_date`` (a FROZEN render
spec, padded-only by contract) and ``sdk._iso_date10`` (already correct, and
its callers render a session label, not a bound).
"""
from __future__ import annotations

import math
import re
from datetime import UTC, date, datetime, timedelta, timezone

__all__ = [
    "UNDATED_SENTINEL_DATE",
    "calendar_date",
    "calendar_datetime",
]

#: The ingest lane's "no date" marker — a POSITIVE value that means UNDATED,
#: in every ISO spelling of its calendar date. Mirrored (not imported) from
#: ``tools/longmem_eval/ingest`` so the product module never imports the eval
#: harness: a fabricated 1970 date is worse than no date, and as a BOUND it
#: turns an unresolvable input into a window that matches (or excludes)
#: everything.
UNDATED_SENTINEL_DATE = "1970-01-01"

#: The calendar-date PREFIX — the shapes both date sources actually arrive in,
#: ISO (``2026-09-01`` / ``2026-09-01T00:00:00Z``) and the producer's
#: ``2023/05/20 (Sat) 03:29``. Month/day are ``\d{1,2}``: an UNPADDED
#: ``2023/4/9`` is the same calendar date, and reading it as undated would drop
#: that session from a bounded span (the session-level #8710 defect). The
#: trailing ``(?!\d)`` keeps a 3-digit day (``2023/04/105``) from being
#: truncated to two. Moved here from ``coverage_loop`` (#11646) so the shape
#: set has ONE home instead of one copy per seam.
#:
#: Its TWO roles are deliberate: it DUPLICATES the leading calendar-date prefix
#: of ``_INSTANT_RE`` (below) — a re-spelling, not a reference, so the two
#: cannot drift by construction only if a test keeps them agreeing — AND it is
#: the TOLERANT last-resort reading for a value the whole-value grammar rejects
#: but whose leading date is unmistakable (``2023-05-20 02:21 PDT``,
#: ``2023/05/20 (Sat) 02:21 (approx)``, ``2023-05-20T+05:00``). Every seam that
#: read a prefix before #11646 accepted those, so a strictly-anchored grammar
#: would have made the union claim false in exactly the direction the issue
#: exists to fix (a real date silently becoming undated) — an unreadable TAIL
#: means the exact INSTANT is unknown, not that the stated DAY is.
#:
#: ⛔ The duplication is DELIBERATE and it is NOT test-enforced: no test
#: references either pattern, so a narrowing of this tolerant prefix that
#: ``_INSTANT_RE`` does not follow would leave the suite green. Keep them
#: agreeing by hand (this prefix is the LAST-RESORT arm, so it must stay at
#: least as wide as the whole-value grammar's — narrowing it silently undates
#: a value whose only defect is an unreadable tail).
_CALENDAR_SHAPE_RE = re.compile(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?!\d)")

#: A WHOLE-value instant: the calendar prefix, an optional producer weekday
#: label, an optional time of day, and an optional offset. The weekday label
#: (``(Sat)``) is REDUNDANT and is deliberately not checked against the date —
#: a mislabelled day must never make a real date unparseable. The time grammar
#: is deliberately as WIDE as ``datetime.fromisoformat`` accepts it (an
#: hour-only ``T02``, comma OR dot decimals, more than 6 fractional digits —
#: ``fromisoformat`` takes all three), because every narrowing here would make a
#: value that parsed before stop parsing (the union rule).
_INSTANT_RE = re.compile(
    r"^(?P<year>\d{4})[-/](?P<month>\d{1,2})[-/](?P<day>\d{1,2})(?!\d)"
    r"(?:\s*\([A-Za-z]{2,9}\))?"
    r"(?:[T\s]+(?P<hour>\d{2})"
    r"(?::(?P<minute>\d{2})(?::(?P<second>\d{2})(?:[.,](?P<fraction>\d+))?)?)?)?"
    # the OFFSET's own fractional seconds are part of the offset grammar, not
    # the time's: ``+05:00:00.5`` is an offset ``fromisoformat`` accepts, and a
    # grammar that refused it fell through to the prefix reading — stating the
    # WRITTEN day where this seam must truncate in UTC (cycle 6, finding 1).
    r"\s*(?P<offset>[Zz]|[+-]\d{2}(?::?\d{2}(?::?\d{2}(?:[.,]\d+)?)?)?)?\s*\Z"
)

#: The same value with a BASIC-format (colon-less) time — ``2023-05-05T0221+05``,
#: ``2023-05-05T022100+05``, ``2023-05-05 0221+05``. ``datetime.fromisoformat``
#: reads the mixed extended-date/basic-time form (Python 3.11+), which is what
#: the assembly ladder used, so it must reach the grammar: the prefix fallback
#: would return the WRITTEN day where the old reader truncated in UTC, i.e. a
#: silent DAY CHANGE on an aware instant (review cycle 4, finding 1).
#: A separate arm, not looser separators on ``_INSTANT_RE``: optional colons
#: there would also accept a MIXED spelling (``02:2100``) that
#: ``fromisoformat`` REJECTS, and reading it as 02:21:00 + offset would move a
#: day the old reader kept at the written value.
_BASIC_TIME_INSTANT_RE = re.compile(
    r"^(?P<year>\d{4})[-/](?P<month>\d{1,2})[-/](?P<day>\d{1,2})(?!\d)"
    r"(?:\s*\([A-Za-z]{2,9}\))?"
    r"[T\s]+(?P<hour>\d{2})(?P<minute>\d{2})(?P<second>\d{2})?"
    r"(?:[.,](?P<fraction>\d+))?"
    r"\s*(?P<offset>[Zz]|[+-]\d{2}(?::?\d{2}(?::?\d{2}(?:[.,]\d+)?)?)?)?\s*\Z"
)

#: The date-only spellings ``date.fromisoformat`` accepts beyond the dashed
#: calendar form: compact (``20261020``) and ISO week (``2026-W43-2``,
#: ``2026W432``, week-only). Kept because the assembly ladder has always
#: accepted them — this union must not drop a value that already parsed.
#: ANCHORED to the whole value: an unanchored ``\d{8}`` would truncate a 9+
#: digit EPOCH spelling (``2020010112`` = 2034-01-04) to its first eight digits
#: and invent 2020-01-01 — the fabricated day this module forbids, and a window
#: bound read fourteen years early.
_COMPACT_OR_WEEK_RE = re.compile(r"^(?:\d{8}|\d{4}-?W\d{2}(?:-?\d)?)\Z")

#: A COMPACT / ISO-week date carrying a TIME (``20230520T02:21:00Z``,
#: ``2023-W43-2T11:00:00``). ``datetime.fromisoformat`` reads these whole, so
#: they are the one family the whole-value grammar above cannot express (it needs
#: dashed separators) while the pre-#11646 assembly parser did read them (its
#: ``T`` branch went straight to ``fromisoformat``). Without this arm such a row
#: becomes UNDATED on the spine — the very failure #11646 removes, on another
#: input. Pure-digit strings are excluded by construction (no ``T``/space/``W``
#: tail), so the digit-count EPOCH rule below keeps them.
_COMPACT_DATETIME_RE = re.compile(
    r"^(?:(?:\d{8})|(?:\d{4}-?W\d{2}(?:-?\d)?))[T\s]+")

#: A COMPACT / ISO-week DATE carrying an OFFSET and no time (``20260610-05:00``,
#: ``2026W243-05:00``, ``20230520-0500``, ``2023-W43-2+05:00``). This family is
#: DECLARED REAL by ``tests/test_3982_date_only_utc.py`` (its compact-with-offset
#: assertion exists to fail if the alternatives are dropped), and the old
#: assembly parser dated them through ``date.fromisoformat``'s tolerance. The
#: OFFSET must be an offset, never trailing digits — that gate is what keeps
#: cycle 1's fabricated-day class (`\d{8}` truncating an epoch spelling) shut.
#: The day returned is the WRITTEN one, which is what the dashed date-only +
#: offset path returns too (``utc_truncate`` applies only to a TIME-bearing
#: instant) and what ``origin/main`` returned.
_COMPACT_DATE_OFFSET_RE = re.compile(
    r"^(?P<date>\d{8}|\d{4}-?W\d{2}(?:-?\d)?)"
    r"(?P<offset>[Zz]|[+-]\d{2}(?::?\d{2}(?::?\d{2}(?:[.,]\d+)?)?)?)$")

_OFFSET_RE = re.compile(
    r"^(?P<sign>[+-])(?P<hours>\d{2})"
    r"(?::?(?P<minutes>\d{2})(?::?(?P<seconds>\d{2})(?:[.,]\d+)?)?)?$")

#: A pure-number string in the SECONDS-epoch range (9-10 digits, optional
#: fraction). 8 digits is a compact date; 11+ is a millisecond epoch. The sign
#: is SYMMETRIC on purpose: ``time_aware._as_date`` read a signed epoch through
#: ``float()``, so ``+1684549260`` must not become undated while
#: ``-1684549260`` stays dated (review cycle 4, finding 2).
_EPOCH_STRING_RE = re.compile(r"^[+-]?\d{9,10}(?:\.\d+)?$")


def calendar_date(value: object, *, utc_truncate: bool = False) -> date | None:
    """``value``'s calendar day, or ``None`` when it names no day.

    Accepts a ``date``/``datetime``, a numeric epoch, and the string shapes in
    the module docstring. ``None`` means UNDATED (absent, empty, free text, a
    month-granularity value, an out-of-range date, the sentinel when the caller
    strips it) — never a fabricated day, and never an exception.

    ``utc_truncate=True`` truncates an OFFSET-bearing instant in UTC instead of
    reading its written day. See the module docstring for which consumers need
    which; do not flip it without re-reading them.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):  # before date — datetime IS a date
        if utc_truncate and value.tzinfo is not None:
            return _utc_day(value, value.date())
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        return _date_from_epoch(value)
    return _string_date(str(value).strip(), utc_truncate=utc_truncate)


def calendar_datetime(value: object) -> datetime | None:
    """``value``'s instant view, or ``None`` when it orders on no instant.

    A date-only value anchors to UTC midnight (a stated offset wins); a
    zone-less date-TIME stays NAIVE, the reading #3982 deliberately left to
    ``datetime.timestamp()``. A numeric epoch is an instant; an epoch spelled
    as a STRING is not (#9202 — see the module docstring).
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    if isinstance(value, (int, float)):
        return _instant_from_epoch(value)
    return _string_instant(str(value).strip())


# ── internals ───────────────────────────────────────────────────────────────


def _instant_from_epoch(value: float) -> datetime | None:
    try:
        return datetime.fromtimestamp(float(value), UTC)
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _date_from_epoch(value: float) -> date | None:
    instant = _instant_from_epoch(value)
    return None if instant is None else instant.date()


def _string_date(s: str, *, utc_truncate: bool) -> date | None:
    """The day a STRING names — the whole-value instant grammar, then the
    date-only spellings, then an epoch spelling (see the #9202 asymmetry), and
    finally the TOLERANT prefix reading (see ``_CALENDAR_SHAPE_RE``)."""
    if not s:
        return None
    for instant_re in (_INSTANT_RE, _BASIC_TIME_INSTANT_RE):
        match = instant_re.match(s)
        if match is not None:
            d = _match_date(match)
            if d is None:
                return None
            if utc_truncate and match.group("hour") is not None:
                naive = _match_naive(match)
                offset = _match_offset(match)
                if naive is not None and offset is not None:
                    return _utc_day(naive.replace(tzinfo=offset), d)
            return d
    d = _date_only_shape(s)
    if d is not None:
        return d
    d = _compact_date_offset(s)
    if d is not None:
        return d
    d = _compact_datetime_date(s, utc_truncate=utc_truncate)
    if d is not None:
        return d
    epoch = _as_epoch(s)
    if epoch is not None:
        return _date_from_epoch(epoch)
    # tolerant fallback: a rejected tail does not make a stated day unknown
    return _prefix_date(s)


def _string_instant(s: str) -> datetime | None:
    if not s:
        return None
    match = _INSTANT_RE.match(s) or _BASIC_TIME_INSTANT_RE.match(s)
    if match is not None:
        naive = _match_naive(match)
        if naive is None:
            return None
        offset = _match_offset(match)
        # ⛔ A STATED offset that is not a real one makes the instant
        # UNKNOWABLE — it does not make the value zone-less. ``_match_offset``
        # returns ``None`` for both "no offset stated" and "offset stated and
        # refused" (``+25:00``, ``+00:99``), so without this guard a value that
        # names a bad offset fell through to the naive reading and
        # ``validity._names_an_instant`` then reported END_KNOWN for a bound
        # whose instant cannot be computed — the exact false PASS #9202 exists
        # to deny. Measured before the guard:
        # ``_names_an_instant("2023/05/20T02:00:00+25:00") is True``.
        if match.group("offset") and offset is None:
            return None
        if match.group("hour") is None:
            # date-only: UTC midnight, a stated offset wins (#3982)
            return naive.replace(tzinfo=offset or UTC)
        return naive if offset is None else naive.replace(tzinfo=offset)
    d = _date_only_shape(s)
    if d is not None:
        return datetime(d.year, d.month, d.day, tzinfo=UTC)
    # ⛔ The instant view must accept the whole-value families THIS ARM owns —
    # the compact / ISO-week date carrying a TIME (cycle 6, finding 3).
    # ``_string_date`` carries this arm because ``origin/main``'s assembly
    # parser read the family, and this module's own docstring points an
    # ordering consumer here. Without it
    # ``calendar_datetime("20230520T02:21:00Z")`` is None while
    # ``calendar_date`` dates it — a later consumer would read a dated family as
    # un-orderable.
    #
    # ⚠ NOT the same shape set as the day view, and the difference is
    # DELIBERATE (cycle 7, finding 1 — an earlier version of this comment
    # claimed parity, which would invite the one change this module forbids):
    # the day view's TOLERANT PREFIX fallback dates values whose tail is
    # unreadable (``2023-05-20 02:21 PDT``, ``… (approx)``, ``T24:00``), and the
    # instant view must NOT widen to it. An unreadable tail means the INSTANT is
    # unknown while the stated DAY is known; deriving an instant there would
    # fabricate one, which is the whole distinction this module exists to keep.
    # So: the INSTANT view takes whole-value families only; the DAY view takes
    # those AND the tolerant prefix.
    #
    # ⚠ The claim is scoped to THIS arm, not universal: the compact /
    # ISO-week date with an OFFSET and no time is whole-value and day-dated
    # (:func:`_compact_date_offset`) yet still NOT orderable —
    # ``_created_sort_key``'s length-and-separator gate needs a ``-``, ``T`` or
    # ``/``, which ``20230520+05:00`` does not have. That gap predates #11646,
    # is untouched here, and is named rather than left to be rediscovered as a
    # violation of this comment.
    if _COMPACT_DATETIME_RE.match(s) is not None:
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _date_only_shape(s: str) -> date | None:
    """Compact / ISO-week date-only spellings, validated by the stdlib."""
    match = _COMPACT_OR_WEEK_RE.match(s)
    if match is None:
        return None
    try:
        return date.fromisoformat(match.group(0))
    except ValueError:
        return None


def _utc_day(stamp: datetime, fallback: date) -> date:
    """``stamp``'s UTC day, or ``fallback`` when the conversion overflows.

    ``date.max``/``date.min`` sit at the boundary where a stated offset can push
    the instant out of the representable range. ``_norm_date`` promises never to
    raise (assembly calls it UNGUARDED inside a per-row loop, and ten call sites
    pass a question date through it), so an unrepresentable instant answers with
    the day the value STATES — never an exception out of a read path.
    """
    try:
        return stamp.astimezone(UTC).date()
    except (OverflowError, ValueError, OSError):
        return fallback


def _compact_date_offset(s: str) -> date | None:
    """The day of a compact / ISO-week date that carries an OFFSET, no time."""
    match = _COMPACT_DATE_OFFSET_RE.match(s)
    if match is None:
        return None
    try:
        return date.fromisoformat(match.group("date"))
    except ValueError:
        return None


def _compact_datetime_date(s: str, *, utc_truncate: bool) -> date | None:
    """The day of a COMPACT / ISO-week date that carries a time.

    ``datetime.fromisoformat`` (the reader the assembly ladder used before
    #11646) accepts these whole, so they must not become undated here. An
    offset-bearing value truncates in UTC when the caller asks for it, exactly
    like the dashed form.
    """
    if _COMPACT_DATETIME_RE.match(s) is None:
        return None
    try:
        stamp = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if utc_truncate and stamp.tzinfo is not None:
        return _utc_day(stamp, stamp.date())
    return stamp.date()


def _prefix_date(s: str) -> date | None:
    """The leading calendar date of a value the whole-value grammar rejected.

    The written day is the answer: an unparseable TAIL (`` PDT``, ``(approx)``,
    a bare ``T+05:00``) means the value's exact instant is unknown, not that its
    stated day is. Validation still goes through ``date``, so ``2023/04/105``
    and ``2026-13-45`` stay undated.
    """
    match = _CALENDAR_SHAPE_RE.match(s)
    if match is None:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)),
                    int(match.group(3)))
    except ValueError:
        return None


def _as_epoch(s: str) -> float | None:
    """A bounded SECONDS-epoch reading of a pure-number string.

    8 digits is a compact DATE (``20230520``), so the epoch arm needs 9+; and it
    stops at 10 digits, the seconds range this repo writes. Beyond that a number
    is a MILLISECOND epoch (``validity`` names that shape and the write door
    refuses it with the number named as the fix, #10079) — reading it here would
    yield year-8380 dates from a 12-digit value. Not reading it is UNDATED,
    which is the honest answer for a shape no reader of this module writes.
    """
    if not _EPOCH_STRING_RE.match(s):
        return None
    try:
        epoch = float(s)
    except (ValueError, TypeError):
        return None
    return epoch if math.isfinite(epoch) else None


def _match_date(match: re.Match[str]) -> date | None:
    try:
        return date(int(match.group("year")),
                    int(match.group("month")),
                    int(match.group("day")))
    except ValueError:
        return None


def _match_naive(match: re.Match[str]) -> datetime | None:
    d = _match_date(match)
    if d is None:
        return None
    hour = match.group("hour")
    try:
        return datetime(
            d.year, d.month, d.day,
            0 if hour is None else int(hour),
            int(match.group("minute") or 0),
            int(match.group("second") or 0),
            # >6 fractional digits are truncated, exactly as the stdlib does
            int((match.group("fraction") or "0")[:6].ljust(6, "0")),
        )
    except ValueError:
        return None

def _match_offset(match: re.Match[str]) -> timezone | None:
    raw = match.group("offset")
    if not raw:
        return None
    if raw in ("Z", "z"):
        return UTC
    parts = _OFFSET_RE.match(raw)
    if parts is None:
        return None
    hours = int(parts.group("hours"))
    minutes = int(parts.group("minutes") or 0)
    seconds = int(parts.group("seconds") or 0)
    if hours > 23 or minutes > 59 or seconds > 59:
        return None
    delta = timedelta(hours=hours, minutes=minutes, seconds=seconds)
    return timezone(-delta if parts.group("sign") == "-" else delta)
