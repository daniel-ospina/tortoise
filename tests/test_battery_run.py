"""Task 6 tests — run orchestration: artifacts, seed pinning, exit-4
boundaries (b1/b2/mixed), budget guard, summary schema."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from battery.arms.base import ArmUnavailable
from battery.enums import ExitCode
from battery.exceptions import ConfigError
from battery.runner.run import RunConfig, run_battery

CONFIG = Path(__file__).parent.parent / "battery" / "config"


def _config_dir(tmp_path) -> Path:
    """A fresh config dir with a 2-scenario corpus + caps sized to pass.
    Idempotent: re-creates files if called twice for the same tmp_path."""
    d = tmp_path / "config"
    d.mkdir(parents=True, exist_ok=True)
    golds = tmp_path / "golds"
    golds.mkdir(parents=True, exist_ok=True)
    gold = golds / "g.txt"
    gold.write_text("gold", encoding="utf-8")
    import hashlib
    sha = hashlib.sha256(b"gold").hexdigest()
    corpus = {
        "scenarios": [
            {"id": f"s{i}", "tier": "probe", "family": "f", "k": 1,
             "gold_ref": {"path": "g.txt", "sha256": sha}}
            for i in range(2)
        ]}
    (d / "corpus.yaml").write_text(yaml.safe_dump(corpus), encoding="utf-8")
    (d / "thresholds.yaml").write_text(
        yaml.safe_dump({"determinism": {"epsilon": 1e-6}, "cal": {}}),
        encoding="utf-8")
    (d / "arms.yaml").write_text(yaml.safe_dump({"arms": [
        {"arm_id": "mock", "adapter": "battery.arms.mock",
         "price_per_1k_usd": 0.0, "expected_tokens_per_episode": 64}]}),
        encoding="utf-8")
    (d / "budget.yaml").write_text(yaml.safe_dump(
        {"max_episodes": 1000, "max_estimated_cost_usd": 50.0}),
        encoding="utf-8")
    return d


def _run(tmp_path, **kw) -> tuple[ExitCode, Path, dict]:
    cfg_dir = kw.pop("config_dir", None) or _config_dir(tmp_path)
    out = kw.pop("out_dir", tmp_path / "out")
    code = run_battery(RunConfig(config_dir=cfg_dir, out_dir=out, **kw),
                       stdout=lambda _: None)
    attempt_dir = sorted(out.iterdir())[0]
    summary = json.loads((attempt_dir / "summary.json").read_text())
    return code, attempt_dir, summary


class TestRunArtifacts:
    def test_per_scenario_artifacts_and_run_id(self, tmp_path):
        code, attempt, summary = _run(tmp_path, mock=True, seed=7)  # noqa: RUF059
        assert code is ExitCode.OK
        names = [a.name for a in attempt.glob("*.json")
                 if a.name != "summary.json"]
        assert len(names) == 2  # 2 scenarios × 1 mock arm
        # run_id = {episode_seed}-{arm}-{scenario}; episode seed = base+index
        assert sorted(names) == ["7-mock-s0.json", "8-mock-s1.json"]
        for n in names:
            art = json.loads((attempt / n).read_text())
            assert art["run_id"] == n[:-5]
            assert art["seed"] == int(n.split("-")[0])
            assert art["run_id"].startswith(f"{art['seed']}-mock-")

    def test_artifact_schema_keys(self, tmp_path):
        _, attempt, _ = _run(tmp_path, mock=True)
        art = json.loads((attempt / next(
            n for n in attempt.iterdir() if n.name != "summary.json")).read_text())
        for key in ("schema_version", "run_id", "seed", "arm", "scenario_id",
                    "tier", "model", "determinism", "episode_trace",
                    "metric_values", "model_call_outcomes", "ep_outcome",
                    "isolation_breach", "excluded", "setup", "timestamps",
                    "provenance"):
            assert key in art, key
        assert art["schema_version"] == "1.1"
        assert art["model"]["temperature"] == 0.0
        assert art["ep_outcome"] == "converged"
        assert art["isolation_breach"] is False
        assert art["episode_trace"]["n_turns"] >= 1

    def test_summary_schema(self, tmp_path):
        _, _, summary = _run(tmp_path, mock=True)
        assert summary["schema_version"] == "1.1"
        assert summary["run"]["exit_code"] == 0
        assert summary["arms"][0]["arm_present"] is True
        assert summary["arms"][0]["valid_episodes"] == 2

    def test_deterministic_seed_ordering(self, tmp_path):
        _, _, summary = _run(tmp_path, mock=True, seed=3)
        assert summary["run"]["seed"] == 3
        run_ids = summary["run"]["run_ids"]
        assert run_ids == sorted(run_ids)  # scenario iteration sorted


class TestBudgetGuard:
    def test_over_budget_refuses(self, tmp_path):
        cfg = _config_dir(tmp_path)
        (cfg / "budget.yaml").write_text(yaml.safe_dump(
            {"max_episodes": 1, "max_estimated_cost_usd": 0.0}),
            encoding="utf-8")
        with pytest.raises(ConfigError):
            run_battery(RunConfig(config_dir=cfg, out_dir=tmp_path / "o",
                                  mock=True), stdout=lambda _: None)

    def test_max_episodes_flag_vs_budget(self, tmp_path):
        cfg = _config_dir(tmp_path)
        (cfg / "budget.yaml").write_text(yaml.safe_dump(
            {"max_episodes": 5, "max_estimated_cost_usd": 50.0}),
            encoding="utf-8")
        # --max-episodes 1 < run size (2) → refused (budget + flag both apply)
        with pytest.raises(ConfigError):
            run_battery(RunConfig(config_dir=cfg, out_dir=tmp_path / "o1",
                                  mock=True, max_episodes=1),
                        stdout=lambda _: None)
        # --max-episodes 3 ≥ run size (2) and < budget 5 → allowed
        code, _, _ = _run(tmp_path, config_dir=cfg, mock=True, max_episodes=3)
        assert code is ExitCode.OK


class _InitFailingArm:
    arm_id = "mock"
    model_id = "mock-agent"
    temperature = 0.0

    def setup_scenarios(self, scenarios):
        raise ArmUnavailable("init fails")

    def retrieve(self, context):
        raise ArmUnavailable

    def record(self, context, item):
        return None

    def isolation_namespace(self):
        return "init-failing"


class _OkArm:
    arm_id = "mock"
    model_id = "mock-agent"
    temperature = 0.0

    def __init__(self, plan=None):
        self._plan = plan or [
            {"turn": 1, "tokens": 50, "tool_calls": 0, "re_derivations": 0}]

    def setup_scenarios(self, scenarios):
        return None

    def retrieve(self, context):
        return []

    def record(self, context, item):
        return None

    def isolation_namespace(self):
        return "ok-arm"

    def trajectory_plan(self, seed):
        return self._plan


class TestExit4Boundaries:
    def test_arm_init_failure_summary_only(self, tmp_path, monkeypatch):
        import battery.runner.run as run_mod
        monkeypatch.setattr(run_mod, "_resolve_arm", lambda *a, **k: _InitFailingArm())
        code, attempt, summary = _run(tmp_path, mock=True)
        assert code is ExitCode.ARM_FAILED
        assert summary["arms"][0]["arm_present"] is False
        # summary-only: no episode artifacts
        names = [n.name for n in attempt.iterdir()]
        assert "summary.json" in names
        assert len(names) == 1

    def test_all_episodes_failed_exit4_after_artifacts(self, tmp_path, monkeypatch):
        """(b1) all-failed → per-scenario artifacts + summary written, THEN
        exit 4 — never silent exit 0 on empty aggregates."""
        import battery.runner.run as run_mod
        from battery.arms.mock import InjectionPolicy, MockArm

        def _failing(*a, **k):
            return MockArm(policy=InjectionPolicy(raise_arm_unavailable=True))
        monkeypatch.setattr(run_mod, "_resolve_arm", _failing)
        code, attempt, summary = _run(tmp_path, mock=True)
        assert code is ExitCode.ARM_FAILED
        # artifacts exist (counted in artifact) before the exit-4 computation
        names = [n.name for n in attempt.iterdir()]
        assert len(names) == 3  # 2 episode artifacts + summary
        assert summary["arms"][0]["valid_episodes"] == 0
        assert summary["arms"][0]["excluded"]["count"] == 2

    def test_mixed_arms_exit4_absent_marked(self, tmp_path, monkeypatch):
        """Arm A init-fails, arm B completes → exit 4, absent marked,
        successful arm's artifacts still written."""
        import battery.runner.run as run_mod
        calls = {"n": 0}

        def _resolver(arm_id, arm_config, *, mock):
            calls["n"] += 1
            if calls["n"] == 1:
                return _InitFailingArm()
            return _OkArm()
        monkeypatch.setattr(run_mod, "_resolve_arm", _resolver)
        cfg = _config_dir(tmp_path)
        (cfg / "arms.yaml").write_text(yaml.safe_dump({"arms": [
            {"arm_id": "mock", "adapter": "battery.arms.mock",
             "price_per_1k_usd": 0.0, "expected_tokens_per_episode": 64},
            {"arm_id": "mock2", "adapter": "battery.arms.mock",
             "price_per_1k_usd": 0.0, "expected_tokens_per_episode": 64}]}),
            encoding="utf-8")
        code, attempt, summary = _run(tmp_path, config_dir=cfg, arms=["mock", "mock2"])
        assert code is ExitCode.ARM_FAILED
        by_id = {a["arm_id"]: a for a in summary["arms"]}
        assert by_id["mock"]["arm_present"] is False
        assert by_id["mock2"]["arm_present"] is True
        names = [n.name for n in attempt.iterdir()]
        assert len([n for n in names if n != "summary.json"]) == 2  # arm B artifacts


class TestMockArmContract:
    def test_metric_values_nonempty(self, tmp_path):
        _, attempt, _ = _run(tmp_path, mock=True)
        art = json.loads((attempt / next(
            n for n in attempt.iterdir() if n.name != "summary.json")).read_text())
        assert art["metric_values"]["n_turns"] >= 1
        assert art["metric_values"]["total_tokens"] > 0
