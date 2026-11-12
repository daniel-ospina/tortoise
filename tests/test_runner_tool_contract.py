"""The runner tool contract (#9018) — the probe, and the coverage that keeps it from rotting.

Why a test rather than trust
----------------------------
`runs-on: ubuntu-latest` names a GitHub-HOSTED IMAGE, and `ubuntu-24.04` names a HOSTED
version pin. The fallback pool has served BOTH: `scripts/ci_runner_gate.sh` attaches
`ubuntu-latest` while the fallback is engaged, and 2026-10-24's measurement found every
failing `finding-provenance` `classify` job that ran on `pi-runner-vm-*` holding the
`ubuntu-24.04` pin as well — a live violation, AT THE TIME, of the invariant that no
self-hosted runner claims an `ubuntu-<version>` label (the drift arm in
`tests/test_ci_selection.py` reads the GATE'S TEXT rather than the live label set, which is
why it did not fire).

⚠️ **THAT INVARIANT IS NO LONGER IN FORCE, AND THIS MEASUREMENT IS THE DOCUMENTED STATE.** Since
the 2026-10-29 departure recorded on #6897 (`OVERRIDES:` in `docs/ops/ci-runner-x64.md` §*the label
contract*, and `MEMORY.md` `[ci-runners]`), the pool claiming a version-pinned hosted label is an
**explicit opt-in mitigation** (`CI_RUNNER_OS_LABEL=on|auto`, default `off`) for the deadlock where
the pinned legs are never dispatched and every PR stays BLOCKED; `--status`' `label-coverage` block
now reports and verdicts that state. So a runner carrying `ubuntu-24.04` is **not** a defect to
repair — the revert is `CI_RUNNER_OS_LABEL=off` plus `--repair-labels`, never an ad-hoc label strip,
which is how one would silently remove the mitigation. What this file still proves is the OTHER
claim, which the departure does not touch: a pool runner is a BARE box whose service `PATH` lacks
the hosted image's tools, so any job that can land on it must carry the tool contract.

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
#: Three are the measured 2026-10-24 reds (`canary-streak`, `finding-provenance`,
#: `availability-watchdog`) and the rest are the same hazard found by the scan at the
#: bottom of this file (a job that shells out to `gh` and asks for a label the pool has
#: served). Each reaches `gh` one of two ways, and which one is not obvious from the
#: entry, so: INLINE in the workflow's own `run:` blocks (`canary-streak`,
#: `finding-provenance`, `ci-timing`, `supabase-deploy`, `mergify-guard-recut`'s digest
#: step), or THROUGH a helper — `ci_check_repair.py` (`ci-check-repair`),
#: `ci_queue_health.py` (`ci-queue-health`), `ci_timing.py` (`ci-timing`),
#: `availability-watchdog.sh` / `availability-liveness.sh` / `availability-record.sh`,
#: `auto-file-issue.sh` (`e2e-live-reconcile`, `prod-csp-monitor`,
#: `tenant-provision-monitor`), `welcome-e2e-monitor.sh` (`welcome-e2e-monitor`),
#: `mergify_config_guard.py` (`mergify-guard-recut`), `inbound_relay_sweep.py`
#: (`inbound-relay-sweep`). The set is 18 jobs across 16 workflows; count it from
#: the literal, not from this sentence.
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
    # `tools/inbound_relay_sweep.py` reaches `gh` for EVERY read and EVERY
    # mutation (label, comment, close, relay) — the job has no other channel to
    # the repo. Found by the scan at the bottom of this file when the sweep was
    # added (#9943/#9946); the job does carry a checkout, so the step can run and
    # this is coverage, not a declared gap.
    "inbound-relay-sweep.yml": ["sweep"],
    # #10597: the warn-only `Closes-coherence` step runs
    # `tools/closes_landing.py`, which reads the PR body and the base branch's PR
    # through `gh` for EVERY measurement. The job has a checkout, so the contract
    # step can run — this is coverage, not a declared gap. Without `gh` present
    # the `Ensure gh` step reds the JOB (it fails closed and has no
    # `continue-on-error`), so the advisory step never gets to report UNMEASURED —
    # which is exactly why the contract step is required here.
    #
    # ⚠️ It is mounted on its OWN workflow, not on `drift-guard.yml` where #10597
    # first put it: that workflow is `disabled_manually`, so a step there never
    # executes and the instrument was inert while the PR was green (#10634 review
    # P1). A NEW workflow is `active` on creation. The pin that keeps a re-mount
    # off a disabled host is `test_an_advisory_mount_is_not_on_a_disabled_workflow`
    # below; `drift-guard.yml` therefore has no entry here any more — the step,
    # its `Ensure gh` and the `pull-requests: read` grant it needed all moved.
    "closes-coherence.yml": ["closes-coherence"],
}

#: Jobs that use `gh` and are NOT covered, with the reason each one cannot simply take
#: the step. Declared rather than omitted: an undeclared gap is indistinguishable from
#: coverage nobody checked, which is the defect class this file exists for. The scan at
#: the bottom of this file reports EVERY such job, so this map cannot quietly shrink.
KNOWN_UNCOVERED = {
    # The job has NO `actions/checkout`, so `.github/scripts/runner-tools.sh` is not in
    # the workspace and the step cannot run. It is a required context, so a 127 here
    # blocks every merge — closing it means adding a checkout to a gate #1224 documents
    # carefully, or inlining the provisioner, and that is its own reviewed change. NOTE
    # on the checkout: the job DOES grant `contents: read` (`ai-review-gate.yml:242-244`),
    # so a checkout would work — `persist-credentials: false` is hygiene (the same job
    # carries `pull-requests: read`), not the obstacle. The obstacle is that a gate's
    # step list is not a drive-by edit. Filed as #9942 — that number is the claim that
    # this is owned; an earlier round said "filed as its own task" with no issue behind it.
    "ai-review-gate.yml": {
        "ai-review-gate": "no checkout in the job — the contract script is not in the workspace (#9942)"
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
    #
    # These two are also why the POOL-ONLY arm above is load-bearing rather than
    # decorative: `repo-harnesses` pins `runs-on: [self-hosted, self-hosted-x64]`, so a
    # predicate that answered only for hosted labels would skip it (and every future
    # pool pin) before looking for a `gh` hazard at all — the silent-skip direction this
    # file's docstring calls unsafe.
    "ci.yml": {
        "ops-harnesses": "runs *.test.sh harnesses that stub `gh` on PATH",
        "repo-harnesses": "runs *.test.sh harnesses that stub `gh` on PATH",
    },
}

CONTRACT_STEP = "runner-tools.sh ensure gh"


#: Workflows GitHub reports as NOT `active`, with the date each state was MEASURED.
#: A workflow in this set never runs: no `pull_request`, no `workflow_dispatch`,
#: nothing. Two review cycles on #10634 measured 45 workflows with exactly two in
#: this state, both from the same window (`drift-guard.yml` updated
#: 2026-10-29T02:33:12Z; `finding-provenance` around it).
#:
#: ⚠️ THIS IS A RECORD, NOT A LIVE READ, and it can go stale in ONE direction that
#: matters: a workflow disabled AFTER this line was written will not red anything
#: until someone adds it here. That is the price of a pin a unit suite can run
#: (this repo's suite has no network and no token), and it is why the entry
#: carries the date it was measured. Re-measure with
#: `gh api "repos/<owner>/<repo>/actions/workflows?per_page=100" \
#:   --jq '.workflows[] | select(.state!="active") | "\(.path)\t\(.state)"'`
#: before trusting it — the same rule that retired a stale count in this repo's
#: instruction file (`agent-infra#2936`).
KNOWN_DISABLED_WORKFLOWS = {
    "drift-guard.yml": "disabled_manually, measured 2026-11-03 (updated_at 2026-10-29T02:33:12Z)",
    "finding-provenance.yml": "disabled_manually, measured 2026-11-03 (same window as drift-guard)",
}

#: Workflows whose job exists ONLY to ADVISE (warn, never gate). Their whole value
#: is that a reader SEES the warning, so a mount on a workflow that cannot run is
#: not a cosmetic defect — it is the instrument being absent (#10634 review P1).
#: `drift-guard.yml:80` was exactly that: a well-built warn-only step on a
#: `disabled_manually` workflow, green on every PR and never executed.
ADVISORY_MOUNTS = {
    "closes-coherence.yml": "closes-coherence",
}


def test_an_advisory_mount_is_not_on_a_disabled_workflow():
    """A warn-only step on a workflow GitHub will not run advises nobody.

    WHY THIS IS A PIN AND NOT A COMMENT. #10597's deliverable was one annotation
    on a stacked PR; it was mounted in `drift-guard.yml`, which had been
    `disabled_manually` for five days. Every other leg was green and correct —
    the contract step was declared, the registration was in `ci-surfaces.yml`, the
    suite passed — because NONE of them can see a workflow's live state. The
    defect survived the whole review history for that reason, so the declared
    disabled set above is the cheapest executable form that reds on a re-mount.
    """
    dead = sorted(set(ADVISORY_MOUNTS) & set(KNOWN_DISABLED_WORKFLOWS))
    assert not dead, (
        f"these workflows host a warn-only mount but are declared DISABLED, so the "
        f"warning can never reach a reader: {dead}. Mount the advisory job on a "
        f"workflow that runs (a new workflow is active on creation), or re-enable "
        f"the host as its own reviewed decision — do not leave the step there "
        f"(#10634 review P1)"
    )
    for workflow, job in ADVISORY_MOUNTS.items():
        path = WORKFLOWS / workflow
        assert path.exists(), f"advisory mount names a workflow that is not in the tree: {workflow}"
        text = path.read_text()
        # The TRIGGER, not the file. `workflow_dispatch`-only would pass a
        # file-exists check and still never fire for the PR the warning is for —
        # and a whole-file substring check cannot see that either, because
        # `pull_request` also occurs in the step's `if:`, in
        # `github.event.pull_request.number`, and in `github.event.pull_request.
        # head.sha`. Measured (#10634 review P2): deleting the `on: pull_request:`
        # entry while leaving the `if:` intact still matched the old substring
        # assertion four times, so the pin passed on a workflow with no PR
        # trigger at all. Parse the `on:` block instead.
        doc = yaml.safe_load(text) or {}
        # PyYAML parses the YAML 1.1 `on:` key as boolean True (same shape as
        # `tests/test_mergify_config_guard.py::test_recut_workflow_is_never_required`).
        triggers = doc.get("on") or doc.get(True) or {}
        if isinstance(triggers, str):
            triggers = {triggers}
        assert "pull_request" in set(triggers), (
            f"{workflow} is an ADVISORY mount but its `on:` trigger is "
            f"{sorted(triggers) if not isinstance(triggers, str) else [triggers]!r}, not "
            f"`pull_request`, so the advisory job cannot fire for a PR"
        )
        # …and the job must really be there, so a rename cannot leave this pin
        # asserting over a workflow with no advisory step in it at all.
        assert re.search(rf"^  {re.escape(job)}:\s*$", text, re.M), (
            f"{workflow}: no job `{job}` — the advisory mount moved or was renamed"
        )


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
TOOLBOX = (
    "bash",
    "gzip",
    "mkdir",
    "rm",
    "find",
    "head",
    "tar",
    "ln",
    "sha256sum",
    "cut",
    "uname",
    "curl",
)


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
#: `welcome-e2e-monitor.sh`, `auto-file-issue.sh`). Deriving the set means less can
#: be forgotten — but a DERIVATION IS ONLY AS GOOD AS ITS IDIOMS, so the claim is
#: scoped to them (stated in `_gh_executing_scripts`, and re-measured by
#: `test_the_gh_script_derivation_has_no_visible_blind_spot`):
#:   * a command substitution / subshell:      `$(gh api …)`, `( gh … )`
#:   * a shell operator or statement start:    `| gh …`, `; gh …`, `&& gh …`, `gh …`
#:   * an argv literal / env default:          `["gh", "api", …]`, `get("X_GH", "gh")`
#:   * a resolved binary variable:             `GH_BIN` (and `*_GH` siblings)
_GH_EXEC = (
    re.compile(r"\(\s*gh\s"),  # command substitution / subshell
    re.compile(r"[;&|(]\s*gh\s+(api|run|pr|issue|search|workflow|label|release|repo)"),
    re.compile(r"^\s*gh\s+(api|run|pr|issue|search|workflow|label|release|repo)", re.M),
    # ⛔ `[),\]]`, not `,`: the repo resolves the binary TWO ways — an argv literal
    # (`subprocess.run(["gh", "api", …])`) and an env default
    # (`os.environ.get("BRANCH_REAPER_GH", "gh")`). Requiring the comma saw only the
    # first, so `branch_reaper.py` / `run_reaper.py` were invisible despite really
    # executing `gh` (#9939 review).
    re.compile(r"""["']gh["']\s*[),\]]"""),
    re.compile(r"\bGH_BIN\b"),  # resolved, then exec'd
)
#: A harness is not what a workflow runs IN ANGER, and three of them legitimately
#: exercise `gh` (they install a STUB on PATH). They are excluded by NAME, not by
#: which glob they came from, so the exclusion cannot drift with the directory.
_NOT_A_HARNESS = re.compile(r"\.test\.|^test_")
#: Everywhere a script that shells out to `gh` can live. `graph-scripts/` was missing and
#: is a real home for one (`e2e_live_reconcile.py` runs in `e2e-live-reconcile`).
_SCRIPT_GLOBS = (
    ".github/scripts/*.sh",
    ".github/scripts/*.py",
    "tools/*.py",
    "tools/*.sh",
    "graph-scripts/*.py",
)
#: Labels the fallback pool has been observed to serve. Not `ubuntu-latest` only:
#: the 2026-10-24 measurement shows the pool serving `ubuntu-24.04` as well.
_POOLABLE_LABEL = re.compile(r"ubuntu-(latest|\d+\.\d+)")
#: The pool's OWN labels — a POOL-ONLY pin, in the runbook's vocabulary
#: (`docs/ops/ci-runner-x64.md:62`: "not a spelling but a CLASS — any POOL-ONLY label:
#: `self-hosted`, and equally the bare custom `self-hosted-x64`").
#:
#: WHY THIS ARM EXISTS. Until it, the predicate answered only for a hosted label, so a job
#: pinned to the pool BY NAME — the most direct route onto a bare box, and the shape a
#: `self-hosted` job actually takes — was invisible to the scan and skipped before its `gh`
#: hazards were ever looked for. `ci.yml`'s #10237 canary is the tree's FIRST literal such pin,
#: and deleting its provisioning steps left the whole suite green (#10511 review). The blind
#: spot was not inherited: it was being introduced.
#:
#: FOLDED CASE, deliberately: GitHub matches runner labels without regard to case, and the
#: three spellings the pool carries are lowercase (`self-hosted`, `self-hosted-x64`,
#: `self-hosted-arm64`). Folding can only admit MORE jobs into the scan, never fewer — the
#: safe direction, because a false positive asks for a declaration and a false negative is the
#: silent skip this whole file exists to prevent.
#:
#: BOUNDARY-MATCHED, not substring: `self-hosted-arm64` must match as one label, and
#: `self-hosted-x65` (the typo `workflow-lint` catches separately) must not be read as the
#: pool marker. A truncated read would admit the typo'd job as pool-eligible.
_POOL_ONLY_LABEL = re.compile(r"(?<![\w-])self-hosted(?:-(?:x64|arm64))?(?![\w-])", re.IGNORECASE)
#: A `runs-on` VALUE the workflow text cannot resolve to a label: GitHub supports
#: `runs-on: ${{ matrix.os }}` (its own matrix example), and the label is then
#: decided per leg at run time. `_poolable_label` fails CLOSED on it — see the
#: docstring — because "cannot be shown non-poolable" is not "non-poolable".
_EXPRESSION = "${{"


def _gh_executing_scripts() -> frozenset[str]:
    """Basenames of the helper scripts that INVOKE `gh` (derived, never listed).

    WHAT THIS CANNOT SEE, stated so nobody reads a green run as proof of absence:
    a script that builds the command in a way none of `_GH_EXEC`'s idioms describe
    (a `subprocess.run(shlex.split($CMD))`, a shell `$GH` variable, a wrapper that
    takes the binary name as an argument). The direction of that miss is the RARE
    one — every script in this repo that execs `gh` does it through one of the four
    idioms, which `test_the_gh_script_derivation_has_no_visible_blind_spot`
    re-measures against the tree instead of trusting this sentence.
    """
    out = set()
    for pattern in _SCRIPT_GLOBS:
        for path in REPO.glob(pattern):
            if _NOT_A_HARNESS.search(path.name):
                continue
            code = "\n".join(
                line
                for line in path.read_text(errors="ignore").splitlines()
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

    ⛔ ONLY the `gh` arm uses THIS function. The SCRIPT-NAME arm reads the RAW line,
    because a script path is legitimately quoted
    (`bash "$GITHUB_WORKSPACE/tools/x.sh"`) — stripping every quoted span there
    loses a real invocation; what that arm exempts instead is a PRINTED MESSAGE,
    via `_message_only` below (#12133).
    """
    return re.sub(r"'[^']*'|\"[^\"]*\"", "''", line)


#: A shell line that ONLY PRINTS a message: its text is prose, not a call site.
#:
#: WHY THE SCAN NEEDS THIS (#12133). `python-ci.yml`'s `test-record-postgres` runs no
#: `gh` anywhere, but its fail-closed `::error::` text NAMES
#: `tools/install-ci-runner-x64.sh` to tell an operator how to repair a runner's
#: labels — so the script-name arm read the instruction as an invocation, demanded a
#: declaration the job does not owe, and redded the REQUIRED `test-carve-out` shard on
#: every PR in the repo. Both remedies the assertion offers are WRONG here: `ensure gh`
#: FAILS CLOSED on a no-egress runner, and a `KNOWN_UNCOVERED` entry would declare a
#: `gh` user that does not exist.
#:
#: ⛔ FOUR CONDITIONS, so this cannot become a filter — the command word is
#: `echo`/`printf` (also after a YAML `run:` prefix or a `then`/`else`), the line has NO
#: shell operator once its quoted spans are gone, the line carries NO command
#: substitution or backtick (a substitution EXECUTES: `echo "$(tools/x.sh)"` runs the
#: script and only prints its output), and the script name is exempted only inside such
#: a line. A bare `.*echo` would exempt `python3 tools/x.py && echo done` — a real
#: invocation hidden behind its own log line, the FALSE NEGATIVE the script arm exists
#: to catch.
#:
#: RESIDUAL, stated rather than discovered later: a message printed by another command
#: (`cat <<'EOF'` heredocs, `python3 -c 'print(…)'`) is still read as code, so is a
#: printed line that carries a substitution (`echo "$(date) — run tools/x.sh"`), and so
#: is a message carrying a `${{ … }}` expression (it contains `${`). All three fail in
#: the SAFE direction — they ask for a declaration the job may not need, never a silent
#: skip.
_MESSAGE_CMD = re.compile(r"^(?:-(?:\s+)?)?(?:run:\s*)?(?:(?:then|else)\s+)?(?:echo|printf)\b")
_SHELL_OPERATOR = re.compile(r"[;&|]")
_SUBSTITUTION = re.compile(r"\$[({]|[<>]\(|`")


def _message_only(line: str) -> bool:
    """Is this line a PRINTED message rather than a shell call site? (#12133)

    It exempts a script name only when the line does nothing but print: command word
    `echo`/`printf`, no shell operator, and **no command substitution or backtick**.
    That last condition is not decoration — `echo "$(tools/x.sh)"` PRINTS, and it also
    EXECUTES the script, so exempting it would be a silent skip. The direction chosen
    is the safe one: such a line asks for a declaration it may not need.

    The script name is matched on the RAW line, so a quoted invocation stays a hazard —
    `OUT="$(python3 graph-scripts/e2e_live_reconcile.py --json)"`
    (e2e-live-reconcile.yml:77) and
    `head_digest="$(python3 tools/mergify_config_guard.py --print-digest)"`
    (mergify-guard-recut.yml:105) are both seen. A whitespace-based strip cannot do
    this: it hid both, one leading space defeated it, and a printed message that
    contained a substitution was read as a call site (#12092 review).

    MEASURED over the 41 `*.yml` workflows: against the pre-#12133 raw scan, exactly
    **4** line verdicts change and **all four are printed `echo` messages** — zero real
    invocations lost, zero new hazards. That single-baseline count is the one this
    predicate can be held to; a count that mixes two baselines is not a measurement.
    """
    code = _code_only(line).strip()
    return (
        bool(_MESSAGE_CMD.match(code))
        and not _SHELL_OPERATOR.search(code)
        and not _SUBSTITUTION.search(line)
    )


def _looks_poolable(value: str) -> bool:
    """One `runs-on` VALUE (inline scalar or one list item): poolable, or unresolved?

    An EXPRESSION value routes to `True` deliberately: a matrix/needs expression is
    resolved per leg at run time, so the text cannot show the label to be
    non-poolable. Admitting it is the whole point when the job uses `gh`; the cost of
    admitting it otherwise is a FALSE POSITIVE, stated rather than waved away — an
    expression whose every leg is non-poolable (`os: [macos-latest, windows-latest]`)
    is admitted too, and if that job also uses `gh` the pin's message asks for a
    declaration rather than a silent skip. That polarity is per LEG, not per shape:
    GitHub's own examples mix classes (`os: [macos-latest, ubuntu-latest]`), and there
    admitting the job is a TRUE positive, because the `ubuntu-latest` leg really can
    land on the pool — do not narrow this arm by analogy with the macOS-only case.
    Cheap in the right direction: a demanded `KNOWN_UNCOVERED` reason, never an
    invisible job —
    and note the remedy that message names (`ensure gh`) is Linux-only, so on a macOS
    leg the declaration is the correct answer rather than the install step.
    """
    if _EXPRESSION in value:
        return True
    return bool(_POOLABLE_LABEL.search(value) or _POOL_ONLY_LABEL.search(value))


def _poolable_label(text: str) -> bool:
    """Does this job's `runs-on` ask for a label the fallback pool has served?

    THREE YAML shapes: the inline scalar (`runs-on: ubuntu-latest`), the block list
    (`runs-on:` then `- self-hosted` / `- ubuntu-latest`), and an EXPRESSION
    (`runs-on: ${{ fromJSON(matrix.runner) }}`). The first version read only the
    INLINE form, so a job written as a list — the shape a `self-hosted` job usually
    takes — was invisible and could have joined the gap with the suite green (#9939
    review).

    A COMMENT is not a value: `runs-on:  # see #6897` then `- ubuntu-latest` is the
    block-list shape, and a comment line among the items or above them must not end
    the list. Both of those read as "no poolable label" before this arm, which is the
    same silent skip in a comment-dense workflow — latent rather than live here (every
    `runs-on:` in the tree is inline today), and exactly the shape this repo's style
    invites next.

    The expression arm exists for the same reason, one step further out: #10186
    turned `test`'s literal `ubuntu-latest` into `${{ fromJSON(matrix.runner) }}`,
    and a text reader that only matches `ubuntu-…` then answered "not poolable" for
    the job that routes its odd legs to an explicit `self-hosted` pin. The skip is
    the UNSAFE direction — the job did not stop being pool-eligible, it stopped
    being visible — and nothing reds when it happens, which is why the answer for an
    unresolvable value must be "assume it can reach the pool".

    This is the same rule the installer already applies from the other side:
    `tools/install-ci-runner-x64.sh:377-378` skips `${{ … }}` — its comment reads
    "a matrix expression (`${{ … }}`) is not a label; it is resolved per job at run
    time" — but that reader resolves labels against the ONLINE runner list instead.
    This scan has no such second source, so it cannot skip: it must fail closed.

    RESIDUAL — shapes this reader still does not see, all returning False (the same
    silent skip, one shape further out): a YAML ALIAS (`runs-on: *pool_labels`), a
    BLOCK SCALAR (`runs-on: >-` or `runs-on: |` with the label on the next line), a
    next-line plain scalar or FLOW list (`runs-on:` then `  ubuntu-latest` /
    `  [self-hosted, ubuntu-latest]`), a next-line nested MAP — the `group:` / `labels:`
    pair GitHub documents as a `runs-on` value, written as `runs-on:` then
    `  group: ubuntu-runners` / `  labels: ubuntu-24.04-16core` — and a `<<:` merge-key
    line. The nested MAP is worth its own clause because it is ASYMMETRIC: the INLINE
    flow spelling (`runs-on: {group: …, labels: ubuntu-24.04}`) IS answered, since the
    inline arm text-searches the whole rest of the line, while the BLOCK one stops at
    `group:` — no `- ` item matches, the item loop breaks, and the job comes back
    invisible although `ubuntu-24.04` is named one line below in `labels:`. None is
    used in the tree today — all 86 `runs-on:` keys are single-line values (one of
    them, `ci.yml:1534`, an inline FLOW list the inline arm answers), so the miss is
    latent, not live — and they are named here so the next author writing one of them
    knows the scan is a text reader rather than a YAML parser, and adds the shape
    instead of assuming coverage.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^\s+runs-on:\s*(.*)$", line)
        if not m:
            continue
        inline = m.group(1).strip()
        if inline and not inline.startswith("#"):
            return _looks_poolable(inline)
        for follow in lines[i + 1 :]:
            stripped = follow.strip()
            if not stripped or stripped.startswith("#"):
                continue
            item = re.match(r"^\s*-\s*(.+)$", follow)
            if not item:
                break  # the next key: the list ended with no poolable label
            if _looks_poolable(item.group(1)):
                return True
        return False
    return False


def _gh_hazards(body: list[str], scripts: frozenset[str]) -> list[str]:
    hits = []
    # ⛔ BOUNDARY-MATCHED, not `name in code`. A substring test made
    # `python3 -m pytest tests/test_ci_timing.py` look like a `gh` user (the helper
    # name `ci_timing.py` sits inside the test module's name), and the remedy it
    # demands — `ensure gh` — FAILS CLOSED, so on a no-egress pool runner it would
    # red a healthy job. 15 such test modules exist (#9939 review).
    script_ref = (
        re.compile(
            r"(?<![\w.-])(?:" + "|".join(re.escape(s) for s in sorted(scripts)) + r")(?![\w.-])"
        )
        if scripts
        else None
    )
    for line in body:
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("- name:"):
            continue
        code = _code_only(line)
        if _GH_CMD.search(code):
            hits.append(stripped)
            continue
        # ⛔ A MESSAGE IS NOT A CALL SITE (#12133). Read on the RAW line, not `code`,
        # because `_code_only` strips quoted spans and the repo really does invoke
        # helpers inside one — `head_digest="$(python3 tools/mergify_config_guard.py …)"`
        # would become invisible if this arm read `code`. Only a printed message is
        # exempt, and only for the script-name arm: `_GH_CMD` above is already
        # quote-stripped and intentionally stays so.
        if script_ref is not None and not _message_only(line) and script_ref.search(line):
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
            if not _poolable_label(text):
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


# ── the derivation's own blind spots and false positives ─────────────────────

#: Scripts that DO exec `gh` through a shape the first derivation missed: an
#: env-defaulted binary name (`os.environ.get("BRANCH_REAPER_GH", "gh")`), and a
#: directory the glob did not reach (`graph-scripts/`). Pinned by NAME so a pattern
#: edit that loses them fails here instead of silently shrinking the scan.
_ENV_DEFAULTED_GH = ("branch_reaper.py", "run_reaper.py", "e2e_live_reconcile.py")


def test_the_gh_script_derivation_has_no_visible_blind_spot():
    """Re-measure the idiom claim against the tree — do not trust its docstring."""
    scripts = _gh_executing_scripts()
    missing = [name for name in _ENV_DEFAULTED_GH if name not in scripts]
    assert not missing, (
        f"the derivation no longer sees {missing}, which really do exec `gh` — a new "
        "poolable job running one would join the #9018 gap with the suite green (#9939 "
        "review)"
    )
    # And the three the earlier round measured through, so neither fix undid the other.
    for name in (
        "auto-file-issue.sh",
        "availability-record.sh",
        "welcome-e2e-monitor.sh",
        "ci_check_repair.py",
        "ci_queue_health.py",
        "ci_timing.py",
    ):
        assert name in scripts, f"the derivation lost {name}"


def test_a_test_module_named_like_a_helper_is_not_a_gh_hazard():
    """The false positive that matters: `ensure gh` FAILS CLOSED.

    `any(name in code)` matched `ci_timing.py` inside `tests/test_ci_timing.py`, so a
    job naming that test module was told to add a contract step — and on a no-egress
    pool runner that step exits 1 and reds a healthy job. Boundary matching, not
    substring: the helper name must not be part of a longer path component.
    """
    scripts = _gh_executing_scripts()
    assert _gh_hazards(["        run: python3 -m pytest tests/test_ci_timing.py -q"], scripts) == []
    assert _gh_hazards(["        run: bash .github/scripts/ci_timing.test.sh"], scripts) == []
    # …while a REAL invocation of the helper is still a hazard, in both shapes.
    assert _gh_hazards(["        run: python3 tools/ci_timing.py --refresh-durations"], scripts)
    assert _gh_hazards(['        run: python3 "$GITHUB_WORKSPACE/tools/ci_timing.py"'], scripts)


def test_a_remediation_message_is_not_read_as_a_gh_call_site():
    """A MESSAGE naming a `gh`-executing script is not the job running it — and a
    message is the ONLY thing exempted (#12133, pinned at the shape #12092's review
    measured).

    The false positive went live: `python-ci.yml`'s `test-record-postgres` runs no `gh`
    anywhere, but its fail-closed `::error::` text names
    `tools/install-ci-runner-x64.sh` to tell an operator how to repair a runner's
    labels, so the script-name arm read the instruction as an invocation and reported
    the job as a `gh` user. The REQUIRED `test-carve-out` shard then redded on every
    open PR — measured on main's `4b1d6cd0d4`. The remedies the assertion offers are
    both wrong for that job: `ensure gh` FAILS CLOSED on a no-egress runner, and a
    `KNOWN_UNCOVERED` entry would declare a `gh` user that does not exist.

    ⛔ THE EXEMPTION IS `_message_only`, NOT "quoted spans with whitespace": a quoted
    span is how a real invocation is commonly written, and the substitution forms below
    are the ones a whitespace test lost (two are in this repo today).

    ⛔ CONVERGENCE WITH #12102, which repairs the same #12133 red: that PR carries its OWN
    copy of this test, and ITS PREDICATE IS **NOT** THE SAME — it has three conditions,
    this one has four (`_SUBSTITUTION`, added here because a printed substitution
    EXECUTES; see the loop below). So keep **this** predicate and **this** test copy.

    The two copies overlap but neither contains the other, so the list is WHICH SHAPES,
    not a count (a count "ten additional" double-counted the one mergify substitution
    both files pin): this copy additionally pins the `e2e-live-reconcile` substitution,
    both process substitutions (`<(…)`/`>(…)`), the two `echo`-printed substitutions,
    the leading-space and mid-span variants, the quoted argv word, the three
    message-FIRST operator forms, and the backtick and `printf` forms. #12102's copy
    additionally pins an `if`-prefixed compound whose `then` clause holds the
    invocation, and the returned strings' positions (`hazards[2].startswith("if")`,
    `len(hazards) == 4`).

    The second lander drops the other copy. A duplicate same-named `def` is NOT a git
    conflict and pytest does not complain (it binds the later definition), but
    `uv run ruff check .` reds it with F811 under the pinned ruff 0.16.4 — and that is
    run by the `lint` job and by `main-lint`, not listed in `REQUIRED_SET`, so it reds
    the tree rather than gating the merge. Either way: never keep both.
    """
    scripts = _gh_executing_scripts()

    # 1. The message itself — the LIVE shape only. A message that carries a
    #    substitution is read as CODE (asserted in the loop below), because printing a
    #    substitution executes it.
    assert _gh_hazards(
        [
            '            echo "::error::no client in this archive — on this box run '
            'CI_RUNNER_OS_LABEL=off tools/install-ci-runner-x64.sh --repair-labels --count 4"'
        ],
        scripts,
    ) == [], "an operator-facing MESSAGE that names a `gh`-executing script was read as an invocation"
    # …but a line that PRINTS a substitution is not "only printing": `echo "$(x)"`
    # EXECUTES x and prints its output, so exempting it would be a silent skip. The
    # exemption therefore requires the absence of ANY substitution or backtick, and a
    # message that carries one is read as code — the SAFE direction (it asks for a
    # declaration the job may not need) rather than the silent one.
    for line, why in [
        ('        run: echo "$(date) — on failure run tools/install-ci-runner-x64.sh --repair-labels"',
         "a printed line carrying a substitution is read as code (safe direction)"),
        ('        run: echo "$(tools/install-ci-runner-x64.sh --repair-labels)"',
         "echoing a substitution RUNS it — exempting that is a silent skip"),
        ("        run: printf '%s' \"$(tools/install-ci-runner-x64.sh)\"",
         "the same shape via printf"),
        ('        run: echo "`tools/install-ci-runner-x64.sh`"',
         "the backtick form runs it too"),
        ("        run: echo done && tools/install-ci-runner-x64.sh --repair-labels",
         "an invocation AFTER an operator is a call site, not a message"),
        ("        run: echo <(tools/install-ci-runner-x64.sh --repair-labels)",
         "PROCESS substitution also EXECUTES the script — the fourth condition catches it"),
        ("        run: echo >(tools/install-ci-runner-x64.sh --repair-labels)",
         "and so does the output form"),
    ]:
        assert _gh_hazards([line], scripts), why
    # …while the `then`/`else` prefix is still a message: a `run: |` body line that only
    # prints after a branch is prose. Deleting the prefix from `_MESSAGE_CMD` reds this.
    assert _gh_hazards(
        ['        then echo "::error::run tools/install-ci-runner-x64.sh --repair-labels"'],
        scripts,
    ) == [], "a `then`-prefixed printed message is still a message"

    # 2. Real invocations, including every shape a whitespace strip hid: the two in
    #    this repo, a leading space inside the quotes, and a mid-span substitution.
    for line, why in [
        ('        run: OUT="$(python3 graph-scripts/e2e_live_reconcile.py --json 2>/tmp/err)"',
         "e2e-live-reconcile.yml:77 — a quoted substitution RUNS the script"),
        ('        run: head_digest="$(python3 tools/mergify_config_guard.py --print-digest)"',
         "mergify-guard-recut.yml:105 — same shape, a gh arm"),
        ('        run: OUT=" $(python3 tools/install-ci-runner-x64.sh --repair-labels)"',
         "one leading space must not hide an invocation"),
        ('        run: OUT="v: $(python3 tools/install-ci-runner-x64.sh --repair-labels)"',
         "a substitution that does not start the span must not be hidden"),
        ('        run: bash "$GITHUB_WORKSPACE/tools/install-ci-runner-x64.sh"',
         "a quoted argv WORD is an invocation"),
        ("        run: bash tools/install-ci-runner-x64.sh --repair-labels",
         "an unquoted invocation is an invocation"),
        ("        run: python3 tools/mergify_config_guard.py --emit && echo done",
         "a log line AFTER a real invocation must not exempt it"),
        ("        run: echo done; tools/install-ci-runner-x64.sh --repair-labels",
         "a message FIRST does not exempt the invocation that follows a `;`"),
        ('        run: echo "a" && tools/install-ci-runner-x64.sh --repair-labels',
         "a message FIRST does not exempt the invocation that follows an `&&`"),
        ("        run: echo x | bash tools/install-ci-runner-x64.sh --repair-labels",
         "a message piped INTO an invocation is an invocation"),
    ]:
        assert _gh_hazards([line], scripts), why


def test_the_poolable_label_reader_sees_every_yaml_shape():
    """`runs-on` as a block list is the shape a self-hosted job usually takes.

    The THIRD shape is an expression, and it is asserted here rather than left to the
    scan below so the reader's contract is complete in one place: a value the text
    cannot resolve is treated as possibly-poolable, not as a non-match. The two
    COMMENT-bearing shapes are asserted for the same reason — a comment is not a
    value, and reading it as one ends the block list before the labels are reached.
    """
    inline = "    runs-on: ubuntu-latest\n    steps:\n"
    block = "    runs-on:\n      - self-hosted\n      - ubuntu-24.04\n    steps:\n"
    block_pool_only = "    runs-on:\n      - self-hosted\n      - linux\n    steps:\n"
    expression = "    runs-on: ${{ fromJSON(matrix.runner) }}\n    steps:\n"
    expression_in_list = (
        "    runs-on:\n      - self-hosted\n      - ${{ matrix.arch }}\n    steps:\n"
    )
    block_after_inline_comment = (
        "    runs-on:  # odd shards go to the pool\n      - self-hosted\n      - ubuntu-latest\n"
        "    steps:\n"
    )
    block_after_item_comment = (
        "    runs-on:\n      # see #6897 for the residual\n      - self-hosted\n"
        "      - ubuntu-latest\n    steps:\n"
    )
    assert _poolable_label(inline)
    assert _poolable_label(block)
    # …and a block list that names ONLY the pool's own markers is pool-eligible, which the
    # pre-fix reader answered False for — the #9018 scan's blind spot, pinned here so the
    # next edit cannot restore it. `[self-hosted, linux]` is a POOL-ONLY pin in the
    # runbook's sense (`docs/ops/ci-runner-x64.md:62`): no GitHub-hosted runner carries
    # `self-hosted`, so every leg is a bare box.
    assert _poolable_label(block_pool_only)
    # An expression is UNRESOLVABLE from the text, so it must not be skipped: on the
    # pre-fix reader both of these answered False (#10186 review).
    assert _poolable_label(expression)
    assert _poolable_label(expression_in_list)
    # A comment-bearing key and a comment above the items answered False before the
    # comment arm — the same silent skip, one shape further out.
    assert _poolable_label(block_after_inline_comment)
    assert _poolable_label(block_after_item_comment)
    # …and the pool arm cannot be satisfied by simply always answering True: a value naming
    # neither class stays out.
    assert not _poolable_label(
        "    runs-on:  # hosted class\n      - linux\n      - macOS\n    steps:\n"
    )


def test_an_expression_valued_runs_on_keeps_its_job_in_the_scan():
    """#10186 turned `test`'s `runs-on` into an expression; the scan must still see it.

    Why this is the shape that matters: under the `CI_SHARD_ROUTING=pool-split` opt-in
    `test` routes its odd legs to an explicit `["self-hosted", "self-hosted-x64"]`
    pin, i.e. the one job whose legs can reach a minimal-`PATH` pool runner BY NAME —
    and it is the largest job in the tree (25 steps). Dropping it out of this scan is
    silent: the scan can only red on jobs it looks at, so a `gh`-using step added to
    `test` later would join the #9018 gap with the suite green. It is `gh`-free today,
    which is why nothing reds now and why the arm has to be a pin instead of a fix to
    a live failure.
    """
    seen = {
        (path.name, job)
        for path in sorted(WORKFLOWS.glob("*.yml"))
        for job, body in _jobs(path)
        if _poolable_label("\n".join(body))
    }
    assert ("python-ci.yml", "test") in seen, (
        "`python-ci.yml::test` is no longer seen by the pool-eligibility scan. Its "
        "`runs-on` is `${{ fromJSON(matrix.runner) }}`, an expression the workflow "
        "text cannot resolve to a label — the scan must fail CLOSED on it (assume the "
        "job can reach the pool), because a silent skip removes the job from "
        "`test_every_pool_eligible_gh_job_is_declared`'s surface entirely (#9018)"
    )
