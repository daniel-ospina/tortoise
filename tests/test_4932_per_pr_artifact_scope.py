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

Every check reads the EFFECTIVE environment (`workflow -> job -> step`, which is
the order GitHub resolves), not just the step's own `env:`. A flag set one level
up is inherited by the step that runs pytest and silences it just as surely, so a
step-only read passes while the contract is dead — and, in the other direction,
false-REDs a job-level flag that is functionally correct.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit

import yaml

ROOT = Path(__file__).resolve().parent.parent
CI = ROOT / ".github" / "workflows" / "ci.yml"
MONITOR = ROOT / ".github" / "workflows" / "welcome-e2e-monitor.yml"
E2E = ROOT / "tests" / "e2e" / "test_welcome_page.py"

FLAG = "WELCOME_E2E_PER_PR"
SUITE = "test_welcome_page.py"


def _doc(workflow: Path) -> dict:
    doc = yaml.safe_load(workflow.read_text()) or {}
    assert doc.get("jobs"), f"{workflow.name} declares no jobs"
    return doc


def _effective_env(doc: dict, job: dict, step: dict) -> dict:
    """The env GitHub actually gives `step`: workflow, then job, then step."""
    env: dict = {}
    for layer in (doc.get("env"), job.get("env"), step.get("env")):
        if isinstance(layer, dict):
            env.update(layer)
    return env


def _is_deployed(url: str) -> bool:
    """True when `url` is not a loopback target.

    Host-based for the same reason `_targets_production` in the e2e module is:
    a substring test reads `http://[::1]:8788/` and `HTTP://127.0.0.1:8788/` as
    deployed while reading `http://127.0.0.1.example.com/` as local — wrong in
    both directions, at the one site whose job is to catch a misdirected target.
    """
    host = (urlsplit(url).hostname or "").lower()
    if host == "localhost":
        return False
    try:
        return not ipaddress.ip_address(host).is_loopback
    except ValueError:
        return True


def _suite_steps(
    doc: dict, job_id: str | None = None
) -> Iterator[tuple[dict, dict]]:
    """`(job, step)` for every step that EXECUTES the welcome suite.

    Selected on `run` alone. Requiring an `env:` block would make a step that
    runs the suite without one invisible — it would fetch production unflagged
    while this guard reported clean.

    `job_id` scopes the scan; `None` means every job (the monitor has no single
    named job to pin).
    """
    jobs = (
        [(job_id, doc["jobs"][job_id])] if job_id else list(doc["jobs"].items())
    )
    for _jid, job in jobs:
        for step in job.get("steps") or []:
            if SUITE in (step.get("run") or ""):
                yield job, step


def test_per_pr_job_marks_the_running_step() -> None:
    doc = _doc(CI)
    assert "welcome-e2e" in doc["jobs"], "ci.yml has no `welcome-e2e` job"
    steps = list(_suite_steps(doc, "welcome-e2e"))
    assert steps, f"no step anywhere runs {SUITE}"
    for job, step in steps:
        env = _effective_env(doc, job, step)
        assert env.get(FLAG) == "1", (
            f"step {step.get('name')!r} runs the welcome suite without {FLAG}=1, "
            f"so it fetches PRODUCTION's onboarding document on every PR (#4932)"
        )


def test_monitor_keeps_targeting_production() -> None:
    """The monitor is the only remaining home of the deployed-artifact contract."""
    doc = _doc(MONITOR)
    steps = list(_suite_steps(doc))
    assert steps, (
        f"welcome-e2e-monitor.yml no longer runs {SUITE}, so skipping it per-PR "
        f"would leave the deployed artifact asserted nowhere (#4932)"
    )
    for job in doc["jobs"].values():
        for step in job.get("steps") or []:
            env = _effective_env(doc, job, step)
            assert FLAG not in env, (
                f"welcome-e2e-monitor.yml resolves {FLAG} for step "
                f"{step.get('name')!r} (workflow/job/step env) — inheriting the "
                f"per-PR flag silences the deployed-artifact assertion on every "
                f"surface (#4932)"
            )
            url = env.get("ONBOARDING_SKILL_URL")
            if url:
                assert _is_deployed(url), (
                    "the monitor must target the deployed host, not a local preview"
                )


def test_the_e2e_module_skips_only_in_per_pr_mode() -> None:
    """The skip is guarded by the flag AND a deployed target, so the monitor runs it."""
    src = E2E.read_text()
    assert f'os.environ.get("{FLAG}") == "1"' in src, (
        f"the e2e module must read {FLAG}; without it the skip either never "
        f"applies or always does"
    )
    # The whole guard, not just the `pytest.skip(` call: the module has other
    # skip sites, so a bare `"pytest.skip(" in src` is true even with this one
    # deleted — an assertion that can never fail.
    assert (
        "if _PER_PR and _targets_production(ONBOARDING_SKILL_URL):\n"
        "        pytest.skip("
    ) in src, (
        "the skip must be guarded by BOTH the flag and a deployed target — "
        "guarding on the flag alone would also skip a deliberate local URL"
    )
