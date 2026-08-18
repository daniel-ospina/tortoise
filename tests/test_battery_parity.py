"""Issue #1414 — parity leg: pinned versions refuse mismatch, baseline
methodology-unchanged check, bespoke staleness probe."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from battery.parity.runner import (
    BaselineMissingError,
    VersionMismatchError,
    check_pinned_version,
    methodology_hashes,
    run_parity,
    score_staleness,
    staleness_probes,
)


def _baseline(reader_prompt="rp", judge_rubric_id="jr"):
    rp, jr = methodology_hashes(reader_prompt, judge_rubric_id)
    return {"reader_prompt_hash": rp, "judge_rubric_id_hash": jr}


def test_pinned_version_ok():
    check_pinned_version("longmemeval", "longmemeval-2025.3")  # no raise


def test_unpinned_version_refuses():
    with pytest.raises(VersionMismatchError):
        check_pinned_version("longmemeval", "longmemeval-2024.1")
    with pytest.raises(VersionMismatchError):
        check_pinned_version("unknown-bench", "x")


def test_baseline_missing_fails_closed():
    with pytest.raises(BaselineMissingError):
        run_parity("longmemeval", "longmemeval-2025.3", "a4",
                   "rp", "jr", None, accuracy=0.5, samples=10)


def test_methodology_unchanged_matches():
    r = run_parity("longmemeval", "longmemeval-2025.3", "a4",
                   "rp", "jr", _baseline(), accuracy=0.66, samples=10)
    assert r.methodology_matched
    assert r.accuracy == 0.66 and r.samples == 10


def test_methodology_drift_detected():
    """Reader prompt or rubric changed → hashes differ → NOT matched."""
    r = run_parity("longmemeval", "longmemeval-2025.3", "a4",
                   "rp-CHANGED", "jr", _baseline(), accuracy=0.6)
    assert not r.methodology_matched


def test_staleness_probes_defined():
    probes = staleness_probes()
    assert len(probes) == 3
    assert all(p.old_claim != p.current_claim for p in probes)


def test_staleness_current_answer_passes():
    probe = staleness_probes()[0]  # /v1/deprecated -> /v1/current
    assert score_staleness(probe, probe.current_claim)


def test_staleness_stale_answer_fails():
    """A stale answer (old claim) must FAIL even though the claims share
    boilerplate tokens ("the api endpoint is")."""
    probe = staleness_probes()[0]
    assert not score_staleness(probe, probe.old_claim)


def test_staleness_ambiguous_fails_closed():
    probe = staleness_probes()[0]
    assert not score_staleness(probe, "unrelated answer")
