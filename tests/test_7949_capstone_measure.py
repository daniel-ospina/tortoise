"""#9080 — the capstone MEASUREMENT probe's pure contract.

``probes/capstone_measure.py`` is the probe that produces the E1–E5 numbers (the
sibling ``capstone_run.py`` produces a refusal table and measures nothing, #9737).
Its privileged half — a real ``capture_session`` over seven sessions on a live
graph — cannot run here, so this module pins the half that CAN: the bar arithmetic
(read out of the frozen record, never typed), the verdict vocabulary (a REFUSED
measurement is not a RED row), the table shape the gate parses, and the two rules
that stop a row from claiming a number its artifact does not carry.

Every assertion is DB-free, network-free and LLM-free — and that is MEASURED, not
asserted from memory (#10525 review P3). ⛔ Importing this module DOES pull in
``tortoise.projection.edges`` (27 ``tortoise.*`` modules, measured): ``measure =
_load()`` at the bottom of the header executes the probe, which derives its class
set from the writer's own constant at ITS import time (``DERIVED_LABELS``, #9736).
What it pulls in NOTHING of is a driver, client or model SDK — no ``falkordb``, no
``redis``, no ``psycopg2``, no ``httpx``, no ``openai``, no ``anthropic`` (all
measured absent), so the slice is DB/network/LLM-free on the axis that matters to
how this file is selected and weighted. The earlier wording claimed "the module
imports no ``tortoise`` symbol at import time", which the tree refuted while no
test could see it; ``test_the_import_claim_is_what_importing_actually_does`` now
measures BOTH halves in a subprocess and requires this docstring to agree with the
measurement, so the claim cannot re-stale silently. The files it reads are the shipped
pre-registration, the landed E1/E2 log, the log the probe itself wrote, and —
for the cite-resolution legs — the documents that print the bundle's
``file:line`` cites (`README.md`, both probes, the `docs/00_index.md` row) plus
the three sources those cites name (`sdk.py`, `projection/edges.py`,
`extractor_v2.py`), read as TEXT only.
"""
from __future__ import annotations

import ast
import importlib.util as _ilu
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from tools import capstone_verdict_check as cvc  # noqa: E402

PROBE = _REPO / "docs/evidence/7949-capstone/probes/capstone_measure.py"
PREREG = _REPO / "docs/evidence/7949-capstone/preregistration.json"
SCORES = _REPO / "docs/evidence/9889/e1-e2-scores.json"
SCORES_PROBE = (_REPO / "docs/evidence/7949-capstone/probes/e1_e2_scores.py")
#: The SUPERSEDED artifact. It is still in the tree — it is a record of what a
#: one-arm run measured — but no row may cite it any more (`#9889` item 5).
E1E2 = _REPO / "docs/evidence/7949-capstone/e1-e2.log"


#: `sdk.py` as text — read ONCE, so every derivation below sees the same bytes.
SDK_SOURCE = (_REPO / "tortoise/sdk.py").read_text()


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
    hits = [n for n, line in enumerate(SDK_SOURCE.splitlines(), 1) if code in line]
    assert len(hits) == 1, (
        f"`{code}` is on {len(hits)} lines of sdk.py {hits} — the pin names one")
    return hits[0]


#: The #9116 seam's SYMBOL — what a cause names instead of a line.
#:
#: A line cite was the right shape while the seam did not exist: the cause had to
#: send the next lane to the one line that DROPPED the block, and
#: `_sdk_line_naming` re-derived it so it could not re-stale. Now that the fold
#: CALLS the seam, what a reader can act on is the helper, and a symbol cannot
#: re-stale at all.
_SEAM_SYMBOL = "_capture_step_witness"

#: The fold's CALL SITE, re-derived from the tree (never typed) — the line that
#: decides whether the seam reaches the receipt at all.
_META_FOLD_CITE = ("tortoise/sdk.py:" + str(_sdk_line_naming(
    "_capture_stats_with_step_witness(out, len(extracted))")))


def _enclosing_functions(source: str, lineno: int) -> list[str]:
    """The `def`s whose body carries `lineno` of `source` (1-based).

    Parsed, not indent-counted: a line number tells you WHERE the cite points, and
    only the parse tree tells you WHAT it is inside. ``ast`` is the tree sdk.py is
    already read as text for, so this adds no new source of truth.
    """
    tree = ast.parse(source)
    return [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.lineno <= lineno <= (node.end_lineno or node.lineno)
    ]


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


def _extractor_fail_open_except(append_code: str) -> str:
    """``:LINE`` of the `except` that swallows the classify pass naming `append_code`.

    The document cites the FAIL-OPEN `except` (not the `errors.append` one line below
    it), so the pin derives the clause from the append rather than typing the number:
    `except` alone is not uniquely searchable — every fail-open arm in the extractor
    carries one — and a bare `except Exception as e:` search would pin whichever arm
    happened to come first. Walking back from the message that names the pass ties the
    cite to the pass it is about.
    """
    lines = (_REPO / _EXTRACTOR).read_text().splitlines()
    for n in range(_extractor_lineno(append_code) - 1, 0, -1):
        if lines[n - 1].strip().startswith("except "):
            return f":{n}"
    raise AssertionError(
        f"no `except` above extractor_v2.py:{_extractor_lineno(append_code)} "
        f"for {append_code!r}")


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


def _load_scores():
    spec = _ilu.spec_from_file_location("e1_e2_scores_pure", str(SCORES_PROBE))
    assert spec and spec.loader
    module = _ilu.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


measure = _load()
scores_probe = _load_scores()


def _load_probe(name: str):
    """Load a sibling probe module by file name — the provenance pins need the
    PRODUCER (`e1_paired_arm`) and not only the scoring driver, because the
    README's sentence is about which reader each of them uses."""
    path = _REPO / "docs/evidence/7949-capstone/probes" / f"{name}.py"
    spec = _ilu.spec_from_file_location(f"{name}_pin", str(path))
    assert spec and spec.loader
    module = _ilu.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    assert bars["floors"] == {"e1_objects": 30,
                             "e1_min_before_share": 0.2,
                             "e2b_surviving_units": 30,
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


def test_a_missing_or_unreadable_share_floor_refuses():
    """E1's SECOND floor is a fraction, so it needs its own reader.

    `e1_min_before_share: 0.2` is not an integer, so the count helper refuses it;
    omitting it from the map is how `render_log` came to subscript a key
    `parse_bars` never emitted (#10610 review issue 1). Absent and unreadable
    both refuse, never "no floor stated", and `True` is an `int` in Python —
    not a share.
    """
    for shape in ("absent", "unreadable", "bool"):
        record = _prereg()
        if shape == "absent":
            del record["denominator_floors"]["e1_min_before_share"]
        else:
            record["denominator_floors"]["e1_min_before_share"] = (
                "0.20" if shape == "unreadable" else True)
        try:
            measure.parse_bars(record)
        except ValueError:
            continue
        raise AssertionError(f"{shape} e1_min_before_share did not refuse")


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


def test_e3_cannot_pass_on_a_registered_class_leg_that_is_empty():
    """#9736: `= 1.000` is a claim about the REGISTERED class set, and an empty leg did not pass it.

    Measured FALSE GREEN on this probe before the fix: with `Event` and `Document` carrying zero
    nodes and every node of the classes that DO exist anchored, the row read
    `e3 numerator=30 denominator=30 … outcome=PASS` — verdict GREEN — because `e3_outcome` only
    ever sees two summed integers. `e3_blocks` selects `per_label[label] for label in
    DERIVED_LABELS`, so a registered class the graph holds none of contributes `0/0` and vanishes
    from both halves of the ratio.

    That is not hypothetical: `:Document` is a RETIRED label — the writer remaps `Document` onto
    `:Source` (`tortoise/projection/edges.py:2765-2769`, D10) — and §10.1 of the pre-registration
    records that the scored run wrote no `Document` at all. So the vacuous leg is the PRODUCTION
    shape, and only the owner's §9736 ruling can change which spelling E3 names.

    The rule this pins is the ruling-free one the issue's own decision card declares
    (`NOT-A-DECISION(9736-gate-empty-leg)`): an empty registered leg must FAIL rather than pass by
    arithmetic. It does not pick a spelling, move a threshold, or re-cut the denominator.
    """
    green = measure.e3_outcome(anchored=30, derived=30, bars=BARS)
    assert green["outcome"] == "PASS", green
    blocked = measure.e3_outcome(anchored=30, derived=30, bars=BARS,
                                 unmeasured_legs=("Document", "Event"))
    assert blocked["outcome"] == "FAIL", blocked
    assert measure.verdict_word(blocked) == "RED", blocked
    assert blocked["unmeasured_legs"] == ("Document", "Event"), blocked
    assert "Document" in blocked["bar"] and "empty" in blocked["bar"].lower(), blocked["bar"]
    # NO SOFTENING and NO NEW RED: the shipped reading (148/383) was already RED and stays RED,
    # with the empty leg disclosed rather than silently folded in.
    shipped = measure.e3_outcome(anchored=148, derived=383, bars=BARS,
                                 unmeasured_legs=("Document",))
    assert shipped["outcome"] == "FAIL", shipped
    # the floor still wins where it applies — a too-small population is REFUSED, never a verdict
    below = measure.e3_outcome(anchored=5, derived=20, bars=BARS,
                              unmeasured_legs=("Document",))
    assert below["outcome"] == "REFUSED", below


def test_e3_unmeasured_legs_reads_the_census_and_never_counts_object():
    """The empty-leg detector reads the CENSUS, so it sees what the run measured — not the row.

    `:Object` is the class #5199 option A leaves unanchored BY DESIGN and #10525 dropped from E3, so
    it must never come back as a "missing" leg (that would re-red the row for the recorded
    decision). An ABSENT block counts as empty: a census that never enumerated a registered class
    did not measure it, and "absent" must not read as "fine".
    """
    census = {"Point": {"nodes": 10, "anchored": 8},
              "Document": {"nodes": 0, "anchored": 0},
              "Object": {"nodes": 50, "anchored": 0},
              "Event": {"nodes": 1, "anchored": 1}}
    assert measure.e3_unmeasured_legs(census) == ("Document",), measure.e3_unmeasured_legs(census)
    assert measure.e3_unmeasured_legs({"Point": {"nodes": 10, "anchored": 10}}) == (
        "Document", "Event")
    full = {"Point": {"nodes": 10, "anchored": 10},
            "Document": {"nodes": 1, "anchored": 1},
            "Event": {"nodes": 1, "anchored": 1}}
    assert measure.e3_unmeasured_legs(full) == ()


def test_main_feeds_the_census_legs_into_e3_and_renders_them():
    """The empty-leg rule is only real if PRODUCTION asks the census for the legs.

    `e3_outcome`'s guard is a pure function, so an unwired rule is indistinguishable from no rule:
    `main` would keep summing the totals and a class the run never wrote would keep disappearing.
    This reads `main`'s own AST and requires (a) `e3_outcome` to be called with an
    `unmeasured_legs=` argument built from `e3_unmeasured_legs(census_data[...])`, and (b)
    `render_log` to read that key back for the log's own `e3 unmeasured_legs=` line — because the
    defect this closes was `0/0` being invisible in both places at once.
    """
    tree = ast.parse(PROBE.read_text())
    main = next(node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == "main")
    call = next(node for node in ast.walk(main)
                if isinstance(node, ast.Call)
                and getattr(node.func, "id", None) == "e3_outcome")
    kwargs = {kw.arg: kw.value for kw in call.keywords}
    assert "unmeasured_legs" in kwargs, (
        "main calls e3_outcome without unmeasured_legs — a registered class the run wrote zero of "
        "is dropped from both halves of the ratio again, and the row can report 1.000 over a "
        "strict subset of the registered population (#9736)")
    wired = kwargs["unmeasured_legs"]
    assert isinstance(wired, ast.Call) and getattr(wired.func, "id", None) == (
        "e3_unmeasured_legs"), ast.dump(wired)
    # the argument is the CENSUS, not a typed tuple: a hardcoded class list would be the very
    # narrowing this rule exists to refuse, and would re-stale with the class set.
    assert "census_data" in ast.dump(wired), ast.dump(wired)

    render = next(node for node in tree.body
                  if isinstance(node, ast.FunctionDef) and node.name == "render_log")
    rendered = [n for n in ast.walk(render)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and "e3 unmeasured_legs=" in n.value]
    assert rendered, (
        "render_log no longer prints `e3 unmeasured_legs=` — the log's per-label lines show a 0-node "
        "leg as `nodes=0 anchored=0 unanchored=0`, which reads as a clean class rather than an "
        "unmeasured one")


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

def test_the_e1_e2_record_parses_to_the_numbers_the_table_uses():
    """E1/E2 come off the `#9889` record now — not off the superseded log.

    The numbers asserted here are the ones `docs/evidence/9889/README.md`
    publishes, so a probe that silently starts reading something else reds this
    test rather than the table.
    """
    parsed = measure.measure_e1_e2(_REPO)
    assert parsed["before_share"] == 0.358974358974359
    assert parsed["after_share"] == 0.21311475409836064
    assert parsed["objects_total"] == 78
    assert parsed["after_objects"] == 61
    assert parsed["real_facts"] == 90
    assert parsed["atomic_points"] == 114
    assert parsed["fused"] == 1
    # E2a's frozen denominator is CARRIED — the acceptance item that was open.
    assert parsed["e2a_units"] == 14
    assert parsed["e2a_survived"] == 8
    # The record's corpus IS the pre-registration's frozen pin (G1 enforced it).
    assert parsed["corpus_sha256"] == measure.normalise_corpus(
        json.loads(PREREG.read_text())["corpus"]["fixtures_hash"])
    assert re.fullmatch(r"[0-9a-f]{40}", parsed["sha"] or ""), parsed["sha"]
    # One after arm against two before realizations DISAGREES, so E1 is not
    # decided — the row is REFUSED, never the convenient pairing.
    assert parsed["decidable"] is False
    assert [pairing["passed"] for pairing in parsed["pairings"]] == [False, True]


def test_the_superseded_log_has_no_reader_left_in_the_probe():
    """P3: the `e1-e2.log` PARSE PATH is deleted, and the README says so.

    `README.md` states *"the old log's parse path is deleted from
    `probes/capstone_measure.py`"* (`#9889` item 5). The tree disagreed: after the
    re-point, `parse_e1e2_log`, `corpus_sha_of_log` and `line_value` all survived
    with no production caller — a reader kept alive beside the record, which reads
    to the next lane as "this log is still scored". `parse_e1e2_log`'s only caller
    was its own test, so nothing but a grep could see it (#10610 P3).

    Non-vacuity: re-add any of the three and this reds, which is the state the PR
    shipped.
    """
    for name in ("parse_e1e2_log", "corpus_sha_of_log", "line_value"):
        assert not hasattr(measure, name), (
            f"{name} is back in the probe with no production caller — a second, "
            "quieter reader of an artifact no row may cite")
    assert hasattr(measure, "normalise_corpus"), (
        "normalise_corpus is the LIVE reader (measure_e1_e2 uses it); removing it "
        "would be the opposite mistake")


def test_no_row_cites_the_superseded_e1_e2_log():
    """`#9889` item 5: the hard-coded log name is gone from the row builder."""
    assert measure.E1_E2_SCORES == "docs/evidence/9889/e1-e2-scores.json"
    assert not hasattr(measure, "E1E2_LOG")
    rows = {row["criterion"]: row for row in _rows(out_path="x.log")}
    for criterion in ("E1", "E2"):
        assert rows[criterion]["evidence"] == measure.E1_E2_SCORES, criterion


def test_the_e1_e2_record_refuses_a_denominator_the_record_did_not_freeze(tmp_path):
    """P1: a drifted E2a denominator must not score. 20 units scored 14/20 would
    PASS a bar the pre-registration wrote over 14 — a false green on the one row
    this bundle exists to certify."""
    repo = tmp_path / "repo"
    shutil.copytree(_REPO / "docs/evidence/9889", repo / "docs/evidence/9889")
    shutil.copytree(_REPO / "docs/evidence/7949-capstone",
                    repo / "docs/evidence/7949-capstone")
    for name in ("e2a-value-units.json", "e2a-value-units-falsifier.json"):
        path = repo / "docs/evidence/9889" / name
        record = json.loads(path.read_text())
        for block in (record, record.get("with_anchor") or {}):
            if block.get("value_units") == 14:
                block["value_units"] = 20
                block["value_survived"] = 14
        path.write_text(json.dumps(record))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value


def _e2a_result(*, survived: int, units: int, bars: dict) -> dict:
    """An E2a result shaped like `e2a_outcome`'s own, without the record."""
    required = bars["E2a"]["required"]
    return measure.outcome(
        measured=True, below_floor=units == 0, passed=survived >= required,
        bar=f"{required} of {bars['E2a']['of']}",
        value=f"value_units {survived}/{units} survived")


def _e2b_result(*, fused: int, atomic: int, bars: dict) -> dict:
    """An E2b result shaped like `main`'s own, without the run."""
    share = fused / atomic if atomic else 1.0
    return measure.outcome(
        measured=True,
        below_floor=atomic < bars["floors"]["e2b_surviving_units"],
        passed=share <= bars["E2b"]["max_fused_share"],
        bar=f"fused share <= {bars['E2b']['max_fused_share']}",
        value=f"fused={fused}/{atomic} = {share:.6f}")


def test_the_e2_row_is_the_composite_and_not_e2a_alone():
    """P1: a record whose fused half fails must not render a GREEN row.

    E2 is `E2a AND E2b`. Deriving the row from E2a alone would let a failing
    proxy through — the row and the record it cites would disagree.

    ⚠️ This pin used to build the composite INLINE (`measure.outcome(...)` over
    an expression it had just written) and never reached the production path, so
    reverting `main` to `verdict_word(e2a_result)` left it green (#10610 review
    issue 1). It now calls `measure.e2_outcome` — the function `main` itself
    calls — and `test_the_e2_row_verdict_is_the_composite_main_uses` holds the
    wiring, so the mutant is killed from both ends.
    """
    bars = measure.parse_bars(json.loads(PREREG.read_text()))
    e2a_green = _e2a_result(survived=13, units=14, bars=bars)
    e2b_bad = _e2b_result(fused=7, atomic=114, bars=bars)
    e2b_good = _e2b_result(fused=1, atomic=114, bars=bars)
    assert e2a_green["outcome"] == "PASS", e2a_green
    assert e2b_bad["outcome"] == "FAIL", e2b_bad

    # 13/14 E2a beside a fused 7/114: the mutant (`verdict_word(e2a_result)`)
    # reads GREEN here; the composite reads RED, and the record itself says FAIL.
    composite = measure.e2_outcome(e2a_result=e2a_green, e2b_result=e2b_bad,
                                   bars=bars)
    assert composite["outcome"] == "FAIL", composite
    assert measure.verdict_word(composite) == "RED", composite
    # …and the success path IS reachable: 13/14 beside the committed fused 1/114.
    green = measure.e2_outcome(e2a_result=e2a_green, e2b_result=e2b_good,
                               bars=bars)
    assert green["outcome"] == "PASS", green
    assert measure.verdict_word(green) == "GREEN", green
    # and the record itself states which half decided it
    record = json.loads(SCORES.read_text())
    assert record["e2"]["outcome"] == "FAIL"
    assert record["e2"]["decided_by"] == "E2a"


def test_the_e2_composite_is_blocked_when_a_half_was_never_measured():
    """P2: a conjunction cannot be MEASURED when an operand was not.

    `main` builds a `measured=False` E2a result when the record carries no
    `e2a.outcome`. The composite hard-coded `measured=True` and took `below_floor`
    from E2b ALONE, so that record's row read RED — where the pre-composite path
    read BLOCKED — for a half nobody scored (#10610 P2).

    Non-vacuity: pre-fix the composite is measured=True / below_floor=False, so the
    first arm reads FAIL and the floor arm reads FAIL too.
    """
    bars = measure.parse_bars(_prereg())
    e2b_good = _e2b_result(fused=1, atomic=114, bars=bars)
    e2a_green = _e2a_result(survived=13, units=14, bars=bars)

    e2a_unmeasured = measure.outcome(
        measured=False, below_floor=False, passed=False, bar="13 of 14",
        value="value_units=None survived=None")
    composite = measure.e2_outcome(e2a_result=e2a_unmeasured,
                                   e2b_result=e2b_good, bars=bars)
    assert composite["measured"] is False, composite
    assert composite["outcome"] == "BLOCKED", composite
    assert measure.verdict_word(composite) == "BLOCKED", composite

    # …the other operand too: a half nobody measured cannot be inherited from.
    e2b_unmeasured = measure.outcome(
        measured=False, below_floor=False, passed=False, bar="fused",
        value="unmeasured")
    other = measure.e2_outcome(e2a_result=e2a_green,
                               e2b_result=e2b_unmeasured, bars=bars)
    assert other["outcome"] == "BLOCKED", other

    # …and the FLOOR half: E2a under its floor is a REFUSAL, and E2b's clean
    # `below_floor` may not overwrite it (that is what "E2b alone" meant).
    e2a_floored = measure.outcome(
        measured=True, below_floor=True, passed=False, bar="13 of 14",
        value="value_units 0/0 survived")
    floored = measure.e2_outcome(e2a_result=e2a_floored,
                                 e2b_result=e2b_good, bars=bars)
    assert floored["outcome"] == "REFUSED", floored
    assert measure.verdict_word(floored) == "BLOCKED", floored


def test_the_e2_note_never_claims_a_shortfall_for_a_half_that_was_not_measured():
    """P2's narration half: "falls short" is a SCORED finding, not a default.

    The note asserted "E2a falls short of 13 of 14 on the run's own graph" for an
    E2a half the record never scored — a measured claim over an unmeasured
    population, beside a row that now reads BLOCKED (#10610 P2).

    Non-vacuity: pre-fix the composite is FAIL, so the note carries "falls short"
    and the first assertion reds.
    """
    parsed = measure.measure_e1_e2(_REPO)
    bars = measure.parse_bars(_prereg())
    e2a_unmeasured = measure.outcome(
        measured=False, below_floor=False, passed=False, bar="13 of 14",
        value="value_units=None survived=None")
    e2b_good = _e2b_result(fused=1, atomic=114, bars=bars)
    composite = measure.e2_outcome(e2a_result=e2a_unmeasured,
                                   e2b_result=e2b_good, bars=bars)
    note = measure.e2_row_note(parsed, e2a_unmeasured, e2b_good, composite)
    assert "falls short" not in note, note
    assert "was NOT measured" in note, note
    assert "BLOCKED" in note, note


def test_the_e2_row_verdict_is_the_composite_main_uses():
    """The WIRING half of the composite pin (#10610 review issue 1).

    A pin that calls the helper proves the helper; it proves nothing about the
    row unless `main` derives the row from it. This reads `main`'s own AST and
    requires (a) a call to `e2_outcome` and (b) the `e2_row` dict's `verdict` to
    be `verdict_word(e2_result)` — the exact expression a revert to
    `verdict_word(e2a_result)`, or to a bare `e2_result = e2a_result`, breaks.
    """
    tree = ast.parse(PROBE.read_text())
    main = next(node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == "main")
    calls = {node.func.id for node in ast.walk(main)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert "e2_outcome" in calls, (
        "main no longer derives E2 through e2_outcome — the composite is inline "
        "again and no pin can reach it")
    row = next(node for node in ast.walk(main)
               if isinstance(node, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == "e2_row"
                       for t in node.targets)
               and isinstance(node.value, ast.Dict))
    keys = [key.value for key in row.value.keys
            if isinstance(key, ast.Constant)]
    verdict = row.value.values[keys.index("verdict")]
    assert isinstance(verdict, ast.Call) and getattr(verdict.func, "id", None) \
        == "verdict_word", ast.dump(verdict)
    assert [getattr(arg, "id", None) for arg in verdict.args] == ["e2_result"], (
        "the E2 row's verdict is no longer verdict_word(e2_result) — a revert to "
        "e2a_result would show the 13/14 E2a beside a fused 7/114 as GREEN")


def test_the_e1_e2_record_refuses_a_denominator_the_record_did_not_freeze_meta(
        tmp_path, capsys):
    """Guard: the frozen denominator and the E2a threshold agree in the record.

    They are asserted through the bar reader rather than against a second copy of
    the numbers: `corpus.value_bearing_units` and `criteria[E2a]`'s `of` are two
    frozen statements about ONE denominator, so a record where they disagree is a
    refusal (below) rather than a bar printed over whichever one the code read
    first.
    """
    floors = scores_probe.frozen_denominators(_REPO)
    assert floors["e2a_units"] == 14
    assert floors["bars"]["E2a"] == {"required": 13, "of": 14}

    repo = tmp_path / "repo"
    shutil.copytree(_REPO / "docs/evidence/9889", repo / "docs/evidence/9889")
    shutil.copytree(_REPO / "docs/evidence/7949-capstone",
                    repo / "docs/evidence/7949-capstone")
    path = repo / "docs/evidence/7949-capstone/preregistration.json"
    prereg = json.loads(path.read_text())
    for entry in prereg["criteria"]:
        if entry["id"] == "E2a":
            entry["threshold"] = "`>= 13 of 20`"   # disagrees with corpus \u2192 14
    path.write_text(json.dumps(prereg))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    assert "denominator disagree" in capsys.readouterr().err


def test_the_e1_e2_record_refuses_when_the_falsifier_is_inert(tmp_path):
    """A counter nothing moves is not a measurement — the record must refuse."""
    repo = tmp_path / "repo"
    shutil.copytree(_REPO / "docs/evidence/9889", repo / "docs/evidence/9889")
    shutil.copytree(_REPO / "docs/evidence/7949-capstone",
                    repo / "docs/evidence/7949-capstone")
    falsifier = repo / "docs/evidence/9889/e2a-value-units-falsifier.json"
    record = json.loads(falsifier.read_text())
    record["with_anchor"]["value_survived"] = 8      # injection moves nothing
    falsifier.write_text(json.dumps(record))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    # A refusal is exit 2 (not 1, which would read as a crash) and the reason is
    # printed on stderr — asserted on the CODE because the text went to stderr.
    assert refused.value.code == 2, refused.value


def test_the_e1_e2_record_refuses_a_corpus_off_the_frozen_pin(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(_REPO / "docs/evidence/9889", repo / "docs/evidence/9889")
    shutil.copytree(_REPO / "docs/evidence/7949-capstone",
                    repo / "docs/evidence/7949-capstone")
    log = repo / "docs/evidence/9889/on-arm.log"
    log.write_text(log.read_text().replace(
        "fixtures_hash=sha256:dd7e8520", "fixtures_hash=sha256:00000000"))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value


# ── the table shape, and the two rules that keep a row honest ────────────

def _rows(out_path: str = "docs/evidence/9889/e1-e2-scores.json",
          *, e2_verdict: str = "RED") -> list[dict]:
    parsed = measure.measure_e1_e2(_REPO)
    return measure.build_rows(
        head="a" * 40, out_path=out_path, log_corpus_sha="sha256:" + "d" * 64,
        e1={"verdict": measure.verdict_word(measure.e1_outcome(**_e1_kwargs())),
            "sha": "c" * 40, "corpus_sha256": "e" * 64,
            "numerator": measure.e1_pass_objects(parsed),
            "denominator": parsed["after_objects"],
            "objects_numerator": 28,
            "objects_total": 78, "note": "E1 note"},
        e2={"verdict": e2_verdict, "sha": "c" * 40, "corpus_sha256": "e" * 64,
            "numerator": 8, "denominator": 14, "note": "E2 note"},
        e3={"verdict": "RED", "numerator": 23, "denominator": 70,
            "note": "E3 note"},
        e4={"verdict": "RED", "numerator": 2, "denominator": 15,
            "sha": "6164c683c", "corpus_sha256": "sha256:" + "f" * 64,
            "note": "E4 note"},
        e5={"verdict": "RED", "numerator": 12, "denominator": 25,
            "note": "E5 note"})


def _e1_kwargs() -> dict:
    """`main`'s own E1 call — the population it FLOORS is the one it SCORES.

    `objects` is the AFTER arm's Objects, because that is the population E1's
    criterion is written over and the one the row's count slots carry. It used
    to be `objects_total` (the BEFORE arm), which made the row's floor arm
    unreachable — `read_off_arms` already refuses a before arm under the floor —
    and left the after arm the row floors unfloored (#10610 review issue 2).
    """
    parsed = measure.measure_e1_e2(_REPO)
    bars = measure.parse_bars(json.loads(PREREG.read_text()))
    return {"before_share": parsed["before_share"],
            "after_share": parsed["after_share"],
            "objects": parsed["after_objects"], "bars": bars,
            "decidable": parsed["decidable"],
            "below_floor": parsed["below_floor"]}


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
             cvc.check(record, repo=_build_gate_repo(), require_green=False).items]
    assert "V4" in codes, codes


def test_a_row_cites_the_artifact_it_was_written_to():
    """The P1 shape, on the probe that replaced it: E3 cites THIS run's log."""
    out = "docs/evidence/7949-capstone/capstone-measured-deadbeef.log"
    rows = {row["criterion"]: row for row in _rows(out_path=out)}
    assert rows["E3"]["evidence"] == out
    assert rows["E1"]["evidence"] == measure.E1_E2_SCORES
    assert rows["E4"]["evidence"] == measure.E4_RECEIPT
    assert rows["E5"]["evidence"] == measure.E5_RESCORE


def test_a_row_measured_on_another_corpus_declares_it(tmp_path):
    """E4 cites an artifact on another corpus revision — and says so.

    E1/E2 no longer take this path: they measure the FROZEN corpus, so their
    artifact digest IS the run's and the V7 declaration must be ABSENT (a
    declaration nothing needs is a claim the gate would then check). E4 does
    still cite a receipt from another revision, and must declare it.
    """
    rows = {row["criterion"]: row for row in _rows()}
    for criterion in ("E1", "E2"):
        row = rows[criterion]
        # E1/E2 now measure the FROZEN corpus, so their artifact digest IS the
        # run's — the V7 declaration is no longer needed and must not appear.
        assert "artifact_corpus_sha256" not in row, criterion
    row = rows["E4"]
    assert measure.normalise_corpus(row["corpus_sha256"]) != \
        measure.normalise_corpus("sha256:" + "d" * 64), "E4"
    assert row.get("artifact_corpus_sha256") == row["corpus_sha256"], "E4"
    for criterion in ("E3", "E5"):
        assert rows[criterion]["corpus_sha256"] == "sha256:" + "d" * 64, criterion
        assert "artifact_corpus_sha256" not in rows[criterion], criterion


def test_the_e1_and_e2_rows_carry_the_counts_their_criteria_are_written_over():
    """#10610 review issue 1: the measured count must REACH the row.

    E2a's frozen criterion is a count (`>= 13 of 14` over the 14 value-bearing
    units), and E2's row used to carry `numerator=None, denominator=None` beside
    `blocked_by="#10360"` — which `tools/capstone_verdict_check.py` refuses on a
    GREEN row (V5 requires an integer numerator/denominator, and a blocker and
    green are mutually exclusive). So the first run to reach 13/14 — the outcome
    the `#9889` record exists to make reachable — produced a table the gate exited
    1 on, and no pin noticed because the pin asserted the emptiness.

    The counts are DERIVED from the record here, not typed: a probe that starts
    reading another population reds this test rather than the table.
    """
    parsed = measure.measure_e1_e2(_REPO)
    rows = {row["criterion"]: row for row in _rows()}
    assert (rows["E2"]["numerator"], rows["E2"]["denominator"]) == (
        parsed["e2a_survived"], parsed["e2a_units"])
    # E1's slot is the PASS-direction half of its (minimised) share: the table's
    # numerator is a count that passed on every other row, and a bare-definite
    # count of 0 — the BEST outcome — would be refused by V5 as a silent drop.
    assert (rows["E1"]["numerator"], rows["E1"]["denominator"]) == (
        parsed["after_objects"] - parsed["after_numerator"],
        parsed["after_objects"])
    assert measure.e1_pass_objects({"after_numerator": 0,
                                    "after_objects": 61}) == 61, (
        "a run with NO bare-definite Objects must still be writable green")
    assert measure.e1_pass_objects({"after_numerator": None,
                                    "after_objects": 61}) is None
    # The two rows score two DIFFERENT populations (all Objects vs the frozen 14
    # value-bearing units), so neither may name the other as a `sibling`: V8
    # compares one population's two copies, and naming this pair siblings is what
    # forced both counts to None in the first place.
    assert "sibling" not in rows["E1"] and "sibling" not in rows["E2"]
    assert parsed["e2a_units"] != parsed["after_objects"], (
        "the two populations are the same number — re-derive this pin")


def test_the_e1_floor_is_applied_to_the_arm_the_row_counts():
    """#10610 review issue 2: the row's floor arm was unreachable.

    `main` passed `objects=e1["objects_total"]` — the BEFORE arm, which
    `read_off_arms` has already refused below the floor, so `objects < floor`
    could never be true — while the row's count slots carried `after_objects`, and
    the after arm's own floor finding was folded into `decidable` and lost. Two
    DERIVED assertions, both non-vacuous against the pre-fix call:

    (1) the population `main` scores is the AFTER arm, read off `main`'s AST, so a
        revert to `objects_total` reds here rather than at run time; and
    (2) an under-floor after arm is REFUSED **as a floor finding**, not reported as
        a pairing disagreement it does not have (the pre-fix `e1_outcome` returned
        BLOCKED with "the pairings DISAGREE" for this input).
    """
    parsed = measure.measure_e1_e2(_REPO)
    bars = measure.parse_bars(json.loads(PREREG.read_text()))
    assert _e1_kwargs()["objects"] == parsed["after_objects"]
    assert parsed["after_objects"] != parsed["objects_total"], (
        "the two arms are the same size here — re-derive this pin")

    # (1) the production call, off `main`'s own AST: `objects=e1["after_objects"]`.
    tree = ast.parse(PROBE.read_text())
    main = next(node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == "main")
    call = next(node for node in ast.walk(main)
                if isinstance(node, ast.Call)
                and getattr(node.func, "id", None) == "e1_outcome")
    objects = next(kw.value for kw in call.keywords if kw.arg == "objects")
    assert isinstance(objects, ast.Subscript), ast.dump(objects)
    assert objects.value.id == "e1" and objects.slice.value == "after_objects", (
        "main floors the wrong arm — E1's criterion is written over the AFTER "
        "arm's Objects, and that is the population the row's count slots carry")
    assert any(kw.arg == "below_floor" for kw in call.keywords), (
        "main drops the record's own floor finding, so an under-floor after arm "
        "is narrated as a pairing disagreement")

    # (2) the arithmetic: floor FIRST, decidability second.
    floor = bars["floors"]["e1_objects"]
    refused = measure.e1_outcome(before_share=0.3590, after_share=0.0500,
                                 objects=floor - 5, bars=bars,
                                 decidable=False, below_floor=True)
    assert refused["outcome"] == "REFUSED", refused
    assert refused["below_floor"] is True, refused
    assert f"under the frozen floor of {floor}" in refused["value"], refused
    assert "DISAGREE" not in refused["value"], refused
    # The disagreement arm still reports the disagreement (non-vacuity both ways).
    disagreed = measure.e1_outcome(before_share=0.3590, after_share=0.2131,
                                   objects=parsed["after_objects"], bars=bars,
                                   decidable=False, below_floor=False)
    assert disagreed["outcome"] == "BLOCKED", disagreed
    assert "DISAGREE" in disagreed["value"], disagreed


def test_the_e1_note_narrates_the_state_the_row_is_in():
    """#10610 review issue 3: the refusal sentence was unconditional.

    Every scored log would have carried "the pairings do not agree, so the row is
    REFUSED" — so the first run that DECIDES E1 writes that sentence beside its own
    `PASS`. The note must state the state it is in, and the three states must stay
    DISTINCT (floor ≠ disagreement ≠ decided).
    """
    parsed = measure.measure_e1_e2(_REPO)
    bars = measure.parse_bars(json.loads(PREREG.read_text()))
    decided = dict(parsed, decidable=True, below_floor=False)
    decided_out = measure.e1_outcome(before_share=0.3590, after_share=0.2131,
                                     objects=parsed["after_objects"], bars=bars,
                                     decidable=True, below_floor=False)
    assert decided_out["outcome"] in {"PASS", "FAIL"}, decided_out
    note = measure.e1_row_note(decided, decided_out, bars)
    assert "do not agree" not in note, note
    assert "not RED and not GREEN" not in note, note
    assert decided_out["outcome"] in note, note

    refused_out = measure.e1_outcome(**_e1_kwargs())
    refused_note = measure.e1_row_note(parsed, refused_out, bars)
    assert "do not agree" in refused_note, refused_note

    under = dict(parsed, decidable=False, below_floor=True,
                 after_objects=bars["floors"]["e1_objects"] - 5)
    floor_out = measure.e1_outcome(before_share=parsed["before_share"],
                                   after_share=parsed["after_share"],
                                   objects=under["after_objects"], bars=bars,
                                   decidable=False, below_floor=True)
    floor_note = measure.e1_row_note(under, floor_out, bars)
    assert "do not agree" not in floor_note, floor_note
    assert f"frozen floor of {bars['floors']['e1_objects']}" in floor_note, (
        floor_note)


def test_the_e2_note_never_claims_a_shortfall_that_did_not_happen():
    """#10610 review issue 3, on the row this record exists to unbury.

    "E2a falls short of 13 of 14" was hard-coded, so the 13/14 run — the one
    where E2's row finally reads GREEN — would have printed the shortfall beside
    its own GREEN verdict, in the log that IS the evidence.
    """
    parsed = measure.measure_e1_e2(_REPO)
    bars = measure.parse_bars(json.loads(PREREG.read_text()))
    e2a_green = _e2a_result(survived=13, units=14, bars=bars)
    e2b_good = _e2b_result(fused=1, atomic=114, bars=bars)
    green = measure.e2_outcome(e2a_result=e2a_green, e2b_result=e2b_good,
                               bars=bars)
    assert measure.verdict_word(green) == "GREEN", green
    note = measure.e2_row_note(parsed, e2a_green, e2b_good, green)
    assert "falls short" not in note, note
    assert "meets the 13-of-14 bar" in note, note

    e2a_red = _e2a_result(survived=8, units=14, bars=bars)
    red = measure.e2_outcome(e2a_result=e2a_red, e2b_result=e2b_good, bars=bars)
    assert measure.verdict_word(red) == "RED", red
    red_note = measure.e2_row_note(parsed, e2a_red, e2b_good, red)
    assert "falls short of 13 of 14" in red_note, red_note


def test_the_record_does_not_narrate_an_under_floor_after_arm_as_a_disagreement(
        tmp_path):
    """#10610 review issue 2, third consequence: `why` contradicted `outcome`.

    `e1_pairings` folds the floor into `decidable`, so a below-floor after arm had
    `outcome: REFUSED` beside a `why` reading "every before realization agrees on
    the pairing" — a pass condition printed against a refusal. The floor is now
    narrated as the floor, on the record itself. Non-vacuity: the pre-fix `why`
    for this input is asserted absent.
    """
    repo = _copied_bundle(tmp_path)
    log = repo / "docs/evidence/9889/on-arm.log"
    log.write_text(re.sub(r"^e1-aux objects_in_graph=61",
                          "e1-aux objects_in_graph=25", log.read_text(),
                          count=1, flags=re.MULTILINE))
    e1 = scores_probe.score(repo)["e1"]
    assert e1["below_floor"] is True, e1
    assert e1["outcome"] == "REFUSED", e1
    assert "under the frozen" in e1["why"], e1["why"]
    assert "paired against two independent before" not in e1["why"], e1["why"]
    assert "agrees on the pairing" not in e1["why"], e1["why"]


def test_the_durations_map_carries_one_row_per_file():
    """A DUPLICATE key is a stale claim that YAML silently keeps only half of.

    The `p0-measure` merge left two `test_7949_capstone_measure.py` rows in
    `durations:` — one describing 79 cases, one describing 75 — and every reader
    (YAML keeps the LAST; the count pin takes the FIRST) saw a different one. A
    pin that reads one line of a file that carries two cannot claim the file is
    clean (the manifest's own 51st pin names this class).
    """
    manifest = (_REPO / "config" / "ci-surfaces.yml").read_text()
    rows = [line for line in manifest.splitlines()
            if line.strip().startswith("test_7949_capstone_measure.py:")]
    assert len(rows) == 1, (
        f"the durations map carries {len(rows)} rows for this file — a duplicate "
        "key is a claim YAML keeps only the last of:" + "".join(
            f"\n  {row[:90]}" for row in rows))


def _build_gate_repo() -> Path:
    """A throwaway git work tree with NO promisor remote, carrying the real `docs/`.

    `cvc.check` resolves every row / pre-registration sha through
    `git cat-file -e`, and the pins below stamp SYNTHETIC shas (`"a"*40`,
    `"c"*40`) that resolve nowhere by design. On a FULL clone that answer comes
    back `False` instantly; on a PARTIAL clone — `blob:none`, which is how every
    worktree of this repo is checked out — git treats the missing object as a
    PROMISOR object and lazy-fetches it from `origin`, one network round trip per
    sha. The result is not a failing assertion but a hang: measured at this head,
    `test_a_green_e2_at_thirteen_of_fourteen_is_not_refused_for_its_own_blocker`
    and `test_a_green_row_on_a_missing_artifact_is_refused` both died at
    `Failed: Timeout (>300.0s)` inside `tools/capstone_verdict_check.py::_git`
    (stack: `_resolves` → `_git` → `subprocess.run`), the same environment class
    the sibling `test_7949_capstone_verdict_check.py` disclosed.

    A throwaway repo has no promisor remote, so an unresolvable sha is the
    instant `False` CI sees. `docs/` is symlinked in so V4's artifact-existence
    check still reads the REAL bundle rather than an empty directory.

    Rebuilt per call (no `lru_cache`): this suite reaps its per-test `TMPDIR`
    after every test, so a cached path would hand the NEXT test a `cwd` that no
    longer exists (`FileNotFoundError` out of `subprocess.run`).
    """
    repo = Path(tempfile.mkdtemp(prefix="capstone-gate-repo-"))
    (repo / "docs").symlink_to(_REPO / "docs", target_is_directory=True)
    (repo / "README.md").write_text("throwaway work tree for the gate pins\n")
    cvc._git(repo, "init", "-q")
    cvc._git(repo, "add", "README.md")
    cvc._git(repo, "-c", "user.email=pin@example.invalid",
             "-c", "user.name=pin", "commit", "-qom", "pin", "README.md")
    assert cvc._is_git_repo(repo), repo
    assert not cvc._resolves(repo, "a" * 40), (
        "the throwaway repo must not be able to resolve a synthetic sha")
    return repo


def _gate_codes(rows: list[dict]) -> list[str]:
    record = {"schema": cvc.SCHEMA, "prereg": {"sha": "a" * 40, "status": "OK"},
              "corpus": {"sha256": "sha256:" + "e" * 64},
              "run": {"status": "COMPLETED",
                      "steps_observed": list(cvc.STEPS)},
              "rows": list(rows)}
    return [code for code, _w, _m in
            cvc.check(record, repo=_build_gate_repo(), require_green=False).items]


def test_a_green_e2_at_thirteen_of_fourteen_is_not_refused_for_its_own_blocker():
    """The success path this record unburies must survive the gate (issue 1).

    Non-vacuity: the SAME row in its pre-fix shape — empty count slots plus the
    `#10360` blocker it carried while it could not be measured — is refused by V5
    on this very input, so the assertion below cannot pass on the old builder.
    """
    green = {row["criterion"]: row for row in _rows(e2_verdict="GREEN")}
    green["E2"].update({"numerator": 13, "denominator": 14})
    codes = _gate_codes(list(green.values()))
    assert "V5" not in codes, codes
    assert "V8" not in codes, codes
    assert green["E2"]["blocked_by"] is None, green["E2"]

    prefix_shape = dict(green["E2"])
    prefix_shape.update({"numerator": None, "denominator": None,
                         "blocked_by": "#10360"})
    old = {**green, "E2": prefix_shape}
    old_codes = _gate_codes(list(old.values()))
    assert "V5" in old_codes, old_codes


def _copied_bundle(tmp_path) -> Path:
    """A throwaway repo carrying ONLY the records the score driver reads.

    The probes refuse an input they cannot find, so a mutation test needs the whole
    bundle, not one file: mutation is how a "read from the frozen record" claim is
    proven rather than asserted.
    """
    repo = tmp_path / "repo"
    shutil.copytree(_REPO / "docs/evidence/9889", repo / "docs/evidence/9889")
    shutil.copytree(_REPO / "docs/evidence/7949-capstone",
                    repo / "docs/evidence/7949-capstone")
    return repo


def test_the_scores_probe_reads_every_bar_out_of_the_frozen_record(tmp_path):
    """#10610 review issue 2: one reader of the frozen bars, not two.

    `e1_e2_scores.py` typed `0.50`, `13`, `0.05` and `90` while loading — and never
    reading — the `criteria` block, so an amendment would move the probe that
    scores the row and leave the record both rows cite behind: two readers of one
    frozen bar, which can only disagree. Every bar is now read by
    `capstone_measure.parse_bars`, the bundle's ONE reader. Non-vacuity: the
    mutated thresholds below move the OUTCOMES, which a typed bar could not.
    """
    repo = _copied_bundle(tmp_path)
    unchanged = scores_probe.score(repo)
    assert unchanged["e1"]["outcome"] == "REFUSED"
    assert unchanged["e2"]["outcome"] == "FAIL"

    # E1's multiplier 0.50 -> 0.20: both pairings now fail, so they AGREE and E1
    # becomes a decided FAIL instead of a refusal. E2a's bar 13 -> 8: the 8 that
    # survive now pass. E2b's bar 0.05 -> 0.001: the 1/114 share no longer does.
    path = repo / "docs/evidence/7949-capstone/preregistration.json"
    prereg = json.loads(path.read_text())
    mutated = {"E1": "`after_share <= 0.20 × before_share`",
               "E2a": "`>= 8 of 14`",
               "E2b": "`<= 0.001`"}
    for entry in prereg["criteria"]:
        if entry["id"] in mutated:
            entry["threshold"] = mutated[entry["id"]]
    path.write_text(json.dumps(prereg))
    moved = scores_probe.score(repo)
    assert moved["e1"]["outcome"] == "FAIL", moved["e1"]
    assert moved["e1"]["decidable"] is True, moved["e1"]
    assert moved["e2"]["e2a"]["outcome"] == "PASS", moved["e2"]["e2a"]
    assert moved["e2"]["outcome"] == "FAIL", moved["e2"]  # E2b's bar now fails


def test_the_scores_probe_refuses_a_denominator_off_the_frozen_fourteen(
        tmp_path, capsys):
    """#10610 review issue 3: `13 of 14` may not be printed over an N.

    The only denominator assertion compared the falsifier against the record — both
    sides of that comparison come from the record's own population, so they move
    together and pin nothing. The frozen `14` is now asserted.
    """
    repo = _copied_bundle(tmp_path)
    e2a = repo / "docs/evidence/9889/e2a-value-units.json"
    record = json.loads(e2a.read_text())
    record["value_units"] = 15
    e2a.write_text(json.dumps(record))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    assert "froze 14" in capsys.readouterr().err


def test_the_scores_probe_refuses_an_after_arm_on_another_graph(tmp_path, capsys):
    """#10610 review issue 4: the invariant's other half, actually implemented.

    The module states "one record, one head" and checked only the BEFORE arms and
    the E2a<->gate-drops pair. The ON run's own `# graph:` line was parsed for
    nothing, so an after share from graph A beside E2a counts from graph B passed
    every check: the head is equal, and both before graphs still name it.
    """
    repo = _copied_bundle(tmp_path)
    log = repo / "docs/evidence/9889/on-arm.log"
    log.write_text(re.sub(r"^# graph: \S+", "# graph: capstone_on_54922a800_other",
                          log.read_text(), count=1, flags=re.MULTILINE))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    assert "capstone_on_54922a800_other" in capsys.readouterr().err


def test_the_scores_probe_refuses_a_record_that_does_not_name_the_on_head(
        tmp_path, capsys):
    """#10610 review issue 1's 79th claim: the head loop had no pin at all.

    `config/ci-surfaces.yml` states that a pin requires "every record the score
    cites (E2a, falsifier, gate-drops) to name the ON run's head". The loop at
    `e1_e2_scores.py` (`for name, record in ((E2A_RECORD, …), …)`) can be deleted
    whole and every case stayed green.

    The mutation moves ALL THREE cited records to the SAME foreign graph, so the
    graph-IDENTITY check (`E2a` vs `gate_drops`) still passes and the ON-vs-E2a
    check is not what refuses: the only arm that can refuse is the head loop, and
    the assertion on its own reason text proves it is the arm that fired. Delete
    the loop and this test reds (no refusal, or the wrong reason).
    """
    repo = _copied_bundle(tmp_path)
    foreign = "capstone_ledger_deadbeef_1700000000"
    for name in (scores_probe.E2A_RECORD, scores_probe.E2A_FALSIFIER,
                 scores_probe.GATE_DROPS):
        path = repo / "docs/evidence/9889" / name
        record = json.loads(path.read_text())
        record["graph"] = foreign
        for block in ("with_anchor", "as_written"):
            if isinstance(record.get(block), dict):
                record[block]["graph"] = foreign
        path.write_text(json.dumps(record))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    err = capsys.readouterr().err
    assert "does not name head" in err, err
    assert foreign in err, err


def _literal_path(node: ast.AST) -> tuple[str, ...] | None:
    """A chained LITERAL subscript's key path — `bars['floors']['x']` → 3 keys.

    The `e1["k"]` scan below is a single Subscript whose value is a Name, so it
    structurally cannot see a nested read. This walks the chain so the scan can
    cover the two maps the renderer reads, not only one of them.
    """
    keys: list[str] = []
    while isinstance(node, ast.Subscript):
        if not (isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, str)):
            return None
        keys.append(node.slice.value)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    return (node.id, *reversed(keys))


def test_every_e1_key_the_renderer_reads_is_one_the_measure_returns():
    """P1 regression: `render_log` crashed AFTER the 7-session LLM measurement.

    The E1/E2 re-point removed keys `render_log` still subscripted
    (`declared_prereg` / `declared_result`), so a real run burned its cost and
    wrote nothing. No test rendered a log, so 70 green cases could not see it.
    This asserts the SHAPE statically: every `e1["k"]` the renderer and the
    machine-record builder read must be a key `measure_e1_e2` returns, and every
    `bars[…][…]` path they read must be one `parse_bars` emits. The bar half is
    #10610 review issue 1: `render_log`'s new E1 note subscripted
    `bars["floors"]["e1_min_before_share"]`, a key `parse_bars` never emitted, so a
    real run paid its 7-session cost and then died at render time — and the
    `e1[...]` scan could not see a Subscript of a Subscript, so every pin stayed
    green. Both maps are now resolved against the committed record.
    """
    returned = set(measure.measure_e1_e2(_REPO))
    tree = ast.parse(PROBE.read_text())
    # The row-note builders are renderers too: they live outside `main` (extracted
    # so their narration is pinnable, #10610 review issue 3), and a key renamed out
    # from under them would crash a real 7-session run exactly as `render_log`'s did.
    watched = {"render_log", "main", "e1_row_note", "e2_row_note"}
    read: set[str] = set()
    bar_reads: set[tuple[str, ...]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in watched:
            for inner in ast.walk(node):
                if (isinstance(inner, ast.Subscript)
                        and isinstance(inner.value, ast.Name)
                        and inner.value.id == "e1"
                        and isinstance(inner.slice, ast.Constant)
                        and isinstance(inner.slice.value, str)):
                    read.add(inner.slice.value)
                # …and the same rule for the BAR map: a `bars['floors'][…]` read
                # is a Subscript OF a Subscript, which the `e1[...]` shape above
                # cannot see at all (#10610 review issue 1 — the renderer
                # subscripted a floor `parse_bars` never emitted, and every pin
                # stayed green because nothing resolved a `bars` key).
                path = _literal_path(inner)
                if path and path[0] == "bars":
                    bar_reads.add(path)
    assert read, "the scan found no e1[...] reads — it would prove nothing"
    assert not (read - returned), sorted(read - returned)
    assert bar_reads, "the scan found no bars[...] reads — it would prove nothing"
    resolved = measure.parse_bars(_prereg())
    for path in sorted(bar_reads):
        node: object = resolved
        for key in path[1:]:
            assert isinstance(node, dict) and key in node, (
                f"{'[' + ']['.join(path) + ']'} — `parse_bars` does not carry "
                f"{key!r} (it carries {sorted(node) if isinstance(node, dict) else node})")
            node = node[key]


def test_the_renderer_renders_a_log_from_the_committed_record(monkeypatch):
    """#10610 review issue 1 — the render had never been EXECUTED by any test.

    A static scan can only ask about keys it knows how to find. This runs the real
    `render_log` over the committed artifacts with no DB, no model and no
    network: `measure_e1_e2` reads `docs/evidence/9889/e1-e2-scores.json`, the
    census is canned (`_canned_census`), the E4 receipt is the committed one, and
    every other argument is a plain dict. Pre-fix this raised
    `KeyError: 'e1_min_before_share'` — the render-time crash a real run reaches
    only AFTER burning its LLM budget.

    Non-vacuity: the floor is moved IN THE RECORD (0.2 → 0.35) and the re-render
    must print the moved number, so the sentence is read from the record rather
    than typed into the f-string.
    """
    census_data = _canned_census(monkeypatch)
    e1 = measure.measure_e1_e2(_REPO)
    e4 = measure.load_e4(_REPO)

    def render(record: dict) -> str:
        bars = measure.parse_bars(record)
        corpus_sha = record["corpus"]["fixtures_hash"]
        e1_out = measure.e1_outcome(**_e1_kwargs())
        e2a_out = (measure.e2a_outcome(e1, bars) if e1["e2a_outcome"] else
                   measure.outcome(
                       measured=False, below_floor=False, passed=False,
                       bar=f"{bars['E2a']['required']} of {bars['E2a']['of']}",
                       value=f"value_units={e1['e2a_units']} "
                             f"survived={e1['e2a_survived']}"))
        e2b_floor = bars["floors"]["e2b_surviving_units"]
        e2b_share = (e1["fused"] / e1["atomic_points"]
                     if e1["atomic_points"] else 1.0)
        e3_out = measure.e3_outcome(anchored=census_data["anchored_total"],
                                    derived=census_data["derived_total"],
                                    bars=bars)
        e5 = {"arms_dir": "docs/evidence/7947/arms", "artifact": "fixture",
              "raw_loss_count": 3, "raw_n": 25, "raw_loss": 3 / 25,
              "narrative_loss_count": 4, "narrative_n": 25,
              "narrative_loss": 4 / 25,
              "recall": {"raw": {"recalled": 22, "planted": 25},
                         "narrative": {"recalled": 21, "planted": 25}},
              "warning": [], "partial_receipts": []}
        e5_out = measure.e5_outcome(raw_loss=e5["raw_loss"],
                                    narrative_loss=e5["narrative_loss"],
                                    sentences=e5["raw_n"], bars=bars)
        e4_out = measure.e4_outcome(edge_correct=e4["edge_correct"],
                                    planted=e4["planted"], bars=bars)
        return measure.render_log(
            head="f" * 40, prereg_sha="0" * 40, bars=bars,
            corpus_sha=corpus_sha, graph="capstone_measure_fixture_0",
            runs=[{"session_id": "s0", "ok": True,
                   "extraction_mode": "measured", "extracted": 1,
                   "errors": 0, "turns": 1, "elapsed_s": 1.0,
                   "stats": {"edus": 1, "chunks": 1,
                   "entities": 1, "events": 1, "points": 1, "operators": 1,
                   "search_queries": 1, "search_degraded": 0,
                   "operator_endpoints_minted": 0, "elapsed_s": 1.0}}],
            observed={}, unobserved={}, census_data=census_data,
            e1=e1, e1_aux={"bare_definite": 0, "share": None},
            e1_out=e1_out,
            e1b_out=measure.outcome(
                measured=True, below_floor=False,
                passed=e1["real_facts"] >= bars["E1b"]["required"],
                bar=f"{bars['E1b']['required']} of {bars['E1b']['of']}",
                value=f"real_facts={e1['real_facts']}"),
            e2a_out=e2a_out,
            e2b_out=measure.outcome(
                measured=True,
                below_floor=e1["atomic_points"] < e2b_floor,
                passed=e2b_share <= bars["E2b"]["max_fused_share"],
                bar=f"fused share <= {bars['E2b']['max_fused_share']}",
                value=f"fused={e1['fused']}/{e1['atomic_points']}"),
            e3={"verdict": measure.verdict_word(e3_out),
                "numerator": census_data["anchored_total"],
                "denominator": census_data["derived_total"],
                "below_floor": e3_out["below_floor"],
                "outcome": e3_out["outcome"], "bar": e3_out["bar"],
                "note": measure.e3_row_note(
                    composition=census_data.get("point_composition"),
                    e3_result=e3_out, graph="capstone_measure_fixture_0",
                    corpus_sha=corpus_sha)},
            e3_out=e3_out, e4=e4, e4_out=e4_out, e5=e5, e5_out=e5_out,
            rows=_rows(), out_path="docs/evidence/9889/e1-e2-scores.json")

    text = render(_prereg())
    assert "e1 note: both before realizations clear E1's own floors" in text
    assert "e1_min_before_share 0.2" in text, text[-3000:]
    # The number is READ, not typed: the same render over a moved floor moves it.
    moved = _prereg()
    moved["denominator_floors"]["e1_min_before_share"] = 0.35
    assert "e1_min_before_share 0.35" in render(moved)


def test_the_committed_score_record_is_what_the_probe_produces():
    """P2-10: the committed record may not drift from its own inputs.

    Without this, `e1b`, `e2b`, `gate_drops` and `denominators` could be
    hand-edited (or fall behind a probe change) while every other pin stayed
    green — the artifact would then be a claim, not a measurement.
    """
    assert scores_probe.score(_REPO) == json.loads(SCORES.read_text())


def test_the_e1b_note_claims_only_the_arm_the_record_carries():
    """P3 (#10610 review issue 5): `e1b.note` claimed BOTH arms for a number
    read from ONE.

    `read_on_arm` greps a single `e1b outcome=… real_facts=` line out of
    `on-arm.log`, and `read_off_arms` returns NO `real_facts` at all for either
    before realization — so "on both arms" was a comparison this artifact does
    not carry, and only the graph's OWN turn nodes could ever corroborate it.
    `capstone_measure.py` renders the same number as "on the scored arm" (its
    `e1b note:` line), so a record saying "both arms" also disagrees with the
    row reader over the same fact.

    Non-vacuity: the OFF-arm shape asserted first is the REASON the note cannot
    say "both arms" — on the pre-fix record the `"scored arm" in note` arm reds.
    """
    off_arms = scores_probe.read_off_arms(
        _REPO, scores_probe.frozen_denominators(_REPO))
    assert off_arms, "no OFF arms read — re-derive this pin"
    assert all("real_facts" not in arm for arm in off_arms), (
        "an OFF arm now carries real_facts, so the note MAY claim both arms — "
        f"but it must then name the reading: {off_arms}")

    note = json.loads(SCORES.read_text())["e1b"]["note"]
    assert "scored arm" in note, (
        f"e1b.note does not name the arm its number is read from: {note!r}")
    assert "both arms" not in note, (
        f"e1b.note claims a population the record does not carry: {note!r}")


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


# ── the census's per-label set is NOT the E3 row selector (#10525 review P1) ──

#: The shipped log's own per-label block, re-used as canned Cypher rows
#: (`capstone-measured-e0fc940b.log:32-34` + `:Point` composition).
_CENSUS_ROWS = {
    "Point": (338, 141, 141, 0),
    "Object": (50, 0, 0, 0),
    "Event": (45, 7, 0, 7),
    "Document": (0, 0, 0, 0),
}


class _CannedQuery:
    """A census-shaped fake: the REAL statements, answered per statement."""

    def __init__(self, cypher: str) -> None:
        if "UNWIND labels(n)" in cypher:
            rows = [["Point", 338], ["Object", 50], ["Event", 45]]
        elif "RETURN n.name" in cypher:                                    # object_names
            rows = [["the quarry"]]
        elif "eventId IS NOT NULL" in cypher:                              # event_stamped
            rows = [[159]]
        elif "type(r) AS t" in cypher:                                     # relationships
            # The log's own `e3_census.relationship_counts` / `.relationship_anchored`
            # (`capstone-measured-e0fc940b.log:250-260`): `extractedFrom` carries **141**
            # links, all 141 anchored — the same 141 claims, one link each. A 150 here
            # was the writer-class size (`extraction_claims` + `other`) written into the
            # LINK slot, and the 9 `other` Points are defined by `deriv = 0`, i.e. by
            # carrying no link at all, so 150 links is not a shape that graph can have.
            # `test_the_canned_census_transcribes_the_committed_log_it_cites` checks
            # every one of these numbers against the artifact.
            rows = [["extractedFrom", 141, 141], ["references", 7, 7],
                    ["aboutObject", 211, 0]]
        elif "AS points" in cypher:                                        # composition
            # Columns, in `census()`'s own unpack order (#10525 review):
            #   points=turn_rows=operator_points=extraction_claims=other_points=
            #   extraction_claims_anchored=turn_rows_anchored=
            #   operator_points_anchored=other_points_anchored
            # The shipped log's own composition line
            # (`capstone-measured-e0fc940b.log:37`) is
            # `turn_rows=159 (anchored 0) extraction_claims=141 (anchored 141)
            #  operator_points=29 (anchored 0) other=9 (anchored 0) (total=338)`,
            # and it PARTITIONS 338 (159 + 29 + 141 + 9). The 150 that sits one
            # column to the left here is the `extractedFrom` LINK count
            # (`["extractedFrom", 150, 141]` above: 141 claims + 9 `other`, one
            # each) — not a claim count. Transcribing it into the claim slot made
            # the fixture's own partition impossible (347 ≠ 338) and pinned a
            # value the artifact it cites cannot produce.
            rows = [[338, 159, 29, 141, 9, 141, 0, 0, 0]]
        else:                                                             # anchor census
            label = re.match(r"MATCH \(n:(\w+)\)", cypher).group(1)
            rows = [list(_CENSUS_ROWS[label])]
        self.result_set = rows


class _CannedGraph:
    def query(self, cypher: str) -> _CannedQuery:
        return _CannedQuery(cypher)


def _canned_census(monkeypatch) -> dict:
    """Run the REAL `census()` against canned rows, with no database anywhere."""
    projection = types.ModuleType("tortoise.projection")
    projection.FalkorProjection = type(
        "FalkorProjection", (),
        {"from_uri": staticmethod(lambda uri: types.SimpleNamespace(g=_CannedGraph()))},
    )
    monkeypatch.setitem(sys.modules, "tortoise", types.ModuleType("tortoise"))
    monkeypatch.setitem(sys.modules, "tortoise.projection", projection)
    return measure.census("fake://canned")


def test_the_census_enumerates_object_even_though_e3_no_longer_does(monkeypatch):
    """#10525 review P1 — a SILENT ZERO on a sibling instrument, from an E3-only change.

    `tools/s3_prior_ablation.py`'s `created_counts` reads `per_label["Object"]["nodes"]` as the
    `objects` numerator of `extraction_nodes = claims + objects + events` (#10463), on live data
    three times per run. Enumerating `per_label` with `DERIVED_LABELS` deleted that key when #9736
    narrowed E3 to the anchored classes, so every future ablation run printed `objects=0` while the
    graph held Objects — and `git grep` found no test anywhere that could see it.

    Non-vacuity: at the pre-fix head `per_label` carries Point/Document/Event only, so the first
    assertion reds (and `created_counts` in the sibling test returns 0 objects).
    """
    data = _canned_census(monkeypatch)
    assert "Object" in data["per_label"], sorted(data["per_label"])
    assert data["per_label"]["Object"]["nodes"] == 50, data["per_label"]["Object"]
    # The row SELECTOR is unchanged and still names what #9736 registered: `:Object` is not in it.
    assert "Object" not in measure.DERIVED_LABELS, measure.DERIVED_LABELS
    assert set(measure.DERIVED_LABELS) <= set(measure.CENSUS_LABELS)
    # …and E3's totals stay on the ROW's classes: 338 + 45 + 0, never 433.
    assert data["derived_total"] == 383, data["derived_total"]
    assert data["anchored_total"] == 148, data["anchored_total"]


def test_the_sibling_ablation_instrument_still_counts_objects(monkeypatch):
    """The consumer of the census, called as it calls itself (`created_counts`).

    This is the end the P1 was measured from: the census's shape is only worth pinning because
    `extraction_nodes` — #10463's primary denominator/numerator, published as `ratio=0.870` — is
    `extraction_claims + objects + events`. A census that drops `Object` narrows a published ratio
    with no error, no refusal and no log line.
    """
    spec = _ilu.spec_from_file_location("s3_prior_ablation_pure",
                                        str(_REPO / "tools/s3_prior_ablation.py"))
    assert spec and spec.loader
    s3 = _ilu.module_from_spec(spec)
    spec.loader.exec_module(s3)
    counts = s3.created_counts(_canned_census(monkeypatch))
    assert counts["objects"] == 50, counts
    # Read off the log's own composition line, never off the link count:
    # `extraction_claims=141 (anchored 141)` (`capstone-measured-e0fc940b.log:37`).
    assert counts["extraction_claims"] == 141, counts
    assert counts["events"] == 45, counts
    # 141 + 50 + 45, which is what #10463's committed log can produce; the 245
    # this pin used to assert came from the mis-transcription above.
    assert counts["extraction_nodes"] == 236, counts
    # The partition the fixture must satisfy to be a transcription at all: the
    # composition's classes sum to the `Point` total the census reports.
    composition = _canned_census(monkeypatch)["point_composition"]
    assert (composition["turn_rows"] + composition["operator_points"]
            + composition["extraction_claims"]
            + composition["other_points"]) == composition["points"], composition


def test_the_canned_census_transcribes_the_committed_log_it_cites(monkeypatch):
    """The fixture SAYS it is the shipped log's block; check that against the artifact.

    Round 4 of this PR found the claim unchecked in three columns at once — the
    composition row carried the `extractedFrom` LINK count in the extraction-CLAIM
    slot (so its own classes summed to 347, not the 338 the census reports), and the
    relationship row carried the writer-class total in the link slot. A fixture whose
    provenance is asserted in a comment rather than read from the file it cites can
    pin any number at all — which is the defect class this whole PR is about, turned
    on the very test that was supposed to catch it.

    So: parse the committed log's own machine record (`=== CAPSTONE-VERDICT-BEGIN/END
    ===`, the same block `capstone_verdict_check.extract_block` reads) and require the
    fixture to match it field for field. The fake is a REDUCED census — it answers the
    statements these tests exercise, not every one — so each field it DOES answer must
    be the log's.
    """
    text = (_REPO / "docs/evidence/7949-capstone"
            / "capstone-measured-e0fc940b.log").read_text()
    begin, end = "=== CAPSTONE-VERDICT-BEGIN ===", "=== CAPSTONE-VERDICT-END ==="
    assert begin in text and end in text, "the shipped log lost its verdict block"
    record = json.loads(text.split(begin, 1)[1].split(end, 1)[0])
    block = record["run"]["e3_census"]

    data = _canned_census(monkeypatch)

    # 1. The composition row, column for column (`census()`'s own unpack order).
    assert data["point_composition"] == block["point_composition"], (
        data["point_composition"], block["point_composition"])

    # 2. The per-label block — and `Document` is a real 0, not a dropped key.
    for label, expected in block["per_label"].items():
        got = data["per_label"][label]
        assert (got["nodes"], got["anchored"], got["anchored_out"],
                got["anchored_in"]) == (
                    expected["nodes"], expected["anchored"],
                    expected["anchored_out"], expected["anchored_in"]), (label, got)
    assert "Document" not in block["per_label"], block["per_label"]
    assert data["per_label"]["Document"]["nodes"] == 0, data["per_label"]

    # 3. Every relationship the fake answers carries the log's count AND anchor count.
    for name, count in data["relationship_counts"].items():
        assert count == block["relationship_counts"][name], (name, count)
        assert (data["relationship_anchored"][name]
                == block["relationship_anchored"][name]), (name, count)


# ── the eight steps: measured, derived, never assigned (#9089 round 5 F3) ─

_FULL_STATS = {
    "edus": 10, "chunks": 1, "entities": 3, "events": 2, "points": 4,
    "search_queries": 5, "search_degraded": False,
    "operator_endpoints_minted": 1,
    "llm": {"by_stage": {"s1": {"p": {"m": {"calls": 1}}},
                          "s2": {"p": {"m": {"calls": 1}}}}},
}

#: A census with the REAL key shape: `per_label` is the WIDER instrument census (it
#: carries `:Object` for `s3_prior_ablation`, see `CENSUS_LABELS`), while `derived_total`
#: is E3's row — `Point` + the link-anchored classes. So the blocks sum to 12 and the
#: total is 11 (#10525 review P2). A fixture whose two halves agree cannot catch a
#: witness that renders one beside the other.
_FULL_CENSUS = {
    "derived_total": 11,
    "per_label": {"Point": {"nodes": 10, "anchored": 8},
                  "Document": {"nodes": 0, "anchored": 0},
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
                 surfaces={"vet": {"enabled": True,
                                    "s2": {"stats": {"candidates": 3}}},
                           "classify_later": {"enabled": True,
                                              "s2": {"kept": 2}}})]
    observed, unobserved = measure.observe_steps(runs, _FULL_CENSUS)
    assert "S2.2" in observed and "S2.2" not in unobserved, observed
    assert "S2.3" in observed and "S2.3" not in unobserved, observed
    assert list(observed) == list(measure.STEPS), observed
    assert not unobserved, unobserved


def test_the_s5_bracket_sums_to_the_total_the_line_names():
    """A witness whose parts sum to a different total than its own is not a witness.

    `derived_total` is E3's row (`Point` + the link-anchored classes). `per_label` is the
    wider instrument census — it carries `:Object` for `tools/s3_prior_ablation.py`'s
    `created_counts` (see `CENSUS_LABELS`) — so rendering EVERY block beside the narrower
    total prints two irreconcilable numbers on one line. That is what this PR's own
    narrowing left behind: the shipped log's S5 line is
    `… 433 … [Point 338, Object 50, Event 45]` (`capstone-measured-e0fc940b.log:26`), and
    after the E3 re-registration the same graph emits
    `… 383 … [Document 0, Event 45, Object 50, Point 338]` — parts summing to 433 beside a
    total of 383, in the bundle whose entire subject is that a number must be carried by
    what backs it.

    Non-vacuity: at the pre-fix head the census below renders
    `11 derived node(s) … [Point 10 (8 anchored), Object 1 (0 anchored), Event 1 (1
    anchored)]` — three labels where E3's set has three but a different one, and parts
    summing to 12 beside a total of 11 — so both the label-set assertion and the sum
    assertion fail there.
    """
    observed, _unobserved = measure.observe_steps([_run()], _FULL_CENSUS)
    line = observed["S5"]
    total = int(re.match(r"the graph write landed: (\d+) derived", line).group(1))
    bracket = re.search(r"\[([^\]]*)\]", line).group(1)
    counts = {label: int(n) for label, n in
              re.findall(r"(\w+) (\d+) \(\d+ anchored\)", bracket)}
    assert set(counts) == set(measure.DERIVED_LABELS), (counts, line)
    assert sum(counts.values()) == total, (
        f"the S5 bracket sums to {sum(counts.values())} beside a total of {total}: {line}")
    # and the class the row excludes is NAMED as excluded, not silently dropped: a reader
    # not told `:Object` is outside the total reads its absence as "the graph held none".
    assert "outside E3's row" in line, line
    outside = line.split("outside E3's row", 1)[1]
    assert "Object 1 (0 anchored)" in outside, line
    assert "Object" not in counts, counts


def test_the_unobserved_s5_names_the_current_class_set():
    """The negative arm carried the RETIRED set long after the positive one moved."""
    empty = {"derived_total": 0, "per_label": {}, "operator_relationships": {},
             "points": 0, "event_stamped_points": 0, "graph_events": 0}
    _observed, unobserved = measure.observe_steps([_run()], empty)
    cause = unobserved["S5"]
    assert "/".join(measure.DERIVED_LABELS) in cause, cause
    assert "Point/Object/Event" not in cause, cause


def test_a_stats_home_for_the_classify_surface_also_counts():
    """The witness may land on the receipt OR in stats; both are the run's own."""
    stats = {**_FULL_STATS, "classify_later": {"enabled": True,
                                              "s2": {"kept": 2}}}
    runs = [_run(stats=stats, stats_keys=sorted(stats),
                 surfaces={"vet": {"enabled": True,
                                    "s2": {"stats": {"candidates": 1}}},
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
    assert _SEAM_SYMBOL not in unobserved["S2.2"], unobserved["S2.2"]


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
        assert _SEAM_SYMBOL not in cause, cause
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
    """The cause is MEASURED, and it names the SEAM that did not reach the run.

    The shipped constant once said "the receipt's stats carries 20 keys" — a
    number nothing counted. The cause must count what it inspected and name the
    thing that is missing. Since #9116 landed, the missing thing is the seam
    itself (``_capture_step_witness``, called at the meta fold), so the cause
    names the SYMBOL: a line cite cannot re-stale, and the helper is what a
    reader can act on now that the fold calls it rather than dropping the block.
    The fold's own line is still re-derived from the tree by the module constant,
    so a lane that moves the call site is told rather than silently misled.
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
    assert _SEAM_SYMBOL in cause, cause
    assert "no receipt carries stats['step_witness']" in cause, cause
    assert "INSTRUMENT gap" in cause, cause
    assert "tortoise/sdk.py:12197" not in cause, cause
    assert "20 keys" not in cause, cause
    assert _SEAM_SYMBOL in unobserved["S2.3"], unobserved["S2.3"]
    # The cite resolves to the PRODUCTION fold — the seam is called from INSIDE
    # `_extract_session_v2`, not from a stray copy elsewhere in the module.
    # (`_sdk_line_naming` above already reds, at IMPORT, if the call site vanishes
    # altogether; this arm is the one that reds when the call MOVES, which that
    # import-time check cannot see.)
    fold_line = int(_META_FOLD_CITE.rsplit(":", 1)[1])
    assert _enclosing_functions(SDK_SOURCE, fold_line) == ["_extract_session_v2"], (
        f"{_META_FOLD_CITE} is not inside _extract_session_v2 — the seam is not called "
        "from the production capture fold")
    # non-vacuity: the same mechanism DISCRIMINATES — the identical call attributed
    # to another function's extent is not `_extract_session_v2`.
    assert _enclosing_functions(
        "def commit():\n"
        "    return _capture_stats_with_step_witness(out, len(extracted))\n", 2
    ) == ["commit"]


def test_the_seam_witnesses_eight_of_eight_with_no_probe_edit_to_the_rule():
    """#9116's acceptance in the MEASUREMENT lane: the seam makes S2.2/S2.3 real.

    The pre-fix lane could witness 6 of 8 and had to say so. This builds the
    ``stats`` exactly as the SDK now does — by calling the production
    ``_capture_step_witness``, not by hand — and requires 8/8 with the witness
    read for the two steps that were missing.
    """
    from tortoise.sdk import _capture_stats_with_step_witness

    out = {
        "stats": {**_FULL_STATS, "search_queries": 15,
                  "search_degraded": False},
        "story_arc": "A narrative.",
        "s2_embed": {"entities": [1], "events": [1], "points": [1],
                     "operators": []},
        # The real VET surface shape (``_run_vet_pass``): counts under
        # ``stats``, the raw per-candidate ``decisions`` beside it. The seam
        # forwards the counts only (#10214 review, P1).
        "vet": {"enabled": True,
                "s2": {"stats": {"candidates": 3, "discarded": 0},
                       "decisions": {"pt_0": {"text": "a candidate"}}},
                "union": {}},
        "classify_later": {"enabled": True, "s2": {"assigned": 3},
                           "union": {}},
        "search": {"mode": "docker", "degraded": False, "queries_run": 15,
                   "entities": [], "points": [], "events": []},
        "payload": {"entities": [1], "events": [1], "points": [2],
                    "operators": [1]},
    }
    stats = _capture_stats_with_step_witness(out, 2)
    surfaces = {"vet": None, "classify_later": None}
    runs = [_run(stats=stats, stats_keys=sorted(stats), surfaces=surfaces,
                 receipt_keys=["ok", "stats"])]
    observed, unobserved = measure.observe_steps(runs, _FULL_CENSUS)
    assert list(observed) == list(measure.STEPS), (
        sorted(measure.STEPS) and [s for s in measure.STEPS if s not in observed])
    assert not unobserved, unobserved
    # and the two that needed the seam name WHERE they found it
    assert "stats['step_witness']['vet']" in observed["S2.2"], observed["S2.2"]
    assert "stats['step_witness']['classify_later']" in observed["S2.3"], (
        observed["S2.3"])


def test_the_seam_present_but_incomplete_is_a_seam_defect_not_a_pass_finding():
    """Three states, three causes: absent, present-but-incomplete, present-and-off.

    A seam on the receipt that does not carry its block is a defect in
    ``_capture_step_witness``; blaming the pass would send the next lane to the
    extractor, and blaming the instrument (as the absent arm does) would hide a
    half-landed seam behind the same words.
    """
    stats = {**_FULL_STATS, "search_queries": 5, "step_witness": {"map": {}}}
    runs = [_run(stats=stats, stats_keys=sorted(stats),
                 receipt_keys=["ok", "stats"])]
    observed, unobserved = measure.observe_steps(runs, _FULL_CENSUS)
    assert "S2.2" not in observed
    cause = unobserved["S2.2"]
    assert "INCOMPLETE" in cause, cause
    assert _SEAM_SYMBOL in cause, cause
    assert "no receipt carries" not in cause, cause
    assert "did NOT run" not in cause, cause



def test_every_step_is_witnessed_or_given_a_cause():
    """The count is derived from two total, disjoint sets — never an assignment."""
    observed, unobserved = measure.observe_steps([_run()], {})
    assert set(observed) | set(unobserved) == set(measure.STEPS)
    assert not set(observed) & set(unobserved)


# ── the conditional step, ACCOUNTED on the SCORING path (#10215 review P1) ──
#
# `capstone_run.py`'s G5 learned #10215's rule; THIS probe — the one that produces
# the E1–E5 numbers — kept emitting neither `steps_required` nor
# `steps_conditional`, so V5 read every log it writes as the pre-#10215 "all eight
# required" shape and refused a GREEN row for S2.3. The restatement reached one of
# the two places its logs come from, which is why #10215's own symptom was still
# live on the scoring path.

_RECORD_ARTIFACT = "docs/evidence/7949-capstone/preregistration.json"


def _idle_run(session_id: str = "wp01_quarry_debug", **overrides) -> dict:
    """One measured session holding the classify surface ENABLED with no work.

    Carries a POSITIVE candidate count on the receipt's own witness, because the
    idle predicates require it: an empty candidate list skips the pass's `try` and
    leaves the same `enabled=true`/`s2={}`/`union={}` shape, which is why
    `observe_idle` has four predicates rather than three.
    """
    run = _run(session_id=session_id,
               stats={**_FULL_STATS,
                      "step_witness": {"map": {"items": {"entities": 4,
                                                          "events": 1}}}},
               surfaces={"classify_later": {"enabled": True, "s2": {},
                                            "union": {}}})
    run.update(overrides)
    return run


def _with_head() -> str:
    """The commit under test — `_record_conditional` reads the record FROM git.

    Derived, never typed: the gate resolves the frozen `steps` block out of
    ``git show <sha>:<artifact>``, so this arm only has teeth against a real
    commit, and a typed one would re-stale on the next push.
    """
    return cvc._git(_REPO, "rev-parse", "HEAD").stdout.strip()


def _seven_observed() -> dict:
    """What a post-seam run witnesses on this corpus: seven, S2.3 absent."""
    return {step: f"witnessed ({step})" for step in measure.STEPS if step != "S2.3"}


def test_the_scoring_probe_emits_the_split_v5_reads_and_the_old_shape_is_what_reds():
    """The finding, end to end: a GREEN row's step half now passes V5.

    Non-vacuity is the second half of this test, and it IS the finding: the same
    seven observed steps under the PRE-FIX run block — no `steps_required`, no
    `steps_conditional` — are refused by the shipped gate for exactly the reason
    #10215 was filed about. Asserted, not described, so the arm cannot regress to
    "the gate says it accepts the accounting" on this probe's logs.
    """
    required, accounts, gap = measure.step_accounting(
        measure.read_conditional(_prereg()), [_idle_run()], _seven_observed())
    assert gap == "", gap
    assert required == [s for s in cvc.STEPS if s != "S2.3"], required
    assert [entry["step"] for entry in accounts] == ["S2.3"], accounts
    assert accounts[0]["state"] == "idle", accounts[0]
    assert "EMPTY ITEM SET" in accounts[0]["basis"], accounts[0]

    run = {"status": "COMPLETED", "steps_required": required,
           "steps_conditional": accounts,
           "steps_observed": [s for s in cvc.STEPS if s != "S2.3"]}
    assert cvc._step_accounting(_REPO, run, tuple(run["steps_observed"]),
                                _with_head(), _RECORD_ARTIFACT) == []
    # ... and the PRE-FIX shape of the same run, which is what this probe emitted:
    prefix = {"status": "COMPLETED",
              "steps_observed": [s for s in cvc.STEPS if s != "S2.3"]}
    assert [(code, msg.split(" — ")[0]) for code, msg in
            cvc._step_accounting(_REPO, prefix, tuple(prefix["steps_observed"]),
                                 _with_head(), _RECORD_ARTIFACT)] == [
        ("V5", "GREEN with steps not observed in the run: S2.3")]


def test_the_log_prints_the_split_it_emitted_and_the_gap_when_it_could_not():
    """The rendering arm, not just the record: a reader sees the same claim V5 reads.

    A log that prints only "6 of 8" hands its reader the pre-#10215 reading of the
    same numbers. An unaccounted conditional step must print the FALLBACK it
    actually emitted, or the log and the machine block disagree about the table.
    """
    conditional = measure.read_conditional(_prereg())
    required, accounts, gap = measure.step_accounting(
        conditional, [_idle_run()], _seven_observed())
    lines = measure.step_accounting_lines(required, accounts, gap)
    assert lines[0].startswith("steps required: S0 S1 S2.1 S2.2 S3 S5 S6"), lines
    assert lines[0].endswith("|  conditional: S2.3=idle"), lines
    assert lines[1].startswith("conditional S2.3: idle — every one of the 1 "), lines
    assert len(lines) == 2, lines
    # the refusal side: all eight required, the step named with its own cause
    required, accounts, gap = measure.step_accounting(
        conditional, [_idle_run(errors=["classify(S2) failed: boom"])],
        _seven_observed())
    lines = measure.step_accounting_lines(required, accounts, gap)
    assert lines[0].startswith("steps required: " + " ".join(cvc.STEPS))
    assert lines[0].endswith("|  conditional: none"), lines
    assert lines[1].startswith("steps accounting: NOT CLAIMED — "), lines
    assert "neither observed nor witnessed idle: S2.3" in lines[1], lines


def test_the_split_is_the_frozen_records_not_this_probes_choice():
    """Direction matters: a step is conditional because the RECORD says so.

    A probe that decided its own conditional set could hand itself an accounting it
    was never granted, and `capstone_run.G5` refuses exactly that. A malformed
    declaration refuses the RUN too — degrading it to "all eight required" would
    look identical to correct behaviour.
    """
    # the real frozen record declares S2.3 conditional
    assert set(measure.read_conditional(_prereg())) == {"S2.3"}
    # a record that declares NOTHING conditional leaves all eight required
    assert measure.read_conditional({}) == {}
    required, accounts, gap = measure.step_accounting({}, [_idle_run()],
                                                     _seven_observed())
    assert required == list(cvc.STEPS) and accounts == [] and gap == ""
    # a malformed declaration is a REFUSAL, never a silent "all eight"
    for broken in ({"steps": []},
                   {"steps": {"conditional": "S2.3"}},
                   {"steps": {"conditional": [{"step": "S4"}]}}):
        try:
            measure.read_conditional(broken)
        except SystemExit as exc:
            assert "unreadable" in str(exc), exc
            continue
        raise AssertionError(f"{broken}: a malformed declaration did not refuse")


def test_a_failed_classify_pass_is_never_accounted_idle_by_this_probe():
    """The fail-open `except` leaves the SAME empty counters as an idle pass.

    The receipt says which one it was in `errors` / `diagnostics` (the raw string
    rides `diagnostics` on the real receipt), so a crashed pass must fall back to
    the pre-#10215 shape instead of handing the run an idle basis. Without this arm
    `#10215`'s restatement would make a CRASH look like "nothing to do".
    """
    for failure in ({"errors": ["classify(S2) failed: RuntimeError: boom"]},
                    {"diagnostics": ["classify(union) failed: TimeoutError: slow"]},
                    {"warnings": ["classify-later init failed"]}):
        required, accounts, gap = measure.step_accounting(
            measure.read_conditional(_prereg()), [_idle_run(**failure)],
            _seven_observed())
        assert required == list(cvc.STEPS), (failure, required)
        assert accounts == [], (failure, accounts)
        assert "neither observed nor witnessed idle: S2.3" in gap, gap


def test_an_idle_claim_is_run_wide_so_one_session_can_refute_it():
    """Seven sessions, one number: an idle step must be idle on ALL of them.

    `observe_idle` grades a single receipt, and this probe measures a corpus. One
    session's idle block beside another's flag-off (or zero-candidate, or failed)
    block is not a run-wide idle — and the fallback is the strict direction: the
    gate then refuses the step for the fact that is true.
    """
    conditional = measure.read_conditional(_prereg())
    idle = _idle_run("wp01_quarry_debug")
    # two idle sessions: accounted, and the basis names the surface it read
    required, accounts, gap = measure.step_accounting(
        conditional, [idle, _idle_run("wp03_ember_design")], _seven_observed())
    assert gap == "" and accounts[0]["state"] == "idle", (gap, accounts)
    assert "2 measured session(s)" in accounts[0]["basis"], accounts[0]["basis"]
    # one flag-off session in the run refutes it
    flag_off = _idle_run("wp05_retro_writeup",
                         surfaces={"classify_later": {"enabled": False,
                                                      "s2": {}, "union": {}}})
    required, accounts, gap = measure.step_accounting(
        conditional, [idle, flag_off], _seven_observed())
    assert accounts == [] and required == list(cvc.STEPS), accounts
    assert "wp05_retro_writeup" in gap, gap
    # a session with NO surface at all is the #9116 gap, not a contradiction, and
    # does not by itself make the run idle either
    silent = _run(session_id="wp06_bare")
    required, accounts, gap = measure.step_accounting(
        conditional, [silent], _seven_observed())
    assert accounts == [] and "INSTRUMENT gap" in gap, (accounts, gap)
    # an OBSERVED conditional step is accounted as observed, never as idle
    observed = dict(_seven_observed(), **{"S2.3": "the pass assigned 3 kinds"})
    required, accounts, gap = measure.step_accounting(conditional, [idle], observed)
    assert gap == "" and accounts == [{"step": "S2.3", "state": "observed",
                                       "basis": "the pass assigned 3 kinds"}], accounts


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
                     "BY CONSTRUCTION", measure.TURN_WRITE_CITE,
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


def test_the_e3_narration_never_claims_the_amended_denominator_counts_object():
    """#10525 review P2, MEASURED: the instrument kept printing the claim this PR falsifies.

    §10.1 re-registered E3's class set to `Point` + `Event` + `Document`, so the frozen denominator
    NO LONGER counts `:Object`. The SET was moved (`DERIVED_LABELS` follows
    `_DERIVATION_REFERENCES_LABELS`); the NARRATION of the set was not, and it is EMITTED —
    `e3_row_note` into both copies of the log's E3 row, `point_composition_line` into the
    `e3 point composition:` line. So every future scored run would have written, beside the amended
    total, prose asserting the denominator counts `:Object` and that no choice had been made.

    Three sentences carried it:

    * ``the frozen denominator counts them anyway … a lane cannot pick`` (`e3_row_note`)
    * ``#9736 must choose between re-registering the metric and reopening the model``
      (`e3_row_note`) — the choice §10.1 makes, under #9736's own ruling
    * ``The frozen 'all derived nodes' reading above is unchanged`` (`point_composition_line`) —
      the reading MOVED, which is the whole point of the amendment

    Non-vacuity: on the pre-fix probe every fragment in `falsified` is PRESENT in both rendered
    strings, so each `not in` below reds; and ``grep`` over the two test files returned NOTHING for
    any of them, which is why no pin caught it (the existing E3-note pins hold the SPLICE defect
    and the two log copies, both orthogonal to these sentences).
    """
    graph, corpus_sha = "capstone_measure_test_1", "sha256:" + "e" * 64
    note = measure.e3_row_note(composition=_shipped_composition(),
                               e3_result=_e3_result(), graph=graph, corpus_sha=corpus_sha)
    line = measure.point_composition_line(_shipped_composition())
    falsified = ("counts them anyway", "a lane cannot pick", "must choose between",
                 "frozen 'all derived nodes' reading")
    for where, rendered in (("e3_row_note", note), ("point_composition_line", line)):
        for fragment in falsified:
            assert fragment not in rendered, (where, fragment, rendered)

    # Both readings are still reported — that part of the narration was right and must survive —
    # and the amendment is named where a reader can follow it, rather than asserted as a fact the
    # owner has not ruled on.
    assert "BOTH readings" in note, note
    assert "§10.1" in note, note
    assert "no longer count" in note, note
    assert "PRE-AMENDMENT" in line, line
    assert "§10.1" in line, line
    assert "no longer counts" in line, line
    # and the splice guard this note has always had stays green on the rewritten text
    assert not re.search(r"\d+/\d+ = 1\.000000", note), note

    # ⛔ #10525 review ROUND 2, P1 — MEASURED: two of these sentences asserted their own arithmetic
    # falsely. The 'all derived nodes' figure had been called "the PRE-AMENDMENT reading" and said to
    # be WIDER than "the figure above", while after the amendment that figure IS the registered one
    # (383 = Point 338 + Event 45 + Document 0) — so the line called 383 pre-amendment and then said
    # the registered reading is narrower than 383. And the row claimed to report "BOTH readings"
    # while its value carries only `148/383` (`"433" in note` is False). Both are now stated where
    # the pre-amendment figure actually lives — §10.1's table — and the claims are pinned as the
    # true ones, not as fragments that merely rhyme with them.
    assert "narrower than the figure above" not in line, line
    assert "PRE-AMENDMENT reading and is retained" not in line, line
    assert "this row reports BOTH readings" not in note, note
    assert "which §9's provenance table states" in line, line
    assert "§10.1 reports BOTH readings" in note, note


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


# ── the bundle's STATE LEDGER must describe the seam that SHIPPED ──────────
#
# #10214 review (P2): the two documents this PR edits carried FIVE present-tense
# statements describing the PRE-seam receipt — "carries no step surface (#9116)",
# "carries none (#9116) … refuses at G5 for **every** input and its table is 0/5",
# "landing #9116 flips both rows with no probe edit", and the two state rows still
# marked **OPEN** — while the SAME diff shipped the seam and a pin asserting G5 can
# now COMPLETE (`test_a_completed_run_is_reachable_now_that_the_seam_landed`). A
# reader who trusts them re-runs the probe expecting 8/8, gets 7/8, and
# mis-attributes it — exactly the misattribution the G5 three-state split exists to
# prevent. The bundle sweeps its `file:line` cites and pins its vocabulary
# (REFUSED is not RED), but nothing read its STATE, which is why the five survived
# ten review rounds and a green CI.

README_DOC = _REPO / "docs/evidence/7949-capstone/README.md"
E1E5_PROOFS = _REPO / "docs/evidence/7949-capstone/E1-E5-PROOFS.md"

#: Present-tense shapes that assert the PRE-seam receipt as the CURRENT state.
#: The PAST-tense forms ("it refused at G5 for every input", "carried none") stay
#: allowed on purpose: the seam's absence IS what the frozen log measured, and
#: deleting that history would delete the reason the measurement probe exists.
_BUNDLE_PRE_SEAM_PRESENT_TENSE = (
    "carries no step surface",
    "carries none (#9116)",
    "flips both rows with no probe edit",
    "refuses at G5 for **every** input",
)


def _state_row(text: str, heading: str, issue: str) -> str:
    """The one row of `heading`'s table that tracks `issue`'s own state.

    Scoped to the section, then to the ROW, because these two documents carry
    several tables: `#9116` also appears in the eight-step table (its witness
    column, which is not a state) and in the closure table's prose.
    """
    section = text.split(heading, 1)[1].split("\n## ", 1)[0]
    lines = section.splitlines()
    row = None
    for index, line in enumerate(lines):
        if not line.lstrip().startswith("|") or f"`{issue}`" not in line:
            continue
        # The table's HEADER may name the issue too ("the `#9116` row re-read at
        # this PR's head"); a header is the row whose successor is the `|---|`
        # separator, so it is not the datum — skip it and keep looking.
        following = next((ln for ln in lines[index + 1:] if ln.strip()), "")
        if re.match(r"\|\s*:?-{3,}", following):
            continue
        row = line
        break
    assert row is not None, (
        f"{heading!r} no longer tracks {issue}: the state ledger a lane reads "
        "was deleted rather than corrected")
    return row


def test_the_bundle_does_not_state_the_pre_seam_receipt_as_the_current_one():
    """A lane reading this bundle must be told the receipt HAS the step surface.

    Three assertions, because the failure had three shapes: (1) prose that still
    says the receipt "carries no step surface" in the present tense; (2) the
    README's dependency table marking `#9116` **OPEN** after this PR closes it;
    and (3) the E-row audit's closure table doing the same, 28 lines after the
    same file states the seam "has since LANDED" — a document contradicting
    itself on the one question it exists to answer.

    The claim is bound to the CODE, not just to the prose: if `sdk.py`'s meta fold
    stops calling `_capture_stats_with_step_witness`, the bundle is wrong again
    and this reds with it, so the two cannot drift apart silently.

    Non-vacuity: at the pre-fix head both documents carry "carries no step
    surface" (README :104 and :568), "carries none (#9116)" (:104),
    "flips both rows with no probe edit" (:570), and both state rows read
    "**OPEN**" (README's Dependency-state row, E1-E5-PROOFS :210), so every
    assertion below reds there.
    """
    readme = README_DOC.read_text()
    proofs = E1E5_PROOFS.read_text()

    for name, text in (("README.md", readme), ("E1-E5-PROOFS.md", proofs)):
        for phrase in _BUNDLE_PRE_SEAM_PRESENT_TENSE:
            assert phrase not in text, (
                f"{name} still states the pre-seam receipt as the current one "
                f"({phrase!r}); `#9116`'s seam shipped in this PR, so the receipt "
                "carries `stats['step_witness']` — say that, and keep the frozen "
                "log's 6/8 as the HISTORY it is")

    for doc, text, heading in (
            ("README.md", readme, "## Dependency state"),
            ("E1-E5-PROOFS.md", proofs, "## Honest closure verdict")):
        row = _state_row(text, heading, "#9116")
        assert "**OPEN**" not in row, (
            f"{doc}'s {heading.lstrip('# ')!r} row still reports `#9116` as OPEN; "
            "this PR lands it (its body says `Fixes #9116`), so the ledger a future "
            "lane reads would send it to re-derive a seam that is already here")
        assert "LANDED" in row, (
            f"{doc}'s {heading.lstrip('# ')!r} row must name `#9116` as LANDED and "
            "say what still bounds the run (S2.3's idle classify pass, and V5's "
            f"8/8 scoping); it reads: {row}")

    # The prose is only allowed to claim this because the fold really does it.
    sdk = (_REPO / "tortoise" / "sdk.py").read_text()
    assert '"stats": _capture_stats_with_step_witness(' in sdk, (
        "the bundle now names `#9116` as LANDED, but `_extract_session_v2`'s meta "
        "fold does not call `_capture_stats_with_step_witness`: the seam is gone, "
        "so the documents are lying again — restore the fold or the pre-seam text")


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

# The GATE the bundle audits, which the bundle also cites by number. Kept OUT of
# `_BUNDLE_SOURCES` on purpose: `_BUNDLE_CITE` matches `sdk|edges|extractor_v2`
# only, so putting a key here would imply the sweep proper reads it when it
# cannot. These are read by `test_the_bare_continuation_cites_name_what_they_say`
# instead, by phrase — the same mechanism the bare `:NNNN` continuations need.
_BUNDLE_TOOL_SOURCES = {
    "capstone_verdict_check": _REPO / "tools/capstone_verdict_check.py",
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
    # The fold that now CALLS the #9116 seam. Before the seam landed this rule
    # named `"stats": out.get("stats")` (the line that dropped the block); the
    # cite is the same fold and a different claim, so the anchors moved with it.
    ("the meta fold that calls the #9116 seam", "sdk",
     ('"stats": _capture_stats_with_step_witness', "meta fold", "same fold"),
     ('"stats": _capture_stats_with_step_witness(out, len(extracted))',),
     "line", None),
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
    fold cite to `12401` reds it, and before this leg nothing read that row.
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


def test_the_bare_continuation_cites_name_what_they_say():
    """A BARE `:NNNN` continuation is invisible to the sweep above — pin it here.

    `_BUNDLE_CITE` requires a cite to NAME ITS FILE, so the docs' house style —
    `` `extractor_v2.py:9056` … `:9060` ``, where the file is named once for
    several numbers — produces cites the sweep neither resolves NOR reports
    `unmatched`. They are simply invisible, which is how SIX wrong numbers
    survived three review rounds in a bundle whose thesis is "a claim nothing
    re-checks can rot silently" (#5095 review round 3, finding 1: three in
    `README.md`, three in `docs/00_index.md` + this probe, the last three a bare
    `+50` bump over sites whose true shift was `+192`/`+199`).

    Each row states the document, a phrase that must sit on ONE line of it, and,
    per cited number, what `tortoise/extractor_v2.py` must read there. The phrase
    is what makes this a claim about the SENTENCE rather than about a markdown
    line number (which would rot exactly as fast as the cites do), and it must
    occur exactly once in the document — an ambiguous phrase reds instead of
    silently checking the easier of two matches.

    Non-vacuity: measured on this head's parent, where `docs/00_index.md` said
    `whose call is :9136` and this probe said `:9426-9430` for the vet block,
    `:9044` for the early return and `:8781` for the `_edus_from_conversation`
    call: every one of those rows FAILS the two checks below (the number is not
    on the phrase's line, and the old number carries other code).

    A row may name a SOURCE outside the bundle's own three (`_BUNDLE_TOOL_SOURCES`)
    — the `E1-E5-PROOFS.md` rows read `tools/capstone_verdict_check.py`, which
    `_BUNDLE_CITE` cannot match at all. Those numbers had NO reader: the document
    said so in its own words ("re-checked BY HAND at the audit head") and then
    went stale within one commit — `#10509` review (P2) found its required-step
    refusal cited at the lines rule 4 (the frozen-record refusal) occupies. On
    that pre-fix document the third row FAILS twice over: its sentence carries
    neither `:475` nor `:477`, and `:460`/`:462` carry the conditional-declaration
    refusal, not the `missing = …` line the prose names.
    """
    rows = (
        # `each VET pass is gated on the LIST` — the gate AND its call, twice.
        ("docs/00_index.md", "each VET pass is gated on the LIST", (
            ("10295", "if vet_enabled and embed_list:"),
            ("10296", "vet_s2_stats, _w, vet_s2_pool = _run_vet_pass("),
            ("10417", "if vet_enabled and complete_list:"),
            ("10418", "vet_union_stats, _w, _ = _run_vet_pass("),
        )),
        # the VET block's start; its three lines of keys (10587-10589) close on
        # line 10590 — DERIVED by content, not by arithmetic: `result["vet"] = {`
        # opens at 10586 and the block's `}` is 10590. ⚠️ The base head's number
        # was 9446, which pointed at an unrelated comment about
        # `_value_verbatim_span`, so a mechanical +1101 lands on `try:`; a COMMENT
        # is not an asserted number and nothing reds on it, which is why the two
        # sides' bump disagreed here and why every ASSERTED number in this table
        # is verified by content instead.
        ("docs/00_index.md", "emitted unconditionally at", (
            ("10576", 'result["classify_later"] = {'),
            ("10586", 'result["vet"] = {'),
        )),
        # `_IDLE_CITES["vet"]`: the early return and the surface it emits
        ("probes/capstone_measure.py", "its vet surface", (
            ("10204", "if not edus:"),
            ("10220", '"vet": {"enabled": vet_enabled, "s2": {}, "union": {}},'),
        )),
        # ...and the same claim in the rule's own docstring
        ("probes/capstone_measure.py", "with the flag ON", (
            ("10220", '"vet": {"enabled": vet_enabled, "s2": {}, "union": {}},'),
        )),
        ("probes/capstone_measure.py", "follows at", (
            ("10203", "edus = _edus_from_conversation(conversation)"),
        )),
        # the S2.2/S2.3 block this probe names as the #9116 seam's subject
        ("probes/capstone_measure.py", 'result["vet"]`` at', (
            ("10586", 'result["vet"] = {'),
            ("10590", ("exact", "}")),
        )),
        # The E-row audit's cites on the GATE ITSELF. `_BUNDLE_CITE` matches only
        # `sdk|edges|extractor_v2`, so a `tools/*.py` number is invisible to the
        # sweep proper — and until these rows existed the document said so in its
        # own words ("re-checked BY HAND at the audit head"). It then went stale
        # in the very commit whose message claimed to fix a stale V5 cite pair:
        # `#10509` review (P2) found `:460`/`:462` landing on rule 4, the
        # frozen-record refusal, instead of rule 2, the unobserved-required-step
        # refusal. A hand-recheck did not hold across one commit, so the numbers
        # are pinned here instead. The sentence wraps over three lines and a row
        # scopes to ONE line, so the claim takes three rows.
        ("E1-E5-PROOFS.md", 'declares `STEPS = ("S0"', (
            ("118", 'STEPS = ("S0", "S1", "S2.1", "S2.2", "S2.3", "S3", '
                    '"S5", "S6")'),
        ), "capstone_verdict_check"),
        ("E1-E5-PROOFS.md", "_step_accounting` (`:379`) refuses a GREEN row whose", (
            ("379", "def _step_accounting("),
        ), "capstone_verdict_check"),
    )
    # The SAME document states two of its claims as the RULE's own text rather than a
    # line number — a quoted rule cannot drift by an insertion above it, which is why
    # #10215's review moved those two numbers twice inside one PR. A quote still rots
    # if the gate renames the message, so each quote is held against the gate's text
    # as well: the doc must carry it (markdown wraps it, so whitespace is normalised)
    # and the gate must still say it.
    quotes = (
        ("E1-E5-PROOFS.md",
         "missing = [s for s in required if s not in observed]",
         "capstone_verdict_check"),
        ("E1-E5-PROOFS.md",
         "GREEN with steps not observed in the run:",
         "capstone_verdict_check"),
    )
    docs = dict(_BUNDLE_DOCS)
    sources = {name: path.read_text(encoding="utf-8").splitlines()
               for name, path in (*_BUNDLE_SOURCES.items(),
                                  *_BUNDLE_TOOL_SOURCES.items())}
    failures: list[str] = []
    for row in rows:
        doc_label, phrase, cites = row[0], row[1], row[2]
        # a row may name its SOURCE; the bundle's own three are the default
        source_key = row[3] if len(row) > 3 else "extractor_v2"
        source_label = f"{source_key}.py"
        lines = docs[doc_label].read_text(encoding="utf-8").splitlines()
        hits = [ln for ln in lines if phrase in ln]
        if len(hits) != 1:
            failures.append(
                f"{doc_label}: the scoping phrase {phrase!r} occurs {len(hits)} "
                f"time(s) — this pin needs exactly one line to scope the cite "
                f"to, so re-word the pin's phrase rather than letting it bind "
                f"the easier of two matches")
            continue
        line = hits[0]
        for number, anchor in cites:
            if f":{number}" not in line and f"-{number}" not in line:
                failures.append(
                    f"{doc_label}: the sentence containing {phrase!r} does not "
                    f"cite :{number} — a bare continuation is invisible to "
                    f"_BUNDLE_CITE, so this pin is the only thing reading it")
                continue
            got = sources[source_key][int(number) - 1]
            if isinstance(anchor, tuple):
                if got.strip() != anchor[1]:
                    failures.append(
                        f"{source_label}:{number} (cited by {doc_label}) reads "
                        f"{got.strip()!r}, not exactly {anchor[1]!r}")
            elif anchor not in got:
                failures.append(
                    f"{source_label}:{number} (cited by {doc_label}) reads "
                    f"{got.strip()!r}, which carries no {anchor!r}")
    for doc_label, quoted, source_key in quotes:
        source_label = f"{source_key}.py"
        doc_text = " ".join(docs[doc_label].read_text(encoding="utf-8").split())
        if " ".join(quoted.split()) not in doc_text:
            failures.append(
                f"{doc_label} no longer carries the rule text {quoted!r} this pin "
                f"holds {source_label} to — re-quote the rule from the gate rather "
                f"than dropping the claim")
        if not any(quoted in ln for ln in sources[source_key]):
            failures.append(
                f"{doc_label} quotes {quoted!r} as the rule, but {source_label} no "
                f"longer says it — re-quote from the gate, do not leave a quote the "
                f"code has moved past")
    assert not failures, (
        "a bare `:NNNN` continuation names code it does not name — re-derive the "
        "number from the tree before changing this pin:\n  " + "\n  ".join(failures))


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
        "| **`#7949` / `#9080` capstone — the closing rule** — the meta fold "
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
    # line 3: the meta fold that CALLS the #9116 seam (it used to be the line
    # that dropped the block — the rule's anchors moved with the fix)
    sdk[2] = ('            "stats": _capture_stats_with_step_witness('
              'out, len(extracted)),')
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


#: A BARE line cite — `` `:NNNN` `` / `` `:NNNN-NNNN` `` with no file in front of
#: it. `_BUNDLE_CITE` cannot match it (a bare number names no tree to look in), so
#: the sweep below cannot resolve it: that is the blind spot this leg covers from
#: the other side, and it is how 20 bare cites rotted at `7b6b2180f` — the commit
#: set out to re-derive this bundle's cites, re-derived the ones with a `file.py:`
#: prefix, and left the continuations pointing at unrelated code while the cite
#: gate stayed green.
_BARE_CITE = re.compile(r"(?<![\w:./])`(:\d+(?:-\d+)?)`")

#: The documents whose prose prints the per-surface numbers. `E1-E5-PROOFS.md`,
#: the probes and `EXTRACTOR-V4-ARCHITECTURE.md` do not restate the flag/gate set,
#: so they are not this leg's business (their own cites are swept above).
_BARE_CITE_DOCS = (
    ("README.md", _REPO / "docs/evidence/7949-capstone/README.md"),
    ("docs/00_index.md", _REPO / "docs/00_index.md"),
)


def _cited_spans(doc_name: str, path: Path) -> set[str]:
    """Every `:N` / `:N-M` this bundle's rows cite, bare OR prefixed.

    Prefixed cites are normalised to the same ``:N`` shape so one assertion can
    read both: the prose writes `extractor_v2.py:8683` and `:8703` in the same
    sentence, and the question here is only WHICH LINES the document names.
    """
    text = path.read_text(encoding="utf-8")
    rows = text.splitlines()
    spans: set[str] = set()

    def owned(pos: int) -> bool:
        return _bundle_in_scope(doc_name, rows[text.count("\n", 0, pos)])

    for m in _BUNDLE_CITE.finditer(text):
        if owned(m.start()):
            spans.add(":" + m.group(2) + ("-" + m.group(3) if m.group(3) else ""))
    for m in _BARE_CITE.finditer(text):
        if owned(m.start()):
            spans.add(m.group(1))
    return spans


def test_the_per_surface_prose_cites_are_the_probe_tables_own_numbers():
    """The S2.2/S2.3 prose prints the numbers `_EXTRACTOR_SITES` DERIVES — or reds.

    `README.md` states the rule this pins: *"The cites now live in ONE table keyed
    by the surface (`_IDLE_CITES`)"*. That claim is only true if moving a gate in
    `extractor_v2.py` moves the prose with it, and nothing enforced it: the table
    was re-derived for the flag-on cause while the paragraph around it kept the
    pre-insertion numbers, so the bundle argued from `vet_enabled` at `:8683` and
    then cited `:8684`/`:8806` for gates that had moved to `:8771`/`:8893`.

    Each site is read out of the TREE by `_EXTRACTOR_SITES` (`_extractor_cite` /
    `_extractor_items_gate`), so this is a cross-artifact check between two things
    that must agree — never a third typed table. Non-vacuity, measured at
    `7b6b2180f`: the pre-fix README carried `:8565`/`:8616`/`:8684`/`:8806` and
    `:8965`/`:8975`, and this leg reds on every one of them, because the derived
    numbers (`:8652`, `:8703`, `:8771`, `:8893`, `:9052`, `:9062`) appear nowhere
    in it. It also reds if a site is cited by its RANGE START only while the
    document prints the wrong half of the span, which is how `:8478-8481` (a
    docstring) survived a `:8562-8565` (the fail-open `except`) re-derivation.
    """
    for surface, sites in _EXTRACTOR_SITES.items():
        for name, site in sites.items():
            if name == "init_fail":
                # named only by `_IDLE_CITES`, which `_EXTRACTOR_SITES` pins
                # directly; the prose never prints this one.
                continue
            start = site.split("-", 1)[0]
            for doc_name, path in _BARE_CITE_DOCS:
                spans = _cited_spans(doc_name, path)
                # a single-line site is cited as-is; a range is cited either
                # whole or by its start line (`:9052` for `:9052-9058`)
                assert site in spans or start in spans, (
                    f"{doc_name} does not cite `{site}` "
                    f"(`{surface}.{name}`), which extractor_v2.py carries at "
                    f"{site} — the prose's per-surface numbers and the probe's "
                    f"tree-derived table have drifted apart. Re-derive the "
                    f"document's cites; do not relax this leg. "
                    f"cited spans: {sorted(spans)}")


#: The documents that carry §11's BY-DESIGN argument and are read by NO sweep:
#: `_BUNDLE_DOCS` covers README / E1-E5-PROOFS / the probes / the index / the
#: architecture doc, and `_BARE_CITE_DOCS` only the two that print the per-surface
#: numbers. That gap was measured: `#10513`/`#10360` inserted 1,101 lines into
#: `tortoise/extractor_v2.py`, the foreward merge re-pointed 99 cites in four
#: bundle files — and left these two behind, so §11 argued its whole "not a
#: relaxation" case from `:8666` (payload-event assembly) and `:9209` (a
#: `refused_value_contents` lookup) while `README.md` carried the correct derived
#: `:10310`/`:10442` two files over, i.e. ONE PR shipping two documents that
#: disagree about where its own load-bearing code lives. The record is the
#: hash-pinned artifact the gate revises itself by (`RECORD_ARTIFACT`), so a wrong
#: cite there is inside the one file the epic treats as change-controlled.
_PREREG_STEP_CITE_DOCS = (
    ("PRE-REGISTRATION.md",
     _REPO / "docs/evidence/7949-capstone/PRE-REGISTRATION.md"),
    ("preregistration.json",
     _REPO / "docs/evidence/7949-capstone/preregistration.json"),
)


def test_the_pre_registration_cites_the_classify_sites_the_tree_actually_has():
    """§11's five code sites are DERIVED from the tree, never typed twice.

    A typed number can only be repaired by another hand re-derivation — the class
    this bundle has already filed twice (`#10377`, `#5095`) and re-derived by hand
    once more at `68b2e617d`. The numbers here come out of `_extractor_lineno` /
    `_EXTRACTOR_SITES`, the same readers the rest of this file resolves cites with,
    so moving the classifier in `extractor_v2.py` moves the document with it.

    The two FAIL-OPEN `except` cites are here because they were the pair the earlier
    re-derivations left behind while every neighbour moved: `#10513`/`#10360`'s
    +1,101-line insert carried the collector and both `if items:` gates forward and
    left `:9223`/`:9353` (comment lines at the head) in the sentence that argues why
    an idle step is distinguishable from a crashed one — the sentence this section's
    whole honesty case rests on. Nothing read them, so nothing red-ed.

    Non-vacuity: at `68b2e617d` the pre-registration cited `:8666`/`:8666-8690`/
    `:9209`/`:9341` while the tree carried `:9767`/`:9767-9789`/`:10310`/`:10442`,
    and at `c3612c299` it still cited `:9223`/`:9353` for the fail-open `except`
    arms the tree carries at `:10323`/`:10453` — this leg reds on every one of them.
    """
    collector = _extractor_lineno("def _collect_classify_items(")
    # The collector's EXTENT is read, not assumed: the cited end is a claim too, and
    # the pre-#10215 range ran two lines past `return items` into the blank lines
    # before the next `def`. `ast` is what knows where a function ends.
    end = next(
        node.end_lineno for node in ast.walk(
            ast.parse((_REPO / _EXTRACTOR).read_text()))
        if isinstance(node, ast.FunctionDef)
        and node.name == "_collect_classify_items")
    gates = (_EXTRACTOR_SITES["classify_later"]["items_s2"],
             _EXTRACTOR_SITES["classify_later"]["items_union"])
    # §11.3's "a crash cannot impersonate an idle pass" argument cites both
    # fail-open `except` arms by their own lines; derive them the same way.
    fail_opens = (
        _extractor_fail_open_except('errors.append(f"classify(S2) failed'),
        _extractor_fail_open_except('errors.append(f"classify(union) failed'),
    )
    for doc_name, path in _PREREG_STEP_CITE_DOCS:
        text = path.read_text(encoding="utf-8")
        assert f"tortoise/extractor_v2.py:{collector}" in text, (
            f"{doc_name} does not cite the collector at "
            f"tortoise/extractor_v2.py:{collector} — §11's argument rests on "
            f"`_collect_classify_items`, so a cite that points anywhere else is an "
            "argument from code the classify pass never reaches")
        if doc_name == "preregistration.json":
            # the record states the condition in one folded scalar; the RANGE and
            # the two gates are the document's table/text, not the record's.
            continue
        assert f"tortoise/extractor_v2.py:{collector}-{end}" in text, (
            f"{doc_name} does not cite the collector's extent as "
            f"tortoise/extractor_v2.py:{collector}-{end} (the `def` through "
            f"`return items`) — a range that runs past the body names the next "
            "function's lines")
        for gate in gates:
            assert f"`{gate}`" in text, (
                f"{doc_name} does not cite the `if items:` gate at {gate} — the "
                f"classify passes are gated on the collector's list being "
                f"non-empty, which is the whole of S2.3's conditionality")
        for fail_open in fail_opens:
            assert f"`{fail_open}`" in text, (
                f"{doc_name} does not cite the fail-open `except` at {fail_open} — "
                "the idle witness is only distinguishable from a CRASHED pass "
                "because this `except` writes a marker, so a cite landing on any "
                "other line is an argument from code that never runs")


def test_the_pre_registration_cites_the_turn_writer_by_symbol_not_by_line():
    """§1's residual counts the turn rows by their WRITER — and that cite was unpinned.

    The paragraph argues that the E1 bar's residual is a second contradiction the
    instrument cannot remove: 159 of the 338 `Point`s are mechanical turn rows
    written by `_TURN_WRITE_CYPHER`, which writes no `extractedFrom` link by
    construction. It named the writer by BOTH its symbol and a `tortoise/sdk.py`
    line — and the line was the half that rotted: at `f16ca1cf6` the paragraph said
    `sdk.py:2408` while the constant sits at `2451`, the same symbol the merge
    closing round 11 re-pointed in five other bundle files. Nothing read this one:
    `_PREREG_STEP_CITE_DOCS` resolves the two classify surfaces and §7's carve-out
    only, and `_BUNDLE_CITE` matches `sdk|edges|extractor_v2` cites in the SIX
    bundle DOCUMENTS — this file is not among them, and a `sdk.py` number quoted in
    it is swept by nothing.

    The durable fix is the one this bundle keeps re-learning (`#9675`, `#10377`,
    `#10108`): cite the symbol, which cannot re-stale. This leg refuses a
    `sdk.py:<line>` anywhere in the document AND resolves the symbol it names
    instead, so a rename reds here rather than leaving the argument resting on
    code the tree does not have.

    Non-vacuity: at the pre-fix head the document carried `tortoise/sdk.py:2408`,
    so the numeric-cite refusal reds; once it is a symbol cite, the resolution arm
    is the one that can fail.
    """
    text = (_REPO / "docs/evidence/7949-capstone/PRE-REGISTRATION.md").read_text(
        encoding="utf-8")
    numeric = re.findall(r"sdk\.py:\d+", text)
    assert not numeric, (
        "PRE-REGISTRATION.md cites `tortoise/sdk.py` by LINE again "
        f"({numeric}) — that number is resolved by no sweep (this document is not "
        "a `_BUNDLE_DOCS` entry, and `_PREREG_STEP_CITE_DOCS` reads the extractor), "
        "so it rots silently on the next insertion above it; cite the symbol"
    )
    assert "_TURN_WRITE_CYPHER" in text, (
        "§1's residual no longer names the turn writer `_TURN_WRITE_CYPHER` — the "
        "159/338 clause rests on it writing no `extractedFrom` link"
    )
    sdk = "tortoise/sdk.py"
    assert re.search(r"^_TURN_WRITE_CYPHER = \(",
                     (_REPO / sdk).read_text(encoding="utf-8"), re.MULTILINE), (
        f"`_TURN_WRITE_CYPHER` is not a module-level constant in {sdk} any more — "
        "the symbol §1 cites has to resolve"
    )


def test_the_pre_registration_cites_the_state_value_carve_out_extent():
    """§7 argues E2a's two exclusions from the carve-out's own text — so ITS cite is derived too.

    The same merge that re-pointed this bundle's cites left this one behind: `:278`
    was the carve-out's opening COMMENT before the merge and is a BLANK LINE at the
    head, so the audit trail for the two exclusions pointed at nothing while the
    clauses the sentence quotes live eight lines below it. Nothing pinned it — the
    leg above reads §11's three classify sites only — and this is the same class
    (`#10377`, `#5095`): a typed number is repaired only by another hand
    re-derivation. The constant's extent comes from `ast`, and the quoted clauses are
    required to fall INSIDE it, so a cite that resolves to the wrong neighbours reds
    even when the number happens to exist in the file.

    Non-vacuity: at `68b2e617d` the document cited `:278` while the constant begins at
    `:294`, and this leg reds on it.
    """
    node = next(
        node for node in ast.walk(
            ast.parse((_REPO / _EXTRACTOR).read_text()))
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name)
                and target.id == "STATE_VALUE_CARVE_OUT"
                for target in node.targets))
    start, end = node.lineno, node.end_lineno
    # The constant is an implicitly concatenated literal, so a clause can straddle
    # a source line ("ids, hashes, and " / "ephemeral counters"). Compare against
    # the VALUE, whitespace-collapsed and case-folded — that is what the sentence
    # quotes, and reading the source text instead would red on a line wrap.
    value = " ".join(str(node.value.value).split()).lower()
    for quoted in ("measurements, deadlines/freezes, thresholds/ttls, versions, "
                   "and counts",
                   "ids, hashes, and ephemeral counters"):
        assert quoted in value, (
            f"the carve-out at tortoise/extractor_v2.py:{start}-{end} no longer "
            f"carries the clause §7 quotes as its authority: {quoted!r}")
    text = (_REPO / "docs/evidence/7949-capstone/PRE-REGISTRATION.md").read_text(
        encoding="utf-8")
    assert f"tortoise/extractor_v2.py:{start}-{end}" in text, (
        f"PRE-REGISTRATION.md does not cite the carve-out as "
        f"tortoise/extractor_v2.py:{start}-{end} — §7's two E2a exclusions are "
        "argued against that text, so a cite that lands anywhere else hands the "
        "auditor a rule it cannot find")


#: A bare CONTINUATION cite in either backtick spelling: the probes are
#: docstrings, so they write ``:13334`` (RST) where the markdown writes `:13334`.
_BARE_CONTINUATION = re.compile(r"`{1,2}(:\d+(?:-\d+)?)`{1,2}")

#: The two documents that print the entry-point pair — the `def` cite AND the
#: `:N` continuation naming the CALL. `E1-E5-PROOFS.md`, `docs/00_index.md` and
#: `EXTRACTOR-V4-ARCHITECTURE.md` never restate the pair, so they are not this
#: leg's business (their own prefixed cites are swept above).
_ENTRY_POINT_DOCS = (
    ("README.md", _REPO / "docs/evidence/7949-capstone/README.md"),
    ("probes/capstone_run.py",
     _REPO / "docs/evidence/7949-capstone/probes/capstone_run.py"),
)


def test_the_bare_entry_point_call_cite_resolves_to_the_call_it_names():
    """The `:N` continuation naming the `_extract_session_v2` CALL is checked.

    `_BUNDLE_CITE` only matches a cite that NAMES a file, so `(sdk.py:14297;
    called at :13334)` had exactly one checked number and one unverifiable one —
    and the unchecked half went stale TWICE on this branch, once at the commit
    that re-derived it (it was already `:13299` there). Re-staling it to a wrong
    line reds NOTHING: measured, `:13334` → `:13399` in both documents leaves this
    file at 51 passed. That is the blind spot this leg closes from the other side,
    the same one `_BARE_CITE` names above for the per-surface numbers.

    Both numbers are DERIVED from the tree (the bundle's own convention: the
    symbol is authoritative, the line is a convenience), so this leg never carries
    a typed number that an insertion above `_extract_session_v2` would silently
    invalidate. The pair must sit on ONE line, because that is what makes a bare
    continuation attributable at all: with no file in front of it, the only thing
    that says WHICH tree the number counts is its sentence.

    Non-vacuity: the anchors must each match exactly one line (two calls would make
    "the" call site a guess, and the message says so), and re-staling the doc's
    continuation to any other number reds here.
    """
    sdk_lines = (_REPO / "tortoise/sdk.py").read_text(encoding="utf-8").splitlines()
    call_anchor = "extracted, meta = self._extract_session_v2("
    def_anchor = "def _extract_session_v2("
    calls = [i + 1 for i, ln in enumerate(sdk_lines) if call_anchor in ln]
    defs = [i + 1 for i, ln in enumerate(sdk_lines)
            if ln.lstrip().startswith(def_anchor)]
    assert len(calls) == 1, (
        f"{len(calls)} lines carry {call_anchor!r} ({calls}) — the call site this "
        "leg pins is no longer unique, so derive the one the entry-point paragraph "
        "means before trusting a number here")
    assert len(defs) == 1, (
        f"{len(defs)} lines define `_extract_session_v2` ({defs}) — the bundle "
        "trumpets ONE entry point; re-derive which def it means")
    call_line, def_line = calls[0], defs[0]
    assert call_line != def_line, (call_line, def_line)
    for name, path in _ENTRY_POINT_DOCS:
        text = path.read_text(encoding="utf-8")
        doc_lines = text.splitlines()
        citing = [ln for ln in doc_lines if f"sdk.py:{def_line}" in ln]
        assert len(citing) == 1, (
            f"{name} cites the entry point `sdk.py:{def_line}` on {len(citing)} "
            f"lines — this leg attributes the bare continuation by its LINE, so "
            "exactly one line may carry the pair")
        bare = [m.group(1) for m in _BARE_CONTINUATION.finditer(citing[0])]
        bare = [b for b in bare if b != f":{def_line}"]
        assert bare == [f":{call_line}"], (
            f"{name} names the entry point at `sdk.py:{def_line}` but its "
            f"continuation cites {bare or 'nothing'}; the CALL is at "
            f"sdk.py:{call_line} (`{call_anchor}`) and the `def` is at "
            f"sdk.py:{def_line}. A bare `:N` names no file, so `_BUNDLE_CITE` — and "
            "every rule in `_BUNDLE_RULES` — cannot check it; this is the leg that "
            "can. Re-derive the number from the tree; do not delete the cite.")


# ── E2's blocker names the LIVE half, not the closed render issue ─────────
#
# PR #10382 closed #9775's render half: every path that builds a prompt now
# carries `STATE_VALUE_CARVE_OUT`, the compact render included. What still blocks
# E2 is the OTHER half — nothing in the write path ENFORCES retention — plus the
# 1-of-14 value loss that sits upstream of the filter, and both are #10360. Until
# this pin, four routes still sent a reader to #9775 as an OPEN blocker, i.e. to a
# lane that had LANDED — and the fourth (the `#9080` capstone row of
# `docs/00_index.md`, which is a DIFFERENT line of the same file from the
# "Blockers named per row" line) survived the round that fixed the other three,
# because the pin only read the one line it knows how to find. A pin that reads one
# line of a file that carries two cannot claim the file is clean; leg 4 reads rows.
#
# This is a different rule from "a row moves to a different verdict only by
# RE-RUNNING the probe": a verdict is a MEASUREMENT, but a blocker name is a
# ROUTE. `capstone_run.py::ALSO_BLOCKED_BY` exists for exactly this failure — E5
# read `#7947` for two commits after that lane closed (#9089) — so the same
# misattribution in the E2 row is a defect of the same class.
#
#: The pre-fix spellings, kept as the mutation this pin catches: each one asserted
#: an absence that PR #10382 removed, in a bundle whose whole purpose is that a
#: claim carries an artifact.
_RETRACTED_E2_RENDER_ABSENCE = (
    "#9775 records that the state-value carve-out is absent on one render path",
    "| `#9775` the state-value carve-out on one render path |",
    "the carve-out is absent on the compact render path",
)


def test_the_bundle_names_the_live_e2_blocker_and_no_closed_render_absence():
    """E2 is blocked by the enforcement half, not by the render #10382 fixed.

    Four surfaces say it, and each had to move: the row the probe EMITS
    (`build_rows`), the E2 row of the audit bundle, the `docs/00_index.md`
    "Blockers named per row" line that is the bundle's entry point, and — leg 4 —
    the `#9080` capstone row of the same index, which is the row a reader meets
    first and which still cited `#9775` as E2's blocker at `136bf8e65` while the
    other three named `#10360`. Leg 4 reads ROWS rather than the one line part 3
    knows how to find, and it lives on that row by name, not by line number.

    Non-vacuity: on the pre-fix tree the probe emitted `blocked_by="#9775"` and
    the README carried both retracted sentences, so legs 1 and 2 both red there;
    leg 4 reds on the `136bf8e65` index (measured: `git show
    136bf8e65:docs/00_index.md`), and it asserts its own subject exists so it
    cannot pass by vacancy on an index that stops routing E2 at all.
    """
    # 1. The row the probe emits names the live half.
    by_id = {row["criterion"]: row for row in _rows()}
    assert by_id["E2"]["blocked_by"] == "#10360", (
        "the probe still emits E2's blocker as "
        f"{by_id['E2']['blocked_by']!r} — #9775's render half is CLOSED (PR "
        "#10382); the live blocker is #10360")

    # 2. No bundle document still asserts the absence the merge removed.
    for name, path in _BUNDLE_DOCS:
        text = path.read_text(encoding="utf-8")
        for phrase in _RETRACTED_E2_RENDER_ABSENCE:
            assert phrase not in text, (
                f"{name} still asserts E2's closed render absence: {phrase!r} — "
                "PR #10382 makes every render path carry the carve-out, so the "
                "blocker is the enforcement half (#10360)")

    # 3. The two entry points a reader is routed through name it too.
    proofs = (_REPO / "docs/evidence/7949-capstone/E1-E5-PROOFS.md").read_text(
        encoding="utf-8")
    e2_row = next(line for line in proofs.splitlines() if line.startswith("| E2 |"))
    assert "`#10360`" in e2_row, e2_row

    index = (_REPO / "docs/00_index.md").read_text(encoding="utf-8")
    blockers = next(line for line in index.splitlines()
                    if "Blockers named per row:" in line)
    assert "`#10360` (E2)" in blockers, blockers[:400]
    assert "`#9775` (E2)" not in blockers, blockers[:400]

    # 4. ⛔ EVERY route, not one line of the file. `docs/00_index.md` carries TWO
    #    routes to E2's verdict: the `#9080` capstone row (which cites every row's
    #    blocker as ``(`#NNNN`)`` right beside `E2 BLOCKED`) and the "Blockers
    #    named per row" line that part 3 above reads. A pin that reads one line of a
    #    file that carries two cannot claim the file is clean — measured at
    #    `136bf8e65`: the "Blockers" line named `#10360` while the `#9080` row
    #    still sent a reader to the CLOSED `#9775`, so the file contradicted
    #    itself on E2's blocker and every gate was green.
    #
    #    The rule is over rows, not over the `#9775` spellings found today: a row
    #    that marks E2 BLOCKED must name the live blocker and must not name the
    #    closed one, whichever words it uses to do it.
    e2_blocked_rows = [line for line in index.splitlines()
                       if "E2" in line and "BLOCKED" in line]
    assert e2_blocked_rows, (
        "no line of docs/00_index.md marks E2 blocked — this leg is vacuous "
        "(it would pass on an index that stopped routing E2 at all)")
    for row in e2_blocked_rows:
        assert "`#10360`" in row, (
            "an index row marks E2 blocked without naming the live blocker "
            f"`#10360`: {row[:400]}")
        assert "`#9775`" not in row, (
            "an index row still routes a reader to `#9775` as E2's blocker — "
            "#9775's render half is CLOSED (PR #10382), so the live blocker is "
            f"`#10360`: {row[:400]}")


# ── the header's import claim, MEASURED (#10525 review P3) ───────────────────────────────────

#: Modules a DB/network/LLM-free slice must never pull in. A driver, client or model SDK at import
#: time is what would make this file's selection and shard weight a lie; the pure-Python `tortoise`
#: package the probe derives its class set FROM is not (it opens no connection and calls no model).
_FORBIDDEN_IMPORTS = ("falkordb", "redis", "psycopg2", "psycopg", "httpx", "requests",
                      "openai", "anthropic")


def _import_this_module_in_a_subprocess() -> dict:
    """Import THIS module in a fresh interpreter and report what it pulled in.

    A subprocess is the only honest instrument here: `sys.modules` in the running session already
    carries whatever the other ~59 pin functions loaded, so an in-process read cannot tell this
    module's own imports from its neighbours'.
    """
    code = (
        "import importlib.util as ilu, json, sys\n"
        f"spec = ilu.spec_from_file_location('t10525_import_probe', {str(Path(__file__).resolve())!r})\n"
        "module = ilu.module_from_spec(spec)\n"
        "sys.modules['t10525_import_probe'] = module\n"
        "spec.loader.exec_module(module)\n"
        "print(json.dumps({\n"
        "  'tortoise': sorted(k for k in sys.modules if k.split('.')[0] == 'tortoise'),\n"
        f"  'forbidden': [d for d in {list(_FORBIDDEN_IMPORTS)!r} if d in sys.modules],\n"
        "}))\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], cwd=str(_REPO),
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout)


def test_the_import_claim_is_what_importing_actually_does():
    """The header's import claim must agree with an executed import (#10525 review P3, MEASURED).

    The docstring used to say *"the module imports no ``tortoise`` symbol at import time"* — the
    file's whole justification for sitting in a DB-free slice. This module executes
    ``measure = _load()`` in its own header, and the probe derives ``DERIVED_LABELS`` from
    ``tortoise.projection.edges`` at import (``_derivation_label_classes``, #9736), so the claim
    had been false since that amendment landed and nothing referenced it
    (``grep "imports no"`` returned only the sentence itself).

    This is the class the bundle exists to refuse — a committed claim the tree refutes, with no
    number moving and no test revealing it. Both halves are measured rather than restated: what
    DOES arrive (27 ``tortoise.*`` modules, ``projection.edges`` among them) and what does NOT
    (every driver/client/model SDK), so the wording is pinned to the tree, not to memory.

    Non-vacuity: at the pre-fix head the docstring names none of ``tortoise.projection.edges``,
    ``falkordb`` or ``#10525``, and carries the bare stale sentence with nothing signalling it
    is refuted, so all three assertions red.
    """
    payload = _import_this_module_in_a_subprocess()
    assert "tortoise.projection.edges" in payload["tortoise"], (
        "the probe's class set no longer comes from `tortoise.projection.edges` "
        f"(#9736) — the docstring states it does, so one of the two moved: {payload['tortoise']}")
    assert payload["forbidden"] == [], (
        "importing this module now pulls in a DB/network/LLM dependency, which the slice's "
        f"selection and shard weight assume it does not: {payload['forbidden']}")

    doc = sys.modules[__name__].__doc__ or ""
    assert "tortoise.projection.edges" in doc, (
        "the docstring must state the import this module ACTUALLY performs, or a reader (and "
        "the next lane) is told a DB-free slice imports nothing at all")
    assert "falkordb" in doc and "redis" in doc, (
        "the driver-free half is the load-bearing half now that a `tortoise` import is real — "
        "the docstring must name what it checked for")
    assert "#10525" in doc, "the corrected claim must name the finding that corrected it"
    stale = "imports no ``tortoise`` symbol at import time"
    if stale in doc:
        assert "refuted" in doc or "earlier" in doc, (
            "the stale claim is restated as current fact — if it is kept at all it must be "
            "marked as the wording that was refuted")


def test_the_e1_e2_record_refuses_a_record_from_another_run(tmp_path):
    """One record, one run — a graph off the ON head must refuse.

    Each of the two record classes the score reads is moved to a graph the ON arm
    never ran, one at a time, so a guard that only covers the class it was
    written for cannot pass the other by accident.
    """
    for name in ("e2a-value-units.json", "e1-gate-drops.json"):
        repo = tmp_path / name.replace(".", "_")
        shutil.copytree(_REPO / "docs/evidence/9889", repo / "docs/evidence/9889")
        shutil.copytree(_REPO / "docs/evidence/7949-capstone",
                        repo / "docs/evidence/7949-capstone")
        path = repo / "docs/evidence/9889" / name
        record = json.loads(path.read_text())
        record["graph"] = "capstone_on_deadbeef0_1790000000"
        path.write_text(json.dumps(record))
        with pytest.raises(SystemExit) as refused:
            scores_probe.score(repo)
        assert refused.value.code == 2, (name, refused.value)


def test_the_falsifier_is_provenanced_on_the_block_its_number_is_read_from(
        tmp_path, capsys):
    """F2: the SCORED block (`with_anchor`) is the one whose graph is checked.

    The falsifier's 14/14 is read from `with_anchor` alone. A record whose
    TOP-LEVEL `graph` names the ON head while that block names a different run
    was accepted by the head-or-with_anchor-or-as_written loop — a provenance
    claim about the run the number came from that the code did not enforce.

    Non-vacuity: the pre-fix guard reads `record.get("graph") or …`, so this
    exact record scores and the `pytest.raises` arm reds.
    """
    repo = tmp_path / "repo"
    shutil.copytree(_REPO / "docs/evidence/9889", repo / "docs/evidence/9889")
    shutil.copytree(_REPO / "docs/evidence/7949-capstone",
                    repo / "docs/evidence/7949-capstone")
    path = repo / "docs/evidence/9889" / "e2a-value-units-falsifier.json"
    record = json.loads(path.read_text())
    scores = json.loads(
        (repo / "docs/evidence/9889/e1-e2-scores.json").read_text())
    head, graph = scores["head"], scores["graph"]
    record["graph"] = graph                                # top level names the head
    assert head[:9] in graph, (head, graph)
    record["with_anchor"]["graph"] = "capstone_on_deadbeef0_1790000000"
    path.write_text(json.dumps(record))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    err = capsys.readouterr().err
    assert "scored block" in err, err
    assert "capstone_on_deadbeef0_1790000000" in err, err

    # …and NAMING THE HEAD is not enough: a graph is `capstone_on_<head>_<stamp>`,
    # so a SIBLING RUN of the same head is a different graph and must refuse too —
    # otherwise the falsifier certifies its counter on a corpus invocation the E2a
    # record was never taken on. Non-vacuity: the head-only guard scores this.
    sibling = json.loads(path.read_text())
    sibling["with_anchor"]["graph"] = f"capstone_on_{head[:9]}_9999999999"
    assert head[:9] in sibling["with_anchor"]["graph"]      # the head check passes
    assert sibling["with_anchor"]["graph"] != graph          # the run differs
    path.write_text(json.dumps(sibling))
    with pytest.raises(SystemExit) as sibling_refused:
        scores_probe.score(repo)
    assert sibling_refused.value.code == 2, sibling_refused.value
    err = capsys.readouterr().err
    assert "the same head is not the same run" in err, err


def test_the_falsifiers_as_written_block_must_be_the_e2a_records_run(
        tmp_path, capsys):
    """F2's second arm: `as_written` names the run the E2a record was taken on.

    The head loop refuses a foreign graph on the top level and on `with_anchor`
    before this arm can fire, so a record that moves ALL of them proves nothing
    about `as_written`. Here the top level and the scored block keep the head and
    ONLY `as_written` moves — same head, different run — and the refusal must name
    that block.

    Non-vacuity: delete the `as_written` arm and this record scores.
    """
    repo = tmp_path / "repo"
    shutil.copytree(_REPO / "docs/evidence/9889", repo / "docs/evidence/9889")
    shutil.copytree(_REPO / "docs/evidence/7949-capstone",
                    repo / "docs/evidence/7949-capstone")
    path = repo / "docs/evidence/9889" / "e2a-value-units-falsifier.json"
    record = json.loads(path.read_text())
    scores = json.loads(
        (repo / "docs/evidence/9889/e1-e2-scores.json").read_text())
    for block in (record, record["with_anchor"]):
        block["graph"] = scores["graph"]          # the head, on both checked blocks
    record["as_written"]["graph"] = "capstone_on_54922a800_other"
    path.write_text(json.dumps(record))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    err = capsys.readouterr().err
    assert "as-written block is on graph" in err, err
    assert "capstone_on_54922a800_other" in err, err


def test_the_falsifiers_as_written_block_must_carry_its_own_graph(tmp_path, capsys):
    """F2's third arm: an `as_written` block with no graph is not inherited from.

    The block is the reading the E2a record was taken on, so an absent graph there
    is precisely the provenance this guard exists to prove — defaulting it to the
    scored block's would assume the claim and then report it as proven.

    Non-vacuity: delete the `carries no graph` arm and this record scores (it
    inherits `falsifier_graph` at the equality check).
    """
    repo = tmp_path / "repo"
    shutil.copytree(_REPO / "docs/evidence/9889", repo / "docs/evidence/9889")
    shutil.copytree(_REPO / "docs/evidence/7949-capstone",
                    repo / "docs/evidence/7949-capstone")
    path = repo / "docs/evidence/9889" / "e2a-value-units-falsifier.json"
    record = json.loads(path.read_text())
    record["as_written"].pop("graph")
    path.write_text(json.dumps(record))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    err = capsys.readouterr().err
    assert "carries no graph" in err, err
    assert "e2a-value-units.json" in err, err

    # …and an EMPTY block is PRESENT, not absent: testing truthiness read it as
    # "no block at all" and scored the record, which is the fail-open this arm
    # exists to refuse.
    empty = json.loads(
        (_REPO / "docs/evidence/9889/e2a-value-units-falsifier.json").read_text())
    empty["as_written"] = {}
    path.write_text(json.dumps(empty))
    with pytest.raises(SystemExit) as empty_refused:
        scores_probe.score(repo)
    assert empty_refused.value.code == 2, empty_refused.value
    assert "carries no graph" in capsys.readouterr().err


def test_the_falsifiers_as_written_numbers_are_the_e2a_reading(tmp_path, capsys):
    """F2's fourth arm: "the same measurement" is a claim about the NUMBERS.

    The bundle's README reads `as_written` as the E2a reading before injection
    (`as_written 8/14 -> injected 14/14`). A block carrying different counts is a
    SECOND reading passed off as the same one, and the graph check alone cannot
    see it.

    Non-vacuity: delete the counts arm and this record scores.
    """
    repo = tmp_path / "repo"
    shutil.copytree(_REPO / "docs/evidence/9889", repo / "docs/evidence/9889")
    shutil.copytree(_REPO / "docs/evidence/7949-capstone",
                    repo / "docs/evidence/7949-capstone")
    path = repo / "docs/evidence/9889" / "e2a-value-units-falsifier.json"
    record = json.loads(path.read_text())
    record["as_written"]["value_survived"] = 14
    path.write_text(json.dumps(record))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    err = capsys.readouterr().err
    assert "the as-written block IS the E2a reading" in err, err

    # …and the DENOMINATOR half: a block reading `8 of 12` is a second population
    # passed off as the same measurement. (The frozen-fourteen refusal names the
    # pre-registered number for the E2a record, so this arm must be the one that
    # catches the falsifier's own denominator.)
    shrunk = json.loads(path.read_text())
    shrunk["as_written"]["value_survived"] = 8
    shrunk["as_written"]["value_units"] = 12
    path.write_text(json.dumps(shrunk))
    with pytest.raises(SystemExit) as denom_refused:
        scores_probe.score(repo)
    assert denom_refused.value.code == 2, denom_refused.value
    err = capsys.readouterr().err
    assert "reads 8 of 12 but" in err, err
    assert "the as-written block IS the E2a reading" in err, err


def test_the_falsifiers_top_level_names_the_run_too(tmp_path, capsys):
    """F2's headline arm: the field a reader sees FIRST is checked like the rest.

    Both blocks name the E2a run while the TOP-LEVEL `graph` names another one —
    the record then asserts two runs. Checking only the scored block let this
    through, so a pin that moves ALL the graphs (and is refused on the E2a record
    first) cannot see it.

    Non-vacuity: drop the falsifier from the one-head loop and this record scores.
    """
    repo = tmp_path / "repo"
    shutil.copytree(_REPO / "docs/evidence/9889", repo / "docs/evidence/9889")
    shutil.copytree(_REPO / "docs/evidence/7949-capstone",
                    repo / "docs/evidence/7949-capstone")
    path = repo / "docs/evidence/9889" / "e2a-value-units-falsifier.json"
    record = json.loads(path.read_text())
    scores = json.loads(
        (repo / "docs/evidence/9889/e1-e2-scores.json").read_text())
    head, graph = scores["head"], scores["graph"]
    for block in (record["with_anchor"], record["as_written"]):
        block["graph"] = graph                           # both blocks on the run
    record["graph"] = "capstone_on_cafebabe0_1791111111"  # the headline is not
    path.write_text(json.dumps(record))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    err = capsys.readouterr().err
    assert "e2a-value-units-falsifier.json was taken on graph" in err, err
    assert "which does not name head" in err, err

    # …and NAMING the head is not enough here either: a headline declaring a
    # SIBLING run of the same head passes the loop and would otherwise go
    # unchecked, while both blocks are required to be the E2a run.
    sibling = json.loads(path.read_text())
    sibling["graph"] = f"capstone_on_{head[:9]}_9999999999"
    assert head[:9] in sibling["graph"]           # the head loop passes
    sibling["with_anchor"]["graph"] = graph
    sibling["as_written"]["graph"] = graph
    path.write_text(json.dumps(sibling))
    with pytest.raises(SystemExit) as sibling_refused:
        scores_probe.score(repo)
    assert sibling_refused.value.code == 2, sibling_refused.value
    err = capsys.readouterr().err
    assert "top-level graph is" in err, err
    assert "would assert two runs" in err, err


def test_the_falsifiers_with_anchor_block_is_present_or_absent_never_empty(
        tmp_path, capsys):
    """A PRESENT-BUT-EMPTY `with_anchor` is refused; an ABSENT one still scores.

    The flat fallback is for a record that declares no block at all. An empty one
    is a record that never wrote its counter, so inheriting the top-level numbers
    would attribute the injected 14/14 to a provenance the block does not carry —
    the same fail-open the `as_written` arm closes, one function up.

    The guard exists at TWO sites with neither reachable through the other in a
    test: `score()` refuses before it delegates to `e2_score()`, so each message
    carries its own token and each is pinned here — a lane that deletes either as
    dead code would otherwise be served green by the other's identical text.

    Non-vacuity: delete BOTH arms and this record scores, reporting the top-level
    14/14 as the `with_anchor` reading.
    """
    repo = tmp_path / "repo"
    shutil.copytree(_REPO / "docs/evidence/9889", repo / "docs/evidence/9889")
    shutil.copytree(_REPO / "docs/evidence/7949-capstone",
                    repo / "docs/evidence/7949-capstone")
    path = repo / "docs/evidence/9889" / "e2a-value-units-falsifier.json"
    record = json.loads(path.read_text())
    scores = json.loads(
        (repo / "docs/evidence/9889/e1-e2-scores.json").read_text())
    injected = dict(record["with_anchor"])
    record["with_anchor"] = {}
    record.update(injected)                  # the 14/14 parked at the top level
    record["graph"] = scores["graph"]
    path.write_text(json.dumps(record))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    score_err = capsys.readouterr().err
    assert "with-anchor block is present but empty" in score_err, score_err
    assert "(score)" in score_err, score_err

    # …and `e2_score`'s own arm has its ONLY witness here: called directly (the
    # route a future caller takes), with the same record, it must refuse too and
    # say so with its own token.
    with pytest.raises(SystemExit) as direct:
        scores_probe.e2_score(repo, scores_probe.frozen_denominators(repo))
    assert direct.value.code == 2, direct.value
    direct_err = capsys.readouterr().err
    assert "with-anchor block is present but empty" in direct_err, direct_err
    assert "(e2_score)" in direct_err, direct_err

    # …and the flat record WITHOUT the key is the documented shape and still
    # scores, so the arm discriminates "empty" from "absent" rather than
    # refusing the whole flat form.
    flat = json.loads(path.read_text())
    flat.pop("with_anchor")
    path.write_text(json.dumps(flat))
    assert scores_probe.score(repo)["e2"]["outcome"] == "FAIL"


def test_the_e2a_bar_is_parsed_from_the_record_not_typed(tmp_path):
    """F3: amend the frozen threshold and the bar must MOVE.

    A typed `required = 13` survives every pin over the shipped record — the
    shipped value says 13 either way. Only an amended copy can tell a parsed 13
    from a typed one, so this asserts BOTH the moved number and the flipped
    outcome.
    """
    repo = tmp_path / "repo"
    shutil.copytree(_REPO / "docs/evidence/9889", repo / "docs/evidence/9889")
    shutil.copytree(_REPO / "docs/evidence/7949-capstone",
                    repo / "docs/evidence/7949-capstone")
    prereg_path = repo / "docs/evidence/7949-capstone/preregistration.json"
    prereg = json.loads(prereg_path.read_text())
    for entry in prereg["criteria"]:
        if entry.get("id") == "E2a":
            entry["threshold"] = ">= 5 of 14"
    prereg_path.write_text(json.dumps(prereg))
    record = scores_probe.score(repo)
    assert record["e2"]["e2a"]["required"] == 5, record["e2"]["e2a"]
    assert record["e2"]["e2a"]["outcome"] == "PASS", record["e2"]["e2a"]


def _bundle(tmp_path: Path) -> Path:
    """A throwaway copy of both evidence bundles — the fixture this family uses."""
    repo = tmp_path / "repo"
    shutil.copytree(_REPO / "docs/evidence/9889", repo / "docs/evidence/9889")
    shutil.copytree(_REPO / "docs/evidence/7949-capstone",
                    repo / "docs/evidence/7949-capstone")
    return repo


@pytest.mark.parametrize("key", ["TORTOISE_VET", "TORTOISE_CLASSIFY_LATER",
                                  "TORTOISE_VALUE_GATE",
                                  "TORTOISE_S2_DECISION_SEAM"])
def test_a_before_arm_that_was_not_OFF_is_refused(key, tmp_path, capsys):
    """#10610 review P1: the before arm's OWN arm-readback is now checked.

    `e1_paired_arm.py` refuses to run with any of the three switches already set
    (this issue's acceptance item 4) and writes the env it read back into `arm`,
    so the record CARRIES the proof that the arm was OFF. The driver collected
    that field and never read it, so two ON realizations scored as "before"
    against an ON "after" produced a DECIDED `FAIL` — a comparison of a
    treatment against itself, reported as a measurement.

    Non-vacuity: the pre-fix driver scores this bundle (measured: `e1.outcome ==
    FAIL`, `decidable is True`), so the `pytest.raises` arm reds without the fix.
    """
    repo = _bundle(tmp_path)
    path = repo / "docs/evidence/9889/e1-paired-off-arm-1.json"
    record = json.loads(path.read_text())
    assert record["arm"][key] is None                 # the shipped arm is OFF
    record["arm"][key] = "1"                          # …now THIS key alone is ON
    path.write_text(json.dumps(record))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    err = capsys.readouterr().err
    assert "not a before arm" in err, err
    assert f"{key}='1'" in err, err

    # …and the shipped bundle still scores, so the arm discriminates an ON record
    # from the OFF record that ships rather than refusing the whole form.
    record["arm"][key] = None
    path.write_text(json.dumps(record))
    assert scores_probe.score(repo)["e1"]["outcome"] in ("REFUSED", "PASS", "FAIL")


def test_a_before_arm_with_no_arm_block_is_refused(tmp_path, capsys):
    """An ABSENT provenance is not an inherited one — the `as_written` rule too."""
    repo = _bundle(tmp_path)
    path = repo / "docs/evidence/9889/e1-paired-off-arm-2.json"
    record = json.loads(path.read_text())
    record.pop("arm")
    path.write_text(json.dumps(record))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    err = capsys.readouterr().err
    assert "carries no `arm` block" in err, err
    assert "provenance cannot" in err, err


def test_a_before_arm_with_an_empty_arm_block_is_refused(tmp_path, capsys):
    """#10610 review P2: a block that is PRESENT but EMPTY is not proof either.

    The absent-block guard closed one half of the provenance gap; a block that
    exists and wrote nothing closed the other half open, because `arm.get(var)`
    on a key that was never written returns `None` — and `str(None).strip()` is
    `"none"`, a token of the OFF vocabulary the value check accepts. So
    `"arm": {}` certified an arm nothing in the record establishes, which is the
    same fail-open the falsifier's `with_anchor` block is refused on ("an empty
    one is not the flat shape").

    Non-vacuity: at the pre-fix head this bundle SCORES (every later check —
    head, graph, E2a, corpus — is satisfied by the shipped bundle), so the
    `pytest.raises` arm reds without the presence check.
    """
    repo = _bundle(tmp_path)
    path = repo / "docs/evidence/9889/e1-paired-off-arm-1.json"
    record = json.loads(path.read_text())
    assert set(record["arm"]) == set(scores_probe._ARM_VARS), record["arm"]
    record["arm"] = {}                               # present, writes nothing
    path.write_text(json.dumps(record))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    err = capsys.readouterr().err
    assert "`arm` block carries no TORTOISE_VET" in err, err
    assert "missing key is not an unset one" in err, err


@pytest.mark.parametrize("key", ["TORTOISE_VET", "TORTOISE_CLASSIFY_LATER",
                                  "TORTOISE_VALUE_GATE",
                                  "TORTOISE_S2_DECISION_SEAM"])
def test_a_before_arm_missing_one_arm_key_is_refused(key, tmp_path, capsys):
    """#10610 review P2: a block that lost ONE key is the same gap, one key at a time.

    A partially-written block (`{"TORTOISE_VET": null}`) is neither absent nor
    complete, and the value check alone reads the three missing switches as
    `"none"` — OFF. Each key is popped ALONE so a presence check narrowed to one
    variable cannot stay green: the refusal names only the first missing var, so
    popping all four together would let a one-key check pass.

    Non-vacuity: at the pre-fix head each of these four bundles scores.
    """
    repo = _bundle(tmp_path)
    path = repo / "docs/evidence/9889/e1-paired-off-arm-2.json"
    record = json.loads(path.read_text())
    assert record["arm"][key] is None                 # the shipped arm is OFF
    record["arm"].pop(key)                            # …and now key is not written
    path.write_text(json.dumps(record))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    err = capsys.readouterr().err
    assert f"`arm` block carries no {key}" in err, err
    assert "missing key is not an unset one" in err, err

    # …and the shipped record (key present as JSON null) still scores, so this
    # discriminates a never-written key from the `null` the producer writes.
    record["arm"][key] = None
    path.write_text(json.dumps(record))
    assert scores_probe.score(repo)["e1"]["outcome"] in ("REFUSED", "PASS", "FAIL")


def test_the_arm_key_sets_are_the_producers():
    """The driver's three key sets must BE the producer's, not restatements.

    `e1_paired_arm.py` owns the OFF arm (`(*ARM_KEYS, SEAM_KEY)`) and
    `capstone_measure.py` owns both measured-arm sets (`ARM_ON` / `ARM_OFF`). The
    driver keeps literals so it can be loaded before either module, which is how
    the before side once ran one key short of the producer's four — and how the
    after side came to assert none of the two switches its arm pins OFF. Holding
    the tuples EQUAL is what stops the next switch from widening the gap
    silently: it reds here rather than certifying an arm nobody defined.
    """
    producer = _load_probe("e1_paired_arm")
    assert (*producer.ARM_KEYS, producer.SEAM_KEY) == scores_probe._ARM_VARS, (
        scores_probe._ARM_VARS, producer.ARM_KEYS, producer.SEAM_KEY)
    assert tuple(measure.ARM_ON) == scores_probe._ARM_ON_VARS, measure.ARM_ON
    assert tuple(measure.ARM_OFF) == scores_probe._ARM_OFF_VARS, measure.ARM_OFF


def test_an_e2b_population_under_its_floor_is_REFUSED_not_FAILED(tmp_path):
    """#10610 review P2: the artifact may not agree with the row that reads it.

    `capstone_measure.e2_outcome` — the row builder this record feeds — returns
    REFUSED → BLOCKED for an under-floor E2b population, on its own docstring's
    rule that "REFUSED is neither a pass nor a fail". Recording `FAIL` here made
    the evidence say "a scored failure" about the same operands the row beside it
    calls unmeasurable.

    Non-vacuity: with `below_floor` folded into the FAIL branch instead (the
    pre-fix shape) this record yields `outcome == "FAIL"` and `decidable` True.
    """
    repo = _bundle(tmp_path)
    log = repo / "docs/evidence/9889/on-arm.log"
    text = log.read_text()
    assert "fused=1/114" in text
    log.write_text(text.replace("fused=1/114", "fused=1/10"))
    record = scores_probe.score(repo)
    e2 = record["e2"]
    assert e2["e2b"]["below_floor"] is True, e2["e2b"]
    assert e2["e2b"]["atomic_points"] == 10, e2["e2b"]
    assert e2["e2b"]["floor"] == 30, e2["e2b"]
    assert e2["outcome"] == "REFUSED", e2
    assert e2["decidable"] is False, e2
    assert "under the frozen floor of 30" in e2["why"], e2
    assert e2["decided_by"] == "E2b floor", e2


def test_the_readmes_floor_provenance_claim_matches_the_code():
    """#10610 review P2: the README's floor-provenance sentence vs its READER.

    The paragraph describes **Probe 1, `e1_paired_arm.py`**, and that probe's
    `min_before_share()` reads the share floor STRAIGHT off `denominator_floors`,
    on purpose and with its own stated reason (an escaping `ValueError` from a
    helper is not a refusal a run can be read from). An earlier cut of this pin
    matched WORDING and asserted the opposite, so it passed a false sentence and
    would have redded the true one — and a later cut still could not fail, because
    its "claim" filter matched an unrelated line and its last assertion compared a
    value with itself.

    So the README half is checked in BOTH directions — the two false phrasings
    must be absent AND each half of the sentence must name its own reader, on the
    collapsed paragraph (a wrapped line cannot hide a claim from `in`). The
    behaviour half then BEHAVES the claim: hand the probe a pre-registration whose
    bars `parse_bars` cannot read at all. A probe routed through that map cannot
    return a floor; a probe reading `denominator_floors` returns the frozen 0.20.
    """
    probe = _load_probe("e1_paired_arm")
    readme = (_REPO / "docs/evidence/9889/README.md").read_text()
    # The stale parenthetical is gone: `parse_bars` DOES expose the share floor
    # since this PR (#10610), so "does not expose" is refuted by the tree.
    # …and the false replacement an earlier cut wrote must not come back either.
    # Both prohibitions run against a WHITESPACE-STRIPPED copy, not the raw file:
    # wrap position is free in Markdown, so a one-line reflow of the same sentence
    # (`… both⏎`through …`) satisfied the raw `in` and shipped both refuted
    # claims (measured). Prohibiting on the squashed text closes that.
    squashed = re.sub(r"\s+", "", readme)
    assert "which`parse_bars`doesnotexpose" not in squashed
    assert "boththrough`parse_bars`" not in squashed
    # Prohibitions alone are satisfied by DELETING or REVERSING the sentence, so
    # the required shape is asserted too — each half against the reader the code
    # actually uses (`object_floor` → the map, `min_before_share` → the record).
    para = " ".join(readme.split())
    assert re.search(r"`e1_objects` through `parse_bars`", para), para[:400]
    assert re.search(r"`e1_min_before_share` straight off `denominator_floors`",
                     para), para[:400]

    prereg = _prereg()
    frozen = prereg["denominator_floors"]["e1_min_before_share"]
    # The map itself cannot read this record's bars any more…
    broken = json.loads(json.dumps(prereg))
    for entry in broken["criteria"]:
        if entry.get("id", "").startswith("E1"):
            entry["threshold"] = "PASS-ish"     # unparseable on purpose
    with pytest.raises((SystemExit, ValueError)):
        scores_probe._parse_bars(broken)
    # …and the probe still returns the floor, which it can only do off
    # `denominator_floors`. Non-vacuity: routing `min_before_share` through
    # `parse_bars` reds here with that same exception.
    assert probe.min_before_share(broken) == frozen, probe.min_before_share(broken)


@pytest.mark.parametrize("key", ["TORTOISE_VET", "TORTOISE_CLASSIFY_LATER"])
def test_the_measured_arm_must_be_ON_in_its_own_readback(key, tmp_path, capsys):
    """#10610 review P1, the other half: the AFTER arm's readback is checked too.

    E1's bar is `after <= 0.5 x before`, which only means anything if the after
    arm was ON and the before arms were OFF. The driver checked the before side
    and never looked at `on-arm.log`'s own `# arm:` readback — which
    `capstone_measure` writes from `os.environ` after forcing the passes — so two
    OFF realizations would be reported as a before/after pair: a treatment the run
    never applied, scored as the treated arm.

    Non-vacuity: the pre-fix driver scores this log unchanged (measured: the record
    it writes is byte-identical to the committed one).
    """
    repo = _bundle(tmp_path)
    log = repo / "docs/evidence/9889/on-arm.log"
    text = log.read_text()
    arm_line = [ln for ln in text.splitlines() if ln.startswith("# arm: ")]
    assert len(arm_line) == 1, arm_line
    # ONE key at a time: setting both together let a narrowed `_ARM_ON_VARS` stay
    # green, because the refusal names only the first failing var.
    dead = arm_line[0].replace(f"{key}=1", f"{key}=<unset>")
    assert dead != arm_line[0], (key, arm_line[0])
    log.write_text(text.replace(arm_line[0], dead))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    err = capsys.readouterr().err
    assert "were not ON" in err, err
    assert key in err, err

    # …and the shipped log still scores, with its readback carried into the
    # record so the after side's provenance is auditable, not only checked.
    log.write_text(text)
    record = scores_probe.score(repo)
    assert record["after_arm"]["TORTOISE_VET"] == "1", record["after_arm"]
    assert record["after_arm"]["TORTOISE_CLASSIFY_LATER"] == "1", record["after_arm"]


def test_the_seam_switch_counts_as_ON_for_a_before_arm(tmp_path, capsys):
    """The producer refuses on FOUR keys; the driver must not check three.

    `e1_paired_arm.py` checks `(*ARM_KEYS, SEAM_KEY)` and says why: the seam arms
    S2.2 *through the arbiter*, so `_vet_enabled` reads True even against an
    explicit `TORTOISE_VET=0`. A before arm with the seam armed is therefore a
    treatment, and scoring it as the control is exactly the defect the guard
    exists for — arriving through the one key the acceptance list does not name.

    Non-vacuity: the shipped records already carry the key (as `None`), so widening
    the tuple cannot reject them; the pre-fix three-key driver exits 0 on this
    bundle (measured) and writes the ON block into the pairing.
    """
    repo = _bundle(tmp_path)
    path = repo / "docs/evidence/9889/e1-paired-off-arm-1.json"
    record = json.loads(path.read_text())
    assert record["arm"]["TORTOISE_S2_DECISION_SEAM"] is None
    record["arm"]["TORTOISE_S2_DECISION_SEAM"] = "1"
    path.write_text(json.dumps(record))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    err = capsys.readouterr().err
    assert "not a before arm" in err, err
    assert "TORTOISE_S2_DECISION_SEAM='1'" in err, err


@pytest.mark.parametrize("key", ["TORTOISE_VALUE_GATE",
                                  "TORTOISE_S2_DECISION_SEAM"])
def test_the_measured_arm_must_pin_its_off_switches_off(key, tmp_path, capsys):
    """#10610 review P3: the AFTER arm is checked for ON, never for its OFF pair.

    The measured arm is two keys ON **and two OFF** — `capstone_measure.ARM_OFF`,
    whose own comment says the value gate "also switches S2.2 on" and the seam
    "would arm a network call and a spend inside an arm this file declares OFF".
    The driver asserted only the ON half, so a log measured with the value gate
    armed — the value-gate arm `E1-E2.md` reports SEPARATELY — was accepted as the
    ON half of E1's pair and its after share credited to a bar that arm never
    measured. The before side refuses that key; the asymmetry inside one file is
    the defect.

    Non-vacuity: the shipped log carries both as `<unset>`, so refusing an armed
    value cannot reject it — and the pre-fix driver scores this bundle (measured:
    the record it writes is the committed one).
    """
    repo = _bundle(tmp_path)
    log = repo / "docs/evidence/9889/on-arm.log"
    text = log.read_text()
    arm_line = [ln for ln in text.splitlines() if ln.startswith("# arm: ")]
    assert len(arm_line) == 1, arm_line
    # ONE key at a time, for the same reason the ON pin is parametrized: the
    # refusal names the first failing var, so arming both together could keep a
    # one-key check green.
    armed = arm_line[0].replace(f"{key}=<unset>", f"{key}=1")
    assert armed != arm_line[0], (key, arm_line[0])
    log.write_text(text.replace(arm_line[0], armed))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    err = capsys.readouterr().err
    assert "pins it OFF" in err, err
    assert f"{key}='1'" in err, err

    # …and the shipped log still scores, with both switches carried into the
    # record so the OFF half of the arm travels with the number.
    log.write_text(text)
    record = scores_probe.score(repo)
    assert record["after_arm"][key] == "<unset>", record["after_arm"]


def test_the_measured_arm_readback_must_CARRY_its_off_switches(tmp_path, capsys):
    """An absent OFF switch is not an unset one — the same rule, after side.

    Dropping `TORTOISE_VALUE_GATE=<unset>` from the readback was read as `"none"`
    by the value check, i.e. as consent: a log that stopped writing the switch
    certified the arm the switch's absence was supposed to be evidence about.
    Presence is required for exactly the reason it is required on the before
    side — `capstone_measure.arm_header` writes all four keys on every run, so a
    line that omits one was edited, not measured.

    Non-vacuity: at the pre-fix head this bundle scores (measured: the record it
    writes is the committed one).
    """
    repo = _bundle(tmp_path)
    log = repo / "docs/evidence/9889/on-arm.log"
    text = log.read_text()
    arm_line = [ln for ln in text.splitlines() if ln.startswith("# arm: ")]
    assert len(arm_line) == 1, arm_line
    stripped = arm_line[0].replace(" TORTOISE_VALUE_GATE=<unset>", "")
    assert stripped != arm_line[0], arm_line[0]
    log.write_text(text.replace(arm_line[0], stripped))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    err = capsys.readouterr().err
    assert "carries no TORTOISE_VALUE_GATE" in err, err
    assert "missing key is not an unset one" in err, err

