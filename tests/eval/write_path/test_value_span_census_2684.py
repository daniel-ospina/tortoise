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
  against the resolved turn).

⛔ **The resolver is not optional in this census.** The receipt's
``source_turn_id`` is the model's ADVISORY index; the write path resolves the
turn from the quote first and never lets a wrong model index win
(``_resolve_source_turn`` docstring; call site ``extractor_v2.py:7478`` →
``:7494``). Passing the receipt field straight through measures a population
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
``pt_entry`` is built (``extractor_v2.py:7482-7496``). It is NOT a question of
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

Hermetic: no DB, no network, no model. Reads committed JSON only.
"""

from __future__ import annotations

import itertools
import json
import re
from pathlib import Path

from tortoise import extractor_v2 as v2
from tortoise.retrieval import (
    DEFAULT_PACKAGE_SAME_TURN_OVERLAP,
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
    fixture = json.loads((WRITE_PATH / "fixtures" / f"{session}.json").read_text())
    return [{"index": i, "role": t["role"], "text": t["content"]}
            for i, t in enumerate(fixture["conversation"])]


def _points(session: str, arm: str) -> list[dict]:
    return _receipt(session)["embed_list"][arm]["points"]


def _absence_class(turn_idx: int | None, quote: str,
                   warnings: list[str]) -> str:
    if not quote:
        return "empty_quote"
    if turn_idx is None:
        return "no_turn"
    if any("token-overlap band" in w for w in warnings):
        return "band"
    if any("carries no value" in w for w in warnings):
        return "valuefree_quote"
    return "unclassified"          # never expected — see the census test


def _census() -> dict:
    """Bucket every value-bearing point in the committed receipts, in the
    SHIPPED order: resolve the turn from the quote, then stamp against it."""
    out = {"value_bearing": 0, "spanned": 0, "absences": {},
           "spans": [], "unclassified": [], "moved": []}
    for session in SESSIONS:
        edus = _edus(session)
        for arm in ARMS:
            for point in _points(session, arm):
                content = point.get("content") or ""
                quote = point.get("quote") or ""
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
                klass = _absence_class(turn_idx, quote, warnings)
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
    total = spanned = 0
    for session in SESSIONS:
        for arm in ARMS:
            for point in _points(session, arm):
                total += 1
                spanned += int("span_start" in point or "span_end" in point)
    assert total, "the census has no population — the receipts did not load"
    assert spanned == 0, (
        f"{spanned} of {total} committed points carry a span key — the "
        "measuring harness now persists committed points (it used to persist "
        "the pre-pt_entry S2 model output), so this census became an "
        "OBSERVATION: re-measure the rate end-to-end, update CENSUS + the "
        "runbook's derivation caveat, and keep the observation/derivation "
        "distinction explicit")


def test_the_census_resolves_the_turn_the_way_the_write_path_does():
    """The census must not measure a population production never produces.

    The write path resolves the source turn from the quote BEFORE stamping
    (``extractor_v2.py:7478`` → ``:7494``), and the receipt's
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
    absences are all in a reported (or by-design-silent) class. An
    ``unclassified`` absence is the failure this test exists for: a
    value-bearing point whose value cannot be re-read AND whose absence no
    consumer can see."""
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


def test_every_absence_in_the_census_is_REPORTED():
    """**0 silent absences** — the claim the runbook and the index row publish.

    Measured over the whole committed population, in the SHIPPED order
    (``_resolve_source_turn`` then ``_value_verbatim_span``): every value-bearing
    absence must carry at least one warning, and it must be the warning of ITS
    OWN class — not any warning at all. The ``empty_quote`` leg is the one that
    matters: it has NO span-side warning (there is no verbatim text to address),
    so the only thing that keeps it out of the silent-drop class is the resolver,
    which runs first and reports it either as "unverified (empty quote) — using
    it" (in-range model index) or as "no resolvable source turn".

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
                content = point.get("content") or ""
                quote = point.get("quote") or ""
                if not v2._carries_value(content, quote):
                    continue
                warnings: list[str] = []
                turn_idx = v2._resolve_source_turn(point, edus,
                                                   warnings=warnings)
                if v2._value_verbatim_span(content, quote, turn_idx, edus,
                                           warnings=warnings) is not None:
                    continue
                klass = _absence_class(turn_idx, quote, warnings)
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
NEAR_DUPE_PAIRS_PER_ARM = 0  # ≥0.9 content overlap
NEAR_DUPE_VALUE_CONFLICTS_PER_ARM = 0

#: The dedupe half's SAME-SOURCE-TURN route (``_same_fact``'s second leg),
#: measured on the SAME per-arm universe as ``PAIRS_PER_ARM``: of those 433
#: pairs, the ones whose two ``source_turn_id`` keys are present and equal AND
#: whose two quotes are non-empty — the point at which the shipped predicate
#: consults its quote-level value guard — then split by that guard and by
#: ``DEFAULT_PACKAGE_SAME_TURN_OVERLAP``.
#: ⛔ This is the route the CONTENT-RATIO zero above says NOTHING about; the
#: runbook once generalised that zero to "the corpus cannot exercise the
#: value-safety leg at all", which these three numbers falsify.
SAME_TURN_DEDUPE = {"consulted": 12, "value_refused": 9, "collapsed": 1}


def _value_bearing_points(session: str, arm: str) -> list[dict]:
    return [p for p in _points(session, arm)
            if v2._carries_value(p.get("content") or "",
                                 p.get("quote") or "")]


def test_the_dedupe_leg_is_measured_on_BOTH_legs():
    """Pinned so no future lane reads "deduped: PASS" off this census, and so
    the numbers the runbook quotes are re-derived rather than asserted.

    TWO routes, and this corpus treats them differently:

    * **content-ratio** — in the guard's own per-arm universe there is **no**
      ≥ 0.9-overlap pair at all, so THAT route cannot be exercised here; the
      property rests on Slice A's unit guards
      (``tests/test_evidence_assembly.py``);
    * **same-source-turn** (``retrieval._same_fact``'s second route: same
      ``source_turn_id``, quote overlap ≥
      ``DEFAULT_PACKAGE_SAME_TURN_OVERLAP``, with
      ``_pkg_differ_value_critical`` consulted on the QUOTES first) — reachable
      HERE, and measured: ``SAME_TURN_DEDUPE``.

    ⛔ The distinction is the point of this test. §2.3 once generalised the
    content-ratio zero to "the corpus cannot exercise the value-safety leg at
    all", which is false and would tell a lane that a change to the
    same-turn/quote route is unmeasurable on this corpus."""
    pairs = near_dupe = conflicts = 0
    pooled_pairs = pooled_near_dupe = 0
    same_turn = {"consulted": 0, "value_refused": 0, "collapsed": 0}
    for session in SESSIONS:
        for arm in ARMS:
            points = _value_bearing_points(session, arm)
            for a, b in itertools.combinations(points, 2):
                pairs += 1
                if _pkg_overlap(a.get("content") or "",
                                b.get("content") or "") >= 0.9:
                    near_dupe += 1
                    if _pkg_differ_value_critical(a.get("content") or "",
                                                  b.get("content") or ""):
                        conflicts += 1
                # the second route: same source turn, both quotes present
                at = _pkg_turn_key(a)
                qa = str(a.get("quote") or "")
                qb = str(b.get("quote") or "")
                if not (at and at == _pkg_turn_key(b) and qa and qb):
                    continue
                same_turn["consulted"] += 1
                if _pkg_differ_value_critical(qa, qb):
                    same_turn["value_refused"] += 1
                elif _pkg_overlap(qa, qb) >= DEFAULT_PACKAGE_SAME_TURN_OVERLAP:
                    same_turn["collapsed"] += 1
        pooled = [p for arm in ARMS for p in _value_bearing_points(session, arm)]
        for a, b in itertools.combinations(pooled, 2):
            pooled_pairs += 1
            if _pkg_overlap(a.get("content") or "",
                            b.get("content") or "") >= 0.9:
                pooled_near_dupe += 1
    assert pairs == PAIRS_PER_ARM, (
        f"the per-arm within-session pair count moved: {pairs} != "
        f"{PAIRS_PER_ARM} — re-run and update the runbook §2.3")
    assert near_dupe == NEAR_DUPE_PAIRS_PER_ARM, (
        f"{near_dupe} pairs reach ≥0.9 content overlap in the guard's own "
        "universe — the CONTENT-RATIO route can now be exercised here, so §2.3 "
        "must be re-measured (it currently says that route cannot be)")
    assert conflicts == NEAR_DUPE_VALUE_CONFLICTS_PER_ARM, (
        f"{conflicts} near-dupe pairs differ in a value-critical token — §2.3 "
        "must be re-measured")
    assert same_turn == SAME_TURN_DEDUPE, (
        "the SAME-SOURCE-TURN route moved (measured before: "
        f"{SAME_TURN_DEDUPE}) — §2.3 quotes these three numbers, and this is the "
        "route the content-ratio zero says nothing about: re-measure both and "
        "update the runbook")
    # Measured context, deliberately loose: the zero above is a property of the
    # PER-ARM universe, not of the corpus in general. Pooling the two arms (a
    # pool no package holds) does produce near-dupes — a lane must not restate
    # the zero as "this corpus has no near-dupes".
    assert pooled_near_dupe > 0, (
        "pooling the two arms produced no ≥0.9 pair — the universe note in "
        f"§2.3 (pooled pairs={pooled_pairs}) no longer holds")


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
                content = point.get("content") or ""
                quote = point.get("quote") or ""
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
                        turn_idx, quote, warnings) == "band":
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
                        adv_idx, quote, adv_warnings) == "band":
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
