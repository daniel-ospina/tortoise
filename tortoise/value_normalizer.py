"""S5 prose → value derivation (#2817) — money, abstention-first.

This is the **derive** half of the WS-C design
(``docs/plans/2026-09-10-ws-c-numeric-normalization.md``). Its sibling
``tortoise/money.py`` (#2782, PR #8641) is the **validate + consume** half:
it owns the flat field shape (``amountMinor``/``currency``/
``minorUnitExponent``/``verbatim``/``asOf``), the pinned ISO 4217 minor-unit
table, the Layer-1 refusal of a malformed amount, and the sum/ratio
arithmetic. **Nothing here rebuilds any of that** — this module turns raw text
into :func:`tortoise.money.money_props` and invents no second canonicaliser
(``docs/epics/2026-08-20-1509-extractor-v3/00-scope.md:28``).

## The decision this module is built to

> **OVERRIDES:** we do **not** ship a locale-aware numeric parser or a
> separator/locale pattern ladder (the industry default for money and number
> normalization) — the value is derived from, and kept beside, the raw
> sentence; a value that cannot be read unambiguously is not typed, and
> **detecting** a wrong reading is owned by the span-carrying slice, **#2684**.

The owner selected that Option (on ``#2817``, 2026-10-07; folded into design
§2.3 by #7645). Three consequences are load-bearing here:

1. **No separator rule, at all.** ``1.234``, ``1,234``, ``1.234,56``,
   ``1,234.56``, ``800.00`` and ``1 234`` are *all* dropped. There is no
   "PT-BR reads dots as thousands" rung and no "EN reads commas as thousands"
   rung to add one — deciding that ``R$ 1.234,56`` is ``1234.56`` **is** the
   locale question the design excludes. So the named regression is handled by
   the only safe route: ``R$ 1.234,56`` is **not typed at all** (design §6/T2
   as restated, §6/T3).
2. **No magnitude table.** ``milhão``/``bilhão``/``mil``/``k``/``M``/``MM``/
   ``bn``/``million`` are dropped as a class. ``M`` is 10³ in fixed-income
   notation and 10⁶ in SI, and ``_value_signature`` (``extractor_v2.py``)
   already reads a bare ``M`` as *metres* — a second interpretation of the
   same token is how a third opinion gets invented. (Design §3: bare ``M``
   magnitude is "dropped, not open".)

   ⚠️ **Recorded residual — the magnitude guard is a curated list of SPELLINGS,
   and unlike the separator guard it fails OPEN.** The separator/locale class is
   refused by a CATEGORY predicate (point 4); a magnitude word can only be
   recognised by its spelling, so an abbreviation nobody listed is read as
   prose and the bare integer types — ``R$ 800 mi`` printed R$ 800.00 for 800
   *million*, the 10⁶ understatement this module exists to prevent. The standard
   short forms of the families the corpus uses (EN ``mn``/``mln``/``bln``, EU
   ``Mio.``, PT-BR/ES ``mi``/``bi``/``millones``) are in the list; closing the
   class needs a lexicon this module does not have, and inventing one is the
   locale reasoning the decision above excludes. Stated rather than implied
   closed, exactly like the anchor-collision residual below.
3. **Only an explicit currency anchor types a value.** An ISO 4217 alpha-3
   code (``EUR 800``, ``800 EUR``) or a *disambiguated* dollar prefix
   (``R$``, ``US$``, ``CA$``, ``A$``/``AU$``, ``MX$``). A bare ``$`` is
   USD/CAD/AUD/BRL/MXN and is **never** a currency; a bare ``¥``/``€``/``£``
   is likewise refused; currency is never a graph default (design §3/OD5,
   §6/T4).

4. **Ambiguity is refused by CLASS, never by a list of known-bad characters.**
   A separator *of any kind* between digit groups abstains: the guard looks at
   what follows the token — through anything that is not alphanumeric — and
   drops when what follows is NUMERIC (``str.isnumeric``, i.e. the ``Nd``,
   ``Nl`` and ``No`` categories alike), so a middle dot, a hyphenation point, a
   bullet
   operator, an Arabic decimal separator — or a separator Unicode has not
   defined yet — lands on ``ambiguous_separator`` instead of ending the number
   at its first digit group. Extending a charset one character at a time is how
   the named 1000× class kept re-opening (review cycles 1–3). The same posture
   governs a RANGE: the connector is matched **word-bounded**, not by a ±6
   character window, so ``EUR 800 or roughly 900`` is a range rather than a
   typed lower bound; and only approximation words may stand between the
   connector and the second bound, so ``EUR 800 and USD 900`` stays two values
   rather than becoming a range. A bound is a bound on EITHER side of the
   amount, and one tuple builds both position regexes — ``above EUR 800`` /
   ``EUR 800 above``, ``some EUR 800``, ``maybe EUR 800`` are thresholds, not
   point values.

   The same posture refuses a currency **PAIR**: ``EUR/USD 800`` / ``800
   EUR/USD`` name no single currency for the amount, so neither leg is an
   anchor (both orders abstain as ``ambiguous_currency_symbol``). The guard is
   scoped to the anchor's neighbourhood, so ``800 EUR was committed while
   EUR/USD rallied`` still types.

   A RATIO or MULTIPLE abstains whether it is glued to the digits or one space
   away (``EUR 12%`` and ``EUR 12 %``, ``EUR 12x`` and ``EUR 12 x``): ``%``,
   ``‰``, ``percent``, ``pct``, ``bps`` land on ``not_money_percent`` and a bare
   ``x`` on ``not_money_multiplier``. Reading only the token made the same
   ratio decisive when attached and invisible one space later.

   ⚠️ **Recorded residual — an anchor can collide with prose, and a curated list
   cannot close that class.** Uppercase acronyms that are also active ISO 4217
   codes are refused as ``ambiguous_currency_symbol`` when they are listed
   (``IRR`` = internal rate of return, ``SAR`` = Special Administrative Region,
   ``AED`` = automated external defibrillator, beside the word-shaped
   ``ALL``/``PEN``/``TOP``/…). An acronym the list does not name still types a
   foreign currency (``the BHD 800 target``). Closing the class needs a context
   signal this module does not have — a bare amount is not one, and inventing
   one is the locale/context reasoning the decision above excludes. This is
   stated rather than implied closed; the abstain-on-ambiguous-anchor posture
   (§3/OD5, §6/T4) is only as wide as the list.

The values that survive are the unambiguous ones the design commits to: an
integer with **no separator and no magnitude word**, attached to an explicit
currency. Everything else stays prose — which is a *decision*, not a gap: the
raw sentence is untouched and the retention graders read prose only
(``tests/eval/write_path/schema.py``, ``tests/eval/harness/grading.py``), so
this module is additive and never moves a number out of ``content``/``quote``.

## Contract

* :func:`normalize_money` → :class:`Normalization` (``props`` or a
  ``drop_reason`` from the closed :data:`DROP_REASONS` set). Deterministic: no
  clock, no network, no process-global locale (design §6/T8).
* :func:`normalize_money_props` — the S5 call pattern, mirroring
  ``_valid_iso_date`` in ``extractor_v2.py``: returns ``None`` and *appends a
  warning* rather than guessing.
* Every ``verbatim`` is a **byte-exact substring of the source text**, so the
  value is recoverable from what was stored (design §6/T1) — normalize-then-
  lose-the-source is the silent-corruption path this exists to prevent.
* ``asOf`` is only an **asserted** date (``as of``/``as at``/``effective``),
  never an occurrence time: extracting a date from the sentence into
  ``Point.when``'s meaning would conflate the two (design §3/OD3, mode 8).

Wiring this into ``execute_embed`` is **not done here**, but the reason is no
longer the dependency it once was: the field's declaration on the Point
property lane **LANDED** (``ef405bd3e``, #2782 / PR #8976 — the projection's
Point property list now takes its money keys from ``MONEY_VALUE_FIELDS``), so
design §7.2 step 2 is satisfied and the ``pt_entry`` write is **unblocked**.
It is left to a follow-up because this PR's scope is the derivation alone: the
write path is a behaviour change on the live extraction lane and needs its own
review, not a rider on this one. (The older blocking reason — writing
undeclared props would set off the projection's "not declared" drift warning
on every rebuild, design §7.1 — no longer applies.)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from tortoise.money import (
    ISO_4217_MINOR_UNITS,
    MoneyValueError,
    money_props,
)

__all__ = [
    "DROP_REASONS",
    "Normalization",
    "normalize_money",
    "normalize_money_props",
]

#: The closed set of drop reasons — a reason is a *decision*, so the vocabulary
#: is fixed (a caller keys on these; an invented one is a typo, and
#: ``tests/test_value_normalizer_2817.py`` pins the set). Every reason means
#: "no typed value; the raw prose remains the record".
DROP_REASONS: frozenset[str] = frozenset({
    # nothing to read
    "no_candidate",           # no number in the text at all
    "no_currency_anchor",      # a number, but no explicit currency anchor
    "ambiguous_currency_symbol",  # bare $ / ¥ / € / £ — never a graph
                               # default; a currency PAIR's leg lands here too
    "not_an_iso4217_code",     # e.g. "800 XYZ" — looks like money, is not ISO
    # the number itself cannot be read without the excluded locale rule
    "ambiguous_separator",     # 1.234 / 1,234 / 1.234,56 / 800.00 / 1 234
    "magnitude_word",          # million / milhão / mil / k / M / MM / bn
    "not_money_percent",       # 12% — a valueKind outside this design (§4)
    "not_money_multiplier",    # 12x — a multiple, not an amount
    "range_or_qualifier",      # 0.5-2 / under / about / up to / post-money
    "signed_amount",           # -EUR 800 / EUR -800 — a sign convention this
                               # design does not define (OD register, §3)
    "year_shaped_amount",      # "the 2026 EUR/USD forecast" — a 4-digit
                               # 1900..2199 token is a year as often as an amount
    "unsupported_token",       # digits + something this parser will not guess
    # the value exists but cannot be canonically represented
    "no_minor_unit",           # XAU/XTS/... — the standard gives no exponent
    "multiple_candidates",     # two different amounts — never pick one
    "refused_by_layer1",       # money_props() refused the canonical value
})


@dataclass(frozen=True)
class Normalization:
    """One derivation result: either the canonical props, or a drop reason."""

    props: dict[str, Any] | None
    drop_reason: str | None = None
    detail: str = ""

    @property
    def typed(self) -> bool:
        return self.props is not None


# ── anchors ────────────────────────────────────────────────────────────────
#: A dollar prefix that names its currency. A BARE ``$`` is in none of these
#: on its own (mode 4): ``$`` ∈ {USD, CAD, AUD, BRL, MXN}. Only the forms the
#: text disambiguates are anchors.
_SYMBOL_ANCHORS: tuple[tuple[str, str], ...] = (
    (r"R\$", "BRL"),
    (r"US\$", "USD"),
    (r"CA\$", "CAD"),
    (r"AU?\$", "AUD"),   # A$ and AU$
    (r"MX\$", "MXN"),
)

#: Active ISO 4217 codes that are also ordinary English words. ``800 PEN
#: issues`` is prose, not Peruvian soles — and an uppercase-only match does not
#: help, because these ARE the uppercase spellings. They are refused as an
#: ambiguous currency rather than guessed (mode 4's posture, applied to the
#: word-shaped codes).
_WORD_CODES = frozenset({
    "ALL", "AMD", "BOB", "COP", "CUP", "GEL", "MAD", "MOP", "PEN", "PHP",
    "RON", "SOS", "TOP", "TRY",
})

#: Active ISO 4217 codes that are also everyday uppercase acronyms in business
#: prose — the same collision as ``_WORD_CODES``, one register up: ``the IRR 800
#: target`` is internal rate of return, ``the SAR 800 delegates`` is a Special
#: Administrative Region, ``AED 800 units`` is an automated external
#: defibrillator. Refused rather than guessed (mode 4's posture). ⚠️ This list
#: is CURATED and cannot close the class — the residual is stated in the module
#: docstring ("Recorded residual — an anchor can collide with prose").
_ACRONYM_CODES = frozenset({
    "AED", "IRR", "SAR",
})

#: Symbols that never become a currency on their own (mode 4 / design §6/T4).
_BARE_SYMBOLS = ("$", "¥", "€", "£")

#: An ISO 4217 alpha-3 code, uppercase ONLY. No case folding: ``all``, ``top``,
#: ``pen``, ``try``, ``bob``, ``cup``, ``mad``, ``gel`` and ``sos`` are ordinary
#: English words that are ALSO active ISO 4217 codes, so a lowercase or
#: case-insensitive match would mint a currency out of prose (tested).
_CODE = r"(?<![A-Z])[A-Z]{3}(?![A-Z])"

_SYM_BEFORE = "|".join(p for p, _ in _SYMBOL_ANCHORS)
_ANCHOR = rf"(?P<a>{_SYM_BEFORE}|{_CODE})"

#: A numeric token: digits, then only characters that can belong to one
#: (separators, and a short trailing word so ``8M``/``12x``/``800ms`` are
#: *seen* rather than silently split). ⚠️ This charset is only a *first* look:
#: the token does not have to END here, and :func:`_continues_as_number`
#: refuses a token whose continuation is any separator + numeral.
_NUM = (r"[\u2212+\-]?\d(?:[\d.,'\u00a0\u202f]{0,34}\d)?"
        r"[A-Za-z%\u2030]{0,8}")

_GAP = r"[ \t\u00a0\u202f]*"      # never a newline: see _GAP's note below
_ANCHOR_THEN_NUM = re.compile(rf"{_ANCHOR}{_GAP}(?P<n>{_NUM})")
_NUM_THEN_ANCHOR = re.compile(rf"(?P<n>{_NUM}){_GAP}{_ANCHOR}")

_MAGNITUDE_SUFFIX = re.compile(r"(?i)(?:k|m|mm|bn|tn|b|trn)$")
#: The magnitude vocabulary — ⚠️ a CURATED list of spellings, not a class, and
#: unlike every other guard here it fails OPEN: an unlisted abbreviation opens
#: the alphabetical stop in :func:`_has_magnitude_word_after` and the bare
#: integer types (``R$ 800 mi`` → R$ 800.00 for 800 MILLION). The standard
#: short forms of the three spellings families the corpus uses (EN ``mn``/
#: ``mln``/``bln``, EU ``Mio.``, PT-BR/ES ``mi``/``bi``/``millones``) are in;
#: the residual is stated in the module docstring rather than implied closed.
_MAGNITUDE_WORD = re.compile(
    r"(?i)^(?:thousand|thousands|million|millions|millon|millón|millones|"
    r"billion|billions|billon|billón|billones|trillion|trillions|"
    r"mil|milhar|milhares|milhao|milhão|milhoes|milhões|"
    r"bilhao|bilhão|bilhoes|bilhões|mille|"
    r"mns|mlns|mln|mn|mios|mio|mi|bn|bln|bio|bil|bill|bi|tn|trn|"
    r"thou|th|cr|mm|[kmb])$")

#: A range CONNECTOR: a dash, a slash, or the word connectors. Matched
#: word-bounded — a distance window (the previous ±6 characters) missed
#: ``EUR 800 or roughly 900`` and typed the lower bound (review cycle 3), while
#: A sentence-wide search dropped ``EUR 800 covers 3-4 hires`` (cycle 1).
#: ``up to``/``through``/``until`` are connectors, not qualifiers (cycle 5: they
#: sat only in the PRE vocabulary, so ``EUR 800 up to 900`` typed the lower
#: bound as an exact amount). Longer alternatives precede their own prefixes
#: (``up\s+to`` before ``\bto\b``). ``vs``/``versus`` compare two bounds the
#: same way: a comparison is not a point value either, so they are connectors.
_RANGE_CONNECTOR = (
    r"(?:-|–|—|/|\bup\s+to\b|\bthrough\b|\bthru\b|\buntil\b|\bto\b|"
    r"\band\b|\bor\b|\bvs\.?|\bversus\b)")

#: The only words a range connector may carry before its second bound
#: ("or **roughly** 900", "to **about** 900"). Deliberately an allowlist of
#: approximation words, not ``[A-Za-z]+``: ``EUR 800 and USD 900`` is TWO
#: values (``multiple_candidates``), not a range — a currency anchor is not
#: filler.
_RANGE_FILLER = (
    r"(?:roughly|about|approximately|approx\.?|around|circa|nearly|almost|"
    r"maybe|perhaps|some|estimated|an?\s+estimated|exactly|just|only|"
    r"up\s+to|at\s+(?:least|most))")

#: A connector, then (optionally) one approximation word, then the other bound.
_RANGE_AFTER = re.compile(
    rf"(?i)[\s,;:]*{_RANGE_CONNECTOR}\s*,?\s*"
    rf"(?:{_RANGE_FILLER}\s*,?\s*)?\d")

#: The mirror: a bound, a connector, (optionally) an approximation word, then
#: the amount under test (``800 to 900 EUR`` — the upper bound is a candidate).
_RANGE_BEFORE_TAIL = re.compile(
    rf"(?i)\d[\d.,'\u00a0\u202f]*\s*,?\s*{_RANGE_CONNECTOR}\s*,?\s*"
    rf"(?:{_RANGE_FILLER}\s*,?\s*)?$")

#: How far back a connector may sit before an amount (chars). The pattern is
#: end-anchored, so this only bounds a pathological scan.
_RANGE_LOOKBEHIND = 48

#: A currency PAIR — two ISO legs joined by a slash (``EUR/USD``, ``US$/BRL``).
#: Each half is a valid anchor on its own, but an amount written beside a pair
#: belongs to the PAIR and names no single currency: ``EUR/USD 800`` typed
#: 800.00 USD and ``800 EUR/USD`` typed 800.00 EUR before this guard (review
#: finding, P2). Refused in BOTH orders as ``ambiguous_currency_symbol`` — a
#: false type is the one outcome this module may never produce.
_PAIR_LEG = rf"(?:{_SYM_BEFORE}|[A-Z]{{3}})"
_PAIR_TAIL = re.compile(rf"{_GAP}/{_GAP}{_PAIR_LEG}(?![A-Z])")
_PAIR_HEAD = re.compile(rf"(?<![A-Z]){_PAIR_LEG}{_GAP}/{_GAP}\Z")

_DATE_SPAN = re.compile(r"\d{4}-\d{2}-\d{2}")
#: Anything that LOOKS like a date is masked before the range check: a
#: malformed date ("2026-6-30") must not make its sentence's amount look like
#: a range, and a date range is not an amount range.
_DATE_LIKE = re.compile(r"\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}")

#: Bound / threshold phrases. ⛔ ONE vocabulary for BOTH positions (review
#: cycle 5): a phrase that qualifies an amount in front of it must qualify it
#: behind it too, or the class re-opens one instance at a time — ``up to EUR
#: 800`` abstained while ``EUR 800 or above`` typed the bound as an exact
#: amount, and ``EUR 800 up to 900`` typed the lower bound. Both regexes below
#: are built from this tuple, so a phrase cannot be added to one side only, and
#: every phrase only ever abstains MORE — the list cannot create a false type.
#: ⛔ This tuple is the UNION of the two former hand-kept lists. A transcription
#: that drops an entry is a NARROWING that types a bound as an exact amount, and
#: it has already happened once (cycle 6: `roug\s*hly` survived only in one list,
#: so ``roug hly EUR 800`` — the word wrapped across a line — typed 800.00).
#: Keep the wrap-tolerant spellings (`\s` where the source can break a word).
_BOUND_PHRASES: tuple[str, ...] = (
    r"under", r"over", r"about", r"around", r"approximately", r"approx\.?",
    r"roughly", r"roug\s*hly", r"nearly", r"almost", r"estimated", r"est\.?",
    r"circa",
    r"exactly", r"between", r"up\s+to", r"at\s+least", r"at\s+most",
    r"more\s+than", r"less\s+than", r"no\s+more\s+than", r"no\s+less\s+than",
    r"give\s+or\s+take", r"in\s+excess\s+of", r"north\s+of", r"upwards\s+of",
    r"min", r"max", r"minimum", r"maximum", r"or\s+less", r"or\s+more",
    r"or\s+so", r"or\s+above", r"or\s+higher", r"or\s+greater", r"and\s+up",
    r"and\s+above", r"and\s+more", r"and\s+higher", r"and\s+greater",
    r"post-money", r"pre-money", r"postmoney", r"premoney", r"plus",
    r"-?ish",
    # ⛔ The STANDALONE comparatives and the approximation words the filler list
    # already knew: ``above EUR 800`` typed the threshold as an exact amount
    # while ``or above`` abstained, and ``some EUR 800`` typed while ``an
    # estimated EUR 800`` abstained — the same one-vocabulary inconsistency the
    # cycle-5/6 fixes closed one pair of tuples over, so it is closed here too.
    r"above", r"below", r"some", r"maybe", r"perhaps",
)

#: Phrases that qualify only when they PRECEDE the amount: they read as a bound
#: in front (``from EUR 800``, ``starting at EUR 800``) but are ordinary prose
#: behind it (``EUR 800 from the fund``), so putting them on BOTH sides would
#: abstain on an exact amount.
_PRE_ONLY_PHRASES: tuple[str, ...] = (r"from", r"starting\s+at")

_BOUND_ALT = "|".join(_BOUND_PHRASES)
_PRE_ALT = "|".join(_PRE_ONLY_PHRASES)

_QUALIFIER_BEFORE = re.compile(
    rf"(?i)(?:\b(?:{_BOUND_ALT}|{_PRE_ALT})\b\.?"
    # "approx." / "est." carry their dot in the PRE position too — the
    # asymmetry with the post-position form (which accepted "approx.") is
    # what let the common abbreviation type as exact (review cycle 3).
    r"(?:\s*,\s*(?:say|again))?"     # "about, say, EUR 800"
    r"[\s,]*$"
    r"|[~≈≤≥<>+±]\s*$)")

_QUALIFIER_AFTER = re.compile(
    rf"(?i)^[\s,;:(\[\u2014\u2013-]*(?:{_BOUND_ALT}|±|\+)")

#: A RATIO / MULTIPLE stated one space away from the amount (``EUR 12 %``,
#: ``EUR 12 percent``, ``EUR 800 x 2``). ``_NUM`` accepts ``%``/``‰``/``x`` only
#: when ATTACHED to the digits, so mode 3 was enforced for ``EUR 12%`` and one
#: space reopened it — a ratio typed as 12.00 EUR (review P2). The
#: no-candidate branch already treats a ``%`` ANYWHERE in the text as decisive,
#: so reading the same signal in the anchored branch is one policy, not two.
_RATIO_AFTER = re.compile(
    r"(?i)^[\s\u00a0\u202f]*(?:(?P<pct>%|\u2030|percent\b|pct\b|bps\b|pp\b)"
    r"|(?P<mult>x\b))")

_YEAR_SHAPED = re.compile(r"(?:19|20|21)\d{2}")

_AS_OF = re.compile(
    r"(?i)\b(?:as\s+of|as\s+at|as-of|effective|valid\s+as\s+of)\s+"
    r"(?P<d>\d{4}-\d{2}-\d{2})\b")

_TRAILING_PUNCT = ".,;:!?)]}\"'"

#: A minus/plus/bracket immediately before a LEADING number, optionally spaced.
_SIGN_BEFORE = re.compile(r"[-\u2212+(]\s*$")

#: ⛔ THE decision, inverted, as a CATEGORY predicate: skip everything after the
#: token that is not alphanumeric, and if what follows is NUMERIC the number did
#: not end at the token — so the token is a truncated first group, not an amount.
#: A charset of known separators is what let U+00B7/U+2027/U+2219/U+2010/U+066B
#: (and the next one Unicode adds) silently type ``1.234`` as ``1``; whitespace
#: was an escape hatch for the same class (``EUR 1 .234``, ``EUR 1 _234``, a
#: number broken across a newline all typed ``1.00`` — review cycle 4); and a
#: ``\\d``-only terminator still left it open for the NON-DECIMAL numerals in
#: Unicode categories ``Nl``/``No`` (``EUR 2½ million`` → ``2.00``, ``EUR 1¼`` →
#: ``1.00`` — review cycle 5), because ``\\d`` is ``Nd`` only. ``str.isnumeric``
#: covers all three, so the predicate cannot be reopened by a new numeral.
#: The cost is a deliberate over-abstention (a sentence-final period followed by
#: a spaced digit, ``EUR 800. 3 hires``, abstains too), which is the safe
#: direction this design always takes.
def _continues_as_number(rest: str) -> bool:
    """Whether a number RESUMES after ``rest``'s leading non-alphanumerics."""
    i = 0
    while i < len(rest) and not rest[i].isalnum():
        i += 1
    return i < len(rest) and rest[i].isnumeric()


def _drop(reason: str, detail: str = "") -> Normalization:
    assert reason in DROP_REASONS, f"unknown drop reason {reason!r}"
    return Normalization(None, reason, detail)


@dataclass(frozen=True)
class _Candidate:
    anchor: str
    token: str
    start: int          # first character of the NUMBER (the verbatim's start)
    end: int            # one past the number
    span_start: int     # first character of the whole anchored form
    span_end: int       # one past the whole anchored form
    anchor_first: bool  # the currency precedes the number ("EUR 800")


def _mask_dates(text: str) -> str:
    """Blank every date-like span, preserving length and positions.

    A date component is not an amount: without this, ``as of 2026-03-31 EUR
    800`` yields the candidate ``31 EUR`` beside ``EUR 800`` (two "amounts",
    so abstention on a perfectly clear sentence), and ``EUR 2026-03-31`` types
    ``2026`` as euros.
    """
    return _DATE_LIKE.sub(lambda m: " " * len(m.group(0)), text)


def _candidates(text: str) -> list[_Candidate]:
    """Every anchored money FORM in ``text``, deduped by its numeric span."""
    found: dict[tuple[int, int, int], _Candidate] = {}
    for rx in (_ANCHOR_THEN_NUM, _NUM_THEN_ANCHOR):
        for m in rx.finditer(text):
            anchor = m.group("a")
            tok = m.group("n")
            start = m.start("n")
            end = start + len(tok)
            found[(start, end, m.start("a"))] = _Candidate(
                anchor=anchor, token=tok, start=start, end=end,
                span_start=m.start(), span_end=m.end(),
                anchor_first=m.start("a") < start)
    return sorted(found.values(), key=lambda c: (c.start, c.end, c.anchor))


def _currency_for(anchor: str) -> str | None:
    """The ISO code an anchor names, or ``None`` when it names none."""
    for pattern, code in _SYMBOL_ANCHORS:
        if re.fullmatch(pattern, anchor):
            return code
    return anchor if anchor in ISO_4217_MINOR_UNITS else None


def _has_magnitude_word_after(text: str, end: int) -> bool:
    """A magnitude word attached to THIS amount (not a neighbouring one).

    Scanning just ``nxt[0]`` hid the word behind one numeral/separator token
    (``EUR 2½ million``, ``EUR 800 - million``), so this walks forward — but only
    across words with no alphabetic content, because a word with letters opens a
    DIFFERENT quantity's phrase: ``EUR 800 for 2 million users`` is an exact
    amount and must keep typing (review cycle 6, P2: widening the scan without
    this stop turned that clear sentence into ``magnitude_word``).
    """
    for word in text[end:end + 40].split():
        w = word.strip(".,;:")
        if _MAGNITUDE_WORD.match(w):
            return True
        if any(ch.isalpha() for ch in w):
            return False
    return False


def _is_currency_pair_leg(text: str, cand: _Candidate) -> bool:
    """Whether this candidate's ANCHOR is one leg of a currency pair.

    Both sides of the anchor are checked because either leg can be the one the
    anchor↔number adjacency resolved: ``EUR/USD 800`` resolves ``USD`` (the
    pair is BEHIND the anchor), while ``800 EUR/USD`` resolves ``EUR`` (the
    pair is AHEAD of it). A pair names no single currency, so neither leg is
    the amount's currency.
    """
    if cand.anchor_first:
        anchor_start = cand.span_start
        anchor_end = cand.span_start + len(cand.anchor)
    else:
        anchor_start = cand.span_end - len(cand.anchor)
        anchor_end = cand.span_end
    return bool(_PAIR_TAIL.match(text, anchor_end)
                or _PAIR_HEAD.match(text[:anchor_start]))


def _has_range(text: str, candidates: list[_Candidate]) -> bool:
    """Whether an amount RANGE touches one of the anchored forms.

    Word-bounded, not distance-bounded: a connector (``-``/``to``/``and``/
    ``or``/``/``) counts when it sits between the amount and the other bound,
    optionally with ONE approximation word in between — so ``EUR 800 or
    roughly 900`` is a range (the previous ±6-character window missed it and
    typed 800), while an unrelated hyphenated pair elsewhere in the sentence
    (``EUR 800 covers 3-4 hires``) is not one, because no connector sits
    between the amount and a bound. The scan is anchored at the end of the
    whole form, so an amount whose anchor FOLLOWS it (``800 EUR to 900``) is
    covered too.
    """
    masked = _mask_dates(text)
    for cand in candidates:
        # ⛔ The connector sits after the whole anchored FORM, not after the
        # number: ``800 EUR to 900`` (the anchor FOLLOWS the amount) ended the
        # scan at the number, saw " EUR to 900", and typed the lower bound as
        # a point value — the very false type the word-bounded rule exists to
        # prevent. ``span_end == end`` for an anchor-first form, so testing
        # both covers either order and costs nothing.
        if _RANGE_AFTER.match(masked, cand.end) \
                or _RANGE_AFTER.match(masked, cand.span_end):
            return True
        before = masked[max(0, cand.start - _RANGE_LOOKBEHIND):cand.start]
        if _RANGE_BEFORE_TAIL.search(before):
            return True
    return False


def _classify_token(token: str, *, text: str, end: int,
                    detail: str) -> Normalization | None:
    """Classify one numeric token; ``None`` means "a plain integer".

    ``detail`` is the whole anchored form, so a warning quotes what the reader
    actually saw ("R$ 1.234,56"), not a fragment of it.
    """
    t = token.strip().rstrip(_TRAILING_PUNCT)
    if not t:
        return _drop("unsupported_token", detail)
    if t[0] in "-\u2212+":
        return _drop("signed_amount", detail)
    if _MAGNITUDE_SUFFIX.search(t) or _MAGNITUDE_WORD.match(t) \
            or _has_magnitude_word_after(text, end):
        # No magnitude table (design §3: bare M is "dropped, not open").
        return _drop("magnitude_word", detail)
    if "%" in t or "\u2030" in t:
        return _drop("not_money_percent", detail)   # §4: no valueKinds
    if re.search(r"(?i)x$", t):
        return _drop("not_money_multiplier", detail)
    if re.search(r"[.,'\u00a0\u202f]", t):
        # ⛔ THE decision, in one line: any separator at all is ambiguous
        # without the locale rule the design excludes. 1.234 / 1,234 /
        # 1.234,56 / 1,234.56 / 800.00 / 1 234 all land here — never a guess.
        return _drop("ambiguous_separator", detail)
    if not t.isdigit() or len(t) > 18:
        # 19+ digits is not a plausible amount, and Python 3.12 refuses
        # int("9"*4300) outright — abstain instead of raising.
        return _drop("unsupported_token", detail)
    if _continues_as_number(text[end:]):
        # "BRL 1 234" / "EUR 1·234" / "EUR 2½" — the number does not end at the
        # token (mode 1). Any separator at all, not just the known ones, and any
        # numeral category, not just ``Nd``.
        return _drop("ambiguous_separator", text[end - 8:end + 12].strip())
    return None


def _as_of(text: str) -> str | None:
    """The ASSERTED date, or ``None`` — never an occurrence time (OD3/mode 8).

    Only an explicit assertion marker counts, so ``on 2026-03-31 we committed
    EUR 800`` yields no ``asOf``: that date is the occurrence time
    (``Point.when``), and reusing it as assertion validity is the mode-8
    error. ``money_props`` then validates the ISO shape at Layer 1.
    """
    dates = {m.group("d") for m in _AS_OF.finditer(text)}
    if len(dates) > 1:
        return None          # several asserted dates — never pick one
    return next(iter(dates)) if dates else None


def normalize_money(text: str) -> Normalization:
    """Derive the canonical money value from one raw sentence.

    Returns the money props (:func:`tortoise.money.money_props`, so the value
    is already Layer-1 valid) or a :class:`Normalization` carrying a reason
    from :data:`DROP_REASONS`. Never guesses, never mutates the text, and
    never emits a float: ``amountMinor`` is an int at the currency's pinned
    exponent (``JPY``=0, ``BRL``/``USD``/``EUR``=2, ``KWD``=3).
    """
    if not isinstance(text, str) or not text.strip():
        return _drop("no_candidate")

    masked = _mask_dates(text)
    candidates = _candidates(masked)
    if not candidates:
        if not any(ch.isdigit() for ch in text):
            return _drop("no_candidate", text[:60])
        if "%" in text:
            return _drop("not_money_percent", text[:60])
        if re.search(r"(?i)\d[a-z]*x\b", text):
            return _drop("not_money_multiplier", text[:60])
        if any(_MAGNITUDE_WORD.match(w.strip(".,;:"))
               for w in text.split()):
            return _drop("magnitude_word", text[:60])
        if any(sym in text for sym in _BARE_SYMBOLS):
            return _drop("ambiguous_currency_symbol", text[:60])
        return _drop("no_currency_anchor", text[:60])

    if _has_range(text, candidates):
        return _drop("range_or_qualifier", text[:60])

    props: list[dict[str, Any]] = []
    as_of = _as_of(text)          # once, not per candidate
    unresolved_anchor: str | None = None
    for cand in candidates:
        before = text[max(0, cand.span_start - 24):cand.span_start]
        after = text[cand.span_end:cand.span_end + 24]
        if _QUALIFIER_BEFORE.search(before) or _QUALIFIER_AFTER.search(after):
            return _drop("range_or_qualifier", text[:60])
        ratio = _RATIO_AFTER.match(after)
        if ratio is not None:
            # §4: a ratio/multiple is not an amount, whether it is glued to the
            # digits or one space away (review P2).``after`` starts at the end
            # of the anchored FORM, so an anchor-last amount is covered too.
            return _drop(
                "not_money_percent" if ratio.group("pct")
                else "not_money_multiplier",
                text[cand.span_start:cand.span_end + 12].strip())
        # A sign the design defines no convention for (mode 5/§3): "-EUR 800"
        # must not be typed as +800.00 — a negative typed as a positive is the
        # worst false type available, so an adjacent sign abstains.
        if cand.anchor_first:
            # ⛔ The SAME probe as the anchor-last branch below, one token
            # further out: a sign in front of the CURRENCY is the same sign
            # when it is spaced, and the two word orders of one sentence
            # cannot get opposite policies. The previous ONE-character
            # lookbehind read "- EUR 800" as +800.00 while "- 800 EUR" — the
            # same negation, reordered — abstained, i.e. a negative typed as a
            # positive, which is the worst false type available (review P1).
            # Side effect, deliberately: a prose dash ("pilot - EUR 800") now
            # abstains too, exactly as "pilot - 800 EUR" already did — the
            # design's abstain-first direction, and one policy instead of two.
            signed = bool(_SIGN_BEFORE.search(
                text[max(0, cand.span_start - 4):cand.span_start]))
        else:
            # the number leads: a minus before it IS a sign even when spaced
            # ("- 800 EUR"), and reading it as +800.00 is the worst false type.
            signed = bool(_SIGN_BEFORE.search(
                text[max(0, cand.start - 4):cand.start]))
        if signed:
            return _drop("signed_amount",
                         text[max(0, cand.span_start - 2):cand.span_end])
        if cand.anchor in _WORD_CODES or cand.anchor in _ACRONYM_CODES:
            return _drop("ambiguous_currency_symbol", cand.anchor)
        currency = _currency_for(cand.anchor)
        if currency is None:
            # "EUR 800 VAT inclusive": an uppercase neighbour that is not a
            # currency must not discard an amount another candidate resolved.
            unresolved_anchor = unresolved_anchor or cand.anchor
            continue
        exponent = ISO_4217_MINOR_UNITS.get(currency)
        if exponent is None:
            return _drop("no_minor_unit", currency)
        classified = _classify_token(
            cand.token, text=text, end=cand.end,
            detail=text[cand.span_start:cand.span_end].strip())
        if classified is not None:
            return classified
        if _YEAR_SHAPED.fullmatch(cand.token):
            # ⛔ FULL match: the rule is a 4-digit token in 1900..2199, and a
            # PREFIX test dropped every amount whose first four digits happen
            # to look like a year — ``USD 2000000`` (2 million), ``USD 190000``,
            # ``EUR 2100000`` — a silent over-abstention on the commonest
            # magnitudes in business prose (review P2).
            return _drop("year_shaped_amount", cand.token)
        if _is_currency_pair_leg(text, cand):
            # ``EUR/USD 800`` — the amount belongs to the PAIR, which names no
            # single currency, so neither leg is the amount's currency (review
            # finding, P2: this typed 800.00 USD / 800.00 EUR). Checked AFTER
            # the year test so a year-shaped token keeps its more specific
            # reason and the ordering change cannot reopen that class.
            return _drop("ambiguous_currency_symbol",
                         text[cand.span_start:cand.span_end].strip())
        token = cand.token.strip().rstrip(_TRAILING_PUNCT)
        verbatim = text[cand.start:cand.start + len(token)]
        try:
            amount_minor = int(token) * (10 ** exponent)
        except (ValueError, OverflowError):  # unreachable guard, kept explicit
            return _drop("unsupported_token", verbatim)
        try:
            props.append(money_props(
                amount_minor, currency, minor_unit_exponent=exponent,
                verbatim=verbatim, as_of=as_of))
        except MoneyValueError as exc:
            # Unreachable by construction TODAY — every refusal money_props can
            # raise is pre-empted above (the anchor is a table member, the
            # exponent IS the table's, ``no_minor_unit`` returns early, the
            # amount is an int, ``verbatim`` is non-empty by ``_NUM``'s grammar,
            # ``asOf`` comes from the asserted-date shape). Kept explicit, like
            # the int() guard below, because ``money.py`` is a LIVE contract that
            # tightened once already (#8976): a Layer-1 refusal must land on an
            # abstention, never on an unhandled exception.
            return _drop("refused_by_layer1", str(exc))

    if not props:
        return _drop("not_an_iso4217_code", unresolved_anchor or text[:60])
    first = props[0]
    if any(p != first for p in props[1:]):
        # Two different amounts in one sentence: picking one is a guess
        # (design §4: multi-value points are out of scope).
        return _drop("multiple_candidates", text[:60])
    return Normalization(props=first)


def normalize_money_props(text: str, *,
                          warnings: list[str] | None = None) -> dict | None:
    """The S5 call pattern: props, or ``None`` + a warning (never a guess).

    Mirrors ``_valid_iso_date``'s posture in ``extractor_v2.py``: a caller
    that cannot type the value drops it and says so, so an abstention is
    visible in the payload instead of being silent.
    """
    result = normalize_money(text)
    if result.typed:
        return result.props
    if warnings is not None:
        warnings.append(
            f"value-normalizer: no typed money value ({result.drop_reason}) "
            f"for {result.detail!r} — prose only")
    return None


#: Fields a caller may rely on this module never writing: the derivation is
#: additive, and prose is never moved out of ``content``/``quote`` (design §4
#: "Prose is load-bearing"; the retention graders read prose only).
PROSE_FIELDS: frozenset[str] = frozenset({"content", "quote", "when"})
