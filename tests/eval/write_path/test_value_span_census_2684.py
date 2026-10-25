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
* the predicate + the stamp under test: the SHIPPED
  ``tortoise.extractor_v2._carries_value`` / ``_value_verbatim_span``.

The receipts predate #8659, so the census is a **re-derivation**, not an
observation: it answers *"what would the shipped mechanism stamp on these
committed points?"*. ``test_the_census_is_a_rederivation_not_an_observation``
pins that reading so the number is never mistaken for a measured extraction
run. (Re-ingesting the corpus with the span code and re-measuring is the
end-to-end half — wave scoping §6's T-2, re-homed to #8486; see
``docs/runbook/2684-slice-b-t-gate.md``.)

**What the numbers are NOT.** No reader, no judge, no answer accuracy. The
census counts whether a value can be re-read verbatim from an address; it says
nothing about whether the reader then sums it correctly.

Hermetic: no DB, no network, no model. Reads committed JSON only.
"""

from __future__ import annotations

import json
from pathlib import Path

from tortoise import extractor_v2 as v2

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

#: Absence classes. Every one is either REPORTED at the point of resolution or
#: silent BY DESIGN (there is no verbatim text to address), never a silent drop
#: of something that could have been addressed:
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
#:  * ``empty_quote``     — no quote, so no verbatim text exists to address.
#:                          Silent BY DESIGN (nothing was ever claimed
#:                          verbatim); pinned in
#:                          ``test_extractor_v2.py::test_empty_quote_model_index_path_carries_no_span``.
ABSENCE_CLASSES = ("band", "valuefree_quote", "no_turn", "empty_quote")

#: The census, measured 2026-10-24 at main + this branch's changes. Any change
#: here is a change in the SHIPPED mechanism's reach on real extractions or in
#: the committed corpus — re-run the census, then update this tuple AND the
#: table in ``docs/runbook/2684-slice-b-t-gate.md``. Do not "fix" a drift by
#: loosening an assertion: a lower span count is the finding.
CENSUS = {
    "value_bearing": 112,
    "spanned": 101,
    "absences": {"band": 6, "valuefree_quote": 3, "no_turn": 1,
                 "empty_quote": 1},
}


def _receipt(session: str) -> dict:
    return json.loads((COMMITTED_ARMS / f"{session}.arms.json").read_text())


def _edus(session: str) -> list[dict]:
    fixture = json.loads((WRITE_PATH / "fixtures" / f"{session}.json").read_text())
    return [{"index": i, "role": t["role"], "text": t["content"]}
            for i, t in enumerate(fixture["conversation"])]


def _points(session: str, arm: str) -> list[dict]:
    return _receipt(session)["embed_list"][arm]["points"]


def _absence_class(point: dict, quote: str, warnings: list[str]) -> str:
    if not quote:
        return "empty_quote"
    if point.get("source_turn_id") is None:
        return "no_turn"
    if any("token-overlap band" in w for w in warnings):
        return "band"
    if any("carries no value" in w for w in warnings):
        return "valuefree_quote"
    return "unclassified"          # never expected — see the census test


def _census() -> dict:
    """Bucket every value-bearing point in the committed receipts."""
    out = {"value_bearing": 0, "spanned": 0, "absences": {},
           "spans": [], "unclassified": []}
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
                span = v2._value_verbatim_span(
                    content, quote, point.get("source_turn_id"), edus,
                    warnings=warnings)
                if span is not None:
                    out["spanned"] += 1
                    out["spans"].append(
                        (session, arm, content, quote, span, edus))
                    continue
                klass = _absence_class(point, quote, warnings)
                if klass == "unclassified":
                    out["unclassified"].append((session, arm, content, quote))
                out["absences"][klass] = out["absences"].get(klass, 0) + 1
    return out


def test_the_census_is_a_rederivation_not_an_observation():
    """The committed receipts carry NO span keys at all — they were extracted
    before #8659. Pinned so the census below is never read as "the extractor
    stamped 101 spans in this run": it is what the SHIPPED mechanism stamps on
    points that were produced without it. The observation needs a re-ingest
    (the T-2 half, re-homed to #8486)."""
    total = spanned = 0
    for session in SESSIONS:
        for arm in ARMS:
            for point in _points(session, arm):
                total += 1
                spanned += int("span_start" in point or "span_end" in point)
    assert total, "the census has no population — the receipts did not load"
    assert spanned == 0, (
        f"{spanned} of {total} committed points carry a span — the receipts "
        "now postdate #8659, so this census IS an observation and the "
        "derivation caveat in the runbook must be retired")


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


def test_every_stamped_span_re_reads_the_quotes_own_words_and_a_value():
    """The invariant the slice exists for, asserted over the whole committed
    population (not just the unit cases): each span is in bounds, forward, its
    text carries a value, and that text is the point's OWN quoted words — a
    consumer re-reading it gets the value verbatim, not a neighbouring number."""
    census = _census()
    assert census["spans"], "no spans to check — the census is empty"
    for session, arm, content, quote, span, edus in census["spans"]:
        start, end = span
        assert 0 <= start < end, (session, arm, content[:60], span)
        turn = v2._turn_text_by_index(edus, _turn_of(census, session, arm,
                                                     content, quote))
        addressed = turn[start:end]
        assert end <= len(turn), (session, arm, span, len(turn))
        assert v2._carries_value(addressed), (
            f"{session}/{arm}: span addresses value-free text {addressed!r} "
            f"for {content[:60]!r}")
        norm = lambda s: " ".join(str(s).lower().split())
        assert norm(addressed) in norm(quote), (
            f"{session}/{arm}: span {addressed!r} is not the point's quoted "
            f"words ({quote[:80]!r})")


def _turn_of(census: dict, session: str, arm: str, content: str,
             quote: str) -> int:
    """The ``source_turn_id`` of the point this span came from (the receipt is
    the authority; the span's own turn text is whatever the function read)."""
    for point in _points(session, arm):
        if (point.get("content") or "") == content and \
                (point.get("quote") or "") == quote:
            return point.get("source_turn_id")
    raise AssertionError(f"point not found in {session}/{arm}: {content[:60]}")


# ── the relaxation a future lane will reach for — MEASURED UNSAFE ─────────
#
# The ``band`` class (6 points) is the visible cost of the strict anchor: a
# quote rewritten by one word keeps no address even though the value IS
# verbatim in the turn ("The cutoff stays September 30 as agreed" vs the turn's
# "Cutoff stays September 30 as agreed, …"). The obvious "improvement" is to
# trim the quote's leading/trailing tokens until a verbatim run is found. This
# pins what that relaxation actually produces on the committed corpus, so the
# next lane inherits the measurement instead of re-deriving it.
#
# Measured: 5 candidates for the 6 band-class points (one has none), and only
# TWO are whole-assertion addresses:
#
#   CLEAN    "two workers grabbed the same batch twice …"   (full sentence)
#   CLEAN    "Cutoff stays September 30 as agreed"          (the value, whole)
#   FRAGMENT "four shards"     — one of the point's two quantities; a consumer
#                                cannot tell which value it is summing
#   FRAGMENT "One flag"        — a number WORD that is a determiner, not a
#                                quantity
#   FRAGMENT "One"             — the SAME defect at its shortest: the search
#                                matches a shared word at the turn's start, in
#                                a sentence the point never quoted (#8966's
#                                false-address class, reintroduced)
#
# 3 of 5 are partial or wrong, so the relaxation is refused and the absence
# stays a REPORTED class. A red here means the candidate set moved — re-run
# the census and update the runbook, do not narrow the assertion.

EXPECTED_RELAXED_ADDRESSES = {
    "two workers grabbed the same batch twice from the ingest queue and each "
    "marked its own copy complete.",
    "four shards",
    "One flag",
    "One",
    "Cutoff stays September 30 as agreed",
}


def test_the_trimmed_anchor_relaxation_is_measured_unsafe():
    recovered: list[tuple[str, str, str, tuple[int, int]]] = []
    for session in SESSIONS:
        edus = _edus(session)
        for arm in ARMS:
            for point in _points(session, arm):
                content = point.get("content") or ""
                quote = point.get("quote") or ""
                if not v2._carries_value(content, quote):
                    continue
                warnings: list[str] = []
                if v2._value_verbatim_span(
                        content, quote, point.get("source_turn_id"), edus,
                        warnings=warnings) is not None:
                    continue
                if _absence_class(point, quote, warnings) != "band":
                    continue
                turn = v2._turn_text_by_index(edus, point.get("source_turn_id"))
                cand = _longest_quote_run(turn, quote)
                if cand is not None:
                    recovered.append((session, arm, cand[2], cand[0]))
    addressed = {t for _s, _a, t, _sp in recovered}
    assert addressed == EXPECTED_RELAXED_ADDRESSES, (
        "the relaxed-anchor candidate set moved — re-measure it and update "
        f"the runbook: {sorted(addressed)}")
    fragments = {t for t in addressed if len(t.split()) <= 2}
    assert fragments == {"four shards", "One flag", "One"}, (
        f"the FRAGMENT class changed: {sorted(fragments)} — these are the "
        "addresses that make the relaxation unsafe (a consumer cannot tell "
        "whether the fragment holds the quantity it is summing)")
    assert len(addressed - fragments) == 2, (
        "the two clean recoveries the strict rule refuses are the cases the "
        f"runbook cites: {sorted(addressed - fragments)}")


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
