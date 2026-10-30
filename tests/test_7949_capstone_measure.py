"""#9080 — the capstone MEASUREMENT probe's pure contract.

``probes/capstone_measure.py`` is the probe that produces the E1–E5 numbers (the
sibling ``capstone_run.py`` produces a refusal table and measures nothing, #9737).
Its privileged half — a real ``capture_session`` over seven sessions on a live
graph — cannot run here, so this module pins the half that CAN: the bar arithmetic
(read out of the frozen record, never typed), the verdict vocabulary (a REFUSED
measurement is not a RED row), the table shape the gate parses, and the two rules
that stop a row from claiming a number its artifact does not carry.

Every assertion is DB-free, network-free and LLM-free: the module imports no
``tortoise`` symbol at import time, and the files it reads are the shipped
pre-registration, the landed E1/E2 log, the log the probe itself wrote, and —
for the cite-resolution legs — the documents that print the bundle's
``file:line`` cites (`README.md`, both probes, the `docs/00_index.md` row) plus
the three sources those cites name (`sdk.py`, `projection/edges.py`,
`extractor_v2.py`), read as TEXT only.
"""
from __future__ import annotations

import importlib.util as _ilu
import json
import os
import re
import sys
import tempfile
import types
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from tools import capstone_verdict_check as cvc  # noqa: E402

PROBE = _REPO / "docs/evidence/7949-capstone/probes/capstone_measure.py"
PREREG = _REPO / "docs/evidence/7949-capstone/preregistration.json"
E1E2 = _REPO / "docs/evidence/7949-capstone/e1-e2.log"


def _sdk_line_naming(code: str) -> int:
    """The ONE line of `sdk.py` that carries `code` — READ from the tree.

    A line cite in the prose is the one number in this bundle that no reader can
    keep true by hand: `main` and every branch that touches `sdk.py` move it, and
    a docstring insert above it is enough (this branch's own #9202 note moved the
    meta fold by 4). The causes the probe emits must name the fold's REAL line,
    so the expected line is derived here instead of typed — the assertion keeps
    its teeth (a cause naming any other line still reds) without carrying a
    number that only a human re-derivation can repair. This is Sphinx's own
    answer to the same problem taken one step further: a `py:obj` cross-reference
    resolves to a SYMBOL rather than a line, precisely because a line number
    cannot survive an edit above it.
    """
    hits = [n for n, line in enumerate(
        (_REPO / "tortoise/sdk.py").read_text().splitlines(), 1)
        if code in line]
    assert len(hits) == 1, (
        f"`{code}` is on {len(hits)} lines of sdk.py {hits} — the pin names one")
    return hits[0]


#: The meta fold's cite, in the exact shape the probe's causes print it.
_META_FOLD_CITE = f"tortoise/sdk.py:{_sdk_line_naming('\"stats\": out.get(\"stats\")')}"


_EXTRACTOR = "tortoise/extractor_v2.py"


def _extractor_lineno(code: str) -> int:
    """The ONE line of `extractor_v2.py` whose code starts with `code` — READ from the tree.

    `_sdk_line_naming`'s reason applies verbatim to the extractor's line cites, and
    the red it prevents is the one this file carried: `69f156db0` re-derived every
    `extractor_v2.py` cite the bundle prints (`ef231735b` had inserted 4 lines above
    the whole S0→S6 block, moving all of them by +4) but left the pins below typed
    at the old numbers — so the two tests asserting them failed at the very head
    whose claim was "the shard is green". A typed number can only be repaired by
    another hand re-derivation; DERIVED here, the assertion keeps its teeth (a cause
    or an `_IDLE_CITES` entry naming ANY OTHER line still reds) without carrying a
    number that re-stales on the next insert above it.
    """
    hits = [n for n, line in enumerate(
        (_REPO / _EXTRACTOR).read_text().splitlines(), 1)
        if line.strip().startswith(code)]
    assert len(hits) == 1, (
        f"`{code}` starts {len(hits)} lines of extractor_v2.py {hits} — the pin names one")
    return hits[0]


def _extractor_block(start: int) -> tuple[int, int]:
    """The first..last line of the `{`-block opened on line `start`, brace-counted.

    A RANGE cite is the shape the bundle prints for an emitted block
    (``result["vet"]``, the early-return dict), and its END moves with every key
    added to the block. Counting braces derives both ends from the tree instead of
    typing the span.
    """
    lines = (_REPO / _EXTRACTOR).read_text().splitlines()
    depth = 0
    for n in range(start, len(lines) + 1):
        depth += lines[n - 1].count("{") - lines[n - 1].count("}")
        if depth == 0:
            return start, n
    raise AssertionError(f"unbalanced `{{` from extractor_v2.py:{start}")


def _extractor_cite(code: str) -> str:
    """``:LINE`` for the one line carrying `code` (bare, the shape causes embed)."""
    return f":{_extractor_lineno(code)}"


def _extractor_range(code: str) -> str:
    """``:START-END`` for the brace block opened on `code`'s line."""
    start, end = _extractor_block(_extractor_lineno(code))
    return f":{start}-{end}"


def _extractor_items_gate(gate_code: str) -> str:
    """``:LINE`` of the first `if items:` after the classify gate `gate_code`.

    `if items:` is not uniquely searchable (the S2 pass and the union pass each
    carry one), so the pin derives the SECOND gate from its enclosing gate rather
    than from a bare string that would match either.
    """
    lines = (_REPO / _EXTRACTOR).read_text().splitlines()
    for n in range(_extractor_lineno(gate_code) + 1, len(lines) + 1):
        if lines[n - 1].strip() == "if items:":
            return f":{n}"
    raise AssertionError(f"no `if items:` after extractor_v2.py:{gate_code!r}")


#: Every ``extractor_v2.py`` cite the flag-on/zero-activity causes print, DERIVED
#: from the tree — one table per SURFACE, because the whole point of the rule below
#: is that the two passes are different code. Every entry except `result` is a line
#: `_IDLE_CITES` must name (so the bundle cannot drift from the tree without a red);
#: `result` is the absent-surface cause's ``:START-END`` for the block it names.
_EXTRACTOR_SITES: dict[str, dict[str, str]] = {
    "vet": {
        "flag": _extractor_cite("vet_enabled = (vet_arbiter is not None"),
        "early": _extractor_cite('"vet": {"enabled": vet_enabled'),
        "gate_s2": _extractor_cite("if vet_enabled and embed_list:"),
        "gate_union": _extractor_cite("if vet_enabled and complete_list:"),
        "result": _extractor_range('result["vet"] = {'),
    },
    "classify_later": {
        "flag": _extractor_cite("classify_later = kind_classifier is not None"),
        "init_fail": _extractor_cite("classify_later = False"),
        "early": _extractor_range('"classify_later": {"enabled": classify_later,'),
        "gate_s2": _extractor_cite("if classify_later and embed_list:"),
        "items_s2": _extractor_items_gate("if classify_later and embed_list:"),
        "gate_union": _extractor_cite("if classify_later and complete_list:"),
        "items_union": _extractor_items_gate("if classify_later and complete_list:"),
        "result": _extractor_range('result["classify_later"] = {'),
    },
}


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
    is `{"enabled": False, "s2": {}, "union": {}}` (the two cites are the block
    ranges `_EXTRACTOR_SITES[k]["early"/"result"]` read out of the tree, see
    `_extractor_block`). An acceptance test of "a non-empty dict" therefore takes an OFF
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
    assert _META_FOLD_CITE not in unobserved["S2.2"], unobserved["S2.2"]


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

    `extractor_v2.py` computes `vet_enabled` (the FLAG) BEFORE the
    `if not edus:` early return, whose surface is literally
    `"vet": {"enabled": vet_enabled, "s2": {}, "union": {}}`; the VET passes
    themselves are gated on the LIST (`if vet_enabled and embed_list:`,
    `if vet_enabled and complete_list:`). So the flag-ON + empty-list session emits
    `enabled: True` with empty counters and no pass ran — and a FAILED vet pass
    leaves the same shape (`_run_vet_pass` returns `{}` on its fail-open path).

    The CLASSIFY pass is DIFFERENT CODE, so the cause is PER SURFACE:
    `classify_later` is computed from the classifier / flag pair, its gates are
    `if classify_later and embed_list:` around `if items:` and
    `if classify_later and complete_list:` around `if items:`, and its fail-open
    `except` sits below each. Every number the two causes print is DERIVED from
    `extractor_v2.py` (`_EXTRACTOR_SITES`), never typed — they move with every
    insert above them (`ef231735b` shifted ALL of them by +4 while the pins stayed
    typed at the old numbers, reddening the very shard the re-derivation commit
    claimed to green). A cause templated for the
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
        assert _META_FOLD_CITE not in cause, cause
        assert "did NOT run" not in cause, cause

    # ── S2.2 cites the VET pass's own code … ──────────────────────────────
    for name, site in _EXTRACTOR_SITES["vet"].items():
        if name == "result":       # emitted by the absent-surface cause, not this one
            continue
        assert site in vet_cause, (site, vet_cause)
    # … and NOT one line of the classify pass's, nor its flag/gate TOKENS
    for site in _EXTRACTOR_SITES["classify_later"].values():
        # a range cite reds on its FIRST line too: the reader sent to `:8612`
        # has landed on the classify early return even if `-8615` is not printed
        for form in {site, site.split("-", 1)[0]}:
            assert form not in vet_cause, (form, vet_cause)
    for classify_token in ("classify_later", "if classify_later"):
        assert classify_token not in vet_cause, (classify_token, vet_cause)

    # ── S2.3 cites the CLASSIFY pass's own code … ────────────────────────
    for name, site in _EXTRACTOR_SITES["classify_later"].items():
        if name == "result":
            continue
        assert site in classify_cause, (site, classify_cause)
    # … and NOT the VET pass's flag, pass function or any of its lines
    for site in _EXTRACTOR_SITES["vet"].values():
        for form in {site, site.split("-", 1)[0]}:
            assert form not in classify_cause, (form, classify_cause)
    for vet_token in ("vet_enabled", "_run_vet_pass", "if vet_enabled"):
        assert vet_token not in classify_cause, (vet_token, classify_cause)

    # The constant the causes are built FROM must name those same lines: this is
    # the seam the pre-fix red was on. `69f156db0` re-derived every cite the
    # bundle prints and left these pins typed, so a `_IDLE_CITES` entry can now
    # only move together with the tree — a re-derivation to a wrong line reds
    # here instead of shipping.
    for key, sites in _EXTRACTOR_SITES.items():
        for name, site in sites.items():
            if name == "result":       # emitted by the absent-surface cause, not the table
                continue
            assert site in measure._IDLE_CITES[key], (
                key, name, site, measure._IDLE_CITES[key])

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
    `result["vet"]` and `result["classify_later"]`. Both cites are DERIVED from
    the tree (`_EXTRACTOR_SITES[k]["result"]`), never typed — a range's END moves
    with every key added to the emitted block, exactly as the start moves with
    every insert above it. The
    drop is the meta fold at `tortoise/sdk.py:14191` — **re-derived from the tree at
    this head, never from the other side of a merge.** Both this branch and `main`
    move `sdk.py` (`#9964` inserts 326 lines above the fold; `main`'s own commits
    insert above it too; #9202's own branch moved it by 4; #9750's 40-line helper at
    `sdk.py:5168` moved it by 40), so any typed number is
    stale on the next merge and a merge that "takes the newer side" silently
    restores it. `_META_FOLD_CITE` therefore READS the line out of `sdk.py` on
    every run (`_sdk_line_naming`) instead of typing it — the cause must still name
    the fold's real line, it just cannot re-stale. The cite was
    `:12196` until an earlier round, and `:12738` until #9952 (`43a6eee6a`, the
    `:Source` MERGE-key resolver
    adopting S0a/S0b) moved the fold 6 lines down, so
    each round the bundle sent the #9116 fixer somewhere else. Getting it wrong sends
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
    assert _META_FOLD_CITE in cause, cause
    assert (f"{_EXTRACTOR}{_EXTRACTOR_SITES['vet']['result']}" in cause), cause
    assert "tortoise/sdk.py:12197" not in cause, cause
    assert "20 keys" not in cause, cause
    assert _META_FOLD_CITE in unobserved["S2.3"], unobserved["S2.3"]
    # EVERY sdk.py line the cause NAMES must carry the fold's code — checked
    # against the tree, independently of how the probe arrived at the number.
    # This is the assertion the three earlier rounds lacked: comparing the cause
    # to `_META_FOLD_CITE` alone passes whenever BOTH sides are typed the same
    # way, so a probe that TYPES the number reds only on the next branch that
    # inserts a line above the fold (#9964's 326, #9202's 4, and this branch's
    # own 40-line helper at `sdk.py:5168` — each one moved the fold, and each
    # one needed a hand re-derivation). Resolving the emitted cite against the
    # tree cannot be satisfied by agreement; it needs the code to BE there.
    sdk_lines = (_REPO / "tortoise/sdk.py").read_text().splitlines()
    cited_fold_lines = {int(n) for n in re.findall(
        r"tortoise/sdk\.py:(\d+)", cause)}
    assert cited_fold_lines, cause
    for cited_line in sorted(cited_fold_lines):
        assert '"stats": out.get("stats")' in sdk_lines[cited_line - 1], (
            f"the cause names sdk.py:{cited_line}, which reads "
            f"{sdk_lines[cited_line - 1].strip()!r} — not the fold's code"
        )


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
                     "BY CONSTRUCTION", "tortoise/sdk.py:2376",
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

# ── the bundle's OWN `file:line` cites must RESOLVE (not merely be spelled) ──
#
# The literal-string assertions above pin the SPELLING of a cite, so they pass on
# a number that has moved under it — twice now (#9835's sdk.py insertion moved the
# entry point, and the same commit moved the meta fold and the capture path).
#
# ⚠️ The first version of this leg hardcoded the numbers into a third copy and
# compared that copy against the tree, so it never read the bundle: reverting the
# README's `12487` to the stale `12401` left it green (PR #9850 review, P1). The
# numbers below are therefore read OUT of the documents — the same shape as
# `test_7949_capstone_verdict_check`'s entry-point pins, which are the legs that
# actually caught this drift on `main`. What is fixed here is the RULE: for each
# cite, the phrase the document itself uses to name the line (`"stats": out.get`,
# `the meta fold`, `link_source_to_event`, …) decides what the cited line must
# carry. A cite whose line names nothing this table knows about FAILS rather than
# passing unchecked, so a re-derived number cannot slip in unverified.
#
# ⚠️ Coverage is every cite that NAMES ITS FILE (`sdk.py`, `edges.py`,
# `extractor_v2.py` — the three sources the bundle prints numbers for), in all
# SIX documents a reader is sent into: the README, both probes, the E-row audit
# (`E1-E5-PROOFS.md`, the sixth and newest — `#10108`), the
# `docs/00_index.md` row that is the entry point to this bundle, and the
# extractor's shared design doc `docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md`
# (two row-scoped documents, not whole: the index routes 142 table rows and the
# architecture doc carries ~15 cites belonging to other epics, so another epic's
# `sdk.py` cite must not be told to add a phrase for a line it is not about). A bare `:NNNN`
# continuation (the docs write `` `extractor_v2.py:8596` and `:8616` ``) is NOT a
# cite this leg can resolve, because the file is named only once for several
# numbers; the README's bare `` `symbol` `:NNN` `` step table is owned by
# `test_7949_capstone_verdict_check.py`. `extractor_v2.py` was the first gap: its
# 15 cites (`8288-8292`, `8278-8284`, `7928-7932`, `7912`, `7881`, `7781-7801`,
# `7804`) matched no pattern at all, so they were neither resolved nor reported
# `unmatched` — the leg stayed green over numbers it never read. The index row
# was the second: nothing but a hand re-derivation stood between it and the same
# silent staleness.

_BUNDLE_DOCS = (
    ("README.md", _REPO / "docs/evidence/7949-capstone/README.md"),
    # #10108 review (P2): the E-row audit's own `file:line` cites (the gate's
    # step list, the two extractor surfaces, the receipt fold) were read by no
    # sweep at all — a document whose thesis is "a claim nothing re-checks can
    # rot silently" cannot leave its own claims unwatched.
    ("E1-E5-PROOFS.md",
     _REPO / "docs/evidence/7949-capstone/E1-E5-PROOFS.md"),
    ("probes/capstone_measure.py", PROBE),
    ("probes/capstone_run.py",
     _REPO / "docs/evidence/7949-capstone/probes/capstone_run.py"),
    ("docs/00_index.md", _REPO / "docs/00_index.md"),
    ("docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md",
     _REPO / "docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md"),
)

_BUNDLE_SOURCES = {
    "sdk": _REPO / "tortoise/sdk.py",
    "edges": _REPO / "tortoise/projection/edges.py",
    "extractor_v2": _REPO / "tortoise/extractor_v2.py",
}

# `docs/00_index.md` is a shared router of ~140 table rows, so only the ROW that
# names this bundle is swept: another epic's row citing `sdk.py` must not red the
# #7949 pin (it would be told to add a phrase for a line it is not about). The
# paragraph is the wrong unit — the whole §5 table is ONE paragraph, so a
# paragraph-scoped filter matches every row in it. The filter is applied to the
# CITE'S OWN LINE (see `_bundle_in_scope`), and
# `test_the_index_sweep_keeps_to_the_row_that_names_this_bundle` holds it there.
_BUNDLE_ROW_FILTER = {
    "docs/00_index.md": re.compile(r"#7949"),
    # `EXTRACTOR-V4-ARCHITECTURE.md` is the shared design doc for the whole
    # extractor, and it carries 15 cites into `sdk.py` / `edges.py` /
    # `extractor_v2.py` that belong to other epics (so most name no phrase in
    # `_BUNDLE_RULES` and would land in `unmatched`). Only the S2.1 FINDING row
    # this bundle owns is swept — the row that carries the `run_s2(...)` call.
    # Without this entry the doc was swept by NOTHING: its D3 cite was
    # re-derived from `:7776` to `:7804`, a comment about the source-turn map,
    # and no gate read it (a false PASS — the sweep reported OK over a number
    # that was never the call).
    "docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md": re.compile(r"run_s2\("),
}


def _bundle_in_scope(doc_name: str, line: str) -> bool:
    """Is a cite found on `line` this bundle's to verify?

    Only for a scoped document, and only by the ROW the cite sits on: the unit is
    the line, never the paragraph (which is the whole markdown table for the
    index). An unscoped document is swept whole.
    """
    scope = _BUNDLE_ROW_FILTER.get(doc_name)
    return scope is None or bool(scope.search(line))

# Shapes this matches, ILLUSTRATIVE ONLY — the live cites are in the bundle docs, so
# a number quoted here is not pinned by anything and would drift with the next
# insertion above it (the exact failure mode the sweep exists to catch):
# `tortoise/sdk.py:<line>`, ``tortoise/projection/edges.py:<start>-<end>``,
# `edges.py:<line>`, ``tortoise/extractor_v2.py:<start>-<end>``.
_BUNDLE_CITE = re.compile(
    r"(?<![\w.])(?:tortoise/)?(?:projection/)?"
    r"(sdk|edges|extractor_v2)\.py:(\d+)(?:-(\d+))?")

# A paragraph that is ABOUT numbers which no longer resolve is not a live cite:
# the README discloses the frozen log's stale set on purpose, and the supersession
# history names the old stamps' cites. Each such paragraph says so in its own
# words, and what it is allowed to skip is pinned below it — frozen numbers by
# definition, so a LIVE cite landing in one of these paragraphs reds instead of
# vanishing into the exclusion.
_BUNDLE_HISTORICAL = {
    r"no longer\s+resolve": (
        "sdk.py:12401", "sdk.py:12682", "sdk.py:2367", "edges.py:467",
        "edges.py:494-503", "edges.py:2168-2192",
        "tortoise/extractor_v2.py:8278-8285", "tortoise/extractor_v2.py:8288-8292"),
    r"SUPERSEDED and its file is gone": ("sdk.py:12196",),
}

# (label, source, phrases the DOCUMENT uses to name the line, what the line must
#  carry, how to check it, how the cited END must read). No numbers: those are read
#  out of the document.
#   line        — every anchor appears IN the cited line
#   def         — the cited line, stripped, STARTS WITH the anchor
#   span        — every anchor appears somewhere in the cited line or range
#   method-span — the cited line is the `def`, and the range stays inside it
# The end spec bounds a RANGE (`None` when the cite is a single line):
#   ("exact", "}")     — the cited end line, stripped, is exactly this; a range
#                        that grows one line past the block it names reds
#   ("contains", tok)   — the cited end line carries this token
_BUNDLE_RULES = (
    ("the meta fold S2.2/S2.3 are dropped at", "sdk",
     ('"stats": out.get', "meta fold", "same fold", "dropped at", "must target"),
     ('"stats": out.get("stats")',), "line", None),
    ("the call that writes the references edge", "sdk",
     ("proj.link_source_to_event(",),
     ("proj.link_source_to_event(",), "line", None),
    ("the capture path's turn writer", "sdk",
     ("_TURN_WRITE_CYPHER",), ("_TURN_WRITE_CYPHER = (",), "line", None),
    ("the entry point", "sdk",
     ("_extract_session_v2",), ("def _extract_session_v2(",), "def", None),
    ("the references target labels", "edges",
     ("_DERIVATION_REFERENCES_LABELS",),
     ("_DERIVATION_REFERENCES_LABELS = frozenset",), "line", None),
    ("the #5199 anchor expression and the set the writers concatenate", "edges",
     ("_anchor_on_create",),
     ("def _anchor_on_create(", "_DERIVATION_ANCHOR_SET = _anchor_on_create("),
     "span", ("contains", "_DERIVATION_ANCHOR_SET = _anchor_on_create(")),
    ("the references writer", "edges",
     ("link_source_to_event",), ("def link_source_to_event(",), "method-span",
     ("exact", ")")),
    # `extractor_v2.py` — the surfaces whose ABSENCE is #9116. Two cites per
    # surface: the block it EMITS (the `result[...] = {` assignment, a range)
    # and the FLAG computed earlier (a single line). The surface rule comes
    # first so a two-cite line names the surface it prints, not a phrase the
    # neighbouring cite happens to share.
    ("the classify-later surface the extractor emits", "extractor_v2",
     ('result["classify_later"]', "result['classify_later']"),
     ('result["classify_later"] = {',), "span", ("exact", "}")),
    ("the vet surface the extractor emits", "extractor_v2",
     ('result["vet"]', "result['vet']"),
     ('result["vet"] = {',), "span", ("exact", "}")),
    ("the classify-later flag, computed before the passed step is reached",
     "extractor_v2", ("classify_later",),
     ("classify_later = kind_classifier is not None",), "line", None),
    ("the vet flag, computed before the pass is reached", "extractor_v2",
     ("vet_enabled",), ("vet_enabled = (",), "line", None),
    ("the vet pass's fail-open exception path", "extractor_v2",
     ("_run_vet_pass",),
     ("except Exception as e:", "return embed_list, {}, ["), "span",
     ("contains", '], {}')),
    ("the early-return block that emits both OFF surfaces", "extractor_v2",
     ("early-return path",),
     ('"classify_later": {"enabled"', '"vet": {"enabled"'), "span",
     ("contains", '"vet": {"enabled"')),
    ("the extractor entry point", "extractor_v2",
     ("extract_session_v2",), ("def extract_session_v2(",), "def", None),
    # The S2.1 FINDING in `docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md`: the
    # call that hands extraction the raw alongside the story. A RANGE, because
    # the claim is the ARGUMENTS — `edus=edus` sits one line below the call and
    # the block closes on `core_only=classify_later)`, so a cite that lands on
    # a neighbouring comment carries neither anchor and reds.
    ("the S2 call that hands extraction the raw alongside the story",
     "extractor_v2", ("run_s2(model, story, master",),
     ("run_s2(model, story, master,", "edus=edus,"), "span",
     ("contains", "core_only=classify_later)")),
)


def _paragraph_spans(text: str) -> list[tuple[int, int]]:
    """Blank-line-delimited paragraph offsets — the unit a cite's claim lives in."""
    spans, start = [], 0
    for sep in re.finditer(r"\n[ \t]*\n", text):
        spans.append((start, sep.start()))
        start = sep.end()
    spans.append((start, len(text)))
    return spans


def _bundle_historical(paragraph: str) -> str | None:
    """The historical marker this paragraph carries, or None (its own words)."""
    for pattern in _BUNDLE_HISTORICAL:
        if re.search(pattern, paragraph, re.IGNORECASE):
            return pattern
    return None


def _bundle_rule(source: str, paragraph: str, base: int, cite_pos: int):
    """The rule whose naming phrase sits NEAREST this cite — distance, not order.

    A document names a line with the phrase right beside it (`` `vet_enabled` is
    computed at `extractor_v2.py:8596` ``), so the closest phrase is the one that
    names THIS cite. Table order cannot decide it: `docs/00_index.md`'s #7949 row
    names the CLASSIFY pass and then the VET flag on one line, and taking the first
    table match bound the VET cite to the classify phrase ~40 characters away (a
    false RED). Distance is measured to the phrase's END, because a naming phrase
    precedes the number it names, and the longer phrase wins an overlap —
    `result["classify_later"]` and its `classify_later` substring end one
    character apart, and the surface must win over the flag.
    """
    best = None
    for order, rule in enumerate(_BUNDLE_RULES):
        if rule[1] != source:
            continue
        for phrase in rule[2]:
            for found in re.finditer(re.escape(phrase), paragraph):
                key = (abs((base + found.end()) - cite_pos), -len(phrase), order)
                if best is None or key < best[0]:
                    best = (key, rule)
    return best[1] if best else None


def _bundle_missing(kind: str, anchors, source_lines, start: int, end,
                    end_spec=None):
    """[] when the cite resolves, else what the cited lines do not carry."""
    stop = int(end) if end else start
    if kind == "line":
        got = source_lines[start - 1]
        return [a for a in anchors if a not in got]
    if kind == "def":
        got = source_lines[start - 1]
        return [] if got.lstrip().startswith(anchors[0]) else [anchors[0]]
    if kind == "span":
        span = source_lines[start - 1:stop]
        missing = [a for a in anchors if not any(a in ln for ln in span)]
        # A RANGE's end is a claim too: `8278-8284` that closes one line into the
        # next block still carries its start anchor and would otherwise pass, so
        # the end line has to read what the rule says it reads.
        if end and end_spec:
            how, token = end_spec
            got = source_lines[stop - 1]
            if how == "exact" and got.strip() != token:
                missing.append(f"an end line reading {token!r} (it reads {got.strip()!r})")
            elif how == "contains" and token not in got:
                missing.append(
                    f"an end line carrying {token!r} (it reads {got.strip()!r})")
        return missing
    # method-span: the def is HERE, the range must not run into the next `def`,
    # and the cited END is a claim too (it must read what the rule says it reads).
    assert kind == "method-span", kind
    if not source_lines[start - 1].lstrip().startswith(anchors[0]):
        return [anchors[0]]
    next_def = next(
        (i + 1 for i in range(start, len(source_lines))
         if re.match(r" {0,4}def ", source_lines[i])), len(source_lines))
    if stop >= next_def:
        return [f"a range ending at {stop}, which runs past the next def at {next_def}"]
    if end and end_spec:
        how, token = end_spec
        got = source_lines[stop - 1]
        if how == "exact" and got.strip() != token:
            return [f"an end line reading {token!r} (it reads {got.strip()!r})"]
        if how == "contains" and token not in got:
            return [f"an end line carrying {token!r} (it reads {got.strip()!r})"]
    return []


def _bundle_sweep(docs, sources) -> dict:
    """Read every LIVE cite out of `docs` and resolve it against `sources`.

    Returns the collected state (`fired`, `checked`, `unmatched`, `failures`,
    `skipped`) and asserts nothing, so a test can drive the SAME sweep over
    synthetic documents — `test_the_index_sweep_keeps_to_the_row_that_names_this_
    bundle` does, because the row scoping cannot be falsified against the real
    index (only the #7949 row carries a swept cite today).
    """
    source_lines = {name: path.read_text(encoding="utf-8").splitlines()
                    for name, path in sources.items()}
    fired, checked, unmatched, failures = set(), 0, [], []
    skipped: dict[str, list[str]] = {}
    for doc_name, doc_path in docs:
        text = doc_path.read_text(encoding="utf-8")
        doc_lines = text.splitlines()
        paragraphs = _paragraph_spans(text)
        for match in _BUNDLE_CITE.finditer(text):
            paragraph = next((s, e) for s, e in paragraphs
                             if s <= match.start() < e)
            line_no = text.count("\n", 0, match.start()) + 1
            # a scoped document is swept ROW by row: only the row (line) that
            # names this bundle is ours, not the whole markdown-table paragraph.
            if not _bundle_in_scope(doc_name, doc_lines[line_no - 1]):
                continue
            marker = _bundle_historical(text[paragraph[0]:paragraph[1]])
            if marker:
                skipped.setdefault(marker, []).append(match.group(0))
                continue
            source, start, end = match.group(1), int(match.group(2)), match.group(3)
            rule = _bundle_rule(source, text[paragraph[0]:paragraph[1]],
                                paragraph[0], match.start())
            cite = f"{doc_name}:{line_no} `{match.group(0)}`"
            if rule is None:
                unmatched.append(cite)
                continue
            label, _, _, anchors, kind, end_spec = rule
            fired.add(label)
            checked += 1
            missing = _bundle_missing(kind, anchors, source_lines[source],
                                      start, end, end_spec)
            if missing:
                shown = source_lines[source][start - 1].strip()
                failures.append(
                    f"{cite} ({label}): {source}.py:{start} reads {shown!r}, which "
                    f"carries none of {missing!r}")
    return {"fired": fired, "checked": checked, "unmatched": unmatched,
            "failures": failures, "skipped": skipped}


def test_the_bundles_cites_resolve_where_the_bundle_says_they_do():
    """Every live `file.py:line` cite that names its file → the code it names there.

    Covers all three sources the bundle prints numbers for (`sdk.py`, `edges.py`,
    `extractor_v2.py`) across `README.md`, both probes, the E-row audit
    (`E1-E5-PROOFS.md`), the `docs/00_index.md` rows that are the bundle's entry
    point, and the `docs/architecture/
    EXTRACTOR-V4-ARCHITECTURE.md` S2.1 FINDING row this bundle owns — 50 cites at
    this head (`#10108` added the sixth document; the count is MEASURED, and it
    moves with the documents, so re-read `checked` rather than trusting this line).

    The architecture doc was the LAST unread one, and it hid a false PASS: its D3
    cite (`run_s2(...)` passing `edus=edus`) was re-derived onto `:7804` — a
    comment about the source-turn map — while no leg read that file at all, so the
    sweep reported OK over a number that was never the call. It is row-scoped like
    `docs/00_index.md` (the array of 15 cites it carries into these three sources
    belongs to other epics) and `the S2 call that hands extraction the raw
    alongside the story` pins the call as a RANGE, because the claim is the
    ARGUMENTS: `edus=edus,` is one line below the call and the block closes on
    `core_only=classify_later)`.

    Non-vacuity: re-staling a LIVE cite reds this test (measured: putting
    `00_index.md`'s fold cite back to `12401` reds it). The RED COUNT is a
    property of the base, not of the rule set, so it is stamped with the commit it
    was measured at rather than asserted about `main`: 17 of the 44 cites red
    across three rules at `0c7062af1` (`sdk.py:12679` for the meta fold, `12960`
    for the call that writes the references edge, `11851` for the entry point — a
    supersession comment, an ORDER comment and a counters line instead of the
    symbols), and 26 of the 44 red at `f86ae965b` (`52e2be157^`), the base #9850
    repaired. An earlier round quoted `26` and `five rules` as a standing fact
    about `main`; both went stale the moment the pins were repaired, which is why
    the numbers here name their base.
    The `extractor_v2.py` cites are correct at this head, so those rules cannot red
    on `main`; they are the tripwire for the NEXT insertion above the live cites
    (the entry point, the classify flag, the vet flag — named by SYMBOL, never by
    a number: a number quoted here would be the very drift this paragraph is
    about, and it was, twice). They bite: with the README's vet-flag cite moved
    to `7913`, `the vet flag` reds with `extractor_v2.py:7913 reads 'continue',
    which carries none of ['vet_enabled = (']` (measured at a head on which 7913
    was that comment; an earlier round quoted an older read of it, which the
    extractor's own growth had since moved).
    The index row's cites are pinned by the same sweep: re-staling `00_index.md`'s
    fold cite from its live line to `12401` reds it, and before this leg nothing
    read that row.
    """
    result = _bundle_sweep(_BUNDLE_DOCS, _BUNDLE_SOURCES)
    fired, checked = result["fired"], result["checked"]
    unmatched, failures, skipped = (result["unmatched"], result["failures"],
                                    result["skipped"])
    assert {marker: tuple(cites) for marker, cites in skipped.items()} \
        == {marker: tuple(cites) for marker, cites in _BUNDLE_HISTORICAL.items()}, (
        "the historical exclusion dropped cites this pin does not know about, and a "
        "cite inside an excluded paragraph is verified by NOTHING — move the live "
        "cite out of the historical paragraph, and update this pin only together "
        f"with the paragraph it names: skipped={skipped}")
    assert not unmatched, (
        "these cites name a line no rule in _BUNDLE_RULES can verify, so nothing "
        "checked them — add the phrase the document uses for the new line, do not "
        "leave the cite unverified: " + "; ".join(unmatched))
    assert not failures, (
        "the bundle points at lines that do not carry the code it names — re-derive "
        "the number (and the range) from the tree before changing this pin:\n  "
        + "\n  ".join(failures))
    assert fired == {rule[0] for rule in _BUNDLE_RULES}, (
        f"rules that checked no cite at all: {sorted({r[0] for r in _BUNDLE_RULES} - fired)}"
        " — the bundle's cites for those symbols moved or were dropped; re-derive them")
    assert checked >= 20, (
        f"only {checked} live cites were resolved — the sweep is not reading the bundle")


def test_the_index_sweep_keeps_to_the_row_that_names_this_bundle():
    """A cite on ANOTHER epic's index row is not this bundle's to verify.

    `docs/00_index.md` routes ~140 table rows and the whole §5 table is ONE
    blank-line-delimited paragraph, so a paragraph-scoped filter cannot tell the
    `#7949` row from any other row: the leg's first index sweep (PR #9850 review,
    P2) matched the paragraph and swept every row inside it, which reds a lane
    that adds a CORRECT `sdk.py` cite to an unrelated row — and tells it to
    "re-derive the number … before changing this pin" for a line this pin is not
    about. The unit is the cite's own LINE.

    Non-vacuity: both rows below sit in ONE paragraph, so the pre-fix unit (the
    paragraph) put both in scope and the unrelated row's cite reached
    `_bundle_rule`, found no naming phrase and landed in `unmatched`. The second
    half drives the shipped `_bundle_sweep`, not the predicate alone, so moving
    the decision back to the paragraph reds this leg.
    """
    table = (
        "| **`#9664` capstone — the onboarding journey** — see `sdk.py:9` | x |\n"
        "| **`#7949` / `#9080` capstone — the closing rule** — dropped at "
        "`sdk.py:3` | y |\n"
    )
    rows = table.splitlines()
    assert len(_paragraph_spans(table)) == 1, (
        "this pin exists because the two rows share one paragraph; if they no "
        "longer do, the paragraph unit would discriminate and this leg is moot")
    assert not _bundle_in_scope("docs/00_index.md", rows[0]), (
        "an unrelated epic's index row is in scope — the sweep would verify a cite "
        "this bundle does not own")
    assert _bundle_in_scope("docs/00_index.md", rows[1])
    # an UNSCOPED document is still swept whole: the filter belongs to the index
    assert _bundle_in_scope("README.md", rows[0])

    sdk = ["x"] * 9
    sdk[2] = '            "stats": out.get("stats") or {},'   # line 3: the meta fold
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        doc = root / "00_index.md"
        doc.write_text(table, encoding="utf-8")
        source = root / "sdk.py"
        source.write_text("\n".join(sdk) + "\n", encoding="utf-8")

        swept = _bundle_sweep((("docs/00_index.md", doc),), {"sdk": source})
        assert swept["unmatched"] == [], (
            "the unrelated row's cite was reported `unmatched` — the sweep is "
            f"reading rows it does not own: {swept['unmatched']}")
        assert swept["checked"] == 1, (
            f"expected only the #7949 row's cite to be checked, got {swept['checked']}")

        # and the scope follows the MARKER, not the row's position
        doc.write_text("\n".join(reversed(rows)) + "\n", encoding="utf-8")
        swept = _bundle_sweep((("docs/00_index.md", doc),), {"sdk": source})
        assert swept["checked"] == 1 and swept["unmatched"] == [], (
            f"the scope is positional, not marker-based: {swept}")

    # the shipped index is swept by the same rule: its only swept cites sit on the
    # #7949 row, so the bundle row is in scope for every one of them
    shipped = (_REPO / "docs/00_index.md").read_text(encoding="utf-8")
    shipped_rows = shipped.splitlines()
    owned = [m for m in _BUNDLE_CITE.finditer(shipped)
             if _bundle_in_scope("docs/00_index.md",
                                 shipped_rows[shipped.count("\n", 0, m.start())])]
    assert owned, "no cite in docs/00_index.md is in scope — the index row is unwatched"
    assert all("#7949" in shipped_rows[shipped.count("\n", 0, m.start())]
               for m in owned)
