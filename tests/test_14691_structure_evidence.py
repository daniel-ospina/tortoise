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

import hashlib
import importlib.util
import json
import re
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


def _report_line(pattern: str) -> str:
    """The first REPORT.md line matching `pattern`, or "" — so a pin can name the
    LINE it grades instead of asking whether a token occurs anywhere in the file."""
    for line in _report().splitlines():
        if re.search(pattern, line):
            return line
    return ""


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
    assert "7/7" in depends, depends

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
    `d4fc8fe` (identical). Rather than regenerate a 784 KB receipt to fix a
    comment-sized defect, the exception is STATED in REPORT.md §8 and pinned
    here, so a future drift reds instead of going unnoticed.
    """
    claimed = (_readback().get("provenance") or {}).get("probe_sha256")
    assert claimed, "readback.json carries no provenance binding at all"
    actual = hashlib.sha256(
        (BUNDLE / "run_readback.py").read_bytes()).hexdigest()
    produced_by = (
        "5a441859d7f953a91d493d9606b8c8404018a40eac35b658a75e4fbe6200f192")
    assert claimed in (actual, produced_by), (
        "readback.json's probe_sha256 is neither the committed script's hash nor "
        f"the documented producing-commit hash: {claimed}")
    assert produced_by in _report(), (
        "REPORT.md §8 no longer states the producing-commit hash the artifact "
        "actually carries")


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
    `score_t6.py`, `run_readback.py` and `corpus/structure-corpus.json`. The 13
    falsifiers therefore ran when someone edited the TEST and NEVER when someone
    edited the artifacts they pin — so a PR that hand-rewrote the score artifact
    or restated a headline number shipped with the guard absent.

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
