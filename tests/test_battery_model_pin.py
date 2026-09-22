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


def test_real_preflight_refuses_unpinned_and_accepts_concrete_pin(tmp_path,
                                                                  monkeypatch):
    # hermetic: stub the real emission seam active (run.py round-3 pattern —
    # hermetic tests activate the seam by stubbing run._episode_log), then a
    # real-executor request must ConfigError BEFORE attempt-dir creation for
    # an unusable pin (zero orphaned artifacts) and ACCEPT a concrete pin at
    # the resolve seam. The class-level 'fixed' sentinel is NOT a refusal
    # branch post-Task-9 (#2746) — the arms.yaml pin wins on the instance.
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

    # Branch (ii): CONCRETE pin — Task 9 parameterizes the real arm INSTANCE
    # off the class-level 'fixed' sentinel (run.py `_resolve_arm`), so a
    # concrete arms.yaml pin must be ACCEPTED and the effective pin must be
    # the arms.yaml value (the sentinel is retired by design, #2746). Asserted
    # at the resolve seam (unit-level) so this test never executes live
    # episodes — the previous shape fell through the pre-flight and made real
    # model calls (~450 s + real spend).
    _write_pin(cfg_dir, "deepseek/deepseek-v4-flash")
    from battery.config.arms import load_arms, resolve_pinned_model
    arm_map = load_arms(cfg_dir / "arms.yaml")
    resolved = run_mod._resolve_arm("a4", arm_map["a4"], mock=False)
    assert resolved.model_id == "deepseek/deepseek-v4-flash", (
        "the arms.yaml pin must win over the class-level 'fixed' sentinel")
    assert resolved.temperature == 0.0
    pinned = resolve_pinned_model(resolved.model_id)  # concrete pin resolves
    assert getattr(pinned, "id", "") == "deepseek/deepseek-v4-flash", (
        "the resolved factory must be the pinned model, not a default")
    # …and the placeholder is still refused at the same seam (branch i's
    # check asserted at the unit boundary).
    with pytest.raises(ConfigError):
        resolve_pinned_model("flash-class-placeholder")
