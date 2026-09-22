"""Issue #2633 — real-run pre-flight per-arm vendor-key gate (hermetic).

The real pre-flight validated the model pin/temperature/UNCAPPED posture but
never each arm's vendor credential: ``--executor real --arms a2`` without
``MEM0_API_KEY`` (or a2b without ``ZEP_API_KEY``) passed every gate and ran
real-model spend against the adapter's seeded in-process MOCK store under
``run_mode=real`` (a false differential measure). Tests lock:

 1. real a2 with MEM0_API_KEY absent  -> ConfigError BEFORE any episode;
 2. real a2b with ZEP_API_KEY absent  -> ConfigError BEFORE any episode;
 3. real a2 WITH a fake MEM0_API_KEY + scripted caller (urlopen faked,
    zero network) -> runs, exit OK, run_mode=real;
 4. real a0 (no vendor surface) is unchanged -> runs exit OK.

All lanes are hermetic: no network, no real spend. The key surface is the
adapter class attr ``required_env_keys`` (battery/arms/a2_mem0.py /
a2b_zep.py); arms with no vendor surface carry no attr and are unaffected.
Mock/embedded lanes never touch the gate (it sits inside the
``executor == "real"`` pre-flight block).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from battery.enums import ExitCode
from battery.exceptions import ConfigError
from battery.runner.run import RunConfig, run_battery


def _config_dir(tmp_path: Path) -> Path:
    """Config dir whose arms.yaml carries the real-capable arms (a0 plain,
    a2 mem0, a2b zep) on concrete measured pins (decision (a)):
    deepseek/deepseek-v4-flash, temp 0 — so a refusal is attributable to
    the #2633 vendor-key gate, never to a pin/temp refusal."""
    d = tmp_path / "cfg"
    golds = tmp_path / "golds"
    golds.mkdir(parents=True, exist_ok=True)
    (golds / "g.txt").write_text("gold", encoding="utf-8")
    sha = hashlib.sha256(b"gold").hexdigest()
    corpus = {"scenarios": [
        {"id": f"s{i}", "tier": "probe", "family": "d", "task_type": "decision",
         "k": 0, "prompt": {"preamble": f"Resolve scenario {i}."},
         "question": f"what should we do about case {i}?",
         "gold_ref": {"path": "g.txt", "sha256": sha}}
        for i in range(2)]}
    d.mkdir(parents=True, exist_ok=True)
    (d / "corpus.yaml").write_text(yaml.safe_dump(corpus), encoding="utf-8")
    (d / "thresholds.yaml").write_text(
        yaml.safe_dump({"determinism": {"epsilon": 1e-6},
                        "cal": {"ep-variance": {"a4": 0.04}}}),
        encoding="utf-8")
    arms = []
    for arm_id, adapter in (("a0", "battery.arms.a0_plain"),
                            ("a2", "battery.arms.a2_mem0"),
                            ("a2b", "battery.arms.a2b_zep")):
        arms.append({"arm_id": arm_id, "adapter": adapter, "config": {},
                     "price_per_1k_usd": 0.5,
                     "expected_tokens_per_episode": 100,
                     "model_pin": "deepseek/deepseek-v4-flash",
                     "temperature": 0.0})
    (d / "arms.yaml").write_text(yaml.safe_dump({"arms": arms}),
                                 encoding="utf-8")
    (d / "budget.yaml").write_text(yaml.safe_dump(
        {"max_episodes": 1000, "max_estimated_cost_usd": 50.0}),
        encoding="utf-8")
    return d


class _Row:
    def __init__(self, ct: int = 20):
        self.completion_tokens = ct


class _ScriptedRealCaller:
    """Caller-bridge-shaped scripted caller (same shape as
    tests/test_battery_realism_run.py): real deliberation contents, per-call
    usage rows, and the #2603 meter protocol (spent_usd + totals) the real
    lane requires."""
    model_id = "deepseek/deepseek-v4-flash"
    temperature = 0.0

    def __init__(self):
        self.rows: list[_Row] = []
        self.calls = 0
        self._spend_usd = 0.0001

    @property
    def spent_usd(self) -> float:
        return self._spend_usd

    def totals(self) -> dict:
        return {
            "calls": self.calls,
            "prompt_tokens": 0,
            "completion_tokens": sum(r.completion_tokens for r in self.rows),
            "cost_usd": round(self._spend_usd, 6),
        }

    def call(self, *, prompt: str) -> str:
        self.calls += 1
        self.rows.append(_Row(20))
        positions = ["Proceed with the migration", "note", "note",
                     "Proceed with the migration"]
        i = min(self.calls - 1, 3)
        env = {"position": positions[i], "stated_confidence": 0.8,
               "undecided": False, "defeat_conditions": ["data-loss"],
               "intents": [], "citations": ["src-1"]}
        return (f"I weigh the migration risk against the benefit; the "
                f"counter-argument names data-loss during migration.\n"
                f"{json.dumps(env)}")


class _EmptyResultsResp:
    """urlopen seam double: an EMPTY real store (``results: []``) — the
    real _real_retrieve code path (request/parse) runs with zero network."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return json.dumps({"results": []}).encode()


@pytest.fixture(autouse=True)
def _no_vendor_keys(monkeypatch):
    """Start hermetic: vendor keys absent. Individual tests opt in by
    setting the one key they exercise; OPENROUTER_API_KEY is faked because
    the pin pre-flight constructs the registry adapter (no network)."""
    monkeypatch.delenv("MEM0_API_KEY", raising=False)
    monkeypatch.delenv("ZEP_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-fake")


def _no_attempt_dir(out: Path) -> None:
    assert not out.exists() or not [p for p in out.iterdir()], (
        "vendor-key refusal must happen BEFORE any episode/attempt dir "
        "(zero orphaned artifacts)")


def test_real_a2_without_mem0_key_refused_before_episodes(tmp_path):
    cfg = _config_dir(tmp_path)
    out = tmp_path / "out"
    with pytest.raises(ConfigError) as ei:
        run_battery(RunConfig(config_dir=cfg, out_dir=out, executor="real",
                              arms=["a2"],
                              caller_factory=_ScriptedRealCaller),
                    stdout=lambda _: None)
    msg = str(ei.value)
    assert "a2" in msg and "MEM0_API_KEY" in msg
    _no_attempt_dir(out)


def test_real_a2b_without_zep_key_refused_before_episodes(tmp_path):
    cfg = _config_dir(tmp_path)
    out = tmp_path / "out"
    with pytest.raises(ConfigError) as ei:
        run_battery(RunConfig(config_dir=cfg, out_dir=out, executor="real",
                              arms=["a2b"],
                              caller_factory=_ScriptedRealCaller),
                    stdout=lambda _: None)
    msg = str(ei.value)
    assert "a2b" in msg and "ZEP_API_KEY" in msg
    _no_attempt_dir(out)


def test_real_a2_with_key_runs_hermetic(tmp_path, monkeypatch):
    """Real a2 WITH a fake MEM0_API_KEY + scripted caller + faked urlopen
    (empty real store, zero network) runs to exit OK under run_mode=real —
    the gate is a fail-closed key assertion, not a ban on a2 real runs."""
    monkeypatch.setenv("MEM0_API_KEY", "sk-fake-mem0")
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda *a, **k: _EmptyResultsResp())
    cfg = _config_dir(tmp_path)
    out = tmp_path / "out"
    code = run_battery(RunConfig(config_dir=cfg, out_dir=out, executor="real",
                                 arms=["a2"],
                                 caller_factory=_ScriptedRealCaller),
                       stdout=lambda _: None)
    assert code is ExitCode.OK
    attempt = sorted(out.iterdir())[0]
    summary = json.loads((attempt / "summary.json").read_text())
    assert summary["run"]["run_mode"] == "real"
    assert summary["arms"][0]["arm_id"] == "a2"
    assert summary["arms"][0]["valid_episodes"] == 2


def test_real_a0_no_vendor_surface_unchanged(tmp_path):
    """a0 (no vendor surface) real runs exit OK without MEM0/ZEP keys — the
    gate only asserts keys arms declare; no-store arms are unaffected."""
    cfg = _config_dir(tmp_path)
    out = tmp_path / "out"
    code = run_battery(RunConfig(config_dir=cfg, out_dir=out, executor="real",
                                 arms=["a0"],
                                 caller_factory=_ScriptedRealCaller),
                       stdout=lambda _: None)
    assert code is ExitCode.OK
    attempt = sorted(out.iterdir())[0]
    summary = json.loads((attempt / "summary.json").read_text())
    assert summary["run"]["run_mode"] == "real"
    assert summary["arms"][0]["arm_id"] == "a0"
    assert summary["arms"][0]["valid_episodes"] == 2
