"""#2165 — pure pre-retrieval shape classifier + subject-term extraction (PRCA).

Deterministic (zero-LLM, zero-retrieval) router that owns the fired decision
for the connected-assembly branch in ``ask_lane.run_ask_lane()``. High-precision ordered
regexes over the question text; nothing else — no model, no graph.

Shapes with MEASURED census support (tests/_assembly_census.json — the 133-Q
temporal taxonomy, see docs/plans/2026-09-08-2165-connected-assembly.md):

* ``current-state`` — "what is the current status of X?" (fixture canary;
  the census's 2 current-state rows are duration-morphology and correctly do
  NOT fire — the measured fireable subset is the fixture + eval lane).
* ``ordering`` — two-subject "Which X first, A or B?" / "Who ... first,
  A or B?" (the census's ordering/compare 34: the shape-typical rows fire;
  duration/count/N-ary rows that the SEMANTIC census filed under
  ordering/compare do NOT — measured below) AND the N-ARY option list
  (#2653): "the order ...: A, B, and C" / "first, A, B or C" — 3+ named
  options decomposed over the SAME dated spine (see the N-ary section
  below).
* ``relative-date-lookup`` — "last Saturday", "on Valentine's day", "the
  past weekend": a DETERMINISTIC calendar anchor (fixed-date holiday, or a
  weekday/weekend offset from ``question_date``) feeding an as-of EQUALITY-DAY
  lookup over the typed spine (#2655). Measured coverage of the census class is
  3/6 — the class's other 3 rows name no calendar anchor at all (one is an
  ``ago``-offset day, #2652's class; one is ordering-within-a-month; one is
  frequency/count, #2886's class) and stay non-firing by design.
* ``interval`` — "How many days/weeks/months/years passed between A and B"
  and the two-anchor since-until form "…had passed since X when Y".
* ``duration-state`` — #2654: "how long had I been <state> when <event>?"
  (the census's 7 duration-state rows MINUS the two whose morphology is not
  the state-entry form — the combined summed span b9cfe692, which belongs
  to #2886's aggregate lane, and the age form d01c6aa8). Single-subject:
  the window's GRAPH end is the subject's state entry (the ObjectRegistered
  stamp or its earliest dated row — see ``_state_entry_date``), its other
  end the question's as-of date. The "when <event>" clause is question
  context, never a second resolved subject.
* ``ago-relative`` (#2652) — 'ago'-relative / implicit-time: an explicit
  offset ("two weeks ago", "a couple of days ago"), the census COUNT form
  ("how many weeks ago did I …"), or "most recently". The offset is
  resolved against ``question_date`` into an as-of WINDOW by
  :func:`resolve_relative_anchor` (deterministic, no LLM, no graph read);
  the selector is the TIME, not an Object (so ``extract_subject_terms``
  returns [] and the R1 both-halves gate is replaced by the
  window-resolved gate). RE-MEASURED against the census: ago-relative
  31/31 + recency 2/2 route (plus 1 morphologically-promoted mis-file).
* ``class-plural`` (#8056) — temporal ENUMERATION of a CLASS: "What is the
  order of the concerts and musical events I attended in the past two
  months, starting from the earliest?". The question names NO subjects, so
  every other shape (all of which need resolvable Object anchors) returned
  None; the selector is WINDOW ∩ CLASS, both resolved deterministically from
  the question text + ``question_date`` by :func:`resolve_class_window` (no
  LLM, no graph read, no entity resolution — a class is not an Object, so
  ``extract_subject_terms`` returns [] and the R1 both-halves gate is
  replaced by the class-resolved gate). RE-MEASURED against the census: the
  class-plural family is 6 rows across three census labels, of which the 3
  with a BOUNDED RELATIVE window fire (3/6); the other 3 stay non-firing —
  see the never-fires list below for each row's reason.

Never fires (R13/R16 + the residual measured negative set — RE-MEASURED
2026-10-11 after #2652 promoted the ago/recency vocabulary and #2654 moved
the state-entry duration-state rows into a shape, and 2026-10-16 after
#8056 promoted the two class-plural rows with a bounded relative window
out of ``recency/current-state``; of the residual rows only the adjudicated
label-noise rows fire, so the floor's non-adjudicated denominator is
rejected — see the floor test):

* the residual census negatives — the measured floor denominator after ALL
  FIVE promotions: #2652 removed the 34 promoted ago/recency rows (31 + 2
  + the 1 morphologically-promoted mis-file), #2653 re-measured
  nary-ordering (2 genuine named-subject rows now fire; its other 2 rows
  adjudicated), #2654 removed the 5 state-entry duration-state rows it now
  fires on, #2655 removed the calendar-anchored rows of
  ``relative-date-lookup`` (plus the 3 rows the census misfiled OUT of the
  class — see ``_RELATIVE_DATE_CLASS_EXPANSION`` in the test file), and
  #8056 removed the 2 class-plural rows with a bounded relative window
  (see ``_CLASS_PLURAL_COVERED``). Of what remains, the only permitted
  fires are the ADJUDICATED label-noise rows (370a8ff4 + gpt4_70e84552_abs);
  the test file is the authority for every count and for the floor.
  'ago' rejects EVEN with state morphology "what is the current status of X
  two weeks ago?" (reject-on-relative-offset);
* duration-state residual forms — "how long did I take … combined" (a
  summed span: #2886's aggregate lane owns it) and "how old was I when …"
  (an age: no state entry to resolve); plus every other duration/count
  morphology ("how long have I been working before …", "how long did it
  take") and the duration-morphology rows mis-filed under other classes;
* frequency/count — "how many times", "how many days did I spend", "how
  many … have passed since <one activity>" (single-anchor since);
* relative-date-lookup — "last Saturday", "on Valentine's day" (sibling
  #2655's CALENDAR-ANCHOR family — deliberately NOT promoted here); the
  three census rows of the class that name NO calendar anchor ("a week
  ago", "the first … in June", "the most in March and April") stay here
  too;
* nary-ordering — 3+ named options ("Mark and Sarah or Tom"), sibling
  #2653; and bare "latest" as an ORDERING INSTRUCTION ("from earliest to
  latest"). The census's nary-ordering class is RE-MEASURED to 2
  named-subject rows (#2653; the committed census stores 140-char question
  TRUNCATIONS, which is why the class was filed as 4): the 2 genuine N-ary
  rows (gpt4_f49edff3 / gpt4_18c2b244) now FIRE as ORDERING with 3 terms;
  gpt4_70e84552_abs is the binary `_abs` twin of an ordering row and
  already fired (adjudicated noise, pinned in test_assembly_pure.py);
  gpt4_d6585ce8 ("the concerts and musical events I attended") names NO
  subjects — it is a class-plural enumeration request, a DIFFERENT feature
  that #8056 now owns, and it fires as ``class-plural`` (not here);
* class-plural residual forms (#8056 — the class-plural rows that are NOT
  this shape, pinned with their reason rather than absorbed): a
  CALENDAR-MONTH window ("the sports events I watched in January" — month
  resolution + as-of year belong to the calendar-anchor surface); an
  OPEN-LEFT window ("airlines I flew with before today" — no bounded
  lookback); and NO window at all ("the six museums I visited from earliest
  to latest"), which is not a TEMPORAL enumeration;
* a 3+ option list whose members are NOT option phrases (a binary question
  whose A-side merely CONTAINS a comma) — see ``_nary_option_list``;
* pattern/recurring; offset-comparison ("how many months before my
  anniversary");
* the R13 COMPOUND pin — a relative offset inside a two-subject
  ordering/interval template keeps rejecting (there the offset is narrative
  anchor text, not the question's window);
* misfire (R16) — preference/advice with two named options + compare
  syntax ("compare X and Y which should I pick");
* single-activity / no resolvable subject.

Extraction (extract_subject_terms): template splits for the two-subject
shapes (A/B either side of the ordering "first,"-marker or the interval
"between…and…"/"since…when…" spans), single subject for current-state,
and the single PEELED state subject for duration-state (#2654).
Subjects are returned as RAW text spans (quotes/possessives intact —
"my cousin's wedding", "'The Hate U Give'") for the Task-3 resolver. For
``ago-relative`` and ``class-plural`` it returns [] (see the shape notes
above): neither selector is an Object, and the class-plural class is
carried by ``resolve_class_window`` instead.
"""
from __future__ import annotations

# ruff: noqa: I001  — the module is deliberately section-appended (each
# #2165 task adds its own imports mid-file); global import-sorting would
# restructure the append boundary on every task.
import re
from enum import StrEnum

from tortoise.calendar_anchors import (  # #2655 calendar-anchor resolution
    CalendarAnchorKind, find_anchor_spans, resolve_calendar_anchor)
from tortoise.entity_identity import (  # #3633 route-then-refuse
    display_holder_ids, record_non_folded)
from tortoise.fanout import PER_ENTITY_FANOUT_CAP, bounded_fanout

__all__ = ["AssemblyShape", "ClassWindow", "RelativeAnchor",
           "classify_question", "extract_subject_terms",
           "resolve_class_window", "resolve_relative_anchor"]

# public shape vocabulary (Task 6's fired branch keys on these)
class AssemblyShape(StrEnum):
    CURRENT_STATE = "current-state"
    ORDERING = "ordering"
    INTERVAL = "interval"
    # #2655: the census class name verbatim, so measurement attribution over
    # the fired decision reads the same string the census carries.
    RELATIVE_DATE = "relative-date-lookup"
    # #2654 — the state-entry anchor variant ("how long had I been X when Y")
    DURATION_STATE = "duration-state"
    #: #2652 — 'ago'-relative / implicit-time: the question carries a
    #: relative offset resolved against ``question_date`` into an as-of
    #: WINDOW (see ``resolve_relative_anchor``). ``extract_subject_terms``
    #: returns [] for this shape: the selector is the TIME, not an Object.
    AGO_RELATIVE = "ago-relative"
    #: #8056 — class-plural temporal ENUMERATION: the question asks for the
    #: order of a CLASS of events over a relative window and names NO
    #: subjects ("the order of the concerts and musical events I attended in
    #: the past two months"). ``extract_subject_terms`` returns [] for this
    #: shape (a class is not an Object): the selector is the WINDOW plus the
    #: class tokens, resolved by ``resolve_class_window``.
    CLASS_PLURAL = "class-plural"

# ── guard morphology (checked first — a guard hit means None, even if a
#    shape template would later match: R13 reject-on-relative-offset,
#    duration/count/N-ary are NEVER two-subject shapes) ──────────────────

# ── relative-time: TWO vocabularies, deliberately split (#2652) ────────
#
# (a) PROMOTED to the AGO_RELATIVE shape — the vocabulary whose offset is
#     resolvable against question_date:
#       * 'how many <unit> ago' (the CENSUS COUNT form — its offset IS the
#         answer, so the window is the open recent past, see
#         ``resolve_relative_anchor``)
#       * 'N days/weeks/months/years ago' + the article/fuzzy determiners
#         ('a week ago', 'a couple of days ago')
#       * 'most recently' (adverbial — the open as-of window)
# (b) STILL A GUARD — vocabulary this shape does NOT own:
#       * 'yesterday'/'tomorrow'/'last <weekday|week|month>'/holidays — the
#         CALENDAR-ANCHOR family of sibling #2655 (census
#         relative-date-lookup: Valentine's / last Saturday)
#       * bare 'latest' / 'the last one' — an ORDERING INSTRUCTION
#         ('from earliest to latest'), sibling #2653's discovery family
#       * the ATTRIBUTIVE 'most recent' — the SAME ordering instruction
#         spelled the other way ('from earliest to most recent'): it is
#         deliberately NOT promoted, so it falls through to the legacy
#         lane exactly as it did before #2652. Only the adverbial
#         'most recently' names a time.
#     Keeping (b) as a guard is what holds the R13 reject and the
#     precision floor while (a) becomes a shape.
_RE_AGO_OTHER = re.compile(
    r"\b(yesterday|tonight|tomorrow|"
    r"last\s+(?:night|week|weekend|month|year|morning|afternoon|evening|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|valentine)|"
    r"this\s+(?:morning|week|month)|in\s+\d+\s+(?:days?|weeks?|months?|"
    r"years?))\b",
    re.IGNORECASE)
# the promoted count form: 'how many <unit> ago' (no offset given)
_RE_AGO_COUNT = re.compile(
    r"\bhow\s+many\s+(?P<unit>days?|weeks?|months?|years?)\s+ago\b",
    re.IGNORECASE)
# the promoted explicit-offset form: '<count> <unit> ago'
_RE_AGO_OFFSET = re.compile(
    r"\b(?P<count>a|an|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|\d+|a\s+couple\s+of|couple\s+of|a\s+few|few|"
    r"several)\s+(?P<unit>days?|weeks?|months?|years?)\s+ago\b",
    re.IGNORECASE)
# the ADVERBIAL recency only. 'most recent' is NOT promoted: as an
# attributive it is the ordering bound in 'from earliest to most recent',
# the semantic twin of the guarded bare 'latest' (sibling #2653) — the
# 2 census recency rows both use 'most recently'.
_RE_MOST_RECENT = re.compile(r"\bmost\s+recently\b", re.IGNORECASE)
# the UN-PROMOTED residue: a bare 'ago' the promoted forms above do not
# consume (minutes/hours/centuries, 'a long time ago'). Checked only AFTER
# the promoted vocabulary routes, so it can never eat a promoted offset.
_RE_AGO_RESIDUAL = re.compile(r"\bago\b", re.IGNORECASE)
# ⛔ WHY THIS IS A TOKEN SCAN AND NOT A REGEX OF NUMBER SPELLINGS.
# Earlier revisions of this guard were a REGEX denylist of number SPELLINGS,
# and six review rounds each closed one spelling and found the next: a
# digit-only left bound left the SPELLED bounds open
# ('two to five weeks ago' → 5); making the left a number word left the ones
# carrying their OWN unit open ('from 2 weeks to 3 months ago' → 3); a
# connector list left 'til'/'upto'/'up until' open; a place-value list left
# 'point zero'/'point o' open; a cardinal left bound left fractions open ('one
# and a half to five weeks ago' → 5, '1½ to 2 weeks ago' → 2); and a dash was
# not in the connector class ('2 weeks - 5 weeks ago' → 5). Enumerating NUMBER
# SPELLINGS is open-ended — there is always another way to write a number — so
# the guard no longer tries. It asks the INVARIANT instead:
#
#     the count is trustworthy iff, scanning LEFT from it, we reach the end
#     of the number expression — i.e. we run out of CONNECTIVE tokens —
#     BEFORE we reach another NUMBER token
#
# Reaching a number token through nothing but connectives means the count is
# the RIGHT BOUND (or the trailing piece) of a longer number, and the template
# read only that piece. That is the whole rule.
#: tokens that can sit BETWEEN two parts of one number, or between the bounds
#: of a range, and so are part of neither bound: conjunctions, range
#: connectors, place-value markers, fraction words, the offset units, degree
#: adverbs that can qualify a bound ('and about five'). A plain SENTENCE word
#: ('things', 'store', 'Wednesday', 'May', 'iPhone') is deliberately absent:
#: that is what ends the scan, and so what makes a legitimately standalone
#: count ('I bought 2 things 3 days ago' → 3) promote.
#:
#: ⛔ THE LIMIT, STATED HONESTLY. THREE residuals, all bounded work rather
#: than an invariant over natural language:
#:   (a) a CONNECTIVE that is not in the list below ends the scan and lets the
#:       count promote, so a range written with it ('2 to <unlisted> 5 weeks
#:       ago') still promotes 5;
#:   (b) a NUMERAL spelling the vocabularies do not carry — a cardinal word
#:       outside `_NUMBER_WORDS`, or a unit spelling outside
#:       `_NUMERAL_UNIT_SUFFIXES` (the one set BOTH paths use, so this residual
#:       is the same for glued and spaced bounds) — likewise ends the scan, so
#:       a range's left bound written that way promotes the right bound;
#:   (c) a range joined by a SEPARATOR GLYPH outside `_RANGE_GLYPHS`: the bare
#:       unit before the count then reads as the end of a duration and the
#:       right bound is promoted (the unit-terminates rule's price: the glyph
#:       is not a linker, so it does not keep the unit a bridge).
#: Every direction is recorded here so the next reader does not over-trust the
#: guard — and so a later tightening does not "simplify" one of these away.
#:
#: ⛔ `_NUMERAL_UNIT_SUFFIXES` is ALSO the unit vocabulary the SPACED path
#: bridges, unioned into `_NUMBER_CONTINUATION_WORDS` above — do not re-list
#: the units there. Same set, both spellings: '2 hrs to 5 days ago' and
#: '2hrs to 5 days ago' must not disagree.
_NUMERAL_UNIT_SUFFIXES = frozenset({
    "d", "day", "days", "w", "wk", "wks", "week", "weeks",
    "mo", "mos", "month", "months", "y", "yr", "yrs", "year", "years",
    "h", "hr", "hrs", "hour", "hours", "m", "min", "mins", "minute",
    "minutes", "s", "sec", "secs", "second", "seconds",
})
#: the words that LINK two bounds of a range. A duration's unit is a range's
#: LEFT BOUND only when one of these sits between it and the count ('2 weeks
#: to 5 days ago', 'between 2 hrs and 5 days ago'); across a DEGREE ADVERB it
#: is still the end of a duration ('2 hours about 5 days ago'). Kept separate
#: from `_DEGREE_ADVERBS` for exactly that reason — they used to share a set,
#: so the scan saw a number to the left of an adverb and mistook it for a
#: second bound (a false reject against `origin/main`).
#: 'up' is here precisely so that EVERY 'up <connector>' spelling ('up to',
#: 'up till', 'up through', 'up until') is traversed without being listed.
_RANGE_CONNECTORS = frozenset({
    "and", "or", "to", "up", "upto", "unto", "till", "til", "until",
    "through", "thru",
    # ... and the rest of an inclusive-connector PHRASE, which is traversed
    # token by token: 'up to and including 5', 'to or beyond 5'
    "including", "inclusive", "inclusively", "onwards", "onward",
    "beyond", "before", "after",
})
#: degree adverbs that can QUALIFY a bound without being one. They stay
#: traversable — a range's right bound may be qualified ('between 2 and about
#: 5 weeks ago', '2 to roughly 5 weeks ago') — but they do NOT link two
#: bounds, so they never turn a preceding unit into a range's left bound. A
#: token in this set therefore leaves the scan's `bridged` flag unset.
_DEGREE_ADVERBS = frozenset({
    "about", "around", "roughly", "nearly", "approximately", "almost",
    "exactly", "precisely", "just", "only", "merely", "some", "another",
    "less", "more", "than", "over", "under",
})
#: tokens that sit INSIDE a number expression but link nothing: place-value /
#: decimal markers, the quantifier link word, and the fraction words (the
#: ZEROS are cardinals, not markers). Traversable, but not range linkers.
_NUMBER_INTERNAL_WORDS = frozenset({
    "point", "dot", "oh", "o",
    "of",
    "half", "halves", "quarter", "quarters", "third", "thirds",
})
#: every token the scan crosses while looking for a number behind the count.
#: The three groups are UNIONED IN deliberately; they differ only in whether
#: crossing them makes a preceding unit a range's left bound (`_RANGE_CONNECTORS`)
#: or not (the adverbs and the number-internal markers).
_NUMBER_CONTINUATION_WORDS = (_RANGE_CONNECTORS | _DEGREE_ADVERBS
                              | _NUMBER_INTERNAL_WORDS
                              | _NUMERAL_UNIT_SUFFIXES)
# ⛔ The unit spellings are NOT listed above — they are UNIONED IN, because a
# RANGE's left bound may carry its own unit ('from 2 weeks – 3 months ago',
# 'between 2 hrs and 5 days ago') and the connector between the two bounds is
# then just a separator, so the unit is what the scan must bridge. Deriving
# them from `_NUMERAL_UNIT_SUFFIXES` is deliberate: when the two vocabularies
# were written out separately the glued path learned 'hrs'/'hours' and the
# spaced path did not, so '2hrs to 5 days ago' rejected while '2 hrs to 5 days
# ago' promoted 5. One source, so they cannot drift apart again.
#: the NUMBER vocabulary: a token that IS a number expression's bound. The
#: cardinals and the quantifiers are here (NOT in the connective set) because
#: reaching one is exactly what proves the count is a right bound — 'twenty
#: two weeks ago' reaches 'twenty' and is a fragment; 'up to 3 weeks ago'
#: reaches only connectives ('to', 'up') and is a whole number. Includes the
#: TENS/SCALE words `_RE_AGO_OFFSET` itself does not read (it stops at
#: 'twelve') — rejecting those counts is the whole reason the guard exists.
#: The SCALE words are here too, in both numbers: a bare one IS the bound
#: ('hundred 5 weeks ago', 'dozens of 5 weeks ago'), so classifying them as
#: connectives let the scan run off the start and promote a count
#: `origin/main` refused.
_NUMBER_WORDS = frozenset({
    "a", "an", "couple", "few", "several",
    "zero", "nought", "naught",
    "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen",
    "fifteen", "sixteen", "seventeen", "eighteen", "nineteen",
    "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
    "eighty", "ninety",
    "hundred", "hundreds", "thousand", "thousands", "million",
    "millions", "billion", "billions", "dozen", "dozens", "score",
    "scores",
})
#: a numeral token may carry a fraction glyph the tokenizer does not split
#: off ('1½', '3¾') and still be a number; but a token whose digits are joined
#: to LETTERS is a number only when those letters are its UNIT ('2wks', '24h',
#: '2mo') — otherwise it is a time, a quantity label or an ordinal ('3pm',
#: '4k', '5G', '3rd') and a count after it is a whole number.
#:
#: The distinction matters in ONE direction more than the other. Treating a
#: glued numeral as NOT a number deletes a range's left bound from the scan,
#: so '2wks to 5 days ago' would promote the RIGHT bound — the wrong-window
#: failure the guard exists to prevent. Treating it as a number costs the
#: promotion on 'at 3pm 5 days ago', which falls back to the legacy lane and
#: never anchors wrongly. Where the two collide, fail safe.
#:
#: ⛔ `_NUMERAL_UNIT_SUFFIXES` is ALSO the unit vocabulary the SPACED path
#: bridges, unioned into `_NUMBER_CONTINUATION_WORDS` above — do not re-list
#: the units there. Same set, both spellings: '2 hrs to 5 days ago' and
#: '2hrs to 5 days ago' must not disagree.
_FRACTION_GLYPHS = "½⅓⅔¼¾⅕⅖⅗⅘⅙⅚⅛⅜⅝⅞"
#: the range/alternative GLYPHS. A bare duration before the count TERMINATES it
#: ('2 hours 5 days ago'), but a glyph between them is itself a range connector
#: ('from 2 weeks – 3 months ago', '2 weeks / 5 days ago'), so the unit is a
#: bridge there after all. Sentence punctuation ('.', ',', ';') is deliberately
#: NOT here — '2 weeks. 5 days ago' is two sentences, not a range.
_RANGE_GLYPHS = "-\u2010\u2011\u2012\u2013\u2014\u2015\u2212/\\|~&+"


def _is_numeric(ch: str) -> bool:
    return ch.isdigit() or ch in _FRACTION_GLYPHS


def _is_number_token(token: str) -> bool:
    """True when `token` IS a bound of a number expression."""
    if not token:
        return False
    if token.lower() in _NUMBER_WORDS:
        return True
    if all(_is_numeric(ch) for ch in token):
        return True
    split = 0
    while split < len(token) and _is_numeric(token[split]):
        split += 1
    if split == 0:
        return False
    return token[split:].lower() in _NUMERAL_UNIT_SUFFIXES


def _continues_number(token: str) -> bool:
    """True when `token` can be sitting INSIDE the number expression."""
    return token.lower() in _NUMBER_CONTINUATION_WORDS


def _is_unit_length_token(token: str) -> bool:
    """True when `token` is the TAIL of a duration: a bare unit ('hrs'), or a
    numeral glued to one ('2hrs'). Both spellings must behave the same way in
    the scan — '2hrs 5 days ago' and '2 hrs 5 days ago' are the same sentence.
    """
    low = token.lower()
    if low in _NUMERAL_UNIT_SUFFIXES:
        return True
    split = 0
    while split < len(token) and _is_numeric(token[split]):
        split += 1
    return 0 < split < len(token) and token[split:].lower() in (
        _NUMERAL_UNIT_SUFFIXES)


def _count_is_fragment(text_before: str) -> bool:
    """True when the count is the RIGHT BOUND (or trailing piece) of a
    longer number, so promoting it would answer from a confidently wrong
    window.

    Scans LEFT from the count, token by token, over separator runs. It stops
    — and reports a WHOLE number — at the first token that is neither a
    number nor a connective; it reports a FRAGMENT at the first token that
    IS a number. So the scan crosses any run of connectives, which is what
    makes 'up to 3 weeks ago' a whole number (only 'to'/'up' on the left)
    while '2 to 5 weeks ago' and 'one and a half to five weeks ago' are
    fragments (a number waits behind the connectives).

    A duration's unit ('hours', '2hrs') terminates the scan — it starts a
    NEW number — UNLESS a RANGE LINKER (a `_RANGE_CONNECTORS` word or a
    `_RANGE_GLYPHS` glyph) sits between the unit and the count. So '2 hours
    5 days ago' is two durations (whole), '2 hrs to 5 days ago' is a range
    (fragment), and '2 hours about 5 days ago' is two durations again: the
    adverb MODIFIES the count, it does not link a second bound, so it must
    not let the scan reach the earlier duration's number. Tracking the
    linker (`bridged`) across the whole scan — not just the first token — is
    what keeps the adverb from re-opening the false reject the first-token
    rule closed.

    Linear in len(text_before) — a plain scan, no regex, so it cannot
    backtrack (the regex this replaced was an availability defect: an
    ambiguous nested quantifier that took 3 s at N=24 and never returned
    beyond, in an ask path with no timeout).
    """
    i = len(text_before)
    bridged = False
    while True:
        j = i
        while j > 0 and not text_before[j - 1].isalnum():
            j -= 1
        k = j
        while k > 0 and text_before[k - 1].isalnum():
            k -= 1
        if k == j:            # ran off the start: nothing to the left
            return False
        token = text_before[k:j]
        if any(ch in _RANGE_GLYPHS for ch in text_before[j:i]):
            # a range GLYPH between the count and this token links them even
            # though no connector WORD is present ('2 weeks / 5 days ago',
            # 'from 2 weeks – 3 months ago'): record it so a duration's unit
            # stays a bridge there.
            bridged = True
        if _is_unit_length_token(token) and not bridged:
            # A DURATION ENDS HERE. '2 hours 5 days ago' is two durations, not
            # a range: the count starts a NEW number, and the unit in front of
            # it belongs to the number before that. A unit is a bridge only
            # when a RANGE LINKER sits between it and the count ('2 hrs to 5
            # days ago', 'between 2 hrs and 5 days ago', '2 weeks / 5 days
            # ago') — across an adverb or nothing at all it terminates the
            # scan instead. Without this the union above made the guard reject
            # '2 hours 5 days ago', which promotes on `origin/main`; keying it
            # on the LINKER rather than on the FIRST token keeps the adverb
            # spellings ('2 hours about 5 days ago') from re-opening that
            # reject.
            return False
        if _is_number_token(token):
            return True
        if not _continues_number(token):
            return False
        if token.lower() in _RANGE_CONNECTORS:
            bridged = True
        i = k

# duration-state semantics that are STILL never a two-subject shape: "how
# long" with a state verb ("how long have I been working before …"), "how
# many <unit> did I spend / had I been", "how long did (I|the narrator)
# take". NOT the bare "how long / how much time passed between A and B" (a
# genuine two-anchor interval). #2654: the "… been <state> when <event>"
# form is NO LONGER covered by this guard — it is the duration-state shape
# (``_RE_DURATION_STATE``), matched BEFORE this guard in classify_question.
_RE_DURATION = re.compile(
    r"\bhow\s+long\s+(?:had|have|did|was|were)\b|"
    r"\bhow\s+much\s+time\s+did\s+(?:i|the\s+narrator)\b|"
    r"\bhow\s+many\s+(?:days?|weeks?|months?|years?)\s+did\s+"
    r"(?:i|the\s+narrator)\s+(?:spend|take)\b|"
    r"\bhow\s+many\s+(?:days?|weeks?|months?|years?)\s+had\s+"
    r"(?:i|the\s+narrator)\s+been\b",
    re.IGNORECASE)
# frequency / count: "how many times/events/units", "how many <unit> did I
# spend", single-anchor "how many <unit> have passed since <one activity>",
# offset-count "how many <entity> before <X>" / "how many <unit> before my
# <X>" — all tied to a how-many count head (never a bare "before" search,
# which would kill legit interval anchors like "the day before Christmas").
_RE_FREQUENCY = re.compile(
    r"\bhow\s+many\s+times\b|\bhow\s+often\b|"
    r"\bhow\s+many\s+(?:days?|weeks?|months?|years?|times?|events?|charity\s+"
    r"events?)\s+(?:did\s+(?:i|the\s+narrator)\s+spend\s+|"
    r"have\s+passed\s+since\s+|did\s+it\s+take\s+|"
    r"(?:weeks?|months?|years?)\s+before\s+)",
    re.IGNORECASE)
# recency / 'latest' / the ATTRIBUTIVE 'most recent' — the ORDERING-
# INSTRUCTION residue ('latest' as in 'from earliest to latest'; 'most
# recent' as in 'from earliest to most recent' or 'my most recent phone')
# stays a guard, exactly as on main. Only the ADVERBIAL 'most recently' is
# promoted to AGO_RELATIVE above. `\W+` (not `\s+`) is what makes the guard
# spelling-independent: 'most-recent' / 'most‑recent' (U+2010) are the same
# instruction — the class the sibling compound guard was hardened for — and
# `\W+` still cannot match the adverbial, because it must be followed by
# `recent` and then `\b`, and 'recently' has no boundary after 'recent'.
# The guard is checked BEFORE the shape templates, so an attributive
# recency question can never fall through and fire
# ORDERING/CURRENT_STATE/INTERVAL.
_RE_RECENCY = re.compile(
    r"\bmost\W+recent\b|\blatest\b|\bthe\s+last\s+one\b", re.IGNORECASE)
# advice / preference / recommendation (R16 misfire): "which SHOULD I do
# first, A or B" carries ordering morphology but is preference, not fact —
# firing would REPLACE the working pool with a shape block. Modal + advice
# verbs reject BEFORE the ordering arm.
#
# #2653: the weak verbs (pick/choose/decide) need an ADVICE FRAME. Bare
# "pick" is an ordinary narrative verb — the census's own N-ary ordering row
# gpt4_f49edff3 says "the day I helped my cousin PICK OUT STUFF for her baby
# shower", which a bare-verb guard rejected as preference and so silently
# excluded a measured row from the shape it belongs to. The frame forms below
# are the ones the R16 family actually uses ("which one should I pick",
# "which to choose"); the strong signals are unchanged.
_RE_ADVICE = re.compile(
    r"\b(?:should|ought|shall|recommend(?:ed|ation)?|suggest(?:s|ed|ing)?|"
    r"prefer(?:red|able|ence)?|advise|better|best\s+(?:to|for)\b|"
    r"prioritize)\b|"
    r"\b(?:which|what)\s+(?:one\s+)?(?:to|should)\b|"
    r"\b(?:shall|would|could)\s+(?:i|we|you|they|he|she|it)\b|"
    r"\bto\s+(?:pick|choose|decide)\b",
    re.IGNORECASE)
# N-ary: an enumerated list of 3+ options. Two shapes of escape: repeated
# " or " ("A or B or C") and a comma list ("first, A, B or C" / "A, B, and
# C"). #2653 DECOMPOSES both (``_nary_terms``); the guard survives only for
# the NON-ordering shapes — an or-chain inside an interval anchor is not an
# option list (see classify_question's arm order).
#
# ⛔ The whitespace runs are SINGLE `\s`, not `\s+`. With `\s+` on both
# sides of the lazy `.+?` the quantifiers overlap — '\s+' and '.' both match
# whitespace — so on a question with ONE 'or' and long space runs the engine
# retries every (start, split, end) triple before failing: measured 307 ms at
# len 204 and ~4.8 s at len 404 for 'a' + ' '*200 + 'or' + ' '*200 + 'b', i.e.
# O(n^3.8). `_RE_OR_CHAIN.search` runs unconditionally in `classify_question`
# on the ask path and `run_ask_lane` has no timeout, so a question inside
# MAX_ASK_QUESTION_CHARS could wedge a lane — the same availability defect the
# ago guard above was rewritten to remove. A single `\s` absorbs the same
# whitespace (the lazy `.+?` covers any extra) and removes the ambiguity.
_RE_OR_CHAIN = re.compile(r"\sor\s.+?\sor\s", re.IGNORECASE | re.DOTALL)

# N-ary list with an EXPLICIT list-introducing colon after an ordering head —
# the measured corpus form ("Which three events happened in the order from
# first to last: A, B, and C" / "What is the order of the three events:
# 'A', 'B', and 'C'"). The colon is the strong signal: the two head forms
# carry no " or " at all, so _RE_ORDERING cannot see them.
# The "earliest to latest" head form is deliberately NOT an alternative here:
# a question containing it always matches ``_RE_RECENCY`` ("\blatest\b") and
# is rejected by the R13 guard before this pattern is ever tried, so the
# alternative was dead code that contradicted that guard.
_RE_ORDERING_LIST = re.compile(
    r"^\s*(?:which|who|what)\b[^?:]*?"
    r"(?:\border\b|\bsequence\b|\bfirst\s+to\s+last\b)"
    r"[^?:]*:\s*[^?\s][^?]*\??\s*$",
    re.IGNORECASE | re.DOTALL)

# the ordering-marker words that introduce an option list when the question
# carries no colon ("which did I get first, A, B or C?")
_RE_ORDER_MARKER = re.compile(
    r"\b(?:first|order|sequence)\b", re.IGNORECASE)

# the TIME/SCOPE clause a binary question may carry between the ordering
# marker and the option list ("first in February, the bike or the car") — the
# comma there is a clause boundary, NOT a second option (the pre-#2653
# classifier's own N-ary comma check; now shared with extraction so the two
# cannot disagree).
_RE_TIME_CLAUSE = re.compile(
    r"^(?:in|on|at|during|for|after|since|before|around)\s+"
    r"(?:[a-z]+\s+)*?(?:january|february|march|april|may|june|july|"
    r"august|september|october|november|december|monday|tuesday|"
    r"wednesday|thursday|friday|saturday|sunday|"
    r"20\d{2}|the\s+\w+)\s*,\s*",
    re.IGNORECASE)

# a member of an option list opens with a determiner/possessive/numeral/quote.
# Used ONLY to keep a NON-colon 3-way split honest: "the couch, which I
# loved, or the chair" is a BINARY question with a parenthetical comma, and
# splitting it into three would fail the R1 all-terms gate where the binary
# path used to fire. The colon form needs no such check (an explicit list
# introducer IS the signal).
_RE_OPTION_LEAD = re.compile(
    r"^(?:['\"]|(?:the|a|an|my|our|your|their|his|her|its|this|that|"
    r"these|those|one|two|three|four|five|six|seven|eight|nine|ten)\b)",
    re.IGNORECASE)

# ── shape templates (ordered high-precision) ─────────────────────────────

# ordering: "Which <X> (did I …|…) first[,:] A or B?" / "Who … first, A or B?"
_RE_ORDERING = re.compile(
    r"^\s*(?:which|who|what)\b[^?]*?\bfirst\b[^?]*?\b(?P<a>.+?)\s+or\s+"
    r"(?P<b>[^?]+?)\??\s*$",
    re.IGNORECASE | re.DOTALL)

# ── #8056 class-plural temporal ENUMERATION ─────────────────────────────
# "What is the order of the concerts and musical events I attended in the
# past two months, starting from the earliest?" — an ORDERING request over a
# CLASS of events, scoped by a RELATIVE WINDOW, naming NO subjects. Every
# other shape needs resolvable Object anchors, so this family returned None
# at HEAD (measured: 0/6 of the class-plural census rows fired).
#
# The template requires ALL THREE of: the ordering HEAD ("what/which is the
# order of"), the first-person RELATIVE CLAUSE ("I attended"), and an
# explicit relative WINDOW ("in/during/over/within the past|last …"). The
# class span between the head and the clause is then the class to select.
#
# ⛔ The count is OPTIONAL and a PLURAL unit with no count is NOT admitted:
# the census carries BOTH spellings of the window ("during the past month" —
# no count, with the singular unit — and "in the past two months"), while
# "the past months" is ambiguous (how many?) and no measured row carries it,
# so it abstains — the module's usual rule for an unmeasured widening.
#
# ⛔ The ordering INSTRUCTION is deliberately NOT part of the template
# ("starting from the earliest" / "from earliest to latest"): it is the
# question's own request and adds nothing to the head, AND the R13 `latest`
# guard would otherwise reject the second spelling while admitting the
# first. That guard is lifted for THIS head alone — see the OVERRIDES note on
# the class-plural branch of ``classify_question``.
# ⛔ The whitespace run before the relative-clause pronoun is a SINGLE `\s`,
# not `\s+`, for the same reason `_RE_OR_CHAIN`'s were (see below): `\s+`
# and the lazy `.+?` BOTH match whitespace, so on a question with long space
# runs the engine retries every (class-end, run-split) pair before failing —
# measured 53 ms at len 1853 and 216 ms at len 3453 for
# 'the order of the ' + 'a'*100 + ' '*n + 'i'*100 + ' '*n + 'took …'. A
# single `\s` absorbs one separator (the lazy `.+?` covers every extra) and
# removes the ambiguity; the tail is GREEDY (`[^?]*`, not `[^?]*?`) so
# `\s*$` cannot retry over a trailing space run either. `MAX_ASK_QUESTION_CHARS`
# bounds the input at 2000 and `run_ask_lane` has no timeout, so the fix is
# availability, not performance — the same defect class as #8097.
_CLASS_COUNT_RX = (
    r"(?:a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|"
    r"twelve|\d+)")
#: the relative-clause SUBJECT. Shared by the outer clause and the class-span
#: boundary below, so the two can never drift apart. The `\b` guards are
#: LOAD-BEARING for the boundary: without them `he` matches inside "the" and
#: `we` inside "wedding", so the tempered class span stops mid-word and the
#: whole template fails.
_CLASS_REL_PRONOUN = r"\b(?:i|we|you|they|he|she|the\s+narrator)\b"
#: possessive determiners. NOT folded into `_CLASS_REL_PRONOUN`: that pattern is
#: the relative-clause SUBJECT, and a possessive is never one. But they DO end
#: the class span -- an interposed possessive noun phrase ("the concerts MY
#: COUSIN and I attended") otherwise lands INSIDE the class vocabulary, and
#: `_class_owns_row`'s any-token overlap then treats it as an alternative
#: selector (#8779 F3: a cousin row was selected for a CONCERT question -- a
#: FALSE INCLUDE, the direction this shape must never produce).
_CLASS_POSSESSIVE = r"\b(?:my|our|his|her|their|its)\b"
_CLASS_SPAN_STOP = (r"\b(?:i|we|you|they|he|she|the\s+narrator"
                   r"|my|our|his|her|their|its)\b")
_RE_CLASS_PLURAL = re.compile(
    r"^\s*(?:what|which)\s+(?:is|was)\s+the\s+order\s+of\s+"
    # ⛔ the class span may NOT cross a relative-clause boundary: this is a
    # TEMPERED lazy run — it stops at the FIRST `<pronoun> <verb>` — so a
    # repeated relative clause ("the concerts I attended and the plays I
    # saw") can no longer be swallowed whole (#8779 P2). Before the bound the
    # lazy `.+?` reached the LAST pronoun and absorbed the first clause's VERB
    # into the class vocabulary; `attended` then matched almost every
    # narrative row and the class filter degraded to a near-no-op — a FALSE
    # INCLUDE, the dangerous direction. The repeated-clause form now
    # ABSTAINS (no match -> the caller's legacy fall-through), which is the
    # plan's recorded recall-loss posture, never a false include.
    r"(?P<cls>(?:(?!" + _CLASS_SPAN_STOP + r"\s+\w).)+?)\s"
    # an interposed possessive noun phrase sitting between the class and its
    # relative clause ("... concerts MY COUSIN and I attended ..."). It is
    # consumed HERE so the span can stop early without the template failing.
    # Consequence, accepted: a class phrase that ITSELF begins with a
    # possessive now abstains -- a recall loss, which is the posture this shape
    # already records for the repeated-clause form, and never a false include.
    r"(?:(?:" + _CLASS_POSSESSIVE + r")\s+\w+\s+(?:and\s+)?)?"
    r"(?:(?:that|which)\s+)?"
    r"(?:" + _CLASS_REL_PRONOUN + r")\s+\w+(?:\s+\w+)?\s+"
    # the window is a NAMED group so the classifier can blank exactly its own
    # text before `_RE_AGO_OTHER` (#8779 E4/F2): the count-less `last <unit>`
    # spelling IS this shape's window, not an extra calendar offset, and
    # without the blanking that guard made the singular-unit arm dead for
    # week/month/year.
    r"(?P<window>(?:in|during|over|within)\s+the\s+(?:past|last)\s+"
    r"(?:(?P<count>" + _CLASS_COUNT_RX + r")\s+"
    r"(?P<unit>days?|weeks?|months?|years?)"
    r"|(?P<unit_sg>day|week|month|year)))(?![a-z])"
    # The ordering INSTRUCTION is CAPTURED here, but the direction reader
    # scans the WHOLE question (#8779 E1: the instruction may sit BEFORE the
    # relative clause, inside the class span), so this group records the
    # tail's instruction rather than being the reader's only input.
    r"(?P<order>[^?]*)\??\s*$",
    re.IGNORECASE | re.DOTALL)
# interval: "how many <unit> (had|have) passed between A and B"
# interval "between": "how many <unit> (had|have) passed between A and B" and
# the bare "how many <unit> between A and B" form. Greedy-A/lazy-B splits on
# the LAST " and " so subjects containing " and " (e.g. "Mark and Sarah") stay
# intact on the A side.
_RE_INTERVAL_BETWEEN = re.compile(
    r"^\s*how\s+(?:many\s+(?:days?|weeks?|months?|years?)|long|"
    r"much\s+time)\s+(?:(?:had|have|were\s+there|went|elapsed)\s+)?"
    r"(?:passed\s+|elapsed\s+)?between\s+(?P<a>.+)\s+and\s+"
    r"(?P<b>[^?]+?)\??\s*$",
    re.IGNORECASE | re.DOTALL)
# leading "Between A and B, how many <unit> passed?"
_RE_INTERVAL_BETWEEN_LEAD = re.compile(
    r"^\s*between\s+(?P<a>.+?)\s+and\s+(?P<b>[^?]+?)\s*,\s*how\s+many\s+"
    r"(?:days?|weeks?|months?|years?)\s+(?:(?:had|have)\s+)?passed\??\s*$",
    re.IGNORECASE | re.DOTALL)
# interval since-until: "how many <unit> had passed since X when Y"
_RE_INTERVAL_SINCE = re.compile(
    r"^\s*how\s+many\s+(?:days?|weeks?|months?|years?)\s+had\s+passed\s+"
    r"since\s+(?P<a>.+?)\s+when\s+(?P<b>[^?]+?)\??\s*$",
    re.IGNORECASE | re.DOTALL)
# current-state: "what is the current status/state of X"
_RE_CURRENT = re.compile(
    r"^\s*(?:what|which|how)\b[^?]*?\bcurrent\s+(?:status|state)\s+of\s+"
    r"(?P<a>[^?]+?)\??\s*$",
    re.IGNORECASE | re.DOTALL)
# current-state "is X still …" (ask-lane state question)
_RE_CURRENT_STILL = re.compile(
    r"^\s*is\s+(?P<a>.+?)\s+still\s+(?:live|active|around|in\s+use|mine)"
    r"\??\s*$", re.IGNORECASE | re.DOTALL)
# #2655 date-lookup surface: the question asks FOR something about the anchored
# occasion (a wh-lookup), rather than merely mentioning the day.
_RE_WH_LOOKUP = re.compile(r"\b(?:what|which|who|whom|whose|where|when)\b",
                           re.IGNORECASE)


def _strip_anchor_spans(question: str, spans) -> str:
    """``question`` with each matched anchor span blanked (R2655).

    The residual is what the R13 relative-offset guard and the
    duration/frequency/recency/advice guards are evaluated against, so a
    NON-anchor relative offset co-present with an anchor ("… two months ago or
    … last Saturday") still rejects the question — the anchor must not act as a
    shield for another shape's morphology.
    """
    out = question
    for span in sorted(spans, key=lambda s: s.start, reverse=True):
        out = out[:span.start] + " " + out[span.end:]
    return out


def _date_lookup_surface(question: str, residual: str, spans) -> bool:
    """#2655: is this calendar-anchor question a DATE LOOKUP?

    High precision by construction, and deliberately NARROW: the caller falls
    through to the unchanged classifier body for every rejection, so a
    question whose anchors belong to another shape keeps that shape, and every
    guard-rejected phrasing stays rejected.

    * ONE distinct anchor only — two anchors ("between Christmas and New
      Year's day …") is an interval, not a day lookup;
    * a wh-lookup surface must be present;
    * no competing shape template matches the question (ordering / interval /
      current-state own it);
    * no competing guard matches the anchor-stripped RESIDUAL (a non-anchor
      relative offset, a duration/frequency/recency/advice surface, or an
      N-ary list).
    """
    if len({(s.kind, s.label) for s in spans}) != 1:
        return False
    if not _RE_WH_LOOKUP.search(question):
        return False
    if (_RE_ORDERING.match(question) or _RE_CURRENT.match(question)
            or _RE_CURRENT_STILL.match(question)
            or _RE_INTERVAL_SINCE.match(question)
            or _RE_INTERVAL_BETWEEN.match(question)
            or _RE_INTERVAL_BETWEEN_LEAD.match(question)
            or _RE_DURATION_STATE.match(question)
            # #8056: the class-plural head is a competing shape. A
            # class-plural question that also carries ONE calendar anchor
            # ("… in the past two months on Christmas?") is not a DAY
            # lookup: the anchor is adjunct and the shape's own window is
            # the relative one — the classifier body below decides it.
            # ⛔ and the body ABSTAINS on that question (#8779 F17), so it
            # returns NO shape where `origin/main` returned
            # `relative-date-lookup`: the day-scoped answer is LOST for the
            # named-holiday forms and the caller falls through to legacy.
            # A ROUTING loss, never a wrong answer — recorded in the plan's
            # §6 rather than hidden here. Handing the question back to this
            # surface once the body declines is the follow-up fix.
            or _RE_CLASS_PLURAL.match(question)):
        return False
    # #2652 split the pre-merge `_RE_AGO` guard into THREE parts -- the
    # CALENDAR-anchor residue (`_RE_AGO_OTHER`), the PROMOTED relative
    # vocabulary (`_relative_match`), and the un-promoted bare-`ago` residue
    # (`_RE_AGO_RESIDUAL`) -- so a non-anchor relative offset in the residual
    # is whichever of the three owns it and ALL must reject here. (The union
    # is exactly the old guard: `_RE_AGO` ≡ `_RE_AGO_OTHER ∪ \bago\b`, and
    # `_relative_match` is a subset of the bare-`ago` residue -- it is named
    # explicitly because it is the vocabulary the promoted forms route on.)
    return not (
        _RE_AGO_OTHER.search(residual)
        or _relative_match(residual) is not None
        or _RE_AGO_RESIDUAL.search(residual)
        or _RE_DURATION.search(residual)
        or _RE_FREQUENCY.search(residual) or _RE_RECENCY.search(residual)
        or _RE_ADVICE.search(residual) or _RE_OR_CHAIN.search(residual))
# duration-state (#2654): "how long had I been <state> when <event>?" — the
# past-elapsed-state form that needs a state's ENTRY date and the question's
# as-of anchor (never an interval: the two anchors are the subject's state
# entry and the as-of date, not two named events). Deliberately NARROW —
# high precision is the point (R13/R15/R16):
#   * "how long (had|have|has) <pronoun> been" — the past-state auxiliary
#     frame. "how many <unit> had I been X" (the unit-prefixed twin) is NOT
#     admitted in v1: no census row carries that form, and widening on an
#     unmeasured surface is what the precision floor exists to prevent.
#   * "when <event>" is REQUIRED: it is what separates this shape from the
#     in-scope, deliberately-rejected "how long have I been working BEFORE
#     …" family (a single-anchor duration with no reference event).
_RE_DURATION_STATE = re.compile(
    r"^\s*how\s+long\s+(?:had|have|has)\s+"
    r"(?:i|you|we|they|he|she|the\s+narrator)\s+been\s+"
    r"(?P<state>.+?)\s+when\s+(?P<event>[^?]+?)\??\s*$",
    re.IGNORECASE | re.DOTALL)


def _relative_match(question: str):
    """Text-only relative-time match for #2652 (no question_date needed).

    Returns ``(kind, match)`` where kind is ``"ago-count" | "ago-offset"
    | "recency"`` and ``match`` is the ``re.Match`` carrying the span, else
    None. Text-only on purpose:
    ``classify_question`` must stay a pure function of the question, and
    the DATE resolution (which needs question_date) lives in
    :func:`resolve_relative_anchor`.

    Ordered most-specific first — the count form ('how many <unit> ago') is
    its own template and cannot match the offset form's determiner
    alternation ('many' is not a determiner), so the order is about which
    template owns the span, not about disambiguation — then the offset
    form, then recency last.
    """
    if not question:
        return None
    for kind, rx in (("ago-count", _RE_AGO_COUNT),
                     ("ago-offset", _RE_AGO_OFFSET),
                     ("recency", _RE_MOST_RECENT)):
        m = rx.search(question)
        if m is None:
            continue
        if kind == "ago-offset" and _count_is_fragment(
                question[:m.start("count")]):
            # a count that is the TRAILING PIECE of a longer number — a
            # compound number word ('twenty two weeks ago'), a multi-part
            # numeral ('2.5 weeks ago'), a range ('2 to 5 weeks ago'), a
            # spelled place-value tail ('two point oh five weeks ago'). The
            # template matched only the last piece, which would anchor the
            # window ~20 units wrong. Not a promoted form: fall through
            # (the bare-'ago' residue then rejects it).
            continue
        return kind, m
    return None


def _relative_guarded(question: str):
    """The R13 boundary for the promoted vocabulary (called by
    ``classify_question`` and ``resolve_relative_anchor``).

    A relative offset inside a TWO-SUBJECT template (ordering / interval)
    stays REJECTED: there the offset is narrative anchor text ('since I
    bought the couch two weeks ago when I sold it'), not the question's
    window — promoting it would hijack an interval question into a window
    walk. N-ary lists are rejected for the same reason. Returns the
    ``(kind, match)`` pair when the question routes to AGO_RELATIVE, else
    None.
    """
    rel = _relative_match(question)
    if rel is None:
        return None
    q = question
    if _RE_OR_CHAIN.search(q):
        return None
    if (_RE_ORDERING.match(q) or _RE_INTERVAL_SINCE.match(q)
            or _RE_INTERVAL_BETWEEN.match(q)
            or _RE_INTERVAL_BETWEEN_LEAD.match(q)):
        return None
    return rel


def classify_question(question: str) -> AssemblyShape | None:
    """Deterministic pre-retrieval shape router.

    Returns the AssemblyShape for questions the assembled branch may answer,
    else None (the caller falls through to the legacy lane byte-identically).
    Guards run first — a 'last <calendar>'/frequency/N-ary/recency/advice
    hit returns None even when a later shape template would textually match
    (R13/R16). The duration-state template is checked BEFORE the blanket
    duration guard, which keeps rejecting every other duration/count
    morphology (#2654).

    #2652: the explicit relative-offset + recency vocabulary is promoted to
    ``AGO_RELATIVE`` BEFORE the two-subject templates, except when the
    question itself matches one of those templates (the R13 compound pin —
    see ``_relative_guarded``), and except when it is a duration-state
    question, where the offset is not the question's window (#2654's
    reject-on-relative-offset is preserved — see the template branch).
    """
    if not question or not question.strip():
        return None
    q = question.strip()
    # #2655: a calendar-anchored date lookup is decided FIRST, and — when an
    # anchor surface is present at all — this function NEVER falls through to
    # the other templates without the R13 re-check below. Both halves are
    # deliberate:
    #   * the anchor branch must precede the R13 guards, because the
    #     CALENDAR-anchor vocabulary (`last <weekday>` / `last weekend` /
    #     `valentine`) is exactly the set #2652 kept in `_RE_AGO_OTHER`, and
    #     that guard would otherwise swallow the shape;
    #   * a question that carries an anchor AND another shape's morphology must
    #     return None ("… two months ago or … last Saturday?" is NOT a date
    #     lookup, and the ordering template would otherwise fire on it and
    #     REGRESS a pinned negative).
    spans = find_anchor_spans(q)
    if spans:
        if _date_lookup_surface(q, _strip_anchor_spans(q, spans), spans):
            return AssemblyShape.RELATIVE_DATE
        # NOT a day lookup. The anchor branch may only change a decision the
        # R13 guard made ON THE ANCHOR SURFACE itself: an anchor-bearing
        # question containing `last <weekday>`/`last weekend`/`valentine`
        # returned None pre-#2655 because that CALENDAR vocabulary was
        # guarded, so a hit here MUST stay None — the anchor may never act as
        # a shield that lets another shape (or an ordering template sitting
        # next to a non-anchor `ago` offset) fire on a question it would have
        # been rejected for. Anything else (a state probe with a HOLIDAY
        # adjunct, two holidays inside a `between … and …` interval) falls
        # through to the unchanged body below and keeps the shape it already
        # had.
        if _RE_AGO_OTHER.search(q):
            return None
    # #8056 — class-plural temporal ENUMERATION (a class of events over a
    # relative window, no named subjects). Checked BEFORE the blanket R13
    # guard block below, because that block's RECENCY arm (`_RE_RECENCY`,
    # "bare `latest` is an ORDERING INSTRUCTION, not a time") rejects the
    # `from earliest to latest` spelling of THIS shape's own ordering
    # request while admitting the `starting from the earliest` spelling of
    # the same question — the feature would serve one arbitrary spelling of
    # its own census class.
    #
    # OVERRIDES: the #2165 R13 recency guard, for the class-plural ordering
    # HEAD ONLY. There the ordering instruction IS the question's request and
    # the window comes from a separate, resolved clause, so the guard's
    # reason ("the N-ary arms cannot read an ordering instruction") does not
    # apply. The guard is otherwise untouched: every pinned `latest` /
    # `most recent` negative that is NOT a class-plural head — including the
    # window-less class-plural row "the order of the six museums I visited
    # from earliest to latest?" — still returns None.
    #
    # The rest of the R13 discipline IS re-applied here explicitly: this
    # branch skips the guard block, so every guard surface that is not the
    # recency arm must still hold. A calendar-anchor/other relative surface
    # OUTSIDE this shape's own window (`_RE_AGO_OTHER`), a promoted or bare
    # relative offset, a frequency/count head, a duration head (`_RE_DURATION`
    # — this branch returns before the blanket duration guard below, so it
    # must re-apply it itself: #8779 E3), an advice frame, or a 2+ `or` chain
    # (the N-ary surface) all reject with `None`, exactly as they do below.
    #
    # CLAIM ACCURACY: "still reject" is not the whole truth for this branch.
    # Two constructs `main` ROUTED to a firing shape — "... in the past two
    # months, most recently?" (AGO_RELATIVE) and "... in the past two months
    # last Saturday?" (RELATIVE_DATE) — now land on `None` here, because the
    # branch is entered first and its `_relative_match`/`_RE_AGO_OTHER` guard
    # fires. That is the SAFE direction (legacy answers), but it is a routing
    # change, and it is recorded rather than implied. The NAMED-HOLIDAY forms
    # are in that list TOO, and one step further down: the question is
    # exempted from the day-lookup surface so this branch can decide it, and
    # the branch then ABSTAINS (the anchor clause below) — so it reaches
    # NEITHER shape and `classify_question` returns `None` where `main`
    # returned `relative-date-lookup`. The named day is DROPPED, not
    # preserved; measured at F17, and the earlier comment here claimed the
    # opposite.
    #
    # A binary `first … or …` ordering question is DIFFERENT: it names two
    # subjects, so the shape ("names NO subjects") must not claim it — but at
    # main it was routed to `ORDERING`, not rejected. So it is NOT put in the
    # guard list (a `return None` here would short-circuit the ordering branch
    # below and LOSE that answer): the class-plural branch is SKIPPED for it,
    # and the ordinary pipeline routes it to `ORDERING` as before (#8779 F1).
    # `_RE_ORDERING_LIST` is exempted for the SAME reason: the N-ary colon-list
    # ordering question is an ORDERING path at main too (`_nary_terms` +
    # `(_RE_ORDERING or _RE_ORDERING_LIST)` guard), and this branch caught it
    # FIRST -- which lost `ORDERING` two ways (→ `class-plural` for the
    # comma form, → `None` via the `_RE_OR_CHAIN` re-guard for the or-chain
    # form). Measured on the head: a colon-list form classified `ordering` at
    # main and `class-plural`/`None` here (#8779 F1, the list half).
    m_cls = _RE_CLASS_PLURAL.match(q)
    if m_cls is not None and not (
            _RE_ORDERING.match(q) or _RE_ORDERING_LIST.match(q)
            # ...and a compound question that names a CURRENT-STATE subject
            # ("…, and what is the current status of my car?") is not a class
            # enumeration either: this branch runs BEFORE the current-state
            # arm, so claiming it LOST the status answer and replaced it with
            # an enumeration of the other half (#8779 F5). `main` routes that
            # string to CURRENT_STATE, so it must not be outranked here.
            or _RE_CURRENT.match(q) or _RE_CURRENT_STILL.match(q)):
        # The shape's OWN window IS the relative window the template already
        # resolved, and it may itself spell `last <unit>`; `_RE_AGO_OTHER`'s
        # calendar surface is an ADDITIONAL such offset ELSEWHERE in the
        # question ("... in the last month last Saturday?"), so the window's
        # own text is blanked before that one guard. Without this the
        # count-less `last <unit>` arm of the template was dead for
        # week/month/year while the grammar advertised it (#8779 E4/F2).
        residual = q[:m_cls.start("window")] + " " + q[m_cls.end("window"):]
        if (_RE_AGO_OTHER.search(residual)
                # ⛔ the SAME surface as `_RE_AGO_OTHER`, for the anchors that
                # regex does not hold: any calendar anchor in the residual —
                # a named holiday, a WEEKDAY form (`this past Saturday`, `past
                # weekend`), or a weekend form — means the question is not a
                # pure relative-window enumeration. `_named_fixed_date_anchor`
                # only held the FIXED_DATE family, so `this past weekend`,
                # `past weekend` and `past Saturday` slipped it and FIRED
                # here, dropping the named day that `main` resolved (#8779
                # F18). `find_anchor_spans` is the whole surface.
                or find_anchor_spans(residual)
                or _RE_FREQUENCY.search(q)
                or _RE_ADVICE.search(q) or _RE_OR_CHAIN.search(q)
                or _RE_DURATION.search(q)
                or _relative_match(q) is not None
                or _RE_AGO_RESIDUAL.search(q)):
            return None
        return AssemblyShape.CLASS_PLURAL
    # R13 rejects that are NOT promotable — mutually independent "return
    # None" clauses, so their order among themselves carries no semantics.
    # They run FIRST: the CALENDAR-anchor vocabulary (sibling #2655's
    # family), frequency/count, recency-as-ordering-instruction and
    # advice/preference (#2652 deliberately REMOVED the promoted
    # ago/recency vocabulary from this guard). The blanket duration guard
    # is NOT here either (#2654): the duration-state template below must be
    # reachable, and `_RE_DURATION` rejects every OTHER duration/count
    # morphology right after it.
    if (_RE_AGO_OTHER.search(q) or _RE_FREQUENCY.search(q)
            or _RE_RECENCY.search(q) or _RE_ADVICE.search(q)):
        return None
    # N-ary (3+ options) is a R13 reject for EVERY shape template that
    # follows — including duration-state — so it stays ahead of all of
    # them (#2654's ordering: main ran this before `_RE_DURATION_STATE`).
    # #2653 lifts that reject ONLY for a genuine N-ary ORDERING list: the
    # exemption is gated on an ordering HEAD as well as a decomposable
    # option span, so an or-chain that is not an ordering question keeps
    # main's hard reject here instead of falling through to the
    # duration-state/interval arms below.
    if _RE_OR_CHAIN.search(q) and not (
            _nary_terms(q)
            and (_RE_ORDERING.match(q) or _RE_ORDERING_LIST.match(q))):
        return None
    # #2654 — duration-state ("how long had I been X when Y"): the state is
    # a single resolved subject and the span runs from its state entry to
    # the as-of date. Checked BEFORE the ago-relative routing so the R13
    # reject is preserved: a relative offset kills a duration-state
    # question too ("how long had I been X two weeks ago when Y" — there
    # the offset is the question's window, not a state-entry anchor), and
    # BEFORE the duration guard so the guard keeps rejecting every OTHER
    # duration/count morphology.
    if _RE_DURATION_STATE.match(q):
        # R13 reject-on-relative-offset: the PROMOTED offsets (#2652) and the
        # un-promoted bare-'ago' residue alike kill the duration-state form —
        # there the offset is the question's as-of window, not a state-entry
        # anchor, so firing would answer the state span out to a
        # question_date the question does not mean.
        if (_relative_match(q) is not None
                or _RE_AGO_RESIDUAL.search(q)):
            return None
        return AssemblyShape.DURATION_STATE
    if _RE_DURATION.search(q):
        return None
    if _relative_guarded(q) is not None:
        return AssemblyShape.AGO_RELATIVE
    if _relative_match(q) is not None:
        # R13 COMPOUND PIN: a relative offset IS present but a two-subject
        # template also matches — the offset is narrative anchor text there
        # ('since I bought the couch two weeks ago when I sold it'), not the
        # question's window. Reject (never hijack the interval/ordering
        # shape, and never let the offset leak back in as an anchor).
        return None
    # #2652 residual (the #2165 R13 reject, narrowed to what #2652 did NOT
    # promote): a BARE 'ago' the promoted vocabulary does not own — minutes/
    # hours/centuries, 'a long time ago' — is still a past-time marker, so
    # the question must NOT fall through to a template that reads it as
    # current ("what is the current status of the couch two hours ago?"
    # would otherwise fire CURRENT_STATE with the offset inside the
    # subject). The promoted offsets were routed above; this is the residue.
    if _RE_AGO_RESIDUAL.search(q):
        return None
    if _RE_INTERVAL_SINCE.match(q):
        return AssemblyShape.INTERVAL
    if _RE_INTERVAL_BETWEEN.match(q) or _RE_INTERVAL_BETWEEN_LEAD.match(q):
        return AssemblyShape.INTERVAL
    m_ord = _RE_ORDERING.match(q)
    m_list = None if m_ord else _RE_ORDERING_LIST.match(q)
    if m_list and _RE_CURRENT.match(q):
        # "the order I placed:" is a CURRENT_STATE subject, not an option
        # list — the ordering-list arm must not steal it. (`_RE_CURRENT_STILL`
        # cannot match here: it is anchored on "is …", which `_RE_ORDERING_LIST`
        # — anchored on which/who/what — can never share.)
        m_list = None
    if m_ord or m_list:
        # N-ary (#2653): 3+ named options DECOMPOSE instead of being rejected
        # (R13's "N-ary out of v1" is lifted here). classify and extract read
        # the SAME predicate, so a fired ORDERING shape can never come back
        # with fewer than 3 terms for a 3+ list (R1).
        if _nary_terms(q):
            return AssemblyShape.ORDERING
        if m_ord is None:
            # the colon-list head with fewer than 3 decomposable options is
            # not a shape this branch can answer
            return None
        # ── binary two-option path (UNCHANGED since #2165) ──
        parts = re.split(r"\s+or\s+", q, flags=re.IGNORECASE)
        a_side = parts[-2] if len(parts) >= 2 else ""
        m_first = re.search(r"\bfirst\b", a_side, re.IGNORECASE)
        a_seg = (a_side[m_first.end():] if m_first else a_side)
        # skip the marker DELIMITER itself (", my cousin's wedding" /
        # ": my graduation" / "- the couch") before the comma check
        a_seg = re.sub(r"^[\s,:;–—-]+", "", a_seg)
        a_seg = _strip_time_clause(a_seg)
        if "," in a_seg or ";" in a_seg:
            return None
        return AssemblyShape.ORDERING
    if _RE_OR_CHAIN.search(q):
        # an or-chain outside an ordering head is never a shape (an interval
        # anchor is "between A and B", not a 3-way option list)
        return None
    if _RE_CURRENT.match(q) or _RE_CURRENT_STILL.match(q):
        return AssemblyShape.CURRENT_STATE
    return None


def _strip_time_clause(seg: str) -> str:
    """Drop a leading time/scope clause ("first in February, the bike" —
    the comma is a clause boundary, NOT a second option)."""
    return _RE_TIME_CLAUSE.sub("", seg, count=1)


def _reads_as_list(opts: list[str], prefix: str) -> bool:
    """Does this split actually read as an OPTION LIST?

    A colon inside a quoted option ("'Star Wars: A New Hope', 'Empire', …")
    leaves a FRAGMENT first — a term that closes a quote it never opened —
    followed by an option that opens the SAME quote. A whole first option
    that merely ends in a quote (an inch mark `5"`, a possessive "the
    girls'") is told apart by the PREFIX: the fragment's opening quote is
    real, so `prefix` holds an unmatched delimiter of that kind, whereas an
    inch mark's or a possessive's quote has none before the colon (and a
    possessive apostrophe is not an opening one — it follows a word
    character).
    """
    if len(opts) < 2:
        return False
    first = opts[0]
    if first[:1] in ("'", '"') or first[-1:] not in ("'", '"'):
        return True
    closer = first[-1:]
    if opts[1][:1] != closer:
        return True
    at = prefix.rfind(closer)
    if at < 0:
        return True
    return at != 0 and prefix[at - 1].isalnum()      # an apostrophe, not a quote


def _list_colon_index(q: str) -> int:
    """Index of the option-list colon, or -1 when the question has none.

    The introducer is the last colon whose TAIL actually reads as an option
    list: at least two options, each with balanced quotes. Walking right to
    left is what stops a colon inside a quoted option ("'Star Wars: A New
    Hope'") from cutting the list in two, WITHOUT having to decide whether
    any particular `'` is a quote or an apostrophe — the tail after such a
    colon is not a list ("A New Hope', 'Empire', …" starts a term on a stray
    quote), so the scan steps over it. The same test disposes of a
    time-of-day head colon and a trailing time clause ("… and the meeting at
    3:00?"): their tails hold a single option.
    """
    for i in range(len(q) - 1, -1, -1):
        if q[i] != ":":
            continue
        if q[i - 1:i].isdigit() and q[i + 1:i + 2].isdigit():
            continue        # a time-of-day colon ("at 3:00"), not an introducer
        if _reads_as_list(_split_option_list(q[i + 1:]), q[:i]):
            return i
    return -1


def _option_list_segment(question: str) -> str:
    """The option-list span of an ordering question.

    The list-introducing colon WINS when the question carries one — the span
    is cut there, never at a colon inside a quoted option ("'Star Wars: A
    New Hope'"). The ordering-list template alone cannot decide this: its
    tail after the first colon admits further colons, so it matches a
    question whose HEAD carries a time ("the events at 3:00: the couch, …")
    with the TIME colon as the introducer. With no such colon, everything
    after the FIRST ordering marker word ("which did I get first, A, B or
    C") — first-occurrence on purpose, so "my first child" is not read as
    the marker.
    """
    q = question.strip()
    ci = _list_colon_index(q)
    if ci >= 0:
        seg = q[ci + 1:]
    else:
        m = _RE_ORDER_MARKER.search(q)
        seg = q[m.end():] if m is not None else ""
    return _strip_time_clause(re.sub(r"^[\s,:;–—-]+", "", seg))


def _split_option_list(seg: str) -> list[str]:
    """Deterministic option split over a clause span.

    Top-level commas plus the conjunctions that INTRODUCE an option
    ("A, B, and C" / "A, B or C" / "A or B or C"). An internal conjunction
    is kept — "the concerts and musical events" stays ONE option (a plain
    " and " split would fragment it). Over-splitting is fail-CLOSED: the
    extra term does not resolve, the R1 all-terms gate keeps the fired
    decision false.
    """
    pieces = [p.strip() for p in re.split(r",\s*", seg.strip().rstrip(" ?"))
              if p.strip()]
    opts: list[str] = []
    for piece in pieces:
        piece = re.sub(r"^(?:and|or)\s+", "", piece, flags=re.IGNORECASE)
        for part in re.split(r"\s+or\s+", piece.strip(),
                             flags=re.IGNORECASE):
            if part.strip():
                opts.append(part.strip())
    return opts


def _nary_option_list(opts: list[str], *, explicit: bool) -> bool:
    """True when the split options are a genuine 3+ option list.

    ``explicit`` (a list-introducing colon) is the strong signal and needs no
    shape check. Without it every NON-FINAL option must look like an option
    phrase, so a binary question whose A-side merely contains a comma
    ("the couch, which I loved, or the chair") is not split into three.
    """
    if len(opts) < 3:
        return False
    if explicit:
        return True
    return all(_RE_OPTION_LEAD.match(o) for o in opts[:-1])


def _nary_terms(question: str) -> list[str]:
    """The N-ary option terms of an ordering question ([] when the option
    list is not a genuine 3+ list).

    classify_question and extract_subject_terms both read THIS — one
    predicate, so the fired shape and the extracted term count cannot
    disagree (an N-ary fire with 2 terms would silently narrow the shape).
    """
    q = question.strip()
    # The list is EXPLICIT when a real introducer colon was found — not merely
    # when the template matched. On a question whose only colon sits inside an
    # OPTION ("the meeting at 3:00"), the template matches yet there is no
    # introducer, the marker fallback leaves the ordering head in the segment,
    # and `explicit` would wave the `_RE_OPTION_LEAD` shape check through on a
    # leaked first term.
    explicit = _list_colon_index(q) >= 0
    opts = _split_option_list(_option_list_segment(q))
    if not _nary_option_list(opts, explicit=explicit):
        return []
    return [_clean(o) for o in opts]


def _clean(term: str) -> str:
    """Whitespace/leading-noise clean of an extracted subject span."""
    t = term.strip()
    t = re.sub(
        r"^(?:the\s+)?(?:day|time)\s+(?=(?:i|we|they|my)\s)", "", t,
        flags=re.IGNORECASE)
    t = re.sub(r"^(?:my\s+|the\s+)?(?:participation\s+in|attendance\s+at)\s+",
               "", t, flags=re.IGNORECASE)
    # strip ONLY whitespace + sentence punctuation — NEVER quote chars: a
    # quoted subject may be a mid-span title ("finished reading 'The Hate U
    # Give'") whose closing quote must survive for the Task-3 resolver
    return t.strip(" ?.,;:—–-") or term.strip()


def extract_subject_terms(question: str,
                          shape: AssemblyShape | None = None,
                          ) -> list[str]:
    """Subject text spans for the shape (raw — quotes/possessives intact).

    * ordering: A/B either side of the marker (split on the LAST " or ").
    * interval: the "between A and B" / "since A when B" spans.
    * current-state: the single subject of "the current status of X".
    * duration-state (#2654): the SINGLE state subject of "how long had I
      been <state> when <event>" — the state predicate peeled by
      ``_state_term``. The "when <event>" clause is deliberately NOT
      returned as a second term: the window's other end is the question's
      as-of date, so demanding a second resolution would gate the shape on
      a subject the arithmetic never reads (an event clause is "I attended
      the meetup", not an entity name the resolver can be held to).
    * class-plural (#8056): [] — a CLASS is not an Object. The selector is
      the resolved WINDOW plus the class tokens
      (``resolve_class_window``); the fired path branches before extraction.

    Returns [] when the template yields no usable span (the caller's R1
    both-halves gate then keeps the fired decision false).
    """
    if not question or not question.strip():
        return []
    if shape is None:
        shape = classify_question(question)
    if shape is None:
        return []
    q = question.strip()
    if shape is AssemblyShape.RELATIVE_DATE:
        # #2655: the DAY is the scope, not a subject — there is no entity span
        # to resolve, and the caller's fired path branches before extraction.
        # Returning a span here would feed the resolver a fragment of the
        # question (e.g. "the past weekend") and fabricate a subject.
        return []
    if shape is AssemblyShape.AGO_RELATIVE:
        # #2652: the SELECTOR is the time window resolved by
        # ``resolve_relative_anchor`` against question_date — not an
        # Object. There is no subject span to extract, and returning a
        # raw activity phrase as if it were one would make the R1
        # both-halves gate reject every ago question. [] is the honest
        # answer; the fired gate for this shape is "anchor resolved".
        return []
    if shape is AssemblyShape.CLASS_PLURAL:
        # #8056: the selector is the WINDOW plus the question's CLASS — a
        # class is not an Object, and the Task-3 resolver probes Object
        # names/aliases, so a span handed to it could only resolve a
        # fragment of the class phrase ("concerts") and fabricate a
        # subject the question never named. [] is the honest answer; the
        # fired gate for this shape is "window + class resolved", and the
        # class is carried by ``resolve_class_window``.
        return []
    if shape is AssemblyShape.CURRENT_STATE:
        m = _RE_CURRENT.match(q) or _RE_CURRENT_STILL.match(q)
        return [_clean(m.group("a"))] if m else []
    if shape is AssemblyShape.DURATION_STATE:
        m = _RE_DURATION_STATE.match(q)
        return [_state_term(m.group("state"))] if m else []
    if shape is AssemblyShape.INTERVAL:
        m = (_RE_INTERVAL_SINCE.match(q) or _RE_INTERVAL_BETWEEN.match(q)
             or _RE_INTERVAL_BETWEEN_LEAD.match(q))
        if not m:
            return []
        return [_clean(m.group("a")), _clean(m.group("b"))]
    # ordering — N-ary list first (#2653): 3+ named options decompose into
    # pairwise/chain comparisons over the SAME dated spine.
    nary = _nary_terms(q)
    if nary:
        return nary
    # binary — split on the LAST " or " (two named options guaranteed by the
    # shape template + the N-ary predicate)
    parts = re.split(r"\s+or\s+", q, maxsplit=0, flags=re.IGNORECASE)
    if len(parts) < 2:
        return []
    a_raw, b_raw = parts[-2], parts[-1]
    if "," in a_raw:
        # the A term sits after the marker clause ("… first, A" / "… first in
        # February, A") — take everything after the LAST comma
        a = a_raw.rsplit(",", 1)[-1]
    else:
        # dash/space-delimited marker ("which came first - the couch")
        m_first = re.search(r"\bfirst\b", a_raw, re.IGNORECASE)
        if m_first:
            a = a_raw[m_first.end():]
            a = re.sub(r"^[\s\-–—:]+", "", a)
        else:
            a = a_raw
    return [_clean(a), _clean(b_raw)]


# ══════════════════════════════════════════════════════════════════════════
# #2165 Task 3 — resolver: recall-first, confidence-tagged, no LLM
# (R1 both-halves gate, R7 never a silent single-match, R10 embedded
# degrade, R12/C7 collision-with-ids).
#
# Chain per subject term: (1) exact-name/id probe (embedded-safe — one
# batched query over normalized variants) → high; (2) docker Object
# name-FTS for paraphrases → med; (3) alias amplifier from anchored
# points' search_keys → low. Missing/raising FTS/alias legs degrade to []
# (R10) — a term that still resolves nowhere stays UNRESOLVED (the
# both-halves gate keeps the fired decision false, R1).
# ══════════════════════════════════════════════════════════════════════════

from dataclasses import dataclass, field  # noqa: E402
from datetime import UTC as _UTC, date as _date, datetime as _datetime  # noqa: E402
from typing import Protocol  # noqa: E402
# #3302: the canonical OBJECT terminal vocabulary. Safe as a module-level
# import: commit_ops' import closure does not reach assembly (verified), so
# this edge cannot cycle — unlike projection/entities.py, which imports the
# same set at FUNCTION level for its own reasons.
from tortoise.commit_ops import OBJECT_TERMINAL_STATUSES  # noqa: E402

# #3317: the Object statuses the RESOLVER will not resolve — the resolver's
# view of the Object-SEARCH exclusion boundary (deliberately NARROWER than
# the read-surface vocabulary ``commit_ops.OBJECT_TERMINAL_STATUSES``
# = superseded / deprecated / archived / retracted):
#
# * ``superseded`` MUST resolve — the state render ("STATE (couch):
#   superseded by sofa") IS the answer to the canonical current-state
#   question, and excluding it makes that question stop firing
#   (RED: test_resolver_docker_exact_and_both_halves).
# * ``outdated`` must not enter the OBJECT-RESOLUTION predicate: it is the
#   POINT vocabulary and the object read/search surface deliberately
#   surfaces it (#2977 scope §1 D3). (This is about the resolver's set
#   only — the successor-EXISTENCE probe lower in this file keeps its own,
#   WIDER, deliberately-divergent view; see
#   ``_RECALL_OBJECT_EXCLUDED_STATUSES``.)
# * ``deprecated`` / ``archived`` Objects still resolve and render their own
#   status verbatim (``STATE (x): deprecated`` — never "current", which is
#   R17 P3-3's requirement, satisfied by the verbatim state read).
#
# A ``retracted`` Object is a REMOVED Object (#2977): it has no current
# state to report, so there is nothing to resolve — #2977's target (c),
# "invisible to the read surfaces". The literal matches the established
# "exclude retracted" idiom (``hosted_api.py:5011/:5023/:5047``).
#
# ⛔ DELIBERATE NARROWING, NOT A COPY. The read-surface vocabulary is
# ``commit_ops.OBJECT_TERMINAL_STATUSES`` (#3301), and the search lane now
# applies it; THIS port resolves the wider set on purpose (superseded /
# deprecated / archived Objects must resolve to render their own state), so
# its FTS leg passes THIS set into the FTS index query and never the read
# lane's wider vocabulary. Re-point this constant at the canonical set and the current-state
# question stops firing.
_RESOLVER_UNRESOLVABLE_OBJECT_STATUSES = frozenset({"retracted"})


@dataclass(frozen=True)
class SubjectCandidate:
    """One resolved Object candidate for one subject term (R7/R12/C7)."""

    subject_index: int          # index into the shape's term list
    object_id: str
    name: str
    confidence: str             # "high" (exact) | "med" (fts) | "low" (alias)
    source: str                 # "exact" | "fts" | "alias"
    term: str = ""


@dataclass(frozen=True)
class ResolveResult:
    candidates: tuple[SubjectCandidate, ...] = ()
    unresolved: tuple[str, ...] = ()

    def candidates_for(self, subject_index: int) -> list[SubjectCandidate]:
        return [c for c in self.candidates if c.subject_index == subject_index]

    def both_halves_ok(self, shape: AssemblyShape | None,
                       n_terms: int | None = None) -> bool:
        """R1: two-subject shapes admit BOTH halves or fire nothing. A
        single-subject shape (current-state) fires when its one term
        resolved. None shape never fires here (classify first).

        ``n_terms`` (#2653): the number of subject terms the shape extracted.
        Omitted (the legacy binary callers) means the two-half contract; the
        N-ary ordering path passes the extracted count so an N-way question
        admits ALL N or nothing — a partial admit would silently narrow the
        shape exactly as a missing half does for the two-option form.
        """
        if shape is None:
            return False
        if shape is AssemblyShape.CURRENT_STATE:
            # never fire with ZERO candidates — an empty resolved set is an
            # unresolved subject, not a match
            return bool(self.candidates) and not self.unresolved
        if shape is AssemblyShape.DURATION_STATE:
            # #2654: ONE graph anchor (the state subject). The window's
            # other end is the as-of date the caller already supplied, so
            # this shape takes the single-subject gate — demanding a second
            # resolved subject would gate on a span the arithmetic never
            # reads (see extract_subject_terms).
            return bool(self.candidates) and not self.unresolved
        # ordering/interval: every term must resolve (a partial admit would
        # silently narrow the shape to a single subject — forbidden)
        expected = 2 if n_terms is None else n_terms
        return not self.unresolved and len(
            {c.subject_index for c in self.candidates}) == expected


class ResolverPort(Protocol):
    """The graph seam the resolver reads through (dict-stubbed in unit
    tests; the docker adapter wraps a live TortoiseSDK/projection)."""

    def exact_objects(self, names: list[str]) -> list[dict]: ...

    def excluded_exact_objects(self, names: list[str]) -> list[dict]: ...

    def fts_objects(self, term: str, limit: int = 8) -> list[dict]: ...

    def alias_objects(self, term: str, limit: int = 8) -> list[dict]: ...


# name normalization: leading determiners/possessives/gerunds + a head cut
# at participial/relative modifiers ("the dog bed getting chewed" → dog bed;
# "the couch I bought in March" → the couch)
_LEAD_NOISE = re.compile(
    r"^(?:the|a|an|my|our|their|your|her|his|its)\s+", re.IGNORECASE)
_GERUND_LEAD = re.compile(
    r"^(?:(?:buy|sell|order|get|fix|trim|start|use|visit|attend|join|take|"
    r"paint|move|replace|purchase|clean|repair|finish|read|watch|meet|see|"
    r"plant|water|harvest|cancel|cook|host|try|rent|build|adopt|bring|wear|"
    r"make|collect|receive|deliver|return|lose|find|book|plan|host|attend|"
    r"participate|complete|set\s+up|sign\s+up\s+for)\w*\s+)", re.IGNORECASE)
_HEAD_CUT = re.compile(
    r"\s+(?:(?:i|you|we|they|he|she|it)\s+)?(?:getting|being|bought|"
    r"sold|delivered|received|ordered|moved|chewed|repaired|painted|fixed|"
    r"replaced|started|finished|installed|came|went|that|which|who|from|"
    r"by|with|in)\b", re.IGNORECASE)

# ── #2654 duration-state: the STATE span is a PREDICATED state, not a bare
#    entity name ────────────────────────────────────────────────────────
# "a member of 'Book Lovers Unite'", "watching stand-up comedy specials
# regularly", "taking guitar lessons" — the Task-3 resolver's legs must
# probe the ENTITY the state is about, so the state predicate is peeled
# before the term reaches them. Deliberately narrow, and only at the LEAD:
# a determiner/possessive lead is left alone (``_candidate_names`` already
# derives the article-stripped variants), and a gerund mid-span is part of
# the entity's own name.
_STATE_LEAD = re.compile(
    r"^(?:an?\s+(?:(?:active|new|long[- ]time|founding)\s+)?"
    r"(?:member|participant|user|owner|holder)\s+of|"
    r"participation\s+in|attendance\s+at)\s+", re.IGNORECASE)
#: the gerund forms of the same predicate ("watching X", "using X")
_STATE_GERUND = re.compile(
    r"^(?:being|watching|using|taking|learning|studying|practi[cs]ing|"
    r"training|doing|playing|building|collecting|reading|writing|growing|"
    r"brewing|attending|following|keeping|hosting|organi[sz]ing|visiting)\s+",
    re.IGNORECASE)
#: a trailing MANNER/duration adverbial ("… specials regularly") — the
#: state's cadence, never part of the entity name
_STATE_MANNER = re.compile(
    r"\s+(?:regularly|consistently|frequently|daily|weekly|monthly|again|"
    r"for\s+(?:a|some|several|many)\s+(?:time|while|years?|months?|weeks?))$",
    re.IGNORECASE)


def _state_term(span: str) -> str:
    """The entity term inside a duration-state's STATE span.

    "a member of 'Book Lovers Unite'" -> "'Book Lovers Unite'"; "watching
    stand-up comedy specials regularly" -> "stand-up comedy specials".
    Returns the ``_clean``-ed span unchanged when no predicate lead matches
    — never an empty term.
    """
    t = _clean(span)
    t = _STATE_LEAD.sub("", t, count=1).strip()
    t = _STATE_GERUND.sub("", t, count=1).strip()
    t = _STATE_MANNER.sub("", t, count=1).strip()
    return _clean(t) or _clean(span)


def _candidate_names(term: str) -> list[str]:
    """Ordered name variants for the exact probe (raw span → cleaned head →
    article-stripped head → gerund+article-stripped → quote-stripped).
    Empty/inert terms yield [] (no probe)."""
    out: list[str] = []
    t = term.strip().strip(" ?.,;:—–-")
    if not t:
        return []
    # strip a WRAPPING quote pair so quoted titles reach the exact probe
    # ("'The Hate U Give'" → The Hate U Give); mid-span quotes untouched
    if len(t) >= 2 and t[0] in "'\"'" and t[-1] == t[0]:
        t = t[1:-1].strip()
    if not t:
        return []
    out.append(t)
    # head cut at a participial/relative modifier
    m = _HEAD_CUT.search(t)
    head = t[: m.start()].strip() if m else t
    for v in (head,):
        if v and v not in out:
            out.append(v)
    v = _LEAD_NOISE.sub("", head, count=1).strip() if head else ""
    for cand in (v,):
        if cand and cand not in out:
            out.append(cand)
    v2 = _GERUND_LEAD.sub("", t, count=1)
    for cand in (v2, _LEAD_NOISE.sub("", v2, count=1).strip()):
        if cand and cand not in out:
            out.append(cand)
    return out


def _excluded_exact_match(port: ResolverPort, names: list[str]) -> bool:
    """True iff ``names`` matched an Object the exclusion set forbids.

    ``exact_objects`` returns LIVE rows only (#3317), so an excluded exact
    match is invisible to it — this probe recovers the distinction between
    "no match" and "a match that must not be surfaced" (#4061 R1).

    Fail-SAFE polarity, deliberately: a probe that cannot answer (raise) is
    treated as an excluded match, because the alternative — falling through
    to a weaker leg — is the fail-open subject substitution this fix exists
    to remove. A port with no exclusion concept (a plain dict stub, the
    embedded lane) has no excluded match to find, so an absent probe is
    False, preserving the pre-existing fall-through for those ports.
    """
    if not names:
        return False
    probe = getattr(port, "excluded_exact_objects", None)
    if probe is None:
        return False
    try:
        return bool(probe(names))
    except Exception:
        return True


def resolve_subjects(port: ResolverPort, terms: list[str], *,
                     shape: AssemblyShape | None) -> ResolveResult:
    """Deterministic no-LLM resolver: prose terms → Object candidates.

    Confidence/source: exact probe → high/exact; Object name-FTS → med/fts;
    anchored search_keys alias → low/alias. Never a silent single match
    (R7): a term matching several Objects yields every candidate with its
    id. Missing/raising FTS or alias legs degrade to [] (R10 embedded).
    """
    candidates: list[SubjectCandidate] = []
    unresolved: list[str] = []
    for idx, term in enumerate(terms):
        names = _candidate_names(term)
        # leg 1 — exact name/id probe (embedded-safe, batched)
        found: list[dict] = []
        if names:
            try:
                found = list(port.exact_objects(names) or [])
            except Exception:
                found = []
        if found:
            seen: set[str] = set()
            for row in found:
                oid = str(row.get("id") or "")
                key = f"{oid}\x00{row.get('name')}"
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(SubjectCandidate(
                    subject_index=idx, object_id=oid,
                    name=str(row.get("name") or ""),
                    confidence="high", source="exact", term=term))
            continue
        # R1 (#4061): the exact leg is the term's NAMED match. #3317's
        # exclusion made an excluded Object invisible to it, which left the
        # resolver unable to tell "no exact match" from "the exact match
        # must not be surfaced" — so the weaker legs ran and a DIFFERENT,
        # live Object was substituted for the excluded one the question
        # actually named. Distinguish the two: a named-but-unresolvable
        # match ABSTAINS (no candidate for the term) instead of falling
        # through. A term that names nothing still falls through unchanged.
        if _excluded_exact_match(port, names):
            unresolved.append(term)
            continue
        # leg 2 — docker Object name-FTS (paraphrase recall)
        rows: list[dict] = []
        try:
            if names:
                rows = list(port.fts_objects(names[0][:160]) or [])
        except Exception:
            rows = []
        if rows:
            seen = set()
            for row in rows:
                oid = str(row.get("id") or "")
                key = f"{oid}\x00{row.get('name')}"
                if key in seen or oid in {c.object_id
                                          for c in candidates
                                          if c.subject_index == idx}:
                    continue
                seen.add(key)
                candidates.append(SubjectCandidate(
                    subject_index=idx, object_id=oid,
                    name=str(row.get("name") or ""),
                    confidence="med", source="fts", term=term))
            continue
        # leg 3 — alias amplifier (anchored points' search_keys)
        rows = []
        try:
            rows = list(port.alias_objects(term) or [])
        except Exception:
            rows = []
        if rows:
            seen = set()
            for row in rows:
                oid = str(row.get("id") or "")
                key = f"{oid}\x00{row.get('name')}"
                if key in seen or oid in {c.object_id
                                          for c in candidates
                                          if c.subject_index == idx}:
                    continue
                seen.add(key)
                candidates.append(SubjectCandidate(
                    subject_index=idx, object_id=oid,
                    name=str(row.get("name") or ""),
                    confidence="low", source="alias", term=term))
            continue
        unresolved.append(term)
    return ResolveResult(candidates=tuple(candidates),
                         unresolved=tuple(unresolved))


def docker_resolver_port(sdk) -> ResolverPort:
    """Adapter over a live TortoiseSDK: exact probe via the projection's
    Object id/name index (one batched query), FTS via the shared FTS leg
    (``search_engine.run_fts_query``, ``entity_type='object'`` — #3223: the
    NAME index itself, not the hybrid read surface), alias via one anchored
    search_keys query. Function-level imports keep the module import-safe
    (no sdk import at module scope).

    #3317: every leg of THIS PORT excludes the UNRESOLVABLE Object statuses
    (``_RESOLVER_UNRESOLVABLE_OBJECT_STATUSES`` — today ``retracted``), so
    resolution here cannot return a removed Object. (Scoped to this port:
    the sibling Object-anchor resolvers — ``aggregate.py``,
    ``coverage_loop.py`` — are status-BLIND: #3301 widened the shared search
    lane's Object exclusion, and those resolution-only legs opt back out of
    it via ``excluded_statuses=()``, so they still resolve terminal Objects.)
    The exact + alias legs carry it as a Cypher conjunct (the graph filters;
    the batched exact probe stays one query). The FTS leg passes it straight
    into the index query (``search_engine.run_fts_query``'s
    ``excluded_statuses``), from the SAME constant, so the narrow set here
    stays the only filter on that leg and a SUPERSEDED Object still resolves
    (#3301) — the query's own WHERE applies it, before its LIMIT.

    #4061 (R1/R2/R3) closes the three residuals #3317 left in THIS port:
    ``excluded_exact_objects`` makes the exact leg's exclusion DISTINGUISHABLE
    from an absent match (so the resolver can abstain instead of substituting
    a weaker leg's live Object); the FTS leg's exclusion is applied BY the
    index query before its LIMIT, so an excluded Object cannot consume the
    leg's bound (the pre-#4061 post-truncation Python filter let >=limit
    excluded Objects starve a live candidate); and ``status`` is read by that
    same query, so the SDK's batch content-fetch degradation cannot drop it
    and let a retracted Object read as live.
    """
    proj = sdk._get_proj()
    # Object-scoped predicate, stated inline rather than routed through
    # ``search_engine._exclude_status_clause``: that helper's ALIAS handling
    # and WHERE-fragment shape differ from this port's plain conjunct, and the
    # resolver's set is a deliberate NARROWING (see
    # ``_RESOLVER_UNRESOLVABLE_OBJECT_STATUSES``), not the read surface's
    # vocabulary. Derived from the constant so the Cypher legs and the FTS
    # leg share one vocabulary; #3301's Object lanes in ``live``
    # (``excluded`` + ``include_outdated_flag``) compose the WIDER read-surface
    # predicate, which this port deliberately does not apply.
    #
    # WELL-FORMED AT ANY CARDINALITY: an emptied constant must not leave a
    # dangling `AND` in either query — the resulting Cypher error is swallowed
    # by ``resolve_subjects``' per-leg `except`, which would turn the
    # connected-assembly lane off silently. Empty set = no exclusion.
    status_predicate = " AND ".join(
        f"(o.status IS NULL OR o.status <> '{s}')"
        for s in sorted(_RESOLVER_UNRESOLVABLE_OBJECT_STATUSES))
    status_filter = f"AND ({status_predicate}) " if status_predicate else ""

    def exact_objects(names: list[str]) -> list[dict]:
        # #3633 §B.1 route-then-refuse, per ref: the single
        # `o.name IN $names OR o.id IN $names` union mixed the id space and the
        # name space in ONE name-keyed coordinate and silently returned both.
        # The arms are resolved APART — an exact id match is unambiguous and
        # wins; a NAME-form ref resolves only when exactly ONE Object holds it.
        # Two or more same-name carriers is a refusal (the non-folded entry is
        # recorded, the ref is dropped), never a union.
        #
        # STATUS NOTE: the predicate here is the port-local
        # `_RESOLVER_UNRESOLVABLE_OBJECT_STATUSES` (`{retracted}`), NOT
        # `entity_identity`'s `_terminal_excluded` — deliberately, because this
        # leg is assembly's resolve-time Object-search surface (#3317/#2977),
        # so a `superseded` Object can still resolve by name here. "Exactly one"
        # is about HOLDER COUNT, not about widening the status set.
        id_rows = proj.g.query(
            "MATCH (o:Object) "
            "WHERE o.id IN $names "
            f"{status_filter}"
            "RETURN o.id, o.name",
            params={"names": names}).result_set
        name_rows = proj.g.query(
            "MATCH (o:Object) "
            "WHERE o.name IN $names "
            f"{status_filter}"
            "RETURN o.id, o.name",
            params={"names": names}).result_set
        by_id: dict[str, list] = {}
        for r in id_rows:
            if r[0]:
                by_id.setdefault(r[0], []).append((r[0], r[1]))
        by_name: dict[str, list] = {}
        for r in name_rows:
            by_name.setdefault(r[1], []).append((r[0], r[1]))
        out: list[dict] = []
        seen: set[tuple] = set()
        for ref in names:
            id_hits = by_id.get(ref, [])
            if len(id_hits) > 1:
                # two nodes claiming one id — corruption, never a candidate.
                record_non_folded("Object", ref,
                                  display_holder_ids([h[0] for h in id_hits]),
                                  shape="duplicate-id")
                continue
            if id_hits:
                row = id_hits[0]
            else:
                hits = by_name.get(ref, [])
                if len(hits) > 1:
                    record_non_folded("Object", ref,
                                      display_holder_ids([h[0] for h in hits]))
                    continue
                if not hits:
                    continue
                row = hits[0]
            if row in seen:
                continue
            seen.add(row)
            out.append({"id": row[0], "name": row[1]})
        return out

    def excluded_exact_objects(names: list[str]) -> list[dict]:
        # #4061 R1: the DISTINGUISHING probe. ``exact_objects`` returns LIVE
        # rows only (#3317), so the resolver cannot tell "no exact match"
        # from "the exact match is excluded". This answers the latter with
        # the same name/id proximity, inverted onto the exclusion set — the
        # same constant, so the two can never drift.
        if not _RESOLVER_UNRESOLVABLE_OBJECT_STATUSES:
            return []
        rows = proj.g.query(
            "MATCH (o:Object) "
            "WHERE (o.name IN $names OR o.id IN $names) "
            "AND o.status IN $statuses "
            "RETURN o.id, o.name",
            params={"names": names, "statuses": sorted(
                _RESOLVER_UNRESOLVABLE_OBJECT_STATUSES)}).result_set
        return [{"id": r[0], "name": r[1]} for r in rows]

    def fts_objects(term: str, limit: int = 8) -> list[dict]:
        # returns [] when the index is absent/unreachable — ``run_fts_query``
        # degrades rather than raising, and ``resolve_subjects`` handles an
        # empty leg and a raising one identically (no FTS candidates).
        #
        # #3223: this leg IS the Object NAME-FTS index, so it runs THE FTS
        # leg (``search_engine.run_fts_query`` — the same function the SDK's
        # sparse leg uses) rather than the HYBRID ``tortoise_fts_query`` (RRF
        # of name-FTS + name-vector + structural). With the embedder live the
        # hybrid's vector half matches ANY term, so a term that names nothing
        # resolved to unrelated Objects and the R1 ``fired=False`` fallback
        # was unreachable in the shipped configuration.
        #
        # Recovering "just the name leg" from the hybrid does NOT work; three
        # shapes were measured and rejected:
        #   * returning the hybrid rows UNFILTERED — the #3223 divergence;
        #   * filtering them with a re-derived LEXICAL predicate — a predicate
        #     cannot reproduce the index's English stemmer, so it both DROPPED
        #     genuine matches ("happiness"/"happy" share the stem "happi",
        #     yet neither token prefixes the other) and ADMITTED non-matches
        #     (a naive "ing" strip reduces "rating" to "rat", re-admitting a
        #     semantic-only neighbour);
        #   * intersecting them with an id-filtered INDEX query — the hybrid's
        #     top-``limit`` is vector-weighted, so a genuine name match ranked
        #     outside the returned window was never in it to survive, and the
        #     leg returned [] for a term the index DOES match (measured).
        # Asking the index directly removes all three: the leg's candidates
        # ARE the index's rows.
        #
        # #4061 R2/R3: the port's deliberately NARROW ``{retracted}``
        # exclusion is pushed INTO that FTS query, before its LIMIT, so an
        # excluded Object cannot consume the leg's bound (the SDK's
        # all-or-nothing ``include_terminal`` opt-in could not express the
        # narrower set — which is what forced the old window-growth dance) —
        # and ``status`` is read by that same query, so the SDK's batch-content
        # degradation (which could drop ``status`` and let a retracted Object
        # read as live) cannot apply here at all.
        #
        # #3301: passing THIS port's own set rather than the read surface's
        # wider OBJECT vocabulary also preserves the decision it encodes — a
        # SUPERSEDED Object is still resolvable here (its state render IS the
        # answer to a current-state question); only ``retracted`` is excluded.
        from tortoise.search_engine import run_fts_query
        hits = run_fts_query(
            proj.g, term, entity_type="object", limit=limit,
            excluded_statuses=tuple(sorted(
                _RESOLVER_UNRESOLVABLE_OBJECT_STATUSES)))
        # ``run_fts_query`` already returns index order (score DESC, id ASC)
        # and omits nothing this port must re-admit; an id-less Object (the
        # #3019 MERGE-by-name shape) can never be a candidate, so it is
        # dropped rather than returned as the string "None".
        ids = [str(oid) for oid, _score in hits if oid]
        if not ids:
            return []
        rows = proj.g.query(
            "MATCH (o:Object) WHERE o.id IN $ids RETURN o.id, o.name",
            params={"ids": ids}).result_set
        names = {str(r[0]): (r[1] or "") for r in rows}
        return [{"id": oid, "name": names.get(oid, "")} for oid in ids]

    def alias_objects(term: str, limit: int = 8) -> list[dict]:
        tokens = [t for t in re.split(r"[^a-z0-9]+", term.lower())
                  if len(t) >= 3][:6]
        if not tokens:
            return []
        rows = proj.g.query(
            "MATCH (p:Point)-[:aboutObject]->(o:Object) "
            "WHERE p.search_keys IS NOT NULL AND "
            "ANY(t IN $tokens WHERE toLower(p.search_keys) CONTAINS t) "
            f"{status_filter}"
            "RETURN o.id, o.name, collect(p.id) LIMIT $limit",
            params={"tokens": tokens, "limit": limit}).result_set
        return [{"id": r[0], "name": r[1]} for r in rows]

    import types
    # SimpleNamespace — NOT a class body: class-local assignment would shadow
    # the enclosing closure names (the classic class-body scoping gotcha)
    return types.SimpleNamespace(exact_objects=exact_objects,
                                 excluded_exact_objects=excluded_exact_objects,
                                 fts_objects=fts_objects,
                                 alias_objects=alias_objects)


# ══════════════════════════════════════════════════════════════════════════
# #2165 Task 4 — typed walker + slice builder (R2/R3-8/R12, R17 P3-1/
# P3-6; P3-3: TWO Object-status vocabularies now exist in this file and they
# are NOT the same set — (a) the RESOLVE-time set
# (``_RESOLVER_UNRESOLVABLE_OBJECT_STATUSES`` = {retracted}, a deliberate
# NARROWING of the read-surface vocabulary
# ``commit_ops.OBJECT_TERMINAL_STATUSES`` (#3301); the resolver's legs
# apply it, see `docker_resolver_port`), and (b) the RENDER/probe-time set
# (``_RECALL_OBJECT_EXCLUDED_STATUSES``, five statuses including ``outdated``,
# used only by `_probe_visible_successors`). Neither is EVER applied to a
# resolved subject's OWN state row (the superseded couch's state IS the
# answer) nor to Points). One batched typed walk (never
# row-level N+1, never blind BFS): state slice
# (Object status/supersededBy/supersededAt + createdAt — the ObjectRegistered
# stamp #2654's duration-state entry date reads — in ONE statement), dated
# spine
# (aboutObject Points ∪ Event-aboutObject edges ∪ ask-lane eventId
# join), evidence view (points with validity + EP). Per-lane date ladder
# when → createdAt (sentinel-stripped) → eventId-joined startedAt (R2);
# parse-fail FALLS THROUGH the ladder (never undated while a usable date
# exists); undated is reserved for rows with NO usable date. As-of windows
# exclude rows dated after question_date (equality-day inclusive, UTC date
# truncation via the shared _norm_date helper).
# ══════════════════════════════════════════════════════════════════════════

# the v2-lane undated sentinel (tools/longmem_eval/ingest.UNDATED_SENTINEL —
# stable value, mirrored here so the pure module never imports tools)
_UNDATED_SENTINEL = "1970-01-01T00:00:00Z"
_TIER_ORDER = {"when": 0, "created": 1, "started": 2, "undated": 3}


@dataclass(frozen=True)
class AssemblySlices:
    """The walker's typed output — consumed by the Task-5 renderer."""

    state_rows: tuple = ()
    timeline_rows: tuple = ()
    evidence_rows: tuple = ()
    admission: dict = field(default_factory=dict)


class WalkerPort(Protocol):
    """Graph seam for the walker (dict-stubbed in unit tests; the docker
    adapter wraps a live projection)."""

    def state_rows(self, object_ids: list[str]) -> list[dict]: ...

    def spine_rows(self, object_ids: list[str],
                   per_subject_cap: int) -> list[dict]: ...

    # #2655: the equality-day probe (no subject anchor — the day IS the
    # scope). Returns a SUPERSET: the day filter is decided on the R2 ladder
    # date in ``collect_day_slices``, never by the port's predicate alone.
    def day_rows(self, days: tuple, per_day_cap: int) -> list[dict]: ...


def _norm_date(value) -> _date | None:
    """Shared date normalization — the SINGLE source for the as-of equality
    boundary AND the Task-5 ordering/diff arithmetic: parse (ISO datetime
    with Z, or YYYY-MM-DD) → sentinel-strip → UTC DATE truncation
    (date-only semantics: 2026-06-10T23:30:00Z → 2026-06-10). Returns None
    for absent/garbage/sentinel values — never raises."""
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        if "T" in s or " " in s:
            dt = s.replace("Z", "+00:00").replace(" ", "T")
            dt = _datetime.fromisoformat(dt)
            if dt.tzinfo is not None:
                # aware instants truncate in UTC — NEVER the machine-local
                # zone (a host-dependent day-boundary would scramble the
                # as-of equality boundary and Task-5 byte-goldens)
                dt = dt.astimezone(_UTC)
            d = dt.date()
        else:
            d = _date.fromisoformat(s[:10])
    except (ValueError, TypeError):
        return None
    # sentinel-strip: the v2-lane undated marker (and any pre-1970 epoch
    # artifact) is NOT a usable date
    if d <= _date(1970, 1, 1):
        return None
    return d


def _tier_and_date(row: dict) -> tuple[str, _date | None]:
    """R2 ladder for ONE spine row: when → createdAt → startedAt, each
    parse-gated; a malformed value FALLS THROUGH to the next tier (never
    undated while a usable date exists). Returns (tier, normalized date)."""
    for key, tier in (("when", "when"), ("created_at", "created"),
                      ("started_at", "started")):
        d = _norm_date(row.get(key))
        if d is not None:
            return tier, d
    return "undated", None


def collect_slices(port: WalkerPort, candidates: list[SubjectCandidate], *,
                   shape: AssemblyShape | None,
                   question_date=None,
                   per_subject_cap: int = PER_ENTITY_FANOUT_CAP) -> AssemblySlices:
    """One batched typed walk over the resolved subjects → typed slices.

    State rows come back in a SINGLE port.state_rows call (one statement —
    no torn supersession header under a concurrent #2242 fold); spine rows
    in one port.spine_rows call. Each spine row is date-tiered by the R2
    ladder and ordered: dated ascending (deterministic tiebreak: subject
    object_id, then row id), undated LAST. ``question_date`` (optional) is
    the as-of window: rows dated AFTER it are excluded (equality-day
    inclusive after UTC truncation); undated rows are always retained
    undated-last. Never raises on malformed stored dates (they fall through
    the ladder).
    """
    if not candidates:
        return AssemblySlices()
    # #5010: clamp ONCE, before the fetch AND before the accounting below —
    # the port now returns at most the adopted cap, so `rows_requested` and
    # the post-fetch `truncated` verdict must be derived from the SAME
    # effective bound the port used. (Deriving them from the unclamped request
    # silently dropped rows with `truncated=False` for any caller above the
    # cap — `resolve_limit_from_caps` accepts up to 10000.)
    per_subject_cap = bounded_fanout(per_subject_cap)
    object_ids = [c.object_id for c in candidates]
    state_rows = list(port.state_rows(object_ids) or [])
    spine_raw = list(port.spine_rows(object_ids, per_subject_cap) or [])

    timeline: list[dict] = []
    per_subject_counts: dict[str, int] = {}
    seen_rows: set[tuple[str, str]] = set()
    for row in spine_raw:
        oid = str(row.get("object_id") or "")
        # per-subject cap enforced post-fetch (single batched query)
        per_subject_counts[oid] = per_subject_counts.get(oid, 0) + 1
        if per_subject_counts[oid] > per_subject_cap:
            continue
        row = dict(row)
        # DEDUPE by (object_id, id): when both interval halves resolve to
        # the SAME object the resolver emits TWO same-oid candidates and the
        # per-candidate walk returns each row twice — the duplicates must
        # never double-count dates (interval anchoring) nor double-render
        rid = row.get("id")
        if rid is not None:
            key = (oid, str(rid))
            if key in seen_rows:
                continue
            seen_rows.add(key)
        tier, d = _tier_and_date(row)
        row["tier"] = tier
        row["date"] = d
        timeline.append(row)

    # as-of window: exclude rows dated AFTER question_date (undated kept)
    if question_date is not None:
        dq = _norm_date(question_date)
        if dq is not None:
            timeline = [r for r in timeline
                        if r["date"] is None or r["date"] <= dq]

    # CHRONOLOGICAL spine: date primary (undated -> _date.max -> naturally
    # LAST), deterministic tiebreak object_id then id (R17 P3-6)
    timeline.sort(key=lambda r: (r["date"] or _date.max,
                                 r.get("object_id", ""), r.get("id", "")))
    # evidence view = the point rows (validity + EP carried on the row)
    evidence_rows = [r for r in timeline if r.get("kind") == "point"]
    rows_requested = per_subject_cap * len(object_ids)
    truncated = any(per_subject_counts.get(oid, 0) >= per_subject_cap
                    for oid in object_ids)
    return AssemblySlices(
        state_rows=tuple(state_rows),
        timeline_rows=tuple(timeline),
        evidence_rows=tuple(evidence_rows),
        admission={"rows_requested": rows_requested,
                   "rows_admitted": len(timeline),
                   "truncated": truncated})


def collect_day_slices(port: WalkerPort, days,
                       *, per_day_cap: int = PER_ENTITY_FANOUT_CAP,
                       ) -> AssemblySlices:
    """#2655 — the as-of EQUALITY-DAY lookup over the typed spine.

    Same contract as ``collect_slices`` (ONE batched read, the R2 date ladder
    as the SINGLE date source, ``bounded_fanout`` clamp, an admission census),
    with the subject anchor replaced by the resolved DAY:

    * the port's ``day_rows`` read is a bounded SUPERSET probe (a date prefix
      is the only index-friendly predicate available); membership is decided
      HERE, on the ladder date, so a row whose truthy ``when`` shadows a
      matching ``createdAt`` is admitted on its OWN date or not at all — never
      by an or-chain that would re-derive a date the walker already resolved;
    * **undated rows are excluded outright** (unlike ``collect_slices``, which
      keeps them undated-last): an undated row can never satisfy a day
      equality, and keeping it would render an unrelated row under a
      ``DATE (…)`` header — a fabricated date claim;
    * the per-day cap is applied AFTER the membership filter, so a row dated
      elsewhere (a port over-fetch) cannot consume the day's budget.

    Deterministic order: ``(date, kind, id)`` — input-order independent.
    """
    day_set = {d for d in days if isinstance(d, _date)}
    if not day_set:
        return AssemblySlices()
    cap = bounded_fanout(per_day_cap)
    ordered_days = tuple(sorted(day_set))
    raw = list(port.day_rows(ordered_days, cap) or [])

    rows: list[dict] = []
    seen: set[str] = set()
    per_day_counts: dict[_date, int] = {}
    for row in raw:
        row = dict(row)
        rid = row.get("id")
        if rid is not None:
            key = str(rid)
            if key in seen:
                continue
            seen.add(key)
        tier, d = _tier_and_date(row)
        if d is None or d not in day_set:
            continue
        per_day_counts[d] = per_day_counts.get(d, 0) + 1
        if per_day_counts[d] > cap:
            continue
        row["tier"] = tier
        row["date"] = d
        rows.append(row)

    # (date, kind, id) — date-major, then the row kind, then the id: the same
    # TOTAL key the subject sections use, extended with the kind so a Point
    # and an Event sharing an id can never swap places between runs.
    rows.sort(key=lambda r: (r["date"], _as_str(r.get("kind")),
                             _as_str(r.get("id"))))
    return AssemblySlices(
        state_rows=(),
        timeline_rows=tuple(rows),
        evidence_rows=tuple(r for r in rows if r.get("kind") == "point"),
        admission={"rows_requested": cap * len(ordered_days),
                   "rows_admitted": len(rows),
                   "truncated": any(v >= cap
                                    for v in per_day_counts.values())})


def _window_precision(valid_from, valid_to, valid_from_precision,
                      valid_to_precision) -> dict:
    """#2350 (E-prec): the additive precision block for an assembly row.

    ONE import point so every assembly read path resolves the 3-state end
    through the same home the search decoration and
    ``get_historical_knowledge`` use.
    """
    from tortoise.validity import window_read_fields
    return window_read_fields(valid_from, valid_to,
                              valid_from_precision, valid_to_precision)


def docker_walker_port(sdk) -> WalkerPort:
    """Adapter over a live TortoiseSDK: the state read is ONE batched
    Cypher statement (status/supersededBy/supersededAt together — no torn
    header under a concurrent fold); the spine is ONE batched points+events
    walk per subject set (never N+1)."""
    proj = sdk._get_proj()

    def state_rows(object_ids: list[str]) -> list[dict]:
        # #3317 decision: this read is NOT a resolution leg — it is the state
        # read for ids the RESOLVER already admitted (and the WalkerPort
        # contract for a caller passing an explicit id). It therefore carries
        # NO status conjunct: ``o.status`` is read VERBATIM so an admitted
        # subject renders its own truth ("STATE (couch): superseded by sofa",
        # "STATE (x): deprecated"). Filtering here would not prevent a
        # retracted Object from resolving (resolution already happened) — it
        # would only DELETE the honest state line of an admitted subject,
        # silently hiding status. The unresolvable-status guard belongs
        # upstream, at the legs — see docker_resolver_port.
        rows = proj.g.query(
            "MATCH (o:Object) WHERE o.id IN $ids "
            "RETURN o.id, o.name, o.status, o.supersededBy, o.supersededAt, "
            "o.createdAt",
            params={"ids": object_ids}).result_set
        # #2654: ``createdAt`` is the ObjectRegistered stamp (the projection's
        # ``coalesce($ca, $now)``) — additive, and the state-entry candidate
        # the duration-state anchor reads (``_state_entry_date``, which takes
        # it only as the EARLIER of {stamp, earliest dated row}).
        return [{"object_id": r[0], "name": r[1], "status": r[2],
                 "superseded_by": r[3], "superseded_at": r[4],
                 "created_at": r[5]} for r in rows]

    def spine_rows(object_ids: list[str],
                   per_subject_cap: int = PER_ENTITY_FANOUT_CAP) -> list[dict]:
        # Deterministic + PER-SUBJECT-FAIR: one ORDER BY id LIMIT query per
        # subject per kind (bounded: 2 kinds x len(subjects) — never per-row
        # N+1). A shared LIMIT over the subject set would let a hub starve a
        # co-subject and make the surviving rows engine-order-dependent.
        # #5010: the adopted fan-out cap (STORAGE-ARCHITECTURE.md §11.5) is a
        # hard per-entity ceiling, so a caller may LOWER this bound but not
        # raise it past the cap.
        if not object_ids:
            return []
        cap = bounded_fanout(per_subject_cap)
        out: list[dict] = []
        for oid in object_ids:
            prow = proj.g.query(
                "MATCH (o:Object {id:$oid})<-[:aboutObject]-(p:Point) "
                "RETURN 'point' AS kind, p.id, p.content, p.when, "
                "p.createdAt, p.status, p.validFrom, p.validTo, "
                "p.expiredAt, p.ep_alpha, p.ep_beta, p.quote, "
                "p.search_keys, p.eventId, p.lme_session_index, "
                "p.validFromPrecision, p.validToPrecision "
                "ORDER BY p.id LIMIT $cap",
                params={"oid": oid, "cap": cap}).result_set
            for r in prow:
                # validTo/expiredAt mirror the FTS-hit shape (validity-window
                # marker parity — P1-2: [valid X -> Y] vs a misleading
                # open-ended [valid since X] on the fired render)
                out.append({"object_id": oid, "kind": r[0], "id": r[1],
                            "content": r[2], "when": r[3],
                            "created_at": r[4], "status": r[5],
                            "valid_from": r[6], "valid_to": r[7],
                            "expired_at": r[8], "ep_alpha": r[9],
                            "ep_beta": r[10], "quote": r[11],
                            "search_keys": r[12], "event_id": r[13],
                            "lme_session_index": r[14],
                            # #2350: the precision block (additive keys).
                            **_window_precision(r[6], r[7], r[15], r[16])})
            erow = proj.g.query(
                "MATCH (o:Object {id:$oid})<-[:aboutObject]-(e:Event) "
                "RETURN 'event' AS kind, e.eventId, e.name, e.startedAt, "
                "e.status, e.lme_event_id, e.lme_session_index "
                "ORDER BY e.eventId LIMIT $cap",
                params={"oid": oid, "cap": cap}).result_set
            for r in erow:
                # the Event node stores its human text under `name`
                # (create_event's first arg) — NOT `content`
                out.append({"object_id": oid, "kind": r[0], "id": r[1],
                            "content": r[2], "started_at": r[3],
                            "status": r[4], "lme_event_id": r[5],
                            "lme_session_index": r[6]})
        return out

    def day_rows(days: tuple, per_day_cap: int = PER_ENTITY_FANOUT_CAP,
                 ) -> list[dict]:
        # #2655: the equality-day probe. ONE prefix-range statement per day
        # per kind (bounded: 2 kinds x len(days) — never row-level N+1).
        #
        # The predicate is a SUPERSET: `when` and `createdAt` are both probed
        # with `STARTS WITH <day>` because the R2 ladder makes `when`
        # authoritative when it parses — so a row whose `when` says another day
        # IS returned here and is then dropped by `collect_day_slices` on its
        # ladder date. Pushing a single conjunct down instead would silently
        # DROP a genuine day row whose `when` is unparseable but whose
        # `createdAt` is the day (a false negative the ladder exists to
        # prevent). `STARTS WITH` on a null property yields null (falsy) —
        # undated rows are never matched.
        #
        # ORDER BY id mirrors `spine_rows` (deterministic, per-kind) — the
        # collector re-sorts on (date, kind, id), so the returned order is an
        # implementation detail, not the contract.
        if not days:
            return []
        cap = bounded_fanout(per_day_cap)
        out: list[dict] = []
        for d in days:
            day = d.isoformat()
            prow = proj.g.query(
                "MATCH (p:Point) "
                "WHERE p.when STARTS WITH $day "
                "OR p.createdAt STARTS WITH $day "
                "RETURN 'point' AS kind, p.id, p.content, p.when, "
                "p.createdAt, p.status, p.validFrom, p.validTo, "
                "p.expiredAt, p.ep_alpha, p.ep_beta, p.quote, "
                "p.search_keys, p.eventId, p.lme_session_index, "
                "p.validFromPrecision, p.validToPrecision "
                "ORDER BY p.id LIMIT $cap",
                params={"day": day, "cap": cap}).result_set
            for r in prow:
                out.append({"kind": r[0], "id": r[1], "content": r[2],
                            "when": r[3], "created_at": r[4],
                            "status": r[5], "valid_from": r[6],
                            "valid_to": r[7], "expired_at": r[8],
                            "ep_alpha": r[9], "ep_beta": r[10],
                            "quote": r[11], "search_keys": r[12],
                            "event_id": r[13], "lme_session_index": r[14],
                            **_window_precision(r[6], r[7], r[15], r[16])})
            erow = proj.g.query(
                "MATCH (e:Event) "
                "WHERE e.startedAt STARTS WITH $day "
                "OR e.when STARTS WITH $day "
                "RETURN 'event' AS kind, e.eventId, e.name, e.startedAt, "
                "e.status, e.lme_event_id, e.lme_session_index "
                "ORDER BY e.eventId LIMIT $cap",
                params={"day": day, "cap": cap}).result_set
            for r in erow:
                # the Event node stores its human text under `name` (mirrors
                # ``spine_rows`` — never `content`)
                out.append({"kind": r[0], "id": r[1], "content": r[2],
                            "started_at": r[3], "status": r[4],
                            "lme_event_id": r[5], "lme_session_index": r[6]})
        return out

    def window_rows(lo, hi, cap=PER_ENTITY_FANOUT_CAP) -> list[dict]:
        """#2652: the TIME-WINDOW walk — every dated Point/Event whose own
        recorded date lands in ``[lo, hi]``, regardless of Object
        anchoring (an 'ago' question's selector is the time, not an
        entity).

        TWO batched statements (points, events) — never N+1. The Cypher
        predicate is a COARSE pre-filter widened by a day: the three date
        fields are free text (a malformed ``when`` is possible, and the
        v2 lane leaves ``when`` sparse), so the AUTHORITATIVE window test
        is the R2 ladder + ``_norm_date`` in ``collect_window_slices``.
        Because the predicate ORs over ``when`` and ``createdAt``, a row
        with a junk ``when`` but a real ``createdAt`` is still fetched.
        ORDER BY the DENSE date field (``createdAt``) DESC is only the FETCH
        priority — it keeps the newest rows when the cap binds, which is what
        ANY window whose answer locus is the most recent end needs (recency,
        and the count form: both render newest-first). It is deliberately a single-property sort with
        NO ``coalesce`` (cheaper, and one less function in an unindexed
        full-scan query); the authoritative window filter AND the rendered
        order both come from Python (the R2 ladder).
        """
        cap = bounded_fanout(cap)
        lo_s = (lo - _timedelta(days=1)).isoformat() if lo else None
        hi_s = (hi + _timedelta(days=1)).isoformat() if hi else None
        out: list[dict] = []
        pclauses = []
        pparams: dict = {"cap": cap}
        for fld in ("when", "createdAt"):
            parts = []
            if lo_s is not None:
                parts.append(f"p.{fld} >= $lo")
            if hi_s is not None:
                parts.append(f"p.{fld} <= $hi")
            if parts:
                pclauses.append("(" + " AND ".join(parts) + ")")
        if lo_s is not None:
            pparams["lo"] = lo_s
        if hi_s is not None:
            pparams["hi"] = hi_s
        if pclauses:
            prow = proj.g.query(
                "MATCH (p:Point) WHERE " + " OR ".join(pclauses)
                + " RETURN 'point' AS kind, p.id, p.content, p.when, "
                  "p.createdAt, p.status, p.validFrom, p.validTo, "
                  "p.expiredAt, p.ep_alpha, p.ep_beta, p.quote, "
                  "p.search_keys, p.eventId, p.lme_session_index, "
                  "p.validFromPrecision, p.validToPrecision "
                  "ORDER BY p.createdAt DESC, p.id "
                  "LIMIT $cap",
                params=pparams).result_set
            for r in prow:
                out.append({"object_id": "", "kind": r[0], "id": r[1],
                            "content": r[2], "when": r[3],
                            "created_at": r[4], "status": r[5],
                            "valid_from": r[6], "valid_to": r[7],
                            "expired_at": r[8], "ep_alpha": r[9],
                            "ep_beta": r[10], "quote": r[11],
                            "search_keys": r[12], "event_id": r[13],
                            "lme_session_index": r[14],
                            **_window_precision(r[6], r[7], r[15], r[16])})
        eclauses = []
        eparams: dict = {"cap": cap}
        if lo_s is not None:
            eclauses.append("e.startedAt >= $lo")
            eparams["lo"] = lo_s
        if hi_s is not None:
            eclauses.append("e.startedAt <= $hi")
            eparams["hi"] = hi_s
        if eclauses:
            erow = proj.g.query(
                "MATCH (e:Event) WHERE " + " AND ".join(eclauses)
                + " RETURN 'event' AS kind, e.eventId, e.name, "
                  "e.startedAt, e.status, e.lme_event_id, "
                  "e.lme_session_index "
                  "ORDER BY e.startedAt DESC, e.eventId LIMIT $cap",
                params=eparams).result_set
            for r in erow:
                out.append({"object_id": "", "kind": r[0], "id": r[1],
                            "content": r[2], "started_at": r[3],
                            "status": r[4], "lme_event_id": r[5],
                            "lme_session_index": r[6]})
        return out

    import types
    return types.SimpleNamespace(state_rows=state_rows,
                                 spine_rows=spine_rows,
                                 day_rows=day_rows,
                                 window_rows=window_rows)


# ══════════════════════════════════════════════════════════════════════════
# #2165 Task 5 — renderer: synthesized hits + deterministic ordering/diff
# (R1/R2/R3-5/R6/R12, R17 P2-1). Slices -> ordinary annotated hit dicts the
# UNCHANGED assemble_context/_render_block render (Task 6's byte-parity
# invariant). CONTRACT (pinned here — Task 6 captures the byte-goldens):
#   * state/section labels EMBED in hit content ("STATE (couch): superseded
#     by sofa on 2026-09-01" / "STATE (bike #obj-b): live") and so do the
#     #2654 duration-state anchors ("STATE ENTRY (couch): 2026-08-10" +
#     "31 days from couch state entry (2026-08-10) to the as-of date
#     (2026-09-10)"); synthesized
#     rows carry NO `id` (why.enrich skips them) and lme_session_index-less
#     -> _render_block prefixes "[session ?]".
#   * superseded_by is DICT-shaped ({"content_snippet": name-or-snippet});
#     empty supersededBy -> {"content_snippet": ""} (the fired path passes
#     [] to the D8 gate at Task 6 — never flips retrieval_degraded).
#   * successor-absent (verified-empty) and torn rows render NAME-ONLY
#     annotations — a successor is never fabricated into a date/evidence
#     line; a >200-char successor name is TRUNCATED FOR DISPLAY only (the
#     probe/verification key is always the stored FULL name, #5370).
#   * real rows pass through unchanged minus the pure walker derivation
#     keys {date, tier} (no point_id — W4-OUTPUT-only).
#   * per-subject sectioning (R12/C7): subject-major line blocks in
#     candidate order, rows chronological within a section; a point
#     anchored to two subjects renders once PER section (not globally).
#   * ordering/diff arithmetic uses the SAME _norm_date helper as the as-of
#     boundary (second-model P2-3) — one date source everywhere.
# ══════════════════════════════════════════════════════════════════════════

# Display-only bound on the successor name in the rendered STATE line. The
# VERIFICATION key is always the stored FULL name (successors_verified holds
# full names) — truncating before the membership test made a >200-char
# successor render "no successor record found" (#5370).
_MAX_SUCC_NAME = 200


def _fmt_date(d: _date) -> str:
    return d.isoformat() if d is not None else ""


def _as_str(v) -> str:
    """Never-raise string coercion: a non-str value on a hand-built slice
    (AssemblySlices is a public pure type) must not crash the renderer nor
    leak ``b'...'`` reprs into the reader text."""
    if isinstance(v, str):
        return v
    return "" if v is None else str(v)


def _subject_names(slices: AssemblySlices) -> dict[str, str]:
    """object_id -> display name from the state slice."""
    names: dict[str, str] = {}
    for sr in slices.state_rows:
        oid = sr.get("object_id")
        if oid and not names.get(oid):
            names[oid] = _as_str(sr.get("name")).strip() or "(unnamed)"
    return names


def _display_label(name: str, oid: str, colliding: bool) -> str:
    """Section/state label: bare name, or name + '#<oid>' when two
    same-named entities share this assembly (R12/C7 disambiguation)."""
    return f"{name} #{oid}" if colliding else name


def _oid_rows(slices: AssemblySlices, oid: str) -> list[dict]:
    return [r for r in slices.timeline_rows
            if r.get("object_id") == oid]


def _subject_order(slices: AssemblySlices,
                   candidates: tuple | list) -> list[tuple[str, str]]:
    """Ordered (object_id, name) list: candidate order when supplied (else
    state-row order, then spine-only oids)."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    names = _subject_names(slices)
    for c in candidates:
        oid = c.object_id
        if oid in seen:
            continue
        seen.add(oid)
        if _oid_rows(slices, oid) or oid in names:
            out.append((oid, (c.name or "").strip() or names.get(oid,
                        "(unnamed)")))
    for sr in slices.state_rows:
        oid = sr.get("object_id")
        if oid and oid not in seen:
            seen.add(oid)
            out.append((oid, names.get(oid, "(unnamed)")))
    for r in slices.timeline_rows:
        oid = r.get("object_id")
        if oid and oid not in seen:
            seen.add(oid)
            out.append((oid, names.get(oid, "(unnamed)")))
    return out


def _dated_instances(rows: list[dict]) -> list[_date]:
    """Per-ROW normalized dates over the rows, read from the walker's OWN
    tier date (the R2 ladder already ran inside collect_slices — when ->
    createdAt -> startedAt with parse FALL-THROUGH, UTC truncation). NEVER
    re-derive here: an or-chain would silently drop a row whose truthy but
    unparseable `when` shadows a valid createdAt (P1: silent wrong math)."""
    out: list[_date] = []
    for r in rows:
        d = r.get("date")
        if isinstance(d, _date):
            out.append(d)
    return out


def _date_anchors(rows: list[dict]) -> tuple[_date | None, _date | None]:
    """(earliest, latest) dated row dates — identical date semantics to the
    walker's tier sort (one date source everywhere)."""
    ds = _dated_instances(rows)
    return (min(ds), max(ds)) if ds else (None, None)


def _state_entry_date(slices: AssemblySlices, oid: str) -> _date | None:
    """#2654 — the subject's STATE-ENTRY date: the ObjectRegistered stamp
    (the Object's ``createdAt``, read into the state slice by the walker) or
    the earliest DATED row recorded about the subject (the status-start
    fallback) — whichever is EARLIER. None when neither is usable.

    Why the earlier of the two rather than the registration stamp alone: in
    the v2 eval lane the stamp is the INGEST clock (the extractor registers
    entities with no ``createdAt`` prop, so the projection writes ``now``),
    while the row dates are the session dates the evidence actually carries
    — trusting the stamp alone would date every state to the ingest RUN and
    answer every duration question with the run's age. Both are "when this
    state is known to have begun"; the earlier one is the one the graph can
    defend. The as-of window is still the caller's ``question_date`` (the
    walker already dropped rows dated after it), so this helper cannot
    return a future-dated entry.
    """
    ds: list[_date] = []
    for sr in slices.state_rows:
        if sr.get("object_id") == oid:
            d = _norm_date(sr.get("created_at"))
            if d is not None:
                ds.append(d)
    ds.extend(_dated_instances(_oid_rows(slices, oid)))
    return min(ds) if ds else None


def _state_header_hit(sr: dict, label: str,
                      successors_verified: frozenset[str],
                      *, question_date: str | None = None) -> dict:
    status = _as_str(sr.get("status")).strip()
    # #5370: successors_verified is keyed on the stored FULL successor name
    # (the probe's name set is the raw supersededBy values). Truncate for
    # DISPLAY only — doing it before the membership test made a >200-char
    # successor look unverified and the renderer claimed "no successor
    # record found" for a successor that exists and is live.
    succ_full = _as_str(sr.get("superseded_by")).strip()
    succ = succ_full
    if len(succ) > _MAX_SUCC_NAME:
        succ = succ[: _MAX_SUCC_NAME] + "…"
    date = _norm_date(sr.get("superseded_at"))
    qd = _norm_date(question_date) if question_date else None
    if (status == "superseded" and qd is not None and date is not None
            and date > qd):
        # P2-4: as-of BEFORE the supersession date — the subject was NOT yet
        # superseded at question_date; render the then-state (live), never a
        # supersession dated AFTER the reader's window
        status = "live"
        succ = ""
        date = None
    if status == "superseded":
        if not succ:
            text = f"STATE ({label}): superseded (successor unknown)"
            sb = {"content_snippet": ""}
        elif succ_full in successors_verified:
            on = f" on {_fmt_date(date)}" if date else ""
            text = f"STATE ({label}): superseded by {succ}{on}"
            sb = {"content_snippet": succ}
        else:
            # name-only annotation — the successor resolved to zero visible
            # nodes (never created / recall-excluded / the fired path's
            # probe found nothing); NEVER fabricate a date/evidence line
            text = (f"STATE ({label}): superseded by {succ} — "
                    f"no successor record found")
            sb = {"content_snippet": ""}
    else:
        text = f"STATE ({label}): {status or 'live'}"
        sb = {"content_snippet": ""}
    return {"content": text, "kind": "state", "status": status,
            "superseded_by": sb}


def _date_section_hits(slices: AssemblySlices,
                       anchor) -> list[dict]:
    """#2655 — the date-scoped block: ONE synthesized header naming the
    RESOLVED day(s), then every admitted row of that day in the walker's own
    date order.

    There is no resolved subject to head a section (the day IS the scope), so
    the subject-section loop does not run here. Rows are cleaned of the
    pure-walker derivation keys ({date, tier}) exactly as the subject sections
    do; the header is the ONLY line this renderer invents. Empty slices render
    NOTHING (the caller's P1-1 gate then keeps the fired decision false — a
    bare header would replace a working legacy pool with a dateline).
    """
    if anchor is None or not slices.timeline_rows:
        return []
    days = ", ".join(d.isoformat() for d in anchor.days)
    rows = sorted(slices.timeline_rows,
                  key=lambda r: (r.get("date") or _date.max,
                                 _as_str(r.get("kind")),
                                 _as_str(r.get("id"))))
    hits: list[dict] = [{"content": f"DATE ({anchor.label}): {days}",
                         "kind": "date"}]
    seen: set[str] = set()
    for r in rows:
        rid = r.get("id")
        if rid is not None:
            key = str(rid)
            if key in seen:
                continue
            seen.add(key)
        hits.append({k: v for k, v in r.items() if k not in ("date", "tier")})
    return hits


def _ago_offset_line(d: _date, qd: _date, unit: str | None) -> str:
    """#2652 deterministic elapsed arithmetic: ``<date> — N <unit> before
    <question_date>``. PURE INTEGER DATE MATH via ``_norm_date`` (the same
    date source as the as-of boundary and the ordering/diff synthesis) —
    never a fabricated date, never a model call.
    """
    days = (qd - d).days
    per = {"day": 1, "week": 7}.get(unit)
    if per:
        if per == 1:
            phrase = f"{days} day{'s' if days != 1 else ''}"
        else:
            phrase = (f"{days} days (~{days // per} "
                      f"{unit}{'s' if days // per != 1 else ''})")
    else:
        phrase = f"{days} days"
    return f"{_fmt_date(d)} — {phrase} before {_fmt_date(qd)}"


def _synthesize_ago_hits(slices: AssemblySlices,
                         anchor, question_date: str | None,
                         ) -> list[dict]:
    """#2652 AGO_RELATIVE render: ONE window header + the dated spine, each
    dated row preceded by its own deterministic elapsed line (so the reader
    gets the offset arithmetic the deterministic tier owns).

    Returns [] when the window holds NO dated row — the caller's empty-block
    guard then falls back to legacy byte-identically, instead of replacing a
    working pool with a content-less window header (the #2165 P1-1 rule).
    """
    qd = _norm_date(question_date)
    rows = [r for r in slices.timeline_rows
            if isinstance(r.get("date"), _date)]
    if not rows or qd is None:
        return []
    lo = anchor.window_lo
    hi = anchor.window_hi
    if anchor.kind == "ago-offset":
        label = (f"WINDOW ({_fmt_date(lo)} -> {_fmt_date(hi)}): "
                 f"{anchor.count} {anchor.unit}"
                 f"{'s' if (anchor.count or 0) != 1 else ''} before "
                 f"{_fmt_date(qd)}")
    elif anchor.kind == "recency":
        label = (f"WINDOW ({_fmt_date(lo)} -> {_fmt_date(hi)}): "
                 f"most recent activity before {_fmt_date(qd)}")
    else:
        label = (f"WINDOW ({_fmt_date(lo)} -> {_fmt_date(hi)}): "
                 f"elapsed time before {_fmt_date(qd)}")
    hits: list[dict] = [{"content": label, "kind": "time-window"}]
    for r in rows:
        hits.append({"content": _ago_offset_line(r["date"], qd,
                                                 anchor.unit),
                     "kind": "ago-offset"})
        hits.append({k: v for k, v in r.items()
                     if k not in ("date", "tier")})
    return hits


def _class_header_line(window: ClassWindow, rendered: int,
                       qd: _date) -> str:
    """#8056 CLASS ORDER header — ONE builder, TWO call sites.

    ``_synthesize_class_hits`` renders it against the selected row count, and
    ``_assemble_class_plural`` RE-derives it after the shared cap tail
    (``_assemble_under_caps`` -> ``assemble_context``), whose skip-not-starve
    drop can remove later position/row hits the renderer never saw. One
    builder is what keeps the header's claimed membership equal to the rows
    the block actually lists (#8779 F1).
    """
    count = window.count or 1
    unit = window.unit or ""
    # #8779 P1: the direction phrase is the question's OWN request, read by
    # ``_class_order_newest_first`` — never a hard-coded `earliest first`.
    direction = "newest first" if window.newest_first else "earliest first"
    return (
        f"CLASS ORDER ({window.class_phrase}): {rendered} dated "
        f"event{'s' if rendered != 1 else ''} in "
        f"{_fmt_date(window.window_lo)} -> {_fmt_date(window.window_hi)} "
        f"({count} {unit}{'s' if count != 1 else ''} before "
        f"{_fmt_date(qd)}), {direction}")


def _synthesize_class_hits(slices: AssemblySlices, window: ClassWindow,
                           question_date: str | None) -> list[dict]:
    """#8056 CLASS_PLURAL render: ONE header naming the class and the window,
    then the class's dated rows in the order the question ASKED for —
    chronological, or reverse-chronological when its ordering instruction says
    so (#8779 P1) — each preceded by its own position line — the
    deterministic tier owns the ordering arithmetic the question asks for.

    Same-DAY rows SHARE a position ("1. 2026-07-04" twice), so a tie is never
    rendered as a strict order — the module's existing tie discipline (the
    N-ary path's "both appeared on", the binary path's same-day phrase).
    Because rows inside the walk are already dated (undated rows are excluded
    by the walker), no member can be missing: an unorderable row is not
    admitted in the first place, never silently dropped from a claimed order.

    Returns [] when there is no dated row or no resolved window — the
    caller's empty-block guard then falls back to legacy byte-identically
    instead of replacing a working pool with a content-less header (#2165
    P1-1).
    """
    if window is None or not slices.timeline_rows:
        return []
    qd = _norm_date(question_date)
    if qd is None or window.window_lo is None or window.window_hi is None:
        return []
    rows = sorted((r for r in slices.timeline_rows
                   if isinstance(r.get("date"), _date)),
                  key=lambda r: (r["date"], _as_str(r.get("id"))),
                  reverse=window.newest_first)
    # dedupe BEFORE the header is built, so the count it names is the count
    # this renderer INTENDS to list (the collector already dedupes its own
    # fetch; a hand-built AssemblySlices may not have). The FIRED path
    # re-derives the count from the surviving rows after the cap tail — see
    # ``_assemble_class_plural`` (#8779 F1).
    seen: set[str] = set()
    dated: list[dict] = []
    for r in rows:
        rid = r.get("id")
        if rid is not None:
            key = str(rid)
            if key in seen:
                continue
            seen.add(key)
        dated.append(r)
    if not dated:
        return []
    hits: list[dict] = [{
        "content": _class_header_line(window, len(dated), qd),
        "kind": "class-window"}]
    position = 1
    previous: _date | None = None
    for index, r in enumerate(dated, 1):
        if previous is not None and r["date"] != previous:
            position = index
        previous = r["date"]
        hits.append({"content": f"{position}. {_fmt_date(r['date'])}",
                     "kind": "class-order"})
        hits.append({k: v for k, v in r.items() if k not in ("date", "tier")})
    return hits


def synthesize_hits(
    slices: AssemblySlices, *, shape: AssemblyShape,
    candidates: tuple | list = (), halves: tuple | list = (),
    successors_verified: frozenset[str] = frozenset(),
    question_date: str | None = None,
    date_anchor=None,
    anchor=None,
) -> list[dict]:
    """Render slices -> annotated hit dicts (the assemble_context input).

    Deterministic: subject-major sections in candidate order, rows
    chronological within a section; state/ordering/interval lines computed
    through the SHARED _norm_date helper (never an independent date lib).
    For ``RELATIVE_DATE`` the day's own block is rendered (#2655) — see
    ``_date_section_hits``.

    #2652: ``AGO_RELATIVE`` has NO subject sections (the selector is the
    time window) — it renders through ``_synthesize_ago_hits`` and needs
    ``anchor``; without it the shape renders nothing (the caller's
    empty-block guard then falls back to legacy). #8056 adds
    ``CLASS_PLURAL``, which has no subject sections either: it renders the
    CLASS's own dated spine through ``_synthesize_class_hits`` and needs
    ``anchor`` (the resolved ``ClassWindow``) the same way.
    """
    if shape is AssemblyShape.RELATIVE_DATE:
        return _date_section_hits(slices, date_anchor)
    if shape is AssemblyShape.CLASS_PLURAL:
        return _synthesize_class_hits(slices, anchor, question_date)
    if shape is AssemblyShape.AGO_RELATIVE:
        if anchor is None:
            return []
        return _synthesize_ago_hits(slices, anchor, question_date)
    cands = list(candidates)
    order = _subject_order(slices, cands)
    # colliding names -> disambiguate every label for those names
    by_name_rows: dict[str, int] = {}
    for _oid, name in order:
        by_name_rows[name] = by_name_rows.get(name, 0) + 1
    collide: set[str] = {n for n, k in by_name_rows.items() if k > 1}

    state_by_oid = {sr.get("object_id"): sr for sr in slices.state_rows}
    hits: list[dict] = []
    if shape is AssemblyShape.INTERVAL:
        # Interval anchoring (P1-1 review): the window is computed PER
        # SUBJECT, never as a global min..max over every dated row of the
        # assembly (an accumulating object would silently inflate the span
        # with unrelated later rows).
        #   * ONE subject in the assembly (both halves name the SAME object
        #     — e.g. "buying the couch"/"selling the couch"): the story span
        #     (earliest..latest dated row) is the answer ONLY when the
        #     subject has exactly TWO dated rows (the anchor pair). More
        #     dated rows mean the anchors cannot be identified -> SUPPRESS
        #     (never a confidently-worded wrong integer).
        #   * TWO distinct subjects: first-known (earliest) date per subject
        #     — a "between X and Y" window is anchored on each subject's
        #     earliest dated row; suppress when either is undated.
        if len(order) == 1:
            ds = _dated_instances(_oid_rows(slices, order[0][0]))
            days = ((ds[1] - ds[0]).days
                    if len(ds) == 2 and ds[1] > ds[0] else None)
        else:
            lo_a, _ = _date_anchors(_oid_rows(slices, order[0][0]))
            lo_b, _ = _date_anchors(_oid_rows(slices, order[1][0]))
            days = abs((lo_b - lo_a).days) if lo_a and lo_b else None
        if days is not None:
            halves_l = [h for h in halves if h and _as_str(h).strip()]
            if len(halves_l) >= 2:
                phrase = (f"{days} days between {_as_str(halves_l[0]).strip()} "
                          f"and {_as_str(halves_l[1]).strip()}")
            elif len(order) >= 2:
                l0 = _display_label(order[0][1], order[0][0],
                                    order[0][1] in collide)
                l1 = _display_label(order[1][1], order[1][0],
                                    order[1][1] in collide)
                phrase = f"{days} days between {l0} and {l1}"
            else:
                phrase = f"{days} days between the events described"
            hits.append({"content": phrase, "kind": "interval"})
    elif shape is AssemblyShape.ORDERING:
        dated: list[tuple[str, str, _date, int]] = []
        for i, (oid, name) in enumerate(order):
            lo, _hi = _date_anchors(_oid_rows(slices, oid))
            if lo is not None:
                dated.append((oid, name, lo, i))
        dated.sort(key=lambda t: (t[2], t[3]))
        # N-ary (#2653): the questions' OWN term count decides the arm, so the
        # two-option path below stays byte-identical (its goldens are pinned).
        n_terms = len(halves) if halves else len(order)
        if n_terms >= 3 and len(order) >= 3:
            # Chain over the SAME dated spine (earliest dated row per
            # subject — the binary path's own anchor). The FULL order is
            # claimed ONLY when EVERY subject is dated: a chain missing a
            # member would state a confidently wrong order, so it emits
            # nothing and the per-subject sections below carry the evidence
            # (same fail-closed polarity as the interval anchors).
            chron = [(oid, name, lo) for oid, name, lo, _i in dated]
            if len(chron) == len(order) and len(chron) >= 2:
                by_day: dict[_date, list[tuple[str, str]]] = {}
                for oid, name, d in chron:
                    by_day.setdefault(d, []).append((oid, name))
                pieces: list[str] = []
                for gi, d in enumerate(sorted(by_day)):
                    # within-day ties are ordered by OBJECT ID — the same
                    # phrasing-independent canon as the binary tie phrase
                    group = sorted(by_day[d], key=lambda t: t[0])
                    labels = [_display_label(nm, oid, nm in collide)
                              for oid, nm in group]
                    joined = " and ".join(labels)
                    if gi == 0:
                        pieces.append(
                            f"{joined} came first on {_fmt_date(d)}"
                            if len(labels) == 1 else
                            f"{joined} both appeared on {_fmt_date(d)}")
                    else:
                        pieces.append(f"then {joined} on {_fmt_date(d)}")
                if pieces:
                    hits.append({"content": ", ".join(pieces),
                                 "kind": "ordering"})
        elif len(dated) >= 2:
            da = dated[0][2]
            if dated[1][2] == da:
                # same-day tie: claiming a word-order-dependent winner would
                # be misleading (P2-1 review) — emit the tie phrase in the
                # CANONICAL object-id order (independent of question phrasing
                # AND candidate order — a subject shuffle yields zero delta)
                tie = sorted((dated[0], dated[1]), key=lambda t: t[0])
                l0 = _display_label(tie[0][1], tie[0][0],
                                    tie[0][1] in collide)
                l1 = _display_label(tie[1][1], tie[1][0],
                                    tie[1][1] in collide)
                hits.append({"content": f"{l0} and {l1} both appeared on "
                                        f"{_fmt_date(da)}",
                             "kind": "ordering"})
            else:
                oid_a, name_a, da, _i = dated[0]
                label = _display_label(name_a, oid_a, name_a in collide)
                hits.append({"content": f"{label} came first on "
                                        f"{_fmt_date(da)}",
                             "kind": "ordering"})
    elif shape is AssemblyShape.CURRENT_STATE:
        # headers follow the SAME candidate-ordered subject sequence as the
        # sections below — never the raw state_rows order (the docker state
        # query has no ORDER BY; P2-1 determinism)
        for oid, name in order:
            sr = state_by_oid.get(oid)
            if sr is None:
                continue
            label = _display_label(name, oid, name in collide)
            hits.append(_state_header_hit(
                sr, label, successors_verified,
                question_date=question_date))
    elif shape is AssemblyShape.DURATION_STATE:
        # #2654 — the state-entry anchor variant. The window's GRAPH end is
        # the subject's state entry (ObjectRegistered/status-start date, see
        # _state_entry_date); its other end is the question's as-of date
        # (``question_date`` — the SAME parameter the walker's as-of window
        # and render_context's ``Current Date:`` header use), so the span is
        # reproducible from the rendered context alone.
        #
        # TWO lines, each naming the dates it used: the reader (whose TR
        # fragment computes elapsed time from the context's date
        # annotations) gets the entry date even when the span cannot be
        # stated, and can never mistake the as-of span for the question's own
        # reference-event span ("when I attended the meetup") — that event is
        # question context, and the subject's dated rows below carry its date
        # when the graph holds it. No usable entry (undated subject, no
        # registration stamp) emits NO line: an anchor line without a date
        # would be a fabricated anchor.
        #
        # REJECTED ALTERNATIVE (recorded, never silently skipped): anchored
        # the far end on the resolved "when <event>" date instead of the
        # as-of date. Rejected because that clause is a first-person
        # PREDICATE, not an entity name ("I attended the meetup", "I
        # rearranged my living room furniture") — resolving it needs an
        # entity-extraction pass this module does not have, and gating the
        # shape on it fires on NOTHING (all 5 census rows phrase the event
        # that way). The as-of end needs no resolution, and it is the same
        # date render_context stamps on the block (``Current Date:``), so the
        # reader reconciles both ends from the rendered context alone.
        qd = _norm_date(question_date)
        for oid, name in order:
            entry = _state_entry_date(slices, oid)
            if entry is None:
                continue
            if qd is not None and entry > qd:
                # as-of BEFORE the state entry: the state had not begun as of
                # the question's window, so there is no window to anchor —
                # the same as-of polarity _state_header_hit applies to a
                # post-as-of supersession (P2-4). The row dates are already
                # as-of-windowed by the walker; only the ObjectRegistered
                # stamp can reach here from outside the window.
                continue
            label = _display_label(name, oid, name in collide)
            hits.append({"content": f"STATE ENTRY ({label}): "
                                    f"{_fmt_date(entry)}",
                         "kind": "duration-state"})
            if qd is not None:
                hits.append({"content": f"{(qd - entry).days} days from "
                                        f"{label} state entry "
                                        f"({_fmt_date(entry)}) to the as-of "
                                        f"date ({_fmt_date(qd)})",
                             "kind": "duration-state"})

    # per-subject sections (subject-major). Rows are re-sorted internally on
    # (date, id) so synthesize_hits output is INDEPENDENT of the input row
    # order — the chronological-within-a-section contract never depends on
    # the caller having pre-sorted (matched-control order-shuffle safe).
    for oid, _name in order:
        section_rows = _oid_rows(slices, oid)
        section_rows.sort(key=lambda r: (r.get("date") or _date.max,
                                         _as_str(r.get("id"))))
        seen: set[str] = set()
        for r in section_rows:
            rid = r.get("id")
            if rid is not None:
                if rid in seen:
                    continue  # cross-subject anchor: once PER section
                seen.add(rid)
            cleaned = {k: v for k, v in r.items()
                       if k not in ("date", "tier")}
            hits.append(cleaned)
    return hits


# ══════════════════════════════════════════════════════════════════════════
# #2652 — 'ago'-relative / implicit-time resolution: a deterministic
# anchor+offset resolver (question_date MINUS offset → as-of window) and
# the window walk that feeds it. Constraint 6 of #2165: NO LLM in this tier.
#
# The resolver is the ONLY new pure surface. The walk reuses the R2 date
# ladder (``_tier_and_date``) and the SAME ``_norm_date`` helper as the
# as-of boundary, so there is ONE date semantics in the module.
#
# WINDOW SEMANTICS (pinned — the resolution is a RANGE, never a fabricated
# single date: a '2 weeks ago' claim is generated from a date rounded to the
# week, so pinning one day would be a confidently wrong anchor):
#   * exact day offset (digits, 'a'/'an' = 1) → [anchor, anchor]
#   * fuzzy determiner (couple/few/several) → anchor ± 2 days
#   * week  → anchor ± 3 days   (7-day window, nearest-week rounding)
#   * month → anchor ± 15 days  (calendar month subtraction, day clamped)
#   * year  → anchor ± 182 days (calendar year subtraction)
#   * 'how many <unit> ago'     → NO offset is given (the count IS the
#     answer): anchor None, window = the recent past
#     ``[qd - _AGO_LOOKBACK_DAYS, qd]``.
#   * 'most recently'           → anchor None, window = the recent past
#     ``[qd - _AGO_LOOKBACK_DAYS, qd]``, rendered MOST-RECENT-FIRST.
# ══════════════════════════════════════════════════════════════════════════

import calendar as _calendar  # noqa: E402
from datetime import timedelta as _timedelta  # noqa: E402

#: How far back the offset-less forms look. The count form asks "how long
#: ago" — an answer measured in days/weeks/months — so a bounded recent
#: window is the deterministic envelope. 400 days covers the day/week/month
#: phrasings with a margin; a year-unit answer beyond ~13 months falls
#: OUTSIDE it DELIBERATELY (the envelope stays bounded, and an empty window
#: falls back to legacy rather than widening on an unmeasured surface).
_AGO_LOOKBACK_DAYS = 400

_WORD_COUNT = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "a couple of": 2, "couple of": 2,
    "a few": 3, "few": 3, "several": 3,
}
#: determiners whose count is approximate → the window widens (days only;
#: week/month/year offsets already carry the unit's own half-span). NOT
#: 'a'/'an': an article means exactly ONE, so "a day ago" is yesterday —
#: there is no slack to widen, and the clamp below is what structurally
#: keeps an as-of window from crossing question_date either way.
_FUZZY_COUNT = frozenset({"a couple of", "couple of", "a few", "few",
                          "several"})
#: unit → half-span of the window that a "<count> <unit> ago" claim covers
_UNIT_HALFSPAN = {"day": 0, "week": 3, "month": 15, "year": 182}
_FUZZY_DAY_HALFSPAN = 2


def _singular(unit: str) -> str:
    u = unit.lower().strip()
    return u[:-1] if u.endswith("s") else u


def _sub_months(d: _date, n: int) -> _date:
    """Calendar month subtraction with a day clamp (2026-03-31 − 1 month =
    2026-02-28) — never a 30-day approximation."""
    total = (d.year * 12 + (d.month - 1)) - n
    y, m = divmod(total, 12)
    return _date(y, m + 1, min(d.day, _calendar.monthrange(y, m + 1)[1]))


def _sub_years(d: _date, n: int) -> _date:
    """Calendar year subtraction with the 02-29 clamp."""
    y = d.year - n
    try:
        return d.replace(year=y)
    except ValueError:  # 02-29 → 02-28 in a non-leap year
        return d.replace(year=y, month=2, day=28)


@dataclass(frozen=True)
class RelativeAnchor:
    """#2652 resolver output: the offset resolved against question_date.

    ``anchor`` is None for the offset-less forms (count / recency) — the
    window is then the bounded recent past. ``fuzzy`` records whether the
    count came from an approximate determiner.
    """
    kind: str                   # "ago-count" | "ago-offset" | "recency"
    unit: str | None            # "day" | "week" | "month" | "year"
    count: int | None
    anchor: _date | None
    window_lo: _date | None
    window_hi: _date | None
    fuzzy: bool = False
    #: render newest-first (the answer locus for 'most recently' is the
    #: most recent entry, so the surviving budget must hold it first)
    newest_first: bool = False


def _parse_count(raw: str) -> tuple[int, bool] | None:
    """Determiner/digit → (count, fuzzy). None when unparseable."""
    key = " ".join(raw.lower().split())
    if key.isdigit():
        return int(key), False
    n = _WORD_COUNT.get(key)
    if n is None:
        return None
    return n, key in _FUZZY_COUNT


def resolve_relative_anchor(question: str,
                            question_date=None) -> RelativeAnchor | None:
    """Deterministic no-LLM 'ago'-relative resolver (#2652).

    question_date MINUS the parsed offset → an as-of WINDOW (see the module
    section header for the pinned semantics). Returns None — never raises —
    when the question carries no promoted relative expression, when
    question_date is absent/unparseable, or when the count is unparseable;
    the caller then falls through to the legacy lane.
    """
    if not question or not isinstance(question, str):
        return None
    qd = _norm_date(question_date)
    if qd is None:
        return None
    # The two entry points must never disagree about which questions the
    # shape owns. ``_relative_guarded`` is only the TWO-SUBJECT half of the
    # boundary; ``classify_question`` also refuses the calendar-anchor,
    # frequency/count, advice and duration-state families. Gate on it, so a
    # caller that skips the router is never handed an anchor for a question
    # the shape does not own (a no-op on the production path, which routes
    # through ``classify_question`` first).
    if classify_question(question) is not AssemblyShape.AGO_RELATIVE:
        return None
    rel = _relative_guarded(question)
    if rel is None:
        return None
    kind, m = rel
    # the offset-less forms share the bounded recent-past envelope; the
    # subtraction is inside a guard so the 'never raises' contract is
    # structural rather than incidental (it currently cannot overflow only
    # because _norm_date floors question_date above 1970)
    try:
        lookback_lo = qd - _timedelta(days=_AGO_LOOKBACK_DAYS)
    except (OverflowError, ValueError):
        return None
    if kind == "recency":
        return RelativeAnchor(
            kind=kind, unit=None, count=None, anchor=None,
            window_lo=lookback_lo, window_hi=qd,
            newest_first=True)
    unit = _singular(m.group("unit"))
    if unit not in _UNIT_HALFSPAN:
        return None
    if kind == "ago-count":
        # no offset given: the COUNT is the answer, so the anchor is
        # unknown and the window is the bounded recent past. Newest-first
        # for the same reason as recency: the answer is the elapsed time to
        # the MOST RECENT occurrence, so the budget must hold that end of
        # the window. The port already fetches newest-first (its cap binds
        # on createdAt DESC), and rendering oldest-first would hand the
        # budget exactly the rows the fetch deprioritised.
        return RelativeAnchor(
            kind=kind, unit=unit, count=None, anchor=None,
            window_lo=lookback_lo, window_hi=qd,
            newest_first=True)
    parsed = _parse_count(m.group("count"))
    if parsed is None:
        return None
    count, fuzzy = parsed
    try:
        if unit == "day":
            anchor = qd - _timedelta(days=count)
        elif unit == "week":
            anchor = qd - _timedelta(days=7 * count)
        elif unit == "month":
            anchor = _sub_months(qd, count)
        else:
            anchor = _sub_years(qd, count)
        half = _UNIT_HALFSPAN[unit]
        if fuzzy and unit == "day":
            half = _FUZZY_DAY_HALFSPAN
        # never extend the as-of window past question_date: the question
        # asks what was true AS OF that date, so a future row is never an
        # answer.
        window_lo = anchor - _timedelta(days=half)
        window_hi = min(anchor + _timedelta(days=half), qd)
    except (OverflowError, ValueError):
        # an UNREPRESENTABLE offset ("99999999999999999999 days ago", or an
        # anchor/window edge that falls outside date's 1..9999) is an
        # unparseable one: the contract is None, never a raise. A raise
        # here would escape _assemble_connected and surface as
        # AskRetrievalUnavailable, defeating the legacy fall-through this
        # shape's absent-anchor path exists for. The WINDOW arithmetic is
        # inside the guard too — an anchor can be representable while
        # anchor ± half still overflows ("what did I do 2025 years ago?"
        # from a 2026 date).
        return None
    return RelativeAnchor(
        kind=kind, unit=unit, count=count, anchor=anchor,
        window_lo=window_lo, window_hi=window_hi, fuzzy=fuzzy)


class Window(Protocol):
    """Structural window seam for ``collect_window_slices``.

    The walk reads ONLY these three attributes, so the #2652
    ``RelativeAnchor`` and the #8056 ``ClassWindow`` share ONE walk — and
    therefore one R2 date ladder, one as-of filter and one fetch leg.
    """

    window_lo: _date | None
    window_hi: _date | None
    newest_first: bool


def collect_window_slices(port, anchor: Window, *,
                          per_window_cap: int = PER_ENTITY_FANOUT_CAP,
                          ) -> AssemblySlices:
    """#2652 window walk → typed slices (the AGO_RELATIVE analogue of
    ``collect_slices``, whose subject selector this shape does not have).

    ONE batched fetch through ``port.window_rows`` (never N+1), each row
    date-tiered by the SAME R2 ladder (``_tier_and_date``: when → createdAt
    → startedAt, malformed values falling through) and filtered to the
    window on the NORMALIZED date — so a row whose stored ``when`` is junk
    but whose ``createdAt`` is real is placed by its real date, never
    dropped. The spine is chronological (newest-first for recency). Never
    raises: a port without the leg degrades to empty slices (the caller's
    empty-block guard then falls back to legacy).
    """
    cap = bounded_fanout(per_window_cap)
    fetch = getattr(port, "window_rows", None)
    rows: list[dict] = []
    if fetch is not None:
        try:
            rows = list(fetch(anchor.window_lo, anchor.window_hi, cap) or [])
        except Exception:  # noqa: BLE001, RUF100 — degrade to legacy
            rows = []
    timeline: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        oid = str(row.get("object_id") or "")
        row = dict(row)
        tier, d = _tier_and_date(row)
        row["tier"] = tier
        row["date"] = d
        if d is None or d < anchor.window_lo or d > anchor.window_hi:
            # an UNDATED row cannot satisfy an elapsed-time window (admitting
            # it would inject an undated line into a date-arithmetic answer)
            continue
        rid = row.get("id")
        if rid is not None:
            key = (oid, str(rid))
            if key in seen:
                continue
            seen.add(key)
        timeline.append(row)
    timeline.sort(key=lambda r: (r.get("date"), r.get("id", "")),
                  reverse=anchor.newest_first)
    # `window_rows` issues TWO LIMIT-ed statements — points and events, each
    # `cap` — so "was anything truncated" is a PER-LEG question and
    # `len(rows) >= cap` answers it wrongly: 3 points + 1 event at cap=4 is
    # 4 rows with neither leg saturated, reported truncated. The port tags
    # each row with its leg (`kind`, the discriminator `evidence_rows`
    # already uses), so the per-leg counts are recoverable without changing
    # the port contract. `rows_requested` is the sum of the two caps for the
    # same reason — the walk asked for up to `2 * cap` rows.
    event_rows = sum(1 for r in rows if r.get("kind") == "event")
    truncated = (len(rows) - event_rows) >= cap or event_rows >= cap
    return AssemblySlices(
        state_rows=(),
        timeline_rows=tuple(timeline),
        evidence_rows=tuple(r for r in timeline
                            if r.get("kind") == "point"),
        admission={"rows_requested": 2 * cap, "rows_admitted": len(timeline),
                   "truncated": truncated})


# ══════════════════════════════════════════════════════════════════════════
# #8056 — class-plural temporal ENUMERATION: the window walk that SELECTS A
# CLASS.
#
# The question asks for the order of a CLASS of events over a relative window
# and names NO subjects ("the concerts and musical events I attended in the
# past two months"), so the selector is WINDOW ∩ CLASS and there is no Object
# to resolve. The class is selected DETERMINISTICALLY from the question's own
# class span by lexical token overlap — no model, no entity resolution (a
# class is not an Object; the #8056 issue asks for "select the class, order
# all its dated rows").
#
# WINDOW SEMANTICS (pinned — a RANGE, never a fabricated point): the question
# asks what happened over the last N <unit> BEFORE it was asked, so
# ``window_hi = question_date`` and ``window_lo = question_date − N <unit>``
# through the SAME ``_sub_months``/``_sub_years`` calendar arithmetic #2652
# uses (a 30-day approximation would drift across month lengths). The count
# is 1 when the question carries none ("during the past month").
#
# This is NOT #2652's window: that shape resolves an 'ago' point-offset into
# an anchor ± a unit half-span ("two months ago"), whereas "in the past two
# months" is a bounded lookback RANGE. Neither is it #2655's day lookup (a
# calendar anchor resolved to an equality day). The one shared piece is the
# walk itself — deliberately (one date ladder, one fetch leg).
# ══════════════════════════════════════════════════════════════════════════

#: the class span's leading determiner and leading count carry no CLASS
#: meaning — "the three sports events" filters on SPORT. Stripped in that
#: order (determiner first) before tokenizing.
_CLASS_LEAD_DET = re.compile(
    r"^(?:the|a|an|my|our|their|your|his|her|its)\s+", re.IGNORECASE)
_CLASS_LEAD_COUNT = re.compile(r"^(?:" + _CLASS_COUNT_RX + r")\s+",
                               re.IGNORECASE)
#: word tokens of a class span / of a row's own text. A token must START with
#: a letter, so digits and punctuation are separators.
_CLASS_WORD = re.compile(r"[a-z][a-z'-]*")
#: function words and number words — no class meaning.
#: the recency/ordinal DIRECTION vocabulary as a CLASS stoplist (#8779 F12).
#: These words carry the ordering INSTRUCTION (`_class_order_newest_first`,
#: whose `_CLASS_DIR_RECENT`/`_CLASS_DIR_OLD` alternations below declare the
#: same vocabulary for the direction read) and are never a class's own
#: content: left in, a bare attributive form survives into the token set and
#: `_class_owns_row` ORs ANY shared token, so `the last five concerts` kept
#: `last` and admitted `read the last news about the election` as a concert —
#: a FALSE INCLUDE. `most` is here for the `most recent` bigram, which the
#: recent alternation matches as one unit.
_CLASS_DIRECTION_WORDS = frozenset({
    "reverse", "latest", "newest", "recent", "last", "most",
    "earliest", "oldest", "first",
})
_CLASS_STOPWORDS = frozenset({
    "a", "an", "and", "at", "by", "couple", "during", "few", "for",
    "from", "his", "her", "i", "in", "is", "it", "its", "many", "me",
    "my", "of", "on", "or", "our", "several", "that", "the", "their",
    "to", "us", "was", "we", "were", "which", "who", "with", "you",
    "your",
    "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve",
}) | _CLASS_DIRECTION_WORDS
#: the CLASS-GENERIC head nouns. A class naming BOTH a discriminative token
#: and a generic one ("concerts and musical events") filters on the
#: discriminative tokens ONLY: `event` alone matches almost any narrative row,
#: so keeping it would turn the class filter into a near-no-op and let the
#: whole window through. When EVERY token of a class is generic ("the things I
#: did") the set is used as-is — a weak filter that selects nothing renders
#: nothing, and the caller falls through to legacy rather than to a wrong
#: answer (fail-closed, below).
_CLASS_GENERIC = frozenset({"event", "thing", "activity", "item", "time"})
#: prepositions that OPEN an adjunct inside the class span. The span runs to
#: the relative-clause pronoun, so an adjunct the user did NOT mean as part of
#: the class ("the concerts IN CENTRAL PARK I attended …") lands inside it, and
#: `_class_owns_row` ORs ANY shared token -- `central` and `park` therefore
#: became alternative SELECTORS and a "walk in Central Park" row was enumerated
#: as a concert (#8779 F4: the same FALSE INCLUDE the possessive boundary
#: closed, for the adjunct case). A class noun phrase's own head and modifiers
#: PRECEDE its first prepositional phrase, so the vocabulary ENDS there.
#: "and"/"or" are deliberately NOT here: a coordinated class ("the concerts
#: and musical events") must keep both sides of the coordination.
_CLASS_ADJUNCT_PREP = frozenset({
    "about", "after", "around", "at", "before", "between", "by", "during",
    "for", "from", "in", "into", "near", "of", "on", "over", "through",
    "to", "under", "with", "within",
})
#: relative markers — they OPEN A CLAUSE, so the class noun phrase ends there.
_CLASS_RELATIVE = frozenset({"that", "which", "who", "whom", "whose",
                             "where", "when"})
#: clause SUBJECTS. A pronoun inside a prepositional span means the preposition
#: opened a clause ("after I graduated"), not a noun complement — that span
#: names no class, so it must not become the vocabulary.
_CLASS_PRONOUN = frozenset({"i", "we", "you", "he", "she", "it", "they",
                            "me", "us", "him", "her", "them"})
#: common IRREGULAR past participles, which the `-ed`/`-ing` suffix test cannot
#: see. Only meaningful in post-modifier position, where the position rule
#: already protects the head noun: an unlisted one then costs a stray selector,
#: never the class noun.
_CLASS_PARTICIPLE = frozenset({
    "held", "put", "given", "written", "sung", "eaten", "heard", "made",
    "taken", "seen", "done", "built", "sent", "bought", "brought",
    "thought", "found", "kept", "felt", "told", "sold", "run", "won",
    "lost", "met", "paid", "said", "spent", "taught", "caught",
    "chosen", "hidden", "known", "shown", "spoken", "stolen", "torn",
    "broken", "frozen", "drawn", "grown", "thrown", "blown", "flown",
})


#: adjective → noun, ONLY where the pair is a MEASURED class word. A bare
#: `-al` strip is not safe: it turned `formal` into `form` (#8779 F16) and
#: enumerated a "filled out the FORM" row as a *formal event*. Deliberately
#: NOT a general stemmer: every rule beyond these widens the match surface
#: without a measured row asking for it.
_CLASS_ADJ_NOUN = {"musical": "music"}

#: plural (or mass) nouns whose singular is a DIFFERENT lexeme, so the trailing
#: `s` strip would collide them with an unrelated word: `news` → `new` made a
#: "ordered a new couch" row a news item, and `goods`/`means` do the same
#: (#8779 F23). A bounded list, not a stemmer — the rule stays recall-only.
_CLASS_NO_STRIP = frozenset({
    "news", "goods", "means", "series", "species", "headquarters",
    "crossroads", "outskirts",
})

#: particles that close a reduced-relative RUN — "the events PUT TOGETHER and
#: ORGANIZED by the club" keeps a particle between two participles, and a run
#: stripper that stops at it leaves the verb as the class (#8779 F22).
_CLASS_POSTMOD_ADV = frozenset({
    "together", "back", "away", "off", "up", "down", "again", "over",
    "through", "apart",
})

#: bare verbs that can follow `to` inside a class span — the INFINITIVE marker
#: is in `_CLASS_ADJUNCT_PREP`, so "the things TO DO" reached the generic-head
#: complement fallback and promoted the VERB `do` to the class vocabulary: a
#: row reading "I do not remember the name" was enumerated as a thing-to-do
#: and the real class row was not (#8779 F21). Bounded on purpose, like
#: `_CLASS_PARTICIPLE`; an unlisted verb costs the same false include the
#: list prevents, and a NOUN after `to` ("the trips TO PARIS") is unaffected.
_CLASS_INFINITIVE = frozenset({
    "do", "attend", "go", "see", "visit", "make", "take", "try", "use",
    "find", "get", "have", "know", "learn", "meet", "read", "run", "watch",
    "buy", "send", "write", "call", "check", "review", "join", "host",
})


def _class_stem(tok: str) -> str:
    """Deterministic class-token stem — ONE function for BOTH the question's
    class span and a row's own text, so the two sides can never drift.

    Three rules only, and the measured surface needs no more:

    * plural ``ies`` → ``y`` (activities ≡ activity);
    * a trailing plural ``s`` (not ``ss``);
    * a bounded ADJECTIVE→NOUN alias (``musical`` ≡ ``music``), so a class
      named with the adjective matches a row named with the noun.

    ⛔ NOT a bare adjectival ``-al`` strip (#8779 F16). Stripping the suffix
    from ANY ``-al`` word turned `formal` into `form`, so a row reading
    "filled out the FORM for the insurance" was enumerated as a *formal
    event* — a FALSE INCLUDE, the direction this design does not accept. The
    mapping is a bounded list of measured pairs instead: a pair that is not
    listed costs RECALL (the adjective fails to match its noun) and never
    precision.
    """
    t = tok.lower()
    if t in _CLASS_ADJ_NOUN:
        return _CLASS_ADJ_NOUN[t]
    if t in _CLASS_NO_STRIP:
        return t
    if t.endswith("ies") and len(t) > 4:
        t = t[:-3] + "y"
    elif t.endswith("s") and not t.endswith("ss") and len(t) > 3:
        t = t[:-1]
    return t


def _class_drop_postmodifiers(tokens: list[str]) -> list[str]:
    """Strip the trailing POST-MODIFIER run of a class noun phrase.

    A token after the class's head noun is a reduced relative ("the concerts
    HELD at …", "the events ORGANIZED AND HOSTED by …") and never class
    vocabulary. Reduced relatives COORDINATE, so the run is stripped as a
    run: trimming a single token promoted the next participle to "the
    discriminator" and selected rows that merely used the verb — "organized
    my desk this morning" was enumerated as an event of the class while the
    real class row was dropped (#8779 F22). A word carrying the PLURAL marker
    is a noun and ends the run, and the run may not consume everything (the
    caller falls back to the head noun).
    """
    out = list(tokens)
    while out and (
            out[-1] in _CLASS_POSTMOD_ADV
            or (not out[-1].endswith("s")
                and (out[-1].endswith(("ed", "ing"))
                     or out[-1] in _CLASS_PARTICIPLE))):
        out.pop()
    return out


#: #8779 E1/E2 — the ORDERING-DIRECTION vocabulary of the class-plural head's
#: ordering instruction. The words are named ONCE so the direction READER and
#: the class-span SCRUBBER below cannot disagree about what an instruction is.
#: `most\W+recent` precedes `recent` so the compound is one word, not two.
_CLASS_DIR_RECENT = r"(?:reverse|latest|newest|most\W+recent|recent|last)"
_CLASS_DIR_OLD = r"(?:earliest|oldest|least\W+recent|first)"
_CLASS_DIR_WORD = r"(?:" + _CLASS_DIR_RECENT + r"|" + _CLASS_DIR_OLD + r")"
_CLASS_DIR_RECENT_FULL = re.compile(_CLASS_DIR_RECENT, re.IGNORECASE)

# ⛔ The instruction is matched STRUCTURALLY, not by scanning for a direction
# word anywhere in the text (#8779 E2). A trailing clause that merely CONTAINS
# `last` — "and which was the last?", "how long did each last?" — is not an
# ordering instruction, and reading it as one reversed the block for a
# question that asked for no direction at all. A direction word counts only
# inside one of these bound forms:
#   * `from [the] X to [the] Y` — the FIRST bound is the end to start from;
#   * `starting from [the] X`   — the census's own spelling;
#   * `X first`                 — "newest first" / "latest first";
#   * `in reverse chronological order`.
# Position is FREE (#8779 E1): an instruction placed BEFORE the relative clause
# ("the concerts, from latest to earliest, that I attended …") sits inside the
# class span rather than the captured tail, so the reader runs over the WHOLE
# question and `_class_span_without_instruction` strips the same forms out of
# the class vocabulary.
_RE_CLASS_ORDER_FROM_TO = re.compile(
    r"\bfrom\s+(?:the\s+)?(?P<first>" + _CLASS_DIR_WORD + r")\s+to\s+"
    r"(?:the\s+)?" + _CLASS_DIR_WORD + r"\b",
    re.IGNORECASE)
#: the SAME structural pair WITHOUT the leading `from` -- "latest to
#: earliest", "newest to oldest", "most recent to oldest" (#8779 F6). The
#: census spells the form WITH `from`, so a reverse request spelled without it
#: was not read as an instruction at all: `newest_first` stayed False and the
#: fired block rendered CHRONOLOGICAL order under an "earliest first" header
#: -- the opposite of what the question asked, which this shape's posture
#: (abstain rather than answer wrong) forbids. It remains a STRUCTURAL pair,
#: never a scan for a direction word, so a `last`/`latest` merely MENTIONED in
#: the question still reverses nothing (#8779 E2).
_RE_CLASS_ORDER_BARE_TO = re.compile(
    r"\b(?P<first>" + _CLASS_DIR_WORD + r")\s+to\s+(?:the\s+)?"
    + _CLASS_DIR_WORD + r"\b",
    re.IGNORECASE)
_RE_CLASS_ORDER_STARTING = re.compile(
    # `starting with` is the SAME structural bound as `starting from`: a
    # reverse request spelled "starting with the most recent" was not read at
    # all, so the block rendered CHRONOLOGICAL order under an "earliest first"
    # header for an unambiguous reverse question (#8779 F7).
    r"\bstarting\s+(?:from|with)\s+(?:the\s+)?(?P<first>"
    + _CLASS_DIR_WORD + r")\b",
    re.IGNORECASE)
_RE_CLASS_ORDER_FIRST = re.compile(
    # ⛔ the `X first` INSTRUCTION is TERMINAL: it specifies the order of the
    # answer and is appended to the question. Without that bound a direction
    # word merely PRECEDING a compound class word reversed the block and was
    # scrubbed out of the class: "the latest first-aid training sessions"
    # became "the -aid training sessions" with the order reversed, and "the
    # most recent first impressions I formed" became "the impressions"
    # (#8779 F11). Requiring end-of-question (bar trailing punctuation) fixes
    # both and still admits "latest first" / "newest first".
    r"\b(?P<first>" + _CLASS_DIR_WORD + r")\s+first\b(?=\s*[?.!,]?\s*$)",
    re.IGNORECASE)
_RE_CLASS_ORDER_REVERSE = re.compile(
    # the HYPHENATED spelling is the ordinary one for this phrase, and `\s+`
    # alone left it unmatched: `newest_first` stayed False and the block
    # rendered CHRONOLOGICAL order under an "earliest first" header for a
    # reverse request (#8779 F9). The sibling vocabulary already absorbs
    # spelling variation this way (`most\W+recent`).
    r"\breverse[\s-]+chronological[\s-]+order\b", re.IGNORECASE)
#: every structural form, for scrubbing the instruction out of the class span.
_RE_CLASS_ORDER_INSTRUCTION = re.compile(
    r"\bfrom\s+(?:the\s+)?" + _CLASS_DIR_WORD + r"\s+to\s+(?:the\s+)?"
    + _CLASS_DIR_WORD + r"\b"
    r"|\bstarting\s+(?:from|with)\s+(?:the\s+)?" + _CLASS_DIR_WORD + r"\b"
    r"|\b" + _CLASS_DIR_WORD + r"\s+first\b(?=\s*[?.!,]?\s*$)"
    r"|\breverse[\s-]+chronological[\s-]+order\b"
    r"|\b" + _CLASS_DIR_WORD + r"\s+to\s+(?:the\s+)?" + _CLASS_DIR_WORD
    + r"\b",
    re.IGNORECASE)


def _named_fixed_date_anchor(text: str) -> bool:
    """Does ``text`` name a FIXED-DATE holiday (Christmas, Halloween, …)?

    #8779 F12: `_RE_AGO_OTHER`'s calendar surface carries the WEEKDAY/WEEKEND
    forms and `valentine` only, so a class-plural question naming any OTHER
    holiday — "… in the past two months on Christmas?" — was claimed by this
    shape and the named day was silently dropped, where `main` routed it to
    the day-scoped lookup. The vocabulary lives in ONE place
    (``calendar_anchors.FIXED_DATE_HOLIDAYS``, read through the shared
    ``find_anchor_spans``), so this asks the owner instead of keeping a second
    copy of the table that could drift from it.
    """
    return any(span.kind == CalendarAnchorKind.FIXED_DATE
               for span in find_anchor_spans(text))


def _class_order_newest_first(question: str) -> bool:
    """Does the question's ordering instruction ask for the NEWEST first?

    ``False`` — the census spelling ("starting from the earliest" / "from
    earliest to latest") and the head's own default when the question carries
    no instruction at all — is chronological; ``True`` is
    reverse-chronological. Only a STRUCTURAL bound phrase is read (see
    ``_RE_CLASS_ORDER_FROM_TO`` and friends): the first bound of a `from X to
    Y` / `starting from X` pair, or the word in an `X first` form, decides; a
    bare `latest`/`last` left over in the tail is NOT an instruction (#8779
    E2). The reader takes the WHOLE question so the instruction's position —
    before or after the relative clause — cannot change the answer (#8779 E1).
    """
    text = _as_str(question)
    best = None
    for rx in (_RE_CLASS_ORDER_FROM_TO, _RE_CLASS_ORDER_BARE_TO,
               _RE_CLASS_ORDER_STARTING, _RE_CLASS_ORDER_FIRST):
        m = rx.search(text)
        if m is not None and (best is None or m.start() < best.start()):
            best = m
    if best is not None:
        return _CLASS_DIR_RECENT_FULL.fullmatch(best.group("first")) is not None
    return _RE_CLASS_ORDER_REVERSE.search(text) is not None


def _class_span_without_instruction(span: str) -> str:
    """The class span with any ordering instruction removed (#8779 E1).

    A class-plural question may place its instruction BEFORE the relative
    clause ("the concerts, from latest to earliest, that I attended …"), where
    the class capture picks it up. The direction words are not class
    vocabulary: left in, ``_class_owns_row`` returns True for any row that
    merely mentions "latest" (a FALSE INCLUDE — the dangerous direction), and
    the header would name the instruction as part of the class. Removing the
    structural forms and tidying the separator they leave keeps the token set
    the class's own content words.
    """
    scrubbed = _RE_CLASS_ORDER_INSTRUCTION.sub(" ", _as_str(span))
    scrubbed = re.sub(r"\s+", " ", scrubbed)
    cleaned = scrubbed.strip(" ,;:-")
    # ⛔ an interposed possessive ends the span AT the possessive
    # ("… the concerts WITH my friend"), so the preposition that governed it
    # is left dangling and the rendered header named a truncated, ungrammatical
    # class ("CLASS ORDER (the concerts with)") (#8779 F20).
    words = cleaned.split()
    while words and words[-1].lower() in _CLASS_ADJUNCT_PREP:
        words.pop()
    return " ".join(words)


def _class_tokens(span: str) -> tuple[str, ...]:
    """The question's CLASS vocabulary (ordered, deduped, stemmed).

    Generic head nouns are dropped whenever a discriminative token exists; a
    span with no usable token yields ``()`` (the resolver then abstains — see
    ``resolve_class_window``).

    The span is ``_RE_CLASS_PLURAL``'s own capture, TRIMMED by
    ``_class_span_without_instruction`` (so any ordering instruction the
    capture swallowed is gone) and ending at the relative-clause pronoun, so
    it carries NO verb — the token set is the class's own content words. That
    boundary is pinned by the exact ``class_phrase`` assertions in
    ``test_resolve_class_window_arithmetic`` and
    ``test_class_order_direction_reads_an_instruction_before_the_clause``, not
    by a verb stoplist (a stoplist would need every verb in the language).
    """
    t = _as_str(span).strip()
    t = _CLASS_LEAD_DET.sub("", t, count=1).strip()
    t = _CLASS_LEAD_COUNT.sub("", t, count=1).strip()

    def _stems(words) -> list[str]:
        acc: list[str] = []
        for raw in words:
            if raw in _CLASS_STOPWORDS:
                continue
            stem = _class_stem(raw)
            if stem and stem not in acc:
                acc.append(stem)
        return acc

    # ⛔ the vocabulary is the class NOUN PHRASE — a CLAUSE, an APPOSITIVE or a
    # POST-MODIFIER is not part of it, and every one of them leaks a token
    # that then acts as an alternative SELECTOR, because `_class_owns_row` ORs
    # ANY shared token (#8779 F12):
    #   * a comma opens an appositive — "the concerts, the BIGGEST events of
    #     the year" left `biggest`, and a "biggest sale of the year" row was
    #     enumerated as a concert;
    #   * a relative marker opens a clause — "the events THAT TOOK PLACE" left
    #     the verb `took`, which matches almost every narrative row;
    #   * a participle in post-modifier position IS a reduced relative —
    #     "the concerts ORGANIZED by the club" left `organized`.
    # The span regex cannot see clause structure, so the boundary is drawn on
    # the token stream, at the FIRST of {comma, relative marker, preposition}.
    words = _CLASS_WORD.findall(t.split(",")[0].lower())
    cut = len(words)
    for i, raw in enumerate(words):
        if raw in _CLASS_ADJUNCT_PREP or raw in _CLASS_RELATIVE:
            cut = i
            break
    run = words[:cut]
    # ⛔ a token AFTER the class's generic head noun is a POST-MODIFIER -- a
    # participle or an adjunct ("the events PUT on by ...", "the concerts HELD
    # at ...") -- and the generic-drop rule would otherwise promote it to
    # "the discriminator" and DELETE THE HEAD NOUN, leaving a bare verb as the
    # vocabulary. Measured: "the events put on by the club" became ('put',),
    # which selected "put the keys on the table" and EXCLUDED the row that
    # actually named the class (#8779 F13). Irregular participles have no
    # suffix to test, so POSITION decides: nothing between the head noun and
    # the boundary is class vocabulary.
    # ... and a DISCRIMINATOR after a generic head REPLACES it (#8779 F15):
    # §2 drops the generic head whenever a discriminator exists, so
    # "the events and the jazz festival" is the jazz festival's class.
    # Truncating AT the head left the near-universal `event` alone, which
    # selected a "security event" row and DROPPED the jazz-festival row — the
    # same wrong answer, inverted. What separates a discriminator from a
    # post-modifier is the test the participle rule below already uses: a
    # trailing participle at a boundary is a reduced relative, not a class
    # word.
    head = [w for w in run if w not in _CLASS_STOPWORDS]
    last_generic = max((i for i, w in enumerate(head)
                        if _class_stem(w) in _CLASS_GENERIC), default=-1)
    if last_generic >= 0:
        after = head[last_generic + 1:]
        if after and not any(_class_stem(w) in _CLASS_GENERIC
                             for w in after):
            if cut < len(words):
                after = _class_drop_postmodifiers(after)
            run = after or head[:last_generic + 1]
        else:
            run = head[:last_generic + 1]
    out = _stems(run)
    # a PARTICIPLE immediately before the boundary is a reduced relative, not a
    # class word — dropped only when a DISCRIMINATOR would survive it. A bare
    # one-word class ("the wedding") is never trimmed, and a word is only a
    # post-modifier when something follows it (a boundary was found). The
    # SUFFIX test misses irregulars, so a bounded list covers the common ones;
    # an unlisted one now costs only a stray selector (the head noun survives
    # the rule above), never the head itself.
    #: the PLURAL marker overrides the suffix test, and the run is stripped as
    #: a RUN — see `_class_drop_postmodifiers`.
    if cut < len(words):
        kept = _class_drop_postmodifiers(
            [w for w in run if w not in _CLASS_STOPWORDS])
        if kept:
            run = kept
    out = _stems(run)
    # ⛔ the cut above is only correct while the HEAD noun phrase still carries
    # a DISCRIMINATIVE token. When it does not -- "the events AT THE JAZZ
    # FESTIVAL", whose head `event` is generic -- cutting leaves the
    # near-universal `event` alone, which selects almost any narrative row (a
    # security event, say) and MISSES the jazz-festival row: a WRONG answer
    # rather than an abstention, i.e. the same defect the cut exists to fix,
    # inverted (#8779 F5). A generic head is identified by its own complement,
    # so fall back to the tokens after the boundary when that is all there is.
    # `the concerts in Central Park` (a DISCRIMINATIVE head) keeps the cut and
    # drops the locative, as before.
    #
    # ⛔ and the fallback must NEVER overwrite a DISCRIMINATIVE head: "the jazz
    # concerts that took place at the event" once became `('event',)` — the
    # class was replaced by the generic noun of its own clause and both concert
    # rows were dropped (#8779 F12). It is also skipped when the complement
    # carries a PRONOUN, because that means the span opened a CLAUSE ("the
    # events that took place AFTER I GRADUATED") which names no class — there
    # the generic head is kept as a WEAK filter rather than a wrong one.
    if cut < len(words) and not [x for x in out if x not in _CLASS_GENERIC]:
        rest = words[cut + 1:]
        # ⛔ what the complement IS depends on what opened it (#8779 F13):
        # after a PREPOSITION it is the noun phrase that preposition governs,
        # so it ENDS at its own next preposition; after a RELATIVE MARKER the
        # words up to the first preposition belong to the clause, so the
        # complement STARTS after it. Treating both alike took the FINAL
        # adjunct instead of the complement's head — "the events at the jazz
        # festival in the park" became ('park',) and "the activities in the
        # park near my house" became ('house',), each selecting a row that
        # merely shared the locator and dropping the real class row.
        if words[cut] in _CLASS_RELATIVE:
            inner = next((j for j, w in enumerate(rest)
                          if w in _CLASS_ADJUNCT_PREP), None)
            rest = rest[inner + 1:] if inner is not None else []
        else:
            stop = next((j for j, w in enumerate(rest)
                         if w in _CLASS_ADJUNCT_PREP), None)
            rest = rest[:stop] if stop is not None else rest
        # a PRONOUN in the span means it is a CLAUSE ("after I graduated"),
        # which names no class — the generic head then stays a WEAK filter
        # rather than becoming a verb.
        if rest and not any(w in _CLASS_PRONOUN for w in rest) \
                and rest[0] not in _CLASS_INFINITIVE:
            complement = _stems(rest)
            if complement:
                out = complement
    discriminative = [tok for tok in out if tok not in _CLASS_GENERIC]
    return tuple(discriminative or out)


def _class_owns_row(row: dict, tokens) -> bool:
    """Does the row's OWN text carry the question's class?

    The row's readable text is ``content`` (a Point's narrative text / an
    Event's ``name``) plus ``search_keys`` (the retrieval index the v2 lane
    authors from the same text — a string, or a list of keys). Both are
    tokenized and stemmed by the SAME ``_class_stem`` as the question side.
    Never raises: a non-string ``content``/key degrades to ``_as_str``.
    """
    if not tokens:
        return False
    parts = [_as_str(row.get("content"))]
    keys = row.get("search_keys")
    if isinstance(keys, str):
        parts.append(keys)
    elif isinstance(keys, (list, tuple, set, frozenset)):
        parts.extend(_as_str(k) for k in keys)
    text = " ".join(parts).lower()
    row_tokens = {_class_stem(w) for w in _CLASS_WORD.findall(text)}
    return bool(row_tokens.intersection(tokens))


@dataclass(frozen=True)
class ClassWindow:
    """#8056 resolver output: the CLASS the question asks to enumerate, and
    the relative window it asks over (see ``resolve_class_window``).

    ``newest_first`` reads the question's OWN ordering instruction (#8779
    P1): the census spelling ("starting from the earliest" / "from earliest
    to latest") is chronological, a reverse spelling ("from latest to
    earliest" / "newest first") is newest-first. It is threaded into the
    shared ``collect_window_slices`` walk AND into this shape's own renderer
    (sort, position numbering and header direction), which is what keeps a
    reverse-order request from being answered in the opposite order.
    """
    class_phrase: str            # the question's own class span, verbatim
    tokens: tuple[str, ...]      # the stemmed class vocabulary
    unit: str | None             # "day" | "week" | "month" | "year"
    count: int | None            # 1 when the question carries no count
    window_lo: _date | None
    window_hi: _date | None
    newest_first: bool = False


def resolve_class_window(question: str,
                         question_date=None) -> ClassWindow | None:
    """Deterministic no-LLM class-plural resolver (#8056).

    The question's relative WINDOW resolved against ``question_date`` —
    ``[question_date − count <unit>, question_date]`` — plus the class
    vocabulary that selects the rows inside it. Returns None — never raises —
    when the question is not this shape, when ``question_date`` is
    absent/unparseable, when the count is unparseable, or when the class span
    yields no usable token; the caller then falls through to legacy.

    Text-only classification + ``question_date`` arithmetic only: no clock
    read, no model, no graph. Gated on ``classify_question`` so the two entry
    points can never disagree about which questions the shape owns (an
    unanswered question is never handed a window).
    """
    if not question or not isinstance(question, str):
        return None
    qd = _norm_date(question_date)
    if qd is None:
        return None
    if classify_question(question) is not AssemblyShape.CLASS_PLURAL:
        return None
    m = _RE_CLASS_PLURAL.match(question.strip())
    if m is None:
        return None
    unit = m.group("unit") or m.group("unit_sg")
    raw_count = m.group("count")
    if raw_count is None:
        # "during the past month" — no count names ONE unit
        count = 1
    else:
        parsed = _parse_count(raw_count)
        if parsed is None:
            return None
        count, _fuzzy = parsed
    if not count or count < 1:
        return None
    unit = _singular(unit or "")
    # the subtraction is inside a guard so the 'never raises' contract is
    # structural rather than incidental: an UNREPRESENTABLE offset (a huge
    # digit count) is an unparseable one, never a raise that would escape
    # _assemble_connected as AskRetrievalUnavailable
    try:
        if unit == "day":
            lo = qd - _timedelta(days=count)
        elif unit == "week":
            lo = qd - _timedelta(days=7 * count)
        elif unit == "month":
            lo = _sub_months(qd, count)
        elif unit == "year":
            lo = _sub_years(qd, count)
        else:
            return None
    except (OverflowError, ValueError):
        return None
    span = _class_span_without_instruction(m.group("cls"))
    tokens = _class_tokens(span)
    if not tokens:
        return None
    return ClassWindow(class_phrase=span or _as_str(m.group("cls")).strip(),
                       tokens=tokens, unit=unit, count=count,
                       window_lo=lo, window_hi=qd,
                       # the WHOLE question: the instruction's position is
                       # free (#8779 E1), so the captured tail alone is not
                       # enough
                       newest_first=_class_order_newest_first(question))


def collect_class_slices(port, window: ClassWindow, *,
                         per_window_cap: int = PER_ENTITY_FANOUT_CAP,
                         ) -> AssemblySlices:
    """#8056: the class-plural walk — the #2652 window walk (same fetch leg,
    same R2 ladder, same as-of filter, undated rows excluded) narrowed to the
    rows that carry the question's CLASS.

    ``admission`` keeps the window census (``rows_requested`` /
    ``truncated`` — the per-leg accounting of what the FETCH returned) and
    adds ``class_filtered_out``, so what the class filter removed is visible
    rather than implied. ``rows_admitted`` is the CLASS-selected count.

    ONE batched fetch (never N+1) and never raises: a port without the leg
    degrades to empty slices, and the caller's empty-block guard then falls
    back to legacy.
    """
    slices = collect_window_slices(port, window, per_window_cap=per_window_cap)
    kept = tuple(r for r in slices.timeline_rows
                 if _class_owns_row(r, window.tokens))
    admission = dict(slices.admission)
    admission["class_filtered_out"] = len(slices.timeline_rows) - len(kept)
    admission["rows_admitted"] = len(kept)
    return AssemblySlices(
        state_rows=(),
        timeline_rows=kept,
        evidence_rows=tuple(r for r in kept if r.get("kind") == "point"),
        admission=admission)


# ══════════════════════════════════════════════════════════════════════════
# #2165 Task 6 — _assemble_connected (R5/R6/R11/R14/R17): the eval-only ask seam.
# One single-source fired path shared by run_ask_lane()'s pre-retrieval
# branch and the eval-lane run_ask_assembled(). R14 drift pin: this function
# is imported ONLY by the two eval-lane entry points (tortoise/ask_lane.py)
# — enforced by a BEHAVIOURAL guard (both entry points fire; a poisoned
# synthesize raises from both), not a source-text grep (replaced in #3849).
# The fired envelope wraps ONLY the content stages
# (classify->resolve->walk->render->decorate->enrich->assemble); the ONE
# reader call stays under the shared run_ask_lane()/run_ask_assembled()
# reader envelope.
# ══════════════════════════════════════════════════════════════════════════

# Object recall-excluded statuses (the successor-EXISTENCE probe treats an
# excluded successor as invisible -> the renderer's NAME-ONLY annotation).
#
# The OBJECT terminal vocabulary is ``commit_ops.OBJECT_TERMINAL_STATUSES``.
# It is NOT ``search_engine``/``live``'s ``TERMINAL_EXCLUDED_STATUSES``, which
# is the POINT vocabulary — #3302: the comment that used to sit here cited
# that Point source for an Object set, sending a reader to the module that
# does not own this vocabulary. Derived from the canonical set rather than
# copied by value, because the by-value copy was the actual root: an Object
# set that was a frozen snapshot of a source it did not reference drifts the
# moment the canonical set moves.
#
# The one divergence is ``outdated``, and it is LOAD-BEARING — not dead. The
# generic write path CAN put it on an :Object: ``create_entity("object", ...)``
# spreads caller props over its literal defaults, and ``_update_entity`` issues
# an unvalidated ``SET n += $props``; ``status`` is not a server-managed prop,
# so both are reachable from the MCP surface (``tortoise_create_entity`` /
# ``tortoise_update_entity``). Dropping the member would flip such a successor
# from recall-excluded (NAME-ONLY annotation) to a verified link. The guard
# that would reject the status (#2977 / PR #3326, ``OBJECT_STATUS_VALUES``) is
# NOT on main.
#
# The divergence is FROZEN by tests/test_assembly_pure.py, which asserts this
# set equal to ``OBJECT_TERMINAL_STATUSES | {"outdated"}`` — a silent drop or
# widening of the member cannot pass unnoticed. (The canonical set itself is
# pinned by tests/test_object_search_visibility_3301.py; the #3317 pin beside
# it guards the RESOLVER set and is not the oracle for this one.)
_RECALL_OBJECT_EXCLUDED_STATUSES = frozenset(
    OBJECT_TERMINAL_STATUSES | {"outdated"})


@dataclass(frozen=True)
class _AssembledBlock:
    """Internal fired block (content stages only — NO reader, NO answer).
    run_ask_lane() and run_ask_assembled() both consume this and add their
    own envelope (shared reader machinery, metering, response shape)."""
    fired: bool
    shape: str | None
    subjects: tuple
    slices: dict
    admission: dict
    post_cap_lines: list
    #: The `assemble_context` drop census for the fired path (items_selected,
    #: dropped_by_token_cap/dropped_by_byte_cap, byte_cap, stopped_by) — so
    #: run_ask_lane() can emit the SAME honest-budget warning on the fired
    #: path that it emits on the legacy path (#4105). Empty when no assembly
    #: ran (fired=False) or an older caller did not pass a stats dict.
    cap_stats: dict = field(default_factory=dict)
    # NOTE: evidence/context_tokens are NOT computed here — run_ask_lane()/the
    # assembled path render post_cap_lines through the SHARED
    # render_context/estimate path so the alignment invariant
    # (context_tokens == estimate_tokens_ask(evidence)) holds by construction.


@dataclass
class AssemblyAnswer:
    """run_ask_assembled() return shape (pinned — Task 7's eval arm reads
    post_cap_lines for gold-id admission and answer for conversion; field
    names are the arm's contract)."""
    fired: bool
    shape: str | None
    question_type: str
    subjects: list
    slices: dict
    post_cap_lines: list
    admission: dict
    evidence: str
    context_tokens: int
    answer: str | None
    retrieval_degraded: bool


def _probe_visible_successors(sdk, slices: AssemblySlices) -> frozenset[str]:
    """Successor-EXISTENCE probe (docker): each distinct non-empty
    superseded_by name in the state slice -> verified ONLY when >= 1 Object
    with that name exists AND is not recall-excluded. The renderer turns an
    unverified name into a NAME-ONLY annotation (never a fabricated link)."""
    names = {(_as_str(sr.get("superseded_by")) or "").strip()
             for sr in slices.state_rows}
    names.discard("")
    if not names:
        return frozenset()
    try:
        proj = sdk._get_proj()
        # ONE batch query: name -> set of visible (non-excluded) statuses
        rows = proj.g.query(
            "MATCH (o:Object) WHERE o.name IN $names "
            "RETURN o.name AS nm, o.status",
            params={"names": sorted(names)}).result_set
    except Exception:  # noqa: BLE001, RUF100 — probe fails open to empty
        rows = []
    statuses_by_name: dict[str, set] = {}
    for nm, st in rows:
        statuses_by_name.setdefault(nm, set()).add(st or "")
    # P2-2: ANY visible (non-excluded) Object of that name verifies the
    # successor — a coexisting excluded same-name row must NOT poison it
    verified = {nm for nm in names
                if statuses_by_name.get(nm)
                and (statuses_by_name[nm]
                     - _RECALL_OBJECT_EXCLUDED_STATUSES)}
    return frozenset(verified)


def _decorate_real_rows(sdk, hits: list[dict]) -> list[dict]:
    """Decorate the REAL (id-keyed) rows of a synthesized block — the
    single source used by BOTH fired paths (subject walk + #2652 window
    walk), so marker/W4 parity is structural rather than re-implemented.

    1. ``annotate_ask_hits`` — additive session_date/speaker join.
    2. D8 claim-level marker parity (P1-2): the SAME decoration source the
       FTS path uses attaches superseded_by/supersedes to real POINT rows.
       Synthesized no-id rows are untouched by both.
    3. W4 why-layer enrichment when the flag is on (id-less rows skipped by
       the enrich guard). Fail-open: a raise degrades to the block WITHOUT
       why keys, never a partial/raw raise.
    """
    import contextlib as _contextlib
    with _contextlib.suppress(Exception):
        hits = sdk.annotate_ask_hits(hits)
    with _contextlib.suppress(Exception):
        from tortoise.search_engine import fetch_point_epistemic_state
        ids = [h.get("id") for h in hits if h.get("id")]
        state = fetch_point_epistemic_state(sdk._get_proj().g, ids) or {}
        out = []
        for h in hits:
            pid = h.get("id")
            st = state.get(str(pid))
            if st is None:
                out.append(h)
                continue
            e = dict(h)
            for k in ("status", "superseded_by", "supersedes"):
                if k in st and st[k] is not None:
                    e[k] = st[k]
            out.append(e)
        hits = out
    with _contextlib.suppress(Exception):
        from tortoise.why import enrich_items, w4_enrichment_enabled
        if w4_enrichment_enabled():
            hits = enrich_items(sdk._get_proj(), hits)
    return hits


def _assemble_under_caps(hits: list[dict], *, caps: dict,
                         question_date: str | None,
                         ) -> tuple[list, dict]:
    """Apply the resolved ask caps to a synthesized block (the shared tail of
    BOTH fired paths — subject walk and #2652 window walk), so the budget
    semantics cannot drift between them.

    #4105: the token budget and the byte ceiling come from ONE validated
    resolution (``resolve_*_from_caps``), and the non-ASCII surcharge is
    opted in — this is the ask lane's connected path, so the bound must hold
    on CJK/emoji pools too. Returns ``(selected_hits, cap_stats)``.
    """
    from tortoise.retrieval import (
        assemble_context,
        resolve_byte_cap_from_caps,
        resolve_item_cap_from_caps,
        resolve_token_cap_from_caps,
    )
    item_cap = resolve_item_cap_from_caps(caps)
    token_cap = resolve_token_cap_from_caps(caps)
    byte_cap = resolve_byte_cap_from_caps(caps)
    cap_stats: dict = {}
    selected = assemble_context(
        hits, top_k=item_cap,
        max_context_tokens=token_cap,
        question_date=question_date,
        context_item_cap=item_cap,
        byte_cap=byte_cap,
        # #4105: opt in to the non-ASCII token surcharge — this is the ask
        # lane's connected path, so the bound must hold on CJK/emoji pools
        # too. The shared function's DEFAULT (and thus the eval re-export,
        # #2070) stays pre-#4105 byte-identical.
        nonascii_token_surcharge=True,
        stats=cap_stats)
    return selected, cap_stats


def _empty_block() -> _AssembledBlock:
    """The R1/P1-1 fall-through: fire NOTHING and let the legacy lane run."""
    return _AssembledBlock(fired=False, shape=None, subjects=(),
                           slices={}, admission={}, post_cap_lines=[])


def _assemble_relative_date(sdk, question: str, *, question_date: str | None,
                            caps: dict) -> _AssembledBlock:
    """#2655 — the calendar-anchored day lookup (the RELATIVE_DATE branch).

    anchor (resolved against the caller's ``question_date`` through the SHARED
    ``_norm_date`` — one date source, never a clock read here) -> one batched
    equality-day walk -> the day's own block. An unresolvable anchor, an
    ambiguous anchor set, or a day with NO rows all fall through to legacy
    byte-identically (P1-1: firing a bare dateline would REPLACE a working
    legacy pool and burn the one reader call).

    The tail is the SHARED one (`_decorate_real_rows` + `_assemble_under_caps`,
    the same helpers the #2652 window path uses), so the decorate/D8/W4/
    assemble stages have exactly one implementation.
    """
    from tortoise.retrieval import resolve_limit_from_caps
    limit = resolve_limit_from_caps(caps)
    anchor = resolve_calendar_anchor(question, _norm_date(question_date))
    if anchor is None:
        return _empty_block()
    slices = collect_day_slices(docker_walker_port(sdk), anchor.days,
                                per_day_cap=limit)
    if not slices.timeline_rows:
        return _empty_block()
    hits = synthesize_hits(slices, shape=AssemblyShape.RELATIVE_DATE,
                           date_anchor=anchor)
    hits = _decorate_real_rows(sdk, hits)
    selected, cap_stats = _assemble_under_caps(
        hits, caps=caps, question_date=question_date)
    if not selected:
        # P1-1: a day whose rows all fall outside the caps would replace a
        # working legacy pool with a dateline — fall through to legacy.
        return _empty_block()
    # the day IS the scope: no resolved subject rides on the block.
    return _AssembledBlock(
        fired=True, shape=AssemblyShape.RELATIVE_DATE.value, subjects=(),
        slices={"state_rows": [],
                "timeline_rows": list(slices.timeline_rows),
                "evidence_rows": list(slices.evidence_rows)},
        admission=dict(slices.admission), post_cap_lines=selected,
        cap_stats=cap_stats)


def _assemble_class_plural(sdk, question: str, *, question_date: str | None,
                           caps: dict) -> _AssembledBlock:
    """#8056 — class-plural temporal enumeration (the CLASS_PLURAL branch).

    window (resolved against the caller's ``question_date`` through the SHARED
    ``_norm_date`` — one date source, never a clock read here) -> the #2652
    window walk narrowed by the question's own class tokens -> the class's
    chronological block.

    Every abstention falls through to legacy byte-identically (the #2165 P1-1
    rule): an unresolvable window, an absent ``question_date``, a class span
    with no usable token, or a window whose rows carry NO class token at all.
    The last case is the shape's normal miss (the graph names the class
    differently than the question does), and firing a bare ``CLASS ORDER``
    header there would REPLACE a working legacy pool with a header and burn
    the one reader call.

    The tail is the SHARED one (`_decorate_real_rows` + `_assemble_under_caps`,
    the same helpers the #2652/#2655 no-subject paths use), so the decorate/
    D8/W4/assemble stages have exactly one implementation.
    """
    from tortoise.retrieval import resolve_limit_from_caps
    limit = resolve_limit_from_caps(caps)
    window = resolve_class_window(question, question_date)
    if window is None:
        return _empty_block()
    slices = collect_class_slices(docker_walker_port(sdk), window,
                                  per_window_cap=limit)
    if not slices.timeline_rows:
        return _empty_block()
    hits = synthesize_hits(slices, shape=AssemblyShape.CLASS_PLURAL,
                           question_date=question_date, anchor=window)
    if not hits:
        return _empty_block()
    hits = _decorate_real_rows(sdk, hits)
    selected, cap_stats = _assemble_under_caps(
        hits, caps=caps, question_date=question_date)
    if not selected:
        # P1-1: a class block whose rows all fall outside the caps would
        # replace a working legacy pool with a header — fall through.
        return _empty_block()
    # #8779 F1: the header is rendered BEFORE this cap tail, whose
    # skip-not-starve drop can remove later position/row hits — so re-derive
    # the member count the header names from what actually SURVIVED. A
    # truncated block then never claims a membership it does not list, and a
    # block whose rows were ALL cut falls through under the same P1-1 rule
    # (a header-only block is a content-less replacement).
    #
    # ⛔ Count the ROWS, not the position lines (#8779 F8). A `class-order`
    # hit is only the ordinal line ("3. 2026-08-21"); the row it points at is
    # the `point`/`event` hit that follows it, and the same drop can remove
    # the ROW while the tiny line survives. Counting lines therefore (a) let
    # the header claim a membership the block does not list, and (b) let a
    # CONTENT-LESS block FIRE — the header itself dropped, one orphan position
    # line left — with no header naming the scope and no row at all, replacing
    # a working legacy pool. The header is NOT always admitted, so its
    # presence is checked rather than assumed.
    header = next((h for h in selected
                   if h.get("kind") == "class-window"), None)
    # ⛔ ...and drop ORPHAN position lines (#8779 F10). The same drop can cut
    # the ROW while leaving the ordinal that names it, so the block would
    # present "96. 2026-08-14" with no row beneath it -- a member the block
    # claims but does not list. A position line is kept only when the row it
    # names survived immediately after it (the builder emits the pair
    # adjacently and the cap tail preserves order).
    pruned: list[dict] = []
    for idx, h in enumerate(selected):
        if h.get("kind") == "class-order":
            nxt = selected[idx + 1] if idx + 1 < len(selected) else None
            if nxt is None or nxt.get("kind") not in ("point", "event"):
                continue
        pruned.append(h)
    # ⛔ ...and re-derive the ORDINALS with the count (#8779 F14). The position
    # lines were numbered over the PRE-cap class order, so one dropped MIDDLE
    # row left the header claiming "N dated events" while the block's last
    # member was labelled with a HIGHER ordinal than N — the same
    # claims-what-it-does-not-list defect the count re-derivation fixed, still
    # visible in the labels themselves. Renumbered over the SURVIVING rows,
    # keeping the builder's tie rule (rows sharing a date share a position).
    positions: dict[str, int] = {}
    for h in pruned:
        if h.get("kind") != "class-order":
            continue
        text = str(h.get("content") or "")
        date_text = text.split(". ", 1)[1] if ". " in text else text
        if date_text not in positions:
            positions[date_text] = len(positions) + 1
        h["content"] = f"{positions[date_text]}. {date_text}"
    rendered_rows = sum(1 for h in pruned
                        if h.get("kind") in ("point", "event"))
    if header is None or not rendered_rows:
        return _empty_block()
    qd = _norm_date(question_date)   # narrowed for the header builder's type
    if qd is None:
        return _empty_block()
    header["content"] = _class_header_line(window, rendered_rows, qd)
    # the (window ∩ class) IS the scope: no resolved subject rides on the
    # block.
    return _AssembledBlock(
        fired=True, shape=AssemblyShape.CLASS_PLURAL.value, subjects=(),
        slices={"state_rows": [],
                "timeline_rows": list(slices.timeline_rows),
                "evidence_rows": list(slices.evidence_rows)},
        admission=dict(slices.admission), post_cap_lines=pruned,
        cap_stats=cap_stats)


def _assemble_connected(sdk, question: str, *, question_date: str | None = None,
                        caps: dict | None = None) -> _AssembledBlock:
    """The fired content pipeline (env-gated at the CALLER —
    run_ask_lane() reads
    TORTOISE_ASK_CONNECTED_ASSEMBLY BEFORE calling; this function assumes
    the gate already passed and fires when the question routes).

    classify -> resolve (both-halves gate R1) -> walk (typed slices) ->
    render (synthesize_hits) -> decorate real rows (annotate_ask_hits —
    synthesized rows have no id and are untouched) -> explicit
    why.enrich_items when W4 is on (R5/R17; id-less rows skipped by the
    guard) -> assemble_context(caps). NEVER raises untyped: the
    run_ask_lane() envelope maps any raise to AskRetrievalUnavailable.
    """
    from tortoise.retrieval import (
        resolve_limit_from_caps,
    )
    if caps is None:
        from tortoise.retrieval import resolve_ask_retrieval_caps
        caps = resolve_ask_retrieval_caps()

    shape = classify_question(question)
    if shape is None:
        return _AssembledBlock(fired=False, shape=None, subjects=(),
                               slices={}, admission={}, post_cap_lines=[])
    if shape is AssemblyShape.RELATIVE_DATE:
        # #2655: the DAY is the scope — there is no subject to resolve, so the
        # resolver/both-halves gate does not apply and the subject pipeline
        # below is entered UNCHANGED for every other shape.
        return _assemble_relative_date(sdk, question,
                                       question_date=question_date, caps=caps)
    if shape is AssemblyShape.AGO_RELATIVE:
        # #2652: the selector is the TIME WINDOW (question_date minus the
        # parsed offset), not an Object — so the R1 both-halves gate is
        # replaced by the window-resolved gate. An unparseable anchor /
        # absent question_date / a window with no dated row falls through
        # to legacy byte-identically (never an empty pool replacement).
        anchor = resolve_relative_anchor(question, question_date)
        if anchor is None:
            return _AssembledBlock(fired=False, shape=None, subjects=(),
                                   slices={}, admission={},
                                   post_cap_lines=[])
        limit = resolve_limit_from_caps(caps)
        slices = collect_window_slices(docker_walker_port(sdk), anchor,
                                       per_window_cap=limit)
        hits = synthesize_hits(slices, shape=shape,
                               question_date=question_date, anchor=anchor)
        hits = _decorate_real_rows(sdk, hits)
        selected, cap_stats = _assemble_under_caps(
            hits, caps=caps, question_date=question_date)
        if not selected:
            return _AssembledBlock(fired=False, shape=None, subjects=(),
                                   slices={}, admission={},
                                   post_cap_lines=[])
        return _AssembledBlock(
            fired=True, shape=shape.value, subjects=(),
            slices={"state_rows": [],
                    "timeline_rows": list(slices.timeline_rows),
                    "evidence_rows": list(slices.evidence_rows)},
            admission=dict(slices.admission), post_cap_lines=selected,
            cap_stats=cap_stats)
    if shape is AssemblyShape.CLASS_PLURAL:
        # #8056: the selector is WINDOW ∩ CLASS (neither is an Object), so
        # the R1 both-halves gate is replaced by the class-resolved gate —
        # same polarity as #2652's window gate. An unresolvable window, an
        # absent question_date, or a window whose rows carry no class token
        # falls through to legacy byte-identically.
        return _assemble_class_plural(sdk, question,
                                     question_date=question_date, caps=caps)
    terms = extract_subject_terms(question, shape)
    if not terms:
        return _AssembledBlock(fired=False, shape=None, subjects=(),
                               slices={}, admission={}, post_cap_lines=[])
    resolved = resolve_subjects(docker_resolver_port(sdk), terms, shape=shape)
    # #2653: the extracted term count IS the R1 width (an N-way list fires
    # only when all N subjects resolve). For the two-option and interval
    # shapes len(terms) == 2, so the legacy two-half contract is unchanged.
    if not resolved.both_halves_ok(shape, n_terms=len(terms)):
        # one half failed to resolve (or a both-subject shape lacks one) ->
        # the R1 gate: fire NOTHING, let legacy fall through
        return _AssembledBlock(fired=False, shape=None, subjects=(),
                               slices={}, admission={}, post_cap_lines=[])
    candidates = list(resolved.candidates)
    # ALL FOUR caps resolve through the validated dict-seam resolvers — an
    # absent/non-numeric/out-of-range entry falls back to the default exactly
    # as the env seam does, rather than reaching assemble_context and raising
    # (which would fail the whole ask). Sanitising only token/byte left this
    # the last unvalidated seam of the same class (#4105 review).
    limit = resolve_limit_from_caps(caps)
    slices = collect_slices(
        docker_walker_port(sdk), candidates, shape=shape,
        question_date=question_date,
        per_subject_cap=limit)
    verified = _probe_visible_successors(sdk, slices)
    hits = synthesize_hits(slices, shape=shape, candidates=candidates,
                           halves=terms, successors_verified=verified,
                           question_date=question_date)
    # decorate REAL rows via the SHARED helper (#2652: the window path uses
    # the same call, so marker/W4 parity is structural)
    hits = _decorate_real_rows(sdk, hits)
    # #4105 review fix: for a LEGACY caps dict (one that predates
    # ``context_byte_cap``) the token budget and the byte ceiling come from
    # ONE validated resolution — the byte ceiling is DERIVED from that dict's
    # token cap when neither the dict nor ``TORTOISE_ASK_CONTEXT_BYTE_CAP``
    # pins one (never the 32 KiB literal, which would re-open the silent
    # no-op on exactly this seam), and the token budget is the SAME sanitised
    # value. Precedence is explicit dict key → byte env → derived, matching
    # ``resolve_byte_cap_from_caps``' own contract. An absent key resolves the
    # same env knob the env seam uses, so a caps dict with no token cap
    # resolves like ``caps=None``. The budget tail is SHARED with the #2652
    # window path (``_assemble_under_caps``).
    selected, cap_stats = _assemble_under_caps(
        hits, caps=caps, question_date=question_date)
    if not selected:
        # P1-1: the assembly has NOTHING to say (content-less subjects, or an
        # empty day) — firing would replace legacy evidence with an empty
        # block and burn the ONE reader call on nothing. Fall through to
        # legacy (the R1 spirit: fire only when the fired block is a REAL
        # replacement).
        return _empty_block()
    subs = [{"object_id": c.object_id, "name": c.name,
             "confidence": c.confidence}
            for c in candidates]
    return _AssembledBlock(
        fired=True, shape=shape.value, subjects=tuple(subs),
        slices={"state_rows": list(slices.state_rows),
                "timeline_rows": list(slices.timeline_rows),
                "evidence_rows": list(slices.evidence_rows)},
        admission=dict(slices.admission), post_cap_lines=selected,
        cap_stats=cap_stats)
