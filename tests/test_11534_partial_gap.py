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

import pytest

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
    # ... and it names a STATEMENT, not just a count
    assert isinstance(entry["statement"], int)
    assert isinstance(entry["clause"], int)
    assert entry["coverage"] < gaps["coverage_floor"]
    assert "code" in entry["missing"] or "clean" in entry["missing"]


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
    """The false-positive direction: coverage must not be reported as a gap."""
    gaps = vet_gate.partial_gaps(COVERED_ITEMS, NARRATIVE)
    assert gaps["flagged_total"] == 1        # only the hydrate statement
    assert not any("lease is keyed" in text for text in _flagged_texts(gaps))


def test_the_gapped_fixture_is_the_same_fixture_minus_one_item():
    """Non-vacuity, stated as a property: the SAME narrative with the covering
    item present is clean, and with it removed is flagged. Without this pair the
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
    assert vet_gate.partial_gaps({"points": "a string"}, NARRATIVE)["segments"] \
        == gaps["segments"]
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


def test_markdown_headings_are_not_statements():
    """The population's dominant false-positive class: `## Narrative: …` is a
    title, and a detector that treats it as prose flags every session."""
    headed = ("## Narrative: the wire stage\n\n" + NARRATIVE)
    assert not any("Narrative:" in text
                   for text in _flagged_texts(vet_gate.partial_gaps(
                       COVERED_ITEMS, headed)))


# ── the ADD-capable stage: a flagged candidate reaches the embed list ─────

_MISSING_POINT = ("The hydrate stage shares the same code as the wire stage "
                  "but was audited and found clean.")


class _Model:
    """A stub model: S1 narrates, S2 covers two of three statements, the
    coverage reviewer emits the third. Prompt-keyed, in the shape the other
    extractor tests use."""

    last_finish_reason = None

    def __init__(self, *, gap_answer: str | None = None, gap_raises: bool = False):
        self.calls: list[str] = []
        self.gap_answer = gap_answer
        self.gap_raises = gap_raises

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
        return json.dumps(COVERED_ITEMS)


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
    assert out["stats"]["partial_gap_fill"]["error"]


def test_an_empty_answer_is_a_valid_answer(monkeypatch):
    """"The spans state no claim" is the CORRECT answer for a label/date/aside
    span, and the dominant one on the recorded population. It must be recorded
    as fired-with-zero-additions, never as an error and never as a retry."""
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
    per_session: dict[str, int] = {}
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
            per_session[arm["session"]] = gaps["flagged_total"]
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
    # per-session counts, both runs agreeing (the detector is deterministic and
    # the S2 list is the same object in both artifacts)
    assert measured["per_session"] == {
        "wp01_quarry_debug": 1, "wp02_lumen_refactor": 10,
        "wp03_ember_design": 8, "wp04_aurora_perf": 2,
        "wp05_retro_writeup": 5, "wp06_quarry_rollout": 1,
        "wp07_bluepeak_followup": 8,
    }


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
