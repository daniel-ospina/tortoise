"""#14691 T6 — the structure bundle's claims pinned to its own receipts.

WHY THIS FILE EXISTS. The T6 bundle
(`docs/evidence/14691-structure-validation/`) reports five assertion verdicts and a
list of door findings. Prose has no compiler: nothing in the tree would redden if
a verdict drifted from the receipts it claims to come from, or if the corpus
fixture stopped describing the business the scorer measures it against. That is
exactly the #14606 review's lesson — *a REPORT whose claims are unguarded prose
reached review contradicted by its own `out/*.json`*.

Each test below names the mutation that reds it. Every one of them settles from
committed artifacts only: no DB, no docker, no LLM, no network. The bundle root is
resolved from this file, so a moved bundle reds loudly instead of pinning nothing.

⛔ Two of these pin a DEFECT (`ownedBy` inverted, `expand_relationships` silent on
structural edges). When either is FIXED, the test reds on purpose: the number in
`REPORT.md` then has to change with it, and a lane that changes one without the
other is the failure mode this file exists to prevent.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUNDLE = REPO / "docs" / "evidence" / "14691-structure-validation"


def _load_score_module():
    """Import the bundle's scorer by path (it is not a package module)."""
    path = BUNDLE / "score_t6.py"
    spec = importlib.util.spec_from_file_location("t6_score", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _corpus() -> dict:
    return json.loads((BUNDLE / "corpus" / "structure-corpus.json").read_text())


def _score() -> dict:
    return json.loads((BUNDLE / "out" / "t6-score.json").read_text())


def _readback() -> dict:
    return json.loads((BUNDLE / "out" / "readback.json").read_text())


def _receipt() -> dict:
    return json.loads((BUNDLE / "out" / "ingest-receipt.json").read_text())


def _rows(reply):
    """The three reply shapes the server uses (dict / row list / one JSON string)."""
    if isinstance(reply, str):
        try:
            return _rows(json.loads(reply))
        except (TypeError, ValueError):
            return []
    if isinstance(reply, dict):
        for key in ("result", "results", "relationships", "nodes", "items"):
            if isinstance(reply.get(key), list):
                return reply[key]
        return []
    if isinstance(reply, list):
        flat = []
        for item in reply:
            if isinstance(item, str):
                flat.extend(_rows(item))
            elif isinstance(item, dict):
                flat.append(item)
        return flat
    return []


def _text(doc: dict) -> str:
    return " ".join(t["content"] for t in doc["turns"]).lower()


def _report() -> str:
    return (BUNDLE / "REPORT.md").read_text()


# ── the corpus must describe the business the scorer scores it against ─────
def test_every_declared_structure_name_is_stated_in_the_corpus():
    """Reds if a name is added to `_declared_structure` that no document says.

    A fixture that declares an element the text never mentions makes A1's
    "28/28 present" a test of the fixture, not of the extractor.
    """
    corpus = _corpus()
    decl = corpus["_declared_structure"]
    blob = "\n".join(_text(d) for d in corpus["documents"])
    names = (decl["products"] + decl["features"] + decl["teams"]
             + decl["roles"] + decl["people"] + decl["components"]
             + decl["customers"] + decl["customerSegments"])
    missing = [n for n in names if n.lower() not in blob]
    assert not missing, f"declared but never stated in the corpus: {missing}"


def test_every_declared_pair_is_co_stated_in_one_document():
    """Reds if a declared containment/ownership/dependency pair is not in the text.

    Scored per DOCUMENT, not per sentence: the corpus legitimately connects two
    things across adjacent sentences ("Both customers are in the mid-market
    utilities segment, and Jonas Bauer owns enterprise renewals for both").
    """
    corpus = _corpus()
    decl = corpus["_declared_structure"]
    docs = [_text(d) for d in corpus["documents"]]
    pairs = (decl["containment"] + decl["membership"] + decl["holdsRole"]
             + decl["ownership"] + decl["dependencies"] + decl["serves"])
    bad = [(a, b) for a, b in pairs
           if not any(a.lower() in d and b.lower() in d for d in docs)]
    assert not bad, f"declared pairs the corpus never states: {bad}"


# ── the orientation rule itself, on synthetic rows ────────────────────────
def test_orientation_verdict_is_not_vacuous():
    """Reds if the rule inverts, or if it stops distinguishing the two orders.

    Built from synthetic rows so the check does not depend on this run's data
    happening to contain one of each.
    """
    mod = _load_score_module()
    verdict = mod.orientation_verdict
    # ownedBy / managedBy: the OWNER must be the dst end (§3.6 `->`).
    assert verdict("ownedBy", "Lumen Core", "Amara Osei", "Amara Osei") == "correct"
    assert verdict("ownedBy", "Amara Osei", "Lumen Core", "Amara Osei") == "INVERTED"
    assert verdict("managedBy", "Jonas Bauer", "Northwind", "Jonas Bauer") == (
        "INVERTED")
    # dependsOn: the DEPENDENCY must be the dst end (§8 `->`).
    assert verdict("dependsOn", "signal-engine", "fact-store", "fact-store") == (
        "correct")
    assert verdict("dependsOn", "fact-store", "signal-engine", "fact-store") == (
        "INVERTED")
    # hasPart `<->` and related `-` carry no direction to get wrong.
    assert verdict("hasPart", "Lumen Core", "Grid Map", "Grid Map") == "symmetric"
    assert verdict("related", "Dana Whitlock", "Platform team", None) == (
        "no-direction")


# ── the reported verdicts against the receipts they claim to come from ────
def test_a1_counts_match_the_typed_scans():
    """Reds if a declared element stops arriving under a kind A1 accepts."""
    score = _score()
    a1 = score["A1_type_coverage"]
    decl = _corpus()["_declared_structure"]
    assert a1["declared"] == a1["present"] == a1["right_kind"] == 28
    assert (len(decl["products"]) + len(decl["features"]) + len(decl["teams"])
            + len(decl["roles"]) + len(decl["people"]) + len(decl["components"])
            + len(decl["customers"]) + len(decl["customerSegments"])) == 28
    assert a1["per_type"]["product"]["right_kind"] == 2
    assert a1["per_type"]["feature"]["right_kind"] == 5
    assert a1["per_type"]["component"]["right_kind"] == 7
    # the caveat A1 carries: no shipped pack declares a component kind, so the
    # seven arrive under `dev:code` and the report must say so.
    assert a1["component_kind_declared"] is False
    assert "core:other" not in a1["accepted_kinds"]["component"]


def test_a2_ownership_is_present_and_inverted_in_the_committed_receipt():
    """Reds when the inversion is FIXED (then update REPORT.md §3/A2) or when an
    ownership edge stops being written at all."""
    a2 = _score()["A2_edges"]
    assert a2["totals"]["containment_present"].startswith("5/5")
    assert a2["totals"]["membership_present"].startswith("4/4")
    assert a2["totals"]["role_links_present"].startswith("4/4")
    assert a2["totals"]["team_to_role_edges"] == 0
    assert a2["totals"]["ownership_present"].startswith("4/4")
    assert a2["totals"]["ownership_oriented_correct"] == 0
    assert a2["totals"]["ownership_inverted"] == 4
    assert a2["totals"]["dependency_present_and_correct"].startswith("7/7")
    assert a2["passes"] is False

    # The inversion is a property of the RECEIPT, not of the scorer: every
    # `ownedBy` row the write door reported has a Subject as src and the owned
    # thing as dst.
    rows = [e for doc in _receipt()["documents"]
            for e in ((doc.get("result") or {}).get("relation_write") or {})
            .get("edges") or [] if e.get("predicate") == "ownedBy"]
    assert len(rows) == 6, f"expected 6 ownedBy rows, got {len(rows)}"


def test_a3_hierarchy_has_two_parents_and_five_children():
    """Reds if the product tree collapses or a feature loses its parent."""
    a3 = _score()["A3_hierarchy"]
    assert a3["reported"]["hasPart_distinct_pairs"] == 5
    assert a3["reported"]["distinct_parents"] == 2
    assert a3["reported"]["distinct_children"] == 5
    assert a3["reported"]["hasPart_write_rows"] == 8  # 3 rewritten by doc-05
    assert a3["flat_list"] is False
    assert a3["passes"] is True


def test_a4_declared_structure_is_traceable_and_relations_are_co_stated():
    """Reds if an entity name, or a relation's two endpoints, stops appearing in
    the corpus the extractor is scored against."""
    a4 = _score()["A4_no_invention"]
    assert a4["declared_structure_untraceable"] == []
    assert a4["relations_not_co_stated"] == []
    assert a4["passes"] is True
    # the CLAIM layer is reported separately and is NOT clean — pinning it keeps
    # a future reader from reading A4's PASS as covering the quotes.
    claim = _score()["claim_layer"]
    assert claim["quotes_total"] == 50
    assert claim["invented"] > 0
    assert claim["passes"] is False


def test_a5_reports_the_text_leg_and_the_structure_leg_separately():
    """Reds if A5 is reported as a clean PASS while no walk reaches an entity."""
    a5 = _score()["A5_retrieval"]
    assert a5["totals"]["answered_by_search_text"] == "5/5"
    assert a5["totals"]["walk_resolved_to_an_ENTITY"] == "0/5"
    assert a5["verdict"] == "PARTIAL"
    assert a5["passes_structure"] is False
    # "is the root an entity" is decided by a KIND, never by an id shape.
    for probe in a5["probes"]:
        assert probe["walk_root_is_entity"] is False


# ── the door findings ────────────────────────────────────────────────────
def test_expand_relationships_reads_a_connected_entity_as_isolated():
    """D2/D4: the door returns [] for a node `explore_connections` reaches.

    Reds if the expand door ever gains the structural predicates — at which
    point D2 must come out of REPORT.md and the orientation story improves.
    """
    witness = _readback()["link_witness"]
    silent = {name: w for name, w in witness.items()
              if not _rows(w["expand_relationships"]["reply"])
              and w["explore_neighbour_count"] > 0}
    # The witness list legitimately GROWS (every owner was added to it), so this
    # pins containment, not equality: the three nodes whose gap the finding was
    # first measured on must stay in it.
    assert {"Amara Osei", "Platform team", "event-bus"} <= set(silent)
    for name, w in silent.items():
        assert w["explore_neighbour_count"] >= 5, name


def test_bare_kind_full_scan_returns_nothing_while_namespaced_returns_rows():
    """D3: the silent zero a caller gets for the spelling the packs declare."""
    rb = _readback()
    bare = {k: _rows(v["reply"]) for k, v in rb["bare_kind_probes"].items()}
    assert all(len(v) == 0 for v in bare.values()), bare
    assert len(_rows(rb["object_kinds"]["product-strategy:product"]["reply"])) == 2
    assert len(_rows(rb["subject_kinds"]["core:team"]["reply"])) == 3


def test_report_headline_numbers_are_the_artifacts_numbers():
    """Reds if REPORT.md keeps a number the receipts no longer support."""
    report = _report()
    score = _score()
    tk = score["extraction_tokens"]
    assert f"{tk['total_tokens']:,}" in report, "token total drifted from the receipt"
    assert f"{tk['amplification_x']}×" in report
    for verdict in ("A1", "A2", "A3", "A4", "A5"):
        assert score["verdicts"][verdict] in report
    # the two headline counts A2/A3 turn on
    assert "0/6" in report and "7/7" in report
    # every door finding has a row in the report's table
    for finding in score["door_findings"]:
        assert finding["id"] in report, finding["id"]
