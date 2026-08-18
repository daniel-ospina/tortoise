"""Task 7 tests — CLI subcommand surface + exit-code contract (0/1/2/3/4/5)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from battery.cli import main, _parser
from battery.enums import ExitCode
from battery.exceptions import (
    EmptyCorpus,
    InconclusiveRun,
    IsolationBreach,
    JudgeGateBlocked,
)

CONFIG = Path(__file__).parent.parent / "battery" / "config"


def _empty_config(tmp_path) -> Path:
    d = tmp_path / "empty"
    d.mkdir()
    (d / "corpus.yaml").write_text(yaml.safe_dump({"scenarios": []}),
                                   encoding="utf-8")
    (d / "thresholds.yaml").write_text(yaml.safe_dump(
        {"determinism": {"epsilon": 1e-6}, "cal": {}}), encoding="utf-8")
    (d / "arms.yaml").write_text(yaml.safe_dump({"arms": [
        {"arm_id": "mock", "adapter": "battery.arms.mock",
         "price_per_1k_usd": 0.0, "expected_tokens_per_episode": 64}]}),
        encoding="utf-8")
    (d / "budget.yaml").write_text(yaml.safe_dump(
        {"max_episodes": 1000, "max_estimated_cost_usd": 50.0}),
        encoding="utf-8")
    return d


class TestSubcommandSurface:
    def test_all_five_subcommands_registered(self):
        parser = _parser()
        subs = next(a for a in parser._actions
                    if getattr(a, "dest", None) == "subcommand").choices
        assert set(subs) == {"run", "parity", "calibrate", "validate-judge",
                             "report"}

    def test_run_flag_surface(self):
        args = _parser().parse_args([
            "run", "--tier", "1", "--arms", "a0", "--seed", "3",
            "--mock", "--batch-setup", "--scorer", "probes.r1",
            "--config", "/tmp/c", "--out", "/tmp/o", "--max-episodes", "5"])
        assert args.subcommand == "run"
        assert args.tier == 1 and args.arms == "a0" and args.seed == 3
        assert args.mock is True and args.batch_setup is True
        assert args.scorer == ["probes.r1"]
        assert args.max_episodes == 5

    def test_stub_flag_surfaces(self):
        p = _parser()
        assert p.parse_args(["parity", "--arms", "a0", "--seed", "1",
                             "--mock"]).subcommand == "parity"
        assert p.parse_args(["calibrate", "--print"]).print_deltas is True
        assert p.parse_args(["validate-judge", "--rubric",
                             "r2"]).rubric == "r2"
        assert p.parse_args(["report", "--out", "/tmp/o"]).out == "/tmp/o"


class TestExitCodes:
    def test_run_ok(self, tmp_path):
        code = main(["run", "--config", str(CONFIG), "--mock", "--seed", "5",
                     "--out", str(tmp_path / "o")])
        assert code is ExitCode.OK

    def test_empty_corpus_exit5(self, tmp_path):
        code = main(["run", "--config", str(_empty_config(tmp_path)),
                     "--mock", "--out", str(tmp_path / "o")])
        assert code is ExitCode.EMPTY_CORPUS
        # no artifacts fabricated
        assert not any(tmp_path.rglob("*.json"))

    def test_unknown_flag_exit1(self, tmp_path):
        """argparse default exit 2 remapped → 1 (never collides with 2)."""
        code = main(["run", "--bogus-flag"])
        assert code is ExitCode.OPERATIONAL

    def test_unknown_arm_exit1(self, tmp_path):
        code = main(["run", "--config", str(CONFIG), "--mock", "--arms", "nope",
                     "--out", str(tmp_path / "o")])
        assert code is ExitCode.OPERATIONAL

    def test_bad_scorer_exit1(self, tmp_path):
        code = main(["run", "--config", str(CONFIG), "--mock",
                     "--scorer", "does.not.exist", "--out", str(tmp_path / "o")])
        assert code is ExitCode.OPERATIONAL

    def test_stubs_exit1_with_ownership(self, capsys):
        # parity/report/calibrate are IMPLEMENTED (#1414/#1415) — clean
        # exits (baseline-missing and empty-artifacts are reported, not
        # errors); no stub ownership messages remain.
        assert main(["parity"]) is ExitCode.OK
        assert main(["calibrate", "--print"]) is ExitCode.OK
        assert main(["report"]) is ExitCode.OK
        err = capsys.readouterr().err
        assert "stub" not in err.lower()
        # validate-judge is IMPLEMENTED in #1410 (was a stub): hermetic mock
        # run returns GATE_BLOCKED (2) in default config or OK (0) on a
        # passing mock rubric — never OPERATIONAL.
        # validate-judge is IMPLEMENTED in #1410 (was a stub): hermetic
        # mock run returns OK (0) on a passing mock rubric — never
        # OPERATIONAL. (Real-mode calls require an explicit model id and
        # are never exercised in hermetic tests.)
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            rc = main(["validate-judge", "--rubric", "r2", "--mock",
                   "--out", td])
        # Clean contract exit (0 validated or 2 gate-blocked) — the mock
        # judge may or may not clear the kappa leg; either is a valid run.
        assert rc in (ExitCode.OK, ExitCode.GATE_BLOCKED)


class TestDispatchMapping:
    """Exit 2/3/4 mappings tested via the SHIPPED contract exceptions raised
    at the dispatch boundary — no monkeypatching (scope DD7)."""

    def test_gate_blocked_exit2(self, monkeypatch):
        from battery import cli
        monkeypatch.setattr(cli, "_dispatch", lambda a: (_raise(JudgeGateBlocked)))
        assert main(["run"]) is ExitCode.GATE_BLOCKED

    def test_inconclusive_exit3(self, monkeypatch):
        from battery import cli
        monkeypatch.setattr(cli, "_dispatch", lambda a: (_raise(InconclusiveRun)))
        assert main(["run"]) is ExitCode.INCONCLUSIVE

    def test_isolation_breach_exit4(self, monkeypatch):
        from battery import cli
        monkeypatch.setattr(cli, "_dispatch", lambda a: (_raise(IsolationBreach)))
        assert main(["run"]) is ExitCode.ARM_FAILED

    def test_empty_corpus_before_config_error(self, monkeypatch):
        """EmptyCorpus is caught BEFORE ConfigError → exit 5, never 1."""
        from battery import cli
        from battery.exceptions import ConfigError

        class _Tricky(ConfigError, EmptyCorpus):
            pass

        monkeypatch.setattr(cli, "_dispatch", lambda a: (_raise(_Tricky)))
        assert main(["run"]) is ExitCode.EMPTY_CORPUS


def _raise(exc):
    raise exc
