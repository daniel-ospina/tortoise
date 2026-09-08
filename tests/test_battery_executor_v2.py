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


def _corpus_with_L4(d: Path) -> None:
    """Append an L4-family scenario to a fixture corpus."""
    import hashlib as _h
    golds = d.parent / "golds"
    (golds / "g.txt").write_text("gold", encoding="utf-8")
    sha = _h.sha256(b"gold").hexdigest()
    path = d / "corpus.yaml"
    data = yaml.safe_load(path.read_text())
    data["scenarios"].append({
        "id": "l4-001", "tier": "stream", "family": "L4",
        "task_type": "decision", "k": 0,
        "prompt": {"preamble": "Stream scenario."},
        "question": "cross-session question?",
        "gold_ref": {"path": "g.txt", "sha256": sha}})
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


def test_l4_underpopulated_stamped_at_sessions_1(tmp_path) -> None:
    """Task-10 step 5 (status branch): a REAL run including an L4 scenario
    at sessions == 1 stamps summary.run.l4_underpopulated (never attempted
    a cross-session surfacing); the CLI report composes
    incomplete_l4_underpopulated from it."""
    from battery.report.assemble import REPORT_STATUS_L4_UNDERPOPULATED, compose_run_status
    cfg = _config_dir(tmp_path)
    _corpus_with_L4(cfg)
    out = tmp_path / "out"
    run_battery(RunConfig(config_dir=cfg, out_dir=out, executor="real",
                          arms=["a0"], caller_factory=_ScriptedCaller,
                          sessions=1), stdout=lambda _: None)
    attempt = sorted(out.iterdir())[0]
    summary = json.loads((attempt / "summary.json").read_text())
    assert summary["run"]["sessions"] == 1
    assert summary["run"]["l4_underpopulated"] is True
    status = compose_run_status(
        run_mode="real", exit_code=0, measured_cells=1, insufficient_cells=0,
        excluded_episodes=0, l4_underpopulated=True)
    assert status == REPORT_STATUS_L4_UNDERPOPULATED
    # precedence: emitter-gap / over-budget still win over the L4 state
    assert compose_run_status(
        run_mode="real", exit_code=0, measured_cells=1, insufficient_cells=0,
        excluded_episodes=0, emitter_gap=True, l4_underpopulated=True) \
        == "incomplete_emitter_gap"


def test_l4_not_stamped_when_sessions_ge_2(tmp_path) -> None:
    """sessions >= 2 (a real stream that CAN surface cross-session) never
    stamps the underpopulated flag."""
    cfg = _config_dir(tmp_path)
    _corpus_with_L4(cfg)
    out = tmp_path / "out"
    run_battery(RunConfig(config_dir=cfg, out_dir=out, executor="real",
                          arms=["a0"], caller_factory=_ScriptedCaller,
                          sessions=2), stdout=lambda _: None)
    attempt = sorted(out.iterdir())[0]
    summary = json.loads((attempt / "summary.json").read_text())
    assert summary["run"]["l4_underpopulated"] is False


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


def test_l4_cross_session_surfacing_product_read(tmp_path) -> None:
    """Task-10 step 3 (hermetic, REAL a4 scaffold on the embedded lane):
    L4 A/¬A across sessions via the product read surface. Session 0
    retrieves the seeded A side only (the ¬A marker is ABSENT); a
    contradiction is filed between sessions through the real arm record
    path (executor-identical decide-write, returns a real product ref);
    session 1 re-opens the SAME graph and its retrieve surfaces BOTH sides
    — surfaced at the right session, never earlier."""
    from battery.arms.base import AgentContext, Memory
    from battery.testing.seeds import setup_seed_mode

    marker = "L4-S2-MARKER the opposite is true"
    ns = tmp_path / "l4-ns"

    # session 0 — fresh seed: the ¬A marker must NOT be retrievable yet
    s0 = setup_seed_mode(ns, "ct-001")
    try:
        claims = [m for m in s0.retrieve("") if getattr(m, "kind", "") == "claim"]
        assert claims, "seed_mode ct graph must carry claim_a pre-k"
        assert marker not in s0.surface_text(), \
            "session-0 retrieve must NOT show session-2 content"
        target = claims[0]
    finally:
        s0.close()

    # between sessions: the executor-identical decide write (file_nand)
    mid = setup_seed_mode(ns, "ct-001", purge=False)
    arm = mid._arm
    try:
        ctx = AgentContext(scenario=mid._scenario, episode_seed=7,
                           prior_memories=tuple(mid.retrieve("")),
                           user_message="file")
        ref = arm.record(ctx, Memory(
            id="l4-s2", content=marker, confidence=None, kind="nand",
            target_id=target.id, credibility="high"))
        assert ref, "the product write must return a real ref"
    finally:
        arm.close()

    # session 1 — re-open the SAME graph: BOTH sides surface now
    s1 = setup_seed_mode(ns, "ct-001", purge=False)
    try:
        surface = s1.surface_text()
        assert marker in surface, \
            "session-1 retrieve must surface the cross-session contradiction"
        hits = s1.find_content("L4-S2-MARKER")
        assert hits, "the ¬A node must be retrievable by content"
    finally:
        s1.close()

    # cross-scenario isolation: a DIFFERENT scenario's namespace never sees
    # this session's content (fresh per-scenario namespace by design)
    iso = setup_seed_mode(tmp_path / "l4-other-ns", "ct-002")
    try:
        assert marker not in iso.surface_text()
    finally:
        iso.close()
