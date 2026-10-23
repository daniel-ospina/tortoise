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


#: The scored-measurement logs, by the name the probe writes (`--out` default).
_SCORED_LOG_GLOB = "capstone-measured-*.log"


def _scored_logs() -> list[Path]:
    return sorted((_REPO / "docs/evidence/7949-capstone").glob(_SCORED_LOG_GLOB))


def _generated_stamp(log: Path) -> str:
    """The log's OWN ``# generated:`` stamp — never its filename.

    The filename carries the HEAD the run was stamped at, which is not an ordering: `main` moves and
    a re-run's head can sort either side of the stamp it replaces (``capstone-measured-6020cbd5``
    sorts BEFORE ``capstone-measured-8a8bbcba``). A log without a stamp cannot be ordered at all,
    so it refuses rather than sorting last.
    """
    match = re.search(r"^# generated: (\S+)$", log.read_text(), re.MULTILINE)
    assert match, (f"{log.name}: no `# generated:` stamp — a log without one cannot "
                   f"be ordered against another")
    return match.group(1)


def _newest_log(logs: list[Path]) -> Path:
    """The newest run by STAMP. With one log this is that log; with two it is the newer."""
    assert logs, ("no measured capstone log is committed — run "
                  "probes/capstone_measure.py")
    return max(logs, key=_generated_stamp)


def test_the_scored_log_is_chosen_by_stamp_not_by_filename_order(tmp_path):
    """A second log must never be silently validated because its name sorts first.

    The glob validated ``logs[0]`` — the lexicographically FIRST filename — and asserted only
    non-emptiness, so a superseded log committed green and the artifact validated was whichever
    name sorted first, not the newest run. Measured with this fixture: ``aaa`` sorts first and is
    the OLDER run, so the old selection validated the superseded log while the new one sat beside
    it.
    """
    older = tmp_path / "capstone-measured-aaa.log"   # sorts FIRST, measured EARLIER
    older.write_text("# generated: 2026-10-23T22:24:52Z\n")
    newer = tmp_path / "capstone-measured-zzz.log"   # sorts LAST, measured LATER
    newer.write_text("# generated: 2026-11-01T09:00:00Z\n")
    assert sorted([older, newer])[0] == older
    assert _newest_log([older, newer]) == newer
    assert _newest_log([newer, older]) == newer
    # a log that carries no stamp cannot be ordered, so it is refused rather than
    # silently ranked last
    bare = tmp_path / "capstone-measured-bare.log"
    bare.write_text("# nothing\n")
    try:
        _generated_stamp(bare)
    except AssertionError:
        pass
    else:
        raise AssertionError("a log with no `# generated:` stamp was ordered anyway")


def test_the_shipped_measurement_log_is_structurally_clean():
    """The artifact this probe wrote must pass the gate — or say why not.

    Globbed rather than named: the file carries the head it was measured at, and a merged branch's
    head is not this checkout's. ⛔ EXACTLY ONE must match: this test used to assert only
    non-emptiness and then validate ``logs[0]``, so a second (superseded) log committed green and
    the validated artifact was the lexicographically-first filename rather than the newest run.
    The bundle's own reproduce instruction (re-run under a ``<new-head8>`` filename) produces that
    state on every re-run, so the ambiguity the supersede chain exists to prevent is exactly what
    the assertion has to refuse. Supersede = re-run + DELETE the old file + disclose the chain.
    """
    logs = _scored_logs()
    assert len(logs) == 1, (
        f"expected EXACTLY ONE scored log under "
        f"docs/evidence/7949-capstone/{_SCORED_LOG_GLOB}, found {len(logs)}: "
        f"{[p.name for p in logs]} — a superseded run must be REMOVED, not kept "
        f"beside the current one (supersede by re-running and deleting, with the "
        f"chain disclosed in README.md and docs/00_index.md)")
    log = _newest_log(logs)
    record = cvc.extract_block(log.read_text())
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


def test_the_durations_row_quotes_the_case_count_it_weights():
    """The shard weight's provenance must describe the file that SHIPS.

    #9823 review: the ``durations`` row quoted a 2.5 s / **24 cases** reading
    for a file that had already grown past it (39 at the pre-merge head, 44
    now) — a measurement of a smaller file used as the input to shard packing,
    and a body sentence ``pytest`` can no longer reproduce. The sibling row for
    ``test_7949_capstone_verdict_check.py`` is pinned the same way
    (``test_the_durations_row_quotes_the_test_count_it_weights``); this is the
    same guard for the row nothing covered, so it cannot re-stale silently the
    next time the file grows.

    Non-vacuity: at the pre-fix head the row said 24 cases for a 39-case file,
    so this reds.
    """
    manifest = (_REPO / "config" / "ci-surfaces.yml").read_text()
    row = next((ln for ln in manifest.splitlines()
                if ln.strip().startswith(
                    "test_7949_capstone_measure.py:")), None)
    assert row is not None, "the durations row is the weight this test guards"
    # The COMMENT-ONLY tail, exactly as the refresher reads it (`tools/ci_timing.py`
    # matches `^[ \t]*#\s*unmeasured\b` against the tail it CLEARS on refresh).
    tail = row.split("#", 1)[1] if "#" in row else ""
    unmeasured = bool(re.match(r"[ \t]*unmeasured\b", tail))
    refreshed_or_unmeasured = unmeasured or not tail.strip()
    quoted = re.search(r"(\d+)\s+cases", row)
    if quoted is None:
        assert refreshed_or_unmeasured, (
            "a hand-written durations note must quote the measured case count, "
            f"or LEAD with `# unmeasured` (the refresher owns that tail): {row}")
        return
    shipped = len(re.findall(r"^def test_",
                             Path(__file__).read_text(), re.MULTILINE))
    assert int(quoted.group(1)) == shipped, (
        f"the durations row quotes {quoted.group(1)} cases; the file ships "
        f"{shipped} — re-measure and move the count and the weight together")


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

    An earlier stamp of this bundle printed `references=7/7` anchored AND
    `:Event 0/41`, which cannot both be true: the 7 anchored references edges ARE
    the 7 anchored Events. This pins the two directions so the pair can never
    disagree again.
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
                           "classify_later": {"enabled": True,
                                              "s2": {"kept": 2}}})]
    observed, unobserved = measure.observe_steps(runs, _FULL_CENSUS)
    assert "S2.2" in observed and "S2.2" not in unobserved, observed
    assert "S2.3" in observed and "S2.3" not in unobserved, observed
    assert list(observed) == list(measure.STEPS), observed
    assert not unobserved, unobserved


def test_a_stats_home_for_the_classify_surface_also_counts():
    """The witness may land on the receipt OR in stats; both are the run's own."""
    stats = {**_FULL_STATS, "classify_later": {"enabled": True,
                                              "s2": {"kept": 2}}}
    runs = [_run(stats=stats, stats_keys=sorted(stats),
                 surfaces={"vet": {"enabled": True, "s2": {"kept": 1}},
                           "classify_later": {}})]
    observed, _unobserved = measure.observe_steps(runs, _FULL_CENSUS)
    assert "S2.3" in observed, observed


def test_a_surface_that_says_the_pass_did_not_run_is_not_a_witness():
    """PRESENT is not RAN — the confusion that made 8/8 reachable with both flags OFF.

    `extract_session_v2` emits `result["vet"]` and `result["classify_later"]`
    UNCONDITIONALLY — on the FLAG-OFF arm and on the early-return path the block
    is `{"enabled": False, "s2": {}, "union": {}}` (`:7928-7932`,
    `:8278-8292`). An acceptance test of "a non-empty dict" therefore takes an OFF
    block as the witness for "the S2.2 VET pass RAN" and prints
    `step S2.2 WITNESSED … enabled=false, s2={}`. That is the hazard, not
    pedantry: the moment #9116 copies the sibling key at the meta fold — exactly
    what this bundle instructs — both steps become WITNESSED on EVERY run,
    including one measured with the flags OFF, which is the "OFF population under
    an ON label" failure one layer up. And `V5` ("ALL eight steps observed in one
    pass") is the epic's only closing gate for the step count.

    Measured from the reviewer's reproduction: with
    `surfaces={"vet": {"enabled": False, …}, "classify_later": {"enabled": False, …}}`
    this returned 8/8 observed. It must return 6/8.
    """
    runs = [_run(stats=dict(_FULL_STATS), stats_keys=sorted(_FULL_STATS),
                 surfaces={"vet": {"enabled": False, "s2": {}, "union": {}},
                           "classify_later": {"enabled": False, "s2": {},
                                              "union": {},
                                              "restamp_overrides": 0,
                                              "slot_rekeys": 0}})]
    observed, unobserved = measure.observe_steps(runs, _FULL_CENSUS)
    assert "S2.2" not in observed and "S2.3" not in observed, observed
    assert len(observed) == len(measure.STEPS) - 2, (
        f"an OFF surface was counted as a witness: {sorted(observed)}")
    for step in ("S2.2", "S2.3"):
        assert "did NOT run" in unobserved[step], unobserved[step]
        assert "enabled` reads False" in unobserved[step], unobserved[step]
    # The refusal names WHERE the OFF block was found, so a reader can tell
    # "absent" (the #9116 cause) from "present and off" (this cause).
    assert "receipt['vet']" in unobserved["S2.2"], unobserved["S2.2"]
    assert "tortoise/sdk.py:12401" not in unobserved["S2.2"], unobserved["S2.2"]


def test_the_witness_rule_requires_the_flag_and_a_pass_that_did_work():
    """The rule, stated: activity is REQUIRED, and `enabled: false` can never be outvoted.

    A block that carries no `enabled` field at all is the one shape where non-empty
    `s2`/`union` is the only available evidence — so it counts. A block that says
    OFF while carrying S2 counters contradicts itself, and a self-contradicting
    surface is not evidence of anything (fail closed). And `enabled: true` with
    EMPTY counters is `False`: the flag was read, nothing ran.
    """
    assert measure._pass_ran({"enabled": True, "s2": {"kept": 2}}) is True
    assert measure._pass_ran({"enabled": True, "s2": {},
                              "union": {"kept": 1}}) is True
    assert measure._pass_ran({"enabled": True, "s2": {},
                              "union": {}}) is False
    assert measure._pass_ran({"enabled": False, "s2": {}, "union": {}}) is False
    assert measure._pass_ran({"enabled": False, "s2": {"kept": 2}}) is False
    assert measure._pass_ran({"s2": {"kept": 2}}) is True
    assert measure._pass_ran({"s2": {}, "union": {}}) is None


def test_the_flag_alone_is_not_a_witness_and_has_its_own_cause():
    """`enabled: true` with zero activity is a NON-witness, and not #9116.

    `extractor_v2.py:7912` computes `vet_enabled` (the FLAG) BEFORE the
    `if not edus:` early return at `:7916`, whose surface is literally
    `"vet": {"enabled": vet_enabled, "s2": {}, "union": {}}` (`:7932`); the
    VET passes themselves are gated on the LIST (`if vet_enabled and embed_list:`
    `:8000`, `if vet_enabled and complete_list:` `:8122`). So the flag-ON +
    empty-list session emits `enabled: True` with empty counters and no pass ran —
    and a FAILED vet pass leaves the same shape (`_run_vet_pass` returns `{}` on
    its fail-open path, `:7781-7801`).

    The CLASSIFY pass is DIFFERENT CODE, so the cause is PER SURFACE:
    `classify_later` is computed at `:7881`, its gates are
    `if classify_later and embed_list:` (`:8012`) around `if items:` (`:8015`) and
    `if classify_later and complete_list:` (`:8137`) around `if items:` (`:8147`),
    and its fail-open `except` sits at `:8028` / `:8158`. A cause templated for the
    surface NAME while citing the VET lines told a reader of the classify block
    that the VET pass's gate idled it — false: an idle classify block comes from no
    embed list or no classify ITEMS, and `embed_list` is only its first gate
    (#9823 r4 P2). The assertions below are per step and mutually exclusive, so
    SWAPPING the two causes fails.

    Accepting that block as a witness made S2.2/S2.3 WITNESSED (6/8 -> 8/8) from
    ONE such session, because `_witness_block` returns the first positive anywhere
    on the run. `V5` (all eight steps observed) is the epic's only closing gate
    for the step count, so that would pass on a step nobody observed.

    Measured from the reviewer's reproduction before the fix: with
    `surfaces={"vet": {"enabled": True, "s2": {}, "union": {}}, …}` both steps
    returned WITNESSED. It must return 6/8, with a cause distinct from #9116's.
    """
    idle = {"enabled": True, "s2": {}, "union": {}}
    assert measure._pass_ran(idle) is False
    runs = [_run(stats=dict(_FULL_STATS), stats_keys=sorted(_FULL_STATS),
                 surfaces={"vet": dict(idle), "classify_later": dict(idle)})]
    observed, unobserved = measure.observe_steps(runs, _FULL_CENSUS)
    assert "S2.2" not in observed and "S2.3" not in observed, observed
    assert len(observed) == len(measure.STEPS) - 2, sorted(observed)
    vet_cause = unobserved["S2.2"]
    classify_cause = unobserved["S2.3"]
    for cause in (vet_cause, classify_cause):
        assert "NO pass activity" in cause, cause
        # the three causes are distinct: flag-on/no-activity names the flag and
        # the LIST gate, and must NOT be attributed to the #9116 meta fold
        assert "`enabled` reads True" in cause, cause
        assert "never that the pass ran" in cause, cause
        assert "tortoise/sdk.py:12401" not in cause, cause
        assert "did NOT run" not in cause, cause

    # ── S2.2 cites the VET pass's own code … ──────────────────────────────
    assert "tortoise/extractor_v2.py:7912" in vet_cause, vet_cause
    assert ":7932" in vet_cause, vet_cause
    assert "if vet_enabled and embed_list" in vet_cause, vet_cause
    assert "_run_vet_pass" in vet_cause, vet_cause
    # … and NOT the classify pass's flag or gates
    for classify_cite in ("classify_later", "if classify_later", ":7881",
                          ":8012", ":8015", ":8137", ":8147"):
        assert classify_cite not in vet_cause, (classify_cite, vet_cause)

    # ── S2.3 cites the CLASSIFY pass's own code … ─────────────────────────
    assert "tortoise/extractor_v2.py:7881" in classify_cause, classify_cause
    assert "if classify_later and embed_list" in classify_cause, classify_cause
    assert ":8012" in classify_cause, classify_cause
    assert ":8015" in classify_cause, classify_cause
    assert "if classify_later and complete_list" in classify_cause, classify_cause
    assert ":8137" in classify_cause, classify_cause
    # … and NOT the VET pass's flag, pass function or gates
    for vet_cite in ("vet_enabled", "_run_vet_pass", "if vet_enabled",
                     ":7912", ":7932", ":8000", ":8122", ":7781"):
        assert vet_cite not in classify_cause, (vet_cite, classify_cause)

    # Different text, so neither cause can stand in for the other: a version
    # emitting the SAME sentence for both surfaces would satisfy every
    # "NO pass activity" assertion above and still send one of the two readers to
    # code its pass never runs.
    assert vet_cause != classify_cause
    assert measure._IDLE_CITES["vet"] in vet_cause
    assert measure._IDLE_CITES["classify_later"] in classify_cause

    # and the OFF refusal still reads as the OFF refusal (the two are not merged)
    off = [_run(stats=dict(_FULL_STATS), stats_keys=sorted(_FULL_STATS),
                surfaces={"vet": {"enabled": False, "s2": {}, "union": {}},
                          "classify_later": {"enabled": False, "s2": {},
                                             "union": {}}})]
    _obs, off_unobserved = measure.observe_steps(off, _FULL_CENSUS)
    assert "did NOT run" in off_unobserved["S2.2"], off_unobserved["S2.2"]
    assert "NO pass activity" not in off_unobserved["S2.2"], off_unobserved["S2.2"]


def test_the_flag_on_cause_is_per_surface_so_a_swap_cannot_pass():
    """One table keyed by the surface — and each entry names ITS OWN pass's code.

    The defect this pins: `_witness_block` templated the surface NAME (`{key!r}`)
    into ONE cause whose every citation was the VET pass's (`vet_enabled`,
    `if vet_enabled and …`, `_run_vet_pass`), so the S2.3 line argued about code
    the classify pass never runs. A test asserting the VET cites INSIDE the S2.3
    cause — which is what shipped — pinned the defect instead of catching it; a
    swapped pair fails the exclusivity assertions here.
    """
    cites = measure._IDLE_CITES
    assert set(cites) == set(measure._WITNESS_KEYS), sorted(cites)
    assert set(cites) == {"vet", "classify_later"}
    for key, text in cites.items():
        assert "tortoise/extractor_v2.py:" in text, (key, text)
        assert "never that the pass ran" in text, (key, text)
    # the vet entry never names the classify gates, and vice versa
    for classify_cite in ("classify_later", "if classify_later"):
        assert classify_cite not in cites["vet"], cites["vet"]
    for vet_cite in ("vet_enabled", "_run_vet_pass", "if vet_enabled"):
        assert vet_cite not in cites["classify_later"], cites["classify_later"]
    # the classify entry must name the gate that idles an EMPTY ITEM SET as well
    # as the list gate: `embed_list` alone does not explain a classify block with
    # a populated list and nothing to classify
    assert "if items:" in cites["classify_later"], cites["classify_later"]
    assert "if items:" not in cites["vet"], cites["vet"]


def test_the_unobserved_cause_counts_the_keys_it_actually_inspected():
    """The cause is MEASURED, and it names the layer that actually drops it.

    The shipped constant said "the receipt's stats carries 20 keys" — a number
    nothing counted — and blamed `extract_session_v2`, which in fact BUILDS
    `result["vet"]` (:8288-8292) and `result["classify_later"]` (:8278-8285). The
    drop is the meta fold at `tortoise/sdk.py:12401` — the cite was `:12196` until
    this round, which at this PR's base is a comment line 200 lines away, so the
    bundle was sending the #9116 fixer into unrelated code. Getting it wrong sends
    the next lane to the wrong file; getting the ARITHMETIC wrong sends it to a
    claim nothing supports.
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
    assert "tortoise/sdk.py:12401" in cause, cause
    assert "tortoise/extractor_v2.py:8288-8292" in cause, cause
    assert "tortoise/sdk.py:12196" not in cause, cause
    assert "20 keys" not in cause, cause
    assert "tortoise/sdk.py:12401" in unobserved["S2.3"], unobserved["S2.3"]


def test_every_step_is_witnessed_or_given_a_cause():
    """The count is derived from two total, disjoint sets — never an assignment."""
    observed, unobserved = measure.observe_steps([_run()], {})
    assert set(observed) | set(unobserved) == set(measure.STEPS)
    assert not set(observed) & set(unobserved)


# ── E3's Point denominator is disclosed, not silently mixed (F4) ──────────

def test_the_derivation_figure_divides_by_the_writer_class_not_the_link():
    """Two wrong denominators, in opposite directions — this pins the right one.

    **(1) Too wide.** `claim_denominator` summed extraction claims **+ operator
    Points**, and no operator Point can carry an `extractedFrom` link:
    `create_operator` writes IMPL/NAND/INPUT only. Measured on the run that
    shipped that line (`extractedFrom=140/140` beside `operator_points=29`, 0
    anchored) and again on the run that replaced it (`extractedFrom=132/132`, 0
    of 29 anchored). The 17% short reading was an artefact of the DENOMINATOR,
    printed beside a frozen denominator — exactly where #9736 reads it for the
    re-register-vs-reopen choice.

    **(2) Circular.** The fix took the denominator over `extraction_claims`
    ALONE — a class DEFINED by `deriv > 0`, i.e. by the very link the figure
    reports. It could not say anything but 1.0, and it was invariant to a lost
    link (see the movement test below). The denominator is now the WRITER class:
    non-episodic, non-operator Points = `extraction_claims + other_points`, with
    `other` INSIDE it, because `other` is exactly where a lost link lands.

    Non-vacuity: on the first pre-fix line the denominator is 152 and the figure
    reads `126/152 = 0.828947`; on the second it reads `126/126`. With `other=15`
    here the figure must read `126/141` — neither of the pre-fix readings.
    """
    composition = {"points": 326, "turn_rows": 159, "turn_rows_anchored": 0,
                   "extraction_claims": 126, "extraction_claims_anchored": 126,
                   "operator_points": 26, "operator_points_anchored": 0,
                   "other_points": 15, "other_points_anchored": 0}
    anchored, total, share = measure.derivation_only(composition)
    assert (anchored, total) == (126, 141), (anchored, total)
    assert round(share, 6) == round(126 / 141, 6), share
    line = measure.point_composition_line(composition)
    assert "126/141 = 0.893617" in line, line
    assert "126/126 = 1.000000" not in line, line
    assert "126/152" not in line, line
    assert "0.828947" not in line, line
    for fragment in ("turn_rows=159 (anchored 0)",
                     "extraction_claims=126 (anchored 126)",
                     "operator_points=26 (anchored 0)",
                     "other=15 (anchored 0)",
                     "total=326", "STRUCTURALLY INCAPABLE",
                     "create_operator", "never writes extractedFrom",
                     "BY CONSTRUCTION", "tortoise/sdk.py:2367",
                     # the denominator is stated as the WRITER class, and the
                     # residual is inside it
                     "supposed-to-derive", "the class where a LOST derivation "
                     "link lands",
                     # and it says WHY the link-defined denominator was wrong
                     "NOT defined by the extractedFrom link"):
        assert fragment in line, (fragment, line)
    assert "2306-2331" not in line, line
    assert measure.derivation_only({}) == (0, 0, None)
    assert measure.derivation_only(None) == (0, 0, None)


def test_the_derivation_figure_moves_when_a_derivation_link_is_lost():
    """The circularity, reproduced: the old figure was 1.0 for ANY `other` count.

    Reviewer's check, run against the pre-fix probe: `derivation_only` returned
    ``(132, 132, 1.0)`` for `other_points` of 0, 1, 9 and 40, because
    `other_points` was not an input at all. And dropping ONE link moved a node
    out of `extraction_claims` (defined by `deriv > 0`) into `other`, so the
    figure went ``132/132 -> 131/131`` — still ``1.000000``. A coverage figure
    that cannot move when the thing it measures is lost measures nothing but its
    own definition, and it restated the relationship line `extractedFrom=132/132`
    in node terms.

    The denominator is now fixed by the WRITER, so the two cases differ on the
    SHIPPED run's own composition (141 claims, all anchored, plus 9 `other`):
    ``141/150`` intact versus ``140/150`` when one of the 141 links is dropped
    and that node joins the residual.
    """
    census = {"points": 338, "turn_rows": 159, "turn_rows_anchored": 0,
              "operator_points": 29, "operator_points_anchored": 0}
    # the shipped run's census: 141 claims (all anchored) + 9 other, unanchored
    intact = dict(census, extraction_claims=141, extraction_claims_anchored=141,
                  other_points=9, other_points_anchored=0)
    anchored, total, share = measure.derivation_only(intact)
    assert (anchored, total) == (141, 150), (anchored, total)
    assert round(share, 6) == round(141 / 150, 6), share

    # ONE derivation link lost: the node leaves the link-defined class and joins
    # the residual, exactly as a dropped extractedFrom edge would move it.
    lost = dict(census, extraction_claims=140, extraction_claims_anchored=140,
                other_points=10, other_points_anchored=0)
    anchored_lost, total_lost, share_lost = measure.derivation_only(lost)
    assert (anchored_lost, total_lost) == (140, 150), (anchored_lost, total_lost)
    assert share_lost < share, (share_lost, share)
    assert round(share_lost, 6) == round(140 / 150, 6), share_lost

    # non-vacuity against BOTH pre-fix readings: the old one ignored `other`
    # entirely (1.0 for every count) and the first one folded operator Points in.
    for other in (0, 1, 9, 40):
        _a, t, _s = measure.derivation_only(
            dict(intact, other_points=other))
        assert t == 141 + other, (other, t)
    assert measure.derivation_only(
        dict(intact, extraction_claims_anchored=120,
             extraction_claims=120, other_points=9))[2] < 1.0


# ── the E3 row note: no live count inside a historical claim ─────────────

def _e3_result(anchored: int = 148, derived: int = 433) -> dict:
    return measure.e3_outcome(anchored=anchored, derived=derived,
                              bars=measure.parse_bars(_prereg()))


def _shipped_composition(**overrides) -> dict:
    """The shipped run's census shape (141 claims, all anchored, + 9 `other`)."""
    composition = {"points": 338, "turn_rows": 159, "turn_rows_anchored": 0,
                   "extraction_claims": 141, "extraction_claims_anchored": 141,
                   "operator_points": 29, "operator_points_anchored": 0,
                   "other_points": 9, "other_points_anchored": 0}
    composition.update(overrides)
    return composition


def test_the_e3_note_never_splices_this_runs_counts_into_a_historical_ratio():
    """The superseded-reading clause must not carry THIS run's counts or a typed ratio.

    #9823 review (P3): the note built the historical sentence as
    ``f"{claims_anchored}/{claims} = 1.000000"`` — the current census's counts with a
    HARDCODED quotient. On the shipped run that read ``141/141 = 1.000000``
    (coincidentally consistent, because every claim happened to be anchored) and it
    would have printed ``140/141 = 1.000000`` the moment a link was lost — a false
    arithmetic identity inside the record a reader quotes for #9736's decision. It
    also attributed those numbers to the SUPERSEDED reading, whose own figure was
    ``132/132``.

    Non-vacuity: on the pre-fix probe `e3_row_note` did not exist (the note was built
    inline in `main()`), so this reds as an AttributeError; the SPLICE itself — the
    ``141/141 = 1.000000`` it printed into the shipped record — is what
    `test_the_shipped_log_states_the_e3_note_the_same_way_in_both_copies` reds on
    against the artifact, which is the check that would have caught it.
    """
    graph, corpus_sha = "capstone_measure_test_1", "sha256:" + "d" * 64
    results = _e3_result()

    intact = measure.e3_row_note(composition=_shipped_composition(),
                                 e3_result=results, graph=graph,
                                 corpus_sha=corpus_sha)
    assert "141/141" not in intact, intact
    lost = measure.e3_row_note(
        composition=_shipped_composition(extraction_claims_anchored=140),
        e3_result=results, graph=graph, corpus_sha=corpus_sha)
    assert "140/141" not in lost, lost
    for note in (intact, lost):
        assert not re.search(r"\d+/\d+ = 1\.000000", note), note
    # the circularity is stated as a CONSTRUCTION (derivable from the definition),
    # which is what lets it be said without any measured number at all
    for fragment in ("the earlier reading divided by the extraction claims ALONE",
                     "the class that link defines",
                     "nothing but 1.000000 BY CONSTRUCTION",
                     "'other' Points sitting outside it"):
        assert fragment in intact, (fragment, intact)

    # every composition figure the note DOES print is this run's, derived
    custom = measure.e3_row_note(
        composition=_shipped_composition(extraction_claims=7,
                                         extraction_claims_anchored=3,
                                         other_points=4),
        e3_result=results, graph=graph, corpus_sha=corpus_sha)
    assert "7 extraction claims (3 anchored)" in custom, custom
    assert "4 other (0 anchored)" in custom, custom
    assert "3/11 = 0.272727" in custom, custom


def test_the_shipped_log_states_the_e3_note_the_same_way_in_both_copies():
    """One note, two copies (the human `row E3 note:` line and the machine `note`).

    #9823 review: the sentence was built from THIS run's counts with a typed quotient,
    so the shipped log asserted that the SUPERSEDED reading produced ``141/141``. An
    earlier revision of this branch had the two copies contradict each other outright
    (the human line said the earlier reading was ``132/132`` while the machine copy
    said ``141/141``), which is the class the byte-identity assertion guards; the
    false-identity assertion is what reds on the pre-fix artifact here — it matches
    ``141/141 = 1.000000`` in both copies.
    """
    log = _newest_log(_scored_logs())
    text = log.read_text()
    record = cvc.extract_block(text)
    machine = [row for row in record["rows"] if row["criterion"] == "E3"]
    assert len(machine) == 1, [row["criterion"] for row in record["rows"]]
    human = re.search(r"^row E3 note: (.*)$", text, re.MULTILINE)
    assert human, "the shipped log carries no `row E3 note:` line"
    assert human.group(1) == machine[0]["note"], (
        "the log's human and machine copies of the E3 note disagree")
    for where, copy in (("human", human.group(1)), ("machine", machine[0]["note"])):
        assert not re.search(r"\d+/\d+ = 1\.000000", copy), (where, copy)


def test_the_not_established_section_quotes_the_s5_operator_counts_the_log_measured():
    """The README's own S5 row and the log's `step S5` line must agree with it.

    #9823 review (P2): the "What this bundle does NOT establish" bullet kept a
    SUPERSEDED run's ``IMPL 61 / INPUT 58 / NAND 6`` while the shipped log's `step S5`
    line — and the README's own S5 table row — measured something else. That specific
    drift was cured by the re-run, and this pin is what keeps it cured: the bullet is
    prose nothing else cross-checks, and the failure it had is the bundle's own rule —
    every number it prints is carried by its artifact.

    Non-vacuity: against the pre-re-run README (bullet ``61 / 58 / 6`` beside a log
    that measured ``70 / 70 / 8``) the equality assertion reds. The separator is
    allowed to be ``=`` or a space, because #9823's fix normalised one call site and a
    future one may spell it the other way; what must not move is the NUMBERS.
    """
    readme = (PROBE.parents[1] / "README.md").read_text()
    log = _newest_log(_scored_logs()).read_text()
    measured = re.search(r"operator-shaped relationships written "
                         r"\[IMPL=(\d+), INPUT=(\d+), NAND=(\d+)\]", log)
    assert measured, "the shipped log carries no `step S5` operator-shaped line"
    assert "step S5 WITNESSED" in log, (
        "the shipped log no longer witnesses S5, so the counts above are not a witness")

    section = readme.split("## What this bundle does NOT establish", 1)[1]
    section = section.split("\n## ", 1)[0]
    quoted = re.search(r"IMPL[= ](\d+)[ ,/]*INPUT[= ](\d+)[ ,/]*NAND[= ](\d+)",
                       section)
    assert quoted, (
        "the 'does NOT establish' section no longer quotes the operator-shaped counts; "
        f"the log measured IMPL={measured.group(1)} INPUT={measured.group(2)} "
        f"NAND={measured.group(3)}")
    assert quoted.groups() == measured.groups(), (
        f"the README's not-established bullet says IMPL={quoted.group(1)} "
        f"INPUT={quoted.group(2)} NAND={quoted.group(3)}; the shipped log measured "
        f"IMPL={measured.group(1)} INPUT={measured.group(2)} NAND={measured.group(3)}")
    # and the S5 table row in the same document must not disagree either
    s5_row = next(ln for ln in readme.splitlines()
                  if ln.startswith("| S5 CONNECT"))
    assert f"IMPL={measured.group(1)} INPUT={measured.group(2)} " \
           f"NAND={measured.group(3)}" in s5_row, s5_row
