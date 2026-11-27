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

import ast
import contextlib
import hashlib
import importlib.util
import json
import re
from pathlib import Path

import pytest

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


def _measured(reply, what: str):
    """Rows from a reply that was actually ANSWERED — reds on an error envelope.

    ⛔ AN ERROR IS NOT AN EMPTY ANSWER, on the ARTIFACT too (#14691 review, 5th
    cycle, N1). `_rows()` maps every unrecognised shape to `[]`, which is right
    for unwrapping and wrong for a verdict: an MCP error payload inside a call's
    `reply` then reads exactly like a door that answered "nothing here".

    `test_an_ERROR_reply_is_not_an_EMPTY_answer` pins the SCORER's arm for D4 by
    replacing the whole call dict in-process. That left the committed artifact
    open: MEASURED BEFORE THIS ARM EXISTED (head 9ea2f606), rewriting both
    `bare_kind_probes` replies (and all seven witness
    `expand_relationships[*].reply` values) with `{"error": …, "ok": false}` —
    the exact shape the review used — kept the whole suite green, because nothing
    read those replies through this distinction. It reds now: this arm.
    """
    # ⛔ ONE IMPLEMENTATION, NOT TWO (#14691 review, round 5, P1). This helper
    # used to re-decide the error-vs-empty question itself, and it decided only the
    # `{"ok": false}`/`{"error": …}` shapes — so the producer's plain-text failure
    # reply read as an ANSWER here even after the scorer learned better. It now
    # asks the scorer, so a shape the scorer learns is a shape this arm sees.
    if _load_score_module().is_error_reply(reply):
        raise AssertionError(
            f"{what} is an error envelope, not a measured answer: {reply!r} — a "
            "door that FAILED must never grade the same as a door that answered "
            "nothing")
    return _rows(reply)


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

    # The write door reported SIX `ownedBy` rows. The scorer's totals above read them as
    # `ownership_oriented_correct` 0 and `ownership_inverted` 4 — all four ownership PAIRS are
    # inverted, and the artifact's `A2_edges.ownership[].verdicts[]` says which way: every row
    # carries the OWNER at `src` (`src_is_owner: true`, `src_kind: core:naturalPerson`) and the
    # owned thing at `dst`, so the arrow LEAVES the Subject instead of landing on one — the
    # reverse of the `(owned)-[:ownedBy]->(Subject)` the door reads. (Six edges over four pairs,
    # which is why the report's `0/6` and this arm's `4` are both right: different units, and the
    # report's §A2 note says so.)
    # ⛔ THIS ARM GRADES THE POPULATION, AND SAYS SO (#14723 review, P3). The receipt's rows
    # carry endpoints as ids (`src_id`/`dst_id`) with no KIND, so the ORIENTATION cannot be
    # read off them here: it is graded by those two totals, which the scorer derives from the
    # readback's typed nodes. The comment that used to sit here claimed this arm checked "a
    # Subject as src", which nothing in it can see — a claim nothing grades, in the arm whose
    # job is to grade claims.
    rows = [e for doc in _receipt()["documents"]
            for e in ((doc.get("result") or {}).get("relation_write") or {})
            .get("edges") or [] if e.get("predicate") == "ownedBy"]
    assert len(rows) == 6, f"expected 6 ownedBy rows, got {len(rows)}"
    # …and the population is EDGES, not placeholders: both endpoints named, and four distinct
    # owners over four distinct owned things (so the six rows are not one row repeated).
    assert all(r.get("src_id") and r.get("dst_id") for r in rows), rows
    assert len({r["src_id"] for r in rows}) == 4, rows
    assert len({r["dst_id"] for r in rows}) == 4, rows


def test_every_owner_was_actually_ASKED_the_ownership_door():
    """D6 must rest on a measurement, not on an absent probe.

    #14691 review, 3rd cycle: the emitter treated a MISSING `governance_owned_by`
    as a measured `[]` (`.get(...) in ([], None)`), so D6 — the bundle's headline
    mechanism finding — could be asserted from witness rows that never asked the
    door. Driven pre-fix: deleting the probe from every row still emitted D6 with
    all four owners listed. The committed artifact was honest, which is exactly
    why nothing caught it: a fail-open needs a pin, not a lucky fixture.
    """
    seen = _readback()["link_witness"]
    owners = {"Amara Osei", "Dana Whitlock", "Tomas Ferreira", "Jonas Bauer"}
    asked = {n: w for n, w in seen.items() if n in owners}
    assert asked, "the readback carries no ownership-door witness at all"
    for name, w in asked.items():
        probe = w.get("governance_owned_by")
        assert isinstance(probe, dict) and "reply" in probe, (
            f"{name}'s witness has no governance-door probe — D6 would report an "
            "answer for a question that was never asked")
        # the door's empty answer is `[]` here (the MCP envelope's result list),
        # and this asserts EMPTINESS rather than one spelling of it
        assert probe["reply"] in ([], {"result": []}), (
            f"{name}'s governance door answered {probe['reply']!r}, not the "
            "empty result D6 asserts")
    a2 = _score()["A2_edges"]["ownership_door"]
    assert a2["owners_not_asked"] == [], a2["owners_not_asked"]
    assert set(a2["owners_asked"]) == owners, a2["owners_asked"]
    assert a2["total_rows"] == 0
    # ⛔ AND THE FINDING ITSELF MUST NOT VANISH (#14691 review, 4th cycle).
    # Every arm above reads the witness; none of them notices D6 DISAPPEARING from
    # the artifact, which is what a shape change alone used to do (bare `[]` →
    # `{"result": []}` dropped it while the suite stayed green). The id set is
    # pinned whole, so a finding that stops being emitted reds here instead of
    # leaving REPORT.md §4 asserting a row the artifact no longer carries.
    ids = [f["id"] for f in _score()["door_findings"]]
    assert "D6" in ids, (
        f"D6 vanished from the artifact's door findings: {ids}")
    assert set(ids) == {"D1", "D2", "D3", "D4", "D5", "D6"}, ids


def test_a3_hierarchy_has_two_parents_and_five_children():
    """Reds if the product tree collapses or a feature loses its parent."""
    a3 = _score()["A3_hierarchy"]
    assert a3["reported"]["hasPart_distinct_pairs"] == 5
    assert a3["reported"]["distinct_parents"] == 2
    assert a3["reported"]["distinct_children"] == 5
    assert a3["reported"]["hasPart_write_rows"] == 8  # 3 rewritten by doc-05
    assert a3["flat_list"] is False
    assert a3["passes"] is True


def test_a3_reds_when_the_two_products_collapse_into_one_node():
    """F1 (#14691 review): A3 must FAIL on the collapse it exists to catch.

    THE MUTANT: every declared product becomes ONE node named `Lumen` — the
    exact failure A3 names. It PASSED before the fix, because `_same_name`
    matched in EITHER direction, so `Lumen` matched both `Lumen Core` and
    `Lumen Field`; `distinct` still equalled `len(DECL["containment"])` because
    the five features kept their single (collapsed) parent. A check that cannot
    red on its own subject is not a check.

    The scorer is driven IN PROCESS with its `EDGES` rewritten, so this pins the
    verdict function itself rather than the committed artifact — a future reader
    changing `_same_name` back to two-directional reds here instead of silently
    restoring the fail-open.
    """
    module = _load_score_module()
    assert module.a3_hierarchy()["passes"] is True, "baseline is not clean"
    products = {module._norm(p) for p in module.DECL["products"]}
    for edge in module.EDGES:
        if edge["predicate"] == "hasPart" and module._norm(edge["src"]) in products:
            edge["src"] = "Lumen"
    collapsed = module.a3_hierarchy()
    assert collapsed["reported"]["distinct_parents"] == 1, collapsed["reported"]
    assert collapsed["passes"] is False, (
        "A3 still PASSES on a graph holding the two declared products as one "
        "node — the prefix arm is tolerating a collapse again")


def test_a4_declared_structure_is_traceable_and_relations_are_co_stated():
    """Reds if an entity name, or a relation's two endpoints, stops appearing in
    the corpus the extractor is scored against."""
    a4 = _score()["A4_no_invention"]
    assert a4["declared_structure_untraceable"] == []
    assert a4["structural_kind_untraceable"] == []
    assert a4["relations_not_co_stated"] == []
    assert a4["passes"] is True
    # the two untraceable names the bundle carries are KNOWLEDGE-LAYER objects,
    # not structure elements — A4's scope is stated by its own counts, so a
    # reader never has to guess which population the PASS covers.
    # ⛔ THE VALUE, not `>= 0` (#14691 review, 3rd cycle, F3): `len()` is never
    # negative, so the old arm was satisfied by 0, 2 and 1e9 alike — it recorded
    # nothing in a file whose docstring promises each test names the mutation
    # that reds it. Pinning the measured 2 means the count this bundle STATES can
    # no longer drift (a new knowledge-layer object moving it to 3 is exactly the
    # drift the neighbouring `structural_kinds == 0` arm cannot see).
    assert a4["untraceable_counts"]["all_entity_names"] == 2, (
        a4["untraceable_counts"], a4["untraceable_entities"])
    # the CLAIM layer is reported separately and is NOT clean — pinning it keeps
    # a future reader from reading A4's PASS as covering the quotes.
    claim = _score()["claim_layer"]
    assert claim["quotes_total"] == 50
    assert claim["invented"] > 0
    assert claim["passes"] is False


def test_a4_reds_for_an_invented_entity_the_declared_list_does_not_name():
    """F5 (#14691 review): A4 must FAIL on an invention, not only on drift.

    THE MUTANT: a fabricated `Lumen Cloud` is added to the readback as a real
    `product-strategy:product` node. It PASSED before the fix, and structurally
    so: the pre-fix verdict was `not structural_misses`, where
    `structural_misses` was filtered to names matching a DECLARED element — and
    an invented name is by definition not one, so the list stayed empty and the
    "nothing invented" verdict could not fail on an invention.

    The fix scopes the population by KIND (the structure kinds A1 already scores
    against), so an invented product reds while a knowledge-layer object does not.
    """
    module = _load_score_module()
    assert module.a4_invention()["passes"] is True, "baseline is not clean"
    block = module.READBACK["object_kinds"]["product-strategy:product"]
    inner = json.loads(block["reply"][0])
    inner.append({"id": "1a2165fake000-c49e1db3abb3", "content": "Lumen Cloud",
                  "point_kind": "product-strategy:product",
                  "match_source": "structural", "relationships": [],
                  "topics": [], "summary": "", "sessionId": "", "eventId": "",
                  "sourcePath": "", "similarity": 1.0, "snippet": "Lumen Cloud"})
    block["reply"][0] = json.dumps(inner)
    mutated = module.a4_invention()
    assert [u["name"] for u in mutated["structural_kind_untraceable"]] == ["Lumen Cloud"]
    # it is NOT in the declared list — that is the whole point of this mutant
    assert mutated["declared_structure_untraceable"] == []
    assert mutated["passes"] is False, (
        "A4 still PASSES on an invented product the declared list does not "
        "name — the verdict is scoped to declared names again")


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
              if not _measured(w["expand_relationships"]["reply"],
                               f"{name}'s expand_relationships reply")
              and w["explore_neighbour_count"] > 0}
    # The witness list legitimately GROWS (every owner was added to it), so this
    # pins containment, not equality: the three nodes whose gap the finding was
    # first measured on must stay in it.
    assert {"Amara Osei", "Platform team", "event-bus"} <= set(silent)
    for name, w in silent.items():
        assert w["explore_neighbour_count"] >= 5, name
    # ...and no witness may carry a FAILED call in place of an empty answer: the
    # silence above has to be a measured silence.
    for name, w in witness.items():
        assert "expand_relationships" in w, name
        _measured(w["expand_relationships"].get("reply"),
                  f"{name}'s expand_relationships reply")


def test_bare_kind_full_scan_returns_nothing_while_namespaced_returns_rows():
    """D3: the silent zero a caller gets for the spelling the packs declare.

    Every reply read here must be a MEASURED answer (#14691 review, 5th cycle,
    N1): rewriting both probes with `{"error": …, "ok": false}` kept this suite
    green while D3 read an identical `[]`.
    """
    rb = _readback()
    probes = rb["bare_kind_probes"]
    assert sorted(probes) == ["object:product", "subject:team"], sorted(probes)
    bare = {k: _measured(v["reply"], f"bare-kind probe {k}")
            for k, v in probes.items()}
    assert all(len(v) == 0 for v in bare.values()), bare
    assert len(_measured(rb["object_kinds"]["product-strategy:product"]["reply"],
                         "the namespaced product scan")) == 2
    assert len(_measured(rb["subject_kinds"]["core:team"]["reply"],
                         "the namespaced team scan")) == 3


def _report_line(pattern: str) -> str:
    """The first REPORT.md line matching `pattern`, or "" — so a pin can name the
    LINE it grades instead of asking whether a token occurs anywhere in the file."""
    for line in _report().splitlines():
        if re.search(pattern, line):
            return line
    return ""


def _report_table_row(pattern: str) -> str:
    """The ONE REPORT.md table row matching `pattern`, flattened — the CELL that owns
    the figure, never the document.

    ⛔ A `literal in flat` pin is satisfied by ANY copy of the literal, and §8 is a
    review-round log that QUOTES every figure it reports on — so the log graded the
    cell it describes, and the cell could drift free (#14691 review, round 15, found
    at `c76156f`): mutating the D4 cell's `**0 rows** each`, the D2 cell's
    `false on 5/5`, or §3/A2 finding 5's `(0 edges)` left the owning arm GREEN,
    because §8.17 re-states all three.
    """
    hits = [ln for ln in _report().splitlines() if re.search(pattern, ln)]
    assert len(hits) == 1, (pattern, len(hits))
    return " ".join(hits[0].split())


def _report_paragraph(pattern: str) -> str:
    """The whole markdown paragraph whose FIRST line matches `pattern`, joined.

    A paragraph is where a claim lives: the A2 tally states its numerator on one
    wrapped line and its denominator on the next, so a line-scoped read returns a
    sentence fragment and grades half a claim.
    """
    lines = _report().splitlines()
    for start, line in enumerate(lines):
        if re.search(pattern, line):
            block = []
            for nxt in lines[start:]:
                if not nxt.strip():
                    break
                block.append(nxt)
            return " ".join(block)
    return ""


_UNITS = ("zero", "one", "two", "three", "four", "five", "six", "seven",
          "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen",
          "fifteen", "sixteen", "seventeen", "eighteen", "nineteen")
_TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
         "eighty", "ninety")


def _spell(n: int) -> str:
    """`n` as an English word (0-99) — the form prose uses when it counts."""
    if n < len(_UNITS):
        return _UNITS[n]
    if n < 100:
        tens, unit = divmod(n, 10)
        return _TENS[tens] if unit == 0 else f"{_TENS[tens]}-{_UNITS[unit]}"
    return str(n)


def test_report_headline_numbers_are_the_artifacts_numbers():
    """Reds if REPORT.md keeps a number the receipts no longer support.

    ⛔ THE PINS ARE LINE-SCOPED ON PURPOSE (#14691 review, 2nd cycle, P1). This
    test used to assert `score["verdicts"][v] in report` and `"0/6" in report` —
    bare TOKEN presence. Measured on the pre-fix version of this file: flipping
    REPORT's A3 heading to `FAIL`, flipping the `produces` row to `7/7 — a clean
    pass`, and changing the turn count all left **13 passed**, because `PASS`
    occurs in four other headings and `7/7` is also `dependsOn`'s number. A pin
    that cannot red on the drift it names is the class this bundle exists to
    catch, so each assertion below now names the LINE (a heading for a verdict, a
    table row for a count) and the artifact field it must equal.
    """
    report = _report()
    score = _score()
    tk = score["extraction_tokens"]
    assert f"{tk['total_tokens']:,}" in report, "token total drifted from the receipt"
    assert f"{tk['amplification_x']}×" in report

    # (a) each verdict, pinned to its OWN heading — the heading must carry this
    # assertion's letter AND the artifact's verdict token after the em dash.
    for assertion, verdict in score["verdicts"].items():
        head = _report_line(rf"^#{{2,4}} {assertion} · ")
        assert head, f"REPORT.md has no `{assertion} ·` heading"
        assert f"**{verdict}" in head, (
            f"REPORT.md's {assertion} heading does not state the artifact's "
            f"verdict `{verdict}`: {head!r}")

    # (b) the two counts the A2/A3 verdicts turn on, pinned to their OWN rows.
    owned = _report_line(r"^\| thing → owner \(`ownedBy`\)")
    assert owned, "REPORT.md has no ownedBy row"
    assert "0/6" in owned and "INVERTED" in owned, owned
    produces = _report_line(r"^\| team → component \(`produces`\)")
    assert produces, "REPORT.md has no produces row"
    assert "0/7" in produces and "INVERTED" in produces, produces
    depends = _report_line(r"^\| dependent → dependency \(`dependsOn`\)")
    assert depends, "REPORT.md has no dependsOn row"
    # ⛔ BOTH CELLS (#14691, 4th fresh-context review, NEW-1e). The row states the
    # number TWICE — present, and correctly oriented — and `"7/7" in row` read only
    # one: `| **7/7** | **9/9** |` was measured GREEN, so the orientation half of
    # the one predicate A2 passes could be rewritten freely.
    dep_total = score["A2_edges"]["totals"]["dependency_present_and_correct"]
    assert depends.count(f"**{dep_total}**") == 2, (
        f"REPORT.md's dependsOn row no longer states {dep_total!r} in BOTH cells: "
        f"{depends!r}")

    # (b2) the A2 TALLY paragraph — the run's own arithmetic, pinned against the
    # receipt that emits it (#14691 review, 3rd cycle, F2). The paragraph once said
    # "seven of the TWENTY correctly aimed rows … the other THIRTEEN", mixing rows
    # (22) with pairs (13) in the very sentence that warns they are different units,
    # and no arm above could see it: `0/6` and `7/7` are table rows, not this
    # paragraph. Each number here is read out of `A2_edges.totals`, so a future
    # edit to the prose reds rather than shipping a figure its own artifact refutes.
    totals = score["A2_edges"]["totals"]
    correct = totals["asymmetric_rows_correct"]
    inverted = totals["asymmetric_rows_inverted"]
    pairs = totals["asymmetric_pairs_inverted"]
    rows = correct + inverted
    tally = _report_paragraph(r"^\*\*One line that counts it\.\*\*")
    assert tally, "REPORT.md has no A2 tally paragraph"
    assert f"the **{rows} asymmetric structural rows**" in tally, tally
    assert (f"**{correct} assert what the corpus says and {inverted} assert the "
            f"reverse**") in tally, tally
    assert f"**{pairs} distinct pairs**" in tally, tally
    # the three inverted classes the paragraph enumerates must SUM to the inverted
    # rows — the arithmetic that caught the mixed units.
    enumerated = [int(a or b) for a, b in
                  re.findall(r"\((\d+) rows?\)|\((\d+)\)", tally)]
    assert sum(enumerated) == inverted, (tally, enumerated)
    # ⛔ AND THE SPELLED FORMS — the observed defect was a WORD contradicting the
    # digits beside it. The pin is TARGETED, not a set-membership sweep, because
    # "thirteen" is a number this artifact legitimately emits (13 distinct pairs):
    # a sweep allows it and stays green over exactly the sentence it was written
    # for (measured — the mutant `the other thirteen` passed a sweep arm). So pin
    # the phrase itself.
    other = re.search(r"the other ([a-z]+(?:-[a-z]+)?)", tally)
    assert other, tally
    assert other.group(1) == _spell(inverted), (
        f"the A2 tally says 'the other {other.group(1)}' while the artifact's "
        f"inverted row count is {inverted} ('{_spell(inverted)}') — the paragraph "
        f"mixed rows with distinct pairs in the sentence that warns they differ")
    # ...and no OTHER spelled count may contradict the artifact either.
    allowed = {_spell(v) for v in (correct, inverted, pairs, rows)}
    # the lead-in is stripped EXPLICITLY rather than by splitting on the first
    # period: "One" is not a count, and a dropped period would otherwise turn a
    # legitimate edit into a red here (fail-closed on the wrong thing).
    body = re.sub(r"^\*\*One line that counts it\.?\*\*", "", tally).lower()
    mentioned = set(re.findall(
        r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
        r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|"
        r"twenty(?:-[a-z]+)?|thirty(?:-[a-z]+)?)\b", body))
    assert mentioned <= allowed, (
        f"the A2 tally names a count its own artifact does not support: "
        f"{sorted(mentioned - allowed)} (artifact: rows={rows}, correct={correct}, "
        f"inverted={inverted}, distinct pairs={pairs})")

    # (c) the corpus's turn count — 30, which the fixture, the receipt and
    # VERIFICATION-independent.md all state; the report said 32 once.
    turns = sum(len(d["turns"]) for d in _corpus()["documents"])
    assert f"(5 documents, {turns} turns)" in report, (
        f"REPORT.md does not state the corpus's own turn count ({turns})")

    # (d) every door finding is pinned to its ARTIFACT QUESTION, not to its id:
    # ids D2/D4/D5 were once permuted against the artifact and a bare-token pin
    # could not see it. The row's question text must match the artifact's, compared
    # after stripping the markdown noise that differs legitimately (`src→dst` in the
    # artifact, `` `src → dst` `` in the report).
    def _norm_q(text: str) -> str:
        return " ".join(re.sub(r"[^a-z0-9 ]+", " ", text.lower()).split())

    for finding in score["door_findings"]:
        row = _report_line(rf"^\| \*\*{finding['id']}\*\* \|")
        assert row, f"REPORT.md has no row for door finding {finding['id']}"
        row_q = _norm_q(row.split("|")[2])
        art_q = _norm_q(finding["question"])
        # compare on the artifact's own first clause, tolerant of the report's
        # editorial rewording of the tail
        head = art_q.split(" ", 6)
        key = " ".join(head[:6])
        assert key in row_q, (
            f"door finding {finding['id']} in REPORT.md asks a different question "
            f"than the artifact's: report row {row_q[:120]!r} vs artifact {art_q[:120]!r}")


def test_readback_provenance_binds_to_its_producer_or_the_stated_exception():
    """Reds if `out/readback.json` names a producer hash that is neither the
    committed script's nor the documented producing commit's.

    The artifact carries `provenance.probe_sha256` (written by
    `run_readback.py`), so it is the one artifact in the bundle bound to the code
    that made it. The `agent-infra-ci / lint` commit renamed an unused loop
    variable in that script, which moved the hash without touching a byte of
    behaviour — verified mechanically by renaming it back and diffing against
    `d4fc8fe` (identical). Rather than regenerate an 809,812-byte receipt to fix a
    comment-sized defect, the exception is STATED in REPORT.md §8 and pinned
    here, so a future drift reds instead of going unnoticed.
    """
    claimed = (_readback().get("provenance") or {}).get("probe_sha256")
    assert claimed, "readback.json carries no provenance binding at all"
    script = (BUNDLE / "run_readback.py")
    actual = hashlib.sha256(script.read_bytes()).hexdigest()
    produced_by = (
        "5a441859d7f953a91d493d9606b8c8404018a40eac35b658a75e4fbe6200f192")
    assert claimed in (actual, produced_by), (
        "readback.json's probe_sha256 is neither the committed script's hash nor "
        f"the documented producing-commit hash: {claimed}")
    assert produced_by in _report(), (
        "REPORT.md §8 no longer states the producing-commit hash the artifact "
        "actually carries")
    # ⛔ THE EXCEPTION IS PINNED AS A DELTA, NOT ACCEPTED AS A LITERAL (#14691
    # review, 3rd cycle). `claimed in (actual, produced_by)` passes on the
    # literal alone, so `run_readback.py` could be edited to anything and the
    # suite stayed green (measured: a producer edit moved the hash and 16 tests
    # still passed) while REPORT §8's "the file in this tree hashes 19f8ed1a…"
    # became false — the next instance of the very drift §8 exists to disclose.
    # Two arms close it: the documented delta must REPRODUCE `produced_by` when
    # reverted (so any edit other than that one rename reds), and §8's stated
    # CURRENT hash must be the file's real hash.
    # ⛔ FOUR EDITS, STATED AS FOUR (#14691 review, round 6, P1; the fourth is
    # #14723's rebase repair). The producer
    # gained two error-vs-empty guards after the producing commit — `_call()` now
    # keeps the MCP `isError` flag, and the D4 emitter refuses a FAILED witness —
    # so the delta is no longer the rename alone. Each removal below must SUCCEED,
    # which is what keeps this a binding: an edit to either guard makes its removal
    # a no-op and the reverted hash then differs from `produced_by`.
    def _reverted_to_producing_commit(text: str) -> str:
        text = text.replace("_element_kind", "element_kind")
        # ⛔ THE FOURTH EDIT — COMMENT-ONLY, and it removes a claim the tree denied
        # (#14723's rebase repair). The D6 comment cited the ownership read as
        # `(sdk.py:62739-62758)`; `origin/main`'s creation hook inserted 23 lines into
        # `tortoise/sdk.py` above `_get_owned_entities`, so that range no longer opens
        # on the `def` — a citation the tree contradicts, which is the class the
        # citation arms in this file exist to catch (they caught this one). It now
        # cites the SYMBOL. One substitution reverts it, and it is asserted present
        # first so a later reflow cannot silently drop it from the delta.
        cited_by_symbol = (
            "            # `MATCH (s:Subject)<-[:ownedBy]-(e)` — cited by SYMBOL in "
            "`sdk.py`,\n"
            "            # not by line range (this one moved 23 lines on a rebase) — "
            "is the\n")
        assert cited_by_symbol in text, (
            "the documented D6 SYMBOL citation is gone from run_readback.py, so the "
            "delta below no longer describes the file it binds")
        text = text.replace(
            cited_by_symbol,
            "            # `MATCH (s:Subject)<-[:ownedBy]-(e)` "
            "(sdk.py:62739-62758) — the\n", 1)
        blocks = [
            re.compile(r'(?s) *# ⛔ THE MCP ERROR FLAG LIVES ON THE RESULT.*?'
                       r'\n *"error": f"MCP isError: \{out!r\}"\[:400\]\}\n'),
            re.compile(r"(?s)\n    # ⛔ A FAILED CALL IS NOT A SILENT DOOR.*?"
                       r"\n        from score_t6 import call_failed.*?"
                       r"\n        return not call_failed\(call or \{\}\)\n"),
        ]
        for pattern in blocks:
            text, count = pattern.subn("", text, count=1)
            assert count == 1, f"the documented guard is gone: {pattern.pattern[:60]}"
        text = text.replace(
            "              and _answered(w.get(\"expand_relationships\"))\n", "", 1)
        assert "_answered(" not in text.replace("def _answered", ""), (
            "a call to the documented guard survived the revert")
        return text

    reverted = _reverted_to_producing_commit(script.read_text())
    assert hashlib.sha256(reverted.encode()).hexdigest() == produced_by, (
        "run_readback.py no longer differs from the producing commit by ONLY the "
        "`element_kind` -> `_element_kind` rename plus the two documented guards, "
        "so REPORT.md §8's stated delta and the artifact's provenance binding no "
        "longer describe it")
    assert actual in _report(), (
        "REPORT.md §8 does not state the hash this tree's run_readback.py "
        f"actually has ({actual})")


def test_a4_reds_for_an_invented_RELATION_too():
    """F5b (#14691 review, 2nd cycle): A4's verdict must cover the relation axis.

    THE MUTANT: `Asset Register --dependsOn--> Jonas Bauer`, a pair no corpus
    document co-states (doc-01 names the component, doc-02 the person). Before
    this fix the verdict was computed from ENTITIES only, so
    `relations_not_co_stated` carried the row while `passes` stayed True — even
    though the report states "0 of 46 relations are unstated" under the same PASS
    heading, and #14691 names an invented edge the worse failure of the two.
    """
    module = _load_score_module()
    assert module.a4_invention()["passes"] is True, "baseline is not clean"
    module.EDGES.append({
        "doc": "doc-01", "predicate": "dependsOn", "write_path": "test_mutant",
        "src_id": "mutant-src", "dst_id": "mutant-dst",
        "src": "Asset Register", "dst": "Jonas Bauer",
        "src_kind": "dev:code", "dst_kind": "core:naturalPerson",
    })
    mutated = module.a4_invention()
    assert mutated["relations_not_co_stated"], (
        "the invented relation was not detected as un-co-stated at all")
    assert mutated["passes_entities"] is True
    assert mutated["passes_relations"] is False
    assert mutated["passes"] is False, (
        "A4 still PASSES on an invented relation — the verdict ignores the "
        "relation axis its own prose asserts")


def test_the_name_variance_retry_drops_a_KIND_WORD_not_the_FIRST_word():
    r"""Reds if the welded-name retry truncates every name to its first word.

    ⛔ #14723 review, round 10, P2. The retry was
    `_squash(str(e["src"]).split(" ")[0])`, so ANY invented relation whose
    endpoints' FIRST WORDS co-occur in one document was graded *stated*.
    MEASURED on the pre-fix code with
    `Lumen Field --dependsOn--> Dana Whitlock` appended to `EDGES` (no corpus
    document names both): `passes: True`, `relations_not_co_stated: []`. The
    full-name arm misses (the pair is never co-stated); `lumen` and `dana` both
    occur in doc-01, and the truncation is what let the row through.
    Post-fix the retry drops ONE trailing kind word (`ingest-gateway component`)
    and nothing else. ⛔ A CODE-side mutant on `_unweld` (→ identity) reds here at
    the BASELINE assertion above the contract block — the one whose message is
    `baseline is not clean` — because removing the retry flips `passes` to False
    before any contract row is reached (measured). The CONTRACT assertions below
    are pinned by a DIFFERENT mutant: the declared-name guard, whose failure message
    is `un-welding truncated a DECLARED name to a bare common noun` (measured).
    ⛔ Round 10's "the same mutant reds A4" claim named a row that never fires;
    naming the CONTRACT rows for the identity mutant was the SAME misattribution
    one level down, and is corrected here by measurement, not by rewording. The
    A4-level dependence on the
    retry is pinned where it can actually be measured:
    `test_the_name_variance_rows_are_RECORDED_and_LOAD_BEARING` removes the retry
    and asserts the verdict flips.
    """
    module = _load_score_module()
    assert module.a4_invention()["passes"] is True, "baseline is not clean"
    # the retry is a NAME variance: it un-welds a declared KIND noun ONLY
    assert module._unweld("ingest-gateway component") == "ingest-gateway"
    assert module._unweld("Lumen Core product") == "Lumen Core"
    assert module._unweld("Northwind Logistics") == "Northwind Logistics", (
        "a name whose last word is not a declared kind word must be untouched — "
        "dropping it is truncation, not un-welding")
    assert module._unweld("Lumen Field") == "Lumen Field"
    # ⛔ AND IT REFUSES A DECLARED NAME THAT *ENDS* IN ITS KIND WORD (#14723
    # review, round 10, P1). Every declared team is `<Something> team`, so a
    # kind-word test ALONE truncated a legitimate name to a bare common noun:
    # `Customer team` → `Customer`, which the corpus says on its own.
    assert module._unweld("Customer team") == "Customer team", (
        "un-welding truncated a DECLARED name to a bare common noun")
    assert module._unweld("Platform team") == "Platform team"
    assert module._unweld("Product team") == "Product team"
    assert module._unweld("Account Executive") == "Account Executive"
    module.EDGES.append({
        "doc": "doc-01", "predicate": "dependsOn", "write_path": "test_mutant",
        "src_id": "mutant-src", "dst_id": "mutant-dst",
        "src": "Lumen Field", "dst": "Dana Whitlock",
        "src_kind": "dev:code", "dst_kind": "core:naturalPerson",
    })
    mutated = module.a4_invention()
    assert mutated["relations_not_co_stated"], (
        "an invented relation whose endpoints' FIRST WORDS co-occur in one "
        "document is still graded stated — the retry is a truncation")
    assert mutated["passes_relations"] is False
    assert mutated["passes"] is False
    # …and the TEAM-shaped mutant is the one the review measured: the corpus
    # never relates either feature to the Customer team, so each row must be
    # reported as un-co-stated rather than granted by `Customer team → Customer`.
    module.EDGES.extend([
        {"doc": "doc-02", "predicate": "managedBy",
         "write_path": "test_mutant",
         "src_id": "mutant-team-1", "dst_id": "mutant-team-1-dst",
         "src": "Grid Map", "dst": "Customer team",
         "src_kind": "dev:code", "dst_kind": "core:team"},
        {"doc": "doc-02", "predicate": "managedBy",
         "write_path": "test_mutant",
         "src_id": "mutant-team-2", "dst_id": "mutant-team-2-dst",
         "src": "Asset Register", "dst": "Customer team",
         "src_kind": "dev:code", "dst_kind": "core:team"},
    ])
    team_mutant = module.a4_invention()
    flagged = {(e["src"], e["dst"])
               for e in team_mutant["relations_not_co_stated"]}
    assert flagged >= {("Grid Map", "Customer team"),
                       ("Asset Register", "Customer team")}, (
        "a fabricated edge to a DECLARED team cleared A4 on the un-weld retry")
    assert team_mutant["passes_relations"] is False


def test_the_kind_word_table_is_DERIVED_from_the_declared_groups():
    r"""Reds if `_KIND_WORDS` goes back to being hand-typed prose.

    ⛔ #14723 review, round 10, P3: the comment claimed the set was "derived from
    `_declared_structure`, never a hand-typed list" while the code was a literal
    tuple — and a naive derivation does NOT reproduce it (`people` → `person`,
    `customerSegments` → `customer segment` are not reachable by singularising a
    key), so the false claim was load-bearing for the P1's reasoning. The
    morphology table is now explicit, the set is READ FROM IT, and a declared
    group with no kind word raises instead of leaving a silent gap.
    """
    module = _load_score_module()
    assert set(module._KIND_OF_GROUP) == module._DECLARED_GROUPS, (
        module._DECLARED_GROUPS, set(module._KIND_OF_GROUP))
    assert {module._squash(kind)
            for kind in module._KIND_OF_GROUP.values()} == module._KIND_WORDS, (
        "the kind-word set is not the derivation of the table it claims to be")
    assert module._KIND_WORDS, module._KIND_WORDS
    with pytest.raises(ValueError, match="no kind word"):
        module._kind_words({**module.DECL, "initiatives": ["Growth"]})


def test_the_summary_truncation_flag_is_the_ONE_the_consumer_reads():
    r"""Reds if the summary cap sets a flag name nothing reads.

    ⛔ #14723 review, round 10, P2: `quotes` initialises
    `summary_examples_truncated`, but the setter derived
    `f"{bucket}_examples_truncated"` and the bucket is `summary_in_one_turn` — so a
    capped summary sample set `summary_in_one_turn_examples_truncated` while the
    consumer was told the sample was COMPLETE. Invisible on this corpus (3
    summaries), so the cap is FORCED here: at `CAP = 0` every list truncates and
    the flag the artifact declares must be the one that fires.
    """
    module = _load_score_module()
    original = module.CAP
    module.CAP = 0
    try:
        quotes = module.a4_invention()["quotes"]
    finally:
        module.CAP = original
    assert quotes["summary_examples_truncated"] is True, (
        "the cap bit but the flag the artifact declares stayed False — the "
        "setter writes a name nothing reads")
    assert "summary_in_one_turn_examples_truncated" not in quotes, (
        "the setter invented a second flag name instead of declaring the one "
        "the consumer reads")
    assert quotes["paraphrase_examples_truncated"] is True
    assert quotes["invented_examples_truncated"] is True


def test_the_turn_support_SCOPE_is_recorded_beside_each_row():
    r"""Reds if `supported_outside_own_doc` stops being measurable.

    ⛔ #14723 review, round 10, P2: the decider searches EVERY corpus turn while a
    row records the `doc` it was attributed to, so a row supported only by another
    document read as "supported by its own turn". MEASURED: exactly one row does
    this — doc-04's `Enterprise renewals, owned by Jonas Bauer…`, 0.571 inside
    doc-04 and 0.714 corpus-wide on a doc-02 turn — and the artifact could not say
    so. Doc-scoping the decider would re-grade a fact the business DOES state as
    invented, so the scope is kept and RECORDED instead: the count, the sample,
    and every row's own-doc figure.
    """
    quotes = _score()["A4_no_invention"]["quotes"]
    # the artifact is a CLAIM: it must be what the scorer derives from the receipt
    derived = _load_score_module().a4_invention()["quotes"]
    assert (quotes["supported_outside_own_doc"]
            == derived["supported_outside_own_doc"]), (
        "the committed count is not the derivation's", quotes, derived)
    assert (quotes["supported_outside_own_doc_examples"]
            == derived["supported_outside_own_doc_examples"]), (
        "the committed sample is not the derivation's")
    rows = quotes["supported_outside_own_doc_examples"]
    assert quotes["supported_outside_own_doc"] == len(rows) == 1, (
        quotes["supported_outside_own_doc"], len(rows))
    row = rows[0]
    assert (row["doc"], row["best_own_doc_turn_overlap"]) == ("doc-04", 0.571)
    assert row["best_turn_overlap"] == 0.714, row
    assert "Enterprise renewals" in row["quote"], row["quote"]
    # the count is a POPULATION, not the length of a capped sample: both are
    # recorded, and the flag says whether the sample is complete
    assert len(rows) <= quotes["supported_outside_own_doc"], quotes
    assert quotes["supported_outside_own_doc_examples_truncated"] is False
    # every SUPPORTED row carries its own-doc figure, and only the one row needs
    # a different document to clear the band
    for bucket in ("paraphrase_examples", "summary_examples"):
        for entry in quotes[bucket]:
            assert "best_own_doc_turn_overlap" in entry, (bucket, entry)
            if (entry["doc"], entry["quote"]) == (row["doc"], row["quote"]):
                continue
            assert entry["best_own_doc_turn_overlap"] >= 0.6, (bucket, entry)


def test_a_finding_that_cannot_be_derived_is_RECORDED_as_WITHHELD():
    r"""Reds if D6 can vanish from `door_findings` with nothing saying why.

    ⛔ #14723 review, round 10, P3: D6 is emitted only when `len(asked_owners) >=
    2`, so with `DECL["people"] = []` it disappeared — `['D1','D2','D3','D4','D6',
    'D5']` → `['D1','D2','D3','D4','D5']` — and nothing recorded that the bundle's
    headline ownership finding had been withdrawn rather than never asked. A2
    states its own unasked population; the findings list now states its withheld
    ones the same way.
    """
    module = _load_score_module()
    assert _score()["withheld_findings"] == [], (
        "a committed run withheld a finding; the artifact must say which")
    original = module.DECL["people"]
    module.DECL["people"] = []
    try:
        ids = [f["id"] for f in module._door_findings()]
        withheld = module._withheld_findings()
    finally:
        module.DECL["people"] = original
    assert "D6" not in ids, ids
    assert [w["id"] for w in withheld] == ["D6"], withheld
    assert "population" in withheld[0]["reason"], withheld[0]
    assert withheld[0]["witnesses_answering_empty"], (
        "the door DID answer empty — the finding is withheld for lack of a "
        "populated question, not for lack of an answer")
    assert withheld[0]["declared_people"] == [], withheld[0]


def test_the_name_variance_rows_are_RECORDED_and_LOAD_BEARING():
    """Reds if the PRODUCER stops recording the retry's rows, or if the retry
    stops carrying A4.

    ⛔ #14723 review, round 10, P2: "the fallback is load-bearing too (1 of the 46
    committed edges, `ingest-gateway component → event-bus`, passes only through
    it)". A load-bearing allowance that leaves no row behind is invisible in the
    artifact — so the rows that need it are emitted, and the one committed edge
    that does is asserted by name rather than by count alone.

    ⛔ AND THE ROW MUST READ THE PRODUCER (round 10, mutation attribution P1).
    This arm used to read `out/t6-score.json` only, which is a COMMITTED receipt:
    deleting `name_variance.append(...)` from `a4_invention` left the artifact
    untouched, so the recording could be removed from the scorer with this row
    GREEN (MEASURED: `if stated: pass` in the producer → `1 passed`; the
    "load-bearing" half was a corpus name-presence check that survives the
    retry's removal too). It now derives from the LOADED scorer, and it removes
    the retry to prove the allowance carries the verdict — so the producer mutant
    reds at the ROW assertion (the first one below), and an `_unweld`-to-identity
    mutant reds THERE TOO, because dropping the retry drops the row as well as the
    verdict (measured). The `passes is False` assertion further down is pinned by
    its OWN mutant — a verdict that ignores `relations_not_co_stated` — which reds
    exactly there (measured). ⛔ An earlier revision of this paragraph said the
    identity mutant reds at `passes is False`; it does not, and that is the same
    mutation-attribution error this paragraph exists to correct, so it is stated
    here rather than quietly reworded. The committed artifact is still checked, as
    the receipt it is.
    """
    module = _load_score_module()
    live = module.a4_invention()
    assert [(r["src"], r["dst"]) for r in live["relation_name_variance"]] == [
        ("ingest-gateway component", "event-bus")], (
        "the PRODUCER records no row for the edge its retry rescued — a deleted "
        "`name_variance.append(...)` leaves the committed artifact unchanged")
    assert live["passes"] is True, "baseline A4 is not clean"
    assert not [e for e in live["relations_not_co_stated"]
                if e["src"] == "ingest-gateway component"], (
        "the retry's own edge is reported as invented")
    # ...and the RETRY is what carries it: without the un-weld, the same committed
    # edge is an invented relation and A4's claim flips.
    original_unweld = module._unweld
    try:
        module._unweld = lambda name: str(name or "")
        without = module.a4_invention()
    finally:
        module._unweld = original_unweld
    assert [(r["src"], r["dst"]) for r in without["relation_name_variance"]] == [], (
        "the retry records rows it did not need")
    assert [e for e in without["relations_not_co_stated"]
            if e["src"] == "ingest-gateway component"], (
        "the retry is not load-bearing — A4 grades this edge stated without it")
    assert without["passes"] is False, (
        "the retry is not load-bearing — A4's verdict does not move without it")
    # ...the committed RECEIPT records the same row, and it is still a fact the
    # corpus states: un-welding resolves BOTH ends
    assert [(r["src"], r["dst"]) for r in
            _score()["A4_no_invention"]["relation_name_variance"]] == [
        ("ingest-gateway component", "event-bus")], (
        "the committed receipt needs the retry but the artifact records none")
    corpus = json.loads((BUNDLE / "corpus" / "structure-corpus.json").read_text())
    text = " ".join(t["content"] for d in corpus["documents"] for t in d["turns"])
    flat = re.sub(r"[^a-z0-9 ]+", "", " ".join(text.lower().split()))
    assert "ingestgateway" in flat and "eventbus" in flat


def test_the_isolation_premise_is_recorded_as_an_EMPTY_graph():
    """Reds if `empty_before` stops being an empty graph.

    Every A1/A2/A3 number rests on the fresh-graph premise; the orchestrator now
    REFUSES a populated graph (`run_ingest.py`), and this pins the receipt the
    committed bundle carries rather than the producer's promise.
    """
    before = _receipt()["empty_before"]
    assert {k: v for k, v in before["taxonomy"].items() if v} == {}, before["taxonomy"]
    assert (before.get("structure") or {}).get("total") == 0, before.get("structure")


# ── the pin must actually RUN on the diff it guards ──────────────────────
def test_a_bundle_only_diff_selects_this_suite():
    """#14691 review (3rd cycle) F1: the guard must run on ITS OWN SUBJECT.

    This file's subject is the committed bundle — `out/*.json`, `REPORT.md`, the
    corpus, the scorers. `docs/` is a flat `NON_PYTHON_PREFIXES` entry, so before
    the `CORE_ALSO` row every one of those diffs took the docs-only early return:
    MEASURED on the pre-fix manifest,
    `select(['docs/evidence/14691-structure-validation/out/t6-score.json'],
    'pull_request')` -> `surfaces: []`, and the same for `REPORT.md`,
    `score_t6.py`, `run_readback.py` and `corpus/structure-corpus.json`. Every
    falsifier in this file therefore ran when someone edited the TEST and NEVER
    when someone edited the artifacts they pin — so a PR that hand-rewrote the
    score artifact or restated a headline number shipped with the guard absent.

    ⛔ NO COUNT IS STATED HERE ANY MORE (#14691 review, 5th cycle, N8). This
    docstring said "the 13 falsifiers" while the file carried 18, and the sibling
    manifest comment said 11 — three numbers for one file, which is how a reader
    learns to trust a stale figure. The suite's size is `grep -c '^def test_'`.

    ⛔ THE SUBJECT IS A PREFIX, and the assertion walks EVERY file the bundle
    commits rather than one representative: a new artifact added to the bundle is
    the same class again, and a single-file pin would let it in silently.
    """
    from tools import ci_selection

    manifest = ci_selection.load_manifest()
    subjects = sorted(
        p.relative_to(REPO).as_posix() for p in BUNDLE.rglob("*")
        if p.is_file() and ".git" not in p.parts)
    # non-vacuity: the walk must actually find the bundle's committed files
    assert {"docs/evidence/14691-structure-validation/REPORT.md",
            "docs/evidence/14691-structure-validation/out/t6-score.json",
            "docs/evidence/14691-structure-validation/corpus/structure-corpus.json",
            } <= set(subjects), subjects
    blind = []
    for path in subjects:
        selection = ci_selection.select([path], "pull_request", manifest)
        files = selection.get("test_files")
        selected = files == "ALL" or "test_14691_structure_evidence.py" in (files or [])
        if "core" not in (selection.get("surfaces") or []) or not selected:
            blind.append((path, selection.get("surfaces")))
    assert not blind, (
        "a diff to the bundle no longer selects its own pin, so the guard is "
        f"absent on the one change class it exists for: {blind}. The `CORE_ALSO` "
        "row in `tools/ci_selection.py` was dropped (a `SOURCE_PATTERNS['core']` "
        "row cannot replace it — `core` is skipped by both `_selection_relevant` "
        "and the match loop)")

    # ... and the row must stay a CLAIM about this bundle, not about `docs/`
    # (a widening nobody would see in the assertion above).
    for sibling in ("docs/evidence/14606-decision-validation/REPORT.md",
                    "docs/evidence/14691-structure-validation-extra/x.json"):
        assert ci_selection.select([sibling], "pull_request", manifest)["surfaces"] == [], (
            f"the T6 `CORE_ALSO` row widened to a sibling bundle: {sibling}")


# ── the artifacts, the prose, and their derivations are ONE artifact ──────
# The 5th review cycle's findings were one class: the suite pinned what the TEST
# believed and never the edges between the committed artifacts, the prose that
# quotes them, and the inputs they are derived from. Each arm below names the
# mutation it was measured against.
def _norm_key(text: str) -> str:
    """A name normalised for set comparison (case, whitespace, markdown noise)."""
    return " ".join(str(text or "").lower().split())


def _norm_words(text: str) -> str:
    """Prose reduced to its WORD SEQUENCE — markdown and punctuation removed."""
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", str(text).lower()).split())


def test_the_quote_audit_artifact_is_re_derived_from_the_receipt():
    """Reds if `out/t6-quote-audit.json` drifts from the receipt it summarises.

    #14691 review (5th cycle, N2): nothing in the tree opened this file. Measured
    before this arm — `verbatim 11 -> 50`, `not_in_corpus 12 -> 0` rewrite →
    **18/18 green**, so the PR's quote-fidelity claim and this artifact could
    disagree forever. `score_t6.a4_invention()` re-derives the same buckets from
    `out/ingest-receipt.json` + the corpus, so the artifact is now a CLAIM.
    """
    audit = json.loads((BUNDLE / "out" / "t6-quote-audit.json").read_text())
    quotes = _load_score_module().a4_invention()["quotes"]
    assert quotes["total"] == audit["quotes_total"], (quotes["total"], audit["quotes_total"])
    assert quotes["verbatim"] == audit["verbatim"], (quotes["verbatim"], audit["verbatim"])
    assert quotes["paraphrase"] == audit["paraphrase"], (quotes["paraphrase"], audit["paraphrase"])
    # ⛔ THE BUCKET THE PR BODY READ, and the reason it read the wrong number:
    # `not_in_corpus` used to be a 10-row list of the OPPOSITE bucket, so its
    # LENGTH (the display cap) was read as a population. It is a count now, and it
    # must equal the invented arm the same scorer reports.
    assert quotes["below_sentence_band"] == audit["not_in_corpus"], (
        quotes["below_sentence_band"], audit["not_in_corpus"])
    # ⛔ THE AUDIT'S POPULATION IS SENTENCE-SCOPED, AND THAT IS NOT THE INVENTED ONE
    # (#14723 review, round 10, P2). `not_in_corpus` used to be asserted equal to
    # `invented` because both were the single-sentence bucket. A faithful multi-fact
    # summary cannot clear a single-sentence band, so the decider is now the TURN and
    # the two populations differ by exactly the summaries: 12 = 9 invented + 3
    # summaries. The audit artifact is untouched — it measured what it measured.
    assert "not_in_corpus" not in quotes, (
        "the artifact still carries `not_in_corpus` — a field whose NAME asserts "
        "`the corpus never says it` about a population that includes 3 rows it does")
    assert (quotes["invented"] + quotes["summary_in_one_turn"]
            == quotes["below_sentence_band"]), (
        "the below-sentence-band population is not (invented + summary)")
    assert (audit["quotes_total"]
            == audit["verbatim"] + audit["paraphrase"] + audit["not_in_corpus"])
    # The `audited` rows are a SAMPLE, not a population, and the artifact does not
    # declare its sampling rule — so this pins what the artifact DOES declare:
    # every listed row is non-verbatim (`verbatim_in_corpus: false`, its own
    # field), the overlaps are shares, and the sample cannot exceed the
    # non-verbatim population it is drawn from. Asserting `overlap >= 0.6` would
    # pin a method the peer never stated and would false-red on a legitimate
    # re-audit that sampled the invented band instead.
    assert audit["audited"], "the audit shipped no rows at all"
    for row in audit["audited"]:
        assert row["verbatim_in_corpus"] is False, row
        assert 0.0 <= row["overlap"] <= 1.0, row
    non_verbatim = audit["quotes_total"] - audit["verbatim"]
    assert len(audit["audited"]) <= non_verbatim, (
        "the audit lists more rows than the non-verbatim population holds",
        len(audit["audited"]), non_verbatim)
    # ⛔ AND THE ROWS MUST BE ROWS THE RUN ACTUALLY PRODUCED (#14691 review, 5th
    # cycle, NEW-4). Shape-checking a sample is not checking it: with only the
    # assertions above, `audited[0] = {"quote": "**FABRICATED** text the corpus
    # never contains", "overlap": 0.64, …}` stayed GREEN (measured). The scorer
    # already derives both populations on this run, so membership is available
    # without trusting the peer's prose.
    derived = [_norm_words(e["quote"])
               for e in quotes["paraphrase_examples"] + quotes["summary_examples"]
               + quotes["invented_examples"]]
    for row in audit["audited"]:
        quote = _norm_words(row["quote"])[:80]
        assert quote and any(d.startswith(quote) or quote.startswith(d)
                             for d in derived), (
            "the audit lists a row the receipt does not contain, so it is not a "
            f"sample of this run: {quote!r}")
    # one truncation flag PER LIST — a single shared boolean cannot say which
    # sample is complete, which is the same "a length is not a count" defect.
    assert quotes["paraphrase_examples_truncated"] is (quotes["paraphrase"] > 10)
    assert quotes["invented_examples_truncated"] is (quotes["invented"] > 10)
    assert quotes["summary_examples_truncated"] is (quotes["summary_in_one_turn"] > 10)
    for per_list, population in (("paraphrase_examples_truncated", "paraphrase"),
                                 ("invented_examples_truncated", "invented")):
        assert per_list in quotes, (
            f"{per_list} is gone — a shared flag cannot say WHICH list truncated")
        assert (len(quotes[per_list.replace("_truncated", "")])
                <= min(10, quotes[population]))


def test_the_committed_score_block_is_the_derivation():
    """Reds if `out/t6-score.json`'s own quote block drifts from its re-derivation.

    #14691 review (5th cycle, NEW-5). The arm above compares the DERIVATION to
    `t6-quote-audit.json`; nothing compared it to the block the scorer WRITES into
    `t6-score.json`, so setting that block to `verbatim: 50, paraphrase: 0,
    invented: 0` while leaving `claim_layer` honest stayed GREEN (measured). The
    committed artifact must be the function's own output, not a second opinion.
    """
    module = _load_score_module()
    committed = _score()["A4_no_invention"]["quotes"]
    derived = module.a4_invention()["quotes"]
    assert committed == derived, (
        "t6-score.json's quote block is not what the scorer derives from the "
        "committed receipts — the artifact is a claim now, not an input)")


def test_the_quote_breakdown_in_the_report_is_the_claim_layer():
    """Reds if REPORT §7's quote breakdown drifts from the claim layer it quotes.

    #14691 review (5th cycle, F1): `(11 verbatim / 27 paraphrase / 12 not in
    corpus)` -> `(50 / 0 / 0)` left 18/18 green, so the prose could state a
    fidelity the artifact refutes.
    ⛔ AND `12 not in the corpus` WAS ITSELF THE NEXT DEFECT (#14723 review, round
    10, P2): 12 is the SENTENCE-scope population, 3 of which the corpus does
    state. §7 now carries the DECIDER's count (invented) and names the 12 as the
    single-sentence population, so both numbers are pinned and neither is glossed
    as the other.
    """
    cl = _score()["claim_layer"]
    want = (f"**{cl['verbatim']} verbatim / {cl['paraphrase']} paraphrase / "
            f"{cl['invented']} unstated in any turn**")
    flat = " ".join(_report().split())
    assert want in flat, (
        f"REPORT.md's §7 quote breakdown is not the claim layer's: expected "
        f"{want!r}")
    # ...and the two populations §7 names are the artifact's, each by its own name
    assert cl["below_sentence_band"] != cl["invented"], (
        "§7 and the artifact agree only if the sentence-scope population is NOT "
        "the invented count — otherwise the round-10 P2 gloss is back")
    assert (f"**{cl['below_sentence_band']}** below the single-sentence band"
            in flat), (
        "§7 no longer names the peer audit's single-sentence population")
    assert (f"**{cl['summary_in_one_turn']}**" in flat
            or f"{cl['summary_in_one_turn']} summaries" in flat), (
        "§3's A4 table does not carry the summary bucket")
    assert cl["verbatim"] + cl["paraphrase"] + cl["summary_in_one_turn"] \
        + cl["invented"] == cl["quotes_total"]
    # ⛔ THE AUDIT'S OWN TRIO MUST BE STATED, BY ITS OWN NAME (#14723 review, P1).
    # §7 said "the same quote audit — **11 verbatim / 27 paraphrase / 9 unstated in
    # any turn**", attributing the DECIDER's count to the audit, which measures
    # 12. That trio does not even sum to the audit's own `quotes_total`
    # (11 + 27 + 9 = 47, against 50), and §3/A4, §8 and the peer write-up all said
    # 12 — while §7 sat inside "it reaches the same numbers from a different read".
    # Both numbers are legitimate; the DEFECT is the attribution, so the audit's
    # buckets are pinned next to their own label and can no longer be replaced by
    # the decider's.
    audit = json.loads((BUNDLE / "out" / "t6-quote-audit.json").read_text())
    assert (f"{audit['verbatim']} verbatim / {audit['paraphrase']} paraphrase / "
            f"{audit['not_in_corpus']} `not_in_corpus`") in flat, (
        "§7 no longer states the PEER audit's own buckets (11/27/12) beside "
        "their own label — with the decider's 9 then the only trio in sight, "
        "the sentence reads as the audit's breakdown again")
    assert audit["verbatim"] + audit["paraphrase"] + audit["not_in_corpus"] \
        == audit["quotes_total"], (
        "the audit's three buckets no longer sum to its own quotes_total, so "
        "the trio §7 states is not a partition of what it audited")
    # ⛔ THE DECIDER'S FOURTH BUCKET MUST BE IN THE SAME PARAGRAPH (#14723 review,
    # cycle 2, P2). §7 named the decider as `11 / 27 / 9` and stopped, omitting
    # `summary_in_one_turn` (3) — so the one paragraph that exists to reconcile the
    # two reads stated a partition summing to 47 of 50, and the §3/A4 table that
    # DOES carry the 3 sat far away, where no assertion compared the two. The
    # paraphrase pin above cannot catch it: `**3**` appears in §3, so a whole-report
    # `in flat` check is satisfied by a section §7 never consults.
    paras = [p for p in _report().split("\n\n") if want in " ".join(p.split())]
    assert len(paras) == 1, (
        f"§7's quote breakdown appears in {len(paras)} paragraphs, expected 1 — "
        "a second copy could disagree with the first")
    assert (f"**{cl['summary_in_one_turn']}**" in paras[0]
            or f"{cl['summary_in_one_turn']} summaries" in paras[0]), (
        "§7 states the decider's buckets WITHOUT the summary bucket "
        f"({cl['summary_in_one_turn']}): the partition it names then sums to "
        f"{cl['verbatim'] + cl['paraphrase'] + cl['invented']} of "
        f"{cl['quotes_total']}, and the missing bucket is the one that tells the "
        "TURN read from the SENTENCE read")


def test_the_orphan_second_measurement_agrees_with_the_primary():
    """Reds if `out/t6-independent-verification.json` is rewritten by hand.

    #14691 review (5th cycle, F1/(b)): this is the graph-side CORROBORATION of the
    A2 inversion and NO test opened it. Measured: replacing `A2` with
    `{"note": "HAND-REWRITTEN: every ownedBy row is correctly oriented"}` and
    emptying `declared` → **18/18 green**. The claims it is cited for are now
    read out of the artifact's own rows.
    """
    iv = json.loads((BUNDLE / "out" / "t6-independent-verification.json").read_text())
    declared = _corpus()["_declared_structure"]
    for key in ("products", "features", "teams", "roles", "people", "components",
                "customers", "customerSegments"):
        assert len(iv["declared"][key]) == len(declared[key]), (
            f"the independent measurement declares {len(iv['declared'][key])} "
            f"{key} where the corpus declares {len(declared[key])}")
    # the A2 corroboration — the pair count REPORT §7 rests on, and the rows
    # the primary receipt counts. 6 write rows reduce to 4 distinct pairs; the
    # peer's independent read saw the PAIRS.
    owned = iv["A2"]["ownedBy"]
    assert owned, "the independent measurement recorded no ownedBy rows"
    pairs = {(_norm_key(r["src"]), _norm_key(r["dst"])) for r in owned}
    assert len(pairs) == 4, (pairs, len(pairs))
    # ⛔ ORIENTATION, NOT JUST MEMBERSHIP (#14691 review, 5th cycle, NEW-1). The
    # set above is UNORDERED, so swapping `src`/`dst` on EVERY row — the exact
    # drift that would erase the corroboration of A2's inversion — left the suite
    # GREEN (measured). This file is cited BECAUSE it corroborates which end the
    # arrow points at, so the oriented pairs are compared to the primary receipt's
    # own edges.
    module = _load_score_module()
    want = {(_norm_key(e["src"]), _norm_key(e["dst"]))
            for e in module.EDGES if e["predicate"] == "ownedBy"}
    got = {(_norm_key(r["src"]), _norm_key(r["dst"])) for r in owned}
    assert got == want, (
        "the independent measurement's ownedBy rows are not the receipt's rows "
        f"in the receipt's orientation: missing={sorted(want - got)}, "
        f"unexpected={sorted(got - want)}")
    # the hierarchy and dependency legs must agree with the primary scorer
    a3 = _score()["A3_hierarchy"]["reported"]
    assert iv["A3"]["distinct_hasPart_pairs"] == a3["hasPart_distinct_pairs"] == 5
    assert len(iv["A2"]["hasPart"]) == 5
    assert len(iv["A2"]["dependsOn"]) == 7


def test_D6_owners_and_row_count_are_DERIVED_not_typed():
    r"""Reds if D6's owner set or its `ownedBy` figure is a literal.

    ⛔ #14723 review, round 10, P3. `asked_owners` filtered the witness names
    against a HARDCODED
    `{"Amara Osei", "Dana Whitlock", "Tomas Ferreira", "Jonas Bauer"}` and the
    answer's figure "holding 6 ownedBy rows" was typed. Both are facts about this
    run, so a re-run with a different population would emit a finding whose number
    contradicts the artifacts — and the REPORT↔artifact arm could not see it,
    because a shared literal drifts on both sides at once.

    MEASURED on the pre-fix code: `evidence.owned_by_rows` does not exist (the
    row count was only ever prose), and dropping a person from the DECLARED people
    left `owners_asked` at all four — the witness filter never read the corpus.

    ⛔ AND THE ROW-COUNT HALF USED TO PIN EXISTENCE, NOT DERIVATION (round 10,
    mutation attribution P1). It compared the figure to a count derived from the
    same `EDGES`, and the typed pre-fix figure was ALSO 6 — so
    `owned_rows = [None] * 6` left this row GREEN (MEASURED; it redded only when
    the field was ABSENT, via `KeyError`). The figure is now forced to move by
    deleting a row from the population, which a literal cannot follow.
    """
    module = _load_score_module()

    def d6(mod):
        return {f["id"]: f for f in mod._door_findings()}["D6"]

    baseline = d6(module)
    owned_rows = [e for e in module.EDGES if e["predicate"] == "ownedBy"]
    assert baseline["evidence"]["owned_by_rows"] == len(owned_rows), (
        "D6's ownedBy figure is not the write receipt's row count")
    assert f"holding {len(owned_rows)} ownedBy rows" in baseline["answer_over_mcp"]
    # ...and the figure FOLLOWS the receipt: with one ownedBy row removed from
    # the population, both the number and the sentence have to move.
    original_edges = module.EDGES
    try:
        module.EDGES = [e for e in original_edges if e is not owned_rows[-1]]
        reduced = d6(module)
    finally:
        module.EDGES = original_edges
    assert reduced["evidence"]["owned_by_rows"] == len(owned_rows) - 1, (
        "D6's ownedBy figure did not follow the receipt — it is a typed literal")
    assert (f"holding {len(owned_rows) - 1} ownedBy rows"
            in reduced["answer_over_mcp"]), (
        "D6's ANSWER still quotes the old ownedBy count")
    assert baseline["evidence"]["owners_asked"] == sorted(
        set(module.DECL["people"])
        & set((module.READBACK.get("link_witness") or {}).keys()))
    # ...and the owner set FOLLOWS the declared people: remove one, D6 asks three
    module.DECL["people"] = [p for p in module.DECL["people"]
                             if p != "Jonas Bauer"]
    mutated = d6(module)
    assert "Jonas Bauer" not in mutated["evidence"]["owners_asked"], (
        "D6 still asks a person the corpus no longer declares — the set is a "
        "literal, so a re-run with a different population would keep the old one")
    assert len(mutated["evidence"]["owners_asked"]) == 3


def test_each_door_finding_answer_states_the_artifacts_ANSWER():
    """Reds if a REPORT answer CONTRADICTS its artifact — not merely if it
    changes its first word.

    #14691 review (5th cycle, N3, then NEW-3). The 5th-cycle fix compared
    POLARITY only, so the exact contradiction the finding is about —
    `**No — a bare kind returns exactly the same rows as the namespaced spelling,
    so every declared spelling works.**`, led by "No" — stayed GREEN (measured).
    The 5th-cycle fix then required the artifact's answer to PREFIX the cell, and
    the 8th cycle measured the hole that leaves: both arms are SUPERSETS, so a
    cell can state the finding AND its negation. Appending
    `The direction is in fact fully visible and correct on every edge read.`
    INSIDE D1's answer cell left `61 passed` (measured at `bf7c610a`), and D2/D4
    share both arms by construction, so every door row was exposed.

    The cell must now be the artifact's answer EXACTLY (normalised words), which is
    what §8.3's last line already claimed. Prose about the machine's answer goes
    in the evidence column — D2's "walked names are 32, not the 28 declared"
    qualifier moved there when this arm became exact, so no claim was dropped.
    """
    for finding in _score()["door_findings"]:
        row = _report_line(rf"^\| \*\*{finding['id']}\*\* \|")
        assert row, f"REPORT.md has no row for door finding {finding['id']}"
        cell = _norm_words(row.split("|")[3])
        answer = _norm_words(finding["answer_over_mcp"])
        assert answer, f"{finding['id']} carries no answer in the artifact"
        assert cell == answer, (
            f"REPORT.md's {finding['id']} answer cell is not the artifact's answer, "
            f"word for word: expected {answer!r}, got {cell!r} — a prefix test "
            "would accept the finding and its negation in one cell, so the cell "
            "carries the machine's answer and nothing else; qualify it in the "
            "evidence column")


#: A `path:line` or `path:a-b` citation. Only citations that name LINES are read here:
#: a bare filename is a subject, not a claim about a location.
_CITATION_RE = re.compile(r"[\w./-]+\.(?:py|md|yml|json|sh|toml):\d+(?:-\d+)?")


def _citation_key(citation: str) -> tuple[str, int, int]:
    """(basename, first line, last line) — `tortoise/sdk.py:62739-62758` and
    `sdk.py:62739-62758` are the same claim about the same code, so the DIRECTORY is
    not part of the key and a single-line citation is the degenerate range N-N."""
    path, _, lines = citation.rpartition(":")
    lo, _, hi = lines.partition("-")
    return path.rsplit("/", 1)[-1], int(lo), int(hi or lo)


#: How far a wider citation may reach BACK before the artifact's own start. The only honest
#: wider span in either document reaches back a FIXED number of lines and ends exactly on the
#: artifact's end. ⛔ That figure is stated ONCE, in `_cites_a_site_in`'s docstring, where it is
#: DERIVED from the documents and graded — do not restate it here: the copy that lived here
#: named a literal site and was graded by nothing (measured: pointed at unrelated code,
#: `103 passed`), so this bound sits comfortably above the derived slack and far below any
#: range that begins in unrelated code.
#: ⛔ STATE EVERY VECTOR HERE AS AN OFFSET, never as a literal line number: the artifact's site
#: moves whenever a product file is rebased (+23 lines in `sdk.py` on the `#14582` fold alone,
#: which silently falsified every vector in this docstring once — round 21), and a literal is
#: a claim that expires with somebody else's commit.
_CITATION_WIDEN_BOUND = 20


def _bound_comment() -> str:
    """The `#:` block that documents `_CITATION_WIDEN_BOUND`, read from THIS file's source.

    A `#:` comment is invisible to `inspect`/`__doc__`, which is how the literal line range it
    used to carry survived three rounds of prose fixes while the docstring beside it was being
    corrected (#14723, round 24). Read the source so the block can be graded like any other
    prose that restates a figure.
    """
    source = Path(__file__).read_text(encoding="utf-8").splitlines()
    index = next((i for i, line in enumerate(source)
                  if re.match(r"^_CITATION_WIDEN_BOUND = ", line)), None)
    assert index is not None, (
        "no `_CITATION_WIDEN_BOUND = ...` assignment in this file — the block this arm grades "
        "is gone, so the arm would grade nothing")
    block: list[str] = []
    for line in reversed(source[:index]):
        if not line.startswith("#:"):
            break
        block.append(line)
    assert block, "the `#:` block above `_CITATION_WIDEN_BOUND` is empty; nothing to grade"
    return "\n".join(reversed(block))


def _end_clause() -> str:
    """The END condition shape 3 actually applies, read from the predicate's own AST.

    REPORT.md's §8.20 states the rule in prose as "pins the END (`<condition>`)", and a prose
    tolerance the code does not apply is how round 25's hole opened (`hi - other_hi == 0` widened
    to `<= 5` with every copy of the sentence still reading "exactly"). So the clause is READ from
    the predicate rather than restated: mutate the comparison and the sentence must move with it.
    """
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    function = next((n for n in ast.walk(tree)
                     if isinstance(n, ast.FunctionDef) and n.name == "_cites_a_site_in"), None)
    assert function is not None, (
        "`_cites_a_site_in` is gone from this file — the arm below would grade nothing")
    # Shape 3 is the only branch whose condition names the allowance, so find THAT `if` and take
    # its `hi`/`other_hi` conjunct — shape 1's `hi == other_hi` (the same-range test) is not it.
    for node in ast.walk(function):
        if not isinstance(node, ast.If):
            continue
        conjuncts = node.test.values if isinstance(node.test, ast.BoolOp) else [node.test]
        if not any("_CITATION_WIDEN_BOUND" in ast.unparse(c) for c in conjuncts):
            continue
        for conjunct in conjuncts:
            if not isinstance(conjunct, ast.Compare):
                continue
            # The END conjunct is `hi - other_hi <op> <n>`; the containment conjunct
            # (`other_hi <= hi`) also names both ends, so the SUBTRACTION is the discriminator.
            left = conjunct.left
            if not isinstance(left, ast.BinOp):
                continue
            if {ast.unparse(left.left), ast.unparse(left.right)} == {"hi", "other_hi"}:
                return ast.unparse(conjunct)
    raise AssertionError(
        "no `hi`/`other_hi` comparison inside the allowance's `if` in `_cites_a_site_in` — shape 3 "
        "has no END condition left for REPORT §8.20's sentence to name")


def _cites_a_site_in(cited: tuple[str, int, int], sites) -> bool:
    """Is `cited` the SAME site as one of `sites`?

    Three shapes are honest, and nothing else is (#14691 review, round 15, finding 5):

    1. **The same range.**
    2. **One line inside** the artifact's range — `lo == hi` and `other_lo <= lo <= other_hi`.
    3. **A wider range that BEGINS at or just before the artifact's start** — the prose quotes
       a span plus its call site (the one honest wider span in either document: start slack 18,
       end exact).

    ⛔ CONTAINMENT IN EITHER DIRECTION WAS NOT ENOUGH, measured: it admitted a range that BEGINS
    exactly where a refused mutant begins and pads its END onto the pinned site (such a range
    CONTAINS the site, which is why a containment test admits it on that one disjunct alone).
    Equality alone would red on the two honest
    shapes above, and OVERLAP would accept a MOVED range (the same width, shifted a few lines
    down), so the three shapes are enumerated rather than approximated.

    ⛔ AND BOUNDING THE START ALONE WAS STILL NOT ENOUGH (#14691 review, round 16, finding 1):
    with only the start bound, the same padding attack survived from the OTHER side — a range
    that opens exactly on the artifact's start and pads its END by tens of thousands of lines
    into the same file left `102 passed` (measured). So shape 3 admits a citation that begins at
    the artifact's start or up to `_CITATION_WIDEN_BOUND` lines BEFORE it, and requires it to end
    at EXACTLY the artifact's end (`hi - other_hi == 0`). A start strictly INSIDE the range is
    refused by the `lo <= other_lo` clause — measured, holding the end at the artifact's end:
    start offsets `1` through `span - 1` inside return False (where `span = other_hi - other_lo`),
    and every offset PAST the artifact's end returns False too, because `lo <= other_lo` then
    fails. ⛔ THE ONE OFFSET THAT IS NOT REFUSED IS `span` ITSELF — the artifact's LAST LINE, where
    `lo == hi == other_hi` collapses the range to the degenerate single line that shape 2 exists
    to admit, so it returns **True**. That is a shape-2 admission, not an inside start.
    ⛔ AND THE END IS SWEPT ON THE SAME PRINCIPLE (#14723, round 25): "end exact" is a claim
    ABOUT THE CODE, so every end offset `1..span` past the artifact's end, at the artifact's own
    start, must be REFUSED. Measured at the previous head, widening `hi - other_hi == 0` to
    `<= 5` — and to `<= 1` — left all `103 passed`: the table's only end-padded row pads by the
    site's WHOLE span, so any tolerance narrower than that was graded by nothing while this
    paragraph's "EXACTLY" became false at the tree it describes. The start side was swept one
    offset at a time in round 22 for exactly this reason; the end side is swept the same way.
    ⛔ STATE THIS RULE AS AN OFFSET FROM THE SPAN, NEVER AS LITERAL POSITIONS — and cite no
    artifact by line range here. A rebase can move a site by lines AND change its span: the
    `#14624` creation hook moved this site +23 lines and widened it 19 → 20, which made the
    previous restatement of this sentence ("start offsets 1..18 inside return False", "19
    GREEN") false at the site the artifact then carried — an off-by-one in the SENTENCE, not in
    the predicate, and the same class as the literal line ranges that expired in round 21. So do
    not trust this prose and do not restate it: it is graded by
    `test_the_citation_shape_table_is_the_predicate_the_docstring_states`, which SWEEPS every
    offset in `1..span + 3` against the live predicate. That is
    what the one honest wider span in the two documents actually does — `entity_reads`'s
    `hasPart` read range against the artifact's copy of it is slack 18 at the start and 0 at the
    end (measured) — so
    the honest shape stays green, and an end-padded citation cannot pass at ANY start offset.
    ⛔ The START allowance is 20, not 0, and it is LIVE — measured as OFFSETS from the artifact's
    start, which is the only form that survives a rebase: a range beginning **19 lines before**
    it is GREEN, **21 lines before** is RED (round 17; re-measured at round 21 after the
    `#14582` fold moved the site by +23 lines). Do not "tidy" `_CITATION_WIDEN_BOUND` away —
    the honest D1 citation above needs 18 of it.
    """
    name, lo, hi = cited
    for other_name, other_lo, other_hi in sites:
        if other_name != name:
            continue
        if lo == other_lo and hi == other_hi:
            return True
        if lo == hi and other_lo <= lo <= other_hi:
            return True
        if (lo <= other_lo and other_hi <= hi
                and other_lo - lo <= _CITATION_WIDEN_BOUND
                and hi - other_hi == 0):
            return True
    return False

def test_the_door_EVIDENCE_cells_state_the_artifacts_numbers():
    r"""Reds if a door finding's EVIDENCE cell drifts from the artifact it cites.

    ⛔ #14691 review, round 14, finding 1 (P1). The ANSWER cell is graded word for
    word by the arm above; the EVIDENCE column was graded by NOTHING, and it is the
    cell a reader uses to decide whether the door is broken. Measured at
    `0368cbb7`, each mutation applied alone, whole file, `97 passed`:

      D4 `**0 rows** each`  -> `**1 rows** each`   GREEN
      D4 `19 real neighbours` -> `20`               GREEN
      D2 `false on 5/5`     -> `false on 6/5`       GREEN
      §3/A2 finding 5 `containment is absent entirely (0 edges)` -> `(1 edges)` GREEN

    Each figure below is DERIVED from the artifact that produces it, so the cell
    cannot state a number its own measurement denies.

    ⛔ AND `flat` RE-OPENED TWO OF THEM (round 16, §8.18): §8.17 quotes `**0 rows**
    each` and `false on 5/5` verbatim, so those mutants were GREEN again at `c76156f`
    once the section that recorded this fix existed. Each figure is pinned in the
    ROW that owns it (`_report_table_row`), never in the document.
    """
    witness = _readback()["link_witness"]

    # ⛔ EACH FIGURE IS GRADED IN THE CELL THAT OWNS IT, never in the flattened
    # document. `flat` is how this arm was VACUOUS for two of its four figures
    # (round 15, found at `c76156f`): §8.17 — the paragraph this arm's own fix
    # added — quotes `**0 rows** each` and `false on 5/5` verbatim, so mutating the
    # D4 or D2 CELL left the suite GREEN while the log still spelled the number.
    # D4's `19 real neighbours` and finding 5's `(0 edges)` survived only because
    # the log happened to spell them differently.
    d4_row = _report_table_row(r"^\| \*\*D4\*\* \|")
    d2_row = _report_table_row(r"^\| \*\*D2\*\* \|")
    finding5 = _report_line(r"^5\. \*\*`team → role` containment")
    assert finding5, "REPORT §3/A2 has no finding 5 row to grade"

    def expand_rows(name: str) -> int:
        reply = witness[name]["expand_relationships"]["reply"]
        return len(reply.get("result") or [])

    # D4 — the expand door returns [] for nodes that HAVE edges (the finding itself)
    lc_rows = expand_rows("Lumen Core")
    zero = {name: expand_rows(name) for name in ("Amara Osei", "Platform team", "event-bus")}
    assert lc_rows == 1, f"the expand door returned {lc_rows} rows for Lumen Core"
    assert set(zero.values()) == {0}, zero
    assert f"Lumen Core: {lc_rows} row" in d4_row, (
        "REPORT's D4 evidence does not state the expand door's own row count "
        f"({lc_rows})")
    neighbours = witness["Lumen Core"]["explore_neighbour_count"]
    assert neighbours != lc_rows, (neighbours, lc_rows)
    assert f"{neighbours} real neighbours" in d4_row, (
        f"REPORT's D4 evidence does not state the witness's {neighbours} neighbours")
    assert f"**{zero['Amara Osei']} rows** each" in d4_row, (
        "REPORT's D4 evidence does not state the zero-row count the door returned "
        "for the three structurally-connected nodes")

    # D2 — the retrieval half is `walk_root_is_entity` false on EVERY probe
    probes = _score()["A5_retrieval"]["probes"]
    false = sum(1 for p in probes if p["walk_root_is_entity"] is False)
    assert false == len(probes) and probes, (false, len(probes))
    assert f"false on {false}/{len(probes)}" in d2_row, (
        f"REPORT's D2 evidence does not state `false on {false}/{len(probes)}`")

    # §3/A2 finding 5 — `team -> role` containment is EMPTY in the artifact
    containment = _score()["A2_edges"]["team_role_containment"]
    assert containment == [], containment
    assert f"containment is absent entirely ({len(containment)} edges)" in finding5, (
        "REPORT §3/A2's finding 5 does not state the artifact's own count of "
        f"team->role containment edges ({len(containment)})")


def test_the_A2_class_table_derives_BOTH_cells_from_the_artifact():
    r"""Reds if either cell of §3/A2's class table drifts — the SECOND COPY included.

    ⛔ #14691 review, round 14, finding 2, the measured instance. The row
    `| product → feature (hasPart) | **5/5** | 5/5 (parent end is the product) | ✅ |`
    states the same figure twice, and a pin shaped `expected in line` grades ONE of
    them: measured at `0368cbb7`, mutating EITHER `5/5` left `97 passed`, and only
    mutating BOTH reddened. A duplicate is a copy nothing grades, so the multiplicity
    bound lives HERE, on the table whose rows are one claim each, and not in the
    generic `cell()` helper — measured on §1's own paragraph, honest prose repeats a
    figure across two different claims (`5 documents` / `5 containment pairs`) and a
    generic bound reds on correct text.

    Every cell below is read off `A2_edges.totals`, which the scorer emits, so the
    table cannot state a count its own artifact denies in either column.
    """
    a2 = _score()["A2_edges"]
    totals = a2["totals"]

    def row(label: str) -> list[str]:
        line = _report_line(rf"^\| {{0,2}}{re.escape(label)}")
        assert line, f"REPORT.md's §3/A2 table has no `{label}` row"
        return [c.strip() for c in line.strip().strip("|").split("|")]

    # product → feature: BOTH cells are artifact totals, and both are graded
    present, oriented = row("product → feature (`hasPart`)")[1:3]
    assert present.strip("*") == totals["containment_present"], (present, totals)
    parent_first = totals["containment_parent_spelled_product_first (observation)"]
    assert oriented.startswith(parent_first), (oriented, parent_first)

    # the single-cell rows
    assert row("person → team (`related`)")[1].strip("*") == totals["membership_present"]
    assert row("person → role (`related`)")[1].strip("*") == totals["role_links_present"]
    assert row("**team → role**")[1].strip("*") == str(totals["team_to_role_edges"])
    assert row("dependent → dependency (`dependsOn`)")[1].strip("*") == (
        totals["dependency_present_and_correct"])

    # ownedBy: present pairs, and the inverted ROW count over the pairs' own edges
    present, oriented = row("thing → owner (`ownedBy`)")[1:3]
    owner_rows = sum(r["n_edges"] for r in a2["ownership"])
    assert present.startswith(f"**{totals['ownership_present']}**"), present
    assert f"{totals['ownership_oriented_correct']}/{owner_rows}" in oriented, (
        f"the ownedBy cell does not state the artifact's "
        f"{totals['ownership_oriented_correct']}/{owner_rows} — {oriented!r}")


def test_every_CODE_citation_in_the_documents_is_one_an_artifact_carries():
    r"""Reds if a `path:line` citation in either document is not one the artifacts carry.

    ⛔ #14691 review, round 14, finding 4 (P2). `readback.json`'s copies are pinned to
    code content by the citation arm above, but the REPORT repeats the same citations
    in its evidence cells and those copies were free. Measured at `0368cbb7`, whole
    file, `97 passed` — the round-10 mutants, applied to the REPORT instead of the
    artifact:

      D1 `tortoise/navigation.py:172-179` -> `166-173`
      D6 `sdk.py:62739-62758`             -> `sdk.py:61000-61019`
      §3/A2 `tortoise/sdk.py:62739-62758`  -> `61000-61019`

    The rule is not "the line numbers must be right" — that is the code arm's job —
    it is "a citation in the prose is one of the citations the artifact records", so
    the document cannot cite code no measurement named. A citation to this bundle's
    own `VERIFICATION-independent.md` is excluded: it is a cross-reference inside the
    two documents, not a claim about product code.
    """
    artifacts: set[tuple[str, int, int]] = set()
    for path in (BUNDLE / "out").glob("*.json"):
        text = path.read_text(encoding="utf-8")
        artifacts |= {_citation_key(c) for c in _CITATION_RE.findall(text)}
    docs = _report() + "\n" + _verification()
    cited = {_citation_key(c) for c in _CITATION_RE.findall(docs)}
    self_ref = {k for k in cited if k[0] == "VERIFICATION-independent.md"}
    unsupported = sorted(
        f"{name}:{lo}" + (f"-{hi}" if hi != lo else "")
        for name, lo, hi in cited - self_ref
        if not _cites_a_site_in((name, lo, hi), artifacts))
    assert not unsupported, (
        "these `path:line` citations appear in REPORT.md / VERIFICATION-independent.md "
        f"and in no committed artifact: {unsupported}. Either the prose cites code no "
        "measurement named, or the citation drifted from the artifact's own copy.")


def test_the_citation_shape_table_is_the_predicate_the_docstring_states():
    r"""Reds if `_cites_a_site_in` admits or refuses a shape its docstring names.

    ⛔ #14723, round 22. The arm below grades the DOCUMENTS, so a wrong table in the
    predicate's OWN docstring is prose nothing grades — and it was wrong: it wrote the
    inside offsets as literal positions ("1..18 inside return False", "19 GREEN")
    while the offset that lands on the artifact's last line collapses the range to the
    single line shape 2 exists to admit and returns True. Those figures were true of a
    span-19 site and false one commit later, when the same site became span 20 — an
    off-by-one in the SENTENCE, not the predicate. So it is now stated as `1..span - 1`
    refused and `span` admitted, and SWEPT below rather than restated anywhere.

    Both directions are graded: the honest shapes must stay green (an over-tightened
    predicate reds the documents) and the padded/moved ones must stay red (the
    containment-in-either-direction predicate this replaced admitted them — mutated
    back in, this arm reds on the end-padded and inside-start rows).

    ⛔ AND THE BOUND'S *VALUE* IS GRADED, not derived from itself (#14723, round 23).
    The two rows that state the slack as `_CITATION_WIDEN_BOUND` / `+1` move WITH the
    constant, so they can only grade the relationship: measured at this head,
    `_CITATION_WIDEN_BOUND = 30` left all **103 passed** while the predicate's docstring
    ("The START allowance is 20 … a range beginning 21 lines before it is RED") and
    REPORT.md's §8.20 ("at most **20 lines** before the artifact's start") both became
    false — the widening "tidy" that docstring forbids, graded by nothing. The last two
    rows therefore carry the PROSE's own figures as LITERALS — 19-before must be
    admitted and 21-before refused, which pins the allowance to 19 or 20 and reds a
    widening to 21+ as well as a tightening below the honest D1 citation's 18.

    Every vector is an offset FROM A SITE a committed artifact carries, never a literal
    line number, so this arm cannot expire when a product file is rebased. The literals
    below are slack WIDTHS — the prose's own figures, which must not move with the
    constant they describe.
    """
    artifacts: set[tuple[str, int, int]] = set()
    for path in (BUNDLE / "out").glob("*.json"):
        artifacts |= {_citation_key(c)
                      for c in _CITATION_RE.findall(path.read_text(encoding="utf-8"))}
    sites = sorted(a for a in artifacts if a[0] == "sdk.py")
    assert sites, "no committed artifact carries an `sdk.py` citation to shape the table from"
    name, lo, hi = sites[0]
    span = hi - lo
    assert span >= 2, (
        f"the shaping site {name}:{lo}-{hi} spans {span} line(s); the inside-start rows need "
        "two or more to be distinguishable from the degenerate single-line shape")

    one = [(name, lo, hi)]
    table = {
        "the same range": ((name, lo, hi), True),
        "one line at the artifact's start (shape 2)": ((name, lo, lo), True),
        "one line at the artifact's END (shape 2, the degenerate range)": ((name, hi, hi), True),
        f"start slack of exactly the bound ({_CITATION_WIDEN_BOUND}), end exact (shape 3)":
            ((name, lo - _CITATION_WIDEN_BOUND, hi), True),
        f"start slack one PAST the bound ({_CITATION_WIDEN_BOUND + 1}), end exact":
            ((name, lo - _CITATION_WIDEN_BOUND - 1, hi), False),
        "one line INSIDE the artifact's start, end exact": ((name, lo + 1, hi), False),
        "one line INSIDE the artifact's end, end exact": ((name, hi - 1, hi), False),
        "a range that begins exactly where the artifact begins and pads its END":
            ((name, lo, hi + span), False),
        "a range that brackets the artifact on both sides": ((name, lo - span, hi + span), False),
        "a MOVED range (the next span down), same width": ((name, lo - span, hi - span), False),
        # …and the prose's two figures as literals, so the allowance's value cannot be
        # widened or tightened while the rows above keep moving with it (#14723, round 23).
        "start slack of the PROSE's green figure (19 lines before), end exact":
            ((name, lo - 19, hi), True),
        "start slack of the PROSE's red figure (21 lines before), end exact":
            ((name, lo - 21, hi), False),
    }
    # …and the docstring's INSIDE claim is SWEPT, derived from the site's own span: every start
    # strictly inside the range refused, the LAST-LINE offset (`span`, the degenerate single line)
    # admitted, and every offset past the end refused. A sweep, because the figures cannot then be
    # restated wrong — the defect above WAS a restated figure, and a literal is what expired.
    for k in range(1, span + 4):
        table[f"start offset {k} inside a span-{span} site, end exact"] = (
            (name, lo + k, hi), k == span)
    # ⛔ …and the END side is swept too (#14723, round 25). The paragraph above says shape 3
    # requires the citation to end at EXACTLY the artifact's end, and the table's only end-padded
    # row pads by the site's WHOLE span — so every tolerance narrower than that was graded by
    # nothing. Measured at the previous head, widening `hi - other_hi == 0` to `<= 5` and to
    # `<= 1` left all **103 passed** while the sentence became false at the tree it describes.
    for k in range(1, span + 1):
        table[f"end offset {k} past a span-{span} site, start at its start"] = (
            (name, lo, hi + k), False)
    # ⛔ …and NEITHER home of the rule may name an artifact by LINE RANGE. A literal site
    # expired twice here (round 21, and the +23-line hook that also widened the span), so a
    # literal — qualified or bare — is the defect the offset rule exists to remove. Grade it in
    # BOTH places the rule is described: the predicate's own docstring, and the `#:` block above
    # `_CITATION_WIDEN_BOUND` (measured at this head: that block's literal could be pointed at
    # unrelated code — `entity_reads.py:500-559` — and all `103 passed`, because the guard read
    # only `__doc__`).
    for home, prose in (("`_cites_a_site_in`'s docstring", _cites_a_site_in.__doc__ or ""),
                        ("the `#:` block above `_CITATION_WIDEN_BOUND`", _bound_comment())):
        literal = re.search(r"(?:[A-Za-z0-9_/.]+\.py)?:\d+(?:-\d+)?", prose)
        assert literal is None, (
            f"{home} cites {literal.group(0) if literal else ''} by line range — state the "
            "vector as an offset from the span; a literal site expires with a rebase, and one "
            "already made this sentence false once")
    # ⛔ AND THE TWO FIGURES THIS PROSE STATES ARE DERIVED, not restated (#14723, round 24).
    # Measured at this head, each applied alone and restored byte-for-byte: the docstring's
    # honest-wider-span slack 18 -> 17 and `_CITATION_WIDEN_BOUND` 20 -> 19 both left
    # **103 passed**, while the sentences stating them became false. The allowance's VALUE is
    # only bounded to 19-or-20 by the 19/21 rows, so the prose's own "is 20" was free.
    doc_cited = {_citation_key(c)
                 for c in _CITATION_RE.findall(_report() + "\n" + _verification())}
    doc_cited = {k for k in doc_cited if k[0] != "VERIFICATION-independent.md"}
    # the honest wider spans are the document citations the artifacts do NOT carry verbatim but
    # which pass shape 3: they reach back from an artifact's start and end exactly on its end.
    wider = {art[1] - cited[1]
             for cited in doc_cited - artifacts
             for art in artifacts
             if art[0] == cited[0] and art[2] == cited[2] and cited[1] < art[1]}
    stated_wider = re.search(r"start slack (\d+),\s+end exact",
                             _cites_a_site_in.__doc__ or "")
    assert stated_wider, (
        "`_cites_a_site_in`'s docstring no longer states the honest wider span as "
        "\"start slack <n>, end exact\"; the figure below is graded by being stated once")
    assert wider == {int(stated_wider.group(1))}, (
        f"the documents' honest wider span(s) reach back {sorted(wider)} line(s), while the "
        f"docstring states {stated_wider.group(1)} — a restated figure that nothing derived, "
        "which is how this sentence went false the first time")
    allowance = re.search(r"START allowance is (\d+)", _cites_a_site_in.__doc__ or "")
    assert allowance, (
        "`_cites_a_site_in`'s docstring no longer states the START allowance as "
        "\"The START allowance is <n>\"; the constant's value below is graded against it")
    assert int(allowance.group(1)) == _CITATION_WIDEN_BOUND, (
        f"the docstring states a START allowance of {allowance.group(1)} while "
        f"`_CITATION_WIDEN_BOUND` is {_CITATION_WIDEN_BOUND} — the 19/21 rows admit either 19 "
        "or 20, so without this the prose's own figure is graded by nothing (measured: 20 -> 19 "
        "left 103 passed)")
    # ⛔ AND EVERY *OTHER* COPY OF THOSE TWO FIGURES MUST BE THE SAME FIGURES (#14723, round 27).
    # Round 26 derived the slack and pinned the allowance against it — and left four copies of the
    # same numbers textually free: two in this docstring ("slack 18 at the start and 0 at the end",
    # "needs 18 of it"), two in REPORT §8.20/§8.21 (the "20 lines" allowance and the 19/21 pair),
    # plus §8.21's "21 lines of start slack … and 19". Each was mutable into a falsehood with the
    # suite GREEN (measured: 18 -> 17 at all four, `103 passed`). A figure is graded when a
    # DERIVATION checks it, not when one of its copies happens to be asserted.
    doc_all = _cites_a_site_in.__doc__ or ""
    derived_slack = min(wider)
    stated_slack = re.search(r"slack (\d+) at the start and (\d+) at the\s+end", doc_all)
    assert stated_slack, (
        "`_cites_a_site_in`'s docstring no longer states the honest span as "
        "\"slack <n> at the start and <n> at the end\"; it is now graded here, so it may not "
        "be dropped silently")
    assert (int(stated_slack.group(1)), int(stated_slack.group(2))) == (derived_slack, 0), (
        f"the docstring states slack {stated_slack.group(1)} at the start / "
        f"{stated_slack.group(2)} at the end while the documents derive {derived_slack} / 0")
    stated_needs = re.search(r"needs (\d+) of it", doc_all)
    assert stated_needs, "`_cites_a_site_in`'s docstring no longer states \"needs <n> of it\""
    assert int(stated_needs.group(1)) == derived_slack, (
        f"the docstring says the honest citation needs {stated_needs.group(1)} of the allowance "
        f"while the documents derive {derived_slack}")
    green_txt = re.search(r"\*\*(\d+) lines before\*\*\s+it is GREEN", doc_all)
    red_txt = re.search(r"\*\*(\d+) lines before\*\* is RED", doc_all)
    assert green_txt and red_txt, (
        "`_cites_a_site_in`'s docstring no longer states the live pair as "
        "\"**<n> lines before** is GREEN / **<n> lines before** is RED\"")
    assert (int(green_txt.group(1)), int(red_txt.group(1))) == (
            _CITATION_WIDEN_BOUND - 1, _CITATION_WIDEN_BOUND + 1), (
        f"the docstring states the pair as {green_txt.group(1)}/{red_txt.group(1)} while the "
        f"constant makes it {_CITATION_WIDEN_BOUND - 1}/{_CITATION_WIDEN_BOUND + 1}")
    assert "1..span + 3" in doc_all, (
        "`_cites_a_site_in`'s docstring no longer names the sweep as \"1..span + 3\" — the sweep "
        "below is `range(1, span + 4)`, i.e. 1..span+3 inclusive")
    # …and REPORT's copies, which are the ones a reader of the EVIDENCE doc actually meets. The
    # slack copy is a REGEX, not a needle, because it is the same figure the docstring copy is
    # graded on and it sits one line below §8.20's allowance needle (#14723, round 28: measured
    # `18 at the start and 0 at the end` -> `17`, `103 passed`).
    report_txt = _report()
    rep_slack = re.search(r"slack\s+(\d+) at the start and (\d+) at the\s+end", report_txt)
    assert rep_slack, (
        "REPORT §8.20 no longer states the honest span's slack as "
        "\"slack <n> at the start and <n> at the end\"")
    assert (int(rep_slack.group(1)), int(rep_slack.group(2))) == (derived_slack, 0), (
        f"REPORT §8.20 states the slack as {rep_slack.group(1)}/"
        f"{rep_slack.group(2)} while the documents derive {derived_slack}/0")
    # …and the copies round 27/28 left free (#14723, round 29). Rounds 27 and 28 graded the
    # §8.20/§8.21 statements they had measured and stopped there; the SAME two figures — and the
    # END condition the rule is named for — stayed textually free in the copies a reader meets
    # FIRST: §8.20's OPENING paragraph (the live rule, not a round record) and §8.22's
    # cross-reference to it. Measured at `bf95c20ff515`, each applied alone, file restored
    # byte-for-byte (`sha256sum` compared), every one left all **103 passed**:
    #
    #   §8.20 "keeping the 20-line START allowance"          -> 19-line       103 passed
    #   §8.20 "the one honest wider span needs (18)"         -> (17)          103 passed
    #   §8.20 "pins the END (`hi - other_hi == 0`)"          -> `<= 5`        103 passed
    #   §8.22 "the sentences that state it say **20**"       -> **19**        103 passed
    #   §8.22 "§8.20's `20-line START allowance`"            -> 19-line       103 passed
    #
    # The END clause is DERIVED from the predicate's AST (`_end_clause`), never restated here: a
    # tolerance the prose does not name is exactly how round 25's `<= 5` hole opened.
    flat_report = re.sub(r"\s+", " ", report_txt)
    for needle, why in (
            (f"pins the END (`{_end_clause()}`)",
             "the END condition shape 3 actually applies (§8.20)"),
            (f"keeping the {_CITATION_WIDEN_BOUND}-line START allowance the one honest "
             f"wider span needs ({derived_slack})",
             "the allowance and the honest slack together, in §8.20's opening paragraph"),
            (f"the sentences that state it say **{_CITATION_WIDEN_BOUND}**",
             "§8.22's cross-reference to those same sentences"),
            (f"§8.20's `{_CITATION_WIDEN_BOUND}-line START allowance`",
             "§8.22's quotation of §8.20's figure"),
    ):
        assert needle in flat_report, (
            f"REPORT.md no longer states {why} as {needle!r} — it is graded against "
            "`_CITATION_WIDEN_BOUND`, the documents' derived slack and the predicate's own END "
            "clause, so it may not drift from any of them")
    for needle, why in (
            (f"most **{_CITATION_WIDEN_BOUND} lines** before",
             "the allowance's figure in §8.20"),
            (f"more than {_CITATION_WIDEN_BOUND} lines early",
             "the allowance restated in §8.20's refused-class list"),
            (f"{_CITATION_WIDEN_BOUND - 1} lines of start slack, inside the "
             f"{_CITATION_WIDEN_BOUND}-line allowance",
             "the live slack and the allowance together in §8.21"),
            (f"{_CITATION_WIDEN_BOUND + 1} lines of start slack is still RED and "
             f"{_CITATION_WIDEN_BOUND - 1}",
             "the live pair in §8.21"),
            (f"at {_CITATION_WIDEN_BOUND + 1};",
             "§8.20's red offset as a literal, beside the refused range that carries it"),
            (f"at {_CITATION_WIDEN_BOUND - 1} still passes",
             "§8.20's green offset as a literal, beside the range that carries it"),
    ):
        assert needle in report_txt, (
            f"REPORT.md no longer states {why} as {needle!r} — it is graded against "
            "`_CITATION_WIDEN_BOUND`/the derived slack, so it may not drift from them")
    wrong = {shape: got for shape, (cited, expected) in table.items()
             if (got := _cites_a_site_in(cited, one)) is not expected}
    assert not wrong, (
        f"`_cites_a_site_in` disagrees with its own docstring's shape table on "
        f"{sorted(wrong)} — the vector is shaped on {name}:{lo}-{hi}, and the rule must be "
        f"the predicate, not a wider gloss of it")


def test_EVERY_bytes_figure_in_the_documents_is_the_committed_file():
    r"""Reds if ANY `<n> bytes` figure in either document is not the committed size.

    ⛔ #14691 review, round 14, finding 3 (P2), the DUPLICATE-FIGURE half. A pin shaped
    `expected in line` grades ONE copy of a figure, so a second copy of the same
    number is free: measured at `0368cbb7`, `§8.9 … keeps the artifact at 809,812
    bytes` -> `809,813` left `97 passed` while the same figure elsewhere in the
    document reddened its own pin. Every occurrence is graded here instead, so a
    duplicate cannot carry a drifted value.
    """
    size = (BUNDLE / "out" / "readback.json").stat().st_size
    docs = _report() + "\n" + _verification()
    stated = re.findall(r"([\d,]+) bytes", docs)
    assert stated, "neither document states an artifact size in `bytes`"
    wrong = [s for s in stated if int(s.replace(",", "")) != size]
    assert not wrong, (
        f"a `bytes` figure in the documents is not the committed readback.json's "
        f"{size:,}: {wrong} (every occurrence is graded, not just the first, because "
        "`expected in line` leaves a duplicate copy free)")


def test_report_headline_counts_are_the_artifacts_counts():
    """Reds if a heading's own COUNTS drift from the artifact (#14691 review, N4).

    The verdict TOKEN was pinned; the numbers beside it were not. Measured:
    `A1 … PASS (28/28)` -> `PASS (35/35)` and `A3 … PASS (5/5 distinct pairs, 2
    parents, 5 children)` -> `PASS (99 …, 1 parent, 42 children)` both left 18/18
    green while `t6-score.json` still said 28 and 5/2/5.
    """
    score = _score()
    a1 = score["A1_type_coverage"]
    head1 = _report_line(r"^#{2,4} A1 · ")
    assert head1, "REPORT.md has no A1 heading"
    assert f"({a1['right_kind']}/{a1['declared']})" in head1, (a1, head1)
    a3 = score["A3_hierarchy"]
    rep = a3["reported"]
    want = (f"({a3['declared']['edges']}/{a3['declared']['edges']} distinct pairs, "
            f"{rep['distinct_parents']} parents, {rep['distinct_children']} children)")
    head3 = _report_line(r"^#{2,4} A3 · ")
    assert head3, "REPORT.md has no A3 heading"
    assert want in head3, (want, head3)


def test_the_directions_table_is_the_ruling_table():
    """Reds if the score artifact records an UNAVAILABLE or locally-copied table.

    #14691 review (5th cycle, item (c)-artifact): the import guard records
    `DIRECTIONS_SOURCE`/`DIRECTIONS` faithfully, but NOTHING read the artifact, so
    a committed `t6-score.json` with `directions_source: UNAVAILABLE…` and
    `directions: {}` passed. The table is `pack_registry`'s ruling (#11548).
    """
    score = _score()
    assert score["directions_source"] == "tortoise.pack_registry.SEMANTICS_DIRECTIONS", (
        score["directions_source"])
    assert score["directions"], "the artifact records no orientation rulings"
    from tortoise.pack_registry import SEMANTICS_DIRECTIONS
    # ⛔ A SUBSET, NOT AN EQUALITY, and that is the honest claim: the artifact
    # carries the rulings for the predicates it SCORES (`_DST_ROLE`'s six), never
    # a local restatement of the whole table. Equality would red on a legitimate
    # narrowing and would pass a local copy that happened to be complete.
    for predicate, direction in score["directions"].items():
        assert SEMANTICS_DIRECTIONS.get(predicate) == direction, (
            f"the artifact records `{predicate}: {direction!r}` where the ruling "
            f"table says {SEMANTICS_DIRECTIONS.get(predicate)!r} — a local copy, "
            "not the table")


def test_an_ERROR_reply_is_not_an_EMPTY_answer():
    """Reds if a FAILED call is read as "the door answered nothing" (N1).

    `rows()` maps an error envelope to `[]`, so a witness reply of `{"error": …}`
    MANUFACTURED D4 — a claim about the product from a call that never answered.
    Measured: replacing all seven witness `expand_relationships` replies with
    `{"ok": false, "error": …}` left 18/18 green. Both arms run on a copy of the
    module's own readback; the measured-empty arm is the non-vacuity control.
    """
    module = _load_score_module()
    assert module.is_error_reply({"ok": False, "error": "boom"}) is True
    assert module.is_error_reply('{"error": "boom"}') is True
    assert module.is_error_reply({"result": []}) is False
    readback = module.READBACK
    witness = readback.get("link_witness") or {}
    assert witness, "the readback carries no link witness to drive"
    saved = json.loads(json.dumps(readback))
    try:
        readback["door_findings"] = [f for f in readback.get("door_findings") or []
                                     if f.get("id") != "D4"]
        for w in witness.values():
            w["expand_relationships"] = {"ok": False, "error": "transport"}
        assert "D4" not in [f["id"] for f in module._door_findings()], (
            "an ERROR reply manufactured the D4 finding — a failed call is not an "
            "answered-empty one")
        for w in witness.values():
            w["expand_relationships"] = {"reply": {"result": []}}
        assert "D4" in [f["id"] for f in module._door_findings()], (
            "a measured EMPTY answer no longer produces D4 — the arm above is "
            "vacuous")

        # ⛔ THE SAME GAP ON THE OWNERSHIP DOOR (#14691 review, 5th cycle, NEW-2).
        # A failure spelled `{"ok": false, "error": …, "reply": []}` satisfies
        # BOTH the "the owner was asked" arm and `rows(...) == []`, so without the
        # guard it counts as a measured empty and manufactures D6. Measured:
        # deleting `if call_failed(probe): continue` left the suite GREEN.
        readback["door_findings"] = [f for f in readback["door_findings"]
                                     if f.get("id") != "D6"]
        for w in witness.values():
            w["governance_owned_by"] = {"ok": False, "error": "transport",
                                        "reply": []}
        assert "D6" not in [f["id"] for f in module._door_findings()], (
            "a FAILED ownership probe was counted as a measured empty answer — a "
            "failed envelope carrying `reply: []` is not an answer")
        for w in witness.values():
            w["governance_owned_by"] = {"reply": []}
        assert "D6" in [f["id"] for f in module._door_findings()], (
            "a measured EMPTY ownership answer no longer produces D6 — the arm "
            "above is vacuous")

        # ⛔ AND ON THE BARE-KIND DOOR (the D3 leg — #14691 review, 5th cycle,
        # N1 STILL-OPEN). D1/D2/D3/D5 arrive from `readback.json` as prose, and
        # nothing re-read the probes D3 cites: with BOTH `bare_kind_probes`
        # rewritten to error envelopes the suite stayed green and D3 — "a bare
        # kind returns an EMPTY list" — was still reported from two calls that
        # never answered.
        probes = readback.get("bare_kind_probes") or {}
        assert probes, "the readback carries no bare-kind probes to drive"
        measured = json.loads(json.dumps(probes))
        for name in probes:
            readback["bare_kind_probes"][name] = {"ok": False,
                                                 "error": "transport"}
        assert "D3" not in [f["id"] for f in module._door_findings()], (
            "D3 survived its own probes failing — a finding built on two error "
            "envelopes is a claim the measurement never made")
        readback["bare_kind_probes"] = measured
        assert "D3" in [f["id"] for f in module._door_findings()], (
            "D3 no longer appears on measured empty probes — the arm above is "
            "vacuous")
    finally:
        readback.clear()
        # `|=` rather than `.update(saved)`: `tools/sdk_privatise.py --check` is a RATCHET on
        # untyped `.update(`/`.query(`/`.delete(` receivers, and a dict this sweep cannot type
        # is one more row of that residue. `dict.__ior__` mutates in place and returns self, so
        # this restores the MODULE's own dict exactly as `.update` did — the repo's established
        # spelling for it (see the same note beside `tools/ci_selection.py`'s pins).
        readback |= saved


def test_the_isolation_refusal_is_enforced_on_the_whole_overview():
    """Reds if the fresh-graph refusal narrows back to two fields (N7 / item (d)).

    The committed receipt was the only reader of the premise and the producer's
    guard read two fields, so a mutated `empty_before` carrying
    `status.total_entities=500`, `audit.node_count=900` and a real `pointkinds`
    list — populated by every other measure the same reply carries — left 18/18
    green AND the producer's guard satisfied. The guard is a pure function over
    the WHOLE overview now, driven here directly.
    """
    keep_clause = importlib.util.spec_from_file_location(
        "t6_run_ingest", BUNDLE / "run_ingest.py")
    ingest = importlib.util.module_from_spec(keep_clause)
    keep_clause.loader.exec_module(ingest)

    before = _receipt()["empty_before"]
    # non-vacuity: the receipt's OWN overview must read as empty, or the guard
    # would refuse a real run (measured: the first cut of the walk returned
    # `{'stale.limit': 50}` — a page size — for this very receipt)
    assert ingest.occupancy(before) == {}, (
        "the receipt's own empty_before reads as populated, so the guard would "
        f"refuse a real run: {ingest.occupancy(before)}")
    # the PREDICATE's coverage only — the RAISE and the call site are pinned in
    # `test_the_refusal_FIRES_on_the_run_path`, so this file grades each half of
    # the guard exactly once.
    for dotted, value in (("taxonomy.Point", 5),
                          ("structure.total", 2),
                          ("status.total_entities", 500),
                          ("audit.node_count", 900),
                          ("pointkinds", ["product-strategy:product"])):
        mutant = json.loads(json.dumps(before))
        node = mutant
        *parents, leaf = dotted.split(".")
        for key in parents:
            node = node[key]
        node[leaf] = value
        assert ingest.occupancy(mutant), (
            f"the fresh-graph guard cannot see content at `{dotted}` — a "
            "populated graph would be measured as fresh")
    # ⛔ A DIAGNOSTIC CODE IS NOT CONTENT (#14691 review, 5th cycle, NEW-6).
    # `audit.exit_code` counts issues the audit check FOUND (as do `node_count`
    # and `summary.total_issues`, which stay counted); scoring it too refused a
    # legitimate run with the wrong reason. ⚠️ CONSTRUCTED case, not a measurement:
    # the receipt's own `empty_before` carries `exit_code: 0` and no committed
    # artifact carries `3` — an earlier revision claimed "measured" and an artifact
    # check falsified it. This arm drives the constructed case.
    diagnostic = json.loads(json.dumps(before))
    diagnostic["audit"]["exit_code"] = 3
    assert ingest.refuse_if_not_fresh(diagnostic) is None, (
        "a non-zero audit exit code is being read as graph CONTENT, so a "
        "diagnostic would refuse a legitimate run")


def test_an_UNREADABLE_kind_is_not_graded_correct():
    """Reds if an unclassifiable `produces` row falls through to "correct" (N6).

    #14691 review (5th cycle, N6): `orientation = "INVERTED" if
    src_kind_is_an_agent else "correct"` graded a row whose `src_kind` the scorer
    could not read as CORRECT, so ONE unreadable row moved
    `asymmetric_rows_correct` 7 -> 8 — inside the tally A2's verdict is read from.
    Measured: reverting that line left the whole suite green, because the committed
    artifact happens to carry no unreadable row — which is exactly why the arm has
    to construct one instead of reading the artifact.
    """
    module = _load_score_module()
    baseline = module.a2_edges()["totals"]
    assert baseline["asymmetric_rows_unclassifiable"] == 0, baseline
    row = {"doc": "test", "predicate": "produces", "write_path": "test",
           "src_id": "UNKNOWN-SRC", "dst_id": "UNKNOWN-DST",
           "src": "unknown-src", "dst": "unknown-dst",
           "src_kind": "", "dst_kind": ""}
    module.EDGES.append(row)
    try:
        mutated = module.a2_edges()["totals"]
    finally:
        module.EDGES.pop()
    assert mutated["asymmetric_rows_unclassifiable"] == 1, (
        "a `produces` row whose src_kind this scorer cannot read was graded "
        "instead of being reported UNCLASSIFIABLE")
    assert mutated["asymmetric_rows_correct"] == baseline["asymmetric_rows_correct"], (
        "an unreadable row was counted into asymmetric_rows_correct")


def test_the_A4_count_TABLE_and_the_S8_artifact_row_are_the_claim_layer():
    """Reds if the quote counts drift in either place §7's sentence does not cover.

    ⛔ #14691 review, 5th cycle, (b) — the residual half. `test_the_quote_breakdown
    _in_the_report_is_the_claim_layer` pins §7's flat sentence, and it holds. It
    does NOT reach the two places the same three numbers appear as a TABLE, and
    both were MEASURED GREEN AT HEAD 9ea2f606, before this arm existed:

      * §3's A4 table — `verbatim **11**`, `paraphrase **27**`, `invented **12**`
        rewritten to `**50** / `**0**` / `**0**` — the whole suite stayed green;
      * §8's artifact table — `its quote-fidelity audit (11 verbatim / 27
        paraphrase / 12 not in corpus)` rewritten to `(50 / 0 / 0)` — likewise.
      (Both red now: this arm.)

    A claim stated three times and guarded once is guarded in one place out of
    three. Each row is now pinned to the claim layer it quotes.
    """
    claim = _score()["claim_layer"]
    rows = {
        "verbatim": _report_line(r"^\| verbatim \(a real substring of the corpus\)"),
        "paraphrase": _report_line(r"^\| paraphrase \(≥60% of the quote"),
        "summary_in_one_turn": _report_line(r"^\| summary — the same share only"),
        "invented": _report_line(r"^\| \*\*no ≥60% support in any corpus turn\*\*"),
    }
    for bucket, row in rows.items():
        assert row, f"REPORT.md has no A4 count row for {bucket!r}"
        assert f"**{claim[bucket]}**" in row, (
            f"the A4 table's {bucket} row is not the claim layer's "
            f"({claim[bucket]}): {row!r}")

    # TWO rows name that artifact (§7's source list and §8's artifact table);
    # the one that STATES the figures is the one pinned here, and the other
    # stating a DIFFERENT figure would be caught by the sentence pin above.
    # ⛔ THE ROW CARRIES THE AUDIT'S OWN (SENTENCE-SCOPE) NUMBER, not `invented`
    # (#14723 review, round 10, P2) — the artifact counted what its rule counted.
    audit_row = _report_line(r"^\| `out/t6-quote-audit\.json` \|.*verbatim")
    assert audit_row, "REPORT.md §8 has no row for out/t6-quote-audit.json"
    want = (f"({claim['verbatim']} verbatim / {claim['paraphrase']} paraphrase / "
            f"{claim['below_sentence_band']} not in corpus)")
    assert want in audit_row, (
        f"§8's quote-audit row is not the claim layer's: expected {want!r} in "
        f"{audit_row!r}")


# ── the 6th review cycle: the artifact's EVIDENCE rows, and the prose ────
def test_the_D2_evidence_cell_cites_fields_that_EXIST():
    """Reds if D2's evidence cell names a path neither artifact has (6th review, N2).

    The cell cited `out/readback.json -> elements[].walk_root_kind`. Measured:
    `walk_root_kind` occurs **0** times in `readback.json` (the field is
    `root_kind`), and `walk_root_kind` actually lives in `out/t6-score.json`'s
    `A5_retrieval.probes[]` — a DIFFERENT artifact for a DIFFERENT population (the
    32 element walks vs the 5 retrieval walks). Replacing the whole cell with a
    path that does not exist at all stayed green, because only cells [2] and [3]
    of a finding row are parsed anywhere.

    A citation naming a non-existent field is the fail-open class this bundle
    exists to catch: a reader cannot falsify it without re-deriving by hand.
    (§6's "12 inverted-or-untyped relation classes", the same review's N1, is now
    pinned by the sibling table sweep and corrected to "15 inverted relation rows
    (13 distinct pairs)" — sourced from `A2_edges.totals`, so it needs no arm here.)
    """
    row = _report_line(r"^\| \*\*D2\*\* \|")
    assert row, "REPORT.md has no D2 row"
    cell = row.split("|")[4]
    # ⛔ SCAN ONLY THE CITATION, before the "⚠️ An earlier revision" note: that
    # note necessarily QUOTES the stale field name, so a substring test over the
    # whole cell is satisfied by the correction itself (measured).
    citation = cell.split("⚠️")[0]
    assert "root_kind" in citation and "A5_retrieval" in citation, (
        "the D2 evidence cell must name the element walk's `root_kind` AND the "
        f"retrieval artifact's `A5_retrieval`: {citation[:220]!r}")
    readback = _readback()
    for named in ("unresolved_elements", "elements"):
        assert named in readback, f"readback.json has no `{named}`"
    assert all("root_kind" in e for e in readback["elements"]), (
        "elements[] no longer carries `root_kind`, so the cell's citation is stale")
    assert "walk_root_is_entity" in _score()["A5_retrieval"]["probes"][0], (
        "A5's probes no longer carry `walk_root_is_entity`, so the cell's "
        "retrieval citation is stale")

def test_the_A1_caveat_cannot_claim_the_table_is_traceable():
    """Reds if the A1 caveat asserts traceability of the table it sits beside.

    ⛔ THIS PARAGRAPH HAS NOW BROKEN TWICE IN TWO DIFFERENT SCOPES (8th and 9th
    fresh-context reviews). It first said the over-typed extras were "precision,
    all traceable to text" while `A4_no_invention.untraceable_entities` names TWO
    of them — `the seven-component dependency graph` and `ownership
    accountability convention` — and `VERIFICATION-independent.md:32` says "the
    two untraceable names". The 8th-cycle fix moved the qualifier from "the
    extras" to "every row in the table below IS stated in the text" — and TWO OF
    THE TABLE'S OWN FIVE ROWS ARE THOSE TWO NAMES, so the sentence refuted itself
    in its next clause.

    The discriminating power a docstring claims has to be the power its
    assertions have: the first version of this arm asserted only that the figure
    string was PRESENT, so it could not see the blanket claim beside it
    (measured: `Every row in the table below IS stated in the text` ->
    `... is a verbatim corpus phrase` stayed green). It now pins the TRUE
    relationship — that the untraceable names are rows of this table — and
    rejects the blanket shape outright.
    """
    a4 = _score()["A4_no_invention"]
    untraceable = a4["untraceable_counts"]["all_entity_names"]
    checked = a4["entity_names_checked"]
    names = sorted(u["name"] for u in a4["untraceable_entities"])
    assert names == ["ownership accountability convention",
                     "the seven-component dependency graph"], names

    caveat = _report_paragraph(r"^\*\*\\\* The component type is a caveat")
    assert caveat, "REPORT.md has no A1 caveat paragraph"

    # (a) the TRUE relationship: these two are rows of the table below it
    assert "two of its five rows are the bundle's own untraceable names" in caveat, (
        "the caveat no longer states that two of the TABLE's own rows are the "
        "untraceable ones — that is the self-refuting residue, and narrowing the "
        f"qualifier is how it recurred: {caveat[:300]!r}")
    assert f"**{untraceable} of the {checked} entity names the" in caveat, (
        f"the caveat does not state A4's own count ({untraceable} of {checked})")
    for name in names:
        assert f"`{name}`" in caveat, f"the caveat does not name {name!r}"

    # (b) the blanket claim, in either of the two shapes it has taken
    assert "IS stated in the text" not in caveat, (
        "the caveat has re-asserted blanket traceability of the table")
    assert "all traceable to text" not in caveat, (
        "the caveat has re-asserted 'all traceable to text'")
def test_the_over_typed_table_matches_the_paragraph_that_describes_it():
    """Reds if the A1 over-typed table is edited out from under its own caveat.

    ⛔ THE PARAGRAPH CAN BE RE-FALSIFIED BY A TABLE EDIT (#14691, 10th review,
    finding 1). The caveat says "two of its five rows are the bundle's own
    untraceable names" — but nothing read the table, so measured GREEN (60
    passed): deleting the `ownership accountability convention` row (making "five"
    false), adding a sixth row, flipping the untraceable row's kind, or renaming
    `control-room customers`. That is the exact mode that broke this paragraph in
    cycles 8 and 9 (a claim beside the table disagreeing with the table), so the
    table itself is now derived.

    It also pinned a band figure the scorer persists: REPORT's "(11 below 0.5)" had
    no artifact behind it until `claim_layer.bands` existed.
    ⛔ THAT BAND BLOCK WAS GRADING THE REVIEW LOG, and it now lives in
    `test_the_CLAIM_layer_and_A2_figures_are_the_artifacts` (#14723, fix lane at
    `4944c705`): the sentences it searched for existed ONLY in §8's review log (§8.2 at that
    head — not in §3/A4, the section they are a claim about), so a
    whole-document `flat` pin was satisfied by the log that records it, and this
    arm — named "matches the paragraph that describes it" — graded a subject its
    docstring never named. Measured AT `4944c705` (`### 8.1`..`### 8.18` was then
    the whole log): stripping every `### 8.x` round section left this arm the only
    red in the file. That number belongs to THAT head — re-run at `05b3d7c0` the
    same sweep leaves `102 passed`, because this arm no longer reads the log at all,
    and the head-independent statement is therefore the stronger one: replacing the
    WHOLE log with a digit-free marker leaves `102 passed`, i.e. no arm depends on
    it. The histogram is a §3/A4 claim and is graded there.
    """
    a4 = _score()["A4_no_invention"]
    untraceable = {u["name"]: u["kind"] for u in a4["untraceable_entities"]}
    caveat = _report_paragraph(r"^\*\*\\\* The component type is a caveat")
    assert caveat, "REPORT.md has no A1 caveat paragraph"

    # (a) THE CAVEAT'S OWN TABLE, read as ROWS: from its header to the first
    # blank line, split on `|`, name tokens from cell 0, kind from cell 1. A
    # regex keyed on ONE cell shape found only 2 of the 5 rows — one name cell is
    # ``**and**``-joined and two kinds carry no namespace colon — which made the
    # size check below vacuous (measured).
    lines = _report().splitlines()
    header = next(i for i, line in enumerate(lines)
                  if re.match(r"^\| node \| typed as \|", line))
    table = []
    for line in lines[header + 1:]:
        if not line.strip():
            break
        if set(line.strip()) <= set("|- "):
            continue
        cells = [c.strip() for c in line.split("|")[1:-1]]
        assert len(cells) >= 2, line
        names = re.findall(r"`([^`]+)`", cells[0]) or [cells[0]]
        table.append((names, cells[1].strip("`")))
    assert table, "the over-typed table has no parseable rows"

    # (b) its size must equal the number the paragraph uses
    assert len(table) == 5, (
        f"the over-typed table has {len(table)} rows, not the five the caveat "
        f"claims: {[n for n, _ in table]}")
    assert "two of its five rows" in caveat, (
        "the caveat no longer says 'two of its five rows' — if the table size "
        "changed, this sentence has to change with it")

    # (c) the two untraceable names must be ROWS of it, with the artifact's kinds
    by_name = {n: k for names, k in table for n in names}
    for name, kind in untraceable.items():
        assert name in by_name, (
            f"the caveat says {name!r} is one of the table's rows, but the table "
            f"has no such row: {sorted(by_name)}")
        assert by_name[name] == kind, (name, by_name[name], kind)

    # (d) the remaining rows ARE stated in the text (so the caveat's "other
    # three" is true) and are not themselves untraceable
    stated_rows = [(names, k) for names, k in table
                   if not set(names) & set(untraceable)]
    assert len(stated_rows) == 3, (stated_rows, list(untraceable))
    corpus = " ".join(" ".join(n.lower().split())
                      for d in _corpus()["documents"] for n in
                      (t["content"] for t in d["turns"]))
    for names, _kind in stated_rows:
        for name in names:
            assert " ".join(name.lower().split()) in corpus, (
                f"the caveat calls {name!r} one of the rows stated in the text, "
                "but its name is not in the corpus")


def test_the_remaining_prose_figures_are_the_artifacts_figures():
    """Reds on the three figures that sat outside every pin (3rd review, P3).

    Measured green before this arm: §2's POST-ingest taxonomy block (all five
    counts -> 99), the sentence-scope relation figure (`8 of 46` -> `3 of 46`),
    and every tool NAME in `out/tool-surface.json` (39 names -> `junk_tool_N`,
    count preserved). The last one makes `VERIFICATION-independent.md:14`'s
    "lists 39 tools and neither name" unfalsifiable, which is worse than an
    unguarded number: it is a claim about a NAMED absence.
    """
    receipt = _receipt()
    a4 = _score()["A4_no_invention"]

    # (a) the graph's shape AFTER the write, from the receipt that recorded it
    after = (receipt.get("graph_overview_after") or {}).get("taxonomy")
    assert after, "the receipt records no post-ingest taxonomy"
    row = _report_line(r'^"taxonomy"')
    assert row, "REPORT.md has no post-ingest taxonomy line"
    assert json.dumps(after) in row, (
        f"§2's post-ingest taxonomy block is not the receipt's: expected "
        f"{json.dumps(after)!r} in {row!r}")

    # (b) the stricter relation figure — 8 of 46 are not co-stated in one SENTENCE
    sentence_scope = a4["relations_not_co_stated_at_SENTENCE_scope"]
    relations = _score()["edge_count"]
    flat = " ".join(_report().split())
    assert (f"{sentence_scope} of {relations} are not co-stated within a single "
            f"*sentence*") in flat, (
        f"the sentence-scope figure is not the artifact's "
        f"({sentence_scope} of {relations})")

    # (c) the tool NAMES, not just their count: the documents cite the ABSENCE of
    # two specific tools, and an absence claim is only checkable if the list is.
    tools = json.loads((BUNDLE / "out" / "tool-surface.json").read_text())
    assert len(tools) == 39, len(tools)
    for name in ("tortoise_get_entity", "tortoise_search_knowledge",
                 "tortoise_explore_connections", "tortoise_graph_overview",
                 "tortoise_mine_knowledge_from_session"):
        assert name in tools, (
            f"{name} is missing from the committed MCP surface, so a document "
            "that describes it is describing a tool this surface does not have")
    for absent in ("tortoise_recall", "tortoise_get_governance"):
        assert absent not in tools, (
            f"{absent} IS advertised on the committed surface, so "
            "VERIFICATION-independent.md:14's 'neither name' claim is false")


def test_the_score_artifact_is_what_the_scorer_DERIVES_from_its_inputs():
    """Reds if `out/t6-score.json` stops being `score_t6.build_score()`'s output.

    ⛔ #14691 review, 6th cycle, finding 2 (P1). The 5th cycle's lesson — "nothing
    in the tree opened this file" — was applied to `t6-quote-audit.json` and not
    to the PRIMARY artifact. Everything a test did not happen to name was free,
    measured green on head 43fbd256, each mutant alone:

      * `corpus_chars` 4,295 → 1 (while REPORT says "4,295 characters");
      * `A4_no_invention.warnings` {36,26,1,2} → {3,0,0,0} (while REPORT says
        "26 of 36 warnings");
      * `edge_count` 46 → 999 (while REPORT says "0 of 46 relations");
      * every `A2_edges.ownership[].verdicts` emptied and `present` flipped — the
        EVIDENCE for the bundle's headline FAIL;
      * `extraction_tokens.by_doc`, `entity_names_checked` 35 → 1, every A5
        probe's `first_rank_in_search`/`top8`, `per_type.team.right_kind` → 0,
        `door_findings[0].evidence`.

    `build_score()` is a pure function of the committed corpus/receipt/readback
    (it writes nothing), so the whole artifact is checkable: one mutation to any
    derived field reds here, including the two that otherwise contradict
    REPORT.md while both stay green.
    """
    module = _load_score_module()
    derived = json.loads(json.dumps(module.build_score(), default=str))
    committed = _score()
    if derived != committed:
        diverged = sorted(k for k in set(derived) | set(committed)
                          if derived.get(k) != committed.get(k))
        raise AssertionError(
            "out/t6-score.json is no longer what score_t6.py derives from the "
            "committed corpus/receipt/readback — re-run the scorer instead of "
            f"editing the artifact. Diverging top-level keys: {diverged}")
    # non-vacuity: the derivation must actually produce the evidence the review
    # found rewritable, or this arm would pass on a score with those fields gone.
    assert committed["corpus_chars"] and committed["edge_count"]
    assert committed["A4_no_invention"]["warnings"]["total"] > 0
    assert all(item["verdicts"] for item in committed["A2_edges"]["ownership"])
    assert all(p["top8"] is not None for p in committed["A5_retrieval"]["probes"])


def test_the_example_BUCKETS_are_the_bands_their_names_claim():
    """Reds if the paraphrase/invented example lists are swapped or emptied.

    ⛔ #14691 review, 6th cycle, finding 3 (P2). The COUNT half of N5 holds; the
    BUCKET identity was unguarded — measured green on 43fbd256: swapping
    `invented_examples` with `paraphrase_examples` (overlap bands 0.2-0.4 ↔
    0.636-1.0) and emptying both lists left **28 passed**, while REPORT §8.1
    states "the paraphrase rows live under `paraphrase_examples`" and §3 quotes
    three of the invented rows as examples of invention.
    """
    quotes = _score()["A4_no_invention"]["quotes"]
    cap = 10
    for bucket, band in (("invented_examples", "below"),
                         ("paraphrase_examples", "at_or_above")):
        rows = quotes[bucket]
        assert rows, f"{bucket} is empty — the artifact shipped no examples"
        assert len(rows) <= cap, (bucket, len(rows))
        for row in rows:
            over = row["best_sentence_overlap"]
            if band == "below":
                assert over < 0.6, (bucket, over)
            else:
                assert 0.6 <= over <= 1.0, (bucket, over)
    # ⛔ AND THE THIRD BUCKET IS BOUNDED ON BOTH SIDES (#14723 review, round 10,
    # P2): a summary is a row the SENTENCE band rejects and the TURN band supports.
    # A row that satisfies only the first half is an invention, so asserting the
    # upper bound is what makes the bucket name true.
    for row in quotes["summary_examples"]:
        assert row["best_sentence_overlap"] < 0.6, row
        assert row["best_turn_overlap"] >= 0.6, row
    # and the truncation is DECLARED, not implied by a silent cap — PER LIST
    # (#14691, merged lanes). This arm originally asserted ONE shared
    # `examples_truncated` flag; a single boolean cannot say WHICH sample is
    # incomplete, which is the same "a length is not a count" defect the counts
    # above exist to close. Each bucket now states its own.
    for bucket in ("invented", "paraphrase"):
        examples = quotes[f"{bucket}_examples"]
        expected = quotes[bucket] > len(examples)
        assert quotes[f"{bucket}_examples_truncated"] is expected, (
            bucket, quotes[f"{bucket}_examples_truncated"], expected,
            quotes[bucket], len(examples))


def test_the_quote_bucket_is_decided_by_the_TURN_and_the_corpus_STATES_them():
    r"""Reds if the band is decided by ONE SENTENCE, grading a summary as invented.

    ⛔ #14723 review, round 10, P2. `best_overlap` looked only inside a single
    corpus sentence, so a faithful MULTI-FACT summary — which by construction
    cannot put ≥60% of its words into one sentence — was graded `invented`, and
    REPORT's gloss ("<60% overlap — the corpus never says it") asserted a property
    the measure could not establish. MEASURED on the pre-fix code: this suite's
    86 arms were green while `invented_examples` carried
    `Three teams exist: Platform (engineering), Product (product management), and
    Customer (commercial).` at 0.30 — a sentence whose every fact the corpus
    states. On that code `quotes["summary_examples"]` does not exist at all, so
    this arm reds there.

    The decisive arm is the last one: each summary's own content words must be
    present in the corpus, so the bucket is not a licence to wave a claim through.
    """
    module = _load_score_module()
    quotes = module.a4_invention()["quotes"]
    summaries = quotes["summary_examples"]
    assert len(summaries) == quotes["summary_in_one_turn"] > 0, quotes
    assert (quotes["invented"] + quotes["summary_in_one_turn"]
            == quotes["below_sentence_band"]), quotes
    # the row the review named is IN the summary bucket, not the invented one
    named = [r for r in summaries if r["quote"].startswith("Three teams exist")]
    assert named, [r["quote"][:60] for r in summaries]
    assert named[0]["best_sentence_overlap"] < 0.6 <= named[0]["best_turn_overlap"]
    assert all(r["best_turn_overlap"] >= 0.6 for r in summaries), summaries
    # ...and the invented bucket did NOT shrink to let it through: every row there
    # is unsupported in EVERY corpus turn, which is the claim the prose makes.
    assert all(r["best_turn_overlap"] < 0.6 for r in quotes["invented_examples"])
    # ⛔ NON-VACUITY OF THE SWING: the corpus supplies the summarised CONTENT. The
    # band is a bag-of-words share over a turn, so it says "these facts are stated
    # in one turn", never "every token appears" — the extractor's own framing words
    # (`exist`, `owned`, `recorded`) are what it does not account for. What must
    # hold is that the two rows the REVIEW named carry their facts, and that the
    # invented bucket is SEPARATED from the band rather than sitting on its edge.
    corpus = json.loads((BUNDLE / "corpus" / "structure-corpus.json").read_text())
    text = " ".join(t["content"] for d in corpus["documents"] for t in d["turns"])

    def words(s):
        return set(re.findall(r"[a-z]{3,}", " ".join(s.lower().split())))

    corpus_words = words(text)
    by_quote = {r["quote"]: r for r in summaries}
    for prefix, facts in (
            ("Three teams exist", {"three", "teams", "platform", "engineering",
                                  "product", "management", "customer",
                                  "commercial"}),
            ("Enterprise renewals", {"enterprise", "renewals", "jonas", "bauer",
                                     "account", "executive"})):
        row = next((r for q_, r in by_quote.items() if q_.startswith(prefix)), None)
        assert row, f"the review's {prefix!r} row is not in the summary bucket"
        assert facts <= corpus_words, sorted(facts - corpus_words)
        assert len(words(row["quote"]) - corpus_words) <= 1, row["quote"][:80]
    # the invented bucket must sit BELOW the band with margin, not on it
    assert all(r["best_turn_overlap"] < 0.5 for r in quotes["invented_examples"]), (
        "an invented row sits inside half a band of the cut, so the rule is a hair "
        "away from grading a recombined claim as stated",
        [(r["best_turn_overlap"], r["quote"][:50])
         for r in quotes["invented_examples"]])
    assert all(r["best_turn_overlap"] >= 0.6 for r in summaries), summaries

def test_each_door_finding_ANSWER_is_the_artifacts_answer_in_SUBSTANCE():
    r"""Reds if a REPORT answer keeps its leading "No" and reverses its claim.

    ⛔ #14691 review, 6th cycle, finding 4 (P2). `_polarity` reads the FIRST word,
    so measured green on 43fbd256: D6's answer
    `**No — `[]` for all four owners**, on a graph holding 6 `ownedBy` rows.`
    → `**No — it returns every owned row for all 4 owners**, on a graph holding 6
    …` — the bundle's headline mechanism finding inverted, with the leading "No"
    kept so the polarity arm stayed quiet.

    The answer cell must now CONTAIN the artifact's own answer text (markdown
    stripped, whitespace flattened), so a reversal cannot keep a "No" beside it.
    """
    def _flat(text: str) -> str:
        return " ".join(re.sub(r"[^a-z0-9]+", " ", str(text).lower()).split())

    for finding in _score()["door_findings"]:
        row = _report_line(rf"^\| \*\*{finding['id']}\*\* \|")
        assert row, f"REPORT.md has no row for door finding {finding['id']}"
        answer = _flat(row.split("|")[3])
        artifact = _flat(finding["answer_over_mcp"])
        assert artifact, finding["id"]
        # ⛔ THE WHOLE ANSWER, not its opening words (#14691 review, 7th cycle,
        # finding 3). A six-word key stops BEFORE the claim: D6's key was
        # "no it returns for all 4", so `… it returns for all 4 owners asked the
        # rows each of them owns` — the door WORKING — kept the key and passed.
        assert artifact in answer, (
            f"door finding {finding['id']}'s ANSWER in REPORT.md does not carry "
            f"the artifact's answer: report {answer[:140]!r} vs artifact "
            f"{artifact[:140]!r}")


def test_an_UNREADABLE_kind_is_not_graded_INVERTED_either():
    """Reds if the `managedBy` mirror of N6 grades an unreadable row INVERTED.

    ⛔ #14691 review, 6th cycle, finding 6 (P2). `produces` got its UNCLASSIFIABLE
    bucket; `managedBy` did not — `dst_kind in subject_kinds` is False for an
    absent `dst_kind`, so an unread row was asserted to be *the reverse of the
    corpus* from a kind the scorer never read, and moved the very tallies A2's
    tally paragraph is pinned to (`inverted` 15 → 16, distinct pairs 13 → 14).
    Measured on 43fbd256: nothing reddened.
    """
    module = _load_score_module()
    baseline = module.a2_edges()["totals"]
    assert baseline["asymmetric_rows_unclassifiable"] == 0, baseline
    row = {"doc": "test", "predicate": "managedBy", "write_path": "test",
           "src_id": "UNKNOWN-SRC", "dst_id": "UNKNOWN-DST",
           "src": "unknown-src", "dst": "unknown-dst",
           "src_kind": "", "dst_kind": ""}
    module.EDGES.append(row)
    try:
        mutated = module.a2_edges()["totals"]
    finally:
        module.EDGES.pop()
    assert mutated["asymmetric_rows_unclassifiable"] == 1, (
        "a `managedBy` row whose kinds this scorer cannot read was graded "
        "instead of being reported UNCLASSIFIABLE")
    assert mutated["asymmetric_rows_inverted"] == baseline["asymmetric_rows_inverted"], (
        "an unreadable `managedBy` row was counted as INVERTED, so the artifact "
        "asserts the reverse of the corpus from a kind it never read")
    assert mutated["asymmetric_pairs_inverted"] == baseline["asymmetric_pairs_inverted"], (
        "an unreadable `managedBy` row entered the distinct-pair count")


def test_report_figures_across_the_sections_are_the_artifacts_figures():
    """Reds if a number anywhere in REPORT.md drifts from the artifact it quotes.

    ⛔ #14691 review, 6th cycle, finding 1 (P1). The N4 arms pinned the A1 and A3
    HEADINGS and nothing else, so on head 43fbd256 each of these rewrites alone
    stayed green (measured, one at a time): the A5 heading's `5/5`/`0/5`, §2's
    isolation block (`"total_entities": 0` → 15, the taxonomy counts → Point 7/…),
    §2's per-document cost/throughput row, §3's `8 hasPart write rows for 5
    distinct pairs`, `26 of 36 warnings`, `0 of 46 relations`, §7's `4 ownedBy
    pairs`, §0's `six ownership edges`, and §8's `(39 tools)`.

    Most pointed: the isolation block is the run's own premise — the report could
    claim the graph held 15 entities BEFORE the first write while the receipt-level
    pin stayed green, because that pin grades the RECEIPT and not the prose.

    Every figure below is derived from the committed artifact on the left of the
    `|`, so a scorer change reds here and the prose gets updated with it.
    """
    score = _score()
    receipt = _receipt()
    a1 = score["A1_type_coverage"]
    a3 = score["A3_hierarchy"]["reported"]
    a4 = score["A4_no_invention"]
    a5 = score["A5_retrieval"]["totals"]
    tokens = score["extraction_tokens"]
    before = receipt["empty_before"]
    tools = json.loads((BUNDLE / "out" / "tool-surface.json").read_text())
    ownedby_rows = sum(
        1 for doc in receipt["documents"]
        for e in (((doc.get("result") or {}).get("relation_write") or {})
                  .get("edges") or []) if e.get("predicate") == "ownedBy")
    ownedby_pairs = sum(1 for o in score["A2_edges"]["ownership"] if o["present"])

    def line(pattern, expected, why):
        got = _report_line(pattern)
        assert got, f"REPORT.md has no line matching {pattern!r} ({why})"
        assert expected in got, (
            f"{why}: REPORT.md does not state the artifact's {expected!r} — "
            f"{got!r}")

    # §0 / §1 / §2 — the premise and the corpus
    line(r"^.*all (?:six|seven|\w+) ownership edges",
         f"all {_spell(ownedby_rows)} ownership edges",
         "§0's ownership-edge count")
    line(r"^.*4,295 characters", f"{receipt['corpus_chars']:,} characters",
         "§1's corpus size")
    line(r"^.*named elements, .* containment pairs",
         f"{a1['declared']} named elements, {a3['hasPart_distinct_pairs']} "
         "containment pairs", "§1's declared-structure counts")
    counts = ", ".join(f'"{k}": {v}' for k, v in before["taxonomy"].items())
    line(r'^"counts": \{', f'"counts": {{{counts}}}', "§2's pre-write taxonomy")
    line(r'^"total_entities":', f'"total_entities": {before["status"]["total_entities"]}',
         "§2's pre-write entity total")

    # §2 — the per-document cost/throughput row
    doc1 = tokens["by_doc"]["doc-01"]
    line(r"^\| doc-01 \|",
         f"| {doc1['calls']} | {doc1['prompt_tokens']:,} | "
         f"{doc1['completion_tokens']:,} |", "§2's doc-01 cost row")
    # ⛔ THIS USED TO BE A PIN THAT NEVER RAN (3rd fresh-context review, P2).
    # It read `line(... if False else r"^.*prompt tokens", ...) if _report_line(
    # r"^.*prompt tokens") else None` — and REPORT.md contains no line matching
    # `prompt tokens`, so `_report_line` returned "" and the ternary skipped the
    # call entirely. The two bare-token asserts below then accepted ANY document
    # containing "130,804" and "17,137" anywhere, which is the weakened form §8.1
    # claims to have retired. Measured: `(130,804 prompt + 17,137 completion)` ->
    # `(130,805 … 17,136 …)` (arithmetic still summing to 147,941) stayed green.
    line(r"^\| extraction tokens \|",
         f"**{tokens['total_tokens']:,}** ({tokens['prompt_tokens']:,} prompt + "
         f"{tokens['completion_tokens']:,} completion) over "
         f"**{tokens['calls']}** model calls",
         "§2's extraction-token line")

    # §3 — the assertion counts
    line(r"^⚠️ \*\*\d+ `hasPart` write rows",
         f"**{a3['hasPart_write_rows']} `hasPart` write rows for "
         f"{a3['hasPart_distinct_pairs']} distinct pairs**",
         "§3's hasPart duplication counts")
    line(r"^.*of \d+ warnings",
         f"`{a4['warnings']['no_source_turn']}` of {a4['warnings']['total']} warnings",
         "§3's no-source-turn warning count")
    line(r"^.*of \d+ relations",
         f"{len(a4['relations_not_co_stated'])} of {score['edge_count']} relations",
         "§3's un-co-stated relation count")
    line(r"^### A5 · ",
         f"{a5['answered_by_search_text']} by text, "
         f"{a5['walk_resolved_to_an_ENTITY']} by structure", "§3/A5's heading")

    # §7 / §8 — the corroboration's own figures
    line(r"^.*the same \*\*\d+\*\* `ownedBy` pairs",
         f"the same **{ownedby_pairs}** `ownedBy` pairs", "§7's ownedBy pair count")
    line(r"^.*the same `dev:code` typing for all",
         f"the same `dev:code` typing for all "
         f"{_spell(a1['per_type']['component']['right_kind'])} components",
         "§7's dev:code typing count")
    line(r"^\| `out/tool-surface\.json` \|",
         f"({len(tools)} tools)", "§8's advertised-tool count")


def test_the_reports_number_TABLES_are_the_artifacts_tables():
    r"""Reds if a row of REPORT.md's number TABLES drifts from its artifact.

    ⛔ #14691 review, 6th cycle, finding 1 (P1) pinned the HEADING figures and §2's
    `doc-01` cost row, and left the tables that carry the same numbers. Measured
    on head 1de0815 by mutating ONE number per REPORT.md line and re-running this
    suite: **91 of 121 numeric lines stayed GREEN**, among them four rows of §2's
    per-document cost table, the whole post-write overview block, §3/A1's kind
    table, §3/A2's declared-side cells, §3/A4's "0 of 28 declared" and §5/A5's
    rank column — every one of them a figure a reader takes as the measurement.

    Each cell below is DERIVED from the committed artifact named beside it, so a
    scorer change reds here and the prose gets updated with it.
    """
    score, receipt = _score(), _receipt()
    tokens = score["extraction_tokens"]
    a1, a2 = score["A1_type_coverage"], score["A2_edges"]
    a4, a5 = score["A4_no_invention"], score["A5_retrieval"]
    corpus_decl = _corpus()["_declared_structure"]
    after = receipt["graph_overview_after"]

    def cell(pattern, expected, why):
        got = _report_line(pattern)
        assert got, f"REPORT.md has no line matching {pattern!r} ({why})"
        assert expected in got, (
            f"{why}: REPORT.md does not carry the artifact's {expected!r} — {got!r}")
        # ⛔ THE DUPLICATE-FIGURE HOLE IS *NOT* CLOSED HERE, and this comment used to say it
        # was (#14691 review, round 15, finding 3 — measured: the helper it named was
        # called from nowhere in 4,900 lines). `expected in got` is a SUBSTRING test, so a
        # row that states a figure twice keeps one copy pinned and leaves the other free.
        # The measured instance — `| … **5/5** | 5/5 (parent end…`, where mutating EITHER
        # copy left `97 passed` — is closed by its OWN arm,
        # `test_the_A2_class_table_derives_BOTH_cells_from_the_artifact`, which derives both
        # columns from `A2_edges.totals`; a generic multiplicity bound was tried and
        # REFUSED because it reds on honest repeats (`| doc-02 | 14.6 | 4 | … | 4 (8
        # `related`, 4 `ownedBy`) |`, measured). So a duplicate in another row needs its
        # own arm: this helper carries none, and nothing here pretends it does.

    def para(pattern, expected, why):
        """A claim whose sentence WRAPS: grade the whole paragraph, not a line."""
        got = _report_paragraph(pattern)
        assert got, f"REPORT.md has no paragraph matching {pattern!r} ({why})"
        assert expected in got, (
            f"{why}: REPORT.md does not carry the artifact's {expected!r} — {got!r}")

    # §1 — the graph under test, bound to the receipt's own record of where it ran
    cell(r"^\| Graph under test \|", f"`{receipt['graph_name']}`",
         "§1's graph name")
    cell(r"^\| Graph under test \|",
         f"`{receipt['db_uri'].rsplit('/', 1)[0].rsplit('@', 1)[-1]}`",
         "§1's host:port")

    # §1 — the corpus's declared structure, counted from the fixture itself
    para(r"^\*\*4,295 characters",
         (f"{len(corpus_decl['ownership'])} ownership pairs, "
          f"{len(corpus_decl['dependencies'])} dependency pairs, "
          f"{len(corpus_decl['serves'])} serves pairs"),
         "§1's declared pair counts")

    # §2 — EVERY row of the per-document table, not only doc-01's
    written_total = 0
    for doc in receipt["documents"]:
        doc_id = doc["doc_id"]
        by_doc = tokens["by_doc"][doc_id]
        rel = doc["result"].get("relation_write") or {}
        edges = len(rel.get("edges") or [])
        written_total += edges
        cell(rf"^\| {doc_id} \|",
             (f"| {round(doc['ack_seconds'], 1)} | {by_doc['calls']} | "
              f"{by_doc['prompt_tokens']:,} | {by_doc['completion_tokens']:,} | "
              f"{len(doc['result'].get('points') or [])} |"),
             f"§2's {doc_id} cost row")
    acks = [d["ack_seconds"] for d in receipt["documents"]]
    cell(r"^\| \*\*total\*\* \|",
         (f"| **{round(sum(acks) / len(acks), 1)} avg** | **{tokens['calls']}** | "
          f"**{tokens['prompt_tokens']:,}** | **{tokens['completion_tokens']:,}** | "
          f"{sum(len(d['result'].get('points') or []) for d in receipt['documents'])} "
          f"| **{written_total}** |"),
         "§2's total row")
    assert written_total == score["edge_count"], (
        "the receipt's written relations do not sum to the scorer's edge_count: "
        f"{written_total} vs {score['edge_count']}")

    # §2 — the post-write overview block, quoted from the receipt. The taxonomy
    # LINE itself is owned by `test_the_remaining_prose_figures_are_the_artifacts_figures`
    # (the parallel lane's arm, kept rather than duplicated); the three cells below
    # are this table's and had no pin.
    cell(r'^"structure":',
         f'"operators": {after["structure"]["operators"]}, '
         f'"total": {after["structure"]["total"]}', "§2's post-write structure")
    kinds = ", ".join(
        f'{{"kind": "{k["kind"]}", "count": {k["count"]}}}'
        for k in after["pointkinds"])
    cell(r'^"pointkinds":', kinds, "§2's post-write point kinds")
    sessions = [s for s in after["sources"] if s.get("sourceKind") == "agentSession"]
    per_source = [s["points"] for s in sessions]
    cell(r'^"sources":',
         f'{len(sessions)} agentSession sources, {min(per_source)}\u2013'
         f'{max(per_source)} points each', "§2's post-write source population")

    # §3 — EVERY row of A1's kind table
    for role, row in a1["per_type"].items():
        star = "*" if role == "component" else ""
        cell(rf"^\| {role} \| \d",
             f"| {role} | {row['declared']} | {row['present']} | "
             f"{row['right_kind']}{star} |", f"§3/A1's {role} row")

    # §3 — A2's DECLARED-side cells (the oriented side is pinned above)
    cell(r"^\| product → feature", a2["totals"]["containment_present"],
         "§3/A2's hasPart row")
    cell(r"^\| person → team", a2["totals"]["membership_present"],
         "§3/A2's membership row")
    cell(r"^\| person → role", a2["totals"]["role_links_present"],
         "§3/A2's role-link row")
    cell(r"^\| dependent → dependency", a2["totals"]["dependency_present_and_correct"],
         "§3/A2's dependency row")
    ownedby_rows = sum(
        1 for doc in receipt["documents"]
        for e in (((doc.get("result") or {}).get("relation_write") or {})
                  .get("edges") or []) if e.get("predicate") == "ownedBy")
    cell(r"^\| thing → owner",
         f"{a2['totals']['ownership_present']}** ({ownedby_rows} rows)",
         "§3/A2's ownership declared-side cell")
    produces = [o for o in a2["other"] if o["edge"]["predicate"] == "produces"]
    produced = f"{len(produces)}/{len(produces)}"
    inverted = sum(1 for o in produces if o["orientation"] == "INVERTED")
    cell(r"^\| team → component", produced, "§3/A2's produces declared-side cell")
    cell(r"^\| team → component",
         f"**{len(produces) - inverted}/{len(produces)} — every",
         "§3/A2's produces oriented cell")

    # §3/A4 — the traceability sentence's declared denominator
    wording = _report_paragraph(r"^\*\*As the issue words it\*\*")
    assert wording, "REPORT.md has no A4 'as the issue words it' paragraph"
    declared_untraceable = (
        f"{len(a4['declared_structure_untraceable'])} of {a1['declared']} declared "
        "structure elements untraceable")
    assert declared_untraceable in wording, (
        "§3/A4's declared traceability does not state the artifact's count "
        f"{declared_untraceable!r}: {wording[:200]!r}")

    # §5 — the rank column of A5's table, per probe
    for probe in a5["probes"]:
        ranks = [probe["first_rank_in_search"].get(e) for e in probe["expected"]]
        unique = sorted({r for r in ranks if r is not None})
        row = _report_line(
            rf"(?i)^\| {re.escape(probe['query'].rstrip('?').lower())[:40]}")
        assert row, f"REPORT.md has no A5 row for {probe['element']}"
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        text = cells[3]
        stated = sorted(int(n) for n in re.findall(r"\d+", text))
        assert stated == unique, (
            f"§5's rank cell for {probe['element']} is {text.strip()!r} while the "
            f"artifact's first ranks are {unique} (expected {probe['expected']})")


def test_the_second_measurements_CORROBORATION_fields_are_pinned():
    """Reds if the fields the corroboration is CITED for are rewritten.

    ⛔ #14691 review, 6th cycle, finding 5 (P2) — the (b) residual. The arm that
    pins `t6-independent-verification.json` asserts its `declared` list and its
    `A2` verdict; measured green on 43fbd256, each alone: every one of the nine
    governance answers set to `[{"name": "something owned"}]` (i.e. the door DID
    answer — the opposite of D6), `A2_edge_row_counts_from_receipt` ownedBy 6→0
    and hasPart 8→0, `advertised_tools` 39→99, `A1.typed_nodes['dev:code']` 7→0
    — while REPORT still cites those very numbers.

    Each field is pinned to the artifact it corroborates, so it cannot disagree
    with the primary record it exists to confirm.
    """
    indep = json.loads((BUNDLE / "out" / "t6-independent-verification.json").read_text())
    score = _score()
    receipt = _receipt()
    # (1) the ownership-door witness that corroborates D6 — all nine empty, and
    #     the four owners present
    gov = indep["witness_governance_door_returns_empty"]
    assert all(v == [] for v in gov.values()), gov
    assert {"Amara Osei", "Dana Whitlock", "Tomas Ferreira", "Jonas Bauer"} <= set(gov), gov
    # (2) the receipt-derived row counts, counted here from the receipt
    counted = {}
    for doc in receipt["documents"]:
        for edge in (((doc.get("result") or {}).get("relation_write") or {})
                     .get("edges") or []):
            counted[edge["predicate"]] = counted.get(edge["predicate"], 0) + 1
    assert indep["A2_edge_row_counts_from_receipt"] == counted, (
        indep["A2_edge_row_counts_from_receipt"], counted)
    # (3) the tool surface it reports is the committed surface
    tools = json.loads((BUNDLE / "out" / "tool-surface.json").read_text())
    assert indep["tool_surface"]["advertised_tools"] == len(tools), (
        indep["tool_surface"], len(tools))
    # (4) its typed-node counts are the score's own kind population
    assert indep["A1"]["typed_nodes"]["dev:code"] == (
        score["A1_type_coverage"]["per_type"]["component"]["right_kind"]), (
        indep["A1"]["typed_nodes"])
    assert indep["A1"]["typed_nodes"]["product-strategy:product"] == (
        score["A1_type_coverage"]["per_type"]["product"]["right_kind"])


def test_every_audited_quote_ROW_is_a_real_quote_of_a_real_document():
    """Reds if the quote audit's ROWS are replaced with sentences that are not there.

    ⛔ #14691 review, 6th cycle, finding 5 (P2). The arm above pins that `audited`
    is non-empty and that each row is non-verbatim and in-band — never that the
    row's QUOTE exists. Measured green on 43fbd256: replacing every `audited`
    row's `quote` with `"some other sentence entirely"` and its `doc` with
    `"doc-99"`.
    """
    audit = json.loads((BUNDLE / "out" / "t6-quote-audit.json").read_text())
    receipt_quotes = set()
    for doc in _receipt()["documents"]:
        for point in ((doc.get("result") or {}).get("points") or []):
            quote = ((point.get("props") or {}).get("quote") or "")
            if quote:
                receipt_quotes.add(" ".join(quote.lower().split()))
    assert receipt_quotes, "the receipt carries no quotes to audit"
    # ⛔ NON-VACUITY IS LOCAL, not borrowed from a sibling arm. With `audited: []`
    # this loop asserted nothing and PASSED (measured) — three other tests redded,
    # so the file was not a gate hole, but an arm that is green on an empty
    # collection is exactly the shape a later refactor keeps while deleting the
    # siblings that covered it.
    assert audit["audited"], "the audit shipped no rows at all"
    for row in audit["audited"]:
        # the audit stores the quote TRUNCATED (the scorer caps the row at 200
        # chars), so the row must be a PREFIX of a recorded quote — a fabricated
        # sentence is not a prefix of any of them.
        quoted = " ".join(row["quote"].lower().split())
        assert any(rq.startswith(quoted) or quoted in rq
                   for rq in receipt_quotes), (
            f"an audited row quotes a span the receipt never recorded: "
            f"{row['quote'][:100]!r}")
        assert row.get("point"), f"an audited row carries no point to attribute: {row}"


def test_the_refusal_FIRES_on_the_run_path(monkeypatch, tmp_path):
    """Reds if the guard is computed and never acted on, on the real run path.

    ⛔ #14691 review, 6th cycle, finding 9 (P3). `run_ingest.main()` was pinned by
    THREE SUBSTRINGS, and measured green on 43fbd256:

        contamination = occupancy(before)
        if contamination:
            contamination = {k: v for k, v in contamination.items()
                             if len(v) > 10**9}   # the guard is now inert

    All three assertions still pass — the call is there, the branch is there, the
    raise is there — and a populated graph is measured as if it were fresh. No
    textual pin can see that, so this arm DRIVES `main()` against a stub SDK and
    asserts the EFFECT: a populated overview raises before any capture is made,
    and a fresh one is allowed through to the first capture.

    `main()` writes `out/ingest-receipt.json` at the end, so `ingest.OUT` is
    repointed at `tmp_path` — a test must never touch a committed artifact.

    ⛔ THIS ARM SUBSUMES THE SOURCE-SUBSTRING WIRING PIN (#14691, merged lanes).
    A parallel lane's `test_the_isolation_refusal_is_WIRED_into_the_ingest_runner`
    asserted that `main()` contained the occupancy call, the branch and the raise.
    It is retired rather than adapted, for two measured reasons: a substring pin
    cannot see an inserted filter that makes the branch inert (this review's own
    finding), and a substring pin that hard-codes one refactor's spelling is
    friction on the next one — the guard now lives in `refuse_if_not_fresh()`, and
    removing the CALL from `main()` reds HERE, because a populated overview would
    then be measured instead of refused.
    """
    import importlib.util as _ilu

    import tortoise.sdk as sdk_module

    spec = _ilu.spec_from_file_location("t6_ingest_run", BUNDLE / "run_ingest.py")
    ingest = _ilu.module_from_spec(spec)
    spec.loader.exec_module(ingest)
    monkeypatch.setattr(ingest, "OUT", tmp_path)

    fresh = json.loads(json.dumps(_receipt()["empty_before"]))
    populated = json.loads(json.dumps(fresh))
    populated["status"]["total_entities"] = 500
    populated["status"]["counts"]["Point"] = 500

    class _StubSDK:
        captured = 0

        def __init__(self, *a, **k):
            pass

        def graph_overview(self, section=None):
            if section == "namespaces":
                return {"namespaces": []}
            return fresh if _StubSDK.mode == "fresh" else populated

        def mine_knowledge_from_session(self, *a, **k):
            _StubSDK.captured += 1
            raise RuntimeError("stub capture")

        def close(self):
            pass

    monkeypatch.setattr(sdk_module, "TortoiseSDK", _StubSDK)

    # (1) a POPULATED graph: refused, and no capture is attempted
    _StubSDK.mode = "populated"
    _StubSDK.captured = 0
    try:
        ingest.main()
    except SystemExit as exc:
        assert "NOT FRESH" in str(exc), exc
    else:
        raise AssertionError(
            "run_ingest.main() measured a POPULATED graph without refusing — the "
            "isolation premise is recorded and never enforced on the run path")
    assert _StubSDK.captured == 0, (
        "the refusal fired after a capture had already been attempted")

    # (2) the non-vacuity control: a FRESH graph reaches the first capture
    _StubSDK.mode = "fresh"
    _StubSDK.captured = 0
    with contextlib.suppress(SystemExit):
        ingest.main()
    assert _StubSDK.captured > 0, (
        "a fresh graph never reached the ingest loop, so arm (1) is vacuous")


def test_every_TABLE_ROW_in_the_report_is_derived_from_an_artifact():
    """Reds if a per-row figure in the A1/A2/A5 tables drifts from its artifact.

    ⛔ #14691 review, 7th cycle, findings 1, 2 and 5 (P1/P2/P2). The headline pins
    reached the A1 and A3 HEADINGS, and the A2 table only in the three cells an
    earlier cycle happened to name. Every other cell was free, measured green on
    1de08152, one at a time:

      * A2 `| **team → role** | **0** |` → `**7**` — the report could state that a
        containment class it declares ABSENT (0 edges) EXISTS, contradicting §3's
        own mechanism item and §5, with the suite green;
      * A2 `person → team **4/4**` → `**9/4**`, `person → role **4/4**` →
        `**9/4**`, the ownedBy `**4/4** (6 rows)` → `**9/4**` / `(11 rows)`,
        `customer → product 2/2` → `9/2`, `dependsOn **7/7** | **7/7**` →
        `**7/7** | **14/7**`;
      * A1 `| component | 7 | 7 | 7* |` → `9 | 9 | 9*`, `| team | 3 | 3 | 3 |` →
        `6 | 6 | 6 |`;
      * A5's `best rank` cells (`2` → `5`).

    Each row is rebuilt from `out/t6-score.json`, so the table cannot say anything
    the artifact does not.
    """
    score = _score()
    a1 = score["A1_type_coverage"]
    a2 = score["A2_edges"]["totals"]
    report = _report()

    # ── A1: one row per declared role, from `per_type`
    for role, block in a1["per_type"].items():
        # the table HEADER also starts `| role |`; the data row's cells are ints
        row = _report_line(rf"^\| {role} \| \d+ \| \d+ \| \d+")
        assert row, f"REPORT.md has no A1 row for {role!r}"
        cells = [c.strip().rstrip("*") for c in row.split("|")[1:-1]]
        assert cells[1:4] == [str(block["declared"]), str(block["present"]),
                              str(block["right_kind"])], (role, cells)
        # ⛔ THE FIFTH CELL TOO (8th fresh-context review, N2). This arm read
        # `cells[1:4]`, so the "kind they arrive as" column — the whole A1 claim —
        # was free: `| feature | 5 | 5 | 5 | \`product-strategy:product\` |` was
        # measured GREEN. The named kind must be one the artifact ACCEPTS for that
        # role and one it actually populated.
        kind_cell = cells[4] if len(cells) > 4 else ""
        named = re.findall(r"`([a-z][a-z-]*:[A-Za-z]+)`", kind_cell)
        assert named, f"A1 row {role!r} names no kind they arrive as: {kind_cell!r}"
        for kind in named:
            assert kind in a1["accepted_kinds"][role], (
                f"A1 row {role!r} says they arrive as {kind!r}, which the artifact "
                f"does not accept for that role ({a1['accepted_kinds'][role]})")
            assert any(kind in group for group in a1["kind_population"].values()), (
                f"A1 row {role!r} names kind {kind!r}, which the artifact "
                "populated nowhere")

    # ── A2: every class row, from `totals` + the receipt's own row counts
    receipt = _receipt()
    rows_by_predicate = {}
    for doc in receipt["documents"]:
        for edge in (((doc.get("result") or {}).get("relation_write") or {})
                     .get("edges") or []):
            rows_by_predicate[edge["predicate"]] = \
                rows_by_predicate.get(edge["predicate"], 0) + 1
    owned_rows = rows_by_predicate.get("ownedBy", 0)
    produced_rows = rows_by_predicate.get("produces", 0)
    ownership_pairs = sum(1 for o in score["A2_edges"]["ownership"] if o["present"])

    def a2row(pattern, *expected, cells_at=()):
        row = _report_line(pattern)
        assert row, f"REPORT.md has no A2 row matching {pattern!r}"
        for fragment in expected:
            assert fragment in row, (pattern, fragment, row)
        cells = [c.strip() for c in row.split("|")[1:-1]]
        for index, want in cells_at:
            assert cells[index] == want, (pattern, index, want, cells)

    a2row(r"^\| product → feature \(`hasPart`\)", f"**{a2['containment_present']}**")
    a2row(r"^\| person → team \(`related`\)", f"**{a2['membership_present']}**")
    a2row(r"^\| person → role \(`related`\)", f"**{a2['role_links_present']}**")
    a2row(r"^\| \*\*team → role\*\* \|", f"**{a2['team_to_role_edges']}**")
    a2row(r"^\| thing → owner \(`ownedBy`\)",
          f"**{ownership_pairs}/4** ({owned_rows} rows)",
          f"**{a2['ownership_oriented_correct']}/{owned_rows}")
    a2row(r"^\| dependent → dependency \(`dependsOn`\)",
          cells_at=((1, f"**{a2['dependency_present_and_correct']}**"),
                    (2, f"**{a2['dependency_present_and_correct']}**")))
    a2row(r"^\| team → component \(`produces`\)",
          f"{produced_rows}/{produced_rows}", f"**0/{produced_rows}")
    a2row(r"^\| customer → product \|", "2/2")

    # ── A5: each row's rank cell holds exactly the ranks the artifact recorded
    probes = score["A5_retrieval"]["probes"]
    # the A5 table's own shape: question | expected | text verdict | ranks | walk
    table = [line for line in report.splitlines()
             if re.match(r"^\| [^|]+ \| [^|]+ \| ✅[^|]* \| [\d, ]+ \| [❌✅] \|$", line)]
    assert len(table) == len(probes), (len(table), len(probes))
    for probe, row in zip(probes, table, strict=True):
        # the report collapses equal ranks ("2" for three features that each
        # ranked 2), so the comparable form is the SET of distinct ranks
        ranks = sorted(set(probe["first_rank_in_search"].values()))
        cells = [c.strip() for c in row.split("|")]
        shown = sorted({int(n) for n in re.findall(r"\d+", cells[-3])})
        assert shown == ranks, (
            f"A5 row for {probe['element']!r} states ranks {shown} while the "
            f"artifact recorded {ranks}: {row}")


def test_the_figures_the_8th_review_measured_as_FREE_are_now_pinned():
    r"""Reds if a figure that only UNION MEMBERSHIP supported moves to another line.

    ⛔ #14691 review (8th cycle, P1 — "no prose figure is free" is a union test).
    The net arm below asserts `norm(token) in supported`, and `supported` is the
    union of every scalar, every list length and every file size across
    `out/*.json` — 233 values, 133 of them ≤ 200. A RIGHT-LOOKING FIGURE IN THE
    WRONG PLACE is therefore satisfied by a number that belongs somewhere else,
    which is the class the 7th cycle actually reported: the net reds "a number no
    artifact carries", never "the right number on the wrong line".

    Measured at `bf7c610a`, each mutant applied ALONE — `61 passed` on all six:

    * §1 `(1.4 min)` → `(16.3 min)` — 16.3 is the MEAN ack, a supported number;
    * §3 `` `managedBy` is inverted too** (2 rows) `` → `(6 rows)` — 6 is supported;
    * §8 `32 element walks` → `39` — 39 is the tool-surface count;
    * §3 doc-03 `7 components and 7 dependencies` → `9 components` — 9 is supported;
    * VERIFICATION `because 6 `ownedBy` rows are only 4 pairs` → `4 … 6 pairs`;
    * §1 `Five short **meeting transcripts**` → `Six` — prose counts are outside the
      net's `\b\d` reach entirely.

    Each figure is now read from the field that produces it, and each read is
    LINE-SCOPED, so a sibling line that quotes the same figure (the §8.3 record of
    the mutant does) cannot satisfy the claim.

    ⛔ #14691 review (9th cycle, P2): line-scoping alone was NOT enough — two of
    the reads were bare substrings with no LEFT boundary, so a figure with a
    leading digit still satisfied them. Measured at `1120c51554bd`, whole suite
    per mutant, each restored byte-for-byte:

    * §1 `· 5 captures, 81.7 s …` → `· 15 captures, 81.7 s …` — **63 passed**;
      15 is a supported value, so neither this arm nor the union net saw it;
    * §8 `32 element walks` → `132 element walks` — this arm PASSED; only the
      union net reddened it (`132` is not an artifact value), i.e. the arm was
      not doing its own job.

    Both reads now carry a boundary: §1 asserts the row's own `· ` separator
    before the count, and §8 uses `(?<![\d.])` so no digit or decimal point may
    precede the figure. Re-measured on this head, both mutants now red THIS arm.
    """
    receipt = _receipt()
    module = _load_score_module()

    # ── §1: the capture count and the ack TOTAL, in seconds AND minutes. The mean
    # ack is a different artifact value and must not be able to take this line.
    docs = receipt["documents"]
    total_ack = sum(d["ack_seconds"] for d in docs)
    run = _report_line(r"^\| Run \|")
    assert run, "REPORT.md has no §1 Run row"
    assert (f"· {len(docs)} captures, {total_ack:.1f} s of capture ack time "
            f"({total_ack / 60:.1f} min)") in run, (
        "§1's capture/ack sentence is not the receipt's own totals — the "
        f"per-document acks sum to {total_ack:.1f} s = {total_ack / 60:.1f} min "
        f"over {len(docs)} captures; the row is {run!r}")

    # ── §1: the corpus size, spelled in prose (outside the net's tokenizer)
    transcripts = len(_corpus()["documents"])
    count_word = {2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six",
                  7: "Seven"}.get(transcripts, str(transcripts))
    corpus_line = _report_line(r"short \*\*meeting transcripts\*\*")
    assert corpus_line, "REPORT.md has no §1 corpus-size sentence"
    assert corpus_line.startswith(
        f"{count_word} short **meeting transcripts**"), (
        f"§1 opens with {corpus_line[:40]!r} while the corpus holds "
        f"{transcripts} documents")

    # ── §3: the `managedBy` row count, and doc-03's component/dependency pair
    managed = [e for e in module.EDGES if e["predicate"] == "managedBy"]
    managed_row = _report_line(r"^\d+\. \*\*`managedBy` is inverted too\*\*")
    assert managed_row, "REPORT.md §3 has no `managedBy` inversion row"
    assert f"**`managedBy` is inverted too** ({len(managed)} rows)" in managed_row, (
        f"§3's managedBy row states a count the receipt's {len(managed)} "
        f"managedBy edges do not: {managed_row!r}")

    declared = _corpus()["_declared_structure"]
    doc03 = _report_line(r"^\| `doc-03` \|")
    assert doc03, "REPORT.md has no doc-03 inventory row"
    assert (f"**architecture** — {len(declared['components'])} components and "
            f"{len(declared['dependencies'])} dependencies") in doc03, (
        "doc-03's inventory row is not the declaration's: "
        f"{len(declared['components'])} components / "
        f"{len(declared['dependencies'])} dependencies — row {doc03!r}")

    # ── §8: the element-walk count on the readback row itself, not wherever else
    # the same figure is quoted (§8.3's mutant record quotes it too)
    walks = len(_readback()["elements"])
    rb_row = _report_line(r"^\| `out/readback\.json` \|")
    assert rb_row, "REPORT.md §8 has no readback row"
    assert re.search(rf"(?<![\d.]){walks} element walks", rb_row), (
        f"§8's readback row does not state the artifact's {walks} element walks: "
        f"{rb_row!r}")

    # ── VERIFICATION: the ownedBy ROWS and the distinct PAIRS behind them
    owned = [e for e in module.EDGES if e["predicate"] == "ownedBy"]
    pairs = {(str(e["src"]).lower(), str(e["dst"]).lower()) for e in owned}
    ver_lines = [line for line in
                 (BUNDLE / "VERIFICATION-independent.md").read_text().splitlines()
                 if "`ownedBy` rows are only" in line]
    assert ver_lines, (
        "VERIFICATION-independent.md no longer states the ownedBy row/pair split")
    assert (f"because {len(owned)} `ownedBy` rows are only {len(pairs)} pairs"
            in " ".join(ver_lines)), (
        "VERIFICATION's ownedBy row/pair split is not the receipt's: "
        f"{len(owned)} rows / {len(pairs)} pairs — got {ver_lines!r}")


def test_the_after_ingest_block_cost_table_and_remaining_figures_are_derived():
    r"""Reds if §2's after-ingest block, the cost/token tables, or the §3/§7/§8
    figures drift from their artifacts.

    ⛔ #14691 review, 7th cycle, finding 5 (P2). Measured green on 1de08152, one
    at a time: the after-ingest `taxonomy` (`"Point": 86` → 93), `structure.total`
    (81 → 88), `pointkinds` count (51 → 58), `5 agentSession sources` → `9`;
    §2's `| doc-02 | 14.6 |` → `21.6`; the total row's `**19**` calls → 26 and
    `50` points → 57; `**0 of 28 declared structure elements untraceable**` → 7;
    `8 of 46 are not co-stated` → 15; `50 \`quote\` spans` → 57; `Examples of the
    invented 12` → 19; §7's `**13**` → 20; §8's `809,812 bytes · 791 KB` → and
    `32 element walks` → 39; and `out/ingest-receipt.json`'s
    `documents[1].ack_seconds` (14.58) → 99.9 — the ack column's own source.

    Each figure is read out of the artifact, never retyped.

    ⛔ #14691 review (10th cycle — the 9th cycle's P2, swept as a CLASS): the 9th
    cycle fixed two reads with no LEFT boundary. Four MORE reads in this arm had
    the same shape, and each was measured here as a suite-level fail-open — the
    mutant applied ALONE at `5d7eb15bd0ec`, whole file, restored byte-for-byte:

    * §2 `5 agentSession sources` → `15` — **63 passed**. The read was
      document-wide, so §8's own record of this mutant satisfied it as well.
    * §3 `50 `quote` spans` → `150` — **63 passed** (same document-wide shape).
    * §3/A4 `8 of 46 are not` → `18 of 46 are not` — **63 passed**; 18 is a
      supported value, so the union net was silent.
    * §3/A1 `11 Subjects is exactly …` → `111 Subjects is exactly …` — **63
      passed**; 111 is supported.

    Each is now anchored: the sources row is LINE-SCOPED to the overview line
    that owns it (`^"sources":`) and the quote-span claim to the line that owns
    it, and both are left-bound so a leading digit cannot satisfy them; the
    sentence-scope and population figures require that no digit, comma or point
    precedes them. The remaining bare-number reads in this arm are NOT
    fail-opens of this class and stay as they are, measured: `1,074 tokens`,
    `4,295 chars`, `809,812 bytes` and `32 element walks` each red the union net
    when prefixed (the prefixed value is in no artifact), and the other reads
    already begin with a literal (`**`, `(`, `·`, `|`) that a leading digit
    cannot join.
    """
    score = _score()
    receipt = _receipt()
    after = receipt["graph_overview_after"]
    a4 = score["A4_no_invention"]
    claim = score["claim_layer"]
    tokens = score["extraction_tokens"]
    readback = _readback()
    report = _report()

    # ── §2: the after-ingest overview block
    tax = ", ".join(f'"{k}": {v}' for k, v in after["taxonomy"].items())
    assert f'"taxonomy":  {{{tax}}}' in report, (tax, "§2 taxonomy")
    assert (f'"structure": {{"operators": {after["structure"]["operators"]}, '
            f'"total": {after["structure"]["total"]}}}') in report, "§2 structure"
    kinds = ", ".join(f'{{"kind": "{p["kind"]}", "count": {p["count"]}}}'
                      for p in after["pointkinds"])
    assert f'"pointkinds": [{kinds}]' in report, ("§2 pointkinds", kinds)
    sources = len(after["sources"])
    sources_row = _report_line(r'^"sources":')
    assert sources_row, 'REPORT.md §2 has no "sources" row'
    assert re.search(rf'^"sources":\s+{sources} agentSession sources\b',
                     sources_row), (
        f'§2\'s sources row is not the receipt\'s {sources} sources: '
        f'{sources_row!r}')

    # ── §2: the per-document cost/ack rows, and the totals row
    docs = {d["doc_id"]: d for d in receipt["documents"]}
    for doc_id, by_doc in tokens["by_doc"].items():
        row = _report_line(rf"^\| {doc_id} \|")
        assert row, f"REPORT.md has no §2 row for {doc_id}"
        cells = [c.strip() for c in row.split("|")[1:-1]]
        assert cells[1] == f"{docs[doc_id]['ack_seconds']:.1f}", (doc_id, cells)
        assert cells[2] == str(by_doc["calls"]), (doc_id, cells)
        assert cells[3] == f"{by_doc['prompt_tokens']:,}", (doc_id, cells)
        assert cells[4] == f"{by_doc['completion_tokens']:,}", (doc_id, cells)
    total = _report_line(r"^\| \*\*total\*\* \|")
    assert total, "REPORT.md has no §2 totals row"
    cells = [c.strip() for c in total.split("|")[1:-1]]
    assert cells[2] == f"**{tokens['calls']}**", cells
    assert cells[3] == f"**{tokens['prompt_tokens']:,}**", cells
    assert cells[4] == f"**{tokens['completion_tokens']:,}**", cells
    assert cells[5] == f"{score['claim_layer']['quotes_total']}", cells
    assert cells[6] == f"**{score['edge_count']}**", cells

    # ── §3: the A4 counts and the claim-layer call-outs
    assert (f"**{a4['untraceable_counts']['declared_structure']} of "
            f"{score['A1_type_coverage']['declared']} declared structure elements "
            "untraceable**") in report, "§3/A4 declared untraceable"
    strict = a4["relations_not_co_stated_at_SENTENCE_scope"]
    if not isinstance(strict, int):
        strict = len(strict)
    assert f"**{len(a4['relations_not_co_stated'])} of {score['edge_count']} " \
           "relations are" in report, "§3/A4 un-stated relations"
    assert re.search(rf"(?<![\d.,]){strict} of {score['edge_count']} are not",
                     report), (
        f"§3/A4's sentence-scope count is not the artifact's {strict} of "
        f"{score['edge_count']}")
    quote_line = _report_line(
        r"`quote` spans the product recorded as the source of a claim:")
    assert quote_line, "REPORT.md has no §3 quote-span claim line"
    assert re.match(rf"{claim['quotes_total']} `quote` spans ", quote_line), (
        f"§3's quote-span claim is not the claim layer's "
        f"{claim['quotes_total']}: {quote_line!r}")
    # ⛔ LINE-SCOPED, and the reason matters: `Examples of the invented 12` occurs
    # TWICE — here, and in §8.3's own record of this very mutant. A document-wide
    # `in report` read is therefore satisfied by the RECORD and stays green while
    # the CLAIM drifts (measured: mutating this line kept the suite green on
    # cf9e1221). The claim line is the one that starts with it.
    examples = _report_line(r"^Examples of the invented")
    assert examples, "REPORT.md has no invented-examples header line"
    assert examples.startswith(f"Examples of the invented {claim['invented']}"), (
        "§3's invented-example header is not the artifact's population: "
        f"{examples!r}")
    # the claim layer's population is stated in two other shapes, both free before
    # this arm: §1's `4,295 characters ≈ 1,074 tokens`, §2's token-budget corpus
    # row, and §8's `(5 transcripts, 4,295 chars)` bundle row.
    assert (f"**{score['corpus_chars']:,} characters ≈ "
            f"{tokens['corpus_tokens_estimate']:,} tokens**") in report, (
        "§1's corpus sentence")
    budget = _report_line(r"^\| corpus \|")
    assert budget, "§2's token-budget table has no corpus row"
    assert (f"{score['corpus_chars']:,} chars ≈ "
            f"{tokens['corpus_tokens_estimate']:,} tokens") in budget, budget
    bundle_row = _report_line(r"^\| `corpus/structure-corpus\.json` \|")
    assert bundle_row, "§8's bundle table has no corpus row"
    assert (f"({len(_corpus()['documents'])} transcripts, "
            f"{score['corpus_chars']:,} chars)") in bundle_row, bundle_row
    # §3/A1's kind population, spelled twice in one sentence: the materialised
    # counts (from the receipt's own post-write taxonomy) and the arithmetic that
    # the Subjects are teams+roles+people — neither was pinned.
    per_type = score["A1_type_coverage"]["per_type"]
    population = _report_paragraph(r"^`core:role`, people are")
    assert population, "REPORT.md has no kind-population paragraph"
    kinds_arithmetic = (f"{after['taxonomy']['Object']} Objects and "
                       f"{after['taxonomy']['Subject']} Subjects")
    assert re.search(r"(?<![\d.,])" + re.escape(kinds_arithmetic), population), (
        "§3/A1's materialised population is not the receipt's")
    subjects_arithmetic = (f"{after['taxonomy']['Subject']} Subjects is exactly "
                           f"{per_type['team']['present']} teams + "
                           f"{per_type['role']['present']} roles + "
                           f"{per_type['person']['present']} people")
    assert re.search(r"(?<![\d.,])" + re.escape(subjects_arithmetic),
                     population), (
        "§3/A1's Subjects arithmetic is not the artifact's")
    # §7's corroboration states the two pair counts in its opening sentence
    same_pairs = _report_line(r"^It reaches the same numbers")
    assert same_pairs, "§7 has no 'it reaches the same numbers' sentence"
    assert (f"the same {len(score['A2_edges']['containment'])} `hasPart` and "
            f"{len(score['A2_edges']['dependency'])} `dependsOn` **pairs**") in \
        same_pairs, same_pairs
    assert (f"**{score['A2_edges']['totals']['asymmetric_pairs_inverted']}** — is "
            "the number this report's A2 line derives") in report, "§7 pairs"

    # ── §8: the readback row's own size and walk count
    path = BUNDLE / "out" / "readback.json"
    size = path.stat().st_size
    assert f"{size:,} bytes · {round(size / 1024)} KB" in report, "§8 readback size"
    assert f"{len(readback['elements'])} element walks" in report, "§8 walk count"


def test_the_readback_artifact_backs_the_findings_it_is_cited_for():
    """Reds if `out/readback.json`'s payload contradicts the findings citing it.

    ⛔ #14691 review, 7th cycle, findings 6 and 7 (P3). `_door_findings()` takes
    `READBACK["door_findings"]` verbatim, so the evidence a finding is cited for
    can be emptied while the finding stands. Measured green on 1de08152:
    `unresolved_elements` → `[]` (while D2 and REPORT keep saying "2 of 32"), a
    retrieval walk's `reply` → `{"entity": {}}`, `structure_check.reply` →
    `{"result": [{"severity": "error", …}]}` (while §6.4 says it returned `[]`),
    `tool_surface.count` 39 → 99, `"ok": true` → `false`, and the receipt's
    `installed_packs` losing a namespace REPORT names.

    These are the fields the report's prose is a claim ABOUT, so they are pinned
    to it — never inferred from it.
    """
    readback = _readback()
    receipt = _receipt()
    report = _report()

    # D2's "NO for 2 of 32" is the readback's own unresolved list and walk count
    unresolved = readback["unresolved_elements"]
    elements = readback["elements"]
    # a walk that was asked must carry a reply, and the unresolved entries must
    # be a subset of the elements read
    assert all("reply" in (e.get("walk") or {}) for e in elements), (
        "an element walk carries no reply at all")
    unresolved_ids = {u.split(":", 1)[-1] for u in unresolved}
    names = {e.get("name") for e in elements}
    assert unresolved_ids <= {str(n) for n in names}, (sorted(unresolved_ids)[:5])
    d2 = next(f for f in _score()["door_findings"] if f["id"] == "D2")
    assert f"{len(unresolved)} of {len(elements)}" in d2["answer_over_mcp"], (
        d2["answer_over_mcp"], len(unresolved), len(elements))

    # §6.4's claim: the structural validator answered nothing
    assert _rows(readback["structure_check"]["reply"]) == [], (
        "check_structure returned rows, so REPORT §6.4's claim is false")
    # the readback's own health and surface
    assert readback["ok"] is True, (
        "the readback reports NOT ok — a layer read errored")
    assert readback["tool_surface"]["count"] == len(
        json.loads((BUNDLE / "out" / "tool-surface.json").read_text())), (
        readback["tool_surface"], "the surface count disagrees with the receipt")
    # every pack REPORT names is one the receipt recorded
    names_in_report = [p["namespace"] for p in receipt["installed_packs"]]
    assert len(names_in_report) == len(set(names_in_report)), names_in_report
    sentence = " ".join(report.split())
    for namespace in names_in_report:
        assert f"`{namespace}`" in sentence, (
            f"REPORT does not name the installed pack {namespace!r} that the "
            "receipt recorded")


def test_the_tool_surface_artifact_carries_a_SCHEMA_for_every_tool():
    """Reds if the surface artifact keeps its tools but loses their schemas.

    ⛔ #14691 review, 7th cycle, finding 8 (P3). REPORT §8 says the artifact is
    "the live MCP surface (39 tools) **with each tool's own `inputSchema`**", and
    the suite pinned only its LENGTH. Measured green on 1de08152: replacing every
    entry's description/params with `{"description": "fake", "required": [],
    "params": []}`.
    """
    surface = json.loads((BUNDLE / "out" / "tool-surface.json").read_text())
    assert surface, "the surface artifact is empty"
    for name, entry in surface.items():
        assert isinstance(entry, dict) and entry, (name, entry)
        text = json.dumps(entry)
        assert len(text) > 40, (
            f"{name}'s entry carries no schema at all: {text}")
        assert "fake" not in text.lower(), (name, text[:120])
    # the surface is the same SET the readback recorded, not just the same size
    names = set(surface)
    assert names == set(_readback()["tool_surface"]["names"]), (
        "the surface receipt and the readback disagree about WHICH tools exist")


def test_the_independent_measurements_FINDING_payload_is_pinned_row_by_row():
    """Reds if the corroboration's rows are rewritten in place.

    ⛔ #14691 review, 7th cycle, finding 4 (P2). The `(b)` pins fixed the
    corroboration's headline fields; its PAYLOAD stayed free. Measured green on
    1de08152: swapping src/dst (and src_kind/dst_kind) on 2 of the 4
    `A2.ownedBy` rows — so the peer read corroborates "half correct" instead of
    "0/6 inverted", the exact claim it is cited for — `witness_neighbourhood_doors`
    given `explore_rows: 99`, and every `A5.*.search_ok` set to false.
    """
    indep = json.loads((BUNDLE / "out" / "t6-independent-verification.json").read_text())
    declared = _corpus()["_declared_structure"]

    # (1) each ownedBy row must record the INVERSION this artifact corroborates:
    #     the OWNER as source and the OWNED THING as target, with the kinds the
    #     live read reported. Reversing any row would make the peer read
    #     corroborate "correct" instead of "0/6 inverted" — the claim it is cited
    #     for — so the row set is pinned whole.
    expected = {(_norm_key(owner), _norm_key(thing) if thing != "ingest-gateway"
                 else "ingest-gateway component")
                for thing, owner in declared["ownership"]}
    rows = indep["A2"]["ownedBy"]
    assert len(rows) == len(expected), rows
    seen = set()
    for row in rows:
        src, dst = _norm_key(row["src"]), _norm_key(row["dst"])
        assert (src, dst) in expected, (
            f"an independent ownedBy row reads {src!r} → {dst!r}, which is not "
            f"the owner → owned-thing inversion the artifact corroborates: "
            f"{sorted(expected)}")
        assert row["src_kind"] == "subject" and row["dst_kind"] == "object", row
        seen.add((src, dst))
    assert seen == expected, sorted(expected - seen)

    # (2) the neighbourhood witness: its counts must be its own lists' lengths
    for node, block in indep["witness_neighbourhood_doors"].items():
        assert block["explore_rows"] == len(block["explore"]), (node, block)
        assert "not found" in json.dumps(block["list_topics"]).lower(), (node, block)

    # (3) every retrieval probe the corroboration ran answered
    for element, block in indep["A5"].items():
        assert block["search_ok"] is True, (element, block)



# ── the 6th-round review's residue: prose figures, absent-reply states, credits
def test_the_A4_heading_clause_is_the_claim_layer_verdict():
    """Reds if A4's post-em-dash clause drifts — 6th round, P2-2.

    `FAIL (the claim layer above it)` → `PASS (…)` left 39 passed: the verdict arm
    reads the FIRST token after the em dash, so the clause naming the claim layer
    was free while `claim_layer.passes` said `false` in the same bundle.
    """
    cl = _score()["claim_layer"]
    head = _report_line(r"^#{2,4} A4 · ")
    assert head, "REPORT.md has no A4 heading"
    clause = ("PASS" if cl["passes"] else "FAIL") + " (the claim layer above it)"
    assert clause in head, (clause, head)
    assert cl["passes"] is False and cl["invented"] > 0, cl


def test_the_A2_table_cells_are_the_artifact_totals():
    """Reds if an A2 cell states a figure the totals do not — 6th round, P2-6.

    Two cells were pinned by SUBSTRING, so `**5/5**` → `**9/9**` in the "oriented
    correctly" column (which carries the same figure) and `team → role **0**` →
    `**1**` both left 39 passed. Each row's cells are now read positionally and
    equated to the artifact's own totals.
    """
    totals = _score()["A2_edges"]["totals"]
    rows = {}
    for line in _report().splitlines():
        if line.startswith("| ") and line.count("|") >= 5:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) >= 4:
                rows[cells[0].replace("*", "")] = cells

    def cell(pattern: str, index: int) -> str:
        for label, cells in rows.items():
            if re.search(pattern, label):
                return cells[index]
        raise AssertionError(f"REPORT.md has no A2 row matching {pattern!r}: "
                             f"{sorted(rows)[:12]}")

    assert cell(r"^product → feature", 1) == f"**{totals['containment_present']}**"
    assert cell(r"^person → team", 1) == f"**{totals['membership_present']}**"
    assert cell(r"^person → role", 1) == f"**{totals['role_links_present']}**"
    assert cell(r"^team → role", 1) == f"**{totals['team_to_role_edges']}**"
    owned_rows = sum(
        1 for doc in _receipt()["documents"]
        for edge in ((doc.get("result") or {}).get("relation_write") or {}).get("edges") or []
        if edge.get("predicate") == "ownedBy")
    assert cell(r"^thing → owner", 1) == (
        f"**{totals['ownership_present']}** ({owned_rows} rows)"), cell(r"^thing → owner", 1)
    assert cell(r"^thing → owner", 2) == (
        f"**{totals['ownership_oriented_correct']}/{owned_rows} — every row "
        "INVERTED**"), cell(r"^thing → owner", 2)
    assert cell(r"^dependent → dependency", 1) == (
        f"**{totals['dependency_present_and_correct']}**")
    assert cell(r"^dependent → dependency", 2) == (
        f"**{totals['dependency_present_and_correct']}**")


def test_the_report_section_5_counts_are_the_artifacts():
    """Reds if §5's "Could/Could not" counts drift — 6th round, P2-4.

    §5 is the section that ANSWERS the PR's question, and none of its seven
    counts was pinned: all of them could be falsified at once, and `*Which team a
    role belongs to* — absent.` could become `— present and typed.`, with the
    suite green.
    """
    declared = _corpus()["_declared_structure"]
    totals = _score()["A2_edges"]["totals"]
    flat = " ".join(_report().replace("*", " ").split())
    words = {2: "two", 5: "five", 3: "three", 4: "four", 7: "seven"}
    inventory = (f"Two products, "
                 f"{words[len(declared['features'])]} features, which feature "
                 f"belongs to which product; {words[len(declared['teams'])]} "
                 f"teams; {words[len(declared['roles'])]} roles and who holds "
                 f"each; {words[len(declared['people'])]} people; "
                 f"{words[len(declared['components'])]} components and what "
                 f"depends on what; {words[len(declared['customers'])]} "
                 "customers and the segment they sit in.")
    assert inventory in flat, ("REPORT.md §5's inventory is not the corpus's "
                               f"declaration: expected {inventory!r}")
    assert totals["team_to_role_edges"] == 0, totals
    assert "Which team a role belongs to — absent." in flat, (
        "REPORT.md §5 no longer states the absent team→role class its own "
        f"artifact measures as {totals['team_to_role_edges']}")
    assert totals["membership_present"] == "4/4" and totals["role_links_present"] == "4/4"


def test_the_cost_table_is_the_receipt():
    """Reds if a §2 cost-table cell drifts from the receipt — 6th round, P3-11.

    `doc-04 … 3 written` → `9`, and the ack seconds across the table, were free
    text. Every cell is derived here: ack to one decimal, the LLM counters, the
    point and relation counts, and the total row (mean ack, sums).
    """
    docs = {d["doc_id"]: d for d in _receipt()["documents"]}
    rows = {}
    for line in _report().splitlines():
        if line.startswith("| doc-0") and line.count("|") >= 7:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            rows[cells[0]] = cells
    assert len(rows) == len(docs), (sorted(rows), sorted(docs))
    total_ack = 0.0
    for doc_id, doc in docs.items():
        res = doc.get("result") or {}
        llm = (res.get("stats") or {}).get("llm") or {}
        edges = (res.get("relation_write") or {}).get("edges") or []
        cells = rows[doc_id]
        assert cells[1] == f"{doc['ack_seconds']:.1f}", (doc_id, cells)
        assert cells[2] == str(llm.get("calls")), (doc_id, cells)
        assert cells[3] == f"{llm.get('prompt_tokens'):,}", (doc_id, cells)
        assert cells[4] == f"{llm.get('completion_tokens'):,}", (doc_id, cells)
        assert cells[5] == str(len(res.get("points") or [])), (doc_id, cells)
        assert cells[6].startswith(str(len(edges))), (doc_id, cells)
        total_ack += doc["ack_seconds"]
    totals = [c.strip() for c in _report_line(r"^\| \*\*total\*\* \|").strip().strip("|").split("|")]
    tk = _score()["extraction_tokens"]
    assert totals[1] == f"**{total_ack / len(docs):.1f} avg**", totals
    assert totals[2] == f"**{tk['calls']}**", totals
    assert totals[3] == f"**{tk['prompt_tokens']:,}**", totals
    assert totals[4] == f"**{tk['completion_tokens']:,}**", totals
    assert totals[6] == f"**{_score()['edge_count']}**", totals


def test_the_quote_audit_ROWS_are_the_paraphrase_examples():
    """Reds if the audit's per-row overlaps drift — 6th round, P3-8.

    Only `overlap >= 0.6` was asserted, so `audited[0].overlap 0.636 → 0.05`
    passed — an "audit" row disagreeing with the bucket it demonstrates. The rows
    are the claim layer's own paraphrase examples.
    """
    audit = json.loads((BUNDLE / "out" / "t6-quote-audit.json").read_text())
    cl = _score()["claim_layer"]
    q = _score()["A4_no_invention"]["quotes"]
    assert len(audit["audited"]) == len(q["paraphrase_examples"]), (
        len(audit["audited"]), len(q["paraphrase_examples"]))
    for row, example in zip(audit["audited"], q["paraphrase_examples"],
                            strict=True):
        assert row["overlap"] == example["best_sentence_overlap"], (row, example)
        assert row["quote"][:40] == example["quote"][:40], (row, example)
    # ⛔ the audit's `not_in_corpus` is the SENTENCE-scope population; the claim
    # layer's `invented` is the TURN-scope one (#14723 review, round 10, P2).
    assert (audit["verbatim"], audit["paraphrase"], audit["not_in_corpus"]) == (
        cl["verbatim"], cl["paraphrase"], cl["below_sentence_band"])


def test_the_tool_surface_NAMES_are_the_readback_census():
    """Reds if tool names are swapped while the count is preserved — 6th round, P3-9.

    Measured: renaming 34 of the 39 keys left 39 passed, because only the count
    and the schemas' presence were checked. The census in `readback.json` is the
    measurement of the live surface; the artifact is a per-tool detail view of it.
    """
    surface = json.loads((BUNDLE / "out" / "tool-surface.json").read_text())
    census = _readback()["tool_surface"]
    assert set(surface) == set(census["names"]), (
        "the tool-surface artifact names tools the live census does not: "
        f"{sorted(set(surface) - set(census['names']))[:5]} / missing "
        f"{sorted(set(census['names']) - set(surface))[:5]}")
    assert len(surface) == census["count"]


def test_the_structure_check_reply_is_the_empty_answer_the_report_states():
    """Reds if `structure_check` is unread or its count is invented — P3-10.

    `readback.structure_check` was read by nothing: replacing it with a 15-issue
    reply stayed green while REPORT §6.4 said it "returned `[]`". That sentence's
    "12 inverted-or-untyped relation classes" also matches no artifact (15 rows,
    13 distinct pairs), so the figure is now the artifact's.
    """
    probe = _readback()["structure_check"]
    assert _measured(probe.get("reply"), "check_structure") == [], probe
    flat = " ".join(_report().split())
    assert "`check_structure` returned `[]`" in flat
    totals = _score()["A2_edges"]["totals"]
    assert (f"{totals['asymmetric_rows_inverted']} inverted relation rows "
            f"({totals['asymmetric_pairs_inverted']} distinct pairs)") in flat, (
        "REPORT §6.4 does not state the inverted-row figure its own artifact "
        f"measures ({totals['asymmetric_rows_inverted']} rows / "
        f"{totals['asymmetric_pairs_inverted']} pairs)")


def test_a_reply_less_call_is_NOT_a_measured_empty():
    """Reds if absence is read as an empty ANSWER — 6th round, P2-3 / P3-7.

    `measured_empty({})` returned True and `rows(call.get("reply"))` is `[]` for a
    call that was never asked, so a witness row carrying `expand_relationships`
    with NO `reply` key still manufactured D4 — the opposite of what the D6 guard
    beside it implements.
    """
    module = _load_score_module()
    assert module.measured_empty({}) is False
    assert module.measured_empty({"tool": "t"}) is False
    assert module.measured_empty({"tool": "t", "reply": []}) is True
    assert module.measured_empty({"tool": "t", "reply": {"result": []}}) is True
    # the D4 arm, driven on the module's own readback
    saved = json.loads(json.dumps(module.READBACK))
    try:
        module.READBACK["door_findings"] = [
            f for f in module.READBACK.get("door_findings") or [] if f.get("id") != "D4"]
        for w in module.READBACK["link_witness"].values():
            w["expand_relationships"] = {"tool": "t", "explore": True}
        assert "D4" not in [f["id"] for f in module._door_findings()], (
            "a call carrying no `reply` key at all manufactured D4 — never asked "
            "is not an empty answer")
        for w in module.READBACK["link_witness"].values():
            w["expand_relationships"] = {"tool": "t", "reply": {"result": []}}
        assert "D4" in [f["id"] for f in module._door_findings()]
    finally:
        module.READBACK.clear()
        # See the note on the sibling restore above: `|=` instead of `.update(saved)` keeps this
        # file out of the untyped-receiver residue that `tools/sdk_privatise.py --check` ratchets.
        module.READBACK |= saved


def test_the_ingest_refusal_cannot_be_starved_of_its_overview():
    """Reds if an unreadable overview counts as a FRESH graph — P3-7.

    `refuse_if_not_fresh(None)` and `({})` were both accepted: `(before or {})`
    walked to "no contamination", so a premise established by no measurement
    passed the one guard that enforces it.
    """
    spec = importlib.util.spec_from_file_location("t6_ingest",
                                                  BUNDLE / "run_ingest.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.refuse_if_not_fresh(_receipt()["empty_before"])
    for starved in (None, {}, {"taxonomy": {}}, {"status": {"total_entities": 0}}):
        try:
            module.refuse_if_not_fresh(starved)
        except SystemExit as exc:
            assert "NOT FRESH" in str(exc), (starved, str(exc))
        else:
            raise AssertionError(
                f"an unmeasurable overview ({starved!r}) was accepted as a fresh "
                "graph — the premise would rest on no measurement at all")


def test_a_CARRIED_but_empty_overview_section_is_not_a_MEASUREMENT():
    """Reds if a PRESENT-but-NULL section satisfies the freshness guard — P3-8.

    ⛔ #14691 review, 8th cycle, P3. `refuse_if_not_fresh` required only the KEYS
    (`set(_OVERVIEW_KEYS) <= set(before)`), so a section that was CARRIED but
    never measured passed the one guard the isolation premise rests on. Measured
    at `bf7c610a`, both ACCEPTED:

    * `{"taxonomy": {}, "structure": {"total": 0}, "status": None}`
    * `{"taxonomy": {}, "structure": {"total": 0}, "status": {}}`

    The real reply's `status` is
    `{"connected": true, "counts": {...}, "total_entities": 0}`
    (`out/ingest-receipt.json` → `empty_before.status`), so neither is a
    legitimate shape — they are "absent is not empty", one level down from the
    6th-cycle finding this same arm exists to close.
    """
    spec = importlib.util.spec_from_file_location("t6_ingest_carried",
                                                  BUNDLE / "run_ingest.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # non-vacuity in the OTHER direction: the receipt's own overview measures all
    # three sections, so the guard must not refuse a real run.
    assert module._unmeasured_sections(_receipt()["empty_before"]) == [], (
        "the receipt's own empty_before no longer measures every required "
        "section, so this arm would refuse a legitimate run")
    module.refuse_if_not_fresh(_receipt()["empty_before"])

    for carried in ({"taxonomy": {}, "structure": {"total": 0}, "status": None},
                    {"taxonomy": {}, "structure": {"total": 0}, "status": {}},
                    {"taxonomy": None, "structure": {"total": 0},
                     "status": {"total_entities": 0}},
                    {"taxonomy": {"Point": 0}, "structure": {},
                     "status": {"total_entities": 0}},
                    {"taxonomy": {"Point": 0}, "structure": [],
                     "status": {"total_entities": 0}}):
        try:
            module.refuse_if_not_fresh(carried)
        except SystemExit as exc:
            assert "NOT FRESH" in str(exc), (carried, str(exc))
        else:
            raise AssertionError(
                f"an overview that CARRIES a section without measuring it "
                f"({carried!r}) was accepted as a fresh graph — the isolation "
                "premise would rest on a field nobody read")


def test_the_corroboration_declaration_and_pairs_are_the_CORPUS_ones():
    """Reds if the peer lane's declaration or pair DIRECTION is rewritten — P2-5.

    Measured on `df788ab`: `declared.products → ["Alpha","Beta"]` and
    `A2.dependsOn → 7 junk pairs` each left 39 passed, because the corroboration
    arm compared LENGTHS. A corroboration whose content is free corroborates
    nothing.
    """
    iv = json.loads((BUNDLE / "out" / "t6-independent-verification.json").read_text())
    declared = _corpus()["_declared_structure"]
    for key in ("products", "features", "teams", "roles", "people", "components",
                "customers", "customerSegments"):
        assert {n.lower() for n in iv["declared"][key]} == {
            n.lower() for n in declared[key]}, (
            f"the independent measurement declares different {key}s than the "
            "corpus does")
    for key in ("containment", "membership", "ownership", "dependencies", "serves"):
        assert [list(p) for p in iv["declared"][key]] == [
            list(p) for p in declared[key]], key

    # direction, not just membership: dependsOn is (dependent, dependency)
    depends = [(r["src"], r["dst"]) for r in iv["A2"]["dependsOn"]]
    assert len(depends) == len(declared["dependencies"]), depends
    for dependent, dependency in declared["dependencies"]:
        hits = [row for row in depends
                if row[0].lower().startswith(dependent.lower())
                and row[1] == dependency]
        assert len(hits) == 1, (dependent, dependency, depends)
    # hasPart is (parent, child), and ownedBy is (subject, object) — the inversion
    products = {p.lower() for p in declared["products"]}
    for row in iv["A2"]["hasPart"]:
        assert row["src"].lower() in products, row
    component_names = {c.lower() for c in declared["components"]}
    for row in iv["A2"]["ownedBy"]:
        # the stored name may carry a welded collective noun (the D2 finding)
        assert row["src_kind"] == "subject" and row["dst_kind"] == "object", row
        owned = row["dst"].lower()
        assert (owned in products
                or any(owned.startswith(c) for c in component_names)
                or owned in {"on-call rotation", "enterprise renewals"}), row


def test_the_second_measurements_COUNTS_and_witness_rows_are_pinned():
    """Reds if the peer artifact's counts/witness rows are rewritten — P3-11/13.

    Measured on `df788ab`: `A1.objects 24 → 1`, `A5` rewritten, and every
    `witness_neighbourhood_doors[*].explore_rows → 99` left 39 passed — while
    REPORT §3 states "24 Objects and 11 Subjects" from the same population.
    """
    iv = json.loads((BUNDLE / "out" / "t6-independent-verification.json").read_text())
    typed = iv["A1"]["typed_nodes"]
    object_kinds = ("product-strategy:product", "product-strategy:customerSegment",
                    "product-strategy:customer", "product-strategy:feature",
                    "core:workflow", "agent-ops:rule", "dev:architectureDoc",
                    "dev:code")
    subject_kinds = ("core:naturalPerson", "core:team", "core:role")
    assert len(iv["A1"]["objects"]) == sum(typed.get(k, 0) for k in object_kinds)
    assert len(iv["A1"]["subjects"]) == sum(typed.get(k, 0) for k in subject_kinds)
    flat = " ".join(_report().split())
    assert (f"{len(iv['A1']['objects'])} Objects and "
            f"{len(iv['A1']['subjects'])} Subjects") in flat, (
        "REPORT.md's Objects/Subjects sentence is not the peer artifact's")
    # the neighbourhood witness: explore_rows is the length of the rows it kept
    for name, w in iv["witness_neighbourhood_doors"].items():
        assert w["explore_rows"] == len(w["explore"]), (name, w)
    # and the peer's A5 block is the text leg only
    assert all(set(v) == {"search_ok"} and v["search_ok"] is True
               for v in iv["A5"].values()), iv["A5"]


def test_the_VERIFICATION_writeup_states_the_artifacts_numbers():
    """Reds if the second write-up states figures the bundle refutes — P3-12.

    Measured: `11 → 50` verbatim and `ownedBy … **0** → **6**` (which ERASES the
    inversion) both left 39 passed — the tests read REPORT.md only, so the sibling
    document was free to describe a different bundle.
    """
    doc = (BUNDLE / "VERIFICATION-independent.md").read_text()
    cl = _score()["claim_layer"]
    assert f"| verbatim substring of the corpus | {cl['verbatim']} |" in doc
    assert (f"| faithful paraphrase (≥60% of the quote's words in one corpus "
            f"sentence) | {cl['paraphrase']} |") in doc
    assert (f"| **best corpus-sentence overlap < 60%** | **{cl['below_sentence_band']}** |"
            in doc), (
        "VERIFICATION's sentence-scope row does not state the artifact's "
        "below-sentence-band population — and it must NOT state the invented "
        "count there, which is what the round-10 P2 gloss did")
    assert (f"{cl['invented']}" in doc), (
        "VERIFICATION does not name the invented count its table's gloss was "
        "corrected to defer to")
    totals = _score()["A2_edges"]["totals"]
    assert (f"| `ownedBy` | `->` | **the target owns the source** | 6 (4 pairs) | "
            f"**{totals['ownership_oriented_correct']}** |") in doc, (
        "VERIFICATION's ownedBy row does not state the artifact's correct-row count")
    inverted = totals["asymmetric_rows_inverted"]
    pairs = totals["asymmetric_pairs_inverted"]
    assert f"**{inverted}**" in doc and f"**{pairs} distinct pairs**" in doc, (
        "VERIFICATION does not state the artifact's inverted-row / distinct-pair "
        f"counts ({inverted} / {pairs})")
    # the byte figure the REPORT states is the committed file's own
    size = (BUNDLE / "out" / "readback.json").stat().st_size
    assert f"{size:,} bytes" in _report(), size


def test_the_CLAIM_layer_and_A2_figures_are_the_artifacts():
    r"""Reds if a §3/A2 or §3/A4 / §7 sentence drifts onto ANOTHER artifact's number.

    ⛔ #14691 review, round 14, finding 2 (P2), the DRIFT half — pinned where it was
    MEASURED, sentence by sentence, rather than by a global rule. The free-figure arm
    above asks only whether SOME artifact carries the number, which is what a `+1`
    mutation exploits: every figure below was mutated `+1` at `0368cbb7` and left
    `97 passed`, because the drifted value happened to be carried by an unrelated
    field. Measured mutants, each alone, whole file:

      §7  `buckets partition its quotes_total (11 + 27 + 3 + 9 = 50)` -> `12 + 27 + 3 + 9 = 50`
      §3/A4 `12 of the 50` -> `13 of the 50`
      §3/A4 `the **invented** count is **9**` -> `10`
      §3/A2 `because 6 ownedBy rows are only 4 pairs` -> `7 / 5`

    ⛔ A GLOBAL UNIQUENESS RULE WAS TRIED AND REFUSED (#14691 round 14): requiring
    every prose figure to be carried by exactly one artifact value reds on honest
    small integers — `2 products`, `5 features`, `3 teams` are non-distinctive by
    construction — and the only way to keep it green was an allowlist of 24 figures,
    which is the hole wearing a gate's clothes. So the figures a reviewer MEASURED are
    pinned to the fields that produce them, and §8 records the residual: a prose figure
    that is not named here is graded by set membership only, which is why the next
    round should pin the sentence it is about rather than widen this arm.

    ⛔ AND `flat` RE-OPENED FIVE OF THEM (round 16, §8.18): §8.17 quotes
    `(11 + 27 + 3 + 9 = 50)` and `12 of the 50` verbatim (the A2 tally's counts too),
    so those mutants were GREEN again at `c76156f`. Each figure is pinned in the
    PARAGRAPH that owns it (`_report_paragraph`), never in the document.
    """
    claim = _score()["claim_layer"]
    audit = _audit()
    a2 = _score()["A2_edges"]

    # ⛔ EVERY FIGURE BELOW IS GRADED IN THE CLAIM'S OWN PARAGRAPH, never in the
    # flattened document. `flat` is how three of these pins were VACUOUS at
    # `c76156f`: §8.17 quotes `(11 + 27 + 3 + 9 = 50)`, `12 of the 50` and
    # `(0 edges)` verbatim (and the A2 tally's counts), so mutating §7's or §3/A4's
    # own copy left this arm GREEN while the review log still spelled the number.
    partition = _report_paragraph(r"buckets partition its")
    assert partition, "REPORT §7 has no decider/audit partition paragraph to grade"
    support = _report_paragraph(r"The unit of support is a TURN")
    assert support, "REPORT §3/A4 has no sentence-scope paragraph to grade"
    invented = _report_paragraph(r"the one this report carries")
    assert invented, "REPORT §3/A4 has no invented-count paragraph to grade"
    tally = _report_paragraph(r"One line that counts it")
    assert tally, "REPORT §3/A2 has no direction tally paragraph to grade"

    # the DECIDER's four buckets, and their sum — the sentence that had it wrong once
    buckets = (claim["verbatim"], claim["paraphrase"], claim["summary_in_one_turn"],
               claim["invented"])
    assert sum(buckets) == claim["quotes_total"], (buckets, claim["quotes_total"])
    stated = (
        f"({buckets[0]} + {buckets[1]} + {buckets[2]} + {buckets[3]} = "
        f"{claim['quotes_total']})")
    assert stated in partition, (
        "REPORT's §7 partition sentence does not state the decider's own buckets — "
        f"expected {stated!r}")

    # the AUDIT's three buckets, which are a DIFFERENT partition of the same total
    audit_buckets = (audit["verbatim"], audit["paraphrase"], audit["not_in_corpus"])
    assert sum(audit_buckets) == audit["quotes_total"], audit_buckets
    audit_stated = (f"({audit_buckets[0]} + {audit_buckets[1]} + {audit_buckets[2]} = "
                    f"{audit['quotes_total']})")
    assert audit_stated in partition, (
        "REPORT's §7 audit partition does not state the audit's own buckets — "
        f"expected {audit_stated!r}")

    # the sentence-scope count AND the invented count, each from its own field
    assert claim["invented"] != claim["below_sentence_band"], claim
    assert f"{claim['below_sentence_band']} of the {claim['quotes_total']}" in support, (
        f"REPORT does not state `{claim['below_sentence_band']} of the "
        f"{claim['quotes_total']}` for the sentence-scope population")
    assert f"**invented** count is **{claim['invented']}**" in invented, (
        f"REPORT does not state the decider's invented count ({claim['invented']})")

    # A2: the row total, the distinct pairs, and the ownedBy rows behind the difference
    totals = a2["totals"]
    rows = totals["asymmetric_rows_correct"] + totals["asymmetric_rows_inverted"]
    assert f"**{rows} asymmetric structural rows**" in tally, (
        f"REPORT does not state the artifact's asymmetric row total ({rows})")
    owned_rows = sum(r["n_edges"] for r in a2["ownership"])
    owned_pairs = len(a2["ownership"])
    assert f"{owned_rows} `ownedBy` rows are only {owned_pairs} pairs" in tally, (
        "REPORT does not state the ownedBy row/pair counts the distinct-pair total "
        f"rests on ({owned_rows} rows / {owned_pairs} pairs)")
    assert f"**{totals['asymmetric_pairs_inverted']} distinct pairs**" in tally, (
        f"REPORT does not state the artifact's inverted-pair count "
        f"({totals['asymmetric_pairs_inverted']})")

    # ── the overlap histogram, graded in the §3/A4 paragraph that states it ──
    # ⛔ THESE FOUR PINS GRADED THE REVIEW LOG (#14723, fix lane at `4944c705`,
    # measured): `**11 fall below 0.5**`, `**12** at `< 0.6`, `<invented> of those are
    # unsupported by any corpus turn` and the `11 / 1 / 27` triple existed ONLY in
    # §8's narrative, never in §3/A4, so the arm that carried them read `flat` (the
    # document) and was satisfied by the log that RECORDS them. Measured three ways at
    # `4944c705`: each figure mutated in the LOG reddened that arm; no body copy existed to
    # mutate; and stripping every `### 8.x` round section left it the only red in the file
    # (at that head `### 8.1`..`### 8.18` WAS the whole log). That red belongs to `4944c705`:
    # re-run at `05b3d7c0` the same sweep leaves 102 passed, because this arm no longer reads
    # the log — the head-independent statement is the digit-free-marker sweep, which also
    # leaves 102 passed, i.e. no arm depends on the log.
    # The histogram is a claim about `claim_layer`, so §3/A4 states it and it is
    # graded in that paragraph — never in the document that logs its review.
    bands = claim["bands"]
    histogram = _report_paragraph(r"The overlap histogram beneath that band")
    assert histogram, "REPORT §3/A4 has no overlap-histogram paragraph to grade"
    assert f"**{bands['below_0_5']} fall below 0.5**" in histogram, (
        "REPORT does not state the artifact's below-0.5 figure "
        f"({bands['below_0_5']})")
    # ⛔ THE SENTENCE READS THE SENTENCE-SCOPE HISTOGRAM, so the figure it states is
    # the SENTENCE-scope population (`below_sentence_band`), not `invented`
    # (#14723 review, round 10, P2: those diverged once the decider moved to the
    # turn, and pinning the wrong one would force the prose back to the gloss).
    assert f"**{claim['below_sentence_band']}** at `< 0.6`" in histogram, (
        "REPORT's below-0.6 total is not `below_sentence_band` "
        f"({claim['below_sentence_band']})")
    assert (f"{claim['invented']} of those are unsupported by any corpus turn"
            in histogram), (
        "the sentence-scope figure is not qualified with the invented count it "
        "does not equal")
    triple = (f"{bands['below_0_5']} / {bands['from_0_5_to_0_6']} / "
              f"{bands['at_or_above_0_6']}")
    assert f"`{triple}`" in histogram, (
        f"REPORT's band histogram is not the artifact's ({triple}) — a false "
        "triple in the paragraph that exists to read it")
    # the histogram covers the NON-VERBATIM population (a verbatim quote never
    # reaches `best_overlap`), so its total is `total - verbatim`, not `total`
    quotes = _score()["A4_no_invention"]["quotes"]
    assert sum(bands.values()) == quotes["total"] - quotes["verbatim"], (
        bands, quotes["total"], quotes["verbatim"])
    # the two real invariants of the histogram against the buckets; ⛔ it is
    # SENTENCE-scoped, so its below-band arm is `below_sentence_band`, not `invented`
    assert bands["below_0_5"] <= quotes["below_sentence_band"], (
        f"{bands['below_0_5']} quotes are below 0.5 but only "
        f"{quotes['below_sentence_band']} are below the 0.6 band", bands, quotes)
    assert bands["at_or_above_0_6"] == quotes["paraphrase"], (
        f"the at-or-above-0.6 band ({bands['at_or_above_0_6']}) is not the "
        f"paraphrase bucket ({quotes['paraphrase']})", bands, quotes)
    # ...and the DECIDER's histogram partitions the same population
    turn = quotes["bands_turn_scope"]
    assert sum(turn.values()) == quotes["total"] - quotes["verbatim"], turn
    assert turn["at_or_above_0_6"] == (quotes["paraphrase"]
                                        + quotes["summary_in_one_turn"]), (
        "the turn-scope histogram's supported arm is not paraphrase + summary",
        turn, quotes)
    assert turn["below_0_5"] <= quotes["invented"], (turn, quotes)


def test_the_D4_neighbour_figures_are_the_witnesses():
    """Reds if the D4 evidence's neighbour counts are rewritten — P3-11.

    Measured: `6 / 10 / 10` → `60 / 100 / 100` left 39 passed. They are the link
    witness's own `explore_neighbour_count` values for the three nodes D4 names.
    """
    witness = _readback()["link_witness"]
    names = ("Amara Osei", "Platform team", "event-bus")
    literal = " / ".join(str(witness[n]["explore_neighbour_count"]) for n in names)
    assert re.search(rf"sees {re.escape(literal)}(?!\d)", " ".join(_report().split())), (
        f"REPORT's D4 row does not state the witness counts ({literal})")


# ── no figure in the two documents is free (the reviewers' recurring class) ──
_SUP_NORM = None


def _supported_numbers() -> set[str]:
    """Every number the bundle's artifacts actually carry, normalised.

    Scalars, LIST LENGTHS (a 24-entry list supports "24"), file sizes in bytes and
    KiB, and the receipt's ack-seconds derived figures (each to 1 dp, the mean, and
    the total in minutes) — the three places REPORT's tables get a number the raw
    JSON does not spell.
    """
    def norm(x) -> str:
        f = float(str(x).replace(",", ""))
        return str(int(f)) if f == int(f) else str(f)

    out: set[str] = set()

    def walk(obj) -> None:
        if isinstance(obj, dict):
            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            out.add(norm(len(obj)))
            for value in obj:
                walk(value)
        elif isinstance(obj, bool):
            return
        elif isinstance(obj, (int, float)):
            out.add(norm(obj))

    for path in (BUNDLE / "out").glob("*.json"):
        walk(json.loads(path.read_text()))
        size = path.stat().st_size
        out |= {norm(size), norm(round(size / 1024))}
    acks = [d["ack_seconds"] for d in _receipt()["documents"]]
    out |= {norm(round(a, 1)) for a in acks}
    out |= {norm(round(sum(acks) / len(acks), 1)), norm(round(sum(acks) / 60, 1)),
            norm(round(sum(acks), 1))}
    return out


#: Contexts in which a number is NOT a claim about the artifacts. Each pattern
#: carries its reason, and the pattern is REMOVED before tokenising — so a number
#: cannot ride in on an allowlist entry that does not match its context.
_PROSE_ALLOWED = (
    (r"§\d+(?:\.\d+)?", "a section reference"),
    (r"(?m)^#{1,4} \d+(?:\.\d+)?", "a heading number"),
    (r"[\w./-]+\.(?:py|md|yml|json|sh|toml):\d+(?:-\d+)?", "a source line reference"),
    (r"#\d{3,}", "an issue/PR reference"),
    # ⛔ A SHA MUST LOOK LIKE ONE (#14691 review, round 4, finding 11).
    # `\b[0-9a-f]{7,40}\b` also matched any bare 7+ digit integer, so a sentence
    # carrying two invented numbers ("1234567 sources, 9876543 nodes") passed as
    # "a commit sha" — measured green. Requiring one hex LETTER keeps a 40-hex sha
    # and makes a plain integer a claim that must be backed by an artifact.
    (r"\b(?=[0-9a-f]{7,40}\b)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b", "a commit sha"),
    (r"\b20\d\d-\d\d-\d\d\b", "a date"),
    (r"\b\d\d:\d\d(?:[–-]\d\d:\d\d)?\b", "a wall-clock time"),
    (r"\b\d+\.\d+\.\d+\.\d+(?::\d+)?\b", "an address/port"),
    (r"\b\d+(?:\.\d+)?%", "a threshold, stated in words as well"),
    (r"\bMD0\d\d\b", "a markdownlint rule id"),
    (r"(?<![\w.])(?:A[1-5]|D[1-6]|T[1-6]|P[0-3]|G[12]|S[1-5]|W[1-5])\b",
     "an assertion/finding/witness id"),
    (r"[<≥] ?\d+\.\d+", "a threshold comparison (`< 0.6`)"),
    (r"docs/evidence/\d+[\w-]*/", "a sibling bundle's path"),
    # ⛔ EXACT figures, not a shape (#14691 review, round 4, finding 4): `\d+`
    # let `3800 / 4400` through while the row cites a raw-data artifact that
    # carries neither. This is the peer lane's own probe output and appears in no
    # artifact of this bundle, so it is allowed verbatim or not at all.
    (r"node_count: 38, edge_count: 44\b",
     "the PEER probe's own node/edge counts for Lumen Core (VERIFICATION §2, "
     "attributed there to the probe, not to this bundle's artifacts)"),
)


def _prose_tokens() -> set[str]:
    text = (_report() + "\n\n" + (BUNDLE / "VERIFICATION-independent.md").read_text())
    # ⛔ THE REVIEW LOG IS `### 8.1` UP TO `## 9.` — every round's `### 8.x` section — NOT the
    # whole of §8 (#14691 review, round 15, finding 2). ⛔ ITS UPPER END NAMES THE NEXT
    # TOP-LEVEL HEADING, NEVER A SECTION NUMBER: it read `..### 8.19` in the commit that added
    # 8.19, and the NEXT commit added 8.20, so the sentence was false one commit after it was
    # written — the rot the paragraph below says an unexplained exclusion invites, one round
    # later. A log section is added every round, so a numbered end rots by construction.
    # The bound used to START at `### 8.1` and run to §9 — excluding 8.2–8.16
    # as well, under a reason that named only the mutant tables. Bounding at `## 8.` fixed that
    # and WIDENED the hole in the same move: it also swallowed `## 8. Artifacts`, the bundle's
    # own artifact INVENTORY (`809,812 bytes`, `39 tools`, `5 transcripts, 4,295 chars`,
    # `11 verbatim / 27 paraphrase / 12 not in corpus`), under a reason that is FALSE of it —
    # that table IS a set of bundle measurements. Measured: adding `scored across 1,234 checks`
    # to its `out/t6-score.json` row left the whole suite GREEN under the `## 8.` bound, and
    # REDs under this one. So the exclusion is the review LOG only, and §8's artifacts table is
    # scanned like any other claim table.
    # ⛔ AND THE REASON THE LOG ITSELF IS EXCLUDED, which nothing else in this file states: its
    # numbers are what each round's falsifier run PRINTED — pytest counts, mutant descriptions,
    # `N passed` — and no artifact carries them, so this scan cannot grade them and must not
    # pretend to. The §8 figures that ARE claims about an artifact are graded by their OWN arms
    # (`test_EVERY_bytes_figure_in_the_documents_is_the_committed_file`,
    # `test_every_CODE_citation_in_the_documents_is_one_an_artifact_carries`), never by riding on
    # this exclusion. Round 16 deleted this paragraph with the stale bound above it and the
    # surviving text dangles on it; restored here, because an unexplained exclusion is one the
    # next lane "fixes" by un-excluding a log full of historical mutants (round 17, finding 3).
    text = re.sub(r"(?ms)^### 8\.1 .*?(?=^## 9\.)", " ", text)
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    for pattern, _reason in _PROSE_ALLOWED:
        text = re.sub(pattern, " ", text, flags=re.M)
    return {t for t in re.findall(r"\b\d[\d,]*(?:\.\d+)?\b", text)}


def test_no_FIGURE_in_the_two_documents_is_free():
    """Reds if a number in the shipped prose matches no artifact value.

    ⛔ THE CLASS THE REVIEWERS KEPT FINDING (5th, 6th and 7th cycles): a number in
    prose that nothing recomputes. Individual pins close the figures they name —
    and the next figure is free again. This arm inverts the burden: every numeric
    token left after removing contexts that are not claims (section refs, source
    line refs, issue ids, dates, times, addresses, thresholds, lint rule ids) must
    be a number some committed artifact actually carries.

    Measured mutants that this arm reds (each green before it existed):
    `24 Objects and 11 Subjects` → `99/98`, `809,812 bytes · 791 KB` →
    `809,999 · 792`, `6 / 10 / 10` → `60 / 100 / 100`, and any invented figure in
    VERIFICATION-independent.md.
    """
    def norm(x: str) -> str:
        f = float(x.replace(",", ""))
        return str(int(f)) if f == int(f) else str(f)

    supported = {norm(n) for n in _supported_numbers()}
    tokens = _prose_tokens()
    assert len(tokens) > 60, (
        f"the prose scan found only {len(tokens)} numeric tokens — the extraction "
        "itself has stopped working, so this arm would be vacuous")
    unsupported = sorted((t for t in tokens if norm(t) not in supported),
                         key=lambda x: float(x.replace(",", "")))
    assert not unsupported, (
        "these numbers appear in REPORT.md / VERIFICATION-independent.md and in "
        "no committed artifact — a claim nothing in the bundle supports: "
        f"{unsupported}. Pin the figure to its field, or drop it.")


def _verification() -> str:
    return (BUNDLE / "VERIFICATION-independent.md").read_text()


def _independent() -> dict:
    return json.loads((BUNDLE / "out" / "t6-independent-verification.json").read_text())


def _audit() -> dict:
    return json.loads((BUNDLE / "out" / "t6-quote-audit.json").read_text())


def _surface() -> dict:
    return json.loads((BUNDLE / "out" / "tool-surface.json").read_text())


def test_the_VERIFICATION_verdict_line_is_the_score_artifact():
    r"""Reds if the "independent" document states a verdict the artifact denies.

    ⛔ #14691 review, round 4, P1-1. Measured green on `7bf32ccdb7`: changing
    `A2 FAIL` → `A2 PASS` in the verdict line left **63 passed**. That is the one
    sentence a reader takes as the second measurement's conclusion, and it says
    the bundle's central defect does not exist — the exact fail-open the
    `#14606` review was filed for.
    """
    verdicts = _score()["verdicts"]
    stated = re.search(
        r"\bA1 (\w+) · A2 (\w+) · A3 (\w+) · A4 (\w+) · A5 (\w+)", _verification())
    assert stated, "VERIFICATION-independent.md states no A1..A5 verdict line"
    for key, word in zip(("A1", "A2", "A3", "A4", "A5"), stated.groups(),
                          strict=True):
        assert word == verdicts[key], (
            f"VERIFICATION's verdict line says {key} {word} where "
            f"out/t6-score.json says {key} {verdicts[key]}: {stated.group(0)!r}")


def test_the_isError_envelope_branch_is_LOAD_BEARING():
    r"""Reds if the MCP error-envelope arm is deleted and nothing else notices.

    ⛔ #14691 review, round 4, P2-2. The suite pinned plain-text and `{"error":…}`
    shapes, so DELETING the `isError`/`content`-item branch of `is_error_reply`
    (score_t6.py:124-129) left 63 passed — the branch was only ever exercised
    through a path that another arm already covered. This arm calls the branch's
    own shape directly, so the guard has to exist to pass.
    """
    module = _load_score_module()
    text = {"type": "text", "text": "boom"}
    for envelope in ({"content": [text], "isError": True},
                     {"content": [text], "is_error": True},
                     {"content": [text, {"isError": True}], "isError": False}):
        assert module.is_error_reply(envelope) is True, envelope
        assert module.measured_empty({"tool": "t", "reply": envelope}) is False, (
            envelope)
        assert module.call_failed({"tool": "t", "reply": envelope}) is True, envelope
    # …and a SUCCESSFUL envelope is still an ANSWER, so the arm above is not
    # satisfied by a guard that simply calls everything an error.
    assert module.is_error_reply({"content": [text], "isError": False}) is False
    assert module.measured_empty({"tool": "t", "reply":
                                  {"content": [text], "isError": False}}) is True


def test_the_peer_probe_counts_are_the_exact_measured_numbers():
    r"""Reds if the peer probe's node/edge counts are changed to other digits.

    ⛔ #14691 review, round 4, finding 4 (P3). The §2 probe row cites
    `node_count: 38, edge_count: 44` for `Lumen Core` and points at
    `out/t6-independent-verification.json` as raw data — which carries neither.
    The generic coverage arm allowed ANY digits there, so `3800 / 4400` stayed
    green. The figures are still not derivable from this bundle (they are the peer
    lane's probe output), so the pin is verbatim, and it says so.
    """
    assert "node_count: 38, edge_count: 44" in _verification(), (
        "VERIFICATION's probe row no longer states the peer's 38/44 counts")
    others = set(re.findall(r"node_count: (\d+), edge_count: (\d+)", _verification()))
    assert others == {("38", "44")}, others


def test_the_peer_A3_block_is_the_receipt_and_the_corpus():
    r"""Reds if the peer artifact's A3 figures drift from the receipt/corpus.

    ⛔ #14691 review, round 4, finding 5 (P3). `out/t6-independent-verification.json`
    is a second lane's measurement, and its `A3` block was read by nothing:
    `hasPart_rows_in_receipt` 8 → 13 and `children` 5 → 2 both stayed green. The
    block restates the primary record's A3, so it is derivable from the receipt's
    edges and the corpus's declared products, and is pinned that way here.
    """
    receipt = _receipt()
    declared = _corpus()["_declared_structure"]
    iv = _independent()["A3"]
    rows = [e for d in receipt["documents"]
            for e in ((d["result"].get("relation_write") or {}).get("edges") or [])
            if e.get("predicate") == "hasPart"]
    assert iv["hasPart_rows_in_receipt"] == len(rows), (
        f'peer A3 says {iv["hasPart_rows_in_receipt"]} hasPart rows; the receipt '
        f"has {len(rows)}")
    assert iv["distinct_hasPart_pairs"] == len(
        {(str(e.get("src_id")), str(e.get("dst_id"))) for e in rows}), (
        "peer A3's distinct-pair count is not the receipt's")
    assert set(iv["products"]) == set(declared["products"])
    assert set(iv["children"]) == set(declared["features"])


def test_the_tool_surface_SCHEMAS_are_tool_specific():
    r"""Reds if `out/tool-surface.json`'s per-tool schema content is blanked.

    ⛔ #14691 review, round 4, finding 6 (P3). The names were pinned; the SCHEMAS
    were not — setting every `params`/`required` to junk stayed green, and so did
    flattening all 39 descriptions to `"a tool"`. The readback cites this artifact
    as the advertised surface, so its content is the claim.
    """
    surface = _surface()
    assert len(surface) == 39, len(surface)
    descriptions = [v["description"] for v in surface.values()]
    assert len(set(descriptions)) == len(descriptions), (
        "two tools share a description — the schema content is not tool-specific")
    for name, entry in surface.items():
        assert set(entry) == {"description", "required", "params"}, (name,
                                                                    sorted(entry))
        assert len(entry["description"]) >= 25, (name, entry["description"])
        assert entry["params"], f"{name} advertises no params"
        assert isinstance(entry["params"], list) and isinstance(
            entry["required"], list), name
        assert set(entry["required"]) <= set(entry["params"]), (
            f"{name} requires a param it does not advertise")
        assert len(set(entry["params"])) == len(entry["params"]), name
    assert sum(len(v["params"]) for v in surface.values()) == 256, (
        "the advertised parameter population drifted")
    assert sum(len(v["required"]) for v in surface.values()) == 30, (
        "the advertised required-parameter population drifted")


def test_the_VERIFICATION_corpus_row_is_the_receipt():
    r"""Reds if the peer document's corpus figures drift from the receipt.

    ⛔ #14691 review, round 4, finding 7 (P3). `5 documents, 30 turns` → `35 turns`
    stayed green: `35` is a real number elsewhere in the bundle, so the generic
    coverage arm could not see it, and nothing recomputed the turn count.
    """
    receipt = _receipt()
    chars = _score()["corpus_chars"]
    turns = sum(int(d["turns"]) for d in receipt["documents"])
    line = next((ln for ln in _verification().splitlines()
                 if ln.startswith("| Corpus |")), None)
    assert line, "VERIFICATION's corpus row is gone"
    assert f"{chars:,} characters" in line, line
    assert f"{len(receipt['documents'])} documents" in line, line
    assert f"{turns} turns" in line, line


def test_the_W3_hasPart_counts_are_the_peer_artifact():
    r"""Reds if the W3 narrative's row/pair counts are changed to other real values.

    ⛔ #14691 review, round 4, finding 8 (P3). `**8** hasPart rows` → `**13**`
    stayed green: 13 is a real count elsewhere (§7). The paragraph describes the
    peer lane's own measurement, so it is pinned to that artifact's A3 block.
    """
    iv = _independent()["A3"]
    text = _verification()
    assert f'holds **{iv["hasPart_rows_in_receipt"]}** `hasPart` rows' in text, (
        "W3's hasPart row count is not the peer artifact's")
    assert f'for **{iv["distinct_hasPart_pairs"]}** distinct product→feature pairs' \
        in text, "W3's distinct-pair count is not the peer artifact's"


def test_the_readback_LAYERS_taxonomy_is_the_receipts_overview():
    r"""Reds if the readback's cached overview taxonomy drifts from the receipt.

    ⛔ #14691 review, round 4, finding 9 (P3). `readback.json.layers` re-states a
    whole `tortoise_graph_overview` reply — a SECOND copy of the §2 taxonomy, read
    by nothing: `Object: 24 → 84`, `Subject: 11 → 91` stayed green (file size
    preserved by shortening another key). A second copy that nobody pins is a copy
    that will disagree.
    """
    cached = _readback()["layers"]["tortoise_graph_overview:{}"]
    assert cached["ok"] is True, "the cached overview is not a successful call"
    assert cached["reply"]["taxonomy"] == _receipt()["graph_overview_after"][
        "taxonomy"], (
        "readback's cached taxonomy is not the receipt's overview taxonomy")


def test_the_graph_ops_claim_is_the_receipts_five_totals():
    r"""Reds if the amplification paragraph rounds the receipt's graph_ops away.

    ⛔ #14691 review, round 4, finding 12 (P3). The sentence said
    `graph_ops.total` was "in the low thousands per capture" — an adjective, so
    no numeric arm could see it, and "low ten-thousands" (false: the five totals
    are 1,177 / 684 / 880 / 728 / 853) stayed green. Stated as the receipt's own
    five numbers instead, so the sentence is checkable and one of them drifting
    reds it.
    """
    totals = [d["result"]["graph_ops"]["total"] for d in _receipt()["documents"]]
    spelled = " / ".join(f"{t:,}" for t in totals)
    assert f"`{spelled}`" in _report(), (
        f"REPORT's graph_ops sentence is not the receipt's five totals ({spelled})")
    assert f"`{totals[0]:,}`" in _report(), totals


def test_the_audited_overlap_list_is_the_audit_artifact():
    r"""Reds if any audited overlap is swapped for another REAL overlap.

    ⛔ #14691 review, round 4, finding 13 (P3). `0.636` → `0.875` in the printed
    list stayed green: both are real rows of `out/t6-quote-audit.json`, so the
    coverage arm was satisfied. The list is the artifact's own sequence, so it is
    pinned row by row.
    """
    overlaps = [row["overlap"] for row in _audit()["audited"]]
    listed = re.search(
        r"Its audited rows' overlaps are\n`([\d., ]+)`, so", _verification())
    if listed is None:
        listed = re.search(r"audited rows' overlaps are\s*`([\d., ]+)`",
                           _verification())
    assert listed, "VERIFICATION no longer prints the audited overlap list"
    shown = [float(x) for x in listed.group(1).split(",")]
    assert shown == overlaps, (
        f"VERIFICATION's overlap list is not the audit artifact's: {shown} vs "
        f"{overlaps}")


# ══════════════════════════════════════════════════════════════════════════════
# Round 5 of review — the corroboration table, the graph under test, and four
# artifacts' second copies (each ONE mutation, whole file, restored byte-for-byte).
# ══════════════════════════════════════════════════════════════════════════════


def _verification_rows() -> dict[str, list[str]]:
    """The corroboration table's rows, keyed by their label column."""
    rows: dict[str, list[str]] = {}
    for line in _verification().splitlines():
        if not line.startswith("| **"):
            continue
        cells = [c.strip() for c in line.split("|")[1:-1]]
        if len(cells) != 4:
            continue
        rows[cells[0].replace("*", "")] = cells
    return rows


def test_the_corroboration_TABLE_verdicts_and_agreements_are_the_artifact():
    r"""Reds if a corroboration row's verdict or its Agree cell is flipped.

    ⛔ #14691 review, round 5, P2. The table's *Agree?* and *Primary record*
    columns were read by NOTHING. Measured on `ae2bcfc864`: `✅ agree` →
    `❌ DISAGREE` and `PASS half — 5/5` → `FAIL — 0/5` each left 73 passed. The
    table is the document's whole conclusion — nine rows that say whether the
    second measurement corroborates the first — so both columns are pinned here:
    the verdict word to the artifact, the Agree cell to the row's own verdict.
    """
    totals = _score()["A2_edges"]["totals"]
    verdicts = _score()["verdicts"]
    rows = _verification_rows()
    assert len(rows) >= 9, sorted(rows)
    for label, cells in rows.items():
        verdict_cell, agree = cells[1], cells[3]
        head = label.split()[0].strip("*")
        plain = verdict_cell.replace("*", "").strip()
        word = next((w for w in ("PASS", "FAIL", "PARTIAL")
                     if plain.upper().startswith(w)), None)
        assert word, (label, verdict_cell)
        if head == "A2" and "containment" in label:
            expected = "PASS"          # 5/5 present; orientation is not at stake
        elif head == "A2" and "dependency" in label:
            expected = "PASS"          # 7/7 oriented correctly
        elif head == "A2":
            expected = "FAIL" if totals["asymmetric_rows_inverted"] else "PASS"
        else:
            expected = verdicts[head]
        assert word == expected, (
            f"corroboration row {label!r} says {word} where the artifact says "
            f"{expected}: {verdict_cell[:70]!r}")
        # ⛔ A4 is the ONE row that cannot agree: the peer probe ran no
        # traceability check, so its ✅ would assert a corroboration it did not
        # make (the row's own text says "not independent").
        want = "⚠️" if head == "A4" else "✅"
        assert agree.startswith(want), (
            f"corroboration row {label!r} agrees with {agree!r}; expected {want}"
            f" — a disagreement marker in a row whose verdict is {word} reverses "
            "the document's conclusion")


def test_the_document_names_the_graph_under_test_and_never_the_forbidden_one():
    r"""Reds if the peer document's graph row drifts off the measured graph.

    ⛔ #14691 review, round 5, P2. The *Graph under test* row is the first thing a
    reader checks the numbers against, and REPORT's identical row is pinned while
    this one was not: rewriting it to `live-v5` — the production graph the bundle
    is explicitly forbidden to touch — left 73 passed.
    """
    graph = _score()["graph"]
    assert graph and graph != "live-v5"
    line = next((ln for ln in _verification().splitlines()
                 if ln.startswith("| Graph under test |")), None)
    assert line, "VERIFICATION has no Graph-under-test row"
    assert f"`{graph}`" in line, line
    assert "live-v5" not in _verification(), (
        "the peer document names the forbidden production graph")


def test_the_tool_surface_SCHEMAS_cannot_be_ROTATED_between_tools():
    r"""Reds if one tool's params/required are swapped for another's.

    ⛔ #14691 review, round 5, P3. The per-tool SHAPES were pinned but not their
    attribution: rotating `(params, required)` between tools preserves the 256/30
    populations and every shape, so it stayed green. The digest binds each schema
    to its own tool name.
    """
    surface = _surface()
    blob = json.dumps({name: {"params": entry["params"],
                              "required": entry["required"]}
                       for name, entry in surface.items()}, sort_keys=True)
    digest = hashlib.sha256(blob.encode()).hexdigest()
    assert digest == (
        "e1a66647e3596dae734b915cbf302cb8983db11aa795f237689a4d6a78e35ff3"), (
        "the tool-surface schema attribution drifted — a rotation between tools "
        "keeps every shape and both populations, so the digest is what reds")


def test_the_readback_RESOLVED_map_is_its_own_elements():
    r"""Reds if `readback.json.resolved` stops resolving the elements it cites.

    ⛔ #14691 review, round 5, P3. The prose cites `resolved`, and nothing read it:
    truncating the map to one entry left 73 passed. Every element the artifact
    resolved must still be resolved, with the id the element carries.
    """
    readback = _readback()
    resolved = readback["resolved"]
    assert resolved, "readback.json resolved no element at all"
    assert len(resolved) >= len(readback["elements"]), (
        f"resolved carries {len(resolved)} entries for "
        f"{len(readback['elements'])} elements")
    # TWO declared components were genuinely NOT resolved by name, and the
    # artifact says so in `unresolved_elements` — that is D2's finding, not a
    # pin to loosen. An element must be in exactly one of the two places.
    unresolved = set(readback["unresolved_elements"])
    for element in readback["elements"]:
        key = element["name"].lower()
        if f'{element["element_kind"]}:{element["name"]}' in unresolved:
            assert key not in resolved, (
                f'{element["name"]} is listed unresolved but IS resolved')
            continue
        assert key in resolved, f"readback.json no longer resolves {element['name']}"
        assert resolved[key]["id"] == element["root_id"], element["name"]
    assert readback["graph_name"] == _score()["graph"], (
        "readback.json was measured against a different graph than the score")


def test_the_peer_surface_booleans_are_the_SURFACE_artifacts():
    r"""Reds if the peer artifact's `*_listed` booleans are flipped to True.

    ⛔ #14691 review, round 5, P3. `tortoise_recall_listed` /
    `tortoise_get_governance_listed` are the row's whole point (both tools EXECUTE
    while the advertised surface lists neither). Flipping them to `True` left 73
    passed — which would delete the product finding.
    """
    iv = _independent()["tool_surface"]
    surface = _surface()
    for name in ("tortoise_recall", "tortoise_get_governance"):
        key = f"{name}_listed"
        assert iv[key] is (name in surface), (
            f"peer artifact says {key}={iv[key]!r}; out/tool-surface.json "
            f"{'lists' if name in surface else 'does not list'} {name}")
    assert iv["advertised_tools"] == len(surface)


def test_the_witness_rows_are_EPISTEMIC_and_the_D4_witness_is_isolated():
    r"""Reds if a witness row's mechanism is rewritten to a structural predicate.

    ⛔ #14691 review, round 5, P3. `explore[0].mechanism` `IMPL` → `hasPart` stayed
    green: D4's claim is that the door reports EPISTEMIC edges and no structural
    one, so a structural predicate in a witness row would invert the finding.
    """
    witness = _readback()["link_witness"]
    assert len(witness) == 7, sorted(witness)
    epistemic = {"supports", "contradicts", "refines", "IMPL", "NAND"}
    rows_total = 0
    for name, entry in witness.items():
        rows = _rows(entry["expand_relationships"]["reply"])
        rows_total += len(rows)
        for row in rows:
            assert row.get("predicate") in epistemic, (name, row.get("predicate"))
            assert row.get("mechanism") in epistemic, (name, row.get("mechanism"))
    assert rows_total == 1, (
        f"the witnesses returned {rows_total} epistemic rows in total; D4's claim "
        "is that exactly one connected witness answers and six are isolated")
    silent = [n for n, e in witness.items()
              if not _rows(e["expand_relationships"]["reply"])]
    assert len(silent) == 6, silent


# ══════════════════════════════════════════════════════════════════════════════
# Round 6 of review — the prose findings that carried no derivation, the peer's
# second copy of the witness rows, and a `--check` that did not check.
# ══════════════════════════════════════════════════════════════════════════════


def test_a_PROSE_door_finding_must_agree_with_its_own_evidence():
    r"""Reds if a prose-carried door finding's answer is reversed.

    ⛔ #14691 review, round 6, P1. D1 and D5 arrive from `readback.json` entirely as
    prose, and their `answer_over_mcp` was copied through by the scorer unread.
    Measured on `ecf4fd5d92`: reversing D1's answer to *"YES — … its direction is
    visible too"* left **79 passed**, SELF-CONTRADICTING the `the_read_that_can`
    field in the same object (that key exists precisely because no MCP door reports
    direction). A finding whose answer contradicts its own evidence is not a
    finding, so the scorer DROPS it — which reds here, because the artifact's
    committed answer no longer derives.
    """
    module = _load_score_module()
    readback = _readback()
    d1 = next(f for f in readback["door_findings"] if f["id"] == "D1")
    assert d1["answer_over_mcp"].upper().startswith("NO")
    assert "the_read_that_can" in d1["evidence"], (
        "D1's evidence no longer names the only read that can see direction")

    # ⛔ DERIVED IN MEMORY, NEVER BY WRITING THE COMMITTED ARTIFACT. This arm used
    # to rewrite `out/readback.json` (and `out/t6-score.json`) in place and restore
    # them from an `ExitStack` callback — against this file's own rule, 1,500 lines
    # above, that "a test must never touch a committed artifact". `score_t6` reads
    # `READBACK` as a module global at CALL time, so patching the loaded module is
    # the seam and no file has to move. A `finally`/`ExitStack` cannot save a
    # committed artifact from a SIGKILL inside CI's `timeout -s INT -k 10` window,
    # and `git status` would not tell the next lane why the bundle was dirty.
    assert "D1" in [f["id"] for f in module._door_findings()], (
        "the committed readback no longer derives D1 at all — this arm is "
        "vacuous, not green")
    patched = json.loads(json.dumps(readback))
    for finding in patched["door_findings"]:
        if finding["id"] == "D1":
            finding["answer_over_mcp"] = (
                "YES — the edge, its type and its direction are visible too")
    # flip D1's answer and re-derive: the finding must NOT survive
    module.READBACK = patched
    derived = [f["id"] for f in module._door_findings()]
    assert "D1" not in derived, (
        "the scorer still publishes D1 with its answer reversed — a finding "
        "that contradicts its own evidence must be dropped")


def test_D4_cannot_survive_its_witnesses_all_failing():
    r"""Reds if D4 is published while every witness call was an error.

    ⛔ #14691 review, round 6, P1 (second half). The append path guards against
    errored calls, but the PROSE D4 arriving from `readback.json` was never
    re-checked: with all seven witness replies replaced by failure text the finding
    stayed in the artifact, with its evidence still citing six "isolated" nodes and
    the scorer re-deriving the file IDENTICALLY. A finding may only stand while a
    non-errored witness is still silent.
    """
    module = _load_score_module()
    readback = _readback()
    d4 = next((f for f in readback["door_findings"] if f["id"] == "D4"), None)
    assert d4, "the artifact no longer carries D4 at all"

    # ⛔ D4'S EVIDENCE IS A CLAIM TOO (#14691 review, round 7, finding 2). The
    # counts beside the finding — `nodes_read_as_isolated_by_expand` and its
    # byte-identical duplicate — were read by NOTHING, so rotating them made the
    # artifact contradict its own `link_witness` block with the size held at
    # 809,812. Each count is the witness's own `explore_neighbour_count`.
    witness = readback["link_witness"]
    isolated = d4["evidence"]["nodes_read_as_isolated_by_expand"]
    twin = d4["evidence"]["same_nodes_seen_by_explore_connections"]
    assert isolated == twin, (
        "D4's two copies of the isolated-node counts disagree with each other")
    for name, count in isolated.items():
        assert name in witness, (name, sorted(witness))
        assert count == witness[name]["explore_neighbour_count"], (
            f"D4 says {name} is seen by {count} explore neighbours; the witness "
            f"recorded {witness[name]['explore_neighbour_count']}")
    # ⛔ THE SET, not only each count (#14691 review, round 8, F2). Rotating a KEY
    # (`Jonas Bauer: 9` → `Lumen Core: 19`) is byte-neutral, keeps the 809,812 size
    # and re-derives cleanly, and made the artifact claim a node that ANSWERED one
    # row was read as isolated. The key set is exactly the SILENT witnesses.
    silent = {name for name, w in witness.items()
              if not _rows(w["expand_relationships"]["reply"])
              and (w.get("explore_neighbour_count") or 0) > 0}
    assert set(isolated) == silent, (sorted(isolated), sorted(silent))
    assert len(isolated) == 6, sorted(isolated)
    # ⛔ AND THE PREDICATE CLAIM IN `code_path` (#14691 review, round 8, F3). The
    # sentence says which predicates the read covers; at constant length it could be
    # rewritten to `NAND|IMPL|ownedBy only` and stay green, so the arrow-list is
    # read out of the field and each token must be a predicate the graph has.
    claimed = re.search(r"the read covers ([A-Za-z|]+) only", d4["evidence"]["code_path"])
    assert claimed, d4["evidence"]["code_path"]
    tokens = set(claimed.group(1).split("|"))
    assert tokens == {"IMPL", "NAND", "hasPart"}, claimed.group(1)
    known = set(_score()["directions"]) | {"IMPL", "NAND"}
    assert tokens <= known, (tokens - known)
    # ⛔ AND THE LINE RANGE IS THE CITATION'S CONTENT (#14691 review, round 9).
    # The filename and the predicate set were pinned, the range was not:
    # `entity_reads.py:551-559` → `:533-541` (same length) left 86 passed and moved
    # the citation onto `entity_relations`' def line — the read that returns NO
    # direction, i.e. exactly the contrast this field exists to draw. The cited
    # lines are read out of the file, so a moved range cannot stay green.
    span = re.search(r"(tortoise/[\w/]+\.py):(\d+)-(\d+)",
                     d4["evidence"]["code_path"])
    assert span, d4["evidence"]["code_path"]
    path, start, end = span.group(1), int(span.group(2)), int(span.group(3))
    cited = (REPO / path).read_text().splitlines()[start - 1:end]
    text = "\n".join(cited)
    for token in tokens:
        assert token in text, (
            f"D4 cites {path}:{start}-{end} as the read that covers {token}, and "
            f"those lines do not mention it: {text[:120]!r}")
    assert "EPISTEMIC" in text.upper(), (
        f"D4's cited range no longer states the read is epistemic-only: {text[:120]!r}")
    assert "tortoise/entity_reads.py" in d4["evidence"]["code_path"]
    assert (REPO / "tortoise" / "entity_reads.py").is_file(), (
        "D4's code_path names a file this tree does not have")

    # ⛔ IN MEMORY, for the reason the D1 arm above states: no committed artifact is
    # written, so a killed run cannot leave `out/readback.json` mutated.
    module = _load_score_module()
    assert "D4" in [f["id"] for f in module._door_findings()], (
        "the committed readback no longer derives D4 at all — this arm is "
        "vacuous, not green")
    patched = json.loads(json.dumps(_readback()))
    for entry in patched["link_witness"].values():
        entry["expand_relationships"] = {
            "tool": "tortoise_expand_relationships",
            "args": entry["expand_relationships"].get("args") or {},
            "ok": True,
            "reply": ["Error: timeout while expanding relationships"],
        }
    module.READBACK = patched
    derived = [f["id"] for f in module._door_findings()]
    assert "D4" not in derived, (
        "D4 is still published although every witness call FAILED — a failed "
        "call is not a silent door")


def test_the_peer_witness_rows_are_the_scorers_witness_rows():
    r"""Reds if the PEER artifact's witness rows carry a structural mechanism.

    ╔═ #14691 review, round 6, P2. The round-5 arm pinned the scorer's own
    `link_witness`; the peer artifact keeps a SECOND copy of the same rows
    (`witness_neighbourhood_doors`), and rewriting its `mechanism` `IMPL` →
    `hasPart` left 79 passed while contradicting both documents. The second copy is
    pinned here, to the same rule and to the same row counts.
    """
    peer = _independent()["witness_neighbourhood_doors"]
    witness = _readback()["link_witness"]
    epistemic = {"supports", "contradicts", "refines", "IMPL", "NAND"}
    assert len(peer) == 3, sorted(peer)          # a SUBSET: three probes here
    for name, entry in peer.items():
        for row in entry.get("explore") or []:
            assert row.get("predicate") in epistemic, (name, row.get("predicate"))
            assert row.get("mechanism") in epistemic, (name, row.get("mechanism"))
        assert entry["explore_rows"] == len(entry.get("explore") or []), name
    # the same answers, where the two probes asked about the same node: one row on
    # `Lumen Core`, none elsewhere — and the ONE epistemic row overall, which is
    # the whole of D4's "it reports epistemic edges, not structural ones".
    assert peer["Lumen Core"]["explore_rows"] == 1
    assert sum(e["explore_rows"] for e in peer.values()) == 1
    for name, entry in peer.items():
        row = _rows(witness.get(name, {}).get("expand_relationships", {})
                    .get("reply"))
        if name in witness:
            assert entry["explore_rows"] == len(row), (name, entry["explore_rows"],
                                                      len(row))


def test_the_readback_db_uri_names_the_graph_it_measured():
    r"""Reds if `readback.json.db_uri` is pointed at another graph or host.

    ⛔ #14691 review, round 6, finding 3 (P3). `db_uri` says which graph the
    artifact was measured against, and nothing read it: pointing it at
    `live-v5.prod.example:6379/live-v5` with the byte count held at 809,812 left 79
    passed. It must name the lane's private graph, on the lane's own port.
    """
    graph = _score()["graph"]
    uri = _readback()["db_uri"]
    assert uri.endswith("/" + graph), (uri, graph)
    assert "live-v5" not in uri
    assert "127.0.0.1:" in uri, uri
    # ⛔ THE PORT IS PART OF THE CLAIM (#14691 review, round 7, finding 3): the
    # docstring said "the lane's own port" and nothing asserted one, so 16413 →
    # 16414 stayed green. The receipt records the same container's address.
    receipt_uri = _receipt().get("db_uri") or ""
    assert receipt_uri, "the receipt records no db_uri to compare against"
    assert uri.rsplit("/", 1)[0] == receipt_uri.rsplit("/", 1)[0], (
        f"readback measured {uri.rsplit('/', 1)[0]}, the receipt wrote through "
        f"{receipt_uri.rsplit('/', 1)[0]} — different container")


def test_the_scorer_refuses_to_derive_from_a_PARTIAL_contract(tmp_path):
    r"""Reds if `DIRECTIONS` may silently be empty.

    ⛔ #14691 review, round 6, finding 4 (the other half). The ruled table was
    imported inside a bare `try/except`, so running the scorer with the repo root
    off `sys.path` produced a derivation missing `produces` — and `--check` then
    disagreed with a committed artifact that had been produced WITH it. The scorer
    now puts the repo root on `sys.path` itself and FAILS CLOSED when the table is
    unavailable, so a partial contract can never be scored against.
    """
    import re as _re

    module = _load_score_module()
    assert module.DIRECTIONS, module.DIRECTIONS_SOURCE
    assert "UNAVAILABLE" not in module.DIRECTIONS_SOURCE
    assert {"ownedBy", "managedBy", "dependsOn", "produces"} <= set(
        module.DIRECTIONS), sorted(module.DIRECTIONS)
    source = (BUNDLE / "score_t6.py").read_text()
    assert _re.search(r"if not DIRECTIONS:", source), (
        "the scorer no longer fails closed when the direction table is missing")


def test_the_scorer_check_flag_REFUSES_a_corrupted_artifact(tmp_path):
    r"""Reds if `score_t6.py --check` writes instead of checking.

    ⛔ #14691 review, round 6, finding 4 (P3). `sys.argv` was never parsed: the
    scorer always rewrote `out/t6-score.json` and exited 0. The review brief's own
    baseline step is `score_t6.py --check`, so a corrupted artifact would be silently
    REPAIRED by the gate that was supposed to catch it — a no-op gate that reads as
    a pass. Reproduced here in a throwaway copy of the bundle.
    """
    import shutil
    import subprocess
    import sys

    # the copy must sit at the SAME DEPTH as the real bundle, because the scorer
    # derives its repo root from `Path(__file__).resolve().parents[2]`.
    bundle = tmp_path / "docs" / "evidence" / "14691-structure-validation"
    shutil.copytree(BUNDLE, bundle)
    # the scorer derives its repo root from its own path (`parents[2]`), so the
    # copy needs the product on that path — symlinked, never copied.
    for name in ("tortoise", "tools", "config"):
        (tmp_path / name).symlink_to(REPO / name, target_is_directory=True)
    artifact = bundle / "out" / "t6-score.json"
    intact = artifact.read_text()
    assert subprocess.run([sys.executable, "score_t6.py", "--check"], cwd=bundle,
                          capture_output=True).returncode == 0, (
        "the uncorrupted artifact does not pass `--check`")
    broken = intact.replace('"A2": "FAIL"', '"A2": "PASS"', 1)
    assert broken != intact
    artifact.write_text(broken)
    result = subprocess.run([sys.executable, "score_t6.py", "--check"], cwd=bundle,
                            capture_output=True, text=True)
    assert result.returncode == 1, (
        f"`--check` accepted a corrupted artifact (exit {result.returncode})")
    assert artifact.read_text() == broken, (
        "`--check` REWROTE the artifact — it must never write")


def test_the_PRODUCER_and_the_SCORER_share_ONE_error_predicate():
    r"""Reds if the producer grows a second, narrower error spelling again.

    ⛔ #14691 review, round 7, P1. The round-6 producer guard spelled its own
    five-token list while `score_t6.is_error_reply` spelled nine, so the two
    DISAGREED on 6 of 20 reply shapes: the producer still wrote D4 for
    `["Failure: …"]`, `["Refused: …"]`, `["Unauthorized"]` — manufacturing the
    bundle's central product finding from a call that failed in a spelling its own
    guard did not know. Its body is unpinned content (emptying both tuples and
    updating REPORT's documented hash left 85 passed), so the contract is pinned by
    BEHAVIOUR here rather than by a literal.
    """
    module = _load_score_module()
    source = (BUNDLE / "run_readback.py").read_text()
    assert "from score_t6 import call_failed" in source, (
        "run_readback.py no longer asks the scorer which CALLS failed")
    shapes = [
        "Error: timeout",
        "Failure: boom",
        "Refused: nope",
        "Unauthorized",
        "Unavailable",
        "exception in tool",
        "Traceback (most recent call last):",
        ["Error: timeout"],
        ["Failure: boom"],
        ["Refused: nope"],
        ["Unauthorized"],
        ["Unavailable"],
        {"error": "x"},
        {"ok": False},
        {"isError": True},
        {"content": [{"isError": True}], "isError": False},
        "[]",
        {"result": []},
        [{"predicate": "supports"}],
        {"result": [{"predicate": "supports", "mechanism": "IMPL"}]},
    ]
    for shape in shapes:
        expected = module.is_error_reply(shape)
        # the producer's own gate, exercised exactly as it is written
        produced = _producer_answered(shape)
        assert produced is (not expected), (
            f"the producer and the scorer disagree about {shape!r}: "
            f"producer answered={produced}, scorer error={expected}")
    # and the six shapes that DISAGREED before are among them, so this arm cannot
    # pass on a producer that only knows the two shapes the scorer's dict arm knows
    disagreed = [s for s in shapes if module.is_error_reply(s)]
    assert len(disagreed) >= 10, disagreed
    # ⛔ THE ENVELOPE IS PART OF THE CALL (#14691 review, round 8, F1). The reply
    # half alone was pinned, so deleting the `ok is False` / `error` lines left the
    # suite green while `_call()`'s own failure shape — an envelope with NO reply —
    # walked straight through the producer's gate.
    envelopes = [{"ok": False, "error": "MCP isError: boom"},
                 {"ok": False, "error": "TimeoutError: 30s"},
                 {"ok": False},
                 {"ok": True, "error": "boom"},
                 {"ok": True, "reply": []}]
    for call in envelopes:
        expected = module.call_failed(call)
        produced = _producer_answered(None, call=call)
        assert produced is (not expected), (
            f"the producer answered {call!r} while the scorer says failed={expected}")


def _arm_start(flat_src: str) -> int:
    """Index of `verify_independent._call`'s MCP-error `if` in normalised source.

    ⛔ BOUNDED TO `_call`'s BODY, NOT THE FILE (#14723 review, cycle 5, P2).
    Searching from index 0 found the FIRST MCP-error arm anywhere in the file, so
    a second arm added ABOVE `_call` — a shared helper, say — made `_arm_end` close
    on THAT one and the envelope assertion passed on the helper's `"ok": False`
    while `_call`'s own arm was broken. Measured: inserting such a helper and
    flipping `_call`'s envelope to `"ok": True` left this test GREEN, i.e. a false
    pass on the exact arm the test names.
    """
    call = flat_src.index("async def _call(")
    return flat_src.index('if getattr(res, "isError", None) is True', call)


def _arm_end(flat_src: str) -> int:
    """Index of the SUCCESS return that closes that arm, so the slice is the arm."""
    return flat_src.index('return {"tool": tool, "args": args, "ok": True',
                          _arm_start(flat_src))


def test_the_PEER_probe_keeps_the_MCP_error_arm():
    r"""Reds if `verify_independent._call` loses the MCP `isError` arm.

    ⛔ #14723 review, cycle 3, P2. `verify_independent.py` deliberately does NOT
    import `score_t6.call_failed`: that predicate decides over a CALL DICT, while
    the flag this arm reads lives on the MCP RESULT OBJECT (`res.isError`) that has
    not become a dict yet. Its error arm is therefore a MIRROR of
    `run_readback.py`'s rather than the shared predicate — and a mirror the tree
    did not read CAN drift. The consequence of drift is the round-6 defect
    returning in a THIRD file: a failed MCP call recorded as
    `{"ok": True, "reply": ["Error: …"]}`, i.e. a timeout published as a silent
    door in a probe `REPORT.md` calls "re-runnable".

    Measured before this test: `rg -l 'verify_independent' tests/ tools/` is EMPTY
    — no test opened the file at all — so deleting the arm left 96 passed.

    ⛔ AND AN EARLIER CUT OF THIS ARM BOUND THE FILE, NOT THE ARM (#14723 review,
    cycle 4, P2). It asserted `'"ok": False' in src` over the WHOLE file, which
    has TWO failure envelopes (`_call`'s MCP-error arm and its `except` arm) — so
    flipping the arm's own envelope to `"ok": True`, or dropping its `ok` key,
    left this test GREEN on the `except` arm's copy. It now slices the arm out
    first; measured, all three of those mutations RED.
    """
    src = (BUNDLE / "verify_independent.py").read_text()
    # ⛔ NORMALISED, because the predicate is LINE-WRAPPED in both files
    # (`getattr(res, "is_error",\n  None) is True`): a raw substring search reds
    # on a healthy file, which is a false block and not a gate.
    flat_src = " ".join(src.split())
    assert 'getattr(res, "isError", None) is True' in flat_src, (
        "verify_independent._call lost the MCP `isError` arm — a tool EXECUTION "
        "error inside a successful transport reads as an ANSWERED-EMPTY call "
        "again, which is how D4 was first published from calls that never ran")
    assert 'getattr(res, "is_error", None) is True' in flat_src, (
        "verify_independent._call keeps only one spelling of the MCP error flag; "
        "the snake_case one is what some client versions set")
    # ⛔ A LEGIBLE RED FOR A BOUNDARY MOVE. `_arm_end` locates the SUCCESS return,
    # so re-ordering that dict's keys — a semantics-preserving edit — raised a bare
    # `ValueError: substring not found` and never printed the pin's message
    # (#14723 review, cycle 5, observation). This says what moved, first.
    success = 'return {"tool": tool, "args": args, "ok": True, "reply": out}'
    assert success in flat_src, (
        "verify_independent._call's SUCCESS return changed shape, so this pin can "
        "no longer find the end of the MCP-error arm. That is a boundary move, not "
        "a missing arm: re-derive `_arm_end`'s token rather than deleting this arm")
    assert '"ok": False' in flat_src[_arm_start(flat_src):_arm_end(flat_src)], (
        "verify_independent._call's MCP-error arm no longer returns a failure "
        "ENVELOPE, so a consumer reading `ok` sees True for the error it "
        "detected. ⛔ SLICED TO THE ARM ON PURPOSE: this file carries a SECOND "
        "failure envelope (`except`), so a whole-file search passes while the "
        "arm's own envelope is wrong (measured: flipping the arm to "
        "`ok: True` left this green before the slice)")
    # ⛔ AND THE MIRROR IS CHECKED AGAINST THE FILE IT MIRRORS. Pinning only the
    # probe's copy would let `run_readback.py`'s arm move while the probe keeps
    # mirroring a spelling the producer abandoned — drift in either direction is
    # the same defect.
    rb = " ".join((BUNDLE / "run_readback.py").read_text().split())
    for fragment in ('getattr(res, "isError", None) is True',
                     'getattr(res, "is_error", None) is True',
                     'f"MCP isError: {out!r}"[:400]'):
        assert fragment in rb, (
            f"run_readback.py no longer carries {fragment!r} — the arm this probe "
            "mirrors has moved, so the mirror is now of nothing")


def _producer_answered(reply, call=None):
    """`run_readback._answered`'s rule, taken from the file, not restated."""
    source = (BUNDLE / "run_readback.py").read_text()
    start = source.index("    def _answered(call: dict) -> bool:")
    end = source.index("    silent = {name: w[\"explore_neighbour_count\"]", start)
    body = source[start:end]
    dedented = "\n".join(line[4:] if line.startswith("    ") else line
                         for line in body.splitlines())
    import sys as _sys

    if str(BUNDLE) not in _sys.path:
        _sys.path.insert(0, str(BUNDLE))
    namespace: dict = {"__name__": "run_readback_answers"}
    exec(compile(dedented, "run_readback.py:answers", "exec"), namespace)
    return namespace["_answered"](call if call is not None
                                  else {"ok": True, "reply": reply})


def _cited_range(text: str):
    """`(path, start, end, lines)` for the first `file.py:start-end` in `text`."""
    span = re.search(r"((?:tortoise/)?[\w/]+\.py):(\d+)-(\d+)", text or "")
    assert span, f"no citation to check: {text!r}"
    path, start, end = span.group(1), int(span.group(2)), int(span.group(3))
    target = REPO / path if (REPO / path).is_file() else REPO / "tortoise" / path
    assert target.is_file(), f"cited file does not exist: {path}"
    lines = target.read_text().splitlines()[start - 1:end]
    assert lines, f"{path}:{start}-{end} is empty"
    return path, start, end, "\n".join(lines)


def test_every_DOOR_finding_citation_names_CODE_it_can_actually_be_about():
    r"""Reds if any door finding's `code_path`/citation says it says — all six.

    ⛔ #14691 review, round 10. The round-9 arm pinned D4's range; the reviewer then
    measured the SAME defect on the other three findings — every one of these is
    byte-neutral, keeps the artifact's size and re-derives cleanly:
      D1: `hasPart` → `ownedBy` in `structural_edges_seen`; `551-559` → `533-541`;
          `navigation.py:172-179` → `:166-173`
      D3: `bare_returned` `0/0` → `2/3`, which contradicts its own "EMPTY list"
      D6: `sdk.py:62739-62758` → `:61000-61019`, unrelated code
    A citation is a claim about a LINE RANGE, so each range is read out of the
    file and asked whether it is about the thing it is cited for.
    """
    score = _score()
    readback = _readback()
    findings = {f["id"]: f for f in readback["door_findings"]}
    for finding in score["door_findings"]:
        findings.setdefault(finding["id"], finding)

    # ── D1: the predicate list, and both citations
    d1 = findings["D1"]["evidence"]
    epistemic = {"IMPL", "NAND"}
    structural = {p for p in score["edges_by_predicate"] if p not in epistemic}
    seen = set(d1["structural_edges_seen"])
    # the four the door actually surfaced (the graph also carries `produces` and
    # `related`, which this probe did not touch), each one a REAL predicate — so a
    # member swapped for another (`hasPart` → `ownedBy`) is a lost predicate, and a
    # member invented is not in the graph at all.
    assert seen == {"dependsOn", "hasPart", "managedBy", "ownedBy"}, sorted(seen)
    assert seen <= structural, sorted(seen - structural)
    assert seen.isdisjoint(epistemic)
    _, _, _, union_lines = _cited_range(d1["explore_connections"])
    # ⛔ BOTH ARM DIRECTIONS, not the word UNION (#14691 review, round 13, P3-3): a
    # range holding the ROOT-branch union join passed, because it also says UNION.
    # The claim is that the read merges the two directions, so both arrow forms must
    # be in the cited lines.
    compact = re.sub(r"\s+", "", union_lines)
    assert "-[r]->" in compact and "<-[r]-" in compact, (
        f"D1 cites {d1['explore_connections']!r} for the undirected read; those "
        f"lines do not merge both directions: {union_lines[:160]!r}")
    _, _, _, expand_lines = _cited_range(d1["expand_relationships"])
    for predicate in ("IMPL", "NAND", "hasPart"):
        assert predicate in expand_lines, (
            f"D1's expand citation no longer covers {predicate}: "
            f"{expand_lines[:120]!r}")

    # ── D3: the counts must be the probes they name
    d3 = findings["D3"]["evidence"]
    probes = readback["bare_kind_probes"]
    derived = {name: len(_rows(call.get("reply"))) for name, call in probes.items()}
    assert d3["bare_returned"] == derived, (
        f"D3 says the bare spelling returned {d3['bare_returned']}; the probes "
        f"returned {derived} — a NON-empty bare answer would refute the finding")
    assert d3["silent_zero_for"] == [n for n, n_rows in derived.items() if not n_rows]
    assert all(call.get("ok") is not False for call in probes.values()), (
        "a bare-kind probe FAILED; its emptiness is not a measurement")

    # ── D6: the citation and the owners it says were asked
    d6 = next(f for f in score["door_findings"] if f["id"] == "D6")["evidence"]
    _, _, _, owned_lines = _cited_range(d6["code_path"])
    assert "ownedBy" in owned_lines, (
        f"D6 cites {d6['code_path']!r} for the ownership read; those lines never "
        f"mention `ownedBy`: {owned_lines[:160]!r}")
    asked = [n for n, w in readback["link_witness"].items()
             if (w.get("governance_owned_by") or {}).get("reply") is not None
             and _rows((w["governance_owned_by"] or {}).get("reply")) == []]
    assert d6["owners_asked"] == sorted(n for n in asked
                                        if n in set(d6["owners_asked"])), (
        f"D6 says it asked {d6['owners_asked']}; the witnesses answered nothing "
        f"for {sorted(asked)}")
    assert set(d6["rows_returned"]) == set(d6["owners_asked"])
    assert not any(d6["rows_returned"].values()), (
        "D6 claims the ownership door returned rows; that refutes the finding")


def test_a_door_finding_PROSE_claim_is_derived_not_merely_cited():
    r"""Reds if a finding's evidence SENTENCE drifts off the derived facts.

    ⛔ #14691 review, round 11. The round-10 arm checked the cited FILE, never the
    CLAIM the sentence makes, so four mutants stayed green — each byte-neutral:

      D1's `expand_relationships` string → `IMPL|NAND|ownedBy` (the artifact then
        claims the door reports `ownedBy`, while the code it cites, D4's own
        `code_path` and REPORT §4 all say `hasPart`).
      D3's `namespaced_returned` → counts the probes never produced.
      D6's citation satisfied by ANY range that merely mentions `ownedBy`.
      D1's `the_read_that_can` naming a route the product does not mount, and D5's
        "for every node asked about" beside evidence about two nodes.

    Every one of them is a claim about a SET or a POPULATION, so each is checked
    against the artifact that produced it rather than against its own prose.
    """
    score = _score()
    readback = _readback()
    findings = {f["id"]: f for f in readback["door_findings"]}
    for finding in score["door_findings"]:
        findings.setdefault(finding["id"], finding)

    # ── D1: the predicate list IN THE SENTENCE, not only in the list field
    d1 = findings["D1"]["evidence"]
    claimed = re.search(r"only for ([A-Za-z|]+)", d1["expand_relationships"])
    assert claimed, d1["expand_relationships"]
    assert set(claimed.group(1).split("|")) == {"IMPL", "NAND", "hasPart"}, (
        f"D1's prose claims the door reports {claimed.group(1)}; the read it cites "
        "covers IMPL|NAND|hasPart — and D4's code_path says the same")
    # …and the route it names is real, or the sentence is a fabrication
    route = re.search(r"(?:GET |POST )?(/v1/[\w/{}-]+)", d1["the_read_that_can"])
    assert route, d1["the_read_that_can"]
    # the route is real if the module that owns the read spells it — a fabricated
    # route would send a reader looking for an endpoint nothing serves.
    # ⛔ AND IT MUST BE a SERVED ROUTE, spelling and all (#14691 review, round 12,
    # P3-3). "some module's text contains it" admitted a PUT packs route at the same
    # length, and admitted the right one on a DOCSTRING rather than the decorator
    # that serves it.
    # the VERB is part of the claim (`PUT` would name a write), and so is the
    # module: a route served by a different handler at the same path length is a
    # different sentence.
    verb = (re.search(r"\b(GET|POST|PUT|DELETE|PATCH)\b", d1["the_read_that_can"])
            or re.search(r"\b(GET|POST|PUT|DELETE|PATCH)\b", route.group(0)))
    assert verb, d1["the_read_that_can"]
    served = []
    for path in (REPO / "tortoise").glob("*.py"):
        lines = path.read_text(errors="ignore").splitlines()
        for index, line in enumerate(lines):
            hit = re.match(r"\s*@app\.(get|post|put|delete|patch)\(\"([^\"]+)\"",
                           line)
            if hit:
                handler = next((ln for ln in lines[index + 1:index + 4]
                                if re.match(r"\s*(?:async )?def ", ln)), "")
                served.append((hit.group(1).upper(), hit.group(2), path.name,
                               handler.strip()))
    assert served, "no @app route decorators found under tortoise/"
    matches = [entry for entry in served
               if entry[0] == verb.group(1).upper()
               and re.fullmatch(re.sub(r"\{\w+\}", r"[^/]+", entry[1]), route.group(1))]
    assert matches, (
        f"D1 names {verb.group(1)} {route.group(1)}; no served decorator matches "
        f"that verb and path")
    # …and the sentence must name the module whose route it is, so a route that
    # exists elsewhere cannot be borrowed
    # ⛔ AND THE MODULE MATCH IS NOT SATISFIED BY A SUBSTRING (#14691 review, round
    # 14, P3): `… or "entity_reads" in sentence` was a TAUTOLOGY — the sentence
    # begins `entity_reads.entity_relations` — so a PUT packs route at the same
    # length passed, and so did a module that merely MENTIONS the route in a
    # docstring (`navigation._resolve_root` does exactly that). Text is not reach.
    # A route is MOUNTED in one module and IMPLEMENTED in another (`hosted_api`
    # delegates to `entity_reads`), so the derivation is the CALL: every
    # `module.attr` the sentence names must be one the served handler actually
    # invokes. That is read off the handler's AST, so a docstring cannot stand in
    # for a call and an import alias cannot stand in for a module.
    server_module, handler = matches[0][2], matches[0][3]
    handler_def = re.match(r"(?:async )?def (\w+)\s*\(", handler)
    assert handler_def, (
        f"cannot read the handler's name out of {handler!r}; the sentence \"{verb.group(1)} "
        f"{route.group(1)}\" is served by {server_module} and the arm cannot check "
        "what it delegates to")
    module_tree = ast.parse((REPO / "tortoise" / server_module).read_text(
        errors="ignore"))
    handler_fn = next(
        (node for node in ast.walk(module_tree)
         if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
         and node.name == handler_def.group(1)), None)
    assert handler_fn is not None, (
        f"{server_module} serves {route.group(1)} from `{handler_def.group(1)}`, "
        "and that function is not in it")
    callees = set()
    for node in ast.walk(handler_fn):
        if not isinstance(node, ast.Call):
            continue
        called = node.func
        if isinstance(called, ast.Name):
            callees.add((None, called.id))
        elif isinstance(called, ast.Attribute):
            base = called.value.id if isinstance(called.value, ast.Name) else None
            callees.add((base, called.attr))
    named = re.findall(r"\b([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)\b",
                       d1["the_read_that_can"])
    assert named, d1["the_read_that_can"]
    unreached = [f"{module}.{attr}" for module, attr in named
                 if (module, attr) not in callees
                 and not ((None, attr) in callees
                          and module == server_module.removesuffix(".py"))]
    assert not unreached, (
        f"D1 names {unreached} as the read that can {verb.group(1)} "
        f"{route.group(1)}; that route's handler "
        f"({server_module}:{handler_def.group(1)}) never calls it — it calls "
        f"{sorted(f'{m}.{a}' if m else a for m, a in callees)}")

    # ── D3: the namespaced counts are the PROBES' OWN, kind by kind
    probes = readback["bare_kind_probes"]
    namespaced = findings["D3"]["evidence"]["namespaced_returned"]
    assert namespaced, "D3 records no namespaced hits at all"
    # ⛔ DERIVED FROM THE PROBE CALLS, NOT BOUNDED (#14691 review, round 12, P3-2).
    # A positivity test plus an edge-total bound let `product-strategy:product`
    # 2 → 9 through — while the very probe row beside it said 2 and REPORT §4 said
    # "→ 2 rows" — and let a KIND no pack defines (`foo:code`) through on `":" in
    # kind`. Each count is the number of rows its own namespaced probe returned.
    tallies = {}
    for section in ("object_kinds", "subject_kinds"):
        for kind, call in (readback.get(section) or {}).items():
            tallies[kind] = len(_rows(call.get("reply")))
    derived = {kind: n for kind, n in tallies.items() if n}
    assert namespaced == derived, (
        f"D3's namespaced counts are not the probes': {namespaced} vs {derived}")
    assert all(call.get("ok") is not False for call in probes.values())

    # ── D6: the citation must name the read, not merely mention the predicate
    d6 = next(f for f in score["door_findings"] if f["id"] == "D6")["evidence"]
    path, start, end, lines = _cited_range(d6["code_path"])
    assert "_get_owned_entities" in d6["code_path"], d6["code_path"]
    assert re.search(r"(?:async )?def _get_owned_entities", lines), (
        f"D6 cites {path}:{start}-{end} for `_get_owned_entities`, and that range "
        f"does not define it: {lines[:120]!r}")
    # ⛔ AND THE RANGE MUST CARRY THE QUERY IT IS CITED FOR (#14691 review, round
    # 12, P3-4): a range ending at the `def` line and its comments kept 92 passed
    # while the `MATCH` the sentence quotes was outside it.
    # ⛔ THE QUOTED CONSTRUCT, not a token (#14691 review, round 13, P3-3): a range
    # that kept `def` and the word MATCH in a comment passed while the quoted
    # `MATCH (s:Subject)<-[:ownedBy]-(e)` sat outside it. The fragment the sentence
    # quotes is taken from the sentence and looked for in the cited lines.
    quoted = re.search(r'MATCH\s*\([^)]*\)\s*<-\[:\w+\]-\s*\([^)]*\)',
                       d6["code_path"])
    assert quoted, d6["code_path"]
    fragment = re.sub(r"\s+", " ", quoted.group(0)).replace('"', "")
    assert re.sub(r"\s+", " ", lines).replace('"', "").count(fragment) == 1, (
        f"D6 quotes {fragment!r} as the ownership read; it is not in "
        f"{path}:{start}-{end}: {lines[:200]!r}")

    # ── D5: "for every node asked about" must be the POPULATION it measured
    # ⛔ D5 WITHOUT A GATE ON ITS OWN WORDING (#14691 review, round 13, P3-1).
    # The arm was wrapped in `if "every" in text`, and the r12 fix removed "every"
    # from the sentence — so the arm became UNREACHABLE: inserting `raise` as its
    # first statement still left 92 passed. A guard that cannot fire is not a guard.
    d5 = findings.get("D5")
    assert d5, "the artifact no longer carries D5"
    evidence = d5.get("evidence") or {}
    asked = {name for name, w in readback["link_witness"].items()
             if (w.get("graph_overview_topics") or {}).get("reply") is not None}
    assert evidence.get("nodes_asked") == len(asked), (
        f"D5 counts {evidence.get('nodes_asked')} nodes asked; the readback holds "
        f"{len(asked)} topic probes: a failed probe is not a node asked")
    failed = {name for name in asked
              if "not found" in str(readback["link_witness"][name]
                                    ["graph_overview_topics"]["reply"])}
    reported = evidence.get("reported_not_found") or {}
    assert set(reported) == failed, (sorted(reported), sorted(failed))
    # ⛔ THE ID IS THE ONE THE PROBE USED, IN THE SAME OBJECT (#14691 review, round
    # 14, P3). Comparing `reported[name]` to `link_witness[name]["id"]` was `X == X`
    # — both were derived from the same field — so swapping two witnesses' ids
    # (24 = 24 chars) and re-deriving stayed green while the artifact reported one
    # node's "not found" under another node's id. The probe's own arguments record
    # which id it asked about, and THAT is what the evidence must echo.
    for name, node_id in reported.items():
        entry = readback["link_witness"][name]
        assert node_id == entry["id"], (
            f"D5 attributes a 'not found' to {node_id} under {name!r}, whose "
            f"witness id is {entry['id']}")
        probed = {arg for arg in (entry.get("graph_overview_topics") or {})
                  .get("args", {}).values() if isinstance(arg, str)}
        assert node_id in probed, (
            f"D5 reports id {node_id} for {name!r}, but the probe asked about "
            f"{sorted(probed)}")
    # the sentence's numerator and denominator must be those two populations
    nums = re.findall(r"(\d+) of the (\d+) nodes asked", d5["answer_over_mcp"])
    assert nums, d5["answer_over_mcp"]
    assert tuple(map(int, nums[0])) == (len(failed), len(asked)), (
        f"D5 says {nums[0]}; the evidence supports ({len(failed)}, {len(asked)})")
    # ⛔ THE CLAUSE IS GUARDED BY WHAT IT SAYS, NOT BY ITS OWN WORDING (#14691
    # review, round 14, P3). The arm was `if "two doors return" in answer`, so
    # rewriting the clause to "…two doors reject" BOTH changed the claim and
    # DISARMED the check — a self-gating guard. The clause is now read structurally
    # (the verb after "two doors"), and the population it quantifies over is the
    # witnesses' own neighbour counts.
    clause = re.search(r"two doors (\w+)", d5["answer_over_mcp"])
    neighbours = {n: (w.get("explore_neighbour_count") or 0)
                  for n, w in readback["link_witness"].items()}
    assert clause, d5["answer_over_mcp"]
    clause_verb = clause.group(1).lower()
    if clause_verb.startswith("return"):
        assert all(neighbours[n] > 0 for n in failed), (
            f"D5 says the other doors RETURN the nodes it failed on; their "
            f"neighbour counts are {sorted(neighbours.items())}")
    elif clause_verb.startswith("reject"):
        # ⛔ THE CLAIM DECIDES THE DIRECTION OF THE CHECK (#14691 review, round 14,
        # P3). "reject" is falsified by a witness that DOES see the node, so the
        # arm is `== 0`, not `> 0` — the opposite sense was left in place here and
        # the `two doors reject` mutant stayed GREEN until it was measured.
        assert all(neighbours[n] == 0 for n in failed), (
            "D5 says the other doors REJECT these nodes, and the witnesses record "
            f"that they DO see them: {sorted(neighbours.items())}")
    else:
        raise AssertionError(
            f"D5's clause names an operation nothing measured: {clause_verb!r}")
