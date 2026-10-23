"""#9080 — the capstone MEASUREMENT probe's pure contract.

``probes/capstone_measure.py`` is the probe that produces the E1–E5 numbers (the
sibling ``capstone_run.py`` produces a refusal table and measures nothing, #9737).
Its privileged half — a real ``capture_session`` over seven sessions on a live
graph — cannot run here, so this module pins the half that CAN: the bar arithmetic
(read out of the frozen record, never typed), the verdict vocabulary (a REFUSED
measurement is not a RED row), the table shape the gate parses, and the two rules
that stop a row from claiming a number its artifact does not carry.

Every assertion is DB-free, network-free and LLM-free: the module imports no
``tortoise`` symbol at import time, and the only files it reads are the shipped
pre-registration, the landed E1/E2 log and the log the probe itself wrote.
"""
from __future__ import annotations

import importlib.util as _ilu
import json
import os
import re
import sys
import types
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from tools import capstone_verdict_check as cvc  # noqa: E402

PROBE = _REPO / "docs/evidence/7949-capstone/probes/capstone_measure.py"
PREREG = _REPO / "docs/evidence/7949-capstone/preregistration.json"
E1E2 = _REPO / "docs/evidence/7949-capstone/e1-e2.log"


def _load():
    spec = _ilu.spec_from_file_location("capstone_measure_pure", str(PROBE))
    assert spec and spec.loader
    module = _ilu.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


measure = _load()


def _prereg() -> dict:
    return json.loads(PREREG.read_text())


def _mutated(**criteria_thresholds: str) -> dict:
    """The frozen record with some thresholds REPLACED — the non-vacuity lever.

    If the probe carried its bars as constants, every assertion below would pass
    against this record too. They do not: the numbers come out of the record.
    """
    record = _prereg()
    for entry in record["criteria"]:
        if entry["id"] in criteria_thresholds:
            entry["threshold"] = criteria_thresholds[entry["id"]]
    return record


# ── the bars come from the frozen record ─────────────────────────────────

def test_the_bars_are_the_frozen_ones():
    bars = measure.parse_bars(_prereg())
    assert bars["E1"]["multiplier"] == 0.5
    assert bars["E1b"] == {"required": 90, "of": 90}
    assert bars["E2a"] == {"required": 13, "of": 14}
    assert bars["E2b"]["max_fused_share"] == 0.05
    assert bars["E3"]["required_share"] == 1.0
    assert bars["E4"] == {"required": 15, "of": 15}
    assert bars["E5"] == {"ratio": 0.9, "raw_absolute": 0.8}
    assert bars["floors"] == {"e1_objects": 30, "e2b_surviving_units": 30,
                             "e3_derived_nodes": 30, "e5_rate_min_sentences": 10}


def test_a_threshold_edited_in_the_record_moves_the_bar():
    """Non-vacuity: the probe reads the record, so an edit moves the number."""
    bars = measure.parse_bars(_mutated(
        E1="`after_share <= 0.25 × before_share`",
        E4="`= 12 of 15`",
        E5="`>= 0.50` ratio `and >= 0.10` raw absolute"))
    assert bars["E1"]["multiplier"] == 0.25
    assert bars["E4"]["required"] == 12
    assert bars["E5"] == {"ratio": 0.5, "raw_absolute": 0.1}


def test_a_floor_edited_in_the_record_moves_the_floor():
    record = _prereg()
    record["denominator_floors"]["e1_objects"] = 7
    assert measure.parse_bars(record)["floors"]["e1_objects"] == 7


def test_a_threshold_that_cannot_be_parsed_refuses_rather_than_defaulting():
    """An unparseable bar is a GATE error, not a reason to reuse a constant."""
    for field in ("E1", "E1b", "E2a", "E2b", "E3", "E4"):
        record = _mutated(**{field: "to be decided"})
        try:
            measure.parse_bars(record)
        except ValueError:
            continue
        raise AssertionError(f"{field}: an unparseable threshold did not refuse")


def test_a_missing_floor_refuses():
    record = _prereg()
    del record["denominator_floors"]["e3_derived_nodes"]
    try:
        measure.parse_bars(record)
    except ValueError:
        return
    raise AssertionError("a missing floor did not refuse")


def test_a_boolean_floor_is_not_a_count():
    record = _prereg()
    record["denominator_floors"]["e1_objects"] = True
    try:
        measure.parse_bars(record)
    except ValueError:
        return
    raise AssertionError("a boolean floor was accepted as a count")


# ── the arithmetic, and what a refusal means ─────────────────────────────

BARS = measure.parse_bars(json.loads(PREREG.read_text()))


def test_e1_below_the_floor_is_refused_even_though_the_arithmetic_fails():
    """The shipped E1 shape: 25 Objects, no reduction, floor 30.

    0.600000 is not <= 0.300000, so a naive reading says RED. The record's own
    floor says REFUSED — "neither a pass nor a fail" — and REFUSED maps to a
    BLOCKED row, never to a red one: a 25-Object population cannot decide a share,
    and reporting the failure would hide that the population is the problem.
    """
    result = measure.e1_outcome(before_share=0.6, after_share=0.6, objects=25,
                                bars=BARS)
    assert result["outcome"] == "REFUSED", result
    assert result["below_floor"] is True
    assert measure.verdict_word(result) == "BLOCKED"
    assert "bound=0.300000" in result["value"], result["value"]


def test_e1_at_the_floor_scores_normally():
    failed = measure.e1_outcome(before_share=0.6, after_share=0.6, objects=30,
                                bars=BARS)
    assert failed["outcome"] == "FAIL"
    assert measure.verdict_word(failed) == "RED"
    passed = measure.e1_outcome(before_share=0.6, after_share=0.3, objects=30,
                                bars=BARS)
    assert passed["outcome"] == "PASS", passed  # exactly at the bound
    assert measure.verdict_word(passed) == "GREEN"


def test_e1_with_no_measurement_is_blocked_not_red():
    result = measure.e1_outcome(before_share=None, after_share=0.3, objects=99,
                                bars=BARS)
    assert result["outcome"] == "BLOCKED"
    assert measure.verdict_word(result) == "BLOCKED"


def test_e3_scores_the_share_and_refuses_below_the_node_floor():
    red = measure.e3_outcome(anchored=23, derived=70, bars=BARS)
    assert red["outcome"] == "FAIL" and measure.verdict_word(red) == "RED"
    green = measure.e3_outcome(anchored=30, derived=30, bars=BARS)
    assert green["outcome"] == "PASS"
    refused = measure.e3_outcome(anchored=5, derived=20, bars=BARS)
    assert refused["outcome"] == "REFUSED"
    assert measure.verdict_word(refused) == "BLOCKED"


def test_e3_zero_one_whole_derived_node_must_be_anchored():
    """`= 1.000` with zero tolerance: 29 of 30 is a RED, not a rounding."""
    result = measure.e3_outcome(anchored=29, derived=30, bars=BARS)
    assert result["outcome"] == "FAIL", result


def test_e4_is_scored_over_the_frozen_fifteen():
    red = measure.e4_outcome(edge_correct=2, planted=15, bars=BARS)
    assert red["outcome"] == "FAIL" and measure.verdict_word(red) == "RED"
    green = measure.e4_outcome(edge_correct=15, planted=15, bars=BARS)
    assert green["outcome"] == "PASS"
    # a 15-of-15 reading over a DIFFERENT denominator is not the criterion
    wrong_den = measure.e4_outcome(edge_correct=4, planted=4, bars=BARS)
    assert wrong_den["outcome"] == "FAIL", wrong_den


def test_e5_can_pass_the_ratio_and_still_fail_the_absolute_floor():
    """The shipped E5 shape: ratio 1.0833 passes 0.90, raw 0.48 fails 0.80."""
    result = measure.e5_outcome(raw_loss=0.52, narrative_loss=0.48, sentences=25,
                               bars=BARS)
    assert result["ratio_ok"] is True and result["raw_ok"] is False
    assert result["outcome"] == "FAIL"
    assert round(result["ratio"], 4) == 1.0833
    assert round(result["raw_recovery"], 4) == 0.48
    assert measure.verdict_word(result) == "RED"


def test_e5_both_bars_must_hold():
    assert measure.e5_outcome(raw_loss=0.1, narrative_loss=0.1, sentences=25,
                              bars=BARS)["outcome"] == "PASS"
    # a degenerate ratio (both arms at 20%) passes the ratio and must not pass
    both_low = measure.e5_outcome(raw_loss=0.8, narrative_loss=0.8, sentences=25,
                                  bars=BARS)
    assert both_low["ratio"] == 1.0
    assert both_low["outcome"] == "FAIL", both_low


def test_e5_below_the_sentence_floor_is_refused():
    result = measure.e5_outcome(raw_loss=0.52, narrative_loss=0.48, sentences=5,
                               bars=BARS)
    assert result["outcome"] == "REFUSED"


def test_the_table_admits_only_the_gates_four_verdicts():
    values = set()
    for result in (
            measure.e1_outcome(before_share=0.6, after_share=0.6, objects=25,
                               bars=BARS),
            measure.e1_outcome(before_share=0.6, after_share=0.6, objects=30,
                               bars=BARS),
            measure.e1_outcome(before_share=0.6, after_share=0.2, objects=30,
                               bars=BARS),
            measure.e3_outcome(anchored=30, derived=30, bars=BARS)):
        values.add(measure.verdict_word(result))
    assert values <= set(cvc.VERDICTS), values
    assert values == {"BLOCKED", "RED", "GREEN"}, values


# ── the artifacts the probe parses ───────────────────────────────────────

def test_the_e1_e2_artifact_parses_to_the_numbers_the_table_uses():
    parsed = measure.measure_e1_e2(_REPO)
    assert parsed["before_share"] == 0.6
    assert parsed["after_share"] == 0.6
    assert parsed["objects_total"] == 25
    assert parsed["objects_numerator"] == 15
    assert parsed["real_facts"] == 90
    assert parsed["atomic_points"] == 114
    assert parsed["fused"] == 1
    assert parsed["corpus_sha256"] == measure.corpus_sha_of_log(E1E2.read_text())
    assert re.fullmatch(r"[0-9a-f]{7,40}", parsed["sha"] or ""), parsed["sha"]
    # The artifact declares ITSELF unscored — which is why E1 cannot be RED.
    assert parsed["declared_prereg"] == "UNPREREGISTERED"
    assert parsed["declared_result"] == "UNPREREGISTERED"


def test_the_parse_reads_arms_not_a_fixed_line():
    """A synthetic log: each arm's share is its own, so the parser cannot be
    passing by echoing a constant."""
    text = "\n".join([
        "sha=abc1234",
        "arm=before metric=objects total=40 numerator=20 denominator=40 share=0.500000",
        "arm=after-vet-shipped metric=objects total=40 numerator=10 denominator=40 share=0.250000",
        "arm=after-vet-shipped metric=real_facts count=90",
        "e1 before_share=0.500000 after_share=0.250000 verdict=GREEN",
        "result=SCORED",
    ])
    arms = measure.parse_e1e2_log(text)
    assert arms["before"]["objects"]["share"] == "0.500000"
    assert arms["after-vet-shipped"]["objects"]["share"] == "0.250000"
    assert arms["summary"]["e1"]["verdict"] == "GREEN"
    assert measure.parse_e1e2_log(text)["before"] is not None


# ── the table shape, and the two rules that keep a row honest ────────────

def _rows(out_path: str = "docs/evidence/7949-capstone/e1-e2.log") -> list[dict]:
    return measure.build_rows(
        head="a" * 40, out_path=out_path, log_corpus_sha="sha256:" + "d" * 64,
        e1={"verdict": "BLOCKED", "sha": "c" * 40, "corpus_sha256": "e" * 64,
            "note": "E1 note"},
        e2={"verdict": "BLOCKED", "sha": "c" * 40, "corpus_sha256": "e" * 64,
            "note": "E2 note"},
        e3={"verdict": "RED", "numerator": 23, "denominator": 70,
            "note": "E3 note"},
        e4={"verdict": "RED", "numerator": 2, "denominator": 15,
            "sha": "6164c683c", "corpus_sha256": "sha256:" + "f" * 64,
            "note": "E4 note"},
        e5={"verdict": "RED", "numerator": 12, "denominator": 25,
            "note": "E5 note"})


def test_the_table_is_five_rows_in_the_gates_order():
    rows = _rows()
    assert [row["criterion"] for row in rows] == list(cvc.CRITERIA)
    for row in rows:
        assert set(cvc.VERDICTS) >= {row["verdict"]}
        assert row["evidence_kind"] == "measurement"
        assert row["blocked_by"].startswith("#"), row
        assert row["note"].strip(), row


def test_a_green_row_on_a_missing_artifact_is_refused():
    """The core promise, applied to THIS probe's own table shape.

    A row whose ``evidence`` names a file the repo does not carry is an inference,
    not evidence — that is the epic's whole failure mode, and the probe may not be
    exempt from the gate it feeds.
    """
    rows = _rows(out_path="docs/evidence/7949-capstone/does-not-exist.log")
    rows[2].update({"verdict": "GREEN", "numerator": 70, "denominator": 70})
    record = {"schema": cvc.SCHEMA, "prereg": {"sha": "a" * 40, "status": "OK"},
              "corpus": {"sha256": "sha256:" + "d" * 64},
              "run": {"status": "COMPLETED", "steps_observed": list(cvc.STEPS)},
              "rows": rows}
    codes = [code for code, _w, _m in
             cvc.check(record, repo=_REPO, require_green=False).items]
    assert "V4" in codes, codes


def test_a_row_cites_the_artifact_it_was_written_to():
    """The P1 shape, on the probe that replaced it: E3 cites THIS run's log."""
    out = "docs/evidence/7949-capstone/capstone-measured-deadbeef.log"
    rows = {row["criterion"]: row for row in _rows(out_path=out)}
    assert rows["E3"]["evidence"] == out
    assert rows["E1"]["evidence"] == measure.E1E2_LOG
    assert rows["E4"]["evidence"] == measure.E4_RECEIPT
    assert rows["E5"]["evidence"] == measure.E5_RESCORE


def test_a_row_measured_on_another_corpus_declares_it():
    """E1/E2/E4 cite artifacts on other corpus revisions — and say so.

    The declaration is what V7 checks against the artifact's own text, so a row
    that carries ``corpus_sha256`` differing from the log's must also carry
    ``artifact_corpus_sha256``. A row that tried to look like the run's own corpus
    instead would be claiming a measurement it did not take.
    """
    rows = {row["criterion"]: row for row in _rows()}
    for criterion in ("E1", "E2", "E4"):
        row = rows[criterion]
        assert measure.normalise_corpus(row["corpus_sha256"]) != \
            measure.normalise_corpus("sha256:" + "d" * 64), criterion
        assert row.get("artifact_corpus_sha256") == row["corpus_sha256"], criterion
    for criterion in ("E3", "E5"):
        assert rows[criterion]["corpus_sha256"] == "sha256:" + "d" * 64, criterion
        assert "artifact_corpus_sha256" not in rows[criterion], criterion


def test_the_e1_and_e2_siblings_carry_the_same_denominator():
    """V8's rule: the composite pair is carried, not restated differently."""
    rows = {row["criterion"]: row for row in _rows()}
    assert rows["E1"]["sibling"] == "E2"
    assert rows["E2"]["sibling"] == "E1"
    assert (rows["E1"]["numerator"], rows["E1"]["denominator"]) == \
        (rows["E2"]["numerator"], rows["E2"]["denominator"])


def test_the_shipped_measurement_log_is_structurally_clean():
    """The artifact this probe wrote must pass the gate — or say why not.

    Globbed rather than named: the file carries the head it was measured at, and a
    merged branch's head is not this checkout's. Exactly one is expected, so a
    second scored run must be added deliberately (two logs claiming to be THE
    measurement is the ambiguity ``extract_block`` refuses inside a single file).
    """
    logs = sorted((_REPO / "docs/evidence/7949-capstone").glob(
        "capstone-measured-*.log"))
    assert logs, ("no measured capstone log is committed — run "
                  "probes/capstone_measure.py")
    record = cvc.extract_block(logs[0].read_text())
    findings = cvc.check(record, repo=_REPO, require_green=False)
    assert not findings, findings.render()
    verdicts = {row["criterion"]: row["verdict"] for row in record["rows"]}
    assert verdicts["E3"] == "RED", verdicts
    assert verdicts["E4"] == "RED", verdicts
    assert verdicts["E5"] == "RED", verdicts
    assert verdicts["E1"] == "BLOCKED" and verdicts["E2"] == "BLOCKED", verdicts
    # the run completed; two of eight steps are unwitnessed, and the log says
    # which and why (#9116) rather than leaving the count to be inferred
    assert record["run"]["status"] == "COMPLETED"
    assert sorted(record["run"]["steps_observed"]) == sorted(
        ["S0", "S1", "S2.1", "S3", "S5", "S6"])
    assert set(record["run"]["steps_unobserved"]) == {"S2.2", "S2.3"}
    # and the epic stays uncloseable
    codes = [code for code, _w, _m in cvc.check(
        record, repo=_REPO, require_green=True).items]
    assert codes == ["V9"], codes


# ── the measured arm: forced, read back, restored (#9089 round 5 F2) ──────
#
# The shipped probe PRINTED an arm it never set — `grep os.environ[...] =` found
# nothing, so it measured whatever the shell held (the default OFF population)
# under a log declaring both passes ON. These pin the three properties that make
# that impossible: the arm is really set, it is really read back, and it is
# restored.

def test_the_measured_arm_is_forced_in_process_and_restored():
    saved = {key: os.environ.get(key)
             for key in (*measure.ARM_ON, *measure.ARM_OFF)}
    try:
        os.environ["TORTOISE_VET"] = "0"
        os.environ["TORTOISE_CLASSIFY_LATER"] = "0"
        os.environ["TORTOISE_VALUE_GATE"] = "1"
        with measure.measured_arm() as arm:
            assert arm["TORTOISE_VET"] == "1", arm
            assert arm["TORTOISE_CLASSIFY_LATER"] == "1", arm
            # pinned OFF so the arm is a function of the probe, not the shell
            assert arm["TORTOISE_VALUE_GATE"] is None, arm
            assert os.environ["TORTOISE_VET"] == "1"
        assert os.environ["TORTOISE_VET"] == "0"
        assert os.environ["TORTOISE_CLASSIFY_LATER"] == "0"
        assert os.environ["TORTOISE_VALUE_GATE"] == "1"
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class _DeafEnviron(dict):
    """An ``environ`` that accepts writes and reads back nothing."""

    def __setitem__(self, key, value):
        """Accept the write and drop it — the read-back then sees nothing."""
        pass


def test_a_flag_that_does_not_read_back_refuses_the_run():
    """Fail closed: no read-back, no table — never a printed claim."""
    original = measure.os
    measure.os = types.SimpleNamespace(environ=_DeafEnviron())
    try:
        try:
            with measure.measured_arm():
                pass
        except SystemExit as exc:
            assert "REFUSED" in str(exc), exc
            return
        raise AssertionError("a flag that never read back as set did not refuse")
    finally:
        measure.os = original


def test_the_arm_line_is_the_read_back_not_a_typed_literal():
    header = measure.arm_header({"TORTOISE_VET": "1",
                                 "TORTOISE_CLASSIFY_LATER": "7",
                                 "TORTOISE_VALUE_GATE": None})
    assert "TORTOISE_CLASSIFY_LATER=7" in header, header
    assert "TORTOISE_VALUE_GATE=<unset>" in header, header
    record = measure.arm_record({"TORTOISE_VET": "1",
                                 "TORTOISE_CLASSIFY_LATER": "1",
                                 "TORTOISE_VALUE_GATE": None})
    assert record["TORTOISE_VET"] == "1"
    assert "read-back" in record["source"]


def test_the_run_happens_inside_the_forced_arm():
    """A forced arm that the RUN sits outside of would measure nothing."""
    source = PROBE.read_text()
    assert "with measured_arm() as arm:" in source
    assert "runs = run_corpus(graph, sessions)" in source.split(
        "with measured_arm() as arm:", 1)[1].split("failed =", 1)[0]
    assert "arm=arm" in source  # the log renders the read-back, not a literal


# ── E3's anchor direction (#9089 round 5 F1) ──────────────────────────────

def test_the_e3_census_counts_references_inbound_and_extractedfrom_outbound():
    """`references` points AT the derived node; counting it outward counts Sources.

    The shipped log prints `references=7/7` anchored AND `:Event 0/41`, which
    cannot both be true: the 7 anchored references edges ARE the 7 anchored
    Events. This pins the two directions so the pair can never disagree again.
    """
    assert measure.ANCHOR_OUT == ("extractedFrom",)
    assert measure.ANCHOR_IN == ("references",)
    cypher = measure.anchor_census_cypher("Event")
    assert "(n)-[out:extractedFrom]->()" in cypher, cypher
    assert "(n)<-[inc:references]-()" in cypher, cypher
    assert "out_anchors + in_anchors > 0" in cypher, cypher
    assert "TARGET of at least one references" in measure.E3_RULE
    assert "SOURCE of at least one extractedFrom" in measure.E3_RULE


# ── the eight steps: measured, derived, never assigned (#9089 round 5 F3) ─

_FULL_STATS = {
    "edus": 10, "chunks": 1, "entities": 3, "events": 2, "points": 4,
    "search_queries": 5, "search_degraded": False,
    "operator_endpoints_minted": 1,
    "llm": {"by_stage": {"s1": {"p": {"m": {"calls": 1}}},
                          "s2": {"p": {"m": {"calls": 1}}}}},
}

_FULL_CENSUS = {
    "derived_total": 12,
    "per_label": {"Point": {"nodes": 10, "anchored": 8},
                  "Object": {"nodes": 1, "anchored": 0},
                  "Event": {"nodes": 1, "anchored": 1}},
    "operator_relationships": {"IMPL": 1},
    "points": 10, "event_stamped_points": 9, "graph_events": 3,
}


def _run(**overrides) -> dict:
    run = {"session_id": "wp01_quarry_debug", "turns": 3, "ok": True,
           "extraction_mode": "llm:deepseek-direct", "extracted": 2,
           "errors": [], "stats": {}, "elapsed_s": 1.0, "surfaces": {},
           "receipt_keys": ["ok", "stats"], "stats_keys": []}
    run.update(overrides)
    return run


def test_a_witnessed_vet_surface_makes_s2_2_observed():
    """The behaviour #9116's fix must produce — no probe edit required.

    A test pinning "6 of 8 forever" would make the epic's own GREEN gate (V5 =
    every step witnessed) unreachable for EVERY future run, including the one that
    fixes the seam. This pins the opposite: give `observe_steps` a run whose
    receipt carries the surface and all eight steps come back witnessed.
    """
    runs = [_run(stats=dict(_FULL_STATS), stats_keys=sorted(_FULL_STATS),
                 surfaces={"vet": {"enabled": True, "s2": {"kept": 3}},
                           "classify_later": {"enabled": True, "s2": {}}})]
    observed, unobserved = measure.observe_steps(runs, _FULL_CENSUS)
    assert "S2.2" in observed and "S2.2" not in unobserved, observed
    assert "S2.3" in observed and "S2.3" not in unobserved, observed
    assert list(observed) == list(measure.STEPS), observed
    assert not unobserved, unobserved


def test_a_stats_home_for_the_classify_surface_also_counts():
    """The witness may land on the receipt OR in stats; both are the run's own."""
    stats = {**_FULL_STATS, "classify_later": {"enabled": True, "s2": {}}}
    runs = [_run(stats=stats, stats_keys=sorted(stats),
                 surfaces={"vet": {"enabled": True, "s2": {}},
                           "classify_later": {}})]
    observed, _unobserved = measure.observe_steps(runs, _FULL_CENSUS)
    assert "S2.3" in observed, observed


def test_the_unobserved_cause_counts_the_keys_it_actually_inspected():
    """The cause is MEASURED, and it names the layer that actually drops it.

    The shipped constant said "the receipt's stats carries 20 keys" — a number
    nothing counted — and blamed `extract_session_v2`, which in fact BUILDS
    `result["vet"]` (:8288-8292) and `result["classify_later"]` (:8278-8285). The
    drop is the meta fold at `tortoise/sdk.py:12196`. Getting that wrong sends the
    next lane to the wrong file.
    """
    stats = {"edus": 10, "chunks": 1, "search_queries": 5,
             "search_degraded": False, "operator_endpoints_minted": 0,
             "entities": 1, "events": 1, "points": 1,
             "llm": _FULL_STATS["llm"]}
    runs = [_run(stats=stats, stats_keys=sorted(stats),
                 receipt_keys=["ok", "session_id", "stats"])]
    observed, unobserved = measure.observe_steps(runs, _FULL_CENSUS)
    assert "S2.2" not in observed
    cause = unobserved["S2.2"]
    assert f"inspected {len(stats)} stats key(s)" in cause, cause
    assert "tortoise/sdk.py:12196" in cause, cause
    assert "tortoise/extractor_v2.py:8288-8292" in cause, cause
    assert "20 keys" not in cause, cause
    assert "tortoise/sdk.py:12196" in unobserved["S2.3"], unobserved["S2.3"]


def test_every_step_is_witnessed_or_given_a_cause():
    """The count is derived from two total, disjoint sets — never an assignment."""
    observed, unobserved = measure.observe_steps([_run()], {})
    assert set(observed) | set(unobserved) == set(measure.STEPS)
    assert not set(observed) & set(unobserved)


# ── E3's Point denominator is disclosed, not silently mixed (F4) ──────────

def test_the_point_composition_disclosure_is_derived_and_shows_both_readings():
    line = measure.point_composition_line(
        {"points": 311, "turn_rows": 159, "extraction_claims": 126,
         "operator_points": 26, "other_points": 0,
         "claim_denominator": 152, "claim_anchored": 126})
    for fragment in ("turn_rows=159", "extraction_claims=126",
                     "operator_points=26", "total=311",
                     "126/152 = 0.828947", "BY CONSTRUCTION",
                     "tortoise/sdk.py:2306-2331"):
        assert fragment in line, (fragment, line)
    assert measure.derivation_only({}) == (0, 0, None)
    assert measure.derivation_only(None) == (0, 0, None)
