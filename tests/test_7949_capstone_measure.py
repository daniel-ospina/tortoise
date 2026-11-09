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
import io
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tokenize
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
    `:Source` (`tortoise/projection/edges.py:2765-2769`, D10) — and §10.1 of the pre-registration,
    computed on the superseded run's census, records no `Document` written at all (the scored log
    agrees: `e3 label=Document nodes=0`). So the vacuous leg is the PRODUCTION
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
    # NOT a Document-only guard: a tuple whose empty leg is `Event` must fail too, or an
    # implementation that honoured only the class the issue happens to name would pass every arm.
    assert measure.e3_outcome(anchored=30, derived=30, bars=BARS,
                              unmeasured_legs=("Event",))["outcome"] == "FAIL"
    # and a FLOOR refusal outranks it — a too-small population is not a verdict either way
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
    `render_log` to DERIVE the log's own `e3 unmeasured_legs=` line from that same CENSUS
    (`e3_legs_line(census_data[...])`) rather than reading it back off the row — because the
    defect this closes was `0/0` being invisible in both places at once, and a caller-built row
    that happens to lack the key would otherwise render "every registered class carried nodes"
    two lines beneath `e3 label=Document nodes=0`; and (c) `main` to hand `e3_row_note` the E3
    OUTCOME dict, because the note reads the clause through a `.get`-with-default and a mis-wire
    there drops the disclosure with no error.
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
    # ...and the class set must not be NARROWED at the call site: `labels=("Document",)` would keep
    # this AST pin green while a run that wrote no `Event` scored PASS over a narrower population —
    # the same defect, one layer up from the pure rule. Neither spelling of the narrowing may pass:
    # the keyword is refused here, and `labels` is KEYWORD-ONLY in the signature so a second
    # positional argument cannot slip past it either (round 3 measured exactly that mutation green).
    assert not [kw for kw in wired.keywords if kw.arg == "labels"], (
        "main narrows e3_unmeasured_legs' class set at the call site — the registered set must come "
        "from DERIVED_LABELS, or the wiring re-creates the hole the rule closes: " + ast.dump(wired))
    assert not wired.args[1:], (
        "main passes e3_unmeasured_legs a second POSITIONAL argument (a class tuple) — the registered "
        "set must come from DERIVED_LABELS: " + ast.dump(wired))
    sig = next(node for node in tree.body
               if isinstance(node, ast.FunctionDef) and node.name == "e3_unmeasured_legs")
    names = [a.arg for a in sig.args.args]
    assert sig.args.kwonlyargs and not sig.args.defaults, (
        "e3_unmeasured_legs' `labels` is no longer keyword-only — a positional class tuple at the "
        "call site would then be unpinnable: args=" + repr(names))

    render = next(node for node in tree.body
                  if isinstance(node, ast.FunctionDef) and node.name == "render_log")
    rendered = [n for n in ast.walk(render)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == "e3_legs_line"]
    assert rendered, (
        "render_log no longer prints the `e3 unmeasured_legs=` line — the log's per-label lines show "
        "a 0-node leg as `nodes=0 anchored=0 unanchored=0`, which reads as a clean class rather than "
        "an unmeasured one")
    # ...and it must read the line from the CENSUS, not from a row a caller may have built by hand:
    # a positive "every registered class carried nodes" asserted from an ABSENT key is the same
    # claim-from-nothing this line exists to disclose.
    assert "census_data" in ast.dump(rendered[0]), ast.dump(rendered[0])

    # (c) the ROW NOTE must be fed the E3 OUTCOME, not the row being built. `e3_row_note` reads the
    # clause through `e3_result.get("unmeasured_legs") or ()` — a `.get` with a default, so a caller
    # that hands it the ROW (whose key is `note`, still being built) yields the empty clause instead
    # of a `KeyError`: the note silently stops disclosing the leg while every other pin stays green.
    # Measured before this arm existed: mutating this call to `e3_result=e3` left the file green
    # (129 passed, 1 unrelated failure), i.e. the note was wired but unpinned.
    notes = [n for n in ast.walk(main)
             if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "e3_row_note"]
    assert len(notes) == 1, (
        "main no longer calls e3_row_note exactly once for the E3 row: " + repr(len(notes)))
    note_kwargs = {kw.arg: kw.value for kw in notes[0].keywords}
    assert isinstance(note_kwargs.get("e3_result"), ast.Name) and note_kwargs[
        "e3_result"].id == "e3_result", (
        "main feeds e3_row_note something other than the E3 outcome dict — the row's note is the "
        "third disclosure site, and `e3_result.get(...) or ()` makes a mis-wire SILENT: the leg "
        "vanishes from `row E3 note:` while the bar and the log line still carry it: "
        + ast.dump(notes[0]))


def test_the_empty_leg_line_and_note_are_derived_from_the_census():
    """Both disclosures are DERIVED, and the arms are discriminated in both directions.

    The row note must gain the clause when a leg is empty and lose it when none is (an
    implementation that always appended it, or never did, fails here). The log line must read the
    census it is printed beside: this bundle's own render fixture builds the E3 row by hand over a
    canned census whose `Document` leg is 0, so a line taken from the ROW would print "every
    registered class carried nodes" two lines below `e3 label=Document nodes=0`.
    """
    full_census = {"Point": {"nodes": 10, "anchored": 10},
                   "Document": {"nodes": 1, "anchored": 1},
                   "Event": {"nodes": 1, "anchored": 1}}
    assert "=none" in measure.e3_legs_line(full_census), measure.e3_legs_line(full_census)
    empty_census = {"Point": {"nodes": 10, "anchored": 10},
                    "Document": {"nodes": 0, "anchored": 0},
                    "Event": {"nodes": 1, "anchored": 1}}
    line = measure.e3_legs_line(empty_census)
    assert "Document" in line and "narrower than the registered one" in line, line
    # an ABSENT census entry is empty, not "fine" — the same rule the detector applies
    assert "Document" in measure.e3_legs_line({"Point": {"nodes": 1, "anchored": 1}})

    graph, corpus_sha = "capstone_measure_test_1", "sha256:" + "f" * 64
    noted = measure.e3_row_note(
        composition=_shipped_composition(),
        e3_result=measure.e3_outcome(anchored=30, derived=30, bars=BARS,
                                     unmeasured_legs=("Document",)),
        graph=graph, corpus_sha=corpus_sha)
    assert "NARROWER THAN THE REGISTRATION" in noted, noted
    assert "Document" in noted.split("NARROWER THAN THE REGISTRATION", 1)[1][:200], noted
    clean = measure.e3_row_note(
        composition=_shipped_composition(),
        e3_result=measure.e3_outcome(anchored=30, derived=30, bars=BARS),
        graph=graph, corpus_sha=corpus_sha)
    assert "NARROWER THAN THE REGISTRATION" not in clean, clean


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

#: The E4 row fixture's artifact: a receipt ON the run's own corpus, because
#: `build_e4_row` REFUSES anything else — handing E4 the retired off-pin constant here
#: would re-assert the defect this fixture is meant to model (a row whose note says
#: "the SAME receipt, on the SAME corpus" while its fields declare another revision).
_E4_ON_PIN = "tests/eval/write_path/receipts/w2b-operator-2026-10-24-8503-b.json"


def _rows(out_path: str = "docs/evidence/9889/e1-e2-scores.json",
          *, e2_verdict: str = "RED",
          log_corpus_sha: str = "sha256:" + "d" * 64,
          e4: dict | None = None) -> list[dict]:
    parsed = measure.measure_e1_e2(_REPO)
    return measure.build_rows(
        head="a" * 40, out_path=out_path, log_corpus_sha=log_corpus_sha,
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
        e4={"verdict": "RED", "numerator": 5, "denominator": 15,
            "sha": "a999be41e", "corpus_sha256": log_corpus_sha,
            "evidence": _E4_ON_PIN, "note": "E4 note"} if e4 is None else e4,
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
    assert rows["E4"]["evidence"] == _E4_ON_PIN
    assert rows["E5"]["evidence"] == measure.E5_RESCORE


def test_a_row_measured_on_another_corpus_declares_it():
    """A row measured on another revision declares it — and E1/E2/E4 no longer do.

    The declaration is what V7 checks against the artifact's own text, so a row
    that carries ``corpus_sha256`` differing from the log's must also carry
    ``artifact_corpus_sha256``.

    ⛔ E1/E2 are NOT in that set any more: they measure the FROZEN corpus
    (`#9889`), so their artifact digest IS the run's and a declaration nothing
    needs would be a claim the gate then checks.

    ⛔ E4 is deliberately NOT in that set either: ``build_e4_row`` refuses a
    receipt whose ``corpus_hash`` is not the frozen pin, so on the real path E4's
    row IS the run's corpus and the declaration must be ABSENT. It was emitted
    unconditionally while E4 read the off-pin constant, and it survived the
    selector — asserting "a different revision" about the one row that cannot be
    scored on one, in a field V7 never reads (its same-corpus arm short-circuits
    first). The off-pin shape is still covered, by handing ``build_rows`` an
    off-pin ``e4`` directly: the condition is on the operands, not a hard ``None``
    that would also neuter the rule.
    """
    log_corpus = "sha256:" + "d" * 64
    rows = {row["criterion"]: row for row in _rows()}
    for criterion in ("E1", "E2"):
        # E1/E2 now measure the FROZEN corpus, so their artifact digest IS the
        # run's — the V7 declaration is no longer needed and must not appear.
        assert "artifact_corpus_sha256" not in rows[criterion], criterion
    for criterion in ("E3", "E4", "E5"):
        assert rows[criterion]["corpus_sha256"] == log_corpus, criterion
        assert "artifact_corpus_sha256" not in rows[criterion], criterion

    off_pin = {row["criterion"]: row for row in _rows(e4={
        "verdict": "RED", "numerator": 2, "denominator": 15,
        "sha": "6164c683c", "corpus_sha256": "sha256:" + "f" * 64,
        "evidence": measure.E4_RECEIPT, "note": "off-pin E4 note"})}
    assert off_pin["E4"]["artifact_corpus_sha256"] == "sha256:" + "f" * 64, \
        off_pin["E4"]
    assert off_pin["E4"]["evidence"] == measure.E4_RECEIPT


def test_the_real_e4_row_declares_no_other_corpus_revision():
    """The row ``main`` actually builds carries NO ``artifact_corpus_sha256``.

    That field told a machine consumer (``run.rows[E4].artifact_corpus_sha256``) that
    E4's artifact measured a different corpus revision than the run froze, while the
    note beside it said the two ARE the same — and V7 could never catch it, because
    ``_same_corpus`` short-circuits before the declaration is read. So the arm went
    from VERIFIED (pre-fix: an off-pin constant whose digest had to appear in the
    artifact) to an unverified, redundant claim on the one row that cannot be scored
    off the pin at all. This drives the real selector -> row -> table path, so a
    reversion reds here and not only in a fixture.

    Non-vacuity: at the previous head ``build_rows`` stamped the field unconditionally
    from ``e4["corpus_sha256"]``, so the first assertion is red there; the fixture case
    above is what reds if the condition becomes a blanket removal.
    """
    pin = _prereg()["corpus"]["fixtures_hash"]
    e4 = measure.build_e4_row(measure.load_e4(_REPO, frozen_pin=pin),
                              bars=measure.parse_bars(_prereg()), corpus_sha=pin)
    rows = {row["criterion"]: row for row in _rows(log_corpus_sha=pin, e4=e4)}
    assert "artifact_corpus_sha256" not in rows["E4"], rows["E4"]
    assert measure._same_corpus(rows["E4"]["corpus_sha256"], pin), rows["E4"]


def test_a_non_string_e4_corpus_yields_a_row_and_not_a_traceback():
    """A synthetic ``e4`` with a non-string corpus produces a ROW, not an exception.

    ``_same_corpus``'s contract is a VERDICT, and it now coerces both operands itself
    for exactly this reason. It did not, and neither did the call sites: a caller
    handing E4 a number escaped as
    ``AttributeError: 'int' object has no attribute 'removeprefix'`` from inside the
    comparison — a traceback where the contract is a row the GATE can report (V7 reds
    a non-string declaration; it is not this function's job to crash).

    Non-vacuity: reds at 6b67974f2 and earlier with that AttributeError.
    """
    rows = {row["criterion"]: row for row in _rows(e4={
        "verdict": "RED", "numerator": 2, "denominator": 15, "sha": "6164c683c",
        "corpus_sha256": 5, "evidence": measure.E4_RECEIPT, "note": "odd note"})}
    assert rows["E4"]["corpus_sha256"] == 5, rows["E4"]
    assert rows["E4"]["artifact_corpus_sha256"] == 5, rows["E4"]


def test_build_e4_row_refuses_rather_than_raising_on_a_non_string_pin():
    """`build_e4_row`'s GUARD must refuse, not raise, on a non-string pin.

    The guard's whole job is a named refusal, and it called ``_same_corpus`` with the
    caller-supplied pin RAW (only the receipt operand was wrapped), so
    ``corpus_sha=12345`` raised ``AttributeError: 'int' object has no attribute
    'removeprefix'`` from INSIDE the `if not _same_corpus(...)` that exists to produce
    the refusal. Not reachable from `main` (`check_corpus` returns a string), hence its
    own arm rather than a real-run case; the coercion now lives in `_same_corpus`, so no
    call site can differ again.

    Non-vacuity: reds at 325b18901 with that AttributeError.
    """
    pin = _prereg()["corpus"]["fixtures_hash"]
    receipt = measure.load_e4(_REPO, frozen_pin=pin)
    with pytest.raises(SystemExit) as exc:
        measure.build_e4_row(receipt, bars=measure.parse_bars(_prereg()),
                             corpus_sha=12345)
    assert "REFUSED" in str(exc.value), str(exc.value)


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


def test_the_record_does_not_narrate_an_under_floor_after_arm_as_a_disagreement():
    """#10610 review issue 2, third consequence: `why` contradicted `outcome`.

    `e1_pairings` folds the floor into `decidable`, so a below-floor after arm had
    `outcome: REFUSED` beside a `why` reading "every before realization agrees on the
    pairing" — a pass condition printed against a refusal. The floor is now narrated as
    the floor, on the record itself.

    Driven through `e1_pairings` DIRECTLY rather than by editing `on-arm.log`: since
    the review at `f12471075` the ON census is reconciled against `e1-gate-drops.json`
    and the log's own `e3 label=Object` line, so a log edit that moves the population
    has to move three artifacts at once — a heavier fixture for a narration rule that is
    a pure function of `(before_arms, after, floors)`. The pure call is also the honest
    subject: this is the branch, not the file.
    """
    bars = measure.parse_bars(
        json.loads((_REPO / "docs/evidence/7949-capstone/preregistration.json")
                   .read_text(encoding="utf-8")))
    floors = {"bars": bars, "e1_objects": 30, "e1_min_before_share": 0.2}
    before = [{"artifact": "docs/evidence/9889/e1-paired-off-arm-1.json",
               "graph": "capstone_off_54922a800_1793378986",
               "share": 0.358974358974359, "objects": 78, "bare_definite": 28,
               "arm": {}},
              {"artifact": "docs/evidence/9889/e1-paired-off-arm-2.json",
               "graph": "capstone_off_54922a800_1793379347",
               "share": 0.5652173913043478, "objects": 69, "bare_definite": 39,
               "arm": {}}]
    after = {"artifact": "docs/evidence/9889/on-arm.log", "share": 13 / 25,
             "objects": 25, "bare_definite": 13}
    e1 = scores_probe.e1_pairings(before, after, floors)
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


def test_an_overflowing_SHARE_FLOOR_refuses_instead_of_tracebacking(
        tmp_path, capsys):
    """#10610 review at `ec7b5e7fe` (P2): the third arm of the refusal contract.

    Every floor this driver reads is a number out of JSON, and `json.loads` returns
    an UNBOUNDED Python int for any integer literal. `_parse_bars` converted only
    `ValueError`, so `denominator_floors.e1_min_before_share = 10**400` reached
    `capstone_measure.share_floor`'s `float(value)` and raised
    `OverflowError: int too large to convert to float` — exit **1** with a traceback,
    from the one loader whose sibling `_count` converts the IDENTICAL value into an
    exit-2 refusal ("AN ARBITRARY-PRECISION INTEGER IS THE INTEGER SIBLING OF
    `Infinity`"). The asymmetry sat inside a single record's floor map.

    Non-vacuity: measured at the pre-fix head (`.venv/bin/python
    docs/evidence/7949-capstone/probes/e1_e2_scores.py --repo <mutated>`) the run
    exited **1** with that traceback; post-fix it exits 2.

    ⛔ WHICH GUARD ANSWERS IT MOVED (#10610 self-audit of the guards at `ec7b5e7fe` and
    `380a2b7a2`; no review at either head raised it — the review recorded at `380a2b7a2`
    returned CLEAN).
    After the non-finite-floor guard landed, an unbounded-magnitude share floor is
    refused in `frozen_denominators` BEFORE `_parse_bars` is reached — so this pin
    now exercises the FLOOR guard and asserts ITS message, because a pin that keeps
    asserting the older wording would be asserting a path this input no longer
    takes. `_parse_bars`'s `OverflowError` arm is kept as defence in depth for a
    bar path or a floor that is added to the parse without the floor validation,
    and its comment says so rather than claiming coverage this pin does not give it.
    """
    repo = _copied_bundle(tmp_path)
    path = repo / "docs/evidence/7949-capstone/preregistration.json"
    prereg = json.loads(path.read_text())
    assert "e1_min_before_share" in prereg["denominator_floors"], (
        "E1's share floor moved — this pin's subject is gone, move the pin with it")
    prereg["denominator_floors"]["e1_min_before_share"] = 10 ** 400
    path.write_text(json.dumps(prereg))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    err = capsys.readouterr().err
    assert "e1_min_before_share" in err, err
    assert "no float can hold" in err, err
    assert "too large to convert to float" in err, err


def test_the_scores_probe_refuses_an_after_arm_on_another_graph(tmp_path, capsys):
    """#10610 review issue 4: the invariant's other half, actually implemented.

    The module states "one record, one head" and checked only the BEFORE arms and
    the E2a<->gate-drops pair. The ON run's own `# graph:` line was parsed for
    nothing, so an after share from graph A beside E2a counts from graph B passed
    every check: the head is equal, and both before graphs still name it.

    ⛔ THE GRAPH MUST DIFFER BY ITS EPOCH, NOT BY A NON-NUMERIC TAIL (`#10996`
    review). The fixture used `capstone_on_54922a800_other`, whose HEAD TOKEN is
    still the right one — but `_names_head` requires the last segment to be a
    numeric epoch, so `other` fails the shape test and `score()`'s
    graph-names-its-own-head guard (`#10996`, `e1_e2_scores.py:1773`) refused it
    FIRST. With `pass` in place of that guard the earlier mutation runs showed
    it, and with the guard live the assertion below was satisfied by ITS message
    — which also contains the graph string. So this pin was measuring the wrong
    guard, and deleting the identity check it is named for left the suite green.
    An epoch-differing graph (`…_1793378319` vs the log's `…_1793378318`) passes
    the head guard and can ONLY be refused by the identity check, which is what
    the assertion now keys on.
    """
    repo = _copied_bundle(tmp_path)
    log = repo / "docs/evidence/9889/on-arm.log"
    other = "capstone_on_54922a800_1793378319"
    log.write_text(re.sub(r"^# graph: \S+", f"# graph: {other}",
                          log.read_text(), count=1, flags=re.MULTILINE))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    err = capsys.readouterr().err
    assert other in err, err
    assert "come from different runs" in err, (
        "the refusal came from another guard — this pin is about the graph "
        f"IDENTITY check (`after[\"graph\"] != e2a_record[\"graph\"]`): {err}")


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


def test_the_scores_probe_refuses_an_e2a_and_gate_drops_pair_from_two_graphs(
        tmp_path, capsys):
    """The E2a <-> gate-drops graph IDENTITY, with the head loop out of the way.

    #10610 review at `21b2af22` (P3). The neighbour above moves all three cited records to
    ONE foreign graph, so the head loop refuses first and the equality check
    (`e2a_record.get("graph") != gates_record.get("graph")`) is never the reason — deleting
    that check left every test green. The two records carry a head and a bare `<head8>`; the
    graph name is `capstone_on_<head8>_<stamp>`, so a SIBLING run of the SAME head is a
    DIFFERENT graph with an equal head. That is the only shape that isolates this guard.

    Non-vacuity: at the pre-fix head this bundle scores, because both records still name the
    ON head and nothing compares their graphs to each other.
    """
    repo = _copied_bundle(tmp_path)
    path = repo / "docs/evidence/9889" / scores_probe.GATE_DROPS
    record = json.loads(path.read_text())
    on_graph = record["graph"]
    assert on_graph.startswith("capstone_on_"), on_graph
    sibling = on_graph.rsplit("_", 1)[0] + "_1799999999"
    assert sibling != on_graph and sibling.startswith("capstone_on_"), sibling
    record["graph"] = sibling
    path.write_text(json.dumps(record))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    err = capsys.readouterr().err
    assert "come from different graphs" in err, err
    assert sibling in err and on_graph in err, err


def _falsifier_inflated(repo: Path) -> None:
    """Make the falsifier's blocks agree with an E2a record that reaches its bar."""
    falsifier_path = repo / "docs/evidence/9889" / scores_probe.E2A_FALSIFIER
    falsifier = json.loads(falsifier_path.read_text())
    for key in ("with_anchor", "as_written"):
        block = falsifier[key]
        for row in block["per_session"].values():
            row["value_survived"] = list(row["value_ids"])
            # Survivors AND the complement, because the census is a PARTITION of
            # `value_ids` (a unit listed as both survived and lost is refused).
            # Leaving `value_lost` untouched made the "inflated to the bar" state
            # illegal — the committed `as_written` block carries 1, 3 and 2 lost
            # units, so those units were listed as BOTH survived and lost, which
            # `_census_reconcile`'s partition rule refuses. Same line the sibling
            # fixture in `test_the_value_gate_liveness_refusal_is_not_a_truthiness_test`
            # already carried (#10610 review at `13db35981`, P3).
            row["value_lost"] = []
        block["value_survived"] = block["value_units"]
    falsifier_path.write_text(json.dumps(falsifier))


def test_the_inflated_falsifier_fixture_is_a_legal_partition(tmp_path):
    """#10610 review at `13db35981` (P3): the helper wrote a fixture the driver REFUSES.

    `_falsifier_inflated` set each row's `value_survived = list(value_ids)` but left
    `value_lost` untouched, and the committed `as_written` block carries `value_lost`
    of 1, 3 and 2 units — so the "inflated to the bar" state listed those units as
    BOTH survived and lost, which `_census_reconcile`'s partition rule refuses
    (`probes/e1_e2_scores.py`). No live consequence before the fix: in both callers
    the E2a guard fires first and each test asserts that guard's exact wording, so a
    future guard REORDER would have let those pins pass for the wrong reason.

    MUTATION THAT REDS THIS: delete `row["value_lost"] = []` from
    `_falsifier_inflated` — the pre-fix fixture is reported verbatim.
    """
    repo = _bundle(tmp_path)
    _falsifier_inflated(repo)
    falsifier = json.loads(
        (repo / "docs/evidence/9889" / scores_probe.E2A_FALSIFIER).read_text())
    for key in ("with_anchor", "as_written"):
        for session_id, row in falsifier[key]["per_session"].items():
            both = sorted(set(row["value_survived"]) & set(row["value_lost"]))
            assert not both, (
                f"{key}/{session_id}: {both} listed as BOTH survived and lost — "
                "the fixture is not a partition of `value_ids`, so the driver's "
                "census reconciliation refuses it instead of scoring the arm")


@pytest.mark.parametrize("mutation", [
    {"value_survived": 20},        # a numerator above its own denominator
    {"value_survived": float("inf")},
    {"value_survived": "8"},       # not a number at all
    {"value_survived": 8.5},       # a ratio wearing a count's name
    {"value_survived": -1},
])
def test_the_e2a_counter_is_a_count_not_a_token(mutation, tmp_path, capsys):
    """#10610 review at `75bfd9c6` (P1): E2a's counter was compared straight to its bar.

    Three mutations went GREEN before `_count` existed: `value_survived: 20` against the
    frozen 14 wrote `E2 PASS — E2a 20/14`; `Infinity` (JSON carries the literal, and
    `int.__ge__` accepts it) wrote `E2 PASS — E2a inf/14`; and `"8"` escaped as
    `TypeError: '>=' not supported between instances of 'str' and 'int'` — exit 1 with a
    traceback, which an operator cannot tell from a crash, not the exit-2 refusal the
    module docstring promises. This is the same pair of checks `_consistent_share` got in
    the two previous rounds, on the counter nobody looked at.

    Non-vacuity, MEASURED against the pre-fix probe (`75bfd9c6e`): the `"8"` arm escapes
    as `TypeError` (exit 1, no `REFUSED` line), and the numeric arms reach the
    `as_written` cross-check, whose message names the record's own contradiction. The
    assertion below therefore keys on the REFUSAL ARRIVING AT ALL plus `value_survived`
    in the reason rather than on `_count`'s exact wording — the guard's own arms are what
    the four numeric cases cover once the record is otherwise consistent.
    """
    repo = _bundle(tmp_path)
    path = repo / "docs/evidence/9889/e2a-value-units.json"
    record = json.loads(path.read_text())
    record.update(mutation)
    path.write_text(json.dumps(record))
    _falsifier_inflated(repo)
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    err = capsys.readouterr().err
    assert "value_survived" in err, err
    if isinstance(mutation["value_survived"], (int, float)) \
            and not isinstance(mutation["value_survived"], bool):
        # The numeric arms must be refused by `_count` ITSELF, not by the as-written
        # cross-check that happens to name the same field: without this the pin would
        # stay green with `_count` deleted whenever the falsifier disagrees first.
        assert ("not a finite number" in err or "not a whole number" in err
                or "not a subset" in err or "of a population of" in err
                or "is negative" in err), err


def test_the_e2a_totals_are_the_census_they_re_summed_from(tmp_path, capsys):
    """#10610 review at `75bfd9c6` (P1): the totals were never reconciled with the census.

    The record carries the per-session census `e2a_value_units.py` sums the totals from,
    and the driver read the totals and ignored it. So ONE field edit to the file a lane
    would reach for to unblock the row — `value_survived: 14`, `per_session` untouched —
    wrote `E2 PASS — E2a 14/14` while the record's own detail still said 8 units survived.
    The shifty version of a forged green: no other file touched, and the artifact
    contradicts itself where nobody was looking.

    Non-vacuity: at the pre-fix head this bundle scores `PASS`, exit 0.
    """
    repo = _bundle(tmp_path)
    record = json.loads((repo / "docs/evidence/9889/e2a-value-units.json").read_text())
    census = sum(len(row["value_survived"]) for row in record["per_session"].values())
    assert census == 8 and record["value_units"] == 14, record["value_survived"]
    record["value_survived"] = 14
    (repo / "docs/evidence/9889/e2a-value-units.json").write_text(json.dumps(record))
    _falsifier_inflated(repo)
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    err = capsys.readouterr().err
    assert "per_session" in err and "census sums to 8 of 14" in err, err


def test_a_graph_names_the_head_in_the_heads_own_position(tmp_path, capsys):
    """#10610 review at `75bfd9c6` (P3): `head[:8] in graph` is not a name test.

    The comment claimed the substring form "still rejects a graph naming a different head".
    Measured: an OFF arm whose graph was set to `capstone_off_9152448d_54922a80` — a graph
    naming head `9152448d` with our own eight characters merely APPENDED — scored, and that
    arm's share became a before realization of head `54922a80`. The producers' documented
    shape is parsed instead (`<name>_<head>_<epoch>`, the head token immediately before the
    numeric epoch) and the token is compared, so a name that does not parse refuses.

    Non-vacuity: at the pre-fix head this bundle scores, exit 0.
    """
    repo = _bundle(tmp_path)
    path = repo / "docs/evidence/9889/e1-paired-off-arm-1.json"
    record = json.loads(path.read_text())
    # The arm records carry no `head` field; the head is the graph's own middle token.
    head = record["graph"].split("_")[-2]
    assert head.startswith("54922a800"), head
    record["graph"] = "capstone_off_9152448d_" + head[:8]
    _shipped_arm(record)          # …so the ONLY defect is the graph's position
    path.write_text(json.dumps(record))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    err = capsys.readouterr().err
    assert "does not name head" in err, err


def test_the_value_gate_liveness_refusal_is_not_a_truthiness_test(tmp_path, capsys):
    """A record that says its positive controls are DEAD must refuse — including `"false"`.

    #10610 review at `21b2af22` (P2, a FAIL-OPEN). The guard read
    `if not gates.get("predicate_alive")`, which refuses `false`/`null`/absent but ACCEPTS
    the STRING `"false"` — and the E2a bar is separately satisfied at 13/14 here, so the
    measured outcome was `e2 = PASS`: a GREEN row whose own artifact states that the positive
    controls no longer drop. That is the exact shape the falsifier's `injected_anchor` guard
    one function over already refuses with `is not True`.

    `False`, `None`, an ABSENT key and the STRING `"false"` are each armed alone: the first
    three are refused by both forms, and the string is the case that tells them apart — a
    truthiness test accepts it (a non-empty string is truthy), `is not True` refuses it.
    """
    for declared, marker in ((False, "bool"), (None, "null"), ("absent", "absent"),
                             ("false", "string")):
        repo = _copied_bundle(tmp_path / marker)
        e2a_path = repo / "docs/evidence/9889" / scores_probe.E2A_RECORD
        e2a = json.loads(e2a_path.read_text())
        assert e2a["value_units"] == 14, e2a          # the bar is ">= 13 of 14"
        # The bar is MET — and the record's own `per_session` census has to agree
        # with it, because the driver reconciles the totals against the census
        # (an edit to the total ALONE is refused there, so a fixture that only
        # bumped the total would be testing the wrong guard).
        for row in e2a["per_session"].values():
            # Survivors AND the complement, because the census is a PARTITION of
            # `value_ids` (a unit listed as both survived and lost is refused).
            row["value_survived"] = list(row["value_ids"])
            row["value_lost"] = []
        e2a["value_survived"] = e2a["value_units"]
        e2a_path.write_text(json.dumps(e2a))
        # ...and the FALSIFIER's own blocks must agree, or its cross-check refuses first and
        # this test would be proving the wrong arm had fired.
        falsifier_path = repo / "docs/evidence/9889" / scores_probe.E2A_FALSIFIER
        falsifier = json.loads(falsifier_path.read_text())
        for key in ("with_anchor", "as_written"):
            block = falsifier[key]
            for row in block["per_session"].values():
                row["value_survived"] = list(row["value_ids"])
                row["value_lost"] = []
            block["value_survived"] = block["value_units"]
        falsifier_path.write_text(json.dumps(falsifier))
        path = repo / "docs/evidence/9889" / scores_probe.GATE_DROPS
        record = json.loads(path.read_text())
        assert record["predicate_alive"] is True, record["predicate_alive"]
        if declared == "absent":
            record.pop("predicate_alive")
        else:
            record["predicate_alive"] = declared
        path.write_text(json.dumps(record))
        with pytest.raises(SystemExit) as refused:
            scores_probe.score(repo)
        assert refused.value.code == 2, (marker, refused.value)
        err = capsys.readouterr().err
        assert "positive controls no longer drop" in err, (marker, err)


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
    # ⛔ The receipt is selected BY the frozen pin (#10679): `load_e4` refuses an
    # unpinned call, because a receipt whose corpus cannot be checked is not
    # evidence for a bar written over the pre-registered corpus. This pin renders
    # the committed record, so it hands the selector the record's own pin.
    e4 = measure.load_e4(
        _REPO, frozen_pin=_prereg()["corpus"]["fixtures_hash"])

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
    # #9736: the empty-leg line is read from the CENSUS, not from the `e3_out` built by hand above
    # it. This fixture's canned census carries `Document` = 0 nodes while `e3_out` was constructed
    # WITHOUT the legs, so a line taken from the row would print "=none" here — two lines below
    # `e3 label=Document nodes=0`. That is the escape this assertion closes.
    assert "e3 unmeasured_legs=Document" in text, text[-2000:]
    assert "e1_min_before_share 0.2" in text, text[-3000:]
    # The number is READ, not typed: the same render over a moved floor moves it.
    moved = _prereg()
    moved["denominator_floors"]["e1_min_before_share"] = 0.35
    assert "e1_min_before_share 0.35" in render(moved)


def _record_matches(produced, committed: bytes) -> tuple[bool, bool]:
    """The committed-record pin's comparison, factored — ``(VALUE, BYTES)``.

    Factored out so the non-vacuity arm inside the pin below drives the SAME
    expression the pin does. ⛔ An arm that re-states the comparison (a
    ``json.dumps``/``json.loads`` round-trip beside it) is evidence about the
    ``json`` module, not about this pin: the review at `8bc8be1e7` found exactly
    that — the arm could not fail if the byte half were deleted (#10610).
    """
    return (
        produced == json.loads(committed),
        (json.dumps(produced, indent=1) + "\n").encode() == committed,
    )


def test_the_committed_score_record_is_what_the_probe_produces(tmp_path):
    """P2-10: the committed record may not drift from its own inputs.

    Without this, `e1b`, `e2b`, `gate_drops` and `denominators` could be
    hand-edited (or fall behind a probe change) while every other pin stayed
    green — the artifact would then be a claim, not a measurement.

    ⛔ The claim this pin is CITED for is byte-level — `config/ci-surfaces.yml`'s
    `durations` note, item 72: *"reproduce `e1-e2-scores.json` byte-for-byte"* —
    and `==` over two parsed values does not establish it: key order and
    formatting are not preserved by value equality, so a reformat or a
    renumbered-key hand edit passed the value pin while a maintainer reading the
    note believed the bytes had been checked (#10610 review at `48187660a`). The
    producer writes `json.dumps(record, indent=1) + "\n"` in its `main()`
    (`probes/e1_e2_scores.py`, the `Path(args.out).write_text(...)` line — sited by
    its expression rather than by a line number, because that number moved three
    times during this PR's review rounds and nothing sweeps it: #10610 review at
    `fe77fb13f`, P3), so the byte property is exactly that expression.

    The comparison is over BYTES, not `read_text()`: universal-newline
    translation would make a CRLF-rewritten record read back identical to the
    producer's LF output, which is precisely the serialization drift this half
    exists to refuse. Do not "simplify" it back to `read_text()`.

    VALUE is asserted first and separately, because a drifted number should name
    the region that moved instead of printing a whole-file diff.
    """
    produced = scores_probe.score(_REPO)
    committed = SCORES.read_bytes()
    value_matches, bytes_match = _record_matches(produced, committed)
    assert value_matches
    assert bytes_match, (
        "the committed record is value-equal but NOT byte-equal to what the "
        "probe writes — its serialization (indent=1, one trailing newline) "
        "drifted, which the value half cannot see")

    # Non-vacuity, measured through the SAME expression the pin uses: a
    # VALUE-equal / BYTE-different copy is exactly what the value half cannot
    # refuse (a reformat to `indent=2`), and the byte half can. Deleting the
    # byte half of `_record_matches` reds THIS assertion — an arm that re-stated
    # the comparison could not, which is the state the review found (#10610).
    reformatted = tmp_path / SCORES.name
    reformatted.write_text(json.dumps(produced, indent=2) + "\n")
    assert _record_matches(produced, reformatted.read_bytes()) == (True, False), (
        "the byte half of the pin accepts a record the producer would not have "
        "written — the `indent=1` + single-trailing-newline serialization claim "
        "is then unprotected, however green the value half looks")


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
    # the run completed and #9116's seam is in: ALL EIGHT steps are witnessed
    # (#10537 re-measure, 2026-11-02 — the superseded `e0fc940b` run could see six
    # because the meta fold forwarded `stats` alone)
    assert record["run"]["status"] == "COMPLETED"
    assert sorted(record["run"]["steps_observed"]) == sorted(
        ["S0", "S1", "S2.1", "S2.2", "S2.3", "S3", "S5", "S6"])
    assert not record["run"].get("steps_unobserved"), record["run"]
    # and the epic stays uncloseable
    codes = [code for code, _w, _m in cvc.check(
        record, repo=_REPO, require_green=True).items]
    assert codes == ["V9"], codes


def test_the_shipped_logs_blocker_cells_are_the_emitters_own():
    """The scored log must carry the `blocked_by` cells its OWN probe emits.

    ⛔ WHY THIS EXISTS (#10537 review P1). The shipped log's E4/E5 cells named
    `#9596`/`#5087`, but the probe at the head the log NAMES (`9152448d7`) still
    emitted `#8503`/`#9738` — the two cells had been edited after the run, and
    the log's E5 note carried a sentence no committed revision of the probe ever
    wrote (`git grep` for it at `9152448d7` finds nothing; `git log --all -S`
    finds only this lane's own commits). The consequence is not cosmetic: a copy
    of the shipped log with those cells set back to what the stamped probe emits
    is REFUSED by this PR's own V12 — so the shipped table passed only because
    of the edit, and nothing pinned artifact to emitter.

    This pin closes that: the cells must be the emitter's values, in row order,
    and the note sentence must exist in the emitter too. At the pre-fix head it
    reds on BOTH legs.
    """
    source = PROBE.read_text()
    emitted = re.findall(r'blocked_by="(#[0-9]+)"', source)
    assert len(emitted) == 5, (
        f"expected the probe to emit five `blocked_by=` cells (E1-E5), found "
        f"{emitted} — this pin reads them in ROW ORDER, so a reordered literal "
        f"list is a pin failure, not a formatting choice")
    log = _newest_log(_scored_logs())
    record = cvc.extract_block(log.read_text())
    shipped = [row["blocked_by"] for row in record["rows"]]
    assert shipped == emitted, (
        f"{log.name} carries blocked_by {shipped} but its own probe emits "
        f"{emitted} — the log names this emitter, so a cell that does not match "
        f"it was edited after the run. Either re-run the probe (the supersede "
        f"path) or disclose the edit in the log's POST-RUN CORRECTION block")
    # the E5 attribution sentence is the emitter's words, not a hand-typed tail.
    # The probe wraps it across adjacent f-string literals, so join those first:
    # a pin that demanded one physical source line would be testing the FORMATTING.
    source_joined = re.sub(r'"\s*\n\s*f?"', "", source)
    sentence = "The row's blocker moved on 2026-11-02 from #9738 to #5087"
    assert sentence in source_joined, (
        "the E5 blocker-move sentence is NOT in the committed probe, so the log "
        "carries words no run wrote — move it into the probe's E5 note template")
    assert sentence in record["rows"][4]["note"], record["rows"][4]["note"]
    # AND THE E4 NOTE (#10601 review round 4): the disclosure pin's stand-down
    # rests on this pin refusing an undisclosed note edit, so the note the
    # correction block NAMES has to be pinned too — otherwise a hand-edit of E4's
    # note, with the block left in place, passes both pins, which is exactly the
    # "edit that reads as emitter output" class #10537 exists to refuse.
    e4_sentence = ("that closure records the DISPROOF, so it is this row's "
                   "measurement record")
    # ⛔ THE FILE-WIDE SEARCH BELOW IS NOT THE BINDING — it is a fast pre-check, and on
    # its own it is VACUOUS (`#10679` review round 6, measured). The phrase occurs
    # TWICE in the joined source: in the reachable `elif` arm AND in `build_e4_row`'s
    # `else` arm, which no committed receipt can reach (`edge_correct >= planted` with
    # an outcome that is not PASS). Rewriting the REACHABLE arm's clause to
    # "… and IS a live blocker again …" left the whole file green (163 passed at
    # `b132e1a28`, the head that review ran on — the file collects more at this head,
    # so the count is dated rather than restated), while
    # `build_e4_row` emitted exactly that sentence into every future scored log. The
    # BEHAVIOURAL binding is asserted on `built["note"]` further down, beside the
    # sibling `unowned` clause round 3 fixed the same way; this line only catches a
    # probe that has lost the sentence entirely.
    assert e4_sentence in source_joined, (
        "the E4 note's closed-blocker sentence is NOT in the committed probe at all, "
        "so a hand-typed E4 note would still pass")
    assert e4_sentence in record["rows"][3]["note"], record["rows"][3]["note"]

    # ⛔ THE CORRECTED RESIDUAL CLAUSE IS PINNED TOO, AND AGAINST THE EMITTED NOTE
    # (`#10679` review rounds 2 and 3). Round 2 established that the token above is
    # not enough: it sits EARLIER in the same sentence, so a log hand-edited back to
    # "the residual is owned by #9596" kept it intact and the suite stayed green.
    #
    # ⛔ Round 2's first fix for that was ITSELF VACUOUS, and round 3 measured it: an
    # `assert unowned in source_joined` searches the WHOLE FILE, so it was satisfied by
    # `build_e4_row`'s THIRD arm (the planted-count-shortfall clause) — an arm no
    # committed receipt can reach, because every receipt reads `edge_correct 5 <
    # planted 15` and takes the `elif`. Reverting the `elif`'s clause to the refuted
    # attribution therefore left the suite green while the emitter would print it into
    # every future scored log.
    #
    # So this binds BEHAVIOUR, not text: drive `build_e4_row` with the receipt the
    # committed tree actually selects and assert on the NOTE IT EMITS. That is the
    # sentence a scored log will carry, and no unreachable arm can satisfy it.
    pin = _prereg()["corpus"]["fixtures_hash"]
    real = measure.load_e4(_REPO, frozen_pin=pin)
    built = measure.build_e4_row(real, bars=measure.parse_bars(_prereg()),
                                 corpus_sha=pin)
    assert built["verdict"] == "RED" and built["numerator"] < built["denominator"], (
        f"this arm binds the SHORTFALL branch, and the committed receipt no longer "
        f"takes it: {built['verdict']} {built['numerator']}/{built['denominator']}")
    unowned = "no open issue owns the residual"
    assert unowned in built["note"], (
        "the note `build_e4_row` EMITS for the committed receipt does not say the "
        "residual is unowned — #9596 owns the receipt's retrievability gap, not the "
        f"planted-edge residual, and #8503's own body says so: {built['note']}")
    assert "residual is owned by" not in built["note"], (
        f"the emitted note carries the refuted ownership attribution: {built['note']}")
    assert unowned in record["rows"][3]["note"].lower(), (
        "the shipped log's E4 note still attributes the residual to an owner: "
        f"{record['rows'][3]['note']}")
    assert "residual is owned by" not in record["rows"][3]["note"], (
        "the shipped log's E4 note carries the refuted '#9596 owns the residual' "
        f"attribution: {record['rows'][3]['note']}")
    # ⛔ AND THE CLOSED-BLOCKER SENTENCE ITSELF, BOUND BEHAVIOURALLY (`#10679` review
    # round 6). The file-wide pre-check above cannot see a rewrite of the REACHABLE
    # arm, because the same phrase survives in the unreachable one — measured: the
    # refuted "… and IS a live blocker again …" stayed green (163 passed at
    # `b132e1a28`) while the
    # emitter printed it. Binding `built["note"]` closes that, and the negative arm
    # below (`"not a live blocker"`) is what makes the binding mean something rather
    # than just "the phrase is still somewhere in the sentence".
    assert e4_sentence in built["note"], (
        "the note `build_e4_row` EMITS for the committed receipt does not carry the "
        f"closed-blocker sentence, so the shipped log's copy is hand-typed: {built['note']}")
    assert "not a live blocker" in built["note"], (
        "the emitted note lost the `#8503` closure reading — that issue CLOSED and its "
        f"closure records E4's DISPROOF, so it is not a live blocker: {built['note']}")
    assert e4_sentence in record["rows"][3]["note"], (
        "the shipped log's E4 note lost the closed-blocker sentence: "
        f"{record['rows'][3]['note']}")
    assert "not a live blocker" in record["rows"][3]["note"], (
        "the shipped log's E4 note no longer says `#8503` is not a live blocker: "
        f"{record['rows'][3]['note']}")


def test_the_shipped_log_discloses_what_the_run_did_not_emit():
    """A post-run edit is allowed only when the artifact SAYS it happened.

    The failure mode is not the edit — it is an edit that reads as emitter
    output, which is the whole class of stale evidence `#10537` exists to gate.
    Three things were updated after the run at `9152448d7` — the E4/E5 `blocked_by`
    cells, the E5 note tail, and the E4 note's `#8503` pointer — so the log must
    name all three, name the reason, and say that the NUMBERS were not touched.

    ⛔ AND IT STANDS DOWN WHEN THERE IS NOTHING TO DISCLOSE (#10601 review round
    3). The first version of this pin REQUIRED the block unconditionally, which
    turned the bundle's own sanctioned remedy — re-run the probe and delete the
    log it supersedes — red on a strictly MORE correct artifact, and the only
    cheap way to satisfy it would be to carry a FALSE disclosure into the new
    file. The neighbouring artifact pin stands down for exactly this reason. The
    protection is unchanged, because the two pins together still refuse an
    undisclosed edit: `test_the_shipped_logs_blocker_cells_are_the_emitters_own`
    reds WHENEVER the five `blocked_by` cells, the E4 note's closed-blocker
    sentence OR the E5 blocker-move sentence diverge from the emitter (those
    three are the surfaces the correction block names, so the block cannot
    confess an edit the artifact does not carry), and this pin reds whenever a
    block claims the edit but does not describe it. What is no
    longer required is a confession for an edit that never happened.
    """
    log = _newest_log(_scored_logs())
    text = log.read_text()
    if "POST-RUN CORRECTION" not in text:
        # Nothing to disclose — a freshly generated log is the good case. The
        # sibling pin is what makes this branch safe rather than a hole: it
        # reds the moment the artifact stops being what its emitter writes.
        return
    for named in ("#8503 -> #9596", "#9738 -> #5087", "E5 note", "E4 note"):
        assert named in text, f"the correction block does not name {named!r}"
    assert "THE NUMBERS ARE THE RUN'S AND WERE NOT TOUCHED" in text, (
        "the disclosure must state the scope of the edit: numbers untouched")
def test_the_scored_logs_pre_repoint_rows_are_disclosed():
    """#10610 review at `2d9e8608` (P3): a frozen log is not re-rendered — so DISCLOSE it.

    `#9889` item 5 moved the E1/E2 rows off `e1-e2.log`, and this README says so — but the
    artifact `capstone_verdict_check.py` is run against is the SCORED log, which is frozen
    at its own stamp, and §"Two artifacts" below forbids editing it (a row moves only by
    RE-RUNNING the probe). So the shipped log still carries both rows on the hard-coded
    name — the eleven-day state `#9889` exists to end, and the only reading an artifact gave.

    The fix is disclosure, and this pin holds the disclosure to the log's ACTUAL rows in
    BOTH directions: while the rows are pre-re-point the artifact table must say so, and
    the README's "cited by no row" / "no longer read" claims must be qualified to the rows
    the builder EMITS; once a re-recorded log carries the record's rows, the disclosure
    must go rather than survive as a stale sentence. Neither direction is assumed: the
    premise is read out of the committed log, and the STALENESS is read out of that log's
    own E1/E2 section header, which the current builder writes differently — so the word
    `PREDATE` cannot go stale here without the log itself changing first.

    ⚠️ The blocker named in this docstring is DERIVED, not typed: the shipped log's E2
    `blocked_by` moved from the now-CLOSED `#9775` to the live `#10360` when the 2026-11-02
    re-measure (`#10601`) landed, and a pin that demanded one spelling would red on an
    artifact that is MORE correct than the one it was written against.

    Non-vacuity: at the pre-fix head the artifact table carried no such line and the three
    claims were unqualified, so both legs red (measured).
    """
    readme = (_REPO / "docs/evidence/7949-capstone/README.md").read_text()
    log = _newest_log(_scored_logs())
    record = cvc.extract_block(log.read_text())
    rows = {row["criterion"]: row for row in record["rows"]}
    superseded = "docs/evidence/7949-capstone/e1-e2.log"
    stale = sorted(c for c in ("E1", "E2") if rows[c]["evidence"] == superseded)
    assert stale in ([], ["E1", "E2"]), (
        "only ONE of the shipped log's E1/E2 rows is pre-re-point — a state no stamp can "
        f"describe: { {c: rows[c]['evidence'] for c in ('E1', 'E2')} }")

    flat = [re.sub(r"\s+", " ", line) for line in readme.splitlines()]
    table_rows = [line for line in flat if line.startswith(f"| `{log.name}`")]
    assert len(table_rows) == 1, (
        f"the artifact table no longer describes {log.name} in exactly one row: "
        f"{table_rows} — this leg's subject is gone, so it would pass by vacancy")
    row = table_rows[0]

    # The disclosure's sub-claim is CHECKED, not trusted: the log really is pre-re-point
    # (its own section header is the OLD one, and the committed builder writes the NEW one)
    # and the record really is the builder's evidence — so requiring them here names a state
    # a reader can verify rather than a phrase they must take on faith.
    if stale:
        assert "parsed out of the landed e1-e2.log" in log.read_text(), (
            f"{log.name}'s E1/E2 section no longer reads the pre-re-point header, so its "
            "rows are not stale after all and this leg would be redding on a TRUE "
            "disclosure — flip it to the else branch with the re-record")
        assert "read out of the #9889 measurement record" in PROBE.read_text(), (
            "the committed builder does not emit the post-re-point header, so the two "
            "cannot be told apart and `stale` above proves nothing")
        for anchor in ("PREDATE", "#9889", superseded, rows["E2"]["blocked_by"],
                       "docs/evidence/9889/e1-e2-scores.json"):
            assert anchor in row, (
                f"the artifact table row for {log.name} does not disclose that its E1/E2 "
                f"rows predate the #9889 re-point (missing {anchor!r}). A reader who follows "
                "the evidence chain lands on a superseded log with nothing saying so — the "
                f"row reads: {row[:500]}")
        squashed = re.sub(r"\s+", " ", readme)
        for claim in ("cited by no row", "no longer read `e1-e2.log`"):
            hits = list(re.finditer(re.escape(claim), squashed))
            assert hits, (
                f"the README no longer states {claim!r} at all — this leg would pass by "
                "vacancy on a document that stopped describing the move")
            for match in hits:
                tail = squashed[match.end():match.end() + 60]
                assert "current builder emits" in tail, (
                    f"the README claim {claim!r} is UNQUALIFIED while the FROZEN scored "
                    f"log {log.name} still carries both rows on `e1-e2.log` "
                    f"(E2 blocked_by {rows['E2']['blocked_by']}): {tail!r}\n"
                    "Qualify it to the rows the current builder emits, and let the "
                    "artifact table disclose the frozen rows (a frozen log moves only by "
                    "re-running the probe).")
    else:
        assert "PREDATE" not in row, (
            f"{log.name} now carries the record's E1/E2 rows, so the disclosure that they "
            "predate the #9889 re-point is FALSE — remove it with the re-record")


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
            # pinned OFF so the arm is a function of the probe, not the shell —
            # and pinned to "0", not to absence: since #10996 an UNSET value gate
            # is the ON arm, so a pop would print a header that denies the arm
            # (#10996 review P2). The shell held "1" and it must not survive.
            assert arm["TORTOISE_VALUE_GATE"] == "0", arm
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
#: (`capstone-measured-9152448d.log:32-34` + `:Point` composition).
_CENSUS_ROWS = {
    "Point": (354, 150, 150, 0),
    "Object": (64, 0, 0, 0),
    "Event": (43, 7, 0, 7),
    "Document": (0, 0, 0, 0),
}


class _CannedQuery:
    """A census-shaped fake: the REAL statements, answered per statement."""

    def __init__(self, cypher: str) -> None:
        if "UNWIND labels(n)" in cypher:
            rows = [["Point", 354], ["Object", 64], ["Event", 43]]
        elif "RETURN n.name" in cypher:                                    # object_names
            rows = [["the quarry"]]
        elif "eventId IS NOT NULL" in cypher:                              # event_stamped
            rows = [[184]]
        elif "type(r) AS t" in cypher:                                     # relationships
            # The log's own `e3_census.relationship_counts` / `.relationship_anchored`
            # (`capstone-measured-9152448d.log`, `e3_census.relationship_counts`):
            # `extractedFrom` carries **150** links, all 150 anchored — the same 150
            # claims, one link each. A count equal to the writer-class size
            # (`extraction_claims` + `other` = 161) written into the LINK slot would be
            # wrong, and the 11 `other` Points are defined by `deriv = 0`, i.e. by
            # carrying no link at all, so 150 links is not a shape that graph can have.
            # `test_the_canned_census_transcribes_the_committed_log_it_cites` checks
            # every one of these numbers against the artifact.
            rows = [["extractedFrom", 150, 150], ["references", 7, 7],
                    ["aboutObject", 248, 0]]
        elif "AS points" in cypher:                                        # composition
            # Columns, in `census()`'s own unpack order (#10525 review):
            #   points=turn_rows=operator_points=extraction_claims=other_points=
            #   extraction_claims_anchored=turn_rows_anchored=
            #   operator_points_anchored=other_points_anchored
            # The shipped log's own composition line
            # (`capstone-measured-9152448d.log`, `point_composition`) is
            # `turn_rows=159 (anchored 0) extraction_claims=150 (anchored 150)
            #  operator_points=34 (anchored 0) other=11 (anchored 0) (total=354)`,
            # and it PARTITIONS 354 (159 + 34 + 150 + 11). The `extractedFrom`
            # LINK count is 150 — one link per extraction claim — so the link
            # count and the claim count coincide here; they did NOT at the
            # superseded stamp (141 claims + 9 `other`), which is why the slot is
            # pinned from the artifact instead of from the coincidence.
            rows = [[354, 159, 34, 150, 11, 150, 0, 0, 0]]
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
    assert data["per_label"]["Object"]["nodes"] == 64, data["per_label"]["Object"]
    # The row SELECTOR is unchanged and still names what #9736 registered: `:Object` is not in it.
    assert "Object" not in measure.DERIVED_LABELS, measure.DERIVED_LABELS
    assert set(measure.DERIVED_LABELS) <= set(measure.CENSUS_LABELS)
    # …and E3's totals stay on the ROW's classes: 354 + 43 + 0, never the 461 that
    # folding `:Object` in would report.
    assert data["derived_total"] == 397, data["derived_total"]
    assert data["anchored_total"] == 157, data["anchored_total"]


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
    assert counts["objects"] == 64, counts
    # Read off the log's own composition line, never off the link count:
    # `extraction_claims=150 (anchored 150)` (`capstone-measured-9152448d.log`,
    # `run.e3_census.point_composition`).
    assert counts["extraction_claims"] == 150, counts
    assert counts["events"] == 43, counts
    # 150 + 64 + 43, which is what the shipped log's own classes produce; the 245
    # this pin used to assert came from the mis-transcription above, and the 236
    # from the SUPERSEDED stamp's 141 + 50 + 45.
    assert counts["extraction_nodes"] == 257, counts
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
    # ⛔ DISCOVERED, NOT NAMED (#10601 review P2, same class as the OK-line pin).
    # The bundle's own rule is that a re-run writes `capstone-measured-<new
    # head8>.log` and DELETES the file it supersedes, so a hardcoded name here
    # turns the next CORRECT artifact into a red on a filename. `_newest_log`
    # orders by the log's own `# generated:` stamp, which is what the rest of
    # this file already uses.
    text = _newest_log(_scored_logs()).read_text()
    begin, end = "=== CAPSTONE-VERDICT-BEGIN ===", "=== CAPSTONE-VERDICT-END ==="
    assert begin in text and end in text, "the shipped log lost its verdict block"
    record = json.loads(text.split(begin, 1)[1].split(end, 1)[0])
    block = record["run"]["e3_census"]

    data = _canned_census(monkeypatch)

    # 1. The composition row, column for column (`census()`'s own unpack order).
    assert data["point_composition"] == block["point_composition"], (
        data["point_composition"], block["point_composition"])

    # 2. The per-label block — and `Document` is a real 0 on both sides, not a key
    # the fixture invents.
    for label, expected in block["per_label"].items():
        got = data["per_label"][label]
        assert (got["nodes"], got["anchored"], got["anchored_out"],
                got["anchored_in"]) == (
                    expected["nodes"], expected["anchored"],
                    expected["anchored_out"], expected["anchored_in"]), (label, got)
    # `:Document` is a REAL 0 the log itself carries (it is in the amended class set,
    # #9736 §10.1) — so the fixture must carry it too, at 0, rather than omitting it.
    assert block["per_label"]["Document"]["nodes"] == 0, block["per_label"]
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


def test_the_shipped_log_states_EVERY_row_note_the_same_way_in_both_copies():
    """One note, two copies — and EVERY row, not just E3.

    `render_log` writes a note twice: the human `row <C> note: …` line and the machine
    record's `rows[i].note`. In a fresh run they cannot disagree (one `row()` dict feeds
    both — `render_log` emits `add(f"row {row['criterion']} note: {row['note']}")` from
    that one dict and the same `rows` list becomes the record's `rows`), so a disagreement in the SHIPPED log is
    proof of a one-sided HAND EDIT — an edit that reads as emitter output, which is the
    class `#10537` exists to refuse and rounds 2-3 of this review claimed to have
    closed.

    ⛔ WHY THIS IS GENERALISED (`#10679` review round 4). E3 had this pin and E4/E5 did
    not, so the standard existed and the two copies this very PR edits were left
    one-sided. Measured in a throwaway copy: restoring
    "… and the residual is owned by #9596)" in the human `row E4 note:` line ALONE —
    leaving the machine copy corrected — kept both note pins GREEN while the two copies
    then contradicted each other; stripping "The row's blocker moved on 2026-11-02 from
    #9738 to #5087" from the human `row E5 note:` line alone also stayed green. Binding
    every criterion closes both, and closes the next one nobody has thought of.

    ⛔ AND A DUPLICATE HUMAN LINE IS A FINDING, NOT AN OVERWRITE (`#10679` review
    round 5). The first cut built the human map with `dict(re.findall(...))`, which
    keeps the LAST match per criterion, and then only compared the criteria SETS — so
    a CONTRADICTORY `row E4 note:` line inserted ABOVE the real one was neither counted
    nor compared. Measured in a throwaway copy: inserting one such line, reverting
    "no open issue owns the residual" to "the residual is owned by #9596", left the
    whole suite green (163 passed at `b132e1a28`) while the log carried a human
    sentence its own
    machine copy refutes — the very attribution round 4 was closing, still readable as
    emitter output. Occurrence COUNTS are compared now, so a duplicate is the finding.

    Non-vacuity: a one-sided edit to E4 or E5 reds here, and so does a duplicated line.
    """
    log = _newest_log(_scored_logs())
    text = log.read_text()
    record = cvc.extract_block(text)
    machine = {row["criterion"]: row["note"] for row in record["rows"]}
    human_lines = re.findall(r"^row (E[1-5][ab]?) note: (.*)$", text, re.MULTILINE)
    assert human_lines, "the shipped log carries no `row <C> note:` line"
    # ⛔ COUNTS, not a dict: `dict(...)` silently keeps the last of a duplicate pair,
    # and the set comparison below cannot see that a THIRD copy exists.
    per_criterion: dict[str, list[str]] = {}
    for criterion, note in human_lines:
        per_criterion.setdefault(criterion, []).append(note)
    assert len(human_lines) == len(per_criterion), (
        "the log carries MORE THAN ONE `row <C> note:` line for the same criterion — a "
        "duplicate is a copy nothing compares, so a contradictory one can sit above the "
        f"real line and read as emitter output: "
        f"{ {c: len(v) for c, v in per_criterion.items() if len(v) > 1} }")
    assert set(per_criterion) == set(machine), (
        f"the log's human copies cover {sorted(per_criterion)} but its machine record "
        f"covers {sorted(machine)} — a row with one copy is a copy nothing can compare")
    for criterion in sorted(machine):
        assert per_criterion[criterion] == [machine[criterion]], (
            f"the log's human and machine copies of the {criterion} note disagree — one "
            f"of them was hand-edited alone, which reads as emitter output:\n"
            f"  human:   {per_criterion[criterion]}\n"
            f"  machine: {machine[criterion]}")


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
    # and the S5 table row in the same document must describe ONE run. #10601 made the
    # eight-step table the SUPERSEDED run's (README's own banner), while `8173e88eb` moved
    # this row's operator triple to THIS run's — pairing the superseded census
    # (433 derived nodes / Point 338 / Object 50 / Event 45) with the shipped run's
    # IMPL=65 INPUT=68 NAND=14, a combination no artifact ever measured
    # (`e0fc940b5` pairs 433/338/50/45 with IMPL=57 INPUT=58 NAND=10). The old form of
    # this leg REQUIRED that fusion, because it was written when the eight-step table WAS
    # the shipped run's; it now holds the row to one run — in BOTH directions. The first
    # form of the fix gated everything on the superseded census literal, so the MIRROR
    # fusion (shipped census `397 derived nodes` beside the superseded triple) passed
    # every leg; measured, and the reason both branches below are asserted.
    s5_row = next(ln for ln in readme.splitlines()
                  if ln.startswith("| S5 CONNECT"))
    shipped_triple = (f"IMPL={measured.group(1)} INPUT={measured.group(2)} "
                      f"NAND={measured.group(3)}")
    superseded_triple = "IMPL=57 INPUT=58 NAND=10"
    shipped_census = "397 derived nodes" in s5_row
    superseded_census = "433 derived nodes" in s5_row
    assert shipped_census or superseded_census, (
        "the S5 row quotes neither run's census, so nothing here can tell which run it "
        f"describes: {s5_row[:400]}")
    assert not (shipped_census and superseded_census), (
        f"the S5 row quotes BOTH runs' census counts: {s5_row[:400]}")
    if superseded_census:
        assert superseded_triple in s5_row and shipped_triple not in s5_row, (
            "the S5 row quotes the SUPERSEDED run's census (433 derived nodes), so its "
            "operator triple must be that run's too (`e0fc940b5`'s `step S5` line pairs "
            f"433 with {superseded_triple}) — not this run's {shipped_triple}: "
            f"{s5_row[:400]}")
    else:
        assert shipped_triple in s5_row and superseded_triple not in s5_row, (
            "the S5 row quotes THIS run's census (397 derived nodes), so its operator "
            f"triple must be this run's too ({shipped_triple}), not the superseded "
            f"{superseded_triple}: {s5_row[:400]}")


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
            "say what still bounds the run (the rows' own blockers, and V5's "
            f"8/8 accounting); it reads: {row}")

    # The prose is only allowed to claim this because the fold really does it.
    sdk = (_REPO / "tortoise" / "sdk.py").read_text()
    assert '"stats": _capture_stats_with_step_witness(' in sdk, (
        "the bundle now names `#9116` as LANDED, but `_extract_session_v2`'s meta "
        "fold does not call `_capture_stats_with_step_witness`: the seam is gone, "
        "so the documents are lying again — restore the fold or the pre-seam text")


#: Present-tense shapes that re-state this bundle's recorded PREDICTION about a
#: post-seam re-run — the prediction the shipped log REFUTES. Guarded on the log
#: itself (see the test), never on a constant: the moment the scored log stops
#: observing S2.2/S2.3 these sentences become true again and the guard stands
#: down, so it cannot outlive the artifact it depends on. Each phrase was
#: measured present in at least one pre-fix document (#10601 round 2, P1).
#: ⛔ Eleven entries, and EVERY one was re-measured present at `fdb19441f` in
#: round 3 — one of them (`"S2.3`, which the measured corpus' idle classify pass
#: does not"`) matched no document at any revision and was replaced; a ban that
#: cannot fire is exactly the no-op this bundle exists to refuse, so any future
#: entry lands with the same flatten-and-grep evidence this one carries.
_BUNDLE_STALE_STEP_PREDICTION = (
    "so a re-run there still reports S2.3",
    "S2.3 does not flip",
    "S2.3 does not move on this corpus",
    # Measured present at `fdb19441f` (and at `7caace29c`): the bundle's own
    # dependency table said `S2.3` was "IDLE on this corpus" while the log this
    # PR ships carries `step S2.3 WITNESSED … assigned_llm: 7`. The spellings
    # above were blindness-by-paraphrase: these name a DIFFERENT failure (idle,
    # not "cannot move") and so survived the guard written for the class of
    # "the bundle states a step prediction its own artifact refutes".
    #
    # ⛔ The last of them was a NO-OP for one review cycle (#10601 round 3): it
    # was registered as ``"S2.3`, which the measured corpus' idle classify pass
    # does not"`` — a `S2.3`,`` prefix the pre-fix sentence never carries (it
    # reads ``(S2.3 flips only once its pass does work, which the measured
    # corpus' idle classify pass does not``), so the entry matched no document at
    # ANY revision and a ban that cannot fire is not a ban. Measured across
    # `origin/main`, `fdb19441f`, `7caace29c`, `d7c48ff95` and this head: the
    # prefixed form is absent from all five; this spelling is present in
    # README at the first three and absent at the last two.
    "S2.3` is IDLE on this corpus",
    "which the measured corpus' idle classify pass does not",
    "the classify pass is IDLE on it, so S2.3",
    "on the measured corpus the pass is IDLE",
    "Until such a re-run is committed",
    "What still has to land is a **committed post-seam re-run**",
    "The two unwitnessed steps stay unwitnessed for THIS run",
    "stays what it is — `capstone-measured-e0fc940b.log`",
)


def _flat(text: str) -> str:
    """One-space-normalised prose, because these sentences WRAP.

    A phrase matched against the raw text goes line-blind: "**S2.3 does not
    flip**" is one sentence to a reader and two unrelated substrings to `in`,
    which is how a guard like this passes over the very sentence it exists for.
    """
    return re.sub(r"\s+", " ", text)


#: The superseded-run history the sibling message ASKS to keep, and the refutation — both TRUE.
#: Kept as spellings only; the regex experiments that tried to generalise this are recorded as
#: dead ends in `test_no_document_states_the_step_prediction_its_own_artifact_refutes`.



def test_no_document_states_the_step_prediction_its_own_artifact_refutes():
    """The bundle must not tell a lane a re-run cannot move S2.3 — one already did.

    `E1-E5-PROOFS.md` is the audit a lane reads to decide whether a row can move,
    and it asserted, in the present tense, that "a re-run there still reports S2.3"
    unobserved, that "S2.3 does not flip", that "S2.3 does not move on this
    corpus", that the epic's closing rule stays unsatisfiable "until such a re-run
    is committed", and that "what still has to land is a **committed post-seam
    re-run**" — while the log THIS PR ships IS that re-run, and it observes all
    eight steps with S2.3's witness non-empty. The PR body already said the run
    "falsified this bundle's recorded prediction that S2.3 stays IDLE"; the
    sentences were simply never swept. A lane reading them concludes a re-run
    cannot move S2.3 and does not run one — the stale-inference class `#10537`
    exists to close.

    Bound to the ARTIFACT, not to prose: the guard reads the shipped scored log's
    own `steps_observed` / `steps_unobserved` / `status`, and only then forbids the
    sentences. If a future re-run stops witnessing S2.3, those sentences become
    true again and this test stands DOWN rather than lying in the other direction.

    Non-vacuity (measured against the pre-fix tree at `fdb19441f`, all eleven
    phrases, by flattening each document and grepping): README carries the "a
    re-run there still reports S2.3", "S2.3 does not flip", "S2.3 does not move on
    this corpus", the four IDLE paraphrases, "stay unwitnessed for THIS run" and
    "stays what it is — `capstone-measured-e0fc940b.log`" shapes, and
    E1-E5-PROOFS carries "Until such a re-run is committed" and "what still has to
    land is a **committed post-seam re-run**". All eleven red there, and none is
    present at this head.
    """
    shipped = _newest_log(_scored_logs())
    run = cvc.extract_block(shipped.read_text())["run"]
    observed = set(run.get("steps_observed") or ())
    assert {"S2.2", "S2.3"} <= observed, (
        f"{shipped.name} no longer observes S2.2+S2.3 (observed: "
        f"{sorted(observed)}), so the documents' 'a re-run cannot move S2.3' "
        "prediction is TRUE again and this guard must stand down — re-derive the "
        "prose before replacing the log, not after")
    assert run.get("steps_unobserved") == {}, (
        f"{shipped.name} reports steps_unobserved={run.get('steps_unobserved')!r}: "
        "a step is unexplained, so neither 'it moved' nor 'it cannot move' is "
        "stateable")
    assert run.get("status") == "COMPLETED", (
        f"{shipped.name} status={run.get('status')!r}: a run that did not complete "
        "cannot be the counter-example the prose is re-derived against")

    for name, path in (("README.md", README_DOC),
                       ("E1-E5-PROOFS.md", E1E5_PROOFS)):
        text = _flat(path.read_text())
        for phrase in _BUNDLE_STALE_STEP_PREDICTION:
            assert _flat(phrase) not in text, (
                f"{name} still states this bundle's recorded step PREDICTION as "
                f"current ({phrase!r}), but {shipped.name} refutes it: the run "
                "observes all eight steps and S2.3's witness is non-empty. A lane "
                "reading that sentence concludes a re-run cannot move S2.3 and "
                "never runs one — say what the SHIPPED log measured, and keep the "
                "6-of-8 count as the SUPERSEDED run's history")

        # ⛔ A STRUCTURAL ROW CHECK WAS TRIED HERE AND REMOVED — measured. Three designs,
        #    each wrong, recorded so a future round does not re-invent them:
        #      · a sentence-level prose regex → false-RED on `#10215`'s general rule
        #        ("an idle classify pass is ACCOUNTED from the receipt's own facts"),
        #        which is TRUE of any corpus;
        #      · a row ban allowlisting the bare words `accounted|conditional` →
        #        false-RED on a legitimate SUPERSEDED-history row AND false-GREEN on
        #        "the pass is IDLE on this corpus, accounted";
        #      · a scope-based row discriminator → false-RED on a TRUE row ("the classify
        #        pass's `union` is unwitnessed (`{}`) while `s2` carries 7 assignments" —
        #        both facts are in the log) and false-GREEN on "the pass was enabled and
        #        did no work on this corpus (s2 counters all zero)".
        #    A guard that reds on true content AND greens on the refuted claim is worse
        #    than none: it is a false PASS that reports the class closed. So it is
        #    deleted, and the residual is STATED rather than hidden: a PARAPHRASE of the
        #    idle claim is not caught by any pin in this file. What actually protects the
        #    load-bearing claim is the artifact-bound leg at the top of this test (it
        #    reads the shipped log's own `steps_observed`) plus the POSITIVE pin below,
        #    which requires the witness row to name the EVIDENCE that the pass did work
        #    rather than a word describing it.
        if name == "README.md":
            witness_rows = [r for r in path.read_text().splitlines()
                            if r.startswith("| S2.3")]
            assert witness_rows, (
                "README.md no longer carries a `| S2.3 …` witness row — the positive "
                "pin below cannot check a row that is not there")
            assert any("assigned_llm" in r for r in witness_rows), (
                "the S2.3 witness row must name the EVIDENCE that the classify pass "
                "did work (`assigned_llm` on the shipped log's witness), not a word "
                f"describing it: {witness_rows[0][:300]!r}")


#: The `e1-aux` triple of the SUPERSEDED run — `capstone-measured-e0fc940b.log`
#: measured 50 objects / 6 bare-definite / 0.12 share. The run this bundle now ships
#: (`capstone-measured-9152448d.log`) measures 64 / 14 / 0.21875.
#:
#: Measured at `7caace29c`: the bundle's E1 summary row already said "**64 Objects** …
#: 14/64", while its own E1 narrative (`**50 `:Object` nodes** … 6/50 = 0.12`, and
#: `6/50 in the shipped log`) and the `00_index` row (`50 Objects at a 6/50 = 0.12`)
#: still quoted the superseded triple as THIS run's — the row's stamp had moved to
#: `9152448d7` without the number moving with it, which is the "a row moves only by
#: re-running the probe" rule failing in the one direction a re-measure cannot fix:
#: the prose was never re-derived. Two documents, one class, no pin.
_SUPERSEDED_E1_AUX = (
    "**50 `:Object` nodes**",
    "6/50 = 0.12",
    "50 Objects at a 6/50",
    "6/50 in the shipped log",
)


def test_no_document_quotes_the_superseded_runs_e1_aux_numbers_as_current():
    """The bundle may not present the deleted run's E1-aux numbers as this run's.

    Non-vacuity, and it is the point of the test: the SHIPPED triple must be present
    where the superseded one was banned. A pin that only forbids would pass on a
    document that dropped the measurement altogether — which is the "no number" form
    of the same defect.
    """
    readme = (_REPO / "docs/evidence/7949-capstone/README.md").read_text(encoding="utf-8")
    index = (_REPO / "docs/00_index.md").read_text(encoding="utf-8")

    # ⛔ THE NUMBERS COME FROM THE LOG, NEVER FROM THIS TEST. A constant here turns the
    #    REQUIRED half of this pin into a false-red trap on the very operation the
    #    bundle exists to support: the next re-measurement would have to edit the pin
    #    to agree with the artifact the pin is supposed to be checking, and until
    #    somebody did, a CORRECT document would red (measured defect, review round 5).
    #    The BAN list below is the opposite case and stays literal: it names one
    #    specific HISTORICAL triple, and "a superseded run produced this" does not stop
    #    being true when a third run supersedes this one.
    shipped = _newest_log(_scored_logs())
    aux = re.search(
        r"^e1-aux objects_in_graph=(\d+) bare_definite=(\d+) share=([0-9.]+)",
        shipped.read_text(encoding="utf-8"), re.M,
    )
    assert aux, (
        f"{shipped.name} carries no `e1-aux` line, so neither the shipped E1 share nor "
        "this pin's REQUIRED half can be stated — re-derive the prose from a log that "
        "does measure it, not by editing this pin")
    objects, bare, share = aux.group(1), aux.group(2), aux.group(3)

    for name, text in (("README.md", readme), ("docs/00_index.md", index)):
        flat = _flat(text)
        for phrase in _SUPERSEDED_E1_AUX:
            assert _flat(phrase) not in flat, (
                f"{name} quotes the SUPERSEDED run's e1-aux numbers as current "
                f"({phrase!r}) — {shipped.name} measures {objects} Objects at "
                f"{bare}/{objects} = {share}. Say what THIS run measured. This pin "
                "bans the superseded triple in these two documents OUTRIGHT: it does "
                "not implement a labelled-history exemption (an earlier version of "
                "this message promised one, which no lane could satisfy) — the "
                "labelled form belongs in the sentence that carries its own "
                "retrieval route, which is not these two documents' E1 headline")

    assert f"{objects} `:Object` nodes" in readme and f"{bare}/{objects} = {share}" in readme, (
        f"README.md no longer states the shipped run's E1-aux numbers ({objects} "
        f"`:Object` nodes / {bare}/{objects} = {share}, from {shipped.name}) — the ban "
        "above would pass vacuously on a bundle that dropped the measurement")
    assert f"{objects} Objects at a {bare}/{objects} = {share}" in index, (
        "docs/00_index.md's capstone row no longer states the shipped run's E1-aux "
        f"numbers ({objects} Objects at a {bare}/{objects} = {share}, from "
        f"{shipped.name}) — re-derive them from that log's `e1-aux` line rather than "
        "dropping the claim")


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
# continuation (the docs write `` `extractor_v2.py:8816` and `:8616` ``) is NOT a
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
    # The two `projection/` modules the bundle also cites by number. They are NOT
    # reachable through `_BUNDLE_CITE` (`__init__`/`entities` are not in its
    # alternation), so they get `_BUNDLE_SIBLING_CITE` below — a cite no pattern
    # can even SEE is verified by nothing (round-4 review, F4).
    "projection_init": _REPO / "tortoise/projection/__init__.py",
    "projection_entities": _REPO / "tortoise/projection/entities.py",
}

# `projection/__init__.py` and `projection/entities.py` are cited by NUMBER in the
# bundle, and the sweep above could not see either: `_BUNDLE_CITE` matches
# `sdk|edges|extractor_v2` and a basename of `__init__`/`entities` is not in that
# alternation. That invisibility is exactly how `__init__.py:13613-13615` sat on a
# `# ── :Batch marker` comment while every gate stayed green — the cite had even
# been RE-DERIVED in the commit that broke it (round-4 review, F4: `main` said
# `:13591-13593`, also wrong, off by −9). `(tortoise/)?projection/<mod>.py:<n>` is
# now a cite the sweep resolves, with the module mapped to its own source key.
_BUNDLE_SIBLING_CITE = re.compile(
    r"(?<![\w.])(?:tortoise/)?projection/(__init__|entities)\.py:(\d+)(?:-(\d+))?")
_BUNDLE_SIBLING_SOURCES = {
    "__init__": "projection_init",
    "entities": "projection_entities",
}

# The GATE the bundle audits, which the bundle also cites by number. Kept OUT of
# `_BUNDLE_SOURCES` on purpose: neither pattern reaches `tools/capstone_verdict_
# check.py` (`_BUNDLE_CITE` matches `sdk|edges|extractor_v2`, the sibling pattern
# matches `projection/__init__|entities`), so putting a key here would imply the
# sweep proper reads it when it cannot. These are read by `test_the_bare_continuation_cites_name_what_they_say`
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
    # The two `projection/` cites the bundle prints (round-4 review, F4 — the
    # class the sweep could not see). Each names the doc phrase beside it, so the
    # rule is chosen by DISTANCE like every other, and the anchors are the code
    # the sentence says is there — a blank-`pointKind` synthesis in the #548
    # rebuild path, and the live write that leaves the property ABSENT.
    ("the #548 rebuild's blank-pointKind synthesis", "projection_init",
     ("#548 rebuild SNAPSHOT synthesis",),
     ("# Ensure pointKind is present (operators often lack it)",
      'clean["pointKind"] = ""'), "span",
     ("contains", 'clean["pointKind"] = ""')),
    ("the live write that leaves pointKind ABSENT", "projection_entities",
     ("the live write is",),
     ("n.pointKind=coalesce($pk, n.pointKind)",), "line", None),
    # Two more `entities.py` cites the sibling pattern made VISIBLE (they were
    # invisible before it, like the `__init__.py` one): the alias derivation the
    # write path reads the identifier flag through, and the Object writer's
    # `objectKind` default that the edge-kind query must read instead of `kind`.
    ("the alias derivation the write path reads the flag through",
     "projection_entities", ("read through",),
     ("identifier_tokens_enabled()",), "line", None),
    ("the Object writer's objectKind default", "projection_entities",
     ("the writer stamps",),
     ("o.objectKind=coalesce($ok",), "line", None),
    # A THIRD `entities.py` cite the pattern surfaced, and its rule must be its
    # own: `:2740` is the `is_episodic` write, not the `pointKind` one `:2602`
    # carries, so a shared "the live write" phrase would bind it to the wrong
    # rule and red on CORRECT code.
    ("the is_episodic write that makes an explicit false a claim",
     "projection_entities", ("really writes the property",),
     ('set_clauses.append("n.is_episodic=$episodic")',), "line", None),
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
    computed at `extractor_v2.py:8816` ``), so the closest phrase is the one that
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
        # Both patterns feed ONE pass, in document order: `_BUNDLE_CITE` carries
        # its source in group 1, the sibling pattern maps its module. Group 2/3
        # are the start/end in both, which is what keeps this a single loop.
        matches = [(m, m.group(1)) for m in _BUNDLE_CITE.finditer(text)]
        matches += [(m, _BUNDLE_SIBLING_SOURCES[m.group(1)])
                    for m in _BUNDLE_SIBLING_CITE.finditer(text)]
        for match, source in sorted(matches, key=lambda pair: pair[0].start()):
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
            start, end = int(match.group(2)), match.group(3)
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

    Covers all the sources the bundle prints numbers for (`sdk.py`, `edges.py`,
    `extractor_v2.py`, and — through the sibling pattern — `projection/__init__.py`
    and `projection/entities.py`) across `README.md`, both probes, the E-row audit
    (`E1-E5-PROOFS.md`), the `docs/00_index.md` rows that are the bundle's entry
    point, and the `docs/architecture/
    EXTRACTOR-V4-ARCHITECTURE.md` S2.1 FINDING row this bundle owns — 47 cites at
    this head, five of them the `projection/*.py` ones the sibling pattern made
    visible (`#10108` added the sixth document; the count is MEASURED, and it
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
    to `7913`, `the vet flag` reds with `extractor_v2.py:7953 reads 'continue',
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
    `` `extractor_v2.py:9276` … `:9060` ``, where the file is named once for
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
            ("15611", "if vet_enabled and embed_list:"),
            ("15612", "embed_list, vet_s2_stats, _w, vet_s2_pool = _run_vet_pass("),
            ("13301", "if vet_enabled and complete_list:"),
            ("13302", "complete_list, vet_union_stats, _w, _ = _run_vet_pass("),
        )),
        # the VET block's start; its lines of keys (13526-13528) close on
        # line 13529 — DERIVED by content, not by arithmetic: `result["vet"] = {`
        # opens at 13525 and the block's `}` is 13529. ⚠️ A conflict resolution
        # here once kept main's numerals (13256/13257-13259/13260 — correct on
        # main, but in THIS tree 13256 is the `"hold"` key and 13257-13260 is
        # the whole `chain_enforcer` block) next to the branch's post-refactor
        # asserts; a comment is not an asserted number and nothing reds on it,
        # which is why every ASSERTED number in this table is verified by
        # content instead.
        ("docs/00_index.md", "emitted unconditionally at", (
            ("13515", 'result["classify_later"] = {'),
            ("13525", 'result["vet"] = {'),
        )),
        # ⛔ THE CLASSIFY SWITCH ITSELF (the rebase onto `a1dff7cec8` re-staled it).
        # `#11509` added this bare continuation to the probe's docstring with
        # `main`'s `:12898`, which IS the assignment on `main`; `#11002`'s +15
        # insertions above it (the S1 `emphasis` seam and the G5 record) moved the
        # switch, so the sentence sent a reader 15 lines up into
        # `extract_session_v2`'s preamble — into the VET/roll-up comment, which is
        # precisely the confusion the sentence exists to prevent. Nothing read it:
        # a bare `` `:N` `` names no file, so the sweep cannot see it. Measured on
        # this head before the fix: `:12898` is `# the additive keys feed the
        # harness's per-question integrity (M4).`
        ("probes/capstone_measure.py", "is different code", (
            ("12913", "classify_later = kind_classifier is not None or "
                      "_classify_later_enabled()"),
        )),
        # ⛔ THE CLASSIFY PASS'S OWN FAIL-OPEN PAIR (#10833 review, P2). A `+33`
        # shift applied to this row moved the first member while leaving the
        # second at its PRE-shift value, so `vet_warnings.extend(_w)` — inside
        # the VET block — was printed as the classify pass's second fail-open
        # `except`. The same sentence one clause earlier condemns exactly that
        # defect ("a cause citing `vet_enabled`/`_run_vet_pass` for S2.3 sent the
        # reader to code that pass never runs"). The two `except` lines are
        # byte-identical, so what distinguishes the pair is STRUCTURE, not text:
        # the row's members must be the fail-opens of `if classify_later and
        # embed_list:` / `if classify_later and complete_list:` — their next line
        # appends `classify(S2) failed` / `classify(union) failed` — and NOT the
        # referentiality-selection fail-opens, which carry a `noqa` directive
        # naming `BLE001` and `RUF100` and warn `referentiality selection …
        # candidates kept (fail-open)`. ⛔ Of the numbers this comment used to
        # quote, THREE were CORRECT at `76a89034e`, the fold head they were
        # written for — `10490`/`10620` were the row's own members and `10599` the
        # `classify_later and complete_list` gate — and only `:10453` was wrong: it
        # is the `TORTOISE_CLASSIFY_LATER` comment line, and
        # `vet_warnings.extend(_w)` sat at `:10465`/`:10587`. All four are dead at
        # this head. A comment asserts nothing, so read the anchors from the row,
        # never from this prose.
        ("docs/00_index.md", "fail-open `except` at", (
            ("13190", "except Exception as e:  # never block capture (P1)"),
            ("13337", "except Exception as e:  # never block capture (P1)"),
        )),
        # `_IDLE_CITES["vet"]`: the early return and the surface it emits
        ("probes/capstone_measure.py", "its vet surface", (
            ("12948", "if not edus:"),
            ("12979", '"vet": {"enabled": vet_enabled, "s2": {}, "union": {}},'),
        )),
        # ...and the same claim in the rule's own docstring
        ("probes/capstone_measure.py", "with the flag ON", (
            ("12979", '"vet": {"enabled": vet_enabled, "s2": {}, "union": {}},'),
        )),
        ("probes/capstone_measure.py", "follows at", (
            ("12947", "edus = _edus_from_conversation(conversation)"),
        )),
        # the S2.2/S2.3 block this probe names as the #9116 seam's subject
        ("probes/capstone_measure.py", 'result["vet"]`` at', (
            ("13525", 'result["vet"] = {'),
            ("13529", ("exact", "}")),
        )),
        # ⛔ The SAME two blocks as the probe's `_pass_ran` docstring names them
        # (#11517 review, finding 3). It said the flag-OFF surfaces live at
        # `:13445-13453`, which is the CLASSIFY block plus the two VET comment
        # lines — it does not contain `result["vet"]` at all, because the two
        # blocks are 13445-13451 and 13455-13459. The row above pins the VET
        # bounds for a DIFFERENT sentence, so this sentence's own copy of the
        # block bounds had no reader: a bare `:N`-range names no file, which is
        # the drift the whole `_PIN_ROWS` leg exists to stop. Non-vacuity: on
        # the pre-fix line the `13451` half fails BOTH clauses (`:13451` and
        # `-13451` are absent from `:13445-13453`), so the pin reds on exactly
        # the wrong range it was added for.
        #
        # ⛔ AND BOTH BLOCK BOUNDS, not a subset: the corrected sentence names
        # `:13515-13521, :13525-13529`, so `13525`/`13529` are its OWN copy of
        # the VET bounds and need the same reader. The `13525`/`13529` row on
        # `probes/capstone_run.py` scopes to THAT document's line; it cannot see
        # this sentence, and a row that pinned 4 of a sentence's 6 numbers would
        # leave the fix's own new cite as the one this leg still cannot see.
        #
        # ⚠️ #11587 rebased these six onto the S2.2 fail-open guard (+70 below it,
        # measured by content on the rebased tree, not by arithmetic); `:12975` and
        # `:12979` sit ABOVE the guard and did not move.
        ("probes/capstone_measure.py", "so an acceptance test of", (
            ("12975", '"classify_later": {"enabled": classify_later,'),
            ("12979", '"vet": {"enabled": vet_enabled, "s2": {}, "union": {}},'),
            ("13515", 'result["classify_later"] = {'),
            ("13521", ("exact", "}")),
            ("13525", 'result["vet"] = {'),
            ("13529", ("exact", "}")),
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
        # ⛔ RE-DERIVED at `#10982`, not offset: the A-row emitter-key drift arm added
        # above `STEPS` moved that cite, and the same commit's additions inside the
        # accounting block moved `_step_accounting` — TWO different deltas from one
        # commit, which is why both numbers are read off the tree
        # (`grep -n '^STEPS = ('` / `'^def _step_accounting('`) and never carried
        # forward as an offset.
        #
        # ⛔ AND A THIRD DELTA, from the `origin/main` carry (#11331): `#11298`'s
        # `door` run-block key registered in `_NON_MEASUREMENT_RUN_KEYS` put 15
        # lines above the accounting block and moved `_step_accounting` again.
        # ⛔ AND A FOURTH: `ed3d8761f` moved the def once more. The historical
        # readings in this block are re-verified by
        # `git show <sha>:tools/capstone_verdict_check.py | grep -n '^def _step_accounting('`
        # (`1390485c1` -> 690, `ed3d8761f` -> 699): they read commits that already
        # happened, so they cannot rot.
        # ⛔ THE LIVE VALUE IS NOT MAINTAINED IN THIS PROSE — even where a
        # historical reading happens to equal it, as `ed3d8761f`'s does today.
        # It went stale four times while it lived here. The row below asserts it;
        # read it from there, or `grep -n '^def _step_accounting('
        # tools/capstone_verdict_check.py`.
        # The comment block above the tuple is where that reasoning has to live,
        # so the shift cannot be avoided by shortening it — a line count above a
        # cite moves the cite.
        ("E1-E5-PROOFS.md", 'declares `STEPS = ("S0"', (
            ("146", 'STEPS = ("S0", "S1", "S2.1", "S2.2", "S2.3", "S3", '
                    '"S5", "S6")'),
        ), "capstone_verdict_check"),
        ("E1-E5-PROOFS.md", "_step_accounting` (`:699`) refuses a GREEN row whose", (
            ("699", "def _step_accounting("),
        ), "capstone_verdict_check"),
        # ⛔ A SECOND DOCUMENT PRINTS THE SAME DECLARATION (#10601 review round 2).
        # `docs/00_index.md`'s capstone row cites this gate too, and it moved
        # `:118` -> `:131` while the target moved to `:132` — so the index pointed
        # at `CRITERIA = (...)` while its sibling document pointed at `STEPS`. No
        # sweep caught it: `_BUNDLE_CITE` resolves only `sdk|edges|extractor_v2`,
        # and the rows above covered ONE document. Every doc that prints a
        # `tools/*.py` number needs its own row here, not just the first one.
        ("docs/00_index.md", "declares the eight steps", (
            ("146", 'STEPS = ("S0", "S1", "S2.1", "S2.2", "S2.3", "S3", '
                    '"S5", "S6")'),
        ), "capstone_verdict_check"),
        # ⛔ THE VET BLOCK'S OWN "BEFORE" CLAIM (#10833 review, P2 — the SECOND
        # time this file's early-return cite went stale). `_BUNDLE_CITE` resolves
        # the NAMED `extractor_v2.py:10626`, but the `(:NNNNN)` one clause later
        # is a bare continuation — invisible to the sweep — and the fold's `+33`
        # shift moved it from `:10371` to `:10237`, a line inside
        # `_run_vet_pass`'s BODY. The sentence then asserted that `:10367` is
        # BEFORE `:10237` (impossible), and sent the reader 150 lines below the
        # return it names, while every gate reported OK. Two rows because the
        # claim wraps over two lines and a row scopes to ONE line.
        ("README.md", "i.e. BEFORE the", (
            ("12944", "vet_enabled = (vet_arbiter is not None or _vet_enabled()"),
        )),
        ("README.md", "early return (`", (
            ("12948", "if not edus:"),
        )),
        # ⛔ THE LITERAL THE SENTENCE QUOTES, on its OWN row because the claim
        # wraps: `:15475` was left pointing inside `_arm_extract`'s
        # `_run_vet_pass` call while the sibling row in `docs/00_index.md` kept
        # `:12979` — two mirrors of one paragraph, two different targets, and
        # neither row could see it (review round 3, P2). The quoted literal is
        # the content this row holds, so a re-derivation that moves the number
        # off the early return reds instead of drifting silently.
        ("README.md", '"s2": {}, "union": {}}', (
            ("12979", '"vet": {"enabled": vet_enabled, "s2": {}, "union": {}},'),
        )),
        # ⛔ The #2684 row's cite (#10833 review, P2): the fold rewrote a CORRECT
        # `5042-5043` into `4948-4949` — inside `_refuse_rewritten_value`, an
        # unrelated #10360 function — on a file BYTE-IDENTICAL to `origin/main`,
        # so no drift can explain it. A full `file:line` cite is not a pin:
        # `_BUNDLE_CITE` resolves that the number exists and lands on a code
        # line, never that it is the code the sentence names. Held by content.
        # ⛔ THE VET PASS'S FAIL-OPEN PAIR (#10997 review round 5, P3): a bare
        # range inside a paragraph whose LIVE half says "here" — the paragraph is
        # `_BUNDLE_HISTORICAL`, so the named cite is excluded from the sweep AND
        # the bare one sat in no row, which is how a `+N` insert left it on a
        # docstring in an unrelated helper while every neighbour moved.
        ("README.md", "on its fail-open path", (
            ("12823", "except Exception as e:  # noqa: BLE001, RUF100"),
            ("12826", '"(fail-open)"], {}'),
        )),
        # ⛔ AND THE PROBE'S OWN COPY (#10997 review round 5, P2): the same range
        # is baked into `_IDLE_CITES["vet"]`, i.e. into the cause a future run
        # PRINTS to a reader — and it sat on a line the neighbouring row does not
        # scope to, so a move would ship a stale number in machine-emitted prose.
        ("probes/capstone_measure.py", "on its fail-open path", (
            ("12823", "except Exception as e:  # noqa: BLE001, RUF100"),
            ("12826", '"(fail-open)"], {}'),
        )),
        ("docs/00_index.md", "refuses a value-free point at", (
            ("5369", "if not _carries_value(content, quote):"),
            ("5370", "return None"),
        )),
        # ⛔ THE S5 CONNECT CALL, IN BOTH DOCUMENTS (#11368 rebase review, P2). The
        # `+74` re-derivation matched `execute_embed` by SYMBOL and landed on its
        # `def` 3,849 lines ABOVE the caller, while the table's own column header is
        # "call site" and every sibling row in it cites a call inside
        # `extract_session_v2`. Nothing red-ed: the cite is BARE, so `_BUNDLE_CITE`
        # cannot see it, and no row scoped to it — which is exactly the invisibility
        # this pin exists for. Both rows hold the CALL, not the definition.
        ("README.md", "gap filler", (
            ("13432", "result = execute_embed(complete_list, search, "
                      "session_id=session_id,"),
        )),
        # ...and the probe's own copy of the same number, which sits in prose a
        # reader is sent to rather than in a table.
        ("probes/capstone_run.py", "ONE cite per symbol", (
            ("13432", "result = execute_embed(complete_list, search, "
                      "session_id=session_id,"),
        )),
        # ⛔ The S1 row's CALL SITE, held on its own row (#11517 rebase review).
        # The phrase row below cannot hold it: `run_s1`'s DEF is at a low line
        # inside the module while its CALL is inside `_arm_extract`, so a
        # symbol-first re-derivation lands on the `def` — which is what this
        # branch's re-derivation did, while `README.md`'s "call site" row kept
        # naming the call. One claim, two documents, two answers; the row pins
        # the CALL, and the phrase is the sentence that carries it.
        ("probes/capstone_run.py", "(S1,", (
            # 15401 -> 15429 -> 15450: this branch's own `_arm_metrics`
            # docstring/bindings and then the arm record's `None`-coverage
            # qualifier insert above the call, so the number is re-derived from
            # the tree each time rather than carried from the round that added
            # the row (the row's INTENT is unchanged: pin the CALL).
            ("15526", "chunk_stories.append(run_s1(model, _edus_to_text(chunk),"),
        )),
        # ⛔ THE FIVE REMAINING BARE STEP CITES ON THE SAME FIVE-LINE CLAIM, and
        # the reason `test (f)` went red on this branch (#11517 review round 5,
        # finding P2). The reviewer's remedy was not "correct the numbers" — it
        # was "**add a `_PIN_ROWS` row per number so the next insertion reds
        # instead of drifting**", because correcting them alone is exactly what
        # had already happened: the F1/F2 round comment blocks inserted ~6 lines
        # above each cited call AFTER the paragraph was written, and only the
        # pinned numbers (`:15456`, `:13362`) were re-derived. All six were off
        # by +6 and NOTHING read the other six — five here plus `:13108`'s
        # sibling in `README.md`, which
        # `test_the_readmes_line_citations_resolve_to_the_symbols_they_name`
        # covers.
        #
        # A row scopes to ONE line, and this claim WRAPS (`(S2.1,` … `(S2.2,`
        # are one line; the S2.3/S3/s4 half is two more), so the populated
        # sentence takes three rows rather than one — the same reason the
        # `README.md` "i.e. BEFORE the" claim above takes two.
        ("probes/capstone_run.py", "(S2.1,", (
            ("15558", "embed_list = run_s2(model, story, master,"),
            ("15612", "embed_list, vet_s2_stats, _w, vet_s2_pool = _run_vet_pass("),
        )),
        ("probes/capstone_run.py", "_apply_classify_kinds`` (S2.3,", (
            ("13178", "out = kind_classifier.classify_items(items)"),
            ("13179", '_apply_classify_kinds(embed_list, out["assignments"])'),
        )),
        ("probes/capstone_run.py", "(S3,", (
            ("13195", "search = search_graph(sdk, embed_list, story, "
                      "session_id=session_id)"),
            ("13216", "s4 = run_s4(model, story, search, embed_list, master,"),
        )),
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


def test_a_cite_that_names_a_directory_names_a_file_that_exists():
    """The path a cite PRINTS must resolve on disk — `_BUNDLE_CITE` never reads it.

    ⛔ WHY (#10833 review round 6, P2). `_BUNDLE_CITE` makes BOTH directory
    prefixes optional AND uncaptured (`(?:tortoise/)?(?:projection/)?`) and then
    resolves the line by the captured BASENAME (`group(1)` against
    `_BUNDLE_SOURCES`), so the printed path is never used for resolution. That
    is what let the fold's own cite-repair commit rewrite a CORRECT path into a
    fabricated one — `tortoise/edges.py`, which does not exist — in four places
    across `README.md` and `probes/capstone_measure.py`, while the sweep went on
    reporting every one of them green: the NUMBER was right, the line was
    resolved through `_BUNDLE_SOURCES["edges"]`, and the file the reader is told
    to open was never on disk. One of the four is inside `E3_RULE`, which is
    stamped into the machine record of every future scored run, so the defect
    was being written into committed evidence.

    A cite that prints NO directory is the bundle's house style for a
    continuation (`` `edges.py:747` `` after the full path has been named once),
    and it is resolved by basename on purpose — so this leg binds only the cites
    that DO name a directory, and asks the one question the sweep cannot: is the
    path this sentence prints a path in this tree?

    Non-vacuity: measured on this head's parent, where the four cites read
    `tortoise/edges.py:774-782` / `:2832-2857` and `git ls-tree HEAD
    tortoise/edges.py` is empty — all four red with `does not exist in the tree`
    and the `assert seen >= 10` floor still holds (the surviving
    `tortoise/projection/edges.py` and `tortoise/extractor_v2.py` cites keep the
    population well above it), so the test is neither vacuous nor a red-on-red.
    """
    seen, missing = 0, []
    for doc_name, doc_path in _BUNDLE_DOCS:
        text = doc_path.read_text(encoding="utf-8")
        doc_lines = text.splitlines()
        for match in _BUNDLE_CITE.finditer(text):
            printed = match.group(0).split(":", 1)[0]
            if "/" not in printed:
                continue
            line_no = text.count("\n", 0, match.start()) + 1
            if not _bundle_in_scope(doc_name, doc_lines[line_no - 1]):
                continue
            seen += 1
            if not (_REPO / printed).is_file():
                missing.append(
                    f"{doc_name}:{line_no} prints `{printed}`, which does not "
                    f"exist in the tree")
    assert not missing, (
        "a cite names a directory path that is not in this tree — the sweep "
        "resolves these by BASENAME, so it reports them green and the reader is "
        "sent to a file that does not exist:\n  " + "\n  ".join(missing))
    assert seen >= 10, (
        f"only {seen} directory-prefixed cites were read — this leg is not "
        f"reaching the bundle's documents")


def test_every_readme_line_cite_carries_the_sentence_it_quotes():
    """A `README.md:<line>` cite is invisible to every sweep — resolve it here.

    ⛔ WHY (#10601 review round 2). `E1-E5-PROOFS.md` cited a line RANGE for the
    sentence *"printed, and NOT used to green the row, because the artifact
    counts atomic Points, not surviving planted units"*, and that range stopped
    carrying the sentence as soon as lines were inserted above it — it went
    stale at the base, and every edit pushed the quote further from the number
    that named it. NO numbers are repeated here on purpose: a quoted line number
    is the very drift this pin exists to catch, and the LIVE cite lives in the
    document that carries it, re-derived from the file. Nothing could see it: `_BUNDLE_CITE` resolves only
    `sdk|edges|extractor_v2` (the constraint lives in `_BUNDLE_CITE`'s own capture
    group and in `_BUNDLE_SOURCES`), and the bundle's own docs are not sources. A document whose
    thesis is "a claim nothing re-checks can rot silently" leaves its
    `README.md:<line>` cites unwatched, so this pin resolves EVERY one of them —
    either the sentence the cite QUOTES must appear inside the lines it names, or
    (for a cite that quotes nothing, e.g. a table cell's pointer) a case token
    such as `E2b` printed on the citing line must appear inside the named range.

    ⛔ EVERY CITE IS CHECKED, NOT JUST THE QUOTED ONES (#10601 review round 3).
    The first version skipped a cite with no quote (`continue`) and then asserted
    `checked >= 1` — satisfied by the single quoted cite — so three of the four
    cites in this document were resolved by nothing, in the one file whose thesis
    is that unwatched claims rot. EVERY cite is now visited — but read the guard
    for what it is: `checked == len(found)` increments before either branch, so it
    cannot fail, and it guards VISITATION, not coverage. What reds is the check
    each visited cite must pass: a cite that QUOTES a sentence must carry that
    sentence inside the range it names. A cite that quotes nothing is anchored by
    a case token instead — a weaker anchor, and the honest limitation of this pin:
    a quote DELETED from a cite degrades to that anchor rather than redding, so the
    pin proves every cite names something real, never that a cite meant to quote.

    Non-vacuity: at the pre-fix head this pin reds, because the cited range no
    longer carries the quote it names. Re-pointing a QUOTED cite at the wrong
    range reds; an UNQUOTED cite reds only when the wrong range carries none of
    the case tokens its citing line names (the weaker anchor, stated above). (The failing range is not re-quoted here for the same
    reason as above — it is the drift, not the spec.)
    """
    import re as _re

    def plain(text: str) -> str:
        # markdown emphasis and code spans are FORMATTING, not wording
        return " ".join(_re.sub(r"[*`]", "", text).split())

    doc = (_REPO / "docs/evidence/7949-capstone/E1-E5-PROOFS.md").read_text(
        encoding="utf-8")
    readme_lines = (_REPO / "docs/evidence/7949-capstone/README.md").read_text(
        encoding="utf-8").splitlines()
    case_token = _re.compile(r"\bE[1-5][ab]?\b")
    checked = 0
    found = list(_re.finditer(r"README\.md:(\d+)(?:-(\d+))?", doc))
    assert found, "E1-E5-PROOFS.md carries no README.md line cite — pin is moot"
    for index, match in enumerate(found):
        start = int(match.group(1))
        end = int(match.group(2) or match.group(1))
        # ⛔ THE WINDOW STOPS AT THE NEXT CITE. A fixed-length lookahead reached
        # past the FOLLOWING cite in the document and attributed ITS quote to this
        # cite, which would have made the pin pass on a cite pointing at nothing.
        nxt = found[index + 1].start() if index + 1 < len(found) else len(doc)
        tail = doc[match.end():min(nxt, match.end() + 600)]
        cited = plain("\n".join(readme_lines[start - 1:end]))
        assert cited, (
            f"E1-E5-PROOFS.md cites README.md:{start}-{end}, which is past the end "
            f"of the {len(readme_lines)}-line README")
        quoted = _re.search(r'"([^"]{25,})"', tail)
        checked += 1
        if quoted is not None:
            wanted = plain(quoted.group(1))
            assert wanted in cited, (
                f"E1-E5-PROOFS.md cites README.md:{start}-{end} for "
                f"{quoted.group(1)[:70]!r}, but those lines carry {cited[:120]!r} — "
                f"re-derive the range from the file, or drop the line numbers and "
                f"keep the quote (the bundle's own convention), rather than "
                f"pointing at text that is not there")
            continue
        # A cite that quotes no sentence is still a POINTER: the line carrying the
        # cite names a criterion, so that criterion must be inside the range. It is
        # a weaker anchor than a quote, and it is what a table cell can carry.
        citing_line = doc.splitlines()[
            doc.count("\n", 0, match.start())]
        tokens = set(case_token.findall(citing_line))
        assert tokens, (
            f"the E1-E5-PROOFS.md line citing README.md:{start}-{end} names no "
            f"criterion, so this cite cannot be anchored at all: {citing_line[:120]!r}")
        assert tokens & set(case_token.findall(cited)), (
            f"E1-E5-PROOFS.md cites README.md:{start}-{end} from a line about "
            f"{sorted(tokens)}, but those lines carry none of them: {cited[:140]!r} — "
            f"re-derive the range, or quote the sentence it points at")
    assert checked == len(found), (
        f"only {checked} of {len(found)} README.md line cites were resolved — a "
        f"cite that skips its own check is the rot this pin exists to stop")


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
    read both: the prose writes `extractor_v2.py:8903` and `:8703` in the same
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


def _without_change_history(text: str) -> str:
    """The document minus every `### 10.x` change-control entry.

    §11's cites are LIVE claims about the tree at this head. A §10.x entry records a
    value the document held in the PAST — its "New value" line quotes the number a
    re-derivation moved TO. A whole-document ``in text`` check cannot tell the two
    apart, so a §10.x entry naming the current number satisfies the pin while the
    live sentence still cites the stale one: the gate is green over exactly the
    claim it exists to guard. Measured at `6c7a15f2a5` — reverting BOTH live §11
    sentences (the `if items:` pair at `PRE-REGISTRATION.md:1145` AND the fail-open
    pair at `:1189`) to the numbers §10.9's "Original value" records left this leg
    `1 passed`, because §10.9's "New value" names all four current numbers.

    Stripping the history is the general fix: it binds each live claim to live
    prose by construction, instead of to a hand-picked carrier line. It is also the
    shape `#11262`'s earlier round landed and the rebase onto `main` dropped, since
    `main` re-derived the CITES but kept the whole-document check.
    """
    out: list[str] = []
    skipping = False
    for line in text.splitlines(keepends=True):
        if re.match(r"^### 10\.", line):
            skipping = True
            continue
        if skipping and re.match(r"^#{1,3} ", line):
            skipping = False        # the next `## 11.` (or any outer heading)
        if not skipping:
            out.append(line)
    return "".join(out)


def _live_blocks(text: str) -> list[str]:
    """Blank-line-delimited blocks — markdown paragraphs and tables.

    A carrier is scoped to its BLOCK rather than its line so that re-wrapping a
    sentence cannot red this pin. `PRE-REGISTRATION.md` wraps at ~100 columns, so a
    line-scoped check would make correctness a property of where a sentence happens
    to break.
    """
    return re.split(r"\n[ \t]*\n", text)


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

    ⛔ HISTORY IS STRIPPED, AND THE GATES ARE BOUND PER CARRIER. Both bypasses were
    live at `6c7a15f2a5` and both were measured, and both are the same shape — a
    whole-document check satisfied by a mention that is not the claim:

    1. §10.9's "New value" quotes all four current numbers, so reverting BOTH live
       §11 sentences stayed GREEN (`1 passed`, measured). `_without_change_history`
       fixes it.
    2. Even with history stripped, `` `if items:` `` has TWO live carriers — the §9
       provenance row and §11.1's prose sentence — so reverting
       the prose alone still left the row satisfying the pin. Every live carrier is
       now bound to both gates, which is why `_live_blocks` exists.

       The section labels here were wrong in an earlier round of this PR, and SO WAS
       EVERY LINE NUMBER IN THIS PARAGRAPH: transcribed from a memory of the document
       rather than read off it, they matched a revision ~600 lines shorter. That is
       why the carriers are named by their SECTION HEADING and their PHRASE, never by
       a number, and every number the legs actually use is DERIVED at run time
       (`_extractor_lineno`, `_live_blocks`, `ast`) rather than typed. For the record:
       the provenance row is under `## 9. Provenance of every number in this file`
       (§7 ends where `## 8. Change control` opens), the `if items:` prose sentence is
       under `### 11.1 The finding, and what it is not`, and the fail-open pair the
       paragraph below refers to is `### 11.3`'s. A wrong label is not cosmetic here:
       the whole point of this leg is that a reader can go and LOOK at the carrier.

    The fail-open pair needs no per-carrier loop, and that is measured rather than
    assumed: after the strip, each of the two arms occurs in exactly ONE live block (the
    §11.3 sentence that pairs them), so the whole-document check below binds to that
    sentence. (`classify(S2) failed` also appears in the frozen YAML corpus pin's
    `idle_evidence` text, which is why the carriers are located by
    the `` `if items:` `` phrase and not by the arms' own marker text.)
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
        # §10.x change-control entries quote numbers the document no longer claims,
        # so they are not live prose and cannot satisfy a live cite. See
        # `_without_change_history` for the measurement that made this load-bearing.
        text = _without_change_history(path.read_text(encoding="utf-8"))
        assert f"tortoise/extractor_v2.py:{collector}" in text, (
            f"{doc_name} does not cite the collector at "
            f"tortoise/extractor_v2.py:{collector} — §11's argument rests on "
            f"`_collect_classify_items`, so a cite that points anywhere else is an "
            "argument from code the classify pass never reaches")
        if doc_name == "preregistration.json":
            # the record states the condition in one folded scalar; the RANGE and
            # the two gates are the document's table/text, not the record's.
            continue
        # ⛔ THE EXTENT IS BOUND PER LIVE CARRIER, for the same reason the gates
        # below are (review round 2, P1): there are two live carriers — the §9
        # provenance row and §11.1's prose — so a whole-document `in text` check is
        # satisfied by whichever ONE of them is right. Measured at
        # `a4b5fe28c`: mutating the §11.1 extent alone (`:11174-11196` ->
        # `:11175-11196`) left this leg `1 passed`, and so did mutating the §9 row's
        # end alone; only a gate cite in the same sentence red it. The pin a
        # re-derivation leans on has to fail for the carrier that is wrong.
        extent_carriers = [block for block in _live_blocks(text)
                           if "_collect_classify_items" in block]
        assert extent_carriers, (
            f"{doc_name} has no live (non-§10) block naming `_collect_classify_items` "
            "— the collector cite must sit in live prose, not only in a change-control "
            "entry that `_without_change_history` strips")
        # A carrier that names the collector's EXTENT must name the right one. (A
        # carrier that names only the single line — the `steps:` condition's folded
        # scalar does — makes no extent claim, so it is not held to this.)
        # ⛔ SHAPE-TOLERANT (review round 5, P2). The first cut matched only
        # `extractor_v2.py:A-B` with an ASCII hyphen, so a stale extent spelled
        # `:A–B` (en dash), `:A - B` (spaced) or `:A … extractor_v2.py:B` (two
        # single cites) escaped BOTH the carrier filter and the equality test —
        # measured: three such rewrites of a live carrier left this leg `0 failed`.
        # A block that names TWO OR MORE extractor_v2 positions is making an
        # extent claim whatever its punctuation, so it is held to the extent; a
        # block naming exactly one (the `steps:` condition's folded scalar) makes
        # no extent claim and stays out.
        _extent_cite = re.compile(r"extractor_v2\.py:(\d+)(?:\s*[-\u2013\u2014]\s*(\d+))?")
        # ⛔ THE BARE FORM IS THE ONE A RE-DERIVATION ACTUALLY WRITES (round 6, P2).
        # This document's dominant spelling for a SECOND cite on a line is bare —
        # "`if items:` at `:13058`" — so a stale extent appended that way carries
        # no `extractor_v2.py:` prefix and the pattern above cannot see it: measured,
        # "`_collect_classify_items` (`tortoise/extractor_v2.py:11174`, `:11098-11120`)"
        # left this leg `PASS (NOT caught)`, the exact "correct twin" defect the set
        # equality below exists to close. A bare RANGE on a line that itself carries
        # an `extractor_v2.py:` cite is therefore an extractor cite too. A CONNECTOR
        # is required, so bare SINGLE cites (the extremely common `at `:13058`` form,
        # which makes no extent claim) stay out, and the lookbehind keeps `log:1-2`
        # style ranges on unrelated lines untouched.
        # ⛔ AND THE CONNECTOR'S PUNCTUATION VARIES (round 10, P2). The dash test
        # above admitted only a dash that sat INSIDE one span (`:A-B`), so a stale
        # extent spelled the way this document actually writes a SECOND cite — each
        # endpoint in its OWN backticks (`:11098`-`:11120`) — or spelled with the word
        # form (`:11098 to :11120`) escaped: measured, appending each of those to a live
        # carrier left this leg `PASS (escaped)`. The span markers and the spaces
        # between them are punctuation, not meaning, so they are absorbed; the second
        # endpoint's leading `:` is optional for the same reason. An ELLIPSIS is in the
        # connector set for the same reason (round 11): it is a punctuation spelling of
        # "to", and the live documents contain no `:A` … `:B` at all, so admitting it
        # costs nothing and closes the shape instead of declaring it. A CONNECTOR is
        # still required, which is what keeps bare SINGLE cites (the extremely common
        # `at `:13058`` form, making no extent claim) out, and `[ \t]` rather than `\s`
        # keeps a cross-line dash from being read as one extent.
        _bare_range = re.compile(
            r"(?<![A-Za-z0-9_.]):(\d+)[` \t]*(?:[-\u2013\u2014]|to|\u2026|\.\.\.)"
            r"[` \t]*:?(\d+)")
        # ⛔ THE BARE RANGE BELONGS TO THE CARRIER'S SENTENCE, NOT TO ITS LINE
        # (round 7, P2). Line-scoping the bare-range rule above was one re-wrap away
        # from blind: the document wraps at ~103 columns and `_live_blocks` scopes
        # carriers to the BLOCK precisely so re-wrapping cannot red this pin — but
        # with a line test it made the pin MISS instead, measured: a wrapped
        # `…was formerly \`:11098-11120\`` appended to the §11.1 carrier left this
        # leg `PASSES (NOT caught)`.
        # ⛔ AND IT BELONGS TO THE NEAREST *PRECEDING* FILE CITE, NOT TO "THE LINE HAS
        # ONE" (round 8, P2). A per-line `is another file cited on this line?` test is
        # order-blind, so `was formerly \`:11098-11120\` per \`README.md:44\`` still
        # escaped — measured `PASSES (NOT caught)` — while the same token placed BEFORE
        # the range correctly excluded it. Both of these bundles cross-cite heavily, so
        # that is a shape a re-derivation produces. A bare range is therefore attributed
        # to the last `file:line` token that precedes it ON ITS LINE, and counted when
        # that token is `extractor_v2.py:` or when nothing precedes it (a wrapped
        # continuation line). That keeps the §9 row's legitimate bare log cite
        # (`capstone-measured-9152448d.log:271` … `:299-309`) out of the extractor's
        # numbers, and it fixes the over-block the line test caused on a row citing the
        # extent AND a log range.
        # ACCEPTED RESIDUALS, declared rather than chased — each needs semantics, not
        # punctuation, to resolve. ⛔ Residual 1 is NOT hypothetical: §9's own table row
        # (`PRE-REGISTRATION.md:1130`, `capstone-measured-9152448d.log:271` … `:299-309`)
        # is exactly that shape, it lives in the SAME live block as the extent row, and
        # the nearest-preceding-token rule is what keeps its `:299-309` out of the
        # extractor's numbers — measured, removing the exclusion yields
        # `[299, 309, 11174, 11196]` and reds. So do not read that branch as dead code:
        #   1. a bare range that FOLLOWS another file's cite on the same line
        #      (`per \`README.md:44\`, was formerly \`:11098-11120\``) — the same shape
        #      that keeps §9's `:299-309` out, so it is not counted;
        #   2. a bare range that follows an EXTRACTOR cite but really belongs to a
        #      third file (`` `:11174-11196` … the log's `:299-309` ``) — once the
        #      basename fix above is in, that one IS counted and would red. The
        #      document avoids it by spelling the log token on that row, so nothing
        #      live reds; closing it needs the file named on every cite.
        #   3. a stale extent whose endpoints are joined by a WORD rather than a
        #      connector glyph (`:11098` and `:11120`). Declared because it CANNOT be
        #      closed by punctuation, and that was measured, not assumed (round 11):
        #      admitting `and` reds the live §9 row (`PRE-REGISTRATION.md:1121`), whose
        #      `:13101` and `:13250` are two SINGLE cites enumerated under the extractor
        #      owner — exactly the `at `:13101`` form this rule exempts. (The ELLIPSIS
        #      half of this residual is GONE: round 11 put `…`/`...` in the connector
        #      set, which reds the stale spelling while the live documents stay green —
        #      they contain no `:A` … `:B` at all.) Closing what is left needs the
        #      extent recognised semantically ("these two endpoints describe one span")
        #      or the document to spell every extent with a connector glyph.
        _file_tok = re.compile(
            r"[\w./-]+\.(?:log|md|json|jsonl|py|yml|yaml|txt|csv):\d+")

        def _bare_ranges(
            line: str,
            _rng: re.Pattern[str] = _bare_range,
            _tok: re.Pattern[str] = _file_tok,
        ) -> list[tuple[str, str]]:
            """Bare `:lo-hi` ranges this line attributes to `extractor_v2.py`."""
            kept: list[tuple[str, str]] = []
            for match in _rng.finditer(line):
                preceding = list(_tok.finditer(line[: match.start()]))
                # ⛔ COMPARE THE BASENAME (round 9, P2). The document writes
                # `tortoise/extractor_v2.py:` — TEN times — and the bare spelling
                # never (`grep -cP "(?<!tortoise/)extractor_v2\.py:"` -> 0), so a
                # prefix test against `extractor_v2.py:` was FALSE for every live
                # carrier: the "counted" arm was dead and the rule collapsed to
                # "counted only when nothing precedes it", silently un-fixing the
                # INLINE stale twin that rounds 6 and 7 closed (measured: inline
                # append to either carrier PASSED at the previous commit). Compare
                # the token's basename, not its path prefix.
                if preceding and not preceding[-1].group(0).split("/")[-1].startswith(
                    "extractor_v2.py:"
                ):
                    continue
                kept.append((match.group(1), match.group(2)))
            return kept

        def _extent_numbers(
            block: str,
            _re: re.Pattern[str] = _extent_cite,
            _bare: re.Pattern[str] = _bare_range,
        ) -> list[int]:
            numbers: list[int] = []
            for lo, hi in _re.findall(block):
                numbers.append(int(lo))
                if hi:
                    numbers.append(int(hi))
            for line in block.splitlines():
                for lo, hi in _bare_ranges(line, _bare):
                    numbers.extend((int(lo), int(hi)))
            return numbers

        def _reversed_ranges(
            block: str,
            _re: re.Pattern[str] = _extent_cite,
        ) -> list[str]:
            pairs = [(lo, hi) for lo, hi in _re.findall(block) if hi]
            for line in block.splitlines():
                pairs.extend(_bare_ranges(line))
            return [f"{lo}-{hi}" for lo, hi in pairs if int(lo) >= int(hi)]

        ranged = [block for block in extent_carriers if len(_extent_numbers(block)) >= 2]
        assert ranged, (
            f"{doc_name} names `_collect_classify_items` but never its extent "
            f"(tortoise/extractor_v2.py:{collector}-{end}) — the `def` through "
            "`return items` is the claim §11's argument rests on")
        for block in ranged:
            # ⛔ THE SET, NOT A MEMBERSHIP TEST (review round 3, P2). A bare
            # `in block` is satisfied by the CORRECT range even when a STALE one
            # sits beside it in the same sentence — measured: appending
            # ", formerly tortoise/extractor_v2.py:11098-11120" next to the right
            # range left this leg `1 passed`. A stale cite with a correct twin is
            # the same defect, so the block must cite the extent ONCE and right.
            found = sorted(_extent_numbers(block))
            assert not _reversed_ranges(block), (
                f"{doc_name}: a live carrier cites a REVERSED range "
                f"{_reversed_ranges(block)} — a range reads low-to-high, and a set "
                "comparison cannot tell `:11196-11174` from the extent it inverts")
            assert found == sorted({collector, end}), (
                f"{doc_name}: a live carrier naming `_collect_classify_items` cites "
                f"position(s) {found or ['<none>']} — it must cite exactly "
                f"tortoise/extractor_v2.py:{collector}-{end} "
                "(the `def` through `return items`). A range that runs past the "
                "body names the next function's lines, and a STALE range left "
                "beside the right one is the same defect with a correct twin")
        # ⛔ EVERY LIVE CARRIER MUST NAME BOTH GATES, not just one of them anywhere
        # in the file. With two live carriers (the §9 provenance row and §11.1's prose)
        # a single whole-document check is satisfied by whichever one is correct,
        # so a stale prose cite rode the row's numbers (measured: reverting the
        # prose alone left this leg `1 passed` at `6c7a15f2a5`).
        gate_blocks = [b for b in _live_blocks(text) if "`if items:`" in b]
        assert gate_blocks, (
            f"{doc_name} no longer cites `if items:` anywhere — S2.3's "
            "conditionality is stated by that phrase, so this leg cannot find what "
            "it guards. Re-word the pin if the prose legitimately moved off it")
        for block in gate_blocks:
            for gate in gates:
                assert f"`{gate}`" in block, (
                    f"{doc_name}: the live `if items:` passage "
                    f"{block.strip()[:90]!r} does not cite the gate at {gate} — "
                    f"every live carrier of that phrase must name BOTH gates "
                    f"({gates[0]}, {gates[1]}), because the classify passes are "
                    f"gated on the collector's list being non-empty, which is the "
                    f"whole of S2.3's conditionality. A §10.x history entry does "
                    f"NOT satisfy this: history is stripped before the check")
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


# ── A cite into the RUN probe by NUMBER is unpinnable, so it must not be written ──
#
# `_BUNDLE_CITE` resolves `sdk|edges|extractor_v2` and `_BUNDLE_TOOL_SOURCES`
# names only the GATE, so a `probes/capstone_run.py:<line>` cite is read by NO
# sweep in this file: not resolved, and — unlike a bare `:NNNN` continuation —
# not even reported `unmatched`, because it carries a file name the sweep never
# looks for. The bundle's own convention for a number in that position is
# the README's *"written here WITHOUT the `file:line` form on purpose … do NOT
# follow them as pointers at this head"* paragraph: the SYMBOL is authoritative, the
# line is a convenience nothing re-derives. Cited by SENTENCE, and with NO typed
# range at all: that paragraph's bounds move with every insertion above it, and a
# number in this position had already walked three times — so the SYMBOL carries
# the identity and the range is deliberately not restated here.
#
# Measured provenance, stated exactly (the previous revision of this comment
# misstated both halves, and this file's own docstring below contradicted it):
# the README's sentence carried `capstone_run.py:813`, and `74d7d1f5e` re-pointed
# it at the SYMBOL — `git log -S'capstone_run.py:19' -- docs/evidence/
# 7949-capstone/README.md` returns NO commit, so the README never carried `:19`.
# The `capstone_run.py:19` / `README.md:615` pair belongs to the SAME class one
# file over: `4fbd69844` (main) recorded it in
# `tests/test_7949_capstone_verdict_check.py`'s entry-point paragraph, and this
# branch's `ef8d1ac08` dropped those two numbers from that paragraph — because it
# outlived two heads and a quoted number IS the drift the paragraph describes.
_PROBE_NUMERIC_CITE = re.compile(r"capstone_run\.py:(\d+)")

#: The line this leg holds the README to, BY SYMBOL (never by a typed number).
_PROBE_ENTRY_POINT_FALLBACK = 'required = run.get("steps_required") or list(STEPS)'


def test_no_document_cites_the_run_probe_by_a_number_nothing_can_resolve():
    """A `capstone_run.py:<line>` cite is invisible to every sweep — refuse it.

    `_BUNDLE_CITE` matches `sdk|edges|extractor_v2` only, and
    `_BUNDLE_TOOL_SOURCES` names just `capstone_verdict_check`, so a number
    written into the RUN probe was checked by nothing: `grep -c '813'` across
    both pin files returns 0/0. The README carried one
    (`probes/capstone_run.py:813`, the `steps_required` fallback). It was
    accurate the day it was written and had no reader, in the file that already
    carries the precedent above.

    So this leg does two things and needs both: it REFUSES the numeric form in
    every bundle document, and it requires the sentence to keep its content by
    naming the SYMBOL — a bare ban would pass vacuously on a bundle that deleted
    the claim instead of re-pointing it.

    Non-vacuity: at the pre-fix head the README's cite is present, so the ban
    reds there; and the symbol leg reds on a README that drops the fallback
    sentence. If a future lane genuinely wants the number, the route the review
    named is to add a `_BUNDLE_TOOL_SOURCES` row plus a phrase anchor, and to
    retarget this ban — never to type the number back in.
    """
    probe_lines = (_REPO / "docs/evidence/7949-capstone/probes/capstone_run.py"
                   ).read_text(encoding="utf-8").splitlines()
    fallbacks = [i + 1 for i, ln in enumerate(probe_lines)
                 if _PROBE_ENTRY_POINT_FALLBACK in ln]
    assert len(fallbacks) == 1, (
        f"{len(fallbacks)} lines of the run probe carry the `steps_required` "
        f"fallback ({fallbacks}) — this leg resolves the README's sentence "
        "against ONE symbol; re-derive which line the paragraph means before "
        "trusting a number here")
    fallback_line = fallbacks[0]

    # 1. The class ban: no bundle document may cite the run probe by number.
    offenders = []
    for name, path in _BUNDLE_DOCS:
        for match in _PROBE_NUMERIC_CITE.finditer(
                path.read_text(encoding="utf-8")):
            offenders.append(f"{name} -> capstone_run.py:{match.group(1)}")
    assert not offenders, (
        "a `capstone_run.py:<line>` cite is read by NO sweep in this file — "
        "`_BUNDLE_CITE` resolves `sdk|edges|extractor_v2` only and "
        "`_BUNDLE_TOOL_SOURCES` names only the gate, so the number rots "
        f"silently: {offenders}. Cite the SYMBOL instead (the bundle's own "
        "convention, its `file:line`-is-not-a-live-cite paragraph), or add a "
        "resolver row for the probe "
        "before writing a number into it")

    # 2. The claim survives as a symbol — the ban above must not be satisfied
    #    by deleting the sentence (that would trade a rotting cite for a
    #    dropped measurement, and the README's paragraph on the committed log
    #    predating the conditional split is where it tells a consumer that —
    #    `E1-E5-PROOFS.md:219` says it too, so "only place" was never true). ⛔ Cited
    #    by SENTENCE, not by line number: that range was `809-815`, then `918-923`,
    #    then `922-927` as this branch's own README insertions walked it, and a
    #    comment carrying the number was stale within one commit every time. The
    #    assertion below resolves the SYMBOL, which is why no stale number ever
    #    redded anything.
    readme_lines = (_REPO / "docs/evidence/7949-capstone/README.md").read_text(
        encoding="utf-8").splitlines()
    carrying = [i + 1 for i, ln in enumerate(readme_lines)
                if _PROBE_ENTRY_POINT_FALLBACK in ln]
    assert len(carrying) == 1, (
        "the README must name the run probe's `steps_required` fallback on "
        f"exactly one line (found {carrying}); that sentence is what tells a "
        f"consumer the shipped log predates the conditional split, and the "
        f"symbol it cites is `capstone_run.py:{fallback_line}` at this head")

    # 3. …and the sentence must still say WHICH probe it is about, because the
    #    symbol alone (`required = … or list(STEPS)`) appears in both probes.
    assert "capstone_run.py" in "\n".join(
        readme_lines[max(0, carrying[0] - 3):carrying[0] + 2]), (
        "the fallback sentence must still name `capstone_run.py` — both probes "
        "carry that fallback, and this paragraph is about the RUN probe")


# ── E2's blocker names the LIVE half, not the closed render issue ─────────
#
# PR #10382 closed #9775's render half: every path that builds a prompt now
# carries `STATE_VALUE_CARVE_OUT`, the compact render included. What still blocks
# E2 is the OTHER half — nothing in the write path ENFORCES retention — plus the
# value loss (13 of 14 lost when #10382's round landed; 6 of 14 lost on the
# `#9889` record, `docs/evidence/9889/e1-e2-scores.json`) that sits upstream of the
# filter, and both are #10360. Until
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


#: The BOLD verdict token the `#9080` row states E2 under. The selector reads the
#: TOKEN (`**E2 <WORD>**`), not one literal word, because the word is the part that
#: MOVES: `#9889`'s record scores this row RED (8 of 14), and `docs/00_index.md` says
#: in the same breath that "the row is not moved here, because a row moves only by
#: re-running the probe". So the index still reads `**E2 BLOCKED**` at this head and
#: a `BLOCKED`-keyed selector would drop the row out of this leg's subject the moment
#: the re-run moves the token — the leg would then pass over nothing, which is the
#: exact failure `assert e2_verdict_rows` below is written to refuse.
#: (#10610 review cycle 2, P3: an earlier revision said review issue 1 HAD moved the
#: row to `E2 RED`. Measured false — every branch revision of that file at this head
#: reads `**E2 BLOCKED**`, and `origin/main` reads the same — so the sentence is
#: restored to the hypothetical it can support.)
_E2_VERDICT_ROW = re.compile(r"\*\*E2 (?:BLOCKED|RED|GREEN)\*\*")


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
    #    that states E2's verdict must name the live blocker and must not name the
    #    closed one, whichever words it uses to do it — AND whichever verdict word
    #    it uses. The selector below reads the BOLD verdict token (`**E2 <WORD>**`),
    #    not one literal: `#9889`'s record scores this row RED, and `docs/00_index.md`
    #    states that "the row is not moved here, because a row moves only by
    #    re-running the probe". So the row reads `E2 BLOCKED` today and will read
    #    `E2 RED` after that re-run, and a selector keyed on `BLOCKED` would drop it
    #    out of this leg's subject on the day the verdict legitimately moves —
    #    quietly, since E1's token (`**E1 REFUSED as shipped**`) sits in the same
    #    line and the leg would still find a row to assert over.
    e2_verdict_rows = [line for line in index.splitlines()
                       if _E2_VERDICT_ROW.search(line)]
    assert e2_verdict_rows, (
        "no line of docs/00_index.md states E2's verdict (`**E2 BLOCKED**` / "
        "`**E2 RED**` / `**E2 GREEN**`) — this leg is vacuous (it would pass on "
        "an index that stopped routing E2 at all)")
    for row in e2_verdict_rows:
        assert "`#10360`" in row, (
            "an index row marks E2 blocked without naming the live blocker "
            f"`#10360`: {row[:400]}")
        assert "`#9775`" not in row, (
            "an index row still routes a reader to `#9775` as E2's blocker — "
            "#9775's render half is CLOSED (PR #10382), so the live blocker is "
            f"`#10360`: {row[:400]}")


# ── the bundle's dependency table must agree with the COMMITTED blocker record ──────
#
# V12 makes this a GATE for the verdict table's own `blocked_by` pointers. The bundle's
# "Dependency state" table is the SAME claim one file over — a row reading `**OPEN**`
# about an issue the committed `blocker-states.json` records CLOSED is stale evidence by
# construction, which is the exact defect #10537 exists to stop.
#
# Measured at `7caace29c` (this repo, before the fix in the commit that added this pin):
# the table still said `#8503` was `**OPEN** — E4's blocker` and `#9738` was
# `**OPEN — owner-gated**`, while the record the SAME PR added records both CLOSED
# (`2026-10-24T20:50:13Z` / `2026-11-02T08:53:06Z`). No pin covered it: the E2 test above
# reads E2's row by name, so it cannot see a stale blocker on E4 or E5. The class is
# mechanical, so the pin is too — it reads EVERY row of the table against the record
# rather than the spellings this one commit happened to leave behind.

#: `| `#NNNN` <title> | <state cell> |` — the dependency table's own shape.
_BUNDLE_DEP_ROW = re.compile(r"^\|\s*`#(?P<issue>\d+)`[^|]*\|\s*(?P<state>[^|]*)\|")


def test_the_bundle_dependency_table_never_reports_a_recorded_closed_issue_as_open():
    """A dependency row may not call an issue OPEN when the record says CLOSED.

    Non-vacuity: the test asserts it actually PARSED rows, that at least one of them
    claims OPEN (so the comparison has something to bite on), and that the record it
    reads carries the schema V12 reads — a pin that finds no rows, or compares against
    an unreadable record, must fail rather than pass silently.

    Measured red on the pre-fix text: rows `#8503` and `#9738` both claimed OPEN
    against `CLOSED` states in the same tree.
    """
    record_path = _REPO / "docs/evidence/7949-capstone/blocker-states.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record.get("schema") == "capstone-blocker-states-v1", (
        f"{record_path} carries schema {record.get('schema')!r}; this pin reads the "
        "same record V12 does and cannot compare against another shape")
    blockers = record.get("blockers") or {}
    assert blockers, f"{record_path} lists no blockers — nothing to compare against"

    readme = (_REPO / "docs/evidence/7949-capstone/README.md").read_text(encoding="utf-8")
    rows = [m for m in (_BUNDLE_DEP_ROW.match(line) for line in readme.splitlines()) if m]
    assert len(rows) >= 5, (
        f"parsed only {len(rows)} dependency row(s) from the bundle README — this pin is "
        "vacuous on a README whose table was renamed or removed")

    claims_open = [m for m in rows if m.group("state").strip().lstrip("*").upper().startswith("OPEN")]
    assert claims_open, (
        "no dependency row claims OPEN — this pin cannot see the claim it exists to "
        "check, so it would pass on a table that stopped carrying states at all")

    stale = [(m.group("issue"), m.group("state").strip())
             for m in claims_open
             if blockers.get(f"#{m.group('issue')}", {}).get("state") == "CLOSED"]
    assert not stale, (
        "the bundle's dependency table calls an issue OPEN that the committed "
        f"blocker record says is CLOSED: {stale} — a row routing a lane to finished "
        "work is stale evidence by construction (this is what V12 refuses inside the "
        "verdict table, one file over). Re-attribute the row to the issue that owns "
        "the residual, as E4 `#8503`->`#9596` and E5 `#9738`->`#5087` did, or refresh "
        f"{record_path.name} from the tracker if the tracker truly re-opened one")
# ── the three routes to E1/E2's verdict state the RECORD's verdict (#10610 issue 1) ──────────

#: `capstone_measure.verdict_word`'s vocabulary, keyed by the record's own outcome word: a
#: REFUSED measurement is BLOCKED (neither a pass nor a fail), a FAIL over a decided
#: population is RED, a PASS is GREEN.
_ROW_VERDICT = {"REFUSED": "BLOCKED", "FAIL": "RED", "PASS": "GREEN"}

#: The bundle's verdict column — `⛔ **BLOCKED** (n=1 per arm …)`, `⛔ **RED** (8 of 14)`.
_README_VERDICT = re.compile(r"(?:⛔|✅)\s*\*\*(BLOCKED|RED|GREEN)\b")

#: The claim #10610 review issue 1 found in TWO documents after both rows moved off
#: `e1-e2.log`: that the frozen 14-unit denominator "is carried by no committed artifact".
#: The premise is CHECKED below (the record names the artifact, the artifact exists, and its
#: denominator IS the frozen 14), so these are REFUTED phrases, not merely unwelcome ones —
#: if the artifact were ever withdrawn the pin reds on its premise instead of banning truth.
#:
#: ⛔ SCOPE, stated rather than implied (#10610 review at `5f636d2f`): this scans the two
#: ROUTING surfaces — `docs/00_index.md` and `E1-E5-PROOFS.md`, the documents a reader is
#: sent through to reach the rows. It deliberately does NOT scan
#: `docs/evidence/7949-capstone/README.md`, which NARRATES the superseded log and is allowed
#: to quote what that log said in a section the document itself marks as history; the
#: bundle README's own present-tense claims are held by its artifact-table and
#: emitter-vs-artifact pins instead. A phrase ban is also evadable by markup
#: (`**no committed artifact**` defeats a plain substring), which is why the ROUTING docs
#: are held to the fact — the reader exists — and not only to the spelling.
_UNCARRIED_DENOMINATOR = (
    "carried by no committed artifact",
    "no committed artifact carries the registered denominators",
)


def test_the_three_routes_to_the_e1_e2_verdict_state_the_records_verdict():
    """A document that routes a reader to E1/E2 must state the verdict the record gives.

    #10610 review issue 1 (P2): the change re-pointed both rows at
    `docs/evidence/9889/e1-e2-scores.json` — so a RE-RUN now scores E2 `RED 8/14`
    where the committed row still reads `BLOCKED` (a row moves only by re-running
    the probe; the correction on `_E2_VERDICT_ROW` above records that an earlier
    revision of THIS docstring wrongly said the change had already moved it) — and
    the two documents a reader is routed to kept asserting the superseded reading
    IN THE PRESENT TENSE — `docs/00_index.md`'s `#9080` row (`3 RED + 2 BLOCKED`, "E1 … its only artifact
    is 25 Objects … with no before arm", "E2 … carried by no committed artifact") and
    `E1-E5-PROOFS.md`'s E2a/E2b rows ("the bundle's README marks the row BLOCKED because
    the frozen 14-unit denominator is carried by no committed artifact"), whose
    `README.md:69` pointer now lands on a row that NAMES the re-run reading
    (`RED 8/14`) while its committed verdict cell still reads
    `⛔ **BLOCKED** (#10360)`. Nothing read that prose, so the suite stayed green while the
    bundle's own entry point told a reader the opposite of the table beside it.
    The cite is now held by `test_the_e2_routing_claim_cites_the_row_it_describes`
    below, which takes the LINE NUMBER from this docstring: review at `7c03efc1d`
    found the previous revision had re-pointed it at `README.md:65`, which is `>` — a
    blank blockquote line above the table header on `:66` — while quoting a reading
    that appears in neither bundle README.

    Every leg is DERIVED, and each document is held to the ARTIFACT IT DESCRIBES — which
    after the 2026-11-02 re-measure is two different artifacts, deliberately:

    * the bundle's verdict table (leg 2) describes THE RUN the committed scored log records,
      so it is pinned to the LOG — a row moves only by re-running the probe, and a table that
      jumped ahead of its artifact would be the stale-evidence class `#10537` exists to stop;
    * the `#9080` index row (leg 3) is held to that same table, plus the record's measured
      numbers;
    * the audit's per-row table (leg 4) audits the CRITERIA against current evidence, so it
      is pinned to the RECORD — it may name the frozen table's `BLOCKED` as the committed
      state, but the reading it states must be the record's.

    Nothing is a typed expectation: verdicts come from the record's `outcome`, counts from
    its own pairings and E2a block, and the headline (`N RED + M BLOCKED`) from the bundle
    README's five verdict rows, so a re-run that flips an outcome moves the documents or
    reds this pin.

    Non-vacuity, measured at `e989c6449` (the head this pin was written against) and
    re-measured at every review round since: the index row stated neither the record's E1
    word nor E2's row verdict while carrying the 1/14 reading, the proofs E2a row carried
    `**BLOCKED**` alone against a record that says FAIL, and the `_UNCARRIED_DENOMINATOR`
    phrases were present in both files — so legs 3, 4 and 5 each red there. Every leg since
    has been mutation-verified at `9ed67a02`: an audit row stating `**GREEN**` reds leg 4,
    and so does a table that has CAUGHT UP to the record while the row still names the old
    frozen word (the mirror that stops that sentence rotting once the re-run lands). Leg 1
    makes the phrase ban honest rather than rhetorical: the record carries two before arms
    and the E2a artifact carries the frozen 14.

    ⛔ No leg bans `"with no before arm"`. That phrase is TRUE about the measurement run
    (`capstone-measured-9152448d.log` says "this run has no before arm"), and the index
    states it in the same sentence that reports the two before arms `#9889` produced; leg 3
    is what holds the row to those two shares, without needing to ban a true sentence.
    """
    record = json.loads((_REPO / "docs/evidence/9889/e1-e2-scores.json")
                        .read_text(encoding="utf-8"))

    # 1. The PREMISE, measured: two before arms exist, and the denominator the two
    #    documents called "uncarried" is carried by the artifact the record names.
    pairings = record["e1"]["pairings"]
    assert len(pairings) == 2, (
        "the record no longer carries two before arms — `with no before arm` would stop "
        "being a refuted claim, so the ban below would be redding on a TRUE sentence")
    e2a = record["e2"]["e2a"]
    denominator = record["denominators"]["e2a_units"]
    assert (e2a["value_units"], e2a["frozen_units"], denominator) == (14, 14, 14), (
        "the E2a block and the frozen denominator disagree — reconcile them before the "
        f"documents can be held to either: {(e2a['value_units'], e2a['frozen_units'], denominator)}")
    for rel in (e2a["artifact"], e2a["falsifier_artifact"]):
        assert (_REPO / rel).is_file(), (
            f"{rel} is missing, so the frozen denominator is uncarried again — this pin "
            "would now be banning a true sentence, not a refuted one")

    # 2. The bundle's verdict table describes THE RUN the committed scored log records, and it
    #    is held to THAT ARTIFACT — not to the record. The distinction is the whole point:
    #    a row moves only by re-running the probe (§"Two artifacts"), and this PR moves the
    #    BUILDER. So the table is pinned to the log, and the gap between log and builder is
    #    pinned separately, below — otherwise this leg would demand a RED the frozen artifact
    #    does not carry, i.e. exactly the stale-evidence class #10537 exists to refuse.
    listed = [ln for ln in (_REPO / "docs/evidence/7949-capstone/README.md")
              .read_text(encoding="utf-8").splitlines()
              if re.match(r"^> \| \*\*E[1-5]\*\* ", ln)]
    assert len(listed) == 5, (
        f"the README's verdict table lists {len(listed)} rows, not 5 — the headline this "
        "pin derives has no subject until the table is whole")
    table = {ln.split("**")[1]: _README_VERDICT.findall(ln)[0] for ln in listed}
    log = _newest_log(_scored_logs())
    log_rows = {row["criterion"]: row
                for row in cvc.extract_block(log.read_text())["rows"]}
    for criterion in ("E1", "E2", "E3", "E4", "E5"):
        assert table[criterion] == log_rows[criterion]["verdict"], (
            f"README says {criterion} is {table[criterion]}, but {log.name} — the artifact "
            f"the table describes — says {log_rows[criterion]['verdict']}: {log_rows[criterion]['evidence']}")
    headline = (f"{sum(1 for w in table.values() if w == 'RED')} RED + "
                f"{sum(1 for w in table.values() if w == 'BLOCKED')} BLOCKED")

    # ...and WHICH rows may legitimately differ is DERIVED, not assumed: a pre-re-point log
    # carries E1/E2 on the superseded `e1-e2.log` while the builder emits the record. Assert
    # the explanation the disclosure rests on, so the two documents' difference is a stated
    # fact rather than a silent disagreement.
    superseded_log = "docs/evidence/7949-capstone/e1-e2.log"
    stale = sorted(c for c in ("E1", "E2")
                   if log_rows[c]["evidence"] == superseded_log)
    emitted = {row["criterion"]: row["evidence"] for row in _rows()}
    assert emitted["E1"] == emitted["E2"], (
        f"the builder emits E1 and E2 from different artifacts ({emitted['E1']!r} vs "
        f"{emitted['E2']!r}) — #9889 item 5 re-points BOTH, so a split is a routing bug")
    if stale:
        assert stale == ["E1", "E2"], (
            f"only ONE of the shipped log's E1/E2 rows is pre-re-point: {stale} — a state "
            "no stamp can describe")
        assert emitted["E1"] == "docs/evidence/9889/e1-e2-scores.json", (
            "the log's E1/E2 rows predate the re-point, but the BUILDER does not emit the "
            f"record either ({emitted['E1']!r}) — then there is nothing to disclose")
    else:
        assert table["E1"] == _ROW_VERDICT[record["e1"]["outcome"]], (
            f"{log.name} now carries the record's E1 row, so the table must state the "
            f"record's verdict — README says {table['E1']}, the record "
            f"{record['e1']['outcome']!r}")
        assert table["E2"] == _ROW_VERDICT[record["e2"]["outcome"]], (
            f"{log.name} now carries the record's E2 row, so the table must state the "
            f"record's verdict — README says {table['E2']}, the record "
            f"{record['e2']['outcome']!r}")

    # 3. The `#9080` row — the route a reader meets first — states E2's verdict, the
    #    headline the table gives, and the numbers the record measured.
    index = (_REPO / "docs/00_index.md").read_text(encoding="utf-8")
    route = next(line for line in index.splitlines() if "#9080" in line)
    assert f"**E2 {table['E2']}**" in route, (
        f"the `#9080` row does not state E2's verdict as `**E2 {table['E2']}**` — the "
        f"record's own outcome is {record['e2']['outcome']!r}: {route[:400]}")
    assert f"E1 {record['e1']['outcome']}" in route or f"E1 {table['E1']}" in route, (
        f"the `#9080` row states neither the record's own word ({record['e1']['outcome']!r}) "
        f"nor the row verdict ({table['E1']!r}) for E1: {route[:400]}")
    assert headline in route, (
        f"the `#9080` row's headline is not the verdict table's own count ({headline!r}) — "
        f"the README lists {' / '.join(f'{k}:{v}' for k, v in table.items())}")
    for value in [p["before_share"] for p in pairings] + [pairings[0]["after_share"]]:
        assert f"{value:.4f}" in route, (
            f"the `#9080` row does not carry the record's measured share {value:.4f} — a row "
            "that prints the superseded run's numbers describes a population the record "
            f"does not: {route[:400]}")

    # 3b. THE BARS, derived from the frozen pre-registration — #10610 review at `e079c8dd`.
    #     Every count and verdict on these rows was pinned and the BARS were not, so the
    #     audit could print "8 of 14 ... against `>= 6 of 14`" under a RED that MEETS its own
    #     printed bar, with every gate green (measured: `>= 13 of 14` -> `>= 6 of 14` in both
    #     docs, `<= 0.05` -> `<= 0.5`, all passed). A bar typed into a document is a bar nobody
    #     froze, so read it from the record through the bundle's ONE reader.
    bars = measure.parse_bars(
        json.loads((_REPO / "docs/evidence/7949-capstone/preregistration.json")
                   .read_text(encoding="utf-8")))
    e2a_bar = f">= {bars['E2a']['required']} of {bars['E2a']['of']}"
    e2b_bar = f"<= {bars['E2b']['max_fused_share']}"
    assert (bars["E2a"]["required"], bars["E2a"]["of"]) == \
        (e2a["required"], e2a["frozen_units"]), (
            "the scoring record's E2a bar and the pre-registration's disagree — reconcile "
            f"them before a document can be held to either: {(e2a['required'], e2a['frozen_units'])} "
            f"vs {(bars['E2a']['required'], bars['E2a']['of'])}")
    e2_table_row = next(ln for ln in listed if ln.startswith("> | **E2**"))
    for surface, text_row in (("the README's E2 verdict cell", e2_table_row),
                              ("the `#9080` index row", route)):
        assert e2a_bar in text_row, (
            f"{surface} does not print the frozen E2a bar {e2a_bar!r}: {text_row[:400]}")
        assert e2b_bar in text_row, (
            f"{surface} does not print the frozen E2b bar {e2b_bar!r}: {text_row[:400]}")

    # 4. The AUDIT is not the run's mirror — it audits the criteria against CURRENT evidence,
    #    so it is held to the RECORD, not to the frozen log's table. That difference is
    #    deliberate and is exactly what legs 2 and 4 together pin: the table may lag (a row
    #    moves only by re-running the probe), the audit may not.
    proofs = (_REPO / "docs/evidence/7949-capstone/E1-E5-PROOFS.md").read_text(
        encoding="utf-8")
    e2_rows = [ln for ln in proofs.splitlines() if re.match(r"^\| \*\*E2[ab]\*\*", ln)]
    assert len(e2_rows) == 2, (
        f"the audit's E2a + E2b rows are {len(e2_rows)}, not 2 — the audit stopped "
        "auditing one half of E2, so this leg has no subject")
    for audit_row, bar in ((e2_rows[0], e2a_bar), (e2_rows[1], e2b_bar)):
        assert bar in audit_row, (
            f"an audit row does not print its frozen bar {bar!r} — a bar a document types "
            f"is a bar nobody froze: {audit_row[:400]}")
    survived = f"{e2a['value_survived']} of {e2a['frozen_units']}"
    recorded = _ROW_VERDICT[record["e2"]["outcome"]]
    # ⛔ THE VERDICT WORDS AS A SET, not substrings (#10610 review at `5f636d2f`, then at
    # `9ed67a02`). Two separate defects lived here: a bare `recorded in row` is
    # unconditionally true because the row says "MEASURED" (which CONTAINS "RED"), and an
    # `or` whose first branch was satisfied by the row's own legitimate mention of the
    # committed table's word. Reading the set fixes both AND closes the rot in the other
    # direction: while the table lags, the audit row must name BOTH words (one as the
    # frozen state, one as the measured reading); once a re-recorded log makes the table
    # agree with the record, the second word becomes a FALSE claim and the set must
    # collapse to one — so the sentence cannot survive the re-run it describes.
    words = set(re.findall(r"\*\*(BLOCKED|RED|GREEN)\b", e2_rows[0]))
    expected = {recorded} | ({table["E2"]} if table["E2"] != recorded else set())
    assert words == expected, (
        f"the audit's E2a row states {sorted(words)}, but the record scores {recorded}"
        + (f" while the frozen table says {table['E2']}" if table["E2"] != recorded
           else " and the frozen table now agrees with it")
        + f" — so the row must state exactly {sorted(expected)}: {e2_rows[0][:400]}")
    # ⛔ BOLD STANDALONE TOKENS, not `"8 of 14" in row` (#10610 review at `42143b80`). The
    # count appears TWICE in the row — once as the measured clause and once inside the re-run
    # clause `**RED (8 of 14)**` — so a plain substring test is satisfied by the second while
    # the first is mutated. Measured: rewriting the measured clause to `**7 of 14**` (or
    # `**8 of 12**`) left this pin GREEN, with the row asserting two contradicting counts and
    # the record refuting one. The bold-ratio SET makes a second, disagreeing figure
    # impossible to carry — and the pattern deliberately also reads the PARENTHESISED form
    # `**RED (8 of 14)**`, because a set that skips it is evaded by mutating the restated
    # count alone (measured at `e079c8dd`: `**RED (7 of 14)**` passed the narrower pattern).
    bold_ratios = set(re.findall(r"\*\*(?:[A-Z]{3,} \()?(\d+) of (\d+)\)?\*\*", e2_rows[0]))
    expected_ratio = {(str(e2a["value_survived"]), str(e2a["frozen_units"]))}
    assert bold_ratios == expected_ratio, (
        f"the audit's E2a row states the bold counts {sorted(bold_ratios)}, but the record "
        f"scores {sorted(expected_ratio)} — a second disagreeing count beside the record's "
        f"is two measurements of one criterion: {e2_rows[0][:400]}")
    assert e2a["artifact"] in e2_rows[0], (
        f"the audit's E2a row does not carry the record's artifact ({e2a['artifact']!r}): "
        f"{e2_rows[0][:400]}")

    # ⛔ THE DEPENDENCY ROW'S LOSS COUNT IS DERIVED TOO (#10610 review at `21b2af22`, P3).
    # The `| E2 |` row states the surviving-value loss to argue that the row needs the
    # EXTRACTION loss fixed rather than the write-path filter (`#10360`) — and it stated
    # `(1 of 14)`, the SUPERSEDED `#9775` reading, while the record scores 8 of 14 surviving
    # (so 6 lost). Nothing read that row, so the audit a lane opens to decide whether E2 can
    # move told that lane the loss was 13 of 14. Derive it from the record: a count typed
    # into prose is a count nobody froze.
    dep_rows = [ln for ln in proofs.splitlines() if re.match(r"^\| E2 \|", ln)]
    assert len(dep_rows) == 1, (
        f"the audit carries {len(dep_rows)} dependency rows for E2, not 1 — this leg has no "
        "subject")
    lost = e2a["frozen_units"] - e2a["value_survived"]
    assert f"`{lost} of {e2a['frozen_units']}` lost" in dep_rows[0], (
        f"the E2 dependency row does not state the record's loss (`{lost} of "
        f"{e2a['frozen_units']}` lost, i.e. {e2a['value_survived']} surviving) — a loss count "
        f"that disagrees with the record tells a lane the wrong dependency: {dep_rows[0][:400]}")

    # ⛔ …AND THE SAME LOSS COUNT ON THE INDEX ROW, which is the SECOND copy of that claim and
    # the route a reader meets first (review). The leg above derives the loss for `E1-E5-PROOFS`
    # only, so `docs/00_index.md`'s copy was read by nothing: it sat at "the 1-of-14 value loss"
    # — the superseded `#9775` reading — while the same row printed `RED 8/14` and the record
    # scored 8 of 14 surviving, i.e. `6 of 14` lost. Measured: the suite stayed green over that
    # contradiction. One criterion, two documents, one derivation —
    # the same rule the dependency row is held to, so a count typed into either row is refused.
    assert f"`{lost} of {e2a['frozen_units']}` lost" in route, (
        f"the `#9080` index row does not state the record's loss (`{lost} of "
        f"{e2a['frozen_units']}` lost, i.e. {e2a['value_survived']} surviving) while the same "
        f"row states RED {e2a['value_survived']}/{e2a['frozen_units']} — a loss count that "
        f"disagrees with the record tells a lane the wrong dependency: {route[:400]}")
    assert "1-of-14" not in route, (
        "the `#9080` index row still carries the SUPERSEDED `1-of-14` loss reading beside the "
        f"record's `{lost} of {e2a['frozen_units']}` — two present-tense loss counts for one "
        f"criterion: {route[:400]}")

    # The E2b row is COUNTED above but was never INSPECTED — so this diff's own new claim on
    # it (E2b's bar is MET at 1/114, and the RED belongs to the composite via E2a) could be
    # mutated to `3/114` with every pin green (measured at `42143b80`). Hold it to the record
    # the same way: the figures it prints, and the half it attributes the RED to.
    e2b = record["e2"]["e2b"]
    fused = f"{e2b['fused']}/{e2b['atomic_points']}"
    row_ratios = set(re.findall(r"(?<![\d/])(\d+/\d+)(?![\d/])", e2_rows[1]))
    assert row_ratios == {fused}, (
        f"the audit's E2b row states {sorted(row_ratios)}, but the record's fused figure is "
        f"{fused!r} — E2b's own bar is MET and the row must say so on the record's numbers: "
        f"{e2_rows[1][:400]}")
    assert f"{e2b['share']:.6f}" in e2_rows[1], (
        f"the audit's E2b row does not carry the record's fused share ({e2b['share']:.6f}): "
        f"{e2_rows[1][:400]}")
    # ⛔ THE TOKEN, not the substring (#10610 review at `75bfd9c6`). `"MET" in row` is
    # satisfied by `"NOT MET"` — measured: `**E2b's own bar is MET**` → `**… is NOT MET**`
    # left the whole suite green while the row claimed the RED belonged to E2b, which is the
    # exact misreading this assertion's message forbids. Same class as `bold_ratios` and the
    # verdict-word SET above, so it is pinned the same way.
    assert e2b["below_floor"] is False and "bar is MET" in e2_rows[1], (
        "the record says E2b is NOT below its floor, so the audit's E2b row must state that "
        f"its own bar is MET — otherwise the RED reads as E2b's verdict: {e2_rows[1][:400]}")
    assert "NOT MET" not in e2_rows[1], (
        "the audit's E2b row states its own bar is NOT MET while the record says the floor is "
        f"NOT breached — the RED then reads as E2b's verdict: {e2_rows[1][:400]}")
    assert record["e2"].get("decided_by") == "E2a" and "E2a" in e2_rows[1], (
        "the record attributes E2's FAIL to E2a, so the audit's E2b row must name the half "
        f"that decides it: {e2_rows[1][:400]}")
    # ⛔ …and SUPERSEDED as a BOLD TOKEN, for the same reason: `"is NOT SUPERSEDED"` contains
    # `"SUPERSEDED"`, and that mutation (two live 1/14 and 8/14 readings) also stayed green.
    assert ("is SUPERSEDED" in e2_rows[0] or "**SUPERSEDED**" in e2_rows[0]) and (
        "NOT SUPERSEDED" not in e2_rows[0]), (
        "the audit's E2a row must mark the reading it replaced as SUPERSEDED and only that "
        "— a superseded count left standing beside the live one is two present-tense "
        f"measurements of one criterion: {e2_rows[0][:400]}")

    # 4b. THE TWO E1 SURFACES, held to the record's own pairings — #10610 review at
    #     `75bfd9c6` (P3). Leg 2 pinned only the README TABLE's verdict WORD, and legs 3/4
    #     read the index row and the audit's E2 rows; the README's E1 verdict cell and the
    #     audit's `| **E1** |` row were read by nothing. Measured: rewriting the README's
    #     `0.3590` to `0.8123` in both of its sites, and the audit's "REFUSED at n=1 per arm"
    #     to "a DECIDED PASS at n=1 per arm", left all 147 cases green — two documents
    #     contradicting the committed record on the row `#9889` is waiting for.
    e1_readme_row = next(ln for ln in listed if ln.startswith("> | **E1**"))
    for pairing in pairings:
        before_text = f"**{pairing['before_objects']} Objects / {pairing['before_share']:.4f}**"
        assert before_text in e1_readme_row, (
            f"the README's E1 verdict cell does not carry the record's before arm "
            f"{before_text!r} — a cell that prints another run's numbers describes a "
            f"population the record does not: {e1_readme_row[:600]}")
        assert f"{pairing['after_share']:.4f}" in e1_readme_row, (
            f"the README's E1 verdict cell does not carry the record's after share "
            f"{pairing['after_share']:.4f}: {e1_readme_row[:600]}")
    assert f"**{pairings[0]['after_objects']} Objects / " \
           f"{pairings[0]['after_share']:.4f}**" in e1_readme_row, (
        "the README's E1 verdict cell does not carry the record's after arm as "
        f"`**{pairings[0]['after_objects']} Objects / {pairings[0]['after_share']:.4f}**`: "
        f"{e1_readme_row[:600]}")
    spread = f"{record['e1']['before_spread_pp']}"
    assert spread in e1_readme_row, (
        f"the README's E1 verdict cell does not carry the record's before spread "
        f"({spread} pp) — the spread is WHY the pairings disagree: {e1_readme_row[:600]}")
    # The audit's own E1 row: the row verdict THIS record gives, and no decided claim
    # while `decidable` is false. The `decidable` flag is the record's, so the ban is
    # conditional: once a re-run decides E1 at n >= 2, the row is free to say so.
    e1_audit_rows = [ln for ln in proofs.splitlines() if re.match(r"^\| \*\*E1\*\*", ln)]
    assert len(e1_audit_rows) == 1, (
        f"the audit carries {len(e1_audit_rows)} E1 rows, not 1 — this leg has no subject")
    # The audit quotes the RECORD's own word (`REFUSED`), where the README table maps it to
    # the row verdict (`BLOCKED`, `_ROW_VERDICT`) — two surfaces, two vocabularies, and the
    # audit's is the record's. Either is acceptable; whatever it says must be one of them.
    assert (record["e1"]["outcome"] in e1_audit_rows[0]
            or _ROW_VERDICT[record["e1"]["outcome"]] in e1_audit_rows[0]), (
        f"the audit's E1 row states neither the record's own word "
        f"({record['e1']['outcome']!r}) nor the row verdict for it "
        f"({_ROW_VERDICT[record['e1']['outcome']]}): {e1_audit_rows[0][:600]}")
    if record["e1"]["decidable"] is not True:
        assert "DECIDED" not in e1_audit_rows[0], (
            "the record's E1 is not decidable, so the audit's E1 row may not claim a "
            f"DECIDED verdict: {e1_audit_rows[0][:600]}")
        assert "n=1" in e1_audit_rows[0], (
            "the audit's E1 row does not state the replication that is missing (n=1 per "
            f"arm) — the reason the row is refused rather than undecided: "
            f"{e1_audit_rows[0][:600]}")

    # ⛔ THE E1 BAR AND THE TWO BOUNDS, DERIVED (#10610 review at `f12471075`, P2).
    # Leg 3b exists because "a bar typed into a document is a bar nobody froze", and it
    # pinned only E2's two bars. So the README's E1 row could print
    # `after_share <= 0.25 × before_share` (measured: green) and BOTH surfaces could
    # print the self-contradicting `0.2131 <= 0.9000 FAILS` / `0.2131 <= 0.1000
    # PASSES` (also green), because leg 3 pinned only the `.4f` token and never the
    # inequality around it.
    e1_multiplier = bars["E1"]["multiplier"]
    e1_bar = f"after_share <= {e1_multiplier:.2f} × before_share"
    assert e1_bar in e1_readme_row, (
        f"the README's E1 verdict cell does not print the frozen E1 bar {e1_bar!r} — a "
        f"bar typed into a document is a bar nobody froze: {e1_readme_row[:400]}")
    for pairing in pairings:
        bound = pairing["after_share"] and pairing["before_share"] * e1_multiplier
        word = "PASSES" if pairing["passed"] else "FAILS"
        # The surfaces print `after <= bound` and the word the RECORD's `passed` gives,
        # so a re-derived multiplier moves the document instead of silently disagreeing
        # with the record's own outcome.
        for surface, text_row in (("the README's E1 verdict cell", e1_readme_row),
                                  ("the `#9080` index row", route)):
            assert f"{pairing['after_share']:.4f} <= {bound:.4f}" in text_row, (
                f"{surface} does not print the record's own bound "
                f"`{pairing['after_share']:.4f} <= {bound:.4f}` for the before arm at "
                f"{pairing['before_share']:.4f}: {text_row[:500]}")
            assert word in text_row, (
                f"{surface} does not state {word} for the before arm at "
                f"{pairing['before_share']:.4f}, which the record scores "
                f"passed={pairing['passed']}: {text_row[:500]}")
    # The E2a count, in both routing surfaces: `RED 8/14` is the re-run reading, and
    # `RED 13/14` (or `8 of 12`) was measured green before this leg existed.
    e2a_count = f"{e2a['value_survived']}/{e2a['frozen_units']}"
    for surface, text_row in (("the README's E2 verdict cell", e2_table_row),
                              ("the `#9080` index row", route)):
        assert e2a_count in text_row, (
            f"{surface} does not carry the record's E2a count {e2a_count!r}: "
            f"{text_row[:500]}")
    # …and the AFTER arm's own Objects count on the first route a reader takes (it was
    # `61` in the record and `62` in a mutation that stayed green).
    assert f"**{pairings[0]['after_objects']} Objects / " \
           f"{pairings[0]['after_share']:.4f}**" in route, (
        "the `#9080` row does not carry the record's after arm as "
        f"`**{pairings[0]['after_objects']} Objects / {pairings[0]['after_share']:.4f}**`: "
        f"{route[:500]}")

    # 5. The refuted absence claim, in every routing surface.
    #
    # ⛔ NO BAN ON `"with no before arm"` (#10610 review at `9ed67a02`): that phrase is TRUE
    # about the measurement run — `capstone-measured-9152448d.log` says "this run has no
    # before arm", and `docs/00_index.md` says so in the same sentence that reports the two
    # before arms `#9889` later produced. The refuted claim is that the row has no before
    # arm NOW, and that is already held by leg 3, which requires the index row to carry both
    # measured before shares. A substring ban here would red a true sentence about a
    # different population, which is the "banning truth" failure this pin's own premise
    # check exists to avoid — so the phrase is deliberately NOT banned.
    for name, text in (("docs/00_index.md", index), ("E1-E5-PROOFS.md", proofs)):
        for phrase in _UNCARRIED_DENOMINATOR:
            assert phrase not in text, (
                f"{name} still asserts the frozen 14-unit denominator is {phrase!r}, which "
                f"{e2a['artifact']} refutes — the row it describes reads "
                f"{table['E2']} ({survived})")


def test_the_e2_routing_claim_cites_the_row_it_describes():
    """The leg's own `README.md:<line>` cite must land on the row it names.

    ⛔ WHY (#10610 review at `7c03efc1d`, item 2). The docstring of
    `test_the_three_routes_to_the_e1_e2_verdict_state_the_records_verdict` cites the
    bundle README by LINE NUMBER and quotes the reading that line carries. A merge
    resolution carried this branch's older revision of that sentence over `main`'s
    corrected one, so the cite went to `README.md:65` — a table-header line — while
    quoting a reading (`RED (8 of 14)`) that appears in NEITHER bundle README
    (`grep -c` = 0 in both), so the numbered claim resolved to nothing. Nothing
    redded: `test_every_readme_line_cite_carries_the_sentence_it_quotes` resolves
    only the cites inside `E1-E5-PROOFS.md` — the four it carries at this head are
    all DOCUMENT cites, none of them in a test docstring — so no pin read the cite
    in the sentence above. (An earlier revision of this paragraph said that document
    "carries none at this head", which its own pin refutes: that pin asserts the
    OPPOSITE, `assert found, "E1-E5-PROOFS.md carries no README.md line cite — pin is
    moot"`. The load-bearing half — no pin reads a test's own docstring — was and is
    true; #10610 review at `fdff8f432`, P3.)

    Derivation, not duplication: the line NUMBER and the quoted readings are both
    taken from the docstring, so this pin carries no count of its own — it holds the
    prose to the artifact the prose points at, and moving either side alone reds.

    Non-vacuity: at the pre-fix head this reds, because `README.md:65` is `>` — a blank
    blockquote line; the header the old sentence called a table header is on `:66` — so
    nothing the docstring attributes to the row appears there. Measured by re-pointing
    this docstring's cite back to 65: the pin reds with `cites README.md:65 for [...]
    but that line reads '>'`.
    """
    docstring = (test_the_three_routes_to_the_e1_e2_verdict_state_the_records_verdict
                 .__doc__ or "")
    lines = docstring.splitlines()
    at = next((i for i, ln in enumerate(lines) if "README.md:" in ln), None)
    assert at is not None, (
        "the E2 routing leg no longer cites the bundle README by line — if that claim "
        "moved, move this pin with it rather than leaving the cite unwatched")
    cite = re.search(r"README\.md:(\d+)", lines[at])
    assert cite is not None, f"unparseable README cite: {lines[at]!r}"
    cited_line = int(cite.group(1))
    # The window is the citing line plus the next three: the sentence wraps, and the
    # readings it attributes to the row are quoted on the following line(s).
    window = " ".join(lines[at:at + 4])
    quoted = [re.sub(r"[*`]", "", tok).strip()
              for tok in re.findall(r"`([^`]+)`", window)]
    # A cite to a FILE, a symbol or an issue is not a reading quoted FROM the line.
    cite_shaped = re.compile(r"[A-Za-z_][\w./-]*(?::[\d-]+)?")
    readings = [tok for tok in quoted
                if len(tok) >= 6 and not cite_shaped.fullmatch(tok)]
    assert readings, (
        f"the cite at {lines[at].strip()!r} quotes no reading, so it holds the row to "
        "nothing — re-state the sentence it points at")
    readme = (_REPO / "docs/evidence/7949-capstone/README.md").read_text(
        encoding="utf-8").splitlines()
    assert 1 <= cited_line <= len(readme), (
        f"the docstring cites README.md:{cited_line}, past the end of the "
        f"{len(readme)}-line README")
    cited = re.sub(r"[*`]", "", readme[cited_line - 1])
    # ⛔ EVERY quoted reading, not "any one of them" (#10610 review at `ec7b5e7fe`,
    # P3). The assertion here was `names_something` — a SINGLE match satisfied it —
    # and this window happens to quote TWO readings, `RED 8/14` and
    # `⛔ BLOCKED (#10360)`. So editing `RED 8/14` to `RED 9/14` left the pin GREEN
    # on the strength of the second quote: the load-bearing number, the one the row
    # exists to state, was the half nothing held. The docstring above claims "moving
    # either side alone reds", and with an any-match test that was measurably false
    # for one of the two sides it names — a pin whose subject can be falsified while
    # it stays green is the class this whole bundle exists to refuse.
    missing = [tok for tok in readings if tok not in cited]
    assert not missing, (
        f"the docstring cites README.md:{cited_line} for {readings}, but {missing} "
        f"appear on no such line — that line reads {cited[:160]!r} — the cite and the "
        "row it describes have drifted apart, and the numbered claim resolves to "
        "nothing")


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


# ── E4's artifact is selected by its OWN corpus hash, never by a filename ──────


def _receipt(path: Path, *, corpus_hash: str, edge_correct: int, date: str,
             planted: int = 15, audit: bool = True) -> Path:
    record = {"run_id": path.stem, "date": date, "commit": "a" * 9,
              "corpus_hash": corpus_hash, "verdict": "regression",
              "failure_origin": "gate_regression"}
    if audit:
        record["operator_audit"] = {"planted": planted, "edge_correct": edge_correct,
                                    "content_ok": 8, "operators_total": 27,
                                    "operators_provenanced": 27}
    path.write_text(json.dumps(record))
    return path


def test_e4_is_selected_by_the_receipts_own_corpus_hash_not_by_its_filename():
    """The E4 row's artifact is the receipt MEASURED ON THE FROZEN CORPUS.

    Non-vacuity, and the defect this pins. The probe used to read one hard-coded
    filename — ``w2b-operator-2026-10-19.json`` — whose own ``corpus_hash`` is
    ``943a99ec…``, not the frozen ``dd7e8520…``, and scored E4 at **2/15** from it
    while three of ``#8503``'s OWN measurement receipts — all five-on-fifteen and all
    on the frozen corpus, dated 14.66–15.06 h BEFORE that issue closed as a DISPROOF, not
    "after its fix", which never existed — sat unread in the same directory. Before this
    test's NEWER, other-corpus receipt is what the row would have carried had anyone
    edited the constant; a filename constant cannot tell the two apart.

    Constructed so the WRONG choice is the appealing one: the other-corpus receipt is
    BOTH the newest file and the sole best number, so "newest file" and "best number"
    each select it. Only the corpus hash selects correctly. (The fixture used to also
    carry a newest-but-unscorable candidate ON the pin, which only worked because the
    selector skipped it silently — that skip is now a REFUSAL and is pinned by
    ``test_an_on_pin_receipt_that_cannot_be_scored_REFUSES_instead_of_falling_back``.)
    """
    pin = "sha256:" + "d" * 64
    root = Path(tempfile.mkdtemp())
    receipts = root / measure.E4_RECEIPT_DIR
    receipts.mkdir(parents=True)
    _receipt(receipts / "w2b-operator-2026-01-01.json", corpus_hash=pin,
             edge_correct=5, date="2026-01-01T00:00:00Z")
    # NEWEST *and* sole best number — the appealing wrong answer.
    _receipt(receipts / "w2b-operator-2026-02-01.json",
             corpus_hash="sha256:" + "9" * 64, edge_correct=99,
             date="2026-02-01T00:00:00Z")

    rel, record = measure.select_e4_receipt(root, frozen_pin=pin)
    assert rel == f"{measure.E4_RECEIPT_DIR}/w2b-operator-2026-01-01.json", rel
    assert record["operator_audit"]["edge_correct"] == 5
    assert record["corpus_hash"] == pin


def test_an_on_pin_receipt_that_cannot_be_scored_REFUSES_instead_of_falling_back():
    """An on-pin receipt with no ``operator_audit`` refuses; it is never skipped.

    The measured hazard: a NEWER receipt on the frozen corpus arrives truncated or with
    a drifted schema, so it cannot be scored. Skipping it silently let an OLDER on-pin
    receipt publish the row's number — the row would then report a measurement of the
    right corpus taken at the wrong head, with nothing anywhere saying so. (In the green
    direction the same path could report a stale 15/15 while the newest measurement on
    the pin was unreadable.) The selection must REFUSE and NAME the offender.
    """
    pin = "sha256:" + "d" * 64
    root = Path(tempfile.mkdtemp())
    receipts = root / measure.E4_RECEIPT_DIR
    receipts.mkdir(parents=True)
    _receipt(receipts / "w2b-operator-2026-01-01.json", corpus_hash=pin,
             edge_correct=5, date="2026-01-01T00:00:00Z")
    # NEWER and ON the pin, but unscorable.
    _receipt(receipts / "w2b-operator-2026-03-01.json", corpus_hash=pin,
             edge_correct=15, date="2026-03-01T00:00:00Z", audit=False)

    with pytest.raises(SystemExit) as exc:
        measure.select_e4_receipt(root, frozen_pin=pin)
    message = str(exc.value)
    assert "REFUSED" in message, message
    assert "w2b-operator-2026-03-01.json" in message, (
        "the refusal must NAME the on-pin receipt it could not score, or a reader "
        "cannot tell which artifact to repair: " + message)
    assert "operator_audit" in message, message


def test_an_unscorable_on_pin_receipt_that_cannot_win_is_ignored_not_refused():
    """The refusal must name a TRUE consequence, or the refusal is itself the bug.

    An on-pin receipt older than the newest scorable one can never win — the ordering
    puts the winner last — so ignoring it changes nothing. Refusing on it would tell a
    reader that an older receipt would have published the row's number when the newest
    measurement on the pin was in fact read: a refusal naming a false reason, which is
    the one failure mode ``_same_corpus``'s own docstring says a refusal must not have.
    """
    pin = "sha256:" + "d" * 64
    root = Path(tempfile.mkdtemp())
    receipts = root / measure.E4_RECEIPT_DIR
    receipts.mkdir(parents=True)
    # OLDER and unscorable — it cannot win, so it must not refuse the selection.
    _receipt(receipts / "w2b-operator-2026-01-01.json", corpus_hash=pin,
             edge_correct=9, date="2026-01-01T00:00:00Z", audit=False)
    _receipt(receipts / "w2b-operator-2026-05-01.json", corpus_hash=pin,
             edge_correct=5, date="2026-05-01T00:00:00Z")

    rel, record = measure.select_e4_receipt(root, frozen_pin=pin)
    assert rel.endswith("w2b-operator-2026-05-01.json"), rel
    assert record["operator_audit"]["edge_correct"] == 5


def test_a_truncated_receipt_refuses_rather_than_letting_an_older_one_win():
    """A half-written file must REFUSE, not be skipped for an older on-pin receipt.

    The operator lane writes its receipt with a plain non-atomic ``write_text``
    (``tests/eval/write_path/runner.py``), so a lane killed mid-write leaves exactly
    this file. It cannot be parsed, so neither its corpus nor its date is knowable:
    publishing an older on-pin receipt's number would be the silent revert this
    selector exists to prevent, and nothing downstream would say so.
    """
    pin = "sha256:" + "d" * 64
    root = Path(tempfile.mkdtemp())
    receipts = root / measure.E4_RECEIPT_DIR
    receipts.mkdir(parents=True)
    _receipt(receipts / "w2b-operator-2026-01-01.json", corpus_hash=pin,
             edge_correct=5, date="2026-01-01T00:00:00Z")
    (receipts / "w2b-operator-2026-09-01.json").write_text('{"run_id": "trunc')

    with pytest.raises(SystemExit) as exc:
        measure.select_e4_receipt(root, frozen_pin=pin)
    message = str(exc.value)
    assert "REFUSED" in message, message
    assert "w2b-operator-2026-09-01.json" in message, (
        "the refusal must NAME the unreadable file, or a reader cannot tell which "
        "artifact to repair: " + message)
    assert "unreadable" in message, message


def test_a_non_utf8_receipt_refuses_instead_of_tracebacks():
    """A non-UTF-8 file raises ``UnicodeDecodeError`` — a ``ValueError``, NOT a
    ``JSONDecodeError`` — so a guard catching only the latter let the traceback escape
    instead of the documented refusal naming the file.
    """
    pin = "sha256:" + "d" * 64
    root = Path(tempfile.mkdtemp())
    receipts = root / measure.E4_RECEIPT_DIR
    receipts.mkdir(parents=True)
    _receipt(receipts / "w2b-operator-2026-01-01.json", corpus_hash=pin,
             edge_correct=5, date="2026-01-01T00:00:00Z")
    (receipts / "w2b-operator-latin1.json").write_bytes(
        '{"run_id": "caf\u00e9"}'.encode("latin-1"))

    with pytest.raises(SystemExit) as exc:
        measure.select_e4_receipt(root, frozen_pin=pin)
    message = str(exc.value)
    assert "REFUSED" in message, message
    assert "w2b-operator-latin1.json" in message, message


def test_a_directory_whose_only_on_pin_receipt_is_unscorable_names_that_cause():
    """When nothing matches because every on-pin receipt is unscorable, say THAT.

    The generic no-match refusal claimed no receipt "was measured on the frozen
    corpus" (one was), printed "none present" about a directory that holds a
    candidate, and blamed "scoring it from another revision" — three false statements
    in one message, with the actual cause (no ``operator_audit``) never named. A
    refusal that names a false reason is the failure mode this family exists to
    remove, so this arm is the reason the branch exists at all.
    """
    pin = "sha256:" + "d" * 64
    root = Path(tempfile.mkdtemp())
    receipts = root / measure.E4_RECEIPT_DIR
    receipts.mkdir(parents=True)
    _receipt(receipts / "w2b-operator-2026-01-01.json", corpus_hash=pin,
             edge_correct=5, date="2026-01-01T00:00:00Z", audit=False)

    with pytest.raises(SystemExit) as exc:
        measure.select_e4_receipt(root, frozen_pin=pin)
    message = str(exc.value)
    assert "operator_audit" in message, message
    assert "w2b-operator-2026-01-01.json" in message, message
    assert "none present" not in message, message
    assert "another revision" not in message, message


def test_build_e4_row_refuses_a_receipt_off_the_pin():
    """The note says "Its corpus_hash IS the frozen corpus", so it must BE that.

    ``build_e4_row`` rendered that sentence unconditionally, so any caller passing an
    off-pin receipt emitted a claim the row's own fields refute — and ``build_rows``
    then stamped ``artifact_corpus_sha256`` from it, so nothing downstream re-checked.
    Both operands are guarded: a mismatch refuses, and an EMPTY pin refuses too,
    because ``_same_corpus("", "")`` is ``True`` and the both-empty shape would
    otherwise slip through the very check that exists to make the sentence true.
    """
    bars = measure.parse_bars(_prereg())
    receipt = {"evidence": "x.json", "planted": 15, "edge_correct": 5,
               "content_ok": 11, "operators_total": 33, "date": "d",
               "commit": "abc", "corpus_hash": "sha256:" + "9" * 64,
               "verdict": "regression", "failure_origin": "gate_regression"}
    with pytest.raises(SystemExit) as exc:
        measure.build_e4_row(receipt, bars=bars, corpus_sha="sha256:" + "d" * 64)
    assert "REFUSED" in str(exc.value), str(exc.value)
    with pytest.raises(SystemExit) as exc:
        measure.build_e4_row({**receipt, "corpus_hash": ""}, bars=bars,
                             corpus_sha="")
    assert "REFUSED" in str(exc.value), str(exc.value)


def test_the_e4_note_follows_the_outcome_it_sits_beside_and_not_a_fixed_claim():
    """The note asserted "#8503: the planted edges are still not all formed" ALWAYS.

    The bar is 15 of 15, so the moment an on-pin receipt reads 15/15 the row is GREEN
    and its own note said the planted edges were not formed — a sentence the row's own
    fields refute, emitted at exactly the epic's success state, and uncovered because
    every fixture sat at 5/15. Both arms are pinned here, because the point is that
    the clause FOLLOWS the number it sits beside.
    """
    bars = measure.parse_bars(_prereg())
    pin = "sha256:" + "d" * 64

    def row(edge_correct: int, planted: int = 15) -> dict:
        receipt = {"evidence": "x.json", "planted": planted,
                   "edge_correct": edge_correct, "content_ok": 11,
                   "operators_total": 33, "date": "d", "commit": "abc",
                   "corpus_hash": pin, "verdict": "regression",
                   "failure_origin": "gate_regression"}
        return measure.build_e4_row(receipt, bars=bars, corpus_sha=pin)

    red = row(5)
    assert red["verdict"] == "RED", red
    assert "still not all formed" in red["note"], red["note"]

    green = row(15)
    assert green["verdict"] == "GREEN", green
    assert "still not all formed" not in green["note"], (
        "the row is GREEN — its note must not say the planted edges are unformed: "
        f"{green['note']}")
    assert "IS met" in green["note"], green["note"]

    # The retrievability half is a fact about the RECEIPT, not about the number, so
    # it stays on BOTH arms — removing it with the #8503 clause would lose a true
    # disclosure at the exact moment the row goes green.
    assert "#9596" in green["note"] and "#9596" in red["note"]

    # ── third arm: FAIL is not one cause ────────────────────────────────────
    # `e4_outcome` fails on `edge_correct < required` OR on `planted != of`, so a
    # receipt that plants its own count is RED with numerator == denominator — where
    # "the planted edges are still not all formed" is refuted by the row's own fields.
    mismatch = row(14, planted=14)
    assert mismatch["verdict"] == "RED", mismatch
    assert mismatch["numerator"] == mismatch["denominator"] == 14, mismatch
    assert "still not all formed" not in mismatch["note"], (
        "numerator == denominator — every planted edge IS formed, so the wiring "
        f"clause is refuted by the row's own fields: {mismatch['note']}")
    assert "COUNT" in mismatch["note"], mismatch["note"]
    assert "#9596" in mismatch["note"], mismatch["note"]


def test_the_newest_frozen_pin_receipt_wins_because_the_ordering_is_the_property():
    """`matched[-1]` is a claim the docs make; pin it or `matched[0]` stays green.

    Both candidates match the pin, so the corpus check cannot decide between them and
    only the ordering can. They differ in `edge_correct`, so picking the wrong end
    changes the ROW's number — which is the measured property, not the sort.
    """
    pin = "sha256:" + "d" * 64
    root = Path(tempfile.mkdtemp())
    receipts = root / measure.E4_RECEIPT_DIR
    receipts.mkdir(parents=True)
    _receipt(receipts / "w2b-operator-older.json", corpus_hash=pin,
             edge_correct=13, date="2026-10-24T05:46:51Z")
    _receipt(receipts / "w2b-operator-newer.json", corpus_hash=pin,
             edge_correct=5, date="2026-10-24T06:10:47Z")
    rel, record = measure.select_e4_receipt(root, frozen_pin=pin)
    assert rel.endswith("w2b-operator-newer.json"), rel
    assert record["operator_audit"]["edge_correct"] == 5, record


def test_the_ordering_compares_instants_and_not_date_strings():
    """An offset stamp sorts ABOVE its ``Z`` equivalent as a STRING while being EARLIER.

    Measured: ``2026-10-24T01:00:00+02:00`` is 2026-10-23T23:00Z, genuinely BEFORE
    ``2026-10-24T00:30:00Z``, but lexically AFTER it. The two fixtures below are chosen
    so a raw-string ordering and a parsed-instant ordering pick DIFFERENT files, and
    they differ in ``edge_correct`` — so the published E4 numerator moves if the
    comparison regresses. Reverting ``_sort_key`` to a string compare leaves every
    other case green; this one is what reds.

    The undated third receipt pins the documented sentinel: an absent ``date`` sorts
    FIRST, so an undated record never displaces a dated one.
    """
    pin = "sha256:" + "d" * 64
    root = Path(tempfile.mkdtemp())
    receipts = root / measure.E4_RECEIPT_DIR
    receipts.mkdir(parents=True)
    _receipt(receipts / "w2b-operator-offset.json", corpus_hash=pin,
             edge_correct=13, date="2026-10-24T01:00:00+02:00")  # 2026-10-23T23:00Z
    _receipt(receipts / "w2b-operator-zulu.json", corpus_hash=pin,
             edge_correct=5, date="2026-10-24T00:30:00Z")
    _receipt(receipts / "w2b-operator-undated.json", corpus_hash=pin,
             edge_correct=99, date="")

    rel, record = measure.select_e4_receipt(root, frozen_pin=pin)
    assert rel.endswith("w2b-operator-zulu.json"), (
        f"the LATER instant must win; a raw-string compare picks the +02:00 stamp: {rel}")
    assert record["operator_audit"]["edge_correct"] == 5, record


def test_the_would_have_won_boundary_is_the_instant_not_the_string():
    """The ignore/refuse boundary must be decided on the same key as the winner.

    An unscorable on-pin receipt that is genuinely EARLIER than the winner can never
    win, so it is ignored. Under a raw-string compare its ``+02:00`` stamp sorts ABOVE
    the winner's ``Z`` stamp and the selection REFUSES — a refusal that names a
    consequence that is not true, over a run that is perfectly fine.
    """
    pin = "sha256:" + "d" * 64
    root = Path(tempfile.mkdtemp())
    receipts = root / measure.E4_RECEIPT_DIR
    receipts.mkdir(parents=True)
    # Chronologically EARLIER (2026-10-23T23:00Z) but lexically later, and unscorable.
    _receipt(receipts / "w2b-operator-offset.json", corpus_hash=pin,
             edge_correct=13, date="2026-10-24T01:00:00+02:00", audit=False)
    _receipt(receipts / "w2b-operator-zulu.json", corpus_hash=pin,
             edge_correct=5, date="2026-10-24T00:30:00Z")

    rel, record = measure.select_e4_receipt(root, frozen_pin=pin)
    assert rel.endswith("w2b-operator-zulu.json"), rel
    assert record["operator_audit"]["edge_correct"] == 5, record


def test_an_unparseable_date_on_the_pin_refuses_instead_of_publishing_an_older():
    """A PRESENT-but-unreadable ``date`` is not an ABSENT one — it REFUSES.

    ``_instant`` tagged both ``(0, …)``, so a garbled stamp ranked with the undated
    records: the newest on-pin measurement could never win and an OLDER receipt
    published E4's number with nothing said — the exact "the row's number is not the
    artifact you think it is" defect the ordering pin exists to close. An absent date
    stays permitted (it is a recorded fact); an unreadable one is UNKNOWABLE, so the
    selection refuses for the reason it already refuses an unreadable file.

    Non-vacuity: at the previous head this returned ``older.json`` (5/15) — the 15/15
    receipt on the pin was ranked first and never read.
    """
    pin = "sha256:" + "d" * 64
    root = Path(tempfile.mkdtemp())
    receipts = root / measure.E4_RECEIPT_DIR
    receipts.mkdir(parents=True)
    _receipt(receipts / "w2b-operator-older.json", corpus_hash=pin,
             edge_correct=5, date="2026-10-24T05:00:00Z")
    _receipt(receipts / "w2b-operator-garbled.json", corpus_hash=pin,
             edge_correct=15, date="2026-10-24T0")

    with pytest.raises(SystemExit) as exc:
        measure.select_e4_receipt(root, frozen_pin=pin)
    message = str(exc.value)
    assert "w2b-operator-garbled.json" in message, message
    assert "not an instant" in message, message


def test_an_unparseable_date_cannot_silence_the_would_have_won_refusal():
    """A garbled date must not let an unscorable receipt hide behind "it sorts first".

    With the sentinel, an unscorable on-pin receipt with a PRESENT-but-unreadable date
    sorted before the winner, so ``key > winner`` was false, the would-have-won refusal
    never fired, and the newest unread measurement on the frozen corpus was skipped
    silently — the failure mode that refusal exists for.

    Non-vacuity: at the previous head this returned the scorable receipt, no refusal.
    """
    pin = "sha256:" + "d" * 64
    root = Path(tempfile.mkdtemp())
    receipts = root / measure.E4_RECEIPT_DIR
    receipts.mkdir(parents=True)
    _receipt(receipts / "w2b-operator-valid.json", corpus_hash=pin,
             edge_correct=5, date="2026-10-24T05:00:00Z")
    _receipt(receipts / "w2b-operator-garbled.json", corpus_hash=pin,
             edge_correct=15, date="24/10/2026", audit=False)

    with pytest.raises(SystemExit) as exc:
        measure.select_e4_receipt(root, frozen_pin=pin)
    assert "w2b-operator-garbled.json" in str(exc.value), str(exc.value)


def test_an_unparseable_date_off_the_pin_is_ignored():
    """A receipt on ANOTHER corpus is not a candidate, so its broken date is moot.

    The refusal protects the ORDER the winner is chosen from; a record already
    excluded by ``corpus_hash`` never enters that order, so refusing on it would name
    a consequence that is not true — the one failure mode a refusal must not have.
    """
    pin = "sha256:" + "d" * 64
    root = Path(tempfile.mkdtemp())
    receipts = root / measure.E4_RECEIPT_DIR
    receipts.mkdir(parents=True)
    _receipt(receipts / "w2b-operator-pin.json", corpus_hash=pin,
             edge_correct=5, date="2026-10-24T05:00:00Z")
    _receipt(receipts / "w2b-operator-other.json",
             corpus_hash="sha256:" + "9" * 64, edge_correct=15, date="nonsense")

    rel, record = measure.select_e4_receipt(root, frozen_pin=pin)
    assert rel.endswith("w2b-operator-pin.json"), rel
    assert record["operator_audit"]["edge_correct"] == 5, record


def test_a_missing_receipts_directory_refuses_differently_from_an_empty_one():
    """An ABSENT receipts directory is not an EMPTY one — nor one that is a FILE.

    Both produced the identical "none present" refusal, so a wrong root or a partial
    checkout pointed the reader at a missing receipt when the real cause was a missing
    tree — the mirror of the case where "none present" was printed about a directory
    that did hold an on-pin candidate. All three arms are pinned here, because the
    point is the DISTINCTION, not any one message on its own.

    The third arm (a path that EXISTS as a regular file) is the one `is_dir()` made
    invisible: it is False for a file, so the absent-tree refusal fired and asserted
    "does not exist" about a path that was plainly there — a refusal naming a false
    reason, which is the single failure mode a refusal must not have.
    """
    pin = "sha256:" + "d" * 64
    absent = Path(tempfile.mkdtemp())
    with pytest.raises(SystemExit) as exc:
        measure.select_e4_receipt(absent, frozen_pin=pin)
    missing = str(exc.value)
    assert "REFUSED" in missing, missing
    assert str(absent / measure.E4_RECEIPT_DIR) in missing, missing
    assert "does not exist" in missing, missing
    assert "candidate(s) were scanned" not in missing, (
        "an absent directory must not report a scan count — there was nothing to "
        f"scan: {missing}")

    empty = Path(tempfile.mkdtemp())
    (empty / measure.E4_RECEIPT_DIR).mkdir(parents=True)
    with pytest.raises(SystemExit) as exc:
        measure.select_e4_receipt(empty, frozen_pin=pin)
    scanned = str(exc.value)
    assert "candidate(s) were scanned" in scanned, scanned
    assert "none present" in scanned, scanned
    assert "does not exist" not in scanned, scanned

    # ── third arm: the path EXISTS, as a regular FILE ──────────────────────────
    # `is_dir()` is False for a file, so this used to fall into the absent-tree
    # refusal and claim the directory did not exist. The message must now say what
    # is actually true, and must NOT send the reader to check the root.
    notadir = Path(tempfile.mkdtemp())
    occupied = notadir / measure.E4_RECEIPT_DIR
    occupied.parent.mkdir(parents=True, exist_ok=True)  # E4_RECEIPT_DIR is multi-level
    occupied.write_text("not a receipts directory")
    with pytest.raises(SystemExit) as exc:
        measure.select_e4_receipt(notadir, frozen_pin=pin)
    as_file = str(exc.value)
    assert "REFUSED" in as_file, as_file
    assert str(occupied) in as_file, as_file
    assert "NOT a" in as_file, as_file
    assert "does not exist" not in as_file, (
        "the path exists as a file — asserting it does not exist is a refusal naming "
        f"a false reason: {as_file}")
    assert "candidate(s) were scanned" not in as_file, as_file


def test_e4_refuses_rather_than_falling_back_to_another_corpus():
    """No receipt on the pin = no measurement, named — never "the newest file".

    A fallback would report a number this run's corpus cannot support, which is the
    whole class of defect the row had. The refusal names the pin and every candidate
    it scanned, so an artifact that is missing or unreadable cannot read as "none
    present".
    """
    pin = "sha256:" + "d" * 64
    root = Path(tempfile.mkdtemp())
    receipts = root / measure.E4_RECEIPT_DIR
    receipts.mkdir(parents=True)
    _receipt(receipts / "w2b-operator-2026-02-01.json",
             corpus_hash="sha256:" + "9" * 64, edge_correct=99,
             date="2026-02-01T00:00:00Z")
    with pytest.raises(SystemExit) as exc:
        measure.select_e4_receipt(root, frozen_pin=pin)
    message = str(exc.value)
    assert pin in message, message
    assert "w2b-operator-2026-02-01.json" in message, message
    assert str(receipts) in message, (
        "the refusal must name the directory it actually scanned, not the module "
        f"constant: {message}")


def test_a_receipt_not_named_w2b_operator_is_still_found():
    """The name is not trusted to say which files are receipts.

    A frozen-pin receipt whose filename does not match the old glob was invisible, so
    the refusal printed "none present" about a directory that held a valid artifact.
    """
    pin = "sha256:" + "d" * 64
    root = Path(tempfile.mkdtemp())
    receipts = root / measure.E4_RECEIPT_DIR
    receipts.mkdir(parents=True)
    _receipt(receipts / "operator-2026-10-24.json", corpus_hash=pin,
             edge_correct=5, date="2026-10-24T06:10:47Z")
    rel, record = measure.select_e4_receipt(root, frozen_pin=pin)
    assert rel.endswith("operator-2026-10-24.json"), rel
    assert record["operator_audit"]["edge_correct"] == 5


def test_a_pin_spelled_without_the_sha256_prefix_still_matches():
    """Two spellings of the same hash name the same corpus (a false refusal is a bug)."""
    pin = "dd7e85200f4809f701b9e9c11816b541acea63afd0b2472998975e2ebcb035dc"
    root = Path(tempfile.mkdtemp())
    receipts = root / measure.E4_RECEIPT_DIR
    receipts.mkdir(parents=True)
    _receipt(receipts / "w2b-operator-x.json", corpus_hash=f"sha256:{pin}",
             edge_correct=5, date="2026-10-24T06:10:47Z")
    rel, _record = measure.select_e4_receipt(root, frozen_pin=pin)
    assert rel.endswith("w2b-operator-x.json"), rel


def test_a_receipt_that_is_json_but_not_an_object_refuses_instead_of_tracebacks():
    root = Path(tempfile.mkdtemp())
    receipts = root / measure.E4_RECEIPT_DIR
    receipts.mkdir(parents=True)
    (receipts / "w2b-operator-list.json").write_text("[]")
    with pytest.raises(SystemExit) as exc:
        measure.select_e4_receipt(root, frozen_pin="sha256:" + "d" * 64)
    assert "not a record" in str(exc.value), str(exc.value)


def test_a_non_string_corpus_hash_REFUSES_instead_of_being_called_absent():
    """A hash that is PRESENT but not a string is unknowable, not missing.

    Two defects in one branch, both of the class this selector exists to close:

    * the refusal LABEL was untrue — a record carrying ``"corpus_hash": 12345`` was
      reported as ``(no corpus_hash)`` when it plainly carries one, which is the
      "a refusal naming a false reason" failure the sibling is_dir()/absent-tree fix
      removed one branch up;
    * it was silently SKIPPED, so a NEWER on-pin measurement whose hash could not be
      read lost to an OLDER scorable one with nothing anywhere saying so — exactly
      the silent skip ``test_an_on_pin_receipt_that_cannot_be_scored_REFUSES_...``
      refuses, and the opposite of how the sibling ``date`` field is treated.

    The fixture makes the shape load-bearing: the garbled-hash record is the NEWER of
    the two and would publish 15/15, so a regression that re-skips it returns the
    older 5/15 and this test moves.
    """
    pin = "sha256:" + "d" * 64
    root = Path(tempfile.mkdtemp())
    receipts = root / measure.E4_RECEIPT_DIR
    receipts.mkdir(parents=True)
    (receipts / "w2b-operator-older.json").write_text(json.dumps({
        "date": "2026-01-01T00:00:00Z", "run_id": "older", "corpus_hash": pin,
        "operator_audit": {"planted": 15, "edge_correct": 5},
    }))
    (receipts / "w2b-operator-newer-garbled-hash.json").write_text(json.dumps({
        "date": "2026-06-01T00:00:00Z", "run_id": "newer", "corpus_hash": 12345,
        "operator_audit": {"planted": 15, "edge_correct": 15},
    }))
    with pytest.raises(SystemExit) as exc:
        measure.select_e4_receipt(root, frozen_pin=pin)
    message = str(exc.value)
    assert "REFUSED" in message, message
    assert "w2b-operator-newer-garbled-hash.json" in message, message
    assert "not a hash" in message, message
    assert "no corpus_hash" not in message, (
        "the file carries a corpus_hash — calling it absent is a refusal naming a "
        f"false reason: {message}")


def test_e4_refuses_to_select_without_the_frozen_pin():
    """An un-pinned selection cannot check the artifact, so it is refused."""
    with pytest.raises(SystemExit) as exc:
        measure.select_e4_receipt(_REPO, frozen_pin="")
    assert "frozen corpus pin" in str(exc.value)


def test_the_e4_row_reads_a_receipt_on_the_frozen_pin_and_not_the_old_constant():
    """The row's artifact is on the FROZEN pin, and is not the pre-#8503 constant.

    The pin is read out of the record (never typed) and compared with the receipt's
    own ``corpus_hash`` — that equality is the property. The NUMBER is not pinned
    here: the bar is 15/15, so the day a 15/15 receipt lands this row goes GREEN and a
    hard-coded ``== 5`` would red on the epic's own success (and the cheapest green
    would be to edit the assertion). The 2 → 5 movement is pinned by its own test.
    """
    pin = _prereg()["corpus"]["fixtures_hash"]
    e4 = measure.load_e4(_REPO, frozen_pin=pin)
    assert measure._same_corpus(e4["corpus_hash"], pin), e4
    assert e4["evidence"] != measure.E4_RECEIPT, (
        "E4 is reading the pre-#8503 receipt on a DIFFERENT corpus revision again — "
        "the row's number is not a measurement of the frozen corpus")
    assert e4["evidence"].startswith("tests/eval/write_path/receipts/")
    # the row built from it cites THAT file, and its number is that file's number
    row = measure.build_e4_row(e4, bars=measure.parse_bars(_prereg()),
                               corpus_sha=pin)
    assert row["evidence"] == e4["evidence"], row
    assert (row["numerator"], row["denominator"]) == (
        _receipt_audit(e4["evidence"])["edge_correct"],
        _receipt_audit(e4["evidence"])["planted"]), row


def _receipt_audit(rel: str) -> dict:
    return json.loads((_REPO / rel).read_text())["operator_audit"]


def test_the_e4_number_is_the_frozen_receipts_five_and_not_the_old_ones():
    """The defect, pinned as a NUMBER: the old artifact says 2, the selected one 5.

    ⛔ THIS IS THE ONE DELIBERATE HARD PIN in this family, and it is labelled as one:
    the test above deliberately does not pin E4's number (so a 15/15 receipt cannot
    red it on the epic's own success), and THAT is why this one exists — the number
    the row used to publish has to be recorded somewhere or the correction has no
    evidence behind it.

    It records that the row used to report **2/15** from `w2b-operator-2026-10-19.json`
    (corpus `943a99ec…`, pre-`#8503`) while the frozen-corpus receipts read **5/15**.
    When either number moves — a 15/15 receipt landing, or the connect step improving —
    this test reds ON PURPOSE: the epic's record then has to be re-read, and the fix is
    to re-derive BOTH numbers here with the receipts in front of you, never to delete
    the comparison. It reads the two artifacts rather than asserting one in isolation,
    so it cannot be greened by editing a single literal.
    """
    old = _receipt_audit(measure.E4_RECEIPT)
    assert old["edge_correct"] == 2 and old["planted"] == 15, old
    pin = _prereg()["corpus"]["fixtures_hash"]
    selected = measure.load_e4(_REPO, frozen_pin=pin)
    assert selected["edge_correct"] == 5 and selected["planted"] == 15, selected
    assert selected["edge_correct"] > old["edge_correct"]


def test_main_builds_the_e4_row_from_the_selector_and_never_from_the_constant():
    """The union of selection and row is pinned, so a reversion cannot stay green.

    The reviewer's finding: `main` built the row inline, so replacing
    ``"evidence": receipt["evidence"]`` with the constant left all tests passing while
    the published row cited a file it was not measured on. This asserts the source
    ``main`` runs calls ``build_e4_row(load_e4(frozen_pin=...))`` — the two names, on
    the path that actually produces the table.
    """
    tree = ast.parse(PROBE.read_text())
    main = next(node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == "main")

    # What main assigns from a call, by target name — resolves `receipt` to load_e4.
    assigned_from: dict[str, str] = {}
    for node in ast.walk(main):
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assigned_from[target.id] = node.value.func.id

    loads = [node for node in ast.walk(main)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
             and node.func.id == "load_e4"]
    assert loads, "main never calls load_e4 — E4 would come from somewhere unpinned"
    for call in loads:
        assert any(kw.arg == "frozen_pin" for kw in call.keywords), (
            "an un-pinned load_e4() call would score E4 from whatever receipt is "
            "newest, not from one on the frozen corpus")

    row_calls = [node for node in ast.walk(main)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                 and node.func.id == "build_e4_row"]
    assert len(row_calls) == 1, (
        f"main must build the E4 row exactly once: {len(row_calls)}")
    source_arg = row_calls[0].args[0] if row_calls[0].args else None
    assert isinstance(source_arg, ast.Name), (
        "main must build E4's row from a plain name, so this pin can follow it")
    assert assigned_from.get(source_arg.id) in {"load_e4", "select_e4_receipt"}, (
        f"main builds E4's row from {source_arg.id}, which is not what "
        "load_e4()/select_e4_receipt() returned — building it from anything else is "
        "the reversion this pin exists to catch (the earlier version of this test "
        "grepped source TEXT, which is why the render-path defects stayed green)")


def test_main_binds_every_name_it_hands_render_log_and_load_e4_carries_its_keys():
    """The render path is EXECUTED by nothing else, so pin its two data contracts.

    Two defects shipped in the first cut of this change with 79 tests green, because
    no test ever ran ``render_log`` or ``main``:

    * ``main`` called ``render_log(..., e4=receipt, e4_out=e4_result, ...)`` after the
      refactor deleted the statement that bound ``e4_result`` — so every real run
      raised ``NameError`` at the END, after the corpus pass and before the log was
      written: all the work, no artifact;
    * ``render_log`` read ``e4["corpus_sha256"]`` from the ``load_e4`` mapping, whose
      field is ``corpus_hash`` — a ``KeyError`` on the same path (twice: the human
      section and the machine record's ``e4_receipt.corpus_hash``).

    The wiring pin beside this one greps source TEXT, which is exactly why both stayed
    green. These are the two properties that were actually violated: every name
    ``main`` hands ``render_log`` is bound in ``main`` BEFORE the call, and every key
    ``render_log`` subscripts off its ``e4`` parameter is a key ``load_e4`` really
    returns (checked against a real call, not a hand-kept list).
    """
    tree = ast.parse(PROBE.read_text())
    functions = {node.name: node for node in tree.body
                 if isinstance(node, ast.FunctionDef)}
    main, render = functions["main"], functions["render_log"]

    # (a) every NAME main hands render_log is bound, and bound BEFORE the call.
    calls = [node for node in ast.walk(main)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
             and node.func.id == "render_log"]
    assert len(calls) == 1, f"main must call render_log exactly once: {len(calls)}"
    call_line = calls[0].lineno
    handed = {kw.value.id for kw in calls[0].keywords
              if isinstance(kw.value, ast.Name)}
    assert handed, "no keyword argument of the render_log call is a bare name"
    # (a) every NAME main hands render_log resolves, and a LOCAL one is bound BEFORE
    # the call. Module-level names resolve without a local binding, so seed them; and
    # main's OWN scope only — a Store inside a nested def/lambda/class does not bind a
    # name in main, so descending into one would let a dead binding satisfy the check.
    module_level: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            module_level.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            module_level.update((alias.asname or alias.name).split(".")[0]
                                for alias in node.names)
        elif isinstance(node, ast.Assign):
            module_level.update(target.id for target in node.targets
                                if isinstance(target, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            module_level.add(node.target.id)
    first_bind: dict[str, int] = dict.fromkeys(module_level, 0)

    def _own_scope(node: ast.AST):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.Lambda, ast.ClassDef)):
                continue
            yield child
            yield from _own_scope(child)

    for node in [main, *_own_scope(main)]:
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            first_bind[node.id] = min(first_bind.get(node.id, node.lineno),
                                      node.lineno)
        elif isinstance(node, ast.arg):
            first_bind[node.arg] = min(first_bind.get(node.arg, node.lineno),
                                       node.lineno)
    unbound = sorted(name for name in handed
                     if first_bind.get(name, call_line + 1) >= call_line)
    assert not unbound, (
        f"main hands render_log name(s) it never binds before the call: {unbound} — "
        "that is a NameError at the END of a full corpus run, after the work and "
        "before the log is written, and no test that does not execute main sees it")

    # (b) every STRING key render_log reads off `e4` is a key load_e4 returns.
    pin = _prereg()["corpus"]["fixtures_hash"]
    supplied = set(measure.load_e4(_REPO, frozen_pin=pin))
    read = {node.slice.value for node in ast.walk(render)
            if isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == "e4"
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)}
    assert read, "render_log no longer subscripts `e4` — this pin is stale"
    missing = sorted(read - supplied)
    assert not missing, (
        f"render_log reads key(s) {missing} off `e4`, but main hands it the load_e4() "
        f"mapping whose keys are {sorted(supplied)} — a KeyError on the render path, "
        "which nothing executes")

    # (c) `e4=` must be the load_e4() MAPPING, not the built row. Arm (b) proves the
    # keys render_log reads exist in load_e4's return; it says nothing about WHAT main
    # hands over, so swapping `e4=receipt` for `e4=e4` (the row `main` also binds) left
    # it green while render_log raised `KeyError` on 11 of its 13 reads. The row is the
    # table shape (`corpus_sha256`, `citations`); the mapping is the receipt's fields.
    e4_kwarg = next(kw for kw in calls[0].keywords if kw.arg == "e4")
    assert isinstance(e4_kwarg.value, ast.Name), (
        "main must pass a plain name as e4=, so this pin can follow it")
    from_loader = {target.id for node in [main, *_own_scope(main)]
                   if isinstance(node, ast.Assign)
                   and isinstance(node.value, ast.Call)
                   and isinstance(node.value.func, ast.Name)
                   and node.value.func.id in {"load_e4", "select_e4_receipt"}
                   for target in node.targets if isinstance(target, ast.Name)}
    assert e4_kwarg.value.id in from_loader, (
        f"main hands render_log e4={e4_kwarg.value.id}, which is not the value of a "
        "load_e4()/select_e4_receipt() call — render_log reads the RECEIPT's fields "
        "off it, so handing it the built row is a KeyError at the END of a full "
        "corpus run, after the work and before the log is written")


#: One canned receipt in `load_e4`'s own shape — the ONLY field the E4 narration
#: branches on is `corpus_hash`, so the rest exist to be formatted.
_E4_RECEIPT_FIXTURE = {
    "evidence": "tests/eval/write_path/receipts/w2b-operator-2026-01-01.json",
    "planted": 15, "edge_correct": 5, "content_ok": 11, "operators_total": 33,
    "operators_provenanced": 33, "run_id": "w2b-canned",
    "date": "2026-01-01T00:00:00Z", "commit": "a" * 9,
    "corpus_hash": "sha256:" + "d" * 64,
    "judge_pin": "w2-write-path-mechanical-v2", "verdict": "regression",
    "failure_origin": "gate_regression",
}


def _render_a_log(monkeypatch, *, receipt: dict, corpus_sha: str) -> str:
    """One REAL `render_log` call over canned data — no DB, no network, no model.

    `render_log` takes dicts and scalars only, so the whole renderer executes here;
    everything it formats is either derived from the frozen record (`parse_bars`) or
    the canned census the sibling cases already use. The `e1` argument is the REAL
    `#9889` record (`measure_e1_e2`, a committed JSON read — no DB, no model), the
    same shape the sibling renderer pin uses, so this fixture cannot drift from the
    keys `render_log` subscripts off it.
    """
    bars = measure.parse_bars(_prereg())
    return measure.render_log(
        head="a" * 40, prereg_sha="b" * 40,
        bars=bars, corpus_sha=corpus_sha,
        graph="canned",
        runs=[{"session_id": "wp01", "ok": True, "extraction_mode": "mechanical",
               "extracted": 1, "errors": 0, "elapsed_s": 0.5, "turns": 1,
               "stats": {}}],
        observed={}, unobserved={},
        census_data=_canned_census(monkeypatch),
        e1=measure.measure_e1_e2(_REPO),
        e1_aux={"bare_definite": 0, "share": 0.0},
        e1_out=measure.e1_outcome(**_e1_kwargs()),
        e1b_out={"outcome": "CONTROL", "value": "90", "bar": "x"},
        e2a_out={"outcome": "BLOCKED", "value": "42", "bar": "x"},
        e2b_out={"outcome": "BLOCKED", "value": "9", "bar": "x"},
        e3={"numerator": 148, "denominator": 383, "below_floor": False,
            "outcome": "RED", "bar": "= 1.000"},
        e3_out={"outcome": "RED", "value": "148/383", "bar": "= 1.000"},
        e4=receipt,
        e4_out=measure.e4_outcome_for(receipt, measure.parse_bars(_prereg())),
        e5={"arms_dir": "d", "artifact": "a.json", "raw_loss": 0.52,
            "raw_loss_count": 13, "raw_n": 25, "narrative_loss": 0.48,
            "narrative_loss_count": 12, "narrative_n": 25,
            "recall": {"raw": {"recalled": 12, "planted": 25},
                       "narrative": {"recalled": 13, "planted": 25}},
            "warning": [], "partial_receipts": []},
        e5_out={"ratio": 1.0833, "ratio_ok": True, "raw_recovery": 0.48,
                "raw_ok": False, "narrative_recovery": 0.52,
                "outcome": "RED", "bar": "x"},
        rows=[], out_path="canned.log")


def test_the_e4_narration_says_which_corpus_the_receipt_was_scored_on(monkeypatch):
    """`render_log`'s `on_pin` branch — the sentence EVERY future scored log prints.

    The scanned receipt is now selected BY the frozen pin, so the renderer's sentence
    must say so. It printed the opposite unconditionally: *"Its corpus_hash … is NOT
    the frozen corpus … scored as a NON-GREEN measurement on ITS OWN corpus revision"* —
    true of the pre-fix constant, false of every receipt `load_e4` can now return, and
    printed into the log of the very run that fixed it.

    Nothing pinned either sentence (`git grep render_log tests/ tools/` found only the
    AST wiring pin, which reads the call site and never executes the renderer), so a
    reversion to the unconditional sentence left every case green.

    Non-vacuity: the pre-fix renderer prints the off-pin sentence for BOTH receipts, so
    the first two assertions red ("IS the frozen corpus" absent, "is NOT the frozen"
    present). The two receipts differ ONLY in `corpus_hash`, so nothing else can decide
    which sentence is emitted.
    """
    pin = _prereg()["corpus"]["fixtures_hash"]
    on_pin = dict(_E4_RECEIPT_FIXTURE, corpus_hash=pin)
    off_pin = dict(_E4_RECEIPT_FIXTURE, corpus_hash="sha256:" + "9" * 64)

    rendered_on = _render_a_log(monkeypatch, receipt=on_pin, corpus_sha=pin)
    assert f"Its corpus_hash={pin} IS the frozen corpus ({pin})" in rendered_on, \
        "an on-pin receipt must be narrated as on the frozen corpus"
    assert "selected BY that hash" in rendered_on, rendered_on[-2000:]
    assert "V7's declared-mismatch arm is not taken for it" in rendered_on
    assert "is NOT the frozen corpus" not in rendered_on, (
        "a receipt selected BY the frozen pin must never be narrated as off it — this "
        "is the sentence the fix exists for")

    rendered_off = _render_a_log(monkeypatch, receipt=off_pin, corpus_sha=pin)
    assert f"Its corpus_hash={off_pin['corpus_hash']} is NOT the frozen corpus ({pin})" \
        in rendered_off, "an off-pin receipt must still be narrated as off the pin"
    assert "NON-GREEN measurement on ITS OWN corpus revision" in rendered_off
    assert "IS the frozen corpus" not in rendered_off, (
        "an off-pin receipt must never be handed the on-pin sentence")


def test_the_frozen_records_stale_E4_sentence_is_disclosed_and_not_edited():
    """The record a MACHINE consumer reads still says 2/15 — and must NOT be edited.

    `PRE-REGISTRATION.md` §10.3 ends *"E4's denominator (15), its threshold (= 15 of 15)
    and its row are unchanged"* and `preregistration.json`'s
    `criteria[E4].post_registration_measurement` still hands a consumer
    `edge_correct=2 of 15 … so it measures its own corpus revision`. "its row are
    unchanged" is no longer true of the row the probe builds, so the bundle would
    publish a current number and a frozen record that contradicts it — the defect
    class this change exists to close — in the one file no human reads.

    The fix is NOT to edit the record: `preregistration.json` pins the document by
    BYTES (`document_sha256`) and the `#10463` ruling this bundle records says an
    amendment is a NEW linked record, never an edit to the frozen one. So this pin
    holds BOTH halves: the record still carries the 2/15 sentence (an edit reds here
    as well as in `test_capstone_preregistration_9075.py`), and the README DISCLOSES
    it — naming §10.3, the machine record, the `document_sha256`/`#10463` reason it is
    untouched, and the selector that makes the current row 5/15.

    Non-vacuity: the pre-fix README carries no disclosure at all, so the README
    assertions red.

    ⛔ AND THE `#11298` DISCLOSURE IS A SEPARATE ARM, because it was SILENTLY LOST once
    (#11331). The `origin/main` carry resolved the conflicted record as "ours' blob
    plus main's new TOP-LEVEL sections" — a method that, by construction, cannot
    carry a change main made INSIDE an existing key. `#11298` appended a suffix to
    `criteria[E4].post_registration_measurement` recording that **5 of 15** is the
    row's figure at this head, read from the FROZEN-corpus receipt the probe SELECTS,
    and the merge dropped it: the cell came out byte-identical to the pre-merge base
    while `corpus_rows.E4.reconciles_with` and `PRE-REGISTRATION.md` §1.6.3 both went
    on asserting the 5-of-15 figure. Nothing redded — arm (a) below only asserts the
    2/15 sentence is PRESENT, which is true either way. Measured, then fixed, so this
    arm pins the half that was lost: the frozen receipt the selector picks, the figure
    it reads, and the cross-reference. It reds on the pre-fix cell (verified: that
    cell carries neither `5 OF 15` nor `w2b-operator-2026-10-24-8503-b`).
    """
    doc = (PREREG.parent / "PRE-REGISTRATION.md").read_text()
    record = json.loads(PREREG.read_text())
    readme = README_DOC.read_text()

    # (a) the frozen record is UNEDITED — asserted, so this cannot drift silently.
    assert "and its row are unchanged" in doc, (
        "PRE-REGISTRATION.md §10.3 no longer states the frozen sentence this bundle "
        "discloses; re-cut the disclosure, do NOT edit the hash-pinned record")
    e4 = next(c for c in record["criteria"] if c["id"] == "E4")
    assert "edge_correct=2 of 15" in e4["post_registration_measurement"], e4

    # (a2) …AND it carries the current-head disclosure `#11298` appended (#11331).
    # A cell that satisfies (a) alone is the state a union merge produced by dropping
    # this suffix, which is why the two halves are asserted apart.
    cell = e4["post_registration_measurement"]
    assert "5 OF 15" in cell, (
        "criteria[E4].post_registration_measurement no longer names 5 of 15 as the "
        "row's figure at this head — `corpus_rows.E4.reconciles_with` and "
        "PRE-REGISTRATION.md §1.6.3 both assert that figure, so the record and the "
        "document now describe different numbers. Restore the `#11298` disclosure "
        "(do not delete the 2/15 sentence it sits beside) (#11331)")
    assert "w2b-operator-2026-10-24-8503-b.json" in cell, (
        "the E4 disclosure must name the FROZEN-corpus receipt the probe SELECTS by "
        "corpus_hash (`select_e4_receipt()`), not only the non-frozen one whose 2/15 "
        "it is disclosing — a figure with no receipt behind it is what the "
        "reconciliation exists to prevent (#11331)")
    assert "corpus_rows.E4.reconciles_with" in cell, (
        "the E4 disclosure must point at the reconciliation record that explains why "
        "2/15 and 5/15 are both true of different corpus revisions (#11331)")

    # (b) the bundle discloses it, in the section a reader of that E4 row lands in.
    section = readme.split("## E4 — the committed operator receipt", 1)[1]
    section = section.split("## E5 —", 1)[0]
    assert "The frozen record still publishes the pre-fix figure for E4" in section, (
        "README §E4 must disclose that the frozen record still carries 2/15")
    assert "its row are unchanged" in section, (
        "the disclosure must quote the sentence that went stale, so a reader who "
        "meets it in the record knows which one this refers to")
    assert "criteria[E4].post_registration_measurement" in section, (
        "the disclosure must name the machine-readable copy, not only the prose")
    assert "document_sha256" in section and "#10463" in section, (
        "the disclosure must state WHY the record is not edited (byte-pinned; the "
        "#10463 new-record rule), not merely that it is not")
    assert "5/15 via `select_e4_receipt`" in section, (
        "the disclosure must hand the reader the CURRENT row and the selector that "
        "produced it, or it only moves the contradiction one paragraph down")


def test_the_v7_paragraph_states_the_merge_once_and_not_a_leftover_half_sentence():
    """The V7 paragraph must state the merged result ONCE, in one grammatical sentence.

    `origin/main` (`#9889`) re-pointed E1/E2 onto the frozen corpus while this branch
    made E4 on-pin, and the two rewrote the SAME V7 paragraph. The merge kept a
    fragment of the sentence both sides had replaced, so `3b1399ef6` shipped:

        "... This table cannot satisfy that honestly:
         measured row's `corpus_sha256` to equal the log's corpus. Two rows could not
         satisfy that honestly: ..."

    — the pre-fix clause stated twice in four lines, the second time as a fragment with
    nothing opening it. This is the merge-damage class the bundle's own changes record
    claims was repaired, and it is invisible to a conflict-marker check: no `<<<<<<<`
    survived, only a duplicated sentence. Every review round on this PR ran on a head
    before the merge, so no reviewer read this paragraph.

    Non-vacuity: at `3b1399ef6` the clause appears twice and this reds; it also reds if
    either half of the merge is dropped, because the paragraph is required to say BOTH
    rows moved onto the frozen corpus and which run still takes the declaration.

    ⛔ The second half of the merge repair published a claim the artifact refutes, and
    `2b38365c9` PINNED it verbatim (`PR #10679 review, F1`): "no row in THIS table takes
    the declaration any more", where "this table" can only be the shipped log's table —
    and that table takes it on THREE rows (measured off the scored log's own machine
    record below — E1, E2, E4 — rather than off a line number in a log that a re-run
    replaces), which the README itself says further up. The
    reading its own antecedent forces made the sentence false, and a later reader could
    not correct it to match the log without reding this test. So the wording is no
    longer pinned: the fact is, from the log's own machine record, and the refuted
    sentence is forbidden by name.
    """
    readme = README_DOC.read_text()
    paragraph = readme[readme.index("*Is the table honest?*"):]
    paragraph = paragraph[:paragraph.index("## Why the steps are observed")]
    flat = " ".join(paragraph.split())

    # The sentence BOTH sides replaced. One copy survives — the one that opens the
    # argument; a second is the merge's leftover.
    assert flat.count("to equal the log's corpus") == 1, (
        "the V7 paragraph repeats the pre-merge sentence: the auto-merge kept a "
        "fragment of the text main and this branch each rewrote, so the paragraph "
        f"argues the same point twice\n{flat[:400]}")

    # …and what it states is BOTH sides' intent, not one of them: each pre-merge copy
    # described only its own row (main: E4; here: E1/E2), which is why the merged text
    # has to name both.
    assert "E1/E2" in flat and "E4" in flat, flat[:200]
    assert "Both now measure the FROZEN corpus" in flat, (
        "the paragraph must say BOTH rows moved onto the frozen pin — the pre-merge "
        "copies each described only their own side of the conflict")
    assert "the table a run builds TODAY takes the declaration on no row" in flat, (
        "the arm's scope is the point of the paragraph: a reader must be told that the "
        "rows a run builds NOW declare no `artifact_corpus_sha256`")

    # ⛔ …and the sentence must not over-reach to the SHIPPED table, which still takes
    # it. Measured off the log's own machine record rather than typed: three rows do.
    #
    # ⛔ The log is DISCOVERED (`_newest_log(_scored_logs())`), never named. `#10601`
    # re-measured the table and DELETED `capstone-measured-e0fc940b.log` wholesale, so
    # this arm raised `FileNotFoundError` against a filename — the bundle's own rule is
    # that a superseded run is replaced by re-running the probe, which mints a NEW
    # `capstone-measured-<head>.log`. A hard-coded name makes this pin fail on data
    # drift rather than on the claim it guards.
    log = _newest_log(_scored_logs()).read_text(encoding="utf-8")
    record = json.loads(log.split("=== CAPSTONE-VERDICT-BEGIN ===", 1)[1]
                        .split("=== CAPSTONE-VERDICT-END ===", 1)[0])
    declared = sorted(row["criterion"] for row in record["rows"]
                      if "artifact_corpus_sha256" in row)

    # ⛔ THE PARAGRAPH'S SCOPE CLAIM IS HELD TO THE ARTIFACT, BOTH WAYS — never to a
    # literal membership list (delta review, F3). Asserting
    # `declared == ["E1", "E2", "E4"]` pinned the SHAPE of one artifact rather than the
    # CLAIM the paragraph makes about it, so it redded on the bundle's own documented
    # supersede path: re-run the probe at a post-selector head and every row stops
    # declaring (`build_e4_row` refuses an off-pin receipt, so E4's corpus IS the run's;
    # E1/E2 are passed no declaration either), giving `declared == []` — a red on DATA
    # DRIFT in a pin whose job is the CLAIM. What the paragraph says is:
    #   * never, ever, that the SHIPPED table declares nothing (F1's refuted sentence);
    #   * "the SHIPPED log still carries it" exactly when the log on disk does.
    # Both arms are asserted, so neither the forbidden over-reach nor a paragraph left
    # describing a log that a re-run replaced can stay green.
    assert "no row in THIS table takes the declaration" not in flat, (
        "the paragraph claims the SHIPPED table declares nothing — state which run "
        "the sentence is about (PR #10679 review, F1: the claim this pin used to "
        "require verbatim)")
    if declared:
        assert "SHIPPED log" in flat, (
            f"the scored log on disk declares `artifact_corpus_sha256` on {declared}, "
            "so the paragraph must say outright that the SHIPPED log still carries the "
            "declaration — otherwise the reader of the artifact cannot tell why the "
            "arm is kept")
    else:
        assert "SHIPPED log" not in flat, (
            "the scored log on disk declares `artifact_corpus_sha256` on NO row (a "
            "re-run at a post-selector head), so the paragraph's 'the SHIPPED log "
            "still carries it' is false about the artifact in the tree — update the "
            "paragraph WITH the log, which is the documented supersede path, rather "
            "than deleting this pin")

    # The merge left the SAME clause twice; the fragment half is the one a count of the
    # sentence above cannot see (delta review, P3): re-applying only the lead-in keeps
    # the sentence unique and restores the duplication.
    assert flat.count("satisfy that honestly") == 1, (
        "the V7 paragraph argues its point twice — the clause opening the argument was "
        f"duplicated by the merge\n{flat[:400]}")


def test_a_row_cites_the_receipt_the_selector_chose():
    """The evidence path on the ROW follows the selection, not the constant."""
    rows = measure.build_rows(
        head="a" * 40, out_path="docs/evidence/7949-capstone/e1-e2.log",
        log_corpus_sha="sha256:" + "d" * 64,
        e1={"verdict": "BLOCKED", "sha": "c" * 40, "corpus_sha256": "e" * 64,
            "numerator": 28, "denominator": 78, "note": "E1 note"},
        e2={"verdict": "BLOCKED", "sha": "c" * 40, "corpus_sha256": "e" * 64,
            "numerator": 8, "denominator": 14, "note": "E2 note"},
        e3={"verdict": "RED", "numerator": 23, "denominator": 70, "note": "E3 note"},
        e4={"verdict": "RED", "numerator": 5, "denominator": 15, "sha": "a" * 9,
            "corpus_sha256": "sha256:" + "d" * 64, "note": "E4 note",
            "evidence": "tests/eval/write_path/receipts/w2b-operator-selected.json"},
        e5={"verdict": "RED", "numerator": 12, "denominator": 25, "note": "E5 note"})
    by_criterion = {row["criterion"]: row for row in rows}
    assert (by_criterion["E4"]["evidence"]
            == "tests/eval/write_path/receipts/w2b-operator-selected.json")


def test_a_row_with_no_evidence_path_is_refused_not_defaulted_to_the_constant():
    """The reviewer's P1: a missing key must not silently cite the old artifact.

    ``build_rows`` used ``e4.get("evidence", E4_RECEIPT)``, so any caller that omitted
    the key produced a row whose NUMBERS came from the pinned receipt and whose
    CITATION pointed at the pre-#8503 one — the exact defect this change closes, left
    open in the function that builds the table, and invisible to the gate (V4 passes,
    V7 passes on the matching corpus, V10 checks only the denominator).

    The refusal is a NAMED one, not a ``KeyError``: this module's header already said
    ``build_rows`` REFUSES such a mapping, and every sibling guard refuses by name
    because a traceback carries no cause and lands at the end of a full corpus run.
    """
    with pytest.raises(SystemExit) as exc:
        measure.build_rows(
            head="a" * 40, out_path="docs/evidence/7949-capstone/e1-e2.log",
            log_corpus_sha="sha256:" + "d" * 64,
            e1={"verdict": "BLOCKED", "sha": "c" * 40, "corpus_sha256": "e" * 64,
                "note": "E1 note"},
            e2={"verdict": "BLOCKED", "sha": "c" * 40, "corpus_sha256": "e" * 64,
                "note": "E2 note"},
            e3={"verdict": "RED", "numerator": 23, "denominator": 70,
                "note": "E3 note"},
            e4={"verdict": "RED", "numerator": 5, "denominator": 15,
                "sha": "a" * 9, "corpus_sha256": "sha256:" + "d" * 64,
                "note": "E4 note"},
            e5={"verdict": "RED", "numerator": 12, "denominator": 25,
                "note": "E5 note"})
    assert "REFUSED" in str(exc.value), str(exc.value)
    assert "evidence" in str(exc.value), str(exc.value)


def test_a_mapping_missing_corpus_sha256_is_refused_by_name_too():
    """The sibling key takes the SAME named refusal — and before it is subscripted.

    The guard above was written for `evidence` alone and PLACED AFTER
    `e4_off_this_corpus = not _same_corpus(e4["corpus_sha256"], …)`, so a caller
    omitting `corpus_sha256` still got a bare ``KeyError`` — from the line three above
    the guard whose own comment promised the mapping was "refused by name rather than
    left to a KeyError" (PR #10679 review, F4: the guarantee was partial in exactly the
    direction the comment denied). Both keys are now checked in one place, ahead of
    every subscript.

    Non-vacuity: at the pre-fix head this mapping raises ``KeyError`` out of
    ``build_rows`` and the ``pytest.raises(SystemExit)`` below never fires.
    """
    with pytest.raises(SystemExit) as exc:
        measure.build_rows(
            head="a" * 40, out_path="docs/evidence/7949-capstone/e1-e2.log",
            log_corpus_sha="sha256:" + "d" * 64,
            e1={"verdict": "BLOCKED", "sha": "c" * 40, "corpus_sha256": "e" * 64,
                "note": "E1 note"},
            e2={"verdict": "BLOCKED", "sha": "c" * 40, "corpus_sha256": "e" * 64,
                "note": "E2 note"},
            e3={"verdict": "RED", "numerator": 23, "denominator": 70,
                "note": "E3 note"},
            e4={"verdict": "RED", "numerator": 5, "denominator": 15,
                "sha": "a" * 9, "evidence": _E4_ON_PIN, "note": "E4 note"},
            e5={"verdict": "RED", "numerator": 12, "denominator": 25,
                "note": "E5 note"})
    message = str(exc.value)
    assert "REFUSED" in message, message
    # The NAMED part is the leading clause; the explanatory tail legitimately mentions
    # `evidence` (the key whose defaulting is refused), so the assertion is on the
    # clause that names what is MISSING, not on the word's absence from the message.
    assert "with no `corpus_sha256`" in message, (
        f"the refusal must name the key the caller did not supply: {message}")
    assert "with no `evidence`" not in message, (
        f"the refusal named a key the caller DID supply: {message}")


def test_a_non_string_date_is_UNREADABLE_and_refuses_instead_of_parsing():
    """`repr(raw)` must never be PARSED — the repr of an int IS the ISO basic form.

    `repr(20261024)` is `'20261024'`, which `datetime.fromisoformat` ACCEPTS, so a
    non-string date used to rank at midnight UTC (measured) while `0` — whose repr
    fails to parse — was refused: two non-string dates, opposite outcomes, for no
    stated reason. A recorded date that is not a string is UNREADABLE, not absent, so
    it takes the same refusal a garbled string does. The absent shapes are pinned
    alongside, because the whole point is that they must stay distinguishable.
    """
    assert measure._instant(20261024)[0] == 0, measure._instant(20261024)
    assert measure._instant(None) == (0, ""), "an ABSENT date keeps the absent shape"
    assert measure._instant("") == (0, ""), "an empty date is absent, not unreadable"

    pin = "sha256:" + "d" * 64
    root = Path(tempfile.mkdtemp())
    receipts = root / measure.E4_RECEIPT_DIR
    receipts.mkdir(parents=True)
    (receipts / "w2b-operator-int-date.json").write_text(json.dumps({
        "date": 20261024, "run_id": "int", "corpus_hash": pin,
        "operator_audit": {"planted": 15, "edge_correct": 15},
    }))
    with pytest.raises(SystemExit) as exc:
        measure.select_e4_receipt(root, frozen_pin=pin)
    message = str(exc.value)
    assert "REFUSED" in message, message
    assert "not an instant" in message, message
    assert "w2b-operator-int-date.json" in message, message


def test_a_green_e4_row_carries_no_blocker():
    """GREEN and blocked are mutually exclusive — the gate refuses both (V5).

    This arm holds `main`'s invariant on E4's row: `row()` resolves the pair ONCE
    (`if verdict == "GREEN": blocked_by = None`) and `build_e4_row`'s `#9596` clause
    follows the outcome, so the very success state the row exists to reach cannot emit
    a table `tools/capstone_verdict_check` calls dishonest ("GREEN carries a blocker").

    ⛔ The RED cell is `#9596`, not `#8503`. `#8503` CLOSED 2026-10-24 and its closure
    records E4's DISPROOF, so it is this row's measurement record, never a live
    blocker — and `#10601`'s V12 now REFUSES a row whose `blocked_by` is recorded
    CLOSED in `blocker-states.json`. Pinning `#8503` here would hold the emitter to a
    cell the gate rejects, i.e. a pin that requires the defect.

    ⛔ This docstring used to claim the arm pins a DEFECT this PR fixed — that
    `build_rows` stamped `blocked_by="#8503"` on E4 unconditionally. That was true of
    this branch's pre-merge head, where the row was built inline; the merge onto
    `origin/main` moved the resolution into `row()`, so both halves of this arm are
    satisfied by code that predates the branch and the stated defect is unreachable
    here (`origin/main`'s `row()` keeps the invariant — PR #10679 review, F2: an arm
    that cannot fail for the reason it gives is a vacuity/claim defect, and the
    manifest note attributed it to this change). The arm is kept because the invariant
    is worth guarding ON THIS ROW (E4 is the row whose blocker now follows a measured
    outcome); it just does not claim to be the fix.
    """
    rows = _rows(e4={"verdict": "GREEN", "numerator": 15, "denominator": 15,
                     "sha": "a999be41e", "corpus_sha256": "sha256:" + "d" * 64,
                     "evidence": _E4_ON_PIN, "note": "E4 note"})
    green = {row["criterion"]: row for row in rows}["E4"]
    assert green["verdict"] == "GREEN", green
    assert not green["blocked_by"], (
        f"a GREEN row may not carry a blocker: {green['blocked_by']!r}")

    red = {row["criterion"]: row for row in _rows()}["E4"]
    assert red["blocked_by"] == "#9596", (
        "the RED row names the row's LIVE blocker (#9596), not #8503 — which closed "
        f"2026-10-24 and whose closure records the disproof: {red['blocked_by']!r}")


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


@pytest.mark.parametrize("mutation", [
    {"bare_definite": 100, "share": 100 / 78},   # numerator above its population
    {"bare_definite": -1, "share": -1 / 78},     # negative numerator
    {"objects": "78"},                            # not a number at all
    {"share": "0.36"},                            # the operand as a string
])
def test_a_before_arm_share_must_be_a_fraction_of_a_real_population(mutation,
                                                                    tmp_path,
                                                                    capsys):
    """The numerator is bounded too, and a non-number is a REFUSAL, not a crash.

    #10610 review at `21b2af22` (P2, a FAIL-OPEN). `_consistent_share` checked only that
    `objects > 0`, so `bare_definite` ABOVE its own population — which no producer can write
    (`len(bare) / len(names)`) and which is therefore an edit — reconciled happily against
    `share = 100/78` and wrote a DECIDED `PASS`: measured, a bound of `0.641` against an
    after share of `0.2131` PASSES both pairings, flipping E1 from REFUSED to a decided PASS
    off the numerator alone.

    The same function ALSO ran its `objects <= 0` test BEFORE the float conversion, so a
    non-numeric field escaped as `TypeError: '<=' not supported between instances of 'str'
    and 'int'` (exit 1) instead of the refusal the neighbouring `except` advertises — a run
    an operator cannot tell from a crash is not a refusal, the class this bundle fixed in
    `e1_paired_arm.py`. Coercing instead of refusing would leave the raw string travelling on
    to the comparisons below, which is how the traceback got out in the first place.

    ⚠️ `bare_definite == objects` with `share: 1.0` is NOT mutated here: a population in
    which EVERY Object name is bare-definite is a legitimate reading (the share is a
    fraction of a population, and 1 is that fraction's maximum), so refusing it would red a
    true measurement. The bound is `0 <= bare <= objects`, inclusive by design.

    Non-vacuity: at the pre-fix head the two range arms score a decided PASS and the two
    non-number arms escape as a traceback.
    """
    repo = _bundle(tmp_path)
    path = repo / "docs/evidence/9889/e1-paired-off-arm-1.json"
    record = json.loads(path.read_text())
    assert record["bare_definite"] == 28 and record["objects"] == 78, record
    record.update(mutation)
    path.write_text(json.dumps(record))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    err = capsys.readouterr().err
    assert ("not a subset of the population" in err or "which is not a number" in err
            or "not a fraction of any population" in err), err


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


def _shipped_arm(record: dict) -> dict:
    """Give a mutated before record the arm the SHIPPED probe writes.

    ⛔ SINCE `#10996` AN ABSENT `TORTOISE_VALUE_GATE` IS THE ON SPELLING (`#10996`
    review cycle 2, P1), and the driver refuses it on its own — before the guard a
    test below is about can speak. `e1_paired_arm.off_arm_readback` therefore
    PINs the key to `"0"` and reads it back, so that is what a record produced by
    the shipped probe carries. Setting it here keeps each fixture to exactly ONE
    defect, which is what these pins are written to discriminate.
    """
    record["arm"]["TORTOISE_VALUE_GATE"] = "0"
    return record


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


def test_a_falsifier_block_that_is_not_an_object_is_refused(tmp_path, capsys):
    """#10610 review cycle 3 (P2): a block of the WRONG TYPE was a traceback, not a refusal.

    The only test applied to `with_anchor` / `as_written` was truthiness
    (`if "with_anchor" in falsifier and not (falsifier.get("with_anchor") or {})`),
    and a non-empty STRING is truthy, so `"none"` was handed straight to
    `.get(...)` — `AttributeError`, exit **1** with a traceback, which an operator
    cannot tell from a crash and which is not the exit-**2** refusal this module's
    docstring promises. Measured on a copy of the shipped bundle BEFORE the guard:
    both arms exit 1 at `with_anchor.get("injected_anchor")` / `.get("value_units")`.

    Non-vacuity: the shipped bundle scores (asserted at the end), so the arm
    discriminates a wrong-typed block from the well-formed one rather than
    refusing the form.
    """
    for field in ("with_anchor", "as_written"):
        repo = _bundle(tmp_path / field)
        path = repo / "docs/evidence/9889/e2a-value-units-falsifier.json"
        record = json.loads(path.read_text())
        assert isinstance(record[field], dict), field      # the shipped shape
        record[field] = "none"                             # …not a block any more
        path.write_text(json.dumps(record))
        with pytest.raises(SystemExit) as refused:
            scores_probe.score(repo)
        assert refused.value.code == 2, (field, refused.value)
        err = capsys.readouterr().err
        assert f"carries a {field} that is not a JSON object" in err, err
        assert "str: 'none'" in err, err
    assert scores_probe.score(_bundle(tmp_path / "intact"))["e2"]["outcome"] in (
        "PASS", "FAIL", "REFUSED")


def test_a_record_whose_top_level_is_not_an_object_is_refused(tmp_path, capsys):
    """#10610 review cycle 3 (P2): a list-valued record was a traceback, not a refusal.

    `_read_json` returned `json.loads(...)` unchecked, so a file holding `[]` (or a
    number, or a string) reached `.get(...)` as an `AttributeError` — exit **1**.
    Guarding the READ covers every record this driver opens, rather than one arm
    per field.

    Non-vacuity: at the pre-fix head this bundle exits 1 with
    `AttributeError: 'list' object has no attribute 'get'` (measured).
    """
    repo = _bundle(tmp_path)
    path = repo / "docs/evidence/9889/e2a-value-units.json"
    path.write_text("[]")
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    err = capsys.readouterr().err
    assert "at the top level, not a JSON object" in err, err
    assert "list" in err, err


def test_a_fixtures_hash_that_is_not_a_string_is_refused(tmp_path, capsys):
    """#10610 review cycle 3 (P2): the frozen corpus PIN was never type-checked.

    `if not corpus.get("fixtures_hash")` accepts `12345`, `{"a": 1}`, `[1, 2]` and
    `true`, and each then reached `normalise_pin`'s `value.startswith` — an
    `AttributeError`, exit **1**. Every other member of that pre-registration
    family (`denominator_floors.e1_objects` / `e1_min_before_share` /
    `e2b_surviving_units`, `corpus.value_bearing_units`) refuses BY TYPE, so the pin
    every E1 corpus comparison rests on was the one nobody typed.

    Non-vacuity: at the pre-fix head each value below exits 1 with
    `AttributeError: '<type>' object has no attribute 'startswith'` (measured).
    """
    path = Path("docs/evidence/7949-capstone/preregistration.json")
    for value in (12345, {"a": 1}, [1, 2], True):
        repo = _bundle(tmp_path / str(abs(hash(json.dumps(value, sort_keys=True)))))
        prereg_path = repo / path
        prereg = json.loads(prereg_path.read_text())
        assert isinstance(prereg["corpus"]["fixtures_hash"], str)
        prereg["corpus"]["fixtures_hash"] = value
        prereg_path.write_text(json.dumps(prereg))
        with pytest.raises(SystemExit) as refused:
            scores_probe.score(repo)
        assert refused.value.code == 2, (value, refused.value)
        err = capsys.readouterr().err
        assert "corpus.fixtures_hash is a" in err, err
        assert "not a string" in err, err


def test_a_prereg_sub_object_that_is_not_an_object_is_refused(tmp_path, capsys):
    """#10610 review cycle 4 (P2): `(value or {})` one level UP was the same traceback.

    `frozen_denominators` defaulted `denominator_floors` and `corpus` with
    `prereg.get(...) or {}`, so a truthy non-dict reached `"e1_objects" not in
    floors` as `TypeError: argument of type 'int' is not iterable`, and
    `corpus.get(...)` as `AttributeError` — exit **1**, before any refusal. Cycle 3
    fixed this shape for the falsifier's blocks; the parent record's two
    sub-objects were the same read one level up.

    Non-vacuity: at the pre-fix head both arms exit 1 (measured). `None` and an
    ABSENT key are still the "missing" case, reported by the named-key refusals —
    asserted here so the shape guard cannot quietly swallow that wording.
    """
    path = Path("docs/evidence/7949-capstone/preregistration.json")
    for field, value, name in (("denominator_floors", 7, "int"),
                               ("denominator_floors", ["e1_objects"], "list"),
                               ("corpus", "sha256:dead", "str"),
                               ("corpus", [1, 2], "list")):
        repo = _bundle(tmp_path / f"{field}-{name}")
        prereg_path = repo / path
        prereg = json.loads(prereg_path.read_text())
        assert isinstance(prereg[field], dict), field     # the shipped shape
        prereg[field] = value
        prereg_path.write_text(json.dumps(prereg))
        with pytest.raises(SystemExit) as refused:
            scores_probe.score(repo)
        assert refused.value.code == 2, (field, value, refused.value)
        err = capsys.readouterr().err
        assert f"carries a {field} that is not a JSON object" in err, err
        assert f"{name}:" in err, err

    # `null` is still "missing": the refusal names the KEY, not the shape, which is
    # the message a reader with a stripped pre-registration needs.
    repo = _bundle(tmp_path / "null-floors")
    prereg_path = repo / path
    prereg = json.loads(prereg_path.read_text())
    prereg["denominator_floors"] = None
    prereg_path.write_text(json.dumps(prereg))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    assert "carries no denominator_floors.e1_objects" in capsys.readouterr().err


def test_a_record_that_is_not_utf8_is_refused_not_tracebacked(tmp_path, capsys):
    """#10610 review cycle 4 (P2): corrupt BYTES were a traceback, next to the JSON arm.

    `_read_json` called `read_text()` inside its `try` but caught only
    `json.JSONDecodeError`, and `read_on_arm` called it with no handler at all — so a
    truncated or UTF-16 record raised `UnicodeDecodeError` and exited **1**, while the
    `is not JSON` branch beside it exists to turn corrupt input into the exit-2
    refusal. Same contract, one line earlier in the same function.

    Non-vacuity: at the pre-fix head both arms exit 1 with `UnicodeDecodeError`.
    """
    repo = _bundle(tmp_path / "json")
    (repo / "docs/evidence/9889/e2a-value-units.json").write_bytes(
        b'\xff\xfe{"value_units": 14}\n')
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    err = capsys.readouterr().err
    assert "is not UTF-8 text" in err, err

    # …and the LOG path (`read_on_arm`), which had no handler at all.
    repo = _bundle(tmp_path / "log")
    (repo / "docs/evidence/9889/on-arm.log").write_bytes(
        b'\xff\xfe# head: deadbeef\ne1-aux objects_in_graph=1 bare_definite=1 '
        b'share=1.0\n')
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    assert "is not UTF-8 text" in capsys.readouterr().err


def test_a_count_at_float_overflowing_magnitude_is_refused(tmp_path, capsys):
    """#10610 review cycle 5 (P2): an unbounded JSON integer was a traceback, not a refusal.

    `json.loads` returns an arbitrary-precision `int`, and both `math.isfinite(10**400)`
    (`_count`) and `float(10**400)` (`_consistent_share`) raise
    `OverflowError: int too large to convert to float`. Neither site caught it, so the
    guard whose whole job is to refuse rather than crash exited **1** with a traceback
    — the integer sibling of the `Infinity` case `_count`'s own docstring says it
    closed. Measured at the pre-fix head: both arms exit 1.

    Non-vacuity: the shipped bundle still scores at the end, so the arms discriminate a
    hostile magnitude from the counts the producers write.
    """
    huge = 10 ** 400
    for record, field, where in (
            ("e2a-value-units.json", "value_units", "_count"),
            ("e2a-value-units.json", "value_survived", "_count"),
            ("e1-paired-off-arm-1.json", "objects", "_consistent_share"),
            ("e1-paired-off-arm-1.json", "bare_definite", "_consistent_share")):
        repo = _bundle(tmp_path / f"{record.split('.')[0]}-{field}")
        path = repo / "docs/evidence/9889" / record
        data = json.loads(path.read_text())
        data[field] = huge
        path.write_text(json.dumps(data))
        with pytest.raises(SystemExit) as refused:
            scores_probe.score(repo)
        assert refused.value.code == 2, (record, field, where, refused.value)
        err = capsys.readouterr().err
        assert "magnitude no float can hold" in err or "is not a share" in err, err
        # The exception TEXT, not its class name: `_refuse` prints the message, and
        # what matters is that the OverflowError was converted rather than escaping.
        assert "too large to convert to float" in err, err

    assert scores_probe.score(_bundle(tmp_path / "intact"))["e2"]["outcome"] in (
        "PASS", "FAIL", "REFUSED")


def test_the_after_arm_is_witnessed_by_the_gate_drops_record(tmp_path, capsys):
    """#10610 review at `f12471075` (P0): a self-consistent ON-log edit scored GREEN.

    Two independent censuses of the SAME ON run are in the tree — `on-arm.log`'s
    `e1-aux` line and `e1-gate-drops.json` (whose graph identity the driver already
    requires) — plus the log's own `e3 label=Object` line, and nothing compared them.
    Measured: editing ONE line of the log to `objects_in_graph=78 bare_definite=10
    share=0.1282…` (self-consistent, so `_consistent_share` passes) exited 0 with
    `e1.outcome = PASS`, `decidable: True`, and the consumer's row came back GREEN,
    while the gate-drops record still said 61 Objects / 13 bare / 0.2131.

    Three arms, so no single witness can carry the check: the gate-drops counts, the
    gate-drops `object_names` census, and the log's own `e3` line.
    """
    # The log-side edit moves BOTH of the log's own lines, because the log's internal
    # check is the earlier guard: a self-consistent log edit that only `e1-gate-drops.json`
    # can refute is the hole this arm closes.
    counts_repo = _bundle(tmp_path / "counts")
    log = counts_repo / "docs/evidence/9889/on-arm.log"
    text = log.read_text()
    for old, new in (
            ("e1-aux objects_in_graph=61 bare_definite=13 share=0.21311475409836064",
             "e1-aux objects_in_graph=78 bare_definite=10 share=0.1282051282051282"),
            ("e3 label=Object nodes=61", "e3 label=Object nodes=78")):
        assert old in text, old
        text = text.replace(old, new, 1)
    log.write_text(text)
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(counts_repo)
    assert refused.value.code == 2, refused.value
    err = capsys.readouterr().err
    assert "two censuses of one ON run cannot disagree" in err, err
    assert "61" in err and "78" in err, err

    # …and the log's own `e3` line alone, which nothing else in the record witnesses.
    e3_repo = _bundle(tmp_path / "e3")
    log = e3_repo / "docs/evidence/9889/on-arm.log"
    log.write_text(log.read_text().replace("e3 label=Object nodes=61",
                                           "e3 label=Object nodes=78", 1))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(e3_repo)
    assert refused.value.code == 2, refused.value
    assert "one run, one Object census" in capsys.readouterr().err


def test_the_on_arms_witness_census_is_required_not_optional(tmp_path, capsys):
    """#10610 review at `67a3b8e98` (P2, fail-open): the census list was optional.

    `e1-gate-drops.json`'s `object_names` is the ONLY witness for the ON arm's counts
    that is independent of the two integers it reconciles: `on-arm.log`'s `e1-aux` line
    and the record's own `objects`/`bare_definite` are plain numbers, so a
    self-consistent multi-field edit of them is refuted by nothing else. The guard was
    written `if isinstance(gate_names, list) and gate_names:`, which skipped BOTH of its
    arms — and fell through to `e1_pairings` — whenever the list was absent, empty, or
    not a list.

    Measured on the PRE-FIX probe (this test's own non-vacuity): the multi-field edit the
    guard exists to refuse, PLUS `"object_names": []`, returned
    `E1 PASS decidable=True` with exit 0 — a CHEAPER edit than the one the guard was
    added for, because there is no 61-name list to forge — and the row builder turned
    that record into a GREEN `#9080` E1 row. Post-fix each of the three shapes exits 2
    and writes no record.

    The shipped record must still score, so this is a refusal of EDITED shapes only and
    not of the producer's own output: `probes/e1_gate_drops.py` writes `object_names`
    unconditionally, so an absent or empty list is always an edit, never an older shape.
    """
    # The control first: the real bundle still scores, so the required-field guard
    # refuses forged shapes without refusing the producer's own record.
    shipped = scores_probe.score(_REPO)
    assert shipped["e1"]["outcome"] == "REFUSED", shipped["e1"]
    gates_shipped = json.loads(
        (_REPO / "docs/evidence/9889/e1-gate-drops.json").read_text())
    assert len(gates_shipped["object_names"]) == 61, gates_shipped.keys()

    # …and every shape that would silently disable the witness is refused.
    absent = object()
    for label, value in (("absent", absent), ("empty", []), ("non-list", "61")):
        repo = _bundle(tmp_path / f"gate-names-{label}")
        path = repo / "docs/evidence/9889/e1-gate-drops.json"
        record = json.loads(path.read_text())
        if value is absent:
            del record["object_names"]
        else:
            record["object_names"] = value
        path.write_text(json.dumps(record))
        with pytest.raises(SystemExit) as refused:
            scores_probe.score(repo)
        assert refused.value.code == 2, (label, refused.value)
        err = capsys.readouterr().err
        assert "carries no `object_names` census" in err, (label, err)


def test_the_after_arm_readback_is_read_with_the_repos_truthiness(tmp_path, capsys):
    """#10610 review at `f12471075` (P1): the ON test was a DENY-list of spellings.

    The pipeline decides with `is_truthy` (an allow-list: unset/blank/falsy/garbage ->
    False). The arm guard compared against a hand-typed OFF-spelling list, so
    `TORTOISE_VET=off` in the ON readback was ACCEPTED as the ON half of E1's pair —
    `is_truthy("off") is False`, i.e. the treatment was off and the "pair" was two
    control arms, greened by changing one line of the log. `no` behaves the same way.

    The other direction is asserted too: a BEFORE arm spelling its switches `off` is
    genuinely OFF and must score, not be refused by the same list.
    """
    for spelling in ("off", "no", "0", "false", "disabled"):
        repo = _bundle(tmp_path / f"on-{spelling}")
        log = repo / "docs/evidence/9889/on-arm.log"
        text = log.read_text()
        arm_line = next(ln for ln in text.splitlines() if ln.startswith("# arm:"))
        edited = arm_line.replace("TORTOISE_VET=1",
                                  f"TORTOISE_VET={spelling}", 1)
        assert edited != arm_line, arm_line
        log.write_text(text.replace(arm_line, edited, 1))
        with pytest.raises(SystemExit) as refused:
            scores_probe.score(repo)
        assert refused.value.code == 2, (spelling, refused.value)
        assert "were not ON" in capsys.readouterr().err, spelling

    # …and a genuine OFF spelling on a BEFORE arm still scores, which the deny-list
    # refused (`off` was not in it, so the arm read as "not a before arm").
    repo = _bundle(tmp_path / "before-off")
    path = repo / "docs/evidence/9889/e1-paired-off-arm-1.json"
    record = json.loads(path.read_text())
    for var in record["arm"]:
        record["arm"][var] = "off"
    path.write_text(json.dumps(record))
    assert scores_probe.score(repo)["e1"]["outcome"] == "REFUSED"


def test_the_arm_vocabulary_is_the_repos_truthiness_contract():
    """The driver's mirrored vocabulary must BE `tortoise.env_truthy`'s.

    `tortoise/env_truthy.py` is the tree's ONE truthiness contract and it is stdlib-only
    so a module may mirror it (`embedded_reaper.py`, `hermes-plugin/__init__.py` already
    do, and `tests/test_env_truthy.py` pins every mirror). The scoring driver runs before
    any DB is up, so it mirrors too — and this is the pin that stops the copy drifting
    into a different rule than the pipeline's.
    """
    from tortoise.env_truthy import FALSY, TRUTHY, is_truthy
    assert set(scores_probe._TRUTHY) == set(TRUTHY), (
        scores_probe._TRUTHY, sorted(TRUTHY))
    assert set(scores_probe._FALSY) == set(FALSY), (
        scores_probe._FALSY, sorted(FALSY))
    for sample in [*sorted(TRUTHY), *sorted(FALSY), "off", "OFF", " off ", "", "  ",
                   None, "disabled", "true", "TRUE", "1", "0", 1, 0]:
        assert scores_probe._is_truthy(sample) == is_truthy(sample), (
            f"the driver reads {sample!r} as truthy={scores_probe._is_truthy(sample)} "
            f"but the repo's contract says {is_truthy(sample)}")


def test_the_e2b_line_is_reconciled_with_the_share_it_prints(tmp_path, capsys):
    """#10610 review at `f12471075` (P1): E2b's counts were unbounded, its share ignored.

    `fused`/`atomic_points` were read as integers and the `= 0.087719` printed on the
    same line was never compared, so editing ONLY the denominator (`fused=10/114` ->
    `10/100000`) wrote `share: 0.0001` and turned E2 from FAIL into PASS while the line
    still printed its own contradiction. A numerator above its denominator is refused
    too, and a non-numeric token is a refusal rather than a traceback.
    """
    cases = (
        ("denominator", "fused=1/114 = 0.008772", "fused=1/100000 = 0.008772",
         "cannot disagree"),
        ("numerator", "fused=1/114 = 0.008772", "fused=200/114 = 0.008772",
         "not a share"),
        ("token", "fused=1/114 = 0.008772", "fused=1/114 = abc",
         "is not a number"),
        ("share-nan", "fused=1/114 = 0.008772", "fused=1/114 = nan",
         "not finite"),
    )
    for marker, old, new, expected in cases:
        repo = _bundle(tmp_path / marker)
        log = repo / "docs/evidence/9889/on-arm.log"
        text = log.read_text()
        assert old in text, marker
        log.write_text(text.replace(old, new, 1))
        with pytest.raises(SystemExit) as refused:
            scores_probe.score(repo)
        assert refused.value.code == 2, (marker, refused.value)
        assert expected in capsys.readouterr().err, marker


def test_the_e2a_record_may_not_be_the_injected_reading(tmp_path, capsys):
    """#10610 review at `f12471075` (P1): only the falsifier's injection was read.

    `e2a_value_units.py --inject-anchor` writes 14/14 WITH `injected_anchor: true` into
    the E2a record's own shape, so a record copied from that run states — in its own
    field — that it graded the injected pass, and 14/14 then meets E2a's bar. Nothing
    read it: the round-5 fix closed the GUARD on `with_anchor` alone. `is not False`
    refuses `true`, `null` and an absent key alike, and the falsifier's `as_written`
    block is held to the same rule because it claims to BE the E2a reading.
    """
    for declared, marker in ((True, "true"), (None, "null"), ("absent", "absent")):
        repo = _bundle(tmp_path / marker)
        e2a_path = repo / "docs/evidence/9889/e2a-value-units.json"
        record = json.loads(e2a_path.read_text())
        assert record["injected_anchor"] is False
        if declared == "absent":
            record.pop("injected_anchor")
        else:
            record["injected_anchor"] = declared
        e2a_path.write_text(json.dumps(record))
        with pytest.raises(SystemExit) as refused:
            scores_probe.score(repo)
        assert refused.value.code == 2, (marker, refused.value)
        assert "UNINJECTED reading" in capsys.readouterr().err, marker

    # …and the as-written block: an INJECTED block claiming to be the E2a reading.
    repo = _bundle(tmp_path / "as-written")
    path = repo / "docs/evidence/9889/e2a-value-units-falsifier.json"
    falsifier = json.loads(path.read_text())
    assert falsifier["as_written"]["injected_anchor"] is False
    falsifier["as_written"]["injected_anchor"] = True
    path.write_text(json.dumps(falsifier))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    assert "as-written block does not declare" in capsys.readouterr().err


def test_the_census_is_a_partition_not_four_lengths(tmp_path, capsys):
    """#10610 review at `f12471075` (P2): `value_lost` and duplicates were unread.

    `e2a_value_units.py` writes `value_lost` beside the two lists, so a row listing the
    same unit as both surviving and lost contradicts itself — measured: `kept = ids`
    with `value_lost` untouched wrote `E2 PASS` while six units appeared in BOTH. `len()`
    also counted duplicates, which inflates the totals the bar is scored on.
    """
    for marker, mutate, expected in (
            ("both",
             lambda row: (row.__setitem__("value_survived", list(row["value_ids"])),
                          row.__setitem__("value_lost", list(row["value_ids"]))),
             "as BOTH survived and lost"),
            ("dup", lambda row: (row.__setitem__(
                "value_survived", [row["value_ids"][0], row["value_ids"][0]]),
                row.__setitem__("value_lost", [])),
             "twice"),
            ("stranger",
             lambda row: (row.__setitem__("value_survived", ["not-a-listed-unit"]),
                          row.__setitem__("value_lost", [])),
             "not among its own value_ids"),
    ):
        repo = _bundle(tmp_path / marker)
        path = repo / "docs/evidence/9889/e2a-value-units.json"
        record = json.loads(path.read_text())
        row = record["per_session"]["wp04_aurora_perf"]
        assert row["value_ids"] and not row["value_survived"], row
        mutate(row)
        total = sum(len(r["value_survived"]) for r in record["per_session"].values())
        record["value_survived"] = total
        path.write_text(json.dumps(record))
        with pytest.raises(SystemExit) as refused:
            scores_probe.score(repo)
        assert refused.value.code == 2, (marker, refused.value)
        assert expected in capsys.readouterr().err, (marker,)


def test_a_census_member_that_is_not_a_unit_id_is_refused(tmp_path, capsys):
    """#10610 review at `ef4c448d7` (P2): the set operations trusted their members.

    `len(set(ids))` / `set(kept) <= set(ids)` ran over whatever JSON held, so a census
    member that was a list or an object escaped as `TypeError: unhashable type` — exit
    **1** with a traceback, which an operator cannot tell from a crash, and not the exit
    **2** refusal this module's docstring promises. `_count`, `_float_token` and
    `_consistent_share` all convert-and-refuse for exactly this reason; the guard the
    whole E2a partition rests on was the one that skipped it.

    Non-vacuity: at the pre-fix head each of these mutations raises `TypeError` out of
    `set(...)`, so the `pytest.raises(SystemExit)` arm reds instead of passing.
    """
    for marker, field, offending, mutate in (
            ("ids", "value_ids", ["a"],
             lambda row: row.__setitem__("value_ids", [["a"], row["value_ids"][0]])),
            ("survived", "value_survived", {"k": 1},
             lambda row: row.__setitem__("value_survived", [{"k": 1}])),
            ("lost", "value_lost", ["a"],
             lambda row: row.__setitem__("value_lost", [["a"]])),
    ):
        repo = _bundle(tmp_path / marker)
        path = repo / "docs/evidence/9889/e2a-value-units.json"
        record = json.loads(path.read_text())
        row = record["per_session"]["wp04_aurora_perf"]
        assert row["value_ids"] and not row["value_survived"], row
        mutate(row)
        path.write_text(json.dumps(record))
        with pytest.raises(SystemExit) as refused:
            scores_probe.score(repo)
        assert refused.value.code == 2, (marker, refused.value)
        err = capsys.readouterr().err
        assert "REFUSED" in err and "is not a unit id" in err, (marker, err)
        assert f"{field}={offending!r}" in err, (marker, err)


def test_the_e1b_numerator_is_bounded_by_its_own_printed_bar(tmp_path, capsys):
    """#10610 review at `f12471075` (P3): E1b had no ceiling, and its bar was unread.

    `real_facts=100000` was accepted beside `bar=90 of 90` — a numerator 1,111x its
    denominator, the shape `_count(ceiling=…)` exists for. The bar token is on the same
    line, so it is read: the numerator is inside its population, and the bar the log
    prints must be the pre-registration's.
    """
    for marker, old, new, expected in (
            ("ceiling", "e1b outcome=PASS real_facts=90 bar=90 of 90",
             "e1b outcome=PASS real_facts=100000 bar=90 of 90", "e1b real_facts"),
            ("bar", "e1b outcome=PASS real_facts=90 bar=90 of 90",
             "e1b outcome=PASS real_facts=90 bar=90 of 100", "froze 90 of 90"),
    ):
        repo = _bundle(tmp_path / marker)
        log = repo / "docs/evidence/9889/on-arm.log"
        text = log.read_text()
        assert old in text, marker
        log.write_text(text.replace(old, new, 1))
        with pytest.raises(SystemExit) as refused:
            scores_probe.score(repo)
        assert refused.value.code == 2, (marker, refused.value)
        assert expected in capsys.readouterr().err, (marker,)


def test_a_re_stamped_copy_of_one_before_arm_is_still_one_realization(tmp_path, capsys):
    """#10610 review at `75bfd9c6` (P1): the graph NAME is not the record's identity.

    The duplicate guard compared the graph string, which left it one character wide.
    Copying arm 2 over arm 1 and bumping its epoch by one second
    (`…_1793379347` -> `…_1793379348`, the record otherwise byte-identical) made two
    names for ONE realization: measured, E1 went from REFUSED to a DECIDED `PASS` off a
    single reading — the pseudoreplication the guard exists to stop, and the cheapest way
    to fake #9889's own next requirement (n >= 2 per arm). `e1_paired_arm.py` writes
    `object_names` from the run, so identity is the POPULATION, not the stamp.

    Non-vacuity, MEASURED against the pre-fix probe (`75bfd9c6e`): copying arm 1 over
    arm 2 with arm 2's graph scores `FAIL, decidable: True` — a decided verdict from one
    realization, which the pin catches. ⚠️ It does NOT score `PASS` in this direction;
    the PASS-forging direction is the mirror (arm 2, share 0.5652, copied over arm 1 with
    the epoch bumped). The guard is direction-agnostic, so both are refused, but the
    claim has to be the measured one.
    """
    repo = _bundle(tmp_path)
    first = repo / "docs/evidence/9889/e1-paired-off-arm-1.json"
    second = repo / "docs/evidence/9889/e1-paired-off-arm-2.json"
    # Arm 1 COPIED over arm 2, with only the stamp changed: the population is arm 1's,
    # the name claims to be arm 2's.
    copy = json.loads(first.read_text())
    second_record = json.loads(second.read_text())
    assert copy["object_names"] != second_record["object_names"], (
        "the SHIPPED before arms already carry one population — re-derive this pin")
    copy["graph"] = second_record["graph"]
    assert copy["graph"] != json.loads(first.read_text())["graph"]
    second.write_text(json.dumps(copy))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    err = capsys.readouterr().err
    assert "SAME `object_names`" in err, err
    assert "n=1" in err, err


def test_a_RE_ORDERED_copy_of_one_before_arm_is_still_one_realization(
        tmp_path, capsys):
    """#10610 review at `ec7b5e7fe` (P1): the identity is the POPULATION, not its ORDER.

    The re-stamp guard compared `object_names` with `==`, i.e. as an ORDERED LIST, so
    it was still one cheap edit wide. Copying arm 2 over arm 1, giving it arm 1's
    graph stamp (so the graph-equality guard legitimately passes — it IS a different
    name) and REVERSING the name list left every later reconciliation green, because
    `objects == len(names)` and the bare-definite count are both order-independent:

        E1 PASS decidable=True (before spread 0.0 pp)
           before 0.5652 -> bound 0.2826 vs after 0.2131 = PASS
           before 0.5652 -> bound 0.2826 vs after 0.2131 = PASS
        EXIT=0

    — a DECIDED row asserted from the replication the design requires and never got,
    with `before_spread_pp: 0.0` reading as perfect agreement rather than as the
    absent replication it is. That is the exact state `read_off_arms` documents this
    guard to refuse, reached by `list(reversed(...))` instead of by a one-second epoch
    bump — the pseudoreplication #9889's own O/I/T forbids.

    Non-vacuity: measured at the pre-fix head (`git show HEAD:e1_e2_scores.py` run over
    the same mutated bundle) the run exited **0** and wrote that DECIDED record; at
    this head it exits 2. `assert attack["object_names"] != arm2["object_names"]`
    keeps the pin honest — if reversing ever became a no-op the pin would be inert.
    """
    repo = _bundle(tmp_path)
    first = repo / "docs/evidence/9889/e1-paired-off-arm-1.json"
    second = repo / "docs/evidence/9889/e1-paired-off-arm-2.json"
    arm1 = json.loads(first.read_text())
    arm2 = json.loads(second.read_text())
    assert arm1["object_names"] != arm2["object_names"], (
        "the SHIPPED before arms already carry one population — re-derive this pin")
    attack = json.loads(json.dumps(arm2))
    attack["graph"] = arm1["graph"]
    attack["object_names"] = list(reversed(attack["object_names"]))
    assert attack["object_names"] != arm2["object_names"], (
        "reversing the list must CHANGE it, or this pin cannot tell an "
        "order-insensitive identity from an order-sensitive one")
    assert attack["object_names"] != arm1["object_names"], (
        "the reversal must differ from arm 1's order too — otherwise the ordered "
        "comparison would refuse for the wrong reason")
    first.write_text(json.dumps(attack))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    err = capsys.readouterr().err
    assert "SAME `object_names`" in err, err
    assert "re-ordering the list" in err, err


def test_a_before_arm_census_with_a_DUPLICATED_name_is_refused(tmp_path, capsys):
    """#10610 self-audit of the guards at `380a2b7a2` (P1): a census has no duplicates.

    ⚠️ No review comment raised this: the review recorded at that head returned
    CLEAN, so this finding and its three siblings came from this lane's own audit
    of the pre-fix guards.

    The identity guard added above reasons about multiplicity ACROSS arms; nothing
    read it WITHIN one. `e1_paired_arm.py` writes one row per `:Object` node, so a
    name can appear once — and a duplicated population is self-consistent for EVERY
    guard on the block: `objects == len(names)` (a duplicate is still a member),
    `bare_in_names`, `_consistent_share` and `label_counts["Object"]` all reconcile.
    Measured at the pre-fix head, replacing arm 1's 78 names with 78 copies of one
    name and reconciling `bare_definite: 78, share: 1.0, label_counts: 78`:

        E1 PASS decidable=True (before spread 43.5 pp)
           before 1.0000 -> bound 0.5000 vs after 0.2131 = PASS
           before 0.5652 -> bound 0.2826 vs after 0.2131 = PASS
        EXIT=0

    — a DECIDED row computed over a census no producer can write. The E2a census
    beside this one has refused the same shape since `f12471075`
    (`len(set(ids)) != len(ids)`); this is that rule on the population E1's share
    is computed over. Non-vacuity: measured, pre-fix exit 0 and this same bundle
    post-fix exit 2.
    """
    repo = _bundle(tmp_path)
    first = repo / "docs/evidence/9889/e1-paired-off-arm-1.json"
    record = json.loads(first.read_text())
    names = record["object_names"]
    assert len(set(names)) == len(names), (
        "the SHIPPED before arm already carries a duplicate — re-derive this pin")
    record["object_names"] = ["the same object"] * len(names)
    record["bare_definite"] = len(names)
    record["share"] = 1.0
    record.setdefault("label_counts", {})["Object"] = len(names)
    first.write_text(json.dumps(record))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    err = capsys.readouterr().err
    assert "lists the same `object_names` member twice" in err, err
    assert "CENSUS" in err, err


def test_a_before_arm_census_member_of_another_type_is_refused(tmp_path, capsys):
    """#10610 self-audit at `380a2b7a2` (P3): the refusal must NAME the field, not a session.

    The member type-check was borrowed from `_unit_id_members`, whose message says
    "session" — but a BEFORE ARM HAS NO SESSIONS, so it reported the graph name as a
    session id and (with `{field}={member!r}`) the offending member as if it were the
    whole population. A message that misnames the field is a defect in the one string
    an operator uses to find the bad record, so it is pinned here.

    The check itself matters as much as the wording: without it the member reaches
    `sorted(...)`, which raises `TypeError` — exit 1 with a traceback, beside a guard
    whose failure mode must be the exit-2 refusal.
    """
    repo = _bundle(tmp_path)
    first = repo / "docs/evidence/9889/e1-paired-off-arm-1.json"
    record = json.loads(first.read_text())
    record["object_names"] = [{"not": "a name"}, *record["object_names"][1:]]
    first.write_text(json.dumps(record))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    err = capsys.readouterr().err
    assert "`object_names` member" in err, err
    assert "not an object name" in err, err
    assert "session" not in err, (
        "a before arm has no sessions — the refusal must not report the graph name "
        f"as one: {err}")


@pytest.mark.parametrize("value,label", [
    (float("nan"), "nan"),
    (float("inf"), "inf"),
    (0, "zero"),
    ("0.2", "string"),
    (True, "bool"),
])
def test_a_frozen_share_floor_that_is_not_a_floor_is_refused(
        value, label, tmp_path, capsys):
    """#10610 self-audit at `380a2b7a2` (P2): a floor that binds nothing is not a floor.

    `frozen_denominators` read the three floors RAW and used them as comparison
    thresholds. JSON carries the `NaN`/`Infinity` literals, so
    `e1_min_before_share = NaN` made E1's floor describe nothing — every `share < nan`
    is False — and a control arm too flat to measure E1 was accepted. Composed with a
    zero-share before arm (legal under `_consistent_share` when `bare_definite: 0`)
    the inert floor let the pairing through and the ratio at
    `after["share"] / arm["share"]` raised `ZeroDivisionError: float division by
    zero` — exit **1** with a traceback, the contract violation the two
    `OverflowError` arms in this file close, one function over. `inf` was refused only
    incidentally (`share < inf` happens to be True), which is luck and not a rule.

    `0` is the third arm: `share < 0` is unreachable for a share, so a zero floor
    refuses nothing and re-opens the division from the other side. A string and a
    bool are the type arms — `bool` is an `int` in Python, and `float("0.2")` would
    accept the string and hand the raw value to the comparison.

    Non-vacuity, MEASURED and stated per arm rather than as one blanket claim (the
    first cut of this docstring said "all of these exited 0", which the measurement
    refutes): `nan` and `0` both exited **0** and wrote a record at the pre-fix head,
    so those two are NEW coverage. `inf`, the string and the bool were ALREADY
    refused there — `inf` incidentally (`share < inf` happens to be True), the other
    two by `_parse_bars`' own type checks — so those three arms are regression
    protection for a refusal that exists for the wrong reason, which is why they are
    kept rather than dropped.
    """
    repo = _copied_bundle(tmp_path)
    path = repo / "docs/evidence/7949-capstone/preregistration.json"
    prereg = json.loads(path.read_text())
    prereg["denominator_floors"]["e1_min_before_share"] = value
    path.write_text(json.dumps(prereg))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, (label, refused.value)
    err = capsys.readouterr().err
    assert "e1_min_before_share" in err, err


@pytest.mark.parametrize("key,value,label", [
    ("e1_objects", 0, "zero"),
    ("e1_objects", -5, "negative"),
    ("e2b_surviving_units", 0, "zero"),
    ("e2b_surviving_units", -1, "negative"),
])
def test_a_frozen_COUNT_floor_that_binds_nothing_is_refused(
        key, value, label, tmp_path, capsys):
    """#10610 review at `120381532` (P2): the positivity arm covered the SHARE floor only.

    The loop above the share-floor arm validated all three `denominator_floors`
    entries for TYPE and FINITENESS, and then required `> 0` of `e1_min_before_share`
    alone — on the reasoning that `0` is not a floor because `share < 0` is
    unreachable for a share. That reasoning holds verbatim for the two COUNT floors,
    which was the point: `count < 0` is equally unreachable, so `e1_objects: 0` and
    `e2b_surviving_units: 0` were accepted and then bound nothing at their consumers.

    Measured end-to-end at the pre-fix head rather than argued: with
    `e2b_surviving_units = 0` the shipped bundle still scores, and an ON arm edited to
    `fused = 0` yields a DECIDED verdict at exit **0** where the frozen floor of `30`
    correctly REFUSES it as an unmeasurable population (`E2 REFUSED … E2b fused 0/3`).
    `e1_objects: 0` likewise disables both the under-floor refusal in `read_off_arms`
    and the BLOCKED-vs-decided split in `e1_pairings`.

    So each arm here is NEW coverage, not regression protection: all four exited **0**
    and wrote a record at the pre-fix head (asserted below by construction — the pin
    is on exit 2, and the pre-fix head reached exit 0).
    """
    repo = _copied_bundle(tmp_path)
    path = repo / "docs/evidence/7949-capstone/preregistration.json"
    prereg = json.loads(path.read_text())
    prereg["denominator_floors"][key] = value
    path.write_text(json.dumps(prereg))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, (key, value, label, refused.value)
    err = capsys.readouterr().err
    assert key in err, err


def test_an_ABSENT_value_lost_is_refused_and_is_not_an_empty_list(tmp_path, capsys):
    """#10610 self-audit at `380a2b7a2` (P2): the field a forger DELETES switched the rule off.

    `_census_reconcile`'s partition rule was gated on `lost is not None`, so the one
    field a forger removes was the one that disabled the check. Measured on the
    shipped bundle — delete `value_lost` from every `per_session` row, set
    `value_survived = value_ids` and mirror the falsifier's `as_written` block —
    the run wrote **`E2 PASS — E2a 14/14`, exit 0**, which is the identical forgery
    that block's own docstring records having measured and closed for the
    row-present case ("the census `kept = ids` with `value_lost` untouched wrote
    `E2 PASS`").

    An absent field is a REFUSAL, not an empty list: `e2a_value_units.py` writes
    `value_lost` on every row and all 21 rows across the three shipped records carry
    it, so absence is an edit. This is the rule the rest of the file already applies
    ("an absent provenance is not a default one").
    """
    repo = _copied_bundle(tmp_path)
    for name in ("e2a-value-units.json", "e2a-value-units-falsifier.json"):
        path = repo / "docs/evidence/9889" / name
        record = json.loads(path.read_text())

        def _strip(block):
            for row in block["per_session"].values():
                assert "value_lost" in row, (
                    "the SHIPPED record already omits value_lost — re-derive this pin")
                row.pop("value_lost")
                row["value_survived"] = list(row["value_ids"])
            block["value_survived"] = block["value_units"]

        if "per_session" in record:
            _strip(record)
        for key in ("with_anchor", "as_written"):
            if isinstance(record.get(key), dict) and "per_session" in record[key]:
                _strip(record[key])
        path.write_text(json.dumps(record))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    err = capsys.readouterr().err
    assert "carries no value_lost" in err, err
    assert "PARTITION" in err, err


def test_the_before_arms_counts_are_the_population_they_count(tmp_path, capsys):
    """#10610 review at `75bfd9c6` (P2): a count that disagrees with the census.

    `_consistent_share` reconciled the share against `bare_definite`/`objects`, and
    nothing reconciled THOSE with the `object_names` they are counted from. Measured: arm
    1 edited to `bare_definite: 70, share: 70/78` with its 78 names untouched (28 bare of
    78) scored a DECIDED `PASS` at a 33.2 pp spread. Three arms, one per census field.
    """
    cases = (
        ("bare", lambda rec: (rec.__setitem__("bare_definite", 70),
                              rec.__setitem__("share", 70 / 78)),
         "states bare_definite=70"),
        ("objects", lambda rec: (rec.__setitem__("objects", 77),
                                 rec.__setitem__("share", 28 / 77)),
         "states objects=77"),
        ("labelled", lambda rec: rec["label_counts"].__setitem__("Object", 70),
         "reports 70 `:Object`"),
    )
    for marker, mutate, expected in cases:
        repo = _bundle(tmp_path / marker)
        path = repo / "docs/evidence/9889/e1-paired-off-arm-1.json"
        record = json.loads(path.read_text())
        assert record["objects"] == 78 and len(record["object_names"]) == 78, record.keys()
        mutate(record)
        path.write_text(json.dumps(record))
        with pytest.raises(SystemExit) as refused:
            scores_probe.score(repo)
        assert refused.value.code == 2, (marker, refused.value)
        err = capsys.readouterr().err
        assert expected in err, (marker, err)


@pytest.mark.parametrize("marker,mutate,expected", (
    ("absent", lambda rec: rec.pop("label_counts"),
     "carries no `label_counts` census"),
    ("str", lambda rec: rec.__setitem__("label_counts", "x"),
     "carries no `label_counts` census"),
    ("list", lambda rec: rec.__setitem__("label_counts", [1, 2]),
     "carries no `label_counts` census"),
    ("object-str", lambda rec: rec["label_counts"].__setitem__("Object", "78"),
     "is not a node count"),
    ("object-bool", lambda rec: rec["label_counts"].__setitem__("Object", True),
     "is not a node count"),
    ("object-absent", lambda rec: rec["label_counts"].pop("Object"),
     "reports 0 `:Object`"),
))
def test_a_before_arms_label_census_cannot_be_omitted_away(marker, mutate, expected, tmp_path, capsys):
    """#10610 review at `2ffc5db19` (P2): `isinstance(counts, dict)` WAS the guard.

    Every other census arm in this file reconciles `objects` / `bare_definite` /
    `share` against `object_names`, and `label_counts` is the run's SECOND census of
    the same population. But the whole check was wrapped in `if isinstance(counts,
    dict):`, so an ABSENT or wrong-typed `label_counts` skipped it silently and the
    record still scored — the `isinstance(...)`-as-a-no-op shape the sibling
    `gate_names` guard had already closed one field over.

    Measured at the pre-fix head on copies of `e1-paired-off-arm-1.json`: replacing
    `label_counts` with `"x"`, and deleting the field, both exited **0** and wrote a
    record. A label map that does not list `Object` means the run counted none, so
    an absent `Object` is 0 rather than "unchecked", and 0 beside a positive
    `objects` is the refusal along with the rest.
    """
    repo = _bundle(tmp_path / marker)
    path = repo / "docs/evidence/9889/e1-paired-off-arm-1.json"
    record = json.loads(path.read_text())
    assert isinstance(record["label_counts"], dict) and record["label_counts"]["Object"] == 78, (
        "the SHIPPED before arm no longer carries label_counts.Object — re-derive this pin")
    mutate(record)
    path.write_text(json.dumps(record))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, (marker, refused.value)
    err = capsys.readouterr().err
    assert expected in err, (marker, err)

    # …and the SHIPPED bundle still scores, so the guard discriminates a record whose
    # second census was removed from the one that ships rather than refusing the form.
    # ⛔ Asserted against the ACTUAL outcome, not a membership test: every return
    # value satisfies `in ("REFUSED", "PASS", "FAIL")`, so a tautology there would
    # only prove `score()` did not raise (#10610 review at `bde94f89d`, P3).
    shipped = _bundle(tmp_path / "shipped")
    assert scores_probe.score(shipped)["e1"]["outcome"] == "REFUSED"


def test_a_gate_drops_census_member_of_another_type_is_refused(tmp_path, capsys):
    """#10610 review at `2ffc5db19` (P3): the ON run's only independent witness.

    `gate_bare` counts `_is_bare_definite(item)`, and that helper stringifies, so a
    list or dict member silently counted as NON-bare. `object_names: [0..60]` was
    refused only INCIDENTALLY — its bare count came out 0 against the log's 13 — and
    a member of another type orchestrated to the right count would have scored. The
    before arms and `_unit_id_members` already refuse a member of another type; this
    is the same rule on the census `e1-gate-drops.json` is cited as witness FOR.
    """
    repo = _bundle(tmp_path / "gate")
    path = repo / "docs/evidence/9889/e1-gate-drops.json"
    record = json.loads(path.read_text())
    names = record["object_names"]
    assert names and all(isinstance(n, str) for n in names), (
        "the SHIPPED gate-drops census is not all strings — re-derive this pin")
    record["object_names"] = [i for i in range(len(names))]
    path.write_text(json.dumps(record))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    err = capsys.readouterr().err
    assert "is not an object name" in err, err
    assert "e1-gate-drops.json" in err, err


def test_a_gate_drops_census_that_lists_a_member_twice_is_refused(tmp_path, capsys):
    """#10610 review at `bde94f89d` (P2): the ON witness census is a POPULATION.

    The before arms refuse a duplicated `object_names` member ("a census no producer
    can write") and the E2a census gets the same rule through its partition check, but
    `e1-gate-drops.json`'s census had neither. Measured at the pre-fix head: replacing
    the 61 names with the 13 bare ones plus 48 COPIES of one non-bare name kept
    `len(gate_names) == after["objects"]` (61) and `gate_bare ==
    after["bare_definite"]` (13), and `score()` wrote a record at exit 0 over a
    population no producer can emit — which is exactly the coupled multi-field
    forgery this witness exists to refuse.

    Non-vacuity: removing the `len(set(...))` rule makes this case score instead of
    refusing (measured).
    """
    repo = _bundle(tmp_path / "dup")
    path = repo / "docs/evidence/9889/e1-gate-drops.json"
    record = json.loads(path.read_text())
    names = record["object_names"]
    bare = [n for n in names if scores_probe._is_bare_definite(n)]
    assert len(set(names)) == len(names), (
        "the SHIPPED gate-drops census already carries a duplicate — re-derive this pin")
    assert bare and len(bare) < len(names), (len(bare), len(names))
    non_bare = next(n for n in names if n not in bare)
    record["object_names"] = bare + [non_bare] * (len(names) - len(bare))
    path.write_text(json.dumps(record))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    err = capsys.readouterr().err
    assert "lists the same `object_names` member twice" in err, err


def test_the_bare_definite_shape_is_the_repos_own_predicate():
    """The driver's local copy of the repo's predicate must BE the repo's predicate.

    `capstone_measure._is_bare_definite` defers to
    `tortoise.definite_description_backfill.is_bare_definite_description`; the scoring
    driver cannot import it (it runs before any DB is up and is stdlib-only by design), so
    it carries the shape as a literal. That is the copy that drifts, so it is held to the
    repo's own function over the shipped populations AND the edge cases a regex rewrite
    would get wrong (`"the"` alone, a trailing space, a newline in the body, `"The"`-case,
    a tab separator, an empty name).
    """
    from tortoise.definite_description_backfill import (
        is_bare_definite_description as repo_predicate,
    )
    shipped: list[str] = []
    for name in ("e1-paired-off-arm-1.json", "e1-paired-off-arm-2.json"):
        shipped.extend(json.loads(
            (_REPO / "docs/evidence/9889" / name).read_text())["object_names"])
    edge = ["the x", "the", "the ", "The Owner", "the\ttabbed", "", "the x\n",
            " the padded body ", "a the x", "the \n span", "THE END"]
    for sample in [*shipped, *edge]:
        assert scores_probe._is_bare_definite(sample) == bool(repo_predicate(sample)), (
            f"the driver's bare-definite shape disagrees with the repo predicate on "
            f"{sample!r} — a share computed over a different rule than the producer's")


def test_the_two_before_arms_must_be_two_RUNS_not_two_copies(tmp_path, capsys):
    """#10610 review P2: the before pair is the ONE place the run rule inverts.

    Every other record class in the driver refuses a record from a DIFFERENT run
    ("the same head is not the same run"). The before pair is the one pair that
    must not be the same run, and nothing enforced it: `decidable` is computed
    from the SET of pairings, so two copies of a single OFF reading pair
    identically and come back `decidable: True` — "every before realization
    agrees" over ONE realization, plus a `before_spread_pp: 0.0` that reads as
    perfect agreement rather than as the absent replication it is. The row then
    drops its honest REFUSED for a decided FAIL, and `e1_row_note` narrates
    agreement between realizations that never happened.

    The producers mint a fresh `capstone_off_<head8>_<stamp>` per run, so an
    identical graph is a duplicated record, not a replicate — which also makes
    this the cheapest way to fake #9889's own next requirement (n >= 2 per arm).

    Non-vacuity, measured: at the pre-fix head this exact bundle SCORES a decided
    `FAIL` (exit 0, `before_spread_pp: 0.0`, both pairings on
    `capstone_off_54922a800_1793378986`), so the `pytest.raises` arm reds.
    """
    repo = _bundle(tmp_path)
    first = repo / "docs/evidence/9889/e1-paired-off-arm-1.json"
    second = repo / "docs/evidence/9889/e1-paired-off-arm-2.json"
    graph = json.loads(first.read_text())["graph"]
    whole = second.read_text()
    assert graph != json.loads(whole)["graph"], (
        "the SHIPPED before arms already share a graph — re-derive this pin")
    second.write_text(first.read_text())              # the same run, copied
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    err = capsys.readouterr().err
    assert "the same run is not a second realization" in err, err
    assert graph in err, err
    assert "e1-paired-off-arm-1.json" in err, err
    assert "e1-paired-off-arm-2.json" in err, err

    # …and the shipped bundle (TWO graphs, 20.6 pp apart) still scores, so this
    # discriminates a duplicated record from the pair that ships rather than
    # refusing the form.
    second.write_text(whole)
    assert scores_probe.score(repo)["e1"]["decidable"] is False


def test_a_before_arm_with_no_graph_is_refused(tmp_path, capsys):
    """An ABSENT run is not a second realization either.

    The duplicate check compares graphs, so an artifact that names none compares
    equal to any other artifact that names none — the refusal would fire for the
    wrong reason (or, one arm later, not at all). Refused as an absent provenance,
    the same rule the falsifier's `as_written` block is held to: "an absent
    provenance is not an inherited one".

    Non-vacuity, MEASURED at this pin's own parent commit `9aee307c8`: the bundle
    does not score there either — `read_off_arms` already collected `graph` as
    `None`, so `score()`'s head loop refuses ONE CHECK LATER with "was taken on
    graph None, which does not name head 54922a80" (`PRE-FIX REFUSES: code=2`).
    The discriminator this pin asserts is therefore the MESSAGE and not the
    refusal: neither "carries no `graph`" nor "SECOND realization" appears in
    the pre-fix text, so the two assertions below red at `9aee307c8` while the
    refusal itself would look like a pass to a pin that only checked the exit.
    """
    repo = _bundle(tmp_path)
    path = repo / "docs/evidence/9889/e1-paired-off-arm-2.json"
    record = json.loads(path.read_text())
    assert record["graph"] == "capstone_off_54922a800_1793379347"   # ships with one
    record.pop("graph")
    _shipped_arm(record)          # …so the ONLY defect is the absent graph
    path.write_text(json.dumps(record))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    err = capsys.readouterr().err
    assert "carries no `graph`" in err, err
    assert "SECOND realization" in err, err


_BEFORE_PIN_CASES = (
    # (label, field, wrong value the record CLAIMS, the class it is refused for)
    ("corpus", "fixtures_hash", "sha256:" + "0" * 64, "another_corpus"),
    ("object_floor", "object_floor", 29, "another_bar"),
    ("min_before_share", "min_before_share", 0.10, "another_bar"),
)


def test_a_before_arm_that_names_another_pin_is_refused(tmp_path, capsys):
    """#10610 review P2 (category A): E1's CONTROL arm may not name another corpus.

    E1's bar is `after <= 0.5 x before`, so the two before shares are the
    denominator of the only number E1 decides — and `read_off_arms` read `arm`,
    `graph`, `objects`, `bare_definite`, `share`, `object_names`, `label_counts`
    and NO corpus field at all. The ON arm's corpus is re-verified against the
    frozen pin; the pair the bar is computed over was not. `#9076`'s rule (a bar
    is a threshold **AND its corpus**) forbids that asymmetry, so a before
    artifact that NAMES a pin must name the FROZEN one.

    The absent case is disclosed rather than refused (the shipped pair predates
    the four pin fields and cannot be re-recorded) — that is the sibling pin
    below. This one is the case that can LIE.

    Non-vacuity, MEASURED at the pre-fix head: the driver ignored unknown keys, so
    this bundle SCORES (`e1.outcome == "REFUSED"`, exit 0) and the `pytest.raises`
    arm reds without the fix.
    """
    for label, field, value, kind in _BEFORE_PIN_CASES:
        repo = _bundle(tmp_path / label)
        path = repo / "docs/evidence/9889/e1-paired-off-arm-1.json"
        record = json.loads(path.read_text())
        assert field not in record, (label, "the shipped arm predates the pin")
        record[field] = value
        _shipped_arm(record)      # …so the ONLY defect is the pin it names
        path.write_text(json.dumps(record))
        with pytest.raises(SystemExit) as refused:
            scores_probe.score(repo)
        assert refused.value.code == 2, (label, refused.value)
        err = capsys.readouterr().err
        assert "e1-paired-off-arm-1.json" in err, (label, err)
        assert "not the frozen" in err, (label, err)
        if kind == "another_corpus":
            assert "corpus" in err, (label, err)
            # …and the refusal names the frozen pin it contradicted.
            assert scores_probe.normalise_pin(
                scores_probe.frozen_denominators(repo)["fixtures_hash"]) in err, err
        else:
            assert "different bar" in err, (label, err)


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_a_blank_before_corpus_pin_is_not_an_absent_one(blank, tmp_path, capsys):
    """A pin written BLANK is a claim that was never made — not an absent field.

    `fixtures_hash: null` (or `""`) is a present-and-unusable pin, and treating it
    as "absent, therefore disclosed" would let an edited artifact inherit the
    shipped pair's disclosed status while asserting a corpus it never measured.
    The sibling guards separate absent from present-but-empty on the same rule
    (`arm` block keys, `with_anchor`, `object_names`).

    Non-vacuity: pre-fix the key is ignored entirely, so the bundle scores.
    """
    repo = _bundle(tmp_path / "blank")
    path = repo / "docs/evidence/9889/e1-paired-off-arm-2.json"
    record = json.loads(path.read_text())
    record["fixtures_hash"] = blank
    _shipped_arm(record)          # …so the ONLY defect is the blank pin
    path.write_text(json.dumps(record))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    assert "EMPTY `fixtures_hash`" in capsys.readouterr().err


def test_the_before_arms_corpus_is_disclosed_in_the_record(tmp_path):
    """The limitation TRAVELS with the number, and does not live only in the README.

    The shipped pair cannot be re-recorded (its graphs lived in a removed container
    and a re-run is a new LLM realization), so the driver discloses instead: the
    record names both before artifacts, the four pin fields they lack, and the
    fact that the after arm's corpus IS re-verified while theirs is not. This is
    the half that makes the asymmetry reachable by whoever reads
    `e1-e2-scores.json` — which is the artifact the row builder consumes.

    Non-vacuity: pre-fix the key does not exist, so this raises `KeyError`.
    """
    record = scores_probe.score(_bundle(tmp_path))
    block = record["before_arm_provenance"]
    assert block["status"] == "unpinned", block
    assert block["pin_fields"] == list(scores_probe._BEFORE_PIN_FIELDS), block
    assert [arm["artifact"] for arm in block["arms"]] == [
        "docs/evidence/9889/e1-paired-off-arm-1.json",
        "docs/evidence/9889/e1-paired-off-arm-2.json",
    ], block["arms"]
    for arm in block["arms"]:
        assert arm["pin_fields_absent"] == list(scores_probe._BEFORE_PIN_FIELDS), arm
        assert arm["pin_fields_present"] == [], arm
        assert arm["fixtures_hash"] is None and arm["unpinned"] is True, arm
        # The head is absent for the same reason, and is NOT compared here: a
        # before arm's graph stamps its head, and that pair IS checked in `score()`.
        assert arm["head"] is None, arm
    # The producing revision is not derivable from the artifact, so the record
    # cites the section that states it rather than inventing one.
    assert block["probe_revision"] is None, block
    readme = (_REPO / "docs/evidence/9889/README.md").read_text()
    heading = block["probe_revision_source"].split("#", 1)[1]
    assert heading.replace("-", " ") in readme.lower(), (
        f"`probe_revision_source` cites a README section that does not exist: "
        f"{heading!r}")
    assert "n >= 2" in block["limitation"], block["limitation"]
    # …and the OLD `corpus_pin_source` sentence is gone: it read as if G1 covered
    # the measurement, when it covered each run at producer time and is not
    # re-verifiable from the pair.
    assert "BEFORE arms cannot re-prove it" in record["corpus_pin_source"], (
        record["corpus_pin_source"])
    assert "before_arm_provenance" in record["corpus_pin_source"]
    # No number moves: the disclosed pair still scores REFUSED at n=1.
    assert record["e1"]["outcome"] == "REFUSED", record["e1"]
    assert record["e1"]["decidable"] is False, record["e1"]


def test_a_before_arm_that_carries_the_frozen_pin_reads_pin_checked(tmp_path):
    """…and the guard cannot be satisfied by refusing the whole form.

    A re-recorded pair (what the shipped probe writes today) carries the frozen
    `fixtures_hash`, and it must be ACCEPTED — with the record reporting
    `pin-checked` and the limitation stating the asymmetry is closed, so the
    disclosure cannot survive a fixed pair and read as a permanent excuse.

    The two arms spell the pin differently on purpose: the producer writes
    `sha256:<hex>` and a bare `<hex>` is the same pin — the comparison is
    `normalise_pin`'s, exactly as the after arm's is.
    """
    repo = _bundle(tmp_path)
    frozen = json.loads(PREREG.read_text())["corpus"]["fixtures_hash"]
    for index, value in ((0, frozen), (1, scores_probe.normalise_pin(frozen))):
        path = repo / f"docs/evidence/9889/e1-paired-off-arm-{index + 1}.json"
        record = json.loads(path.read_text())
        record["fixtures_hash"] = value
        _shipped_arm(record)      # a re-recorded pair pins the gate explicitly too
        path.write_text(json.dumps(record))
    scored = scores_probe.score(repo)
    block = scored["before_arm_provenance"]
    assert block["status"] == "pin-checked", block
    for arm in block["arms"]:
        assert arm["unpinned"] is False, arm
        assert "fixtures_hash" in arm["pin_fields_present"], arm
        assert arm["fixtures_hash"] is not None, arm
    assert block["limitation"].startswith("closed:"), block["limitation"]


def test_the_default_on_key_set_is_the_one_whose_default_moved():
    """`_DEFAULT_ON_VARS` is DERIVED from the resolvers, never restated.

    ⛔ THE WHOLE FIX TURNS ON WHICH KEY MOVED (`#10996` review cycle 2, P1). The
    driver stops reading ABSENCE as an OFF spelling for exactly the switches whose
    UNSET value is now ON — no more (that would refuse genuine control arms whose
    switches still default off) and no fewer (that would leave the hole open for
    the one key that moved). So the literal is pinned against the real resolvers:
    with the flags CLEARED, `value_gate.value_gate_enabled()` must be True and
    `s2_decision_seam.seam_enabled()` must be False, and the tuple must be the set
    that reads ON. If a future flag joins the default-ON family, the producer
    sets move and this reds instead of the driver silently keeping the old one.
    """
    from tortoise import s2_decision_seam, value_gate

    cleared = {key: os.environ.pop(key, None)
               for key in scores_probe._ARM_OFF_VARS}
    try:
        assert value_gate.value_gate_enabled() is True, (
            "the S2.2 mechanical half must read ON with the flag unset — that is "
            "the default this driver's absence rule is about")
        assert s2_decision_seam.seam_enabled() is False, (
            "the seam is a paid call and still defaults OFF, so absence remains a "
            "genuine OFF spelling for it")
        reads_on_when_unset = {
            key for key, arm in (("TORTOISE_VALUE_GATE",
                                  value_gate.value_gate_enabled()),
                                 ("TORTOISE_S2_DECISION_SEAM",
                                  s2_decision_seam.seam_enabled()))
            if arm}
    finally:
        for key, value in cleared.items():
            if value is not None:
                os.environ[key] = value
    assert reads_on_when_unset == set(scores_probe._DEFAULT_ON_VARS), (
        scores_probe._DEFAULT_ON_VARS, reads_on_when_unset)
    assert set(scores_probe._DEFAULT_ON_VARS) <= set(scores_probe._ARM_OFF_VARS), (
        "a switch this driver does not check cannot need the absence rule")


def test_a_before_arm_that_expresses_the_value_gate_by_absence_is_refused(
        tmp_path, capsys):
    """A record the SHIPPED producer could write may not say OFF by leaving it out.

    ⛔ `#10996` review cycle 2, P1: `_is_truthy(None)` is False, so an absent
    `TORTOISE_VALUE_GATE` certified the before arm OFF — while the post-flip
    reader resolves absence to ON and runs the predicate inside it. The pair this
    driver scores (`after <= 0.50 x before`) could then be TWO gate-armed arms and
    still be reported as a decided E1. `e1_paired_arm.off_arm_readback` pins the
    key explicitly now, so a record carrying a pin field and spelling the gate as
    absent is not a shipped shape and is refused.

    Non-vacuity, run rather than reasoned: the SAME record with the shipped `"0"`
    scores (`read_off_arms` returns it), so the refusal is caused by the absence
    and not by some other defect in the fixture.
    """
    repo = _bundle(tmp_path)
    path = repo / "docs/evidence/9889/e1-paired-off-arm-1.json"
    record = json.loads(path.read_text())
    assert scores_probe._is_absent_pin(record["arm"]["TORTOISE_VALUE_GATE"])
    record["head"] = scores_probe._PRE_FLIP_HEAD     # the shipped probe's pin field
    path.write_text(json.dumps(record))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    err = capsys.readouterr().err
    assert "CARRIES" not in err, err
    assert ("records TORTOISE_VALUE_GATE as ABSENT" in err
            or "`arm` block records TORTOISE_VALUE_GATE as ABSENT" in err), err
    assert "TORTOISE_VALUE_GATE=0" in err, err

    # …and the same record, shipped-shaped, is ACCEPTED: the refusal is the absence.
    _shipped_arm(record)
    path.write_text(json.dumps(record))
    assert scores_probe.score(repo)["e1"]["decidable"] is False


def test_a_before_arm_at_another_head_cannot_express_the_value_gate_by_absence(
        tmp_path, capsys):
    """The legacy licence is a HEAD, so removing the pin fields is not enough.

    The two committed arms predate the pin fields AND name the pre-`#10996`
    campaign head, and both halves are required: a record that merely LOOKS old
    (no `head`/`fixtures_hash`/… because someone stripped them) but names another
    head was measured by code that could be post-flip, so its absent value gate
    cannot be read as OFF. Without this half the licence degenerates into a
    spelling rule anyone can satisfy by deleting four keys.
    """
    repo = _bundle(tmp_path)
    path = repo / "docs/evidence/9889/e1-paired-off-arm-1.json"
    record = json.loads(path.read_text())
    assert scores_probe._is_absent_pin(record["arm"]["TORTOISE_VALUE_GATE"])
    # Legacy SHAPE (no pin fields), a head from after the flip's campaign.
    record["graph"] = "capstone_off_9152448d_1793378986"
    path.write_text(json.dumps(record))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    err = capsys.readouterr().err
    assert "records TORTOISE_VALUE_GATE as ABSENT" in err, err
    assert scores_probe._PRE_FLIP_HEAD[:9] in err, err


def test_the_after_arm_at_another_head_cannot_express_the_value_gate_by_absence(
        tmp_path, capsys):
    """`<unset>` on the ON log is a legacy spelling too, and bound to the same head.

    `capstone_measure.measured_arm` now writes `TORTOISE_VALUE_GATE=0` and reads it
    back, so the committed `<unset>` is an artifact of the pre-flip producer. A log
    from ANY OTHER head that still spells it absent is refused: its pinned-OFF
    switch was in fact armed, so the ON half of the pair is the value-gate arm —
    the easier side of E1's bar under a label that never declared it.
    """
    repo = _bundle(tmp_path)
    log = repo / "docs/evidence/9889/on-arm.log"
    text = log.read_text()
    assert "TORTOISE_VALUE_GATE=<unset>" in text, text[:400]
    other = "9f3c1d7e8a2b4c5d6e7f8091a2b3c4d5e6f70819"
    text = re.sub(r"^# head: \S+", f"# head: {other}", text, count=1,
                  flags=re.MULTILINE)
    text = re.sub(r"^# graph: \S+", f"# graph: capstone_on_{other[:8]}_1793378318",
                  text, count=1, flags=re.MULTILINE)
    log.write_text(text)
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    err = capsys.readouterr().err
    assert "arm readback records TORTOISE_VALUE_GATE as ABSENT" in err, err


def test_the_on_log_graph_must_name_its_own_head(tmp_path, capsys):
    """The legacy licence reads the LOG's head, so the log's graph must agree.

    ⛔ `#10996` review (blocking finding on the first cut): the licence, the
    before-arm graph check and the after arm's own `# graph:`/`# head:` pair all
    key off the log's `# head:` line, so a log whose `# graph:` named a DIFFERENT
    run would sail through every later check on a head it merely shares — the
    head would be `54922a800` while the after share came from another graph. That
    is the same class of hole `_names_head` was hardened for at `#10610`
    (`75bfd9c6`), one level out.

    The guard had no pin when it landed: deleting it left this file green. This
    is that pin, and it is one defect per fixture — the head is untouched, only
    the graph moves.
    """
    repo = _bundle(tmp_path)
    log = repo / "docs/evidence/9889/on-arm.log"
    text = log.read_text()
    assert "# head: 54922a800" in text, text[:400]
    other = "9152448d"
    text = re.sub(r"^# graph: capstone_on_(\w+)_",
                  f"# graph: capstone_on_{other}_", text, count=1,
                  flags=re.MULTILINE)
    log.write_text(text)
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    err = capsys.readouterr().err
    assert "does not name its own `# head:` line" in err, err
    assert other in err, err


def test_the_committed_legacy_absence_is_disclosed_in_the_record(tmp_path):
    """The licence is READ, not trusted: every arm that needed it is named.

    A residual that lives only in the driver is one a reader of
    `e1-e2-scores.json` cannot see, and that file is what the row builder
    consumes. So the record says, beside the numbers, which arms were read as OFF
    on the campaign-head licence and what closes it.
    """
    record = scores_probe.score(_bundle(tmp_path))
    block = record["value_gate_pin"]
    assert block["default_on_keys"] == list(scores_probe._DEFAULT_ON_VARS), block
    assert block["legacy_head"] == scores_probe._PRE_FLIP_HEAD, block
    assert block["after"]["pin"] == "legacy-absent", block["after"]
    assert scores_probe._PRE_FLIP_HEAD[:9] in block["after"]["basis"], block
    assert [arm["pin"] for arm in block["before"]] == [
        "legacy-absent", "legacy-absent"], block["before"]
    assert [arm["artifact"] for arm in block["before"]] == [
        "docs/evidence/9889/e1-paired-off-arm-1.json",
        "docs/evidence/9889/e1-paired-off-arm-2.json",
    ], block["before"]
    assert "#7949" in block["limitation"], block["limitation"]
    assert "TORTOISE_VALUE_GATE=0" in block["limitation"], block["limitation"]
    # No number moves: the licensed pair still scores the outcome it shipped with.
    assert record["e1"]["outcome"] == "REFUSED", record["e1"]
    # …and a re-recorded pair needs no licence, so the disclosure cannot survive it.
    repo = _bundle(tmp_path / "pinned")
    for index in (1, 2):
        path = repo / f"docs/evidence/9889/e1-paired-off-arm-{index}.json"
        arm_record = json.loads(path.read_text())
        _shipped_arm(arm_record)
        path.write_text(json.dumps(arm_record))
    log = repo / "docs/evidence/9889/on-arm.log"
    log.write_text(log.read_text().replace(
        "TORTOISE_VALUE_GATE=<unset>", "TORTOISE_VALUE_GATE=0", 1))
    pinned = scores_probe.score(repo)["value_gate_pin"]
    assert pinned["after"]["pin"] == "explicit", pinned
    assert [arm["pin"] for arm in pinned["before"]] == ["explicit", "explicit"], (
        pinned["before"])


def test_the_legacy_head_really_predates_the_flip():
    """The licence's PREMISE, checked at the commit it licences.

    `_PRE_FLIP_HEAD` is admitted as "a head where absence really was OFF". That is
    a claim about the code AT THAT COMMIT, so it is read there — `git show` the
    resolver, not the working tree — and the flip's own default is required to be
    ABSENT from it. A test that read today's file would prove nothing about the
    records; one that hardcoded the answer would be the inert trust token this
    whole change removes. If the campaign head ever turns out to carry the flip,
    this reds and the licence has to be narrowed instead of silently widening.

    ⛔ READABLE-IN-THIS-CLONE IS NOT THE PREMISE. CI's `test` job checks this repo
    out with a bare `actions/checkout@v4` (`python-ci.yml`, default
    `fetch-depth: 1`), where an ANCESTOR commit does not exist by construction —
    so a hard failure here would red every CI run for the checkout alone, reported
    as a defect in the change. It SKIPS in that case, naming what is unchecked,
    exactly as `test_no_legacy_token_path.py` does for the same `git show <sha>:…`
    read. A skip is not a pass: the licence premise is UNCHECKED on a shallow
    clone, and this says so rather than passing silently as if it had checked.

    ⛔ BUT "UNREADABLE" IS NOT ONE STATE (`#10996` review). Catching the failed
    `git show` alone cannot tell a SHALLOW checkout — where the ancestor is
    absent by construction — from a WRONG `_PRE_FLIP_HEAD`, where the sha this
    licence rests on does not exist at all. The first is a skip; the second is a
    defect in the pin, and it would skip forever. So the skip is licensed by
    `git rev-parse --is-shallow-repository` itself, and an unreadable head in a
    FULL clone FAILS: the premise is then underivable, not merely unchecked.
    """
    head = scores_probe._PRE_FLIP_HEAD
    try:
        shown = subprocess.run(
            ["git", "show", f"{head}:tortoise/value_gate.py"],
            cwd=_REPO, capture_output=True, text=True)
    except FileNotFoundError:  # no `git` on this host at all
        pytest.skip("`git` is not installed here, so no commit can be read")
    if shown.returncode != 0:
        shallow = subprocess.run(
            ["git", "rev-parse", "--is-shallow-repository"],
            cwd=_REPO, capture_output=True, text=True).stdout.strip()
        if shallow == "true":
            pytest.skip(
                f"{head[:9]} (the licensed pre-flip head) is not in this "
                "clone's history — a `fetch-depth: 1` checkout cannot contain "
                "an ancestor, so the licence premise is UNCHECKED here, not "
                "disproved. Re-derive it on a full clone: "
                "`git fetch --unshallow origin` (or the worktree this branch "
                "was written in).")
        pytest.fail(
            f"{head[:9]} (the licensed pre-flip head) is not readable in a FULL "
            "clone, so the sha this licence rests on is WRONG rather than "
            "merely unreachable — a skip here would license an absence reading "
            "at a head nobody can verify. "
            f"git: {shown.stderr.strip()}")
    source = shown.stdout
    assert 'os.environ.get(ENV_FLAG, "1")' not in source, (
        f"{head[:9]} already carries the post-`#10996` default — the legacy "
        "licence for an ABSENT value gate no longer holds at this head")
    assert "os.environ.get(ENV_FLAG)" in source, (
        f"{head[:9]} does not read the flag the way the licence assumes")
    # …and the flip is what changed it, so the two heads genuinely differ.
    assert 'os.environ.get(ENV_FLAG, "1")' in (_REPO / "tortoise/value_gate.py"
                                               ).read_text()


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
    assert "fused=1/114 = 0.008772" in text
    # Keep the printed share consistent with the counts: the reconciliation that
    # refuses a counts/share disagreement is a different guard, and this case is about
    # the FLOOR (a self-consistent small population still refuses).
    log.write_text(text.replace("fused=1/114 = 0.008772", "fused=1/10 = 0.100000"))
    record = scores_probe.score(repo)
    e2 = record["e2"]
    assert e2["e2b"]["below_floor"] is True, e2["e2b"]
    assert e2["e2b"]["atomic_points"] == 10, e2["e2b"]
    assert e2["e2b"]["floor"] == 30, e2["e2b"]
    assert e2["outcome"] == "REFUSED", e2
    assert e2["decidable"] is False, e2
    assert "under the frozen floor of 30" in e2["why"], e2
    assert e2["decided_by"] == "E2b floor", e2


def test_the_readmes_floor_provenance_claim_matches_the_code(monkeypatch,
                                                             tmp_path):
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

    # ⛔ …and the sentence is about a RUN, so drive `main()`. #10610 review at
    # `2d9e8608` (P3): `parse_bars` reads `e1_min_before_share` too (this PR put it
    # in the map), and `main()` resolved the map-backed `object_floor` FIRST — so
    # for the exact shapes above the run died on a bare `ValueError` from a helper
    # and the `REFUSED:` message this paragraph describes was unreachable from the
    # production path. The helper-only assertion above could not see that split.
    # Non-vacuity: on the pre-fix head `main()` propagates `ValueError` here, so
    # the `SystemExit` expectation reds.
    monkeypatch.setattr(
        probe, "read_preregistration",
        lambda sha: {**prereg, "denominator_floors": {
            **prereg["denominator_floors"], "e1_min_before_share": "0.20"}})
    monkeypatch.setattr(probe, "resolve_head", lambda: "0" * 40)
    monkeypatch.setattr(probe, "corpus_pin", lambda record: "sha256:frozen")
    monkeypatch.setattr(probe, "backend_ready", lambda: (True, "ok"))
    for key in (*probe.ARM_KEYS, probe.SEAM_KEY):
        monkeypatch.delenv(key, raising=False)
    # #10996: the OFF arm's value gate is EXPLICIT — an unset default-ON switch is
    # refused by the driver now, and that refusal would pre-empt the floor refusal
    # this test is about.
    monkeypatch.setenv("TORTOISE_VALUE_GATE", "0")
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:@127.0.0.1:1/tortoise_test")
    with pytest.raises(SystemExit) as refused:
        probe.main(["--graph", "g", "--out", str(tmp_path / "off.json")])
    assert "REFUSED" in str(refused.value), refused.value
    assert "e1_min_before_share" in str(refused.value), refused.value
    assert not (tmp_path / "off.json").exists(), (
        "a refused run writes no record")


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



# ── the share is a FRACTION of the counts printed beside it (#10610 review P2) ───────────────

#: The exact spelling `on-arm.log` writes on its `e1-aux` census line. The share is
#: read off that one line, so the mutation below edits the ONE place the driver reads.
_ON_ARM_SHARE_TOKEN = "share=0.21311475409836064"


@pytest.mark.parametrize("arm", ["e1-paired-off-arm-1.json",
                                 "e1-paired-off-arm-2.json"])
def test_a_before_arm_share_must_be_the_fraction_its_counts_give(arm, tmp_path,
                                                                capsys):
    """The share is not a TRUSTED TOKEN: it is the fraction of its own counts.

    `share` is the only operand E1's bar has, and every producer writes it as the
    float division of the two counts printed on the same line — so all three
    inputs for `share == bare_definite / objects` are already in hand wherever it
    is read, and nothing compared them. Editing ONLY `share` in the two OFF records scored
    both pairings PASS on a share the records' own counts (28/78 and 39/69)
    contradict, which flipped E1's aggregate from REFUSED to a decided PASS and
    wrote the row GREEN off the edit — the row `#9889` is waiting on.

    Each arm is mutated ALONE because the reconciliation lives at two sites (the
    before reader and the after reader below); a fix applied to one cannot stay
    green on the other.

    Non-vacuity: at the pre-fix head this bundle scores (measured: the shipped
    `e1-e2-scores.json` is exactly what `score()` writes), so the `pytest.raises`
    arm reds without the check.
    """
    repo = _bundle(tmp_path)
    path = repo / "docs/evidence/9889" / arm
    record = json.loads(path.read_text())
    assert record["share"] == record["bare_definite"] / record["objects"]
    record["share"] = 0.90                    # the counts are NOT touched
    path.write_text(json.dumps(record))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    err = capsys.readouterr().err
    assert "but its own counts" in err, err
    assert "score a number nothing in it measured" in err, err

    # …and the shipped record still scores, so the arm discriminates an edited
    # share from the unreconciled-but-consistent one that ships.
    record["share"] = record["bare_definite"] / record["objects"]
    path.write_text(json.dumps(record))
    assert scores_probe.score(repo)["e1"]["outcome"] == "REFUSED"


def test_the_after_arm_share_must_be_the_fraction_its_counts_give(tmp_path,
                                                                 capsys):
    """The same identity on the AFTER arm — the cheap half to forge.

    `on-arm.log` prints `objects_in_graph=61 bare_definite=13
    share=0.21311475409836064` on ONE line, and `13/61` is that float exactly. A
    log whose share moved while its counts stood is the after arm scored on a
    number the log does not carry, and it is the arm a false GREEN is cheapest to
    forge: `share` alone decides `after <= multiplier x before`.

    Non-vacuity: at the pre-fix head the edited log scores and reports the edited
    share as the after arm's reading.
    """
    repo = _bundle(tmp_path)
    log = repo / "docs/evidence/9889/on-arm.log"
    text = log.read_text()
    assert text.count(_ON_ARM_SHARE_TOKEN) == 1, text.count(_ON_ARM_SHARE_TOKEN)
    log.write_text(text.replace(_ON_ARM_SHARE_TOKEN, "share=0.9"))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    err = capsys.readouterr().err
    assert "but its own counts (13 of 61)" in err, err

    log.write_text(text)
    assert scores_probe.score(repo)["e1"]["outcome"] == "REFUSED"


@pytest.mark.parametrize("token", ["share=nan", "share=inf", "share=-inf"])
def test_a_non_finite_share_is_refused_rather_than_scored(token, tmp_path, capsys):
    """A share that is not a NUMBER must refuse — `abs(nan - implied) > 1e-9` is False.

    #10610 review at `e079c8dd` (P3, and a FAIL-OPEN). `share=nan` parses fine from the
    log, and EVERY comparison against NaN is False — so the reconciliation one test
    above passed it and the probe wrote a DECIDED record (`outcome: FAIL`,
    `decidable: true`) off a share that is not a number. Measured on `on-arm.log` with
    the share set to `nan`: exit 0, a record written, both pairings FAIL, ratio `nan`.
    `inf` refused only by luck (its comparison happens to be True), which is not a rule.

    The refusal is now a finiteness check BEFORE the comparison, so all three forms go
    the same way — and the shipped log still scores, so this leg discriminates a
    non-finite share from the finite one that ships.

    Non-vacuity: at the pre-fix head every case here scores instead of refusing.
    """
    repo = _bundle(tmp_path)
    log = repo / "docs/evidence/9889/on-arm.log"
    text = log.read_text()
    assert text.count(_ON_ARM_SHARE_TOKEN) == 1, text.count(_ON_ARM_SHARE_TOKEN)
    log.write_text(text.replace(_ON_ARM_SHARE_TOKEN, token))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    err = capsys.readouterr().err
    assert "not finite" in err, err

    log.write_text(text)
    assert scores_probe.score(repo)["e1"]["outcome"] == "REFUSED"


# ── the falsifier must DECLARE the injection it certifies (#10610 review P2) ─────────────────

@pytest.mark.parametrize("declared", [False, None, "absent", "False"])
def test_the_falsifier_must_declare_the_injection_it_is_credited_for(declared,
                                                                    tmp_path,
                                                                    capsys):
    """A liveness certificate may not be minted from a block declaring NO injection.

    `e2a-value-units-falsifier.json` carries `injected_anchor` precisely to say
    which reading a block is (`as_written: false`, `with_anchor: true`), and
    nothing read it: the liveness proof was a COUNT equality
    (`value_survived == value_units`), so a scored block declaring
    `injected_anchor: false` with 14/14 was accepted — the driver certifying "the
    counter is shown to move" on a block that states, in the record's own words,
    that it ran no injection. That is the inert-counter class the refusal above
    it is written for, read one field short.

    `False`, `None`, an ABSENT key and the STRING `"False"` are each mutated alone.
    The first three are refused by `is not True` and by a truthiness test alike — so
    they prove the guard REFUSES a non-declaration, not that it is `is not True` —
    and the fourth is the case that actually tells the two forms apart: a truthiness
    test accepts `"False"` (a non-empty string is truthy), `is not True` refuses it.
    Measured at `e079c8dd`: with the guard rewritten as
    `if not with_anchor.get("injected_anchor")`, the three original cases still passed
    and only the string case reds. A permissive default is not an assertion, and
    neither is a parametrization whose members all agree.

    Non-vacuity: at the pre-fix head this bundle scores, reporting the 14/14 from a
    block that declares no injection as the falsifier reading.
    """
    repo = _bundle(tmp_path)
    path = repo / "docs/evidence/9889/e2a-value-units-falsifier.json"
    record = json.loads(path.read_text())
    assert record["with_anchor"]["injected_anchor"] is True
    if declared == "absent":
        record["with_anchor"].pop("injected_anchor")
    else:
        record["with_anchor"]["injected_anchor"] = declared
    path.write_text(json.dumps(record))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    err = capsys.readouterr().err
    assert "does not declare an injection" in err, err
    assert "moved because an anchor was injected" in err, err

    # …and the declared-true block that ships still scores — the arm discriminates
    # a block declaring no injection from the injected one, rather than refusing
    # the whole falsifier form.
    record["with_anchor"]["injected_anchor"] = True
    path.write_text(json.dumps(record))
    assert scores_probe.score(repo)["e2"]["outcome"] == "FAIL"


# ── the head prefix is the one the PRODUCERS write (#10610 review P3) ───────────────────────

def test_a_graph_naming_the_head_under_the_producers_eight_chars_scores(tmp_path):
    """A record reproduced with a producer's OWN documented command must score.

    The driver required NINE hex characters (`after["head"][:9]`), while every
    producer documents and writes EIGHT: `e1_paired_arm.py`'s usage is
    `--graph capstone_off_<head8>_<epoch>`, `e2a_value_units.py`'s and
    `e1_gate_drops.py`'s are `capstone_measure_<head8>_<epoch>`, and
    `capstone_measure.py` builds its own default as
    `f"capstone_measure_{head[:8]}_{int(time.time())}"`. So reproducing this
    record with the documented command produced
    `capstone_off_54922a80_<epoch>`, which the driver refused with *"does not name
    head 54922a800"* — a refusal that blames a correctly-named graph. The
    committed artifacts happen to carry nine (`capstone_off_54922a800_…`), which
    is why only the reproduction path failed.

    The check now compares the SHORTER prefix, so both spellings score, and a graph
    naming a DIFFERENT head is still refused (pinned by the sibling graph pins).

    Non-vacuity: at the pre-fix head every `score()` call below exits 2.
    """
    repo = _bundle(tmp_path)
    head = next(ln.split()[-1] for ln in
                (repo / "docs/evidence/9889/on-arm.log").read_text().splitlines()
                if ln.startswith("# head: "))
    declared = (_REPO / "docs/evidence/7949-capstone/probes/capstone_measure.py"
                ).read_text()
    assert 'capstone_measure_{head[:8]}_' in declared, (
        "the probe no longer builds its own graph from head[:8] — this pin's "
        "claim about the producers is stale")
    # A distinct EPOCH per arm: the two before artifacts must be two RUNS, so
    # spelling them with one stamp would trip the duplicate-run guard and red this
    # pin on a defect that is not the one it is about.
    for arm, stamp in (("e1-paired-off-arm-1.json", 1793378986),
                       ("e1-paired-off-arm-2.json", 1793379347)):
        path = repo / "docs/evidence/9889" / arm
        record = json.loads(path.read_text())
        record["graph"] = f"capstone_off_{head[:8]}_{stamp}"   # the DOCUMENTED form
        assert head[:9] not in record["graph"]     # …nine would fail, which was the bug
        path.write_text(json.dumps(record))
    assert scores_probe.score(repo)["e1"]["outcome"] == "REFUSED"

    # …and a graph naming a DIFFERENT head is still refused, so the shorter prefix
    # did not become no prefix at all.
    path = repo / "docs/evidence/9889/e1-paired-off-arm-1.json"
    record = json.loads(path.read_text())
    record["graph"] = "capstone_off_00000000_1793378986"
    path.write_text(json.dumps(record))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value


# ── the last unguarded count, and the switch that stringified (#10610 review at `2ffc5db19`) ──

@pytest.mark.parametrize("value", ["Infinity", "-Infinity", "NaN", "61.5",
                                   '"61"', "true", "null", "62", "-1"])
def test_gate_drops_total_is_a_count_or_a_refusal(value, tmp_path, capsys):
    """P2: `drops_total` is the ONE count the record emitted without `_count`.

    Every sibling count passes `_count` (`objects`/`bare_definite` in the head loop,
    E2a's denominators, `e1b real_facts`), and `predicate_alive` is `is not True`
    -checked — but the gate-drops block emitted `drops_total` as a bare `.get()`.
    So it was the one count a caller could set to `Infinity` (or a string, or a
    negative) and still get a RECORD back. Measured at this head, one arm per value
    below: each scored and exited 0, and `Infinity` wrote a record that is not valid
    STRICT JSON (`json.loads(..., parse_constant=… )` rejects it; the record is read
    by tools that do).

    Not cosmetic either: `capstone_measure.py` reads this field into
    `gate_drops_total` and renders it into the scored E1 note as
    `reports value_gate drops <N> of <M> Objects with its positive controls alive`
    — a mechanism claim nothing measured. The ceiling is the same run's `objects`
    (`e1_gate_drops.py`'s `measure` writes `drops_total` as `len(drops)` over a dict keyed by object name).

    Non-vacuity, measured rather than assumed: ALL NINE arms exit 0 at this head's
    predecessor and exit 2 here. The two JSON-literal arms are `true`/`null` and NOT
    `True`/`None` — the uppercase spellings make the DOCUMENT invalid JSON, so they
    would exercise the parser rather than the coercion and would pass at BOTH heads,
    which is a pin that discriminates nothing (a first cut of this pin did exactly
    that, and the pre-fix run is what caught it).
    """
    repo = _bundle(tmp_path / "drops")
    path = repo / "docs/evidence/9889/e1-gate-drops.json"
    text = path.read_text()
    record = json.loads(text)
    assert record["drops_total"] == 0, (
        "the SHIPPED gate-drops record no longer reads 0 drops — re-derive this pin")
    # Splice the RAW token, so the non-standard constants (`Infinity`, `NaN`) reach
    # the reader instead of being rejected by `json.dumps` on the way out.
    record["drops_total"] = "__DROPSTOTAL__"
    path.write_text(json.dumps(record).replace('"__DROPSTOTAL__"', value))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    err = capsys.readouterr().err
    assert "drops_total" in err, err

    # …and the shipped 0 still scores, so the guard refuses the edited count rather
    # than the whole record.
    path.write_text(text)
    # ⛔ Asserted against the ACTUAL outcome, not a membership test: every return
    # value `capstone_measure.outcome()` can produce satisfies
    # `in ("REFUSED", "PASS", "FAIL")`, so a tautology there would only prove
    # `score()` did not raise — the over-block half of this pin would be no pin
    # at all (#10610 review at `05761cf29`, P3; same class fixed at `bde94f89d`).
    assert scores_probe.score(repo)["e1"]["outcome"] == "REFUSED"


@pytest.mark.parametrize("value", ["[]", '{"a": 1}', "[1, 2]"])
def test_a_before_arm_switch_value_of_another_type_is_refused(value, tmp_path, capsys):
    """P3: `_is_truthy` stringifies, so a list or object certified a before arm.

    `_is_truthy` is `str(raw).strip().lower() in _TRUTHY`, so `[]` and `{"a": 1}`
    both stringify to something outside the truthy set and read FALSY — and the
    before-arm guard only refuses a TRUTHY value. A non-string switch therefore
    passed as "the pass was OFF", which is the one thing the comment above it says
    the guard establishes: the arm's provenance is the environment it was measured
    in, and a list is not an environment value.

    Measured at this head's predecessor: setting arm 1's `TORTOISE_VET` to `[]` and
    to `{"a": 1}` each exited 0 with a byte-identical score. No producer writes
    either shape (`e1_paired_arm.py` writes `os.environ` strings and `null`), so
    both are edited provenance. Non-vacuity: all three arms exit 0 pre-fix, exit 2
    here.
    """
    repo = _bundle(tmp_path / "switch")
    path = repo / "docs/evidence/9889/e1-paired-off-arm-1.json"
    text = path.read_text()
    record = json.loads(text)
    assert record["arm"]["TORTOISE_VET"] is None     # the shipped arm is OFF
    record["arm"]["TORTOISE_VET"] = "__SWITCH__"
    path.write_text(json.dumps(record).replace('"__SWITCH__"', value))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    err = capsys.readouterr().err
    assert "which is not a switch value" in err, err

    # …and the shipped `null` still scores.
    path.write_text(text)
    # ⛔ Asserted against the ACTUAL outcome, not a membership test: every return
    # value `capstone_measure.outcome()` can produce satisfies
    # `in ("REFUSED", "PASS", "FAIL")`, so a tautology there would only prove
    # `score()` did not raise — the over-block half of this pin would be no pin
    # at all (#10610 review at `05761cf29`, P3; same class fixed at `bde94f89d`).
    assert scores_probe.score(repo)["e1"]["outcome"] == "REFUSED"


# ── the drop census, and a bar that binds nothing (#10610 review at `6e28a20e1f`) ─────────────

def test_drops_total_must_equal_its_own_drop_census(tmp_path, capsys):
    """P2: `drops_total` was BOUNDED by the Object population, never reconciled.

    `_count(..., ceiling=gate_objects)` refuses a total above the 61-Object
    population, which is necessary and not sufficient: the SAME record carries the
    drop census itself (`e1_gate_drops.py`'s `measure` writes `value_gate_drops` as a
    name-keyed dict and `drops_total` as `len(drops)` beside it) and nothing read it — so
    the field was a trusted integer anywhere inside `[0, objects]`.

    Measured at the pre-fix head, on a copied bundle whose shipped pair is
    `value_gate_drops: {} / drops_total: 0`: setting `drops_total: 61` (equal to the
    ceiling, while `62` is refused) exited 0 and emitted `drops_total: 61`. That is
    the harm the bound exists to prevent, because `capstone_measure.py` renders the
    number into the scored E1 note as `reports value_gate drops 61 of 61 Objects
    with its positive controls alive` — a mechanism claim the artifact contradicts.

    Non-vacuity: measured, exit 0 pre-fix and exit 2 here; the shipped `{}`/`0` pair
    still scores. TWO arms, because the fix has two directions and only the EQUALITY
    check can see either of them — a ceiling cannot, in the direction below it:

    * a total ABOVE the census (`61` against the shipped empty census) is refused by
      the EQUALITY check (`drops_total != len(census)`), NOT by a ceiling sized to
      the census — `_count(ceiling=gate_objects)` bounds it by the 61-Object
      population, and 61 is inside that bound. ⛔ The ceiling is deliberately NOT
      `len(census)`: that was the P1 at `05761cf29`, where a census is
      ATTACKER-SIZED, so it bound nothing while DROPPING the population bound
      (62 and 100 fabricated keys were ACCEPTED beside `objects: 61`). A lane that
      "restores" `ceiling=len(gate_census)` here does NOT by itself re-open that
      forgery — the `len(census) > objects` check and the `object_names` membership
      check already refuse those censuses — and saying so would be the same
      claim-outruns-the-code defect this file exists to catch. What the restore does
      is move ARM 1's refusal onto `_count`, which is why the assertion below names
      the EQUALITY guard's own message;
    * a total BELOW the census (`0` against a one-name census) passes every bound
      and is refused only by the equality check — the direction a ceiling can never
      see, and the one that keeps a real drop from vanishing out of the note.
    """
    repo = _bundle(tmp_path / "census")
    path = repo / "docs/evidence/9889/e1-gate-drops.json"
    text = path.read_text()
    record = json.loads(text)
    assert record["value_gate_drops"] == {} and record["drops_total"] == 0, (
        "the SHIPPED gate record's drop census is not `{}`/0 — re-derive this pin")

    # ARM 1 — the reviewer's measurement: a total inside the OBJECT population but
    # outside the drop census. It was `61` of 61 Objects, emitted and exit 0.
    record["drops_total"] = record["objects"]
    path.write_text(json.dumps(record))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    # ⛔ The EQUALITY check is the one that must refuse this, and the assertion says
    # so by quoting its message: `_count` also names `drops_total` in its own
    # refusal, so a bare `"drops_total" in err` stayed green when the ceiling was
    # `len(gate_census)`. MEASURED, with that mutant in place: this arm then refuses
    # through `_count` (`carries drops_total=61 of a population of 0`) while the
    # fabricated-census forgeries stay refused by the `len(census) > objects` check
    # — so the restore is a PIN defect and a docstring defect, not a fresh
    # fail-open, and the assertion's job is to keep the pin pointing at the guard
    # that actually binds here.
    assert "value_gate_drops` census holds 0" in capsys.readouterr().err, (
        "a drops_total above the census was not refused by the EQUALITY check — "
        "a refusal from `_count`'s population message means the ceiling was "
        "re-sized to the census, which is the attacker-sized bound "
        "`05761cf29` removed")

    # ARM 2 — the opposite direction, which only the equality check can refuse: one
    # REAL Object in the census, zero claimed in the total. The name must be one of
    # this run's `object_names`, or the census-identity arm added at `05761cf29`
    # refuses it first and this arm would no longer be about the equality check.
    record["value_gate_drops"] = {record["object_names"][0]: "value.identifier_only.path"}
    record["drops_total"] = 0
    path.write_text(json.dumps(record))
    with pytest.raises(SystemExit) as refused2:
        scores_probe.score(repo)
    assert refused2.value.code == 2, refused2.value
    err = capsys.readouterr().err
    assert "census holds 1" in err, err

    # …and the shipped pair still scores, so the guard refuses an edited count
    # rather than the field.
    path.write_text(text)
    # ⛔ Asserted against the ACTUAL outcome, not a membership test: every return
    # value `capstone_measure.outcome()` can produce satisfies
    # `in ("REFUSED", "PASS", "FAIL")`, so a tautology there would only prove
    # `score()` did not raise — the over-block half of this pin would be no pin
    # at all (#10610 review at `05761cf29`, P3; same class fixed at `bde94f89d`).
    assert scores_probe.score(repo)["e1"]["outcome"] == "REFUSED"


def test_a_drop_census_that_is_not_an_object_is_refused(tmp_path, capsys):
    """The census is now READ, so its shape matters: a non-object is a refusal.

    `_object_block` is the helper `with_anchor`/`as_written` already go through —
    `(value or {})` is not a guard, because a non-empty string is truthy and hands
    the value on to a `.get` that raises `AttributeError` (exit 1). Reading this
    field for the first time is what makes that relevant at this site.
    """
    repo = _bundle(tmp_path / "census-shape")
    path = repo / "docs/evidence/9889/e1-gate-drops.json"
    text = path.read_text()
    record = json.loads(text)
    record["value_gate_drops"] = "none"
    path.write_text(json.dumps(record))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value            # NOT exit 1/AttributeError
    err = capsys.readouterr().err
    assert "value_gate_drops" in err, err

    path.write_text(text)
    # ⛔ Asserted against the ACTUAL outcome, not a membership test: every return
    # value `capstone_measure.outcome()` can produce satisfies
    # `in ("REFUSED", "PASS", "FAIL")`, so a tautology there would only prove
    # `score()` did not raise — the over-block half of this pin would be no pin
    # at all (#10610 review at `05761cf29`, P3; same class fixed at `bde94f89d`).
    assert scores_probe.score(repo)["e1"]["outcome"] == "REFUSED"


def _prereg_with_threshold(tmp_path: Path, bar: str, new: str) -> Path:
    """A bundle whose frozen pre-registration states `bar`'s threshold as `new`."""
    repo = _bundle(tmp_path / f"bar-{bar}")
    path = repo / "docs/evidence/7949-capstone/preregistration.json"
    prereg = json.loads(path.read_text())
    hits = [c for c in prereg["criteria"] if c["id"] == bar]
    assert len(hits) == 1, f"preregistration no longer carries exactly one {bar} row"
    hits[0]["threshold"] = new
    path.write_text(json.dumps(prereg))
    return repo


@pytest.mark.parametrize("bar,new", [
    # a COUNT bar: `n == 0` never fails, `n > m` never passes — neither binds
    ("E2a", "`>= 0 of 14`"),
    ("E2a", "`>= 20 of 14`"),
    ("E1b", "`>= 0 of 90`"),
    # E2b is a SHARE over surviving units, so `t >= 1.0` accepts every reading
    ("E2b", "`<= 1.0`"),
    # E1 is a REDUCTION: at `m >= 1` the null result satisfies it
    ("E1", "`after_share <= 1.0 × before_share`"),
    ("E1", "`after_share <= 100 × before_share`"),
])
def test_a_frozen_bar_that_binds_nothing_is_refused(bar, new, tmp_path, capsys):
    """P2: the `criteria` bars were parse-checked, never BINDING-checked.

    Three statements above them, `denominator_floors` got a positivity arm for
    exactly this reason — *"a floor of zero or less binds nothing: … every reading
    would clear it and the guard it feeds would pass everything"*. The four bars are
    read from the SAME frozen record one statement later and only E2a's `of` was
    reconciled, so each of these edits moved a VERDICT:

        E1  `<= 0.50 ×` -> `<= 100 ×`   exit 0, E1=PASS, decided=True (was REFUSED)
        E2a `>= 13 of 14` -> `>= 0 of 14` exit 0, E2=PASS (its own detail: 8/14)
        E2b `<= 0.05` -> `<= 1.0`       exit 0, accepted

    The rules are provable from each bar's population rather than a policy about
    how strict a pre-registration should be: a `>= n of m` count binds iff
    `0 < n <= m`; E2b's share is in `[0, 1]`, so it binds iff `0 <= t < 1`; E1's
    multiplier is relative and binds iff `0 <= m < 1` (at `m == 0` only a zero
    after-share passes, so the strict end of the window is INCLUSIVE, exactly as
    E2b's is).

    Non-vacuity: measured, all six exit 0 at the pre-fix head and exit 2 here. The
    SHIPPED bars (0.50, 90/90, 13/14, 0.05) are all inside those windows, which the
    byte-pin above proves independently — that is the over-block check.
    """
    repo = _prereg_with_threshold(tmp_path, bar, new)
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    err = capsys.readouterr().err
    assert f"criteria[{bar}]" in err, err

    # …and the SHIPPED bar for that same row still scores, so the rule refuses the
    # inert value rather than the row.
    shipped = _bundle(tmp_path / f"shipped-{bar}")
    # ⛔ Asserted against the ACTUAL outcome, not a membership test: every return
    # value `capstone_measure.outcome()` can produce satisfies
    # `in ("REFUSED", "PASS", "FAIL")`, so a tautology there would only prove
    # `score()` did not raise — the over-block half of this pin would be no pin
    # at all (#10610 review at `05761cf29`, P3; same class fixed at `bde94f89d`).
    assert scores_probe.score(shipped)["e1"]["outcome"] == "REFUSED"


def test_a_forged_drop_census_cannot_size_its_own_ceiling(tmp_path, capsys):
    """P1: the census must not size the bound it is checked against.

    The first cut of the census fix passed `ceiling=len(gate_census)`: the census is
    ATTACKER-SIZED, so that ceiling bound nothing while DROPPING the population bound
    the previous head had. Measured at that head, with a matching `drops_total`,
    **62 and 100 fabricated keys were ACCEPTED** and the record emitted
    `drops_total: 100` beside `objects: 61` — a numerator 1.64x its own population,
    which is the class `_count(ceiling=…)` exists for (E1b's `real_facts=100000`
    beside `bar=90 of 90` was a finding earlier in this PR).

    Four checks now hold the census to the record's OTHER statements, so it can no
    longer vouch for itself: a census larger than `objects`; a key that is not one of
    the run's `object_names`; a non-string `rule_id`; and
    `drops_among_bare_definite` — the record's second statement of the same drops,
    written by the producer on every run whose Objects census is non-empty and
    read by nothing before this.
    """
    repo = _bundle(tmp_path / "forged")
    path = repo / "docs/evidence/9889/e1-gate-drops.json"
    text = path.read_text()
    record = json.loads(text)

    # ARM 1 — the P1 itself: a census big enough to carry the bound past the population.
    for n in (62, 100):
        record["value_gate_drops"] = {f"forged-{i}": "r" for i in range(n)}
        record["drops_total"] = n
        path.write_text(json.dumps(record))
        with pytest.raises(SystemExit) as refused:
            scores_probe.score(repo)
        assert refused.value.code == 2, refused.value
        err = capsys.readouterr().err
        assert "cannot be larger than the population" in err, err

    # ARM 2 — a census that FITS but names an Object this run never wrote.
    record["value_gate_drops"] = {"not an object of this run": "r"}
    record["drops_total"] = 1
    path.write_text(json.dumps(record))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    err = capsys.readouterr().err
    assert "not one of this run's `object_names`" in err, err

    # ARM 3 — a real name, a value the producer cannot write.
    real = record["object_names"][0]
    record["value_gate_drops"] = {real: {"nested": 1}}
    record["drops_total"] = 1
    path.write_text(json.dumps(record))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    assert "the verdict's `rule_id` string" in capsys.readouterr().err

    # ARM 4 — the record's SECOND statement of the same drops, contradicted: a
    # bare-definite Object is dropped while `drops_among_bare_definite` says none was.
    bare = next(n for n in record["object_names"]
                if scores_probe._is_bare_definite(n))
    record["value_gate_drops"] = {bare: "r"}
    record["drops_total"] = 1
    record["drops_among_bare_definite"] = []
    path.write_text(json.dumps(record))
    with pytest.raises(SystemExit) as refused:
        scores_probe.score(repo)
    assert refused.value.code == 2, refused.value
    assert "drops_among_bare_definite" in capsys.readouterr().err

    # ARM 5 — OVER-BLOCK: a legitimate drop of one real Object, with the second
    # statement agreeing, must SCORE. Without this the arms above are satisfied by
    # refusing every non-empty census.
    record["value_gate_drops"] = {real: "value.identifier_only.path"}
    record["drops_total"] = 1
    record["drops_among_bare_definite"] = ([real]
                                           if scores_probe._is_bare_definite(real)
                                           else [])
    path.write_text(json.dumps(record))
    scored = scores_probe.score(repo)
    assert scored["gate_drops"]["drops_total"] == 1, scored["gate_drops"]

    # …and the shipped record still scores, byte-identically (checked by the
    # file's reproduction pin, which passes independently).
    path.write_text(text)
    assert scores_probe.score(repo)["e1"]["outcome"] == "REFUSED"


def test_a_MAXIMALLY_strict_E1_bar_is_accepted(tmp_path):
    """P3: `0 < m < 1` refused `0.0`, a bar that binds as hard as a bar can.

    The rule's own proof covers `m >= 1` (satisfied by the null result) and says
    nothing about `m == 0`: `after_share <= 0.0 x before_share` is satisfied only by a
    zero after-share, so it fails every non-zero reading. Measured at the pre-fix
    head — `0.0` REFUSED while the equally strict sibling E2b `<= 0` was ACCEPTED, an
    asymmetry between two rules that must agree at the strict end. The shipped 0.50
    sits inside the window either way, so this arm is over-block only.
    """
    repo = _prereg_with_threshold(tmp_path, "E1",
                                  "`after_share <= 0.0 × before_share`")
    # No exit: only the AFTER share is compared, and the rule must not refuse the bar.
    record = scores_probe.score(repo)
    assert record["e1"]["outcome"] in ("FAIL", "REFUSED"), record["e1"]["outcome"]

    # …and the strict-but-legal sibling on E2b is accepted for the same reason.
    repo2 = _prereg_with_threshold(tmp_path, "E2b", "`<= 0`")
    assert scores_probe.score(repo2)["e1"]["outcome"] == "REFUSED"


def test_the_rules_block_states_the_boundary_the_code_enforces():
    """P3: the block that JUSTIFIES E1's rule is prose, and nothing read it.

    The round at `52d7173d5` corrected this block's E1 bullet back to the inclusive
    window and corrected the same stale sentence in
    `test_a_frozen_bar_that_binds_nothing_is_refused`'s docstring — but a comment is
    unread by every pin in this file, so the drift can come straight back: the review's
    own diagnosis was that a lane taking `0 < m < 1` at its word "fixes" the
    enforcement back to `0 <` and re-opens the `0.0` over-block, with the suite green.
    This pin is the reading that was missing.

    The expected window is derived from the CODE (the AST of the guard comparison),
    never restated here, so it reds in BOTH directions: code inclusive-at-zero with
    prose that says `0 <`, and prose that claims the inclusive window while the code
    reverts to `0 <`.
    """
    source = SCORES_PROBE.read_text(encoding="utf-8")

    # The enforced window, read off the guard itself rather than copied into this test.
    guard = next((n for n in ast.walk(ast.parse(source))
                  if isinstance(n, ast.Compare)
                  and isinstance(n.left, ast.Constant) and n.left.value == 0
                  and len(n.ops) == 2
                  and isinstance(n.comparators[0], ast.Name)
                  and n.comparators[0].id == "multiplier"
                  and isinstance(n.comparators[-1], ast.Constant)
                  and n.comparators[-1].value == 1), None)
    assert guard is not None, (
        "INSTRUMENT FOUND NOTHING: no `0 <op> multiplier <op> 1` comparison in "
        f"{SCORES_PROBE.name} — the E1 bar rule this block justifies is gone or "
        "re-spelled, so re-derive what it enforces before trusting the prose.")
    inclusive_at_zero = isinstance(guard.ops[0], ast.LtE)

    # The bullet, wrap-joined: the block is a comment block, so its continuation
    # lines are the `#    ` lines directly under the `* E1 is a REDUCTION` marker.
    lines = source.splitlines()
    start = next((i for i, ln in enumerate(lines)
                  if "* E1 is a REDUCTION" in ln), None)
    assert start is not None, (
        "INSTRUMENT FOUND NOTHING: the rules block's `* E1 is a REDUCTION` bullet "
        "is gone — this pin has no subject on this tree.")
    block = [lines[start]]
    for ln in lines[start + 1:]:
        if not ln.startswith("    #    "):
            break
        block.append(ln)
    bullet = re.sub(r"\s+", " ", " ".join(
        re.sub(r"^\s*#\s*", "", ln) for ln in block))

    assert ("0 <= m < 1" in bullet) is inclusive_at_zero, (
        "the E1 bullet states the WRONG window for the code beside it: the guard is "
        f"`{'0 <= multiplier < 1' if inclusive_at_zero else '0 < multiplier < 1'}` "
        f"(inclusive_at_zero={inclusive_at_zero}) while the bullet reads {bullet!r}. "
        "`0 <` refuses `0.0`, which is MAXIMALLY strict rather than inert — see "
        "`test_a_MAXIMALLY_strict_E1_bar_is_accepted`.")
    assert ("0 < m < 1" in bullet) is not inclusive_at_zero, (
        "the E1 bullet argues BOTH sides of the boundary — it still carries the "
        "pre-fix window `0 < m < 1` beside the guard it justifies "
        f"(inclusive_at_zero={inclusive_at_zero}): {bullet!r}")


# ── the bundle's re-run recipe states the arm the PROBE now sets ──────────────
#
# `#10996` changed what `capstone_measure.measured_arm()` writes: the two OFF
# switches are set to an EXPLICIT `"0"` instead of being popped, because an unset
# `TORTOISE_VALUE_GATE` is now the ON arm. `docs/evidence/9889/README.md` was
# updated with its producer; this bundle's README was not, so the two bundles
# taught opposite contracts and this one still said the probe pins the switches
# "unset" — the exact pre-fix behaviour, and an invitation for the next lane to
# restore the pop. The committed log's `<unset>` is HISTORY (it predates the
# flip); the RECIPE must not reproduce it.

_RE_RUN_RECIPE_STALE_ARM = (
    "`TORTOISE_VALUE_GATE` **and** `TORTOISE_S2_DECISION_SEAM` unset",
    "pins TORTOISE_VALUE_GATE and TORTOISE_S2_DECISION_SEAM unset",
    "TORTOISE_VALUE_GATE=<unset> TORTOISE_S2_DECISION_SEAM=<unset>",
)


def test_the_re_run_recipe_states_the_arm_the_probe_actually_sets():
    """One contract for both bundles: OFF means `"0"`, not absence (#10996).

    Red-first by construction — all three phrases were present when this pin was
    written — and the failure it exists to catch is a READER following the recipe
    and measuring the ON arm under a log that declares OFF.
    """
    readme = README_DOC.read_text()

    for phrase in _RE_RUN_RECIPE_STALE_ARM:
        assert phrase not in readme, (
            f"the bundle's re-run recipe still says the probe pins the switches "
            f"UNSET ({phrase!r}) — since #10996 absence is the ON arm, so a "
            "reader following this recipe measures the treatment under the "
            "control's label. The committed log's `<unset>` is history; the "
            "recipe must say `=0`")

    assert 'to an EXPLICIT `"0"`' in readme, (
        "the README must STATE that capstone_measure pins the two OFF switches "
        "to an explicit \"0\" — a corrected recipe whose reason is not written "
        "down is the one the next lane reverts")


#: ⛔ THE FOURTH COPY (#11298 review, P2). The guard above scans the README; the
#: arm definition a re-run operator reads FIRST is the PROBE'S OWN module
#: docstring, and nothing read it. So the stale sentence survived the `#10996`
#: flip in the file that defines the arm: it still said ``TORTOISE_VALUE_GATE``
#: was "pinned UNSET" while ``measured_arm()`` sets an explicit ``"0"`` through
#: ``ARM_OFF``. A phrase list over one document pins that DOCUMENT, not the class.
_PROBE_DOCSTRING_STALE_ARM = (
    "pinned UNSET",
    "pinned unset",
    "pinned to UNSET",
    "pinned to an unset",
)


def test_the_probe_docstring_states_the_arm_the_probe_actually_sets():
    """The probe's own prose must not teach the pre-`#10996` arm.

    Red-first by construction: at the reviewed head the docstring read
    "``TORTOISE_VALUE_GATE`` is pinned UNSET", so this test FAILED before the fix
    and passes only once the prose states the explicit ``"0"`` pin AND names
    ``ARM_OFF`` — the constant that holds the truth — so the next lane that pops
    it reds here instead of in a re-take.
    """
    doc = ast.get_docstring(ast.parse(PROBE.read_text())) or ""
    flat = " ".join(doc.split())
    assert flat, (
        f"no module docstring resolved from {PROBE.name} — the derivation is "
        "broken, so this pin would pass over anything")

    for phrase in _PROBE_DOCSTRING_STALE_ARM:
        assert phrase not in flat, (
            f"the probe's module docstring still says the OFF switches are "
            f"{phrase!r} — since #10996 an ABSENT `TORTOISE_VALUE_GATE` IS the "
            "ON arm, so the doc describes the pre-fix behaviour while "
            "`measured_arm()` sets an explicit \"0\". A re-run operator reads "
            "THIS docstring first (the README guard above cannot see it)")

    assert 'pinned to an' in flat and '``"0"``' in flat and "ARM_OFF" in flat, (
        "the probe docstring must state the pin the code actually sets — an "
        "explicit ``\"0\"`` performed by ``ARM_OFF`` — or a reader cannot tell "
        "the current arm from the pre-#10996 one")


def test_the_control_arm_definition_names_the_frozen_records_divergence():
    """The frozen pre-registration and this bundle must not silently disagree.

    Review cycle 4 (P2): `PRE-REGISTRATION.md` §3 still defines CONTROL as "the
    selection flags unset … their default is OFF". Since `#10996` an unset
    `TORTOISE_VALUE_GATE` is the TREATMENT, so that bullet is the PRE-flip
    definition — and this bundle's README now says the opposite (`"0"`, not
    `<unset>`), leaving two definitions of one arm in the same bundle.

    The frozen record is NOT amended here: editing a registered arm definition
    after an evidence log exists is §8's owner decision, and the committed
    artifacts genuinely ran OFF at their pre-flip heads. What a lane owes is that
    the divergence is STATED where the arm is read — this pin holds the statement
    in place, and holds the frozen bullet itself un-rewritten, so neither side can
    drift into quiet agreement with the other.
    """
    bundle = (_REPO / "docs" / "evidence" / "7949-capstone")
    doc = (bundle / "E1-E2.md").read_text()
    prereg = (bundle / "PRE-REGISTRATION.md").read_text()

    for phrase in (
        "`PRE-REGISTRATION.md` §3 still DEFINES the control arm as the flags ABSENT",
        "definition is now the pre-`#10996` one",
        "left unamended here on purpose",
        "a re-take sets `TORTOISE_VALUE_GATE=0`",
    ):
        assert phrase in doc, (
            f"E1-E2.md no longer states the frozen record's divergence "
            f"({phrase!r}) — without it the bundle carries two definitions of "
            "the control arm and a reader takes whichever one they read first")

    assert "their default is OFF" in prereg, (
        "PRE-REGISTRATION.md §3 no longer carries the pre-#10996 CONTROL "
        "definition — if it WAS amended, this pin and the E1-E2.md disclosure "
        "must move together (and the amendment needs a §8 Deviations entry plus "
        "a re-cut `document_sha256`, which is the owner's call, not a silent edit)")

    # ⛔ The disclosure is inside a BLOCKQUOTE, and the paragraph it was added to
    # was mid-sentence here (review-cycle-4's staging caught it: the insertion
    # split "…even against an explicit" from "`TORTOISE_VET=0`…", leaving a
    # truncated claim above and an orphaned fragment below — merge-damage prose in
    # the one artifact this round exists to make readable). Both halves must stay
    # adjacent and quoted.
    assert ("> `TORTOISE_S2_DECISION_SEAM` would arm the pass even against an explicit\n"
            "> `TORTOISE_VET=0`") in doc, (
        "the arm-table paragraph was broken across the disclosure — the sentence "
        "ends mid-clause and a fragment resumes after the blockquote")
    assert ">\n> ⛔ **`PRE-REGISTRATION.md`" in doc, (
        "the disclosure must be a separate quoted paragraph, not wedged into a "
        "sentence")


def _call_sites(src: str) -> list[tuple[int, str]]:
    """Call sites of the three edge-creating verbs — TOKENS, not substrings.

    A regex cannot tell a call from a comment or a string that names the same verb, and
    `re` also cannot tell `create_operator(payload)` from a prose mention ending in a
    parenthesis. Both false readings were measured on this record's arm: a comment adding
    `# call create_operator(payload) to publish` claimed the record's "no producer"
    conclusion was false when nothing was called. `tokenize` separates NAME/OP from
    COMMENT/STRING by construction, so the arm only ever reports a real `NAME (` pair.

    A `def` is NOT a call site: `def create_operator(payload)` is the same NAME/`(` PAIR, and
    counting it told a fixer the record's conclusion was FALSE when only the function's own
    definition was present (measured) — the wrong diagnosis, since a definition produces
    nothing.
    """
    names = {"create_edge", "create_about_edge", "create_operator"}
    toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    out: list[tuple[int, str]] = []
    for i, (a_line, b_line) in enumerate(itertools.pairwise(toks)):
        if not (a_line.type == tokenize.NAME and a_line.string in names
                and b_line.type == tokenize.OP and b_line.string == "("):
            continue
        # ⛔ Indexed, not a `zip(toks, toks[1:], toks[2:])` triple: that form binds the verb to
        # `toks[1:]`, so a call at the FIRST token of the source was invisible (measured — the
        # helper answered `[]` for `create_operator(p)` while every later line was caught).
        prev = toks[i - 1] if i else None
        if prev is not None and prev.type == tokenize.NAME and prev.string == "def":
            continue
        out.append((a_line.start[0], a_line.string))
    return out


_CITE_QUALIFIER_PATTERN = (
    # ⛔ A RANGE CARRIES ITS QUALIFIER TOO. `:12944-12945` at `<sha>` is a base-qualified
    # cite in the spelling §2 ALREADY USES three times (`extractor_v2.py:11731-11732` at
    # `80c912244`, `:12302-12303` at `d69faaf38`, `sdk.py:16516-16530`), and without the
    # optional `(?:-\d+)?` tail the helper reported every one of them as UNQUALIFIED — a
    # false BLOCK of a conformant edit whose own message told the author to add the base
    # they had already added (measured by a fresh-context review at `f885a51663`).
    # The tail admits digits-dash-digits and NOTHING else: a bare `:12944-12945` still
    # yields no qualifier and still reds, so the range spelling cannot become a false
    # PASS — the qualifier is what this group captures, never the tail. ⛔ AND BOTH
    # ENDPOINTS ARE SITES: `_unqualified_number_cites` reads its sites from the same range
    # grammar, so a bare `:12943-12944` reds for 12944 too — a range whose TRAILING member
    # is the live line rots exactly like a bare cite of it, and matching only `:<n>` left
    # that half uncovered (measured by a fresh-context review at `35a53c4ccc`).
    # The bound is 40, not 24, because the base predicate both call sites use is
    # `[0-9a-f]{7,40}`: at `{0,24}` a full 40-char sha in the PARENTHESISED spelling never
    # matched `\s*[,)]` and was reported UNQUALIFIED — a false block that told the author to
    # add the base they had already written in the other spelling (measured, same review).
    r":(\d+)(?:-(\d+))?`?(?:\s*\bat\b\s*([^\s,;)\]]{0,40})|[^\S\n]*\([^\S\n]*([^\s,;)\]]{0,40})\s*[,)])"
)


def _cite_qualifier_matches(region: str) -> list[tuple[int, str, str]]:
    """`(start_offset, number, qualifier)` for every QUALIFIED `:NNN` cite in `region`.

    ⛔ THE ONE SCAN, so the classifier's list form and `_unqualified_number_cites`' offset form
    cannot drift apart. The offset is that of the `:` the qualifier belongs to, which is what
    lets a caller pair a base with the occurrence it QUALIFIES instead of with whichever cite
    happens to sit beside it. `_cite_qualifiers` used to call `re.findall` directly, so the one
    caller that needed positions had no way to get them and re-derived qualification from a
    forward window — the defect `_unqualified_number_cites` now avoids by construction.
    """
    out: list[tuple[int, str, str]] = []
    for m in re.finditer(_CITE_QUALIFIER_PATTERN, region):
        lead, trail, at_q, paren_q = m.group(1), m.group(2), m.group(3), m.group(4)
        # ⛔ `or ""`, because `finditer` yields `None` for a NON-PARTICIPATING group where the
        # `findall` this replaced yielded `""`. Without it, a degenerate qualifier that leaves
        # the `at` group empty and the parenthesis group absent (`:12944 at )`) raised
        # `AttributeError` out of BOTH readers instead of classifying as "no qualifier" — a
        # regression introduced by the offset split, measured by a fresh-context review at
        # `35a53c4ccc`, and fail-closed only by accident.
        q = (at_q or paren_q or "").strip("*`().,;")
        if q:
            # ⛔ BOTH ENDPOINTS OF A QUALIFIED RANGE ARE QUALIFIED, and both are emitted at the
            # SAME offset the SITES reader uses (`m.start()`, the `:`) — because that reader
            # already counts `:M-N` as a site for M AND for N. Reporting only `lead` here made
            # the two readers DISAGREE: `:12943-12944` at `<sha>` was counted as a site for 12944
            # by one and as unqualified by the other, so a conformant base-qualified range whose
            # live line is the TRAILING endpoint was reported UNQUALIFIED — the same false BLOCK
            # the range tail removed, with a diagnostic telling the author to add a base they had
            # already written (measured by a fresh-context review at `615bb14b31`).
            out.append((m.start(), lead, q))
            if trail:
                out.append((m.start(), trail, q))
    return out


def _cite_qualifiers(region: str) -> list[tuple[str, str]]:
    """`(number, qualifier)` for every `:NNN` cite in `region` that CARRIES one — both spellings.

    The record qualifies a cite two ways: `<n> at <base>` and, for a base it prints in
    parentheses, `<n> (<base>)`. An arm that parses only the `at` spelling is blind to
    `` `:13011` (HEAD) `` at all, and one that demands the qualifier be unbracketed reads the
    PERMITTED `` :12929 at `a1dff7cec8` `` as a violation while its message names the opposite
    defect (`[]`, "qualifier is not a base") — both measured on this block (#11519 review).
    Backticks, bold markers and a trailing sentence period are typography, not the claim, so
    they are stripped: the caller classifies the qualifier's VALUE, never its decoration.

    A region with no qualified cite returns `[]`, which a caller MUST read as "this arm would be
    inert here" — never as "there is nothing to check".
    """
    # ⛔ The `at` branch and the parenthesised branch are SEPARATE captures because they have
    # DIFFERENT terminators and DIFFERENT gaps:
    #   * an `at` cite's qualifier ends at a word boundary, and the cite may be WRAPPED — the
    #     record itself writes `` `:1154` `` at end-of-line with `` at **`80c912244`** `` on the
    #     next (measured at `:313`), so its gap is `\s*` and CROSSES a newline;
    #   * a parenthesised qualifier must CLOSE, and it must be on the cite's OWN line — its gap
    #     is `[^\S\n]*`, so an ordinary aside on the next line is not read as this cite's. It
    #     ends at `[,)]`, NOT at `\)` alone: an ANNOTATED parenthetical (`(HEAD, main)`,
    #     `(62248db88, see #11555)`) is the same claim with a footnote beside it, and demanding
    #     the token be immediately followed by `)` made those invisible — measured, and a live
    #     shape in that form passed green (#11517 review). `\s*[,)]` cannot step over a space,
    #     so `(see the note above)` and `(line 3)` are still NOT qualifiers.
    # Folding both into one capture group (as this line first did) made any `(...)` after a cite
    # a "qualifier": `:9021 (see the note above)` redded as `[('9021','see')]` and `(line 3)` as
    # `('9021','line')` — an ordinary sentence reported as "a LIVE shape came back" (#11517
    # review). ⛔ And barring the newline for BOTH branches (the first fix attempt) blinded the
    # `at` branch on exactly the wrapped typography this record uses — measured, and it is the
    # state the same review caught in the second round.
    # The PATTERN and the scan both live in `_cite_qualifier_matches` — kept as ONE scan so the
    # offset form this classifier does not need cannot drift from the one it does.
    return [(n, q) for _pos, n, q in _cite_qualifier_matches(region)]


def _unqualified_number_cites(region: str, number: str) -> list[int]:
    """Offsets of `:number` cites in `region` that carry NO base qualifier of their OWN.

    ⛔ THE BASE IS PAIRED WITH THE OCCURRENCE IT QUALIFIES, because both cheaper forms were
    measured wrong in a direction and neither is an acceptable trade:

      * a ±window ("a sha appears within 60 chars of the cite") is satisfied by a NEIGHBOURING
        cite's base — the exact false pass the `:12929` arm in this test documents (main moved
        `vet_enabled = (` and the window-shaped arms stayed green);
      * a region-wide deny-list (`f":{number}" not in region`) FORBIDS the repair the record's
        own landed policy prescribes — `:<number>` at `<sha>` (`#11555`) — so a CONFORMANT edit
        reds a required check, and the diagnostic sends the fixer to make the edit it blocks
        (`#11523` review). It also let two arms in one loop require and forbid the SAME string
        whenever the derived live number happens to equal the number the pair arm pins.

    One qualifier discharges ONE occurrence, in the two spellings `_cite_qualifiers` reads — so a
    bare re-stale printed beside a base-qualified cite of the same number still reds. ⛔ The
    FIRST implementation of this sentence did not honour it in the mirrored order: it ran the
    classifier over a FORWARD WINDOW from each site and then discharged `own[:count]`, so when a
    bare cite was printed BEFORE its base-qualified twin the bare one inherited the neighbour's
    base (silently discharged) and the QUALIFIED one was returned as the offender — measured,
    `` `:12944` bare, see `:12944` at `b3c2b1a` `` returned `[20]`, i.e. the diagnostic sent the
    fixer to edit the conformant cite. Qualification is now read from the cite's OWN start
    offset, so the pairing cannot be inherited. Returns `[]`
    when every occurrence is base-qualified, or when the region cites `number` NOWHERE (the
    inverted reading of this sentence — "or when the region cites `number` at all" — was the
    opposite of the code and is corrected here; measured by a fresh-context review at
    `f885a51663`).
    """
    # ⛔ SITES COME FROM THE SAME RANGE GRAMMAR the qualifier scan uses, so BOTH endpoints of
    # `:M-N` are sites. Matching only `:number` made a bare `:12943-12944` return `[]` for
    # 12944 — the trailing endpoint of a range is exactly as HEAD-relative as a bare cite of
    # it, so the arm was blind to half the spelling it exists to catch (measured by a
    # fresh-context review at `35a53c4ccc`).
    sites = [
        m.start()
        for m in re.finditer(r":(\d+)(?:-(\d+))?", region)
        if number in (m.group(1), m.group(2))
    ]
    if not sites:
        return []
    # ⛔ A BASE IS PAIRED WITH THE OCCURRENCE IT STARTS AT: a site is discharged only when a
    # hex qualifier BEGINS at that exact offset. Neither a forward window nor a count/slice can
    # express that, which is why both were removed rather than tuned.
    own = {
        pos for pos, n, q in _cite_qualifier_matches(region)
        if n == number and re.fullmatch(r"[0-9a-f]{7,40}", q)
    }
    return [pos for pos in sites if pos not in own]


def test_the_reconcile_cites_resolve_where_the_record_says_they_do():
    """`RECONCILE-A1-A2-A5.md`'s LIVE cites, which no sweep reads.

    Rule 3 of agent-infra#3442: this leg pins the record's LIVE cites — the four
    #11509 re-pointed (`extractor_v2.py:10815`->`:10891`, `sdk.py:17472`->`:17574`,
    `sdk.py:16414-16428`->`:16516-16530` x3, `probes/capstone_measure.py:2238-2243`
    ->`:2738-2740`, which was `:2726-2728` before main's #11517 landed +12 above it), `extractor_v2.py:1720` unchanged, the `create_operator`
    reproduction quote this PR re-derived (`:8938 :8956 :8963 :9343` ->
    `:9021 :9039 :9046 :9426` after the `f5c69f7478` base merge (+68 at #11449, then +15
    from this branch's own insertions), drifted unnoticed — the neighbouring
    #11376 did not move these lines), the `:12929` `vet_enabled` live-cite, the
    bundle's `hasPart`/`hierarchies` split, and the four further LIVE cites a fresh
    review found still unwatched (`pack_hierarchy.py:431`, the
    `probes/capstone_measure.py:275-283` window and the `sdk.py:17574` cite it carries
    INSIDE it, `identity_hold.py:125`, `value_gate.py:81`) — and, measured before
    this pin existed, re-staling ANY of them
    left every suite green: the record is in no `_BUNDLE_DOCS` entry and
    `git grep -ln RECONCILE-A1-A2-A5 -- tests/ ':!tests/test_7949_capstone_measure.py'`
    printed nothing at the base this pin was added — note the self-match, the same trap
    as the `hasPart` grep below: this file names the record, so an unexcluded search
    returns itself. The bare `RECONCILE` token matches 10 unrelated files
    (`_BILLING_RECONCILE_INFLIGHT` and friends), so the record's NAME, excluded, is the
    claim. ⚠️ No numeral is stated for the count: a typed count is this bundle's
    recurring defect, and every one written so far undercounted the cites a later
    commit added (`five` here went stale the moment the `create_operator` quote and
    the `vet_enabled` cite landed). The enumeration above is the claim; the asserts
    below are what bind it. A record whose whole thesis is
    that "a claim nothing re-checks can rot silently" cannot leave its own numbers
    unwatched.

    It is pinned HERE rather than by adding the record to `_BUNDLE_DOCS`, because
    the record also carries amendment paragraphs that quote numbers at an OLD base
    on purpose (`:8880` at `80c912244`, `:9433` at `d69faaf38`); sweeping the whole
    document would either red on those or need a historical marker plus its exact
    cite list, i.e. more pinned surface than the claims this PR touches. This leg
    binds only what the PR re-derived, and for THOSE cites it is discriminating in
    both directions: re-staling a cite in the RECORD reds it, and an insertion in
    the CODE above a cited line reds it too — every arm pins its cited line EXACTLY
    (a 3-line range pins both endpoints AND its middle; a longer one pins both
    endpoints, which is sufficient because an interior insertion moves the closing
    endpoint), never a window that a shift can slide inside.

    ⛔ SCOPE, stated so this docstring does not claim more than the arms bind: the record
    carries FURTHER live cites this leg does NOT pin — among them
    `pack_hierarchy.py:59`/`:78`/`:353`/`:447` and `extractor_v2.py:1154`, each stated once
    or twice and watched by nothing (measured: re-staling any of them leaves this file
    green). They are recorded on #11173, the open root for capstone cite drift, whose
    durable fix is putting RECONCILE in a sweep (`_BUNDLE_DOCS`) rather than growing this
    leg cite by cite. A reviewer who finds another one is finding a TRUE gap in the
    record's coverage — not a defect in the arms listed here, and not a reason to add a
    pin to this micro PR.
    """
    record = _REPO / "docs/evidence/7949-capstone/RECONCILE-A1-A2-A5.md"
    text = record.read_text(encoding="utf-8")
    sdk = (_REPO / "tortoise/sdk.py").read_text(encoding="utf-8").splitlines()
    ext = (_REPO / "tortoise/extractor_v2.py").read_text(encoding="utf-8").splitlines()
    probe = PROBE.read_text(encoding="utf-8").splitlines()
    pack_hierarchy = (_REPO / "tortoise/pack_hierarchy.py").read_text(
        encoding="utf-8").splitlines()
    identity_hold = (_REPO / "tortoise/identity_hold.py").read_text(
        encoding="utf-8").splitlines()
    value_gate = (_REPO / "tortoise/value_gate.py").read_text(
        encoding="utf-8").splitlines()

    # ⛔ The CALL-shaped arm comes FIRST, because it is the only one that can report the
    # true failure. A production `create_operator(` call necessarily contains the bare
    # token, so a token-scanning arm below would fire too — and an earlier revision's DID
    # fire first (measured), instructing a fixer to RE-DERIVE the record's line numbers:
    # the wrong repair, aimed at a staleness where the conclusion itself is false.
    #
    # ⛔ This check is TOKENIZED, not a regex, for THREE measured reasons — the first two
    # defective in opposite directions, the third a limitation of `re` itself:
    #   1. A dot-ANCHORED pattern (the record's own command shape) missed the unqualified
    #      `return create_operator(payload)` — a real call with no receiver.
    #   2. A dot-EXCLUDING lookbehind then missed the RECEIVER-qualified form
    #      (`sdk.create_edge(payload)`), i.e. it pinned the COMPLEMENT of the claim while
    #      its message asserted the claim.
    #   3. Any `re` arm also reads a COMMENT or a STRING that names the verb as a call, so
    #      a prose mention claimed the record's conclusion was FALSE when nothing was called.
    # `_call_sites` matches a NAME/`(` token PAIR, so it covers both receivers and the
    # unqualified form, and COMMENT/STRING tokens cannot match by construction.
    _calls = _call_sites("\n".join(ext) + "\n")
    assert _calls == [], (
        f"the record's load-bearing half is the CALL count (0) — a production call site "
        f"makes its 'no producer' conclusion FALSE, not merely stale (found {_calls}); this "
        "reds before the line-number arm below can misdirect the repair")
    # The helper's own two degenerate cases, so the detector cannot go inert silently: a call at the
    # FIRST token of the source (which a `zip(toks, toks[1:], toks[2:])` form missed — measured),
    # and a DEFINITION, which is not a call site.
    assert _call_sites("create_operator(payload)\n") == [(1, "create_operator")], (
        "`_call_sites` must see a call at the FIRST token of the source — a triple-zip over "
        "`(toks, toks[1:], toks[2:])` cannot, and answered `[]` there")
    assert _call_sites("def create_operator(payload):\n    pass\n") == [], (
        "a `def` is not a call site — counting it tells a fixer the record's conclusion is FALSE "
        "when only the function's own definition is present")
    # The four shapes the comment above claims the arm separates, each asserted rather than described:
    # the docstring's own two false readings (a receiver-qualified call missed by a dot-EXCLUDING
    # lookbehind, a comment read as a call) were UNBOUND — reintroducing either left the suite green
    # (measured), because the live tree has neither shape.
    assert _call_sites("sdk.create_edge(payload)\n") == [(1, "create_edge")], (
        "a RECEIVER-qualified call is a call site")
    assert _call_sites("return create_operator(x)\n") == [(1, "create_operator")], (
        "an UNQUALIFIED call is a call site")
    assert _call_sites("# call create_operator(payload)\n") == [], (
        "a COMMENT naming the verb is not a call site")
    assert _call_sites('_X = "create_operator(payload)"\n') == [], (
        "a STRING naming the verb is not a call site")
    # ⛔ `_cite_qualifiers`' own cases, asserted rather than described — the same idiom as
    # `_call_sites` above and for the same reason: a parser that silently stops matching is a
    # no-op gate that still reports green. Each case is a spelling measured on this PR's record or
    # named by its review, so the arm's coverage is a measurement and not a claim.
    assert _cite_qualifiers("`:13011 at HEAD`") == [("13011", "HEAD")], (
        "the `at`-word spelling, backtick-wrapped, is a qualified cite")
    assert _cite_qualifiers(":13011 at `HEAD`") == [("13011", "HEAD")], (
        "a BACKTICKED non-base qualifier is a qualified cite — main's `[\\w]` could not see it")
    assert _cite_qualifiers("`:13011` (HEAD)") == [("13011", "HEAD")], (
        "the parenthesised spelling carries no `at` at all — main's arm was blind to it")
    assert _cite_qualifiers(":12929 at a1dff7cec8, :12779 at 62248db88") == [
        ("12929", "a1dff7cec8"), ("12779", "62248db88")], (
        "the §5 block's own sentence: two cites, two BASE qualifiers, both classified")
    assert _cite_qualifiers("`:9343` (`62248db88`)") == [("9343", "62248db88")], (
        "a base in parentheses is still a BASE — the false block main's shape produced")
    assert _cite_qualifiers("`:12929` at\n**`a1dff7cec8`**") == [("12929", "a1dff7cec8")], (
        "§2's own spelling wraps the base in a newline and bold — typography, not the claim")
    assert _cite_qualifiers("the comment at :10906 and 40 lines") == [], (
        "a BARE cite has no qualifier — it is the grammar gap stated beside the arm, not a hit")
    assert _cite_qualifiers(":9021 (see the note above)") == [], (
        "an ordinary PARENTHESISED ASIDE after a cite is not a qualifier — only a CLOSED,"
        " single-token parenthetical is a parenthesised cite (#11517 review: 'see' was read"
        " as the qualifier and reported as a live shape)")
    assert _cite_qualifiers(":9021\n(entry one) -> nothing") == [], (
        "nor is a parenthetical on the NEXT line — the PARENTHESISED branch's gap cannot "
        "cross a newline")
    assert _cite_qualifiers(":13011\nat HEAD") == [("13011", "HEAD")], (
        "but the `at` branch's gap MUST cross one: the record wraps a base onto the next line "
        "(`:313` — `` `:1154` `` then `` at **`80c912244`** ``), and a rule that bars the "
        "newline for both branches is blind to a live shape written that way (measured)")
    assert _cite_qualifiers(":13011 (HEAD, main)") == [("13011", "HEAD")], (
        "an ANNOTATED parenthetical is the same claim with a footnote beside it — requiring "
        "the token to be followed immediately by `)` made `(HEAD, main)` invisible, i.e. a "
        "live shape passing green (#11517 review)")
    assert _cite_qualifiers(":9343 (62248db88, see #11555)") == [("9343", "62248db88")], (
        "and a base annotated the same way stays classified")
    # ⛔ `_unqualified_number_cites`' own cases — the arm §2 uses to forbid a HEAD-relative LIVE
    # number, asserted here for the same reason as the classifier above: it was a region-wide
    # `not in` until `#11523`'s review found it reds the ONE repair its own diagnostic prescribes.
    # Each case is a state that review named, so "a base-qualified cite passes" is a measurement.
    assert _unqualified_number_cites("`:12944` at `b3c2b1a`", "12944") == [], (
        "a cite pinned to a sha is the `#11555` repair — the region-wide ban redded it, i.e. a "
        "conformant edit failed a required check")
    assert _unqualified_number_cites("`:12944` (`b3c2b1a`)", "12944") == [], (
        "the parenthesised spelling of a base is the same repair, and it must pass too")
    assert len(_unqualified_number_cites("`:12944` (HEAD)", "12944")) == 1, (
        "a LIVE qualifier is not a base — `#11555` retired this shape, so it must still red")
    assert len(_unqualified_number_cites(
        "`:12944` at `a1dff7cec8` then `:12944` (HEAD)", "12944")) == 1, (
        "a base discharges ONE occurrence: a bare re-stale printed beside a base-qualified cite "
        "of the same number must not ride its base (the ±window false pass)")
    # ⛔ This case used to be `` _unqualified_number_cites("`:13011` at HEAD", "12944") == [] ``
    # — which returns at the `if not sites: return []` early return because the region cites
    # `number` NOWHERE, so it asserted the early return while its message claimed the PAIRING
    # property ("a neighbour's qualifier is not this cite's"). A case that cannot vary under a
    # mutation of the pairing logic is the dead weight this file deletes an arm for under its own
    # "an assertion that can never be the row that reds is not attribution" rule (fresh-context
    # review at `35a53c4ccc`). It now carries BOTH numbers, so a neighbour's base discharging this
    # cite's occurrence reds it — measured: shipped `[28]`, ±window mutant `[]`.
    _neighbour_base = "`:13011` at `b3c2b1a`, and `:12944` here"
    assert _unqualified_number_cites(_neighbour_base, "12944") == [
        _neighbour_base.index(":12944")], (
        "the arm names ITS number — a neighbour's qualifier is not this cite's: `:13011`'s base "
        "must not discharge `:12944`")
    assert _unqualified_number_cites("`:12943-12944`", "12944") == [1], (
        "⛔ THE TRAILING ENDPOINT OF A RANGE is as HEAD-relative as a bare cite, and reading "
        "sites from `:number` alone made it invisible — a bare `:12943-12944` returned `[]` "
        "for 12944 while `:12944-12945` redded, i.e. the arm covered half the spelling it "
        "exists to catch (measured at `35a53c4ccc`)")
    # ⛔ AND A QUALIFIED RANGE QUALIFIES BOTH OF ITS ENDPOINTS. The SITES reader counts both;
    # the DISCHARGE reader reported only the leading number, so a base-qualified range whose
    # live line is the TRAILING endpoint was reported UNQUALIFIED — a false BLOCK of an edit
    # that already wrote the base, in the spelling §2 uses (measured at `615bb14b31`).
    assert _unqualified_number_cites("`:12943-12944` at `b3c2b1a`", "12944") == [], (
        "a range's base covers its TRAILING endpoint — reporting it unqualified tells the "
        "author to add the base they already wrote")
    assert _unqualified_number_cites("`:12943-12944` at `b3c2b1a`", "12943") == [], (
        "and the LEADING endpoint of the same qualified range stays discharged")
    assert _unqualified_number_cites("`:12943-12944` (`b3c2b1a`)", "12944") == [], (
        "the parenthesised spelling of a range's base covers the trailing endpoint too")
    assert _unqualified_number_cites("`:12944-12945`", "12944") == [1], (
        "and the LEADING endpoint still reds when the range carries no base")
    assert _unqualified_number_cites(
        "`:12944` (5ef5805f0953c2cf0eb2338ca0ad0af4594ee70d)", "12944") == [], (
        "a FULL 40-CHAR base in the PARENTHESISED spelling is the same repair as the `at` "
        "spelling: the qualifier bound was 24 while the base predicate is `{7,40}`, so a "
        "40-char sha was reported UNQUALIFIED — the exact false block the range tail removed, "
        "in the other spelling")
    assert _cite_qualifiers(":12944 at )") == [], (
        "⛔ a DEGENERATE `at` (empty qualifier, no parenthetical) must classify as 'no "
        "qualifier', not raise: `finditer` yields `None` where `findall` yielded `""`, so the "
        "offset split briefly raised `AttributeError` out of both readers")
    assert _cite_qualifiers(":12944 at") == [], (
        "the same degenerate shape at end-of-region")
    assert _unqualified_number_cites("`:12944` bare, see `:12944` at `b3c2b1a`", "12944") == [1], (
        "⛔ THE MIRROR ORDER — a BARE cite printed BEFORE its base-qualified twin. Only the "
        "qualified-first order was asserted, so a forward window that let the bare cite inherit "
        "the neighbour's base went unnoticed while NAMING the qualified cite as the offender "
        "(measured: `[20]`, the conformant one). A diagnostic that sends the fixer to edit the "
        "cite it should accept is the same defect class this helper replaced the region-wide ban "
        "for, so both orders are pinned")

    # A1: #11205's channel is TRANSPORT ONLY — the schema line that spells the
    # predicate allowlist, and the comment saying nothing in `tortoise/` consumes it.
    # ⛔ RE-DERIVED for `#11002`'s rebase, not offset by hand: this branch inserts
    # above BOTH anchors (the G5 record at the module head and the `run_s1`
    # emphasis seam), so the schema line moved `:1720` → `:1735` and the comment
    # `:10891` → `:10906` — the same **+15** §10.15 records for the collector.
    # Measured off the MERGED tree (`grep -n 'hasPart' tortoise/extractor_v2.py`
    # → `:1735`, `grep -n 'nothing in this tree consumes'` → `:10906`).
    assert "`hasPart`" in text and "`tortoise/extractor_v2.py:1735`" in text, (
        "the record must cite the relations schema where `hasPart` is spelled")
    # ⛔ THE ADJACENCY IS DELIBERATELY NOT PINNED — recorded here, which is what the review of
    # the forward merge (#11519) asked for as the alternative to restoring it. Three forms were
    # measured, and each one that binds the mention to the cite in ONE assertion is a
    # false-BLOCK generator: side1's literal redded on an em-dash reword of the same clause;
    # `[^.\n]{0,80}` redded on `` `hasPart`, i.e. `cite` `` and on the reversed order
    # (`` the allowlist at `cite` includes `hasPart` ``), while still passing a two-CLAUSE
    # rewrite. `#11546` (de1e2c9b2) removed exactly this needle class by name — *"remaining
    # needles whose claim was a number or a token and whose tail was prose"*. main's two membership tests above
    # are that decision, this file follows it, and the claim they cannot express (mention and
    # cite about the same line) is left to a reader rather than to a predicate that misfires in
    # three of the four rewrites measured.
    # The A1 proof command must match BOTH shapes its sentence covers, and must stay on ONE line WITH
    # its pathspec. Two measured ways to pin the COMPLEMENT instead: a dot-ANCHORED `'\.(verb)\('`
    # exits 1 on an unqualified `return create_operator(payload)`, and a `[^[:alnum:]_.]` class
    # EXCLUDES the dot so it exits 1 on `sdk.create_edge(payload)` — the exact false reading the
    # tokenized arm below was written for. Wrapped, the command loses its pathspec and greps the whole
    # tree (measured: 133 hits, exit 0, where the record states no output). The pattern is READ OUT of
    # the record and proved on both samples, so a future tidy cannot re-install the complement.
    _a1_cmd = re.search(
        r"git grep -nE '([^']*create_edge[^']*)' tortoise/extractor_v2\.py", text)
    assert _a1_cmd is not None, (
        "§1 item 2 must print its A1 proof command on ONE line with its pathspec — split across two "
        "lines it drops `tortoise/extractor_v2.py` and greps the whole tree")
    _a1 = _a1_cmd.group(1)
    # ⚠️ Structural BEFORE behavioural: the class that decides both halves is the dotted one. A `[^…_.]`
    # class excludes the `.` that precedes a receiver, so it is blind to `sdk.create_edge(payload)`;
    # dropping the `(^|…)` alternation and anchoring on `\.` is blind to the bare call instead.
    assert "[^[:alnum:]_]" in _a1 and "[^[:alnum:]_.]" not in _a1, (
        f"the A1 proof command's class reads {_a1!r} — a dot-EXCLUDING class is blind to a "
        "receiver-qualified call, which is the false reading the tokenized arm exists to catch")
    # …and BEHAVIOUR, through `grep -E` itself: `re` does not speak POSIX classes, so proving this
    # pattern with `re` would prove nothing about the command the record hands the reader.
    _probe = Path(tempfile.mkdtemp()) / "a1_probe.py"
    _probe.write_text(
        "return create_operator(payload)\nsdk.create_edge(payload)\n", encoding="utf-8")
    _hit = subprocess.run(["grep", "-nE", _a1, str(_probe)], capture_output=True, text=True)
    assert _hit.returncode == 0 and len(_hit.stdout.splitlines()) == 2, (
        f"the A1 proof command matches {len(_hit.stdout.splitlines())} of the two call shapes (an "
        "UNQUALIFIED and a RECEIVER-qualified call) — it must match both, or it certifies a tree "
        f"that calls the verb; grep said {_hit.stdout!r} exit {_hit.returncode}")
    # ⛔ …and so is the §5 statement of the SAME command. The record prints it TWICE (its §1
    # item-2 quote and the §5 reproduction block), and the `re.search` above reads one of them —
    # so restoring the dot-excluding class in the OTHER copy left the pin GREEN (measured on the
    # previous round). Graded per occurrence, the rule the mention-count arm applies below; the
    # pathspec leg of the check stays §1-only because that is the one-line form the reader pastes.
    _all_a1 = re.findall(r"git grep -nE '([^']*create_edge[^']*)'", text)
    assert len(_all_a1) == 2, (
        "the record must state the A1 proof command exactly TWICE (§1 item 2 and the §5 "
        f"reproduction block) — found {len(_all_a1)}")
    assert all("[^[:alnum:]_]" in _c and "[^[:alnum:]_.]" not in _c for _c in _all_a1), (
        "EVERY statement of the A1 proof command must use the non-dot-excluding class — a "
        f"`[^[:alnum:]_.]` copy is blind to a receiver-qualified call (found {_all_a1})")
    assert "hasPart" in ext[1735 - 1] and '"predicate":' in ext[1735 - 1], (
        f"extractor_v2.py:1735 reads {ext[1735 - 1].strip()[:90]!r}, not the schema "
        "line that spells the predicate allowlist")
    assert "`extractor_v2.py:10906`" in text, (
        "the record must cite the comment that says nothing consumes the channel")
    # The SAME line is cited a SECOND time as a bare `:10906`, inside the
    # reproduction block's comment. A bare cite carries no file token, so the
    # prefixed arm above cannot see it: measured by re-staling it to `:10815` in
    # place, which left every arm True before this assert existed.
    # ⛔ Pinned as the BARE occurrence of the NUMBER, not as the sentence around it (#10891
    # is the claim; "the comment at" is one way of saying it). A literal needle redded on
    # `the comment on :10906` and on `` the comment at `:10906` `` — both correct, both
    # sending the reader to the live line (measured) — the same false-block this leg removed
    # for `both now read`, the `# §0/§2` heading and `MENTIONS`. The prose above carries the
    # PREFIXED form `extractor_v2.py:10906`; this is the only statement without a file token,
    # so exactly one bare occurrence is the claim.
    assert re.findall(r"(?<!extractor_v2\.py):10906", text) == [":10906"], (
        "the reproduction block must state the live number `:10906` exactly once as a BARE "
        "cite — re-stale it and this record's only reproduction path points at a dead line")
    assert "nothing in this tree consumes" in ext[10906 - 1], (
        f"extractor_v2.py:10906 reads {ext[10906 - 1].strip()[:90]!r}, not the "
        "comment stating nothing consumes `result[\"relations\"]`")

    # A1's environment block: the reproduction that states A1's producer claim — the
    # bare-token grep whose four `create_operator` mentions ARE its evidence that the
    # channel has no producer. It is pinned BOTH ways because a quoted number with
    # nothing re-checking it is the very rot this record is about: the quote must be the
    # live lines, and the live lines must be what the quote says. Measured on `main`
    # before this assert existed, the quote had drifted +68
    # (`:8938 :8956 :8963 :9343` vs live `:9021 :9039 :9046 :9426`) across two
    # merges and no suite noticed. The HISTORICAL siblings above it in the same block
    # (`de72cfd45` → `:8892 :8910 :8917 :9231`, `80c912244` → 0) are deliberately NOT
    # pinned: they are claims about those commits, and they verify there.
    #
    # The LIVE run is the LEADING `:NNNN` group, not the whole comment: #11519's landing
    # appends the PROVENANCE of the four numbers (`(`62248db88` printed :8938 …; #11449,
    # moved all four +68)`), which is a claim about two other bases and must not be read as
    # live. Pinning the whole tail pinned one PR's wording instead of the invariant, and
    # redded on a re-worded history while every live number was correct.
    _quote = re.findall(
        r"git grep -n 'create_operator' -- tortoise/extractor_v2\.py\s+# (.*)", text)
    assert len(_quote) == 1, (
        "the record must carry EXACTLY ONE `git grep -n 'create_operator' -- "
        f"tortoise/extractor_v2.py` reproduction (found {len(_quote)}) — a second one states "
        "numbers no arm below re-derives")
    _live_run = re.match(r"((?::\d+[ ]*)+)", _quote[0])
    assert _live_run is not None, (
        "the create_operator reproduction must open with the LIVE line numbers it quotes — "
        f"it reads {_quote[0][:70]!r}; re-derive them with `git grep -n 'create_operator' -- "
        "tortoise/extractor_v2.py`")
    assert _live_run.group(1).split() == [
        f":{i}" for i, line in enumerate(ext, 1) if "create_operator" in line], (
        "the record's create_operator reproduction must quote the LIVE line numbers, in the "
        f"tree's order ({_live_run.group(1).split()}) — re-derive them with "
        "`git grep -n 'create_operator' -- tortoise/extractor_v2.py`")
    _trailing = _quote[0][_live_run.end():].strip()
    assert not _trailing or re.match(r"^\(`[0-9a-f]{7,}` printed", _trailing), (
        "any number the create_operator quote carries BESIDES the live run must be introduced "
        "by a BASE-QUALIFIED parenthetical (`(\u0060<sha>\u0060 printed …)`) — a bare second "
        f"run reads as live while it is history (got {_trailing[:70]!r})")
    # §1 item 3's `0 hits` claim belongs to the EXCLUDED command AND to a named base. As first
    # written it hung on the UN-excluded grep (`docs/evidence/7949-capstone/`), which at
    # `4e6a3ce612bd^` returns this record's OWN lines (18 of them, measured) — so the claim was
    # false as written and nothing watched it. Measured: un-excluded at `4e6a3ce612bd^` → 18,
    # excluded there → 0/exit 1, at the record's own base `80c912244` → 0 both ways (`#10982`
    # landed after BOTH bases).
    # ⛔ Located by the NUMBER TOKEN alone, not by the sentence's leading word: `The **0
    # hits**` redded on `Its **0 hits**` — a reword that keeps the number and the emphasis —
    # with a diagnostic asserting the zero was not stated at all (measured, and false).
    _zero_at = text.find("**0 hits**")
    assert _zero_at > 0, (
        "§1 item 3 must state the pre-#10982 zero, base-qualified")
    assert ":!docs/evidence/7949-capstone/RECONCILE-A1-A2-A5.md" in text[max(0, _zero_at - 900):_zero_at], (
        "the `0 hits` claim must hang on the EXCLUDED command — the un-excluded one returns "
        "this record's own lines at the sha it names, so the claim would be false as written")
    assert "4e6a3ce612bd^" in text[_zero_at:_zero_at + 260] and \
        "80c912244" in text[_zero_at:_zero_at + 260], (
        "the zero must name BOTH bases it was measured at — one of them is this record's own, "
        "and `#10982` landed after both")
    # §1 item 3's RESULT (`measured at each → 0, exit 1`) is re-derived in its OWN test — see
    # `test_the_records_zero_is_rederived_at_the_bases_it_names`. It is not here on purpose: it
    # READS TWO HISTORICAL BASES, and `pytest.skip` ABORTS the test function, so on CI's
    # `fetch-depth: 1` checkout a skip there would have killed every assert below it in THIS one
    # (an earlier revision of this comment typed that count; it has grown since — a typed count is
    # this bundle's recurring defect, so the tail is named, not counted) — including arms that need
    # no history at all.
    live_mentions = [i for i, line in enumerate(ext, 1) if "create_operator" in line]
    assert live_mentions == [9021, 9039, 9046, 9426], (
        f"extractor_v2.py's create_operator mentions are at {live_mentions}, not the "
        "four the record quotes — an insertion above them moves the quote; re-derive "
        "the record's block (its `create_operator` line AND this list) rather than the list")
    # ⛔ The arm above binds the four LINE NUMBERS; the record also STATES the count in prose.
    # EIGHT statements across SIX spellings — `prints **4** on origin/main` (x2), `in four
    # comments` (x2), `prints exactly four`, `# §0's four create_operator MENTIONS`,
    # `(four COMMENTS naming`, and the §5 numeral `that is 4 on … origin/main`. SIX patterns
    # grade all EIGHT occurrences (the numeral pattern IS one of the six — it is not an extra).
    # Those were unbound: measured, editing `:81` to "prints exactly three" left the suite GREEN while `:629`
    # still listed four numbers, i.e. the record contradicted its own pinned quote and
    # nothing reddened. A typed count is this bundle's recurring defect (the docstring above
    # says so), so the count is DERIVED here and every prose statement of it is bound.
    #
    # ⛔ Graded PER OCCURRENCE (`re.findall` + `all`), not with `in text`: two of the
    # spellings appear TWICE, so a bare membership test is satisfied by the SURVIVOR and one
    # edited copy passed with the suite green (measured — the same false PASS this assertion
    # exists to close, one level down). A spelling that stops appearing entirely also reds,
    # via the `_found` emptiness check.
    #
    # ⛔ `re.IGNORECASE`, because the claim is the NUMBER: `MENTIONS` -> `mentions` is an
    # editorial reword that moves no number, and a case-sensitive pattern redded on it with a
    # misleading `yields []` diagnostic (measured) — the same false-block this PR removed for
    # the `# §0/§2` heading and the `both now read` parenthetical. Every other token in these
    # patterns is already lower-case, so the tolerance cannot admit a wrong value.
    #
    # The `0` at `80c912244` and the `56 lines / 59 occurrences` at `62248db88` are sha-pinned
    # readings — they read commits that already happened and cannot rot — so they are not bound.
    _n = len(live_mentions)
    _words = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
    for _count_pat in (r"prints \*\*(\d+)\*\* on `origin/main`",
                       r"names `sdk\.create_operator` in (\w+) comments",
                       r"prints exactly (\w+)",
                       r"§0's (\w+) `create_operator` MENTIONS",
                       r"\((\w+) COMMENTS naming",
                       # the §5 numeral, which the record wraps across a COMMENT COLUMN
                       # (`that is 4 on` / newline + ~57 spaces + `# origin/main`) — a
                       # plain `\s+` cannot cross the `# `, so the continuation is named.
                       r"that is (\d+) on\s+#\s+origin/main"):
        _found = re.findall(_count_pat, text, re.IGNORECASE)
        # ⛔ `.lower()` on the MATCH, not just `re.IGNORECASE` in the pattern: the flag makes the
        # pattern match `Four`, but `_found` returns the raw text, so a case-sensitive membership
        # test redded a number-preserving reword (`in four comments` -> `in Four comments`) with a
        # message asserting the count disagreed when `Four == 4` (measured).
        assert _found and all(_v.lower() in (str(_n), _words[_n]) for _v in _found), (
            f"the record states the create_operator mention count as {_n}, but "
            f"{_count_pat!r} yields {_found} — EVERY occurrence must agree with the derived "
            "count (a typed count that nothing re-checks is what rotted here)")

    # A1's §5 "self-matching" note: its annotation claimed `# exit 1 = no output`, which
    # ROTTED to 40 lines once #10982 registered the A1 row into the pinned bundle — the
    # bundle now CITES `hasPart` without producing it, so an unqualified "no output"
    # would be false while the producer claim it stood for stays true. The count is
    # therefore re-derived from the tree here instead of being read out of the record:
    # a re-worded annotation cannot un-state it, and a new bundle mention forces the
    # record to be re-measured.
    mention = re.compile(r"hasPart|hierarchies|decomposesInto")
    # Enumerated through the INDEX, not by walking the directory: the record's own command
    # is `git grep`, which reads tracked files only. A `rglob` walk is NOT equivalent in
    # either direction — an untracked `PRE-REGISTRATION.md~` reds the pin while the
    # recorded command still prints 40 (a false red), and a same-named file under a
    # subdirectory (`corpus/capstone_measure.py`) collapsed into the `probes/` entry under
    # a BASENAME key, so the true 42 lines passed as 40 (a FALSE GREEN on exactly the
    # number this pin exists to protect). Keys are relative paths for that reason.
    tracked = subprocess.run(
        ["git", "ls-files", "-z", "--", "docs/evidence/7949-capstone/"],
        cwd=str(_REPO), capture_output=True, text=True, check=True,
    ).stdout.split("\x00")
    per_file = {}
    for relpath in (p for p in tracked if p):
        if relpath == "docs/evidence/7949-capstone/RECONCILE-A1-A2-A5.md":
            continue
        path = _REPO / relpath
        if not path.is_file():
            continue
        n = sum(1 for line in path.read_text(
            encoding="utf-8", errors="replace").splitlines() if mention.search(line))
        if n:
            per_file[relpath.removeprefix("docs/evidence/7949-capstone/")] = n
    assert per_file == {"PRE-REGISTRATION.md": 22, "preregistration.json": 8,
                        "probes/capstone_measure.py": 10}, (
        f"the bundle's hasPart/hierarchies mentions are {per_file} — the record's §5 "
        "self-matching note states them per file; re-derive the note (and this map) "
        "rather than deleting the qualifier, or it reads as 'no output' when it is not")
    # ── The §5 bundle-grep note, pinned as an EXACT BLOCK whose numbers are DERIVED ──
    #
    # Why a block and not number patterns: matching prose by pattern was measured to fail in
    # every direction, and each failure cost a review round — a re-worded second statement
    # (`21 lines in PRE-REGISTRATION.md`, number BEFORE the name) bypassed it (false PASS); an
    # unrelated command's `12 lines, exit 0` tripped it (false BLOCK); a `count(...) == 2` bound
    # refused the de-duplication the record's own amendment prescribes; and a combined needle
    # reported a dropped historical qualifier as a wrong LIVE number (an inverted message whose
    # repair would INTRODUCE the re-stale). A block has one failure mode and one repair.
    #
    # The numbers are interpolated from `per_file`, so the record's two statements of the total
    # cannot disagree — they are the same DERIVED value, not two typed ones.
    #
    # ⚠️ STATED RESIDUAL: this pins what the record ASSERTS at these cites. It is NOT a proof
    # that the document contains no other sentence stating a different number — a newly written
    # contradictory sentence, in a word order nobody anticipated, is a review-time defect and
    # not something substring matching can see. A reviewer constructed exactly that; the honest
    # answer is to name the residual here, not to chase it with a wider regex that false-blocks.
    _total = sum(per_file.values())
    _split_rows = list(per_file.items())
    _note_block = (
        "git grep -rn -E 'hasPart|hierarchies|decomposesInto' -- 'docs/evidence/7949-capstone/' \\\n"
        "    ':!docs/evidence/7949-capstone/RECONCILE-A1-A2-A5.md'    "
        f"# {_total} lines, exit 0 \u2014 all in the bundle\n"
    )
    for _i, (_name, _n) in enumerate(_split_rows):
        _open = "(" if _i == 0 else ""
        _close = ")" if _i == len(_split_rows) - 1 else ","
        _note_block += (
            f"                                                            # {_open}{_name} {_n}{_close}\n")
    # Compared ROW BY ROW, not with a bare `in`: pytest elides the MIDDLE of a long needle,
    # and a measured one-trailing-space drift on the split row produced a failure whose
    # visible text contained no difference at all — an undiagnosable red.
    _want = _note_block.rstrip("\n").splitlines()
    # The anchor is row 2 MINUS its number: row 1 is the grep command, which §1's item ALSO
    # carries verbatim (`text.find(row1)` landed in §1 and reported a phantom drift —
    # measured), and anchoring on the numbered row raised ValueError the moment the NUMBER
    # was what drifted, i.e. exactly when a diagnosis is needed (also measured).
    _anchor = "    ':!docs/evidence/7949-capstone/RECONCILE-A1-A2-A5.md'    #"
    _at = text.find(_anchor)
    if _at < 0:
        raise AssertionError(
            "the §5 note's reproduction block is GONE — the record must carry it verbatim "
            f"(command + `# {_total} lines, exit 0 — all in the bundle` + the per-file split); "
            "re-derive it rather than deleting it")
    _at -= len(_want[0]) + 1
    _got = text[_at:].splitlines()[:len(_want)]
    if _got != _want:
        _k = next((i for i, pair in enumerate(zip(_got, _want, strict=False)) if pair[0] != pair[1]), 0)
        raise AssertionError(
            "the §5 note's row {} drifted — command, LIVE total and per-file split all come "
            "from the map above plus `git grep`; any edit here means re-derive it (the "
            "unqualified `# exit 1 = no output` and the hand-typed 22/8/10 are the forms that "
            "rotted, #10982 registered the A1 row after this record was written)\n"
            "  want: {!r}\n  got:  {!r}".format(
                _k + 1, _want[_k] if _k < len(_want) else "",
                _got[_k] if _k < len(_got) else ""))
    # ⛔ The number is the claim; the surrounding words are not. `all 40 hits` redded on
    # `40 hits, all of them the bundle's own registration` — which states the derived total
    # correctly (measured) — so only the number and its unit are required.
    assert f"{_total} hits" in text, (
        f"§1 item 3's conclusion must state the DERIVED total ({_total}) — it is the same "
        "`git grep` the block above re-derives")

    # §0/§5's `vet_enabled` live-cite, pinned as SEPARATE exact claims so each failure names
    # the defect it actually is. The combined needle used to conflate the live number with the
    # historical qualifier: dropping the parenthetical wrote `:12929` correctly and was still
    # told it had quoted the WRONG live line — a message whose repair would INTRODUCE the
    # re-stale (measured).
    # ⚠️ The NUMBER is the claim, not the region. A region-only check passed with §1 re-staled to
    # `:12853` while an unrelated `:12929` sat earlier in the record (measured) — the record states
    # one number at several places, so "appears somewhere before the block" is not "§1 quotes it".
    # Bound to the base-qualified cite instead, and MADE wording-free rather than described as
    # such: an earlier `at (?:HEAD|here)` pinned the two spellings the record happened to use, so a
    # third qualifier (`at the head`) redded with a diagnostic asserting the WRONG NUMBER while
    # `:12929` was present (measured — the comment claimed a tolerance the code did not have).
    # The qualifier is now any word run, and "base-qualified" is expressed as its opposite: the
    # qualifier must NOT be a base (`at 62248db88` / `at `62248db88`` are excluded), which is what
    # separates the LIVE cite from the historical one in the same sentence.
    # ⛔ The boundary is located by the SECTION MARKER alone (`^# §0/§2`) — no typography and no
    # extra word: the em-dash reword redded once (measured), and `sha-pinned` in the needle made
    # the word do work it was not doing (only one line matches `^# §0/§2`, verified) while a reword
    # of it redded asserting the block was GONE.
    _hdr = re.search(r"^# §0/§2\b", text, re.MULTILINE)
    _block_at = _hdr.start() if _hdr else -1
    _s2_at = text.find("## 2. A2")
    assert _block_at > 0 and _s2_at > 0, (
        "the record's §2 (A2) section or its §0/§2 reproduction block is GONE — both are where "
        "the sha-pinned and live cites live")
    # Bounded at the NEXT heading, not at the block: `text[_s2_at:_block_at]` spans §2→§5, so a
    # decoy cite in §3/§4 was reported as "§2 (A2)"'s (measured) — the same mis-labelling the
    # §1→§2 rename fixed, at the far end. The §5 region is bounded the same way for the same reason.
    _s2_end = text.find("\n## ", _s2_at + 1)
    _block_end = text.find("\n## ", _block_at + 1)
    assert _s2_end > _s2_at and _block_end > _block_at, (
        "§2 or the §0/§2 block is now the LAST section — the heading bound that makes the region "
        "label true cannot be found, so re-derive these two bounds")
    # ⛔ THE POLICY OF THIS PARAGRAPH IS THE RECORD'S, NOT THIS PIN'S — `#11555` re-cut it and
    # this arm follows (#11002 forward-merge). The register used to quote `:12929` **at HEAD**;
    # the landed record now quotes it **at the base it was true at**, and states why: *"cite the
    # symbol and let the line number be a convenience printed beside it … pinned to the two shas,
    # never to `HEAD`, because a `HEAD`-relative number is the same rot in a new coat"*. A pin
    # that still demanded a live `HEAD` cite therefore asserted a RETIRED policy, and the only way
    # to make it green would be to re-instate the rot the record exists to remove. The claim
    # asserted here is the base-qualified PAIR: a `:NNNN` with no base beside it is the shape
    # `#11555` retired, and it must not come back.
    for _label, _region, _is_repro_block in (("§2 (A2)", text[_s2_at:_s2_end], False),
                                             ("the §5 reproduction block",
                                              text[_block_at:_block_end], True)):
        _found = re.findall(r":(\d+)`? at `?([0-9a-f]{7,40})`?", _region)
        assert ("12929", "a1dff7cec8") in _found, (
            f"{_label} must quote the vet_enabled line as a BASE-QUALIFIED pair "
            f"(`:12929` at `a1dff7cec8`) — it reads {_found}; re-derive it with "
            "`git grep -n 'vet_enabled = ' -- tortoise/extractor_v2.py`")
        # ⛔ WORDING-INDEPENDENT, because a deny-list of qualifiers is not a pin (#11519 review):
        # binding the check to the literal word `at` meant a re-stale written ANY other way
        # shipped green — measured, `` `:12944` (HEAD) `` in §2 passed. So the arms assert two
        # things a wording cannot dodge: every `:12929` in these regions carries the base it was
        # true at within 60 characters (the same idiom the neighbouring `:12779` arm uses for its
        # own number), and the LIVE number — derived from the tree below, never typed — is not
        # cited without a base of its own in §2 (the §5 block classifies every qualifier it
        # carries, also below).
        # ⛔ THE LIVE-NUMBER ARM'S FIRST FORM WAS ITSELF A MEASURED DEFECT (#11523 review), kept
        # here rather than deleted because the wrong form reads correct: it was a region-wide
        # deny-list (`f":{live}" not in region`), wrong in BOTH directions. It over-blocked a
        # sha-qualified read — the very repair its own diagnostic prescribes (`:<n>` at `<sha>`,
        # `#11555`'s landed policy) — so a CONFORMANT edit redded a required check while the
        # message told the fixer to make that edit; and it made this arm able to require and
        # forbid the SAME string as the base-qualified pair arm above (were a merge to leave
        # `vet_enabled = (` on `:12929`, the pair arm demands a base-qualified `:12929` and the
        # deny-list demands its absence — each reds the other, and the message blames a cite that
        # is correct). Two earlier forms were measured wrong as well: a qualifier regex
        # under-blocked a BARE `:12944` and over-blocked ordinary prose (`HEAD` unanchored matches
        # "ahead"/"header", and "in this tree" read as a citation), and a finite number list is a
        # word list by another name. So the arm is neither a ban on the string nor a window: it
        # pairs each occurrence with ITS OWN base qualifier (`_unqualified_number_cites`, whose
        # cases are asserted with the classifier's above) — which reds exactly the shape `#11555`
        # retired, a `:NNNN` standing alone, and never the base-qualified form that policy
        # prescribes.
        # ⚠️ WHAT THIS ARM STILL DOES **NOT** SEE, restored because an earlier rewrite of this
        # comment deleted it (#11523 review at `f885a51663`): it covers the LIVE number —
        # `vet_enabled = (`'s own line, derived below — and `:12929`, and NOTHING ELSE. A
        # re-stale of any OTHER number in §2 to the live/HEAD shape (`:13011` at HEAD) still reds
        # nothing (measured: whole file green). That was a true statement when `#11517` added it
        # and it is still true, so it stays: closing it needs a citation grammar §2 does not have
        # yet — its cites are base-qualified, file-qualified AND git-grep output, and a blanket
        # "every `:NNNN` carries a base" rule would false-block the symbol-only cites the record's
        # policy explicitly prefers — which is a separate piece of work, not a wording change
        # here.
        for _m in re.finditer(r":12929", _region):
            _near = _region[max(0, _m.start() - 60):_m.end() + 60]
            assert "a1dff7cec8" in _near, (
                f"{_label}: `:12929` at offset {_m.start()} does not name the base it was true at "
                "— an unqualified occurrence is a re-stale presented as live")
        # ⛔ A BARE HEAD-RELATIVE LIVE NUMBER IS FORBIDDEN HERE, and the number is DERIVED from the
        # tree rather than typed, because the arm above cannot see one: §2 used to carry
        # `:NNNN at the tree you are reading` immediately followed by a base-qualified
        # `:12929 at a1dff7cec8`, and the ±60 window around the LIVE occurrence reached that
        # neighbour's base, so the PAIR was satisfied by the NEIGHBOURING cite (measured: main
        # moved `vet_enabled = (` from `:12929` to `:12944` under the merge and this whole file
        # stayed green). ⛔ WHICH IS WHY THE ARM BINDS A BASE TO THE OCCURRENCE INSTEAD OF BANNING
        # THE NUMBER (#11523 review): the record's landed policy (#11555) is "cite the symbol and
        # let the line number be a convenience printed beside it", pinned to a SHA — so
        # `` `:12944` at `<sha>` `` is the CORRECT repair and passes, while a bare `:12944`, a
        # `:12944` (HEAD) and a re-staled `:12944 at the tree you are reading` all red. What is
        # read from the tree is the NUMBER; nothing is copied from the prose that cites it.
        if not _is_repro_block:
            _v2 = (_REPO / "tortoise/extractor_v2.py").read_text(encoding="utf-8").splitlines()
            _vet = [i + 1 for i, _ln in enumerate(_v2) if "vet_enabled = (" in _ln]
            assert len(_vet) == 1, (
                f"{_label}: expected exactly one `vet_enabled = (` in tortoise/extractor_v2.py, "
                f"found {_vet} — re-derive this region's cites")
            _unbased = _unqualified_number_cites(_region, str(_vet[0]))
            assert not _unbased, (
                f"{_label}: `:{_vet[0]}` — the line `vet_enabled = (` is on in the tree this pin "
                f"just read — appears {len(_unbased)} time(s), at offset(s) {_unbased}, with no "
                "base of its own. A HEAD-relative number rots on the next merge, and a base "
                "printed on a NEIGHBOURING cite does not discharge it (measured). Cite the "
                f"symbol, or pin the number to the sha it was true at — `` `:{_vet[0]}` at "
                "`<sha>` `` — which this arm accepts.")
        # ⛔ The §5 watch, kept where main's WORKS and widened to the spellings main's cannot see
        # (#11519 review). Main's arm was `_found == ["12929"]` over an `at`-WORD shape: green on
        # the §5 block and RED on §2 (main's record re-cut §2's cite, so arm and text disagreed
        # there — why it could not be kept verbatim). Dropping it wholesale took a WORKING watch off
        # the block the record calls its reproduction path (measured: `:13011 at HEAD` injected
        # there left the whole file GREEN), so it is block-scoped — and main's word shape is
        # replaced by a qualifier CLASSIFIER, because that shape catches ONE spelling of three:
        # `` `:13011` (HEAD) `` never contains `at`, and `` `:13011` at `HEAD` `` fails its `[\w]`
        # at the backtick — both measured GREEN on main's arm. It also redded the PERMITTED
        # `` :12929 at `a1dff7cec8` `` (a base, printed in backticks) while reporting "qualifier is
        # not a base ([])" — naming the OPPOSITE defect — because its lookahead excludes only a
        # base that STARTS WITH A DIGIT.
        # ⚠️ Still not seen here: a cite with NO qualifier at all (`:13011` alone) — the block as
        # shipped carries `:10906` that way (a live cite, correctly), so a no-bare-number rule
        # would false-block on the record itself. That is the same grammar gap as §2, one line up.
        if _is_repro_block:
            _quals = _cite_qualifiers(_region)
            # ⛔ TWO reachable diagnoses, and no third that cannot fire. `#11519`'s finding was that
            # ONE conflated list reported "qualifier is not a base ([])" for a cite whose qualifier
            # was a perfectly good base — the message named the opposite defect. The distinction is
            # made by two asserts that each fire in a state a mutation can reach: the pair assert
            # above says the base-qualified SPELLING is gone (`:12929 (a1dff7cec8)`, `@`, any
            # non-`at` connector), and `_not_base` says a qualifier IS present and is not a base
            # (`at HEAD`, `` at `HEAD` ``, `(HEAD)`). A third guard here — "did the classifier see
            # anything" — would be an assert that cannot fail (the pair regex above is a subset of
            # what the classifier reads), i.e. exactly the inert-enforcer class this file exists to
            # keep out; the classifier's own non-inertness is pinned by the degenerate cases near
            # `_call_sites` instead.
            _base = {(_n, _q) for _n, _q in _quals if re.fullmatch(r"[0-9a-f]{7,40}", _q)}
            _not_base = [(_n, _q) for _n, _q in _quals if (_n, _q) not in _base]
            assert not _not_base, (
                f"the §5 block qualifies a cite with something that is NOT a base "
                f"({_not_base}) — a base is 7–40 lowercase hex, and `<n> at HEAD` / "
                "`<n> (HEAD)` are the LIVE shapes `#11555` retired. ⛔ What this predicate "
                "measures is the qualifier's SHAPE only; base CORRECTNESS is not tested here "
                "— it is carried by the `:12929`/`:12779` arms beside this one, so a "
                "sha-shaped qualifier for the wrong base stays green (#11517 review)")
    # The SYMBOL is what the record cites, so the invariant that survives any later insertion is
    # that `vet_enabled = (` is unique IN THE FILE. A rename or a second assignment is what makes
    # the citation ambiguous; the line it happens to sit on is not the claim.
    # ⛔ The CLAIM must be what the predicate measures (#11519 review): `strip()` ignores
    # indentation, so "at module scope" was a scope claim this scan cannot make — the real
    # definition is itself indented inside its function. What IS asserted is uniqueness in the
    # FILE, which is the invariant that keeps the record's symbol citation unambiguous; a rename
    # or a second assignment of the same name is what breaks it.
    # ⛔ A SPELLING IS NOT A DEFINITION (#11519 review, P3 — twice). A literal `startswith` scan
    # counted one spelling, so a second assignment written `vet_enabled: bool = (False)` stayed
    # green while the message named exactly that as the invariant, and a docstring line carrying
    # the text redded as a "second assignment". A looser LINE pattern then over-matched the KEYWORD
    # ARGUMENT uses (`vet_enabled=vet_enabled,` at :12999 and :13039, measured), which are not
    # assignments at all. So count DEFINITIONS in the AST: an Assign/AnnAssign whose target is the
    # bare name `vet_enabled`. That is the invariant the message states, measured the way it says.
    _vet_lines = [
        node.lineno
        for node in ast.walk(ast.parse("\n".join(ext)))
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and any(isinstance(t, ast.Name) and t.id == "vet_enabled"
                for t in (node.targets if isinstance(node, ast.Assign) else [node.target]))]
    assert len(_vet_lines) == 1, (
        f"`vet_enabled` must be DEFINED exactly once in extractor_v2.py (an `Assign`/`AnnAssign` "
        f"to the bare name) — found at {_vet_lines}. The record cites this symbol, so a second "
        "definition or a rename is what makes its citation ambiguous")
    # The INVARIANT is that every `:12779` names the base it was true at — never that there are
    # two of them. (A `count(...) >= 2` bound refused a legitimate consolidation to one
    # base-qualified note, and fired before the pin that names the real defect.)
    assert ":12779" in text, (
        "the historical mention of the old base's number must survive, base-qualified")
    for _m in re.finditer(r":12779", text):
        _near = text[max(0, _m.start() - 60):_m.end() + 60]
        assert "62248db88" in _near, (
            f"`:12779` at offset {_m.start()} does not name the base it was true at — "
            "an unqualified occurrence is a re-stale presented as live")
    # Both SITES must still carry it — deleting either one left the suite green (measured), and
    # the old number is exactly what a reader meets in each place. Per-REGION, never a count: one
    # qualified mention per region is the claim.
    for _label, _region in (("§2 (A2)", text[_s2_at:_s2_end]),
                            ("the §5 reproduction block", text[_block_at:_block_end])):
        assert re.search(r":12779`? at `?62248db88", _region), (
            f"{_label} must carry the historical `:12779` qualified by the base it was true at — "
            "deleting it leaves the old number unmarked where a reader meets it")
    # ⛔ No phrase pin here on purpose: the loop above is the claim (EVERY occurrence names
    # its base). Pinning "both" as two exact sentences redded on a re-worded pair that kept
    # both base-qualified — and this paragraph's own note says the invariant is never the
    # count.

    # A2: the capture path that writes the `references` edge — the record names it
    # THREE times: the prose cite, the amendment parenthetical that states BOTH
    # numbers moved together, and the `sed -n '<n>p'` that reproduces it. All three
    # must agree; the parenthetical is pinned by its NUMBER because it is the only one
    # that has no `sdk.py:` token or `sed` command for the arms below to catch
    # (measured: re-staling it to `17472` left every arm True).
    # ⛔ No backticks in the needle: `tortoise/sdk.py:17574` is the citation, and the code
    # span around it is Markdown styling. The `16516-16530` arm below has always allowed
    # optional backticks (`r"`?(?:tortoise/)?sdk\.py:16516-16530`?"`); this one arm did not,
    # so an unstyled citation redded the same way (measured).
    assert re.search(r"`?tortoise/sdk\.py:17574`?", text), (
        "the record must cite the capture path by its live number")
    # ⛔ Pinned as the BARE occurrence of the NUMBER, not as the sentence around it: the
    # claim is that this paragraph states the same line the prose and the `sed` do.
    # The prose carries it as `sdk.py:17574` and the recipe as `sed -n '17574p'`, so the
    # amendment paragraph is the ONE remaining bare statement of it. A literal
    # `"both now read `17574`"` needle redded on every number-preserving reword
    # (`the two now agree on `17574``, `both now read `:17574`` — measured), which is the
    # same false-block this PR fixed for the `# §0/§2` heading one arm above.
    _bare = [c for c in re.findall(r".{0,12}17574", text)
             if not c.endswith("sdk.py:17574") and "sed -n '" not in c]
    assert len(_bare) == 1, (
        "the amendment paragraph must state the live number `17574` exactly ONCE as a BARE "
        f"cite — it is the only one of the three statements that carries neither a `sdk.py:` "
        f"token nor a sed (found {_bare}); re-derive it with `grep -n '^ *proj\\.link_source_to_event' "
        "tortoise/sdk.py`")
    assert re.findall(r"sed -n '(\d+)p' tortoise/sdk\.py", text) == ["17574"], (
        "the record's reproduction command must name the SAME line the prose does")
    assert sdk[17574 - 1].strip() == "proj.link_source_to_event(source_key, event_id)", (
        f"sdk.py:17574 reads {sdk[17574 - 1].strip()!r}, not the references writer")

    # ── The four LIVE cites a fresh review found STILL unwatched ─────────────────────
    #
    # ⛔ Why these and not others: the record is in no `_BUNDLE_DOCS` entry, and each of
    # these four is a LIVE (un-stamped) cite in it that appeared in NO test. Measured at
    # `54ec77db0e`: `git grep -n -- 'pack_hierarchy.py:419' -- tests/` (and the other
    # three) printed nothing, i.e. the record's thesis — "a claim nothing re-checks can
    # rot silently" — was still true of its own text here. The `capstone_measure.py`
    # window is the proven-rotten one: the record says this very cite was stale-corrected
    # once and moved again by `#11459`'s +102 insertion.
    #
    # ⛔ Pinned at BOTH ENDPOINTS, exactly as the 2738-2740 arm below pins its three lines:
    # a substring test over the 9-line slice was GREEN against a 1-2 line insertion above the
    # window (measured) — the content simply slid along inside the shifted slice, so the arm
    # added BECAUSE this cite rots by insertion did not detect an insertion. The two content
    # facts below then hold the window's MEANING, and the record-side assert holds its number.
    assert probe[275 - 1].strip() == 'ANCHOR_OUT = ("extractedFrom",)', (
        f"capstone_measure.py:275 reads {probe[275 - 1].strip()!r}, not the window's opening line")
    assert probe[283 - 1].strip() == "#: is on the same link, but the DERIVED node is its TARGET.", (
        f"capstone_measure.py:283 reads {probe[283 - 1].strip()!r}, not the window's closing line")
    _probe_win = "\n".join(probe[275 - 1:283])
    assert "link_source_to_event" in _probe_win, (
        "probes/capstone_measure.py:275-283 no longer names `link_source_to_event` — the "
        "record cites that window as the `references` writer, so re-derive the cite")
    # ⛔ The record states this window TWICE — as the cite pinned above and as the
    # reproduction command — and only the first was watched (measured: re-staling the second
    # to `274,282` left the leg GREEN). Symmetric with the `sed -n '17574p'` arm for the
    # sibling capture-path cite, which was watched from the start.
    assert re.findall(
        r"sed -n '(\d+),(\d+)p' docs/evidence/7949-capstone/probes/capstone_measure\.py",
        text) == [("275", "283")], (
        "the record's reproduction command must name the SAME window the cite and the arms "
        "above do — it is the second statement of these two numbers")
    assert "tortoise/sdk.py:17574" in _probe_win, (
        "the cited probe window must still carry the `sdk.py:17574` cite it states — one "
        "number in two places cannot be allowed to drift apart silently")
    assert pack_hierarchy[431 - 1].strip() == "def apply_hierarchy_edges(", (
        f"tortoise/pack_hierarchy.py:431 reads {pack_hierarchy[431 - 1].strip()!r}, not the "
        "declaration the record names as the thing that writes the hierarchy edges")
    assert identity_hold[125 - 1].strip() == 'HOLD_ENDPOINT = "operator_endpoint"', (
        f"tortoise/identity_hold.py:125 reads {identity_hold[125 - 1].strip()!r}, not the "
        "HOLD's one declared kind")
    assert value_gate[81 - 1].strip() == 'return is_truthy(os.environ.get(ENV_FLAG, "1"))', (
        f"tortoise/value_gate.py:81 reads {value_gate[81 - 1].strip()!r}, not the default-ON "
        "the record cites")
    # ⛔ The RECORD side of all four, so a re-stale in the record reds too: every other cite
    # arm in this leg is two-directional (it pins the code line AND the record's citation of
    # it), and these four were code-side only — a record re-stale passed (measured), which
    # contradicts the docstring's "re-staling a cite in the RECORD reds it". The window's
    # RANGE is asserted as the range its endpoints were just verified at, so the record cannot
    # name a different window than the one pinned above.
    for _cite in ("`pack_hierarchy.py:431`", "`probes/capstone_measure.py:275-283`",
                  "`identity_hold.py:125`", "`tortoise/value_gate.py:81`"):
        assert _cite in text, (
            f"the record must still cite {_cite} — the arm below pins the line it names, "
            "so deleting the citation would leave the pin asserting something no reader is "
            "sent to")
    # ⛔ `value_gate.py:81` is stated TWICE — the prefixed cite in §2 and the BARE
    # parenthetical in the §5 reproduction block (`# the default-ON itself
    # (value_gate.py:81)`). The loop above watches the prefixed one only, so re-staling the
    # §5 copy shipped GREEN (measured) — the same asymmetry the `275-283` `sed` line had.
    assert re.findall(r"(?<!tortoise/)value_gate\.py:81", text) == ["value_gate.py:81"], (
        "the record states `value_gate.py:81` a second time in the §5 reproduction block, "
        "bare — that copy must name the same line the §2 cite does")

    # A2's Object mint: the write seam that routes creation by kind, cited THREE
    # times as a range. Both endpoints are claims — the classifier opens it, the
    # object arm's `is_episodic=False)` closes it.
    assert len(re.findall(r"`?(?:tortoise/)?sdk\.py:16516-16530`?", text)) == 3, (
        "the record names this seam three times — the mint, the route, and the "
        "reproduction command — and all three must move together")
    assert sdk[16516 - 1].strip() == "_ekind = _payload_entity_kind(e)", (
        f"sdk.py:16516 reads {sdk[16516 - 1].strip()!r}, not the classifier call")
    assert sdk[16530 - 1].strip() == "is_episodic=False)", (
        f"sdk.py:16530 reads {sdk[16530 - 1].strip()!r}, not the object arm's close")

    # A5: the census query the "cannot observe the edge" gap rests on. All THREE
    # lines of the cited range are pinned, because the claim is the RANGE: the
    # `g.query(` call opens it, the `MATCH` enumeration is its middle, and the
    # anchored count closes it. A one-line insertion above the call therefore moves
    # every endpoint and reds — a window test on the MATCH token alone did NOT
    # (measured in review: one inserted line above the window left this arm GREEN while
    # the record's number was stale at both ends). ⛔ The number is the MERGED tree's:
    # main's #11517 landed +12 above the window, so the same query now opens at `:2738`.
    assert "`probes/capstone_measure.py:2738-2740`" in text
    assert probe[2738 - 1].strip() == "for name, count, anchored in projection.g.query(", (
        f"capstone_measure.py:2738 reads {probe[2738 - 1].strip()!r}, not the census call")
    assert "MATCH ()-[r]->() RETURN type(r)" in probe[2739 - 1], (
        f"capstone_measure.py:2739 reads {probe[2739 - 1].strip()!r}, not the "
        "relationship census query")
    assert "count(r.sourceVersion)" in probe[2740 - 1], (
        f"capstone_measure.py:2740 reads {probe[2740 - 1].strip()!r}, not the "
        "anchored count that closes the query")


def test_the_records_zero_is_rederived_at_the_bases_it_names():
    """§1 item 3's `0 hits` — a claim about TWO HISTORICAL bases, so it is read there.

    ⛔ Split out of the cite pin on purpose. `pytest.skip` ABORTS the test function, and CI
    checks this repo out with a bare `actions/checkout@v4` (`python-ci.yml`, default
    `fetch-depth: 1`), where neither base is in the clone:

      * `4e6a3ce612bd^` — the pre-`#10982` tree, where the record exists but the bundle has not
        yet registered the predicate;
      * `80c912244` — this record's own base.

    A skip inside the cite pin therefore killed every assert BELOW it — the whole tail of that
    function, measured,
    including arms that need no history at all. Here a skip costs exactly this leg, and says so.

    The claim itself: `measured at each → 0, exit 1`. Both halves are re-derived, because a typed
    number nothing re-checks is the defect this whole record exists to catch — flipping the
    record to `9, exit 0` left every arm of the cite pin green (measured).
    """
    record = _REPO / "docs/evidence/7949-capstone/RECONCILE-A1-A2-A5.md"
    text = record.read_text(encoding="utf-8")
    for _sha in ("4e6a3ce612bd^", "80c912244"):
        _run = subprocess.run(
            ["git", "grep", "-rn", "-E", "hasPart|hierarchies|decomposesInto", _sha, "--",
             "docs/evidence/7949-capstone/",
             ":!docs/evidence/7949-capstone/RECONCILE-A1-A2-A5.md"],
            cwd=str(_REPO), capture_output=True, text=True)
        if _run.returncode not in (0, 1):
            # A sha that cannot be READ is not a zero. Licence the skip by the clone's own shape
            # (a depth-1 checkout cannot contain an ancestor), or fail: an absent object in a FULL
            # clone means the base the record names is WRONG, and skipping would hide it forever.
            _shallow = subprocess.run(
                ["git", "rev-parse", "--is-shallow-repository"],
                cwd=str(_REPO), capture_output=True, text=True).stdout.strip()
            if _shallow == "true":
                pytest.skip(
                    f"{_sha} is not in this shallow clone — THIS LEG only (the two historical "
                    "bases of §1 item 3's `0 hits`) is UNCHECKED here, not disproved; re-derive "
                    "it on a full clone. The cite pin above needs no history and still runs.")
            pytest.fail(
                f"{_sha} is unreadable in a FULL clone ({_run.stderr.strip()[:200]}) — the record "
                "names a base nobody can verify")
        assert (_run.returncode, _run.stdout) == (1, ""), (
            f"`{_sha}` returns {_run.stdout.count(chr(10))} hit(s), not the 0 exit-1 the record "
            "states — re-derive §1 item 3's zero; it is measured at BOTH bases it names")
        # (No post-loop `_checked == 2` assert: the ONLY ways out of this body that do not
        # increment are `pytest.skip`/`pytest.fail`, both of which RAISE, so such an assert
        # could never fire — an inert arm (measured by reading every path). The invariant is
        # enforced by those raises themselves, which is where it belongs.)
    _stated_zero = re.search(r"measured at\s+each → (\d+), exit (\d+)", text)
    assert _stated_zero is not None, (
        "§1 item 3 must STATE the result it names its bases for (`measured at each → N, exit N`) "
        "— naming the bases without the number is what left the claim unwatched")
    assert _stated_zero.groups() == ("0", "1"), (
        f"the record states `{_stated_zero.group(0)}` while the bases measure 0 hit(s), exit 1 "
        "each — correct the RECORD, not the measurement")


#: `capstone_measure.py:N` cites that live in files this bundle does NOT sweep, with the
#: SYMBOL each one claims is at `:N`. The NUMBER is never typed here — it is read out of the
#: citing file by the pattern below — so this table cannot itself go stale in the direction
#: that matters (a typed number is the defect it exists to catch).
_CROSS_FILE_PROBE_CITES = (
    (
        "docs/evidence/7949-capstone/probes/e1_e2_scores.py",
        re.compile(r"`share_floor`'s `float\(value\)` \(`capstone_measure\.py:(\d+)`\)"),
        ("return float(value)",),
        "the `OverflowError` arm names the floor's own `float(value)` conversion",
    ),
    (
        "tools/capstone_verdict_check.py",
        re.compile(
            r"written INTO the run block by the emitter\s*\n"
            r"\s*# \(`capstone_measure\.py:(\d+)`\)"),
        ('"a_row_census": a_census,',),
        "#10982's key-set entry names where the emitter writes `a_row_census`",
    ),
    (
        "tools/capstone_verdict_check.py",
        re.compile(
            r"``require_door_entered\(runs\)`` BEFORE it reaches ``render_log``\s*\n"
            r"#: \(``capstone_measure\.py:(\d+)`` vs ``:(\d+)``\)"),
        ("require_door_entered(runs)", "text = render_log("),
        "#11298/#11331's `door` entry names the two CALLS whose ORDER it argues from",
    ),
)


def test_the_probes_cross_file_cites_resolve_to_the_symbols_they_name():
    """Three `capstone_measure.py:N` cites sit in files no sweep here reads — so they rotted.

    This PR's own `DOOR_PINNED_ON` entry grew by two comment lines — moving the
    `TORTOISE_STATE_VALUE_VERBATIM` tuple `:504` → `:506` on this branch (`:518` in the
    merged tree, after main's own insert) — and every line below it by
    **+2**, so three cites naming the probe from OUTSIDE this bundle moved with it. Two
    were already stale before this PR moved anything, measured against the base it
    merged (`f5c69f74781`): `:519` sat 480 lines above the `float(value)` it means
    (`:999`, and `:1001` at this branch's pre-fix head) and `:4019` 25 lines above the
    census key (`:4044`, `:4046` there), so the +2 was the smaller half of each defect —
    the rot was structural, not this PR's: nothing re-checks a `file:line` string that no
    suite reads, and `grep` for each number finds only the cite itself, so the wrong
    number is silent in BOTH directions. That is this bundle's own thesis, found inside
    its own citations.

    ⛔ What is pinned is the PAIR (which line, which symbol), and the line is read OUT of
    the citing file — never typed here. Typing the number would repeat the defect: the
    table would go stale exactly as the prose it watches does, and a fixer would be
    re-pointing two copies of the same fact. The SYMBOL is the durable half, which is
    why the repair re-points numbers at symbols that did not move.

    Non-vacuity, measured at the pre-fix head: every arm reds there — `:519` is a
    `TORTOISE_VALUE_GATE` comment (the value is at `:1001`), `:4019` is the
    `"probe": "docs/…/capstone_measure.py",` key (the census key is at `:4046`), and
    the `door` entry's `:4164`/`:4299` name NEITHER half of any pair — and the numbers in
    this paragraph are deliberately SYMBOLS, not tree positions: the merge that landed
    main's #11502 moved every one of them (measured after it: the frozen-sessions GUARD,
    the `REFUSED` f-string, `"verdict": verdict_word(e5_result),` and
    `"denominator": e5["raw_n"],` all sit on new lines). What the arm asserts is the
    PAIRING at the head it runs on, which it reads out of the citing file, so no number
    here is a claim about a tree. Re-staling any cite reds it again.

    ⛔ The pair is TWO CALL SITES on purpose. An earlier cut of this pin bound ``:3782`` —
    ``def render_log(``, a DEFINITION — against the ``require_door_entered(runs)`` call,
    which cannot evidence an execution ORDER at all: a function body is not a call, and
    the pair read ascending (``3782 < 4191``), so a checking reader concluded the
    OPPOSITE of the sentence's "BEFORE". The shape assertion below (``def`` on neither
    side, and matching) is what keeps a definition from silently returning.
    """
    probe = PROBE.read_text(encoding="utf-8").splitlines()
    for rel, pattern, symbols, claim in _CROSS_FILE_PROBE_CITES:
        source = (_REPO / rel).read_text(encoding="utf-8")
        hits = pattern.findall(source)
        assert len(hits) == 1, (
            f"{rel} must state this cite exactly ONCE — found {len(hits)} matches of "
            f"{pattern.pattern!r}. A second copy is a second thing to re-point, and one of "
            "the two will be missed")
        nums = hits[0] if isinstance(hits[0], tuple) else (hits[0],)
        assert len(nums) == len(symbols), (
            f"the pattern reads {len(nums)} line number(s) but {len(symbols)} symbol(s) are "
            "pinned — an unpaired number is a cite nothing checks")
        for num, symbol in zip(nums, symbols, strict=True):
            line = probe[int(num) - 1].strip()
            assert symbol in line, (
                f"{rel} cites capstone_measure.py:{num} — {claim} — but that line reads "
                f"{line!r}, which does not contain {symbol!r}. Re-derive the number from the "
                "tree (grep the symbol); do NOT move the symbol to fit the cite")
            # A cite that lands on the DEFINITION would satisfy `symbol in line` too —
            # `def require_door_entered(runs) -> dict:` and `def render_log(` are both
            # definitions, but neither proves ORDER: the `door` entry argues that `main`
            # reaches the gate BEFORE the renderer, so what it must name is the two CALLS
            # in `main` (`require_door_entered(runs)` and `text = render_log(`). The shape
            # is asserted alongside the token so the pair cannot silently slide onto a
            # definition and lose the argument. ⛔ No line numbers here: the merge that
            # landed main moved all four of them, and a number in a comment is a claim no
            # arm reads (this pin's thesis, turned on its own prose).
            assert line.startswith("def ") == symbol.startswith("def "), (
                f"capstone_measure.py:{num} is cited for {symbol!r}, but that line is "
                f"{'a definition' if line.startswith('def ') else 'not a definition'} — the "
                "entry names the two CALLS whose order it argues from, never a definition")
