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
import os
import re
import subprocess
import sys
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


def _source_rows(docs: tuple[str, ...]) -> dict:
    """A `sources` door reply whose rows name the documents they came from.

    T4 grades PER-DOCUMENT coverage off these rows, so a fixture that hands it a global
    count alone is no longer a valid subject for the leg.
    """
    return {"ok": True, "reply": [{"url": f"session:{d}", "sourceKind": "agentSession",
                                   "points": 3} for d in docs]}


def _corpus_tokens() -> set[str]:
    """Every content token in the corpus, through the SCORER's own tokenizer.

    Using the module's tokenizer (not a local copy) is what makes the stopword filter the
    thing under test: if function words creep back into `tokens()`, this set gains them and
    the arm below loses its meaning.
    """
    module = _score_module()
    out: set[str] = set()
    for text in module.doc_texts(_corpus()).values():
        out |= module.tokens(text)
    return out


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
        "T1": "FAIL", "T2": "FAIL", "T3": "FAIL", "T4": "PASS", "T5": "FAIL",
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
    computed_rows = {
        "T1": module.t1(receipt),
        "T2": module.t2(receipt, corpus),
        "T3": module.t3(receipt, corpus),
        "T4": module.t4(receipt, corpus),
        "T5": module.t5(receipt, corpus),
    }
    computed = {name: row["verdict"] for name, row in computed_rows.items()}
    committed = {name: row["verdict"] for name, row in _score()["tests"].items()}
    assert computed == committed, (
        "the scorer no longer computes the verdicts its own artifact states — a rule "
        "changed under figures that were not re-derived")
    assert computed == _report_verdicts()
    # ⛔ RULE TEXT is what REPORT §2 promises is "quoted beside" every verdict, and it
    # was the ONE thing a committed-JSON assertion could not see: deleting T5's whole D6
    # disclosure from the scorer left the suite green, because the tests read
    # `score["rules"]` and never the module. A scorer whose rule has silently diverged
    # from the artifact's is the exact defect class this bundle exists to catch.
    assert {name: module.RULE_T1 if name == "T1" else getattr(module, f"RULE_{name}")
            for name in ("T1", "T2", "T3", "T4", "T5")} == {
        name: _score()["rules"][name] for name in ("T1", "T2", "T3", "T4", "T5")}, (
        "the scorer's RULE_* text has diverged from the artifact's `rules` block — the "
        "rule quoted beside a verdict must be the rule the scorer holds")
    # ⛔ The MEASURED numbers, not only the verdicts. Deactivating T1's restatement
    # detector left the suite green (T1 still FAILs on object_delta), so the bundle's
    # headline mechanism could be broken with nothing reddening. These are recomputed
    # from the scorer and compared to the committed artifact.
    assert (computed_rows["T1"]["restated_points_pass_2"]["restating_a_pass_1_point"]
            == _score()["tests"]["T1"]["restated_points_pass_2"]["restating_a_pass_1_point"])
    assert (computed_rows["T3"]["population"]["entities_enumerated_by_full_scan"]
            == _score()["tests"]["T3"]["population"]["entities_enumerated_by_full_scan"])
    assert (computed_rows["T5"]["tiers"] == _score()["tests"]["T5"]["tiers"]
            and computed_rows["T5"]["sampled"] == _score()["tests"]["T5"]["sampled"])
    assert (computed_rows["T4"]["decision_leg"]["decision_entities"]
            == _score()["tests"]["T4"]["decision_leg"]["decision_entities"])


def test_every_conjunct_of_a_rule_changes_its_verdict(monkeypatch):
    """Each rule's conjunct must be READ, not merely computed and printed.

    Both findings were found from a report field that existed and was ignored: T3's
    `orphan_traceability` (its second conjunct) and T4's `event_point_kind_rows` (its
    decision clause). A field nobody reads is a rule that cannot fail, so each leg is
    driven here in the direction that must FAIL — the shape the pre-fix code passed.
    """
    module = _score_module()

    # ── T3: the share leg passes (1 of 3 orphans is 0.333, a strict majority NOT
    #        orphaned) and only the second conjunct can fail: `a thing the corpus
    #        never says` is untraceable. ⛔ The fixture is deliberately NOT 1-of-2:
    #        at exactly half neither side is a majority, so the share leg would fail
    #        too and this arm would no longer isolate the traceability conjunct.
    readback_t3 = {
        "entities": [{"id": "e1", "name": "Priya Nair", "kind": "core:naturalPerson",
                      "layer": "subject"},
                     {"id": "e2", "name": "a thing the corpus never says",
                      "kind": "core:strategy", "layer": "object"},
                     {"id": "e3", "name": "Sales Lead", "kind": "core:role",
                      "layer": "subject"}],
        "neighbours": {},
    }
    receipt_t3 = {"passes": [{"pass": 1, "documents": [{"doc_id": "doc-01", "result": {
        "relation_write": {"edges": [{"src_id": "e1", "dst_id": "e3"}]}}}]}]}
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


def test_t3_share_leg_is_graded_at_the_majority_boundary(monkeypatch):
    """A majority is MORE than half: at exactly half, neither side is one, so FAIL.

    The code spelled the ORPHAN side as `share > 0.5` while the rule printed beside
    the verdict says "PASS iff the majority are NOT orphans". At exactly 50 % it
    therefore reported `majority_are_not_orphans: true` and PASSed where its own rule
    says FAIL — and an earlier arm pinned that wrong side of the boundary (review
    round 2, finding 2).
    """
    module = _score_module()

    def row_for(n_entities: int, n_linked: int):
        ents = [{"id": f"e{i}", "name": "Priya Nair", "kind": "core:naturalPerson",
                 "layer": "subject"} for i in range(n_entities)]
        # An edge marks BOTH endpoints linked; each edge therefore points at a name
        # outside the population, leaving exactly `n_entities - n_linked` orphans.
        edges = [{"src_id": f"e{i}", "dst_id": "outside"} for i in range(n_linked)]
        monkeypatch.setattr(module, "load", lambda name: {"entities": ents, "neighbours": {}})
        receipt = {"passes": [{"pass": 1, "documents": [{"doc_id": "doc-01", "result": {
            "relation_write": {"edges": edges}}}]}]}
        return module.t3(receipt, _corpus())

    half = row_for(2, 1)          # 1 of 2 = 0.5 orphaned, every orphan traceable
    assert half["orphans_by_write_receipt"]["share"] == 0.5
    assert half["orphan_legs"]["every_orphan_explained_in_corpus"] is True, \
        "the fixture must leave the traceability conjunct satisfied"
    assert half["orphan_legs"]["majority_are_not_orphans"] is False, \
        "at exactly half, 'the majority are not orphans' is false — that is what a majority means"
    assert half["verdict"] == "FAIL", \
        "T3 PASSes at exactly 50 % orphaned — the rule it prints requires a strict majority"
    assert row_for(3, 2)["verdict"] == "PASS", \
        "T3 fails a 0.333 orphan share with every orphan traceable — the rule's own PASS"
    assert row_for(5, 2)["verdict"] == "FAIL", \
        "T3 PASSes a 0.6 orphan share — the orphan side is not graded"

    # ── T4: every layer leg holds (all four layers, 6 Sources); only the DECISION
    #        leg is missing (no decision-kind entity), which the pre-fix verdict
    #        ignored and the first repair still missed by reading a graph-wide
    #        event-Point tally instead of the entities.
    readback_t4 = {
        "overview": {"taxonomy": {"Point": 100, "Event": 26, "Subject": 10,
                                   "Object": 25, "Source": 6},
                     "structure": {"total": 90, "operators": 30},
                     "pointkinds": {"by_kind": {"statement": 100, "event": 80}},
                     "sources": {"total": 6, "with_points": 6}},
        "sources": _source_rows(("doc-01", "doc-02", "doc-03", "doc-04", "doc-05", "doc-06")),
        "entities": [{"id": "s1", "name": "Marta Reyes", "layer": "subject",
                      "kind": "core:naturalPerson"},
                     {"id": "s2", "name": "Sales Lead", "layer": "subject",
                      "kind": "core:role"}],
    }
    monkeypatch.setattr(module, "load", lambda name: readback_t4)
    row = module.t4({}, _corpus())
    assert row["flat_distribution"].get("Event") == 26, "the fixture must satisfy the layer leg"
    assert row["role_leg"]["graded"] is True, "the fixture must satisfy the role leg"
    # ⛔ `event_point_kind_rows` is 80 in this fixture and its verdict is still FAIL —
    # the pre-repair code read that tally and PASSed, which is the defect.
    assert row["decision_leg"]["event_point_kind_rows"] == 80
    assert row["decision_leg"]["graded"] is False
    assert row["verdict"] == "FAIL", (
        "T4 PASSes a run whose decisions never landed as a decision-kind ENTITY — the "
        "rule's decision leg is computed and dropped, or graded on an unrelated tally")
    # …and with a decision-kind entity present it must PASS, so the arm cannot be
    #   satisfied by making T4 fail unconditionally.
    readback_t4["entities"].append({"id": "s3", "name": "the delivery partner decision",
                                    "layer": "object", "kind": "core:decision"})
    row = module.t4({}, _corpus())
    assert row["decision_leg"]["graded"] is True
    assert row["verdict"] == "PASS", (
        "T4 fails with a decision-kind entity present — the decision leg is not reading "
        "the entity kinds")
    # …and the ROLE leg's own bite: a subject-layer person does not stand in for a role.
    readback_t4["entities"] = [readback_t4["entities"][0], readback_t4["entities"][2]]
    assert module.t4({}, _corpus())["verdict"] == "FAIL", (
        "T4 PASSes a graph with no `core:role` entity — any subject-layer entity is "
        "being read as a role")

    # ── T2: the rule states TWO graded legs; the committed run fails both. Resolve the
    #        goal leg on the committed data and the verdict must STILL be FAIL because the
    #        entity delta is non-zero — which is what proves the entity-delta conjunct is
    #        read, the one a review measured as deletable with every row still green.
    readback_t2 = json.loads(json.dumps(_readback()))
    monkeypatch.setattr(module, "load", lambda name: readback_t2)
    receipt_t2 = json.loads(json.dumps(_receipt()))
    base = module.t2(receipt_t2, _corpus())
    assert base["verdict"] == "FAIL", "the committed run fails T2; the fixture starts there"
    assert base["legs"]["goal_resolves_to_one_thing"] is False
    cands = _score()["tests"]["T2"]["goal_point_candidates"]
    ids_doc3 = [c["point_id"] for c in cands if c["doc_id"] == "doc-03"]
    ids_doc6 = [c["point_id"] for c in cands if c["doc_id"] == "doc-06"]
    assert len(ids_doc3) == len(ids_doc6) == 2, \
        "the goal fact is two sentences per wording; the arm resolves them 1:1"
    renamed = 0
    # ⛔ Mutate the RECEIPT, not `points()`'s return: that helper yields `{**pt, …}`
    # copies, so a rebind on its output never reaches the data the scorer reads.
    for doc in receipt_t2["passes"][0]["documents"]:
        for pt in ((doc.get("result") or {}).get("points") or []):
            if pt.get("point_id") in ids_doc6:
                pt["point_id"] = ids_doc3[ids_doc6.index(pt["point_id"])]
                renamed += 1
    assert renamed == len(ids_doc6), "the fixture could not rebind every doc-06 goal point"
    forced = module.t2(receipt_t2, _corpus())
    assert forced["legs"]["goal_resolves_to_one_thing"] is True, (
        "the goal leg did not see the forced resolution — the arm is not driving the leg")
    assert forced["legs"]["entity_delta_zero"] is False
    assert forced["verdict"] == "FAIL", (
        "T2 PASSes a COMPLETE merge that still grew entities — its entity-delta "
        "conjunct is dropped")


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
    assert restated["restating_a_pass_1_point"] == 40
    dedups = [r["dedup"] for r in restated["rows"]]
    assert dedups.count("new") == 28
    assert dedups.count("content_hash_hit") == 12
    # Event-kind POINTS re-mint in lockstep — the cleanest single witness.
    assert after_1["points_by_kind"].get("event") == 40
    assert after_2["points_by_kind"].get("event") == 80
    report = _report()
    for figure in ("**+109 Points", "+14 Events", "+4 Objects", "40 of the 61 Points", "28 carry"):
        assert figure in report, f"REPORT.md no longer quotes {figure!r}"


def test_t2_the_two_wordings_landed_as_two_points():
    row = _score()["tests"]["T2"]
    assert row["goal_point_ids"]["shared"] == [], \
        "T2 fails because the two wordings share no point id; a merge would change this"
    # BOTH graded legs are recorded, not just the one that happens to fail first.
    legs = row["legs"]
    assert legs["entity_delta_zero"] is False
    assert legs["goal_resolves_to_one_thing"] is False
    assert len(legs["doc_06_ids_unresolved_in_doc_03"]) == 2 and legs["shared_count"] == 0
    # The goal fact is TWO sentences per wording, so four candidate Points exist and a
    # count of shared ids asks the wrong question in both directions (review round 3,
    # finding 2). The artifact must disclose the granularity it graded at.
    assert len(row["goal_point_candidates"]) == 4
    assert "two sentences per wording" in legs["granularity"]
    # Event is excluded on purpose — RULE_T1 declares one Event per capture expected, so
    # counting it here would fail T2 on an axis the bundle calls clean.
    assert "Event excluded" in legs["entity_axes"]
    assert row["delta_across_doc_06_ingest"]["taxonomy.Object"] == 1
    # …and the leg the door cannot answer is disclosed, not silently implied.
    assert row["source_link_leg"]["graded"] is False
    assert "D7" in row["source_link_leg"]["why"]
    assert row["delta_across_doc_06_ingest"]["taxonomy.Point"] == 15
    assert row["delta_across_doc_06_ingest"]["taxonomy.Object"] == 1
    assert row["sources"] == {"before": 5, "after": 6, "delta": 1}


def test_t3_the_orphan_share_is_stated_with_its_population_and_its_door():
    row = _score()["tests"]["T3"]
    assert row["population"]["entities_enumerated_by_full_scan"] == 30
    assert row["orphans_by_write_receipt"]["count"] == 7
    assert row["orphans_by_write_receipt"]["share"] == pytest.approx(0.233)
    # RULE_T3 has TWO conjuncts and both are recorded: the share passes, the
    # traceability conjunct is what makes this FAIL.
    legs = row["orphan_legs"]
    assert legs["majority_are_not_orphans"] is True
    assert legs["every_orphan_explained_in_corpus"] is False
    # THREE names fall below the band, at the band the rule itself prints (content
    # tokens of >2 characters, i.e. T1's `overlap()`, NOT a second tokenisation):
    # `the ownership map` scores 0.500 against doc-04, and the other two 0.600/0.667.
    assert [r["name"] for r in legs["unexplained"]] == [
        "the ownership map", "the cold-chain rules as operational preferences",
        "chilled-goods operation"]
    overlap_by_name = {r["name"]: r["best_overlap"] for r in row["orphan_traceability"]}
    assert overlap_by_name["the ownership map"] == {"doc": "doc-04", "share": 0.5}
    assert overlap_by_name["chilled-goods operation"]["share"] == pytest.approx(0.667)
    # The band is named in the rule, so the count cannot drift into "no document states
    # it" for a name that merely falls below the band.
    assert "0.8" in row["rule"]
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
    # Both named legs are GRADED, and each is read from its own quantity — not from a
    # graph-wide tally that any Event Point would satisfy (the pre-review defect).
    assert row["decision_leg"]["graded"] is True
    assert [e["kind"] for e in row["decision_leg"]["decision_entities"]] == ["core:decision"]
    assert row["decision_leg"]["decision_entities"]
    assert row["role_leg"]["graded"] is True and row["role_leg"]["subject_entities"] == 3
    # …and the adjacent fact is DISCLOSED rather than implied away: the decision's own
    # sentence is a `statement` Point, not an event-kind one.
    assert row["decision_leg"]["decision_point_kinds"] == ["statement"]
    assert row["decision_leg"]["decisions_stated"] == 2


def test_t5_every_sampled_relation_is_traced_and_the_tiers_are_separate():
    row = _score()["tests"]["T5"]
    # ⛔ THE POPULATION IS BOTH PASSES. The first cut sampled pass 1 only
    # (`relation_rows(receipt, 1)`), which excluded pass 2's 21 of the run's 47 relations
    # (44.7 %) — measured, `relation_rows` over the committed receipt. ⛔ Pass-1-only
    # sampling does NOT rescue the verdict: it samples 26 with tiers
    # {verbatim 17, paraphrase 7, none 2} and still FAILs. So the wider population is here
    # because a third of the run went unexamined, not because the FAIL depends on it — it
    # strengthened the evidence from 2 unsupported edges to 3. (Two earlier spellings of
    # this comment were wrong: one said the only failing edge lived in the excluded half,
    # and its replacement said "two thirds of the run", which no denominator produces.)
    assert row["sampled"] == 47, (
        "T5 must sample every relation the receipts say was written (26 pass 1 + 21 "
        "pass 2); pass-1-only sampling leaves pass 2's 21 of 47 relations unexamined")
    assert {s["pass"] for s in row["rows"]} == {1, 2}
    assert row["tiers"] == {"verbatim": 34, "paraphrase": 10, "none": 3}
    # One invented edge, named — and its name is the point: doc-04 says Priya *reports*
    # the count monthly, and pass 2 minted an entity for the reporting act.
    assert len(row["unsupported"]) == 3, (
        "T5's FAIL rests on exactly three unsupported edges; a rescored run must move this "
        "number deliberately")
    assert {(u["doc_id"], u["pass"], u["predicate"], u["src"], u["dst"])
            for u in row["unsupported"]} == {
        ("doc-05", 1, "related", "ColdPath Logistics", "the wholesale push"),
        ("doc-05", 1, "related", "NordicFreight", "the wholesale push"),
        ("doc-04", 2, "ownedBy", "monthly account count report", "Priya Nair"),
    }, "the invented edges changed — re-derive T5 rather than relaxing this set"
    for u in row["unsupported"]:
        assert u["support"] == "none"
    # ⛔ Content tokens exclude function words: `the wholesale push` is 0.67 WITH them and
    # 0.50 without, and the corpus never says `push`. This is the arm that keeps a
    # function word from carrying an invented name over the paraphrase band.
    assert "push" not in _corpus_tokens(), (
        "the token `push` is in the corpus now — re-derive T5 rather than leaving two "
        "unsupported edges pinned to a name the corpus does say")
    for sample in row["rows"]:
        assert sample["support"] in ("verbatim", "paraphrase", "none")
        if sample["support"] != "none":
            assert sample["co_stated_verbatim"] or sample["co_stated_paraphrase"]
    # The predicate axis is declared ungraded and carries a number (gap D6).
    axis = row["predicate_axis"]
    assert axis["graded"] is False and axis["sampled"] == 47
    assert axis["predicate_wording_in_co_stated_docs"] == 0


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
    assert axis["graded"] is False and axis["sampled"] == 47
    assert axis["predicate_wording_in_co_stated_docs"] == 0, (
        "the reported predicate-gap number moved — re-read D6 rather than assuming the "
        "axis is still unmeasurable")


def is_t4_decision_graded(score: dict) -> bool:
    """T4's verdict must FAIL when its own decision leg is false.

    `decision_leg.graded` is the scorer's own record of the evaluation, so the rule and
    the verdict cannot disagree about whether the clause was read.
    """
    return score["tests"]["T4"]["decision_leg"].get("graded") is True


def module_build() -> dict:
    """A fresh build of the whole artifact, from the committed inputs."""
    return _score_module().build_result()


def test_the_artifact_is_byte_identical_under_a_different_hash_seed():
    """Re-running the scorer must reproduce the committed bytes, not merely the values.

    The bundle's one reproducible property is "a reviewer re-runs `score_t1_t5.py` and
    diffs it against the committed file". Two `set`-ordered dict builds (`delta()`'s axis
    loop and `predicate_histogram`) made the emitted KEY ORDER depend on
    `PYTHONHASHSEED`: rerunning produced byte-different JSON while every value agreed, so
    a reviewer's diff was noise and `git status` was never clean after a verify. Both are
    `sorted()` now, and this arm runs the scorer under two seeds in separate processes —
    the only way the hash seed can differ — so a return to set-ordering reds here.
    """
    script = (
        "import importlib.util, json, sys\n"
        "spec = importlib.util.spec_from_file_location('noise_score', sys.argv[1])\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "receipt = json.loads(open(sys.argv[2]).read())\n"
        "corpus = json.loads(open(sys.argv[3]).read())\n"
        "payload = {'tests': {\n"
        "    'T1': m.t1(receipt), 'T2': m.t2(receipt, corpus),\n"
        "    'T3': m.t3(receipt, corpus), 'T4': m.t4(receipt, corpus),\n"
        "    'T5': m.t5(receipt, corpus)}}\n"
        "sys.stdout.write(json.dumps(payload, indent=2, default=str))\n"
    )
    outputs = []
    for seed in ("0", "1", "4294967295"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        outputs.append(subprocess.run(
            [sys.executable, "-c", script,
             str(BUNDLE / "score_t1_t5.py"),
             str(BUNDLE / "out" / "ingest-receipt.json"),
             str(BUNDLE / "corpus" / "noise-corpus.json")],
            capture_output=True, text=True, env=env, check=True).stdout)
    assert len(set(outputs)) == 1, (
        "the scorer's output is not byte-stable across PYTHONHASHSEED — a set-ordered "
        "dict build has crept back in, so a reviewer re-running it gets a spurious diff")
    # ⛔ EXACT BYTES, not a parsed round-trip. Comparing `json.loads` on both sides is blind
    # to dict KEY ORDER, so an artifact re-sorted in place stayed green while a reviewer's
    # re-run produced a diff — the one property this arm is named for (measured: rewriting
    # the committed file with `sort_keys=True` left the earlier version of this test green).
    fresh = _score_module().render(module_build())
    committed = (BUNDLE / "out" / "t1-t5-score.json").read_text()
    assert fresh == committed, (
        "the committed artifact is not byte-for-byte what the scorer writes — re-run "
        "`python docs/evidence/14691-noise-validation/score_t1_t5.py` and commit its output")


def test_the_conjuncts_that_never_decide_on_this_run_are_still_pinned():
    """The conjuncts that cannot decide on the committed run, driven in both directions.

    Each of these is TRUE on this run (or decides in only one direction here), so no
    committed number moves when it is deleted — and earlier rounds measured several as
    freely deletable with the suite green. Each is driven with the OTHER legs held true, so
    the conjunct under test is the only thing that can decide.

    - T4's `at least one Source per document ingested`.
    - T1's `no Point whose text re-states an existing Point`, which is the only thing
      keeping T1 red once the entity layers hold.
    - T2's goal leg, as a RESOLUTION: a complete merge must PASS and a half merge FAIL
      (two shared ids is a COMPLETE merge on this corpus, not a failure — an earlier
      `len(shared) == 1` inverted that).
    """
    import copy
    import unittest.mock as mock

    module = _score_module()
    corpus = _corpus()

    # ── T1: entity deltas zero + no duplicate names, so `restated` alone decides.
    receipt_t1 = copy.deepcopy(_receipt())
    p2 = receipt_t1["passes"][1]
    for doc in p2["documents"]:
        after = copy.deepcopy(doc["overview_after"])
        before = doc["overview_before"]
        # Hold each entity layer at its pre-document value: no Object/Subject/Event growth.
        for layer in ("taxonomy",):
            after[layer] = dict(before.get(layer) or {})
        doc["overview_after"] = after
    receipt_t1["passes"][1]["overview_after"] = copy.deepcopy(
        receipt_t1["passes"][1]["overview_before"])
    row = module.t1(receipt_t1)
    assert row["entities"]["object_delta"] == 0 and row["entities"]["subject_delta"] == 0
    assert row["same_name_entities_after_both_passes"] == {}
    assert row["restated_points_pass_2"]["restating_a_pass_1_point"] > 0, (
        "the fixture must leave restatements in place, so this conjunct is the only one "
        "that can fail")
    assert row["verdict"] == "FAIL", (
        "T1 PASSes a re-ingestion whose only defect is that Points re-state earlier Points "
        "— the restatement conjunct is dropped")
    # …and with the restatements removed it must PASS, so the arm is not satisfied by
    #   making T1 fail unconditionally.
    for doc in p2["documents"]:
        for pt in ((doc.get("result") or {}).get("points") or []):
            pt["text"] = "zzz nothing in common zzz"
    assert module.t1(receipt_t1)["verdict"] == "PASS", (
        "T1 fails a re-ingestion that adds no entity and re-states nothing — the "
        "restatement conjunct is not reading the detector")

    # ── T2: the entity delta is forced to zero, so the goal RESOLUTION decides.
    cands = _score()["tests"]["T2"]["goal_point_candidates"]
    ids3 = [c["point_id"] for c in cands if c["doc_id"] == "doc-03"]
    ids6 = [c["point_id"] for c in cands if c["doc_id"] == "doc-06"]
    assert len(ids3) == 2 and len(ids6) == 2, "the fixture needs two goal points per document"

    def t2_with(mapping: dict) -> dict:
        """A fresh receipt each time: a rebind is not idempotent, so reusing one would
        silently apply the SECOND mapping to the FIRST mapping's output."""
        receipt = copy.deepcopy(_receipt())
        doc6 = next(d for d in receipt["passes"][0]["documents"]
                    if d["doc_id"] == "doc-06")
        doc6["overview_after"] = copy.deepcopy(doc6["overview_before"])
        for doc in receipt["passes"][0]["documents"]:
            for pt in ((doc.get("result") or {}).get("points") or []):
                if pt.get("point_id") in mapping:
                    pt["point_id"] = mapping[pt["point_id"]]
        return module.t2(receipt, corpus)

    # ── The goal leg is a RESOLUTION, not a count (review round 3, finding 2): the goal
    #    fact is two sentences per wording, so `len(shared) == 1` FAILED a complete 1:1
    #    merge (two shared ids) and PASSED a half merge (one) — the acceptance gate this
    #    issue sets, open on a partial fix and shut on the fix it asks for.
    # A COMPLETE merge — every doc-06 goal candidate reuses a doc-03 id — is the bar.
    full = t2_with({ids3[0]: ids6[0], ids3[1]: ids6[1]})
    assert full["legs"]["entity_delta_zero"] is True, "the entity leg must be held true here"
    assert full["legs"]["doc_06_ids_unresolved_in_doc_03"] == []
    assert full["legs"]["goal_resolves_to_one_thing"] is True
    assert full["verdict"] == "PASS", (
        "T2 FAILs a complete 1:1 merge — the leg is still counting shared ids instead of "
        "asking whether anything survived as a second copy")
    # A HALF merge — one doc-06 candidate re-minted, the other resolved — is FAIL.
    half = t2_with({ids3[1]: ids6[0]})
    assert half["legs"]["doc_06_ids_unresolved_in_doc_03"] == [ids6[1]], \
        "the half-merge control did not rebind exactly one doc-06 id"
    assert half["verdict"] == "FAIL", (
        "T2 PASSes a half merge — the partial fix still leaves a second copy of the goal, "
        "which is the defect this leg exists to grade")
    # ⛔ The THIRD case, and the one the non-emptiness guard exists for: doc-06
    #    contributed NO goal candidate at all (the fact dropped, or reworded below the
    #    0.45 candidate band) with no entity growth. `ids_b - ids_a` is then empty, so a
    #    containment test alone PASSes total loss — the acceptance gate this issue sets,
    #    open on the worst outcome (review round 5, finding 1).
    receipt_absent = copy.deepcopy(_receipt())
    doc6_absent = next(d for d in receipt_absent["passes"][0]["documents"]
                       if d["doc_id"] == "doc-06")
    doc6_absent["overview_after"] = copy.deepcopy(doc6_absent["overview_before"])
    for pt in ((doc6_absent.get("result") or {}).get("points") or []):
        pt["text"] = "zzz nothing about the goal in common zzz"
    absent = module.t2(receipt_absent, corpus)
    assert absent["legs"]["entity_delta_zero"] is True, "the fixture must hold the entity leg true"
    assert absent["legs"]["doc_06_ids_unresolved_in_doc_03"] == [], \
        "the fixture must leave NOTHING unresolved, so only the non-emptiness guard decides"
    assert absent["legs"]["goal_resolves_to_one_thing"] is False
    assert absent["verdict"] == "FAIL", (
        "T2 PASSes a restatement that produced no goal point at all — the leg's "
        "non-emptiness guard is dropped, so total loss satisfies a containment test")

    # ── T4: every ingested document must carry a Source — PER-DOCUMENT coverage, not a
    #    global count. Six Sources all from doc-01 is `Source >= documents` and must FAIL
    #    (review round 6, finding 1: the global count was fail-open).
    readback = copy.deepcopy(_readback())
    readback["overview"]["taxonomy"] = {"Point": 100, "Event": 26, "Subject": 10,
                                        "Object": 25, "Source": 6}
    readback["overview"]["sources"] = {"total": 6, "with_points": 6}
    readback["sources"] = _source_rows(("doc-01",) * 6)
    readback["entities"] = [
        {"id": "s1", "name": "Sales Lead", "layer": "subject", "kind": "core:role"},
        {"id": "s2", "name": "the delivery partner decision", "layer": "object",
         "kind": "core:decision"},
    ]
    with mock.patch.object(module, "load", return_value=readback):
        hoarded = module.t4({}, corpus)
        assert hoarded["document_leg"]["sources"] == 6, \
            "the fixture satisfies the global COUNT, so only coverage can decide"
        assert hoarded["document_leg"]["documents_with_a_source"] == 1
        assert hoarded["verdict"] == "FAIL", (
            "T4 PASSes six Sources that all came from ONE document — the leg grades a "
            "global count, not RULE_T4's per-document coverage")
        readback["sources"] = _source_rows(
            ("doc-01", "doc-02", "doc-03", "doc-04", "doc-05", "doc-06"))
        assert module.t4({}, corpus)["verdict"] == "PASS", (
            "T4 fails when every document carries a Source — the coverage leg is "
            "over-strict")

    # ── T1's OTHER three conjuncts: `object_delta <= 0`, `subject_delta <= 0` and
    #    `not dup_names` (review round 3, finding 4). All three are TRUE on the
    #    committed data and the only T1 arm drove `restated`, so deleting any of them
    #    left the artifact byte-identical and every pinned row green — T1 would have
    #    PASSed a re-ingestion that added 4 Objects and restated nothing. Each is
    #    driven here with the other three legs held true.
    def t1_with(*, objects: int = 0, subjects: int = 0, duplicate_name: bool = False) -> dict:
        receipt = copy.deepcopy(_receipt())
        p2 = receipt["passes"][1]
        # No restatements: `restated` cannot decide.
        for doc in p2["documents"]:
            for pt in ((doc.get("result") or {}).get("points") or []):
                pt["text"] = "zzz nothing in common zzz"
        after = copy.deepcopy(receipt["passes"][0]["overview_after"])
        tax = dict(after.get("taxonomy") or {})
        tax["Object"] = tax.get("Object", 0) + objects
        tax["Subject"] = tax.get("Subject", 0) + subjects
        after["taxonomy"] = tax
        p2["overview_after"] = after
        readback = copy.deepcopy(_readback())
        if duplicate_name:
            ents = readback.get("entities") or []
            ents.append(dict(ents[0], id="dup-of-" + str(ents[0]["id"])))
        with mock.patch.object(module, "load", return_value=readback):
            return module.t1(receipt)

    control = t1_with()
    assert control["restated_points_pass_2"]["restating_a_pass_1_point"] == 0
    assert control["entities"]["object_delta"] == 0
    assert control["entities"]["subject_delta"] == 0
    assert control["same_name_entities_after_both_passes"] == {}
    assert control["verdict"] == "PASS", "the control must hold all four legs true"
    grew_objects = t1_with(objects=4)
    assert grew_objects["entities"]["object_delta"] == 4
    assert grew_objects["verdict"] == "FAIL", (
        "T1 PASSes a re-ingestion that added 4 Objects — its `object_delta <= 0` "
        "conjunct is dropped")
    grew_subjects = t1_with(subjects=1)
    assert grew_subjects["entities"]["subject_delta"] == 1
    assert grew_subjects["verdict"] == "FAIL", (
        "T1 PASSes a re-ingestion that added a Subject — its `subject_delta <= 0` "
        "conjunct is dropped")
    named_twice = t1_with(duplicate_name=True)
    assert named_twice["same_name_entities_after_both_passes"], \
        "the duplicate-name fixture did not create a same-name pair"
    assert named_twice["verdict"] == "FAIL", (
        "T1 PASSes a graph that counts the same name twice — its `not dup_names` "
        "conjunct is dropped")
def test_both_no_data_arms_refuse_an_empty_population():
    """An empty population must report NO-DATA, never a free PASS.

    `NO-DATA` is the fail-open direction of both arms — turning either into PASS makes
    an empty run read as a clean one — and neither was driven, so both mutations survived
    a whole review round.
    """
    import unittest.mock as mock

    module = _score_module()
    corpus = _corpus()

    empty_readback = {"entities": [], "neighbours": {}}
    with mock.patch.object(module, "load", return_value=empty_readback):
        row = module.t3({"passes": []}, corpus)
    assert row["population"]["entities_enumerated_by_full_scan"] == 0
    assert row["verdict"] == "NO-DATA", "T3 reports PASS on an EMPTY population"
    assert row["orphan_legs"]["majority_are_not_orphans"] is False, \
        "an empty population satisfies neither side of a majority, so the leg is False"

    with mock.patch.object(module, "relation_rows", return_value=[]):
        row = module.t5({}, corpus)
    assert row["sampled"] == 0
    assert row["verdict"] == "NO-DATA", "T5 reports PASS with NO relations sampled"

    # ⛔ An UNNAMED orphan must not pass the traceability conjunct BY ABSENCE. The share leg
    # counts it in the denominator, so skipping it here makes the second conjunct grade a
    # population it silently shrank — fail-open, and nothing pinned it.
    # THREE entities with ONE orphan, so the share leg passes under the STRICT majority
    # (1/3 = 0.333 < 0.5) and only the traceability conjunct can decide.
    unnamed = {
        "entities": [{"id": "e1", "name": "Priya Nair", "kind": "core:naturalPerson",
                      "layer": "subject"},
                     {"id": "e2", "name": "", "kind": "core:strategy",
                      "layer": "object"},
                     {"id": "e3", "name": "ColdPath Logistics", "kind": "core:organization",
                      "layer": "object"}],
        "neighbours": {},
    }
    edges = {"passes": [{"pass": 1, "documents": [{"doc_id": "doc-01", "result": {
        "relation_write": {"edges": [{"src_id": "e1", "dst_id": "e1"},
                                     {"src_id": "e3", "dst_id": "e3"}]}}}]}]}
    with mock.patch.object(module, "load", return_value=unnamed):
        row = module.t3(edges, corpus)
    assert row["orphan_legs"]["majority_are_not_orphans"] is True, \
        "the fixture keeps the share leg passing, so only the second conjunct decides"
    unexplained = row["orphan_legs"]["unexplained"]
    assert [r["name"] for r in unexplained] == [""], (
        "an UNNAMED orphan vanished from the traceability sample — the conjunct then passes "
        "it by absence while the share leg still counts it")
    assert row["verdict"] == "FAIL", (
        "T3 PASSes an unnamed orphan: nothing can be standalone in the text under a name "
        "it does not have")


def test_t2s_candidate_filter_uses_all_four_directions():
    """`RULE_T2` says "both directions" for BOTH wordings; the code must use four.

    An earlier cut computed three (`wording_b`'s reverse was missing) while printing "both".
    A missing direction can only drop a candidate, and a dropped candidate is a dropped
    `unresolved` id — an easier PASS. Each direction is removed in turn and the filter must
    be seen to lose a point it otherwise keeps, in a fixture built so that exactly one
    direction carries the match.
    """
    module = _score_module()
    corpus = _corpus()
    fact = corpus["_declared_facts"]["goal"]
    a, b = module.norm(fact["wording_a"]), module.norm(fact["wording_b"])

    # A text that matches ONLY b->text (its own words are in wording_b, and wording_b's
    # words are not a subset of it), so the b-reverse direction is the sole argmax.
    probe = b
    dirs = {
        "text->a": module.overlap(probe, a),
        "text->b": module.overlap(probe, b),
        "a->text": module.overlap(fact["wording_a"], probe),
        "b->text": module.overlap(fact["wording_b"], probe),
    }
    assert dirs["b->text"] == 1.0, "the probe must be carried by the b->text direction"
    assert dirs["a->text"] < 1.0, "the probe must NOT be carried by a->text as well"
    # Every direction is reachable, so no one of them is dead code the rule overstates.
    assert all(v >= 0.0 for v in dirs.values())

    # And the scorer's own argmax includes the reverse of BOTH wordings: a text that only
    # the b-reverse explains still clears the 0.45 candidate band.
    cands = module.t2(_receipt(), corpus)["goal_point_candidates"]
    assert cands, "the committed run has candidates; the direction set is what produces them"
    # The four-direction set is what the rule prints — pinned against the source so a
    # narrowing back to three reds, whether or not a committed number moves.
    src = (BUNDLE / "score_t1_t5.py").read_text()
    for direction in ('overlap(text, wording_a)', 'overlap(text, wording_b)',
                      'overlap(fact["wording_a"], text)', 'overlap(fact["wording_b"], text)'):
        assert direction in src, f"RULE_T2 says 'both directions'; {direction} is missing"


def test_t4s_role_leg_requires_the_subject_layer_and_t2_allows_a_net_deletion():
    """Two legs whose second half was unpinned, both fail-open as shipped.

    - `RULE_T4` says roles land as **Subject-layer** `core:role` entities. Deleting the
      `layer == "subject"` check left the suite green, because no committed `:role` sits off
      the subject layer — so a role landing as an object would have PASSed T4.
    - `RULE_T2` says the restatement *adds* 0 entities; `adds` is `delta <= 0`, the reading
      `RULE_T1` already uses. `== 0` made a net DELETION fail, and nothing pinned either
      direction.
    """
    import copy
    import unittest.mock as mock

    module = _score_module()
    corpus = _corpus()

    readback = copy.deepcopy(_readback())
    readback["overview"]["taxonomy"] = {"Point": 100, "Event": 26, "Subject": 10,
                                        "Object": 25, "Source": 6}
    readback["overview"]["sources"] = {"total": 6, "with_points": 6}
    readback["sources"] = _source_rows(
        ("doc-01", "doc-02", "doc-03", "doc-04", "doc-05", "doc-06"))
    readback["entities"] = [
        {"id": "s1", "name": "Sales Lead", "layer": "object", "kind": "core:role"},
        {"id": "s2", "name": "the delivery partner decision", "layer": "object",
         "kind": "core:decision"},
    ]
    with mock.patch.object(module, "load", return_value=readback):
        row = module.t4({}, corpus)
    assert row["role_leg"]["graded"] is False, (
        "a `core:role` entity on the OBJECT layer satisfied the role leg — the "
        "`layer == subject` half of RULE_T4 is not read")
    assert row["verdict"] == "FAIL"
    readback["entities"][0]["layer"] = "subject"
    with mock.patch.object(module, "load", return_value=readback):
        assert module.t4({}, corpus)["verdict"] == "PASS", "the control must pass"

    # T2: a net DELETION is not an addition, so `adds 0 entities` holds.
    cands = _score()["tests"]["T2"]["goal_point_candidates"]
    ids3 = [c["point_id"] for c in cands if c["doc_id"] == "doc-03"]
    ids6 = [c["point_id"] for c in cands if c["doc_id"] == "doc-06"]
    receipt = copy.deepcopy(_receipt())
    d6 = next(d for d in receipt["passes"][0]["documents"] if d["doc_id"] == "doc-06")
    shrunk = copy.deepcopy(d6["overview_before"])
    shrunk["taxonomy"]["Object"] = max(0, shrunk["taxonomy"].get("Object", 0) - 2)
    d6["overview_after"] = shrunk
    for doc in receipt["passes"][0]["documents"]:
        for pt in ((doc.get("result") or {}).get("points") or []):
            if pt.get("point_id") == ids3[0]:
                pt["point_id"] = ids6[0]
            elif pt.get("point_id") == ids3[1]:
                pt["point_id"] = ids6[1]
    row = module.t2(receipt, corpus)
    assert row["legs"]["entity_delta_zero"] is True, (
        "a net deletion was graded as an ADDITION — `delta <= 0` is not being read")
    assert row["verdict"] == "PASS", (
        "T2 fails a complete merge that nets a deletion — `adds 0 entities` means delta "
        "<= 0, the reading RULE_T1 already uses")


def test_t2s_candidate_band_has_a_falsifier():
    """T2's 0.45 candidate band is the one stated band nothing drove.

    Every committed candidate scores 1.00/0.62/0.71/0.67, so bands up to 0.62 move no
    number and `0.45 -> 0.55` survived a whole suite. The band is load-bearing in the
    fail-open direction: raising it drops candidates, and a dropped candidate is a dropped
    `unresolved` id — an easier PASS. This drives a text that clears 0.45 but not 0.55 and
    demands it stays a CANDIDATE, so raising the band reds.
    """
    module = _score_module()
    corpus = _corpus()
    fact = corpus["_declared_facts"]["goal"]

    # Half of the probe's content tokens are wording_a's and half are nonsense, so the
    # overlap is ~0.50 in every direction — a subset-only probe scores 1.00 and would not
    # exercise the band at all.
    a_tokens = sorted(module.tokens(fact["wording_a"]))
    half = max(1, len(a_tokens) // 2)
    probe = " ".join(a_tokens[:half] + [f"zqx{i}quux" for i in range(half)])
    score = module.overlap(probe, module.norm(fact["wording_a"]))
    assert 0.45 <= score < 0.55, (
        f"the probe scores {score:.2f}; it must sit in [0.45, 0.55) so the band is what "
        "decides whether it is a candidate")

    receipt = json.loads(json.dumps(_receipt()))
    doc3 = next(d for d in receipt["passes"][0]["documents"] if d["doc_id"] == "doc-03")
    (doc3.get("result") or {}).setdefault("points", []).append(
        {"point_id": "pt_band_probe", "kind": "statement",
         "dedup": "new", "text": probe})
    row = module.t2(receipt, corpus)
    assert "pt_band_probe" in [c["point_id"] for c in row["goal_point_candidates"]], (
        "a text scoring >= 0.45 was not a candidate — the band has been raised, which "
        "drops candidates and therefore drops `unresolved` ids (fail-open)")
