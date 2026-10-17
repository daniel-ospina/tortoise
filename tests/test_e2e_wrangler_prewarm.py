"""The wrangler pin is one artefact, ASSERTED against what actually resolved (#8816).

The pin used to be a string repeated in twelve `run:` blocks, and the guard that
"checked" it read that text. Reading text cannot see a spec written as
`wran\\gler@4`, `"wrangler"@4` or `wrangler$X@4`, cannot see a bare `wrangler`,
and never looked at `deploy-pages.yml` at all — where `wrangler@4` floated in
production. Two measurements decided the replacement (bash 5, node 22):

  * `wrangler --version` prints the bare resolved version on stdout;
  * a float declaration can never equal its own resolution — `wrangler@4`
    resolves to 4.151.1, so `[ "$got" = "$WRANGLER_VERSION" ]` fails.

So the version now lives in ONE file, `config/wrangler-version`, and the lane
ASSERTS the version it got against it. That is exact for every spelling,
including the ones no text scan can see, because it compares an observation to a
declaration. The boot steps then run bare `wrangler`, so there is no spec at the
call site to float, and the deploy steps assert before they upload.

What is left for THIS file is the shape that keeps those properties true:

  1. one artefact, full `X.Y.Z`, and no second copy of the value in any workflow;
  2. no workflow declares the version in `env:` (the spelling that would shadow
     nothing and let a consuming step act on a different version than the one
     verified) and no inline assignment other than the file read;
  3. every wrangler spec is the asserted `@${WRANGLER_VERSION:?...}` form;
  4. every e2e job has an unconditional install-and-VERIFY step, and that step
     keeps its 3-attempt registry retry (a single npm `ECONNRESET` reded the
     required `legal-e2e` check on 2026-10-15);
  5. boot steps carry no spec and keep their two-attempt retry;
  6. every deploy step asserts before it uploads;
  7. every job that installs wrangler pins node 22 — `npm install` only WARNS
     (`EBADENGINE`) on an engines mismatch, so a node-20 job would install
     "successfully" and fail later, inside the readiness loop.

A spec-free invocation is a second class, and it has no `wrangler@` token for the
spec arm (`test_every_wrangler_spec_is_the_asserted_form`) to read:
`npx --yes wrangler pages deploy` resolves to the registry's latest without ever
writing a spec down. `test_no_step_invokes_wrangler_without_the_asserted_spec`
therefore refuses a bare `wrangler` invocation EVERYWHERE except the five
declared boot steps (which run the binary the verify step proved) and the
`--version` probe — so a new step, a new job or a new deploy line cannot float
without also losing its name.

The removed machinery is the point, not an accident: with no spec at the call
site there is no shell word-splitting to model. The residual is stated rather
than hidden — a NEW step can always be written with `wrangler@4`, and a version
can reach npm through a spelling that never appears in these files at all (a
shell alias, a function, or a wrapper script naming its own version), which no
text scan over the workflows can see. The spec and invocation arms refuse the
first in every workflow file, the executed harnesses in
`tests/test_pages_bindings.py` prove the deploy step's assertion, and the arms
below execute the manual deploy script rather than grepping its path.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
VERSION_FILE = REPO_ROOT / "config" / "wrangler-version"
VERSION_SOURCE = "config/wrangler-version"

PREWARM_JOBS = frozenset({"welcome-e2e", "legal-e2e", "dashboard-e2e"})
VERIFY_STEP = "Install and verify the wrangler toolchain (#8816)"
BOOT_STEP_PREFIX = "Start wrangler pages dev preview"
DEPLOY_STEP_PREFIX = "Deploy to Cloudflare Pages"

EXACT_VERSION = re.compile(r"\d+\.\d+\.\d+")

#: The only legal spelling of a wrangler spec: the declared variable, with `:?`
#: so an empty declaration aborts instead of expanding to `wrangler@` (= latest).
LEGAL_SPEC = re.compile(r"wrangler@\$\{WRANGLER_VERSION:\?")

#: The only legal way the variable may be SET.
DECLARATION = 'WRANGLER_VERSION="$(cat config/wrangler-version)"'

#: Tokens that may legitimately accompany `${WRANGLER_VERSION…}` on a line: the
#: `:?` guard, an equality comparison, or a diagnostic. Anything else — notably
#: an inline `WRANGLER_VERSION="4.151.1"` — is an offender.
LEGAL_TOKENS = (
    "${WRANGLER_VERSION:?",
    '= "$WRANGLER_VERSION"',
    '!= "$WRANGLER_VERSION"',
    "declared ${WRANGLER_VERSION}",
    "wrangler ${WRANGLER_VERSION}",
)

#: The SPEC-FREE wrangler command word, and nothing that merely contains it: not
#: `wrangler.log` / `wrangler.pid` / `/tmp/wrangler-*` (paths), not
#: `config/wrangler-version` (a filename), not `test_e2e_wrangler_prewarm.py` —
#: and not `wrangler@…`, which carries a spec and belongs to the arm above.
WRANGLER_WORD = re.compile(r"(?<![\w./@-])wrangler(?![\w@.-])")



#: `if:` values that are false by construction: the boolean, the integer 0, and
#: their `${{ }}` spellings. `str(gate)` alone cannot tell `0` from `"0"`.
_CONSTANT_FALSE = re.compile(
    r"^(?:\$\{\{\s*)?(?:false|0(?:\.0*)?)(?:\s*\}\})?$", re.IGNORECASE
)


def _is_constant_false(gate: object) -> bool:
    if isinstance(gate, bool):
        return not gate
    if isinstance(gate, (int, float)):
        return not gate
    return bool(_CONSTANT_FALSE.match(str(gate).strip()))

def _workflow_paths() -> list:
    paths = sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))
    assert paths, "no workflows found — the repo root probe is wrong"
    return paths


def _load(path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _declared_version() -> str:
    return VERSION_FILE.read_text(encoding="utf-8").strip()


def _all_steps(jobs: dict) -> list[tuple[str, dict]]:
    return [
        (name, step)
        for name, job in jobs.items()
        for step in (job.get("steps") or [])
    ]


def _run(step: dict) -> str:
    return step.get("run") or ""


def _code(run: str) -> list[str]:
    """The step's CODE lines: a `#` comment cannot execute.

    Only full-line comments are dropped. An inline `#` is left alone on purpose:
    in these `run:` blocks it is as often part of a value (`${VAR#prefix}`) as a
    comment, and a wrong strip would silently hide an invocation.
    """
    return [line for line in run.splitlines() if not line.lstrip().startswith("#")]


def _steps_named(name: str) -> list[tuple[str, dict]]:
    out = []
    for path in _workflow_paths():
        for job_name, step in _all_steps(_load(path).get("jobs") or {}):
            if (step.get("name") or "") == name:
                out.append((job_name, step))
    return out


# ---------------------------------------------------------------- 1. one artefact


def test_the_version_lives_in_one_artefact_and_is_a_full_version() -> None:
    assert VERSION_FILE.is_file(), f"{VERSION_SOURCE} is missing — the pin has no home"
    declared = _declared_version()
    assert EXACT_VERSION.fullmatch(declared), (
        f"{VERSION_SOURCE} holds {declared!r}, which is not a full X.Y.Z — a partial "
        "version resolves to whatever npm serves, which is the drift this replaces"
    )
    for path in _workflow_paths():
        text = path.read_text(encoding="utf-8")
        assert declared not in text, (
            f"{path.name} restates the pinned version verbatim ({declared}); it must read "
            f"{VERSION_SOURCE} instead, or a bump edits one place and drifts in another"
        )


def test_no_workflow_declares_the_version_as_an_env_key() -> None:
    """`env: {WRANGLER_VERSION: 4.151.1}` is the spelling a text scan cannot see."""

    def walk(node, where):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "env" and isinstance(value, dict):
                    assert "WRANGLER_VERSION" not in value, (
                        f"{where}: `env` sets WRANGLER_VERSION={value['WRANGLER_VERSION']!r}. "
                        "It must be read from "
                        f"{VERSION_SOURCE} in the step that uses it — an env declaration "
                        "lets a consuming step act on a different version than the one "
                        "the verify step asserted"
                    )
                walk(value, f"{where}.{key}")
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{where}[{i}]")

    for path in _workflow_paths():
        walk(_load(path), path.name)


def test_every_version_mention_is_a_read_or_an_asserted_spec() -> None:
    offenders = []
    for path in _workflow_paths():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "WRANGLER_VERSION" not in line:
                continue
            if DECLARATION in line:
                continue
            if any(token in line for token in LEGAL_TOKENS):
                continue
            offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, "WRANGLER_VERSION used outside the declared shapes:\n" + "\n".join(
        offenders
    )


def test_every_wrangler_spec_is_the_asserted_form() -> None:
    offenders = []
    for path in _workflow_paths():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0] if line.lstrip().startswith("#") else line
            for match in re.finditer(r"wrangler@\S*", code):
                token = match.group(0)
                if LEGAL_SPEC.match(token):
                    continue
                # A bare `wrangler@` followed by prose punctuation is a mention,
                # not a spec — anything shaped like a spec (digit, letter, `^`,
                # `~`, `$`, quote, backslash, `{`) is an offender.
                if len(token) > len("wrangler@") and token[len("wrangler@")] in "`).,;:":
                    continue
                offenders.append(f"{path.name}:{lineno}: {token}")
    assert not offenders, (
        "wrangler spec is not the asserted variable form (a literal spec cannot be "
        "verified against what resolved):\n" + "\n".join(offenders)
    )


def test_each_workflow_reads_the_version_at_least_once() -> None:
    for path in _workflow_paths():
        text = path.read_text(encoding="utf-8")
        if "wrangler@" not in text:
            continue
        assert DECLARATION in text, (
            f"{path.name} uses a wrangler spec but never reads {VERSION_SOURCE}"
        )


# ------------------------------------------------- 2. the lane asserts, per job


def test_the_three_website_e2e_jobs_install_and_verify() -> None:
    found = _steps_named(VERIFY_STEP)
    jobs = {job for job, _step in found}
    assert jobs >= PREWARM_JOBS, (
        f"jobs without a {VERIFY_STEP!r} step: {sorted(PREWARM_JOBS - jobs)} — the pin is "
        "unverified there, so any spec that resolves elsewhere runs unnoticed"
    )
    for job, step in found:
        run = _run(step)
        assert DECLARATION in run, f"{job}: the verify step does not read {VERSION_SOURCE}"
        assert "npm install -g" in run, f"{job}: the verify step does not install"
        assert "--version" in run, f"{job}: the verify step never asks what resolved"
        assert '"$WRANGLER_VERSION"' in run, (
            f"{job}: the verify step installs/comares something other than the declared value"
        )
        # The loop must be the FULL 3-attempt retry, in CODE: a substring match on
        # "for attempt" is satisfied by a rewrite that silently drops attempts, and
        # the last attempt is the hard one (a single npm-registry ECONNRESET reded
        # the required legal-e2e check on 2026-10-15 — #2178/#8744).
        assert re.search(r"for attempt in 1 2 3; do", run), (
            f"{job}: the install lost its 3-attempt retry loop — one npm-registry "
            "ECONNRESET (2026-10-15) reded the required legal-e2e check when the "
            "fetch had none"
        )
        assert re.search(r"after 3 attempts", run), (
            f"{job}: the install loop has no hard failure after the last attempt — "
            "a persistent registry failure would leave the pin unverified"
        )


def test_the_verify_step_is_unconditional() -> None:
    for job, step in _steps_named(VERIFY_STEP):
        assert "if" not in step, (
            f"{job}: the install-and-verify step is gated by `if: {step['if']}` — a gate "
            "that skips it leaves the pin unverified while the run is green"
        )
        job_cfg = _load(WORKFLOWS / "ci.yml")["jobs"][job]
        gate = job_cfg.get("if")
        if gate is not None:
            assert not _is_constant_false(gate), (
                f"{job}: job-level `if: {gate!r}` is constant-false — the whole job, "
                "including the verify, can never run"
            )


def test_the_version_check_is_an_equality_against_the_declared_value() -> None:
    """The assertion must compare, not merely print — a print cannot fail a job."""

    for job, step in _steps_named(VERIFY_STEP):
        run = _run(step)
        assert re.search(r'\[ "\$got" = "\$WRANGLER_VERSION" \]', run), (
            f"{job}: no `[ \"$got\" = \"$WRANGLER_VERSION\" ]` comparison — the resolved "
            "version is not asserted"
        )
        assert "exit 1" in run, f"{job}: a mismatch would not fail the step"


# ------------------------------------------------------ 3. boot and deploy shape


def test_boot_steps_carry_no_spec_and_keep_their_retry() -> None:
    boots = [
        (job, step)
        for job, step in _all_steps(_load(WORKFLOWS / "ci.yml")["jobs"])
        if (step.get("name") or "").startswith(BOOT_STEP_PREFIX)
    ]
    assert len(boots) == 5, f"expected 5 boot steps, found {len(boots)}"
    for job, step in boots:
        run = _run(step)
        assert "wrangler pages dev" in run, f"{job}: boot does not start wrangler"
        assert "wrangler@" not in run, (
            f"{job}: the boot carries a spec — it must run the binary the verify step "
            "asserted, so that a spec has nowhere to float"
        )
        assert re.search(r"for attempt in 1 2; do", run), (
            f"{job}: the boot lost its two-attempt retry"
        )


def test_each_deploy_step_asserts_before_it_uploads() -> None:
    deploys = [
        (job, step)
        for job, step in _all_steps(_load(WORKFLOWS / "deploy-pages.yml")["jobs"])
        if (step.get("name") or "").startswith(DEPLOY_STEP_PREFIX)
    ]
    assert len(deploys) == 2, f"expected 2 deploy steps, found {len(deploys)}"
    for job, step in deploys:
        run = _run(step)
        assert DECLARATION in run, f"{job}: the deploy step does not read {VERSION_SOURCE}"
        assert "--version" in run, f"{job}: the deploy step never asserts what resolved"
        assert run.index("--version") < run.index("pages deploy"), (
            f"{job}: `pages deploy` runs before the version is asserted — the upload "
            "would go out on an unverified spec"
        )
        assert '"$WRANGLER_VERSION"' in run, (
            f"{job}: the assertion compares something other than the value being used"
        )


# ---------------------------------------- 4. no spec-free invocation, anywhere


def test_no_step_invokes_wrangler_without_the_asserted_spec() -> None:
    """A bare `wrangler` is the spelling a `wrangler@` scan can never see.

    `npx --yes wrangler pages deploy` (or a bare `wrangler pages dev` in a NEW
    step) has no spec token at all and resolves to whatever npm serves. A
    spec-free invocation is therefore legal in exactly two places, both declared
    here: the five boot steps, which run the binary the verify step proved, and
    the `--version` probe of that installed binary. Everywhere else — a new step,
    a new job, a deploy line — it is an offender.

    Declared residual: an `echo`/`printf` line QUOTES the word, so it is skipped —
    a command smuggled onto a diagnostic line (`echo x; npx wrangler pages deploy`)
    is out of this arm's reach. The other lines are not: the two skip classes are
    positive (the asserted spec, the probe), so a new spelling of the command is
    an offender by default rather than a non-match.
    """
    offenders = []
    inspected = 0
    for path in _workflow_paths():
        for job, step in _all_steps(_load(path).get("jobs") or {}):
            name = step.get("name") or ""
            for line in _code(_run(step)):
                if not WRANGLER_WORD.search(line):
                    continue
                if LEGAL_SPEC.search(line):
                    inspected += 1
                    continue  # the asserted spec
                # A diagnostic QUOTES the word; it does not invoke it.
                if line.lstrip().startswith(("echo ", "printf ")):
                    continue
                # The assertion on the binary the verify step installed.
                if re.search(r"\bwrangler\s+--version", line):
                    inspected += 1
                    continue
                if name.startswith(BOOT_STEP_PREFIX):
                    inspected += 1
                    continue
                offenders.append(f"{path.name}:{job}: {name!r}: {line.strip()}")
    # 5 declared boots + the 3 verify steps' `wrangler --version` probes. The
    # spec-carrying invocations (`wrangler@…`) are deliberately NOT matches here —
    # they are the arm above's subject — so this count is exact, not a floor that
    # hides a rewrite.
    assert inspected == 8, (
        f"expected 8 legal spec-free invocations (5 boots + 3 probes), found "
        f"{inspected} — the scan stopped matching, so this arm would pass on nothing"
    )
    assert not offenders, (
        "wrangler invoked WITHOUT the asserted spec outside the five boots and the "
        "`--version` probe — a bare invocation resolves to whatever npm serves, "
        "which is the float this file exists to kill:\n" + "\n".join(offenders)
    )


# ------------------------------------------------------------- 5. node floor, derived


def test_every_job_that_installs_wrangler_pins_node_22() -> None:
    """Derived, not a list: a new wrangler job joins the arm automatically."""

    offenders = []
    checked = 0
    for path in _workflow_paths():
        for job_name, job in (_load(path).get("jobs") or {}).items():
            steps = job.get("steps") or []
            # A job "installs wrangler" if any step's CODE names the command —
            # with a spec or without one. Deriving it from `wrangler@` alone let a
            # new job that calls a bare `npx wrangler` escape this arm entirely.
            installs = any(
                WRANGLER_WORD.search("\n".join(_code(_run(step)))) for step in steps
            )
            if not installs:
                continue
            checked += 1
            node = None
            for step in steps:
                uses = step.get("uses") or ""
                if uses.startswith("actions/setup-node"):
                    node = str((step.get("with") or {}).get("node-version", ""))
            # No `setup-node` at all is an offender, not a crash: the job would
            # run whatever node the image ships, and `npm install` only WARNS on
            # an engines mismatch.
            if "22" not in (node or ""):
                offenders.append(f"{path.name}:{job_name} node-version={node!r}")
    assert checked >= 4, f"only {checked} wrangler-installing jobs found — the probe is wrong"
    assert not offenders, (
        "a job installs wrangler without node 22; `npm install` only WARNS on an engines "
        "mismatch, so it would 'succeed' and break later in the readiness loop:\n"
        + "\n".join(offenders)
    )


# ------------------------------------------ 6. the manual deploy script, EXECUTED

DEPLOY_SCRIPT = REPO_ROOT / "website" / "apps" / "dashboard" / "deploy.sh"

#: Stub `npm`: the script's `npm ci` is skipped by a pre-existing node_modules and
#: `npm run build` is not the subject here.
STUB_NPM = "#!/bin/sh\nexit 0\n"

#: Stub `npx`: answers the `--version` probe with the declared value and records
#: its argv, so the executed arm can prove WHICH spec the deploy would use.
STUB_NPX_MANUAL = """#!/bin/bash
printf '%s\\n' "$@" > "$STUB_DIR/npx_args"
for arg in "$@"; do
  if [ "$arg" = "--version" ]; then
    cat "$STUB_DIR/config/wrangler-version"
    break
  fi
done
exit 0
"""


def _run_manual_deploy(tmp_path: Path, *, from_repo_root: bool):
    """Execute the SHIPPED deploy.sh from a scratch tree, stubs on PATH.

    The tree mirrors the checkout layout (`website/apps/dashboard/deploy.sh` +
    `config/wrangler-version`) so the script's own path arithmetic is under test,
    not a string in a file.
    """
    dashboard = tmp_path / "website" / "apps" / "dashboard"
    dashboard.mkdir(parents=True)
    shutil.copy2(DEPLOY_SCRIPT, dashboard / "deploy.sh")
    (dashboard / "node_modules").mkdir()  # skips `npm ci`
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "wrangler-version").write_text(
        _declared_version() + "\n", encoding="utf-8"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("npm", STUB_NPM), ("npx", STUB_NPX_MANUAL)):
        stub = bin_dir / name
        stub.write_text(body, encoding="utf-8")
        stub.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "STUB_DIR": str(tmp_path),
    }
    if from_repo_root:
        # Exactly what docs/infra-runbook.md documents.
        cwd, argv = tmp_path, "./website/apps/dashboard/deploy.sh"
    else:
        cwd, argv = dashboard, "./deploy.sh"
    return subprocess.run(
        ["bash", argv], cwd=str(cwd), capture_output=True, text=True, env=env
    )


@pytest.mark.parametrize("from_repo_root", [True, False])
def test_the_manual_deploy_reads_the_version_from_either_invocation(
    tmp_path, from_repo_root: bool
) -> None:
    """#8852 review P1: BOTH documented invocations must resolve the artefact.

    The script `cd`s to its own directory, and re-deriving `$(dirname "$0")`
    AFTER that `cd` applied the directory component twice: the runbook's
    `./website/apps/dashboard/deploy.sh` looked for
    `website/apps/dashboard/../../../config/wrangler-version` and died inside
    `cat` under `set -e` — before the `:?` message could explain anything.

    Executed rather than grepped on purpose: a text pin of the path string gave
    the broken form the appearance of coverage, because a path that does not
    resolve still matches its own literal.
    """
    r = _run_manual_deploy(tmp_path, from_repo_root=from_repo_root)
    out = r.stdout + r.stderr
    where = "the repo root" if from_repo_root else "its own directory"
    assert r.returncode == 0, f"deploy.sh failed when invoked from {where}:\n{out}"
    args = (tmp_path / "npx_args").read_text(encoding="utf-8").splitlines()
    assert args[:2] == ["--yes", f"wrangler@{_declared_version()}"], (
        f"the manual deploy ran {args!r} — it must carry the version it read"
    )


def test_the_manual_deploy_script_pins_the_same_version() -> None:
    """Shape, beside the execution arms: one artefact, asserted spec, no copy.

    `website/apps/dashboard/deploy.sh` is the documented manual deploy and sits
    outside the workflows, so no other arm reaches it.
    """
    assert DEPLOY_SCRIPT.is_file(), f"{DEPLOY_SCRIPT} is missing"
    code = "\n".join(_code(DEPLOY_SCRIPT.read_text(encoding="utf-8")))
    assert VERSION_SOURCE in code, (
        "the manual deploy does not read config/wrangler-version — it would deploy "
        "an unasserted wrangler"
    )
    assert "--version" in code, "the manual deploy never asserts what resolved"
    declared = _declared_version()
    assert declared not in code, (
        f"the manual deploy restates the pinned version ({declared}); it must read "
        f"{VERSION_SOURCE}"
    )
    for match in re.finditer(r"wrangler@\S*", code):
        assert LEGAL_SPEC.match(match.group(0)), (
            f"manual deploy spec {match.group(0)!r} is not the asserted variable form"
        )
