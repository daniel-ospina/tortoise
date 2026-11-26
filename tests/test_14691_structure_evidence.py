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

import contextlib
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
    payload = reply
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            payload = None
    if isinstance(payload, dict) and (payload.get("ok") is False
                                      or "error" in payload):
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

    # The inversion is a property of the RECEIPT, not of the scorer: every
    # `ownedBy` row the write door reported has a Subject as src and the owned
    # thing as dst.
    rows = [e for doc in _receipt()["documents"]
            for e in ((doc.get("result") or {}).get("relation_write") or {})
            .get("edges") or [] if e.get("predicate") == "ownedBy"]
    assert len(rows) == 6, f"expected 6 ownedBy rows, got {len(rows)}"


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
    reverted = script.read_text().replace("_element_kind", "element_kind")
    assert hashlib.sha256(reverted.encode()).hexdigest() == produced_by, (
        "run_readback.py no longer differs from the producing commit by ONLY the "
        "`element_kind` -> `_element_kind` rename, so REPORT.md §8's stated "
        "delta and the artifact's provenance binding no longer describe it")
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
    assert quotes["not_in_corpus"] == audit["not_in_corpus"], (
        quotes["not_in_corpus"], audit["not_in_corpus"])
    assert quotes["not_in_corpus"] == quotes["invented"], (
        "`not_in_corpus` and `invented` are the same population, counted twice")
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
               for e in quotes["paraphrase_examples"] + quotes["invented_examples"]]
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
    """
    cl = _score()["claim_layer"]
    want = (f"**{cl['verbatim']} verbatim / {cl['paraphrase']} paraphrase / "
            f"{cl['invented']} not in the corpus**")
    flat = " ".join(_report().split())
    assert want in flat, (
        f"REPORT.md's §7 quote breakdown is not the claim layer's: expected "
        f"{want!r}")
    # ...and the claim layer's own count is the artifact's (§8 table row)
    assert cl["not_in_corpus"] == cl["invented"], cl


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


def test_each_door_finding_answer_states_the_artifacts_ANSWER():
    """Reds if a REPORT answer CONTRADICTS its artifact — not merely if it
    changes its first word.

    #14691 review (5th cycle, N3, then NEW-3). The 5th-cycle fix compared
    POLARITY only, so the exact contradiction the finding is about —
    `**No — a bare kind returns exactly the same rows as the namespaced spelling,
    so every declared spelling works.**`, led by "No" — stayed GREEN (measured).
    The cell must now state the artifact's answer: its normalised words PREFIX the
    cell, so the prose may add colour after the machine's answer but may not
    reverse it. The five cells that paraphrased were aligned to the artifact's own
    wording for this reason — the answer is a machine output, and prose about a
    machine output should be the machine's words.
    """
    for finding in _score()["door_findings"]:
        row = _report_line(rf"^\| \*\*{finding['id']}\*\* \|")
        assert row, f"REPORT.md has no row for door finding {finding['id']}"
        cell = _norm_words(row.split("|")[3])
        answer = _norm_words(finding["answer_over_mcp"])
        assert answer, f"{finding['id']} carries no answer in the artifact"
        assert cell.startswith(answer), (
            f"REPORT.md's {finding['id']} answer does not state the artifact's: "
            f"expected the cell to begin {answer!r}, got {cell!r}")


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
        readback.update(saved)


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
    # `test_the_isolation_refusal_is_WIRED_into_the_ingest_runner`, so this file
    # grades each half of the guard exactly once.
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
        "invented": _report_line(r"^\| \*\*invented\*\* \(<60% overlap"),
    }
    for bucket, row in rows.items():
        assert row, f"REPORT.md has no A4 count row for {bucket!r}"
        assert f"**{claim[bucket]}**" in row, (
            f"the A4 table's {bucket} row is not the claim layer's "
            f"({claim[bucket]}): {row!r}")

    # TWO rows name that artifact (§7's source list and §8's artifact table);
    # the one that STATES the figures is the one pinned here, and the other
    # stating a DIFFERENT figure would be caught by the sentence pin above.
    audit_row = _report_line(r"^\| `out/t6-quote-audit\.json` \|.*verbatim")
    assert audit_row, "REPORT.md §8 has no row for out/t6-quote-audit.json"
    want = (f"({claim['verbatim']} verbatim / {claim['paraphrase']} paraphrase / "
            f"{claim['not_in_corpus']} not in corpus)")
    assert want in audit_row, (
        f"§8's quote-audit row is not the claim layer's: expected {want!r} in "
        f"{audit_row!r}")


# ── the 6th review cycle: the artifact's EVIDENCE rows, and the prose ────
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
        # the artifact's sentence, compared on its first six words — prose may add
        # to it, but the claim itself has to be present, not merely the sign
        key = " ".join(artifact.split()[:6])
        assert key in answer, (
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

    # §2 — the post-write overview block, quoted from the receipt
    cells = ", ".join(f'"{k}": {v}' for k, v in after["taxonomy"].items())
    cell(r'^"taxonomy":', f'"taxonomy":  {{{cells}}}', "§2's post-write taxonomy")
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
