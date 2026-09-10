"""#2800 — released-benchmark executors + the parity execution seam.

The parity scaffold could always say "methodology unchanged"; it could never
say "accuracy X" (#2797). These tests pin the seam that produces a number:
it must come from the released runner's own output, it must carry its sample
count and the dataset revision actually loaded, and every unavailable path
must yield an explicitly not-measured cell — never a default.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from battery.parity.executors import (
    EXECUTORS,
    ExecutedCell,
    ExecutorUnavailable,
    longmemeval_executor,
)
from battery.parity.runner import methodology_hashes


class TestExecutedCellInvariants:
    def test_accuracy_without_samples_is_refused(self):
        with pytest.raises(ValueError, match="not a measurement"):
            ExecutedCell(benchmark="longmemeval", accuracy=0.7, samples=0,
                         revision="ds@s", lane="real")

    def test_a_measured_cell_must_name_its_revision(self):
        """A number without its dataset identity is not comparable."""
        with pytest.raises(ValueError, match="must name the dataset revision"):
            ExecutedCell(benchmark="longmemeval", accuracy=0.7, samples=10,
                         revision="", lane="real")

    def test_healthy_cell_constructs(self):
        c = ExecutedCell(benchmark="longmemeval", accuracy=0.7, samples=10,
                         revision="ds@s", lane="real")
        assert c.accuracy == 0.7 and c.samples == 10


def _fake_run_main(report):
    def _f(argv):
        _f.argv = argv
        return report
    return _f


class TestLongMemEvalExecutor:
    """The executor adapts argv and reads the OFFICIAL runner's own fields."""

    def test_reads_official_accuracy_and_samples(self, monkeypatch, tmp_path):
        import tools.longmem_eval.run as lme
        monkeypatch.setattr(lme, "run_main", _fake_run_main({
            "dataset": "xiaowu0162/longmemeval-cleaned", "split": "s",
            "n_questions": 40,
            "methodology": {"dataset_fingerprint": "a" * 64},
            "accuracy": {"overall": 0.775, "task_averaged": 0.79}}))
        c = longmemeval_executor(mock=True, limit=5, out_dir=tmp_path,
                                 fixture=_touch(tmp_path / "mini.json"))
        assert c.accuracy == 0.775 and c.samples == 40
        # a MOCK run must not wear the real dataset's identity (review P1):
        # the fixture is named and the lane is recorded
        assert c.lane == "mock"
        assert c.revision == "fixture:mini.json"
        assert c.detail["task_averaged"] == 0.79

    def test_retrieval_only_report_is_not_measured(self, monkeypatch, tmp_path):
        """accuracy=None by design (retrieval-only) must stay a not-measured
        cell — never coerced to 0.0."""
        import tools.longmem_eval.run as lme
        monkeypatch.setattr(lme, "run_main", _fake_run_main({
            "dataset": "d", "split": "s", "n_questions": 40,
            "accuracy": None}))
        c = longmemeval_executor(mock=True, limit=5, out_dir=tmp_path,
                                 fixture=_touch(tmp_path / "mini.json"))
        assert c.accuracy is None and c.samples == 40

    def test_lane_is_required_and_validated(self):
        """A cell that does not say which lane produced it is refused — a
        default would fail OPEN by asserting the real lane."""
        with pytest.raises(ValueError, match="must be"):
            ExecutedCell(benchmark="longmemeval", accuracy=0.5, samples=5,
                         revision="d@s", lane="production")
        import dataclasses
        with pytest.raises(TypeError):
            ExecutedCell(benchmark="longmemeval", accuracy=0.5, samples=5,
                         revision="d@s")  # lane is keyword-required
        # lane sits directly after revision (both keyword-required, before
        # the defaulted `detail`) — the ordering is what makes omission a
        # TypeError rather than a silent default
        names = [f.name for f in dataclasses.fields(ExecutedCell)]
        assert names.index("lane") == names.index("revision") + 1
        assert ExecutedCell(benchmark="longmemeval", accuracy=0.5, samples=5,
                            revision="d@s", lane="real").lane == "real"

    def test_missing_fixture_fails_closed(self, tmp_path):
        with pytest.raises(ExecutorUnavailable, match="mini fixture"):
            longmemeval_executor(mock=True, fixture=tmp_path / "nope.json",
                                 out_dir=tmp_path)

    def test_runner_failure_is_unavailable_not_a_number(self, monkeypatch,
                                                        tmp_path):
        import tools.longmem_eval.run as lme

        def _boom(argv):
            raise RuntimeError("no OPENROUTER_API_KEY")

        monkeypatch.setattr(lme, "run_main", _boom)
        with pytest.raises(ExecutorUnavailable, match="no OPENROUTER_API_KEY"):
            longmemeval_executor(mock=True, out_dir=tmp_path,
                                 fixture=_touch(tmp_path / "mini.json"))

    def test_runner_sys_exit_is_unavailable(self, monkeypatch, tmp_path):
        import tools.longmem_eval.run as lme

        def _exit(argv):
            raise SystemExit(2)

        monkeypatch.setattr(lme, "run_main", _exit)
        with pytest.raises(ExecutorUnavailable, match="exited"):
            longmemeval_executor(mock=True, out_dir=tmp_path,
                                 fixture=_touch(tmp_path / "mini.json"))


class TestCliExecutionSeam:
    """`battery parity --execute` records the runner's number; without it the
    cell stays explicitly not-measured."""

    def _cfg(self, tmp_path: Path) -> Path:
        import shutil
        cfg = Path(__file__).resolve().parent.parent / "battery" / "config"
        tmp_cfg = tmp_path / "cfg"
        tmp_cfg.mkdir()
        shutil.copy(cfg / "arms.yaml", tmp_cfg / "arms.yaml")
        rp, jr, _ = methodology_hashes("default-reader",
                                       "longmemeval-official")
        (tmp_cfg / "parity_baseline.json").write_text(json.dumps(
            {"reader_prompt_hash": rp, "judge_rubric_id_hash": jr}))
        return tmp_cfg

    def test_execute_persists_the_measured_cell(self, tmp_path, monkeypatch):
        import battery.cli as cli
        import battery.parity.executors as ex

        def _fake_executor(*, mock=False, limit=None, out_dir=None):
            return ExecutedCell(benchmark="longmemeval", accuracy=0.775,
                                samples=40, lane="real",
                                revision="xiaowu0162/longmemeval-cleaned@s#abc")

        monkeypatch.setitem(ex.EXECUTORS, "longmemeval", _fake_executor)
        rc = cli.main(["parity", "--config", str(self._cfg(tmp_path)),
                       "--out", str(tmp_path), "--execute", "--allow-spend",
                       "--limit", "5"])
        assert rc == 0
        record = json.loads((tmp_path / "parity_record.json").read_text())
        cell = record["benchmarks"]["longmemeval"]
        assert cell["measured"] is True
        assert cell["accuracy"] == 0.775 and cell["samples"] == 40
        assert cell["revision"] == "xiaowu0162/longmemeval-cleaned@s#abc"
        assert cell["lane"] == "real"
        # a benchmark with no registered executor stays honestly not-measured
        other = record["benchmarks"]["locomo"]
        assert other["measured"] is False and other["accuracy"] is None

    def test_real_lane_needs_explicit_spend_opt_in(self, tmp_path, monkeypatch,
                                                   capsys):
        import battery.cli as cli

        def _must_not_run(*, mock=False, limit=None, out_dir=None):
            raise AssertionError("a real lane ran without --allow-spend")

        monkeypatch.setitem(EXECUTORS, "longmemeval", _must_not_run)
        rc = cli.main(["parity", "--config", str(self._cfg(tmp_path)),
                       "--out", str(tmp_path), "--execute"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "--allow-spend" in out
        record = json.loads((tmp_path / "parity_record.json").read_text())
        assert record["benchmarks"]["longmemeval"]["measured"] is False

    def test_limit_without_execute_warns(self, tmp_path, capsys):
        import battery.cli as cli
        rc = cli.main(["parity", "--config", str(self._cfg(tmp_path)),
                       "--out", str(tmp_path), "--limit", "5"])
        assert rc == 0
        assert "no effect without --execute" in capsys.readouterr().out

    def test_executor_unavailable_records_not_measured(self, tmp_path,
                                                       monkeypatch, capsys):
        import battery.cli as cli
        import battery.parity.executors as ex

        def _unavailable(*, mock=False, limit=None, out_dir=None):
            raise ExecutorUnavailable("longmemeval: no dataset here")

        monkeypatch.setitem(ex.EXECUTORS, "longmemeval", _unavailable)
        rc = cli.main(["parity", "--config", str(self._cfg(tmp_path)),
                       "--out", str(tmp_path), "--execute", "--allow-spend"])
        assert rc == 0, "an unavailable executor must not crash the leg"
        out = capsys.readouterr().out
        assert "executor unavailable" in out
        record = json.loads((tmp_path / "parity_record.json").read_text())
        assert record["benchmarks"]["longmemeval"]["measured"] is False

    def test_registry_contains_longmemeval(self):
        assert "longmemeval" in EXECUTORS


def _touch(p: Path) -> Path:
    p.write_text("{}")
    return p


@pytest.mark.skipif(
    not __import__("os").environ.get("BATTERY_PARITY_EXECUTOR_E2E"),
    reason="opt-in: runs the REAL released LongMemEval runner (mock lane, "
           "1 question, embedded graph) — set BATTERY_PARITY_EXECUTOR_E2E=1")
def test_longmemeval_real_mock_lane_end_to_end(tmp_path):
    """The seam against the REAL released runner.

    Not a mocked ``run_main``: this invokes ``tools.longmem_eval`` end to end
    on the committed mini fixture (mocked reader+judge: no keys, no spend),
    and asserts the cell carries the runner's OWN accuracy, its sample count,
    and the dataset revision it loaded. Opt-in because it stands up an
    embedded graph (~20s); the hermetic tests above cover the seam itself.
    """
    cell = longmemeval_executor(mock=True, limit=1, out_dir=tmp_path)
    assert cell.benchmark == "longmemeval"
    assert cell.samples == 1, "one question in, one sample out"
    assert cell.accuracy is not None, "the mock lane publishes an accuracy"
    assert cell.lane == "mock"
    assert cell.revision == "fixture:longmemeval_mini.json"
    assert (tmp_path / "longmemeval_official.json").is_file()
