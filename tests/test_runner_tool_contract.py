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
    # its own task — #9942 — rather than smuggled into this one.
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
    """
    lines = path.read_text().splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(rf"^  {re.escape(job)}:\s*$", line):
            start = i
            break
    assert start is not None, f"{path.name}: no job `{job}`"
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if re.match(r"^  [A-Za-z0-9_.-]+:\s*$", lines[j]):
            end = j
            break
    return "\n".join(lines[start:end])


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
    hazard = re.compile(
        r"(?<![\w-])gh\s+(api|run|pr|issue|search|workflow|label|release)"
        r"|ci_check_repair\.py|ci_queue_health\.py|ci_timing\.py"
        r"|availability-watchdog\.sh|availability-liveness\.sh"
    )
    contract_at = next(i for i, line in enumerate(lines) if CONTRACT_STEP in line)
    for i, line in enumerate(lines):
        if i >= contract_at:
            break
        stripped = line.strip()
        # Comments are prose ABOUT the hazard, not the hazard. (`- name:` lines
        # are prose too; a step name cannot execute anything.)
        if stripped.startswith("#") or stripped.startswith("- name:"):
            continue
        code = _code_only(line)
        assert not hazard.search(code), (
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
#: `gh` at an EXECUTION site, used to DERIVE which helper scripts carry the hazard.
#: A text list of script names was the first attempt and it under-counted: five
#: poolable jobs run scripts it did not name (`availability-record.sh`,
#: `welcome-e2e-monitor.sh`, `auto-file-issue.sh`). Deriving the set means a new
#: script cannot be forgotten — it is found, or it is not executing `gh`.
_GH_EXEC = (
    re.compile(r"\(\s*gh\s"),                                  # command substitution / subshell
    re.compile(r"[;&|(]\s*gh\s+(api|run|pr|issue|search|workflow|label|release|repo)"),
    re.compile(r"^\s*gh\s+(api|run|pr|issue|search|workflow|label|release|repo)", re.M),
    re.compile(r'''["']gh["']\s*,'''),                        # subprocess argv literal
    re.compile(r"\bGH_BIN\b"),                                   # resolved, then exec'd
)
#: Labels the fallback pool has been observed to serve. Not `ubuntu-latest` only:
#: the 2026-10-24 measurement shows the pool serving `ubuntu-24.04` as well.
_POOLABLE_LABEL = re.compile(r"ubuntu-(latest|\d+\.\d+)")


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
    """(job name, its lines) for every job in a workflow file."""
    lines = path.read_text().splitlines()
    starts = [
        (i, m.group(1))
        for i, line in enumerate(lines)
        if (m := re.match(r"^  ([A-Za-z0-9_.-]+):\s*$", line))
    ]
    out = []
    for n, (start, name) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else len(lines)
        out.append((name, lines[start:end]))
    return out


def _code_only(line: str) -> str:
    """Drop single- and double-quoted spans before looking for `gh`.

    WHY: the first version of this scan red on a legitimate step whose only `gh`
    was INSIDE an operator-facing message —
    `echo "::error::… run FROM main: gh workflow run supabase-deploy.yml"`.
    A lint that reds on prose is a lint that earns a `|| true`, so quoting is
    removed first. Cost, stated: a real `gh` call hidden inside a quoted string
    (`bash -c "gh api …"`) is not seen. That is the safe direction here — the
    hazard this pin guards (a bare `gh` step that dies 127) is written unquoted —
    and the pin is a backstop, not the only defence: the contract step itself is
    what makes the job work.
    """
    return re.sub(r"'[^']*'|\"[^\"]*\"", "''", line)


def _gh_hazards(body: list[str], scripts: frozenset[str]) -> list[str]:
    hits = []
    for line in body:
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("- name:"):
            continue
        code = _code_only(line)
        if _GH_CMD.search(code) or any(name in code for name in scripts):
            hits.append(stripped)
    return hits


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
            if not label or not _POOLABLE_LABEL.search(label.group(1)):
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
