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
  10. ⛔ **A REPAIR UNIONS; IT CAN NEVER REMOVE A LABEL A WORKFLOW NEEDS.** `--repair-labels` used
     to PUT exactly the set `resolve_labels` computed — a REPLACE — so every extra label on a
     runner was silently dropped. With the ARM64 pool stripped of `ubuntu-latest` (#6897) `auto`
     computes `self-hosted-x64` alone, so the repair removed the label the REQUIRED
     `python-ci-gate` aggregate is pinned to: the run sat `pending` with ZERO materialised jobs —
     no red, no green, every PR blocked with nothing to look at (measured 2026-10-27: runs
     40898791192 and 40901844458, `jobs=0`; a hand-restored label was gone within ~25 minutes,
     twice). The repair now reads the runner's CURRENT labels and PUTs `current ∪ computed`, so it
     can ADD a label and can never REMOVE one a workflow targets — EXCEPT the two named carve-outs
     the union would otherwise make permanent, each a false PASS rather than a queue delay:
     a VERSION-PINNED HOSTED label (`ubuntu-<version>`, the label a leg pins to ESCAPE this pool)
     and a FOREIGN ARCH MARKER (`self-hosted-*` other than this box's, the pre-#2327 stamp a repair
     is supposed to heal). Both are printed, and the derived `removed=[…]` list must name exactly
     them and nothing else; `ubuntu-latest` and a genuine custom label always survive.
     `--status` answers "who answers this `runs-on:` label" for the same reason: the failure it
     reports produces no check to look at.
  10b. **The version-pin carve-out is the `off` state of `CI_RUNNER_OS_LABEL`, not an unconditional
     drop** — #6897's `OVERRIDES:` marker records the revert as `CI_RUNNER_OS_LABEL=off` PLUS
     `--repair-labels`, so a repair that dropped the pin whatever the flag said performed that revert
     with no switch involved. `on|auto` KEEPS the pin (and prints `version-pinned, kept=[…]`),
     `off`/unset drops it, an unrecognised value is read as `off`. The keep-half is also ARCH-AWARE:
     a runner whose read-only host label says `ARM64`/`ARM` is refused, because keeping
     `ubuntu-latest` there is the measured #6897 false red rather than a queue delay.
  10c. **A label set is PARSED literally, and `relabelled` is a claim about the server.** An unquoted
     split expanded a glob-carrying label against the operator's cwd and PUT the filenames as runner
     labels (the illegal-label guard now refuses that runner); and the Set verb's 200 is read back,
     so every requested label must be in the returned set before `relabelled` is printed — a pool has
     already reported `ok=16 fail=0` for a label that was never attached (#6897).
  11. ⛔ **AN UNREAD WORKFLOW MUST NOT READ AS COVERED.** GitHub accepts `.yml` and `.yaml`, and the
     coverage reader globbed only `*.yml` — so a `.yaml` workflow was dropped silently while the
     block printed "every one has an online self-hosted answer". There are 0 `.yaml` workflows
     today, which is exactly why nothing caught it: the block's own contract is that an unread
     label never reads as healthy, one level down.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

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
}


def _env(base: Path, **extra: str) -> dict[str, str]:
    """A hermetic installer environment: local base dir, neutral headroom, no host state.

    `CI_RUNNER_OS_LABEL` is pinned EMPTY here, not inherited: it is the box's recorded choice about
    claiming a version-pinned hosted label, so a machine that happens to export `on` would flip the
    version-pin carve-out under every arm in this file and make the drop arm pass on the real host
    for the wrong reason. An empty value reads as `off`, which is the installer's default.
    """
    env = dict(os.environ)
    env.update(_NEUTRAL_HEADROOM)
    env.update(
        {"CI_RUNNER_REPO": "daniel-ospina/tortoise", "CI_RUNNER_BASE": str(base), "CI_RUNNER_OS_LABEL": ""}
    )
    env.update(extra)
    return env


def _run(
    *args: str, env: dict[str, str] | None = None, cwd: Path | None = None
) -> subprocess.CompletedProcess:
    """Run the installer. `cwd` matters for exactly one arm: an unquoted label split would expand a
    `*` against the shell's working directory, so that test runs the installer FROM the directory
    holding the decoy files it would otherwise swallow."""
    return subprocess.run(
        [BASH, str(INSTALLER), *args],
        capture_output=True,
        text=True,
        env=env or _env(Path("/nonexistent-base")),
        cwd=None if cwd is None else str(cwd),
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
def _stub_gh(
    tmp_path: Path,
    stdout: str,
    rc: int = 0,
    *,
    labels: str = "self-hosted,Linux,X64",
    rows: str = "",
    detail_by_name: dict[str, str] | None = None,
    put_response: str | None = None,
) -> Path:
    """A `gh` that answers all three reads the installer makes, and logs every call.

    The three answers are dispatched on the jq expression, the only thing that distinguishes them:
      * `busy=` → the `--status` runner rows (`rows`);
      * `select(.name==` → the repair's `id<TAB>label,label,…` detail line (`labels`, or
        `detail_by_name` for a per-runner answer — a batch arm needs one runner's read to differ
        from another's);
      * anything else → the ARM64 pool's fallback count (`stdout`, the existing argument).
    A `-X PUT` also records the request BODY it was fed on stdin, so a test can assert the exact
    label set a repair would write — not a message about it — and answers it the way the REAL Set
    verb does: with the runner's resulting label set, which is what the repair reads back before it
    calls a write `relabelled`. `put_response` overrides that answer, which is how the arm for #6897's
    `ok=16 fail=0` false PASS is exercised (an accepted PUT whose returned set is missing a label).
    `labels` defaults to the read-only trio GitHub itself returns for any runner, which is also what
    makes the union's host-label filter observable rather than exercised on an impossible empty list.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    log = tmp_path / "gh-calls.log"
    gh = bin_dir / "gh"
    detail = f"7\t{labels}"
    if detail_by_name:
        per_name = "".join(
            f"      {shlex.quote(name)}) printf '7\\t%s\\n' {shlex.quote(value)} ;;\n"
            for name, value in detail_by_name.items()
        )
        detail_arm = (
            "  *'select(.name=='*)\n"
            "    nm=\"$(printf '%s' \"$*\" | sed -n 's/.*select(\\.name==\"\\([^\"]*\\)\").*/\\1/p')\"\n"
            "    case \"$nm\" in\n"
            f"{per_name}"
            f"      *) printf '7\\t%s\\n' {shlex.quote(labels)} ;;\n"
            "    esac ;;\n"
        )
    else:
        detail_arm = f"  *'select(.name=='*) printf '%s\\n' {shlex.quote(detail)} ;;\n"
    # ⛔ THE SET VERB'S REAL 200 IS A LABEL SET, AND THE REPAIR READS IT BACK. Echoing the body it was
    # fed is the SUCCESS case (the write took effect); `put_response` replaces that answer with the
    # false PASS #6897 measured — an accepted PUT whose returned set is missing a requested label.
    if put_response is not None:
        put_arm = f"    printf '%s\\n' {shlex.quote(put_response)}\n"
    else:
        put_arm = (
            "    printf '%s\\n' \"$(printf '%s' \"$body\" | sed -n 's/^{\"labels\":\\[\"\\(.*\\)\"\\]}$/\\1/p'"
            " | sed 's/\",\"/,/g')\"\n"
        )
    gh.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> {log}\n'
        "case \"$*\" in\n"
        "  *'-X PUT'*)\n"
        "    body=\"$(cat)\"\n"
        f'    printf \'PUT %s\\n\' "$body" >> {log}\n'
        f"{put_arm}"
        f"    exit {rc} ;;\n"
        "esac\n"
        "case \"$*\" in\n"
        f"  *'busy='*) printf '%s\\n' {shlex.quote(rows)} ;;\n"
        f"{detail_arm}"
        f"  *) printf '%s\\n' {shlex.quote(stdout)} ;;\n"
        "esac\n"
        f"exit {rc}\n"
    )
    gh.chmod(0o755)
    return bin_dir


def _put_body(stub_dir: Path) -> list[str]:
    """The label list of the LAST `-X PUT …/labels` the stub `gh` was asked to make."""
    log = (stub_dir / "gh-calls.log").read_text()
    puts = re.findall(r"PUT (\{.*\})", log)
    assert puts, f"no label PUT was attempted; the gh calls were:\n{log}"
    return json.loads(puts[-1])["labels"]


def _repair_runner(
    tmp_path: Path, *, pool: str, labels: str, put_response: str | None = None
) -> tuple[Path, dict[str, str]]:
    """An already-configured runner 1 whose CURRENT labels are `labels`, plus the stub env.

    `put_response` is the label set the Set verb's 200 will be read back as; the default (None) is
    the honest one — the labels the request asked for.
    """
    stub = _stub_gh(tmp_path / "gh", pool, labels=labels, put_response=put_response)
    (tmp_path / ".runner").write_text('{"agentName": "pi-runner-x64-1"}\n')
    env = _env(tmp_path, PATH=f"{stub}:{os.environ['PATH']}", CI_RUNNER_ARCH="x86_64")
    return tmp_path / "gh", env


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
    and the runbook used to sell the repair as "syncs the fallback label to the gate's state", a
    sentence this PR corrects to the ADD-only direction (a union cannot detach the label; the gate
    does that, and `--repair-labels --fallback off` is now refused with exit 2).
    Repair mode
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


# ── 3b. a repair UNIONS — it can never remove a label a workflow needs ───────────────────────
#: The pool label set a repair must get RIGHT in both directions, shared by every arm below so the
#: arms cannot drift into exercising different sets. Same spelling as
#: `tests/test_runner_autoscale.py::POOL_LABELS_WITH_VERSION_PINS` — ONE invariant, two writers
#: (`--repair-labels` and `_preserve_labels`), so the two must drop and keep the same labels.
POOL_LABELS = (
    "self-hosted,Linux,X64,self-hosted-x64,ubuntu-latest,ubuntu-24.04,ubuntu-24.04-arm,"
    "gpu,ubuntu-2204-tools"
)


def test_repair_on_a_runner_that_already_carries_the_label_keeps_it(tmp_path: Path):
    """Case 1: a runner that already carries `ubuntu-latest` still carries it after a repair — along
    with every genuine CUSTOM label it carries.

    The pre-fix repair PUT exactly the computed set, so an already-correct runner was re-stamped
    with whatever the resolver happened to compute and any label it did not compute was dropped.
    `gpu` below is that witness: a custom label no resolver computes — under a REPLACE it disappears
    from the body, which is the whole defect in one line. `ubuntu-2204-tools` is the boundary
    witness: it merely STARTS `ubuntu-<digits>` and is not a hosted label, so the version-pin
    carve-out (the next arm) must not eat it.
    """
    stub, env = _repair_runner(
        tmp_path,
        pool="1",
        labels="self-hosted,Linux,X64,self-hosted-x64,ubuntu-latest,gpu,ubuntu-2204-tools",
    )

    r = _run("--repair-labels", "--count", "1", env=env)
    assert r.returncode == 0, r.stderr
    assert "ENGAGED" in r.stderr, r.stderr          # here the computed set DOES include the label
    put = _put_body(stub)
    assert "ubuntu-latest" in put, put
    assert set(put) == {"self-hosted-x64", "ubuntu-latest", "gpu", "ubuntu-2204-tools"}, put
    # the host-owned trio is read back on every runner and cannot be PUT (HTTP 422), so it is
    # filtered out of the body — and PRINTED, not silently dropped
    assert "host-owned, not sent=[self-hosted,Linux,X64]" in r.stderr, r.stderr
    assert "removed=[]" in r.stderr, f"a union must remove nothing here: {r.stderr}"


def test_a_version_pinned_hosted_label_is_dropped_never_made_permanent(tmp_path: Path):
    """⛔ THE UNION'S NAMED CARVE-OUT (a): `ubuntu-<version>` is DROPPED, not carried forward.

    A literal union makes every out-of-band version pin PERMANENT, and that reverses a recorded
    decision (not just a style choice): `docs/ops/ci-runner-x64.md:61` — "these runners
    deliberately **do not** claim any `ubuntu-<version>` label" — and `docs/ops/ci-runner-x64.md:70-71`,
    an out-of-band
    attachment "cannot survive a re-registration or `tools/install-ci-runner-x64.sh
    --repair-labels`". `tools/runner_autoscale.py::_preserve_labels` already drops the family
    (`VERSION_PINNED_HOSTED_LABEL`), so a union that kept it would leave the two writers with
    OPPOSITE policies for the same label set, both green.

    Why it matters — the label is what a leg pins to ESCAPE this pool: `test-slow`, `workflow-lint`,
    `finding-provenance` and `mirror-images` name it because they need the hosted image's PATH and
    a fresh egress IP. A runner serving it is not a queue delay, it is a FALSE PASS on a bare box
    (measured 2026-10-24: 38 of 39 `finding-provenance` failures on `pi-runner-vm-*`, exit 127,
    `gh: command not found` — `tests/test_runner_tool_contract.py:5-12`).

    ⛔ `ubuntu-24.04-arm` is the same class, not a near-miss: GitHub publishes it as a hosted label,
    so a job naming it escapes this pool exactly as a bare pin does. `ubuntu-latest` is NOT in the
    class (it carries no version) and is always kept.

    Non-vacuity: with the carve-out removed (the plain union at the previous head) both pins appear
    in the PUT and the set assertion reds.
    """
    stub, env = _repair_runner(tmp_path, pool="1", labels=POOL_LABELS)

    r = _run("--repair-labels", "--count", "1", env=env)
    assert r.returncode == 0, r.stderr
    put = _put_body(stub)
    assert set(put) == {"self-hosted-x64", "ubuntu-latest", "gpu", "ubuntu-2204-tools"}, (
        "a repair must never hand this pool a version-pinned HOSTED label: every job pinned to it "
        f"is then served by the pool it pinned to escape: {put}"
    )
    assert "ubuntu-24.04" not in put and "ubuntu-24.04-arm" not in put, put
    # the removal is PRINTED (a silent drop is the same defect wearing the other mask)…
    assert "version-pinned, not kept=[ubuntu-24.04,ubuntu-24.04-arm]" in r.stderr, r.stderr
    # …and the derived removal list agrees, so nothing else is being removed behind the carve-out
    assert "removed=[ubuntu-24.04,ubuntu-24.04-arm]" in r.stderr, r.stderr
    assert "alien-arch, not kept=[]" in r.stderr, r.stderr


def test_a_foreign_arch_marker_is_dropped_so_the_repair_still_heals(tmp_path: Path):
    """⛔ THE UNION'S NAMED CARVE-OUT (b): a `self-hosted-*` marker that is NOT this box's is DROPPED.

    `is_host_label` deliberately excludes `self-hosted-x64`/`self-hosted-arm64` (they are CUSTOM
    labels GitHub lets you remove), so a literal union leaves `self-hosted-arm64` on an x86 box —
    the measured pre-#2327 stamp (`docs/ops/ci-runner-x64.md:220-222`) — and the runbook still sells
    `--repair-labels` as the manual heal that "re-applies this arch's marker"
    (`docs/ops/ci-runner-x64.md:340-341`). Keeping
    the alien marker makes an arm64-specific job routable to an x86 box and produce a wrong
    artifact: a false PASS, not a queue delay (this file's own header).
    `tools/runner_autoscale.py::_preserve_labels` re-derives the arch marker rather than preserving
    any `self-hosted-*`, for exactly this reason.

    Non-vacuity: with the carve-out removed (the plain union at the previous head) `self-hosted-arm64`
    is in the PUT and `removed=[]` claims nothing was dropped.
    """
    stub, env = _repair_runner(
        tmp_path,
        pool="0",
        labels="self-hosted,Linux,X64,self-hosted-x64,self-hosted-arm64,ubuntu-latest",
    )

    r = _run("--repair-labels", "--count", "1", env=env)
    assert r.returncode == 0, r.stderr
    put = _put_body(stub)
    assert "self-hosted-arm64" not in put, (
        f"an x86 runner must never advertise the arm64 pool: {put}"
    )
    assert set(put) == {"self-hosted-x64", "ubuntu-latest"}, put
    assert "alien-arch, not kept=[self-hosted-arm64]" in r.stderr, r.stderr
    assert "removed=[self-hosted-arm64]" in r.stderr, r.stderr
    assert "version-pinned, not kept=[]" in r.stderr, r.stderr


@pytest.mark.parametrize("marker", ["Self-Hosted-Arm64", "SELF-HOSTED-ARM64"])
def test_a_mixed_case_foreign_arch_marker_is_dropped_too(tmp_path: Path, marker: str):
    """⛔ THE FOREIGN-ARCH CARVE-OUT IS CASE-INSENSITIVE, AND IT USED TO NOT BE.

    GitHub's own words, docs.github.com "apply labels": **"Labels are case-insensitive."** So a
    mixed-case spelling is not a different label — `Self-Hosted-Arm64` answers a lower-case
    `runs-on: self-hosted-arm64` exactly as the lower-case marker does, and the false PASS this
    carve-out exists to prevent (an arm64 job won by an x86 box, a wrong artifact) is unchanged by
    the spelling. The pre-fix pattern (`case "$1" in self-hosted-*)`) therefore let the marker
    escape: it was not `alien-arch, not kept`, not in `removed=`, and the PUT attached it — the
    repair reporting success over a false arch claim.

    Unlike `is_host_label`, case-insensitivity is CORRECT here and this arm is the boundary between
    the two: the host-owned names are vendor-fixed spellings (and filtering one of those WOULD be a
    removal), while `self-hosted-<arch>` is a CUSTOM label whose spelling is whatever created it.

    Non-vacuity: with the pre-fix exact-case pattern the marker survives into the PUT, so both the
    set assertion and the `removed=` line red.
    """
    stub, env = _repair_runner(
        tmp_path,
        pool="0",
        labels=f"self-hosted,Linux,X64,self-hosted-x64,{marker},ubuntu-latest",
    )

    r = _run("--repair-labels", "--count", "1", env=env)
    assert r.returncode == 0, r.stderr
    put = _put_body(stub)
    assert marker not in put, (
        f"a mixed-case arm64 marker is the SAME label to GitHub, so an x86 runner must not keep "
        f"it: {put}"
    )
    assert set(put) == {"self-hosted-x64", "ubuntu-latest"}, put
    assert f"alien-arch, not kept=[{marker}]" in r.stderr, r.stderr
    assert f"removed=[{marker}]" in r.stderr, r.stderr


def test_the_version_pin_carve_out_obeys_the_boxes_own_choice(tmp_path: Path):
    """⛔ THE VERSION-PIN CARVE-OUT IS A FLAG'S `off` STATE, NOT AN UNCONDITIONAL DROP.

    #6897's `OVERRIDES:` marker suspends the version-pin invariant and measures the departure as
    APPLIED (2026-10-29: 7 of 7 x64 runners carry `ubuntu-24.04`), and it records the REVERT as
    `CI_RUNNER_OS_LABEL=off` **plus** `--repair-labels`. A repair that dropped the pin whatever the
    flag said therefore reached that revert with NO SWITCH INVOLVED: the next manual heal would undo
    an owner departure the operator had opted this box into, printing a warning about a label the
    box was asked to claim. So the flag is read, under the same name and values the install-side
    contract uses (#10275), and the four arms below are the whole truth table:

      * `on`     → the pin is KEPT and printed as `version-pinned, kept=[…]`, `removed=[]`;
      * `auto`   → same (the box may claim one);
      * unset    → dropped (the pre-departure invariant `_preserve_labels` also enforces);
      * `yes`    → read as `off` — the invariant-consistent side, never a silent keep — and says so.

    Non-vacuity: with the flag ignored (an unconditional drop, the shape review rejected) the `on`
    arm reds on the PUT set and on the `kept=` line; with the flag inverted (an unconditional keep)
    the unset arm reds on `ubuntu-24.04` appearing in the PUT.
    """
    # `on` / `auto`: the box has opted in, so the pin it was asked to claim survives
    for value in ("on", "auto"):
        stub, env = _repair_runner(tmp_path / f"opt-{value}", pool="1", labels=POOL_LABELS)
        env["CI_RUNNER_OS_LABEL"] = value
        r = _run("--repair-labels", "--count", "1", env=env)
        assert r.returncode == 0, r.stderr
        put = _put_body(stub)
        assert {"ubuntu-24.04", "ubuntu-24.04-arm"} <= set(put), (
            f"CI_RUNNER_OS_LABEL={value} is the box's recorded state — a repair must not strip the "
            f"pin it opted into: {put}"
        )
        assert "version-pinned, kept=[ubuntu-24.04,ubuntu-24.04-arm]" in r.stderr, r.stderr
        assert "removed=[]" in r.stderr, f"nothing is removed when the box opted in: {r.stderr}"

    # unset (and an explicit `off`): the pre-departure invariant — the pin is dropped and printed
    for value in ("", "off"):
        stub, env = _repair_runner(tmp_path / f"off-{value or 'unset'}", pool="1", labels=POOL_LABELS)
        env["CI_RUNNER_OS_LABEL"] = value
        r = _run("--repair-labels", "--count", "1", env=env)
        assert r.returncode == 0, r.stderr
        put = _put_body(stub)
        assert not {"ubuntu-24.04", "ubuntu-24.04-arm"} & set(put), put
        assert "version-pinned, kept=[]" in r.stderr, r.stderr

    # an unrecognised value is READ AS off, and says so — never a silent keep
    stub, env = _repair_runner(tmp_path / "bogus", pool="1", labels=POOL_LABELS)
    env["CI_RUNNER_OS_LABEL"] = "yes"
    r = _run("--repair-labels", "--count", "1", env=env)
    assert r.returncode == 0, r.stderr
    assert "is not on|auto|off — read as off" in r.stderr, r.stderr
    assert not {"ubuntu-24.04", "ubuntu-24.04-arm"} & set(_put_body(stub))


def test_the_repair_refuses_an_arm64_runner_instead_of_keeping_ubuntu_latest_on_it(tmp_path: Path):
    """⛔ THE KEEP-HALF IS ARCH-BLIND, WHICH RE-CREATES #6897's OWN DEFECT ON ARM64.

    A repair can be run from any host and against any install, so a runner registered on an arm64
    box reaches this loop too. The union keeps `ubuntu-latest` ON PRINCIPLE — and on an arm64 runner
    that keep is the exact measured false red (an arm64 box wins an `ubuntu-latest` job and dies in
    "Set up job" on a codeload timeout, one shard per run), the reason the ARM pool was stripped in
    the first place. The read-only host label GitHub derives from the HOST (`ARM64`/`ARM`) is the one
    witness a caller cannot spoof, so such a runner is REFUSED (exit 3, nothing written) and the
    batch contract still holds: no PUT, no `relabelled`, and the message names the label.

    Non-vacuity: without the guard the arm PUTs `{ubuntu-latest,self-hosted-x64}` onto the arm64
    registration and exits 0 — measured on the pre-fix loop, `before=[…,self-hosted-arm64,
    ubuntu-latest]` → `after=[ubuntu-latest,self-hosted-x64]`, `removed=[self-hosted-arm64]`.
    """
    stub, env = _repair_runner(
        tmp_path, pool="1", labels="self-hosted,Linux,ARM64,self-hosted-arm64,ubuntu-latest"
    )
    r = _run("--repair-labels", "--count", "1", env=env)
    assert r.returncode == 3, (
        "an arm64 registration must be refused (exit 3 = operation failed), never relabelled as an "
        f"x86 runner: {r.returncode}\n{r.stderr}"
    )
    assert "host label 'ARM64'" in r.stderr, r.stderr
    log = (stub / "gh-calls.log").read_text()
    assert "PUT " not in log, f"nothing may be written to an arm64 runner: {log}"


def test_a_label_with_a_glob_character_is_refused_never_expanded_into_cwd_filenames(tmp_path: Path):
    """⛔ AN UNQUOTED LABEL SPLIT IS A PATHNAME EXPANSION, AND IT WROTE THE CWD INTO A RUNNER'S LABELS.

    Every loop that walks a label set expands `$(… | tr ',' ' ')` UNQUOTED, so a label carrying `*`
    is expanded against the operator's working directory before the loop sees it. The vendor's PUT
    schema declares no pattern — the `[A-Za-z0-9._-]` charset is server-side only — so nothing stops
    one arriving. Measured on the pre-fix loop, run from a directory holding two files:
    `current=…,DECOY-*` → `after=[self-hosted-x64,DECOY-1.txt,DECOY-2.log]`, `removed=[]`, PUT
    accepted: the operator's filenames became the runner's labels, and the block's own "a removal
    must be visible" guarantee could not show it.

    With the split literal the label reaches the illegal-label guard and REFUSES that runner (exit 3,
    no PUT). `DECOY-1.txt` is a legal label, which is what makes the injected form invisible to every
    other guard in the block.
    """
    (tmp_path / "DECOY-1.txt").write_text("decoy\n")
    (tmp_path / "DECOY-2.log").write_text("decoy\n")
    stub, env = _repair_runner(
        tmp_path, pool="1", labels="self-hosted,Linux,X64,self-hosted-x64,DECOY-*"
    )
    r = _run("--repair-labels", "--count", "1", env=env, cwd=tmp_path)
    assert r.returncode == 3, f"a glob-carrying label must refuse the runner: {r.returncode}\n{r.stderr}"
    assert "is not a legal runner label" in r.stderr, r.stderr
    log = (stub / "gh-calls.log").read_text()
    assert "PUT " not in log, f"no body may be built from an expanded label set: {log}"
    assert "DECOY-1.txt" not in log, f"the cwd leaked into the request: {log}"


def test_a_custom_label_that_only_looks_host_owned_survives_the_filter(tmp_path: Path):
    """⛔ `is_host_label` IS CASE-SENSITIVE ON PURPOSE, AND NOTHING TESTED IT.

    Filtering a label out of the PUT body IS a removal — the one thing this block must never do — so
    the host-owned filter matches GitHub's EXACT spellings only. A case-insensitive matcher (the
    obvious "cleanup", e.g. `tr '[:upper:]' '[:lower:]'` comparisons) would also swallow an
    operator's own `linux` or `x64` label and delete it from the runner on the next repair, silently:
    both are ordinary custom labels, and the filtered list is only PRINTED, never a refusal.

    Non-vacuity: with a case-insensitive `is_host_label` the PUT drops `linux` and the set assertion
    reds (measured line-neutral mutant: `case "$(printf '%s' "$1" | tr 'A-Z' 'a-z')"`).
    """
    stub, env = _repair_runner(
        tmp_path, pool="1", labels="self-hosted,Linux,X64,self-hosted-x64,linux,gpu"
    )
    r = _run("--repair-labels", "--count", "1", env=env)
    assert r.returncode == 0, r.stderr
    put = _put_body(stub)
    assert "linux" in put, f"a CUSTOM label spelled like a host label must survive: {put}"
    assert set(put) == {"self-hosted-x64", "ubuntu-latest", "linux", "gpu"}, put
    # only GitHub's own spellings are filtered, and they are printed rather than silently dropped
    assert "host-owned, not sent=[self-hosted,Linux,X64]" in r.stderr, r.stderr


def test_a_put_that_is_accepted_but_not_reflected_is_never_called_relabelled(tmp_path: Path):
    """⛔ `relabelled` MUST BE A CLAIM ABOUT THE SERVER, NOT ABOUT THE PUT'S EXIT STATUS.

    This pool has already reported `set_labels ok=16 fail=0` while the label it said it had attached
    was still absent (#6897) — the false PASS this whole block exists to prevent is precisely a write
    that is ACCEPTED and not applied. The Set verb's own 200 carries the runner's resulting label set,
    so the repair reads it back and requires every label it asked for to be in it; a mismatch is exit
    3 with the returned set in the warning, and NO `relabelled` line.

    Non-vacuity: with the read-back removed (the pre-fix `>/dev/null 2>&1` + exit status) this arm
    exits 0 and prints `relabelled …` over a label set the server did not reflect.
    """
    stub, env = _repair_runner(
        tmp_path,
        pool="1",
        labels="self-hosted,Linux,X64,self-hosted-x64,gpu",
        put_response="self-hosted,Linux,X64,self-hosted-x64",
    )
    r = _run("--repair-labels", "--count", "1", env=env)
    assert r.returncode == 3, (
        "an accepted PUT whose returned set is missing the labels must not read as success: "
        f"{r.returncode}\n{r.stderr}"
    )
    assert "relabelled" not in r.stderr, f"nothing was relabelled, so nothing may say so: {r.stderr}"
    assert "did NOT take effect" in r.stderr, r.stderr
    assert "gpu" in r.stderr and "ubuntu-latest" in r.stderr, r.stderr
    _put_body(stub)  # the PUT was attempted; only the CLAIM is withheld


def test_the_computed_set_is_never_empty_which_is_what_makes_the_empty_union_guard_dead():
    """⛔ THE EMPTY-UNION GUARD IS KEPT AND UNREACHABLE, AND THIS PINS THE PROPERTY THAT MAKES IT SO.

    The guard refuses to PUT `{"labels":[]}` — which the vendor documents as "remove all custom
    labels", i.e. the full strip this block exists to stop. Its condition is `after` empty, and
    `after ⊇ LABELS`, so it can only fire if the COMPUTED set is empty. `resolve_labels` always emits
    at least `$ARCH_LABEL` in every mode, so the guard is dead code at this head: a witness for it
    would have to fake a state the resolver cannot produce, which is why the honest test is this one.
    It is the check that makes the guard's presence (rather than its deletion) defensible, and it
    reds the moment a future edit lets `resolve_labels` emit nothing — which is the day the guard
    starts earning its line.
    """
    for mode in ("on", "off", "auto"):
        r = _run("--print-labels", "--fallback", mode)
        assert r.returncode == 0, f"--fallback {mode} must resolve: {r.stderr}"
        labels = [label for label in r.stdout.strip().split(",") if label]
        assert labels, f"--fallback {mode} computed an EMPTY label set: {r.stdout!r}"
        assert "self-hosted-x64" in labels, (mode, labels)


def test_a_computed_set_that_omits_the_label_cannot_remove_it(tmp_path: Path):
    """Case 2 (the negative case, and the one that blocked every PR).

    The computed set OMITS `ubuntu-latest` (the ARM64 pool does not carry it — it was stripped on
    the evidence in #6897 — so `--fallback auto` resolves to `self-hosted-x64` alone) and the label
    on the runner STILL survives, unchanged, because the repair unions instead of replacing.

    This is exactly the measured stall: the repair stripped the label from all 7 x64 runners, the
    REQUIRED `python-ci-gate` aggregate (`runs-on: ubuntu-latest`) never got a runner, and the run
    sat `pending` with ZERO materialised jobs — no red and no green on any PR head (runs
    40898791192 / 40901844458). A label restored by hand through the API was gone within ~25
    minutes, twice, because the next repair replaced the set again.

    Non-vacuity: restore the replace (`body` built from `$LABELS`, or skip the `current` loop) and
    the first assertion reds — `ubuntu-latest` is absent from the PUT.
    """
    stub, env = _repair_runner(tmp_path, pool="0", labels=POOL_LABELS)

    r = _run("--repair-labels", "--count", "1", env=env)
    assert r.returncode == 0, r.stderr
    computed = re.search(r"labels=([^\s]+)", r.stderr)
    assert computed and computed.group(1) == "self-hosted-x64", (
        f"this case is only meaningful while the computed set OMITS the label: {r.stderr}"
    )
    put = _put_body(stub)
    assert "ubuntu-latest" in put, f"a repair must not be able to remove the required gate's label: {put}"
    assert set(put) == {"self-hosted-x64", "ubuntu-latest", "gpu", "ubuntu-2204-tools"}, put
    # the before/after sets are printed BEFORE the PUT, so a removal is visible, never inferred
    assert (
        "before=[self-hosted,Linux,X64,self-hosted-x64,ubuntu-latest,ubuntu-24.04,ubuntu-24.04-arm,gpu,ubuntu-2204-tools]"
        in r.stderr
    ), r.stderr
    assert "after =[self-hosted-x64,ubuntu-latest,gpu,ubuntu-2204-tools]" in r.stderr, r.stderr
    before = re.search(r"before=\[(.*?)\]", r.stderr).group(1).split(",")
    after = re.search(r"after =\[(.*?)\]", r.stderr).group(1).split(",")
    # ⛔ Every label a runner carried survives EXCEPT the two carved-out classes — a version pin and
    # a foreign arch marker may go (that is the point), nothing else may.
    carved_out = {
        lab for lab in before if re.fullmatch(r"ubuntu-[0-9]+(?:\.[0-9]+)*(?:-arm)?", lab, re.IGNORECASE)
    }
    assert set(before) - {"self-hosted", "Linux", "X64"} - carved_out <= set(after), (
        f"no label outside the two named carve-outs may disappear: {before} → {after}"
    )
    assert "removed=[]" not in r.stderr, (
        f"the derivation must REPORT the carved-out removals rather than claim a clean union: {r.stderr}"
    )


def test_an_unreadable_label_set_is_refused_never_replaced(tmp_path: Path):
    """A failed label READ must not degrade into the old REPLACE.

    The tempting fallback — "the read failed, use the computed set" — is the exact defect behind a
    different door: it drops `ubuntu-latest` on a transport error, with only the error to show for
    it. So the repair refuses THIS RUNNER (nothing written) and never reaches the PUT.

    ⛔ THE CODE IS 3, NOT 2, and that is deliberate: 2 promises "nothing was downloaded, registered
    or removed", which a `--count N` batch cannot promise once an earlier runner's PUT has
    succeeded. The pre-fix loop met the analogous per-runner condition (`not found in the repo's
    runners`) with `say WARNING; rc=3; continue`, and the three guards inside this loop keep that
    shape (the batch arm below is the proof for the mixed case).

    Non-vacuity: the pre-union script had no label read at all, and the pre-fix-code control flow
    exits 0 here having PUT the computed set — the `returncode == 3` assertion reds on both.
    """
    stub, env = _repair_runner(tmp_path, pool="0", labels="")

    r = _run("--repair-labels", "--count", "1", env=env)
    assert r.returncode == 3, f"an unreadable label set must be a per-runner refusal: {r.returncode} {r.stderr}"
    assert "WARNING: cannot read the current labels" in r.stderr, r.stderr
    assert not re.search(r"PUT \{", (stub / "gh-calls.log").read_text()), "nothing may be written"


def test_a_per_runner_guard_skips_that_runner_and_the_batch_still_runs(tmp_path: Path):
    """⛔ A GUARD INSIDE THE REPAIR LOOP REFUSES **THIS RUNNER**, NEVER THE BATCH.

    `die` exits the whole script. With `--count 2`, runner 1's `PUT …/labels` can already have
    succeeded when runner 2 hits a guard — and the caller would receive exit 2, whose contract is
    "Nothing was downloaded, registered or removed", for a run that relabelled a runner. That is
    the shape `test_a_failed_delete_reports_an_operation_failure_not_a_clean_removal` exists to
    forbid on the destructive path, and it is why the loop's three guards say WARNING + `rc=3` +
    `continue` instead of `die`.

    Non-vacuity: with the guard still a `die … 2`, the returncode is 2 and runner 1's PUT is the
    last thing that happens — both assertions below red.
    """
    stub = _stub_gh(
        tmp_path / "gh",
        "0",
        labels="self-hosted,Linux,X64,self-hosted-x64,ubuntu-latest",
        # runner 2's label read comes back EMPTY — the unreadable-set guard
        detail_by_name={"pi-runner-x64-2": ""},
    )
    (tmp_path / ".runner").write_text('{"agentName": "pi-runner-x64-1"}\n')
    second = Path(str(tmp_path) + "-2")   # `runner_dir 2` is `$CI_RUNNER_BASE-2`
    second.mkdir()
    (second / ".runner").write_text('{"agentName": "pi-runner-x64-2"}\n')
    env = _env(tmp_path, PATH=f"{stub}:{os.environ['PATH']}", CI_RUNNER_ARCH="x86_64")

    r = _run("--repair-labels", "--count", "2", env=env)
    assert r.returncode == 3, f"a per-runner refusal is 3, never 2: {r.returncode} {r.stderr}"
    assert "relabelled pi-runner-x64-1" in r.stderr, r.stderr
    assert "WARNING: cannot read the current labels of pi-runner-x64-2" in r.stderr, r.stderr
    puts = re.findall(r"PUT (\{.*\})", (tmp_path / "gh" / "gh-calls.log").read_text())
    assert len(puts) == 1, f"runner 1 is relabelled, runner 2 is skipped: {puts}"
    assert "relabelled pi-runner-x64-2" not in r.stderr, r.stderr


def test_repair_refuses_fallback_off_because_a_union_cannot_detach(tmp_path: Path):
    """⛔ AN EXPLICIT `--fallback off` ON THE REPAIR PATH IS REFUSED, NOT ACCEPTED-AND-IGNORED.

    The union never subtracts, so this invocation cannot do the one thing it asks for. On a healthy
    hosted day the gate's state IS `off`, `resolve_labels` computes `self-hosted-x64` alone — and
    the path before this refusal kept `ubuntu-latest`, printed `removed=[]`, said `relabelled …` and
    exited 0. An operator following the runbook's "detach by hand" step was told the label was
    gone while it was still attached, which is the silently-wrong manual heal the runbook exists to
    prevent against. Exit 2 is the honest code and it is taken BEFORE any read or PUT, so its
    "nothing was changed" promise is literally true; the refusal names the verb that CAN detach.

    Non-vacuity: pre-refusal this invocation exits 0 and PUTs `{self-hosted-x64,ubuntu-latest}`, so
    the `returncode == 2` assertion reds.
    """
    stub, env = _repair_runner(
        tmp_path,
        pool="0",
        labels="self-hosted,Linux,X64,self-hosted-x64,ubuntu-latest",
    )

    refused = _run("--repair-labels", "--count", "1", "--fallback", "off", env=env)
    assert refused.returncode == 2, f"a union cannot detach: {refused.returncode} {refused.stderr}"
    assert "REFUSED" in refused.stderr and "--fallback off" in refused.stderr, refused.stderr
    assert "DELETE" in refused.stderr and "labels/ubuntu-latest" in refused.stderr, (
        f"the refusal must name the verb that can detach the label: {refused.stderr}"
    )
    log = stub / "gh-calls.log"
    assert not log.exists() or not re.search(r"PUT \{", log.read_text()), "nothing may be written"

    # `--fallback on` still repairs: the refusal covers only the direction a union cannot take.
    allowed = _run("--repair-labels", "--count", "1", "--fallback", "on", env=env)
    assert allowed.returncode == 0, allowed.stderr
    assert "ubuntu-latest" in _put_body(stub), _put_body(stub)


# ── 3c. `--status` answers "who answers this runs-on: label" ─────────────────────────────────
def test_status_counts_answering_runners_per_runs_on_label(tmp_path: Path):
    """The stalled gate produced NO check to look at, so the queue has to be READ, not inferred.

    `--status` counts, per `runs-on:` label the workflows actually use, how many ONLINE self-hosted
    runners answer it — and says so loudly when the answer is zero, because that is the state in
    which a job sits `pending` with zero materialised jobs while every PR waits.

    ⛔ HERMETIC: the installer is COPIED one directory down and the `runs-on:` surface it reads is
    written under that copy — the same shape as the two coverage arms below. Reading this repo's
    real `.github/workflows` made an INSTALLER test depend on the checkout: the day an unrelated
    workflow retargets its last `ubuntu-24.04` pin (a plausible change for a runner-capacity PR,
    which is exactly this area) the arm reds with a label-coverage message that has nothing to do
    with the installer — and it is the host/checkout coupling this module's contract forbids.
    """
    tools = tmp_path / "tools"
    tools.mkdir()
    copied = tools / "install-ci-runner-x64.sh"
    copied.write_text(Path(INSTALLER).read_text())
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text(
        "jobs:\n"
        "  gate:\n    runs-on: ubuntu-latest\n"
        "  slow:\n    runs-on: ubuntu-24.04\n"
    )

    def _status(stub: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [BASH, str(copied), "--status", "--count", "1"],
            capture_output=True,
            text=True,
            env=_env(
                tmp_path,
                PATH=f"{stub}:{os.environ['PATH']}",
                CI_RUNNER_UNIT_DIR=str(tmp_path / "units"),
            ),
        )

    carrying = "pi-runner-x64-1\tonline\tbusy=false\tself-hosted,Linux,X64,self-hosted-x64,ubuntu-latest"
    r = _status(_stub_gh(tmp_path / "carrying", "0", rows=carrying))
    assert r.returncode == 0, r.stderr
    assert re.search(r"^\s+ubuntu-latest → 1 online, 0 offline$", r.stderr, re.M), r.stderr
    assert re.search(r"^\s+ubuntu-24\.04 → 0 online, 0 offline$", r.stderr, re.M), r.stderr
    assert "NO RUNNER ANSWERS 'ubuntu-24.04'" in r.stderr, r.stderr
    assert "NO RUNNER ANSWERS 'ubuntu-latest'" not in r.stderr, r.stderr

    # …and with nothing carrying the gate's label, it is named — the measured stall, made visible
    stripped = "pi-runner-x64-1\tonline\tbusy=false\tself-hosted,Linux,X64,self-hosted-x64"
    r2 = _status(_stub_gh(tmp_path / "stripped", "0", rows=stripped))
    assert r2.returncode == 0, r2.stderr
    assert "NO RUNNER ANSWERS 'ubuntu-latest'" in r2.stderr, r2.stderr
    # ⛔ LABEL-SCOPED, not byte-for-byte: the summary line names EVERY uncovered label, so an
    # unrelated workflow edit must not be able to red this arm. Assert that the labels THIS arm is
    # about are named, and nothing about the rest.
    summary = re.search(r"NO online self-hosted answer for: (.*)$", r2.stderr, re.M)
    assert summary, r2.stderr
    named = set(summary.group(1).split())
    assert {"ubuntu-latest", "ubuntu-24.04"} <= named, (
        f"the summary must name each uncovered label; got {named}: {r2.stderr}"
    )

    # ⛔ THE COUNT IS AN EXACT LABEL MATCH, and this row is why: `ubuntu-latest-extra` is a
    # DIFFERENT label, so a substring/prefix comparison would report `ubuntu-latest → 1` and declare
    # the required gate covered by a runner that cannot answer it — a false green on the one surface
    # whose whole job is to be accountable for silence.
    lookalike = "pi-runner-x64-1\tonline\tbusy=false\tself-hosted,Linux,X64,self-hosted-x64,ubuntu-latest-extra"
    r3 = _status(_stub_gh(tmp_path / "lookalike", "0", rows=lookalike))
    assert r3.returncode == 0, r3.stderr
    assert re.search(r"^\s+ubuntu-latest → 0 online, 0 offline$", r3.stderr, re.M), r3.stderr
    assert "NO RUNNER ANSWERS 'ubuntu-latest'" in r3.stderr, r3.stderr


def test_label_coverage_makes_no_claim_when_it_cannot_read_the_workflows(tmp_path: Path):
    """⚠️ AN UNREAD SURFACE MUST NOT READ AS COVERED.

    The coverage block derives its label list from `.github/workflows` in the checkout the SCRIPT
    lives in. Run from a tree without them (a copied installer, a packaged deploy) it must say it
    read nothing — a "every label has an answer" over an empty surface is the same false-clean this
    block exists to remove, one level down. It must also still exit 0: this is a report, not a gate.
    """
    tools = tmp_path / "tools"
    tools.mkdir()
    copied = tools / "install-ci-runner-x64.sh"
    copied.write_text(Path(INSTALLER).read_text())

    carrying = "pi-runner-x64-1\tonline\tbusy=false\tself-hosted,Linux,X64,self-hosted-x64,ubuntu-latest"
    stub = _stub_gh(tmp_path / "pool", "0", rows=carrying)
    r = subprocess.run(
        [BASH, str(copied), "--status", "--count", "1"],
        capture_output=True,
        text=True,
        env=_env(tmp_path, PATH=f"{stub}:{os.environ['PATH']}", CI_RUNNER_UNIT_DIR=str(tmp_path / "units")),
    )
    assert r.returncode == 0, r.stderr
    assert "SKIPPED (this makes NO claim about any label)" in r.stderr, r.stderr
    assert "every one has an online self-hosted answer" not in r.stderr, r.stderr


def test_label_coverage_reads_yaml_workflows_too(tmp_path: Path):
    """⛔ A `.yaml` WORKFLOW MUST NOT READ AS COVERED.

    GitHub accepts both `.yml` and `.yaml`, and the reader globbed only `*.yml` — so a `.yaml`
    workflow's `runs-on:` was dropped while the block still printed "every one has an online
    self-hosted answer". The repo has 0 `.yaml` workflows today, which is exactly why nothing caught
    it: the block's own contract (`docs/ops/ci-runner-x64.md:229-231`) is that an unread label must
    never read as healthy.

    Non-vacuity: with the glob reverted to `*.yml` the `macos-14` row never appears and the summary
    claims full coverage off a surface it did not read.
    """
    tools = tmp_path / "tools"
    tools.mkdir()
    copied = tools / "install-ci-runner-x64.sh"
    copied.write_text(Path(INSTALLER).read_text())
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "legacy.yaml").write_text("jobs:\n  m:\n    runs-on: macos-14\n")
    (wf / "plain.yml").write_text("jobs:\n  p:\n    runs-on: ubuntu-latest\n")

    carrying = "pi-runner-x64-1\tonline\tbusy=false\tself-hosted,Linux,X64,self-hosted-x64,ubuntu-latest"
    stub = _stub_gh(tmp_path / "pool", "0", rows=carrying)
    r = subprocess.run(
        [BASH, str(copied), "--status", "--count", "1"],
        capture_output=True,
        text=True,
        env=_env(tmp_path, PATH=f"{stub}:{os.environ['PATH']}", CI_RUNNER_UNIT_DIR=str(tmp_path / "units")),
    )
    assert r.returncode == 0, r.stderr
    assert re.search(r"^\s+macos-14 → 0 online, 0 offline$", r.stderr, re.M), (
        f"a `.yaml` workflow's `runs-on:` must be READ, not silently dropped: {r.stderr}"
    )
    assert "NO RUNNER ANSWERS 'macos-14'" in r.stderr, r.stderr
    assert "every one has an online self-hosted answer" not in r.stderr, r.stderr


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


# ── 8a2. the REST of the runbook's installer refs (the map pin above reads ONE bullet) ─────────
def test_every_runbook_installer_ref_lands_on_the_line_it_names():
    """⛔ THE MAP PIN ABOVE READS THE FIRST BULLET; THE RUNBOOK CITES THE INSTALLER IN ALL OF THEM.

    `test_the_runbooks_installer_line_map_lands_on_what_it_names` starts at the first
    `install-ci-runner-x64.sh:NNN` and stops at the first blank line, so it pins ONE bullet of a
    section whose other bullets carry ~27 more bare `:NNN` refs — the same file, the same drift, and
    nothing watched them. Measured: the `--print-labels` bullet still said "the refusal itself dies
    at `:534`" after two commits had inserted 33 lines above it (`:534` is the comment `# every
    install.`, and the `die` is at `:567`) while the map bullet had been re-derived and stayed green.

    So every citation in the installer section is pinned here against what its sentence claims that
    line holds. The table is deliberately two-way: a citation this pin does not know is a FAIL (add
    it rather than leaving it unchecked), and an entry the runbook stopped citing is a FAIL too (the
    pin must not silently shrink). A number in `anchors` is therefore expected to move TOGETHER with
    the runbook when the installer shifts — re-derive both, which is exactly the manual step that
    was skipped. And because a number-keyed table cannot see a SWAP between two numbers it already
    knows, the claims in the sentence that broke are also re-checked from the sentence itself: a
    `MODE = …` block ref must land on that block's own `if [ "$MODE" = … ]` line, and "the refusal
    dies at `:NNN`" must land on a `die`, not on the comment that explains it.

    Non-vacuity (measured, each mechanism in isolation): the pre-fix pair `:529`/`:534` reds on the
    unknown-number check — "cites installer line(s) this pin does not know: [529, 534]". Supply table
    entries that ACCEPT those lines and the two sentence checks still catch it on their own: the
    `MODE = install` claim reds on `:529` (the `--print-labels` prelude comment) and the refusal
    claim reds on `:534` (`# every install.`). The same holds for a number this pin already knows:
    `:562` swapped for `:535` (the `--print-labels` block) reds only the sentence check.
    """
    runbook = (REPO_ROOT / "docs" / "ops" / "ci-runner-x64.md").read_text().splitlines()
    start = next(
        (i for i, line in enumerate(runbook) if "tools/install-ci-runner-x64.sh:" in line), None
    )
    assert start is not None, "the runbook no longer cites the installer — re-anchor this pin"
    end = next(
        (i for i in range(start + 1, len(runbook)) if runbook[i].startswith("## ")), len(runbook)
    )
    installer = INSTALLER.read_text().splitlines()

    # `install-ci-runner-x64.sh:740` AND the bare `:740` the section writes after the first one. The
    # lookbehind keeps `config.sh:76-81` (a DIFFERENT file, cited in the `--uninstall` paragraph) out.
    ref = re.compile(r"(?:install-ci-runner-x64\.sh|(?<![A-Za-z0-9_.\-])):(\d+)(?:-\d+)?")
    cited = {int(m.group(1)) for line in runbook[start:end] for m in ref.finditer(line)}
    assert cited, (
        "the runbook's installer section cites no installer line — re-anchor this pin rather than "
        "deleting it"
    )

    # cited number -> a regex the line at that number must match, i.e. what the sentence names
    anchors: dict[int, str] = {
        89: r"^# EXIT — the one distinction",  # "Exit codes (`:84-100`)"
        185: r"VALIDATED ONCE, BEFORE THE MODE DISPATCH",  # "--count is validated ONCE"
        218: r"^is_runner_install\(\) \{",  # "the proof is `is_runner_install` at"
        222: r"the fallback label: follow the gate's own state",  # "label resolution at"
        226: r"^fallback_engaged\(\) \{",
        237: r"^resolve_labels\(\) \{",
        266: r"label algebra: the UNION",  # "the union's label algebra at"
        290: r"^is_foreign_arch_label\(\) \{",  # "TWO NAMED CARVE-OUTS"
        308: r"label coverage: which",  # "the label-coverage block"
        328: r"^workflow_files\(\) \{",  # "read from BOTH `.yml` and `*.yaml`"
        365: r"^label_coverage\(\) \{",
        410: r"── status ──",  # "the `--status` block that calls it"
        445: r'MODE" = repair',  # "--repair-labels UNIONS" / "--fallback off is REFUSED"
        470: r"UNREADABLE LABEL SET IS A REFUSAL",  # "An unreadable current label set is a REFUSAL"
        574: r"`relabelled` IS A CLAIM ABOUT THE SERVER",  # "relabelled … is a claim about the server"
        606: r'MODE" = labels',  # "`--print-labels`"
        633: r'MODE" = install',  # "the two mode blocks" / "only an install"
        638: r'\|\| die "this installer is for x86_64',  # "the refusal itself dies at"
        641: r"FORK HEADROOM FIRST",  # "forks"
        661: r"DISK NEXT",  # "disk"
        668: r"CPU LAST",  # "cpu/memory"
        686: r'MODE" = uninstall',  # "`uninstall`" / "`--uninstall` deletes only what it can PROVE"
        811: r'^for i in \$\(seq 1 "\$COUNT"\); do',  # "the scale loop"
        822: r"SHA256_LINUX_X64",  # "sha256 pin" / "verify"
        825: r"registration-token",  # "token"
        830: r"set -- --unattended",  # "one argv"
        832: r'\./config\.sh "\$@"',  # "`config.sh`"
        842: r"svc\.sh install",  # "systemd install/start"
    }

    unknown = sorted(cited - anchors.keys())
    assert not unknown, (
        "the runbook cites installer line(s) this pin does not know — re-derive them against the "
        f"installer and add them to `anchors` rather than leaving them unchecked: {unknown}"
    )
    stale = sorted(anchors.keys() - cited)
    assert not stale, (
        "this pin still lists installer line(s) the runbook no longer cites — a pin over refs that "
        f"were re-derived or removed is pinning nothing: {stale}"
    )

    wrong: list[str] = []
    for n, expect in sorted(anchors.items()):
        assert 1 <= n <= len(installer), f"the runbook cites :{n}, past EOF ({len(installer)} lines)"
        if not re.search(expect, installer[n - 1]):
            actual = [i + 1 for i, line in enumerate(installer) if re.search(expect, line)]
            wrong.append(f":{n} is {installer[n - 1].strip()!r} (anchor is at {actual})")
    assert not wrong, (
        "the runbook cites an installer line that is not what the sentence names — re-derive the "
        "number (and this pin's entry) from the installer: " + "; ".join(wrong)
    )

    # ⛔ THE TABLE ABOVE IS KEYED BY NUMBER, SO IT CANNOT SEE A SWAP BETWEEN TWO NUMBERS IT ALREADY
    # KNOWS (both are cited somewhere, both land on something). The two claims in the broken
    # sentence are therefore re-checked from the SENTENCE, which is what a reader actually follows.
    region_text = "\n".join(runbook[start:end])

    # "the `MODE = install` block" must point at THAT block's own `if [ "$MODE" = … ]` line — the
    # failure this whole file warns about ("a fixer sent to :327 edits the capacity guard on the
    # destructive path") is exactly a mode ref landing one guard away.
    mode_refs = list(re.finditer(r"`MODE = (install|uninstall|repair|labels)`", region_text))
    assert mode_refs, "the runbook no longer names a `MODE = …` block — re-anchor this pin"
    for m in mode_refs:
        mode = m.group(1)
        before = re.findall(ref, region_text[max(0, m.start() - 120): m.start()])
        assert before, (
            f"the runbook's `MODE = {mode}` sentence cites no installer line — re-anchor this pin"
        )
        n = int(before[-1])
        assert re.search(rf'^if \[ "\$MODE" = {mode} \]', installer[n - 1]), (
            f"the runbook sends a reader to :{n} for the `MODE = {mode}` block, but that line is "
            f"{installer[n - 1].strip()!r} — re-derive the number for THAT block"
        )

    # "the refusal itself dies at `:NNN`" must point at the `die` that refuses, not at a comment
    # explaining it (the pre-fix `:534` was `# every install.`).
    dies = list(re.finditer(r"dies at `:(\d+)`", region_text))
    assert dies, "the runbook no longer points at the arch refusal — re-anchor this pin"
    for m in dies:
        n = int(m.group(1))
        assert 1 <= n <= len(installer), f"the runbook cites :{n}, past EOF ({len(installer)} lines)"
        assert re.search(r"\bdie\b", installer[n - 1]), (
            f"the runbook says the refusal 'dies at :{n}', but that line is "
            f"{installer[n - 1].strip()!r} — a comment, not the refusal"
        )


# ── 8b. the citations the runbook's map does NOT cover ──────────────────────────────────────
def test_every_installer_citation_outside_the_runbook_lands_on_the_symbol_it_names():
    """⛔ THE MAP PIN ABOVE COVERS ONE BULLET; THE REST OF THE TREE CITES THE INSTALLER TOO.

    `tools/runner_autoscale.py`, `tests/test_runner_autoscale.py` and `MEMORY.md` all point at
    installer lines by NUMBER and name the symbol in the same sentence (`die()`, `resolve_labels`,
    the repair's whole-set relabel). Two reviews found those wrong — `:153` was `-h|--help` and
    `:208` was `fallback_engaged()`, a DIFFERENT seam — while every pinned ref in the runbook still
    landed, so nothing went red. Every installer edit above them shifts them again; that is exactly
    the class this arm makes red instead of silent.

    Non-vacuity: pointing either `die()` citation back at `:153` (the help flag) reds it, and so
    does leaving the repair-range citation on the pre-PR `:241-256` (the tail of `resolve_labels`).
    """
    installer = INSTALLER.read_text().splitlines()

    def land(file: str, n: int, expect: str) -> None:
        assert 1 <= n <= len(installer), f"{file} cites :{n}, past EOF ({len(installer)} lines)"
        line = installer[n - 1]
        assert re.search(expect, line), (
            f"{file} cites install-ci-runner-x64.sh:{n} but that line is {line.strip()!r}, not the "
            "symbol the sentence names — re-derive the number"
        )

    # The citations in the two files, and the symbol the sentence around them names. The `:NNN`
    # sometimes sits on the NEXT line (`install-ci-runner-x64.sh::resolve_labels` then ``:232``), so
    # the window is two lines each side rather than the cited line alone.
    known = {"die()": r"^die\(\)", "resolve_labels": r"^resolve_labels\(\)"}
    for rel in ("tools/runner_autoscale.py", "tests/test_runner_autoscale.py"):
        lines = (REPO_ROOT / rel).read_text().splitlines()
        cited = 0
        for i, line in enumerate(lines):
            for m in re.finditer(r"install-ci-runner-x64\.sh:(\d+)", line):
                window = "\n".join(lines[max(0, i - 2): i + 3])
                named = [rx for key, rx in known.items() if key in window]
                assert named, (
                    f"{rel}:{i + 1} cites install-ci-runner-x64.sh:{m.group(1)} but names no symbol "
                    f"this pin knows — add it to `known` rather than leaving the ref unchecked: "
                    f"{line.strip()}"
                )
                n = int(m.group(1))
                land(f"{rel}:{i + 1}", n,
                     named[0] if len(named) == 1 else f"(?:{'|'.join(named)})")
                cited += 1
        assert cited, f"{rel} no longer cites an installer line — re-anchor this pin"

    # `MEMORY.md` cites the REPAIR's range (the whole-set relabel that drops a version pin).
    memory = (REPO_ROOT / "MEMORY.md").read_text()
    repair = re.search(r"whole-set relabel \(`:(\d+)-\d+`\)", memory)
    assert repair, "MEMORY.md no longer cites the repair's line range — re-anchor this pin"
    land("MEMORY.md", int(repair.group(1)), r'^if \[ "\$MODE" = repair \]')


# ── 8c. the installer's OWN self-citations ──────────────────────────────────────────────────
def test_the_installers_own_self_citations_land_on_the_line_they_name():
    """⛔ THE INSTALLER CITES ITSELF, AND THOSE REFS WERE THE SAME WRONG-TARGET CLASS.

    Two refs inside `tools/install-ci-runner-x64.sh` point at its own lines by number, and both
    pointed at the wrong one at this head: "the false PASS this file forbids at `:42-46`" (`:42-46`
    is the OVERRIDES union/refusal paragraph; the arch rule is `:62-69`) and "`set -o pipefail` is
    on (`:77`)" (`:77` is a `--print-labels` USAGE row; `set -uo pipefail` is `:101`). Neither is
    covered by any other pin — an operator sent to `:42-46` for the rule that a foreign arch marker
    is a false PASS reads a paragraph about the fallback label instead.

    Pinned here the way the cross-file pin does it: the citing COMMENT PARAGRAPH names the
    construct, and the line it points at must contain it. A self-citation whose paragraph names
    nothing in `known` FAILS (add the fragment) and a `known` fragment nothing cites FAILS (the pin
    must not silently shrink).

    Non-vacuity: the pre-fix pair reds both ways — `:42-46` is not the `ARCH LABELS ARE NOT
    INTERCHANGEABLE` block, and `:77` is not `set -uo pipefail`.
    """
    src = INSTALLER.read_text().splitlines()

    # (a fragment of the citing comment paragraph, a regex the CITED line must match)
    known: list[tuple[str, str]] = [
        ("the false PASS this file forbids", r"ARCH LABELS ARE NOT INTERCHANGEABLE"),
        ("`set -o pipefail` is on", r"^set -uo pipefail$"),
    ]

    # contiguous `#` runs (blank lines and code end a paragraph, which is what keeps a phrase from
    # claiming a citation in an unrelated block further down)
    blocks: list[tuple[int, int, str]] = []
    first: int | None = None
    for i, line in enumerate([*src, ""]):
        if line.lstrip().startswith("#"):
            if first is None:
                first = i
        elif first is not None:
            blocks.append((first, i, "\n".join(src[first:i])))
            first = None

    def block_of(idx: int) -> str:
        for begin, stop, text in blocks:
            if begin <= idx < stop:
                return text
        return src[idx]

    ref = re.compile(r"(?<![A-Za-z0-9_.\-]):(\d+)(?:-\d+)?")
    seen = 0
    used: set[int] = set()
    for i, line in enumerate(src):
        if not line.lstrip().startswith("#"):
            continue
        for m in ref.finditer(line):
            n = int(m.group(1))
            paragraph = block_of(i)
            claimed = [k for k, (frag, _) in enumerate(known) if frag in paragraph]
            assert claimed, (
                f"install-ci-runner-x64.sh:{i + 1} cites its own line :{n} but its paragraph names "
                "no construct this pin knows — add the sentence fragment to `known` rather than "
                f"leaving the ref unchecked: {paragraph!r}"
            )
            assert 1 <= n <= len(src), (
                f"install-ci-runner-x64.sh:{i + 1} cites :{n}, past EOF ({len(src)} lines)"
            )
            landed = [k for k in claimed if re.search(known[k][1], src[n - 1])]
            assert landed, (
                f"install-ci-runner-x64.sh:{i + 1} cites install-ci-runner-x64.sh:{n} as "
                f"{known[claimed[0]][0]!r}, but that line is {src[n - 1].strip()!r} — re-derive the "
                "number"
            )
            used.update(claimed)
            seen += 1

    assert seen, "the installer no longer cites its own lines — re-anchor this pin"
    silent = sorted(k for k in range(len(known)) if k not in used)
    assert not silent, (
        "a self-citation this pin lists is gone from the installer — a pin over refs that were "
        f"re-worded or removed is pinning nothing: {[known[k][0] for k in silent]}"
    )


# ── 8d. the label assertion FIVE OTHER FILES send a reader to ───────────────────────────────
def test_the_label_assertion_other_files_cite_lands_on_it():
    """⛔ THIS FILE'S OWN LINE NUMBERS ARE CITED FROM OUTSIDE IT, AND NOTHING PINNED THEM.

    Four fleet workflows and the mirror test in `tests/test_reporting_workflow_concurrency.py` send a
    reader to `tests/test_ci_runner_x64_install.py:NNN` for the assertion that the installer never
    hands `config.sh` a foreign arch marker or a version pin. This PR's module docstring grew, the
    assertion moved and the old number became a BLANK LINE — so all five comments then pointed at
    nothing, and none of the three pins above could see them: all three read INSTALLER refs. A
    blank-line target is worse than a stale one, because the reader concludes the assertion is gone.

    Two-way over the scanned surface, like the installer pins: a citing file this pin does not know
    FAILS (add it rather than leaving it unchecked), a known site that stopped citing FAILS (the pin
    must not silently shrink), and every citation must land on the ONE line that makes the claim and
    agree with the others. The scan is bounded to `.github/workflows` (the surface carrying these
    comments) plus the mirror test — globbing every file under `tests/` for this class would read
    ~48 MB per run and cover nothing further.

    Non-vacuity (measured): the pre-fix refs red on the landing check — the old number is an empty
    line — and re-pointing only SOME of the five reds on the agreement check.
    """
    here = Path(__file__).read_text().splitlines()
    assertion = r'^\s*for alien in \("self-hosted-arm64", "ARM64", "ubuntu-24\.04", "ubuntu-22\.04"\):$'

    known = {
        ".github/workflows/availability-liveness.yml",
        ".github/workflows/availability-watchdog.yml",
        ".github/workflows/ci-check-repair.yml",
        ".github/workflows/ci-queue-health.yml",
        "tests/test_reporting_workflow_concurrency.py",
    }
    scanned = [
        *(REPO_ROOT / ".github" / "workflows").glob("*.yml"),
        *(REPO_ROOT / ".github" / "workflows").glob("*.yaml"),
        REPO_ROOT / "tests" / "test_reporting_workflow_concurrency.py",
    ]
    ref = re.compile(r"tests/test_ci_runner_x64_install\.py:(\d+)")
    cited: dict[str, list[int]] = {}
    for path in sorted(scanned):
        assert path.is_file(), f"the pin's own scan surface is missing: {path}"
        rel = str(path.relative_to(REPO_ROOT))
        for line in path.read_text().splitlines():
            for m in ref.finditer(line):
                cited.setdefault(rel, []).append(int(m.group(1)))

    unknown = sorted(set(cited) - known)
    assert not unknown, (
        "file(s) cite this test file's line numbers and this pin does not know them — add them to "
        f"`known` rather than leaving the refs unchecked: {unknown}"
    )
    stale = sorted(known - set(cited))
    assert not stale, (
        "this pin still lists file(s) that no longer cite this test file — a pin over refs that "
        f"were re-worded or removed is pinning nothing: {stale}"
    )

    lands = [i + 1 for i, line in enumerate(here) if re.match(assertion, line)]
    assert len(lands) == 1, (
        f"expected exactly one alien-label assertion in this file, found {lands} — re-anchor this "
        "pin to the assertion those five comments describe"
    )
    hits = {n for numbers in cited.values() for n in numbers}
    assert hits == set(lands), (
        f"{sorted(cited)} cite tests/test_ci_runner_x64_install.py:{sorted(hits)} as the alien-label "
        f"assertion, but it lives at {lands} — re-point every citation (they must also agree with "
        "each other, or the mirror has silently diverged)"
    )


# ── 8e. this file's citations INTO the runbook ──────────────────────────────────────────────
def test_every_runbook_citation_in_this_file_lands_on_what_it_names():
    """⛔ THE RUNBOOK IS EDITED OFTEN; THIS FILE'S CITATIONS INTO IT WERE NEVER PINNED.

    The installer→runbook direction has two pins above. The runbook→THIS-FILE direction had none,
    which is how `test_a_foreign_arch_marker_is_dropped_so_the_repair_still_heals` came to quote the
    runbook's "the manual heal that re-applies this arch's marker" while citing two lines that are a
    row of the measurement table and the `## Known residuals` heading. Four docstrings cite the
    runbook; the whole set is pinned here, two-way, so the next runbook edit reds instead of leaving
    a quotation whose citation points somewhere else.

    Non-vacuity (measured): with the pre-fix number the landing check reds on its own — those two
    lines are a `test (c)` row of the measurement table and the `## Known residuals` heading, and
    neither says "re-applies".
    """
    here = Path(__file__).read_text().splitlines()
    doc = (REPO_ROOT / "docs" / "ops" / "ci-runner-x64.md").read_text().splitlines()

    # cited start line -> a regex the CITED RANGE must match, i.e. the sentence quoted here
    anchors: dict[int, str] = {
        61: r"deliberately \*\*do not\*\* claim any `ubuntu-<version>` label",
        70: r"an out-of-band attachment cannot survive",
        220: r"A foreign arch marker",
        229: r'answers "who answers this `runs-on:` label"',
        340: r"install-ci-runner-x64\.sh --repair-labels` re-applies\s+this arch's marker",
    }

    ref = re.compile(r"docs/ops/ci-runner-x64\.md:(\d+)(?:-(\d+))?")
    cited: dict[int, int] = {}
    for line in here:
        for m in ref.finditer(line):
            start = int(m.group(1))
            cited[start] = int(m.group(2) or start)
    assert cited, "this file no longer cites the runbook — re-anchor this pin rather than deleting it"

    unknown = sorted(set(cited) - anchors.keys())
    assert not unknown, (
        "this file cites runbook line(s) the pin does not know — re-derive them against the runbook "
        f"and add them to `anchors` rather than leaving them unchecked: {unknown}"
    )
    stale = sorted(anchors.keys() - set(cited))
    assert not stale, (
        "this pin still lists runbook line(s) nothing here cites — a pin over refs that were "
        f"re-worded or removed is pinning nothing: {stale}"
    )

    wrong: list[str] = []
    for start, end in sorted(cited.items()):
        assert 1 <= start <= end <= len(doc), (
            f"cites docs/ops/ci-runner-x64.md:{start}-{end}, past EOF ({len(doc)} lines)"
        )
        cited_text = "\n".join(doc[start - 1:end])
        if not re.search(anchors[start], cited_text):
            actual = [
                i + 1
                for i, line in enumerate(doc)
                if re.search(anchors[start], line) or re.search(anchors[start].replace(r"\s+", " "), line)
            ]
            wrong.append(f":{start}-{end} is {cited_text.strip()!r} (the sentence is at {actual})")
    assert not wrong, (
        "this file quotes the runbook but cites a line range that does not say it — re-derive the "
        "number from the runbook at this head: " + "; ".join(wrong)
    )


# ── 8. a missing installer is a FAIL, not a silent skip ─────────────────────────────────────
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


# ── 8f. the `CI_RUNNER_OS_LABEL` writers: one reader, ZERO writers, said plainly ────────────
def test_the_os_label_flag_is_described_as_one_reader_and_no_writer():
    """⛔ THREE ARTIFACTS ASSERTED A WRITER THAT DOES NOT EXIST — AND THE FLEET IS THE COST.

    `CI_RUNNER_OS_LABEL` guards the version-pin carve-out: `on|auto` KEEPS `ubuntu-24.04`,
    `off`/unset DROPS it. Three places described the live pool as `on|auto` — the runbook's
    "the live pool's own state", its section preamble's "BOTH writers read it", and the installer
    header's "cannot revert an APPLIED owner departure with no switch involved". Measured at the
    head this pin was written: `git grep -n CI_RUNNER_OS_LABEL` finds ONE reader (this installer's
    repair `case`) and ZERO writers — the install-side emitter is open PR #10275 — so an unset flag
    resolves to `off` and a plain `--repair-labels` on the live pool (7 of 7 x64 runners carrying
    `ubuntu-24.04` on 2026-10-29) performs #6897's recorded revert. An operator who read the old
    sentences would run it believing the pin was protected.

    Two-way over the two artifacts a lane or an operator reads (the runbook and the installer
    header): a claim that a second writer exists FAILS, and the corrective disclosure must be
    PRESENT — so deleting the paragraph instead of restating it reds too. The whitespace is
    normalised before the match: these are wrapped prose, and `installer`-style wrapping must not
    decide whether a claim is visible.

    ⛔ AND THE SAME CLAIM WAS REACHED ONE STEP FURTHER: a statement about the LIVE UNIT's own
    environment. "what this fleet's unit has today" (installer header), "so nothing exports it
    there" and "nothing exports it yet" (runbook) assert what `runner-autoscale.service` carries,
    which no checkout can read — the reviewer's remaining item on this is explicitly UNVERIFIED
    without `systemctl show -p Environment runner-autoscale.service`, which is the check the runbook
    itself prints. The tree can only speak for the TREE, so the sentences must be tree-scoped ("this
    tree exports none", "the unit is not readable from here"); asserting the box's state is how the
    page sent an operator to a heal that reverts #6897's departure while saying it was protected.

    ⛔ AND TWICE THE PHRASE LIST WAS CLOSED ON THE SENTENCES, NOT THE CLASS (review of `4cfdb8a72`,
    P2-1). The section preamble — the artifact an operator reads FIRST, and the one that draws the
    operational conclusion — said "the flag is unset on every box here and every row below that says
    `off`/unset is the live one", which is the same unreadable claim in fresh words, 150 lines above
    the page's own "CANNOT BE READ OFF THIS PAGE" and its "Re-measure, never assume" block. A
    blacklist cannot close a class on its own, so it is closed from BOTH ends: the surviving
    phrasings are in `forbidden` below, AND the tree-scoped disclosure is in `required` — deleting
    the paragraph instead of restating it reds too. The external take (rung 3, GitHub REST docs for
    self-hosted runners) is why the fix is a MEASUREMENT pointer and not a different assertion: a
    runner's labels and its registered state are SERVER-side, read back through
    `gh api repos/<owner>/<repo>/actions/runners`, so no checkout can hold them.

    Non-vacuity (measured): with the pre-fix wording restored, "BOTH writers read it", "the live
    pool's own state", "cannot revert an APPLIED owner departure with no switch involved", "— what
    this fleet's", "so nothing exports it there", "nothing exports it yet", "unset on every box" and
    "every row below that says" each red on their own; deleting the required disclosure reds on
    "not a fact this page holds".
    """
    def flat(text: str) -> str:
        return re.sub(r"\s+", " ", text)

    runbook = flat((REPO_ROOT / "docs" / "ops" / "ci-runner-x64.md").read_text())
    installer = flat(INSTALLER.read_text())

    forbidden = {
        "BOTH writers read it": "the tree has ONE reader (this repair) and nothing writes the flag",
        "the live pool's own state": "nothing exports the flag, so unset is the state to assume",
        "the live fleet is opted in": "the pins were attached WITHOUT the switch",
        "cannot revert an APPLIED owner departure with no switch involved": (
            "unset IS `off`, so a plain repair IS #6897's recorded revert"
        ),
        "— what this fleet's": (
            "the unit's own `Environment` is not readable from a checkout, so the page must tell the "
            "operator to MEASURE it (`systemctl show -p Environment runner-autoscale.service`) "
            "instead of asserting what it carries"
        ),
        "so nothing exports it there": (
            "the tree exports none; whether the unit does is a measurement, not a fact this page holds"
        ),
        "nothing exports it yet": (
            "the tree exports none; whether the unit does is a measurement, not a fact this page holds"
        ),
        "unset on every box": (
            "a checkout cannot read a box's unit — `systemctl show -p Environment "
            "runner-autoscale.service` is the measurement, so the page may only say what the TREE "
            "exports"
        ),
        "every row below that says": (
            "which row is LIVE is per-box state, so the page may only say which row an unexported "
            "repair takes — the fleet is read back through `gh api …/actions/runners` (I took this "
            "from the REST docs for self-hosted runners: registered labels are server-side state)"
        ),
    }
    for artifact, text in (("docs/ops/ci-runner-x64.md", runbook), ("tools/install-ci-runner-x64.sh", installer)):
        for claim, why in forbidden.items():
            assert claim not in text, (
                f"{artifact} claims {claim!r} about `CI_RUNNER_OS_LABEL`, but {why} — a repair run "
                "with the flag unset DROPS the version pin, so that sentence would send an operator "
                "to a heal that reverts an applied owner departure believing it was protected"
            )

    required = {
        "tools/install-ci-runner-x64.sh": (
            installer,
            ("and nothing WRITES it", "Export `CI_RUNNER_OS_LABEL=on` to KEEP one"),
        ),
        "docs/ops/ci-runner-x64.md": (
            runbook,
            (
                "and nothing WRITES it",
                "has ONE reader in this tree (this repair) and NO writer",
                "nothing in this tree exports it",
                "not a fact this page holds",
            ),
        ),
    }
    for artifact, (text, fragments) in required.items():
        for fragment in fragments:
            assert fragment in text, (
                f"{artifact} no longer states {fragment!r} — the flag has one reader and zero "
                "writers in this tree (the install-side emitter is open PR #10275), and that is the "
                "fact an operator needs before running the repair"
            )


# ── 8g. the carve-out lead-in promises the writer roles instead of trailing off ───────────────
def test_the_carveout_lead_in_names_the_writer_roles_instead_of_trailing_off():
    """⛔ THE LEAD-IN ENDED AT A DANGLING COLON, AT THE EXACT POINT IT PROMISED TWO WRITERS AGREE.

    Review of `4cfdb8a72` (P3-2). `docs/ops/ci-runner-x64.md`'s carve-out bullet read:

        … and both are what `_preserve_labels` drops on re-registration, except that it reads NO
        flag yet: **ONE writer here:**

    The clause stops at a colon with nothing after it, and the numbered list that follows is NOT
    its continuation (it starts with `1. **\`ubuntu-<version>\`**`), so a reader of the carve-out
    contract is left with a dangling referent exactly where the two writers are being promised to
    agree. The installer header carries the complete sentence
    (`tools/install-ci-runner-x64.sh:58`), so this is leftover text from the `c725ab7f5` rewrite,
    not a deliberate omission.

    WHAT IS PINNED, and why it is not another phrase blacklist. Three properties of the LEAD-IN
    PARAGRAPH itself (from the "TWO NAMED CARVE-OUTS" bullet to the first numbered item):
      1. it is not TRUNCATED — a lead-in whose last character is `:` promises a continuation the
         numbered list does not carry;
      2. it still states the ROLE (`reads`/`reading` NO flag) — the fact an operator needs, and
         the one a "fix" that simply deletes the dangling clause would silently drop;
      3. it NAMES the missing writer (`#10275`) — the dangling clause promised a writer and named
         none, which is how it survived a pin that only checked the flag's reader count.

    Non-vacuity (measured at 9dc9d727f, each mutation restored afterwards): with the pre-fix lead-in
    restored the truncation check reds on its own; deleting `#10275` reds on its own; deleting the
    `reading NO flag` clause reds on its own.
    """
    doc = (REPO_ROOT / "docs" / "ops" / "ci-runner-x64.md").read_text().splitlines()

    starts = [i for i, line in enumerate(doc) if line.startswith("- **⚠️ TWO NAMED CARVE-OUTS")]
    assert len(starts) == 1, f"expected exactly one carve-out bullet, found {len(starts)} — re-anchor this pin"
    start = starts[0]
    numbered = [i for i in range(start, len(doc)) if re.match(r"^\s+1\. ", doc[i])]
    assert numbered, "the carve-out bullet no longer carries a numbered list — re-anchor this pin"
    lead_in = re.sub(r"\s+", " ", " ".join(doc[start:numbered[0]])).strip()

    assert not lead_in.endswith(":"), (
        "the carve-out lead-in ends at a colon with nothing after it — the numbered list below is "
        f"not its continuation, so the clause is truncated: {lead_in[-120:]!r}"
    )
    assert re.search(r"read(?:s|ing)? NO flag", lead_in), (
        "the carve-out lead-in no longer states that `_preserve_labels` reads NO flag — that role "
        f"statement is the operator-visible fact the clause carried: {lead_in[-160:]!r}"
    )
    assert "#10275" in lead_in, (
        "the carve-out lead-in no longer NAMES the missing writer (#10275) — a clause that promises "
        f"a writer and names none is what dangled in the first place: {lead_in[-160:]!r}"
    )
