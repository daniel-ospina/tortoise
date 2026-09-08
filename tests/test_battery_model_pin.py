"""#2292 Task 5 — model pin re-lock + read path + real-run pre-flight."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml

from battery.config.arms import load_arms, resolve_pinned_model
from battery.exceptions import ConfigError

CONFIG = Path(__file__).resolve().parents[1] / "battery/config"


def test_arms_pin_relocked_concrete_same_across_arms():
    arms = load_arms(CONFIG / "arms.yaml")
    real = [a for aid, a in arms.items() if aid != "mock"]
    assert {a.model_pin for a in real} == {"deepseek/deepseek-v4-flash"}
    assert {a.temperature for a in real} == {0.0}


def test_placeholder_pin_refused():
    with pytest.raises(ConfigError):
        resolve_pinned_model("flash-class-placeholder")   # sentinel never runs


def test_unknown_pin_refused():
    with pytest.raises(ConfigError):
        resolve_pinned_model("no/such-model")


def test_concrete_pin_resolves_registry_factory():
    # decision (a): deepseek/deepseek-v4-flash UNCAPPED temp 0 via the
    # model_adapters registry (no network in the constructor).
    m = resolve_pinned_model("deepseek/deepseek-v4-flash")
    assert getattr(m, "id", "") == "deepseek/deepseek-v4-flash"
    assert getattr(m, "max_tokens", None) is None      # UNCAPPED
    assert getattr(m, "temperature", None) == 0.0


def _hermetic_cfg(tmp_path) -> Path:
    """Yaml-only config dir with a 2-scenario corpus + sized caps (the
    test_battery_run._config_dir pattern) — corpus.json absent so the
    freshness gate no-ops. arms.yaml is NOT copied from live CONFIG: it is
    written per branch below so the test is ORDER-INDEPENDENT — it must stay
    green both RED (live pins still placeholders) AND after Step 5.3
    re-locks the live arms.yaml to concrete pins."""
    d = tmp_path / "cfg"
    d.mkdir(parents=True, exist_ok=True)
    golds = tmp_path / "golds"
    golds.mkdir(parents=True, exist_ok=True)
    gold = golds / "g.txt"
    gold.write_text("gold", encoding="utf-8")
    sha = hashlib.sha256(b"gold").hexdigest()
    corpus = {"scenarios": [
        {"id": f"s{i}", "tier": "probe", "family": "f", "k": 1,
         "gold_ref": {"path": "g.txt", "sha256": sha}}
        for i in range(2)]}
    (d / "corpus.yaml").write_text(yaml.safe_dump(corpus), encoding="utf-8")
    (d / "thresholds.yaml").write_text(
        yaml.safe_dump({"determinism": {"epsilon": 1e-6}, "cal": {}}),
        encoding="utf-8")
    (d / "budget.yaml").write_text(yaml.safe_dump(
        {"max_episodes": 1000, "max_estimated_cost_usd": 50.0}),
        encoding="utf-8")
    return d


def _write_pin(cfg_dir: Path, pin: str) -> None:
    (cfg_dir / "arms.yaml").write_text(yaml.safe_dump({"arms": [
        {"arm_id": "mock", "adapter": "battery.arms.mock", "config": {},
         "model_pin": pin, "temperature": 0.0},
        {"arm_id": "a4", "adapter": "battery.arms.a4_tortoise", "config": {},
         "model_pin": pin, "temperature": 0.0}]}), encoding="utf-8")


def test_real_preflight_refuses_unpinned_or_fixed_sentinel(tmp_path,
                                                           monkeypatch):
    # hermetic: stub the real emission seam active (run.py round-3 pattern —
    # hermetic tests activate the seam by stubbing run._episode_log), then a
    # real-executor request must ConfigError BEFORE attempt-dir creation on
    # EITHER refusal branch (zero orphaned artifacts).
    from battery.runner import run as run_mod
    run_mod._episode_log = lambda *a, **k: []        # seam "active"
    cfg_dir = _hermetic_cfg(tmp_path)
    out = tmp_path / "out"

    # Branch (i): placeholder-pin arms.yaml fixture -> the pin gate refuses.
    _write_pin(cfg_dir, "flash-class-placeholder")
    with pytest.raises(ConfigError):                 # placeholder pin refused
        run_mod.run_battery(run_mod.RunConfig(config_dir=cfg_dir, arms=["a4"],
                                              executor="real", out_dir=out),
                            stdout=lambda s: None)
    assert not out.exists() or not [p for p in out.iterdir()]  # no orphaned dir

    # Branch (ii): CONCRETE pin but the arm class still hardcodes the
    # model_id = "fixed" class sentinel (battery/arms/a4_tortoise.py etc. —
    # verified present) -> the class-sentinel gate refuses. Asserted
    # explicitly here (no trailing "..."): it clears only when Task 9's
    # executor parameterizes the arm classes off the fixed sentinel.
    _write_pin(cfg_dir, "deepseek/deepseek-v4-flash")
    with pytest.raises(ConfigError):                 # class-sentinel refused
        run_mod.run_battery(run_mod.RunConfig(config_dir=cfg_dir, arms=["a4"],
                                              executor="real", out_dir=out),
                            stdout=lambda s: None)
    assert not out.exists() or not [p for p in out.iterdir()]  # no orphaned dir
