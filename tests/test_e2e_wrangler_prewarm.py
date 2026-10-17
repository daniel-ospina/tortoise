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
  4. every e2e job has an unconditional install-and-VERIFY step that runs BEFORE
     its boot and keeps its three-attempt registry retry (a single npm
     `ECONNRESET` reded the required `legal-e2e` check on 2026-10-15, and the
     last attempt is the hard one now);
  5. boot steps carry no spec and keep their two-attempt retry;
  6. every deploy step asserts before it uploads;
  7. every job that installs wrangler pins node 22 — `npm install` only WARNS
     (`EBADENGINE`) on an engines mismatch, so a node-20 job would install
     "successfully" and fail later, inside the readiness loop.

A spec-LESS invocation is a second class, and it has no `wrangler@` token for the
spec arm to read: `npx --yes wrangler pages deploy` resolves to the registry's
latest without writing a spec down anywhere. Two arms cover it — one restricted
to the deploy paths, where the assertion validates the declared spec rather than
the spec on the upload line, and one over EVERY step: a bare `wrangler` is legal
only in the five declared boot steps (which run the binary the verify step
proved).

The removed machinery is the point, not an accident: with no spec at the call
site there is no shell word-splitting to model. The residual is stated rather
than hidden — a NEW step can always be written with `wrangler@4`, and a version
can reach npm through a spelling that never appears in these files at all (a
shell alias, a function, or a wrapper script naming its own version), which no
text scan over the workflows can see. The spec and invocation arms refuse the
first in every workflow file, the executed harnesses in
`tests/test_pages_bindings.py` prove the deploy step's assertion, and the arms
below EXECUTE the manual deploy script rather than grepping its path.
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

BARE_INVOCATION = re.compile(r"(?<![\w@$./-])wrangler(?![\w@])")

#: A quoted LONE command word — `npx --yes "wrangler" pages deploy`. bash runs it as
#: the binary, but ``_strip_quoted`` deletes whole quoted SEGMENTS (so a diagnostic
#: is not an offence), and deleting the segment hid this spelling — one the module
#: header names explicitly. A message is never a LONE ``wrangler`` token, so matching
#: the quoted token itself cannot resurrect the diagnostic false positive.
QUOTED_WRANGLER = re.compile(r"""(?<![\w@$-])["']wrangler["'](?![\w@])""")

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


def _fenced_blocks(text: str) -> str:
    """The contents of every ``` fence, joined — the parts a reader would PASTE."""
    out, inside = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            inside = not inside
            continue
        if inside:
            out.append(line)
    return "\n".join(out)

def _workflow_paths() -> list:
    """Every file that can carry a `run:` — workflows AND composite actions.

    A composite action is invoked from a workflow with `uses:`, so a spec inside
    one reaches the lane exactly like a step's own: scanning only
    `.github/workflows/` leaves the four actions under `.github/actions/`
    unguarded.
    """
    paths = sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))
    actions = REPO_ROOT / ".github" / "actions"
    if actions.is_dir():
        paths += sorted(actions.glob("*/action.yml")) + sorted(actions.glob("*/action.yaml"))
    assert paths, "no workflows found — the repo root probe is wrong"
    return paths


#: Documents that show a copy-pasteable wrangler command. They are guidance, and
#: guidance that floats teaches the drift this file exists to stop.
COMMAND_DOCS = ("website/README.md",)


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


def _document_steps(doc: dict) -> list[tuple[str, dict]]:
    """Steps from a WORKFLOW **or** a COMPOSITE ACTION.

    A composite action declares `runs: steps:`, never `jobs:` — so reading only
    `jobs:` yielded ZERO steps for every `.github/actions/*/action.yml`, and a
    spec-less invocation inside one was invisible to every step arm while the
    file was still in `_workflow_paths()` (the arm looked like it covered them).
    """
    steps = _all_steps(doc.get("jobs") or {})
    runs = doc.get("runs") or {}
    if isinstance(runs, dict) and runs.get("using") == "composite":
        steps += [("<composite>", step) for step in (runs.get("steps") or [])]
    return steps


def _run(step: dict) -> str:
    return step.get("run") or ""


def _steps_named(name: str) -> list[tuple[str, dict]]:
    out = []
    for path in _workflow_paths():
        for job_name, step in _all_steps(_load(path).get("jobs") or {}):
            if (step.get("name") or "") == name:
                out.append((job_name, step))
    return out


def _deploy_steps() -> list[tuple[str, dict]]:
    return [
        (job, step)
        for job, step in _all_steps(_load(WORKFLOWS / "deploy-pages.yml")["jobs"])
        if (step.get("name") or "").startswith(DEPLOY_STEP_PREFIX)
    ]


# ------------------------------------------------- shell text, read honestly


def _cut_comment(line: str) -> str:
    """`line` with a COMMENT TAIL cut off — quoting honoured, so `"a#b"` is data.

    A `#` starts a comment only at the start of a word and outside quotes, which
    is shell's own rule, and a word starts after a blank OR a metacharacter — so a
    `;#` tail is a comment too. This file's subject is prose ABOUT the code: a
    comment that NAMES a retry loop on a step that has none must never satisfy the
    check, and `ci.yml`'s boot steps carry exactly that kind of prose.
    """
    quote = ""
    at_word_start = True
    i = 0
    while i < len(line):
        ch = line[i]
        if quote == "'":
            if ch == "'":
                quote = ""
        elif quote == '"':
            if ch == '"':
                quote = ""
            elif ch == "\\" and i + 1 < len(line):
                i += 1
        elif ch in "'\"":
            quote = ch
            at_word_start = False
        elif ch == "\\" and i + 1 < len(line):
            at_word_start = False
            i += 1
        elif ch == "#" and at_word_start:
            return line[:i]
        elif ch in " \t;&|()<>":
            at_word_start = True
        else:
            at_word_start = False
        i += 1
    return line


def _strip_quoted(line: str) -> str:
    """`line` with quoted SEGMENTS removed — a diagnostic is not a command.

    `::error::resolved wrangler ${got} != …` NAMES wrangler in a message, and a
    `npx --yes "wrangler@${WRANGLER_VERSION:?}"` spec is judged by the spec arm
    (which keeps quotes, or a literal spec inside one would vanish).
    """
    out = []
    quote = ""
    i = 0
    while i < len(line):
        ch = line[i]
        if quote == "'":
            if ch == "'":
                quote = ""
        elif quote == '"':
            if ch == '"':
                quote = ""
            elif ch == "\\" and i + 1 < len(line):
                i += 1
        elif ch in "'\"":
            quote = ch
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _retry_loop_bodies(run: str, header: str) -> list[str]:
    """The bodies of each `header` … `done` loop found in comment-stripped CODE."""
    bodies: list[str] = []
    current: list[str] | None = None
    for raw in str(run or "").splitlines():
        line = _cut_comment(raw)
        if current is None:
            if header in line:
                current = []
            continue
        if line.strip() == "done":
            bodies.append("\n".join(current))
            current = None
        else:
            current.append(line)
    return bodies


def _bare_invocations(code: str) -> list[str]:
    """Spec-less wrangler invocations — `npx wrangler`, `wrangler pages deploy`."""
    offences = []
    for lineno, line in enumerate(str(code or "").splitlines(), 1):
        uncommented = _cut_comment(line)
        if BARE_INVOCATION.search(_strip_quoted(uncommented)) or QUOTED_WRANGLER.search(
            uncommented
        ):
            offences.append(f"line {lineno}: {line.strip()}")
    return offences


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
            # `_cut_comment`, not a whole-line strip: a TRAILING comment naming a
            # spec (`… --version  # was wrangler@4`) is prose too, and reading it
            # as code makes this arm fail on a correct workflow.
            code = _cut_comment(line)
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
        assert _retry_loop_bodies(run, "for attempt in 1 2 3;"), (
            f"{job}: the install lost its three-attempt retry loop — one npm-registry "
            "ECONNRESET (2026-10-15) reded the required legal-e2e check when the fetch "
            "had none, and the last attempt is HARD now"
        )
        # The loop must END in a hard failure, or "three attempts" is prose: a
        # `done` that falls through leaves the pin unverified on a persistent
        # registry failure.
        assert re.search(r"after 3 attempts", run), (
            f"{job}: the install loop has no hard failure after the last attempt — "
            "a persistent registry failure would leave the pin unverified"
        )
        for body in _retry_loop_bodies(run, "for attempt in 1 2 3;"):
            assert "npm install -g" in body, (
                f"{job}: the retry does not wrap the install, so the install itself is "
                "still a single unprotected fetch"
            )
            assert re.search(r"sleep \$\(\(attempt \* 10\)\)", body), (
                f"{job}: the retry carries no backoff — all three attempts would burn "
                "inside the same second and fail together"
            )


def test_the_retry_check_ignores_a_commented_out_loop() -> None:
    """Prose about the loop must not satisfy the check — and a 2-attempt loop is not it."""
    header = "for attempt in 1 2 3;"
    assert _retry_loop_bodies(f"{header} do\n  npm install -g x\ndone", header)
    assert not _retry_loop_bodies(
        "# old body used `for attempt in 1 2 3;`\nnpm install -g x", header
    )
    assert not _retry_loop_bodies("npm install -g x  # for attempt in 1 2 3;", header)
    assert not _retry_loop_bodies("for attempt in 1 2; do\nnpm install -g x\ndone", header)
    assert not _retry_loop_bodies("npm install -g x", header)


def test_the_verify_step_precedes_every_boot_in_the_same_job() -> None:
    """The boot must run the binary the verify step proved — asserted as an ORDER.

    ``test_the_three_website_e2e_jobs_install_and_verify`` proves a verify step
    EXISTS in each job, and ``test_boot_steps_carry_no_spec_and_keep_their_retry``
    proves the boot carries no spec. Neither asserts that the verify runs FIRST:
    with the order unasserted, moving the verify after the boot leaves the boot
    running whatever global ``wrangler`` happens to be on PATH, unverified, while
    every test in this file stays green.
    """
    verify_at: dict[str, list[int]] = {}
    boot_at: dict[str, list[int]] = {}
    steps = _all_steps(_load(WORKFLOWS / "ci.yml").get("jobs") or {})
    for idx, (job, step) in enumerate(steps):
        name = step.get("name") or ""
        if name == VERIFY_STEP:
            verify_at.setdefault(job, []).append(idx)
        elif name.startswith(BOOT_STEP_PREFIX):
            boot_at.setdefault(job, []).append(idx)

    for job in sorted(PREWARM_JOBS):
        verifies = verify_at.get(job) or []
        assert verifies, f"{job}: no {VERIFY_STEP!r} step at all"
        for boot in boot_at.get(job) or []:
            assert any(v < boot for v in verifies), (
                f"{job}: the boot at step {boot} runs BEFORE every verify step "
                f"{verifies} — the boot would use whatever wrangler is on PATH, "
                "which nothing asserted"
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


def test_the_ci_yml_wrangler_wiring_is_exercised_by_an_ungated_job() -> None:
    """`ci.yml` is in no `changes` gate, so one job must run it on EVERY PR.

    `ci.yml` carries three of the five consumers and every assertion, and it is
    deliberately absent from the `website`/`dashboard_e2e` gates (a workflow-only
    change would otherwise run two full e2e suites). What makes that safe is
    this: an UNGATED job installs, verifies and boots from the same wiring, so
    the lane is executed on every PR that can change it.
    """
    ci_jobs = _load(WORKFLOWS / "ci.yml")["jobs"]
    verified = {job for job, _step in _steps_named(VERIFY_STEP)} & set(ci_jobs)
    assert verified, f"no job in ci.yml carries {VERIFY_STEP!r} — the probe is wrong"
    ungated = {job for job in verified if ci_jobs[job].get("if") is None}
    assert ungated, (
        "every job carrying the wrangler wiring is gated by `changes`, and ci.yml is in "
        f"no gate — a ci.yml-only wiring change would run none of them: gated={sorted(verified)}"
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
        assert re.search(r"for attempt in 1 2;", run), (
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


# ------------------------------------------- 4. no spec-LESS invocation on a deploy path


def test_every_deploy_invocation_is_the_declared_spec() -> None:
    """A spec-LESS invocation ships an UNDECLARED version past the assertion.

    The runtime assertion validates the DECLARED spec, never the spec on the
    upload line — so a deploy step copied as `npx --yes wrangler pages deploy`
    would deploy an unverified version while every `wrangler@` arm stayed green
    (a bare invocation has no `@` to match). Bare `wrangler` is legal only in the
    boot steps, where the install-and-verify step already proved the binary.
    """
    deploys = _deploy_steps()
    assert len(deploys) == 2, f"expected 2 deploy steps, found {len(deploys)}"
    offenders = [
        f"{job}: {hit}" for job, step in deploys for hit in _bare_invocations(_run(step))
    ]
    script = REPO_ROOT / "website" / "apps" / "dashboard" / "deploy.sh"
    offenders += [f"deploy.sh: {hit}" for hit in _bare_invocations(script.read_text())]
    assert not offenders, (
        "a deploy path invokes wrangler without the declared spec, so the version it "
        "uploads with is not the one that was asserted:\n" + "\n".join(offenders)
    )


def test_the_bare_invocation_scan_ignores_messages_and_comments() -> None:
    for code in (
        'npx --yes wrangler pages deploy "$STAGE"',
        "npx wrangler --version",
        "wrangler pages deploy dist",
        # A QUOTED lone command word is still a command: `_strip_quoted` removes
        # quoted segments, so before #8852's review this spelling — named in the
        # module header — passed the scan untouched.
        'npx --yes "wrangler" pages deploy dist',
        "npx --yes 'wrangler' pages deploy dist",
    ):
        assert _bare_invocations(code), code
    for code in (
        'got="$(npx --yes "wrangler@${WRANGLER_VERSION:?}" --version)"',
        'echo "::error::resolved wrangler ${got} != declared ${WRANGLER_VERSION}"',
        "# npx --yes wrangler pages deploy (the old float)",
    ):
        assert not _bare_invocations(code), code


def test_no_step_invokes_wrangler_without_a_spec_outside_the_declared_boots() -> None:
    """The deploy-path arm above is keyed on the TWO deploy steps; this one is not.

    A spec-LESS invocation has no `wrangler@` token and, in a step that is not
    named `Deploy to Cloudflare Pages…`, no arm read it either — measured on this
    file's predecessor: a one-off `npx --yes wrangler pages deploy dist` inserted
    into `welcome-e2e`, and a whole new job booting a bare `npx wrangler`, BOTH
    survived every arm. A bare `wrangler` is therefore legal in exactly one place:
    the five declared boot steps, which run the binary the verify step proved
    (`test_boot_steps_carry_no_spec_and_keep_their_retry` pins that there are
    exactly five and that each carries no spec). Everything else — a new step, a
    new job, a deploy line — is an offender.

    The scan is quote- and comment-aware (`_strip_quoted` / `_cut_comment`), so a
    diagnostic that NAMES wrangler, or a commented-out old command, is not an
    offence; the count assertion keeps the arm from passing vacuously.
    """
    offenders = []
    boots = 0
    for path in _workflow_paths():
        for job, step in _document_steps(_load(path)):
            name = step.get("name") or ""
            hits = _bare_invocations(_run(step))
            if name.startswith(BOOT_STEP_PREFIX):
                assert hits, (
                    f"{path.name}:{job}: {name!r} is a declared boot but invokes no "
                    "wrangler — the probe is wrong"
                )
                boots += len(hits)
                continue
            offenders += [f"{path.name}:{job}: {name!r}: {hit}" for hit in hits]
    assert boots == 5, f"expected 5 spec-free boot invocations, found {boots}"
    assert not offenders, (
        "wrangler invoked WITHOUT a spec outside the five declared boots — a bare "
        "invocation resolves to whatever npm serves, which is the float this file "
        "exists to kill:\n" + "\n".join(offenders)
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
            # with a spec or without one. Keying on `wrangler@` alone let a new
            # job that calls a bare `npx wrangler` escape this arm entirely (the
            # reviewer measured exactly that mutation surviving every arm).
            installs = any(
                "wrangler@" in _run(step)
                or "npm install -g wrangler" in _run(step)
                or _bare_invocations(_run(step))
                for step in steps
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


# ------------------------------------------------ 6. the manual deploy script


def test_the_manual_deploy_script_pins_the_same_version() -> None:
    """`website/apps/dashboard/deploy.sh` is the documented manual deploy.

    It is outside the workflows, so no other arm reaches it — and a bare
    `npx wrangler pages deploy` there ships whatever npm serves, with nothing
    asserting what that was. It reads the same artefact.
    """

    script = REPO_ROOT / "website" / "apps" / "dashboard" / "deploy.sh"
    assert script.is_file(), f"{script} is missing"
    text = script.read_text(encoding="utf-8")
    code = "\n".join(_cut_comment(ln) for ln in text.splitlines())
    # The read must go through an ABSOLUTE script dir resolved BEFORE any `cd`.
    # This arm used to pin the literal `cat "$(dirname "$0")/…config/wrangler-version"`,
    # which pinned the BUG: after `cd "$(dirname "$0")"`, a RELATIVE `$0` makes that
    # path double-apply the prefix, so the documented
    # `bash website/apps/dashboard/deploy.sh` aborted with "No such file or
    # directory" — and no arm could see it, because the arm asserted the broken
    # text verbatim (#8816 review).
    assert 'SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"' in code, (
        "the manual deploy does not resolve an ABSOLUTE script dir — a relative `$0` "
        "re-derived after `cd` points at a path that does not exist"
    )
    assert 'cat "$SCRIPT_DIR/../../../config/wrangler-version"' in code, (
        "the manual deploy does not read config/wrangler-version through SCRIPT_DIR — "
        "it would deploy an unasserted wrangler"
    )
    assert 'cat "$(dirname "$0")/../../../config/wrangler-version"' not in code, (
        "the relative-`$0` read is back: after `cd`, it double-applies the prefix and "
        "the documented manual deploy aborts"
    )
    assert "--version" in code, "the manual deploy never asserts what resolved"
    declared = VERSION_FILE.read_text(encoding="utf-8").strip()
    assert declared not in code, (
        f"the manual deploy restates the pinned version ({declared}); it must read "
        f"{VERSION_SOURCE}, or a bump edits one place and drifts in another"
    )
    for match in re.finditer(r"wrangler@\S*", code):
        assert LEGAL_SPEC.match(match.group(0)), (
            f"manual deploy spec {match.group(0)!r} is not the asserted variable form"
        )


#: Stub `npm` for the executed arms: `npm ci` is skipped by a pre-existing
#: node_modules and `npm run build` is not the subject.
STUB_NPM = "#!/bin/sh\nexit 0\n"

#: Stub `npx`: answers the `--version` probe with the declared value and records
#: its argv, so the harness can prove WHICH spec the deploy would use.
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
    `config/wrangler-version`), so the script's own path arithmetic is under test
    rather than a string in a file.
    """
    dashboard = tmp_path / "website" / "apps" / "dashboard"
    dashboard.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "website" / "apps" / "dashboard" / "deploy.sh",
                 dashboard / "deploy.sh")
    (dashboard / "node_modules").mkdir()  # skips `npm ci`
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "wrangler-version").write_text(
        VERSION_FILE.read_text(encoding="utf-8"), encoding="utf-8"
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
def test_the_manual_deploy_resolves_the_version_from_either_invocation(
    tmp_path, from_repo_root: bool
) -> None:
    """EXECUTED, because a text pin cannot see a path that does not resolve.

    The text arm above pins the shape. This one runs the shipped script the way
    the runbook documents (`./website/apps/dashboard/deploy.sh` from the repo
    root) AND from its own directory, and requires the deploy to carry the
    declared version. On the pre-fix script (re-derived `$(dirname "$0")` after
    the `cd`) the repo-root invocation aborts inside `cat` under `set -e` with
    `…/website/apps/dashboard/../../../config/wrangler-version: No such file or
    directory` — one of these two arms would have failed and the other passed,
    which is exactly the blind spot the old single text pin had.
    """
    r = _run_manual_deploy(tmp_path, from_repo_root=from_repo_root)
    out = r.stdout + r.stderr
    where = "the repo root" if from_repo_root else "its own directory"
    assert r.returncode == 0, f"deploy.sh failed when invoked from {where}:\n{out}"
    args = (tmp_path / "npx_args").read_text(encoding="utf-8").splitlines()
    declared = VERSION_FILE.read_text(encoding="utf-8").strip()
    assert args[:2] == ["--yes", f"wrangler@{declared}"], (
        f"the manual deploy ran {args!r} — it must carry the version it read"
    )


# ------------------------------------------- 6. the documentation that teaches it


def test_no_document_shows_a_floating_wrangler_spec() -> None:
    """A README example is a command someone will paste.

    `website/README.md` showed `npx --yes wrangler@4 pages deploy` next to two
    shipped steps that assert 4.127.0 — the exact drift this change removes,
    stated as guidance. Every spec in these files must be the asserted form, and
    the version must come from the one artefact.
    """

    for rel in COMMAND_DOCS:
        path = REPO_ROOT / rel
        assert path.is_file(), f"{rel} is missing"
        text = path.read_text(encoding="utf-8")
        # Only FENCED code, not prose: a doc may legitimately name the bad
        # spelling while explaining why it is bad.
        code = "\n".join(
            line for line in _fenced_blocks(text).splitlines()
            if not line.lstrip().startswith("#")
        )
        offenders = [
            token
            for token in re.findall(r"wrangler@[^\s`\"')]+", code)
            if not LEGAL_SPEC.match(token)
        ]
        assert not offenders, (
            f"{rel} still shows a copy-pasteable wrangler spec that is not the "
            f"asserted form ({offenders}) — it would ship whatever npm serves"
        )
        if "wrangler@" in code:
            assert "config/wrangler-version" in text, (
                f"{rel} shows a wrangler command without pointing at "
                "config/wrangler-version — the version has one home"
            )
