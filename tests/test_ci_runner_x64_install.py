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
     `off`/unset drops it, an unrecognised value is REFUSED (exit 1, before any mode runs). The keep-half is also ARCH-AWARE:
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
# ⛔ EVERY `runs-on` FORM THE SHIPPED EXTRACTOR READS, or the counter-guard over the runbook's
# accepted-cost inventory is blind to one form again each round. TWO kinds of spelling matter:
#
#   • the KEY. Keys are NODES, not spellings — plain, single- and double-quoted all resolve to the
#     key `runs-on` (measured with PyYAML for every form below; spec 1.2.2 makes two spellings that
#     resolve equal a DUPLICATE key, which is the same statement). The same rule governs the
#     `labels:` key INSIDE a mapping, which is where the single-spelling bug survived a round
#     longer: the extractor's `keyis()` accepts `labels`, `'labels'`, `"labels"` and a space before
#     the colon, so `{group: x, "labels": [ubuntu-24.04]}` is verdict-able at rc 5 while a guard
#     matching only `\blabels:` kept the inventory green — one spelling over from the `"runs-on":`
#     half that was already fixed.
#   • the VALUE. The extractor reads FIVE forms, so a pin in any of them makes its file
#     pool-eligible: scalar, explicit key (`? runs-on` / `: value`), block sequence, block MAPPING
#     (`group:` / `labels:` keys on deeper-indented lines), and a flow `{group: …, labels: […]}`.
#     A count that sees only the plain scalar silently omits that file from the inventory.
_QUOTED_RUNS_ON = r'(?:"runs-on"|\'runs-on\'|runs-on)'
# The `labels:` key, spelled any way the extractor's `keyis(k, "labels")` accepts — including the
# space-before-colon form (`labels :`), which is valid YAML and which `keyis` reads.
_LABELS_KEY = r'(?:["\']?labels["\']?[ \t]*:[ \t]*)'
# The pin itself, and NOT its `-arm` sibling: `ubuntu-24.04-arm` is an ARM64 hosted label the
# autoscaler keeps dropping, so counting it would inflate the inventory with a leg the mitigation
# does not serve (measured: the old flow-mapping arm's `\bubuntu-24\.04\b` matched it).
_PIN_2404 = r'["\']?ubuntu-24\.04["\']?(?![-\w.])'
RUNS_ON_UBUNTU_2404_RE = (
    r'^    (?:'
    # scalar value, and the explicit-key form (a two-line construct: `? runs-on` then `: value`)
    + _QUOTED_RUNS_ON + r'[ \t]*:[ \t]+' + _PIN_2404 + r'[ \t]*$'
    + r'|\?[ \t]*' + _QUOTED_RUNS_ON + r'[ \t]*\r?\n[ \t]*:[ \t]+' + _PIN_2404 + r'[ \t]*$'
    # block sequence: the runs-on key, then one or more `- label` items (stops at the next key)
    + r'|' + _QUOTED_RUNS_ON + r'[ \t]*:\r?\n(?:[ \t]+-[^\n]*\r?\n)*?[ \t]+-[ \t]*' + _PIN_2404 + r'[ \t]*$'
    # block MAPPING: `runs-on:` then a `group:`/`labels:` key on a deeper-indented line. The repeat
    # may cross sibling lines but never another `runs-on`, so one job cannot absorb the next one's.
    + r'|' + _QUOTED_RUNS_ON + r'[ \t]*:\r?\n(?:[ \t]+(?!' + _QUOTED_RUNS_ON + r')[^\n]*\r?\n)*?'
    + r'[ \t]+' + _LABELS_KEY + r'[^\n]*' + _PIN_2404
    # flow mapping: `{group: …, labels: [ubuntu-24.04]}` (the group: prefix is consumed)
    + r'|' + _QUOTED_RUNS_ON + r'[ \t]*:[ \t]*\{[^\n}]*' + _LABELS_KEY + r'\[[^\]]*' + _PIN_2404
    + r'[^\n}]*\}[ \t]*$'
    + r')'
)

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
    """Run the installer. `cwd` matters for two arms: an unquoted label split would expand a `*`
    against the shell's working directory, so that test runs the installer FROM the directory
    holding the decoy files it would otherwise swallow — and `--status`' label-coverage read is a
    read OF THE CWD (it parses `.github/workflows`), so a case that exercises it must say where it
    stands."""
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
      * anything else → the pooled reads: the ARM64 fallback count (`stdout`, the existing
        argument) and the label-coverage rows, whose fixture spelling is `name<TAB>labels` for an
        ONLINE runner or `name<TAB>status<TAB>labels` to express an OFFLINE one (`coverage` below
        fills the status in, so a fixture can only say what the installer's own projection says).
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
    # ⛔ THE COVERAGE READ CARRIES A THIRD FIELD — `.status` — BECAUSE AN OFFLINE RUNNER IS NOT
    # COVERAGE. A fixture written as `name<TAB>labels` therefore means "a runner that IS online"
    # (the state every pre-existing arm was written for), while a three-field row
    # (`name<TAB>status<TAB>labels`) is how an arm expresses an OFFLINE carrier. Normalising here
    # keeps ONE spelling in the fixtures and still answers the installer's real projection, so a
    # fixture cannot express a row the installer could not read.
    coverage = "\n".join(
        line.replace("\t", "\tonline\t", 1) if line.count("\t") == 1 else line
        for line in stdout.split("\n")
    )
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
        f"  *) printf '%s\\n' {shlex.quote(coverage)} ;;\n"
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
    decision (not just a style choice): `docs/ops/ci-runner-x64.md:92-94` —
    "`_preserve_labels` DROPS the whole class on re-registration … so an attachment survives a
    repair that re-applies it (`--repair-labels` keeps only the box's own label under `on|auto`) and does NOT
    survive a re-registration". `tools/runner_autoscale.py::_preserve_labels` already drops the
    family
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
    the measured pre-#2327 stamp (`docs/ops/ci-runner-x64.md:586-588`) — and the runbook still sells
    `--repair-labels` as the manual heal that "re-applies this arch's marker"
    (`docs/ops/ci-runner-x64.md:742-743`). Keeping
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

      * `on`     → the pin THIS BOX CAN CLAIM (`ubuntu-24.04`) is KEPT and printed as
                   `version-pinned, kept=[…]`; the rest of the family — `ubuntu-24.04-arm`, an
                   ARM64 image — stays DROPPED and is printed on `not kept=` / `removed=`;
      * `auto` on a host whose `/etc/os-release` reports 24.04 → same (the box claims one);
      * `auto` on any OTHER host (22.04, non-Linux) → DROPPED. The DECLINE is the answer: the
                   repair reads the RESOLUTION (`$OS_LABELS`), not the flag's spelling;
      * unset    → dropped (the pre-departure invariant `_preserve_labels` also enforces);
      * `yes`    → REFUSED before any mode runs (exit 1). The flag is validated ONCE, before the
                   dispatch, so an unrecognised value cannot reach this carve-out at all — the
                   pre-#10275 code read it as `off`, which made a typo look like a working revert.

    ⛔ THE DECLINING-`auto` ARMS ARE THE POINT OF THIS TEST AT THIS HEAD (#10275). The carve-out
    used to read `case "${CI_RUNNER_OS_LABEL:-off}" in on|auto)`, i.e. the FLAG SPELLING, so a box
    that had just printed `os-label: auto DECLINED — … ubuntu-24.04 not claimed` went on to KEEP the
    pin and re-PUT it — and a repair only UNIONS, so that claim the box had itself declined became
    PERMANENT until an operator ran an explicit `off` repair. `--status`, in the same script,
    already read the resolution for the identical flag and reported the pool as NOT asked. The
    `auto` arms therefore set BOTH seams (`CI_RUNNER_OS_NAME` / `CI_RUNNER_OS_RELEASE`): without
    them the arm only ever exercised whatever host the suite happens to run on, which is insensitive
    to the one property `auto` is defined by and cannot tell "reads the resolution" from "ignores
    the resolution".

    ⛔ THE KEEP IS NARROWED TO `$OS_LABEL`, NOT THE WHOLE CLASS. `resolve_os_label` can only ever
    emit `ubuntu-24.04`, and a label names an IMAGE: keeping `ubuntu-24.04-arm` on an x86_64 runner
    is the false-architecture claim this file's header forbids, and it diverges from the sibling
    writer `runner_autoscale.py::_preserve_labels` (which drops the whole family on every arch). A
    repair UNIONS, so a kept pin is permanent — the narrowing has to happen in the repair or not at
    all.

    Non-vacuity: with the flag ignored (an unconditional drop, the shape review rejected) the `on`
    arm reds on the PUT set and on the `kept=` line; with the flag inverted (an unconditional keep)
    the unset arm reds on `ubuntu-24.04` appearing in the PUT; with the keep widened back to the
    whole family the `on` arm reds on `ubuntu-24.04-arm` appearing in the PUT and on the `kept=`
    line; and with the pre-fix FLAG-SPELLING read (`case "${CI_RUNNER_OS_LABEL:-off}" in on|auto)`)
    the declining-`auto` arms red on the PUT set, on `kept=[]`, on `not kept=` and on `removed=` —
    measured against the pre-fix code at `b5ff9b24a`.
    """
    rel24 = tmp_path / "os-release-24"
    rel24.write_text('NAME="Ubuntu"\nVERSION_ID="24.04"\n')
    rel22 = tmp_path / "os-release-22"
    rel22.write_text('NAME="Ubuntu"\nVERSION_ID="22.04"\n')

    # `on`, and `auto` on a host that REPORTS the pinned release: the box has opted in, so the pin
    # it can actually claim survives — and ONLY it
    for value, seams in (
        ("on", {}),
        ("auto", {"CI_RUNNER_OS_NAME": "Linux", "CI_RUNNER_OS_RELEASE": str(rel24)}),
    ):
        stub, env = _repair_runner(tmp_path / f"opt-{value}", pool="1", labels=POOL_LABELS)
        env["CI_RUNNER_OS_LABEL"] = value
        env.update(seams)
        r = _run("--repair-labels", "--count", "1", env=env)
        assert r.returncode == 0, r.stderr
        put = _put_body(stub)
        assert "ubuntu-24.04" in put, (
            f"CI_RUNNER_OS_LABEL={value} is the box's recorded state, and this host CLAIMS the pin "
            f"— a repair must not strip it: {put}"
        )
        assert "ubuntu-24.04-arm" not in put, (
            f"keeping `ubuntu-24.04-arm` PUTs an ARM64 image claim onto an x86_64 runner — the "
            f"false-architecture claim the whole file forbids: {put}"
        )
        assert "version-pinned, kept=[ubuntu-24.04]" in r.stderr, r.stderr
        assert "version-pinned, not kept=[ubuntu-24.04-arm]" in r.stderr, r.stderr
        assert "removed=[ubuntu-24.04-arm]" in r.stderr, (
            f"dropping the arm64 spelling is a REMOVAL and must be printed as one: {r.stderr}"
        )
        assert "NOT KEPT: 'ubuntu-24.04-arm'" in r.stderr, (
            f"the reason must be printed, not left to be inferred from `not kept=`: {r.stderr}"
        )
        # …and the log says WHICH reading decided it, so it cannot disagree with `--status`
        assert "resolved claim=ubuntu-24.04" in r.stderr, r.stderr

    # ⛔ A DECLINING `auto` IS THE SAME ANSWER AS `off`: the resolution claimed nothing, so nothing is
    # kept — even though the runner already carries the pin, and even though the FLAG spelling is
    # still `auto`. Keeping it here is what makes a declined claim permanent.
    for case, seams in (
        ("rel-22.04", {"CI_RUNNER_OS_NAME": "Linux", "CI_RUNNER_OS_RELEASE": str(rel22)}),
        ("not-linux", {"CI_RUNNER_OS_NAME": "Darwin"}),
    ):
        stub, env = _repair_runner(tmp_path / f"declined-{case}", pool="1", labels=POOL_LABELS)
        env["CI_RUNNER_OS_LABEL"] = "auto"
        env.update(seams)
        r = _run("--repair-labels", "--count", "1", env=env)
        assert r.returncode == 0, r.stderr
        assert "DECLINED" in r.stderr, (
            f"the premise of this arm is that `auto` DECLINED on this host: {r.stderr}"
        )
        put = _put_body(stub)
        assert not {"ubuntu-24.04", "ubuntu-24.04-arm"} & set(put), (
            f"`auto` declined on this host, so the box claimed nothing — a repair that keeps (and "
            f"re-PUTs) the pin makes a claim the box itself declined PERMANENT, and it is the false "
            f"PASS the carve-out exists to stop: {put}"
        )
        assert "version-pinned, kept=[]" in r.stderr, r.stderr
        assert "version-pinned, not kept=[ubuntu-24.04,ubuntu-24.04-arm]" in r.stderr, r.stderr
        assert "removed=[ubuntu-24.04,ubuntu-24.04-arm]" in r.stderr, (
            f"the drop is a REMOVAL and must be printed as one: {r.stderr}"
        )
        assert "resolved claim=none" in r.stderr, (
            f"the `removed=` line must print the resolution that decided the carve-out — printing "
            f"the flag alone is how the log agreed with the writer even where it was wrong: "
            f"{r.stderr}"
        )

    # unset (and an explicit `off`): the pre-departure invariant — the pin is dropped and printed
    for value in ("", "off"):
        stub, env = _repair_runner(tmp_path / f"off-{value or 'unset'}", pool="1", labels=POOL_LABELS)
        env["CI_RUNNER_OS_LABEL"] = value
        r = _run("--repair-labels", "--count", "1", env=env)
        assert r.returncode == 0, r.stderr
        put = _put_body(stub)
        assert not {"ubuntu-24.04", "ubuntu-24.04-arm"} & set(put), put
        assert "version-pinned, kept=[]" in r.stderr, r.stderr
        assert "resolved claim=none" in r.stderr, r.stderr

    # an unrecognised value NEVER REACHES the carve-out: the flag is validated once, before every
    # mode, so this is a usage error with nothing read and nothing PUT.
    stub, env = _repair_runner(tmp_path / "bogus", pool="1", labels=POOL_LABELS)
    env["CI_RUNNER_OS_LABEL"] = "yes"
    r = _run("--repair-labels", "--count", "1", env=env)
    assert r.returncode == 1, (r.returncode, r.stderr)
    assert "must be off|on|auto" in r.stderr, r.stderr
    log = stub / "gh-calls.log"
    assert not log.exists() or not re.search(r"PUT ", log.read_text()), (
        f"a refused value must change nothing, but gh was asked to relabel: "
        f"{log.read_text() if log.exists() else '<no gh call at all>'}"
    )


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
    assert "0 of 1 online self-hosted runner(s)" in r.stderr, r.stderr
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
    assert "0 of 1 online self-hosted runner(s)" in r.stderr, r.stderr
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
    # `0 of 1 online self-hosted runner(s)` — a fabricated runner, in the block whose doctrine is "an empty
    # read is not a coverage verdict". The count the script already had (`grep -c .` → 0) is now the
    # denominator it prints. Mutation: stop passing `pool_total` to the awk -> `0 of 1` -> RED.
    empty_pool = _stub_gh(tmp_path / "empty", "")
    r = _run(
        "--status",
        env=_env(tmp_path, PATH=f"{empty_pool}:{os.environ['PATH']}", CI_RUNNER_OS_LABEL="on"),
        cwd=_repo_with_workflows(tmp_path / "e", "jobs:\n  a:\n    runs-on: ubuntu-24.04\n    steps: []\n"),
    )
    assert r.returncode == 5, (r.returncode, r.stderr)
    assert "0 of 0 online self-hosted runner(s)" in r.stderr, (
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
    assert "0 of 0 online self-hosted runner(s)" in r.stderr, r.stderr


def test_label_coverage_refuses_a_workflow_it_cannot_read(tmp_path: Path):
    r"""⛔ A WORKFLOW THE READ COULD NOT OPEN IS `exit 4`, NEVER A SILENTLY DROPPED FILE.

    The extraction was a raw glob with `2>/dev/null`, which discarded grep's per-file read error: a
    workflow that could not be OPENED contributed NOTHING, the surviving files still produced a row
    set, no `!` was emitted, and the block printed a clean verdict and exited 0 over the one file it
    had failed to read. Measured: an unreadable workflow naming `ubuntu-24.04` beside a readable
    `ubuntu-latest` → `ubuntu-24.04` appeared NOWHERE in the report and `--status` exited 0; the same
    tree with the mode restored → rc 5. That is the incident state this block exists to surface,
    cleared through the one path its three exit-4 arms cannot see: they all inspect what the read
    PRODUCED, never whether every file was read at all.

    ⛔ GREP'S rc ALONE CANNOT DECIDE IT, which is why the checkpoint is the enumeration: with only
    `*.yml` present the literal `.yaml` glob makes grep exit 2 for a reason that is NOT a read
    failure (measured `grep-rc=2`), so a blanket `|| exit 4` would red the ordinary tree. The
    predicate is "every ENUMERATED file could be opened" — the `[ -f ]`-per-path enumerator the
    pre-rewrite script had and this one replaced with a glob.

    Mutation: restore the glob + `2>/dev/null` → this exits 0 over a file it never read → RED.
    """
    bindir = _stub_gh(tmp_path / "gh", "r1\tself-hosted-x64,ubuntu-latest")
    env = _env(tmp_path, PATH=f"{bindir}:{os.environ['PATH']}", CI_RUNNER_OS_LABEL="on")

    # A `.yml` entry that is NOT a regular file cannot be opened by any user, root included — so this
    # arm states the same predicate as the permission case below without depending on the test uid.
    repo = tmp_path / "repo"
    wf = repo / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "aa-not-a-file.yml").mkdir()
    (wf / "zz-readable.yml").write_text("jobs:\n  b:\n    runs-on: ubuntu-latest\n    steps: []\n")
    r = _run("--status", env=env, cwd=repo)
    assert r.returncode == 4, (r.returncode, r.stderr)
    assert "cannot READ" in r.stderr and "aa-not-a-file.yml" in r.stderr, r.stderr

    # The shape the review measured: a readable file silently ABSORBS the one it could not open.
    # Skipped under root, where a mode bit is not a fence — never asserted vacuously.
    if os.geteuid() != 0:
        repo2 = tmp_path / "repo2"
        wf2 = repo2 / ".github" / "workflows"
        wf2.mkdir(parents=True)
        (wf2 / "aa-unreadable.yml").write_text("jobs:\n  a:\n    runs-on: ubuntu-24.04\n    steps: []\n")
        (wf2 / "zz-readable.yml").write_text("jobs:\n  b:\n    runs-on: ubuntu-latest\n    steps: []\n")
        (wf2 / "aa-unreadable.yml").chmod(0o000)
        try:
            r = _run("--status", env=env, cwd=repo2)
        finally:
            (wf2 / "aa-unreadable.yml").chmod(0o644)
        assert r.returncode == 4, (r.returncode, r.stderr)
        assert "cannot READ" in r.stderr, r.stderr
        # CONTROL: the same tree, readable, measures BOTH labels — so the 4 above is caused by the
        # unreadable file and not by the fixture.
        r = _run("--status", env=env, cwd=repo2)
        assert r.returncode in (0, 5), (r.returncode, r.stderr)
        assert "ubuntu-24.04" in r.stderr and "ubuntu-latest" in r.stderr, r.stderr


def test_label_coverage_refuses_a_runs_on_in_flow_position(tmp_path: Path):
    r"""⛔ A `runs-on` IN FLOW POSITION IS REFUSED, NOT CLEARED — `a: {runs-on: …}` is valid YAML.

    The opener only ever saw a line that STARTS with `runs-on:`, so a job written as a flow mapping
    opened no block, `keyseen` never incremented, the END rule had nothing to report, and the block
    CLEARED. Measured on a fixture whose other workflow named `ubuntu-latest`: the `ubuntu-24.04`
    leg was absent from the report entirely and the read exited 0. GitHub parses the JOB MAPPING, not
    the formatting, so the leg is real and this instrument is the only thing that would notice it.

    This pass does not carry flow-position keys, so counting them is what turns "invisible" into
    "refused" — the same content-not-prefix rule the quoted-key and explicit-key spellings were
    added for: a form the pass cannot read is refused rather than allowed to clear the rest.

    Mutation: revert `keylike()` to the line-start spellings only → rc 0 with no `ubuntu-24.04` row
    → RED.
    """
    bindir = _stub_gh(tmp_path / "gh", "r1\tself-hosted-x64,ubuntu-latest")
    repo = tmp_path / "repo"
    wf = repo / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "a.yml").write_text(
        "jobs:\n"
        "  a: {runs-on: ubuntu-24.04, steps: [{run: echo hi}]}\n"
        "  b:\n    runs-on: ubuntu-latest\n    steps: []\n"
    )
    r = _run(
        "--status",
        env=_env(tmp_path, PATH=f"{bindir}:{os.environ['PATH']}", CI_RUNNER_OS_LABEL="on"),
        cwd=repo,
    )
    assert r.returncode == 4, (r.returncode, r.stderr)
    assert "could not read 1 runs-on: block(s)" in r.stderr, r.stderr


def test_label_coverage_matches_labels_case_insensitively(tmp_path: Path):
    r"""⛔ A LABEL IS CASE-INSENSITIVE — GitHub matches runner labels that way.

    The carrier match (`a[i]==want`), the class regexes and the shell-side identity compare all keyed
    on the EXACT spelling, so a workflow naming `Ubuntu-24.04` beside a pool whose runners carry
    `ubuntu-24.04` printed `Ubuntu-24.04 = 0 of 7`, fell to the `*)` class arm ("Not a label this
    pool serves … a report, not a block") and exited 0 — the incident state, cleared, on a spelling
    Actions treats as identical. This file already relies on the case-insensitivity elsewhere (the
    repair block records that `Self-Hosted-Arm64` / `SELF-HOSTED-ARM64` ARE the arm64 marker,
    `_preserve_labels` tests with `grep -qiE`, and `tools/runner_autoscale.py` uses
    `re.IGNORECASE`), which made this read the only case-SENSITIVE member of the family.

    The fold lives in `emit()` — the one choke point every documented `runs-on` form goes through —
    so the label set, the carrier match, the class arms and `lab_asked` all see one spelling.

    Mutation: drop `v = tolower(v)` from `emit()` → the mixed-case arms RED (0 of 1 / rc 0 instead
    of covered / rc 5).
    """
    # A pool that answers the lowercase spelling only: `ubuntu-24.04` on ONE online x64 runner.
    bindir = _stub_gh(tmp_path / "gh", "r1\tself-hosted-x64,ubuntu-24.04")

    # (a) The mixed-case REQUIRED label is MEASURED, not reported as somebody else's label.
    covered = _repo_with_workflows(
        tmp_path / "c",
        "jobs:\n  a:\n    runs-on: Ubuntu-24.04\n    steps: []\n  b:\n    runs-on: UBUNTU-LATEST\n    steps: []\n",
    )
    r = _run(
        "--status",
        env=_env(tmp_path, PATH=f"{bindir}:{os.environ['PATH']}", CI_RUNNER_OS_LABEL="on"),
        cwd=covered,
    )
    assert "ubuntu-24.04 = 1 of 1" in r.stderr, r.stderr
    assert "ubuntu-latest = 0 of 1" in r.stderr, r.stderr
    assert "Not a label this pool serves" not in r.stderr, r.stderr
    assert r.returncode == 0, (r.returncode, r.stderr)

    # (b) And the fold does not CLEAR the incident state: the same mixed-case pin against a pool with
    # NO carrier is still the verdict it is in lowercase. Without the fold `lab_asked` was 0 (the
    # identity compare failed on case), so this arm exited 0 — a false clear in the blocking
    # direction, on the one spelling Actions normalises away.
    empty = _stub_gh(tmp_path / "empty", "r1\tself-hosted-x64,ubuntu-latest")
    r = _run(
        "--status",
        env=_env(tmp_path, PATH=f"{empty}:{os.environ['PATH']}", CI_RUNNER_OS_LABEL="on"),
        cwd=_repo_with_workflows(
            tmp_path / "e", "jobs:\n  a:\n    runs-on: Ubuntu-24.04\n    steps: []\n"
        ),
    )
    assert r.returncode == 5, (r.returncode, r.stderr)
    assert "0 of 1 online self-hosted runner(s)" in r.stderr, r.stderr


def test_label_coverage_counts_only_online_carriers(tmp_path: Path):
    """⛔ A CARRIER THAT IS OFFLINE IS NOT AN ANSWER — `online` is a GATE, not a decoration.

    GitHub's own runner-status definition: `Offline` means "the runner is not connected to GitHub",
    so it cannot be handed a job. The coverage read used to project `(name, labels)` only, so the
    exact incident this block exists to surface was reported GREEN: 7 carriers of `ubuntu-24.04`,
    every one offline, printed `ubuntu-24.04 = 7 of 7 runner(s)`, no `⚠️ PARTIAL`, and exit 0 even
    under `CI_RUNNER_OS_LABEL=on` — i.e. "`python-ci-gate` never dispatched, every PR blocked"
    reported as covered. It also inflated the denominator, so the PARTIAL arm added in this PR
    (the only read that sees erosion before the last carrier goes) could not fire for a
    partially-offline pool.

    Runners go offline routinely — `runner_autoscale.py::converge` re-registers DEAD/ORPHANED ones —
    so this is a state, not an edge case.

    Mutation: drop `.status` from the coverage projection (the pre-fix read) -> the first arm below
    prints `7 of 7` and exits 0 -> RED. Count offline carriers in `n` (or in `denom`) -> the first
    and second arms RED.
    """
    repo = _repo_with_workflows(
        tmp_path / "cov", "jobs:\n  a:\n    runs-on: ubuntu-24.04\n    steps: []\n"
    )

    # ── the incident: every carrier exists, none of them is connected ────────────────────────
    offline_pool = _stub_gh(
        tmp_path / "gh-offline",
        "\n".join(
            f"o{i}\toffline\tself-hosted,Linux,X64,self-hosted-x64,ubuntu-24.04"
            for i in range(1, 8)
        ),
    )
    r = _run(
        "--status",
        env=_env(
            tmp_path, PATH=f"{offline_pool}:{os.environ['PATH']}", CI_RUNNER_OS_LABEL="on"
        ),
        cwd=repo,
    )
    assert r.returncode == 5, (
        f"a label whose every carrier is OFFLINE has no answer, so the verdict is `bad`: "
        f"{(r.returncode, r.stderr)}"
    )
    assert "7 of 7" not in r.stderr, (
        f"seven offline runners are not seven answers — this is the false green: {r.stderr}"
    )
    assert "0 of 0 online self-hosted runner(s) answer it" in r.stderr, r.stderr
    assert "7 runner(s) in the pool are not `online`, 7 of them carrying ubuntu-24.04" in r.stderr, (
        f"the offline carriers must be printed as CONTEXT, so the measurement is readable: {r.stderr}"
    )
    assert "⛔ UNCOVERED" in r.stderr, r.stderr

    # ── the erosion shape: one online runner that CANNOT answer, one offline one that can ────
    # Pre-fix this printed `ubuntu-24.04 = 1 of 2 runner(s) — ⚠️ PARTIAL` and exited 0: the only
    # carrier was disconnected, and the label had no answer at all.
    mixed = _stub_gh(
        tmp_path / "gh-mixed",
        "on1\tonline\tself-hosted-x64\noff1\toffline\tself-hosted-x64,ubuntu-24.04",
    )
    r = _run(
        "--status",
        env=_env(tmp_path, PATH=f"{mixed}:{os.environ['PATH']}", CI_RUNNER_OS_LABEL="on"),
        cwd=repo,
    )
    assert r.returncode == 5, (r.returncode, r.stderr)
    assert "ubuntu-24.04 = 0 of 1 online self-hosted runner(s) answer it" in r.stderr, (
        f"the denominator counts the runners that COULD answer — the online one, not the offline "
        f"carrier: {r.stderr}"
    )
    assert "1 runner(s) in the pool are not `online`, 1 of them carrying ubuntu-24.04" in r.stderr, (
        r.stderr
    )

    # ── CONTROL: the fix is not "everything is red" ─────────────────────────────────────────
    # One ONLINE carrier is coverage, and an offline runner that does NOT carry the label must not
    # inflate the denominator either (`1 of 1`, never `1 of 2`).
    live = _stub_gh(
        tmp_path / "gh-live",
        "on1\tonline\tself-hosted,Linux,X64,self-hosted-x64,ubuntu-24.04\n"
        "on2\toffline\tself-hosted-x64",
    )
    r = _run(
        "--status",
        env=_env(tmp_path, PATH=f"{live}:{os.environ['PATH']}", CI_RUNNER_OS_LABEL="on"),
        cwd=repo,
    )
    assert r.returncode == 0, (r.returncode, r.stderr)
    assert "ubuntu-24.04 = 1 of 1 online runner(s)" in r.stderr, (
        f"an online carrier is coverage, and an offline non-carrier is not a candidate: {r.stderr}"
    )
    assert "1 runner(s) in the pool are not `online`, 0 of them carrying ubuntu-24.04" in r.stderr, (
        r.stderr
    )


def test_label_coverage_never_widens_a_class_numerator_to_foreign_arch_carriers(tmp_path: Path):
    """⛔ AN EMPTY SUB-POOL IS THE INCIDENT — WIDENING THE NUMERATOR TO THE WHOLE POOL HIDES IT.

    The class denominator (`nx64` for a bare `ubuntu-<version>`, `narm` for an `-arm` spelling) can
    legitimately be 0, and the read used to widen BOTH halves in that case
    (`if (denom == 0) { denom = pool_online; num = n }`). `n` counts EVERY online carrier, so the
    widening put foreign-arch carriers back into the numerator the class exists to exclude — and
    the verdict got BETTER as more of them were added, which is the fail-open direction:

      0 online x64 + 5 online arm64 wrongly carrying `ubuntu-24.04`
        -> `ubuntu-24.04 = 5 of 5 online runner(s)` and exit 0   (full coverage, stated)
        -> `0 of 0 ... answer it` + `⛔ UNCOVERED` + exit 5        (the incident, after the fix)

    The same shape hid a false architecture claim for the `-arm` class (0 online arm64, 2 online x64
    wrongly carrying `ubuntu-24.04-arm` read `2 of 2`, now `0 of 0`).

    Mutation: restore the widening, or use `n` as the numerator for a class -> the first arm below
    prints `5 of 5` and exits 0 -> RED; the second prints `2 of 2` -> RED.
    """
    repo = _repo_with_workflows(
        tmp_path / "cov-foreign", "jobs:\n  a:\n    runs-on: ubuntu-24.04\n    steps: []\n"
    )

    # ── the bare class: every online carrier is FOREIGN-arch, the one real x64 carrier is offline ──
    foreign = _stub_gh(
        tmp_path / "gh-foreign",
        "\n".join(
            [
                f"a{i}\tonline\tself-hosted,Linux,ARM64,self-hosted-arm64,ubuntu-24.04"
                for i in range(1, 6)
            ]
            + ["x1\toffline\tself-hosted,Linux,X64,self-hosted-x64,ubuntu-24.04"]
        ),
    )
    r = _run(
        "--status",
        env=_env(tmp_path, PATH=f"{foreign}:{os.environ['PATH']}", CI_RUNNER_OS_LABEL="on"),
        cwd=repo,
    )
    assert r.returncode == 5, (
        f"0 online x64 runners is NO answer for a bare x64 pin however many arm64 runners wrongly "
        f"carry it — a verdict, not a green: {(r.returncode, r.stderr)}"
    )
    assert "5 of 5" not in r.stderr, (
        f"five foreign-arch carriers are not five answers; counting them makes the read WORSE as "
        f"unusable runners are added: {r.stderr}"
    )
    assert "0 of 0 online self-hosted runner(s) answer it" in r.stderr, (
        f"the denominator counts the runners that COULD answer (the online x64 pool, here empty), "
        f"and the numerator must be counted in that same population: {r.stderr}"
    )
    assert "⛔ UNCOVERED" in r.stderr, r.stderr
    assert "1 runner(s) in the pool are not `online`, 1 of them carrying ubuntu-24.04" in r.stderr, (
        f"the offline x64 carrier is still printed as CONTEXT, so the measurement stays readable: "
        f"{r.stderr}"
    )

    # ── the `-arm` class: 0 online arm64, 2 online x64 wrongly carrying the ARM spelling ─────────
    arm_repo = _repo_with_workflows(
        tmp_path / "cov-foreign-arm", "jobs:\n  a:\n    runs-on: ubuntu-24.04-arm\n    steps: []\n"
    )
    wrong_arch = _stub_gh(
        tmp_path / "gh-wrong-arch",
        "\n".join(
            f"x{i}\tonline\tself-hosted,Linux,X64,self-hosted-x64,ubuntu-24.04-arm"
            for i in range(1, 3)
        ),
    )
    r = _run(
        "--status",
        env=_env(tmp_path, PATH=f"{wrong_arch}:{os.environ['PATH']}", CI_RUNNER_OS_LABEL="on"),
        cwd=arm_repo,
    )
    assert "2 of 2" not in r.stderr, (
        f"an x64 runner carrying an `-arm` spelling is a false architecture claim, never coverage: "
        f"{r.stderr}"
    )
    assert "0 of 0 online self-hosted runner(s) answer it" in r.stderr, r.stderr
    assert r.returncode == 0, (
        f"an `-arm` spelling is not this pool's class on an x64 box, so it is a REPORT, not a "
        f"block: {(r.returncode, r.stderr)}"
    )


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
    assert "ubuntu-latest = 1 of 2 online runner(s)" in r.stderr, r.stderr
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
    assert "ubuntu-24.04 = 1 of 2 online runner(s)" in r.stderr, r.stderr
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
    # ⛔ BOTH READINGS MUST BE STATED, and this assertion used to name a string the script never
    # emits (`"leftover from a previous state"` — `grep` finds it nowhere), so it could not fail and
    # the "Mutation ... both RED" note above rested on the two assertions below it alone. Pin the
    # text the code ACTUALLY prints, positively: the both-readings sentence is the fix, a
    # single-reading revert drops one half of it, and the pin reds.
    assert "leftover from an earlier" in r.stderr, (
        f"the flag-off PARTIAL arm must name the leftover reading it fits: {r.stderr}"
    )
    assert "erosion while the pool is still asked" in r.stderr, (
        f"and the OTHER reading it fits — a single-reading message is the destructive half: {r.stderr}"
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
    assert "ubuntu-24.04 = 3 of 7 online runner(s)" in r.stderr, (
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
    assert "ubuntu-2204-tools = 0 of 1 online self-hosted runner(s)" in r.stderr, r.stderr
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
    assert "ubuntu-22.04 = 0 of 1 online self-hosted runner(s)" in r.stderr, r.stderr
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
        assert "ubuntu-24.04 = 0 of 1 online self-hosted runner(s)" in r.stderr, f"{name}: {r.stderr}"
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


def test_label_coverage_reads_a_quoted_labels_key_and_refuses_an_unclosed_collection(tmp_path: Path):
    r"""Three shapes were CLEARED at exit 0 while a required pin went unmeasured — all fail-open.

    They are one class: the block's own contract says "a form this pass cannot read is refused",
    and each of these read as a form it COULD read.

    1. **A quoted `labels:` key.** `mapping()` and the two block arms matched `/^labels[ \t]*:/`,
       the SPELLING, not the key node. YAML gives a plain, a single-quoted and a double-quoted key
       the same mapping key — the argument this file already makes for `runs-on` — so
       `runs-on: {group: pool, "labels": [ubuntu-24.04]}` emitted NO label, `plabels` stayed 0, and
       the `group:` beside it suppressed the one refusal marker (`finish()` prints `!` only on
       `bad > 0 || (got == 0 && (!pgroup || plabels))`). Measured: rc 0 with no `ubuntu-24.04` row
       at all. The block form `'labels': ubuntu-24.04` under a `group:` behaves identically.
    2. **An unterminated flow collection.** `flowlist()` does `sub(/\]$/, "", s)`, a silent no-op
       when the `]` is not on the line, and the caller `finish()`es the block on the same line — so
       a multi-line flow sequence (valid YAML; what a formatted workflow produces) was read as its
       opening line: `runs-on: [self-hosted,` / `ubuntu-24.04]` emitted `self-hosted`, dropped the
       pin, and exited 0. Same for a multi-line flow MAPPING.
    3. **CRLF.** `trim()` stripped `[ \t]` only, so `ubuntu-24.04\r` survived into the emitted
       label, matched no class arm, and fell to the `*)` "a report, not a block" default — the
       whole read silently became "not a label this pool serves", exit 0.

    Mutation: restore `/^labels[ \t]*:/` -> the two quoted-key cases exit 0 -> RED.
    Mutation: drop the two `bad++` arms in `value()` -> the unclosed cases exit 0 -> RED.
    Mutation: drop `sub(/\r+$/, "", s)` from `trim()` -> the CRLF case exits 0 -> RED.
    """
    bindir = _stub_gh(tmp_path / "gh", "r1\tself-hosted-x64,ubuntu-latest")

    def status(repo: Path) -> subprocess.CompletedProcess:
        return _run(
            "--status",
            env=_env(tmp_path, PATH=f"{bindir}:{os.environ['PATH']}", CI_RUNNER_OS_LABEL="on"),
            cwd=repo,
        )

    # 1. a QUOTED `labels:` key is the same key — measured, exactly like the plain spelling.
    measured = {
        "flow mapping, double-quoted key": 'jobs:\n  a:\n    runs-on: {group: pool, "labels": [ubuntu-24.04]}\n  b:\n    runs-on: ubuntu-latest\n    steps: []\n',
        "block mapping, single-quoted key": "jobs:\n  a:\n    runs-on:\n      group: pool\n      'labels': ubuntu-24.04\n  b:\n    runs-on: ubuntu-latest\n    steps: []\n",
    }
    for i, (name, body) in enumerate(measured.items()):
        r = status(_repo_with_workflows(tmp_path / f"ql{i}", body))
        combined = r.stdout + r.stderr
        assert r.returncode == 5, (
            f"{name}: a quoted `labels:` key carries a required pin this pool is asked to serve, "
            f"so a zero answer is 5 — not {r.returncode}: {combined}"
        )
        assert "ubuntu-24.04 = 0 of 1" in combined, (
            f"{name}: the pin was never measured — the `group:` beside the quoted key suppressed "
            f"the refusal as well, which is the incident state cleared at exit 0: {combined}"
        )

    # 2. an UNCLOSED collection has no static label set, so it is 4 (no verdict) — never 0.
    unclosed = {
        "multi-line flow sequence": "jobs:\n  a:\n    runs-on: [self-hosted,\n      ubuntu-24.04]\n  b:\n    runs-on: ubuntu-latest\n    steps: []\n",
        "multi-line flow mapping": "jobs:\n  a:\n    runs-on: {group: pool,\n      labels: [ubuntu-24.04]}\n  b:\n    runs-on: ubuntu-latest\n    steps: []\n",
    }
    for i, (name, body) in enumerate(unclosed.items()):
        r = status(_repo_with_workflows(tmp_path / f"un{i}", body))
        combined = r.stdout + r.stderr
        assert r.returncode == 4, (
            f"{name}: the members past the line break cannot be read, so the read is PARTIAL and "
            f"has no verdict — that is 4, not {r.returncode}: {combined}"
        )
        assert "STATIC label set" in combined, f"{name}: {combined}"
        assert not re.search(r"= \d+ of \d+", combined), (
            f"{name}: a partial read must not verdict any label: {combined}"
        )
        # CONTROL — the identical array on ONE line is a static label set, and it verdicts.
        one_line = body.replace("[self-hosted,\n      ubuntu-24.04]", "[self-hosted, ubuntu-24.04]").replace(
            "{group: pool,\n      labels: [ubuntu-24.04]}", "{group: pool, labels: [ubuntu-24.04]}"
        )
        assert one_line != body, f"{name}: the control fixture did not change — fix the substitution"
        ctl = status(_repo_with_workflows(tmp_path / f"un{i}-ctl", one_line))
        assert ctl.returncode == 5, (
            f"{name} (control, one line): a readable static set verdicts at 5 — not {ctl.returncode}: "
            f"{ctl.stdout}{ctl.stderr}"
        )

    # 3. CRLF must not fabricate a label: `ubuntu-24.04\r` is not a class arm and is not a name.
    crlf = _repo_with_workflows(
        tmp_path / "crlf",
        "jobs:\r\n  a:\r\n    runs-on: ubuntu-24.04\r\n  b:\r\n    runs-on: ubuntu-latest\r\n    steps: []\r\n",
    )
    r = status(crlf)
    combined = r.stdout + r.stderr
    assert r.returncode == 5, (
        f"a CRLF workflow names the same labels as an LF one, so the pin's zero answer is 5 — not "
        f"{r.returncode}: {combined}"
    )
    assert "ubuntu-24.04 = 0 of 1" in combined, (
        "the `\\r` was kept in the label, so every row fell to the "
        f"\"not a label this pool serves\" default: {combined}"
    )
    assert "\r" not in combined, f"a carriage return survived into the report: {combined!r}"


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
    # ⛔ THE SYSTEMD REF IS NOT IN THE DICT ABOVE BECAUSE THE MAP DOES NOT WRITE IT AS
    # `label` `:NNN` — it writes `systemd install/start at `:NNN``, so the label-to-ref regex the
    # dict is matched with cannot be anchored on it. That is exactly why it drifted unchecked: the
    # sentence cited `:1062-1064` (the download block) while the systemd install/start lives at
    # `:1085-1088`, and `main` had it right at `:510-512` before this change — a regression that only
    # the in-range arm could see. Pinned here by the phrase the sentence uses.
    systemd = re.search(r"systemd\s+install/start at `:(\d+)(?:-(\d+))?`", map_text)
    assert systemd is not None, (
        f"the runbook map no longer cites the systemd install/start line — re-anchor this pin: {map_text!r}"
    )
    s_start = int(systemd.group(1))
    s_stop = int(systemd.group(2) or s_start)
    s_block = installer[s_start - 1 : s_stop]
    assert any(re.search(r"svc\.sh install", line) for line in s_block), (
        "the runbook cites the systemd install/start at "
        f":{s_start}-{s_stop}, which carries no `svc.sh install` — the systemd block is at "
        f"{[i + 1 for i, line in enumerate(installer) if 'svc.sh install' in line]}"
    )
    assert any(re.search(r"svc\.sh start", line) for line in s_block), (
        f"the same sentence names a START, and :{s_start}-{s_stop} carries no `svc.sh start`"
    )
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
        111: r"^# EXIT — the one distinction",  # "Exit codes (`:107-134`)"
        237: r"VALIDATED ONCE, BEFORE THE MODE DISPATCH",  # the flag/--count validation
        270: r"^is_runner_install\(\) \{",  # "the proof is `is_runner_install` at"
        274: r"the fallback label: follow the gate's own state",  # "label resolution at"
        278: r"^fallback_engaged\(\) \{",
        301: r'^case "\$\{CI_RUNNER_OS_LABEL:-off\}" in',  # the flag, validated once
        306: r"^resolve_os_label\(\) \{",  # "resolve_os_label"
        338: r"^resolve_labels\(\) \{",
        377: r"label algebra: the UNION",  # "the union's label algebra at"
        401: r"^is_foreign_arch_label\(\) \{",  # "TWO NAMED CARVE-OUTS"
        429: r"── status ──",  # the `--status` block
        452: r"label-coverage — labels the workflows name",  # "the label-coverage block"
        792: r"label-coverage read NOTHING",  # no workflows -> exit 4
        800: r"could not read \$runs_on_unreadable",  # a partial read -> exit 4
        809: r"found NO runs-on label to check",  # zero labels -> exit 4
        375: r"^\}$",  # "whose body ends at its `}` on"
        1027: r"exit 5",  # the uncovered verdict
        1049: r'MODE" = repair',  # "--repair-labels UNIONS" / "--fallback off is REFUSED"
        1074: r"UNREADABLE LABEL SET IS A REFUSAL",  # "An unreadable current label set is a REFUSAL"
        1221: r"`relabelled` IS A CLAIM ABOUT THE SERVER",  # "relabelled … is a claim about the server"
        1253: r'MODE" = labels',  # "`--print-labels`"
        1280: r'MODE" = install',  # "the two mode blocks" / "only an install"
        1285: r'\|\| die "this installer is for x86_64',  # "the refusal itself dies at"
        1288: r"FORK HEADROOM FIRST",  # "forks"
        1308: r"DISK NEXT",  # "disk"
        1315: r"CPU LAST",  # "cpu/memory"
        1332: r"^# ── uninstall",  # the span's first line ("`--uninstall` deletes only what it can PROVE")
        1333: r'MODE" = uninstall',  # "`uninstall`"
        1458: r'^for i in \$\(seq 1 "\$COUNT"\); do',  # "the scale loop"
        1461: r"dry-run: would download",  # "the run at … is the dry-run echo"
        1469: r'\[ "\$got" = "\$SHA256_LINUX_X64" \]',  # "sha256 pin" / "verify"
        1472: r"registration-token",  # "token"
        1477: r"^    set -- --unattended",  # "one argv"
        1479: r'\./config\.sh "\$@"',  # "`config.sh`"
        1489: r'svc\.sh install "\$actor"',  # "systemd install/start"
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
    repair = re.search(r"the repair \(`(?:tools/)?install-ci-runner-x64\.sh:(\d+)-\d+`\)", memory)
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
        # ⛔ THE `set -o pipefail` SELF-CITATION WENT WITH THE HELPER IT DESCRIBED. It pinned the
        # `*.yml`/`*.yaml` glob reader's `pipefail` hazard; that reader was replaced by the
        # label-coverage extractor, which does not depend on a matched glob at all — a read that
        # finds nothing is `exit 4` rather than a silent empty set. Re-anchor this pin to that
        # reader if the globbing path ever comes back.
        # the writer section names `resolve_os_label` and cites its own line for it (#10275)
        ("THE FLAG HAS TWO SIDES IN THIS TREE", r"^resolve_os_label\(\) \{"),
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
        92: r"DROPS the whole class on re-registration",
        586: r"A foreign arch marker",
        742: r"install-ci-runner-x64\.sh --repair-labels` re-applies\s+this arch's marker",
        # 8g's docstring quotes the phantom-test-name relay; anchored on the TEST NAME it names,
        # so a future shift of that sentence reds here instead of leaving the quotation adrift.
        862: r"test_the_changes_job_carries_no_job_scope_env",
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


# ── 8g. the TEST NAMES the runbook advertises must exist ─────────────────────────────────
_RUNBOOK_TEST_REF = re.compile(r"(tests/[A-Za-z0-9_/]+\.py)::(test_[A-Za-z0-9_]+)")


def test_every_test_name_the_runbook_names_exists():
    """⛔ A RUNBOOK REFERENCE TO A TEST THAT DOES NOT EXIST READS AS CONFIRMATION.

    `docs/ops/ci-runner-x64.md:862` told the operator the job-scope lever is a red check
    "`tests/test_ci_selection.py::test_the_changes_job_carries_no_job_scope_env`" — and no test of
    that name existed in the tree (the guard was
    `test_the_opt_in_is_step_scoped_so_no_env_sits_above_the_capstone_gate`). Nothing redded: the
    runbook→installer pins above check SYMBOLS in the installer, and no pin resolved a TEST name.
    So `pytest -k test_the_changes_job_carries_no_job_scope_env` collected **zero** tests and its
    green "no tests ran" is exactly what an operator reads as "the rule is enforced" (#10186
    review, FINDING 1 — the same relay-accuracy class, one surface out).

    Resolved against the AST rather than by collection: a name that is defined (or a class that
    holds it) passes, and a name that moved, was renamed, or never existed reds with the real
    names the file does define. Both directions are pinned — a reference to a missing FILE reds too.

    Non-vacuity, measured: restoring the pre-fix runbook text (the phantom name) reds this with the
    phantom reference named in the message; a reference to a file that does not exist reds the same
    way.
    """
    import ast  # local: a top-level import would shift this file's own cited line numbers (8d)

    doc = (REPO_ROOT / "docs" / "ops" / "ci-runner-x64.md").read_text()
    refs = sorted(set(_RUNBOOK_TEST_REF.findall(doc)))
    assert refs, (
        "the runbook no longer names a test — re-anchor this pin rather than deleting it; a pin "
        "over an empty reference set checks nothing"
    )

    defined: dict[str, set[str]] = {}
    missing: list[str] = []
    for rel, name in refs:
        path = REPO_ROOT / rel
        if rel not in defined:
            if not path.exists():
                defined[rel] = set()
            else:
                tree = ast.parse(path.read_text(), filename=rel)
                defined[rel] = {
                    node.name
                    for node in ast.walk(tree)
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                }
        if name not in defined[rel]:
            near = sorted(n for n in defined[rel] if name[:24] in n or n[:24] in name)
            missing.append(f"{rel}::{name}" + (f" (did you mean {near}?)" if near else ""))

    assert not missing, (
        "the runbook sends an operator to a test that does not exist — a `-k` filter over a phantom "
        "name collects zero tests and reads as confirmation. Rename the test to the advertised "
        "name (or fix the citation): " + "; ".join(missing)
    )


# ── 8h. `tools/` citations INTO the runbook ────────────────────────────────────────────────
_TOOLS_CITING_THE_RUNBOOK = ("tools/install-ci-runner-x64.sh", "tools/runner_autoscale.py")

# cited landing line -> a regex the cited line (or range) must match, i.e. the sentence the
# citing comment names. Keyed by the runbook line the citation must point at at THIS head.
_TOOLS_RUNBOOK_ANCHORS: dict[int, str] = {
    13: r"^# x86_64 CI runners",
    25: r"\*\*GitHub-hosted, plus — as a temporary mitigation",
    87: r"still does \*\*not\*\* claim any",
    92: r"DROPS the whole class on re-registration",
}


def test_every_tools_citation_into_the_runbook_lands_on_what_it_names():
    """⛔ THE RUNBOOK'S OWN EDIT BROKE TWO `tools/` CITATIONS AND NOTHING REDDED.

    This file's citations into the runbook are pinned (8e above), and so is the runbook→installer
    direction — but the `tools/*` → runbook direction had no pin at all, so when the ARM64/X64
    bullet grew the runbook by six lines, `tools/install-ci-runner-x64.sh:299` and
    `tools/runner_autoscale.py:31` kept citing `:61` for the "these runners deliberately **do not**
    claim any `ubuntu-<version>` label" rule while `:61` had become the `--fallback auto` paragraph
    (the sentence moved to `:67`). Both citations were CORRECT on `main` and wrong at this head —
    which is precisely why the numbers are pinned by CONTENT here and not trusted (#10186 review,
    FINDING 2).

    Three arms, all two-way: every fully-qualified citation in those files must be a landing line
    this pin knows (a new citation reds until it is anchored), every anchor must still be cited (a
    pin quietly shrunk to nothing reds), and each citation must land on the sentence it names — the
    failure message reports the line where that sentence actually is, so re-anchoring is mechanical.

    Known residual, stated rather than hidden: the runbook shorthand `` `:33-34` `` that follows a
    fully-qualified citation in the same sentence is NOT scanned (a bare `:NN` is ambiguous in a
    file that also cites installer lines by number).

    Non-vacuity, measured both arms: with the pre-fix number (`:61` in both files) the COVERAGE arm
    reds, reporting `[61]` as unanchored; with a simulated +6-line runbook shift the LANDING arm
    reds and names `:73` — the line the sentence actually moved to.
    """
    doc = (REPO_ROOT / "docs" / "ops" / "ci-runner-x64.md").read_text().splitlines()
    ref = re.compile(r"docs/ops/ci-runner-x64\.md:(\d+)(?:-(\d+))?")

    cited: dict[tuple[str, int], int] = {}
    for rel in _TOOLS_CITING_THE_RUNBOOK:
        lines = (REPO_ROOT / rel).read_text().splitlines()
        for i, line in enumerate(lines):
            for m in ref.finditer(line):
                start = int(m.group(1))
                cited[(f"{rel}:{i + 1}", start)] = int(m.group(2) or start)
    assert cited, (
        "no `tools/` file cites the runbook any more — re-anchor this pin rather than deleting it"
    )

    unknown = sorted({start for _, start in cited} - _TOOLS_RUNBOOK_ANCHORS.keys())
    assert not unknown, (
        "a `tools/` citation points at a runbook line this pin does not anchor — read that line, "
        f"re-derive the number, and add it to `_TOOLS_RUNBOOK_ANCHORS`: {unknown}"
    )
    stale = sorted(_TOOLS_RUNBOOK_ANCHORS.keys() - {start for _, start in cited})
    assert not stale, (
        "this pin anchors runbook line(s) nothing in `tools/` cites any more — a pin over refs "
        f"that were re-worded or removed is pinning nothing: {stale}"
    )

    wrong: list[str] = []
    for (where, start), end in sorted(cited.items()):
        assert 1 <= start <= end <= len(doc), (
            f"{where} cites docs/ops/ci-runner-x64.md:{start}-{end}, past EOF ({len(doc)} lines)"
        )
        text = "\n".join(doc[start - 1:end])
        if not re.search(_TOOLS_RUNBOOK_ANCHORS[start], text):
            actual = [
                i + 1 for i, line in enumerate(doc) if re.search(_TOOLS_RUNBOOK_ANCHORS[start], line)
            ]
            wrong.append(f"{where} cites :{start} but that is {text.strip()!r} (the sentence is at {actual})")
    assert not wrong, (
        "a `tools/` comment sends a reader to a runbook line that does not say what the comment "
        "claims — re-derive the number from the runbook at this head: " + "; ".join(wrong)
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

    # The provisioner invocation — a ref into a file THIS PR edits, and one the first pass missed.
    # `tools/runner_autoscale.py` gained ~96 lines above it, so the number that was correct on
    # `main` (`:965`, the installer invocation) landed on a systemd unit-name f-string here and
    # still passed the only check it had (`1 <= n <= EOF`). Pinned by SENTENCE, like the three
    # above, so a sibling-bullet ref drifts red instead of passing a range check.
    m = re.search(r"`tools/runner_autoscale\.py:(\d+)` invokes the installer", runbook)
    assert m is not None, (
        "the runbook no longer cites `tools/runner_autoscale.py:<line>` for the installer "
        "invocation — re-anchor this pin rather than deleting it"
    )
    n = int(m.group(1))
    assert '"--count", str(target_count), "--fallback", "auto"' in at(
        "tools/runner_autoscale.py", n
    ), (
        f"the runbook cites tools/runner_autoscale.py:{n} as the installer invocation, but that "
        f"line is {at('tools/runner_autoscale.py', n)!r}"
    )


# ── 8. the accepted-cost inventory is the MEASURED set, not the remembered one ─────────────────
def test_the_inventory_pattern_carries_every_documented_runs_on_form():
    r"""The counter-guard over the inventory must see every form that can make a file pool-eligible.

    This is the arm that reds when `RUNS_ON_UBUNTU_2404_RE` is narrowed back — it exists because the
    shipped pattern was `["\']?runs-on[\']?`: it OPENED on both quotes and CLOSED on one, so a pin
    added as `"runs-on": ubuntu-24.04` (a spelling YAML resolves to the SAME key, measured with
    PyYAML) left `total` at 9 and
    `test_the_runbook_names_every_leg_the_mitigation_makes_pool_eligible` green — the exact
    stale-inventory failure `finding-provenance.yml` produced for a whole review round, one spelling
    over. The same one-instance-over reasoning applies to the VALUE forms: a `runs-on` block
    sequence or a `{group: …, labels: […]}` mapping is equally servable by the mitigation, and a
    count that sees only the plain scalar omits that file from the accepted-cost list just as
    silently.

    The `labels:` KEY inside a mapping is the same rule one level in, and it outlived the `runs-on:`
    half by a round: the extractor's `keyis()` reads `"labels":` / `'labels':` / `labels :`, so a
    pin written `{group: x, "labels": [ubuntu-24.04]}` was verdict-able at rc 5 while a guard
    matching only `\blabels:` counted nothing and left the inventory green. The block-MAPPING value
    form (`runs-on:` then `group:` / `labels:` on deeper-indented lines) was the same shape: read by
    the extractor, invisible to the count. Both are positive arms below.

    Mutation: restore the pre-fix pattern (`^    ["\']?runs-on[\']?[ \t]*:[ \t]*ubuntu-24\.04$`) ->
    the double-quoted and explicit-key arms RED, and the block/mapping arms RED too. Narrow the
    `labels:` key back to `\blabels:` -> the quoted-key and spaced-colon arms RED. Restore
    `\bubuntu-24\.04\b` in the flow-mapping arm -> the arm64-in-a-mapping arm RED.
    """
    positive = {
        "plain scalar": "    runs-on: ubuntu-24.04\n",
        "single-quoted key": "    'runs-on': ubuntu-24.04\n",
        "double-quoted key": '    "runs-on": ubuntu-24.04\n',
        "explicit key": "    ? runs-on\n    : ubuntu-24.04\n",
        "block sequence": "    runs-on:\n      - self-hosted-x64\n      - ubuntu-24.04\n",
        "labels mapping": "    runs-on: {group: pi-runner, labels: [self-hosted-x64, ubuntu-24.04]}\n",
        # The same mapping with the `labels:` KEY spelled the other two ways `keyis()` reads. A
        # mapping is what a formatted workflow produces, and the runtime verdicts all three — so a
        # guard that sees only the plain spelling under-counts the very files the mitigation serves.
        "double-quoted labels key": '    runs-on: {group: pi-runner, "labels": [self-hosted-x64, ubuntu-24.04]}\n',
        "single-quoted labels key": "    runs-on: {group: pi-runner, 'labels': [ubuntu-24.04]}\n",
        "spaced labels colon": "    runs-on: {group: pi-runner, labels : [ubuntu-24.04]}\n",
        # The FIFTH value form: a block mapping under `runs-on:`. The extractor reads it (its
        # `keyis(line, "labels")` arm), so the file is pool-eligible and the inventory must see it.
        "block mapping": "    runs-on:\n      group: pi-runner\n      labels: [self-hosted-x64, ubuntu-24.04]\n",
        "block mapping, quoted labels key": '    runs-on:\n      group: pi-runner\n      "labels": [ubuntu-24.04]\n',
        "block mapping, scalar value": "    runs-on:\n      group: pi-runner\n      labels: ubuntu-24.04\n",
    }
    for name, text in positive.items():
        assert re.search(RUNS_ON_UBUNTU_2404_RE, text, re.MULTILINE), (
            f"the inventory's counter-guard cannot see the {name} form, so a pin added that way makes "
            f"its file pool-eligible while the runbook's accepted-cost list stays silent about it — "
            f"widen RUNS_ON_UBUNTU_2404_RE, do not relax this arm: {text!r}"
        )

    # The boundaries that must NOT count, or the guard would red the count on legitimate spellings.
    negative = {
        "the arm64 hosted spelling": "    runs-on: ubuntu-24.04-arm\n",
        "a different version pin": "    runs-on: ubuntu-latest\n",
        "an expression, not a static set": "    runs-on: ${{ matrix.os }}\n",
        "a step-level key (indent 8)": "        runs-on: ubuntu-24.04\n",
        "a mapping without the label": "    runs-on: {group: pi-runner, labels: [self-hosted-x64]}\n",
        "a block without the label": "    runs-on:\n      - self-hosted-x64\n      - ubuntu-latest\n",
        # The `-arm` sibling INSIDE a mapping. `\b` alone matched it (measured), which is the
        # wrong direction of error for an inventory: it invents a leg the mitigation does not serve
        # and the runbook then has to explain a job it never named.
        "the arm64 pin inside a flow mapping": "    runs-on: {group: pi-runner, labels: [ubuntu-24.04-arm]}\n",
        "the arm64 pin inside a block mapping": "    runs-on:\n      group: pi-runner\n      labels: [ubuntu-24.04-arm]\n",
        "a block mapping without the label": "    runs-on:\n      group: pi-runner\n      labels: [self-hosted-x64]\n",
        "an expression in a block mapping": "    runs-on:\n      group: pi-runner\n      labels: ${{ matrix.labels }}\n",
    }
    for name, text in negative.items():
        assert not re.search(RUNS_ON_UBUNTU_2404_RE, text, re.MULTILINE), (
            f"the counter-guard counts {name}, which is not a `ubuntu-24.04` job pin — the inventory "
            f"number would then be wrong in the direction nobody re-checks: {text!r}"
        )

    # ⛔ AND ONE JOB MUST NOT ABSORB THE NEXT ONE'S PIN. The block-mapping arm scans forward to the
    # `labels:` key, so a `runs-on:` block that never names the pin could walk into the FOLLOWING
    # job's block and count it twice — the count is `len(re.findall(...))`, so a double count is a
    # wrong number in the inventory, not a red. Exactly one job here is pinned.
    two_jobs = (
        "    runs-on:\n"
        "      group: pi-runner\n"
        "      labels: [self-hosted-x64]\n"
        "    steps: []\n"
        "  second:\n"
        "    runs-on:\n"
        "      group: pi-runner\n"
        "      labels: [ubuntu-24.04]\n"
    )
    assert len(re.findall(RUNS_ON_UBUNTU_2404_RE, two_jobs, re.MULTILINE)) == 1, (
        "the block-mapping arm counted a nested `runs-on` block more than once (or followed it into "
        "the next job) — the inventory total would then be inflated: "
        f"{re.findall(RUNS_ON_UBUNTU_2404_RE, two_jobs, re.MULTILINE)!r}"
    )
    pinned_then_next = (
        "    runs-on:\n"
        "      group: pi-runner\n"
        "      labels: [ubuntu-24.04]\n"
        "  second:\n"
        "    runs-on: ubuntu-latest\n"
        "  third:\n"
        "    runs-on: {group: pi-runner, labels: [ubuntu-latest]}\n"
    )
    assert len(re.findall(RUNS_ON_UBUNTU_2404_RE, pinned_then_next, re.MULTILINE)) == 1, (
        "the arms double-count a file whose unpinned siblings follow a pinned block mapping: "
        f"{re.findall(RUNS_ON_UBUNTU_2404_RE, pinned_then_next, re.MULTILINE)!r}"
    )


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
    # ⛔ THE PATTERN CARRIES EVERY DOCUMENTED FORM OF THE KEY — see `RUNS_ON_UBUNTU_2404_RE`. The
    # single-quoted spelling alone was not enough: it closes on one quote, so a pin added as
    # `"runs-on": ubuntu-24.04` left the count at 9 and this suite green, which is the same
    # blind spot one spelling over from the one the extractor had just been fixed for.
    pinned = {
        path.name: len(
            re.findall(
                RUNS_ON_UBUNTU_2404_RE,
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


# A GitHub Actions expression opener, spelled without braces-in-f-string hazards.
_EXPR = "$" + "{{"


def test_the_runbook_inventory_matches_the_tree_and_says_the_read_refuses(tmp_path: Path):
    r"""The `ubuntu-latest` row is a MEASUREMENT, and the page must not sell a read it cannot get.

    Both halves of this pin were false at `f83a7d1c1`, and nothing redded: the branch's own merge
    brought in `#10186`, which replaced one literal `runs-on: ubuntu-latest` with
    `runs-on: ${{ fromJSON(matrix.runner) }}` in `python-ci.yml`. Two consequences, neither
    re-derived against the tree the merge produced:

      1. the inventory is **76** `ubuntu-latest` jobs, not the 77 the page states (a number this PR
         added itself);
      2. that one non-static block makes `--status`' `label-coverage` read refuse with `exit 4`
         BEFORE it verdicts any label — so the page's *Verify it* section told the operator to
         confirm the mitigation with a read that prints the header, the refusal, and ZERO
         `<label> = <n> of <m>` rows, while the PR body's transcript showed `rc 0` with two rows.

    The refusal is CORRECT (a partial read is not a coverage verdict, pinned by 3b-ii); what was
    wrong is a page and a verification transcript that assume this tree has no expression block.
    So this pin holds both directions: the numbers must be the measured ones, and the refusal
    paragraph must be present exactly while the tree still HAS a job the extractor cannot read.

    Non-vacuity (measured at `f83a7d1c1`): with the pre-fix doc restored the count arms RED on
    `77 != 76` and the *Verify it* arms RED on a section that never names `python-ci.yml:716`,
    `exit 4`, or the `STATIC label set` refusal; deleting the `fromJSON` block (or making it static)
    reds the last arm, which is the signal to delete the paragraph in the same change.
    """
    # Local import: a module-level one shifts this file's line numbers, and five other files cite
    # `tests/test_ci_runner_x64_install.py:228` by number (pinned by
    # `test_the_label_assertion_other_files_cite_lands_on_it`).
    import yaml

    wf_dir = REPO_ROOT / ".github" / "workflows"
    counts = {"ubuntu-latest": 0, "ubuntu-24.04": 0}
    unreadable: list[tuple[str, str, object]] = []

    for path in sorted([*wf_dir.glob("*.yml"), *wf_dir.glob("*.yaml")]):
        workflow = yaml.safe_load(path.read_text()) or {}
        for job, body in (workflow.get("jobs") or {}).items():
            if not isinstance(body, dict) or "runs-on" not in body:
                continue
            value = body["runs-on"]
            labels = value.get("labels") if isinstance(value, dict) else value
            if labels is None:  # a `group:` with no `labels:` names no static label — skipped, as
                continue  # the extractor's `group:`-only arm does (it is a report, not a refusal)
            labels = [labels] if isinstance(labels, str) else labels
            if any(_EXPR in str(m) for m in labels):
                unreadable.append((path.name, job, value))
                continue
            for label in labels:
                if label in counts:
                    counts[label] += 1

    assert counts["ubuntu-24.04"] == 9, (
        "the `ubuntu-24.04` job count moved — re-derive the runbook's table with this pin: "
        f"{counts}"
    )
    assert counts["ubuntu-latest"] == 76, (
        "the `ubuntu-latest` job count moved (the runbook states it as a dated measurement, and "
        "this arm is what makes 'dated' honest) — re-derive the table and this number together, do "
        f"not assume {counts['ubuntu-latest']} is right: {counts}"
    )

    runbook = (REPO_ROOT / "docs" / "ops" / "ci-runner-x64.md").read_text()

    row = re.search(r"^\| `ubuntu-latest` \| (\d+) \|", runbook, re.MULTILINE)
    assert row is not None, (
        "the runbook no longer carries an `ubuntu-latest` count row — re-anchor this pin rather "
        "than deleting it"
    )
    assert int(row.group(1)) == counts["ubuntu-latest"], (
        f"the runbook's `ubuntu-latest` row says {row.group(1)}, the tree has "
        f"{counts['ubuntu-latest']} jobs whose `runs-on:` names that single label"
    )

    stated = re.search(r"count each job's `runs-on`\): \*\*(\d+) / (\d+)\*\*", runbook)
    assert stated is not None, (
        "the runbook no longer states its re-measured `N / M` total — re-anchor this pin rather "
        "than deleting it"
    )
    assert (int(stated.group(1)), int(stated.group(2))) == (
        counts["ubuntu-latest"],
        counts["ubuntu-24.04"],
    ), (
        f"the runbook states {stated.group(1)} / {stated.group(2)}, the tree measures "
        f"{counts['ubuntu-latest']} / {counts['ubuntu-24.04']}"
    )

    # ⛔ AND THE REFUSAL PARAGRAPH IS TRUE ONLY WHILE THE TREE HAS A BLOCK IT CANNOT READ. The
    # extractor's own arm (3b-ii) is what makes it correct; this is the RUNBOOK side, which is
    # where an operator learns that the ``<label> = <n> of <m>`` transcript does not exist here.
    assert unreadable, (
        "no workflow names a non-static `runs-on:` any more, so this tree's `--status` DOES report "
        "coverage rows — the runbook's 'on THIS tree that read refuses' paragraph is now false and "
        "must be deleted in the same change as this assertion: "
        f"{(REPO_ROOT / 'docs' / 'ops' / 'ci-runner-x64.md')}"
    )
    start = runbook.index("**Verify it \u2014 do not assume it.**")
    section = runbook[start : runbook.index("The EXIT is decided per CLASS", start)]
    for rel, job, value in unreadable:
        needle = f"{rel}:{_line_of(REPO_ROOT / '.github' / 'workflows' / rel, f'runs-on: {value}')}"
        assert needle in section, (
            f"`{rel}::{job}` names `runs-on: {value}`, so `--status` refuses on THIS tree — but the "
            f"runbook's *Verify it* section never cites `{needle}`. A page that tells the operator "
            "to verify with a read that returns no verdict, and does not say so, sends them to a "
            "transcript that cannot exist here."
        )
    assert "`exit 4`" in section and "STATIC label set" in section, (
        "the *Verify it* section no longer states that this tree's coverage read REFUSES with exit "
        f"4 (and why) — an operator reads it as a broken instrument: {section[-600:]!r}"
    )

    # ── behaviour, not prose: the read the page tells you to run, on the tree it ships in.
    bindir = _stub_gh(tmp_path / "gh", "r1\tself-hosted-x64,ubuntu-latest")
    r = _run(
        "--status",
        env=_env(tmp_path, PATH=f"{bindir}:{os.environ['PATH']}"),
        cwd=REPO_ROOT,
    )
    assert r.returncode == 4, (
        "`--status` on THIS tree must refuse (4) rather than report coverage over the blocks it "
        f"could not read — the runbook says so; got {r.returncode}: {r.stderr}"
    )
    assert "STATIC label set" in r.stderr, r.stderr
    assert "online runner(s)" not in r.stderr, (
        "a partial read printed per-label answer rows — the no-verdict arms must run BEFORE the "
        f"loop, or a refusal is outvoted by the labels that did parse: {r.stderr}"
    )


def _line_of(path: Path, needle: str) -> int:
    for i, line in enumerate(path.read_text().splitlines(), start=1):
        if needle in line:
            return i
    raise AssertionError(f"{path} no longer contains {needle!r} — re-anchor this pin")



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
        # `-x`: the proof is that the MODULE goes red, so the first failure is the whole answer —
        # without it the nested run re-proves it once per arm and outlives its own timeout.
        # ⛔ `--rootdir` IS NOT COSMETIC: without it the nested pytest resolves the ini/rootdir
        # upward out of the throwaway tree and inherits this repo's configuration, whose test paths
        # it then walks — measured here as a silent 300 s hang with no output, i.e. the
        # non-vacuity proof timing out instead of proving anything.
        [
            sys.executable, "-m", "pytest", str(copied), "-x", "-q",
            "-p", "no:cacheprovider", "--noconftest", f"--rootdir={copy_root}",
        ],
        capture_output=True, text=True,
        cwd=str(copy_root),
    )
    assert r.returncode != 0, f"a missing installer must FAIL, not pass or skip: {r.stdout}{r.stderr}"
    assert "skipped" not in r.stdout, f"a missing installer must never be a skip: {r.stdout}"
    assert "installer not found" in (r.stdout + r.stderr), r.stdout + r.stderr


# ── 8f. the `CI_RUNNER_OS_LABEL` writers: one reader, ZERO writers, said plainly ────────────
def test_the_os_label_flag_describes_its_writers_without_asserting_the_live_box():
    """⛔ A WRITER THAT DOES NOT EXIST WAS ASSERTED, AND SO WAS A BOX NO CHECKOUT CAN READ.

    `CI_RUNNER_OS_LABEL` guards the version-pin carve-out: `on|auto` KEEPS `ubuntu-24.04`,
    `off`/unset DROPS it. The flag arrived with the repair (its READER), and two mistakes were made
    around it in opposite directions. First, three places asserted a WRITER that did not exist yet
    ("BOTH writers read it", "the live pool's own state", "cannot revert an APPLIED owner departure
    with no switch involved") — so an operator read a pin as protected that a plain
    `--repair-labels` reverts. Then, once the install-side writer existed (#10275), the correction
    ("ONE reader … nothing WRITES it") became the stale half: the writer is now `resolve_os_label`
    in this tree, the autoscaler mirrors it in `_os_labels`, and the schedule renders it into the
    timer.

    Both halves are pinned, because both failures read as diligence. A claim about the LIVE BOX's
    own environment FAILS (no checkout can read a unit's `Environment`), and the WRITERS this tree
    actually has must be named — so deleting the paragraph instead of restating it reds too.

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
        "nothing in this tree exports it yet": (
            "the schedule writer renders the value into the timer, so `off` on a hand-run command is "
            "the ABSENCE of that writer's environment, not the absence of a writer"
        ),
        "the computed set never carries an `ubuntu-<version>`": (
            "`resolve_labels` appends `$OS_LABELS` on every arm after its `case`, so under "
            "`on|auto` the computed set DOES carry `ubuntu-24.04` and the repair ADDS it — the "
            "sentence denies the one path that re-applies the #6897 mitigation"
        ),
        "Both belong to #10275": (
            "that framing made the page defer its own subject to a PR that IS this branch; the "
            "`_preserve_labels` drop is a NAMED residual here, and the re-attach path is "
            "`--repair-labels`"
        ),
        "an empty answer is the expected one today, since nothing in this tree writes the flag": (
            "the schedule writes it and `systemctl show -p Environment` is the read, so an empty "
            "answer is the `off` row rather than the expected state"
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
            (
                "THE FLAG HAS TWO SIDES IN THIS TREE",
                "resolve_os_label",
                "Export `CI_RUNNER_OS_LABEL=on` to KEEP one",
            ),
        ),
        "docs/ops/ci-runner-x64.md": (
            runbook,
            (
                "Both sides now live in this tree",
                "resolve_os_label",
                "not a fact this page holds",
                # the writer the page must name, and the read that replaces guessing
                "the only WRITER is the SCHEDULE",
                "systemctl show -p Environment runner-autoscale.service",
                # the UNION half: `on|auto` ADDS the pin, it is not only a preservation
                "ADDS it to a runner that lacks it",
                # the residual is named, not deferred to a PR
                "NAMED residual",
            ),
        ),
    }
    # ⛔ THE RUNBOOK'S "ADDS it" IS BACKED BY THE INSTALLER'S OWN APPEND POINT, not by prose: the
    # OS label rides on EVERY `resolve_labels` arm, after its `case`, which is what makes the union
    # carry it under `on|auto`. Without this pair the runbook could keep the sentence while the code
    # stopped honouring it.
    assert 'LABELS="$LABELS$OS_LABELS"' in installer, (
        "`resolve_labels` no longer has its single unconditional append point "
        "(`LABELS=\"$LABELS$OS_LABELS\"`) — then the runbook's \"the repair ADDS it\" is false and "
        "the #6897 mitigation has no re-apply path outside a fresh install"
    )
    for arm in ("LABELS=\"$ARCH_LABEL,$FALLBACK_LABEL\"", "LABELS=\"$ARCH_LABEL\""):
        assert arm in installer, f"`resolve_labels` lost the arm {arm!r} the append point completes"

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
    its continuation (it starts with `1. **`ubuntu-<version>`**`), so a reader of the carve-out
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
      3. it NAMES the writer that can put the class back (`resolve_os_label` / `_preserve_labels`)
         — the dangling clause promised a writer and named none, which is how it survived a pin
         that only checked the flag's reader count.

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
    assert re.search(r"resolve_os_label|_preserve_labels", lead_in), (
        "the carve-out lead-in no longer NAMES the writer that can put the class back — a clause "
        "that promises a writer and names none is what dangled in the first place: "
        f"{lead_in[-160:]!r}"
    )


# ── 9. a sibling test's runbook CITE is a reader instruction too ───────────────────────────────
def test_the_autoscale_tests_runbook_refs_land_on_what_they_name():
    """A sibling test's runbook cite is a reader instruction like any other, and nothing read it.

    `tests/test_runner_autoscale.py` argues why `ubuntu-24.04-arm` stays dropped and cites a line
    span for the sentence it leans on ("the ARM64 VMs register their own markers"). The number was
    right on `main`; this PR's own measurement paragraph was inserted ABOVE it, so `:33-34` came to
    name the `ubuntu-latest = 0 of 12` paragraph instead. No test read the cite at all — the same
    only-range-checked class `test_the_runbooks_cross_file_refs_land_on_what_they_name` exists for,
    one direction over (a test citing the runbook, not the runbook citing a workflow).

    Mutation: restore `:33-34` in that docstring -> RED (the span says `ubuntu-latest = 0 of 12`,
    not the ARM64 sentence).
    """
    sibling = (REPO_ROOT / "tests" / "test_runner_autoscale.py").read_text()
    # the span may be written `(`:NN-MM`)` or `(`:NN-MM`, `docs/ops/…`)` — both name the same lines,
    # so the citation is matched on the SPAN and not on how the sentence closes its parenthesis.
    m = re.search(r"the ARM64 VMs register their own markers \(`:([0-9]+)-([0-9]+)`", sibling)
    assert m is not None, (
        "`tests/test_runner_autoscale.py` no longer cites a runbook span for the ARM64-marker "
        "sentence — re-anchor this pin rather than deleting it"
    )
    lo, hi = int(m.group(1)), int(m.group(2))
    lines = (REPO_ROOT / "docs" / "ops" / "ci-runner-x64.md").read_text().splitlines()
    assert 1 <= lo <= hi <= len(lines), f"cite :{lo}-{hi} is outside the runbook's {len(lines)} lines"
    span = "\n".join(lines[lo - 1 : hi])
    assert "ARM64 VMs register" in span, (
        f"`tests/test_runner_autoscale.py` cites docs/ops/ci-runner-x64.md:{lo}-{hi} for the "
        f"arm64-marker sentence, but that span is {span!r} — re-derive the ref, do not offset it"
    )


# ── 12. the narrowed keep is described as narrowed, everywhere it is described ────────────────
def test_no_surface_claims_the_repair_keeps_the_whole_version_pinned_class():
    """⛔ A KEEP THAT IS NARROWED IN CODE AND WIDE IN PROSE IS THE FALSE-ARCHITECTURE CLAIM BACK.

    `--repair-labels` keeps ONLY `$OS_LABEL` (`if [ "$l" = "$OS_LABEL" ]`, and every other member of
    the family is printed `NOT KEPT:`) — deliberately, because keeping `ubuntu-24.04-arm` is how an
    x86_64 runner serves an ARM64 job and produces a wrong artifact. The instruction read by the
    next maintainer is the COMMENT and the RUNBOOK, not the `case` arm, so a sentence saying the
    repair "keeps the class" licenses exactly the state the narrowing exists to forbid.

    This is not hypothetical: the claim was removed from two workflow comments, then from
    `runner_autoscale.py::_preserve_labels`, and each time it survived in a SIBLING sentence that
    no test read (the re-registration branch of `tools/runner_autoscale.py` here, and the runbook
    sentence that contradicted its own "TWO NAMED CARVE-OUTS" section ON THE SAME PAGE). So the pin
    is
    mechanical and covers every surface that describes the repair, not the one line a review named.

    Mutation: restore any of the three `keeps the class` sentences -> RED on the file it is in.
    """
    surfaces = [
        REPO_ROOT / "tools" / "runner_autoscale.py",
        REPO_ROOT / "docs" / "ops" / "ci-runner-x64.md",
        REPO_ROOT / "MEMORY.md",
        REPO_ROOT / "tests" / "test_runner_autoscale.py",
        REPO_ROOT / "tests" / "test_reporting_workflow_concurrency.py",
    ]
    wide = re.compile(r"keep[s]?\s+the\s+(whole\s+)?class", re.IGNORECASE)
    offenders: list[str] = []
    for path in surfaces:
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if wide.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{n}: {line.strip()}")
        # a claim can also be WRAPPED across two prose lines, which the per-line scan above cannot
        # see — normalise before concluding the surface is clean (#10275: the surviving sentence in
        # the runbook was a wrap of the one the review had already removed elsewhere).
        joined = re.sub(r"\s+", " ", path.read_text())
        if wide.search(joined) and not wide.search(path.read_text()):
            offenders.append(f"{path.relative_to(REPO_ROOT)}: (wrapped) {wide.search(joined).group(0)!r}")
    assert not offenders, (
        "the repair keeps `$OS_LABEL` and DROPS the rest of the version-pinned family "
        "(`NOT KEPT:`), so no surface may say it keeps the class — a narrowed keep described as "
        "wide is the false-architecture claim the narrowing removed: " + "; ".join(offenders)
    )

    # ...and the narrowed truth is actually STATED where a maintainer looks, not merely un-denied.
    # (whitespace-normalised: both files wrap this sentence across two lines.)
    narrowed = re.compile(r"dropping the rest of the family|drops? the rest of the family", re.IGNORECASE)
    autoscale = re.sub(r"\s+", " ", (REPO_ROOT / "tools" / "runner_autoscale.py").read_text())
    assert narrowed.search(autoscale), (
        "`_preserve_labels`'s residual must name the narrowing (`$OS_LABEL` kept, the rest dropped) "
        "— an unstated narrowing reads as the wide keep this pin forbids"
    )
    runbook = re.sub(r"\s+", " ", (REPO_ROOT / "docs" / "ops" / "ci-runner-x64.md").read_text())
    assert narrowed.search(runbook), (
        "the runbook must name the narrowing too — it is the page an operator reads before running "
        "`--repair-labels`"
    )


# ── 8j. the relays a SIBLING test file makes into the runbook and the tree ──────────────────
_CITING_TEST_FILE = "tests/test_ci_selection.py"


def test_the_relays_a_sibling_test_file_makes_land_on_what_they_name():
    """⛔ EVERY PIN ABOVE READS THIS FILE; A SIBLING'S RELAYS WERE UNSCANNED, AND ONE DRIFTED.

    `tests/test_ci_selection.py` tells a maintainer that `docs/ops/ci-runner-x64.md` and the
    capstone pin's re-cut history both advertise
    `test_the_changes_job_carries_no_job_scope_env` — the reference-surface relay for #10186
    FINDING 1. While re-deriving this PR's own refs, that runbook citation was moved to `:474` by
    a constant: at this head `:474` is a BLANK LINE inside a fenced block (473 closes the fence,
    475 opens "If a runner is gone …") and the sentence naming the test is the file's only
    occurrence of it, at `:862`. Nothing redded, because every pin above scans THIS file's
    citations and `tests/test_ci_selection.py` is in none of their surfaces — so the one file
    whose relay drifted was the one file no pin read.

    A blank-line target is worse than a stale one, because the reader concludes the assertion is
    gone. All three citations in the sibling file are pinned here, two-way: a citation this pin
    does not know FAILS (add it rather than leaving it unchecked), a known site that stopped
    citing FAILS (the pin must not silently shrink), and each citation must land on the line that
    says the thing its own paragraph claims.

    Non-vacuity (measured): with the pre-fix citation (runbook `:474`, named that way in this
    docstring rather than as a `path:line` ref, so 8e's scan does not read it as a claim) the
    landing check reds on ``cites …:474, but that line is ''``; re-pointed at `:862` it
    is GREEN; re-pointed at `:448` (where the sentence lives on `origin/main`, i.e. a plausible
    offset re-derivation) it reds again — which is what makes this a landing check and not a
    range check.
    """
    citing = (REPO_ROOT / _CITING_TEST_FILE).read_text().splitlines()

    # (target file, cited line) -> a regex the CITED LINE ITSELF must match
    anchors: dict[tuple[str, int], str] = {
        # the /docs guard's import of the schema types the citing paragraph names
        ("tests/test_website_docs_consistency.py", 56):
            r"from tortoise\.commit_schema import Point, point_content_id",
        # the reference-surface relay that drifted: the runbook sentence naming the test
        ("docs/ops/ci-runner-x64.md", 862): r"test_the_changes_job_carries_no_job_scope_env",
        # the capstone pin's re-cut history, which names it too
        ("tests/test_capstone_preregistration_9075.py", 1590):
            r"test_the_changes_job_carries_no_job_scope_env",
    }

    ref = re.compile(r"((?:tests/[A-Za-z0-9_/]+\.py)|(?:docs/ops/ci-runner-x64\.md)):(\d+)")
    cited: dict[tuple[str, int], list[int]] = {}
    for i, line in enumerate(citing):
        for m in ref.finditer(line):
            cited.setdefault((m.group(1), int(m.group(2))), []).append(i + 1)
    assert cited, (
        f"{_CITING_TEST_FILE} no longer cites the runbook or the tree — re-anchor this pin rather "
        "than deleting it; a pin over an empty reference set checks nothing"
    )

    unknown = sorted(set(cited) - anchors.keys())
    assert not unknown, (
        f"{_CITING_TEST_FILE} cites target(s) this pin does not know — re-derive them against the "
        f"tree at this head and add them to `anchors`: {unknown}"
    )
    stale = sorted(anchors.keys() - set(cited))
    assert not stale, (
        "this pin still lists citations nothing makes — a pin over refs that were re-worded or "
        f"removed is pinning nothing: {stale}"
    )

    wrong: list[str] = []
    for (target, n), citing_lines in sorted(cited.items()):
        path = REPO_ROOT / target
        assert path.is_file(), f"{_CITING_TEST_FILE}:{citing_lines} cites a missing file: {target}"
        lines = path.read_text().splitlines()
        assert 1 <= n <= len(lines), f"{target}:{n} is past EOF ({len(lines)} lines)"
        if not re.search(anchors[(target, n)], lines[n - 1]):
            wrong.append(
                f"{_CITING_TEST_FILE}:{citing_lines} cites {target}:{n}, but that line is "
                f"{lines[n - 1].strip()!r}"
            )
    assert not wrong, (
        "a citation names a line that does not say it — re-derive the number at this head: "
        + "; ".join(wrong)
    )
