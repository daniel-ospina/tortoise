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
    # TWO hazard sources in this job at least, so the reason names both. Measured, the
    # job body hazards on `python3 tools/mergify_config_guard.py --static` AND
    # `python3 .github/scripts/check-required-set.py`: `mergify_config_guard.py` executes
    # `gh` only in `--live` mode (whose docstring records that CI must never use it — a
    # fail-closed admin read inside CI would deadlock every PR), and
    # `check-required-set.py` reaches `gh api …/branches/main/protection` only through
    # `read_live_protection()`, whose only caller is behind `--live`. The job runs
    # `--static` / no `--live`, so neither reaches `gh`.
    "python-ci.yml": {
        "manifest-integrity": (
            "mergify_config_guard.py and check-required-set.py touch gh only under --live, "
            "which CI never runs"
        )
    },
    # These jobs run `*.test.sh` harnesses, which install a STUB `gh` on PATH
    # (`.github/scripts/ai-review-gate.test.sh:416` writes `$T/bin/gh`). A harness that
    # stubs its tool is not a hazard; giving it the real one would mask the stub.
    #
    # These two are also why the POOL-ONLY arm above is load-bearing rather than
    # decorative — as a UNIT property, not for these two jobs. Both are pool-only pins
    # (`repo-harnesses` is `runs-on: [self-hosted, self-hosted-x64]`), so a predicate that
    # answered only for hosted labels would skip them (and every future pool pin) before
    # looking for a `gh` hazard at all — the silent-skip direction this file's docstring
    # calls unsafe. ⛔ WHAT IS NOT TRUE: neither job produces a hazard TODAY (measured,
    # both `_gh_hazards` -> []), so deleting the arm moves no live verdict here; the arm
    # is kept because the shape is what a future pool-only `gh` user will take, and the
    # pin that reds when it is deleted is
    # `test_the_poolable_label_reader_sees_every_yaml_shape`, not this list.
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


#: The CONTRACT STEP as a step's own `run:`, bound strictly so a line that merely PRINTS the
#: remedy cannot stand in for it. `test_the_contract_step_reader_cannot_be_defused_by_prose`
#: pins that, because the loose form (`CONTRACT_STEP in line`) silently disabled the whole
#: ordering check for any job whose advice step printed the remedy first.
#:
#: ⛔ `.*<CONTRACT_STEP>` IS NOT STRICT ENOUGH EITHER, and that was measured rather than
#: assumed: `run: echo "if this 404s, run runner-tools.sh ensure gh by hand"` satisfies it, so
#: a decoy advice step still became `contract_at` and the ordering check still switched
#: itself off. The step must be in COMMAND POSITION — the `run:` has to START with the
#: script path (optionally behind `bash`) — which is also how all 20 live occurrences are
#: written, so binding it this way moves no verdict.
_CONTRACT_RUN_RE = re.compile(
    r"^        run: (?:bash )?\.github/scripts/" + re.escape(CONTRACT_STEP)
)

#: A step BEGINS at a sequence item under `steps:` — six spaces and a `-`. The dash may be
#: BARE (the step's first key then sits on the following line), which is why this admits
#: end-of-line as well as whitespace after it: `re.match(r"^      - ")` did not, so a bare
#: marker fell through, the backward search ran to line 0, and the JOB's own `if:` was then
#: attributed to the contract step — a false violation demanding a remedy that cannot apply.
_STEP_MARKER = re.compile(r"^      -(?:\s|$)")

#: Step keys that make a step able to never run (`if:`) or to pass while failing
#: (`continue-on-error:`). Either one defuses the contract: the job then reaches `gh` with
#: no provisioned tool and dies 127 (#9018) while every textual check still passes.
_STEP_DEFUSERS = ("if", "continue-on-error")


def _step_bounds(lines: list[str], at: int) -> tuple[int, int]:
    """The range of `lines` holding the step that owns `lines[at]`.

    ⛔ THE BOUND IS THE WHOLE STEP. A guard written AFTER the `run:` is invisible to a scan
    that stops at it, and YAML puts no constraint on key order — GitHub's own examples write
    `continue-on-error:` after the command. See `_contract_violations`.
    """
    start = 0
    for i in range(at - 1, -1, -1):
        if _STEP_MARKER.match(lines[i]):
            start = i
            break
    end = len(lines)
    for i in range(at + 1, len(lines)):
        if _STEP_MARKER.match(lines[i]):
            end = i
            break
    return start, end


def _step_defusers(lines: list[str], at: int) -> list[str]:
    """The defusing keys the step owning `lines[at]` carries (`[]` when none).

    ONE implementation, because the coverage arm and the ordering arm must not disagree: a
    predicate that saw a guard where the other did not would SUPPRESS the very finding the
    ordering arm raises — `_undeclared_gh_jobs` reads this function too, through
    `_live_contract_step`. A commented-out key is not a key.
    """
    start, end = _step_bounds(lines, at)
    out = []
    for line in lines[start:end]:
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        key = stripped.split(":")[0]
        if key in _STEP_DEFUSERS:
            out.append(key)
    return out


def _live_contract_step(body: list[str]) -> bool:
    """Is some step the contract step itself, in command position, and UNGUARDED?

    The coverage arm's half of the ordering arm's question. A job whose only contract step
    is guarded is NOT covered: the step is present textually and can never provision
    anything, which is exactly the silent fail-open `#9018` is about.
    """
    for at, line in enumerate(body):
        if _CONTRACT_RUN_RE.match(line) and not _step_defusers(body, at):
            return True
    return False


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


def _contract_violations(block: str, scripts: frozenset[str]) -> list[str]:
    """Why `block` breaks the ordering contract — `[]` when it complies.

    ⛔ EXTRACTED SO IT CAN BE PINNED, and that is not tidiness. While this logic lived
    inline in the parametrised test, a FAITHFUL revert of its pre-contract arm to
    `origin/main`'s hardcoded text list left all 42 tests green (measured with a mutant
    built from the artifact): the sibling test pinned `_gh_hazards`' COMPONENTS, never the
    job-level arm, so the arm was correct but unexercised and a rebase could reinstate the
    defect silently. `test_the_precontract_arm_is_load_bearing` pins it on a synthetic
    block instead.

    Checks, in order: the step is present; it is bound as a step's own `run:` in COMMAND
    POSITION (see `_CONTRACT_RUN_RE`); nothing before it uses `gh`; and — new — the step is
    not guarded, because `ensure gh` is Linux-only and `if: runner.os == 'Linux'` is the
    natural spelling a future author would reach for. A guarded step satisfies every
    textual check while being able to never run, which is this file's definition of a
    silent fail-open.

    ⛔ THE GUARD IS READ OVER THE WHOLE STEP (`_step_defusers`), not over the lines before
    the `run:`. YAML puts no constraint on key order and GitHub's own examples write
    `continue-on-error:` after the command, so the earlier `[step_start, at)` scan answered
    `[]` for a step carrying BOTH defusers after its `run:` (measured on this head, #12167
    review). The same scan also attributed a JOB-LEVEL `if:` to the step whenever the step's
    marker was a bare `-`, because `re.match(r"^      - ")` needs a space after the dash.
    """
    if CONTRACT_STEP not in block:
        return [
            f"does not run `{CONTRACT_STEP}` — a pool runner without it fails with "
            "exit 127, which reads as a test failure (#9018)"
        ]
    lines = block.splitlines()
    at = next((i for i, line in enumerate(lines) if _CONTRACT_RUN_RE.match(line)), None)
    if at is None:
        return [
            f"has no `run:` in command position that is `{CONTRACT_STEP}` — the step may "
            "be commented out, printed rather than executed, or spelled in a form this "
            "reader does not model (a `run: |` block, `./`, an `env` prefix)"
        ]
    out = []
    early = _gh_hazards(lines[:at], scripts)
    if early:
        out.append(
            f"a step BEFORE the contract step uses `gh` — directly or through one of "
            f"the derived helpers — so it can still die with 127: {early}. "
            f"`{CONTRACT_STEP}` must come first (#9018)"
        )
    for key in _step_defusers(lines, at):
        out.append(
            f"the contract step carries a `{key}:`, so it may never run and the "
            "job can reach `gh` with no runner-tool contract at all (#9018)"
        )
    return out


def test_the_precontract_arm_is_load_bearing():
    """The pre-contract arm must FLAG a job, not merely be correct in isolation.

    ⛔ MEASURED GAP THIS CLOSES. With the arm reverted to `origin/main`'s hardcoded text
    list — a faithful revert, not a strawman — all 42 tests stayed green, because the
    parametrised pin above only fed it blocks that have NO pre-contract `gh` call. The arm
    was right and nothing exercised it, so a rebase could reinstate the defect silently.
    These two rows red on that revert: `check-required-set.py` and `gh secret` are among the
    names the old list omitted, and both are real pre-contract hazards.
    """
    scripts = _gh_executing_scripts()
    clean = (
        "  job:\n"
        "    steps:\n"
        "      - name: contract\n"
        f"        run: bash .github/scripts/{CONTRACT_STEP}\n"
    )
    assert _contract_violations(clean, scripts) == [], "a compliant block was flagged"

    # A DERIVED HELPER that the old text list did not name, BEFORE the contract step.
    helper = (
        "  job:\n"
        "    steps:\n"
        "      - name: check\n"
        "        run: python3 .github/scripts/check-required-set.py\n"
        "      - name: contract\n"
        f"        run: bash .github/scripts/{CONTRACT_STEP}\n"
    )
    got = _contract_violations(helper, scripts)
    assert got and "check-required-set.py" in got[0], got

    # A `gh` SUBCOMMAND the old list did not name.
    sub = (
        "  job:\n"
        "    steps:\n"
        "      - name: peek\n"
        "        run: gh secret list\n"
        "      - name: contract\n"
        f"        run: bash .github/scripts/{CONTRACT_STEP}\n"
    )
    got = _contract_violations(sub, scripts)
    assert got and "gh secret list" in got[0], got


def test_a_guarded_contract_step_is_not_compliant():
    """A step that satisfies every textual check but may never run is the silent failure.

    `ensure gh` provisions a Linux pool runner, so `if: runner.os == 'Linux'` is the shape a
    future author reaches for — and it made this file's own definition of fail-open true:
    the job reaches `gh` with no contract, exit 127, and every textual check still passes.
    Also pins that the step must be in COMMAND POSITION, so a commented-out or merely
    printed step is not mistaken for the real one.
    """
    scripts = _gh_executing_scripts()
    body = f"        run: bash .github/scripts/{CONTRACT_STEP}\n"

    guarded = (
        "  job:\n    steps:\n      - name: contract\n        if: runner.os == 'Linux'\n" + body
    )
    got = _contract_violations(guarded, scripts)
    assert got and "`if:`" in got[0], got

    tolerant = (
        "  job:\n    steps:\n      - name: contract\n        continue-on-error: true\n" + body
    )
    got = _contract_violations(tolerant, scripts)
    assert got and "`continue-on-error:`" in got[0], got

    for shape, why in (
        (f"        # run: bash .github/scripts/{CONTRACT_STEP}", "commented out"),
        (f'        run: echo "run .github/scripts/{CONTRACT_STEP} by hand"', "printed"),
        (f"        run: |\n          bash .github/scripts/{CONTRACT_STEP}", "a block scalar"),
        (f"          bash .github/scripts/{CONTRACT_STEP}", "a continuation line"),
    ):
        got = _contract_violations(
            "  job:\n    steps:\n      - name: contract\n" + shape + "\n", scripts
        )
        assert got and "command position" in got[0], (why, got)

    # ⛔ KEY ORDER IS NOT A DEFENCE (#12167 review). YAML puts no constraint on it, and
    # GitHub's own examples write `continue-on-error:` after the command. The first cut of
    # this arm scanned only the lines BEFORE `run:`, so a step carrying BOTH defusers after
    # it answered `[]` — measured on this head: `if: runner.os == 'Linux'` and
    # `continue-on-error: true` each written below the `run:` produced no violation at all.
    for late in ("if: runner.os == 'Linux'", "continue-on-error: true"):
        got = _contract_violations(
            "  job:\n    steps:\n      - name: contract\n" + body + f"        {late}\n", scripts
        )
        assert got and late.split(":")[0] in got[0], (
            f"a `{late}` written AFTER the step's `run:` was not seen — a guarded contract "
            f"step reported clean ({got!r})"
        )

    # …and the SAME scan must not reach OUTSIDE the step. A bare `-` marker (the step's first
    # key on the next line) did not match `^      - `, so `step_start` stayed 0 and the JOB's
    # own `if:` was attributed to the contract step — a false violation whose demanded remedy
    # cannot apply. Measured on this head: `['the contract step carries a `if:`', …]`.
    bare = (
        "  job:\n"
        "    if: needs.changes.outputs.python == 'true'\n"
        "    runs-on: ubuntu-24.04\n"
        "    steps:\n"
        "      -\n"
        "        name: contract\n"
        f"        run: bash .github/scripts/{CONTRACT_STEP}\n"
    )
    assert _contract_violations(bare, scripts) == [], (
        "a JOB-level `if:` was attributed to the contract step — the bare `-` step marker "
        "does not end the backward search"
    )


def test_the_command_spelling_variants_are_hazards():
    """The spellings bash accepts for the SAME invocation must all be hazards.

    Three were measured as misses and each is now a row: a MODULE invocation
    (`python3 -m tools.ci_timing` — really runs `tools/ci_timing.py`, proven with a stub
    module that touched a marker), a GLOBAL FLAG before the subcommand
    (`gh -R owner/repo pr list`, verified against the real gh as `exit=0`), and the
    subcommands the old closed list omitted. The PATH-QUALIFIED binary stays a MISS and is
    declared in `_gh_hazards` — every live invocation spells the bare name, and the same
    lookbehind that keeps filenames out is what keeps paths out.
    """
    scripts = _gh_executing_scripts()
    for shape in (
        "        run: python3 -m tools.ci_timing --refresh-durations",
        "        run: python3 -m tools.ci_queue_health",
        "        run: gh cache delete --all",
        "        run: gh -R owner/repo pr list --limit 5",
        "        run: gh --repo owner/repo issue list",
        "        run: gh org list",
        "        run: gh project list",
    ):
        assert _gh_hazards([shape], scripts), shape
    # …and it must not have become a prose matcher.
    for prose in ("        run: gh is a tool", "        run: gh foo bar"):
        assert not _gh_hazards([prose], scripts), prose


def test_the_derivation_covers_the_operations_harness_home():
    """`_SCRIPT_GLOBS` must include the homes that really hold gh-executing helpers.

    `operations/orchestrator-harness/` was outside the tuple, so `merge-one.sh` (7 `gh`
    invocations) and `merged-not-closed.sh` (6) were invisible to the derivation while the
    comment above it claimed "everywhere". The SDK under `tortoise/` is excluded ON PURPOSE
    and the exclusion is named there, so this pins the half that must be covered.
    """
    names = _gh_executing_scripts()
    for name in ("merge-one.sh", "merged-not-closed.sh"):
        assert name in names, f"{name} is not derived — {_SCRIPT_GLOBS} does not reach it"


def test_every_gh_subcommand_is_recognised():
    """`_GH_CMD`'s closed list must not be narrower than the installed `gh`.

    This is the pin that keeps the list honest: the enumeration named 12 of the 28
    subcommands `gh --help` publishes, so eight real invocations (`gh cache delete`,
    `gh gist create`, `gh attestation verify`, `gh ruleset list`, `gh alias set`,
    `gh extension install`, `gh org list`, `gh ssh-key list`) were all really executed and
    all read CLEAN. Each was confirmed real by `gh <name> --help` exiting 0.
    """
    live = subprocess.run(["gh", "--help"], capture_output=True, text=True)
    if live.returncode != 0:
        pytest.skip("`gh` is not on PATH, so the installed command set cannot be read")
    # ⛔ ONLY THE `… COMMANDS` SECTIONS ARE SUBCOMMANDS. A flat `^  ([a-z-]+)` scan also
    # harvests HELP TOPICS (`accessibility`, `environment`, `exit-codes`, `reference`,
    # `mintty`, …) and FLAGS, which are not commands — and `gh` itself. The sections are
    # CORE / GITHUB ACTIONS / ALIAS / ADDITIONAL COMMANDS, each an all-caps header.
    published: set[str] = set()
    in_commands = False
    for line in live.stdout.splitlines():
        header = line.strip()
        if re.fullmatch(r"[A-Z][A-Z ]*COMMANDS", header):
            # ⛔ ALIAS COMMANDS ARE THIS MACHINE'S, NOT `gh`'s. The section lists whatever
            # the local user defined (`co` here), so folding it into a closed built-in list
            # would make the pin depend on who ran it — and the list would then acquire an
            # entry that no other box has.
            in_commands = "ALIAS" not in header
            continue
        if re.fullmatch(r"[A-Z][A-Z ]+", line.strip()):
            in_commands = False
            continue
        if in_commands:
            m = re.match(r"^  ([a-z][a-z-]+)\b", line)
            if m:
                published.add(m.group(1))
    assert published, "could not parse `gh --help`; this pin would otherwise pass vacuously"
    missing = sorted(published - set(_GH_SUBCOMMANDS))
    assert not missing, (
        f"`gh` publishes {missing} and `_GH_SUBCOMMANDS` does not list it/them — an "
        "invocation of any of these reads CLEAN in a workflow scan"
    )
    for name in _GH_SUBCOMMANDS:
        assert _gh_hazards(["        run: gh " + name + " x"], _gh_executing_scripts()), name


@pytest.mark.parametrize(
    ("workflow", "job"),
    [(wf, job) for wf, jobs in GH_JOBS.items() for job in jobs],
)
def test_every_gh_using_pool_job_carries_the_contract_step(workflow, job):
    """If a job that calls `gh` can land on the pool, it must provide it first.

    The step must be the FIRST shell step in the job: a `gh` call that precedes it
    still dies with 127, and the ordering cannot be seen from the exit code.
    """
    problems = _contract_violations(_job_block(WORKFLOWS / workflow, job), _gh_executing_scripts())
    assert not problems, f"{workflow}::{job} " + "; ".join(problems)


# ── the scan: a NEW gh-using job must be declared, not silently uncovered ────

#: `gh` at COMMAND position. Deliberately narrow: it must not fire on `GH_TOKEN`,
#: on a filename, or on prose (`gh` inside a comment is skipped by the caller).
#:
#: ⛔ THE LIST IS THE WHOLE COMMAND SET, and that is a correction. It named 12 of the 28
#: subcommands `gh --help` publishes, so `gh cache delete --all`, `gh gist create`,
#: `gh attestation verify`, `gh ruleset list`, `gh alias set`, `gh extension install`,
#: `gh org list` and `gh ssh-key list` were all really executed and all read CLEAN
#: (execution proven by a stub `gh` on PATH, and each name confirmed real by
#: `gh <name> --help` exiting 0). `test_every_gh_subcommand_is_recognised` re-derives the
#: set from the installed `gh` so a NEW subcommand cannot silently join the blind spot.
#:
#: ⛔ AND THE TRAILING BOUNDARY IS A WIDENING, NOT A NARROWING. The old pattern had none,
#: so `gh project` matched on `pr` and `gh organization` would have matched on `org`. Adding
#: `(?![-\w])` without `project`/`org` in the list would have NARROWED it — the one
#: direction this file must never drift — which is why the list carries both.
#:
#: A GLOBAL FLAG may sit between the binary and the subcommand (`gh -R owner/repo pr list`,
#: verified against the real gh as `exit=0`); without allowing it the whole invocation was
#: invisible. A PATH-QUALIFIED binary (`/usr/bin/gh …`) is still a MISS and is declared in
#: `_gh_hazards` rather than guessed at: the lookbehind that keeps filenames out is the same
#: one that keeps paths out, and every live invocation spells the bare name.
_GH_SUBCOMMANDS = (
    "agent-task",
    "alias",
    "api",
    "attestation",
    "auth",
    "browse",
    "cache",
    "codespace",
    "completion",
    "config",
    "copilot",
    "discussion",
    "extension",
    "gist",
    "gpg-key",
    "issue",
    "label",
    "licenses",
    "org",
    "pr",
    "preview",
    "project",
    "release",
    "repo",
    "ruleset",
    "run",
    "search",
    "secret",
    "skill",
    "ssh-key",
    "status",
    "variable",
    "workflow",
)
_GH_CMD = re.compile(
    r"(?<![\w./-])gh\s+(?:-{1,2}[A-Za-z][\w-]*\s+(?:[^\s-]\S*\s+)*)?"
    r"(?:" + "|".join(_GH_SUBCOMMANDS) + r")(?![-\w])"
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
#: A harness is not what a workflow runs IN ANGER; the ones that legitimately exercise
#: `gh` install a STUB on PATH. TWO do today (`ai-review-gate.test.sh`,
#: `deploy-api-alert.test.sh` — re-derived by matching `_GH_EXEC` against every file this
#: predicate excludes, so the number is measured, not remembered). They are excluded by
#: NAME, not by which glob they came from, so the exclusion cannot drift with the directory.
_NOT_A_HARNESS = re.compile(r"\.test\.|^test_")
#: Everywhere a CI HELPER that shells out to `gh` can live. Two corrections are recorded
#: here because the sentence above used to claim more than the tuple delivered:
#: `operations/orchestrator-harness/` was missing and is where two real ones live
#: (`merge-one.sh` — 7 `gh` invocations, `merged-not-closed.sh` — 6), and the claim
#: "everywhere" was measured false against the tree: 5 files outside this tuple matched
#: `_GH_EXEC`, of which 4 really execute `gh`.
#:
#: ⛔ `tortoise/` IS EXCLUDED DELIBERATELY, and the exclusion is NAMED rather than implied.
#: It is the SDK — a library, not a CI helper a workflow step invokes — and the three
#: modules in it that do execute `gh` (`gates.py` via `["gh","api",path]`,
#: `connectors/github.py` via `["gh","issue","list"]`/`["gh","pr","list"]`,
#: `scripts/manifest.py` as a prose match only) are reached through Python imports, never
#: as a `run:` command, so deriving their basenames would add three names to every
#: workflow scan for no coverage. Measured: 0 workflows reference any of them, and
#: widening the tuple to `tortoise/**/*.py` moved the live verdict not at all
#: (22/92 before and after) while reading 285 more files per derivation.
_SCRIPT_GLOBS = (
    ".github/scripts/*.sh",
    ".github/scripts/*.py",
    "tools/*.py",
    "tools/*.sh",
    "graph-scripts/*.py",
    "operations/*.sh",
    "operations/orchestrator-harness/*.sh",
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
    idioms (re-measured at this head by matching `_GH_EXEC` against every file in
    `_SCRIPT_GLOBS`; ⛔ an earlier version of this sentence claimed
    `test_the_gh_script_derivation_has_no_visible_blind_spot` re-measured it, and that
    test does NOT — it asserts the membership of nine REMEMBERED names, so a new script
    using an idiom outside the four keeps the suite green).
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


def _yaml_jobs(text: str) -> list[tuple[str, list[str], list[str]]] | None:
    """(job name, body lines, its `run:` values as YAML resolves them), by POSITION.

    ⛔ THE JOB -> BODY -> `run:` MAPPING COMES FROM ONE PLACE, and that is the whole point.
    A previous cut enumerated the jobs textually and then looked the composer's answers up BY
    NAME, so the fix was delivered through the enumeration it was meant to replace: a job id
    the text reader spelled differently (`"bu\\u0069ld":`, which YAML decodes to `build`) was
    enumerated under one name and the resolved `run:` values arrived under the other, so a
    folded step that really executed a helper landed on NO job — a regression the parent cut
    did not have (#12167 review, pass 5). `start_mark`/`end_mark` carry the line span, so a
    body is SLICED, never matched, and the two cannot disagree.

    `None` when the file does not parse at all, so the caller can fall back to the textual
    reader rather than lose every job in the file.
    """
    try:
        root = yaml.compose(text)
    except yaml.YAMLError:
        return None
    jobs = _mapping_entry(root, "jobs")
    if not isinstance(jobs, yaml.MappingNode):
        return None
    lines = text.splitlines()
    out: list[tuple[str, list[str], list[str]]] = []
    for key, job in jobs.value:
        if not isinstance(key, yaml.ScalarNode):
            continue
        body = lines[key.start_mark.line : job.end_mark.line + 1]
        runs: list[str] = []
        steps = _mapping_entry(job, "steps")
        if isinstance(steps, yaml.SequenceNode):
            for step in steps.value:
                run = _mapping_entry(step, "run")
                if isinstance(run, yaml.ScalarNode) and run.value.strip():
                    runs.append(run.value)
        out.append((key.value, body, runs))
    return out


def _textual_jobs(text: str) -> list[tuple[str, list[str], list[str]]]:
    """The FALLBACK reader, for a file YAML cannot parse at all.

    ⛔ BOUNDED TO THE `jobs:` BLOCK, and that bound is load-bearing rather than tidy. An
    earlier version collected EVERY two-space key in the file, which included the children
    of `on:` — so `push:`, `schedule:` and each `pull_request:` entry came back as a "job"
    (measured: 171 entries for 92 real jobs across the 41 workflows). A trigger block is a
    silently-scanned pseudo-job carrying a `paths:` list, which really does name workflow
    and helper-script paths, so the name arm flagged `welcome-e2e-monitor.yml::push` as a
    `gh` hazard (the unbounded collector and its false hit are pinned as REMOVED).

    This runs only when `yaml.compose` refuses the file, where the job names and bodies are
    all that can be recovered — the folded reading is unavailable, which is the ADDITIVE
    direction: a malformed file loses the join, never the physical lines.
    """
    lines = text.splitlines()
    header = re.compile(r'^  (?:"([^"]+)"|\'([^\']+)\'|([A-Za-z0-9_.-]+)):\s*(?:#.*)?$')
    start = next((i for i, line in enumerate(lines) if re.match(r"^jobs:\s*(?:#.*)?$", line)), None)
    if start is None:
        return []
    starts: list[tuple[int, str]] = []
    for i in range(start + 1, len(lines)):
        line = lines[i]
        if line and not line[0].isspace() and not line.startswith("#"):
            break  # the next top-level key: the `jobs:` block ended
        m = header.match(line)
        if m:
            starts.append((i, next(g for g in m.groups() if g is not None)))
    out = []
    for n, (at, name) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else len(lines)
        out.append((name, lines[at:end], []))
    return out


def _jobs(path: Path) -> list[tuple[str, list[str]]]:
    """(job name, its lines) for every job in a workflow file.

    Each `run:` value AS YAML RESOLVED IT is ADDED to its job's body and never substituted for
    the physical lines: the scan is allowed to see more, never less, and the physical lines are
    still what a message or a comment is read from. A value already present is skipped, so a
    literal block (`run: |`), whose resolved lines ARE its physical lines, adds nothing — and
    neither does the ordinary single-line `run: <cmd>`, whose resolved value is that command
    WITHOUT the `run: ` prefix. Comparing raw lines alone would add every one of those twice
    and report each hazard twice (measured: 51 duplicated hit lines across 18 workflows / 22
    jobs if this filter is removed — `finding-provenance.yml::provenance` 8, `::classify` 5,
    `ci-timing.yml::measure` 4, `mergify-guard-recut.yml::recut` 4, …).
    """
    text = path.read_text()
    parsed = _yaml_jobs(text)
    if parsed is None:
        parsed = _textual_jobs(text)
    out = []
    for name, body, runs in parsed:
        present = {line.strip() for line in body}
        present |= {re.sub(r"^-\s+run:\s*|^run:\s*", "", line.strip()) for line in body}
        out.append(
            (
                name,
                body
                + [
                    "      " + folded
                    for value in runs
                    for folded in value.splitlines()
                    if folded.strip() and folded.strip() not in present
                ],
            )
        )
    return out


def _mapping_entry(node: object, name: str) -> object | None:
    """The value node for key `name` in a YAML mapping node, or None.

    ⛔ LAST WINS, deliberately. A duplicate key (`steps:` twice, or `run:` twice in one step)
    is a file YAML 1.1/1.2 call invalid, but a reader may still accept it — and PyYAML's own
    `safe_load` keeps the LAST entry. First-wins here silently dropped a folded `run:` that
    `safe_load` resolves and that bash really executes, while this file's sibling assertions use
    `safe_load` as their oracle: two readers of the same file disagreeing about which duplicate
    wins is a divergence the suite could never see (#12167 review, pass 6).
    """
    found = None
    if not isinstance(node, yaml.MappingNode):
        return None
    for key, value in node.value:
        if isinstance(key, yaml.ScalarNode) and key.value == name:
            found = value
    return found


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
    `tools/install-ci-runner-x64.sh:759-764` REFUSES an expression rather than skipping it —
    "A matrix expression (`runs-on: ${{ matrix.os }}`) is the same case for a different
    reason: its label set is not static, so there is nothing to verdict" — and the refusal
    is `exit 4`, never a silent 0. (An earlier version of this paragraph cited `:377-378`
    and quoted a sentence that exists nowhere in that file: `:377-378` are `# label algebra`
    and `# Host-derived labels` comments. It also called the behaviour "skips", the
    opposite of what the script does.) The installer resolves labels against the ONLINE
    runner list instead; this scan has no such second source, so it cannot verdict: it must
    fail closed.

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
    used in the tree today — all 88 `runs-on:` keys carry their value on the key's own line
    (86 plain scalars, one inline FLOW list at `ci.yml:1609` that the inline arm answers, and
    one expression at `python-ci.yml:745`), so the miss is latent, not live — and they are
    named here so the next author writing one of them
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


#: Commands that EMIT a message rather than EXECUTE their arguments.
_MESSAGE_CMDS = ("echo", "printf")

#: A whole line that can only PRINT: a message command whose every argument is a quoted
#: literal. Anchored at BOTH ends, so a separator, a second command, a substitution or an
#: unterminated quote fails the match and the line is searched RAW, exactly as it was
#: before this fix. TRAILING REDIRECTS ARE ALLOWED because a redirect consumes its
#: argument without executing it (`>&2`, `>/dev/null`), and a PIPE IS NOT — a pipe hands
#: the text to the next command, which is exactly how `echo 'bash h.sh' | bash` runs the
#: helper.
#:
#: ⛔ THE REDIRECT TARGET IS OPTIONAL, and that is not sloppiness. `_blank_quoted` replaces
#: every quoted span with a space, so a QUOTED target erases itself:
#: `echo "…helper…" >> "$GITHUB_STEP_SUMMARY"` blanks to `echo   >>  `. A target class of
#: `[^\s;|&<>]+` then had nothing to consume, the anchored match failed, and the line fell
#: through to the raw search — so the most idiomatic redirect in this tree (`>>` to the job
#: summary, 14 live uses) turned PROSE into a HAZARD while its UNQUOTED twin
#: (`> /dev/null`) stayed inert. A bare `>` with no target left is a bash SYNTAX ERROR, so
#: treating it as a redirect that consumed nothing is the safe reading, not a hole.
#:
#: ⛔ AND A TARGET MAY CONTAIN ONLY **COMPLETE** ESCAPES, AND NO PARENTHESES. `\X` replaces
#: the pair, so a LONE trailing `\` cannot appear in a target — if the class accepted one it
#: would read `echo '…' 2>&1\` as "a redirect whose target is a file named `\`", the anchored
#: match would succeed, and the line would answer INERT. bash reads `\`+newline as a
#: CONTINUATION, so that line and a following `| bash` are ONE command that pipes the
#: message into bash and RUNS the helper — measured, with the `\` accepted. DIRECTION: an
#: INHERITED MISS on `origin/main` (it misses too) and a REGRESSION against pre-`#12106`
#: (which HIT it), so it is closed rather than declared (#12167 review).
#:
#: NOR A PARENTHESIS, because `>( … )` / `<( … )` IS a process substitution and bash RUNS its
#: body — which falsifies this block's own premise one paragraph up ("a redirect consumes its
#: argument without executing it"). A target class that admitted `(`/`)` swallowed the whole
#: substitution as a consumed argument, so `echo > >(bash${IFS}tools/ci_tim\ing.py)` answered
#: INERT, and an inert line is searched ONLY by `_code_only`, never by the spliced views — so
#: the escaped name was contiguous nowhere and 45 spellings ran the helper with no hit
#: (measured). DIRECTION: inherited on all three readers, but it sat INSIDE a rule this
#: change introduces, so it is closed here rather than declared.
_INERT_MESSAGE = re.compile(
    r"^\s*(?:-\s+)?(?:run:\s*)?(?:" + "|".join(_MESSAGE_CMDS) + r")"
    r"(?:\s+-[A-Za-z]+)*"  # the message command's own flags (`-e`, `-n`)
    r"(?:\s*(?:\d?>>?|\d?>&|&>>?)\s*(?:[^\s;|&<>()\\]|\\.)*)*"  # no pipe, no `(`, no lone `\`
    r"\s*$"
)

#: bash's two WORD-joining rules, applied to a line so a SPLIT name is still visible to the
#: contiguous-name matcher (see `_gh_hazards`): delete the quote characters, and unescape
#: `\X`. Run only on lines that are NOT inert, so a message's own prose quotes are untouched.
_UNQUOTED = re.compile(r"[\"']")
_UNESCAPED = re.compile(r"\\(.)")

#: The quote characters bash treats as span delimiters.
_QUOTE_CHARS = frozenset({"'", '"'})


def _blank_quoted(line: str) -> str:
    """Replace every QUOTED SPAN with ONE space; return the line unchanged if a span
    never closes (so an unterminated quote can never read as bare).

    This is the guard that decides which text a message can only PRINT, so it is written
    as a small LEXICAL WALK rather than a regex, because a regex got this wrong three
    times, each time in the LENIENT direction, each time found only by running bash:

      1. DELETING A SPAN MOVES COMMAND POSITION. `"bash" "tools/h.sh" echo` lost both
         quoted words and left ` echo`, satisfying the anchored question while bash ran
         `bash tools/h.sh`. `_MESSAGE_WORD` (below) guards command position.
      2. A BACKSLASH IS AN ESCAPE, NOT A PREVIOUS CHARACTER. `echo>\" ; bash h.sh ; \"`
         — bash reads `\"` as a LITERAL quote, so the `;` is a REAL separator; a regex
         that treated the escaped quote as an opener swallowed the separator.
      3. …AND ESCAPES PAIR, so "not preceded by a backslash" is the wrong test too:
         `echo>a\\'~'; bash 'tools/h.sh'` has an ESCAPED BACKSLASH followed by a REAL
         opener, and a lookbehind skipped it and shifted the whole pairing.
      4. DELETING A SPAN MANUFACTURES AN OPERATOR. `echo>"x"&bash 'tools/h.sh'` welded
         `>` and `&` into `>&`, which the redirect arm read as one redirect.

    So: walk left to right, consume `\\X` as a PAIR (which is exactly bash's parity rule
    OUTSIDE a single-quoted span: an odd run escapes, an even run does not — inside `'…'`
    there are no escapes at all, see (5)), and replace a span — the two delimiters and
    everything between — with a SINGLE SPACE. A space cannot be mistaken for an operator
    and cannot fuse with its neighbours, so (4) is impossible; walking the escapes closes
    (2) and (3); `_MESSAGE_WORD` closes (1); handling the single-quote span before the
    escape branch closes (5).

    5. SINGLE QUOTES DO NOT ESCAPE — `\'` INSIDE `'…'` IS A BACKSLASH AND THE CLOSER.
       bash ends a single-quoted span at the next `'` unconditionally, so a backslash in
       there is an ordinary character. Consuming `\'` as a PAIR (the rule above, applied
       everywhere) swallowed the span's REAL closer, kept the walk "inside" the span to
       end of line, blanked everything after it, and answered INERT for a line that
       RUNS the helper — measured, marker file created:

           echo 'a\' \" ; bash tools/h.sh ; true \" # '

       Both readings then miss it: the RAW arm is skipped because the line judged inert,
       and `_code_only`'s own naive pairing blanks the region holding the name. `#12149`'s
       landed walker misses it too, so it is an inherited hole rather than a regression —
       but this walk documents its escape rule as "exactly bash's parity rule", and that
       was false in the one span type where bash has no escapes.
    """
    out: list[str] = []
    i, n = 0, len(line)
    quote = ""
    while i < n:
        ch = line[i]
        # ⛔ A SINGLE-QUOTED SPAN CLOSES AT THE NEXT `'`, ESCAPE OR NOT. Checked BEFORE the
        # escape branch, because that branch's pair rule is right everywhere else and
        # catastrophically wrong here: `'a\' ; bash h.sh` would keep the walk inside the
        # span (the `\\`+`'` consumed as a pair) and blank the invocation. Folded into the
        # general `if quote:` branch below instead of a separate case would re-open it.
        if quote == "'":
            if ch == "'":
                quote = ""
                out.append(" ")
            i += 1
            continue
        if ch == "\\" and i + 1 < n:
            # An escape consumes BOTH characters — inside a span it is skipped, outside it
            # is kept, and either way the byte after it can never open or close a span.
            if not quote:
                out.append(line[i : i + 2])
            i += 2
            continue
        if quote:
            if ch == quote:
                quote = ""
                out.append(" ")
            i += 1
            continue
        if ch in _QUOTE_CHARS:
            quote = ch
            i += 1
            continue
        out.append(ch)
        i += 1
    if quote:
        return line  # unterminated: nothing about this line is proved
    return "".join(out)


def _comment_cut(line: str) -> int:
    """Index where bash's trailing COMMENT starts, or `len(line)` when there is none.

    ⛔ COMPUTED ON THE RAW LINE, and that is the whole point. `_blank_quoted` replaces a
    closed span with a SPACE, which MANUFACTURES a word boundary the comment rule then
    keys on: `echo "x"#c; bash h.sh` blanks to `echo  #c; bash h.sh`, the `#c` reads as a
    comment, the real invocation after the `;` is deleted from the text being scanned, and
    the line answers INERT. bash reads the `#` there as CONTINUING the word `x`, so it is
    DATA and the `;` is a REAL separator — measured: the helper runs while the PRE-REVIEW
    head answered nothing, and BOTH baselines (`origin/main` and pre-`#12106`) HIT it. That
    makes this a REGRESSION the new rule introduced, closed here.

    bash starts a comment at a `#` that begins a WORD. This walk decides "word start" the
    same way `_blank_quoted` decides spans — escapes consume a PAIR, and `'…'` has no
    escapes at all — and it is deliberately NARROWER than bash on exactly one point:
    unescaped WHITESPACE is the only thing that opens a new word here, so a `#` right
    after an operator (`echo x;#c`) is NOT treated as a comment. That direction is
    fail-closed — the line keeps its tail, fails the anchored question and is searched
    RAW — which is the only direction this gate may err in.
    """
    i, n = 0, len(line)
    quote = ""
    word_start = True  # start of line IS a word start
    while i < n:
        ch = line[i]
        if quote == "'":  # a single-quoted span ends at the next `'`, escape or not
            if ch == "'":
                quote = ""
            i += 1
            word_start = False
            continue
        if ch == "\\" and i + 1 < n:
            i += 2  # an escape is one WORD-internal unit, never a boundary
            word_start = False
            continue
        if quote:
            if ch == quote:
                quote = ""
            i += 1
            word_start = False
            continue
        if ch in _QUOTE_CHARS:
            quote = ch
            i += 1
            word_start = False
            continue
        if ch == "#" and word_start:
            return i
        word_start = ch.isspace()
        i += 1
    return n


#: The message command must be the FIRST WORD OF THE RAW LINE — i.e. in command
#: position, not brought into command position by `_blank_quoted` deleting a quoted word
#: that bash would have EXECUTED (`"bash" "tools/h.sh" echo`).
_MESSAGE_WORD = re.compile(r"^\s*(?:-\s+)?(?:run:\s*)?(?:" + "|".join(_MESSAGE_CMDS) + r")\b")


def _message_line_is_inert(line: str) -> bool:
    """Can this whole line only PRINT? If not, the line is searched RAW.

    ⛔ THE RULE IS CONSERVATIVE BY CONSTRUCTION, AND THAT IS THE POINT. Three earlier cuts
    of this fix each modelled the shell and each was falsified by a shape it did not
    anticipate — every one of them a FALSE NEGATIVE (a real invocation made invisible) in
    a gate whose whole subject is that an undeclared `gh` helper must not be hideable:

        echo "starting"; bash tools/install-ci-runner-x64.sh  # cut 1 excluded by line prefix
        echo $(python3 "$WS/tools/ci_timing.py")              # cut 2 decided by the span's TEXT
        echo hi & bash "tools/install-ci-runner-x64.sh"       # cut 2 had no `&` separator
        echo "$(printf ')'; bash tools/h.sh)"                 # cut 3 desynced on a quoted `)`
        echo 'bash tools/h.sh' | bash                         # cut 3: the consumer EXECUTES it

    So this does not try to decide WHICH parts of a line are executable. It asks one
    anchored question — is the entire line a message command with nothing but quoted
    literal arguments? — and answers `False` the moment anything else is present:

      * `$(` or a backtick ANYWHERE  → False. Detected on the raw line, before any quote
        handling, so a quoted `)` or a `case` pattern cannot desync anything. This is what
        closes the `printf ')'; bash …` shape without tracking the substitution at all.
      * a separator (`;` `&` `|`), a pipe, a second command, an unquoted argument, or an
        UNTERMINATED quote → the blanked text is not bare `echo`/`printf` → False. A
        TRAILING REDIRECT is NOT in this list: a redirect consumes its argument without
        executing it, so `echo "…" >&2` is inert — see `_INERT_MESSAGE`, which is where
        that allowance lives and where removing it would `>` a pinned row.
      * the first word of the RAW line is not the message command → False, even when the
        blanked text looks bare. `"bash" "tools/h.sh" echo` strips to ` echo` and would
        otherwise answer `True` for a line that RUNS `bash tools/h.sh`.

    WHAT THE CODE ACTUALLY GUARANTEES, stated without the monotone claim two earlier
    versions of this docstring made and could not support: **a line this returns `False`
    for is searched by the RAW-line rule the PRE-#12106 scan applied to every line.** So
    relative to that scan the exclusion REMOVES detections and adds none — measured: 0
    gained over a 60 000-line fuzz corpus, and exactly 3 lines / 0 added across the tree
    today (`finding-provenance.yml` x2, `mergify-guard-recut.yml` — all three operator
    messages; it was 4 before `#12051` reworded the fourth away, see
    `test_a_script_named_only_in_an_operator_message_is_not_a_gh_hazard`).
    **A removal is only as safe as the shape test behind it**: this is a regex, not a
    parser, so a wrong answer in the lenient direction is possible and three have already
    happened (see `_blank_quoted`, classes (1)-(5)). The mitigation is not a promise but the guards —
    `$(`-anywhere, `_blank_quoted`'s escape-parity walk, `_MESSAGE_WORD` — each pinned by a
    fail-open test. Relative to #12106's cut 3 (which blanked prose inside message
    segments) this is wider on every message line: measured, 0 differing verdicts across
    the tree. Do NOT "restore" cut 3's prose blanking to reconcile the two — that
    blanking is the modelling this cut deliberately drops.

    A pipeline whose consumer runs the message (`echo 'bash h.sh' | bash`) is inert to the
    MESSAGE and executable to `bash` — the pipe is not a redirect, and a message's text
    being handed to an interpreter is precisely what "another command" means.

    ⛔ STATED RESIDUAL — ONE CLASS IS OVER-REPORTED, AND IT IS THE PRICE OF THE `$(` BAIL.
    Because `$(` anywhere answers `False`, an operator message that merely CONTAINS a
    nested substitution is searched raw and reports the helper it prints:

        echo "dir $(basename "$(pwd)") — repair with tools/install-ci-runner-x64.sh"

    #12149's walker read that as prose. Splitting it from
    `echo "$(bash h.sh)"` — which RUNS the helper — requires reading the substitution
    body, and every cut that read it was falsified by a shape it did not anticipate
    (`case` patterns, an escaped quote, a `)` bash treats as data). So the bail is
    constant and this line pays for `echo "$(bash h.sh)"` staying closed. The direction
    is the acceptable one: over-reporting asks a healthy job for a contract step, which
    is visibly repairable; under-reporting hides an undeclared `gh` helper, which is the
    defect the gate exists for. MEASURED for live cost: across all 41 workflows / 92 jobs
    this arm and #12149's landed walker produce ZERO differing verdicts — no live instance
    exists in the tree today. Pinned in both directions by
    `test_the_nested_substitution_residual_is_over_report_not_a_miss`; if one ever
    appears, add the contract step or narrow the substitution reading — do NOT drop the
    bail.

    ⛔ SECOND DECLARED RESIDUAL, IN THE OPPOSITE DIRECTION — A MESSAGE'S TEXT EXECUTED ON A
    LATER LINE IS A MISS. This reader answers for ONE LINE, so:

        echo 'bash tools/h.sh' > /tmp/gen.sh   # inert: a redirect executes nothing
        bash /tmp/gen.sh                       # names no helper, so no hit

    comes back CLEAN, though the job body really runs the helper — measured, marker file
    created, `_gh_hazards(body, scripts) == []`. The SAME-LINE version
    (`echo 'bash h.sh' > /tmp/x; bash /tmp/x`) IS a hazard, so the sliver is the cross-line
    case only; it is inherited from `origin/main` and is not a regression.

    ⛔ IT IS NOT CLOSED BY FORBIDDING REDIRECTS ON AN INERT MESSAGE, and that is a
    decision rather than an omission: such a rule would flag every operator message that
    sends its help text to stderr (`echo "… tools/h.sh …" >&2`), which is `#12133`'s
    false-positive class and is pinned INERT by three rows in
    `test_the_message_exclusion_cannot_hide_a_real_invocation`. Trading a live false red
    for a cross-line shape with no live instance is the wrong direction, and #12133's cost
    was a required CI gate withheld from every PR. Closing this needs body-level dataflow,
    which is the widening `#12157` owns. Pinned as a declared miss, so a future fix reds
    the pin and must update this paragraph.
    """
    if "$(" in line or "`" in line:
        return False
    if _MESSAGE_WORD.match(line) is None:
        return False
    return _INERT_MESSAGE.match(_blank_quoted(line[: _comment_cut(line)])) is not None


#: A `python -m` DOTTED MODULE reference expanded into the path it names, so
#: `python3 -m tools.ci_timing` and `tools/ci_timing.py` are matched by ONE predicate.
#: ⛔ THE DIRECTION MATTERS: the dotted form carries no `.py`, so rewriting paths INTO dots
#: matches nothing — the expansion has to be dots -> slashes + `.py`. Restricted to at least
#: one dot so it cannot invent a path from a bare word, and applied to the already-blanked
#: views so a version number in a comment is never the thing that gets expanded.
_DOTTED_MODULE = re.compile(r"(?<![\w.])(?:[A-Za-z_]\w*\.)+[A-Za-z_]\w*(?![\w.])")


def _gh_hazards(body: list[str], scripts: frozenset[str]) -> list[str]:
    hits = []
    # ⛔ BOUNDARY-MATCHED, not `name in code`. A substring test made
    # `python3 -m pytest tests/test_ci_timing.py` look like a `gh` user (the helper
    # name `ci_timing.py` sits inside the test module's name), and the remedy it
    # demands — `ensure gh` — FAILS CLOSED, so on a no-egress pool runner it would
    # red a healthy job. 32 such test modules exist today (#9939 review).
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
        # TWO ARMS, TWO READINGS, deliberately — see `_message_line_is_inert`: `gh` is
        # read from quote-stripped text (a `gh` in an operator message is prose), while a
        # helper-script NAME is read from the RAW line unless the WHOLE line can only
        # print, in which case quote-stripped text is used instead.
        #
        # ⛔ AND THE RAW LINE IS NOT ENOUGH, because bash WORD-SPLICES. `script_ref` matches a
        # CONTIGUOUS name, but bash joins adjacent quote-delimited fragments and unescapes
        # `\X`, so all three of these RUN the helper while no literal `tools/ci_timing.py`
        # appears in the line (measured against a stub that ran under `bash -c`, and identical
        # on the walker this PR replaces — so this is a PRE-EXISTING fail-open, not one the
        # change introduces):
        #
        #     bash tools"/ci_timing."py     -> bash tools/ci_timing.py
        #     bash 'tools/ci_'timing.py    -> bash tools/ci_timing.py
        #     bash tools/ci_tim\ing.py     -> bash tools/ci_timing.py
        #
        # The two views below are bash's two joining rules applied to the same line, so a
        # spliced name is visible without a parser: delete the quote characters (bash
        # deletes them and keeps the words adjacent), and unescape `\X` (bash does the same).
        # ⛔ WHAT THAT COSTS — stated rather than denied, because an earlier version of this
        # comment claimed the views "add no false-positive class" and that is FALSE. They add
        # ONE, in the fail-closed direction: a NON-inert message line that spell-splits a
        # helper name as ordinary prose is now reported where `origin/main` read it as prose.
        # Measured, all PROSE to bash and all HAZARD here / prose on `origin/main`:
        #
        #     echo run tools/ci_"timing.py"
        #     echo see: tools/ci_"timing.py" for details
        #     printf %s tools/ci_tim\ing.py
        #
        # The class needs an UNQUOTED word plus a quote or an escape inside the name, and it
        # has no live instance — but it is a real widening and is named here so nobody reads
        # this arm as free.
        #
        # STATED RESIDUALS — shapes that really EXECUTE a helper and stay out of reach, each
        # measured with a stub that ran under `bash -c`:
        #
        #   * a name assembled through a VARIABLE (needs dataflow, not a regex):
        #         for f in ci_timing; do bash tools/$f.py; done
        #         H=tools/ci_timing; bash $H.py
        #   * BRACE EXPANSION — a third word-formation rule this arm does not model:
        #         bash tools/ci_timing.p{y,}
        #         bash tools/{ci_timing,other}.py
        #   * a BACKSLASH-NEWLINE join ACROSS LINES, which `_gh_hazards` cannot see at all
        #     because it reads one line at a time (`run: |` with a trailing `\`):
        #         bash tools/ci_tim\
        #         ing.py
        #     The same-line `\X` case IS caught; only the join is out of reach. All three
        #     classes are inherited from `origin/main`, and all are pinned as declared misses
        #     in `test_the_unmodelled_word_formation_residuals_are_declared_misses`.
        if _message_line_is_inert(line):
            name_views: tuple[str, ...] = (code,)
        else:
            name_views = (line, _UNQUOTED.sub("", line), _UNESCAPED.sub(r"\1", line))
            # ⛔ AND A MODULE INVOCATION SPELLS THE NAME WITH DOTS. `python3 -m tools.ci_timing`
            # really runs `tools/ci_timing.py` (proven with a stub module that touched a
            # marker), but `script_ref` matches a contiguous `tools/ci_timing.py` path, so the
            # dotted spelling read CLEAN. Added as a fourth view so the two syntaxes are
            # matched by one predicate; `tools/` is a package (`tools/__init__.py` exists),
            # which is why the form works at all.
            name_views = name_views + tuple(
                _DOTTED_MODULE.sub(lambda m: m.group(0).replace(".", "/") + ".py", v)
                for v in name_views
            )
        if _GH_CMD.search(code) or (
            script_ref is not None and any(script_ref.search(v) for v in name_views)
        ):
            hits.append(stripped)
    return hits


def _undeclared_gh_jobs(
    workflows: Path,
    scripts: frozenset[str],
    wired: set[tuple[str, str]],
    declared_gaps: set[tuple[str, str]],
) -> list[str]:
    """`workflow::job` for every poolable gh-using job that carries no contract step.

    ⛔ EXTRACTED SO THE COVERAGE ARM CAN BE PINNED, for the same reason
    `_contract_violations` was. While this lived inline, reverting its cover test to the
    loose `CONTRACT_STEP in text` left every test green (measured): the arm was right and
    nothing exercised it, so a rebase could reinstate a scan that goes green on a job whose
    contract step is commented out. `test_a_commented_out_or_printed_step_is_not_coverage`
    pins it on a synthetic workflow, where a brand-new job is exactly what this exists to
    catch.
    """
    undeclared: list[str] = []
    for path in sorted(workflows.glob("*.yml")):
        for job, body in _jobs(path):
            text = "\n".join(body)
            if not _poolable_label(text):
                continue
            if not _gh_hazards(body, scripts):
                continue
            # Covered = carries the step (the property that matters) OR is declared.
            # `GH_JOBS` is the separate POSITION pin, not the coverage record.
            #
            # ⛔ THE SAME PREDICATE AS THE ORDERING PIN — literally the same function's
            # question, and it used to be the loose substring test one arm up.
            # `CONTRACT_STEP in text` matched a COMMENTED-OUT step
            # (`# run: bash .github/scripts/…`) and a prose one
            # (`echo "if this 404s, run … ensure gh by hand"`) — so `_gh_hazards` correctly
            # reported a `gh`-using job that had NO contract step, and THIS arm then
            # suppressed the finding and the scan went GREEN. Measured on a synthetic
            # workflow with an empty `GH_JOBS`/`KNOWN_UNCOVERED` (i.e. a brand-new job, which
            # is exactly what this scan exists to catch): both shapes passed. Loose == strict
            # across all 41 workflows / 92 jobs today (0 divergence), so this moves no live
            # verdict — and it is the `#12133` prose these very steps carry.
            #
            # ⛔ AND A GUARDED STEP IS NOT COVERAGE EITHER (#12167 review). The strict
            # predicate alone still accepted a contract step carrying `if:` or
            # `continue-on-error:`, so a BRAND-NEW job — the only thing this scan exists to
            # catch — was suppressed as covered while `_contract_violations` called the same
            # block a violation. `_live_contract_step` reads the guard through the SAME
            # `_step_defusers` the ordering arm uses, so the two cannot diverge again.
            if _live_contract_step(body):
                continue
            if (path.name, job) in wired or (path.name, job) in declared_gaps:
                continue
            undeclared.append(f"{path.name}::{job}")
    return undeclared


def test_a_commented_out_or_printed_step_is_not_coverage(tmp_path):
    """The coverage arm must not accept a step that only MENTIONS the remedy.

    ⛔ FINDING #1 OF REVIEW ROUND 5, PINNED. Round 5 bound the ordering pin to a strict
    predicate and left this sibling arm on `CONTRACT_STEP in text`. That substring test
    matches a commented-out step and a printed one, so `_gh_hazards` correctly reported a
    `gh`-using job with NO contract step and this arm then SUPPRESSED it: the scan went
    green. Measured on a synthetic workflow with empty `GH_JOBS`/`KNOWN_UNCOVERED` — i.e. a
    brand-new job, which is precisely what the scan exists to catch — both shapes passed.
    Loose == strict across all 41 real workflows today (0 divergence), so this pin is the
    only thing standing between the two.
    """

    def synthetic(body: str) -> str:
        return (
            "name: synth\n"
            "on: push\n"
            "jobs:\n"
            "  newjob:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n" + body
        )

    (tmp_path / "synthetic.yml").write_text(
        synthetic(
            "      - name: peek\n"
            "        run: gh pr view 1 --json number\n"
            "      - name: contract (disabled)\n"
            "        # run: bash .github/scripts/" + CONTRACT_STEP + "\n"
        )
    )
    got = _undeclared_gh_jobs(tmp_path, _gh_executing_scripts(), set(), set())
    assert got == ["synthetic.yml::newjob"], (
        "a job whose contract step is COMMENTED OUT reported no violation — the coverage "
        f"arm accepted prose as coverage ({got!r})"
    )

    (tmp_path / "synthetic.yml").write_text(
        synthetic(
            "      - name: peek\n"
            "        run: gh pr view 1 --json number\n"
            "      - name: advise\n"
            '        run: echo "if this 404s, run .github/scripts/' + CONTRACT_STEP + ' by hand"\n'
        )
    )
    got = _undeclared_gh_jobs(tmp_path, _gh_executing_scripts(), set(), set())
    assert got == ["synthetic.yml::newjob"], (
        f"a job that only PRINTS the remedy reported no violation ({got!r})"
    )

    # …and the control: the real step IS coverage, so the pin is not passing by flagging
    # everything.
    (tmp_path / "synthetic.yml").write_text(
        synthetic(
            "      - name: contract\n        run: bash .github/scripts/" + CONTRACT_STEP + "\n"
        )
    )
    assert _undeclared_gh_jobs(tmp_path, _gh_executing_scripts(), set(), set()) == [], (
        "a compliant synthetic job was reported as undeclared"
    )

    # ⛔ AND A GUARDED STEP IS NOT COVERAGE (#12167 review). The strict `_CONTRACT_RUN_RE`
    # predicate says nothing about guard keys, so a brand-new job whose contract step carries
    # `if:`/`continue-on-error:` was suppressed as COVERED — while `_contract_violations` on
    # the same block called it a violation. Measured on this head: `[]` from this arm and
    # `['the contract step carries a `if:`', …]` from the ordering arm. The two arms must not
    # be able to disagree; `_live_contract_step` reads the guard through the SAME
    # `_step_defusers` the ordering arm uses.
    for where in ("before", "after"):
        guard = "        if: runner.os == 'Linux'\n"
        run_line = "        run: bash .github/scripts/" + CONTRACT_STEP + "\n"
        step = (
            "      - name: contract\n" + guard + run_line
            if where == "before"
            else "      - name: contract\n" + run_line + guard
        )
        (tmp_path / "synthetic.yml").write_text(
            synthetic("      - name: peek\n        run: gh pr view 1 --json number\n" + step)
        )
        got = _undeclared_gh_jobs(tmp_path, _gh_executing_scripts(), set(), set())
        assert got == ["synthetic.yml::newjob"], (
            f"a job whose ONLY contract step is guarded (`if:` {where} the run:) was accepted "
            f"as coverage — the scan went green on a step that can never provision anything "
            f"({got!r})"
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
    undeclared = _undeclared_gh_jobs(WORKFLOWS, _gh_executing_scripts(), wired, declared_gaps)
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


def test_the_job_splitter_returns_exactly_the_jobs_yaml_declares(tmp_path):
    """`_jobs` must enumerate JOBS, not every two-space key in the file.

    ⛔ WITHOUT THIS PIN THE BOUND IS UNENFORCED, and it is load-bearing twice over. The
    unbounded version also collected the children of `on:` — so `push:`, `schedule:` and each
    `pull_request:` entry came back as a "job" (measured: 171 entries for 92 real jobs across
    the 41 workflows). Those pseudo-jobs carry a `paths:` list that really does name workflow
    and helper-script paths, so the name arm flagged `welcome-e2e-monitor.yml::push` as a `gh`
    hazard — harmless only because a trigger block has no `runs-on`, which is a coincidence and
    not a guarantee. In the other direction a job header with a trailing comment or a quoted id
    did not match at all, so the job (and, if it came first, its whole body) left the scan
    silently.

    Comparing against `yaml.safe_load` is the point: it is an INDEPENDENT reader, so this
    cannot pass by agreeing with `_jobs`' own mistakes.
    """
    wrong = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        declared = set((yaml.safe_load(path.read_text()) or {}).get("jobs") or {})
        split = [name for name, _ in _jobs(path)]
        if set(split) != declared or len(split) != len(declared):
            wrong.append(f"{path.name}: _jobs={sorted(split)} declared={sorted(declared)}")
    assert not wrong, (
        "_jobs no longer agrees with the `jobs:` block these files declare — a pseudo-job (an "
        "`on:` trigger child) is being scanned as a job, or a real job is being dropped from "
        "the scan: " + "; ".join(wrong)
    )

    # ⛔ AND THE TOLERANCE ITSELF IS PINNED, because the tree-wide comparison above does NOT
    # arm it: no live workflow writes a job id with a trailing comment or in quotes, so
    # reverting `header` to the strict `^  [A-Za-z0-9_.-]+:\s*$` leaves this test green
    # (measured). The consequence of that revert is a job SILENTLY LEAVING THE SCAN — and if
    # it is the first two-space key in its file, its whole body with it — which is a
    # fail-open, so the two spellings get a synthetic file of their own.
    synthetic = tmp_path / "synthetic.yml"
    synthetic.write_text(
        "jobs:\n"
        "  plain:\n"
        "    runs-on: ubuntu-24.04\n"
        "  build:  # a trailing comment on the id\n"
        "    runs-on: ubuntu-24.04\n"
        '  "quoted":\n'
        "    runs-on: ubuntu-24.04\n"
        "  'single-quoted':\n"
        "    runs-on: ubuntu-24.04\n"
    )
    assert [name for name, _ in _jobs(synthetic)] == [
        "plain",
        "build",
        "quoted",
        "single-quoted",
    ], "the tolerant job header stopped tolerating a spelling it is documented to tolerate"

    # ⛔ AND THE `jobs:` BOUND'S END IS PINNED TOO, because it is the same fail-open shape as
    # the start: a top-level key AFTER `jobs:` must end the block, or every later key's
    # children are scanned as jobs. Measured, deleting that `break` left the whole suite green
    # — no live workflow has a top-level key after `jobs:` today, which is exactly why the
    # bound needs a synthetic file rather than a tree-wide claim (#12167 review).
    trailing = tmp_path / "trailing.yml"
    trailing.write_text(
        "jobs:\n"
        "  build:\n"
        "    runs-on: ubuntu-24.04\n"
        "env:\n"
        "  DEPLOY_ENV:\n"
        "    descends from a top-level key, not a job\n"
    )
    assert [name for name, _ in _jobs(trailing)] == ["build"], (
        "a top-level key after `jobs:` leaked into the job list — its children are being "
        "scanned as jobs"
    )

    # …and the SAME bound in the FALLBACK reader, which is the only reader a file YAML cannot
    # parse gets. It is reachable code with no live instance (every workflow parses), so
    # without this row deleting its `break` leaves the whole suite green — measured.
    malformed = (
        "jobs:\n"
        "  build:\n"
        "    runs-on: ubuntu-24.04\n"
        "    steps: [ { run: 'unbalanced }\n"  # unparseable on purpose
        "env:\n"
        "  DEPLOY_ENV:\n"
        "    descends from a top-level key, not a job\n"
    )
    assert _yaml_jobs(malformed) is None, "this file is supposed to be unparseable"
    assert [name for name, _, _ in _textual_jobs(malformed)] == ["build"], (
        "the fallback reader leaked a top-level key into the job list"
    )


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
    # ⛔ THE TRAILING HALF OF THE BOUNDARY, which the rows above do NOT arm. `test_ci_timing.py`
    # is also caught by the LEADING lookbehind (the `_` before the stem), so deleting
    # `(?![\w.-])` from the compiled pattern left this suite green — the docstring's "the helper
    # name must not be part of a longer path component" was half-armored. These rows violate
    # only the TRAILING arm, so they red exactly when it goes.
    assert _gh_hazards(["        run: python3 tools/ci_timing.py.bak"], scripts) == []
    assert _gh_hazards(["        run: python3 tools/ci_timing.pyc"], scripts) == []
    # …while a REAL invocation of the helper is still a hazard, in both shapes.
    assert _gh_hazards(["        run: python3 tools/ci_timing.py --refresh-durations"], scripts)
    assert _gh_hazards(['        run: python3 "$GITHUB_WORKSPACE/tools/ci_timing.py"'], scripts)
    # ⛔ A NAME SPLIT BY QUOTING OR ESCAPING IS STILL A NAME (see `_gh_hazards`). bash
    # WORD-SPLICES, so each of these executes `tools/ci_timing.py` while no contiguous
    # spelling of it appears in the line — measured against a stub that ran under `bash -c`.
    # This was a PRE-EXISTING fail-open, identical on the walker this PR replaces.
    for spliced in (
        '        run: bash tools"/ci_timing."py',
        "        run: bash 'tools/ci_'timing.py",
        "        run: bash tools/ci_tim\\ing.py",
    ):
        assert _gh_hazards([spliced], scripts), spliced
    # …and the DECLARED residual stays a residual: a name assembled through a variable is out
    # of reach of a name matcher. Asserted so the limitation is measured, not folklore.
    assert _gh_hazards(["        run: H=tools/ci_timing; bash $H.py"], scripts) == []


def test_an_operator_message_naming_a_helper_is_not_a_gh_hazard():
    """PROSE is not an INVOCATION — the false positive that reddened main's tip.

    MEASURED 2026-11-12 on main `4b1d6cd0d4`: `test-record-postgres`'s client-pin step
    emitted `::error::… tools/install-ci-runner-x64.sh --repair-labels …` as operator
    guidance, and the NAME arm — reading the raw line, which it must — took the message
    for a hazard. `test_every_pool_eligible_gh_job_is_declared` then named
    `python-ci.yml::test-record-postgres` an undeclared gap, `test-carve-out (c)` reddened
    on main's tip, and `python-ci-gate` withheld a REQUIRED check from every PR.

    ⛔ THE `::error::` BRANCH IS GONE NOW (`9c9dcdd3cc`, #12117), so this is a READER CASE
    and not a row copied from the tree; the sentence above is a dated measurement about
    main's tip then, not a claim about today. The live-row version of this pin is what
    #12188 fixed after the deletion turned it red.
    """
    scripts = _gh_executing_scripts()
    assert "install-ci-runner-x64.sh" in scripts, (
        "the derivation no longer sees the helper this pin is written against, so the "
        "assertions below would pass vacuously"
    )
    prose = (
        '            echo "::error::routing drift #10289 — on it run CI_RUNNER_OS_LABEL=off'
        ' tools/install-ci-runner-x64.sh --repair-labels --count <N>"'
    )
    assert _gh_hazards([prose], scripts) == []
    # …the SAME name in a real invocation is still a hazard, in both shapes.
    assert _gh_hazards(
        ["        run: bash tools/install-ci-runner-x64.sh --repair-labels"], scripts
    )
    assert _gh_hazards(
        ['        run: bash "tools/install-ci-runner-x64.sh" --repair-labels'], scripts
    )
    # …and an `echo` that really shells out is caught by the OTHER arm, not this one.
    assert _gh_hazards(['            echo "x"; gh api repos/o/r'], scripts)


def test_the_message_exclusion_cannot_hide_a_real_invocation():
    """The THREAT SURFACE the prose fix must not open (adversarial, #9018).

    The exclusion exists so inert text stops reddening. Every way a command can still
    EXECUTE on a line that also emits a message is a way this gate could fail OPEN — a
    job that runs a `gh` helper with no contract step, and a green suite.

    ⛔ THIRTEEN CLASSES ACROSS FOUR CUTS — each one was a FALSE NEGATIVE in a fix that
    modelled the shell. Where the class was already caught on `origin/main` it is stated
    as ARMOR rather than claimed as this change's finding (`#12167` review, and the same
    correction as the shape matrix above):

      1. `echo "starting"; bash h.sh`                  cut 1 excluded by LINE PREFIX
      2. `echo hi & bash "h.sh"`                       cut 2 had no bare-`&` separator
      3. `echo $(python3 "$WS/tools/h.py")`            cut 2 decided by the span's TEXT
      4. `echo "$(printf ')'; bash h.sh)"`             cut 3 desynced on a quoted `)`
      5. `echo 'bash h.sh' | bash`                     cut 3: the consumer EXECUTES the text
      6. `echo 'bash h.sh' > f; bash f`                cut 3: ditto, via a file

    …and classes 7-13, every one found by REVIEW on a later head rather than by a red —
    they are the reason this docstring no longer calls any guard "covered": a guard is
    covered when deleting it reds a row, and nothing else. Each is a FAIL-OPEN in this
    gate's own subject (a real invocation the scan answered nothing for):

      7. `printf x | bash h.sh`                        the helper AFTER a pipe
      8. `"bash" "tools/h.sh" echo`                    quoted words in COMMAND position
      9. `echo>\" ; bash h.sh ; \"`                     an ESCAPED quote is not an opener
     10. `echo>a\\'~'; bash 'h.sh'`                     escape PARITY / span welding
     11. `echo 'a\' …; bash h.sh`                       no escapes inside `'…'`
     12. `echo "x"#c; bash h.sh`                       a `#` continuing a WORD is not a comment
         `echo 'bash h.sh' 2>&1\\` + `| bash`           a redirect target holds no lone `\\`

    REGRESSIONS ON `origin/main` WHEN THIS CUT WAS WRITTEN: classes 5 and 6 only —
    measured, main's landed arm MISSes both. Classes 1-4 already HIT there and are armor:
    they pin the classes a future narrowing could re-open, which is the half of the pair
    that keeps the next cut honest. (Re-measured after the `#12149` rebase: unchanged.)

    Classes 4 and 5 are pinned below rather than just remembered, because they are the
    shapes that made "model the shell" the wrong approach: the rule is now CONSERVATIVE —
    `_message_line_is_inert` answers for the WHOLE line and every other line is searched by
    the RAW-line rule the PRE-#12106 scan applied to every line, so the exclusion removes
    detections and adds none (measured: 0 gained on a 60 000-line fuzz corpus). Each
    removal is only as safe as the shape test behind it, so every guard the shape test
    relies on has a HAZARD row here, and the pinned INERT tail proves the false positive
    is still gone.
    """
    scripts = _gh_executing_scripts()
    helper = "install-ci-runner-x64.sh"
    assert helper in scripts, "the derivation no longer sees the helper this pin uses"

    # 1 — a second command after `;`
    assert _gh_hazards(
        [f'            echo "starting"; bash tools/{helper} --repair-labels'], scripts
    )
    # 2 — after `&&`, `||`, `|` and the BARE `&` (background)
    assert _gh_hazards([f'            echo "starting" && bash tools/{helper}'], scripts)
    assert _gh_hazards([f'            echo "starting" || bash tools/{helper}'], scripts)
    assert _gh_hazards([f'            echo "starting" |& bash tools/{helper}'], scripts)
    assert _gh_hazards([f'            echo hi & bash "tools/{helper}"'], scripts)
    assert _gh_hazards([f'            echo hi &> /dev/null; bash "tools/{helper}"'], scripts)
    # 3 — the helper QUOTED inside, or after, a substitution
    assert _gh_hazards(
        [f'            echo $(python3 "${{GITHUB_WORKSPACE}}/tools/{helper}")'], scripts
    )
    assert _gh_hazards([f"            echo $(bash 'tools/{helper}')"], scripts)
    assert _gh_hazards([f'            echo "see $(python3 tools/{helper})"'], scripts)
    assert _gh_hazards([f"            echo $(python3 tools/{helper})"], scripts)
    assert _gh_hazards([f'            echo "$(bash -c "bash tools/{helper}")"'], scripts)
    assert _gh_hazards([f"            echo $((1)) $(bash tools/{helper})"], scripts)
    # 4 — a `)` bash treats as DATA does not close the substitution, so the helper is
    #     inside it. A walker that decrements on any `)` desynced here and blanked it.
    assert _gh_hazards([f"            echo \"$(printf ') '; bash tools/{helper})\""], scripts)
    assert _gh_hazards([f'            echo "$(case $X in a) bash tools/{helper};; esac)"'], scripts)
    # 5 — the message's own text CONSUMED by the next command. Inert to `echo`,
    #     executable to `bash`.
    assert _gh_hazards([f"            echo 'bash tools/{helper}' | bash"], scripts)
    assert _gh_hazards([f"            printf 'bash tools/{helper}\n' | sh"], scripts)
    assert _gh_hazards([f"            echo 'bash tools/{helper}' | bash -s"], scripts)
    # 6 — …or written to a file and run from the next command on the same line.
    assert _gh_hazards([f"            echo 'bash tools/{helper}' > /tmp/x; bash /tmp/x"], scripts)
    # 7 — the helper as the ARGUMENT-EXECUTING end of a pipeline
    assert _gh_hazards([f"            printf x | bash tools/{helper}"], scripts)
    # 8 — the message word is in ARGUMENT position, not command position. `_blank_quoted`
    #     deletes quoted spans WHOLE, so `"bash" "tools/h.sh" echo` strips to ` echo` and
    #     would satisfy the anchored question while bash RUNS `bash tools/h.sh`. Found by
    #     review on this cut; the raw-line anchor guard (`_MESSAGE_WORD`) closes it.
    assert _gh_hazards(['            "bash" "tools/' + helper + '" echo'], scripts)
    assert _gh_hazards(['            "/bin/bash" "tools/' + helper + '" echo'], scripts)
    assert _gh_hazards(["            " + "'" + "tools/" + helper + "'" + " echo"], scripts)

    # 9 — an ESCAPED quote is not a quote OPENER. Bash treats `\"` as a LITERAL quote, so
    #     the `;` after it is a REAL separator; a naive `"[^"]*"` starts a span there and
    #     swallows the separator, leaving a shape that reads as a bare message. Found by
    #     review on this cut — 256 spellings executed the helper while the scan was silent,
    #     and BOTH baselines flagged them, so this was the strongest form of regression.
    #     `_blank_quoted`'s escape-parity walk is the guard.
    assert _gh_hazards([f'            echo>\\" ; bash tools/{helper} ; \\"'], scripts)
    assert _gh_hazards([f'            echo>2\\" ; bash tools/{helper} ; \\"'], scripts)
    assert _gh_hazards([f"            echo>\\' ; bash tools/{helper} ; \\'"], scripts)
    assert _gh_hazards([f'            printf>\\" ; bash tools/{helper} ; \\"'], scripts)
    assert _gh_hazards(
        [f'            echo "run it by hand" > /tmp/log\\" ; bash tools/{helper} ; \\"'], scripts
    )
    assert _gh_hazards([f'            echo -e "note" >&2\\" ; bash tools/{helper} ; \\"'], scripts)

    # 10 — ESCAPE PARITY, and a span that DELETION would have welded to an operator.
    #      `\\'` is an escaped BACKSLASH then a REAL opener (not a lookbehind case), and
    #      `>"x"&` must not become `>&`. Both ran the helper while the scan was silent;
    #      both are flagged by both baselines, so both were regressions.
    assert _gh_hazards([f"            echo>a\\\\'~'; bash 'tools/{helper}'"], scripts)
    assert _gh_hazards([f'            echo>a\\\\"~"; bash "tools/{helper}"'], scripts)
    assert _gh_hazards([f"            echo>\"x\"&bash 'tools/{helper}'"], scripts)
    assert _gh_hazards([f"            echo>\"\"&bash 'tools/{helper}'"], scripts)
    assert _gh_hazards([f"            echo>\";\"&bash 'tools/{helper}'"], scripts)

    # 11 — a SINGLE-QUOTED span has NO escapes, so a backslash before the closing `'` is a
    #      literal character AND the span ends there. Treating that pair as an ESCAPE (the
    #      rule that is right in double quotes and outside spans) kept the walk "inside"
    #      the span to end of line, blanked the invocation and answered INERT — measured,
    #      the helper really runs (marker file created) while the scan reported nothing.
    #      Found by `#12167` review on the rebased head. `#12149`'s walker misses it too,
    #      so it is inherited rather than a regression.
    #      ⛔ WHAT THIS ROW IS AND IS NOT: it is a HAZARD row, NOT a pin for the branch. The
    #      row survives deleting the branch, because the mis-walk then ends INSIDE the span and
    #      the unterminated guard falls back to the raw line, which still names the helper —
    #      fail-closed by a different route. (Before class 11b existed, deleting the branch left
    #      the whole suite green, and that is what the acceptance review caught.) The branch's
    #      own discriminating row is 11b below, and it is what reds when the branch goes.
    #      The probe is BUILT BY CONCATENATION on purpose: as one literal it is a wall of
    #      backslashes where a reader cannot tell a bash escape from a Python one, which is
    #      the very confusion that produced the hole.
    bs = "\\"  # ONE backslash, named so the probe below reads as bash, not as Python
    sq = "'"
    single_quote_escape = (
        f'            echo {sq}a{bs}{sq} {bs}" ; bash tools/{helper} ; true {bs}" # {sq}'
    )
    assert _gh_hazards([single_quote_escape], scripts), single_quote_escape
    # 11b — THE BRANCH'S OWN PIN, in the OVER-REPORT direction. With the branch the span
    #       closes at the `'` after the backslash and the rest is quoted prose → INERT. With
    #       the branch deleted the walk stays "inside" until end of line, the unterminated
    #       guard hands back the RAW line, and this message about a helper REDS a healthy
    #       job — measured: inert here, hazard under the mutation.
    assert _gh_hazards([f'            echo {sq}a{bs}{sq} "bash tools/{helper}"'], scripts) == []
    # 11c — THE UNTERMINATED GUARD'S OWN PIN, in the FAIL-OPEN direction. `_blank_quoted`
    #       returns the line UNCHANGED when a span never closes, so the raw arm still sees
    #       the name. Deleting that `return line` blanks the span to a space, the line
    #       answers INERT, `_code_only` hides the quoted name too, and this hazard vanishes
    #       (measured: hazard here, `[]` under the mutation).
    assert _gh_hazards([f'            echo {sq}bash tools/{helper}{sq} "unterminated'], scripts)

    # 12 — TWO SHAPES THAT DEFEATED THE *BLANKED* TEXT RATHER THAN THE WALK, both found by
    #      `#12167` review with a marker file. Their baseline directions DIFFER, so both are
    #      stated rather than lumped together (measured, three readers):
    #      a) A `#` CONTINUING A WORD IS NOT A COMMENT. bash starts a comment only at the
    #         START of a word, so in `"x"#c` the `#` is data and the `;` after it is a REAL
    #         separator. `_blank_quoted` replaces a closed span with a SPACE, which
    #         manufactured the boundary the old comment rule keyed on and deleted the rest
    #         of an executable line. `_comment_cut` decides the boundary on the RAW line.
    #         DIRECTION: a REGRESSION against BOTH baselines — `origin/main` and
    #         pre-`#12106` both HIT this shape; only the pre-review head answered INERT.
    assert _gh_hazards([f'            echo "x"#c; bash "tools/{helper}"'], scripts)
    assert _gh_hazards([f"            echo 'x'#c && bash 'tools/{helper}'"], scripts)
    assert _gh_hazards([f'            echo ""#c; bash "tools/{helper}"'], scripts)
    #      b) A REDIRECT TARGET HOLDS NO LONE `\`. `\`+newline is a CONTINUATION, so
    #         `echo 'bash h.sh' 2>&1\` followed by `| bash` is ONE command that pipes the
    #         message into bash. A target class that accepted `\` read it as "a file named
    #         `\`", the anchored match succeeded and the line answered INERT.
    #         DIRECTION: an INHERITED MISS on `origin/main` (it misses too) and a REGRESSION
    #         against pre-`#12106`, which HIT it — so it must be closed, not declared.
    assert _gh_hazards([f"            echo 'bash tools/{helper}' 2>&1" + bs], scripts)
    assert _gh_hazards([f"            echo 'bash tools/{helper}' >/tmp/x" + bs], scripts)

    # 13 — A PROCESS SUBSTITUTION IS NOT A "CONSUMED" REDIRECT TARGET. `>( … )` / `<( … )`
    #      RUNS its body, which falsifies the premise the redirect allowance rests on ("a
    #      redirect consumes its argument without executing it"). A target class that
    #      admitted `(`/`)` swallowed the whole substitution, so the line answered INERT —
    #      and an INERT line is searched ONLY by `_code_only`, never by the spliced views, so
    #      a name made non-contiguous there was contiguous nowhere. Measured: 45 spellings ran
    #      the helper with no hit before the parens were excluded (#12167 review, cycle 2).
    assert _gh_hazards([f"            echo > >(bash tools/{helper})"], scripts)
    assert _gh_hazards(
        [f"            printf x &> >(bash tools/install-ci-runner-x64{bs}.sh)"], scripts
    )
    # ⛔ THE ROW THAT ACTUALLY DISCRIMINATES: NO SPACE in the substitution and a name that is
    # contiguous ONLY after unescaping. With the parens excluded the line is searched by the
    # raw + spliced views and the `\.` resolves; with them allowed it is INERT and only
    # `_code_only` is searched, where the name is split by the backslash — measured, this is
    # the difference between a hit and `[]`, and the plain-name rows above do NOT discriminate
    # (the name is contiguous in `_code_only` either way).
    assert _gh_hazards(
        [f"            echo > >(bash${{IFS}}tools/install-ci-runner-x64{bs}.sh)"], scripts
    )
    assert _gh_hazards([f"            echo > >(bash${{IFS}}tools/{helper})"], scripts)
    assert _gh_hazards([f"            echo <(bash tools/{helper})"], scripts)
    # …and the trailing half: the `<(`/`>(` rows that stay INERT are prose ABOUT a
    # substitution, and erasing them would re-open #12133's false block.
    assert _gh_hazards([f'            echo "a <(b: run tools/{helper}) c"'], scripts) == []

    # …and the INERT shapes that motivated the fix stay inert, so it cannot be satisfied
    # by reverting it.
    assert (
        _gh_hazards([f'            echo "run tools/{helper} --repair-labels by hand"'], scripts)
        == []
    )
    assert _gh_hazards([f"            printf 'run tools/{helper} by hand\\n'"], scripts) == []
    assert _gh_hazards([f'            echo "a & b: run tools/{helper} by hand"'], scripts) == []
    assert _gh_hazards([f'            echo "a; b: run tools/{helper} by hand"'], scripts) == []
    # ⛔ `|` AND `||` ARE PINNED TOO, and the omission was measured as a real hole rather than
    # noticed by reading. #12149's final commit added these two rows after a fresh review; the
    # rebase onto that head dropped them, and the drop was INVISIBLE — injecting the plausible
    # narrowing `if "|" in line: return False` into `_message_line_is_inert` left this suite
    # green while the same class of regression reds #12149's. A message whose PROSE carries a
    # pipe is the exact false block #12133 is about, so the rows are load-bearing.
    assert _gh_hazards([f'            echo "a | b: run tools/{helper} by hand"'], scripts) == []
    assert _gh_hazards([f'            echo "a || b: run tools/{helper} by hand"'], scripts) == []
    assert _gh_hazards([f'            echo -e "run tools/{helper}\n"'], scripts) == []
    # …and a trailing REDIRECT is inert, because a redirect consumes its argument without
    # executing it. This is the part of #12157 the conservative rule can close for free.
    assert _gh_hazards([f'            echo "run tools/{helper}" >&2'], scripts) == []
    assert _gh_hazards([f'            echo "run tools/{helper}" > /dev/null'], scripts) == []
    assert _gh_hazards([f'            echo "run tools/{helper}" 2>&1'], scripts) == []
    # …and so is a QUOTED target, which `_blank_quoted` erases to a space — the shape that made
    # `>> "$GITHUB_STEP_SUMMARY"` (14 live uses in this tree, 11 of them in the message +
    # redirect position this sentence is about — `grep -rn 'GITHUB_STEP_SUMMARY'
    # .github/workflows/ | grep '>>'`) read as a hazard while its
    # unquoted twin stayed inert. And a trailing COMMENT is not code.
    assert (
        _gh_hazards([f'            echo "run tools/{helper}" >> "$GITHUB_STEP_SUMMARY"'], scripts)
        == []
    )
    assert _gh_hazards([f'            echo "run tools/{helper}" > "/dev/null"'], scripts) == []
    assert _gh_hazards([f'            echo "run tools/{helper}"  # see #12133'], scripts) == []
    assert _gh_hazards([f"            echo 'run tools/{helper}' # note"], scripts) == []
    # …but a PIPE is never inert: that is a CONSUMER, not a redirect, which is exactly
    # how `echo 'bash h.sh' | bash` runs the helper.
    assert _gh_hazards([f'            echo "run tools/{helper}" | cat'], scripts)
    # …but an UNQUOTED argument is not prose, and an unterminated quote is not bare.
    assert _gh_hazards([f"            echo tools/{helper}"], scripts)
    assert _gh_hazards([f'            echo "run tools/{helper}'], scripts)
    # …and a TRAILING BACKSLASH is not bare either, and that is LOAD-BEARING rather than
    # cosmetic: `\` at end of line is a bash CONTINUATION, so `echo 'bash h.sh' \\` plus a
    # following `| bash` are ONE command that pipes the message into bash. `_blank_quoted`
    # only consumes `\X` when the PAIR is complete, so a lone trailing `\` survives the walk
    # and `_INERT_MESSAGE`'s `\s*$` can never match it. Nothing pinned that until review
    # found it: a "handle line continuations" edit that STRIPS the trailing `\` leaves
    # `echo  `, which IS inert, so the quoted name is hidden, the two-line fail-open lands,
    # and the whole suite stays green (measured: shipped = hazard; stripping the lone `\` =
    # `[]` with 0 of 40 tests red).
    trailing_bs = "\\"  # ONE backslash, named for the same reason as `bs` above
    assert _gh_hazards([f"            echo 'bash tools/{helper}' {trailing_bs}"], scripts)


def test_every_printer_prefixed_shape_that_still_runs_a_command_is_a_hazard():
    """Every way a message EMITTING on a line can still EXECUTE a helper (#12133/#12149).

    The prose fix itself is pinned by `test_an_operator_message_naming_a_helper_is_not_a_gh_hazard`
    and `test_the_message_exclusion_cannot_hide_a_real_invocation` above; this pins the
    SHAPE MATRIX, one assertion per shell mechanism. It came out of #12149's review
    cycles, where each of the first three cuts missed one of these and was caught only
    by writing the shape down:

      * a printer followed by a second command  — `;`, `&&`, `|`, a bare `&`
      * a substitution the printer expands      — `$( … )`, a backtick
      * a PROCESS substitution                  — `<( … )`, `>( … )`
      * a `)` INSIDE a quoted span in a substitution body — it must not be read as
        the substitution's own closer, or the rest of the line is blanked away

    ⛔ CARRIED FROM #12149, RE-MEASURED AGAINST THIS ARM — the two arms reach these rows
    by different means and both must answer the same. #12149 taught its WALKER about
    process substitution; this arm needs no such lesson, because a line carrying `<( … )`
    or `>( … )` fails the anchored "bare message" question and is therefore searched RAW,
    where the quoted helper is visible. Replayed row by row on the rebased head: all
    twelve HIT here and all twelve HIT on #12149's landed walker. The `>(python3 '…')`
    row is the one #12149 closed and the pre-#12149 scan missed; the quoted-`)` row is
    the one THIS arm closed and #12149 also closes.

    ⛔ WHICH ROWS DISCRIMINATE AND WHICH ARE ARMOR — stated, because a pin that would
    pass against an older reader proves nothing about this change. Measured row by row
    against BOTH older readers, and the distinction matters because two different readers
    are easy to confuse:

      * against the TRUE pre-`#12106` reader (`7e3fef06f7`, whose name arm is
        `script_ref.search(line)` — raw, no exclusion at all): **0 rows flip. All twelve
        HIT there.** That is arithmetically required, not a coincidence: this arm's honest
        claim is that the exclusion REMOVES detections and adds none, so it can never beat
        the reader that had no exclusion.
      * against the pre-`#12149` walker (`edcac493a5`): **THREE rows flip** — `<( … )`
        quoted, `>( … )` quoted, and the `$( … )`-body-with-a-quoted-`)` row. These are the
        DISCRIMINATING rows in this matrix.
      * the two `;`-after-a-quoted-`)` rows (`grep -E "xml)"`, `sed -e 's/x)y/z/'`) already
        HIT on `edcac493a5` — ARMOR, not flips (this docstring said "four rows flip"
        including them, and called `edcac493a5` the pre-`#12106` reader; both wrong, found
        by `#12167` review).
      * every `;`/`&&`/`|`/`&`/backtick row was already ARMOR on both.

    Both kinds are kept deliberately: armor is what stops the next cut from re-opening a
    closed class, and the three discriminating rows are what this change closes.

    The `<( … )`/`>( … )` class is why this matrix exists at all: a printer whose helper
    sits QUOTED inside one was read as prose on `edcac493a5` (both spellings), while the
    unquoted spelling was caught — so the hole was exactly the quote-blanking of an
    executable body. Zero live instances exist in the 41 workflows (measured:
    `python-ci.yml:1582` is the only `<(`/`>(` they contain, and it names no helper), and
    `.github/scripts/*.sh` carry 8 more, none naming a gh-executing helper either — which
    is why it needs a pin rather than a red to keep it closed.

    WHY IT DOES NOT WEAKEN THE PROSE FIX: every shape here EXECUTES its helper, and the
    inert shapes (`echo "run tools/h.sh by hand"`, prose carrying `;`, `&`, `|`) stay
    asserted inert in the tests above. Both halves are required — a suite that only pins
    the hazards can be satisfied by reverting the fix, and one that only pins the prose
    can be satisfied by opening this gate.
    """
    scripts = _gh_executing_scripts()
    assert "ci_timing.py" in scripts, "the derivation no longer sees the helper this pin uses"
    for chained in (
        # ── a second command after the printer (armor) ──
        '        run: echo "==> timing" && python3 "$GITHUB_WORKSPACE/tools/ci_timing.py"',
        '        run: echo "==> timing"; bash "tools/install-ci-runner-x64.sh"',
        '        run: printf \'%s\\n\' "$CHANGED" | python3 "$GITHUB_WORKSPACE/tools/ci_timing.py"',
        "        run: echo start & bash 'tools/ci_timing.py'",
        # ── a substitution the printer EXPANDS (armor) ──
        '        run: echo "$(bash tools/ci_timing.py)"',
        '        run: echo "`tools/ci_timing.py`"',
        # ── a PROCESS substitution: body runs, and quoting inside it does not make it
        #    prose (DISCRIMINATING — the pre-#12149 scan misses both quoted spellings) ──
        "        run: echo <(bash 'tools/ci_timing.py')",
        "        run: echo >(python3 'tools/ci_timing.py')",
        "        run: while read -r x; do :; done < <(printf '%s' \"$Y\" | python3 'tools/ci_timing.py')",
        # ── a `(`/`)` INSIDE a quoted span of a substitution body must not be read as
        #    the closer. ARMOR here (this arm never enters substitution mode at all —
        #    the line fails the anchored question, so it is searched raw), and the rows
        #    that pinned #12149's own first regression, where a walker that ended the
        #    substitution at the quoted `)` blanked the real invocation after `;`. ──
        "        run: echo <(grep -E \"xml)\" /etc/hostname) ; bash 'tools/ci_timing.py'",
        "        run: echo <(sed -e 's/x)y/z/' /etc/hostname) ; bash \"tools/ci_timing.py\"",
        # ── …and the same quoted `)` on `$( … )`, which the pre-#12149 scan MISSES as
        #    well (latent before this change): DISCRIMINATING. ──
        '        run: echo "$(bash -c "x)")" ; bash \'tools/ci_timing.py\'',
    ):
        assert _gh_hazards([chained], scripts), chained

    # …and process substitution does NOT run inside double quotes, so a message that
    # merely SPELLS one stays prose — the conservative direction must not be bought by
    # flagging text bash never executes.
    assert (
        _gh_hazards(['        run: echo "a <(b: run tools/install-ci-runner-x64.sh) c"'], scripts)
        == []
    )


#: Nested-substitution prose this arm OVER-REPORTS, on purpose — see the residual block
#: in `_message_line_is_inert`. Kept as data so the residual is measurable rather than
#: asserted-by-absence: `test_the_nested_substitution_residual_is_over_report_not_a_miss`
#: pins BOTH that these still flag here and that they flag for the declared reason.
_NESTED_SUBSTITUTION_PROSE = (
    '        run: echo "dir $(basename "$(pwd)") — repair with tools/install-ci-runner-x64.sh"',
    '        run: echo "$(printf "%s" "$(date)") run tools/ci_timing.py by hand"',
)


def test_the_nested_substitution_residual_is_over_report_not_a_miss():
    """The TWO rows #12149 kept inert and this arm declares as an over-report.

    ⛔ THIS IS A DECLARED RESIDUAL IN THE FAIL-CLOSED DIRECTION, PINNED RATHER THAN
    GLOSSED — both nested-prose lines above are reported as hazards here, and #12149's
    walker read them as prose. The trade is unavoidable and it is the whole content of
    the cut: `_message_line_is_inert` answers `False` for ANY line carrying `$(`, because
    distinguishing `echo "$(bash h.sh)"` (which RUNS the helper) from
    `echo "$(basename "$(pwd)")"` (which cannot) requires parsing the substitution body
    — and every cut that parsed it was falsified by a shape it did not anticipate. So the
    bail is a constant, and this line pays for `echo "$(bash h.sh)"` staying closed.

    WHY IT IS THE ACCEPTABLE DIRECTION: over-reporting asks a healthy job to add a
    contract step, which is visibly repairable; under-reporting leaves an undeclared `gh`
    helper invisible, which is the defect the gate exists for. MEASURED, so neither the
    cost nor the benefit is hypothetical: across all 41 workflows / 92 jobs this arm
    and #12149's landed walker produce ZERO differing verdicts (22 jobs hit under both),
    so there is no live instance of this residual in the tree today — it costs nothing
    here and buys the closed classes above.

    If a live instance ever appears, the repair is a contract step on that job or a
    narrowed substitution reading — NOT dropping the `$(`-anywhere bail, which is what
    re-opens `echo "$(bash h.sh)"`.
    """
    scripts = _gh_executing_scripts()
    # ⛔ THE BAIL IS PINNED DIRECTLY, because the rows below do NOT pin it. Measured: with
    # `if "$(" in line or "`" in line: return False` DELETED, both nested rows still answer
    # HAZARD — their blanked residue (`echo  $(pwd) ` / `echo  %s $(date) `) already fails the
    # anchored question on its own, so an assertion on those rows alone stays green while the
    # bail is gone. The message below blanks to a BARE `echo`, which is inert exactly when the
    # bail is present and only then.
    assert _message_line_is_inert('        run: echo "$(date)"') is False, (
        "the `$(`-anywhere bail is gone: this line blanks to a bare `echo` and would now be "
        'read as prose, re-opening `echo "$(bash h.sh)"`'
    )
    assert _message_line_is_inert('        run: echo "`date`"') is False
    assert _message_line_is_inert('        run: echo "generated at build time"') is True
    for line in _NESTED_SUBSTITUTION_PROSE:
        assert _gh_hazards([line], scripts), (
            "the residual changed direction: this line is now INERT, which means the "
            "`$(`-anywhere bail no longer fires and the substitution-body classes "
            '(`echo "$(bash h.sh)"`) are searched by a rule that must handle them: ' + line
        )
    # …and the reason it fires is the bail, not an accident of the helper's position: a
    # line with the SAME message and no substitution is inert.
    assert (
        _gh_hazards(
            ['        run: echo "dir x — repair with tools/install-ci-runner-x64.sh"'], scripts
        )
        == []
    )


def test_the_unmodelled_word_formation_residuals_are_declared_misses():
    r"""Three shapes that really EXECUTE a helper and are declared, not closed.

    ⛔ PINNED AS MISSES, NOT AS PASSES — if this test ever reds, someone closed one of them
    (good) or broke the same-line case (bad); either way update the STATED RESIDUALS block in
    `_gh_hazards` rather than delete this pin. Each was measured with a stub
    `tools/ci_timing.py` that `touch`es a marker file, executed by `bash -c` in that cwd.

    All three are INHERITED from `origin/main` — none is introduced by this change — and none
    has a live instance in the 41 workflows. They are separate from the same-line `\X`
    escape, which IS caught: only the CROSS-LINE join is out of reach, because
    `_gh_hazards` reads one line at a time.
    """
    scripts = _gh_executing_scripts()
    assert "ci_timing.py" in scripts, "the derivation no longer sees the helper used below"
    # BRACE EXPANSION — a third word-formation rule the two spliced views do not model.
    # (bash expands it before exec, so `tools/ci_timing.p{y,}` names `tools/ci_timing.py`.)
    assert _gh_hazards(["        run: bash tools/ci_timing.p{y,}"], scripts) == []
    assert _gh_hazards(["        run: bash tools/{ci_timing,other}.py"], scripts) == []
    # A BACKSLASH-NEWLINE JOIN ACROSS LINES (`run: |`), which a per-line reader cannot see.
    assert (
        _gh_hazards(
            ["          run: |", "            bash tools/ci_tim\\", "ing.py --refresh"],
            scripts,
        )
        == []
    )
    # …and the SAME-LINE escape IS caught, so the pin above is about the join and not about
    # escapes in general — this row is what would red if the spliced views were removed.
    assert _gh_hazards(["        run: bash tools/ci_tim\\ing.py"], scripts)


def test_a_folded_block_scalar_is_one_command_not_two(tmp_path):
    """`run: >` JOINS its lines, so reading the body line by line invents two commands.

    ⛔ THIS IS THE SAME FAIL-OPEN CLASS AS EVERY OTHER ROW IN THIS FILE, one layer up: the
    defect is not in the message rule but in the SPLITTER. YAML folds a `>` block scalar —
    newline becomes a space, a blank line becomes a newline — so

        - run: >
            echo 'bash tools/h.sh'
            | bash

    is `echo 'bash tools/h.sh' | bash`: ONE command that pipes the message into bash and
    RUNS the helper. Physically, line 1 is an inert message about a file and line 2 names
    no helper, so the scan answered `[]` with the helper running (measured, marker file).
    It is an INHERITED MISS on `origin/main` and a REGRESSION against pre-`#12106`.

    It reds if the composer stops being consulted, and it asserts the FOLDED RENDERING is
    present in the job body — so it cannot pass by the physical lines happening to hit.
    """
    scripts = _gh_executing_scripts()
    helper = "install-ci-runner-x64.sh"
    workflow = tmp_path / "folded.yml"
    workflow.write_text(
        "jobs:\n"
        "  folded:\n"
        "    runs-on: ubuntu-24.04\n"
        "    steps:\n"
        "      - run: >\n"
        f"          echo 'bash tools/{helper}'\n"
        "          | bash\n"
    )
    bodies = dict(_jobs(workflow))
    body = bodies["folded"]
    folded = [line for line in body if "|" in line and "echo" in line]
    assert folded, f"`run: >` was not folded into one command: {body}"
    assert _gh_hazards(body, scripts), body

    # …and a LITERAL block (`run: |`) is NOT folded: its lines really are separate commands,
    # and the shape that is a hazard when folded is inert when not.
    literal = tmp_path / "literal.yml"
    literal.write_text(
        "jobs:\n"
        "  literal:\n"
        "    runs-on: ubuntu-24.04\n"
        "    steps:\n"
        "      - run: |\n"
        f"          echo 'bash tools/{helper}'\n"
        "          | bash\n"
    )
    literal_body = dict(_jobs(literal))["literal"]
    assert not [line for line in literal_body if "|" in line and "echo" in line], (
        "`run: |` must not be folded — its newlines are real"
    )

    # ⛔ THE SPELLINGS YAML ACCEPTS — NOT a table the CODE enumerates, which is the lesson
    # four review passes taught. Each pass closed a hole by naming more spellings and the next
    # spelling beat it: `>2`/`>-2` (indicator), `"run":`/`run :` (key), `&a`/`!!str` (one
    # decoration), `&a !!str` (two), `!<tag:…>` (verbatim form), `>` on the next line (the
    # plainest spelling of all), `>` after a COMMENT line, a bare `!` tag, and the explicit-key
    # `? run` / `: >` form. So `_yaml_run_scalars` asks PyYAML: the composer folds, and this row
    # asserts the ANSWER rather than the pattern. Every spelling below is one the runner
    # really folds into a single executing command — verified against `yaml.safe_load`.
    for header in (
        "run: >",
        "run: >-",
        "run: >+",
        "run: >2",
        "run: >-2",
        "run: >2-",
        "run: >+2",
        "run: >  # keep the newline",
        '"run": >',
        "'run': >",
        "run : >",
        "run: &a >",
        "run: !!str >",
        "run: ! >",
        "run: &a !!str >",
        "run: !!str &a >",
        "run: !<tag:yaml.org,2002:str> >",
    ):
        spelling = tmp_path / "spelling.yml"
        spelling.write_text(
            "jobs:\n"
            "  folded:\n"
            "    runs-on: ubuntu-24.04\n"
            "    steps:\n"
            f"      - {header}\n"
            f"          echo 'bash tools/{helper}'\n"
            "          | bash\n"
        )
        spelled_body = dict(_jobs(spelling))["folded"]
        assert _gh_hazards(spelled_body, scripts), f"`{header}` was not folded: {spelled_body}"

    # ⛔ AND THE SHAPES THAT ARE NOT A HEADER AT ALL — the indicator on its own line, behind a
    # COMMENT line, on a decorated value, and the explicit-key form. Each is the SAME ONE
    # COMMAND to the runner and answered NO HAZARD under the pattern that preceded the
    # composer (#12167 review, cycles 4 and 5).
    for shape in (
        "      - run:\n          >\n",
        "      - run:\n          &a >\n",
        "      - run:\n          !!str >\n",
        "      - run:\n          # how the body folds\n          >\n",
        "      - ? run\n        : >\n",
    ):
        spelled = tmp_path / "shape.yml"
        spelled.write_text(
            "jobs:\n"
            "  folded:\n"
            "    runs-on: ubuntu-24.04\n"
            "    steps:\n"
            f"{shape}"
            f"          echo 'bash tools/{helper}'\n"
            "          | bash\n"
        )
        shaped_body = dict(_jobs(spelled))["folded"]
        assert _gh_hazards(shaped_body, scripts), f"{shape!r} was not folded: {shaped_body}"

    # ⛔ AND THE FOLD MUST REACH ITS OWN JOB, whatever the job id or indentation is spelled
    # like. A cut that enumerated the jobs textually and then looked the composer's answers up
    # BY NAME delivered the fix through the enumeration it replaced: `"bu\u0069ld":` decodes to
    # `build`, so the job was enumerated under one name and its resolved `run:` arrived under
    # the other, and a folded step that really ran a helper landed on NO job — a REGRESSION the
    # parent cut did not have. Bodies are sliced by node position now, so the two cannot
    # disagree (#12167 review, pass 5).
    for header, child_indent in (
        ('  "bu\\u0069ld":', "    "),
        ("  build:", "      "),  # 4-space children: block indentation is relative
        ("  'build' :", "    "),  # a space before the colon
        ("  build:  # the job", "    "),
    ):
        renamed = tmp_path / "renamed.yml"
        renamed.write_text(
            "jobs:\n"
            f"{header}\n"
            f"{child_indent}runs-on: ubuntu-24.04\n"
            f"{child_indent}steps:\n"
            f"{child_indent}  - run: >\n"
            f"{child_indent}      echo 'bash tools/{helper}'\n"
            f"{child_indent}      | bash\n"
        )
        renamed_jobs = _jobs(renamed)
        assert len(renamed_jobs) == 1, (
            f"{header!r} enumerated {len(renamed_jobs)} jobs: {renamed_jobs}"
        )
        # ⛔ THE BODY MUST BE THE JOB'S OWN LINES, not just the appended fold. Measured: replacing
        # the node slice with a lookup in the textual reader (`body = textual.get(name, [])`) left
        # the whole suite GREEN while the body collapsed to the single folded line — and because
        # `_poolable_label("")` is False, the COVERAGE arm then silently SKIPPED the job, which is
        # the very R2/R3 fail-open this cut exists to close (#12167 review, pass 6).
        renamed_body = renamed_jobs[0][1]
        assert any("runs-on:" in line for line in renamed_body), (
            f"the body for {header!r} lost the job's own lines: {renamed_body}"
        )
        assert _poolable_label("\n".join(renamed_body)), (
            f"the body for {header!r} stopped reading as a poolable job, so the coverage arm "
            f"would skip it: {renamed_body}"
        )
        assert _gh_hazards(renamed_body, scripts), (
            f"the fold did not reach the job spelled {header!r}: {renamed_jobs[0]}"
        )

    # …and a PLAIN multi-line scalar, which is not a block scalar at all: YAML joins its lines
    # exactly like a `>`, so it is the same hazard with no indicator in sight. `origin/main`
    # misses it, which makes it inherited — and the composer closes it for free.
    plain = tmp_path / "plain.yml"
    plain.write_text(
        "jobs:\n"
        "  folded:\n"
        "    runs-on: ubuntu-24.04\n"
        "    steps:\n"
        f"      - run: echo 'bash tools/{helper}'\n"
        "          | bash\n"
    )
    plain_body = dict(_jobs(plain))["folded"]
    assert _gh_hazards(plain_body, scripts), (
        f"a plain multi-line scalar was not joined: {plain_body}"
    )

    # ⛔ AND A DUPLICATE KEY RESOLVES THE WAY `safe_load` RESOLVES IT — LAST wins. YAML 1.1/1.2
    # call a duplicate key invalid, but a reader may accept it, and this module's own oracle is
    # `safe_load`: a first-wins lookup here resolved a key `safe_load` never returns, so a folded
    # `run:` that bash really executes was read as its innocent FIRST duplicate and reported
    # nothing — two readers of one file disagreeing, a divergence no assertion could see
    # (#12167 review, pass 6).
    duplicate = tmp_path / "duplicate.yml"
    duplicate.write_text(
        "jobs:\n"
        "  folded:\n"
        "    runs-on: ubuntu-24.04\n"
        "    steps:\n"
        "      - run: echo harmless\n"
        "    steps:\n"
        "      - run: >\n"
        f"          echo 'bash tools/{helper}'\n"
        "          | bash\n"
    )
    duplicate_body = dict(_jobs(duplicate))["folded"]
    assert yaml.safe_load(duplicate.read_text())["jobs"]["folded"]["steps"][0]["run"].startswith(
        "echo 'bash"
    ), "this fixture no longer exercises last-wins"
    assert _gh_hazards(duplicate_body, scripts), (
        f"a duplicate `steps:` key resolved first-wins, against `safe_load`: {duplicate_body}"
    )


def test_the_cross_line_redirect_residual_is_a_declared_miss():
    """The one FAIL-OPEN shape this arm declares instead of closing (#12167 review).

    ⛔ PINNED AS A MISS, NOT AS A PASS — if this test ever reds, someone closed the shape
    (or broke the same-line case) and must update the second residual block in
    `_message_line_is_inert` rather than delete this pin.

    The reader answers for ONE LINE and a job body is a list of them, so a message whose
    text is written to a file and EXECUTED by a later line comes back clean while bash
    really runs the helper — measured with a marker file. The same-line version is a
    HAZARD and is pinned in `test_the_message_exclusion_cannot_hide_a_real_invocation`
    (class 6), so only the cross-line sliver is open, and it is inherited from
    `origin/main`'s walker, not introduced here.

    Why it is declared rather than closed: the cheap rule that would catch it — treat a
    redirect on an otherwise-inert message as non-inert — also flags `echo "… helper …" >&2`,
    the shape that reddened `test-carve-out` on every PR (#12133) and that this arm pins
    INERT in three rows. Swapping a live false red for a cross-line shape with no live
    instance is the wrong trade; body-level dataflow is #12157's widening, not this cut's.
    """
    scripts = _gh_executing_scripts()
    helper = "install-ci-runner-x64.sh"
    # the cross-line shape: DECLARED MISS (this assertion is the declaration)
    body = [
        "          run: |",
        f"            echo 'bash tools/{helper}' > /tmp/gen_probe.sh",
        "            bash /tmp/gen_probe.sh",
    ]
    assert _gh_hazards(body, scripts) == [], (
        "a cross-line fix landed: update the residual paragraph"
    )
    # …and the SAME-LINE shape stays a hazard, which is the half that IS closed.
    assert _gh_hazards([f"            echo 'bash tools/{helper}' > /tmp/x; bash /tmp/x"], scripts)
    # …and the redirect-on-prose rows stay INERT, which is why the cheap rule was refused.
    assert _gh_hazards([f'            echo "run tools/{helper}" >&2'], scripts) == []


def test_a_script_named_only_in_an_operator_message_is_not_a_gh_hazard():
    """The false positive that red `test-carve-out (c)` on every PR (#12133).

    `python-ci.yml::test-record-postgres` named `tools/install-ci-runner-x64.sh` in its
    `::error::` REMEDY text — an operator instruction, never an invocation. The name arm
    reads the RAW command (a real invocation is usually quoted), so the job was reported
    as a `gh` user and the remedy the scan demanded (`ensure gh`, a step that fails closed
    on a no-egress pool runner) would have reddened a healthy job.

    ⛔ THE LIVE ROW IS GONE, so this no longer reads one (#12188). The pin used to derive
    the row from the job's own body and assert it still NAMED the helper, so it could not
    go vacuous if the remedy were reworded. `9c9dcdd3cc` (`#12117`) then moved the PG client
    to PGDG, which deleted that `::error::` branch — correctly, the failure it described no
    longer exists — and took the row with it: the job keeps only a `#` comment naming the
    helper, which the scan and this pin both ignore. The assert fired as designed and
    `main` went red on the shard, which is what #12188 fixes.

    So the shape is pinned as a READER CASE now, never as a copy of live tree text: a
    live-text pin is a cross-lane collision surface — the pin's author cannot see an
    in-flight edit to the text it pins, and that editor cannot see the pin. ⛔ A live prose
    row DOES still exist, so a live pin is refused for the COLLISION, not for want of one:
    a probe at `1f28d107cf` found 33 non-comment lines naming one of the 49 gh-executing
    helpers — 30 the arm flags and 3 `echo` prose it ignores (`finding-provenance.yml` ×2
    and `mergify-guard-recut.yml::recut`'s `::error::`, which is the SAME prose-only shape
    as the row below), and one of the 30 is an `on.push.paths` filter entry rather than an
    invocation. Pinning any of them re-creates the coupling that red `main` here. The
    `::error::` line below is the shape #12133 red on.

    ⛔ THE OVERLAP WITH `test_an_operator_message_naming_a_helper_…` IS DELIBERATE, and
    the split is stated so a later de-duplication pass does not delete coverage: that
    sibling pins the same prose shape for the #12133 false positive (and carries the
    block-scalar spelling), while the rows UNIQUE to this test are the inert two-argument
    `printf` spelling (`printf '%s\\n' 'see …'`) and the bare-`echo` residual hazard —
    neither appears there. What is also unique here is the subject: this pin is the one
    that READ the live job body, so its docstring is where #12188's red and the reason a
    live-text pin is refused are recorded.
    """
    scripts = _gh_executing_scripts()
    # ANTI-VACUITY: the PROSE cases below pass trivially the moment this name stops being a
    # KNOWN gh-executing helper, because then the name arm has nothing to match. ⛔ This
    # assert is NOT what stops the test going vacuous — the two positive controls below
    # collapse to `[]` on their own and red, measured (they are load-bearing; do not drop
    # one). What this adds is a failure that NAMES the cause (the helper deleted, or it stops
    # calling `gh`) instead of surfacing on a positive row — and, unlike a live-text pin, it
    # cannot be invalidated by another lane rewording prose it does not own.
    assert "install-ci-runner-x64.sh" in scripts, (
        "the derivation no longer sees the helper this pin uses"
    )
    # #12133's exact red: the `::error::` operator message that named the helper.
    assert (
        _gh_hazards(
            [
                "        run: echo \"::error::no postgresql-client-16 in this image's archive — "
                "SELF-HOSTED runner: run CI_RUNNER_OS_LABEL=off "
                'tools/install-ci-runner-x64.sh --repair-labels --count <N>"'
            ],
            scripts,
        )
        == []
    )
    assert (
        _gh_hazards(["        run: printf '%s\\n' 'see tools/install-ci-runner-x64.sh'"], scripts)
        == []
    )
    # …a launcher's quoted ARGUMENT is still code…
    assert _gh_hazards(
        ['        run: bash "$GITHUB_WORKSPACE/tools/install-ci-runner-x64.sh"'], scripts
    )
    # …and an unquoted name after a bare `echo` still counts (conservative residual).
    assert _gh_hazards(["        run: echo run tools/install-ci-runner-x64.sh"], scripts)


def test_the_contract_step_reader_cannot_be_defused_by_prose():
    """The ordering pin's step-reader must find the REAL step, not a line that names it.

    ⛔ THE LOOSE FORM WAS A SILENT OFF SWITCH. `contract_at = next(… if CONTRACT_STEP in line)`
    accepted ANY line — including a `run:` that merely PRINTS the remedy — and this repo's
    step bodies deliberately print such remedies (the whole #12133/#12149 subject). With a
    decoy advice line first, `contract_at` pointed at it, the pre-contract loop `break`s
    immediately, and the ordering check disabled itself while the final assert still matched
    because its pattern was greedy (`^        run: .*<CONTRACT_STEP>`).

    Measured on the shape below before the fix: `contract_at` -> the advice line, `gh` calls
    the loop would have seen -> `[]`, and the pin PASSED although a real `gh pr view` sat
    between the decoy and the real contract step. No live `GH_JOBS` entry has more than one
    occurrence of the string, which is exactly why nothing red when it was wrong.
    """
    lines = [
        "      - name: advise",
        '        run: echo "if this 404s, run runner-tools.sh ensure gh by hand"',
        "      - name: peek",
        "        run: gh pr view 1 --json number",
        "      - name: contract",
        f"        run: bash .github/scripts/{CONTRACT_STEP}",
    ]
    scripts = _gh_executing_scripts()
    # ⛔ DRIVEN THROUGH `_contract_violations` — THE FUNCTION THE LIVE JOBS USE — not the
    # regex in isolation. Pinning the regex alone was measurably vacuous: the pre-contract
    # arm replaced by `early = []`, and the loose `CONTRACT_STEP in line` reader, each left
    # the whole suite green (#12167 review). `_contract_step_index`/`_contract_step_problems`
    # were this lane's first attempt at that pin; round 6 extracted the same arm as
    # `_contract_violations`, which is the better factoring, so the pin rides THAT.
    problems = _contract_violations("\n".join(lines), scripts)
    assert problems and "BEFORE the contract step" in problems[0], (
        "the ordering arm reports nothing for a job with a real `gh pr view` ahead of the "
        f"contract step — it is vacuous: {problems}"
    )
    # …and a compliant block reports nothing, so the arm cannot pass by reporting everything.
    assert (
        _contract_violations(
            "\n".join(
                [
                    "      - name: contract",
                    f"        run: bash .github/scripts/{CONTRACT_STEP}",
                    "      - name: peek",
                    "        run: gh pr view 1 --json number",
                ]
            ),
            scripts,
        )
        == []
    )
    # …and a job that runs NO contract step is a problem rather than an empty answer:
    # silently answering `[]` there is how a missing step reads as a clean job.
    missing = _contract_violations("        run: gh pr view 1", scripts)
    assert missing and CONTRACT_STEP in missing[0], missing
    # ⛔ AND IT IS DERIVED, not a text list. The old list named 5 of the 49 helpers derivable
    # at that head (51 at this one — `_SCRIPT_GLOBS` gained `operations/orchestrator-harness/`)
    # and 8 of `_GH_CMD`'s subcommands, so a job could put `check-required-set.py` or `gh secret`
    # ahead of the contract step and this pin stayed green. Both are pre-contract hazards now.
    for shape in (
        "        run: python3 .github/scripts/check-required-set.py",
        "        run: gh secret list",
        "        run: gh repo view --json name",
        "        run: python3 tools/ci_timing.py --refresh-durations",
    ):
        assert _gh_hazards([shape], _gh_executing_scripts()), shape


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
    and it is the largest job in the tree (28 steps; next largest is 19), so it is the
    one whose dropping-out this rule exists to notice. Dropping it out of this scan is
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
