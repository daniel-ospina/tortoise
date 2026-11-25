"""#14592 — the operating-model evidence bundle's claims pinned to its own receipts.

WHY THIS FILE EXISTS (#14606 review, findings 1-7). The bundle
(`docs/evidence/14592-operating-model-validation/`) shipped a REPORT whose claims
were unguarded prose: nothing in the tree would redden if a gap statement were
false, and three of them reached review **contradicted by the bundle's own
`out/*.json` receipts** —

* G5 claimed `list_knowledge(kind="claim")` returns "0 rows of 62" while
  `out/census2.json` shows that exact call returning `total: 96`;
* two G5 probes (`tortoise_search(entity_type="object")` with no `kind`,
  `tortoise_query(kind="operator")`) could not return rows by construction;
* §5 counted "7 incoming IMPL edges" where the artifact holds 8 rows (one
  duplicated), and called 8 relationship rows "8 nodes";
* G1 blamed a stage that had already emitted the cross-document edge, which the
  receipt shows was HELD at the S3 identity gate (`stats.held: 3`,
  `held_reasons: {"partial": 6}`) and never persisted;
* G9 judged `check_confidence(topic=…)` against a contract `tortoise/sdk.py`
  documents as a dated belief-chain window, not a population filter.

Each test below names the mutation that reds it. Every one of them is settled
from the committed artifacts and from `REPORT.md` itself: no DB, no docker, no
LLM, no network. The `EVIDENCE_BUNDLE` environment variable exists so the same
pins can be run against the PRE-FIX bundle to prove they are not vacuous (the
measured pre-fix result is in each test's docstring).

The bundle root is resolved from this file, so a moved bundle reds loudly rather
than silently pinning nothing.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUNDLE = Path(os.environ.get(
    "EVIDENCE_BUNDLE",
    REPO / "docs" / "evidence" / "14592-operating-model-validation",
))


def _report() -> str:
    return (BUNDLE / "REPORT.md").read_text()


def _artifact(name: str) -> dict:
    return json.loads((BUNDLE / "out" / name).read_text())


def _reply_rows(reply):
    """The three reply shapes the server uses (dict / rows / one JSON string)."""
    if isinstance(reply, str):
        return _reply_rows(json.loads(reply))
    if isinstance(reply, dict):
        for key in ("result", "results", "relationships", "nodes", "items"):
            if isinstance(reply.get(key), list):
                return reply[key]
        return []
    if isinstance(reply, list):
        if len(reply) == 1 and isinstance(reply[0], str):
            return _reply_rows(reply[0])
        return [row for row in reply if isinstance(row, dict)]
    return []


def _driver(filename: str):
    """Import a bundle driver by path (they are scripts, not a package)."""
    path = BUNDLE / filename
    spec = importlib.util.spec_from_file_location(f"evidence_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ── finding 2 (P1): the claim door's measured answer ───────────────────────────


def test_the_claim_door_returns_the_whole_population_and_the_report_says_so():
    """REDS IF the report restores the "0 rows" claim, or if the artifact drifts.

    Measured pre-fix (`REPORT.md:362`): `list_knowledge(kind="claim")` was
    reported as "returns 0 rows, though the graph has 62 statement Points" while
    this very artifact recorded `total: 96`. Both halves of the pin fail there:
    the reply assertion (96 != 0) and the prose assertion (the claim is present).
    """
    claims = _artifact("census2.json")["claims"]
    assert claims["args"] == {"kind": "claim", "limit": 200}
    reply = claims["reply"]
    assert reply["total"] == 96, "the claim door's population moved — re-read, do not edit"
    assert reply["hasMore"] is False
    bins: dict[str, int] = {}
    for row in reply["results"]:
        bins[row["pointKind"]] = bins.get(row["pointKind"], 0) + 1
    assert bins == {"statement": 62, "event": 34}

    report = _report()
    assert "96" in report.split("### G5")[1].split("### G6")[0], (
        "G5 must state the measured claim population (96), not a restated count")
    assert "returns **0 rows**, though the graph has 62 statement Points" not in report
    assert "kind=\"claim\")` returns **0 rows" not in report


# ── finding 1 (P1): G1's mechanism is the HOLD, not "never emitted" ───────────


def test_g1_names_the_hold_path_the_receipt_records():
    """REDS IF G1 reverts to "S3 produced none", or the hold fields disappear.

    Measured pre-fix: `stats.held`/`held_reasons` appeared NOWHERE in the report,
    and G1's cause was `supersessions: 0` + `search_degraded: false`. The
    receipt says doc-05 held 3 candidates for a partial identity lookup, and that
    one held NAND endpoint names **doc-01's** certificate claim — the edge the
    report said was never built.
    """
    docs = _artifact("ingest-receipt.json")["documents"]
    doc5 = docs[4]["result"]
    assert doc5["stats"]["held"] == 3
    assert doc5["stats"]["held_reasons"] == {"partial": 6}
    assert doc5["stats"]["supersessions"] == 0

    holds = [w for w in doc5["warnings"] if "HELD not minted (#10997 S3 HOLD)" in w]
    assert len(holds) == 6
    assert any(
        "validated cold-chain certificate" in warning and warning.startswith(
            "operator endpoint HELD not minted")
        for warning in holds
    ), "the held NAND endpoint must still name doc-01's certificate claim"
    assert any("3 candidate(s) held" in w and "no hold store was supplied" in w
               for w in doc5["warnings"])

    g1 = _report().split("### G1")[1].split("### G2")[0]
    assert "held" in g1 and "held_reasons" in g1, "G1 must report the hold counters"
    assert "#10997" in g1
    assert "`stats.supersessions: 0` on all five documents" not in g1, (
        "the old 'supersessions: 0 therefore S3 produced none' account is withdrawn")
    assert "S3 ran (`search_queries: 15`, `search_degraded: false`) and produced none" not in g1
    # And the code fact the new account rests on still holds.
    extractor = (REPO / "tortoise" / "extractor_v2.py").read_text()
    assert "path that ships (`hold_store=None`)" in extractor


# ── finding 5 (P2): §5's counts are the artifact's counts ─────────────────────


def test_section_5_counts_every_impl_row_and_does_not_call_rows_nodes():
    """REDS IF the IMPL count is restated, or rows are called nodes again.

    Measured pre-fix: the report said "7 incoming `IMPL` edges" (twice) over an
    artifact holding 8 rows — rows 6 and 8 the same `operator_id` — and called
    `explore_connections`'s 8 relationship rows "8 nodes".
    """
    expand = _reply_rows(_artifact("census3.json")["expand_doc01_decision"]["reply"])
    assert len(expand) == 8
    assert {row["mechanism"] for row in expand} == {"IMPL"}
    operator_ids = [row["operator_id"] for row in expand]
    assert len(set(operator_ids)) == 6, "8 rows across 6 operators"
    assert operator_ids[4] == operator_ids[5] and operator_ids[6] == operator_ids[7], (
        "each MITIGATES-bearing operator is returned once per endpoint")
    contents = [row["related_content"] for row in expand]
    assert len(set(contents)) == 7, "the MITIGATION text repeats through two operators"

    explore = _reply_rows(_artifact("census3.json")["explore_doc01_decision"]["reply"])
    assert [row["operator_id"] for row in explore] == operator_ids, (
        "the explore reply holds the same relationship rows, not node objects")

    report = _report()
    assert "8 incoming `IMPL` rows" in report
    assert "7 incoming `IMPL` edges" not in report
    assert "returns **8 nodes" not in report


# ── finding 4 (P1): every receipt has a producer in the tree ──────────────────


def _receipt_names_written(driver: Path) -> set[str]:
    """The receipt names a driver actually WRITES — from its AST, not its prose.

    A filename MENTION is not a producer: with `run_census2.py`'s write deleted
    but its filename left behind in a comment, a substring check stays green
    (#14606 review F2), so a receipt could lose its producer while the pin that
    claims to guard it kept passing. Walk for a `write_text` call whose receiver
    is an `OUT / "<name>"` path instead.
    """
    written: set[str] = set()
    for node in ast.walk(ast.parse(driver.read_text())):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "write_text"):
            continue
        receiver = node.func.value
        if not (isinstance(receiver, ast.BinOp)
                and isinstance(receiver.op, ast.Div)
                and isinstance(receiver.left, ast.Name)
                and receiver.left.id == "OUT"):
            continue
        name = receiver.right
        if isinstance(name, ast.Constant) and isinstance(name.value, str):
            written.add(name.value)
    return written


def test_every_committed_receipt_has_a_committed_producer():
    """REDS IF an artifact is added, or its producer's WRITE is removed.

    Measured pre-fix: `census2.json` and `census3.json` had NO producer —
    `run_census.py` has exactly one write, to `census.json` — so the report's
    "reproducible" path could not regenerate its own decisive evidence. And
    measured in review: deleting the write while leaving the filename in a
    comment kept the earlier substring pin GREEN, which is why this walks the
    AST for `(OUT / "<name>").write_text(` rather than grepping for the name.
    """
    written: set[str] = set()
    for driver in sorted(BUNDLE.glob("run_*.py")):
        written |= _receipt_names_written(driver)
    receipts = sorted(receipt.name for receipt in (BUNDLE / "out").glob("*.json"))
    assert receipts, "the evidence bundle must commit its receipts"
    unproduced = [name for name in receipts if name not in written]
    assert not unproduced, f"no committed script WRITES: {unproduced}"


def test_the_ingest_driver_refuses_a_graph_that_is_not_empty():
    """REDS IF the isolation guard is removed, or stops being CALLED.

    Measured in review (finding F2): step 1 READ `graph_overview()` and stored it
    as `empty_before`, but nothing refused a non-empty graph — a second
    invocation appends five captures onto the 120 Points already there, every
    document still reports `ok: true, errors: []`, and `ingest-receipt.json` is
    OVERWRITTEN, so §2's isolation claim would silently stop describing the
    artifacts with no red anywhere.
    """
    ingest = _driver("run_ingest.py")
    assert ingest.assert_graph_is_empty(
        {"taxonomy": {"Point": 0, "Event": 0, "Subject": 0,
                      "Object": 0, "Source": 0}}) is None
    try:
        ingest.assert_graph_is_empty({"taxonomy": {"Point": 120}})
    except SystemExit as exc:
        assert "already holds 120" in str(exc), exc
    else:
        raise AssertionError(
            "a second run would APPEND onto 120 Points and overwrite the receipt")
    # ...and the guard must be WIRED to the reading, not merely defined. A COMMENT
    # carrying the call text would satisfy a substring check (the same
    # prose-satisfiable shape the producer pin above was upgraded away from), so
    # look for the Call node.
    tree = ast.parse((BUNDLE / "run_ingest.py").read_text())
    call_sites = [node for node in ast.walk(tree)
                  if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Name)
                  and node.func.id == "assert_graph_is_empty"]
    assert call_sites, "`main()` must CALL the guard, not merely define it"
    assert any(isinstance(arg, ast.Name) and arg.id == "before"
               for call in call_sites for arg in call.args), (
        "the guard must be called on the overview `main()` read (`before`)")


def test_section_0_and_10_state_the_provenance_facts_the_tree_holds():
    """REDS IF §0 restates a guarantee the writers do not implement.

    Measured in review (findings F1/A-F4): §0 claimed *"Every artifact writer
    names itself and its sha256 in a `provenance` block"* (one of five writers
    then defined none) and that `run.sh` *"fails loudly when either is absent"*
    — `TORTOISE_VENV` is a DEFAULT, `DEEPSEEK_API_KEY` is demanded only for
    `run_ingest.py`, and `TORTOISE_DB_URI` was not named at all. §10 made the
    mirror-image overclaim: "Every receipt in `out/` carries a `provenance`
    block" over a directory whose stamp tool grades FOUR of six files.
    """
    report = _report()
    assert "Every artifact writer names itself and its sha256" not in report
    assert "fails loudly when either is absent" not in report
    assert "Every receipt in `out/` carries a" not in report
    flat = " ".join(report.split())
    assert "FOUR define a `provenance` block" in flat, (
        "§0 must state that four of the five run_*.py writers define one")
    assert "four of the six committed receipts" in flat, (
        "§0 must state the receipt count the tree holds")

    # ...and the counts are MEASURED here, not asserted in prose.
    writers = {path.name: path.read_text() for path in BUNDLE.glob("run_*.py")}
    with_provenance = sorted(name for name, body in writers.items()
                             if "_provenance(" in body)
    assert with_provenance == ["run_census.py", "run_census2.py",
                               "run_census3.py", "run_readback.py"], with_provenance
    stamped = sorted(name for name in
                     (path.name for path in (BUNDLE / "out").glob("*.json"))
                     if "provenance" in json.loads((BUNDLE / "out" / name).read_text()))
    assert stamped == ["census.json", "census2.json", "census3.json",
                       "readback.json"], stamped
    # The stamp tool's subject set is the same four, and it says so.
    stamp = (BUNDLE / "stamp_provenance.py").read_text()
    named = set(re.findall(r'\("([^"]+\.json)", "run_[^"]+"\)', stamp))
    assert named == set(stamped), (sorted(named), stamped)


def test_the_evidence_drivers_carry_no_box_bound_path_or_credential_scrape():
    """REDS IF `run.sh` re-bakes this box (the "one invocation" claim).

    Measured pre-fix: `run.sh` had `VENV=/home/cmux/tortoise/.venv` and scraped
    `~/.pi/agent/auth.json` for the LLM key — neither is reproducible elsewhere,
    which is what "reproducible" was claiming for the whole bundle.
    """
    script = (BUNDLE / "run.sh").read_text()
    assert "/home/" not in script
    assert "auth.json" not in script
    assert "TORTOISE_VENV" in script and "DEEPSEEK_API_KEY" in script
    assert '${DEEPSEEK_API_KEY:?' in script


# ── finding 4b (P1): the relation probe cannot settle on a raw turn ──────────


def test_the_decision_probe_selects_an_extracted_statement_not_a_raw_turn():
    """REDS IF the picker regresses to a substring scan over the search hits.

    Measured pre-fix: `run_readback.py` matched the substring "decision" and
    settled on `doc-05_t5` — an episodic turn id — and then wrote
    `expand = {"result": []}` / `explore = []` down as a successful read of the
    decision's relations.
    """
    readback = _driver("run_readback.py")

    # The recorded decision probe's own hits do NOT contain the decision
    # statement, so a picker that guesses would return a turn here.
    probe_rows = _reply_rows(_artifact("readback.json")["search"][0]["reply"])
    assert readback.pick_decision_point(probe_rows) is None, (
        "the probe's hits never carried the decision — a pick here is a guess")

    # Against the claim population, the picker lands on the extracted statement.
    claims = _artifact("census2.json")["claims"]["reply"]["results"]
    picked = readback.pick_decision_point(claims)
    assert picked is not None
    assert picked["id"].startswith("pt_f0a2b965")
    assert picked["pointKind"] == "statement"


def test_an_empty_relation_read_is_recorded_as_a_finding_not_as_a_result():
    """REDS IF the guard is removed or stops naming the three pre-fix defects."""
    readback = _driver("run_readback.py")
    committed = _artifact("readback.json")
    issues = readback.decision_probe_findings(
        committed["decision_point"], committed["expand"], committed["explore"],
        surfaced_by_probe=False)
    assert len(issues) == 3, issues
    assert any("did not surface the decision statement" in issue for issue in issues)
    assert any("returned no relations" in issue for issue in issues)

    census3 = _artifact("census3.json")
    clean = readback.decision_probe_findings(
        census3["expand_doc01_decision"]["args"]["point_id"],
        census3["expand_doc01_decision"], census3["explore_doc01_decision"],
        surfaced_by_probe=True)
    assert clean == [], clean


# ── finding 3 (P1) + 6 (P2): the withdrawn gap and the named contract ────────


def test_g5_withdraws_the_probes_that_could_not_return_rows():
    """REDS IF "no MCP door enumerates Object/Subject/Event" is restated.

    Measured pre-fix: G5 carried three 0-row bullets, two of which could not
    return rows by construction (`tortoise_search` without `kind` never enters
    full-scan; `tortoise_query(kind=)` is a pointKind). The artifact shows the
    malformed probe returning 0 rows for `source` too, which another door reads.
    """
    census = _artifact("census.json")
    assert census["fullscan_source"]["args"] == {"entity_type": "source", "limit": 100}
    assert census["fullscan_source"]["reply"] == {"result": []}
    assert _artifact("readback.json")["entities"][2]["ok"] is True  # kind="source"

    report = _report()
    g5 = report.split("### G5")[1].split("### G6")[0]
    assert "Withdrawn" in g5
    assert '"No MCP door enumerates Object/Subject/Event" is withdrawn' in g5
    # The claim must survive only inside a withdrawal, never as a bare heading.
    assert "### G5 — no MCP door to enumerate Object / Subject / Event layers" not in report


def test_g9_names_the_documented_belief_chain_contract():
    """REDS from either side: G9 restating "inert/system-filter", or sdk.py drifting.

    Measured pre-fix: G9 called the arm "inert" and improvement 7 asked to "make
    `check_confidence(topic=…)` filter rather than return `[]`", while
    `tortoise/sdk.py` documents `topic=` as the dated belief-chain window.
    """
    sdk = (REPO / "tortoise" / "sdk.py").read_text()
    assert "topic= — the dated belief chain for a topic, oldest window first." in sdk

    g9 = _report().split("### G9")[1].split("### G10")[0]
    assert "belief chain" in g9
    assert "inert, and disagreeing doors" not in g9
    improvements = _report().split("## 8. Improvements")[1]
    assert "make `check_confidence(topic=…)` filter rather than return `[]`" not in improvements


# ── finding 7 (P2): the artifacts table describes files a reader can open ────


def test_the_artifacts_table_names_only_files_that_exist():
    """REDS IF a listed artifact is missing, or a scratch file is listed unmarked.

    Measured pre-fix: `REPORT.md:485` listed `out/events.jsonl` as an artifact
    while this bundle's own `.gitignore` excludes it, and the census rows
    described the wrong files' contents.
    """
    section = _report().split("## 10. Artifacts")[1].split("## 11.")[0]
    rows = [line for line in section.splitlines() if line.startswith("| `")]
    assert rows, "the artifacts table must list paths"
    missing = []
    for line in rows:
        rel = line.split("`")[1]
        if "not committed" in line or "scratch" in line:
            continue
        if str(rel).endswith("/"):
            continue
        if not (BUNDLE / rel).exists():
            missing.append(rel)
    assert not missing, f"listed as committed but absent: {missing}"

    scratch = next(line for line in rows if "out/events.jsonl" in line)
    assert "not committed" in scratch and "scratch" in scratch


# ══════════════════════════════════════════════════════════════════════════════
# The PRODUCERS' own behaviour (this lane's half). The cases above grade the
# report; these grade the code the report now claims to run: how a Point is
# selected, what a failed read does to the exit code, and whether each receipt
# says truthfully which code wrote it.
# ══════════════════════════════════════════════════════════════════════════════

import itertools  # noqa: E402  (kept beside its only user)
import subprocess  # noqa: E402

#: The bundle on THIS checkout. The cases below grade committed code, so they do
#: not follow `EVIDENCE_BUNDLE` (which exists to re-run the REPORT pins against a
#: pre-fix bundle).
REAL_BUNDLE = REPO / "docs" / "evidence" / "14592-operating-model-validation"

CERTIFICATE_CLAIM = "ColdPath Logistics holds a validated cold-chain certificate"
DECISION_CLAIM = "We choose ColdPath Logistics as our delivery partner"

#: The four-way tie the review measured on the real corpus: doc-01's claim (the
#: target), doc-05's refutation of it, and two licence statements that also
#: mention a validated cold-chain certificate. Every row is a `statement`, so the
#: `+1 statement` term separates nothing — which is the tie the retired
#: `startswith` preference claimed to break and could not.
CERT_TIE = (
    {"id": "pt_doc01", "pointKind": "statement", "content": CERTIFICATE_CLAIM},
    {"id": "pt_doc05", "pointKind": "statement", "content":
        "Two of ColdPath's vehicles could not show a validated cold-chain "
        "certificate for the January deliveries"},
    {"id": "pt_licence_1", "pointKind": "statement", "content":
        "Our wholesale licence forbids subcontracting chilled goods to a "
        "partner without a validated cold-chain certificate"},
    {"id": "pt_licence_2", "pointKind": "statement", "content":
        "The wholesale licence forbids subcontracting chilled-goods delivery to "
        "any partner that does not hold a validated cold-chain certificate"},
)


def _real_driver(filename: str):
    path = REAL_BUNDLE / filename
    spec = importlib.util.spec_from_file_location(f"real_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_certificate_claim_is_selected_by_identity_in_every_search_order():
    """REDS IF the selection depends on the order the search listed the rows.

    Measured pre-fix on this exact 4-way tie: the ranked `contains`/`startswith`
    picker chose the WRONG row in 18 of 24 permutations (doc-05's refutation wins
    whenever it is listed before doc-01's claim). The claim must win every time.
    """
    census3 = _real_driver("run_census3.py")
    readback = _real_driver("run_readback.py")
    seen = set()
    for order in itertools.permutations(CERT_TIE):
        chosen = census3.pick_point(list(order), CERTIFICATE_CLAIM)
        assert chosen is not None
        assert chosen["id"] == "pt_doc01", [r["id"] for r in order]
        seen.add(chosen["id"])
    assert seen == {"pt_doc01"}
    # `run_readback`'s picker answers the DECISION claim, so drive it with that.
    decision_rows = [
        {"id": "pt_decision", "pointKind": "statement",
         "content": DECISION_CLAIM},
        {"id": "pt_turn", "pointKind": "event", "content":
            "[assistant] Decision: we choose ColdPath Logistics as our delivery "
            "partner after reviewing both options"},
    ]
    for order in itertools.permutations(decision_rows):
        assert readback.pick_decision_point(list(order))["id"] == "pt_decision"


def test_a_near_miss_is_refused_instead_of_being_expanded():
    """REDS IF the picker falls back to "the row that contains the marker".

    That fallback is how doc-05's refutation could be expanded as if it were the
    claim it refutes. A producer that cannot find its own sentence must refuse.
    """
    census3 = _real_driver("run_census3.py")
    readback = _real_driver("run_readback.py")
    near_misses = [row for row in CERT_TIE if row["id"] != "pt_doc01"]
    assert census3.pick_point(near_misses, CERTIFICATE_CLAIM) is None
    assert readback.pick_decision_point(near_misses) is None
    assert census3.pick_point([], CERTIFICATE_CLAIM) is None
    assert readback.pick_decision_point([]) is None


def test_a_row_that_starts_with_the_marker_is_not_the_claim():
    """REDS on the retired `startswith` preference, which scored this row `+2`.

    The term was measured False on every row the corpus produced, so it could
    never fire — and a row like this one, which DOES begin with the marker phrase
    while being a different sentence, is exactly what it would have promoted.
    """
    census3 = _real_driver("run_census3.py")
    readback = _real_driver("run_readback.py")
    lookalike = {"id": "pt_lookalike", "pointKind": "statement", "content":
                 "choose coldpath logistics — a note to self, not the decision"}
    assert census3.pick_point([lookalike], DECISION_CLAIM) is None
    assert readback.pick_decision_point([lookalike]) is None


def test_an_empty_relation_read_is_a_non_zero_exit_not_just_a_printed_finding():
    """REDS IF the guard is printed while `run()` still returns 0.

    The finding was recorded and printed; the process exited green, so a run
    whose headline evidence was missing committed cleanly. The exit code is the
    enforcement the commit message claimed.
    """
    readback = _real_driver("run_readback.py")
    assert readback.verdict({"decision_probe_findings": ["expand … no relations"]}) == 1
    assert readback.verdict({"decision_probe_findings": []}) == 0
    assert readback.verdict({}) == 0

    census3 = _real_driver("run_census3.py")
    resolved = {"resolved": {"expand_doc01_decision": "pt_a",
                             "expand_cert_claim": "pt_b"},
                "expand_doc01_decision": {"reply": {"result": [{"a": 1}]}},
                "expand_cert_claim": {"reply": {"result": [{"a": 1}]}}}
    assert census3.verdict(resolved) == 0
    unresolved = {"resolved": {"expand_doc01_decision": None,
                               "expand_cert_claim": "pt_b"},
                  "expand_cert_claim": {"reply": {"result": [{"a": 1}]}}}
    assert census3.verdict(unresolved) == 1
    empty = {**resolved,
             "expand_doc01_decision": {"reply": {"result": []}}}
    assert census3.verdict(empty) == 1

    census = _real_driver("run_census.py")
    assert census.verdict({"relation_read_findings": []}) == 0
    assert census.verdict({"relation_read_findings": ["no decision point"]}) == 1

    # The WIRING, by AST: deleting the `verdict(...)` return from `run()` reds
    # here rather than silently restoring the always-zero exit. `run_census2` joined
    # this loop in #14606 round 2: it was outside it by construction, which is how a
    # declared-but-never-written `ok` survived review (`run_ingest`'s `main` is graded
    # by its own test below).
    import ast
    census2 = _real_driver("run_census2.py")
    for module in (readback, census3, census, census2):
        tree = ast.parse(Path(module.__file__).read_text())
        run_fn = next(node for node in tree.body
                      if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                      and node.name == "run")
        source = ast.unparse(run_fn)
        assert "verdict(" in source, module.__name__
        returned = {ast.unparse(node.value) for node in ast.walk(run_fn)
                    if isinstance(node, ast.Return) and node.value is not None}
        assert "code" in returned, (module.__name__, returned)


def test_every_receipt_declares_that_its_producer_did_not_write_it():
    """REDS IF a receipt loses its provenance block, or claims a producer wrote it.

    All four `out/*.json` were carried from the 2026-11-25 run made BEFORE their
    producers were committed. `stamp_provenance.py --check` is the gate; this is
    the pinned verdict, including the deviations it MEASURED rather than asserted.
    """
    result = subprocess.run(
        [sys.executable, str(REAL_BUNDLE / "stamp_provenance.py"), "--check"],
        capture_output=True, text=True, cwd=str(REPO))
    assert result.returncode == 0, result.stdout + result.stderr

    blocks = {name: _artifact(name)["provenance"]
              for name in ("census.json", "census2.json", "census3.json",
                           "readback.json")}
    assert all(block["produced_by_committed_producer"] is False
               for block in blocks.values())
    assert blocks["census3.json"]["missing_producer_keys"] == [
        "graph", "provenance", "ok", "resolved"]
    assert len(blocks["census3.json"]["hardcoded_point_ids"]) == 2
    assert blocks["census2.json"]["missing_producer_keys"] == [
        "graph", "provenance", "ok"], (
        "`ok` is a key the producer now WRITES (`verdict`), so this list is exactly the "
        "gap a re-run closes — a phantom entry here could never come true")
    assert blocks["readback.json"]["missing_producer_keys"] == [
        "provenance", "ok", "decision_probe_findings"]
    # `census.json`'s `provenance` key was a CALL RECEIPT, not a block. The shape
    # is recorded and the receipt is kept under the name the fixed producer writes.
    census = _artifact("census.json")
    assert census["provenance"]["provenance_key_shape"] == "call-receipt"
    assert census["provenance"]["renamed_receipt_key"] == (
        "provenance -> confidence_provenance_read")
    assert census["confidence_provenance_read"]["tool"] == "tortoise_check_confidence"


# ── #14606 round 2, finding 2 (P1): G2's defect is an INVERTED edge, not an absent one ──


def test_g2_reports_the_owned_by_rows_its_own_receipt_carries():
    """REDS IF G2 goes back to "never emitted", or the receipt drifts under it.

    Measured pre-fix (this round's review, finding 2): §G2 said *"there is no Object
    endpoint and no `owns`/`performs`/`managedBy` edge from a person to the thing they
    own"* while `out/ingest-receipt.json` carries **8** `ownedBy` rows with
    `write_path: "structural"` — 7 in doc-04 and 1 in doc-03 — two of them resolving
    to a `core:naturalPerson` Subject pointing at an `object`. The prose arm reds on
    that text; the artifact arms red if a re-run drops or renames the rows.
    """
    docs = _artifact("ingest-receipt.json")["documents"]
    edges = [edge for doc in docs
             for edge in ((doc.get("result") or {}).get("relation_write") or {})
             .get("edges") or []]
    owned = [edge for edge in edges if edge["predicate"] == "ownedBy"]
    assert len(owned) == 8, "the ownership rows moved — re-read, do not edit"
    assert all(edge["write_path"] == "structural" for edge in owned)
    assert all(edge["direction"] is None for edge in owned), (
        "with no declared direction, `SEMANTICS_DIRECTIONS['ownedBy']` is the only reading")
    per_doc = [sum(1 for edge in (((doc.get("result") or {}).get("relation_write") or {})
                                 .get("edges") or [])
                    if edge["predicate"] == "ownedBy") for doc in docs]
    assert per_doc == [0, 0, 1, 7, 0], per_doc
    # The old parenthetical's arithmetic was wrong in both directions.
    assert sum(1 for edge in edges if edge["predicate"] == "managedBy") == 1
    assert sum(1 for edge in edges if edge["write_path"] == "structural") == 22
    assert sum(1 for edge in edges if edge["write_path"] == "direct") == 4

    # Two rows have BOTH endpoints re-derivable from the committed artifacts.
    pairs = set()
    for row in _artifact("census.json")["recall_people"]["reply"]["results"]:
        subject = row.get("subject") or {}
        for obj in row.get("related_objects") or []:
            if subject.get("kind") == "core:naturalPerson" and obj.get("entity_type") == "object":
                pairs.add((subject["id"], obj["id"], subject["name"], obj["content"]))
    assert ("1a21036f576-4ff0e1fbd786", "1a21038124d-aa5c6c6cfc8f",
            "Marta Reyes", "cold-chain temperature log") in pairs
    assert ("1a21036f586-adbeff52ccd8", "1a210381266-cfc6aa838e51",
            "Dev Patel", "capital spending approvals") in pairs
    assert {("1a21036f576-4ff0e1fbd786", "1a21038124d-aa5c6c6cfc8f"),
            ("1a21036f586-adbeff52ccd8", "1a210381266-cfc6aa838e51")} <= {
        (edge["src_id"], edge["dst_id"]) for edge in owned}, (
        "the resolved person→object pairs must BE receipt rows, not a paraphrase of them")

    # The convention that makes person→object read as "the object owns the person".
    registry = (REPO / "tortoise" / "pack_registry.py").read_text()
    assert '"ownedBy": "->"' in registry
    assert "means *the fund owns the investment*" in registry
    sdk = (REPO / "tortoise" / "sdk.py").read_text()
    assert 'MATCH (s)<-[:ownedBy]-(e) RETURN properties(e) LIMIT 100' in sdk

    report = _report()
    g2 = report.split("### G2")[1].split("### G3")[0]
    assert "is never emitted" not in g2, "G2 must not restore the claim its receipt refutes"
    assert "inverted" in g2.lower()
    assert "8 `ownedBy` rows" in g2
    assert "the log owns Marta Reyes" in g2
    assert "no `owns`/`performs`/`managedBy` edge from a person" not in report
    assert "Three structural `managedBy`" not in report
    assert "does not emit the role→object" not in report, "the headline state is withdrawn"

    # §5's required-class row and §6's element 14 must not restate the absent claim.
    section5 = report.split("### Required relation classes")[1].split("### Were any")[0]
    assert "ABSENT" not in section5.split("cross-document")[0], (
        "the ownership row is emitted, so it cannot read ABSENT")
    element14 = next(line for line in report.splitlines() if line.startswith("| 14 |"))
    assert "INVERTED" in element14


# ── #14606 round 2, finding 4 (P2): a producer that DECLARES a verdict writes one ──


def test_census2_computes_the_verdict_it_declares_and_exits_on_it():
    """REDS IF `run_census2.py` goes back to `return 0` with `ok` in WRITTEN_KEYS.

    Measured pre-fix: `WRITTEN_KEYS` listed `ok`, the producer never wrote it (so
    `census2.json`'s `missing_producer_keys` reports a key a re-run cannot supply and
    the stamp can never come true) and `run()` returned 0 unconditionally — the same
    fail-open shape the other three producers were changed to close.
    """
    census2 = _real_driver("run_census2.py")
    assert "ok" in census2.WRITTEN_KEYS, "`ok` must stay declared AND be written"

    def receipt(reply, ok=True):
        return {"tool": "t", "args": {}, "ok": ok, "reply": reply}

    good = {
        "claims": receipt({"results": [{"id": "a"}], "total": 1}),
        "conf_one": receipt({"result": [{"id": "b"}]}),
        "structure": receipt({"result": []}),
    }
    assert census2.verdict(good) == 0, (
        "an empty `structure` reply is the validator answering NOTHING TO REPORT")
    assert census2.verdict({**good, "claims": receipt(None, ok=False)}) == 1
    assert census2.verdict({**good, "conf_one": receipt([])}) == 1
    assert census2.verdict({key: good[key] for key in ("claims", "conf_one")}) == 1, (
        "a phase that never ran is not a phase that succeeded")

    # The WIRING, by AST: `run()` must compute AND return the verdict and write `ok`.
    import ast
    tree = ast.parse(Path(census2.__file__).read_text())
    run_fn = next(node for node in tree.body
                  if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                  and node.name == "run")
    source = ast.unparse(run_fn)
    assert "verdict(" in source
    assert "out['ok'] = code == 0" in source, source[-400:]
    returned = {ast.unparse(node.value) for node in ast.walk(run_fn)
                if isinstance(node, ast.Return) and node.value is not None}
    assert returned == {"code"}, returned


def test_run_ingest_returns_non_zero_when_a_capture_recorded_not_ok():
    """REDS IF `run_ingest.py` returns 0 over a corpus whose capture raised.

    Measured pre-fix: a document whose capture raised was recorded `ok: False` and
    `main()` still returned 0, so the phase that produces every later phase's input
    could report success over a corpus it never wrote.
    """
    ingest = _real_driver("run_ingest.py")
    assert ingest.verdict({"documents": [{"ok": True}, {"ok": True}]}) == 0
    assert ingest.verdict({"documents": [{"ok": True}, {"ok": False}]}) == 1
    assert ingest.verdict({"documents": []}) == 1, "a receipt with no document is not a run"

    import ast
    tree = ast.parse(Path(ingest.__file__).read_text())
    main_fn = next(node for node in tree.body
                   if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and node.name == "main")
    source = ast.unparse(main_fn)
    assert "verdict(" in source
    returned = {ast.unparse(node.value) for node in ast.walk(main_fn)
                if isinstance(node, ast.Return) and node.value is not None}
    assert "code" in returned, returned


def test_a_producer_written_block_is_never_stamped_over():
    """REDS IF the stamp replaces a real run's own provenance with a claim."""
    stamp = _real_driver("stamp_provenance.py")
    doc = {"graph": "g", "provenance": {
        "probe_path": "x/run_census3.py", "probe_sha256": "abc", "graph": "g",
        "generated_at": "2026-01-01T00:00:00+00:00"}}
    assert stamp.provenance_key_shape(doc) == "producer-block"
    assert stamp.expected_block("census3.json", "run_census3.py", doc, "") is None


def test_the_read_only_recipe_runs_without_an_llm_credential(tmp_path):
    """REDS IF `run.sh` demands the extraction key for a phase that calls no model.

    `REPORT.md`'s recipe is the whole bundle's entry point, and every step of it
    used to abort before Python started — including the read-only ones — on the
    unconditional `:?` guard.
    """
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").symlink_to(sys.executable)

    env = {key: value for key, value in os.environ.items()
           if key not in ("DEEPSEEK_API_KEY", "TORTOISE_REQUIRE_LLM_KEY")}
    env["TORTOISE_DB_URI"] = "docker://:@127.0.0.1:1/tortoise_test_matrix"
    env["TORTOISE_VENV"] = str(venv)

    def run(*args, **extra):
        return subprocess.run(
            ["bash", str(REAL_BUNDLE / "run.sh"), *args],
            capture_output=True, text=True, env={**env, **extra}, cwd=str(REPO))

    read_only = run("-c", "print('read-only phase ran')")
    assert read_only.returncode == 0, read_only.stderr
    assert "read-only phase ran" in read_only.stdout

    ingest = run(str(REAL_BUNDLE / "run_ingest.py"))
    assert ingest.returncode != 0
    assert "DEEPSEEK_API_KEY" in ingest.stderr

    forced = run("-c", "print('x')", TORTOISE_REQUIRE_LLM_KEY="1")
    assert forced.returncode != 0
    assert "DEEPSEEK_API_KEY" in forced.stderr


def test_the_bundle_is_registered_in_the_docs_index():
    """REDS IF a 15-file evidence bundle ships without a row in `docs/00_index.md`.

    `AGENTS.md` → Documentation Filing Protocol step 3; every neighbouring
    `docs/evidence/` bundle carries one and this one did not.
    """
    index = (REPO / "docs" / "00_index.md").read_text()
    assert "docs/evidence/14592-operating-model-validation/REPORT.md" in index
    row = next(line for line in index.splitlines()
               if "docs/evidence/14592-operating-model-validation/REPORT.md" in line)
    # REDS IF the row cites a gap the report does not define (`G12` was cited for a
    # section REPORT.md does not carry), or restates a READ-side duplicate as a WRITE.
    assert "G12" not in row
    assert "writes one operator TWICE" not in row
    report = _report()
    cited = {f"G{n}" for n in re.findall(r"\*\*G(\d+)\*\*", row)}
    missing = sorted(gap for gap in cited if f"### {gap} " not in report)
    assert not missing, f"the index cites gaps the report does not define: {missing}"
    assert "never emitted" not in row, "the index restated the G2 claim its receipt refutes"
