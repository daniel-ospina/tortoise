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
    assert result["qualifier"] == "SINGLE-SESSION-DRIVEN"


def test_an_increase_cannot_subsidise_another_sessions_share(probe):
    stats = probe.concentration([(10, 40), (100, 10)])
    assert stats["total_reduction"] == 90
    assert stats["share"] == pytest.approx(1.0)


def test_outside_the_evidence_bundle_is_refused(probe):
    assert probe.inside_evidence(probe.EVIDENCE_DIR / "x.log")
    assert not probe.inside_evidence(ROOT / "docs/evidence/other/x.log")
    assert not probe.inside_evidence(Path("/tmp/x.log"))


def test_only_evidence_and_tools_count_as_nothing_changed(probe):
    assert probe.is_evidence_or_tool("docs/evidence/5087-hypothesis/x.log")
    assert probe.is_evidence_or_tool("tools/s3_prior_ablation.py")
    assert not probe.is_evidence_or_tool("tortoise/extractor_v2.py")
    assert not probe.is_evidence_or_tool("tests/eval/write_path/gold/x.json")


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
