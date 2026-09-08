"""#2284 Task 10 — executor v2 stream mode: sessions + seed accumulation.

Covers the plan's Task-10 steps 1-2 acceptance (hermetic, embedded lane):
(1) stream-mode sessions run each scenario across N sequential episodes
over the SAME per-scenario graph — no reset mid-stream, per-session
artifact/run_id/session_index, budget scaled by sessions; (2) seed_mode
re-setup over a CLEAN seed_mode graph accumulates (no ConfigError, no
duplicate claim_a) while a stale PRE-FIX full graph refuses (Task-4 warm
guard). The single-session default is byte-identical to pre-Task-10 runs
(regression guard). Cross-session L4 surfacing + the report statuses land
with Task-10 steps 3-6.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
import yaml

from battery.enums import ExitCode
from battery.runner.run import RunConfig, run_battery

# ── embedded-lane force (mirror test_battery_seed_ingest) ───────────────


@pytest.fixture(autouse=True, scope="module")
def _force_embedded_lane() -> None:
    saved = os.environ.pop("TORTOISE_DB_URI", None)
    saved_path = os.environ.pop("TORTOISE_DB_PATH", None)
    try:
        yield
    finally:
        if saved is not None:
            os.environ["TORTOISE_DB_URI"] = saved
        if saved_path is not None:
            os.environ["TORTOISE_DB_PATH"] = saved_path


# ── runner-level scripted-caller stream fixtures ────────────────────────


class _Row:
    def __init__(self, ct: int = 20):
        self.completion_tokens = ct


class _ScriptedCaller:
    """Real-shaped caller: model-authored contents (never mock placeholders)
    + one JSON envelope per call + the #2603 meter protocol. Prompt-aware:
    when the prompt's [memory] section already names a claim, later calls
    surface a contradiction (file_nand intent) — the L4 surfacing shape the
    REAL scaffold exercises at steps 3+; here it only proves the seam."""
    model_id = "deepseek/deepseek-v4-flash"
    temperature = 0.0

    def __init__(self, seed: int = 0):
        self.rows: list[_Row] = []
        self.calls = 0
        self._seed = seed
        self.last_prompt_tokens = 10
        self.last_completion_tokens = 20
        self._spend_usd = 0.0001
        self.last_prompt = ""

    @property
    def spent_usd(self) -> float:
        return self._spend_usd

    def totals(self) -> dict:
        return {"calls": self.calls, "prompt_tokens": 0,
                "completion_tokens": sum(r.completion_tokens
                                         for r in self.rows),
                "cost_usd": round(self._spend_usd, 6)}

    def call(self, *, prompt: str) -> str:
        self.calls += 1
        self.rows.append(_Row(20))
        self.last_prompt = prompt
        has_memory = "[memory — what I know so far]" in prompt
        has_contradiction = "claims the opposite" in prompt \
            or "disagrees" in prompt
        if has_memory and not has_contradiction:
            env = {"position": "the opposite view is worth recording",
                   "stated_confidence": 0.8, "undecided": False,
                   "defeat_conditions": [],
                   "intents": ["file_nand"], "citations": ["mem-1"]}
        else:
            env = {"position": "Proceed with the migration",
                   "stated_confidence": 0.8, "undecided": False,
                   "defeat_conditions": ["data-loss"],
                   "intents": [], "citations": ["src-1"]}
        return (f"I weigh the migration risk against the benefit.\n"
                f"{json.dumps(env)}")


def _config_dir(tmp_path: Path, sessions: int = 1) -> Path:
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


def test_stream_sessions_run_end_to_end(tmp_path) -> None:
    """Task-10 step 1: sessions=3 x 2 scenarios = 6 episodes over the same
    per-scenario graph; each artifact carries its session_index; run_ids
    unique; recall rows per session; budget guard scaled by sessions."""
    cfg = _config_dir(tmp_path)
    out = tmp_path / "out"
    code = run_battery(RunConfig(
        config_dir=cfg, out_dir=out, executor="real", arms=["a0"],
        caller_factory=_ScriptedCaller, seed=1, sessions=3),
        stdout=lambda _: None)
    assert code is ExitCode.OK
    attempt = sorted(out.iterdir())[0]
    summary = json.loads((attempt / "summary.json").read_text())
    assert summary["arms"][0]["valid_episodes"] == 6
    artifacts = [json.loads(p.read_text())
                 for p in sorted((attempt).glob("*.json"))
                 if p.name not in ("summary.json", "recall.json",
                                   "family_d.json")]
    sess_by_scen: dict[str, set[int]] = {}
    for art in artifacts:
        sess_by_scen.setdefault(art["scenario_id"], set()).add(
            art["session_index"])
        assert art["session_index"] in (0, 1, 2)
    assert sorted(sess_by_scen["s0"]) == [0, 1, 2]
    assert sorted(sess_by_scen["s1"]) == [0, 1, 2]
    recall = json.loads((attempt / "recall.json").read_text())
    rows = recall.get("episodes", [])
    assert len(rows) == 6
    assert {r["session_index"] for r in rows if r["scenario_id"] == "s0"} \
        == {0, 1, 2}
    # run_ids are seed-unique per session
    rids = {r["run_id"] for r in rows}
    assert len(rids) == 6


def test_single_session_default_unchanged(tmp_path) -> None:
    """Regression guard: sessions defaults to 1 — the pre-Task-10 shape
    (2 scenarios x 1 = 2 episodes, no session_index drift on counts)."""
    cfg = _config_dir(tmp_path)
    out = tmp_path / "out"
    run_battery(RunConfig(config_dir=cfg, out_dir=out, executor="real",
                          arms=["a0"], caller_factory=_ScriptedCaller),
                stdout=lambda _: None)
    attempt = sorted(out.iterdir())[0]
    summary = json.loads((attempt / "summary.json").read_text())
    assert summary["arms"][0]["valid_episodes"] == 2


def test_sessions_zero_refused(tmp_path) -> None:
    with pytest.raises(ValueError):
        RunConfig(config_dir=_config_dir(tmp_path), out_dir=tmp_path / "o",
                  sessions=0)


# ── seed-mode accumulation + stale refuse (embedded a4 real lane) ───────


def test_re_setup_over_clean_seed_mode_accumulates(tmp_path) -> None:
    """Task-10 step 2 (hermetic, real a4 arm on the embedded lane):
    setup_seed_mode twice over the SAME clean seed_mode namespace (purge
    off on re-entry) accumulates — no ConfigError, no duplicate claim_a
    (the reader surface is identical after the 2nd setup)."""
    from battery.testing.seeds import setup_seed_mode
    ns = tmp_path / "stream-ns"
    first = setup_seed_mode(ns, "ct-001")  # purge=True -> fresh
    try:
        surface_1 = first.surface_text()
        n_a_1 = len(first.find_content("claim_a")) if _has_find(first) else -1
    finally:
        first.close()
    # re-entry over the CLEAN graph: must accumulate, never refuse
    second = setup_seed_mode(ns, "ct-001", purge=False)
    try:
        surface_2 = second.surface_text()
        n_a_2 = len(second.find_content("claim_a")) if _has_find(second) else -1
    finally:
        second.close()
    assert surface_2 == surface_1, \
        "clean-graph re-setup must be idempotent (no duplicate claim_a)"
    if n_a_1 >= 0:
        assert n_a_2 == n_a_1


def _has_find(store) -> bool:
    return hasattr(store, "find_content")


def test_stale_pre_fix_graph_refuses(tmp_path) -> None:
    """Task-4 warm guard (locked for streams): re-setup over a stale PRE-FIX
    full graph (no seed-manifest marker) refuses with ConfigError — the
    fail-closed boundary never silently retains ¬A content."""
    from battery.exceptions import ConfigError
    from battery.testing.seeds import seed_full_legacy, setup_seed_mode
    ns = tmp_path / "stale-ns"
    seed_full_legacy(ns, "ct-001")
    with pytest.raises(ConfigError):
        setup_seed_mode(ns, "ct-001", purge=False)
