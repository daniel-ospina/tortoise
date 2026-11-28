"""#14691 T1-T5 — the noise bundle's claims pinned to its own receipts.

WHY THIS FILE EXISTS. `docs/evidence/14691-noise-validation/REPORT.md` reports five
verdicts and a set of before/after numbers. Prose has no compiler: nothing in the
tree would redden if a verdict drifted from the receipts it claims to come from,
if the corpus fixture stopped describing the business the scorer measures it
against, or if the freshness guard stopped refusing a populated graph while its
predicate stayed measurable. That is #14606's review lesson, restated by T6's
bundle, and it applies here for the same reason.

Each test names the mutation that reds it. Every one settles from committed
artifacts only: no DB, no docker, no LLM, no network. The bundle root is resolved
from this file, so a moved bundle reds loudly instead of pinning nothing.

⛔ Several tests pin a FAILING measurement (T1 and T2 fail on purpose — a failed
test is this issue's most valuable output). When the extractor is fixed, they red:
that is the signal to re-run the bundle and change the numbers in `REPORT.md`
together, which is exactly the drift this file exists to prevent.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
BUNDLE = REPO / "docs" / "evidence" / "14691-noise-validation"
CORPUS_PREDECESSOR = (REPO / "docs" / "evidence" / "14592-operating-model-validation"
                      / "corpus" / "corpus.json")


def _corpus() -> dict:
    return json.loads((BUNDLE / "corpus" / "noise-corpus.json").read_text())


def _score() -> dict:
    return json.loads((BUNDLE / "out" / "t1-t5-score.json").read_text())


def _receipt() -> dict:
    return json.loads((BUNDLE / "out" / "ingest-receipt.json").read_text())


def _readback() -> dict:
    return json.loads((BUNDLE / "out" / "readback.json").read_text())


def _report() -> str:
    return (BUNDLE / "REPORT.md").read_text()


def _module(name: str, filename: str):
    """Import a bundle script by path (they are not package modules)."""
    path = BUNDLE / filename
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _ingest_module():
    return _module("noise_ingest", "run_ingest.py")


def _score_module():
    return _module("noise_score", "score_t1_t5.py")


# ── the corpus ──────────────────────────────────────────────────────────────
def test_the_corpus_is_the_six_documents_the_report_measures():
    corpus = _corpus()
    ids = [d["doc_id"] for d in corpus["documents"]]
    assert ids == ["doc-01", "doc-02", "doc-03", "doc-04", "doc-05", "doc-06"]
    chars = sum(len(t["content"]) for d in corpus["documents"] for t in d["turns"])
    assert chars == 5280, "REPORT.md quotes 5,280 chars — a corpus edit must move that number"


def test_documents_one_to_five_are_byte_identical_to_the_predecessor_corpus():
    """The lineage the fixture's `_about` claims, checked rather than asserted.

    Byte identity is what makes #14606's numbers comparable with these: a
    transcription drift here would silently change the business the two bundles
    describe while both still call themselves the same corpus.
    """
    before = {d["doc_id"]: d for d in json.loads(CORPUS_PREDECESSOR.read_text())["documents"]}
    after = {d["doc_id"]: d for d in _corpus()["documents"]}
    for doc_id in ("doc-01", "doc-02", "doc-03", "doc-04", "doc-05"):
        assert after[doc_id] == before[doc_id], f"{doc_id} drifted from #14592's corpus"


def test_doc_six_is_the_t2_restatement_and_introduces_no_new_element():
    corpus = _corpus()
    fact = corpus["_declared_facts"]["goal"]
    assert fact["stated_in"] == ["doc-03", "doc-06"]
    doc = {d["doc_id"]: d for d in corpus["documents"]}["doc-06"]
    text = " ".join(t["content"] for t in doc["turns"]).lower()
    # The restatement must carry the goal's numbers and none of the corpus's
    # other named things (a new element would make T2's "+0 entities" unmeasurable).
    for token in ("forty", "eighteen", "twenty-two", "december"):
        assert token in text, f"doc-06 must restate the goal — missing {token!r}"
    for other in ("coldpath", "nordicfreight", "dev patel", "marta"):
        assert other not in text, f"doc-06 must not introduce {other!r} (that would be a new element)"


# ── the premises (driven, not assumed) ──────────────────────────────────────
def test_the_run_started_from_a_graph_the_overview_measured_as_empty():
    receipt = _receipt()
    before = receipt["empty_before"]
    assert before["taxonomy"] == {"Point": 0, "Event": 0, "Subject": 0, "Object": 0, "Source": 0}
    assert before["structure"]["total"] == 0
    assert before["structure"]["operators"] == 0
    _ingest_module().refuse_if_not_fresh(before)          # must not raise


@pytest.mark.parametrize("before,reason", [
    (None, "an absent overview is not a fresh graph"),
    ({}, "an empty reply carries no measurement"),
    ({"taxonomy": {}, "structure": {"total": 0}, "status": None},
     "a carried-but-empty section is not a measurement"),
    ({"taxonomy": {"Point": 1}, "structure": {"total": 0},
      "status": {"connected": True, "total_entities": 0}}, "a populated taxonomy"),
    ({"taxonomy": {"Point": 0, "Event": 0, "Subject": 0, "Object": 0, "Source": 0},
      "structure": {"total": 0}, "status": {"connected": True, "total_entities": 500}},
     "content hidden in status.total_entities"),
    ({"taxonomy": {"Point": 0}, "structure": {"total": 12},
      "status": {"connected": True, "total_entities": 0}}, "content in structure.total"),
])
def test_the_freshness_guard_refuses_every_shape_that_is_not_an_empty_graph(before, reason):
    """The REFUSAL is the gate, so the refusal is what is driven here.

    A guard whose only testable part is its predicate can have its `raise`
    deleted with the suite still green (#14691 review, T6 5th cycle). Each case
    above is a shape a real `graph_overview()` reply can take.
    """
    with pytest.raises(SystemExit):
        _ingest_module().refuse_if_not_fresh(before)


def test_the_occupancy_walk_ignores_connection_vocabulary_and_diagnostics():
    module = _ingest_module()
    benign = {"namespaces": [{"namespace": "dev", "kind_count": 32}],
              "status": {"connected": True},
              "stale": {"limit": 50, "count": 0},
              "audit": {"exit_code": 0, "node_count": 0},
              "taxonomy": {}}
    assert module.occupancy(benign) == {}
    hostile = {"audit": {"node_count": 900}, "status": {"total_entities": 5}}
    assert module.occupancy(hostile), "content buried in an audit/status field must be seen"


# ── the five verdicts ───────────────────────────────────────────────────────
_VERDICT_CELL_RE = (r"^\|\s*\*\*(?P<name>T[1-5])\*\*\s*\|[^|\n]*\|"
                    r"\s*\*\*(?P<v>[A-Z][A-Z-]*)\*\*\s*\|")


def _report_verdicts() -> dict[str, str]:
    """Each row's verdict, read from THAT row's own cell.

    The earlier spelling asserted `f"**{row['verdict']}**" in report`, which is not
    scoped to the row: the report carries BOTH `**FAIL**` and `**PASS**`, so any
    single row's verdict could be flipped with the suite still green — the exact
    drift this file exists to catch, and the one mutation the review measured as
    surviving. Anchoring on `| **T3** |` and capturing the third cell is what ties
    the verdict to its own row.
    """
    found = {m.group("name"): m.group("v")
             for m in re.finditer(_VERDICT_CELL_RE, _report(), re.MULTILINE)}
    assert set(found) == {"T1", "T2", "T3", "T4", "T5"}, (
        f"REPORT.md's verdict table no longer parses per row: {sorted(found)}")
    return found


def test_the_five_verdicts_are_the_reports_verdicts():
    tests = _score()["tests"]
    committed = {name: row["verdict"] for name, row in tests.items()}
    assert committed == {
        "T1": "FAIL", "T2": "FAIL", "T3": "FAIL", "T4": "PASS", "T5": "PASS",
    }
    # ROW-SCOPED: a flip in one row cannot be satisfied by another row's word.
    assert _report_verdicts() == committed, (
        "REPORT.md's verdict row disagrees with the score artifact; the cell is read "
        "from the row it belongs to, so one row's `**PASS**` cannot stand in for "
        "another's")


def test_the_scorer_RECOMPUTES_the_committed_verdicts():
    """Drive the scorer; do not only trust the bytes it once wrote.

    Before #14812's review every verdict test compared the committed
    `out/t1-t5-score.json` to literals and `_score_module()` was defined and never
    called, so changing a `RULE_*` or dropping a conjunct from a verdict changed no
    committed number and reddened nothing. That is precisely why two unevaluated
    conjuncts (T3's second leg, T4's decision leg) shipped invisibly. This runs the
    scorer's own functions over the committed artifacts and demands the verdicts it
    computes are the verdicts the artifact and REPORT.md carry.
    """
    module = _score_module()
    receipt, corpus = _receipt(), _corpus()
    computed = {
        "T1": module.t1(receipt)["verdict"],
        "T2": module.t2(receipt, corpus)["verdict"],
        "T3": module.t3(receipt, corpus)["verdict"],
        "T4": module.t4(receipt, corpus)["verdict"],
        "T5": module.t5(receipt, corpus)["verdict"],
    }
    assert computed == {name: row["verdict"] for name, row in _score()["tests"].items()}, (
        "the scorer no longer computes the verdicts its own artifact states — a rule "
        "changed under figures that were not re-derived")
    assert computed == _report_verdicts()


def test_every_conjunct_of_a_rule_changes_its_verdict(monkeypatch):
    """Each rule's conjunct must be READ, not merely computed and printed.

    Both findings were found from a report field that existed and was ignored: T3's
    `orphan_traceability` (its second conjunct) and T4's `event_point_kind_rows` (its
    decision clause). A field nobody reads is a rule that cannot fail, so each leg is
    driven here in the direction that must FAIL — the shape the pre-fix code passed.
    """
    module = _score_module()

    # ── T3: the share leg passes (1 of 2 orphans is 0.5, not >0.5) and only the
    #        second conjunct can fail: `a thing the corpus never says` is untraceable.
    readback_t3 = {
        "entities": [{"id": "e1", "name": "Priya Nair", "kind": "core:naturalPerson",
                      "layer": "subject"},
                     {"id": "e2", "name": "a thing the corpus never says",
                      "kind": "core:strategy", "layer": "object"}],
        "neighbours": {},
    }
    receipt_t3 = {"passes": [{"pass": 1, "documents": [{"doc_id": "doc-01", "result": {
        "relation_write": {"edges": [{"src_id": "e1", "dst_id": "e1"}]}}}]}]}
    monkeypatch.setattr(module, "load", lambda name: readback_t3)
    row = module.t3(receipt_t3, _corpus())
    assert row["orphan_legs"]["majority_are_not_orphans"] is True, \
        "the fixture must keep the SHARE leg passing, so only the second conjunct decides"
    assert row["orphan_legs"]["every_orphan_explained_in_corpus"] is False
    assert row["verdict"] == "FAIL", (
        "T3 PASSes an orphan whose name the corpus never states — its second conjunct "
        "is being computed and dropped, which is the defect the review measured")
    # …and the same fixture with a TRACEABLE orphan must PASS, so the arm cannot be
    #   satisfied by making T3 fail unconditionally.
    readback_t3["entities"][1]["name"] = "Priya Nair"
    row = module.t3(receipt_t3, _corpus())
    assert row["verdict"] == "PASS", (
        "T3 fails a traceable orphan — the second conjunct is not reading the corpus")

    # ── T4: every OTHER leg holds (all four layers, 6 Sources, 10 Subjects); only
    #        the decision leg is missing (no event-kind Point), which the pre-fix
    #        verdict ignored.
    readback_t4 = {
        "overview": {"taxonomy": {"Point": 100, "Event": 26, "Subject": 10,
                                   "Object": 25, "Source": 6},
                     "structure": {"total": 90, "operators": 30},
                     "pointkinds": {"by_kind": {"statement": 100}},
                     "sources": {"total": 6, "with_points": 6}},
        "entities": [{"id": "s1", "name": "Marta Reyes", "layer": "subject"}],
    }
    # A decision worded in a Point, so the `decision_points` half of the leg holds and
    # the arm isolates the event-KIND half.
    receipt_with_decision = {"passes": [{"pass": 1, "documents": [{"doc_id": "doc-01",
        "result": {"points": [{"kind": "event",
                               "text": "we decided to revert to NordicFreight"}]}}]}]}
    monkeypatch.setattr(module, "load", lambda name: readback_t4)
    row = module.t4(receipt_with_decision, _corpus())
    assert row["flat_distribution"].get("Event") == 26, "the fixture must satisfy the layer leg"
    assert row["decision_leg"]["event_point_kind_rows"] == 0
    assert row["verdict"] == "FAIL", (
        "T4 PASSes a run whose decisions never landed as an event-kind row — the rule's "
        "decision clause is computed and dropped")
    readback_t4["overview"]["pointkinds"] = {"by_kind": {"event": 80}}
    assert module.t4(receipt_with_decision, _corpus())["verdict"] == "PASS", (
        "T4 fails with an event-kind Point present — the decision leg is not reading the "
        "distribution")
    # …and the leg's OTHER half: event-kind Points exist, but no decision is worded
    # anywhere they could have landed from.
    assert module.t4({}, _corpus())["verdict"] == "FAIL", (
        "T4 PASSes a graph holding no decision at all — the `decision_points` half of "
        "its decision clause is not read")


def test_t1_the_second_ingestion_numbers_are_the_artifacts_numbers():
    row = _score()["tests"]["T1"]
    after_1, after_2 = row["counts"]["after_pass_1"], row["counts"]["after_pass_2"]
    assert after_1["taxonomy"] == {"Point": 134, "Event": 12, "Subject": 10,
                                   "Object": 25, "Source": 6}
    assert after_2["taxonomy"] == {"Point": 243, "Event": 26, "Subject": 10,
                                   "Object": 29, "Source": 12}
    # The decisive claim: entity identity holds, the claim layer multiplies.
    assert row["entities"]["subject_delta"] == 0
    assert row["entities"]["object_delta"] == 4
    restated = row["restated_points_pass_2"]
    assert restated["pass_2_points"] == 61
    assert restated["restating_a_pass_1_point"] == 38
    dedups = [r["dedup"] for r in restated["rows"]]
    assert dedups.count("new") == 26
    assert dedups.count("content_hash_hit") == 12
    # Event-kind POINTS re-mint in lockstep — the cleanest single witness.
    assert after_1["points_by_kind"].get("event") == 40
    assert after_2["points_by_kind"].get("event") == 80
    report = _report()
    for figure in ("**+109 Points", "+14 Events", "+4 Objects", "38 of the 61 Points", "26 carry"):
        assert figure in report, f"REPORT.md no longer quotes {figure!r}"


def test_t2_the_two_wordings_landed_as_two_points():
    row = _score()["tests"]["T2"]
    assert row["goal_point_ids"]["shared"] == [], \
        "T2 fails because the two wordings share no point id; a merge would change this"
    assert row["delta_across_doc_06_ingest"]["taxonomy.Point"] == 15
    assert row["delta_across_doc_06_ingest"]["taxonomy.Object"] == 1
    assert row["sources"] == {"before": 5, "after": 6, "delta": 1}


def test_t3_the_orphan_share_is_stated_with_its_population_and_its_door():
    row = _score()["tests"]["T3"]
    assert row["population"]["entities_enumerated_by_full_scan"] == 30
    assert row["orphans_by_write_receipt"]["count"] == 7
    assert row["orphans_by_write_receipt"]["share"] == pytest.approx(0.233)
    # The disagreement between the two reads is the finding, not noise: a door
    # that reported 7 "isolated" entities would make D4 unfalsifiable.
    assert row["orphans_by_door"]["count"] == 0
    coverage = _readback()["population_coverage"]
    assert coverage["resolved"] == 30
    assert coverage["unresolved"]["event"] == 26, \
        "Events are unroutable through the doors (D2); a fix moves this number"


def test_t4_every_ontology_layer_is_used_and_the_kinds_are_not_collapsed():
    row = _score()["tests"]["T4"]
    flat = row["flat_distribution"]
    assert flat == {"Point": 243, "Event": 26, "Subject": 10, "Object": 29, "Source": 12}
    kinds = row["entity_kinds_seen"]
    assert kinds.get("core:naturalPerson") == 3
    assert kinds.get("core:role") == 3
    assert kinds.get("core:decision") == 1
    assert len([k for k, n in kinds.items() if n]) >= 12, \
        "T4's PASS rests on the kinds being USED; a collapse to one kind reds here"
    assert row["document_leg"]["sources"] == 12
    assert row["document_leg"]["sources_with_points"] == 12


def test_t5_every_sampled_relation_is_traced_and_the_tiers_are_separate():
    row = _score()["tests"]["T5"]
    assert row["sampled"] == 26, "T5 requires a sample of at least 10 relations"
    assert row["tiers"] == {"verbatim": 17, "paraphrase": 9, "none": 0}
    assert row["unsupported"] == []
    for sample in row["rows"]:
        assert sample["support"] in ("verbatim", "paraphrase")
        assert sample["co_stated_verbatim"] or sample["co_stated_paraphrase"]


def test_the_scorer_keeps_no_data_of_its_own():
    """The scorer is a pure function of the two receipts — no graph, no network.

    A scorer that reached a database would make every verdict above unreplayable
    by a reviewer, which is the whole value of committing the artifacts.
    """
    source = (BUNDLE / "score_t1_t5.py").read_text()
    for forbidden in ("import socket", "TortoiseSDK(", "requests.", "urllib.request",
                      "MATCH (", "falkordb"):
        assert forbidden not in source, f"scorer must stay artifact-only: found {forbidden!r}"


def test_the_five_rules_state_BOTH_their_conjuncts_and_the_report_discloses_the_ungraded_axis():
    """Every conjunct a rule names must be one the scorer ENFORCES, and every axis it
    does NOT enforce must be disclosed to the reader.

    The earlier spelling of this test was named "the five rules are quoted in the
    report" and did neither half: it looped `assert needle in report`, which any single
    mention satisfies (four other T2 mentions keep it green after deleting one), and the
    report quotes no rule at all. #14812's review measured both. What is graded instead
    is the pair of claims the findings landed on: T3's rule must keep BOTH conjuncts (its
    second is the one that was dropped), and T5's rule must say the predicate axis is
    not graded — because a rule that silently omits an axis it cannot see is the
    fail-open shape this bundle exists to prevent.
    """
    score = _score()
    rules, report = score["rules"], _report()
    for name in ("T1", "T2", "T3", "T4", "T5"):
        assert rules[name].startswith(("PASS iff", "Reported as a share")), \
            f"{name}'s rule must state its criterion, not a narrative"
    assert "0.8" in rules["T1"], "T1's restatement band is part of the criterion"
    assert "two sources" in rules["T2"]
    # T3 has TWO conjuncts and both are graded — the surviving defect's own guard.
    assert "majority" in rules["T3"]
    assert "every orphan" in rules["T3"], (
        "RULE_T3 lost its second conjunct — that is the clause the scorer dropped, and "
        "deleting it from the rule instead of implementing it re-opens the same "
        "rule-says-one-thing/verdict-does-another gap")
    assert "every orphan" in score["tests"]["T3"]["rule"]
    # T4's decision clause is named in the rule and graded by the verdict.
    assert "decision" in rules["T4"]
    assert is_t4_decision_graded(score), "T4's rule names a decision clause the verdict skips"
    # T5 grades the endpoints and SAYS the predicate axis is not graded; the report
    # carries the same disclosure and the number behind it.
    assert "PREDICATE axis is NOT graded" in rules["T5"], (
        "RULE_T5 no longer discloses that it cannot grade a relation's predicate — the "
        "bundle would then read as covering the invention axis it does not cover")
    assert "D6" in report and "predicate axis is not graded" in report.lower(), (
        "REPORT.md lost D6, so the ungraded predicate axis is no longer disclosed")
    axis = score["tests"]["T5"]["predicate_axis"]
    assert axis["graded"] is False and axis["sampled"] == 26
    assert axis["predicate_wording_in_co_stated_docs"] == 0, (
        "the reported predicate-gap number moved — re-read D6 rather than assuming the "
        "axis is still unmeasurable")


def is_t4_decision_graded(score: dict) -> bool:
    """T4's verdict must FAIL when its own decision leg is false.

    `decision_leg.graded` is the scorer's own record of the evaluation, so the rule and
    the verdict cannot disagree about whether the clause was read.
    """
    return score["tests"]["T4"]["decision_leg"].get("graded") is True
