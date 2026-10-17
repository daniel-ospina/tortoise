"""#4932: the per-PR welcome suite must not grade a branch against PRODUCTION.

`tests/e2e/test_welcome_page.py::test_onboarding_instructions_serves_markdown`
fetches `ONBOARDING_SKILL_URL`, which defaults to the deployed
`app.premiselabs.co` copy. The per-PR `welcome-e2e` job never set that variable,
so every PR was graded against whatever was last deployed instead of against the
branch: it could pass while the PR's own copy was broken, and it could red a PR
for a reason that belonged to the deploy rather than to the change.

The fix is a scope split, and this module pins BOTH halves of it — because the
split fails open in either direction. If the per-PR flag is dropped, the
production fetch comes back silently. If the flag leaks into the monitor, the
deployed-artifact assertion stops running anywhere and the surface is lost with
no red to show for it.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CI = ROOT / ".github" / "workflows" / "ci.yml"
MONITOR = ROOT / ".github" / "workflows" / "welcome-e2e-monitor.yml"
E2E = ROOT / "tests" / "e2e" / "test_welcome_page.py"

FLAG = "WELCOME_E2E_PER_PR"
SUITE = "test_welcome_page.py"


def _jobs(workflow: Path) -> dict:
    jobs = (yaml.safe_load(workflow.read_text()) or {}).get("jobs") or {}
    assert jobs, f"{workflow.name} declares no jobs"
    return jobs


def _suite_steps(jobs: dict, job_id: str) -> list[dict]:
    """The steps that actually EXECUTE the welcome suite.

    Scoped to the running step, not the job: a job-level `env:` would satisfy a
    job-level check while the step that runs the suite stayed unflagged.
    """
    steps = (jobs.get(job_id) or {}).get("steps") or []
    return [
        s for s in steps if SUITE in (s.get("run") or "") and isinstance(s.get("env"), dict)
    ]


def test_per_pr_job_marks_the_running_step() -> None:
    jobs = _jobs(CI)
    assert "welcome-e2e" in jobs, "ci.yml has no `welcome-e2e` job"
    steps = _suite_steps(jobs, "welcome-e2e")
    assert steps, f"no step in `welcome-e2e` runs {SUITE}"
    for step in steps:
        assert step["env"].get(FLAG) == "1", (
            f"step {step.get('name')!r} runs the welcome suite without {FLAG}=1, "
            f"so it fetches PRODUCTION's onboarding document on every PR (#4932)"
        )


def test_monitor_keeps_targeting_production() -> None:
    """The monitor is the only remaining home of the deployed-artifact contract."""
    jobs = _jobs(MONITOR)
    steps = [s for job in jobs.values() for s in (job.get("steps") or [])]
    assert any(SUITE in (s.get("run") or "") for s in steps), (
        f"welcome-e2e-monitor.yml no longer runs {SUITE}, so skipping it per-PR "
        f"would leave the deployed artifact asserted nowhere (#4932)"
    )
    for step in steps:
        env = step.get("env") or {}
        assert FLAG not in env, (
            f"welcome-e2e-monitor.yml sets {FLAG} on step "
            f"{step.get('name')!r} — inheriting the per-PR flag silences the "
            f"deployed-artifact assertion on every surface (#4932)"
        )
        url = env.get("ONBOARDING_SKILL_URL")
        if url:
            assert "://127.0.0.1" not in url and "://localhost" not in url, (
                "the monitor must target the deployed host, not the local preview"
            )


def test_the_e2e_module_skips_only_in_per_pr_mode() -> None:
    """The skip is guarded by the flag AND a deployed target, so the monitor runs it."""
    src = E2E.read_text()
    assert f'os.environ.get("{FLAG}") == "1"' in src, (
        f"the e2e module must read {FLAG}; without it the skip either never "
        f"applies or always does"
    )
    assert "if _PER_PR and _targets_production(ONBOARDING_SKILL_URL):" in src, (
        "the skip must be guarded by BOTH the flag and a deployed target — "
        "guarding on the flag alone would also skip a deliberate local URL"
    )
    assert "pytest.skip(" in src
