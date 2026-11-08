"""#11534 — the PARTIAL-gap detector and its ADD-capable consumer.

Two things are pinned here, and they are different kinds of claim.

**The detector** (`vet_gate.partial_gaps`) — a lexical coverage instrument that
names WHICH narrative span no candidate accounts for. `check_batch`'s
`coverage_suspect` can only fire on a zero-candidate batch
(`tortoise/vet_gate.py`, ``bool(statements and total == 0)``), so the case this
issue exists for — a narrative that yielded candidates and still left a
statement uncovered — is invisible to every other instrument in the tree.

**The measurement** — the detector's own precision/recall over the F3
population, pinned against committed artifacts so it cannot rot into folklore:

* inputs: the seven recorded sessions' narratives + S2 lists in
  ``tests/eval/write_path/receipts/7947-arms-2026-10-18/*.arms.json``;
* labels: S4's own ``legacy``-arm ``added_items`` in
  ``docs/evidence/1789-s4-reeemit-tax/ab-20261107T02{3958,4226}Z.json``.

The measured result (asserted below, and argued in
``docs/evidence/11534-partial-gap/README.md``): the detector reaches 9 of the 21
items S4 added across both runs — **8/12 of its point additions and 1/9 of its
operator additions**. That asymmetry is the finding, not a defect to tune away:
S4's operator additions are GENERALISATIONS of statements the batch already
covers, so no narrative span is uncovered even though the item is new. It is why
the ADD stage below is not a like-for-like replacement for `run_s4`, and why the
stage's removal stays gated (#11534 target 3).
"""
from __future__ import annotations

import glob
import json
import os

from tortoise import extractor_v2 as v2
from tortoise import vet_gate

# ── fixtures ───────────────────────────────────────────────────────────────

#: Three statements, the third of which no candidate below carries.
NARRATIVE = (
    "The wire stage stalled because the retry loop bypassed the batch "
    "ownership check. The lease is keyed by batch id with a thirty-second "
    "expiry and a compare-and-swap update on completion. The hydrate stage "
    "shares the same code as the wire stage but was audited and found clean."
)

COVERED_ITEMS = {
    "entities": [{"name": "wire stage", "kind": "core:workflow"},
                 {"name": "hydrate stage", "kind": "core:workflow"}],
    "events": [],
    "points": [
        {"content": "The retry loop in the batch runner bypassed the batch "
                    "ownership check, so the wire stage stalled.",
         "pointKind": "statement"},
        {"content": "The lease is keyed by batch id with a thirty-second "
                    "expiry and a compare-and-swap update on completion.",
         "pointKind": "statement"},
    ],
    "operators": [],
}

#: The SAME narrative with a point for EVERY statement — the false-positive
#: direction. ``COVERED_ITEMS`` above is the *partial-gap* fixture (two of three
#: statements covered), so a row named "fully covered is not flagged" cannot use
#: it: at this head ``partial_gaps(COVERED_ITEMS, NARRATIVE)["flagged_total"]``
#: is **1**, which is the point of that fixture (review round 5).
FULLY_COVERED_ITEMS = {
    "entities": COVERED_ITEMS["entities"],
    "events": [],
    "points": [*COVERED_ITEMS["points"],
               {"content": "The hydrate stage shares the same code as the wire "
                           "stage but was audited and found clean.",
                "pointKind": "statement"}],
    "operators": [],
}

#: The same session with the covering lease point REMOVED — the non-vacuity
#: control. A detector that flags nothing here is measuring nothing.
GAPPED_ITEMS = {
    "entities": COVERED_ITEMS["entities"],
    "events": [],
    "points": COVERED_ITEMS["points"][:1],
    "operators": [],
}


def _flagged_texts(gaps: dict) -> list[str]:
    return [entry["text"] for entry in gaps["flagged"]]


#: The fixture the ORDERING row needs: a population where narrative order and
#: coverage order DISAGREE. ``GAPPED_ITEMS`` cannot serve it — its two flags
#: already arrive least-covered-first, so a row asserting only
#: ``coverages == sorted(coverages)`` stays GREEN with the sort deleted. Here the
#: FIRST sentence is partly covered (coverage > 0) while the later ones are
#: covered by nothing (0.0), so the least-covered span sits LAST in narrative
#: order.
_ORDER_NARRATIVE = (
    "The lease is keyed by batch id with a thirty-second expiry, and the audit "
    "trail is written by the wire stage. The hydrate stage reconciles the retry "
    "ledger against the poison queue on every restart.")
_ORDER_ITEMS = {
    "entities": [],
    "events": [],
    # covers only the first two content words of the first clause: enough to
    # move that segment's coverage off 0.0, not enough to reach the floor.
    "points": [{"content": "The lease is keyed"}],
    "operators": [],
}


# ── the detector: does it name the uncovered statement? ────────────────────


def test_a_partial_gap_is_flagged_and_names_its_statement():
    """`total > 0` AND one uncovered statement — the exact case
    `coverage_suspect` cannot see. The flag must NAME the statement."""
    batch = vet_gate.check_batch(COVERED_ITEMS, NARRATIVE)
    # the case this issue exists for: candidates exist, so the old signal is off
    assert batch["candidates"] > 0
    assert batch["coverage_suspect"] is False

    gaps = batch["partial_gaps"]
    assert gaps["flagged_total"] == 1, (
        "exactly the hydrate/code statement is uncovered — nothing else")
    entry = gaps["flagged"][0]
    assert "hydrate stage" in entry["text"]
    assert "code" in entry["text"]
    # ... and it names a STATEMENT, not just a count. ⛔ THE ADDRESS IS PINNED TO
    # ITS VALUE, not to `isinstance(..., int)` (review, P2): a constant index
    # (`statement=0, clause=0` for every segment) kept an isinstance-only row
    # green while destroying the field the checklist actually asks for — the
    # same name-vs-assertion gap round 5 fixed elsewhere in this file.
    assert (entry["statement"], entry["clause"]) == (2, 0), (
        "the hydrate/code clause is the THIRD sentence of NARRATIVE (2) and its "
        "first clause (0); see test_the_statement_address_is_an_ordinal for why "
        "the index is this function's own ordinal and not the reported count")
    assert entry["coverage"] < gaps["coverage_floor"]
    # `missing` carries STEMS (the same lens coverage is computed in), so the
    # assertion goes through the stemmer rather than guessing its output.
    missing = set(entry["missing"])
    assert {vet_gate._gap_stem("code"), vet_gate._gap_stem("shares")} <= missing


def test_the_span_address_indexes_the_original_narrative():
    """Every flagged span slices back out of the narrative it was judged in.

    The address is what a re-extraction reads, so an offset that does not
    round-trip is worse than no offset at all.
    """
    gaps = vet_gate.partial_gaps(GAPPED_ITEMS, NARRATIVE)
    assert gaps["flagged"], "non-vacuity: the gapped fixture must flag"
    for entry in gaps["flagged"]:
        assert NARRATIVE[entry["start"]:entry["end"]].strip() == entry["text"]
    # the same holds for every segment the detector considered, not only the
    # flagged ones — the address must not depend on the verdict
    for segment in vet_gate.narrative_segments(NARRATIVE):
        assert NARRATIVE[segment["start"]:segment["end"]].strip() == \
            segment["text"]


def test_a_fully_covered_narrative_is_not_flagged():
    """The false-positive direction: coverage must not be reported as a gap.

    ⛔ THIS ROW'S NAME WAS FALSE UNTIL ROUND 5 and the shape is worth keeping
    visible: it asserted `flagged_total == 1` on `COVERED_ITEMS`, a fixture whose
    third statement has no covering item — so it pinned "the covered statement is
    not flagged" (a real thing, and its own second assertion) while carrying the
    name and the `#11534` checklist row for the OPPOSITE claim. The last line
    below is the property the name claims, and nothing asserted it: reverting the
    behaviour moved this quantity from 0 to a positive number with every row
    still green — a surviving mutant, not an equivalent one.
    """
    assert vet_gate.partial_gaps(FULLY_COVERED_ITEMS, NARRATIVE)[
        "flagged_total"] == 0, "a narrative every statement of which is covered is clean"
    # ... and the partial-gap fixture is what its name says it is: one statement
    # uncovered, and exactly that one is flagged.
    gaps = vet_gate.partial_gaps(COVERED_ITEMS, NARRATIVE)
    assert gaps["flagged_total"] == 1
    assert "hydrate stage" in gaps["flagged"][0]["text"]
    assert not any("lease is keyed" in text for text in _flagged_texts(gaps))


def test_the_gapped_fixture_is_the_same_fixture_minus_one_item():
    """Non-vacuity, stated as the property this row actually asserts: removing a
    covering item from the SAME narrative RAISES the flag count (`2 > 1`).

    ⚠️ Not "and is clean when present" (round 5): `COVERED_ITEMS` still leaves the
    third statement uncovered, so the absolute count is 1 → 2. The clean-vs-absent
    property lives in `test_a_fully_covered_narrative_is_not_flagged`, on
    `FULLY_COVERED_ITEMS`. Without this pair the
    other tests could pass on a detector that flags everything."""
    with_item = vet_gate.partial_gaps(COVERED_ITEMS, NARRATIVE)
    without = vet_gate.partial_gaps(GAPPED_ITEMS, NARRATIVE)
    assert without["flagged_total"] > with_item["flagged_total"]
    assert any("lease is keyed" in text for text in _flagged_texts(without))
    assert not any("lease is keyed" in text for text in _flagged_texts(with_item))


def test_detector_is_total_on_malformed_shapes():
    """Fail-open on every shape a caller can pass: this reports, it never
    raises, and an empty candidate set flags the prose (the honest answer —
    with no candidates nothing is accounted for)."""
    assert vet_gate.partial_gaps(None, None)["flagged_total"] == 0
    assert vet_gate.partial_gaps({}, "")["segments"] == 0
    # a malformed candidate list is not an exception — every content-bearing
    # segment is flagged, which is the honest answer: nothing is accounted for
    malformed = vet_gate.partial_gaps("not a mapping", NARRATIVE)
    assert malformed["segments"] > 0
    # every segment that clears the content-token floor is flagged, and at
    # coverage ZERO: with no candidates nothing at all is accounted for
    assert malformed["flagged_total"] >= 1
    assert all(entry["coverage"] == 0.0 for entry in malformed["flagged"])
    gaps = vet_gate.partial_gaps({}, NARRATIVE)
    assert gaps["flagged_total"] >= 1
    # ⚠️ The `segments` half of this arm was DEAD WEIGHT and is gone (review
    # round 3, rule 3): `segments` is computed purely from the narrative, so no
    # mutation of the embed-list path can move it, and asserting it equalled
    # another dict's `segments` was a tautology. The `flagged`/`coverage` arms
    # above are the live ones; the malformed-list arm below is a real one.
    bad_section = vet_gate.partial_gaps({"points": "a string"}, NARRATIVE)
    assert bad_section["flagged_total"] == gaps["flagged_total"], (
        "a non-list section value is treated as empty, not as a crash")
    assert vet_gate.narrative_segments(None) == []


def test_coverage_suspect_is_unchanged_by_the_new_signal():
    """`coverage_suspect` keeps its exact old meaning — the new key is
    additive, so no existing consumer's reading shifts."""
    zero = vet_gate.check_batch({}, NARRATIVE)
    assert zero["coverage_suspect"] is True
    assert zero["partial_gaps"]["flagged_total"] >= 1
    one = vet_gate.check_batch(COVERED_ITEMS, NARRATIVE)
    assert one["coverage_suspect"] is False
    # the old keys are still all present
    assert set(zero) >= {"narrative_statements", "candidates", "by_section",
                         "coverage_ratio", "coverage_suspect"}


def test_the_flagged_list_is_capped_but_the_count_is_true():
    """`stats["batch"]` is a session-sized document; the cap bounds it without
    lying about how many gaps there were."""
    gaps = vet_gate.partial_gaps({}, NARRATIVE, min_content_tokens=1,
                                 max_flagged=1)
    assert gaps["flagged_total"] > 1
    assert len(gaps["flagged"]) == 1
    assert gaps["truncated"] is True


def test_the_flagged_list_is_ordered_least_covered_first():
    """The ordering is LOAD-BEARING and was pinned by nothing (review, P2).

    ``partial_gaps`` documents ascending coverage, and two consumers depend on
    it rather than merely preferring it: ``render_partial_gap_prompt`` enumerates
    the spans in list order, and ``flagged[:max_flagged]`` truncates AFTER the
    sort — so with the sort removed, a session flagging more than
    ``GAP_MAX_FLAGGED`` spans hands the model the first 20 in NARRATIVE order
    instead of the 20 least-covered. Deleting the one sort line therefore
    changes which spans the stage ever sees.

    ⛔ ``GAPPED_ITEMS`` CANNOT PIN THIS, and the first form of this row used it:
    that fixture's two flags already arrive least-covered-first, so the row
    stayed GREEN with the sort line deleted (caught by mutation).
    ``_ORDER_ITEMS`` is built for the job — its least-covered spans sit LAST in
    narrative order — and the `starts` assertion below is what makes the row
    discriminate: it fails the moment the output is narrative-ordered again.
    """
    gaps = vet_gate.partial_gaps(_ORDER_ITEMS, _ORDER_NARRATIVE)
    coverages = [entry["coverage"] for entry in gaps["flagged"]]
    starts = [entry["start"] for entry in gaps["flagged"]]
    assert len(coverages) >= 2, "non-vacuity: the ordering needs two entries"
    assert coverages == sorted(coverages), (
        "ascending coverage: the least accounted-for span comes first")
    assert starts != sorted(starts), (
        "non-vacuity of the ASSERTION ITSELF: on this fixture narrative order "
        "and coverage order differ, so this row passes only if the sort ran")
    # The cap takes the k LOWEST, not the first k in narrative order.
    capped = vet_gate.partial_gaps(_ORDER_ITEMS, _ORDER_NARRATIVE, max_flagged=1)
    assert [e["coverage"] for e in capped["flagged"]] == [coverages[0]]
    assert capped["flagged"][0]["start"] != min(starts), (
        "the single span kept is a least-covered one, NOT the first span")
    assert capped["truncated"] is True


def test_markdown_headings_are_not_statements(monkeypatch):
    """A heading spanning a whole clause is blanked before segmentation.

    ⛔ THIS TEST WAS UNFALSIFIABLE IN ITS FIRST FORM (review round 2, P1) and the
    shape is worth keeping visible: it asserted that no flagged span contained
    ``"Narrative:"`` — but ``_GAP_CLAUSE`` splits every segment ON the colon, so
    the segment text is the slice BETWEEN separators and that string can never
    appear in ANY segment, flagged or not. The assertion was true by
    construction; with both guards switched off it still passed while
    ``'## Narrative'`` was demonstrably being flagged.

    The fixture is therefore a heading that is ONE clause, carries >= 4 content
    words and has NO colon — so the heading is removable only by the mask, and
    the second half below proves the assertion CAN fail by removing the mask and
    watching it flag.
    """
    heading = "## The wire stage stalled again yesterday"
    headed = heading + "\n\n" + NARRATIVE
    masked = vet_gate.partial_gaps(COVERED_ITEMS, headed)
    assert not any("wire stage stalled again" in text.lower()
                   for text in _flagged_texts(masked)), (
        "a blanked heading must not be flagged as uncovered prose")

    # NON-VACUITY — the same fixture with the mask neutralised DOES flag it,
    # so the assertion above is a behavioural one rather than a tautology.
    monkeypatch.setattr(vet_gate, "_gap_mask", lambda text: str(text or ""))
    unmasked = vet_gate.partial_gaps(COVERED_ITEMS, headed)
    assert any("wire stage stalled again" in text.lower()
               for text in _flagged_texts(unmasked)), (
        "with the heading mask removed the heading must be flagged, or this test "
        "cannot observe the guard it names")


def test_the_gap_stem_meets_the_word_forms_each_other():
    """Base-form SYMMETRY, not just suffix stripping (review round 2, P2).

    The first stemmer stripped a suffix only when the remainder was long enough,
    which is asymmetric: the plural loses ``es`` AND the ``e`` while the singular
    keeps its ``e`` — so ``share``/``shares`` (the docstring's own example) did
    not meet, and the miss lowers coverage, i.e. it INVENTS a gap. Measured
    before the fix: 2 of the population's 70 flagged spans were this class.
    """
    for base, inflected in (("stage", "stages"), ("lease", "leases"),
                            ("cache", "caches"), ("queue", "queues"),
                            ("service", "services"), ("share", "shares"),
                            ("policy", "policies"), ("state", "stated")):
        assert vet_gate._gap_stem(base) == vet_gate._gap_stem(inflected), (
            f"{base} and {inflected} must meet — an unequal pair lowers coverage "
            "and manufactures a gap")


def test_the_detector_reports_on_the_unarmed_arm():
    """`check_batch` carries the additive key on the default arm.

    Pins the correction of a false invariant: an earlier comment claimed an
    unarmed run's stats were byte-identical to pre-#11534, while `check_batch`
    reports `partial_gaps` unconditionally on the default arm (the S2.2
    mechanical half runs by default, #10996).

    ⛔ THIS ROW DOES NOT PIN THE `_run_vet_pass` -> `stats["batch"]` THREADING,
    and its first sentence used to claim it did (review, P2): the body only calls
    `check_batch` and never builds a `result`, so a narrowing of that copy would
    have left this row green while the key stopped reaching `result["vet"]`.
    `test_the_stage_is_off_by_default` runs the unarmed pipeline and is the row
    that pins it.
    """
    batch = vet_gate.check_batch(COVERED_ITEMS, NARRATIVE)
    assert "partial_gaps" in batch
    assert set(batch["partial_gaps"]) >= {"segments", "flagged",
                                           "flagged_total", "coverage_floor"}


# ── the ADD-capable stage: a flagged candidate reaches the embed list ─────

_MISSING_POINT = ("The hydrate stage shares the same code as the wire stage "
                  "but was audited and found clean.")


class _Model:
    """A stub model: S1 narrates, S2 covers two of three statements, the
    coverage reviewer emits the third. Prompt-keyed, in the shape the other
    extractor tests use."""

    last_finish_reason = None

    def __init__(self, *, gap_answer: str | None = None, gap_raises: bool = False,
                 s2_answer: dict | None = None):
        self.calls: list[str] = []
        self.gap_answer = gap_answer
        self.gap_raises = gap_raises
        self.s2_answer = s2_answer

    def complete(self, *, system, user, max_tokens=None):
        self.last_finish_reason = "stop"
        if "STORY SUMMARIZER" in system:
            self.calls.append("s1")
            return NARRATIVE
        if "COVERAGE REVIEWER" in system:
            self.calls.append("gap_fill")
            if self.gap_raises:
                raise RuntimeError("gap-fill model down")
            if self.gap_answer is not None:
                return self.gap_answer
            return json.dumps({"entities": [], "events": [], "operators": [],
                               "points": [{"content": _MISSING_POINT,
                                           "pointKind": "statement"}]})
        if "GAP REVIEWER" in system:
            self.calls.append("s4")
            return '{"entities": [], "events": [], "operators": [], "points": []}'
        self.calls.append("s2")
        return json.dumps(self.s2_answer if self.s2_answer is not None
                          else COVERED_ITEMS)


def _conv():
    from tests.test_extractor_reliability import _conv as base_conv
    return base_conv()


def _run(monkeypatch, model):
    monkeypatch.setenv("TORTOISE_PARTIAL_GAP_FILL", "1")
    return v2.extract_session_v2(model, _conv())


def test_a_flagged_candidate_reaches_the_embed_list(monkeypatch):
    """#11534 target 2: the ADD path emits the flagged statement's candidate,
    on a fixture where `coverage_suspect` is False — the case `#11002`'s
    bounded re-narrate provably cannot reach."""
    model = _Model()
    out = _run(monkeypatch, model)
    assert "gap_fill" in model.calls
    contents = [str(p.get("content") or "")
                for p in out["embed_list"]["points"]]
    assert any("shares the same code" in c for c in contents), (
        "the flagged statement's candidate must reach result['embed_list']")

    stats = out["stats"]["partial_gap_fill"]
    assert stats["armed"] is True and stats["fired"] is True
    assert stats["flagged_before"] >= 1
    assert stats["flagged_after"] < stats["flagged_before"], (
        "the stage's own claim is that it CLOSES a gap; flagged_after must fall")
    assert stats["added_items"] >= 1


def test_an_operator_only_answer_is_not_recorded_as_zero_additions(monkeypatch):
    """A VALID operator-only answer adds an item and must not record 0 (P1).

    ``added_items`` used to be ``check_batch["candidates"]`` after-minus-before,
    and that counter walks ``vet_gate.SECTIONS`` — entities, events, points —
    which has no ``operators`` entry. This stage DOES merge operators and the
    prompt explicitly authorises them, so an operator-only answer merged its
    item, left ``candidates`` unchanged, and recorded ``added_items: 0``: a
    false zero for the operator half of the measurement — the half (1 of 9)
    `run_s4`'s removal is gated on. ``flagged_before``/``flagged_after`` cannot
    disclose it either (the detector scores candidate TEXT, and no span is
    un-covered merely because an operator is absent), which is why the row
    asserts them UNCHANGED alongside the non-zero addition.

    ⛔ THE FIXTURE IS THE **CONTRACT** SHAPE, and the first version of this row
    was not: it invented ``{kind, source, target, content}``, which no producer
    emits, so it passed while a real operator still recorded zero
    (``_OUTPUT_SCHEMA``: ``{"src": str, "dst": str, "op_type": ...}`` — no
    ``name``, no ``content``). Both shapes are exercised below, because the
    counter must not under-count an operator the merge accepts either.
    """
    contract_op = {"src": "the hydrate stage", "dst": "the wire stage",
                   "op_type": "IMPL"}
    model = _Model(gap_answer=json.dumps(
        {"entities": [], "events": [], "operators": [contract_op],
         "points": []}))
    out = _run(monkeypatch, model)
    assert model.calls.count("gap_fill") == 1, "the stage must have fired"
    stats = out["stats"]["partial_gap_fill"]
    assert out["embed_list"]["operators"], (
        "non-vacuity: the operator must actually be in the embed list")
    assert stats["added_items"] == 1, (
        "the CONTRACT-shaped operator did not reach the embed list")
    assert stats["added_by_section"] == {"operators": 1}
    # the detector cannot see this addition — the very reason a separate count
    # is needed, and the reason `added_items: 0` was so believable.
    assert stats["flagged_before"] == stats["flagged_after"]


def test_an_operator_carrying_only_name_or_content_is_still_counted(
        monkeypatch):
    """The counter's operator arm is ADDITIVE, not a contract filter.

    ``merge_embed_lists`` merges an operator-shaped item whether or not it is
    contract-valid, so a predicate that required ``src``/``dst``/``op_type``
    would under-count a merged item — the same false zero in the other
    direction. An operator carrying only ``content`` (or only ``name``) is
    therefore counted too.
    """
    off_contract = {"kind": "causes",
                    "content": "the hydrate stage shares code with the wire stage"}
    model = _Model(gap_answer=json.dumps(
        {"entities": [], "events": [], "operators": [off_contract],
         "points": []}))
    out = _run(monkeypatch, model)
    stats = out["stats"]["partial_gap_fill"]
    assert out["embed_list"]["operators"], "non-vacuity: it merged"
    assert stats["added_items"] == 1
    assert stats["added_by_section"] == {"operators": 1}
    # ... and an EMPTY operator adds nothing, so the arm is not counting noise.
    model = _Model(gap_answer=json.dumps(
        {"entities": [], "events": [], "operators": [{}], "points": []}))
    stats = _run(monkeypatch, model)["stats"]["partial_gap_fill"]
    assert stats["added_items"] == 0
    assert "added_by_section" not in stats


def test_coverage_suspect_is_false_on_that_fixture():
    """The precondition the whole test rests on, asserted rather than assumed:
    a batch with candidates cannot fire the old coverage signal."""
    batch = vet_gate.check_batch(COVERED_ITEMS, NARRATIVE)
    assert batch["candidates"] > 0
    assert batch["coverage_suspect"] is False
    assert batch["partial_gaps"]["flagged_total"] == 1


def test_the_stage_is_off_by_default(monkeypatch):
    """DEFAULT OFF is a measured position, not caution (see the block comment
    on `_partial_gap_fill_enabled`). Off, the pipeline must not call the model
    a third time, and `stats` must not grow a key describing a stage that did
    not run."""
    monkeypatch.delenv("TORTOISE_PARTIAL_GAP_FILL", raising=False)
    model = _Model()
    out = v2.extract_session_v2(model, _conv())
    assert "gap_fill" not in model.calls
    assert "partial_gap_fill" not in out["stats"]
    # ⛔ THE THREADING IS PINNED HERE, not by `test_the_detector_reports_on_the_
    # unarmed_arm` — that row calls `check_batch` directly and never builds a
    # `result`, so a narrowing of the `_run_vet_pass` → `stats["batch"]` copy
    # would have left every row green while the key stopped reaching
    # `result["vet"]` (review round 5, P3). This is the row that runs the unarmed
    # pipeline, so it is the row that must assert the key arrives.
    assert out["vet"]["s2"]["batch"]["partial_gaps"]["segments"] > 0


def test_a_malformed_gap_fill_answer_is_not_scored_as_no_claim(monkeypatch):
    """MALFORMED ≠ EMPTY, on the STAGE's answer (review round 4, P1).

    `_s4_section_is_content` is False for a truthy-but-garbage section, exactly
    as for an absent one — so before this guard an all-malformed answer fell to
    the "the model found no claim in the spans" branch and the run recorded
    `added_items: 0`. That is a false statement about a run whose model DID emit
    content, and it corrupts the benefit side of the A/B whose cost side is
    `stats["llm"]`. S4 has carried `_s4_malformed_sections` since #1789 Task 3,
    which found this same silent loss there; this stage did not call it.
    """
    model = _Model(gap_answer=json.dumps({"points": "oops"}))
    out = _run(monkeypatch, model)
    stats = out["stats"]["partial_gap_fill"]
    assert stats["malformed_sections"] == ["points"]
    assert out["error_census"].get("gap_fill_shape_error") == 1
    assert any("malformed section 'points'" in e for e in out["errors"])
    # the garbage section cannot be scored as a legitimate zero-benefit session
    assert stats["added_items"] == 0
    assert "flagged_before" not in stats, (
        "nothing valid arrived, so the union was never re-scored — that is the "
        "state the census above distinguishes from 'the spans state no claim'")


def test_a_partly_malformed_gap_fill_answer_still_merges_the_valid_section(
        monkeypatch):
    """The per-section half: a garbage `entities` must not take the valid
    `points` down with it, and must not vanish without a census.

    Before the guard `any(...)` was True (the valid points), the merge ran, and
    `merge_embed_lists` filtered the garbage section out — no error, no census.
    """
    answer = json.dumps({"points": [{"content": _MISSING_POINT,
                                     "pointKind": "statement"}],
                         "entities": "garbage"})
    model = _Model(gap_answer=answer)
    out = _run(monkeypatch, model)
    stats = out["stats"]["partial_gap_fill"]
    assert stats["malformed_sections"] == ["entities"]
    assert out["error_census"].get("gap_fill_shape_error") == 1
    # the VALID section still reached the union
    assert stats["added_items"] >= 1
    assert stats["flagged_after"] < stats["flagged_before"]
    assert any(_MISSING_POINT[:40] in (p.get("content") or "")
               for p in out["embed_list"]["points"])


def test_a_wrapped_gap_fill_answer_is_recorded_malformed_not_as_no_claim(
        monkeypatch):
    """An UNKNOWN key carrying content is a SHAPE error, not "no claim"
    (review round 5, P2) — the same silent-drop class as a garbage `points`,
    reached by a shape `_s4_malformed_sections` cannot see.

    `_s4_malformed_sections` classifies only `_OUTPUT_SCHEMA` keys, so
    `{"items": [...]}` / `{"claims": [...]}` / a bare unwrapped item passed it
    untouched; `_s4_section_is_content` then counted it as content (the gate
    fired) and `merge_embed_lists` — which iterates a FIXED section tuple —
    dropped it. The run recorded `added_items: 0` with NO error and NO census,
    indistinguishable from a model that legitimately found nothing, which
    corrupts the benefit side of the A/B whose cost side is `stats["llm"]`.
    """
    wrapped = _Model(gap_answer=json.dumps(
        {"items": [{"content": _MISSING_POINT}]}))
    out = _run(monkeypatch, wrapped)
    stats = out["stats"]["partial_gap_fill"]
    assert stats["malformed_sections"] == ["items"]
    assert out["error_census"].get("gap_fill_shape_error") == 1
    assert any("malformed section 'items'" in e for e in out["errors"])
    assert stats["added_items"] == 0
    assert "flagged_before" not in stats, (
        "nothing usable arrived, so the union was never re-scored — the census "
        "above is what distinguishes it from 'the spans state no claim'")
    assert not any(_MISSING_POINT[:40] in (p.get("content") or "")
                   for p in out["embed_list"]["points"]), (
        "the dropped content must not have reached the union")

    # A truthy NON-list unknown key is the same class: it carries content and
    # the merge drops it, so it must be censused too.
    stringy = _Model(gap_answer=json.dumps({"items": "the spans state a claim"}))
    out2 = _run(monkeypatch, stringy)
    assert out2["stats"]["partial_gap_fill"]["malformed_sections"] == ["items"]
    assert out2["error_census"].get("gap_fill_shape_error") == 1

    # A bare single item (`[{...}]`, which `_parse_json_robust` unwraps to the
    # item itself) has no section keys at all — also a shape error, also lost
    # silently before this guard.
    bare = _Model(gap_answer=json.dumps(
        [{"content": _MISSING_POINT, "pointKind": "statement"}]))
    out3 = _run(monkeypatch, bare)
    stats3 = out3["stats"]["partial_gap_fill"]
    assert stats3["malformed_sections"], (
        "an unwrapped item carries content under keys that are not sections")
    assert out3["error_census"].get("gap_fill_shape_error") >= 1, (
        "one census bump per dropped section, and a bare item has several")


def test_the_statement_index_is_an_ADDRESS_not_the_statement_count():
    """The docstring used to claim the splitter and `_narrative_statements`
    "agree only up to the headings" — FALSE, and measured (review round 6, P2).

    `_SENTENCE_SPLIT` breaks a terminator only where whitespace or end-of-string
    follows it; `narrative_segments` breaks on EVERY `.`/`;`. So a DECIMAL POINT
    and a terminator immediately followed by markdown (`.**`) each open a segment
    that `_narrative_statements` does not count — which is why `flagged[].
    statement` is an address into a different indexing, never the reported count,
    and why the evidence record's `structural fragments` class exists.

    The behaviour is pinned rather than repaired: changing it would move every
    number the F3 measurement (#11534) and `docs/evidence/11534-partial-gap/`
    quote, so a fix is a re-measurement, not a cleanup.
    """
    decimal = "The run hit 4.2 second latency. Then it settled."
    texts = [seg["text"] for seg in vet_gate.narrative_segments(decimal)]
    assert "The run hit 4" in texts and "2 second latency" in texts, (
        "the splitter breaks on the decimal point — if this arm now passes as one "
        "clause, the splitter changed and every measured number must be re-derived")
    bold = "It works.** The next claim follows here."
    bold_texts = [seg["text"] for seg in vet_gate.narrative_segments(bold)]
    assert len(bold_texts) == 2 and bold_texts[0] == "It works", (
        "`.**` opens a segment here while `_SENTENCE_SPLIT` does not break there")
    # and the two indices really do disagree on this input
    assert max(seg["statement"] for seg in vet_gate.narrative_segments(decimal)) + 1 \
        != vet_gate._narrative_statements(decimal), (
        "the docstring's invariant would be true if these agreed — it is not")


def test_the_census_whitelist_is_the_merges_keep_set():
    """The dropped-key census asks `_OUTPUT_SCHEMA`, so that set MUST be the set
    `merge_embed_lists` actually keeps (review round 6, P2).

    They held the same nine keys but nothing compared them. A section added to
    `merge_embed_lists` alone would make the census classify VALID content as
    malformed — dropping it and recording a fabricated `gap_fill_shape_error` —
    and a section added to `_OUTPUT_SCHEMA` alone would pass the census and then
    be dropped by the merge with no record at all: the round-5 P2 reopened.
    Pinned here rather than by sharing a constant, because sharing one moves
    every line in `extractor_v2.py` and re-stales the #7949 bundle's absolute
    cites — a 311-number re-derivation for a latent hazard a pin closes.

    ⛔ AND THE PIN WAS ONE-DIRECTIONAL UNTIL THE #11554 REVIEW (P2): it iterated
    `_OUTPUT_SCHEMA` only, so it could not see the direction that LOSES content
    — a channel `merge_embed_lists` KEEPS that the census does not know about.
    `mention_held` was exactly that: the merge unions it by surface, the census
    called it malformed, and a `mention_held` row was dropped and recorded as a
    fabricated `gap_fill_shape_error`. The loop below covers BOTH halves of the
    keep-set, and asserts the census's own answer (`_unknown_delta_sections`)
    as well as the merge's behaviour.

    ⛔ WHAT THIS DOES **NOT** PIN, because an earlier version of this docstring
    claimed it did: the REVERSE direction — a channel added to
    `merge_embed_lists`' own section list and not to `_MERGE_KEPT_EXTRA_SECTIONS`.
    That list is a LOCAL variable in the merge, so no test can read it; the only
    reverse evidence here is the single `{"items": …}` probe below. Say so rather
    than claiming a bidirectionality the code cannot provide.
    """
    for section in (*v2._OUTPUT_SCHEMA, *v2._MERGE_KEPT_EXTRA_SECTIONS):
        # `mention_held` rows are keyed by SURFACE, not by `content` — a probe
        # item that does not satisfy the section's own row shape merges to an
        # EMPTY list and the assertion below would read as "the merge drops it".
        _item = ({"surface": "it", "rule_id": "referentiality.pronoun"}
                 if section == "mention_held" else {"content": "x"})
        merged = v2.merge_embed_lists({}, {section: [_item]})
        assert merged.get(section), (
            f"`{section}` is treated as a KNOWN channel, so the census does not "
            "flag it — but merge_embed_lists DROPS it, which is exactly the "
            "silent drop the census exists to catch")
        assert v2._unknown_delta_sections({section: [_item]}) == [], (
            f"`{section}` is KEPT by the merge, so the census must not classify "
            "it as malformed — that drops it AND records a shape error for a "
            "valid answer")
    assert not any(v2.merge_embed_lists({}, {"items": [{"content": "x"}]})), (
        "a key outside the keep-set is dropped by the merge — which is why the "
        "census must classify it as malformed rather than let it vanish silently")
    assert v2._unknown_delta_sections({"items": [{"content": "x"}]}) == ["items"], (
        "... and the census DOES name it")
    assert v2._unknown_delta_sections({"items": []}) == [], (
        "an EMPTY unknown key adds nothing, so it is not a shape error")
    # ⛔ THE EXEMPTION IS CONDITIONAL, NOT A HOLE (#11554 review, P2).
    # `mention_held` is exempt only in the shape the merge CARRIES (a non-empty
    # list of dicts with a non-empty `surface`); every other value is a shape
    # error the merge would drop silently, so the census must name it — the same
    # round-5 guarantee, for the channel the exemption was added for.
    _good = [{"surface": "it", "rule_id": "referentiality.pronoun"}]
    assert v2._unknown_delta_sections({"mention_held": _good}) == []
    for _bad in ("garbage", [42], [{}], [{"rule_id": "x"}], [], None):
        _got = v2._unknown_delta_sections({"mention_held": _bad})
        if _bad:
            assert _got == ["mention_held"], (
                f"mention_held={_bad!r} is NOT carried by the merge, so the "
                "census must record it as malformed — an unconditional "
                "exemption records it as 'the spans state no claim'")
        else:
            assert _got == [], f"mention_held={_bad!r} adds nothing"


def _long_narrative_model(narrative, **kw):
    """`_Model` with an S1 that narrates a DIFFERENT story (the fixture's
    narrative is fixed at module level, so the >20-flag arm needs its own)."""

    class _LongNarrative(_Model):
        def complete(self, *, system, user, max_tokens=None):
            if "STORY SUMMARIZER" in system:
                self.last_finish_reason = "stop"
                self.calls.append("s1")
                return narrative
            return super().complete(system=system, user=user,
                                    max_tokens=max_tokens)

    return _LongNarrative(**kw)


def test_a_truncated_span_list_is_visible_in_the_record(monkeypatch):
    """The record must say how many spans the model was SHOWN (review round 5,
    P2). `partial_gaps` renders at most `GAP_MAX_FLAGGED` (20) spans while
    `flagged_total`/`flagged_before` are TRUE counts, so on a long session the
    recorded closure charges the model for spans it never saw — and nothing in
    the record said so. The cap never binds on the measured F3 population
    (max 10 flags/session), which is why no existing row caught it.
    """
    narrative = "\n".join(
        f"The zorbix lease of sector {i} requires a distinct frobnicator {i}, "
        f"though the quux valve {i} stays sealed against backflow {i}."
        for i in range(30))
    model = _long_narrative_model(
        narrative,
        gap_answer=json.dumps({"entities": [], "points": [], "operators": [],
                               "events": []}))
    out = _run(monkeypatch, model)
    stats = out["stats"]["partial_gap_fill"]
    assert stats["fired"] is True
    assert stats["flagged_total"] > vet_gate.GAP_MAX_FLAGGED, (
        "the fixture must actually overflow the cap, or this row proves nothing")
    assert stats["spans_shown"] == vet_gate.GAP_MAX_FLAGGED
    assert stats["spans_truncated"] is True


def test_the_span_count_matches_the_gap_when_the_cap_does_not_bind(monkeypatch):
    """The other half: on the ordinary fixture the two counts must AGREE, so
    `spans_shown` cannot silently become a second, drifting denominator."""
    out = _run(monkeypatch, _Model())
    stats = out["stats"]["partial_gap_fill"]
    assert stats["flagged_total"] <= vet_gate.GAP_MAX_FLAGGED
    assert stats["spans_shown"] == stats["flagged_total"]
    assert stats["spans_truncated"] is False


def test_an_all_empty_s2_batch_is_skipped_without_a_model_call(monkeypatch):
    """THE ZERO-CANDIDATE POPULATION IS SKIPPED, AND THE SKIP IS RECORDED.

    This arm is why review round 3's P1 existed: the skip predicate tested the
    post-VET dict's FALSINESS, and that dict is always truthy
    (`{"entities": [], "events": [], "points": [], "operators": []}` is a
    non-empty mapping) — so the arm never fired, the stage took its FIRING path
    instead, every narrative span scored coverage 0.0 and was flagged, and one
    billed call went out. That is the exact inverse of what the evidence record
    claims, and nothing exercised it.

    The population is real and the pipeline already anticipates it — a model
    replying with empty arrays, `apply_vet` discarding every item, or `run_s2`
    returning a well-formed empty list — and it is the session `run_s4` counts
    as a full rescue (`s4_full_rescues`, #2335). #11534 scopes itself to PARTIAL
    gaps, so the correct behaviour is: no call, and `skipped` says why.
    """
    empty = {"entities": [], "events": [], "operators": [], "points": []}
    model = _Model(s2_answer=empty)
    out = _run(monkeypatch, model)
    assert "gap_fill" not in model.calls, (
        "a zero-candidate batch is S4's rescue, not a partial gap — the stage "
        "must not spend a call on it")
    stats = out["stats"]["partial_gap_fill"]
    assert stats["armed"] is True
    assert stats["skipped"] == "no candidate the detector counts"
    assert stats["fired"] is False
    assert "llm" not in stats


def test_the_skip_also_fires_when_the_mention_channel_wrote_bookkeeping(
        monkeypatch):
    """THE SKIP PREDICATE TESTS THE CANDIDATE SECTIONS, NOT THE MAPPING.

    Review round 7's P2. The predicate was `any(embed_list.values())`, which is
    true on a ZERO-candidate batch as soon as any pass has written bookkeeping
    onto the same mapping — and `route_mentions` writes `mention_stats` (an 8-key
    dict) plus `mentions` there whenever `TORTOISE_REFERENTIALITY` is armed. So
    on the exact armed-for-measurement configuration the evidence record
    prescribes, the skip never fired: every narrative span scored coverage 0.0,
    one billed call went out, and `skipped` was never recorded — the stage's
    stated contract inverted, silently, on the population it is scoped to
    exclude.

    The channel-only shape is not contrived: `route_mentions` documents that a
    model may emit `mentions[]` and no candidate section, and its own docstring
    records the SAME hazard defeating `s4_full_rescues` on the unarmed path.
    """
    monkeypatch.setenv("TORTOISE_REFERENTIALITY", "1")
    empty_with_channel = {
        "entities": [], "events": [], "operators": [], "points": [],
        "mentions": [{"surface": "it",
                      "rule_id": "referentiality.pronoun"}],
    }
    model = _Model(s2_answer=empty_with_channel)
    out = _run(monkeypatch, model)
    assert "gap_fill" not in model.calls, (
        "a bookkeeping key is not a candidate — the zero-candidate batch is "
        "still S4's rescue and must not cost a model call")
    stats = out["stats"]["partial_gap_fill"]
    assert stats["armed"] is True
    assert stats["skipped"] == "no candidate the detector counts"
    assert stats["fired"] is False
    assert "llm" not in stats


def test_no_model_call_when_nothing_is_flagged():
    """Boundedness: a fully-covered session pays nothing."""
    class _NoCall:
        last_finish_reason = None

        def complete(self, **kwargs):       # pragma: no cover - must not run
            raise AssertionError("no model call may be made when no gap exists")

    covered = {
        "points": [{"content": "The wire stage stalled because the retry loop "
                               "bypassed the batch ownership check.",
                    "pointKind": "statement"},
                   {"content": "The lease is keyed by batch id with a "
                               "thirty-second expiry and a compare-and-swap "
                               "update on completion.", "pointKind": "statement"},
                   {"content": "The hydrate stage shares the same code as the "
                               "wire stage but was audited and found clean.",
                    "pointKind": "statement"}],
    }
    assert v2.run_partial_gap_fill(_NoCall(), NARRATIVE, covered) == {}


def test_a_failing_gap_fill_keeps_the_s2_output(monkeypatch):
    """Fail-open: the stage cannot cost the session its candidates."""
    model = _Model(gap_raises=True)
    out = _run(monkeypatch, model)
    contents = [str(p.get("content") or "")
                for p in out["embed_list"]["points"]]
    assert any("retry loop" in c for c in contents), (
        "S2's own candidates survive a gap-fill failure")
    assert not any("shares the same code" in c for c in contents)
    assert any("gap-fill failed" in e for e in out["errors"])
    stats = out["stats"]["partial_gap_fill"]
    assert stats["error"]
    # ⛔ THE FAILURE ARM'S SPEND IS STILL ACCOUNTED (review round 2, P1). The
    # roll-up sits OUTSIDE the try, as S4's does, and `fired` is set BEFORE the
    # call — a call that raises was still billed, so a record saying
    # `fired: False` would be a false statement about the run.
    assert stats["fired"] is True, (
        "a gap-fill call that raised was still billed — `fired: False` is false")
    assert stats["llm"]["attempts"] >= 1
    assert out["stats"]["llm"]["calls"] >= 1


def test_an_empty_answer_is_a_valid_answer(monkeypatch):
    """"The spans state no claim" is the CORRECT answer for a label/date/aside
    span. How much of the population is that class is NOT measured (evidence
    record §2/§4). It must be recorded as fired-with-zero-additions, never as
    an error and never as a retry."""
    model = _Model(gap_answer=json.dumps(
        {"entities": [], "events": [], "operators": [], "points": []}))
    out = _run(monkeypatch, model)
    stats = out["stats"]["partial_gap_fill"]
    assert stats["fired"] is True
    assert stats["added_items"] == 0
    assert not any("gap-fill" in e for e in out["errors"])


# ── the measurement: the detector scored on the F3 population ─────────────

#: The committed recordings. Both are read: the population's own record says
#: the two runs differ (wp01/wp07 add in one and not the other), so scoring
#: against one of them alone understates the label set.
_RECEIPTS = os.path.join("tests", "eval", "write_path", "receipts",
                         "7947-arms-2026-10-18")
_ARTIFACTS = (
    os.path.join("docs", "evidence", "1789-s4-reeemit-tax",
                 "ab-20261107T023958Z.json"),
    os.path.join("docs", "evidence", "1789-s4-reeemit-tax",
                 "ab-20261107T024226Z.json"),
)


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _measure() -> dict:
    """Re-derive the detector's score over the recorded population.

    A flagged span is credited with one of S4's additions when at least 30% of
    the ADDITION's content tokens appear in it — deliberately generous to the
    detector (an addition is a paraphrase of the span, not a copy of it), so a
    miss is a real miss.

    ⚠️ The labels are S4's own output, so they are a LOWER bound on the gaps in
    each session and an UPPER bound on what a coverage stage should emit: S4
    did not find everything either. Recall against them is therefore the honest
    question ("does the detector reach what the stage it replaces found?") and
    precision is not derivable from them at all — which is why the flag rate is
    reported instead, and why the evidence record lists every flagged span.
    """
    root = _repo_root()
    sessions = {}
    for path in sorted(glob.glob(os.path.join(root, _RECEIPTS, "*.arms.json"))):
        name = os.path.basename(path).split(".")[0]
        with open(path, encoding="utf-8") as handle:
            sessions[name] = json.load(handle)

    segments = 0
    flagged = 0
    hits = 0
    labels = 0
    by_section: dict[str, list[int]] = {}
    # ⛔ KEYED BY (artifact, session), NOT BY SESSION (review round 3, P2).
    # Both artifacts carry the same seven session keys, so a plain
    # `per_session[arm["session"]] = …` made the second run OVERWRITE the
    # first's row — and the pin below then asserted "both runs agreeing" from a
    # dict holding one run's numbers. A detector that became run-dependent in
    # the FIRST artifact would have left that row green, which is exactly the
    # regression the comment claims to guard.
    per_session: dict[tuple[str, str], int] = {}
    for artifact in _ARTIFACTS:
        with open(os.path.join(root, artifact), encoding="utf-8") as handle:
            evidence = json.load(handle)
        for arm in evidence["arms"]:
            session = sessions[arm["session"]]
            narrative = session["stories"]["narrative"]
            gaps = vet_gate.partial_gaps(session["embed_list"]["narrative"],
                                         narrative)
            segments += gaps["segments"]
            flagged += gaps["flagged_total"]
            per_session[(os.path.basename(artifact), arm["session"])] = (
                gaps["flagged_total"])
            span_tokens = [vet_gate._gap_tokens(entry["text"])
                           for entry in gaps["flagged"]]
            for item in arm["legacy"]["added_items"]["items"]:
                addition = vet_gate._gap_tokens(
                    item.get("content") or item.get("key") or "")
                if not addition:
                    continue
                labels += 1
                best = max((len(addition & toks) / len(addition)
                            for toks in span_tokens), default=0.0)
                reached = 1 if best >= 0.3 else 0
                hits += reached
                by_section.setdefault(item["section"], []).append(reached)
    return {"segments": segments, "flagged": flagged, "hits": hits,
            "labels": labels, "by_section": by_section,
            "per_session": per_session}


def test_the_recorded_population_is_still_the_one_we_measured():
    """Guard the inputs before guarding the result: if a receipt or artifact
    is retargeted or rewritten, the pins below would be measuring something
    else and say so."""
    root = _repo_root()
    assert len(glob.glob(os.path.join(root, _RECEIPTS, "*.arms.json"))) == 7
    for artifact in _ARTIFACTS:
        with open(os.path.join(root, artifact), encoding="utf-8") as handle:
            evidence = json.load(handle)
        assert len(evidence["arms"]) == 7
        assert evidence["arms"][0]["legacy"]["added_items"]["cap_hit"] is False


def test_the_detector_s_flag_rate_on_the_population():
    """The instrument's cost, measured: 8.3% of the 844 segments scored across
    the 7 sessions × 2 recorded runs are flagged. A detector that flagged most
    segments would make a downstream ADD stage pay a model call on every
    session; one that flagged none would be inert."""
    measured = _measure()
    assert measured["segments"] == 844
    assert measured["flagged"] == 70
    rate = measured["flagged"] / measured["segments"]
    assert 0.05 <= rate <= 0.15, f"flag rate {rate:.1%} moved out of range"
    # Per-session counts, pinned PER RUN and then shown to agree. The
    # detector is deterministic and both artifacts carry the same S2 list, so
    # agreement is the expected outcome — which is why it is asserted rather
    # than assumed (review round 3, P2).
    expected = {
        "wp01_quarry_debug": 1, "wp02_lumen_refactor": 10,
        "wp03_ember_design": 8, "wp04_aurora_perf": 2,
        "wp05_retro_writeup": 5, "wp06_quarry_rollout": 1,
        "wp07_bluepeak_followup": 8,
    }
    runs: dict[str, dict[str, int]] = {}
    for (artifact, session), count in measured["per_session"].items():
        runs.setdefault(artifact, {})[session] = count
    assert len(runs) == 2, f"both recorded runs must be scored; got {sorted(runs)}"
    for artifact, rows in runs.items():
        assert rows == expected, f"{artifact} disagrees with the pinned counts"
    assert len({tuple(sorted(rows.items())) for rows in runs.values()}) == 1, (
        "the two recorded runs must agree per session — a divergence here is a "
        "run-dependent detector, the regression this pin exists to catch")


def test_the_detector_s_recall_against_what_s4_actually_added():
    """The substantive measurement, and the reason `run_s4`'s removal stays
    gated: the detector reaches 9 of 21 additions — 8 of 12 POINTS but only 1
    of 9 OPERATORS.

    The operator half is the finding. An operator addition generalises a claim
    the batch already carries ("a retry path that does not re-establish
    ownership … defeats any downstream completion check"), so NO narrative span
    is uncovered even though the item is new: a coverage detector cannot reach
    it, and an ADD stage triggered by coverage is therefore not a like-for-like
    replacement for the stage it would replace.
    """
    measured = _measure()
    assert measured["labels"] == 21
    assert measured["hits"] == 9
    assert sum(measured["by_section"]["points"]) == 8
    assert len(measured["by_section"]["points"]) == 12
    assert sum(measured["by_section"]["operators"]) == 1
    assert len(measured["by_section"]["operators"]) == 9


def test_the_coverage_floor_is_justified_by_the_measured_distribution():
    """The constant's own rationale, re-derived so it cannot rot.

    ``GAP_COVERAGE_FLOOR``'s comment justifies 0.55 by the population's
    per-segment coverage distribution, and that comment carried PRE-correction
    percentiles ("median 0.85, p10 0.40") through two review rounds because no
    pin re-derived them. This is the pin.

    The read is done through the SHIPPED function, not a re-implementation of
    its formula: ``coverage_floor=2.0`` is above every attainable coverage, so
    every segment that clears ``GAP_MIN_CONTENT_TOKENS`` comes back with the
    coverage the detector computed for it.
    """
    root = _repo_root()
    values: list[float] = []
    for path in sorted(glob.glob(os.path.join(root, _RECEIPTS, "*.arms.json"))):
        with open(path, encoding="utf-8") as handle:
            session = json.load(handle)
        gaps = vet_gate.partial_gaps(
            session["embed_list"]["narrative"], session["stories"]["narrative"],
            coverage_floor=2.0, max_flagged=10_000)
        values += [entry["coverage"] for entry in gaps["flagged"]]
    values.sort()
    assert len(values) == 210, "segments clearing the content-token floor"
    # a full-coverage MASS plus a low TAIL, and the floor sits in the trough
    assert sum(1 for v in values if v >= 1.0) == 147
    assert sum(1 for v in values if v < vet_gate.GAP_COVERAGE_FLOOR) == 35
    assert sum(1 for v in values if 0.5 <= v < 0.6) == 4
    # ⛔ AND THE PLACEMENT CLAIM IS PINNED, NOT MERELY QUOTED (round 4, P2).
    # The code comment and the evidence record both used to call [0.5, 0.6) "the
    # THINNEST band between the two"; that was FALSE — [0.2, 0.3) holds 3 and
    # [0.8, 0.9) holds 4 — and the pin only counted the band, so the sentence
    # stayed green while it was wrong. What IS true and is asserted here: the
    # band is the LOCAL MINIMUM of the three straddling the floor, and one of its
    # four values sits ABOVE the floor, so 0.55 cuts three of the four.
    bands = [sum(1 for v in values if lo <= v < lo + 0.1)
             for lo in (0.4, 0.5, 0.6)]
    assert bands == [6, 4, 14], bands
    assert bands[1] == min(bands), (
        "the floor's band must be the local minimum of the three straddling it — "
        "this is the whole placement argument")
    straddling = [v for v in values if 0.5 <= v < 0.6]
    assert sum(1 for v in straddling if v >= vet_gate.GAP_COVERAGE_FLOOR) == 1, (
        "the band is a shallow minimum, not a clean separation: 0.586 is above "
        "the floor, so the record must not call the floor a gap between modes")
    # ... and the 35 below the floor IS the pinned flag count for one run
    # (70 over both runs), so the distribution and the flag rate cannot drift
    # apart: one number, two independent readings.
    assert sum(1 for v in values if v < vet_gate.GAP_COVERAGE_FLOOR) * 2 == 70
    assert values[len(values) // 2] == 1.0
    assert values[int(0.10 * len(values))] == 0.355
    assert values[int(0.25 * len(values))] == 0.736


def test_the_content_floor_s_removal_class_is_measured_not_asserted():
    """`GAP_MIN_CONTENT_TOKENS`'s rationale, re-derived (review round 12, P3).

    The constant's comment used to call "markdown headings and bold labels" the
    population's **dominant** flag class. Neither half survives its own
    population, and the first attempt to fix it installed two numerals ("0 of
    416", "6 of 432") that no invocation of the shipped function reproduces —
    the exact rot the paragraph it sits in warns about.

    So both halves are pinned against the SHIPPED defaults here, and the record
    and the comment quote these numbers instead of restating a count:

    * **headings can never be a flag class** while :func:`vet_gate._gap_mask`
      runs — it blanks them before segmentation, so even at
      ``min_content_tokens=1`` (103 flags over the seven recordings) not one
      flag begins with ``#``;
    * **bold labels are a plurality, not a dominant class**: 68 of the 212
      sub-floor segments carry ``**`` (32.1%).
    """
    root = _repo_root()
    flagged_at_one = 0
    heading_flags = 0
    segments = 0
    sub_floor = 0
    bold = 0
    for path in sorted(glob.glob(os.path.join(root, _RECEIPTS, "*.arms.json"))):
        with open(path, encoding="utf-8") as handle:
            session = json.load(handle)
        gaps = vet_gate.partial_gaps(
            session["embed_list"]["narrative"], session["stories"]["narrative"],
            min_content_tokens=1, max_flagged=10_000)
        flagged_at_one += gaps["flagged_total"]
        heading_flags += sum(1 for entry in gaps["flagged"]
                             if entry["text"].lstrip().startswith("#"))
        for segment in vet_gate.narrative_segments(
                session["stories"]["narrative"]):
            segments += 1
            if len(vet_gate._gap_tokens(segment["text"])) < (
                    vet_gate.GAP_MIN_CONTENT_TOKENS):
                sub_floor += 1
                if "**" in segment["text"]:
                    bold += 1
    # ⛔ ASSERTION ORDER IS PART OF THE GUARD (review round 7, mutation leg).
    # Each row below is the FIRST to fail under its OWN mutant: the mask, then
    # the segmentation, then the removal class, then the flag count. Asserting
    # the flag count first made three later rows unreachable — `narrative_segments
    # -> []` and the mask both reddened the count instead of the behaviour they
    # revert, which is a proof of the wrong thing.
    # `bold * 2 < sub_floor` was dropped because it can never be FIRST to fail,
    # per mutant: the mask, removal-class and bold mutants are caught above it;
    # the empty-segment mutant moves it (`0 * 2 < 0`) but `segments == 422`
    # catches that first; and the flag-count mutant leaves it at `136 < 212`
    # (True) and is caught one row below. So it would read as coverage while
    # guarding nothing.
    assert heading_flags == 0, (
        "a heading reached the flag list — the mask is applied after segmentation")
    assert segments == 422
    assert sub_floor == 212
    assert bold == 68, "68 of 212 = 32.1%: a plurality, not a dominant class"
    assert flagged_at_one == 103, "flags at min_content_tokens=1, shipped floor"


def test_the_heading_mask_does_not_eat_issue_numbered_lines(monkeypatch):
    """The mask is NARROW, not merely present (review round 9, P2).

    Round 8 narrowed :func:`vet_gate._gap_mask` from ``^[ \\t]*#+[^\\n]*$`` to a
    form that requires a space (or end-of-line) after the hashes — correctly: the
    old pattern ate **any** line starting with ``#``, including a claim that opens
    with an issue number, and that is the fleet's own prose shape.

    ⛔ WHY THIS ROW EXISTS. The test above pins that the mask EXISTS; measured,
    reverting the regex to the round-7 form left every row GREEN (``24 passed``),
    so the narrowing was guarded by nothing. The failure it protects is the
    detector's MISS direction: a blanked claim is never a segment, so a gap in it
    can never be flagged — invisible by construction, in the population we
    actually process.
    """
    claim = ("#11534 note: the canary rollback window is bounded by the operator "
             "dashboard cache ttl so a stale panel outlives the revert")
    narrative = claim + "\n\n" + NARRATIVE

    narrowed = vet_gate.partial_gaps(COVERED_ITEMS, narrative)
    assert any("canary rollback window" in text.lower()
               for text in _flagged_texts(narrowed)), (
        "an issue-numbered line is NOT a markdown heading — the mask must leave it "
        "intact so its uncovered claim can still be flagged")

    # NON-VACUITY: under the round-7 mask the line is blanked, so the claim is
    # never a segment and the assertion above could not have observed it.
    import re

    wide = re.compile(r"^[ \t]*#+[^\n]*$", re.MULTILINE)
    monkeypatch.setattr(
        vet_gate, "_gap_mask",
        lambda text: wide.sub(lambda m: " " * len(m.group(0)), str(text or "")))
    starved = vet_gate.partial_gaps(COVERED_ITEMS, narrative)
    assert not any("canary rollback window" in text.lower()
                   for text in _flagged_texts(starved)), (
        "with the round-7 mask the issue-numbered claim is swallowed, which is the "
        "regression this row exists to catch — if this arm fails, the arm above "
        "proves nothing")


# ── review round 9: the two gaps a fresh reviewer found ────────────────────


def test_the_dedup_block_renders_the_operators_the_batch_carries():
    """`operators` must reach EXISTING ITEMS, or the prompt invites its destruction.

    ``_vet_gate.SECTIONS`` carries only entities/events/points (an operator has no
    ``name``/``content``, so ``_item_text`` answers ""), so a loop over that table
    alone left the section **entirely absent from the prompt** — measured before
    the fix: a batch carrying one operator rendered an EMPTY dedup block. That is
    the opposite of what the block exists for, against a prompt that forbids
    re-emission ("Do NOT re-emit, restate, split or 'improve' anything in
    EXISTING ITEMS") and permits an operator whose endpoints sit "in EXISTING
    ITEMS" — and the collision it invites is destructive: ``_merge_key``'s
    operator identity carries NO ``claim``, so a re-emitted operator matches the
    S2 one and ``merge_embed_lists`` lets the gap-fill version WIN.
    """
    op = {"src": "wire stage", "dst": "hydrate stage", "op_type": "IMPL",
          "claim": "the wire stage must not hand off before the lease lands"}
    rendered = v2._render_existing_items(
        {"entities": [{"name": "wire stage", "kind": "core:workflow"}],
         "events": [], "points": [], "operators": [op]})
    assert "[operators]" in rendered, (
        "the section the prompt names must not be invisible — before this fix the "
        "block rendered nothing at all for an operator-only batch")
    assert "wire stage -[IMPL]-> hydrate stage" in rendered


def test_an_operator_only_batch_is_skipped_with_no_model_call(monkeypatch):
    """The skip predicate must be the DETECTOR's, not a broader structural one.

    ``_batch_has_candidates`` walks four sections (``operators`` included) while
    ``check_batch``/``partial_gaps`` count through three, so on an operator-only
    batch the two disagreed in the fatal direction — measured: ``candidates: 0``,
    ``coverage_suspect: True``, ``flagged_total: 1``, yet the helper said True and
    the arm took the FIRING path over a fully-flagged narrative, spending a billed
    call on exactly the population it documents as excluded (S4's rescue).
    """
    operator_only = {"entities": [], "events": [], "points": [],
                     "operators": [{"src": "wire stage", "dst": "hydrate stage",
                                    "op_type": "IMPL", "claim": "x"}]}
    batch = v2._vet_gate.check_batch(operator_only, NARRATIVE)
    assert batch["candidates"] == 0 and batch["coverage_suspect"] is True, (
        "the fixture must be the zero-candidate shape the detector reports")
    assert batch["partial_gaps"]["flagged_total"] > 0, (
        "…and a fully-flagged narrative, which is why firing here is inverted")
    assert v2._batch_has_candidates(operator_only) is True, (
        "the old predicate is True on this shape — that divergence IS the defect")

    model = _Model(s2_answer=operator_only)
    out = _run(monkeypatch, model)
    assert "gap_fill" not in model.calls
    stats = out["stats"]["partial_gap_fill"]
    assert stats["skipped"] == "no candidate the detector counts"
    assert stats["fired"] is False
    assert "llm" not in stats


def test_a_candidate_that_tokenises_to_nothing_does_not_arm_the_call(monkeypatch):
    """The population the union is built from, not the item count (round 10, P2).

    ``check_batch``'s ``candidates`` counts an item whenever ``_item_text`` is
    non-empty, but ``partial_gaps`` builds its idf union from the **tokens**. An
    item whose text carries no content token is therefore a candidate that covers
    nothing, and asking the first number armed a billed call over a narrative the
    detector had flagged in full — the S4-rescue population this arm excludes.
    Measured on the code this replaces: ``candidates 1``, ``flagged_total 2``,
    ``candidates_with_tokens 0``.
    """
    tokenless = {"entities": [{"name": "Done.", "kind": "core:event"}],
                 "events": [], "points": [], "operators": []}
    batch = v2._vet_gate.check_batch(tokenless, NARRATIVE)
    assert batch["candidates"] == 1, (
        "the item exists, so the item count is 1 — that is why it was trusted")
    assert batch["candidates_with_tokens"] == 0, (
        "…and it contributes nothing to the union, so every span is flagged")
    assert batch["partial_gaps"]["flagged_total"] > 0

    model = _Model(s2_answer=tokenless)
    out = _run(monkeypatch, model)
    assert "gap_fill" not in model.calls
    stats = out["stats"]["partial_gap_fill"]
    assert stats["skipped"] == "no candidate text tokens", (
        "the record must name the shape it skipped — 'empty embed_list' was the "
        "false label a review caught on a batch that is not empty")
    assert stats["fired"] is False
    assert "llm" not in stats


def test_a_raising_detector_is_recorded_not_swallowed(monkeypatch):
    """Fail-open AND loud (round 10, P3).

    The stage's own contract, two lines above the call site, is that a detector
    that raises records the reason in ``errors``/``vet_warnings``. ``except:
    return False`` turned a crashed detector into the same silent skip as an empty
    batch, so no record could tell them apart.
    """
    def _boom(*_a, **_k):
        raise RuntimeError("detector exploded")

    monkeypatch.setattr(v2._vet_gate, "check_batch", _boom)
    model = _Model()
    out = _run(monkeypatch, model)
    assert "gap_fill" not in model.calls, "an unknown gap is not a licence to bill"
    stats = out["stats"]["partial_gap_fill"]
    assert stats["skipped"] == "detector raised: RuntimeError"
    assert any("detector failed" in e for e in out["errors"]), (
        "the contract is FAIL-OPEN **AND LOUD** — the reason must reach errors, "
        f"not only the stats dict; got {out['errors']!r}")
