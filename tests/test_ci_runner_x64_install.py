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
    """A hermetic installer environment: local base dir, neutral headroom, no host state."""
    env = dict(os.environ)
    env.update(_NEUTRAL_HEADROOM)
    env.update({"CI_RUNNER_REPO": "daniel-ospina/tortoise", "CI_RUNNER_BASE": str(base)})
    env.update(extra)
    return env


def _run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [BASH, str(INSTALLER), *args], capture_output=True, text=True, env=env or _env(Path("/nonexistent-base"))
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
) -> Path:
    """A `gh` that answers all three reads the installer makes, and logs every call.

    The three answers are dispatched on the jq expression, the only thing that distinguishes them:
      * `busy=` → the `--status` runner rows (`rows`);
      * `select(.name==` → the repair's `id<TAB>label,label,…` detail line (`labels`);
      * anything else → the ARM64 pool's fallback count (`stdout`, the existing argument).
    A `-X PUT` also records the request BODY it was fed on stdin, so a test can assert the exact
    label set a repair would write — not a message about it. `labels` defaults to the read-only trio
    GitHub itself returns for any runner, which is also what makes the union's host-label filter
    observable rather than exercised on an impossible empty list.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    log = tmp_path / "gh-calls.log"
    gh = bin_dir / "gh"
    detail = f"7\t{labels}"
    gh.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> {log}\n'
        "case \"$*\" in\n"
        f"  *'-X PUT'*) printf 'PUT %s\\n' \"$(cat)\" >> {log} ;;\n"
        "esac\n"
        "case \"$*\" in\n"
        f"  *'busy='*) printf '%s\\n' {shlex.quote(rows)} ;;\n"
        f"  *'select(.name=='*) printf '%s\\n' {shlex.quote(detail)} ;;\n"
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


def _repair_runner(tmp_path: Path, *, pool: str, labels: str) -> tuple[Path, dict[str, str]]:
    """An already-configured runner 1 whose CURRENT labels are `labels`, plus the stub env."""
    stub = _stub_gh(tmp_path / "gh", pool, labels=labels)
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
    deliberately **do not** claim any `ubuntu-<version>` label" — and `:65-66`, an out-of-band
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
    the measured pre-#2327 stamp (`docs/ops/ci-runner-x64.md:258-263`) — and the runbook still sells
    `--repair-labels` as the manual heal that "re-applies this arch's marker" (`:275-276`). Keeping
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
    it. So the repair refuses (exit 2, nothing written) and never reaches the PUT.
    """
    stub, env = _repair_runner(tmp_path, pool="0", labels="")

    r = _run("--repair-labels", "--count", "1", env=env)
    assert r.returncode == 2, f"an unreadable label set must be a GUARD refusal: {r.returncode} {r.stderr}"
    assert "REFUSED" in r.stderr and "cannot read the current labels" in r.stderr, r.stderr
    assert not re.search(r"PUT \{", (stub / "gh-calls.log").read_text()), "nothing may be written"


# ── 3c. `--status` answers "who answers this runs-on: label" ─────────────────────────────────
def test_status_counts_answering_runners_per_runs_on_label(tmp_path: Path):
    """The stalled gate produced NO check to look at, so the queue has to be READ, not inferred.

    `--status` counts, per `runs-on:` label the workflows actually use, how many ONLINE self-hosted
    runners answer it — and says so loudly when the answer is zero, because that is the state in
    which a job sits `pending` with zero materialised jobs while every PR waits.
    """
    carrying = "pi-runner-x64-1\tonline\tbusy=false\tself-hosted,Linux,X64,self-hosted-x64,ubuntu-latest"
    stub = _stub_gh(tmp_path / "carrying", "0", rows=carrying)
    env = _env(
        tmp_path,
        PATH=f"{stub}:{os.environ['PATH']}",
        CI_RUNNER_UNIT_DIR=str(tmp_path / "units"),
    )
    r = _run("--status", "--count", "1", env=env)
    assert r.returncode == 0, r.stderr
    assert re.search(r"^\s+ubuntu-latest → 1 online, 0 offline$", r.stderr, re.M), r.stderr
    assert re.search(r"^\s+ubuntu-24\.04 → 0 online, 0 offline$", r.stderr, re.M), r.stderr
    assert "NO RUNNER ANSWERS 'ubuntu-24.04'" in r.stderr, r.stderr
    assert "NO RUNNER ANSWERS 'ubuntu-latest'" not in r.stderr, r.stderr

    # …and with nothing carrying the gate's label, it is named — the measured stall, made visible
    stripped = "pi-runner-x64-1\tonline\tbusy=false\tself-hosted,Linux,X64,self-hosted-x64"
    stub2 = _stub_gh(tmp_path / "stripped", "0", rows=stripped)
    r2 = _run(
        "--status",
        "--count",
        "1",
        env=_env(tmp_path, PATH=f"{stub2}:{os.environ['PATH']}", CI_RUNNER_UNIT_DIR=str(tmp_path / "units")),
    )
    assert r2.returncode == 0, r2.stderr
    assert "NO RUNNER ANSWERS 'ubuntu-latest'" in r2.stderr, r2.stderr
    # ⛔ LABEL-SCOPED, not byte-for-byte. The summary names EVERY `runs-on:` label with zero
    # answers, derived from all ~40 workflow files — so asserting its exact text made this
    # INSTALLER test red whenever an unrelated workflow added a label (`macos-14`, a new hosted
    # pin). Assert that the labels this arm is about are NAMED, and nothing about the rest.
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
    stub3 = _stub_gh(tmp_path / "lookalike", "0", rows=lookalike)
    r3 = _run(
        "--status",
        "--count",
        "1",
        env=_env(tmp_path, PATH=f"{stub3}:{os.environ['PATH']}", CI_RUNNER_UNIT_DIR=str(tmp_path / "units")),
    )
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
    it: the block's own contract (`:259-262`) is that an unread label must never read as healthy.

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
