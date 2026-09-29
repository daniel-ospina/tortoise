"""The schema↔app co-move window (#3627) — guard for the Supabase deploy path.

``.github/workflows/supabase-deploy.yml`` applies migrations, deploys the two
edge functions and, until #3627, STOPPED there: flipping the Fly app
(``deploy-hosted.yml``) was a SECOND operator dispatch. The tenancy rename
(``supabase/migrations/20260915000001_tenancy_team_to_org.sql``, #3543) is a
hard cut at the wire level — ``provision_team`` is dropped and recreated with
``p_org_id``, and PostgREST resolves RPCs by PARAMETER NAME — so no single app
revision is compatible with both schemas. The schema therefore always landed
alone, and the app was guaranteed stale for the whole inter-run window: every
tenant-scoped read returned 0 rows and provisioning failed closed.

The file's own header comment asserted the co-move step existed (``3. DISPATCH
deploy-hosted …``) while no such step did — which is why the gap read as closed.

These tests pin the properties that make the claim true, against the PARSED
document and the workflow's ACTUAL ``run:`` text (no duplicated copy to drift),
following this repo's existing workflow guards (``tests/test_deploy_workflow.py``,
``tests/test_ai_review_gate_contract.py``):

1. the app-flip dispatch EXISTS, is the LAST step of the ``deploy`` job, and
   shares that job with the applies — so a failed apply skips it via GitHub's
   implicit ``success()``;
2. it is reachable only from a ``workflow_dispatch`` and only under the same
   secret gate as the applies — it can never fire on a push, and never fire
   when the applies were skipped;
3. it FAILS CLOSED: a refused API call (`gh` non-zero), and a 2xx carrying a
   body this step cannot read, both exit non-zero. A dispatch that silently
   no-ops looks like a closed window while leaving the gap, which is the defect
   #3627 exists to remove. The RESPONSE BODY is not the contract, though —
   GitHub has answered this endpoint with `204 No Content` and with `200` +
   run details at different times, so an empty body is a SUCCESS (requiring a
   run id there would red every successful co-move run and send the operator to
   re-dispatch a deploy that already happened);
4. the credential it relies on is GRANTED (workflow-level
   ``permissions: actions: write`` + ``GH_TOKEN: ${{ github.token }}``): the
   repo default is ``read``, so without the grant the API answers 403.

The behavioural tests execute the workflow's own ``run:`` block with a stubbed
``gh`` on ``PATH`` — hermetic, no network, no workflow state touched.
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "supabase-deploy.yml"

# Step names, selected by NAME (never position): a rename or a deletion must
# fail loudly here rather than silently point these tests at another step.
_DISPATCH_STEP = "Dispatch deploy-hosted (the app flip)"
_REF_GUARD_STEP = "Refuse a non-main dispatch (the co-move is main-only)"
_APPLY_STEP = "Apply migrations"
_FUNCTION_STEPS = (
    "Deploy waitlist-subscribe edge function",
    "Deploy tenant-provision edge function",
)

# The gate every apply step and the dispatch share. A dispatch that loses any
# clause of this can fire when the applies did not.
_APPLY_GATE = "github.event_name == 'workflow_dispatch' && env.SUPABASE_ACCESS_TOKEN != ''"

# GitHub's status-check functions. Their presence in `if:` would REPLACE the
# implicit success() ("A default status check of success() is applied unless
# you include one of these functions"), so the dispatch step must name none of
# them — that is what makes a failed apply skip it.
_STATUS_FUNCTIONS = ("always()", "failure()", "cancelled()", "success()")


@lru_cache(maxsize=1)
def _doc() -> dict:
    assert _WORKFLOW.is_file(), f"workflow not found: {_WORKFLOW}"
    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))


def _steps() -> list[dict]:
    return _doc()["jobs"]["deploy"]["steps"]


def _step(name: str) -> dict:
    for step in _steps():
        if step.get("name") == name:
            return step
    raise AssertionError(
        f"{name!r} is not a step of the `deploy` job in {_WORKFLOW.name} — "
        "the guard these tests express no longer has anything to guard"
    )


def _index(name: str) -> int:
    return [s.get("name") for s in _steps()].index(name)


# ── Behavioural harness: run the step's own `run:` block offline ───────────


def _run_step(
    name: str,
    tmp_path: Path,
    *,
    gh_stdout: str = "",
    gh_rc: int = 0,
    env_extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Execute the named step's run block with a stubbed ``gh`` on PATH.

    The stub logs its argv to ``tmp_path/gh-argv.log`` (so a test can assert the
    request the step actually makes, not the one its comments claim) and prints
    ``gh_stdout`` with exit status ``gh_rc``.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    argv_log = tmp_path / "gh-argv.log"
    gh = bindir / "gh"
    gh.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' {shlex.quote(gh_stdout)}\n"
        f"printf '%s\\n' \"$@\" >> {shlex.quote(str(argv_log))}\n"
        f"exit {gh_rc}\n",
        encoding="utf-8",
    )
    gh.chmod(0o755)
    script = tmp_path / "run.sh"
    script.write_text(_step(name)["run"], encoding="utf-8")

    env = dict(os.environ)
    env.update(
        {
            "PATH": f"{bindir}{os.pathsep}{env['PATH']}",
            "GH_TOKEN": "stub-token-never-used",
            "REPO_SLUG": "daniel-ospina/tortoise",
            "APP_REF": "main",
            "RUN_REF": "refs/heads/main",
        }
    )
    env.update(env_extra or {})
    return subprocess.run(["bash", str(script)], capture_output=True, text=True, env=env)


def _argv(tmp_path: Path) -> str:
    log = tmp_path / "gh-argv.log"
    return log.read_text(encoding="utf-8") if log.is_file() else ""


@pytest.fixture(scope="module", autouse=True)
def _require_tools() -> None:
    """`bash` and `jq` are what the run blocks execute (and what the ubuntu
    runner provides). Missing tools must fail, never silently skip."""
    missing = [t for t in ("bash", "jq") if shutil.which(t) is None]
    if missing:
        pytest.fail(f"the co-move guards need {missing} on PATH")


# ── 1. The dispatch exists, last, and shares the applies' job ──────────────


def test_app_flip_is_dispatched_and_is_the_last_step_of_the_deploy_job() -> None:
    """#3627's whole deliverable: the app flip must be a step of THIS workflow.

    Fails when the dispatch step is deleted (the regression this issue is), and
    when any step is appended AFTER it — a later step could run after the app
    flipped, and the co-move promise ('the last thing this run does is close the
    window') would be prose again.
    """
    names = [s.get("name") for s in _steps()]
    assert _DISPATCH_STEP in names, (
        f"{_WORKFLOW.name} no longer dispatches the app flip — the schema would "
        f"land alone and the Fly app stay stale (#3627)"
    )
    assert names[-1] == _DISPATCH_STEP, (
        f"the app-flip dispatch must be the LAST step of the `deploy` job, "
        f"found {names[-1]!r} after it"
    )


def test_app_flip_shares_the_job_with_every_apply_step() -> None:
    """Implicit `success()` is per-JOB: the dispatch must sit after the applies
    in the SAME job, or the ordering guarantee is not expressed at all.

    Fails when the dispatch is moved into its own job without `needs` (a
    separate job starts unconditionally) or ahead of an apply.
    """
    dispatch = _index(_DISPATCH_STEP)
    for name in (_APPLY_STEP, *_FUNCTION_STEPS):
        assert _index(name) < dispatch, (
            f"{name!r} runs after the app-flip dispatch — the app would flip "
            f"onto a schema that had not been applied yet (#3627)"
        )


def test_dispatch_if_relies_on_the_implicit_success_check() -> None:
    """A failed apply must SKIP the dispatch (implicit success()).

    Fails when the step's `if:` gains `always()`/`failure()`/`cancelled()`
    (which replaces the default status check) or `continue-on-error: true`
    (which would let the fail-closed `exit 1` colour nothing).
    """
    step = _step(_DISPATCH_STEP)
    condition = step.get("if", "")
    for fn in _STATUS_FUNCTIONS:
        assert fn not in condition, (
            f"the dispatch's `if:` names {fn}, which REPLACES GitHub's implicit "
            f"success() — the app could then flip with a failed apply (#3627): "
            f"{condition!r}"
        )
    assert not step.get("continue-on-error"), (
        "`continue-on-error: true` on the dispatch makes its fail-closed exit "
        "non-blocking — the window would look closed while the app stayed stale"
    )


# ── 2. Reachable only from a dispatch, under the applies' own gate ─────────


def test_dispatch_is_unreachable_from_a_push_and_uses_the_applies_gate() -> None:
    """A push must never deploy, and a no-op job must never dispatch.

    Fails when the dispatch's `if:` diverges from the applies' gate (e.g. it
    loses `env.SUPABASE_ACCESS_TOKEN != ''`, so it fires while the applies were
    SKIPPED for a missing secret) or when the `deploy` job's own gate stops
    being workflow_dispatch-only.
    """
    job = _doc()["jobs"]["deploy"]
    assert job.get("if") == "github.event_name == 'workflow_dispatch'", (
        f"the `deploy` job must stay workflow_dispatch-only (#771 flip gating): "
        f"{job.get('if')!r}"
    )
    assert _step(_DISPATCH_STEP).get("if") == _APPLY_GATE, (
        f"the dispatch must carry the applies' EXACT gate, else it can fire "
        f"when the applies were skipped: {_step(_DISPATCH_STEP).get('if')!r}"
    )
    for name in (_APPLY_STEP, *_FUNCTION_STEPS):
        assert _step(name).get("if") == _APPLY_GATE, (
            f"the gate expression moved on {name!r}: the dispatch's pin is stale"
        )


def test_trigger_set_has_no_second_dispatch_lane() -> None:
    """The trigger set is `workflow_dispatch` + the push that only records (#771).

    Fails when a `repository_dispatch`/`workflow_call`/`schedule` trigger is
    added, which would add a lane into the `deploy` job other than the operator
    dispatch the flip sequence is built on.
    """
    triggers = _doc()[True]  # YAML parses the bare `on:` key as boolean True
    assert set(triggers) == {"push", "workflow_dispatch"}, (
        f"unexpected trigger set on the schema deploy: {sorted(triggers)}"
    )


# ── 3. The credential the dispatch depends on ─────────────────────────────


def test_app_flip_dispatch_is_granted_actions_write() -> None:
    """The dispatch authenticates with the workflow token, which needs
    `actions: write` (the repo default is `read`, so the default alone 403s).

    Fails when the `permissions:` block is dropped or downgraded, when a PAT
    secret replaces `github.token`, or when `GH_TOKEN` is not bound at all.
    """
    perms = _doc().get("permissions") or {}
    assert perms.get("actions") == "write", (
        "the app-flip dispatch calls the workflow-dispatches endpoint, which "
        "requires 'Actions' write; the repo default workflow permission is "
        f"`read`, so this grant is load-bearing (#3627): {perms!r}"
    )
    assert perms.get("contents") == "read", (
        "specifying any permission sets every unspecified one to `none` — "
        f"`contents: read` must be spelled out or checkout loses its token: {perms!r}"
    )
    env = _step(_DISPATCH_STEP).get("env") or {}
    assert env.get("GH_TOKEN") == "${{ github.token }}", (
        f"the dispatch must authenticate as the workflow token, not a PAT "
        f"secret (a second long-lived credential on the flip path): {env!r}"
    )
    assert env.get("APP_REF") == "main", (
        f"the app half is dispatched at `main`, which is what makes the "
        f"main-only ref guard load-bearing: {env!r}"
    )


def test_non_main_run_is_refused_before_any_apply() -> None:
    """The schema comes from this run's ref and the app from `main`, so the
    pair matches only when this run is main.

    Fails when the guard is deleted, moved after an apply step, or stops
    testing `refs/heads/main`.
    """
    assert _steps()[0].get("name") == _REF_GUARD_STEP, (
        "the main-only refusal must be the FIRST step of the `deploy` job, so a "
        f"non-main dispatch writes nothing: first step is {_steps()[0].get('name')!r}"
    )
    assert "refs/heads/main" in _step(_REF_GUARD_STEP)["run"], (
        "the ref guard no longer tests for refs/heads/main"
    )
    # The guard's INPUT, not just its logic: an unbound RUN_REF expands to the
    # empty string, which the test above reads as "not main" — so a dropped
    # binding reds every dispatch rather than silently allowing a branch run.
    assert (_step(_REF_GUARD_STEP).get("env") or {}).get("RUN_REF") == "${{ github.ref }}", (
        "the ref guard must read the run's own ref from RUN_REF: "
        f"{_step(_REF_GUARD_STEP).get('env')!r}"
    )


# ── 3b. Behaviour: the dispatch fails closed, and the guard refuses ────────


def test_dispatch_fails_closed_when_the_api_refuses(tmp_path: Path) -> None:
    """State that fails it: `gh` exits non-zero (what an `actions: read` token
    produces — 403). Reachable: the stub's exit status IS that state."""
    proc = _run_step(_DISPATCH_STEP, tmp_path, gh_rc=1)
    assert proc.returncode != 0, (
        "a refused dispatch exited 0 — the run would report a closed window "
        f"while the app stayed stale (#3627)\n{proc.stdout}"
    )
    assert "has NOT flipped" in proc.stdout, (
        f"the failure must name the open window: {proc.stdout!r}"
    )


def test_dispatch_accepts_a_204_no_content_response(tmp_path: Path) -> None:
    """State that fails it: `gh` exits 0 with an EMPTY body — the documented
    ``204 No Content`` answer, whose existence is why the run id cannot be
    required. Reachable: GitHub has returned 204 for this endpoint (and
    community reports describe it reverting to 204 after returning 200), and
    the stub's empty stdout IS that state.

    A RED here is not fail-closed, it is broken: the API accepted the dispatch
    and the app is flipping, so the job would report an open window, and the
    error text would send the operator to re-dispatch a deploy already in
    flight.
    """
    proc = _run_step(_DISPATCH_STEP, tmp_path, gh_stdout="", gh_rc=0)
    assert proc.returncode == 0, (
        "a 204 No Content dispatch (gh exit 0, empty body) was reported as a "
        f"failure — every successful co-move run would be red (#3627)\n{proc.stdout}"
    )
    assert "deploy-hosted dispatched" in proc.stdout, proc.stdout


def test_dispatch_fails_closed_on_a_body_without_a_run_id(tmp_path: Path) -> None:
    """State that fails it: a 2xx whose non-empty body carries no digits-only
    run id — a payload this step cannot read. (`0` is accepted: the acceptance
    set is a non-empty all-digit string, matching the workflow's own "digits-only"
    wording, and a live run id is never 0.)

    Reachable whenever the response shape drifts, and distinguishable from the
    204 case above precisely by the body being non-empty: a body means the API
    chose to answer with detail, and a body we cannot parse is not a success.
    """
    for body in ('{"message": "Accepted"}', "{}", '{"workflow_run_id": null}',
                 '{"workflow_run_id": "nope"}', "not-json-at-all"):
        proc = _run_step(_DISPATCH_STEP, tmp_path, gh_stdout=body, gh_rc=0)
        assert proc.returncode != 0, (
            f"a dispatch whose response body carried no run id exited 0 for "
            f"{body!r} — that is the 'looks closed, is not' failure (#3627)\n{proc.stdout}"
        )
        # `not-json-at-all` is the unreadable sub-case: jq fails, so the step
        # must translate that into its own operator-facing error, not jq's.
        assert "UNCONFIRMED" in proc.stdout, proc.stdout


def test_dispatch_succeeds_and_names_the_created_run(tmp_path: Path) -> None:
    """State that fails it: a 200 whose body DOES name the run. Reachable — the
    same stub — and load-bearing: without it the fail-closed tests above would
    pass for the trivial reason that the block always fails."""
    proc = _run_step(
        _DISPATCH_STEP, tmp_path, gh_stdout='{"workflow_run_id": 4242}', gh_rc=0
    )
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    assert "4242" in proc.stdout, proc.stdout


def test_dispatch_targets_the_deploy_hosted_workflow_at_main(tmp_path: Path) -> None:
    """Asserted on the argv the step ACTUALLY ran, not on its comments.

    State that fails it: the request is not a POST to deploy-hosted's
    dispatches endpoint with `ref=main` — e.g. the `-f ref=` field is dropped
    (the dispatch then defaults to the default branch, which is not the ref the
    schema in this run came from) or the workflow id is misspelled (404 → red).
    """
    _run_step(_DISPATCH_STEP, tmp_path, gh_stdout='{"workflow_run_id": 7}')
    argv = _argv(tmp_path)
    assert "POST" in argv, argv
    assert "repos/daniel-ospina/tortoise/actions/workflows/deploy-hosted.yml/dispatches" in argv, argv
    assert "ref=main" in argv, argv


def test_ref_guard_refuses_a_non_main_run_and_allows_main(tmp_path: Path) -> None:
    """Both states of the guard. A branch dispatch is reachable (an operator
    can pass --ref), and it is refused BEFORE any write; main is allowed."""
    branch = _run_step(_REF_GUARD_STEP, tmp_path, env_extra={"RUN_REF": "refs/heads/fix/x"})
    assert branch.returncode != 0, (
        f"a branch dispatch was allowed — it would apply branch migrations to "
        f"production and flip main's app (#3627)\n{branch.stdout}"
    )
    assert "Nothing was applied" in branch.stdout, branch.stdout

    main = _run_step(_REF_GUARD_STEP, tmp_path, env_extra={"RUN_REF": "refs/heads/main"})
    assert main.returncode == 0, f"{main.stdout}\n{main.stderr}"


# ── 4. The header comment describes the file that exists ──────────────────


def test_header_step_three_names_the_step_that_implements_it() -> None:
    """The divergence #3627 was filed about: the header asserted a step
    (`3. DISPATCH deploy-hosted …`) the workflow did not implement.

    Fails when step 3 stops naming the real step (e.g. reverts to describing an
    operator action), which is how the gap stopped being visible.
    """
    text = _WORKFLOW.read_text(encoding="utf-8")
    header = text.split("\nname:", 1)[0]
    lines = header.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.startswith("#   3.")), None)
    assert start is not None, "the flip sequence no longer has a step 3"
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("#   4.")), len(lines))
    step_three = "\n".join(lines[start:end])
    assert _DISPATCH_STEP in step_three, (
        "header step 3 must name the step that actually performs it "
        f"({_DISPATCH_STEP!r}), else the header claims a guarantee the file does "
        f"not provide:\n{step_three}"
    )
    # …and the named step must EXIST: naming a step is the header's half of
    # the contract, and this raises AssertionError if the step was deleted.
    _step(_DISPATCH_STEP)
    assert re.search(r"DISPATCHED BY THIS\s*\n?#\s*RUN", step_three), (
        "header step 3 must say the dispatch happens IN THIS RUN (not as a "
        f"second operator action):\n{step_three}"
    )
