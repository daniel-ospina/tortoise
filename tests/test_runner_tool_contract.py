"""The runner tool contract (#9018) — the probe, and the coverage that keeps it from rotting.

Why a test rather than trust
----------------------------
`runs-on: ubuntu-latest` names a GitHub-HOSTED IMAGE, and `ubuntu-24.04` names a HOSTED
version pin. The fallback pool has served BOTH: `scripts/ci_runner_gate.sh` attaches
`ubuntu-latest` while the fallback is engaged, and 2026-10-24's measurement found every
failing `finding-provenance` `classify` job that ran on `pi-runner-vm-*` holding the
`ubuntu-24.04` pin as well — a live violation of the recorded invariant that no self-hosted
runner claims an `ubuntu-<version>` label (`docs/ops/ci-runner-x64.md`, `MEMORY.md`, and the
drift arm in `tests/test_ci_selection.py` that reads the GATE'S TEXT rather than the live
label set, which is why it did not fire).

Either label can land a job on a bare runner whose service environment is a minimal
NON-login `PATH`, where a tool of the hosted image is simply absent. Measured 2026-10-24,
all on `pi-runner-vm-*` and none on GitHub-hosted: 38 of 39 `finding-provenance` `classify`
failures plus `canary-streak`, `availability-watchdog/probe`, `availability-liveness/check`,
`ci-check-repair/repair` — `line 2: gh: command not found` / `Process completed with exit
code 127`. Each red LOOKED like a test failure. Two disguised themselves further: a
`if ! out="$(gh … 2>/dev/null)"` wrapper swallowed the `command not found`, so the runner's
fault was re-reported as the instrument's own verdict.

The contract is `.github/scripts/runner-tools.sh`. This file proves it DISCRIMINATES — a
probe that only ever passes proves nothing — and that every job using `gh` is either
carrying it or declared as a known gap.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / ".github" / "scripts" / "runner-tools.sh"
WORKFLOWS = REPO / ".github" / "workflows"

MARKER = "RUNNER-TOOL-CONTRACT-UNSATISFIED"

#: Jobs that NEED `gh` and MAY land on the pool, each carrying the contract step.
#: Two entries are the measured 2026-10-24 reds and four are the same hazard found by
#: the scan at the bottom of this file (a job that shells out to `gh` and asks for a
#: label the pool has served). Each one calls `gh` either inline or through
#: `tools/ci_check_repair.py` / `tools/ci_queue_health.py` / `tools/ci_timing.py` /
#: `.github/scripts/availability-*.sh`.
GH_JOBS = {
    "python-ci.yml": ["canary-streak"],
    "finding-provenance.yml": ["classify", "provenance"],
    "availability-watchdog.yml": ["probe"],
    "availability-liveness.yml": ["check"],
    "ci-check-repair.yml": ["repair"],
    "ci-queue-health.yml": ["queue-health"],
    "ci-timing.yml": ["measure", "refresh"],
    "mergify-guard-recut.yml": ["recut"],
    "availability-record.yml": ["record"],
    "welcome-e2e-monitor.yml": ["welcome-e2e"],
    "tenant-provision-monitor.yml": ["monitor"],
    "e2e-live-reconcile.yml": ["reconcile"],
    "prod-csp-monitor.yml": ["audit"],
    "supabase-deploy.yml": ["deploy"],
}

#: Jobs that use `gh` and are NOT covered, with the reason each one cannot simply take
#: the step. Declared rather than omitted: an undeclared gap is indistinguishable from
#: coverage nobody checked, which is the defect class this file exists for. The scan at
#: the bottom of this file reports EVERY such job, so this map cannot quietly shrink.
KNOWN_UNCOVERED = {
    # The job has NO `actions/checkout`, so `.github/scripts/runner-tools.sh` is not in
    # the workspace and the step cannot run. It is a required context, so a 127 here
    # blocks every merge — closing it means adding a checkout (`persist-credentials:
    # false`, since the token it carries is `pull-requests: read` only) or inlining the
    # provisioner, and that is a change to a gate #1224 documents carefully. Filed as
    # its own task rather than smuggled into this one.
    "ai-review-gate.yml": {
        "ai-review-gate": "no checkout in the job — the contract script is not in the workspace"
    },
    # `mergify_config_guard.py` executes `gh` only in `--live` mode, whose own docstring
    # records that CI must never use it (a fail-closed admin read inside CI would
    # deadlock every PR). The job runs `--static`, which reads committed files.
    "python-ci.yml": {
        "manifest-integrity": "mergify_config_guard.py touches gh only under --live, which CI never runs"
    },
    # These jobs run `*.test.sh` harnesses, which install a STUB `gh` on PATH
    # (`.github/scripts/ai-review-gate.test.sh:416` writes `$T/bin/gh`). A harness that
    # stubs its tool is not a hazard; giving it the real one would mask the stub.
    "ci.yml": {
        "ops-harnesses": "runs *.test.sh harnesses that stub `gh` on PATH",
        "repo-harnesses": "runs *.test.sh harnesses that stub `gh` on PATH",
    },
}

CONTRACT_STEP = "runner-tools.sh ensure gh"


#: The MECHANICAL half of each declared gap's reason. A reason is prose and cannot be
#: argued with, so the claim that makes the gap acceptable is asserted instead: if the
#: checkout is added to `ai-review-gate`, or `--live` appears in `manifest-integrity`, or
#: a harness stops stubbing `gh`, the declaration stops being true and this fails.
#: ⛔ Keyed exactly as `KNOWN_UNCOVERED` is, and the key sets must match — adding a
#: declaration without a check is refused, so the map cannot grow silently, which is the
#: same defect one level up from the typed hazard list this file already removed.
_GAP_CHECKS = {
    ("ai-review-gate.yml", "ai-review-gate"): lambda block: "uses: actions/checkout"
    not in block,
    # The guard's `gh` is confined to `--live`, so the claim is about the MODE it runs
    # in — not about the string appearing in the block (the job comments the absence of
    # `--live` twice, which a bare substring test reads as a violation).
    ("python-ci.yml", "manifest-integrity"): lambda block: not re.search(
        r"mergify_config_guard\.py\s+--live", block
    ),
    ("ci.yml", "ops-harnesses"): lambda block: "test.sh" in block,
    ("ci.yml", "repo-harnesses"): lambda block: "test.sh" in block,
}


# ── the probe: the same call, both polarities ────────────────────────────────


def _run(args, *, env=None, cwd=None):
    base = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": "/tmp",
        # Keep the probe honest: the ambient shell's PATH is exactly what the
        # runner service does NOT have, so we build the environment explicitly.
    }
    if env:
        base.update(env)
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=base,
        cwd=str(cwd or REPO),
        check=False,
    )


def _stub_tool_dir(tmp_path: Path, name: str) -> Path:
    d = tmp_path / "stubbin"
    d.mkdir(exist_ok=True)
    exe = d / name
    exe.write_text("#!/bin/sh\necho stub\n")
    exe.chmod(0o755)
    return d


#: The external commands `install_gh` needs. A PATH built from symlinks to just
#: these carries the utilities but NOT `gh` — which is the whole point: on this
#: repo's hosts `gh` lives in `/usr/bin` beside them, so "absent" cannot be
#: spelled by dropping `/usr/bin`.
TOOLBOX = ("bash", "gzip", "mkdir", "rm", "find", "head", "tar", "ln", "sha256sum", "cut", "uname", "curl")


def _toolbox_without_gh(tmp_path: Path) -> str:
    box = tmp_path / "toolbox"
    box.mkdir(exist_ok=True)
    for tool in TOOLBOX:
        found = shutil.which(tool)
        assert found, f"the probe needs {tool} on the host running it"
        link = box / tool
        if not link.exists():
            link.symlink_to(found)
    return str(box)


def test_assert_passes_when_the_tool_resolves(tmp_path):
    """POLARITY A — present: exit 0, and the line names the resolved path."""
    cli = "gh"
    d = _stub_tool_dir(tmp_path, cli)
    out = _run(["assert", cli], env={"PATH": f"{d}:/usr/local/bin:/usr/bin:/bin"})
    assert out.returncode == 0, out.stderr
    assert f"tool={cli} action=present" in out.stdout
    assert str(d / cli) in out.stdout


def test_assert_fails_naming_the_tool_when_it_is_absent(tmp_path):
    """POLARITY B — absent: exit 1 AND a marker that names the tool and the runner.

    This is the whole point: the failure a pool red produces must say `gh is
    missing on <runner>`, never `Process completed with exit code 127`.
    """
    cli = "gh"
    out = _run(
        ["assert", cli],
        env={"PATH": _toolbox_without_gh(tmp_path), "RUNNER_NAME": "pi-runner-vm-5"},
    )
    assert out.returncode == 1, (out.stdout, out.stderr)
    assert MARKER in out.stderr
    assert f"missing={cli}" in out.stderr
    assert "runner=pi-runner-vm-5" in out.stderr
    assert "command not found" not in out.stderr  # the old, unhelpful shape


def test_ensure_refuses_to_lie_when_it_cannot_provision(tmp_path):
    """No egress and no binary is UNSATISFIED (1), and nothing is claimed green.

    Nothing was installed, so this is the `absent` class — not `could-not-provide`.
    """
    cli = "gh"
    out = _run(
        ["ensure", cli],
        env={
            "PATH": _toolbox_without_gh(tmp_path),
            "RUNNER_TOOL_CONTRACT_OFFLINE": "1",
            "RUNNER_NAME": "pi-runner-vm-5",
        },
    )
    assert out.returncode == 1, (out.stdout, out.stderr)
    assert "reason=offline-and-absent" in out.stderr


def _tarball_with_stub_gh(tmp_path: Path, payload: bytes) -> tuple[Path, str]:
    """A gh-release-shaped archive: <ver>/bin/gh, plus its sha256."""
    root = tmp_path / "src"
    bindir = root / "gh_9.9.9_linux_amd64" / "bin"
    bindir.mkdir(parents=True)
    exe = bindir / "gh"
    exe.write_bytes(payload)
    exe.chmod(0o755)
    archive = tmp_path / "gh_9.9.9_linux_amd64.tar.gz"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        tf.add(bindir.parent, arcname="gh_9.9.9_linux_amd64")
    archive.write_bytes(buf.getvalue())
    return archive, hashlib.sha256(archive.read_bytes()).hexdigest()


def test_ensure_provisions_and_proves_it_resolves(tmp_path):
    """POLARITY C — absent + provisionable: installed, linked, and on GITHUB_PATH."""
    cli = "gh"
    archive, digest = _tarball_with_stub_gh(tmp_path, b"#!/bin/sh\necho 'gh 9.9.9 stub'\n")
    gh_path_file = tmp_path / "github_path"
    gh_path_file.write_text("")
    bindir = tmp_path / "installed"
    out = _run(
        ["ensure", cli],
        env={
            "PATH": _toolbox_without_gh(tmp_path),
            "GITHUB_PATH": str(gh_path_file),
            "RUNNER_TOOL_CONTRACT_BIN_DIR": str(bindir),
            "RUNNER_TOOL_CONTRACT_GH_TARBALL": str(archive),
            "RUNNER_TOOL_CONTRACT_GH_SHA256": digest,
            "RUNNER_NAME": "stub-runner",
        },
    )
    assert out.returncode == 0, (out.stdout, out.stderr)
    assert "action=installed" in out.stdout
    assert str(bindir / "bin" / "gh") in out.stdout
    # The artifact, not the message: the link exists, is executable, and the
    # next step's PATH file names its directory.
    assert (bindir / "bin" / "gh").exists()
    assert os.access(bindir / "bin" / "gh", os.X_OK)
    assert str(bindir / "bin") in gh_path_file.read_text()


def test_ensure_refuses_to_certify_an_install_it_could_not_publish(tmp_path):
    """In Actions, an install nobody later sees is a FAILURE, not an install.

    `0` means "every requested tool resolves" — for the JOB, not for this shell. The
    runner drains `$GITHUB_PATH` into later steps, so a provisioned binary that was
    never published is invisible to the step that needs it, and returning 0 would
    certify a tool the job does not have. Actions always sets `$GITHUB_PATH`, so its
    absence there is an anomaly, and this is the "0 without the tool proven" case the
    file's own doctrine forbids. Gated on `GITHUB_ACTIONS` so a local `ensure` on a
    dev box (where nothing later needs publishing) still works.
    """
    archive, digest = _tarball_with_stub_gh(tmp_path, b"#!/bin/sh\necho 'gh 9.9.9 stub'\n")
    out = _run(
        ["ensure", "gh"],
        env={
            "PATH": _toolbox_without_gh(tmp_path),
            "GITHUB_ACTIONS": "true",
            # deliberately no GITHUB_PATH
            "RUNNER_TOOL_CONTRACT_BIN_DIR": str(tmp_path / "installed"),
            "RUNNER_TOOL_CONTRACT_GH_TARBALL": str(archive),
            "RUNNER_TOOL_CONTRACT_GH_SHA256": digest,
            "RUNNER_NAME": "stub-runner",
        },
    )
    assert out.returncode == 2, (out.stdout, out.stderr)
    assert "reason=provisioned-but-not-published" in out.stderr


def test_ensure_refuses_an_archive_that_does_not_match_the_pin(tmp_path):
    """The pin is load-bearing: a wrong archive must not be installed."""
    cli = "gh"
    archive, _real_digest = _tarball_with_stub_gh(tmp_path, b"#!/bin/sh\necho impostor\n")
    gh_path_file = tmp_path / "github_path"
    gh_path_file.write_text("")
    out = _run(
        ["ensure", cli],
        env={
            "PATH": _toolbox_without_gh(tmp_path),
            "GITHUB_PATH": str(gh_path_file),
            "RUNNER_TOOL_CONTRACT_BIN_DIR": str(tmp_path / "installed"),
            "RUNNER_TOOL_CONTRACT_GH_TARBALL": str(archive),
            # No SHA256 seam: the pinned release digest is used, and it cannot match.
        },
    )
    assert out.returncode == 2, (out.stdout, out.stderr)
    assert "reason=sha256-mismatch" in out.stderr
    assert gh_path_file.read_text() == ""
    assert not (tmp_path / "installed" / "bin" / "gh").exists()


def test_a_lone_sha256_override_is_refused(tmp_path):
    """The pin cannot be loosened without also naming the archive it belongs to."""
    cli = "gh"
    out = _run(
        ["ensure", cli],
        env={"PATH": _toolbox_without_gh(tmp_path), "RUNNER_TOOL_CONTRACT_GH_SHA256": "0" * 64},
    )
    assert out.returncode == 2, (out.stdout, out.stderr)
    assert "reason=sha256-override-without-tarball-seam" in out.stderr


def test_unknown_verb_is_a_usage_error():
    out = _run(["frobnicate", "gh"])
    assert out.returncode == 1
    assert "unknown verb" in out.stderr


def test_no_arguments_is_a_usage_error_not_a_green_no_op():
    """A `run:` that forgets its arguments must FAIL, not silently pass.

    A step that provisions nothing and exits 0 is the "no-op gate" shape: the
    YAML looks like it enforces the contract and enforces nothing.
    """
    out = _run([])
    assert out.returncode == 1, (out.stdout, out.stderr)
    assert "no verb" in out.stderr


def test_a_tool_that_cannot_be_provisioned_is_named_not_skipped(tmp_path):
    """`unzip` needs root; the contract must say so rather than pretend."""
    out = _run(
        ["ensure", "unzip"],
        env={"PATH": _toolbox_without_gh(tmp_path), "RUNNER_NAME": "pi-runner-vm-5"},
    )
    assert out.returncode == 1, (out.stdout, out.stderr)
    assert MARKER in out.stderr
    assert "tool=unzip" in out.stderr
    assert "reason=absent-not-provisionable" in out.stderr


# ── the coverage: a new gh-using job must declare the contract ───────────────


def _job_block(path: Path, job: str) -> str:
    """The YAML slice for one job — from `  <job>:` to the next top-level job.

    Deliberately textual: this repo has no YAML-with-position loader available in
    the URI-unset leg, and the assertion only needs the job's own text.

    The trailing `(#…)` group is not decoration. `  newjob:  # runs gh` is valid
    YAML, and without it the job key does not match, its lines are absorbed into
    the PREVIOUS job, and a first-job version makes `_jobs()` return nothing — the
    whole workflow then goes uninspected while the suite stays green.
    """
    lines = path.read_text().splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(rf"^  {re.escape(job)}:\s*(?:#.*)?$", line):
            start = i
            break
    assert start is not None, f"{path.name}: no job `{job}`"
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if re.match(r"^\S", lines[j]) or re.match(
            r"^  [A-Za-z0-9_.-]+:\s*(?:#.*)?$", lines[j]
        ):
            end = j
            break
    return "\n".join(lines[start:end])


def test_the_hazard_test_sees_the_repo_inline_gh_idiom():
    """The scan must see `out="$(gh …)"` — the idiom used throughout this repo.

    A previous version stripped quoted spans before scanning, which erased the
    whole argument list of a single-line command substitution. That made the scan
    blind to 21 non-comment occurrences in five helper scripts and to a live
    workflow (`ai-review-gate.yml`: `if fetched="$(gh api …)"`), i.e. blind to the
    NORMAL way a `gh` call is written here. These three lines are the discrimination
    proof: the same fix must SEE the call and still not see the prose.
    """
    scripts = frozenset()
    assert _is_hazard_line('        run: out="$(gh issue list --label x)"', scripts)
    assert _is_hazard_line('          if fetched="$(gh api repos/a/b/pulls/1)"; then', scripts)
    assert _is_hazard_line("        run: gh issue list --label x", scripts)
    assert _is_hazard_line("      - run: |\n        gh pr view 1", scripts)
    # …and prose that names a command is NOT a call. Without this the scan reds on
    # supabase-deploy's operator message and earns a `|| true`.
    assert not _is_hazard_line(
        '        run: echo "::error::run it FROM main: gh workflow run supabase-deploy.yml"',
        scripts,
    )
    assert not _is_hazard_line(
        '        run: echo "clear it with `gh variable delete X`"', scripts
    )
    assert not _is_hazard_line("      # gh api /repos/x is called later", scripts)
    assert not _is_hazard_line("      - name: gh probe step", scripts)


def test_a_script_basename_used_as_DATA_is_not_a_hazard():
    """`isfile("….test.sh")` and a glob are not invocations.

    `ci.yml::changes` evaluates `isfile(".github/scripts/ai-review-gate.test.sh")` and
    `deploy-bypass.sh` merely PRINTS `gh variable delete …` inside emitted markdown,
    so neither job needs the contract. Both were false positives before the reference
    had to be at an execution position with an interpreter or a `./` prefix; a scan
    that demands the step for them is a scan that gets suppressed.
    """
    scripts = frozenset({"ai-review-gate.test.sh", "deploy-bypass.sh"})
    assert not _is_hazard_line(
        '        run: python -c "import os; os.path.isfile(\'.github/scripts/ai-review-gate.test.sh\')"',
        scripts,
    )
    assert not _is_hazard_line('        run: echo "${{ steps.x.outputs.files }}"', scripts)
    # …but actually RUNNING one is the hazard, with either invocation shape.
    assert _is_hazard_line("        run: bash .github/scripts/deploy-bypass.sh report", scripts)
    assert _is_hazard_line("        run: ./.github/scripts/deploy-bypass.sh audit", scripts)
    assert _is_hazard_line(
        "        run: uv run python tools/finding_provenance.py classify",
        frozenset({"finding_provenance.py"}),
    )


def test_every_workflow_job_is_seen_by_the_line_scanner():
    """A workflow the line scanner cannot see is a workflow the scan never checks.

    `_jobs()` is textual. A job key with a trailing comment (`  newjob:  # runs gh`)
    is valid YAML that the first regex did not match, and a FIRST job written that way
    made `_jobs()` return nothing at all — the file then went uninspected while the
    suite stayed green. Cross-checking against a real YAML parse makes that loud.
    """
    for path in sorted(WORKFLOWS.glob("*.yml")):
        scanned = {name for name, _ in _jobs(path)}
        parsed = set(yaml.safe_load(path.read_text())["jobs"])
        assert scanned == parsed, f"{path.name}: line scan saw {scanned ^ parsed} differently"


def test_the_coverage_scan_is_not_vacuous():
    """A scan that inspects nothing passes everything.

    Pin the floor: the derived script set must contain the scripts known to carry
    `gh`, and the flagged-job population must be non-empty. Without this, a broken
    regex silently turns the whole coverage test into `assert []`.
    """
    scripts = _gh_executing_scripts()
    for name in (
        "availability-liveness.sh",
        "availability-watchdog.sh",
        "availability-record.sh",
        "welcome-e2e-monitor.sh",
        "auto-file-issue.sh",
        "ci_check_repair.py",
        "ci_queue_health.py",
        "ci_timing.py",
        "finding_provenance.py",
    ):
        assert name in scripts, f"{name} calls `gh` but the derivation missed it"
    # `deploy-bypass.sh` only PRINTS `gh …` inside emitted markdown — including it
    # would demand the step from three jobs that never run `gh`.
    assert "deploy-bypass.sh" not in scripts

    flagged = [
        (path.name, job)
        for path in sorted(WORKFLOWS.glob("*.yml"))
        for job, body in _jobs(path)
        if _gh_hazards(body, scripts)
        and _POOLABLE_LABEL.search("\n".join(body))
    ]
    assert len(flagged) >= 16, f"the scan flagged only {flagged} — the hazard test broke"


@pytest.mark.parametrize(("workflow", "job"), [(w, j) for w, js in GH_JOBS.items() for j in js])
def test_every_gh_using_pool_job_carries_the_contract_step(workflow, job):
    """If a job that calls `gh` can land on the pool, it must provide it first.

    The step must be the FIRST shell step in the job: a `gh` call that precedes it
    still dies with 127, and the ordering cannot be seen from the exit code.
    """
    block = _job_block(WORKFLOWS / workflow, job)
    assert CONTRACT_STEP in block, (
        f"{workflow}::{job} calls `gh` but does not run `{CONTRACT_STEP}` — a pool "
        "runner without it fails with exit 127, which reads as a test failure (#9018)"
    )
    lines = block.splitlines()
    # The hazard, not the position: a `run:` step BEFORE the contract that calls
    # `gh` (inline or through one of the scripts that shell out to it) is the
    # thing that dies with 127. A benign earlier step is not a defect, and a pin
    # that reds on one is a pin that gets a `|| true`.
    #
    # ⛔ The hazard set is DERIVED, never typed. It used to be a literal regex of
    # five script names, which is the same defect this file removes one arm over:
    # it did not know `availability-record.sh`, `welcome-e2e-monitor.sh` or
    # `auto-file-issue.sh`, so moving the step to the END of those five jobs left
    # the ordering UNPINNED while the suite stayed green (measured).
    scripts = _gh_executing_scripts()
    contract_at = next(i for i, line in enumerate(lines) if CONTRACT_STEP in line)
    for i, line in enumerate(lines):
        if i >= contract_at:
            break
        assert not _is_hazard_line(line, scripts), (
            f"{workflow}::{job}: a step at line {i + 1} uses `gh` BEFORE the contract "
            f"step — it can still die with 127. Offending line: {line.strip()!r}"
        )
    # And the contract step must still exist and carry a `run:` (the step is the
    # shell invocation; a `name:` alone provisions nothing).
    assert re.match(r"^        run: .*" + re.escape(CONTRACT_STEP), lines[contract_at]), (
        f"{workflow}::{job}: the contract step's `run:` is not `{CONTRACT_STEP}`"
    )


# ── the scan: a NEW gh-using job must be declared, not silently uncovered ────

#: `gh` at COMMAND position. Deliberately narrow: it must not fire on `GH_TOKEN`,
#: on a filename, or on prose (`gh` inside a comment is skipped by the caller).
_GH_CMD = re.compile(
    r"(?<![\w./-])gh\s+(api|run|pr|issue|search|workflow|label|release|repo|auth|secret|variable)"
)
#: `gh` at EXECUTION position. Anchored on the construct that EXECUTES it — command
#: substitution, a subshell/compound start, a pipeline or list separator, or a `then`/`do`/
#: `else` body — so a mention in emitted prose is not a call.
_GH_EXEC_SITE = re.compile(
    r"(?:\$\(|`|\|\||&&|[;|({]|\bthen\b|\bdo\b|\belse\b)\s*(?:!\s*)?"
    r"(?:(?:env\s+)?[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*"
    r"gh\s+(api|run|pr|issue|search|workflow|label|release|repo|auth|secret|variable)"
)
#: PROSE positions: a line that only PRINTS text is not a call. Without this the scan
#: reds on `supabase-deploy.yml`'s operator message (`echo "::error::… gh workflow run
#: …"`) — and a lint that reds on prose earns a `|| true`. The `run:` / `- run:`
#: prefix is part of the position, not decoration: `run: echo "… gh …"` starts with
#: `run:`, so an anchor that only accepted `^echo` saw it as code.
_PROSE = re.compile(
    r"(?:^|:\s*)(?:#|-\s*name:|-?\s*(?:echo|printf|emit|say|log|warn|die|fail)\b)"
    r"|::(?:error|warning|notice|debug)::"
)
#: Labels the fallback pool has been observed to serve. Not `ubuntu-latest` only:
#: the 2026-10-24 measurement shows the pool serving `ubuntu-24.04` as well.
_POOLABLE_LABEL = re.compile(r"ubuntu-(latest|\d+\.\d+)")


#: `gh` at an EXECUTION site inside a HELPER SCRIPT (not a workflow line), used to
#: DERIVE which scripts carry the hazard. A text list of script names was the first
#: attempt and it under-counted: five poolable jobs run scripts it did not name
#: (`availability-record.sh`, `welcome-e2e-monitor.sh`, `auto-file-issue.sh`).
#: Deriving the set means a new script cannot be forgotten — it is found, or it is
#: not executing `gh`. The argv-literal and `GH_BIN` shapes are here and not in
#: `_GH_EXEC_SITE` because only Python helpers put `gh` in a list; a WORKFLOW never
#: does.
_GH_EXEC = (
    re.compile(r"\(\s*gh\s"),                                    # `$(gh …)` / subshell
    re.compile(r"[;&|(]\s*gh\s+(api|run|pr|issue|search|workflow|label|release|repo)"),
    re.compile(r"^\s*gh\s+(api|run|pr|issue|search|workflow|label|release|repo)", re.M),
    re.compile(r'''["']gh["']\s*,'''),                        # subprocess argv literal
    re.compile(r"\bGH_BIN\b"),                                   # resolved, then exec'd
)

#: How a helper script is INVOKED from a workflow line. `bash .github/scripts/x.sh`
#: executes it; `isfile(".github/scripts/x.sh")` in a python one-liner does not, and
#: neither does a basename in a path glob — both were false positives before this
#: required an interpreter or a `./` prefix AND a path that is a bare argument.
#: ⛔ The `[^"']*?` is load-bearing: without it `python -c "import os; os.path.isfile(
#: '….sh')"` parses as "python, then a name", which is how a pure path TEST got
#: classified as an invocation.
_SCRIPT_RUN = re.compile(
    r"(?:^|[\s;|&(])"
    r"(?:(?:\./|(?:bash|sh|python3?|node|source|\.)\s+)[^\n\"']*?)"
    r"(?P<name>(?:[\w.-]+/)*[\w.-]+\.(?:sh|py))\b"
)


def _gh_executing_scripts() -> frozenset[str]:
    """Basenames of the helper scripts that INVOKE `gh` (derived, never listed)."""
    out = set()
    for pattern in (".github/scripts/*.sh", "tools/*.py", "tools/*.sh"):
        for path in REPO.glob(pattern):
            code = "\n".join(
                line for line in path.read_text(errors="ignore").splitlines()
                if not line.strip().startswith("#")
            )
            if any(rule.search(code) for rule in _GH_EXEC):
                out.add(path.name)
    return frozenset(out)


def _jobs(path: Path) -> list[tuple[str, list[str]]]:
    """(job name, its lines) for every job in a workflow file.

    ⛔ Scoped to the `jobs:` block. Every 2-space key in the file is NOT a job:
    `on:`'s children (`  pull_request_target:`) and any other top-level mapping
    share the indent, and the first version collected them — `ai-review-gate.yml`
    "had" a job called `pull_request_target`. Fake entries inflate the population
    and, worse, shift `_job_block`'s boundary so a real job's text leaks into a
    neighbour and a hazard lands on the wrong job.
    """
    lines = path.read_text().splitlines()
    try:
        top = next(
            i for i, line in enumerate(lines) if re.match(r"^jobs:\s*(?:#.*)?$", line)
        )
    except StopIteration:
        return []
    starts = []
    for i in range(top + 1, len(lines)):
        if re.match(r"^\S", lines[i]):  # back to column 0 — the jobs block ended
            break
        if m := re.match(r"^  ([A-Za-z0-9_.-]+):\s*(?:#.*)?$", lines[i]):
            starts.append((i, m.group(1)))
    out = []
    for n, (start, name) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else len(lines)
        out.append((name, lines[start:end]))
    return out


def _is_hazard_line(line: str, scripts: frozenset[str]) -> bool:
    """Does this workflow line EXECUTE `gh` (inline, or through a helper script)?

    ⛔ Why not strip quoted spans first. The previous version did, and that made the
    scan blind to the repo's DOMINANT idiom rather than to an exotic one: a
    single-line `out="$(gh api …)"` lost its whole argument list, so the scan saw
    nothing. 21 non-comment lines across five helper scripts and a live workflow
    (`ai-review-gate.yml`, `if fetched="$(gh api …)"`) were invisible, and the one
    job the scan did catch was caught by an unrelated multi-line call. A new job
    written in the normal style would have taken no contract step and the suite
    would have stayed green — the false PASS this scan exists to prevent.

    So: remove PROSE POSITIONS only (a line that prints, a step name, a comment),
    then test the RAW text — for a command at execution position, or at any
    position (which is what catches `run: gh issue list`).
    """
    stripped = line.strip()
    if _PROSE.search(stripped):
        return False
    if _GH_EXEC_SITE.search(line) or _GH_CMD.search(line):
        return True
    # A helper script counts only when the line RUNS it (`bash <path>`, `./<path>`,
    # `uv run python <path>`) — not when its basename merely appears as data. The
    # captured value is a PATH, so compare its basename against the derived set.
    return any(
        Path(m.group("name")).name in scripts for m in _SCRIPT_RUN.finditer(line)
    )


def _gh_hazards(body: list[str], scripts: frozenset[str]) -> list[str]:
    return [line.strip() for line in body if _is_hazard_line(line, scripts)]


def test_every_declared_gap_still_names_a_real_job_and_a_true_reason():
    """A declaration is a claim about the tree, and it must be re-checked.

    Without this, `KNOWN_UNCOVERED` is a place any hazard can be parked with any
    sentence: a reason goes stale (add the checkout to `ai-review-gate` and the "no
    checkout" sentence absorbs a now-fixable job forever), an entry survives a job
    rename, and a real hazard can be silenced by typing a plausible line. That is the
    "the list is the coverage record" failure this scan was written to end, one level
    up — so every entry is re-measured against the file it names.
    """
    declared = {(wf, job) for wf, jobs in KNOWN_UNCOVERED.items() for job in jobs}
    assert declared == set(_GAP_CHECKS), (
        "a declaration without a mechanical check (or a check without a declaration): "
        f"{declared ^ set(_GAP_CHECKS)}"
    )
    scripts = _gh_executing_scripts()
    for (workflow, job), check in _GAP_CHECKS.items():
        path = WORKFLOWS / workflow
        assert path.exists(), f"KNOWN_UNCOVERED names a workflow that does not exist: {workflow}"
        # A job that was renamed or deleted must not keep absorbing a gap.
        assert job in {name for name, _ in _jobs(path)}, (
            f"KNOWN_UNCOVERED names a job that does not exist: {workflow}::{job}"
        )
        block = _job_block(path, job)
        assert check(block), (
            f"{workflow}::{job} is declared uncovered but the reason no longer holds — "
            "either it can take the contract step now, or a NEW hazard was added to it"
        )
        # And the declaration must still be about a real hazard: an entry for a job that
        # no longer uses `gh` is a stale declaration that would mask a future one.
        assert _gh_hazards(block.splitlines(), scripts), (
            f"{workflow}::{job} is declared as a `gh` gap but no longer uses `gh`"
        )


def test_every_pool_eligible_gh_job_is_declared():
    """The generalisation of the coverage pin: a NEW job cannot join the gap.

    Why the scan and not just the hand-list: `GH_JOBS` is a literal, so without this
    test a brand-new `runs-on: ubuntu-latest` job that shells out to `gh` would be
    uncovered and the suite would stay green — the same "it looks covered" defect the
    pin above is written against. Here every job that asks for a label the pool has
    served AND uses `gh` must be in `GH_JOBS` (covered) or `KNOWN_UNCOVERED` (a stated
    reason). Neither list is inferred.
    """
    wired = {(wf, job) for wf, jobs in GH_JOBS.items() for job in jobs}
    declared_gaps = {(wf, job) for wf, jobs in KNOWN_UNCOVERED.items() for job in jobs}
    scripts = _gh_executing_scripts()
    undeclared = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for job, body in _jobs(path):
            text = "\n".join(body)
            label = re.search(r"^\s+runs-on:\s*(.+)$", text, re.M)
            if not label:
                continue
            # `runs-on: ${{ matrix.os }}` never contains a literal label, so a hit is
            # only visible in the job's own `matrix:` block. 84 jobs use a literal
            # today, so this is a pin against the next one, not an active gap.
            poolable = bool(_POOLABLE_LABEL.search(label.group(1))) or (
                "matrix." in label.group(1) and bool(_POOLABLE_LABEL.search(text))
            )
            if not poolable:
                continue
            if not _gh_hazards(body, scripts):
                continue
            # Covered = carries the step (the property that matters) OR is declared.
            # `GH_JOBS` is the separate POSITION pin, not the coverage record.
            if CONTRACT_STEP in text:
                continue
            if (path.name, job) in wired or (path.name, job) in declared_gaps:
                continue
            undeclared.append(f"{path.name}::{job}")
    assert not undeclared, (
        "these jobs use `gh` and ask for a label the fallback pool has served, but are "
        f"neither covered nor declared as a gap: {undeclared}. Add the job to GH_JOBS and "
        "run `bash .github/scripts/runner-tools.sh ensure gh` as its first shell step, or "
        "add it to KNOWN_UNCOVERED with the reason (#9018)"
    )
    for wf, jobs in KNOWN_UNCOVERED.items():
        for job, why in jobs.items():
            assert why.strip(), f"{wf}::{job}: a declared gap must say WHY"
