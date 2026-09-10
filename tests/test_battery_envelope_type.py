"""#1416 — a non-conforming real envelope is a realism violation (excluded),
never a mid-run crash (seen live at scenario 58 of the E2E-1.1 real run)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from battery.enums import ExitCode
from battery.runner.run import RunConfig, run_battery


class _BadEnvelopeCaller:
    """Meter-protocol caller that emits an envelope missing
    stated_confidence (None) on its second call — the live crash shape."""
    model_id = "deepseek/deepseek-v4-flash"
    temperature = 0.0

    def __init__(self):
        self.calls = 0
        self._spend_usd = 0.0001

    @property
    def spent_usd(self) -> float:
        return self._spend_usd

    def totals(self) -> dict:
        return {"calls": self.calls, "prompt_tokens": 0,
                "completion_tokens": 0, "cost_usd": round(self._spend_usd, 6)}

    def call(self, *, prompt: str) -> str:
        self.calls += 1
        # EVERY call emits the malformed envelope (confidence None) — the
        # live crash shape. The bounded repair re-ask cannot rescue it, so
        # the run must still complete with honest exclusions, never crash.
        env = {"position": "x", "stated_confidence": None,
               "undecided": False, "defeat_conditions": [],
               "intents": [], "citations": []}
        return f"deliberation text.\n{json.dumps(env)}"


def _cfg(tmp_path: Path) -> Path:
    d = tmp_path / "cfg"
    golds = tmp_path / "golds"
    golds.mkdir(parents=True, exist_ok=True)
    (golds / "g.txt").write_text("gold", encoding="utf-8")
    sha = hashlib.sha256(b"gold").hexdigest()
    corpus = {"scenarios": [
        {"id": f"s{i}", "tier": "stream", "family": "L1",
         "task_type": "decision", "k": 0,
         "prompt": {"preamble": f"Resolve {i}."},
         "question": f"question {i}?",
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
         "price_per_1k_usd": 0.0011, "expected_tokens_per_episode": 100,
         "model_pin": "deepseek/deepseek-v4-flash", "temperature": 0.0}]}),
        encoding="utf-8")
    (d / "budget.yaml").write_text(yaml.safe_dump(
        {"max_episodes": 1000, "max_estimated_cost_usd": 50.0}),
        encoding="utf-8")
    return d


def test_bad_envelope_excludes_not_crashes(tmp_path):
    out = tmp_path / "out"
    code = run_battery(RunConfig(config_dir=_cfg(tmp_path), out_dir=out,
                                 executor="real", arms=["a0"],
                                 caller_factory=_BadEnvelopeCaller),
                       stdout=lambda _: None)
    # runs COMPLETE (no crash) with every non-conforming episode excluded
    assert code is ExitCode.ARM_FAILED  # all excluded -> honest arm-failed
    attempt = sorted(out.iterdir())[0]
    assert (attempt / "summary.json").is_file(), "summary must be written"
    summary = json.loads((attempt / "summary.json").read_text())
    assert summary["arms"][0]["excluded"]["count"] == 2, (
        "both bad-envelope episodes must exclude, never crash the run")


def test_bad_envelope_repair_rescues_when_model_recovers(tmp_path):
    """#1416: a schema violation gets ONE corrective re-ask of the SAME
    question; when the model's second answer conforms, the episode is
    VALID (not excluded) — the repair is a real re-answer, not a
    fabrication."""
    class _OnceBadCaller(_BadEnvelopeCaller):
        def call(self, *, prompt: str) -> str:
            self.calls += 1
            if self.calls == 2:
                env = {"position": "x", "stated_confidence": None,
                       "undecided": False, "defeat_conditions": [],
                       "intents": [], "citations": []}
            else:
                env = {"position": "Proceed", "stated_confidence": 0.8,
                       "undecided": False,
                       "defeat_conditions": ["data-loss"],
                       "intents": [], "citations": []}
            return f"deliberation text.\n{json.dumps(env)}"

    out = tmp_path / "out"
    code = run_battery(RunConfig(config_dir=_cfg(tmp_path), out_dir=out,
                                 executor="real", arms=["a0"],
                                 caller_factory=_OnceBadCaller),
                       stdout=lambda _: None)
    assert code is ExitCode.OK, "repaired episodes must count as valid"
    attempt = sorted(out.iterdir())[0]
    summary = json.loads((attempt / "summary.json").read_text())
    assert summary["arms"][0]["excluded"]["count"] == 0
    art = summary["arms"][0]["artifacts"][0]
    tr = json.loads((attempt / art).read_text())
    repaired = [t for t in tr["episode_trace"]["turns"] if t.get("repaired")]
    assert repaired, "the repaired turn must be recorded in the artifact"
    assert "[[repair]]" in repaired[0]["content"], (
        "the trace must carry the model's repaired answer")


def test_shared_caller_rows_attribute_per_episode(tmp_path):
    """Review #2717 P2: an injected caller_factory may hand back a SHARED
    caller whose row list already holds earlier episodes' calls. Each
    episode's turns must be attributed to ITS OWN rows (never from index 0),
    and a prompt-template position echo must never be scored."""
    class _Row:
        def __init__(self, ct):
            self.prompt_tokens = 1
            self.completion_tokens = ct
            self.cost_usd = 0.0

    class _SharedCaller:
        model_id = "deepseek/deepseek-v4-flash"
        temperature = 0.0

        def __init__(self):
            self.rows = []
            self.calls = 0

        @property
        def spent_usd(self) -> float:
            return 0.0

        def totals(self) -> dict:
            return {"calls": self.calls, "prompt_tokens": 0,
                    "completion_tokens": 0, "cost_usd": 0.0}

        def call(self, *, prompt: str) -> str:
            self.calls += 1
            self.rows.append(_Row(self.calls))  # token == call ordinal
            env = {"position": "Proceed with mitigation",
                   "stated_confidence": 0.8, "undecided": False,
                   "defeat_conditions": ["data-loss"], "intents": [],
                   "citations": []}
            return f"deliberation.\n{json.dumps(env)}"

    shared = _SharedCaller()
    out = tmp_path / "out"
    code = run_battery(RunConfig(config_dir=_cfg(tmp_path), out_dir=out,
                                 executor="real", arms=["a0"],
                                 caller_factory=lambda: shared),
                       stdout=lambda _: None)
    assert code is ExitCode.OK
    attempt = sorted(out.iterdir())[0]
    summary = json.loads((attempt / "summary.json").read_text())
    arts = summary["arms"][0]["artifacts"]
    assert len(arts) == 2
    per_ep = []
    for art in arts:
        tr = json.loads((attempt / art).read_text())
        per_ep.append([t["tokens"] for t in tr["episode_trace"]["turns"]])
    # episode 2's tokens must CONTINUE from episode 1 (shared caller), i.e.
    # they must not restart at 1
    assert per_ep[1][0] == len(per_ep[0]) + 1, (
        f"episode 2 turn 1 must read its OWN row, got {per_ep[1][0]} "
        f"(ep1 had {len(per_ep[0])} turns)")
    assert min(per_ep[1]) > max(per_ep[0]), (
        "no episode-1 row may be attributed to episode 2")


def test_template_position_echo_rejected():
    """A verbatim echo of the prompt template token is a harness artifact,
    never a position — validate_envelope must refuse it."""
    from battery.runner.executor import validate_envelope

    for bad in ("<one sentence>", "<ONE SENTENCE>", "one sentence"):
        try:
            validate_envelope({"position": bad, "stated_confidence": 0.5,
                               "undecided": False})
        except ValueError:
            continue
        raise AssertionError(f"template echo {bad!r} must be rejected")
    ok = validate_envelope({"position": "Hire the external finalist.",
                            "stated_confidence": 0.5, "undecided": False})
    assert ok.position == "Hire the external finalist."
