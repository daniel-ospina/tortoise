"""Task 9 — real-executor run-level realism (hermetic).

A fixture config + a scripted caller drive `battery run --arms a0
--executor real` end-to-end with ZERO network: the episode trace carries
REAL turn contents from the caller (never the mock "turn N (seed S)"
placeholders), run_mode=real, the event log covers the MANDATORY
schema-v1.1 fields, and no episode is excluded (all-ok realism gate).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from battery.enums import ExitCode
from battery.runner.emit import MANDATORY
from battery.runner.run import RunConfig, run_battery


def _config_dir(tmp_path: Path) -> Path:
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
    (d / "arms.yaml").write_text(yaml.safe_dump({"arms": [
        {"arm_id": "a0", "adapter": "battery.arms.a0_plain", "config": {},
         "price_per_1k_usd": 0.5, "expected_tokens_per_episode": 100,
         "model_pin": "deepseek/deepseek-v4-flash", "temperature": 0.0}]}),
        encoding="utf-8")
    (d / "budget.yaml").write_text(yaml.safe_dump(
        {"max_episodes": 1000, "max_estimated_cost_usd": 50.0}),
        encoding="utf-8")
    return d


class _Row:
    def __init__(self, ct: int = 20):
        self.completion_tokens = ct


class _ScriptedRealCaller:
    """Caller-bridge-shaped scripted caller: real deliberation contents
    (never 'turn N (seed S)' placeholders) + a JSON envelope per call,
    per-call usage rows for the token capture."""
    model_id = "deepseek/deepseek-v4-flash"
    temperature = 0.0

    def __init__(self):
        self.rows: list[_Row] = []
        self.calls = 0
        self.last_prompt_tokens = 10
        self.last_completion_tokens = 20

    def call(self, *, prompt: str) -> str:
        self.calls += 1
        self.rows.append(_Row(20))
        # REVISE (call 4) holds the ALIGN position -> early exit after 1
        # cycle: align/challenge/deepen/revise = 4 calls.
        positions = ["Proceed with the migration", "note", "note",
                     "Proceed with the migration"]
        i = min(self.calls - 1, 3)
        env = {"position": positions[i], "stated_confidence": 0.8,
               "undecided": False, "defeat_conditions": ["data-loss"],
               "intents": [], "citations": ["src-1"]}
        return (f"I weigh the migration risk against the benefit; the "
                f"counter-argument names data-loss during migration.\n"
                f"{json.dumps(env)}")


@pytest.fixture(autouse=True)
def _fake_key(monkeypatch):
    # resolve_pinned_model constructs the registry adapter (no network on
    # construction); a fake key keeps any env read hermetic.
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-fake")


def test_real_executor_run_end_to_end_realism(tmp_path):
    cfg = _config_dir(tmp_path)
    out = tmp_path / "out"
    code = run_battery(RunConfig(
        config_dir=cfg, out_dir=out, executor="real", arms=["a0"],
        caller_factory=_ScriptedRealCaller, seed=1),
        stdout=lambda _: None)
    assert code is ExitCode.OK
    attempt = sorted(out.iterdir())[0]
    summary = json.loads((attempt / "summary.json").read_text())
    assert summary["run"]["run_mode"] == "real"
    episodes = [p for p in attempt.glob("*.json")
                if p.name not in ("summary.json", "recall.json")
                and not p.name.startswith("family_")]
    assert len(episodes) == 2
    for p in episodes:
        art = json.loads(p.read_text())
        trace = art.get("episode_trace", {})
        turns = trace.get("turns", [])
        assert turns, f"{p.name}: real episode must have turns"
        for t in turns:
            content = t.get("content", "")
            assert content and content.strip(), f"{p.name}: empty turn"
            # realism gate: never the fabricated mock placeholder
            assert not content.startswith("turn "), f"{p.name}: fabricated"
            assert "(seed " not in content, f"{p.name}: fabricated"
        # MANDATORY event-log fields covered on every real episode
        log = art.get("event_log", [])
        fields = {e.get("field") for e in log if e.get("field")}
        assert fields >= MANDATORY, (
            f"{p.name}: event log missing mandatory fields "
            f"{sorted(MANDATORY - fields)}")
        assert art.get("run_mode") == "real"


def test_real_executor_never_excludes_all_ok(tmp_path):
    cfg = _config_dir(tmp_path)
    out = tmp_path / "out"
    # per-episode exclusion would surface in summary excluded counts
    run_battery(RunConfig(config_dir=cfg, out_dir=out, executor="real",
                          arms=["a0"], caller_factory=_ScriptedRealCaller),
                stdout=lambda _: None)
    attempt = sorted(out.iterdir())[0]
    summary = json.loads((attempt / "summary.json").read_text())
    assert summary["run"]["exit_code"] == 0
    assert summary["arms"][0]["valid_episodes"] == 2
