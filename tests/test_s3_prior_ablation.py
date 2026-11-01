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


@pytest.fixture(scope="module")
def probe():
    return _probe()


BARS = {
    "primary_ratio_max": 0.75,
    "guard_ratio_min": 1.0,
    "corroboration_min_sessions": 4,
    "sessions_total": 7,
    "arm_integrity_required": 14,
    "concentration_max": 0.60,
    "cost_ratio_max": 2.0,
}
FLOORS = {"created_nodes_off": 30, "real_facts_off": 20, "sessions": 7}


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


def test_ratio_near_one_is_falsified(probe):
    """The falsifier: telling extraction what exists does NOT reduce creates."""
    off = _arm(14, [40] * 7, facts=60, operator_edges=10)
    on = _arm(14, [40] * 7, facts=60, operator_edges=10)
    result = probe.decide(BARS, FLOORS, off, on)
    assert result["verdict"] == probe.VERDICT_FALSIFIED
    assert result["ratio"] == pytest.approx(1.0)
    assert result["primary_met"] is False


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
    text = (ROOT / probe.PREREG_REL).read_text()
    pins = probe.parse_pins(text)
    assert pins["hypothesis_bars"]["primary_ratio_max"] == 0.75
    assert pins["denominator_floors"]["created_nodes_off"] == 30
    assert len(pins["source_sha"]) == 40
    # The bars the probe compares against are these, never literals in the module.
    source = PROBE.read_text()
    assert "primary_ratio_max = 0.75" not in source


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
