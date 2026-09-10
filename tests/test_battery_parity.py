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
    rp, jr, _ = methodology_hashes(reader_prompt, judge_rubric_id)
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
                   "rp-CHANGED", "jr", _baseline(), accuracy=0.6, samples=5)
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


# ── #2797: a parity cell may only carry a number that was MEASURED ────────
class TestNoFabricatedAccuracy:
    """The parity CLI must never supply an accuracy it did not measure.

    Pre-#2797 `battery parity` passed a literal ``accuracy=0.5, samples=0``
    for every pinned benchmark without executing any of them. The value was
    inert (nothing read ``ParityRun.accuracy``), but it is the shape of a
    fabricated metric: any future consumer of the cell — or of the parity
    record — would have read a constant as a score.
    """

    def test_accuracy_with_zero_samples_is_refused(self):
        with pytest.raises(ValueError, match="not a measurement"):
            run_parity("longmemeval", "longmemeval-2025.3", "a4",
                       "rp", "jr", _baseline(), accuracy=0.5, samples=0)

    def test_not_measured_cell_has_no_accuracy(self):
        r = run_parity("longmemeval", "longmemeval-2025.3", "a4",
                       "rp", "jr", _baseline(), accuracy=None, samples=0)
        assert r.accuracy is None
        assert r.measured is False
        # the methodology compare still works — the cell is not useless,
        # it simply makes no claim about a score
        assert r.methodology_matched

    def test_measured_cell_requires_samples(self):
        r = run_parity("longmemeval", "longmemeval-2025.3", "a4",
                       "rp", "jr", _baseline(), accuracy=0.66, samples=10)
        assert r.measured is True

    def test_cli_supplies_no_fabricated_accuracy(self, tmp_path, monkeypatch):
        """The CLI's parity cell must be not-measured until #2800 wires a
        real runner — asserted on the CALL, which is where the constant was."""
        import battery.cli as cli
        import battery.parity.runner as parity_runner

        seen: list[tuple] = []
        real = parity_runner.run_parity

        def _spy(benchmark, version, arm, rp, jr, baseline, **kw):
            seen.append((benchmark, kw.get("accuracy"), kw.get("samples")))
            return real(benchmark, version, arm, rp, jr, baseline, **kw)

        monkeypatch.setattr(parity_runner, "run_parity", _spy)
        cfg = Path(__file__).resolve().parent.parent / "battery" / "config"
        # A baseline record is what makes the CLI persist parity_record.json
        # (no baseline = fail-closed, nothing to record).
        import json
        import shutil
        tmp_cfg = tmp_path / "cfg"
        tmp_cfg.mkdir()
        shutil.copy(cfg / "arms.yaml", tmp_cfg / "arms.yaml")
        rp, jr, _ = methodology_hashes("default-reader", "longmemeval-official")
        (tmp_cfg / "parity_baseline.json").write_text(json.dumps(
            {"reader_prompt_hash": rp, "judge_rubric_id_hash": jr}))
        rc = cli.main(["parity", "--config", str(tmp_cfg),
                       "--out", str(tmp_path), "--mock"])
        assert rc == 0, f"parity CLI failed: {rc}"
        assert seen, "parity CLI ran no cells"
        for benchmark, accuracy, samples in seen:
            assert accuracy is None, (
                f"{benchmark}: CLI supplied accuracy={accuracy!r} — a value "
                f"no runner produced (#2797)")
            assert samples == 0
        record = json.loads((tmp_path / "parity_record.json").read_text())
        assert set(record["benchmarks"]) == {
            "longmemeval", "locomo", "memoryarena", "memoryagentbench"}
        for benchmark, cell in record["benchmarks"].items():
            assert cell["measured"] is False, benchmark
            assert cell["accuracy"] is None, benchmark
            assert cell["samples"] == 0, benchmark
