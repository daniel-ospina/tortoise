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
  * the changed `.md` set is reported, and the `$GITHUB_OUTPUT` encoding is
    multi-line safe;
  * an empty changed set produces `files=` — NOT a truthy newline heredoc, which
    would run markdownlint with NO file args and lint the WHOLE repo;
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


def _output_files(text: str) -> str:
    """The `files` value from a `$GITHUB_OUTPUT` file, in either encoding."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line == "files=":
            return ""
        if line.startswith("files<<"):
            delimiter = line[len("files<<") :]
            assert delimiter, f"empty heredoc delimiter in GITHUB_OUTPUT: {text!r}"
            collected: list[str] = []
            for following in lines[index + 1 :]:
                if following == delimiter:
                    return "\n".join(collected)
                collected.append(following)
            raise AssertionError(f"unterminated heredoc in GITHUB_OUTPUT: {text!r}")
    raise AssertionError(f"no `files` entry in GITHUB_OUTPUT: {text!r}")


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
    assert "::error::" in code, (
        "the step must fail LOUDLY when it cannot compute the changed set (#2386)"
    )
    assert (step.get("env") or {}).get("BASE_SHA") == (
        "${{ github.event.pull_request.base.sha }}"
    ), "the base SHA must arrive through `env: BASE_SHA` (#2386)"


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
    assert _output_files(output).splitlines() == ["a.md", "b.md"]


def test_detection_reports_nothing_when_no_markdown_changed(tmp_path: Path):
    """A clean empty result must be encoded as `files=` (falsy)."""
    repo = _repo(tmp_path)
    (repo / "a.md").write_text("# a\n", encoding="utf-8")
    base = _commit(repo, "base")
    (repo / "notes.txt").write_text("x\n", encoding="utf-8")
    _commit(repo, "change")

    proc, output = _run_detection(tmp_path, repo, base)
    assert proc.returncode == 0, proc.stderr
    assert output.splitlines() == ["files="], (
        f"the empty result must be the falsy `files=`; got {output!r}. A truthy "
        "multi-line value here would make the `files != ''` gate pass and run "
        "markdownlint with no args — i.e. lint the WHOLE repo (#2386)"
    )
    assert _output_files(output) == ""


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
    run = _step(LINT_STEP)["run"]
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
    assert step.get("if") == "steps.changed.outputs.files != ''", (
        f"{name!r} must stay gated on a non-empty changed set (#2386); "
        f"got if={step.get('if')!r}"
    )
