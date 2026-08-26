"""Tests for the #1549 run-protocol tooling (F lane — fixtures + run-protocol
scripts for the Extractor V3 capstone).

Covers the three F-lane deliverables WITHOUT any harness (M2–M8) dependency:
    1. run_protocol.py  — the 9-step resumable checklist state machine
       (gate ordering, owner gates, confirmation-set builder).
    2. full_context.py  — the option-5 full-context comparison cell
       (ceiling/headroom measurement; reader sees the entire haystack).
    3. smoke scaffolding — the 1-question real-extractor smoke command wiring.

All tests run OFFLINE (mocked reader/judge, committed mini fixture, embedded
DB) — the full 500-Q run and real-extractor smoke are the capstone's job, not
CI's.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.longmem_eval import run_protocol as rp  # noqa: E402, I001, RUF100
from tools.longmem_eval.full_context import (
    CELL_EXTRACTION_APPROACH, full_haystack_hits, run_cell,
)
from tools.longmem_eval.judge import MockJudge
from tools.longmem_eval.reader import MockReader

MINI = Path(__file__).parent / "fixtures" / "longmemeval_mini.json"


def _mini() -> list[dict]:
    return json.loads(MINI.read_text(encoding="utf-8"))


def _fresh_state(tmp_path) -> rp.ProtocolState:
    return rp.ProtocolState(tmp_path / "state.json")


# ── 1. run_protocol state machine ───────────────────────────────────────────

def test_state_machine_initial_and_resume(tmp_path):
    """The state file persists across sessions: a fresh ProtocolState on the
    same path resumes exactly where the previous one left off."""
    state = _fresh_state(tmp_path)
    assert state.next_pending() == 1
    assert all(state.status(n) == "pending" for n in range(1, 10))

    state.pass_gate(1, "clean review (code-review skill)")
    state.pass_gate(2, "knob selected; marking calibrated (0.4)")
    assert state.next_pending() == 3

    # "new session": a fresh object on the same path sees the same state.
    resumed = rp.ProtocolState(tmp_path / "state.json")
    assert resumed.status(1) == "passed"
    assert resumed.status(2) == "passed"
    assert resumed.next_pending() == 3


def test_gate_ordering_enforced(tmp_path):
    """A step's gate cannot pass before every prior step passed."""
    state = _fresh_state(tmp_path)
    with pytest.raises(SystemExit):
        state.pass_gate(3, "too early")
    with pytest.raises(SystemExit):
        state.pass_gate(5, "too early")
    # passing in order works
    for n in range(1, 5):
        state.pass_gate(n, f"step {n} done")
    assert state.is_done(4)


def test_owner_gated_steps_require_approval(tmp_path):
    """Steps 8/9 (1k benchmark, R6/E6 follow-up) are owner-gated — the
    03-scope 'explicit owner decision' gate."""
    state = _fresh_state(tmp_path)
    for n in range(1, 8):
        state.pass_gate(n, f"step {n} done")
    with pytest.raises(SystemExit):
        state.pass_gate(8, "not approved yet")
    state.pass_gate(8, "owner said yes", owner_approve="needed for CI at V4")
    assert state.data["steps"]["8"]["owner_approved"]["reason"] == (
        "needed for CI at V4")
    with pytest.raises(SystemExit):
        state.pass_gate(9, "still not approved")
    state.pass_gate(9, "owner said yes", owner_approve="post-baseline R6/E6")


def test_fail_gate_returns_to_pending_and_reset(tmp_path):
    """Retry-then-fix (M4): a failed gate returns to pending; reset clears
    a step so it can be re-run."""
    state = _fresh_state(tmp_path)
    state.pass_gate(1, "ok")
    state.fail_gate(2, "marking calibration drifted — fixing")
    assert state.status(2) == "failed"
    state.reset(2)
    assert state.status(2) == "pending"
    assert state.is_done(1)  # earlier gates untouched


def test_plan_requires_expected_direction_for_confirm(tmp_path):
    """Step 7's expected-delta direction must be stated in advance (03-scope:
    'expected direction of the delta stated in advance')."""
    state = _fresh_state(tmp_path)
    step7 = rp.STEPS_BY_NUMBER[7]
    with pytest.raises(SystemExit):
        rp.build_command(step7, [], state=state)
    # even with the direction, the confirmation needs prior run artifacts
    with pytest.raises(SystemExit):
        rp.build_command(step7, [], state=state,
                         expected_direction="up on KU/TR, flat elsewhere")


def test_build_command_uses_base_runner_flags(tmp_path):
    """Run-step commands must use flags that exist on the BASE runner (no
    M2–M8 dependency): --split/--limit/--ingest-mode/--checkpoint/--output."""
    state = _fresh_state(tmp_path)
    for s in (rp.STEPS_BY_NUMBER[3], rp.STEPS_BY_NUMBER[5]):
        cmd = rp.build_command(s, [], state=state)
        assert cmd[0].endswith("python")
        joined = " ".join(cmd)
        assert "--ingest-mode v2" in joined
        assert "--checkpoint" in joined and "--output" in joined
        assert "--limit" in joined or "baseline" in joined  # pilot limits, baseline doesn't
    # step 3 (pilot) limits to 50 questions
    pilot = rp.build_command(rp.STEPS_BY_NUMBER[3], [], state=state)
    assert "--limit 50" in " ".join(pilot)
    # step 5 (baseline) runs the full split (no --limit)
    base = rp.build_command(rp.STEPS_BY_NUMBER[5], [], state=state)
    assert "--limit" not in " ".join(base)


def test_confirmation_subset_from_recorded_runs(tmp_path):
    """The confirmation set (step 7) = pilot questions ∪ regression sample of
    500-Q failures — read from the recorded run artifacts."""
    state = _fresh_state(tmp_path)
    run_dir = tmp_path / "runs"
    run_dir.mkdir()

    # Fake pilot report: 3 questions.
    pilot_report = run_dir / "pilot.report.json"
    pilot_report.write_text(json.dumps({"outcomes": [
        {"question_id": "q1"}, {"question_id": "q2"}, {"question_id": "q3"}]}))
    # Fake 500-Q report: 2 failures (q2 among them, plus qX, qY).
    base_report = run_dir / "base.report.json"
    base_report.write_text(json.dumps({"failures": [
        {"question_id": "q2"}, {"question_id": "qX"}, {"question_id": "qY"}]}))
    state.data["runs"]["3"] = {"report": str(pilot_report)}
    state.data["runs"]["5"] = {"report": str(base_report)}

    subset_qids, pilot_qids, regression = rp._confirmation_qids(state)
    # pilot ∪ regression sample (q2 already in pilot; failures qX/qY added).
    assert {"q1", "q2", "q3"} <= subset_qids
    assert "qX" in subset_qids and "qY" in subset_qids
    assert len(pilot_qids) == 3
    assert set(regression) == {"q2", "qX", "qY"}  # all failures sampled
    # q2 in both pilot and regression — union dedupes
    assert "q2" in subset_qids and subset_qids == {"q1", "q2", "q3", "qX", "qY"}

    # the writer filters a dataset to the set
    instances = [{"question_id": "q1"}, {"question_id": "qX"},
                 {"question_id": "nope"}]
    subset = rp._build_confirmation_subset(state, run_dir, instances=instances)
    data = json.loads(subset.read_text(encoding="utf-8"))
    assert {q["question_id"] for q in data} == {"q1", "qX"}


def test_cmd_status_and_resume_via_cli(tmp_path, capsys):
    """`status` after passing gates shows the resume point; `gate` on a
    run step without prior gates fails loudly."""
    state = _fresh_state(tmp_path)
    rp.cmd_status(state, argparse_namespace(state=state))
    out = capsys.readouterr().out
    assert "next: step 1" in out

    with pytest.raises(SystemExit):
        rp.cmd_gate(state, argparse_namespace(
            step="3", pass_=True, fail=False, note="", owner_approve=None))


def argparse_namespace(**kw):
    class _NS:
        pass
    ns = _NS()
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


# ── 2. full-context comparison cell ─────────────────────────────────────────

def test_full_haystack_hits_cover_every_session():
    """The cell's hit list covers ALL haystack sessions (the entire haystack
    — that's the point of the ceiling measurement), each dated + indexed."""
    question = _mini()[0]
    hits = full_haystack_hits(question)
    assert len(hits) == len(question["haystack_sessions"])
    assert {h["lme_session_index"] for h in hits} == set(
        range(len(question["haystack_sessions"])))
    for h, sdate in zip(hits, question["haystack_dates"], strict=True):
        assert h["session_date"] == sdate
    assert all(h["match_source"] == "full-context" for h in hits)
    # content is the verbatim session (role-prefixed), not empty
    assert all(h["content"].strip() for h in hits)


def test_run_cell_mock_offline(tmp_path):
    """The full-context cell runs fully offline (mocked reader+judge, mini
    fixture) and reports the ceiling — with recall trivially 1.0 and the
    option-5 methodology marker."""
    outcomes, report = run_cell(
        _mini(), reader=MockReader(), judge=MockJudge(),
        checkpoint=str(tmp_path / "fc.checkpoint.json"), split="s",
    )
    assert len(outcomes) == 5
    assert report["n_questions"] == 5
    assert report["cell"] == "option-5 full-context comparison"
    m = report["methodology"]
    assert "option-5 full-context" in m["extraction_approach"]
    assert m["ingest_mode"] == "full-context-cell"
    # recall trivially 1.0 (every session in context by construction)
    assert report["retrieval"]["session_recall@k"] == {
        "5": 1.0, "10": 1.0, "20": 1.0}
    # context tokens > 0 (the reader actually saw the haystack)
    assert report["retrieval"]["context_tokens_mean"] > 0
    # the extraction approach is the cell marker, not the retrieval-backed one
    assert CELL_EXTRACTION_APPROACH in m["extraction_approach"]


def test_run_cell_resume_skips_completed(tmp_path):
    """Checkpoint/resume: a second run_cell with the same checkpoint file
    reuses completed outcomes — the reader/judge must NOT be called again on
    resumed questions (proved with call counters, not just outcome counts)."""
    cp = str(tmp_path / "fc.checkpoint.json")

    class CountingReader(MockReader):
        def __init__(self):
            self.calls = 0

        def answer(self, **kw):
            self.calls += 1
            return super().answer(**kw)

    class CountingJudge(MockJudge):
        def __init__(self):
            self.calls = 0

        def judge(self, **kw):
            self.calls += 1
            return super().judge(**kw)

    r1, j1 = CountingReader(), CountingJudge()
    _, report1 = run_cell(_mini(), reader=r1, judge=j1, checkpoint=cp, split="s")
    n1 = report1["n_questions"]
    assert r1.calls == 5 and j1.calls == 5  # first run judged everything

    # resume run: zero new reader/judge calls, same outcome count
    r2, j2 = CountingReader(), CountingJudge()
    outcomes2, _ = run_cell(_mini(), reader=r2, judge=j2, checkpoint=cp, split="s")
    assert len(outcomes2) == n1
    assert r2.calls == 0 and j2.calls == 0  # resume never re-judges
    data = json.loads(Path(cp).read_text(encoding="utf-8"))
    assert len(data["outcomes"]) == 5


# ── 3. smoke scaffolding wiring ─────────────────────────────────────────────

def test_smoke_command_uses_mini_fixture_and_v2(tmp_path, capsys):
    """The pre-pilot smoke targets 1 real-extractor question via the committed
    MINI fixture (no dataset download) with --ingest-mode v2."""
    state = _fresh_state(tmp_path)
    rp.cmd_smoke(state, argparse_namespace(mock=True, dry_run=True))
    out = capsys.readouterr().out
    assert "tests/fixtures/longmemeval_mini.json" in out
    assert "--limit 1" in out
    assert "--ingest-mode v2" in out
    assert "--mock" in out
    assert "[dry-run]" in out


def test_full_context_cli_dry_run(tmp_path, capsys):
    """`full-context --dry-run` prints the cell command without executing."""
    state = _fresh_state(tmp_path)
    rp.cmd_full_context(state, argparse_namespace(
        data=None, limit=50, split="s", mock=True, dry_run=True,
        output=None))
    out = capsys.readouterr().out
    assert "tools.longmem_eval.full_context" in out
    assert "--limit 50" in out
    assert "[dry-run]" in out
    # default output is timestamped (two cell runs — pilot + 500 — must not
    # clobber each other's ceiling measurement)
    assert "full_context_" in out and ".report.json" in out


def test_cmd_run_owner_gate_and_ordering(tmp_path, monkeypatch):
    """The `run` path enforces owner gates (8/9) and gate ordering exactly
    like `gate` does — 03-scope's 'explicit owner decision'."""
    # Epic #1647 (PR #1684 CI-fix): this test pins the ORDERING contract —
    # the final step-8 assertion expects the real-backend guard to fire
    # (SystemExit without TORTOISE_DB_URI). On the docker lane the URI is
    # present → step 8 proceeds → no exit. Pop the URI so the ordering
    # contract is lane-independent (the URI-required behavior is its own
    # test below).
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    state = _fresh_state(tmp_path)
    # owner-gated step without approval → SystemExit
    with pytest.raises(SystemExit):
        rp.cmd_run(state, argparse_namespace(
            step="8", owner_approve=None, dry_run=True, extra=[],
            expected_direction=None))
    # approval but earlier gates unmet → SystemExit
    with pytest.raises(SystemExit):
        rp.cmd_run(state, argparse_namespace(
            step="8", owner_approve="owner said yes", dry_run=True, extra=[],
            expected_direction=None))
    # gate step via `run` → SystemExit (use `gate` instead)
    with pytest.raises(SystemExit):
        rp.cmd_run(state, argparse_namespace(
            step="1", owner_approve=None, dry_run=True, extra=[],
            expected_direction=None))
    # passing gates 1..7 unlocks step 8 dry-run (no TORTOISE_DB_URI yet →
    # the real-backend guard fires, which is its own test below)
    for n in range(1, 8):
        state.pass_gate(n, f"step {n} done")
    with pytest.raises(SystemExit):
        rp.cmd_run(state, argparse_namespace(
            step="8", owner_approve="owner said yes", dry_run=True, extra=[],
            expected_direction=None))


def test_cmd_run_requires_real_backend_env(tmp_path, monkeypatch):
    """Run steps 3/5/7/8/9 fail closed without TORTOISE_DB_URI — the V3
    baseline must be a REAL-backend run (E2E-1); the base runner silently
    degrades to embedded FalkorDBLite otherwise (the exact silent-degradation
    failure M-series exists to prevent)."""
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    state = _fresh_state(tmp_path)
    for n in range(1, 8):
        state.pass_gate(n, f"step {n} done")
    with pytest.raises(SystemExit, match="TORTOISE_DB_URI"):
        rp.cmd_run(state, argparse_namespace(
            step="3", owner_approve=None, dry_run=True, extra=[],
            expected_direction=None))
    with pytest.raises(SystemExit, match="TORTOISE_DB_URI"):
        rp.cmd_run(state, argparse_namespace(
            step="5", owner_approve=None, dry_run=True, extra=[],
            expected_direction=None))
    # with the env set, dry-run prints the command (no execution)
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:falkordb@localhost:6379")
    rp.cmd_run(state, argparse_namespace(
        step="3", owner_approve=None, dry_run=True, extra=[],
        expected_direction=None))


def test_cmd_run_records_resume_quality_scan(tmp_path, monkeypatch, capsys):
    """#1764: the `run` path runs a pre-resume health check on the checkpoint
    (when one exists) — outcomes the runner's resume-quality gate will reject
    (dead FTS leg / zero session recall) are counted, surfaced loudly, and
    recorded in the run state (population-purity note); a clean checkpoint
    records a clean scan. The scan mirrors the runner's own gate signal
    (run.resume_gate_reject_reason — single source of truth)."""
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:falkordb@localhost:6379")
    state = _fresh_state(tmp_path)
    for n in range(1, 8):
        state.pass_gate(n, f"step {n} done")
    run_dir = tmp_path / "runs"
    run_dir.mkdir()
    # run steps write their checkpoint under DEFAULT_RUN_DIR (module-level)
    monkeypatch.setattr(rp, "DEFAULT_RUN_DIR", run_dir)

    # a checkpoint with one dead-FTS outcome + one healthy outcome
    cp = run_dir / "pilot.checkpoint.json"
    cp.write_text(json.dumps({
        "format": "lme-checkpoint-v2",
        "outcomes": [
            {"question_id": "q-dead", "session_recall@k": {"20": 0.0},
             "turn_recall@k": {"20": 0.0},
             "legs": [{"leg": "fts", "ran": True, "degraded": False,
                        "reason": "empty_results", "count": 0}]},
            {"question_id": "q-healthy", "session_recall@k": {"20": 1.0},
             "turn_recall@k": {"20": 1.0},
             "legs": [{"leg": "fts", "ran": True, "degraded": False,
                        "reason": "ok", "count": 7}]},
        ],
    }), encoding="utf-8")

    # dry-run surfaces the scan without executing or recording
    rp.cmd_run(state, argparse_namespace(
        step="3", owner_approve=None, dry_run=True, extra=[],
        expected_direction=None))
    err = capsys.readouterr().err
    assert "q-dead" in err and "resume-quality gate" in err
    assert "3" not in state.data["runs"]  # dry-run records nothing

    # real run records the scan in the run state (no subprocess side effects)
    monkeypatch.setattr(rp.subprocess, "run", lambda cmd, env: type(
        "R", (), {"returncode": 0})())
    rp.cmd_run(state, argparse_namespace(
        step="3", owner_approve=None, dry_run=False, extra=[],
        expected_direction=None))
    run = state.data["runs"]["3"]
    assert run["resume_quality"]["rejected"] == 1
    assert run["resume_quality"]["fts_dead"] == 1
    # per-signal PRESENCE: q-dead carries BOTH dead-FTS and all-zero
    # session recall (the pilot's artifact shape) — counts in both fields
    assert run["resume_quality"]["zero_session"] == 1
    assert run["resume_quality"]["truncated"] == 0
    assert run["resume_quality"]["qids"] == ["q-dead"]
    assert run["resume_quality"]["checked"] == 2


def test_checkpoint_resume_quality_guard_rails(tmp_path):
    """#1764: the pre-resume scan returns None (nothing to scan) for empty,
    null, or missing outcomes — never crashes and never prints a false
    'clean' claim; a truncated outcome (missing required keys) is counted
    separately — the runner re-encodes it via the truncated path, not the
    gate; a breaker_open-only checkpoint has nothing gate-eligible."""
    # empty outcomes list → None (nothing to scan)
    cp = tmp_path / "empty.json"
    cp.write_text(json.dumps({"format": "lme-checkpoint-v2",
                              "outcomes": []}), encoding="utf-8")
    assert rp.checkpoint_resume_quality(cp) is None
    # explicit null outcomes → None (no TypeError on iteration)
    cp = tmp_path / "null.json"
    cp.write_text(json.dumps({"format": "lme-checkpoint-v2",
                              "outcomes": None}), encoding="utf-8")
    assert rp.checkpoint_resume_quality(cp) is None
    # missing outcomes key → None
    cp = tmp_path / "missing.json"
    cp.write_text(json.dumps({"format": "lme-checkpoint-v2"}),
                  encoding="utf-8")
    assert rp.checkpoint_resume_quality(cp) is None
    # truncated outcome (missing hybrid required keys) → truncated count
    cp = tmp_path / "truncated.json"
    cp.write_text(json.dumps({
        "format": "lme-checkpoint-v2",
        "run_key": "embedded__hybrid__default__default",
        "outcomes": [{"question_id": "q-trunc"}],
    }), encoding="utf-8")
    scan = rp.checkpoint_resume_quality(cp)
    assert scan is not None
    assert scan["truncated"] == 1
    assert scan["rejected"] == 0 and scan["checked"] == 0
    # breaker_open-only checkpoint → no gate-eligible outcomes → None
    cp = tmp_path / "breaker.json"
    cp.write_text(json.dumps({
        "format": "lme-checkpoint-v2",
        "run_key": "embedded__hybrid__default__default",
        "outcomes": [{"question_id": "q-drop", "breaker_open": True,
                       "dropped_reason": "breaker_open",
                       "session_recall@k": {"20": 0.0},
                       "turn_recall@k": {"20": 0.0}}],
    }), encoding="utf-8")
    assert rp.checkpoint_resume_quality(cp) is None
    # vector checkpoint (run_key carries the retriever): the scan mirrors
    # the runner's vector key set (run_evaluation forwards the retriever to
    # _load_checkpoint) — an outcome missing vector-specific keys is
    # truncated, not gate-checked
    cp = tmp_path / "vector-trunc.json"
    cp.write_text(json.dumps({
        "format": "lme-checkpoint-v2",
        "run_key": "embedded__vector__minilm__default",
        "outcomes": [{"question_id": "q-vec",
                       "session_recall@k": {"5": 1.0},
                       "turn_recall@k": {"5": 1.0}}],
    }), encoding="utf-8")
    scan = rp.checkpoint_resume_quality(cp)
    assert scan is not None
    assert scan["truncated"] == 1 and scan["checked"] == 0


def test_checkpoint_resume_quality_prefers_top_level_retriever(tmp_path):
    """#1764/code-review: the scan derives the required-key retriever from
    the checkpoint's first-class top-level ``retriever`` field (written by
    run._save_checkpoint) when it names a known key set, falling back to the
    run_key segment (``{surface}__{retriever}__...``) for older files — a
    stale run_key segment must not override the authoritative field."""
    # top-level retriever="vector" wins over a run_key whose segment says
    # hybrid → hybrid-keys outcome is truncated (missing vector keys)
    cp = tmp_path / "prefer.json"
    cp.write_text(json.dumps({
        "format": "lme-checkpoint-v2",
        "run_key": "embedded__hybrid__default__default",
        "retriever": "vector",
        "outcomes": [{"question_id": "q-vec",
                       "session_recall@k": {"5": 1.0},
                       "turn_recall@k": {"5": 1.0}}],
    }), encoding="utf-8")
    scan = rp.checkpoint_resume_quality(cp)
    assert scan is not None
    assert scan["truncated"] == 1 and scan["checked"] == 0

    # unknown top-level retriever value → falls back to the run_key segment
    cp = tmp_path / "fallback.json"
    cp.write_text(json.dumps({
        "format": "lme-checkpoint-v2",
        "run_key": "embedded__vector__minilm__default",
        "retriever": "bogus-retriever",
        "outcomes": [{"question_id": "q-vec",
                       "session_recall@k": {"5": 1.0},
                       "turn_recall@k": {"5": 1.0}}],
    }), encoding="utf-8")
    scan = rp.checkpoint_resume_quality(cp)
    assert scan is not None
    assert scan["truncated"] == 1 and scan["checked"] == 0


def test_checkpoint_resume_quality_int_question_ids(tmp_path, capsys):
    """#1764/code-review: int question_ids (foreign/dataset shapes) must not
    TypeError in the scan's sorted() or the print path's join() — qids are
    string-coerced end to end."""
    cp = tmp_path / "ints.json"
    cp.write_text(json.dumps({
        "format": "lme-checkpoint-v2",
        "run_key": "embedded__hybrid__default__default",
        "outcomes": [{"question_id": 123,
                       "session_recall@k": {"20": 0.0},
                       "turn_recall@k": {"20": 0.0},
                       "legs": [{"leg": "fts", "ran": True,
                                  "degraded": False,
                                  "reason": "empty_results", "count": 0}]}],
    }), encoding="utf-8")
    scan = rp.checkpoint_resume_quality(cp)
    assert scan is not None
    assert scan["rejected"] == 1
    assert scan["qids"] == ["123"]  # coerced, sorted
    rp._print_resume_quality(scan)  # the join must not raise
    assert "123" in capsys.readouterr().err


def test_checkpoint_resume_quality_corrupt_warns_loudly(tmp_path, capsys):
    """#1764/code-review: an existing-but-corrupt checkpoint must NOT degrade
    silently (the runner itself warns loudly on the same failure) — the scan
    prints a loud stderr warning and returns None."""
    cp = tmp_path / "corrupt.json"
    cp.write_text("{not json!!", encoding="utf-8")
    assert rp.checkpoint_resume_quality(cp) is None
    err = capsys.readouterr().err
    assert "corrupt" in err.lower() and str(cp) in err


def test_print_resume_quality_clean_verdict_softened(tmp_path, capsys):
    """#1764/code-review: the clean-path verdict must not bless a file the
    runner would refuse wholesale — the scan only re-checks per-outcome gate
    decisions, never the whole-file load gates (format / run_key /
    fingerprint mismatch), and the verdict says so."""
    rp._print_resume_quality({
        "checked": 3, "rejected": 0, "fts_dead": 0, "zero_session": 0,
        "truncated": 0, "qids": [],
    })
    err = capsys.readouterr().err
    assert "scan-clean" in err
    assert "not re-checked" in err.lower() or "NOT re-checked" in err
    assert "checkpoint clean" not in err


def test_last_flag_value_helper():
    """#1764/code-review: _last_flag_value resolves the LAST occurrence
    (argparse last-wins), and returns None for absent flags or a flag with
    no value — never an unhandled ValueError/IndexError."""
    cmd = ["python", "-m", "tools.longmem_eval.run",
           "--checkpoint", "a", "--output", "b", "--checkpoint", "c"]
    assert rp._last_flag_value(cmd, "--checkpoint") == "c"
    assert rp._last_flag_value(cmd, "--output") == "b"
    assert rp._last_flag_value(["python", "-m", "x"], "--checkpoint") is None
    # flag as the final token (no value) → None, not IndexError
    assert rp._last_flag_value(["python", "--checkpoint"],
                               "--checkpoint") is None



def test_cmd_run_scan_uses_last_checkpoint_flag(tmp_path, monkeypatch):
    """#1764: the pre-resume scan resolves the LAST --checkpoint occurrence —
    build_command prepends the operator's extra flags and appends the
    protocol's own --checkpoint after them, and the runner's argparse is
    last-wins — so the scan and the recorded state describe the file the
    runner actually uses."""
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:falkordb@localhost:6379")
    state = _fresh_state(tmp_path)
    for n in range(1, 8):
        state.pass_gate(n, f"step {n} done")
    run_dir = tmp_path / "runs"
    run_dir.mkdir()
    monkeypatch.setattr(rp, "DEFAULT_RUN_DIR", run_dir)
    # the operator's extra --checkpoint points at a CLEAN file...
    extra_cp = tmp_path / "extra.json"
    extra_cp.write_text(json.dumps({
        "format": "lme-checkpoint-v2",
        "run_key": "embedded__hybrid__default__default",
        "outcomes": [{"question_id": "q-clean",
                       "session_recall@k": {"20": 1.0},
                       "turn_recall@k": {"20": 1.0},
                       "legs": [{"leg": "fts", "ran": True,
                                  "degraded": False, "reason": "ok",
                                  "count": 7}]}],
    }), encoding="utf-8")
    # ...while the protocol-default checkpoint (what the runner will use,
    # argparse last-wins) is dirty
    cp = run_dir / "pilot.checkpoint.json"
    cp.write_text(json.dumps({
        "format": "lme-checkpoint-v2",
        "run_key": "embedded__hybrid__default__default",
        "outcomes": [{"question_id": "q-dead",
                       "session_recall@k": {"20": 0.0},
                       "turn_recall@k": {"20": 0.0},
                       "legs": [{"leg": "fts", "ran": True,
                                  "degraded": False,
                                  "reason": "empty_results",
                                  "count": 0}]}],
    }), encoding="utf-8")
    monkeypatch.setattr(rp.subprocess, "run", lambda cmd, env: type(
        "R", (), {"returncode": 0})())
    rp.cmd_run(state, argparse_namespace(
        step="3", owner_approve=None, dry_run=False,
        extra=["--checkpoint", str(extra_cp)], expected_direction=None))
    run = state.data["runs"]["3"]
    assert run["checkpoint"] == str(cp)  # protocol-default, not the extra
    assert run["resume_quality"]["qids"] == ["q-dead"]
    assert run["resume_quality"]["rejected"] == 1


def test_cmd_run_step7_requires_expected_direction(tmp_path, monkeypatch, capsys):
    """Step 7 via `run` needs the pre-stated expected-delta direction AND
    the recorded step-3/5 reports before building the confirmation set."""
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:falkordb@localhost:6379")
    state = _fresh_state(tmp_path)
    for n in range(1, 7):
        state.pass_gate(n, f"step {n} done")
    # direction missing → SystemExit
    with pytest.raises(SystemExit):
        rp.cmd_run(state, argparse_namespace(
            step="7", owner_approve=None, dry_run=True, extra=[],
            expected_direction=None))
    # direction present but no step-3/5 reports → SystemExit
    with pytest.raises(SystemExit):
        rp.cmd_run(state, argparse_namespace(
            step="7", owner_approve=None, dry_run=True, extra=[],
            expected_direction="up on KU/TR"))
    # direction + fake reports → command builds (dry-run, no subset write)
    run_dir = tmp_path / "runs"
    run_dir.mkdir()
    state.data["runs"]["3"] = {"report": str(run_dir / "pilot.json")}
    state.data["runs"]["5"] = {"report": str(run_dir / "base.json")}
    for p, qids in ((run_dir / "pilot.json", ["q1"]),
                    (run_dir / "base.json", None)):
        if qids is not None:
            p.write_text(json.dumps({"outcomes": [{"question_id": x}
                                                   for x in qids]}))
        else:
            p.write_text(json.dumps({"failures": []}))
    rp.cmd_run(state, argparse_namespace(
        step="7", owner_approve=None, dry_run=True, extra=[],
        expected_direction="up on KU/TR"))
    out = capsys.readouterr().out
    assert "--data" in out and "confirm_subset.json" in out
    assert "[dry-run]" in out
