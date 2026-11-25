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

import importlib.util
import json
import os
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


def test_every_committed_receipt_has_a_committed_producer():
    """REDS IF an artifact is added or produced without its script.

    Measured pre-fix: `census2.json` and `census3.json` had NO producer —
    `run_census.py` has exactly one write, to `census.json` — so the report's
    "reproducible" path could not regenerate its own decisive evidence.
    """
    drivers = {path: path.read_text() for path in BUNDLE.glob("run_*.py")}
    unproduced = []
    for receipt in sorted((BUNDLE / "out").glob("*.json")):
        needle = f'"{receipt.name}"'
        if not any(needle in body for body in drivers.values()):
            unproduced.append(receipt.name)
    assert not unproduced, f"no committed script writes: {unproduced}"


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
