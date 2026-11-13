"""Unit tests for tools/ci_check_repair.py — the #8254 "no required check at this
head" repair (#8375 item 1).

The classification is the whole risk surface, and it is pure, so it is tested
without touching GitHub. The three things these tests exist to keep true:

  1. **A check that is merely LATE is not absent.** `python-ci-gate` only starts
     after its `needs:` shards, so it has NO check run while they run. Misreading
     that as absent would re-trigger CI on every in-flight PR — the exact churn
     the repair exists to remove.
  2. **A completed run that produced no check IS dead**, and must be re-run at
     the same head sha (never merged, never re-committed).
  3. **The tool has no merge capability at all.** "These jobs repair and report;
     they never merge" (#8375 Research Needed 5) is asserted against the source.
"""
from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import ci_check_repair as R


@pytest.fixture(autouse=True)
def _pin_the_launcher_to_this_checkout(monkeypatch):
    """A FLEET shell exports `TORTOISE_REPO`; this module's launcher tests must not.

    `tools/ci-check-repair-local.sh` honours `TORTOISE_REPO` as its documented repo
    override (`:57`, `:84`), so in a lane whose shell exports it the launcher resolved
    `TOOL` to a DIFFERENT checkout's `tools/ci_check_repair.py` and judged THIS
    checkout's launcher by another tree. Measured (2026-11-04, fleet lane):
    `test_the_launcher_acts_on_a_failed_window_and_stays_idle_on_a_green_one` failed
    with `TOOL=/home/cmux/tortoise/tools/ci_check_repair.py`, and the same file passed
    under `env -u TORTOISE_REPO`.

    A false red is not free: it trains a lane to skip the launcher test, which is the
    one place the "covered → idle" / "ran-not-succeeded → act" handoff is executed
    rather than grepped. Pinned out here so the test measures the launcher it names.
    """
    monkeypatch.delenv("TORTOISE_REPO", raising=False)

REQUIRED = ["pricing-artifact", "docs", "test-isolation", "license-surface",
            "legal-e2e", "python-ci-gate", "ai-review-gate"]
EMITTERS = {
    "pricing-artifact": ["ci.yml"],
    "docs": ["ci.yml"],
    "test-isolation": ["ci.yml"],
    "license-surface": ["ci.yml"],
    "legal-e2e": ["ci.yml"],
    "python-ci-gate": ["python-ci.yml"],
    "ai-review-gate": ["ai-review-gate.yml"],
}


def run(rid, path, status, conclusion=None, attempt=1, name=""):
    return R.Run(id=rid, name=name or Path(path).stem, path=f".github/workflows/{path}",
                 status=status, conclusion=conclusion, run_attempt=attempt, event="pull_request")


def pr(number=1, head="a" * 40):
    return {"number": number, "headRefOid": head, "isDraft": False}


def check(name, status="completed", conclusion="success"):
    return {"name": name, "status": status, "conclusion": conclusion, "id": 1}


# ── 1. LATE is not ABSENT ──────────────────────────────────────────────────

def test_in_flight_producing_run_is_pending_not_absent():
    """`python-ci-gate` absent while the Python CI shards are still running."""
    checks = [check(c) for c in REQUIRED if c != "python-ci-gate"]
    runs = [run(10, "python-ci.yml", "in_progress"), run(11, "ci.yml", "completed", "success")]
    plan = R.classify(pr(), checks, runs, REQUIRED, EMITTERS, max_attempts=3)
    assert plan.absent == ["python-ci-gate"]
    assert plan.pending == ["python-ci-gate"]
    assert plan.rerun == [] and plan.missing_run == []
    assert not plan.actionable


def test_queued_run_is_pending_too():
    checks = [check(c) for c in REQUIRED if c != "docs"]
    runs = [run(10, "ci.yml", "queued")]
    plan = R.classify(pr(), checks, runs, REQUIRED, EMITTERS, max_attempts=3)
    assert plan.pending == ["docs"] and not plan.actionable


# ── STARVED: a QUEUED run is not "pending" when the repo is in a FLOOD #9115 ──

def test_a_queued_run_is_starved_only_under_a_measured_repo_flood():
    """The same per-head shape is ordinary on a busy runner — the flood decides."""
    checks = [check(c) for c in REQUIRED if c != "docs"]
    runs = [run(10, "ci.yml", "queued")]

    quiet = R.classify(pr(), checks, runs, REQUIRED, EMITTERS, max_attempts=3,
                       queue_starved=False)
    assert quiet.pending == ["docs"] and quiet.starved == []

    flooded = R.classify(pr(), checks, runs, REQUIRED, EMITTERS, max_attempts=3,
                         queue_starved=True)
    # still PENDING (it drives the reopen deferral) AND named as STARVED
    assert flooded.pending == ["docs"]
    assert flooded.starved == ["docs"]
    # a starved context is never repaired: no rerun, no reopen, not "actionable"
    assert flooded.rerun == [] and flooded.missing_run == []
    assert not flooded.actionable
    # ...but it must still be REPORTED, or a starved head goes silent
    assert flooded.needs_report


def test_a_run_that_has_actually_started_is_not_starved_under_a_flood():
    """`queued` beside `in_progress` means a slot WAS granted."""
    checks = [check(c) for c in REQUIRED if c != "python-ci-gate"]
    runs = [run(10, "python-ci.yml", "queued"), run(11, "python-ci.yml", "in_progress")]
    plan = R.classify(pr(), checks, runs, REQUIRED, EMITTERS, max_attempts=3,
                      queue_starved=True)
    assert plan.pending == ["python-ci-gate"] and plan.starved == []


def test_a_starved_context_is_recorded_and_repaired_by_nothing(monkeypatch):
    """No per-PR action can create a runner slot, so none may be attempted."""
    checks = [check(c) for c in REQUIRED if c != "docs"]
    plan = R.classify(pr(), checks, [run(10, "ci.yml", "queued")], REQUIRED, EMITTERS,
                      max_attempts=3, queue_starved=True)
    monkeypatch.setattr(R, "_rerun", lambda *a, **k: pytest.fail("a starved head must not be re-run"))
    monkeypatch.setattr(R, "_reopen", lambda *a, **k: pytest.fail("a starved head must not be reopened"))
    code, recs = R.report([plan], apply=True, trigger_ok=True, repo="o/r", cooldown_min=60)
    assert code == 0
    assert recs[0]["starved"] == ["docs"]
    assert recs[0]["rerun"] == [] and recs[0]["reopened"] is False


def test_a_starved_head_is_not_deferred_as_though_CI_were_about_to_report():
    """It must be reported as a QUEUE problem, not folded into in-flight deferral."""
    checks = [check(c) for c in REQUIRED if c != "docs"]
    plan = R.classify(pr(), checks, [run(10, "ci.yml", "queued")], REQUIRED, EMITTERS,
                      max_attempts=3, queue_starved=True)
    assert plan.in_flight == ["ci"]        # it IS non-completed...
    assert plan.starved == ["docs"]        # ...and the report says WHY


def test_the_queue_reading_degrades_to_no_flood_when_it_cannot_be_read(monkeypatch):
    """A failed queue read must not invent a flood, and must not fail the pass."""
    import tools.ci_queue_health as Q

    def boom(*a, **k):
        raise Q.MeasureError("gh api 500")

    monkeypatch.setattr(Q, "read_health", boom)
    assert R.read_queue_health("o/r") is None


def test_the_queue_reading_is_the_repo_level_state(monkeypatch):
    import tools.ci_queue_health as Q

    health = Q.classify_queue([], [], None)
    monkeypatch.setattr(Q, "read_health", lambda repo: health)
    assert R.read_queue_health("o/r") is health


def test_all_required_present_means_no_plan_at_all():
    checks = [check(c) for c in REQUIRED]
    plan = R.classify(pr(), checks, [run(1, "ci.yml", "completed", "success")],
                      REQUIRED, EMITTERS, max_attempts=3)
    assert plan.absent == [] and not plan.actionable


# ── 2. completed run, check never landed -> DEAD ───────────────────────────

def test_completed_run_without_check_run_is_dead_and_gets_rerun():
    """The #8254 signature: the run exists, completed, and produced no check."""
    checks = [check("Mergify Merge Queue", conclusion="neutral")]
    runs = [run(42, "ci.yml", "completed", "failure"),
            run(43, "python-ci.yml", "completed", "cancelled"),
            run(44, "ai-review-gate.yml", "completed", "failure")]
    plan = R.classify(pr(), checks, runs, REQUIRED, EMITTERS, max_attempts=3)
    assert set(plan.absent) == set(REQUIRED)
    assert plan.pending == [] and plan.missing_run == []
    assert sorted(r.id for r in plan.rerun) == [42, 43, 44]
    assert plan.actionable


def test_one_workflow_covers_five_contexts_with_a_single_rerun():
    """ci.yml emits five required contexts; it must not be re-run five times."""
    runs = [run(42, "ci.yml", "completed", "failure")]
    plan = R.classify(pr(), [], runs, ["docs", "legal-e2e", "pricing-artifact"],
                      EMITTERS, max_attempts=3)
    assert [r.id for r in plan.rerun] == [42]


def test_exhausted_run_escalates_instead_of_rerunning_forever():
    runs = [run(42, "ci.yml", "completed", "failure", attempt=3)]
    plan = R.classify(pr(), [], runs, ["docs"], EMITTERS, max_attempts=3)
    assert plan.exhausted == ["docs"] and plan.rerun == []
    assert not plan.actionable


# ── 3. no run at all -> needs a NEW PR event ───────────────────────────────

def test_no_producing_run_is_missing_run():
    """Nothing at the head for ai-review-gate: only a new PR event can make it."""
    runs = [run(42, "ci.yml", "completed", "success")]
    plan = R.classify(pr(), [], runs, ["docs", "ai-review-gate"], EMITTERS, max_attempts=3)
    assert plan.missing_run == ["ai-review-gate"]
    assert [r.id for r in plan.rerun] == [42]


# ── report(): who is allowed to act ────────────────────────────────────────

def _plan(**kw):
    return R.PrPlan(number=kw.pop("number", 7), head=kw.pop("head", "b" * 40), **kw)


def test_missing_run_under_automatic_token_escalates_to_the_local_fallback(monkeypatch):
    """GITHUB_TOKEN cannot create a workflow run, so reopen must NOT be attempted."""
    opened = []
    monkeypatch.setattr(R, "_reopen", lambda *a: opened.append(a) or True)
    monkeypatch.setattr(R, "_rerun", lambda *a, **k: "rerun")
    plan = _plan(absent=["ai-review-gate"], missing_run=["ai-review-gate"])
    code, recs = R.report([plan], apply=True, trigger_ok=False, repo="o/r", cooldown_min=60)
    assert code == 1
    assert recs[0]["needs_user_actor"] is True
    assert recs[0]["rerun"] == []          # a reopen would supersede it anyway
    assert opened == []


def test_missing_run_with_a_user_credential_reopens_and_does_not_also_rerun(monkeypatch):
    """The reopen subsumes every rerun — doing both is pure churn."""
    opened, rerun = [], []
    monkeypatch.setattr(R, "_reopen", lambda *a, **k: opened.append(a) or "reopened")
    monkeypatch.setattr(R, "_rerun", lambda *a, **k: rerun.append(a[1]) or "rerun")
    monkeypatch.setattr(R, "_timeline_reopen_state", lambda *a, **k: (False, 0))
    plan = _plan(absent=["docs", "ai-review-gate"], missing_run=["ai-review-gate"],
                 rerun=[run(42, "ci.yml", "completed", "failure")])
    code, recs = R.report([plan], apply=True, trigger_ok=True, repo="o/r", cooldown_min=60)
    assert code == 0 and recs[0]["reopened"] is True
    assert opened and rerun == []


def test_rerun_only_path_reports_success(monkeypatch):
    rerun = []
    monkeypatch.setattr(R, "_rerun", lambda *a, **k: rerun.append(a[1].id) or "rerun")
    plan = _plan(absent=["docs"], rerun=[run(42, "ci.yml", "completed", "failure")])
    code, recs = R.report([plan], apply=True, trigger_ok=True, repo="o/r", cooldown_min=60)
    assert code == 0 and rerun == [42]
    assert recs[0]["rerun"][0]["ok"] is True


def test_failed_rerun_escalates(monkeypatch):
    monkeypatch.setattr(R, "_rerun", lambda *a, **k: "failed")
    plan = _plan(absent=["docs"], rerun=[run(42, "ci.yml", "completed", "failure")])
    code, _ = R.report([plan], apply=True, trigger_ok=True, repo="o/r", cooldown_min=60)
    assert code == 1


def test_rerun_skips_when_the_head_cannot_be_re_read(monkeypatch):
    """An unverifiable head is a skip, never a guessed rerun (mirrors `_reopen`)."""
    def boom(*a, **k):
        raise R.MeasureError("unreadable")

    monkeypatch.setattr(R, "gh_json", boom)
    called = []
    monkeypatch.setattr(R, "_run", lambda *a, **k: called.append(a))
    assert R._rerun("o/r", run(42, "ci.yml", "completed", "failure"), 1,
                    expected_head="a" * 40) == "failed"
    assert called == [], "nothing may be re-run when the head is unknown"


def test_rerun_reports_head_moved_without_acting(monkeypatch):
    """TOCTOU: a pass takes minutes, so a rerun on a replaced head burns CI."""
    monkeypatch.setattr(R, "gh_json", lambda *a, **k: {"headRefOid": "c" * 40})
    called = []
    monkeypatch.setattr(R, "_run", lambda *a, **k: called.append(a))
    assert R._rerun("o/r", run(42, "ci.yml", "completed", "failure"), 1,
                    expected_head="a" * 40) == "head_moved"
    assert called == [], "a superseded head must not be re-run"


def test_rerun_acts_when_the_head_still_matches(monkeypatch):
    """The positive pole: an unchanged head re-runs at the same sha."""
    monkeypatch.setattr(R, "gh_json", lambda *a, **k: {"headRefOid": "a" * 40})
    calls = []

    class _Done:
        returncode = 0

    monkeypatch.setattr(R, "_run", lambda *a, **k: calls.append(a) or _Done())
    assert R._rerun("o/r", run(42, "ci.yml", "completed", "failure"), 1,
                    expected_head="a" * 40) == "rerun"
    assert len(calls) == 1 and calls[0][0][-1].endswith("/rerun")


def test_a_rerun_that_moved_the_head_is_a_skip_not_an_escalation(monkeypatch):
    """The report half of the rerun TOCTOU guard: a replaced head is a skip — the
    new head's own CI is the repair — so it must not produce a false exit 1."""
    seen = {}

    def fake(repo, run_, number, expected_head=""):
        seen["run"], seen["head"] = run_.id, expected_head
        return "head_moved"

    monkeypatch.setattr(R, "_rerun", fake)
    plan = _plan(absent=["docs"], rerun=[run(42, "ci.yml", "completed", "failure")])
    code, recs = R.report([plan], apply=True, trigger_ok=True, repo="o/r", cooldown_min=60)
    assert seen == {"run": 42, "head": "b" * 40}, "the expected head must be passed through"
    assert recs[0]["rerun"][0]["head_moved"] is True
    assert recs[0]["rerun"][0]["ok"] is False
    assert code == 0, "a moved head is a skip, not a failure"


def test_reopen_cooldown_blocks_a_duplicate_and_is_not_an_escalation(monkeypatch):
    monkeypatch.setattr(R, "_timeline_reopen_state", lambda *a, **k: (True, 1))
    monkeypatch.setattr(R, "_reopen", lambda *a: pytest.fail("must not reopen inside cooldown"))
    plan = _plan(absent=["ai-review-gate"], missing_run=["ai-review-gate"])
    code, recs = R.report([plan], apply=True, trigger_ok=True, repo="o/r", cooldown_min=60)
    assert code == 0 and recs[0]["cooldown"] is True


def test_reopen_budget_is_bounded_and_escalates_instead_of_looping(monkeypatch):
    """A context no workflow can emit must NOT be reopened forever.

    Unbounded automated remediation is the documented anti-pattern (retry budgets
    with escalation once the budget is spent). Here the PR's timeline says the
    reopen budget is already spent, so the tool must REPORT, never act.
    """
    monkeypatch.setattr(R, "_timeline_reopen_state", lambda *a, **k: (False, 3))
    monkeypatch.setattr(R, "_reopen", lambda *a: pytest.fail("reopen budget is spent"))
    plan = _plan(absent=["ai-review-gate"], missing_run=["ai-review-gate"])
    code, recs = R.report([plan], apply=True, trigger_ok=True, repo="o/r",
                          cooldown_min=60, max_attempts=3)
    assert code == 1
    assert recs[0]["reopened"] is False
    assert recs[0]["reopen_attempts"] == 3
    assert recs[0]["exhausted"] == ["ai-review-gate"]


def test_reopen_budget_below_max_still_reopens(monkeypatch):
    monkeypatch.setattr(R, "_timeline_reopen_state", lambda *a, **k: (False, 2))
    monkeypatch.setattr(R, "_reopen", lambda *a, **k: "reopened")
    plan = _plan(absent=["ai-review-gate"], missing_run=["ai-review-gate"])
    code, recs = R.report([plan], apply=True, trigger_ok=True, repo="o/r",
                          cooldown_min=60, max_attempts=3)
    assert code == 0 and recs[0]["reopened"] is True


def test_reopen_that_leaves_the_pr_closed_is_named_and_escalated(monkeypatch):
    """`close` succeeded and `reopen` did not: worse than the absent check."""
    monkeypatch.setattr(R, "_timeline_reopen_state", lambda *a, **k: (False, 0))
    monkeypatch.setattr(R, "_reopen", lambda *a, **k: "left_closed")
    plan = _plan(absent=["ai-review-gate"], missing_run=["ai-review-gate"])
    code, recs = R.report([plan], apply=True, trigger_ok=True, repo="o/r", cooldown_min=60)
    assert code == 1
    assert recs[0]["left_closed"] is True
    assert recs[0]["reopened"] is False


def test_a_close_that_never_happened_is_a_plain_failure(monkeypatch):
    monkeypatch.setattr(R, "_timeline_reopen_state", lambda *a, **k: (False, 0))
    monkeypatch.setattr(R, "_reopen", lambda *a, **k: "failed")
    plan = _plan(absent=["ai-review-gate"], missing_run=["ai-review-gate"])
    code, recs = R.report([plan], apply=True, trigger_ok=True, repo="o/r", cooldown_min=60)
    assert code == 1
    assert recs[0]["reopened"] is False
    assert "left_closed" not in recs[0]      # nothing was left closed


def test_exit_zero_never_hides_an_unhandled_repair(monkeypatch):
    """The coverage read treats exit 0 as "the window is discharged".

    That is only sound if exit 0 cannot coexist with an unhandled repair.
    """
    monkeypatch.setattr(R, "_timeline_reopen_state", lambda *a, **k: (False, 0))
    monkeypatch.setattr(R, "_reopen", lambda *a, **k: "reopened")
    monkeypatch.setattr(R, "_rerun", lambda *a, **k: "rerun")
    dirty = [_plan(number=1, absent=["docs"],
                   rerun=[run(1, "ci.yml", "completed", "failure")]),
             _plan(number=2, absent=["ai-review-gate"], missing_run=["ai-review-gate"]),
             _plan(number=3, absent=["docs"], exhausted=["docs"])]
    code, _recs = R.report(dirty, apply=True, trigger_ok=True, repo="o/r", cooldown_min=60)
    assert code == 1

    code, clean = R.report(
        [_plan(number=4, absent=["docs"],
               rerun=[run(2, "ci.yml", "completed", "failure")])],
        apply=True, trigger_ok=True, repo="o/r", cooldown_min=60)
    assert code == 0
    assert clean[0]["rerun"][0]["ok"] is True
    assert not any(r.get("needs_user_actor") or r.get("left_closed") or r.get("exhausted")
                   for r in clean)


# The keys that green a run WITHOUT the step succeeding. `continue-on-error`
# is the obvious one; `if:` is the same green one line earlier — a SKIPPED job or
# step concludes `success`, and #8254's whole failure mode is a required check
# that nobody produced. `needs:` is the third and the least obvious: a job whose
# dependency was skipped is itself skipped, so `repair: needs: [<a job with
# `if: false`>]` never runs the tool while the run concludes `success` — the same
# fail-open, with neither of the first two keys present (#9365 re-review).
# The repo already bans all three together in
# `tests/test_ai_review_gate_contract.py` (`for banned in ("if", "needs",
# "continue-on-error")`, "a {banned!r}-gated job reports Success without
# evaluating any evidence"), so this is the established shape rather than a new
# rule.
_GREENING_KEYS = ("if", "needs", "continue-on-error")


def _repair_step(job):
    """The step that runs `tools/ci_check_repair.py`, found by CONTENT.

    Keyed on the command, not on the display `name:`: renaming a step for clarity
    is a legitimate edit, and a name-keyed lookup reddens it (#9365 review).
    """
    steps = [s for s in (job.get("steps") or [])
             if "tools/ci_check_repair.py" in (s.get("run") or "")]
    assert len(steps) == 1, (
        "expected exactly one step running tools/ci_check_repair.py, found "
        f"{len(steps)}: {[s.get('name') for s in (job.get('steps') or [])]}")
    return steps[0]


def _effective_shell(job, step, workflow):
    """The shell Actions will ACTUALLY run this step with (#9365 review).

    GitHub's precedence is step `shell:` > job `defaults.run.shell` > workflow
    `defaults.run.shell` > the platform default (`bash -e {0}` on Linux; `-e` is
    implied, which is why the step's own `set +e`/`set -e` matter). The test used
    to hardcode `bash -c` and never read this at all, so `shell: 'echo {0}'` on
    the step — which echoes the script path and exits 0 — left BOTH new tests
    green while the tool never ran: the run concludes `success`,
    `workflow_state()` reads `covered`, the local fallback idles, and nobody
    produces the required checks.
    """
    return (step.get("shell")
            or (job.get("defaults") or {}).get("run", {}).get("shell")
            or (workflow.get("defaults") or {}).get("run", {}).get("shell"))


# The runner variables the step is allowed to be influenced by. PINNED, not
# inherited (#9365 review): the test used to run the step with `**os.environ`, so
# a branch keyed on a runner-provided variable was invisible — prepending
# `if [ "$GITHUB_EVENT_NAME" = schedule ]; then exit 0; fi` kept both tests green
# in a developer shell (where the variable is unset) while the real 30-minute
# `schedule` run exited 0 and never ran the tool. The values below are the ones
# this workflow actually gets.
_RUNNER_ENV = {
    "GITHUB_ACTIONS": "true",
    "GITHUB_EVENT_NAME": "schedule",
    "GITHUB_RUN_ATTEMPT": "1",
    "GITHUB_REF": "refs/heads/main",
    "GITHUB_REPOSITORY": "daniel-ospina/tortoise",
    "CI": "true",
}


def _run_repair_step(script, tmp_path, tool_rc):
    """Execute the step's OWN shell with a stubbed `python3` that exits `tool_rc`.

    BEHAVIOURAL, not textual (#9365 review). A regex over the script can only
    enumerate the spellings somebody thought of: `declare -i rc=0`,
    `[ "$rc" -ne 0 ] && rc=0` and `printf -v rc 0` all force the exit code while
    leaving `rc=${PIPESTATUS[0]}` AND `exit "$rc"` intact, so a text pin reads
    green on a real fail-open. Running the script measures the only thing that
    matters: what exit code the step actually hands GitHub.

    The environment is a PINNED runner env, not the caller's (#9365 review) — see
    `_RUNNER_ENV`. `PATH` still carries the system path so the script's own
    `tee`/`cat` resolve; the stub `python3` wins by coming first.

    `tmp_path` must be FRESH per call — the journal below is the run's argv.

    ARGV IS RECORDED, not just the exit code (#9365 review). An exit-code-only
    stub accepts ANY invocation, so dropping `--apply` from the step read green:
    without it `report()` stops escalating the `needs_user_actor` case
    (`tools/ci_check_repair.py`: `if apply: escalations += 1`), the tool exits 0
    where it exits 1 when armed, and `workflow_state()` therefore reads `covered`
    — the same fail-open, reached by a deletion rather than by greening.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    argv_log = tmp_path / "python3-argv.log"
    stub = bindir / "python3"
    # `--workflow-state` is the LAUNCHER's flag and must NOT be answered here:
    # this stub exists to stand in for the repair tool the step invokes.
    stub.write_text("#!/usr/bin/env bash\n"
                    f'printf "%s\\n" "$*" >> {str(argv_log)!r}\n'
                    f"exit {tool_rc}\n")
    stub.chmod(0o755)
    summary = tmp_path / "step-summary.md"
    summary.write_text("")
    env = dict(_RUNNER_ENV)
    env.update({
        "PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}",
        "HOME": str(tmp_path),
        # The step appends to `$GITHUB_STEP_SUMMARY`. Unset, that redirect is
        # an "ambiguous redirect" and the run fails for the wrong reason.
        "GITHUB_STEP_SUMMARY": str(summary),
    })
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                       timeout=120, env=env, cwd=tmp_path)
    argv = argv_log.read_text() if argv_log.exists() else ""
    return r, argv, summary.read_text()


def test_the_red_is_the_coverage_signal_so_it_cannot_be_greened_alone(tmp_path):
    """#9365 — greening this run without moving the coverage signal is a fail-open.

    The red is not decoration. `tools/ci-check-repair-local.sh` decides whether the
    local fallback runs AT ALL from the newest run's CONCLUSION (`--workflow-state`
    -> `workflow_state()`), and a failed window IS the GitHub->local handoff. So
    #9365's "stop reddening main" arms (A: informational only, B: split the
    verdicts) are only safe together with a moved coverage signal — otherwise a
    `needs_user_actor` PR reads as "the window was discharged" down there and its
    required checks are never produced by anyone. That is the #8254 fail-open,
    restored silently, and it is strictly worse than a red somebody learns to
    ignore.

    This test pins the workflow half in BOTH directions, so a future lane cannot
    implement half of #9365 and read green:

      * STRUCTURE — no key may green the run without the step succeeding
        (`if:` at JOB or step level, `continue-on-error` at either), because a
        skipped or tolerated job/step concludes `success`;
      * BEHAVIOUR — the step must hand GitHub the TOOL's exit code, for EVERY
        non-zero class the tool can produce (1 = needs a local fallback, 2 =
        could not measure) and for an unknown future one. Asserted by RUNNING the
        step script against a stubbed interpreter, not by matching its text: a
        text pin cannot see `rc=0`, and a probe of exit 1 alone cannot see
        `[ "$rc" = 2 ] && rc=0`, which greens the exit-2 class the record counts
        as 5 of its 73 reds.

    The launcher half is pinned by
    `test_the_launcher_acts_on_a_failed_window_and_stays_idle_on_a_green_one`
    immediately below: `workflow_state`'s polarity alone does not prove the
    fallback still READS it.

    If a change deliberately moves the coverage signal off the run conclusion,
    update BOTH tests in the same commit — that is the point: they cannot drift
    apart.

    Reading the workflow through parsed YAML (rather than a substring window) is
    what makes the STRUCTURE half possible at all: a window anchored on the step's
    `name:` cannot see a job-level key, which sits above that marker, and a
    content-keyed step lookup survives a rename.

    The key ban is applied to the ONE job this workflow has, and the job SET is
    pinned too (#9365 re-review): banning keys on `repair` alone left
    `repair: needs: [<a job with `if: false`>]` green — the dependency is skipped,
    so `repair` is skipped, so the run concludes `success` and the fallback idles
    while neither banned key appears on `repair` itself.
    """
    import yaml

    root = Path(__file__).resolve().parents[1]
    wf = yaml.safe_load((root / ".github/workflows/ci-check-repair.yml").read_text())
    assert set(wf["jobs"]) == {"repair"}, (
        "this workflow must carry exactly one job. A second job is not forbidden "
        "for its own sake — it is forbidden because `repair: needs: [<it>]` skips "
        "the repair whenever the other job is skipped, which greens the run "
        "without the tool running (#9365). If a job genuinely has to be added, "
        "extend this assertion deliberately and pin that it cannot skip. "
        f"jobs={sorted(wf['jobs'])}")
    job = wf["jobs"]["repair"]

    for banned in _GREENING_KEYS:
        assert banned not in job, (
            # The hazard is a SKIPPABLE dependency: a job that carries `if:`,
            # `continue-on-error` or a matrix that can end up empty makes `repair`
            # inherit the skip. A dependency that can never skip does not green
            # anything, which is why this is refused only where it can.
            f"`{banned}` on the repair job can green the RUN without the repair "
            "succeeding — as a skip key it drops the job (a skipped job is not a "
            "failure), as a `needs:` target it inherits that job's skip — so "
            "`workflow_state()` reads the window as `covered` and the local "
            "fallback stays idle for every `needs_user_actor` PR (#9365)")

    step = _repair_step(job)
    for banned in _GREENING_KEYS:
        assert banned not in step, (
            f"`{banned}` on the repair step greens the run without the repair "
            f"succeeding — the #8254 fail-open, silently (#9365)")

    # The step must run under the shell the RUNNER will use (#9365 review): with
    # `shell: 'echo {0}'` on this step — or the same at either `defaults.run`
    # level — GitHub echoes the script path and exits 0, so the tool never runs
    # while the run concludes `success`. Nothing else in this file read it.
    shell = _effective_shell(job, step, wf)
    assert shell in (None, "bash", "bash {0}"), (
        "the repair step must run under bash (Actions' Linux default is `bash -e "
        "{0}`). A custom `shell:` that does not execute the script greys the step "
        f"without the tool running (#9365); got shell={shell!r}")

    # Behaviour: the exit code the step hands GitHub, under the RUNNER's env.
    #
    # EVERY non-zero class, not just exit 1. `tools/ci_check_repair.py` exits 2
    # from `_could_not_measure` (a failed measurement) AND from the <3.12
    # interpreter guard at the top of the module, which raises `SystemExit(2)`
    # before the rest of the tool is imported — so it is not the only exit-2 path,
    # and `workflow_state()` maps the class to `ran-not-succeeded` just like exit
    # 1, so it IS the coverage signal; the record measures 5 of its 73 reds as
    # exit 2.
    # Probing exit 1 alone let `[ "$rc" = 2 ] && rc=0` green that entire class
    # while this test stayed green: the window then reads `covered`, the local
    # fallback goes idle, and nobody produces the required checks (#9365 review).
    #
    # 3 is not a class the tool emits today, and it is probed ON PURPOSE: a NEW
    # class must default to propagate, never to green. If a future arm greens one,
    # the coverage signal has to move in the same commit.
    #
    # A FRESH tmp_path per call: the stub journals its argv into it.
    script = step["run"]
    for tool_rc in (1, 2, 3):
        r, argv, summary_body = _run_repair_step(script, tmp_path / f"rc{tool_rc}", tool_rc)
        assert not r.stderr, (
            "the step's own shell failed for a reason that is NOT the repair "
            f"(stderr={r.stderr!r}) — without this, the summary block crashing "
            "would exit 1 and the `must exit 1` arm below would pass without "
            "rc ever being propagated (#9365 review)")
        assert r.returncode == tool_rc, (
            "the step must hand GitHub the tool's own exit code — every non-zero "
            f"class is the local fallback's handoff signal (#9365), so {tool_rc} must "
            f"not be greened; the step exited {r.returncode}. "
            f"stdout={r.stdout!r} stderr={r.stderr!r}")
        # ARGV: the step must still invoke the repair ARMED. Dropping `--apply`
        # makes the tool exit 0 for a `needs_user_actor` PR, i.e. it greens the
        # run while every exit-code assertion above still holds.
        assert "tools/ci_check_repair.py" in argv and "--apply" in argv, (
            "the repair step must invoke `tools/ci_check_repair.py --apply` — "
            "without `--apply` the tool exits 0 for the very state this run "
            f"exists to report, i.e. it greens the run (#9365); argv={argv!r}")
        assert "```json" in summary_body, (
            "the step summary must be written — its absence means the block "
            f"crashed rather than the rc being propagated (#9365); summary={summary_body!r}")
    r, _, _ = _run_repair_step(script, tmp_path / "rc0", tool_rc=0)
    assert r.returncode == 0, (
        f"a clean repair must exit 0 (#9365); the step exited {r.returncode}")

    # The classifier's polarity, on the states the launcher keys on.
    assert R.workflow_state([_wf("failure")], 35, 30) == "ran-not-succeeded"
    assert R.workflow_state([_wf("success")], 35, 30) == "covered"


def test_the_launcher_acts_on_a_failed_window_and_stays_idle_on_a_green_one(tmp_path):
    """The OTHER half of the coupling, on the surface that actually decides.

    `workflow_covered()` in `tools/ci-check-repair-local.sh` is
    `[ "$rc" = 0 ]` over `--workflow-state`'s exit code. Replacing that one line
    with `return 0` makes the local fallback permanently idle — the TOTAL #8254
    fail-open — while every assertion on `R.workflow_state` still passes, because
    the classifier is untouched (#9365 review). So drive the launcher, not the
    classifier: a `covered` window must not run the tool, and a
    `ran-not-succeeded` window must.

    Hermetic: `gh`, the interpreter and `cmux` are stubs, and the state dir is a
    tmpdir. The `cmux` stub matters — `ORCH_WS` defaults to a REAL workspace
    uuid, and `CI_REPAIR_ORCH_WS=''` does not disable it (`${VAR:-default}`
    treats empty as unset). Failing `cmux` is the launcher's own best-effort
    path, which keeps the durable inbox record and sends nothing.
    """
    launcher = str(Path(__file__).resolve().parents[1] / "tools/ci-check-repair-local.sh")
    calls = tmp_path / "tool-calls.log"
    bindir = tmp_path / "bin"
    bindir.mkdir()

    pystub = bindir / "pystub"
    pystub.write_text(
        "#!/usr/bin/env bash\n"
        'case " $* " in\n'
        '  *" --workflow-state "*)\n'
        '    printf "%s\\n" "${STUB_STATE}"; exit "${STUB_RC}" ;;\n'
        'esac\n'
        '# the launcher\'s inline interpreter-version guard\n'
        'if [ "${1:-}" = "-c" ]; then exit 0; fi\n'
        f'printf "%s\\n" "$*" >> {str(calls)!r}\n'
        'printf \'{"needing_repair": 0}\\n\'\n'
        "exit 0\n")
    pystub.chmod(0o755)

    ghstub = bindir / "ghstub"
    ghstub.write_text("#!/usr/bin/env bash\nprintf '[]'\nexit 0\n")
    ghstub.chmod(0o755)

    cmuxstub = bindir / "cmux"
    cmuxstub.write_text("#!/usr/bin/env bash\nexit 1\n")
    cmuxstub.chmod(0o755)

    def fire(state, rc):
        # A FRESH state dir per fire: the launcher APPENDS to its log, so a shared
        # one would carry the previous fire's "fallback idle" line into this one.
        fire_dir = tmp_path / f"state-{state}"
        if calls.exists():
            calls.unlink()
        env = {**os.environ, "CI_REPAIR_PYTHON": str(pystub),
               "CI_REPAIR_GH": str(ghstub),
               "CI_REPAIR_STATE_DIR": str(fire_dir),
               "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
               "STUB_STATE": state, "STUB_RC": str(rc)}
        r = subprocess.run(["bash", launcher, "--apply"], capture_output=True,
                           text=True, timeout=180, env=env)
        log_path = fire_dir / "ci-check-repair.log"
        log = log_path.read_text() if log_path.exists() else ""
        return r, log, (calls.read_text() if calls.exists() else "")

    # `covered` (exit 0) = the window WAS discharged -> the fallback must not run.
    r, log, invoked = fire("covered", 0)
    assert r.returncode == 0, r.stderr
    assert "local fallback idle" in log, log
    assert invoked == "", f"the fallback ran on a covered window: {invoked!r}"

    # `ran-not-succeeded` (exit 1) = the HANDOFF -> the fallback must run the tool.
    r, log, invoked = fire("ran-not-succeeded", 1)
    assert "local fallback idle" not in log, log
    assert "--apply" in invoked, (
        "a failed window IS the handoff — the local fallback must run the tool "
        f"(#9365); invocations were {invoked!r}, log={log!r}")


def test_reopen_is_deferred_while_the_head_still_has_runs_in_flight(monkeypatch):
    """A reopen re-fires every PR-evaluable workflow; doing it mid-run cancels CI."""
    monkeypatch.setattr(R, "_timeline_reopen_state", lambda *a, **k: (False, 0))
    monkeypatch.setattr(R, "_reopen", lambda *a: pytest.fail("must not cancel in-flight CI"))
    plan = _plan(absent=["ai-review-gate"], missing_run=["ai-review-gate"],
                 in_flight=["Python CI"])
    code, recs = R.report([plan], apply=True, trigger_ok=True, repo="o/r", cooldown_min=60)
    assert code == 0 and recs[0]["deferred"] == ["Python CI"]
    assert recs[0]["reopened"] is False


def test_classify_records_which_workflows_are_in_flight():
    runs = [run(1, "python-ci.yml", "in_progress", name="Python CI"),
            run(2, "ci.yml", "completed", "success", name="CI")]
    plan = R.classify(pr(), [], runs, ["ai-review-gate"], EMITTERS, max_attempts=3)
    assert plan.in_flight == ["Python CI"] and plan.missing_run == ["ai-review-gate"]


def test_timeline_reopen_state_counts_and_windows(monkeypatch):
    """One fetch answers both the cooldown and the budget question."""
    now = 1_700_000_000.0

    def at(seconds_ago):
        from datetime import UTC, datetime
        return datetime.fromtimestamp(now - seconds_ago, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    events = [{"event": "closed", "created_at": at(10)},
              {"event": "reopened", "created_at": at(30 * 60)},   # inside 60 min
              {"event": "reopened", "created_at": at(5 * 3600)},  # outside
              {"event": "reopened", "created_at": "not-a-date"}]     # still counted
    monkeypatch.setattr(R, "_paginate_array", lambda path: events)
    recent, total = R._timeline_reopen_state("o/r", 1, 60, now=now)
    assert recent is True and total == 3


# ── the recorded required set / emitters map ───────────────────────────────

def test_recorded_emitters_cover_every_required_context():
    record = Path(__file__).resolve().parents[1] / "docs/ci/required-contexts.json"
    doc = json.loads(record.read_text())
    emitters = R.emitters_from_record(record)
    for ctx in doc["required_contexts"]:
        assert emitters.get(ctx), f"required context {ctx} has no recorded emitting workflow"


def test_live_and_recorded_required_sets_must_agree(monkeypatch, tmp_path):
    record = tmp_path / "rec.json"
    record.write_text(json.dumps({"required_contexts": ["docs"],
                                  "emitters": {"docs": [["ci.yml", "docs"]]}}))
    monkeypatch.setattr(R, "gh_api", lambda *a, **k: {
        "required_status_checks": {"contexts": ["docs", "test-isolation"]}})
    with pytest.raises(R.MeasureError, match="disagree"):
        R.resolve_required("o/r", record, live=True)


def test_recorded_set_is_used_when_live_protection_is_unreadable(monkeypatch, tmp_path):
    record = tmp_path / "rec.json"
    record.write_text(json.dumps({"required_contexts": ["docs"], "emitters": {}}))

    def boom(*a, **k):
        raise R.MeasureError("403")

    monkeypatch.setattr(R, "gh_api", boom)
    assert R.resolve_required("o/r", record, live=True) == ["docs"]


# ── fail-closed measurement ────────────────────────────────────────────────

def test_gh_failure_is_a_measure_error_not_an_empty_answer(monkeypatch):
    monkeypatch.setattr(R, "GH_BIN", "/nonexistent/definitely-not-gh")
    with pytest.raises(R.MeasureError, match="could not run"):
        R.gh_json(["api", "repos/o/r"])


def test_a_nonzero_gh_exit_is_a_measure_error(monkeypatch):
    class Failed:
        returncode = 1
        stdout = ""
        stderr = "gh: Not Found (HTTP 404)"

    monkeypatch.setattr(R, "_run", lambda *a, **k: Failed())
    with pytest.raises(R.MeasureError, match="exited 1"):
        R.gh_json(["api", "repos/o/r/missing"])


def test_unparseable_gh_output_is_a_measure_error(monkeypatch):
    class Junk:
        returncode = 0
        stdout = "not json at all"
        stderr = ""

    monkeypatch.setattr(R, "_run", lambda *a, **k: Junk())
    with pytest.raises(R.MeasureError, match="unparseable"):
        R.gh_json(["api", "repos/o/r"])


# ── 3. no merge capability, asserted against the source ────────────────────

# ── fail closed when the record cannot name the emitter ────────────────────

def test_required_context_without_an_emitter_refuses_instead_of_looping():
    """An unmappable context would read as UNTRIGGERED forever -> refuse (exit 2)."""
    with pytest.raises(R.MeasureError, match="no recorded emitting workflow"):
        R.require_emitters(["docs", "mystery-context"],
                           {"docs": ["ci.yml"]}, Path("docs/ci/required-contexts.json"))


def test_every_live_required_context_has_a_recorded_emitter():
    """The record must be able to answer "which workflow emits this?" for all 7."""
    record = Path(__file__).resolve().parents[1] / "docs/ci/required-contexts.json"
    doc = json.loads(record.read_text())
    R.require_emitters(doc["required_contexts"], R.emitters_from_record(record), record)


# ── the scheduled half: bindings the repo-wide guards cannot see ───────────

def test_workflow_binds_a_token_where_the_repo_wide_guard_cannot_look():
    """`test_workflow_gh_token.py` greps for a literal `gh` in a `run:` step.

    This workflow has none — it runs `python3 tools/ci_check_repair.py`, which
    shells out to `gh` internally — so the repo-wide guard passes VACUOUSLY while
    an unbound token would make every measurement fail at run time. Pin the
    binding here, where the blind spot is visible.
    """
    wf = (Path(__file__).resolve().parents[1] / ".github/workflows/ci-check-repair.yml").read_text()
    assert "GH_TOKEN: ${{ github.token }}" in wf
    assert "actions: write" in wf          # POST /actions/runs/{id}/rerun
    assert "GITHUB_ACTIONS: \"true\"" in wf  # tells the tool it cannot trigger events


def test_truncated_pr_read_refuses_instead_of_reporting_nothing_to_repair(monkeypatch):
    """A truncated read is a false "nothing to repair" (#4256)."""
    monkeypatch.setattr(R, "gh_json", lambda *a, **k: [
        {"number": i, "isDraft": False, "headRefOid": "a" * 40} for i in range(400)])
    with pytest.raises(R.MeasureError, match="TRUNCATED"):
        R.open_prs("o/r", include_drafts=False)


def test_oldest_pending_age_is_reported_so_the_residual_re_measures_itself():
    from datetime import UTC, datetime, timedelta
    old = (datetime.now(UTC) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    runs = [R.Run(id=1, name="Python CI", path=".github/workflows/python-ci.yml",
                  status="queued", conclusion=None, run_attempt=1,
                  event="pull_request", created_at=old)]
    plan = R.classify(pr(), [], runs, ["python-ci-gate"], EMITTERS, max_attempts=3)
    assert plan.pending == ["python-ci-gate"]
    assert plan.oldest_pending_age_seconds and plan.oldest_pending_age_seconds > 3600


def test_an_unreadable_timestamp_is_not_a_zero_age():
    assert R.age_seconds("") is None
    assert R.age_seconds("not-a-timestamp") is None


# ── coverage: did the WORKFLOW discharge the window? ───────────────────────

WF = [{"event": "schedule", "status": "completed", "conclusion": "success"}]


def _wf(conclusion=None, status="completed", event="schedule", age_min=1.0):
    from datetime import UTC, datetime, timedelta
    ts = (datetime.now(UTC) - timedelta(minutes=age_min)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"conclusion": conclusion, "status": status, "event": event, "createdAt": ts}


def test_workflow_state_covered_success_discharges_the_window():
    assert R.workflow_state([_wf("success")], 35, 30) == "covered"


def test_workflow_state_in_flight_run_holds_the_fallback_off():
    assert R.workflow_state([_wf(None, status="in_progress", age_min=2)], 35, 30) == "in-flight"


def test_workflow_state_zombie_run_is_not_in_flight_forever():
    """A run still going past the grace bound must not hold the fallback off for ever.

    It is inside the coverage window (so it is a real window with a run) but older
    than the in-flight grace, so it is treated as a run that produced nothing —
    NOT as coverage.
    """
    assert R.workflow_state([_wf(None, status="in_progress", age_min=34)], 35, 30) \
        == "ran-not-succeeded"
    # Past the window entirely it is not a candidate at all (see the window test).
    assert R.workflow_state([_wf(None, status="in_progress", age_min=90)], 35, 30) \
        == "absent"


def test_workflow_state_a_failed_run_is_the_normal_credential_handoff():
    """exit-1 is the workflow MEASURING, not an outage — the two must not collapse."""
    assert R.workflow_state([_wf("failure")], 35, 30) == "ran-not-succeeded"
    assert R.workflow_state([_wf("success", age_min=90)], 35, 30) == "absent"


def test_workflow_state_ignores_runs_outside_the_window_and_other_events():
    assert R.workflow_state([_wf("success", event="push")], 35, 30) == "absent"
    assert R.workflow_state([], 35, 30) == "absent"
    assert R.workflow_state(None, 35, 30) == "absent"


def test_workflow_state_an_unreadable_run_is_not_a_coverage_claim():
    assert R.workflow_state([{"createdAt": "garbage", "status": "completed"}], 35, 30) \
        == "absent"


def test_workflow_state_rejects_json_of_the_wrong_shape():
    """No shape may traceback: the classifier answers `unreadable` instead."""
    assert R.workflow_state({}, 35, 30) == "unreadable"
    assert R.workflow_state([1, 2, 3], 35, 30) == "unreadable"
    assert R.workflow_state("covered", 35, 30) == "unreadable"
    assert R.workflow_state(123, 35, 30) == "unreadable"


def test_workflow_state_reports_exit_codes_the_launcher_keys_on(tmp_path, monkeypatch, capsys):
    """The launcher treats exit 0 as "the window was discharged" — pin both poles."""
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps([_wf("success")])))
    assert R.main(["--workflow-state", "--repo", "o/r"]) == 0
    assert capsys.readouterr().out.strip() == "covered"

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps([_wf("failure")])))
    assert R.main(["--workflow-state", "--repo", "o/r"]) == 1
    assert capsys.readouterr().out.strip() == "ran-not-succeeded"

    monkeypatch.setattr("sys.stdin", io.StringIO("not json at all"))
    assert R.main(["--workflow-state", "--repo", "o/r"]) == 1
    assert capsys.readouterr().out.strip() == "unreadable"


def test_installer_refuses_to_pin_a_schedule_to_an_ephemeral_worktree(tmp_path):
    """A plist pointing into `.worktrees/...` dies when the lane's PR merges.

    Without the hatch the installer must re-point at the MAIN checkout — and when
    main does not have the code yet (i.e. before this change lands) it must REFUSE
    rather than install a job that cannot run.

    HERMETIC BY CONSTRUCTION (#8392 review, P2). The first cut ran the REAL install
    path with the REAL `launchctl`: a throwaway `AGENTS_DIR` is not a sandbox,
    because `launchctl` addresses the domain by uid — and the refusal it depended on
    is gated on `main` lacking the launcher, so the moment this change merges the
    test would bootstrap a live job pointing at a temporary plist. The stubs below
    record `launchctl` invocations instead of performing them, and the assertion is
    on the script's DECISION (refuse, or pin to MAIN — never the worktree).
    """
    repo = Path(__file__).resolve().parents[1]
    common = subprocess.run(["git", "-C", str(repo), "rev-parse", "--path-format=absolute",
                             "--git-common-dir"], capture_output=True, text=True).stdout.strip()
    if not common.endswith("/.git") or Path(common[:-5]).resolve() == repo.resolve():
        pytest.skip("not a linked worktree")
    main_checkout = Path(common[:-5])

    bindir = tmp_path / "bin"
    bindir.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    stub_log = tmp_path / "launchctl.log"
    for name, body in (
        ("uname", "#!/usr/bin/env bash\necho Darwin\n"),
        # #8392 (`fa40084d0`) added the `gh` precondition to the install path —
        # the only commit that ever touched it (`git log -S "no usable 'gh'"`);
        # #9118 hit that refusal as PRE-EXISTING on a host with no `gh` in
        # /usr/bin, which is why the stub is needed here and why its citation
        # is #8392 and not #9118 (#9089 review). On the MAIN
        # checkout the launcher EXISTS (so the refuse branch below is not
        # taken), which means the controlled PATH has to satisfy that guard —
        # otherwise the install returns 2 for a reason this test does not
        # assert. The stub is resolved, never executed.
        ("gh", "#!/usr/bin/env bash\nexit 0\n"),
        ("launchctl", '#!/usr/bin/env bash\nprintf "launchctl %s\\n" "$*" '
                      '>> "$STUB_LOG"\nexit 0\n'),
        ("plutil", "#!/usr/bin/env bash\nexit 0\n"),
    ):
        stub = bindir / name
        stub.write_text(body, encoding="utf-8")
        stub.chmod(0o755)
    agents = home / "Library" / "LaunchAgents"
    env = {**os.environ, "AGENTS_DIR": str(agents),
           "CI_REPAIR_ALLOW_NONSTANDARD_AGENTS_DIR": "1", "TORTOISE_REPO": str(repo),
           "HOME": str(home), "STUB_LOG": str(stub_log),
           # CONTROLLED PATH: the stubs plus the system dirs only, so the host's real
           # `launchctl` can never be reached by the install path.
           "PATH": f"{bindir}:/usr/bin:/bin:/usr/sbin:/sbin"}
    env.pop("CI_REPAIR_ALLOW_WORKTREE_INSTALL", None)
    r = subprocess.run(["bash", str(repo / "tools/install-ci-check-repair-schedule.sh")],
                       capture_output=True, text=True, env=env, timeout=120)
    assert "MAIN checkout" in r.stderr, r.stderr
    plist = agents / "com.tortoise.ci-check-repair.plist"
    if r.returncode == 2:
        # `main` lacks the launcher yet: refuse rather than pin the worktree.
        assert "no tools/ci-check-repair-local.sh yet" in r.stderr
        assert not plist.exists(), "a refused install must not write a plist"
    else:
        # `main` HAS the code: the install is pinned to MAIN, never the worktree.
        import plistlib
        assert r.returncode == 0, (r.returncode, r.stderr)
        assert plist.exists(), r.stderr
        prog = plistlib.loads(plist.read_bytes())["ProgramArguments"]
        assert not any(".worktrees" in str(a) for a in prog), prog
        assert str(main_checkout) in " ".join(map(str, prog)), prog
        # Non-vacuity: the stub (not the host) served the install.
        assert "bootstrap" in stub_log.read_text(encoding="utf-8"), stub_log.read_text()


def test_installer_creates_the_log_directory_launchd_opens_before_exec():
    """launchd opens StandardOutPath BEFORE exec: a missing dir = the job cannot spawn."""
    src = (Path(__file__).resolve().parents[1]
           / "tools/install-ci-check-repair-schedule.sh").read_text()
    assert 'mkdir -p "$(dirname "$LOG_PATH")"' in src


def test_installer_passes_the_pinned_interpreter_to_the_worker():
    """Otherwise the launcher re-resolves at fire time and finds python 3.9."""
    src = (Path(__file__).resolve().parents[1]
           / "tools/install-ci-check-repair-schedule.sh").read_text()
    assert "<key>CI_REPAIR_PYTHON</key><string>@@PY@@</string>" in src


def test_launchd_entry_point_passes_its_own_directory(tmp_path):
    """The P0: under `bash -s` there is no script argument, so BASH_SOURCE is UNSET."""
    repo = Path(__file__).resolve().parents[1]
    entry = (repo / "tools/ci_check_repair_launchd.py").read_text()
    assert "CI_REPAIR_SCRIPT_DIR" in entry
    launcher = (repo / "tools/ci-check-repair-local.sh").read_text()
    assert 'CI_REPAIR_SCRIPT_DIR:-' in launcher, "launcher must accept the passed dir"
    assert '${BASH_SOURCE[0]:-}' in launcher, "`set -u` makes an unset BASH_SOURCE fatal"
    # Run it the way launchd does and require that the tool is actually FOUND. The
    # state dir is redirected so the run cannot touch the developer's real
    # ~/.pi/agent/state, and `--status` is read-only (it never repairs).
    #
    # ⛔ `TORTOISE_REPO` is POPPED, never inherited (#9388). The launcher resolves
    # `REPO="${TORTOISE_REPO:-$(cd "$SCRIPT_DIR/.." && pwd)}"`, so the fleet's
    # AMBIENT export (`TORTOISE_REPO=<the main checkout>`, set in every lane's
    # shell) outranks the directory the launchd entry point passes — the launcher
    # then correctly reports the MAIN checkout's tool, while the assertion below
    # demands the WORKTREE's, and the nodeid reds in every fleet lane while staying
    # green in CI (which exports no `TORTOISE_REPO`). Verified red on the ambient
    # value before this line existed.
    #
    # ⛔ NOT `TORTOISE_REPO=str(repo)`, the shape the sibling tests above use: the
    # tool path would then come from the OVERRIDE, so an entry point that passed a
    # garbage `CI_REPAIR_SCRIPT_DIR` — the #8375 P0 this test exists for — would
    # still be green. The override arm is pinned on its own below rather than
    # folded in here.
    env = {**os.environ, "CI_REPAIR_STATE_DIR": str(tmp_path),
           "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
    env.pop("TORTOISE_REPO", None)
    r = subprocess.run([sys.executable, str(repo / "tools/ci_check_repair_launchd.py"),
                        "--status"], capture_output=True, text=True, timeout=180,
                       cwd=repo, env=env)
    assert r.returncode == 0, r.stderr
    assert "tool not found" not in (r.stdout + r.stderr)
    assert "unbound variable" not in (r.stdout + r.stderr)
    assert "FATAL" not in (r.stdout + r.stderr)
    # And the runtime leg proves it: `--status` prints the RESOLVED tool path, so a
    # wrong `$SCRIPT_DIR` (the P0's actual failure) shows up here as MISSING. Without
    # this line the leg would pass even with a wrong path — `--status` returns before
    # the launcher's own tool-existence check.
    assert "(present)" in r.stdout, r.stdout
    assert str(repo / "tools/ci_check_repair.py") in r.stdout


def test_the_launcher_lets_TORTOISE_REPO_override_the_script_directory(tmp_path):
    """The arm the fleet's ambient export takes — pinned explicitly (#9388).

    `tools/ci-check-repair-local.sh` resolves `REPO="${TORTOISE_REPO:-<script
    dir>/..}"`, so an exported `TORTOISE_REPO` OUTRANKS the directory the launchd
    entry point passes. That precedence is intended (an operator pins the job at a
    chosen checkout) and it is exactly why the entry-point pin above must clear the
    variable: under the fleet's export the launcher reports the main checkout's
    tool, correctly, and an assertion demanding the worktree's path reds.

    Both arms are pinned here so neither can be "fixed" by weakening the other: in
    THIS one the resolved tool path must come from the override, not from the
    script's own directory. If a future edit makes the entry point ignore
    `CI_REPAIR_SCRIPT_DIR`, the test above reds; if one makes the launcher ignore
    `TORTOISE_REPO`, this one does.
    """
    repo = Path(__file__).resolve().parents[1]
    pinned = tmp_path / "pinned-checkout"
    (pinned / "tools").mkdir(parents=True)
    (pinned / "tools/ci_check_repair.py").write_text("", encoding="utf-8")
    env = {**os.environ, "CI_REPAIR_STATE_DIR": str(tmp_path / "state"),
           "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
           "TORTOISE_REPO": str(pinned)}
    r = subprocess.run([sys.executable, str(repo / "tools/ci_check_repair_launchd.py"),
                        "--status"], capture_output=True, text=True, timeout=180,
                       cwd=repo, env=env)
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    assert f"tool       : {pinned}/tools/ci_check_repair.py (present)" in r.stdout, (
        "the launcher did not resolve the tool from TORTOISE_REPO, so an operator "
        f"cannot pin the job at a checkout of their choosing: {r.stdout!r}")
    assert str(repo / "tools/ci_check_repair.py") not in r.stdout, (
        "the script's own directory still decided the tool path although "
        f"TORTOISE_REPO was set — the override is not in force: {r.stdout!r}")


def test_the_python_guard_refuses_an_old_interpreter_with_exit_2(tmp_path):
    """Exit 2, not 1: 1 is reserved for a repair this credential cannot perform.

    The guard is EXECUTED, never grepped for: a source-level
    `assert "SystemExit(2)" in src` would pass against a guard that never runs.

    It is driven hermetically, by running the module under a forced pre-3.12
    `sys.version_info`, so the assertion holds on ANY host. The previous form
    ran the box's `/usr/bin/python3` and skipped only when a probe disagreed --
    but the probe read `sys.version_info[0]`, the MAJOR version, which is `3`
    for both 3.9 and 3.12, so it never once skipped. On the Linux runner
    (`/usr/bin/python3` is 3.12) this test therefore executed the tool under a
    MODERN interpreter, got exit 0, and asserted 2 -- red `python-ci-gate`, and
    invisible on macOS, where that same probe happened to be right by accident
    (#8392).
    """
    repo = Path(__file__).resolve().parents[1]
    script = repo / "tools/ci_check_repair.py"
    driver = (
        "import runpy, sys\n"
        "sys.version_info = (3, 9, 0, 'final', 0)\n"
        f"sys.argv = [{str(script)!r}, '--help']\n"
        f"runpy.run_path({str(script)!r}, run_name='__main__')\n"
    )
    r = subprocess.run([sys.executable, "-c", driver],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
    assert "requires Python >= 3.12" in r.stderr


def test_the_tool_honours_the_GH_BIN_the_caller_resolved(tmp_path):
    """The launcher resolves a TCC-safe `gh`; a re-resolution via PATH can silently
    pick the fleet shim under ~/Documents that macOS denies."""
    repo = Path(__file__).resolve().parents[1]
    r = subprocess.run([sys.executable, "-c",
                        "from tools import ci_check_repair as R; print(R.GH_BIN)"],
                       capture_output=True, text=True, timeout=120, cwd=repo,
                       env={**os.environ, "GH_BIN": "/tmp/a-brand-new-gh"})
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "/tmp/a-brand-new-gh"


def test_installer_uses_the_checkout_interpreter_not_a_bash_entry_point():
    """macOS TCC: a launchd `bash <script under ~/Documents>` cannot read it.

    A source-level pin is the honest test here — actually RUNNING the installer
    would bootstrap a launchd job into the user's real domain, which no test may
    do. It pins the two defects that were really found: the bash entry point, and
    an UNQUOTED heredoc (which ran command substitution on a backtick inside a
    comment and emitted the output into the plist).
    """
    src = (Path(__file__).resolve().parents[1]
           / "tools/install-ci-check-repair-schedule.sh").read_text()
    assert "ci_check_repair_launchd.py" in src
    assert "<string>/bin/bash</string>" not in src
    assert """cat <<'PLIST'""" in src, "an unquoted heredoc executes backticks in the template"
    assert "patsub_replacement" in src


def test_installer_dry_run_touches_nothing(tmp_path):
    """`--dry-run` must mean NO SIDE EFFECTS — it once loaded a real launchd job.

    The flag was an alias for "install, but inert", so a lane that ran it to read
    the plist got a bootstrapped job in its login domain instead. This runs the
    real installer with a throwaway AGENTS_DIR and asserts the directory is still
    absent afterwards; the dry-run branch exits BEFORE `mkdir`/`bootstrap`.
    """
    repo = Path(__file__).resolve().parents[1]
    agents = tmp_path / "LaunchAgents"
    r = subprocess.run(["bash", str(repo / "tools/install-ci-check-repair-schedule.sh"),
                        "--dry-run"], capture_output=True, text=True,
                       env=_installer_env(tmp_path), timeout=120)
    assert r.returncode == 0, r.stderr
    assert "<key>ProgramArguments</key>" in r.stdout, "dry-run renders the real plist"
    assert "DRY RUN" in r.stderr
    assert not agents.exists(), "--dry-run wrote to the launchd dir"


def _installer_env(tmp_path, agents=None):
    """An env for executing the installer with the HOST's `uname` hidden.

    The installer refuses a non-Darwin host at its launchd gate (launchd is
    macOS-only, and the refusal names the cron route), so every test that
    EXECUTES it must pin the platform it reports — exactly as
    tests/test_install_reaper_schedule.py and
    tests/test_install_queue_reconcile_schedule.py do with their `_sandbox`.
    Without the stub the suite passes on the macOS dev box and fails on the
    Linux runner, which is the only platform CI runs: a real portability gap
    hidden by the host (#8392 review). `launchctl`/`plutil` are shadowed too, so
    an accidental install path can never reach the live launchd domain.

    Only `uname`/`launchctl`/`plutil` are stubbed on PATH; `git` and `python3`
    fall through to the host, which the installer's preflight needs.

    ⛔ `gh` is NOT taken from the host. It is pinned through the installer's
    own `CI_REPAIR_GH` seam (tools/install-ci-check-repair-schedule.sh:109) to a
    stub in this sandbox, because a PATH lookup made this whole file host-
    dependent: it resolved on the macOS dev box (a `gh` on PATH) and refused on
    any runner that ships no `gh` — `ERROR: refusing to install: no usable 'gh'
    (current: '<none>')`, `assert 2 == 0`, red on six tests in one shard
    (#10500 CI job 124879926455). That is the same class the queue-reconcile
    sibling already records in config/ci-surfaces.yml: "`gh`-absence is
    expressed through `GH_BIN`, NOT through the sandbox PATH". Presence belongs
    on the same seam for the same reason, and the sibling at
    `test_launcher_hands_the_plist_arguments_to_the_tool` already does it.
    Nothing here measures gh, so a stub is the honest double: these tests pin
    the RENDERED plist and the argument order, not gh's behaviour.
    """
    repo = Path(__file__).resolve().parents[1]
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    for name, body in (
        ("uname", "#!/usr/bin/env bash\necho Darwin\n"),
        ("launchctl", "#!/usr/bin/env bash\nexit 0\n"),
        ("plutil", "#!/usr/bin/env bash\nexit 0\n"),
        ("gh", "#!/usr/bin/env bash\nprintf '[]'\nexit 0\n"),
    ):
        stub = bindir / name
        stub.write_text(body, encoding="utf-8")
        stub.chmod(0o755)
    env = {**os.environ,
           "AGENTS_DIR": str(agents or (tmp_path / "LaunchAgents")),
           "TORTOISE_REPO": str(repo),
           # The preflight's `gh` requirement, satisfied from the sandbox instead
           # of from whichever host happens to have gh on PATH.
           "CI_REPAIR_GH": str(bindir / "gh"),
           "CI_REPAIR_ALLOW_NONSTANDARD_AGENTS_DIR": "1",
           "CI_REPAIR_ALLOW_WORKTREE_INSTALL": "1",
           # Prepend (not replace): the installer's preflight still needs the
           # host's git/python3, while the stubs win every lookup they own.
           "PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}"}
    return env


def _render_plist(tmp_path, *args):
    """Render the installer's plist without touching launchd, and parse it."""
    import plistlib
    repo = Path(__file__).resolve().parents[1]
    r = subprocess.run(["bash", str(repo / "tools/install-ci-check-repair-schedule.sh"),
                        "--dry-run", *args], capture_output=True, text=True,
                       env=_installer_env(tmp_path), timeout=120)
    assert r.returncode == 0, r.stderr
    return plistlib.loads(r.stdout.encode())


def test_installer_report_only_installs_an_INERT_job(tmp_path):
    """RENDER it, do not grep the source.

    The source-grep version of this test passed against a job that was ARMED:
    `--report-only` left the launcher with no arguments, and the launcher defaults
    to `--apply` — so the flag documented as "never repairs" closed and reopened PRs
    unattended. The plist is the artifact that decides.
    """
    args = _render_plist(tmp_path, "--report-only")["ProgramArguments"]
    assert "--dry-run" in args, args
    assert "--apply" not in args, args


def _run_installed_job(tmp_path, *installer_args):
    """Run the ENTRY POINT with the arguments the installer actually rendered.

    The plist's `ProgramArguments` are only half the contract; this asserts the
    OTHER half — that the launcher ACCEPTS those tokens and hands the tool the mode
    they name. Returns (CompletedProcess, list of tool invocation argv lines).
    """
    prog = _render_plist(tmp_path, *installer_args)["ProgramArguments"]
    assert prog[1].endswith("ci_check_repair_launchd.py"), prog
    mode_args = prog[2:]

    call_log = tmp_path / "tool-calls.log"
    stub = tmp_path / "pystub"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "${1:-}" = "-c" ]; then exit 0; fi\n'
        'case " $* " in\n'
        '  *" --workflow-state "*) printf "absent\\n"; exit 1 ;;\n'
        "esac\n"
        f'printf "%s\\n" "$*" >> {str(call_log)!r}\n'
        "printf '{\"needing_repair\": 0}\\n'\n"
        "exit 0\n")
    stub.chmod(0o755)
    ghstub = tmp_path / "ghstub"
    ghstub.write_text("#!/usr/bin/env bash\nprintf '[]'\nexit 0\n")
    ghstub.chmod(0o755)

    env = {**os.environ, "CI_REPAIR_PYTHON": str(stub), "CI_REPAIR_GH": str(ghstub),
           "CI_REPAIR_STATE_DIR": str(tmp_path / "state")}
    r = subprocess.run([sys.executable, prog[1], *mode_args], capture_output=True,
                       text=True, timeout=180, env=env)
    calls = call_log.read_text().splitlines() if call_log.exists() else []
    return r, calls


def test_the_armed_install_actually_runs_the_tool_armed(tmp_path):
    """P1 (both reviews): the armed plist passed `--apply`, the launcher rejected it,
    and the installed job exited 2 on every fire — before it made its state dir or
    logged anything. Render the plist, RUN the entry point with the plist's real
    arguments, and assert the tool is invoked, armed. `--status` (the only token the
    old test exercised) never reaches this path.
    """
    r, calls = _run_installed_job(tmp_path)          # armed is the install default
    assert r.returncode != 2, (r.returncode, r.stderr)
    assert "usage:" not in (r.stdout + r.stderr), r.stderr
    tool_calls = [c for c in calls if "--json" in c]
    assert tool_calls, f"the tool was never invoked: {calls!r}"
    assert "--apply" in tool_calls[-1], tool_calls


def test_the_report_only_install_never_arms_the_tool(tmp_path):
    """P1: `--report-only` must reach the tool WITHOUT `--apply`."""
    r, calls = _run_installed_job(tmp_path, "--report-only")
    assert r.returncode != 2, (r.returncode, r.stderr)
    tool_calls = [c for c in calls if "--json" in c]
    assert tool_calls, f"the tool was never invoked: {calls!r}"
    assert "--apply" not in tool_calls[-1], tool_calls


def test_the_launcher_accepts_every_token_the_installer_renders(tmp_path):
    """The launcher's real CLI contract, asserted directly."""
    launcher = str(Path(__file__).resolve().parents[1] / "tools/ci-check-repair-local.sh")
    harmless = {**os.environ, "CI_REPAIR_STATE_DIR": str(tmp_path / "s"),
                "CI_REPAIR_GH": "/bin/echo", "CI_REPAIR_PYTHON": "/bin/echo"}
    for token in ("--apply", "--dry-run", "--report-only", "--force"):
        r = subprocess.run(["bash", launcher, token, "--status"], capture_output=True,
                           text=True, timeout=120, env=harmless)
        assert r.returncode == 0, (token, r.returncode, r.stderr)
        assert "usage:" not in r.stderr, (token, r.stderr)
    # Word order must never decide the mode.
    r = subprocess.run(["bash", launcher, "--apply", "--dry-run"], capture_output=True,
                       text=True, timeout=120, env=harmless)
    assert r.returncode == 2 and "mutually exclusive" in r.stderr, r.stderr


def test_exhausted_context_escalates_even_when_the_pr_also_has_a_missing_run(monkeypatch):
    """P1: this combination used to exit 0 and report nothing to act on.

    Exit 0 is the workflow's "window discharged" signal the local fallback keys on,
    so a required check that can never be produced was reported as handled and
    nobody was told. The escalation is now hoisted above the repair branches.
    """
    monkeypatch.setattr(R, "_timeline_reopen_state", lambda *a, **k: (False, 0))
    monkeypatch.setattr(R, "_reopen", lambda *a, **k: "reopened")
    plan = _plan(absent=["docs", "ai-review-gate"], exhausted=["docs"],
                 missing_run=["ai-review-gate"])
    code, recs = R.report([plan], apply=True, trigger_ok=True, repo="o/r", cooldown_min=60)
    assert code == 1, "an exhausted context must never be silently discharged"
    assert recs[0]["exhausted"] == ["docs"]


def test_a_stuck_run_cannot_make_the_budget_escalation_unreachable(monkeypatch):
    """P1: the deferral was tested BEFORE the budget, so a permanently-queued run
    (the documented residual) kept `in_flight` true for ever and the escalation that
    exists to end the loop could never fire — while the tool still exited 0."""
    monkeypatch.setattr(R, "_timeline_reopen_state", lambda *a, **k: (False, 99))
    monkeypatch.setattr(R, "_reopen", lambda *a, **k: "reopened")
    plan = _plan(absent=["ai-review-gate"], missing_run=["ai-review-gate"],
                 in_flight=["Mergify Merge Queue"])
    code, recs = R.report([plan], apply=True, trigger_ok=True, repo="o/r",
                          cooldown_min=60, max_attempts=3)
    assert code == 1
    assert "deferred" not in recs[0]
    assert recs[0]["exhausted"] == ["ai-review-gate"]


def test_deferral_still_applies_below_the_budget(monkeypatch):
    monkeypatch.setattr(R, "_timeline_reopen_state", lambda *a, **k: (False, 0))
    monkeypatch.setattr(R, "_reopen", lambda *a, **k: "reopened")
    plan = _plan(absent=["ai-review-gate"], missing_run=["ai-review-gate"],
                 in_flight=["Python CI"])
    code, recs = R.report([plan], apply=True, trigger_ok=True, repo="o/r", cooldown_min=60)
    assert code == 0 and recs[0]["deferred"] == ["Python CI"]


def test_a_head_that_moved_is_not_repaired(monkeypatch):
    """TOCTOU: a pass over many PRs takes minutes, and a reopen re-fires EVERY
    PR-evaluable workflow, whose concurrency groups cancel the NEW head's CI."""
    seen = {}

    def fake(repo, number, expected_head=""):
        seen["head"] = expected_head
        return "head_moved"

    monkeypatch.setattr(R, "_timeline_reopen_state", lambda *a, **k: (False, 0))
    monkeypatch.setattr(R, "_reopen", fake)
    plan = _plan(absent=["ai-review-gate"], missing_run=["ai-review-gate"])
    code, recs = R.report([plan], apply=True, trigger_ok=True, repo="o/r", cooldown_min=60)
    assert seen["head"] == "b" * 40, "the expected head must be passed through"
    assert recs[0]["head_moved"] is True and recs[0]["reopened"] is False
    assert code == 0, "a moved head is a skip, not a failure"


def test_a_timed_out_reopen_is_not_reported_as_nothing_changed(monkeypatch):
    """P1: `close` succeeded and `reopen` timed out — the PR is CLOSED, and that
    used to be reported as 'failed' ("nothing changed"), the one outcome the
    docstring calls strictly worse than the absent check."""
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="gh", timeout=30)

    monkeypatch.setattr(R, "_run", boom)
    monkeypatch.setattr(R, "_pr_state", lambda repo, n: "CLOSED")
    assert R._reopen("o/r", 1) == "left_closed"


def test_a_timed_out_close_that_did_not_land_is_a_plain_failure(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="gh", timeout=30)

    monkeypatch.setattr(R, "_run", boom)
    monkeypatch.setattr(R, "_pr_state", lambda repo, n: "OPEN")
    assert R._reopen("o/r", 1) == "failed"


def test_a_close_timeout_with_an_UNREADABLE_state_still_names_left_closed(monkeypatch):
    """The presumption goes to the outcome that must never be silent.

    `_pr_state` returns '' when the PR cannot be read. Reporting that as "nothing
    changed" would lose the name of the one outcome the docstring calls worse than
    the absent check.
    """
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="gh", timeout=30)

    monkeypatch.setattr(R, "_run", boom)
    monkeypatch.setattr(R, "_pr_state", lambda repo, n: "")
    assert R._reopen("o/r", 1) == "left_closed"


def test_uninstall_under_dry_run_is_refused_not_executed(tmp_path):
    """`--dry-run` promises NO side effects; removing the installed schedule is the
    largest one available. Word order must not decide whether it survives."""
    repo = Path(__file__).resolve().parents[1]
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    stale = agents / "com.tortoise.ci-check-repair.plist"
    stale.write_text("<plist/>")
    env = _installer_env(tmp_path, agents=agents)
    for order in (["--dry-run", "--uninstall"], ["--uninstall", "--dry-run"]):
        r = subprocess.run(["bash", str(repo / "tools/install-ci-check-repair-schedule.sh"),
                            *order], capture_output=True, text=True, env=env, timeout=120)
        assert r.returncode == 2, (order, r.stdout, r.stderr)
        assert "mutually exclusive" in r.stderr
        assert stale.exists(), f"{order} removed the installed schedule"
    # An unknown argument is refused even when it follows an action flag.
    r = subprocess.run(["bash", str(repo / "tools/install-ci-check-repair-schedule.sh"),
                        "--status", "--nonsense"], capture_output=True, text=True,
                       env=env, timeout=120)
    assert r.returncode == 2 and "unknown argument" in r.stderr


def test_reopen_skips_when_the_head_cannot_be_re_read(monkeypatch):
    """An unverifiable head is a skip, never a guessed repair."""
    def boom(*a, **k):
        raise R.MeasureError("unreadable")

    monkeypatch.setattr(R, "gh_json", boom)
    called = []
    monkeypatch.setattr(R, "_run", lambda *a, **k: called.append(a))
    assert R._reopen("o/r", 1, expected_head="a" * 40) == "failed"
    assert called == [], "nothing may be closed when the head is unknown"


def test_workflow_state_the_newest_run_decides():
    """P2: 'any run in the window succeeded' let a stale success mask a newer
    failure, suppressing the credential handoff for a whole window."""
    assert R.workflow_state([_wf("failure", age_min=3), _wf("success", age_min=25)],
                            35, 30) == "ran-not-succeeded"
    assert R.workflow_state([_wf("success", age_min=25), _wf("failure", age_min=30)],
                            35, 30) == "covered"


def test_can_trigger_events_reads_the_environment(monkeypatch):
    """The whole GitHub/local credential split, previously untested."""
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("CI_CHECK_REPAIR_TRIGGER", raising=False)
    assert R.can_trigger_events() is True, "a local box holds a user credential"
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert R.can_trigger_events() is False, "GITHUB_TOKEN events create no runs"
    monkeypatch.setenv("CI_CHECK_REPAIR_TRIGGER", "yes")
    assert R.can_trigger_events() is True, "an operator may declare a PAT"
    monkeypatch.setenv("CI_CHECK_REPAIR_TRIGGER", "no")
    assert R.can_trigger_events() is False


def test_a_readable_but_empty_live_required_set_refuses(monkeypatch, tmp_path):
    """P2: `if live_ctx:` collapsed [] into None, so after every required context
    was removed the tool silently fell back to the RECORD and kept reopening PRs for
    a set that is no longer protected. A readable live set that disagrees refuses."""
    rec = tmp_path / "required-contexts.json"
    rec.write_text(json.dumps({"required_contexts": ["docs"]}))
    monkeypatch.setattr(R, "gh_api", lambda *a, **k: {
        "required_status_checks": {"contexts": []}})
    with pytest.raises(R.MeasureError, match="disagree"):
        R.resolve_required("o/r", rec, live=True)


def test_an_empty_live_set_and_an_empty_record_refuses(monkeypatch, tmp_path):
    """Nothing is required at all: repairs would act on a retired set."""
    rec = tmp_path / "required-contexts.json"
    rec.write_text(json.dumps({"required_contexts": []}))
    monkeypatch.setattr(R, "gh_api", lambda *a, **k: {
        "required_status_checks": {"contexts": []}})
    with pytest.raises(R.MeasureError, match="requires NOTHING"):
        R.resolve_required("o/r", rec, live=True)


def test_an_UNREADABLE_live_set_still_falls_back_to_the_record(monkeypatch, tmp_path):
    """The other pole: unreadable is not empty, and the record is the fallback."""
    rec = tmp_path / "required-contexts.json"
    rec.write_text(json.dumps({"required_contexts": ["docs"]}))

    def boom(*a, **k):
        raise R.MeasureError("no admin")

    monkeypatch.setattr(R, "gh_api", boom)
    assert R.resolve_required("o/r", rec, live=True) == ["docs"]


def test_main_reports_exit_2_when_the_record_cannot_be_read(capsys, tmp_path):
    """The fail-closed pole of the contract, through `main()` itself.

    `--no-queue-health` is passed deliberately: this suite is registered on the
    `core` surface as "Pure unit tests, no network", and without the flag `main()`
    reaches `read_queue_health()` before its `try:` block, i.e. a real
    `gh api repos/.../actions/runs` call on every run. The queue read is not what
    this test is about, so it is switched off rather than depended on.
    """
    assert R.main(["--repo", "o/r", "--no-live", "--no-queue-health", "--record",
                   str(tmp_path / "nope.json")]) == 2
    assert "COULD NOT MEASURE" in capsys.readouterr().err


def test_an_unexpected_error_in_the_pass_is_exit_2_not_1(capsys, monkeypatch):
    """Exit 1 is a specific claim; a bug must not be able to make it."""
    def boom(*a, **k):
        raise KeyError("malformed run payload")

    monkeypatch.setattr(R, "open_prs", boom)
    assert R.main(["--repo", "o/r", "--no-live", "--no-queue-health"]) == 2
    assert "unexpected KeyError" in capsys.readouterr().err


# ── the #9115 composition: a measured FLOOD reaches the report ─────────────

def _flood_record(tmp_path):
    """A minimal required-contexts record for one context, `docs` <- ci.yml."""
    rec = tmp_path / "required-contexts.json"
    rec.write_text(json.dumps({
        "required_contexts": ["docs"],
        "emitters": {"docs": [["ci.yml", "docs"]]},
    }))
    return rec


def _flood_health():
    """A REAL (pure, no API) FLOOD reading: a trim-safe offender + a starved PR run."""
    from datetime import UTC, datetime, timedelta

    from tools import ci_queue_health as Q

    now = datetime(2026, 10, 19, 3, 30, 0, tzinfo=UTC)

    def stamp(seconds):
        return (now - timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")

    queued = [Q.QueuedRun(id=1000 + i, name="finding-provenance", event="issues",
                          created_at=stamp(1200 - i)) for i in range(12)]
    queued.append(Q.QueuedRun(id=9000, name="Python CI", event="pull_request",
                              created_at=stamp(1200)))
    health = Q.classify_queue(queued, [], now)
    assert health.verdict == Q.FLOOD, "the fixture must actually be a flood"
    return health


def test_main_threads_a_measured_flood_into_the_report(monkeypatch, capsys, tmp_path):
    """The load-bearing composition: FLOOD -> `starves_pr` -> classify -> starved.

    `read_queue_health` and `classify(..., queue_starved=)` are each tested in
    isolation, and inverting the predicate between them (the call site's verdict
    test, or `is not None` -> `is None`) left BOTH suites green while the
    instrument silently never reported STARVED — the blindness #9115 exists to
    remove, restored with a green suite. This test is that seam.
    """
    monkeypatch.setattr(R, "read_queue_health", lambda repo: _flood_health())
    monkeypatch.setattr(R, "open_prs", lambda repo, include_drafts: [pr(1)])
    monkeypatch.setattr(R, "check_runs_at", lambda repo, head: [])
    monkeypatch.setattr(R, "runs_at_head", lambda repo, head: [run(10, "ci.yml", "queued")])

    assert R.main(["--repo", "o/r", "--no-live", "--json",
                   "--record", str(_flood_record(tmp_path))]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["queue_starved"] is True
    assert summary["starved"] == 1
    assert summary["queue"].startswith("CI: FLOOD(")
    assert summary["prs"][0]["starved"] == ["docs"]


def test_main_reports_a_PR_STARVED_reading_as_starved_too(monkeypatch, capsys, tmp_path):
    """ISSUE 2 of the #9118 final review: the late stage must not go blind.

    `PR-STARVED` is the repo-level reading for "no PR-event run is executing and a
    queued PR run has waited past the age floor, while no governor block is
    reached" — the offender's share is below the dominance floor, or below
    `global_max` with PR work holding more of the queue, so a trim-safe offender
    CAN be present and the reason line names the clause that declined. It differs
    from `FLOOD` only in whether this reading reaches the governor, not in whether
    PR CI is running — so keying the per-PR seam on `FLOOD` alone left exactly the
    state the module calls the LATE STAGE of the measured incident reading as
    `pending` and therefore dropped from the report (#9115's original silence, one
    level down).
    """
    from datetime import UTC, datetime, timedelta

    from tools import ci_queue_health as Q

    now = datetime(2026, 10, 19, 3, 30, 0, tzinfo=UTC)

    def stamp(seconds):
        return (now - timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # A PR-dominated queue: the offender (here `Python CI` itself) is not a
    # trim-safe workflow, so this WITNESSES starvation with no action available.
    queued = [Q.QueuedRun(id=9000 + i, name="Python CI", event="pull_request",
                         created_at=stamp(1200)) for i in range(20)]
    pr_starved = Q.classify_queue(queued, [], now)
    assert pr_starved.verdict == Q.PR_STARVED, "the fixture must actually be PR-STARVED"
    assert pr_starved.starves_pr is True

    monkeypatch.setattr(R, "read_queue_health", lambda repo: pr_starved)
    monkeypatch.setattr(R, "open_prs", lambda repo, include_drafts: [pr(1)])
    monkeypatch.setattr(R, "check_runs_at", lambda repo, head: [])
    monkeypatch.setattr(R, "runs_at_head", lambda repo, head: [run(10, "ci.yml", "queued")])

    seen: list[bool] = []
    real_classify = R.classify

    def spy(*a, **k):
        seen.append(k.get("queue_starved"))
        return real_classify(*a, **k)

    monkeypatch.setattr(R, "classify", spy)

    assert R.main(["--repo", "o/r", "--no-live", "--json",
                   "--record", str(_flood_record(tmp_path))]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert seen == [True]
    assert summary["queue_starved"] is True
    assert summary["queue"].startswith("CI: PR-STARVED(")
    assert summary["starved"] == 1
    assert summary["prs"][0]["starved"] == ["docs"]


def test_main_reports_starvation_not_witnessed_when_the_queue_is_unreadable(
        monkeypatch, capsys, tmp_path):
    """The other pole: no flood measured is NOT a flood, and says so."""
    monkeypatch.setattr(R, "read_queue_health", lambda repo: None)
    monkeypatch.setattr(R, "open_prs", lambda repo, include_drafts: [pr(1)])
    monkeypatch.setattr(R, "check_runs_at", lambda repo, head: [])
    monkeypatch.setattr(R, "runs_at_head", lambda repo, head: [run(10, "ci.yml", "queued")])
    # The seam itself: what `classify` is actually TOLD. Without this the assertion
    # would only be that no flood was reported, which is also what a hard-wired
    # `queue_starved=False` produces.
    seen: list[bool] = []
    real_classify = R.classify

    def spy(*a, **k):
        seen.append(k.get("queue_starved"))
        return real_classify(*a, **k)

    monkeypatch.setattr(R, "classify", spy)

    assert R.main(["--repo", "o/r", "--no-live", "--json",
                   "--record", str(_flood_record(tmp_path))]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert seen == [False]
    assert summary["queue_starved"] is False
    assert summary["starved"] == 0
    assert summary["queue"] == "CI: UNKNOWN (the queue was not read)"
    assert summary["open_prs"] == 1
    # A merely-PENDING context is LATE, not absent: no repair, no record (the
    # pre-#9115 behaviour this flag restores exactly).
    assert summary["prs"] == []


def test_installer_armed_by_default(tmp_path):
    assert _render_plist(tmp_path)["ProgramArguments"][-1] == "--apply"


def test_installer_refuses_an_unknown_argument_and_dry_run_wins_by_order(tmp_path):
    """Only `$1` used to be read, so `--report-only --dry-run` INSTALLED."""
    repo = Path(__file__).resolve().parents[1]
    agents = tmp_path / "LaunchAgents"
    env = _installer_env(tmp_path)
    r = subprocess.run(["bash", str(repo / "tools/install-ci-check-repair-schedule.sh"),
                        "--report-only", "--nonsense"],
                       capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 2 and "unknown argument" in r.stderr
    assert not agents.exists()
    r = subprocess.run(["bash", str(repo / "tools/install-ci-check-repair-schedule.sh"),
                        "--report-only", "--dry-run"],
                       capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 0 and "DRY RUN" in r.stderr
    assert not agents.exists(), "--dry-run did not win by word order"


def test_launchd_entry_point_hands_the_launcher_to_bash_on_stdin():
    entry = (Path(__file__).resolve().parents[1]
             / "tools/ci_check_repair_launchd.py").read_text()
    # bash -s reads the script from stdin; bash must never open the file itself.
    assert '"/bin/bash", "-s", "--"' in entry
    assert "with LAUNCHER.open" in entry


# ── 3. no merge capability, asserted against the source ────────────────────

def test_module_never_merges():
    """Pins the SURFACE, not a list of expletives (a grep for "merge" misses these).

    A regression could merge without ever writing the word `merge` as a quoted
    string: `gh api -X PUT repos/o/r/pulls/1/merge`, a GraphQL `mergePullRequest`
    mutation, or `gh api graphql -f query=...`. Each of those is named here.
    """
    repo = Path(__file__).resolve().parents[1]
    for rel in ("tools/ci_check_repair.py", "tools/ci-check-repair-local.sh",
                ".github/workflows/ci-check-repair.yml",
                "tools/ci_check_repair_launchd.py"):
        src = (repo / rel).read_text()
        for pattern in (r"pr\s+merge", r"--admin", r"--merge\b", r"/merge\b",
                        r"mergePullRequest", r"enablePullRequestAutoMerge",
                        r"auto[-_]?merge", r"graphql"):
            assert not re.search(pattern, src), f"{rel} matches {pattern!r}"


def test_the_workflow_cannot_merge_even_in_principle():
    """Least privilege: the Actions actor has no permission a merge would need."""
    import yaml
    wf = yaml.safe_load((Path(__file__).resolve().parents[1]
                         / ".github/workflows/ci-check-repair.yml").read_text())
    perms = wf.get("permissions") or wf[True]["permissions"]  # `on:` parses as True
    assert perms.get("pull-requests") == "read", "a write scope would allow a merge"
    assert perms.get("contents") == "read"
    assert perms.get("actions") == "write", "needed for POST /actions/runs/{id}/rerun"


def test_the_held_run_shape_this_tool_exists_to_repair_is_completed():
    """MEASURED, not assumed: `action_required` runs are `status=completed`.

    This matters because `classify()` reads a NON-completed producing run as
    PENDING (late) and never repairs it. Measured live on this repo 2026-10-12
    (PR #8381 head 6e462035, PR #8378 head 05dfe90e): every held run was
    `status=completed, conclusion=action_required, attempt=1, actor
    github-actions[bot]`, with zero jobs and zero check runs. So the shape reaches
    the DEAD branch and `rerun` fixes it. If GitHub ever parks a run in a
    non-completed status, this test is where that would first show up.
    """
    run = R.Run(id=1, name="Python CI", path=".github/workflows/python-ci.yml",
                status="completed", conclusion="action_required", run_attempt=1,
                event="pull_request")
    plan = R.classify(pr(), [], [run], ["python-ci-gate"], EMITTERS, max_attempts=3)
    assert plan.pending == [], "a held-but-completed run must NOT read as merely late"
    assert [r.id for r in plan.rerun] == [1]


# ── 12. a run that is PENDING with ZERO JOBS will never emit a verdict ──────
#
# MEASURED on daniel-ospina/tortoise 2026-11-02T20:14Z: 11 of 16 `pending` runs
# had a job list of `total_count: 0` at 11-22 minutes old, while 11 runners were
# online (10 busy, 1 offline). The merge rail's precondition is "a run must have
# TESTED this head", so such a run can never satisfy it, and the class was
# reported as merely PENDING — a wait that cannot clear. These tests keep the
# three halves apart: MEASURED zero + AGE is a named refusal; a young zero-job run
# is ordinary progressive-job-creation lag; an UNREADABLE count is an abstention.

def aged_run(rid, status="pending", jobs=0, age_s=1800.0, attempt=1,
             path="python-ci.yml", name="Python CI", started_age_s=None,
             event="pull_request"):
    from datetime import UTC, datetime
    created = datetime.fromtimestamp(R.time.time() - age_s, UTC).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    started = ("" if started_age_s is None else datetime.fromtimestamp(
        R.time.time() - started_age_s, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
    return R.Run(id=rid, name=name or Path(path).stem, path=f".github/workflows/{path}",
                 status=status, conclusion=None, run_attempt=attempt,
                 event=event, created_at=created, job_count=jobs,
                 started_at=started)


def test_aged_zero_job_pending_run_is_named_no_verdict_will_arrive(capsys):
    runs = [aged_run(41480218593, jobs=0, age_s=5700.0)]
    plan = R.classify(pr(), [], runs, ["python-ci-gate"], EMITTERS, max_attempts=3)

    assert [d["run"] for d in plan.no_verdict] == [41480218593]
    assert plan.no_verdict[0]["jobs"] == 0
    assert plan.no_verdict[0]["age_seconds"] >= 5400, (
        "the reported age is past the MEASURED max first-job wait (81.9 min)")
    # It stays PENDING as well: a reopen must remain deferred while a run sits there.
    assert "python-ci-gate" in plan.pending
    # And it is ACTED on with this module's own primitive, not waited on.
    assert [r.id for r in plan.rerun] == [41480218593]
    assert plan.needs_report is True

    R.report([plan], apply=False, trigger_ok=True, repo="o/r", cooldown_min=60)
    captured = capsys.readouterr()
    assert "NO-VERDICT-WILL-ARRIVE(run 41480218593, pending, 0 jobs," in captured.err
    # ⛔ stdout is reserved for the ONE `--json` document: a notice printed there makes
    # `json.load` raise in tools/ci-check-repair-local.sh (swallowed by `2>/dev/null`,
    # reported as "PRs needing repair: ?", freezing its transition signature) and
    # invalidates the workflow's step-summary JSON block.
    assert captured.out == "", "the notice must never enter the --json stream"


def test_young_zero_job_run_is_still_ordinary_lag():
    """Below the floor this shape is normal: GitHub creates jobs progressively."""
    plan = R.classify(pr(), [], [aged_run(1, jobs=0, age_s=120.0)],
                      ["python-ci-gate"], EMITTERS, max_attempts=3)
    assert plan.no_verdict == [] and plan.rerun == []
    assert "python-ci-gate" in plan.pending


def test_unreadable_job_count_is_an_abstention_never_a_zero():
    """`None` is not `0`: inferring a stall from an unreadable read is fail-open."""
    plan = R.classify(pr(), [], [aged_run(1, jobs=None, age_s=3600.0)],
                      ["python-ci-gate"], EMITTERS, max_attempts=3)
    assert plan.no_verdict == [] and plan.rerun == []


def test_a_configured_budget_of_one_still_escalates_the_repair_it_forbids():
    """`max_attempts` is honoured for the attempt this tool WOULD have repaired.

    The structural bound (`ZERO_JOB_MAX_REPAIRABLE_ATTEMPT`) caps the ACT at attempt 1;
    `max_attempts` still decides whether attempt 1 may be spent at all. A caller who
    configures a budget of 1 gets the escalation, not a silent no-op.
    """
    plan = R.classify(pr(), [], [aged_run(1, jobs=0, age_s=5700.0, attempt=1)],
                      ["python-ci-gate"], EMITTERS, max_attempts=1)
    assert plan.rerun == [], "the budget is spent — re-running again is a loop"
    assert "python-ci-gate" in plan.exhausted


def test_the_zero_job_floor_is_past_the_measured_queue_wait():
    """⭐ P1 (review): the floor must sit PAST the measured-normal population.

    `docs/ops/ci-wall-clock.md` §1 line 50 (54 `python-ci` runs) measures the wait for a
    run's FIRST job: p50 29.9 min, p95 77.0 min, max 81.9 min. The budget derivation
    this floor defers to is `tools/ci_queue_health.py:350`
    (`DEFAULT_MAX_PR_WAIT_SECONDS = 90 * 60.0`, "the first round number past" that max),
    and `ci-wall-clock.md` §6 lines 439-441 records the same derivation in its own
    words ("max first-job wait 81.9 min → 90 min, so it fires past the measured-normal
    population rather than on it"). This docstring carries the SECOND citation of that same
    span, and the mechanical pin below holds it EQUAL to the one in
    `tools/ci_check_repair.py` — so a re-cut of either alone reddens, and the span is never
    restated from memory. The floor was
    1200 s, i.e. it selected the MEDIAN wait, and the repair for this class is
    cancel-then-rerun: at the median it makes the median run slower and adds one more
    run to the queue it is starved behind.

    Discriminating: at the pre-fix 1200 s this assertion is red.
    """
    assert R.ZERO_JOB_MIN_AGE_S >= 81.9 * 60, (
        f"the floor is {R.ZERO_JOB_MIN_AGE_S:.0f}s, INSIDE the measured max first-job "
        "wait (81.9 min) — a run flagged there is the measured-NORMAL pre-start state")


def test_the_floor_citations_RESOLVE_to_what_they_claim_at_this_head():
    """P3 (review): the citation block is the change's own claim about ITSELF.

    Commit `a7436214f` ("cite the floor's calibration as it is written") ships three
    coordinates for the 5400 s floor, and the defect class it kept is that a coordinate
    can name the WRONG place: a trailing `#:` comment beside the derived value, or a
    different section of the document than the sentence it is quoted for. Both drifts
    were real, and both were hand-re-cut more than once before the pin below existed.

    ⛔ NEITHER DRIFT'S ILLUSTRATIVE SPAN IS RESTATED HERE (round 13's P3s, and round
    14's P3: an earlier revision described them with numbers that were stale at this
    very head — and in one case had never been right at ANY head, which is why an
    anecdote about them is not evidence worth keeping). This PR grew that document, so
    a coordinate that is only true on `origin/main` hands the next reader
    a checkable fact that is false here. The lesson needs no coordinate: a citation can
    name another section entirely, and the docstring quoted above warns that whoever
    mis-locates the derivation "discards the one section that supports this floor" — so
    a stale coordinate sends the next lane to exactly the misreading the paragraph
    exists to prevent.

    Pinned MECHANICALLY instead of corrected by hand a third time: the numbers are read
    back out of the docstrings and resolved against the files they cite, and the pin is
    PHRASING-AGNOSTIC (round 14's P2: pinning one literal phrasing left this file's own
    previous wording — and every new one — uncovered, so a re-introduced coordinate
    stayed green). FIVE arms, each with its scope stated so the claim matches the code
    (round 15's P2 caught an earlier sentence claiming file-wide reach while the sweeping
    arm read only one function):

    - **every** `ci_queue_health.py:<line>` cited by EITHER file must BE the assignment;
    - the cited `§6 (lines A-B)` span must contain the sentence it is quoted for;
    - the span is DERIVED from the document's own text (the sentence's first and last
      line) and the citation must equal it — so a shift reds with the number to write
      instead of leaving the next lane to re-derive the offset by hand, which is what
      arms 1-2 cannot do: they accept any span WIDE ENOUGH to contain the measurement,
      which is not the same thing as the span the sentence occupies;
    - the sibling's `§6 lines A-B` citation must be found EXACTLY once (as the liveness
      anchor, so the checked set cannot silently shrink) and must name the pin's span;
    - every line of this file containing one of the CITATION tokens below **and** an
      `A-B` span must name that same span. ⛔ THE TOKEN LIST IS THE DEFINITION, not an
      illustrative sample (round 16's blocking P2: an earlier sentence promised
      "whatever the wording around it", which the implementation never did, so a citation
      phrased in words this file uses constantly — "cited at", "quoted at" — stayed
      green while naming a stale span). The tokens are `§ 6` (with or without the space),
      `derivation`, `cite`/`cited`/`cites`/`citing`/`citation`, `re-cut` (either
      spelling) and `quote`/`quoted`; a span may use a hyphen, en dash or em dash.

      THREE STATED EXCLUSIONS, listed because each is a place a stale span can still sit
      — declaring them is the point, not hiding them: (1) lines containing the literal
      `[0-9]` (this pin's own regex sources); (2) date-shaped spans such as `2026-11`
      (`MEASURED 2026-11-03` is this file's house style, and a date is not a citation);
      (3) a citation phrased WITHOUT one of those tokens — e.g. "see `ci-wall-clock.md`
      lines 111-222" — which no lexical arm can see. That residual is why the sibling
      citation stays the liveness anchor: the canonical citation of this span is
      always token-bearing and always present.

    ⚠ The derivation arm and the two below it OVERLAP ON PURPOSE and none of the three may
    be deleted as redundant: the derivation arm is `origin/main`'s own independent pin
    (written after rounds eleven to sixteen above), and each catches a shape the others
    cannot — the derivation reds a span that is WIDE ENOUGH to contain the sentence but is
    not the sentence's own extent, naming the number to write, while the last two red a
    citation this file states ANYWHERE ELSE (reworded, appended to another docstring, or
    re-introduced under a synonym) that the derivation cannot see, because it reads the ONE
    coordinate in `tools/ci_check_repair.py`.

    Discriminating: shift a cited span, reword the sibling's phrasing away, widen the
    cited span while staying on the sentence, or state a different span on any
    token-bearing line of this file, and it reddens.
    """
    root = Path(__file__).resolve().parents[1]
    src = (root / "tools" / "ci_check_repair.py").read_text()
    own_src = Path(__file__).read_text()

    # 1. Every `ci_queue_health.py:<line>` cited — in the tool AND in this file — must
    #    BE the derived-value assignment. The test file's own coordinates were unchecked
    #    until round 14, which is how a bare "one line below, at `:NNN`" describing the
    #    wrong line survived three rounds while being wrong at every head. The BARE
    #    `:NNN` form is deliberately not matched here: a bare coordinate in this file may
    #    name a line in some OTHER file, and widening the arm would resolve it against
    #    `ci_queue_health.py` and red falsely (round 15's non-blocking P3).
    cited = sorted(set(re.findall(r"ci_queue_health\.py:(\d+)", src)))
    cited += sorted(set(re.findall(r"ci_queue_health\.py:(\d+)", own_src)))
    assert cited, "the citation block no longer cites ci_queue_health.py by line"
    health_lines = (root / "tools" / "ci_queue_health.py").read_text().splitlines()
    for number in cited:
        line = health_lines[int(number) - 1]
        assert line.startswith("DEFAULT_MAX_PR_WAIT_SECONDS = 90 * 60.0"), (
            f"tools/ci_queue_health.py:{number} is cited as the derived budget but reads "
            f"{line!r}")

    # 2. The `§6 (lines A-B)` span cited must contain the sentence it is quoted FOR.
    spans = re.findall(r"§6\s*\n#: \(lines (\d+)-(\d+)\)", src)
    assert len(spans) == 1, f"expected exactly one §6 line-span citation, got {spans}"
    first, last = (int(n) for n in spans[0])
    doc = (root / "docs" / "ops" / "ci-wall-clock.md").read_text().splitlines()
    quoted = " ".join(doc[first - 1:last])
    assert "81.9 min → 90 min" in quoted, (
        f"ci-wall-clock.md lines {first}-{last} are cited for the budget derivation but "
        f"do not contain it: {quoted!r}")
    assert doc[first - 1].strip().startswith("document's own §1 measurement") or (
        "derived from this" in quoted), (
        "the cited span must open on the sentence the docstring quotes")

    # 3. The cited span must be the span the QUOTED SENTENCE actually occupies, derived
    #    from the doc's own text — so a shift reds with the number to write instead of
    #    leaving the next lane to re-derive it. Arms (1)-(2) cannot tell a wide-enough span
    #    from the right one — the first version of this citation was four lines wide and
    #    passed them — and this is a span that any insert above §6 moves.
    opens = [i + 1 for i, line in enumerate(doc)
             if "The budget's default is derived from this" in line]
    closes = [i + 1 for i, line in enumerate(doc)
              if "measured-normal population rather than on it." in line]
    assert len(opens) == 1 and len(closes) == 1 and opens[0] <= closes[0], (
        f"the sentence this citation quotes is no longer locatable in ci-wall-clock.md §6 "
        f"(opening line {opens}, closing line {closes}) — re-quote the derivation the tool "
        f"cites rather than deleting the span assertion")
    assert (first, last) == (opens[0], closes[0]), (
        f"ci-wall-clock.md §6 moved: the sentence tools/ci_check_repair.py quotes now "
        f"occupies lines {opens[0]}-{closes[0]}, but the citation says {first}-{last}. "
        f"Change it to `(lines {opens[0]}-{closes[0]})`.")

    # 4. THIS test's own docstrings carry the third citation of the same span, and
    #    it is exactly what drifted when this PR grew `ci-wall-clock.md`:
    #    the other two citations were re-cut to the span of that head and this one was
    #    left behind
    #    (on a span that had become other prose entirely). "Pinned MECHANICALLY
    #    instead of corrected by hand a third time" has to include it, or the next
    #    insert re-arms the same silent drift (#10962 round 11, P3).
    #    EVERY occurrence and BOTH phrasings, deliberately: the first version of this
    #    pin checked only the first `quoted derivation is at` match, so a hand-re-cut
    #    of the sibling docstring 20 lines above ("§6 lines A-B records the same
    #    derivation") — or a second copy of the parenthetical — stayed GREEN while
    #    stale, which is the same false PASS this pin exists to prevent (round 12, P2).
    own_src = Path(__file__).read_text()
    sibling_hits = re.findall(
        r"§6 lines ([0-9]{1,4})-([0-9]{1,4}) records the same derivation", own_src)
    assert len(sibling_hits) == 1, (
        f"expected exactly ONE '§6 lines A-B records the same derivation' citation in "
        f"this file's docstrings, found {len(sibling_hits)} — ZERO means the citation "
        "moved to a phrasing this pin does not cover (round 13's P2: a phrasing that "
        "silently leaves the checked set is the same false PASS as a stale number), "
        "and more than one means a copy nothing examines")
    for found in sibling_hits:
        assert found == (str(first), str(last)), (
            f"a docstring in this file cites the derivation at {found[0]}-{found[1]} "
            f"while the file's own citation (and the pinned span above) says "
            f"{first}-{last} — a stale coordinate sends the next lane to the "
            "misreading the paragraph exists to prevent")

    # 5. PHRASING-AGNOSTIC backstop over the WHOLE FILE (round 14's P2, widened in
    #    round 15 after its P2: the first version read one function, so a new citation
    #    appended to the SIBLING docstring — the very site that drifted in round 11 —
    #    stayed green). Keyed on the CITATION token list defined in this function's
    #    docstring — that list is the DEFINITION of the scope, and the residual it cannot
    #    reach is declared there rather than claimed away (round 16's blocking P2).
    #    Spans accept a hyphen, en dash or em dash, because prose uses all three.
    CITATION_WORDS = re.compile(
        r"§\s*6|derivation|cit(?:e|ed|es|ing|ation)|re-?cut|quot(?:e|ed)")
    ANY_SPAN = re.compile(r"(?<!\d)([0-9]{1,4})[-\u2013\u2014]([0-9]{1,4})(?!\d)")

    def _is_date(start: str, end: str) -> bool:
        return len(start) == 4 and start.startswith(("19", "20")) and len(end) <= 2

    prose_spans = [
        match
        for line in own_src.splitlines()
        if "[0-9]" not in line and CITATION_WORDS.search(line)
        for match in ANY_SPAN.findall(line)
        if not _is_date(*match)
    ]
    for found in prose_spans:
        assert found == (str(first), str(last)), (
            f"a line of this file naming the citation (one of the tokens listed in this "
            f"function's docstring) states the span {found[0]}-{found[1]} while the "
            f"pinned span is {first}-{last} — a coordinate that has moved is stale "
            "here, and the token list is broad on purpose so a reworded citation "
            "cannot re-enter through a synonym")


def test_a_run_at_the_measured_median_queue_wait_is_not_cancelled():
    """The population pin in the direction that matters: 29.9 min, zero jobs.

    This is the measured p50 pre-start wait (the MAJORITY state at this age — 11 of 16
    `pending` runs sat at zero jobs at 11-22 min). Waiting is the repair.

    Discriminating: at the pre-fix floor this run is named, cancelled and re-run.
    """
    plan = R.classify(pr(), [], [aged_run(9, jobs=0, age_s=29.9 * 60)],
                      ["python-ci-gate"], EMITTERS, max_attempts=3)
    assert plan.no_verdict == [], "the median wait is not 'no verdict will arrive'"
    assert plan.rerun == [], "so nothing is destroyed to 'repair' it"
    assert "python-ci-gate" in plan.pending


def test_a_second_attempt_zero_job_run_is_named_and_never_cancelled():
    """⭐ P1 (review): the LOOP BOUND must be structural, not an abstention.

    `attempt_age_seconds` abstaining on a null `run_started_at` was described as the
    bound on cancel→rerun→cancel. MEASURED 2026-11-03 on this repo (run 41585740714:
    `run_attempt` 2, created 05:45:16Z, `run_started_at` 05:54:22Z, still `queued`),
    queued re-run attempts DO carry a stamped start, so they are ageable and the loop
    is reachable: attempt 2 (queued, zero jobs) → cancelled → attempt 3 → EXHAUSTED,
    i.e. exit 1 "a human is needed" for a run that was only waiting for a slot.

    A queued zero-job run at a LATER attempt IS the fresh attempt the repair produced,
    so it is NAMED and left to the queue — never cancelled, never escalated.

    Discriminating: pre-fix `plan.rerun == [run 2]` and, at `max_attempts=2`, the
    context is in `exhausted`.
    """
    from datetime import UTC, datetime
    run_ = aged_run(41480218593, jobs=0, age_s=6000.0, attempt=2)
    run_.started_at = datetime.fromtimestamp(
        R.time.time() - 5700.0, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")  # a LATER attempt
    plan = R.classify(pr(), [], [run_], ["python-ci-gate"], EMITTERS, max_attempts=2)
    assert [d["run"] for d in plan.no_verdict] == [41480218593], "it is still NAMED"
    assert plan.rerun == [], (
        "cancelling the attempt the previous window created is the loop, not a repair")
    assert plan.exhausted == [], (
        "a queue wait is not 'a repair this credential cannot perform' — the governor "
        "owns it and #9115 says nothing per-PR may act")


def test_a_backlogged_queue_names_the_zero_job_run_but_never_repairs_it():
    """⭐ P1 (review): the #9115 guard could not fire in the state the report cites.

    `starves_pr` is False for `PR-BACKLOGGED` BY DESIGN — it asserts an ABSENCE ("no
    PR-event run is executing"), which a queue that IS executing falsifies — so guarding
    the destructive act on `starves_pr` alone let a cancel through under
    `CI: OK(queued=60, pr_running=2, pr_queued=56)`, the very state this tool's own
    body names. The REPAIR question is congestion, which is the wider `delays_pr`,
    threaded as `queue_congested`.
    """
    runs = [aged_run(11, jobs=0, age_s=5700.0, path="ci.yml", name="CI")]
    plan = R.classify(pr(), [], runs, ["docs"], EMITTERS, max_attempts=3,
                      queue_starved=False, queue_congested=True)
    assert [d["run"] for d in plan.no_verdict] == [11], "it is still NAMED"
    assert plan.rerun == [], "a congested queue is repaired by NOTHING"
    assert plan.exhausted == []
    assert plan.starved == [], (
        "and it is NOT called starved — PR CI is executing, so naming it that would "
        "be a false statement in the instrument that exists to refuse them")


def test_a_MIXED_context_may_still_not_repair_under_a_congested_queue():
    """⭐ P1 (review): a COMPLETED sibling must not license the cancel the gate refuses.

    The refusal was conjunctive — `crowded and all(x.status in QUEUED_STATUSES for x in
    runs)` — so a context with one completed producing run and one queued zero-job run
    skipped it and landed in `plan.rerun`, i.e. a cancel was ISSUED while the queue
    reading said congested. That is reachable: two runs of ONE workflow at ONE head
    (`pull_request` + `workflow_dispatch` on the same sha, or a same-sha re-push) give
    exactly this shape.

    It matters most for the UNMEASURED pole, which is why the first case passes
    `queue_starved=False, queue_congested=True` — the value `main()` derives from
    `queue_congestion_gate` when nothing measured the queue (`--no-queue-health`, a
    failed read, `UNKNOWN`, `PR-UNWITNESSED`, a truncated `OK`). The documented promise
    is that silence never authorises a cancel, and the sibling's conclusion is not a
    measurement of the queue.

    Discriminating: pre-fix BOTH cases yield `plan.rerun == [12]`.
    """
    runs = [aged_run(11, status="completed", jobs=5, age_s=6000.0),
            aged_run(12, jobs=0, age_s=5700.0)]
    for label, starved, congested in (
            ("UNMEASURED (silence is not permission)", False, True),
            ("witnessed starvation", True, None)):
        plan = R.classify(pr(), [], runs, ["python-ci-gate"], EMITTERS,
                          max_attempts=3, queue_starved=starved,
                          queue_congested=congested)
        assert [d["run"] for d in plan.no_verdict] == [12], (
            f"[{label}] it is still NAMED — refusing to act is not refusing to report")
        assert plan.rerun == [], (
            f"[{label}] the harm is discarding THIS run's queue position; what its "
            "sibling concluded does not measure the queue")
        assert plan.exhausted == []

    # The other non-queued sibling shape: a producing run still EXECUTING at the head.
    executing = [aged_run(11, status="in_progress", jobs=3, age_s=6000.0),
                 aged_run(12, jobs=0, age_s=5700.0)]
    plan = R.classify(pr(), [], executing, ["python-ci-gate"], EMITTERS,
                      max_attempts=3, queue_starved=False, queue_congested=True)
    assert [d["run"] for d in plan.no_verdict] == [12]
    assert plan.rerun == [], (
        "UNMEASURED queue + a sibling still executing: still no cancel")


def test_an_unmeasured_queue_never_authorises_the_cancel(monkeypatch, capsys, tmp_path):
    """⭐ P1 (review): `bool(getattr(queue, "delays_pr", queue_starved))` read
    SILENCE AS PERMISSION on a DESTRUCTIVE act.

    `read_queue_health` returns `None` on ANY exception, and `delays_pr` is
    `False`-by-design for the unmeasured verdicts (`UNKNOWN`, `PR-UNWITNESSED`, a
    truncated `OK`). Folding either into the permissive branch meant the state where
    congestion was NEVER MEASURED — positively correlated with the load that makes a
    cancel harmful, since a queue read is refused under exactly that load — was the
    state that switched cancel-then-rerun ON: an aged attempt-1 zero-job run is
    cancelled and re-run, discarding its queue position and spending an attempt.

    `job_count` distinguishes `None` from `0` one function away precisely so an
    unreadable read can never authorise anything; the congestion sector of the same
    decision now answers three-valued too.

    Discriminating: pre-fix this summary shows `queue_congested: false` and run
    41480218593 in `rerun`.
    """
    monkeypatch.setattr(R, "read_queue_health", lambda repo: None)
    monkeypatch.setattr(R, "open_prs", lambda repo, include_drafts: [pr(1)])
    monkeypatch.setattr(R, "check_runs_at", lambda repo, head: [])
    monkeypatch.setattr(R, "runs_at_head", lambda repo, head: [
        aged_run(41480218593, jobs=0, age_s=5700.0, path="ci.yml", name="CI")])

    code = R.main(["--repo", "o/r", "--no-live", "--apply", "--json",
                   "--record", str(_flood_record(tmp_path))])
    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    # ⛔ THE WINDOW-LEVEL CONSEQUENCE — what the review asked to be asserted, not just
    # the presence of the key. `report()` returns 1 only for a repair this credential
    # could not perform, so a suppressed cancel used to leave exit 0: that is what
    # `workflow_state()` reads as `covered`, which makes the local fallback exit idle
    # and no notification fire. An UNMEASURED reading never clears by itself (nothing
    # in the next window measures that queue either), so exit 0 here means the PR is
    # skipped silently every 30 minutes — the co-existence
    # `docs/ci/ci-check-repair-red-surface.md` §Fact 2 declares unsound.
    assert code == 1, (
        "a refusal under SILENCE is not a discharged window: exit 0 is the 'covered' "
        "the local rail consumes, so the pass would report a clean window forever")
    assert "REFUSED-REPAIR" in captured.err, (
        "the notice must reach the --json callers (BOTH production invocations pass "
        "--json), or the refusal reaches nobody")
    assert "REFUSED-REPAIR" not in captured.out, (
        "...and never into the --json stream: that would break json.load in "
        "tools/ci-check-repair-local.sh and the workflow's step summary")
    assert [d["run"] for d in summary["prs"][0]["no_verdict"]] == [41480218593], \
        "it is still NAMED — refusing to act is not refusing to report"
    assert summary["queue_congested"] is True, (
        "an unmeasured queue must reach the gate as REFUSED, not as 'clear'")
    assert summary["queue_congestion_measured"] is False, (
        "the report must not claim a congestion reading it never took")
    assert summary["prs"][0]["rerun"] == [], (
        "silence is not permission: no cancel may be authorised by a queue nobody "
        "read")
    # ⭐ P2 (review): the refusal must be VISIBLE in the aggregate, not only inside a
    # per-PR record. `needing_repair` counts neither `zero_job_named` nor a suppressed
    # repair, so on this fixture it is 0 — and 0 is what the local rail quotes as a
    # clean, covered window.
    assert summary["needing_repair"] == 0, "the fixture must be the understating shape"
    assert summary["zero_job_named"] == 1, (
        "a plan carrying a NAMED zero-job context must be counted — the key counts "
        "what was named, not what is stranded (#10641 review P2: it was called "
        "`no_verdict` and documented as 'named-but-unrepairable', which a later "
        "successful repair in the same pass makes false)")
    assert summary["repair_refused"] == 1, (
        "the run the congestion gate suppressed must be counted beside "
        "needing_repair, or `needing_repair=0` reads as a clean window")
    assert [d["run"] for d in summary["prs"][0]["repair_refused"]] == [41480218593], (
        "the record names the run that was identified and NOT acted on")
    assert [d["reason"] for d in summary["prs"][0]["repair_refused"]] == [
        "queue_congested=True (UNMEASURED — the gate refuses silence, nothing was "
        "read)"], (
        "...and the gate that refused it, WITH the witness for that `True`: this "
        "fixture's queue was never read, so a bare `queue_congested=True` here would "
        "state as a measured fact what the summary reports as unmeasured (#10641 "
        "review P2)")
    assert [d["measured"] for d in summary["prs"][0]["repair_refused"]] == [False], (
        "the structured witness, not only the rendered reason")
    # ⭐ The exit-1 REMEDY must name the cause that produced it. `--json` now carries
    # the credential sentence out of the `else:` branch (#10766, merged from main),
    # and it used to fire on ANY non-zero exit — including this one, whose cause is a
    # queue nobody read. Naming a credential here sends the reader to hunt
    # permissions for a jam, which is the misdiagnosis this whole gate exists to end
    # (`ci_queue_health.py` is the remedy, and the local rail already branches its
    # own text for it). Discriminating: pre-guard this fixture prints it.
    assert "credential that can trigger events" not in captured.err, (
        "a QUEUE refusal must not be reported as a credential failure")
    assert "REFUSED-REPAIR" in captured.err, (
        "...and the cause that did produce the exit must still be named")
    # ⭐ The consequence sentence must agree with the exit code in the SAME run
    # (#10641 review P2). This pole exits 1, so "NOT discharged" is true here — and
    # asserting it is what makes the measured pole's assertion discriminating rather
    # than a text match on either wording.
    assert "This window is NOT discharged" in captured.err, (
        "an UNMEASURED refusal is exactly the window that stays open")
    assert "self-clearing" not in captured.err, (
        "a queue nobody read is not a measured congestion that clears itself")


def test_the_local_rail_reads_the_refused_count_into_its_signature(tmp_path):
    """P2 (review): the refusal was emitted but the ONE consumer never read it.

    `tools/ci-check-repair-local.sh` decided its transition with
    `signature="local:$rc:$needing"` and `needing_repair_count()` reading only
    `['needing_repair']`. A refused zero-job cancel appears in NEITHER (it is
    deliberately not a repair the pass will attempt), so `needing_repair=0,
    repair_refused=3` produced the SAME signature as a clean, covered window: no
    transition, no notification, and a fail-shut the operator could not see.

    Executed, not grepped: the extraction is run against the artifact the tool
    actually writes, so a shell quoting bug reddens this too.
    """
    launcher_path = Path(__file__).resolve().parents[1] / "tools/ci-check-repair-local.sh"
    launcher = launcher_path.read_text()
    assert 'signature="local:$rc:$needing:$refused"' in launcher, (
        "the transition signature must carry the refusal, or it reads as a clean window")
    assert "Refused zero-job repairs: ${refused}" in launcher, (
        "and the notification must name it, or the transition says nothing useful")
    # ⛔ P3 (review, rail-C): exit 1 gained a SECOND cause — a cancel refused because
    # the queue reading did not certify it clear — and the single notification still
    # said "even the local credential could not complete", pointing the operator at the
    # credential when the remedy is to read the queue. Discriminating: pre-fix this
    # branch test is absent and both causes share one message.
    assert '[ "$refused" != "0" ]' in launcher, (
        "the exit-1 notification must branch on the refusal count")

    out = tmp_path / "out.json"
    out.write_text(json.dumps({"needing_repair": 0, "repair_refused": 3}))
    fn = re.search(r"^summary_count\(\) \{.*?^\}$", launcher, re.S | re.M)
    assert fn, "the shared extractor must still exist for both counts"
    script = (
        f"PYTHON_BIN={sys.executable}\nOUT_FILE={out}\n{fn.group(0)}\n"
        "summary_count repair_refused\n"
    )
    ran = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.strip() == "3", (
        "the rail must read the key the tool emits, not a lookalike")

    # An unreadable artifact still prints `?`, never 0 — the same rule the
    # `needing_repair` reader already had (an unreadable report is not "nothing wrong").
    broken = tmp_path / "broken.json"
    broken.write_text("not json")
    script = (
        f"PYTHON_BIN={sys.executable}\nOUT_FILE={broken}\n{fn.group(0)}\n"
        "summary_count repair_refused\n"
    )
    ran = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert ran.stdout.strip() == "?", "an unreadable report must never print as 0"


def test_the_exit_one_notification_names_the_pole_the_reading_measured(tmp_path):
    """P2 (review): `repair_refused` is non-zero on BOTH queue poles.

    `main()` returns 1 only for the UNMEASURED pole (plus the credential-only
    repairs), but the rail branched on `[ "$refused" != "0" ]` **alone**:

    * a window whose queue WAS read and found congested was told the reading
      "did not certify it clear" and sent to `ci_queue_health.py` for an answer it
      had already given ("I read it: congested, and this clears itself"), and
    * the same message said "rather than blaming the credential" — while a
      `denied` cancel is a `rerun[]` entry, so it lands in `needing_repair` and
      escalates independently. A window can refuse zero-job runs AND need a
      credential, and this sentence ruled the credential out of it.

    Executed, one case per pole: the sentence is composed by the rail's own
    `exit_one_cause`, so a shell quoting bug reddens this too. Pre-fix the
    extractor finds no such function — both poles share one message.
    """
    launcher = (Path(__file__).resolve().parents[1]
                / "tools/ci-check-repair-local.sh").read_text()
    # …and the witness is READ from the artifact, never assumed from a count.
    assert "summary_count 'queue_congestion_measured'" in launcher, (
        "the rail must read the tool's own measured/unmeasured witness")
    fn = re.search(r"^exit_one_cause\(\) \{.*?^\}$", launcher, re.S | re.M)
    assert fn, (
        "the exit-1 cause must be a function the suite can EXECUTE, one case per pole")

    def cause(needing, refused, measured):
        ran = subprocess.run(
            ["bash", "-c",
             f"{fn.group(0)}\nexit_one_cause '{needing}' '{refused}' '{measured}'\n"],
            capture_output=True, text=True)
        assert ran.returncode == 0, ran.stderr
        return ran.stdout

    # THE MEASURED POLE — the queue was read; the suppression self-clears and no
    # credential or manual unjam is involved.
    measured_true = cause(0, 3, "True")
    assert "WAS read and is congested" in measured_true
    assert "did not certify it clear" not in measured_true, (
        "a measured congestion must not be reported as an unread queue")
    assert "blaming the credential" not in measured_true

    # THE UNMEASURED POLE — silence, a failed read, or an unreadable artifact
    # (`?`) keeps today's text: nothing measured, so read the queue.
    for pole in ("False", "None", "?"):
        unmeasured = cause(0, 3, pole)
        assert "did not certify it clear" in unmeasured, pole
        assert "WAS read and is congested" not in unmeasured, pole

    # BOTH CAUSES IN ONE WINDOW: the refusal is measured AND a `denied`/`failed`
    # cancel needs the credential, so the credential sentence stays reachable
    # instead of being denied by the queue sentence.
    both = cause(2, 3, "True")
    assert "WAS read and is congested" in both
    assert "2 PR(s) still need a repair this pass did not complete" in both
    assert "user credential can perform" in both

    # THE CREDENTIAL-ONLY POLE is unchanged (no refusal to explain).
    credential_only = cause(1, 0, "False")
    assert "even the local credential could not complete" in credential_only
    assert "zero-job cancel was REFUSED" not in credential_only


# ── the congestion gate's two poles, built by the module's OWN core ──────────
#
# ⛔ NEITHER POLE MAY BE A HAND-BUILT `QueueHealth` (#10641 review P2). Both were,
# and the "measured clear" one could not be true of any repo: it declared
# `pr_queued=1, oldest=60.0` while the scan was fed a `pending` PR run at 5700 s —
# with that run present the oldest wait IS 5700 s and it sits in `pr_pending`, so
# `classify_queue` answers `PR-STARVED`. A hand-built reading proves only that the
# gate passes a pre-computed bool through; it cannot show the CLASS is reachable,
# which is the one thing these fixtures exist to pin. Both now go through
# `classify_queue`, so a change to the age clauses reddens them here.

def _queue_run(rid, age_s=5700.0, event="pull_request", name="Python CI"):
    from datetime import UTC, datetime, timedelta

    from tools import ci_queue_health as Q

    return Q.QueuedRun(
        id=rid, name=name, event=event,
        created_at=(datetime.now(UTC) - timedelta(seconds=age_s)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"))


def _queue_reading(queued=(), in_progress=(), pending=()):
    from tools import ci_queue_health as Q

    return Q.classify_queue(list(queued), list(in_progress), None,
                            pending=list(pending))


def test_a_MEASURED_congested_refusal_does_not_escalate(monkeypatch, capsys, tmp_path):
    """The other pole of the escalation, so the fix is not "exit 1 on any refusal".

    A refusal made on a MEASURED congestion is the correct, self-clearing outcome: the
    queue was read, it is congested, so re-queueing the run at the back of the same
    wait is exactly the harm the gate exists to prevent — and the next window, when
    the queue has moved, is when the repair should fire. Escalating here would turn a
    healthy-but-busy repo into a red repair workflow plus a notification every 30
    minutes, which trains the reader to ignore the one signal that matters.

    ⛔ AND THIS POLE MUST STAY A REFUSAL ONCE THE SELF-WITNESS EXEMPTION EXISTS. The
    congestion here is carried by ANOTHER old wait as well as the target, so
    `congests_pr_apart_from(target)` is `True` — the queue is busy with work that is
    not the run under repair, and cancel-then-rerun would discard a queue position
    behind a wait that only drains. Discriminating: making the exemption fire on
    `congests_pr is True` alone reddens this test.
    """
    from tools import ci_queue_health as Q

    congested = _queue_reading(
        queued=[_queue_run(700001, age_s=9000.0)],
        in_progress=[_queue_run(700002, age_s=60.0)],
        pending=[_queue_run(41480218593, age_s=5700.0)])
    assert congested.verdict == Q.PR_BACKLOGGED, congested.reasons
    assert congested.congests_pr is True, "the fixture must be a MEASURED congestion"
    assert congested.starves_pr is False, (
        "PR CI IS executing — the fixture must not be a starvation reading as well")
    assert congested.congests_pr_apart_from(41480218593) is True, (
        "the OTHER old wait congests the queue independently of the target")

    monkeypatch.setattr(R, "read_queue_health", lambda repo: congested)
    monkeypatch.setattr(R, "open_prs", lambda repo, include_drafts: [pr(1)])
    monkeypatch.setattr(R, "check_runs_at", lambda repo, head: [])
    monkeypatch.setattr(R, "runs_at_head", lambda repo, head: [
        aged_run(41480218593, jobs=0, age_s=5700.0, path="ci.yml", name="CI")])

    assert R.main(["--repo", "o/r", "--no-live", "--apply", "--json",
                   "--record", str(_flood_record(tmp_path))]) == 0, (
        "a refusal the next window can clear on its own is not an unresolved window")
    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert summary["queue_congestion_measured"] is True
    assert summary["repair_refused"] == 1
    assert summary["self_witness_exempted"] == 0, (
        "the exemption must NOT fire when another old wait witnesses the congestion")
    assert summary["prs"][0]["rerun"] == [], "the cancel is still suppressed"
    assert summary["prs"][0]["self_witness_exempted"] == []
    assert [d["measured"] for d in summary["prs"][0]["repair_refused"]] == [True], (
        "the witness for the OTHER pole: this refusal came from a congestion that "
        "was actually READ (#10641 review P2)")
    assert [d["reason"] for d in summary["prs"][0]["repair_refused"]] == [
        "queue_congested=True (measured)"], (
        "...and the per-run record says so, so the granular record and the summary "
        "cannot describe different readings")
    assert "REFUSED-REPAIR" in captured.err, "...and it is still NAMED"
    # ⛔ AND IT MUST NOT CONTRADICT THE EXIT CODE IT PRINTS BESIDE (#10641 review P2).
    # This fixture exits 0 by design (the suppression is the correct, self-clearing
    # outcome), yet the notice said "This window is NOT discharged" and offered a
    # credential remedy for a queue that HAD been read and needed only to drain.
    # clig.dev's rule is the external form: exit codes are how the consumer decides
    # succeeded/failed, so a diagnostic must not state the opposite of the code.
    # Discriminating: pre-fix `"NOT discharged" in stderr` is True and this reddens.
    assert "This window is NOT discharged" not in captured.err, (
        "exit 0 IS the discharge: the notice must not say the window is still open")
    assert "self-clearing" in captured.err, (
        "the measured pole must say what is actually true — the refusal clears itself "
        "when the queue drains, with no credential and no manual unjam")


def test_the_SOLE_witness_target_is_REPAIRED_not_stranded_forever(
        monkeypatch, capsys, tmp_path):
    """⛔ P1 (review): the cancel-then-rerun repair was UNREACHABLE for its own class.

    A zero-job run is stuck ABOVE the queue but is still IN the wait population, and
    its own repair floor (`ZERO_JOB_MIN_AGE_S` = 5400 s) is at or above every threshold
    the age clause compares against. So every reading that contained a repairable run
    witnessed a congestion, `congests_pr` answered `True`, and the gate refused the one
    act that would remove the witness. `main()` keeps exit 0 on a MEASURED congestion,
    `.github/workflows/ci-check-repair.yml` concludes `success`, `workflow_state()`
    maps that to `covered` and `tools/ci-check-repair-local.sh` goes idle — `exit 0`
    beside a required check that can never be produced, which
    `docs/ci/ci-check-repair-red-surface.md` §Fact 2 declares unsound.

    The fixture is the module's OWN core answering, with the target present, and it
    composes the three layers the review asked to be pinned together:
    `classify_queue` → `congests_pr_apart_from` → `classify()`. Measured on the pre-fix
    head (pure core, no API, no venv): `verdict=PR-STARVED oldest=5700.0
    congests_pr=True`, `repair_refused=[41480218593]`, `rerun=[]`.

    The refusal is still CORRECT one shape over: the sibling test above carries a
    second old wait and there the cancel stays suppressed — so this is not "always
    exempt".
    """
    from tools import ci_queue_health as Q

    target = 41480218593
    reading = _queue_reading(pending=[_queue_run(target, age_s=5700.0)])
    assert reading.verdict == Q.PR_STARVED, reading.reasons
    assert reading.congests_pr is True, (
        "the queue IS congested — the question is BY WHAT")
    assert reading.wait_witness_ids == (target,), (
        "the reading must name the run its verdict is witnessed by")
    assert reading.congests_pr_apart_from(target) is False, (
        "nothing other than the target witnesses this congestion, so refusing it is "
        "self-referential")

    # THE UNIT SEAM FIRST: the same classify() call WITHOUT the reading's answer still
    # refuses, so the exemption is provably what changed, not something else in main().
    refused = R.classify(pr(), [], [aged_run(target, jobs=0, age_s=5700.0,
                                             path="ci.yml", name="CI")],
                         ["docs"], EMITTERS, max_attempts=3,
                         queue_starved=reading.starves_pr,
                         queue_congested=R.queue_congestion_gate(reading.congests_pr),
                         queue_congestion_measured=True)
    assert [d["run"] for d in refused.repair_refused] == [target]
    assert refused.rerun == [] and refused.self_witness == []

    plan = R.classify(pr(), [], [aged_run(target, jobs=0, age_s=5700.0,
                                          path="ci.yml", name="CI")],
                      ["docs"], EMITTERS, max_attempts=3,
                      queue_starved=reading.starves_pr,
                      queue_congested=R.queue_congestion_gate(reading.congests_pr),
                      queue_congestion_measured=True,
                      queue_congestion_apart_from=reading.congests_pr_apart_from)
    assert [r.id for r in plan.rerun] == [target], (
        "the repair the module exists for must be reachable for its own target class")
    assert plan.repair_refused == []
    assert [r.id for r in plan.self_witness] == [target], (
        "and the pass records WHY it acted while the reading said congested")

    # …then END TO END, so the exit code, the summary and the record agree.
    monkeypatch.setattr(R, "read_queue_health", lambda repo: reading)
    monkeypatch.setattr(R, "open_prs", lambda repo, include_drafts: [pr(1)])
    monkeypatch.setattr(R, "check_runs_at", lambda repo, head: [])
    monkeypatch.setattr(R, "runs_at_head", lambda repo, head: [
        aged_run(target, jobs=0, age_s=5700.0, path="ci.yml", name="CI")])

    # ⛔ `--apply` IS PART OF THIS TEST (#10641 review P2). Without it `report()` takes
    # the report-only branch, records `{"ok": None}` and NEVER CALLS `_rerun` — so the
    # test passed while proving nothing about the apply path it names. Stubbed so the
    # case stays hermetic; what is asserted is the exit code and the record the apply
    # path actually produces.
    rerun_calls = []
    monkeypatch.setattr(R, "_rerun", lambda *a, **k: rerun_calls.append(a[1].id) or "rerun")

    assert R.main(["--repo", "o/r", "--no-live", "--apply", "--json",
                   "--record", str(_flood_record(tmp_path))]) == 0
    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert summary["queue_congested"] is True, "the reading is still reported as read"
    assert summary["queue_congestion_measured"] is True
    assert rerun_calls == [target], (
        "the exemption must reach the ACT, not stop at the gate")
    assert summary["self_witness_exempted"] == 1
    assert summary["repair_refused"] == 0, "nothing was stranded this window"
    assert [r["run"] for r in summary["prs"][0]["rerun"]] == [target]
    assert summary["prs"][0]["self_witness_exempted"] == [target]
    assert "REFUSED-REPAIR" not in captured.err, (
        "nothing was refused, so the notice must not fire")
    assert summary["zero_job_named"] == 1, (
        "it was named 'no verdict will arrive' and then repaired — the count describes "
        "the NAMING, not the outcome")

    # ⛔ ⛔ THE TWO SURFACES THAT DESCRIBE A STARVED WINDOW MUST NOT DISAGREE WITH THE
    # REPAIR (#10641 review P2). The exemption makes this context `starved` AND
    # `rerun` on the same pass — the run whose presence witnessed the starvation is
    # the one it cancelled — and neither surface said so: the record read
    # `starved: ["docs"]` beside `rerun: [41480218593]`, and the STARVED remedy line
    # told the operator "No per-PR repair can clear this", which is false for exactly
    # this context. Discriminating: `starved_repaired` does not exist pre-fix
    # (KeyError on the record) and the bare denial is what the pre-fix line printed.
    # The MEASUREMENT is kept, because issuing a rerun does not un-take it — the
    # replacement attempt is queued too — so the record now says which measure was
    # taken and which act was performed, one key each.
    assert summary["prs"][0]["starved"] == ["docs"], (
        "the starvation was MEASURED and stays reported: the fresh attempt is queued "
        "too, so the queue reading is still starved at this window")
    assert summary["prs"][0]["starved_repaired"] == ["docs"], (
        "and the record names the context the exemption actually repaired")
    assert "ISSUED the cancel-then-rerun" in captured.err, (
        "the remedy line must name the repair this pass performed for the starved "
        "context, instead of denying that any per-PR repair exists")
    assert "No per-PR repair can clear this" not in captured.err, (
        "pre-fix this was the whole remedy on this window — the denial of the act "
        "that had just landed")
    assert "ci_queue_health.py" in captured.err, (
        "the unjam is NOT withheld in the repaired pole: the queue itself is still "
        "starved and the fresh attempt is queued behind it")


def test_a_starved_context_the_exemption_repaired_keeps_the_remnant_remedy(
        monkeypatch, capsys, tmp_path):
    """⛔ P2 (review): the MIXED window — one starved context repaired, one not.

    The remedy line now has three shapes, and the two-context window is the one that
    can silently be wrong: `docs`' only witness is a repairable zero-job run (so the
    exemption cancels and re-runs it), while `python-ci-gate` is queued behind a run
    that holds jobs and no per-PR act can start it. Saying only "the repair was
    issued" would hide the context that still needs the queue unjammed; saying only
    "no per-PR repair can clear this" is the pre-fix denial of the repair that WAS
    issued. The line must carry both halves, and the per-PR record must name which
    context is which.

    `python-ci-gate`'s run is a `workflow_dispatch` run on purpose: it is QUEUED at
    the head (so the context really is starved by `classify`'s own predicate) without
    being a PR-event run, so the repo-level PR reading is still witnessed by the
    `docs` target alone and the exemption is genuinely reachable beside it.
    """
    target = 41480218593
    reading = _queue_reading(pending=[_queue_run(target, age_s=5700.0)])
    assert reading.wait_witness_ids == (target,)
    assert reading.congests_pr_apart_from(target) is False

    monkeypatch.setattr(R, "read_queue_health", lambda repo: reading)
    monkeypatch.setattr(R, "open_prs", lambda repo, include_drafts: [pr(1)])
    monkeypatch.setattr(R, "check_runs_at", lambda repo, head: [])
    monkeypatch.setattr(R, "runs_at_head", lambda repo, head: [
        aged_run(target, jobs=0, age_s=5700.0, path="ci.yml", name="CI"),
        aged_run(778899, jobs=2, age_s=600.0, path="python-ci.yml",
                 name="Python CI", event="workflow_dispatch")])
    monkeypatch.setattr(R, "_rerun", lambda *a, **k: "rerun")

    rec = tmp_path / "required-contexts.json"
    rec.write_text(json.dumps({
        "required_contexts": ["docs", "python-ci-gate"],
        "emitters": {"docs": [["ci.yml", "docs"]],
                     "python-ci-gate": [["python-ci.yml", "python-ci-gate"]]},
    }))

    assert R.main(["--repo", "o/r", "--no-live", "--apply", "--json",
                   "--record", str(rec)]) == 0
    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert summary["prs"][0]["starved"] == ["docs", "python-ci-gate"], (
        "both contexts are starved by the module's own predicate")
    assert summary["prs"][0]["starved_repaired"] == ["docs"], (
        "only the context whose every queued run crossed the gate was repaired")
    assert [d["run"] for d in summary["prs"][0]["rerun"]] == [target], (
        "the repaired context's run is the one that was actually cancelled and re-run")
    assert "this pass ISSUED the cancel-then-rerun above" in captured.err, (
        "the mixed pole must name the repair it issued")
    assert "for the remaining 1" in captured.err, (
        "…and must not let it mask the context no per-PR repair can clear")
    assert "ci_queue_health.py" in captured.err, "…and still names the unjam remedy"


def _starved_target_reading():
    """The reading this PR's exemption exists for: one lone aged `pending` PR run."""
    from tools import ci_queue_health as Q

    reading = _queue_reading(pending=[_queue_run(41480218593, age_s=5700.0)])
    assert reading.verdict == Q.PR_STARVED, reading.reasons
    assert reading.congests_pr_apart_from(41480218593) is False
    return reading


def test_a_REPORT_ONLY_pass_must_not_say_it_ISSUED_the_starved_repair(
        monkeypatch, capsys, tmp_path):
    """⛔ P2 (review): the STARVED remedy stated an ACT from a DECISION.

    `starved_repaired` is appended in `classify()` — a PURE function that runs before
    `report()` and knows nothing of `_rerun`. `report()`'s apply branch is the only
    place the act is attempted, and in a report-only pass it is never entered at all
    (`rec["rerun"]` holds `{"ok": None}`). The remedy nevertheless read "this pass
    ISSUED the cancel-then-rerun", and `tools/ci-check-repair-local.sh`'s `DRY=1`
    passes no `--apply` — so the documented dry run told the operator the repair had
    landed, in the direction that leaves the PR stuck.

    Discriminating: pre-fix this fixture prints "ISSUED the cancel-then-rerun" and has
    no `starved_repaired_outcome` key at all (KeyError), so both assertions below red.
    The `--apply` sibling test above pins the opposite pole, so this is not
    "never claim an act".
    """
    target = 41480218593
    reading = _starved_target_reading()

    monkeypatch.setattr(R, "read_queue_health", lambda repo: reading)
    monkeypatch.setattr(R, "open_prs", lambda repo, include_drafts: [pr(1)])
    monkeypatch.setattr(R, "check_runs_at", lambda repo, head: [])
    monkeypatch.setattr(R, "runs_at_head", lambda repo, head: [
        aged_run(target, jobs=0, age_s=5700.0, path="ci.yml", name="CI")])
    rerun_calls = []
    monkeypatch.setattr(R, "_rerun", lambda *a, **k: rerun_calls.append(a[1].id) or "rerun")

    # NO `--apply`: the documented dry run.
    assert R.main(["--repo", "o/r", "--no-live", "--json",
                   "--record", str(_flood_record(tmp_path))]) == 0
    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert summary["apply"] is False
    assert rerun_calls == [], "nothing is attempted without --apply — that is the point"
    assert [r["ok"] for r in summary["prs"][0]["rerun"]] == [None], (
        "the record itself says the act was never attempted")
    assert summary["prs"][0]["starved_repaired"] == ["docs"], (
        "the DECISION still stands — the exemption covers this context")
    assert summary["prs"][0]["starved_repaired_outcome"] == {
        "docs": R.STARVED_OUTCOME_NOT_ATTEMPTED}, (
        "…and the OUTCOME is its own key: covered, not attempted")
    assert "ISSUED the cancel-then-rerun" not in captured.err, (
        "pre-fix the dry run claimed the repair was issued")
    assert "WAS issued" not in captured.err, "…in either of its two pre-fix wordings"
    assert "Nothing was ATTEMPTED in this report-only pass" in captured.err, (
        "the sentence must be the one `repair_refused` already uses one block above")
    assert "ci_queue_health.py" in captured.err, (
        "the unjam remedy is not withheld just because nothing was attempted")


def test_an_APPLY_pass_whose_rerun_DEFERRED_must_not_say_it_ISSUED(
        monkeypatch, capsys, tmp_path):
    """⛔ P2 (review), the APPLY pole: attempted is not issued.

    In apply mode the act is attempted, but `_rerun` answers `deferred` whenever the
    cancel could not be sent (no budget, `gh` could not start, an unclassifiable
    refusal). `report()` records exactly that — `{"ok": False, "deferred": True}` —
    and the remedy ignored it, still claiming ISSUED. An operator reading the durable
    log is told a jammed PR was repaired when nothing was sent.

    Discriminating: pre-fix this prints "ISSUED the cancel-then-rerun"; the outcome
    key does not exist.
    """
    target = 41480218593
    reading = _starved_target_reading()

    monkeypatch.setattr(R, "read_queue_health", lambda repo: reading)
    monkeypatch.setattr(R, "open_prs", lambda repo, include_drafts: [pr(1)])
    monkeypatch.setattr(R, "check_runs_at", lambda repo, head: [])
    monkeypatch.setattr(R, "runs_at_head", lambda repo, head: [
        aged_run(target, jobs=0, age_s=5700.0, path="ci.yml", name="CI")])
    monkeypatch.setattr(R, "_rerun", lambda *a, **k: "deferred")

    assert R.main(["--repo", "o/r", "--no-live", "--apply", "--json",
                   "--record", str(_flood_record(tmp_path))]) == 0, (
        "a deferral is a SKIP, not a failure: the next window acts")
    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert [r.get("deferred") for r in summary["prs"][0]["rerun"]] == [True], (
        "the record says the act was deferred, not performed")
    assert summary["prs"][0]["starved_repaired_outcome"] == {
        "docs": R.STARVED_OUTCOME_NOT_ISSUED}
    assert "ISSUED the cancel-then-rerun" not in captured.err, (
        "pre-fix a deferred attempt was reported as an issued repair")
    assert "was NOT issued" in captured.err, (
        "the sentence must name the deferral as the absence of the act")
    assert "see `rerun[]` for which" in captured.err, (
        "…and point at the record that says WHY (deferred / head_moved / denied)")


def test_a_cancel_ACCEPTED_but_UNOBSERVED_is_named_as_itself(
        monkeypatch, capsys, tmp_path):
    """⛔ P2 (review): the middle pole — a destructive act REQUESTED, `ok` False.

    `cancel_unobserved` means the cancel POST was accepted and its settle was never
    observed: `ok is False` (nothing is claimed as repaired) with `cancel_accepted`
    set. "Issued the cancel-then-rerun" is false (no replacement was issued) and
    "nothing happened" is equally false (a cancel may have landed), so the sentence
    must be this pole's own. Discriminating: pre-fix prints "ISSUED".
    """
    target = 41480218593
    reading = _starved_target_reading()

    monkeypatch.setattr(R, "read_queue_health", lambda repo: reading)
    monkeypatch.setattr(R, "open_prs", lambda repo, include_drafts: [pr(1)])
    monkeypatch.setattr(R, "check_runs_at", lambda repo, head: [])
    monkeypatch.setattr(R, "runs_at_head", lambda repo, head: [
        aged_run(target, jobs=0, age_s=5700.0, path="ci.yml", name="CI")])
    monkeypatch.setattr(R, "_rerun", lambda *a, **k: "cancel_unobserved")

    assert R.main(["--repo", "o/r", "--no-live", "--apply", "--json",
                   "--record", str(_flood_record(tmp_path))]) == 0
    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert summary["prs"][0]["rerun"][0]["cancel_accepted"] is True
    assert summary["prs"][0]["starved_repaired_outcome"] == {
        "docs": R.STARVED_OUTCOME_CANCEL_ACCEPTED}
    assert "ISSUED the cancel-then-rerun" not in captured.err
    assert "the cancel was ACCEPTED but its effect was never observed" in captured.err, (
        "an accepted-but-unobserved cancel is its own claim")
    assert "no replacement was issued" in captured.err, (
        "…and must not be read as a completed repair")


def test_a_REFUSED_cancel_is_named_a_DIRECT_rerun_not_a_cancel_then_rerun(
        monkeypatch, capsys, tmp_path):
    """⛔ P2 (review): the 409 pole — a re-run WAS issued, but no cancel-then-rerun was.

    `rerun_not_queued` is the API's 409 for the orphan shape: the run was never
    queued, so there is nothing to cancel first and `_rerun` issues the DIRECT
    re-run. `report()` records `cancel_refused_409` so the entry "never reads as a
    clean cancel-then-rerun" — its own comment in `report()` — and
    `STARVED_OUTCOME_RERUN_WITHOUT_CANCEL` is the pole that keeps the sentence
    honest about which of the two acts happened.

    This pole was implemented but UNPINNED: deleting the `cancel_refused_409` test in
    `_starved_repair_outcomes` left every test green, so the sentence would silently
    fall back to claiming the cancel. Discriminating: with that test removed this
    reddens on the outcome assertion and on "REFUSED the cancel".
    """
    target = 41480218593
    reading = _starved_target_reading()

    monkeypatch.setattr(R, "read_queue_health", lambda repo: reading)
    monkeypatch.setattr(R, "open_prs", lambda repo, include_drafts: [pr(1)])
    monkeypatch.setattr(R, "check_runs_at", lambda repo, head: [])
    monkeypatch.setattr(R, "runs_at_head", lambda repo, head: [
        aged_run(target, jobs=0, age_s=5700.0, path="ci.yml", name="CI")])
    monkeypatch.setattr(R, "_rerun", lambda *a, **k: "rerun_not_queued")

    assert R.main(["--repo", "o/r", "--no-live", "--apply", "--json",
                   "--record", str(_flood_record(tmp_path))]) == 0
    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert summary["prs"][0]["rerun"][0]["ok"] is True, (
        "the direct re-run WAS issued, so the entry is a success")
    assert summary["prs"][0]["rerun"][0]["cancel_refused_409"] is True, (
        "…and the record names the refused cancel as the reason there was no "
        "cancel-then-rerun")
    assert summary["prs"][0]["starved_repaired_outcome"] == {
        "docs": R.STARVED_OUTCOME_RERUN_WITHOUT_CANCEL}
    assert "ISSUED the cancel-then-rerun" not in captured.err, (
        "the one claim this pole exists to prevent")
    assert "REFUSED the cancel and this pass issued the DIRECT re-run instead" \
        in captured.err, "the sentence must name the act that actually happened"


def test_a_cancel_whose_ANSWER_WAS_LOST_is_named_as_itself(
        monkeypatch, capsys, tmp_path):
    """⛔ P2 (review): the `cancel_unknown` pole — the POST was sent, the answer lost.

    `_rerun`'s docstring is explicit that this is neither `cancel_unobserved` (which
    claims the cancel was ACCEPTED) nor a deferral (which claims nothing was
    changed): the run MAY be cancelled. `STARVED_OUTCOME_CANCEL_EFFECT_UNKNOWN` keeps
    that distinction in the operator-facing sentence.

    This pole was implemented but UNPINNED: deleting its branch in
    `_starved_repair_outcomes` silently downgrades the context to `not_issued` ("the
    attempt was deferred or skipped"), which asserts nothing changed — the
    understatement the pole exists to remove. Discriminating: with that branch removed
    this reddens on the outcome assertion and on "ANSWER was lost".
    """
    target = 41480218593
    reading = _starved_target_reading()

    monkeypatch.setattr(R, "read_queue_health", lambda repo: reading)
    monkeypatch.setattr(R, "open_prs", lambda repo, include_drafts: [pr(1)])
    monkeypatch.setattr(R, "check_runs_at", lambda repo, head: [])
    monkeypatch.setattr(R, "runs_at_head", lambda repo, head: [
        aged_run(target, jobs=0, age_s=5700.0, path="ci.yml", name="CI")])
    monkeypatch.setattr(R, "_rerun", lambda *a, **k: "cancel_unknown")

    assert R.main(["--repo", "o/r", "--no-live", "--apply", "--json",
                   "--record", str(_flood_record(tmp_path))]) == 0
    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert summary["prs"][0]["rerun"][0]["cancel_effect_unknown"] is True
    assert summary["prs"][0]["starved_repaired_outcome"] == {
        "docs": R.STARVED_OUTCOME_CANCEL_EFFECT_UNKNOWN}
    assert "ISSUED the cancel-then-rerun" not in captured.err
    assert "a cancel was SENT and its answer was lost" in captured.err, (
        "the unknown pole is its own claim")
    assert "was NOT issued" not in captured.err, (
        "…and must not be downgraded to \"nothing changed\", which is the "
        "understatement `_rerun`'s own docstring rejects for this state")


def test_the_exemption_is_asked_with_the_WHOLE_head_wide_repairable_set(monkeypatch):
    """⛔ P1 (review): a per-run exclusion DEADLOCKS whenever N>1 runs are repairable.

    `wait_witness_ids` is repo-wide and `congests_pr_apart_from` excludes only the run
    it is asked about. With three aged zero-job runs (the module's own documented
    `CI` + `Python CI` + `drift-guard` shape) each one is "another witness" for every
    other, so every target answers `True` and NOTHING is repaired: measured on the
    head's pure core, `rerun=[]`, `repair_refused=[101,102,103]`, `exit 0` — permanent,
    not the "converges over windows" the docstring claimed.

    The fix is that `classify()` asks the reading with the WHOLE repairable set, so the
    exclusion covers every run this pass can repair. Pinned here by RECORDING the
    argument the seam is called with: dropping the set (a per-run
    `congests_pr_apart_from(r.id)`) reddens the second assertion, and it is the
    multi-run fixture — not the single-run one — that makes that discriminating.

    ⛔ THIS IS THE ONE-HEAD HALF ONLY. The sibling test below is the PASS-wide half:
    a set built at ONE head is still narrower than the repo-wide witness list, so the
    same deadlock survives one PR apart.
    """
    seen: list[tuple[int, frozenset[int]]] = []

    def reader(run_id, also_repairable=()):
        seen.append((run_id, frozenset(also_repairable)))
        # The unfixed reading's answer for the N>1 shape: every target sees a witness
        # other than itself. Returning `False` only when the caller excluded the whole
        # set is what makes this test pin the CALLER's half of the contract.
        return any(t != run_id for t in (101, 102, 103)
                   if t not in set(also_repairable))

    runs = [aged_run(rid, jobs=0, age_s=5700.0) for rid in (101, 102, 103)]
    plan = R.classify(pr(), [], runs, ["python-ci-gate"], EMITTERS, max_attempts=3,
                      queue_congested=True, queue_congestion_measured=True,
                      queue_congestion_apart_from=reader)

    assert [r.id for r in plan.rerun] == [101, 102, 103], (
        "with every witness repairable, all of them must be re-run — a subset here is "
        "the deadlock: the unrepaired ones keep witnessing the repaired ones")
    assert plan.repair_refused == [], (
        "nothing is stranded by a congestion its own repairable set causes")
    for run_id, repairable in seen:
        assert {101, 102, 103} <= repairable, (
            f"the exemption was asked about {run_id} with {sorted(repairable)} — the "
            "caller must pass the whole head-wide repairable set")


def test_the_repairable_set_is_PASS_wide_so_one_run_per_PR_cannot_deadlock(monkeypatch):
    """⛔ P1 (review): the set was built at ONE head while the pass repairs EVERY PR.

    `build_plans()` walks every open PR and `wait_witness_ids` is REPO-WIDE, but the
    exemption set was derived from `all_runs` — one PR's head. So with ONE aged
    zero-job run on each of two open PRs, each run is "another witness" for the other,
    symmetrically: every plan answers `congests_pr_apart_from(...) is True`, refuses,
    and the pass sits at `rerun=[]` / `exit 0` for ever — the same §Fact 2 harm the
    single-PR fix closed, one PR apart, while the commit message claimed "the WHOLE
    repairable set".

    The fixture is `build_plans()` itself (the defect was in what the PASS hands down,
    not in `classify()`), fed the module's own pure `classify_queue` reading. The
    pre-fix shape is reproduced at the end, so a green run here cannot be green for
    some unrelated reason: the same reading asked per head, with only that head's
    derived set, refuses both plans.
    """
    from tools import ci_queue_health as Q

    head1, head2 = "1" * 40, "2" * 40
    # ⚠️ A zero-job run can only be aged INTO the witness class at
    # ZERO_JOB_MIN_AGE_S (5400 s), so 5700 s is both "old enough to be repairable"
    # and "old enough to witness" — the shape that deadlocked.
    reading = _queue_reading(pending=[_queue_run(101, age_s=5700.0),
                                      _queue_run(102, age_s=5700.0)])
    assert reading.verdict == Q.PR_STARVED, reading.reasons
    assert reading.wait_witness_ids == (101, 102)

    monkeypatch.setattr(R, "open_prs", lambda repo, include_drafts: [
        pr(1, head=head1), pr(2, head=head2)])
    monkeypatch.setattr(R, "check_runs_at", lambda repo, head: [])
    monkeypatch.setattr(R, "runs_at_head", lambda repo, head: [
        aged_run(101 if head == head1 else 102, jobs=0, age_s=5700.0,
                 path="ci.yml", name="CI")])

    plans = R.build_plans("o/r", ["docs"], EMITTERS, False, 3,
                          queue_congested=True, queue_congestion_measured=True,
                          queue_congestion_apart_from=reading.congests_pr_apart_from)
    by_pr = {p.number: p for p in plans}
    assert [r.id for r in by_pr[1].rerun] == [101], (
        "PR#1's own run must be repaired — PR#2's is not independent work")
    assert [r.id for r in by_pr[2].rerun] == [102]
    assert by_pr[1].repair_refused == [] and by_pr[2].repair_refused == [], (
        "nothing is stranded: each run is the other's witness only under a set built "
        "at one head")
    assert [r.id for r in by_pr[1].self_witness] == [101]
    assert [r.id for r in by_pr[2].self_witness] == [102]

    # THE PRE-FIX SHAPE, reproduced: `classify()` with only its OWN head's derived set
    # (what the pass used to hand down) refuses both — so the assertions above pin the
    # PASS-wide set and not something else in `build_plans`.
    for number, head, rid in ((1, head1, 101), (2, head2, 102)):
        alone = R.classify(pr(number, head=head), [],
                          [aged_run(rid, jobs=0, age_s=5700.0, path="ci.yml", name="CI")],
                          ["docs"], EMITTERS, max_attempts=3,
                          queue_congested=True, queue_congestion_measured=True,
                          queue_congestion_apart_from=reading.congests_pr_apart_from)
        assert alone.rerun == [], "the deadlock this test exists for"
        assert [d["run"] for d in alone.repair_refused] == [rid]


def test_the_PASS_wide_set_still_stops_at_the_structural_bound():
    """The wider set must not swallow the two bounds that keep it fail-closed.

    Widening the exemption from one head to the whole pass is exactly the change that
    could turn "a zero-job run" into "any queued run": a later attempt IS the fresh
    attempt a previous window's cancel-then-rerun created and is progressing, and a
    spent budget is a human's problem — neither is a wait that cannot drain, so
    neither may be excluded. Discriminating: adding the attempt bound back into the
    predicate moves 202 into `repairable_zero_job_ids` here and clears the refusal
    below.
    """
    from tools import ci_queue_health as Q

    runs = [aged_run(101, jobs=0, age_s=5700.0, path="ci.yml", name="CI"),
            aged_run(202, jobs=0, age_s=9000.0, attempt=2, path="ci.yml", name="CI")]
    assert R.repairable_zero_job_ids(runs, R.ZERO_JOB_MIN_AGE_S, 3) == frozenset({101}), (
        "an attempt-2 run at another PR is NOT in the repairable class")

    reading = _queue_reading(pending=[_queue_run(101, age_s=5700.0),
                                      _queue_run(202, age_s=9000.0)])
    assert reading.verdict == Q.PR_STARVED, reading.reasons
    plan = R.classify(pr(), [], [runs[0]], ["docs"], EMITTERS, max_attempts=3,
                      queue_congested=True, queue_congestion_measured=True,
                      queue_congestion_apart_from=reading.congests_pr_apart_from,
                      also_repairable=R.repairable_zero_job_ids(
                          runs, R.ZERO_JOB_MIN_AGE_S, 3))
    assert plan.rerun == [], "run 202 witnesses independently and still suppresses"
    assert [d["run"] for d in plan.repair_refused] == [101]


def test_a_NON_target_old_wait_still_refuses_the_cancel(monkeypatch):
    """The other pole: the widened exemption must NOT become "always exempt".

    A witness that is not itself a repairable zero-job run (here an ordinary aged
    queued run in `wait_witness_ids`) is work this pass cannot remove, so
    cancel-then-rerun would just re-queue behind it — the refusal is correct and the
    fix must not have cleared it.
    """
    from datetime import UTC, datetime, timedelta

    from tools import ci_queue_health as Q

    def qrun(rid, age_s):
        return Q.QueuedRun(id=rid, name="Python CI", event="pull_request",
                           created_at=(datetime.now(UTC) - timedelta(seconds=age_s))
                           .strftime("%Y-%m-%dT%H:%M:%SZ"))

    reading = Q.classify_queue([qrun(999, 9000.0)], [], None,
                               pending=[qrun(101, 5700.0), qrun(102, 5700.0)])
    assert reading.congests_pr is True, reading.reasons
    assert reading.congests_pr_apart_from(
        101, also_repairable=frozenset({101, 102})) is True, (
        "run 999 is not repairable by this pass, so it is a real independent witness")

    plan = R.classify(pr(), [], [aged_run(101, jobs=0, age_s=5700.0),
                                 aged_run(102, jobs=0, age_s=5700.0)],
                      ["python-ci-gate"], EMITTERS, max_attempts=3,
                      queue_congested=True, queue_congestion_measured=True,
                      queue_congestion_apart_from=reading.congests_pr_apart_from)
    assert plan.rerun == [], "an independent witness must still suppress the cancel"
    assert sorted(d["run"] for d in plan.repair_refused) == [101, 102], (
        "and both are recorded as refused, not silently dropped")


def test_a_STRUCTURALLY_UNREPAIRABLE_attempt_is_never_recorded_as_REFUSED(
        monkeypatch, capsys, tmp_path):
    """⭐ P1 (review, rail-C): the loop bound must be checked BEFORE the gate.

    `ZERO_JOB_MAX_REPAIRABLE_ATTEMPT`'s own docstring promises a later attempt "is
    NAMED (`NO-VERDICT-WILL-ARRIVE`), never cancelled and never escalated". The
    `crowded` branch sat ABOVE that bound, so a queued attempt-2 run — the fresh
    attempt a previous window's repair created, which this tool will never cancel BY
    DESIGN — was appended to `repair_refused`. `main()` escalates on any refusal under
    an UNMEASURED queue, so one such run made EVERY window exit 1 forever while
    nothing could ever repair it: the permanent false red the local fallback reads as
    "not covered".

    Discriminating: pre-fix the unit case yields `repair_refused == [4242]` and the
    `main()` case exits 1 with `repair_refused=1`.
    """
    plan = R.classify(pr(), [],
                      [aged_run(4242, jobs=0, age_s=9000.0, attempt=2,
                                started_age_s=8700.0, path="ci.yml", name="CI")],
                      ["docs"], EMITTERS, max_attempts=3,
                      queue_starved=False, queue_congested=True,
                      queue_congestion_measured=False)
    assert [d["run"] for d in plan.no_verdict] == [4242], (
        "refusing to act is not refusing to report — it is still NAMED")
    assert plan.repair_refused == [], (
        "the attempt a previous window created is owned by the QUEUE; recording it "
        "as a refusal makes an unmeasured queue escalate it every window, forever")
    assert plan.rerun == [] and plan.exhausted == []

    # The window-level consequence: an UNMEASURED queue and nothing REFUSED is a
    # discharged window (exit 0), because nothing is left that a later window could
    # not repair by itself.
    monkeypatch.setattr(R, "read_queue_health", lambda repo: None)
    monkeypatch.setattr(R, "open_prs", lambda repo, include_drafts: [pr(1)])
    monkeypatch.setattr(R, "check_runs_at", lambda repo, head: [])
    monkeypatch.setattr(R, "runs_at_head", lambda repo, head: [
        aged_run(4242, jobs=0, age_s=9000.0, attempt=2, started_age_s=8700.0,
                 path="ci.yml", name="CI")])

    code = R.main(["--repo", "o/r", "--no-live", "--apply", "--json",
                   "--record", str(_flood_record(tmp_path))])
    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert code == 0, (
        "an attempt the tool will NEVER act on must not pin the window red — that is "
        "the alarm fatigue the red-surface doc measures at 31.3%")
    assert summary["repair_refused"] == 0
    assert summary["zero_job_named"] == 1, "it is still named in the record"
    assert "REFUSED-REPAIR" not in captured.err


def test_a_SPENT_BUDGET_still_escalates_under_a_congested_queue():
    """⭐ P1 (review, rail-C) second harm: a spent budget must reach `exhausted`.

    `report()` escalates on `p.exhausted` regardless of what else is true of the PR,
    and it is the escalation that says "a human is needed". Routing a spent budget
    through `repair_refused` instead made that escalation conditional on the queue
    reading: a MEASURED congestion keeps exit 0 (correct for a refusal), so the spent
    budget disappeared silently. Discriminating: pre-fix `exhausted == []`.
    """
    plan = R.classify(pr(), [],
                      [aged_run(11, jobs=0, age_s=5700.0, attempt=1)],
                      ["python-ci-gate"], EMITTERS, max_attempts=1,
                      queue_starved=True, queue_congested=True)
    assert "python-ci-gate" in plan.exhausted, (
        "the budget is spent: re-running again is a loop, and a human must be told")
    assert plan.rerun == [], "nothing may be re-run past the budget"
    assert plan.repair_refused == [], (
        "it was not REFUSED — there was nothing left to refuse")


def test_zero_job_named_and_repair_refused_share_a_unit(monkeypatch, capsys, tmp_path):
    """⭐ P2 (review): the two adjacent zero-job counters must count the SAME thing.

    `zero_job_named` counted PLANS with at least one named run while `repair_refused`
    counted RUNS, so ONE PR with TWO stalled zero-job runs printed
    `zero_job_named=1, repair_refused=2` — "more repairs refused than runs named",
    impossible under a shared unit, and it contradicted the comment block above the key
    that compares the two. A one-run fixture (every earlier test) answers `1` under
    either definition, which is why the wrong unit survived.

    Discriminating: pre-fix `zero_job_named == 1` on this fixture.
    """
    monkeypatch.setattr(R, "read_queue_health", lambda repo: None)
    monkeypatch.setattr(R, "open_prs", lambda repo, include_drafts: [pr(1)])
    monkeypatch.setattr(R, "check_runs_at", lambda repo, head: [])
    monkeypatch.setattr(R, "runs_at_head", lambda repo, head: [
        aged_run(4242, jobs=0, age_s=9000.0, path="ci.yml", name="CI"),
        aged_run(4243, jobs=0, age_s=9100.0, path="ci.yml", name="CI")])

    R.main(["--repo", "o/r", "--no-live", "--apply", "--json",
            "--record", str(_flood_record(tmp_path))])
    summary = json.loads(capsys.readouterr().out)
    assert summary["needing_repair"] == 0, "the fixture must be the understating shape"
    assert summary["zero_job_named"] == 2, (
        "TWO runs were named — the key counts runs, like `repair_refused` beside it")
    assert summary["repair_refused"] == 2
    assert summary["zero_job_named"] >= summary["repair_refused"], (
        "a refusal is a subset of the named runs; 'more refused than named' is the "
        "symptom of the mismatched unit this test exists to prevent")


def test_the_gate_refuses_every_unmeasured_queue_reading():
    """The mapping itself, verdict by verdict, so no single state can regress alone."""
    from tools import ci_queue_health as Q

    def reading(verdict, **kw):
        base = dict(verdict=verdict, queued_total=0, in_progress_total=0,
                    dominant_workflow=None, dominant_queued=0, pr_running=0,
                    pr_queued=0, oldest_pr_queued_age_seconds=None)
        base.update(kw)
        return Q.QueueHealth(**base)

    def gate_for(queue):
        """The shipped pair: one reading, then the decision on it."""
        return R.queue_congestion_gate(R.queue_congestion_reading(queue))

    assert R.queue_congestion_gate(None) is True, (
        "a queue that was never read (or failed to read) refuses the act")
    assert R.queue_congestion_gate(reading(Q.UNKNOWN).congests_pr) is True
    assert R.queue_congestion_gate(reading(Q.PR_UNWITNESSED).congests_pr) is True
    assert R.queue_congestion_gate(reading(Q.OK, truncated=True).congests_pr) is True, (
        "a NEWEST-FIRST feed cut at the page cap dropped the oldest queued runs — "
        "exactly the ones the wait clause needs, so it cannot rule a backlog out")
    assert R.queue_congestion_gate(reading(Q.OK).congests_pr) is True, (
        "⭐ P1 (review): an `OK` whose `pr_queued == 0` is the clause that says "
        "itself 'starvation was not witnessed' — the feed is filtered to "
        "status=queued and a pending/waiting/requested PR run is not in it, so "
        "this is an UNREAD queue, not a measured-clear one")
    assert R.queue_congestion_gate(
        reading(Q.OK, pr_wait_measured=False).congests_pr) is True, (
            "the same fact named explicitly: `False` requires the wait to have been read")
    assert gate_for(object()) is True, (
        "a reading that cannot answer the question at all is not a measured 'clear'")
    # ...and the measured readings keep their answers. `pr_wait_measured=True` is the
    # WITNESS a `False` needs. A verdict that WITNESSES congestion needs none:
    # FLOOD / PR-STARVED / PR-BACKLOGGED are positive claims about runs that were read.
    assert gate_for(reading(Q.OK, pr_queued=1, oldest_pr_queued_age_seconds=60.0,
                            pr_wait_measured=True)) is False
    assert gate_for(reading(Q.FLOOD)) is True
    assert gate_for(reading(Q.PR_STARVED)) is True
    assert gate_for(reading(Q.PR_BACKLOGGED)) is True
    assert R.queue_congestion_reading(None) is None, (
        "no read at all and an abstained read both answer None — the REPORT is where "
        "they are told apart, and the gate refuses either")
    assert R.queue_congestion_reading(object()) is None


def test_the_same_run_shared_by_two_contexts_is_named_once():
    runs = [aged_run(7, path="ci.yml", name="CI", jobs=0, age_s=5700.0)]
    plan = R.classify(pr(), [], runs, ["docs", "test-isolation"], EMITTERS, max_attempts=3)
    assert len(plan.no_verdict) == 1 and [r.id for r in plan.rerun] == [7]


# ── 13. a rate-limited read is a REFUSAL, never an empty result ─────────────

def _fake(returncode=0, stdout="null", stderr=""):
    return subprocess.CompletedProcess(args=["gh"], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


LIMITED = "HTTP 403: API rate limit exceeded for user ID 1. (secondary rate limit)"


def test_rate_limited_read_raises_a_named_refusal_not_an_empty_result(monkeypatch):
    monkeypatch.setattr(R, "_run", lambda argv, **_kw: _fake(1, "", LIMITED))
    monkeypatch.setattr(R, "_sleep", lambda s: None)
    with pytest.raises(R.MeasureError, match="RATE-LIMITED \\(not empty\\)"):
        R.gh_json(["api", "repos/o/r/commits/x/check-runs"], retries=0)


def test_rate_limited_read_is_retried_then_succeeds(monkeypatch):
    calls = {"n": 0}
    slept: list[float] = []

    def fake(argv, **_kw):
        calls["n"] += 1
        return _fake(1, "", "HTTP 403: API rate limit exceeded. Retry-After: 7") \
            if calls["n"] == 1 \
            else _fake(0, '{"total_count": 0}', "")

    monkeypatch.setattr(R, "_run", fake)
    monkeypatch.setattr(R, "_sleep", slept.append)
    assert R.gh_json(["api", "x"], retries=1) == {"total_count": 0}
    assert calls["n"] == 2 and slept == [7.0], "the server's own hint is honoured"


def test_non_rate_limit_failure_is_not_retried_and_not_called_empty(monkeypatch):
    calls = {"n": 0}

    def fake(argv, **_kw):
        calls["n"] += 1
        return _fake(1, "", "HTTP 404: Not Found")

    monkeypatch.setattr(R, "_run", fake)
    monkeypatch.setattr(R, "_sleep", lambda s: None)
    with pytest.raises(R.MeasureError, match="404"):
        R.gh_json(["api", "x"], retries=2)
    assert calls["n"] == 1


def test_a_wait_the_pass_cannot_afford_is_a_refusal_not_a_silent_death(monkeypatch):
    """⭐ P2 (review): the pass had no wall-clock bound, so the reads alone could sleep
    past the consumer workflow's `timeout-minutes: 15` and the job would be KILLED —
    neither `RATE-LIMITED (not empty)` nor an exit code ever emitted.

    Discriminating: pre-fix, `gh_json` slept unconditionally and this raised nothing.
    """
    monkeypatch.setattr(R, "_run", lambda argv, **_kw: _fake(1, "", LIMITED))
    monkeypatch.setattr(R, "_sleep",
                        lambda s: pytest.fail("must not sleep past the pass budget"))
    monkeypatch.setattr(R, "_PASS_DEADLINE", R.time.time() + 1.0)
    with pytest.raises(R.MeasureError, match="RATE-LIMITED \\(not empty\\)"):
        R.gh_json(["api", "repos/o/r/commits/x/check-runs"], retries=2)


def test_a_pass_budget_leaves_room_for_the_repairs_inside_the_workflow_timeout():
    """The budget is only useful if it is under the consumer's own `timeout-minutes`."""
    assert R.PASS_BUDGET_S < 15 * 60, (
        f"a {R.PASS_BUDGET_S}s budget cannot pre-empt a 900s workflow timeout")


def test_permission_403_is_not_misread_as_a_rate_limit(monkeypatch):
    """`HTTP 403` is also how a PERMISSION error arrives — retrying it is wrong.

    A permission 403 was routed into the rate-limit path, which slept up to two
    minutes and then diagnosed a token problem as a budget problem.
    """
    calls = {"n": 0}

    def fake(argv, **_kw):
        calls["n"] += 1
        return _fake(1, "", "HTTP 403: Resource not accessible by integration")

    monkeypatch.setattr(R, "_run", fake)
    monkeypatch.setattr(R, "_sleep", lambda s: pytest.fail("must not sleep on a 403 that is not a rate limit"))
    with pytest.raises(R.MeasureError, match="Resource not accessible"):
        R.gh_json(["api", "x"], retries=2)
    assert calls["n"] == 1


def test_approval_parked_run_is_not_named_no_verdict():
    """`waiting` = parked for manual approval: no age starts it, and a rerun cannot
    obtain an approval, so spending the budget there is wasted."""
    plan = R.classify(pr(), [], [aged_run(1, status="waiting", jobs=0, age_s=7200.0)],
                      ["python-ci-gate"], EMITTERS, max_attempts=3)
    assert plan.no_verdict == [] and plan.rerun == []


def test_backoff_honours_retry_after_then_reset_then_jitter():
    assert R._rate_limit_sleep_s("HTTP 403: Retry-After: 30", 0) == 30.0
    assert R._rate_limit_sleep_s("Retry-After: 999999", 0) == R.RATE_LIMIT_MAX_SLEEP_S
    # X-RateLimit-Reset is an absolute epoch; the wait is what is LEFT of it.
    # A reset is an ABSOLUTE epoch (10 digits in practice); a short number is not
    # one, and treating it as one would sleep for a nonsense interval.
    assert R._rate_limit_sleep_s("X-RateLimit-Reset: 1700000030", 0,
                                 now=1_700_000_000.0) == 30.0
    assert R._rate_limit_sleep_s("X-RateLimit-Reset: 1000", 0, now=970.0) != 30.0
    # No hint: GitHub's documented floor ("wait at least one minute") with EQUAL
    # jitter upward, never below the floor.
    for attempt in (0, 1, 2):
        for _ in range(5):
            v = R._rate_limit_sleep_s("secondary rate limit", attempt)
            assert R.RATE_LIMIT_MIN_SLEEP_S <= v <= R.RATE_LIMIT_MAX_SLEEP_S, (
                f"a hint-less wait of {v}s at attempt {attempt} is inside the window "
                "the limiter is still enforcing — GitHub's floor is one minute")


def test_the_hint_less_backoff_window_grows_instead_of_a_fixed_minute():
    """⭐ P2 (review): the "EQUAL jitter … so a fleet of lanes does not retry in
    lockstep" claim was a NO-OP at the shipped defaults.

    `min(max(RATE_LIMIT_BASE_S * 2**attempt, floor), MAX)` with a 2 s base started
    BELOW the 60 s floor, so the ceiling was clamped UP to the floor and
    `uniform(60, 60)` returned exactly 60.0 at both attempts `RATE_LIMIT_RETRIES = 2`
    can reach — every lane waited the same minute (the lockstep burst the comment says
    it prevents) and the delay never grew, which is GitHub's other documented half
    (*"use exponentially increasing delays for repeated failures"*).

    This asserts the PROPERTY, not the range: a constant satisfies
    `MIN <= v <= MAX` (and so would deleting `random` and returning 60.0).
    Discriminating: pre-fix `max(draws) == floor` and `len(set(draws)) == 1`.
    """
    floor = R.RATE_LIMIT_MIN_SLEEP_S
    # The documented one-minute floor is not exceeded at the first retry: nothing in
    # the guidance justifies waiting MORE than a minute before the first retry.
    assert {R._rate_limit_sleep_s("secondary rate limit", 0) for _ in range(20)} == {floor}
    for attempt in (1, 2):
        ceiling = min(floor * 2 ** attempt, R.RATE_LIMIT_MAX_SLEEP_S)
        draws = [R._rate_limit_sleep_s("secondary rate limit", attempt) for _ in range(64)]
        assert all(floor <= v <= ceiling for v in draws), (
            f"attempt {attempt} drew {sorted(draws)[:1]}…{sorted(draws)[-1:]}, outside "
            f"[{floor}, {ceiling}]")
        assert len(set(draws)) > 1, (
            "a CONSTANT wait is a fleet retrying in lockstep — the burst the jitter "
            "window exists to break")
        assert max(draws) > floor, "the jitter window is empty; the ceiling equals the floor"
    # ... and it GROWS: the second retry can wait longer than the first.
    growth = min(floor * 2, R.RATE_LIMIT_MAX_SLEEP_S)
    second = [R._rate_limit_sleep_s("secondary rate limit", 2) for _ in range(64)]
    assert max(second) > growth, (
        "the delay must increase across attempts, not repeat the same minute")
    # Past the retry budget the growth is capped rather than unbounded.
    assert all(R._rate_limit_sleep_s("secondary rate limit", 6) <= R.RATE_LIMIT_MAX_SLEEP_S
               for _ in range(20))


def test_the_wall_clock_budget_is_armed_before_the_first_live_read(monkeypatch, tmp_path):
    """⭐ P2 (review): `_PASS_DEADLINE` was armed inside `build_plans`, but `main()`
    calls `resolve_required(..., live=True)` BEFORE it, and that path performs a LIVE
    read (branch protection).

    With the deadline still `None`, `_spend_rate_limit_wait` skipped the budget
    comparison entirely and slept the full wait — up to 600 s UNBUDGETED on top of the
    720 s armed afterwards, i.e. ~1380 s against the consumer workflow's
    `timeout-minutes: 15`, where the job is KILLED and neither
    `RATE-LIMITED (not empty)` nor any exit code is ever emitted. That is the same
    lost-measurement failure `PASS_BUDGET_S` exists to prevent, on the read that
    happens FIRST.

    Discriminating: pre-fix `_sleep` IS called here (the deadline is still `None`, so
    the wait reads as affordable).
    """
    rec = tmp_path / "required-contexts.json"
    rec.write_text(json.dumps({"required_contexts": ["docs"],
                               "emitters": {"docs": [["ci.yml", "docs"]]}}))
    monkeypatch.setattr(R, "read_queue_health", lambda repo: None)
    monkeypatch.setattr(R, "open_prs", lambda repo, include_drafts: [])
    monkeypatch.setattr(R, "_run", lambda argv, **_kw: _fake(1, "", LIMITED))
    slept: list[float] = []
    monkeypatch.setattr(R, "_sleep", slept.append)
    monkeypatch.setattr(R, "_PASS_DEADLINE", None)
    how_long_may_a_pass_sleep = 0.0     # every wait is unaffordable
    monkeypatch.setattr(R, "PASS_BUDGET_S", how_long_may_a_pass_sleep)
    R.main(["--repo", "o/r", "--record", str(rec), "--json"])
    assert slept == [], (
        "the pass budget must already be armed when the FIRST live read runs — an "
        "unarmed deadline makes that read's waits unbounded")


def test_a_hint_less_backoff_reaches_the_documented_minute():
    """⭐ P2 (review): the first cut backed off ~1 s then ~2 s, so BOTH retries were
    spent inside ~3 s and a hint-less secondary limit ended as a refusal having never
    effectively retried. GitHub's documented no-hint handling is *wait at least one
    minute*.

    Discriminating: pre-fix this returns <= 4.0.
    """
    assert R._rate_limit_sleep_s("secondary rate limit", 0) >= 60.0
    assert R._rate_limit_sleep_s("secondary rate limit", 1) >= 60.0


def test_a_server_hint_is_not_shortened_by_the_cap():
    """⭐ P2 (review): a real `Retry-After: 300` was capped DOWN to 60 s, which is the
    opposite of "do not retry until after that many seconds has elapsed".

    Discriminating: pre-fix this returns 60.0.
    """
    assert R._rate_limit_sleep_s("HTTP 403: Retry-After: 300", 0) == 300.0


# ── 14. the zero-job read must not fight a flood, and must not add load ─────

def test_a_starved_zero_job_run_is_named_but_never_rerun():
    """A measured FLOOD is repaired by the governor, never by a per-head rerun.

    Under a flood EVERY queued run has zero jobs, so naming them must not turn into
    "re-run them": that adds load to the flood they are starving behind, and it burns
    the attempt budget the post-flood repair needs (leaving the context EXHAUSTED).
    """
    runs = [aged_run(10, jobs=0, age_s=5700.0, path="ci.yml", name="CI")]
    plan = R.classify(pr(), [], runs, ["docs"], EMITTERS, max_attempts=3,
                      queue_starved=True)
    assert [d["run"] for d in plan.no_verdict] == [10], "it is still NAMED"
    assert plan.rerun == [], "a starved context is repaired by NOTHING"
    assert plan.exhausted == []
    assert plan.starved == ["docs"]


def test_job_reads_stop_after_the_aggregate_failure_bound(monkeypatch):
    """A refused jobs read abstains, and the pass stops probing — it must not pay a
    rate-limit sleep once per queued run (11 measured runs ~ 22 min against a 15-min
    workflow timeout: the job gets KILLED instead of exiting 2)."""
    calls = {"n": 0}

    def refuse(argv, **_kw):
        calls["n"] += 1
        return _fake(1, "", LIMITED)

    monkeypatch.setattr(R, "_run", refuse)
    monkeypatch.setattr(R, "_sleep", lambda s: None)
    monkeypatch.setattr(R, "_JOB_READ_FAILURES", 0)
    monkeypatch.setattr(R, "_READ_CACHE", {})
    ids = [R.job_count("o/r", 100 + i) for i in range(10)]
    assert ids == [None] * 10, "every unreadable probe ABSTAINS (never a zero)"
    assert calls["n"] == R.JOB_READ_FAILURE_LIMIT, (
        f"probed {calls['n']} times for 10 runs; expected to stop at the bound")


def test_approval_parked_runs_are_not_probed_at_all(monkeypatch):
    probed: list[int] = []
    monkeypatch.setattr(R, "job_count", lambda repo, rid, attempt=0: probed.append(rid))
    monkeypatch.setattr(R, "_READ_CACHE", {})
    monkeypatch.setattr(R, "_paginate", lambda path, key: [
        {"id": 1, "name": "CI", "path": ".github/workflows/ci.yml", "status": "waiting",
         "conclusion": None, "run_attempt": 1, "event": "pull_request",
         "created_at": "2026-11-02T18:00:00Z"},
        {"id": 2, "name": "CI", "path": ".github/workflows/ci.yml", "status": "queued",
         "conclusion": None, "run_attempt": 1, "event": "pull_request",
         "created_at": "2026-11-02T18:00:00Z"}])
    runs = R.runs_at_head("o/r", "a" * 40)
    assert probed == [2], "only the queued run can change a verdict"
    assert [r.job_count for r in runs] == [None] * 2


# ── 14. the age and the job count must describe the SAME ATTEMPT ─────────────
#
# MEASURED 2026-11-02 on run 41523739094: `POST /actions/runs/<id>/rerun` reuses the
# run object, so attempt 2 began at 21:41:43Z (`run_started_at`) while `created_at`
# still read 20:56:13Z (the attempt-1 time). `job_count` and the `/jobs` read that
# feeds it both describe the LATEST attempt, so ageing the attempt from the run's
# creation gives every rerun a floor of 0: a fresh attempt that has not materialised
# its jobs yet is declared "no verdict will arrive" the instant it exists, re-run
# again, and escalated EXHAUSTED for a run that was progressing normally. Both
# directions are pinned here so neither half can drift back.

def test_a_fresh_rerun_attempt_is_not_aged_by_the_runs_creation():
    """Attempt 2 at 60s old must NOT inherit attempt 1's 1800s age."""
    from datetime import UTC, datetime
    run_ = aged_run(41480218593, jobs=0, age_s=1800.0, attempt=2)
    run_.started_at = datetime.fromtimestamp(
        R.time.time() - 60.0, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    plan = R.classify(pr(), [], [run_], ["python-ci-gate"], EMITTERS, max_attempts=3)
    assert plan.no_verdict == [], (
        "a fresh attempt is ordinary progressive-job lag, not a dead run — the "
        "run's creation time says nothing about the attempt that has zero jobs")
    assert plan.rerun == [], "and it must not be re-run (and re-burned) again"
    assert "python-ci-gate" in plan.pending


def test_a_rerun_attempt_past_its_OWN_floor_is_still_named():
    """The other direction: an attempt 2 that really is old is still NAMED.

    Its age is the ATTEMPT's (a stamped `run_started_at` later than `created_at`),
    never the run's creation. It is named so the no-verdict state is not silent — and
    NOT repaired: a queued zero-job re-run IS the fresh attempt the previous window
    created, so cancelling it is the loop (`ZERO_JOB_MAX_REPAIRABLE_ATTEMPT`).
    """
    from datetime import UTC, datetime
    run_ = aged_run(41480218593, jobs=0, age_s=6000.0, attempt=2)
    run_.started_at = datetime.fromtimestamp(
        R.time.time() - 5700.0, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    plan = R.classify(pr(), [], [run_], ["python-ci-gate"], EMITTERS, max_attempts=3)
    assert [d["run"] for d in plan.no_verdict] == [41480218593]
    assert plan.no_verdict[0]["age_seconds"] >= 5400, (
        "the reported age must be the ATTEMPT's, not the run's creation")
    assert plan.rerun == [], (
        "the repair is attempt-1 only: this IS the attempt it produced")


def test_a_queued_rerun_without_a_stamped_start_abstains():
    """An attempt whose start was NEVER STAMPED has no measurable age ⇒ abstain.

    ⛔ The premise this test was originally written on is REFUTED, and the refutation is
    recorded in `attempt_age_seconds`: the claim that a queued re-run carries a NULL
    `run_started_at` came from a third-party report
    (open-telemetry/shared-workflows#440) and was NOT reproduced on this repo —
    measured 2026-11-03, run 41585740714 carried `run_attempt` 2 with a stamped
    `run_started_at` (05:54:22Z vs created 05:45:16Z) while still `queued`. So a
    queued re-run IS normally ageable, and this abstention is NOT the bound on the
    cancel→rerun→cancel loop (that bound is structural:
    `ZERO_JOB_MAX_REPAIRABLE_ATTEMPT`).

    What the test pins is the fail-closed direction for the residual shape: an attempt
    that was never stamped must not be aged from `created_at` (which never moves on a
    rerun, so it would return ATTEMPT 1's age and authorise a destructive act on a
    fresh attempt). It waits, reports nothing, and changes nothing.

    Discriminating: pre-fix `attempt_age_seconds` falls back to `created_at` here, so
    this run is named, cancelled and re-run.
    """
    run_ = aged_run(41480218593, jobs=0, age_s=1800.0, attempt=2)  # started_at == ""
    assert run_.started_at == "" and run_.run_attempt == 2
    assert R.attempt_age_seconds(run_) is None, (
        "an attempt whose start has not been stamped has NO measurable age")
    plan = R.classify(pr(), [], [run_], ["python-ci-gate"], EMITTERS, max_attempts=3)
    assert plan.no_verdict == [], "an unmeasurable age must never enter the stall class"
    assert plan.rerun == [], "so nothing destructive is authorised from it either"
    assert plan.exhausted == []
    assert "python-ci-gate" in plan.pending, (
        "it is still 'late', not 'dead' — the deferral of a reopen is unchanged")


def test_runs_at_head_reads_the_attempt_scoped_start(monkeypatch):
    """`run_started_at` is what makes the age attempt-scoped; it must be carried."""
    monkeypatch.setattr(R, "_READ_CACHE", {})
    monkeypatch.setattr(R, "job_count", lambda repo, rid, attempt=0: 0)
    monkeypatch.setattr(R, "_paginate", lambda path, key: [
        {"id": 7, "name": "Python CI", "path": ".github/workflows/python-ci.yml",
         "status": "pending", "conclusion": None, "run_attempt": 2,
         "event": "pull_request", "created_at": "2026-11-02T20:56:13Z",
         "run_started_at": "2026-11-02T21:41:43Z"}])
    runs = R.runs_at_head("o/r", "a" * 40)
    assert runs[0].created_at == "2026-11-02T20:56:13Z"
    assert runs[0].started_at == "2026-11-02T21:41:43Z", (
        "dropping run_started_at silently restores the attempt-1 age for every rerun")


# ── 15. a NON-COMPLETED run is CANCELLED before it is re-run ─────────────────
#
# MEASURED 2026-11-02 against run 41523739094 while it was `pending` with zero jobs:
# `POST /actions/runs/<id>/rerun` → `403 {"message": "This workflow is already
# running"}`. The stall class is BY CONSTRUCTION a non-completed run, so a rerun
# without a preceding cancel returns 'failed' every window and escalates the very PR
# this tool exists to repair. Cancel → `completed/cancelled` → rerun was accepted
# (attempt 2). Cancelling is safe HERE and only here because the class is reached
# only for a run with a MEASURED zero jobs — there is no job to destroy.

class _Done:
    """A `subprocess.CompletedProcess` stand-in that can carry a refusal's stderr."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _api(*, head="a" * 40, status="queued", jobs=0, settle="completed", attempt=1):
    """`gh_json` for the cancel path: PR head, LIVE run status, LIVE job count, settle.

    The runs endpoint is answered twice — once by `_cancel`'s pre-act re-measurement and
    then by the settle polls — so the first answer is the live status and every later
    one is `settle`. `run_attempt` is carried because the pre-act gate compares it with
    the attempt the run was CLASSIFIED on: a fresh re-run attempt is `queued` with `0`
    jobs, so status and job count alone cannot tell it apart from the stalled run.
    """
    seen = {"runs": 0}

    def fake(a, **_kw):
        path = a[-1] if a else ""
        if a[:1] == ["pr"]:
            return {"headRefOid": head}
        if "jobs" in path:
            return {"total_count": jobs}
        seen["runs"] += 1
        return {"status": status if seen["runs"] == 1 else settle,
                "run_attempt": attempt}
    return fake


def _verbs(verbs, *, cancel_rc=0, cancel_stderr="", rerun_rc=0):
    def fake(argv, **_kw):
        verb = argv[-1].rsplit("/", 1)[-1]
        verbs.append(verb)
        if verb == "cancel":
            return _Done(cancel_rc, stderr=cancel_stderr)
        return _Done(rerun_rc)
    return fake


def test_a_queued_zero_job_run_is_cancelled_before_it_is_rerun(monkeypatch):
    monkeypatch.setattr(R, "gh_json", _api())
    monkeypatch.setattr(R, "_sleep", lambda s: None)
    verbs: list[str] = []
    monkeypatch.setattr(R, "_run", _verbs(verbs))
    state = R._rerun("o/r", run(41523739094, "python-ci.yml", "pending"), 1,
                     expected_head="a" * 40)
    assert state == "cancelled_rerun"
    assert verbs == ["cancel", "rerun"], (
        "the cancel must precede the rerun — a rerun of a still-running run is "
        f"refused by the API with 'This workflow is already running'; got {verbs}")


def test_a_refused_cancel_does_not_spend_the_rerun(monkeypatch):
    """A cancel the API refuses must not be followed by a rerun it will refuse too.

    ⭐ P2 (review): the stub GRANTED the cancel, so the `returncode != 0` branch this
    test is named for was never entered (deleting the guard left it green), and the
    un-patched `_sleep` made the failed settle loop cost 9 real seconds per run.

    ⭐ P2 #10641 review: the state MOVED. A non-zero exit here is the API ANSWERING —
    `403 Resource not accessible by integration` from a token without `actions: write`,
    or an org policy. Folding that into `deferred` ("nothing was changed") reported a
    repair this credential cannot perform as a discharged window, so the scheduled run
    exited 0, coverage said the window was covered, and the local actor — the credential
    that COULD perform it — was never engaged. It is `denied` now, which escalates.

    ⛔ `denied`, NOT `failed`: #10763 landed the SAME distinction on main while this
    branch was open, from the other end (it anchored a `_CANCEL_DENIED_RE` on GitHub's
    permission wording so a rate-limited 403 stays a deferral). Both fixes agree on the
    behaviour that matters — a readable permission refusal must escalate — and this
    rebase keeps that fix's STATE NAME and its narrower anchor rather than replacing
    them, because the anchor is what keeps the secondary rate limiter's own 403 out of
    the escalation path.
    """
    monkeypatch.setattr(R, "gh_json", _api())
    monkeypatch.setattr(R, "_sleep",
                        lambda s: pytest.fail("a refused cancel has no settle loop to sleep in"))
    verbs: list[str] = []
    # ⛔ A NEUTRAL stderr, deliberately: this test is named for the plain transient
    # refusal, and `deferred` is its state. The permission case has its own test
    # (`test_a_PERMISSION_denied_cancel_is_TERMINAL_and_never_a_deferral`) with the
    # GitHub 403 wording, and a rate-limited 403 has a third — with a permission
    # string here this test asserted `denied` too and pinned nothing of its own
    # (#10641 review P2).
    monkeypatch.setattr(R, "_run", _verbs(verbs, cancel_rc=1,
                                           cancel_stderr="gh: Server Error (HTTP 502)"))
    assert R._rerun("o/r", run(1, "ci.yml", "pending"), 1,
                    expected_head="a" * 40) == "deferred", (
        "an unclassifiable refusal MAY be a blip: it stays a skip, not a terminal "
        "credential failure — escalating every blip is the over-reporting `deferred` "
        "was introduced to stop")
    assert verbs == ["cancel"], "no rerun may be issued after a refused cancel"


def test_a_cancel_post_whose_answer_was_lost_is_not_a_skip(monkeypatch):
    """⭐ P2 #10641 review: a POST TIMEOUT is UNKNOWN EFFECT, not "nothing changed".

    `_run` kills the child on expiry (`timeout=120`), and a killed `gh` may already have
    had its request applied — the server's ANSWER is what was lost, not the act. The
    post-POST states are the ones that must not read as "nothing was changed", yet this
    path answered `refused` → `deferred`, whose record carried no effect key at all.

    Discriminating: pre-fix this returns `deferred` (indistinguishable from the
    pre-POST `no_budget` skip), and a rerun is issued off a cancel that may have landed.
    """
    monkeypatch.setattr(R, "gh_json", _api())
    monkeypatch.setattr(R, "_sleep", lambda s: pytest.fail("no settle loop without a POST answer"))
    verbs: list[str] = []

    def fake(argv, **_kw):
        verb = argv[-1].rsplit("/", 1)[-1]
        verbs.append(verb)
        if verb == "cancel":
            raise subprocess.TimeoutExpired(cmd=argv, timeout=120)
        return _Done(0)

    monkeypatch.setattr(R, "_run", fake)
    assert R._rerun("o/r", run(1, "ci.yml", "pending"), 1,
                    expected_head="a" * 40) == "cancel_unknown", (
        "the POST was sent and the answer was lost — the effect is UNKNOWN")
    assert verbs == ["cancel"], (
        "a rerun issued off a cancel that may have landed is refused 'already running'")


def test_a_cancel_post_that_never_left_the_process_is_still_a_deferral(monkeypatch):
    """The other half of the split: `gh` could not be STARTED, so NOTHING was changed.

    `OSError` is raised before any request is made (a missing/unrunnable binary), so
    "nothing was changed" is TRUE and the deferral is correct — this must not be folded
    into the unknown-effect state, which would suppress a legitimate retry's record.
    """
    monkeypatch.setattr(R, "gh_json", _api())
    monkeypatch.setattr(R, "_sleep", lambda s: pytest.fail("no settle loop without a POST"))
    verbs: list[str] = []

    def fake(argv, **_kw):
        verb = argv[-1].rsplit("/", 1)[-1]
        verbs.append(verb)
        if verb == "cancel":
            raise FileNotFoundError("gh")
        return _Done(0)

    monkeypatch.setattr(R, "_run", fake)
    assert R._rerun("o/r", run(1, "ci.yml", "pending"), 1,
                    expected_head="a" * 40) == "deferred"
    assert verbs == ["cancel"]


def test_a_PERMISSION_denied_cancel_is_TERMINAL_and_never_a_deferral(monkeypatch):
    """⭐ P1 #10763: a 403 is the ONE cancel refusal no next window can repair.

    `_cancel` mapped every non-`not_queued` POST failure to 'refused', `_rerun`
    mapped 'refused' to 'deferred', `report` excludes 'deferred' from the escalation
    predicate — so a token without `actions:write` made the tool exit 0 with the
    required check still absent, indefinitely, and nothing ever escalated. That is a
    no-op gate: an empty repair that is indistinguishable from a performed one.

    Discriminating: pre-fix this returns 'deferred' (measured before the fix).
    """
    monkeypatch.setattr(R, "gh_json", _api())
    monkeypatch.setattr(R, "_sleep",
                        lambda s: pytest.fail("a refused cancel has no settle loop"))
    verbs: list[str] = []
    monkeypatch.setattr(R, "_run", _verbs(
        verbs, cancel_rc=1,
        cancel_stderr="gh: Resource not accessible by integration (HTTP 403)"))
    assert R._rerun("o/r", run(1, "ci.yml", "pending"), 1,
                    expected_head="a" * 40) == "denied", (
        "a permission refusal is terminal — deferring it promises a window that can "
        "never succeed, and the caller then reports the window as discharged")
    assert verbs == ["cancel"], "a denied cancel must not be followed by a rerun"


def test_a_rate_limited_403_cancel_is_STILL_a_deferral(monkeypatch):
    """The trap in the other direction: the secondary rate limiter ALSO answers 403.

    `_RATE_LIMIT_RE` exists because that wording must not be read as a token problem.
    The mirror is just as load-bearing here: escalating a rate-limited 403 every
    window would spam a human for a condition that clears itself, which is the
    over-reporting `deferred` was introduced to stop.

    ⭐ P2 (review): the stub must match BOTH regexes — `_CANCEL_DENIED_RE` *and*
    `_RATE_LIMIT_RE` — or the `and not _RATE_LIMIT_RE.search(detail)` guard is never
    the deciding operand: `…secondary rate limit (HTTP 403)` matches the rate-limit
    pattern ONLY, so deleting the guard left this test green. The input below is the
    real overlapping shape, and with it the guard is load-bearing (verified: drop the
    guard and this test fails).
    """
    monkeypatch.setattr(R, "gh_json", _api())
    monkeypatch.setattr(R, "_sleep",
                        lambda s: pytest.fail("a refused cancel has no settle loop"))
    verbs: list[str] = []
    monkeypatch.setattr(R, "_run", _verbs(
        verbs, cancel_rc=1,
        cancel_stderr="gh: Forbidden (HTTP 403): You have exceeded a secondary "
                      "rate limit — please wait before retrying"))
    detail = "gh: Forbidden (HTTP 403): You have exceeded a secondary rate limit"
    assert R._CANCEL_DENIED_RE.search(detail), "the stub must look like a denial …"
    assert R._RATE_LIMIT_RE.search(detail), "… AND like a rate limit, or the guard is inert"
    assert R._rerun("o/r", run(1, "ci.yml", "pending"), 1,
                    expected_head="a" * 40) == "deferred", (
        "a rate-limited 403 clears itself; it is a deferral, not a credential defect")
    assert verbs == ["cancel"]


def test_a_denied_cancel_ESCALATES_and_is_named_in_the_record(monkeypatch):
    """⭐ P1 #10763, the half that actually fails open: the exit code.

    `deferred` is exempted by the escalation predicate in `report`, so the state must
    NOT carry that exemption. 'denied' carries its own `credential_denied` flag
    instead — nameable in `--json`, and not exempt — so the pass exits 1 and
    `ci-check-repair-local.sh` takes over.
    """
    monkeypatch.setattr(R, "_rerun", lambda *a, **k: "denied")
    plan = _plan(absent=["docs"], rerun=[run(42, "ci.yml", "completed", "failure")])
    code, recs = R.report([plan], apply=True, trigger_ok=True, repo="o/r", cooldown_min=60)
    row = recs[0]["rerun"][0]
    assert code == 1, "a permission refusal must escalate, not report a discharged window"
    assert row["ok"] is False
    assert row.get("credential_denied") is True, "the cause must be named in the record"
    assert "deferred" not in row, "'denied' must never be filed as a skip"


def test_a_cancel_that_never_settles_is_not_followed_by_a_rerun(monkeypatch):
    """The settle wait is bounded, and an UNOBSERVED settle is SKIPPED, not guessed.

    ⭐ P2 (review): the cancel POST was ACCEPTED, so the run IS cancelled and gets no
    replacement attempt — the state is `cancel_unobserved` ('the cancel landed, the
    rerun did not'), NOT `deferred` ('nothing was changed'). It is still a SKIP: an
    observation that never landed is not "a PR needs a repair this credential cannot
    perform", and the next window re-runs the now-cancelled run as the next attempt.
    """
    monkeypatch.setattr(R, "_sleep", lambda s: None)
    verbs: list[str] = []
    monkeypatch.setattr(R, "_run", _verbs(verbs))
    monkeypatch.setattr(R, "gh_json", _api(settle="in_progress"))
    assert R._rerun("o/r", run(1, "ci.yml", "pending"), 1,
                    expected_head="a" * 40) == "cancel_unobserved"
    assert verbs == ["cancel"], "an unobserved settle must not be re-run blindly"


def test_a_post_cancel_budget_exhaustion_is_not_the_pre_cancel_skip(monkeypatch):
    """⭐ P2 (review): `no_budget` was ONE state with two opposite meanings.

    Checked BEFORE the POST it means nothing was cancelled; reached INSIDE the settle
    loop (the budget can be spent by a read the loop itself awaited) it means the cancel
    ALREADY LANDED and only the observation ran out. `_cancel` answered 'no_budget' for
    both, so the caller could not tell a pass that changed nothing from one that had
    just cancelled a run and left it with no replacement attempt.

    Discriminating: pre-fix this returns 'no_budget', indistinguishable from the
    pre-POST skip — and the poll count below is what makes the IN-LOOP branch itself
    load-bearing, because both it and the loop fallthrough answer 'cancel_unobserved'.
    """
    monkeypatch.setattr(R, "_sleep", lambda s: None)
    verbs: list[str] = []
    monkeypatch.setattr(R, "_run", _verbs(verbs))
    budget = R.CANCEL_SETTLE_PASS_BUDGET_S
    # Spend the remainder on the FIRST settle poll, so the loop's own budget check trips
    # on the next iteration — AFTER the cancel POST has been accepted.
    monkeypatch.setattr(R, "_CANCEL_SETTLE_SPENT_S", budget - 0.5)
    seen = {"runs": 0}  # the pre-act re-measurement, then one read per settle poll

    def fake(a, **_kw):
        path = a[-1] if a else ""
        if a[:1] == ["pr"]:
            return {"headRefOid": "a" * 40}
        if "jobs" in path:
            return {"total_count": 0}
        seen["runs"] += 1
        if seen["runs"] > 1:
            # A read INSIDE the settle loop is slow enough to spend the pass's remaining
            # budget — real wall clock, which is what `_cancel` compares against. It is
            # keyed on the PRE-ACT read's count, not on the argv shape: every `gh_json`
            # call here passes the API path as the LAST element, so a predicate on
            # `a[-2]` (always the literal "api") can never fire.
            R._CANCEL_SETTLE_SPENT_S = budget + 1.0
        return {"status": "queued", "run_attempt": 1}

    monkeypatch.setattr(R, "gh_json", fake)
    assert R._cancel("o/r", run(1, "ci.yml", "pending")) == "cancel_unobserved"
    assert verbs == ["cancel"], "the POST was issued before the budget ran out"
    assert seen["runs"] == 2, (
        "the in-loop budget check must STOP the poll: one pre-act read plus exactly one "
        f"settle read, not all {R._CANCEL_SETTLE_POLLS} polls falling through to the "
        "same state")


def test_a_run_whose_jobs_appear_before_the_cancel_is_never_cancelled(monkeypatch):
    """⭐ P1 (review): the cancel rests on a `job_count` measured at the START of the
    pass and cached for all of it, while `build_plans` scans every PR before `report`
    acts. The module's OWN floor comment records that the class is known to contain
    runs that would have created jobs on their own — so a measurement artifact was
    being acted on destructively.

    Discriminating: pre-fix the cancel is issued from the stale classification.
    """
    monkeypatch.setattr(R, "_sleep", lambda s: pytest.fail("must not settle-wait"))
    verbs: list[str] = []
    monkeypatch.setattr(R, "_run", _verbs(verbs))
    # The classification said 0 jobs; the jobs endpoint says 4 AT CANCEL TIME.
    monkeypatch.setattr(R, "gh_json", _api(jobs=4))
    assert R._rerun("o/r", run(1, "ci.yml", "pending"), 1,
                    expected_head="a" * 40) == "target_moved"
    assert verbs == [], (
        "cancelling a run that has 4 jobs destroys work — the safety property must be "
        "measured at the moment of action, not at the start of the pass")


def test_a_run_that_completed_before_the_cancel_is_never_cancelled(monkeypatch):
    """The same hazard in its worst form: a run that FINISHED in the window.

    Pre-fix `_cancel` returned True for an already-`completed` run, so a successful
    verdict was converted into a fresh attempt and the record claimed
    `cancelled_first: True` for a cancel that did not happen.
    """
    monkeypatch.setattr(R, "_sleep", lambda s: pytest.fail("must not settle-wait"))
    verbs: list[str] = []
    monkeypatch.setattr(R, "_run", _verbs(verbs))
    monkeypatch.setattr(R, "gh_json", _api(status="completed", jobs=0))
    assert R._rerun("o/r", run(1, "ci.yml", "pending"), 1,
                    expected_head="a" * 40) == "target_moved"
    assert verbs == []


def test_an_unreadable_pre_act_read_refuses_the_cancel(monkeypatch):
    """A gate that cannot be read refuses the destructive act — it never guesses."""
    monkeypatch.setattr(R, "_sleep", lambda s: pytest.fail("must not settle-wait"))
    verbs: list[str] = []

    def raise_it(a, **k):
        if a[:1] == ["pr"]:  # the HEAD re-read is fine — the pre-ACT gate is what fails
            return {"headRefOid": "a" * 40}
        raise R.MeasureError("RATE-LIMITED (not empty): refused")

    monkeypatch.setattr(R, "gh_json", raise_it)
    monkeypatch.setattr(R, "_run", _verbs(verbs))
    assert R._rerun("o/r", run(1, "ci.yml", "pending"), 1,
                    expected_head="a" * 40) == "deferred"
    assert verbs == [], "an unreadable safety surface must not authorise a cancel"


def test_a_peer_rerun_inside_the_pass_window_is_never_cancelled(monkeypatch):
    """⭐ P2 (review): the act-time re-measurement re-read the run's STATUS and JOB
    COUNT but DISCARDED its attempt identity.

    A fresh re-run attempt is `queued` with `0` jobs — identical to the stalled run on
    the two fields the act-time gate originally compared, so it was
    byte-indistinguishable from the stall the classification was made from. So a peer (or
    a human) re-running the same run id between `build_plans` and `report` acting was
    cancelled and re-run AGAIN: a normally-progressing attempt destroyed, a second
    attempt of the budget spent, and with `max_attempts = 3` the context escalated
    EXHAUSTED — the loop `attempt_age_seconds`/`_zero_job_stalled` abstain to prevent.

    Discriminating: pre-fix the cancel is issued (this stub's only difference from
    `test_a_queued_zero_job_run_is_cancelled_before_it_is_rerun` is the live attempt).
    """
    monkeypatch.setattr(R, "_sleep", lambda s: pytest.fail("must not settle-wait"))
    verbs: list[str] = []
    monkeypatch.setattr(R, "_run", _verbs(verbs))
    # Classification says attempt 1; the live run is attempt 2 with 0 jobs.
    monkeypatch.setattr(R, "gh_json", _api(status="queued", jobs=0, attempt=2))
    assert R._rerun("o/r", run(1, "ci.yml", "pending", attempt=1), 1,
                    expected_head="a" * 40) == "target_moved"
    assert verbs == [], (
        "a different attempt is a different measurement — cancelling it destroys an "
        "attempt that is only waiting for a runner slot")


def test_the_attempt_identity_is_read_before_the_cancel(monkeypatch):
    """A gate that cannot report the attempt is unreadable, not a licence to cancel."""
    monkeypatch.setattr(R, "_sleep", lambda s: pytest.fail("must not settle-wait"))
    verbs: list[str] = []

    def fake(a, **_kw):
        if a[:1] == ["pr"]:
            return {"headRefOid": "a" * 40}
        if "jobs" in a[-1]:
            return {"total_count": 0}
        return {"status": "queued"}          # no `run_attempt` in the payload

    monkeypatch.setattr(R, "gh_json", fake)
    monkeypatch.setattr(R, "_run", _verbs(verbs))
    assert R._cancel("o/r", run(1, "ci.yml", "pending")) == "unreadable"
    assert verbs == [], "an unverifiable attempt identity must not authorise a cancel"


def test_a_cancel_the_api_says_was_never_queued_falls_back_to_the_rerun(monkeypatch):
    """⭐ P2 (review): `409 Cannot cancel a workflow run that has not been queued yet`
    is the documented answer for the orphan shape this class targets (orgs/community
    #206436). Pre-fix it was a terminal 'failed' every window, with no alternative
    path and the stderr discarded, so the tool escalated the very PR it repairs.
    """
    monkeypatch.setattr(R, "_sleep", lambda s: None)
    verbs: list[str] = []
    monkeypatch.setattr(R, "_run", _verbs(
        verbs, cancel_rc=1,
        cancel_stderr="gh: Cannot cancel a workflow run that has not been queued yet. "
                      "(HTTP 409)"))
    monkeypatch.setattr(R, "gh_json", _api())
    assert R._rerun("o/r", run(1, "ci.yml", "pending"), 1,
                    expected_head="a" * 40) == "rerun_not_queued"
    assert verbs == ["cancel", "rerun"], (
        "the 409 means there is nothing to cancel, so the direct rerun is the "
        "remaining primitive — not a terminal failure")


def test_the_settle_polls_carry_no_retry_and_their_own_timeout(monkeypatch):
    """⭐ P2 (review): the settle polls used the DEFAULT `retries=2`, so ONE run's
    settle could cost ~8 minutes (4 polls x 3 attempts x `_run(timeout=120)` plus hint
    sleeps) against a 15-minute workflow — the in-code claim was false.
    """
    monkeypatch.setattr(R, "_sleep", lambda s: None)
    monkeypatch.setattr(R, "_run", _verbs([]))
    base = _api()
    seen: list[tuple[str, dict]] = []

    def fake(a, **k):
        seen.append((a[-1] if a else "", k))
        return base(a, **k)
    monkeypatch.setattr(R, "gh_json", fake)
    assert R._cancel("o/r", run(1, "ci.yml", "pending")) == "cancelled"
    runs_reads = [k for path, k in seen if path.endswith("actions/runs/1")]
    assert len(runs_reads) >= 2, "the pre-act read and the settle poll must both read the run"
    for _path, kwargs in seen:
        assert kwargs.get("retries") == 0, (
            f"a gate/settle read retried a refused read: {kwargs}")
    for kwargs in runs_reads[1:]:
        assert kwargs.get("timeout") == R._CANCEL_SETTLE_POLL_TIMEOUT_S, (
            f"a settle poll used the default 120 s timeout: {kwargs}")


def test_a_cancel_is_not_issued_when_the_pass_has_no_settle_budget_left(monkeypatch):
    """The per-pass budget is spent BEFORE the POST: cancelling without being able to
    observe the settle would destroy the attempt and then be refused a rerun."""
    monkeypatch.setattr(R, "_sleep", lambda s: pytest.fail("must not sleep"))
    verbs: list[str] = []
    monkeypatch.setattr(R, "_run", _verbs(verbs))
    monkeypatch.setattr(R, "gh_json", _api())
    monkeypatch.setattr(R, "_CANCEL_SETTLE_SPENT_S", R.CANCEL_SETTLE_PASS_BUDGET_S)
    assert R._cancel("o/r", run(1, "ci.yml", "pending")) == "no_budget"
    assert verbs == [], "nothing may be cancelled with no budget to re-establish a verdict"


def test_a_cancelled_rerun_reports_success_and_does_not_escalate(monkeypatch):
    """The report half: 'cancelled_rerun' is a SUCCESS, not a failure to escalate."""
    monkeypatch.setattr(R, "_rerun", lambda *a, **k: "cancelled_rerun")
    plan = _plan(absent=["python-ci-gate"],
                 rerun=[run(41523739094, "python-ci.yml", "pending")])
    code, recs = R.report([plan], apply=True, trigger_ok=True, repo="o/r", cooldown_min=60)
    assert recs[0]["rerun"][0]["ok"] is True
    assert recs[0]["rerun"][0]["cancelled_first"] is True
    assert code == 0, "a completed repair must not escalate"


def test_a_moved_target_is_recorded_as_a_skip_and_does_not_escalate(monkeypatch):
    """A run that started/completed while the pass ran is a SKIP — like 'head_moved'."""
    monkeypatch.setattr(R, "_rerun", lambda *a, **k: "target_moved")
    plan = _plan(absent=["python-ci-gate"],
                 rerun=[run(41523739094, "python-ci.yml", "pending")])
    code, recs = R.report([plan], apply=True, trigger_ok=True, repo="o/r", cooldown_min=60)
    assert recs[0]["rerun"][0]["ok"] is False
    assert recs[0]["rerun"][0]["target_moved"] is True
    assert code == 0, "the run repaired itself — escalating would be a false alarm"


def test_a_409_cancel_is_named_in_the_record(monkeypatch):
    """The direct-rerun fallback is a success, and the record says WHY (never a clean
    `cancelled_first` it did not earn)."""
    monkeypatch.setattr(R, "_rerun", lambda *a, **k: "rerun_not_queued")
    plan = _plan(absent=["python-ci-gate"],
                 rerun=[run(41523739094, "python-ci.yml", "pending")])
    code, recs = R.report([plan], apply=True, trigger_ok=True, repo="o/r", cooldown_min=60)
    entry = recs[0]["rerun"][0]
    assert entry["ok"] is True and entry["cancel_refused_409"] is True
    assert "cancelled_first" not in entry
    assert code == 0


def test_a_cancel_skipped_for_want_of_budget_defers_the_rerun(monkeypatch):
    """⭐ P2 (review): the half the suite never covered — `_cancel` → `_rerun`.

    `_cancel`'s own contract calls 'no_budget' a SKIP (*"Skip; the next window has a
    fresh budget"*), but `_rerun` folded it — with the unobserved-settle state, 'refused'
    and 'unreadable' — into 'failed'. With the shipped budgets a pass whose settles run to
    their bound exhausts the cancel budget on its SECOND run, so every later run was
    reported as needing a human. The pre-POST skip is `deferred`; the post-POST one is
    `cancel_unobserved`, which the record names as an ACCEPTED cancel whose effect was
    never observed.
    """
    monkeypatch.setattr(R, "_sleep", lambda s: pytest.fail("must not sleep"))
    verbs: list[str] = []
    monkeypatch.setattr(R, "_run", _verbs(verbs))
    monkeypatch.setattr(R, "gh_json", _api())
    monkeypatch.setattr(R, "_CANCEL_SETTLE_SPENT_S", R.CANCEL_SETTLE_PASS_BUDGET_S)
    assert R._rerun("o/r", run(1, "ci.yml", "pending"), 1,
                    expected_head="a" * 40) == "deferred"
    assert verbs == [], "nothing may be cancelled with no budget to re-establish a verdict"


def test_a_deferred_repair_is_recorded_as_a_skip_and_does_not_escalate(monkeypatch):
    """... and the report half: a deferral must not become exit 1.

    Exit 1's documented meaning is *"some PR needs a repair this credential cannot
    perform … or a repair has spent its attempt budget and must be escalated to a
    human"*. 'Nothing was done this window' is neither, and it is reachable in the
    shipped configuration.
    """
    monkeypatch.setattr(R, "_rerun", lambda *a, **k: "deferred")
    plan = _plan(absent=["python-ci-gate"],
                 rerun=[run(41523739094, "python-ci.yml", "pending")])
    code, recs = R.report([plan], apply=True, trigger_ok=True, repo="o/r", cooldown_min=60)
    entry = recs[0]["rerun"][0]
    assert entry["ok"] is False and entry["deferred"] is True
    assert "cancel_accepted" not in entry and "cancel_landed" not in entry, (
        "a deferral cancels NOTHING — the record must not claim a cancel was even "
        "accepted")
    assert code == 0, "a skip must stay a skip: nothing was done, so nothing escalated"


def test_a_cancel_accepted_but_unobserved_is_recorded_as_ACCEPTED_not_LANDED(monkeypatch):
    """⭐ P1 (review): an ACCEPTED cancel POST is not an APPLIED cancel.

    `deferred` was documented as "nothing was changed", which is FALSE for a cancel whose
    POST was accepted and whose settle was never observed; but the opposite claim is not
    measured either. A 2xx means the request was ACCEPTED — GitHub documents that a
    cancel can simply not take effect, which is why `/force-cancel` exists for a run "not
    responding to POST …/cancel" — so `completed` is the only evidence of a landed
    cancel, and this state exists precisely because that read never succeeded.

    The record therefore carries `cancel_accepted` (what was measured) and NEVER
    `cancel_landed` (what was not), and `skipped` rather than `deferred` so the entry
    never reads as a clean no-op. Still a SKIP: bounded by the next window, which acts on
    what is then true.

    Discriminating: pre-fix there is no such state, so the entry carries `deferred` with
    no effect key and — because the escalation predicate only excludes `deferred` by name
    — a plain `cancel_unobserved` escalates to exit 1.
    """
    monkeypatch.setattr(R, "_rerun", lambda *a, **k: "cancel_unobserved")
    plan = _plan(absent=["python-ci-gate"],
                 rerun=[run(41523739094, "python-ci.yml", "pending")])
    code, recs = R.report([plan], apply=True, trigger_ok=True, repo="o/r", cooldown_min=60)
    entry = recs[0]["rerun"][0]
    assert entry["ok"] is False
    assert entry["cancel_accepted"] is True, (
        "the POST was ACCEPTED — the record must say that, and only that")
    assert "cancel_landed" not in entry, (
        "no `completed` was ever read, so nothing measured the cancel as APPLIED")
    assert entry["skipped"] is True, (
        "still a SKIP: the next window owns the replacement attempt")
    assert "deferred" not in entry, (
        "`deferred` means the pass changed NOTHING — a destructive act was requested here")
    assert code == 0, "an accepted cancel is bounded — this is not 'needs a human'"


def test_a_cancel_whose_effect_is_unknown_is_recorded_as_neither(monkeypatch):
    """⭐ P2 #10641 review: the third state — the POST was SENT and the ANSWER was lost.

    Neither claim is true: the run MAY be cancelled (`cancel_accepted` would overstate it
    too: no answer to the POST was ever read)
    and something MAY have changed (`deferred`'s bare form would understate it). Still a
    SKIP — a lost answer is not "a repair this credential cannot perform", and the next
    window re-reads the run and acts on what is then true.

    Discriminating: pre-fix the timeout path answered `refused` → `deferred`, so the
    entry carries no effect key at all and reads as a clean no-op.
    """
    monkeypatch.setattr(R, "_rerun", lambda *a, **k: "cancel_unknown")
    plan = _plan(absent=["python-ci-gate"],
                 rerun=[run(41523739094, "python-ci.yml", "pending")])
    code, recs = R.report([plan], apply=True, trigger_ok=True, repo="o/r", cooldown_min=60)
    entry = recs[0]["rerun"][0]
    assert entry["ok"] is False
    assert entry["cancel_effect_unknown"] is True, (
        "the POST was sent and the answer was lost — the record must say the effect is unknown")
    assert "cancel_landed" not in entry and "cancel_accepted" not in entry, (
        "the cancel MAY have landed — claiming it did is the same error in the other "
        "direction")
    assert entry["skipped"] is True and "deferred" not in entry, (
        "`deferred` claims nothing was changed, which is not known here either")
    assert code == 0, "unknown is bounded by the next window — this is not 'needs a human'"


def test_a_cancel_the_credential_cannot_perform_escalates_the_window(monkeypatch):
    """⭐ P2 #10641 review — the WHOLE path, not a stubbed `_rerun` return.

    `_cancel` → `_rerun` → `report` for an API that ANSWERS with a refusal (a `403` from
    a token without `actions: write`). The workflow's exit code is documented as
    "non-zero when a PR needs a repair this credential cannot perform. That is the signal
    the local fallback keys on" — so this must reach exit 1.

    Discriminating: pre-fix the whole chain produced `deferred` and exit 0, so every
    30-minute window reported the window as covered while the cancel was never attempted
    by anyone — the one outcome the local fallback exists to prevent.
    """
    monkeypatch.setattr(R, "gh_json", _api())
    monkeypatch.setattr(R, "_sleep", lambda s: pytest.fail("a refused cancel never settles"))
    monkeypatch.setattr(R, "_run", _verbs([], cancel_rc=1,
                                           cancel_stderr="Resource not accessible by integration"))
    plan = _plan(absent=["python-ci-gate"], head="a" * 40,
                 rerun=[run(41523739094, "python-ci.yml", "pending")])
    code, recs = R.report([plan], apply=True, trigger_ok=True, repo="o/r", cooldown_min=60)
    entry = recs[0]["rerun"][0]
    assert entry["ok"] is False and "deferred" not in entry, (
        "the API answered — this is not a deferral")
    assert code == 1, (
        "a repair this credential cannot perform must engage the local fallback")


# ── 16. the two per-pass resets and the `no_verdict` record key are load-bearing ──
#
# MUTATION-MEASURED: deleting `_READ_CACHE.clear()`, deleting `_JOB_READ_FAILURES = 0`,
# or replacing `"no_verdict": list(p.no_verdict)` with `[]` each left the suite's
# failure set BYTE-IDENTICAL. Both resets are the scope that stops a verdict about a
# head that has moved (and a leaked failure count would abstain on EVERY job count for
# every later pass, silently disabling the whole class), and the record key is the
# machine-readable half of a notice deliberately sent to stderr.

def test_every_pass_clears_the_read_cache_and_the_job_read_failure_count(monkeypatch):
    monkeypatch.setattr(R, "open_prs", lambda repo, include_drafts: [])
    monkeypatch.setattr(R, "_READ_CACHE", {("checks", "o/r", "a" * 40): [{"stale": True}]})
    monkeypatch.setattr(R, "_JOB_READ_FAILURES", R.JOB_READ_FAILURE_LIMIT)
    monkeypatch.setattr(R, "_CANCEL_SETTLE_SPENT_S", R.CANCEL_SETTLE_PASS_BUDGET_S)
    monkeypatch.setattr(R, "_PASS_DEADLINE", 0.0)
    R.build_plans("o/r", ["python-ci-gate"], EMITTERS, False, 3)
    assert R._READ_CACHE == {}, (
        "a cached read that survives a pass describes a head that may have moved")
    assert R._JOB_READ_FAILURES == 0, (
        "a leaked count makes EVERY later pass abstain on EVERY job count")
    assert R._CANCEL_SETTLE_SPENT_S == 0.0, (
        "a leaked settle spend silently disables the cancel on every later pass")
    assert R._PASS_DEADLINE is not None and R.time.time() < R._PASS_DEADLINE, (
        "the wall-clock budget must be RE-ARMED per pass, not inherited from the last"
        " one — a spent deadline turns every later refusal into an instant exit 2")


def test_the_json_record_carries_no_verdict_for_machine_readers(monkeypatch, capsys, tmp_path):
    """The stderr notice is for a human; `--json` consumers get THIS key or nothing."""
    monkeypatch.setattr(R, "open_prs", lambda repo, include_drafts: [pr(1)])
    monkeypatch.setattr(R, "check_runs_at", lambda repo, head: [])
    monkeypatch.setattr(R, "runs_at_head",
                        lambda repo, head: [aged_run(41480218593, jobs=0, age_s=5700.0)])
    rec = tmp_path / "required-contexts.json"
    rec.write_text(json.dumps({
        "required_contexts": ["python-ci-gate"],
        "emitters": {"python-ci-gate": [["python-ci.yml", "python-ci-gate"]]},
    }))
    assert R.main(["--repo", "o/r", "--no-live", "--no-queue-health", "--json",
                   "--record", str(rec)]) == 0
    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert summary["prs"][0]["no_verdict"][0]["run"] == 41480218593
    assert "NO-VERDICT-WILL-ARRIVE" in captured.err
    assert "NO-VERDICT-WILL-ARRIVE" not in captured.out, (
        "the notice must never enter the --json stream (it would break json.load in "
        "tools/ci-check-repair-local.sh and the workflow's step summary)")


# ── 17. the remedy lines must reach a human IN PRODUCTION (#10766) ──────────
#
# Both callers pass `--json` — `.github/workflows/ci-check-repair.yml:170`
# (`--apply --json`) and `tools/ci-check-repair-local.sh` (`--json`, its stderr
# appended to the durable log). While the STARVED and needs-credential lines sat
# inside the `else` branch, NEITHER ever printed there: the two states that say
# "no per-PR repair can help you" were the two whose remedy text was withheld.
# These tests drive the real entry point in the mode production uses, so moving
# the prints back into the format branch fails them.

def test_the_starved_remedy_prints_under_json_too(monkeypatch, capsys, tmp_path):
    """#10766: `--json` is the only mode both callers use — the remedy must survive it."""
    monkeypatch.setattr(R, "read_queue_health", lambda repo: _flood_health())
    monkeypatch.setattr(R, "open_prs", lambda repo, include_drafts: [pr(1)])
    monkeypatch.setattr(R, "check_runs_at", lambda repo, head: [])
    monkeypatch.setattr(R, "runs_at_head", lambda repo, head: [run(10, "ci.yml", "queued")])

    assert R.main(["--repo", "o/r", "--no-live", "--json",
                   "--record", str(_flood_record(tmp_path))]) == 0
    captured = capsys.readouterr()
    summary = json.loads(captured.out)      # the stdout stream stays ONE document
    assert summary["starved"] == 1
    assert "STARVED" in captured.err, (
        "the STARVED sentence is the only place the unjam remedy is named, and "
        "`--json` is the only mode both production callers use")
    assert "ci_queue_health.py" in captured.err, (
        "naming the state without the action leaves the reader with nothing to do")
    assert "STARVED" not in captured.out, (
        "it must never enter the --json stream (json.load in tools/ci-check-repair-local.sh)")


def test_the_credential_remedy_prints_under_json_too(monkeypatch, capsys, tmp_path):
    """#10766, the exit-1 half: the exit code alone never says what to do about it."""
    # NOT `can_trigger_events()` from the environment: the point is the automatic
    # token, which is what CI runs under, and a stray env var must not decide this.
    monkeypatch.setattr(R, "can_trigger_events", lambda: False)
    monkeypatch.setattr(R, "open_prs", lambda repo, include_drafts: [pr(1)])
    monkeypatch.setattr(R, "check_runs_at", lambda repo, head: [])
    monkeypatch.setattr(R, "runs_at_head", lambda repo, head: [])
    rec = tmp_path / "required-contexts.json"
    rec.write_text(json.dumps({
        "required_contexts": ["docs"],
        "emitters": {"docs": [["ci.yml", "docs"]]},
    }))

    assert R.main(["--repo", "o/r", "--no-live", "--no-queue-health", "--json",
                   "--apply", "--record", str(rec)]) == 1
    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert summary["prs"][0]["needs_user_actor"] is True
    assert "credential that can trigger events" in captured.err, (
        "exit 1 means 'a user credential is needed'; without this line the caller "
        "sees only a non-zero exit")
    assert "credential that can trigger events" not in captured.out
