"""Tests for the two-arm S3 prior-injection ablation probe (#10463).

The probe measures a pre-registered hypothesis, so the tests that matter are the ones that pin the
ARITHMETIC and the REFUSALS — not the run: a verdict mapping that could be re-read generously after
a result, a concentration statistic that divides by zero, an evidence path that escapes the bundle,
or a bars source that a worktree edit could serve.

The run itself needs a live graph and a model key, so it is not exercised here; `tests/` runs
keyless in CI, and a test that needed the key would be a test that never runs.
"""
from __future__ import annotations

import importlib.util
import inspect
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PROBE = ROOT / "tools/s3_prior_ablation.py"


def _probe():
    spec = importlib.util.spec_from_file_location("_s3_prior_ablation", PROBE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_MODULE = _probe()


@pytest.fixture(scope="module")
def probe():
    return _MODULE


#: The frozen thresholds — READ OUT OF `PRE-REGISTRATION.md` with the probe's own parser.
#:
#: They used to be a SECOND copy: a `BARS`/`FLOORS` literal here, beside the real pins in the
#: document. That is the "two copies of a threshold that can disagree" shape the pre-registration
#: gate names as its reason for existing — and here it was worse than a disagreement, because it
#: made the guard that was supposed to exist not exist: a docs-only diff (which runs only the
#: `tier1` slice) could change `primary_ratio_max: 0.75 → 0.90` and `guard_ratio_min: 1.00 → 0.80`
#: in the pins block, flip the published verdict from `HYPOTHESIS-FALSIFIED` to
#: `HYPOTHESIS-SUPPORTED` with no reported number moving (`0.8697 <= 0.90`, `40 >= 0.8 × 47`,
#: `139 >= 0.8 × 171`), and every test in this file would still have passed against its own
#: private copy (#10468 r6 F1). There is now ONE copy: the document's.
_PINS = _MODULE.parse_pins((ROOT / _MODULE.PREREG_REL).read_text())
BARS = _PINS["hypothesis_bars"]
FLOORS = _PINS["denominator_floors"]

#: Where the measured log lives, RELATIVE to the bundle (`probe.EVIDENCE_DIR`) — one constant, so the
#: next move is one edit rather than four.
#: ⛔ It is under `withdrawn/` on purpose (#10463): the pre-registration guard
#: (`tools/capstone_preregistration_check.py`) globs `*.log` NON-recursively in the registration's own
#: directory, and the squash merge #10468 collapsed the registration and this log into ONE commit, so
#: its strict-ordering claim could not be shown from the merged history. The move is a WITHDRAWAL of
#: the ordering provenance, NOT of the measurement: the bytes are identical (sha256
#: `bba52c90d005ae728dc7c3cc7d25c232e322d0abe5bffc6873a6e74fa5960b4d`), which is why every assertion
#: below still measures the run that shipped.
MEASURED_LOG_REL = Path("withdrawn") / "s3-ablation-a1075dfc.log"


def _log_thresholds(log_text: str) -> dict[str, float]:
    """Every threshold the committed log says the run compared against."""
    import ast
    import re

    seen: dict[str, float] = {}
    for line in log_text.splitlines():
        if line.startswith("bars:"):
            seen.update({key: float(value) for key, value in
                         re.findall(r"(\w+)=(\S+)", line[len("bars:"):])})
        elif line.startswith("floors:"):
            seen.update(ast.literal_eval(line.split(":", 1)[1].strip()))
    return seen


def _threshold_mismatches(pins_bars: dict, pins_floors: dict, log_text: str) -> list[str]:
    """Where the document's frozen thresholds and the measured log's disagree.

    Only the thresholds the LOG names are compared, because a log can only be held to what it
    recorded — the committed log predates the full-bars emission below and names five of the seven.
    The two it does not name are held through the RECORD, not through this function:
    `preregistration.json` restates every bar, and `tools/capstone_preregistration_check.py` — which
    CI now runs on this bundle — compares that map against the document's pins in both directions,
    so a bar moved in either copy (or dropped from the record, or invented by it) reds the gate.
    `corroboration_min_sessions` decides the published `not-majority-of-sessions` qualifier;
    `sessions_total` is emitted-only. `test_the_machine_record_restates_every_frozen_bar` pins the
    first half here, `tests/test_capstone_preregistration_9075.py` holds the second (#10468 P2).
    """
    seen = _log_thresholds(log_text)
    problems = []
    for key, value in {**pins_bars, **pins_floors}.items():
        if key in seen and float(seen[key]) != float(value):
            problems.append(
                f"{key}: the measured run compared against {seen[key]}, the pre-registration now "
                f"says {value} — a threshold moved after the numbers landed")
    return problems


def _arm(proven_per_session: int, created: list[int], facts: int, operator_edges: int,
         llm_calls: int = 21) -> dict:
    sessions = [
        {
            "session_id": f"wp{i:02d}_x",
            "created": {"extraction_nodes": value, "extraction_claims": value,
                        "objects": 0, "events": 0, "label_instances": value,
                        "points": value, "operator_edges": 0},
            "s3": {"proven": i < proven_per_session, "state": "resolved"},
            "llm_calls": llm_calls // 7,
            "ok": True,
        }
        for i, value in enumerate(created)
    ]
    return {
        "sessions": sessions,
        "real_facts": {"survived": facts, "total": 90},
        "totals": {"extraction_nodes": sum(created), "operator_edges": operator_edges,
                   "label_instances": sum(created)},
        "llm_calls": llm_calls,
    }


def test_arm_integrity_precedes_any_ratio(probe):
    """A degraded/suppressed-but-unproven arm must produce ARM-INVALID and NO ratio.

    This is the pre-registration's rule 1: a run whose ON lookup degraded measures the
    degradation (#3679), so a ratio computed from it would be a ratio about the wrong thing.
    """
    off = _arm(proven_per_session=6, created=[40] * 7, facts=60, operator_edges=10)
    on = _arm(proven_per_session=7, created=[10] * 7, facts=60, operator_edges=10)
    result = probe.decide(BARS, FLOORS, off, on)
    assert result["verdict"] == probe.VERDICT_ARM_INVALID
    assert result["ratio"] is None
    assert "13 of 14" in result["why"]


def test_the_ratio_near_one_is_falsified(probe):
    """The falsifier: telling extraction what exists does NOT reduce creates."""
    off = _arm(14, [40] * 7, facts=60, operator_edges=10)
    on = _arm(14, [40] * 7, facts=60, operator_edges=10)
    result = probe.decide(BARS, FLOORS, off, on)
    assert result["verdict"] == probe.VERDICT_FALSIFIED
    assert result["ratio"] == pytest.approx(1.0)
    assert result["primary_met"] is False


def test_rule_5_freezes_the_ratio_into_the_verdict_text(probe):
    """PRE-REGISTRATION rule 5 freezes `HYPOTHESIS-FALSIFIED (ratio X)`, not a bare token.

    Only the rule-4 branch (guard-violated) set `verdict_text`; the branch that ACTUALLY fired at
    this head — E1 not met, ratio ≈ 1.0 — set none, so the log emitted `VERDICT: HYPOTHESIS-FALSIFIED`
    with no number and no rule label (the committed log's line 58). A verdict mapping is only
    faithful if the text the document froze is the text the run prints.
    """
    off = _arm(14, [40] * 7, facts=60, operator_edges=10)
    on = _arm(14, [47] * 7, facts=60, operator_edges=10)
    result = probe.decide(BARS, FLOORS, off, on)
    assert result["verdict"] == probe.VERDICT_FALSIFIED
    assert result["verdict_text"] == "HYPOTHESIS-FALSIFIED (ratio 1.175)"


def test_primary_bar_met_with_guards_held_is_supported(probe):
    off = _arm(14, [100] * 7, facts=60, operator_edges=10)
    on = _arm(14, [50] * 7, facts=60, operator_edges=10)
    result = probe.decide(BARS, FLOORS, off, on)
    assert result["verdict"] == probe.VERDICT_SUPPORTED
    assert result["ratio"] == pytest.approx(0.5)


def test_guard_violation_falsifies_even_when_the_ratio_passes(probe):
    """The epic's tension rule: volume may not be bought by extracting less real content."""
    off = _arm(14, [100] * 7, facts=60, operator_edges=10)
    on = _arm(14, [50] * 7, facts=40, operator_edges=10)
    result = probe.decide(BARS, FLOORS, off, on)
    assert result["verdict"] == probe.VERDICT_FALSIFIED
    assert result["guard_facts"] is False
    assert "GUARD-VIOLATED" in result["verdict_text"]


def test_operator_guard_alone_also_falsifies(probe):
    off = _arm(14, [100] * 7, facts=60, operator_edges=10)
    on = _arm(14, [50] * 7, facts=60, operator_edges=4)
    result = probe.decide(BARS, FLOORS, off, on)
    assert result["verdict"] == probe.VERDICT_FALSIFIED
    assert result["guard_operators"] is False


def test_floors_refuse_rather_than_score(probe):
    """An under-powered OFF arm is REFUSED, never a ratio (and never a 0)."""
    off = _arm(14, [2] * 7, facts=60, operator_edges=10)
    on = _arm(14, [1] * 7, facts=60, operator_edges=10)
    result = probe.decide(BARS, FLOORS, off, on)
    assert result["verdict"] == probe.VERDICT_REFUSED
    assert result["ratio"] is None
    assert "floor" in result["why"]

    starved = _arm(14, [100] * 7, facts=5, operator_edges=10)
    result = probe.decide(BARS, FLOORS, starved, _arm(14, [50] * 7, 60, 10))
    assert result["verdict"] == probe.VERDICT_REFUSED
    assert "planted units" in result["why"]


def test_concentration_is_undefined_when_nothing_fell(probe):
    """A share of zero reduction is undefined — reported None, never a clean-looking 0."""
    assert probe.concentration([(10, 10), (10, 12)]) == {
        "share": None, "largest_session_reduction": 0, "total_reduction": 0}


def test_concentration_flags_one_session_carrying_the_result(probe):
    """The n=7 trap: one session supplying almost all of the reduction."""
    stats = probe.concentration([(100, 10), (10, 10), (10, 10)])
    assert stats["total_reduction"] == 90
    assert stats["share"] == pytest.approx(1.0)
    off = _arm(14, [100, 10, 10, 10, 10, 10, 10], facts=60, operator_edges=10)
    on = _arm(14, [10, 10, 10, 10, 10, 10, 10], facts=60, operator_edges=10)
    result = probe.decide(BARS, FLOORS, off, on)
    assert "SINGLE-SESSION-DRIVEN" in (result["qualifier"] or "")


def test_an_increase_cannot_subsidise_another_sessions_share(probe):
    stats = probe.concentration([(10, 40), (100, 10)])
    assert stats["total_reduction"] == 90
    assert stats["share"] == pytest.approx(1.0)


def test_outside_the_evidence_bundle_is_refused(probe):
    assert probe.inside_evidence(probe.EVIDENCE_DIR / "x.log")
    assert not probe.inside_evidence(ROOT / "docs/evidence/other/x.log")
    assert not probe.inside_evidence(Path("/tmp/x.log"))


def test_only_non_production_paths_count_as_nothing_changed(probe):
    """`docs/`, `tools/` and `tests/` are not production code; `tortoise/` is.

    The first cut allowed only `docs/evidence/` + `tools/`, which made the probe's own documented
    reproduce command REFUSE at its own PR head (the PR also touches `docs/architecture/` and
    `tests/`) — a refusal that would have been read as "production code changed" (review r1 P1).
    The corpus under `tests/eval/write_path/` is allowed here because it is hash-guarded instead.
    """
    assert probe.is_evidence_or_tool("docs/evidence/5087-hypothesis/x.log")
    assert probe.is_evidence_or_tool("docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md")
    assert probe.is_evidence_or_tool("tools/s3_prior_ablation.py")
    assert probe.is_evidence_or_tool("tests/test_s3_prior_ablation.py")
    assert not probe.is_evidence_or_tool("tortoise/extractor_v2.py")
    assert not probe.is_evidence_or_tool("config/routing.yml")


def test_bars_are_parsed_from_the_pins_block(probe):
    pins = probe.parse_pins((ROOT / probe.PREREG_REL).read_text())
    assert pins["hypothesis_bars"] == BARS
    assert pins["denominator_floors"] == FLOORS
    assert len(pins["source_sha"]) == 40
    # The bars the probe compares against are these, never literals in the module.
    source = PROBE.read_text()
    assert "primary_ratio_max = 0.75" not in source


def test_every_frozen_threshold_is_named_so_none_can_be_dropped_silently(probe):
    """A pins block that DROPS a key must not disable the comparison that key belongs to.

    `denominator_floors.sessions` was parsed and never checked, and `corroboration_min_sessions`
    was computed and never emitted — both found by review, both the same shape: the key existed, so
    the code looked complete. The sets are exact, so a removal is a red here rather than a quieter
    run.

    ⛔ EMITTED is not the same as COMPARED (#10468 review, obs 2). This docstring used to say every
    key here is compared by `decide()`; it is not — `sessions_total` is written into the log header
    and never branched on, because the session count a verdict gates on is
    `denominator_floors.sessions`. The partition below is read from `decide()`'s OWN SOURCE, so the
    claim cannot re-stale: a bar that starts, or stops, being compared reds here.
    """
    assert set(BARS) == {
        "primary_ratio_max", "guard_ratio_min", "corroboration_min_sessions", "sessions_total",
        "arm_integrity_required", "concentration_max", "cost_ratio_max"}, (
        "the pre-registration's bar set changed — classify every new key COMPARED or EMITTED-ONLY")
    assert set(FLOORS) == {"created_nodes_off", "real_facts_off", "sessions"}
    assert probe.BARS_LOG_ORDER == (
        "primary_ratio_max", "guard_ratio_min", "corroboration_min_sessions", "sessions_total",
        "arm_integrity_required", "concentration_max", "cost_ratio_max"), (
        "the log header must name every frozen bar, in the document's order")
    assert set(probe.BARS_LOG_ORDER) == set(BARS), (
        "a bar the log does not name is a bar the log cannot be held to")
    compared = {key for key in probe.BARS_LOG_ORDER if key in inspect.getsource(probe.decide)}
    assert compared == set(probe.BARS_LOG_ORDER) - {"sessions_total"}, (
        "`decide()` reads exactly six of the seven bars; `sessions_total` is emitted-only because "
        f"the session gate is `denominator_floors.sessions`. Found compared: {sorted(compared)}")


def test_the_measured_log_agrees_with_the_thresholds_it_names(probe):
    """The one anti-tuning check that needs no git history: the LOG versus the DOCUMENT.

    Moving a bar in the pins block — the docs-only change that flips the published verdict —
    now disagrees with the committed measured log, which recorded what the run actually compared
    against. An edit that also rewrites the log has to keep it a log (the one-measured-log rule,
    `capstone_preregistration_check`'s ordering rule and the RATIO/VERDICT lines all move with it).
    """
    log_text = (probe.EVIDENCE_DIR / MEASURED_LOG_REL).read_text()
    seen = _log_thresholds(log_text)
    assert {"primary_ratio_max", "guard_ratio_min", "arm_integrity_required",
            "concentration_max", "cost_ratio_max"} <= set(seen), (
        "the committed log must still name the bars the verdict was computed against")
    assert _threshold_mismatches(BARS, FLOORS, log_text) == []


def test_the_log_and_the_document_check_can_actually_fail(probe):
    """Non-vacuity: the exact wrong outcome r6 F1 described is a `0.75 -> 0.90` pins edit."""
    log_text = (probe.EVIDENCE_DIR / MEASURED_LOG_REL).read_text()
    moved_bars = {**BARS, "primary_ratio_max": 0.90, "guard_ratio_min": 0.80}
    moved_floors = {**FLOORS, "sessions": 6}
    problems = _threshold_mismatches(moved_bars, moved_floors, log_text)
    assert any("primary_ratio_max" in problem for problem in problems), problems
    assert any("guard_ratio_min" in problem for problem in problems), problems
    assert any("sessions" in problem for problem in problems), problems
    # …and the bars really do flip the published verdict on the measured numbers, which is why the
    # check has to exist. (`moved_floors` is excluded here: lowering `sessions` makes `decide()`
    # REFUSE the 7-session arm rather than score it — the floors are a different guard.)
    off = _arm(14, [45, 51, 43, 42, 34, 42, 27], facts=47, operator_edges=15)
    on = _arm(14, [39, 36, 34, 41, 35, 35, 27], facts=40, operator_edges=13)
    assert probe.decide(BARS, FLOORS, off, on)["verdict"] == probe.VERDICT_FALSIFIED
    assert probe.decide(moved_bars, FLOORS, off, on)["verdict"] == probe.VERDICT_SUPPORTED, (
        "0.8697 <= 0.90 with both guards held at 0.80 is exactly the verdict flip a two-line "
        "docs-only edit would buy")


def test_the_log_header_names_every_bar_and_floor(probe):
    """Five of seven bars were emitted before; `corroboration_min_sessions` decides a qualifier."""
    import ast

    bars_line = probe.bars_log_line(BARS)
    assert all(f"{key}=" in bars_line for key in BARS), f"missing bars in {bars_line!r}"
    floors_line = probe.floors_log_line(FLOORS)
    assert ast.literal_eval(floors_line.split(":", 1)[1].strip()) == FLOORS
    main_src = PROBE.read_text().split("def main(", 1)[1]
    assert "log(bars_log_line(bars))" in main_src and "log(floors_log_line(floors))" in main_src, (
        "the helpers must be what main emits — a helper beside an unchanged f-string pins nothing")


def test_the_machine_record_restates_every_frozen_bar(probe):
    """The half `_threshold_mismatches` delegates: the record's bars ARE the document's.

    Without this the two bars the log cannot hold — `corroboration_min_sessions` and
    `sessions_total` — would be held to nothing but the document digest, which a re-cut
    registration moves by design (#10468 review `f0b6842eb`, P2). The gate half (a record that
    moves, drops or invents a bar reds) lives in `tests/test_capstone_preregistration_9075.py`.
    """
    import json

    record = json.loads((probe.EVIDENCE_DIR / "preregistration.json").read_text())
    assert record["hypothesis_bars"] == BARS, (
        "the record the probe consumes must restate the document's bars character for character")


def test_ci_checks_this_bundle_not_only_the_capstone_one(probe):
    """r6 F1: no CI job read this bundle, so its ordering rule never executed anywhere.

    `.github/workflows/python-ci.yml` ran `capstone_preregistration_check.py --dir
    docs/evidence/7949-capstone` and nothing else, so for THIS bundle the ordering rule and the
    record↔pins cross-check did not run in any automated path — and a docs-only diff is exactly the
    change that can move a threshold after the numbers landed.
    """
    workflow = (ROOT / ".github/workflows/python-ci.yml").read_text()
    invocations = [line for line in workflow.splitlines()
                   if "tools/capstone_preregistration_check.py" in line]
    assert invocations, "the pre-registration gate must run in CI"
    assert any("--dir docs/evidence/5087-hypothesis" in line for line in invocations), (
        "CI must run the gate on THIS bundle: an ordering rule that no job executes is a rule that "
        f"does not exist. Found: {invocations}")


def test_a_missing_pins_block_refuses(probe):
    with pytest.raises(SystemExit):
        probe.parse_pins("no pins here")


def test_the_census_is_reused_not_reimplemented(probe):
    """#10463 forbids a second census: the probe must import capstone_measure's."""
    source = PROBE.read_text()
    assert "capstone_measure.py" in source
    assert "def census(" not in source
    assert "MATCH (n) UNWIND labels(n)" not in source


def test_witness_must_be_this_sessions_and_count_REAL_calls(probe):
    """Arm integrity cannot be inherited, arm-total, or counted from the STUB (r1 P1, r2 P1).

    An empty witness proves nothing however healthy the blob reads; ON needs at least one real call
    IN THIS SESSION; and OFF must be judged on calls to the REAL function — the OFF stub is invoked
    once per session, so a counter the stub increments makes every OFF session unprovable by
    construction (ARM-INVALID 0 of 7, always). The third argument is REAL calls, per session.
    """
    healthy = {"mode": "real", "degraded": False, "queries_run": 3}
    assert probe.witness_ok("ON", healthy, 1)
    assert not probe.witness_ok("ON", healthy, 0)
    assert not probe.witness_ok("ON", {}, 1)
    stub = {"mode": "real", "degraded": False, "queries_run": 0}
    assert probe.witness_ok("OFF", stub, 0)      # stub ran, real function did not: PROVEN
    assert not probe.witness_ok("OFF", stub, 1)  # the real function ran in the OFF arm: unproven
    assert not probe.witness_ok("OFF", {}, 0)


def test_the_session_floor_refuses(probe):
    """`denominator_floors.sessions` is a REFUSED floor, not decoration (review r1 P2)."""
    # The integrity bar is lowered so the SESSION floor is the thing under test: at the real bar
    # (14 of 14) a short arm is refused by integrity first, which is the correct precedence.
    off = _arm(14, [100] * 7, facts=60, operator_edges=10)
    on = _arm(14, [50] * 6, facts=60, operator_edges=10)   # short arm
    result = probe.decide(dict(BARS, arm_integrity_required=12), FLOORS, off, on)
    assert result["verdict"] == probe.VERDICT_REFUSED
    assert result["ratio"] is None


def test_unequal_arms_are_refused_rather_than_silently_truncated(probe):
    """The per-session table pairs the arms row by row, so a short arm must REFUSE.

    `zip` truncates to the shorter iterable, so a 7-session OFF arm against a 6-session ON arm
    printed a table that looked complete while the seventh session was simply absent from it. The
    refusal is exit 3 — an unmeasurable run, never 1, which is what a crash looks like.
    """
    off = _arm(14, [100] * 7, facts=60, operator_edges=10)
    on_short = _arm(14, [50] * 6, facts=60, operator_edges=10)
    assert len(probe.paired_sessions(off, off)) == 7
    with pytest.raises(SystemExit) as refusal:
        probe.paired_sessions(off, on_short)
    assert refusal.value.code == 3


def test_a_non_majority_result_is_qualified_not_silently_omitted(probe):
    """E2b is a registered QUALIFIER: 2 of 7 sessions falling must be said out loud."""
    off = _arm(14, [100, 100, 10, 10, 10, 10, 10], facts=60, operator_edges=10)
    on = _arm(14, [40, 40, 10, 10, 10, 10, 10], facts=60, operator_edges=10)
    result = probe.decide(BARS, FLOORS, off, on)
    assert result["corroboration_met"] is False
    assert "not-majority-of-sessions" in (result["qualifier"] or "")


def test_the_guard_floor_comes_from_the_bars(probe):
    """The guards compare against `guard_ratio_min`, never a hardcoded 1."""
    loose = dict(BARS, guard_ratio_min=0.5)
    off = _arm(14, [100] * 7, facts=60, operator_edges=10)
    on = _arm(14, [50] * 7, facts=40, operator_edges=5)
    assert probe.decide(BARS, FLOORS, off, on)["guard_facts"] is False
    assert probe.decide(loose, FLOORS, off, on)["guard_facts"] is True


def test_the_off_stub_never_increments_the_real_call_counter():
    """The exact cycle-2 regression: a stub-incremented counter makes OFF unprovable forever.

    Pinned on the SOURCE text because the counters live inside `run_arm`'s closures, which need a
    live graph to exercise. `off_stub` must not touch `real_session` — the OFF arm's proof is
    "the REAL function was never called", so a stub call is evidence of nothing.
    """
    import re

    source = PROBE.read_text()
    body = re.search(r"def off_stub\(.*?\n(?=    ev\.search_graph)", source, re.S)
    assert body, "off_stub not found — the OFF arm's behaviour moved"
    assert "real_session" not in body.group(0)
    assert 'calls["intercepted"] += 1' in body.group(0)


# ── the fresh-graph premise (review P2) ────────────────────────────────────
#
# The pre-registration's "FRESH disposable graph" (§0) was a premise no code checked. Every printed
# delta is `after - before`, so it stays arithmetically correct over a graph that already held
# Points — while `real_facts` grades the arm's WHOLE graph, so the E2a guard would be scored over a
# previous run's Points. `--graph-prefix` makes that reachable on purpose, and the default name
# (`s3ablate_<head8>_<epoch-second>`) collides when two runs start inside the same second.

#: A baseline census with every count 0 — a fresh disposable graph.
_ZERO_CENSUS = {"points": 0, "point_composition": {}, "per_label": {}, "label_counts": {},
                "operator_relationships": {}}


def _run_one_off_arm(probe, monkeypatch, census, counter: dict | None = None):
    """Drive `run_arm` with fakes: no graph, no key, no model call.

    ``counter`` (optional) is a caller-owned dict `{"sessions": n}` the fake SDK increments, so a
    test that expects a REFUSAL can still prove the ordering it asserts — the helper raises before
    returning its own copy, so a caller that could only read the returned dict could not check that
    no session was captured.
    """
    import tortoise.sdk as sdk_module

    captured = counter if counter is not None else {}
    captured.setdefault("sessions", 0)

    class FakeSDK:
        def __init__(self, graph_name=None):
            self.graph_name = graph_name

        def capture_session(self, turns, session_id=None):
            captured["sessions"] += 1
            return {"ok": True, "stats": {"llm": {"calls": 1}},
                    "search": probe.off_arm_stub("real")}

    monkeypatch.setattr(sdk_module, "TortoiseSDK", FakeSDK)
    monkeypatch.setattr(probe, "_turns", lambda sid: [{"role": "user", "content": "hi"}])
    monkeypatch.setattr(probe, "read_point_contents", lambda uri: [])
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:pw@localhost:6379/tortoise_test_matrix")
    lines: list[str] = []
    return probe.run_arm("OFF", "g", ["wp01"], census, {}, lines.append), captured, lines


def test_a_reused_graph_is_refused_before_any_session_is_captured(probe, monkeypatch, capsys):
    """A non-zero baseline must REFUSE (exit 3) — not merely be printed.

    Behavioral, not a source poke: the fake census reports 12 Points already in the arm's graph, so
    the pre-fix code (which read the baseline and asserted nothing) proceeded to capture the
    session and score it; the fixed code refuses first, and no model call happens.
    """
    reused = dict(_ZERO_CENSUS, points=12)
    captured: dict = {}
    with pytest.raises(SystemExit) as refusal:
        _run_one_off_arm(probe, monkeypatch, lambda uri: reused, counter=captured)
    assert refusal.value.code == 3, "a reused graph is a REFUSAL (3), never a crash (1)"
    assert "is NOT fresh" in capsys.readouterr().err
    assert captured["sessions"] == 0, (
        "the baseline must be asserted BEFORE the first capture — a refusal that still ran a "
        "session has already written into the reused graph")


def test_a_fresh_graph_prints_the_zero_baseline_it_ran_on(probe, monkeypatch, capsys):
    """A fresh graph runs, and the log carries the baseline it was measured against."""
    result, captured, lines = _run_one_off_arm(probe, monkeypatch, lambda uri: dict(_ZERO_CENSUS))
    assert captured["sessions"] == 1
    assert result["totals"]["extraction_nodes"] == 0
    baseline = [line for line in lines if "graph baseline" in line]
    assert baseline, f"the log must print the baseline census, got: {lines}"
    assert "every count 0" in baseline[0], baseline[0]


def test_the_integrity_log_counts_the_sessions_the_arm_actually_ran(probe):
    """The line was hardcoded `/7` — a denominator no arm produced."""
    def arm(sessions: int, proven: int, calls: int) -> dict:
        return {"real_search_calls": calls,
                "sessions": [{"s3": {"proven": index < proven}} for index in range(sessions)]}

    line = probe.arm_integrity_line(arm(7, 7, 47), arm(7, 7, 40))
    assert "OFF 7/7 proven" in line and "ON 7/7 resolved" in line, line
    shrunk = probe.arm_integrity_line(arm(3, 2, 5), arm(3, 3, 5))
    assert "OFF 2/3 proven" in shrunk and "ON 3/3 resolved" in shrunk, shrunk
    assert "/7 proven" not in PROBE.read_text(), (
        "the integrity line must interpolate the arm's session count, never a literal")


# ── the metric-definition pin (review r3 P2a) ──────────────────────────────
#
# The path allowlist answers "is this production code?". It does not answer "is this the code that
# COMPUTED the number?", and the three artifacts that do are all under an allowed prefix: the census
# (`created_new_nodes`, `operator_edges`), `grading.macro_survival_counts` (`real_facts`) and
# `corpus.compute_fixtures_hash` (the corpus guard itself). A prefix allowlist alone lets those
# differ between the pinned `source_sha` and `HEAD` without a refusal — a re-defined metric beside an
# unchanged log, which reads as an unchanged measurement.


def test_the_code_that_computes_the_numbers_is_pinned_too(probe):
    """The allowlist admits them; the metric pin must still refuse them."""
    for rel in (
        "docs/evidence/7949-capstone/probes/capstone_measure.py",
        "tests/eval/write_path/grading.py",
        "tests/eval/write_path/corpus.py",
    ):
        assert probe.is_evidence_or_tool(rel), f"{rel}: the allowlist alone would admit this"
        assert probe.is_metric_artifact(rel), f"{rel}: the metric pin must refuse this"
    # …and it must not refuse everything: the probe, the log and the docs are not metrics.
    for rel in (
        "tools/s3_prior_ablation.py",
        "docs/evidence/5087-hypothesis/README.md",
        "docs/evidence/5087-hypothesis/withdrawn/s3-ablation-a1075dfc.log",
        "tests/test_s3_prior_ablation.py",
        "tortoise/extractor_v2.py",
    ):
        assert not probe.is_metric_artifact(rel), rel


def _git(repo, *args):
    proc = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True,
                          check=False)
    assert proc.returncode == 0, f"git {args} failed: {proc.stderr}"
    return proc.stdout.strip()


def test_a_changed_metric_artifact_refuses_the_run(probe, monkeypatch, tmp_path):
    """Behavioural, not a source poke: a moved metric definition must exit 3 (REFUSED).

    Two temp repos, same shape, one variable: the second commit touches a metric artifact in the
    first and an ordinary non-production file in the second. The first must refuse — that is the
    guarantee the README states — and the second must be admitted, so the guard is not refusing
    everything that is merely under an allowed prefix.
    """
    monkeypatch.setenv("GIT_AUTHOR_NAME", "probe-test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "probe-test@example.invalid")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "probe-test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "probe-test@example.invalid")

    def build(name: str, touched: str) -> tuple[Path, str, str]:
        repo = tmp_path / name
        for rel in ("tools", "tests/eval/write_path", "docs/evidence/7949-capstone/probes"):
            (repo / rel).mkdir(parents=True, exist_ok=True)
        (repo / "tools/s3_prior_ablation.py").write_text("# probe\n")
        (repo / "tests/eval/write_path/grading.py").write_text("REAL = 1\n")
        (repo / "tests/eval/write_path/corpus.py").write_text("HASH = 1\n")
        (repo / "docs/evidence/7949-capstone/probes/capstone_measure.py").write_text("C = 1\n")
        _git(repo, "init", "-q", "-b", "main")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "baseline")
        source_sha = _git(repo, "rev-parse", "HEAD")
        target = repo / touched
        target.parent.mkdir(parents=True, exist_ok=True)
        prior = target.read_text() if target.exists() else ""
        target.write_text(prior + "\n# moved after the pin\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "move one file")
        return repo, source_sha, _git(repo, "rev-parse", "HEAD")

    metric_repo, source_sha, head = build("metric", "tests/eval/write_path/grading.py")
    monkeypatch.setattr(probe, "ROOT", metric_repo)
    with pytest.raises(SystemExit) as refusal:
        probe.assert_source_sha(source_sha, head)
    assert refusal.value.code == 3, "a moved metric artifact is an unmeasurable run, never a crash"

    other_repo, source_sha, head = build("other", "tools/another_probe.py")
    monkeypatch.setattr(probe, "ROOT", other_repo)
    assert probe.assert_source_sha(source_sha, head) == ["tools/another_probe.py"]


# ── the log has to survive the run (review r4 P2-3) ────────────────────────
#
# The measured run's OUTPUT — not only its definition — has to be storable without the process's
# cooperation. The pre-fix probe buffered every line and wrote the file once, at the end, so an
# abort mid-run left no file at all. That is exactly what happened to replay A: its per-session
# noise distribution exists nowhere, which is why the bundle's ±31 % band rests on two quoted pairs.


def test_an_aborted_run_leaves_the_sessions_it_already_measured(probe, tmp_path):
    """Behavioural: lines are on DISK before the run finishes, without closing the handle.

    The `try/except` below stands in for the abort (replay A died on `arm["arm"]` indexing a string
    mid-run). Nothing closes the handle, so a file that only gets its body at exit reads empty here.
    """
    path = tmp_path / "nested" / "s3-ablation.log"
    handle, write_line = probe.open_log(path)
    try:
        write_line("--- ARM OFF — fresh graph s3ablate_test_off ---")
        write_line("  [OFF] created extraction nodes: 45")
        raise RuntimeError("the run aborted mid-arm")
    except RuntimeError:
        pass
    assert path.read_text().splitlines() == [
        "--- ARM OFF — fresh graph s3ablate_test_off ---",
        "  [OFF] created extraction nodes: 45",
    ], "an abort must leave the completed sessions behind, not an empty or missing log"
    handle.close()


def test_the_log_is_not_written_once_at_the_end():
    """The wiring, pinned on the source: `main`'s log() must go through the incremental writer.

    The writer lives in `main`'s closure and needs a live graph and a model key to exercise, so the
    end-of-run-only write is pinned here — that single one-shot write IS the defect (r4 P2-3): it is
    the reason an aborted run leaves nothing at all.
    """
    import re

    source = PROBE.read_text()
    assert not re.search(r"out_path\.write_text\(", source), (
        "a one-shot write at the end loses every measured session")
    assert "open_log(out_path)" in source, "the log must be opened before the arms run"


# ── the documented reproduce path must be executable where it is documented (r4 P2-2) ──
#
# `is_evidence_or_tool` refuses anything outside `docs/`, `tools/`, `tests/`, so the `## Reproduce`
# command is admitted only at a head whose diff from the pinned `source_sha` touches nothing else.
# `config/` moves on nearly every merge — this PR touches two of those files itself — so at the head
# the bundle lands on, the FIRST command a reader types refused at exit 3 and read as a broken
# checkout. The docs and the pin have to agree about that, and the agreement has to be pinned.

#: The head the measured run was taken at: the commit `--prereg` names, which is also the `head=`
#: the committed log prints. `718ee3b` (the pins' `source_sha`) is its parent-side pin.
MEASURED_HEAD = "a1075dfc2"


def test_the_reproduce_section_states_the_head_its_pin_admits(probe):
    readme = (probe.EVIDENCE_DIR / "README.md").read_text()
    assert "## Reproduce" in readme, "the bundle must document how to reproduce it"
    section = readme.split("## Reproduce", 1)[1]
    assert MEASURED_HEAD in section, (
        "the reproduce command is admitted only at the head its `source_sha` pins — the section "
        "must name it (r4 P2-2)")
    assert "config/" in section and "exit 3" in section, (
        "the section must say that a later head where non-evidence files (e.g. `config/`) moved is "
        "REFUSED at exit 3 BY DESIGN — otherwise the refusal reads as a broken checkout")


def test_the_admission_check_is_a_necessary_condition_not_the_whole_pin(probe):
    """r6 F2 found the section demanding an EMPTY diff; this is the SAME defect one axis over.

    `is_evidence_or_tool` admits `docs/`, `tools/` and `tests/`, so at `a1075dfc2` — the ONE head
    the section declared admissible — the unrestricted diff prints three files (the same three the
    log's `source_sha..head changed files` line records). A reader following the section in order
    saw non-empty output at the only "admitted" head and concluded the bundle was stale or that
    reproduction needed a new pre-registration, for a head the probe in fact admits.

    The check then carried the pin's allowlist as a pathspec and the section said **empty output
    means the pin admits this head** — which is FALSE, because the allowlist is only ONE of the two
    rules `assert_source_sha` applies. `METRIC_ARTIFACT_PREFIXES` is strictly stronger and BOTH of
    its prefixes sit INSIDE the excluded `docs/`/`tests/` prefixes, so a head that moved only
    `grading.py` printed nothing at the documented check and the probe still refused at `exit 3`:
    the section sent a reader into a refusal by following its own advice.

    So the prose now says NECESSARY, and this test compares the pathspec **and both prefix tuples**
    so the two cannot drift on that axis again (the pathspec alone can never express the second).
    """
    import re

    readme = (probe.EVIDENCE_DIR / "README.md").read_text()
    section = readme.split("## Reproduce", 1)[1]
    commands = [line for line in section.splitlines()
                if line.startswith("git diff --name-only 718ee3b")]
    assert len(commands) == 1, f"expected exactly ONE admission command, got {commands}"
    excluded = {name.rstrip("/") for name in re.findall(r"':!([^']+)'", section)}
    assert excluded == {prefix.rstrip("/") for prefix in probe.EVIDENCE_OR_TOOL_PREFIXES}, (
        "the admission check's pathspec must be EXACTLY the pin's allowlist "
        f"({list(probe.EVIDENCE_OR_TOOL_PREFIXES)}): a check excluding more than the pin does "
        "reports a broken bundle at a head the pin admits, and one excluding less reports a "
        "refusal the pin will not make")
    assert "NECESSARY but not sufficient" in section, (
        "empty pathspec output is a NECESSARY condition: the pin also refuses any movement under "
        "METRIC_ARTIFACT_PREFIXES, which the pathspec cannot see")
    for prefix in probe.METRIC_ARTIFACT_PREFIXES:
        assert prefix.rstrip("/") in section, (
            f"the section must name the stricter rule's prefix {prefix!r}: it is the axis the "
            "documented pathspec cannot express, so a reader is sent to a refusal without it")


# ── the documented lane command must be one that RUNS (r9 P3) ────────────────
#
# The section used to open with `uv run python tools/test_lane.py run -- true`, described as "a private
# FalkorDB lane". `run` is the PYTEST entry point: it strips the leading `--`, finds no test target in
# `["true"]`, PREPENDS `tests/` and invokes `python -m pytest tests/ true` — pytest then exits 4 with
# `file or directory not found: true` while the lane container is up. It also applies the three seam
# variables to the CHILD only, so it can never populate the shell that the following `export` line was
# written for. The two tests below pin the section's shape and MEASURE the defect it would reintroduce.


def _test_lane():
    """The lane tool as a module — same loader shape as `_probe()`, no subprocess needed."""
    spec = importlib.util.spec_from_file_location("_test_lane_repro", ROOT / "tools/test_lane.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_reproduce_section_starts_the_lane_and_exports_its_seam(probe):
    """The documented path must start a lane AND put the seam in the caller's shell.

    `run` does the first and not the second, so the section uses the tool's own documented two-step
    form (`uri` then `eval`), which is the only spelling that does both. r9 P3.
    """
    readme = (probe.EVIDENCE_DIR / "README.md").read_text()
    section = readme.split("## Reproduce", 1)[1]
    assert "test_lane.py run --" not in section, (
        "`run` executes PYTEST on the rest of the argv — it cannot start-and-export for a probe")
    assert 'uri="$(uv run python tools/test_lane.py uri)"' in section, section
    assert 'eval "$uri"' in section, "the seam variables must reach THIS shell, not a child"
    assert "test_lane.py down" in section, "a lane left up is the residue the tool exists to bound"


def test_a_lane_command_with_no_pytest_target_exits_4():
    """MEASURED, not asserted from prose: `["true"]` is not a target and `tests/ true` is exit 4.

    This is the failure the section used to open with, reproduced with the tool's own predicate and
    the same argv `cmd_run` builds for it (`[sys.executable, "-m", "pytest", "tests/", "true"]`).
    pytest rejects the bogus path at argument resolution — no collection, no Docker, 0.5 s.
    """
    lane = _test_lane()
    assert not lane._argv_names_a_target(["true"]), (
        "if `true` were a target the tool would not prepend `tests/` and the defect would not exist")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "true", "-q", "-p", "no:cacheprovider"],
        cwd=str(ROOT), capture_output=True, text=True, check=False)
    assert proc.returncode == 4, (proc.returncode, proc.stdout, proc.stderr)
    assert "file or directory not found: true" in (proc.stdout + proc.stderr), proc.stdout


def test_a_metric_artifact_move_is_invisible_to_the_documented_pathspec(probe):
    """The claim in `## Reproduce`, measured with the module's own two predicates.

    Both halves are properties the tests must not re-derive by inspection: every metric artifact is
    (a) under a prefix the pinned allowlist ADMITS — so the documented pathspec, which excludes
    `docs/`, `tools/` and `tests/`, prints nothing for it — and (b) under a prefix the pin REFUSES,
    which is why the section cannot say "empty output means admitted".
    """
    for prefix in probe.METRIC_ARTIFACT_PREFIXES:
        assert prefix.startswith(probe.EVIDENCE_OR_TOOL_PREFIXES), (
            f"{prefix!r} is inside the allowlist, which is exactly why the pathspec cannot see it")
    for path in ("docs/evidence/7949-capstone/probes/capstone_measure.py",
                 "tests/eval/write_path/grading.py",
                 "tests/eval/write_path/corpus.py"):
        assert probe.is_evidence_or_tool(path), (
            f"{path} is under docs/ or tests/, so the documented pathspec prints nothing for it")
        assert probe.is_metric_artifact(path), (
            f"{path} computes a reported number, so the pin refuses its movement at exit 3")


def test_a_later_head_that_moved_a_config_file_refuses_the_pin(probe, monkeypatch, tmp_path):
    """The rule the docs now state is the rule the code enforces (temp repos, one variable).

    `config/` is not production extraction code, but it is outside the allowlist, so the pin refuses
    it: the run must be reproduced at the pinned head. Pinning the behaviour keeps the docs honest.
    """
    monkeypatch.setenv("GIT_AUTHOR_NAME", "probe-test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "probe-test@example.invalid")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "probe-test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "probe-test@example.invalid")

    def build(name: str, touched: str) -> tuple[Path, str, str]:
        repo = tmp_path / name
        (repo / "tools").mkdir(parents=True, exist_ok=True)
        (repo / "config").mkdir(parents=True, exist_ok=True)
        (repo / "tools/s3_prior_ablation.py").write_text("# probe\n")
        (repo / "config/ci-surfaces.yml").write_text("core: []\n")
        _git(repo, "init", "-q", "-b", "main")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "baseline")
        source_sha = _git(repo, "rev-parse", "HEAD")
        target = repo / touched
        target.parent.mkdir(parents=True, exist_ok=True)
        prior = target.read_text() if target.exists() else ""
        target.write_text(prior + "# moved after the pin\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "move one file")
        return repo, source_sha, _git(repo, "rev-parse", "HEAD")

    config_repo, source_sha, head = build("config", "config/ci-surfaces.yml")
    monkeypatch.setattr(probe, "ROOT", config_repo)
    with pytest.raises(SystemExit) as refusal:
        probe.assert_source_sha(source_sha, head)
    assert refusal.value.code == 3

    docs_repo, source_sha, head = build("docs", "tools/another_probe.py")
    monkeypatch.setattr(probe, "ROOT", docs_repo)
    assert probe.assert_source_sha(source_sha, head) == ["tools/another_probe.py"]


# ── the write-back must not leave the doc contradicting its own measurement (r4 P2-1) ──


def test_the_architecture_doc_no_longer_calls_the_question_unmeasured(probe):
    """§10 reported the falsification, but §4.1's note still pointed at §14 as "still unmeasured".

    `#10463`'s contract says a falsified hypothesis is never re-run until it agrees, so the design
    doc is what has to settle it: :548 routed the next reader to :1758's "UNMEASURED", i.e. to the
    state that produced this lane. Both must carry the figure now, and §14 must keep the half that
    really is still open (S3's disambiguation accuracy).
    """
    doc = (ROOT / "docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md").read_text()
    assert "**Still unmeasured (§14):** whether giving extraction the neighbourhood" not in doc, (
        "§4.1's note still sends the reader to §14 for an open question this PR measured")
    assert "Whether the neighbourhood lookup changes extraction's output is UNMEASURED" not in doc, (
        "§14 still declares UNMEASURED what §10 reports as measured")
    assert "284" in doc and "247" in doc, "the measured figures must live where the claim was"
    assert "whether S3's disambiguation is accurate" in doc, (
        "the half that really IS still unmeasured must survive the edit")


# ── the pin must see the WORKING TREE, and the log must name the bytes that ran (r5 P1) ──
#
# `assert_source_sha` compares two COMMITS, so an uncommitted edit to production code, to a metric
# artifact or to the probe itself was admitted silently while the log still asserted that the pinned
# sha was the tree that ran. The measured run is an instance of that hole: its `head=` line names
# `a1075dfc2`, while the `S3 WITNESS` line it prints does not exist in the probe at that commit
# (it was an uncommitted working copy whose content equals `325f2a1a5`). So the tree is now asserted
# clean over exactly the paths that can move a number, and the log carries the probe's own sha256 —
# which names the bytes that ran whether or not they were ever committed.


def test_an_uncommitted_edit_refuses_the_run(probe, monkeypatch, tmp_path):
    """Behavioural, temp repo, one variable: clean is admitted, a dirty guarded path is exit 3.

    A dirty tree is refused because a commit id cannot certify it; a dirty path OUTSIDE the guarded
    set (the evidence bundle, `config/`) is not refused, so the guard is not simply refusing
    everything that is uncommitted.
    """
    monkeypatch.setenv("GIT_AUTHOR_NAME", "probe-test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "probe-test@example.invalid")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "probe-test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "probe-test@example.invalid")

    repo = tmp_path / "dirty"
    for rel in ("tortoise", "tests/eval/write_path", "docs/evidence/7949-capstone/probes", "tools",
                "docs/evidence/5087-hypothesis", "config"):
        (repo / rel).mkdir(parents=True, exist_ok=True)
    (repo / "tools/s3_prior_ablation.py").write_text("# probe\n")
    (repo / "tortoise/extractor_v2.py").write_text("SEARCH = 1\n")
    (repo / "tests/eval/write_path/grading.py").write_text("REAL = 1\n")
    (repo / "docs/evidence/7949-capstone/probes/capstone_measure.py").write_text("C = 1\n")
    (repo / "docs/evidence/5087-hypothesis/PRE-REGISTRATION.md").write_text("# prereg\n")
    (repo / "config/ci-surfaces.yml").write_text("core: []\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "baseline")
    monkeypatch.setattr(probe, "ROOT", repo)

    assert probe.dirty_paths() == [], "a committed tree must satisfy the guard"
    assert probe.assert_clean_tree(), "a clean tree returns the `tree=` value the log prints"

    # An uncommitted edit to the production extraction code — the case the commit diff cannot see.
    (repo / "tortoise/extractor_v2.py").write_text("SEARCH = 2  # tuned after the pin\n")
    with pytest.raises(SystemExit) as refusal:
        probe.assert_clean_tree()
    assert refusal.value.code == 3, "a dirty tree is an unmeasurable run, never a crash"

    # The same edit to a metric artifact, and to the probe itself, are refused too.
    for rel in ("tests/eval/write_path/grading.py", "tools/s3_prior_ablation.py"):
        _git(repo, "checkout", "--", "tortoise/extractor_v2.py")
        target = repo / rel
        target.write_text(target.read_text() + "# tuned after the pin\n")
        with pytest.raises(SystemExit) as refusal:
            probe.assert_clean_tree()
        assert refusal.value.code == 3, f"{rel}: a dirty metric artifact must refuse"
        _git(repo, "checkout", "--", rel)

    # …but an uncommitted change the run cannot measure through is NOT refused, so the guard stays
    # a dirty-CODE guard rather than a blanket "commit before you run".
    # ⚠️ The DOCUMENT below is the admitted case on purpose and it is not the record: the bars come
    # out of a COMMIT (`git show <sha>:…`), so a dirty copy of it cannot move one. Its neighbour
    # `preregistration.json` is NOT in this case — that file is read from the working tree and
    # decides which document the bars come from, so it IS guarded
    # (`test_an_uncommitted_record_repoints_the_bars_and_is_refused`).
    (repo / "docs/evidence/5087-hypothesis/PRE-REGISTRATION.md").write_text("# prereg edited\n")
    (repo / "config/ci-surfaces.yml").write_text("core: ['x']\n")
    assert probe.dirty_paths() == [], (
        "an uncommitted copy of the pre-registration or of a config file cannot move a number — "
        "the bars are parsed out of a commit — and must not refuse the run")
    status = _git(repo, "status", "--porcelain")
    assert "config/ci-surfaces.yml" in status and "PRE-REGISTRATION.md" in status, (
        "the repo really IS dirty there — so the empty guard above is the paths, not a clean tree")
    assert probe.assert_clean_tree()


# ── the record is guarded, and `--prereg` is bound to this tree's history (review P2) ────────
#
# Two files sit in the same bundle and are NOT the same kind of thing. `PRE-REGISTRATION.md` is read
# out of a COMMIT (`git show <sha>:<rel>`), so an uncommitted copy of it cannot move a bar and must
# not refuse a run. `preregistration.json` is read out of the WORKING TREE, and its
# `document_sha256` is what decides WHICH document the bars may come from — so an uncommitted edit to
# it re-points the whole chain at a friendlier argument. That was the residual tuning path the pin
# could not see: edit the record (uncommitted), point `--prereg` at any commit carrying the friendlier
# document (an unreferenced `git commit-tree` object is enough), and every guard passes while the
# committed bundle never moves — invisible to CI, which compares the WORKTREE document's add-epoch
# and reads a log's `prereg=` only to spot `UNPREREGISTERED`.


def test_an_uncommitted_record_repoints_the_bars_and_is_refused(probe, monkeypatch, tmp_path):
    """The record is guarded; the document beside it deliberately is not.

    One temp repo, one variable: the same uncommitted edit to the DOCUMENT is admitted (the bars come
    from the commit, so it cannot move a number) while the edit to the RECORD refuses. Both halves
    are asserted, so the fix is not "guard the whole bundle" — which would refuse the run for a docs
    edit the pin provably cannot see.
    """
    monkeypatch.setenv("GIT_AUTHOR_NAME", "probe-test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "probe-test@example.invalid")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "probe-test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "probe-test@example.invalid")

    repo = tmp_path / "record"
    (repo / "docs/evidence/5087-hypothesis").mkdir(parents=True)
    (repo / "docs/evidence/5087-hypothesis/PRE-REGISTRATION.md").write_text("# prereg\n")
    (repo / "docs/evidence/5087-hypothesis/preregistration.json").write_text('{"a": 1}\n')
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "baseline")
    monkeypatch.setattr(probe, "ROOT", repo)

    assert probe.dirty_paths() == [], "a committed bundle must satisfy the guard"

    # The DOCUMENT is read out of a commit, so a dirty working copy of it cannot move a bar.
    (repo / "docs/evidence/5087-hypothesis/PRE-REGISTRATION.md").write_text("# friendlier bars\n")
    assert probe.dirty_paths() == [], (
        "the pre-registration is read via `git show <sha>:…`, so an uncommitted copy cannot change "
        "the bars and must not refuse the run")
    assert probe.assert_clean_tree()

    # The RECORD is read out of the WORKING TREE and arbitrates which document is admissible.
    (repo / "docs/evidence/5087-hypothesis/preregistration.json").write_text(
        '{"document_sha256": "sha256:' + "f" * 64 + '"}\n')
    assert "preregistration.json" in "\n".join(probe.dirty_paths()), (
        "the record is read from the working tree, so it must be inside WORKTREE_GUARDED_PATHS")
    with pytest.raises(SystemExit) as refusal:
        probe.assert_clean_tree()
    assert refusal.value.code == 3, (
        "an uncommitted record re-points which document the bars come from — an unmeasurable run, "
        "never a crash, and never admitted")


def test_the_prereg_commit_must_belong_to_this_trees_history(probe, monkeypatch, tmp_path):
    """`--prereg` was bound to nothing; now a ref must contain it AND it must precede `head`.

    One temp repo: `base` (the document), `head` (on top of it) and a later commit on top of that
    (a DESCENDANT). Plus a dangling object minted with `git commit-tree` that no ref points at. The
    admitted case and every refusal are measured, so the binding is not refusing everything.
    """
    monkeypatch.setenv("GIT_AUTHOR_NAME", "probe-test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "probe-test@example.invalid")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "probe-test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "probe-test@example.invalid")

    repo = tmp_path / "binding"
    (repo / "docs/evidence/5087-hypothesis").mkdir(parents=True)
    doc = repo / "docs/evidence/5087-hypothesis/PRE-REGISTRATION.md"
    doc.write_text("# prereg\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "the pre-registration")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "tools").mkdir()
    (repo / "tools/note.txt").write_text("head\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "the head being measured")
    head = _git(repo, "rev-parse", "HEAD")
    monkeypatch.setattr(probe, "ROOT", repo)

    # 1. A commit that precedes `head` and that a ref carries — the documented reproduce shape.
    assert probe.assert_prereg_binding(base, head) == base, (
        "the recorded reproduce command passes the pre-registration's own commit; it must be "
        "admitted, and resolved to a full sha")

    # 2. A DESCENDANT of `head`: the plan would post-date the run it arbitrates.
    doc.write_text("# a friendlier document, cut later\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "after the numbers were in")
    later = _git(repo, "rev-parse", "HEAD")
    with pytest.raises(SystemExit) as refusal:
        probe.assert_prereg_binding(later, head)
    assert refusal.value.code == 3

    # 3. A DANGLING commit — the cheapest way to make a hostile document exist. It carries the
    #    document, `git show` reads it happily, and no branch, tag or remote ever carried it.
    tree = _git(repo, "rev-parse", f"{later}^{{tree}}")
    dangling = _git(repo, "commit-tree", tree, "-m", "unreferenced")
    assert _git(repo, "for-each-ref", "--contains", dangling) == "", (
        "the dangling object must really be unreachable, or this case proves nothing")
    with pytest.raises(SystemExit) as refusal:
        probe.assert_prereg_binding(dangling, head)
    assert refusal.value.code == 3, (
        "a commit no ref contains holds a document that never preceded this run; `git show` would "
        "read it happily, which is why the binding has to refuse it")

    # 4. …and an unresolvable `--prereg` is the module's own refusal (exit 3), never a traceback.
    with pytest.raises(SystemExit) as refusal:
        probe.assert_prereg_binding("deadbeef" * 5, head)
    assert refusal.value.code == 3, "an unreadable `--prereg` is a refusal, not a traceback"

    assert "assert_prereg_binding(" in PROBE.read_text().split("def main(", 1)[1], (
        "the binding has to be wired into main — `git show` reads any commit the repository can "
        "resolve, so an available function beside it binds nothing")


def test_the_bundle_discloses_the_identity_binding_it_now_enforces(probe):
    """§4 registers that deviations are reported; this one must not live only in the code.

    The record's guard and the `--prereg` binding are the guarantee this bundle's anti-tuning
    argument rests on, so a reader has to be able to find them stated — and stated as what they do
    NOT establish about the measured run, which predates both.
    """
    flat = " ".join((probe.EVIDENCE_DIR / "README.md").read_text().split())
    assert "read from the **working tree**" in flat, (
        "the bundle must say WHY the record is guarded and the document beside it is not — they "
        "are not the same kind of file")
    assert "`assert_prereg_binding` refuses a `--prereg` commit that no ref contains" in flat, (
        "the binding must be described where a reader looks for the probe's refusals")
    assert "it was taken *before* either guard existed" in flat, (
        "and it must say what that does NOT establish about the run already on record")


def test_the_log_names_the_probe_bytes_not_only_a_commit(probe):
    """`head=` names a commit and a commit cannot name a working copy; `probe_sha256` can."""
    import hashlib

    assert probe.probe_sha256() == "sha256:" + hashlib.sha256(PROBE.read_bytes()).hexdigest()
    main_src = PROBE.read_text().split("def main(", 1)[1]
    assert "probe_sha256()" in main_src, "main must compute the probe's own hash"
    assert 'log(f"probe_sha256=' in main_src, (
        "the log must carry the hash of the bytes that ran — the producing revision is otherwise "
        "only reconstructible from a README note (r5 P1)")
    assert 'log(f"tree={tree}' in main_src, (
        "the log must print the working-tree state beside the changed-file line, so it can never "
        "read as a clean-tree assertion")


# ── `--overwrite` may replace an aborted run and nothing else (r5 P2) ──
#
# The flag existed for replay A (an aborted, unmeasured log). Nothing inspected the existing file,
# so the same flag truncated a log carrying `RATIO:`/`VERDICT:` — the "re-run until it is
# friendlier" path the one-log refusal exists to close, one flag away. An aborted run cannot print
# either line, so the FILE decides.


def test_overwrite_cannot_replace_a_measured_log(probe, monkeypatch, tmp_path):
    monkeypatch.setattr(probe, "ROOT", tmp_path)
    path = tmp_path / "docs/evidence/5087-hypothesis/s3-ablation-test.log"
    path.parent.mkdir(parents=True)

    assert probe.out_path_refusal(path, overwrite=False) is None, "an absent log is never refused"

    path.write_text("--- ARM OFF — fresh graph x ---\n  [OFF] wp01_quarry_debug: created=45\n")
    assert probe.out_path_refusal(path, overwrite=False) is not None, (
        "an existing log is refused without --overwrite")
    assert probe.out_path_refusal(path, overwrite=True) is None, (
        "an aborted, unmeasured run must stay replaceable — that is what the flag is for")

    path.write_text("--- ARM OFF ---\nRATIO: 0.8697\nVERDICT: HYPOTHESIS-FALSIFIED\n")
    refusal = probe.out_path_refusal(path, overwrite=True)
    assert refusal is not None and "MEASURED" in refusal, (
        "the flag must not truncate a log that carries a verdict — that is the tuning path")

    assert "out_path_refusal(" in PROBE.read_text().split("def main(", 1)[1], (
        "the rule has to be wired into main, not merely available beside it")


def test_the_bundle_states_the_overwrite_rule_the_probe_enforces(probe):
    """README:168 claimed the probe "refuses to overwrite the log at all" — it refuses a MEASURED one."""
    readme = (probe.EVIDENCE_DIR / "README.md").read_text()
    assert "refuses to overwrite the log at all" not in readme, (
        "the probe refuses a log that carries a verdict; --overwrite still replaces an aborted run")
    assert "--overwrite" in readme and "RATIO" in readme, (
        "the README must state the rule the code enforces, and name what marks a log as measured")


def test_the_bundle_says_the_measured_run_came_from_an_uncommitted_probe(probe):
    """r5 P1: the log's `head=` is the commit; the producing probe was an uncommitted working copy.

    The bundle said the run was produced by `tools/s3_prior_ablation.py` "as committed in
    `325f2a1a5`", which would mean a clean tree at a commit that does not contain the `S3 WITNESS`
    line the log prints. It has to state the weaker, true thing — and with the new guards, why that
    cannot happen again.
    """
    readme = (probe.EVIDENCE_DIR / "README.md").read_text()
    assert "as committed in `325f2a1a5`" not in readme, (
        "the measured run's log prints a line its `head=` commit does not contain: the producing "
        "working copy was uncommitted")
    assert "uncommitted" in readme, "the bundle must say plainly that the probe was uncommitted"


# ── the bundle's prose about its own refusals must be true of the code (r7 F2) ─────────────
#
# The Reproduce section said "Every refusal happens **before the log file is created**, so a refused
# run leaves nothing behind". That holds for the pin / pins / record / `--out` / prerequisite
# refusals and is FALSE for three that can only be reached once the arms have run: `_turns()` (a
# fixture carrying no conversation), `paired_sessions()` (two arms of unequal length) and `main`'s
# single-arm refusal. Each exits 3 leaving the sessions already measured on disk — which is the
# incremental writer doing exactly its job, and which the same section says two paragraphs earlier.
# The sentence is the one a reader uses to decide a refusal is side-effect free, so the prose and
# the behaviour are both pinned here: the first assertion fails on the pre-fix README, the second
# fails if the claim is ever "corrected" back to a nicer one the code does not have.


def test_the_bundle_does_not_claim_every_refusal_precedes_the_log(probe):
    """r7 F2: the universal was false for three refusals raised after `open_log`."""
    # ⚠️ Assert on the TEXT, never the wrapping: the bundle is hard-wrapped at ~98 columns, so the
    # pre-fix sentence was broken as "…**before the log file is\ncreated**…" and a literal
    # substring test passed against the very README it was written to reject (measured: the first
    # draft of this test did exactly that).
    flat = " ".join((probe.EVIDENCE_DIR / "README.md").read_text().split())
    assert "Every refusal happens **before the log file is created**" not in flat, (
        "three refusals run only after the arms start (`_turns`, `paired_sessions`, the single-arm "
        "refusal at the end of `main`) and each exits 3 with the measured sessions already on "
        "disk — the bundle must not state a universal its own code falsifies")
    assert "leaving the sessions already measured on disk" in flat, (
        "the corrected sentence must say what a post-arm refusal DOES leave behind, and why that "
        "is the incremental writer working rather than a lost run")
    assert "`--overwrite` replaces it" in flat, (
        "such a log carries no `RATIO:`/`VERDICT:`, so it is replaceable — the reader needs that "
        "to recover from a refusal they can only see after it happened")
    assert "Four refusals are raised AFTER the log exists" in flat, (
        "the count must follow the code: the fresh-graph refusal is a fourth, and a stated count "
        "that the code does not have is the defect this test exists for")
    assert "arm graph that is not fresh" in flat, (
        "the fresh-graph refusal (review P2) must be named where the bundle enumerates them")


def test_a_post_arm_refusal_REALLY_leaves_the_measured_sessions(probe, monkeypatch, tmp_path):
    """…and the corrected sentence is grounded in behaviour, not in a kinder wording.

    Driven through the real `main` on a single-arm `--arm-order`: the log is opened, the arm's
    lines are flushed, and the refusal that follows exits 3 **with the file still on disk**. Put
    the universal back and this is the run that disproves it.
    """
    bundle, doc = _copy_bundle(tmp_path)
    _stub_below_the_refusals(probe, monkeypatch, bundle, doc)
    monkeypatch.setattr(probe, "corpus_state", lambda pins: ([], "sha256:" + "1" * 64))
    monkeypatch.setattr(probe, "load_census", lambda: object())
    monkeypatch.setattr(probe, "run_arm", lambda arm, graph, sessions, census, golds, log: {
        "arm": arm,
        "real_search_calls": 0,
        "totals": {"extraction_nodes": 0, "label_instances": 0, "operator_edges": 0},
        "real_facts": {"survived": 0, "total": 0},
        "llm_calls": 0,
        "sessions": [],
    })
    monkeypatch.setenv("DEEPSEEK_API_KEY", "present")
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:falkordb@localhost:6379/tortoise_test_matrix")
    out = bundle / "s3-ablation-single-arm.log"

    with pytest.raises(SystemExit) as refusal:
        probe.main(["--prereg", "a1075dfc2", "--arm-order", "OFF", "--out", str(out)])

    assert refusal.value.code == 3
    assert out.exists(), (
        "a refusal raised after `open_log` does NOT leave nothing behind — this is the state the "
        "README's corrected sentence describes")
    text = out.read_text()
    assert "--- ARM OFF" in text, "the sessions already measured must survive the refusal"
    assert "RATIO:" not in text and "VERDICT:" not in text, (
        "only a run that reached a verdict can write one — so this log carries no measurement and "
        "stays replaceable with `--overwrite`")


# ── the frozen pre-registration cannot be corrected in place (r7 F3) ──────────────────────
#
# §4b claims the bars "are the numbers this document was committed with in its first commit, and
# the commit that carries this amendment changes only definitions and floors". The amendment commit
# IS the document's only add-commit (`a1075dfc2` adds all 236 lines), so the second clause describes
# a diff that does not exist — an unverifiable process claim, the class this lane has re-cut for
# several rounds. It is withdrawn in the README rather than deleted from the document, and that is
# not a preference: `preregistration.json` pins the document's BYTES, the probe compares that pin
# against the document AT THE `--prereg` COMMIT, and `tools/capstone_preregistration_check.py`
# compares it against the worktree file in CI. No single digest satisfies both sides of an in-place
# edit, so correcting the sentence there would break the record↔document cross-check permanently
# and re-cut the registration this falsification is reported against. Both halves are pinned: the
# erratum must exist, and the frozen bytes must still be the ones the record pins.


def test_the_bundle_withdraws_the_frozen_documents_unverifiable_first_commit_claim(probe):
    """r7 F3: the claim is withdrawn where it CAN be corrected, and the pin is named."""
    flat = " ".join((probe.EVIDENCE_DIR / "README.md").read_text().split())
    assert "## Errata in the frozen pre-registration" in flat, (
        "the document's known inaccuracy must be recorded — silence leaves a claim a reader will "
        "take as verified")
    errata = flat.split("## Errata in the frozen pre-registration", 1)[1]
    assert "first-commit claim" in errata and "unverifiable" in errata, (
        "the erratum must name the claim and say it cannot be verified from history")
    assert "only add-commit" in errata, (
        "it must say WHY: the amendment commit is the document's only add-commit")
    assert "changes nothing" in errata and "00:12:17Z" in errata, (
        "and what it names: the add-commit epoch on the CONTRIBUTION branch — the history, not an "
        "assurance. The ordering it once reached for is not readable from merged history and is "
        "withdrawn (#10463), so the erratum must say so rather than restore the claim")
    assert "not readable from merged history" in errata and "withdrawn" in errata, (
        "the erratum must not promise an ordering `capstone_preregistration_check.py` can no "
        "longer read: the squash collapsed both add-commits, so the provenance is withdrawn "
        "(`withdrawn/WITHDRAWN.md`) — a lane that 'restores' the assurance reds here on purpose")
    assert "a1075dfc2" in errata and "325f2a1a5" in errata, (
        "the erratum must name the same two commits the log and the record do (the amendment "
        "add-commit and the measured log's add-commit), so it cannot drift onto another")


def test_the_frozen_pre_registration_is_still_the_document_its_record_pins(probe):
    """The constraint the erratum exists for: an in-place correction would break the pin.

    A future lane that reads r7 F3 as "delete the sentence" and edits `PRE-REGISTRATION.md` reds
    here. `capstone_preregistration_check.py` catches the same edit in CI against the worktree;
    this catches it against the probe's own commit-side pin as well, because the two cannot both
    hold after an edit and the probe would then refuse every run at exit 3.
    """
    import hashlib
    import json

    document = (ROOT / probe.PREREG_REL).read_bytes()
    record = json.loads((probe.EVIDENCE_DIR / "preregistration.json").read_text())
    got = "sha256:" + hashlib.sha256(document).hexdigest()
    assert got == record["document_sha256"], (
        f"{probe.PREREG_REL} hashes to {got} but preregistration.json pins "
        f"{record['document_sha256']} — the frozen document is pinned by BYTES, and the probe "
        "compares that pin against the document at the `--prereg` COMMIT, so an in-place edit "
        "(an erratum included) makes every run refuse at exit 3. Record corrections in the "
        "README's errata section; re-cutting the registration is a NEW pre-registration")


# ── `--prereg` mistakes are exit 3, not 2 (r5 P3) ─────────────────────────
#
# The module header and README item 4 said 3; `refuse()`'s own docstring said `--prereg` mistakes
# "are the exception: they are invocation errors and exit 2". They are not: every `--prereg` failure
# goes through `refuse()`, and the only exit-2s in the module are the `--out` checks. A docstring
# that names the wrong branch is how a caller ends up treating an unmeasurable run as a retryable
# usage error, so both the behaviour and the sentence are pinned here.


def test_every_prereg_failure_is_exit_3_never_2(probe):
    bad_texts = {
        "no pins fence at all": "no pins here",
        "unterminated fence": "```yaml prereregistration-pins\nhypothesis_bars: {}\n",
        "not a mapping": "```yaml prereregistration-pins\n- a list\n```",
        "missing pinned keys": "```yaml prereregistration-pins\nhypothesis_bars: {}\n```",
    }
    for label, text in bad_texts.items():
        with pytest.raises(SystemExit) as refusal:
            probe.parse_pins(text)
        assert refusal.value.code == 3, f"{label} is an unmeasurable run, not an invocation error"

    with pytest.raises(SystemExit) as refusal:
        probe.show("0" * 40, probe.PREREG_REL)  # no such commit
    assert refusal.value.code == 3, "an unreadable --prereg commit is a refusal, not exit 2"

    doc = probe.refuse.__doc__ or ""
    assert "--prereg" not in doc or "not 2" in doc, (
        "refuse()'s docstring must not call --prereg failures exit 2 — they are exit 3")


# ── a refused run must leave NO file behind, and a missing record must refuse (r6 F3/F4) ──
#
# Two fail-opens, both in `main`'s ordering rather than in what it computes:
#
#   F3 — the log was opened and its eleven header lines written BEFORE the `DEEPSEEK_API_KEY` and
#        `TORTOISE_DB_URI` refusals, so a keyless invocation exited 3 having left a header-only
#        `*.log` INSIDE the evidence bundle. The next attempt with the same `--out` was then refused
#        at exit 2 by the one-measured-log rule, and the documented reproduce command passes no
#        `--overwrite` — one prerequisite mistake and the documented path stops working.
#   F4 — the document↔record cross-check was `if record_path.is_file()`, so deleting
#        `preregistration.json` switched the ONLY check that the bars belong to the document off,
#        with no refusal and no line in the log recording that the check had not run.
#
# Both are driven through the real `main`, with everything below the prerequisite refusals stubbed
# (git, the corpus re-derivation, the census) — the questions here are *did a file get created* and
# *did the absent record refuse*, neither of which needs a graph or a model key.


def _copy_bundle(tmp_path: Path, *, with_record: bool = True,
                 record: dict | str | None = None) -> tuple[Path, str]:
    """A minimal evidence bundle: the real pre-registration, and a record that pins it."""
    import hashlib
    import json

    source = ROOT / "docs/evidence/5087-hypothesis"
    bundle = tmp_path / "docs/evidence/5087-hypothesis"
    bundle.mkdir(parents=True)
    doc = (source / "PRE-REGISTRATION.md").read_text()
    (bundle / "PRE-REGISTRATION.md").write_text(doc)
    if with_record:
        payload = record
        if payload is None:
            payload = json.loads((source / "preregistration.json").read_text())
            payload["document_sha256"] = "sha256:" + hashlib.sha256(doc.encode()).hexdigest()
        (bundle / "preregistration.json").write_text(
            payload if isinstance(payload, str) else json.dumps(payload))
    return bundle, doc


def _stub_below_the_refusals(probe, monkeypatch, bundle: Path, doc: str) -> None:
    """Everything `main` reaches AFTER the prerequisite refusals: git, corpus, the census.

    `assert_prereg_binding` is stubbed here because `head_sha` is: the binding asserts `--prereg` is
    an ancestor of `HEAD`, and `HEAD` is faked to `"0" * 40` in these tests. It has its own
    behavioural test against a real temp repository (`test_the_prereg_commit_must_belong_to_this_
    trees_history`), which is where the refusal is measured.
    """
    monkeypatch.setattr(probe, "EVIDENCE_DIR", bundle)
    monkeypatch.setattr(probe, "head_sha", lambda: "0" * 40)
    monkeypatch.setattr(probe, "assert_prereg_binding", lambda prereg, head: prereg)
    monkeypatch.setattr(probe, "show", lambda sha, rel: doc)
    monkeypatch.setattr(probe, "assert_source_sha", lambda source_sha, head: [])
    monkeypatch.setattr(probe, "assert_clean_tree", lambda: "a1075dfc2")
    monkeypatch.setattr(probe, "probe_sha256", lambda: "sha256:" + "0" * 64)
    monkeypatch.setattr(probe, "corpus_state", lambda pins: (["wp01_x"] * 7, "sha256:" + "1" * 64))


def test_a_refused_run_leaves_no_log_file_behind(probe, monkeypatch, tmp_path, capsys):
    """F3: the keyless refusal must happen BEFORE the log is created, not after its header."""
    bundle, doc = _copy_bundle(tmp_path)
    _stub_below_the_refusals(probe, monkeypatch, bundle, doc)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    out = bundle / "s3-ablation-refused.log"

    with pytest.raises(SystemExit) as refusal:
        probe.main(["--prereg", "a1075dfc2", "--out", str(out)])

    assert refusal.value.code == 3, "a missing key is an unmeasurable run, never a crash"
    assert "DEEPSEEK_API_KEY" in capsys.readouterr().err
    assert not out.exists(), (
        "a refused run must leave no file: a header-only log inside the bundle reads as the setup "
        "of a run that never started, and the next attempt is refused at exit 2 by the one-log rule "
        "while the documented reproduce command passes no --overwrite (r6 F3)")

    # …and the same for the second prerequisite, so the guard is not one-if-order lucky.
    monkeypatch.setenv("DEEPSEEK_API_KEY", "present")
    with pytest.raises(SystemExit) as refusal:
        probe.main(["--prereg", "a1075dfc2", "--out", str(out)])
    assert refusal.value.code == 3
    assert "TORTOISE_DB_URI" in capsys.readouterr().err
    assert not out.exists()


def test_a_missing_machine_record_refuses_instead_of_skipping_the_cross_check(
        probe, monkeypatch, tmp_path, capsys):
    """F4: absence must refuse. `if record_path.is_file()` made the guard deletable."""
    bundle, doc = _copy_bundle(tmp_path, with_record=False)
    _stub_below_the_refusals(probe, monkeypatch, bundle, doc)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "present")
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:falkordb@localhost:6379/tortoise_test_matrix")
    out = bundle / "s3-ablation-x.log"

    with pytest.raises(SystemExit) as refusal:
        probe.main(["--prereg", "a1075dfc2", "--out", str(out)])

    assert refusal.value.code == 3
    assert "preregistration.json" in capsys.readouterr().err, (
        "the refusal must name the file whose absence switched the cross-check off")
    assert not out.exists(), "a refused run must not start writing the log it refused"


@pytest.mark.parametrize(
    "record, expected",
    [
        ({"schema": "capstone-preregistration/v1"}, "document_sha256"),
        ({"document_sha256": None}, "document_sha256"),
        ("{not json", "not JSON"),
        ("[1, 2]", "JSON object"),
    ],
    ids=["no-sha-field", "null-sha-field", "unparseable", "not-an-object"],
)
def test_a_record_that_cannot_pin_the_document_refuses(
        probe, monkeypatch, tmp_path, capsys, record, expected):
    """`if want and want != got` failed open one field in: a record with no digest checked nothing."""
    bundle, doc = _copy_bundle(tmp_path, record=record)
    _stub_below_the_refusals(probe, monkeypatch, bundle, doc)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "present")
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:falkordb@localhost:6379/tortoise_test_matrix")
    out = bundle / "s3-ablation-x.log"

    with pytest.raises(SystemExit) as refusal:
        probe.main(["--prereg", "a1075dfc2", "--out", str(out)])

    assert refusal.value.code == 3
    assert expected in capsys.readouterr().err
    assert not out.exists()


def test_the_bundle_states_the_two_orderings_the_probe_now_enforces(probe):
    """F3/F4 are behaviour, but the bundle's disclosure list is what a reader trusts."""
    readme = (probe.EVIDENCE_DIR / "README.md").read_text()
    assert "before anything is written" in readme, (
        "the hardening list must say the prerequisite refusals precede the log creation (F3)")
    assert "a bundle whose record is missing" in readme, (
        "the reproduce section must say a missing record is REFUSED, not skipped (F4)")


# ── the hardening disclosure list (review r7 P3, and the count it restated) ─────────────────
#
# The section enumerates every instrument gap found AFTER the run. Two failure modes were live at
# head `779f2c2c5`, and both are the same one: the list is the disclosure, so a wrong count or a
# missing item is a hole in it. (a) The prose restated the list's length — "eight further hardening
# gaps" — against TEN numbered items. (b) The freshest post-run refusal, `assert_fresh_graph`
# (added in `2e1d5d730`), was not in the list at all: the probe refused a class of run the bundle
# never mentioned. External discipline (Lakens, *Improving Your Statistical Inferences* ch. 13.5,
# on deviating from a pre-registration): a deviation "should not just be reported, but the
# consequences of the deviation should also be evaluated" — and a list missing an item reports it
# nowhere at all.

_HARDENING_HEADING = "## Post-run instrument hardening"


def _hardening_list(readme: str) -> tuple[str, list[int]]:
    """The disclosure section, and the numbers its items actually carry."""
    import re

    assert _HARDENING_HEADING in readme, "the bundle lost its post-run hardening disclosure"
    section = readme.split(_HARDENING_HEADING, 1)[1].split("## Reproduce", 1)[0]
    return section, [int(number) for number in re.findall(r"^(\d+)\. \*\*", section, re.M)]


def test_the_hardening_list_is_contiguous_and_restates_no_count(probe):
    """A numeral beside the list is a SECOND copy of the list, and it rots on the next addition."""
    import re

    readme = (probe.EVIDENCE_DIR / "README.md").read_text()
    _section, numbers = _hardening_list(readme)
    assert numbers, "the hardening list lost its numbering"
    assert numbers == list(range(1, len(numbers) + 1)), (
        f"the hardening items must be numbered 1..{len(numbers)} contiguously, got {numbers}")
    stale = re.findall(r"\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b"
                       r"\s+(?:further\s+)?hardening", readme, re.I)
    assert not stale, (
        f"the section restates a count ({stale}) beside a {len(numbers)}-item list: `eight` was "
        "already stale against ten items, and the fresh-graph refusal makes it eleven")


def test_the_bundle_discloses_the_fresh_graph_refusal_and_what_the_log_cannot_show(probe):
    """The eleventh item, including the half the committed log CANNOT be asked for.

    `assert_fresh_graph` refuses a reused graph only from `2e1d5d730` — after the run. The shipped
    log therefore predates it and carries no baseline census, so a sentence claiming the log
    evidences freshness would be false. Both halves are measured here rather than inspected.
    """
    readme = (probe.EVIDENCE_DIR / "README.md").read_text()
    section, _numbers = _hardening_list(readme)
    assert "FRESH disposable graph" in section, (
        "the hardening list must carry the fresh-graph refusal (pre-registration §0's premise)")
    assert "graph baseline" in section, (
        "the item must name the line the guard now prints — the evidence the pre-fix probe could "
        "not leave behind")
    assert "consistent with" in section and "not evidence of one" in section, (
        "and it must say what the measured run does NOT establish: its arm headers name fresh "
        "graphs, which is consistency, not proof")

    log_text = (probe.EVIDENCE_DIR / MEASURED_LOG_REL).read_text()
    assert "graph baseline" not in log_text, (
        "the committed log now carries a baseline census — the disclosure's 'not evidence' half "
        "has to be re-measured, and the measured run is not this probe's output")
    assert "fresh graph s3ablate_" in log_text, (
        "the disclosure quotes the arm headers as the consistency evidence; they are not there")


# ── the disclosure list's completeness claim, and the deviation it was hiding (review r8 P2) ─────
#
# The list is the bundle's disclosure. Two defects lived at head `f46ba5198`, and both are the same
# one: the section promised more than it delivered. (a) It stated that the review-found probe gaps
# were "every one of them enumerated below" — a claim about process with no artefact behind it, and
# four further review-found probe fixes falsified it. (b) The omission that is NOT cosmetic is rule
# 5's verdict TEXT: `PRE-REGISTRATION.md` §3 freezes `HYPOTHESIS-FALSIFIED (ratio X)`, the shipped
# log's `VERDICT:` line carries the bare token, and §4 registers that *any* deviation is reported —
# so this was a live deviation reported nowhere while the bundle's headline printed the fixed form.
# External discipline (Center for Open Science, preregistration guidance): "transparently disclose
# **any** changes from the proposed plan" — not only the changes that moved a number. The fix follows
# the repo's own rule for a self-referential process claim: stop promising, do not re-word the promise.


def test_the_hardening_list_promises_no_completeness_and_carries_the_late_findings(probe):
    """Both halves fail on the pre-fix README, and are measured against the shipped artefacts."""
    import re

    readme = (probe.EVIDENCE_DIR / "README.md").read_text()
    section, _numbers = _hardening_list(readme)

    # (a) the universal. A list that claims exhaustiveness is a claim nothing can check, and it
    # re-stales on the next addition — which is exactly what happened to it here.
    universal = re.search(r"every one of them|all of them are listed|complete list of", section, re.I)
    assert not universal, (
        f"the disclosure list promises completeness ({universal.group(0)}) that four further "
        "review-found probe fixes falsify: describe the list as what it is, do not re-word the "
        "promise")

    # (b) the material omission — the frozen verdict TEXT the shipped log does not carry.
    assert "rule 5" in section and "bare token" in section, (
        "the list must carry rule 5's verdict-text deviation: the frozen rule fixes "
        "`HYPOTHESIS-FALSIFIED (ratio X)` and the shipped log prints the bare token")
    assert "RATIO: 0.8697" in section, (
        "and it must say where the number actually is — the log's own RATIO/WHY lines — so the "
        "disclosure is checkable instead of a reassurance")
    log_text = (probe.EVIDENCE_DIR / MEASURED_LOG_REL).read_text()
    assert "VERDICT: HYPOTHESIS-FALSIFIED\n" in log_text, (
        "MEASURED, not asserted: the shipped log's VERDICT line is the bare token. If this fails, a "
        "measured log was rewritten or replaced and the deviation disclosure must be re-derived")

    # The three probe fixes that were documented only in a test docstring or a source comment.
    assert "zip" in section and "truncat" in section, (
        "the list must carry the per-session table's silent truncation of unequal arms")
    # A bare `/7` is not the assertion: the section already contains `probes/7949-…`, so that
    # substring is true of the PRE-fix list too and would pin nothing (measured, not assumed).
    assert "hardcoded its denominator" in section and "no arm produced" in section, (
        "the list must carry the arm-integrity line's hardcoded `7`-session denominator")
    assert "ratio range" in section.lower() and "NOT REPORTED" in section, (
        "the list must carry the per-session ratio range printed for arms whose S3 was never proven")

    # The deviation is named where the headline makes the claim, not only in a list further down —
    # a reader who stops after the verdict must not read it as the log's own text.
    headline = readme.split(_HARDENING_HEADING, 1)[0]
    assert "bare token" in headline, (
        "the verdict headline must name the verdict-text deviation beside the verdict itself")
