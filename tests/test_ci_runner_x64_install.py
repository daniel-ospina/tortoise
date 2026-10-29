"""The x86_64 runner installer's invariants: it sizes against measured headroom, it never lets a
runner claim an architecture (or a hosted version pin) that is not its own, and its exit codes
distinguish "retry smaller" from "the install broke".

WHY BEHAVIOURAL, NOT TEXT-SCRAPED
    A guard that greps the script for a string passes when the string moves and fails when the
    code is merely reformatted (the class agent-infra#1426 records: semantics-preserving edits win
    against text readers). So every invariant is exercised by RUNNING the installer:
      * the capacity guard, through `--dry-run` (which computes every headroom bound and stops
        before any network, token, download, or registration);
      * the label invariant, through `--print-labels`, which calls the SAME `resolve_labels` the
        install path feeds to `config.sh`;
      * the fallback read, through `--repair-labels` against a stub `gh` (the read path that
        decides whether the fallback label is attached at all);
      * the exit contract, through the refusals themselves.
    ⛔ NO TEST HERE MAY SKIP, and none may depend on the host it runs on:
      * the label tests used to skip whenever the headroom guard refused — a false PASS on a
        loaded host. `--print-labels` now runs BEFORE the arch and headroom guards, so asking
        "which labels would a runner get" cannot be gated by this box's state;
      * they also used to hard-FAIL on any non-x86_64 host (`--print-labels` exited 2 at the arch
        guard), which is this fleet's Apple-Silicon box and the five ARM64 pool runners;
      * the cpu test used to depend on disk and fork headroom, because the guards run
        forks → disk → cpu and it asserted the cpu message. Every bound is now env-overridable and
        the test bounds the other legs out of the way, so it is a pure function of the arithmetic.
    A missing installer is a COLLECTION ERROR (`raise` at import), never three skips: this file
    pins that installer's behaviour, and a green suite with zero assertions is the failure mode
    the file exists to prevent.

WHAT IS BEING PINNED
  1. `--count N` is refused (exit 2) when N runners would eat the cores the agent lanes need; the
     refusal names the arithmetic. This box is a CI runner AND an agent host, so capacity that
     starves the lanes is not capacity (#5215).
  2. The labels are `self-hosted-x64` plus `ubuntu-latest` ONLY when the fallback is engaged:
     `self-hosted-arm64` would let an x86 box serve an arm64-specific job (a false PASS, not a
     delay), and `ubuntu-<version>` is the version-pinned HOSTED label a leg needs when the pool
     cannot carry it. The fallback label is never permanent — it follows the gate's state
     (`--fallback on|off|auto`), and `auto` must NOT silently equal `on`.
  3. `auto` READS the pool's state (per page, summed) and refuses (exit 2) when it cannot, rather
     than guessing which pool to join.
  4. A dry run decides nothing and calls nothing: no token, no download, no label read.
  5. `COUNT` is validated for EVERY mode, so `--count 0 --uninstall` cannot report success having
     deregistered nothing — and `--uninstall` deletes only a directory it can PROVE is a runner
     install (`.runner` / `bin/Runner.Listener`; a lone `config.sh` is explicitly NOT proof — it is
     a common filename and a bare script that exits 0 passes `./config.sh remove`), never just
     "whatever `BASE` names".
  5b. `--uninstall` removes the systemd unit BEFORE the directory, and a unit it cannot remove
     stops that target dead: the directory and its registration are LEFT INTACT (exit 3), never
     deleted out from under an enabled unit. Every removal token is minted before the FIRST
     deletion, so a mint failure leaves every runner untouched rather than a half-uninstalled set.
  6. An unreadable bound is refused, never coerced — and `ulimit -u` = `unlimited` is a READ
     bound (infinity), not an unreadable one.
  7. The exit contract holds: 1 usage, 2 guard refused, 3 operation failed.
  8. No job name, no `runs-on:`, and no required check is touched by this change. That is a
     property of the PR diff (tools/, docs/ops/, docs/00_index.md), which no test of this file can
     see — the diff is the artifact.
  9. The arch refusal is an **INSTALL** guard, in the code and in the runbook: `--uninstall` (like
     `--status`, `--repair-labels` and `--print-labels`) runs on a host of either architecture, so
     a runner can be decommissioned from the box it was installed on.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALLER = REPO_ROOT / "tools" / "install-ci-runner-x64.sh"
BASH = shutil.which("bash") or "/bin/bash"

if not INSTALLER.exists():  # pragma: no cover - a broken checkout must not read as green
    raise AssertionError(
        f"installer not found at {INSTALLER}: this file pins THAT installer's behaviour, so a "
        "missing installer is a FAIL — a module-level skip would report three skips and exit 0 "
        "with zero assertions run"
    )

# Every headroom leg EXCEPT the one under test, satisfied. The guards run forks → disk → cpu, so
# without this the disk bound can refuse first and the cpu assertion fails on a message that never
# mentions cpu (that is what a GitHub-hosted ubuntu-latest does: ~14G free on `/`, where /opt is).
_NEUTRAL_HEADROOM = {
    "CI_RUNNER_FORKS_PER_RUNNER": "0",
    "CI_RUNNER_FORK_CAP": "1000000",
    "CI_RUNNER_DISK_PER_RUNNER_GB": "0",
    "CI_RUNNER_DISK_RESERVE_GB": "0",
    "CI_RUNNER_MEM_PER_RUNNER_GB": "0",
    "CI_RUNNER_MEM_RESERVE_GB": "0",
    # ⛔ THIS FILE CALLS ITSELF HERMETIC AND WAS NOT. It starts from `dict(os.environ)`, and this PR
    # adds a variable that CHANGES THE INSTALLER'S ANSWER — so a box (or CI job) with
    # `CI_RUNNER_OS_LABEL=on|auto` exported reded the label tests for a reason unrelated to the code
    # under test. The default arm is pinned here; each arm then sets what it means.
    "CI_RUNNER_OS_LABEL": "off",
}


def _env(base: Path, **extra: str) -> dict[str, str]:
    """A hermetic installer environment: local base dir, neutral headroom, no host state."""
    env = dict(os.environ)
    env.update(_NEUTRAL_HEADROOM)
    env.update({"CI_RUNNER_REPO": "daniel-ospina/tortoise", "CI_RUNNER_BASE": str(base)})
    env.update(extra)
    return env


def _run(
    *args: str, env: dict[str, str] | None = None, cwd: Path | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [BASH, str(INSTALLER), *args],
        capture_output=True,
        text=True,
        env=env or _env(Path("/nonexistent-base")),
        # `--status`' label-coverage read is a read OF THE CWD (it parses `.github/workflows`), so a
        # case that exercises it must say where it stands.
        cwd=str(cwd) if cwd else None,
    )


# ── 1. the label invariant ───────────────────────────────────────────────────────────────────
def test_labels_are_this_archs_own_and_follow_the_fallback(tmp_path: Path):
    """`--fallback on` adds ONLY the gate's fallback label; `off` leaves the arch marker alone."""
    on = _run("--print-labels", "--fallback", "on", env=_env(tmp_path))
    off = _run("--print-labels", "--fallback", "off", env=_env(tmp_path))
    auto = _run("--print-labels", env=_env(tmp_path))
    for r, why in ((on, "--fallback on"), (off, "--fallback off"), (auto, "auto")):
        assert r.returncode == 0, f"{why}: {r.stderr}"
        assert r.stdout.strip(), f"{why} printed no label set: {r.stderr}"

    assert on.stdout.strip() == "self-hosted-x64,ubuntu-latest"
    assert off.stdout.strip() == "self-hosted-x64"
    # `auto` decides at install from the pool's state; it must never silently equal `on`
    assert auto.stdout.strip() == "self-hosted-x64", (
        "auto must not attach the fallback label without reading the gate's state"
    )

    for r in (on, off, auto):
        for alien in ("self-hosted-arm64", "ARM64", "ubuntu-24.04", "ubuntu-22.04"):
            assert alien not in r.stdout, f"installer would hand config.sh {alien!r}"


def test_labels_answer_regardless_of_arch_but_install_refuses_a_foreign_one(tmp_path: Path):
    """The label question depends on no host property; the INSTALL guard still refuses a foreign arch.

    Both directions are exercised on every host through the arch seam, so neither a Linux x86_64
    runner nor an Apple-Silicon dev box can disagree: `--print-labels` must answer on aarch64 (it
    used to exit 2 there, reddening the suite on the very hardware the fallback uses), and
    `--dry-run` must still refuse to install.
    """
    labels = _run("--print-labels", "--fallback", "on", env=_env(tmp_path, CI_RUNNER_ARCH="aarch64"))
    assert labels.returncode == 0, f"--print-labels must not be gated by the arch guard: {labels.stderr}"
    assert labels.stdout.strip() == "self-hosted-x64,ubuntu-latest"

    refused = _run("--count", "1", "--dry-run", env=_env(tmp_path, CI_RUNNER_ARCH="aarch64", CI_RUNNER_NCPU="16"))
    assert refused.returncode == 2, f"a non-x86_64 install must be refused: {refused.stderr}"
    assert "REFUSED" in refused.stderr and "x86_64" in refused.stderr, refused.stderr

    allowed = _run("--count", "1", "--dry-run", env=_env(tmp_path, CI_RUNNER_ARCH="x86_64", CI_RUNNER_NCPU="16"))
    assert allowed.returncode == 0, allowed.stderr


# ── 2. the capacity guard, as arithmetic ────────────────────────────────────────────────────
def test_count_is_refused_when_it_would_starve_the_agent_lanes(tmp_path: Path):
    """4 runners on 8 cores = 4x2 cores + 2 reserved = 10 > 8 → REFUSED, naming the arithmetic.

    The core count is pinned, so this is a pure function of the guard's own arithmetic on every
    host (8 cores is this fleet's box; 16 stands in for a larger one) — and the second probe proves
    the refusal is not a hardcoded `--count` ceiling.
    """
    r = _run(
        "--count", "4", "--dry-run",
        env=_env(tmp_path, CI_RUNNER_NCPU="8", CI_RUNNER_ARCH="x86_64"),
    )
    assert r.returncode == 2, f"count=4 on 8 cores must be refused: {r.stderr}"
    assert "REFUSED" in r.stderr and "cpu headroom" in r.stderr, r.stderr
    assert "4x2" in r.stderr and "this box has 8" in r.stderr, (
        f"the refusal must name the arithmetic so an operator can re-size: {r.stderr}"
    )

    ok = _run(
        "--count", "4", "--dry-run",
        env=_env(tmp_path, CI_RUNNER_NCPU="16", CI_RUNNER_ARCH="x86_64"),
    )
    assert ok.returncode == 0, f"count=4 on 16 cores must be allowed: {ok.stderr}"


# ── 3. the fallback read (`auto`), against a stub gh ────────────────────────────────────────
def _stub_gh(tmp_path: Path, stdout: str, rc: int = 0) -> Path:
    """A `gh` that answers the runner-list query and nothing else, logging every call."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    log = tmp_path / "gh-calls.log"
    gh = bin_dir / "gh"
    gh.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> {log}\n'
        f"cat <<'EOF'\n{stdout}\nEOF\n"
        f"exit {rc}\n"
    )
    gh.chmod(0o755)
    return bin_dir


def test_auto_reads_the_pools_state_and_refuses_when_it_cannot(tmp_path: Path):
    """`--fallback auto` is a READ of the ARM64 pool's current state, per page and summed.

    This is the path the install actually takes, and it had no coverage at all: `--print-labels`
    forces the `auto` short-circuit, so hardcoding the read to `on` or `off` kept the labels test
    green. `--repair-labels` calls the same `resolve_labels` WITHOUT the short-circuit, so it
    reaches `fallback_engaged` — the one place the gate's state is consulted.
    """
    engaged = _stub_gh(tmp_path / "engaged", "1")
    r = _run("--repair-labels", "--count", "1", env=_env(tmp_path, PATH=f"{engaged}:{os.environ['PATH']}"))
    assert r.returncode == 0, r.stderr
    assert "ENGAGED" in r.stderr and "labels=self-hosted-x64,ubuntu-latest" in r.stderr, r.stderr

    # two pages, one engaged (the multi-page shape the gate itself got wrong): the counts are SUMMED
    paged = _stub_gh(tmp_path / "paged", "0\n2")
    r = _run("--repair-labels", "--count", "1", env=_env(tmp_path, PATH=f"{paged}:{os.environ['PATH']}"))
    assert r.returncode == 0, r.stderr
    assert "ENGAGED" in r.stderr, f"a per-page count must be summed, not read line-by-line: {r.stderr}"

    idle = _stub_gh(tmp_path / "idle", "0")
    r = _run("--repair-labels", "--count", "1", env=_env(tmp_path, PATH=f"{idle}:{os.environ['PATH']}"))
    assert r.returncode == 0, r.stderr
    assert "not engaged" in r.stderr and "labels=self-hosted-x64" in r.stderr, r.stderr

    # unreadable is exit 2 (fail closed) — never "off", which would starve CI on a guess
    broken = _stub_gh(tmp_path / "broken", "gh: Not Found (HTTP 404)", rc=1)
    r = _run("--repair-labels", "--count", "1", env=_env(tmp_path, PATH=f"{broken}:{os.environ['PATH']}"))
    assert r.returncode == 2, f"an unreadable fallback state must be refused: {r.returncode} {r.stderr}"
    assert "REFUSED" in r.stderr and "refusing to guess" in r.stderr, r.stderr


def test_repair_dry_run_plans_the_body_the_real_run_puts(tmp_path: Path):
    """`--repair-labels --dry-run` must show the label body the repair would actually PUT.

    `resolve_labels`' `auto` branch short-circuits on `DRY=1` so an INSTALL plan works without `gh`.
    `--repair-labels` never installs and prints its resolved set as the concrete request body
    (`dry-run: would PUT <body> …`), so that short-circuit made the plan LIE: on an engaged pool the
    dry run showed `{self-hosted-x64}` while the real run sent `{self-hosted-x64,ubuntu-latest}` —
    and the runbook sells the repair as "sync the fallback label to the gate's state". Repair mode
    calls `need gh` before `resolve_labels`, so reading the state in a dry run costs nothing.

    Non-vacuity: with the short-circuit still keyed on `DRY` alone, the first assertion reds (no
    "ENGAGED", and the planned body lacks `ubuntu-latest`).
    """
    engaged = _stub_gh(tmp_path / "engaged", "1")
    # `_env(tmp_path)` makes tmp_path the runner base, so instance 1 lives in it
    (tmp_path / ".runner").write_text('{"agentName": "pi-runner-x64-1"}\n')
    env = _env(tmp_path, PATH=f"{engaged}:{os.environ['PATH']}", CI_RUNNER_ARCH="x86_64")

    plan = _run("--repair-labels", "--count", "1", "--dry-run", env=env)
    assert plan.returncode == 0, plan.stderr
    assert "ENGAGED" in plan.stderr, (
        f"a repair plan must read the gate's state, not defer it to an install that never happens: {plan.stderr}"
    )
    planned = re.search(r"would PUT (\{.*\}) to runner", plan.stderr)
    assert planned, plan.stderr

    done = _run("--repair-labels", "--count", "1", env=env)
    assert done.returncode == 0, done.stderr
    assert planned.group(1) == '{"labels":["self-hosted-x64","ubuntu-latest"]}', (
        f"the plan must name the body the repair would PUT on an engaged pool: {planned.group(1)}"
    )
    assert "self-hosted-x64,ubuntu-latest" in done.stderr, (
        f"the real run sends that same set: {done.stderr}"
    )


# ── 3b. the OS-release label: the deadlock escape, opt-in and honest about declining ────────
def test_the_os_label_is_opt_in_and_no_fallback_arm_can_drop_it(tmp_path: Path):
    """`CI_RUNNER_OS_LABEL=on` adds `ubuntu-24.04` on EVERY `--fallback` arm; the default adds none.

    The default arm is the #6897 decision (`test_labels_are_this_archs_own_and_follow_the_fallback`
    pins it as the shipped behaviour). What is pinned HERE is the other half nobody executed: the
    opt-in, and the invariant that makes it an OS FACT rather than a fallback state — `--fallback
    off` must not be able to drop it, because a runner that drops it leaves `workflow-lint` and
    `test-slow (a–e)` unschedulable and `python-ci-gate` is never dispatched (every PR BLOCKED).

    Mutation: append `$OS_LABELS` inside the `off` arm only -> the third case lacks it -> RED.
    Mutation: return early from `resolve_labels` after the `--fallback` case -> RED.
    """
    on = _run("--print-labels", "--fallback", "on", env=_env(tmp_path, CI_RUNNER_OS_LABEL="on"))
    assert on.returncode == 0, on.stderr
    assert on.stdout.strip() == "self-hosted-x64,ubuntu-latest,ubuntu-24.04", on.stdout

    # ⛔ THE ONE INVARIANT THE FEATURE RESTS ON: the OS fact rides every arm.
    off = _run("--print-labels", "--fallback", "off", env=_env(tmp_path, CI_RUNNER_OS_LABEL="on"))
    assert off.returncode == 0, off.stderr
    assert off.stdout.strip() == "self-hosted-x64,ubuntu-24.04", (
        f"`--fallback off` must not drop the OS fact: {off.stdout}"
    )

    # The default is `off`: the shipped behaviour still keeps the #6897 pin.
    default = _run("--print-labels", "--fallback", "off", env=_env(tmp_path))
    assert "ubuntu-24.04" not in default.stdout, default.stdout

    # ⛔ AND THE DEFAULT IS THE ONE WITH THE VARIABLE ABSENT — not merely the one where it says
    # `off`. `_env` now sets `CI_RUNNER_OS_LABEL=off` explicitly (the suite is hermetic), so without
    # this case the suite could only prove that `off` behaves, and a flip of the DEFAULT would pass
    # every assertion here. This is the #6897 pin itself: a fresh box must claim nothing.
    unset = _env(tmp_path)
    unset.pop("CI_RUNNER_OS_LABEL", None)
    for args in (("--print-labels", "--fallback", "off"), ("--print-labels", "--fallback", "on")):
        r = _run(*args, env=unset)
        assert r.returncode == 0, r.stderr
        assert "ubuntu-24.04" not in r.stdout, (
            f"the DEFAULT flipped: a box with no CI_RUNNER_OS_LABEL exported claimed the "
            f"version-pinned label ({args}): {r.stdout}"
        )


def test_auto_claims_the_os_label_only_when_the_host_reports_that_release(tmp_path: Path):
    """`auto` derives the label from the HOST, and SAYS SO when it declines.

    `auto` used to decline in total silence: a 22.04 box, a container with no `/etc/os-release`, or
    a macOS lane got a label set without `ubuntu-24.04` and no signal — indistinguishable from
    "opted out", on the one property (the host's release) that decides whether the escape applies.
    The recommended spelling must not be the quiet one. Both reads are seams (`CI_RUNNER_OS_NAME`,
    `CI_RUNNER_OS_RELEASE`) for the same reason `CI_RUNNER_ARCH` is: the box that reads this code is
    not the box being guarded.

    Mutation: return 0 silently in the declined arms -> the DECLINED assertions red -> RED.
    Mutation: drop the `VERSION_ID` comparison -> the 22.04 case claims the label -> RED.
    """
    rel24 = tmp_path / "os-release-24"
    rel24.write_text('NAME="Ubuntu"\nVERSION_ID="24.04"\n')
    rel22 = tmp_path / "os-release-22"
    rel22.write_text('NAME="Ubuntu"\nVERSION_ID="22.04"\n')

    derived = _run(
        "--print-labels",
        env=_env(
            tmp_path,
            CI_RUNNER_OS_LABEL="auto",
            CI_RUNNER_OS_NAME="Linux",
            CI_RUNNER_OS_RELEASE=str(rel24),
        ),
    )
    assert derived.returncode == 0, derived.stderr
    assert derived.stdout.strip() == "self-hosted-x64,ubuntu-24.04", (
        f"auto on a 24.04 box must claim the OS fact: {derived.stdout}"
    )
    assert "DECLINED" not in derived.stderr, derived.stderr

    wrong_release = _run(
        "--print-labels",
        env=_env(
            tmp_path,
            CI_RUNNER_OS_LABEL="auto",
            CI_RUNNER_OS_NAME="Linux",
            CI_RUNNER_OS_RELEASE=str(rel22),
        ),
    )
    assert wrong_release.returncode == 0, wrong_release.stderr
    assert wrong_release.stdout.strip() == "self-hosted-x64", wrong_release.stdout
    assert "DECLINED" in wrong_release.stderr and "$OS_LABEL" not in wrong_release.stderr, (
        "a declining `auto` must say why, not return 0 in silence: " f"{wrong_release.stderr}"
    )
    assert "22.04" in wrong_release.stderr, wrong_release.stderr

    not_linux = _run(
        "--print-labels",
        env=_env(tmp_path, CI_RUNNER_OS_LABEL="auto", CI_RUNNER_OS_NAME="Darwin"),
    )
    assert not_linux.returncode == 0, not_linux.stderr
    assert not_linux.stdout.strip() == "self-hosted-x64", not_linux.stdout
    assert "DECLINED" in not_linux.stderr and "not Linux" in not_linux.stderr, not_linux.stderr


def test_an_unrecognised_os_label_value_is_refused_in_every_mode(tmp_path: Path):
    """A typo is a USAGE error (exit 1) in every mode — including `--status`, where it is looked for.

    The refusal used to live inside `resolve_os_label`, which only install / repair / --print-labels
    reach. `--status` never calls it, so `CI_RUNNER_OS_LABEL=true --status` exited 0 with no
    complaint — and `--status` is exactly the mode an operator runs AFTER setting the flag to
    VERIFY the escape is applied. A fail-open in the one place the answer is read. The check is now
    hoisted beside `--count`, so all four modes refuse before doing anything.

    Mutation: move the `case` back inside `resolve_os_label` -> `--status` exits 0/3, not 1 -> RED.
    """
    for args in (
        ("--print-labels",),
        ("--status",),
        ("--uninstall",),
        ("--repair-labels",),
        ("--count", "1", "--dry-run"),
    ):
        r = _run(*args, env=_env(tmp_path, CI_RUNNER_OS_LABEL="true"))
        assert r.returncode == 1, f"{args} must refuse a typo as a usage error: {r.returncode} {r.stderr}"
        assert "CI_RUNNER_OS_LABEL must be off|on|auto" in r.stderr, (args, r.stderr)


def test_the_log_names_the_label_set_that_is_actually_registered(tmp_path: Path):
    """The `fallback:` line must print the FINAL set — the one `config.sh` gets, OS label included.

    `$OS_LABELS` is appended at the END of `resolve_labels`, and the two `say` lines printed
    `$LABELS` before that — so one run logged `labels=self-hosted-x64,ubuntu-latest` and registered
    `…,ubuntu-24.04` four lines later, and the runbook quotes the log line as recorded output. An
    operator reading the log for the mitigation saw it absent and concluded the flag had not applied.

    Mutation: revert either `say` to `$LABELS` alone -> the assertion reds -> RED.
    """
    engaged = _stub_gh(tmp_path / "engaged", "1")
    (tmp_path / ".runner").write_text('{"agentName": "pi-runner-x64-1"}\n')
    env = _env(
        tmp_path,
        PATH=f"{engaged}:{os.environ['PATH']}",
        CI_RUNNER_ARCH="x86_64",
        CI_RUNNER_OS_LABEL="on",
    )
    r = _run("--repair-labels", "--count", "1", "--dry-run", env=env)
    assert r.returncode == 0, r.stderr
    logged = re.search(r"labels=(\S+)", r.stderr)
    assert logged, r.stderr
    assert "ubuntu-24.04" in logged.group(1), (
        f"the logged label set is not the one that would be registered: {r.stderr}"
    )


# ── 3c. label-coverage: the instrument that must not lie in either direction ────────────────
def _repo_with_workflows(tmp_path: Path, body: str, suffix: str = ".yml") -> Path:
    repo = tmp_path / "repo"
    wf = repo / ".github" / "workflows"
    wf.mkdir(parents=True, exist_ok=True)
    (wf / f"w{suffix}").write_text(body)
    return repo


def test_label_coverage_distinguishes_did_not_look_from_looked_and_found(tmp_path: Path):
    """Exit 4 for an EMPTY READ, 0 when the read happened, 5 when a label has NO answer.

    Two separate predicates, neither implied by the other: the directory may be missing (no read at
    all), or it may exist and yield zero labels (a `uses:`-only workflow). Exit 0 used to cover BOTH
    "every label covered" and "a required label has no runner", so `--status && …` could not tell
    the state that blocks every PR from the one that does not — a verdict, and it is bad, is exit 5.

    Mutation: drop either exit-4 arm -> the corresponding case exits 0 -> RED.
    Mutation: return 0 on `uncovered` -> the 5 case reds -> RED.
    """
    bindir = _stub_gh(tmp_path / "gh", "r1\tself-hosted-x64,ubuntu-latest")

    no_dir = tmp_path / "no-dir"
    no_dir.mkdir()
    r = _run("--status", env=_env(tmp_path, PATH=f"{bindir}:{os.environ['PATH']}"), cwd=no_dir)
    assert r.returncode == 4, (r.returncode, r.stderr)
    assert "read NOTHING" in r.stderr, r.stderr

    # `.yaml` is as real as `.yml` (GitHub accepts both): a `uses:`-only caller yields zero labels.
    uses_only = _repo_with_workflows(
        tmp_path, "jobs:\n  call:\n    uses: org/repo/.github/workflows/x.yml@main\n", suffix=".yaml"
    )
    r = _run("--status", env=_env(tmp_path, PATH=f"{bindir}:{os.environ['PATH']}"), cwd=uses_only)
    assert r.returncode == 4, (r.returncode, r.stderr)
    assert "found NO runs-on label" in r.stderr, r.stderr

    # A covered label: the read happened, the pool answers it. Exit 0.
    covered = _repo_with_workflows(
        tmp_path,
        "jobs:\n"
        "  a:\n    runs-on: ubuntu-latest\n    steps: []\n"
        # ⛔ A COMMENT IS NOT A `runs-on:` LINE. Counted, this printed a false ⛔ UNCOVERED for a
        # label no workflow asks for (and this tree's own comments name real labels).
        "  # legacy: runs-on: windows-2022\n"
        "  b:\n    runs-on: ubuntu-latest  # note: runs-on: macos-14\n    steps: []\n",
        suffix=".yaml",
    )
    r = _run("--status", env=_env(tmp_path, PATH=f"{bindir}:{os.environ['PATH']}"), cwd=covered)
    assert r.returncode == 0, (r.returncode, r.stderr)
    assert "ubuntu-latest = 1 of 1" in r.stderr, r.stderr
    assert "windows-2022" not in r.stderr and "macos-14" not in r.stderr, (
        f"comment prose was counted as a required label: {r.stderr}"
    )

    # The incident state: a REQUIRED label the pool IS ASKED to serve and nobody answers. A verdict,
    # and it is bad -> 5. ⛔ `CI_RUNNER_OS_LABEL=on` IS PART OF THE FIXTURE: `--status` reads the
    # operator's intent (the same `resolve_os_label` the install path uses) before it verdicts a bare
    # pin, because with the flag at its shipped default `off` a zero-answer `ubuntu-24.04` cannot be
    # NAMED — it is the pin OR erosion to zero, and one shell cannot tell them apart. See the
    # flag-off control below.
    uncovered = _repo_with_workflows(tmp_path / "u", "jobs:\n  a:\n    runs-on: ubuntu-24.04\n    steps: []\n")
    r = _run(
        "--status",
        env=_env(tmp_path, PATH=f"{bindir}:{os.environ['PATH']}", CI_RUNNER_OS_LABEL="on"),
        cwd=uncovered,
    )
    assert r.returncode == 5, (r.returncode, r.stderr)
    assert "0 of 1 self-hosted runner(s)" in r.stderr, r.stderr
    # ⛔ THE REMEDY MUST NAME ITS PRECONDITION: `--repair-labels` PUTs a WHOLE set, so the bare
    # command (variable unset ⇒ `off`) removes the mitigation while the operator means to fix it.
    assert "CI_RUNNER_OS_LABEL=on" in r.stderr and "REMOVES the mitigation" in r.stderr, r.stderr
    # ⛔ AND THE REMEDY MUST NOT PRESCRIBE A SIDE OF AN OPEN DECISION. Reaching exit 5 means the pool
    # is ALREADY asked to serve the label, so the command repeats the operator's own spelling and
    # never re-derives `on` (which would silently widen an `auto` install).
    assert "This pool IS asked to serve them" in r.stderr, r.stderr

    # ⛔ THE MIRROR — AND THE FALSE CLEAR THIS FIX REMOVES. With `CI_RUNNER_OS_LABEL` at its shipped
    # default (`off`, and `_env` pins that), a pool that answers NO `ubuntu-24.04` used to be
    # reported as **"that is the #6897 pin applied correctly, NOT the incident state"**, exit 0 —
    # and the runbook tells the operator to verify with the BARE `--status`, where the variable is
    # unset. But `n == 0` cannot distinguish "never asked" (the pin, nothing to do) from "asked and
    # gone" (erosion to zero — the incident, and the exact event the `⚠️ PARTIAL` arm exists to see
    # coming one fill at a time). Naming the harmless reading was a fail-open in the BLOCKING
    # direction: the default invocation cleared precisely the erosion this block exists to surface.
    # The honest output is the measurement plus the ambiguity, and never an assertion of one side.
    r = _run("--status", env=_env(tmp_path, PATH=f"{bindir}:{os.environ['PATH']}"), cwd=uncovered)
    assert r.returncode == 0, (
        f"the pinned configuration (#6897, flag off) is not the incident state: {r.stderr}"
    )
    assert "0 of 1 self-hosted runner(s)" in r.stderr, r.stderr
    assert "#6897 pin applied correctly" in r.stderr, r.stderr
    assert "⛔ UNCOVERED" not in r.stderr, r.stderr
    # Mutation (the pre-fix code): print only the harmless reading -> RED, both below.
    assert "never asked" in r.stderr and "asked and gone" in r.stderr, (
        f"a zero answer under an unset flag must name BOTH readings, not assert the pin: {r.stderr}"
    )
    assert "NOT the incident state" not in r.stderr, (
        f"a shell's flag cannot rule out erosion to zero — that assertion is the fail-open: {r.stderr}"
    )
    # The verdict is still ONE read away, and the report says so without claiming it applied anything.
    assert "CI_RUNNER_OS_LABEL=on" in r.stderr and "read-only" in r.stderr, r.stderr

    # ⛔ AND THE INVOCATION THE RUNBOOK ACTUALLY DOCUMENTS IS THE ONE WITH THE VARIABLE ABSENT, not
    # the one where it says `off`. `_env` pins `off` explicitly (the suite is hermetic), so without
    # this case the suite proves only that `off` behaves — and `off`/absent is exactly the pair the
    # shipped default lives in. Same assertion: the ambiguity, never the pin asserted as the state.
    bare = _env(tmp_path, PATH=f"{bindir}:{os.environ['PATH']}")
    bare.pop("CI_RUNNER_OS_LABEL", None)
    r = _run("--status", env=bare, cwd=uncovered)
    assert r.returncode == 0, r.stderr
    assert "never asked" in r.stderr and "NOT the incident state" not in r.stderr, (
        f"the bare `--status` the runbook tells an operator to verify with must not name the pin "
        f"as the state: {r.stderr}"
    )

    # ⛔ AN EMPTY POOL IS NOT ONE RUNNER. `awk` counts RECORDS, and an empty pool feeds it exactly
    # one empty record, so the denominator was `total=1` and an empty pool reported
    # `0 of 1 self-hosted runner(s)` — a fabricated runner, in the block whose doctrine is "an empty
    # read is not a coverage verdict". The count the script already had (`grep -c .` → 0) is now the
    # denominator it prints. Mutation: stop passing `pool_total` to the awk -> `0 of 1` -> RED.
    empty_pool = _stub_gh(tmp_path / "empty", "")
    r = _run(
        "--status",
        env=_env(tmp_path, PATH=f"{empty_pool}:{os.environ['PATH']}", CI_RUNNER_OS_LABEL="on"),
        cwd=_repo_with_workflows(tmp_path / "e", "jobs:\n  a:\n    runs-on: ubuntu-24.04\n    steps: []\n"),
    )
    assert r.returncode == 5, (r.returncode, r.stderr)
    assert "0 of 0 self-hosted runner(s)" in r.stderr, (
        f"an empty pool must report 0 runners, not a fabricated one: {r.stderr}"
    )
    # CONTROL — the same empty pool with the flag OFF is a report, so the 5 above is caused by the
    # intent read and not by the empty pool (the two arms are independent predicates).
    r = _run(
        "--status",
        env=_env(tmp_path, PATH=f"{empty_pool}:{os.environ['PATH']}"),
        cwd=_repo_with_workflows(tmp_path / "e2", "jobs:\n  a:\n    runs-on: ubuntu-24.04\n    steps: []\n"),
    )
    assert r.returncode == 0, (r.returncode, r.stderr)
    assert "0 of 0 self-hosted runner(s)" in r.stderr, r.stderr


def test_label_coverage_reports_partial_coverage_rather_than_green(tmp_path: Path):
    """`0 < n < pool_total` is the erosion state: a label half the pool cannot answer is WARNED.

    The scheduled autoscaler ADDS runners, and its FILL leg registers them through
    `install-ci-runner-x64.sh` — which attaches `ubuntu-24.04` only under `CI_RUNNER_OS_LABEL`. A
    pool that drifts to `ubuntu-24.04 = 3` still printed a clean "covered" line, so the label could
    reach zero answers one fill at a time with this instrument green at every step and the return of
    the ALL-PR-BLOCKED state landing with no red in between.

    Mutation: `elif [ "$n" -lt "$pool_total" ]` -> `elif false` -> "PARTIAL" absent -> RED.
    """
    bindir = _stub_gh(tmp_path / "gh", "r1\tself-hosted-x64,ubuntu-latest\nr2\tself-hosted-x64")
    repo = _repo_with_workflows(tmp_path, "jobs:\n  a:\n    runs-on: ubuntu-latest\n    steps: []\n")
    r = _run("--status", env=_env(tmp_path, PATH=f"{bindir}:{os.environ['PATH']}"), cwd=repo)
    assert r.returncode == 0, (r.returncode, r.stderr)
    assert "ubuntu-latest = 1 of 2 runner(s)" in r.stderr, r.stderr
    assert "PARTIAL" in r.stderr, (
        f"a label only half the pool answers must not read as plain coverage: {r.stderr}"
    )
    # ⛔ AND THE REMEDY IS CLASS-CORRECT, because one message for all classes prescribed a fix that
    # cannot apply. `ubuntu-latest` is the GATE's label: `scripts/ci_runner_gate.sh` attaches and
    # detaches it, and this installer never registers it — so telling an operator to "re-apply with
    # CI_RUNNER_OS_LABEL=on --repair-labels" during a fallback transition sends them to a command
    # that does not change this label at all. Only the bare `ubuntu-<version>` pin is re-applied by
    # this script, so only that class may carry the instruction.
    assert "nothing to re-apply here" in r.stderr, (
        f"a gate-owned label must not be given this script's re-apply instruction: {r.stderr}"
    )
    assert "CI_RUNNER_OS_LABEL=on --repair-labels" not in r.stderr, (
        f"the repair instruction cannot apply to a label the gate owns: {r.stderr}"
    )

    # CONTROL — the bare pin, half-covered, is the class where the instruction IS correct (a fill or
    # re-registration that omitted it is exactly what erodes coverage).
    half = _stub_gh(tmp_path / "gh-half", "x1\tself-hosted-x64,ubuntu-24.04\nx2\tself-hosted-x64")
    pin = _repo_with_workflows(
        tmp_path / "pin", "jobs:\n  a:\n    runs-on: ubuntu-24.04\n    steps: []\n"
    )
    r = _run(
        "--status",
        env=_env(tmp_path, PATH=f"{half}:{os.environ['PATH']}", CI_RUNNER_OS_LABEL="on"),
        cwd=pin,
    )
    assert r.returncode == 0, (r.returncode, r.stderr)
    assert "ubuntu-24.04 = 1 of 2 runner(s)" in r.stderr, r.stderr
    assert "CI_RUNNER_OS_LABEL=on --repair-labels" in r.stderr, (
        f"a half-covered bare pin IS this script's to re-apply: {r.stderr}"
    )

    # ⛔ AND THE SAME ARM WITH THE FLAG OFF MUST NOT INVERT THE REMEDY — the fix for finding 1's (b),
    # and the destructive half. This branch is reached only when `0 < n < denom`, i.e. it has just
    # MEASURED runners carrying the label. A runner's labels are explicitly assigned (GitHub REST:
    # `Add custom labels` / `Set custom labels` / `Remove all custom labels`; registration labels
    # come from `config.sh --labels`), so a carrier is proof the pool WAS asked — "this pool is not
    # asked to serve it" was asserted on a line reporting carriers, and the remedy it printed
    # (`--repair-labels` with the variable UNSET) PUTs a WHOLE set: following it removes the label
    # from the LAST carriers and lands the pool at `0 of 12`, which the zero arm then reports as
    # intentional. Both readings must be stated and the destructive command refused.
    r = _run("--status", env=_env(tmp_path, PATH=f"{half}:{os.environ['PATH']}"), cwd=pin)
    assert r.returncode == 0, (r.returncode, r.stderr)
    assert "CI_RUNNER_OS_LABEL=on --repair-labels" not in r.stderr, (
        f"the remedy must not re-apply a departure the operator has switched off: {r.stderr}"
    )
    # Mutation (the pre-fix code): assert the leftover reading and prescribe the revert -> both RED.
    assert "leftover from a previous state" not in r.stderr, (
        f"an unset flag cannot make a MEASURED carrier set a leftover — both readings must be "
        f"stated: {r.stderr}"
    )
    assert "proof the pool WAS asked" in r.stderr, (
        f"a carrier must outvote a shell variable, and the line must say so: {r.stderr}"
    )
    assert "Do NOT settle it by accident" in r.stderr, (
        f"the destructive instruction must be refused out loud, not printed as the fix: {r.stderr}"
    )

    # ⛔ AND THE NUMERATOR IS COUNTED IN THE SAME POPULATION AS THE DENOMINATOR — the arm that
    # reads EROSION was defeated by its own arithmetic. The denominator was fixed to the sub-pool
    # (the ARM64 runners must never claim the bare `ubuntu-<version>`), but the numerator still
    # counted EVERY carrier in the pool — including the arm64 runners whose answer the denominator
    # deliberately does not count. Measured pre-fix on this exact fixture: `ubuntu-24.04 = 8 of 7
    # runner(s)` with NO `⚠️ PARTIAL` and exit 0, because `n < denom` is false once `denom - n_real`
    # foreign carriers exist. So the one read that sees the x64 sub-pool eroding before its last
    # carrier goes printed a clean row — a ratio that cannot be true, reported as coverage — and the
    # scheduled autoscaler could walk that sub-pool to zero with this instrument green at every
    # step. The arm64 carrier is not "coverage" on the instrument's own terms either: per the
    # published label→arch mapping a bare `ubuntu-<version>` IS the x64 image, so an arm64 runner
    # answering it is the false architecture claim `_preserve_labels` now drops on re-registration.
    # Mutation: count `hit` for the whole pool (use `n` as the numerator) -> `8 of 7`, no PARTIAL,
    # and the two assertions below are RED.
    foreign = _stub_gh(
        tmp_path / "gh-foreign",
        "b1\tself-hosted-x64,ubuntu-24.04\n"
        "b2\tself-hosted-x64,ubuntu-24.04\n"
        "b3\tself-hosted-x64,ubuntu-24.04\n"
        "b4\tself-hosted-x64\n"
        "b5\tself-hosted-x64\n"
        "b6\tself-hosted-x64\n"
        "b7\tself-hosted-x64\n"
        "a1\tself-hosted-arm64,ubuntu-24.04\n"
        "a2\tself-hosted-arm64,ubuntu-24.04\n"
        "a3\tself-hosted-arm64,ubuntu-24.04\n"
        "a4\tself-hosted-arm64,ubuntu-24.04\n"
        "a5\tself-hosted-arm64,ubuntu-24.04",
    )
    r = _run(
        "--status",
        env=_env(tmp_path, PATH=f"{foreign}:{os.environ['PATH']}", CI_RUNNER_OS_LABEL="on"),
        cwd=pin,
    )
    assert r.returncode == 0, (r.returncode, r.stderr)
    assert "ubuntu-24.04 = 3 of 7 runner(s)" in r.stderr, (
        "a foreign-arch carrier must not be counted as an answer for the x64 sub-pool "
        f"(pre-fix this printed `8 of 7`): {r.stderr}"
    )
    assert "PARTIAL" in r.stderr, (
        f"4 of the 7 runners that must answer the bare pin cannot, so a clean row is a fail-open: {r.stderr}"
    )
    assert "8 of 7" not in r.stderr, (
        f"a ratio above 1 means the numerator and denominator came from different populations: {r.stderr}"
    )

    # ⛔ AND THE MIRROR: `ubuntu-latest` with NO carrier is the HEALTHY state, not a block. The gate
    # detaches that label from the pool whenever GitHub-hosted is healthy, so a zero-answer
    # `ubuntu-latest` verdict would make `--status` — and any `--status && …` guard — permanently red
    # in normal operation: the false red this instrument must never produce. The denominator is the
    # runners that COULD answer (x64 pool for a bare version label), so a two-arch pool is not read
    # as erosion either.
    healthy = _stub_gh(tmp_path / "gh2", "x1\tself-hosted,Linux,X64,self-hosted-x64,ubuntu-24.04")
    repo2 = _repo_with_workflows(
        tmp_path / "h", "jobs:\n  a:\n    runs-on: ubuntu-latest\n    steps: []\n"
    )
    r = _run("--status", env=_env(tmp_path, PATH=f"{healthy}:{os.environ['PATH']}"), cwd=repo2)
    assert r.returncode == 0, (r.returncode, r.stderr)
    assert "report, not a block" in r.stderr, r.stderr


def test_label_coverage_does_not_verdict_a_custom_label_that_starts_ubuntu_digits(tmp_path: Path):
    """A CUSTOM label is not the bare-version class — in the verdict OR the denominator.

    The block held TWO definitions of "a bare `ubuntu-<version>` pin" eight lines apart: the
    denominator matched `^ubuntu-[0-9][0-9.]*$` (so `ubuntu-2204-tools` was an ordinary custom
    label counted against the whole pool) while the verdict arm used the `case` glob
    `ubuntu-[0-9]*`, which matches anything after the digits. A zero-carrier custom label therefore
    exited `5` and was told *"python-ci-gate is never dispatched and every PR stays BLOCKED"* — a
    false red, and a false all-PRs-blocked claim, from the one instrument whose own comment says it
    must never produce one. `tools/runner_autoscale.py:284-287` states the boundary the verdict arm
    was missing ("a custom label that merely starts `ubuntu-<digits>` … is NOT in the class"), so
    the two files disagreed about it.

    Mutation: `case "$label" in ubuntu-[0-9]*)` -> this case exits 5 and names the gate -> RED.
    Control: the bare pin in the same shape still exits 5, so the fix is not "never verdict".
    """
    pool = _stub_gh(tmp_path / "gh", "x1\tself-hosted,Linux,X64,self-hosted-x64,ubuntu-latest")
    custom = _repo_with_workflows(
        tmp_path / "custom", "jobs:\n  a:\n    runs-on: ubuntu-2204-tools\n    steps: []\n"
    )
    r = _run("--status", env=_env(tmp_path, PATH=f"{pool}:{os.environ['PATH']}"), cwd=custom)
    assert r.returncode == 0, (
        f"a custom label with no carrier is a report, not the incident state: {r.stderr}"
    )
    # The denominator is the WHOLE pool for a custom label (not the x64 sub-pool), so the two
    # arms of the block agreeing is visible in the printed line.
    assert "ubuntu-2204-tools = 0 of 1 self-hosted runner(s)" in r.stderr, r.stderr
    assert "every PR stays BLOCKED" not in r.stderr, (
        f"a custom label must not be told it blocks every PR: {r.stderr}"
    )
    assert "⛔ UNCOVERED" not in r.stderr, r.stderr

    # CONTROL — the class the verdict exists for. Same pool, same shape, bare pin at zero — with the
    # pool ASKED to serve it (`CI_RUNNER_OS_LABEL=on`); with the flag off the same fixture is a
    # report, because a zero answer cannot be told apart from the pin applied correctly.
    bare = _repo_with_workflows(
        tmp_path / "bare", "jobs:\n  a:\n    runs-on: ubuntu-24.04\n    steps: []\n"
    )
    r = _run(
        "--status",
        env=_env(tmp_path, PATH=f"{pool}:{os.environ['PATH']}", CI_RUNNER_OS_LABEL="on"),
        cwd=bare,
    )
    assert r.returncode == 5, (r.returncode, r.stderr)
    assert "every PR BLOCKED" in r.stderr, r.stderr


# ── 3a-bis. the intent is PER LABEL, not per block ─────────────────────────────────────────
def test_label_coverage_verdicts_only_the_label_this_script_can_register(tmp_path: Path):
    """A bare pin the installer CANNOT emit must never be verdicted, whatever the flag says.

    `resolve_os_label` can only ever set `$OS_LABEL` (`ubuntu-24.04`), but the awk's `bare` class is
    ANY `^ubuntu-[0-9][0-9.]*$`. One intent flag was therefore applied to the whole class: with
    `CI_RUNNER_OS_LABEL=on`, a workflow pinning `ubuntu-22.04` at zero took the verdict arm, exited
    `5`, and printed the remedy `CI_RUNNER_OS_LABEL=on --repair-labels` — a command that CANNOT
    apply `ubuntu-22.04`, because the installer only ever emits `$OS_LABEL`. That is a permanent red
    with an unactionable remedy: the false-red class this block refuses to create, and the mirror of
    `tools/runner_autoscale.py:284-287` ("a custom label that merely starts `ubuntu-<digits>` … is
    NOT in the class") in the other direction.

    Mutation: `if [ "$shell_asks_os_label" = 1 ]` without the `$label = $OS_LABEL` conjunct -> this
    case exits 5 -> RED.
    Control: the SAME pool and flag with `ubuntu-24.04` still exits 5, so the fix is not "the flag
    stopped working".
    """
    pool = _stub_gh(tmp_path / "gh", "x1\tself-hosted-x64,ubuntu-24.04")
    empty = _stub_gh(tmp_path / "gh-empty", "x1\tself-hosted-x64,ubuntu-latest")
    other = _repo_with_workflows(
        tmp_path / "other", "jobs:\n  a:\n    runs-on: ubuntu-22.04\n    steps: []\n"
    )
    r = _run(
        "--status",
        env=_env(tmp_path, PATH=f"{pool}:{os.environ['PATH']}", CI_RUNNER_OS_LABEL="on"),
        cwd=other,
    )
    assert r.returncode == 0, (
        f"a bare pin this script cannot register is a report, never a permanent red with a remedy "
        f"that cannot apply: {r.returncode} {r.stderr}"
    )
    assert "ubuntu-22.04 = 0 of 1 self-hosted runner(s)" in r.stderr, r.stderr
    assert "nothing here re-applies ubuntu-22.04" in r.stderr, r.stderr
    assert "every PR BLOCKED" not in r.stderr, (
        f"a label this pool cannot be asked to serve must not be told it blocks every PR: {r.stderr}"
    )
    assert "--repair-labels" not in r.stderr, (
        f"the unactionable remedy must not be printed either: {r.stderr}"
    )

    # CONTROL — the label the flag actually governs, same flag, a pool with no carrier for it.
    ours = _repo_with_workflows(
        tmp_path / "ours", "jobs:\n  a:\n    runs-on: ubuntu-24.04\n    steps: []\n"
    )
    r = _run(
        "--status",
        env=_env(tmp_path, PATH=f"{empty}:{os.environ['PATH']}", CI_RUNNER_OS_LABEL="on"),
        cwd=ours,
    )
    assert r.returncode == 5, (r.returncode, r.stderr)
    assert "every PR BLOCKED" in r.stderr, r.stderr


# ── 3b. every DOCUMENTED `runs-on` form must be read, or refused ────────────────────────────
def test_label_coverage_reads_every_documented_runs_on_form(tmp_path: Path):
    """A required label in ANY documented `runs-on` spelling must be counted — or refused, never cleared.

    GitHub documents `runs-on` as a single string, an **array of strings**, a variable, and a
    `key: value` pair using the `group` or `labels` keys. The extractor this replaces required the
    value on the SAME line and unquoted DOUBLE quotes only, so three of those four forms were
    cleared to `exit 0` on the incident state itself:

    * `runs-on: ['ubuntu-24.04']` kept its single quotes, matched no class arm, fell to the `*)`
      report arm ("not a label this pool serves") and left `uncovered=0`;
    * a BLOCK sequence and a `group:`/`labels:` mapping yielded NOTHING at all, so the label was
      never counted and the read looked covered.

    Each fixture carries a second, COVERED label on purpose: that makes the read PARTIAL, which is
    the shape neither exit-4 arm could see (the read was not empty, so `labels_seen` never hit 0).

    Mutation (measured against the pre-fix script): restore the three-grep pipeline -> the four
    spellings below exit 0 instead of 5 -> RED.
    """
    # The pool answers `ubuntu-latest` and nothing else: the required pin has NO self-hosted answer.
    bindir = _stub_gh(tmp_path / "gh", "r1\tself-hosted-x64,ubuntu-latest")
    forms = {
        "block sequence": (
            "jobs:\n  a:\n    runs-on:\n      - self-hosted-x64\n      - ubuntu-24.04\n"
            "  b:\n    runs-on: ubuntu-latest\n    steps: []\n"
        ),
        "single-quoted flow sequence": (
            "jobs:\n  a:\n    runs-on: ['ubuntu-24.04']\n"
            "  b:\n    runs-on: ubuntu-latest\n    steps: []\n"
        ),
        "labels: mapping, block list": (
            "jobs:\n  a:\n    runs-on:\n      group: pool\n      labels:\n        - ubuntu-24.04\n"
            "  b:\n    runs-on: ubuntu-latest\n    steps: []\n"
        ),
        "labels: mapping, inline flow": (
            "jobs:\n  a:\n    runs-on: {group: pool, labels: [ubuntu-24.04]}\n"
            "  b:\n    runs-on: ubuntu-latest\n    steps: []\n"
        ),
    }
    for i, (name, body) in enumerate(forms.items()):
        repo = _repo_with_workflows(tmp_path / f"form{i}", body)
        r = _run(
            "--status",
            env=_env(tmp_path, PATH=f"{bindir}:{os.environ['PATH']}", CI_RUNNER_OS_LABEL="on"),
            cwd=repo,
        )
        assert r.returncode == 5, (
            f"{name}: a required label written this way has NO self-hosted answer, so this is the "
            f"incident state and must exit 5, not {r.returncode}: {r.stderr}"
        )
        assert "ubuntu-24.04 = 0 of 1 self-hosted runner(s)" in r.stderr, f"{name}: {r.stderr}"
        assert "every PR BLOCKED" in r.stderr, f"{name}: {r.stderr}"

    # ⛔ AND A SPELLING NOBODY PARSED IS `exit 4`, NOT `0`. A matrix expression has no static label
    # set to verdict, so the honest answer is "no verdict" — the fail-closed direction every other
    # arm of this block already chose. The covered `ubuntu-latest` here is what makes this a PARTIAL
    # read: without the backstop it would sail through as coverage.
    expr = _repo_with_workflows(
        tmp_path / "expr",
        "jobs:\n  a:\n    runs-on: ${{ matrix.os }}\n"
        "  b:\n    runs-on: ubuntu-latest\n    steps: []\n",
    )
    r = _run("--status", env=_env(tmp_path, PATH=f"{bindir}:{os.environ['PATH']}"), cwd=expr)
    assert r.returncode == 4, (
        f"a `runs-on` whose labels are not a static label set must refuse, not clear: {r.stderr}"
    )
    assert "STATIC label set" in r.stderr, r.stderr

    # CONTROL — `group:` alone names a runner GROUP, not a label: it must be consumed, never counted
    # (counting `pool` would print a false UNCOVERED for a "label" no runner is supposed to carry).
    grp = _repo_with_workflows(
        tmp_path / "grp",
        "jobs:\n  a:\n    runs-on:\n      group: pool\n"
        "  b:\n    runs-on: ubuntu-latest\n    steps: []\n",
    )
    r = _run("--status", env=_env(tmp_path, PATH=f"{bindir}:{os.environ['PATH']}"), cwd=grp)
    assert r.returncode == 0, (r.returncode, r.stderr)
    assert "pool" not in r.stderr, f"a runner GROUP was counted as a label: {r.stderr}"


# ── 3b-ii. ONE UNREADABLE MEMBER REFUSES THE BLOCK ────────────────────────────────────────
def test_label_coverage_refuses_a_block_with_any_unreadable_member(tmp_path: Path):
    r"""A parseable sibling must NOT absorb an unparsed member — the block has no static label set.

    The extractor dropped an unreadable member silently (`if (v ~ /^\$/) return`, no marker) and the
    only marker that reached the `exit 4` arm was printed when the block yielded NOTHING. So a
    `runs-on:` cleared as long as ANY part of it parsed:

    * `group: pool` + `labels: ${{ … }}` — no label emitted, and the `group:` suppressed the marker
      (`got == 0 && !pgroup`), so the block vanished entirely;
    * `runs-on: [self-hosted, "${{ matrix.arch }}"]` — `self-hosted` emitted, the expression member
      dropped, so the block read as covered.

    Both are DOCUMENTED shapes (GitHub's own example of the combined form is `group: ubuntu-runners`
    / `labels: ubuntu-24.04-16core`, and a matrix-ified workflow's first move is to put the OS into
    the label list), so this is not an exotic spelling: it is the ordinary way a required leg gets
    routed, and the instrument meant to prove coverage reported `covered` and exited 0 — clearing
    the incident state this whole block exists to surface.

    Mutation: `if (v ~ /^\$/) return` -> no `bad++` -> every fixture below exits 0/5, never 4 -> RED.
    Mutation: `finish()` back to `got == 0 && !pgroup` -> the `group:`/`labels:` fixture clears -> RED.
    Mutation: the refusal back to a PREFIX test (`/^\$/`) -> the four CONTENT fixtures below (an
    expression INSIDE a label, a YAML alias, a space-bearing scalar) are emitted as literal labels,
    no `!` is printed, and `--status` exits 0 with fabricated `= 0 of N … a report` rows -> RED.
    Mutation: drop `|| v ~ /^[>|]/` -> the two BLOCK-SCALAR fixtures below print the INDICATOR as a
    label (`⛔ >- = 0 of 1 … a report, not a block`) and exit 0 over the incident state -> RED.
    Control: a `group:` with genuinely NO `labels:` key stays a report — there is no label to check,
    which is a real read (pinned by the case above).
    """
    bindir = _stub_gh(tmp_path / "gh", "r1\tself-hosted-x64,ubuntu-latest")
    unreadable = {
        "group + expression labels": (
            "jobs:\n  a:\n    runs-on:\n      group: pool\n"
            "      labels: ${{ fromJSON(vars.labels) }}\n"
            "  b:\n    runs-on: ubuntu-latest\n    steps: []\n"
        ),
        "expression member in a list": (
            "jobs:\n  a:\n    runs-on: [self-hosted, \"${{ matrix.arch }}\"]\n"
            "  b:\n    runs-on: ubuntu-latest\n    steps: []\n"
        ),
        "group + block-list of expressions": (
            "jobs:\n  a:\n    runs-on:\n      group: pool\n      labels:\n"
            "        - ${{ matrix.os }}\n"
            "  b:\n    runs-on: ubuntu-latest\n    steps: []\n"
        ),
        "inline mapping with an expression": (
            "jobs:\n  a:\n    runs-on: {group: pool, labels: \"${{ matrix.os }}\"}\n"
            "  b:\n    runs-on: ubuntu-latest\n    steps: []\n"
        ),
        # ⛔ THE EXPRESSION **INSIDE** THE LABEL, NOT AS THE WHOLE VALUE. A prefix test (`/^\$/`)
        # emitted these as literal label names — so the block HAD a static label set, no `!` was
        # printed, and the one instrument meant to surface the all-PR-BLOCKED state exited 0. This
        # is the ordinary way a required leg gets parameterised (GitHub documents expressions in
        # `labels:`), and actionlint declines to verdict a value containing `${{` ANYWHERE
        # (`ast.go` `ContainsExpression` = `strings.Index(s, "${{")`, not a prefix test).
        "expression inside a scalar label": (
            "jobs:\n  a:\n    runs-on: ubuntu-24.04-${{ matrix.arch }}\n"
            "  b:\n    runs-on: ubuntu-latest\n    steps: []\n"
        ),
        "expression inside a list member": (
            "jobs:\n  a:\n    runs-on: [self-hosted-x64, \"ubuntu-24.04-${{ matrix.arch }}\"]\n"
            "  b:\n    runs-on: ubuntu-latest\n    steps: []\n"
        ),
        # A YAML ALIAS is a reference to a sequence elsewhere, not a label: every token here used to
        # be emitted as a label named `*pool`.
        "yaml alias": (
            "x-pool: &pool [self-hosted-x64, ubuntu-24.04]\n"
            "jobs:\n  a:\n    runs-on: *pool\n"
            "  b:\n    runs-on: ubuntu-latest\n    steps: []\n"
        ),
        # ⛔ WHITESPACE INSIDE A TOKEN is refused because `coverage_labels` is consumed by an
        # UNQUOTED `for label in $coverage_labels`, which word-splits — so one unreadable token
        # printed several FABRICATED labels (one row per fragment, exit 0).
        "space-bearing scalar": (
            "jobs:\n  a:\n    runs-on: self-hosted-x64 ubuntu-24.04\n"
            "  b:\n    runs-on: ubuntu-latest\n    steps: []\n"
        ),
        # ⛔ A BLOCK-SCALAR INDICATOR IS THE SCALAR HEADER, NOT A LABEL, AND IT PASSED THE PREDICATE.
        # `${{`, whitespace and `^[*&]` miss `>` / `|`, so the INDICATOR was printed as a label name
        # (`⛔ >- = 0 of 1 … a report, not a block`), the scalar VALUE on the next line was consumed
        # as an unreadable continuation, and the block cleared at exit 0 — over exactly the incident
        # state, with a `--status && …` guard told coverage was fine. These are ordinary YAML for a
        # single string (the folded/literal spelling of the same `ubuntu-24.04` the case above
        # refuses at exit 5), which is why the refusal cannot be a same-line-scalar assumption.
        "folded block scalar": (
            "jobs:\n  a:\n    runs-on: >-\n      ubuntu-24.04\n"
            "  b:\n    runs-on: ubuntu-latest\n    steps: []\n"
        ),
        "literal block scalar": (
            "jobs:\n  a:\n    runs-on: |\n      ubuntu-24.04\n"
            "  b:\n    runs-on: ubuntu-latest\n    steps: []\n"
        ),
    }
    for i, (name, body) in enumerate(unreadable.items()):
        repo = _repo_with_workflows(tmp_path / f"bad{i}", body)
        r = _run("--status", env=_env(tmp_path, PATH=f"{bindir}:{os.environ['PATH']}"), cwd=repo)
        assert r.returncode == 4, (
            f"{name}: a block with an unreadable member has NO static label set, so it must refuse "
            f"with 4 — not clear it at {r.returncode}: {r.stderr}"
        )
        assert "STATIC label set" in r.stderr, f"{name}: {r.stderr}"

    # ⛔ AND A PARTIAL READ REFUSES **BEFORE** IT VERDICTS ANYTHING. The three exit-4 arms used to
    # sit AFTER the per-label loop and after the block-level `⛔ UNCOVERED` message, so a repo with
    # ONE unreadable `runs-on:` block AND a zero-answer `ubuntu-24.04` under `CI_RUNNER_OS_LABEL=on`
    # printed the full UNCOVERED verdict plus its `--repair-labels` remedy and THEN exited 4: the
    # exit code disowning the verdict it had just printed (the EXIT header and the runbook define `4`
    # as "NO verdict" — `docs/ops/ci-runner-x64.md`, Exit codes), and the printed remedy unable to
    # fix the actual cause, since relabelling runners cannot parse an unreadable block. That is the
    # "a red whose printed remedy cannot work is a false red" rule this block applies to
    # `ubuntu-22.04` and to the gate-owned `ubuntu-latest`. The `b:` job below is a COVERED sibling
    # that gives the loop a verdict to print; `c:` is the zero-answer bare pin (stub pool carries
    # `self-hosted-x64,ubuntu-latest`, never `ubuntu-24.04`) that made the verdict fire.
    #
    # Mutation: move the three exit-4 arms back below the loop -> `UNCOVERED` reappears in stderr at
    # rc 4 -> RED.
    partial = (
        "jobs:\n  a:\n    runs-on: ${{ matrix.os }}\n"
        "  b:\n    runs-on: ubuntu-latest\n    steps: []\n"
        "  c:\n    runs-on: ubuntu-24.04\n    steps: []\n"
    )
    repo = _repo_with_workflows(tmp_path / "partial-and-unreadable", partial)
    r = _run(
        "--status",
        env=_env(
            tmp_path,
            PATH=f"{bindir}:{os.environ['PATH']}",
            CI_RUNNER_OS_LABEL="on",
        ),
        cwd=repo,
    )
    assert r.returncode == 4, (
        "an unreadable block means NO verdict — the pool's coverage of the readable labels is not "
        f"a verdict either, so this must be 4, not {r.returncode}: {r.stderr}"
    )
    assert "STATIC label set" in r.stderr, r.stderr
    assert "UNCOVERED" not in r.stderr, (
        "the block printed the ⛔ UNCOVERED verdict and its `--repair-labels` remedy and THEN "
        "refused with 4 — the exit code disowns the verdict just printed, and the printed remedy "
        "cannot fix an unreadable block. The no-verdict arms must run BEFORE the per-label loop: "
        f"{r.stderr}"
    )
    assert "ubuntu-24.04 = 0 of" not in r.stderr, (
        f"a partial read must not verdict any label, but a per-label verdict line was printed: {r.stderr}"
    )

    # ⛔ AND THE FABRICATION ITSELF MUST BE GONE, not merely out-voted. The content fixtures above
    # prove the refusal; this proves WHY it matters — the exit-4 arm is what stands between an
    # unreadable token and the unquoted `for label in $coverage_labels`, which word-splits. Measured
    # on the pre-fix program, one `[self-hosted-x64, "ubuntu-24.04-${{ matrix.arch }}"]` printed
    # THREE fabricated rows (`ubuntu-24.04-${{`, `matrix.arch`, `}}`) as "custom labels, a report,
    # not a block" and exited 0 — with `CI_RUNNER_OS_LABEL=on`, since no fragment matches the pin.
    fabricated = _repo_with_workflows(
        tmp_path / "fabricated",
        "jobs:\n  a:\n    runs-on: [self-hosted-x64, \"ubuntu-24.04-${{ matrix.arch }}\"]\n"
        "  b:\n    runs-on: ubuntu-latest\n    steps: []\n",
    )
    r = _run(
        "--status",
        env=_env(tmp_path, PATH=f"{bindir}:{os.environ['PATH']}", CI_RUNNER_OS_LABEL="on"),
        cwd=fabricated,
    )
    assert r.returncode == 4, (
        "a `runs-on` whose label set is not static has NO verdict — the pool's coverage of the "
        f"labels it could read is not a verdict either, so this must be 4, not {r.returncode}: {r.stderr}"
    )
    combined = r.stdout + r.stderr
    assert "${{" not in combined, (
        f"an expression reached the output as if it were a label name: {combined}"
    )
    assert not re.search(r"= \d+ of \d+", combined), (
        "a per-label `= N of M` row was printed for a block with no static label set — that is the "
        f"fabrication the unquoted `for label in $coverage_labels` produces: {combined}"
    )


def test_label_coverage_reads_the_runs_on_key_in_every_yaml_spelling(tmp_path: Path):
    """A QUOTED `runs-on` KEY is the same key, so it must be MEASURED — and the form still unread is refused.

    The extractor opened a block on ONE spelling of the key (`^runs-on[ \\t]*:`), so a key written as
    a quoted scalar opened no block: `finish()` never ran, NO `!` was printed, `runs_on_unreadable`
    stayed 0, and the two "no verdict" arms could not fire (a readable sibling label always exists)
    — so a workflow whose only `ubuntu-24.04` pin was `'runs-on': ubuntu-24.04` reported that label
    NOWHERE, printed the other labels as covered, and exited 0. That is the all-PR-BLOCKED incident
    reported as covered, in the direction this block forbids, and it is the spelling a
    machine-formatted / `yq`-style workflow produces. YAML gives a plain, a single-quoted, a
    double-quoted and an explicit key the SAME mapping key (measured with PyYAML: all four resolve
    to `runs-on`, value included), so GitHub routes the job identically and the instrument may not
    see them differently. The explicit-key form is instead COUNTED against the blocks opened, so a
    spelling this pass still cannot carry refuses at 4 rather than clearing everything.

    Mutation: restore `if (line ~ /^runs-on[ \\t]*:/)` -> both quoted cases exit 0 and print no
    `ubuntu-24.04` row -> RED. Mutation: drop the `keyseen`/`opened` count -> the explicit-key case
    exits 0 -> RED.
    """
    bindir = _stub_gh(tmp_path / "gh", "r1\tself-hosted-x64,ubuntu-latest")
    quoted = {
        "single-quoted key": "jobs:\n  a:\n    'runs-on': ubuntu-24.04\n  b:\n    runs-on: ubuntu-latest\n    steps: []\n",
        "double-quoted key": 'jobs:\n  a:\n    "runs-on": ubuntu-24.04\n  b:\n    runs-on: ubuntu-latest\n    steps: []\n',
    }
    for i, (name, body) in enumerate(quoted.items()):
        repo = _repo_with_workflows(tmp_path / f"quoted{i}", body)
        r = _run(
            "--status",
            env=_env(tmp_path, PATH=f"{bindir}:{os.environ['PATH']}", CI_RUNNER_OS_LABEL="on"),
            cwd=repo,
        )
        combined = r.stdout + r.stderr
        assert r.returncode == 5, (
            f"{name}: a quoted key IS the `runs-on` key, so the pin it carries must be measured — "
            f"a zero answer the pool is asked to serve is 5, not {r.returncode}: {combined}"
        )
        assert "ubuntu-24.04 = 0 of 1" in combined, (
            f"{name}: the quoted key was never measured, so the pin is reported NOWHERE — that is "
            f"the incident state cleared at exit 0: {combined}"
        )
        assert set(re.findall(r"^\s+(?:⛔ )?(\S+) = \d+ of \d+", combined, re.MULTILINE)) == {
            "ubuntu-latest",
            "ubuntu-24.04",
        }, f"{name}: a label row was printed for something that is not a label: {combined}"
        # CONTROL — quoting must not tilt the verdict either way: the same fixture under the
        # shipped default reports both readings (asked-and-gone vs never asked) and exits 0.
        r = _run("--status", env=_env(tmp_path, PATH=f"{bindir}:{os.environ['PATH']}"), cwd=repo)
        assert r.returncode == 0, (
            f"{name}: the flag-off reading of the pin is a report, so 0 — not {r.returncode}: {r.stderr}"
        )
        assert "ubuntu-24.04 = 0 of 1" in r.stderr and "asked and gone" in r.stderr, (
            f"{name}: the quoted key must reach the same measurement as the plain one: {r.stderr}"
        )

    # ⛔ THE FORM THE PASS STILL CANNOT CARRY IS REFUSED, NOT CLEARED. `? runs-on` / `: value` is
    # valid YAML for the same mapping (measured), so it is a required leg the tool cannot see — and
    # the count of key-shaped lines against the blocks opened turns it into the honest 4 instead of
    # a 0 that measured every OTHER label and called the set covered.
    explicit = _repo_with_workflows(
        tmp_path / "explicit",
        "jobs:\n  a:\n    ? runs-on\n    : ubuntu-24.04\n  b:\n    runs-on: ubuntu-latest\n    steps: []\n",
    )
    for flag in ("on", "off"):
        r = _run(
            "--status",
            env=_env(tmp_path, PATH=f"{bindir}:{os.environ['PATH']}", CI_RUNNER_OS_LABEL=flag),
            cwd=explicit,
        )
        combined = r.stdout + r.stderr
        assert r.returncode == 4, (
            f"an explicit-key `runs-on` opens no block, so the read is PARTIAL and has no verdict — "
            f"that is 4 under CI_RUNNER_OS_LABEL={flag}, not {r.returncode}: {combined}"
        )
        assert "STATIC label set" in combined, f"flag={flag}: {combined}"
        assert not re.search(r"= \d+ of \d+", combined), (
            f"flag={flag}: a partial read must not verdict any label: {combined}"
        )


# ── 4. dry run = plan only ──────────────────────────────────────────────────────────────────
def test_dry_run_decides_nothing_and_calls_nothing(tmp_path: Path):
    """A plan is the operator's step: no token, no download, no registration, no label read."""
    r = _run(
        "--count", "1", "--dry-run",
        env=_env(tmp_path, CI_RUNNER_NCPU="16", CI_RUNNER_ARCH="x86_64"),
    )
    out = r.stdout + r.stderr
    assert r.returncode == 0, f"a hermetic dry run must succeed: {r.stderr}"
    assert "dry-run: would" in out, out
    for forbidden in ("registration-token", "remove-token", "downloading", "sha256 verified"):
        assert forbidden not in out, f"a dry run reached {forbidden!r}: {out}"


# ── 5. COUNT is validated for every mode ────────────────────────────────────────────────────
def test_count_is_validated_for_every_mode_not_just_install(tmp_path: Path):
    """`--count 0 --uninstall` must not exit 0 having deregistered nothing."""
    for args in (
        ("--count", "0", "--uninstall", "--dry-run"),
        ("--count", "0", "--status"),
        ("--count", "0", "--repair-labels"),
        ("--count", "abc", "--uninstall", "--dry-run"),
        ("--count", "-2", "--uninstall", "--dry-run"),
    ):
        r = _run(*args, env=_env(tmp_path))
        assert r.returncode == 1, f"{args} must be a usage error, got {r.returncode}: {r.stderr}"
        assert "REFUSED" in r.stderr and "--count" in r.stderr, r.stderr
        # and it must not have got as far as acting on anything
        assert "skip:" not in r.stderr and "deregistered" not in r.stderr, r.stderr


# ── 5b. `--uninstall` deletes only what is provably a runner install ─────────────────────
def _stub_tool(bin_dir: Path, name: str, body: str) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    p = bin_dir / name
    p.write_text(f"#!/usr/bin/env bash\n{body}\n")
    p.chmod(0o755)


def _uninstall_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    """An uninstall env with `sudo`/`gh` stubs, a pinned arch, and a hermetic unit directory.

    ⛔ `CI_RUNNER_UNIT_DIR` IS NOT OPTIONAL HERE. Unpinned, the installer stats the REAL
    `/etc/systemd/system` for `actions.runner.daniel-ospina-tortoise.pi-runner-x64-1.service` — the
    unit this PR's own `svc.sh install` writes — so on the box these runners live on every
    uninstall arm that expects a removal gets `rc=3` with the directory intact (measured in review:
    arm A unpinned `rc=3`, dir survives; arm B pinned `rc=0`, dir gone). A test may not depend on
    whose host it runs on.

    The `sudo` stub logs its argv and then EXECUTES it, so `sudo rm -rf` really removes the
    directory — a no-op stub would make every removal arm vacuous.
    """
    bin_dir = tmp_path / "bin"
    _stub_tool(bin_dir, "sudo", f'printf "%s\\n" "$*" >> {tmp_path / "sudo.log"}\nexec "$@"')
    _stub_tool(bin_dir, "gh", 'echo "tok"')
    unit_dir = tmp_path / "systemd"
    unit_dir.mkdir(exist_ok=True)
    env = _env(tmp_path / "base", PATH=f"{bin_dir}:{os.environ['PATH']}", CI_RUNNER_ARCH="x86_64")
    env.update({"CI_RUNNER_UNIT_DIR": str(unit_dir)})
    env.update(extra)
    return env


def _deregisterable(runner: Path) -> Path:
    """A configured runner install whose `config.sh remove` SUCCEEDS (rc=0, like the real thing).

    ⛔ `config.sh` IS PART OF THE FIXTURE, and its absence is why the uninstall "positive arms"
    used to be vacuous: with no `config.sh` in the directory, `./config.sh remove` could only FAIL,
    and the pre-fix script deleted the directory and exited 0 anyway — so each arm exercised the
    failure path while asserting a successful removal. The stub exits 0 exactly as a real
    `config.sh remove` does against a registered runner, so the arm now reaches the path it names.
    The refused arm is pinned separately in
    `test_a_failed_deregistration_leaves_the_directory_and_exits_3`.
    """
    runner.mkdir(parents=True, exist_ok=True)
    (runner / ".runner").write_text("{}\n")
    _stub_tool(runner, "config.sh", "exit 0")
    return runner


def test_uninstall_refuses_to_sudo_rm_a_directory_that_is_not_a_runner(tmp_path: Path):
    """`CI_RUNNER_BASE` is operator input; "a directory exists" is not proof of a runner.

    The pre-fix gate was `[ -d "$d" ]` followed by `sudo rm -rf "$d"`, so
    `CI_RUNNER_BASE=/opt --uninstall` (or `$HOME/work`) recursively deleted whatever was there.
    Non-vacuity, measured on the pre-fix script: the unrelated directory below was DELETED and
    the run exited 0; post-fix it exits 2 and the directory and its file survive.

    The positive arm rides in the same test so the gate cannot pass by refusing everything: an
    install-shaped directory (`.runner`) IS deregistered and removed.
    """
    calls = tmp_path / "sudo.log"
    # ⛔ `CI_RUNNER_UNIT_DIR` IS PINNED, and that pin is the difference between a hermetic test and
    # a host-dependent one. Unpinned, the installer stats the REAL `/etc/systemd/system` for
    # `actions.runner.daniel-ospina-tortoise.pi-runner-x64-1.service` — the unit this PR's own
    # `svc.sh install` writes — so on the box these runners live on the positive arm got `rc=3`
    # with the directory still on disk, i.e. a RED that no assertion here is about. `_uninstall_env`
    # also supplies the `sudo` stub (logging to `calls`) and the `gh` stub.
    env = _uninstall_env(tmp_path)

    debris = tmp_path / "not-a-runner"
    debris.mkdir()
    (debris / "important.txt").write_text("an operator's unrelated tree\n")
    r = _run("--uninstall", "--count", "1", env={**env, "CI_RUNNER_BASE": str(debris)})
    assert r.returncode == 2, f"a non-runner directory must be refused, got {r.returncode}: {r.stderr}"
    assert "not a runner install" in r.stderr, r.stderr
    assert debris.is_dir() and (debris / "important.txt").exists(), (
        "the installer deleted a directory it could not prove was a runner"
    )
    # the refusal is the same gate in a plan: it is not deferred to the destructive step
    r = _run("--uninstall", "--count", "1", "--dry-run", env={**env, "CI_RUNNER_BASE": str(debris)})
    assert r.returncode == 2 and "not a runner install" in r.stderr, r.stderr

    # ⛔ `config.sh` ALONE IS NOT PROOF, and accepting it deleted a directory this installer never
    # created. It is a very common filename, and a bare `config.sh` that exits 0 satisfies
    # `./config.sh remove …` — so the loop reached `sudo rm -rf` on an unrelated tree and reported a
    # clean removal (measured before the fix: `deregistered proj`, EXIT=0, tree gone). It also adds
    # no coverage: the tarball is extracted in ONE `tar xzf`, so `config.sh` and `bin/Runner.Listener`
    # always land together.
    cfg_only = tmp_path / "a-config-script-that-is-not-a-runner"
    cfg_only.mkdir()
    _stub_tool(cfg_only, "config.sh", "exit 0")
    r = _run("--uninstall", "--count", "1", env={**env, "CI_RUNNER_BASE": str(cfg_only)})
    assert r.returncode == 2, f"a lone config.sh is not proof of a runner: {r.returncode} {r.stderr}"
    assert "not a runner install" in r.stderr, r.stderr
    assert cfg_only.is_dir() and (cfg_only / "config.sh").exists(), (
        "the installer deleted a directory it could not prove was a runner"
    )

    # positive arm: `.runner` is proof of a configured runner, so this one IS removed
    runner = _deregisterable(tmp_path / "a-runner")
    r = _run("--uninstall", "--count", "1", env={**env, "CI_RUNNER_BASE": str(runner)})
    assert r.returncode == 0, r.stderr
    assert not runner.exists(), f"a configured runner directory must be removed: {r.stderr}"
    assert "rm -rf" in calls.read_text(), "the sudo stub never saw the delete — the arm is vacuous"

    # ⛔ AND THE OTHER HALF OF THAT GATE: an install whose DEREGISTRATION FAILED is not deleted and
    # is not reported as success. `config.sh` and `.runner` live in the directory, so deleting it is
    # what turns "still registered in the repo" into an unrecoverable state — no local record, and a
    # re-run that reports "nothing to uninstall". The exit code is the file's own contract:
    # `3 OPERATION failed — … config.sh …`, never 0.
    failed = _deregisterable(tmp_path / "a-runner-that-could-not-be-removed")
    _stub_tool(failed, "config.sh", "exit 1")
    r = _run("--uninstall", "--count", "1", env={**env, "CI_RUNNER_BASE": str(failed)})
    assert r.returncode == 3, (
        "a failed deregistration is an OPERATION failure — it must not be reported as a clean "
        f"removal: {r.returncode} {r.stderr}"
    )
    assert failed.is_dir() and (failed / ".runner").exists(), (
        "a failed deregistration deleted the directory that holds its registration — the retry "
        f"(and `--status`) now has nothing to read: {r.stderr}"
    )
    assert "could not deregister" in r.stderr and "LEFT" in r.stderr, r.stderr

    # ⛔ THE UNIT-DIR PIN IS LOAD-BEARING, so this arm must be able to see it: the SAME runner
    # directory, with its unit present in the pinned dir and no `svc.sh`, is refused with rc=3 and
    # survives. That is precisely the state the UNPINNED env reaches on a host with the unit
    # installed (the review measured `rc=3`, directory survives), so dropping the pin again turns
    # the assertion above red instead of silently depending on whose box runs it.
    unit = "actions.runner.daniel-ospina-tortoise.pi-runner-x64-1.service"
    unit_dir = Path(env["CI_RUNNER_UNIT_DIR"])
    (unit_dir / unit).write_text("[Unit]\n")
    held = tmp_path / "a-runner-with-its-unit-installed"
    held.mkdir()
    (held / ".runner").write_text("{}\n")
    r = _run("--uninstall", "--count", "1", env={**env, "CI_RUNNER_BASE": str(held)})
    assert r.returncode == 3, (
        "a configured runner whose unit is installed and has no svc.sh must be LEFT intact at "
        f"exit 3 — this is the state an UNPINNED unit dir produces on the runners' own host, which "
        f"is why the pin above exists: {r.returncode} {r.stderr}"
    )
    assert held.is_dir(), r.stderr
    (unit_dir / unit).unlink()


# ── 5c0. a FAILED delete is an operation failure, not a clean removal ──────────────────────
def test_a_failed_delete_reports_an_operation_failure_not_a_clean_removal(tmp_path: Path):
    """`sudo rm -rf` is the last destructive step and its status used to be discarded.

    `set -e` is off on this script and `rc` was untouched, so a `sudo rm` that failed printed
    `deregistered <name>`, left the directory on disk, and exited 0 — the same fail-open shape the
    unit branch (5c) and the deregistration branch (5b) were fixed for. The registration IS gone at
    that point, so a re-run cannot deregister it either: the operator has to be told the directory
    survived, and told by the exit code as well as the prose.
    """
    env = _uninstall_env(tmp_path)
    # a `sudo` stub that fails ONLY the delete, exec'ing everything else
    _stub_tool(
        tmp_path / "bin", "sudo",
        f'printf "%s\\n" "$*" >> {tmp_path / "sudo.log"}\n'
        'case "$*" in *"rm -rf"*) exit 1 ;; esac\nexec "$@"',
    )
    runner = _deregisterable(tmp_path / "base")
    r = _run("--uninstall", "--count", "1", env={**env, "CI_RUNNER_BASE": str(runner)})
    assert r.returncode == 3, (
        "a delete that failed must not be reported as a clean removal: "
        f"{r.returncode} {r.stderr}"
    )
    assert "deregistered" in r.stderr, f"the deregistration did succeed: {r.stderr}"
    assert "could not remove" in r.stderr and "by hand" in r.stderr, r.stderr
    assert runner.is_dir() and (runner / ".runner").exists(), r.stderr


# ── 5c. a unit that cannot be removed must not be deleted out from under ─────────────────────
def test_uninstall_never_deletes_under_an_enabled_unit_it_failed_to_remove(tmp_path: Path):
    """The unit is removed FIRST; if that fails the directory and its registration are LEFT INTACT.

    Pre-fix, the destructive step was
    `[ -f ./svc.sh ] && sudo ./svc.sh stop >/dev/null 2>&1; sudo ./svc.sh uninstall >/dev/null 2>&1`
    — the `-f` guard covered `stop` only, `uninstall` ran unconditionally (where, with no svc.sh, it
    can only fail), and its exit status was discarded. `sudo rm -rf` then ran anyway and the run
    exited 0, so an enabled unit survived pointing at a deleted WorkingDirectory while
    `--uninstall` reported a clean removal. Measured on the pre-fix script: the directory was
    DELETED and the run exited 0; post-fix it exits 3 and the directory survives.
    """
    unit = "actions.runner.daniel-ospina-tortoise.pi-runner-x64-1.service"

    # arm 1: svc.sh exists but `svc.sh uninstall` fails while the unit is still installed
    env = _uninstall_env(tmp_path)
    (Path(env["CI_RUNNER_UNIT_DIR"]) / unit).write_text("[Unit]\nDescription=a runner\n")
    runner = tmp_path / "base"
    runner.mkdir()
    (runner / ".runner").write_text("{}\n")
    _stub_tool(runner, "svc.sh", "exit 1")   # `stop` and `uninstall` both fail
    r = _run("--uninstall", "--count", "1", env=env)
    assert r.returncode == 3, f"a failed unit removal is an OPERATION failure (3), got {r.returncode}: {r.stderr}"
    assert "could not remove the systemd unit" in r.stderr and unit in r.stderr, r.stderr
    assert runner.is_dir() and (runner / ".runner").exists(), (
        "the installer deleted the directory out from under a unit it could not remove"
    )
    assert "rm -rf" not in (tmp_path / "sudo.log").read_text(), "sudo rm -rf ran anyway"

    # arm 2: the unit is installed but the directory has no svc.sh, so it cannot be removed from here
    env = _uninstall_env(tmp_path / "no-svc")
    (Path(env["CI_RUNNER_UNIT_DIR"]) / unit).write_text("[Unit]\n")
    runner = tmp_path / "no-svc" / "base"
    runner.mkdir(parents=True)
    (runner / ".runner").write_text("{}\n")
    r = _run("--uninstall", "--count", "1", env=env)
    assert r.returncode == 3, r.stderr
    assert "has no svc.sh" in r.stderr and runner.is_dir(), r.stderr

    # positive control: the unit IS removable, so the directory IS deleted and nothing is warned
    env = _uninstall_env(tmp_path / "clean")
    (Path(env["CI_RUNNER_UNIT_DIR"]) / unit).write_text("[Unit]\n")
    runner = _deregisterable(tmp_path / "clean" / "base")
    _stub_tool(runner, "svc.sh", "exit 0")
    r = _run("--uninstall", "--count", "1", env=env)
    assert r.returncode == 0, r.stderr
    assert not runner.exists(), f"a removable unit's directory must be deleted: {r.stderr}"
    assert "could not remove" not in r.stderr, r.stderr


# ── 5c1. a unit that outlives its directory is never reported as "nothing to uninstall" ──────
def test_an_installed_unit_with_no_runner_directory_is_reported_not_called_clean(tmp_path: Path):
    """`--uninstall` must not exit 0 on an instance whose directory is gone but whose unit is not.

    Phase 1 skipped every instance whose DIRECTORY was absent before the unit was ever considered,
    and the block then printed `nothing to uninstall` and exited 0 — while an enabled unit kept
    restarting into a `WorkingDirectory` that no longer exists. The unit name needs no directory
    (`unit_for` derives it from the instance NUMBER), so the unit is knowable there. Without
    `$d/svc.sh` it cannot be uninstalled from this script, so it is REPORTED and the run exits 3:
    a caller must never read "ok" for a box that still runs a runner service. Measured on the
    pre-fix script: `skip: … does not exist` / `nothing to uninstall` / EXIT=0, unit still on disk.
    """
    env = _uninstall_env(tmp_path)
    unit = "actions.runner.daniel-ospina-tortoise.pi-runner-x64-1.service"
    (Path(env["CI_RUNNER_UNIT_DIR"]) / unit).write_text("[Unit]\n")
    absent = tmp_path / "nowhere"          # the directory a hand `rm -rf` (or the old gate) removed
    r = _run("--uninstall", "--count", "1", env={**env, "CI_RUNNER_BASE": str(absent)})
    assert r.returncode == 3, (
        f"an installed unit with no runner directory is an OPERATION failure: {r.returncode} {r.stderr}"
    )
    assert "nothing to uninstall" not in r.stderr, r.stderr
    assert unit in r.stderr and "systemctl disable --now" in r.stderr, r.stderr

    # …and with no unit either, the same invocation is still the clean no-op it claims to be
    clean = _uninstall_env(tmp_path / "clean")
    r = _run("--uninstall", "--count", "1", env={**clean, "CI_RUNNER_BASE": str(absent)})
    assert r.returncode == 0 and "nothing to uninstall" in r.stderr, r.stderr


# ── 5d. every removal token is minted BEFORE the first deletion ──────────────────────────────
def test_a_token_mint_failure_leaves_every_runner_untouched(tmp_path: Path):
    """A mint that fails on runner 2 must not leave runner 1 already deregistered and deleted.

    The mint used to sit inside the deleting loop, so the `die` fired after the first directory was
    gone — a half-deleted set (which an earlier comment in the installer claimed impossible). It is
    now a separate phase: on the pre-fix script the first directory was DELETED and the run exited
    3; post-fix both directories survive and the refusal says so.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    count = tmp_path / "mints"
    # succeed on the first mint, fail on the second
    _stub_tool(
        bin_dir, "gh",
        f'n=$(cat {count} 2>/dev/null || echo 0); echo $((n+1)) > {count}\n'
        f'[ "$n" -lt 1 ] && {{ echo tok; exit 0; }}; echo "gh: boom" >&2; exit 1',
    )
    _stub_tool(bin_dir, "sudo", f'printf "%s\\n" "$*" >> {tmp_path / "sudo.log"}\nexec "$@"')
    unit_dir = tmp_path / "systemd"
    unit_dir.mkdir()
    env = _env(
        tmp_path / "base", PATH=f"{bin_dir}:{os.environ['PATH']}",
        CI_RUNNER_ARCH="x86_64", CI_RUNNER_UNIT_DIR=str(unit_dir),
    )
    dirs = [tmp_path / "base", tmp_path / "base-2"]
    for d in dirs:
        d.mkdir()
        (d / ".runner").write_text("{}\n")

    r = _run("--uninstall", "--count", "2", env=env)
    assert r.returncode == 3, f"a token-mint failure is an OPERATION failure: {r.returncode} {r.stderr}"
    assert "NOTHING was deregistered or deleted" in r.stderr, r.stderr
    assert all(d.is_dir() for d in dirs), f"a half-deleted set: {[d.name for d in dirs if not d.is_dir()]}"
    sudo_log = tmp_path / "sudo.log"
    assert not sudo_log.exists() or "rm -rf" not in sudo_log.read_text(), (
        "a delete ran before every token was minted"
    )


# ── 5e. the arch refusal is an INSTALL guard, not a decommissioning gate ─────────────────────
def test_the_arch_refusal_governs_install_but_not_uninstall(tmp_path: Path):
    """A non-x86_64 host cannot INSTALL — and the arch guard does not refuse UNINSTALL.

    The guard sat ABOVE the uninstall block, so `--uninstall` on an aarch64 host (this fleet's
    Apple-Silicon box, and the five ARM64 pool runners) exited 2 and removed nothing: a runner
    could not be decommissioned from the host it was installed on, while the runbook claimed the
    refusal "still governs every INSTALL".

    ⚠️ WHAT THIS PROVES, AND WHAT IT DOES NOT. The `config.sh` below is a STUB that exits 0, so the
    final arm proves that the GUARD no longer refuses the removal on a foreign arch — NOT that a
    real deregistration succeeds there. It does not: the runner package's `config.sh remove` execs
    `./bin/Runner.Listener`, an x86-64 ELF, so on aarch64 the exec fails and the runner stays
    registered (the runbook's `--uninstall` bullet states this, and the installer surfaces the
    captured output). Testing the real failure would need either the real x64 package on the host
    or an `exec format error` stub; the stub here is deliberately the matching-arch case.

    Non-vacuity, measured on the pre-fix installer: the same `--uninstall --dry-run` exited **2**
    with "this installer is for x86_64 hosts" and planned nothing; post-fix it plans the removal
    (exit 0) and the removal itself succeeds.
    """
    env = _uninstall_env(tmp_path, CI_RUNNER_ARCH="aarch64")
    runner = tmp_path / "base"
    runner.mkdir()
    (runner / ".runner").write_text("{}\n")

    # INSTALL is still refused on the foreign arch, before any download or registration
    refused = _run(
        "--count", "1", "--dry-run",
        env=_env(tmp_path, CI_RUNNER_ARCH="aarch64", CI_RUNNER_NCPU="16"),
    )
    assert refused.returncode == 2, f"a non-x86_64 INSTALL must still be refused: {refused.stderr}"
    assert "x86_64 hosts" in refused.stderr, refused.stderr

    # the PLAN is produced (and the arch guard is not what stopped anything)
    plan = _run("--uninstall", "--count", "1", "--dry-run", env={**env, "CI_RUNNER_BASE": str(runner)})
    assert plan.returncode == 0, (
        "a non-x86_64 host must still be able to PLAN a removal — the arch refusal is an INSTALL "
        f"guard: {plan.returncode} {plan.stderr}"
    )
    assert "would stop and uninstall" in plan.stderr and "x86_64 hosts" not in plan.stderr, plan.stderr
    assert runner.is_dir(), "a dry run removed the directory"

    # …and the removal completes when the deregistration can run (the stub stands in for a
    # matching-arch `config.sh remove`; see this test's docstring for what that does NOT prove).
    _stub_tool(runner, "config.sh", "exit 0")
    done = _run("--uninstall", "--count", "1", env={**env, "CI_RUNNER_BASE": str(runner)})
    assert done.returncode == 0, f"a non-x86_64 host must be able to decommission a runner: {done.stderr}"
    assert not runner.exists(), done.stderr


# ── 6. bounds: unreadable is refused, infinite is a bound ───────────────────────────────────
def test_unreadable_bounds_are_refused_and_unlimited_forks_is_a_reading(tmp_path: Path):
    """A bound nobody can read is refused; `ulimit -u` = `unlimited` IS readable (it is infinity)."""
    unbounded = _run(
        "--count", "1", "--dry-run",
        env=_env(tmp_path, CI_RUNNER_ARCH="x86_64", CI_RUNNER_NCPU="16", CI_RUNNER_FORK_CAP="unlimited"),
    )
    assert unbounded.returncode == 0, unbounded.stderr
    assert "forks unbounded" in unbounded.stderr, (
        f"an infinite fork cap must read as no bound, not as an unreadable one: {unbounded.stderr}"
    )

    # NOTE: an EMPTY value is "unset" (`${VAR:-…}`), so it falls back to the real `ulimit -u` —
    # only a value that is present and unparseable is refused.
    for cap in ("garbage", "unknown", "12k"):
        r = _run(
            "--count", "1", "--dry-run",
            env=_env(tmp_path, CI_RUNNER_ARCH="x86_64", CI_RUNNER_NCPU="16", CI_RUNNER_FORK_CAP=cap),
        )
        assert r.returncode == 2, f"CI_RUNNER_FORK_CAP={cap!r} must be refused: {r.returncode} {r.stderr}"
        assert "cannot read the fork cap" in r.stderr, r.stderr

    # a garbage sizing override is a usage error, never silently $(( )) -> 0
    r = _run(
        "--count", "1", "--dry-run",
        env=_env(tmp_path, CI_RUNNER_ARCH="x86_64", CI_RUNNER_NCPU="16", CI_RUNNER_CORES_PER_RUNNER="two"),
    )
    assert r.returncode == 1, r.stderr
    assert "CORES_PER_RUNNER must be a non-negative integer" in r.stderr, r.stderr

    # ⛔ THE OTHER HOST READING IS A GUARD REFUSAL, NOT A USAGE ERROR — the same class as `FORK_CAP`
    # above, and the runbook says so. `CI_RUNNER_NCPU` is a seam for reading the core count, so an
    # unparseable value is refused rather than coerced to 0 (which would refuse every install on a
    # bound nobody read). Pinned here because the runbook's exit-code split (1 for the seven sizing
    # units, 2 for the two host readings) is only true while both readings behave that way.
    r = _run(
        "--count", "1", "--dry-run",
        env=_env(tmp_path, CI_RUNNER_ARCH="x86_64", CI_RUNNER_NCPU="two"),
    )
    assert r.returncode == 2, f"an unreadable core count must be refused as a guard: {r.returncode} {r.stderr}"
    assert "cannot read the core count" in r.stderr, r.stderr


# ── 7. the published exit contract ──────────────────────────────────────────────────────────
def test_exit_codes_and_help_distinguish_refusal_from_operation_failure(tmp_path: Path):
    """1 usage · 2 guard refused · 3 operation failed — and `--help` prints the whole contract."""
    usage = _run("--fallback", "sideways", "--print-labels", env=_env(tmp_path))
    assert usage.returncode == 1, usage.stderr

    # a missing tool is an OPERATION failure (3), not a guard refusal (2): the one distinction the
    # contract promises is exactly what a caller needs to decide whether re-running helps.
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    no_tools = _run("--status", env=_env(tmp_path, PATH=str(empty_bin)))
    assert no_tools.returncode == 3, f"a missing tool must exit 3, got {no_tools.returncode}: {no_tools.stderr}"
    assert "not on PATH" in no_tools.stderr, no_tools.stderr

    refused = _run("--count", "1", "--dry-run", env=_env(tmp_path, CI_RUNNER_ARCH="aarch64"))
    assert refused.returncode == 2, refused.stderr

    help_out = _run("--help", env=_env(tmp_path)).stdout
    for must in ("--uninstall", "--fallback", "--print-labels", "--repair-labels", "REBOOT", "EXIT"):
        assert must in help_out, f"--help dropped {must!r} (the help text must cover the whole header): {help_out}"


# ── 8. the runbook's line map is a MEASUREMENT, so re-measure it ─────────────────────────────
def test_the_runbooks_installer_line_map_lands_on_what_it_names():
    """The runbook's `install-ci-runner-x64.sh:NNN` map is what another operator's fixer is sent to.

    A line map records a PAST state of a file that keeps changing; two of its refs had already
    drifted (the gate's `current_mode` and the re-apply condition — corrected in the same PR), and
    every edit above them shifts the rest. So the refs are re-derived here: the map bullet is
    located by its own opening ref, every `:NNN` in it must be inside the installer, and the
    labelled refs (`forks`, `disk`, and friends) must land on the line that does the thing named.
    """
    runbook = (REPO_ROOT / "docs" / "ops" / "ci-runner-x64.md").read_text()
    lines = runbook.splitlines()
    start = next((i for i, line in enumerate(lines) if "tools/install-ci-runner-x64.sh:" in line), None)
    assert start is not None, (
        "the runbook no longer cites `tools/install-ci-runner-x64.sh:<line>` — re-anchor this pin "
        "to whatever map replaced it rather than deleting it"
    )
    idx = start
    bullet: list[str] = []
    for line in lines[idx:]:
        if not line.strip():
            break
        bullet.append(line)
    map_text = "\n".join(bullet)

    installer = INSTALLER.read_text().splitlines()
    refs = {int(n) for n in re.findall(r":(\d+)(?:-\d+)?\b", map_text)}
    assert refs, f"no line refs parsed out of the map bullet: {map_text!r}"
    out_of_range = {n: len(installer) for n in refs if not 1 <= n <= len(installer)}
    assert not out_of_range, f"the runbook cites lines past EOF ({len(installer)} lines): {out_of_range}"

    # label -> a regex that must match the line the runbook points at
    anchors = {
        "forks": r"FORK HEADROOM FIRST",
        "disk": r"DISK NEXT",
        "cpu/memory": r"CPU LAST",
        "fallback_engaged": r"^fallback_engaged\(\)",
        # ⛔ THE ANCHOR THIS CHANGE ADDS. Without an entry it was checked only by the in-range arm,
        # so the next edit above it moved `resolve_os_label` and the map kept pointing at an
        # unrelated line — exactly the drift this dict's own comment records for `repair` /
        # `print-labels`, and why it demands EVERY anchor be found by name.
        "resolve_os_label": r"^resolve_os_label\(\)",
        "resolve_labels": r"^resolve_labels\(\)",
        "verify": r'\[ "\$got" = "\$SHA256_LINUX_X64" \]',
        "token": r"registration-token",
        # ⛔ THE TWO MODE BLOCKS THE MAP LINKS TO. Both were cited one whole guard-block away from
        # where they actually start after a fix commit shifted lines above them — the labelled
        # anchors below (`forks`, `verify`, …) all still landed, so nothing caught it (review
        # finding, cycle 2). A ref that points INTO a guard the sentence does not name is worse than
        # no ref: a fixer sent to `:327` edits the capacity guard on the destructive path.
        "uninstall": r'^if \[ "\$MODE" = uninstall \]',
        "install": r'^if \[ "\$MODE" = install \]',
        # The two MODE blocks that sit between the labelled anchors above. `repair` and
        # `--print-labels` were cited by NUMBER and drifted +4 the moment an edit landed above them,
        # while every anchored ref still landed — so nothing caught it (measured by a verifier that
        # re-checked the map by hand). Anchoring them is what makes that drift red instead.
        "repair": r'^if \[ "\$MODE" = repair \]',
        "print-labels": r'^if \[ "\$MODE" = labels \]',
    }
    # ⛔ EVERY anchor must be found by name: a label the map stopped naming is how this pin would
    # silently degrade to checking nothing (the runbook writes some labels bare — `(forks :262` —
    # and some in backticks — `` `fallback_engaged` :160``, so both forms are matched).
    wrong: dict[str, tuple[int, list[int]]] = {}
    missing: list[str] = []
    for label, regex in anchors.items():
        m = re.search(rf"`?{re.escape(label)}`?\s*`:(\d+)(?:-\d+)?`", map_text)
        if m is None:
            missing.append(label)
            continue
        n = int(m.group(1))
        target = installer[n - 1] if 1 <= n <= len(installer) else ""
        if not re.search(regex, target):
            wrong[label] = (n, [i + 1 for i, line in enumerate(installer) if re.search(regex, line)])
    assert not missing, (
        f"the runbook's installer map no longer names {missing!r} with a line ref — re-anchor this "
        "pin (a label dropped from the map is a line map that stops being checked): " f"{map_text!r}"
    )
    assert not wrong, (
        "the runbook's installer line map no longer lands on what it names "
        "(re-derive the numbers from the installer): "
        + "; ".join(f"{k}: doc says :{n}, the anchor is at {actual}" for k, (n, actual) in wrong.items())
    )

    # ⛔ AND THE **SIBLING** BULLETS, WHICH THE MAP PIN ABOVE CANNOT SEE. Three refs sat OUTSIDE the
    # map bullet, so they were only range-checked (`1 <= n <= EOF`) — and an offset-shifted number
    # always passes a range check. All three had been shifted by a constant that matched main's
    # numbers plus a line delta, and one of them landed on the INSTALL disk guard
    # (`die "disk headroom: …"`) while its sentence named the uninstall block: a fixer sent there
    # edits the capacity guard on the destructive path, which is worse than no ref at all. They are
    # pinned by SENTENCE here so that class reds instead of sailing through.
    whole = "\n".join(lines)

    m = re.search(r"`--uninstall` deletes only what it can PROVE[^(]*\(`:(\d+)-(\d+)`", whole)
    assert m is not None, (
        "the runbook no longer cites the `--uninstall` span — re-anchor this pin rather than "
        "deleting it (an unpinned span is how it drifted onto the install disk guard)"
    )
    lo, hi = int(m.group(1)), int(m.group(2))
    span = "\n".join(installer[lo - 1 : hi])
    assert "# ── uninstall ──" in span, (
        f"the runbook's --uninstall span :{lo}-{hi} does not begin inside the uninstall section "
        f"(it starts at {installer[lo - 1]!r})"
    )
    assert 'if [ "$MODE" = uninstall ]' in span, (
        f"the runbook's --uninstall span :{lo}-{hi} does not contain the uninstall mode block"
    )
    assert installer[hi - 1].strip() == "fi", (
        f"the runbook's --uninstall span ends at :{hi} = {installer[hi - 1]!r}, not at the block's fi"
    )

    # The arch refusal, cited twice by NUMBER outside the map: `(:NNN, the MODE = install block)` and
    # `the refusal itself dies at :NNN`. Both were off by the same constant, one of them landing on a
    # comment line. One is a block start, the other is a `die` — checking them by shape, not range.
    m = re.search(r"`:(\d+)`, the `MODE = install`", whole)
    assert m is not None, "the runbook no longer cites the `MODE = install` block — re-anchor this pin"
    n = int(m.group(1))
    assert re.search(r'^if \[ "\$MODE" = install \]', installer[n - 1]), (
        f"the runbook cites :{n} as the `MODE = install` block, but that line is {installer[n - 1]!r}"
    )

    m = re.search(r"the refusal itself dies at `:(\d+)`", whole)
    assert m is not None, "the runbook no longer cites where the arch refusal dies — re-anchor this pin"
    n = int(m.group(1))
    assert "would register a DIFFERENT arch label" in installer[n - 1], (
        f"the runbook says the arch refusal dies at :{n}, but that line is {installer[n - 1]!r}"
    )

    # ⛔ THE FIFTH SIBLING SENTENCE, AND THE ONE THAT HAD DRIFTED RED-FREE AT THIS HEAD. The map
    # above re-derivation moved the runbook's `--uninstall` proof ref off `is_runner_install` and
    # onto `:268` — a `# shellcheck disable=SC1091` pragma inside `resolve_os_label()` — while the
    # function itself was 54 lines earlier (on `main` the sentence cited `:173`, and `:173` WAS the
    # function, so the re-derivation is what moved it). An operator sent to `:268` to check the "is
    # this provably a runner install?" proof lands in the OS-label resolver instead, which is
    # precisely the class the doc itself calls worse than no ref at all. Pinned by SENTENCE, not by
    # the `anchors` dict, because that dict's regex requires the ref to follow the label
    # immediately and this sentence spells it `` `is_runner_install` at `:NNN` ``.
    m = re.search(r"`is_runner_install` at `:(\d+)`", whole)
    assert m is not None, (
        "the runbook no longer cites `is_runner_install` by line — re-anchor this pin rather than "
        "deleting it (an unpinned ref is how it drifted into the OS-label resolver)"
    )
    n = int(m.group(1))
    assert re.search(r"^is_runner_install\(\)", installer[n - 1]), (
        f"the runbook cites :{n} as the `is_runner_install` proof, but that line is "
        f"{installer[n - 1]!r} (the definition is at "
        f"{[i + 1 for i, line in enumerate(installer) if re.search(r'^is_runner_install\\(\\)', line)]})"
    )


# ── 8. a missing installer is a FAIL, not a silent skip ─────────────────────────────────────
def test_the_runbooks_cross_file_refs_land_on_what_they_name():
    """Refs OUTSIDE the installer map were only range-checked, so they drifted silently.

    The runbook cites `.github/workflows/*.yml`, `scripts/ci_runner_gate.sh` and `config.sh` by line
    number, and the installer map pin above can only see refs INSIDE its own bullet. Measured at
    this head: `mirror-images.yml:92` cited the arm64 comment while the file had moved it to `:104`
    (this PR adds 12 net lines above it), and `python-ci.yml:1556` / `:1544-1546` were ALREADY stale
    on `main` (the cited comment sits at `:1761`). Each still passed the only check they had — `1 <=
    n <= EOF` — which an offset-shifted number always passes.

    Mutation: revert any of the four refs to its pre-fix number -> RED with the line it actually
    hit. A ref that lands two files away is worse than none: a fixer sent there edits workflow
    routing or a shell script the sentence never named.
    """
    runbook = (REPO_ROOT / "docs" / "ops" / "ci-runner-x64.md").read_text()

    def at(rel: str, n: int) -> str:
        lines = (REPO_ROOT / rel).read_text().splitlines()
        assert 1 <= n <= len(lines), f"{rel} has {len(lines)} lines, the runbook cites :{n}"
        return lines[n - 1]

    # Who the pool's arm64-ness is (and why nothing depends on it).
    m = re.search(r"`mirror-images\.yml:(\d+)` records", runbook)
    assert m is not None, (
        "the runbook no longer cites `mirror-images.yml:<line>` for the arm64 measurement — "
        "re-anchor this pin rather than deleting it"
    )
    n = int(m.group(1))
    assert "orbctl run -m pi-runner uname -m" in at(".github/workflows/mirror-images.yml", n), (
        f"the runbook cites mirror-images.yml:{n} for the arm64 comment, but that line is "
        f"{at('.github/workflows/mirror-images.yml', n)!r}"
    )

    # The prohibition, and the reason for it.
    m = re.search(r"`\.github/workflows/python-ci\.yml:(\d+)`\s*\n?\s*is \"Do NOT change it back", runbook)
    assert m is not None, (
        "the runbook no longer cites `python-ci.yml:<line>` as the \"Do NOT change it back\" "
        "prohibition — re-anchor this pin"
    )
    n = int(m.group(1))
    assert "never to `self-hosted`" in at(".github/workflows/python-ci.yml", n), (
        f"the runbook cites python-ci.yml:{n} as the prohibition, but that line is "
        f"{at('.github/workflows/python-ci.yml', n)!r}"
    )

    m = re.search(r"recorded at `:(\d+)-(\d+)` \u2014 a `self-hosted` pin removes", runbook)
    assert m is not None, (
        "the runbook no longer cites the python-ci.yml span that records WHY a `self-hosted` pin is "
        "forbidden — re-anchor this pin"
    )
    lo, hi = int(m.group(1)), int(m.group(2))
    span = "\n".join((REPO_ROOT / ".github/workflows/python-ci.yml").read_text().splitlines()[lo - 1 : hi])
    assert "not answered" in span and "WAIT for a hosted runner" in span, (
        f"the runbook's python-ci.yml span :{lo}-{hi} does not record the reason it names "
        f"(pending-not-answered instead of a false red): {span!r}"
    )


# ── 8. the accepted-cost inventory is the MEASURED set, not the remembered one ─────────────────
def test_the_runbook_names_every_leg_the_mitigation_makes_pool_eligible():
    """A `runs-on: ubuntu-24.04` job whose file was never told about the mitigation stays invisible.

    Three sites still asserted the premise this change inverts, because the inventory was written
    from memory rather than read off the tree: `python-ci.yml` (`test-slow`, the leg #6897 was
    filed about — *"`ubuntu-24.04` is claimed by NO self-hosted runner"*), the `workflow-lint`
    STEP comment (*"pinned to a HOSTED label … so it has its own egress IP"*, 22 lines under the
    header that says the opposite), and `finding-provenance.yml` (*"a version-pinned label no
    self-hosted runner claims removes the ambiguity"*) — and `finding-provenance` was not named in
    the runbook's accepted-cost list at all, so its two jobs became pool-eligible with nobody
    having recorded the cost.

    So this pins the INVENTORY against the TREE, two ways: every workflow that carries a
    job-level `runs-on: ubuntu-24.04` must be named in the runbook's accepted-cost list, the stated
    job count must be the measured one, and each such file must itself name the mitigation (a file
    whose pin the mitigation makes servable and that never mentions it is the shape that stayed
    false for a whole round of review).

    Mutation: drop the `finding-provenance` bullet -> RED on both arms (the file is pinned twice
    but named nowhere, and its own comment still asserts the reverse).
    """
    wf_dir = REPO_ROOT / ".github" / "workflows"
    # ⛔ THE PATTERN CARRIES EVERY YAML SPELLING OF THE KEY, or this pin cannot see the spelling the
    # extractor used to miss: counting only `^    runs-on: ubuntu-24\.04$` reds when an EXISTING pin
    # is re-spelled as a quoted key (total 9 -> 8), but `'runs-on': ubuntu-24.04` ADDED as a new pin
    # leaves the count at 9 and the suite green — so the one counter-guard over this inventory was
    # blind to exactly the spelling that made the extractor report the label NOWHERE.
    pinned = {
        path.name: len(
            re.findall(
                r'^    ["\']?runs-on[\']?[ \t]*:[ \t]*ubuntu-24\.04$',
                path.read_text(),
                re.MULTILINE,
            )
        )
        for path in sorted([*wf_dir.glob("*.yml"), *wf_dir.glob("*.yaml")])
    }
    pinned = {name: n for name, n in pinned.items() if n}
    total = sum(pinned.values())
    assert total == 9, (
        "the `runs-on: ubuntu-24.04` job count moved — re-derive the runbook's accepted-cost list "
        f"and this pin together, do not assume {total} is right: {pinned}"
    )

    runbook = (REPO_ROOT / "docs" / "ops" / "ci-runner-x64.md").read_text()
    start = runbook.index("⛔ **The mitigation is label-WIDE")
    section = runbook[start : runbook.index("These are **accepted costs", start)]

    words = {6: "six", 8: "eight", 9: "nine"}
    assert f"all **{words[total]}**" in section, (
        "the runbook no longer states the measured number of legs the mitigation makes servable, or "
        f"states a different one than the tree has ({total}): {section[:400]!r}"
    )

    for name, n in pinned.items():
        assert name in section, (
            f"{name} pins {n} job(s) to `ubuntu-24.04`, so the mitigation makes {'them' if n > 1 else 'it'} "
            "pool-eligible — but the runbook's accepted-cost list does not name the file, so its cost is "
            "unrecorded. Add a bullet (or re-derive the count) rather than widening this assertion."
        )
        assert "CI_RUNNER_OS_LABEL" in (wf_dir / name).read_text(), (
            f"{name} pins a job to `ubuntu-24.04` and never mentions `CI_RUNNER_OS_LABEL`: its in-file "
            "comment still reads as if the pool cannot answer that label, which is the premise this "
            "mitigation makes FALSE (measured: 7 of 12 runners carry it)"
        )


# ── 8. a missing installer is a FAIL, not a silent skip ─────────────────────────────────────
def test_memory_ci_runners_exit_contract_is_intent_gated():
    """`MEMORY.md` is the shortcut file an agent reads MID-CODE, so its exit contract must match.

    This PR edits the `[ci-runners]` line, and it stated the OLD contract: *"`0` exits 5, a partial
    pool prints ⚠️ PARTIAL"*. At this head a zero-answer bare pin exits `5` only when the invoking
    shell sets `CI_RUNNER_OS_LABEL=on|auto`; under the shipped default it reports both readings and
    exits `0`. A reader keying on that line took a bare `--status` returning `0` as "no zero-answer
    label" — the fail-open direction, in the one file that is read without the runbook open. The
    doc's exit list was updated for the intent read; this line was not.

    Mutation: restore the unqualified phrasing -> RED. The assertion is the QUALIFIER's presence,
    not the whole sentence, so the line can be rewritten without re-pinning prose.
    """
    text = (REPO_ROOT / "MEMORY.md").read_text()
    m = re.search(r"^\[ci-runners\]:.*$", text, re.MULTILINE)
    assert m is not None, "MEMORY.md lost its `[ci-runners]` line — re-anchor this pin"
    line = m.group(0)
    assert "exits 5" in line, (
        "the `[ci-runners]` line no longer states the exit contract at all — re-anchor this pin "
        "rather than deleting it"
    )
    assert "exits 5 ONLY when" in line and "reports BOTH readings" in line, (
        "MEMORY.md's `[ci-runners]` line states the exit-5 contract WITHOUT the intent gate — a "
        f"bare `--status` exiting 0 then reads as 'no zero-answer label': {line!r}"
    )


# ── 8. a missing installer is a FAIL, not a silent skip ───────────────────────────────────────
def test_a_missing_installer_fails_collection_instead_of_skipping(tmp_path: Path):
    """Copied one directory down, the module's own `REPO_ROOT` has no installer → it must go RED.

    This is the non-vacuity proof for the file-level policy, measured on the same copy with the
    old module-level `pytest.mark.skipif` restored: it reported every test SKIPPED and exit 0 — a
    green suite with zero assertions run on a tree where the installer it pins is gone.
    """
    copy_root = tmp_path / "copy"
    (copy_root / "tests").mkdir(parents=True)
    copied = copy_root / "tests" / "test_ci_runner_x64_install.py"
    copied.write_text(Path(__file__).read_text())

    r = subprocess.run(
        # `--noconftest`: the throwaway tree gets no conftest at all (this repo's `tests/conftest.py`
        # forbids collecting under a shared temp dir, and a stray ancestor conftest is not the
        # subject here). The copied module imports nothing but the stdlib and pytest.
        [sys.executable, "-m", "pytest", str(copied), "-q", "-p", "no:cacheprovider", "--noconftest"],
        capture_output=True, text=True,
        cwd=str(copy_root),
    )
    assert r.returncode != 0, f"a missing installer must FAIL, not pass or skip: {r.stdout}{r.stderr}"
    assert "skipped" not in r.stdout, f"a missing installer must never be a skip: {r.stdout}"
    assert "installer not found" in (r.stdout + r.stderr), r.stdout + r.stderr
