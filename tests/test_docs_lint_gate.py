"""#2386 — the REQUIRED `docs` check must actually lint (fail-closed detection).

`docs` is one of the six required branch-protection contexts and is named in
`.mergify.yml` `queue_conditions`. Before #2386 it could not fail:

  * the `docs` job checked out at DEPTH 1, so `pull_request.base.sha` was absent
    from the local object store;
  * `git diff --no-renames --name-only "<base>...HEAD" -- '*.md' || true` died
    with `fatal: Invalid symmetric difference expression` and the `|| true`
    swallowed it;
  * `FILES` came out empty and both lint steps — gated on
    `if: steps.changed.outputs.files != ''` — were SKIPPED;
  * the job reported SUCCESS having linted nothing.

Job 100109903325 (PR #2132) is the recorded instance: the log carries the
`fatal` line immediately before job cleanup and no markdownlint/lychee step at
all.

The detection step's `run:` body is EXECUTED here against real temporary git
repositories, not read as text — a text scanner would be satisfied by a `|| true`
spelled one line over, or by a guard that exists but is unreachable. Execution
decides the verdict the step would actually return for a given base.

What is pinned:

  * fail-closed on an EMPTY base (git reads `...HEAD` as `HEAD...HEAD` — an empty
    diff with exit 0, the silent green one input over);
  * fail-closed on an UNRESOLVABLE base (the #2386 defect itself);
  * the changed `.md` set is reported as a NUL-delimited list under
    `$RUNNER_TEMP` with a `count=` `$GITHUB_OUTPUT` value — the #4449 data
    contract the main-health path already uses (a filename is data, never shell
    text);
  * an empty changed set produces `count=0`, so both lint steps skip — the
    linter is never invoked with no file args, which would lint the WHOLE repo;
  * the job keeps the required-check shape: name `docs`, and NO `needs`/`if`
    (a skipped required job reports Success, so either would re-create the same
    "required check that cannot fail" defect one level up);
  * the checkout is full-depth, so the three-dot diff resolves against a real
    base;
  * the linter is cli2, which reads `.markdownlint-cli2.jsonc` — v1
    `markdownlint-cli` cannot parse that JSONC file and silently fell back to
    DEFAULTS (MD013 on), the opposite of the repo's declared rules.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
CI = REPO / ".github" / "workflows" / "ci.yml"
CONFIG = REPO / ".markdownlint-cli2.jsonc"

DOCS_JOB = "docs"
DETECT_STEP = "Get changed markdown files"
LINT_STEP = "Markdownlint (changed files)"
LINK_STEP = "Link check (changed files)"


def _workflow() -> dict:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))


def _docs_job() -> dict:
    jobs = _workflow().get("jobs") or {}
    assert DOCS_JOB in jobs, (
        "the `docs` job was renamed or deleted — either detaches the required "
        "branch-protection context (#2386)"
    )
    return jobs[DOCS_JOB]


def _step(name: str) -> dict:
    for step in _docs_job().get("steps") or []:
        if step.get("name") == name:
            return step
    raise AssertionError(f"no step named {name!r} in the `docs` job (#2386)")


def _step_by_id(step_id: str) -> dict:
    for step in _docs_job().get("steps") or []:
        if step.get("id") == step_id:
            return step
    raise AssertionError(f"no step with id {step_id!r} in the `docs` job (#7628)")


def _checkout_step() -> dict:
    for step in _docs_job().get("steps") or []:
        if str(step.get("uses", "")).startswith("actions/checkout"):
            return step
    raise AssertionError("the `docs` job has no actions/checkout step (#2386)")


def _code(body: str) -> str:
    """A run body with whole-line comments dropped.

    The step's own comments QUOTE the rejected forms (`|| true`, `--no-renames`)
    to explain why they are (or are not) used, so a raw-text scan would flag the
    documentation rather than the code — the trap
    `tests/test_ci_guard_invocation.py` was written to end.
    """
    return "\n".join(
        line for line in body.splitlines() if not line.lstrip().startswith("#")
    )


# ── executing the detection step ─────────────────────────────────────────────


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True
    )
    assert proc.returncode == 0, f"git {' '.join(args)} failed: {proc.stderr}"
    return proc.stdout.strip()


def _commit(repo: Path, message: str) -> str:
    """Commit everything and return the new HEAD sha (the base for the next)."""
    _git(repo, "add", "-A")
    _git(
        repo,
        "-c",
        "user.email=pin@example.com",
        "-c",
        "user.name=pin",
        "commit",
        "-qm",
        message,
    )
    return _git(repo, "rev-parse", "HEAD")


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / f"repo-{tmp_path.name}"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    return repo


def _run_detection(
    tmp_path: Path, repo: Path, base: str | None
) -> tuple[subprocess.CompletedProcess, str]:
    """Run the workflow's real detection `run:` body inside `repo`.

    `base=None` leaves `BASE_SHA` UNSET (not empty), which is the other spelling
    the `${BASE_SHA:-}` guard must survive.
    """
    body = _step(DETECT_STEP)["run"]
    script = tmp_path / "detect.sh"
    script.write_text(body, encoding="utf-8")
    output = tmp_path / "github_output"
    output.write_text("", encoding="utf-8")

    env = {key: value for key, value in os.environ.items() if key in ("PATH", "HOME", "LANG")}
    env["GITHUB_OUTPUT"] = str(output)
    # The step writes its NUL list under `$RUNNER_TEMP` (the #4449 data contract,
    # shared with the main-health path); point it at tmp_path so the test can
    # read the list back.
    env["RUNNER_TEMP"] = str(tmp_path)
    if base is not None:
        env["BASE_SHA"] = base

    proc = subprocess.run(
        ["bash", "-e", str(script)],
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
    )
    return proc, output.read_text(encoding="utf-8")


def _output_count(text: str) -> str:
    """The `count` value from a `$GITHUB_OUTPUT` file."""
    for line in text.splitlines():
        if line.startswith("count="):
            return line[len("count=") :]
    raise AssertionError(f"no `count` entry in GITHUB_OUTPUT: {text!r}")


def _changed_list(tmp_path: Path) -> list[str]:
    """The changed `.md` list the step wrote, read back from its NUL file."""
    raw = (tmp_path / "pr-md.nul").read_text(encoding="utf-8")
    return [entry for entry in raw.split("\0") if entry]


# ── the required-check shape ─────────────────────────────────────────────────


def test_docs_job_keeps_the_required_check_shape():
    """The job must always RUN: a skipped required job reports Success.

    The job name is the required context, so it may not be renamed; and #2149
    records that the required jobs deliberately carry no `needs`/`if`. Either
    added back here is the #2386 defect one level up — green by absence of the
    job rather than by passing it.
    """
    job = _docs_job()
    assert job.get("name") in (None, DOCS_JOB), (
        f"the `docs` job must keep the name `{DOCS_JOB}` (its required-check "
        f"context); got name={job.get('name')!r}"
    )
    assert "needs" not in job, (
        "`docs` is a required check — a `needs:` can skip the job, and a skipped "
        "required job reports Success (#2386/#2149)"
    )
    assert "if" not in job, (
        "`docs` is a required check — an `if:` can skip the job, and a skipped "
        "required job reports Success (#2386/#2149)"
    )


def test_docs_checkout_has_full_history():
    """`fetch-depth: 0` — the base commit must be in the object store."""
    with_block = _checkout_step().get("with") or {}
    depth = with_block.get("fetch-depth")
    assert depth is not None, (
        "the `docs` checkout must set fetch-depth (#2386): a depth-1 checkout "
        "omits pull_request.base.sha, so the three-dot diff cannot resolve"
    )
    assert str(depth) == "0", f"expected fetch-depth: 0, got {depth!r} (#2386)"


def test_detection_step_is_executable_fail_closed_shell():
    """The run body must be runnable offline, and must not swallow a failure."""
    step = _step(DETECT_STEP)
    body = step["run"]
    code = _code(body)

    assert "${{" not in body, (
        "the detection run body interpolates an Actions expression, so it cannot "
        "be executed (and verified) offline; pass the value through `env:` "
        "instead (#2386)"
    )
    assert "|| true" not in code and "||true" not in code, (
        "a `|| true` here is the #2386 defect: it turns a failed diff into an "
        "empty file list and a green required check"
    )
    assert "--no-renames" in code, (
        "the changed-set pin (tests/test_ci_selection.py::"
        "test_every_changed_set_diff_disables_rename_detection) requires "
        "--no-renames on this diff (#4378)"
    )
    assert "--diff-filter" in code and "ACMR" in code, (
        "the PR list must keep only paths that exist on disk (#4449/#5215): a "
        "deleted (or renamed-away) .md names a path lychee hard-errors on"
    )
    assert "::error::" in code, (
        "the step must fail LOUDLY when it cannot compute the changed set (#2386)"
    )
    # #7628: the base must be the base branch's TIP, resolved by the `base_tip`
    # step — NOT `github.event.pull_request.base.sha`, which is the PR's
    # MERGE-BASE. Pairing the merge-base with a merge-ref checkout made this diff
    # include the base branch's own newly-landed commits, so a PR that was merely
    # behind was failed for findings it did not author (#7542).
    assert (step.get("env") or {}).get("BASE_SHA") == (
        "${{ steps.base_tip.outputs.sha }}"
    ), "the changed-set base must be the resolved base TIP, not pull_request.base.sha (#7628)"


def test_base_is_resolved_from_the_base_branch_tip_not_the_merge_base():
    """#7628: the changed set must be THIS PR's changes, not the base's own.

    The defect: `BASE_SHA` was bound to `github.event.pull_request.base.sha`, which
    is the PR's MERGE-BASE, while the checkout is the MERGE ref (`refs/pull/N/merge`)
    that GitHub builds on the base's CURRENT tip. So `merge-base...HEAD` swallowed
    every commit the base branch landed after the merge-base, and a PR one commit
    behind was failed for the base's own finding. Measured on #7542: base.sha ==
    merge-base(16bb2ab12, main) == be01e05c0, while the evaluated tree sat on
    3c1fc8086, whose #7611 introduced the offending MD018.

    The guard is the shape of the fix: the base is RESOLVED, and the merge-base
    field is not used as the base anywhere in this job.
    """
    steps = _docs_job().get("steps") or []
    by_id = {s["id"]: s for s in steps if s.get("id")}

    base_tip = by_id.get("base_tip")
    assert base_tip is not None, (
        "the base tip must be resolved by its own step: the diff step's body is "
        "EXECUTED offline by this module, so it cannot fetch (#7628)"
    )

    code = base_tip["run"]
    assert "git fetch" in code, "the step must fetch the base branch (#7628)"
    assert "refs/heads/${BASE_REF}" in code, (
        "it must fetch the BASE BRANCH, on the strength of a ref name (#7628)"
    )
    assert (base_tip.get("env") or {}).get("BASE_REF") == (
        "${{ github.event.pull_request.base.ref }}"
    ), "the base ref must arrive through `env: BASE_REF` (#2386)"
    assert "${{ " not in code and "${{}}" not in code, (
        "the run body must be pure bash: a `${{ }}` interpolation is evaluated "
        "before the shell sees it (#2386)"
    )
    assert "::error::" in code and "exit 1" in code, (
        "an empty base ref must fail closed rather than leave an empty output (#2386)"
    )
    assert base_tip.get("if") == "${{ !inputs.main_health }}", (
        "a scheduled main-health call has no PR base, so this step is inert there "
        "(#5215 Task 8)"
    )

    assert by_id["changed"]["env"]["BASE_SHA"] == "${{ steps.base_tip.outputs.sha }}", (
        "the diff step must consume the RESOLVED tip (#7628)"
    )

    # ...and the resolver must run BEFORE its consumer. Reordering the two makes
    # `steps.base_tip.outputs.sha` evaluate EMPTY when `changed` reads it, and
    # `changed`'s own fail-closed guard then reds EVERY pull request — a
    # whole-fleet outage that a suite asserting only the binding would ship
    # (#7628 review).
    ids = [s.get("id") for s in steps]
    assert ids.index(BASE_TIP_STEP_ID) < ids.index("changed"), (
        "the base-tip resolver must precede `changed`, or its output is empty there (#7628)"
    )

    # The regression itself: the merge-base field is no longer the base.
    for step in steps:
        assert "pull_request.base.sha" not in yaml.safe_dump(step), (
            "the PR's merge-base must not be used as the changed-set base anywhere "
            "in the `docs` job — that is the #7628 defect, and using it in a second "
            "place would reintroduce it (#545 DRIFT_BASE_SHA is a separate job)"
        )


# ── the base-tip resolver (executed) ─────────────────────────────────────────
#
# #7628 review: the resolver's fail-closed behaviour was asserted only by STRING
# PRESENCE (`"::error::" in code and "exit 1" in code`) while the sibling diff
# step's body is EXECUTED. That is the text-scan this module exists to replace, and
# it left the resolver's own discipline unpinned: deleting the load-bearing
# `SHA="$(git rev-parse …)"` ASSIGNMENT (whose exit status `set -e` propagates) in
# favour of a nested `echo "sha=$(…)"` — whose status is DISCARDED — kept the suite
# green. The body is executable offline, so it is executed: the remote is a LOCAL
# bare repo, so no step of this test touches the network.

BASE_TIP_STEP_ID = "base_tip"


def _repo_with_bare_remote(tmp_path: Path) -> tuple[Path, str]:
    """A repo whose `origin` is a LOCAL bare remote, already carrying `main`."""
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(remote)],
        check=True,
        capture_output=True,
    )
    repo = _repo(tmp_path)
    (repo / "seed.md").write_text("# seed\n", encoding="utf-8")
    sha = _commit(repo, "base")
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-q", "origin", "main")
    # The push leaves a LOCAL tracking ref behind. Delete it and ASSERT it is gone, so
    # the positive control can only be satisfied by the step's OWN fetch — otherwise a
    # mutation to the fetch DESTINATION still resolves (#7628 review).
    subprocess.run(
        ["git", "-C", str(repo), "update-ref", "-d", "refs/remotes/origin/main"],
        check=True, capture_output=True,
    )
    assert not _has_tracking_ref(repo), (
        "the fixture must start with NO local tracking ref, or the fetch is not tested"
    )
    return repo, sha


def _has_tracking_ref(repo: Path) -> bool:
    """Does `refs/remotes/origin/main` exist locally?"""
    return (
        subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--verify", "-q", "refs/remotes/origin/main"],
            capture_output=True,
        ).returncode
        == 0
    )


def _run_base_tip(
    tmp_path: Path, repo: Path, base_ref: str | None, shim_dir: Path | None = None
) -> tuple[subprocess.CompletedProcess, str]:
    """Run the workflow's real resolver `run:` body inside `repo`.

    `base_ref=None` leaves `BASE_REF` UNSET (not empty) — the other spelling the
    `${BASE_REF:-}` guard must survive. `shim_dir` is prepended to PATH so a test
    can make `git` itself misbehave.
    """
    body = _step_by_id(BASE_TIP_STEP_ID)["run"]
    script = tmp_path / "base-tip.sh"
    script.write_text(body, encoding="utf-8")
    output = tmp_path / "base-tip-output"
    output.write_text("", encoding="utf-8")

    env = {key: value for key, value in os.environ.items() if key in ("PATH", "HOME", "LANG")}
    if shim_dir is not None:
        env["PATH"] = f"{shim_dir}{os.pathsep}{env.get('PATH', '')}"
    env["GITHUB_OUTPUT"] = str(output)
    if base_ref is not None:
        env["BASE_REF"] = base_ref

    # NO harness-supplied `-e`: the body's own `set -euo pipefail` must be the thing
    # that aborts a failing step, or a mutation deleting it escapes the suite while
    # the assignment-discipline test above still passes (#7628 review).
    proc = subprocess.run(
        ["bash", str(script)],
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
    )
    return proc, output.read_text(encoding="utf-8")


def _git_shim_failing(tmp_path: Path, subcommand: str) -> Path:
    """A PATH shim whose `git <subcommand>` exits 1 and delegates everything else."""
    # `shutil.which`, NOT `subprocess.run(["command", "-v", "git"])`. `command` is a
    # SHELL BUILTIN: macOS happens to ship a real `/usr/bin/command` so the subprocess
    # form works there, but the Linux CI runner has no such binary and it raised
    # `FileNotFoundError: 'command'` — a macOS-only pass that failed on GitHub-hosted.
    resolved = shutil.which("git") or "git"
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    shim = shim_dir / "git"
    shim.write_text(
        "#!/bin/sh\n"
        f'if [ "$1" = "{subcommand}" ]; then exit 1; fi\n'
        f'exec "{resolved}" "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    return shim_dir


def test_base_tip_step_publishes_the_resolved_tip(tmp_path: Path):
    """Positive control: the resolver fetches the base branch and publishes its tip."""
    repo, sha = _repo_with_bare_remote(tmp_path)
    proc, output = _run_base_tip(tmp_path, repo, "main")
    assert proc.returncode == 0, proc.stderr
    assert f"sha={sha}" in output, (
        f"the resolver must publish the base branch TIP as `sha=` (#7628); got {output!r}"
    )
    # ...and it must be the STEP'S OWN fetch that created the ref it read — the
    # fixture removed it, so a mutation to the fetch destination cannot pass here.
    assert _has_tracking_ref(repo), (
        "the step's own `git fetch` must create refs/remotes/origin/main; it was absent "
        "before the step ran, so resolving it means the fetch DID NOT put it there (#7628)"
    )


def test_base_tip_step_fails_closed_on_an_empty_ref(tmp_path: Path):
    """An empty ref must fail LOUDLY, not publish an empty output (#2386 one input over)."""
    repo, _ = _repo_with_bare_remote(tmp_path)
    for spelling in ("", None):
        proc, output = _run_base_tip(tmp_path, repo, spelling)
        assert proc.returncode != 0, (
            f"BASE_REF={spelling!r} must fail closed, not leave an empty `sha=` (#7628)"
        )
        assert "sha=" not in output, "an empty ref must not publish an output"


def test_base_tip_step_fails_closed_when_the_ref_does_not_resolve(tmp_path: Path):
    """A ref that cannot be fetched takes the step down rather than yielding empty."""
    repo, _ = _repo_with_bare_remote(tmp_path)
    proc, output = _run_base_tip(tmp_path, repo, "no-such-branch")
    assert proc.returncode != 0, proc.stderr
    assert "sha=" not in output


def test_base_tip_step_fails_when_rev_parse_fails_even_though_the_fetch_succeeded(
    tmp_path: Path,
):
    """#7628 review: the ASSIGNMENT is load-bearing, and this is what pins it.

    `SHA="$(git rev-parse …)"` propagates the substitution's exit status under
    `set -e`, so a failed `rev-parse` fails the STEP; the nested
    `echo "sha=$(…)"` spelling DISCARDS that status and publishes whatever the
    substitution printed while still exiting 0 — a silent green. The fetch is made
    to SUCCEED and only `rev-parse` to fail, so the fetch's own failure cannot be
    what this test is observing; it observes the assignment discipline itself.
    """
    repo, _ = _repo_with_bare_remote(tmp_path)
    shim_dir = _git_shim_failing(tmp_path, "rev-parse")
    proc, output = _run_base_tip(tmp_path, repo, "main", shim_dir=shim_dir)
    assert proc.returncode != 0, (
        "a failed `git rev-parse` must FAIL THE STEP (the assignment propagates its "
        f"status under `set -e`); the step exited 0 and published {output!r}"
    )
    assert "sha=" not in output, (
        "a failed `rev-parse` must not publish an output — that is the silent green "
        "the guard exists to prevent (#2386/#7628)"
    )


# ── the detection step's verdicts (executed) ─────────────────────────────────


def test_detection_reports_changed_markdown(tmp_path: Path):
    """Positive control: the changed `.md` set is the diff's set."""
    repo = _repo(tmp_path)
    (repo / "a.md").write_text("# a\n", encoding="utf-8")
    base = _commit(repo, "base")
    (repo / "a.md").write_text("# a\n\nchanged\n", encoding="utf-8")
    (repo / "b.md").write_text("# b\n", encoding="utf-8")
    (repo / "notes.txt").write_text("x\n", encoding="utf-8")
    _commit(repo, "change")

    proc, output = _run_detection(tmp_path, repo, base)
    assert proc.returncode == 0, proc.stderr
    assert _output_count(output) == "2"
    # `./`-prefixed NUL entries: a filename is data, never shell text (#4449).
    assert _changed_list(tmp_path) == ["./a.md", "./b.md"]


def test_detection_excludes_the_base_branch_when_the_tip_moved_past_the_merge(
    tmp_path: Path,
):
    """#7628: the shape the fix EXISTS for — a base tip NEWER than HEAD's first parent.

    Every other detection test builds a LINEAR graph, where `BASE_SHA` is an ancestor
    of HEAD and `A...HEAD` and `A..HEAD` are the SAME diff — so none of them can tell
    the fix from the bug. Here the tip is a SIBLING of the merge commit, which is the
    state production is in whenever the base branch moves after the merge ref is cut:
    `base..HEAD` then inverts the base branch's own commits into the changed set (a
    modified `.md` comes back as `M`, and `--diff-filter=ACMR` keeps `M`), while
    `base...HEAD` stays exactly this PR's changes.
    """
    repo = _repo(tmp_path)
    (repo / "seed.md").write_text("# seed\n", encoding="utf-8")
    a = _commit(repo, "A")
    (repo / "main1.md").write_text("# main1\n", encoding="utf-8")
    m1 = _commit(repo, "M1")  # the base tip at the moment the merge ref is cut

    _git(repo, "checkout", "-q", "-b", "pr", a)
    (repo / "pr.md").write_text("# pr\n", encoding="utf-8")
    _commit(repo, "PR")

    # The merge ref itself: a MERGE of the then-current tip and the PR head, on NO
    # branch — which is what `actions/checkout` puts at HEAD for a `pull_request`.
    _git(repo, "checkout", "-q", "--detach", m1)
    _git(
        repo,
        "-c",
        "user.email=pin@example.com",
        "-c",
        "user.name=pin",
        "merge",
        "--no-ff",
        "-m",
        "merge",
        "pr",
    )
    merged = _git(repo, "rev-parse", "HEAD")

    # ...and the base branch moves on again, so the TIP is NOT an ancestor of HEAD.
    # It MODIFIES a file that exists on both sides — a modification comes back as `M`
    # and `--diff-filter=ACMR` KEEPS it, which is what makes the two-dot form differ.
    _git(repo, "checkout", "-q", "main")
    (repo / "seed.md").write_text("# seed\n\nmain moved on\n", encoding="utf-8")
    (repo / "main2.md").write_text("# main2\n", encoding="utf-8")
    tip = _commit(repo, "M2")

    assert _git(repo, "merge-base", tip, merged) == m1, (
        "the fixture must be DIVERGENT: the tip and the merge commit share only M1"
    )

    # HEAD is what `actions/checkout` leaves behind — the MERGE ref, not the branch —
    # while the base tip is a SIBLING of it. That pair is the production shape.
    _git(repo, "checkout", "-q", "--detach", merged)
    assert _git(repo, "rev-parse", "HEAD") == merged

    proc, output = _run_detection(tmp_path, repo, tip)
    assert proc.returncode == 0, proc.stderr
    assert _changed_list(tmp_path) == ["./pr.md"], (
        "the changed set must be THIS PR's markdown only; the base branch's own "
        f"main1.md/main2.md are not this PR's changes (#7628) — got {_changed_list(tmp_path)}"
    )
    assert _output_count(output) == "1"


def test_detection_reports_nothing_when_no_markdown_changed(tmp_path: Path):
    """A clean empty result must be `count=0`, so both lint steps skip."""
    repo = _repo(tmp_path)
    (repo / "a.md").write_text("# a\n", encoding="utf-8")
    base = _commit(repo, "base")
    (repo / "notes.txt").write_text("x\n", encoding="utf-8")
    _commit(repo, "change")

    proc, output = _run_detection(tmp_path, repo, base)
    assert proc.returncode == 0, proc.stderr
    assert output.splitlines() == ["count=0"], (
        f"the empty result must be `count=0`; got {output!r}. A truthy value here "
        "would make the `count != '0'` gate pass and run the linter with no args "
        "— i.e. lint the WHOLE repo (#2386)"
    )
    assert _changed_list(tmp_path) == []


def test_detection_fails_closed_on_empty_base(tmp_path: Path):
    """An empty base makes `...HEAD` read `HEAD...HEAD` — empty, exit 0."""
    repo = _repo(tmp_path)
    (repo / "a.md").write_text("# a\n", encoding="utf-8")
    _commit(repo, "base")

    proc, _ = _run_detection(tmp_path, repo, "")
    assert proc.returncode != 0, (
        "an empty base must FAIL the step: git would read `...HEAD` as "
        "`HEAD...HEAD` and return an empty, exit-0 diff (#2386)"
    )
    assert "base SHA is empty" in (proc.stdout + proc.stderr)


def test_detection_fails_closed_on_unset_base(tmp_path: Path):
    """The `set -u` / `${BASE_SHA:-}` path must fail closed too, not crash open."""
    repo = _repo(tmp_path)
    (repo / "a.md").write_text("# a\n", encoding="utf-8")
    _commit(repo, "base")

    proc, _ = _run_detection(tmp_path, repo, None)
    assert proc.returncode != 0
    assert "base SHA is empty" in (proc.stdout + proc.stderr)


def test_detection_fails_closed_on_unresolvable_base(tmp_path: Path):
    """The #2386 case: a base absent from the shallow store must RED the step."""
    repo = _repo(tmp_path)
    (repo / "a.md").write_text("# a\n", encoding="utf-8")
    _commit(repo, "base")
    (repo / "a.md").write_text("# a\n\nchanged\n", encoding="utf-8")
    _commit(repo, "change")

    proc, _ = _run_detection(tmp_path, repo, "0" * 40)
    assert proc.returncode != 0, (
        "an unresolvable base must fail the step. This is the exact #2386 "
        "defect: the old `|| true` turned this same `fatal` into an empty list "
        "and a green required check"
    )


# ── the lint steps ───────────────────────────────────────────────────────────


def test_lint_step_uses_cli2_and_the_repo_config():
    """cli2 reads `.markdownlint-cli2.jsonc`; v1 cli silently ignored it."""
    run = _code(_step(LINT_STEP)["run"])
    assert "markdownlint-cli2" in run, (
        "the lint step must use markdownlint-cli2 (#2386): v1 markdownlint-cli "
        "cannot parse the repo's JSONC config and falls back to defaults"
    )
    assert re.search(r"markdownlint-cli(?!2)", run) is None, (
        "v1 `markdownlint-cli` is still invoked somewhere in the step (#2386)"
    )
    assert re.search(r"markdownlint-cli2@\d", run), (
        "pin the cli2 version — a floating npx would let the rule surface drift "
        "under the required check (#2386)"
    )
    assert CONFIG.is_file(), f"{CONFIG.name} is the config the linter reads"
    assert '"MD013": false' in CONFIG.read_text(encoding="utf-8"), (
        "the repo config must keep MD013 disabled — the CI lint is supposed to "
        "honour it, not the linter's defaults (#2386)"
    )


@pytest.mark.parametrize("name", [LINT_STEP, LINK_STEP])
def test_lint_steps_are_gated_so_an_empty_set_lints_nothing(name: str):
    """No changed docs ⇒ skip; never run the linter with no file args.

    `markdownlint-cli2` with no file arguments lints the config's globs (the
    whole repo). Removing this gate while the detection step can legitimately
    return an empty set would turn a docs-only check into a full-repo lint.
    """
    step = _step(name)
    assert step.get("if") == (
        "${{ !inputs.main_health && steps.changed.outputs.count != '0' }}"
    ), (
        f"{name!r} must stay gated on the PR path and a non-empty changed set "
        f"(#2386); got if={step.get('if')!r}"
    )
