"""#2684 (Slice B) — the value-span census on the SEALED corpus.

The T's per-question leg is *"the value-bearing evidence is verbatim-present"*
(wave scoping §6). That leg has two halves that this file separates:

* **the mechanism** — a value-bearing point carries a span, and re-reading the
  span yields text that CARRIES the value. Landed in #8659 (stamp) + #8966
  (a verbatim quote is not yet an address to the value). Pinned hermetically in
  ``tests/test_extractor_v2.py::TestValueVerbSpan2684``.
* **the population** — how often the shipped mechanism actually stamps one on
  real extracted points, and whether every absence is a REPORTED class rather
  than a silent drop. That is what this file measures, and it is the part that
  no unit fixture can answer: the population is the model's own extractions.

**Method — zero paid calls.** The measurement reads committed artefacts only:

* extracted points: ``receipts/7947-arms-2026-10-18/<session>.arms.json``
  (7 sealed sessions x 2 arms, real ``deepseek-v4-flash`` S2 extractions),
* the turns the quotes must anchor in:
  ``fixtures/<session>.json`` → ``conversation`` (index = turn id),
* the resolution + the predicate + the stamp under test: the SHIPPED
  ``tortoise.extractor_v2._resolve_source_turn`` / ``_carries_value`` /
  ``_value_verbatim_span``, called in the SHIPPED ORDER (resolve, then stamp
  against the resolved turn). **The two stamp-side inputs are the ones
  production hands it** — content cut to ``_MAX_CONTENT`` and the quote to
  ``_MAX_QUOTE``, both at a TOKEN boundary (``extractor_v2.py:10051`` /
  ``:10119``), while the resolver gets the raw point (``:10342``). See
  ``_stamp_inputs``.

⛔ **The resolver is not optional in this census.** The receipt's
``source_turn_id`` is the model's ADVISORY index; the write path resolves the
turn from the quote first and never lets a wrong model index win
(``_resolve_source_turn`` docstring; call site ``extractor_v2.py:10342`` →
``:10367``). Passing the receipt field straight through measures a population
production never produces *and* never fires the resolver's own E3 anchor
warning — it under-counts ``no_turn`` and over-counts ``band`` (measured: 3
points, all ``wp06_quarry_rollout``/``narrative``, which the resolver reports
as unresolvable). This is pinned by
``test_the_census_resolves_the_turn_the_way_the_write_path_does``.

The receipts carry **no** span key, so the census is a **re-derivation**, not an
observation: it answers *"what would the shipped mechanism stamp on these
committed points?"*. The structural reason is that the measuring harness
persists the S2 *model* output (``tests/eval/write_path/raw_vs_narrative.py`` →
``ev2.run_s2(...)``), which is pre-``pt_entry``; the span is stamped later, when
``pt_entry`` is built (``extractor_v2.py:10343-10350``, stamped at
``:10367-10370``). It is NOT a question of
dates — the receipts (committed 2026-10-19, ``ecc7bc8ca8``) postdate #8659
(``2312845b6``, 2026-10-14) — so no re-ingest through that harness can ever put
a span key in a receipt.
``test_the_census_is_a_rederivation_not_an_observation`` pins that reading so
the number is never mistaken for a measured extraction run. (Re-measuring the
OBSERVED rate needs an end-to-end run — wave scoping §6's T-2, re-homed to
#8486; see ``docs/runbook/2684-slice-b-t-gate.md``.)

**What the numbers are NOT.** No reader, no judge, no answer accuracy. The
census counts whether a value can be re-read verbatim from an address; it says
nothing about whether the reader then sums it correctly.

Hermetic: no DB, no network, no model. Reads committed JSON only — plus one
`git ls-files` call per bare-basename cite in the FINAL sweep (it needs a `git`
binary and a readable git dir; the sweep says so BY NAME when it cannot ask, so
an environment failure is never read as a broken cite).
"""

from __future__ import annotations

import ast
import io
import itertools
import json
import re
import subprocess
import tokenize
from pathlib import Path

from tortoise import extractor_v2 as v2
from tortoise.retrieval import (
    DEFAULT_PACKAGE_SAME_TURN_OVERLAP,
    DEFAULT_PACKAGE_VERBATIM_OVERLAP,
    _pkg_differ_value_critical,
    _pkg_overlap,
    _pkg_turn_key,
)

WRITE_PATH = Path(__file__).resolve().parent
COMMITTED_ARMS = WRITE_PATH / "receipts" / "7947-arms-2026-10-18"
SESSIONS = (
    "wp01_quarry_debug",
    "wp02_lumen_refactor",
    "wp03_ember_design",
    "wp04_aurora_perf",
    "wp05_retro_writeup",
    "wp06_quarry_rollout",
    "wp07_bluepeak_followup",
)
ARMS = ("raw", "narrative")

#: Absence classes. Every one is REPORTED at the point of absence — measured by
#: ``test_every_absence_in_the_census_is_REPORTED`` over the whole committed
#: population, never a silent drop of something that could have been addressed:
#:  * ``band``            — resolved only by the >=0.6 token-overlap band, so
#:                          the quote's words are not in the turn. Reported by
#:                          ``_value_verbatim_span`` (the band route is
#:                          otherwise silent, and it misses by construction
#:                          exactly when the value was REWRITTEN — #2817).
#:  * ``valuefree_quote`` — the quote IS verbatim but addresses no value (the
#:                          model quoted the context sentence; the number lives
#:                          elsewhere in the turn). Reported by
#:                          ``_value_verbatim_span`` (#8966).
#:  * ``no_turn``         — no source turn resolved at all. Reported by the E3
#:                          anchor warning at the resolution site
#:                          (``_resolve_source_turn``), not by this function.
#:  * ``empty_quote``     — no quote, so no verbatim text exists to address, and
#:                          ``_value_verbatim_span`` says nothing about it (its
#:                          warning branch requires a non-empty quote). The
#:                          absence is still REPORTED — by the RESOLVER, which
#:                          the census runs FIRST: "unverified (empty quote) —
#:                          using it" when the model index is in range,
#:                          "no resolvable source turn" when it is not (this
#:                          corpus's single point carries no index). Calling this
#:                          class "silent" contradicts the shipped order; it is
#:                          the class with no SPAN-side warning, not an
#:                          unreported one. Span shape pinned in
#:                          ``test_extractor_v2.py::test_empty_quote_model_index_path_carries_no_span``.
ABSENCE_CLASSES = ("band", "valuefree_quote", "no_turn", "empty_quote")

#: The message each class's absence report must carry, so the census proves it
#: sees the RIGHT warning rather than any warning at all. ``empty_quote`` has two
#: legal forms (both from the resolver — see the class note above).
EXPECTED_ABSENCE_WARNINGS = {
    "band": ("token-overlap band",),
    "valuefree_quote": ("carries no value",),
    "no_turn": ("no resolvable source turn",),
    "empty_quote": ("unverified (empty quote)", "no resolvable source turn"),
}

#: Every absence in this census is reported — the zero the runbook §2.2 and the
#: ``docs/00_index.md`` row publish. A NON-zero value here means a class stopped
#: reporting, i.e. a counted outcome became a silent drop; re-measure before
#: touching this constant.
SILENT_ABSENCES = 0

#: The census, measured 2026-10-25 at main + this branch's changes, through the
#: SHIPPED resolution order. Any change here is a change in the SHIPPED
#: mechanism's reach on real extractions or in the committed corpus — re-run the
#: census, then update this tuple AND the table in
#: ``docs/runbook/2684-slice-b-t-gate.md``. Do not "fix" a drift by loosening an
#: assertion: a lower span count is the finding.
CENSUS = {
    "value_bearing": 112,
    "spanned": 101,
    "absences": {"band": 3, "valuefree_quote": 3, "no_turn": 4,
                 "empty_quote": 1},
}

#: The three points the receipt's ADVISORY index puts in one turn and the
#: shipped resolver reports as unresolvable (quote-overlap < 0.6). Recorded so
#: the resolver's presence in the census is pinned as a MEASUREMENT and not as
#: a refactor detail.
ADVISORY_MISATTRIBUTED = 3

#: The population the census runs over, and the half of it that is value-free.
#: Both are PUBLISHED (runbook §6 quotes "105 of the 217 committed points are
#: value-free"), so both are pinned: the test used to assert only that
#: ``total`` was truthy, which let either number drift under a green run.
#: ``VALUE_FREE_POINTS`` is measured through the SHIPPED predicate on the
#: SHIPPED (clamped) inputs, i.e. the same test production draws.
TOTAL_POINTS = 217
VALUE_FREE_POINTS = 105

#: An INDEPENDENT value detector — deliberately NOT ``_carries_value``, which is
#: the predicate under test. A currency amount, a numeral, a month name, a
#: number word, or a unit of time/quantity. Used to prove the addressed text
#: carries a value even if ``_carries_value`` itself regresses to always-True.
INDEPENDENT_VALUE_RE = re.compile(
    r"(?:[$€£]\s?\d"
    r"|\d[\d.,]*\s?%?"
    r"|\b(?:January|February|March|April|May|June|July|August|September"
    r"|October|November|December)\b"
    r"|\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven"
    r"|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen"
    r"|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred"
    r"|thousand|million|billion|dozen|half|quarter|double|triple|twice"
    r"|second|seconds|minute|minutes|hour|hours|day|days|week|weeks"
    r"|month|months|year|years)\b)",
    re.IGNORECASE,
)


def _norm(text: object) -> str:
    """Whitespace-collapsed, lowercased — the normalisation the E3 anchor and
    ``_verbatim_span_in_turn`` apply, so the two cannot disagree."""
    return " ".join(str(text).lower().split())


def _receipt(session: str) -> dict:
    return json.loads((COMMITTED_ARMS / f"{session}.arms.json").read_text())


def _edus(session: str) -> list[dict]:
    """The turns the quotes anchor in, built by the SHIPPED builder.

    ⛔ Deliberately NOT a local re-implementation. ``_edus_from_conversation``
    DROPS empty-content turns while PRESERVING their original indices, and the
    resolver resolves by index VALUE rather than list position (see
    ``_resolve_source_turn`` — "position and value can diverge"). A hand-rolled
    copy keeps the empty turns, so its list is LONGER and the divergence lands
    on the resolver's in-range bound, ``0 <= model_idx < len(edus)``
    (``extractor_v2.py:9027``): a model index that fell inside the hand-rolled
    list can fall outside production's, so the resolved ``turn_idx`` differs for
    exactly those points. That flips them between ``no_turn`` (``turn_idx is
    None``) and ``band``/``valuefree_quote``, changes which warning variant an
    ``empty_quote`` point emits (in-range "unverified (empty quote) — using it"
    vs the shared step-4 string), and moves the E3 contradiction check and the
    ``moved``/``ADVISORY_MISATTRIBUTED`` pin — all while every pinned number
    stays green. Byte-identical on all 7 sealed fixtures today — none has an
    empty-content turn — which is exactly what would let the divergence ship
    unnoticed: build the turns the way the write path builds them.
    """
    fixture = json.loads((WRITE_PATH / "fixtures" / f"{session}.json").read_text())
    return v2._edus_from_conversation(fixture["conversation"])


def _points(session: str, arm: str) -> list[dict]:
    return _receipt(session)["embed_list"][arm]["points"]


def _stamp_inputs(point: dict) -> tuple[str, str]:
    """The two inputs production hands the span mechanism, CLAMPED the way the
    write path clamps them.

    ⛔ Not the receipt's raw strings. ``extractor_v2`` trims the content to
    ``_MAX_CONTENT`` (``:10051``) and the quote to ``_MAX_QUOTE`` (``:10119``)
    BEFORE ``_value_verbatim_span(content, quote, ...)`` is called (``:10367``);
    only ``_resolve_source_turn`` gets the raw point (``:10342``, deliberately).
    A census on the raw strings therefore answers a question production never
    asks: a >``_MAX_QUOTE``-char quote gets an address production cannot stamp.
    Measured over the sealed corpus: 3 of the 217 points carry a
    >``_MAX_QUOTE``-char quote and 1 of them is value-bearing AND spanned, where
    the raw census reports ``(0, 201)`` and production writes ``(0, 200)``. No
    published count moves (the other two are value-free, and no content reaches
    ``_MAX_CONTENT``), which is exactly why nothing else would ever catch it.

    ⛔ BOTH caps are IMPORTED from production, never re-typed here. An earlier
    revision of this function carried its own ``[:200]``, which made the pin
    below self-referential: it compared the census against an expectation
    produced by that same copy, so raising production's cap to 250 left this
    whole file green while the census reported offsets production never writes.
    With the caps imported, production's cap and the census's cap are one
    object, and the literal ``(0, 200)`` in the pin is what forces a re-measure.

    ⛔ **AND THE CUT IS PRODUCTION'S TOO — ``_write_span``, not ``[:cap]``**
    (#10742). #10513 (``ee31e0412``) moved the write path off the hard slice
    onto a TOKEN-BOUNDARY cut that drops a split token whole when dropping it
    would truncate a numeral; this function kept re-typing ``[:cap]``, so the
    census stamped addresses production no longer writes. That is the SAME
    trap as the ``[:200]`` revision above, one layer down: the caps were
    imported and the cut was not, so the pin stayed green on a re-typed
    mechanism. Measured on the sealed corpus: **0 of 217 points diverge** (no
    committed quote happens to end mid-token at a cap) — the divergence is
    LATENT, which is exactly why nothing caught it. The mechanism pin below
    (``test_the_census_clamps_with_the_writes_own_cut``) constructs the case
    that makes it visible.
    """
    # The coercions differ ON PURPOSE, because production's do: the write path
    # builds `_raw_content` with `str(p.get("content", ""))` — a falsy content is
    # the claim "None" (#5060), so `or ""` would collapse it and drop the item —
    # and the quote with `str(p.get("quote") or "")`.
    content = v2._write_span(str(point.get("content", "")).strip(),
                             v2._MAX_CONTENT)
    quote = v2._write_span(str(point.get("quote") or "").strip(),
                           v2._MAX_QUOTE)
    return content, quote


def _stamp_quote(point: dict) -> str:
    """The ``quote`` production COMMITS for this point — ``_stamp_inputs``' quote half.

    The dedupe half reads quotes too, and ``retrieval._same_fact`` compares the
    COMMITTED hit (``retrieval.py:3292``: ``qa = str(a.get("quote") or "")``),
    i.e. after ``extractor_v2`` rewrote the field to the ``_MAX_QUOTE`` span at
    the point's own commit (``:10119``). Reading the raw
    receipt string here would measure a population no package holds — the same
    defect §2.1 and §2.3 each corrected once already (advisory turn index, raw
    span inputs), one field over.
    """
    return _stamp_inputs(point)[1]


def _absence_class(turn_idx: int | None, quote: str,
                   edus: list[dict]) -> str:
    """WHICH reported class an absence belongs to, derived from SETTLED FACTS.

    ⛔ This must never read the warning text. Deriving the class from a warning
    substring and then asserting that the warning carries that substring is
    circular (``band`` ⟺ a warning saying "token-overlap band"): swapping the
    two messages inside ``_value_verbatim_span`` would move the classifier with
    the swap and leave both the census and the pin green. So the class comes
    from the same inputs production decides on — the quote, the resolved turn's
    own text, and whether the addressed text carries a value — exactly mirroring
    ``_value_verbatim_span``'s own branch order.

    The caller has already established the point is value-bearing and that
    ``_value_verbatim_span`` returned ``None``, so a mirror that agrees with
    production lands on one of the four classes below. ``unclassified`` is the
    fifth, and it means the mirror DISAGREES — see its own note.
    """
    if not quote:
        return "empty_quote"
    if turn_idx is None:
        return "no_turn"
    turn_text = v2._turn_text_by_index(edus, turn_idx)
    span = v2._verbatim_span_in_turn(turn_text, quote)
    if span is None:
        return "band"
    if not v2._carries_value(turn_text[span[0]:span[1]]):
        # verbatim-present, so the stamp bailed on the ADDRESSED TEXT: the quote
        # is the point's context, not its value sentence.
        return "valuefree_quote"
    # ⛔ REACHABLE, and deliberately so: the mirror says a span SHOULD have been
    # stamped (verbatim address + a value in it) yet production returned None —
    # so `_value_verbatim_span` has a bail-out path this mirror does not model.
    # Reporting it as `unclassified` surfaces that divergence instead of
    # silently folding it into `valuefree_quote`.
    return "unclassified"


def _census() -> dict:
    """Bucket every value-bearing point in the committed receipts, in the
    SHIPPED order: resolve the turn from the quote, then stamp against it."""
    out = {"value_bearing": 0, "spanned": 0, "absences": {},
           "spans": [], "unclassified": [], "moved": []}
    for session in SESSIONS:
        edus = _edus(session)
        for arm in ARMS:
            for point in _points(session, arm):
                content, quote = _stamp_inputs(point)
                if not v2._carries_value(content, quote):
                    continue
                out["value_bearing"] += 1
                warnings: list[str] = []
                turn_idx = v2._resolve_source_turn(point, edus,
                                                   warnings=warnings)
                span = v2._value_verbatim_span(
                    content, quote, turn_idx, edus, warnings=warnings)
                if span is not None:
                    out["spanned"] += 1
                    out["spans"].append(
                        (session, arm, content, quote, span, turn_idx,
                         v2._turn_text_by_index(edus, turn_idx)))
                    continue
                if turn_idx is None and point.get("source_turn_id") is not None:
                    # the advisory index named a turn the resolver refuses
                    out["moved"].append((session, arm, point.get("source_turn_id"),
                                         content[:70]))
                klass = _absence_class(turn_idx, quote, edus)
                if klass == "unclassified":
                    out["unclassified"].append((session, arm, content, quote))
                out["absences"][klass] = out["absences"].get(klass, 0) + 1
    return out


def test_the_census_is_a_rederivation_not_an_observation():
    """The committed receipts carry NO span keys at all.

    Pinned so the census below is never read as "the extractor stamped 101
    spans in this run": it is what the SHIPPED mechanism stamps on points that
    were produced without it. The reason is STRUCTURAL, not chronological — the
    harness persists ``ev2.run_s2(...)``, the S2 model output, and the span is
    stamped later when ``pt_entry`` is built. A span key appearing here means
    the HARNESS changed (it now persists committed points), not that a date
    passed; retire the derivation caveat only after re-measuring through the
    new harness."""
    total = spanned = value_free = 0
    for session in SESSIONS:
        for arm in ARMS:
            for point in _points(session, arm):
                total += 1
                spanned += int("span_start" in point or "span_end" in point)
                value_free += int(not v2._carries_value(*_stamp_inputs(point)))
    assert total, "the census has no population — the receipts did not load"
    assert total == TOTAL_POINTS, (
        f"the committed population moved: {total} points, pinned {TOTAL_POINTS} "
        "— runbook §6 publishes this total; re-measure before touching it")
    assert value_free == VALUE_FREE_POINTS, (
        f"{value_free} of the {total} committed points are value-free, pinned "
        f"{VALUE_FREE_POINTS} — runbook §6 publishes this count as the reason no "
        "widening to every Point can be read off this corpus")
    assert spanned == 0, (
        f"{spanned} of {total} committed points carry a span key — the "
        "measuring harness now persists committed points (it used to persist "
        "the pre-pt_entry S2 model output), so this census became an "
        "OBSERVATION: re-measure the rate end-to-end, update CENSUS + the "
        "runbook's derivation caveat, and keep the observation/derivation "
        "distinction explicit")


def test_the_census_stamps_on_the_inputs_production_hands_it():
    """The clamps are the FIX, so they must be pinned by a re-derivation.

    ``_stamp_inputs`` trims the content to ``_MAX_CONTENT`` and the quote to
    ``_MAX_QUOTE`` because that is what ``extractor_v2`` hands
    ``_value_verbatim_span`` (``:10051`` / ``:10119`` -> ``:10367``). Measured at
    this head: 3 points in the population carry a >``_MAX_QUOTE``-char quote and
    exactly ONE of them is value-bearing AND spanned — ``wp04_aurora_perf``/``raw``
    — and it is the only place the two readings differ, ``(0, 201)`` raw vs
    ``(0, 200)`` clamped.

    ⛔ **The runbook's "3" is pinned HERE, over ALL long quotes** — not over the
    value-bearing subset the ``found`` pin below uses. Pinning only the subset
    would let a fourth, value-FREE long quote appear and make the runbook's
    "3 of the 217" false while this test stayed green (the value-bearing count
    would be unmoved at 1).

    ⛔ **This test exists because nothing else reds without the clamps.**
    Reverting ``_stamp_inputs`` to the raw receipt strings leaves the whole
    suite green: no published COUNT moves (census ``112/101`` and all four
    absence totals are byte-identical under both readings), so only the span
    END of that one point differs — and the runbook publishes that pair. A
    count-only census therefore lets "the addresses it reports are the ones
    production writes" become false silently. Pinned as a re-derivation of
    both readings, not as a constant: if the clamps are removed the clamped
    arm returns ``(0, 201)`` and reds here."""
    found = []
    long_quotes: list[tuple[str, str]] = []
    for session in SESSIONS:
        for arm in ARMS:
            edus = _edus(session)
            for point in _points(session, arm):
                raw_content = str(point.get("content") or "").strip()
                raw_quote = str(point.get("quote") or "").strip()
                if len(raw_quote) <= v2._MAX_QUOTE:
                    continue
                long_quotes.append((session, arm))
                content, quote = _stamp_inputs(point)
                if not v2._carries_value(content, quote):
                    continue
                turn = v2._resolve_source_turn(point, edus, warnings=[])
                clamped = v2._value_verbatim_span(
                    content, quote, turn, edus, warnings=[])
                raw = v2._value_verbatim_span(
                    raw_content, raw_quote, turn, edus, warnings=[])
                found.append((session, arm, raw, clamped))
    assert len(long_quotes) == 3, (
        f"the >_MAX_QUOTE-char quote population is {len(long_quotes)} "
        f"({sorted(long_quotes)}), pinned 3 — the runbook publishes "
        "\"3 of the 217 points carry a >_MAX_QUOTE-char quote\"; re-measure "
        "it before changing this pin")
    assert len(found) == 1, (
        "the >_MAX_QUOTE-char value-bearing quote population moved: "
        f"{found} — re-measure before trusting (0, 200) below")
    session, arm, raw, clamped = found[0]
    assert (session, arm) == ("wp04_aurora_perf", "raw")
    assert raw == (0, 201), (
        f"the RAW reading is {raw}, not (0, 201) — this pin's premise changed")
    assert clamped == (0, 200), (
        f"the census stamps {clamped}, not production's (0, 200) — "
        "_stamp_inputs is not applying the quote clamp")
    # the CONTENT clamp is unexercised on this corpus (declared, not assumed):
    # every point's raw content already fits under _MAX_CONTENT.
    over = [p for s in SESSIONS for a in ARMS for p in _points(s, a)
            if len(str(p.get("content") or "").strip()) > v2._MAX_CONTENT]
    assert over == [], (
        f"{len(over)} point(s) now exceed _MAX_CONTENT, so the content clamp "
        "is exercised and the (0, 200) pin above no longer covers it: "
        "re-measure and pin that leg too")


def test_the_census_resolves_the_turn_the_way_the_write_path_does():
    """The census must not measure a population production never produces.

    The write path resolves the source turn from the quote BEFORE stamping
    (``extractor_v2.py:10342`` → ``:10367``), and the receipt's
    ``source_turn_id`` is only the model's ADVISORY index. Reading that field
    straight through puts three ``wp06_quarry_rollout``/``narrative`` points on
    turns the resolver refuses (quote-overlap < 0.6), which (a) hides the
    resolver's E3 anchor warning, (b) reports them as ``band`` when production
    reports ``no_turn``, and (c) inflates the ``band`` class by 2x."""
    census = _census()
    assert len(census["moved"]) == ADVISORY_MISATTRIBUTED, (
        "the advisory-index divergence moved: "
        f"{census['moved']} — re-measure before updating the runbook")
    assert {s for s, _a, _idx, _c in census["moved"]} == {"wp06_quarry_rollout"}
    assert all(a == "narrative" for _s, a, _idx, _c in census["moved"])
    # and the class they land in is the RESOLVER's verdict, not the band's
    assert census["absences"].get("no_turn") == CENSUS["absences"]["no_turn"]
    assert census["absences"].get("band") == CENSUS["absences"]["band"]


def test_every_value_bearing_point_carries_a_span_or_is_a_REPORTED_class():
    """The census: 101 of 112 value-bearing points get an address, and the 11
    absences are all in a REPORTED class. An ``unclassified`` absence is the
    failure this test exists for: a value-bearing point whose value cannot be
    re-read AND whose absence no consumer can see."""
    census = _census()
    assert census["unclassified"] == [], (
        "value-bearing points with NO span and NO reported class: "
        f"{census['unclassified']}")
    assert census["value_bearing"] == CENSUS["value_bearing"], (
        f"value-bearing population moved: {census['value_bearing']} "
        f"!= {CENSUS['value_bearing']} — re-run the census and update CENSUS "
        "and docs/runbook/2684-slice-b-t-gate.md (a LOWER count is a finding, "
        "not an assertion to loosen)")
    assert census["spanned"] == CENSUS["spanned"], (
        f"span stamping moved: {census['spanned']} != {CENSUS['spanned']} — "
        "re-run the census and update CENSUS + the runbook table")
    assert census["absences"] == CENSUS["absences"], (
        f"absence classes moved: {census['absences']} != {CENSUS['absences']}"
        " — a NEW class here means a silent drop, check it before updating")


def _per_session_split() -> dict[tuple[str, str], tuple]:
    """Runbook §2.1's table, re-derived: ``(value_bearing, spanned, absences)``
    per ``(session, arm)`` arm, in the SHIPPED order."""
    out: dict[tuple[str, str], tuple] = {}
    for session in SESSIONS:
        edus = _edus(session)
        for arm in ARMS:
            value_bearing = spanned = 0
            absences: dict[str, int] = {}
            for point in _points(session, arm):
                content, quote = _stamp_inputs(point)
                if not v2._carries_value(content, quote):
                    continue
                value_bearing += 1
                warnings: list[str] = []
                turn_idx = v2._resolve_source_turn(point, edus,
                                                   warnings=warnings)
                if v2._value_verbatim_span(content, quote, turn_idx, edus,
                                           warnings=warnings) is not None:
                    spanned += 1
                    continue
                klass = _absence_class(turn_idx, quote, edus)
                absences[klass] = absences.get(klass, 0) + 1
            out[(session, arm)] = (value_bearing, spanned,
                                   tuple(sorted(absences.items())))
    return out


#: Runbook §2.1's 14-row split, VERBATIM. This is the only published census
#: figure that says WHICH session lost WHICH class, so it is pinned row-for-row
#: rather than only through the totals: a corpus change can preserve `112`/`101`
#: and all four class counts while moving points BETWEEN sessions, which would
#: leave the table stale under a green suite.
EXPECTED_PER_SESSION_SPLIT = {
    ("wp01_quarry_debug", "raw"): (9, 8, (("band", 1),)),
    ("wp01_quarry_debug", "narrative"): (10, 9, (("band", 1),)),
    ("wp02_lumen_refactor", "raw"): (6, 6, ()),
    ("wp02_lumen_refactor", "narrative"): (7, 5, (("valuefree_quote", 2),)),
    ("wp03_ember_design", "raw"): (6, 6, ()),
    ("wp03_ember_design", "narrative"): (5, 5, ()),
    ("wp04_aurora_perf", "raw"): (9, 9, ()),
    ("wp04_aurora_perf", "narrative"): (12, 12, ()),
    ("wp05_retro_writeup", "raw"): (10, 10, ()),
    ("wp05_retro_writeup", "narrative"): (11, 10, (("no_turn", 1),)),
    ("wp06_quarry_rollout", "raw"): (10, 10, ()),
    ("wp06_quarry_rollout", "narrative"): (5, 1, (("band", 1), ("no_turn", 3))),
    ("wp07_bluepeak_followup", "raw"): (4, 3, (("empty_quote", 1),)),
    ("wp07_bluepeak_followup", "narrative"): (8, 7, (("valuefree_quote", 1),)),
}


def test_the_per_session_split_is_pinned():
    """Runbook §2.1 is a MEASUREMENT, not a reading.

    The totals alone are not a sufficient guard: a corpus change can preserve
    ``112``/``101`` and the four absence totals while moving a point from one
    session's arm to another's, and §2.1 is the only published figure that says
    which session lost which class. Diffed row-for-row, and the diff names the
    rows that moved.
    """
    split = _per_session_split()
    moved = {k: (split.get(k), EXPECTED_PER_SESSION_SPLIT[k])
             for k in EXPECTED_PER_SESSION_SPLIT
             if split.get(k) != EXPECTED_PER_SESSION_SPLIT[k]}
    assert not moved, (
        "runbook §2.1's per-session split moved (got, pinned): "
        f"{moved} — re-measure the table before publishing it")
    assert split.keys() == EXPECTED_PER_SESSION_SPLIT.keys(), (
        "the measured arms no longer match §2.1's rows: "
        f"extra={sorted(split.keys() - EXPECTED_PER_SESSION_SPLIT.keys())} "
        f"missing={sorted(EXPECTED_PER_SESSION_SPLIT.keys() - split.keys())}")
    # and the split must still sum to the pinned totals — the two guards are
    # independent, so agreement here cannot mask a moved row above.
    assert sum(v[0] for v in split.values()) == CENSUS["value_bearing"]
    assert sum(v[1] for v in split.values()) == CENSUS["spanned"]


def test_every_absence_in_the_census_is_REPORTED():
    """**0 silent absences** — the claim the runbook and the index row publish.

    Measured over the whole committed population, in the SHIPPED order
    (``_resolve_source_turn`` then ``_value_verbatim_span``): every value-bearing
    absence must carry a warning drawn from ITS OWN class's allowed set — not any
    warning at all. The ``empty_quote`` leg is the one that matters: it has NO
    span-side warning (there is no verbatim text to address), so the only thing
    that keeps it out of the silent-drop class is the resolver, which runs first
    and reports it either as "unverified (empty quote) — using it" (in-range
    model index) or as "no resolvable source turn".

    ⚠️ That second form is the string a ``no_turn`` absence emits, so for this
    corpus's single ``empty_quote`` point the warning proves the absence is
    REPORTED but does not identify the class — ``_absence_class`` derives the
    class from the point's own settled facts (it has no quote at all) while the
    warning comes from the resolver. The doc claims say exactly that; do not
    read this assertion as class-identification *for this class*. For
    ``band``/``valuefree_quote`` the assertion IS class-identification, because
    those classes are derived from the resolved turn's text and the addressed
    text's own value reading — never from the warning string being checked.

    ⛔ One shape is silent BY CONSTRUCTION and is NOT exercised here: with an
    EMPTY ``edus`` list ``_resolve_source_turn`` returns ``None`` before any
    warning. Declared in runbook §2.2 rather than measured, because the census
    has no such point."""
    silent: list[tuple[str, str, str, str]] = []
    wrong_class: list[tuple[str, str, str, list[str]]] = []
    reported = 0
    for session in SESSIONS:
        edus = _edus(session)
        for arm in ARMS:
            for point in _points(session, arm):
                content, quote = _stamp_inputs(point)
                if not v2._carries_value(content, quote):
                    continue
                warnings: list[str] = []
                turn_idx = v2._resolve_source_turn(point, edus,
                                                   warnings=warnings)
                if v2._value_verbatim_span(content, quote, turn_idx, edus,
                                           warnings=warnings) is not None:
                    continue
                klass = _absence_class(turn_idx, quote, edus)
                if not warnings:
                    silent.append((klass, session, arm, content[:60]))
                    continue
                reported += 1
                expected = EXPECTED_ABSENCE_WARNINGS[klass]
                if not any(any(e in w for e in expected) for w in warnings):
                    wrong_class.append((klass, session, arm, warnings))
    assert silent == [], (
        "value-bearing absences NO consumer can see (neither the resolver nor "
        f"the stamp warned): {silent} — that is the silent-drop class the "
        "census claims is empty (§2.2), so the runbook + the index row must "
        "stop claiming \"0 silent\"")
    assert wrong_class == [], (
        "absences reported by a message that does not name their class — the "
        f"report exists but a consumer cannot tell WHICH absence it is: {wrong_class}")
    assert reported == sum(CENSUS["absences"].values()) - SILENT_ABSENCES, (
        f"reported absences moved: {reported} != "
        f"{sum(CENSUS['absences'].values()) - SILENT_ABSENCES} — re-measure "
        "§2.2 and the index row before changing SILENT_ABSENCES")


def test_every_stamped_span_addresses_the_whole_quote_and_carries_a_value():
    """The invariant the slice exists for, asserted over the whole committed
    population (not just the unit cases).

    Three legs, each of which a WRONG implementation fails:

    1. **in bounds and forward** — ``0 <= start < end <= len(turn)``;
    2. **the address is the WHOLE quote**, not a fragment of it
       (``_norm(addressed) == _norm(quote)``). ⛔ Not ``in``: a fragment of the
       quote satisfies containment, and a fragment is exactly the false-address
       shape §3 of the runbook refuses (``"four shards"``, ``"One flag"``,
       ``"One"``). Equality is what pins the refusal.
    3. **the address carries a value** — by an INDEPENDENT detector in this
       file, so the leg still discriminates if ``_carries_value`` regresses to
       always-True (the three ``valuefree_quote`` points would then be stamped
       and this leg would fire)."""
    census = _census()
    assert census["spans"], "no spans to check — the census is empty"
    for session, arm, content, quote, span, _turn_idx, turn in census["spans"]:
        start, end = span
        assert 0 <= start < end <= len(turn), (session, arm, content[:60], span,
                                               len(turn))
        addressed = turn[start:end]
        assert _norm(addressed) == _norm(quote), (
            f"{session}/{arm}: span addresses {addressed!r}, which is not the "
            f"point's WHOLE quoted words ({quote[:80]!r}) — a partial address "
            "hands a consumer text it cannot tell apart from the value it is "
            "summing")
        assert INDEPENDENT_VALUE_RE.search(addressed), (
            f"{session}/{arm}: span addresses value-free text {addressed!r} "
            f"for {content[:60]!r} (independent value detector found nothing)")


# ── the dedupe half of T-1 — MEASURED, and NOT discriminating here ────────
#
# "Verbatim-present **+ deduped**" is two properties. The dedupe half is Slice
# A's (#2683, merged): a point and its own source collapse to ≤
# `1 + max_verbatim` slots, and a differing number/currency/negation token
# refuses the collapse at ANY content overlap.
#
# The universe is the one the guard actually runs in: ONE package, i.e. one
# ingest = ONE ARM of one session. Pooling the two arms would compare two
# INDEPENDENT ingests of the same session — a pool no package ever holds — and
# that pooled reading is measured below as a separate, explicitly non-guard
# number.
#
# Value-bearing points only (the census population), pairwise within each
# session-arm, using the shipped guard predicates `_pkg_overlap` +
# `_pkg_differ_value_critical`.

PAIRS_PER_ARM = 433          # Σ C(n, 2) over the 14 session-arms
NEAR_DUPE_PAIRS_PER_ARM = 0  # ≥DEFAULT_PACKAGE_VERBATIM_OVERLAP overlap
NEAR_DUPE_VALUE_CONFLICTS_PER_ARM = 0

#: The dedupe half's SAME-SOURCE-TURN route (``_same_fact``'s second leg), over
#: the population dedupe ACTUALLY sees — every extracted point of a session-arm,
#: not only the value-bearing ones (a hit's partners are whatever the package
#: holds). Pairs whose two ``source_turn_id`` keys are present and equal AND
#: whose two quotes are non-empty, i.e. the point at which the shipped predicate
#: consults its quote-level value guard:
#:
#:   consulted            — that pair count (the route IS reachable here);
#:   value_flagged        — of them, ``_pkg_differ_value_critical(qa, qb)`` True;
#:   value_guard_decisive — the ones where deleting the guard CHANGES the
#:                          outcome (flagged AND the overlap gate would otherwise
#:                          have collapsed them);
#:   collapsed            — quote overlap >= the same-turn gate (value-equal).
#:
#: ⛔ The key is the RESOLVED turn, never the receipt's advisory
#: ``source_turn_id`` (``_resolved_turn_key`` below) — §2.1's rule one leg over:
#: production overwrites the advisory index before the point is committed, and
#: ``_same_fact`` compares the committed field. Grouping by the advisory index
#: measures a population no package holds.
#:
#: ⛔ And the QUOTES are the committed ones too (``_stamp_quote``) —
#: ``_same_fact`` reads the hit's ``quote``, which the write path already
#: truncated to ``_MAX_QUOTE`` at the point's own commit. The sealed corpus is
#: clamp-INVARIANT here (3 of the 217 points carry a longer quote and none of
#: them moves a counter), so this cannot be a corpus-shaped pin: the synthetic
#: pair in ``test_the_dedupe_leg_reads_the_COMMITTED_quote_not_the_raw_receipt``
#: is where the two readings diverge and a revert reds.
#:
#: ⛔ ``value_guard_decisive = 1`` — the corpus DOES carry evidence that the
#: quote-value guard prevents a value loss: ``wp07_bluepeak_followup``/narrative
#: turn 14 pairs a value-bearing point against a strict SUB-quote that carries no
#: value, at quote overlap 1.00. The value-bearing-only restriction
#: (``SAME_TURN_DEDUPE_VALUE_BEARING``) cannot see that pair, which is why the
#: restricted reading must never be published as the leg's reach. Both readings
#: are pinned; a change to either is a re-measure.
SAME_TURN_DEDUPE = {"consulted": 41, "value_flagged": 24,
                    "value_guard_decisive": 1, "collapsed": 2}

#: The same measurement restricted to the census's span population (both sides
#: value-bearing). Pinned as the WEAKER reading, with its blind spot named: it
#: excludes exactly the pair the quote-value guard is decisive on, because that
#: pair's partner is a non-value-bearing sub-quote.
SAME_TURN_DEDUPE_VALUE_BEARING = {"consulted": 11, "value_flagged": 8,
                                  "value_guard_decisive": 0, "collapsed": 1}

#: The SAME measurement grouped on the receipt's own ``source_turn_id`` — the
#: model's ADVISORY index — instead of the resolved turn. Published in runbook
#: §2.3 as the third population an advisory grouping would produce, and pinned
#: here so those four numbers are re-derived rather than asserted: the previous
#: form derived only ``consulted`` (the other three fields stayed 0) and then
#: asserted ``advisory != resolved``, an inequality that holds for ANY consulted
#: count, so the doc's figures were checked by nothing.
ADVISORY_KEY_DEDUPE = {"consulted": 43, "value_flagged": 26,
                       "value_guard_decisive": 1, "collapsed": 2}

#: EVERY per-arm pair of extracted points, for the content-ratio zero: the
#: value-bearing restriction narrows the population, and the zero must not be a
#: property of the narrowing.
PAIRS_PER_ARM_ALL_POINTS = 1719


def _value_bearing_points(session: str, arm: str) -> list[dict]:
    return [p for p in _points(session, arm)
            if v2._carries_value(*_stamp_inputs(p))]


def _resolved_turn_key(point: dict, edus: list[dict]) -> str:
    """The turn key the WRITE PATH would stamp: the RESOLVED turn.

    ⛔ Not the receipt's own ``source_turn_id``. That field is the S2 model's
    ADVISORY index; production overwrites it with ``_resolve_source_turn``'s
    answer before the point is committed (``extractor_v2.py:10342`` → ``:10349``),
    and ``_same_fact`` groups on the committed field
    (``retrieval.py:3288-3290`` — the ``_pkg_turn_key`` pair and its equality
    test). Same defect class as §2.1's, one leg over —
    grouping by the advisory index counts pairs production never forms."""
    warnings: list[str] = []
    return _pkg_turn_key({"source_turn_id":
                          v2._resolve_source_turn(point, edus,
                                                  warnings=warnings)})


def _dedupe_acc() -> dict:
    """The four-field counter §2.3 publishes for the same-source-turn route."""
    return {"consulted": 0, "value_flagged": 0,
            "value_guard_decisive": 0, "collapsed": 0}


def _same_turn_route(acc: dict, a: dict, b: dict, edus: list[dict]) -> None:
    """``retrieval._same_fact``'s SAME-SOURCE-TURN route, applied to one PAIR.

    ⛔ Both quotes are the ones production COMMITS (``_stamp_quote``), not the
    receipt's raw strings: ``_same_fact`` compares the committed hit
    (``retrieval.py:3292-3293``), which the write path already rewrote to
    ``[:_MAX_QUOTE]``. On the sealed corpus the published counts are
    clamp-invariant (3 points carry a longer quote, none of them moves a
    counter), so a corpus-shaped pin cannot tell the two readings apart — this
    function is module-level precisely so
    ``test_the_dedupe_leg_reads_the_COMMITTED_quote_not_the_raw_receipt`` can
    drive it with a pair where they DIFFER.
    """
    at = _resolved_turn_key(a, edus)
    qa = _stamp_quote(a)
    qb = _stamp_quote(b)
    if not (at and at == _resolved_turn_key(b, edus) and qa and qb):
        return
    acc["consulted"] += 1
    passes_overlap = _pkg_overlap(qa, qb) >= DEFAULT_PACKAGE_SAME_TURN_OVERLAP
    if _pkg_differ_value_critical(qa, qb):
        acc["value_flagged"] += 1
        if passes_overlap:          # the guard CHANGED this outcome
            acc["value_guard_decisive"] += 1
    elif passes_overlap:
        acc["collapsed"] += 1


def test_the_dedupe_leg_is_measured_on_BOTH_legs():
    """Pinned so no future lane reads "deduped: PASS" off this census, and so
    the numbers the runbook quotes are re-derived rather than asserted.

    TWO routes, and this corpus treats them differently:

    * **content-ratio** — in the guard's own per-arm universe there is **no**
      ≥ ``DEFAULT_PACKAGE_VERBATIM_OVERLAP`` overlap pair at all, so THAT route
      cannot be exercised here; the
      property rests on Slice A's unit guards
      (``tests/test_evidence_assembly.py``). Checked over EVERY per-arm pair of
      extracted points, not just the value-bearing narrowing
      (``PAIRS_PER_ARM_ALL_POINTS``), so the zero is not an artefact of the
      population filter;
    * **same-source-turn** (``retrieval._same_fact``'s second route: same
      ``source_turn_id``, quote overlap ≥
      ``DEFAULT_PACKAGE_SAME_TURN_OVERLAP``, with
      ``_pkg_differ_value_critical`` consulted on the QUOTES first) — REACHABLE
      here, and **decisive once** (``SAME_TURN_DEDUPE``), so the corpus does
      carry evidence that the guard prevents a value loss.

    ⛔ The population is the finding, twice over. §2.3 first generalised the
    content-ratio zero to "the corpus cannot exercise the value-safety leg at
    all"; the correction then narrowed the same-turn route to the CENSUS's
    value-bearing pairs and concluded "decisive on 0" — which is a property of
    that narrowing, not of the corpus. ``SAME_TURN_DEDUPE_VALUE_BEARING`` keeps
    the restricted reading visible *as the weaker one*, with its blind spot
    named: it excludes the one decisive pair, whose partner is a
    non-value-bearing sub-quote."""
    pairs = near_dupe = conflicts = 0
    all_pairs = all_near_dupe = 0
    pooled_pairs = pooled_near_dupe = 0
    same_turn = _dedupe_acc()
    same_turn_vb = _dedupe_acc()
    # The ADVISORY-key reading (``ADVISORY_KEY_DEDUPE``): the same measurement
    # grouped on the receipt's field, counted with all four fields so runbook
    # §2.3's published table is RE-DERIVED, and pinned by equality so a lane
    # that switches the grouping back to the receipt's field gets a red instead
    # of a quiet re-measure.
    same_turn_advisory = _dedupe_acc()

    for session in SESSIONS:
        edus = _edus(session)
        for arm in ARMS:
            for a, b in itertools.combinations(_points(session, arm), 2):
                # content-ratio zero over the population dedupe actually sees
                all_pairs += 1
                if _pkg_overlap(_stamp_inputs(a)[0],
                                _stamp_inputs(b)[0]) >= DEFAULT_PACKAGE_VERBATIM_OVERLAP:
                    all_near_dupe += 1
                _same_turn_route(same_turn, a, b, edus)
                # advisory-key reading: same pairs, WRONG key — all FOUR
                # fields, so the numbers §2.3 publishes are re-derived (a
                # consulted-only counter cannot fail: every other field stays
                # 0, and "advisory != resolved" then holds for any count).
                at, bt = _pkg_turn_key(a), _pkg_turn_key(b)
                qa = _stamp_quote(a)
                qb = _stamp_quote(b)
                if at and at == bt and qa and qb:
                    same_turn_advisory["consulted"] += 1
                    adv_passes = (_pkg_overlap(qa, qb)
                                  >= DEFAULT_PACKAGE_SAME_TURN_OVERLAP)
                    if _pkg_differ_value_critical(qa, qb):
                        same_turn_advisory["value_flagged"] += 1
                        if adv_passes:
                            same_turn_advisory["value_guard_decisive"] += 1
                    elif adv_passes:
                        same_turn_advisory["collapsed"] += 1
            points = _value_bearing_points(session, arm)
            for a, b in itertools.combinations(points, 2):
                pairs += 1
                _same_turn_route(same_turn_vb, a, b, edus)
                if _pkg_overlap(_stamp_inputs(a)[0],
                                _stamp_inputs(b)[0]) >= DEFAULT_PACKAGE_VERBATIM_OVERLAP:
                    near_dupe += 1
                    if _pkg_differ_value_critical(_stamp_inputs(a)[0],
                                                  _stamp_inputs(b)[0]):
                        conflicts += 1
        pooled = [p for arm in ARMS for p in _value_bearing_points(session, arm)]
        for a, b in itertools.combinations(pooled, 2):
            pooled_pairs += 1
            if _pkg_overlap(a.get("content") or "",
                            b.get("content") or "") >= DEFAULT_PACKAGE_VERBATIM_OVERLAP:
                pooled_near_dupe += 1
    assert pairs == PAIRS_PER_ARM, (
        f"the per-arm within-session pair count moved: {pairs} != "
        f"{PAIRS_PER_ARM} — re-run and update the runbook §2.3")
    assert all_pairs == PAIRS_PER_ARM_ALL_POINTS, (
        f"the ALL-POINTS per-arm pair count moved: {all_pairs} != "
        f"{PAIRS_PER_ARM_ALL_POINTS} — §2.3's wider reading must be re-measured")
    assert all_near_dupe == 0, (
        f"{all_near_dupe} pairs reach "
        f"≥{DEFAULT_PACKAGE_VERBATIM_OVERLAP} content overlap over ALL extracted "
        "points — the content-ratio route is now reachable in the population "
        "dedupe sees, so the value-bearing zero in §2.3 is no longer the "
        "strongest reading and the section must be re-measured")
    assert near_dupe == NEAR_DUPE_PAIRS_PER_ARM, (
        f"{near_dupe} pairs reach ≥{DEFAULT_PACKAGE_VERBATIM_OVERLAP} content "
        "overlap in the guard's own "
        "universe — the CONTENT-RATIO route can now be exercised here, so §2.3 "
        "must be re-measured (it currently says that route cannot be)")
    assert conflicts == NEAR_DUPE_VALUE_CONFLICTS_PER_ARM, (
        f"{conflicts} near-dupe pairs differ in a value-critical token — §2.3 "
        "must be re-measured")
    assert same_turn == SAME_TURN_DEDUPE, (
        "the SAME-SOURCE-TURN route moved over ALL extracted points (measured "
        f"before: {SAME_TURN_DEDUPE}) — §2.3 publishes these four numbers: "
        "re-measure both readings and update the runbook")
    assert same_turn_advisory == ADVISORY_KEY_DEDUPE, (
        "the ADVISORY-key reading of the same-turn route moved (measured: "
        f"{same_turn_advisory}, pinned: {ADVISORY_KEY_DEDUPE}) — runbook §2.3 "
        "publishes these four numbers as the population an advisory grouping "
        "would produce; re-measure both readings and update the table")
    assert same_turn_advisory != same_turn, (
        "the advisory-key reading of the same-turn route no longer differs "
        f"from the RESOLVED-key one ({same_turn_advisory} vs {same_turn}) — "
        "either the receipts changed or the resolver now agrees with the "
        "model's index everywhere; §2.1's rule (the turn is the RESOLVED one) "
        "is what this test applies, so re-measure before removing the control")
    assert same_turn_vb == SAME_TURN_DEDUPE_VALUE_BEARING, (
        "the value-bearing-only reading of the same-turn route moved (measured "
        f"before: {SAME_TURN_DEDUPE_VALUE_BEARING}) — it is the WEAKER reading "
        "and its blind spot (the decisive pair's non-value-bearing partner) is "
        "what makes it zero: re-measure and update §2.3")
    # Measured context, deliberately loose: the zero above is a property of the
    # PER-ARM universe, not of the corpus in general. Pooling the two arms (a
    # pool no package holds) does produce near-dupes — a lane must not restate
    # the zero as "this corpus has no near-dupes".
    assert pooled_near_dupe > 0, (
        f"pooling the two arms produced no "
        f"≥{DEFAULT_PACKAGE_VERBATIM_OVERLAP} pair — the universe note in "
        f"§2.3 (pooled pairs={pooled_pairs}) no longer holds")


def test_the_dedupe_leg_reads_the_COMMITTED_quote_not_the_raw_receipt(monkeypatch):
    """The dedupe half's two quotes are the COMMITTED ones, and this pair proves it.

    ⛔ The sealed corpus cannot discriminate a clamped quote from a raw one:
    the published counts are clamp-invariant on it (3 of the 217 points carry a
    >``_MAX_QUOTE``-char quote and none of them moves a counter), so a
    corpus-shaped pin stays GREEN on a revert to ``str(hit.get("quote"))`` — a
    guard that cannot fail. This pair is constructed to differ: the same 200
    characters on both sides, plus a value token that production's
    ``[:_MAX_QUOTE]`` truncates away.

    Committed reading (what the census must do): the two quotes are IDENTICAL,
    the overlap clears the threshold, nothing fact-critical differs →
    ``collapsed``. Raw reading (what the census used to do): the tail survives,
    ``300`` is a fact-critical symmetric-difference token, the guard is
    consulted and becomes DECISIVE — a different outcome on the same pair, which
    is exactly what makes this pin non-vacuous.
    """
    base = ("quarry rollout proceeds as described while handlers discuss the "
            "rollout openly " * 6)[:v2._MAX_QUOTE]
    assert len(base) == v2._MAX_QUOTE, len(base)
    edus = [{"index": 7, "text": base + " 300"}]
    a = {"content": base, "quote": base + " 300", "source_turn_id": 7}
    b = {"content": base, "quote": base, "source_turn_id": 7}

    committed = _dedupe_acc()
    _same_turn_route(committed, a, b, edus)
    assert committed == {"consulted": 1, "value_flagged": 0,
                         "value_guard_decisive": 0, "collapsed": 1}, (
        f"the route did not read the COMMITTED quote: {committed} — production "
        f"commits the _MAX_QUOTE span (extractor_v2.py:10119), so the pair is a "
        "collapse, not a value conflict")

    # … and the SAME pair read the way this file used to read it, to show the
    # substitution is what produces the difference (not the synthetic data).
    monkeypatch.setitem(globals(), "_stamp_quote",
                        lambda p: str(p.get("quote") or ""))
    raw = _dedupe_acc()
    _same_turn_route(raw, a, b, edus)
    assert raw == {"consulted": 1, "value_flagged": 1,
                   "value_guard_decisive": 1, "collapsed": 0}, (
        f"the raw-receipt reading was expected to see a value conflict and did "
        f"not: {raw} — the synthetic pair no longer discriminates, so this pin "
        "proves nothing and must be rebuilt")


# ── the relaxation a future lane will reach for — MEASURED ON BOTH READINGS ─
#
# The ``band`` class (3 points) is the visible cost of the strict anchor: a
# quote rewritten by one word keeps no address even though the value IS
# verbatim in the turn ("The cutoff stays September 30 as agreed" vs the turn's
# "Cutoff stays September 30 as agreed, …"). The obvious "improvement" is to
# trim the quote's leading/trailing tokens until a verbatim run is found. This
# pins what that relaxation actually produces on the committed corpus.
#
# ⛔ TWO readings, because only one of them is production:
#
#   SHIPPED (resolver first — what the write path does): 2 candidates, BOTH
#   whole-assertion CLEAN —
#       "two workers grabbed the same batch twice …"   (full sentence)
#       "Cutoff stays September 30 as agreed"          (the value, whole)
#   So on the shipped band class this corpus does NOT make the relaxation look
#   unsafe: the refusal rests on the fragment SHAPE (below) and on #8966's unit
#   guards, not on this census.
#
#   ADVISORY-INDEX reading (the receipt's model index — NOT production): 3 more
#   candidates, ALL fragments —
#       "four shards"  — one of the point's two quantities
#       "One flag"     — a number WORD that is a determiner, not a quantity
#       "One"          — the SAME defect at its shortest: the search matches a
#                        shared word at the turn's start, in a sentence the
#                        point never quoted (#8966's false-address class,
#                        reintroduced)
#   Those three points are exactly the ones the SHIPPED resolver reports as
#   unresolvable, so the fragments are reachable only by a lane that applies the
#   relaxation to the raw model index instead of the resolved turn — which is
#   the population-production mistake the census itself had (see the resolver
#   test above).
#
# A red here means a candidate set moved — re-measure and update the runbook,
# do not narrow the assertion.

EXPECTED_SHIPPED_RELAXATIONS = {
    "two workers grabbed the same batch twice from the ingest queue and each "
    "marked its own copy complete.",
    "Cutoff stays September 30 as agreed",
}
EXPECTED_ADVISORY_FRAGMENTS = {"four shards", "One flag", "One"}


def test_the_trimmed_anchor_relaxation_is_pinned_on_both_readings():
    shipped: set[str] = set()
    advisory: set[str] = set()
    for session in SESSIONS:
        edus = _edus(session)
        for arm in ARMS:
            for point in _points(session, arm):
                content, quote = _stamp_inputs(point)
                if not v2._carries_value(content, quote):
                    continue
                # SHIPPED order: resolve, then stamp.
                warnings: list[str] = []
                turn_idx = v2._resolve_source_turn(point, edus,
                                                   warnings=warnings)
                if v2._value_verbatim_span(content, quote, turn_idx, edus,
                                           warnings=warnings) is not None:
                    continue
                # The relaxation only applies where production HAS a turn.
                if turn_idx is not None and _absence_class(
                        turn_idx, quote, edus) == "band":
                    cand = _longest_quote_run(
                        v2._turn_text_by_index(edus, turn_idx), quote)
                    if cand is not None:
                        shipped.add(cand[2])
                # The ADVISORY-INDEX reading, kept as a counter-example.
                adv_idx = point.get("source_turn_id")
                adv_warnings: list[str] = []
                if adv_idx is not None and v2._value_verbatim_span(
                        content, quote, adv_idx, edus,
                        warnings=adv_warnings) is None and _absence_class(
                        adv_idx, quote, edus) == "band":
                    cand = _longest_quote_run(
                        v2._turn_text_by_index(edus, adv_idx), quote)
                    if cand is not None:
                        advisory.add(cand[2])
    assert shipped == EXPECTED_SHIPPED_RELAXATIONS, (
        "the shipped-population relaxation candidates moved — re-measure and "
        f"update the runbook §3: {sorted(shipped)}")
    fragments = {t for t in shipped if len(t.split()) <= 2}
    assert fragments == set(), (
        f"the shipped band class now yields FRAGMENT addresses {fragments} — "
        "the relaxation is unsafe on the population production produces, "
        "which is a stronger finding than §3 records")
    assert advisory == EXPECTED_SHIPPED_RELAXATIONS | EXPECTED_ADVISORY_FRAGMENTS, (
        "the advisory-index candidate set moved — re-measure it and update "
        f"the runbook §3: {sorted(advisory)}")
    assert advisory - shipped == EXPECTED_ADVISORY_FRAGMENTS, (
        "the candidates that exist ONLY on the advisory reading moved — those "
        f"are the #8966 false-address class: {sorted(advisory - shipped)}")


def _longest_quote_run(turn: str, quote: str):
    """The longest contiguous run of the quote's own tokens found verbatim in
    the turn whose addressed text carries a value — the relaxation's candidate.
    Deliberately naive: it is the shape a well-meaning "fix" takes."""
    tokens = quote.split()
    for size in range(len(tokens) - 1, 0, -1):
        for start in range(0, len(tokens) - size + 1):
            frag = " ".join(tokens[start:start + size])
            span = v2._verbatim_span_in_turn(turn, frag)
            if span is not None and v2._carries_value(turn[span[0]:span[1]]):
                return (span, frag, turn[span[0]:span[1]])
    return None


# ── the numbers THIS file publishes, resolved at the head it publishes them at ─
#
# ⛔ A `file:line` in a durable artifact is only safe if something re-derives it
# on every insertion above it. This PR's own `_MAX_QUOTE` block moved every
# `extractor_v2.py` cite below it by +10, and the numbers this file publishes
# were the ones that did NOT move with it: the census's docstrings kept pointing
# ten lines above the code they name, while `_stamp_inputs`' docstring (updated
# by the same commit) pointed at the right ones — one file, two answers, and no
# leg reading either. The 7949 bundle's sweep (`tests/test_7949_capstone_measure
# .py::_bundle_sweep`) is row-scoped to the `#7949` index row and does not read
# this file, so the pin lives here, beside the cites it holds.
#
# The pin is a MAPPING, not an assertion about the code: each published number
# is paired with the line it must name, so the two move together. Every
# FILE-QUALIFIED cite in this file must be in the mapping (an unlisted one is
# unverified, so it reds like the 7949 sweep's `assert not unmatched`), and every
# mapped line must still carry its symbol — a production insertion above these
# lines reds until the numbers are re-derived, and a cite edited without
# re-deriving reds too. That is the contract, not an inconvenience.
_PUBLISHED_CITES = {
    10051: ("content = _write_span(_raw_content, _MAX_CONTENT)",
           "the content clamp"),
    10119: ('quote = _write_span(str(p.get("quote") or "").strip(), _MAX_QUOTE)',
           "the quote clamp, at the point's own commit"),
    9027: ("if type(model_idx) is int and 0 <= model_idx < len(edus):",
           "the resolver's in-range bound"),
    10342: ("turn_idx = _resolve_source_turn(p, edus, warnings=warnings)",
           "the resolver call"),
    10343: ("pt_entry = {", "the point entry the span is stamped into"),
    10349: ('"source_turn_id": turn_idx,',
           "the resolved turn the point COMMITS"),
    10350: ("}", "the end of the point entry"),
    10367: ("_span = _value_verbatim_span(content, quote, turn_idx, edus,",
           "the stamp itself"),
}

#: The value-free gate `_value_verbatim_span` applies, published by the two
#: artifacts this PR adds (they disagreed about it: the index row pointed at
#: prose INSIDE the gate's docstring, while the runbook pointed at the gate
#: itself). One site, three readers — so all three are pinned together.
#: ⛔ No concrete line number is spelled in this comment: the two examples that
#: used to sit here went stale in the very drift this PR repairs, and an example
#: that rots is the defect restated in the guidance (#10848 review P2).
_VALUE_FREE_GATE = (5582, "if not _carries_value(content, quote):")

#: The same contract, for the RUNBOOK — the doc half of the census (#10742).
#: The runbook `publishes the same numbers` as this file, so when the file's
#: cites rotted the runbook's rotted with them and nothing read the doc at all:
#: `test_the_value_free_gate_cite_agrees_across_the_index_row_and_the_runbook`
#: asserted ONE number, and the other seven went stale unread. This mapping is
#: keyed the same way so the two halves can never drift apart again.
_RUNBOOK_CITES = {
    10051: ("content = _write_span(_raw_content, _MAX_CONTENT)",
           "the content clamp"),
    10119: ('quote = _write_span(str(p.get("quote") or "").strip(), _MAX_QUOTE)',
           "the quote clamp, at the point's own commit"),
    10342: ("turn_idx = _resolve_source_turn(p, edus, warnings=warnings)",
           "the resolver call"),
    10343: ("pt_entry = {", "the point entry the span is stamped into"),
    10350: ("}", "the end of the point entry"),
    10367: ("_span = _value_verbatim_span(content, quote, turn_idx, edus,",
           "the stamp itself"),
    4472: ("def _verbatim_span_in_turn(turn_text: str, quote: str) -> tuple[int, int] | None:",
           "`_verbatim_span_in_turn`'s definition"),
    4500: ("def _carries_value(*texts: object) -> bool:",
           "`_carries_value`'s definition"),
    5542: ("def _value_verbatim_span(content: str, quote: str, turn_idx: int | None,",
           "`_value_verbatim_span`'s definition"),
    9009: ("if not edus:", "the resolver's empty-edus silent bound"),
    9010: ("return None", "the resolver's silent early return"),
    5582: ("if not _carries_value(content, quote):",
           "the value-free gate (`_VALUE_FREE_GATE`)"),
    5583: ("return None", "the value-free gate's refusal"),
    10347: ('"source_ref": "session.md", "quote": quote, "status": "draft",',
           "the committed quote inside `pt_entry`"),
    10349: ('"source_turn_id": turn_idx,',
           "the resolved turn the point COMMITS"),
    10370: ('pt_entry["span_start"], pt_entry["span_end"] = _span',
           "the span assignment"),
}

_RUNBOOK = "docs/runbook/2684-slice-b-t-gate.md"
_REPO = WRITE_PATH.resolve().parents[2]

#: #10742's root fix, one level up: the two artifacts cite MORE than
#: `extractor_v2.py`, and until this existed **nothing read those cites at all**.
#: The first revision of this pin repeated the pre-fix shape exactly — it pinned
#: the `extractor_v2.py` subset and called that "the doc" — and the review of
#: that revision found FIVE false `file:line` cites left behind in the two files
#: it was editing, one of which was a live INSTRUCTION ("Delete `if
#: _pkg_differ_value_critical(qa, qb): return False`", cited at what had once
#: been the guard's lines but was by then depth-cap counters — so a lane
#: following it would have edited the wrong two lines and concluded the guard
#: was not decisive, the OPPOSITE of the published finding). Partial coverage of
#: a document is the same defect as no coverage with a green tick.
#:
#: So the sweep is now TOTAL over both artifacts for any `*.py:NNNN` cite,
#: excluding `extractor_v2.py` (held by `_PUBLISHED_CITES` / `_RUNBOOK_CITES`).
#: Both ENDS of a range must be mapped: a range's end is a cite too, and it is
#: the end that silently points at unrelated code when the file grows.
_CROSS_FILE_CITES: dict[tuple[str, int], tuple[str, str]] = {
    ("tortoise/commit_schema.py", 445):
        ("def validate_span(span_start: object, span_end: object) -> None:",
         "`validate_span`'s definition"),
    ("tortoise/commit_schema.py", 678):
        ("span_start: int | None = Field(default=None, ge=0, le=SPAN_OFFSET_MAX)",
         "the `span_start` field"),
    ("tortoise/commit_schema.py", 705):
        ("return self", "the end of `_span_is_an_address`"),
    ("tools/longmem_eval/retrieve.py", 1004):
        ('if p.get("span_start") is not None and p.get("span_end") is not None:',
         "the eval lane's offset annotation"),
    ("tools/longmem_eval/retrieve.py", 1006):
        ('entry["span_end"] = p["span_end"]',
         "the annotated end offset"),
    ("tortoise/sdk.py", 39000):
        ("rows = graph.query(", "the provenance query's opener"),
    ("tortoise/sdk.py", 39006):
        ('+ ", n.quote, n.source_turn_id, n.span_start, n.span_end",',
         "the RETURN continuation appending #8658's four columns"),
    ("tortoise/sdk.py", 38967):
        ("# ⛔ KNOWN LIMITATION — this read is the PROJECT-search point",
         "the ⛔ KNOWN LIMITATION comment"),
    ("tortoise/sdk.py", 38983):
        ("# or of #8658.", "the end of the ⛔ KNOWN LIMITATION comment"),
    ("tortoise/retrieval.py", 3288):
        ("at = _pkg_turn_key(a)",
         "the same-source-turn route's entry"),
    ("tortoise/retrieval.py", 3290):
        ("if not (at and at == bt):",
         "the same-turn GROUPING test `_same_fact` keys on"),
    ("tortoise/retrieval.py", 3292):
        ('qa = str(a.get("quote") or "")',
         "the COMMITTED quote read (`_same_fact`)"),
    ("tortoise/retrieval.py", 3293):
        ('qb = str(b.get("quote") or "")',
         "the COMMITTED quote read, partner half"),
    ("tortoise/retrieval.py", 3296):
        ("if _pkg_differ_value_critical(qa, qb):",
         "the quote-value guard"),
    ("tortoise/retrieval.py", 3297):
        ("return False", "the quote-value guard's refusal"),
    ("tortoise/retrieval.py", 3298):
        ("return _pkg_overlap(qa, qb) >= DEFAULT_PACKAGE_SAME_TURN_OVERLAP",
         "the same-turn overlap gate"),
}

#: A `*.py:NNNN`, optionally with a `-NNNN` range end.
_CITE_RE = re.compile(r"([\w][\w./-]*\.py):(\d+)(?:-(\d+))?")


def _resolve_cite_path(spelled: str) -> str | None:
    """The repo-relative path a cite spells, or None if it does not resolve.

    A doc writes both `retrieval.py:3292` and `tortoise/retrieval.py:3292`; the
    mapping is keyed repo-relative so the two spellings share one entry (and a
    spelling that resolves to NOTHING is itself reported, rather than silently
    filtered out of the sweep).

    ⛔ Resolved through `git ls-files`, NEVER a `_REPO.glob("**/<name>")` walk
    (#10742 review, measured). The repo's lane convention is
    `<repo>/.worktrees/<lane>`, so from the CANONICAL checkout a tree-wide glob
    for `extractor_v2.py` finds 111 copies and `retrieval.py` 118 — the obvious
    "take the unique hit" rule then resolves to None and reds 23 cites that are
    all CORRECT, i.e. a false RED on the primary checkout for a diff that did
    not touch them (and invisible to CI, which has no `.worktrees`). Tracked
    files are exactly the ones a cite can legitimately name, and `git ls-files`
    is also the cheap form: one subprocess instead of three full-tree walks.
    ⛔ "Could not ask git" and "this cite does not resolve" are DIFFERENT
    failures and must not share a message (#10742 review, measured). 23 of the 31
    cites in the two artifacts are bare basenames that resolve only through `git
    ls-files`, so a machine without a usable `git` (a mounted/exported checkout, a
    minimal image, a `safe.directory` refusal at exit 128) red the sweep with 23
    entries that are ALL CORRECT, under a message that says the cites are broken.
    `_GIT_FAILURE` carries the reason; the test reports it as an ENVIRONMENT
    failure before it looks at a single cite.
    """
    if (_REPO / spelled).is_file():
        return spelled
    name = Path(spelled).name
    tracked = _git_ls_files(name)
    if tracked is None:
        return None
    hits = [ln for ln in tracked if Path(ln).name == name and (_REPO / ln).is_file()]
    return hits[0] if len(hits) == 1 else None


#: Why the last `git ls-files` could not be asked, or None when it worked. Set by
#: `_git_ls_files` so the sweep can tell an environment failure from a bad cite.
_GIT_FAILURE: list[str] = []


def _git_ls_files(name: str) -> list[str] | None:
    """Tracked paths named ``name``, or None when git could not be asked.

    Read-only: `git ls-files` never writes, so the sweep stays non-mutating (the
    earlier tree-wide glob was read-only too, and was dropped only because it
    resolved 113 copies of `extractor_v2.py` from the canonical checkout).
    """
    try:
        done = subprocess.run(
            ["git", "-C", str(_REPO), "ls-files", "--", f"**/{name}", name],
            capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        _GIT_FAILURE.append(f"`git ls-files` could not run: {type(exc).__name__} {exc}")
        return None
    if done.returncode != 0:
        _GIT_FAILURE.append(
            f"`git ls-files` exited {done.returncode}: "
            f"{(done.stderr or done.stdout).strip()[:200]}")
        return None
    return done.stdout.splitlines()


def _cites(text: str) -> list[tuple[str, int]]:
    """Every `(repo-relative path, line)` the text cites, `extractor_v2.py`
    excluded (held by `_PUBLISHED_CITES` / `_RUNBOOK_CITES`).

    ⛔ The ONE enumerator: this function used to be dead code while the sweep
    inlined its own `_CITE_RE.findall`, so a lane that hardened `_cites` (for
    instance to add the `.worktrees` guard above) changed NOTHING. One
    implementation, called by the test.
    """
    out: list[tuple[str, int]] = []
    for spelled, start, end in _CITE_RE.findall(text):
        path = _resolve_cite_path(spelled)
        if path is None or Path(path).name == "extractor_v2.py":
            continue
        out.append((path, int(start)))
        if end:
            out.append((path, int(end)))
    return out


def _file_lines(relpath: str) -> list[str]:
    return (_REPO / relpath).read_text(encoding="utf-8").splitlines()


def _extractor_lines() -> list[str]:
    return (_REPO / "tortoise/extractor_v2.py").read_text(encoding="utf-8") \
        .splitlines()


def _stale(mapping: dict, lines: list[str]) -> list[tuple[int, str, str, list[int]]]:
    """``(old number, expected text, label, the lines that DO carry it)``.

    The 4th element is the whole point: a stale cite that reports only *what* it
    found leaves the re-derivation to hand, and hands are why these rot again
    (#10742 ask 4 — the 2026-11-01 #10360 lane derived these by hand).
    """
    out: list[tuple[int, str, str, list[int]]] = []
    for number, (expected, label) in sorted(mapping.items()):
        got = (lines[number - 1].strip()
               if 0 < number <= len(lines) else "<past EOF>")
        if got == expected:
            continue
        found = [i + 1 for i, ln in enumerate(lines) if ln.strip() == expected]
        out.append((number, expected, label, found))
    return out


def _paste_ready(stale: list[tuple[int, str, str, list[int]]]) -> str:
    """The corrected mapping, READY TO PASTE — issue #10742 ask 4."""
    blocks: list[str] = []
    for number, expected, label, found in stale:
        if not found:
            blocks.append(
                f"  {number}: # ⛔ SYMBOL GONE — {label}: {expected!r} is not in\n"
                "      # extractor_v2.py at this head; re-author the cite, do\n"
                "      # not merely move the number")
        elif len(found) == 1:
            blocks.append(f"  {found[0]}: ({expected!r},\n"
                          f"          {label!r}),")
        else:
            blocks.append(
                f"  {number}: # ⛔ AMBIGUOUS — {label}: {expected!r} is on\n"
                f"      # lines {found}; cite the one this sentence means")
    return "\n".join(blocks)


def test_every_extractor_v2_cite_this_file_publishes_resolves_at_this_head():
    """Each published ``extractor_v2.py`` number → the line it names, and back.

    Non-vacuity: moving a number in this file's own prose (or inserting lines
    above it in `extractor_v2.py`) reds — the mapping is the tripwire, and it is
    the reason a future lane cannot re-stale these sites silently. An
    UNLISTED file-qualified cite reds as unverified, so the pin cannot be
    outgrown by adding a cite beside it.

    The failure emits the CORRECTED MAPPING, ready to paste (#10742 ask 4): a
    message that names only the mismatch makes re-derivation hand work, and hand
    derivation is what rotted all eight entries at once.
    """
    text = Path(__file__).read_text(encoding="utf-8")
    lines = _extractor_lines()

    published = {int(n) for n in re.findall(r"extractor_v2\.py:(\d+)", text)}
    # … and any bare continuation (``→ `:NNNN` ``, or a range end whose start is
    # a file-qualified cite) counts as PUBLISHED, so a mapped number is not
    # orphaned merely because its file is named once. ⛔ No concrete number is
    # spelled in this comment: an example that rots is the defect restated in
    # the guidance, and `test_guidance_prose_quotes_only_numbers_the_pins_hold`
    # now reds on one.
    for start, end in re.findall(r":(\d{4,5})(?:-(\d{4,5}))?", text):
        published.add(int(start))
        if end:
            published.add(int(end))
    unswept = sorted({int(n) for n in
                      re.findall(r"extractor_v2\.py:(\d+)", text)}
                     - set(_PUBLISHED_CITES))
    assert not unswept, (
        "this file publishes a file-qualified extractor_v2.py cite that this pin "
        "does not hold, so nothing verifies it — add it to _PUBLISHED_CITES "
        f"with the line it must name: {unswept}")
    orphaned = sorted(set(_PUBLISHED_CITES) - published
                      - {n for (_p, n) in _CROSS_FILE_CITES})
    assert not orphaned, (
        "_PUBLISHED_CITES holds numbers this file no longer publishes — the pin "
        f"would cover a cite nobody reads: {orphaned}")

    stale = _stale(_PUBLISHED_CITES, lines)
    assert not stale, (
        "the cites this census publishes point at lines that no longer carry "
        "the code they name — the symbol is authoritative and the line is a "
        "convenience, so paste the corrected mapping below over "
        "`_PUBLISHED_CITES` and update the prose with the same numbers:\n\n"
        "_PUBLISHED_CITES = {\n" + _paste_ready(stale) + "\n}")


def test_every_extractor_v2_cite_THE_RUNBOOK_publishes_resolves_at_this_head():
    """The runbook publishes the same numbers — so it gets the same tripwire.

    #10742: the census file and the runbook are two readers of one set of
    cites, and the pin used to cover only one of them (`_VALUE_FREE_GATE`). All
    eight of the census's mapped numbers had rotted, and the runbook's had
    rotted with them while its own test stayed green on a single number — the
    doc half had NO reader at all. Symmetric coverage is the fix: every
    file-qualified `extractor_v2.py:NNNN` the runbook publishes must be in
    `_RUNBOOK_CITES`, and every mapped number must still name its line.
    """
    text = (_REPO / _RUNBOOK).read_text(encoding="utf-8")
    lines = _extractor_lines()

    # ⛔ TOTAL over the runbook's numbers, not just its file-qualified ones
    # (#10742 review). The runbook continues a cite with a bare `:NNNN`, and
    # prints range ENDS whose second number a lane renumbering the doc can leave
    # pointing at unrelated code — the "one file, two answers, and no leg
    # reading either" shape, one level down. Every number the runbook prints is
    # in `published`. ⛔ No concrete line number is spelled in this comment: the
    # examples that used to sit here went stale in the very drift this PR
    # repairs, and an example that rots is the defect restated in the guidance.
    published = {int(n) for n in re.findall(r"extractor_v2\.py:(\d+)", text)}
    for start, end in re.findall(r":(\d{4,5})(?:-(\d{4,5}))?", text):
        published.add(int(start))
        if end:
            published.add(int(end))

    # A number that belongs to a CROSS-FILE cite is not an `extractor_v2.py`
    # number — `_CROSS_FILE_CITES` verifies those and this map must not claim
    # them (the bare-continuation regex cannot tell whose bare number it found).
    published -= {n for (_p, n) in _CROSS_FILE_CITES}

    unswept = sorted(published - set(_RUNBOOK_CITES))
    assert not unswept, (
        "the runbook publishes an extractor_v2.py number that no pin holds, so "
        "nothing verifies it — add it to _RUNBOOK_CITES with the line it must "
        f"name: {unswept}")
    orphaned = sorted(set(_RUNBOOK_CITES) - published)
    assert not orphaned, (
        "_RUNBOOK_CITES holds numbers the runbook no longer publishes — the pin "
        f"would cover a cite nobody reads: {orphaned}")

    stale = _stale(_RUNBOOK_CITES, lines)
    assert not stale, (
        "the runbook's extractor_v2.py cites point at lines that no longer "
        "carry the code they name — paste the corrected mapping over "
        "`_RUNBOOK_CITES` and update the prose with the same numbers:\n\n"
        "_RUNBOOK_CITES = {\n" + _paste_ready(stale) + "\n}")


def test_every_non_extractor_v2_cite_in_EITHER_artifact_resolves_at_this_head():
    """#10742's root fix: the sweep is TOTAL over both artifacts, not per-file.

    The first revision covered only `extractor_v2.py` — the same partial-coverage
    shape it was written to remove, one level up — and the review of that
    revision found FIVE false `file:line` cites it had stepped over, including a
    live runbook INSTRUCTION that named the wrong two lines to delete. A tripwire
    that pins part of a document reports "clean" for the part nobody checked, so
    it is worse than an honest gap.

    Coverage is demanded by construction in BOTH directions: every non-
    `extractor_v2.py` `*.py:NNNN` cite in either artifact must be mapped (an
    unlisted one reds), and every mapped site must still carry its line.
    """
    stale: list[str] = []
    unmapped: list[str] = []
    unresolvable: list[str] = []
    _GIT_FAILURE.clear()
    for artifact in (Path(__file__).resolve().relative_to(_REPO).as_posix(),
                     _RUNBOOK):
        body = (_REPO / artifact).read_text(encoding="utf-8")
        for spelled, start, _end in _CITE_RE.findall(body):
            if _resolve_cite_path(spelled) is None:
                unresolvable.append(f"{artifact}: `{spelled}:{start}`")
        for path, n in _cites(body):
            key = (path, n)
            if key not in _CROSS_FILE_CITES:
                unmapped.append(f"{artifact}: `{path}:{n}`")
                continue
            expected, label = _CROSS_FILE_CITES[key]
            lines = _file_lines(path)
            got = (lines[n - 1].strip()
                   if 0 < n <= len(lines) else "<past EOF>")
            if got == expected:
                continue
            found = [i + 1 for i, ln in enumerate(lines)
                     if ln.strip() == expected]
            stale.append(
                f"  (\"{path}\", {n}): # ⛔ {label}: reads {got!r}, wanted "
                f"{expected!r} — found on line(s) {found}")

    assert not _GIT_FAILURE, (
        "the cite sweep could not ASK GIT, so it verified nothing — this is an "
        "ENVIRONMENT failure, not a broken cite: every bare-basename spelling "
        "(`extractor_v2.py:NNNN`, `retrieval.py:NNNN`, `sdk.py:NNNN`) resolves "
        "through `git ls-files`. Fix the checkout/`git`, do NOT edit the cites. "
        f"{_GIT_FAILURE[0]}")
    assert not unresolvable, (
        "a cite spells a file that does not resolve to exactly one path in this "
        f"repo, so nothing can verify it: {unresolvable}")
    assert not unmapped, (
        "these artifacts cite a `file:line` that no pin holds, so nothing "
        "verifies it — add each to _CROSS_FILE_CITES with the line it must "
        f"name: {sorted(set(unmapped))}")
    assert not stale, (
        "a cross-file cite points at a line that no longer carries the code it "
        "names — paste the corrected entries over `_CROSS_FILE_CITES`:\n\n"
        + "\n".join(stale))


def test_the_census_clamps_with_the_writes_own_cut():
    """The census's clamp is production's ``_write_span``, never a re-typed slice.

    #10742. The two caps were imported (see ``_stamp_inputs``), but the CUT was
    re-typed as ``[:cap]`` while the write path has fed ``_value_verbatim_span``
    ``_write_span``'s token-boundary pair since #10513 (``ee31e0412``). The
    census therefore answered *"what would production stamp?"* by measuring a
    cut production does not use.

    ⛔ **The sealed corpus CANNOT catch this.** Measured: 0 of the 217 committed
    points diverge (no committed quote ends mid-token at a cap), so every
    published count is identical under both cuts — a corpus-shaped pin stays
    green on a revert to ``[:cap]``, i.e. a guard that cannot fail. The
    divergence is LATENT and has to be CONSTRUCTED.

    The construction: a quote whose cap lands two characters into ``1200units``.
    The kept prefix of the split token (``12``) carries decimals and the
    discarded remainder (``00units…``) carries alphanumerics, so
    ``_write_span`` drops the split token WHOLE while ``[:cap]`` keeps a
    truncated numeral — precisely the shape that reads a value SHORT (``1200``
    -> ``12``).

    Non-vacuity: this reds if the census re-types a slice (it would return the
    hard cut), and it reds if production's cut stops discriminating on this
    fixture (the literal below would stop matching), which is the signal that
    the fixture — not the mechanism — needs re-authoring.
    """
    # ── the QUOTE cap ──
    head = "quarry rollout proceeds as described while handlers discuss it "
    pad = "x" * (v2._MAX_QUOTE - 3 - len(head))
    quote = head + pad + " 1200units and more"
    assert len(head + pad) == v2._MAX_QUOTE - 3, len(head + pad)
    # the cap lands INSIDE the token, else the fixture proves nothing
    assert quote[:v2._MAX_QUOTE] == head + pad + " 12", quote[:v2._MAX_QUOTE][-8:]
    token_safe = head + pad + " "          # the split token dropped WHOLE
    hard_slice = head + pad + " 12"        # the numeral read as `12`
    assert v2._write_span(quote, v2._MAX_QUOTE) == token_safe, (
        "production's own cut no longer drops the split token on this fixture "
        "— re-author the fixture before trusting anything below")
    assert _stamp_inputs({"quote": quote})[1] == v2._write_span(
        quote, v2._MAX_QUOTE), (
        "the census's quote clamp is NOT production's `_write_span` — it is "
        "stamping an address the write path never stores (#10742)")
    assert _stamp_inputs({"quote": quote})[1] == token_safe
    assert _stamp_inputs({"quote": quote})[1] != hard_slice
    # and the quote production hands the span is the one it stores, so a
    # re-typed slice is visible at the CENSUS's own door, not just at v2's
    assert _stamp_quote({"quote": quote}) == token_safe

    # ── the CONTENT cap, same shape ──
    chead = "quarry rollout proceeds as described while handlers discuss it "
    cpad = "y" * (v2._MAX_CONTENT - 3 - len(chead))
    content = chead + cpad + " 1200units and more"
    assert len(chead + cpad) == v2._MAX_CONTENT - 3, len(chead + cpad)
    assert content[:v2._MAX_CONTENT] == chead + cpad + " 12"
    assert _stamp_inputs({"content": content})[0] == chead + cpad + " ", (
        "the census's content clamp is a hard slice — production stores the "
        "token-boundary span (#10742)")

def test_the_value_free_gate_cite_agrees_across_the_index_row_and_the_runbook():
    """The index row and the runbook must publish the SAME line for one gate.

    They did not: `docs/00_index.md`'s `#2684` row pointed at prose INSIDE the
    gate's docstring while §6 of the runbook pointed at the gate's own line. A
    summary that contradicts the document it summarises is the failure mode the
    bundle's cite convention exists to prevent, and nothing read either.

    #10742: this test covered ONE number while the file published nine, so
    eight rotted unread. It now emits the corrected pair too, and the other
    eight are held by `_PUBLISHED_CITES` / `_RUNBOOK_CITES` above.

    ⛔ The two disagreeing numbers are described, not spelled: they were stale
    the moment the gate moved, and spelling them here restated the very defect
    (they pointed at unrelated code — one at a template slot's value, the other
    at two blank lines). `test_guidance_prose_quotes_only_numbers_the_pins_hold`
    is the guard that keeps a number out of this prose unless a pin holds it.
    """
    number, expected = _VALUE_FREE_GATE
    lines = _extractor_lines()
    got = lines[number - 1].strip()
    assert got == expected, (
        f"extractor_v2.py:{number} reads {got!r}, not {expected!r} — re-derive "
        "the gate's line and update `_VALUE_FREE_GATE`, docs/00_index.md and "
        f"{_RUNBOOK} together. Found on line(s): "
        f"{[i + 1 for i, ln in enumerate(lines) if ln.strip() == expected]}")

    cite = f"extractor_v2.py:{number}-{number + 1}"
    for name in ("docs/00_index.md", _RUNBOOK):
        body = (_REPO / name).read_text(encoding="utf-8")
        assert cite in body, (
            f"{name} no longer publishes `{cite}` for the value-free gate — "
            "the two artifacts must name the same site, and a stale number here "
            "is a number nothing can self-detect")


def _prose_of(src: str) -> list[tuple[int, str]]:
    """(line, text) for every COMMENT and every DOCSTRING in `src`.

    The class this guards is prose-only: a bare `:NNNN` written in guidance is
    read by no sweep (the file arm of `_cites` is file-qualified-only), so it
    rots silently the moment code moves above it. Docstrings come from the AST
    (so a string that merely LOOKS like a docstring is not one) and comments
    from `tokenize` (so a `#` inside a string is not one); both are used rather
    than a line regex, which would flag the maps themselves — they are the pins,
    not guidance.
    """
    prose: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                       ast.AsyncFunctionDef)):
            continue
        doc = ast.get_docstring(node, clean=False)
        if doc is not None and node.body and isinstance(node.body[0], ast.Expr):
            prose.append((node.body[0].lineno, doc))
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            prose.append((tok.start[0], tok.string))
    return prose


def _prose_cites(prose: list[tuple[int, str]]) -> list[tuple[int, int]]:
    """(line, number) for every `:NNNN` / `:NNNN-NNNN` spelled in `prose`."""
    cite_re = re.compile(r":(\d{4,5})(?:-(\d{4,5}))?")
    out: list[tuple[int, int]] = []
    for line_no, text in prose:
        for m in cite_re.finditer(text):
            for spelled in (m.group(1), m.group(2)):
                if spelled:
                    out.append((line_no, int(spelled)))
    return out


def test_the_prose_extractor_sees_both_a_comment_and_a_docstring():
    """FAILS IF `_prose_of` stops finding prose, which would make the guard vacuous.

    A guard over an extraction that silently returns nothing passes on every rot
    it exists to catch — the `#8175` shape (a tool that holds the datum and does
    not read it). Driven on a SYNTHETIC module so this check cannot rot with
    this file's own prose.
    """
    sample = '''"""A module docstring with `:1111`."""


def f():
    """A function docstring with `:2222-3333`."""
    x = "a # that is not a comment, and :9999 inside a string"
    # a real comment with `:4444`
    return x
'''
    found = _prose_cites(_prose_of(sample))
    assert [n for _ln, n in found] == [1111, 2222, 3333, 4444], (
        f"the prose extractor saw {found} — expected the module docstring, the "
        "function docstring and the comment, and NOT the `:9999` inside the "
        "string literal. A guard whose extraction is broken passes silently")


def test_guidance_prose_quotes_only_numbers_the_pins_hold():
    """FAILS IF this file's own guidance spells a `:NNNN` no pin verifies.

    Twice now the file has repaired a cite while leaving the OLD number spelled
    in the prose beside it (`_VALUE_FREE_GATE`'s comment, and the docstring of
    `test_the_value_free_gate_cite_agrees_across_the_index_row_and_the_runbook`
    which kept the twin of the same two numbers), and twice the example was
    already wrong — pointing at a template slot's value and at two blank lines.
    A number in guidance is a citation nobody re-checks, so the file's own rule
    ("an example that rots is the defect restated in the guidance") can only be
    enforced mechanically.

    The contract it enforces is the one `#10742` established one level up: a
    number may appear in this module's prose ONLY if one of the four maps
    already pins it — in which case the sweeps above red when it rots, and the
    example cannot go stale on its own. A live cite that is not yet pinned is
    therefore reported with its fix: pin it, or drop it from the prose.
    """
    pinned = (
        set(_PUBLISHED_CITES)
        | set(_RUNBOOK_CITES)
        | {n for _spelled, n in _CROSS_FILE_CITES}
        | {_VALUE_FREE_GATE[0]}
    )
    bad = [
        f"  line {line_no}: `:{number}` — not a key of "
        f"_PUBLISHED_CITES / _RUNBOOK_CITES / _CROSS_FILE_CITES / "
        f"_VALUE_FREE_GATE"
        for line_no, number in _prose_cites(_prose_of(Path(__file__).read_text(
            encoding="utf-8")))
        if number not in pinned
    ]
    assert not bad, (
        "this file's guidance spells `:NNNN` numbers no pin verifies, so they "
        "rot unread — nothing in this module's sweeps reads an unqualified "
        "number, and an example that rots is the defect restated in the "
        "guidance. Either pin the number (add it to the map for its file) or "
        "describe it without spelling it:\n" + "\n".join(bad))
